"""Quick-sale KiotViet CHỈ lên hoá đơn — nửa phía ORDERS.

Hai mảnh thuộc sở hữu app `orders`: hành động API ghi cờ `direct_dispatch_deferred=True`,
và worker gắn nhãn `shipping_type='direct'` sau khi tạo hoá đơn thành công. Worker KHÔNG
ghi sổ tồn — hàng rời kho qua một trong hai cửa của app inventory (quét mã hoá đơn, hoặc
nút "Xuất kho" ở chi tiết đơn), xem `apps.inventory.tests.test_manual_dispatch`.

Cờ `direct_dispatch_deferred` vẫn phải được ghi `=True`: nó phân biệt đơn direct CŨ (đã
trừ tồn lúc lên hoá đơn) với đơn mới. Xem
docs/superpowers/specs/2026-08-29-warehouse-dispatch-rework-design.md §4.
"""
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.models import MovementType, StockMovement
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order, OrderAssessment, OrderItem, OrderKiotVietJob, OrderStatus
from apps.users.tests.helpers import make_admin
from apps.warehouses.models import Warehouse


def _make_order_with_item(channel, warehouse, suffix, qty=2):
    """Fixture tối giản CHỈ dùng model của orders/catalog/customers (không chạm
    inventory): `DeferredWorkerTests` không cần tồn đầu kỳ — `record_movement`
    cho phép âm, và bài test chỉ quan tâm có/không có bút toán SALE, không quan
    tâm số dư tuyệt đối. Tránh reach vào `apps.inventory.tests.helpers.make_order`
    để file này không vô tình tạo phụ thuộc ngược `orders → inventory`.
    """
    customer = Customer.objects.create(
        full_name=f"Khach Worker {suffix}",
        phone_raw=f"09330000{suffix}",
        phone_normalized=f"09330000{suffix}",
    )
    product = Product.objects.create(name=f"SP Worker {suffix}", sku=f"SKU-WK-{suffix}")
    order = Order.objects.create(
        channel=channel,
        customer=customer,
        name=f"Khach Worker {suffix}",
        phone=f"09330000{suffix}",
        total_amount="200000",
        status=OrderStatus.AWAITING_SHIPMENT,
        shipping_code="",
        fulfillment_warehouse=warehouse,
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        sku=product.sku,
        quantity=qty,
        unit_price=100000,
        line_total=100000 * qty,
    )
    return order


class QuickSaleDeferredApiTests(TestCase):
    """API đặt lệnh: cờ phải được ghi TƯỜNG MINH cả hai chiều."""

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-deferred")
        self.client.force_authenticate(self.admin)
        self.channel = Channel.objects.create(code="dd-api", name="Deferred Api")
        self.kv_channel = self._make_kv_channel()
        self.warehouse = Warehouse.objects.create(name="Kho Deferred", code="KHO-DD")
        self.customer = Customer.objects.create(
            full_name="KH DD", phone_raw="0911000010", phone_normalized="0911000010"
        )

    def _make_kv_channel(self):
        # KiotVietChannel không có cột `code` (đã kiểm bằng grep trước khi viết test) —
        # `retailer` mới là field bắt buộc, theo đúng convention của
        # test_direct_sale_shipping_type.py.
        return KiotVietChannel.objects.create(name="KV DD", retailer="r-dd")

    def _make_order(self, phone: str) -> Order:
        return Order.objects.create(
            channel=self.channel, customer=self.customer, name="KH DD", phone=phone,
            status=OrderStatus.AWAITING_SHIPMENT,
            kiotviet_channel=self.kv_channel, fulfillment_warehouse=self.warehouse,
        )

    def _post(self, action: str, order: Order):
        # dispatch bị patch: test này chỉ kiểm cờ được ghi, không chạy worker.
        with patch("apps.orders.application.services.order_kiotviet_job_service.dispatch"):
            return self.client.post(
                "/api/orders/kiotviet-actions",
                {"action": action, "ids": [order.id]},
                format="json",
            )

    def test_deferred_action_sets_flag(self):
        order = self._make_order("0911000011")
        response = self._post("quick_sale_deferred_selected", order)
        self.assertEqual(response.status_code, 202, response.data)
        order.refresh_from_db()
        self.assertIs(order.direct_dispatch_deferred, True)

    def test_legacy_quick_sale_action_also_defers(self):
        """Đảo kỳ vọng 2026-08-28: `quick_sale_selected` không còn là đường trừ tồn ngay.

        Enum cũ được giữ để tab đang mở của sale không ăn 400 lúc deploy, nhưng nó phải
        đi đúng cùng một đường — nếu nó còn ghi cờ False thì đơn của người bấm nhầm tab
        cũ vẫn bị trừ tồn theo luật cũ, đúng thứ ta vừa gỡ.
        """
        order = self._make_order("0911000012")
        response = self._post("quick_sale_selected", order)
        self.assertEqual(response.status_code, 202, response.data)
        order.refresh_from_db()
        self.assertIs(order.direct_dispatch_deferred, True)

    def test_deferred_action_keeps_existing_guards(self):
        # Guard "đã đẩy đi vận chuyển" phải áp cho CẢ hành động mới.
        order = self._make_order("0911000013")
        order.order_code = "PUSHED-1"
        order.save(update_fields=["order_code"])
        response = self._post("quick_sale_deferred_selected", order)
        self.assertEqual(response.status_code, 400, response.data)
        order.refresh_from_db()
        self.assertIs(order.direct_dispatch_deferred, False)

    def test_deferred_action_requires_warehouse(self):
        order = self._make_order("0911000014")
        order.fulfillment_warehouse = None
        order.save(update_fields=["fulfillment_warehouse"])
        response = self._post("quick_sale_deferred_selected", order)
        self.assertEqual(response.status_code, 400, response.data)


class DeferredWorkerTests(TestCase):
    """Job create_invoice thành công trên đơn deferred: gắn nhãn, KHÔNG ghi sổ."""

    def setUp(self):
        self.channel = Channel.objects.create(code="dd-wk", name="Deferred Worker")
        self.warehouse = Warehouse.objects.create(name="Kho WK", code="KHO-WK")
        self.order = _make_order_with_item(self.channel, self.warehouse, "21")
        self.order.invoice_code = "HD-DD-21"
        self.order.direct_dispatch_deferred = True
        self.order.save(update_fields=["invoice_code", "direct_dispatch_deferred"])
        self.job = OrderKiotVietJob.objects.create(
            order=self.order, action=OrderKiotVietJob.ACTION_CREATE_INVOICE
        )

    def _sale_movements(self):
        return StockMovement.objects.filter(
            reference_type="order", reference_id=self.order.id, movement_type=MovementType.SALE
        )

    def test_deferred_order_is_labelled_but_not_issued(self):
        from apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks import (
            _apply_shipping_type_after_job,
        )

        _apply_shipping_type_after_job(self.job, self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_type, "direct")
        self.assertEqual(self._sale_movements().count(), 0)
        assessment = OrderAssessment.objects.filter(order=self.order).first()
        self.assertNotEqual(
            getattr(assessment, "delivery_business_status_code", None), "dispatched"
        )

    def test_flag_false_is_no_longer_a_second_path(self):
        """Đảo kỳ vọng 2026-08-28: worker không đọc cờ nữa, không còn nhánh thứ hai.

        Ca này mô phỏng đơn mang cờ cũ (False) rơi vào worker — ví dụ job nằm hàng đợi
        từ trước lúc deploy. Nó phải đi CHUNG một đường với đơn deferred: gắn nhãn, không
        ghi sổ. Nếu ai đó khôi phục nhánh rẽ theo cờ thì test này đỏ.
        """
        self.order.direct_dispatch_deferred = False
        self.order.save(update_fields=["direct_dispatch_deferred"])

        from apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks import (
            _apply_shipping_type_after_job,
        )

        _apply_shipping_type_after_job(self.job, self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_type, "direct")
        self.assertEqual(self._sale_movements().count(), 0)
