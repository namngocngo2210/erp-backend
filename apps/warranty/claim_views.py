from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import parse_required_pagination
from apps.users.permissions import require_method_permission, require_permission

from .claim_repository import WarrantyClaimRepository
from .claim_service import WarrantyClaimExists, WarrantyClaimService
from .serializers import WarrantyClaimSerializer, WarrantyClaimWriteSerializer


def _query_int(request, key):
    try:
        return int(request.query_params.get(key))
    except (TypeError, ValueError):
        return None


@extend_schema(
    request=WarrantyClaimWriteSerializer,
    responses={
        200: inline_serializer(
            "WarrantyClaimListResponse",
            fields={
                "results": WarrantyClaimSerializer(many=True),
                "total": drf_serializers.IntegerField(),
            },
        ),
        201: WarrantyClaimSerializer,
        400: OpenApiResponse(description="Dữ liệu không hợp lệ"),
        409: OpenApiResponse(description="Đơn hàng này đã có phiếu bảo hành"),
    },
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_method_permission(GET="warranty:view", POST="warranty:create")])
def list_create_warranty_claims(request):
    if request.method == "POST":
        write = WarrantyClaimWriteSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        order_id = write.validated_data.get("order_id")
        if not order_id:
            return Response({"detail": "Thiếu order_id."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            claim = WarrantyClaimService.create_claim(
                order_id=order_id,
                data=write.validated_data,
                created_by=request.user if request.user.is_authenticated else None,
            )
        except WarrantyClaimExists:
            return Response(
                {"detail": "Đơn hàng này đã có phiếu bảo hành."}, status=status.HTTP_409_CONFLICT
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(WarrantyClaimSerializer(claim).data, status=status.HTTP_201_CREATED)

    pagination, error = parse_required_pagination(request)
    if error is not None:
        return error
    rows, total = WarrantyClaimRepository.list_claims(
        q=request.query_params.get("q", ""),
        status=_query_int(request, "status"),
        limit=pagination["limit"],
        offset=pagination["offset"],
    )
    return Response({"results": WarrantyClaimSerializer(rows, many=True).data, "total": total})


@extend_schema(
    request=WarrantyClaimWriteSerializer,
    responses={
        200: WarrantyClaimSerializer,
        204: OpenApiResponse(description="Đã xóa phiếu"),
        404: OpenApiResponse(description="Không tìm thấy phiếu"),
    },
)
@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_method_permission(GET="warranty:view", PATCH="warranty:edit", DELETE="warranty:delete")])
def detail_update_delete_warranty_claim(request, claim_id: int):
    claim = WarrantyClaimRepository.get_claim(claim_id)
    if claim is None:
        return Response(
            {"detail": "Không tìm thấy phiếu bảo hành."}, status=status.HTTP_404_NOT_FOUND
        )

    if request.method == "GET":
        return Response(WarrantyClaimSerializer(claim).data)

    if request.method == "DELETE":
        WarrantyClaimService.soft_delete(claim)
        return Response(status=status.HTTP_204_NO_CONTENT)

    write = WarrantyClaimWriteSerializer(data=request.data, partial=True)
    write.is_valid(raise_exception=True)
    data = dict(write.validated_data)
    data.pop("order_id", None)  # order không đổi sau khi tạo
    claim = WarrantyClaimService.update_claim(claim, data)
    return Response(WarrantyClaimSerializer(claim).data, status=status.HTTP_200_OK)


@extend_schema(
    # POST không đọc body (claim_id lấy từ URL). Không khai `request=None` thì
    # spectacular cố đoán serializer, không đoán được và bỏ qua view.
    request=None,
    responses={
        200: WarrantyClaimSerializer,
        404: OpenApiResponse(description="Không tìm thấy phiếu"),
    },
)
@api_view(["POST"])
# Audit 2026-08-19 N2: ghi can quyen ghi, khong gate bang require_section (=:view).
@permission_classes([IsAuthenticated, require_permission("warranty:edit")])
def complete_warranty_claim(request, claim_id: int):
    claim = WarrantyClaimRepository.get_claim(claim_id)
    if claim is None:
        return Response(
            {"detail": "Không tìm thấy phiếu bảo hành."}, status=status.HTTP_404_NOT_FOUND
        )
    claim = WarrantyClaimService.complete(claim)
    return Response(WarrantyClaimSerializer(claim).data)


@extend_schema(
    request=inline_serializer(
        "WarrantyClaimBulkDeleteRequest",
        fields={"ids": drf_serializers.ListField(child=drf_serializers.IntegerField())},
    ),
    responses={
        200: inline_serializer(
            "WarrantyClaimBulkDeleteResponse",
            fields={"deleted": drf_serializers.IntegerField()},
        ),
        400: OpenApiResponse(description="Danh sách id không hợp lệ"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("warranty:delete")])
def bulk_delete_warranty_claims(request):
    ids = request.data.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return Response({"detail": "Chưa chọn phiếu bảo hành."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return Response({"detail": "Danh sách id không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)
    deleted = WarrantyClaimService.bulk_soft_delete(ids)
    return Response({"deleted": deleted}, status=status.HTTP_200_OK)
