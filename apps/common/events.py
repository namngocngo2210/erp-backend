"""In-process domain event bus (Modular Monolith integration boundary).

Modules publish domain events instead of importing each other's internals. A
publisher knows nothing about its subscribers; a subscriber registers a handler
for an event type. This keeps cross-module side-effects decoupled while staying
in one process (and one DB transaction) — see ``docs/architecture-standard.md``.

Events are plain immutable data (ids/values, never ORM objects) so the bus
remains a real boundary. Dispatch is synchronous and best-effort: one failing
handler is logged and isolated so it never breaks the publisher or sibling
handlers. Use :meth:`EventBus.publish_on_commit` when consumers must observe
committed state (the usual case for domain events).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from django.db import transaction

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DomainEvent:
    """Base for in-process domain events.

    Subclass as a frozen dataclass and set ``event_type``::

        @dataclass(frozen=True)
        class OrderConfirmed(DomainEvent):
            event_type: ClassVar[str] = "order.confirmed"
            order_id: int
    """

    event_type: ClassVar[str] = ""


Handler = Callable[[DomainEvent], None]


class EventBus:
    """Synchronous, in-process publish/subscribe keyed by ``event_type``."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    def is_subscribed(self, event_type: str, handler: Handler) -> bool:
        """Return whether ``handler`` is currently registered for ``event_type``."""
        return handler in self._handlers.get(event_type, ())

    def publish(self, event: DomainEvent) -> None:
        """Dispatch ``event`` to every handler for its type, now.

        Handlers are isolated: an exception is logged and swallowed so it
        cannot break the publisher or other handlers.
        """
        for handler in list(self._handlers.get(event.event_type, ())):
            try:
                handler(event)
            except Exception:  # noqa: BLE001 — isolate consumers from each other
                logger.exception("Event handler failed for %s", event.event_type)

    def publish_on_commit(self, event: DomainEvent) -> None:
        """Publish only after the current DB transaction commits.

        Consumers then see committed state, and a rollback cancels the event.
        Outside an atomic block Django runs the callback immediately.
        """
        transaction.on_commit(lambda: self.publish(event))

    def clear(self) -> None:
        """Drop all subscriptions (test helper)."""
        self._handlers.clear()


# Process-wide singleton. Modules subscribe at app ``ready()`` and publish from
# their application services.
event_bus = EventBus()


def subscribe(event_type: str) -> Callable[[Handler], Handler]:
    """Decorator registering a function as a handler on the shared bus::

    @subscribe("order.confirmed")
    def on_order_confirmed(event: DomainEvent) -> None:
        ...
    """

    def decorator(handler: Handler) -> Handler:
        event_bus.subscribe(event_type, handler)
        return handler

    return decorator
