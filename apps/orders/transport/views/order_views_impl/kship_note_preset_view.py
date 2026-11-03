"""CRUD endpoints for KShip delivery note presets.

Presets are shop-wide: any authenticated user can list, create, edit,
and delete entries (matches the PhoneBlocklist pattern).
"""
from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import ORDER_SECTIONS, require_method_permission

# Audit 2026-08-19 N2: preset la cau hinh dung chung cua trang don — ghi can :edit.
_KP_VIEW = tuple(f"{s}:view" for s in ORDER_SECTIONS)
_KP_EDIT = tuple(f"{s}:edit" for s in ORDER_SECTIONS)

from apps.orders.models import KshipNotePreset


class KshipNotePresetSerializer(serializers.ModelSerializer):
    created_by_username = serializers.SerializerMethodField()

    class Meta:
        model = KshipNotePreset
        fields = [
            "id",
            "text",
            "sort_order",
            "created_by",
            "created_by_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "created_at", "updated_at"]

    def get_created_by_username(self, obj: KshipNotePreset) -> str:
        user = getattr(obj, "created_by", None)
        return getattr(user, "username", "") if user else ""

    def validate_text(self, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise serializers.ValidationError("Nội dung ghi chú không được để trống.")
        if len(text) > 500:
            raise serializers.ValidationError("Ghi chú vượt quá 500 ký tự.")
        return text


# GET trả {items, total}; POST nhận body preset và trả bản ghi (201).
@extend_schema(
    request=KshipNotePresetSerializer,
    responses={
        200: inline_serializer(
            "KshipNotePresetListResponse",
            fields={
                "items": KshipNotePresetSerializer(many=True),
                "total": drf_serializers.IntegerField(),
            },
        ),
        201: KshipNotePresetSerializer,
    },
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_method_permission(GET=_KP_VIEW, POST=_KP_EDIT)])
def list_create_kship_note_presets(request):
    if request.method == "GET":
        queryset = KshipNotePreset.objects.select_related("created_by").order_by("sort_order", "id")
        items = list(queryset)
        data = KshipNotePresetSerializer(items, many=True).data
        return Response({"items": data, "total": len(data)})

    serializer = KshipNotePresetSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    text = serializer.validated_data["text"]
    sort_order = serializer.validated_data.get("sort_order", 0) or 0

    entry = KshipNotePreset.objects.create(
        text=text,
        sort_order=sort_order,
        created_by=request.user if request.user.is_authenticated else None,
    )
    return Response(
        KshipNotePresetSerializer(entry).data,
        status=status.HTTP_201_CREATED,
    )


# PATCH cập nhật text/sort_order → trả bản ghi; DELETE → 204.
@extend_schema(
    request=inline_serializer(
        "KshipNotePresetUpdateRequest",
        fields={
            "text": drf_serializers.CharField(required=False),
            "sort_order": drf_serializers.IntegerField(required=False),
        },
    ),
    responses={
        200: KshipNotePresetSerializer,
        204: OpenApiResponse(description="Đã xóa bản ghi"),
        400: OpenApiResponse(description="Dữ liệu không hợp lệ"),
        404: OpenApiResponse(description="Không tìm thấy bản ghi"),
    },
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_method_permission(PATCH=_KP_EDIT, DELETE=_KP_EDIT)])
def update_delete_kship_note_preset(request, entry_id: int):
    entry = KshipNotePreset.objects.filter(id=entry_id).first()
    if entry is None:
        return Response({"detail": "Không tìm thấy bản ghi."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        entry.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    update_fields: list[str] = []
    if "text" in request.data:
        text = str(request.data.get("text") or "").strip()
        if not text:
            return Response({"detail": "Nội dung ghi chú không được để trống."}, status=status.HTTP_400_BAD_REQUEST)
        if len(text) > 500:
            return Response({"detail": "Ghi chú vượt quá 500 ký tự."}, status=status.HTTP_400_BAD_REQUEST)
        entry.text = text
        update_fields.append("text")
    if "sort_order" in request.data:
        try:
            entry.sort_order = int(request.data.get("sort_order") or 0)
        except (TypeError, ValueError):
            return Response({"detail": "sort_order không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)
        update_fields.append("sort_order")
    if update_fields:
        update_fields.append("updated_at")
        entry.save(update_fields=update_fields)
    return Response(KshipNotePresetSerializer(entry).data, status=status.HTTP_200_OK)
