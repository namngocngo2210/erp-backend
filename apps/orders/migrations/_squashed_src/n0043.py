# Verbatim copy of apps/orders/migrations/0043_resync_need_catalog_merge_low_activity.py, kept so the squashed
# migration 0001_squashed_0089_* can reference its RunPython functions even
# after the original per-step migration files are eventually deleted.
from django.db import migrations


GROUP_SEED = [
    {"code": "pain_relief", "name_vi": "Giảm đau nhức", "sort_order": 10},
    {"code": "relax_stress_relief", "name_vi": "Thư giãn giảm stress", "sort_order": 20},
    {"code": "rehab_support", "name_vi": "Hỗ trợ điều trị - phục hồi", "sort_order": 30},
    {"code": "gift", "name_vi": "Quà tặng", "sort_order": 40},
]

TYPE_SEED = [
    {"code": "neck_shoulder_nape", "group_code": "pain_relief", "name_vi": "Cổ - vai - gáy", "sort_order": 10},
    {"code": "palm", "group_code": "pain_relief", "name_vi": "Bàn tay", "sort_order": 20},
    {"code": "whole_arm", "group_code": "pain_relief", "name_vi": "Toàn tay", "sort_order": 30},
    {"code": "shoulder_blade", "group_code": "pain_relief", "name_vi": "Đau bả vai", "sort_order": 35},
    {"code": "whole_back", "group_code": "pain_relief", "name_vi": "Toàn lưng", "sort_order": 40},
    {"code": "lower_back_waist", "group_code": "pain_relief", "name_vi": "Lưng dưới - thắt lưng", "sort_order": 50},
    {"code": "numb_cold_limbs", "group_code": "pain_relief", "name_vi": "Tê bì chân tay - lạnh chân", "sort_order": 60},
    {"code": "whole_leg_pain", "group_code": "pain_relief", "name_vi": "Đau toàn chân", "sort_order": 70},
    {"code": "knee_pain", "group_code": "pain_relief", "name_vi": "Đau đầu gối", "sort_order": 80},
    {"code": "calf_pain", "group_code": "pain_relief", "name_vi": "Đau bắp chân", "sort_order": 90},
    {"code": "foot_pain", "group_code": "pain_relief", "name_vi": "Đau bàn chân", "sort_order": 100},
    {"code": "muscle_pain_after_heavy_activity", "group_code": "pain_relief", "name_vi": "Đau cơ sau vận động nặng", "sort_order": 110},
    {"code": "whole_body_pain", "group_code": "pain_relief", "name_vi": "Đau toàn thân", "sort_order": 115},
    {"code": "relax_after_work", "group_code": "relax_stress_relief", "name_vi": "Thư giãn sau giờ làm", "sort_order": 10},
    {"code": "poor_sleep_insomnia", "group_code": "relax_stress_relief", "name_vi": "Ngủ kém - khó ngủ", "sort_order": 20},
    {"code": "work_stress", "group_code": "relax_stress_relief", "name_vi": "Căng thẳng - stress công việc", "sort_order": 30},
    {"code": "sitting_too_much", "group_code": "relax_stress_relief", "name_vi": "Ngồi nhiều - ít vận động", "sort_order": 40},
    {"code": "light_massage", "group_code": "relax_stress_relief", "name_vi": "Muốn massage nhẹ", "sort_order": 60},
    {"code": "elderly_low_activity", "group_code": "rehab_support", "name_vi": "Người lớn tuổi ít vận động", "sort_order": 10},
    {"code": "post_mild_stroke", "group_code": "rehab_support", "name_vi": "Người sau tai biến nhẹ", "sort_order": 20},
    {"code": "post_injury", "group_code": "rehab_support", "name_vi": "Sau chấn thương", "sort_order": 30},
    {"code": "gift_for_grandparents", "group_code": "gift", "name_vi": "Quà biếu ông bà", "sort_order": 10},
    {"code": "gift_for_parents", "group_code": "gift", "name_vi": "Quà biếu bố mẹ", "sort_order": 15},
    {"code": "gift_for_couple", "group_code": "gift", "name_vi": "Quà tặng vợ chồng", "sort_order": 20},
    {"code": "gift_holiday_birthday", "group_code": "gift", "name_vi": "Quà tết - sinh nhật - lễ", "sort_order": 30},
    {"code": "gift_for_teacher_boss", "group_code": "gift", "name_vi": "Quà cho thầy cô - sếp", "sort_order": 40},
    {"code": "gift_for_customer", "group_code": "gift", "name_vi": "Quà cho khách hàng", "sort_order": 50},
    {"code": "buy_for_family_shared_use", "group_code": "gift", "name_vi": "Mua để gia đình dùng chung", "sort_order": 60},
]


def resync_and_merge(apps, schema_editor):
    NeedGroup = apps.get_model("orders", "NeedGroup")
    NeedType = apps.get_model("orders", "NeedType")
    OrderNeed = apps.get_model("orders", "OrderNeed")

    # Step 1: upsert groups.
    groups = {}
    for item in GROUP_SEED:
        group, _ = NeedGroup.objects.update_or_create(
            code=item["code"],
            defaults={
                "name_vi": item["name_vi"],
                "sort_order": item["sort_order"],
                "is_active": True,
            },
        )
        groups[item["code"]] = group

    # Step 2: upsert types (covers label updates + new entries).
    for item in TYPE_SEED:
        NeedType.objects.update_or_create(
            code=item["code"],
            defaults={
                "group": groups[item["group_code"]],
                "name_vi": item["name_vi"],
                "sort_order": item["sort_order"],
                "is_active": True,
            },
        )

    # Step 3: remap OrderNeed rows from low_activity to sitting_too_much
    # (merged label "Ngồi nhiều - ít vận động"). Respect unique(assessment, type):
    # if an assessment already has sitting_too_much, drop the duplicate low_activity.
    low_activity = NeedType.objects.filter(code="low_activity").first()
    sitting = NeedType.objects.filter(code="sitting_too_much").first()
    if low_activity and sitting:
        existing_sitting_assessments = set(
            OrderNeed.objects.filter(need_type=sitting).values_list("order_assessment_id", flat=True)
        )
        OrderNeed.objects.filter(
            need_type=low_activity,
            order_assessment_id__in=existing_sitting_assessments,
        ).delete()
        OrderNeed.objects.filter(need_type=low_activity).update(need_type=sitting)

    # Step 4: deactivate anything not in current seed (soft-remove low_activity etc.).
    desired_type_codes = {item["code"] for item in TYPE_SEED}
    desired_group_codes = {item["code"] for item in GROUP_SEED}
    NeedType.objects.exclude(code__in=desired_type_codes).update(is_active=False)
    NeedGroup.objects.exclude(code__in=desired_group_codes).update(is_active=False)


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0042_order_product_raw"),
    ]

    operations = [
        migrations.RunPython(resync_and_merge, noop_reverse),
    ]
