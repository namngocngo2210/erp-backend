from urllib.parse import parse_qs
import logging

from channels.middleware import BaseMiddleware
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import TokenError

from apps.channels_core.handshake import handshake_db_gate

logger = logging.getLogger(__name__)

# Hợp đồng với frontend (chốt 2026-09-23, audit H2): client mở
#   new WebSocket(url, ["desi.jwt.v1", <access_token>])
# → token nằm trong header `Sec-WebSocket-Protocol`, KHÔNG vào URL nên không vào
# access log uvicorn/nginx/Loki. Server phải chọn lại đúng subprotocol này khi
# accept (consumers.py) — trình duyệt đóng kết nối nếu server không chọn một
# subprotocol đã offer.
WS_JWT_SUBPROTOCOL = "desi.jwt.v1"


def extract_ws_token(scope) -> tuple[str, str]:
    """Trả (token, nguồn). Nguồn ∈ {"subprotocol", "query", ""}.

    Ưu tiên subprotocol. Fallback `?token=` giữ MỘT release nữa cho client cũ và
    log WARNING (không in token) để đo còn ai dùng trước khi gỡ.
    """
    subprotocols = list(scope.get("subprotocols") or [])
    if len(subprotocols) >= 2 and subprotocols[0] == WS_JWT_SUBPROTOCOL and subprotocols[1]:
        return str(subprotocols[1]), "subprotocol"
    query_string = scope.get("query_string", b"")
    if isinstance(query_string, bytes):
        query_string = query_string.decode("utf-8", errors="replace")
    token = (parse_qs(query_string).get("token") or [""])[0]
    if token:
        logger.warning(
            "WS auth: token đi qua query string (client cũ) — sẽ gỡ ở release sau; "
            "dùng subprotocol %s. path=%s",
            WS_JWT_SUBPROTOCOL,
            scope.get("path", ""),
        )
        return token, "query"
    return "", ""


@database_sync_to_async
def _get_user(user_id: int, token_version):
    User = get_user_model()
    try:
        user = User.objects.get(id=user_id, is_active=True, deleted_at__isnull=True)
    except User.DoesNotExist:
        return AnonymousUser()
    except Exception:
        logger.exception("WS user lookup failed for user_id=%s", user_id)
        return AnonymousUser()

    # Từ chối token phát hành trước khi mật khẩu thay đổi (xem apps/users/jwt.py).
    # Token đời cũ không có claim → coi như 0, khớp mặc định token_version=0.
    expected = int(getattr(user, "token_version", 0) or 0)
    if int(token_version if token_version is not None else 0) != expected:
        return AnonymousUser()
    return user


class JwtAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        scope["user"] = AnonymousUser()

        try:
            token, _source = extract_ws_token(scope)
            if token:
                payload = AccessToken(token)
                user_id = payload.get("user_id")
                if user_id is not None:
                    # Gate: khi cả trăm tab reconnect cùng lúc, phần chạm DB phải
                    # xếp hàng chứ không mở cả trăm connection Postgres đồng thời
                    # (sự cố 2026-07-23 — xem apps/channels_core/handshake.py).
                    async with handshake_db_gate():
                        scope["user"] = await _get_user(
                            int(user_id), payload.get("token_version")
                        )
        except (ValueError, TokenError):
            logger.warning("WS JWT auth failed; falling back to AnonymousUser")
            scope["user"] = AnonymousUser()
        except Exception:
            logger.exception("WS middleware unexpected error")
            scope["user"] = AnonymousUser()

        return await super().__call__(scope, receive, send)


def JwtAuthMiddlewareStack(inner):
    return JwtAuthMiddleware(inner)
