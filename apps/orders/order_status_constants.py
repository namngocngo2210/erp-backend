"""Hằng số trạng thái đơn/hóa đơn dùng chung trong app `orders`.

Cùng lý do tồn tại với `apps/customers/order_status_constants.py`: một nguồn sự
thật để các service khỏi tự định nghĩa lại magic number.

`invoice_status` là cột của CHÍNH `orders.Order`; giá trị của nó bắt nguồn từ mã
trạng thái hóa đơn KiotViet. Từ 2026-09-23 mã này khai MỘT lần ở
`apps/common/kiotviet_constants.py` (tầng đáy, app nào cũng import được) — module
này re-export để đường import cũ trong orders/daily_report không đổi.
"""

# Hóa đơn đã hủy (so với `Order.invoice_status`, KHÔNG phải `Order.status`).
from apps.common.kiotviet_constants import CANCELLED_INVOICE_STATUS  # noqa: F401  (re-export)
