"""
Minimal Lark Bitable client (batch create/update records).

Token + request plumbing sống ở ``apps.common.lark_open_api`` (dùng chung với
các module khác); file này chỉ còn phần Bitable-specific.

Docs:
  - https://open.larksuite.com/document/server-docs/docs/bitable-v1/app-table-record/batch_create
  - https://open.larksuite.com/document/server-docs/docs/bitable-v1/app-table-record/batch_update
"""

from __future__ import annotations

import logging

from apps.common.lark_open_api import (
    LarkOpenAPIError,
    get_tenant_access_token,  # noqa: F401  (re-export cho caller cũ)
    lark_request,
)

logger = logging.getLogger(__name__)

# Bitable record batch limit per request (Lark caps at 1000; staying conservative).
_BATCH_LIMIT = 500

# Backward-compat alias: caller cũ bắt LarkBitableError vẫn hoạt động.
LarkBitableError = LarkOpenAPIError


def batch_create_records(app_token: str, table_id: str, records: list[dict]) -> list[dict]:
    """Create records. Returns the created records (with ``record_id``)."""
    if not records:
        return []
    path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
    created: list[dict] = []
    for chunk in _chunked(records, _BATCH_LIMIT):
        data = lark_request("POST", path, json_body={"records": chunk})
        created.extend(data.get("records") or [])
    return created


def batch_update_records(app_token: str, table_id: str, records: list[dict]) -> list[dict]:
    """Update records. Each item must contain ``record_id`` + ``fields``."""
    if not records:
        return []
    path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_update"
    updated: list[dict] = []
    for chunk in _chunked(records, _BATCH_LIMIT):
        data = lark_request("POST", path, json_body={"records": chunk})
        updated.extend(data.get("records") or [])
    return updated


def _chunked(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]
