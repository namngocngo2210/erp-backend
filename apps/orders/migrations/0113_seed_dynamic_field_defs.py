from django.db import migrations

SEEDS = [
    {
        "code": "call_summary",
        "label": "Tóm tắt cuộc gọi",
        "ai_hint": "tóm tắt ngắn gọn tình trạng, nhu cầu và ý định mua của khách qua cuộc gọi",
        "sort_order": 10,
    },
    {
        "code": "competitors_mentioned",
        "label": "Đối thủ được nhắc tới",
        "ai_hint": "tên thương hiệu/hãng ghế hoặc máy massage khác mà khách nhắc tới hoặc đang so sánh",
        "sort_order": 20,
    },
    {
        "code": "family_context",
        "label": "Hoàn cảnh gia đình",
        "ai_hint": "ai trong nhà sẽ dùng máy, khách sống cùng ai, ai là người quyết định chi tiền",
        "sort_order": 30,
    },
]


def seed(apps, schema_editor):
    AssessmentFieldDef = apps.get_model("orders", "AssessmentFieldDef")
    for item in SEEDS:
        AssessmentFieldDef.objects.update_or_create(
            code=item["code"],
            defaults={**item, "kind": "text", "options": [], "ai_enabled": True, "is_active": True},
        )


def unseed(apps, schema_editor):
    AssessmentFieldDef = apps.get_model("orders", "AssessmentFieldDef")
    AssessmentFieldDef.objects.filter(code__in=[item["code"] for item in SEEDS]).delete()


class Migration(migrations.Migration):
    # Phụ thuộc CẢ HAI 0112: hai nhánh merge cùng ngày 2026-08-03 tạo hai leaf
    # song song (0112_price_books ↔ 0112_dynamic_assessment_fields) — migrate nổ
    # "Conflicting migrations detected" và deploy prod sẽ chết ở bước migrate.
    # Khai thêm dependency tại đây (0113 chưa áp ở đâu) thay vì đẻ file merge mới.
    dependencies = [
        ("orders", "0112_dynamic_assessment_fields"),
        ("orders", "0112_price_books"),
    ]
    operations = [migrations.RunPython(seed, unseed)]
