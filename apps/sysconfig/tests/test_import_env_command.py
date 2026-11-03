import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.sysconfig import registry
from apps.sysconfig.models import SystemSetting
from apps.sysconfig.registry import Setting
from apps.sysconfig.service import sysconfig

FAKE = (
    Setting("T_BOOL", "bool", False, "ai", "b"),
    Setting("T_INT", "int", 80, "ai", "i"),
    Setting("T_STR", "str", "", "ai", "t"),
    Setting("T_SECRET", "str", "", "ai", "s", secret=True),
)
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _fake(monkeypatch, settings):
    monkeypatch.setattr(registry, "SETTINGS", FAKE)
    monkeypatch.setattr(registry, "BY_KEY", {s.key: s for s in FAKE})
    settings.SYSCONFIG_TEST_MODE = False
    sysconfig.reset()


def test_dry_run_prints_diff_and_writes_nothing(monkeypatch, capsys):
    monkeypatch.setenv("T_INT", "90")
    monkeypatch.setenv("T_SECRET", "abcdef")
    call_command("sysconfig_import_env")
    out = capsys.readouterr().out
    assert "T_INT" in out and "90" in out
    assert "abcdef" not in out and "***" in out
    assert not SystemSetting.objects.exists()


def test_apply_creates_rows(monkeypatch):
    monkeypatch.setenv("T_INT", "90")
    monkeypatch.setenv("T_SECRET", "abcdef")
    call_command("sysconfig_import_env", "--apply")
    assert SystemSetting.objects.get(key="T_INT").value == "90"
    assert SystemSetting.objects.get(key="T_SECRET").secret_value == "abcdef"


def test_apply_skips_empty_env_and_existing_diff_without_force(monkeypatch):
    SystemSetting.objects.create(key="T_INT", value="1")
    monkeypatch.setenv("T_INT", "90")
    monkeypatch.delenv("T_SECRET", raising=False)
    call_command("sysconfig_import_env", "--apply")
    assert SystemSetting.objects.get(key="T_INT").value == "1"
    assert not SystemSetting.objects.filter(key="T_SECRET").exists()
    call_command("sysconfig_import_env", "--apply", "--force")
    assert SystemSetting.objects.get(key="T_INT").value == "90"


def test_non_secret_values_printed_in_full(monkeypatch, capsys):
    """Trước đây cột ENV/DB cắt 8 ký tự — URL/model dài không đối chiếu được."""
    monkeypatch.setenv("T_STR", "https://example.com/a/very/long/path/that/exceeds/eight")
    call_command("sysconfig_import_env")
    assert "https://example.com/a/very/long/path/that/exceeds/eight" in capsys.readouterr().out


def test_invalid_env_value_dry_run_shows_error_and_writes_nothing(monkeypatch, capsys):
    monkeypatch.setenv("T_BOOL", "maybe")
    monkeypatch.setenv("T_INT", "90")
    call_command("sysconfig_import_env")
    out = capsys.readouterr().out
    assert "LỖI" in out and "T_BOOL" in out
    assert "CẢNH BÁO" in out
    assert not SystemSetting.objects.exists()


def test_invalid_env_value_apply_writes_valid_keys_and_exits_nonzero(monkeypatch, capsys):
    """Ruling review 2026-09-21 F5: ghi khóa hợp lệ, in WARNING, thoát mã 1 để người deploy thấy."""
    monkeypatch.setenv("T_BOOL", "maybe")
    monkeypatch.setenv("T_INT", "90")
    with pytest.raises(CommandError, match="bị bỏ qua"):
        call_command("sysconfig_import_env", "--apply")
    out = capsys.readouterr().out
    assert "LỖI" in out and "CẢNH BÁO" in out
    assert SystemSetting.objects.get(key="T_INT").value == "90"
    assert not SystemSetting.objects.filter(key="T_BOOL").exists()


def test_bool_env_stored_canonical_and_compared_by_value(monkeypatch, capsys):
    monkeypatch.setenv("T_BOOL", "true")
    call_command("sysconfig_import_env", "--apply")
    capsys.readouterr()  # bỏ output lượt đầu
    assert SystemSetting.objects.get(key="T_BOOL").value == "1"
    # DB "1" vs env "yes" → cùng giá trị, không được báo KHÁC
    monkeypatch.setenv("T_BOOL", "yes")
    call_command("sysconfig_import_env")
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if ln.startswith("T_BOOL"))
    assert "giống nhau" in line
