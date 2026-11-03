"""Facade module to preserve public imports while delegating to layered implementation."""

from .transport.views.kiotviet_views_impl import (
    invoice_webhook,
)

__all__ = [
    "invoice_webhook",
]
