"""Order-status constants shared across the customer services.

Single source of truth so the loyalty / resale / profile services don't each
redefine (or import from each other) these magic numbers.
"""

# Đơn "giao thành công" — đồng bộ cách tính doanh thu trong báo cáo
# (apps/orders/_repositories_reports.py: revenue = Sum(total_after_discount, status=3)).
DELIVERED_SUCCESS_ORDER_STATUS = 3

# Hóa đơn đã hủy (invoice_status=2) — loại khỏi "đơn giao thành công".
# Nguồn duy nhất ở apps/common/kiotviet_constants.py (2026-09-23); re-export để
# đường import cũ trong customers không đổi.
from apps.common.kiotviet_constants import CANCELLED_INVOICE_STATUS  # noqa: E402, F401  (re-export)
