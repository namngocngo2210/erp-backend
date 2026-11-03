"""Gửi tin nhắn Lark cho nhân sự qua app bot (không phải webhook custom bot).

Yêu cầu phía Lark console:
  - App bật capability Bot, scope ``im:chat:readonly`` (đọc thành viên nhóm)
    + ``im:message:send_as_bot`` (gửi DM), availability mở cho toàn công ty.
  - Bot phải được mời vào nhóm thì mới đọc được danh sách thành viên.

Docs:
  - https://open.larksuite.com/document/server-docs/group/chat-member/get
  - https://open.larksuite.com/document/server-docs/im-v1/message/create
"""

from __future__ import annotations

import json

from apps.common.lark_open_api import LarkCredentials, lark_request
from apps.sysconfig import sysconfig

_MEMBER_PAGE_SIZE = 100
_CHAT_PAGE_SIZE = 100


def _credentials() -> LarkCredentials | None:
    """App riêng cho nhắc chấm công (khác app Bitable); chưa cấu hình → app mặc định."""
    app_id = sysconfig.get("LARK_CHECKIN_REMINDER_APP_ID")
    app_secret = sysconfig.get("LARK_CHECKIN_REMINDER_APP_SECRET")
    if app_id and app_secret:
        return LarkCredentials(app_id=app_id, app_secret=app_secret)
    return None


def list_chat_member_ids(chat_id: str) -> list[str]:
    """Trả về ``open_id`` của toàn bộ thành viên (người) trong nhóm, theo trang."""
    member_ids: list[str] = []
    page_token = ""
    while True:
        params: dict = {"member_id_type": "open_id", "page_size": _MEMBER_PAGE_SIZE}
        if page_token:
            params["page_token"] = page_token
        data = lark_request(
            "GET", f"/im/v1/chats/{chat_id}/members", params=params, credentials=_credentials()
        )
        for item in data.get("items") or []:
            member_id = str(item.get("member_id") or "").strip()
            if member_id:
                member_ids.append(member_id)
        page_token = str(data.get("page_token") or "")
        if not data.get("has_more") or not page_token:
            return member_ids


def send_post_message(open_id: str, *, title: str, content_lines: list[list[dict]]) -> None:
    """DM một người bằng rich-text (``post``) — có tiêu đề đậm như Base assistant.

    ``content_lines``: mảng dòng, mỗi dòng là mảng element theo spec post của Lark
    (vd ``{"tag": "text", "text": "..."}`` / ``{"tag": "a", "text": "Link", "href": ...}``).
    """
    content = json.dumps({"en_us": {"title": title, "content": content_lines}}, ensure_ascii=False)
    lark_request(
        "POST",
        "/im/v1/messages",
        params={"receive_id_type": "open_id"},
        json_body={"receive_id": open_id, "msg_type": "post", "content": content},
        credentials=_credentials(),
    )


def list_bot_chats() -> list[dict]:
    """Liệt kê các nhóm bot đang tham gia — dùng để tra ``chat_id`` khi cấu hình."""
    chats: list[dict] = []
    page_token = ""
    while True:
        params: dict = {"page_size": _CHAT_PAGE_SIZE}
        if page_token:
            params["page_token"] = page_token
        data = lark_request("GET", "/im/v1/chats", params=params, credentials=_credentials())
        for item in data.get("items") or []:
            chats.append({"chat_id": item.get("chat_id"), "name": item.get("name")})
        page_token = str(data.get("page_token") or "")
        if not data.get("has_more") or not page_token:
            return chats
