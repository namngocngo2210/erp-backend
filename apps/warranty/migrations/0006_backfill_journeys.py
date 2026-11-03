from django.db import migrations

_LEGACY_TO_NOTE = ("contact_channel", "social_nickname", "return_status")


def forwards(apps, schema_editor):
    WarrantyClaim = apps.get_model("warranty", "WarrantyClaim")
    WarrantyJourney = apps.get_model("warranty", "WarrantyJourney")

    # status cũ 1 Mới/2 Đang xử lý → 1 Đang xử lý; 3 Hoàn tất → 2; 4 Hủy → 3.
    status_map = {1: 1, 2: 1, 3: 2, 4: 3}

    for claim in WarrantyClaim.objects.all().iterator():
        if claim.journeys.exists():
            continue
        note_parts = []
        for f in _LEGACY_TO_NOTE:
            v = getattr(claim, f, "") or ""
            if v:
                note_parts.append(f"{f}: {v}")
        if getattr(claim, "require_carrier_call", False):
            note_parts.append("YC gọi xác nhận: có")
        if getattr(claim, "send_method", ""):
            note_parts.append(f"hình thức gửi cũ: {claim.send_method}")
        # classification cũ là text tự do → không map enum, ghi vào note.
        legacy_classification = getattr(claim, "classification", "") or ""
        if legacy_classification:
            note_parts.append(f"phân loại cũ: {legacy_classification}")

        resolution_note_parts = []
        for f in ("accounting_check", "accounting_note"):
            v = getattr(claim, f, "") or ""
            if v:
                resolution_note_parts.append(f"{f}: {v}")

        WarrantyJourney.objects.create(
            claim=claim,
            seq=1,
            send_reason=getattr(claim, "send_reason", "") or "",
            recall_code=getattr(claim, "recall_code", "") or "",
            note="\n".join(note_parts),
            resolution_note="\n".join(resolution_note_parts),
            status="new",
            created_by=claim.created_by,
        )
        new_status = status_map.get(claim.status, 1)
        if claim.status != new_status:
            claim.status = new_status
            claim.save(update_fields=["status"])


def backwards(apps, schema_editor):
    # Không reverse hoàn hảo dữ liệu; chỉ xóa journey seq=1 sinh bởi migration.
    WarrantyJourney = apps.get_model("warranty", "WarrantyJourney")
    WarrantyJourney.objects.filter(seq=1).delete()


class Migration(migrations.Migration):
    dependencies = [("warranty", "0005_warranty_journey")]
    operations = [migrations.RunPython(forwards, backwards)]
