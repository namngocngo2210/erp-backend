"""View tải video (stream) + cache video: forward header, ASGI-safe, force chỉ admin."""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.django_db


def _stream_resp(body: bytes, headers: dict) -> MagicMock:
    """Mock requests.Response cho stream_get: `.raw` file-like, đọc dần qua .read(n)."""
    r = MagicMock()
    r.status_code = 200
    r.headers = headers
    r.raw = io.BytesIO(body)
    return r


def test_download_streams_and_forwards_headers(api, shared_viewer, connection, fake_client, read_streaming_response):
    fake_client.stream_get.return_value = _stream_resp(
        b"abcdef", {"Content-Type": "video/mp4", "Content-Disposition": 'attachment; filename="v.mp4"', "Content-Length": "6"}
    )
    r = api(shared_viewer).get("/api/media/contents/12/download")
    assert r.status_code == 200
    assert r["Content-Type"] == "video/mp4"
    assert r["Content-Disposition"] == 'attachment; filename="v.mp4"'
    assert r["Content-Length"] == "6"
    assert r.is_async is True
    assert read_streaming_response(r) == b"abcdef"
    assert fake_client.stream_get.call_args.args == ("/contents/12/download",)
    assert fake_client.stream_get.call_args.kwargs["timeout"] == 120.0


def test_download_gzip_upstream_no_content_length(api, shared_viewer, connection, fake_client, read_streaming_response):
    """Upstream nén gzip: Content-Length gốc (nén) không được forward vì
    `decode_content = True` đã trả bytes giải nén — copy nguyên văn sẽ sai kích
    thước và làm trình duyệt cắt cụt/tải treo. Content-Disposition vẫn forward."""
    fake_client.stream_get.return_value = _stream_resp(
        b"abcdef",
        {
            "Content-Type": "video/mp4",
            "Content-Disposition": 'attachment; filename="v.mp4"',
            "Content-Length": "6",
            "Content-Encoding": "gzip",
        },
    )
    r = api(shared_viewer).get("/api/media/contents/12/download")
    assert r.status_code == 200
    assert r.has_header("Content-Length") is False
    assert r["Content-Disposition"] == 'attachment; filename="v.mp4"'
    assert read_streaming_response(r) == b"abcdef"


def test_download_upstream_errors_mapped(api, shared_viewer, connection, fake_client, upstream_error):
    fake_client.stream_get.side_effect = upstream_error("photo_post", 422)
    r = api(shared_viewer).get("/api/media/contents/12/download")
    assert (r.status_code, r.data["code"]) == (422, "photo_post")


def test_video_cache_force_only_admin(api, shared_viewer, shared_admin, connection, fake_client):
    fake_client.json_request.return_value = (202, {"video_status": "pending"})
    assert api(shared_viewer).post("/api/media/contents/12/video/cache").status_code == 202
    assert fake_client.json_request.call_args.kwargs["params"] is None
    assert api(shared_viewer).post("/api/media/contents/12/video/cache?force=1").status_code == 403
    assert api(shared_admin).post("/api/media/contents/12/video/cache?force=1").status_code == 202
    assert fake_client.json_request.call_args.kwargs["params"] == [("force", "1")]
