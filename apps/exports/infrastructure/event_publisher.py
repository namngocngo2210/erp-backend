"""Phát trạng thái job tới đúng MỘT người — chủ của job.

Dùng group riêng theo user (``exports_user_<id>``) chứ không phải group chung
``crm_updates``: payload mang tên file và số dòng của một người, không có lý do
fan-out tới mọi tab đang mở của cả công ty.
"""

from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def export_group(user_id: int) -> str:
    return f"exports_user_{user_id}"


def publish_export_event(job) -> None:
    payload = {
        "type": "export.updated",
        "id": str(job.id),
        "kind": job.kind,
        "status": job.status,
        "progress_rows": job.progress_rows,
        "total_rows": job.total_rows,
        "file_name": job.file_name,
        "error_message": job.error_message,
    }
    try:
        layer = get_channel_layer()
        if layer is None:
            return
        async_to_sync(layer.group_send)(
            export_group(job.user_id), {"type": "export_event", "payload": payload}
        )
    except Exception:
        # Best-effort: mất event realtime thì FE vẫn có polling 3s làm lưới an toàn,
        # KHÔNG được để lỗi phát event làm hỏng job đã chạy xong.
        logger.exception("Không phát được export event job_id=%s", job.id)
