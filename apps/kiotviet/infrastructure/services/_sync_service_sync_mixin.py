"""Per-invoice sync (create on target) for SyncInvoicesService (extracted mixin)."""

import json

from apps.kiotviet.infrastructure.services.common_auth_customer import (
    _phone_for_kiotviet,
    _sanitize_text,
)
from apps.kiotviet.infrastructure.services.invoice_sync_queries import create_invoice
from apps.kiotviet.infrastructure.services.invoice_payload_builder import (
    CustomerCodeMissingError,
    build_target_invoice_payload,
    patch_invoice_status_after_create,
    resolve_target_customer as shared_resolve_target_customer,
)
from apps.kiotviet.models import KiotVietChannel, channel_vat_tax_id

from ._sync_service_shared import _SkipInvoice


class SyncCreateMixin:
    # ------------------------------------------------------------------
    # Per-invoice
    # ------------------------------------------------------------------
    def _sync_invoice(
        self,
        *,
        source_invoice: dict,
        source_channel: KiotVietChannel,
        target_channel: KiotVietChannel,
        target_branch_id: int,
        dry_run: bool,
    ) -> str:
        source_code_for_log = self._first_non_empty(
            source_invoice.get("code"),
            source_invoice.get("Code"),
            source_invoice.get("invoiceCode"),
        ) or "(unknown)"
        self._current_code = source_code_for_log
        self._current_fallbacks = []

        for detail in (source_invoice.get("invoiceDetails") or source_invoice.get("InvoiceDetails") or []):
            if not isinstance(detail, dict):
                continue
            tax_rows = detail.get("invoiceDetailTaxs") or detail.get("InvoiceDetailTaxs")
            has_tax_price = False
            if isinstance(tax_rows, list) and tax_rows and isinstance(tax_rows[0], dict):
                first_tax = tax_rows[0]
                cand = first_tax.get("priceAfterTax") or first_tax.get("PriceAfterTax")
                if cand not in (None, "") and self._to_decimal(cand) > 0:
                    has_tax_price = True
                if not has_tax_price:
                    sub_after = first_tax.get("subTotalAfterTax") or first_tax.get("SubTotalAfterTax")
                    if sub_after not in (None, "") and self._to_decimal(sub_after) > 0:
                        has_tax_price = True
                if not has_tax_price:
                    tax_rate = (
                        first_tax.get("taxRate") or first_tax.get("TaxRate")
                        or first_tax.get("value") or first_tax.get("Value")
                    )
                    if tax_rate not in (None, "") and self._to_decimal(tax_rate) > 0:
                        has_tax_price = True
            if not has_tax_price:
                pcode = self._first_non_empty(
                    detail.get("productCode"), detail.get("ProductCode"),
                    detail.get("code"), detail.get("Code"),
                ) or "?"
                self._current_fallbacks.append(f"price_fallback(product={pcode})")

        delivery = (
            source_invoice.get("invoiceDelivery")
            if isinstance(source_invoice.get("invoiceDelivery"), dict) else {}
        )
        if not delivery:
            delivery = (
                source_invoice.get("InvoiceDelivery")
                if isinstance(source_invoice.get("InvoiceDelivery"), dict) else {}
            )
        delivery = delivery if isinstance(delivery, dict) else {}

        customer_block, customer_code_used, customer_name_used = self._resolve_target_customer(
            source_invoice=source_invoice,
            delivery=delivery,
            target_channel=target_channel,
            target_branch_id=target_branch_id,
            dry_run=dry_run,
        )

        from apps.kiotviet.infrastructure.services.invoice_payload_builder import (
            build_invoice_details,
        )
        vat_tax_id = channel_vat_tax_id(target_channel)
        if not build_invoice_details(source_invoice, vat_tax_id=vat_tax_id):
            return "skipped"

        src_sale_channel_id_log = self._first_non_empty(
            source_invoice.get("saleChannelId"), source_invoice.get("SaleChannelId"),
        )
        mapped_sale_channel = self._map_sale_channel_id(
            src_sale_channel_id_log,
            source_kv=source_channel,
            target_kv=target_channel,
        )
        if not src_sale_channel_id_log:
            self._summary["missing_sale_channel_codes"].append(source_code_for_log)
            self._current_fallbacks.append("missing_sale_channel_id")
        elif mapped_sale_channel is None:
            self._summary["unmapped_sale_channel_codes"].append(
                f"{source_code_for_log} (src={src_sale_channel_id_log})"
            )
            self._current_fallbacks.append(
                f"unmapped_sale_channel_id(src={src_sale_channel_id_log})"
            )
        src_sold_by_id_log = self._first_non_empty(
            source_invoice.get("soldById"), source_invoice.get("SoldById"),
        )
        mapped_sold_by = self._map_sold_by_id(
            src_sold_by_id_log,
            source_kv=source_channel,
            target_kv=target_channel,
        )
        if src_sold_by_id_log and mapped_sold_by is None:
            self._summary["unmapped_sold_by_codes"].append(
                f"{source_code_for_log} (src={src_sold_by_id_log})"
            )
            self._current_fallbacks.append(
                f"unmapped_sold_by_id(src={src_sold_by_id_log})"
            )
        self._stdout(
            f"      mapped saleChannelId: {src_sale_channel_id_log or '(empty)'} -> {mapped_sale_channel}"
        )
        self._stdout(
            f"      mapped soldById: {src_sold_by_id_log or '(empty)'} -> {mapped_sold_by}"
        )

        built = build_target_invoice_payload(
            source_invoice=source_invoice,
            target_branch_id=int(target_branch_id),
            vat_tax_id=vat_tax_id,
            customer_block=customer_block,
            customer_code=customer_code_used,
            customer_name=customer_name_used,
            source_channel=source_channel,
            target_channel=target_channel,
        )
        payload = built["payload"]
        source_invoice_status = built["source_invoice_status"]
        source_delivery_status = built["source_delivery_status"]
        source_delivery_code = built.get("source_delivery_code") or ""
        source_delivery_price = built.get("source_delivery_price")

        self._stdout(
            f"[3/3] payload for B (create_invoice): "
            f"{json.dumps(payload, ensure_ascii=False, default=str)}"
        )

        if dry_run:
            return "dry_run"

        created = create_invoice(payload, order=None, channel=target_channel)
        self._stdout(
            f"      create_invoice response: "
            f"{json.dumps(created, ensure_ascii=False, default=str)}"
        )

        created_invoice_id = created.get("id") or created.get("Id")
        if created_invoice_id:
            try:
                patch_invoice_status_after_create(
                    target_channel=target_channel,
                    created_invoice_id=int(created_invoice_id),
                    source_invoice_status=source_invoice_status,
                    source_delivery_status=source_delivery_status,
                    source_delivery_code=source_delivery_code,
                    source_delivery_price=source_delivery_price,
                    exact_mirror=getattr(self, "_exact_mirror", False),
                )
                self._stdout(
                    f"      patched: invoice status={source_invoice_status}, "
                    f"delivery status={source_delivery_status}, "
                    f"delivery code={source_delivery_code or '(empty)'}, "
                    f"delivery price={source_delivery_price}"
                )
            except Exception as exc:  # noqa: BLE001
                self._stderr(f"      patch failed: {exc}")

        return str(created.get("code") or created.get("invoiceCode") or created.get("id") or "ok")

    # ------------------------------------------------------------------
    # Customer resolution
    # ------------------------------------------------------------------
    def _resolve_target_customer(
        self,
        *,
        source_invoice: dict,
        delivery: dict,
        target_channel: KiotVietChannel,
        target_branch_id: int,
        dry_run: bool,
    ) -> tuple[dict, str, str]:
        phone_log = _phone_for_kiotviet(
            source_invoice.get("customerPhone"),
            source_invoice.get("CustomerPhone"),
            (source_invoice.get("customer") or {}).get("contactNumber") if isinstance(source_invoice.get("customer"), dict) else None,
            (source_invoice.get("Customer") or {}).get("ContactNumber") if isinstance(source_invoice.get("Customer"), dict) else None,
            (source_invoice.get("customer") or {}).get("phoneNumber") if isinstance(source_invoice.get("customer"), dict) else None,
            (source_invoice.get("Customer") or {}).get("PhoneNumber") if isinstance(source_invoice.get("Customer"), dict) else None,
            delivery.get("contactNumber"),
            delivery.get("ContactNumber"),
        )
        full_name_log = _sanitize_text(self._first_non_empty(
            source_invoice.get("customerName"),
            source_invoice.get("CustomerName"),
            (source_invoice.get("customer") or {}).get("name") if isinstance(source_invoice.get("customer"), dict) else None,
            (source_invoice.get("Customer") or {}).get("Name") if isinstance(source_invoice.get("Customer"), dict) else None,
            delivery.get("receiver"),
            delivery.get("Receiver"),
            "Khách lẻ",
        )) or "Khách lẻ"
        self._stdout(
            f"[2/3] search customer on B by phone={phone_log or '(empty)'}, source_name={full_name_log}"
        )
        try:
            customer_block, code, name = shared_resolve_target_customer(
                source_invoice=source_invoice,
                delivery=delivery,
                target_channel=target_channel,
                target_branch_id=int(target_branch_id),
                order=None,
                create_missing=not dry_run,
            )
        except CustomerCodeMissingError as exc:
            raise _SkipInvoice(f"{exc}, skip") from exc
        if hasattr(self, "_current_fallbacks") and code.endswith(".1"):
            self._current_fallbacks.append("customer_create_new")
        self._stdout(
            f"      customer_block: {json.dumps(customer_block, ensure_ascii=False, default=str)}"
        )
        return customer_block, code, name

    # ------------------------------------------------------------------
    # ID mapping (new kiot -> old kiot)
    # ------------------------------------------------------------------
    def _map_sale_channel_id(
        self,
        source_sale_channel_id,
        *,
        source_kv,
        target_kv,
    ) -> int | None:
        from apps.kiotviet.infrastructure.services.id_mappers import (
            map_sale_channel_id,
        )
        return map_sale_channel_id(
            source_sale_channel_id,
            source_kv=source_kv,
            target_kv=target_kv,
        )

    def _map_sold_by_id(
        self,
        source_sold_by_id,
        *,
        source_kv,
        target_kv,
    ) -> int | None:
        from apps.kiotviet.infrastructure.services.id_mappers import (
            map_kiot_user_id,
        )
        return map_kiot_user_id(
            source_sold_by_id,
            source_kv=source_kv,
            target_kv=target_kv,
        )
