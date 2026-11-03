"""Cắm serializer transport vào port của application (``apps.orders.application.ports``).

Gọi từ ``OrdersConfig.ready()``. Import serializer LƯỜI (tại thời điểm gọi) để
``ready()`` không kéo cả cây serializer vào lúc khởi động và test patch thẳng
module nguồn vẫn ăn vào call qua port. Audit 2026-09-23 (T3/P2-2).
"""

from __future__ import annotations

from apps.orders.application.ports import (
    LandingPayloadValidation,
    register_landing_payload_validator,
    register_order_row_presenter,
)


def _present_order_row(order, context: dict) -> dict:
    from apps.orders.serializers import OrderListSerializer

    return dict(OrderListSerializer(order, context=context).data)


def _validate_landing_payload(data) -> LandingPayloadValidation:
    from apps.orders.serializers import LandingOrderSerializer

    serializer = LandingOrderSerializer(data=data)
    if not serializer.is_valid():
        return LandingPayloadValidation(is_valid=False, validated_data=None, errors=serializer.errors)
    return LandingPayloadValidation(is_valid=True, validated_data=serializer.validated_data, errors=None)


def bind() -> None:
    register_order_row_presenter(_present_order_row)
    register_landing_payload_validator(_validate_landing_payload)
