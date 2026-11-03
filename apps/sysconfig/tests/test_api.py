import pytest
from rest_framework.test import APIClient

from apps.sysconfig.models import SystemSetting, SystemSettingChange
from apps.sysconfig.service import sysconfig
from apps.users.models import User
from apps.users.tests.helpers import make_admin

pytestmark = pytest.mark.django_db
URL = "/api/system-settings/"


@pytest.fixture(autouse=True)
def _db_mode(settings):
    settings.SYSCONFIG_TEST_MODE = False
    sysconfig.reset()


@pytest.fixture
def admin_client():
    # `superuser_only` (mirror của apps.pancake.transport.views) gate qua
    # apps.users.rbac.resolve_effective_permissions — dựa vào Role.is_system
    # ("Administrator"), KHÔNG phải cờ Django User.is_superuser. Dùng
    # `make_admin` (cùng helper apps.pancake.tests dùng cho fixture `su`) thay
    # vì `User.objects.create_superuser` — với decorator này, create_superuser
    # đơn thuần vẫn nhận 403.
    user = make_admin("root")
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def staff_client():
    user = User.objects.create_user("staff", "s@example.com", "pw")
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_non_superuser_forbidden(staff_client):
    assert staff_client.get(URL).status_code == 403
    assert staff_client.patch(URL, {"values": {}}, format="json").status_code == 403


def test_get_groups_and_masks_secret(admin_client):
    SystemSetting.objects.create(key="GEMINI_API_KEY", secret_value="abcdWXYZ")
    SystemSetting.objects.create(key="QA_PASS_SCORE", value="75")
    body = admin_client.get(URL).json()
    groups = {g["key"]: g for g in body["groups"]}
    assert list(groups) == ["lark_bots", "lark_open", "ai", "call_qa", "zenify", "integrations", "inbound_tokens", "orders_portal"]
    by_key = {s["key"]: s for g in body["groups"] for s in g["settings"]}
    gem = by_key["GEMINI_API_KEY"]
    assert gem["is_secret"] and gem["is_set"] and gem["value"] is None and gem["hint"] == "…WXYZ"
    assert "abcdWXYZ" not in str(body)
    assert by_key["QA_PASS_SCORE"]["value"] == "75"
    assert by_key["QA_MIN_DURATION_SECONDS"]["value"] is None
    assert by_key["QA_MIN_DURATION_SECONDS"]["default"] == 60


def test_patch_writes_many_and_returns_get_payload(admin_client):
    r = admin_client.patch(URL, {"values": {"QA_PASS_SCORE": "70", "GEMINI_API_KEY": "new-key-1234"}}, format="json")
    assert r.status_code == 200
    assert SystemSetting.objects.get(key="QA_PASS_SCORE").value == "70"
    assert SystemSetting.objects.get(key="GEMINI_API_KEY").secret_value == "new-key-1234"
    by_key = {s["key"]: s for g in r.json()["groups"] for s in g["settings"]}
    assert by_key["GEMINI_API_KEY"]["hint"] == "…1234"
    assert by_key["QA_PASS_SCORE"]["updated_by"]["name"]


def test_patch_null_deletes(admin_client):
    SystemSetting.objects.create(key="GEMINI_API_KEY", secret_value="x")
    assert admin_client.patch(URL, {"values": {"GEMINI_API_KEY": None}}, format="json").status_code == 200
    assert not SystemSetting.objects.filter(key="GEMINI_API_KEY").exists()


def test_patch_invalid_returns_400_and_writes_nothing(admin_client):
    r = admin_client.patch(URL, {"values": {"QA_PASS_SCORE": "abc", "NOPE": "1", "GEMINI_MAX_RPM": "9"}}, format="json")
    assert r.status_code == 400
    assert set(r.json()["errors"]) == {"QA_PASS_SCORE", "NOPE"}
    assert not SystemSetting.objects.exists()


def test_patch_non_object_body_returns_400(admin_client):
    r = admin_client.patch(URL, [{"QA_PASS_SCORE": "70"}], format="json")
    assert r.status_code == 400
    assert r.json() == {"errors": {"body": "Phải là object {values: {...}}"}}
    assert not SystemSetting.objects.exists()


def test_history(admin_client):
    admin_client.patch(URL, {"values": {"QA_PASS_SCORE": "70"}}, format="json")
    admin_client.patch(URL, {"values": {"QA_PASS_SCORE": "71"}}, format="json")
    r = admin_client.get(URL + "history/", {"key": "QA_PASS_SCORE"})
    assert r.status_code == 200
    rows = r.json()
    assert [x["new_value"] for x in rows] == ["71", "70"]
    assert rows[0]["changed_by"]["name"]
    assert SystemSettingChange.objects.count() == 2
