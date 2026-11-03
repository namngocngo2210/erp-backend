"""Wiring test cho port inventory → kiotviet (Lô 5).

`return_order_sync` không import service của `kiotviet` nữa mà gọi qua
`apps.inventory.contracts.kiotviet_delivery_port`; kiotviet đăng ký adapter tại
`KiotVietConfig.ready()`.

`test_return_order_sync.py` vẫn patch thẳng
`invoice_mutations_push.update_invoice_delivery_status_by_id` và KHÔNG phải sửa —
adapter bind lười lúc gọi nên patch đó chặn đúng call đi qua port.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from apps.inventory.contracts.kiotviet_delivery_port import kiotviet_delivery_port

_PUSH = (
    "apps.kiotviet.infrastructure.services.invoice_mutations_push"
    ".update_invoice_delivery_status_by_id"
)


class KiotVietDeliveryPortWiringTests(SimpleTestCase):
    def test_port_registered_at_app_ready(self):
        port = kiotviet_delivery_port()
        self.assertEqual(type(port).__module__, "apps.kiotviet.adapters")
        self.assertEqual(type(port).__name__, "KiotVietDeliveryAdapter")

    def test_delegates_with_same_keyword_arguments(self):
        with patch(_PUSH, return_value="ok") as mock_push:
            result = kiotviet_delivery_port().update_invoice_delivery_status(
                invoice_id=55555,
                status="returned",
                order="order-sentinel",
                channel="channel-sentinel",
            )
        self.assertEqual(result, "ok")
        mock_push.assert_called_once_with(
            invoice_id=55555,
            status="returned",
            order="order-sentinel",
            channel="channel-sentinel",
        )

    def test_does_not_swallow_errors(self):
        """Port KHÔNG nuốt lỗi: `return_order_sync` tự bọc try/except và chỉ log,
        vì lỗi KiotViet không được chặn nghiệp vụ kho. Nuốt ở đây thì `except` bên
        caller thành code chết và người đọc sau sẽ hiểu sai luồng lỗi."""
        with patch(_PUSH, side_effect=RuntimeError("kiotviet down")):
            with self.assertRaises(RuntimeError):
                kiotviet_delivery_port().update_invoice_delivery_status(
                    invoice_id=1, status="returned", order=None, channel=None
                )
