"""Service lịch sử Nhập hoàn — mỗi dòng là 1 NHÓM ĐƠN HOÀN (ReturnEntry) đã ghi
sổ, tức 1 đơn được hoàn. Phiếu ngày (ReturnNote) chỉ đóng góp mã phiếu + kho +
ngày chứng từ.
"""
from __future__ import annotations

from collections import defaultdict

from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.db.models.functions import Coalesce

from apps.common import db_lookups  # noqa: F401  (đăng ký lookup `__ilike_contains`)
from apps.inventory.application.services.carrier_rules import carrier_q
from apps.inventory.models import (
    ReturnEntry,
    ReturnNoteLine,
    ReturnStatus,
    ReturnType,
)

_RETURN_TYPE_LABELS = dict(ReturnType.choices)
_MIXED_LABEL = "Nhiều loại"
_UNKNOWN_LABEL = "Chưa phân loại"


def _type_view(types: set[str]) -> tuple[str, str]:
    """Loại hoàn ở mức nhóm đơn: suy từ các DÒNG (nhóm đơn không còn field riêng).

    Nhiều loại trong cùng nhóm → mã rỗng + nhãn "Nhiều loại" (FE chỉ hiển thị nhãn).
    """
    if len(types) == 1:
        code = next(iter(types))
        return code, _RETURN_TYPE_LABELS.get(code) or _UNKNOWN_LABEL
    if not types:
        return "", _UNKNOWN_LABEL
    return "", _MIXED_LABEL


class ReturnHistoryService:
    @staticmethod
    def _base(
        *,
        date_from="",
        date_to="",
        # Rỗng/None = mọi kho; nhiều id = lọc theo tập kho đã chọn.
        warehouse_ids=None,
        product="",
        product_ids=None,
        order="",
        carrier="",
    ):
        qs = ReturnEntry.objects.filter(status=ReturnStatus.POSTED)
        if carrier:
            # ĐVVC suy từ ĐƠN NGUỒN của nhóm hoàn; nhóm không gắn đơn nguồn thì
            # không thuộc ĐVVC nào (kể cả "Khác") — loại khỏi kết quả lọc.
            qs = qs.filter(source_order__isnull=False).filter(
                carrier_q(carrier, prefix="source_order__")
            )
        if product_ids:
            line_ids_match = ReturnNoteLine.objects.filter(
                entry_id=OuterRef("pk"), product_id__in=product_ids
            )
            qs = qs.annotate(_has_pid=Exists(line_ids_match)).filter(_has_pid=True)
        # Lọc theo NGÀY CHỨNG TỪ (giờ VN) của phiếu ngày — created_at là UTC nên
        # lọc theo nó lệch ngày với các nhóm đơn quét buổi tối.
        if date_from:
            qs = qs.filter(return_note__doc_date__gte=date_from)
        if date_to:
            qs = qs.filter(return_note__doc_date__lte=date_to)
        if warehouse_ids:
            qs = qs.filter(return_note__warehouse_id__in=warehouse_ids)
        order = (order or "").strip()
        if order:
            # `__ilike_contains`, KHÔNG phải `__icontains`: xem apps/common/db_lookups.py.
            # `icontains` sinh `UPPER(col) LIKE` nên planner bỏ qua sạch index GIN
            # trigram trên `orders.order_code` / `invoice_code` / `shipping_code`.
            # Bản vá 28/07 (commit db_lookups) mới áp cho dispatch_history_service —
            # ba màn hình còn lại vẫn seq scan qua cùng một cơ chế, chỉ chưa lộ ra
            # trong top pg_stat_statements vì lưu lượng thấp hơn nhiều.
            # `shipping_code` của phiếu trả (bảng ReturnEntry) chưa có index trigram
            # nên chưa nhanh lên, vẫn đổi để ba nhánh OR cùng một dạng câu và để lần
            # thêm index sau ăn ngay.
            qs = qs.filter(
                Q(source_order__order_code__ilike_contains=order)
                | Q(source_order__invoice_code__ilike_contains=order)
                | Q(shipping_code__ilike_contains=order)
            )
        product = (product or "").strip()
        if product:
            line_match = ReturnNoteLine.objects.filter(entry_id=OuterRef("pk")).filter(
                Q(product__sku__icontains=product)
                | Q(product__name__icontains=product)
            )
            qs = qs.annotate(_has_prod=Exists(line_match)).filter(_has_prod=True)
        return qs

    @staticmethod
    def total_returned_quantity(**filters) -> int:
        """Tổng SL hoàn trên toàn bộ entry khớp filter — luật per-SKU khi lọc SKU.

        Lọc `product_ids` thì chỉ đếm dòng của đúng SKU đó (parity với
        transfer_quantity per-SKU và footer tab Xuất); không lọc thì đếm đủ mọi dòng
        như cũ (audit 2026-07-18). Mọi nơi hiển thị return_quantity PHẢI dùng chung
        helper này để 2 vế "hoàn + chuyển = C" cùng một luật.
        """
        line_qs = ReturnNoteLine.objects.filter(
            entry__in=ReturnHistoryService._base(**filters)
        )
        if filters.get("product_ids"):
            line_qs = line_qs.filter(product_id__in=filters["product_ids"])
        return line_qs.aggregate(t=Coalesce(Sum("quantity"), 0))["t"]

    @staticmethod
    def _lines_qty_types(
        entry_ids: list[int],
    ) -> tuple[dict[int, list[dict]], dict[int, int], dict[int, set]]:
        """Dòng SKU + SL hoàn + tập loại hoàn của từng nhóm đơn — 1 query cho cả trang.

        `lines` ở mức NHÓM ĐƠN (không lọc theo bộ lọc sản phẩm): bộ lọc sản phẩm lọc
        nhóm đơn bằng `Exists`, nên nhóm đã khớp phải hiện đủ mọi SKU của nó.
        """
        lines_map: dict[int, list[dict]] = defaultdict(list)
        qty_map: dict[int, int] = defaultdict(int)
        type_map: dict[int, set] = defaultdict(set)
        rows = (
            ReturnNoteLine.objects.filter(entry_id__in=entry_ids)
            .values("entry_id", "return_type", "product_id", "product__sku")
            .annotate(qty=Coalesce(Sum("quantity"), 0))
        )
        for row in rows:
            eid = row["entry_id"]
            lines_map[eid].append(
                {
                    "product_id": row["product_id"],
                    "sku": row["product__sku"],
                    "qty": row["qty"],
                }
            )
            qty_map[eid] += row["qty"]
            type_map[eid].add(row["return_type"])
        for rows_of_entry in lines_map.values():
            rows_of_entry.sort(key=lambda x: (-x["qty"], x["sku"] or ""))
        return lines_map, qty_map, type_map

    @staticmethod
    def entry_items(page: list) -> list[dict]:
        """Item hiển thị cho 1 trang nhóm đơn (đã select_related) — kind='return'.

        Tách riêng để ``MergedReturnHistory`` (trộn thêm phiếu chuyển kho) dùng
        chung shape với danh sách thuần."""
        lines_map, qty_map, type_map = ReturnHistoryService._lines_qty_types(
            [e.id for e in page]
        )
        items = []
        for e in page:
            return_type, type_label = _type_view(type_map.get(e.id, set()))
            items.append(
                {
                    "return_id": e.id,
                    "code": e.return_note.code,
                    "source_order_id": e.source_order_id,
                    "source_order_code": (
                        e.source_order.order_code if e.source_order_id else None
                    ),
                    "source_order_invoice_code": (
                        (e.source_order.invoice_code or None)
                        if e.source_order_id
                        else None
                    ),
                    "shipping_code": e.shipping_code or None,
                    "warehouse_name": e.return_note.warehouse.name,
                    "performed_by": (
                        (e.created_by.display_name or e.created_by.username)
                        if e.created_by_id
                        else ""
                    ),
                    "return_type": return_type,
                    "return_type_label": type_label,
                    "lines": lines_map.get(e.id, []),
                    "qty_total": qty_map.get(e.id, 0),
                    "note": e.note,
                    "created_at": e.created_at,
                    "kind": "return",
                }
            )
        return items

    @staticmethod
    def list_page(*, pagination: dict, **filters) -> dict:
        """Danh sách chính tab Nhập hoàn: mỗi dòng 1 nhóm đơn đã ghi sổ."""
        qs = ReturnHistoryService._base(**filters).select_related(
            "return_note", "return_note__warehouse", "source_order", "created_by"
        )
        total = qs.count()
        offset = pagination["offset"]
        page = list(
            qs.order_by("-created_at", "-id")[offset : offset + pagination["limit"]]
        )
        items = ReturnHistoryService.entry_items(page)
        # Tổng SL hoàn trên TOÀN BỘ nhóm đơn khớp bộ lọc, không chỉ trang hiện tại
        # (luật per-SKU khi lọc SKU — xem total_returned_quantity).
        total_quantity = ReturnHistoryService.total_returned_quantity(**filters)
        return {
            "items": items,
            "total": total,
            "total_quantity": total_quantity,
            "limit": pagination["limit"],
            "offset": offset,
        }

    @staticmethod
    def skus_page(*, pagination: dict, **filters) -> dict:
        """View Theo SKU tab Nhập hoàn: gộp dòng của nhóm đơn POSTED theo product."""
        entry_qs = ReturnHistoryService._base(**filters)
        line_qs = ReturnNoteLine.objects.filter(entry__in=entry_qs)
        # Lọc SKU: view Theo-SKU chỉ hiện + đếm đúng SKU được lọc (parity tab Xuất).
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
        total = line_qs.values("product_id").distinct().count()
        offset = pagination["offset"]
        page = list(grouped[offset : offset + pagination["limit"]])
        items = [
            {
                "product_id": r["product_id"],
                "sku": r["product__sku"],
                "name": r["product__name"],
                "return_count": r["return_count"],
                "qty_total": r["qty_total"],
            }
            for r in page
        ]
        total_quantity = line_qs.aggregate(t=Coalesce(Sum("quantity"), 0))["t"]
        return {
            "items": items,
            "total": total,
            "total_quantity": total_quantity,
            "limit": pagination["limit"],
            "offset": offset,
        }

    @staticmethod
    def sku_returns(*, product_id: int, **filters) -> list[dict]:
        """Drill-down: các nhóm đơn hoàn POSTED chứa một SKU."""
        entry_qs = ReturnHistoryService._base(**filters)
        rows = (
            ReturnNoteLine.objects.filter(entry__in=entry_qs, product_id=product_id)
            .select_related(
                "entry",
                "entry__return_note",
                "entry__return_note__warehouse",
                "entry__source_order",
            )
            .order_by("-entry__created_at", "-id")
        )
        out = []
        for r in rows:
            e = r.entry
            out.append(
                {
                    "return_id": e.id,
                    "code": e.return_note.code,
                    "source_order_code": (
                        e.source_order.order_code if e.source_order_id else None
                    ),
                    "source_order_invoice_code": (
                        (e.source_order.invoice_code or None)
                        if e.source_order_id
                        else None
                    ),
                    "shipping_code": e.shipping_code or None,
                    "warehouse_name": e.return_note.warehouse.name,
                    # Loại hoàn của CHÍNH DÒNG này (chính xác hơn mức nhóm đơn).
                    "return_type_label": _RETURN_TYPE_LABELS.get(r.return_type)
                    or _UNKNOWN_LABEL,
                    "quantity": r.quantity,
                    "created_at": e.created_at,
                }
            )
        return out
