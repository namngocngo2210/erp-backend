"""Tests cho các guard cấu hình trong config.settings_parts.base.

Gộp từ: test_settings_guard.py, test_settings_guard_staging.py,
test_settings_pgbouncer.py, test_db_statement_timeout.py (restructure 2026-08-05).
"""

import importlib
import os
import unittest
from unittest import mock

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import TestCase

from config.settings_parts import base
from config.settings_parts.base import (
    STAGING_FORBIDDEN_OUTBOUND_ENVS,
    assert_production_debug_off,
    assert_production_secrets,
    assert_staging_outbound_disabled,
    find_insecure_production_secrets,
    find_staging_forbidden_env,
)


def _getenv(env):
    return lambda key, default="": env.get(key, default)


# ---------------------------------------------------------------------------
# Prod secret guard — audit 2026-07 mục 1.3.
#
# Guard chạy lúc nạp settings khi APP_ENV=production; ở đây test trực tiếp
# các hàm thuần (inject getenv giả) thay vì re-import settings module.
# ---------------------------------------------------------------------------

_SECURE_ENV = {
    "DJANGO_SECRET_KEY": "a-long-real-secret-key",
    "POSTGRES_PASSWORD": "real-pg-password",
    "REDIS_PASSWORD": "real-redis-password",
    "MINIO_ACCESS_KEY": "real-minio-access",
    "MINIO_SECRET_KEY": "real-minio-secret",
    "INTERNAL_AUTH_TOKEN": "real-internal-token",
    "ENCRYPTION_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
}


def test_secure_env_passes():
    assert find_insecure_production_secrets(_getenv(_SECURE_ENV)) == []
    assert_production_secrets(_getenv(_SECURE_ENV))  # không raise


@pytest.mark.parametrize(
    ("key", "bad_value"),
    [
        ("DJANGO_SECRET_KEY", ""),
        ("DJANGO_SECRET_KEY", "dev-secret-key-change-me"),
        ("POSTGRES_PASSWORD", "desi_order"),
        ("REDIS_PASSWORD", ""),
        ("MINIO_ACCESS_KEY", "minioadmin"),
        ("MINIO_SECRET_KEY", "minioadmin"),
        ("INTERNAL_AUTH_TOKEN", ""),
        ("ENCRYPTION_KEY", ""),
        ("ENCRYPTION_KEY", "your-32-byte-encryption-key-here"),
    ],
)
def test_default_or_empty_secret_flagged(key, bad_value):
    env = {**_SECURE_ENV, key: bad_value}
    assert find_insecure_production_secrets(_getenv(env)) == [key]
    with pytest.raises(ImproperlyConfigured, match=key):
        assert_production_secrets(_getenv(env))


def test_missing_keys_all_flagged():
    flagged = find_insecure_production_secrets(_getenv({}))
    assert set(flagged) == set(_SECURE_ENV)


@pytest.mark.parametrize("debug_value", ["1", "true", "True", "yes", "YES", "on", "ON"])
def test_debug_on_raises(debug_value):
    with pytest.raises(ImproperlyConfigured, match="DJANGO_DEBUG"):
        assert_production_debug_off(_getenv({"DJANGO_DEBUG": debug_value}))


@pytest.mark.parametrize("debug_value", ["0", "false", "", "no"])
def test_debug_off_passes(debug_value):
    assert_production_debug_off(_getenv({"DJANGO_DEBUG": debug_value}))  # không raise


def test_debug_missing_defaults_to_off():
    assert_production_debug_off(_getenv({}))  # không raise


# ---------------------------------------------------------------------------
# Staging outbound guard — docs/staging-environment.md §3.
#
# Guard chạy lúc nạp settings khi APP_ENV=staging; test trực tiếp các hàm thuần
# (inject getenv giả) — cùng pattern với phần prod secret guard ở trên.
# ---------------------------------------------------------------------------


def _find(env):
    # environ=env: quét prefix LARK_ đọc từ map env giả, không đụng os.environ thật.
    return find_staging_forbidden_env(_getenv(env), environ=env)


def _assert_disabled(env):
    return assert_staging_outbound_disabled(_getenv(env), environ=env)


def test_clean_env_passes():
    assert _find({}) == []
    _assert_disabled({})  # không raise


def test_mock_mode_passes():
    env = {"KIOTVIET_API_MODE": "mock"}
    assert _find(env) == []


def test_live_mode_flagged():
    env = {"KIOTVIET_API_MODE": "live"}
    assert _find(env) == ["KIOTVIET_API_MODE"]
    with pytest.raises(ImproperlyConfigured, match="KIOTVIET_API_MODE"):
        _assert_disabled(env)


@pytest.mark.parametrize("key", STAGING_FORBIDDEN_OUTBOUND_ENVS)
def test_each_outbound_env_flagged(key):
    env = {key: "some-real-value"}
    assert _find(env) == [key]
    with pytest.raises(ImproperlyConfigured, match=key):
        _assert_disabled(env)


@pytest.mark.parametrize(
    "key",
    [
        "LARK_APP_ID",
        # 2 webhook từng bị danh sách tay bỏ sót (audit 2026-07-18) + bot tương lai.
        "LARK_INVENTORY_BOT_WEBHOOK_URL",
        "LARK_VAT_BOT_WEBHOOK_URL",
        "LARK_FUTURE_NEW_BOT_TOKEN",
    ],
)
def test_lark_prefix_flagged(key):
    env = {key: "real"}
    assert _find(env) == [key]
    with pytest.raises(ImproperlyConfigured, match=key):
        _assert_disabled(env)


def test_whitespace_only_value_ignored():
    env = {"LARK_APP_ID": "   "}
    assert _find(env) == []


def test_multiple_flagged_all_reported():
    env = {"KIOTVIET_API_MODE": "LIVE", "LARK_APP_ID": "x", "OPS_HEARTBEAT_URL": "y"}
    flagged = _find(env)
    assert flagged[0] == "KIOTVIET_API_MODE"
    assert set(flagged) == {"KIOTVIET_API_MODE", "LARK_APP_ID", "OPS_HEARTBEAT_URL"}


# ---------------------------------------------------------------------------
# Bẫy #3 của PgBouncer transaction pooling (settings_parts.base).
#
# QuerySet.iterator() dùng server-side cursor; ở autocommit mỗi lượt FETCH là một
# transaction riêng nên PgBouncer đảo backend connection giữa chừng → "cursor ...
# does not exist" (sự cố backfill_landing_confirmed_amount 2026-08-04).
# DISABLE_SERVER_SIDE_CURSORS phải bám cờ DB_PGBOUNCER.
# ---------------------------------------------------------------------------


def test_databases_carry_disable_server_side_cursors_flag():
    """settings đang chạy phải khai flag, giá trị bám DB_PGBOUNCER của env hiện tại."""
    assert "DISABLE_SERVER_SIDE_CURSORS" in settings.DATABASES["default"]
    assert settings.DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] == base.DB_PGBOUNCER


def test_flag_follows_pgbouncer_env():
    """DB_PGBOUNCER=1 phải bật đủ bộ ba: no-prepare, no-session-timeout, no-cursor."""
    try:
        with mock.patch.dict(os.environ, {"DB_PGBOUNCER": "1"}):
            reloaded = importlib.reload(base)
            db = reloaded.DATABASES["default"]
            assert reloaded.DB_PGBOUNCER is True
            assert db["DISABLE_SERVER_SIDE_CURSORS"] is True
            assert db["OPTIONS"]["prepare_threshold"] is None
            assert "options" not in db["OPTIONS"]  # không gửi statement_timeout mức session
    finally:
        # Trả module về đúng trạng thái theo env thật để không rò sang test khác.
        importlib.reload(base)


# ---------------------------------------------------------------------------
# Global statement_timeout mặc định (audit 2026-07-06 N2).
# ---------------------------------------------------------------------------

_TIMEOUT_MS = os.environ.get("DB_STATEMENT_TIMEOUT_MS", "30000")


class StatementTimeoutTests(TestCase):
    """Test cho global statement_timeout mặc định (audit 2026-07-06 N2).

    Chỉ report query mới có timeout riêng (apps.common.db_guards.statement_timeout,
    120s). Mọi query khác (view thường, admin, management command) trước đây không
    có trần thời gian nào -> query runaway có thể treo connection vô hạn. Test này
    xác nhận giá trị DB_STATEMENT_TIMEOUT_MS (default 30000) được áp vào session DB
    qua DATABASES["default"]["OPTIONS"]. Giá trị mong đợi lấy từ chính env thay vì
    hard-code "30s" — runbook (docs/audit-followups-2026-07-06.md) có escape hatch
    DB_STATEMENT_TIMEOUT_MS=0 (tắt trần) cho lần chạy migration backfill nặng; nếu
    suite chạy dưới env đó thì test này skip thay vì fail sai chỗ.
    """

    @unittest.skipIf(
        _TIMEOUT_MS == "0",
        "DB_STATEMENT_TIMEOUT_MS=0 tắt trần statement_timeout (escape hatch runbook)",
    )
    def test_default_statement_timeout_applied(self):
        expected = f"{int(_TIMEOUT_MS) // 1000}s"
        with connection.cursor() as cur:
            cur.execute("SHOW statement_timeout")
            self.assertEqual(cur.fetchone()[0], expected)


# ---------------------------------------------------------------------------
# lock_timeout mặc định cho web/worker (audit 2026-09-23 T2/P2-11).
# ---------------------------------------------------------------------------

_LOCK_TIMEOUT_MS = os.environ.get("DB_LOCK_TIMEOUT_MS", "5000")


class LockTimeoutTests(TestCase):
    """DB_LOCK_TIMEOUT_MS (mặc định 5000) đi cùng statement_timeout qua startup
    options; migrate/makemigrations được miễn (xem _IS_MIGRATION_CMD)."""

    @unittest.skipIf(_LOCK_TIMEOUT_MS == "0", "DB_LOCK_TIMEOUT_MS=0 tắt lock_timeout")
    def test_default_lock_timeout_applied(self):
        expected = f"{int(_LOCK_TIMEOUT_MS) // 1000}s"
        with connection.cursor() as cur:
            cur.execute("SHOW lock_timeout")
            self.assertEqual(cur.fetchone()[0], expected)

    def test_migration_command_skips_session_timeouts(self):
        try:
            with mock.patch.object(base.sys, "argv", ["manage.py", "migrate"]):
                reloaded = importlib.reload(base)
                assert "options" not in reloaded.DATABASES["default"]["OPTIONS"]
        finally:
            importlib.reload(base)


# ---------------------------------------------------------------------------
# Trần vòng đời JWT trên production (audit 2026-09-23 N2/H1).
#
# Prod từng đặt JWT_ACCESS_MINUTES=525600 (1 năm). Guard KHÔNG crash (đang giữa
# deploy) mà kẹp về mặc định an toàn + log ERROR; ngoài production giữ nguyên env.
# ---------------------------------------------------------------------------


def test_jwt_clamp_production_pulls_oversized_values_to_defaults(caplog):
    with caplog.at_level("ERROR", logger="config.settings_parts.base"):
        assert base.clamp_jwt_lifetimes(525600, 365, is_production=True) == (
            base.JWT_ACCESS_DEFAULT_MINUTES,
            base.JWT_REFRESH_DEFAULT_DAYS,
        )
    messages = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any("JWT_ACCESS_MINUTES=525600" in m for m in messages)
    assert any("JWT_REFRESH_DAYS=365" in m for m in messages)


@pytest.mark.parametrize(("access", "refresh"), [(60, 14), (1440, 30), (5, 1)])
def test_jwt_clamp_production_keeps_values_within_limit(access, refresh, caplog):
    with caplog.at_level("ERROR", logger="config.settings_parts.base"):
        assert base.clamp_jwt_lifetimes(access, refresh, is_production=True) == (access, refresh)
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_jwt_clamp_outside_production_is_passthrough():
    assert base.clamp_jwt_lifetimes(525600, 365, is_production=False) == (525600, 365)


def test_simple_jwt_lifetimes_follow_clamped_settings():
    from datetime import timedelta

    assert settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"] == timedelta(minutes=settings.JWT_ACCESS_MINUTES)
    assert settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"] == timedelta(days=settings.JWT_REFRESH_DAYS)


# ---------------------------------------------------------------------------
# Env nghiệp vụ bắt buộc ở production — audit 2026-09-23 T3/P2-7.
#
# Trước đó KIOTVIET_STATUS_SYNC_TARGETS / KOC_CHANNEL_ID / SALE_FB_CHANNEL_IDS có
# default là id kênh/kho PROD hard-code trong code và chỉ WARNING khi thiếu.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["KIOTVIET_STATUS_SYNC_TARGETS", "KOC_CHANNEL_ID", "SALE_FB_CHANNEL_IDS"])
@pytest.mark.parametrize("raw", [None, "", "   "])
def test_require_production_env_raises_when_missing_in_production(name, raw):
    env = {} if raw is None else {name: raw}
    with mock.patch.object(base, "IS_PRODUCTION", True), mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ImproperlyConfigured, match=name):
            base._require_production_env(name, why="test")


def test_require_production_env_passes_when_set_in_production():
    with (
        mock.patch.object(base, "IS_PRODUCTION", True),
        mock.patch.dict(os.environ, {"KOC_CHANNEL_ID": "15"}, clear=True),
    ):
        base._require_production_env("KOC_CHANNEL_ID", why="test")  # không raise


def test_require_production_env_is_noop_outside_production():
    with mock.patch.object(base, "IS_PRODUCTION", False), mock.patch.dict(os.environ, {}, clear=True):
        base._require_production_env("KOC_CHANNEL_ID", why="test")  # không raise


def test_no_production_channel_ids_hardcoded_as_defaults():
    """Nguồn base.py không được chứa lại id prod làm default (15 / 24 / branch 231174...)."""
    from pathlib import Path

    src = Path(base.__file__).read_text(encoding="utf-8")
    for needle in ('"KOC_CHANNEL_ID", "15"', '"SALE_FB_CHANNEL_IDS", "24"', '"channel_id": 4, "branch_ids"'):
        assert needle not in src, f"default id prod hard-code quay lại: {needle}"
