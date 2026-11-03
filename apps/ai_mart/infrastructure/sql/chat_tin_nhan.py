"""1 dòng/tin nhắn chat. Nguồn là foreign table ai_stg.chat_* (spec C1).

Bộ lọc "chỉ tin có nội dung" hiện là no-op (đo 04/09/2026: 0/769.234 tin có
content rỗng) nhưng phải giữ: lần crawl sau tin đính-kèm-thuần sẽ có content
rỗng và không được lọt vào bảng chữ.
"""

from apps.ai_mart.infrastructure.sql.base import MartTable
from apps.analytics.sql.tz import BUSINESS_TZ

# Tin nhắn append-only nên không có updated_at riêng — lấy theo hội thoại vừa đổi.
_CANDIDATES = """
SELECT m.id
FROM ai_stg.chat_messages m
JOIN ai_stg.chat_conversations c ON c.id = m.conversation_id
WHERE c.updated_at > %(cutoff)s
"""

_HAS_CONTENT = "NULLIF(btrim(coalesce(m.content, '')), '') IS NOT NULL"


def _select(where: str) -> str:
    return f"""
SELECT
    m.id AS tin_nhan_id,
    m.conversation_id AS hoi_thoai_id,
    NULLIF(btrim(coalesce(p.name, '')), '') AS page_ten,
    m.inserted_at AS gui_luc,
    (m.inserted_at AT TIME ZONE '{BUSINESS_TZ}')::date AS gui_luc_ngay,
    ai.nhan('chieu_tin_chat', m.direction) AS chieu_text,
    ai.nhan('loai_tin_chat', m.type) AS loai_tin_text,
    btrim(m.content) AS noi_dung,
    CASE jsonb_typeof(m.attachments)
        WHEN 'array' THEN jsonb_array_length(m.attachments) > 0
        WHEN 'null' THEN false
        ELSE m.attachments IS NOT NULL
    END AS co_dinh_kem,
    now() AS dong_bo_luc
FROM ai_stg.chat_messages m
LEFT JOIN ai_stg.chat_pages p ON p.id = m.page_id
WHERE {_HAS_CONTENT} AND ({where})
"""


MART = MartTable(
    name="ai.tin_nhan",
    key_column="tin_nhan_id",
    source_key="m.id",
    group="hot",
    select_sql=_select,
    candidate_ids_sql=_CANDIDATES,
)
