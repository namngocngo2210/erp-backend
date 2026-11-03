"""GET /contents/<id>/video-stream: bơm bytes video từ ads-crm (đã theo redirect
tới R2) qua chính ERP — đường lùi khi trình duyệt của người xem không tới được
`*.r2.cloudflarestorage.com` trực tiếp. Hỗ trợ Range để tua/seek.

Nhóm test `test_cookie_auth_*` phủ `RefreshCookieAuthentication`
(`apps/media/transport/stream_auth.py`) — endpoint DUY NHẤT chấp nhận cookie
`refresh_token` thay Bearer, vì thẻ <video src=…> không gắn được header."""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest
from rest_framework.test import APIClient

from apps.users.jwt import VersionedRefreshToken

pytestmark = pytest.mark.django_db


def _stream_resp(body: bytes, headers: dict, status: int = 200) -> MagicMock:
    """Mock requests.Response cho stream_media: `.raw` file-like, đọc dần qua .read(n)."""
    r = MagicMock()
    r.status_code = status
    r.headers = headers
    r.raw = io.BytesIO(body)
    return r


def _client_with_refresh_cookie(user) -> APIClient:
    """APIClient KHÔNG force_authenticate — đi đúng đường DRF authentication
    thật (gắn cookie `refresh_token`, không có header Authorization)."""
    c = APIClient()
    c.cookies["refresh_token"] = str(VersionedRefreshToken.for_user(user))
    return c


def test_stream_passes_through_and_forwards_headers(api, shared_viewer, connection, fake_client, read_streaming_response):
    fake_client.stream_media.return_value = _stream_resp(
        b"abcdef", {"Content-Type": "video/mp4", "Content-Length": "6", "Accept-Ranges": "bytes"}
    )
    r = api(shared_viewer).get("/api/media/contents/12/video-stream")
    assert r.status_code == 200
    assert r["Content-Type"] == "video/mp4"
    assert r["Content-Length"] == "6"
    assert r["Accept-Ranges"] == "bytes"
    assert r["Cache-Control"] == "private, max-age=3600"
    assert r.is_async is True
    assert read_streaming_response(r) == b"abcdef"
    assert fake_client.stream_media.call_args.args == ("/contents/12/video",)
    assert fake_client.stream_media.call_args.kwargs["timeout"] == 120.0
    assert fake_client.stream_media.call_args.kwargs["range_header"] is None


def test_range_request_forwards_range_and_returns_206(api, shared_viewer, connection, fake_client, read_streaming_response):
    fake_client.stream_media.return_value = _stream_resp(
        b"bcdef",
        {"Content-Type": "video/mp4", "Content-Length": "5", "Content-Range": "bytes 1-5/6", "Accept-Ranges": "bytes"},
        status=206,
    )
    r = api(shared_viewer).get("/api/media/contents/12/video-stream", HTTP_RANGE="bytes=1-5")
    assert r.status_code == 206
    assert r["Content-Range"] == "bytes 1-5/6"
    assert read_streaming_response(r) == b"bcdef"
    assert fake_client.stream_media.call_args.kwargs["range_header"] == "bytes=1-5"


def test_gzip_upstream_no_content_length(api, shared_viewer, connection, fake_client, read_streaming_response):
    """`decode_content = True` trả bytes đã giải nén; Content-Length upstream là
    kích thước nén gốc — forward nguyên văn sẽ sai, trình duyệt cắt cụt/treo tải."""
    fake_client.stream_media.return_value = _stream_resp(
        b"abcdef",
        {"Content-Type": "video/mp4", "Content-Length": "6", "Content-Encoding": "gzip"},
    )
    r = api(shared_viewer).get("/api/media/contents/12/video-stream")
    assert r.status_code == 200
    assert r.has_header("Content-Length") is False
    assert read_streaming_response(r) == b"abcdef"


def test_unprovisioned_user_forbidden_no_upstream_call(api, unprovisioned, connection, fake_client):
    r = api(unprovisioned).get("/api/media/contents/12/video-stream")
    assert (r.status_code, r.data["code"]) == (403, "media_not_provisioned")
    fake_client.stream_media.assert_not_called()


def test_upstream_content_not_found(api, shared_viewer, connection, fake_client, upstream_error):
    fake_client.stream_media.side_effect = upstream_error("content_not_found", 404)
    r = api(shared_viewer).get("/api/media/contents/12/video-stream")
    assert (r.status_code, r.data["code"]) == (404, "content_not_found")


def test_bearer_header_still_works_without_force_authenticate(shared_viewer, connection, fake_client, read_streaming_response):
    """Không dùng fixture `api` (force_authenticate né hẳn tầng authentication) —
    dựng access token thật, gắn header Authorization, để chắc chắn thêm
    RefreshCookieAuthentication vào danh sách không phá đường Bearer sẵn có."""
    access = str(VersionedRefreshToken.for_user(shared_viewer).access_token)
    fake_client.stream_media.return_value = _stream_resp(
        b"abcdef", {"Content-Type": "video/mp4", "Content-Length": "6"}
    )
    c = APIClient()
    r = c.get("/api/media/contents/12/video-stream", HTTP_AUTHORIZATION=f"Bearer {access}")
    assert r.status_code == 200
    assert read_streaming_response(r) == b"abcdef"


def test_cookie_auth_streams_when_no_bearer_header(shared_viewer, connection, fake_client, read_streaming_response):
    fake_client.stream_media.return_value = _stream_resp(
        b"abcdef", {"Content-Type": "video/mp4", "Content-Length": "6"}
    )
    r = _client_with_refresh_cookie(shared_viewer).get("/api/media/contents/12/video-stream")
    assert r.status_code == 200
    assert read_streaming_response(r) == b"abcdef"
    fake_client.stream_media.assert_called_once()


def test_cookie_auth_garbage_cookie_unauthorized(shared_viewer, connection, fake_client):
    c = APIClient()
    c.cookies["refresh_token"] = "not-a-real-jwt"
    r = c.get("/api/media/contents/12/video-stream")
    assert r.status_code == 401
    fake_client.stream_media.assert_not_called()


def test_cookie_auth_version_mismatch_unauthorized(shared_viewer, connection, fake_client):
    """Đổi mật khẩu (tăng token_version) → refresh cookie phát trước đó bị từ chối,
    dù còn hạn và ký hợp lệ — cùng cơ chế `token_refresh_view` đã áp dụng."""
    stale_cookie = str(VersionedRefreshToken.for_user(shared_viewer))
    shared_viewer.token_version = (shared_viewer.token_version or 0) + 1
    shared_viewer.save(update_fields=["token_version"])

    c = APIClient()
    c.cookies["refresh_token"] = stale_cookie
    r = c.get("/api/media/contents/12/video-stream")
    assert r.status_code == 401
    fake_client.stream_media.assert_not_called()


def test_cookie_auth_unprovisioned_user_forbidden_no_upstream_call(unprovisioned, connection, fake_client):
    r = _client_with_refresh_cookie(unprovisioned).get("/api/media/contents/12/video-stream")
    assert (r.status_code, r.data["code"]) == (403, "media_not_provisioned")
    fake_client.stream_media.assert_not_called()


def test_cookie_not_accepted_on_other_media_endpoints(shared_viewer, connection, fake_client):
    """RefreshCookieAuthentication CHỈ gắn cho content_video_stream — endpoint
    Media khác (vd. /me) không được khai thêm, nên cookie đơn thuần không đủ."""
    c = APIClient()
    c.cookies["refresh_token"] = str(VersionedRefreshToken.for_user(shared_viewer))
    r = c.get("/api/media/me")
    assert r.status_code == 401
