"""Dựng lại tồn kho tại một MỐC NGÀY từ sổ cái append-only.

Cách làm: TRỪ NGƯỢC từ `stock_items` (vốn là projection của toàn bộ
`stock_movements`, xem `management/commands/rebuild_stock_items.py`):

    tồn(D) = stock_items.quantity − Σ qty_delta của movement có occurred_at ≥ 00:00 ngày D+1

Vì sao không cộng xuôi từ đầu sổ (`Σ qty_delta WHERE occurred_at < end`)? Bảng
`stock_movements` RANGE-partition theo THÁNG trên `created_at`, nên cộng xuôi
luôn quét toàn bộ partition lịch sử — kể cả khi người dùng chỉ xem hôm qua, tức
ca dùng phổ biến nhất. Trừ ngược thì càng gần hiện tại càng ít partition.

Điều kiện `created_at >= end` đi kèm là mẹo cắt partition, KHÔNG đổi kết quả:
nhờ bất biến `occurred_at <= created_at` (ép ở `StockCommandService`), mọi dòng
bị nó loại thêm đều có `created_at < end` ⇒ `occurred_at < end` ⇒ đằng nào cũng
bị loại. Luật đầy đủ ở `movement_window` — module đó là NƠI DUY NHẤT giữ luật,
đừng chép sang đây.

Service này CHỈ ĐỌC. Sổ cái append-only, tính năng tra cứu không được ghi gì.
"""
from __future__ import annotations

from datetime import date, datetime

from django.db.models import Sum

from apps.inventory.application.services.movement_window import local_day_bounds
from apps.inventory.models import StockItem, StockMovement


class StockAsOfService:
    @staticmethod
    def end_bound(as_of: date) -> datetime:
        """Cận trên nửa mở: 00:00 giờ VN của ngày `as_of + 1`."""
        _, end = local_day_bounds(date_to=as_of)
        return end

    @staticmethod
    def deltas_after(
        as_of: date,
        *,
        warehouse_id: int | None = None,
        product_ids: set[int] | None = None,
    ) -> dict[tuple[int, int, str], int]:
        """Tổng biến động SAU mốc, gộp theo (kho, mã, trạng thái)."""
        end = StockAsOfService.end_bound(as_of)
        qs = StockMovement.objects.filter(occurred_at__gte=end, created_at__gte=end)
        if warehouse_id:
            qs = qs.filter(warehouse_id=warehouse_id)
        if product_ids is not None:
            qs = qs.filter(product_id__in=product_ids)
        rows = qs.values("warehouse_id", "product_id", "state").annotate(
            delta=Sum("qty_delta")
        )
        return {
            (r["warehouse_id"], r["product_id"], r["state"]): r["delta"] or 0
            for r in rows
        }

    @staticmethod
    def apply(
        items: list[StockItem],
        as_of: date,
        *,
        warehouse_id: int | None = None,
    ) -> list[StockItem]:
        """Ghi đè `quantity` của từng item = tồn tại mốc, bỏ cặp (kho, mã) toàn 0.

        Ghi đè TRONG BỘ NHỚ để `StockItemSerializer` (một ModelSerializer) dùng
        lại được nguyên xi. Các instance trả về KHÔNG được `.save()` ở bất cứ
        đâu — làm thế là ghi số quá khứ đè lên tồn hiện tại.

        Cặp (kho, mã) chỉ bị loại khi TẤT CẢ các dòng trạng thái của nó = 0 tại
        mốc: sản phẩm chưa nhập về thời điểm đó không nên chiếm chỗ trong bảng.
        KHÔNG được xét bằng TỔNG các trạng thái — `StockItem.quantity` được phép
        âm (bán vượt hợp lệ, xem `models.py`), nên một cặp có thể có
        SELLABLE=+5, DEFECT=−5 (tổng 0) mà vẫn còn 5 chiếc bán được thật; cộng
        tổng rồi so 0 sẽ giấu mất tồn đó. Loại theo CẶP (giữ hoặc bỏ cả 3 dòng
        cùng lúc), không loại lẻ từng dòng trạng thái — bảng web pivot ba trạng
        thái thành một dòng.
        """
        if not items:
            return []
        deltas = StockAsOfService.deltas_after(
            as_of,
            warehouse_id=warehouse_id,
            product_ids={item.product_id for item in items},
        )
        nonzero_pairs: set[tuple[int, int]] = set()
        for item in items:
            key = (item.warehouse_id, item.product_id)
            item.quantity -= deltas.get((*key, item.state), 0)
            if item.quantity != 0:
                nonzero_pairs.add(key)
        return [
            item for item in items if (item.warehouse_id, item.product_id) in nonzero_pairs
        ]
