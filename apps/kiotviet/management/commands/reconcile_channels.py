"""CLI: đối soát new->old KiotViet channel cho ngày/khoảng thời gian tuỳ chỉnh.

Tương tự cron ``apps.kiotviet.tasks.reconcile_paired_channels`` nhưng chạy ĐỒNG BỘ
(tuần tự từng cặp, stream output ra stdout) và cho phép chỉ định ngày. Gọi THẲNG
``SyncInvoicesService`` qua ``infrastructure.services.reconcile`` — command này chỉ là
lớp CLI mỏng, KHÔNG chứa logic đối soát.

Ví dụ:
  python manage.py reconcile_channels                      # trong ngày hôm nay
  python manage.py reconcile_channels --date 2026-06-15    # 1 ngày cụ thể
  python manage.py reconcile_channels --from-dt "2026-06-01 00:00:00" --to-dt "2026-06-18 23:59:59"
  python manage.py reconcile_channels --date 2026-06-15 --source-kv-id 4 --target-kv-id 5
  python manage.py reconcile_channels --source-kv-id 4 --source-branch-id 231001   # 1 cặp kho trong map
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.kiotviet.infrastructure.services.reconcile import (
    iter_reconcile_pairs,
    reconcile_one_pair,
)


class Command(BaseCommand):
    help = "Đối soát new->old KiotViet channel cho ngày/khoảng tuỳ chỉnh (gọi SyncInvoicesService)."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="Ngày YYYY-MM-DD (00:00 → 23:59:59). Mặc định: hôm nay.")
        parser.add_argument(
            "--from-dt", dest="from_dt",
            help="Mốc đầu 'YYYY-MM-DD HH:MM:SS' (đi kèm --to-dt; ưu tiên hơn --date).",
        )
        parser.add_argument("--to-dt", dest="to_dt", help="Mốc cuối 'YYYY-MM-DD HH:MM:SS'.")
        parser.add_argument(
            "--source-kv-id", dest="source_kv_id", type=int,
            help="Chỉ chạy cặp có source (kênh mới) kv id này.",
        )
        parser.add_argument(
            "--target-kv-id", dest="target_kv_id", type=int,
            help="Chỉ chạy cặp có target (kênh cũ) kv id này.",
        )
        parser.add_argument(
            "--source-branch-id", dest="source_branch_id",
            help="Chỉ chạy cặp kho có kho nguồn (kênh mới) này — dùng khi kênh có nhiều cặp trong old_sync_branch_map.",
        )
        parser.add_argument(
            "--dry-run", dest="dry_run", action="store_true",
            help="Chỉ log dự định, KHÔNG DELETE/CREATE hóa đơn bên B.",
        )

    def _resolve_window(self, options) -> tuple[str, str]:
        from_dt = options.get("from_dt")
        to_dt = options.get("to_dt")
        if from_dt and to_dt:
            return from_dt, to_dt
        if from_dt or to_dt:
            raise CommandError("--from-dt và --to-dt phải đi cùng nhau.")
        date_arg = options.get("date")
        if date_arg:
            return f"{date_arg} 00:00:00", f"{date_arg} 23:59:59"
        now = timezone.localtime()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        fmt = "%Y-%m-%d %H:%M:%S"
        return start.strftime(fmt), now.strftime(fmt)

    def handle(self, *args, **options):
        window_from, window_to = self._resolve_window(options)
        only_source = options.get("source_kv_id")
        only_target = options.get("target_kv_id")
        only_source_branch = (options.get("source_branch_id") or "").strip()
        dry_run = bool(options.get("dry_run"))
        self.stdout.write(self.style.NOTICE(
            f"Reconcile window: {window_from} -> {window_to}{' [DRY-RUN]' if dry_run else ''}"
        ))

        def _log(channel: str, msg: str) -> None:
            (self.stderr if channel == "stderr" else self.stdout).write(msg)

        ran = 0
        any_failed = False
        for pair in iter_reconcile_pairs():
            if pair["skip"]:
                self.stderr.write(self.style.WARNING(f"SKIP {pair['pair']}: {pair['reason']}"))
                continue
            if only_source and pair["source_kv_id"] != only_source:
                continue
            if only_target and pair["target_kv_id"] != only_target:
                continue
            if only_source_branch and pair["source_branch_id"] != only_source_branch:
                continue

            self.stdout.write(self.style.NOTICE(f"\n===== Pair {pair['pair']} ====="))
            result = reconcile_one_pair(
                source_kv_id=pair["source_kv_id"],
                source_branch_id=pair["source_branch_id"],
                target_kv_id=pair["target_kv_id"],
                target_branch_id=pair["target_branch_id"],
                from_dt=window_from,
                to_dt=window_to,
                dry_run=dry_run,
                extra_log=_log,
            )
            ran += 1
            failed = result.get("failed_modes") or []
            if failed:
                any_failed = True
                self.stderr.write(self.style.ERROR(f"{pair['pair']}: MODE FAILED -> {failed}"))
            self.stdout.write(self.style.SUCCESS(f"{pair['pair']} modes: {result['modes']}"))

        if ran == 0:
            self.stdout.write(self.style.WARNING("Không có cặp kênh nào để đối soát."))
        if any_failed:
            raise CommandError("Có mode đối soát thất bại — xem log ở trên.")
        self.stdout.write(self.style.SUCCESS(f"Done: {ran} pair(s)."))
