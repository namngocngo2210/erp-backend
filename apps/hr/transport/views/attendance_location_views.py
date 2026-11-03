from __future__ import annotations

from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.hr.models import CompanyLocation
from apps.hr.transport.serializers.location_serializers import LocationSerializer
from apps.hr.transport.views._helpers import can, forbidden


# GET: danh sách vị trí chấm công {items}; POST: tạo vị trí mới (201).
@extend_schema(
    methods=["GET"],
    responses=inline_serializer(
        "HrAttendanceLocationList",
        fields={"items": LocationSerializer(many=True)},
    ),
)
@extend_schema(
    methods=["POST"],
    request=LocationSerializer,
    responses={201: LocationSerializer},
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
@never_cache
def attendance_locations(request):
    if request.method == "GET":
        if not can(request, "employees:view"):
            return forbidden()
        rows = CompanyLocation.objects.all().order_by("name")
        return Response({"items": LocationSerializer(rows, many=True).data})
    if not can(request, "employees:edit"):
        return forbidden()
    ser = LocationSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    loc = ser.save()
    return Response(LocationSerializer(loc).data, status=status.HTTP_201_CREATED)


# PATCH: cập nhật một phần vị trí (trả LocationSerializer); DELETE: xoá (204).
@extend_schema(
    methods=["PATCH"],
    request=LocationSerializer,
    responses={200: LocationSerializer},
)
@extend_schema(
    methods=["DELETE"],
    responses={204: None},
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
@never_cache
def attendance_location_detail(request, location_id: int):
    if not can(request, "employees:edit"):
        return forbidden()
    loc = CompanyLocation.objects.filter(pk=location_id).first()
    if not loc:
        return Response({"detail": "Không tìm thấy vị trí."}, status=404)
    if request.method == "DELETE":
        loc.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    ser = LocationSerializer(instance=loc, data=request.data, partial=True)
    ser.is_valid(raise_exception=True)
    loc = ser.save()
    return Response(LocationSerializer(loc).data)
