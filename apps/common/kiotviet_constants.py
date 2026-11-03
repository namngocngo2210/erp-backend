"""Mã trạng thái KiotViet mà NHIỀU app cùng đọc — một nguồn sự thật.

Trước 2026-09-23 ``CANCELLED_INVOICE_STATUS = 2`` được khai lại ở 4 nơi (orders,
customers, kiotviet, floating_pool) vì các app đó cùng tầng hoặc không được import
nhau (.importlinter). ``common`` là tầng đáy nên mọi app import được; các module
cũ vẫn re-export để đường import hiện có không đổi.

PHÂN BIỆT với ``apps.orders.models.OrderStatus`` (trạng thái GIAO HÀNG của đơn,
``Order.status``): ``OrderStatus.CANCELLED = 6`` là "hủy giao", còn hằng dưới đây
so với ``Order.invoice_status`` = trạng thái HÓA ĐƠN KiotViet.
"""

# KiotViet invoice ``status``: 1 = hoàn thành, 2 = đã hủy, 3 = đang xử lý.
COMPLETED_INVOICE_STATUS = 1
CANCELLED_INVOICE_STATUS = 2
PROCESSING_INVOICE_STATUS = 3
