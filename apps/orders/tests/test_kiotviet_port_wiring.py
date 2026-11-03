"""Wiring test cho các port orders → kiotviet.

`orders` không import service/model của app kiotviet nữa mà gọi qua port khai ở
`orders/contracts/`; app kiotviet đăng ký adapter tại `KiotVietConfig.ready()`.
Ba port hiện có:

- `kiotviet_invoice_port` (Lô 1) — in hoá đơn / phiếu giao;
- `kiotviet_order_flow_port` (Lô 2) — liệt kê kênh, KShip options, chạy hành
  động đẩy đơn;
- `kiotviet_cancellation_port` (Lô 3) — xác minh hoá đơn đã huỷ chưa.

Test này ghim 2 điều cho mỗi port:

1. Import-time wiring: sau khi Django khởi động app registry, port resolve được
   (không RuntimeError) và đúng là adapter của kiotviet.
2. Adapter delegate ĐÚNG hàm service cũ — patch tại module kiotviet vẫn ăn vào
   call qua port (adapter bind lười lúc gọi), nên toàn bộ test hiện có patch
   `apps.kiotviet.infrastructure.services.*` không phải đổi gì.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from apps.orders.contracts.kiotviet_cancellation_port import (
    CancellationVerificationUnavailable,
    CancelVerdict,
    kiotviet_cancellation_port,
)
from apps.orders.contracts.kiotviet_invoice_port import kiotviet_invoice_port
from apps.orders.contracts.kiotviet_order_flow_port import kiotviet_order_flow_port

_VERIFY = (
    "apps.kiotviet.application.services.cancellation_verifier."
    "InvoiceCancellationVerifier.verify_not_cancelled"
)


class KiotVietPortsRegisteredAtAppReadyTests(SimpleTestCase):
    """Cả 3 port resolve ra adapter của kiotviet sau khi app registry khởi động.

    So sánh theo tên (không import apps.kiotviet.adapters): test nằm trong orders
    nên cũng phải tôn trọng contract phân tầng .importlinter.
    """

    PORTS = (
        (kiotviet_invoice_port, "KiotVietInvoiceAdapter"),
        (kiotviet_order_flow_port, "KiotVietOrderFlowAdapter"),
        (kiotviet_cancellation_port, "KiotVietCancellationAdapter"),
    )

    def test_port_registered_at_app_ready(self):
        for port_fn, adapter_name in self.PORTS:
            with self.subTest(port=port_fn.__name__):
                port = port_fn()
                self.assertEqual(type(port).__module__, "apps.kiotviet.adapters")
                self.assertEqual(type(port).__name__, adapter_name)


class KiotVietInvoicePortWiringTests(SimpleTestCase):
    def test_get_invoice_by_code_delegates_to_kiotviet_service(self):
        with patch(
            "apps.kiotviet.infrastructure.services.invoice_sync_queries.get_invoice_by_code",
            return_value={"code": "HD-1"},
        ) as mock_get:
            payload = kiotviet_invoice_port().get_invoice_by_code(
                invoice_code="HD-1", order="order-sentinel", channel="channel-sentinel"
            )
        self.assertEqual(payload, {"code": "HD-1"})
        mock_get.assert_called_once_with(
            invoice_code="HD-1", order="order-sentinel", channel="channel-sentinel"
        )

    def test_is_koc_non_cod_order_delegates_to_kiotviet_service(self):
        with patch(
            "apps.kiotviet.infrastructure.services.invoice_mutations_push.is_koc_non_cod_order",
            return_value=True,
        ) as mock_koc:
            self.assertTrue(kiotviet_invoice_port().is_koc_non_cod_order("order-sentinel"))
        mock_koc.assert_called_once_with("order-sentinel")

    def test_web_request_delegates_to_kiotviet_gateway(self):
        with patch(
            "apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook._kiotviet_web_request",
            return_value="response-sentinel",
        ) as mock_req:
            response = kiotviet_invoice_port().web_request(
                order="order-sentinel",
                channel="channel-sentinel",
                method="GET",
                endpoint="/api/invoices/1",
                params={"Includes": "Order"},
            )
        self.assertEqual(response, "response-sentinel")
        mock_req.assert_called_once_with(
            order="order-sentinel",
            channel="channel-sentinel",
            method="GET",
            endpoint="/api/invoices/1",
            params={"Includes": "Order"},
        )

    def test_orders_invoice_module_calls_through_port(self):
        """apps.orders.invoice.get_invoice_by_code đi qua port, không import service."""
        from apps.orders import invoice

        with patch(
            "apps.kiotviet.infrastructure.services.invoice_sync_queries.get_invoice_by_code",
            return_value={"code": "HD-2"},
        ):
            payload = invoice.get_invoice_by_code(
                invoice_code="HD-2", order=None, channel=None
            )
        self.assertEqual(payload, {"code": "HD-2"})


class KiotVietOrderFlowPortWiringTests(SimpleTestCase):
    def test_get_kship_options_delegates_to_kiotviet_gateway(self):
        with patch(
            "apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook."
            "get_kship_providers_and_services",
            return_value={"providers": []},
        ) as mock_opts:
            options = kiotviet_order_flow_port().get_kship_options("channel-sentinel")
        self.assertEqual(options, {"providers": []})
        mock_opts.assert_called_once_with("channel-sentinel")

    def test_execute_order_action_delegates_to_kiotviet_service(self):
        with patch(
            "apps.kiotviet.infrastructure.services.service_actions.KiotVietService.execute_action",
            return_value={"status": "success", "payload": {}},
        ) as mock_exec:
            result = kiotviet_order_flow_port().execute_order_action(
                "order-sentinel",
                "push",
                actor_id=7,
                provider_code="BEST",
                service_code="STD",
                kship_note="ghi chú",
                shipper_note="KHONGCHOXEMHANG",
                sender_address_id=42,
                service_extra={"PickShift": "1789030800-2"},
            )
        self.assertEqual(result, {"status": "success", "payload": {}})
        mock_exec.assert_called_once_with(
            "order-sentinel",
            "push",
            actor_id=7,
            provider_code="BEST",
            service_code="STD",
            kship_note="ghi chú",
            shipper_note="KHONGCHOXEMHANG",
            sender_address_id=42,
            service_extra={"PickShift": "1789030800-2"},
        )

    def test_crud_view_kship_helper_calls_through_port(self):
        """View giữ tên module-level cũ nên test đang patch nó vẫn ăn."""
        from apps.orders.transport.views.order_views_impl import order_crud_view

        with patch(
            "apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook."
            "get_kship_providers_and_services",
            return_value={"providers": ["BEST"]},
        ):
            options = order_crud_view.get_kship_providers_and_services("channel-sentinel")
        self.assertEqual(options, {"providers": ["BEST"]})


class KiotVietCancellationPortWiringTests(SimpleTestCase):
    def test_translates_each_cancel_check_into_orders_enum(self):
        """Kết quả phải là enum CỦA ORDERS — guard so sánh bằng `is`, không phải `==`."""
        from apps.kiotviet.application.services.cancellation_verifier import CancelCheck

        for source, expected in (
            (CancelCheck.OK, CancelVerdict.OK),
            (CancelCheck.CANCELLED, CancelVerdict.CANCELLED),
            (CancelCheck.UNVERIFIABLE, CancelVerdict.UNVERIFIABLE),
        ):
            with self.subTest(source=source), patch(_VERIFY, return_value=source):
                verdict = kiotviet_cancellation_port().verify_not_cancelled("order-sentinel")
            self.assertIs(verdict, expected)

    def test_translates_verification_unavailable_into_orders_exception(self):
        from apps.kiotviet.application.services.cancellation_verifier import (
            VerificationUnavailable,
        )

        with patch(_VERIFY, side_effect=VerificationUnavailable("timeout")):
            with self.assertRaises(CancellationVerificationUnavailable):
                kiotviet_cancellation_port().verify_not_cancelled("order-sentinel")

    def test_unknown_value_falls_back_to_unverifiable(self):
        """Giá trị lạ không được im lặng cho qua như 'không phải CANCELLED'."""
        with patch(_VERIFY, return_value="gia-tri-la"):
            verdict = kiotviet_cancellation_port().verify_not_cancelled("order-sentinel")
        self.assertIs(verdict, CancelVerdict.UNVERIFIABLE)
