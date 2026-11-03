"""Test helpers for ai_mart app."""

import itertools

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order


_order_counter = itertools.count()


def mk_order(**kw):
    """Create an Order for testing.

    Order.channel and Order.customer are both NOT NULL (PROTECT) foreign keys,
    so they must be created first.

    Args:
        **kw: arbitrary kwargs to pass to Order.objects.create(), with special
              handling for 'status' (default 0), 'name' (default "Test Customer"),
              and 'phone' (default unique auto-generated number).

    Returns:
        The created Order instance.
    """
    # Use monotonic counter to generate unique phone numbers (avoid collisions)
    counter = next(_order_counter)
    phone = f"09{counter:08d}"

    # Create channel if not provided
    if "channel" not in kw:
        channel = Channel.objects.create(
            code=f"test-ch-{counter}", name=f"Test Channel {counter}"
        )
        kw["channel"] = channel

    # Create customer if not provided
    if "customer" not in kw:
        customer = Customer.objects.create(
            full_name="Test Customer",
            phone_raw=phone,
            phone_normalized=phone,
        )
        kw["customer"] = customer

    # Set defaults for Order fields
    if "name" not in kw:
        kw["name"] = "Test Customer"
    if "phone" not in kw:
        kw["phone"] = phone
    if "status" not in kw:
        kw["status"] = 0

    return Order.objects.create(**kw)
