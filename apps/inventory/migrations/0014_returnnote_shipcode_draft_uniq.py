"""Partial unique cho ReturnNote.shipping_code trên tập phiếu nháp chưa khớp đơn
(audit 2026-07 mục race 2 scan cùng vận đơn).

Hai bước:
1. Dedup: với mỗi ``shipping_code`` bị trùng ở nhiều phiếu draft/unmatched
   (``source_order`` rỗng, ``status="draft"``), giữ lại phiếu tạo sớm nhất
   (survivor), các phiếu draft trùng còn lại chuyển ``status=cancelled`` và
   append note đánh dấu để vận hành biết đây là dedup tự động, không phải huỷ
   tay. Các phiếu đã posted/cancelled không bị đụng tới (điều kiện constraint
   chỉ áp cho draft nên không cần dedup).
2. AddConstraint như bình thường — bảng ``stock_return_notes`` nhỏ nên không
   cần CONCURRENTLY.
"""
from django.conf import settings
from django.db import migrations, models
from django.db.models import Count


_DEDUP_NOTE_SUFFIX = " [dedup audit 2026-07-06]"


def dedup_draft_shipping_codes(apps, schema_editor):
    ReturnNote = apps.get_model("inventory", "ReturnNote")

    dup_codes = list(
        ReturnNote.objects.filter(status="draft", source_order__isnull=True)
        .exclude(shipping_code="")
        .values("shipping_code")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .values_list("shipping_code", flat=True)
    )

    for code in dup_codes:
        group = list(
            ReturnNote.objects.filter(
                shipping_code=code, status="draft", source_order__isnull=True,
            ).order_by("created_at", "id")
        )
        if len(group) < 2:
            continue
        _survivor, losers = group[0], group[1:]
        for loser in losers:
            loser.status = "cancelled"
            loser.note = f"{loser.note}{_DEDUP_NOTE_SUFFIX}"
            loser.save(update_fields=["status", "note"])


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0013_returnnoteline_return_type'),
        ('orders', '0095_order_ord_no_address_created_idx'),
        ('warehouses', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(dedup_draft_shipping_codes, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='returnnote',
            constraint=models.UniqueConstraint(condition=models.Q(('shipping_code__gt', ''), ('source_order__isnull', True), ('status', 'draft')), fields=('shipping_code',), name='returnnote_shipcode_draft_uniq'),
        ),
    ]
