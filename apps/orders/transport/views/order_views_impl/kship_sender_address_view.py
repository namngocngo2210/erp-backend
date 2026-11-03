"""CRUD địa chỉ người gửi KShip.

Đọc: ai có quyền orders (cần chọn địa chỉ khi tạo vận đơn). Sửa: kiotviet:edit —
nhập sai location_id/ward_id là vận đơn tạo SAI ĐIỂM GỬI, nhạy hơn note preset.
GET nhận ?ids=<order_ids> để trả kèm default_id theo rule kênh/branch (đơn đầu lô).
"""
from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import ORDER_SECTIONS, require_method_permission, require_permission

from apps.orders.application.services.kship_sender_service import resolve_default_sender_address
from apps.orders.models import KshipSenderAddress, Order


class KshipSenderAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = KshipSenderAddress
        fields = [
            "id",
            "name",
            "code",
            "location_id",
            "ward_id",
            "address",
            "mobile",
            "sort_order",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "code", "created_at", "updated_at"]

    def validate_name(self, value: str) -> str:
        name = (value or "").strip()
        if not name:
            raise serializers.ValidationError("Tên địa chỉ gửi không được để trống.")
        return name

    def validate_address(self, value: str) -> str:
        address = (value or "").strip()
        if not address:
            raise serializers.ValidationError("Địa chỉ không được để trống.")
        return address

    def validate_mobile(self, value: str) -> str:
        mobile = (value or "").strip()
        if not mobile:
            raise serializers.ValidationError("SĐT người gửi không được để trống.")
        return mobile

    def validate_location_id(self, value: int) -> int:
        if value is None or int(value) <= 0:
            raise serializers.ValidationError("location_id phải là số nguyên dương.")
        return int(value)

    def validate_ward_id(self, value: int) -> int:
        if value is None or int(value) <= 0:
            raise serializers.ValidationError("ward_id phải là số nguyên dương.")
        return int(value)


def _default_sender_address_id(request) -> int | None:
    raw = str(request.query_params.get("ids") or "").strip()
    if not raw:
        return None
    try:
        first_id = int(raw.split(",")[0])
    except (TypeError, ValueError):
        return None
    order = (
        Order.objects.select_related("kiotviet_channel")
        .filter(id=first_id, deleted_at__isnull=True)
        .first()
    )
    if order is None:
        return None
    row = resolve_default_sender_address(order)
    return row.id if row else None


@extend_schema(
    request=KshipSenderAddressSerializer,
    responses={
        200: inline_serializer(
            "KshipSenderAddressListResponse",
            fields={
                "items": KshipSenderAddressSerializer(many=True),
                "total": drf_serializers.IntegerField(),
                "default_id": drf_serializers.IntegerField(allow_null=True),
            },
        ),
        201: KshipSenderAddressSerializer,
    },
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_method_permission(GET=tuple(f"{x}:view" for x in ORDER_SECTIONS), POST="kiotviet:edit")])
def list_create_kship_sender_addresses(request):
    if request.method == "GET":
        items = list(KshipSenderAddress.objects.order_by("sort_order", "id"))
        data = KshipSenderAddressSerializer(items, many=True).data
        return Response({"items": data, "total": len(data), "default_id": _default_sender_address_id(request)})

    # Mutation gate chặt hơn GET: sai ID điểm gửi = vận đơn sai kho.
    if not require_permission("kiotviet:edit")().has_permission(request, None):
        return Response({"detail": "Bạn không có quyền quản lý địa chỉ gửi."}, status=status.HTTP_403_FORBIDDEN)

    serializer = KshipSenderAddressSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    entry = serializer.save(created_by=request.user if request.user.is_authenticated else None)
    return Response(KshipSenderAddressSerializer(entry).data, status=status.HTTP_201_CREATED)


@extend_schema(
    request=KshipSenderAddressSerializer,
    responses={
        200: KshipSenderAddressSerializer,
        204: OpenApiResponse(description="Đã xóa bản ghi"),
        404: OpenApiResponse(description="Không tìm thấy bản ghi"),
    },
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_permission("kiotviet:edit")])
def update_delete_kship_sender_address(request, entry_id: int):
    entry = KshipSenderAddress.objects.filter(id=entry_id).first()
    if entry is None:
        return Response({"detail": "Không tìm thấy bản ghi."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        entry.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = KshipSenderAddressSerializer(entry, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(KshipSenderAddressSerializer(entry).data, status=status.HTTP_200_OK)
