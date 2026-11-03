"""Rule suy diễn "tác nghiệp cần" cho trang Danh sách contact.

Điểm chân lý duy nhất — đổi mapping chỉ sửa file này. Input là trạng thái của
ĐƠN MỚI NHẤT của khách (sales_status_code + Order.status) và loại khách.
Xem docs/sale-contacts-page-design.md §5.
"""

from apps.orders.models import OrderStatus

ACTION_KEYS: tuple[str, ...] = (
    "new_customer",
    "new_lead",
    "call_2",
    "call_3",
    "call_4",
    "call_5",
    "call_6",
    "recheck_phone",
    "nurture",
    "follow_up",
    "awaiting_delivery",
    "post_sale",
    "none",
)

# Nhãn tiếng Việt của từng action — MIRROR `CONTACT_ACTIONS` ở frontend
# (features/contacts/contact-actions.ts). Cần ở backend vì file Excel do worker
# sinh ra, không đi qua frontend: đổi nhãn phải sửa CẢ HAI chỗ.
ACTION_LABELS: dict[str, str] = {
    "new_customer": "KH mới",
    "new_lead": "Lead mới",
    "call_2": "Gọi lần 2",
    "call_3": "Gọi lần 3",
    "call_4": "Gọi lần 4",
    "call_5": "Gọi lần 5",
    "call_6": "Gọi lần 6",
    "recheck_phone": "Check lại số",
    "nurture": "Chăm sóc thêm",
    "follow_up": "Gọi theo hẹn",
    "awaiting_delivery": "Chờ giao",
    "post_sale": "Chăm sóc sau bán",
    "none": "",
}

# sales_status_code → action key (các code không cần diễn giải theo Order.status)
_CODE_TO_ACTION = {
    "no_answer_1": "call_2",
    "no_answer_2": "call_3",
    "no_answer_3": "call_4",
    "no_answer_4": "call_5",
    "no_answer_5": "call_6",
    "no_answer_6": "recheck_phone",
    "unreachable": "recheck_phone",
    "wrong_number": "recheck_phone",
    "need_more_consulting": "nurture",
    "scheduled": "follow_up",
    "call_back_later": "follow_up",
    "wants_store_visit": "follow_up",
    "closed_scheduled_date": "follow_up",
    "closed": "awaiting_delivery",
    "closed_express": "awaiting_delivery",
    "created_order": "awaiting_delivery",
}


def derive_action_needed(
    sales_status_code: str | None, order_status: int, is_new_customer: bool
) -> str:
    """Suy ra tác nghiệp cần từ trạng thái đơn mới nhất. Không raise với code lạ."""
    if order_status == OrderStatus.CANCELLED:
        return "none"
    if order_status == OrderStatus.DELIVERED_SUCCESS:
        return "post_sale"
    code = (sales_status_code or "").strip()
    if not code:
        return "new_customer" if is_new_customer else "new_lead"
    return _CODE_TO_ACTION.get(code, "none")
