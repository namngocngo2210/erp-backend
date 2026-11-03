"""Transcribe file ghi âm cuộc gọi bằng Gemini (Google AI Studio free tier).

Luồng 2 của pipeline (docs/zenify-call-transcripts.md §5). Free tier giới hạn
RPM/RPD → quota guard bằng counter Redis (pattern login-limit Best Express);
chạm trần thì dừng tick, hàng đợi giữ nguyên chờ tick/ngày sau.

SDK: google-genai (user chốt 18/07). Import lazily để app không đòi dependency
khi tính năng tắt (GEMINI_API_KEY rỗng) và để test mock được client.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from collections.abc import Collection
from datetime import timedelta
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.sysconfig import sysconfig

from .models import CallRecord, TranscribeStatus

logger = logging.getLogger(__name__)

# Trần inline data của generate_content ~20MB — chừa lề an toàn.
MAX_AUDIO_BYTES = 19 * 1024 * 1024
DOWNLOAD_TIMEOUT = 60
# Record kẹt PROCESSING quá lâu (worker bị kill giữa chừng) được trả về PENDING.
STUCK_PROCESSING_MINUTES = 30

TRANSCRIBE_PROMPT = (
    "Đây là file ghi âm cuộc gọi giữa nhân viên tư vấn/CSKH và khách hàng, "
    "bằng tiếng Việt. Hãy transcribe toàn bộ hội thoại, phân vai từng lượt nói "
    'theo định dạng "Nhân viên: ..." / "Khách: ...". Chỉ trả về nội dung '
    "transcript, không thêm lời dẫn hay nhận xét."
)

_MIME_BY_EXTENSION = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
}


class TranscribeQuotaExceeded(Exception):
    """Chạm trần RPM/RPD — dừng tick, KHÔNG phải lỗi record.

    ``cooldown=True`` = dừng vì Google trả 429 (quota THẬT cạn) chứ không phải vì
    trần local; lúc đó slot đã được hoàn và cờ cooldown đã bật. Phân biệt để log
    vận hành nói đúng nguyên nhân nghỉ.
    """

    def __init__(self, message: str, *, cooldown: bool = False):
        super().__init__(message)
        self.cooldown = cooldown


class TranscribeError(Exception):
    """Lỗi 1 record (tải file / Gemini) — message nguyên văn lưu vào record.

    ``transient=True`` = lỗi hạ tầng nhất thời (mạng, 429/5xx): KHÔNG đốt lượt
    attempt — Gemini/CDN chập chờn 15' mà đốt 3 attempt qua 3 tick là hàng loạt
    record FAILED vĩnh viễn oan (audit 2026-07-18). Lỗi nội dung (404, file rỗng,
    transcript rỗng) mới đếm attempt.

    ``quota_exhausted=True`` = Gemini trả 429 RESOURCE_EXHAUSTED: quota THẬT đã
    cạn, không phải lỗi của record. Chỉ set từ ``transcribe_audio`` — HTTP 429 lúc
    tải file là Zenify chặn, không liên quan quota Gemini.
    """

    def __init__(self, message: str, *, transient: bool = False, quota_exhausted: bool = False):
        super().__init__(message)
        self.transient = transient
        self.quota_exhausted = quota_exhausted


# Mã HTTP tải file coi là nhất thời (server ghi âm lỗi/chặn tạm) — còn lại (404/410...)
# là file không còn tồn tại, đếm attempt.
_TRANSIENT_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}
# Dấu hiệu lỗi nhất thời trong message SDK Gemini (không có mã lỗi máy đọc ổn định).
_TRANSIENT_GEMINI_MARKERS = (
    "429",
    "500",
    "503",
    "resource_exhausted",
    "unavailable",
    "deadline",
    "timeout",
    "timed out",
    "connection",
)
# Dấu hiệu quota THẬT của Google đã cạn (khác 5xx/mạng): trần local chỉ là phỏng
# đoán, 429 mới là câu trả lời của Google. Con số RPD thật không công bố ổn định
# nên không thể chỉnh trần local cho khớp — phải nghe 429 mà nghỉ.
#
# Google dùng chung 429 cho cả rate-limit-phút lẫn quota-ngày, ở đây gộp làm một:
# batch chạy tuần tự (tải file + Gemini ~10-20s/record) và trần local RPM đã chặn
# trước, nên 429-vì-phút gần như không xảy ra — còn 429-vì-ngày thì đã xảy ra thật
# (2026-07-22). Nếu vận hành thấy nghỉ oan, hạ GEMINI_QUOTA_COOLDOWN_MINUTES.
_QUOTA_GEMINI_MARKERS = ("429", "resource_exhausted")

# Key cờ cooldown (không gắn ngày): TTL tự hết nên chu kỳ nghỉ bám theo thời điểm
# chạm 429, không lệ thuộc mốc reset quota của Google (00:00 PT) vốn lệch múi giờ
# app. Hết TTL thì tick kế tiếp tự probe lại — không cần thao tác tay.
QUOTA_COOLDOWN_KEY = "gemini:transcribe:cooldown"


def guess_mime_type(url: str) -> str:
    path = url.split("?", 1)[0].lower()
    for ext, mime in _MIME_BY_EXTENSION.items():
        if path.endswith(ext):
            return mime
    return "audio/mpeg"


def _consume_quota() -> tuple[str, ...]:
    """Tăng counter phút + ngày; vượt trần thì raise TranscribeQuotaExceeded.

    cache.add trước incr để key luôn có TTL (incr trên key vắng mặt sẽ lỗi hoặc
    tạo key không TTL tuỳ backend).

    Trả về các key đã trừ để ``_refund_quota`` hoàn đúng chỗ: request Gemini có
    timeout 120s nên hoàn toàn có thể vắt qua ranh giới phút, tính lại key từ
    ``now()`` lúc hoàn sẽ trừ oan bucket của phút sau.
    """
    now = timezone.now()
    minute_key, day_key = _minute_key(now), _day_key(now)
    cache.add(minute_key, 0, timeout=120)
    cache.add(day_key, 0, timeout=26 * 3600)
    if cache.incr(minute_key) > sysconfig.get("GEMINI_MAX_RPM"):
        raise TranscribeQuotaExceeded(f"chạm trần {sysconfig.get('GEMINI_MAX_RPM')} RPM")
    if cache.incr(day_key) > sysconfig.get("GEMINI_MAX_RPD"):
        raise TranscribeQuotaExceeded(f"chạm trần {sysconfig.get('GEMINI_MAX_RPD')} request/ngày")
    return (minute_key, day_key)


def _minute_key(now) -> str:
    return f"gemini:transcribe:rpm:{now.strftime('%Y%m%d%H%M')}"


def _day_key(now) -> str:
    return f"gemini:transcribe:rpd:{now.strftime('%Y%m%d')}"


def _refund_quota(keys: tuple[str, ...]) -> None:
    """Hoàn slot đã tiêu khi Google từ chối bằng 429.

    Counter phải incr TRƯỚC khi gọi Gemini (không thì N request song song cùng
    lọt qua trần), nhưng request bị 429 thì Google không phục vụ — giữ slot đã
    trừ nghĩa là trần local tự tụt xuống 0 bằng chính các lần thất bại (sự cố
    2026-07-22: counter chạm 300 dù chỉ 23 file transcribe được).
    """
    for key in keys:
        try:
            cache.decr(key)
        except ValueError:  # key vừa hết TTL — không còn gì để hoàn
            continue


def _in_quota_cooldown() -> bool:
    return cache.get(QUOTA_COOLDOWN_KEY) is not None


def _enter_quota_cooldown() -> None:
    cache.set(QUOTA_COOLDOWN_KEY, 1, timeout=settings.GEMINI_QUOTA_COOLDOWN_MINUTES * 60)


def allowed_recording_hosts() -> tuple[str, ...]:
    """Host được phép tải ghi âm: env ZENIFY_RECORDING_HOSTS, mặc định suy từ
    ZENIFY_API_BASE_URL (host đó + mọi subdomain của domain mẹ, vd api.zenify.vn →
    zenify.vn/*.zenify.vn — file ghi âm nằm ở CDN cùng domain)."""
    configured = tuple(
        h.strip().lower().lstrip(".")
        for h in (getattr(settings, "ZENIFY_RECORDING_HOSTS", None) or ())
        if h and h.strip()
    )
    if configured:
        return configured
    base_host = (urlsplit(getattr(settings, "ZENIFY_API_BASE_URL", "") or "").hostname or "").lower()
    if not base_host:
        return ()
    labels = base_host.split(".")
    parent = ".".join(labels[1:]) if len(labels) >= 3 else base_host
    return (base_host, parent)


def _host_matches(host: str, allowed: str) -> bool:
    return host == allowed or host.endswith("." + allowed)


def _is_private_address(value: str) -> bool:
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not addr.is_global


def assert_recording_url_allowed(url: str) -> None:
    """SSRF guard (audit 2026-09-23 L5): `recording_url` tới từ payload Zenify —
    chỉ tải https tới host trong allowlist và không trỏ về IP nội bộ (kể cả qua DNS).
    Raise TranscribeError (không transient: URL sai là lỗi nội dung, đếm attempt)."""
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        raise TranscribeError(f"URL ghi âm không hợp lệ (chỉ nhận https): {url!r}")
    allowed = allowed_recording_hosts()
    if not any(_host_matches(host, a) for a in allowed):
        raise TranscribeError(f"Host ghi âm không nằm trong allowlist: {host}")
    if _is_private_address(host):
        raise TranscribeError(f"URL ghi âm trỏ về địa chỉ nội bộ: {host}")
    try:
        resolved = {info[4][0] for info in socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)}
    except socket.gaierror as exc:
        raise TranscribeError(f"Không phân giải được host ghi âm {host}: {exc}", transient=True) from exc
    private = sorted(ip for ip in resolved if _is_private_address(ip))
    if private:
        raise TranscribeError(f"Host ghi âm {host} phân giải về địa chỉ nội bộ: {private[0]}")


def download_recording(url: str) -> bytes:
    assert_recording_url_allowed(url)
    try:
        resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT)
    except requests.RequestException as exc:
        raise TranscribeError(f"Không tải được file ghi âm: {exc}", transient=True) from exc
    if resp.status_code != 200:
        raise TranscribeError(
            f"Tải file ghi âm thất bại: HTTP {resp.status_code}",
            transient=resp.status_code in _TRANSIENT_HTTP_CODES,
        )
    content = resp.content or b""
    if not content:
        raise TranscribeError("File ghi âm rỗng.")
    if len(content) > MAX_AUDIO_BYTES:
        raise TranscribeError(
            f"File ghi âm {len(content)} bytes vượt trần {MAX_AUDIO_BYTES} bytes (inline Gemini)."
        )
    return content


def transcribe_audio(audio: bytes, mime_type: str) -> str:
    """Gọi Gemini generate_content với audio inline. Trả transcript (non-empty)."""
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - thiếu dependency là lỗi deploy
        raise TranscribeError(f"Thiếu thư viện google-genai: {exc}") from exc

    # timeout 120s (ms theo SDK): 1 request treo không được ăn cả tick tới soft limit.
    client = genai.Client(
        api_key=sysconfig.get("GEMINI_API_KEY"),
        http_options=types.HttpOptions(timeout=120_000),
    )
    try:
        response = client.models.generate_content(
            model=sysconfig.get("GEMINI_TRANSCRIBE_MODEL"),
            contents=[
                types.Part.from_bytes(data=audio, mime_type=mime_type),
                TRANSCRIBE_PROMPT,
            ],
        )
    except Exception as exc:  # noqa: BLE001 — lỗi SDK/API giữ nguyên văn cho record
        message = str(exc)
        lowered = message.lower()
        transient = any(marker in lowered for marker in _TRANSIENT_GEMINI_MARKERS)
        quota = any(marker in lowered for marker in _QUOTA_GEMINI_MARKERS)
        raise TranscribeError(
            f"Gemini lỗi: {message}", transient=transient, quota_exhausted=quota
        ) from exc

    text = (getattr(response, "text", None) or "").strip()
    if not text:
        raise TranscribeError("Gemini trả về transcript rỗng.")
    return text


def requeue_stuck_processing() -> int:
    """Trả record kẹt PROCESSING (worker chết giữa chừng) về PENDING."""
    threshold = timezone.now() - timedelta(minutes=STUCK_PROCESSING_MINUTES)
    return CallRecord.objects.filter(
        transcribe_status=TranscribeStatus.PROCESSING, updated_at__lt=threshold
    ).update(transcribe_status=TranscribeStatus.PENDING)


def claim_pending_batch(limit: int, exclude_ids: Collection[int] = ()) -> list[int]:
    """Nhận batch record pending: khoá dòng (skip_locked — 2 worker không giành
    nhau), set PROCESSING rồi trả id để xử lý ngoài transaction (giữ transaction
    ngắn — phần tải audio + Gemini chậm không được ôm lock).

    ``exclude_ids`` loại các record đã xử lý trong CÙNG lần chạy. Bắt buộc với
    vòng drain: record lỗi được trả về PENDING, không loại ra thì lần claim kế
    tiếp bốc lại chính nó — lỗi transient (không đốt attempt) sẽ lặp vô hạn.
    Tick Celery không cần vì mỗi tick chỉ claim một lần.
    """
    with transaction.atomic():
        ids = list(
            CallRecord.objects.select_for_update(skip_locked=True)
            .filter(transcribe_status=TranscribeStatus.PENDING)
            .exclude(recording_url="")
            .exclude(id__in=exclude_ids)
            # Đơn đã khớp ưu tiên trước (nulls_last), MỚI trước: quota ngày chỉ
            # GEMINI_MAX_RPD — ưu tiên cũ-trước thì backlog/backfill (nhiều URL đã
            # hết hạn retention) chiếm sạch quota, cuộc gọi MỚI không bao giờ có
            # transcript (audit 2026-07-18).
            .order_by(F("order_id").desc(nulls_last=True), "-id")
            .values_list("id", flat=True)[:limit]
        )
        if ids:
            CallRecord.objects.filter(id__in=ids).update(
                transcribe_status=TranscribeStatus.PROCESSING
            )
    return ids


def _finish_record(record: CallRecord, transcript: str) -> None:
    record.transcript = transcript
    record.transcribe_status = TranscribeStatus.DONE
    record.transcribe_error = ""
    record.save(update_fields=["transcript", "transcribe_status", "transcribe_error", "updated_at"])


def _fail_record(record: CallRecord, error: str, *, transient: bool = False) -> None:
    if not transient:
        record.transcribe_attempts += 1
    record.transcribe_error = error
    record.transcribe_status = (
        TranscribeStatus.FAILED
        if record.transcribe_attempts >= settings.GEMINI_MAX_ATTEMPTS
        else TranscribeStatus.PENDING
    )
    record.save(
        update_fields=[
            "transcribe_attempts",
            "transcribe_error",
            "transcribe_status",
            "updated_at",
        ]
    )


def requeue_batch(record_ids: list[int]) -> int:
    """Trả phần batch chưa xử lý về PENDING (dừng tick giữa chừng)."""
    return CallRecord.objects.filter(
        id__in=record_ids, transcribe_status=TranscribeStatus.PROCESSING
    ).update(transcribe_status=TranscribeStatus.PENDING)


def process_record(record: CallRecord) -> str:
    """Xử lý 1 record: tải file → tiêu quota → gọi Gemini → ghi kết quả.

    Trả ``"done"`` hoặc ``"error"`` (lỗi đã được ghi vào record). Raise
    ``TranscribeQuotaExceeded`` khi chạm trần local (chưa tiêu gì) hoặc khi Google
    trả 429 — trường hợp sau đã hoàn slot + bật cooldown và cờ ``cooldown=True``.

    Dùng chung cho tick Celery (tuần tự) và command drain (nhiều luồng): mọi quy
    tắc attempt/quota nằm ở đây, không nơi nào khác.
    """
    # Download TRƯỚC — quota chỉ tiêu khi chắc chắn sắp gọi Gemini. Đốt quota
    # trước download thì record URL chết (Zenify xoá theo retention — backfill
    # chắc chắn dính) đốt RPD mà không hề gọi Gemini (audit 2026-07-18).
    try:
        audio = download_recording(record.recording_url)
    except TranscribeError as exc:
        _fail_record(record, str(exc), transient=exc.transient)
        logger.warning("transcribe lỗi tải file call_id=%s: %s", record.call_id, exc)
        return "error"

    consumed = _consume_quota()  # raise TranscribeQuotaExceeded — caller dừng vòng

    try:
        transcript = transcribe_audio(audio, guess_mime_type(record.recording_url))
    except TranscribeError as exc:
        if exc.quota_exhausted:
            # Google đã nói hết quota — slot local vừa trừ chưa được phục vụ,
            # hoàn lại rồi nghỉ trọn chu kỳ. Record không có lỗi gì: caller trả nó
            # về PENDING nguyên vẹn, không đốt attempt.
            _refund_quota(consumed)
            _enter_quota_cooldown()
            raise TranscribeQuotaExceeded(str(exc), cooldown=True) from exc
        _fail_record(record, str(exc), transient=exc.transient)
        logger.warning("transcribe lỗi call_id=%s: %s", record.call_id, exc)
        return "error"

    _finish_record(record, transcript)
    return "done"


def transcribe_pending_records(limit: int | None = None) -> dict:
    """Xử lý tối đa ``limit`` record pending trong 1 tick. Lỗi 1 record không
    chặn record khác; chạm quota thì trả record chưa xử lý về PENDING và dừng."""
    summary = {"claimed": 0, "done": 0, "errors": 0, "quota_stopped": False, "requeued_stuck": 0}
    if not sysconfig.get("GEMINI_API_KEY"):
        return summary

    summary["requeued_stuck"] = requeue_stuck_processing()
    # Chặn TRƯỚC khi claim: hết quota mà vẫn claim thì mỗi tick ghi 2 lượt UPDATE
    # cả batch (PROCESSING → PENDING) và tải thật 1 file ghi âm để rồi vứt —
    # 288 tick/ngày (sự cố 2026-07-22).
    if _in_quota_cooldown():
        summary["quota_stopped"] = True
        return summary

    batch = claim_pending_batch(limit or settings.GEMINI_TRANSCRIBE_BATCH)
    summary["claimed"] = len(batch)

    for index, record_id in enumerate(batch):
        record = CallRecord.objects.filter(id=record_id).first()
        if record is None:  # pragma: no cover - record bị xoá giữa chừng
            continue
        try:
            outcome = process_record(record)
        except TranscribeQuotaExceeded as exc:
            # Trả cả record hiện tại lẫn phần batch chưa xử lý về PENDING rồi dừng tick.
            remaining = requeue_batch(batch[index:])
            summary["quota_stopped"] = True
            if exc.cooldown:
                logger.warning(
                    "transcribe nghỉ %s phút vì Gemini hết quota: %s (%s record chờ lại)",
                    settings.GEMINI_QUOTA_COOLDOWN_MINUTES,
                    exc,
                    remaining,
                )
            else:
                logger.info("transcribe dừng vì quota: %s (%s record chờ tick sau)", exc, remaining)
            break
        summary["done" if outcome == "done" else "errors"] += 1
    return summary
