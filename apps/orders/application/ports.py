"""Port (cổng) để application dùng serializer mà KHÔNG import transport.

Audit 2026-09-23 (T3/P2-2): ``order_event_publisher`` và ``order_ingest_service``
từng lazy-import ``OrderListSerializer`` / ``LandingOrderSerializer`` từ transport —
sai chiều phụ thuộc (application → transport). Thay bằng dependency inversion:
application khai hai port nhỏ ở đây, transport cắm hiện thực lúc
``OrdersConfig.ready()`` (xem ``apps/orders/transport/serializers/port_bindings.py``),
giống mẫu ``apps/channels_core/contracts.py`` ↔ ``apps/orders/adapters.py``.

Hai port:

* ``present_order_row(order, context)`` — dựng row bản LIST (dict) cho payload
  ``order.created``; hiện thực là ``OrderListSerializer(...).data``.
* ``validate_landing_payload(data)`` — validate payload landing ingest; hiện thực
  là ``LandingOrderSerializer``. Trả ``LandingPayloadValidation`` mang nguyên
  ``validated_data`` / ``errors`` của serializer để service/view giữ y hành vi cũ
  (service còn mutate ``validated_data``, view trả ``errors`` làm body 400).

Chưa bind (chạy ngoài Django app registry) → ``PortNotBoundError`` để lỗi lộ sớm
thay vì trả dữ liệu rỗng.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class PortNotBoundError(RuntimeError):
    """Port chưa được transport cắm hiện thực (``OrdersConfig.ready()`` chưa chạy)."""


@dataclass(frozen=True)
class LandingPayloadValidation:
    """Kết quả validate payload landing — bọc nguyên output của serializer."""

    is_valid: bool
    validated_data: Any
    errors: Any


OrderRowPresenter = Callable[[Any, dict], dict]
LandingPayloadValidator = Callable[[Any], LandingPayloadValidation]

_order_row_presenter: OrderRowPresenter | None = None
_landing_payload_validator: LandingPayloadValidator | None = None


def register_order_row_presenter(presenter: OrderRowPresenter) -> None:
    global _order_row_presenter
    _order_row_presenter = presenter


def register_landing_payload_validator(validator: LandingPayloadValidator) -> None:
    global _landing_payload_validator
    _landing_payload_validator = validator


def present_order_row(order, context: dict) -> dict:
    """Row bản LIST của một đơn (dict thuần, đã ``dict(...)`` khỏi ReturnDict)."""
    if _order_row_presenter is None:
        raise PortNotBoundError("order row presenter chưa được bind (OrdersConfig.ready())")
    return _order_row_presenter(order, context)


def validate_landing_payload(data) -> LandingPayloadValidation:
    if _landing_payload_validator is None:
        raise PortNotBoundError("landing payload validator chưa được bind (OrdersConfig.ready())")
    return _landing_payload_validator(data)
