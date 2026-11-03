"""Branch-purge operations for SyncInvoicesService (extracted mixin)."""

import re as _re
import time
from collections import defaultdict
from datetime import datetime

from django.utils.dateparse import parse_datetime

from apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook import (
    _kiotviet_request_with_retry,
)
from apps.kiotviet.infrastructure.services.invoice_mutations_push import delete_invoice_by_id
from apps.kiotviet.infrastructure.services.invoice_sync_queries import _extract_invoice_items
from apps.kiotviet.models import KiotVietChannel

from ._sync_service_shared import KIOTVIET_LIST_MAX_PAGE_SIZE, SyncError


class SyncPurgeMixin:
    def _purge_target_branch(
        self,
        *,
        target_channel: KiotVietChannel,
        target_branch_id: int,
        from_dt: str,
        to_dt: str | None,
        page_size: int,
        dry_run: bool,
        yes_flag: bool,
        sleep_pages: float,
        sleep_between: float,
        summary: dict,
        only_source_codes: set[str] | None = None,
    ) -> None:
        mode_label = (
            f"(chỉ source codes: {','.join(sorted(only_source_codes))})"
            if only_source_codes else "(tất cả)"
        )
        self._stdout(
            f"\n=== PURGE target branch={target_branch_id} from={from_dt} to={to_dt or '(open)'} {mode_label} ==="
        )

        to_delete: list[tuple[int, str, str]] = []
        current_item = 0
        pages = 0

        while True:
            params = {
                "pageSize": page_size,
                "currentItem": current_item,
                "branchIds": str(target_branch_id),
                "fromPurchaseDate": from_dt,
                "includeInvoiceDelivery": "true",
                # Sort id bất biến → phân trang public /invoices ổn định, không lặp/sót.
                "orderBy": "id",
                "orderDirection": "Asc",
            }
            if to_dt:
                params["toPurchaseDate"] = to_dt
            # Retry 5xx qua helper dùng chung (BE-5) — semantics giữ nguyên vòng tay cũ.
            response = _kiotviet_request_with_retry(
                order=None,
                channel=target_channel,
                method="GET",
                endpoint="/invoices",
                params=params,
            )
            if response.status_code != 200:
                raise SyncError(
                    f"GET /invoices failed: status={response.status_code}, "
                    f"body={(response.text or '').strip()[:500]}"
                )

            payload = response.json() if response.content else {}
            invoices = _extract_invoice_items(payload)
            total = payload.get("total") or payload.get("Total") or 0

            pages += 1
            self._stdout(
                f"  list page {pages}: currentItem={current_item}, fetched={len(invoices)}, total={total}"
            )
            if not invoices:
                break

            in_range_count = 0
            for invoice in invoices:
                purchase_date_raw = self._first_non_empty(
                    invoice.get("purchaseDate"),
                    invoice.get("PurchaseDate"),
                )

                status_val = str(
                    invoice.get("statusValue") or invoice.get("StatusValue") or ""
                ).strip().lower()
                status_code = invoice.get("status") or invoice.get("Status")
                if status_val in {"đã hủy", "đã huỷ", "cancelled"} or status_code == 2:
                    continue

                inv_delivery = invoice.get("invoiceDelivery") or invoice.get("InvoiceDelivery") or {}
                if isinstance(inv_delivery, dict):
                    delivery_status = inv_delivery.get("status") or inv_delivery.get("Status")
                    if delivery_status == 6:
                        continue

                invoice_id = invoice.get("id") or invoice.get("Id") or invoice.get("invoiceId")
                invoice_code = self._first_non_empty(
                    invoice.get("code"),
                    invoice.get("Code"),
                    invoice.get("invoiceCode"),
                )
                if invoice_id in (None, -1, 0):
                    continue

                if only_source_codes is not None:
                    desc = str(invoice.get("description") or invoice.get("Description") or "")
                    m = _re.search(r"SYNC từ kênh \S+ - (\S+)", desc)
                    if not m or m.group(1) not in only_source_codes:
                        continue

                try:
                    to_delete.append((int(invoice_id), str(invoice_code or ""), str(purchase_date_raw or "")))
                    in_range_count += 1
                except (TypeError, ValueError):
                    continue

            self._stdout(f"    in_range={in_range_count}")

            current_item += len(invoices)
            if len(invoices) < page_size:
                break
            if total and current_item >= int(total):
                break
            if sleep_pages > 0:
                time.sleep(sleep_pages)

        total_to_delete = len(to_delete)
        self._stdout(f"  found {total_to_delete} invoice(s) to delete on target")
        for inv_id, inv_code, purchase_dt in to_delete:
            self._stdout(f"    - id={inv_id} code={inv_code} purchaseDate={purchase_dt}")

        if total_to_delete == 0:
            return

        if dry_run:
            self._stdout("  dry-run: skip actual delete")
            return

        if not yes_flag:
            self._stderr(
                "  refused to delete: --purge-target requires --yes (or --dry-run) to actually run DELETE"
            )
            raise SyncError("Missing --yes for --purge-target.")

        for inv_id, inv_code, _pd in to_delete:
            try:
                delete_invoice_by_id(invoice_id=inv_id, order=None, channel=target_channel)
                summary["purged"] += 1
                self._stdout(f"    deleted id={inv_id} code={inv_code}")
            except Exception as exc:  # noqa: BLE001
                summary["purge_errors"] += 1
                self._stderr(f"    delete failed id={inv_id} code={inv_code}: {exc}")
            if sleep_between > 0:
                time.sleep(sleep_between)

        self._stdout(
            f"  purge done: purged={summary['purged']}, errors={summary['purge_errors']}"
        )

    # ------------------------------------------------------------------
    # Purge cancelled
    # ------------------------------------------------------------------
    def _purge_cancelled(
        self,
        *,
        source_channel: KiotVietChannel,
        source_branch_id: int,
        target_channel: KiotVietChannel,
        target_branch_id: int,
        from_dt: str,
        to_dt: str | None,
        page_size: int,
        dry_run: bool,
        yes_flag: bool,
        sleep_pages: float,
        sleep_between: float,
    ) -> None:
        self._stdout(
            f"\n=== PURGE CANCELLED: source branch={source_branch_id} -> target branch={target_branch_id} ==="
        )

        check_top = min(max(int(page_size or 0), 1), KIOTVIET_LIST_MAX_PAGE_SIZE)

        cancelled_codes: list[str] = []
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
                    code = self._first_non_empty(inv.get("Code"), inv.get("code"), inv.get("invoiceCode"))
                    if code:
                        cancelled_codes.append(code)
            skip += len(web_data)
            if len(web_data) < check_top or (page_total and skip >= page_total):
                break
            if sleep_pages > 0:
                time.sleep(sleep_pages)

        self._stdout(f"  cancelled invoices on A: {len(cancelled_codes)}")
        if not cancelled_codes:
            self._stdout("  Không có đơn hủy nào.")
            return

        for code in cancelled_codes:
            self._stdout(f"    - {code}")

        target_map: dict[str, tuple[int, str]] = {}
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
                # B đã huỷ sẵn → đã ở trạng thái đích (KiotViet DELETE = huỷ mềm, không xoá cứng).
                # Cố huỷ lại sẽ trả 420 "Không thể hủy hóa đơn ở trạng thái đã hủy" → bỏ qua để
                # hết noise lặp lại mỗi ngày (53 lỗi/đêm trong log reconcile 21h).
                b_status_val = str(inv.get("StatusValue") or inv.get("statusValue") or "").strip().lower()
                b_status_code = inv.get("Status") or inv.get("status")
                if b_status_val in {"đã hủy", "đã huỷ", "cancelled"} or b_status_code == 2:
                    continue
                desc = str(inv.get("Description") or inv.get("description") or "")
                match = _re.search(r"SYNC từ kênh \S+ - (\S+)", desc)
                if match:
                    src_code = match.group(1)
                    inv_id = inv.get("Id") or inv.get("id")
                    inv_code = self._first_non_empty(inv.get("Code"), inv.get("code"))
                    if inv_id and inv_id not in (None, -1, 0):
                        target_map[src_code] = (int(inv_id), str(inv_code))
            skip += len(web_data)
            if len(web_data) < check_top or (page_total and skip >= page_total):
                break
            if sleep_pages > 0:
                time.sleep(sleep_pages)

        self._stdout(f"  target synced invoices mapped: {len(target_map)}")

        to_delete: list[tuple[int, str, str]] = []
        for src_code in cancelled_codes:
            if src_code in target_map:
                inv_id, inv_code_b = target_map[src_code]
                to_delete.append((inv_id, inv_code_b, src_code))

        self._stdout(f"  found {len(to_delete)} cancelled invoice(s) to delete on B")
        for inv_id, inv_code_b, src_code in to_delete:
            self._stdout(f"    - B: id={inv_id} code={inv_code_b} <- A: {src_code}")

        if not to_delete:
            self._stdout("  Không cần xóa gì.")
            return

        if dry_run:
            self._stdout("  dry-run: skip actual delete")
            return

        if not yes_flag:
            self._stderr("  --purge-cancelled requires --yes (or --dry-run) to actually run DELETE")
            raise SyncError("Missing --yes for --purge-cancelled.")

        deleted = 0
        errors = 0
        for inv_id, inv_code_b, src_code in to_delete:
            try:
                delete_invoice_by_id(invoice_id=inv_id, order=None, channel=target_channel)
                deleted += 1
                self._stdout(f"    deleted B: id={inv_id} code={inv_code_b} <- A: {src_code}")
            except Exception as exc:  # noqa: BLE001
                errors += 1
                self._stderr(f"    delete failed B: id={inv_id} code={inv_code_b}: {exc}")
            if sleep_between > 0:
                time.sleep(sleep_between)

        self._stdout(f"  purge cancelled done: deleted={deleted}, errors={errors}")

    # ------------------------------------------------------------------
    # Purge duplicates
    # ------------------------------------------------------------------
    @staticmethod
    def _select_duplicates_to_delete(
        entries_by_src: dict[str, list[dict]],
    ) -> tuple[list[dict], list[dict]]:
        """Chọn đơn giữ/xóa cho mỗi nhóm duplicate.

        entries_by_src: {src_code: [{id, code, created_date}, ...]}.
        Trả về (to_keep, to_delete); mỗi entry gắn thêm "src". Giữ đơn cũ nhất theo
        (created_date, code); entry thiếu/parse lỗi created_date bị đẩy xuống cuối.
        Chỉ nhóm có >1 entry mới sinh kết quả.
        """
        def _sort_key(entry: dict):
            raw = str(entry.get("created_date") or "").strip()
            dt = None
            if raw:
                try:
                    dt = parse_datetime(raw)
                except ValueError:
                    dt = None
            if dt is not None and dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return (dt is None, dt or datetime.min, str(entry.get("code") or ""))

        to_keep: list[dict] = []
        to_delete: list[dict] = []
        for src, entries in entries_by_src.items():
            if not entries or len(entries) < 2:
                continue
            ordered = sorted(({**e, "src": src} for e in entries), key=_sort_key)
            to_keep.append(ordered[0])
            to_delete.extend(ordered[1:])
        return to_keep, to_delete

    def _purge_duplicates(
        self,
        *,
        target_channel: KiotVietChannel,
        target_branch_id: int,
        from_dt: str,
        to_dt: str | None,
        page_size: int,
        dry_run: bool,
        yes_flag: bool,
        sleep_pages: float,
        sleep_between: float,
    ) -> None:
        """Quét đơn B trong [from, to], gom theo source_code lấy từ description
        "SYNC từ kênh ... - {src}" (bỏ đơn đã hủy). source_code nào có >1 đơn là
        duplicate → giữ đơn cũ nhất, xóa phần còn lại."""
        self._stdout(
            f"\n=== PURGE DUPLICATES: target branch={target_branch_id} "
            f"from={from_dt} to={to_dt or '(open)'} ==="
        )
        check_top = min(max(int(page_size or 0), 1), KIOTVIET_LIST_MAX_PAGE_SIZE)
        entries_by_src: dict[str, list[dict]] = defaultdict(list)
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
                status_val = str(inv.get("StatusValue") or inv.get("statusValue") or "").strip().lower()
                status_code = inv.get("Status") or inv.get("status")
                if status_val in {"đã hủy", "đã huỷ", "cancelled"} or status_code == 2:
                    continue
                desc = str(inv.get("Description") or inv.get("description") or "")
                match = _re.search(r"SYNC từ kênh \S+ - (\S+)", desc)
                if not match:
                    continue
                inv_id = inv.get("Id") or inv.get("id")
                if inv_id in (None, -1, 0):
                    continue
                entries_by_src[match.group(1)].append({
                    "id": int(inv_id),
                    "code": self._first_non_empty(inv.get("Code"), inv.get("code")) or "",
                    "created_date": self._first_non_empty(
                        inv.get("CreatedDate"), inv.get("createdDate"),
                        inv.get("PurchaseDate"), inv.get("purchaseDate"),
                    ) or "",
                })
            skip += len(web_data)
            if len(web_data) < check_top or (page_total and skip >= page_total):
                break
            if sleep_pages > 0:
                time.sleep(sleep_pages)

        to_keep, to_delete = self._select_duplicates_to_delete(dict(entries_by_src))
        dup_groups = len(to_keep)
        self._stdout(f"  duplicate groups={dup_groups}, to_delete={len(to_delete)}")
        if not to_delete:
            self._stdout("  Không có duplicate nào.")
            return

        keep_by_src = {e["src"]: e for e in to_keep}
        delete_by_src: dict[str, list[dict]] = {}
        for e in to_delete:
            delete_by_src.setdefault(e["src"], []).append(e)
        for src in sorted(delete_by_src):
            kept = keep_by_src.get(src, {})
            dels = ", ".join(f"B:{d['code']}(id={d['id']})" for d in delete_by_src[src])
            self._stdout(f"    - A:{src} -> KEEP B:{kept.get('code')}(id={kept.get('id')}) | DELETE {dels}")

        if dry_run:
            self._stdout("  dry-run: skip actual delete")
            return
        if not yes_flag:
            raise SyncError("purge_duplicates cần yes=True (hoặc dry_run) để thực thi DELETE.")

        deleted = 0
        errors = 0
        for d in to_delete:
            try:
                delete_invoice_by_id(invoice_id=d["id"], order=None, channel=target_channel)
                deleted += 1
                self._stdout(f"    deleted B:{d['code']}(id={d['id']}) <- A:{d['src']}")
            except Exception as exc:  # noqa: BLE001
                errors += 1
                self._stderr(f"    delete failed B:{d['code']}(id={d['id']}): {exc}")
            if sleep_between > 0:
                time.sleep(sleep_between)

        self._stdout(
            f"  purge duplicates done: groups={dup_groups}, kept={dup_groups}, "
            f"deleted={deleted}, errors={errors}"
        )

    # ------------------------------------------------------------------
    # Purge wrong date
    # ------------------------------------------------------------------
    def _purge_wrong_date(
        self,
        *,
        source_channel: KiotVietChannel,
        source_branch_id: int,
        target_channel: KiotVietChannel,
        target_branch_id: int,
        from_dt: str,
        to_dt: str | None,
        page_size: int,
        dry_run: bool,
        yes_flag: bool,
        sleep_pages: float,
        sleep_between: float,
    ) -> None:
        self._stdout(f"\n=== PURGE WRONG DATE: source [from={from_dt}, to={to_dt}] ===")

        check_top = min(max(int(page_size or 0), 1), KIOTVIET_LIST_MAX_PAGE_SIZE)

        source_codes: set[str] = set()
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
                code = self._first_non_empty(inv.get("Code"), inv.get("code"), inv.get("invoiceCode"))
                if code:
                    source_codes.add(code)
            skip += len(web_data)
            if len(web_data) < check_top or (page_total and skip >= page_total):
                break
            if sleep_pages > 0:
                time.sleep(sleep_pages)

        self._stdout(f"  source codes in range: {len(source_codes)}")
        if not source_codes:
            self._stdout("  Không có source code nào trong range.")
            return

        to_delete: list[tuple[int, str, str, str]] = []
        skip = 0
        while True:
            lastmod_hint = self._shift_dt(from_dt, days=-7)
            try:
                page_total, web_data = self._web_list_invoices(
                    channel=target_channel,
                    branch_id=target_branch_id,
                    from_dt=lastmod_hint,
                    to_dt=None,
                    top=check_top,
                    skip=skip,
                )
            except Exception as exc:  # noqa: BLE001
                self._stderr(f"  scan B failed: {exc}")
                break
            self._stdout(f"  target page: skip={skip}, fetched={len(web_data)}, total={page_total}")
            if not web_data:
                break
            for inv in web_data:
                desc = str(inv.get("Description") or inv.get("description") or "")
                match = _re.search(r"SYNC từ kênh \S+ - (\S+)", desc)
                if not match:
                    continue
                src_code = match.group(1)
                if src_code not in source_codes:
                    continue
                pd_b = self._first_non_empty(
                    inv.get("PurchaseDate"),
                    inv.get("purchaseDate"),
                    inv.get("ComparePurchaseDate"),
                )
                in_range = True
                if self._is_after(from_dt, pd_b):
                    in_range = False
                if to_dt and self._is_after(pd_b, to_dt):
                    in_range = False
                if in_range:
                    continue
                inv_id = inv.get("Id") or inv.get("id")
                inv_code_b = self._first_non_empty(inv.get("Code"), inv.get("code"))
                if inv_id and inv_id not in (None, -1, 0):
                    to_delete.append((int(inv_id), str(inv_code_b), src_code, str(pd_b or "")))
            skip += len(web_data)
            if len(web_data) < check_top or (page_total and skip >= page_total):
                break
            if sleep_pages > 0:
                time.sleep(sleep_pages)

        self._stdout(f"  found {len(to_delete)} invoice(s) with wrong date to delete on B")
        for inv_id, inv_code_b, src_code, pd_b in to_delete:
            self._stdout(
                f"    - B: id={inv_id} code={inv_code_b} purchaseDate={pd_b} <- A: {src_code}"
            )

        if not to_delete:
            self._stdout("  Không cần xóa gì.")
            return

        if dry_run:
            self._stdout("  dry-run: skip actual delete")
            return

        if not yes_flag:
            self._stderr("  --purge-wrong-date requires --yes (or --dry-run) to actually run DELETE")
            raise SyncError("Missing --yes for --purge-wrong-date.")

        deleted = 0
        errors = 0
        for inv_id, inv_code_b, src_code, _pd_b in to_delete:
            try:
                delete_invoice_by_id(invoice_id=inv_id, order=None, channel=target_channel)
                deleted += 1
                self._stdout(f"    deleted B: id={inv_id} code={inv_code_b} <- A: {src_code}")
            except Exception as exc:  # noqa: BLE001
                errors += 1
                self._stderr(f"    delete failed B: id={inv_id} code={inv_code_b}: {exc}")
            if sleep_between > 0:
                time.sleep(sleep_between)

        self._stdout(f"  purge wrong date done: deleted={deleted}, errors={errors}")
