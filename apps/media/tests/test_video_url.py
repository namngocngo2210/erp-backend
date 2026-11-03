"""GET /contents/<id>/video-url: URL phát trực tiếp (redirect 302 upstream) hoặc báo
fallback proxy (upstream tự stream, 200 không Location) — spec: xem task-video-url-report."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def test_redirect_returns_location_url(api, shared_viewer, connection, fake_client):
    fake_client.video_location.return_value = (302, "https://r2.example/presigned?sig=abc")
    r = api(shared_viewer).get("/api/media/contents/12/video-url")
    assert r.status_code == 200
    assert r.data == {"url": "https://r2.example/presigned?sig=abc", "mode": "redirect"}
    assert fake_client.video_location.call_args.args == ("/contents/12/video",)


def test_proxy_fallback_when_no_location(api, shared_viewer, connection, fake_client):
    """Cấu hình fallback trên ads-crm: 200 tự stream, không Location → frontend rơi
    về endpoint /download thay vì phát trực tiếp."""
    fake_client.video_location.return_value = (200, None)
    r = api(shared_viewer).get("/api/media/contents/12/video-url")
    assert r.status_code == 200
    assert r.data == {"url": None, "mode": "proxy"}


def test_unprovisioned_user_forbidden(api, unprovisioned, connection, fake_client):
    r = api(unprovisioned).get("/api/media/contents/12/video-url")
    assert (r.status_code, r.data["code"]) == (403, "media_not_provisioned")
    fake_client.video_location.assert_not_called()


def test_upstream_content_not_found(api, shared_viewer, connection, fake_client, upstream_error):
    fake_client.video_location.side_effect = upstream_error("content_not_found", 404)
    r = api(shared_viewer).get("/api/media/contents/12/video-url")
    assert (r.status_code, r.data["code"]) == (404, "content_not_found")


def test_authorize_happens_before_upstream_call(api, shared_viewer, connection, fake_client, monkeypatch):
    """Viewer không có Action.READ (giả lập role rỗng) → 403 và KHÔNG gọi upstream."""

    def deny(cred, action, *, owner_check=None):
        from apps.media.exceptions import MediaForbidden

        raise MediaForbidden()

    monkeypatch.setattr("apps.media.transport.file_views.authorize", deny)
    r = api(shared_viewer).get("/api/media/contents/12/video-url")
    assert (r.status_code, r.data["code"]) == (403, "media_forbidden")
    fake_client.video_location.assert_not_called()
