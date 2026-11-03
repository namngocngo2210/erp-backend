from django.core.management.base import BaseCommand

from apps.common.phone_utils import normalize_phone
from apps.orders.models import Order

BATCH_SIZE = 2000


class Command(BaseCommand):
    help = (
        "Backfill Order.phone_normalized from Order.phone for existing rows. "
        "Idempotent and batched: safe to re-run, picks up where it left off if "
        "interrupted. Kept off the migrate/deploy path so a slow full-table pass "
        "can never block the web container from booting."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size", type=int, default=BATCH_SIZE,
            help=f"Rows per bulk_update (default {BATCH_SIZE}).",
        )

    def handle(self, *args, **options):
        batch_size = max(1, int(options["batch_size"]))
        # Only rows that still need it: a non-empty phone whose normalized form is empty.
        # Re-running after an interruption skips already-filled rows, so progress is kept.
        qs = (
            Order.objects.exclude(phone="")
            .filter(phone_normalized="")
            .only("id", "phone")
            .iterator(chunk_size=batch_size)
        )
        batch = []
        updated = 0
        for order in qs:
            normalized = normalize_phone(order.phone)
            if not normalized:
                continue
            order.phone_normalized = normalized
            batch.append(order)
            if len(batch) >= batch_size:
                Order.objects.bulk_update(batch, ["phone_normalized"])
                updated += len(batch)
                batch = []
                self.stdout.write(f"  updated {updated} rows...")
        if batch:
            Order.objects.bulk_update(batch, ["phone_normalized"])
            updated += len(batch)
        self.stdout.write(self.style.SUCCESS(f"Done. Backfilled {updated} orders."))
