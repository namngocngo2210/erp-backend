"""Test nguồn đơn sàn (Shopee/TikTok): detect theo prefix mã hoá đơn + command backfill
+ loại đơn sàn khỏi trang Landing (landing list, sheet 1, sheet 3)."""
from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderSource
from apps.users.tests.helpers import make_admin


def _make_order(channel, customer, **kwargs):
    return Order.objects.create(channel=channel, customer=customer, **kwargs)


class OrderSourceFromInvoiceCodeTests(SimpleTestCase):
    def test_new_choices_exist(self):
        self.assertIn("shopee", OrderSource.values)
        self.assertIn("tiktok", OrderSource.values)

    def test_tiktok_prefix(self):
        self.assertEqual(OrderSource.from_invoice_code("HDTTS_12345"), OrderSource.TIKTOK)

    def test_shopee_prefix(self):
        self.assertEqual(OrderSource.from_invoice_code("HDSPE_ABC"), OrderSource.SHOPEE)

    def test_case_insensitive(self):
        self.assertEqual(OrderSource.from_invoice_code("hdtts_x"), OrderSource.TIKTOK)
        self.assertEqual(OrderSource.from_invoice_code("HdSpE_1"), OrderSource.SHOPEE)

    def test_no_match(self):
        self.assertIsNone(OrderSource.from_invoice_code("HD004321"))
        self.assertIsNone(OrderSource.from_invoice_code(""))
        self.assertIsNone(OrderSource.from_invoice_code(None))
        # Prefix phải ở ĐẦU chuỗi, không phải contains.
        self.assertIsNone(OrderSource.from_invoice_code("XHDTTS_1"))


class OrderSaveMarketplaceSourceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(code="mkt-ch", name="Marketplace Channel")
        cls.customer = Customer.objects.create(
            full_name="KH San",
            phone_raw="0900000002",
            phone_normalized="0900000002",
        )

    def test_create_sets_source_from_invoice_code(self):
        order = _make_order(self.channel, self.customer, invoice_code="HDSPE_001")
        order.refresh_from_db()
        self.assertEqual(order.order_source, OrderSource.SHOPEE)

    def test_create_overrides_other_source_when_prefix_matches(self):
        order = _make_order(
            self.channel,
            self.customer,
            invoice_code="HDTTS_001",
            order_source=OrderSource.LANDING,
        )
        order.refresh_from_db()
        self.assertEqual(order.order_source, OrderSource.TIKTOK)

    def test_create_without_marketplace_code_keeps_source(self):
        order = _make_order(
            self.channel,
            self.customer,
            invoice_code="HD004321",
            order_source=OrderSource.LANDING,
        )
        order.refresh_from_db()
        self.assertEqual(order.order_source, OrderSource.LANDING)

    def test_create_without_invoice_code_keeps_null_source(self):
        order = _make_order(self.channel, self.customer)
        order.refresh_from_db()
        self.assertIsNone(order.order_source)

    def test_save_update_fields_persists_detected_source(self):
        # Luồng kiểu sync KiotViet: save(update_fields=[...]) chỉ chứa
        # invoice_code — order_source detect được phải được kéo vào update_fields.
        order = _make_order(self.channel, self.customer, order_source=OrderSource.MESSENGER)
        order.invoice_code = "HDSPE_777"
        order.save(update_fields=["invoice_code"])
        order.refresh_from_db()
        self.assertEqual(order.order_source, OrderSource.SHOPEE)


class BackfillMarketplaceOrderSourceCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(code="bf-ch", name="Backfill Channel")
        cls.customer = Customer.objects.create(
            full_name="KH Backfill",
            phone_raw="0900000003",
            phone_normalized="0900000003",
        )

    def _create_raw(self, invoice_code, order_source):
        """Tạo đơn rồi ép order_source qua queryset.update() để né hook save() —
        mô phỏng dữ liệu cũ tồn tại trước khi có auto-detect."""
        order = Order.objects.create(channel=self.channel, customer=self.customer)
        Order.objects.filter(pk=order.pk).update(
            invoice_code=invoice_code, order_source=order_source
        )
        order.refresh_from_db()
        return order

    def _run(self, *args):
        out = StringIO()
        call_command("backfill_marketplace_order_source", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_counts_but_does_not_write(self):
        shopee = self._create_raw("HDSPE_001", None)
        tiktok = self._create_raw("HDTTS_001", OrderSource.LANDING)
        output = self._run()
        shopee.refresh_from_db()
        tiktok.refresh_from_db()
        self.assertIsNone(shopee.order_source)
        self.assertEqual(tiktok.order_source, OrderSource.LANDING)
        self.assertIn("DRY-RUN", output)

    def test_apply_backfills_and_overrides(self):
        shopee = self._create_raw("HDSPE_001", None)
        tiktok = self._create_raw("HDTTS_001", OrderSource.LANDING)
        untouched = self._create_raw("HD004321", OrderSource.LANDING)
        already_ok = self._create_raw("HDSPE_002", OrderSource.SHOPEE)
        no_code = self._create_raw("", None)

        self._run("--apply")

        shopee.refresh_from_db()
        tiktok.refresh_from_db()
        untouched.refresh_from_db()
        already_ok.refresh_from_db()
        no_code.refresh_from_db()
        self.assertEqual(shopee.order_source, OrderSource.SHOPEE)
        self.assertEqual(tiktok.order_source, OrderSource.TIKTOK)
        self.assertEqual(untouched.order_source, OrderSource.LANDING)
        self.assertEqual(already_ok.order_source, OrderSource.SHOPEE)
        self.assertIsNone(no_code.order_source)

    def test_apply_is_case_insensitive(self):
        order = self._create_raw("hdtts_lower", None)
        self._run("--apply")
        order.refresh_from_db()
        self.assertEqual(order.order_source, OrderSource.TIKTOK)


class LandingListExcludesMarketplaceTests(TestCase):
    """Đơn sàn (shopee/tiktok) không thuộc luồng sale Landing — phải bị loại khỏi
    trang Landing (list + sheet 1) và Sheet 3, giống cách loại đơn koc."""

    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(code="ld-mkt-ch", name="Landing Mkt", owner="Landing Mkt")
        cls.customer = Customer.objects.create(
            full_name="KH Landing",
            phone_raw="0900000004",
            phone_normalized="0900000004",
        )
        complete = dict(
            channel=cls.channel,
            customer=cls.customer,
            name="KH Du TT",
            phone="0900000004",
            raw_address="1 Phố Huế, Hà Nội",
        )
        cls.landing = Order.objects.create(**complete, order_source=OrderSource.LANDING)
        # order_source được hook save() tự gắn shopee/tiktok từ mã hoá đơn.
        cls.shopee = Order.objects.create(**complete, invoice_code="HDSPE_L1")
        cls.tiktok = Order.objects.create(**complete, invoice_code="HDTTS_L1")
        # Thiếu thông tin (không tên/SĐT/địa chỉ) → thuộc phạm vi Sheet 3.
        cls.landing_incomplete = Order.objects.create(
            channel=cls.channel, customer=cls.customer, order_source=OrderSource.LANDING
        )
        cls.tiktok_incomplete = Order.objects.create(
            channel=cls.channel, customer=cls.customer, invoice_code="HDTTS_L2"
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=make_admin("admin-landing-mkt"))

    def _ids(self, endpoint, body=None):
        response = self.client.post(
            endpoint, {"limit": "100", "offset": "0", **(body or {})}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        return {item["id"] for item in response.json()["results"]}

    def test_landing_list_excludes_marketplace_orders(self):
        ids = self._ids("/api/orders/landing")
        self.assertIn(self.landing.id, ids)
        self.assertIn(self.landing_incomplete.id, ids)
        self.assertNotIn(self.shopee.id, ids)
        self.assertNotIn(self.tiktok.id, ids)
        self.assertNotIn(self.tiktok_incomplete.id, ids)

    def test_landing_sheet1_excludes_marketplace_orders(self):
        ids = self._ids("/api/orders/landing", {"sheet": "1"})
        self.assertIn(self.landing.id, ids)
        self.assertNotIn(self.shopee.id, ids)
        self.assertNotIn(self.tiktok.id, ids)

    def test_sheet3_excludes_marketplace_orders(self):
        ids = self._ids("/api/orders/sheet3")
        self.assertIn(self.landing_incomplete.id, ids)
        self.assertNotIn(self.tiktok_incomplete.id, ids)
