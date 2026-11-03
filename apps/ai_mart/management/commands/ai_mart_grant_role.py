"""Tạo/cập nhật role hermes_ro (spec §6). Chạy TAY trên prod sau migrate:
  docker compose exec -e HERMES_DB_PASSWORD=... web python manage.py ai_mart_grant_role
Role là đối tượng cluster-wide — KHÔNG đặt trong migration (test DB không được
tạo role, và migrate chạy lại là ALTER ROLE vô nghĩa)."""

import os

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from psycopg import sql as pg_sql


class Command(BaseCommand):
    help = "Tạo/cập nhật role chỉ-đọc hermes_ro cho schema ai"

    def handle(self, *args, **options):
        password = os.environ.get("HERMES_DB_PASSWORD", "")
        if not password:
            raise CommandError("thiếu env HERMES_DB_PASSWORD")
        with connection.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'hermes_ro'")
            exists = cur.fetchone() is not None
            if not exists:
                cur.execute("CREATE ROLE hermes_ro LOGIN CONNECTION LIMIT 3")
            # đổi mật khẩu qua tham số — KHÔNG format chuỗi SQL bằng tay
            cur.execute(
                pg_sql.SQL(
                    "ALTER ROLE hermes_ro WITH LOGIN PASSWORD {} CONNECTION LIMIT 3"
                ).format(pg_sql.Literal(password))
            )
            cur.execute("ALTER ROLE hermes_ro SET statement_timeout = '30s'")
            cur.execute("ALTER ROLE hermes_ro SET default_transaction_read_only = on")
            cur.execute("GRANT USAGE ON SCHEMA ai TO hermes_ro")
            cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA ai TO hermes_ro")
            cur.execute(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA ai GRANT SELECT ON TABLES TO hermes_ro"
            )
        self.stdout.write(self.style.SUCCESS("hermes_ro sẵn sàng — chỉ đọc schema ai"))
