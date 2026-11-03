"""Smoke test cho `report_index_usage`.

CỐ Ý không assert số liệu (`idx_scan`, size, số dòng): chúng phụ thuộc hoàn toàn
vào môi trường — DB test vừa tạo thì mọi bộ đếm bằng 0, còn trên prod thì khác
mỗi lần chạy. Test ở đây chỉ chốt HỢP ĐỒNG của command: chạy được, không nổ SQL,
và in ra đủ những phần mà người vận hành cần để ĐỌC ĐÚNG con số (uptime + header).
"""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import TestCase


class ReportIndexUsageCommandTests(TestCase):
    def _run(self, *args) -> str:
        out = StringIO()
        call_command("report_index_usage", *args, stdout=out)
        return out.getvalue()

    def test_default_table_is_orders_and_prints_uptime_context(self):
        output = self._run()

        self.assertIn("Table: orders", output)
        # Uptime là phần BẮT BUỘC: thiếu nó thì idx_scan=0 bị đọc sai thành
        # "index chết" ngay sau một lần restart Postgres.
        self.assertIn("Postgres started at:", output)
        self.assertIn("Stats accumulated over:", output)

    def test_output_has_expected_columns(self):
        output = self._run()

        self.assertIn("INDEX", output)
        self.assertIn("IDX_SCAN", output)
        self.assertIn("SIZE", output)
        self.assertIn("FLAGS", output)

    def test_accepts_table_and_min_scans_arguments(self):
        output = self._run("--table", "customers", "--min-scans", "5")

        self.assertIn("Table: customers", output)

    def test_unknown_table_reports_empty_instead_of_crashing(self):
        # Gõ nhầm tên bảng là chuyện thường khi chạy tay trên prod — phải ra thông
        # báo rỗng chứ không phải traceback.
        output = self._run("--table", "table_that_does_not_exist")

        self.assertIn("no indexes matching", output)
