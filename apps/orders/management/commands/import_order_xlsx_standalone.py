import json

from django.core.management.base import BaseCommand, CommandError

from apps.orders.scripts.import_order_xlsx_standalone import import_orders_from_xlsx


class Command(BaseCommand):
    help = "Import standalone orders từ file xlsx, hỗ trợ dry-run/apply và xuất file lỗi."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="Đường dẫn file xlsx cần import.")
        parser.add_argument("--error-file", default=None, help="Đường dẫn file CSV lỗi output.")
        parser.add_argument("--summary-file", default=None, help="Đường dẫn file JSON summary output.")
        parser.add_argument("--dry-run", action="store_true", help="Chỉ validate, không ghi DB.")
        parser.add_argument("--apply", action="store_true", help="Import thật, có ghi DB.")

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        apply_mode = bool(options.get("apply"))

        if dry_run and apply_mode:
            raise CommandError("Chỉ chọn một trong --dry-run hoặc --apply.")
        if not dry_run and not apply_mode:
            raise CommandError("Bắt buộc chọn --dry-run hoặc --apply.")

        try:
            result = import_orders_from_xlsx(
                file_path=options["file"],
                dry_run=dry_run,
                error_file=options.get("error_file"),
                summary_file=options.get("summary_file"),
            )
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise CommandError(str(exc)) from exc

        payload = {
            "total_rows": result.total_rows,
            "created_count": result.created_count,
            "updated_count": result.updated_count,
            "skipped_count": result.skipped_count,
            "error_count": result.error_count,
            "error_file": result.error_file,
            "summary_file": result.summary_file,
            "mode": "dry-run" if dry_run else "apply",
        }
        self.stdout.write(self.style.SUCCESS("Import order xlsx standalone hoàn tất."))
        self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
