"""API quản trị kết nối CNV: bắt đầu luồng SSO, nhận callback, xem trạng thái sync.

``connect``/``status`` chỉ cho admin. ``callback`` là AllowAny CÓ CHỦ ĐÍCH:
browser được id.cnv.vn redirect về không mang JWT header — an ninh nằm ở
``state`` (random 40 ký tự, chỉ phát cho admin qua ``connect``, sống 10 phút
trong cache, dùng một lần).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count
from django.utils import timezone
from django.utils.crypto import get_random_string
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response

from apps.cnv_loyalty.exceptions import CnvError
from apps.cnv_loyalty.infrastructure.sso_client import CnvSsoClient
from apps.cnv_loyalty.models import CnvConnection, CnvSyncRecord
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)

_STATE_CACHE_PREFIX = "cnv_sso_state:"
_STATE_TTL = 600  # 10 phút — đủ cho một lượt authorize tay


@extend_schema(
    responses=inline_serializer(
        name="CnvConnectResponse",
        fields={"authorize_url": drf_serializers.CharField()},
    ),
)
@api_view(["GET"])
@permission_classes([IsAdminUser])
def cnv_connect(request):
    connection = CnvConnection.get_active()
    if connection is None or not connection.client_id:
        return Response(
            {"detail": "Chưa có CnvConnection active (client_id/client_secret) — tạo qua admin/shell."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    state = get_random_string(40)
    cache.set(f"{_STATE_CACHE_PREFIX}{state}", connection.pk, timeout=_STATE_TTL)
    return Response({"authorize_url": CnvSsoClient(connection).build_authorize_url(state)})


@extend_schema(
    responses=inline_serializer(
        name="CnvCallbackResponse",
        fields={
            "detail": drf_serializers.CharField(),
            "token_expires_at": drf_serializers.DateTimeField(allow_null=True),
        },
    ),
)
@api_view(["GET"])
@permission_classes([AllowAny])
def cnv_callback(request):
    code = str(request.query_params.get("code") or "").strip()
    state = str(request.query_params.get("state") or "").strip()
    if not code or not state:
        return Response({"detail": "Thiếu code/state."}, status=status.HTTP_400_BAD_REQUEST)

    state_key = f"{_STATE_CACHE_PREFIX}{state}"
    connection_pk = cache.get(state_key)
    if connection_pk is None:
        return Response(
            {"detail": "State không hợp lệ hoặc đã hết hạn — chạy lại /api/cnv/connect."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    cache.delete(state_key)  # dùng một lần

    connection = CnvConnection.objects.filter(pk=connection_pk, is_active=True).first()
    if connection is None:
        return Response({"detail": "CnvConnection không còn active."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        bundle = CnvSsoClient(connection).exchange_code(code)
        connection.access_token = bundle.access_token
        if bundle.refresh_token:
            connection.refresh_token = bundle.refresh_token
        connection.token_type = bundle.token_type or "Bearer"
        connection.scope = bundle.scope
        connection.token_expires_at = timezone.now() + timedelta(
            seconds=bundle.expires_in or 3600
        )
        connection.save(
            update_fields=[
                "access_token", "refresh_token", "token_type", "scope",
                "token_expires_at", "updated_at",
            ]
        )
    except CnvError as exc:
        logger.warning("CNV callback: đổi code thất bại: %s", exc)
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return Response(
        {"detail": "Đã kết nối CNV.", "token_expires_at": connection.token_expires_at}
    )


@extend_schema(
    responses=inline_serializer(
        name="CnvStatusResponse",
        fields={
            "enabled": drf_serializers.BooleanField(),
            "connected": drf_serializers.BooleanField(),
            "token_expires_at": drf_serializers.DateTimeField(allow_null=True),
            "records": drf_serializers.DictField(),
        },
    ),
)
@api_view(["GET"])
@permission_classes([IsAdminUser])
def cnv_status(request):
    connection = CnvConnection.get_active()
    records: dict[str, dict[str, int]] = {}
    for row in CnvSyncRecord.objects.values("entity_type", "status").annotate(n=Count("id")):
        records.setdefault(row["entity_type"], {})[row["status"]] = row["n"]
    return Response(
        {
            "enabled": bool(sysconfig.get("CNV_SYNC_ENABLED")),
            "connected": bool(connection and connection.refresh_token),
            "token_expires_at": connection.token_expires_at if connection else None,
            "records": records,
        }
    )
