"""Vòng đời đơn bán trực tiếp (shipping_type=direct).

Một file cho cả vòng đời thay vì rải test theo từng hàm: giá trị của cờ này nằm ở chỗ
nó nối "tạo hoá đơn nhanh" với "trừ tồn" và "hoàn tồn khi huỷ" — tách ra thì mỗi test
đúng một mảnh mà đường nối vẫn có thể gãy.
"""
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderStatus, ShippingType
from apps.users.tests.helpers import make_admin


class ShippingTypeFieldTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="st-ch", name="Shipping Type Channel")
        # Order.customer là FK bắt buộc (PROTECT) — không liên quan tới cờ shipping_type
        # đang test, chỉ là điều kiện tối thiểu để tạo được một Order hợp lệ.
        self.customer = Customer.objects.create(
            full_name="KH Shipping Type", phone_raw="0900000000", phone_normalized="0900000000"
        )

    def test_new_order_has_no_shipping_type(self):
        # NULL = "chưa biết đơn này đi đường nào", khác hẳn shipment. Đơn cũ giữ NULL
        # vĩnh viễn nên mọi chỗ đọc cờ đều phải chịu được None.
        order = Order.objects.create(
            channel=self.channel, customer=self.customer, name="A", phone="0900000001"
        )
        self.assertIsNone(order.shipping_type)

    def test_shipping_type_accepts_both_values(self):
        order = Order.objects.create(
            channel=self.channel, customer=self.customer, name="B", phone="0900000002",
            shipping_type=ShippingType.DIRECT,
        )
        order.refresh_from_db()
        self.assertEqual(order.shipping_type, "direct")

        order.shipping_type = ShippingType.SHIPMENT
        order.save(update_fields=["shipping_type"])
        order.refresh_from_db()
        self.assertEqual(order.shipping_type, "shipment")


class ShippingTypeApiExposureTests(TestCase):
    """Task 2: cờ phải lộ ra API đọc nhưng KHÔNG được client ghi qua PATCH.

    Dùng Customer riêng trong setUp (Order.customer là FK PROTECT bắt buộc) — theo
    đúng convention của ShippingTypeFieldTests ở trên, không liên quan tới cờ đang test.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-shipping-type")
        self.channel = Channel.objects.create(code="st-api", name="Shipping Type Api")
        self.customer = Customer.objects.create(
            full_name="KH Shipping Type Api", phone_raw="0900000003", phone_normalized="0900000003"
        )

    def test_order_detail_exposes_shipping_type(self):
        order = Order.objects.create(
            channel=self.channel, customer=self.customer, name="C", phone="0900000003",
            status=OrderStatus.NEW, shipping_type=ShippingType.DIRECT,
        )
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/orders/{order.id}")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["shipping_type"], "direct")

    def test_shipping_type_is_read_only_on_patch(self):
        # Cờ do hệ thống gắn: client PATCH thẳng vào phải bị bỏ qua, không phải chấp nhận.
        order = Order.objects.create(
            channel=self.channel, customer=self.customer, name="D", phone="0900000004",
            status=OrderStatus.NEW,
        )
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            f"/api/orders/{order.id}", {"shipping_type": "direct"}, format="json"
        )
        self.assertIn(response.status_code, (200, 400))
        order.refresh_from_db()
        self.assertIsNone(order.shipping_type)


class QuickSaleWarehouseGuardTests(TestCase):
    """Task 3: chặn "Tạo nhanh" ở API khi đơn chưa có kho xuất.

    Bán trực tiếp trừ tồn NGAY theo kho xuất, nên guard phải chạy TRƯỚC khi tạo
    OrderKiotVietJob/dispatch — nếu để lọt, hoá đơn KiotViet được tạo trong khi
    tồn không trừ (nghiệp vụ coi như xong, kho sai âm thầm).
    """

    def setUp(self):
        from apps.kiotviet.models import KiotVietChannel
        from apps.warehouses.models import Warehouse

        self.client = APIClient()
        self.admin = make_admin("admin-quick-sale-guard")
        self.channel = Channel.objects.create(code="st-qs", name="Quick Sale Channel")
        self.kv_channel = KiotVietChannel.objects.create(
            name="KV QS", branch_id="1", branch_ids=["1"],
            client_id="c", client_secret="s", retailer="r",
        )
        self.warehouse = Warehouse.objects.create(code="KHO-QS", name="Kho Quick Sale")
        # Order.customer là FK PROTECT bắt buộc — như hai class trên, không liên
        # quan tới cờ đang test, chỉ là điều kiện tối thiểu để tạo Order hợp lệ.
        self.customer = Customer.objects.create(
            full_name="KH Quick Sale Guard", phone_raw="0900000005", phone_normalized="0900000005"
        )

    def _make_order(self, phone, warehouse=None):
        return Order.objects.create(
            channel=self.channel, customer=self.customer, kiotviet_channel=self.kv_channel,
            name="QS", phone=phone, status=OrderStatus.NEW,
            fulfillment_warehouse=warehouse,
        )

    def test_quick_sale_rejects_order_without_warehouse(self):
        from apps.orders.models import OrderKiotVietJob

        order = self._make_order("0900000005", warehouse=None)
        self.client.force_authenticate(user=self.admin)
        with patch("apps.orders.application.services.order_kiotviet_job_service.dispatch") as mock_dispatch:
            response = self.client.post(
                "/api/orders/kiotviet-actions",
                {"action": "quick_sale_selected", "step": "execute", "ids": [order.id]},
                format="json",
            )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["orders_without_warehouse"], [order.id])
        # Không được tạo job: hoá đơn KiotViet tạo ra mà tồn không trừ là ca tệ nhất.
        mock_dispatch.assert_not_called()
        self.assertEqual(OrderKiotVietJob.objects.count(), 0)

    def test_quick_sale_rejects_order_already_pushed(self):
        """Đơn đã có order_code = đã đẩy đi vận chuyển, chờ kho quét xuất theo đường
        shipment. Quick-sale chọn nhầm vào đây sẽ lật nhãn sang direct và ép
        "dispatched" TRƯỚC KHI hàng thật sự rời kho theo đường shipment — chặn ở API,
        cùng tầng với guard thiếu kho ở trên.
        """
        from apps.orders.models import OrderKiotVietJob

        order = self._make_order("0900000009", warehouse=self.warehouse)
        Order.objects.filter(pk=order.pk).update(order_code="DH-PUSHED-1")
        self.client.force_authenticate(user=self.admin)
        with patch("apps.orders.application.services.order_kiotviet_job_service.dispatch") as mock_dispatch:
            response = self.client.post(
                "/api/orders/kiotviet-actions",
                {"action": "quick_sale_selected", "step": "execute", "ids": [order.id]},
                format="json",
            )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["orders_already_pushed"], [order.id])
        mock_dispatch.assert_not_called()
        self.assertEqual(OrderKiotVietJob.objects.count(), 0)

    def test_quick_sale_allows_order_with_warehouse(self):
        order = self._make_order("0900000006", warehouse=self.warehouse)
        self.client.force_authenticate(user=self.admin)
        with patch("apps.orders.application.services.order_kiotviet_job_service.dispatch"):
            response = self.client.post(
                "/api/orders/kiotviet-actions",
                {"action": "quick_sale_selected", "step": "execute", "ids": [order.id]},
                format="json",
            )
        self.assertEqual(response.status_code, 202, response.data)


class DirectSaleStockTests(TestCase):
    """Task 4: worker gắn cờ shipping_type=direct khi hoá đơn tạo xong — KHÔNG ghi sổ.

    Lớp 1 (view, class trên) chặn lúc bấm nút; lớp 2 ở đây là chốt cuối trong worker vì
    kho có thể bị gỡ trong lúc job nằm hàng đợi.

    Từ 2026-08-28 worker KHÔNG trừ tồn nữa: worker chỉ gắn nhãn, hàng rời kho qua một
    trong hai cửa của app inventory — kho quét MÃ HOÁ ĐƠN ở màn quét xuất, hoặc người
    dùng bấm nút "Xuất kho" ở chi tiết đơn (`ManualDispatchService.dispatch`). Guard
    "phải có kho xuất" vẫn giữ nguyên — kho xuất chính là kho mà hai cửa đó ghi sổ vào,
    thiếu nó thì không có kho nào để chọn khi xuất.
    """

    def setUp(self):
        from apps.kiotviet.models import KiotVietChannel
        from apps.orders.models import OrderItem
        from apps.catalog.models import Product
        from apps.warehouses.models import Warehouse

        self.admin = make_admin("admin-direct-stock")
        self.channel = Channel.objects.create(code="st-ds", name="Direct Sale Channel")
        self.kv_channel = KiotVietChannel.objects.create(
            name="KV DS", branch_id="1", branch_ids=["1"],
            client_id="c", client_secret="s", retailer="r",
        )
        self.warehouse = Warehouse.objects.create(code="KHO-DS", name="Kho Direct Sale")
        self.product = Product.objects.create(sku="SKU-DS", name="San pham DS")
        # Order.customer là FK PROTECT bắt buộc — theo đúng convention của các class trên,
        # không liên quan tới cờ/trừ tồn đang test.
        self.customer = Customer.objects.create(
            full_name="KH Direct Stock", phone_raw="0900000007", phone_normalized="0900000007"
        )
        self.order = Order.objects.create(
            channel=self.channel, customer=self.customer, kiotviet_channel=self.kv_channel,
            name="Direct", phone="0900000007", status=OrderStatus.NEW,
            fulfillment_warehouse=self.warehouse,
        )
        OrderItem.objects.create(order=self.order, product=self.product, quantity=2)

    def _run_job(self, action="create_invoice", fake_invoice_code="HD-1"):
        from apps.orders.models import OrderKiotVietJob
        from apps.orders.infrastructure.tasks.order_tasks_impl import ingest_job_tasks

        job = OrderKiotVietJob.objects.create(
            order=self.order, action=action, requested_by=self.admin
        )

        def fake_execute(order, job_action, **kwargs):
            if fake_invoice_code:
                Order.objects.filter(pk=order.pk).update(invoice_code=fake_invoice_code)
            return {"status": "created", "payload": {"status": "created"}, "update_fields": []}

        with patch.object(ingest_job_tasks, "_execute_kiotviet_action", side_effect=fake_execute):
            ingest_job_tasks.process_kiotviet_job(job.id)
        return job

    def test_quick_sale_marks_direct_without_issuing_stock(self):
        """Đảo kỳ vọng 2026-08-28: chỉ gắn nhãn, tồn kho không nhúc nhích.

        Cũng canh luôn `dispatched`: cờ đó là tín hiệu mà `_dispatched_order_ids` dùng để
        loại đơn đã xuất khỏi con số "đang chờ xuất" (cảnh báo trên form đơn). Gắn nó ở
        đây là đơn biến mất khỏi cảnh báo trước khi kho thật sự bấm "Xuất kho" hay quét
        mã hoá đơn — hàng coi như đã ra khỏi kho mà không ai ghi sổ.

        StockMovement không có cột order_id/quantity — nó lưu chứng từ qua
        (reference_type, reference_id) và số lượng qua qty_delta.
        """
        from apps.inventory.models import MovementReferenceType, StockMovement
        from apps.orders.models import OrderAssessment

        self._run_job()
        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_type, "direct")
        assessment = OrderAssessment.objects.filter(order=self.order).first()
        self.assertNotEqual(
            getattr(assessment, "delivery_business_status_code", None), "dispatched"
        )
        self.assertFalse(
            StockMovement.objects.filter(
                reference_type=MovementReferenceType.ORDER,
                reference_id=self.order.id,
            ).exists()
        )

    def test_auto_flip_sales_status_records_requester_as_actor(self):
        """Flip created_order là hệ quả thao tác của người bấm job — journey phải hiện
        tên họ, không phải "Hệ thống"."""
        from apps.orders.models import OrderJourneyEvent

        self._run_job()
        event = OrderJourneyEvent.objects.get(
            order=self.order, event_type="sales_status_changed", to_code="created_order"
        )
        self.assertEqual(event.actor_id, self.admin.id)
        self.assertEqual(event.payload_json["source"], "quick_sale_invoice_created")

    def test_quick_sale_never_calls_update_order_inline(self):
        """Chốt cứng việc gỡ đường trừ tồn ngay (2026-08-28).

        Assert "không có StockMovement" một mình là chưa đủ: `update_order_inline` với
        `delivery_business_status_code=dispatched` KHÔNG sinh bút toán nào cho đơn không
        khớp sản phẩm nào, nhưng vẫn gắn cờ đã xuất và đẩy đơn ra khỏi hàng đợi "Chờ xuất
        kho" — tồn sai im lặng mà test đếm bút toán vẫn xanh. Canh thẳng vào lời gọi.
        """
        from apps.orders.application.services.order_service_impl import OrderService

        with patch.object(OrderService, "update_order_inline") as mock_inline:
            self._run_job()

        mock_inline.assert_not_called()
        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_type, "direct")

    def test_quick_sale_is_idempotent_when_job_runs_twice(self):
        from apps.inventory.models import MovementReferenceType, StockMovement

        self._run_job()
        self._run_job()
        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_type, "direct")
        self.assertFalse(
            StockMovement.objects.filter(
                reference_type=MovementReferenceType.ORDER, reference_id=self.order.id
            ).exists()
        )

    def test_worker_refuses_quick_sale_without_warehouse(self):
        from apps.orders.models import OrderKiotVietJob, OrderKiotVietJobStatus
        from apps.orders.infrastructure.tasks.order_tasks_impl import ingest_job_tasks

        # Kho bị gỡ SAU khi job đã vào hàng đợi — lớp guard ở view không bắt được ca này.
        Order.objects.filter(pk=self.order.pk).update(fulfillment_warehouse=None)
        job = OrderKiotVietJob.objects.create(
            order=self.order, action="create_invoice", requested_by=self.admin
        )
        with patch.object(ingest_job_tasks, "_execute_kiotviet_action") as mock_exec:
            with self.assertRaises(ValueError):
                ingest_job_tasks.process_kiotviet_job(job.id)
        mock_exec.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, OrderKiotVietJobStatus.FAILED)
        self.assertIn("kho xuất", job.error_message.lower())

    def test_job_stays_success_when_post_processing_raises(self):
        """Hoá đơn KiotViet đã tạo THẬT (invoice_code đã ghi DB) trước khi
        `_apply_shipping_type_after_job` chạy. Nếu bước hậu xử lý này nổ (đơn bị huỷ
        chen ngang, mất kho, deadlock...) mà làm job thành FAILED, UI báo "tạo hoá đơn
        thất bại" trong khi hoá đơn đang SỐNG trên KiotViet → nhân viên bấm lại → hoá
        đơn TRÙNG. Job phải giữ SUCCESS, lỗi chỉ log để xử lý tay.
        """
        from apps.orders.models import OrderKiotVietJob, OrderKiotVietJobStatus
        from apps.orders.infrastructure.tasks.order_tasks_impl import ingest_job_tasks

        job = OrderKiotVietJob.objects.create(
            order=self.order, action="create_invoice", requested_by=self.admin
        )

        def fake_execute(order, job_action, **kwargs):
            Order.objects.filter(pk=order.pk).update(invoice_code="HD-CRIT-1")
            return {"status": "created", "payload": {"status": "created"}, "update_fields": []}

        with (
            patch.object(ingest_job_tasks, "_execute_kiotviet_action", side_effect=fake_execute),
            patch.object(
                ingest_job_tasks, "_apply_shipping_type_after_job", side_effect=RuntimeError("boom")
            ),
        ):
            # Không được raise ra ngoài process_kiotviet_job.
            ingest_job_tasks.process_kiotviet_job(job.id)

        job.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(job.status, OrderKiotVietJobStatus.SUCCESS)
        self.assertEqual(self.order.invoice_code, "HD-CRIT-1")

    def test_create_invoice_does_not_flip_shipment_to_direct(self):
        """Important (fix round 1): phòng thủ chiều sâu. Tầng API (Task 4) chỉ chặn
        quick-sale trên đơn có `order_code` ở NHÁNH ĐÓ; nếu vẫn lọt tới worker với một
        đơn đã xác nhận rời kho qua đường vận chuyển (`shipping_type=shipment`), job
        `create_invoice` không được lật cờ sang `direct` — lật rồi đơn bị huỷ sẽ khiến
        subscriber cộng trả tồn NGAY trong khi hàng thật đang ở đơn vị vận chuyển,
        chưa hề về kho (tồn ảo)."""
        from apps.inventory.models import MovementReferenceType, StockMovement

        Order.objects.filter(pk=self.order.pk).update(
            shipping_type="shipment", order_code="DH-GUARD-1"
        )
        self._run_job()
        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_type, "shipment")
        self.assertFalse(
            StockMovement.objects.filter(
                reference_type=MovementReferenceType.ORDER, reference_id=self.order.id
            ).exists()
        )

    def test_create_invoice_does_not_flip_when_shipping_code_present_but_flag_null(self):
        """Important (fix round 2): guard cũ chỉ kiểm shipping_type == shipment, nhưng
        MỌI đơn tạo trước khi tính năng này deploy đều mang cờ NULL — nên lớp phòng thủ
        đó không chặn được bất kỳ đơn nào trong dữ liệu hiện có. Đơn #12345 kiểu spec:
        đã lên vận đơn KShip (có shipping_code) từ trước, nhưng cờ vẫn NULL vì đơn được
        tạo trước deploy. Quick-sale chọn nhầm vào đây phải vẫn bị chặn ở worker."""
        from apps.inventory.models import MovementReferenceType, StockMovement

        Order.objects.filter(pk=self.order.pk).update(
            shipping_type=None, shipping_code="KS-999"
        )
        self._run_job()
        self.order.refresh_from_db()
        self.assertIsNone(self.order.shipping_type)
        self.assertFalse(
            StockMovement.objects.filter(
                reference_type=MovementReferenceType.ORDER, reference_id=self.order.id
            ).exists()
        )


class ShipmentFlagTests(TestCase):
    """Task 5: gắn shipping_type=shipment khi tạo vận đơn KShip thành công.

    push và create_kship_delivery là hai cửa khác nhau dẫn tới cùng một sự thật: đơn
    rời kho qua đường vận chuyển, không phải bán quầy. Test push_does_not_overwrite_direct
    canh đúng nguyên tắc bất di bất dịch: không ghi đè shipping_type đã có giá trị.
    """

    def setUp(self):
        from apps.kiotviet.models import KiotVietChannel

        self.admin = make_admin("admin-shipment-flag")
        self.channel = Channel.objects.create(code="st-sf", name="Shipment Flag Channel")
        self.kv_channel = KiotVietChannel.objects.create(
            name="KV SF", branch_id="1", branch_ids=["1"],
            client_id="c", client_secret="s", retailer="r",
        )
        # Order.customer là FK PROTECT bắt buộc — theo đúng convention của các class
        # trên, không liên quan tới cờ shipping_type đang test.
        self.customer = Customer.objects.create(
            full_name="KH Shipment Flag", phone_raw="0900000008", phone_normalized="0900000008"
        )

    def _run(self, order, action, mutate):
        from apps.orders.models import OrderKiotVietJob
        from apps.orders.infrastructure.tasks.order_tasks_impl import ingest_job_tasks

        job = OrderKiotVietJob.objects.create(order=order, action=action, requested_by=self.admin)

        def fake_execute(o, job_action, **kwargs):
            Order.objects.filter(pk=o.pk).update(**mutate)
            return {"status": "created", "payload": {"status": "created"}, "update_fields": []}

        with patch.object(ingest_job_tasks, "_execute_kiotviet_action", side_effect=fake_execute):
            ingest_job_tasks.process_kiotviet_job(job.id)

    def _order(self, phone, **kwargs):
        return Order.objects.create(
            channel=self.channel, customer=self.customer, kiotviet_channel=self.kv_channel,
            name="SF", phone=phone, status=OrderStatus.NEW, **kwargs
        )

    def test_kship_delivery_marks_shipment(self):
        order = self._order("0900000008")
        self._run(order, "create_kship_delivery", {"shipping_code": "KS-1"})
        order.refresh_from_db()
        self.assertEqual(order.shipping_type, "shipment")

    def test_push_marks_shipment(self):
        order = self._order("0900000009")
        self._run(order, "push", {"order_code": "DH-1"})
        order.refresh_from_db()
        self.assertEqual(order.shipping_type, "shipment")

    def test_push_does_not_overwrite_direct(self):
        order = self._order("0900000010", shipping_type="direct")
        self._run(order, "push", {"order_code": "DH-2"})
        order.refresh_from_db()
        self.assertEqual(order.shipping_type, "direct")


class DirectSaleCancelTests(TestCase):
    """Task 6: huỷ đơn bán trực tiếp CŨ → hoàn tồn NGAY, không qua hàng đợi chờ hàng về.

    CHỈ CÒN ÁP CHO ĐƠN CŨ. Từ 2026-08-28 mọi quick-sale mới mang
    `direct_dispatch_deferred=True` nên không rơi vào nhánh này; fixture dưới đây cố ý để
    cờ mặc định False để mô phỏng đơn direct tạo TRƯỚC mốc đó — chúng đã bị trừ tồn ngay
    lúc lên hoá đơn nên luật huỷ của chúng phải giữ nguyên. Nhánh của đơn deferred nằm ở
    `apps.inventory.tests.test_manual_dispatch`.

    Khách mua tại quầy nên hàng chưa từng rời khỏi kho theo nghĩa vật lý "đang trên
    đường về" — khác hẳn đơn giao vận, nơi hàng đã ra khỏi kho và cần kho quét nhập
    hoàn mới được cộng trả. Test đối chứng `test_shipment_order_cancel_keeps_waiting_
    for_return` canh đúng: đơn `shipment` (và ngầm định cả đơn NULL, do cả hai đều
    không khớp `_is_direct_sale`) phải giữ nguyên luật cũ.
    """

    def setUp(self):
        from apps.inventory.contracts.stock_sales import SaleLine, StockSalesService
        from apps.orders.models import OrderItem
        from apps.catalog.models import Product
        from apps.warehouses.models import Warehouse

        self.channel = Channel.objects.create(code="st-dc", name="Direct Cancel Channel")
        # Order.customer là FK PROTECT bắt buộc — theo đúng convention của các class
        # trên, không liên quan tới cờ shipping_type đang test.
        self.customer = Customer.objects.create(
            full_name="KH Direct Cancel", phone_raw="0900000011", phone_normalized="0900000011"
        )
        self.warehouse = Warehouse.objects.create(code="KHO-DC", name="Kho Direct Cancel")
        self.product = Product.objects.create(sku="SKU-DC", name="San pham DC")
        self.order = Order.objects.create(
            channel=self.channel, customer=self.customer, name="DC", phone="0900000011",
            status=OrderStatus.NEW, fulfillment_warehouse=self.warehouse,
            shipping_type="direct",
        )
        OrderItem.objects.create(order=self.order, product=self.product, quantity=3)
        StockSalesService.issue_for_order(
            order_id=self.order.id, warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=3)],
        )

    def _cancel(self):
        from apps.common.events import event_bus
        from apps.inventory import subscribers
        from apps.orders.contracts.order_events import OrderStatusChanged

        # register() idempotent theo trạng thái thật của bus (không dùng cờ module) —
        # gọi lại ở đây để không phụ thuộc thứ tự chạy test khác (test khác có thể đã
        # gọi event_bus.clear() và xoá mất subscriber này khỏi process).
        subscribers.register()
        event_bus.publish(
            OrderStatusChanged(order_id=self.order.id, old_status=0, new_status=6)
        )

    def test_direct_order_cancel_releases_stock_immediately(self):
        from apps.inventory.application.services.stock_sales_service import order_goods_still_out
        from apps.inventory.models import CancelledDispatchWatch

        self._cancel()
        # Hàng bán tại quầy nếu huỷ thì đang nằm ngay trong kho — không có khâu "hàng
        # đang trên đường về" để chờ.
        self.assertFalse(order_goods_still_out(self.order.id))
        self.assertFalse(CancelledDispatchWatch.objects.filter(order_id=self.order.id).exists())

    def test_shipment_order_cancel_keeps_waiting_for_return(self):
        from apps.inventory.models import CancelledDispatchWatch

        Order.objects.filter(pk=self.order.pk).update(shipping_type="shipment")
        self._cancel()
        self.assertTrue(CancelledDispatchWatch.objects.filter(order_id=self.order.id).exists())

    def test_null_shipping_type_order_cancel_keeps_waiting_for_return(self):
        """Đơn cũ trước tính năng này mang shipping_type=NULL — phải giữ luật cũ y hệt
        đơn shipment, không được vô tình rơi vào nhánh hoàn tồn ngay của đơn direct."""
        from apps.inventory.models import CancelledDispatchWatch

        Order.objects.filter(pk=self.order.pk).update(shipping_type=None)
        self._cancel()
        self.assertTrue(CancelledDispatchWatch.objects.filter(order_id=self.order.id).exists())

    def test_direct_order_revival_re_deducts_stock_to_exact_balance(self):
        """Regression cho bug Critical (fix round 1): note tự chế thay vì
        `CANCEL_RELEASE_NOTE` làm `pending_cancel_release_by_key` mù với bút toán hoàn
        tồn của đơn direct → `reissue_after_cancel_release` no-op khi đơn hồi sinh, tồn
        phồng vĩnh viễn. `625/625 passed` ở lần chạy trước KHÔNG bắt được bug này vì
        chưa test đúng chỗ hỏng: assert có/không CancelledDispatchWatch không đủ, phải
        đối chiếu SỐ DƯ THẬT trước/sau vòng huỷ → hồi sinh.
        """
        from apps.common.events import event_bus
        from apps.inventory import subscribers
        from apps.inventory.models import StockItem, StockState
        from apps.orders.contracts.order_events import OrderStatusChanged

        def _balance():
            return StockItem.objects.get(
                warehouse_id=self.warehouse.id, product_id=self.product.id,
                state=StockState.SELLABLE,
            ).quantity

        balance_after_sale = _balance()

        self._cancel()
        self.assertEqual(_balance(), balance_after_sale + 3)

        # Hồi sinh: Sale đổi trạng thái đơn từ Huỷ (6) về sống (0=NEW) bằng tay, không
        # qua tạo lại hoá đơn KV — đúng kịch bản coordinator mô tả trong finding Critical.
        subscribers.register()
        event_bus.publish(
            OrderStatusChanged(order_id=self.order.id, old_status=6, new_status=0)
        )

        # Số dư sau hồi sinh phải quay lại ĐÚNG số dư ngay sau lúc bán — không hơn,
        # không kém. Bug Critical để lại số dư = balance_after_sale + 3 (không trừ lại).
        self.assertEqual(_balance(), balance_after_sale)


class ShippingTypeFilterTests(TestCase):
    """Task 7: bộ lọc backend theo shipping_type cho trang Đơn hàng.

    Token "none" là quy ước riêng của tầng filter cho IS NULL — chuỗi rỗng đã
    mang nghĩa "không lọc" ở mọi trục khác nên không tái dùng được. Đơn cũ (trước
    khi có cờ này) đều nằm nhóm NULL nên thiếu token "none" là mất một tập lớn
    khỏi bộ lọc.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-shiptype-filter")
        self.channel = Channel.objects.create(code="st-fl", name="Filter Channel")
        # Order.customer là FK PROTECT bắt buộc — theo đúng convention của các
        # class trên, không liên quan tới cờ shipping_type đang test.
        self.customer = Customer.objects.create(
            full_name="KH Shipping Type Filter",
            phone_raw="0900000012",
            phone_normalized="0900000012",
        )
        self.direct = Order.objects.create(
            channel=self.channel, customer=self.customer, name="F1", phone="0900000012",
            status=OrderStatus.NEW, shipping_type="direct",
        )
        self.shipment = Order.objects.create(
            channel=self.channel, customer=self.customer, name="F2", phone="0900000013",
            status=OrderStatus.NEW, shipping_type="shipment",
        )
        self.unknown = Order.objects.create(
            channel=self.channel, customer=self.customer, name="F3", phone="0900000014",
            status=OrderStatus.NEW,
        )

    def _list_ids(self, payload):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post("/api/orders", payload, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        return {row["id"] for row in response.data["results"]}

    def test_filter_direct_only(self):
        self.assertEqual(self._list_ids({"shipping_type": ["direct"]}), {self.direct.id})

    def test_filter_unknown_uses_none_token(self):
        # "none" là quy ước của tầng filter cho IS NULL — chuỗi rỗng đã mang nghĩa
        # "không lọc" ở mọi trục khác nên không tái dùng được.
        self.assertEqual(self._list_ids({"shipping_type": ["none"]}), {self.unknown.id})

    def test_no_filter_returns_all(self):
        ids = self._list_ids({})
        self.assertTrue({self.direct.id, self.shipment.id, self.unknown.id} <= ids)

    def test_filter_combines_direct_and_none(self):
        # Nhánh dễ vỡ nhất của khối Q lồng nhau (repositories.py): concrete
        # ("direct") VÀ "none" cùng có mặt phải là HỢP (direct OR IS NULL), không
        # phải giao — kết quả phải chứa cả đơn direct lẫn đơn chưa xác định, và
        # KHÔNG được lẫn đơn shipment.
        self.assertEqual(
            self._list_ids({"shipping_type": ["direct", "none"]}),
            {self.direct.id, self.unknown.id},
        )

    def test_filter_unknown_value_matches_nothing(self):
        # Giá trị rác không rơi về "không lọc" (khác has_shipping_code/...): nó đi
        # thẳng vào Q(shipping_type__in=["xyz"]) như filter vat/order_type liền kề
        # xử lý giá trị lạ — không đơn nào có shipping_type="xyz" nên khớp rỗng.
        self.assertEqual(self._list_ids({"shipping_type": ["xyz"]}), set())
