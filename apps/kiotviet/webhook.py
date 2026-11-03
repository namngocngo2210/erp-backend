"""Facade module to preserve public imports while delegating to implementation."""

from .webhook_impl import (  # noqa: F401
    logger,
    process_invoice_webhook,
    verify_webhook_signature,
)
