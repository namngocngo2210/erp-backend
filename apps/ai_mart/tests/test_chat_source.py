"""Bảng local (test/dev) và foreign table (prod) PHẢI cùng tên cột, cùng kiểu.

Lệch một cột là transform chạy xanh trên CI rồi chết trên prod — đúng loại bug
mà 'một nguồn sinh cả hai DDL' sinh ra để chặn.
"""

import re

from django.db import connection
from django.test import TestCase

from apps.ai_mart.infrastructure import chat_source


def _columns(ddl: str, table: str) -> list[str]:
    body = re.search(rf"{table} \((.*?)\n\)", ddl, re.S).group(1)
    return [line.strip().split()[0] for line in body.strip().splitlines() if line.strip()]


class ChatSourceTests(TestCase):
    def test_local_and_foreign_ddl_have_identical_columns(self):
        local = chat_source.create_local_tables_sql()
        foreign = chat_source.create_foreign_tables_sql()
        for table in chat_source.CHAT_TABLES:
            self.assertEqual(
                _columns(local, table), _columns(foreign, table), f"lệch cột ở {table}"
            )

    def test_foreign_ddl_sets_fetch_size_but_local_ddl_has_no_options(self):
        # fetch_size mặc định 100 dòng/round-trip là quá đắt cho rebuild đêm
        # (quét trọn 769k tin qua cầu). Kiểm luôn rằng nó KHÔNG rò sang DDL bảng
        # local — nếu rò, test "hai DDL cùng cột" ở trên mất ý nghĩa vì cả hai
        # phía cùng sai giống nhau.
        cau_lenh = chat_source.create_foreign_tables_sql().split(";")
        local = chat_source.create_local_tables_sql()
        for table in chat_source.CHAT_TABLES:
            ddl = next(c for c in cau_lenh if f"{chat_source.STG_SCHEMA}.{table} (" in c)
            self.assertIn(f"fetch_size '{chat_source.FETCH_SIZE}'", ddl, table)
        self.assertNotIn("fetch_size", local)
        self.assertNotIn("OPTIONS", local)

    def test_foreign_ddl_maps_to_real_source_table_names(self):
        foreign = chat_source.create_foreign_tables_sql()
        # ai_stg.chat_conversations phải trỏ tới public.conversations bên crawler
        self.assertIn("table_name 'conversations'", foreign)
        self.assertIn("SERVER chat_crawler", foreign)
        self.assertNotIn("password", foreign.lower())  # mật khẩu KHÔNG nằm trong DDL bảng

    def test_migration_created_local_tables(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = 'ai_stg' AND c.relname LIKE 'chat\\_%'"
            )
            found = {r[0] for r in cur.fetchall()}
        self.assertEqual(found, set(chat_source.CHAT_TABLES))

    def test_messages_table_has_columns_transform_depends_on(self):
        # Task 3/4 đọc đúng các cột này; đổi tên ở nguồn phải làm test đỏ ở đây trước.
        with connection.cursor() as cur:
            cur.execute(
                "SELECT a.attname FROM pg_attribute a"
                " WHERE a.attrelid = 'ai_stg.chat_messages'::regclass AND a.attnum > 0"
            )
            cols = {r[0] for r in cur.fetchall()}
        self.assertLessEqual(
            {"id", "page_id", "conversation_id", "direction", "type", "content",
             "attachments", "inserted_at"},
            cols,
        )

    def test_drop_tables_sql_works_on_local_tables(self):
        """Đường local -> foreign của `ai_mart_chat_fdw` phải chạy được.

        Sự cố prod 05/09/2026: `DROP FOREIGN TABLE IF EXISTS` gặp đúng tên nhưng
        SAI KIỂU object thì Postgres nổ ("chat_pages" is not a foreign table) —
        `IF EXISTS` chỉ bỏ qua trường hợp KHÔNG TỒN TẠI. Sau migration 0015 các
        bảng này là bảng LOCAL, nên lệnh chết ngay ở lần chạy đầu trên môi trường
        mới. Test chạy đúng trạng thái đó: DROP rồi tạo lại.
        """
        with connection.cursor() as cur:
            cur.execute(chat_source.drop_tables_sql())
            cur.execute(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = 'ai_stg' AND c.relname LIKE 'chat\\_%'"
            )
            self.assertEqual(cur.fetchone()[0], 0, "DROP phải dọn sạch cả 6 bảng")
            # dựng lại để không bỏ lại schema rỗng cho test khác trong cùng lớp
            cur.execute(chat_source.create_local_tables_sql())
            cur.execute(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = 'ai_stg' AND c.relname LIKE 'chat\\_%'"
            )
            self.assertEqual(cur.fetchone()[0], len(chat_source.CHAT_TABLES))

    def test_drop_tables_sql_is_noop_when_nothing_exists(self):
        """Chạy lại lệnh khi ai_stg đã trống không được nổ."""
        with connection.cursor() as cur:
            cur.execute(chat_source.drop_tables_sql())
            cur.execute(chat_source.drop_tables_sql())  # lần hai: không còn gì để DROP
            cur.execute(chat_source.create_local_tables_sql())
