from __future__ import annotations

from django.utils import timezone
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.hr.application.services.leave_service import LeaveService
from apps.hr.domain.leave import LeaveRuleError
from apps.hr.repositories import AttendanceRepository, LeaveRepository
from apps.hr.transport.serializers.leave_attendance_serializers import (
    AttendanceSerializer,
    LeaveRequestSerializer,
)
from apps.hr.transport.serializers.self_service_serializers import (
    MeLeaveCreateSerializer,
    MeProfileSerializer,
)


def _employee(request):
    # Quan hệ 1-N: tài khoản đăng nhập trỏ về nhân sự qua EmployeeAccount.
    account = getattr(request.user, "employee_account", None)
    return account.employee if account else None


def _no_employee():
    return Response(
        {"detail": "Tài khoản chưa gắn hồ sơ nhân viên."},
        status=status.HTTP_404_NOT_FOUND,
    )


# Hồ sơ của chính nhân viên đang đăng nhập.
@extend_schema(responses={200: MeProfileSerializer})
@api_view(["GET"])
@permission_classes([IsAuthenticated])
@never_cache
def me_profile(request):
    emp = _employee(request)
    if not emp:
        return _no_employee()
    return Response(MeProfileSerializer(emp).data)


# Bảng công theo tháng của chính nhân viên đang đăng nhập {items}.
@extend_schema(
    responses=inline_serializer(
        "HrMeAttendanceList",
        fields={"items": AttendanceSerializer(many=True)},
    ),
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
@never_cache
def me_attendance(request):
    emp = _employee(request)
    if not emp:
        return _no_employee()
    month = request.query_params.get("month")  # "YYYY-MM"
    now = timezone.localdate()
    year, mon = now.year, now.month
    if month:
        try:
            year_s, month_s = month.split("-")
            year, mon = int(year_s), int(month_s)
        except (ValueError, TypeError):
            year, mon = now.year, now.month
    qs = AttendanceRepository.for_month(emp.id, year, mon)
    return Response({"items": AttendanceSerializer(qs, many=True).data})


# GET: đơn nghỉ của chính nhân viên {items}; POST: tự tạo đơn nghỉ (201).
@extend_schema(
    methods=["GET"],
    responses=inline_serializer(
        "HrMeLeaveRequestList",
        fields={"items": LeaveRequestSerializer(many=True)},
    ),
)
@extend_schema(
    methods=["POST"],
    request=MeLeaveCreateSerializer,
    responses={201: LeaveRequestSerializer},
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
@never_cache
def me_leave_requests(request):
    emp = _employee(request)
    if not emp:
        return _no_employee()

    if request.method == "GET":
        qs = LeaveRepository.for_employee(emp.id)
        return Response({"items": LeaveRequestSerializer(qs, many=True).data})

    ser = MeLeaveCreateSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data
    try:
        lr = LeaveService.request(
            emp.id,
            leave_type=data["leave_type"],
            start_date=data["start_date"],
            end_date=data["end_date"],
            reason=data["reason"],
        )
    except LeaveRuleError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(LeaveRequestSerializer(lr).data, status=status.HTTP_201_CREATED)
