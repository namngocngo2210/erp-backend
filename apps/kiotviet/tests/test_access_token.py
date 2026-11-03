from unittest.mock import Mock, call, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.kiotviet.models import KiotVietAccessToken, KiotVietChannel
from apps.kiotviet.infrastructure.services.common_auth_customer import (
    _jwt_expires_in_seconds,
    get_web_access_token,
    invalidate_web_access_token,
)
from apps.kiotviet.infrastructure.services.invoice_mutations_push import _koc_payment_override
from apps.kiotviet.services import _kiotviet_web_request, get_access_token, push_order_to_kiotviet
from apps.orders.models import Order, OrderItem
from apps.catalog.models import Product
from apps.users.models import UserKiotvietMapping


class KiotVietAccessTokenTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="lp-kiotviet", name="LP KiotViet")
        self.customer = Customer.objects.create(
            full_name="Token Customer",
            phone_raw="0909000000",
            phone_normalized="0909000000",
        )
        self.kiotviet_channel = KiotVietChannel.objects.create(
            name="KV Test",
            branch_id="231174",
            branch_ids=["231174"],
            client_id="kv-client-id",
            client_secret="kv-client-secret",
            retailer="kv-retailer",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            kiotviet_channel=self.kiotviet_channel,
        )

    def test_get_access_token_uses_cached_token_when_not_expired(self):
        KiotVietAccessToken.objects.create(
            channel=self.kiotviet_channel,
            access_token="cached-token",
            expires_in=3600,
            token_type="Bearer",
            scope="PublicApi.Access",
        )

        with patch(
            "apps.kiotviet.infrastructure.services.common_auth_customer.http_read.post"
        ) as mock_post:
            token = get_access_token(order=self.order)

        self.assertEqual(token, "cached-token")
        mock_post.assert_not_called()

    @patch("apps.kiotviet.infrastructure.services.common_auth_customer.http_read.post")
    def test_get_access_token_requests_new_token_when_cache_expired(self, mock_post):
        expired = KiotVietAccessToken.objects.create(
            channel=self.kiotviet_channel,
            access_token="expired-token",
            expires_in=60,
            token_type="Bearer",
            scope="PublicApi.Access",
        )
        KiotVietAccessToken.objects.filter(pk=expired.pk).update(
            created_at=timezone.now() - timezone.timedelta(hours=2)
        )

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-token",
            "expires_in": 1800,
            "token_type": "Bearer",
            "scope": "PublicApi.Access",
        }
        mock_post.return_value = mock_response

        token = get_access_token(order=self.order)

        self.assertEqual(token, "new-token")
        mock_post.assert_called_once()
        called_data = mock_post.call_args.kwargs["data"]
        self.assertEqual(called_data["scopes"], "PublicApi.Access")
        self.assertEqual(called_data["grant_type"], "client_credentials")
        self.assertEqual(called_data["client_id"], self.kiotviet_channel.client_id)
        self.assertEqual(called_data["client_secret"], self.kiotviet_channel.client_secret)

        latest = (
            KiotVietAccessToken.objects.filter(channel=self.kiotviet_channel)
            .order_by("-created_at")
            .first()
        )
        self.assertIsNotNone(latest)
        self.assertEqual(latest.access_token, "new-token")

    def test_get_access_token_prefers_explicit_channel_over_order_channel(self):
        old_channel = KiotVietChannel.objects.create(
            name="KV Old",
            branch_id="231175",
            branch_ids=["231175"],
            client_id="kv-old-client-id",
            client_secret="kv-old-client-secret",
            retailer="kv-old-retailer",
            old=True,
        )

        KiotVietAccessToken.objects.create(
            channel=self.kiotviet_channel,
            access_token="new-channel-token",
            expires_in=3600,
            token_type="Bearer",
            scope="PublicApi.Access",
        )
        KiotVietAccessToken.objects.create(
            channel=old_channel,
            access_token="old-channel-token",
            expires_in=3600,
            token_type="Bearer",
            scope="PublicApi.Access",
        )

        token = get_access_token(order=self.order, channel=old_channel)

        self.assertEqual(token, "old-channel-token")


class KiotVietPushOrderCustomerFlowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="kv-push-user",
            password="test12345",
            old_kiot_id="2222",
            new_kiot_id="1111",
        )
        self.channel = Channel.objects.create(code="lp-kiotviet-push", name="LP KiotViet Push")
        self.customer = Customer.objects.create(
            full_name="Push Customer",
            code="CUST-LOCAL-001",
            phone_raw="0909888777",
            phone_normalized="0909888777",
            gender=1,
        )
        self.kiotviet_channel = KiotVietChannel.objects.create(
            name="KV Push Test",
            branch_id="231174",
            branch_ids=["231174"],
            client_id="kv-client-id",
            client_secret="kv-client-secret",
            retailer="kv-retailer",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            kiotviet_channel=self.kiotviet_channel,
            staff=self.user,
            phone="",
            address="123 Street",
            district_code="",
            province_code="",
            # push_order_to_kiotviet yêu cầu mã kho (raise "Thiếu mã kho" nếu thiếu).
            branch_id="231174",
        )
        # soldById resolve qua UserKiotvietMapping theo (staff, kv_channel),
        # không còn đọc old_kiot_id/new_kiot_id trực tiếp từ User.
        UserKiotvietMapping.objects.create(
            user=self.user,
            kiotviet_channel=self.kiotviet_channel,
            external_kiot_user_id="1111",
        )

    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.create_order")
    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.get_or_create_customer")
    def test_push_order_uses_customer_code_and_checks_customer_first(self, mock_get_or_create_customer, mock_create_order):
        mock_get_or_create_customer.return_value = {"code": "CUST-LOCAL-001"}
        mock_create_order.return_value = {"id": 123456, "code": "OD-001"}

        result = push_order_to_kiotviet(self.order, self.kiotviet_channel)

        self.assertTrue(result["success"])
        mock_get_or_create_customer.assert_called_once()
        customer_payload, customer_order, customer_channel = mock_get_or_create_customer.call_args.args
        self.assertEqual(customer_payload["code"], self.customer.code)
        self.assertEqual(customer_payload["name"], self.customer.full_name)
        self.assertEqual(customer_payload["contactNumber"], self.customer.phone_raw)
        self.assertEqual((customer_order, customer_channel), (self.order, self.kiotviet_channel))

        mock_create_order.assert_called_once()
        order_payload, pushed_order, pushed_channel = mock_create_order.call_args.args
        self.assertEqual(order_payload["customerCode"], self.customer.code)
        self.assertEqual(order_payload["soldById"], 1111)
        self.assertEqual((pushed_order, pushed_channel), (self.order, self.kiotviet_channel))

    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.create_order")
    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.get_or_create_customer")
    def test_push_order_sends_line_discount_per_unit(self, mock_get_or_create_customer, mock_create_order):
        from decimal import Decimal

        from apps.catalog.models import Product
        from apps.orders.models import OrderItem

        product = Product.objects.create(name="SP KV disc", sku="SKU-KV-DISC", price=Decimal("100000"))
        OrderItem.objects.create(
            order=self.order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            quantity=2,
            unit_price=Decimal("100000"),
            discount=Decimal("15000.50"),
            line_total=Decimal("169999"),
        )
        mock_get_or_create_customer.return_value = {"code": "CUST-LOCAL-001"}
        mock_create_order.return_value = {"id": 123458, "code": "OD-003"}

        push_order_to_kiotviet(self.order, self.kiotviet_channel)

        detail = mock_create_order.call_args.args[0]["orderDetails"][0]
        self.assertEqual(detail["price"], 100000)
        # KV nhận giảm giá theo MỖI sản phẩm, số nguyên VNĐ.
        self.assertEqual(detail["discount"], 15000)

    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.create_order")
    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.get_or_create_customer")
    def test_push_order_resolves_sold_by_from_order_staff_only(
        self,
        mock_get_or_create_customer,
        mock_create_order,
    ):
        user_model = get_user_model()
        requester = user_model.objects.create_user(
            username="kv-requester-no-kiot",
            password="test12345",
            old_kiot_id="",
            new_kiot_id="",
        )
        mock_get_or_create_customer.return_value = {"code": "CUST-LOCAL-001"}
        mock_create_order.return_value = {"id": 123457, "code": "OD-002"}

        push_order_to_kiotviet(
            self.order,
            self.kiotviet_channel,
            requested_by_id=requester.id,
        )

        order_payload = mock_create_order.call_args.args[0]
        self.assertEqual(order_payload["soldById"], 1111)

    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.create_order")
    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.create_invoice")
    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.find_customer_by_code")
    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.find_customer_by_phone")
    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.get_or_create_customer")
    def test_push_order_old_channel_prefers_customer_from_old_kiotviet(
        self,
        mock_get_or_create_customer,
        mock_find_customer_by_phone,
        mock_find_customer_by_code,
        mock_create_invoice,
        mock_create_order,
    ):
        old_kv_channel = KiotVietChannel.objects.create(
            name="KV Old",
            branch_id="231175",
            branch_ids=["231175"],
            client_id="kv-old-client-id",
            client_secret="kv-old-client-secret",
            retailer="kv-old-retailer",
            old_sync_branch_id="998877",
            old=True,
        )
        self.order.kiotviet_channel = old_kv_channel
        self.order.save(update_fields=["kiotviet_channel", "updated_at"])
        UserKiotvietMapping.objects.create(
            user=self.user,
            kiotviet_channel=old_kv_channel,
            external_kiot_user_id="2222",
        )

        mock_find_customer_by_phone.return_value = {
            "id": 200,
            "code": "CUST-LOCAL-001",
            "name": "Name From Old Kiot",
            "contactNumber": "0911222333",
            "address": "Old Kiot Address",
        }
        mock_get_or_create_customer.return_value = {
            "id": 100,
            "code": "CUST-LOCAL-001",
            "name": "Name From New Context",
        }
        mock_find_customer_by_code.return_value = {
            "id": 200,
            "code": "CUST-LOCAL-001",
            "name": "Name From Old Kiot",
            "contactNumber": "0911222333",
            "address": "Old Kiot Address",
        }
        product = Product.objects.create(name="Old Sync Product", sku="SKU-OLD-001", price=90000)
        OrderItem.objects.create(
            order=self.order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            quantity=1,
            unit_price=50000,
            line_total=50000,
        )
        mock_create_order.return_value = {"id": 999, "code": "OD-OLD-001"}

        push_order_to_kiotviet(self.order, old_kv_channel)

        mock_find_customer_by_phone.assert_called_once()
        phone_call_kwargs = mock_find_customer_by_phone.call_args.kwargs
        self.assertEqual(phone_call_kwargs["channel"], old_kv_channel)
        self.assertEqual(phone_call_kwargs["order"], self.order)
        self.assertEqual(phone_call_kwargs["phone"], self.customer.phone_raw)
        # Đã tìm thấy customer theo phone → không cần re-fetch theo code.
        mock_find_customer_by_code.assert_not_called()
        # Kênh cũ giờ cũng push qua create_order (makeInvoice=True), không dùng create_invoice.
        mock_create_invoice.assert_not_called()
        mock_create_order.assert_called_once()
        order_payload = mock_create_order.call_args.args[0]
        self.assertEqual(mock_create_order.call_args.args[1:], (self.order, old_kv_channel))
        self.assertEqual(order_payload["customer"]["id"], 200)
        self.assertEqual(order_payload["branchId"], 998877)
        self.assertTrue(order_payload["makeInvoice"])
        self.assertEqual(order_payload["soldById"], 2222)
        self.assertNotIn("accountId", order_payload)
        self.assertNotIn("InvoiceDetailTaxs", order_payload["orderDetails"][0])

    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.create_order")
    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.create_invoice")
    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.find_customer_by_code")
    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.find_customer_by_phone")
    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.get_or_create_customer")
    def test_push_order_old_channel_new_customer_uses_dot1_suffix(
        self,
        mock_get_or_create_customer,
        mock_find_customer_by_phone,
        mock_find_customer_by_code,
        mock_create_invoice,
        mock_create_order,
    ):
        old_kv_channel = KiotVietChannel.objects.create(
            name="KV Old Create New Customer",
            branch_id="231175",
            branch_ids=["231175"],
            client_id="kv-old-client-id",
            client_secret="kv-old-client-secret",
            retailer="kv-old-retailer",
            old_sync_branch_id="998877",
            old=True,
        )
        self.order.kiotviet_channel = old_kv_channel
        self.order.save(update_fields=["kiotviet_channel", "updated_at"])

        mock_find_customer_by_phone.return_value = None
        mock_find_customer_by_code.return_value = {
            "id": 300,
            "code": "CUST-LOCAL-001.1",
            "name": "Name From Old Kiot",
            "contactNumber": "0911222333",
            "address": "Old Kiot Address",
        }
        mock_get_or_create_customer.return_value = {
            "id": 300,
            "code": "CUST-LOCAL-001.1",
            "name": "Created On Old Kiot",
        }
        product = Product.objects.create(name="Old Sync Product .1", sku="SKU-OLD-002", price=90000)
        OrderItem.objects.create(
            order=self.order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            quantity=1,
            unit_price=50000,
            line_total=50000,
        )
        mock_create_order.return_value = {"id": 1000, "code": "OD-OLD-002"}

        push_order_to_kiotviet(self.order, old_kv_channel)

        mock_get_or_create_customer.assert_called_once()
        customer_payload = mock_get_or_create_customer.call_args.args[0]
        self.assertEqual(customer_payload["code"], "CUST-LOCAL-001.1")
        self.assertEqual(mock_get_or_create_customer.call_args.args[1:], (self.order, old_kv_channel))
        mock_find_customer_by_phone.assert_called_once()
        self.assertEqual(mock_find_customer_by_phone.call_args.kwargs["order"], self.order)
        self.assertEqual(mock_find_customer_by_phone.call_args.kwargs["channel"], old_kv_channel)
        mock_find_customer_by_code.assert_called_once_with(
            customer_code="CUST-LOCAL-001.1",
            order=self.order,
            channel=old_kv_channel,
        )
        # Kênh cũ giờ cũng push qua create_order, không dùng create_invoice.
        mock_create_invoice.assert_not_called()
        mock_create_order.assert_called_once()
        order_payload = mock_create_order.call_args.args[0]
        self.assertEqual(mock_create_order.call_args.args[1:], (self.order, old_kv_channel))
        self.assertEqual(order_payload["customer"]["id"], 300)

    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.create_order")
    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.get_or_create_customer")
    def test_push_order_payload_price_mapping_new_rules(
        self,
        mock_get_or_create_customer,
        mock_create_order,
    ):
        self.order.total_after_discount = 123456
        self.order.total_amount = 888888
        self.order.shipping_fee = 22222
        self.order.customer_prepaid = 0
        self.order.save(update_fields=["total_after_discount", "total_amount", "shipping_fee", "customer_prepaid", "updated_at"])

        product = Product.objects.create(name="Demo Product", sku="SKU-DEMO-01", price=99999)
        OrderItem.objects.create(
            order=self.order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            quantity=2,
            unit_price=77777,
            line_total=155554,
        )

        mock_get_or_create_customer.return_value = {"code": "CUST-LOCAL-001"}
        mock_create_order.return_value = {"id": 321, "code": "OD-PRICE-001"}

        push_order_to_kiotviet(self.order, self.kiotviet_channel)

        order_payload = mock_create_order.call_args.args[0]
        self.assertEqual(order_payload["orderDetails"][0]["price"], 77777)
        self.assertEqual(order_payload["orderDelivery"]["price"], 0)
        self.assertEqual(order_payload["orderDelivery"]["codAmount"], 123456)
        self.assertEqual(order_payload["orderDelivery"]["receiveMoney"], 123456)
        self.assertEqual(order_payload["soldById"], 1111)

    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.create_order")
    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.get_or_create_customer")
    def test_push_koc_has_return_no_cod_and_debt(self, mock_get_or_create_customer, mock_create_order):
        from django.conf import settings

        koc_channel = Channel.objects.create(
            id=settings.KOC_CHANNEL_ID, code="koc", name="hang mau koc"
        )
        self.order.channel = koc_channel
        self.order.order_type = "has_return"
        self.order.total_after_discount = 100000
        self.order.customer_prepaid = 0
        self.order.branch_id = "231174"
        self.order.save(update_fields=["channel", "order_type", "total_after_discount", "customer_prepaid", "branch_id", "updated_at"])

        product = Product.objects.create(name="KOC Sample", sku="SKU-KOC-01", price=100000)
        OrderItem.objects.create(
            order=self.order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            quantity=1,
            unit_price=100000,
            line_total=100000,
        )

        mock_get_or_create_customer.return_value = {"code": "CUST-LOCAL-001"}
        mock_create_order.return_value = {"id": 999, "code": "OD-KOC-01"}

        push_order_to_kiotviet(self.order, self.kiotviet_channel)

        order_payload = mock_create_order.call_args.args[0]
        self.assertFalse(order_payload["orderDelivery"]["isCod"])
        self.assertEqual(order_payload["orderDelivery"]["codAmount"], 0)
        self.assertEqual(order_payload["orderDelivery"]["receiveMoney"], 0)
        self.assertEqual(order_payload["totalPayment"], 0)

    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.create_order")
    @patch("apps.kiotviet.infrastructure.services.invoice_mutations_push.get_or_create_customer")
    def test_push_koc_no_return_never_charges_cod_even_if_prepaid_stale(
        self, mock_get_or_create_customer, mock_create_order
    ):
        """Đơn KOC "không trả lại" có customer_prepaid lệch tổng tiền (đổi loại đơn
        hoặc sửa tiền sau khi tạo, import Excel) vẫn phải không thu COD."""
        from django.conf import settings

        koc_channel = Channel.objects.create(
            id=settings.KOC_CHANNEL_ID, code="koc", name="hang mau koc"
        )
        self.order.channel = koc_channel
        self.order.order_type = "no_return"
        self.order.total_after_discount = 100000
        self.order.customer_prepaid = 0  # prepaid chưa được set khớp tổng tiền
        self.order.branch_id = "231174"
        self.order.save(
            update_fields=[
                "channel", "order_type", "total_after_discount",
                "customer_prepaid", "branch_id", "updated_at",
            ]
        )

        product = Product.objects.create(name="KOC Keep", sku="SKU-KOC-02", price=100000)
        OrderItem.objects.create(
            order=self.order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            quantity=1,
            unit_price=100000,
            line_total=100000,
        )

        mock_get_or_create_customer.return_value = {"code": "CUST-LOCAL-001"}
        mock_create_order.return_value = {"id": 1000, "code": "OD-KOC-02"}

        push_order_to_kiotviet(self.order, self.kiotviet_channel)

        order_payload = mock_create_order.call_args.args[0]
        self.assertFalse(order_payload["orderDelivery"]["isCod"])
        self.assertFalse(order_payload["orderDelivery"]["usingCod"])
        self.assertEqual(order_payload["orderDelivery"]["codAmount"], 0)
        self.assertEqual(order_payload["orderDelivery"]["receiveMoney"], 0)
        self.assertEqual(order_payload["totalPayment"], 100000)


class KocPaymentOverrideTests(SimpleTestCase):
    """Helper thuần quyết định COD/thanh toán theo loại đơn KOC."""

    def test_no_return_is_fully_paid_without_cod(self):
        self.assertEqual(
            _koc_payment_override(True, "no_return", 250000),
            {"is_cod": False, "cod_amount": 0, "total_payment": 250000},
        )

    def test_has_return_leaves_debt_without_cod(self):
        self.assertEqual(
            _koc_payment_override(True, "has_return", 250000),
            {"is_cod": False, "cod_amount": 0, "total_payment": 0},
        )

    def test_normal_koc_order_keeps_default_behaviour(self):
        self.assertIsNone(_koc_payment_override(True, "normal", 250000))

    def test_non_koc_channel_is_untouched(self):
        self.assertIsNone(_koc_payment_override(False, "no_return", 250000))
        self.assertIsNone(_koc_payment_override(False, "has_return", 250000))


class KiotVietWebRequestHeaderTests(TestCase):
    def setUp(self):
        self.channel = KiotVietChannel.objects.create(
            name="KV Web Request",
            branch_id="231174",
            branch_ids=["231174"],
            client_id="kv-client-id",
            client_secret="kv-client-secret",
            retailer="kv-retailer",
        )

    # _kiotviet_web_request GET đi qua http_read (client đọc, có retry) — patch method
    # trên instance dùng chung thay vì requests.request trần.
    @patch("apps.kiotviet.infrastructure.services.common_auth_customer.http_read.request")
    @patch("apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook.get_web_access_token")
    def test_web_request_sets_token_header_and_overwrites_caller_authorization(
        self, mock_get_web_access_token, mock_request
    ):
        mock_get_web_access_token.return_value = "web-token-123"
        mock_response = Mock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        _kiotviet_web_request(
            channel=self.channel,
            method="GET",
            endpoint="/api/deliverypartner/getall",
            headers={"Authorization": "Bearer old-token"},
        )

        sent_headers = mock_request.call_args.kwargs["headers"]
        self.assertEqual(sent_headers.get("Token"), "web-token-123")
        # Caller-provided Authorization is overwritten with the web token bearer.
        self.assertEqual(sent_headers.get("Authorization"), "Bearer web-token-123")
        self.assertEqual(sent_headers.get("retailer"), "kv-retailer")


class KiotVietWebTokenCacheTests(TestCase):
    def setUp(self):
        self.channel = KiotVietChannel.objects.create(
            name="KV Web Cache",
            branch_id="231174",
            branch_ids=["231174"],
            client_id="kv-client-id",
            client_secret="kv-client-secret",
            retailer="kv-retailer",
            username="user",
            password="pass",
        )

    def _make_jwt(self, iat: int, exp: int) -> str:
        import base64
        import json as _json

        header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(
            _json.dumps({"iat": iat, "exp": exp}).encode()
        ).rstrip(b"=").decode()
        return f"{header}.{body}.sig"

    def test_jwt_expires_in_seconds_uses_exp_minus_iat(self):
        token = self._make_jwt(iat=1_000_000, exp=1_000_000 + 3600)
        self.assertEqual(_jwt_expires_in_seconds(token), 3600)

    def test_jwt_expires_in_seconds_falls_back_when_unparseable(self):
        self.assertEqual(_jwt_expires_in_seconds("not-a-jwt", fallback_seconds=42), 42)
        self.assertEqual(_jwt_expires_in_seconds("", fallback_seconds=99), 99)

    def test_invalidate_web_access_token_removes_only_web_scope(self):
        KiotVietAccessToken.objects.create(
            channel=self.channel,
            access_token="web-cached",
            expires_in=3600,
            token_type="Bearer",
            scope="web",
        )
        KiotVietAccessToken.objects.create(
            channel=self.channel,
            access_token="public-cached",
            expires_in=3600,
            token_type="Bearer",
            scope="PublicApi.Access",
        )

        deleted = invalidate_web_access_token(self.channel)

        self.assertEqual(deleted, 1)
        self.assertFalse(
            KiotVietAccessToken.objects.filter(channel=self.channel, scope="web").exists()
        )
        self.assertTrue(
            KiotVietAccessToken.objects.filter(
                channel=self.channel, scope="PublicApi.Access"
            ).exists()
        )

    @patch(
        # Login web là mutation (đếm lượt đăng nhập) → đi qua http_mutate, KHÔNG retry.
        "apps.kiotviet.infrastructure.services.common_auth_customer.http_mutate.post"
    )
    def test_force_refresh_drops_cache_and_re_logins_with_jwt_ttl(self, mock_post):
        cached = KiotVietAccessToken.objects.create(
            channel=self.channel,
            access_token="stale-cached",
            expires_in=86400,
            token_type="Bearer",
            scope="web",
        )

        fresh_jwt = self._make_jwt(iat=2_000_000, exp=2_000_000 + 1800)
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '{"access_token":"' + fresh_jwt + '"}'
        mock_response.json.return_value = {"access_token": fresh_jwt, "token_type": "Bearer"}
        mock_post.return_value = mock_response

        token = get_web_access_token(channel=self.channel, force_refresh=True)

        self.assertEqual(token, fresh_jwt)
        self.assertFalse(KiotVietAccessToken.objects.filter(pk=cached.pk).exists())
        latest = (
            KiotVietAccessToken.objects.filter(channel=self.channel, scope="web")
            .order_by("-created_at")
            .first()
        )
        self.assertIsNotNone(latest)
        self.assertEqual(latest.access_token, fresh_jwt)
        self.assertEqual(latest.expires_in, 1800)
