# Verbatim copy of apps/orders/migrations/0049_backfill_spam_blocklist.py, kept so the squashed
# migration 0001_squashed_0089_* can reference its RunPython functions even
# after the original per-step migration files are eventually deleted.
"""Backfill PhoneBlocklist from historical OrderAssessment spam flags.

Any order whose assessment has customer_classification_code='spam' or
sales_status_code='spam' gets its normalized phone inserted into the
blocklist with reason='spam'. Idempotent — re-running is safe because of
the unique(phone_normalized) constraint + get_or_create semantics.
"""
from __future__ import annotations

import re

from django.db import migrations
from django.db.models import Q


def _normalize_phone(raw: str) -> str:
    if not raw:
        return ""
    digits = re.sub(r"\D+", "", str(raw).strip())
    if not digits:
        return ""
    if digits.startswith("0084"):
        digits = digits[4:]
    elif digits.startswith("84"):
        digits = digits[2:]
    if digits.startswith("0"):
        normalized = digits
    elif len(digits) == 9:
        normalized = f"0{digits}"
    elif len(digits) == 10 and digits.startswith("2"):
        normalized = f"0{digits}"
    else:
        normalized = digits
    if not re.fullmatch(r"0\d{9,10}", normalized):
        return ""
    return normalized


def forwards(apps, schema_editor):
    OrderAssessment = apps.get_model("orders", "OrderAssessment")
    PhoneBlocklist = apps.get_model("orders", "PhoneBlocklist")

    spam_assessments = (
        OrderAssessment.objects.filter(order__deleted_at__isnull=True)
        .filter(Q(customer_classification_code="spam") | Q(sales_status_code="spam"))
        .select_related("order")
    )

    inserted = 0
    skipped = 0
    for assessment in spam_assessments.iterator(chunk_size=500):
        order = assessment.order
        phone_norm = _normalize_phone(order.phone)
        if not phone_norm:
            skipped += 1
            continue
        source = (
            "customer_classification_code"
            if assessment.customer_classification_code == "spam"
            else "sales_status_code"
        )
        _, created = PhoneBlocklist.objects.get_or_create(
            phone_normalized=phone_norm,
            defaults={
                "reason": "spam",
                "notes": f"auto-backfill: {source}=spam order_id={order.id}",
            },
        )
        if created:
            inserted += 1

    if inserted or skipped:
        print(
            f"[0049_backfill_spam_blocklist] inserted={inserted} skipped_invalid_phone={skipped}"
        )


def backwards(apps, schema_editor):
    """Remove only auto-backfilled entries; leave manual entries alone."""
    PhoneBlocklist = apps.get_model("orders", "PhoneBlocklist")
    PhoneBlocklist.objects.filter(notes__startswith="auto-backfill:").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0048_lead_filter_layer"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
