"""Cross-channel consistency checks for SyncInvoicesService (extracted mixin)."""

import json as _json
import re as _re
import time
from decimal import Decimal

from apps.kiotviet.infrastructure.services.invoice_mutations_push import delete_invoice_by_id
from apps.kiotviet.infrastructure.services.invoice_sync_queries import get_invoice_by_code
from apps.kiotviet.models import KiotVietChannel

from ._sync_service_shared import KIOTVIET_LIST_MAX_PAGE_SIZE, SyncError


class SyncCheckMixin:
    def _check_missing(
        self,
        *,
        source_channel: KiotVietChannel,
        source_branch_id: int,
        target_channel: KiotVietChannel,
        target_branch_id: int,
        from_dt: str,
        to_dt: str | None,
        page_size: int,
        sleep_pages: float,
    ) -> dict:
        self._stdout(
            f"\n=== CHECK MISSING: source branch={source_branch_id} -> target branch={target_branch_id} ==="
        )
        check_top = min(max(int(page_size or 0), 1), KIOTVIET_LIST_MAX_PAGE_SIZE)

        source_codes: list[str] = []
        skip = 0
        while True:
            page_total, web_data = self._web_list_invoices(
                channel=source_channel,
                branch_id=source_branch_id,
                from_dt=from_dt,
                to_dt=to_dt,
                top=check_top,
                skip=skip,
            )
            self._stdout(f"  source page: skip={skip}, fetched={len(web_data)}, total={page_total}")
            if not web_data:
                break
            for inv in web_data:
                status_val = str(inv.get("StatusValue") or inv.get("statusValue") or "").strip().lower()
                status_code = inv.get("Status") or inv.get("status")
                if status_val in {"đã hủy", "đã huỷ", "cancelled"} or status_code == 2:
                    continue
                code = self._first_non_empty(inv.get("Code"), inv.get("code"), inv.get("invoiceCode"))
                if code:
                    source_codes.append(code)
            skip += len(web_data)
            if len(web_data) < check_top or (page_total and skip >= page_total):
                break
            if sleep_pages > 0:
                time.sleep(sleep_pages)

        self._stdout(f"  source total codes (excl. cancelled): {len(source_codes)}")

        synced_source_codes: set[str] = set()
        synced_delivery_codes: set[str] = set()
        # (target_code, src_code | None, target_id | None)
        target_entries: list[tuple[str, str | None, int | None]] = []
        skip = 0
        while True:
            page_total, web_data = self._web_list_invoices(
                channel=target_channel,
                branch_id=target_branch_id,
                from_dt=from_dt,
                to_dt=to_dt,
                top=check_top,
                skip=skip,
            )
            self._stdout(f"  target page: skip={skip}, fetched={len(web_data)}, total={page_total}")
            if not web_data:
                break
            for inv in web_data:
                status_val = str(inv.get("StatusValue") or inv.get("statusValue") or "").strip().lower()
                status_code = inv.get("Status") or inv.get("status")
                if status_val in {"đã hủy", "đã huỷ", "cancelled"} or status_code == 2:
                    continue
                target_code = self._first_non_empty(
                    inv.get("Code"), inv.get("code"), inv.get("invoiceCode")
                )
                tid_raw = inv.get("Id") or inv.get("id")
                try:
                    tid = int(tid_raw) if tid_raw is not None else None
                except (TypeError, ValueError):
                    tid = None
                desc = str(inv.get("Description") or inv.get("description") or "")
                match = _re.search(r"SYNC từ kênh \S+ - (\S+)", desc)
                src_code = match.group(1) if match else None
                if src_code:
                    synced_source_codes.add(src_code)
                if target_code:
                    target_entries.append((target_code, src_code, tid))
                dc = str(
                    inv.get("InvoiceDeliveryCode")
                    or inv.get("AllDeliveryCodes")
                    or inv.get("deliveryCode")
                    or ""
                ).strip()
                if dc:
                    synced_delivery_codes.add(dc)
            skip += len(web_data)
            if len(web_data) < check_top or (page_total and skip >= page_total):
                break
            if sleep_pages > 0:
                time.sleep(sleep_pages)

        self._stdout(f"  target total: {len(target_entries)}")
        self._stdout(f"  target synced source codes (from description): {len(synced_source_codes)}")
        self._stdout(f"  target delivery codes: {len(synced_delivery_codes)}")

        source_code_set = set(source_codes)
        missing: list[str] = [c for c in source_codes if c not in synced_source_codes]
        extra_orphan: list[tuple[str, str]] = []
        extra_unsynced: list[str] = []
        extras_to_delete: list[dict] = []
        from collections import defaultdict as _defaultdict
        synced_src_to_targets: dict[str, list[tuple[str, int | None]]] = _defaultdict(list)
        for target_code, src_code, tid in target_entries:
            if src_code is None:
                extra_unsynced.append(target_code)
            elif src_code not in source_code_set:
                extra_orphan.append((target_code, src_code))
                if tid is not None:
                    extras_to_delete.append(
                        {"id": tid, "code": target_code, "src_code": src_code}
                    )
            else:
                synced_src_to_targets[src_code].append((target_code, tid))
        duplicate_synced: dict[str, list[str]] = {}
        duplicates_to_delete: list[dict] = []
        for src, targets in synced_src_to_targets.items():
            if len(targets) <= 1:
                continue
            duplicate_synced[src] = [t[0] for t in targets]
            # Giữ đơn có target_code lớn nhất (mới nhất), xóa phần còn lại.
            targets_sorted = sorted(targets, key=lambda t: t[0] or "", reverse=True)
            for target_code, tid in targets_sorted[1:]:
                if tid is None:
                    continue
                duplicates_to_delete.append(
                    {"id": tid, "code": target_code, "src_code": src}
                )
        total_dup_extra = sum(len(t) - 1 for t in duplicate_synced.values())

        self._stdout(
            f"  result: source(A)={len(source_codes)}, target(B)={len(target_entries)}, "
            f"matched={len(source_codes) - len(missing)}, "
            f"missing_on_B={len(missing)}, "
            f"extra_on_B={len(extra_orphan) + len(extra_unsynced)} "
            f"(orphan_synced={len(extra_orphan)}, not_synced={len(extra_unsynced)}), "
            f"duplicate_synced={total_dup_extra} ({len(duplicate_synced)} source codes)"
        )
        if missing:
            self._stdout(f"  -- A có, B chưa có ({len(missing)}) --")
            for code in missing:
                self._stdout(f"    - {code}")
        if extra_orphan:
            self._stdout(
                f"  -- B có SYNC nhưng source code không nằm trong A [{from_dt} → {to_dt or '(open)'}] "
                f"(có thể đơn A ngoài khoảng thời gian hoặc đã hủy) ({len(extra_orphan)}) --"
            )
            for target_code, src_code in extra_orphan:
                self._stdout(f"    - B:{target_code}  (SYNC từ A:{src_code})")
        if extra_unsynced:
            self._stdout(
                f"  -- B không có SYNC desc, không map được sang A ({len(extra_unsynced)}) --"
            )
            for target_code in extra_unsynced:
                self._stdout(f"    - B:{target_code}")
        if duplicate_synced:
            self._stdout(
                f"  -- B có nhiều đơn cùng SYNC từ 1 source code (duplicate) "
                f"({len(duplicate_synced)} sources, {total_dup_extra} extra) --"
            )
            for src_code, target_codes in duplicate_synced.items():
                self._stdout(f"    - A:{src_code} -> {', '.join(f'B:{tc}' for tc in target_codes)}")

        return {
            "missing": missing,
            "extras_to_delete": extras_to_delete,
            "duplicates_to_delete": duplicates_to_delete,
        }

    # ------------------------------------------------------------------
    # Bulk delete target invoices by id
    # ------------------------------------------------------------------
    def _delete_target_by_ids(
        self,
        *,
        target_channel: KiotVietChannel,
        entries: list[dict],
        sleep_between: float,
        dry_run: bool,
    ) -> dict:
        result = {"deleted": 0, "errors": 0}
        if not entries:
            return result
        self._stdout(
            f"\n=== DELETE TARGET by id ({len(entries)} entries){' [DRY-RUN]' if dry_run else ''} ==="
        )
        for entry in entries:
            inv_id = int(entry["id"])
            code = entry.get("code") or ""
            src_code = entry.get("src_code") or ""
            label = f"id={inv_id} code={code} src={src_code}"
            if dry_run:
                self._stdout(f"  [dry-run] delete {label}")
                continue
            try:
                delete_invoice_by_id(invoice_id=inv_id, order=None, channel=target_channel)
                result["deleted"] += 1
                self._stdout(f"  deleted {label}")
            except Exception as exc:  # noqa: BLE001
                result["errors"] += 1
                self._stderr(f"  delete failed {label}: {exc}")
            if sleep_between > 0:
                time.sleep(sleep_between)
        self._stdout(
            f"  delete done: deleted={result['deleted']}, errors={result['errors']}"
        )
        return result

    # ------------------------------------------------------------------
    # Check price
    # ------------------------------------------------------------------
    def _check_price(
        self,
        *,
        source_channel: KiotVietChannel,
        source_branch_id: int,
        target_channel: KiotVietChannel,
        target_branch_id: int,
        from_dt: str,
        to_dt: str | None,
        page_size: int,
        sleep_pages: float,
    ) -> list[str]:
        self._stdout(
            f"\n=== CHECK PRICE: source branch={source_branch_id} -> target branch={target_branch_id} ==="
        )
        check_top = min(max(int(page_size or 0), 1), KIOTVIET_LIST_MAX_PAGE_SIZE)

        def _extract_discounts(inv: dict) -> tuple[Decimal, Decimal]:
            """Trả về (discount, discount_after_tax). None → 0."""
            d_raw = self._first_non_empty(inv.get("Discount"), inv.get("discount"), 0)
            d_after_raw = self._first_non_empty(
                inv.get("DiscountAfterTax"), inv.get("discountAfterTax"), 0,
            )
            return (self._to_decimal(d_raw), self._to_decimal(d_after_raw))

        # 1) Source: code -> (total, discount, discount_after_tax)
        source_map: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
        skip = 0
        while True:
            page_total, web_data = self._web_list_invoices(
                channel=source_channel,
                branch_id=source_branch_id,
                from_dt=from_dt,
                to_dt=to_dt,
                top=check_top,
                skip=skip,
            )
            self._stdout(f"  source page: skip={skip}, fetched={len(web_data)}, total={page_total}")
            if not web_data:
                break
            for inv in web_data:
                status_val = str(inv.get("StatusValue") or inv.get("statusValue") or "").strip().lower()
                status_code = inv.get("Status") or inv.get("status")
                if status_val in {"đã hủy", "đã huỷ", "cancelled"} or status_code == 2:
                    continue
                code = self._first_non_empty(inv.get("Code"), inv.get("code"), inv.get("invoiceCode"))
                if not code:
                    continue
                total = self._to_decimal(self._first_non_empty(
                    inv.get("Total"), inv.get("total"),
                    inv.get("TotalPayment"), inv.get("totalPayment"),
                ))
                discount, discount_after = _extract_discounts(inv)
                source_map[code] = (total, discount, discount_after)
            skip += len(web_data)
            if len(web_data) < check_top or (page_total and skip >= page_total):
                break
            if sleep_pages > 0:
                time.sleep(sleep_pages)

        self._stdout(f"  source total (excl. cancelled): {len(source_map)}")

        # 2) Target: src_code -> (target_code, target_total, target_discount, target_discount_after)
        target_map: dict[str, tuple[str, Decimal, Decimal, Decimal]] = {}
        skip = 0
        while True:
            page_total, web_data = self._web_list_invoices(
                channel=target_channel,
                branch_id=target_branch_id,
                from_dt=from_dt,
                to_dt=to_dt,
                top=check_top,
                skip=skip,
            )
            self._stdout(f"  target page: skip={skip}, fetched={len(web_data)}, total={page_total}")
            if not web_data:
                break
            for inv in web_data:
                status_val = str(inv.get("StatusValue") or inv.get("statusValue") or "").strip().lower()
                status_code = inv.get("Status") or inv.get("status")
                if status_val in {"đã hủy", "đã huỷ", "cancelled"} or status_code == 2:
                    continue
                desc = str(inv.get("Description") or inv.get("description") or "")
                match = _re.search(r"SYNC từ kênh \S+ - (\S+)", desc)
                if not match:
                    continue
                src_code = match.group(1)
                target_code = self._first_non_empty(inv.get("Code"), inv.get("code"), inv.get("invoiceCode"))
                total = self._to_decimal(self._first_non_empty(
                    inv.get("Total"), inv.get("total"),
                    inv.get("TotalPayment"), inv.get("totalPayment"),
                ))
                discount, discount_after = _extract_discounts(inv)
                target_map[src_code] = (target_code or "", total, discount, discount_after)
            skip += len(web_data)
            if len(web_data) < check_top or (page_total and skip >= page_total):
                break
            if sleep_pages > 0:
                time.sleep(sleep_pages)

        self._stdout(f"  target synced (excl. cancelled): {len(target_map)}")

        # 3) So sánh theo `total` (số tiền cuối) làm chân lý duy nhất.
        mismatches: list[tuple[str, Decimal, Decimal, Decimal, str, Decimal, Decimal, Decimal, str]] = []
        not_found_on_b: list[str] = []
        matched_same = 0

        for src_code, (src_total, src_d, src_da) in source_map.items():
            if src_code not in target_map:
                not_found_on_b.append(src_code)
                continue
            target_code, target_total, target_d, target_da = target_map[src_code]
            reasons: list[str] = []
            if src_total != target_total:
                reasons.append("total")
            # Chỉ flag theo `total`. KHÔNG so `discount`/`discountAfterTax`: A tách giảm giá
            # sản phẩm (line) và giảm giá hóa đơn (header) riêng, còn B gộp tất cả vào header
            # nên các field này cố tình khác nhau dù total khớp — so sẽ ra lệch giả.
            # (fix từ command a782572d; nhất quán với mode update_price cùng lý do.)
            if reasons:
                mismatches.append((
                    src_code, src_total, src_d, src_da,
                    target_code, target_total, target_d, target_da,
                    ",".join(reasons),
                ))
            else:
                matched_same += 1

        self._stdout(
            f"\n  result: source={len(source_map)}, matched_same={matched_same}, "
            f"mismatch={len(mismatches)}, not_found_on_B={len(not_found_on_b)}"
        )

        if mismatches:
            self._stdout(f"\n  -- Lệch ({len(mismatches)}) --")
            total_diff_sum = Decimal("0")
            d_diff_sum = Decimal("0")
            da_diff_sum = Decimal("0")
            for (src_code, src_total, src_d, src_da,
                 target_code, target_total, target_d, target_da, reason) in mismatches:
                t_diff = src_total - target_total
                d_diff = src_d - target_d
                da_diff = src_da - target_da
                total_diff_sum += t_diff
                d_diff_sum += d_diff
                da_diff_sum += da_diff
                self._stdout(
                    f"    - [{reason}] A:{src_code} total={src_total} discount={src_d} discountAfterTax={src_da} "
                    f"| B:{target_code} total={target_total} discount={target_d} discountAfterTax={target_da} "
                    f"| diff: total={t_diff:+}, discount={d_diff:+}, discountAfterTax={da_diff:+}"
                )
            self._stdout(
                f"\n  Tổng chênh lệch: total={total_diff_sum:+}, "
                f"discount={d_diff_sum:+}, discountAfterTax={da_diff_sum:+}"
            )
        else:
            self._stdout("\n  Không có đơn nào lệch.")

        if not_found_on_b:
            self._stdout(f"\n  -- Chưa có bên B ({len(not_found_on_b)}) --")
            for code in not_found_on_b:
                self._stdout(f"    - {code}")

        return [m[0] for m in mismatches]

    # ------------------------------------------------------------------
    # Compare 1 invoice A vs B (diagnostic, read-only)
    # ------------------------------------------------------------------
    def _compare_invoice(
        self,
        *,
        source_code: str,
        source_channel: KiotVietChannel,
        source_branch_id: int,
        target_channel: KiotVietChannel,
        target_branch_id: int,
        from_dt: str,
        to_dt: str | None,
        page_size: int,
        sleep_pages: float,
    ) -> None:
        """
        So sánh chi tiết 1 invoice giữa A và B (port từ CLI stack, WP8.1).
        1. Fetch source invoice trực tiếp qua get_invoice_by_code.
        2. Tìm target code qua description match (scan target list).
        3. Fetch target invoice qua get_invoice_by_code.
        4. In side-by-side so sánh các field quan trọng.
        """
        self._stdout(f"\n=== COMPARE INVOICE: {source_code} ===")

        # 1) Fetch source
        try:
            src_inv = get_invoice_by_code(source_code, order=None, channel=source_channel)
        except Exception as exc:  # noqa: BLE001
            raise SyncError(f"Fetch source {source_code} failed: {exc}") from exc
        if not isinstance(src_inv, dict) or not src_inv:
            raise SyncError(f"Source {source_code} không tồn tại")

        self._stdout("\n[A] Source invoice:")
        self._stdout(_json.dumps(src_inv, ensure_ascii=False, default=str, indent=2))

        # 2) Scan target list để tìm target code
        check_top = min(max(int(page_size or 0), 1), KIOTVIET_LIST_MAX_PAGE_SIZE)
        target_code = ""
        skip = 0
        self._stdout(f"\nScanning target to find SYNC match for {source_code}...")
        while True:
            page_total, web_data = self._web_list_invoices(
                channel=target_channel, branch_id=target_branch_id,
                from_dt=from_dt, to_dt=to_dt, top=check_top, skip=skip,
            )
            if not web_data:
                break
            for inv in web_data:
                desc = str(inv.get("Description") or inv.get("description") or "")
                m = _re.search(r"SYNC từ kênh \S+ - (\S+)", desc)
                if m and m.group(1) == source_code:
                    target_code = str(self._first_non_empty(
                        inv.get("Code"), inv.get("code"), ""
                    ))
                    break
            if target_code:
                break
            skip += len(web_data)
            if len(web_data) < check_top or (page_total and skip >= page_total):
                break
            if sleep_pages > 0:
                time.sleep(sleep_pages)

        if not target_code:
            self._stdout(
                f"\nKhông tìm thấy target invoice có SYNC desc cho {source_code} "
                f"trong khoảng [{from_dt} → {to_dt or '(open)'}]"
            )
            return

        # 3) Fetch target invoice
        try:
            tgt_inv = get_invoice_by_code(target_code, order=None, channel=target_channel)
        except Exception as exc:  # noqa: BLE001
            raise SyncError(f"Fetch target {target_code} failed: {exc}") from exc
        if not isinstance(tgt_inv, dict) or not tgt_inv:
            raise SyncError(f"Target {target_code} không tồn tại")

        self._stdout(f"\n[B] Target invoice ({target_code}):")
        self._stdout(_json.dumps(tgt_inv, ensure_ascii=False, default=str, indent=2))

        # 4) So sánh key fields
        self._stdout("\n=== DIFF SUMMARY ===")

        def _get(d: dict, *keys) -> object:
            for k in keys:
                v = d.get(k)
                if v is not None:
                    return v
            return None

        def _row(label: str, a_val, b_val, highlight: bool = False) -> None:
            mark = " ⚠️" if (highlight and a_val != b_val) else ("  ≠" if a_val != b_val else "")
            self._stdout(f"  {label:<28} A={a_val!r:<30} | B={b_val!r}{mark}")

        _row("total", _get(src_inv, "total", "Total"), _get(tgt_inv, "total", "Total"), True)
        _row("totalPayment", _get(src_inv, "totalPayment", "TotalPayment"), _get(tgt_inv, "totalPayment", "TotalPayment"))
        _row("discount", _get(src_inv, "discount", "Discount"), _get(tgt_inv, "discount", "Discount"))
        _row("discountAfterTax", _get(src_inv, "discountAfterTax", "DiscountAfterTax"), _get(tgt_inv, "discountAfterTax", "DiscountAfterTax"))
        _row("status", _get(src_inv, "status", "Status"), _get(tgt_inv, "status", "Status"))
        _row("statusValue", _get(src_inv, "statusValue", "StatusValue"), _get(tgt_inv, "statusValue", "StatusValue"))
        _row("pricingMode", _get(src_inv, "pricingMode", "PricingMode"), _get(tgt_inv, "pricingMode", "PricingMode"))
        _row("purchaseDate", _get(src_inv, "purchaseDate", "PurchaseDate"), _get(tgt_inv, "purchaseDate", "PurchaseDate"))
        _row("branchId", _get(src_inv, "branchId", "BranchId"), _get(tgt_inv, "branchId", "BranchId"))
        _row("totalTax", _get(src_inv, "totalTax", "TotalTax"), _get(tgt_inv, "totalTax", "TotalTax"))

        # Delivery
        src_del = src_inv.get("invoiceDelivery") or src_inv.get("InvoiceDelivery") or {}
        tgt_del = tgt_inv.get("invoiceDelivery") or tgt_inv.get("InvoiceDelivery") or {}
        _row("delivery.status", _get(src_del, "status", "Status"), _get(tgt_del, "status", "Status"))
        _row("delivery.deliveryCode", _get(src_del, "deliveryCode", "DeliveryCode"), _get(tgt_del, "deliveryCode", "DeliveryCode"))

        # Chi tiết items - so sánh từng line
        src_details = src_inv.get("invoiceDetails") or src_inv.get("InvoiceDetails") or []
        tgt_details = tgt_inv.get("invoiceDetails") or tgt_inv.get("InvoiceDetails") or []
        self._stdout(
            f"\n  invoiceDetails: A={len(src_details)} items, B={len(tgt_details)} items"
        )
        for i in range(max(len(src_details), len(tgt_details))):
            sd = src_details[i] if i < len(src_details) else {}
            td = tgt_details[i] if i < len(tgt_details) else {}
            self._stdout(f"  -- item [{i}] --")
            _row("  productCode", _get(sd, "productCode", "ProductCode"), _get(td, "productCode", "ProductCode"))
            _row("  quantity", _get(sd, "quantity", "Quantity"), _get(td, "quantity", "Quantity"), True)
            _row("  price", _get(sd, "price", "Price"), _get(td, "price", "Price"), True)
            _row("  subTotal", _get(sd, "subTotal", "SubTotal"), _get(td, "subTotal", "SubTotal"))
            _row("  allocationDiscount", _get(sd, "allocationDiscount", "AllocationDiscount"), _get(td, "allocationDiscount", "AllocationDiscount"))
            # Giá vốn (thử nhiều field name có thể có)
            _row("  cost (capital)", _get(sd, "capitalPrice", "CapitalPrice", "costPrice", "CostPrice", "cost"), _get(td, "capitalPrice", "CapitalPrice", "costPrice", "CostPrice", "cost"), True)
            # Tax row
            st_rows = sd.get("invoiceDetailTaxs") or sd.get("InvoiceDetailTaxs") or []
            tt_rows = td.get("invoiceDetailTaxs") or td.get("InvoiceDetailTaxs") or []
            st0 = st_rows[0] if st_rows and isinstance(st_rows[0], dict) else {}
            tt0 = tt_rows[0] if tt_rows and isinstance(tt_rows[0], dict) else {}
            _row("  tax.taxRate", _get(st0, "taxRate", "TaxRate"), _get(tt0, "taxRate", "TaxRate"))
            _row("  tax.priceAfterTax", _get(st0, "priceAfterTax", "PriceAfterTax"), _get(tt0, "priceAfterTax", "PriceAfterTax"))
            _row("  tax.subTotalAfterTax", _get(st0, "subTotalAfterTax", "SubTotalAfterTax"), _get(tt0, "subTotalAfterTax", "SubTotalAfterTax"))
            _row("  tax.taxAmount", _get(st0, "taxAmount", "TaxAmount"), _get(tt0, "taxAmount", "TaxAmount"))

    # ------------------------------------------------------------------
    # Update status (invoice + delivery) on target to match source
    # ------------------------------------------------------------------
    def _update_status(
        self,
        *,
        source_channel: KiotVietChannel,
        source_branch_id: int,
        target_channel: KiotVietChannel,
        target_branch_id: int,
        from_dt: str,
        to_dt: str | None,
        page_size: int,
        sleep_pages: float,
        sleep_between: float,
        dry_run: bool,
        specific_codes: list[str] | None = None,
        target_specific_codes: list[str] | None = None,
    ) -> None:
        """
        Scan A vs B, tìm các đơn lệch status, PUT update B (mode CLI-only, port nguyên
        văn từ command monolith khi hợp nhất engine — BE-1).
        - Nếu A.invoice=1 (Hoàn thành): set B.delivery=3 (Giao thành công) trước, rồi B.invoice=1.
        - Nếu A.invoice=3 và B.invoice=1: SKIP (KiotViet không cho revert Hoàn thành).
        - Case khác: update theo A.

        Filter:
        - specific_codes: filter theo SOURCE (A) code.
        - target_specific_codes: filter theo TARGET (B) code; suy ra source code từ description.
        """
        # Import cục bộ như bản command gốc — giữ 1 điểm patch duy nhất ở gateway cho test.
        from apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook import (  # noqa: PLC0415
            _kiotviet_request,
        )

        specific_codes_set = set(specific_codes) if specific_codes else None
        target_specific_codes_set = set(target_specific_codes) if target_specific_codes else None
        filter_note = ""
        if specific_codes_set:
            filter_note += f" [source_codes={','.join(sorted(specific_codes_set))}]"
        if target_specific_codes_set:
            filter_note += f" [target_codes={','.join(sorted(target_specific_codes_set))}]"
        self._stdout(
            f"\n=== UPDATE STATUS: source branch={source_branch_id} -> target branch={target_branch_id} "
            f"{'[DRY-RUN]' if dry_run else ''}{filter_note} ==="
        )

        check_top = min(max(int(page_size or 0), 1), KIOTVIET_LIST_MAX_PAGE_SIZE)

        def _extract_statuses(inv: dict) -> tuple[int | None, int | None]:
            inv_st_raw = self._first_non_empty(inv.get("Status"), inv.get("status"))
            try:
                inv_st = int(inv_st_raw) if inv_st_raw is not None else None
            except (TypeError, ValueError):
                inv_st = None
            del_st = None
            nested = inv.get("invoiceDelivery") or inv.get("InvoiceDelivery")
            if isinstance(nested, dict):
                del_st = self._first_non_empty(nested.get("Status"), nested.get("status"))
            if del_st is None:
                infoes = inv.get("DeliveryInfoes") or inv.get("deliveryInfoes")
                if isinstance(infoes, list) and infoes and isinstance(infoes[0], dict):
                    del_st = self._first_non_empty(infoes[0].get("Status"), infoes[0].get("status"))
            try:
                del_st = int(del_st) if del_st is not None else None
            except (TypeError, ValueError):
                del_st = None
            return inv_st, del_st

        # 1) Fetch B trước để (a) build target_map, (b) suy ra source codes nếu filter theo target.
        # src_code -> (target_id, target_code, inv_st, del_st)
        target_map: dict[str, tuple[int | None, str, int | None, int | None]] = {}
        skip = 0
        while True:
            page_total, web_data = self._web_list_invoices(
                channel=target_channel, branch_id=target_branch_id,
                from_dt=from_dt, to_dt=to_dt, top=check_top, skip=skip,
            )
            self._stdout(f"  target page: skip={skip}, fetched={len(web_data)}, total={page_total}")
            if not web_data:
                break
            for inv in web_data:
                sv = str(inv.get("StatusValue") or inv.get("statusValue") or "").strip().lower()
                sc = inv.get("Status") or inv.get("status")
                # Cancelled numeric = 2 (không phải 5): bản monolith revert (bc2dd1e5) còn giữ
                # mapping cũ ==5; e59650dc đã chốt 5 = "Không giao được", 2 = "Đã hủy".
                if sv in {"đã hủy", "đã huỷ", "cancelled"} or sc == 2:
                    continue
                tcode = self._first_non_empty(inv.get("Code"), inv.get("code"), "") or ""
                if target_specific_codes_set and tcode not in target_specific_codes_set:
                    continue
                desc = str(inv.get("Description") or inv.get("description") or "")
                m = _re.search(r"SYNC từ kênh \S+ - (\S+)", desc)
                if not m:
                    continue
                src_code = m.group(1)
                if specific_codes_set and src_code not in specific_codes_set:
                    continue
                tid = inv.get("Id") or inv.get("id")
                inv_st, del_st = _extract_statuses(inv)
                try:
                    tid_int = int(tid) if tid is not None else None
                except (TypeError, ValueError):
                    tid_int = None
                target_map[src_code] = (tid_int, tcode, inv_st, del_st)
            skip += len(web_data)
            if len(web_data) < check_top or (page_total and skip >= page_total):
                break
            if sleep_pages > 0:
                time.sleep(sleep_pages)

        # 2) Fetch A. Nếu filter theo target codes thì chỉ scan source codes đã suy ra từ B.
        effective_source_codes: set[str] | None
        if target_specific_codes_set:
            effective_source_codes = set(target_map.keys())
            if specific_codes_set:
                effective_source_codes &= specific_codes_set
        else:
            effective_source_codes = specific_codes_set

        source_map: dict[str, tuple[int | None, int | None]] = {}
        skip = 0
        while True:
            page_total, web_data = self._web_list_invoices(
                channel=source_channel, branch_id=source_branch_id,
                from_dt=from_dt, to_dt=to_dt, top=check_top, skip=skip,
            )
            self._stdout(f"  source page: skip={skip}, fetched={len(web_data)}, total={page_total}")
            if not web_data:
                break
            for inv in web_data:
                sv = str(inv.get("StatusValue") or inv.get("statusValue") or "").strip().lower()
                sc = inv.get("Status") or inv.get("status")
                if sv in {"đã hủy", "đã huỷ", "cancelled"} or sc == 2:
                    continue
                code = self._first_non_empty(inv.get("Code"), inv.get("code"), inv.get("invoiceCode"))
                if not code:
                    continue
                if effective_source_codes and code not in effective_source_codes:
                    continue
                source_map[code] = _extract_statuses(inv)
            skip += len(web_data)
            if len(web_data) < check_top or (page_total and skip >= page_total):
                break
            if sleep_pages > 0:
                time.sleep(sleep_pages)

        # 3) Iterate mismatches, PUT update
        updated = 0
        skipped_cannot_revert = 0
        failed = 0
        not_found = 0
        for src_code, (src_inv_st, src_del_st) in source_map.items():
            if src_code not in target_map:
                not_found += 1
                continue
            tid, tcode, tgt_inv_st, tgt_del_st = target_map[src_code]
            if src_inv_st == tgt_inv_st and src_del_st == tgt_del_st:
                continue
            # Case: A đang xử lý, B đã hoàn thành -> không revert được
            if src_inv_st == 3 and tgt_inv_st == 1:
                skipped_cannot_revert += 1
                self._stdout(
                    f"  SKIP [cannot-revert] A:{src_code}(inv={src_inv_st}) -> B:{tcode}(inv={tgt_inv_st}): "
                    f"KiotViet chặn revert từ Hoàn thành"
                )
                continue
            if tid is None:
                failed += 1
                self._stderr(f"  skip {src_code}: target id missing")
                continue

            # Xác định delivery target: nếu A không có delivery (None) nhưng A invoice=1 → set B delivery=3
            # để cho phép patch invoice=1. Nếu A có delivery → copy A.
            effective_del_st = src_del_st
            if src_del_st is None and src_inv_st == 1 and tgt_del_st is not None and tgt_del_st != 3:
                effective_del_st = 3  # Giao thành công

            endpoint = f"/invoices/{int(tid)}"
            actions: list[str] = []
            ok = True

            # Step 1: update delivery nếu cần
            if effective_del_st is not None and effective_del_st != tgt_del_st:
                payload = {"deliveryDetail": {
                    "status": int(effective_del_st),
                    "partnerDelivery": {"name": "DVVC cho đơn DESI"},
                }}
                if dry_run:
                    actions.append(f"delivery={tgt_del_st}->{effective_del_st} [dry-run]")
                else:
                    resp = _kiotviet_request(
                        order=None, channel=target_channel,
                        method="PUT", endpoint=endpoint, json=payload,
                    )
                    if resp.status_code in {200, 201, 204}:
                        actions.append(f"delivery={tgt_del_st}->{effective_del_st}")
                    else:
                        ok = False
                        actions.append(
                            f"delivery FAILED({resp.status_code}:{(resp.text or '')[:100]})"
                        )
                if sleep_between > 0:
                    time.sleep(sleep_between)

            # Step 2: update invoice status nếu cần
            if ok and src_inv_st is not None and src_inv_st != tgt_inv_st:
                payload = {"status": int(src_inv_st)}
                if dry_run:
                    actions.append(f"invoice={tgt_inv_st}->{src_inv_st} [dry-run]")
                else:
                    resp = _kiotviet_request(
                        order=None, channel=target_channel,
                        method="PUT", endpoint=endpoint, json=payload,
                    )
                    if resp.status_code in {200, 201, 204}:
                        actions.append(f"invoice={tgt_inv_st}->{src_inv_st}")
                    else:
                        ok = False
                        actions.append(
                            f"invoice FAILED({resp.status_code}:{(resp.text or '')[:100]})"
                        )
                if sleep_between > 0:
                    time.sleep(sleep_between)

            if ok:
                updated += 1
                self._stdout(f"  UPDATE A:{src_code} -> B:{tcode}: {', '.join(actions)}")
            else:
                failed += 1
                self._stderr(f"  FAIL A:{src_code} -> B:{tcode}: {', '.join(actions)}")

        self._stdout(
            f"\n  result: updated={updated}, skipped_cannot_revert={skipped_cannot_revert}, "
            f"failed={failed}, not_found_on_B={not_found}"
        )

    # ------------------------------------------------------------------
    # Check status (invoice + delivery)
    # ------------------------------------------------------------------
    def _check_status(
        self,
        *,
        source_channel: KiotVietChannel,
        source_branch_id: int,
        target_channel: KiotVietChannel,
        target_branch_id: int,
        from_dt: str,
        to_dt: str | None,
        page_size: int,
        sleep_pages: float,
    ) -> list[str]:
        """
        So sánh invoice status + delivery status giữa A và B.
        Target match qua description "SYNC từ kênh ... - {source_code}".
        """
        self._stdout(
            f"\n=== CHECK STATUS: source branch={source_branch_id} -> target branch={target_branch_id} ==="
        )
        check_top = min(max(int(page_size or 0), 1), KIOTVIET_LIST_MAX_PAGE_SIZE)

        def _extract_statuses(inv: dict) -> tuple[int | None, int | None]:
            """Trả về (invoice_status, delivery_status)."""
            inv_status_raw = self._first_non_empty(
                inv.get("Status"), inv.get("status"),
            )
            try:
                inv_status = int(inv_status_raw) if inv_status_raw is not None else None
            except (TypeError, ValueError):
                inv_status = None
            delivery_status = None
            nested = inv.get("invoiceDelivery") or inv.get("InvoiceDelivery")
            if isinstance(nested, dict):
                delivery_status = self._first_non_empty(
                    nested.get("Status"), nested.get("status"),
                )
            if delivery_status is None:
                infoes = inv.get("DeliveryInfoes") or inv.get("deliveryInfoes")
                if isinstance(infoes, list) and infoes and isinstance(infoes[0], dict):
                    delivery_status = self._first_non_empty(
                        infoes[0].get("Status"), infoes[0].get("status"),
                    )
            try:
                delivery_status = int(delivery_status) if delivery_status is not None else None
            except (TypeError, ValueError):
                delivery_status = None
            return inv_status, delivery_status

        # 1) Source: code -> (invoice_status, delivery_status)
        source_map: dict[str, tuple[int | None, int | None]] = {}
        skip = 0
        while True:
            page_total, web_data = self._web_list_invoices(
                channel=source_channel,
                branch_id=source_branch_id,
                from_dt=from_dt,
                to_dt=to_dt,
                top=check_top,
                skip=skip,
            )
            self._stdout(f"  source page: skip={skip}, fetched={len(web_data)}, total={page_total}")
            if not web_data:
                break
            for inv in web_data:
                status_val = str(inv.get("StatusValue") or inv.get("statusValue") or "").strip().lower()
                status_code = inv.get("Status") or inv.get("status")
                if status_val in {"đã hủy", "đã huỷ", "cancelled"} or status_code == 2:
                    continue
                code = self._first_non_empty(inv.get("Code"), inv.get("code"), inv.get("invoiceCode"))
                if not code:
                    continue
                source_map[code] = _extract_statuses(inv)
            skip += len(web_data)
            if len(web_data) < check_top or (page_total and skip >= page_total):
                break
            if sleep_pages > 0:
                time.sleep(sleep_pages)

        self._stdout(f"  source total (excl. cancelled): {len(source_map)}")

        # 2) Target: src_code -> (target_code, invoice_status, delivery_status)
        target_map: dict[str, tuple[str, int | None, int | None]] = {}
        skip = 0
        while True:
            page_total, web_data = self._web_list_invoices(
                channel=target_channel,
                branch_id=target_branch_id,
                from_dt=from_dt,
                to_dt=to_dt,
                top=check_top,
                skip=skip,
            )
            self._stdout(f"  target page: skip={skip}, fetched={len(web_data)}, total={page_total}")
            if not web_data:
                break
            for inv in web_data:
                status_val = str(inv.get("StatusValue") or inv.get("statusValue") or "").strip().lower()
                status_code = inv.get("Status") or inv.get("status")
                if status_val in {"đã hủy", "đã huỷ", "cancelled"} or status_code == 2:
                    continue
                desc = str(inv.get("Description") or inv.get("description") or "")
                match = _re.search(r"SYNC từ kênh \S+ - (\S+)", desc)
                if not match:
                    continue
                src_code = match.group(1)
                target_code = self._first_non_empty(inv.get("Code"), inv.get("code"), inv.get("invoiceCode"))
                inv_st, del_st = _extract_statuses(inv)
                target_map[src_code] = (target_code or "", inv_st, del_st)
            skip += len(web_data)
            if len(web_data) < check_top or (page_total and skip >= page_total):
                break
            if sleep_pages > 0:
                time.sleep(sleep_pages)

        self._stdout(f"  target synced (excl. cancelled): {len(target_map)}")

        # 3) So sánh
        mismatches: list[tuple[str, int | None, int | None, str, int | None, int | None, str]] = []
        not_found_on_b: list[str] = []
        matched_same = 0

        for src_code, (src_inv_st, src_del_st) in source_map.items():
            if src_code not in target_map:
                not_found_on_b.append(src_code)
                continue
            target_code, tgt_inv_st, tgt_del_st = target_map[src_code]
            reasons: list[str] = []
            if src_inv_st != tgt_inv_st:
                reasons.append("invoice_status")
            if src_del_st != tgt_del_st:
                reasons.append("delivery_status")
            if reasons:
                mismatches.append((
                    src_code, src_inv_st, src_del_st,
                    target_code, tgt_inv_st, tgt_del_st,
                    ",".join(reasons),
                ))
            else:
                matched_same += 1

        self._stdout(
            f"\n  result: source={len(source_map)}, matched_same={matched_same}, "
            f"mismatch={len(mismatches)}, not_found_on_B={len(not_found_on_b)}"
        )

        if mismatches:
            self._stdout(f"\n  -- Lệch status ({len(mismatches)}) --")
            for (src_code, src_inv_st, src_del_st,
                 target_code, tgt_inv_st, tgt_del_st, reason) in mismatches:
                self._stdout(
                    f"    - [{reason}] A:{src_code} invoice={src_inv_st} delivery={src_del_st} "
                    f"| B:{target_code} invoice={tgt_inv_st} delivery={tgt_del_st}"
                )
        else:
            self._stdout("\n  Không có đơn nào lệch status.")

        if not_found_on_b:
            self._stdout(f"\n  -- Chưa có bên B ({len(not_found_on_b)}) --")
            for code in not_found_on_b:
                self._stdout(f"    - {code}")

        return [m[0] for m in mismatches]
