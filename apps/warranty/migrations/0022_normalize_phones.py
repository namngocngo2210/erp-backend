"""Backfill chuẩn hoá SĐT đã lưu (audit 2026-09-23 T2/P2-8) sau khi
WarrantyJourney.receiver_phone / WarrantyRegistration.phone bắt đầu normalize
trong save(). Đo prod 23/09: warranty_journeys 34 dòng (max 10 ký tự),
warranty_registrations 0 dòng → chạy tức thì.

Chuỗi không ra SĐT hợp lệ giữ nguyên (chỉ strip) — không mất dữ liệu. Reverse
là noop: giá trị cũ không còn để khôi phục và dạng chuẩn hoá vẫn hợp lệ với
code cũ."""

from django.db import migrations

from apps.common.phone_utils import normalize_phone


def _normalize_keep_raw(value):
    raw = (value or "").strip()
    return normalize_phone(raw) or raw


def forwards(apps, schema_editor):
    WarrantyJourney = apps.get_model("warranty", "WarrantyJourney")
    WarrantyRegistration = apps.get_model("warranty", "WarrantyRegistration")
    for model, field in ((WarrantyJourney, "receiver_phone"), (WarrantyRegistration, "phone")):
        for row in model.objects.exclude(**{field: ""}).only("id", field).iterator(chunk_size=500):
            new = _normalize_keep_raw(getattr(row, field))
            if new != getattr(row, field):
                model.objects.filter(pk=row.pk).update(**{field: new})


class Migration(migrations.Migration):
    dependencies = [
        ("warranty", "0021_claim_status_in_transit"),
    ]

    operations = [
        migrations.RunPython(forwards, reverse_code=migrations.RunPython.noop),
    ]
