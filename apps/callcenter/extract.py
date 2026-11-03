"""Trích xuất chân dung & nhu cầu khách từ transcript bằng Gemini.

Luồng 3 của pipeline cuộc gọi (sau sync + transcribe). Text-only nên rẻ và
nhanh hơn transcribe nhiều, và chạy lại được bất kỳ lúc nào mà không cần audio
gốc — URL ghi âm Zenify hết hạn theo retention của họ.

Hợp đồng dữ liệu (field nào, code nào hợp lệ) thuộc về apps.orders — module này
chỉ biết cách nói chuyện với Gemini. Xem
docs/superpowers/specs/2026-07-24-call-transcript-assessment-autofill-design.md
"""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.core.cache import cache
from django.db import transaction

from apps.orders.ai_extract_contract import (
    build_extract_response_schema,
    build_prompt_catalog_text,
    normalize_extracted,
)
from apps.sysconfig import sysconfig

from .models import CallRecord, ExtractStatus, TranscribeStatus

logger = logging.getLogger(__name__)

# Cờ cooldown riêng của luồng extract — không dùng chung với transcribe để 429
# của luồng này không khoá luôn việc transcribe (và ngược lại).
EXTRACT_COOLDOWN_KEY = "gemini:extract:cooldown"

_TRANSIENT_MARKERS = (
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
_QUOTA_MARKERS = ("429", "resource_exhausted")

_PROMPT_HEADER = (
    "Đây là transcript một cuộc gọi giữa nhân viên tư vấn và khách hàng, bằng "
    "tiếng Việt, về sản phẩm ghế/máy massage. Hãy trích xuất thông tin chân dung "
    "và nhu cầu của KHÁCH HÀNG.\n\n"
    "QUY TẮC BẮT BUỘC:\n"
    "- CHỈ điền khi khách nói rõ trong transcript. TUYỆT ĐỐI không suy đoán.\n"
    "- Không chắc chắn thì để null (với mảng thì để mảng rỗng).\n"
    "- Mỗi field có điền giá trị PHẢI kèm một mục trong `evidence` với trích dẫn "
    "NGUYÊN VĂN câu nói của khách làm căn cứ. Field không có trích dẫn sẽ bị bỏ.\n"
    "- Chỉ dùng đúng các mã dưới đây, không tự chế mã mới.\n\n"
    "DANH MỤC MÃ HỢP LỆ (mã = ý nghĩa):\n"
)


class ExtractError(Exception):
    """Lỗi extract 1 record. Message giữ nguyên văn để lưu vào record.

    ``transient=True`` = lỗi hạ tầng nhất thời (mạng, 429/5xx): KHÔNG đốt lượt
    attempt — cùng bài học audit 2026-07-18 của transcribe, Gemini chập chờn
    15 phút mà đốt attempt là hàng loạt record FAILED oan.

    ``quota_exhausted=True`` = Google trả 429: nghỉ trọn chu kỳ cooldown thay vì
    dội request vào tường.
    """

    def __init__(self, message: str, *, transient: bool = False, quota_exhausted: bool = False):
        super().__init__(message)
        self.transient = transient
        self.quota_exhausted = quota_exhausted


def build_prompt(transcript: str) -> str:
    return f"{_PROMPT_HEADER}{build_prompt_catalog_text()}\n\nTRANSCRIPT:\n{transcript}"


def _generate_json(prompt: str) -> str:
    """Gọi Gemini với structured output. Tách hàm riêng để test mock được."""
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - thiếu dependency là lỗi deploy
        raise ExtractError(f"Thiếu thư viện google-genai: {exc}") from exc

    client = genai.Client(
        api_key=sysconfig.get("GEMINI_API_KEY"),
        http_options=types.HttpOptions(timeout=120_000),
    )
    response = client.models.generate_content(
        model=sysconfig.get("GEMINI_EXTRACT_MODEL"),
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=build_extract_response_schema(),
            # temperature=0: trích xuất là việc đọc hiểu, không cần sáng tạo.
            temperature=0,
        ),
    )
    return getattr(response, "text", None) or ""


def extract_insights(transcript: str) -> tuple[dict, dict, dict]:
    """Trả ``(raw, clean, rejected)`` cho 1 transcript."""
    try:
        text = _generate_json(build_prompt(transcript))
    except ExtractError:
        raise
    except Exception as exc:  # noqa: BLE001 — lỗi SDK/API giữ nguyên văn cho record
        message = str(exc)
        lowered = message.lower()
        raise ExtractError(
            f"Gemini lỗi: {message}",
            transient=any(marker in lowered for marker in _TRANSIENT_MARKERS),
            quota_exhausted=any(marker in lowered for marker in _QUOTA_MARKERS),
        ) from exc

    text = (text or "").strip()
    if not text:
        raise ExtractError("Gemini trả về response rỗng.")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExtractError(f"Gemini trả về JSON không hợp lệ: {exc}") from exc
    if not isinstance(raw, dict):
        raise ExtractError("Gemini trả về JSON không phải object.")

    clean, rejected = normalize_extracted(raw)
    return raw, clean, rejected


# Record kẹt PROCESSING quá lâu (worker bị kill giữa chừng) được trả về PENDING.
STUCK_PROCESSING_MINUTES = 30


def _requeue_stuck_processing() -> int:
    from datetime import timedelta

    from django.utils import timezone

    threshold = timezone.now() - timedelta(minutes=STUCK_PROCESSING_MINUTES)
    return CallRecord.objects.filter(
        extract_status=ExtractStatus.PROCESSING, updated_at__lt=threshold
    ).update(extract_status=ExtractStatus.PENDING)


def _claim_pending_batch(limit: int) -> list[int]:
    """Khoá dòng (skip_locked — 2 worker không giành nhau), set PROCESSING rồi trả
    id để xử lý NGOÀI transaction: phần gọi Gemini chậm không được ôm lock.

    order_id NOT NULL: extract xong mà chưa khớp đơn thì không có chỗ đổ. Vòng
    re-match của Task 1 sync sẽ gán đơn sau, tick kế tiếp mới tới lượt.
    """
    with transaction.atomic():
        ids = list(
            CallRecord.objects.select_for_update(skip_locked=True)
            .filter(
                extract_status=ExtractStatus.PENDING,
                transcribe_status=TranscribeStatus.DONE,
                order__isnull=False,
            )
            .order_by("-id")  # mới trước, cùng lý do ưu tiên của transcribe
            .values_list("id", flat=True)[:limit]
        )
        if ids:
            CallRecord.objects.filter(id__in=ids).update(extract_status=ExtractStatus.PROCESSING)
    return ids


def _fail_record(record: CallRecord, error: str, *, transient: bool = False) -> None:
    if not transient:
        record.extract_attempts += 1
    record.extract_error = error
    record.extract_status = (
        ExtractStatus.FAILED
        if record.extract_attempts >= settings.GEMINI_EXTRACT_MAX_ATTEMPTS
        else ExtractStatus.PENDING
    )
    record.save(
        update_fields=["extract_attempts", "extract_error", "extract_status", "updated_at"]
    )


def _requeue_batch(record_ids: list[int]) -> int:
    return CallRecord.objects.filter(
        id__in=record_ids, extract_status=ExtractStatus.PROCESSING
    ).update(extract_status=ExtractStatus.PENDING)


def extract_pending_insights(limit: int | None = None) -> dict:
    """Xử lý tối đa ``limit`` record trong 1 tick. Lỗi 1 record không chặn record khác."""
    from apps.orders.application.services.assessment_ai_fill_service import apply_ai_insights

    summary = {
        "claimed": 0,
        "done": 0,
        "filled": 0,
        "errors": 0,
        "skipped": 0,
        "quota_stopped": False,
        "requeued_stuck": 0,
    }
    if not sysconfig.get("GEMINI_EXTRACT_ENABLED") or not sysconfig.get("GEMINI_API_KEY"):
        return summary

    summary["requeued_stuck"] = _requeue_stuck_processing()
    # Chặn TRƯỚC khi claim: hết quota mà vẫn claim thì mỗi tick ghi 2 lượt UPDATE
    # cả batch để rồi vứt (sự cố transcribe 2026-07-22).
    if cache.get(EXTRACT_COOLDOWN_KEY) is not None:
        summary["quota_stopped"] = True
        return summary

    # limit=0 phải claim đúng 0 record (không phải "falsy nên dùng batch mặc
    # định") — `limit or X` từng nuốt mất ý nghĩa này.
    batch = _claim_pending_batch(settings.GEMINI_EXTRACT_BATCH if limit is None else limit)
    summary["claimed"] = len(batch)

    for index, record_id in enumerate(batch):
        record = CallRecord.objects.filter(id=record_id).select_related("order").first()
        if record is None:  # pragma: no cover - record bị xoá giữa chừng
            continue
        transcript = record.transcript or ""
        if len(transcript) < settings.GEMINI_EXTRACT_MIN_TRANSCRIPT_CHARS:
            record.extract_status = ExtractStatus.SKIPPED
            record.save(update_fields=["extract_status", "updated_at"])
            summary["skipped"] += 1
            continue

        try:
            raw, clean, rejected = extract_insights(transcript)
        except ExtractError as exc:
            if exc.quota_exhausted:
                cache.set(
                    EXTRACT_COOLDOWN_KEY, 1, timeout=settings.GEMINI_QUOTA_COOLDOWN_MINUTES * 60
                )
                remaining = _requeue_batch(batch[index:])
                summary["quota_stopped"] = True
                logger.warning(
                    "extract nghỉ %s phút vì Gemini hết quota: %s (%s record chờ lại)",
                    settings.GEMINI_QUOTA_COOLDOWN_MINUTES,
                    exc,
                    remaining,
                )
                break
            _fail_record(record, str(exc), transient=exc.transient)
            summary["errors"] += 1
            logger.warning("extract lỗi call_id=%s: %s", record.call_id, exc)
            continue

        filled = apply_ai_insights(order=record.order, clean=clean, call_record_id=record.id)
        record.extract_payload = {"raw": raw, "clean": clean, "rejected": rejected, "filled": filled}
        record.extract_status = ExtractStatus.DONE
        record.extract_error = ""
        record.save(
            update_fields=["extract_payload", "extract_status", "extract_error", "updated_at"]
        )
        summary["done"] += 1
        summary["filled"] += len(filled)
        # KHÔNG log nội dung transcript/giá trị extract (PII) — chỉ tên field.
        logger.info(
            "extract xong call_id=%s order_id=%s fields=%s", record.call_id, record.order_id, filled
        )
    return summary
