"""Chạy tay bước chấm chất lượng cuộc gọi.

Dùng để calibrate rubric trước khi bật kill switch trên prod: `--dry-run` in ra
đúng thứ Gemini trả về (điểm từng tiêu chí + trích dẫn + phần bị loại) mà không
ghi gì. Đây là công cụ chính của Sprint 1 — team lead nghe tay vài cuộc rồi đối
chiếu với bảng in ra.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand
from django.db.models.functions import Length

from apps.callcenter.models import CallRecord, ScoreStatus, TranscribeStatus
from apps.callcenter.qa_rubric import CRITERION_BY_CODE, compute_scores
from apps.callcenter.scoring import (
    ScoreError,
    drain_pending_calls,
    score_pending_calls,
    score_transcript,
)
from apps.sysconfig import sysconfig


class Command(BaseCommand):
    help = "Chấm chất lượng tư vấn của cuộc gọi từ transcript theo khung kịch bản mẫu."

    def add_arguments(self, parser):
        parser.add_argument("--call-record", type=int, help="Chỉ xử lý 1 cuộc gọi theo id")
        parser.add_argument("--order", type=int, help="Chỉ xử lý cuộc gọi của đơn này")
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Chấm đúng MỘT lô cỡ này. Bỏ trống = cuốn chiếu tới khi hết hàng đợi",
        )
        parser.add_argument(
            "--max-minutes",
            type=int,
            default=None,
            help="Trần thời gian khi cuốn chiếu (phút). 0 = không giới hạn",
        )
        parser.add_argument(
            "--re-score",
            action="store_true",
            help="Đưa record đã DONE/FAILED/SKIPPED về PENDING trước khi chạy",
        )
        parser.add_argument(
            "--re-score-skipped",
            action="store_true",
            help="Đưa về PENDING các record BỊ BỎ QUA mà nay đã lọt ngưỡng "
            "(dùng sau khi hạ QA_MIN_DURATION_SECONDS). Không đụng record đã chấm",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Chỉ in kết quả Gemini, KHÔNG ghi điểm và không đổi trạng thái",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Chạy dù QA_SCORING_ENABLED=0 — để chấm một lô có kiểm soát "
            "trước khi bật chạy tự động",
        )

    def handle(self, *args, **options):
        queryset = CallRecord.objects.filter(transcribe_status=TranscribeStatus.DONE)
        if options["order"]:
            queryset = queryset.filter(order_id=options["order"])
        if options["call_record"]:
            queryset = queryset.filter(id=options["call_record"])

        if options["dry_run"]:
            self._dry_run(queryset, options["limit"])
            return

        if options["re_score"]:
            # Chỉ reset trạng thái terminal. KHÔNG đụng PROCESSING: record đó
            # đang được beat xử lý dở, kéo về PENDING ở đây là gọi Gemini lần 2
            # trên cùng transcript (cùng bẫy đã gặp ở extract).
            reset = queryset.filter(
                score_status__in=[ScoreStatus.DONE, ScoreStatus.FAILED, ScoreStatus.SKIPPED]
            ).update(score_status=ScoreStatus.PENDING, score_attempts=0, score_error="")
            self.stdout.write(f"Đã đưa {reset} record về PENDING.")

        if options["re_score_skipped"]:
            # Hạ ngưỡng (vd QA_MIN_DURATION_SECONDS 120 → 60) KHÔNG tự chấm lại:
            # hàng đợi chỉ lấy PENDING, còn record đã SKIPPED thì nằm im mãi. Cờ
            # này là đường kéo chúng về — nhưng CHỈ chúng: `--re-score` ở trên
            # kéo cả nhóm DONE, tức gọi lại Gemini cho hàng nghìn cuộc đã chấm
            # mà kết quả không đổi (đo prod 29/08: 1.712 cuộc đã chấm).
            #
            # Lọc sẵn theo đúng luật của scoring._is_eligible thay vì kéo hết:
            # record vẫn dưới ngưỡng mà về PENDING thì mỗi lô claim lại chiếm
            # slot rồi bị SKIP lại, đẩy phần thật sự cần chấm ra sau.
            eligible_ids = list(
                queryset.filter(
                    score_status=ScoreStatus.SKIPPED,
                    acd_time__gte=sysconfig.get("QA_MIN_DURATION_SECONDS"),
                )
                .annotate(transcript_len=Length("transcript"))
                .filter(transcript_len__gte=sysconfig.get("QA_MIN_TRANSCRIPT_CHARS"))
                .values_list("id", flat=True)
            )
            reset = CallRecord.objects.filter(id__in=eligible_ids).update(
                score_status=ScoreStatus.PENDING, score_attempts=0, score_error=""
            )
            self.stdout.write(
                f"Đã đưa {reset} record BỊ BỎ QUA về PENDING "
                f"(ngưỡng hiện tại: {sysconfig.get('QA_MIN_DURATION_SECONDS')}s / "
                f"{sysconfig.get('QA_MIN_TRANSCRIPT_CHARS')} ký tự)."
            )

        # Có --limit = chấm đúng một lô cỡ đó (dùng khi calibrate, muốn kiểm soát
        # số cuộc). Không có = cuốn chiếu tới khi hết hàng đợi, đúng như beat.
        if options["limit"] is None:
            summary = drain_pending_calls(
                force=options["force"], max_minutes=options["max_minutes"]
            )
        else:
            summary = score_pending_calls(limit=options["limit"], force=options["force"])
        if summary["claimed"] == 0 and not options["force"] and not sysconfig.get("QA_SCORING_ENABLED"):
            self.stdout.write(
                self.style.WARNING(
                    "QA_SCORING_ENABLED=0 nên không chấm gì. Thêm --force để chạy "
                    "một lô có kiểm soát mà không cần bật chạy tự động."
                )
            )
        self.stdout.write(self.style.SUCCESS(json.dumps(summary, ensure_ascii=False)))

    def _dry_run(self, queryset, limit):
        records = list(queryset.filter(score_status=ScoreStatus.PENDING)[: limit or 10])
        if not records:
            records = list(queryset[: limit or 10])
        for record in records:
            try:
                _raw, clean, rejected = score_transcript(record.transcript or "")
            except ScoreError as exc:
                self.stdout.write(self.style.ERROR(f"[{record.call_id}] {exc}"))
                continue

            total, blocks = compute_scores(clean["criteria"])
            self.stdout.write(
                f"\n[{record.call_id}] order={record.order_id} "
                f"thời lượng={record.acd_time}s → TỔNG {total}/100"
            )
            self.stdout.write(f"  khối: {json.dumps(blocks, ensure_ascii=False)}")
            for code, item in clean["criteria"].items():
                criterion = CRITERION_BY_CODE[code]
                mark = "na" if item["na"] else item["score"]
                self.stdout.write(f"  {code:<22} {mark}  {criterion.label}")
                if item.get("evidence"):
                    self.stdout.write(f"      ↳ {item['evidence'][:160]}")
            for flag in clean["red_flags"]:
                self.stdout.write(self.style.ERROR(f"  CỜ ĐỎ {flag['code']}: {flag['evidence']}"))
            if clean["summary"]:
                self.stdout.write(f"  nhận xét: {clean['summary']}")
            if clean["coaching"]:
                self.stdout.write(f"  cần sửa: {clean['coaching']}")
            if rejected:
                # Phần bị loại là tín hiệu quan trọng nhất khi calibrate: nhiều
                # dòng ở đây = model đang bịa trích dẫn, phải sửa prompt.
                self.stdout.write(
                    self.style.WARNING(f"  loại: {json.dumps(rejected, ensure_ascii=False)}")
                )
