"""Helper parse query params dùng chung cho list endpoint của warranty_codes.

Mirror pattern ``_query_int`` của ``apps.warranty.claim_views`` — parse lỗi
thì bỏ filter (trả ``None``) thay vì để lỗi ValueError/ValidationError rơi
thẳng xuống ORM và làm 500 (vd ``?product_id=abc``, ``?date_from=xyz``).
"""

from __future__ import annotations

import datetime


def query_int(request, key: str) -> int | None:
    try:
        return int(request.query_params.get(key))
    except (TypeError, ValueError):
        return None


def query_date(request, key: str) -> datetime.date | None:
    raw = request.query_params.get(key)
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        return None
