"""CRUD endpoints for the phone blocklist.

Any authenticated sales user can add/remove entries (per product requirements).
"""
from __future__ import annotations

from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import ORDER_SECTIONS, require_method_permission

# Audit 2026-08-19 N2: go/sua blocklist SDT la hanh vi GHI cau hinh chan — can
# `{section}:edit` (DELETE entry khong phai xoa don nen khong doi :delete).
_BL_VIEW = tuple(f"{s}:view" for s in ORDER_SECTIONS)
_BL_EDIT = tuple(f"{s}:edit" for s in ORDER_SECTIONS)

from apps.common.phone_utils import normalize_phone
from apps.orders.models import PhoneBlocklist, PhoneBlocklistReason

from ._view_helpers import _parse_required_pagination


class PhoneBlocklistSerializer(serializers.ModelSerializer):
    phone = serializers.CharField(write_only=True, required=True)
    created_by_username = serializers.SerializerMethodField()

    class Meta:
        model = PhoneBlocklist
        fields = [
            "id",
            "phone",
            "phone_normalized",
            "reason",
            "notes",
            "created_by",
            "created_by_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "phone_normalized", "created_by", "created_at", "updated_at"]

    def get_created_by_username(self, obj: PhoneBlocklist) -> str:
        user = getattr(obj, "created_by", None)
        return getattr(user, "username", "") if user else ""

    def validate_phone(self, value: str) -> str:
        normalized = normalize_phone(value)
        if not normalized:
            raise serializers.ValidationError("Số điện thoại không hợp lệ.")
        return normalized

    def validate_reason(self, value: str) -> str:
        if value and value not in dict(PhoneBlocklistReason.choices):
            raise serializers.ValidationError("Lý do không hợp lệ.")
        return value or PhoneBlocklistReason.BLACKLIST


# GET trả {items, total}; POST nhận body blocklist và trả bản ghi (200/201).
@extend_schema(
    request=PhoneBlocklistSerializer,
    responses={
        200: inline_serializer(
            "PhoneBlocklistListResponse",
            fields={
                "items": PhoneBlocklistSerializer(many=True),
                "total": drf_serializers.IntegerField(),
            },
        ),
        201: PhoneBlocklistSerializer,
    },
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_method_permission(GET=_BL_VIEW, POST=_BL_EDIT)])
def list_create_phone_blocklist(request):
    if request.method == "GET":
        pagination, error = _parse_required_pagination(request, max_limit=1000)
        if error is not None:
            return error
        q = str(request.query_params.get("q", "")).strip()
        reason = str(request.query_params.get("reason", "")).strip()
        queryset = PhoneBlocklist.objects.select_related("created_by").order_by("-created_at")
        if q:
            normalized_q = normalize_phone(q) or q
            queryset = queryset.filter(
                Q(phone_normalized__icontains=normalized_q) | Q(notes__icontains=q)
            )
        if reason:
            queryset = queryset.filter(reason=reason)
        total = queryset.count()
        items = list(queryset[pagination["offset"] : pagination["offset"] + pagination["limit"]])
        data = PhoneBlocklistSerializer(items, many=True).data
        return Response({"items": data, "total": total})

    serializer = PhoneBlocklistSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    phone_normalized = serializer.validated_data["phone"]
    reason = serializer.validated_data.get("reason") or PhoneBlocklistReason.BLACKLIST
    notes = serializer.validated_data.get("notes", "") or ""

    entry, created = PhoneBlocklist.objects.get_or_create(
        phone_normalized=phone_normalized,
        defaults={
            "reason": reason,
            "notes": notes,
            "created_by": request.user if request.user.is_authenticated else None,
        },
    )
    if not created:
        updated_fields = []
        if entry.reason != reason:
            entry.reason = reason
            updated_fields.append("reason")
        if notes and entry.notes != notes:
            entry.notes = notes
            updated_fields.append("notes")
        if updated_fields:
            entry.updated_at = timezone.now()
            updated_fields.append("updated_at")
            entry.save(update_fields=updated_fields)

    return Response(
        PhoneBlocklistSerializer(entry).data,
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


# PATCH cập nhật reason/notes → trả bản ghi; DELETE → 204.
@extend_schema(
    request=inline_serializer(
        "PhoneBlocklistUpdateRequest",
        fields={
            "reason": drf_serializers.CharField(required=False),
            "notes": drf_serializers.CharField(required=False, allow_blank=True),
        },
    ),
    responses={
        200: PhoneBlocklistSerializer,
        204: OpenApiResponse(description="Đã xóa bản ghi"),
        404: OpenApiResponse(description="Không tìm thấy bản ghi"),
    },
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_method_permission(PATCH=_BL_EDIT, DELETE=_BL_EDIT)])
def update_delete_phone_blocklist(request, entry_id: int):
    entry = PhoneBlocklist.objects.filter(id=entry_id).first()
    if entry is None:
        return Response({"detail": "Không tìm thấy bản ghi."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        entry.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    update_fields: list[str] = []
    new_reason = request.data.get("reason")
    if new_reason is not None:
        if new_reason not in dict(PhoneBlocklistReason.choices):
            return Response({"detail": "Lý do không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)
        entry.reason = new_reason
        update_fields.append("reason")
    if "notes" in request.data:
        entry.notes = str(request.data.get("notes") or "")
        update_fields.append("notes")
    if update_fields:
        update_fields.append("updated_at")
        entry.save(update_fields=update_fields)
    return Response(PhoneBlocklistSerializer(entry).data, status=status.HTTP_200_OK)
