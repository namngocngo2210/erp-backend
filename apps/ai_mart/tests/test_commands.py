from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.ai_mart.tests.helpers import mk_order


class CommandTests(TestCase):
    def test_rebuild_then_sync(self):
        mk_order(status=3)
        out = StringIO()
        call_command("ai_mart_rebuild", stdout=out)
        self.assertIn("ai.don_hang", out.getvalue())
        out = StringIO()
        call_command("ai_mart_sync", stdout=out)
        self.assertIn("cutoff", out.getvalue())

    def test_verify_reports_clean_on_test_data(self):
        mk_order(status=3)
        call_command("ai_mart_rebuild")
        out = StringIO()
        call_command("ai_mart_verify", stdout=out)
        self.assertIn("0 mã chưa có nhãn", out.getvalue())


class CommandTimeoutTests(TestCase):
    """Lệnh chạy tay phải tự nới statement_timeout (mặc định prod 30s cắt INSERT
    547k dòng — sự cố deploy 28/08/2026). Kiểm bằng cách đọc giá trị session
    ngay trong lúc lệnh chạy, không mock guard."""

    def _timeout_during(self, command, **opts):
        from django.db import connection
        from unittest import mock

        seen = {}

        def probe(*args, **kwargs):
            with connection.cursor() as cur:
                cur.execute("SHOW statement_timeout")
                seen["value"] = cur.fetchone()[0]
            return {"tables": {}, "cutoff": "x"}

        target = f"apps.ai_mart.management.commands.{command}.{'rebuild_all' if command == 'ai_mart_rebuild' else 'sync_hot'}"
        with mock.patch(target, side_effect=probe):
            call_command(command, stdout=StringIO(), **opts)
        return seen["value"]

    def test_rebuild_raises_timeout_to_one_hour(self):
        self.assertEqual(self._timeout_during("ai_mart_rebuild"), "1h")

    def test_sync_raises_timeout_to_ten_minutes(self):
        self.assertEqual(self._timeout_during("ai_mart_sync"), "10min")

    def test_timeout_option_zero_keeps_connection_default(self):
        # Guard coi <= 0 là no-op (apps.common.db_guards) — KHÔNG phải "tắt trần".
        self.assertNotEqual(self._timeout_during("ai_mart_rebuild", timeout=0), "1h")
