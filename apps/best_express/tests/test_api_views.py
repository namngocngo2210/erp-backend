"""Endpoint tests cho REST API Best Express (transport/api_views).

Mock ở ranh giới service (shipment_service / tracking_service trong namespace
api_views) — không gọi Best thật. RBAC: đọc = shipments:view, ghi = shipments:edit.

Bảo mật REST API Best Express (audit 2026-07-14 vòng 2):

- payload passthrough KHÔNG được ghi đè mã vận đơn (`Code`) mà server resolve từ URL;
- endpoint phải lọc theo phạm vi dữ liệu của user (scope kênh/NV), kể cả `codes`.
"""

from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.best_express.application.services import shipment_service
from apps.best_express.exceptions import BestExpressError
from apps.best_express.models import BestExpressAccount, BestShipment
from apps.channels_core.models import Channel, UserChannel
from apps.customers.models import Customer
from apps.orders.models import Order
from apps.users.models import DataScope
from apps.users.tests.helpers import make_user_with_permissions


def _make_account(**overrides) -> BestExpressAccount:
    defaults = {
        "name": "Best test",
        "environment": "test",
        "username": "u1",
        "password": "p1",
    }
    defaults.update(overrides)
    return BestExpressAccount.objects.create(**defaults)


def _make_shipment(account: BestExpressAccount, **overrides) -> BestShipment:
    defaults = {"merchant_order_code": "HD001", "code_best": "BEST001"}
    defaults.update(overrides)
    return BestShipment.objects.create(account=account, **defaults)


class BestApiRbacTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.viewer = make_user_with_permissions("bx_viewer", ["shipments:view"])
        cls.outsider = make_user_with_permissions("bx_outsider", ["orders:view"])
        _make_account()

    def _client(self, user) -> APIClient:
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_outsider_403(self):
        resp = self._client(self.outsider).get("/api/best-express/accounts")
        self.assertEqual(resp.status_code, 403)

    def test_viewer_cannot_create_shipment(self):
        resp = self._client(self.viewer).post(
            "/api/best-express/shipments", {"payload": {}}, format="json"
        )
        self.assertEqual(resp.status_code, 403)

    def test_viewer_cannot_cancel(self):
        resp = self._client(self.viewer).post("/api/best-express/shipments/1/cancel")
        self.assertEqual(resp.status_code, 403)

    def test_accounts_list_hides_secrets(self):
        resp = self._client(self.viewer).get("/api/best-express/accounts")
        self.assertEqual(resp.status_code, 200)
        row = resp.json()[0]
        self.assertEqual(row["username"], "u1")
        self.assertNotIn("password", row)
        self.assertNotIn("token", row)
        self.assertNotIn("webhook_token", row)


class BestApiActionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.editor = make_user_with_permissions(
            "bx_editor", ["shipments:view", "shipments:edit"]
        )
        cls.account = _make_account()

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.editor)

    def test_estimate_fee_resolves_single_account(self):
        with patch(
            "apps.best_express.transport.api_views.tracking_service.estimate_fee",
            return_value={"TotalFee": 31900.0},
        ) as mock_fee:
            resp = self.client.post(
                "/api/best-express/estimate-fee",
                {"params": {"ServiceId": 12491}},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["TotalFee"], 31900.0)
        self.assertEqual(mock_fee.call_args.args[0].pk, self.account.pk)

    def test_estimate_fee_ambiguous_accounts_400(self):
        _make_account(username="u2", name="Best 2")
        resp = self.client.post(
            "/api/best-express/estimate-fee", {"params": {}}, format="json"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("account_id", resp.json()["detail"])

    def test_create_shipment_201(self):
        shipment = _make_shipment(self.account, merchant_order_code="HDNEW")
        with patch(
            "apps.best_express.transport.api_views.shipment_service.create_shipment",
            return_value=shipment,
        ):
            resp = self.client.post(
                "/api/best-express/shipments",
                {"account_id": self.account.pk, "payload": {"Code": "HDNEW"}},
                format="json",
            )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["merchant_order_code"], "HDNEW")

    def test_create_shipment_best_error_passthrough_400(self):
        with patch(
            "apps.best_express.transport.api_views.shipment_service.create_shipment",
            side_effect=BestExpressError("Mã đơn HD001 đã có vận đơn Best — không tạo trùng."),
        ):
            resp = self.client.post(
                "/api/best-express/shipments", {"payload": {"Code": "HD001"}}, format="json"
            )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("không tạo trùng", resp.json()["detail"])

    def test_create_shipment_order_not_found_404(self):
        resp = self.client.post(
            "/api/best-express/shipments",
            {"order_id": 999999, "payload": {}},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_cancel_shipment(self):
        shipment = _make_shipment(self.account)
        with patch(
            "apps.best_express.transport.api_views.shipment_service.cancel_shipment",
            return_value=shipment,
        ) as mock_cancel:
            resp = self.client.post(f"/api/best-express/shipments/{shipment.pk}/cancel")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_cancel.call_args.args[0].pk, shipment.pk)

    def test_cancel_missing_shipment_404(self):
        resp = self.client.post("/api/best-express/shipments/999999/cancel")
        self.assertEqual(resp.status_code, 404)

    def test_update_shipment(self):
        shipment = _make_shipment(self.account)
        with patch(
            "apps.best_express.transport.api_views.shipment_service.update_shipment",
            return_value=shipment,
        ) as mock_update:
            resp = self.client.patch(
                f"/api/best-express/shipments/{shipment.pk}",
                {"changes": {"CollectAmount": 50000}},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_update.call_args.args[1], {"CollectAmount": 50000})

    def test_trace(self):
        with patch(
            "apps.best_express.transport.api_views.tracking_service.trace",
            return_value=[{"code_best": "BEST001"}],
        ):
            resp = self.client.post(
                "/api/best-express/trace", {"codes": ["BEST001"]}, format="json"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["items"][0]["code_best"], "BEST001")

    def test_print_waybill(self):
        with patch(
            "apps.best_express.transport.api_views.tracking_service.print_waybills",
            return_value=["http://example.com/waybill.pdf"],
        ):
            resp = self.client.post(
                "/api/best-express/print-waybill", {"codes": ["BEST001"]}, format="json"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["urls"], ["http://example.com/waybill.pdf"])

    def test_no_active_account_400(self):
        BestExpressAccount.objects.update(is_active=False)
        resp = self.client.post("/api/best-express/trace", {"codes": ["X"]}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("BestExpressAccount", resp.json()["detail"])


class BestShipmentListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.viewer = make_user_with_permissions("bx_lister", ["shipments:view"])
        account = _make_account()
        _make_shipment(account, merchant_order_code="HD001", code_best="BEST001")
        _make_shipment(account, merchant_order_code="HD002", code_best="BEST002", status="cancelled")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.viewer)

    def test_list_all(self):
        resp = self.client.get("/api/best-express/shipments")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["total"], 2)

    def test_search_by_code(self):
        resp = self.client.get("/api/best-express/shipments?q=BEST002")
        body = resp.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["merchant_order_code"], "HD002")


def _account(**overrides) -> BestExpressAccount:
    defaults = {
        "name": "Best sec",
        "environment": "test",
        "username": "sec-u",
        "password": "p",
    }
    defaults.update(overrides)
    return BestExpressAccount.objects.create(**defaults)


class BestCodeOverrideTests(TestCase):
    """Client KHÔNG được chỉ định `Code` — nếu được, gate theo shipment_id vô hiệu:
    thao tác trên vận đơn của người khác (hạ COD, đổi địa chỉ nhận)."""

    def setUp(self):
        self.account = _account()
        self.mine = BestShipment.objects.create(
            account=self.account, merchant_order_code="HD-MINE", code_best="BEST-MINE"
        )

    @patch("apps.best_express.application.services.shipment_service.BestExpressClient")
    def test_update_shipment_ignores_client_supplied_code(self, mock_client):
        shipment_service.update_shipment(
            self.mine, {"Code": "BEST-NAN-NHAN", "CollectAmount": 0}
        )

        payload = mock_client.return_value.update_order.call_args[0][0]
        self.assertEqual(payload["Code"], "BEST-MINE", "Mã phải do server quyết định")
        self.assertEqual(payload["CollectAmount"], 0)

    @patch("apps.best_express.application.services.shipment_service.BestExpressClient")
    def test_update_address_ignores_client_supplied_code(self, mock_client):
        shipment_service.update_shipment_address(
            self.mine, {"code": "BEST-NAN-NHAN", "DestName": "Kẻ tấn công"}
        )

        payload = mock_client.return_value.update_address.call_args[0][0]
        self.assertEqual(payload["Code"], "BEST-MINE", "Kể cả key viết thường")
        self.assertEqual(payload["DestName"], "Kẻ tấn công")


class BestShipmentScopeTests(TestCase):
    """Vận đơn ngoài phạm vi dữ liệu của user phải vô hình (404) và không tra cứu được."""

    def setUp(self):
        self.account = _account(username="sec-u2")
        self.channel_mine = Channel.objects.create(code="ch-mine", name="Kênh của tôi")
        self.channel_other = Channel.objects.create(code="ch-other", name="Kênh khác")
        customer = Customer.objects.create(
            full_name="KH", phone_raw="0900000123", phone_normalized="0900000123"
        )
        self.order_mine = Order.objects.create(
            channel=self.channel_mine, customer=customer, name="KH", phone="0900000123"
        )
        self.order_other = Order.objects.create(
            channel=self.channel_other, customer=customer, name="KH", phone="0900000123"
        )
        self.ship_mine = BestShipment.objects.create(
            account=self.account,
            order=self.order_mine,
            merchant_order_code="HD-MINE",
            code_best="BEST-MINE",
        )
        self.ship_other = BestShipment.objects.create(
            account=self.account,
            order=self.order_other,
            merchant_order_code="HD-OTHER",
            code_best="BEST-OTHER",
        )

        # User scope CHANNEL, chỉ được gán kênh của mình.
        self.user = make_user_with_permissions(
            "bx_scoped", ["shipments:view", "shipments:edit"], scope=DataScope.CHANNEL
        )
        UserChannel.objects.create(user=self.user, channel=self.channel_mine)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_list_only_returns_in_scope_shipments(self):
        response = self.client.get("/api/best-express/shipments")
        self.assertEqual(response.status_code, 200)
        codes = [item["code_best"] for item in response.data["items"]]
        self.assertEqual(codes, ["BEST-MINE"])
        self.assertEqual(response.data["total"], 1)

    def test_cancel_out_of_scope_shipment_is_404(self):
        response = self.client.post(
            f"/api/best-express/shipments/{self.ship_other.id}/cancel"
        )
        self.assertEqual(response.status_code, 404)
        self.ship_other.refresh_from_db()
        self.assertNotEqual(self.ship_other.status, "cancelled")

    def test_print_waybill_rejects_out_of_scope_codes(self):
        response = self.client.post(
            "/api/best-express/print-waybill",
            {"codes": ["BEST-OTHER"]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Không có quyền", response.data["detail"])

    def test_trace_allows_in_scope_codes(self):
        with patch("apps.best_express.transport.api_views.tracking_service") as svc:
            svc.trace.return_value = [{"code": "BEST-MINE"}]
            response = self.client.post(
                "/api/best-express/trace", {"codes": ["BEST-MINE"]}, format="json"
            )
        self.assertEqual(response.status_code, 200)
