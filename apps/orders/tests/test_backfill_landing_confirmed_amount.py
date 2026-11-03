"""Backfill snapshot tiền khách tự đăng ký cho đơn landing cũ.

Fallback đúng thứ tự ingest: tiền parse từ product_raw -> quantity × giá bảng
giá kênh theo sku -> giữ 0. Idempotent: chỉ đụng đơn đang = 0.
"""
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import LandingIngestTrace, LandingIngestTraceStatus, Order
from apps.catalog.models import Product


class BackfillLandingConfirmedAmountTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="fb-bf", name="FB BF")
        self.customer = Customer.objects.create(
            full_name="KH", phone_raw="0900000010", phone_normalized="0900000010"
        )

    def _order(self, **kwargs):
        return Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="X",
            phone="0900000010",
            order_source="landing",
            **kwargs,
        )

    def _run(self, *args):
        out = StringIO()
        call_command("backfill_landing_confirmed_amount", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_writes_nothing(self):
        order = self._order(product_raw="Mua 1 Thảm: 1.290.000 VNĐ")
        output = self._run()
        order.refresh_from_db()
        self.assertEqual(order.landing_confirmed_amount, Decimal("0"))
        self.assertIn("DRY-RUN", output)

    def test_apply_derives_amount_from_sku_price_times_quantity(self):
        # Đơn ingest không gửi text, chỉ có product_id -> DB lưu sku + quantity,
        # product_raw rỗng (hoặc text không bóc được giá). Trước đây bị bỏ qua
        # (mãi = 0) -> dồn sai vào cột Chốt.
        for label, sku, price, quantity, product_raw, expected in (
            ("sku_only", "BNC07", "1715000", 1, "", Decimal("1715000")),
            ("sku_only_x2", "DTL07", "1200000", 2, "", Decimal("2400000")),
            ("raw_without_price", "BNC08", "1500000", 2, "đăng ký bồn ngâm chân", Decimal("3000000")),
        ):
            with self.subTest(label):
                Product.objects.create(name=f"SP {sku}", sku=sku, price=price)
                order = self._order(product_raw=product_raw, sku=sku, quantity=quantity)
                self._run("--apply")
                order.refresh_from_db()
                self.assertEqual(order.landing_confirmed_amount, expected)

    def test_apply_covers_ingest_order_with_non_landing_source(self):
        # Đơn đổ qua ingest nhưng order_source=zalo (payload gửi tường minh) —
        # nhận diện bằng LandingIngestTrace CREATED, không bằng order_source.
        order = self._order(product_raw="Mua 1 Thảm: 1.290.000 VNĐ")
        Order.objects.filter(pk=order.pk).update(order_source="zalo")
        LandingIngestTrace.objects.create(
            status=LandingIngestTraceStatus.CREATED,
            source="landing",
            payload={},
            order=order,
        )
        self._run("--apply")
        order.refresh_from_db()
        self.assertEqual(order.landing_confirmed_amount, Decimal("1290000"))

    def test_skips_order_with_neither_product_raw_nor_sku(self):
        order = self._order(product_raw="", sku="", quantity=1)
        self._run("--apply")
        order.refresh_from_db()
        self.assertEqual(order.landing_confirmed_amount, Decimal("0"))

    def test_apply_uses_amount_parsed_from_product_raw(self):
        order = self._order(product_raw="Mua 1 Thảm: 1.290.000 VNĐ")
        self._run("--apply")
        order.refresh_from_db()
        self.assertEqual(order.landing_confirmed_amount, Decimal("1290000"))

    def test_apply_keeps_zero_when_nothing_derivable(self):
        order = self._order(product_raw="text lạ không bóc được")
        self._run("--apply")
        order.refresh_from_db()
        self.assertEqual(order.landing_confirmed_amount, Decimal("0"))

    def test_apply_skips_orders_already_backfilled(self):
        order = self._order(
            product_raw="Mua 1 Thảm: 1.290.000 VNĐ",
            landing_confirmed_amount=Decimal("999000"),
        )
        self._run("--apply")
        order.refresh_from_db()
        self.assertEqual(order.landing_confirmed_amount, Decimal("999000"))

    def test_skips_non_landing_orders(self):
        order = self._order(product_raw="Mua 1 Thảm: 1.290.000 VNĐ")
        Order.objects.filter(pk=order.pk).update(order_source="zalo")
        self._run("--apply")
        order.refresh_from_db()
        self.assertEqual(order.landing_confirmed_amount, Decimal("0"))


class RepairTextSuffixTests(TestCase):
    """--repair-text-suffix: sửa đơn bị parser cũ khớp-phần-đuôi ghi sai."""

    TEXT = (
        "1 Máy massage chân cao cấp MSC06 giá 1.599.000 "
        "+ Voucher 100.000 cho đơn tiếp theo"
    )

    def setUp(self):
        self.channel = Channel.objects.create(code="fb-rp", name="FB RP")
        self.customer = Customer.objects.create(
            full_name="KH", phone_raw="0388581760", phone_normalized="0388581760"
        )

    def _order(self, **kwargs):
        return Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="X",
            phone="0388581760",
            order_source="landing",
            **kwargs,
        )

    def _run(self, *args):
        out = StringIO()
        call_command("backfill_landing_confirmed_amount", *args, stdout=out)
        return out.getvalue()

    def test_repair_fixes_suffix_matched_snapshot(self):
        # nạn nhân thật 2026-08-04: 1.599.000 bị ghi thành 599.000
        order = self._order(
            product_raw=self.TEXT, landing_confirmed_amount=Decimal("599000")
        )
        self._run("--repair-text-suffix", "--apply")
        order.refresh_from_db()
        self.assertEqual(order.landing_confirmed_amount, Decimal("1599000"))

    def test_repair_dry_run_writes_nothing(self):
        order = self._order(
            product_raw=self.TEXT, landing_confirmed_amount=Decimal("599000")
        )
        output = self._run("--repair-text-suffix")
        order.refresh_from_db()
        self.assertEqual(order.landing_confirmed_amount, Decimal("599000"))
        self.assertIn("DRY-RUN", output)

    def test_repair_skips_snapshot_from_explicit_payload(self):
        # snapshot 1tr4 đến từ payload tường minh (khác số parser cũ 599k) -> giữ nguyên
        order = self._order(
            product_raw=self.TEXT, landing_confirmed_amount=Decimal("1400000")
        )
        self._run("--repair-text-suffix", "--apply")
        order.refresh_from_db()
        self.assertEqual(order.landing_confirmed_amount, Decimal("1400000"))

    def test_repair_skips_text_parsed_correctly(self):
        # text giá đứng độc lập: parser cũ và mới cùng ra 599k -> không phải nạn nhân
        order = self._order(
            product_raw="Bồn ngâm chân giá 599.000",
            landing_confirmed_amount=Decimal("599000"),
        )
        self._run("--repair-text-suffix", "--apply")
        order.refresh_from_db()
        self.assertEqual(order.landing_confirmed_amount, Decimal("599000"))
