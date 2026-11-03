"""Lấy và cache uid của FoxiaConnection.

uid là định danh user, không phải session token — Odoo trả cùng một số cho tới
khi tài khoản bị vô hiệu hoá. Vì vậy cache trong DB và chỉ authenticate lại khi
chưa có hoặc bị ép làm mới, thay vì mỗi lượt sync một lần.

Trạng thái `revoked` là điểm dừng có chủ đích: API key bị thu hồi thì không có
gì để retry: job dừng, cảnh báo bật, người vận hành xin key mới. Retry vòng lặp
ở đây chỉ nhân log lỗi lên — đúng bài học vòng đời token Facebook 25–27/07.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from apps.foxia.exceptions import FoxiaAuthError
from apps.foxia.metrics import set_auth_ok
from apps.foxia.models import FoxiaAuthStatus

logger = logging.getLogger(__name__)


def ensure_uid(connection, client=None, force_refresh: bool = False) -> int:
    """Trả uid dùng được. Raise FoxiaAuthError nếu Foxia từ chối."""
    if not force_refresh and connection.uid and connection.auth_status == FoxiaAuthStatus.OK:
        return connection.uid

    if client is None:
        from apps.foxia.infrastructure.foxia_client import FoxiaClient

        client = FoxiaClient(connection)

    try:
        uid = client.authenticate()
    except FoxiaAuthError as exc:
        _mark(connection, FoxiaAuthStatus.REVOKED, str(exc))
        raise
    except Exception as exc:  # noqa: BLE001 — lỗi mạng/API cũng phải ghi lại trạng thái
        _mark(connection, FoxiaAuthStatus.ERROR, str(exc))
        raise

    connection.uid = uid
    connection.uid_checked_at = timezone.now()
    connection.auth_status = FoxiaAuthStatus.OK
    connection.auth_error = ""
    connection.save(update_fields=["uid", "uid_checked_at", "auth_status", "auth_error", "updated_at"])
    set_auth_ok(True)
    return uid


def _mark(connection, status: str, error: str) -> None:
    connection.auth_status = status
    connection.auth_error = error[:2000]
    connection.uid_checked_at = timezone.now()
    connection.save(update_fields=["auth_status", "auth_error", "uid_checked_at", "updated_at"])
    set_auth_ok(False)
    logger.warning("Foxia auth -> %s: %s", status, error[:300])
