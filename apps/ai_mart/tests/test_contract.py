"""Hợp đồng schema ai: mọi bảng, mọi cột PHẢI có COMMENT tiếng Việt (spec §4.7).

Introspect pg_catalog nên bảng thêm ở sprint sau TỰ ĐỘNG bị kiểm — không phải
sửa test. Đây là 'một bộ test theo cấu trúc hệ thống' mà CLAUDE.md yêu cầu.
"""

from django.db import connection
from django.test import TestCase


class AiSchemaContractTests(TestCase):
    def _tables(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = 'ai' AND c.relkind IN ('r', 'v')"
            )
            return [r[0] for r in cur.fetchall()]

    def test_every_table_and_column_has_comment(self):
        missing = []
        with connection.cursor() as cur:
            for table in self._tables():
                cur.execute(
                    "SELECT obj_description(('ai.' || quote_ident(%s))::regclass)", [table]
                )
                if not (cur.fetchone()[0] or "").strip():
                    missing.append(f"ai.{table} (bảng)")
                cur.execute(
                    "SELECT a.attname, col_description(a.attrelid, a.attnum)"
                    " FROM pg_attribute a"
                    " WHERE a.attrelid = ('ai.' || quote_ident(%s))::regclass"
                    " AND a.attnum > 0 AND NOT a.attisdropped",
                    [table],
                )
                for col, comment in cur.fetchall():
                    if not (comment or "").strip():
                        missing.append(f"ai.{table}.{col}")
        self.assertEqual(missing, [], f"thiếu COMMENT: {missing}")

    def test_no_forbidden_pii_columns(self):
        # spec §4.6 — không cột nào mang email/địa chỉ chi tiết/SĐT thô
        forbidden = ("email", "dia_chi", "address", "phone_raw", "sdt_goc")
        bad = []
        with connection.cursor() as cur:
            for table in self._tables():
                cur.execute(
                    "SELECT a.attname FROM pg_attribute a"
                    " WHERE a.attrelid = ('ai.' || quote_ident(%s))::regclass"
                    " AND a.attnum > 0 AND NOT a.attisdropped",
                    [table],
                )
                for (col,) in cur.fetchall():
                    if any(f in col for f in forbidden):
                        bad.append(f"ai.{table}.{col}")
        self.assertEqual(bad, [])
