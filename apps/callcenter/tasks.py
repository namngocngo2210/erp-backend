"""Celery tasks pipeline cuộc gọi Zenify (docs/zenify-call-transcripts.md).

Cả 2 task gate bằng ZENIFY_CALL_SYNC_ENABLED (kill switch, default false — bật
sau smoke test prod) + lock Redis chống 2 tick chạy chồng (tick sau SKIP, không
xếp hàng — pattern import_create_only_one_channel).
"""

import logging

from celery import shared_task
from django.db import OperationalError
from django.core.cache import cache

from apps.sysconfig import sysconfig

from . import services
from .extract import extract_pending_insights
from .scoring import drain_pending_calls
from .sync_service import sync_call_histories
from .transcribe import transcribe_pending_records

logger = logging.getLogger(__name__)

_SYNC_LOCK_KEY = "callcenter:sync_call_histories:lock"
_TRANSCRIBE_LOCK_KEY = "callcenter:transcribe:lock"
_EXTRACT_LOCK_KEY = "callcenter:extract:lock"
_SCORE_LOCK_KEY = "callcenter:score:lock"


@shared_task(
    bind=True,
    name="apps.callcenter.tasks.sync_zenify_call_histories",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=300,
    time_limit=360,
)
def sync_zenify_call_histories(self) -> dict:
    if not sysconfig.get("ZENIFY_CALL_SYNC_ENABLED"):
        return {"skipped": "disabled"}
    if not services.zenify_configured():
        return {"skipped": "zenify_not_configured"}
    if not cache.add(_SYNC_LOCK_KEY, 1, timeout=360):
        return {"skipped": "locked"}
    try:
        summary = sync_call_histories()
        logger.info("sync_zenify_call_histories: %s", summary)
        return summary
    except services.ZenifyError as exc:
        logger.warning("sync_zenify_call_histories thất bại: %s", exc)
        return {"error": str(exc)}
    finally:
        cache.delete(_SYNC_LOCK_KEY)


@shared_task(
    bind=True,
    name="apps.callcenter.tasks.transcribe_pending_call_records",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=480,
    time_limit=540,
)
def transcribe_pending_call_records(self) -> dict:
    if not sysconfig.get("ZENIFY_CALL_SYNC_ENABLED"):
        return {"skipped": "disabled"}
    if not sysconfig.get("GEMINI_API_KEY"):
        return {"skipped": "gemini_not_configured"}
    if not cache.add(_TRANSCRIBE_LOCK_KEY, 1, timeout=540):
        return {"skipped": "locked"}
    try:
        summary = transcribe_pending_records()
        logger.info("transcribe_pending_call_records: %s", summary)
        return summary
    finally:
        cache.delete(_TRANSCRIBE_LOCK_KEY)


@shared_task(
    bind=True,
    name="apps.callcenter.tasks.extract_pending_call_insights",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=480,
    time_limit=540,
)
def extract_pending_call_insights(self) -> dict:
    # Gate riêng GEMINI_EXTRACT_ENABLED, KHÔNG dùng ZENIFY_CALL_SYNC_ENABLED:
    # transcribe có thể đang chạy mà chưa muốn ghi gì vào assessment.
    if not sysconfig.get("GEMINI_EXTRACT_ENABLED"):
        return {"skipped": "disabled"}
    if not sysconfig.get("GEMINI_API_KEY"):
        return {"skipped": "gemini_not_configured"}
    if not cache.add(_EXTRACT_LOCK_KEY, 1, timeout=540):
        return {"skipped": "locked"}
    try:
        summary = extract_pending_insights()
        logger.info("extract_pending_call_insights: %s", summary)
        return summary
    finally:
        cache.delete(_EXTRACT_LOCK_KEY)


# Time limit rộng hơn hẳn 3 task kia: beat chỉ chạy 1 lần/ngày và task chấm cuốn
# chiếu tới khi hết hàng đợi. Trần thật nằm ở QA_SCORING_MAX_MINUTES (mặc định
# 60'); soft/hard time limit chỉ là lưới an toàn phía Celery, đặt rộng hơn trần
# đó để phần dừng có kiểm soát luôn xảy ra TRƯỚC khi Celery giết task giữa chừng.
@shared_task(
    bind=True,
    name="apps.callcenter.tasks.score_pending_call_quality",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=4200,
    time_limit=4500,
)
def score_pending_call_quality(self) -> dict:
    # Gate riêng QA_SCORING_ENABLED: transcribe/extract có thể đang chạy mà chưa
    # muốn chấm điểm chất lượng tư vấn.
    if not sysconfig.get("QA_SCORING_ENABLED"):
        return {"skipped": "disabled"}
    if not sysconfig.get("GEMINI_API_KEY"):
        return {"skipped": "gemini_not_configured"}
    # timeout khớp time_limit của task: lock ngắn hơn thì nó tự hết hạn giữa lúc
    # tick còn đang chạy, và chốt chặn chống chạy chồng thành vô nghĩa.
    if not cache.add(_SCORE_LOCK_KEY, 1, timeout=4500):
        return {"skipped": "locked"}
    try:
        summary = drain_pending_calls()
        logger.info("score_pending_call_quality: %s", summary)
        return summary
    finally:
        cache.delete(_SCORE_LOCK_KEY)
