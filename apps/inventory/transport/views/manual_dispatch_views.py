"""Xuất kho tay từ trang chi tiết đơn.

Phân quyền theo đúng luật đã ghi ở `flow_pending_views.py`: hành động GHI (trừ tồn) gate
bằng "stock:edit". Không đẻ permission mới — permission mới lại phải nhớ gán role.
"""
from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.inventory.application.services.manual_dispatch_service import ManualDispatchService
from apps.users.permissions import require_permission


class _DispatchInputSerializer(drf.Serializer):
    order_ids = drf.ListField(
        child=drf.IntegerField(min_value=1), allow_empty=False, max_length=200
    )
    warehouse_id = drf.IntegerField(min_value=1)

    def validate_warehouse_id(self, value: int) -> int:
        # Kho đã tắt/xoá mềm vẫn còn id hợp lệ trong DB — không chặn ở đây thì sổ ghi
        # vào một kho không ai còn nhìn tới.
        from apps.warehouses.models import Warehouse

        exists = Warehouse.objects.filter(
            pk=value, is_active=True, deleted_at__isnull=True
        ).exists()
        if not exists:
            raise drf.ValidationError("Kho không tồn tại hoặc đã ngừng hoạt động.")
        return value


@extend_schema(
    request=_DispatchInputSerializer,
    responses={
        200: inline_serializer(
            "ManualDispatchResult",
            fields={
                "confirmed": drf.ListField(child=drf.IntegerField()),
                "skipped": inline_serializer(
                    "ManualDispatchSkipped",
                    many=True,
                    fields={"order_id": drf.IntegerField(), "reason": drf.CharField()},
                ),
            },
        ),
        400: OpenApiResponse(description="Danh sách đơn hoặc kho không hợp lệ"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("stock:edit")])
def manual_dispatch(request):
    serializer = _DispatchInputSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    result = ManualDispatchService.dispatch(
        order_ids=serializer.validated_data["order_ids"],
        warehouse_id=serializer.validated_data["warehouse_id"],
        actor=request.user,
    )
    return Response(result, status=status.HTTP_200_OK)
