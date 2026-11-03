import pytest

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_cache")]


def test_me_unprovisioned_returns_200_provisioned_false(api, unprovisioned, connection):
    r = api(unprovisioned).get("/api/media/me")
    assert r.status_code == 200
    assert r.data["provisioned"] is False
    assert r.data["public_base_url"] == "https://media.example"


def test_me_shared_reports_role_without_calling_upstream(api, shared_uploader, connection, fake_client):
    r = api(shared_uploader).get("/api/media/me")
    assert r.status_code == 200
    assert r.data == {
        "provisioned": True, "mode": "shared", "role": "uploader", "remote_user": None,
        "public_base_url": "https://media.example", "connection_ok": True,
    }
    fake_client.me.assert_not_called()


def test_me_personal_reads_role_from_upstream_and_caches(api, personal_user, connection, fake_client):
    fake_client.me.return_value = {"id": 9, "username": "hung", "name": "Hùng", "role": "marketing"}
    r1 = api(personal_user).get("/api/media/me")
    r2 = api(personal_user).get("/api/media/me")
    assert r1.data["mode"] == "personal" and r1.data["role"] == "marketing"
    assert r1.data["remote_user"] == {"id": 9, "username": "hung", "name": "Hùng"}
    assert r2.status_code == 200
    assert fake_client.me.call_count == 1


def test_me_personal_key_rejected_gives_connection_false_not_401(api, personal_user, connection, fake_client, upstream_error):
    fake_client.me.side_effect = upstream_error("invalid_token", 401)
    r = api(personal_user).get("/api/media/me")
    assert r.status_code == 200
    assert r.data["connection_ok"] is False and r.data["role"] is None


def test_contents_list_passes_only_allowlisted_repeated_params(api, shared_viewer, connection, fake_client):
    r = api(shared_viewer).get("/api/media/contents?product_id=1&product_id=2&page=2&evil=1&sort=title")
    assert r.status_code == 200
    args, kwargs = fake_client.json_request.call_args
    assert args == ("GET", "/contents")
    assert sorted(kwargs["params"]) == [("page", "2"), ("product_id", "1"), ("product_id", "2"), ("sort", "title")]


def test_unprovisioned_gets_403_media_not_provisioned(api, unprovisioned, connection):
    r = api(unprovisioned).get("/api/media/contents")
    assert r.status_code == 403
    assert r.data == {"detail": "Bạn chưa được cấp quyền Media. Nhờ admin gán vai trò Media hoặc nhập API key riêng.", "code": "media_not_provisioned"}


def test_no_connection_gives_503(api, shared_viewer):
    r = api(shared_viewer).get("/api/media/contents")
    assert (r.status_code, r.data["code"]) == (503, "media_disabled")


def test_upstream_error_status_and_code_passthrough(api, shared_viewer, connection, fake_client, upstream_error):
    fake_client.json_request.side_effect = upstream_error("content_not_found", 404)
    r = api(shared_viewer).get("/api/media/contents/99")
    assert r.status_code == 404
    assert r.data == {"detail": "Nội dung không tồn tại hoặc đã xoá.", "code": "content_not_found"}


def test_taxonomy_cached_per_group_and_flag(api, shared_viewer, shared_admin, connection, fake_client):
    fake_client.json_request.return_value = (200, {"items": [{"id": 1, "name": "TikTok"}]})
    api(shared_viewer).get("/api/media/content-sources")
    api(shared_viewer).get("/api/media/content-sources")
    assert fake_client.json_request.call_count == 1
    # include_inactive: chỉ admin mới được truyền; viewer bị lược bỏ → dùng chung cache.
    api(shared_viewer).get("/api/media/content-sources?include_inactive=true")
    assert fake_client.json_request.call_count == 1
    api(shared_admin).get("/api/media/content-sources?include_inactive=true")
    assert fake_client.json_request.call_count == 2
    assert fake_client.json_request.call_args.kwargs["params"] == [("include_inactive", "true")]
    # include_inactive=True không bao giờ cache: mỗi request admin gọi lại upstream.
    api(shared_admin).get("/api/media/content-sources?include_inactive=true")
    assert fake_client.json_request.call_count == 3


def test_personal_include_inactive_always_forwards(api, personal_user, connection, fake_client):
    """Key riêng: ads-crm phải tự phân quyền mỗi request — không được đọc từ cache
    include_inactive do người khác làm nóng (spec §5.1)."""
    fake_client.json_request.return_value = (200, {"items": []})
    api(personal_user).get("/api/media/content-sources?include_inactive=true")
    api(personal_user).get("/api/media/content-sources?include_inactive=true")
    assert fake_client.json_request.call_count == 2
    for call in fake_client.json_request.call_args_list:
        assert call.kwargs["params"] == [("include_inactive", "true")]

    api(personal_user).get("/api/media/content-sources")
    api(personal_user).get("/api/media/content-sources")
    assert fake_client.json_request.call_count == 3


def test_unknown_taxonomy_group_is_404(api, shared_viewer, connection, fake_client):
    assert api(shared_viewer).get("/api/media/users").status_code == 404
    assert api(shared_viewer).get("/api/media/api-keys").status_code == 404


def test_reports_and_proposals_routes(api, shared_viewer, connection, fake_client):
    for path in ("/api/media/reports/overview?uploaded_from=2026-09-01", "/api/media/reports/ratings-by-channel",
                 "/api/media/reports/content-quality", "/api/media/proposals?status=pending&q=x", "/api/media/proposals/3",
                 "/api/media/contents/3/ad-evaluations"):
        assert api(shared_viewer).get(path).status_code == 200, path


def test_staging_blocks_with_503(api, shared_viewer, connection, settings):
    settings.IS_STAGING = True
    r = api(shared_viewer).get("/api/media/contents")
    assert (r.status_code, r.data["code"]) == (503, "media_staging_blocked")
