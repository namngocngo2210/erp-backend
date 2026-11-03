"""Resolve thông tin người gửi cho luồng tạo vận đơn KShip.

Ưu tiên: bản ghi được chọn từ dialog → bản ghi seed theo rule kênh/branch
lịch sử → hằng số hardcode cũ (safety net, log warning). Không bao giờ raise
vì thiếu config — thiếu địa chỉ gửi không được chặn việc tạo vận đơn.

Đặt ở apps/orders (không phải apps/kiotviet) để giữ chiều import layering:
kiotviet → orders được phép, orders → kiotviet là nợ importlinter.
"""
from __future__ import annotations

import logging

from apps.orders.models import KshipSenderAddress

logger = logging.getLogger(__name__)

# Rule lịch sử (nguyên văn từ service_actions.py trước refactor):
# kênh mới hoặc chi nhánh Đại Mỗ legacy ship từ Văn Quán; kênh cũ từ Đại Mỗ.
LEGACY_DAI_MO_BRANCH_ID = "1000000477"
LEGACY_DAI_MO_BRANCH_ID_2 = "89084"

_FALLBACK_BY_CODE = {
    "van-quan": {"location_id": 238, "ward_id": 260, "address": "số 60 Đại Mỗ", "mobile": "+84 359 529 280"},
    "dai-mo": {"location_id": 255, "ward_id": 209, "address": "số 60 Đại Mỗ", "mobile": "+84 359 529 280"},
}


def default_sender_code_for_order(order) -> str:
    """Trả code bản ghi seed mặc định ("van-quan" | "dai-mo") theo rule kênh/branch."""
    channel = getattr(order, "kiotviet_channel", None)
    is_new_channel = (
        channel is not None
        and not channel.old
        and str(order.branch_id) != LEGACY_DAI_MO_BRANCH_ID_2
    )
    is_legacy_dai_mo = str(order.branch_id) == LEGACY_DAI_MO_BRANCH_ID
    return "van-quan" if (is_new_channel or is_legacy_dai_mo) else "dai-mo"


def resolve_default_sender_address(order) -> KshipSenderAddress | None:
    """Bản ghi mặc định theo rule, hoặc None nếu seed đã bị xóa."""
    return KshipSenderAddress.objects.filter(code=default_sender_code_for_order(order)).first()


def _to_sender_info(row: KshipSenderAddress) -> dict:
    return {
        "location_id": row.location_id,
        "ward_id": row.ward_id,
        "address": row.address,
        "mobile": row.mobile,
    }


def resolve_kship_sender_info(order, sender_address_id: int | None = None) -> dict:
    """Resolve sender_info dict cho payload KShip (SENDER_LOCATION_ID/WARD_ID/ADDRESS/MOBILE)."""
    if sender_address_id:
        row = KshipSenderAddress.objects.filter(id=sender_address_id).first()
        if row is not None:
            return _to_sender_info(row)
        logger.warning(
            "kship_sender.selected_missing id=%s order=%s — dùng rule mặc định",
            sender_address_id,
            order.id,
        )

    code = default_sender_code_for_order(order)
    row = KshipSenderAddress.objects.filter(code=code).first()
    if row is not None:
        return _to_sender_info(row)

    logger.warning("kship_sender.seed_missing code=%s order=%s — dùng hằng số fallback", code, order.id)
    return dict(_FALLBACK_BY_CODE[code])
