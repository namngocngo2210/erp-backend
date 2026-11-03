"""Dựng dữ liệu trong ai_stg.chat_* bằng SQL trần.

Không có model Django cho các bảng này (chúng là foreign table trên prod), nên
test phải INSERT tay. id tự tăng bằng bộ đếm — bảng nguồn không có sequence.
"""

import itertools

from django.db import connection
from django.utils import timezone

_ids = itertools.count(1)


def _insert(table: str, **values) -> int:
    cols = ", ".join(values)
    marks = ", ".join(["%s"] * len(values))
    with connection.cursor() as cur:
        cur.execute(f"INSERT INTO ai_stg.{table} ({cols}) VALUES ({marks})", list(values.values()))
    return values.get("id", 0)


def mk_page(**kw) -> int:
    values = {
        "id": next(_ids),
        "pancake_page_id": f"pp-{next(_ids)}",
        "platform": "facebook",
        "name": "Delax Fanpage",
        "status": "active",
    }
    values.update(kw)
    return _insert("chat_pages", **values)


def mk_customer(page_id: int, **kw) -> int:
    values = {
        "id": next(_ids),
        "page_id": page_id,
        "pancake_customer_id": f"pc-{next(_ids)}",
        "name": "Khách Test",
        "phone": "",
        "fb_id": "",
        "gender": "",
    }
    values.update(kw)
    return _insert("chat_customers", **values)


def mk_conversation(page_id: int, **kw) -> int:
    now = timezone.now()
    values = {
        "id": next(_ids),
        "page_id": page_id,
        "pancake_conversation_id": f"conv-{next(_ids)}",
        "customer_id": None,
        "type": "INBOX",
        "snippet": "",
        "message_count": 0,
        "last_message_at": now,
        "updated_at": now,
        "created_at": now,
    }
    values.update(kw)
    return _insert("chat_conversations", **values)


def mk_message(conversation_id: int, page_id: int, **kw) -> int:
    values = {
        "id": next(_ids),
        "page_id": page_id,
        "conversation_id": conversation_id,
        "pancake_message_id": f"msg-{next(_ids)}",
        "sender_id": "",
        "direction": "in",
        "type": "INBOX",
        "content": "xin chào",
        "attachments": None,
        "inserted_at": timezone.now(),
    }
    values.update(kw)
    return _insert("chat_messages", **values)


def mk_tag(page_id: int, name: str, **kw) -> int:
    values = {
        "id": next(_ids),
        "page_id": page_id,
        "pancake_tag_id": f"tag-{next(_ids)}",
        "name": name,
        "color": "",
    }
    values.update(kw)
    return _insert("chat_tags", **values)


def tag_conversation(conversation_id: int, tag_id: int) -> None:
    _insert("chat_conversation_tags", conversation_id=conversation_id, tag_id=tag_id)
