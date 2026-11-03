"""API Cấu hình hệ thống — CHỈ superuser (cầm API key Gemini/Zenify/Lark + token
mọi API vào). Không endpoint nào trả giá trị secret ra ngoài."""

from __future__ import annotations

from functools import wraps

from django.contrib.auth import get_user_model
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.sysconfig import registry
from apps.sysconfig.models import SystemSetting, SystemSettingChange
from apps.sysconfig.service import SysconfigValidationError, hint_for, set_many
from apps.users.rbac import resolve_effective_permissions

User = get_user_model()
HISTORY_MAX = 200


def superuser_only(view):
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not resolve_effective_permissions(request.user).is_superuser:
            return Response({"detail": "Chỉ quản trị viên."}, status=status.HTTP_403_FORBIDDEN)
        return view(request, *args, **kwargs)

    return _wrapped


# Chỉ để khai hợp đồng OpenAPI (schema.yaml → FE schema.d.ts); payload dựng tay ở dưới.
class SystemSettingUserRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class SystemSettingOutSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    help = serializers.CharField()
    type = serializers.CharField(help_text="bool | int | float | str | url | datetime")
    is_secret = serializers.BooleanField()
    is_set = serializers.BooleanField()
    value = serializers.CharField(allow_null=True)
    hint = serializers.CharField(allow_null=True)
    default = serializers.JSONField()
    updated_at = serializers.DateTimeField(allow_null=True)
    updated_by = SystemSettingUserRefSerializer(allow_null=True)


class SystemSettingGroupSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    settings = SystemSettingOutSerializer(many=True)


_SETTINGS_RESPONSE = inline_serializer(
    "SystemSettingsResponse", fields={"groups": SystemSettingGroupSerializer(many=True)}
)


def _user_out(user) -> dict | None:
    if user is None:
        return None
    name = (getattr(user, "display_name", "") or user.get_full_name() or user.get_username()).strip()
    return {"id": user.pk, "name": name}


def _payload() -> dict:
    rows = {r.key: r for r in SystemSetting.objects.select_related("updated_by").filter(key__in=registry.BY_KEY)}
    groups = []
    for gkey, glabel in registry.GROUPS:
        items = []
        for spec in registry.SETTINGS:
            if spec.group != gkey:
                continue
            row = rows.get(spec.key)
            raw = (row.secret_value if spec.secret else row.value) if row else None
            items.append(
                {
                    "key": spec.key,
                    "label": spec.label,
                    "help": spec.help,
                    "type": spec.type,
                    "is_secret": spec.secret,
                    "is_set": bool(raw),
                    "value": None if (spec.secret or raw is None) else raw,
                    "hint": hint_for(raw) if (spec.secret and raw) else None,
                    "default": spec.default,
                    "updated_at": row.updated_at.isoformat() if row else None,
                    "updated_by": _user_out(row.updated_by) if row else None,
                }
            )
        groups.append({"key": gkey, "label": glabel, "settings": items})
    return {"groups": groups}


@extend_schema(methods=["GET"], operation_id="system_settings_retrieve", responses={200: _SETTINGS_RESPONSE})
@extend_schema(
    methods=["PATCH"],
    operation_id="system_settings_update",
    request=inline_serializer(
        "SystemSettingsUpdateRequest",
        fields={"values": serializers.DictField(child=serializers.CharField(allow_null=True))},
    ),
    responses={200: _SETTINGS_RESPONSE, 400: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT},
)
@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
@superuser_only
def system_settings(request):
    if request.method == "GET":
        return Response(_payload())
    # Body là JSON list/scalar → request.data không có .get → 500 thay vì 400.
    if not isinstance(request.data, dict):
        return Response({"errors": {"body": "Phải là object {values: {...}}"}}, status=status.HTTP_400_BAD_REQUEST)
    values = request.data.get("values")
    if not isinstance(values, dict):
        return Response({"errors": {"values": "Phải là object {KEY: value}."}}, status=status.HTTP_400_BAD_REQUEST)
    try:
        set_many({str(k): (None if v is None else str(v)) for k, v in values.items()}, user=request.user)
    except SysconfigValidationError as exc:
        return Response({"errors": exc.errors}, status=status.HTTP_400_BAD_REQUEST)
    return Response(_payload())


@extend_schema(
    operation_id="system_settings_history",
    parameters=[
        OpenApiParameter("key", str, required=True),
        OpenApiParameter("limit", int, required=False),
    ],
    responses={
        200: inline_serializer(
            "SystemSettingChange",
            fields={
                "key": serializers.CharField(),
                "old_value": serializers.CharField(),
                "new_value": serializers.CharField(),
                "changed_by": SystemSettingUserRefSerializer(allow_null=True),
                "changed_at": serializers.DateTimeField(),
            },
            many=True,
        ),
        400: OpenApiTypes.OBJECT,
        403: OpenApiTypes.OBJECT,
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
@superuser_only
def system_settings_history(request):
    key = (request.query_params.get("key") or "").strip()
    if key not in registry.BY_KEY:
        return Response({"detail": "key không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        limit = min(max(int(request.query_params.get("limit", 50)), 1), HISTORY_MAX)
    except ValueError:
        limit = 50
    qs = SystemSettingChange.objects.select_related("changed_by").filter(key=key)[:limit]
    return Response(
        [
            {
                "key": c.key,
                "old_value": c.old_value,
                "new_value": c.new_value,
                "changed_by": _user_out(c.changed_by),
                "changed_at": c.changed_at.isoformat(),
            }
            for c in qs
        ]
    )
