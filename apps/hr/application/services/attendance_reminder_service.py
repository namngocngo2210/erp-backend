"""Nhắc chấm công qua Lark DM — thay automation Base assistant.

Người nhận = toàn bộ thành viên của nhóm cấu hình ở
``LARK_CHECKIN_REMINDER_CHAT_ID`` (giống hệt automation cũ: recipient là nhóm
DESI NHÓM TỔNG + "send private messages to group members"). chat_id rỗng =
tính năng tắt (kill-switch, cùng convention với các Lark bot khác).
"""

from __future__ import annotations

import logging

from apps.common.lark_open_api import LarkOpenAPIError
from apps.hr.infrastructure import lark_messenger
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)

REMINDER_KINDS = {
    "arrive": {
        "title": "Nhắc Chấm Công Khi Đến",
        "body": "Nhắc nhở mọi người chấm công khi đến",
    },
    "leave": {
        "title": "Nhắc Chấm Công Khi Về",
        "body": "Nhắc nhở mọi người chấm công khi về",
    },
}


def send_attendance_reminder(kind: str) -> dict:
    """Gửi DM nhắc chấm công cho mọi thành viên nhóm; trả summary {sent, failed, skipped}.

    Best-effort per-recipient: một người gửi lỗi không chặn những người còn lại.
    """
    template = REMINDER_KINDS.get(kind)
    if template is None:
        raise ValueError(f"Unknown attendance reminder kind: {kind!r}")

    chat_id = sysconfig.get("LARK_CHECKIN_REMINDER_CHAT_ID")
    if not chat_id:
        logger.info("attendance reminder (%s) skipped: LARK_CHECKIN_REMINDER_CHAT_ID empty", kind)
        return {"sent": 0, "failed": 0, "skipped": True}

    member_ids = lark_messenger.list_chat_member_ids(chat_id)
    content_lines = _build_content_lines(template["body"])

    sent = failed = 0
    for open_id in member_ids:
        try:
            lark_messenger.send_post_message(
                open_id, title=template["title"], content_lines=content_lines
            )
            sent += 1
        except LarkOpenAPIError:
            failed += 1
            logger.warning(
                "attendance reminder (%s) failed for open_id=%s", kind, open_id, exc_info=True
            )

    logger.info(
        "attendance reminder (%s) done: sent=%s failed=%s members=%s",
        kind,
        sent,
        failed,
        len(member_ids),
    )
    return {"sent": sent, "failed": failed, "skipped": False}


def _build_content_lines(body: str) -> list[list[dict]]:
    lines: list[list[dict]] = [[{"tag": "text", "text": body}]]
    link = sysconfig.get("LARK_CHECKIN_REMINDER_LINK")
    if link:
        lines.append(
            [
                {"tag": "text", "text": "Link chấm công: "},
                {"tag": "a", "text": "Link", "href": link},
            ]
        )
    return lines
