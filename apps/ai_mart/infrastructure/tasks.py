"""Celery tasks — khuôn y hệt analytics/infrastructure/tasks.py: single_flight,
statement_timeout, LỖI THOÁT RA NGOÀI."""

import logging

from celery import shared_task
from django.db import OperationalError

from apps.ai_mart.application.etl_service import rebuild_all, sync_hot
from apps.common.db_guards import single_flight, statement_timeout
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)

_SYNC_LOCK = "lock:ai_mart_sync_hot"
_REBUILD_LOCK = "lock:ai_mart_rebuild_all"
_EXTRACT_LOCK = "lock:ai_mart_chat_extract"


@shared_task(
    name="apps.ai_mart.tasks.sync_hot_marts",
    soft_time_limit=240,
    time_limit=300,
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
)
def sync_hot_marts() -> dict:
    with single_flight(_SYNC_LOCK, 360) as acquired:
        if not acquired:
            logger.warning("sync_hot_marts already running — skip this cycle")
            return {"skipped": True}
        with statement_timeout(180):
            return sync_hot()


# Trần đo THẬT trên prod 05/09/2026, lượt rebuild đầu tiên có bảng chat: 2159s
# tổng — ai.hoi_thoai 968s (95k hội thoại x 2 LATERAL qua FDW), ai.don_hang 789s,
# ai.tin_nhan 175s. Trần cũ (soft 1500s) sinh ra TRƯỚC khi có 2 bảng chat và sẽ
# giết job đêm giữa chừng. Đặt 4200s = ~2x số đo, chừa chỗ cho lưu lượng chat tăng
# (~15-20k hội thoại/tháng). statement_timeout là trần MỖI CÂU LỆNH, không phải cả
# job: câu nặng nhất đo được là 968s.
@shared_task(
    name="apps.ai_mart.tasks.rebuild_all_marts",
    soft_time_limit=4200,
    time_limit=4500,
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
)
def rebuild_all_marts() -> dict:
    with single_flight(_REBUILD_LOCK, 5400) as acquired:
        if not acquired:
            logger.warning("rebuild_all_marts already running — skip this cycle")
            return {"skipped": True}
        with statement_timeout(2400):
            return rebuild_all()


@shared_task(
    name="apps.ai_mart.tasks.extract_chat_labels",
    soft_time_limit=540,
    time_limit=600,
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
)
def extract_chat_labels() -> dict:
    """Gán nhãn hội thoại chat. KHÔNG chạm foreign table — nguồn FDW chết vẫn
    tiêu hoá được phần tồn đọng trong hàng đợi."""
    from apps.ai_mart.infrastructure import chat_extract_runner

    # Công tắc tắt phải chặn TRƯỚC enqueue_pending(). run_batch() tự kiểm cờ rồi
    # trả về summary rỗng, nhưng enqueue_pending() thì KHÔNG — để nguyên thì một
    # luồng ĐÃ TẮT vẫn quét + upsert toàn bộ ai.hoi_thoai (95k dòng trên prod)
    # mỗi 10 phút, vô ích. Task vẫn kết thúc THÀNH CÔNG nên alert
    # AiChatExtractSilent vẫn im đúng như phạm vi nó đã ghi (monitoring/alerts.yml).
    if not sysconfig.get("AI_CHAT_EXTRACT_ENABLED"):
        return {"skipped": True, "disabled": True}

    with single_flight(_EXTRACT_LOCK, 900) as acquired:
        if not acquired:
            logger.warning("extract_chat_labels already running — skip this cycle")
            return {"skipped": True}
        # statement_timeout bọc CẢ run_batch(), không riêng enqueue_pending()
        # (spec §5.3: "dùng lại single_flight + statement_timeout"). 120s là
        # timeout của MỘT CÂU SQL, không phải của cả lô: mọi câu lệnh trong
        # run_batch() đều là claim/UPDATE/SELECT một dòng, nặng nhất là _CLAIM_SQL
        # join ai.hoi_thoai — 120s đã là rộng rãi. Lượt chờ Gemini (tới 120s mỗi
        # hội thoại) KHÔNG phải câu lệnh DB nên timeout này không đụng tới nó;
        # cái canh tổng thời gian lô là soft_time_limit=540 của task.
        with statement_timeout(120):
            queued = chat_extract_runner.enqueue_pending()
            summary = chat_extract_runner.run_batch()
        return {"queued": queued, **summary}
