"""Partial unique cho Channel.code trên tập kênh active (audit 2026-07 mục 3.6).

Bảng channels nhỏ (vài chục dòng) → AddConstraint thường, không cần CONCURRENTLY.
Bước kiểm tra dup chạy trước và FAIL RÕ RÀNG kèm danh sách mã trùng — vận hành phải
xử lý tay (đổi mã / soft-delete kênh thừa) rồi migrate lại, không tự gộp ngầm.
"""
from django.db import migrations, models
from django.db.models import Count


def check_duplicate_codes(apps, schema_editor):
    Channel = apps.get_model("channels_core", "Channel")
    dups = list(
        Channel.objects.filter(deleted_at__isnull=True)
        .values("code")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .values_list("code", "n")
    )
    if dups:
        detail = ", ".join(f"{code!r} (x{n})" for code, n in dups)
        raise RuntimeError(
            "Không thể tạo unique constraint channel_code_active_uniq: "
            f"tồn tại mã kênh active trùng lặp: {detail}. "
            "Đổi code hoặc soft-delete kênh thừa rồi chạy migrate lại."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("channels_core", "0019_channel_google_ads_accounts"),
    ]

    operations = [
        migrations.RunPython(check_duplicate_codes, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="channel",
            constraint=models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(deleted_at__isnull=True),
                name="channel_code_active_uniq",
            ),
        ),
    ]
