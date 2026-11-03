import hashlib
import hmac
import json
import uuid
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.test.utils import override_settings
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order


def _reset_event_bus() -> None:
    """Trả bus về đúng bộ subscriber thật (EventBus không có unsubscribe)."""
    from apps.common.events import event_bus
    from apps.inventory import subscribers as inventory_subscribers
    from apps.users import subscribers as users_subscribers

    event_bus.clear()
    inventory_subscribers.register()
    users_subscribers.register()


class KiotVietInvoiceWebhookTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sales_channel = Channel.objects.create(code="lp-webhook", name="Landing Webhook")
        self.customer = Customer.objects.create(
            full_name="Webhook User",
            phone_raw="0909111222",
            phone_normalized="0909111222",
        )
        self.kiot_channel = KiotVietChannel.objects.create(
            name="KV New",
            branch_id="B1",
            retailer="Retailer A",
            client_id="client-a",
            client_secret="secret-abc123",
        )
        self.other_kiot_channel = KiotVietChannel.objects.create(
            name="KV New Other",
            branch_id="B2",
            retailer="Retailer B",
            client_id="client-b",
            client_secret="secret-other-456",
        )
        self.order = Order.objects.create(
            channel=self.sales_channel,
            kiotviet_channel=self.kiot_channel,
            customer=self.customer,
            order_code="DH-10001",
            invoice_code="HD-10001",
            shipping_code="",
            status=1,
        )
        self.order_other_channel = Order.objects.create(
            channel=self.sales_channel,
            kiotviet_channel=self.other_kiot_channel,
            customer=self.customer,
            order_code="DH-10001",
            invoice_code="HD-10001",
            shipping_code="",
            status=1,
        )
        self.path = f"/api/webhook/invoice/channel/{self.kiot_channel.id}"

    @staticmethod
    def _sign(raw_body: bytes, secret: str) -> str:
        return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-abc123")
    def test_invoice_webhook_returns_401_when_signature_invalid(self):
        payload = {
            "Id": f"x-{uuid.uuid4()}",
            "Notifications": [
                {
                    "Action": "Invoice.Update",
                    "Data": [{"Code": "HD-10001"}],
                }
            ],
        }
        raw = json.dumps(payload).encode("utf-8")
        response = self.client.post(
            self.path,
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE="sha256=invalid-signature",
        )

        self.assertEqual(response.status_code, 401)
        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_code, "")
        self.assertEqual(self.order.status, 1)

    @patch("apps.orders.application.services.order_service_impl.OrderService.push_event")
    @patch("apps.dispatch.dispatch")
    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-abc123")
    def test_invoice_webhook_updates_order_and_publishes_event(
        self,
        mock_dispatch,
        push_event,
    ):
        modified_date = "2026-03-31T10:00:00+07:00"
        payload = {
            "Id": f"x-{uuid.uuid4()}",
            "Notifications": [
                {
                    "Action": "Invoice.Update",
                    "Data": [
                        {
                            "Code": "HD-10001",
                            "ModifiedDate": modified_date,
                            "InvoiceDelivery": {
                                "DeliveryCode": "GHN-8899",
                                "Status": 2,
                            },
                        }
                    ],
                }
            ],
        }
        raw = json.dumps(payload).encode("utf-8")
        signature = self._sign(raw, settings.KIOTVIET_WEBHOOK_SECRET)

        response = self.client.post(
            self.path,
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_code, "GHN-8899")
        self.assertEqual(self.order.status, 2)
        self.assertEqual(self.order.updated_at, parse_datetime(modified_date))

        # Sync sang kênh cũ do cron reconcile đảm nhiệm — webhook không dispatch task nào.
        mock_dispatch.assert_not_called()
        push_event.assert_called_once()
        event = push_event.call_args.args[0]
        self.assertEqual(event["type"], "order.updated")
        self.assertEqual(event["order_id"], self.order.id)
        self.assertEqual(event["invoice_code"], "HD-10001")
        self.assertEqual(event["shipping_code"], "GHN-8899")
        self.assertEqual(event["source"], "kiotviet.webhook.invoice")

        self.order_other_channel.refresh_from_db()
        self.assertEqual(self.order_other_channel.shipping_code, "")
        self.assertEqual(self.order_other_channel.status, 1)

    @patch("apps.dispatch.dispatch")
    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-abc123")
    def test_invoice_webhook_only_shipping_code_change_updates_order(
        self,
        mock_dispatch,
    ):
        payload = {
            "Notifications": [
                {
                    "Data": [
                        {
                            "Code": "HD-10001",
                            "InvoiceDelivery": {
                                "DeliveryCode": "GHN-ONLY-SHIPPING",
                                "Status": 1,
                            },
                        }
                    ]
                }
            ]
        }
        raw = json.dumps(payload).encode("utf-8")
        signature = self._sign(raw, settings.KIOTVIET_WEBHOOK_SECRET)

        response = self.client.post(
            self.path,
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_code, "GHN-ONLY-SHIPPING")
        self.assertEqual(self.order.status, 1)
        # Không còn push realtime sang kênh cũ từ webhook (cron reconcile lo việc này).
        mock_dispatch.assert_not_called()

    @patch("apps.dispatch.dispatch")
    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-abc123")
    def test_invoice_webhook_only_status_change_updates_status_without_dispatch(
        self,
        mock_dispatch,
    ):
        payload = {
            "Notifications": [
                {
                    "Data": [
                        {
                            "Code": "HD-10001",
                            "InvoiceDelivery": {
                                "Status": 2,
                            },
                        }
                    ]
                }
            ]
        }
        raw = json.dumps(payload).encode("utf-8")
        signature = self._sign(raw, settings.KIOTVIET_WEBHOOK_SECRET)

        response = self.client.post(
            self.path,
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_code, "")
        self.assertEqual(self.order.status, 2)
        # status=2 (đang giao) không kích hoạt dispatch nào từ webhook.
        mock_dispatch.assert_not_called()

    @patch("apps.orders.application.services.order_service_impl.OrderService.push_event")
    @patch("apps.dispatch.dispatch")
    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-abc123")
    def test_invoice_webhook_delivered_success_updates_status_without_dispatch(
        self,
        mock_dispatch,
        push_event,
    ):
        payload = {
            "Notifications": [
                {
                    "Data": [
                        {
                            "Code": "HD-10001",
                            "InvoiceDelivery": {
                                "Status": 3,
                            },
                        }
                    ]
                }
            ]
        }
        raw = json.dumps(payload).encode("utf-8")
        signature = self._sign(raw, settings.KIOTVIET_WEBHOOK_SECRET)

        response = self.client.post(
            self.path,
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 3)
        mock_dispatch.assert_not_called()
        push_event.assert_called_once()
        event = push_event.call_args.args[0]
        self.assertEqual((event["type"], event["order_id"]), ("order.updated", self.order.id))
        self.assertEqual(event["to_status"], 3)

    @override_settings(KIOTVIET_WEBHOOK_SECRET="")
    def test_invoice_webhook_returns_500_when_env_secret_missing(self):
        payload = {"Notifications": [{"Data": [{"Code": "HD-10001"}]}]}
        raw = json.dumps(payload).encode("utf-8")
        response = self.client.post(
            self.path,
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE="sha256=whatever",
        )
        self.assertEqual(response.status_code, 500)

    @patch("apps.orders.application.services.order_service_impl.OrderService.push_event")
    @patch("apps.kiotviet.infrastructure.services.invoice_sync_queries.get_invoice_by_code")
    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-abc123")
    def test_invoice_webhook_does_not_flip_invoice_status_when_kiotviet_not_cancelled(
        self,
        mock_get_invoice_by_code,
        push_event,
    ):
        """Webhook báo status=2 nhưng KiotViet (đúng channel) xác nhận đơn vẫn bình thường
        → KHÔNG được ghi invoice_status=2 (chống match nhầm mã trùng chéo retailer)."""
        self.order.invoice_status = 3
        self.order.save(update_fields=["invoice_status"])
        mock_get_invoice_by_code.return_value = {"code": "HD-10001", "status": 1}

        payload = {
            "Notifications": [
                {"Data": [{"Code": "HD-10001", "Status": 2}]}
            ]
        }
        raw = json.dumps(payload).encode("utf-8")
        signature = self._sign(raw, settings.KIOTVIET_WEBHOOK_SECRET)

        response = self.client.post(
            self.path,
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.invoice_status, 3)
        mock_get_invoice_by_code.assert_called_once()
        self.assertEqual(mock_get_invoice_by_code.call_args.args[0], "HD-10001")
        self.assertEqual(mock_get_invoice_by_code.call_args.args[2], self.kiot_channel)
        push_event.assert_not_called()

    @patch("apps.orders.application.services.order_service_impl.OrderService.push_event")
    @patch("apps.kiotviet.infrastructure.services.invoice_sync_queries.get_invoice_by_code")
    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-abc123")
    def test_invoice_webhook_applies_cancel_when_kiotviet_confirms(
        self,
        mock_get_invoice_by_code,
        push_event,
    ):
        """Webhook báo status=2 và KiotViet xác nhận status=2 → ghi invoice_status=2."""
        self.order.invoice_status = 3
        self.order.save(update_fields=["invoice_status"])
        mock_get_invoice_by_code.return_value = {"code": "HD-10001", "status": 2}

        payload = {
            "Notifications": [
                {"Data": [{"Code": "HD-10001", "Status": 2}]}
            ]
        }
        raw = json.dumps(payload).encode("utf-8")
        signature = self._sign(raw, settings.KIOTVIET_WEBHOOK_SECRET)

        response = self.client.post(
            self.path,
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.invoice_status, 2)
        mock_get_invoice_by_code.assert_called_once()
        self.assertEqual(mock_get_invoice_by_code.call_args.args[0], "HD-10001")
        self.assertEqual(mock_get_invoice_by_code.call_args.args[2], self.kiot_channel)

    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-abc123")
    def test_invoice_webhook_accepts_base64_literal_secret_signature(self):
        payload = {
            "Notifications": [
                {
                    "Data": [
                        {
                            "Code": "HD-10001",
                            "InvoiceDelivery": {"DeliveryCode": "GHN-B64-LITERAL"},
                        }
                    ]
                }
            ]
        }
        raw = json.dumps(payload).encode("utf-8")
        # Một số bên gửi ký bằng bản Base64 của secret (đã đăng ký với KiotViet)
        # thay vì secret thô — _resolve_webhook_signing_secrets phải sinh cả hai
        # ứng viên từ KIOTVIET_WEBHOOK_SECRET (xem apps/kiotviet/webhook_impl.py).
        signature = self._sign(raw, "c2VjcmV0LWFiYzEyMw==")

        response = self.client.post(
            self.path,
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_code, "GHN-B64-LITERAL")

    @patch("apps.orders.application.services.order_service_impl.OrderService.push_event")
    @patch("apps.dispatch.dispatch")
    @patch("apps.kiotviet.infrastructure.services.invoice_sync_queries.get_invoice_by_code")
    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-abc123")
    def test_invoice_webhook_auto_creates_order_when_missing(
        self,
        mock_get_invoice_by_code,
        mock_dispatch,
        push_event,
    ):
        invoice_code = "HD-WEBHOOK-NEW-01"
        mock_get_invoice_by_code.return_value = {
            "id": 8001,
            "code": invoice_code,
            "orderCode": "DH-WEBHOOK-NEW-01",
            "createdDate": "2026-03-31T09:00:00+07:00",
            "modifiedDate": "2026-03-31T09:30:00+07:00",
            "customerName": "Webhook New Customer",
            "customerPhone": "0909333444",
            "invoiceDelivery": {
                "receiver": "Webhook New Customer",
                "contactNumber": "0909333444",
                "address": "123 Test Street",
                "wardName": "Ward 1",
                "locationName": "District 1, Ho Chi Minh",
                "deliveryCode": "GHN-FROM-IMPORT",
                "status": 1,
            },
            "invoiceDetails": [
                {
                    "productCode": "SKU-WEBHOOK-01",
                    "productName": "Webhook Product",
                    "quantity": 2,
                    "price": 10000,
                    "subTotal": 20000,
                }
            ],
        }

        payload = {
            "Notifications": [
                {
                    "Data": [
                        {
                            "Code": invoice_code,
                            "ModifiedDate": "2026-03-31T10:00:00+07:00",
                            "InvoiceDelivery": {
                                "DeliveryCode": "GHN-FROM-WEBHOOK",
                                "Status": 2,
                            },
                        }
                    ]
                }
            ]
        }
        raw = json.dumps(payload).encode("utf-8")
        signature = self._sign(raw, settings.KIOTVIET_WEBHOOK_SECRET)

        response = self.client.post(
            self.path,
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 200)
        created_order = Order.objects.filter(
            kiotviet_channel=self.kiot_channel,
            invoice_code=invoice_code,
            deleted_at__isnull=True,
        ).first()
        self.assertIsNotNone(created_order)
        self.assertEqual(created_order.status, 2)
        self.assertEqual(created_order.shipping_code, "GHN-FROM-WEBHOOK")

        mock_get_invoice_by_code.assert_called_once()
        # Nhánh auto-create gọi bằng keyword (webhook_impl._auto_create_order).
        self.assertEqual(mock_get_invoice_by_code.call_args.kwargs["invoice_code"], invoice_code)
        self.assertEqual(mock_get_invoice_by_code.call_args.kwargs["channel"], self.kiot_channel)
        # Webhook không dispatch task nào khi status != 3 (sync kênh cũ do cron reconcile).
        mock_dispatch.assert_not_called()
        push_event.assert_called_once()
        event = push_event.call_args.args[0]
        self.assertEqual((event["type"], event["order_id"]), ("order.updated", created_order.id))
        self.assertEqual(event["invoice_code"], invoice_code)
        self.assertEqual(event["shipping_code"], "GHN-FROM-WEBHOOK")

    @patch("apps.orders.application.services.order_service_impl.OrderService.push_event")
    @patch("apps.kiotviet.infrastructure.services.invoice_sync_queries.get_invoice_by_code")
    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-abc123")
    def test_invoice_webhook_cancel_skips_order_matched_only_by_order_code(
        self,
        mock_get_invoice_by_code,
        push_event,
    ):
        """Sự kiện hủy chỉ được áp vào đơn có invoice_code == mã webhook. Đơn match qua
        order_code (va mã) tuyệt đối không được lật — kể cả khi hóa đơn mang mã đó
        thật sự bị hủy trên KiotViet (nó là hóa đơn của đơn khác)."""
        collided = Order.objects.create(
            channel=self.sales_channel,
            kiotviet_channel=self.kiot_channel,
            customer=self.customer,
            order_code="HD-909",  # trùng namespace với mã hóa đơn của đơn khác
            invoice_code="HD-DIFF",
            invoice_status=3,
            status=1,
        )
        payload = {
            "Notifications": [
                {"Data": [{"Code": "HD-909", "Status": 2}]}
            ]
        }
        raw = json.dumps(payload).encode("utf-8")
        signature = self._sign(raw, settings.KIOTVIET_WEBHOOK_SECRET)

        response = self.client.post(
            self.path,
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 200)
        collided.refresh_from_db()
        self.assertEqual(collided.invoice_status, 3)
        self.assertEqual(collided.status, 1)
        # Skip trước cả bước verify → không tốn call API.
        mock_get_invoice_by_code.assert_not_called()
        push_event.assert_not_called()


class KiotVietWebhookReattachReissuedInvoiceTests(TestCase):
    """KiotViet 'hủy → xuất lại hóa đơn' sinh mã `<gốc>.NN`. Webhook phải nối lại đơn cũ
    (repoint invoice_code/invoice_id, giữ CRM) thay vì tạo dòng đơn mới trùng."""

    def setUp(self):
        self.client = APIClient()
        self.sales_channel = Channel.objects.create(code="lp-reissue", name="Landing Reissue")
        self.customer = Customer.objects.create(
            full_name="Reissue User",
            phone_raw="0909555666",
            phone_normalized="0909555666",
        )
        self.kiot_channel = KiotVietChannel.objects.create(
            name="KV Reissue",
            branch_id="B9",
            retailer="Retailer R",
            client_id="client-r",
            client_secret="secret-abc123",
        )
        self.path = f"/api/webhook/invoice/channel/{self.kiot_channel.id}"

    @staticmethod
    def _sign(raw_body: bytes, secret: str) -> str:
        return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    def _make_order(self, **overrides) -> Order:
        defaults = {
            "channel": self.sales_channel,
            "kiotviet_channel": self.kiot_channel,
            "customer": self.customer,
            "order_code": "DH-500",
            "invoice_code": "HD-500",
            "invoice_id": 111,
            "shipping_code": "OLD-SHIP-01",
            "status": 3,
            "invoice_status": 2,
        }
        defaults.update(overrides)
        return Order.objects.create(**defaults)

    @staticmethod
    def _fake_get_invoice(responses: dict):
        """get_invoice_by_code được gọi lúc positional lúc keyword tuỳ call-site."""
        def _fake(*args, **kwargs):
            code = args[0] if args else kwargs.get("invoice_code")
            return responses[code]
        return _fake

    @staticmethod
    def _reissued_payload(code: str, order_code: str = "DH-500") -> dict:
        return {
            "id": 8002,
            "code": code,
            "orderCode": order_code,
            "status": 1,
            "createdDate": "2026-07-06T22:17:23+07:00",
            "modifiedDate": "2026-07-06T22:17:23+07:00",
            "customerName": "Reissue User",
            "customerPhone": "0909555666",
            "invoiceDelivery": {
                "receiver": "Reissue User",
                "contactNumber": "0909555666",
                "address": "456 Reissue Street",
                "deliveryCode": "OLD-SHIP-01",
                "status": 3,
            },
            "invoiceDetails": [
                {
                    "productCode": "SKU-REISSUE-01",
                    "productName": "Reissue Product",
                    "quantity": 1,
                    "price": 1699000,
                    "subTotal": 1699000,
                }
            ],
        }

    def _post_webhook(self, invoice_code: str) -> None:
        payload = {
            "Notifications": [
                {
                    "Data": [
                        {
                            "Code": invoice_code,
                            "InvoiceDelivery": {"DeliveryCode": "OLD-SHIP-01", "Status": 3},
                        }
                    ]
                }
            ]
        }
        raw = json.dumps(payload).encode("utf-8")
        signature = self._sign(raw, settings.KIOTVIET_WEBHOOK_SECRET)
        response = self.client.post(
            self.path,
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, 200)

    @patch("apps.orders.application.services.order_service_impl.OrderService.push_event")
    @patch("apps.kiotviet.infrastructure.services.invoice_sync_queries.get_invoice_by_code")
    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-abc123")
    def test_reattaches_reissued_invoice_to_already_cancelled_order(
        self,
        mock_get_invoice_by_code,
        push_event,
    ):
        """Đơn cũ ĐÃ lật invoice_status=2 → nối thẳng, không cần verify lại bản gốc."""
        order = self._make_order()
        mock_get_invoice_by_code.return_value = self._reissued_payload("HD-500.01")

        self._post_webhook("HD-500.01")

        order.refresh_from_db()
        self.assertEqual(order.invoice_code, "HD-500.01")
        self.assertEqual(order.invoice_id, 8002)
        self.assertEqual(order.invoice_status, 1)
        self.assertEqual(order.status, 3)
        # Không được sinh dòng đơn mới.
        self.assertEqual(
            Order.objects.filter(kiotviet_channel=self.kiot_channel, deleted_at__isnull=True).count(),
            1,
        )
        mock_get_invoice_by_code.assert_called_once()
        self.assertEqual(mock_get_invoice_by_code.call_args[0][0], "HD-500.01")

    @patch("apps.orders.application.services.order_service_impl.OrderService.push_event")
    @patch("apps.kiotviet.infrastructure.services.invoice_sync_queries.get_invoice_by_code")
    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-abc123")
    def test_reattach_verifies_base_cancelled_when_db_not_flipped_yet(
        self,
        mock_get_invoice_by_code,
        push_event,
    ):
        """Thực tế webhook `.01` đến TRƯỚC khi dòng cũ bị lật hủy trong DB → phải hỏi
        KiotViet xác nhận bản gốc đã hủy rồi mới nối."""
        order = self._make_order(invoice_code="HD-501", invoice_status=None)
        responses = {
            "HD-501.01": self._reissued_payload("HD-501.01"),
            "HD-501": {"id": 111, "code": "HD-501", "status": 2},
        }
        mock_get_invoice_by_code.side_effect = self._fake_get_invoice(responses)

        self._post_webhook("HD-501.01")

        order.refresh_from_db()
        self.assertEqual(order.invoice_code, "HD-501.01")
        self.assertEqual(order.invoice_id, 8002)
        self.assertEqual(order.invoice_status, 1)
        self.assertEqual(
            Order.objects.filter(kiotviet_channel=self.kiot_channel, deleted_at__isnull=True).count(),
            1,
        )
        self.assertEqual(mock_get_invoice_by_code.call_count, 2)

    @patch("apps.orders.application.services.order_service_impl.OrderService.push_event")
    @patch("apps.kiotviet.infrastructure.services.invoice_sync_queries.get_invoice_by_code")
    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-abc123")
    def test_reattach_skipped_when_base_invoice_still_alive(
        self,
        mock_get_invoice_by_code,
        push_event,
    ):
        """Bản gốc còn sống trên KiotViet → mã `.01` là chuyện khác, giữ luồng auto-create."""
        order = self._make_order(invoice_code="HD-502", invoice_status=None)
        responses = {
            "HD-502.01": self._reissued_payload("HD-502.01"),
            "HD-502": {"id": 111, "code": "HD-502", "status": 1},
        }
        mock_get_invoice_by_code.side_effect = self._fake_get_invoice(responses)

        self._post_webhook("HD-502.01")

        order.refresh_from_db()
        self.assertEqual(order.invoice_code, "HD-502")  # không bị đụng
        created = Order.objects.filter(
            kiotviet_channel=self.kiot_channel, invoice_code="HD-502.01", deleted_at__isnull=True
        ).first()
        self.assertIsNotNone(created)
        self.assertNotEqual(created.id, order.id)

    @patch("apps.orders.application.services.order_service_impl.OrderService.push_event")
    @patch("apps.kiotviet.infrastructure.services.invoice_sync_queries.get_invoice_by_code")
    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-abc123")
    def test_reattach_skipped_when_order_code_mismatch(
        self,
        mock_get_invoice_by_code,
        push_event,
    ):
        """orderCode của hóa đơn mới khác order_code đơn cũ → không nối (khác đơn thật)."""
        order = self._make_order(invoice_code="HD-503", order_code="DH-503")
        responses = {
            "HD-503.01": self._reissued_payload("HD-503.01", order_code="DH-KHAC"),
        }
        mock_get_invoice_by_code.side_effect = self._fake_get_invoice(responses)

        self._post_webhook("HD-503.01")

        order.refresh_from_db()
        self.assertEqual(order.invoice_code, "HD-503")
        created = Order.objects.filter(
            kiotviet_channel=self.kiot_channel, invoice_code="HD-503.01", deleted_at__isnull=True
        ).first()
        self.assertIsNotNone(created)
        self.assertNotEqual(created.id, order.id)

    @patch("apps.orders.application.services.order_service_impl.OrderService.push_event")
    @patch("apps.kiotviet.infrastructure.services.invoice_sync_queries.get_invoice_by_code")
    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-abc123")
    def test_late_cancel_of_base_code_does_not_create_phantom_order(
        self,
        mock_get_invoice_by_code,
        push_event,
    ):
        """Webhook huỷ của mã GỐC đến SAU reattach: mã gốc không còn thuộc đơn nào →
        trước fix, auto-create sinh một dòng đơn huỷ trùng (đúng thứ reattach muốn diệt).
        Đơn kế thừa giữ mã gốc trong `reattached_invoice_codes` nên phải nhận ra được,
        và guard cancel-theo-invoice_code giữ đơn sống không bị lật (audit 2026-07-14)."""
        order = self._make_order(status=6, invoice_status=2)
        responses = {
            "HD-500.01": self._reissued_payload("HD-500.01"),
            # Payload mã gốc: KiotViet xác nhận đã huỷ.
            "HD-500": {**self._reissued_payload("HD-500"), "status": 2},
        }
        mock_get_invoice_by_code.side_effect = self._fake_get_invoice(responses)

        self._post_webhook("HD-500.01")
        order.refresh_from_db()
        self.assertEqual(order.invoice_code, "HD-500.01")
        self.assertEqual(order.reattached_invoice_codes, ["HD-500"])

        # Webhook huỷ của mã gốc đến muộn.
        self._post_webhook("HD-500")

        self.assertEqual(
            Order.objects.filter(
                kiotviet_channel=self.kiot_channel, deleted_at__isnull=True
            ).count(),
            1,
            "Không được sinh dòng đơn huỷ trùng cho mã gốc đã reattach",
        )
        order.refresh_from_db()
        self.assertEqual(order.invoice_code, "HD-500.01")
        self.assertNotEqual(order.status, 6, "Đơn kế thừa (đang sống) không được lật huỷ")
        self.assertNotEqual(order.invoice_status, 2)

    @patch("apps.orders.application.services.order_service_impl.OrderService.push_event")
    @patch("apps.kiotviet.infrastructure.services.invoice_sync_queries.get_invoice_by_code")
    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-abc123")
    def test_non_cancel_event_of_dead_code_does_not_overwrite_live_order(
        self,
        mock_get_invoice_by_code,
        push_event,
    ):
        """Sự kiện KHÔNG phải huỷ của mã đã chết (vd cập nhật trạng thái giao của hoá đơn
        gốc) không được ghi đè dữ liệu lên đơn kế thừa đang sống — guard cancel cũ chỉ
        chặn nhánh huỷ (audit 2026-07-14 vòng 2)."""
        order = self._make_order(status=6, invoice_status=2)
        mock_get_invoice_by_code.side_effect = self._fake_get_invoice(
            {"HD-500.01": self._reissued_payload("HD-500.01")}
        )
        self._post_webhook("HD-500.01")
        order.refresh_from_db()
        self.assertEqual(order.invoice_code, "HD-500.01")
        live_shipping_code = order.shipping_code
        live_status = order.status

        # Webhook của mã CHẾT mang dữ liệu giao khác (không phải sự kiện huỷ).
        payload = {
            "Notifications": [
                {
                    "Data": [
                        {
                            "Code": "HD-500",
                            "InvoiceDelivery": {"DeliveryCode": "SHIP-CU-DA-CHET", "Status": 2},
                        }
                    ]
                }
            ]
        }
        raw = json.dumps(payload).encode("utf-8")
        response = self.client.post(
            self.path,
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE=self._sign(raw, settings.KIOTVIET_WEBHOOK_SECRET),
        )
        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()
        self.assertEqual(order.shipping_code, live_shipping_code, "Mã vận đơn không được đè")
        self.assertEqual(order.status, live_status, "Trạng thái không được đè")
        self.assertEqual(
            Order.objects.filter(
                kiotviet_channel=self.kiot_channel, deleted_at__isnull=True
            ).count(),
            1,
        )

    @patch("apps.orders.application.services.order_service_impl.OrderService.push_event")
    @patch("apps.kiotviet.infrastructure.services.invoice_sync_queries.get_invoice_by_code")
    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-abc123")
    def test_second_reattach_keeps_same_order(
        self,
        mock_get_invoice_by_code,
        push_event,
    ):
        """Huỷ-xuất lại LẦN 2 (`X` → `X.01` → `X.02`): vẫn nối vào đúng đơn cũ, không sinh
        dòng trùng; cả 2 mã chết đều được lưu vết (audit 2026-07-14 vòng 2)."""
        order = self._make_order(status=6, invoice_status=2)
        mock_get_invoice_by_code.side_effect = self._fake_get_invoice(
            {
                "HD-500.01": self._reissued_payload("HD-500.01"),
                "HD-500.02": {**self._reissued_payload("HD-500.02"), "id": 8003},
                "HD-500.01_verify": {},
            }
        )

        self._post_webhook("HD-500.01")
        order.refresh_from_db()
        self.assertEqual(order.invoice_code, "HD-500.01")

        # Đơn bị huỷ lần nữa rồi xuất lại thành HD-500.02.
        Order.objects.filter(id=order.id).update(status=6, invoice_status=2)
        self._post_webhook("HD-500.02")

        order.refresh_from_db()
        self.assertEqual(order.invoice_code, "HD-500.02", "Phải nối tiếp vào cùng đơn")
        self.assertEqual(order.invoice_id, 8003)
        self.assertCountEqual(
            order.reattached_invoice_codes,
            ["HD-500", "HD-500.01"],
            "Cả mã gốc lẫn mã vừa bị thay đều phải được lưu vết",
        )
        self.assertEqual(
            Order.objects.filter(
                kiotviet_channel=self.kiot_channel, deleted_at__isnull=True
            ).count(),
            1,
            "Reattach lần 2 không được sinh dòng đơn trùng",
        )

    @patch("apps.orders.application.services.order_service_impl.OrderService.push_event")
    @patch("apps.kiotviet.infrastructure.services.invoice_sync_queries.get_invoice_by_code")
    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-abc123")
    def test_skipped_middle_code_is_still_recorded_as_dead(
        self,
        mock_get_invoice_by_code,
        push_event,
    ):
        """`X → X.01 → X.02` mà event của `X.01` KHÔNG kịp xử lý (đến sau `X.02`, hoặc
        fetch timeout): lúc xử lý `X.02` đơn vẫn đang mang `X`, nên nếu chỉ lưu "mã gốc +
        mã đang giữ" thì `X.01` không bao giờ vào mảng — sự kiện huỷ của nó tới muộn sẽ
        sinh dòng đơn huỷ TRÙNG, đúng thứ reattach sinh ra để diệt. Mã chết phải suy từ
        CHUỖI MÃ (audit 2026-07-14 vòng 3).
        """
        order = self._make_order(status=6, invoice_status=2)
        mock_get_invoice_by_code.side_effect = self._fake_get_invoice(
            {"HD-500.02": {**self._reissued_payload("HD-500.02"), "id": 8003}}
        )

        # Bỏ qua hẳn webhook của HD-500.01 — chỉ HD-500.02 tới.
        self._post_webhook("HD-500.02")

        order.refresh_from_db()
        self.assertEqual(order.invoice_code, "HD-500.02")
        self.assertCountEqual(
            order.reattached_invoice_codes,
            ["HD-500", "HD-500.01"],
            "Mã trung gian HD-500.01 phải được coi là đã chết dù chưa từng xử lý",
        )

        # Sự kiện huỷ của mã trung gian tới muộn → phải bị BỎ QUA, không tạo dòng trùng.
        self._post_webhook("HD-500.01")
        self.assertEqual(
            Order.objects.filter(
                kiotviet_channel=self.kiot_channel, deleted_at__isnull=True
            ).count(),
            1,
            "Event của mã trung gian không được sinh dòng đơn mới",
        )

    @patch("apps.orders.application.services.order_service_impl.OrderService.push_event")
    @patch("apps.kiotviet.infrastructure.services.invoice_sync_queries.get_invoice_by_code")
    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-abc123")
    def test_reattach_publishes_status_changed_when_order_revived(
        self,
        mock_get_invoice_by_code,
        push_event,
    ):
        """Hồi sinh đơn đang HUỶ phải phát OrderStatusChanged — kho nghe sự kiện này để
        TRỪ LẠI tồn đã cộng lúc huỷ (audit 2026-07-14 Task 1). `save()` trần không phát
        sự kiện → tồn phồng vĩnh viễn."""
        from apps.common.events import event_bus
        from apps.orders.contracts.order_events import OrderStatusChanged

        captured: list[OrderStatusChanged] = []
        event_bus.subscribe(OrderStatusChanged.event_type, captured.append)
        # Bus là singleton process-wide: gỡ đăng ký test-only bằng clear + register lại
        # subscriber thật (EventBus không có unsubscribe).
        self.addCleanup(_reset_event_bus)

        order = self._make_order(status=6, invoice_status=2)
        mock_get_invoice_by_code.return_value = self._reissued_payload("HD-500.01")

        with self.captureOnCommitCallbacks(execute=True):
            self._post_webhook("HD-500.01")

        order.refresh_from_db()
        self.assertEqual(order.invoice_code, "HD-500.01")
        self.assertEqual(order.status, 3, "Đơn được hồi sinh về trạng thái sống")
        revived = [e for e in captured if e.order_id == order.id and e.old_status == 6]
        self.assertEqual(len(revived), 1, "Phải phát đúng 1 sự kiện hồi sinh 6 → sống")
        self.assertEqual(revived[0].new_status, 3)


class WebhookReplayHardeningTests(TestCase):
    """Hardening audit vòng 5: dedupe theo Id thông báo + trần số item mỗi request."""

    def setUp(self):
        self.client = APIClient()
        self.sales_channel = Channel.objects.create(code="lp-replay", name="Replay Ch")
        self.customer = Customer.objects.create(
            full_name="Replay User",
            phone_raw="0909111333",
            phone_normalized="0909111333",
        )
        self.kiot_channel = KiotVietChannel.objects.create(
            name="KV Replay",
            branch_id="B9",
            retailer="Retailer R",
            client_id="client-r",
            client_secret="secret-replay-1",
        )
        self.order = Order.objects.create(
            channel=self.sales_channel,
            kiotviet_channel=self.kiot_channel,
            customer=self.customer,
            order_code="DH-77001",
            invoice_code="HD-77001",
            shipping_code="",
            status=1,
        )
        self.path = f"/api/webhook/invoice/channel/{self.kiot_channel.id}"

    def _post(self, payload: dict):
        raw = json.dumps(payload).encode("utf-8")
        signature = hmac.new(
            settings.KIOTVIET_WEBHOOK_SECRET.encode("utf-8"), raw, hashlib.sha256
        ).hexdigest()
        return self.client.post(
            self.path,
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE=signature,
        )

    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-replay-1")
    def test_replayed_notification_id_is_skipped(self):
        payload = {
            "Id": f"replay-{uuid.uuid4()}",
            "Notifications": [
                {
                    "Data": [
                        {
                            "Code": "HD-77001",
                            "InvoiceDelivery": {"DeliveryCode": "GHN-R1", "Status": 2},
                        }
                    ]
                }
            ],
        }
        first = self._post(payload)
        self.assertEqual(first.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_code, "GHN-R1")

        second = self._post(payload)
        self.assertEqual(second.status_code, 200)
        self.assertIn("Duplicate", second.json()["detail"])

    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-replay-1")
    def test_payload_without_id_is_not_deduped(self):
        payload = {
            "Notifications": [
                {
                    "Data": [
                        {
                            "Code": "HD-77001",
                            "InvoiceDelivery": {"DeliveryCode": "GHN-R2", "Status": 2},
                        }
                    ]
                }
            ]
        }
        self.assertEqual(self._post(payload).status_code, 200)
        second = self._post(payload)
        self.assertEqual(second.status_code, 200)
        self.assertNotIn("Duplicate", second.json().get("detail", ""))

    @override_settings(
        KIOTVIET_WEBHOOK_SECRET="secret-replay-1", KIOTVIET_WEBHOOK_MAX_ITEMS=2
    )
    def test_too_many_items_rejected(self):
        payload = {
            "Id": f"bomb-{uuid.uuid4()}",
            "Notifications": [
                {"Data": [{"Code": f"HD-BOMB-{i}"} for i in range(3)]}
            ],
        }
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Too many items", resp.json()["detail"])
