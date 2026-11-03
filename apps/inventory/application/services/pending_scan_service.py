"""Bảng chờ mã vận đơn quét chưa khớp đơn + cron tự khớp.

Xem spec: docs/erp-inventory-pending-scan.md.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from celery.exceptions import SoftTimeLimitExceeded
from django.db import IntegrityError, transaction
from django.db.models import Case, Q, Sum, When
from django.db.models.functions import Coalesce

from apps.common import db_lookups  # noqa: F401  (đăng ký lookup `__ilike_contains`)
from apps.inventory.models import (
    DocStatus,
    DocType,
    MovementType,
    PendingScanKind,
    PendingScanStatus,
    PendingShipmentScan,
    ReturnEntry,
    ReturnNoteLine,
    ReturnStatus,
    ReturnType,
    StockDocument,
    StockMovement,
)

logger = logging.getLogger(__name__)


_UNMATCHED = [PendingScanStatus.PENDING, PendingScanStatus.FAILED]
# Hiển thị trên trang: gồm cả mã đã đánh dấu "không đúng" (để còn thấy + xoá được)
# và mã dừng vì đơn đã hủy (kho phải thấy để đối chiếu, không im lặng nuốt mã).
_LIST_STATUSES = [
    PendingScanStatus.PENDING,
    PendingScanStatus.FAILED,
    PendingScanStatus.INVALID,
    PendingScanStatus.CANCELLED,
]
_STATUS_LABELS = dict(PendingScanStatus.choices)

# Ngân sách thời gian cho 1 vòng cron. Task có soft_time_limit=100s; mỗi mã DISPATCH
# phải hỏi KiotViet (HTTP) nên vòng lặp KHÔNG được chạy tới hết 200 mã một cách mù quáng.
# Ta chốt trước mỗi mã: hết ngân sách → dừng, để vòng sau (60s) xử lý tiếp.
# Tệ nhất: ngân sách (55s) + 1 mã cuối vừa kịp lọt (token refresh 3s + invoice 3s ≈ 6s;
# SCAN_VERIFY_TIMEOUT_SECONDS áp cho cả hai) ≈ 61s < 100s soft_time_limit.
_CYCLE_TIME_BUDGET_SECONDS = 55.0
# Chặn cứng số mã DISPATCH mỗi vòng (phòng khi verifier trả lời tức thì mà lô quá lớn).
_DISPATCH_PER_CYCLE = 40
# Backoff cho mã ĐÃ thất bại xác minh (verify_unavailable → giữ pending + có `error`).
# Không có backoff, các mã "độc" (payload KiotViet không đọc được trạng thái → hỏng vĩnh
# viễn) luôn đứng đầu hàng theo created_at và ăn sạch _DISPATCH_PER_CYCLE mỗi vòng → mã
# MỚI quét không bao giờ tới lượt (head-of-line blocking).
# Chỉ áp cho mã có `error` — mã chưa từng thử (error rỗng) và mã đang chờ đơn về
# (không set error) vẫn được xử lý mỗi vòng như cũ.
_VERIFY_RETRY_BACKOFF_SECONDS = 300

# Lý do BỎ QUA việc sinh phiếu hàng gửi bù — trả thẳng cho FE để nói rõ với người dùng
# thay vì im lặng không tạo gì.
SKIP_NOT_DISPATCHED = "order_was_not_dispatched"
SKIP_ALREADY_CREATED = "already_created"
SKIP_INVALID_LINES = "invalid_lines"

# Thông điệp DUY NHẤT khi một lần khớp theo MÃ HOÁ ĐƠN không tìm được kho xuất của
# đơn — cron (`_process_one`) và khớp tay (`manual_match`) đều phải dùng đúng câu
# này (qua `_resolve_dispatch_warehouse_id`), không được diễn đạt lại hay để rơi về
# `scan.warehouse_id`. FE (`INVOICE_MATCH_MISSING_WAREHOUSE_ERROR` trong
# scan-dispatch-groups.ts) cũng in nguyên văn câu này cho nhánh invoice thiếu kho.
MISSING_FULFILLMENT_WAREHOUSE_ERROR = (
    "Đơn thiếu kho xuất — bổ sung kho trên đơn rồi quét lại."
)

# Bounds cho `list_unmatched` (audit followups 2026-07 mục 3.2): thiếu CẢ HAI
# date_from/date_to → mặc định 30 ngày gần nhất (BE-side, tránh quét toàn bộ lịch
# sử nếu caller quên truyền); FE hiện tại luôn gửi kèm khoảng ngày nên hành vi thấy
# được không đổi. Số dòng trả về bị chặn cứng — chạm giới hạn thì log cảnh báo
# thay vì âm thầm cắt (nghi ngờ tồn đọng bất thường cần người xử lý).
_LIST_UNMATCHED_DEFAULT_RANGE_DAYS = 30
_LIST_UNMATCHED_MAX_ROWS = 1000


def live_return_scan(code: str) -> dict | None:
    """Lần bắn NHẬP HOÀN còn sống của mã vận đơn, hoặc None nếu mã còn tự do.

    Đây là định nghĩa DUY NHẤT của "mã này đã bắn rồi" — mọi cửa chặn trùng phải
    hỏi hàm này để ba đường (tra mã, ghi mã chờ khớp, tạo nhóm đơn) không lệch luật.

    Phải kiểm CẢ HAI vế, kiểm một vế là thủng một nửa:
    - ``ReturnEntry`` chưa huỷ — đơn tìm thấy ngay lúc bắn đi thẳng ra nhóm đơn
      hoàn, KHÔNG sinh dòng scan nào.
    - ``PendingShipmentScan`` kind=return chưa thu hồi — đơn chưa sync về thì mã
      mới chỉ nằm ở bảng chờ.

    Nháp RỖNG không tính là còn sống: nó không mang bút toán nào và không còn cửa
    nào ghi sổ; coi là "đã bắn" thì mã bị khoá chết, hàng hoàn không bao giờ được
    cộng tồn. Cùng luật với ``create_entry`` / ``_complete_return`` (audit 14/07
    vòng 3) — ba chỗ lệch nhau là tái hiện đúng con bug đó.
    """
    code = (code or "").strip()
    if not code:
        return None
    entry = (
        ReturnEntry.objects.filter(shipping_code=code)
        .exclude(status=ReturnStatus.CANCELLED)
        .exclude(Q(status=ReturnStatus.DRAFT) & Q(lines__isnull=True))
        .select_related("return_note")
        .order_by("-id")
        .first()
    )
    if entry is not None:
        return {"note_code": entry.return_note.code, "scanned_at": entry.created_at}
    scan = (
        PendingShipmentScan.objects.filter(
            code=code, kind=PendingScanKind.RETURN, voided_at__isnull=True
        )
        .order_by("-id")
        .first()
    )
    if scan is not None:
        # Mã mới bắn, chưa ra phiếu → note_code rỗng, FE tự diễn đạt câu chữ.
        return {"note_code": scan.result_ref or "", "scanned_at": scan.created_at}
    return None


def void_return_scans(*, code: str, user_id: int | None = None) -> int:
    """Thu hồi mọi dòng quét NHẬP HOÀN còn sống của mã. Trả về số dòng đã thu hồi.

    Gọi khi lần nhập hoàn của mã bị huỷ: dòng quét phải biến mất khỏi màn hình
    (kho không còn thấy một lần nhập đã bị đảo tồn) và nhả mã ra cho lần bắn sau.
    """
    from django.utils import timezone

    code = (code or "").strip()
    if not code:
        return 0
    return PendingShipmentScan.objects.filter(
        code=code, kind=PendingScanKind.RETURN, voided_at__isnull=True
    ).update(voided_at=timezone.now(), voided_by_id=user_id)


class ScanOrderCancelled(Exception):
    """Đơn đã hủy → KHÔNG xuất kho, đánh dấu scan là cancelled (không tự khớp lại)."""


class ScanVerifyUnavailable(Exception):
    """Chưa hỏi được KiotViet → lỗi TẠM: giữ scan pending để cron thử lại vòng sau."""


class PendingScanService:
    @staticmethod
    def list_unmatched(
        *,
        kind: str,
        date_from="",
        date_to="",
        # Rỗng/None = mọi kho; nhiều id = lọc theo tập kho đã chọn.
        warehouse_ids=None,
        match_filter="unmatched",
        with_return_note=False,
        order="",
        product="",
        product_ids=None,
        carrier="",
    ) -> list[dict]:
        """Mã vận đơn đã quét, theo kind + bộ lọc trạng thái khớp.

        ``match_filter``:
        - ``"unmatched"`` (mặc định): pending/failed/invalid — mã CHƯA khớp.
        - ``"matched"``: chỉ mã ĐÃ khớp.
        - ``"all"``: cả hai.

        Dòng đã khớp kèm thời điểm khớp (``matched_at`` = updated_at của bản ghi
        lúc chuyển sang matched).

        ``with_return_note`` (chỉ dùng cho tab Nhập hoàn): gắn thêm ``return_id``
        + ``note`` của NHÓM ĐƠN HOÀN (ReturnEntry) tương ứng (khớp theo mã vận
        đơn) để sửa ghi chú ngay trên dòng mã đã khớp. Dòng chưa khớp / không có
        nhóm đơn → return_id None, note "".

        ``order`` / ``product``: dùng chung ô tìm kiếm với danh sách lịch sử để
        khối "mã chờ khớp" KHÔNG bỏ qua bộ lọc (nếu bỏ qua thì gõ 1 mã vẫn hiện
        toàn bộ mã đã quét trong kỳ). ``order`` lọc theo chính ``code`` của mã
        quét hoặc mã đơn đã khớp (order/invoice/shipping). ``product`` → ẩn hẳn
        khối vì mã chờ khớp chưa có thông tin sản phẩm để đối chiếu.

        ``date_from``/``date_to`` rỗng CẢ HAI → mặc định
        ``_LIST_UNMATCHED_DEFAULT_RANGE_DAYS`` ngày gần nhất (BE-side); chỉ thiếu
        MỘT trong hai thì giữ nguyên (khoảng nửa-mở như trước, hành vi cũ). Kết
        quả bị chặn cứng ở ``_LIST_UNMATCHED_MAX_ROWS`` dòng.
        """
        from django.utils import timezone

        product = (product or "").strip()
        if product:
            return []
        # Cùng luật với ô tìm SP dạng text: lọc theo SKU (MultiSelect product_ids) hoặc
        # ĐVVC thì ẨN khối chờ khớp — mã quét chưa gắn đơn không quy được về SKU/ĐVVC.
        # Thiếu 2 tham số này thì lọc 1 SKU vẫn hiện toàn bộ mã đã quét, và export cũng
        # in đủ (mất parity — audit 2026-07-18, cùng lớp flow-history-pending-search).
        if product_ids or (carrier or "").strip():
            return []
        if not date_from and not date_to:
            today = timezone.localdate()
            date_from = (
                today - timedelta(days=_LIST_UNMATCHED_DEFAULT_RANGE_DAYS - 1)
            ).isoformat()
            date_to = today.isoformat()
        if match_filter == "matched":
            statuses = [PendingScanStatus.MATCHED]
        elif match_filter == "all":
            statuses = [*_LIST_STATUSES, PendingScanStatus.MATCHED]
        else:
            statuses = list(_LIST_STATUSES)
        qs = (
            # Dòng đã THU HỒI (lần nhập hoàn tương ứng bị huỷ) biến mất hẳn khỏi
            # màn hình — người dùng không phải nhìn một lần nhập đã bị đảo tồn.
            PendingShipmentScan.objects.filter(kind=kind, status__in=statuses)
            .filter(voided_at__isnull=True)
            .select_related("warehouse", "matched_order", "scanned_by")
            .order_by("-created_at", "-id")
        )
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        if warehouse_ids:
            qs = qs.filter(warehouse_id__in=warehouse_ids)
        order = (order or "").strip()
        if order:
            # Ba nhánh `matched_order__*` dùng `__ilike_contains` để planner còn
            # dùng được index GIN trigram trên `orders` — `icontains` sinh
            # `UPPER(col) LIKE` và vô hiệu hoá chúng (apps/common/db_lookups.py,
            # bản vá 28/07 mới áp cho dispatch_history_service).
            # `code` là cột của chính bảng mã quét: bảng nhỏ, không có index
            # trigram, nên giữ `icontains` cho khớp với ghi chú ở
            # dispatch_history_service — đổi cũng không nhanh hơn.
            qs = qs.filter(
                Q(code__icontains=order)
                | Q(matched_order__order_code__ilike_contains=order)
                | Q(matched_order__invoice_code__ilike_contains=order)
                | Q(matched_order__shipping_code__ilike_contains=order)
            )
        # Lấy dư 1 dòng để biết có bị cắt hay không mà không cần thêm 1 query COUNT.
        scans = list(qs[: _LIST_UNMATCHED_MAX_ROWS + 1])
        if len(scans) > _LIST_UNMATCHED_MAX_ROWS:
            scans = scans[:_LIST_UNMATCHED_MAX_ROWS]
            logger.warning(
                "list_unmatched(kind=%s) chạm giới hạn %s dòng "
                "(date_from=%s date_to=%s warehouse_ids=%s match_filter=%s)",
                kind, _LIST_UNMATCHED_MAX_ROWS, date_from, date_to,
                warehouse_ids, match_filter,
            )
        unmatchable_ids = PendingScanService._unmatchable_scan_ids(scans, kind=kind)
        items = [
            {
                "id": s.id,
                "code": s.code,
                "warehouse_name": s.warehouse.name,
                "status": s.status,
                "status_label": _STATUS_LABELS.get(s.status, s.status),
                "performed_by": (
                    (s.scanned_by.display_name or s.scanned_by.username)
                    if s.scanned_by_id
                    else ""
                ),
                "error": s.error or "",
                "created_at": s.created_at,
                # Thời điểm khớp: bản ghi matched không bị đụng sau khi khớp nên
                # updated_at ≈ đúng lúc khớp. Null với dòng chưa khớp.
                "matched_at": (
                    s.updated_at if s.status == PendingScanStatus.MATCHED else None
                ),
                "matched_order_id": s.matched_order_id,
                "matched_order_code": (
                    s.matched_order.order_code if s.matched_order_id else ""
                ),
                "return_id": None,
                # Ghi chú của dòng chờ khớp; với dòng đã khớp (tab Nhập hoàn)
                # _attach_return_notes đè bằng ghi chú của phiếu trả tương ứng.
                "note": s.note or "",
                "needs_replacement_issue": (
                    PendingScanService.scan_needs_replacement_issue(s)
                ),
                "can_unmatch": s.id in unmatchable_ids,
            }
            for s in scans
        ]
        if with_return_note:
            PendingScanService._attach_return_notes(items)
        PendingScanService._attach_sku_lines(items, kind=kind)
        return items

    @staticmethod
    def scan_notes_by_code(
        *, kind: str, date_from="", date_to="", warehouse_ids=None
    ) -> dict[str, str]:
        """Ghi chú của mã quét theo mã vận đơn (``code``), MỌI trạng thái.

        Dùng cho file export: dòng đơn/phiếu không có trường ghi chú riêng nên
        ghi chú người dùng nhập trên mã quét được gộp thẳng vào đúng dòng đơn
        (join theo mã vận đơn) thay vì tách thành một dòng mã quét riêng — tránh
        lặp mã vận đơn thành 2 dòng. Lấy cả mã FAILED/CANCELLED (không chỉ MATCHED)
        vì đơn có thể đã xuất kho tay dù mã quét báo lỗi ("đã xuất kho trước đó").

        LƯU Ý: code+kind KHÔNG duy nhất — unique constraint chỉ ép giữa các bản ghi
        PENDING, quét lại cùng mã sau khi bản trước FAILED/MATCHED tạo bản ghi mới
        hợp lệ. Vì vậy gộp TẤT CẢ note (cũ → mới, "; " ngăn cách) thay vì để dict
        ghi đè — bản cũ nhất từng thắng làm mất note mới (audit 2026-07-15b).
        """
        # `voided_at__isnull=True`: dòng đã thu hồi cũng phải biến mất khỏi file
        # Excel, không chỉ khỏi màn hình — lệch hai bên là mất parity (bài học
        # cùng lớp với bộ lọc kho của Lịch sử nhập xuất).
        qs = (
            PendingShipmentScan.objects.filter(kind=kind, voided_at__isnull=True)
            .exclude(note="")
        )
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        if warehouse_ids:
            qs = qs.filter(warehouse_id__in=warehouse_ids)
        notes: dict[str, list[str]] = {}
        for code, note in qs.order_by("created_at", "id").values_list("code", "note"):
            bucket = notes.setdefault(code, [])
            if note not in bucket:
                bucket.append(note)
        return {code: "; ".join(parts) for code, parts in notes.items()}

    @staticmethod
    def _attach_sku_lines(items: list[dict], *, kind: str) -> None:
        """Gắn lines/sku_count/qty cho dòng ĐÃ khớp — 1 query gộp, không N+1.

        Dòng chưa khớp chưa có đơn/phiếu nên để rỗng. Shape trùng FlowLineBrief
        của danh sách lịch sử để FE dùng chung component SkuLines.

        Chỉ dòng ``status == MATCHED`` mới được gắn lines: ``matched_order``
        cũng được set cho FAILED/CANCELLED (xem `_dispatch_and_finalize` +
        nhánh return trong `_process_one`) — nếu không lọc theo status thì một
        mã quét lỗi ("đã xuất kho trước đó") vẫn hiện đủ SKU × SL, trông y hệt
        dòng đã xuất kho thành công.
        """
        for it in items:
            it.setdefault("lines", [])
            it.setdefault("sku_count", 0)
            it.setdefault("qty", 0)
        matched = PendingScanStatus.MATCHED
        if kind == "return":
            lines_map = PendingScanService._return_lines_map(
                [
                    it["return_id"]
                    for it in items
                    if it["status"] == matched and it.get("return_id")
                ]
            )
            key = "return_id"
        else:
            lines_map = PendingScanService._issue_lines_map(
                [
                    it["matched_order_id"]
                    for it in items
                    if it["status"] == matched and it.get("matched_order_id")
                ]
            )
            key = "matched_order_id"
        for it in items:
            if it["status"] == matched:
                rows = lines_map.get(it.get(key)) or []
                it["lines"] = rows
                it["sku_count"] = len(rows)
                it["qty"] = sum(r["qty"] for r in rows)
            it.pop("matched_order_id", None)

    @staticmethod
    def _issue_lines_map(order_ids: list[int]) -> dict[int, list[dict]]:
        """SKU × SL net (xuất − hoàn) theo đơn — dùng cho mã quét XUẤT đã khớp."""
        if not order_ids:
            return {}
        rows = (
            StockMovement.objects.filter(
                movement_type__in=[MovementType.SALE, MovementType.SALE_REVERSAL],
                reference_type="order",
                reference_id__in=order_ids,
            )
            .values("reference_id", "product_id", "product__sku")
            .annotate(
                sale_sum=Coalesce(
                    Sum("qty_delta", filter=Q(movement_type=MovementType.SALE)), 0
                ),
                rev_sum=Coalesce(
                    Sum(
                        "qty_delta",
                        filter=Q(movement_type=MovementType.SALE_REVERSAL),
                    ),
                    0,
                ),
            )
        )
        out: dict[int, list[dict]] = {}
        for r in rows:
            out.setdefault(r["reference_id"], []).append(
                {
                    "product_id": r["product_id"],
                    "sku": r["product__sku"],
                    "qty": -r["sale_sum"] - r["rev_sum"],
                    # Xem `dispatch_history_service.enrich_orders`: `qty` là net nên
                    # có thể bằng 0, `gross` là phần đã từng rời kho để FE hiện số dương.
                    "gross": -r["sale_sum"],
                }
            )
        for v in out.values():
            v.sort(key=lambda x: (-x["qty"], x["sku"] or ""))
        return out

    @staticmethod
    def _return_lines_map(entry_ids: list[int]) -> dict[int, list[dict]]:
        """SKU × SL hoàn theo NHÓM ĐƠN HOÀN (ReturnEntry) — mã quét HOÀN đã khớp.

        Dòng hàng treo ở entry (không phải phiếu ngày): phiếu ngày gộp nhiều đơn
        nên gom theo `return_note_id` sẽ trộn SKU của các đơn khác vào cùng dòng.
        """
        if not entry_ids:
            return {}
        rows = (
            ReturnNoteLine.objects.filter(entry_id__in=entry_ids)
            .values("entry_id", "product_id", "product__sku")
            .annotate(qty=Coalesce(Sum("quantity"), 0))
        )
        out: dict[int, list[dict]] = {}
        for r in rows:
            out.setdefault(r["entry_id"], []).append(
                {
                    "product_id": r["product_id"],
                    "sku": r["product__sku"],
                    "qty": r["qty"],
                }
            )
        for v in out.values():
            v.sort(key=lambda x: (-x["qty"], x["sku"] or ""))
        return out

    @staticmethod
    def _attach_return_notes(items: list[dict]) -> None:
        """Gắn return_id + note của NHÓM ĐƠN HOÀN (ReturnEntry, khớp theo mã vận
        đơn) vào các dòng đã khớp — 1 query gộp, tránh N+1. Nhóm đã huỷ bị bỏ qua.

        ``return_id`` giữ nguyên TÊN field (FE đang dùng) nhưng giá trị nay là id
        của ReturnEntry (nhóm đơn), KHÔNG còn là id của phiếu trả.

        Người tiêu thụ thật của ``return_id`` (FE: features/inventory/components/
        ReturnTab.tsx → flow-shared.tsx ``ReturnNoteCell`` → return-history-queries.ts):
        - ``PATCH /api/inventory/return-history/{return_id}/note`` — sửa ghi chú.
        - ``_attach_sku_lines`` → ``_return_lines_map`` — SKU × SL của nhóm đơn.

        Endpoint note đó vì vậy resolve theo ReturnEntry, không theo phiếu ngày.
        """
        codes = [
            it["code"] for it in items if it["status"] == PendingScanStatus.MATCHED
        ]
        if not codes:
            return
        entry_by_code: dict[str, dict] = {}
        for entry in (
            ReturnEntry.objects.filter(shipping_code__in=codes)
            .exclude(status=ReturnStatus.CANCELLED)
            .order_by("id")
            .values("shipping_code", "id", "note")
        ):
            # order_by id tăng dần → bản ghi ghi đè sau cùng là nhóm mới nhất.
            entry_by_code[entry["shipping_code"]] = entry
        for it in items:
            entry = entry_by_code.get(it["code"])
            if entry is not None:
                it["return_id"] = entry["id"]
                it["note"] = entry["note"]

    @staticmethod
    def create_batch(
        *,
        kind: str,
        warehouse_id: int,
        codes: list[str],
        return_type: str = "",
        user_id: int | None = None,
        scan_batch_id: str | None = None,
    ) -> list[dict]:
        """Ghi từng mã vào bảng chờ; mã trùng (đang pending cùng kind) không chặn cả lô."""
        results: list[dict] = []
        for raw in codes:
            code = (raw or "").strip()
            if not code:
                results.append({"code": raw, "ok": False, "reason": "empty"})
                continue
            # Nhập hoàn: 1 mã = 1 lần bắn sống. Unique index chỉ phủ các dòng còn
            # PENDING nên tự nó không chặn được ca "bắn lại mã đã khớp" — đây mới
            # là cửa chặn của luồng đó. Không áp cho kind=dispatch: cùng một mã vận
            # đơn dĩ nhiên vừa đi vừa về.
            if kind == PendingScanKind.RETURN and live_return_scan(code) is not None:
                results.append({"code": code, "ok": False, "reason": "duplicate"})
                continue
            try:
                with transaction.atomic():
                    PendingShipmentScan.objects.create(
                        code=code,
                        kind=kind,
                        warehouse_id=warehouse_id,
                        return_type=return_type if kind == PendingScanKind.RETURN else "",
                        scan_batch_id=scan_batch_id if kind == PendingScanKind.RETURN else None,
                        scanned_by_id=user_id,
                    )
                results.append({"code": code, "ok": True})
            except IntegrityError as exc:
                if "uniq_pending_scan_code_kind" not in str(exc):
                    raise  # không phải trùng mã (vd FK sai) — lỗi thật, không nuốt
                results.append({"code": code, "ok": False, "reason": "duplicate"})
        return results

    @staticmethod
    def match_pending_scans(limit: int = 200) -> dict:
        """Chạy bởi cron 60s: khớp các mã pending với Order đã sync về.

        Nhánh DISPATCH phải hỏi KiotViet (xem `_dispatch_and_finalize`) nên vòng lặp
        chạy theo NGÂN SÁCH THỜI GIAN: hết ngân sách → dừng, mã còn lại để vòng sau.
        Nếu cứ chạy đủ `limit` mã thì task bị soft_time_limit giết giữa chừng.

        Mã vừa thất bại xác minh bị hoãn `_VERIFY_RETRY_BACKOFF_SECONDS` để không chiếm
        chỗ của mã mới quét (xem hằng số).
        """
        from django.utils import timezone

        cutoff = timezone.now() - timedelta(seconds=_VERIFY_RETRY_BACKOFF_SECONDS)
        ids = list(
            # Dòng đã thu hồi không được tự khớp lại: nó đã biến mất khỏi màn hình
            # nên một nhóm đơn hoàn mọc ra từ nó sẽ không ai truy được nguồn gốc.
            PendingShipmentScan.objects.filter(
                status=PendingScanStatus.PENDING, voided_at__isnull=True
            )
            .filter(
                Q(last_attempt_at__isnull=True)
                | Q(error="")
                | Q(last_attempt_at__lt=cutoff)
            )
            .order_by("created_at")
            .values_list("id", flat=True)[:limit]
        )
        summary = {
            "processed": 0, "matched": 0, "failed": 0, "cancelled": 0, "unmatched": 0,
        }
        deadline = time.monotonic() + _CYCLE_TIME_BUDGET_SECONDS
        dispatch_budget = _DISPATCH_PER_CYCLE
        seen = 0
        for scan_id in ids:
            if time.monotonic() >= deadline or dispatch_budget <= 0:
                # `summary["processed"]` đã gồm cả unmatched ở thời điểm này (chỉ bị trừ ra ở
                # cuối hàm) → trừ unmatched lần nữa sẽ ra số sai/âm. Đếm thẳng số mã đã duyệt.
                logger.info(
                    "match_pending_scans dừng sớm (ngân sách): còn %s mã cho vòng sau",
                    len(ids) - seen,
                )
                break
            seen += 1
            outcome, used_remote = PendingScanService._process_one(scan_id)
            if used_remote:
                dispatch_budget -= 1
            if outcome is not None:
                summary["processed"] += 1
                summary[outcome] += 1
        # unmatched không tính processed — đơn chưa về là trạng thái chờ bình thường.
        summary["processed"] -= summary["unmatched"]
        return summary

    # Vé xử lý dispatch sống tối đa 10' — đủ dài cho verify + retry token KiotViet,
    # đủ ngắn để worker chết giữa chừng không khoá mã vĩnh viễn (cron 60s/vòng).
    _CLAIM_TTL_SECONDS = 600

    @staticmethod
    def _claim_is_active(scan: PendingShipmentScan) -> bool:
        from django.utils import timezone

        if scan.claimed_at is None:
            return False
        age = (timezone.now() - scan.claimed_at).total_seconds()
        return age < PendingScanService._CLAIM_TTL_SECONDS

    @staticmethod
    def _process_one(scan_id: int) -> tuple[str | None, bool]:
        """Xử lý 1 mã. Trả về ``(outcome, đã_gọi_KiotViet)``.

        Nhánh DISPATCH: transaction #1 chỉ KHOÁ + dò đơn (DB thuần), rồi ĐÓNG lại trước
        khi gọi KiotViet — HTTP không bao giờ chạy khi đang giữ transaction/lock.
        Kết quả được ghi trong transaction #2 (khoá lại + re-check trạng thái).
        """
        from django.utils import timezone

        from apps.orders.models import Order

        dispatch_ctx: tuple[PendingShipmentScan, object, str, int] | None = None
        cutoff_persist = timezone.now() - timedelta(seconds=_VERIFY_RETRY_BACKOFF_SECONDS)
        with transaction.atomic():
            scan = (
                PendingShipmentScan.objects.select_for_update(skip_locked=True)
                .filter(id=scan_id, status=PendingScanStatus.PENDING)
                .first()
            )
            if scan is None:
                return None, False
            if PendingScanService._claim_is_active(scan):
                # Manual match (hoặc worker khác) đang giữ vé xử lý trong cửa sổ
                # HTTP — dispatch chồng lên là trừ kho cho 2 đơn từ 1 lần quét.
                return None, False
            # Quét XUẤT khớp thêm theo MÃ HOÁ ĐƠN: đơn bán trực tiếp không có mã vận
            # đơn nên mã hoá đơn là thứ duy nhất kho quét được. Quét HOÀN giữ nguyên
            # chỉ theo mã vận đơn — `_complete_return` ghi `scan.code` thẳng vào
            # `ReturnEntry.shipping_code` và 4 chỗ khác đọc lại cột đó để chống ghi
            # nhận trùng; cho mã hoá đơn vào đấy là phá đối chiếu phiếu hoàn.
            code_filter = Q(shipping_code=scan.code)
            if scan.kind == PendingScanKind.DISPATCH:
                code_filter |= Q(invoice_code=scan.code)
            orders = list(
                Order.objects.filter(code_filter, deleted_at__isnull=True)
                # select_related như lookup/dispatch batch: mọi nhánh đọc trạng thái kênh
                # KiotViet của đơn (verify/ghi sổ/log) không tốn thêm 1 SELECT lazy mỗi mã.
                # `fulfillment_warehouse` thêm vào cho nhánh khớp theo mã hoá đơn.
                .select_related("kiotviet_channel", "fulfillment_warehouse")
                .prefetch_related("items")
                # Mã vận đơn THẮNG tuyệt đối: một mã trùng cả `shipping_code` đơn A lẫn
                # `invoice_code` đơn B phải khớp A, để không đơn giao vận nào đổi hành vi.
                .annotate(
                    _by_shipping=Case(
                        When(shipping_code=scan.code, then=1), default=0
                    )
                )
                .order_by("-_by_shipping", "-id")[:2]
            )
            previous_attempt_at = scan.last_attempt_at
            scan.attempts += 1
            scan.last_attempt_at = timezone.now()
            if not orders:
                # Không khớp Order — thử khớp 1 trong 3 mã vận đơn (gửi đi / thu hồi /
                # gửi lại) của một hành trình bảo hành.
                if scan.kind == PendingScanKind.DISPATCH:
                    # Quét xuất: kho đóng gói hàng BH gửi đi → packed.
                    journey = PendingScanService._match_warranty_journey(scan.code)
                    note = "— đã đóng hàng."
                else:
                    # Quét hoàn: kho đã nhận hàng BH về — ghi ngày nhận + trạng thái
                    # nội bộ returned, KHÔNG ghi sổ tồn (hàng BH nhập qua phiếu kho riêng).
                    journey = PendingScanService._match_warranty_journey_return(
                        scan.code,
                        # Ngày kho nhận = ngày QUÉT, không phải ngày cron khớp.
                        received_at=timezone.localtime(scan.created_at).date(),
                    )
                    note = "— đã nhận hàng hoàn."
                if journey is not None:
                    scan.status = PendingScanStatus.MATCHED
                    scan.result_ref = f"warranty-journey:{journey.id}"
                    scan.error = (
                        f"Khớp hành trình bảo hành #{journey.id} (claim #{journey.claim_id}) "
                        + note
                    )
                    scan.save(
                        update_fields=[
                            "attempts", "last_attempt_at", "status",
                            "result_ref", "error", "updated_at",
                        ]
                    )
                    return "matched", False
                # Chưa có đơn để khớp → không đổi trạng thái gì. Trước đây vẫn UPDATE
                # attempts/last_attempt_at cho MỌI mã chờ MỖI vòng 60s (mã chờ có
                # `error=""` nên không bị backoff chặn) — 106k UPDATE/20h trên
                # `pending_shipment_scans` prod (audit 2026-09-23 T1). Chỉ ghi dấu
                # lần thử khi mốc cũ chưa có hoặc đã quá backoff: UI/đối soát vẫn thấy
                # mã "đang được thử" (trễ ≤ backoff), còn số UPDATE giảm ~5×.
                if previous_attempt_at is None or previous_attempt_at < cutoff_persist:
                    scan.save(update_fields=["attempts", "last_attempt_at", "updated_at"])
                return "unmatched", False
            order = orders[0]
            # `_by_shipping` đến từ annotate ở query trên — dùng lại đúng MỘT nguồn sự
            # thật cho "khớp theo mã vận đơn hay mã hoá đơn" thay vì tính lại bằng Python
            # (`shipping_code == scan.code`), để luật ở SQL và luật ở đây không trôi lệch
            # nhau nếu sau này có ai sửa 1 bên mà quên bên kia.
            matched_by_shipping = bool(order._by_shipping)
            if len(orders) > 1 and orders[1]._by_shipping != order._by_shipping:
                # Mã trùng giữa 2 ĐƠN THEO 2 LOẠI MÃ khác nhau (1 khớp mã vận đơn, 1 khớp
                # mã hoá đơn) — đơn thắng vì LUẬT ƯU TIÊN mã vận đơn, KHÔNG PHẢI vì mới
                # hơn. Nói "lấy đơn mới nhất" ở đây là sai và đánh lạc hướng kho khi họ
                # đọc `scan.error` để tra soát lệch tồn — đơn thắng có thể CŨ hơn (xem
                # test_shipping_code_wins_over_invoice_code, nơi order_a cũ hơn order_b).
                multi_note = (
                    "Mã trùng ở 2 đơn (1 khớp mã vận đơn, 1 khớp mã hoá đơn) — ưu tiên "
                    "đơn khớp mã vận đơn. "
                )
            elif len(orders) > 1:
                # Trùng cùng LOẠI mã (2 đơn cùng shipping_code, hoặc 2 đơn cùng
                # invoice_code) — không có luật ưu tiên nào khác ngoài lấy bản ghi mới.
                multi_note = "Nhiều đơn cùng mã — lấy đơn mới nhất. "
            else:
                multi_note = ""
            # Kho ghi sổ tách theo ĐƯỜNG KHỚP: mã vận đơn → kho của màn quét (hành vi
            # cũ, không đổi); mã hoá đơn → kho xuất của ĐƠN (quyết định nghiệp vụ
            # 29/08/2026 — kho do sale chọn lúc tạo đơn là chủ đích, người quét có thể
            # đang để kho mặc định).
            # Query trên chỉ trả đơn khớp 1 trong 2 mã → không phải mã vận đơn tức là
            # mã hoá đơn.
            dispatch_warehouse_id = PendingScanService._resolve_dispatch_warehouse_id(
                scan=scan, order=order, matched_by_invoice=not matched_by_shipping
            )
            if scan.kind == PendingScanKind.DISPATCH and not dispatch_warehouse_id:
                # Rơi về kho màn quét là trừ nhầm kho — sai số im lặng. FAILED thì kho
                # nhìn thấy và sửa được.
                scan.status = PendingScanStatus.FAILED
                scan.matched_order = order
                scan.error = (
                    multi_note + MISSING_FULFILLMENT_WAREHOUSE_ERROR
                ).strip()
                scan.save()
                return "failed", False
            if scan.kind == PendingScanKind.DISPATCH:
                # Xuất kho phải xác minh với KiotViet (HTTP) → làm NGOÀI transaction này.
                # Không lưu attempts ở đây: bước ghi kết quả sẽ cộng (giữ hành vi cũ —
                # task bị soft-timeout giữa chừng thì scan y nguyên, không tốn lượt thử).
                # Claim TRƯỚC khi nhả lock: manual match chen vào cửa sổ HTTP sẽ thấy
                # vé còn hạn và từ chối (xem model.claimed_at).
                PendingShipmentScan.objects.filter(id=scan.id).update(
                    claimed_at=timezone.now()
                )
                dispatch_ctx = (scan, order, multi_note, dispatch_warehouse_id)
            else:
                try:
                    result_ref, note = PendingScanService._complete_return(scan, order)
                except SoftTimeLimitExceeded:
                    raise  # timeout của task, không phải lỗi nghiệp vụ của scan này
                except Exception as exc:  # noqa: BLE001 — lỗi nghiệp vụ → failed, chờ xử lý tay
                    logger.warning(
                        "pending scan %s failed matching order %s: %s", scan.code, order.id, exc
                    )
                    scan.status = PendingScanStatus.FAILED
                    scan.matched_order = order
                    scan.error = (multi_note + str(exc)).strip()
                    scan.save()
                    return "failed", False
                scan.status = PendingScanStatus.MATCHED
                scan.matched_order = order
                scan.result_ref = result_ref
                scan.error = (multi_note + note).strip()
                scan.save()
                return "matched", False

        # ── NGOÀI transaction: hỏi KiotViet + ghi sổ, rồi khoá lại scan để ghi kết quả ──
        scan, order, multi_note, dispatch_warehouse_id = dispatch_ctx
        result = PendingScanService._dispatch_and_finalize(
            scan=scan,
            order=order,
            actor=None,
            multi_note=multi_note,
            allowed_statuses=[PendingScanStatus.PENDING],
            warehouse_id=dispatch_warehouse_id,
        )
        return result["outcome"], True

    @staticmethod
    def _resolve_dispatch_warehouse_id(
        *, scan: PendingShipmentScan, order, matched_by_invoice: bool
    ) -> int | None:
        """MỘT nguồn sự thật cho "kho nào bị trừ tồn" của 1 lần khớp XUẤT.

        - Khớp ĐÚNG MÃ HOÁ ĐƠN (``matched_by_invoice=True``: ``scan.code`` bằng
          ``order.invoice_code``) → kho xuất CỦA ĐƠN (``order.fulfillment_warehouse_id``)
          — quyết định nghiệp vụ 29/08/2026 cho đơn bán trực tiếp: kho do sale chọn
          lúc tạo đơn là chủ đích, người đang thao tác (kho quét hoặc người khớp
          tay) có thể để kho mặc định/kho khác trên màn hình của họ.
        - Mọi đường khác → kho của MÀN QUÉT (``scan.warehouse_id``) — hành vi cũ
          của đơn giao vận, không đổi. Gồm khớp theo mã vận đơn VÀ khớp tay theo
          một mã KHÔNG bằng mã nào của đơn: đơn sàn giao hoả tốc (Shopee → Grab/
          Ahamove) in MÃ ĐƠN SÀN lên tem, kho quét mã đó, còn đơn mang
          ``shipping_code`` là mã Grab và ``invoice_code`` là ``HDSPE_<mã đơn sàn>``.
          Đó vẫn là gói hàng giao vận đang nằm ở kho quét — không phải đơn bán
          trực tiếp, và đơn sàn thường KHÔNG có ``fulfillment_warehouse``. Coi
          "không phải mã vận đơn" là "mã hoá đơn" khiến khớp tay của cả lớp đơn
          này FAILED "Đơn thiếu kho xuất" (prod 01/09/2026, scan #1508/#1527/#1532).

        Trả ``None`` khi không resolve được (khớp mã hoá đơn nhưng đơn thiếu
        ``fulfillment_warehouse``) — nơi gọi PHẢI fail loudly bằng
        ``MISSING_FULFILLMENT_WAREHOUSE_ERROR``, TUYỆT ĐỐI không tự rơi về
        ``scan.warehouse_id``. Cả `_process_one` (cron) lẫn `manual_match` (khớp
        tay) phải gọi qua đây thay vì tự tính lại, để hai nơi không lệch luật.
        """
        return (
            order.fulfillment_warehouse_id if matched_by_invoice else scan.warehouse_id
        )

    @staticmethod
    def _dispatch_and_finalize(
        *,
        scan: PendingShipmentScan,
        order,
        actor,
        multi_note: str,
        allowed_statuses: list[str],
        warehouse_id: int,
    ) -> dict:
        """Xác minh + xuất kho (NGOÀI transaction), rồi ghi kết quả trong 1 transaction ngắn.

        Bắt buộc gọi khi KHÔNG ở trong transaction: `_complete_dispatch` đi qua
        `dispatch_orders_batch` → `InvoiceCancellationVerifier` (HTTP tới KiotViet, timeout
        tới 3s, chưa kể refresh token). Giữ transaction/`select_for_update` mở suốt thời
        gian chờ mạng sẽ ăn connection + khoá hàng, và với 200 mã/vòng thì task chết trước
        khi xong. Đổi lại: lock được nhả trong lúc chờ HTTP → phải KHOÁ LẠI và re-check
        trạng thái trước khi ghi (bản ghi có thể đã bị khớp tay/xoá trong lúc đó).
        """
        from django.utils import timezone

        result_ref = ""
        warnings: list[str] = []
        try:
            result_ref, note, warnings = PendingScanService._complete_dispatch(
                scan, order, actor=actor, warehouse_id=warehouse_id
            )
            new_status = PendingScanStatus.MATCHED
            message = (multi_note + note).strip()
            outcome = "matched"
            ok = True
        except SoftTimeLimitExceeded:
            raise  # timeout của task, không phải lỗi nghiệp vụ của scan này
        except ScanVerifyUnavailable as exc:
            # Lỗi TẠM (KiotViet timeout/5xx): giữ PENDING để vòng cron sau thử lại.
            # Đánh FAILED ở đây = mã kẹt vĩnh viễn (cron chỉ quét PENDING) → phải khớp tay.
            logger.warning("pending scan %s verify unavailable: %s", scan.code, exc)
            new_status = PendingScanStatus.PENDING
            message = (multi_note + str(exc)).strip()
            outcome = "unmatched"
            ok = False
        except ScanOrderCancelled as exc:
            # Đơn về ở trạng thái hủy → TUYỆT ĐỐI không xuất kho; giữ mã cho người xử lý.
            logger.warning("pending scan %s: order %s cancelled — no dispatch", scan.code, order.id)
            new_status = PendingScanStatus.CANCELLED
            message = (multi_note + str(exc)).strip()
            # Tách khỏi "failed": đơn hủy là kết quả nghiệp vụ HỢP LỆ, không phải lỗi hệ
            # thống — gộp chung thì metric cron không phân biệt được hai chuyện.
            outcome = "cancelled"
            ok = False
        except Exception as exc:  # noqa: BLE001 — lỗi nghiệp vụ → failed, chờ xử lý tay
            logger.warning(
                "pending scan %s failed matching order %s: %s", scan.code, order.id, exc
            )
            new_status = PendingScanStatus.FAILED
            message = (multi_note + str(exc)).strip()
            outcome = "failed"
            ok = False

        with transaction.atomic():
            # KHÔNG skip_locked ở đây: nếu chỉ vì row đang bị khoá tạm mà bỏ qua thì ta
            # mất kết quả của một lần xuất kho ĐÃ ghi sổ (và log ra thông điệp sai "đã đổi
            # trạng thái"). Chờ lock là an toàn — block này thuần DB, không có HTTP.
            locked = (
                PendingShipmentScan.objects.select_for_update()
                .filter(id=scan.id, status__in=allowed_statuses)
                .first()
            )
            if locked is None:
                # Ai đó đã xử lý/xoá mã này trong lúc ta chờ KiotViet — không ghi đè.
                logger.info("pending scan %s đã đổi trạng thái trong lúc xác minh — bỏ qua ghi kết quả", scan.code)
                return {
                    "ok": False, "outcome": None, "error": "stale",
                    "result_ref": "", "warnings": [],
                }
            locked.attempts += 1
            locked.last_attempt_at = timezone.now()
            locked.status = new_status
            locked.error = message
            # Giải phóng vé xử lý — kể cả khi giữ PENDING chờ vòng sau.
            locked.claimed_at = None
            if new_status != PendingScanStatus.PENDING:
                # Giữ nguyên mã "chờ khớp" (chưa gắn đơn) khi còn phải thử lại.
                locked.matched_order = order
                locked.result_ref = result_ref
            locked.save()
        return {
            "ok": ok, "outcome": outcome, "error": message,
            "result_ref": result_ref, "warnings": warnings,
        }

    @staticmethod
    def _match_warranty_journey(code: str):
        """Quét XUẤT khớp 1 trong 3 mã của hành trình bảo hành → packed.

        Lazy import: inventory không phụ thuộc module-level vào warranty (tránh
        vòng phụ thuộc + giữ app inventory độc lập khi warranty đổi/gỡ).
        """
        from apps.warranty.journey_service import WarrantyJourneyService

        return WarrantyJourneyService.mark_packed_by_tracking(code)

    @staticmethod
    def _match_warranty_journey_return(code: str, received_at: date | None = None):
        """Quét HOÀN khớp 1 trong 3 mã hành trình BH → returned + ngày kho nhận
        (lazy import như trên)."""
        from apps.warranty.journey_service import WarrantyJourneyService

        return WarrantyJourneyService.mark_returned_by_tracking(
            code, received_at=received_at
        )

    @staticmethod
    def mark_invalid(scan_id: int) -> bool:
        """Đánh dấu 1 mã chờ khớp là 'không đúng' → cron ngừng thử, giữ bản ghi."""
        scan = (
            PendingShipmentScan.objects.filter(id=scan_id, status__in=_UNMATCHED).first()
        )
        if scan is None:
            return False
        scan.status = PendingScanStatus.INVALID
        scan.save(update_fields=["status", "updated_at"])
        return True

    @staticmethod
    def update_note(*, scan_id: int, note: str) -> PendingShipmentScan | None:
        """Sửa ghi chú của một dòng mã chờ khớp (mọi trạng thái).

        Ghi chú là metadata thuần — không đụng tồn kho/trạng thái. Trả về bản ghi
        đã cập nhật, hoặc None nếu không tồn tại.
        """
        scan = PendingShipmentScan.objects.filter(id=scan_id).first()
        if scan is None:
            return None
        scan.note = note or ""
        scan.save(update_fields=["note", "updated_at"])
        return scan

    @staticmethod
    def delete_scan(scan_id: int) -> bool:
        """Xoá 1 mã CHƯA khớp đơn (pending/failed/invalid); đã khớp thì giữ lại."""
        scan = (
            PendingShipmentScan.objects.filter(id=scan_id)
            .exclude(status=PendingScanStatus.MATCHED)
            .first()
        )
        if scan is None:
            return False
        scan.delete()
        return True

    @staticmethod
    def manual_match(
        *, scan_id: int, order_id: int, actor=None, replacement: dict | None = None
    ) -> dict:
        """Khớp thủ công 1 mã chờ khớp với đơn NGƯỜI DÙNG CHỌN (theo mã hóa đơn).

        Chạy đúng luồng như cron tự khớp (_complete_dispatch/_complete_return),
        chỉ khác là đơn do người dùng chỉ định thay vì dò theo shipping_code. Đây
        là đường XỬ LÝ TAY chuẩn khi cron khớp trượt — nên kind=dispatch phải đi
        qua ĐÚNG luật chọn kho của cron (`_resolve_dispatch_warehouse_id`): mã quét
        ĐÚNG BẰNG mã hoá đơn của đơn → kho CỦA ĐƠN (thiếu kho thì fail loudly thay
        vì âm thầm trừ nhầm); mọi trường hợp khác (mã vận đơn, hoặc mã đơn sàn hoả
        tốc không trùng mã nào của đơn) → kho màn quét như đơn giao vận thường.

        ``replacement`` (chỉ kind=dispatch): dict ``{"lines": [...], "note": str}`` —
        đánh dấu mã này là HÀNG GỬI BÙ của đơn đã xuất, sinh thêm Phiếu Xuất kho.
        ``None`` (mặc định) → hành vi cũ, không tạo gì.
        """
        from django.utils import timezone

        from apps.orders.models import Order

        # CANCELLED cũng khớp tay được: cron auto-pick đơn MỚI NHẤT cùng mã —
        # khi đơn đó là đơn huỷ thì scan bị đánh CANCELLED dù đơn ĐÚNG (đơn sống
        # cũ hơn) vẫn chờ xuất; không cho khớp lại là ngõ cụt, phải xoá + quét
        # lại (mất vết). _complete_dispatch tự verify đơn ĐÍCH với KiotViet nên
        # chọn nhầm đơn huỷ lần nữa vẫn bị chặn như thường.
        _ALLOWED = [
            PendingScanStatus.PENDING,
            PendingScanStatus.FAILED,
            PendingScanStatus.CANCELLED,
        ]
        dispatch_ctx: tuple[PendingShipmentScan, object, int] | None = None
        with transaction.atomic():
            scan = (
                PendingShipmentScan.objects.select_for_update(skip_locked=True)
                .filter(id=scan_id, status__in=_ALLOWED, voided_at__isnull=True)
                .first()
            )
            if scan is None:
                return {"ok": False, "error": "Mã chờ khớp không tồn tại hoặc đã khớp."}
            if PendingScanService._claim_is_active(scan):
                return {
                    "ok": False,
                    "error": "Mã đang được xử lý bởi tiến trình khác — thử lại sau ít phút.",
                }
            order = (
                Order.objects.filter(id=order_id, deleted_at__isnull=True)
                .prefetch_related("items")
                .first()
            )
            if order is None:
                return {"ok": False, "error": "Không tìm thấy đơn."}
            if scan.kind == PendingScanKind.DISPATCH:
                # Người dùng chọn đơn bằng tay nên mã quét có thể là: mã vận đơn
                # (cron chưa kịp xử lý), mã hoá đơn (đơn bán trực tiếp cron khớp
                # trượt), hoặc KHÔNG BẰNG mã nào của đơn (đơn sàn hoả tốc: tem in mã
                # đơn sàn, đơn lưu mã Grab + `HDSPE_<mã đơn sàn>`). Chỉ trường hợp
                # thứ hai là đường mã hoá đơn → kho của đơn; hai trường hợp còn lại
                # là gói hàng giao vận → kho màn quét. So ĐÚNG BẰNG `invoice_code`,
                # không suy "khác mã vận đơn" = "mã hoá đơn" — chính suy luận đó
                # làm khớp tay đơn sàn hoả tốc FAILED "Đơn thiếu kho xuất" (01/09).
                # Mã vận đơn thắng tuyệt đối khi trùng cả hai, cùng luật với cron.
                matched_by_invoice = (
                    order.shipping_code != scan.code and order.invoice_code == scan.code
                )
                dispatch_warehouse_id = PendingScanService._resolve_dispatch_warehouse_id(
                    scan=scan, order=order, matched_by_invoice=matched_by_invoice
                )
                if not dispatch_warehouse_id:
                    # Fail loudly TRƯỚC khi claim/gọi KiotViet — không trừ nhầm kho,
                    # không dispatch nửa vời. Đánh FAILED để kho thấy + xử lý được,
                    # cùng cách cron báo lỗi này.
                    scan.status = PendingScanStatus.FAILED
                    scan.matched_order = order
                    scan.error = MISSING_FULFILLMENT_WAREHOUSE_ERROR
                    scan.save()
                    return {"ok": False, "error": MISSING_FULFILLMENT_WAREHOUSE_ERROR}
                # Như cron: HTTP tới KiotViet không được nằm trong transaction này.
                # Claim trước khi nhả lock — cùng luật với cron (chống dispatch chồng).
                PendingShipmentScan.objects.filter(id=scan.id).update(
                    claimed_at=timezone.now()
                )
                dispatch_ctx = (scan, order, dispatch_warehouse_id)
            else:
                scan.attempts += 1
                scan.last_attempt_at = timezone.now()
                try:
                    result_ref, note = PendingScanService._complete_return(
                        scan, order, actor=actor
                    )
                except SoftTimeLimitExceeded:
                    raise
                except Exception as exc:  # noqa: BLE001 — lỗi nghiệp vụ → failed, chờ xử lý tay
                    logger.warning(
                        "manual match scan %s → order %s failed: %s", scan.code, order.id, exc
                    )
                    scan.status = PendingScanStatus.FAILED
                    scan.matched_order = order
                    scan.error = str(exc)
                    scan.save()
                    return {"ok": False, "error": str(exc)}
                scan.status = PendingScanStatus.MATCHED
                scan.matched_order = order
                scan.result_ref = result_ref
                scan.error = note or ""
                scan.save()
                return {
                    "ok": True,
                    "result_ref": result_ref,
                    "note": note or "",
                    "order_id": order.id,
                    "stock_doc_code": "",
                    "replacement_skipped": "",
                }

        scan, order, dispatch_warehouse_id = dispatch_ctx
        result = PendingScanService._dispatch_and_finalize(
            scan=scan,
            order=order,
            actor=actor,
            multi_note="",
            allowed_statuses=_ALLOWED,
            warehouse_id=dispatch_warehouse_id,
        )
        if result["outcome"] is None:
            return {"ok": False, "error": "Mã chờ khớp vừa được xử lý bởi tiến trình khác."}
        if not result["ok"]:
            return {"ok": False, "error": result["error"]}
        doc_code, skipped = PendingScanService._create_replacement_issue(
            scan=scan,
            order=order,
            actor=actor,
            replacement=replacement,
            warnings=result["warnings"],
        )
        return {
            "ok": True,
            "result_ref": result["result_ref"],
            "note": result["error"],
            "order_id": order.id,
            "stock_doc_code": doc_code,
            "replacement_skipped": skipped,
        }

    @staticmethod
    def _unmatchable_scan_ids(scans: list[PendingShipmentScan], *, kind: str) -> set[int]:
        """Id các mã XUẤT đã khớp còn GỠ được (cờ `can_unmatch` cho nút FE).

        Gỡ được = matched + gắn đơn + không còn phiếu bù hiệu lực. Phiếu bù đã
        huỷ / bị từ chối duyệt (về nháp) không chặn. 1 query gộp cho cả trang,
        không N+1; kind=return trả rỗng (gỡ bên hoàn đi đường huỷ nhóm đơn hoàn).
        """
        if kind != PendingScanKind.DISPATCH:
            return set()
        out: set[int] = set()
        doc_id_by_scan: dict[int, int] = {}
        for s in scans:
            if s.status != PendingScanStatus.MATCHED or not s.matched_order_id:
                continue
            ref = s.result_ref or ""
            if not ref:
                out.add(s.id)
            elif ref.startswith("stock-doc:"):
                try:
                    doc_id_by_scan[s.id] = int(ref.split(":", 1)[1])
                except ValueError:
                    pass  # ref hỏng → fail-closed, không cho gỡ
        if doc_id_by_scan:
            inactive = set(
                StockDocument.objects.filter(
                    id__in=set(doc_id_by_scan.values()),
                    status__in=[DocStatus.CANCELLED, DocStatus.DRAFT],
                ).values_list("id", flat=True)
            )
            out.update(
                sid for sid, did in doc_id_by_scan.items() if did in inactive
            )
        return out

    @staticmethod
    def _replacement_doc_block_error(scan: PendingShipmentScan) -> str:
        """Thông điệp chặn gỡ khớp nếu mã đã sinh phiếu bù còn hiệu lực; rỗng = không chặn.

        Cùng luật với `_unmatchable_scan_ids` — hai bên lệch nhau là nút hiện mà
        bấm bị chặn (hoặc ngược lại).
        """
        ref = scan.result_ref or ""
        if not ref:
            return ""
        if not ref.startswith("stock-doc:"):
            return "Kết quả khớp của mã này không phải xuất kho theo đơn — không gỡ ở đây được."
        try:
            doc_id = int(ref.split(":", 1)[1])
        except ValueError:
            return "Tham chiếu phiếu bù của mã không đọc được — cần kiểm tra tay."
        doc = StockDocument.objects.filter(id=doc_id).first()
        if doc is None or doc.status in (DocStatus.CANCELLED, DocStatus.DRAFT):
            return ""
        return (
            f"Mã đã sinh phiếu xuất bù {doc.code} (đang chờ duyệt hoặc đã ghi sổ) — "
            "huỷ/từ chối phiếu đó trước rồi mới gỡ khớp."
        )

    @staticmethod
    def unmatch_dispatch(*, scan_id: int, actor=None) -> dict:
        """Gỡ khớp 1 mã XUẤT đã khớp nhầm đơn — đường lùi ngay tại bảng chờ.

        - Lần khớp ĐÃ ghi SALE (không có warning `already_dispatched` trong error):
          un-dispatch đơn qua đúng đường `update_order_inline` (hoàn tồn outstanding
          + sự kiện + đổi trạng thái giao vận về "Kho đang xử lý") — chỉ khi đơn
          VẪN đang "Đã xuất kho"; đơn đã sang trạng thái sau đó (Đã nhận,...) thì
          không tự lùi, trả lỗi để xử lý trên màn đơn hàng.
        - Lần khớp KHÔNG ghi SALE (đơn đã xuất từ trước — quét trùng/hàng gửi bù):
          chỉ tháo scan, tuyệt đối không đụng tồn/trạng thái đơn.
        - Scan về FAILED (cron chỉ quét PENDING nên không tự khớp lại mã vừa gỡ)
          + giữ matched_order làm vết; sau đó khớp tay lại đơn đúng như mã lỗi.

        Toàn bộ trong 1 transaction, thuần DB — un-dispatch không hỏi KiotViet.
        """
        from apps.orders.crm_catalog import DISPATCHED_DELIVERY_CODE
        from apps.orders.application.services.order_service_impl import OrderService

        with transaction.atomic():
            scan = (
                # of=("self",): chỉ khoá dòng scan — FOR UPDATE không áp được lên
                # phía nullable của outer join (matched_order).
                PendingShipmentScan.objects.select_for_update(of=("self",))
                .select_related("matched_order")
                .filter(
                    id=scan_id,
                    kind=PendingScanKind.DISPATCH,
                    status=PendingScanStatus.MATCHED,
                )
                .first()
            )
            if scan is None:
                return {"ok": False, "error": "Mã không tồn tại hoặc chưa khớp đơn."}
            if scan.matched_order_id is None:
                # Khớp hành trình bảo hành (result_ref="warranty-journey:...") —
                # không có bút toán đơn để đảo, xử lý bên module bảo hành.
                return {
                    "ok": False,
                    "error": "Mã không gắn đơn (khớp hành trình bảo hành) — không gỡ ở đây được.",
                }
            blocked = PendingScanService._replacement_doc_block_error(scan)
            if blocked:
                return {"ok": False, "error": blocked}
            order = scan.matched_order
            released = False
            if "already_dispatched" not in (scan.error or ""):
                assessment = getattr(order, "assessment", None)
                current = getattr(assessment, "delivery_business_status_code", "") or ""
                if current != DISPATCHED_DELIVERY_CODE:
                    return {
                        "ok": False,
                        "error": (
                            "Đơn không còn ở trạng thái 'Đã xuất kho' "
                            f"(hiện: {current or 'trống'}) — xử lý trên màn đơn hàng."
                        ),
                    }
                # Đúng đường un-dispatch chuẩn: hoàn tồn outstanding (idempotent,
                # đã trừ credit hoàn) + sự kiện cập nhật cho FE.
                OrderService.update_order_inline(
                    order,
                    {"crm": {"delivery_business_status_code": "warehouse_processing"}},
                    actor,
                )
                released = True
            label = order.invoice_code or order.order_code or f"#{order.id}"
            scan.status = PendingScanStatus.FAILED
            scan.error = (
                f"Đã gỡ khớp khỏi đơn {label}"
                + (" (đã hoàn tồn)" if released else " (không hoàn tồn — đơn xuất từ trước)")
                + " — chọn lại đơn đúng."
            )
            scan.save(update_fields=["status", "error", "updated_at"])
            logger.info(
                "unmatch dispatch scan %s (id=%s) khỏi order %s bởi %s (released=%s)",
                scan.code, scan.id, order.id, getattr(actor, "id", None), released,
            )
        return {"ok": True, "released_stock": released, "order_id": order.id}

    @staticmethod
    def _create_replacement_issue(
        *, scan: PendingShipmentScan, order, actor, replacement: dict | None,
        warnings: list[str],
    ) -> tuple[str, str]:
        """Sinh Phiếu Xuất kho cho HÀNG GỬI BÙ (đổi lỗi 2 chiều). Trả (mã phiếu, lý do bỏ qua).

        Nghiệp vụ: đơn đã xuất kho nay gửi THÊM một cái thay thế bằng mã vận đơn mới.
        Guard `already_dispatched` chặn trừ tồn lần 2 trên cùng đơn (đúng), nên cái
        thay thế phải trừ tồn bằng phiếu xuất kho riêng — xem
        docs/erp-issue-history-include-stock-documents.md.

        Gọi NGOÀI mọi transaction đang mở: `_dispatch_and_finalize` đã đóng transaction
        của nó trước khi trả về (không được giữ transaction qua HTTP tới KiotViet). Ở
        đây khoá lại scan để re-check idempotent trước khi ghi.
        """
        if not replacement:
            return "", ""
        if "already_dispatched" not in warnings:
            # Lần khớp này VỪA ghi SALE cho đơn → tạo thêm phiếu là trừ tồn 2 lần.
            # Fail-safe: thà thiếu phiếu (người dùng thấy cảnh báo) còn hơn sai tồn.
            return "", SKIP_NOT_DISPATCHED
        return PendingScanService._post_replacement_document(
            scan=scan, order=order, actor=actor, replacement=replacement
        )

    @staticmethod
    def scan_needs_replacement_issue(scan: PendingShipmentScan) -> bool:
        """Mã đã khớp vào đơn ĐÃ xuất mà chưa sinh phiếu bù nào.

        Dấu vết của các ca CŨ (trước khi có bước hỏi "hàng gửi bù?"): warning
        `already_dispatched` được `_dispatch_and_finalize` lưu vào `error`. Đây chỉ là
        NGHI NGỜ — có thể là hàng gửi bù thật, cũng có thể chỉ là quét trùng cùng một
        kiện. Máy không phân biệt được nên chỉ đánh dấu, để người quyết định từng ca.
        """
        return bool(
            scan.kind == PendingScanKind.DISPATCH
            and scan.status == PendingScanStatus.MATCHED
            and "already_dispatched" in (scan.error or "")
            and not scan.result_ref
        )

    @staticmethod
    def create_replacement_issue_for_scan(
        *, scan_id: int, replacement: dict, actor=None
    ) -> dict:
        """Tạo phiếu bù cho mã ĐÃ khớp từ trước — xử lý tay các ca lịch sử.

        `manual_match` chỉ nhận mã pending/failed nên không dùng lại được cho mã đã
        khớp. Guard tương đương: chỉ mã thuộc diện nghi ngờ (xem
        `scan_needs_replacement_issue`) mới được tạo, và idempotent qua `result_ref`.
        """
        scan = (
            PendingShipmentScan.objects.select_related("matched_order")
            .filter(id=scan_id)
            .first()
        )
        if scan is None:
            return {"ok": False, "error": "Không tìm thấy mã chờ khớp."}
        if not PendingScanService.scan_needs_replacement_issue(scan):
            return {
                "ok": False,
                "error": (
                    "Mã này không thuộc diện cần phiếu bù: chưa khớp đơn, đơn chưa "
                    "từng xuất kho, hoặc đã sinh phiếu trước đó."
                ),
            }
        if scan.matched_order_id is None:
            return {"ok": False, "error": "Mã chưa gắn đơn."}
        code, skipped = PendingScanService._post_replacement_document(
            scan=scan, order=scan.matched_order, actor=actor, replacement=replacement
        )
        if skipped:
            return {"ok": False, "error": skipped}
        return {"ok": True, "stock_doc_code": code}

    @staticmethod
    def _post_replacement_document(
        *, scan: PendingShipmentScan, order, actor, replacement: dict
    ) -> tuple[str, str]:
        """Dựng + ghi Phiếu Xuất kho hàng gửi bù. Trả (mã phiếu, lý do bỏ qua).

        Dùng chung cho 2 đường vào: khớp tay ngay lúc quét, và xử lý tay ca cũ. Guard
        "đơn có thật sự đã xuất chưa" nằm ở CALLER — mỗi đường có tín hiệu riêng.
        """
        from apps.inventory.application.services.stock_document_service import (
            StockDocumentService,
        )

        lines = [
            {"product_id": int(row["product_id"]), "quantity": int(row["quantity"])}
            for row in (replacement.get("lines") or [])
            if row.get("product_id") and int(row.get("quantity") or 0) > 0
        ]
        if not lines:
            return "", SKIP_INVALID_LINES

        order_label = order.invoice_code or order.order_code or f"#{order.id}"
        parts = [f"Hàng gửi bù đổi 2 chiều · đơn {order_label} · VĐ {scan.code}"]
        user_note = (replacement.get("note") or "").strip()
        if user_note:
            parts.append(user_note)
        user_id = actor.id if actor else scan.scanned_by_id

        with transaction.atomic():
            # result_ref="" trong điều kiện lọc = khoá idempotent: bản ghi đã sinh phiếu
            # thì lần gọi thứ 2 không match, không tạo phiếu trùng.
            locked = (
                PendingShipmentScan.objects.select_for_update()
                .filter(id=scan.id, status=PendingScanStatus.MATCHED, result_ref="")
                .first()
            )
            if locked is None:
                return "", SKIP_ALREADY_CREATED
            doc = StockDocumentService.create_document(
                doc_type=DocType.ISSUE,
                source_warehouse_id=scan.warehouse_id,
                lines=lines,
                note=" · ".join(parts),
                user_id=user_id,
                # Ngày kho QUÉT, không phải ngày duyệt: duyệt muộn vẫn thuộc đúng ngày
                # làm việc. Cùng luật với `_complete_dispatch` / `_complete_return`.
                occurred_at=scan.created_at,
            )
            # Vào thẳng hàng chờ duyệt: giữ nguyên vòng kiểm soát xuất kho (tồn chỉ trừ
            # khi có người duyệt), đồng thời không để lại phiếu nháp mồ côi.
            StockDocumentService.submit_for_approval(
                document_id=doc.id, user_id=user_id
            )
            locked.result_ref = f"stock-doc:{doc.id}"
            locked.save(update_fields=["result_ref", "updated_at"])
        return doc.code, ""

    @staticmethod
    def _complete_dispatch(
        scan: PendingShipmentScan, order, actor=None, *, warehouse_id: int
    ) -> tuple[str, str, list[str]]:
        """Xuất kho cho 1 đơn tại `warehouse_id` do người gọi quyết định. GỌI NGOÀI
        TRANSACTION: bên trong có HTTP tới KiotViet (xác minh đơn chưa hủy) — xem
        `_dispatch_and_finalize`.

        `warehouse_id` KHÔNG mặc định về `scan.warehouse_id`: từ 29/08/2026 mã khớp theo
        hoá đơn ghi sổ ở kho của ĐƠN, còn mã vận đơn giữ kho của màn quét. Để mặc định ở
        đây thì nhánh mới sẽ âm thầm trừ sai kho.

        Trả về ``(result_ref, thông điệp, danh sách warning thô)``. Nhánh hàng gửi bù cần
        LIST gốc để so `already_dispatched`; khớp chuỗi trong thông điệp là mong manh."""
        # Đúng đường xuất kho tay: set dispatched + issue_for_order (idempotent).
        from apps.orders.application.services.order_service_impl import OrderService

        results = OrderService.dispatch_orders_batch(
            order_ids=[order.id],
            warehouse_id=warehouse_id,
            actor=actor or scan.scanned_by,
            # Ngày nghiệp vụ = lúc kho QUÉT mã, không phải lúc cron khớp được đơn:
            # đơn KiotViet về muộn vài ngày thì sản lượng xuất vẫn thuộc ngày kho
            # làm việc. Cùng luật với doc_date ở `_complete_return`.
            occurred_at=scan.created_at,
        )
        result = results[0]
        if not result.get("ok"):
            error = str(result.get("error") or "")
            if error == "order_cancelled":
                raise ScanOrderCancelled("Đơn đã hủy — không xuất kho.")
            if error == "verify_unavailable":
                raise ScanVerifyUnavailable("Chưa xác minh được với KiotViet — sẽ thử lại.")
            raise ValueError(error or "Xuất kho thất bại.")
        warnings = list(result.get("warnings") or [])
        return "", ", ".join(warnings), warnings

    @staticmethod
    def _complete_return(scan: PendingShipmentScan, order, actor=None) -> tuple[str, str]:
        """Tạo + ghi sổ NHÓM ĐƠN HOÀN cho mã quét, trong phiếu ngày của kho.

        Trả về (mã phiếu ngày, cảnh báo). Nhóm đơn không có mã riêng nên
        ``scan.result_ref`` mang mã PHIẾU NGÀY chứa nó.
        """
        from collections import defaultdict

        from django.utils import timezone

        from apps.inventory.application.services.return_entry_service import (
            ReturnEntryService,
        )

        existing = (
            ReturnEntry.objects.filter(shipping_code=scan.code)
            .exclude(status=ReturnStatus.CANCELLED)
            # Nháp RỖNG không tính là "đã ghi nhận": nó không có bút toán nào và không
            # còn cửa nào ghi sổ. Coi nó là đã ghi nhận thì mã bị khoá chết, hàng hoàn
            # không bao giờ được cộng tồn. create_entry sẽ tự huỷ nháp rỗng đó rồi tạo
            # nhóm đơn thật (audit 2026-07-14 vòng 3).
            .exclude(Q(status=ReturnStatus.DRAFT) & Q(lines__isnull=True))
            .select_related("return_note")
            .order_by("-id")
            .first()
        )
        if existing is not None:
            # Mã này đã được ghi nhận (quét tay sau khi đơn sync) — không tạo trùng.
            code = existing.return_note.code
            return code, f"Mã {scan.code} đã có trong phiếu {code} — không tạo thêm."

        merged: dict[int, int] = defaultdict(int)
        for item in order.items.all():
            if item.product_id:
                merged[item.product_id] += item.quantity
        if not merged:
            raise ValueError("Đơn không có dòng sản phẩm để tạo phiếu trả.")
        # Ghi sổ ngay (không dừng ở nháp): khớp mã = hàng đã về kho, tồn phải cộng
        # ngay như luồng quét khớp-ngay (auto_post) và như nhánh xuất giao ở
        # _complete_dispatch. Guard chặn → ValueError → caller đánh dấu scan FAILED
        # và savepoint rollback: không để lại nhóm đơn nháp mồ côi.
        entry = ReturnEntryService.create_and_post_entry(
            warehouse_id=scan.warehouse_id,
            # Ngày chứng từ = ngày mã được QUÉT (hàng về kho), không phải ngày cron
            # chạy: mã quét 23h hôm qua, cron khớp sáng nay vẫn thuộc phiếu hôm qua.
            doc_date=timezone.localtime(scan.created_at).date(),
            # Bút toán kho mang đúng thời điểm quét (cả giờ) — cùng lý do trên,
            # nhưng cho các báo cáo đọc thẳng ledger thay vì đọc phiếu ngày.
            occurred_at=scan.created_at,
            lines=[
                {
                    "product_id": pid,
                    "quantity": qty,
                    "return_type": scan.return_type or ReturnType.DELIVERY_RETURNED,
                }
                for pid, qty in merged.items()
            ],
            source_order_id=order.id,
            shipping_code=scan.code,
            customer_name=order.name or "",
            customer_phone=order.phone or "",
            note=scan.note or "",
            user_id=actor.id if actor else scan.scanned_by_id,
            scan_batch_id=scan.scan_batch_id,
        )
        return entry.return_note.code, ""
