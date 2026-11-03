import pytest
from django.core.cache import cache
from django.test import override_settings

from apps.common import runtime_config
from apps.sysconfig import registry, service
from apps.sysconfig.models import SystemSetting
from apps.sysconfig.registry import Setting
from apps.sysconfig.service import override_sysconfig, sysconfig

FAKE = (
    Setting("T_BOOL", "bool", False, "ai", "b"),
    Setting("T_INT", "int", 80, "ai", "i"),
    Setting("T_SECRET", "str", "", "ai", "s", secret=True, staging_forbidden=True),
    Setting("T_STR", "str", "dflt", "ai", "t"),
)


@pytest.fixture(autouse=True)
def _fake_registry(monkeypatch):
    monkeypatch.setattr(registry, "SETTINGS", FAKE)
    monkeypatch.setattr(registry, "BY_KEY", {s.key: s for s in FAKE})
    sysconfig.reset()
    cache.delete(service.VERSION_KEY)
    yield
    sysconfig.reset()


@pytest.fixture
def sysconfig_db(settings):
    settings.SYSCONFIG_TEST_MODE = False
    sysconfig.reset()


# ── test mode (mặc định cho toàn suite) ────────────────────────────────────────

def test_test_mode_returns_default_without_db():
    assert sysconfig.get("T_INT") == 80
    assert sysconfig.get("T_BOOL") is False


def test_test_mode_honours_override_settings():
    with override_settings(T_INT=5, T_BOOL="1"):
        assert sysconfig.get("T_INT") == 5
        assert sysconfig.get("T_BOOL") is True


def test_override_sysconfig_context_and_decorator():
    with override_sysconfig(T_STR="x"):
        assert sysconfig.get("T_STR") == "x"
    assert sysconfig.get("T_STR") == "dflt"

    @override_sysconfig(T_INT=1)
    def inner():
        return sysconfig.get("T_INT")

    assert inner() == 1


def test_unknown_key_raises():
    with pytest.raises(KeyError):
        sysconfig.get("NOPE")


# ── DB thật ────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_db_value_wins_over_default(sysconfig_db):
    SystemSetting.objects.create(key="T_INT", value="90")
    assert sysconfig.get("T_INT") == 90


@pytest.mark.django_db
def test_secret_read_from_secret_column(sysconfig_db):
    SystemSetting.objects.create(key="T_SECRET", secret_value="shh")
    assert sysconfig.get("T_SECRET") == "shh"


@pytest.mark.django_db
def test_bad_db_value_falls_back_to_default(sysconfig_db, caplog):
    SystemSetting.objects.create(key="T_INT", value="abc")
    assert sysconfig.get("T_INT") == 80
    assert "T_INT" in caplog.text


@pytest.mark.django_db
def test_snapshot_reloads_when_version_bumps(sysconfig_db, monkeypatch):
    SystemSetting.objects.create(key="T_INT", value="1")
    assert sysconfig.get("T_INT") == 1
    SystemSetting.objects.filter(key="T_INT").update(value="2")
    # Trong cửa sổ 2s không so version → vẫn 1
    assert sysconfig.get("T_INT") == 1
    # Hết cửa sổ nhưng version chưa đổi → vẫn 1
    monkeypatch.setattr(service, "_monotonic", lambda: 10_000.0)
    assert sysconfig.get("T_INT") == 1
    # Version đổi → nạp lại
    service.bump_version()
    monkeypatch.setattr(service, "_monotonic", lambda: 20_000.0)
    assert sysconfig.get("T_INT") == 2


@pytest.mark.django_db
def test_redis_failure_keeps_snapshot(sysconfig_db, monkeypatch):
    """Backend cache prod (InstrumentedRedisCache) nuốt lỗi Redis và trả None như key
    chưa có → _read_version ra 0. Store phải giữ snapshot, KHÔNG nạp lại DB mỗi 2s."""
    SystemSetting.objects.create(key="T_INT", value="7")
    service.bump_version()
    assert sysconfig.get("T_INT") == 7
    # DB đổi nhưng không ai bump version (mô phỏng process khác ghi lúc Redis chết)
    SystemSetting.objects.filter(key="T_INT").update(value="8")

    monkeypatch.setattr(service.cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(service, "_monotonic", lambda: 10_000.0)
    assert sysconfig.get("T_INT") == 7
    monkeypatch.setattr(service, "_monotonic", lambda: 20_000.0)
    assert sysconfig.get("T_INT") == 7


@pytest.mark.django_db
def test_redis_exception_keeps_snapshot(sysconfig_db, monkeypatch):
    """Biến thể backend raise (cache khác / lỗi lạ) — cũng giữ snapshot."""
    SystemSetting.objects.create(key="T_INT", value="7")
    assert sysconfig.get("T_INT") == 7
    SystemSetting.objects.filter(key="T_INT").update(value="8")

    def boom(*a, **k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(service.cache, "get", boom)
    monkeypatch.setattr(service, "_monotonic", lambda: 10_000.0)
    assert sysconfig.get("T_INT") == 7


@pytest.mark.django_db
def test_staging_forbidden_returns_default(sysconfig_db, settings):
    SystemSetting.objects.create(key="T_SECRET", secret_value="prod-key")
    settings.IS_STAGING = True
    sysconfig.reset()
    assert sysconfig.get("T_SECRET") == ""


def test_runtime_config_hook_delegates():
    with override_sysconfig(T_STR="via-hook"):
        assert runtime_config.get("T_STR") == "via-hook"
