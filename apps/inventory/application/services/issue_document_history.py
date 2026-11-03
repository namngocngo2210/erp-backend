"""Phần báo cáo tab "Xuất" đến từ PHIẾU XUẤT KHO (StockDocument doc_type=issue).

Phiếu xuất kho ghi `StockMovement` `movement_type='issue'`, `reference_type='document'`,
`qty_delta` âm. Huỷ phiếu tạo bút toán đảo CÙNG `movement_type` nên SL xuất qua phiếu
= ``-SUM(qty_delta)`` tự về 0 khi cửa sổ lọc chứa cả hai vế; huỷ khác ngày ghi sổ
thì phải loại thẳng phiếu ``cancelled`` (xem ``base_queryset``). Chỉ tính phiếu
Xuất kho (transfer dùng `transfer_out/in`, điều chỉnh dùng `adjustment` → không lẫn).

`MergedIssueHistory` trộn nguồn "xuất theo đơn" (DispatchHistoryService) với nguồn phiếu
kho ở đây thành 1 danh sách/tổng có breakdown, cho cả view Theo đơn và Theo SKU.
"""
from __future__ import annotations

from django.db.models import Count, Max, Q, QuerySet, Sum
from django.db.models.functions import Coalesce

from apps.inventory.application.services.dispatch_history_service import (
    DispatchHistoryService,
)
from apps.inventory.application.services.flow_note_service import FlowNoteService
from apps.inventory.application.services.movement_window import (
    filter_occurred_between,
)
from apps.inventory.application.services.transfer_document_history import (
    TransferDocumentHistoryService,
)
from apps.inventory.models import (
    DocStatus,
    MovementType,
    StockDocument,
    StockFlowNoteTarget,
    StockMovement,
)

_ISSUE = "issue"
_DOC = "document"


class IssueDocumentHistoryService:
    @staticmethod
    def base_queryset(
        *,
        date_from: str = "",
        date_to: str = "",
        # Rỗng/None = mọi kho; nhiều id = lọc theo tập kho đã chọn.
        warehouse_ids: list[int] | None = None,
        product: str = "",
        product_id: int | None = None,
        product_ids: list[int] | None = None,
        order: str = "",
        order_id: int | None = None,
        carrier: str = "",
    ) -> QuerySet[StockMovement]:
        # Phiếu kho không gắn đơn hàng: lọc theo 1 đơn cụ thể hoặc theo ĐVVC ⇒
        # không có phiếu nào.
        if order_id or carrier:
            return StockMovement.objects.none()
        qs = StockMovement.objects.filter(movement_type=_ISSUE, reference_type=_DOC)
        # Phiếu ĐÃ HUỶ không thuộc về báo cáo nào — loại theo TRẠNG THÁI, không
        # dựa vào "net tự về 0". Bút toán đảo lúc huỷ mang mốc thời điểm bấm huỷ
        # còn bút toán gốc mang mốc nghiệp vụ của phiếu, nên phiếu ghi sổ ngày D
        # rồi huỷ ngày D+n có HAI vế nằm ở HAI cửa sổ lọc khác nhau: lọc ngày D+n
        # chỉ nhặt được vế đảo và trả về SL ÂM (ca CK20260818-0001 trên prod),
        # lọc ngày D thì lại hiện phiếu đã huỷ như phiếu còn hiệu lực.
        # Subquery trên `stock_documents` (bảng nhỏ) nên không phát sinh N+1.
        qs = qs.exclude(
            reference_id__in=StockDocument.objects.filter(
                status=DocStatus.CANCELLED
            ).values("id")
        )
        if product_ids:
            qs = qs.filter(product_id__in=product_ids)
        # Phiếu kho tay luôn có occurred_at == created_at; dùng chung helper để
        # trong codebase chỉ còn MỘT luật về "ngày của bút toán".
        qs = filter_occurred_between(qs, date_from, date_to)
        if warehouse_ids:
            qs = qs.filter(warehouse_id__in=warehouse_ids)
        if product_id:
            qs = qs.filter(product_id=product_id)
        product = (product or "").strip()
        if product:
            qs = qs.filter(
                Q(product__sku__icontains=product) | Q(product__name__icontains=product)
            )
        order = (order or "").strip()
        if order:
            # Phiếu không có mã đơn → ô tìm khớp theo mã phiếu hoặc ghi chú phiếu.
            doc_ids = list(
                StockDocument.objects.filter(
                    Q(code__icontains=order) | Q(note__icontains=order)
                ).values_list("id", flat=True)
            )
            qs = qs.filter(reference_id__in=doc_ids or [-1])
        return qs

    @staticmethod
    def total_issued(**filters) -> int:
        qs = IssueDocumentHistoryService.base_queryset(**filters)
        return -qs.aggregate(s=Coalesce(Sum("qty_delta"), 0))["s"]

    @staticmethod
    def doc_groups(**filters) -> list[dict]:
        """Danh sách phiếu (net ≠ 0) sắp theo thời gian giảm dần.

        Mỗi phần tử: ``{reference_id, quantity, last_at}``. Phiếu đã huỷ (net 0)
        bị loại — không hiện trong báo cáo."""
        grouped = (
            IssueDocumentHistoryService.base_queryset(**filters)
            .values("reference_id")
            .annotate(issued=Coalesce(Sum("qty_delta"), 0), last_at=Max("occurred_at"))
            .order_by("-last_at", "-reference_id")
        )
        rows: list[dict] = []
        for r in grouped:
            qty = -r["issued"]
            if qty == 0:
                continue
            rows.append(
                {
                    "reference_id": r["reference_id"],
                    "quantity": qty,
                    "last_at": r["last_at"],
                }
            )
        return rows

    @staticmethod
    def enrich_docs(groups: list[dict], **filters) -> list[dict]:
        """Gắn mã phiếu/kho/người ghi sổ/dòng SKU cho tập phiếu (1 trang).

        Trả item CÙNG SHAPE dòng đơn (để FE dùng chung bảng), thêm
        ``kind='document'`` + ``doc_code``; các trường mã đơn để None."""
        qs = IssueDocumentHistoryService.base_queryset(**filters)
        ref_ids = [g["reference_id"] for g in groups]
        docs = {
            d["id"]: d
            for d in StockDocument.objects.filter(id__in=ref_ids).values(
                "id", "code", "note", "source_warehouse__name"
            )
        }
        # Người ghi sổ: created_by của movement issue mới nhất của phiếu.
        performer: dict[int, str] = {}
        for rid, disp, uname in (
            StockMovement.objects.filter(
                movement_type=_ISSUE, reference_type=_DOC, reference_id__in=ref_ids
            )
            .order_by("created_at", "id")
            .values_list(
                "reference_id", "created_by__display_name", "created_by__username"
            )
        ):
            performer[rid] = disp or uname or ""
        # Dòng SKU (net theo product) — 1 query gộp, không N+1.
        lines_map: dict[int, list[dict]] = {}
        for r in (
            qs.filter(reference_id__in=ref_ids)
            .values("reference_id", "product_id", "product__sku")
            .annotate(issued=Coalesce(Sum("qty_delta"), 0))
        ):
            qty = -r["issued"]
            if qty == 0:
                continue
            lines_map.setdefault(r["reference_id"], []).append(
                {"product_id": r["product_id"], "sku": r["product__sku"], "qty": qty}
            )
        for v in lines_map.values():
            v.sort(key=lambda x: (-x["qty"], x["sku"] or ""))
        items: list[dict] = []
        for g in groups:
            rid = g["reference_id"]
            d = docs.get(rid, {})
            lines = lines_map.get(rid, [])
            items.append(
                {
                    "order_id": None,
                    "order_code": None,
                    "invoice_code": None,
                    "shipping_code": None,
                    "scan_codes": [],
                    "warehouse_name": d.get("source_warehouse__name"),
                    "performed_by": performer.get(rid, ""),
                    "lines": lines,
                    "sku_count": len(lines),
                    "dispatched": g["quantity"],
                    "reversed": 0,
                    "net": g["quantity"],
                    "last_at": g["last_at"],
                    "kind": "document",
                    "doc_code": d.get("code"),
                    # Ghi chú của phiếu là ghi chú của DÒNG này — sửa trên trang
                    # Lịch sử nhập xuất ghi thẳng vào `StockDocument.note`.
                    "doc_id": rid,
                    "note": d.get("note") or "",
                }
            )
        return items

    @staticmethod
    def skus_map(**filters) -> dict[int, dict]:
        """{product_id: {doc_quantity, doc_count, last_at, sku, name}} cho phiếu kho.

        Chỉ product có net ≠ 0. Dùng để cộng vào view Theo SKU."""
        out: dict[int, dict] = {}
        for r in (
            IssueDocumentHistoryService.base_queryset(**filters)
            .values("product_id", "product__sku", "product__name")
            .annotate(
                issued=Coalesce(Sum("qty_delta"), 0),
                doc_count=Count("reference_id", distinct=True),
                last_at=Max("occurred_at"),
            )
        ):
            qty = -r["issued"]
            if qty == 0:
                continue
            out[r["product_id"]] = {
                "doc_quantity": qty,
                "doc_count": r["doc_count"],
                "last_at": r["last_at"],
                "sku": r["product__sku"],
                "name": r["product__name"],
            }
        return out

    @staticmethod
    def sku_documents(*, product_id: int, **filters) -> list[dict]:
        """Drill-down: các phiếu kho chứa 1 SKU (dòng nhãn phiếu, không có mã đơn)."""
        filters.pop("product_id", None)
        filters.pop("product", None)
        qs = IssueDocumentHistoryService.base_queryset(product_id=product_id, **filters)
        grouped = qs.values("reference_id").annotate(
            issued=Coalesce(Sum("qty_delta"), 0), last_at=Max("occurred_at")
        )
        ref_ids = [r["reference_id"] for r in grouped]
        docs = {
            d["id"]: d
            for d in StockDocument.objects.filter(id__in=ref_ids).values(
                "id", "code", "source_warehouse__name"
            )
        }
        rows: list[dict] = []
        for r in grouped:
            qty = -r["issued"]
            if qty == 0:
                continue
            d = docs.get(r["reference_id"], {})
            rows.append(
                {
                    "order_id": None,
                    "order_code": None,
                    "invoice_code": None,
                    "shipping_code": None,
                    "warehouse_name": d.get("source_warehouse__name"),
                    # Phiếu kho không có phần đảo → gộp = net. Giữ cùng shape với
                    # dòng đơn để FE dùng chung một luật hiển thị số dương.
                    "dispatched": qty,
                    "net": qty,
                    "last_at": r["last_at"],
                    "kind": "document",
                    "doc_code": d.get("code"),
                }
            )
        rows.sort(key=lambda x: x["net"], reverse=True)
        return rows


class MergedIssueHistory:
    """Trộn "xuất theo đơn" + "phiếu xuất kho" thành báo cáo tab Xuất có breakdown."""

    @staticmethod
    def orders_page(*, pagination: dict, **filters) -> dict:
        limit = pagination["limit"]
        offset = pagination["offset"]
        window = offset + limit

        order_head = list(DispatchHistoryService.order_groups(**filters)[:window])
        doc_rows = IssueDocumentHistoryService.doc_groups(**filters)
        doc_head = doc_rows[:window]
        transfer_rows = TransferDocumentHistoryService.doc_groups(
            MovementType.TRANSFER_OUT, **filters
        )
        transfer_head = transfer_rows[:window]

        # Top-(offset+limit) của hợp phải nằm trong top-(offset+limit) của mỗi nguồn.
        tagged = (
            [("order", g) for g in order_head]
            + [("doc", g) for g in doc_head]
            + [("transfer", g) for g in transfer_head]
        )
        _PRIORITY = {"order": 0, "doc": 1, "transfer": 2}
        tagged.sort(key=lambda t: (t[1]["last_at"], _PRIORITY[t[0]]), reverse=True)
        page = tagged[offset:window]

        order_page = [g for k, g in page if k == "order"]
        doc_page = [g for k, g in page if k == "doc"]
        transfer_page = [g for k, g in page if k == "transfer"]
        order_enriched = DispatchHistoryService.enrich_orders(order_page, **filters)
        # Ghi chú của dòng đơn xuất — 1 query cho cả trang. Nhồi Ở ĐÂY (không
        # phải ở view) để job export Excel dùng chung nguồn, không thể lệch màn
        # hình (xem docs/flow-history-notes.md §3.3).
        order_notes = FlowNoteService.notes_for(
            StockFlowNoteTarget.ORDER_DISPATCH,
            [it["order_id"] for it in order_enriched],
        )
        for it in order_enriched:
            it["kind"] = "order"
            it["doc_code"] = None
            it["doc_id"] = None
            it["note"] = order_notes.get(it["order_id"], "")
        order_map = {g["reference_id"]: it for g, it in zip(order_page, order_enriched)}
        doc_enriched = IssueDocumentHistoryService.enrich_docs(doc_page, **filters)
        doc_map = {g["reference_id"]: it for g, it in zip(doc_page, doc_enriched)}
        transfer_enriched = TransferDocumentHistoryService.enrich_out(
            transfer_page, **filters
        )
        transfer_map = {
            g["reference_id"]: it for g, it in zip(transfer_page, transfer_enriched)
        }
        source_maps = {"order": order_map, "doc": doc_map, "transfer": transfer_map}

        items = [source_maps[k][g["reference_id"]] for k, g in page]

        # Đếm trên CHÍNH tập đã lọc như `order_groups` — `base_queryset` còn cả
        # đơn chỉ có bút toán đảo trong kỳ, đếm ở đó ra total lớn hơn số dòng
        # bảng thật sự liệt kê (cùng bài học với DispatchHistoryService.orders_page).
        order_total = DispatchHistoryService.order_groups(**filters).count()
        sale_quantity = DispatchHistoryService.total_issued(**filters)
        doc_quantity = IssueDocumentHistoryService.total_issued(**filters)
        transfer_quantity = TransferDocumentHistoryService.total_out(**filters)
        return {
            "items": items,
            "total": order_total + len(doc_rows) + len(transfer_rows),
            "total_quantity": sale_quantity + doc_quantity + transfer_quantity,
            "sale_quantity": sale_quantity,
            "doc_quantity": doc_quantity,
            "transfer_quantity": transfer_quantity,
            "limit": limit,
            "offset": offset,
        }

    @staticmethod
    def skus_page(*, pagination: dict, **filters) -> dict:
        limit = pagination["limit"]
        offset = pagination["offset"]
        window = offset + limit

        order_head = list(DispatchHistoryService.sku_groups(**filters)[:window])
        doc_map = IssueDocumentHistoryService.skus_map(**filters)
        transfer_map = TransferDocumentHistoryService.skus_map(
            MovementType.TRANSFER_OUT, **filters
        )

        _EXTRA_ZERO = {
            "doc_quantity": 0,
            "doc_count": 0,
            "transfer_quantity": 0,
            "transfer_count": 0,
        }
        combined: dict[int, dict] = {}
        for r in order_head:
            combined[r["product_id"]] = {
                **DispatchHistoryService._sku_item(r),
                **_EXTRA_ZERO,
            }

        # SKU có phiếu kho/phiếu chuyển nhưng RỚT khỏi top-window theo đơn (lần
        # xuất-theo-đơn cũ hơn các SKU khác): không truy bù thì dòng nổi lên nhờ
        # last_at phiếu sẽ hiện "SL theo đơn = 0" trong khi footer (aggregate đầy
        # đủ) vẫn đúng → màn hình tự mâu thuẫn, đúng kiểu đếm-hụt-per-row mà
        # feature này vá (audit 2026-07-15b). 1 query bounded theo tập pid thiếu.
        extra_pids = set(doc_map) | set(transfer_map)
        missing_pids = [pid for pid in extra_pids if pid not in combined]
        if missing_pids:
            backfill = DispatchHistoryService.sku_groups(**filters).filter(
                product_id__in=missing_pids
            )
            for r in backfill:
                combined[r["product_id"]] = {
                    **DispatchHistoryService._sku_item(r),
                    **_EXTRA_ZERO,
                }

        for pid, d in doc_map.items():
            row = combined.get(pid)
            if row is None:
                row = MergedIssueHistory._empty_sku_row(pid, d)
                combined[pid] = row
            row["doc_quantity"] = d["doc_quantity"]
            row["doc_count"] = d["doc_count"]
            if d["last_at"] and (row["last_at"] is None or d["last_at"] > row["last_at"]):
                row["last_at"] = d["last_at"]

        for pid, d in transfer_map.items():
            row = combined.get(pid)
            if row is None:
                row = MergedIssueHistory._empty_sku_row(pid, d)
                combined[pid] = row
            row["transfer_quantity"] = d["transfer_quantity"]
            row["transfer_count"] = d["transfer_count"]
            if d["last_at"] and (row["last_at"] is None or d["last_at"] > row["last_at"]):
                row["last_at"] = d["last_at"]

        rows = sorted(
            combined.values(), key=lambda x: (x["last_at"], x["product_id"]), reverse=True
        )
        page = rows[offset:window]

        # Cùng luật với orders_page: đếm trên tập đã lọc `_HAS_REAL_SALE` như
        # `sku_groups`, không phải `base_queryset` (còn SKU chỉ có bút toán đảo).
        order_total = DispatchHistoryService.sku_groups(**filters).count()
        extra_pid_list = list(extra_pids)
        order_pids_with_extra = (
            set(
                DispatchHistoryService.sku_groups(**filters)
                .filter(product_id__in=extra_pid_list)
                .values_list("product_id", flat=True)
            )
            if extra_pid_list
            else set()
        )
        extra_only = [pid for pid in extra_pid_list if pid not in order_pids_with_extra]

        sale_quantity = DispatchHistoryService.total_issued(**filters)
        doc_quantity = IssueDocumentHistoryService.total_issued(**filters)
        transfer_quantity = TransferDocumentHistoryService.total_out(**filters)
        return {
            "items": page,
            "total": order_total + len(extra_only),
            "total_quantity": sale_quantity + doc_quantity + transfer_quantity,
            "sale_quantity": sale_quantity,
            "doc_quantity": doc_quantity,
            "transfer_quantity": transfer_quantity,
            "limit": limit,
            "offset": offset,
        }

    @staticmethod
    def _empty_sku_row(pid: int, d: dict) -> dict:
        """Dòng SKU không có xuất-theo-đơn (chỉ phiếu kho / phiếu chuyển)."""
        return {
            "product_id": pid,
            "sku": d["sku"],
            "name": d["name"],
            "order_count": 0,
            "dispatched": 0,
            "reversed": 0,
            "net": 0,
            "last_at": d["last_at"],
            "doc_quantity": 0,
            "doc_count": 0,
            "transfer_quantity": 0,
            "transfer_count": 0,
        }

    @staticmethod
    def sku_drill(*, product_id: int, **filters) -> list[dict]:
        """Đơn + phiếu kho + phiếu chuyển chứa 1 SKU, mỗi dòng có ``kind``."""
        orders = DispatchHistoryService.sku_orders(product_id=product_id, **filters)
        for it in orders:
            it["kind"] = "order"
            it["doc_code"] = None
        docs = IssueDocumentHistoryService.sku_documents(
            product_id=product_id, **filters
        )
        transfers = TransferDocumentHistoryService.sku_documents_out(
            product_id=product_id, **filters
        )
        rows = orders + docs + transfers
        rows.sort(key=lambda x: x["net"], reverse=True)
        return rows
