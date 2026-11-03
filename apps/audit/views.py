"""Facade module to preserve public imports while delegating to layered implementation."""

from .transport.views.audit_views_impl import (
    AuditLogViewSet,
)

__all__ = [
    "AuditLogViewSet",
]
