"""Token WebSocket đi qua Sec-WebSocket-Protocol (audit 2026-09-23 H2).

Hợp đồng với frontend: `new WebSocket(url, ["desi.jwt.v1", <access_token>])`.
Ba đường: subprotocol (chuẩn), `?token=` (client cũ, giữ 1 release, log WARNING
không in token), không có token → AnonymousUser. Consumer phải chọn lại
"desi.jwt.v1" khi accept nếu client có offer.

Không dùng WebsocketCommunicator (không có tiền lệ — xem apps/exports/tests/test_ws.py):
gọi thẳng middleware/consumer với scope dict, phần chạm DB được patch.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase
from rest_framework_simplejwt.tokens import AccessToken

from apps.channels_core import jwt_middleware
from apps.channels_core.consumers import CRMConsumer
from apps.channels_core.jwt_middleware import (
    WS_JWT_SUBPROTOCOL,
    JwtAuthMiddleware,
    extract_ws_token,
)

_STUB_USER = SimpleNamespace(id=7, pk=7, is_active=True, is_authenticated=True)


def _access_token() -> str:
    return str(AccessToken.for_user(_STUB_USER))


class ExtractTokenTests(SimpleTestCase):
    def test_subprotocol_wins(self):
        scope = {"subprotocols": [WS_JWT_SUBPROTOCOL, "tok-abc"], "query_string": b"token=old"}
        self.assertEqual(extract_ws_token(scope), ("tok-abc", "subprotocol"))

    def test_query_fallback_logs_warning_without_token(self):
        scope = {"subprotocols": [], "query_string": b"token=secret-value-123", "path": "/ws/crm/"}
        with self.assertLogs(jwt_middleware.logger, level="WARNING") as logs:
            self.assertEqual(extract_ws_token(scope), ("secret-value-123", "query"))
        self.assertTrue(any("query string" in line for line in logs.output))
        self.assertFalse(any("secret-value-123" in line for line in logs.output))

    def test_no_token(self):
        self.assertEqual(extract_ws_token({"subprotocols": [], "query_string": b""}), ("", ""))
        # Offer sai dạng (không có phần tử token) → không lấy, không crash.
        self.assertEqual(extract_ws_token({"subprotocols": [WS_JWT_SUBPROTOCOL]}), ("", ""))


class MiddlewareTests(SimpleTestCase):
    def _run(self, scope: dict):
        inner = AsyncMock()
        with patch.object(jwt_middleware, "_get_user", AsyncMock(return_value=_STUB_USER)) as lookup:
            async_to_sync(JwtAuthMiddleware(inner))(scope, AsyncMock(), AsyncMock())
        inner.assert_awaited_once()
        return scope["user"], lookup

    def test_subprotocol_token_authenticates(self):
        user, lookup = self._run(
            {"subprotocols": [WS_JWT_SUBPROTOCOL, _access_token()], "query_string": b""}
        )
        self.assertIs(user, _STUB_USER)
        lookup.assert_awaited_once()
        self.assertEqual(lookup.await_args.args[0], 7)

    def test_query_token_still_authenticates_this_release(self):
        with self.assertLogs(jwt_middleware.logger, level="WARNING"):
            user, _ = self._run(
                {"subprotocols": [], "query_string": f"token={_access_token()}".encode()}
            )
        self.assertIs(user, _STUB_USER)

    def test_no_token_is_anonymous(self):
        user, lookup = self._run({"subprotocols": [], "query_string": b""})
        self.assertFalse(getattr(user, "is_authenticated", True))
        lookup.assert_not_awaited()

    def test_garbage_subprotocol_token_is_anonymous(self):
        with self.assertLogs(jwt_middleware.logger, level="WARNING"):
            user, lookup = self._run(
                {"subprotocols": [WS_JWT_SUBPROTOCOL, "not-a-jwt"], "query_string": b""}
            )
        self.assertFalse(getattr(user, "is_authenticated", True))
        lookup.assert_not_awaited()


class _FakeUser:
    id = 55
    is_authenticated = True


def _consumer(scope: dict) -> CRMConsumer:
    consumer = CRMConsumer()
    consumer.scope = {"user": _FakeUser(), **scope}
    consumer.channel_layer = AsyncMock()
    consumer.channel_name = "specific.channel.abcd"
    consumer.accept = AsyncMock()
    consumer._load_scope = AsyncMock(return_value=("all", None, {}, True))
    return consumer


class ConsumerSubprotocolTests(SimpleTestCase):
    def test_accept_selects_offered_subprotocol(self):
        consumer = _consumer({"subprotocols": [WS_JWT_SUBPROTOCOL, "tok"]})
        async_to_sync(consumer.connect)()
        consumer.accept.assert_awaited_once_with(subprotocol=WS_JWT_SUBPROTOCOL)

    def test_accept_without_offer_selects_nothing(self):
        consumer = _consumer({"subprotocols": []})
        async_to_sync(consumer.connect)()
        consumer.accept.assert_awaited_once_with(subprotocol=None)
