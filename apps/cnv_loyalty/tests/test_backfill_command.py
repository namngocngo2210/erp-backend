"""Backfill chạy được khi CNV_SYNC_ENABLED=0 (lệnh chạy tay, không phải luồng tự động)."""

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


def test_dry_run_works_with_sync_disabled(settings, cnv_connection):
    settings.CNV_SYNC_ENABLED = False
    out = StringIO()
    with patch(
        "apps.cnv_loyalty.tasks._build_service", return_value=MagicMock()
    ) as build:
        call_command("cnv_backfill", "--dry-run", "--entities", "orders", stdout=out)
    assert "cnv_backfill xong" in out.getvalue()
    assert build.call_args.kwargs["require_enabled"] is False


def test_async_dispatch_works_with_sync_disabled(settings, cnv_connection):
    settings.CNV_SYNC_ENABLED = False
    out = StringIO()
    with patch("apps.cnv_loyalty.tasks.run_backfill.delay") as delay:
        delay.return_value = MagicMock(id="task-1")
        call_command("cnv_backfill", stdout=out)
    assert "Đã dispatch" in out.getvalue()
    delay.assert_called_once()


def test_run_backfill_task_ignores_enabled_flag(settings, cnv_connection):
    """Task cũng phải chạy khi cờ tắt — command --async đi qua đường này."""
    from apps.cnv_loyalty import tasks

    settings.CNV_SYNC_ENABLED = False
    with patch.object(tasks, "_build_service") as build:
        build.return_value = MagicMock()
        with patch(
            "apps.cnv_loyalty.application.services.backfill_service.CnvBackfillService.run",
            return_value={"orders": {"seen": 0}},
        ):
            result = tasks.run_backfill(entities=["orders"])
    assert result == {"orders": {"seen": 0}}
    assert build.call_args.kwargs["require_enabled"] is False
