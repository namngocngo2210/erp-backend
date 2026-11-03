"""Task hạ tầng dùng chung, không thuộc domain app nào.

``ops_heartbeat``: bắn GET tới OPS_HEARTBEAT_URL (healthchecks.io) mỗi 5 phút từ
beat. Chuỗi beat → broker Redis → worker → HTTP ra ngoài phải sống toàn bộ thì
heartbeat mới tới nơi, nên một check "ngừng nhận ping" phía healthchecks phát
hiện được cả cụm Celery chết — điều Prometheus chạy cùng VPS không làm được khi
VPS sập. URL trống = tắt (no-op im lặng, dev/staging không cần cấu hình).
"""

import logging

from celery import shared_task
from django.conf import settings

from .http_client import HttpClient

logger = logging.getLogger(__name__)


@shared_task(
    name="apps.common.tasks.ops_heartbeat",
    ignore_result=True,
    # KHÔNG retry: heartbeat chạy mỗi phút, tick sau tự bù; retry chỉ dồn request
    # khi endpoint ngoài đang lỗi (audit 2026-09-23 T1).
    max_retries=0,
)
def ops_heartbeat() -> str:
    url = getattr(settings, "OPS_HEARTBEAT_URL", "").strip()
    if not url:
        return "skipped"
    # retries=1: heartbeat là tín hiệu sống định kỳ, lỡ 1 nhịp thì nhịp sau bù —
    # không cần retry dài chiếm slot worker (time_limit global 30s).
    with HttpClient(timeout=10, retries=1) as client:
        response = client.get(url)
        response.raise_for_status()
    return "ok"
