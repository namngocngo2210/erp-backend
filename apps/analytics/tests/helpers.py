"""Factory tối thiểu cho test của app analytics.

Không dùng lại `apps/orders/tests/conftest.py` — fixture ở đó chỉ có phạm vi
trong app `orders`.
"""

from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderAssessment

_seq = {"n": 0}


def _next() -> int:
    _seq["n"] += 1
    return _seq["n"]


def make_customer(**kwargs) -> Customer:
    n = _next()
    defaults = {"full_name": f"Khach {n}", "phone_normalized": f"09000000{n:02d}"}
    defaults.update(kwargs)
    return Customer.objects.create(**defaults)


def make_channel(**kwargs) -> Channel:
    n = _next()
    defaults = {"code": f"an-ch-{n}", "name": f"Kenh {n}"}
    defaults.update(kwargs)
    return Channel.objects.create(**defaults)


def make_order(customer=None, channel=None, **kwargs) -> Order:
    """Đơn GTC theo mặc định (status=3, invoice_status=None, order_source=None)."""
    defaults = {
        "customer": customer or make_customer(),
        "channel": channel or make_channel(),
        "status": 3,
        "total_after_discount": 1_000_000,
        "created_at": timezone.now(),
    }
    defaults.update(kwargs)
    return Order.objects.create(**defaults)


def make_assessment(order, **kwargs) -> OrderAssessment:
    return OrderAssessment.objects.create(order=order, **kwargs)
