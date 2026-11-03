"""Business-wide constants shared across apps.

Kept in ``apps.common`` (lowest layer) so higher-layer apps (orders, customers,
inventory, ...) can share them without importing each other.
"""

# KiotViet branch ids hidden from every report/list (internal/test branches).
EXCLUDED_BRANCH_IDS = ("1000000139",)

# Nguồn đơn SÀN TMĐT (`Order.order_source`). Bản sao ở tầng đáy của
# ``apps.orders.models.MARKETPLACE_ORDER_SOURCES`` — nguồn sự thật nghiệp vụ vẫn là
# ``INVOICE_CODE_SOURCE_PREFIXES`` bên orders, hai bên được khoá khớp nhau bằng
# ``apps/orders/tests/test_customer_phone_masking.py``.
#
# Vì sao cần bản sao: luật che SĐT (xem apps/users/phone_visibility.py) phải MIỄN cho
# đơn sàn, mà nơi áp gồm cả consumer WebSocket của ``channels_core`` — app đó nằm DƯỚI
# orders trong contract phân tầng nên không được import lên.
MARKETPLACE_ORDER_SOURCE_CODES = frozenset({"shopee", "tiktok"})
