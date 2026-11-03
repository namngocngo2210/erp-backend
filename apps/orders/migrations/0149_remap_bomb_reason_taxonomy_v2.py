"""Chuyển dữ liệu lý do bom sang bộ phân loại mới (7 nhóm, chốt 2026-10-01).

- Loại cũ có loại tương đương → đổi mã; nếu ghi chú đang trống thì lưu nhãn cũ vào
  `bomb_reason_text` để không mất thông tin gốc.
- Mã giữ nguyên nhưng đổi nhóm (vd `defective_not_working` → `product_quality`) →
  chỉ cập nhật nhóm theo bảng TYPE_GROUP.
- Nhóm cũ không còn (`finance`, `trust_information`) mà không có loại → xoá nhóm.

Mapping được đóng băng tại đây (không import crm_catalog) để migration không đổi
nghĩa khi catalog đổi về sau. Reverse là no-op: không khôi phục được mã cũ đã gộp.
"""

from django.db import migrations
from django.db.models import Q

# old_type_code → (new_type_code, nhãn cũ để giữ làm ghi chú)
TYPE_REMAP = {
    "found_cheaper_elsewhere": ("price_expectation", "Thấy giá chỗ khác rẻ hơn"),
    "trial_or_emotional_order": ("changed_mind_not_buying", "Đặt thử cho biết / đặt theo cảm xúc"),
    "require_staff_installation": ("customer_uncooperative", "Phải có nhân viên lắp đặt mới lấy"),
    "mistook_other_product": ("expected_other_features", "Khách nhớ nhầm sản phẩm bên khác"),
    "no_chair_not_accepting": ("product_not_suitable", "Không lấy do không có ghế"),
    "waiting_for_salary_or_cash_flow": ("insufficient_cash_on_delivery", "Đợi lương / chưa xoay kịp tiền"),
    "refuse_china_need_japan": ("origin_expectation", "Hàng Trung Quốc không lấy, cần hàng Nhật"),
    "negative_feedback_after_order": ("changed_mind_not_buying", "Đọc feedback xấu sau khi đặt"),
    "order_commitment_mismatch": ("overpromising_wording", "Chốt đơn báo 1 kiểu, giao báo 1 kiểu"),
    "natural_disaster": ("delivery_too_slow", "Thiên tai"),
    "misunderstood_product_utility": ("unclear_feature_description", "Hiểu sai công dụng"),
    "massage_painful": ("not_suitable_for_user", "Chê massage bị đau"),
}

# type_code → group_code của bộ mới (gồm cả mã legacy `other_reason`).
TYPE_GROUP = {
    "no_answer": "customer",
    "customer_away_business_trip": "customer",
    "customer_uncooperative": "customer",
    "changed_mind_not_buying": "customer",
    "family_disallow_acceptance": "customer",
    "received_from_other_seller_first": "customer",
    "insufficient_cash_on_delivery": "customer",
    "cancelled_after_dispatch": "customer",
    "other_reason": "customer",
    "shipper_route_no_time_support": "shipping_unit",
    "shipper_no_trial_allowed": "shipping_unit",
    "shipper_auto_returned": "shipping_unit",
    "hub_overload_not_delivered": "shipping_unit",
    "delivery_too_slow": "shipping_unit",
    "customer_shipper_conflict": "shipping_unit",
    "parcel_lost": "shipping_unit",
    "wrong_address": "order_processing",
    "wrong_product": "order_processing",
    "wrong_price": "order_processing",
    "duplicate_order": "order_processing",
    "cancelled_before_dispatch_still_sent": "order_processing",
    "slow_order_processing": "order_processing",
    "underlying_condition": "order_processing",
    "wrong_item_packed": "packaging",
    "missing_gifts": "packaging",
    "missing_items": "packaging",
    "mixed_up_item_packed": "packaging",
    "used_product": "packaging",
    "poor_packaging_damaged": "packaging",
    "product_not_suitable": "product_expectation",
    "complain_machine_weak": "product_expectation",
    "expected_other_features": "product_expectation",
    "not_satisfied_after_inspection": "product_expectation",
    "size_not_suitable": "product_expectation",
    "not_suitable_for_user": "product_expectation",
    "origin_expectation": "product_expectation",
    "price_expectation": "product_expectation",
    "not_as_advertised": "advertising_info",
    "media_misleading": "advertising_info",
    "unclear_feature_description": "advertising_info",
    "overpromising_wording": "advertising_info",
    "defective_not_working": "product_quality",
    "missing_accessories": "product_quality",
    "damaged_appearance": "product_quality",
    "abnormally_weak": "product_quality",
    "poor_experience_product_issue": "product_quality",
}

NEW_GROUPS = frozenset(TYPE_GROUP.values())


def remap_bomb_reasons(apps, schema_editor):
    OrderAssessment = apps.get_model("orders", "OrderAssessment")

    for old_code, (new_code, old_label) in TYPE_REMAP.items():
        rows = OrderAssessment.objects.filter(bomb_reason_type_code=old_code)
        rows.filter(Q(bomb_reason_text="") | Q(bomb_reason_text__isnull=True)).update(bomb_reason_text=old_label)
        rows.update(bomb_reason_type_code=new_code)

    for type_code, group_code in TYPE_GROUP.items():
        OrderAssessment.objects.filter(bomb_reason_type_code=type_code).exclude(
            bomb_reason_group_code=group_code
        ).update(bomb_reason_group_code=group_code)

    OrderAssessment.objects.filter(bomb_reason_type_code__isnull=True).exclude(
        Q(bomb_reason_group_code__isnull=True) | Q(bomb_reason_group_code="") | Q(bomb_reason_group_code__in=NEW_GROUPS)
    ).update(bomb_reason_group_code=None)


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0148_order_channel_created_index"),
    ]

    operations = [
        migrations.RunPython(remap_bomb_reasons, migrations.RunPython.noop),
    ]
