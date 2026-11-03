"""Cấp access token CNV còn hạn cho REST client, tự refresh khi cần.

Pattern ``ensure_token`` của best_express: trước khi tốn một lượt refresh,
đọc lại row từ DB (worker khác có thể vừa refresh xong) và rate-limit refresh
bằng counter Redis — CNV trả 401 hàng loạt thì không thành bão refresh.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from apps.cnv_loyalty.exceptions import CnvAuthError
from apps.cnv_loyalty.infrastructure.sso_client import CnvSsoClient
from apps.cnv_loyalty.models import CnvConnection

logger = logging.getLogger(__name__)

# Token coi như hết hạn sớm hơn thực tế một khoảng đệm — tránh dùng token chết
# giữa chừng request.
TOKEN_SKEW = timedelta(seconds=120)
REFRESH_RATE_LIMIT = 5  # lần/phút, cùng ngưỡng login của best_express

_TOKEN_FIELDS = ["access_token", "refresh_token", "token_type", "token_expires_at"]


def _token_valid(connection: CnvConnection) -> bool:
    return bool(
        connection.access_token
        and connection.token_expires_at
        and connection.token_expires_at > timezone.now() + TOKEN_SKEW
    )


def ensure_access_token(
    connection: CnvConnection, force_refresh: bool = False
) -> tuple[str, str]:
    """Trả về ``(token_type, access_token)`` còn hạn; refresh nếu cần."""
    if not force_refresh and _token_valid(connection):
        return connection.token_type or "Bearer", connection.access_token
    return _refresh(connection)


def _refresh(connection: CnvConnection) -> tuple[str, str]:
    stale_token = connection.access_token
    connection.refresh_from_db(fields=_TOKEN_FIELDS)
    if connection.access_token != stale_token and _token_valid(connection):
        return connection.token_type or "Bearer", connection.access_token

    if not connection.refresh_token:
        raise CnvAuthError(
            "CNV: chưa có refresh token — admin cần kết nối SSO qua /api/cnv/connect."
        )

    rate_key = f"cnv:refresh_count:{connection.pk}"
    cache.add(rate_key, 0, timeout=60)
    if cache.incr(rate_key) > REFRESH_RATE_LIMIT:
        raise CnvAuthError("CNV: vượt giới hạn refresh token/phút — thử lại sau.")

    logger.info("CNV SSO refresh token: connection=%s", connection.pk)
    bundle = CnvSsoClient(connection).refresh(connection.refresh_token)
    connection.access_token = bundle.access_token
    # CNV có thể xoay refresh token; không trả thì giữ token cũ.
    if bundle.refresh_token:
        connection.refresh_token = bundle.refresh_token
    connection.token_type = bundle.token_type or "Bearer"
    connection.token_expires_at = timezone.now() + timedelta(
        seconds=bundle.expires_in or 3600
    )
    connection.save(update_fields=[*_TOKEN_FIELDS, "updated_at"])
    return connection.token_type, connection.access_token
