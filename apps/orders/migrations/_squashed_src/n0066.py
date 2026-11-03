# Verbatim copy of apps/orders/migrations/0066_fix_invalid_created_at_year.py, kept so the squashed
# migration 0001_squashed_0089_* can reference its RunPython functions even
# after the original per-step migration files are eventually deleted.
"""Sửa các đơn có created_at năm vô lý (vd 0202) do ingest parse lỗi.

Những đơn này lọt ra ngoài mọi khoảng ngày của trang Orders nên "có trong DB
mà search không ra". Đặt lại created_at = updated_at khi updated_at hợp lệ
(năm >= 2000), ngược lại fallback về thời điểm chạy migration.

Không reversible (dữ liệu năm gốc là rác, không cần khôi phục) — backwards là noop.
"""
from __future__ import annotations

from django.db import migrations
from django.utils import timezone

_MIN_VALID_YEAR = 2000


def forwards(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    now = timezone.now()

    broken = Order.objects.filter(created_at__year__lt=_MIN_VALID_YEAR)
    fixed = 0
    for order in broken.iterator(chunk_size=500):
        if order.updated_at and order.updated_at.year >= _MIN_VALID_YEAR:
            order.created_at = order.updated_at
        else:
            order.created_at = now
        order.save(update_fields=["created_at"])
        fixed += 1

    if fixed:
        print(f"[0066_fix_invalid_created_at_year] fixed={fixed}")


def backwards(apps, schema_editor):
    """Noop — không thể (và không nên) khôi phục năm rác ban đầu."""


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0065_alter_order_order_source"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
