from django.db import migrations

# Seed nội tuyến để migration độc lập với thay đổi code sau này.
SEGMENT_SEED = [
    {"code": "S1", "name": "Tiềm năng chưa mua", "criteria": "Khách có trạng thái gọi \"Tư vấn thêm, Hẹn lịch, Gọi lại sau\"", "care_priority": "Chốt đơn", "sort_order": 10},
    {"code": "S2", "name": "Mới mua 1 lần", "criteria": "1 đơn GTC ≤90 ngày", "care_priority": "Upsell trong 30–60 ngày", "sort_order": 20},
    {"code": "S3", "name": "Trung thành", "criteria": "≥2 đơn GTC hoặc ≥2 triệu", "care_priority": "Chăm 30–60 ngày/lần", "sort_order": 30},
    {"code": "S4", "name": "Ngủ đông", "criteria": ">180 ngày không mua", "care_priority": "Tái kích hoạt ngay", "sort_order": 40},
    {"code": "S5", "name": "Có vấn đề", "criteria": "Hoàn / khiếu nại / đánh giá xấu / bảo hành", "care_priority": "Xử lý trước khi resale", "sort_order": 50},
    {"code": "S6", "name": "B2B / Đối tác", "criteria": "≥3 đơn GTC hoặc phòng khám/đại lý", "care_priority": "Chăm riêng, quy trình B2B", "sort_order": 60},
    {"code": "S7", "name": "Từ chối", "criteria": "Tiếp cận được nhưng không mua", "care_priority": "Remarketing sau 30–60 ngày", "sort_order": 70},
]


def seed_segments(apps, schema_editor):
    CustomerSegmentCategory = apps.get_model("customers", "CustomerSegmentCategory")
    for item in SEGMENT_SEED:
        CustomerSegmentCategory.objects.update_or_create(
            code=item["code"],
            defaults={
                "name": item["name"],
                "criteria": item["criteria"],
                "care_priority": item["care_priority"],
                "sort_order": item["sort_order"],
                "is_active": True,
            },
        )


def unseed_segments(apps, schema_editor):
    CustomerSegmentCategory = apps.get_model("customers", "CustomerSegmentCategory")
    CustomerSegmentCategory.objects.filter(code__in=[item["code"] for item in SEGMENT_SEED]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0014_customersegmentcategory"),
    ]

    operations = [
        migrations.RunPython(seed_segments, unseed_segments),
    ]
