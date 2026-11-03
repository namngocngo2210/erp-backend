"""SQL đọc đơn đã chốt của một kênh — một câu, keyset theo ``orders.id``.

Đọc thẳng bảng ``orders`` (không qua mart ``ai.don_hang``): mart che SĐT và trễ
30 phút, còn feed conversion cần SĐT đầy đủ để nền tảng quảng cáo khớp người dùng.
Tham số đi qua psycopg (list → ARRAY), KHÔNG format chuỗi từ input.
"""

from __future__ import annotations

from datetime import datetime

from django.conf import settings
from django.db import connection

from apps.common.db_guards import statement_timeout
from apps.common.order_scope import sql_exclude_mirror_orders

# SKU gom bằng subquery tương quan trên index ``order_item_order_cr_idx`` —
# tối đa ``limit`` lần tra index, không N+1 phía Python.
_SQL = f"""
SELECT o.id, o.created_at, o.name, o.phone, o.phone_normalized,
       o.province, o.province_level2, o.kiotviet_location, o.district,
       o.sku, o.total_after_discount, o.order_source,
       a.sales_status_code,
       (SELECT string_agg(i.sku, ';' ORDER BY i.id)
          FROM order_items i
         WHERE i.order_id = o.id AND i.sku <> '') AS item_skus
FROM orders o
JOIN order_assessments a ON a.order_id = o.id
WHERE o.channel_id = %(channel_id)s
  AND o.deleted_at IS NULL
  AND o.created_at >= %(start)s
  AND o.created_at <= %(end)s
  AND a.sales_status_code = ANY(%(status_codes)s)
  AND {sql_exclude_mirror_orders("o")}
  {{after}}
ORDER BY o.id
LIMIT %(limit)s
"""


def closed_lead_rows(
    *,
    channel_id: int,
    start: datetime,
    end: datetime,
    status_codes: list[str],
    after: int | None,
    limit: int,
) -> list[dict]:
    params: dict = {
        "channel_id": channel_id,
        "start": start,
        "end": end,
        "status_codes": status_codes,
        "limit": limit,
    }
    # Không dùng `%(after)s IS NULL OR …`: psycopg không suy được kiểu NULL.
    after_clause = ""
    if after is not None:
        params["after"] = after
        after_clause = "AND o.id > %(after)s"
    with statement_timeout(settings.REPORT_QUERY_TIMEOUT_SECONDS), connection.cursor() as cur:
        cur.execute(_SQL.format(after=after_clause), params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
