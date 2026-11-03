"""Đồng bộ phiếu trả hàng KiotViet (GET /returns) → OrderRecall.return_slip_code.

Map theo invoiceId → Order.invoice_id (fallback /invoices/{id} lấy code khi đơn cũ
thiếu invoice_id). Policy ghi = UNION: giữ nguyên mọi mã đang có (nhập tay hoặc sync
trước đó) theo đúng thứ tự, chỉ nối thêm mã KiotViet chưa có — không xoá/sắp xếp lại
dữ liệu nhân viên nhập tay.
"""

import logging
from collections.abc import Iterator

from celery.exceptions import SoftTimeLimitExceeded
from django.db import transaction
from django.utils.dateparse import parse_datetime

from apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook import _kiotviet_request
from apps.kiotviet.models import KiotVietChannel
from apps.orders.application.services.order_service_helpers import _parse_slip_codes
from apps.orders.models import Order, OrderRecall

logger = logging.getLogger(__name__)

PAGE_SIZE = 100
# Van an toàn: nếu KiotViet lờ currentItem (bug/đổi hợp đồng API) thì phân trang có thể
# không bao giờ dừng — chặn cứng để vòng lặp luôn kết thúc thay vì treo task.
MAX_PAGES = 1000


def _empty_summary() -> dict:
    return {
        "read": 0,
        "matched": 0,
        "updated": 0,
        "skipped_no_order": 0,
        "skipped_no_code": 0,
        "skipped_no_new_codes": 0,
        "skipped_out_of_window": 0,
        "skipped_invalid_invoice_id": 0,
        "pages": 0,
    }


def iter_returns(channel: KiotVietChannel, *, last_modified_from: str | None = None) -> Iterator[list[dict]]:
    """Phân trang GET /returns. Raise RuntimeError khi KiotViet trả != 200 (không nuốt lỗi)."""
    current_item = 0
    pages_fetched = 0
    while True:
        pages_fetched += 1
        if pages_fetched > MAX_PAGES:
            raise RuntimeError(
                f"KiotViet /returns vượt quá {MAX_PAGES} trang (channel_id={channel.id}) — "
                "dừng để tránh vòng lặp vô hạn (khả năng KiotViet bỏ qua currentItem)."
            )

        params: dict = {"pageSize": PAGE_SIZE, "currentItem": current_item}
        if last_modified_from:
            params["lastModifiedFrom"] = last_modified_from

        response = _kiotviet_request(
            None, channel, "GET", "/returns", params=params
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"KiotViet /returns failed (channel_id={channel.id}): "
                f"status={response.status_code}, body={(response.text or '').strip()}"
            )

        payload = response.json() if response.content else {}
        items = payload.get("data") or []
        yield items
        if len(items) < PAGE_SIZE:
            return
        current_item += len(items)


def _parse_return_date(raw: str):
    """parse_datetime raises ValueError on a well-formed but invalid date (e.g. 2026-02-30)."""
    if not raw:
        return None
    try:
        return parse_datetime(raw)
    except ValueError:
        return None


def _map_invoice_ids_to_orders(channel: KiotVietChannel, invoice_ids: set[int]) -> dict[int, int]:
    """Map invoiceId -> order_id cho CẢ LÔ bằng MỘT query (không N+1, không gọi thêm API).

    Cố ý KHÔNG có fallback /invoices/{id} cho các id trượt: khi lô có nhiều id lạ (vd
    hoá đơn không thuộc kênh này), fallback bắn 1 request/id và KiotViet trả 420
    (rate-limit) — tốn hàng nghìn call mà vẫn không khớp được đơn nào. Trượt = phiếu
    của hoá đơn ta không quản lý → đếm skipped_no_order và bỏ qua.
    """
    if not invoice_ids:
        return {}

    return dict(
        Order.objects.filter(
            kiotviet_channel=channel,
            invoice_id__in=invoice_ids,
            deleted_at__isnull=True,
        ).values_list("invoice_id", "id")
    )


def _in_date_window(raw_date: str, date_from: str | None, date_to: str | None) -> bool:
    """Lọc client-side theo returnDate (KiotViet /returns không nhận khoảng ngày trả)."""
    if not date_from and not date_to:
        return True
    parsed = _parse_return_date(raw_date)
    if parsed is None:
        return False
    day = parsed.date().isoformat()
    if date_from and day < date_from:
        return False
    if date_to and day > date_to:
        return False
    return True


def _sort_returns_by_date(entries: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Sort theo returnDate đã parse (không phải chuỗi thô). Mục không parse được giữ
    nguyên thứ tự tương đối (sort ổn định) và xếp sau các mục parse được."""

    def key(indexed_entry: tuple[int, tuple[str, str]]):
        index, (raw_date, _code) = indexed_entry
        parsed = _parse_return_date(raw_date)
        if parsed is None:
            return (1, index)
        return (0, parsed)

    ordered = sorted(enumerate(entries), key=key)
    return [entry for _index, entry in ordered]


def _union_codes(existing: list[str], incoming: list[str]) -> list[str]:
    """Nối `incoming` (đã chuẩn hoá qua _parse_slip_codes) vào cuối `existing`, giữ
    nguyên thứ tự và nội dung `existing` — dữ liệu nhân viên nhập tay hoặc đã sync
    trước đó không bao giờ bị xoá/sắp xếp lại."""
    merged = list(existing)
    for code in incoming:
        if code not in merged:
            merged.append(code)
    return merged


def sync_returns_for_channel(
    channel: KiotVietChannel,
    *,
    last_modified_from: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    dry_run: bool = False,
) -> dict:
    summary = _empty_summary()

    # PHA 1 — gom TOÀN BỘ phiếu trả trong khoảng ngày (chưa đụng DB).
    # collected: list[(invoice_id, returnDate, code)]
    collected: list[tuple[int, str, str]] = []
    for items in iter_returns(channel, last_modified_from=last_modified_from):
        summary["pages"] += 1
        for item in items:
            summary["read"] += 1
            code = str(item.get("code") or "").strip()
            invoice_id = item.get("invoiceId")
            return_date = str(item.get("returnDate") or "")
            if not code or not invoice_id:
                summary["skipped_no_code"] += 1
                continue
            if not _in_date_window(return_date, date_from, date_to):
                summary["skipped_out_of_window"] += 1
                continue
            # invoiceId rác (KiotViet trả kiểu lạ / không parse được số) không được abort
            # cả kênh — bỏ item này, đếm riêng, các item còn lại vẫn xử lý bình thường.
            try:
                collected.append((int(invoice_id), return_date, code))
            except (TypeError, ValueError):
                summary["skipped_invalid_invoice_id"] += 1
                continue

    # PHA 2 — map cả lô sang order bằng 2 query (không N+1).
    invoice_to_order = _map_invoice_ids_to_orders(channel, {inv for inv, _d, _c in collected})

    # order_id -> list[(returnDate, code)] để sort theo ngày rồi dedupe
    by_order: dict[int, list[tuple[str, str]]] = {}
    for invoice_id, return_date, code in collected:
        order_id = invoice_to_order.get(invoice_id)
        if order_id is None:
            summary["skipped_no_order"] += 1
            continue
        summary["matched"] += 1
        by_order.setdefault(order_id, []).append((return_date, code))

    # PHA 3 — ghi (UNION) từng đơn.
    for order_id, entries in by_order.items():
        kv_codes = _parse_slip_codes([code for _date, code in _sort_returns_by_date(entries)])

        if dry_run:
            # Không lock/không tạo row khi dry-run: chỉ đọc để mô phỏng kết quả.
            recall = OrderRecall.objects.filter(order_id=order_id).first()
            existing_codes = list(recall.return_slip_code) if recall is not None else []
            merged = _union_codes(existing_codes, kv_codes)
            if merged == existing_codes:
                summary["skipped_no_new_codes"] += 1
                continue
            summary["updated"] += 1
            logger.info("DRY-RUN order_id=%s return_slip_code -> %s", order_id, merged)
            continue

        # transaction.atomic() + select_for_update() + get_or_create(): khoá đúng dòng
        # recall trong lúc đọc-sửa-ghi (chống 1 PATCH tay của nhân viên chạy song song
        # bị sync ghi đè), và get_or_create tự bắt IntegrityError khi 2 tiến trình cùng
        # tạo recall lần đầu — order khác trong vòng lặp không bị lỗi này chặn theo.
        with transaction.atomic():
            recall, _created = OrderRecall.objects.select_for_update().get_or_create(order_id=order_id)
            existing_codes = list(recall.return_slip_code)
            merged = _union_codes(existing_codes, kv_codes)
            if merged == existing_codes:
                summary["skipped_no_new_codes"] += 1
                continue
            recall.return_slip_code = merged
            recall.save(update_fields=["return_slip_code", "updated_at"])
            summary["updated"] += 1

    return summary


def sync_returns_all_channels(
    *,
    last_modified_from: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Lặp mọi KiotVietChannel active, BỎ QUA kênh cũ (mirror). Một kênh lỗi KHÔNG chặn
    kênh còn lại.

    Kênh cũ (`old=True`) bị loại vì hoá đơn của nó nằm ở `Order.old_id` / `old_invoice_code`
    chứ không phải `Order.invoice_id` (trường này là hoá đơn kênh MỚI, và `Order.kiotviet_channel`
    cũng trỏ tới kênh mới). Sync kênh cũ theo `invoice_id` không bao giờ khớp — chỉ tạo ra
    hàng nghìn call vô ích và mã phiếu trả trùng lặp của cùng một lần trả hàng.
    """
    total = _empty_summary()
    total["channels"] = 0
    total["errors"] = []

    for channel in KiotVietChannel.objects.filter(deleted_at__isnull=True, old=False):
        total["channels"] += 1
        try:
            summary = sync_returns_for_channel(
                channel,
                last_modified_from=last_modified_from,
                date_from=date_from,
                date_to=date_to,
                dry_run=dry_run,
            )
        except SoftTimeLimitExceeded:
            # Soft-timeout của celery phải abort CẢ RUN (task sắp bị SIGKILL bởi hard
            # time_limit) — không được nuốt vào summary["errors"] rồi chạy tiếp kênh
            # sau, nếu không worker cứ ăn hết soft limit tới lúc hard-kill giữa chừng.
            raise
        except Exception as exc:  # noqa: BLE001 — lỗi 1 kênh không được chặn kênh khác
            logger.exception("sync_returns failed for channel_id=%s", channel.id)
            total["errors"].append({"channel_id": channel.id, "error": str(exc)})
            continue

        for key in _empty_summary():
            total[key] += summary.get(key, 0)

    return total
