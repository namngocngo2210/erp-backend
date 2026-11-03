"""product_id từ landing đến dạng chuỗi có dấu phân cách hàng nghìn ("3.681", "3,681").

Ladipage format số ≥ 1000 kiểu Việt Nam trước khi gửi; id < 1000 vẫn đến dạng số.
Trước fix: chuỗi bị coi là SKU → không khớp → đơn tạo ra không có dòng hàng
(~440 ingest/30 ngày cho DC12/DV11/DG20, 09/2026).
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.orders.application.services.order_ingest_service import resolve_landing_product_ids
from apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks import process_landing_ingest
from apps.orders.models import Order
from apps.orders.product_parser import normalize_landing_product_id
from apps.orders.tests.helpers import ingest_headers


class NormalizeLandingProductIdTests(SimpleTestCase):
    def test_int_passthrough(self):
        self.assertEqual(normalize_landing_product_id(3681), 3681)

    def test_digit_string_becomes_int(self):
        self.assertEqual(normalize_landing_product_id("3681"), 3681)
        self.assertEqual(normalize_landing_product_id(" 12 "), 12)

    def test_thousands_separator_dot_and_comma(self):
        self.assertEqual(normalize_landing_product_id("3.681"), 3681)
        self.assertEqual(normalize_landing_product_id("3,681"), 3681)
        self.assertEqual(normalize_landing_product_id("1.234.567"), 1234567)

    def test_sku_strings_untouched(self):
        self.assertEqual(normalize_landing_product_id("BNC02"), "BNC02")
        self.assertEqual(normalize_landing_product_id("BNC02,MSG01"), "BNC02,MSG01")
        self.assertEqual(normalize_landing_product_id("SQA03-1.7M"), "SQA03-1.7M")
        # Không phải nhóm 3 chữ số → không phải định dạng hàng nghìn, giữ nguyên
        self.assertEqual(normalize_landing_product_id("3.68"), "3.68")
        self.assertEqual(normalize_landing_product_id("12.3456"), "12.3456")

    def test_none_bool_empty(self):
        self.assertIsNone(normalize_landing_product_id(None))
        self.assertIsNone(normalize_landing_product_id(True))
        self.assertEqual(normalize_landing_product_id(""), "")


class LandingIngestThousandsProductIdTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(code="facebook-thanh", name="Facebook Thành", owner="FB")
        self.product = Product.objects.create(name="Máy massage bắp chân DC12", sku="DC12", price="1999000")
        self.product.id  # id thật do DB cấp; test dùng str(id) có dấu chấm để mô phỏng landing

    def _formatted_id(self, sep: str) -> str:
        digits = str(self.product.id)
        groups = []
        while digits:
            groups.insert(0, digits[-3:])
            digits = digits[:-3]
        return sep.join(groups)

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

    def test_dotted_product_id_attaches_item(self):
        resp = self._post({
            "channel_id": "Facebook Thành", "phone": "0879551164", "address": "Thanh Trì, Hà Nội",
            "product": "", "product_id": self._formatted_id("."),
        })
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.latest("id")
        item = order.items.first()
        self.assertIsNotNone(item, "product_id '3.681' phải gán được sản phẩm theo id")
        self.assertEqual(item.product_id, self.product.id)
        self.assertEqual(item.quantity, 1)

    def test_comma_product_id_attaches_item(self):
        resp = self._post({
            "channel_id": "Facebook Thành", "phone": "0879551165", "address": "Thanh Trì, Hà Nội",
            "product_id": self._formatted_id(","),
        })
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.latest("id")
        self.assertEqual(order.items.count(), 1)

    def test_digit_string_product_id_attaches_item(self):
        resp = self._post({
            "channel_id": "Facebook Thành", "phone": "0879551166", "address": "Thanh Trì, Hà Nội",
            "product_id": str(self.product.id),
        })
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Order.objects.latest("id").items.count(), 1)

    def test_resolve_landing_product_ids_handles_thousands_format(self):
        self.assertEqual(resolve_landing_product_ids(self._formatted_id(".")), [self.product.id])
        self.assertEqual(resolve_landing_product_ids(self._formatted_id(",")), [self.product.id])
        self.assertEqual(resolve_landing_product_ids(str(self.product.id)), [self.product.id])
        self.assertEqual(resolve_landing_product_ids("DC12"), [self.product.id])
