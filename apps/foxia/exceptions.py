"""Lỗi của tích hợp Foxia — passthrough message để tra cứu được nguyên văn."""

from __future__ import annotations


class FoxiaError(Exception):
    def __init__(self, message: str, payload: dict | None = None):
        super().__init__(message)
        self.payload = payload or {}


class FoxiaApiError(FoxiaError):
    """Lỗi từ JSON-RPC: HTTP >= 400, body không parse được, hoặc HTTP 200 kèm
    key `error` (Odoo báo lỗi nghiệp vụ theo cách này — xem foxia_client)."""

    def __init__(self, message: str, status_code: int = 0, payload: dict | None = None):
        super().__init__(message, payload=payload)
        self.status_code = status_code


class FoxiaAuthError(FoxiaError):
    """`authenticate` trả false: API key bị thu hồi hoặc tài khoản ngừng hoạt động.

    KHÔNG retry — đây là việc của con người (xin key mới). Caller đánh dấu
    connection `revoked` rồi dừng job.
    """


class FoxiaConfigError(FoxiaError):
    """Chưa có FoxiaConnection active, hoặc thiếu endpoint/database/username/key."""
