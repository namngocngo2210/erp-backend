"""Xếp hạng sản phẩm theo doanh thu gần đây — dùng để chọn biến thể khi nhận dạng SKU.

Sống ở ``apps.orders`` vì phải đọc ``OrderItem``: ``apps.catalog`` nằm DƯỚI orders nên
không được import ngược lên. Caller (task/command của app quảng cáo, vốn nằm TRÊN orders)
lấy bảng xếp hạng ở đây rồi truyền xuống cho parser trong catalog.
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.orders.models import OrderItem

# 90 ngày: đủ dài để một biến thể bán chậm không thắng nhờ một tuần may mắn, đủ ngắn để
# hàng đã ngừng bán không còn giữ ngôi.
DEFAULT_WINDOW_DAYS = 90


def product_revenue_rank(days: int = DEFAULT_WINDOW_DAYS) -> dict[int, Decimal]:
    """``{product_id: doanh thu trong `days` ngày}``. Sản phẩm không bán được không có mặt.

    Dùng để trả lời câu "mã `TMS01` trong tên chiến dịch là biến thể nào": danh mục có
    `TMS01` (SKU rác, tên đúng bằng mã, 1,7tr/90 ngày), `TMS01-PRO` và `TMS01-PROMAX`
    (47,5 tỉ/90 ngày). Khớp SKU đầy đủ sẽ chọn đúng cái rác; xếp theo doanh thu chọn
    đúng cái phòng Ads đang chạy.
    """
    since = timezone.now() - timedelta(days=days)
    rows = (
        OrderItem.objects.filter(order__created_at__gte=since, order__deleted_at__isnull=True)
        .values("product_id")
        .annotate(revenue=Sum("line_total"))
    )
    return {
        row["product_id"]: (row["revenue"] or Decimal("0"))
        for row in rows
        if row["product_id"] is not None
    }
