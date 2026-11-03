"""Vòng đời access token của Facebook App: kiểm tra, phân loại, gia hạn.

Trước module này, ``FacebookApp`` chỉ có một chuỗi ``access_token`` nhập tay —
không biết hạn, không biết trạng thái. Sự cố 25–27/07/2026 (token user hết hạn lúc
nửa đêm, chi tiêu quảng cáo đứng im 2 ngày) là hệ quả trực tiếp.

Ghi chú thiết kế:

- Mọi thay đổi trạng thái ghi qua ``QuerySet.update()`` chứ không ``save()``: tránh
  đè lên ``access_token`` mới mà admin vừa dán trong lúc cron đang chạy, và không
  kích hoạt signal reset ở ``signals.py``.
- ``expires_at = 0`` từ Graph API nghĩa là token KHÔNG hết hạn (System User token
  của Business Manager) — đây là dạng token duy nhất nên dùng cho máy-gọi-máy.
- ``fb_exchange_token`` yêu cầu token còn sống; token đã chết thì không API nào cứu
  được, bắt buộc con người cấp lại. Vì vậy giá trị thật nằm ở cảnh báo sớm.

Docs: https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, UTC
from typing import NamedTuple

from django.utils import timezone

from apps.common.http_client import HttpClient
from apps.sysconfig import sysconfig

from ..models import FacebookApp
from ..services import GRAPH_API_BASE, FacebookGraphError, _error_from_response

logger = logging.getLogger(__name__)

_client = HttpClient()

DEFAULT_WARN_DAYS = 7


def _warn_days() -> int:
    return int(sysconfig.get("FB_TOKEN_EXPIRY_WARN_DAYS") or DEFAULT_WARN_DAYS)


def _to_datetime(epoch) -> datetime | None:
    """Epoch giây → datetime aware. ``0``/rỗng = không hết hạn → ``None``."""
    try:
        value = int(epoch or 0)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return datetime.fromtimestamp(value, tz=UTC)


class TokenInfo(NamedTuple):
    is_valid: bool
    token_type: str
    expires_at: datetime | None
    data_access_expires_at: datetime | None
    scopes: tuple[str, ...]
    error_message: str

    @property
    def never_expires(self) -> bool:
        return self.expires_at is None


def _apply(app: FacebookApp, **fields) -> None:
    """Ghi thẳng xuống DB rồi đồng bộ lại instance trong bộ nhớ."""
    FacebookApp.objects.filter(pk=app.pk).update(updated_at=timezone.now(), **fields)
    for name, value in fields.items():
        setattr(app, name, value)


def debug_token(app: FacebookApp) -> TokenInfo:
    """Hỏi Graph API về chính token đang lưu, dùng app access token làm "người soi"."""
    response = _client.get(
        f"{GRAPH_API_BASE}/debug_token",
        params={
            "input_token": app.access_token,
            "access_token": f"{app.app_id}|{app.app_secret}",
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise _error_from_response(response, "debug_token error")

    data = (response.json() or {}).get("data") or {}
    error = data.get("error") or {}
    return TokenInfo(
        is_valid=bool(data.get("is_valid")),
        token_type=str(data.get("type") or ""),
        expires_at=_to_datetime(data.get("expires_at")),
        data_access_expires_at=_to_datetime(data.get("data_access_expires_at")),
        scopes=tuple(data.get("scopes") or ()),
        error_message=str(error.get("message") or ""),
    )


def mark_token_invalid(app: FacebookApp, message: str) -> None:
    """Đánh dấu token hỏng. Giữ nguyên ``token_invalid_since`` nếu đã hỏng từ trước."""
    now = timezone.now()
    fields = {
        "token_status": FacebookApp.TOKEN_INVALID,
        "token_error": str(message)[:1000],
        "token_checked_at": now,
    }
    if app.token_invalid_since is None:
        fields["token_invalid_since"] = now
    _apply(app, **fields)


def mark_token_ok(app: FacebookApp) -> None:
    """Xoá dấu vết hỏng sau một lần gọi Graph API thành công."""
    if app.token_status == FacebookApp.TOKEN_OK and not app.token_error:
        return
    _apply(
        app,
        token_status=FacebookApp.TOKEN_OK,
        token_error="",
        token_invalid_since=None,
        token_checked_at=timezone.now(),
    )


def refresh_token_metadata(app: FacebookApp) -> FacebookApp:
    """Gọi ``debug_token`` và ghi trạng thái/hạn vào model.

    Lỗi mạng hoặc lỗi tạm thời của Graph API KHÔNG bị coi là token hỏng — chỉ ghi
    log, giữ nguyên trạng thái cũ để lượt sau thử lại.
    """
    try:
        info = debug_token(app)
    except FacebookGraphError as exc:
        logger.warning("debug_token thất bại cho app %s: %s", app.pk, exc)
        return app
    except Exception:  # noqa: BLE001 — lỗi mạng không được giết cả vòng cron
        logger.exception("debug_token lỗi bất thường cho app %s", app.pk)
        return app

    now = timezone.now()
    if not info.is_valid:
        _apply(
            app,
            token_expires_at=info.expires_at,
            data_access_expires_at=info.data_access_expires_at,
        )
        mark_token_invalid(app, info.error_message or "Token không hợp lệ")
        return app

    expiring = (
        info.expires_at is not None
        and info.expires_at <= now + timedelta(days=_warn_days())
    )
    _apply(
        app,
        token_status=FacebookApp.TOKEN_EXPIRING if expiring else FacebookApp.TOKEN_OK,
        token_expires_at=info.expires_at,
        data_access_expires_at=info.data_access_expires_at,
        token_checked_at=now,
        token_error="",
        token_invalid_since=None,
    )
    return app


def try_extend_token(app: FacebookApp) -> bool:
    """Đổi token hiện tại lấy long-lived token (60 ngày). Trả True nếu đã đổi.

    Bỏ qua khi token đã chết (API yêu cầu token còn sống) hoặc khi đã xác nhận là
    System User token không hết hạn — gọi thêm chỉ tốn request.
    """
    if app.token_status == FacebookApp.TOKEN_INVALID:
        return False
    if app.token_never_expires:
        return False

    try:
        response = _client.get(
            f"{GRAPH_API_BASE}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app.app_id,
                "client_secret": app.app_secret,
                "fb_exchange_token": app.access_token,
            },
            timeout=30,
        )
    except Exception:  # noqa: BLE001
        logger.exception("fb_exchange_token lỗi mạng cho app %s", app.pk)
        return False

    if response.status_code != 200:
        logger.warning(
            "fb_exchange_token thất bại cho app %s: %s", app.pk, _error_from_response(response, "exchange"),
        )
        return False

    payload = response.json() or {}
    new_token = str(payload.get("access_token") or "").strip()
    if not new_token:
        logger.warning("fb_exchange_token không trả access_token cho app %s", app.pk)
        return False

    expires_in = int(payload.get("expires_in") or 0)
    now = timezone.now()
    _apply(
        app,
        access_token=new_token,
        token_status=FacebookApp.TOKEN_OK,
        token_expires_at=now + timedelta(seconds=expires_in) if expires_in else None,
        token_checked_at=now,
        token_error="",
        token_invalid_since=None,
    )
    logger.info("Đã gia hạn token long-lived cho app %s (expires_in=%s)", app.pk, expires_in)
    return True
