"""Mệnh đề GTC dùng chung cho mọi matview trong schema `dwh`.

Sinh từ hằng số nguồn (`EXCLUDED_BRANCH_IDS`, `MIRROR_ORDER_SOURCES`) thay vì
chép cứng: thêm chi nhánh loại trừ hay đối tác mirror mới thì mart tự phủ theo,
không phải sửa 3 chỗ.

Định nghĩa gốc từng nằm ở apps/customers/report_trends.py — file đó đã bị xoá cùng
trang Báo cáo khách hàng (2026-08-21). Module này giờ là NGUỒN CHÂN LÝ DUY NHẤT của
mệnh đề GTC; mọi chỗ cần nó phải gọi `gtc_where()` chứ không chép lại 5 mệnh đề.
"""

from apps.common.business_constants import EXCLUDED_BRANCH_IDS
from apps.common.order_scope import sql_exclude_mirror_orders

GTC_STATUS = 3          # OrderStatus.DELIVERED_SUCCESS
CANCELLED_INVOICE = 2


def gtc_where(alias: str = "o") -> str:
    """Trả 5 mệnh đề GTC nối bằng AND. KHÔNG kèm từ khoá WHERE."""
    prefix = f"{alias}." if alias else ""
    branches = ", ".join(f"'{b}'" for b in EXCLUDED_BRANCH_IDS)
    return (
        f"{prefix}deleted_at IS NULL"
        f" AND {prefix}status = {GTC_STATUS}"
        f" AND ({prefix}invoice_status IS DISTINCT FROM {CANCELLED_INVOICE})"
        f" AND NOT ({prefix}branch_id = ANY(ARRAY[{branches}]))"
        f" AND {sql_exclude_mirror_orders(alias)}"
    )
