from django.core.management.base import BaseCommand

from apps.orders.application.services.lead_close_service import LeadCloseService


class Command(BaseCommand):
    help = (
        "Mở lại các lead bị job close_converted_leads đóng oan: SKU của lead "
        "không nằm trong hóa đơn được khớp (sự cố 2026-09-08). Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        summary = LeadCloseService.reopen_mismatched(dry_run=options["dry_run"])
        self.stdout.write(
            f"scanned={summary['scanned']} reopened={summary['reopened']} "
            f"dry_run={summary['dry_run']}"
        )
        if summary["reopened_ids"]:
            self.stdout.write("ids=" + ",".join(str(i) for i in summary["reopened_ids"]))
