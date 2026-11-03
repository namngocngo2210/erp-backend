"""Tests for the in-process domain event bus.

The pure pub/sub tests need no database. The ``publish_on_commit`` test uses
``TestCase.captureOnCommitCallbacks`` to drive Django's on-commit hook.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from django.test import TestCase

from apps.common.events import DomainEvent, EventBus, event_bus, subscribe


@dataclass(frozen=True)
class _Confirmed(DomainEvent):
    event_type: ClassVar[str] = "test.confirmed"
    order_id: int = 0


@dataclass(frozen=True)
class _Shipped(DomainEvent):
    event_type: ClassVar[str] = "test.shipped"
    order_id: int = 0


def test_publish_invokes_subscribed_handler():
    bus = EventBus()
    seen: list[int] = []
    # Handler nhận subclass cụ thể (_Confirmed) lúc runtime; chữ ký subscribe
    # chỉ biết DomainEvent nên mypy không thấy order_id.
    bus.subscribe("test.confirmed", lambda e: seen.append(e.order_id))  # type: ignore[attr-defined]

    bus.publish(_Confirmed(order_id=7))

    assert seen == [7]


def test_publish_only_reaches_matching_event_type():
    bus = EventBus()
    confirmed: list[int] = []
    shipped: list[int] = []
    bus.subscribe("test.confirmed", lambda e: confirmed.append(e.order_id))  # type: ignore[attr-defined]
    bus.subscribe("test.shipped", lambda e: shipped.append(e.order_id))  # type: ignore[attr-defined]

    bus.publish(_Confirmed(order_id=1))

    assert confirmed == [1]
    assert shipped == []


def test_multiple_handlers_all_invoked_in_order():
    bus = EventBus()
    calls: list[str] = []
    bus.subscribe("test.confirmed", lambda e: calls.append("a"))
    bus.subscribe("test.confirmed", lambda e: calls.append("b"))

    bus.publish(_Confirmed(order_id=1))

    assert calls == ["a", "b"]


def test_handler_exception_is_isolated():
    bus = EventBus()
    calls: list[str] = []

    def boom(_event):
        raise RuntimeError("handler blew up")

    bus.subscribe("test.confirmed", boom)
    bus.subscribe("test.confirmed", lambda e: calls.append("after"))

    # publish must not raise even though the first handler throws
    bus.publish(_Confirmed(order_id=1))

    assert calls == ["after"]


def test_publish_with_no_handlers_is_noop():
    bus = EventBus()
    bus.publish(_Confirmed(order_id=1))  # should simply do nothing


def test_subscribe_decorator_registers_on_shared_bus():
    event_bus.clear()
    seen: list[int] = []

    @subscribe("test.confirmed")
    def _handler(event):
        seen.append(event.order_id)

    try:
        event_bus.publish(_Confirmed(order_id=42))
        assert seen == [42]
    finally:
        event_bus.clear()


class PublishOnCommitTests(TestCase):
    def test_defers_until_transaction_commits(self):
        bus = EventBus()
        seen: list[int] = []
        bus.subscribe("test.confirmed", lambda e: seen.append(e.order_id))  # type: ignore[attr-defined]

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            bus.publish_on_commit(_Confirmed(order_id=99))
            # still inside the atomic block: handler has NOT run yet
            assert seen == []

        # after the block commits, the on_commit callback fired
        assert len(callbacks) == 1
        assert seen == [99]
