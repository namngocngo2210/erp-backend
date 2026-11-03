from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import Sum

from apps.inventory.models import StockItem, StockMovement


class Command(BaseCommand):
    help = "Rebuild StockItem balances from the immutable StockMovement ledger."

    @transaction.atomic
    def handle(self, *args, **options):
        # Chặn ghi movement mới trong lúc rebuild — nếu không, movement commit
        # sau khi SUM được tính sẽ bị số dư cũ ghi đè.
        with connection.cursor() as cursor:
            cursor.execute("LOCK TABLE stock_movements IN SHARE MODE;")
        sums = (
            StockMovement.objects.values("warehouse_id", "product_id", "state")
            .annotate(total=Sum("qty_delta"))
        )
        updated = 0
        for row in sums:
            StockItem.objects.update_or_create(
                warehouse_id=row["warehouse_id"],
                product_id=row["product_id"],
                state=row["state"],
                defaults={"quantity": row["total"] or 0},
            )
            updated += 1
        self.stdout.write(self.style.SUCCESS(f"Rebuilt {updated} stock item(s)."))
