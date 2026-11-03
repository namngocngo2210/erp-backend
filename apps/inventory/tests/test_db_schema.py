"""Schema/DB-level của sổ kho: partition stock_movements, FK tầng DB, system checks.

Lưu ý: các test partition tạo bảng partition THẬT (stock_movements_203003,
stock_movements_203506, ...) trên test DB và KHÔNG drop sau khi chạy — giữ chúng
cùng file để sự phụ thuộc này nhìn thấy được.
"""
from __future__ import annotations

import pytest
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase

from apps.inventory.application.services.partition_service import PartitionService
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import MovementType, StockMovement
from apps.catalog.models import Product
from apps.warehouses.models import Warehouse


class PartitionServiceTests(TransactionTestCase):
    def _partition_exists(self, name):
        with connection.cursor() as cur:
            cur.execute("SELECT to_regclass(%s);", [f"public.{name}"])
            return cur.fetchone()[0] is not None

    def test_ensure_month_partition_creates_and_is_idempotent(self):
        name = PartitionService.ensure_month_partition(2030, 3)
        assert name == "stock_movements_203003"
        assert self._partition_exists(name)
        again = PartitionService.ensure_month_partition(2030, 3)
        assert again == name


class EnsurePartitionTaskTests(TransactionTestCase):
    def test_task_creates_current_and_next_month(self):
        from apps.inventory.tasks import ensure_stock_movements_partition

        result = ensure_stock_movements_partition.run()
        assert len(result["partitions"]) == 2
        assert all(p.startswith("stock_movements_") for p in result["partitions"])


class EnsureMonthPartitionSelfHealTests(TransactionTestCase):
    def test_absorbs_rows_already_in_default(self):
        wh = Warehouse.objects.create(code="W-HEAL", name="Kho Heal")
        product = Product.objects.create(name="SP Heal", sku="HEAL-1")
        # Movement dated in a far-future month with no partition -> lands in DEFAULT.
        # occurred_at có DEFAULT now() ở tầng DB (migration 0024/0026) nên bỏ trống
        # vẫn chạy; ở đây truyền tay để ngày nghiệp vụ khớp ngày ghi sổ giả lập.
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO stock_movements "
                "(warehouse_id, product_id, qty_delta, movement_type, state, "
                " balance_after, note, created_at, occurred_at) "
                "VALUES (%s, %s, 1, 'receipt', 'sellable', 1, '', %s, %s);",
                [wh.id, product.id, "2035-06-15 00:00:00+00", "2035-06-15 00:00:00+00"],
            )
        name = PartitionService.ensure_month_partition(2035, 6)
        assert name == "stock_movements_203506"
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM stock_movements_203506;")
            assert cur.fetchone()[0] == 1
            cur.execute(
                "SELECT count(*) FROM stock_movements_default "
                "WHERE created_at >= '2035-06-01' AND created_at < '2035-07-01';"
            )
            assert cur.fetchone()[0] == 0


class StockMovementsPartitionTests(TransactionTestCase):
    reset_sequences = True

    def test_parent_table_is_partitioned_by_range(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT partstrat FROM pg_partitioned_table pt "
                "JOIN pg_class c ON c.oid = pt.partrelid "
                "WHERE c.relname = 'stock_movements';"
            )
            row = cur.fetchone()
        assert row is not None, "stock_movements phải là partitioned table"
        assert row[0] == "r"  # RANGE

    def test_insert_routes_and_reads_back(self):
        wh = Warehouse.objects.create(code="W-PART", name="Kho Part")
        product = Product.objects.create(name="SP Part", sku="PART-1")
        result = StockCommandService.record_movement(
            warehouse_id=wh.id, product_id=product.id,
            qty_delta=5, movement_type=MovementType.RECEIPT,
        )
        mv = StockMovement.objects.get(id=result.movement_id)
        assert mv.qty_delta == 5
        assert mv.balance_after == 5

    def test_current_and_next_month_partitions_exist(self):
        from django.utils import timezone

        now = timezone.now()
        year, month = now.year, now.month
        ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
        cur_name = f"stock_movements_{year:04d}{month:02d}"
        next_name = f"stock_movements_{ny:04d}{nm:02d}"
        with connection.cursor() as cur:
            cur.execute("SELECT to_regclass(%s), to_regclass(%s);",
                        [f"public.{cur_name}", f"public.{next_name}"])
            cur_reg, next_reg = cur.fetchone()
        assert cur_reg is not None, f"{cur_name} phải tồn tại"
        assert next_reg is not None, f"{next_name} phải tồn tại"


# ---------------------------------------------------------------------------
# Bảng partitioned `stock_movements` phải có FK thật ở tầng DB.
#
# Migration 0008 dựng lại bảng bằng raw SQL và làm rơi TOÀN BỘ mệnh đề
# REFERENCES — PROTECT/SET_NULL từ đó chỉ tồn tại ở ORM: một đường ghi raw
# SQL/psql xoá Warehouse/Product sẽ để lại movement mồ côi trong sổ cái mà DB
# không chặn. Migration 0032 phải vá lại (PG hỗ trợ FK outgoing trên bảng
# partitioned). (gốc: test_stock_movements_db_fks.py)
# ---------------------------------------------------------------------------

_EXPECTED = {
    "stockmove_warehouse_fk": ("warehouses", "r"),
    "stockmove_product_fk": ("products", "r"),
    "stockmove_created_by_fk": ("users", "n"),
}


@pytest.mark.django_db
def test_stock_movements_has_db_level_foreign_keys():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT con.conname, rel.relname AS referenced, con.confdeltype
            FROM pg_constraint con
            JOIN pg_class src ON src.oid = con.conrelid
            JOIN pg_class rel ON rel.oid = con.confrelid
            WHERE con.contype = 'f' AND src.relname = 'stock_movements'
            """
        )
        rows = {name: (ref, deltype) for name, ref, deltype in cursor.fetchall()}
    for conname, expected in _EXPECTED.items():
        assert rows.get(conname) == expected, (
            f"thiếu/sai FK {conname}: {rows.get(conname)} != {expected}; "
            f"toàn bộ FK hiện có: {rows}"
        )


@pytest.mark.django_db
def test_stock_movements_created_by_has_index():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'stock_movements'"
        )
        names = {row[0] for row in cursor.fetchall()}
    assert "stockmove_created_by_idx" in names, names


# ---------------------------------------------------------------------------
# Chạy Django system checks KÈM check phụ thuộc database.
#
# Vì sao cần một test riêng cho việc này: pytest KHÔNG chạy system checks, còn
# `manage.py check` trần thì `Model._check_indexes(databases)` lặp trên danh sách
# RỖNG ⇒ mọi lỗi E033/E034 (tên index/constraint vượt giới hạn của backend) bị bỏ
# qua im lặng. Đúng lỗ hổng đã để một tên index 31 ký tự đi tới tận lúc `migrate`
# abort. `scripts/deploy-prod.sh` nay truyền `--database default`; test này là
# chốt chặn tương ứng trong CI. (gốc: test_system_checks.py)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_system_checks_pass_including_database_checks():
    # call_command("check") raise SystemCheckError khi có lỗi mức ERROR trở lên.
    call_command("check", databases=["default"])
