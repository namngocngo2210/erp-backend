"""VAT trên dòng hàng đẩy sang KiotViet đi theo `KiotVietChannel.vat_tax_id`, KHÔNG theo cờ `old`.

Sự cố 17/09/2026: kênh 6 "HKD Kiot" (hộ kinh doanh, không kê khai VAT) có `old=False` nên
push luôn gửi `TaxId: 3`. Trước đó KV HKD bỏ qua; từ 14:17 ngày 17/09 KV bật thuế suất bán
hàng cho cửa hàng HKD và bắt đầu dán nhãn "VAT 8%" lên từng dòng hoá đơn.
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.kiotviet.infrastructure.services.invoice_payload_builder import (
    build_invoice_details,
    build_target_invoice_payload,
)
from apps.kiotviet.models import KiotVietChannel
from apps.kiotviet.services import push_order_to_kiotviet
from apps.orders.models import Order, OrderItem
from apps.users.models import UserKiotvietMapping

_CREATE_ORDER = "apps.kiotviet.infrastructure.services.invoice_mutations_push.create_order"
_GET_OR_CREATE_CUSTOMER = "apps.kiotviet.infrastructure.services.invoice_mutations_push.get_or_create_customer"


class PushLineTaxFollowsChannelVatTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="kv-vat-user", password="test12345")
        self.channel = Channel.objects.create(code="lp-kv-vat", name="LP KV VAT")
        self.customer = Customer.objects.create(
            full_name="VAT Customer", code="CUST-VAT-001", phone_raw="0909111222", phone_normalized="0909111222"
        )
        self.product = Product.objects.create(name="SP VAT", sku="SKU-VAT", price=Decimal("1490000"))

    def _make(self, **channel_kwargs) -> tuple[Order, KiotVietChannel]:
        kv = KiotVietChannel.objects.create(
            name="KV",
            branch_id="89084",
            branch_ids=["89084"],
            client_id="cid",
            client_secret="sec",
            retailer="ret",
            **channel_kwargs,
        )
        order = Order.objects.create(
            channel=self.channel, customer=self.customer, kiotviet_channel=kv, staff=self.user,
            phone="", address="1 Street", district_code="", province_code="", branch_id="89084",
        )
        OrderItem.objects.create(
            order=order, product=self.product, product_name=self.product.name, sku=self.product.sku,
            quantity=1, unit_price=Decimal("1490000"), line_total=Decimal("1490000"),
        )
        UserKiotvietMapping.objects.create(user=self.user, kiotviet_channel=kv, external_kiot_user_id="1")
        return order, kv

    @patch(_CREATE_ORDER)
    @patch(_GET_OR_CREATE_CUSTOMER)
    def test_channel_without_vat_sends_no_tax_rows_even_when_not_old(self, mock_cust, mock_create):
        mock_cust.return_value = {"code": "CUST-VAT-001"}
        mock_create.return_value = {"id": 1, "code": "DH1"}
        order, kv = self._make(old=False, vat_tax_id=None)

        push_order_to_kiotviet(order, kv)

        payload = mock_create.call_args.args[0]
        self.assertNotIn("OrderDetailTaxs", payload["orderDetails"][0])
        self.assertNotIn("InvoiceDetailTaxs", payload["orderDetails"][0])
        self.assertEqual(payload["pricingMode"], 0)

    @patch(_CREATE_ORDER)
    @patch(_GET_OR_CREATE_CUSTOMER)
    def test_channel_with_vat_sends_configured_tax_id(self, mock_cust, mock_create):
        mock_cust.return_value = {"code": "CUST-VAT-001"}
        mock_create.return_value = {"id": 1, "code": "DH1"}
        order, kv = self._make(old=False, vat_tax_id=3)

        push_order_to_kiotviet(order, kv)

        payload = mock_create.call_args.args[0]
        self.assertEqual(payload["orderDetails"][0]["OrderDetailTaxs"], [{"TaxId": 3}])
        self.assertEqual(payload["pricingMode"], 1)


class BuilderLineTaxFollowsVatTaxIdTests(SimpleTestCase):
    _SOURCE = {
        "code": "HD1",
        "total": 1490000,
        "invoiceDetails": [{"productCode": "SQA03", "productName": "Tu say", "quantity": 1, "price": 1490000}],
    }

    def test_no_vat_tax_id_omits_tax_rows(self):
        rows = build_invoice_details(self._SOURCE, vat_tax_id=None)
        self.assertNotIn("OrderDetailTaxs", rows[0])

    def test_vat_tax_id_adds_tax_rows(self):
        rows = build_invoice_details(self._SOURCE, vat_tax_id=3)
        self.assertEqual(rows[0]["OrderDetailTaxs"], [{"TaxId": 3}])

    def test_payload_pricing_mode_follows_vat_tax_id(self):
        kwargs = dict(
            source_invoice=self._SOURCE, target_branch_id=1, customer_block={"contactNumber": "0900000000"},
            customer_code="C1", customer_name="Khach", source_channel=None, target_channel=None,
        )
        self.assertEqual(build_target_invoice_payload(vat_tax_id=None, **kwargs)["payload"]["pricingMode"], 0)
        self.assertEqual(build_target_invoice_payload(vat_tax_id=3, **kwargs)["payload"]["pricingMode"], 1)


class VatTaxIdMigrationDefaultTests(TestCase):
    def test_new_channel_defaults_to_no_vat(self):
        kv = KiotVietChannel.objects.create(name="x", retailer="r")
        self.assertIsNone(kv.vat_tax_id)
