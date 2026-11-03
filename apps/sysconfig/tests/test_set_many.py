import pytest
from django.core.cache import cache

from apps.sysconfig import registry, service
from apps.sysconfig.models import SystemSetting, SystemSettingChange
from apps.sysconfig.registry import Setting
from apps.sysconfig.service import SysconfigValidationError, set_many, sysconfig
from apps.users.models import User

FAKE = (
    Setting("T_BOOL", "bool", False, "ai", "b"),
    Setting("T_INT", "int", 80, "ai", "i"),
    Setting("T_SECRET", "str", "", "ai", "s", secret=True),
    Setting("T_URL", "url", "", "ai", "u"),
    Setting("T_AT", "datetime", "", "ai", "d"),
)
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _fake_registry(monkeypatch, settings):
    monkeypatch.setattr(registry, "SETTINGS", FAKE)
    monkeypatch.setattr(registry, "BY_KEY", {s.key: s for s in FAKE})
    settings.SYSCONFIG_TEST_MODE = False
    sysconfig.reset()
    cache.delete(service.VERSION_KEY)


@pytest.fixture
def admin():
    return User.objects.create_superuser("root", "root@example.com", "pw")


def test_set_plain_and_secret_use_right_columns(admin):
    set_many({"T_INT": "90", "T_SECRET": "abc123"}, user=admin)
    plain = SystemSetting.objects.get(key="T_INT")
    secret = SystemSetting.objects.get(key="T_SECRET")
    assert (plain.value, plain.secret_value) == ("90", "")
    assert (secret.value, secret.secret_value) == ("", "abc123")
    assert plain.updated_by == admin


def test_history_masks_secret(admin):
    set_many({"T_INT": "90", "T_SECRET": "abc123"}, user=admin)
    set_many({"T_INT": "95", "T_SECRET": "xyz"}, user=admin)
    plain = SystemSettingChange.objects.filter(key="T_INT").first()
    secret = SystemSettingChange.objects.filter(key="T_SECRET").first()
    assert (plain.old_value, plain.new_value) == ("90", "95")
    assert (secret.old_value, secret.new_value) == ("***", "***")
    first_secret = SystemSettingChange.objects.filter(key="T_SECRET").last()
    assert (first_secret.old_value, first_secret.new_value) == ("", "***")


def test_none_deletes_row_and_logs(admin):
    set_many({"T_SECRET": "abc"}, user=admin)
    set_many({"T_SECRET": None}, user=admin)
    assert not SystemSetting.objects.filter(key="T_SECRET").exists()
    assert SystemSettingChange.objects.filter(key="T_SECRET").first().new_value == ""


def test_validation_error_writes_nothing(admin):
    with pytest.raises(SysconfigValidationError) as exc:
        set_many({"T_INT": "90", "T_URL": "not-a-url", "NOPE": "1"}, user=admin)
    assert set(exc.value.errors) == {"T_URL", "NOPE"}
    assert not SystemSetting.objects.exists()


@pytest.mark.django_db(transaction=True)  # on_commit chỉ chạy khi có commit thật
def test_bumps_version_after_commit(admin):
    assert cache.get(service.VERSION_KEY) is None
    set_many({"T_INT": "1"}, user=admin)
    assert int(cache.get(service.VERSION_KEY)) >= 1


def test_unchanged_value_no_history(admin):
    set_many({"T_INT": "1"}, user=admin)
    set_many({"T_INT": "1"}, user=admin)
    assert SystemSettingChange.objects.filter(key="T_INT").count() == 1


@pytest.mark.parametrize(
    "raw, stored",
    [("yes", "1"), ("true", "1"), ("on", "1"), (" 1 ", "1"), ("no", "0"), ("off", "0")],
)
def test_bool_stored_canonical_1_or_0(admin, raw, stored):
    """Frontend render checked={value === "1"} — chuỗi bool phải chuẩn hoá khi lưu."""
    set_many({"T_BOOL": raw}, user=admin)
    assert SystemSetting.objects.get(key="T_BOOL").value == stored
    assert SystemSettingChange.objects.get(key="T_BOOL").new_value == stored


def test_first_enabled_at_reads_history(admin):
    assert service.first_enabled_at("T_BOOL") is None
    set_many({"T_BOOL": "true"}, user=admin)
    first = SystemSettingChange.objects.get(key="T_BOOL", new_value="1").changed_at
    set_many({"T_BOOL": "false"}, user=admin)
    set_many({"T_BOOL": "true"}, user=admin)
    assert service.first_enabled_at("T_BOOL") == first
    with pytest.raises(ValueError):
        service.first_enabled_at("T_INT")


def test_datetime_setting_validated_and_parsed(admin):
    with pytest.raises(SysconfigValidationError):
        set_many({"T_AT": "hôm qua"}, user=admin)
    assert sysconfig.get_datetime("T_AT") is None
    set_many({"T_AT": "2026-10-01 08:00"}, user=admin)
    sysconfig.reset()
    assert sysconfig.get_datetime("T_AT").isoformat() == "2026-10-01T08:00:00+07:00"
