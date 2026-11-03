from django.core.management.base import BaseCommand

from apps.users.rbac_catalog import sync_permissions


class Command(BaseCommand):
    help = "Đồng bộ bảng Permission khớp PERMISSION_CATALOG (idempotent)."

    def handle(self, *args, **options):
        created, deleted = sync_permissions()
        self.stdout.write(
            self.style.SUCCESS(f"RBAC permissions synced: +{created} created, -{deleted} deleted")
        )
