from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, UserChannel
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import LeadAssignmentShare, Order, OrderJourneyEvent, OrderStatus
from apps.orders.sheet_rules import STAFF_ASSIGNED_EVENT, sheet_q
from apps.users.models import User
from apps.users.tests.helpers import make_admin, make_user_with_sections


class OrderCreateApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-create-order")
        self.staff_assigned = User.objects.create_user(username="staff-create-order", password="x")
        self.staff_other = User.objects.create_user(username="staff-create-order-other", password="x")
        self.channel = Channel.objects.create(code="lp-create", name="Landing Create")
        self.kiot_channel = KiotVietChannel.objects.create(
            name="New Kiot",
            branch_id="231174",
            branch_ids=["231174"],
            client_id="kv-client",
            client_secret="kv-secret",
            retailer="retailer-a",
        )
        UserChannel.objects.create(user=self.staff_assigned, channel=self.channel, is_active=True)

    def test_admin_can_create_order(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": self.channel.code,
                "name": "Khach Test",
                "phone": "0909 123 456",
                "raw_address": "123 Nguyen Trai, Quan 1, HCM",
                "product": "SKU01 x2",
                "status": OrderStatus.NEW,
                "total_amount": "1290000",
                "shipping_fee": "25000",
                "shipping_code": "GHN-01",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.filter(channel=self.channel).first()
        self.assertIsNotNone(order)
        self.assertEqual(order.name, "Khach Test")
        self.assertEqual(order.phone, "0909123456")
        self.assertEqual(order.raw_address, "123 Nguyen Trai, Quan 1, HCM")
        self.assertEqual(order.shipping_code, "GHN-01")

    def test_admin_create_order_accepts_numeric_branch_id_from_excel_float(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": self.channel.code,
                "name": "Khach Float Branch",
                "phone": "0909 123 457",
                "kiotviet_channel_id": self.kiot_channel.id,
                "branch_id": "231174.0",
                "total_amount": "100000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.filter(name="Khach Float Branch").first()
        self.assertIsNotNone(order)
        self.assertEqual(order.branch_id, "231174")

    def test_create_with_show_orders_false_defaults_order_source_to_landing(self):
        # Landing manual-create button sends show_orders=false alongside
        # order_source="landing". If a stale client / future regression drops
        # order_source from the payload, the order would otherwise be invisible
        # to the landing list (which forces order_source="landing"). Backend
        # defaults to "landing" whenever show_orders=False is the only
        # landing-flow signal present.
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": self.channel.code,
                "name": "Khach Landing Nosource",
                "phone": "0909 123 459",
                "show_orders": False,
                "total_amount": "100000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.filter(name="Khach Landing Nosource").first()
        self.assertIsNotNone(order)
        self.assertEqual(order.order_source, "landing")
        self.assertFalse(order.show_orders)

    def test_create_default_order_source_remains_none_when_show_orders_true(self):
        # Regular /orders create flow keeps show_orders=true (default) and does
        # not specify order_source; fallback must NOT mislabel it as landing.
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": self.channel.code,
                "name": "Khach Manual Nonlanding",
                "phone": "0909 123 460",
                "total_amount": "100000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.filter(name="Khach Manual Nonlanding").first()
        self.assertIsNotNone(order)
        self.assertIsNone(order.order_source)
        self.assertTrue(order.show_orders)

    def _create_zalo_order(self, name, phone, **extra):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": self.channel.code,
                "name": name,
                "phone": phone,
                "order_source": "zalo",
                "total_amount": "100000",
                **extra,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        return Order.objects.get(name=name)

    def test_create_without_address_sticks_to_consulting_sheet(self):
        # Đơn tạo tay chưa có địa chỉ phải vào Tư vấn và ở lại đó kể cả khi sale
        # điền địa chỉ sau (sự cố HD001316 2026-09-24: lọt sang Landing XN đơn).
        order = self._create_zalo_order("Khach Zalo Noaddr", "0909 123 461")
        self.assertFalse(order.has_address)

        order.raw_address = "122 Nguyen Van Hoi, Hai An, Hai Phong"
        order.save(update_fields=["raw_address"])
        self.assertTrue(Order.objects.filter(sheet_q("sheet3"), pk=order.pk).exists())
        self.assertFalse(Order.objects.filter(sheet_q("sheet1"), pk=order.pk).exists())

    def test_create_with_address_goes_to_confirm_sheet(self):
        order = self._create_zalo_order(
            "Khach Zalo Addr", "0909 123 462", raw_address="123 Nguyen Trai, Quan 1, HCM"
        )
        self.assertTrue(order.has_address)
        self.assertTrue(Order.objects.filter(sheet_q("sheet1"), pk=order.pk).exists())

    def test_admin_create_order_accepts_branch_pool_item_with_braces(self):
        self.kiot_channel.branch_ids = ["{231174}"]
        self.kiot_channel.save(update_fields=["branch_ids", "updated_at"])

        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": self.channel.code,
                "name": "Khach Braces Branch",
                "phone": "0909 123 458",
                "kiotviet_channel_id": self.kiot_channel.id,
                "branch_id": "231174",
                "total_amount": "100000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)

    def test_create_order_persists_fulfillment_warehouse(self):
        # Form tạo đơn có ô "Kho xuất"; trước fix serializer không khai báo field
        # nên DRF nuốt im lặng → đơn tạo ra luôn null kho, và bước chuyển sang
        # "Đã xuất kho" (đường xuất kho tay của đơn bán trực tiếp, không quét)
        # bị chặn với lỗi "Hãy chọn kho xuất trước...".
        from apps.warehouses.models import Warehouse

        warehouse = Warehouse.objects.create(code="KHO-CREATE", name="Kho Create")
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": self.channel.code,
                "name": "Khach Co Kho",
                "phone": "0909 123 463",
                "total_amount": "100000",
                "fulfillment_warehouse": warehouse.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        order = Order.objects.get(id=response.data["id"])
        self.assertEqual(order.fulfillment_warehouse_id, warehouse.id)
        self.assertEqual(response.data["fulfillment_warehouse"], warehouse.id)

    def test_create_order_without_fulfillment_warehouse_stays_null(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": self.channel.code,
                "name": "Khach Khong Kho",
                "phone": "0909 123 464",
                "total_amount": "100000",
                "fulfillment_warehouse": None,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        order = Order.objects.get(id=response.data["id"])
        self.assertIsNone(order.fulfillment_warehouse_id)

    def test_create_order_rejects_deleted_fulfillment_warehouse(self):
        from django.utils import timezone

        from apps.warehouses.models import Warehouse

        warehouse = Warehouse.objects.create(
            code="KHO-DEL", name="Kho Da Xoa", deleted_at=timezone.now()
        )
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": self.channel.code,
                "name": "Khach Kho Xoa",
                "phone": "0909 123 465",
                "total_amount": "100000",
                "fulfillment_warehouse": warehouse.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)

    def _create_koc_channel(self):
        from django.conf import settings
        from django.db import connection
        from apps.channels_core.models import UserChannel

        koc = Channel.objects.create(
            id=settings.KOC_CHANNEL_ID, code="koc", name="hang mau koc"
        )
        # Explicit PK insert doesn't advance the id sequence. Bump it past
        # KOC_CHANNEL_ID so later auto-assigned Channel ids in this test run
        # never collide with the hard-coded koc id (flaky, order-dependent
        # failure otherwise: an unrelated channel accidentally matches
        # Channel.is_koc by id).
        table = Channel._meta.db_table
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"GREATEST((SELECT MAX(id) FROM {table}), %s))",
                [settings.KOC_CHANNEL_ID],
            )
        UserChannel.objects.create(user=self.admin, channel=koc, is_active=True)
        return koc

    def test_koc_new_customer_uses_koc_prefix(self):
        koc = self._create_koc_channel()
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": koc.code,
                "name": "KOC New Cust",
                "phone": "0911 222 333",
                "total_amount": "100000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.filter(name="Koc New Cust").first()
        self.assertIsNotNone(order)
        self.assertTrue(order.customer.code.startswith("KOC"), order.customer.code)

    def test_non_koc_new_customer_uses_kh_prefix(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": self.channel.code,
                "name": "Normal New Cust",
                "phone": "0911 222 444",
                "total_amount": "100000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.filter(name="Normal New Cust").first()
        self.assertTrue(order.customer.code.startswith("KH"), order.customer.code)

    def test_koc_existing_customer_keeps_old_code(self):
        from apps.customers.models import Customer

        existing = Customer.objects.create(
            full_name="Old Cust", phone_raw="0911555666", phone_normalized="0911555666"
        )
        old_code = existing.code  # KH<id>
        koc = self._create_koc_channel()
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": koc.code,
                "name": "Old Cust",
                "phone": "0911 555 666",
                "total_amount": "100000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        existing.refresh_from_db()
        self.assertEqual(existing.code, old_code)
        self.assertTrue(old_code.startswith("KH"))

    def test_koc_no_return_order_is_marked_fully_paid(self):
        koc = self._create_koc_channel()
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": koc.code,
                "name": "KOC No Return",
                "phone": "0909 000 111",
                "order_type": "no_return",
                "total_amount": "500000",
                "total_after_discount": "500000",
                "discount_amount": "0",
                "customer_prepaid": "0",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        # name is normalized (each word capitalized) on create.
        order = Order.objects.filter(name="Koc No Return").first()
        self.assertIsNotNone(order)
        self.assertEqual(order.customer_prepaid, order.total_after_discount)
        self.assertEqual(int(order.customer_prepaid), 500000)

    def test_koc_has_return_order_keeps_prepaid_untouched(self):
        koc = self._create_koc_channel()
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": koc.code,
                "name": "KOC Has Return",
                "phone": "0909 000 222",
                "order_type": "has_return",
                "total_amount": "500000",
                "customer_prepaid": "0",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.filter(name="Koc Has Return").first()
        self.assertEqual(int(order.customer_prepaid), 0)

    def test_non_koc_no_return_order_is_not_overridden(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": self.channel.code,
                "name": "Normal Channel No Return",
                "phone": "0909 000 333",
                "order_type": "no_return",
                "total_amount": "500000",
                "customer_prepaid": "0",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.filter(name="Normal Channel No Return").first()
        self.assertEqual(int(order.customer_prepaid), 0)

    def test_koc_order_source_is_koc(self):
        koc = self._create_koc_channel()
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": koc.code,
                "name": "KOC Source",
                "phone": "0909 000 444",
                "order_source": "zalo",
                "total_amount": "100000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.filter(name="Koc Source").first()
        self.assertIsNotNone(order)
        self.assertEqual(order.order_source, "koc")

    def test_non_koc_order_source_is_not_forced(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": self.channel.code,
                "name": "Normal Source",
                "phone": "0909 000 555",
                "order_source": "zalo",
                "total_amount": "100000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.filter(name="Normal Source").first()
        self.assertEqual(order.order_source, "zalo")

    def test_create_order_with_order_type_and_recall_code(self):
        payload = {
            "channel_code": self.channel.code,
            "name": "Khach Test",
            "phone": "0909 123 461",
            "raw_address": "123 Nguyen Trai, Quan 1, HCM",
            "product": "SKU01 x2",
            "status": OrderStatus.NEW,
            "total_amount": "1290000",
            "shipping_fee": "25000",
            "shipping_code": "GHN-01",
        }
        payload.update(
            {
                "order_type": "no_return",
                "recall_code": "RC-OUT-001",
            }
        )
        self.client.force_authenticate(self.admin)
        resp = self.client.post("/api/orders/create", payload, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        order = Order.objects.get(id=resp.data["id"])
        self.assertEqual(order.order_type, "no_return")
        # recall_code đã tách sang OrderRecallEntry (Task 1/2 ruling): luồng tạo đơn
        # nhận MỘT mã và tạo đúng MỘT nhóm, không còn ghi lên OrderRecall.
        entries = list(order.recall_entries.all())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].recall_code, "RC-OUT-001")

    def test_create_order_without_recall_code_has_no_recall(self):
        payload = {
            "channel_code": self.channel.code,
            "name": "Khach Test",
            "phone": "0909 123 462",
            "raw_address": "123 Nguyen Trai, Quan 1, HCM",
            "product": "SKU01 x2",
            "status": OrderStatus.NEW,
            "total_amount": "1290000",
            "shipping_fee": "25000",
            "shipping_code": "GHN-01",
        }
        self.client.force_authenticate(self.admin)
        resp = self.client.post("/api/orders/create", payload, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        order = Order.objects.get(id=resp.data["id"])
        self.assertFalse(hasattr(order, "recall") and order.recall is not None)
        self.assertEqual(order.recall_entries.count(), 0)


class OrderCreateAutoAssignTests(TestCase):
    """Checkbox "Chia đơn tự động" trên form tạo đơn.

    Dùng CHUNG bộ chia với luồng landing-ingest (``LeadAssignmentService``) — chỉ
    khác điểm kích hoạt: người tạo đơn tự bật cờ thay vì webhook bật hộ.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-auto-assign")
        self.sale = User.objects.create_user(username="sale-auto-assign", password="x")
        self.channel = Channel.objects.create(code="auto-assign", name="Auto Assign")

    def _payload(self, **overrides):
        payload = {
            "channel_code": self.channel.code,
            "name": "Khach Auto",
            "phone": "0912 000 001",
            "raw_address": "12 Le Loi, Quan 1, HCM",
            "order_source": "landing",
            "status": OrderStatus.NEW,
            "total_amount": "100000",
        }
        payload.update(overrides)
        return payload

    def _share(self, sheet="sheet1", weight=1, user=None):
        return LeadAssignmentShare.objects.create(
            date=timezone.localdate(),
            shift="morning",
            sheet=sheet,
            user=user or self.sale,
            weight=weight,
        )

    @patch(
        "apps.orders.application.services.lead_assignment_service.ShiftService.current_shift_and_date",
        side_effect=lambda: ("morning", timezone.localdate()),
    )
    def test_auto_assign_uses_configured_shares(self, _shift):
        self._share()
        self.client.force_authenticate(user=self.admin)

        response = self.client.post(
            "/api/orders/create", self._payload(auto_assign_staff=True), format="json"
        )

        self.assertEqual(response.status_code, 201, response.data)
        order = Order.objects.get(id=response.data["id"])
        self.assertEqual(order.staff_id, self.sale.id)
        # Bộ đếm largest-remainder phải tăng, nếu không đơn kế tiếp chia lệch người.
        share = LeadAssignmentShare.objects.get(user=self.sale, sheet="sheet1")
        self.assertEqual(share.assigned_count, 1)

    @patch(
        "apps.orders.application.services.lead_assignment_service.ShiftService.current_shift_and_date",
        side_effect=lambda: ("morning", timezone.localdate()),
    )
    def test_auto_assign_response_carries_assigned_staff(self, _shift):
        # FE đọc staff_id trong response để biết chia được hay chưa (quyết định cảnh báo).
        self._share()
        self.client.force_authenticate(user=self.admin)

        response = self.client.post(
            "/api/orders/create", self._payload(auto_assign_staff=True), format="json"
        )

        self.assertEqual(response.data["staff_id"], self.sale.id)

    @patch(
        "apps.orders.application.services.lead_assignment_service.ShiftService.current_shift_and_date",
        side_effect=lambda: ("morning", timezone.localdate()),
    )
    def test_auto_assign_logs_journey_with_creator_as_actor(self, _shift):
        # Bộ chia nền ghi actor=None; đường form phải ghi ĐÚNG người bấm, nếu không
        # không truy được ai đã đẩy đơn sang tay người khác qua cờ này.
        self._share()
        self.client.force_authenticate(user=self.admin)

        response = self.client.post(
            "/api/orders/create", self._payload(auto_assign_staff=True), format="json"
        )

        event = OrderJourneyEvent.objects.get(
            order_id=response.data["id"], event_type=STAFF_ASSIGNED_EVENT
        )
        self.assertEqual(event.actor_id, self.admin.id)
        self.assertEqual(event.to_code, str(self.sale.id))

    @patch(
        "apps.orders.application.services.lead_assignment_service.ShiftService.current_shift_and_date",
        side_effect=lambda: ("morning", timezone.localdate()),
    )
    def test_flag_off_keeps_staff_empty(self, _shift):
        # Hành vi cũ giữ nguyên: không bật cờ thì tạo đơn KHÔNG đụng bộ chia.
        self._share()
        self.client.force_authenticate(user=self.admin)

        response = self.client.post("/api/orders/create", self._payload(), format="json")

        self.assertEqual(response.status_code, 201, response.data)
        order = Order.objects.get(id=response.data["id"])
        self.assertIsNone(order.staff_id)

    @patch(
        "apps.orders.application.services.lead_assignment_service.ShiftService.current_shift_and_date",
        side_effect=lambda: ("morning", timezone.localdate()),
    )
    def test_explicit_staff_wins_over_auto_assign(self, _shift):
        # Cờ bật nhưng người tạo đã chỉ đích danh sale → tôn trọng lựa chọn tay.
        self._share()
        picked = User.objects.create_user(username="sale-picked", password="x")
        self.client.force_authenticate(user=self.admin)

        response = self.client.post(
            "/api/orders/create",
            self._payload(auto_assign_staff=True, staff_id=picked.id),
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        order = Order.objects.get(id=response.data["id"])
        self.assertEqual(order.staff_id, picked.id)

    @patch(
        "apps.orders.application.services.lead_assignment_service.ShiftService.current_shift_and_date",
        side_effect=lambda: ("morning", timezone.localdate()),
    )
    def test_auto_assign_without_sheet_creates_order_with_empty_staff(self, _shift):
        # Đơn không thuộc sheet1/2/3 (nguồn trống) → không có bảng trọng số để tra.
        # Quyết định nghiệp vụ 17/08: VẪN tạo đơn, để trống sale; FE tự cảnh báo.
        self._share()
        self.client.force_authenticate(user=self.admin)

        response = self.client.post(
            "/api/orders/create",
            self._payload(auto_assign_staff=True, order_source=None, phone="0912 000 002"),
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        order = Order.objects.get(id=response.data["id"])
        self.assertIsNone(order.staff_id)

    @patch(
        "apps.orders.application.services.lead_assignment_service.ShiftService.current_shift_and_date",
        side_effect=lambda: (None, timezone.localdate()),
    )
    def test_auto_assign_outside_working_hours_creates_order_with_empty_staff(self, _shift):
        # Ngoài giờ làm việc: giữ nguyên hành vi bộ chia (bỏ qua), sweep vớt sau.
        self._share()
        self.client.force_authenticate(user=self.admin)

        response = self.client.post(
            "/api/orders/create", self._payload(auto_assign_staff=True), format="json"
        )

        self.assertEqual(response.status_code, 201, response.data)
        order = Order.objects.get(id=response.data["id"])
        self.assertIsNone(order.staff_id)

    @patch(
        "apps.orders.application.services.lead_assignment_service.ShiftService.current_shift_and_date",
        side_effect=lambda: ("morning", timezone.localdate()),
    )
    def test_auto_assign_without_config_creates_order_with_empty_staff(self, _shift):
        # Ca chưa ai nhập trọng số → không chia, không lỗi.
        self.client.force_authenticate(user=self.admin)

        response = self.client.post(
            "/api/orders/create", self._payload(auto_assign_staff=True), format="json"
        )

        self.assertEqual(response.status_code, 201, response.data)
        order = Order.objects.get(id=response.data["id"])
        self.assertIsNone(order.staff_id)

    @patch(
        "apps.orders.application.services.lead_assignment_service.ShiftService.current_shift_and_date",
        side_effect=lambda: ("morning", timezone.localdate()),
    )
    def test_auto_assign_allowed_without_sale_assignment_permission(self, _shift):
        # Người thiếu quyền `sale-assignment:edit` bình thường bị ép staff=chính mình.
        # Bật cờ chia tự động thì để trống cho bộ chia — quyết định sản phẩm 17/08.
        self._share()
        sale_no_perm = make_user_with_sections("sale-auto-no-perm", ["orders"])
        UserChannel.objects.create(user=sale_no_perm, channel=self.channel, is_active=True)
        self.client.force_authenticate(user=sale_no_perm)

        response = self.client.post(
            "/api/orders/create", self._payload(auto_assign_staff=True), format="json"
        )

        self.assertEqual(response.status_code, 201, response.data)
        order = Order.objects.get(id=response.data["id"])
        self.assertEqual(order.staff_id, self.sale.id)

    @patch(
        "apps.orders.application.services.lead_assignment_service.ShiftService.current_shift_and_date",
        side_effect=lambda: ("morning", timezone.localdate()),
    )
    def test_named_staff_still_forbidden_without_permission_even_with_flag(self, _shift):
        # Cờ chia tự động KHÔNG được biến thành đường chỉ định sale trá hình.
        self._share()
        sale_no_perm = make_user_with_sections("sale-auto-no-perm-2", ["orders"])
        UserChannel.objects.create(user=sale_no_perm, channel=self.channel, is_active=True)
        self.client.force_authenticate(user=sale_no_perm)

        response = self.client.post(
            "/api/orders/create",
            self._payload(auto_assign_staff=True, staff_id=self.sale.id),
            format="json",
        )

        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(Order.objects.filter(phone="0912000001").exists())
