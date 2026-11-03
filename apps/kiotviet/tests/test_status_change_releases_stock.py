"""Tests: KiotViet sync paths publish OrderStatusChanged when order.status changes.

Strategy: patch event_bus.publish_on_commit and assert it is called with the correct
OrderStatusChanged event. This is reliable without invoking real KiotViet API calls.

Sites covered:
  1. webhook_impl.py   — delivery status 5 (RETURNED) via Invoice.Update webhook
  2. service_actions.py ACTION_PUSH       — invoice_status synced from KiotViet
  3. service_actions.py ACTION_SYNC_SHIPPING — delivery_status synced from invoice
  4. service_actions.py ACTION_CREATE_INVOICE — order set to DELIVERED_SUCCESS
  5. service_actions.py cancel_invoice       — order set to CANCELLED
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch

from django.test import TestCase
from django.test.utils import override_settings

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.kiotviet.models import KiotVietChannel
from apps.orders.contracts.order_events import OrderStatusChanged, StatusChangeSource
from apps.orders.models import Order, OrderStatus


def _assert_status_changed_once(mock_publish, expected_event):
    """Chỉ soi OrderStatusChanged: Order.save() còn publish OrderSaved qua cùng bus (2026-09-23)."""
    status_events = [c.args[0] for c in mock_publish.call_args_list if isinstance(c.args[0], OrderStatusChanged)]
    assert status_events == [expected_event], mock_publish.call_args_list


def _make_kv_channel(**kwargs) -> KiotVietChannel:
    defaults = {
        "name": "KV Test",
        "branch_id": "B99",
        "retailer": "RetailerTest",
        "client_id": "cli-test",
        "client_secret": "secret-test-xyz",
    }
    defaults.update(kwargs)
    return KiotVietChannel.objects.create(**defaults)


def _make_order(
    channel: Channel,
    kv_channel: KiotVietChannel,
    customer: Customer,
    *,
    status: int = 1,
    invoice_code: str = "HD-TEST-001",
    invoice_id: int | None = 9001,
    **kwargs,
) -> Order:
    return Order.objects.create(
        channel=channel,
        kiotviet_channel=kv_channel,
        customer=customer,
        name="Test KV Customer",
        phone="0909000099",
        invoice_code=invoice_code,
        invoice_id=invoice_id,
        status=status,
        **kwargs,
    )


def _webhook_sign(raw: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Webhook path (webhook_impl.py)
# ---------------------------------------------------------------------------

class WebhookPublishesOrderStatusChangedTests(TestCase):
    """Webhook delivery-status change → event_bus.publish_on_commit called."""

    def setUp(self):
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.sales_channel = Channel.objects.create(code="lp-kv-evt", name="KV Event Test")
        self.customer = Customer.objects.create(
            full_name="KV Event User",
            phone_raw="0909111333",
            phone_normalized="0909111333",
        )
        self.kv_channel = _make_kv_channel()
        self.order = _make_order(
            self.sales_channel,
            self.kv_channel,
            self.customer,
            status=3,  # currently DELIVERED_SUCCESS
        )
        self.path = f"/api/webhook/invoice/channel/{self.kv_channel.id}"

    @patch("apps.common.events.event_bus.publish_on_commit")
    @patch("apps.kiotviet.webhook_impl._verify_invoice_cancelled_on_channel", return_value=True)
    @patch("apps.orders.application.services.order_service_impl.OrderService.push_event")
    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-test-xyz")
    def test_webhook_delivery_status_5_publishes_OrderStatusChanged(
        self, push_event, mock_verify, mock_publish
    ):
        """Webhook nhận delivery status=5 (RETURNED) → publish_on_commit được gọi
        với OrderStatusChanged(order_id, old=3, new=5)."""
        payload = {
            "Notifications": [
                {
                    "Data": [
                        {
                            "Code": "HD-TEST-001",
                            "InvoiceDelivery": {"Status": 5},
                        }
                    ]
                }
            ]
        }
        raw = json.dumps(payload).encode("utf-8")
        sig = _webhook_sign(raw, "secret-test-xyz")

        response = self.client.post(
            self.path,
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE=sig,
        )

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 5)

        expected_event = OrderStatusChanged(
            order_id=self.order.id,
            old_status=3,
            new_status=5,
            source=StatusChangeSource.KIOTVIET_WEBHOOK,
        )
        _assert_status_changed_once(mock_publish, expected_event)

    @patch("apps.common.events.event_bus.publish_on_commit")
    @patch("apps.orders.application.services.order_service_impl.OrderService.push_event")
    @override_settings(KIOTVIET_WEBHOOK_SECRET="secret-test-xyz")
    def test_webhook_no_status_change_does_not_publish(self, push_event, mock_publish):
        """Webhook không đổi status (shipping code thay đổi, status giữ nguyên)
        → publish_on_commit KHÔNG được gọi với OrderStatusChanged."""
        payload = {
            "Notifications": [
                {
                    "Data": [
                        {
                            "Code": "HD-TEST-001",
                            "InvoiceDelivery": {
                                "DeliveryCode": "GHN-NOCHANGE",
                                "Status": 3,  # same as current order.status=3
                            },
                        }
                    ]
                }
            ]
        }
        raw = json.dumps(payload).encode("utf-8")
        sig = _webhook_sign(raw, "secret-test-xyz")

        response = self.client.post(
            self.path,
            data=raw,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE=sig,
        )

        self.assertEqual(response.status_code, 200)
        # No status change → no OrderStatusChanged event
        for c in mock_publish.call_args_list:
            event_arg = c.args[0] if c.args else None
            self.assertNotIsInstance(event_arg, OrderStatusChanged)


# ---------------------------------------------------------------------------
# service_actions.py — ACTION_PUSH
# ---------------------------------------------------------------------------

class ServiceActionsPushPublishesOrderStatusChangedTests(TestCase):
    """ACTION_PUSH: sync delivery status from invoice → publish OrderStatusChanged."""

    def setUp(self):
        self.sales_channel = Channel.objects.create(code="lp-kv-sa-push", name="KV SA Push")
        self.customer = Customer.objects.create(
            full_name="KV SA Push User",
            phone_raw="0909222444",
            phone_normalized="0909222444",
        )
        self.kv_channel = _make_kv_channel(
            name="KV SA Push",
            branch_id="B98",
            client_id="cli-sa-push",
            client_secret="secret-sa-push",
        )
        self.order = _make_order(
            self.sales_channel,
            self.kv_channel,
            self.customer,
            status=1,
            invoice_code="",
            invoice_id=None,
        )

    @patch("apps.common.events.event_bus.publish_on_commit")
    @patch("apps.kiotviet.infrastructure.services.service_actions.get_invoice_by_code")
    @patch("apps.kiotviet.infrastructure.services.service_actions.push_order_to_kiotviet")
    def test_action_push_status_change_publishes_event(
        self, mock_push, mock_get_invoice, mock_publish
    ):
        """ACTION_PUSH: invoice sync trả delivery status=5 → publish OrderStatusChanged(old=1, new=5)."""
        from apps.kiotviet.infrastructure.services.service_actions import KiotVietService

        mock_push.return_value = {
            "order_result": {
                "code": "DH-PUSH-001",
                "invoiceCode": "HD-PUSH-001",
                "invoiceId": 8100,
                "invoices": [{"invoiceCode": "HD-PUSH-001", "invoiceId": 8100}],
            },
            "make_invoice": False,
        }
        mock_get_invoice.return_value = {
            "code": "HD-PUSH-001",
            "status": 1,
            "invoiceDelivery": {"status": 5},
        }

        KiotVietService.execute_action(self.order, KiotVietService.ACTION_PUSH)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 5)

        expected_event = OrderStatusChanged(
            order_id=self.order.id,
            old_status=1,
            new_status=5,
            source=StatusChangeSource.KIOTVIET_ACTION,
        )
        _assert_status_changed_once(mock_publish, expected_event)

    @patch("apps.common.events.event_bus.publish_on_commit")
    @patch("apps.kiotviet.infrastructure.services.service_actions.get_invoice_by_code")
    @patch("apps.kiotviet.infrastructure.services.service_actions.push_order_to_kiotviet")
    def test_action_push_same_status_does_not_publish_event(
        self, mock_push, mock_get_invoice, mock_publish
    ):
        """ACTION_PUSH: invoice status same as current → no publish."""
        from apps.kiotviet.infrastructure.services.service_actions import KiotVietService

        self.order.status = 3
        self.order.save(update_fields=["status"])

        mock_push.return_value = {
            "order_result": {
                "code": "DH-PUSH-SAME",
                "invoiceCode": "HD-PUSH-SAME",
                "invoiceId": 8200,
                "invoices": [{"invoiceCode": "HD-PUSH-SAME", "invoiceId": 8200}],
            },
            "make_invoice": False,
        }
        mock_get_invoice.return_value = {
            "code": "HD-PUSH-SAME",
            "status": 1,
            "invoiceDelivery": {"status": 3},  # same as current
        }

        KiotVietService.execute_action(self.order, KiotVietService.ACTION_PUSH)

        self.order.refresh_from_db()
        # status unchanged
        self.assertEqual(self.order.status, 3)
        # publish_on_commit may be called for other events but NOT OrderStatusChanged
        for c in mock_publish.call_args_list:
            event_arg = c.args[0] if c.args else None
            self.assertNotIsInstance(event_arg, OrderStatusChanged)


# ---------------------------------------------------------------------------
# service_actions.py — ACTION_SYNC_SHIPPING
# ---------------------------------------------------------------------------

class ServiceActionsSyncStatusPublishesOrderStatusChangedTests(TestCase):
    """ACTION_SYNC_STATUS: delivery status synced from invoice → publish OrderStatusChanged."""

    def setUp(self):
        self.sales_channel = Channel.objects.create(code="lp-kv-sync-st", name="KV Sync Status")
        self.customer = Customer.objects.create(
            full_name="KV Sync Status User",
            phone_raw="0909333555",
            phone_normalized="0909333555",
        )
        self.kv_channel = _make_kv_channel(
            name="KV Sync Status",
            branch_id="B97",
            client_id="cli-sync-st",
            client_secret="secret-sync-st",
        )
        self.order = _make_order(
            self.sales_channel,
            self.kv_channel,
            self.customer,
            status=3,
            invoice_code="HD-STATUS-001",
            invoice_id=7001,
        )

    @patch("apps.common.events.event_bus.publish_on_commit")
    @patch("apps.kiotviet.infrastructure.services.service_actions.get_invoice_by_code")
    def test_sync_status_delivery_status_change_publishes_event(self, mock_get_invoice, mock_publish):
        """ACTION_SYNC_STATUS: delivery status=6 → publish OrderStatusChanged(old=3, new=6)."""
        from apps.kiotviet.infrastructure.services.service_actions import KiotVietService

        mock_get_invoice.return_value = {
            "code": "HD-STATUS-001",
            "status": 1,
            "invoiceDelivery": {
                "status": 6,
                "deliveryCode": "GHN-STATUS-001",
            },
        }

        KiotVietService.execute_action(self.order, KiotVietService.ACTION_SYNC_STATUS)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 6)

        expected_event = OrderStatusChanged(
            order_id=self.order.id,
            old_status=3,
            new_status=6,
            source=StatusChangeSource.KIOTVIET_ACTION,
        )
        _assert_status_changed_once(mock_publish, expected_event)


# ---------------------------------------------------------------------------
# service_actions.py — cancel_invoice
# ---------------------------------------------------------------------------

class ServiceActionsCancelInvoicePublishesOrderStatusChangedTests(TestCase):
    """cancel_invoice: order set to CANCELLED → publish OrderStatusChanged."""

    def setUp(self):
        self.sales_channel = Channel.objects.create(code="lp-kv-cancel", name="KV Cancel")
        self.customer = Customer.objects.create(
            full_name="KV Cancel User",
            phone_raw="0909444666",
            phone_normalized="0909444666",
        )
        self.kv_channel = _make_kv_channel(
            name="KV Cancel",
            branch_id="B96",
            client_id="cli-cancel",
            client_secret="secret-cancel",
        )
        self.order = _make_order(
            self.sales_channel,
            self.kv_channel,
            self.customer,
            status=3,
            invoice_code="HD-CANCEL-001",
            invoice_id=6001,
        )

    @patch("apps.common.events.event_bus.publish_on_commit")
    @patch(
        "apps.kiotviet.infrastructure.services.service_actions.delete_invoice_by_id",
        return_value={"id": 6001},
    )
    def test_cancel_invoice_publishes_OrderStatusChanged(self, mock_delete, mock_publish):
        """cancel_invoice → publish OrderStatusChanged(old=3, new=CANCELLED=6)."""
        from apps.kiotviet.infrastructure.services.service_actions import KiotVietService

        KiotVietService.execute_action(self.order, "cancel_invoice")

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, int(OrderStatus.CANCELLED))

        expected_event = OrderStatusChanged(
            order_id=self.order.id,
            old_status=3,
            new_status=int(OrderStatus.CANCELLED),
            source=StatusChangeSource.KIOTVIET_ACTION,
        )
        _assert_status_changed_once(mock_publish, expected_event)


# ---------------------------------------------------------------------------
# service_actions.py — ACTION_CREATE_INVOICE
# ---------------------------------------------------------------------------

class ServiceActionsCreateInvoicePublishesOrderStatusChangedTests(TestCase):
    """ACTION_CREATE_INVOICE: order set to DELIVERED_SUCCESS → publish OrderStatusChanged."""

    def setUp(self):
        self.sales_channel = Channel.objects.create(code="lp-kv-create-inv", name="KV Create Inv")
        self.customer = Customer.objects.create(
            full_name="KV Create Inv User",
            phone_raw="0909555777",
            phone_normalized="0909555777",
        )
        self.kv_channel = _make_kv_channel(
            name="KV Create Inv",
            branch_id="B95",
            client_id="cli-create-inv",
            client_secret="secret-create-inv",
        )
        self.order = _make_order(
            self.sales_channel,
            self.kv_channel,
            self.customer,
            status=1,  # PENDING_PROCESSING
            invoice_code="",
            invoice_id=None,
        )

    @patch("apps.common.events.event_bus.publish_on_commit")
    @patch("apps.kiotviet.infrastructure.services.service_actions.push_order_to_kiotviet")
    def test_create_invoice_publishes_OrderStatusChanged(self, mock_push, mock_publish):
        """ACTION_CREATE_INVOICE (quick sale): order status → DELIVERED_SUCCESS=3
        → publish OrderStatusChanged(old=1, new=3)."""
        from apps.kiotviet.infrastructure.services.service_actions import KiotVietService
        from apps.orders.models import OrderStatus

        delivered = int(OrderStatus.DELIVERED_SUCCESS)

        mock_push.return_value = {
            "order_result": {
                "invoiceCode": "HD-CREATE-001",
                "invoiceId": 5001,
                "id": 5001,
            },
        }

        KiotVietService.execute_action(self.order, KiotVietService.ACTION_CREATE_INVOICE)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, delivered)

        expected_event = OrderStatusChanged(
            order_id=self.order.id,
            old_status=1,
            new_status=delivered,
            source=StatusChangeSource.KIOTVIET_ACTION,
        )
        _assert_status_changed_once(mock_publish, expected_event)
