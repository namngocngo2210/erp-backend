"""Cửa xác thực token tĩnh cho API Daily Report.

Bản sao CÓ CHỦ ĐÍCH của helper bên Hermes (callcenter) — không import ngang
module private của app khác. Vì sao token tĩnh chứ không JWT: bên gọi là agent
chạy nền, không có phiên đăng nhập; thu hồi = đổi env + restart.
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

from apps.common.throttling import DailyReportApiThrottle
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
        expected = sysconfig.get("DAILY_REPORT_API_TOKEN")
        if not expected:
            if getattr(settings, "IS_PRODUCTION", False):
                return Response(
                    {"detail": "API Daily Report chưa được cấu hình token (DAILY_REPORT_API_TOKEN)."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return view_func(request, *args, **kwargs)
        provided = extract_api_token(request)
        # .encode(): compare_digest với str non-ASCII ném TypeError → 500 thay vì 401.
        if not provided or not secrets.compare_digest(provided.encode(), expected.encode()):
            return Response({"detail": "Token không hợp lệ."}, status=status.HTTP_401_UNAUTHORIZED)
        return view_func(request, *args, **kwargs)

    return wrapped


def daily_report_view(view_func):
    """GET + không auth CRM + AllowAny + throttle scope riêng + token gate.

    Thứ tự: token gate nằm TRONG DRF view (sau throttle) để request rác vẫn bị
    đếm vào trần. ``@extend_schema`` phải đặt TRÊN decorator này.
    """
    return api_view(["GET"])(
        authentication_classes([])(
            permission_classes([AllowAny])(
                throttle_classes([DailyReportApiThrottle])(require_api_token(view_func))
            )
        )
    )
