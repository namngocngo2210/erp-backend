"""Lệnh ``sync_invoice_prices`` (gộp ``sync_invoice_amounts`` + ``sync_invoice_item_prices``)."""

from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.kiotviet.infrastructure.services.invoice_price_sync import invoice_price_map, invoice_total
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order, OrderItem

_PATCH = "apps.kiotviet.infrastructure.services.invoice_price_sync.get_invoice_by_code"

INVOICE = {
    "Total": 250000,
    "invoiceDetails": [
        {"productCode": "MSC06", "price": 200000, "invoiceDetailTaxs": [{"priceAfterTax": 216000}]},
        {"productCode": "BNC02", "price": 34000},
        {"productCode": "", "price": 1},
    ],
}


class SyncInvoicePricesCommandTests(TestCase):
    def setUp(self):
        self.kv = KiotVietChannel.objects.create(
            name="KV", branch_id="1", client_id="c", client_secret="s", retailer="r",
        )
        self.channel = Channel.objects.create(code="ch-sip", name="CH")
        self.customer = Customer.objects.create(
            full_name="KH", phone_raw="0911111111", phone_normalized="0911111111"
        )
        self.order = Order.objects.create(
            channel=self.channel, customer=self.customer, kiotviet_channel=self.kv,
            invoice_code="HD1", external_order_id="HD1", name="X", phone="0911111111",
            total_amount=Decimal("0"), total_after_discount=Decimal("0"),
        )
        msc06 = Product.objects.create(name="Máy massage", sku="MSC06")
        khac = Product.objects.create(name="Khác", sku="KHAC")
        self.item = OrderItem.objects.create(
            order=self.order, product=msc06, sku="MSC06", quantity=2,
            unit_price=Decimal("200000"), discount=Decimal("10000"), line_total=Decimal("380000"),
        )
        self.other = OrderItem.objects.create(
            order=self.order, product=khac, sku="KHAC", quantity=1,
            unit_price=Decimal("5"), line_total=Decimal("5"),
        )

    def _run(self, *args):
        out = StringIO()
        with patch(_PATCH, return_value=INVOICE) as mocked:
            call_command(
                "sync_invoice_prices", "--kiotviet-channel-id", str(self.kv.id), *args, stdout=out,
            )
        return out.getvalue(), mocked

    def test_pure_helpers(self):
        self.assertEqual(invoice_total(INVOICE), Decimal("250000"))
        self.assertEqual(invoice_total({"total": "7"}), Decimal("7"))
        self.assertEqual(
            invoice_price_map(INVOICE), {"MSC06": Decimal("216000"), "BNC02": Decimal("34000")}
        )
        self.assertIsNone(invoice_price_map({"invoiceDetails": []}))
        self.assertEqual(invoice_price_map({"InvoiceDetails": [{"code": "A"}]}), {})

    def test_amounts_only(self):
        output, _ = self._run("--target", "amounts")
        self.order.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.order.total_amount, Decimal("250000"))
        self.assertEqual(self.order.total_after_discount, Decimal("250000"))
        self.assertEqual(self.item.unit_price, Decimal("200000"))  # items không đụng
        self.assertIn("'amounts_updated': 1", output)
        self.assertNotIn("items_updated", output)

    def test_items_only_keeps_line_discount(self):
        output, _ = self._run("--target", "items")
        self.order.refresh_from_db()
        self.item.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.order.total_amount, Decimal("0"))  # amounts không đụng
        self.assertEqual(self.item.unit_price, Decimal("216000"))
        self.assertEqual(self.item.line_total, Decimal("412000"))  # (216000 − 10000) × 2
        self.assertEqual(self.other.unit_price, Decimal("5"))  # SKU không có trên hóa đơn
        self.assertIn("'items_updated': 1", output)
        self.assertIn("'items_skipped': 1", output)

    def test_both_fetches_invoice_once_per_order(self):
        output, mocked = self._run("--target", "both")
        self.order.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(self.order.total_amount, Decimal("250000"))
        self.assertEqual(self.item.unit_price, Decimal("216000"))
        self.assertIn("Sync invoice prices completed.", output)

    def test_dry_run_writes_nothing(self):
        output, _ = self._run("--dry-run")
        self.order.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.order.total_amount, Decimal("0"))
        self.assertEqual(self.item.unit_price, Decimal("200000"))
        self.assertIn("[DRY-RUN]", output)

    def test_limit_and_unknown_channel(self):
        output, _ = self._run("--limit", "0")
        self.assertIn("Found 1 orders", output)
        with self.assertRaises(CommandError):
            call_command("sync_invoice_prices", "--kiotviet-channel-id", "999999", stdout=StringIO())

    def test_fetch_error_is_counted_not_raised(self):
        err = StringIO()
        with patch(_PATCH, side_effect=RuntimeError("boom")):
            call_command(
                "sync_invoice_prices", "--kiotviet-channel-id", str(self.kv.id),
                stdout=StringIO(), stderr=err,
            )
        self.assertIn("boom", err.getvalue())
