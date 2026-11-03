"""Dựng cầu postgres_fdw sang DB crawler. Chạy TAY trên prod, cần SUPERUSER:

  docker compose exec -e CHAT_CRAWLER_DB_PASSWORD=... web \\
      python manage.py ai_mart_chat_fdw

CREATE EXTENSION/SERVER/USER MAPPING là đối tượng cấp cluster — không đặt trong
migration (DB test không có superuser). `--check` chỉ đo, không sửa gì.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from psycopg import sql as pg_sql

from apps.ai_mart.infrastructure import chat_source
from apps.sysconfig import sysconfig


class Command(BaseCommand):
    help = "Tạo/cập nhật foreign table ai_stg.chat_* trỏ sang DB crawler"

    def add_arguments(self, parser):
        parser.add_argument(
            "--check", action="store_true",
            help="chỉ kiểm tra cầu có đọc được không, không thay đổi gì",
        )

    def handle(self, *args, **options):
        if options["check"]:
            # _check chỉ in ra stdout, không có giá trị trả về; return trần để
            # handle() luôn trả None (BaseCommand chỉ in chuỗi nếu handle trả về).
            self._check()
            return

        password = sysconfig.get("CHAT_CRAWLER_DB_PASSWORD")
        if not password:
            raise CommandError("thiếu env CHAT_CRAWLER_DB_PASSWORD")

        # MỘT transaction cho toàn bộ DDL: DROP và CREATE là hai câu lệnh riêng,
        # hỏng ở giữa (mất mạng tới crawler, sai mật khẩu, tên bảng nguồn đổi) sẽ
        # để ai_stg KHÔNG CÒN bảng nguồn nào — lượt sync kế tiếp đổ vì
        # "relation does not exist", che mất nguyên nhân thật. DDL của Postgres
        # là transactional, kể cả CREATE EXTENSION/SERVER/USER MAPPING.
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS postgres_fdw")
            self._create_or_alter_server(cur)
            self._create_or_alter_user_mapping(cur, password)
            # Bảng local do migration 0015 tạo bị thay bằng foreign table.
            cur.execute(chat_source.drop_tables_sql())
            cur.execute(chat_source.create_foreign_tables_sql())
        self.stdout.write(self.style.SUCCESS("cầu FDW sẵn sàng — kiểm bằng --check"))

    def _server_exists(self, cur) -> bool:
        cur.execute(
            "SELECT 1 FROM pg_foreign_server WHERE srvname = %s", [chat_source.SERVER_NAME]
        )
        return cur.fetchone() is not None

    def _create_or_alter_server(self, cur) -> None:
        server = pg_sql.Identifier(chat_source.SERVER_NAME)
        host = pg_sql.Literal(settings.CHAT_CRAWLER_DB_HOST)
        port = pg_sql.Literal(settings.CHAT_CRAWLER_DB_PORT)
        dbname = pg_sql.Literal(settings.CHAT_CRAWLER_DB_NAME)
        if self._server_exists(cur):
            cur.execute(
                pg_sql.SQL(
                    "ALTER SERVER {server} OPTIONS (SET host {host}, SET port {port},"
                    " SET dbname {dbname})"
                ).format(server=server, host=host, port=port, dbname=dbname)
            )
        else:
            cur.execute(
                pg_sql.SQL(
                    "CREATE SERVER {server} FOREIGN DATA WRAPPER postgres_fdw"
                    " OPTIONS (host {host}, port {port}, dbname {dbname})"
                ).format(server=server, host=host, port=port, dbname=dbname)
            )

    def _user_mapping_exists(self, cur) -> bool:
        cur.execute(
            "SELECT 1 FROM pg_user_mappings WHERE srvname = %s AND usename = current_user",
            [chat_source.SERVER_NAME],
        )
        return cur.fetchone() is not None

    def _create_or_alter_user_mapping(self, cur, password: str) -> None:
        server = pg_sql.Identifier(chat_source.SERVER_NAME)
        user = pg_sql.Literal(settings.CHAT_CRAWLER_DB_USER)
        # Mật khẩu CHỈ đi qua psycopg.sql.Literal — không nối chuỗi, không log.
        pw = pg_sql.Literal(password)
        if self._user_mapping_exists(cur):
            cur.execute(
                pg_sql.SQL(
                    "ALTER USER MAPPING FOR CURRENT_USER SERVER {server}"
                    " OPTIONS (SET user {user}, SET password {pw})"
                ).format(server=server, user=user, pw=pw)
            )
        else:
            cur.execute(
                pg_sql.SQL(
                    "CREATE USER MAPPING FOR CURRENT_USER SERVER {server}"
                    " OPTIONS (user {user}, password {pw})"
                ).format(server=server, user=user, pw=pw)
            )

    def _check(self):
        with connection.cursor() as cur:
            for table in chat_source.CHAT_TABLES:
                cur.execute(f"SELECT count(*) FROM ai_stg.{table}")  # noqa: S608 — tên do code sở hữu
                (n,) = cur.fetchone()
                self.stdout.write(f"ai_stg.{table}: {n} dòng")
