"""Ai được xem SĐT khách ĐẦY ĐỦ, ai chỉ thấy bản che (091****678).

Luật nghiệp vụ (chốt 2026-09-22): sale được cấp scope rộng để tra cứu/đối soát đơn
toàn công ty, nhưng SĐT khách của đơn KHÔNG do mình phụ trách là tài sản cạnh tranh
giữa sale — thấy đủ để nhận ra đơn, không đủ để mang tệp khách đi.

Hai điều kiện, thoả BẤT KỲ cái nào thì thấy số thật:
- Có quyền `customer-phone:view` (role quản lý/kho/CSKH — migration 0050 grandfather
  cho mọi role đang tồn tại, siết thật = bỏ tick ở role Sale trên UI Phân quyền).
- Đơn/khách đó do CHÍNH user phụ trách (`Order.staff_id == user.id`).

Đặt ở apps.users (không phải apps.common) vì phải hỏi resolver quyền; apps.common nằm
DƯỚI users trong contract phân tầng nên không được import ngược lên.
"""

from __future__ import annotations

from dataclasses import dataclass

# Section RBAC riêng, KHÔNG có trang riêng — cùng khuôn `sale-assignment` (một chính
# sách công ty nằm một chỗ, thay vì rải thành field rule trên 5 section đơn). Hằng nằm
# ở apps.common vì consumer WebSocket cũng cần (xem ghi chú tại chỗ định nghĩa).
from apps.common.business_constants import MARKETPLACE_ORDER_SOURCE_CODES
from apps.common.masking import (  # noqa: F401  (re-export cho call site tầng trên)
    CUSTOMER_PHONE_PERMISSION,
    is_masked_phone,
    mask_phone_middle,
)


def is_public_phone_source(order_source: str | None) -> bool:
    """True nếu SĐT của đơn đến từ SÀN TMĐT — diện miễn che (xem `PhoneVisibility.apply`)."""
    return str(order_source or "").strip().lower() in MARKETPLACE_ORDER_SOURCE_CODES


@dataclass(frozen=True)
class PhoneVisibility:
    """Quyết định che/không-che SĐT cho MỘT user, tính 1 lần/request.

    Dùng như giá trị context của serializer hoặc gọi thẳng trong view/service. Không
    tự hỏi lại resolver ở từng dòng (list 100 dòng = 100 lần resolve cache).
    """

    user_id: int | None
    can_view_full: bool

    def owns(self, owner_id: int | None) -> bool:
        return (
            self.user_id is not None
            and owner_id is not None
            and int(owner_id) == int(self.user_id)
        )

    def apply(
        self, phone: str | None, owner_id: int | None, *, public_source: bool = False
    ) -> str:
        """SĐT hiển thị cho user: số thật nếu được phép / đơn của mình, ngược lại che.

        `public_source=True` — đơn SÀN TMĐT (Shopee/TikTok): KHÔNG che. Sàn đã trả về
        số che sẵn (vd TikTok Shop `(+84)037*****54`), che thêm một lớp nữa chỉ ăn mất
        chữ số mà không giấu thêm gì; và số đó là của sàn, không phải tệp khách sale
        này gây dựng nên cũng không thuộc diện "tài sản cạnh tranh giữa sale".
        """
        if self.can_view_full or public_source or self.owns(owner_id):
            return str(phone or "")
        return mask_phone_middle(phone)


def phone_visibility_for(user) -> PhoneVisibility:
    """PhoneVisibility của user — tính qua resolver có cache (superuser: thấy đủ)."""
    from .auth_core import user_has_any_permission

    return PhoneVisibility(
        user_id=getattr(user, "id", None),
        can_view_full=user_has_any_permission(user, CUSTOMER_PHONE_PERMISSION),
    )


def strip_masked_phone(data: dict, key: str = "phone") -> dict:
    """Bỏ `key` khỏi payload GHI nếu giá trị là SĐT đã che (chuỗi có '*').

    Vì sao im lặng bỏ thay vì 400: FE nạp form sửa đơn từ chính payload list/detail,
    nên với đơn bị che thì ô SĐT mang sẵn "091****678" và mọi lần lưu (dù sale chỉ
    sửa địa chỉ) đều gửi chuỗi đó lên. Bỏ key = giữ nguyên số gốc trong DB — cùng
    khuôn `field_policy.strip_protected_write_fields` (strip + log, không 403).
    """
    if key in data and is_masked_phone(data.get(key)):
        cleaned = dict(data)
        cleaned.pop(key, None)
        return cleaned
    return data
