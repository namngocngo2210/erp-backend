import pytest

from apps.media.models import MediaConnection
from apps.users.tests.helpers import make_admin, make_user_with_permissions

pytestmark = pytest.mark.django_db


def test_non_admin_forbidden(api):
    staff = make_user_with_permissions("s", ["orders:view"])
    assert api(staff).get("/api/media/settings").status_code == 403
    assert api(staff).patch("/api/media/settings", {"base_url": "x"}, format="json").status_code == 403


def test_get_empty_when_not_configured(api):
    r = api(make_admin()).get("/api/media/settings")
    assert r.status_code == 200
    assert r.data["base_url"] == "" and r.data["shared_api_key_set"] is False and r.data["is_active"] is False


def test_patch_creates_then_updates_without_echoing_key(api):
    admin = make_admin()
    r = api(admin).patch("/api/media/settings", {"base_url": "https://media.example/", "shared_api_key": "dak_sharedkey_abc"}, format="json")
    assert r.status_code == 200, r.data
    assert r.data["base_url"] == "https://media.example"
    assert r.data["shared_api_key_set"] is True and r.data["shared_api_key_prefix"] == "dak_sharedke"
    assert "shared_api_key" not in r.data
    conn = MediaConnection.objects.get()
    assert conn.shared_api_key == "dak_sharedkey_abc"

    # "" giữ nguyên key; null xoá key; is_active đổi được.
    api(admin).patch("/api/media/settings", {"shared_api_key": "", "is_active": False}, format="json")
    conn.refresh_from_db()
    assert conn.shared_api_key == "dak_sharedkey_abc" and conn.is_active is False
    api(admin).patch("/api/media/settings", {"shared_api_key": None}, format="json")
    conn.refresh_from_db()
    assert conn.shared_api_key == ""


def test_patch_rejects_bad_url_and_bad_key(api):
    admin = make_admin()
    assert api(admin).patch("/api/media/settings", {"base_url": "media.example"}, format="json").status_code == 400
    assert api(admin).patch("/api/media/settings", {"base_url": "https://ok.example", "shared_api_key": "nope"}, format="json").status_code == 400


def test_test_connection_records_result(api, connection, fake_client):
    admin = make_admin()
    fake_client.me.return_value = {"id": 1, "username": "svc", "role": "uploader"}
    r = api(admin).post("/api/media/settings/test")
    assert r.status_code == 200
    assert r.data == {"ok": True, "role": "uploader", "username": "svc",
                      "warning": "Key chung không phải role admin: người dùng key chung sẽ bị 403 khi sửa nội dung không do key tạo."}
    connection.refresh_from_db()
    assert connection.last_check_ok is True and connection.shared_key_role == "uploader"


def test_test_connection_failure_is_200_ok_false(api, connection, fake_client, upstream_error):
    fake_client.me.side_effect = upstream_error("invalid_token", 401)
    r = api(make_admin()).post("/api/media/settings/test")
    assert r.status_code == 200 and r.data["ok"] is False
    assert "từ chối" in r.data["error"]
    connection.refresh_from_db()
    assert connection.last_check_ok is False and connection.last_check_error
