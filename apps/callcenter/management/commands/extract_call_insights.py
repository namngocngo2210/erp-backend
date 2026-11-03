"""Chạy tay bước extract chân dung khách từ transcript.

Dùng để backfill transcript cũ và để SOI kết quả trước khi bật kill switch
trên prod: `--dry-run` in ra đúng thứ Gemini trả về mà không ghi gì.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.callcenter.extract import ExtractError, extract_insights, extract_pending_insights
from apps.callcenter.models import CallRecord, ExtractStatus, TranscribeStatus


class Command(BaseCommand):
    help = "Trích xuất chân dung khách từ transcript cuộc gọi và điền vào OrderAssessment."

    def add_arguments(self, parser):
        parser.add_argument("--order", type=int, help="Chỉ xử lý cuộc gọi của đơn này")
        parser.add_argument("--call-record", type=int, help="Chỉ xử lý 1 cuộc gọi theo id")
        parser.add_argument("--limit", type=int, default=None, help="Số record tối đa")
        parser.add_argument(
            "--re-extract",
            action="store_true",
            help="Đưa record đã DONE/FAILED/SKIPPED về PENDING trước khi chạy",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Chỉ in kết quả Gemini, KHÔNG ghi assessment và không đổi trạng thái",
        )

    def handle(self, *args, **options):
        queryset = CallRecord.objects.filter(
            transcribe_status=TranscribeStatus.DONE, order__isnull=False
        )
        if options["order"]:
            queryset = queryset.filter(order_id=options["order"])
        if options["call_record"]:
            queryset = queryset.filter(id=options["call_record"])

        if options["dry_run"]:
            self._dry_run(queryset, options["limit"])
            return

        if options["re_extract"]:
            # Chỉ reset các trạng thái terminal (DONE/FAILED/SKIPPED). Không đụng tới
            # PROCESSING vì record đó đang được beat extract_pending_call_insights xử lý
            # dở dang — nếu kéo về PENDING ở đây, batch chạy ngay sau sẽ claim lại và
            # gọi Gemini lần 2 trên cùng transcript.
            reset = queryset.filter(
                extract_status__in=[
                    ExtractStatus.DONE,
                    ExtractStatus.FAILED,
                    ExtractStatus.SKIPPED,
                ]
            ).update(extract_status=ExtractStatus.PENDING, extract_attempts=0, extract_error="")
            self.stdout.write(f"Đã đưa {reset} record về PENDING.")

        summary = extract_pending_insights(limit=options["limit"])
        self.stdout.write(self.style.SUCCESS(json.dumps(summary, ensure_ascii=False)))

    def _dry_run(self, queryset, limit):
        records = list(queryset.filter(extract_status=ExtractStatus.PENDING)[: limit or 10])
        if not records:
            records = list(queryset[: limit or 10])
        for record in records:
            try:
                _raw, clean, rejected = extract_insights(record.transcript or "")
            except ExtractError as exc:
                self.stdout.write(self.style.ERROR(f"[{record.call_id}] {exc}"))
                continue
            self.stdout.write(f"[{record.call_id}] order={record.order_id}")
            self.stdout.write(f"  clean={json.dumps(clean, ensure_ascii=False)}")
            if rejected:
                self.stdout.write(
                    self.style.WARNING(f"  rejected={json.dumps(rejected, ensure_ascii=False)}")
                )
