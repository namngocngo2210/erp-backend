"""Cửa token tĩnh cho feed conversion.

Bản sao CÓ CHỦ ĐÍCH của ``apps.daily_report.transport.auth`` — không import
ngang module của app khác cùng tầng.

Token dùng chung với API ingest đơn (``ORDERS_INGEST_API_TOKEN``) theo chốt
2026-09-28: cùng một đối tác web vừa đẩy lead vào vừa kéo conversion ra — đổi
token là cắt cả hai chiều.
"""

from __future__ import annotations

import secrets
from functools import wraps

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.common.throttling import ConversionFeedApiThrottle
from apps.sysconfig import sysconfig


def extract_api_token(request) -> str:
    token = (request.headers.get("X-Api-Token") or "").strip()
    if token:
        return token
    auth_header = (request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


def require_api_token(view_func):
    """Prod thiếu token → 503 (fail-closed). Dev thiếu token → mở. Sai token → 401."""

    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        expected = sysconfig.get("ORDERS_INGEST_API_TOKEN")
        if not expected:
            if getattr(settings, "IS_PRODUCTION", False):
                return Response(
                    {"detail": "Conversion feed chưa được cấu hình token (ORDERS_INGEST_API_TOKEN)."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return view_func(request, *args, **kwargs)
        provided = extract_api_token(request)
        # .encode(): compare_digest với str non-ASCII ném TypeError → 500 thay vì 401.
        if not provided or not secrets.compare_digest(provided.encode(), expected.encode()):
            return Response({"detail": "Token không hợp lệ."}, status=status.HTTP_401_UNAUTHORIZED)
        return view_func(request, *args, **kwargs)

    return wrapped


def conversion_feed_view(view_func):
    """GET + không auth CRM + AllowAny + throttle scope riêng + token gate (sau throttle)."""
    return api_view(["GET"])(
        authentication_classes([])(
            permission_classes([AllowAny])(
                throttle_classes([ConversionFeedApiThrottle])(require_api_token(view_func))
            )
        )
    )
