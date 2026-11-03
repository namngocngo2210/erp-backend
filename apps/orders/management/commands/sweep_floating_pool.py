from django.core.management.base import BaseCommand

from apps.orders.application.services.floating_pool_service import FloatingPoolService


class Command(BaseCommand):
    help = "Quét kho số thả nổi: đưa đơn bỏ bê vào kho + gỡ entry đã được chăm lại."

    def handle(self, *args, **options):
        summary = FloatingPoolService.sweep()
        self.stdout.write(self.style.SUCCESS(f"floating-pool sweep: {summary}"))
