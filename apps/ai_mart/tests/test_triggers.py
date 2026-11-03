"""Trigger dw_updated_at phải bắt MỌI đường ghi — kể cả .update()/.bulk_update()
vốn bỏ qua auto_now (chính lý do không dùng được updated_at làm watermark:
webhook KiotViet webhook_impl.py:380, sync Foxia order_sync_service.py:253)."""

from django.db import connection
from django.test import TestCase, TransactionTestCase

from apps.orders.models import Order
from .helpers import mk_order


def _dw(order_id: int):
    with connection.cursor() as cur:
        cur.execute("SELECT dw_updated_at FROM orders WHERE id = %s", [order_id])
        return cur.fetchone()[0]


class DwUpdatedAtTriggerTests(TransactionTestCase):
    """TransactionTestCase is a deliberate, COSTLY exception used only because
    PostgreSQL's now() is transaction-scoped: within Django's TestCase atomic
    wrapper, both INSERT and UPDATE get identical timestamps. Future ai_mart
    tests should default to TestCase unless requiring explicit transaction control.

    Ba đường ghi được kiểm trong MỘT test (subTest) vì mỗi test TransactionTestCase
    flush toàn bộ bảng khi kết thúc (5–6 s) — ba đơn độc lập, ba subTest, một
    lần flush; subTest nào đỏ vẫn hiện tên riêng.
    """

    def test_every_write_path_bumps_dw_updated_at(self):
        with self.subTest("insert sets dw_updated_at"):
            order = mk_order()
            self.assertIsNotNone(_dw(order.id))

        with self.subTest("queryset .update() bumps"):
            order = mk_order()
            before = _dw(order.id)
            Order.objects.filter(id=order.id).update(status=2)
            self.assertGreater(_dw(order.id), before)

        with self.subTest("bulk_update() bumps"):
            order = mk_order()
            before = _dw(order.id)
            order.status = 3
            Order.objects.bulk_update([order], ["status"])
            self.assertGreater(_dw(order.id), before)


class DwTouchTriggerCatalogTests(TestCase):
    """Chỉ đọc information_schema — không cần transaction thật, TestCase đủ."""

    def test_all_five_tables_have_trigger(self):
        expected = {
            "orders", "order_items", "customers",
            "order_assessments", "callcenter_call_records",
        }
        with connection.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT event_object_table FROM information_schema.triggers"
                " WHERE trigger_name LIKE '%%_dw_touch'"
            )
            tables = {row[0] for row in cur.fetchall()}
        self.assertEqual(tables, expected)
