"""uid cache + trạng thái xác thực, và cổng bật/tắt của task đồng bộ."""

from unittest.mock import MagicMock

import pytest

from apps.foxia.exceptions import FoxiaAuthError
from apps.foxia.infrastructure.uid_provider import ensure_uid
from apps.foxia.models import FoxiaAuthStatus, FoxiaConnection

pytestmark = pytest.mark.django_db


def _connection(**overrides):
    values = {
        "endpoint_url": "https://crm.example.vn/jsonrpc",
        "database": "foxia_prod",
        "username": "integration.marketing",
        "api_key": "secret-key",
    }
    values.update(overrides)
    return FoxiaConnection.objects.create(**values)


def test_first_call_authenticates_and_caches_uid():
    connection = _connection()
    client = MagicMock()
    client.authenticate.return_value = 42

    assert ensure_uid(connection, client=client) == 42

    connection.refresh_from_db()
    assert connection.uid == 42
    assert connection.auth_status == FoxiaAuthStatus.OK
    assert connection.uid_checked_at is not None


def test_cached_uid_skips_authenticate():
    connection = _connection(uid=42, auth_status=FoxiaAuthStatus.OK)
    client = MagicMock()

    assert ensure_uid(connection, client=client) == 42
    client.authenticate.assert_not_called()


def test_force_refresh_reauthenticates():
    connection = _connection(uid=42, auth_status=FoxiaAuthStatus.OK)
    client = MagicMock()
    client.authenticate.return_value = 77

    assert ensure_uid(connection, client=client, force_refresh=True) == 77
    connection.refresh_from_db()
    assert connection.uid == 77


def test_revoked_key_marks_connection_and_does_not_retry():
    """Key bị thu hồi là việc của con người, không phải thứ để retry vòng lặp."""
    connection = _connection()
    client = MagicMock()
    client.authenticate.side_effect = FoxiaAuthError("Foxia từ chối xác thực")

    with pytest.raises(FoxiaAuthError):
        ensure_uid(connection, client=client)

    connection.refresh_from_db()
    assert connection.auth_status == FoxiaAuthStatus.REVOKED
    assert "từ chối" in connection.auth_error
    assert client.authenticate.call_count == 1


def test_network_error_marks_error_status_not_revoked():
    connection = _connection()
    client = MagicMock()
    client.authenticate.side_effect = RuntimeError("timeout")

    with pytest.raises(RuntimeError):
        ensure_uid(connection, client=client)

    connection.refresh_from_db()
    # Phân biệt "mạng hỏng" với "key bị thu hồi": chỉ cái sau mới cần người xử lý.
    assert connection.auth_status == FoxiaAuthStatus.ERROR


def test_cached_uid_is_ignored_when_status_is_not_ok():
    connection = _connection(uid=42, auth_status=FoxiaAuthStatus.REVOKED)
    client = MagicMock()
    client.authenticate.return_value = 99

    assert ensure_uid(connection, client=client) == 99


# ── task gating ──────────────────────────────────────────────────────────────


def test_build_client_returns_none_when_disabled(settings):
    from apps.foxia.tasks import build_client

    settings.FOXIA_SYNC_ENABLED = False
    _connection()

    assert build_client() is None


def test_build_client_ignores_flag_for_manual_runs(settings):
    """`foxia_probe` phải chạy được TRƯỚC khi bật cron."""
    from apps.foxia.tasks import build_client

    from apps.foxia.infrastructure.foxia_client import FoxiaClient

    settings.FOXIA_SYNC_ENABLED = False
    connection = _connection()

    client = build_client(require_enabled=False)
    assert isinstance(client, FoxiaClient)
    assert client.connection == connection


def test_build_client_returns_none_without_active_connection(settings):
    from apps.foxia.tasks import build_client

    settings.FOXIA_SYNC_ENABLED = True
    _connection(is_active=False)

    assert build_client() is None


def test_sync_catalog_task_is_noop_when_disabled(settings):
    from apps.foxia.tasks import sync_catalog

    settings.FOXIA_SYNC_ENABLED = False
    _connection()

    assert sync_catalog() == "disabled"
