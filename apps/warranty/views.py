"""Facade module to preserve public imports while delegating to layered implementation."""

from .transport.views.warranty_views_impl import (
    WarrantyRegisterThrottle,
    create_warranty_registration,
)

__all__ = [
    "WarrantyRegisterThrottle",
    "create_warranty_registration",
]
