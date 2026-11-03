"""Lấy ghi âm + transcript cuộc gọi của MỘT số điện thoại rồi xuất ra file text.

Đường tắt cho nghiệp vụ "đọc lại toàn bộ cuộc gọi của khách X ngay bây giờ" mà
không phải chờ cron 5' bò qua hàng đợi chung: tự kéo call-history Zenify trong
cửa sổ ngày → lọc đúng SĐT → transcribe cuộc nào chưa có → ghi tất cả ra 1 file.

    python manage.py export_call_transcript --phone 0912345678 --days 30 --out /tmp/kh.txt
    python manage.py export_call_transcript --phone 0912345678 --no-sync      # chỉ đọc DB
    python manage.py export_call_transcript --phone 0912345678 --no-transcribe # không gọi Gemini

Khác task nền `transcribe_pending_call_records`: command này KHÔNG đụng hàng đợi
ưu tiên (chọn đúng record của SĐT được hỏi) nhưng VẪN đi qua quota guard Redis —
chạy tay không được phép đốt lén trần free tier của cron.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from apps.callcenter import services
from apps.callcenter.models import CallRecord, TranscribeStatus
from apps.callcenter.sync_service import ZENIFY_TZ, sync_call_histories
from apps.callcenter.transcribe import (
    TranscribeError,
    TranscribeQuotaExceeded,
    _consume_quota,
    download_recording,
    guess_mime_type,
    transcribe_audio,
)
from apps.common.phone_utils import normalize_phone
from apps.sysconfig import sysconfig

# Zenify cap số bản ghi mỗi response (audit 2026-07-18: backfill khoảng dài bằng
# 1 request có thể mất dữ liệu im lặng) → luôn kéo theo từng ngày.
_SYNC_CHUNK_DAYS = 1
# 9 số cuối là phần bất biến giữa mọi cách viết SĐT VN (0912…, +84912…, 84912…).
_PHONE_SUFFIX_LEN = 9


def build_phone_filter(phone_normalized: str) -> Q:
    """Bắt cả chiều gọi VÀO (khách ở ``caller``) lẫn gọi RA (khách ở ``called``).

    ``caller_normalized`` chỉ được điền cho ``caller`` — matching của pipeline
    dùng mỗi trường này nên cuộc gọi ra không tra được bằng nó (ghi nhận audit
    2026-07-18). Đối chiếu thêm 9 số cuối trên cả 2 cột raw để không sót.
    """
    suffix = phone_normalized[-_PHONE_SUFFIX_LEN:]
    return (
        Q(caller_normalized=phone_normalized)
        | Q(caller__endswith=suffix)
        | Q(called__endswith=suffix)
    )


def render_report(phone: str, records: list[CallRecord]) -> str:
    """Dựng nội dung file: 1 khối / cuộc gọi, transcript nguyên văn."""
    lines = [
        f"TRANSCRIPT CUỘC GỌI — SĐT {phone}",
        f"Xuất lúc: {timezone.localtime().strftime('%d/%m/%Y %H:%M:%S')}",
        f"Tổng số cuộc gọi: {len(records)}",
        "=" * 72,
        "",
    ]
    for index, record in enumerate(records, start=1):
        started = (
            timezone.localtime(record.start_time).strftime("%d/%m/%Y %H:%M:%S")
            if record.start_time
            else "—"
        )
        direction = "Gọi ra" if record.call_type == "1" else "Gọi vào"
        lines += [
            f"[{index}] {started} | {direction} | {record.acd_time}s"
            f" | NV: {record.agent_name or '—'}",
            f"     call_id: {record.call_id}",
            f"     ghi âm: {record.recording_url or '(không có)'}",
            "",
        ]
        if record.transcribe_status == TranscribeStatus.DONE and record.transcript:
            lines += [record.transcript.strip(), ""]
        elif record.transcribe_status == TranscribeStatus.SKIPPED:
            lines += ["     (bỏ qua — gọi nhỡ hoặc đàm thoại quá ngắn)", ""]
        else:
            reason = record.transcribe_error or "chưa transcribe"
            lines += [f"     (không có transcript — {reason})", ""]
        lines += ["-" * 72, ""]
    return "\n".join(lines)


class Command(BaseCommand):
    help = "Xuất ghi âm + transcript mọi cuộc gọi của 1 SĐT ra file text (chạy tay)."

    def add_arguments(self, parser):
        parser.add_argument("--phone", required=True, help="SĐT khách (mọi định dạng).")
        parser.add_argument(
            "--days", type=int, default=7, help="Số ngày lùi về để kéo/lọc cuộc gọi (mặc định 7)."
        )
        parser.add_argument("--out", help="Đường dẫn file xuất (mặc định /tmp/transcript_<sdt>.txt).")
        parser.add_argument(
            "--limit", type=int, default=50, help="Trần số cuộc gọi transcribe trong 1 lần chạy."
        )
        parser.add_argument(
            "--no-sync", action="store_true", help="Không gọi Zenify, chỉ dùng dữ liệu đã có."
        )
        parser.add_argument(
            "--no-transcribe", action="store_true", help="Không gọi Gemini, chỉ xuất cái đã có."
        )
        parser.add_argument(
            "--ignore-quota",
            action="store_true",
            help=(
                "Bỏ qua quota guard Redis (RPM/RPD). CHỈ dùng khi lưu lượng gọi còn thấp "
                "và cần lấy gấp — trần thật của Google vẫn áp, vượt sẽ ăn 429."
            ),
        )

    def handle(self, *args, **options):
        phone = normalize_phone(options["phone"])
        if not phone:
            raise CommandError(f"SĐT không hợp lệ: {options['phone']!r}")

        days = max(int(options["days"]), 1)
        since = timezone.now() - timedelta(days=days)
        out_path = options.get("out") or f"/tmp/transcript_{phone}.txt"

        if not options["no_sync"]:
            self._sync_window(days)

        records = list(
            CallRecord.objects.filter(build_phone_filter(phone))
            .filter(Q(start_time__gte=since) | Q(start_time__isnull=True))
            .order_by("-start_time", "-id")
        )
        if not records:
            raise CommandError(
                f"Không có cuộc gọi nào của {phone} trong {days} ngày gần đây. "
                "Kiểm tra ZENIFY_* đã cấu hình và khoảng ngày có đúng không."
            )
        self.stdout.write(f"Tìm thấy {len(records)} cuộc gọi của {phone}.")

        if not options["no_transcribe"]:
            self._transcribe_missing(
                records,
                limit=max(int(options["limit"]), 1),
                ignore_quota=options["ignore_quota"],
            )
            records = list(
                CallRecord.objects.filter(id__in=[r.id for r in records]).order_by(
                    "-start_time", "-id"
                )
            )

        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(render_report(phone, records))
        done = sum(1 for r in records if r.transcribe_status == TranscribeStatus.DONE)
        self.stdout.write(
            self.style.SUCCESS(f"Đã ghi {out_path} — {done}/{len(records)} cuộc có transcript.")
        )

    def _sync_window(self, days: int) -> None:
        """Kéo call-history từng ngày một (chunk) cho đủ cửa sổ."""
        if not services.zenify_configured():
            raise CommandError("Zenify chưa cấu hình (ZENIFY_DOMAIN/ZENIFY_API_KEY/ZENIFY_API_SECRET).")
        today = timezone.now().astimezone(ZENIFY_TZ).date()
        for offset in range(days, -1, -_SYNC_CHUNK_DAYS):
            day = today - timedelta(days=offset)
            from_dt = datetime(day.year, day.month, day.day, 0, 0, tzinfo=ZENIFY_TZ)
            to_dt = from_dt + timedelta(days=_SYNC_CHUNK_DAYS) - timedelta(minutes=1)
            summary = sync_call_histories(from_dt=from_dt, to_dt=to_dt)
            self.stdout.write(f"  sync {day}: {summary}")

    def _transcribe_missing(
        self, records: list[CallRecord], *, limit: int, ignore_quota: bool = False
    ) -> None:
        """Transcribe tại chỗ những record còn thiếu — lỗi 1 cuộc không chặn cuộc khác."""
        targets = [
            r
            for r in records
            if r.recording_url
            and r.transcribe_status
            not in (TranscribeStatus.DONE, TranscribeStatus.SKIPPED)
        ][:limit]
        if not targets:
            return
        self.stdout.write(f"Transcribe {len(targets)} cuộc còn thiếu…")

        for index, record in enumerate(targets):
            if index:
                # Giãn đều dưới trần RPM thay vì bắn cả loạt rồi ăn 429.
                time.sleep(self._rpm_delay())
            try:
                audio = download_recording(record.recording_url)
                if not ignore_quota:
                    _consume_quota()
                transcript = transcribe_audio(audio, guess_mime_type(record.recording_url))
            except TranscribeQuotaExceeded as exc:
                self.stderr.write(f"  DỪNG — chạm quota Gemini: {exc}")
                return
            except TranscribeError as exc:
                record.transcribe_error = str(exc)
                record.save(update_fields=["transcribe_error", "updated_at"])
                self.stderr.write(f"  lỗi {record.call_id}: {exc}")
                continue
            record.transcript = transcript
            record.transcribe_status = TranscribeStatus.DONE
            record.transcribe_error = ""
            record.save(
                update_fields=["transcript", "transcribe_status", "transcribe_error", "updated_at"]
            )
            self.stdout.write(f"  ok {record.call_id} ({len(transcript)} ký tự)")

    @staticmethod
    def _rpm_delay() -> float:
        return 60.0 / max(int(sysconfig.get("GEMINI_MAX_RPM")), 1)
