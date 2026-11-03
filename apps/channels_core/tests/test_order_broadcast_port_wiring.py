"""Wiring test cho port channels_core → orders (Lô 4).

`consumers.py` không import model/contract của orders nữa mà gọi qua
`apps.channels_core.contracts.order_broadcast_port`; app orders đăng ký adapter
tại `OrdersConfig.ready()`.

Test này CỐ Ý không import gì từ `apps.orders`: channels_core nằm dưới orders nên
kể cả test cũng phải tôn trọng contract phân tầng. Mọi thứ cần đụng tới orders đều
patch bằng đường dẫn chuỗi.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from apps.channels_core.contracts import order_broadcast_port

_VALIDATE = "apps.orders.contracts.OrderEventContract.validate_payload"
_ORDER_QS = "apps.orders.models.Order.objects"


class OrderBroadcastPortWiringTests(SimpleTestCase):
    def test_port_registered_at_app_ready(self):
        port = order_broadcast_port()
        self.assertEqual(type(port).__module__, "apps.orders.adapters")
        self.assertEqual(type(port).__name__, "OrderBroadcastAdapter")

    def test_invalid_payload_becomes_none_not_exception(self):
        """Payload rác → None. Consumer chỉ bỏ qua event, không phải bắt exception
        của orders (kiểu lỗi không được vượt biên)."""
        self.assertIsNone(order_broadcast_port().validate_event_payload({}))

    def test_valid_payload_delegates_to_order_event_contract(self):
        with patch(_VALIDATE) as mock_validate:
            mock_validate.return_value.to_payload.return_value = {"order_id": 7}
            result = order_broadcast_port().validate_event_payload({"raw": True})
        self.assertEqual(result, {"order_id": 7})
        mock_validate.assert_called_once_with({"raw": True})

    def test_order_belongs_to_user_short_circuits_without_user(self):
        """user_id rỗng → False mà KHÔNG chạm DB.

        Quan trọng vì đường này chạy trên mọi client trong group `crm_updates`
        (sự cố 2026-07-29): một query thừa ở đây nhân với số tab đang mở.
        """
        with patch(_ORDER_QS) as mock_objects:
            self.assertFalse(order_broadcast_port().order_belongs_to_user(1, None))
        mock_objects.filter.assert_not_called()

    def test_order_belongs_to_user_queries_by_order_and_staff(self):
        with patch(_ORDER_QS) as mock_objects:
            mock_objects.filter.return_value.exists.return_value = True
            self.assertTrue(order_broadcast_port().order_belongs_to_user(7, 42))
        mock_objects.filter.assert_called_once_with(pk=7, staff_id=42)

    def test_resolve_order_channel_code_reads_channel_code(self):
        with patch(_ORDER_QS) as mock_objects:
            chain = mock_objects.filter.return_value.values_list.return_value
            chain.first.return_value = "FB"
            self.assertEqual(order_broadcast_port().resolve_order_channel_code(7), "FB")
        mock_objects.filter.assert_called_once_with(pk=7)
        mock_objects.filter.return_value.values_list.assert_called_once_with(
            "channel__code", flat=True
        )
