from __future__ import annotations

from django.utils import timezone
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.hr.application.services.attendance_checkin_service import (
    AlreadyCompleteError,
    AttendanceCheckinService,
    OutsideGeofenceError,
)
from apps.hr.models import AttendanceEventKind
from apps.hr.repositories import AttendanceEventRepository, CompanyLocationRepository
from apps.hr.transport.serializers.attendance_checkin_serializers import CheckinSerializer


def _employee(request):
    # Quan hệ 1-N: tài khoản đăng nhập trỏ về nhân sự qua EmployeeAccount.
    account = getattr(request.user, "employee_account", None)
    return account.employee if account else None


# Cấu hình chấm công self-service: danh sách vị trí + trạng thái check-in/out hôm nay.
@extend_schema(
    responses=inline_serializer(
        "HrAttendanceConfig",
        fields={
            "locations": inline_serializer(
                "HrAttendanceConfigLocation",
                many=True,
                fields={
                    "id": drf_serializers.IntegerField(),
                    "name": drf_serializers.CharField(),
                    "latitude": drf_serializers.FloatField(allow_null=True),
                    "longitude": drf_serializers.FloatField(allow_null=True),
                    "radius_m": drf_serializers.IntegerField(),
                },
            ),
            "today": inline_serializer(
                "HrAttendanceConfigToday",
                fields={
                    "check_in": drf_serializers.CharField(allow_null=True),
                    "check_out": drf_serializers.CharField(allow_null=True),
                },
            ),
        },
    ),
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
@never_cache
def attendance_config(request):
    locations = [
        {
            "id": loc.id,
            "name": loc.name,
            "latitude": loc.latitude,
            "longitude": loc.longitude,
            "radius_m": loc.radius_m,
        }
        for loc in CompanyLocationRepository.active()
    ]
    today = {"check_in": None, "check_out": None}
    emp = _employee(request)
    if emp:
        work_date = timezone.localdate()
        for e in AttendanceEventRepository.for_day(emp.id, work_date):
            t = timezone.localtime(e.occurred_at).strftime("%H:%M")
            if e.kind == AttendanceEventKind.CHECK_IN:
                today["check_in"] = t
            else:
                today["check_out"] = t
    return Response({"locations": locations, "today": today})


# Check-in/out self-service kèm ảnh + GPS; trả về sự kiện vừa tạo (201).
@extend_schema(
    request=CheckinSerializer,
    responses={
        201: inline_serializer(
            "HrAttendanceCheckinResult",
            fields={
                "kind": drf_serializers.CharField(),
                "occurred_at": drf_serializers.DateTimeField(),
                "distance_m": drf_serializers.FloatField(allow_null=True),
                "location_name": drf_serializers.CharField(),
            },
        ),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@never_cache
def attendance_check_in(request):
    emp = _employee(request)
    if not emp:
        return Response(
            {"detail": "Tài khoản chưa gắn hồ sơ nhân viên."},
            status=status.HTTP_404_NOT_FOUND,
        )
    ser = CheckinSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    try:
        ev = AttendanceCheckinService.check_in(
            employee_id=emp.id,
            latitude=ser.validated_data["latitude"],
            longitude=ser.validated_data["longitude"],
            photo_bytes=ser.validated_data["photo"],
            content_type=ser.validated_data["content_type"],
        )
    except OutsideGeofenceError as exc:
        return Response(
            {
                "detail": "Bạn đang ở ngoài khu vực công ty.",
                "distance_m": exc.distance_m,
                "radius_m": exc.radius_m,
            },
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except AlreadyCompleteError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    return Response(
        {
            "kind": ev.kind,
            "occurred_at": ev.occurred_at,
            "distance_m": ev.distance_m,
            "location_name": ev.location.name if ev.location else "",
        },
        status=status.HTTP_201_CREATED,
    )
