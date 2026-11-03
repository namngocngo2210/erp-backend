"""Lỗi của tích hợp CNV Loyalty — passthrough message để tra cứu được nguyên văn."""

from __future__ import annotations


class CnvError(Exception):
    def __init__(self, message: str, payload: dict | None = None):
        super().__init__(message)
        self.payload = payload or {}


class CnvApiError(CnvError):
    """Lỗi từ REST API apis.cnvloyalty.com (HTTP >= 400 hoặc body không parse được)."""

    def __init__(self, message: str, status_code: int = 0, payload: dict | None = None):
        super().__init__(message, payload=payload)
        self.status_code = status_code


class CnvAuthError(CnvError):
    """Chưa kết nối SSO / token không refresh được — cần admin kết nối lại."""
