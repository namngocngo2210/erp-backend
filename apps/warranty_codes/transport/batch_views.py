from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import parse_required_pagination
from apps.users.auth_core import user_has_any_permission
from apps.users.permissions import require_method_permission, require_permission, require_section
from apps.warranty_codes import repository
from apps.warranty_codes.application.services.batch_service import WarrantyCodeBatchService
from apps.warranty_codes.transport._query_params import query_date, query_int
from apps.warranty_codes.transport.serializers import (
    AssignBatchSerializer,
    CreateBatchSerializer,
    SplitBatchSerializer,
    WarrantyCodeBatchSerializer,
)


@extend_schema(
    request=CreateBatchSerializer,
    responses={
        200: inline_serializer(
            "WarrantyCodeBatchListResponse",
            fields={"results": WarrantyCodeBatchSerializer(many=True), "total": drf_serializers.IntegerField()},
        ),
        201: WarrantyCodeBatchSerializer,
        400: OpenApiResponse(description="Dữ liệu không hợp lệ"),
    },
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_method_permission(GET="warranty-codes:view", POST="warranty-codes:create")])
def list_create_batches(request):
    if request.method == "POST":
        if not user_has_any_permission(request.user, "warranty-codes:create"):
            return Response(
                {"detail": "Bạn không có quyền tạo lô mã."}, status=status.HTTP_403_FORBIDDEN
            )
        ser = CreateBatchSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            batch = WarrantyCodeBatchService.create_batch(
                quantity=ser.validated_data["quantity"],
                start_seq=ser.validated_data.get("start_seq"),
                product_id=ser.validated_data.get("product_id"),
                created_by=request.user,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(WarrantyCodeBatchSerializer(batch).data, status=status.HTTP_201_CREATED)

    pagination, error = parse_required_pagination(request)
    if error is not None:
        return error
    rows, total = repository.list_batches(
        q=request.query_params.get("q"),
        product_id=query_int(request, "product_id"),
        status=request.query_params.get("status"),
        date_from=query_date(request, "date_from"),
        date_to=query_date(request, "date_to"),
        limit=pagination["limit"],
        offset=pagination["offset"],
    )
    return Response({"results": WarrantyCodeBatchSerializer(rows, many=True).data, "total": total})


@extend_schema(request=AssignBatchSerializer, responses={200: WarrantyCodeBatchSerializer})
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("warranty-codes:edit")])
def assign_batch(request, batch_id: int):
    ser = AssignBatchSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    try:
        batch = WarrantyCodeBatchService.assign_product(
            batch_id=batch_id, product_id=ser.validated_data["product_id"], assigned_by=request.user
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(WarrantyCodeBatchSerializer(batch).data)


@extend_schema(
    request=SplitBatchSerializer,
    responses={
        200: inline_serializer(
            "WarrantyCodeBatchSplitResponse",
            fields={"parent": WarrantyCodeBatchSerializer(), "child": WarrantyCodeBatchSerializer()},
        )
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("warranty-codes:edit")])
def split_batch(request, batch_id: int):
    ser = SplitBatchSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    try:
        parent, child = WarrantyCodeBatchService.split_batch(
            batch_id=batch_id, cut_seq=ser.validated_data["cut_seq"], user=request.user
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(
        {"parent": WarrantyCodeBatchSerializer(parent).data, "child": WarrantyCodeBatchSerializer(child).data}
    )
