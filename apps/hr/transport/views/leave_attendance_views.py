from __future__ import annotations

from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import parse_limit, parse_required_pagination
from apps.hr.application.services.attendance_service import AttendanceService
from apps.hr.application.services.leave_service import LeaveService
from apps.hr.domain.leave import LeaveRuleError
from apps.hr.repositories import (
    AttendanceRepository,
    EmployeeRepository,
    LeaveRepository,
)
from apps.hr.transport.serializers.leave_attendance_serializers import (
    AttendanceSerializer,
    AttendanceSetSerializer,
    LeaveCreateSerializer,
    LeaveRequestSerializer,
)
from apps.hr.transport.views._helpers import can as _can
from apps.hr.transport.views._helpers import forbidden as _forbidden

_MAX_LIMIT = 200
# Trần lịch sử công một nhân sự khi không lọc tháng (audit 2026-09-23 L6).
_ATTENDANCE_MAX_LIMIT = 500


def _is_department_head_of_leave(request, leave) -> bool:
    """Người dùng hiện tại có phải trưởng phòng của nhân sự xin nghỉ không?"""
    from apps.hr.models import DepartmentHead, EmployeeAccount

    dept_id = leave.employee.department_id
    if not dept_id:
        return False
    # Phòng thủ chiều sâu (audit 2026-07-15b): head phải CÒN thuộc chính phòng đó và
    # chưa nghỉ việc — luồng chuyển phòng đã dọn DepartmentHead, nhưng dữ liệu cũ/đường
    # ghi khác không được phép giữ quyền duyệt phòng cũ.
    head_emp_id = (
        DepartmentHead.objects.filter(
            department_id=dept_id,
            employee__department_id=dept_id,
            employee__deleted_at__isnull=True,
        )
        .values_list("employee_id", flat=True)
        .first()
    )
    if head_emp_id is None:
        return False
    return EmployeeAccount.objects.filter(
        employee_id=head_emp_id, user_id=request.user.id
    ).exists()


def _can_approve_leave(request, leave) -> bool:
    """Duyệt/từ chối được nếu có quyền ``employees:approve`` HOẶC là trưởng phòng
    của nhân sự xin nghỉ (cộng dồn — không thu hẹp quyền hiện có)."""
    return _can(request, "employees:approve") or _is_department_head_of_leave(request, leave)


# GET: danh sách đơn nghỉ phân trang {items,total,limit,offset}; POST: tạo đơn (201).
@extend_schema(
    methods=["GET"],
    responses=inline_serializer(
        "HrLeaveRequestList",
        fields={
            "items": LeaveRequestSerializer(many=True),
            "total": drf_serializers.IntegerField(),
            "limit": drf_serializers.IntegerField(),
            "offset": drf_serializers.IntegerField(),
        },
    ),
)
@extend_schema(
    methods=["POST"],
    request=LeaveCreateSerializer,
    responses={201: LeaveRequestSerializer},
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
@never_cache
def leave_requests(request):
    if request.method == "GET":
        if not _can(request, "employees:view"):
            return _forbidden()
        pagination, err = parse_required_pagination(
            request, max_limit=_MAX_LIMIT, default_limit=50
        )
        if err is not None:
            return err
        qs = LeaveRepository.active()
        employee = request.query_params.get("employee")
        if employee:
            qs = qs.filter(employee_id=employee)
        status_f = request.query_params.get("status")
        if status_f:
            qs = qs.filter(status=status_f)
        qs = qs.order_by("-start_date")
        total = qs.count()
        items = LeaveRepository.paginate(
            qs, limit=pagination["limit"], offset=pagination["offset"]
        )
        return Response(
            {
                "items": LeaveRequestSerializer(items, many=True).data,
                "total": total,
                "limit": pagination["limit"],
                "offset": pagination["offset"],
            }
        )

    if not _can(request, "employees:edit"):
        return _forbidden()
    ser = LeaveCreateSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data
    if not EmployeeRepository.get_or_none(data["employee_id"]):
        return Response({"detail": "Không tìm thấy nhân viên."}, status=404)
    try:
        lr = LeaveService.request(
            data["employee_id"],
            leave_type=data["leave_type"],
            start_date=data["start_date"],
            end_date=data["end_date"],
            reason=data["reason"],
        )
    except LeaveRuleError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(
        LeaveRequestSerializer(lr).data, status=status.HTTP_201_CREATED
    )


# Duyệt đơn nghỉ — không body, trả về đơn đã cập nhật trạng thái.
@extend_schema(request=None, responses={200: LeaveRequestSerializer})
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@never_cache
def leave_approve(request, leave_id: int):
    leave = LeaveRepository.active().select_related("employee").filter(pk=leave_id).first()
    if not leave:
        return Response({"detail": "Không tìm thấy đơn nghỉ."}, status=404)
    if not _can_approve_leave(request, leave):
        return _forbidden()
    try:
        lr = LeaveService.approve(leave_id, approver_id=request.user.id)
    except LeaveRuleError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(LeaveRequestSerializer(lr).data)


# Từ chối đơn nghỉ — không body, trả về đơn đã cập nhật trạng thái.
@extend_schema(request=None, responses={200: LeaveRequestSerializer})
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@never_cache
def leave_reject(request, leave_id: int):
    leave = LeaveRepository.active().select_related("employee").filter(pk=leave_id).first()
    if not leave:
        return Response({"detail": "Không tìm thấy đơn nghỉ."}, status=404)
    if not _can_approve_leave(request, leave):
        return _forbidden()
    try:
        lr = LeaveService.reject(leave_id, approver_id=request.user.id)
    except LeaveRuleError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(LeaveRequestSerializer(lr).data)


# GET: bảng công của nhân sự {items}; POST: ghi nhận công một ngày (201).
@extend_schema(
    methods=["GET"],
    responses=inline_serializer(
        "HrEmployeeAttendanceList",
        fields={"items": AttendanceSerializer(many=True)},
    ),
)
@extend_schema(
    methods=["POST"],
    request=AttendanceSetSerializer,
    responses={201: AttendanceSerializer},
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
@never_cache
def employee_attendance(request, employee_id: int):
    if not EmployeeRepository.get_or_none(employee_id):
        return Response({"detail": "Không tìm thấy nhân viên."}, status=404)

    if request.method == "GET":
        if not _can(request, "employees:view"):
            return _forbidden()
        year = request.query_params.get("year")
        month = request.query_params.get("month")
        if year and month:
            qs = AttendanceRepository.for_month(employee_id, int(year), int(month))
        else:
            # Không lọc tháng = toàn bộ lịch sử công của một người: trần 500 dòng
            # (~1,4 năm), mới nhất trước (audit 2026-09-23 L6). Response giữ `items`.
            limit, limit_error = parse_limit(request, default=_ATTENDANCE_MAX_LIMIT, max_limit=_ATTENDANCE_MAX_LIMIT)
            if limit_error is not None:
                return limit_error
            qs = AttendanceRepository.active().filter(
                employee_id=employee_id
            ).order_by("-work_date")[:limit]
        return Response({"items": AttendanceSerializer(qs, many=True).data})

    if not _can(request, "employees:edit"):
        return _forbidden()
    ser = AttendanceSetSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data
    rec = AttendanceService.record(
        employee_id,
        work_date=data["work_date"],
        check_in=data.get("check_in"),
        check_out=data.get("check_out"),
        note=data["note"],
    )
    return Response(
        AttendanceSerializer(rec).data, status=status.HTTP_201_CREATED
    )
