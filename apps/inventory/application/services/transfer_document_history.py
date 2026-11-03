"""Phiếu CHUYỂN KHO (StockDocument doc_type=transfer) trên trang Lịch sử nhập xuất.

Phiếu chuyển ghi 2 movement mỗi SKU: ``transfer_out`` (qty âm, kho nguồn) và
``transfer_in`` (qty dương, kho đích). Tab Xuất đọc nhánh OUT, tab Nhập hoàn đọc
nhánh IN — mỗi bên là bucket ``transfer_quantity`` RIÊNG, không lẫn vào tổng
xuất theo đơn / phiếu XK / SL hoàn. Huỷ phiếu tạo bút toán đảo CÙNG movement_type
nên tổng theo phiếu tự về 0 trong cửa sổ chứa CẢ HAI vế; huỷ khác ngày ghi sổ
thì mỗi vế rơi một cửa sổ nên phải loại thẳng phiếu ``cancelled`` (xem
``base_queryset``), net 0 chỉ còn là lưới đỡ.

``MergedReturnHistory`` trộn nguồn "nhóm đơn hoàn" (ReturnHistoryService) với
nhánh transfer_in ở đây — mirror pattern ``MergedIssueHistory`` của tab Xuất.
"""
from __future__ import annotations

from django.db.models import Count, Max, Q, QuerySet, Sum
from django.db.models.functions import Coalesce

from apps.inventory.application.services.movement_window import (
    filter_occurred_between,
)
from apps.inventory.application.services.return_history_service import (
    ReturnHistoryService,
)
from apps.inventory.models import (
    DocStatus,
    MovementType,
    ReturnNoteLine,
    StockDocument,
    StockMovement,
)

_DOC = "document"
TRANSFER_LABEL = "Chuyển kho"

# transfer_out ghi qty_delta âm → đảo dấu để ra SL dương; transfer_in giữ nguyên.
_SIGN = {MovementType.TRANSFER_OUT: -1, MovementType.TRANSFER_IN: 1}


class TransferDocumentHistoryService:
    @staticmethod
    def base_queryset(
        *,
        movement_type: str,
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
        # Phiếu chuyển không gắn đơn hàng: lọc theo 1 đơn cụ thể hoặc theo ĐVVC
        # ⇒ không có phiếu.
        if order_id or carrier:
            return StockMovement.objects.none()
        qs = StockMovement.objects.filter(
            movement_type=movement_type, reference_type=_DOC
        )
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
        # movement transfer_out nằm ở kho nguồn, transfer_in ở kho đích → lọc kho
        # tự khớp đúng phía (tab Xuất khớp kho bị trừ, tab Nhập hoàn kho được cộng).
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

    # ───────────────────────── aggregate primitives ─────────────────────────

    @staticmethod
    def _total(movement_type: str, **filters) -> int:
        qs = TransferDocumentHistoryService.base_queryset(
            movement_type=movement_type, **filters
        )
        return _SIGN[movement_type] * qs.aggregate(s=Coalesce(Sum("qty_delta"), 0))["s"]

    @staticmethod
    def total_out(**filters) -> int:
        return TransferDocumentHistoryService._total(MovementType.TRANSFER_OUT, **filters)

    @staticmethod
    def total_in(**filters) -> int:
        return TransferDocumentHistoryService._total(MovementType.TRANSFER_IN, **filters)

    @staticmethod
    def doc_groups(movement_type: str, **filters) -> list[dict]:
        """Danh sách phiếu (net ≠ 0) sắp theo thời gian giảm dần.

        Mỗi phần tử: ``{reference_id, quantity, last_at}``; phiếu đã huỷ (net 0)
        bị loại."""
        sign = _SIGN[movement_type]
        grouped = (
            TransferDocumentHistoryService.base_queryset(
                movement_type=movement_type, **filters
            )
            .values("reference_id")
            .annotate(moved=Coalesce(Sum("qty_delta"), 0), last_at=Max("occurred_at"))
            .order_by("-last_at", "-reference_id")
        )
        rows: list[dict] = []
        for r in grouped:
            qty = sign * r["moved"]
            if qty == 0:
                continue
            rows.append(
                {"reference_id": r["reference_id"], "quantity": qty, "last_at": r["last_at"]}
            )
        return rows

    @staticmethod
    def _doc_meta(ref_ids: list[int]) -> dict[int, dict]:
        """{doc_id: {code, label, note}} — label = "Kho nguồn → Kho đích"."""
        out: dict[int, dict] = {}
        for d in StockDocument.objects.filter(id__in=ref_ids).values(
            "id", "code", "note", "source_warehouse__name", "dest_warehouse__name"
        ):
            src = d["source_warehouse__name"] or "?"
            dest = d["dest_warehouse__name"] or "?"
            out[d["id"]] = {
                "code": d["code"],
                "note": d["note"] or "",
                "label": f"{src} → {dest}",
            }
        return out

    @staticmethod
    def _performers(movement_type: str, ref_ids: list[int]) -> dict[int, str]:
        """Người ghi sổ: created_by của movement mới nhất của phiếu."""
        performer: dict[int, str] = {}
        for rid, disp, uname in (
            StockMovement.objects.filter(
                movement_type=movement_type, reference_type=_DOC, reference_id__in=ref_ids
            )
            .order_by("created_at", "id")
            .values_list(
                "reference_id", "created_by__display_name", "created_by__username"
            )
        ):
            performer[rid] = disp or uname or ""
        return performer

    @staticmethod
    def _lines_map(
        movement_type: str, ref_ids: list[int], **filters
    ) -> dict[int, list[dict]]:
        """Dòng SKU (net theo product) của từng phiếu — 1 query gộp, không N+1."""
        sign = _SIGN[movement_type]
        lines_map: dict[int, list[dict]] = {}
        for r in (
            TransferDocumentHistoryService.base_queryset(
                movement_type=movement_type, **filters
            )
            .filter(reference_id__in=ref_ids)
            .values("reference_id", "product_id", "product__sku")
            .annotate(moved=Coalesce(Sum("qty_delta"), 0))
        ):
            qty = sign * r["moved"]
            if qty == 0:
                continue
            lines_map.setdefault(r["reference_id"], []).append(
                {"product_id": r["product_id"], "sku": r["product__sku"], "qty": qty}
            )
        for v in lines_map.values():
            v.sort(key=lambda x: (-x["qty"], x["sku"] or ""))
        return lines_map

    # ─────────────────────── item shapes cho 2 tab ───────────────────────

    @staticmethod
    def enrich_out(groups: list[dict], **filters) -> list[dict]:
        """Item CÙNG SHAPE dòng tab Xuất (kind='transfer'), mã đơn để None."""
        ref_ids = [g["reference_id"] for g in groups]
        docs = TransferDocumentHistoryService._doc_meta(ref_ids)
        performer = TransferDocumentHistoryService._performers(
            MovementType.TRANSFER_OUT, ref_ids
        )
        lines_map = TransferDocumentHistoryService._lines_map(
            MovementType.TRANSFER_OUT, ref_ids, **filters
        )
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
                    "warehouse_name": d.get("label"),
                    "performed_by": performer.get(rid, ""),
                    "lines": lines,
                    "sku_count": len(lines),
                    "dispatched": g["quantity"],
                    "reversed": 0,
                    "net": g["quantity"],
                    "last_at": g["last_at"],
                    "kind": "transfer",
                    "doc_code": d.get("code"),
                    # Ghi chú của dòng = ghi chú của PHIẾU chuyển kho: cùng một
                    # chứng từ nên tab Xuất và tab Nhập hoàn dùng chung (cố ý —
                    # xem docs/flow-history-notes.md §2.4).
                    "doc_id": rid,
                    "note": d.get("note", ""),
                }
            )
        return items

    @staticmethod
    def enrich_in(groups: list[dict], **filters) -> list[dict]:
        """Item CÙNG SHAPE dòng tab Nhập hoàn (kind='transfer'), return_id=None."""
        ref_ids = [g["reference_id"] for g in groups]
        docs = TransferDocumentHistoryService._doc_meta(ref_ids)
        performer = TransferDocumentHistoryService._performers(
            MovementType.TRANSFER_IN, ref_ids
        )
        lines_map = TransferDocumentHistoryService._lines_map(
            MovementType.TRANSFER_IN, ref_ids, **filters
        )
        items: list[dict] = []
        for g in groups:
            rid = g["reference_id"]
            d = docs.get(rid, {})
            lines = lines_map.get(rid, [])
            items.append(
                {
                    "return_id": None,
                    "code": d.get("code"),
                    "source_order_id": None,
                    "source_order_code": None,
                    "source_order_invoice_code": None,
                    "shipping_code": None,
                    "warehouse_name": d.get("label"),
                    "performed_by": performer.get(rid, ""),
                    "return_type": "",
                    "return_type_label": TRANSFER_LABEL,
                    "lines": lines,
                    "qty_total": g["quantity"],
                    "note": d.get("note", ""),
                    "created_at": g["last_at"],
                    "kind": "transfer",
                    "doc_id": rid,
                }
            )
        return items

    @staticmethod
    def skus_map(movement_type: str, **filters) -> dict[int, dict]:
        """{product_id: {transfer_quantity, transfer_count, last_at, sku, name}}.

        Chỉ product có net ≠ 0 — dùng để cộng vào view Theo SKU."""
        sign = _SIGN[movement_type]
        out: dict[int, dict] = {}
        for r in (
            TransferDocumentHistoryService.base_queryset(
                movement_type=movement_type, **filters
            )
            .values("product_id", "product__sku", "product__name")
            .annotate(
                moved=Coalesce(Sum("qty_delta"), 0),
                transfer_count=Count("reference_id", distinct=True),
                last_at=Max("occurred_at"),
            )
        ):
            qty = sign * r["moved"]
            if qty == 0:
                continue
            out[r["product_id"]] = {
                "transfer_quantity": qty,
                "transfer_count": r["transfer_count"],
                "last_at": r["last_at"],
                "sku": r["product__sku"],
                "name": r["product__name"],
            }
        return out

    @staticmethod
    def sku_documents_out(*, product_id: int, **filters) -> list[dict]:
        """Drill-down tab Xuất: các phiếu chuyển chứa 1 SKU."""
        filters.pop("product_id", None)
        filters.pop("product", None)
        qs = TransferDocumentHistoryService.base_queryset(
            movement_type=MovementType.TRANSFER_OUT, product_id=product_id, **filters
        )
        grouped = qs.values("reference_id").annotate(
            moved=Coalesce(Sum("qty_delta"), 0), last_at=Max("occurred_at")
        )
        docs = TransferDocumentHistoryService._doc_meta(
            [r["reference_id"] for r in grouped]
        )
        rows: list[dict] = []
        for r in grouped:
            qty = -r["moved"]
            if qty == 0:
                continue
            d = docs.get(r["reference_id"], {})
            rows.append(
                {
                    "order_id": None,
                    "order_code": None,
                    "invoice_code": None,
                    "shipping_code": None,
                    "warehouse_name": d.get("label"),
                    # Phiếu chuyển kho không có phần đảo → gộp = net (xem
                    # `issue_document_history.sku_orders`).
                    "dispatched": qty,
                    "net": qty,
                    "last_at": r["last_at"],
                    "kind": "transfer",
                    "doc_code": d.get("code"),
                }
            )
        rows.sort(key=lambda x: x["net"], reverse=True)
        return rows

    @staticmethod
    def sku_documents_in(*, product_id: int, **filters) -> list[dict]:
        """Drill-down tab Nhập hoàn: các phiếu chuyển đưa 1 SKU về kho đích."""
        filters.pop("product_id", None)
        filters.pop("product", None)
        qs = TransferDocumentHistoryService.base_queryset(
            movement_type=MovementType.TRANSFER_IN, product_id=product_id, **filters
        )
        grouped = qs.values("reference_id").annotate(
            moved=Coalesce(Sum("qty_delta"), 0), last_at=Max("occurred_at")
        )
        docs = TransferDocumentHistoryService._doc_meta(
            [r["reference_id"] for r in grouped]
        )
        rows: list[dict] = []
        for r in grouped:
            qty = r["moved"]
            if qty == 0:
                continue
            d = docs.get(r["reference_id"], {})
            rows.append(
                {
                    "return_id": None,
                    "code": d.get("code"),
                    "source_order_code": None,
                    "source_order_invoice_code": None,
                    "shipping_code": None,
                    "warehouse_name": d.get("label"),
                    "return_type_label": TRANSFER_LABEL,
                    "quantity": qty,
                    "created_at": r["last_at"],
                    "kind": "transfer",
                }
            )
        rows.sort(key=lambda x: x["quantity"], reverse=True)
        return rows


class MergedReturnHistory:
    """Trộn "nhóm đơn hoàn" + "phiếu chuyển kho (nhánh IN)" cho tab Nhập hoàn."""

    @staticmethod
    def list_page(*, pagination: dict, **filters) -> dict:
        limit = pagination["limit"]
        offset = pagination["offset"]
        window = offset + limit

        entry_qs = (
            ReturnHistoryService._base(**filters)
            .select_related(
                "return_note", "return_note__warehouse", "source_order", "created_by"
            )
            .order_by("-created_at", "-id")
        )
        entry_total = entry_qs.count()
        entry_head = list(entry_qs[:window])
        transfer_rows = TransferDocumentHistoryService.doc_groups(
            MovementType.TRANSFER_IN, **filters
        )
        transfer_head = transfer_rows[:window]

        # Top-(offset+limit) của hợp phải nằm trong top-(offset+limit) mỗi nguồn.
        tagged = [("return", e) for e in entry_head] + [
            ("transfer", g) for g in transfer_head
        ]
        tagged.sort(
            key=lambda t: (
                t[1].created_at if t[0] == "return" else t[1]["last_at"],
                0 if t[0] == "return" else 1,
            ),
            reverse=True,
        )
        page = tagged[offset:window]

        entry_page = [e for k, e in page if k == "return"]
        entry_items = ReturnHistoryService.entry_items(entry_page)
        # Dòng nhóm đơn hoàn không gắn với phiếu kho nào — giữ khoá `doc_id` để
        # FE có một shape duy nhất cho ô ghi chú của cả hai loại dòng.
        for it in entry_items:
            it["doc_id"] = None
        entry_map = {e.id: it for e, it in zip(entry_page, entry_items)}
        transfer_page = [g for k, g in page if k == "transfer"]
        transfer_items = TransferDocumentHistoryService.enrich_in(
            transfer_page, **filters
        )
        transfer_map = {
            g["reference_id"]: it for g, it in zip(transfer_page, transfer_items)
        }

        items = [
            entry_map[e.id] if k == "return" else transfer_map[e["reference_id"]]
            for k, e in page
        ]

        # Luật per-SKU khi lọc SKU (parity với transfer per-SKU — audit 2026-07-18).
        return_quantity = ReturnHistoryService.total_returned_quantity(**filters)
        transfer_quantity = TransferDocumentHistoryService.total_in(**filters)
        return {
            "items": items,
            "total": entry_total + len(transfer_rows),
            "total_quantity": return_quantity + transfer_quantity,
            "return_quantity": return_quantity,
            "transfer_quantity": transfer_quantity,
            "limit": limit,
            "offset": offset,
        }

    @staticmethod
    def skus_page(*, pagination: dict, **filters) -> dict:
        limit = pagination["limit"]
        offset = pagination["offset"]
        window = offset + limit

        entry_qs = ReturnHistoryService._base(**filters)
        line_qs = ReturnNoteLine.objects.filter(entry__in=entry_qs)
        # Lọc SKU: view Theo-SKU chỉ hiện + đếm đúng SKU được lọc (audit 2026-07-18).
        if filters.get("product_ids"):
            line_qs = line_qs.filter(product_id__in=filters["product_ids"])
        grouped = (
            line_qs.values("product_id", "product__sku", "product__name")
            .annotate(
                qty_total=Coalesce(Sum("quantity"), 0),
                return_count=Count("entry", distinct=True),
            )
            .order_by("-qty_total", "-product_id")
        )
        transfer_map = TransferDocumentHistoryService.skus_map(
            MovementType.TRANSFER_IN, **filters
        )

        combined: dict[int, dict] = {}
        for r in grouped[:window]:
            combined[r["product_id"]] = MergedReturnHistory._sku_item(r)
        # SKU có phiếu chuyển nhưng RỚT khỏi top-window hoàn: truy bù để dòng
        # không hiện SL hoàn = 0 tự mâu thuẫn với footer (mirror tab Xuất).
        missing_pids = [pid for pid in transfer_map if pid not in combined]
        if missing_pids:
            for r in grouped.filter(product_id__in=missing_pids):
                combined[r["product_id"]] = MergedReturnHistory._sku_item(r)
        for pid, d in transfer_map.items():
            row = combined.get(pid)
            if row is None:
                row = {
                    "product_id": pid,
                    "sku": d["sku"],
                    "name": d["name"],
                    "return_count": 0,
                    "qty_total": 0,
                    "transfer_quantity": 0,
                    "transfer_count": 0,
                }
                combined[pid] = row
            row["transfer_quantity"] = d["transfer_quantity"]
            row["transfer_count"] = d["transfer_count"]

        rows = sorted(
            combined.values(),
            key=lambda x: (x["qty_total"] + x["transfer_quantity"], x["product_id"]),
            reverse=True,
        )
        page = rows[offset:window]

        return_total = line_qs.values("product_id").distinct().count()
        transfer_pids = list(transfer_map.keys())
        return_pids_with_transfer = (
            set(
                line_qs.filter(product_id__in=transfer_pids)
                .values_list("product_id", flat=True)
                .distinct()
            )
            if transfer_pids
            else set()
        )
        transfer_only = [
            pid for pid in transfer_pids if pid not in return_pids_with_transfer
        ]

        return_quantity = line_qs.aggregate(t=Coalesce(Sum("quantity"), 0))["t"]
        transfer_quantity = TransferDocumentHistoryService.total_in(**filters)
        return {
            "items": page,
            "total": return_total + len(transfer_only),
            "total_quantity": return_quantity + transfer_quantity,
            "return_quantity": return_quantity,
            "transfer_quantity": transfer_quantity,
            "limit": limit,
            "offset": offset,
        }

    @staticmethod
    def _sku_item(r: dict) -> dict:
        return {
            "product_id": r["product_id"],
            "sku": r["product__sku"],
            "name": r["product__name"],
            "return_count": r["return_count"],
            "qty_total": r["qty_total"],
            "transfer_quantity": 0,
            "transfer_count": 0,
        }

    @staticmethod
    def sku_drill(*, product_id: int, **filters) -> list[dict]:
        """Nhóm đơn hoàn + phiếu chuyển chứa 1 SKU, mỗi dòng có ``kind``."""
        returns = ReturnHistoryService.sku_returns(product_id=product_id, **filters)
        for it in returns:
            it["kind"] = "return"
        transfers = TransferDocumentHistoryService.sku_documents_in(
            product_id=product_id, **filters
        )
        rows = returns + transfers
        rows.sort(key=lambda x: x["quantity"], reverse=True)
        return rows
