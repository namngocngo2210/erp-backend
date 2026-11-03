"""Kiểm bảng ai.dong_don_hang — 1 dòng/sản phẩm trong đơn, key_column=don_id.

Dùng chung fixture đơn của apps/ai_mart/tests/helpers.py (mk_order) — bám codebase,
factory OrderFactory không tồn tại trong repo này.
"""

from django.db import connection
from django.test import TestCase

from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.dong_don_hang import MART
from apps.ai_mart.tests.helpers import mk_order
from apps.catalog.models import Product
from apps.orders.models import OrderItem


class DongDonHangTests(TestCase):
    def test_lines_per_order(self):
        """Kiểm xem số dòng trong ai.dong_don_hang bằng số OrderItem của đơn.

        Tạo 2 OrderItem rõ ràng để test thực sự tính toán được dòng sản phẩm.
        """
        order = mk_order(status=0)

        # Tạo 2 sản phẩm và gắn vào đơn dưới dạng 2 dòng
        product1 = Product.objects.create(name="Test Product 1", sku="TEST-SKU-1")
        product2 = Product.objects.create(name="Test Product 2", sku="TEST-SKU-2")

        OrderItem.objects.create(order=order, product=product1, sku="TEST-SKU-1", quantity=1)
        OrderItem.objects.create(order=order, product=product2, sku="TEST-SKU-2", quantity=1)

        # Verify 2 items were created
        self.assertEqual(OrderItem.objects.filter(order=order).count(), 2)

        # Rebuild mart and verify rows in ai.dong_don_hang
        loaders.full_rebuild(MART)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM ai.dong_don_hang WHERE don_id = %s", [order.id]
            )
            count = cur.fetchone()[0]
        self.assertEqual(count, 2, f"Expected 2 lines for order {order.id}, got {count}")

    def test_deleted_item_removed_on_upsert(self):
        """Kiểm xem item bị xoá khỏi đơn được dọn ra khỏi ai.dong_don_hang.

        key_column=don_id (không phải dong_id) nên upsert xoá TOÀN BỘ dòng của đơn
        rồi insert lại — cách này xử lý được item bị xoá.
        """
        order = mk_order(status=0)

        # Tạo 2 item ban đầu
        product1 = Product.objects.create(name="Product A", sku="SKU-A")
        product2 = Product.objects.create(name="Product B", sku="SKU-B")
        item1 = OrderItem.objects.create(order=order, product=product1, sku="SKU-A")
        item2 = OrderItem.objects.create(order=order, product=product2, sku="SKU-B")

        # Rebuild mart
        loaders.full_rebuild(MART)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM ai.dong_don_hang WHERE don_id = %s", [order.id]
            )
            self.assertEqual(cur.fetchone()[0], 2)

        # Xoá 1 item
        item1.delete()

        # Upsert (nên xoá và tạo lại tất cả dòng của đơn)
        loaders.upsert_by_ids(MART, [order.id])

        # Verify chỉ còn 1 dòng
        with connection.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM ai.dong_don_hang WHERE don_id = %s", [order.id]
            )
            count = cur.fetchone()[0]
        self.assertEqual(count, 1, f"Expected 1 line after deleting 1 item, got {count}")

        # Xoá item cuối cùng
        item2.delete()

        # Upsert lần 2
        loaders.upsert_by_ids(MART, [order.id])

        # Verify không còn dòng nào
        with connection.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM ai.dong_don_hang WHERE don_id = %s", [order.id]
            )
            count = cur.fetchone()[0]
        self.assertEqual(count, 0, f"Expected 0 lines after deleting all items, got {count}")
