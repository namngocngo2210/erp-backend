from __future__ import annotations

from datetime import date, datetime, timedelta

from django.db.models import Q, QuerySet, Sum
from django.utils import timezone

from apps.inventory.application.services.stock_as_of_service import StockAsOfService
from apps.inventory.models import MovementType, StockItem, StockMovement, StockState
from apps.catalog.models import Product


def _stock_items_base() -> QuerySet[StockItem]:
    """Hợp nhất từ `apps.inventory.repositories.StockItemRepository.base` (stub 23
    dòng, chỉ service này dùng — audit T3/P2-3): join sẵn kho + sản phẩm."""
    return StockItem.objects.select_related("warehouse", "product")


def _stock_movements_base() -> QuerySet[StockMovement]:
    """Hợp nhất từ `apps.inventory.repositories.StockMovementRepository.base`."""
    return StockMovement.objects.select_related("warehouse", "product", "created_by")


class StockQueryService:
    @staticmethod
    def list_stock(
        *, warehouse_id: int | None = None, search: str = "", state: str | None = None
    ) -> QuerySet[StockItem]:
        qs = _stock_items_base()
        if warehouse_id:
            qs = qs.filter(warehouse_id=warehouse_id)
        if state:
            qs = qs.filter(state=state)
        search = (search or "").strip()
        if search:
            qs = qs.filter(Q(product__sku__icontains=search) | Q(product__name__icontains=search))
        return qs.order_by("product__sku", "warehouse__name", "id")

    @staticmethod
    def availability(*, warehouse_id: int, skus: list[str]) -> list[dict]:
        """Tồn BÁN ĐƯỢC của từng SKU tại MỘT kho — phục vụ form đơn hàng.

        Chỉ cộng state `sellable`: hàng bảo hành/lỗi không bán được, gộp vào là
        cho sale một con số họ không thực sự xuất được.

        Không N+1 theo số SKU: 1 lấy sản phẩm theo SKU, 1 gộp tồn theo
        product_id, cộng thêm các query BATCH (không lặp theo từng dòng) của
        `_pending_direct_by_product` cho `pending_direct` — xem docstring hàm
        đó. SKU có sản phẩm nhưng chưa có dòng tồn ở kho đó trả về 0 chứ không
        biến mất — "không có dòng tồn" chính là tồn 0, im lặng bỏ đi thì giao
        diện không phân biệt được với "chưa tải xong".

        Thứ tự trả về bám đúng thứ tự `skus` client gửi lên (thứ tự dòng hàng
        trên form), nên giao diện không phải sắp lại.
        """
        if not skus:
            return []
        products = list(Product.objects.filter(sku__in=skus).values("id", "sku", "name"))
        if not products:
            return []
        product_ids = [row["id"] for row in products]
        qty_by_product = dict(
            StockItem.objects.filter(
                warehouse_id=warehouse_id,
                product_id__in=product_ids,
                state=StockState.SELLABLE,
            )
            .values_list("product_id")
            .annotate(total=Sum("quantity"))
            .values_list("product_id", "total")
        )
        pending_by_product = StockQueryService._pending_direct_by_product(
            warehouse_id=warehouse_id, product_ids=product_ids
        )
        rank = {sku: index for index, sku in enumerate(skus)}
        rows = [
            {
                "sku": row["sku"],
                "product_name": row["name"],
                "sellable": int(qty_by_product.get(row["id"]) or 0),
                "pending_direct": int(pending_by_product.get(row["id"]) or 0),
            }
            for row in products
        ]
        rows.sort(key=lambda row: rank.get(row["sku"], len(rank)))
        return rows

    @staticmethod
    def _pending_direct_by_product(*, warehouse_id: int, product_ids: list[int]) -> dict[int, int]:
        """SL đã lên hoá đơn nhưng sổ chưa trừ tồn của đơn bán trực tiếp "xuất sau", quy về MÃ LẺ.

        Đơn "xuất sau" đã có hoá đơn KiotViet nhưng sổ CHƯA trừ tồn (worker cố ý bỏ
        qua bước trừ tồn lúc tạo, xem `manual_dispatch_service.py`), nên `sellable`
        ở trên vẫn đang tính đúng số hàng đó là còn bán được — hệ thống cố ý không
        có khái niệm giữ chỗ trong sổ. Con số này chỉ để form đơn CẢNH BÁO thêm
        "đang có N cái chờ xuất" cạnh `sellable`, không phải để khoá hay trừ vào nó.

        Loại đơn đã xuất bằng đúng `_dispatched_order_ids`: cờ
        `direct_dispatch_deferred` không tự dọn sau khi kho bấm xác nhận (nó nói
        "đơn đi theo luật nào", không nói "đã xuất chưa"), tự chế định nghĩa thứ
        hai ở đây sẽ đếm trùng vĩnh viễn ngay khi hai định nghĩa lệch nhau.

        Nở combo là bắt buộc: sổ trừ tồn theo mã lẻ (`expand_desired_quantities`),
        cộng "đang chờ xuất" theo SKU combo sẽ lệch đúng ở nhóm đơn dễ sai nhất
        (bài học MR !1317/!1326).
        """
        from apps.inventory.application.services.combo_expansion import ComboExpander
        from apps.inventory.application.services.manual_dispatch_service import (
            _dispatched_order_ids,
        )
        from apps.orders.models import OrderItem, OrderStatus, ShippingType

        lines = list(
            OrderItem.objects.filter(
                order__shipping_type=ShippingType.DIRECT,
                order__direct_dispatch_deferred=True,
                order__fulfillment_warehouse_id=warehouse_id,
                # `Order.objects` không tự lọc deleted_at — quy ước app inventory là lọc
                # TAY (xem manual_dispatch_service.py). Đơn xoá mềm vẫn thoả các điều
                # kiện trên (xoá hàng loạt chỉ update(deleted_at=now), không đổi các cờ
                # này) nên phải loại tường minh, kẻo "đang chờ xuất" đếm cả đơn đã xoá.
                order__deleted_at__isnull=True,
                product_id__isnull=False,
                quantity__gt=0,
            )
            .exclude(order__status=OrderStatus.CANCELLED)
            .values("order_id", "product_id", "quantity")
        )
        if not lines:
            return {}

        dispatched = _dispatched_order_ids(list({row["order_id"] for row in lines}))
        if dispatched:
            lines = [row for row in lines if row["order_id"] not in dispatched]
        if not lines:
            return {}

        expander = ComboExpander.for_product_ids({row["product_id"] for row in lines})
        wanted = set(product_ids)
        totals: dict[int, int] = {}
        for row in lines:
            components = expander.components_of(row["product_id"])
            if components:
                for component in components:
                    pid = component["product_id"]
                    if pid in wanted:
                        totals[pid] = totals.get(pid, 0) + row["quantity"] * component["quantity"]
            elif row["product_id"] in wanted:
                totals[row["product_id"]] = totals.get(row["product_id"], 0) + row["quantity"]
        return totals

    @staticmethod
    def pivot_stock(
        items: list[StockItem], run_rates: dict[tuple[int, int], dict[str, float]]
    ) -> list[dict]:
        """Gộp các dòng StockItem theo (kho, mã) thành 1 dòng, state thành cột.

        Cùng phép pivot mà bảng web làm ở client (`buildPivot`); đặt ở service để
        file Excel dùng chung nguồn dữ liệu với màn hình. Giữ nguyên thứ tự `items`
        (đã sắp theo SKU ở `list_stock`).
        """
        rows: dict[tuple[int, int], dict] = {}
        for item in items:
            key = (item.warehouse_id, item.product_id)
            row = rows.get(key)
            if row is None:
                rate = run_rates.get(key) or {}
                row = {
                    "warehouse_name": item.warehouse.name,
                    "product_sku": item.product.sku,
                    "product_name": item.product.name,
                    StockState.SELLABLE: 0,
                    StockState.WARRANTY: 0,
                    StockState.DEFECT: 0,
                    "total": 0,
                    # RR3/RR7 tính theo (kho, mã) — mọi dòng state mang cùng giá trị.
                    "rr3": rate.get("rr3", 0.0),
                    "rr7": rate.get("rr7", 0.0),
                }
                rows[key] = row
            # State mới thêm sau này (chưa có cột riêng) vẫn vào Tổng thay vì mất hút.
            if item.state in row:
                row[item.state] += item.quantity
            row["total"] += item.quantity
        return list(rows.values())

    @staticmethod
    def sales_run_rates(
        pairs: set[tuple[int, int]],
        *,
        as_of_end: datetime | None = None,
    ) -> dict[tuple[int, int], dict[str, float]]:
        """RR3/RR7: số bán trung bình mỗi ngày trong 3/7 ngày gần nhất, theo (kho, mã).

        Đọc net SALE + SALE_REVERSAL từ sổ movement (cùng quy ước outstanding) —
        các cặp bút toán hành chính cùng cửa sổ (chuyển sổ đơn anh em, huỷ→hồi
        sinh) tự triệt tiêu. Reversal của đơn bán TRƯỚC cửa sổ có thể kéo net
        âm → clamp về 0.

        `as_of_end` dời điểm KẾT THÚC cửa sổ về một mốc quá khứ (màn hình xem tồn
        theo ngày): xem lại ngày D thì phải thấy tốc độ bán của ngày D, không
        phải của hôm nay. Chỉ khi đó mới thêm cận trên `occurred_at < end` —
        đường mặc định giữ nguyên hành vi cũ, không thêm điều kiện nào.
        """
        if not pairs:
            return {}
        end = as_of_end or timezone.now()
        since_3d = end - timedelta(days=3)
        since_7d = end - timedelta(days=7)
        qs = StockMovement.objects.filter(
            movement_type__in=[MovementType.SALE, MovementType.SALE_REVERSAL],
            # Tốc độ bán tính theo ngày hàng RỜI KHO. `created_at` đi kèm chỉ
            # để cắt partition — hợp lệ nhờ bất biến occurred_at <= created_at
            # (xem movement_window).
            occurred_at__gte=since_7d,
            created_at__gte=since_7d,
            warehouse_id__in={wh for wh, _ in pairs},
            product_id__in={prod for _, prod in pairs},
        )
        if as_of_end is not None:
            # Cận TRÊN không có điều kiện `created_at` đối xứng: bút toán ghi sổ
            # trễ có created_at > occurred_at, thêm vào là loại nhầm đúng những
            # dòng cần đếm (xem docstring movement_window).
            qs = qs.filter(occurred_at__lt=end)
        rows = qs.values("warehouse_id", "product_id").annotate(
            net_7d=Sum("qty_delta"),
            net_3d=Sum("qty_delta", filter=Q(occurred_at__gte=since_3d)),
        )
        rates: dict[tuple[int, int], dict[str, float]] = {}
        for row in rows:
            key = (row["warehouse_id"], row["product_id"])
            if key not in pairs:
                continue
            sold_3d = max(0, -(row["net_3d"] or 0))
            sold_7d = max(0, -(row["net_7d"] or 0))
            rates[key] = {"rr3": round(sold_3d / 3, 2), "rr7": round(sold_7d / 7, 2)}
        return rates

    @staticmethod
    def list_movements(
        *,
        warehouse_id: int | None = None,
        product_id: int | None = None,
        as_of: date | None = None,
    ) -> QuerySet[StockMovement]:
        qs = _stock_movements_base()
        if warehouse_id:
            qs = qs.filter(warehouse_id=warehouse_id)
        if product_id:
            qs = qs.filter(product_id=product_id)
        if as_of is not None:
            # Thẻ sổ phải dừng đúng ở mốc mà bảng tồn đang hiển thị — nếu không,
            # người dùng thấy tồn ngày 05/08 mà bên dưới liệt kê bút toán 10/08.
            # KHÔNG kèm `created_at` ở cận trên: bút toán ghi sổ trễ có
            # created_at > occurred_at, thêm vào là loại nhầm chính chúng.
            qs = qs.filter(occurred_at__lt=StockAsOfService.end_bound(as_of))
        return qs.order_by("-created_at", "-id")

    @staticmethod
    def resolve_reference_labels(movements) -> dict[tuple[str, int], str]:
        from apps.inventory.models import ReturnEntry, StockDocument
        from apps.orders.models import Order

        ids_by_type: dict[str, set[int]] = {}
        for m in movements:
            if m.reference_type and m.reference_id:
                ids_by_type.setdefault(m.reference_type, set()).add(m.reference_id)

        labels: dict[tuple[str, int], str] = {}

        order_ids = ids_by_type.get("order")
        if order_ids:
            for row in Order.objects.filter(id__in=order_ids).values(
                "id", "order_code", "invoice_code"
            ):
                labels[("order", row["id"])] = (
                    row["order_code"] or row["invoice_code"] or f"#{row['id']}"
                )

        doc_ids = ids_by_type.get("document")
        if doc_ids:
            for row in StockDocument.objects.filter(id__in=doc_ids).values("id", "code"):
                labels[("document", row["id"])] = row["code"] or f"#{row['id']}"

        # Movement của hàng hoàn trỏ NHÓM ĐƠN (return_entry); nhãn = mã phiếu ngày
        # + mã vận đơn để truy vết được về đúng đơn trong phiếu gom.
        # Không còn nhánh "return" cũ: migration 0022_return_entry đã trỏ lại toàn bộ movement
        # lịch sử sang return_entry, và không code path nào xoá cứng ReturnNote.
        entry_ids = ids_by_type.get("return_entry")
        if entry_ids:
            for row in ReturnEntry.objects.filter(id__in=entry_ids).values(
                "id", "return_note__code", "shipping_code"
            ):
                label = row["return_note__code"] or f"#{row['id']}"
                if row["shipping_code"]:
                    label = f"{label} / {row['shipping_code']}"
                labels[("return_entry", row["id"])] = label

        return labels
