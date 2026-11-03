# Verbatim copy of apps/orders/migrations/0026_sync_crm_need_catalog.py, kept so the squashed
# migration 0001_squashed_0089_* can reference its RunPython functions even
# after the original per-step migration files are eventually deleted.
from django.db import migrations


GROUP_SEED = [
    {"code": "pain_relief", "name_vi": "giảm đau nhức", "sort_order": 10},
    {"code": "relax_stress_relief", "name_vi": "thư giãn giảm stress", "sort_order": 20},
    {"code": "rehab_support", "name_vi": "hỗ trợ điều trị - phục hồi", "sort_order": 30},
    {"code": "gift", "name_vi": "quà tặng", "sort_order": 40},
]

TYPE_SEED = [
    {"code": "neck_shoulder_nape", "group_code": "pain_relief", "name_vi": "cổ - vai - gáy", "sort_order": 10},
    {"code": "palm", "group_code": "pain_relief", "name_vi": "bàn tay", "sort_order": 20},
    {"code": "whole_arm", "group_code": "pain_relief", "name_vi": "toàn tay", "sort_order": 30},
    {"code": "whole_back", "group_code": "pain_relief", "name_vi": "toàn lưng", "sort_order": 40},
    {"code": "lower_back_waist", "group_code": "pain_relief", "name_vi": "lưng dưới - thắt lưng", "sort_order": 50},
    {"code": "numb_cold_limbs", "group_code": "pain_relief", "name_vi": "tê bì chân tay - lạnh chân", "sort_order": 60},
    {"code": "whole_leg_pain", "group_code": "pain_relief", "name_vi": "đau toàn chân", "sort_order": 70},
    {"code": "knee_pain", "group_code": "pain_relief", "name_vi": "đau đầu gối", "sort_order": 80},
    {"code": "calf_pain", "group_code": "pain_relief", "name_vi": "đau bắp chân", "sort_order": 90},
    {"code": "foot_pain", "group_code": "pain_relief", "name_vi": "đau bàn chân", "sort_order": 100},
    {
        "code": "muscle_pain_after_heavy_activity",
        "group_code": "pain_relief",
        "name_vi": "đau cơ sau vận động nặng",
        "sort_order": 110,
    },
    {"code": "relax_after_work", "group_code": "relax_stress_relief", "name_vi": "thư giãn sau giờ làm", "sort_order": 10},
    {"code": "poor_sleep_insomnia", "group_code": "relax_stress_relief", "name_vi": "ngủ kém - khó ngủ", "sort_order": 20},
    {"code": "work_stress", "group_code": "relax_stress_relief", "name_vi": "căng thẳng - stress công việc", "sort_order": 30},
    {"code": "sitting_too_much", "group_code": "relax_stress_relief", "name_vi": "ngồi nhiều", "sort_order": 40},
    {"code": "low_activity", "group_code": "relax_stress_relief", "name_vi": "ít vận động", "sort_order": 50},
    {"code": "light_massage", "group_code": "relax_stress_relief", "name_vi": "muốn massage nhẹ", "sort_order": 60},
    {"code": "elderly_low_activity", "group_code": "rehab_support", "name_vi": "người lớn tuổi ít vận động", "sort_order": 10},
    {"code": "post_mild_stroke", "group_code": "rehab_support", "name_vi": "người sau tai biến nhẹ", "sort_order": 20},
    {"code": "post_injury", "group_code": "rehab_support", "name_vi": "sau chấn thương", "sort_order": 30},
    {"code": "gift_for_grandparents", "group_code": "gift", "name_vi": "quà biếu ông bà", "sort_order": 10},
    {"code": "gift_for_couple", "group_code": "gift", "name_vi": "quà tặng vợ chồng", "sort_order": 20},
    {"code": "gift_holiday_birthday", "group_code": "gift", "name_vi": "quà tết - sinh nhật - lễ", "sort_order": 30},
    {"code": "gift_for_teacher_boss", "group_code": "gift", "name_vi": "quà cho thầy cô - sếp", "sort_order": 40},
    {"code": "gift_for_customer", "group_code": "gift", "name_vi": "quà cho khách hàng", "sort_order": 50},
    {"code": "buy_for_family_shared_use", "group_code": "gift", "name_vi": "mua để gia đình dùng chung", "sort_order": 60},
]


def sync_need_catalog(apps, schema_editor):
    NeedGroup = apps.get_model("orders", "NeedGroup")
    NeedType = apps.get_model("orders", "NeedType")

    desired_group_codes = {item["code"] for item in GROUP_SEED}
    desired_type_codes = {item["code"] for item in TYPE_SEED}

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

    NeedType.objects.exclude(code__in=desired_type_codes).update(is_active=False)
    NeedGroup.objects.exclude(code__in=desired_group_codes).update(is_active=False)


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0025_orderassessment_crm_models"),
    ]

    operations = [
        migrations.RunPython(sync_need_catalog, noop_reverse),
    ]

