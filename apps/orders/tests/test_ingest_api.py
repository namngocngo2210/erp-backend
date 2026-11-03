import json
from decimal import Decimal

from django.db import OperationalError
from django.test import SimpleTestCase, TestCase
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from types import SimpleNamespace
from unittest.mock import patch
from rest_framework.test import APIClient

from apps.customers.models import Customer
from apps.orders.application.services.lead_assignment_service import LeadAssignmentService
from apps.orders.application.services.order_ingest_service import resolve_ingest_staff_id
from apps.orders.application.services.shift_service import ShiftService
from apps.orders.import_landing_excel import build_landing_payload_from_fields
from apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks import process_landing_ingest
from apps.orders.models import LandingIngestTrace, LandingIngestTraceStatus, Order, OrderJourneyEvent
from apps.catalog.models import Product
from apps.orders.serializers import LandingOrderSerializer
from apps.orders.application.services.order_service_impl import OrderService
from apps.orders.sheet_rules import STAFF_ASSIGNED_EVENT, STAFF_INGEST_DESIGNATED_EVENT
from apps.orders.tests.helpers import ingest_headers
from apps.channels_core.models import Channel
from apps.users.models import User
from apps.users.tests.helpers import make_admin


class LandingIngestApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(
            code="facebook-ads-team", name="Facebook Ads Team", owner="Facebook Ads Team"
        )

    def test_ingest_accepts_new_payload_fields(self):
        Product.objects.create(name="Bồn ngâm chân", sku="BNC02", price="1290000")
        payload = {
            "channel_id": "Facebook Ads Team",
            "name": "Nguyen Van A",
            "phone": "0909 123 456",
            "address": "1 Nguyen Trai, Q1",
            "product": "Mua 1 Bồn Ngâm Chân: 1.290.000 VNĐ Tặng Kèm 30 Gói Thảo Dược + Ổ Cắm Chống Giật",
            "product_id": "BNC02",
            "link": "https://example.com/landing/san-pham-a",
        }

        headers = ingest_headers()
        def _run_task(_task_name, **kwargs):
            process_landing_ingest(payload=kwargs["payload"], trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        with patch(
            "apps.orders.application.services.order_ingest_service.dispatch",
            side_effect=_run_task,
        ):
            response = self.client.post("/api/orders/ingest", data=payload, format="json", **headers)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["accepted"])
        self.assertIn("task_id", response.data)
        order = Order.objects.latest("id")
        self.assertEqual(order.name, payload["name"])
        self.assertEqual(order.phone, "0909123456")  # serializer normalizes phone (strips spaces)
        self.assertEqual(order.raw_address, payload["address"])
        self.assertTrue(order.address)
        self.assertEqual(order.product_raw, payload["product"])  # product_raw stores the original free-text
        self.assertEqual(order.sku, "BNC02")
        self.assertEqual(order.quantity, 1)
        self.assertEqual(int(order.total_amount), 1290000)
        self.assertEqual(order.channel_owner, "Facebook Ads Team")
        self.assertEqual(order.url_page, payload["link"])
        self.assertEqual(order.customer.phone_raw, "0909123456")  # stored normalized by serializer
        self.assertEqual(order.customer.full_name, payload["name"])
        self.assertEqual(order.channel_owner, order.channel.name)

    def test_ingest_treats_no_address_placeholder_as_empty(self):
        """Địa chỉ là placeholder 'Không có địa chỉ giao hàng' phải lưu rỗng để đơn
        rơi vào tab tư vấn (không có địa chỉ), không bị tính là đủ thông tin."""
        cases = [
            ("0909123451", "Không có địa chỉ giao hàng"),
            ("0909123452", "  KHÔNG CÓ ĐỊA CHỈ  "),
            ("0909123453", "N/A"),
            ("0909123454", "Tư vấn"),
            ("0909123455", "  TƯ VẤN  "),
        ]
        for phone, placeholder in cases:
            with self.subTest(placeholder=placeholder):
                payload = {
                    "channel_id": "Facebook Ads Team",
                    "phone": phone,
                    "address": placeholder,
                }
                headers = ingest_headers()

                def _run_task(_task_name, **kwargs):
                    process_landing_ingest(payload=kwargs["payload"], trace_id=kwargs["trace_id"])
                    return SimpleNamespace(id="test-job")

                with patch(
                    "apps.orders.application.services.order_ingest_service.dispatch",
                    side_effect=_run_task,
                ):
                    response = self.client.post(
                        "/api/orders/ingest", data=payload, format="json", **headers
                    )

                self.assertEqual(response.status_code, 200)
                order = Order.objects.latest("id")
                self.assertEqual(order.raw_address, "")
                self.assertEqual(order.address, "")

    def test_ingest_persists_explicit_order_source(self):
        """Đơn đổ về với order_source != landing phải giữ nguyên, không bị ép về landing."""
        payload = {
            "channel_id": "Facebook Ads Team",
            "phone": "0909123456",
            "order_source": "zalo",
        }
        headers = ingest_headers()

        def _run_task(_task_name, **kwargs):
            process_landing_ingest(payload=kwargs["payload"], trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        with patch(
            "apps.orders.application.services.order_ingest_service.dispatch",
            side_effect=_run_task,
        ):
            response = self.client.post("/api/orders/ingest", data=payload, format="json", **headers)

        self.assertEqual(response.status_code, 200)
        order = Order.objects.latest("id")
        self.assertEqual(order.order_source, "zalo")

    def test_ingest_order_is_hidden_from_regular_orders_list(self):
        """Đơn đổ qua API ingest luôn thuộc trang Landing → show_orders=False, không
        được lọt vào trang đơn hàng thường (/orders chỉ hiện show_orders=True).
        Áp dụng cho mọi nguồn, kể cả messenger."""
        payload = {
            "channel_id": "Facebook Ads Team",
            "phone": "0909123456",
            "order_source": "messenger",
        }
        headers = ingest_headers()

        def _run_task(_task_name, **kwargs):
            process_landing_ingest(payload=kwargs["payload"], trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        with patch(
            "apps.orders.application.services.order_ingest_service.dispatch",
            side_effect=_run_task,
        ):
            response = self.client.post("/api/orders/ingest", data=payload, format="json", **headers)

        self.assertEqual(response.status_code, 200)
        order = Order.objects.latest("id")
        self.assertEqual(order.order_source, "messenger")
        self.assertFalse(
            order.show_orders,
            "Ingested messenger order leaked into /orders (show_orders=True)",
        )

    def test_ingest_defaults_order_source_to_landing_when_absent(self):
        payload = {"channel_id": "Facebook Ads Team", "phone": "0909123456"}
        headers = ingest_headers()

        def _run_task(_task_name, **kwargs):
            process_landing_ingest(payload=kwargs["payload"], trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        with patch(
            "apps.orders.application.services.order_ingest_service.dispatch",
            side_effect=_run_task,
        ):
            response = self.client.post("/api/orders/ingest", data=payload, format="json", **headers)

        self.assertEqual(response.status_code, 200)
        order = Order.objects.latest("id")
        self.assertEqual(order.order_source, "landing")


class LandingIngestCrossChannelDuplicateTests(TestCase):
    """Trùng chéo kênh: cùng phone + cùng product ở 2 kênh khác nhau -> 409;
    cùng phone nhưng khác product -> chấp nhận."""

    def setUp(self):
        self.client = APIClient()
        self.channel_a = Channel.objects.create(
            code="facebook-a", name="Facebook A", owner="FB A"
        )
        self.channel_b = Channel.objects.create(
            code="facebook-b", name="Facebook B", owner="FB B"
        )
        self.product_1 = Product.objects.create(name="Bồn ngâm chân", sku="BNC02", price="1290000")
        self.product_2 = Product.objects.create(name="Máy massage", sku="MSG01", price="990000")

    def _post(self, payload):
        headers = ingest_headers()

        def _run_task(_task_name, **kwargs):
            process_landing_ingest(payload=kwargs["payload"], trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        with patch(
            "apps.orders.application.services.order_ingest_service.dispatch",
            side_effect=_run_task,
        ):
            return self.client.post("/api/orders/ingest", data=payload, format="json", **headers)

    def test_same_phone_same_product_other_channel_returns_409(self):
        phone = "0909123460"
        first = self._post({
            "channel_id": "Facebook A", "phone": phone, "address": "1 Q1",
            "product_id": "BNC02",
        })
        self.assertEqual(first.status_code, 200, first.data)

        second = self._post({
            "channel_id": "Facebook B", "phone": phone, "address": "1 Q1",
            "product_id": "BNC02",
        })
        self.assertEqual(second.status_code, 409, second.data)
        self.assertEqual(second.data["action"], "reject_duplicate")
        # Báo đúng kênh đã giữ đơn trước đó.
        self.assertEqual(second.data["existing_channel_code"], "facebook-a")
        # Chỉ tạo đúng 1 đơn.
        self.assertEqual(Order.objects.filter(phone=phone).count(), 1)

    def test_same_phone_different_product_other_channel_is_accepted(self):
        phone = "0909123461"
        first = self._post({
            "channel_id": "Facebook A", "phone": phone, "address": "1 Q1",
            "product_id": "BNC02",
        })
        self.assertEqual(first.status_code, 200, first.data)

        second = self._post({
            "channel_id": "Facebook B", "phone": phone, "address": "1 Q1",
            "product_id": "MSG01",
        })
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(Order.objects.filter(phone=phone).count(), 2)


class BuildLandingPayloadOrderSourceTests(SimpleTestCase):
    BASE_FIELDS = {"channel_code": "Facebook", "phone": "0909123456"}

    def test_defaults_to_landing_when_absent(self):
        payload = build_landing_payload_from_fields(dict(self.BASE_FIELDS))
        self.assertEqual(payload["order_source"], "landing")

    def test_reads_order_source_from_fields(self):
        for value in ("zalo", "messenger", "kol", "hotline"):
            with self.subTest(value=value):
                payload = build_landing_payload_from_fields(
                    {**self.BASE_FIELDS, "order_source": value.upper()}
                )
                self.assertEqual(payload["order_source"], value)

    def test_blank_or_invalid_order_source_falls_back_to_landing(self):
        for value in ("  ", "facebook"):
            with self.subTest(value=value):
                payload = build_landing_payload_from_fields(
                    {**self.BASE_FIELDS, "order_source": value}
                )
                self.assertEqual(payload["order_source"], "landing")


class OrderSourceNormalizeTests(SimpleTestCase):
    def test_normalize_trims_and_lowercases(self):
        from apps.orders.models import OrderSource

        self.assertEqual(OrderSource.normalize("  HOTLINE "), "hotline")
        self.assertEqual(OrderSource.normalize("Zalo"), "zalo")

    def test_normalize_returns_default_for_empty_or_invalid(self):
        from apps.orders.models import OrderSource

        self.assertIsNone(OrderSource.normalize(""))
        self.assertIsNone(OrderSource.normalize(None))
        self.assertIsNone(OrderSource.normalize("facebook"))
        self.assertEqual(OrderSource.normalize("", default="landing"), "landing")
        self.assertEqual(OrderSource.normalize("xxx", default="landing"), "landing")


class LandingOrderSerializerOrderSourceTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="facebook-thanh", name="Facebook Thành")

    def _validated(self, **extra):
        serializer = LandingOrderSerializer(
            data={"channel_id": "Facebook Thành", "phone": "0909123456", **extra}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        return serializer.validated_data

    def test_defaults_to_landing_when_absent(self):
        self.assertEqual(self._validated()["order_source"], "landing")

    def test_reads_and_normalizes_explicit_source(self):
        self.assertEqual(self._validated(order_source="HOTLINE")["order_source"], "hotline")

    def test_invalid_source_falls_back_to_landing(self):
        # "tiktok" từng là ví dụ nguồn không hợp lệ, nay đã thành nguồn đơn sàn thật.
        self.assertEqual(self._validated(order_source="not-a-source")["order_source"], "landing")


class LandingOrderSerializerFieldTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="facebook-thanh", name="Facebook Thành")

    def test_accepts_text_channel_and_new_fields(self):
        serializer = LandingOrderSerializer(data={
            "channel_id": "Facebook Thành",
            "phone": "0909123456",
            "quantity": 3,
            "total_amount": "350000",
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        data = serializer.validated_data
        self.assertEqual(data["channel_id"], "Facebook Thành")
        self.assertEqual(data["quantity"], 3)
        self.assertEqual(data["total_amount"], Decimal("350000"))

    def test_accepts_int_channel_backward_compat(self):
        serializer = LandingOrderSerializer(data={"channel_id": self.channel.id, "phone": "0909123456"})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["channel_id"], self.channel.id)

    def test_rejects_empty_channel(self):
        serializer = LandingOrderSerializer(data={"channel_id": "  ", "phone": "0909123456"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("channel_id", serializer.errors)

    def test_accepts_discount_and_total_after_discount(self):
        serializer = LandingOrderSerializer(data={
            "channel_id": "Facebook Thành",
            "phone": "0909123456",
            "total_amount": "1000000",
            "discount_amount": "300000",
            "total_after_discount": "900000",
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        data = serializer.validated_data
        self.assertEqual(data["discount_amount"], Decimal("300000"))
        # Lệch với total - discount là CỐ Ý: bên gửi chốt số, CRM không sửa.
        self.assertEqual(data["total_after_discount"], Decimal("900000"))

    def test_rejects_discount_greater_than_total_when_no_total_after_discount(self):
        serializer = LandingOrderSerializer(data={
            "channel_id": "Facebook Thành",
            "phone": "0909123456",
            "total_amount": "100000",
            "discount_amount": "150000",
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn("discount_amount", serializer.errors)

    def test_allows_discount_greater_than_total_when_total_after_discount_given(self):
        serializer = LandingOrderSerializer(data={
            "channel_id": "Facebook Thành",
            "phone": "0909123456",
            "total_amount": "100000",
            "discount_amount": "150000",
            "total_after_discount": "0",
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_rejects_negative_discount(self):
        serializer = LandingOrderSerializer(data={
            "channel_id": "Facebook Thành", "phone": "0909123456", "discount_amount": "-1",
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn("discount_amount", serializer.errors)


class LandingIngestChannelResolveTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(
            code="facebook-thanh", name="Facebook Thành", owner="Facebook Thành"
        )

    def _post(self, payload):
        headers = ingest_headers()

        def _run_task(_task_name, **kwargs):
            process_landing_ingest(payload=kwargs["payload"], trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        with patch(
            "apps.orders.application.services.order_ingest_service.dispatch",
            side_effect=_run_task,
        ):
            return self.client.post("/api/orders/ingest", data=payload, format="json", **headers)

    def test_resolves_text_channel(self):
        resp = self._post({"channel_id": "Facebook Thành", "phone": "0909123456", "address": "1 Q1"})
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.latest("id")
        self.assertEqual(order.channel_id, self.channel.id)

    def test_resolves_int_channel_backward_compat(self):
        resp = self._post({"channel_id": self.channel.id, "phone": "0909123457", "address": "1 Q1"})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Order.objects.latest("id").channel_id, self.channel.id)

    def test_rejects_unmappable_channel(self):
        before = Order.objects.count()
        resp = self._post({"channel_id": "Kênh Không Tồn Tại", "phone": "0909123458", "address": "1 Q1"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Order.objects.count(), before)
        self.assertIn("Kênh bán không tồn tại", resp.data["detail"])


class LandingIngestQuantityTotalTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(code="facebook-thanh", name="Facebook Thành", owner="FB")
        self.product = Product.objects.create(name="Bồn ngâm chân", sku="BNC02", price="1290000")

    def _post(self, payload):
        headers = ingest_headers()

        def _run_task(_task_name, **kwargs):
            process_landing_ingest(payload=kwargs["payload"], trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        with patch(
            "apps.orders.application.services.order_ingest_service.dispatch",
            side_effect=_run_task,
        ):
            return self.client.post("/api/orders/ingest", data=payload, format="json", **headers)

    def test_explicit_quantity_drives_item_qty(self):
        resp = self._post({
            "channel_id": "Facebook Thành", "phone": "0909123456", "address": "1 Q1",
            "product_id": "BNC02", "quantity": 4,
        })
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.latest("id")
        self.assertEqual(order.quantity, 4)
        item = order.items.first()
        self.assertIsNotNone(item)
        self.assertEqual(item.quantity, 4)

    def test_explicit_total_amount_overrides_item_sum(self):
        resp = self._post({
            "channel_id": "Facebook Thành", "phone": "0909123457", "address": "1 Q1",
            "product_id": "BNC02", "quantity": 4, "total_amount": "999000",
        })
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.latest("id")
        self.assertEqual(int(order.total_amount), 999000)
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(int(order.total_after_discount), 999000)

    def test_total_amount_without_item_match(self):
        resp = self._post({
            "channel_id": "Facebook Thành", "phone": "0909123458", "address": "1 Q1",
            "product_id": "SKU-KHONG-CO", "total_amount": "777000",
        })
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.latest("id")
        self.assertEqual(int(order.total_amount), 777000)
        self.assertEqual(order.items.count(), 0)
        self.assertEqual(order.quantity, 0)

    def test_total_amount_survives_json_payload_roundtrip(self):
        # Production path: payload is JSON-encoded (DjangoJSONEncoder turns Decimal
        # into a string) before the Go worker hands it back to the task. Reproduce
        # that round-trip so total_amount arrives as str, not Decimal.
        headers = ingest_headers()

        def _run_task(_task_name, **kwargs):
            roundtripped = json.loads(json.dumps(kwargs["payload"], cls=DjangoJSONEncoder))
            process_landing_ingest(payload=roundtripped, trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        with patch(
            "apps.orders.application.services.order_ingest_service.dispatch",
            side_effect=_run_task,
        ):
            resp = self.client.post(
                "/api/orders/ingest",
                data={
                    "channel_id": "Facebook Thành", "phone": "0909123459", "address": "1 Q1",
                    "product_id": "BNC02", "quantity": 4, "total_amount": "999000",
                },
                format="json", **headers,
            )
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.latest("id")
        self.assertEqual(int(order.total_amount), 999000)
        self.assertEqual(order.items.count(), 1)


class LandingIngestExplicitAmountsTests(TestCase):
    """Bên gửi chốt tiền trong payload: total_amount / discount_amount /
    total_after_discount. Gửi gì áp nấy, thiếu thì tính bù; đủ bộ thì áp nguyên
    văn kể cả khi lệch với dòng hàng (spec 2026-08-28)."""

    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(code="facebook-thanh", name="Facebook Thành", owner="FB")
        self.product = Product.objects.create(name="Bồn ngâm chân", sku="BNC02", price="1290000")

    def _post(self, payload, *, roundtrip=False):
        def _run_task(_task_name, **kwargs):
            task_payload = kwargs["payload"]
            if roundtrip:
                # Đường prod: payload đi qua JSON (Decimal -> str) trước khi worker
                # trả lại cho task.
                task_payload = json.loads(json.dumps(task_payload, cls=DjangoJSONEncoder))
            process_landing_ingest(payload=task_payload, trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        with patch(
            "apps.orders.application.services.order_ingest_service.dispatch",
            side_effect=_run_task,
        ):
            return self.client.post(
                "/api/orders/ingest", data=payload, format="json", **ingest_headers()
            )

    def test_explicit_triple_applied_verbatim_ignoring_items(self):
        resp = self._post({
            "channel_id": "Facebook Thành", "phone": "0909123460", "address": "1 Q1",
            "product_id": "BNC02",
            "total_amount": "1000000",
            "discount_amount": "300000",
            "total_after_discount": "900000",
        })
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.latest("id")
        self.assertEqual(int(order.total_amount), 1000000)
        self.assertEqual(int(order.discount_amount), 300000)
        # 900k chứ không phải 700k: số bên gửi thắng, KHÔNG tính lại.
        self.assertEqual(int(order.total_after_discount), 900000)
        # Dòng hàng vẫn tạo theo bảng giá để kho trừ tồn được, chấp nhận lệch.
        item = order.items.first()
        self.assertIsNotNone(item)
        self.assertEqual(int(item.line_total), 1290000)

    def test_explicit_triple_survives_json_payload_roundtrip(self):
        resp = self._post({
            "channel_id": "Facebook Thành", "phone": "0909123461", "address": "1 Q1",
            "product_id": "BNC02",
            "total_amount": "1000000",
            "discount_amount": "300000",
            "total_after_discount": "900000",
        }, roundtrip=True)
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.latest("id")
        self.assertEqual(int(order.total_amount), 1000000)
        self.assertEqual(int(order.discount_amount), 300000)
        self.assertEqual(int(order.total_after_discount), 900000)

    def test_discount_without_total_after_discount_is_subtracted(self):
        resp = self._post({
            "channel_id": "Facebook Thành", "phone": "0909123462", "address": "1 Q1",
            "product_id": "BNC02",
            "total_amount": "1000000",
            "discount_amount": "200000",
        })
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.latest("id")
        self.assertEqual(int(order.total_amount), 1000000)
        self.assertEqual(int(order.total_after_discount), 800000)
        # Snapshot bất biến giữ nghĩa cũ: tổng TRƯỚC giảm giá.
        self.assertEqual(int(order.landing_confirmed_amount), 1000000)

    def test_discount_applied_when_no_product_match(self):
        resp = self._post({
            "channel_id": "Facebook Thành", "phone": "0909123463", "address": "1 Q1",
            "product_id": "SKU-KHONG-CO",
            "total_amount": "777000",
            "discount_amount": "77000",
        })
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.latest("id")
        self.assertEqual(order.items.count(), 0)
        self.assertEqual(int(order.total_amount), 777000)
        self.assertEqual(int(order.total_after_discount), 700000)

    def test_total_after_discount_alone_is_applied(self):
        resp = self._post({
            "channel_id": "Facebook Thành", "phone": "0909123464", "address": "1 Q1",
            "product_id": "BNC02",
            "total_after_discount": "500000",
        })
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.latest("id")
        # total_amount vẫn suy ra từ bảng giá kênh vì payload không chốt.
        self.assertEqual(int(order.total_amount), 1290000)
        self.assertEqual(int(order.discount_amount), 0)
        self.assertEqual(int(order.total_after_discount), 500000)

    def test_discount_over_total_returns_400_and_creates_no_order(self):
        before = Order.objects.count()
        resp = self._post({
            "channel_id": "Facebook Thành", "phone": "0909123465", "address": "1 Q1",
            "product_id": "BNC02",
            "total_amount": "100000",
            "discount_amount": "150000",
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("discount_amount", resp.data)
        self.assertEqual(Order.objects.count(), before)
        trace = LandingIngestTrace.objects.latest("id")
        self.assertEqual(trace.status, LandingIngestTraceStatus.VALIDATION_FAILED)

    def test_missing_amount_fields_keep_legacy_behaviour(self):
        resp = self._post({
            "channel_id": "Facebook Thành", "phone": "0909123466", "address": "1 Q1",
            "product_id": "BNC02",
        })
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.latest("id")
        self.assertEqual(int(order.total_amount), 1290000)
        self.assertEqual(int(order.discount_amount), 0)
        self.assertEqual(int(order.total_after_discount), 1290000)

    def test_total_after_discount_filled_when_no_product_match(self):
        """Trước đây đơn không khớp SP nào lưu total_after_discount = 0 dù
        total_amount > 0, làm hụt mọi báo cáo doanh thu ăn cột này."""
        resp = self._post({
            "channel_id": "Facebook Thành", "phone": "0909123467", "address": "1 Q1",
            "product_id": "SKU-KHONG-CO",
            "total_amount": "777000",
        })
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.latest("id")
        self.assertEqual(order.items.count(), 0)
        self.assertEqual(int(order.total_after_discount), 777000)


class LandingIngestTransientDbErrorRetryTests(TestCase):
    """Task 18 audit follow-up: process_landing_ingest phải tự retry khi gặp lỗi
    hạ tầng DB tạm thời (OperationalError), và không được tạo đơn trùng khi
    idempotency guard thấy trace đã gắn order_id (bằng chứng đơn đã tạo)."""

    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(
            code="facebook-retry", name="Facebook Retry", owner="Facebook Retry"
        )
        Product.objects.create(name="SP Retry", sku="SKU-RETRY", price="100000")

    def _headers(self):
        return ingest_headers()

    def test_operational_error_autoretries_and_creates_exactly_one_order(self):
        original_create = OrderService.create_order_from_landing
        calls = {"count": 0}

        def flaky_create(payload):
            calls["count"] += 1
            if calls["count"] == 1:
                raise OperationalError("connection reset by peer")
            return original_create(payload)

        def _run_task(_task_name, **kwargs):
            # Chạy qua .apply() (không gọi hàm trực tiếp) để autoretry_for của
            # Celery thực sự kích hoạt: request.called_directly=False khi qua
            # .apply()/tracer, nên self.retry() bên trong sẽ re-apply task thay
            # vì chỉ re-raise lỗi gốc như khi gọi task như hàm Python thường.
            return process_landing_ingest.apply(
                kwargs={"payload": kwargs["payload"], "trace_id": kwargs["trace_id"]}
            )

        with patch(
            "apps.orders.application.services.order_ingest_service.dispatch",
            side_effect=_run_task,
        ), patch(
            "apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks.OrderService.create_order_from_landing",
            side_effect=flaky_create,
        ):
            resp = self.client.post(
                "/api/orders/ingest",
                data={
                    "channel_id": "Facebook Retry",
                    "name": "Nguyen Van Retry",
                    "phone": "0909555111",
                    "address": "1 Retry St",
                    "product_id": "SKU-RETRY",
                },
                format="json",
                **self._headers(),
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(calls["count"], 2, "expected exactly one retry after the transient failure")
        self.assertEqual(Order.objects.filter(phone="0909555111").count(), 1)
        trace = LandingIngestTrace.objects.get(id=resp.data["trace_id"])
        self.assertEqual(trace.status, LandingIngestTraceStatus.CREATED)
        self.assertIsNotNone(trace.order_id)

    def test_post_commit_failure_does_not_duplicate_order_on_retry(self):
        """Race thật sự cần guard: tạo đơn THÀNH CÔNG (đã commit, trace đã gắn
        order_id) nhưng một bước SAU đó trong task nổ OperationalError →
        autoretry chạy lại → phải thấy bằng chứng order_id trên trace và bỏ
        qua: đúng 1 đơn tồn tại, trace kết thúc CREATED (không bị đè
        TASK_FAILED)."""
        from apps.orders.infrastructure.tasks.order_tasks_impl import ingest_job_tasks

        real_labels = ingest_job_tasks.landing_ingest_processed_total.labels
        state = {"raised": False}

        def flaky_labels(**kwargs):
            # Nổ lỗi hạ tầng ở bước metric NGAY SAU khi đơn + trace CREATED đã
            # ghi xong — mô phỏng "commit rồi mới rớt kết nối".
            if kwargs.get("result") == "created" and not state["raised"]:
                state["raised"] = True
                raise OperationalError("connection lost after commit")
            return real_labels(**kwargs)

        def _run_task(_task_name, **kwargs):
            return process_landing_ingest.apply(
                kwargs={"payload": kwargs["payload"], "trace_id": kwargs["trace_id"]}
            )

        with patch(
            "apps.orders.application.services.order_ingest_service.dispatch",
            side_effect=_run_task,
        ), patch.object(
            ingest_job_tasks.landing_ingest_processed_total,
            "labels",
            side_effect=flaky_labels,
        ):
            resp = self.client.post(
                "/api/orders/ingest",
                data={
                    "channel_id": "Facebook Retry",
                    "name": "Nguyen Van PostCommit",
                    "phone": "0909555114",
                    "address": "2 Retry St",
                    "product_id": "SKU-RETRY",
                },
                format="json",
                **self._headers(),
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(state["raised"], "the post-commit step must have raised once")
        self.assertEqual(Order.objects.filter(phone="0909555114").count(), 1)
        trace = LandingIngestTrace.objects.get(id=resp.data["trace_id"])
        self.assertEqual(trace.status, LandingIngestTraceStatus.CREATED)
        self.assertEqual(trace.order_id, Order.objects.get(phone="0909555114").id)

    def test_validation_error_is_not_retried(self):
        """ValueError (đơn hỏng, ví dụ channel không tồn tại) không được retry —
        retry vô ích vì lỗi không phải do hạ tầng DB."""
        call_count = {"count": 0}

        def counting_create(payload):
            call_count["count"] += 1
            raise ValueError("Kênh bán không tồn tại")

        trace = LandingIngestTrace.objects.create(
            status=LandingIngestTraceStatus.RECEIVED,
            source="landing",
            payload={},
        )
        with patch(
            "apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks.OrderService.create_order_from_landing",
            side_effect=counting_create,
        ):
            process_landing_ingest.apply(
                kwargs={"payload": {"channel_id": self.channel.id, "phone": "0909555112"}, "trace_id": trace.id}
            )

        self.assertEqual(call_count["count"], 1, "ValueError must not be retried")
        trace.refresh_from_db()
        self.assertEqual(trace.status, LandingIngestTraceStatus.TASK_FAILED)

    def test_idempotency_guard_skips_when_trace_already_created(self):
        """Nếu trace đã ở trạng thái CREATED (đơn đã tạo thành công trước đó, ví
        dụ retry chạy sau khi transaction gốc thực ra đã commit) → task phải
        return sớm, không gọi lại create_order_from_landing, không tạo đơn thứ 2."""
        customer = Customer.objects.create(
            full_name="Existing",
            phone_raw="0909555113",
            phone_normalized="0909555113",
        )
        existing_order = Order.objects.create(
            channel=self.channel,
            customer=customer,
            name="Existing",
            phone="0909555113",
            order_source="landing",
        )
        trace = LandingIngestTrace.objects.create(
            status=LandingIngestTraceStatus.CREATED,
            source="landing",
            payload={},
            order=existing_order,
        )
        before_count = Order.objects.count()

        with patch(
            "apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks.OrderService.create_order_from_landing"
        ) as mock_create:
            process_landing_ingest(
                payload={"channel_id": self.channel.id, "phone": "0909555113"},
                trace_id=trace.id,
            )

        mock_create.assert_not_called()
        self.assertEqual(Order.objects.count(), before_count)

    def test_guard_is_evidence_based_skips_even_if_status_was_downgraded(self):
        """Guard dựa trên bằng chứng order_id, không dựa status: kể cả khi
        status trace từng bị đè nhầm xuống TASK_FAILED (bug cũ) mà order_id
        vẫn còn → retry vẫn phải bỏ qua, không tạo đơn trùng."""
        customer = Customer.objects.create(
            full_name="Downgraded",
            phone_raw="0909555115",
            phone_normalized="0909555115",
        )
        existing_order = Order.objects.create(
            channel=self.channel,
            customer=customer,
            name="Downgraded",
            phone="0909555115",
            order_source="landing",
        )
        trace = LandingIngestTrace.objects.create(
            status=LandingIngestTraceStatus.TASK_FAILED,
            source="landing",
            payload={},
            order=existing_order,
        )
        before_count = Order.objects.count()

        with patch(
            "apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks.OrderService.create_order_from_landing"
        ) as mock_create:
            process_landing_ingest(
                payload={"channel_id": self.channel.id, "phone": "0909555115"},
                trace_id=trace.id,
            )

        mock_create.assert_not_called()
        self.assertEqual(Order.objects.count(), before_count)


class LandingConfirmedAmountTests(TestCase):
    """Snapshot tiền khách tự đăng ký: ghi 1 lần lúc ingest, bất biến về sau."""

    def setUp(self):
        self.channel = Channel.objects.create(
            code="fb-lca", name="FB LCA", owner="FB LCA"
        )

    def _create(self, phone: str, **extra):
        payload = {"channel_id": self.channel.id, "phone": phone, **extra}
        return OrderService.create_order_from_landing(payload)

    def test_snapshot_from_product_id_and_quantity(self):
        Product.objects.create(name="Bồn ngâm chân", sku="BNC09", price="1500000")
        order = self._create("0911000001", product_id="BNC09", quantity=1)
        self.assertEqual(order.landing_confirmed_amount, Decimal("1500000"))

    def test_snapshot_from_product_raw_text(self):
        for phone, raw_text, expected in (
            ("0911000002", "Mua 1 Thảm massage: 1.290.000 VNĐ", Decimal("1290000")),
            ("0911000004", "hàng lạ không giá", Decimal("0")),
        ):
            with self.subTest(product=raw_text):
                order = self._create(phone, product=raw_text)
                self.assertEqual(order.landing_confirmed_amount, expected)

    def test_snapshot_from_explicit_total_amount(self):
        order = self._create("0911000003", total_amount=2550000)
        self.assertEqual(order.landing_confirmed_amount, Decimal("2550000"))

    def test_snapshot_immutable_when_order_edited(self):
        Product.objects.create(name="Đai lưng", sku="DTL09", price="1500000")
        order = self._create("0911000005", product_id="DTL09", quantity=1)
        order.total_amount = Decimal("4200000")
        order.total_after_discount = Decimal("4200000")
        order.save()
        order.refresh_from_db()
        self.assertEqual(order.landing_confirmed_amount, Decimal("1500000"))

    def test_clone_copies_snapshot(self):
        Product.objects.create(name="Tủ", sku="SQA09", price="1500000")
        order = self._create("0911000006", product_id="SQA09", quantity=1)
        clone = OrderService.duplicate_order(order, None)
        self.assertEqual(clone.landing_confirmed_amount, Decimal("1500000"))


class LandingIngestHasAddressTests(TestCase):
    """Cờ `Order.has_address` — đơn API ingest (landing/lark) đổ về không có địa chỉ.

    Ngữ nghĩa: ghi tại thời điểm ingest, bất biến sau đó. Đơn tạo tay/Excel giữ
    default True (không test ở đây — không có điểm set nào ngoài 2 luồng ingest).
    """

    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(code="fb-team", name="FB Team", owner="FB Team")

    def _post_ingest(self, payload):
        def _run_task(_task_name, **kwargs):
            process_landing_ingest(payload=kwargs["payload"], trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        with patch(
            "apps.orders.application.services.order_ingest_service.dispatch",
            side_effect=_run_task,
        ):
            return self.client.post("/api/orders/ingest", payload, format="json", **ingest_headers())

    def test_with_address_keeps_true(self):
        resp = self._post_ingest(
            {"channel_id": "fb-team", "phone": "0909123456", "address": "1 Nguyen Trai, Q1"}
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.get(phone="0909123456")
        self.assertTrue(order.has_address)

    def test_without_address_sets_false(self):
        resp = self._post_ingest({"channel_id": "fb-team", "phone": "0909123457"})
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.get(phone="0909123457")
        self.assertFalse(order.has_address)

    def test_placeholder_address_sets_false(self):
        """Placeholder 'không có địa chỉ' bị normalize thành rỗng → coi như không có địa chỉ."""
        cases = [
            ("0909123458", "Không có địa chỉ giao hàng"),
            ("0909123459", "N/A"),
            ("0909123460", "  Tư vấn  "),
        ]
        for phone, placeholder in cases:
            with self.subTest(placeholder=placeholder):
                resp = self._post_ingest(
                    {"channel_id": "fb-team", "phone": phone, "address": placeholder}
                )
                self.assertEqual(resp.status_code, 200, resp.data)
                order = Order.objects.get(phone=phone)
                self.assertFalse(order.has_address)


class LarkIngestHasAddressTests(TestCase):
    """Cờ has_address cho luồng lark-ingest — cùng ngữ nghĩa với
    LandingIngestHasAddressTests (xem docstring class đó)."""

    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(code="lark-ch", name="Lark Ch", owner="Lark Ch")

    def _post_lark(self, body):
        return self.client.post("/api/orders/lark-ingest", body, format="json", **ingest_headers())

    def test_with_address_keeps_true(self):
        resp = self._post_lark(
            {"channel_code": "lark-ch", "name": "Tran B", "phone": "0911222333", "raw_address": "2 Le Loi"}
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        order = Order.objects.get(phone="0911222333")
        self.assertTrue(order.has_address)

    def test_without_address_sets_false(self):
        resp = self._post_lark({"channel_code": "lark-ch", "name": "Tran C", "phone": "0911222334"})
        self.assertEqual(resp.status_code, 200, resp.content)
        order = Order.objects.get(phone="0911222334")
        self.assertFalse(order.has_address)


class HasAddressListFilterApiTests(TestCase):
    """Bộ lọc has_address (yes/no/rỗng) trên các list endpoint dùng chung OrdersFilterPanel."""

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-has-address-filter")
        self.client.force_authenticate(user=self.admin)
        self.channel = Channel.objects.create(code="fb-addr", name="FB Addr", owner="FB Addr")
        self.customer = Customer.objects.create(
            full_name="Khach Loc Dia Chi",
            phone_raw="0909000010",
            phone_normalized="0909000010",
        )
        self.order_with = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order Co Dia Chi",
            phone="0909000010",
            raw_address="1 Nguyen Trai, Q1",
            order_source="landing",
        )
        self.order_without = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order Khong Dia Chi",
            phone="0909000011",
            has_address=False,
            order_source="landing",
        )

    def _ids(self, endpoint, body):
        response = self.client.post(
            endpoint, {"limit": "100", "offset": "0", **body}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        return {item["id"] for item in response.json()["results"]}

    def test_list_endpoints_filter_by_has_address(self):
        for endpoint in ("/api/orders", "/api/orders/landing"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self._ids(endpoint, {"has_address": "yes"}), {self.order_with.id})
                self.assertEqual(self._ids(endpoint, {"has_address": "no"}), {self.order_without.id})
                self.assertEqual(
                    self._ids(endpoint, {}), {self.order_with.id, self.order_without.id}
                )

    def test_serializer_exposes_has_address(self):
        response = self.client.post(
            "/api/orders", {"limit": "100", "offset": "0"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        by_id = {item["id"]: item for item in response.json()["results"]}
        self.assertTrue(by_id[self.order_with.id]["has_address"])
        self.assertFalse(by_id[self.order_without.id]["has_address"])


class LandingOrderSerializerStaffUsernameTests(SimpleTestCase):
    """Luật gate của `staff_username` — thuần serializer, không cần DB kênh/user."""

    def _validated(self, **extra):
        serializer = LandingOrderSerializer(
            data={"channel_id": "Facebook Ads Team", "phone": "0909123456", **extra}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        return serializer.validated_data

    def test_keeps_trimmed_username_for_non_landing_source(self):
        data = self._validated(order_source="zalo", staff_username="  sale.a  ")
        self.assertEqual(data["staff_username"], "sale.a")

    def test_drops_username_when_source_normalizes_to_landing(self):
        # Thiếu order_source hoặc giá trị lạ → normalize về "landing" → bỏ qua chỉ định sale.
        for label, extra in (
            ("landing", {"order_source": "landing"}),
            ("absent", {}),
            ("invalid", {"order_source": "not-a-source"}),
        ):
            with self.subTest(label):
                data = self._validated(staff_username="sale.a", **extra)
                self.assertNotIn("staff_username", data)

    def test_drops_blank_username(self):
        for blank in ("", "   "):
            with self.subTest(value=blank):
                data = self._validated(order_source="zalo", staff_username=blank)
                self.assertNotIn("staff_username", data)

    def test_drops_null_username(self):
        data = self._validated(order_source="zalo", staff_username=None)
        self.assertNotIn("staff_username", data)

    def test_truncates_overlong_username_instead_of_rejecting(self):
        # Field best-effort: tên quá dài phải bị CẮT, không được làm is_valid()
        # = False và làm mất cả đơn (sai một cái tên không đáng mất lead).
        overlong = "a" * 200
        data = self._validated(order_source="zalo", staff_username=overlong)
        self.assertEqual(data["staff_username"], overlong[:150])
        self.assertEqual(len(data["staff_username"]), 150)


class ResolveIngestStaffIdTests(TestCase):
    def test_matches_username_case_insensitively(self):
        user = User.objects.create_user(username="Sale.A", password="x")
        self.assertEqual(resolve_ingest_staff_id("sale.a"), user.id)

    def test_returns_none_for_unknown_username(self):
        self.assertIsNone(resolve_ingest_staff_id("khong-ton-tai"))

    def test_ignores_inactive_user(self):
        User.objects.create_user(username="sale.off", password="x", is_active=False)
        self.assertIsNone(resolve_ingest_staff_id("sale.off"))

    def test_ignores_soft_deleted_user(self):
        User.objects.create_user(
            username="sale.deleted", password="x", deleted_at=timezone.now()
        )
        self.assertIsNone(resolve_ingest_staff_id("sale.deleted"))

    def test_ignores_system_role_user(self):
        # Endpoint AllowAny + token dùng chung → "admin" là username dễ đoán
        # nhất. Không loại role hệ thống thì payload giả gán được đơn cho
        # Administrator, và đơn đó thoát khỏi cả assign_if_unassigned lẫn sweep.
        make_admin("sale.admin")
        self.assertIsNone(resolve_ingest_staff_id("sale.admin"))


class LandingIngestPayloadStaffTests(TestCase):
    """Payload đẩy sang queue phải mang `staff_id` (int) và KHÔNG mang username."""

    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(
            code="facebook-ads-team", name="Facebook Ads Team", owner="Facebook Ads Team"
        )
        self.sale = User.objects.create_user(username="sale.designated", password="x")

    def _dispatch_kwargs(self, **extra):
        """POST ingest với dispatch bị chặn — chỉ soi payload, không tạo đơn."""
        captured = {}

        def _capture(_task_name, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id="test-job")

        with patch(
            "apps.orders.application.services.order_ingest_service.dispatch",
            side_effect=_capture,
        ):
            response = self.client.post(
                "/api/orders/ingest",
                data={"channel_id": "Facebook Ads Team", "phone": "0909123456", **extra},
                format="json",
                **ingest_headers(),
            )
        self.assertEqual(response.status_code, 200, response.data)
        return captured["payload"]

    def test_resolved_username_becomes_staff_id(self):
        payload = self._dispatch_kwargs(order_source="zalo", staff_username="sale.designated")
        self.assertEqual(payload["staff_id"], self.sale.id)
        self.assertNotIn("staff_username", payload)

    def test_unknown_username_leaves_no_staff_id(self):
        payload = self._dispatch_kwargs(order_source="zalo", staff_username="khong-ton-tai")
        self.assertNotIn("staff_id", payload)
        self.assertNotIn("staff_username", payload)

    def test_landing_source_leaves_no_staff_id(self):
        payload = self._dispatch_kwargs(order_source="landing", staff_username="sale.designated")
        self.assertNotIn("staff_id", payload)


class LandingIngestStaffUsernameTests(TestCase):
    """Ma trận quyết định: khi nào đơn gán theo payload, khi nào rơi về bộ chia.

    `pick_staff_for` bị patch trả `auto_picked` nên "đi vào bộ chia" quan sát được
    bằng chính danh tính sale trên đơn, không phải bằng việc mock có được gọi.
    `current_shift_and_date` phải patch vì test DB không có cấu hình ca — thiếu nó
    bộ chia skip im lặng và hai nhánh trở nên không phân biệt được.
    """

    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(
            code="facebook-ads-team", name="Facebook Ads Team", owner="Facebook Ads Team"
        )
        self.designated = User.objects.create_user(username="sale.designated", password="x")
        self.auto_picked = User.objects.create_user(username="sale.auto", password="x")

    def _ingest(self, phone, **extra):
        """POST ingest chạy task đồng bộ. Trả (order, mock pick_staff_for)."""
        payload = {
            "channel_id": "Facebook Ads Team",
            "name": "Nguyen Van A",
            "phone": phone,
            "address": "1 Nguyen Trai, Q1",
            **extra,
        }

        def _run_task(_task_name, **kwargs):
            process_landing_ingest(payload=kwargs["payload"], trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        with (
            patch(
                "apps.orders.application.services.order_ingest_service.dispatch",
                side_effect=_run_task,
            ),
            patch.object(
                ShiftService,
                "current_shift_and_date",
                return_value=("morning", timezone.localdate()),
            ),
            patch.object(
                LeadAssignmentService, "pick_staff_for", return_value=self.auto_picked
            ) as pick_mock,
        ):
            response = self.client.post(
                "/api/orders/ingest", data=payload, format="json", **ingest_headers()
            )
        self.assertEqual(response.status_code, 200, response.data)
        return Order.objects.latest("id"), pick_mock

    def test_non_landing_source_assigns_designated_sale(self):
        order, pick_mock = self._ingest(
            "0909000001", order_source="zalo", staff_username="sale.designated"
        )
        self.assertEqual(order.staff_id, self.designated.id)
        pick_mock.assert_not_called()

    def test_unknown_username_falls_back_to_auto_assign(self):
        order, pick_mock = self._ingest(
            "0909000002", order_source="zalo", staff_username="khong-ton-tai"
        )
        self.assertEqual(order.staff_id, self.auto_picked.id)
        pick_mock.assert_called_once()

    def test_inactive_user_falls_back_to_auto_assign(self):
        User.objects.create_user(username="sale.off", password="x", is_active=False)
        order, _ = self._ingest(
            "0909000003", order_source="zalo", staff_username="sale.off"
        )
        self.assertEqual(order.staff_id, self.auto_picked.id)

    def test_soft_deleted_user_falls_back_to_auto_assign(self):
        User.objects.create_user(
            username="sale.deleted", password="x", deleted_at=timezone.now()
        )
        order, _ = self._ingest(
            "0909000004", order_source="zalo", staff_username="sale.deleted"
        )
        self.assertEqual(order.staff_id, self.auto_picked.id)

    def test_designation_ignored_falls_back_to_auto_assign(self):
        for label, phone, extra in (
            ("landing_source", "0909000005", {"order_source": "landing", "staff_username": "sale.designated"}),
            ("absent_source", "0909000006", {"staff_username": "sale.designated"}),
            ("blank_username", "0909000007", {"order_source": "zalo", "staff_username": "   "}),
        ):
            with self.subTest(label):
                order, _ = self._ingest(phone, **extra)
                self.assertEqual(order.staff_id, self.auto_picked.id)

    def test_designated_sale_logs_ingest_journey_event(self):
        # Đường ingest ẩn danh (token dùng chung, không actor) → phải để lại
        # dấu vết audit trên timeline khi sale được chỉ định từ payload.
        order, _ = self._ingest(
            "0909000008", order_source="zalo", staff_username="sale.designated"
        )
        events = OrderJourneyEvent.objects.filter(
            order=order, event_type=STAFF_INGEST_DESIGNATED_EVENT
        )
        self.assertEqual(events.count(), 1)
        event = events.first()
        self.assertIsNone(event.actor_id)
        self.assertEqual(event.to_code, str(self.designated.id))
        # Không lẫn với event của bộ chia tự động.
        self.assertFalse(
            OrderJourneyEvent.objects.filter(order=order, event_type=STAFF_ASSIGNED_EVENT).exists()
        )

    def test_auto_assigned_order_has_no_ingest_journey_event(self):
        order, _ = self._ingest("0909000009", order_source="zalo", staff_username="khong-ton-tai")
        self.assertFalse(
            OrderJourneyEvent.objects.filter(
                order=order, event_type=STAFF_INGEST_DESIGNATED_EVENT
            ).exists()
        )
