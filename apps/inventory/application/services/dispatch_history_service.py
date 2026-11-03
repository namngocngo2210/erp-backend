from __future__ import annotations

from django.db.models import Count, Exists, F, Max, OuterRef, Q, QuerySet, Sum, Value
from django.db.models.functions import Coalesce

from apps.common import db_lookups  # noqa: F401  (đăng ký lookup `__ilike_contains`)
from apps.inventory.application.services.carrier_rules import carrier_q
from apps.inventory.application.services.movement_window import (
    filter_occurred_between,
)
from apps.inventory.application.services.stock_sales_service import (
    ADMIN_SALE_NOTES,
    CANCEL_RELEASE_NOTE,
    COMBO_BACKFILL_RELEASE_NOTE,
    SIBLING_TRANSFER_RELEASE_NOTE,
)
from apps.inventory.models import (
    PendingScanKind,
    PendingScanStatus,
    PendingShipmentScan,
    StockMovement,
)
from apps.orders.models import Order

_SALE = "sale"
_SALE_REVERSAL = "sale_reversal"


# Nhóm CHỈ được coi là một lần xuất khi trong khoảng lọc có bút toán SALE thật
# (`sale_sum` là tổng qty_delta của SALE nên luôn ÂM khi có hàng rời kho).
#
# Không có điều kiện này thì một lần HUỶ ĐƠN tự dựng ra dòng "xuất": đơn xuất
# ngày 23 bị huỷ ngày 27 để hạ COD, lọc đúng ngày 27 chỉ thấy bút toán đảo →
# `_dispatch_last_at` rơi vào fallback và gắn ngày 27 cho dòng, SL ra số ÂM
# (ca prod 999800020077015: hiện "xuất ngày 27, -2 sản phẩm"). Đảo sổ là nghiệp
# vụ hoàn tồn, không phải hàng rời kho.
#
# Bút toán đảo VẪN trừ vào SL của đơn khi đơn đó có lần xuất trong CÙNG khoảng
# lọc (xuất rồi thu hồi trong ngày) — chỉ là nó không tự tạo dòng một mình.
_HAS_REAL_SALE = {"sale_sum__lt": 0}


# Bút toán đảo xảy ra SAU khi hàng đã rời kho: huỷ đơn, và chuyển sổ sang đơn
# xuất lại. Hàng đã đi thật nên chúng KHÔNG xoá đi một lần xuất đã xảy ra.
#
# Ngược lại, đảo mang note rỗng (bỏ đánh dấu đã xuất kho — bấm nhầm) hoặc
# `RESYNC_NOTE` (sửa items sau xuất) nghĩa là phần hàng đó CHƯA từng rời kho —
# phải trừ đi, nếu không sản lượng xuất phồng lên vì thao tác sửa sai.
_POST_DISPATCH_REVERSAL_NOTES = (
    CANCEL_RELEASE_NOTE,
    SIBLING_TRANSFER_RELEASE_NOTE,
)


def _combo_moved_expr():
    """Phần SL đã CHUYỂN SỔ từ SKU combo sang mã lẻ (backfill combo).

    Khác mọi bút toán đảo khác: hàng vẫn rời kho thật, chỉ là nó được ghi lại
    dưới các mã lẻ nên phần này KHÔNG còn là sản lượng xuất của chính SKU combo.
    Không trừ nó khỏi `dispatched` thì dòng combo có net = 0 vẫn hiện SL bằng
    `gross` (xem `display_qty`) — tức là đếm hai lần cùng một kiện hàng, một lần
    ở SKU combo và một lần ở mã lẻ.
    """
    return Coalesce(
        Sum(
            "qty_delta",
            filter=Q(movement_type=_SALE_REVERSAL)
            & Q(note=COMBO_BACKFILL_RELEASE_NOTE),
        ),
        0,
    )


def _issued_qty_expr():
    """SL hàng THẬT rời kho trong khoảng lọc, gộp theo nhóm.

    = SALE thật (loại bút toán hành chính — đơn `.NN` chỉ NHẬN SỔ chứ không có
    lần rời kho riêng, tính nữa là đếm hai lần cùng một kiện hàng)
    − các đảo "chưa từng rời kho".
    """
    dispatched = Coalesce(
        Sum(
            "qty_delta",
            filter=Q(movement_type=_SALE) & ~Q(note__in=ADMIN_SALE_NOTES),
        ),
        0,
    )
    undone = Coalesce(
        Sum(
            "qty_delta",
            filter=Q(movement_type=_SALE_REVERSAL)
            & ~Q(note__in=_POST_DISPATCH_REVERSAL_NOTES),
        ),
        0,
    )
    return (Value(0) - dispatched) - undone


def display_qty(net: int, gross: int) -> int:
    """SL xuất ghi nhận cho một đơn/SKU — LUÔN dương, không bao giờ 0.

    Bản Python của ``frontend/src/features/inventory/flow-quantity.ts``; dùng
    chung cho export Excel và cho TỔNG chạy trên DB (``_issued_expr``) để ba nơi
    không trôi khỏi nhau.

    Đơn xuất rồi bị huỷ/chuyển sổ hết trong kỳ có net = 0 — tồn về 0 là đúng,
    nhưng hàng ĐÃ rời kho nên vẫn phải ghi nhận là một lần xuất (ca prod
    HDTTS_585205879163881436). Đơn hoàn MỘT PHẦN vẫn lấy net: xuất 3 hoàn 1 thì
    sản lượng là 2, không phải 3.
    """
    return abs(net) or abs(gross)


def _dispatch_last_at():
    """Ngày xuất hiển thị = lần SALE THẬT gần nhất (loại SALE hành chính).

    Dùng `occurred_at` (ngày hàng rời kho) chứ không phải `created_at` (ngày ghi
    sổ): mã quét ngày D khớp được đơn ở ngày D+n vẫn phải hiện ở ngày D.

    Fallback ``Max(occurred_at)`` cho nhóm chỉ còn bút toán hành chính trong
    khoảng lọc (vd. lọc đúng ngày hồi sinh → cặp release/reissue net 0) —
    không được trả null vì cột ngày và sort đều dựa vào giá trị này. Với đơn
    xuất lại `<mã>.NN` thì bút toán hành chính là thứ DUY NHẤT đơn có, nên
    fallback này chính là ngày hiển thị — đường ghi phải đóng sẵn ngày nghiệp
    vụ vào nó (``real_dispatch_occurred_at``).
    """
    return Coalesce(
        Max(
            "occurred_at",
            filter=Q(movement_type=_SALE) & ~Q(note__in=ADMIN_SALE_NOTES),
        ),
        Max("occurred_at"),
    )


class DispatchHistoryService:
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
        # Kết quả đã giải sẵn của ô tìm ``order`` (xem ``with_resolved_order``).
        # ``None`` = chưa giải, tự giải tại đây; ``[]`` = tìm nhưng không khớp đơn nào.
        order_ids: list[int] | None = None,
        order_id: int | None = None,
        carrier: str = "",
    ) -> QuerySet[StockMovement]:
        qs = StockMovement.objects.filter(
            movement_type__in=[_SALE, _SALE_REVERSAL], reference_type="order"
        )
        if product_ids:
            qs = qs.filter(product_id__in=product_ids)
        if carrier:
            # Subquery (không materialize): tập đơn thuộc ĐVVC có thể rất lớn.
            qs = qs.filter(
                reference_id__in=Order.objects.filter(carrier_q(carrier)).values("id")
            )
        qs = filter_occurred_between(qs, date_from, date_to)
        if warehouse_ids:
            qs = qs.filter(warehouse_id__in=warehouse_ids)
        if product_id:
            qs = qs.filter(product_id=product_id)
        if order_id:
            qs = qs.filter(reference_id=order_id)
        product = (product or "").strip()
        if product:
            qs = qs.filter(
                Q(product__sku__icontains=product) | Q(product__name__icontains=product)
            )
        if order_ids is None:
            order_ids = DispatchHistoryService.resolve_order_ids(order)
        if order_ids is not None:
            qs = qs.filter(reference_id__in=order_ids or [-1])
        return qs

    @staticmethod
    def resolve_order_ids(order: str) -> list[int] | None:
        """Id các đơn khớp ô tìm (mã đơn / hoá đơn / vận đơn / mã đã quét).
        ``None`` = không tìm.

        Tách khỏi ``base_queryset`` để tầng page giải ĐÚNG MỘT LẦN rồi truyền
        xuống mọi query của cùng một lượt dựng bảng: `base_queryset` được gọi ~5
        lần cho một trang, mà đây là phần đắt nhất của bộ lọc.

        Dùng ``__ilike_contains`` chứ không phải ``__icontains``: xem
        ``apps/common/db_lookups.py`` — ``icontains`` sinh ``UPPER(col) LIKE``
        và bỏ qua sạch 3 index GIN trigram đang có trên chính 3 cột này.
        """
        order = (order or "").strip()
        if not order:
            return None
        ids = set(
            Order.objects.filter(
                Q(order_code__ilike_contains=order)
                | Q(invoice_code__ilike_contains=order)
                | Q(shipping_code__ilike_contains=order)
            ).values_list("id", flat=True)
        )
        # Mã QUÉT có thể khác shipping_code của đơn (đổi ĐVVC, dán nhãn lại,
        # khớp tay — ca prod 84859721026220 khớp vào đơn mang mã SPXVN...):
        # dò thêm bảng mã quét đã khớp, không thì tra theo mã trên nhãn kiện
        # ra 0 đơn dù tồn đã trừ. Bảng nhỏ + không có index trigram nên
        # `icontains` (seq scan) là đủ, khỏi cần `ilike_contains`.
        ids.update(
            PendingShipmentScan.objects.filter(
                code__icontains=order, matched_order_id__isnull=False
            ).values_list("matched_order_id", flat=True)
        )
        return list(ids)

    @staticmethod
    def with_resolved_order(filters: dict) -> dict:
        """Giải ô tìm một lần cho cả lượt dựng bảng."""
        if filters.get("order_ids") is not None:
            return filters
        if not (filters.get("order") or "").strip():
            return filters
        return {
            **filters,
            "order_ids": DispatchHistoryService.resolve_order_ids(filters["order"]),
        }

    @staticmethod
    def order_groups(**filters) -> QuerySet:
        """Nhóm movement xuất theo đơn (reference_id), sắp theo thời gian giảm dần.

        Tách riêng để tầng orchestration (trộn với phiếu kho) tái dùng cùng thứ
        tự/aggregate mà không lặp query — xem ``issue_document_history``.
        """
        return (
            DispatchHistoryService.base_queryset(**filters)
            .values("reference_id")
            .annotate(
                sale_sum=Coalesce(Sum("qty_delta", filter=Q(movement_type=_SALE)), 0),
                rev_sum=Coalesce(
                    Sum("qty_delta", filter=Q(movement_type=_SALE_REVERSAL)), 0
                ),
                sku_count=Count("product_id", distinct=True),
                last_at=_dispatch_last_at(),
                # SL hàng thật rời kho — tile tổng và tile "ĐƠN ĐÃ XUẤT" đọc
                # cùng con số này để không nói khác bảng bên dưới.
                issued=_issued_qty_expr(),
                admin_sale=Coalesce(
                    Sum(
                        "qty_delta",
                        filter=Q(movement_type=_SALE) & Q(note__in=ADMIN_SALE_NOTES),
                    ),
                    0,
                ),
            )
            .filter(**_HAS_REAL_SALE)
            # issued = 0 VÀ không có SALE hành chính: mọi bút toán xuất trong kỳ
            # đã bị đảo bởi un-dispatch/resync — hàng thực CHƯA rời kho (bấm nhầm
            # rồi sửa). Không lọc thì bảng liệt kê dòng "SL dương" (display_qty
            # rơi về gross) trong khi tile ĐƠN ĐÃ XUẤT và footer đều bỏ qua — ba
            # con số trên cùng màn hình nói ba kiểu. Đơn `.NN` nhận sổ (chỉ có
            # SALE hành chính, issued = 0 by design) vẫn phải hiện — hàng của nó
            # đã rời kho thật dưới tên đơn gốc.
            .filter(Q(issued__gt=0) | Q(admin_sale__lt=0))
            .order_by("-last_at", "-reference_id")
        )

    @staticmethod
    def enrich_orders(groups: list[dict], **filters) -> list[dict]:
        """Gắn mã đơn/kho/người thực hiện/dòng SKU cho một tập nhóm đơn (1 trang).

        ``groups`` là list dict do :meth:`order_groups` trả (đã cắt trang). Làm
        giàu bằng các query gộp — không N+1."""
        order_ids = [r["reference_id"] for r in groups]
        # Trang hiện tại đã biết chính xác id đơn, chặt hơn ô tìm → truyền thẳng
        # để KHỎI dò lại 3 cột trigram cho mỗi query làm giàu.
        qs = DispatchHistoryService.base_queryset(
            **{**filters, "order_ids": order_ids}
        )
        order_map = {
            o["id"]: o
            for o in Order.objects.filter(id__in=order_ids).values(
                "id", "order_code", "invoice_code", "shipping_code"
            )
        }
        # Nhãn kho + người thực hiện: 1 query gộp (từng là 2 query gần trùng trên
        # cùng tập đơn). Kho = movement mới nhất (last-wins). Người thực hiện =
        # user NON-NULL mới nhất: reversal hệ thống (webhook/cron, created_by=NULL)
        # đến sau không được xoá trắng tên người đã thực xuất — cột trống làm người
        # xem tưởng "không ai thực hiện" (audit 2026-07-15b).
        wh_names: dict[int, str] = {}
        performer_names: dict[int, str] = {}
        for rid, wh_name, disp, uname in (
            StockMovement.objects.filter(
                reference_type="order", reference_id__in=order_ids
            )
            .order_by("created_at", "id")
            .values_list(
                "reference_id",
                "warehouse__name",
                "created_by__display_name",
                "created_by__username",
            )
        ):
            wh_names[rid] = wh_name
            performer = disp or uname or ""
            if performer:
                performer_names[rid] = performer
        # Dòng SKU của các đơn trong TRANG hiện tại — 1 query gộp, không N+1.
        lines_map: dict[int, list[dict]] = {}
        line_rows = (
            qs.filter(reference_id__in=order_ids)
            .values("reference_id", "product_id", "product__sku")
            .annotate(
                sale_sum=Coalesce(Sum("qty_delta", filter=Q(movement_type=_SALE)), 0),
                rev_sum=Coalesce(
                    Sum("qty_delta", filter=Q(movement_type=_SALE_REVERSAL)), 0
                ),
                combo_moved=_combo_moved_expr(),
            )
        )
        for r in line_rows:
            # Cùng luật với dòng SKU (`sku_groups`): phần đã chuyển sổ sang mã lẻ
            # không còn là sản lượng của chính SKU combo — hàng đã được ghi dưới
            # các mã lẻ, mỗi mã lẻ đã có dòng riêng ngay trong đơn này.
            gross = -r["sale_sum"] - r["combo_moved"]
            # Chuyển sổ HẾT (gross <= 0) thì bỏ hẳn dòng combo. Không bỏ thì
            # `display_qty` rơi về `gross` cho dòng net = 0 và file Excel còn nở
            # dòng đó ra chính các mã lẻ → mỗi mã hiện hai lần (ca prod 20/08).
            # Điều kiện gác bằng `combo_moved` để KHÔNG đụng các dòng đảo khác
            # (bỏ đánh dấu đã xuất, huỷ đơn ở kỳ khác) vốn vẫn hiện như cũ.
            if r["combo_moved"] and gross <= 0:
                continue
            lines_map.setdefault(r["reference_id"], []).append(
                {
                    "product_id": r["product_id"],
                    "sku": r["product__sku"],
                    "qty": -r["sale_sum"] - r["rev_sum"],
                    # SL đã TỪNG rời kho, chưa trừ phần hoàn/chuyển sổ. Đơn huỷ đã
                    # chuyển sổ sang đơn `.NN` có `qty` = 0 — màn Lịch sử nhập xuất
                    # không hiện số 0 nên cần con số dương này để thay thế.
                    "gross": gross,
                }
            )
        for rows in lines_map.values():
            rows.sort(key=lambda x: (-x["qty"], x["sku"] or ""))
        # Mã kho THỰC QUÉT khác mã trên đơn (kiện bảo hành gửi lại bằng vận đơn
        # mới, đổi ĐVVC, dán nhãn lại → khớp tay). Ca prod 84859795173645
        # (16/09/2026): dòng đơn chỉ in mã cũ nên tra mã trên nhãn kiện trong
        # Excel ra 0 dù tồn đã trừ. Chỉ lấy MATCHED chưa thu hồi: FAILED/CANCELLED
        # cũng có `matched_order` nhưng không phải lần xuất kho nào. Mã trùng
        # shipping/invoice của đơn (ca thường) bỏ qua để không in đôi.
        scan_codes_map: dict[int, list[str]] = {}
        for oid, code in (
            PendingShipmentScan.objects.filter(
                kind=PendingScanKind.DISPATCH,
                status=PendingScanStatus.MATCHED,
                voided_at__isnull=True,
                matched_order_id__in=order_ids,
            )
            .order_by("created_at", "id")
            .values_list("matched_order_id", "code")
        ):
            o = order_map.get(oid, {})
            if code in (o.get("shipping_code"), o.get("invoice_code")):
                continue
            bucket = scan_codes_map.setdefault(oid, [])
            if code not in bucket:
                bucket.append(code)
        items = []
        for r in groups:
            oid = r["reference_id"]
            o = order_map.get(oid, {})
            dispatched = -r["sale_sum"]
            reversed_qty = r["rev_sum"]
            lines = lines_map.get(oid, [])
            items.append(
                {
                    "order_id": oid,
                    "order_code": o.get("order_code"),
                    "invoice_code": o.get("invoice_code") or None,
                    "shipping_code": o.get("shipping_code"),
                    "scan_codes": scan_codes_map.get(oid, []),
                    "warehouse_name": wh_names.get(oid),
                    "performed_by": performer_names.get(oid, ""),
                    "lines": lines,
                    # Đếm theo chính các dòng còn lại: `sku_count` gộp ở tầng
                    # nhóm vẫn tính cả SKU combo đã chuyển sổ, làm ô "Số SKU"
                    # nói 3 trong khi đơn chỉ còn 2 mã lẻ. Giữ số cũ khi không
                    # có dòng nào (đơn `.NN` chỉ nhận sổ) để không ra 0.
                    "sku_count": len(lines) or r["sku_count"],
                    "dispatched": dispatched,
                    "reversed": reversed_qty,
                    "net": dispatched - reversed_qty,
                    "last_at": r["last_at"],
                }
            )
        return items

    @staticmethod
    def total_issued(**filters) -> int:
        """Tổng SL xuất (net) theo đơn trên TOÀN BỘ bộ lọc (không chỉ 1 trang).

        Gộp theo đơn rồi mới cộng, thay vì cộng thẳng mọi bút toán: bút toán đảo
        của đơn KHÔNG xuất trong khoảng lọc (huỷ đơn ở ngày khác) phải bị loại,
        nếu không tile "Tổng SL xuất" của ngày huỷ ra số âm (xem ``_HAS_REAL_SALE``).

        Cộng theo SL hàng THẬT rời kho (``_issued_qty_expr``), không phải net:
        đơn xuất rồi bị huỷ có tồn về 0 nhưng hàng đã đi thật, vẫn phải ghi nhận
        là một lần xuất (ca prod HDTTS_585205879163881436).
        """
        groups = (
            DispatchHistoryService.base_queryset(**filters)
            .values("reference_id")
            .annotate(
                sale_sum=Coalesce(Sum("qty_delta", filter=Q(movement_type=_SALE)), 0),
                issued=_issued_qty_expr(),
            )
            .filter(**_HAS_REAL_SALE)
        )
        return groups.aggregate(s=Coalesce(Sum("issued"), 0))["s"] or 0

    @staticmethod
    def orders_page(*, pagination: dict, **filters) -> dict:
        """Danh sách chính tab Xuất: gộp theo đơn hàng (reference_id)."""
        filters = DispatchHistoryService.with_resolved_order(filters)
        grouped = DispatchHistoryService.order_groups(**filters)
        # Đếm trên chính tập đã lọc — `base_queryset` còn cả đơn chỉ có bút toán
        # đảo trong kỳ, đếm thẳng ở đó sẽ ra tổng lớn hơn số dòng thật sự liệt kê.
        total = grouped.count()
        offset = pagination["offset"]
        page = list(grouped[offset : offset + pagination["limit"]])
        items = DispatchHistoryService.enrich_orders(page, **filters)
        return {
            "items": items,
            "total": total,
            "total_quantity": DispatchHistoryService.total_issued(**filters),
            "limit": pagination["limit"],
            "offset": offset,
        }

    @staticmethod
    def _pair_dispatched_in_window(**filters) -> Exists:
        """Cặp (đơn, SKU) của dòng ngoài có bút toán XUẤT thật trong kỳ lọc?

        `sku_groups` gộp theo product nên `_HAS_REAL_SALE` ở tầng nhóm chỉ hỏi
        "SKU này có lần xuất nào trong kỳ", không hỏi từng đơn. Đơn xuất NGÀY
        KHÁC mà kỳ lọc chỉ chứa bút toán đảo (huỷ đơn/chuyển sổ) vì thế vẫn lọt
        vào dòng SKU: vừa +1 vào "Số đơn" vừa −1 vào "SL xuất", trong khi phần
        bung dòng (``sku_orders``) đã loại nó theo luật per-đơn — ca prod BNC02
        12/8 hiện "11 đơn · 9 chiếc" cho 10 đơn × 1.

        Điều kiện đặt ở tầng WHERE (semi-join, Postgres gộp được với index
        ``stockmove_ref_idx``) chứ không phải HAVING theo cặp: rẻ và đủ để bỏ
        các bút toán đảo mồ côi.
        """
        return Exists(
            DispatchHistoryService.base_queryset(**filters).filter(
                reference_id=OuterRef("reference_id"),
                product_id=OuterRef("product_id"),
                movement_type=_SALE,
                qty_delta__lt=0,
            )
        )

    @staticmethod
    def sku_groups(**filters) -> QuerySet:
        """Nhóm movement xuất theo product, sắp theo thời gian giảm dần.

        Tách riêng để tầng orchestration (trộn phiếu kho theo SKU) tái dùng."""
        # Giải ô tìm MỘT lần rồi truyền xuống cả query ngoài lẫn subquery
        # ``Exists`` — không thì mỗi lần gọi dò lại 3 cột trigram thêm một lượt.
        filters = DispatchHistoryService.with_resolved_order(filters)
        return (
            DispatchHistoryService.base_queryset(**filters)
            .filter(DispatchHistoryService._pair_dispatched_in_window(**filters))
            .values("product_id", "product__sku", "product__name")
            .annotate(
                sale_sum=Coalesce(Sum("qty_delta", filter=Q(movement_type=_SALE)), 0),
                rev_sum=Coalesce(
                    Sum("qty_delta", filter=Q(movement_type=_SALE_REVERSAL)), 0
                ),
                order_count=Count("reference_id", distinct=True),
                # Cùng thước đo với tile "Tổng SL xuất" và tab Theo đơn — xem
                # ``_issued_qty_expr``.
                issued=_issued_qty_expr(),
                combo_moved=_combo_moved_expr(),
                last_at=_dispatch_last_at(),
            )
            .filter(**_HAS_REAL_SALE)
            # SKU combo đã chuyển sổ HẾT sang mã lẻ không còn là một dòng xuất:
            # hàng đã được ghi nhận dưới các mã lẻ rồi. Combo chuyển sổ MỘT PHẦN
            # (khai thành phần muộn, phần cũ chưa chuyển) vẫn hiện phần còn lại.
            .exclude(sale_sum__gte=Value(0) - F("combo_moved"))
            .order_by("-last_at", "-product_id")
        )

    @staticmethod
    def _sku_item(r: dict) -> dict:
        return {
            "product_id": r["product_id"],
            "sku": r["product__sku"],
            "name": r["product__name"],
            "order_count": r["order_count"],
            # `dispatched` = SL đã TỪNG rời kho (gộp, luôn dương) — FE/Excel lấy
            # làm giá trị thay thế khi `net` = 0 (đơn `.NN` chỉ nhận sổ). Trừ phần
            # đã chuyển sổ sang mã lẻ, nếu không dòng combo còn sót lại một phần
            # sẽ báo cả phần đã tính ở mã lẻ.
            "dispatched": -r["sale_sum"] - r.get("combo_moved", 0),
            "reversed": r["rev_sum"],
            # KHÔNG phải `dispatched - reversed`: đảo sổ SAU khi hàng đã rời kho
            # (huỷ đơn, chuyển sổ sang đơn xuất lại) không xoá đi một lần xuất
            # đã xảy ra. Cộng thẳng net thì dòng SKU nói ít hơn cả tile lẫn phần
            # bung dòng của chính nó.
            "net": r["issued"],
            "last_at": r["last_at"],
        }

    @staticmethod
    def skus_page(*, pagination: dict, **filters) -> dict:
        """Danh sách chính view Theo SKU tab Xuất: gộp theo product (mọi đơn)."""
        filters = DispatchHistoryService.with_resolved_order(filters)
        grouped = DispatchHistoryService.sku_groups(**filters)
        total = grouped.count()
        offset = pagination["offset"]
        page = list(grouped[offset : offset + pagination["limit"]])
        items = [DispatchHistoryService._sku_item(r) for r in page]
        return {
            "items": items,
            "total": total,
            "total_quantity": DispatchHistoryService.total_issued(**filters),
            "limit": pagination["limit"],
            "offset": offset,
        }

    @staticmethod
    def sku_orders(*, product_id: int, **filters) -> list[dict]:
        """Drill-down: các đơn chứa một SKU (net = dispatched − reversed)."""
        filters.pop("product_id", None)
        filters.pop("product", None)
        qs = DispatchHistoryService.base_queryset(product_id=product_id, **filters)
        grouped = (
            qs.values("reference_id")
            .annotate(
                sale_sum=Coalesce(Sum("qty_delta", filter=Q(movement_type=_SALE)), 0),
                rev_sum=Coalesce(
                    Sum("qty_delta", filter=Q(movement_type=_SALE_REVERSAL)), 0
                ),
                issued=_issued_qty_expr(),
                admin_sale=Coalesce(
                    Sum(
                        "qty_delta",
                        filter=Q(movement_type=_SALE) & Q(note__in=ADMIN_SALE_NOTES),
                    ),
                    0,
                ),
                last_at=_dispatch_last_at(),
            )
            .filter(**_HAS_REAL_SALE)
            # Cùng luật với ``order_groups``: đơn bị bỏ đánh dấu xuất kho / sửa
            # items sau xuất (đảo hết, không có SALE hành chính) là thao tác sửa
            # sai, hàng chưa từng rời kho — tab Theo đơn đã ẩn, phần bung dòng
            # của view Theo SKU phải ẩn theo.
            .filter(Q(issued__gt=0) | Q(admin_sale__lt=0))
        )
        order_ids = [r["reference_id"] for r in grouped]
        order_map = {
            o["id"]: o
            for o in Order.objects.filter(id__in=order_ids).values(
                "id", "order_code", "invoice_code", "shipping_code"
            )
        }
        wh_names = dict(
            StockMovement.objects.filter(
                reference_type="order", reference_id__in=order_ids
            )
            .order_by("created_at", "id")
            .values_list("reference_id", "warehouse__name")
        )
        rows = []
        for r in grouped:
            oid = r["reference_id"]
            o = order_map.get(oid, {})
            dispatched = -r["sale_sum"]
            reversed_qty = r["rev_sum"]
            rows.append(
                {
                    "order_id": oid,
                    "order_code": o.get("order_code"),
                    "invoice_code": o.get("invoice_code") or None,
                    "shipping_code": o.get("shipping_code"),
                    "warehouse_name": wh_names.get(oid),
                    "dispatched": dispatched,
                    "net": dispatched - reversed_qty,
                    "last_at": r["last_at"],
                }
            )
        rows.sort(key=lambda x: x["net"], reverse=True)
        return rows
