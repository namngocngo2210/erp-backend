"""Chạy tay cho tới khi hết hàng đợi transcribe — không đợi cron 5'.

Cron `callcenter-transcribe-pending` claim 8 record/tick và xử lý TUẦN TỰ (mỗi
record 15-20s), nên một tick tốn 2-3 phút và tick kế tiếp thường gặp lock rồi
SKIP. Thực đo prod 2026-07-27: 685 record tồn, throughput ~53/ngày → cần ~13 ngày
trong khi quota Gemini (đã bật billing) dư sức làm trong một giờ.

Command này giữ nguyên mọi quy tắc của tick (thứ tự ưu tiên, download-trước-quota,
transient-không-đốt-attempt, nghỉ khi Google trả 429) — chỉ khác ở chỗ chạy nhiều
luồng và lặp tới khi sạch hàng đợi.

    python manage.py drain_transcribe --dry-run
    python manage.py drain_transcribe --concurrency 4 --rpm 60
    python manage.py drain_transcribe --limit 100          # chạy thử một mẻ nhỏ

Xem docs/superpowers/specs/2026-07-27-transcribe-drain-command-design.md.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connections
from django.db.models import Count

from apps.callcenter.models import CallRecord, TranscribeStatus
from apps.callcenter.transcribe import (
    TranscribeQuotaExceeded,
    claim_pending_batch,
    process_record,
    requeue_batch,
    requeue_stuck_processing,
)
from apps.sysconfig import override_sysconfig, sysconfig


class Command(BaseCommand):
    help = "Transcribe toàn bộ record PENDING (chạy tay, nhiều luồng, tới khi hết hàng đợi)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--concurrency",
            type=int,
            default=4,
            help="Số luồng gọi Gemini song song (mặc định 4; 1 = chạy inline, không tạo thread)",
        )
        parser.add_argument(
            "--limit", type=int, default=None, help="Trần tổng số record; bỏ trống = tới khi hết"
        )
        parser.add_argument(
            "--rpm", type=int, default=None, help="Ghi đè GEMINI_MAX_RPM trong tiến trình này"
        )
        parser.add_argument(
            "--rpd", type=int, default=None, help="Ghi đè GEMINI_MAX_RPD trong tiến trình này"
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Chỉ đếm hàng đợi rồi thoát, không gọi Gemini"
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            self._report_queue()
            return

        if not sysconfig.get("GEMINI_API_KEY"):
            self.stderr.write(self.style.ERROR("GEMINI_API_KEY rỗng — không có gì để chạy."))
            return

        concurrency = max(1, int(options["concurrency"]))
        # Trần quota override chỉ sống trong tiến trình command, không đụng .env
        # của worker: drain một mẻ lớn mà không phải restart cả cụm Celery.
        overrides = {}
        if options["rpm"]:
            overrides["GEMINI_MAX_RPM"] = int(options["rpm"])
        if options["rpd"]:
            overrides["GEMINI_MAX_RPD"] = int(options["rpd"])
        with override_sysconfig(**overrides):
            self._run(concurrency, options["limit"])

    def _run(self, concurrency: int, limit: int | None) -> None:
        stuck = requeue_stuck_processing()
        if stuck:
            self.stdout.write(f"Trả {stuck} record kẹt PROCESSING về PENDING.")

        totals = self._drain(concurrency, limit)
        self.stdout.write(
            self.style.SUCCESS(
                "Xong: {done} transcript, {errors} lỗi{stopped}.".format(
                    stopped=" — DỪNG SỚM vì chạm quota" if totals["quota_stopped"] else "",
                    **totals,
                )
            )
        )
        self._report_queue()

    # ── vòng drain ──────────────────────────────────────────────────────────
    def _drain(self, concurrency: int, limit: int | None) -> dict:
        totals = {"done": 0, "errors": 0, "quota_stopped": False}
        # Batch gấp đôi số luồng: đủ để không luồng nào rỗi giữa hai lần claim,
        # nhưng vẫn nhỏ để lần dừng vì quota chỉ phải trả lại vài record.
        batch_size = concurrency * 2
        stop_event = threading.Event()
        # Mỗi record chỉ được thử MỘT lần trong một lần drain: record lỗi quay về
        # PENDING, không loại ra thì vòng sau bốc lại chính nó — lỗi transient
        # (theo thiết kế không đốt attempt) sẽ lặp vô hạn, command không bao giờ
        # thoát. Lần thử tiếp theo để cron lo, cách đó vài phút mới có ý nghĩa.
        seen: set[int] = set()

        while not stop_event.is_set():
            remaining = None if limit is None else limit - totals["done"] - totals["errors"]
            if remaining is not None and remaining <= 0:
                break

            batch = claim_pending_batch(min(batch_size, remaining or batch_size), exclude_ids=seen)
            if not batch:
                break
            seen.update(batch)

            try:
                results = self._run_batch(batch, concurrency, stop_event)
            finally:
                # Mọi record còn treo PROCESSING (dừng vì quota, thread chết) phải
                # quay về PENDING ngay — không để cron chờ 30' mới vớt.
                requeue_batch(batch)

            totals["done"] += results.count("done")
            totals["errors"] += results.count("error")
            if "quota" in results:
                totals["quota_stopped"] = True
            self.stdout.write(
                f"  đã xử lý {totals['done'] + totals['errors']} "
                f"(ok {totals['done']}, lỗi {totals['errors']})"
            )

        return totals

    def _run_batch(self, batch: list[int], concurrency: int, stop_event) -> list[str]:
        if concurrency == 1:
            return [self._process_one(record_id, stop_event) for record_id in batch]
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            return list(pool.map(lambda rid: self._worker(rid, stop_event), batch))

    def _worker(self, record_id: int, stop_event) -> str:
        try:
            return self._process_one(record_id, stop_event)
        finally:
            # Connection của thread con không ai đóng hộ — bỏ quên là rò slot
            # Postgres qua pgbouncer cho tới khi tiến trình thoát.
            connections.close_all()

    def _process_one(self, record_id: int, stop_event) -> str:
        if stop_event.is_set():
            return "skipped"
        record = CallRecord.objects.filter(id=record_id).first()
        if record is None:  # record bị xoá giữa chừng
            return "skipped"
        try:
            return process_record(record)
        except TranscribeQuotaExceeded as exc:
            stop_event.set()
            self.stderr.write(
                self.style.WARNING(
                    "Dừng vì quota: {}{}".format(
                        exc,
                        f" — đã bật cooldown {settings.GEMINI_QUOTA_COOLDOWN_MINUTES} phút"
                        if exc.cooldown
                        else "",
                    )
                )
            )
            return "quota"

    # ── báo cáo ─────────────────────────────────────────────────────────────
    def _report_queue(self) -> None:
        labels = dict(TranscribeStatus.choices)
        rows = (
            CallRecord.objects.values("transcribe_status")
            .annotate(n=Count("id"))
            .order_by("transcribe_status")
        )
        for row in rows:
            self.stdout.write(
                f"  {labels.get(row['transcribe_status'], '?'):<16} {row['n']}"
            )
        pending = (
            CallRecord.objects.filter(transcribe_status=TranscribeStatus.PENDING)
            .exclude(recording_url="")
            .count()
        )
        self.stdout.write(f"  PENDING có file ghi âm: {pending}")
