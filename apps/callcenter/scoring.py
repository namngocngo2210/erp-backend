"""Chấm chất lượng cuộc gọi từ transcript bằng Gemini.

Luồng 4 của pipeline (sau sync + transcribe + extract). Text-only nên rẻ và
chạy lại được bất kỳ lúc nào mà không cần audio gốc — URL ghi âm Zenify hết hạn
theo retention của họ.

Luật chấm (tiêu chí, thang điểm, cờ đỏ, chuẩn hoá) thuộc về ``qa_rubric``;
module này chỉ biết cách nói chuyện với Gemini và quản hàng đợi. Bám nguyên
pattern của ``extract.py`` — nó đã đi qua 2 sự cố quota, không phát minh lại.

Xem docs/superpowers/specs/2026-07-30-call-quality-scoring-design.md
"""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.sysconfig import sysconfig

from .models import CallQualityScore, CallRecord, ScoreStatus, TranscribeStatus
from .qa_rubric import (
    RUBRIC_VERSION,
    build_prompt,
    build_score_response_schema,
    compute_scores,
    normalize_scored,
)

logger = logging.getLogger(__name__)

# Cờ cooldown riêng của luồng chấm điểm — 429 ở đây không được khoá luôn
# transcribe/extract (và ngược lại).
SCORE_COOLDOWN_KEY = "gemini:score:cooldown"

# Record kẹt PROCESSING quá lâu (worker bị kill giữa chừng) được trả về PENDING.
STUCK_PROCESSING_MINUTES = 30

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

# Transcript đã phân vai từ TRANSCRIBE_PROMPT: "Nhân viên: ..." / "Khách: ...".
_STAFF_TURN = re.compile(r"^\s*nh[aâ]n vi[eê]n\s*:", re.IGNORECASE)
_CUSTOMER_TURN = re.compile(r"^\s*kh[aá]ch\s*:", re.IGNORECASE)


class ScoreError(Exception):
    """Lỗi chấm 1 record. Message giữ nguyên văn để lưu vào record.

    ``transient=True`` = lỗi hạ tầng nhất thời (mạng, 429/5xx): KHÔNG đốt lượt
    attempt — cùng bài học audit 2026-07-18 của transcribe.

    ``quota_exhausted=True`` = Google trả 429: nghỉ trọn chu kỳ cooldown thay vì
    dội request vào tường.
    """

    def __init__(self, message: str, *, transient: bool = False, quota_exhausted: bool = False):
        super().__init__(message)
        self.transient = transient
        self.quota_exhausted = quota_exhausted


# ── chỉ số đo bằng code (không tốn AI) ───────────────────────────────────────


def compute_speech_metrics(transcript: str, duration_seconds: int) -> tuple[int, float]:
    """Trả ``(wpm, talk_ratio)`` từ transcript đã phân vai.

    Hai số này khách quan và KHÔNG BAO GIỜ ảo — dùng làm đối chứng cho điểm AI.
    ``talk_ratio`` cao bất thường (> 0.75) đúng là lỗi "thao thao bất tuyệt" mà
    khung kịch bản cảnh báo, đo được mà không cần hỏi model.
    """
    staff_words = 0
    customer_words = 0
    current = None
    for line in (transcript or "").splitlines():
        if _STAFF_TURN.match(line):
            current = "staff"
            line = line.split(":", 1)[1] if ":" in line else ""
        elif _CUSTOMER_TURN.match(line):
            current = "customer"
            line = line.split(":", 1)[1] if ":" in line else ""
        count = len(line.split())
        if current == "staff":
            staff_words += count
        elif current == "customer":
            customer_words += count

    total = staff_words + customer_words
    talk_ratio = round(staff_words / total, 3) if total else 0.0
    wpm = int(staff_words / (duration_seconds / 60)) if duration_seconds > 0 else 0
    return wpm, talk_ratio


# ── gọi Gemini ───────────────────────────────────────────────────────────────


def _generate_json(prompt: str) -> str:
    """Gọi Gemini với structured output. Tách hàm riêng để test mock được."""
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - thiếu dependency là lỗi deploy
        raise ScoreError(f"Thiếu thư viện google-genai: {exc}") from exc

    client = genai.Client(
        api_key=sysconfig.get("GEMINI_API_KEY"),
        http_options=types.HttpOptions(timeout=120_000),
    )
    response = client.models.generate_content(
        model=sysconfig.get("QA_SCORING_MODEL"),
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=build_score_response_schema(),
            # temperature=0: chấm điểm phải lặp lại được. Cùng transcript mà mỗi
            # lần một điểm thì không so sánh được theo tuần.
            temperature=0,
        ),
    )
    return getattr(response, "text", None) or ""


def score_transcript(transcript: str) -> tuple[dict, dict, dict]:
    """Trả ``(raw, clean, rejected)`` cho 1 transcript."""
    try:
        text = _generate_json(build_prompt(transcript))
    except ScoreError:
        raise
    except Exception as exc:  # noqa: BLE001 — lỗi SDK/API giữ nguyên văn cho record
        message = str(exc)
        lowered = message.lower()
        raise ScoreError(
            f"Gemini lỗi: {message}",
            transient=any(marker in lowered for marker in _TRANSIENT_MARKERS),
            quota_exhausted=any(marker in lowered for marker in _QUOTA_MARKERS),
        ) from exc

    text = (text or "").strip()
    if not text:
        raise ScoreError("Gemini trả về response rỗng.")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScoreError(f"Gemini trả về JSON không hợp lệ: {exc}") from exc
    if not isinstance(raw, dict):
        raise ScoreError("Gemini trả về JSON không phải object.")

    clean, rejected = normalize_scored(raw, transcript)
    return raw, clean, rejected


# ── hàng đợi ─────────────────────────────────────────────────────────────────


def _requeue_stuck_processing() -> int:
    threshold = timezone.now() - timedelta(minutes=STUCK_PROCESSING_MINUTES)
    return CallRecord.objects.filter(
        score_status=ScoreStatus.PROCESSING, updated_at__lt=threshold
    ).update(score_status=ScoreStatus.PENDING)


def _claim_pending_batch(limit: int) -> list[int]:
    """Khoá dòng (skip_locked — 2 worker không giành nhau), set PROCESSING rồi
    trả id để xử lý NGOÀI transaction: phần gọi Gemini chậm không được ôm lock.

    Khác extract: KHÔNG đòi ``order_id`` — chấm điểm không ghi ngược vào đơn,
    cuộc gọi chưa khớp đơn vẫn đánh giá được kỹ năng tư vấn.
    """
    with transaction.atomic():
        ids = list(
            CallRecord.objects.select_for_update(skip_locked=True)
            .filter(
                score_status=ScoreStatus.PENDING,
                transcribe_status=TranscribeStatus.DONE,
            )
            .order_by("-id")  # mới trước, cùng lý do ưu tiên của transcribe
            .values_list("id", flat=True)[:limit]
        )
        if ids:
            CallRecord.objects.filter(id__in=ids).update(score_status=ScoreStatus.PROCESSING)
    return ids


def _fail_record(record: CallRecord, error: str, *, transient: bool = False) -> None:
    if not transient:
        record.score_attempts += 1
    record.score_error = error
    record.score_status = (
        ScoreStatus.FAILED
        if record.score_attempts >= settings.QA_SCORING_MAX_ATTEMPTS
        else ScoreStatus.PENDING
    )
    record.save(update_fields=["score_attempts", "score_error", "score_status", "updated_at"])


def _requeue_batch(record_ids: list[int]) -> int:
    return CallRecord.objects.filter(
        id__in=record_ids, score_status=ScoreStatus.PROCESSING
    ).update(score_status=ScoreStatus.PENDING)


def _sale_snapshot(record: CallRecord) -> tuple[str, str]:
    """Bản chụp trạng thái sale tự nhập, để đối chiếu với thứ AI nghe được."""
    if record.order_id is None:
        return "", ""
    from apps.orders.models import OrderAssessment

    assessment = (
        OrderAssessment.objects.filter(order_id=record.order_id)
        .values("sales_status_code", "not_closed_reason_code")
        .first()
    )
    if not assessment:
        return "", ""
    return (
        str(assessment.get("sales_status_code") or ""),
        str(assessment.get("not_closed_reason_code") or ""),
    )


def _local_call_date(record: CallRecord):
    moment = record.start_time or record.created_at or timezone.now()
    return timezone.localtime(moment).date()


def _persist_score(record: CallRecord, clean: dict, rejected: dict) -> CallQualityScore:
    total, block_scores = compute_scores(clean["criteria"])
    wpm, talk_ratio = compute_speech_metrics(record.transcript, record.acd_time)
    sales_status, not_closed_reason = _sale_snapshot(record)

    score, _ = CallQualityScore.objects.update_or_create(
        call_record=record,
        rubric_version=RUBRIC_VERSION,
        defaults={
            "model_name": sysconfig.get("QA_SCORING_MODEL"),
            "agent_id": record.agent_id,
            "agent_name": record.agent_name,
            "call_date": _local_call_date(record),
            "order_id_snapshot": record.order_id,
            "duration_seconds": record.acd_time,
            "total_score": total,
            "block_scores": block_scores,
            "criteria": clean["criteria"],
            "red_flags": clean["red_flags"],
            "rejected": rejected,
            "summary": clean["summary"],
            "coaching": clean["coaching"],
            "wpm": wpm,
            "talk_ratio": talk_ratio,
            "ai_not_closed_reason": clean["not_closed_reason"],
            "sale_sales_status_code": sales_status,
            "sale_not_closed_reason_code": not_closed_reason,
            "contraindications": clean["contraindications"],
        },
    )
    return score


def _is_eligible(record: CallRecord) -> bool:
    return (
        record.acd_time >= sysconfig.get("QA_MIN_DURATION_SECONDS")
        and len(record.transcript or "") >= sysconfig.get("QA_MIN_TRANSCRIPT_CHARS")
    )


def score_pending_calls(limit: int | None = None, *, force: bool = False) -> dict:
    """Chấm tối đa ``limit`` record trong 1 tick. Lỗi 1 record không chặn record khác.

    ``force=True`` bỏ qua kill switch ``QA_SCORING_ENABLED`` — chỉ dùng cho lệnh
    chạy tay. Có nó thì chấm được một lô có kiểm soát để đối chiếu với team lead
    mà KHÔNG phải bật beat lên chạy tự động; thiếu nó thì hai việc đó dính vào
    nhau và không còn bước calibrate riêng.
    """
    summary = {
        "claimed": 0,
        "done": 0,
        "skipped": 0,
        "errors": 0,
        "red_flags": 0,
        "quota_stopped": False,
        "requeued_stuck": 0,
    }
    if (not sysconfig.get("QA_SCORING_ENABLED") and not force) or not sysconfig.get("GEMINI_API_KEY"):
        return summary

    summary["requeued_stuck"] = _requeue_stuck_processing()
    # Chặn TRƯỚC khi claim: hết quota mà vẫn claim thì mỗi tick ghi 2 lượt UPDATE
    # cả batch để rồi vứt (sự cố transcribe 2026-07-22).
    if cache.get(SCORE_COOLDOWN_KEY) is not None:
        summary["quota_stopped"] = True
        return summary

    # limit=0 phải claim đúng 0 record (không phải "falsy nên dùng batch mặc
    # định") — `limit or X` từng nuốt mất ý nghĩa này ở extract.
    batch = _claim_pending_batch(settings.QA_SCORING_BATCH if limit is None else limit)
    summary["claimed"] = len(batch)

    for index, record_id in enumerate(batch):
        record = CallRecord.objects.filter(id=record_id).first()
        if record is None:  # pragma: no cover - record bị xoá giữa chừng
            continue

        if not _is_eligible(record):
            record.score_status = ScoreStatus.SKIPPED
            record.save(update_fields=["score_status", "updated_at"])
            summary["skipped"] += 1
            continue

        try:
            _, clean, rejected = score_transcript(record.transcript)
        except ScoreError as exc:
            if exc.quota_exhausted:
                cache.set(
                    SCORE_COOLDOWN_KEY, 1, timeout=settings.GEMINI_QUOTA_COOLDOWN_MINUTES * 60
                )
                remaining = _requeue_batch(batch[index:])
                summary["quota_stopped"] = True
                logger.warning(
                    "chấm điểm nghỉ %s phút vì Gemini hết quota: %s (%s record chờ lại)",
                    settings.GEMINI_QUOTA_COOLDOWN_MINUTES,
                    exc,
                    remaining,
                )
                break
            _fail_record(record, str(exc), transient=exc.transient)
            summary["errors"] += 1
            logger.warning("chấm điểm lỗi call_id=%s: %s", record.call_id, exc)
            continue

        score = _persist_score(record, clean, rejected)
        record.score_status = ScoreStatus.DONE
        record.score_error = ""
        record.save(update_fields=["score_status", "score_error", "updated_at"])
        summary["done"] += 1
        summary["red_flags"] += len(score.red_flags)
        # KHÔNG log transcript hay trích dẫn (PII) — chỉ id và điểm tổng.
        logger.info(
            "chấm xong call_id=%s total=%s flags=%s",
            record.call_id,
            score.total_score,
            [f["code"] for f in score.red_flags],
        )

    if summary["red_flags"]:
        _notify_red_flags(summary["red_flags"])
    return summary


def drain_pending_calls(
    *, max_minutes: int | None = None, force: bool = False, batch: int | None = None
) -> dict:
    """Chấm CUỐN CHIẾU: lặp từng lô cho tới khi hết hàng đợi.

    Khác ``score_pending_calls`` (đúng một lô): hàm này chạy tới khi không còn
    record PENDING nào, hoặc chạm trần thời gian, hoặc Gemini hết quota. Cuộc đã
    chấm không bao giờ vào lại hàng đợi — ``_claim_pending_batch`` chỉ lấy
    ``score_status=PENDING``, chấm xong là DONE.

    Trần thời gian (``QA_SCORING_MAX_MINUTES``) là thứ giữ cho tick đêm không
    chạy vô hạn: beat chỉ 1 lần/ngày nên một lô hỏng kéo dài sẽ ngốn worker
    `slow` tới sáng. Chạm trần thì phần chưa chấm nằm nguyên PENDING và đêm sau
    chạy tiếp — cuốn chiếu, không mất gì.
    """
    budget_minutes = settings.QA_SCORING_MAX_MINUTES if max_minutes is None else max_minutes
    # CHỈ 0 mới là "không giới hạn". Giá trị âm (gõ nhầm env) phải cho deadline
    # nằm ở quá khứ ⇒ dừng ngay, chứ không được rơi vào nhánh chạy vô hạn —
    # nhầm dấu một ký tự mà thành chấm không phanh là cái giá quá đắt.
    deadline = (
        None if budget_minutes == 0 else timezone.now() + timedelta(minutes=budget_minutes)
    )

    total = {
        "claimed": 0,
        "done": 0,
        "skipped": 0,
        "errors": 0,
        "red_flags": 0,
        "quota_stopped": False,
        "requeued_stuck": 0,
        "rounds": 0,
        "stopped_by": "empty",
    }

    while True:
        if deadline is not None and timezone.now() >= deadline:
            total["stopped_by"] = "time_budget"
            break

        summary = score_pending_calls(limit=batch, force=force)
        total["rounds"] += 1
        for key in ("claimed", "done", "skipped", "errors", "red_flags", "requeued_stuck"):
            total[key] += summary[key]

        if summary["quota_stopped"]:
            total["quota_stopped"] = True
            total["stopped_by"] = "quota"
            break
        # Không claim được gì = hết hàng đợi. Cũng bắt luôn trường hợp kill switch
        # tắt hoặc thiếu API key — vòng lặp dừng ngay thay vì quay vô hạn.
        if summary["claimed"] == 0:
            break

    return total


def _notify_red_flags(count: int) -> None:
    """Thẻ Lark báo quản lý có cờ đỏ mới — THÔNG BÁO, không phải đồng bộ dữ liệu.

    Cố ý không kèm trích dẫn: nội dung có thể lộ bệnh án khách, chỗ để đọc là
    trang QA có phân quyền.
    """
    from apps.common.ops_alerts import send_ops_alert

    send_ops_alert(
        key="callqa:red_flags",
        title="[QA cuộc gọi] Phát hiện cờ đỏ tuân thủ",
        lines=[
            f"Có {count} cờ đỏ mới trong lượt chấm gần nhất.",
            "Xem chi tiết ở trang Báo cáo → Chất lượng cuộc gọi.",
        ],
        dedupe_seconds=3600,
    )
