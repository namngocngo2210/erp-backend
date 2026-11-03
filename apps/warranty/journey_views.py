from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import require_method_permission, require_permission

from .claim_repository import WarrantyClaimRepository
from .journey_repository import WarrantyJourneyRepository
from .journey_service import WarrantyJourneyService
from .serializers import WarrantyJourneySerializer, WarrantyJourneyWriteSerializer


@extend_schema(
    # POST không đọc body (claim_id lấy từ URL) — xem ghi chú ở complete_warranty_claim.
    request=None,
    responses={
        201: WarrantyJourneySerializer,
        404: OpenApiResponse(description="Không tìm thấy phiếu"),
    },
)
@api_view(["POST"])
# Audit 2026-08-19 N2: ghi can quyen ghi, khong gate bang require_section (=:view).
@permission_classes([IsAuthenticated, require_permission("warranty:edit")])
def add_journey(request, claim_id: int):
    claim = WarrantyClaimRepository.get_claim(claim_id)
    if claim is None:
        return Response({"detail": "Không tìm thấy phiếu."}, status=status.HTTP_404_NOT_FOUND)
    journey = WarrantyJourneyService.add_journey(
        claim, created_by=request.user if request.user.is_authenticated else None
    )
    return Response(WarrantyJourneySerializer(journey).data, status=status.HTTP_201_CREATED)


@extend_schema(
    request=WarrantyJourneyWriteSerializer,
    responses={
        200: WarrantyJourneySerializer,
        204: OpenApiResponse(description="Đã xóa"),
        404: OpenApiResponse(description="Không tìm thấy hành trình"),
    },
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_method_permission(PATCH="warranty:edit", DELETE="warranty:edit")])
def update_delete_journey(request, journey_id: int):
    journey = WarrantyJourneyRepository.get(journey_id)
    if journey is None:
        return Response({"detail": "Không tìm thấy hành trình."}, status=status.HTTP_404_NOT_FOUND)
    if request.method == "DELETE":
        WarrantyJourneyService.delete_journey(journey)
        return Response(status=status.HTTP_204_NO_CONTENT)
    write = WarrantyJourneyWriteSerializer(data=request.data, partial=True)
    write.is_valid(raise_exception=True)
    journey = WarrantyJourneyService.update_journey(journey, dict(write.validated_data))
    return Response(WarrantyJourneySerializer(journey).data)
