from __future__ import annotations

from django.db.models import Q
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import parse_required_pagination
from apps.hr.infrastructure.photo_storage import get_object_bytes
from apps.hr.models import AttendanceEvent
from apps.hr.transport.views._helpers import can, forbidden

_MAX_LIMIT = 200
# Chỉ phục vụ ảnh với content-type an toàn (chống stored-XSS khi serve trên app-origin).
_ALLOWED_PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp"}


# Sự kiện chấm công gộp theo (nhân sự, ngày): mỗi dòng có check_in/check_out
# là sub-object nullable. Response phân trang {items,total,limit,offset}.
@extend_schema(
    responses=inline_serializer(
        "HrAttendanceEventList",
        fields={
            "items": inline_serializer(
                "HrAttendanceEventRow",
                many=True,
                fields={
                    "employee_id": drf_serializers.IntegerField(),
                    "employee_code": drf_serializers.CharField(),
                    "employee_name": drf_serializers.CharField(),
                    "department_name": drf_serializers.CharField(),
                    "work_date": drf_serializers.CharField(),
                    "check_in": inline_serializer(
                        "HrAttendanceEventSubIn",
                        required=False,
                        allow_null=True,
                        fields={
                            "event_id": drf_serializers.IntegerField(),
                            "occurred_at": drf_serializers.CharField(),
                            "distance_m": drf_serializers.FloatField(allow_null=True),
                            "location_name": drf_serializers.CharField(),
                            "latitude": drf_serializers.FloatField(allow_null=True),
                            "longitude": drf_serializers.FloatField(allow_null=True),
                            "photo_url": drf_serializers.CharField(),
                        },
                    ),
                    "check_out": inline_serializer(
                        "HrAttendanceEventSubOut",
                        required=False,
                        allow_null=True,
                        fields={
                            "event_id": drf_serializers.IntegerField(),
                            "occurred_at": drf_serializers.CharField(),
                            "distance_m": drf_serializers.FloatField(allow_null=True),
                            "location_name": drf_serializers.CharField(),
                            "latitude": drf_serializers.FloatField(allow_null=True),
                            "longitude": drf_serializers.FloatField(allow_null=True),
                            "photo_url": drf_serializers.CharField(),
                        },
                    ),
                },
            ),
            "total": drf_serializers.IntegerField(),
            "limit": drf_serializers.IntegerField(),
            "offset": drf_serializers.IntegerField(),
        },
    ),
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
@never_cache
def attendance_events(request):
    if not can(request, "employees:view"):
        return forbidden()
    pagination, err = parse_required_pagination(request, max_limit=_MAX_LIMIT, default_limit=50)
    if err is not None:
        return err
    qs = AttendanceEvent.objects.select_related("employee", "employee__department", "location")
    department = request.query_params.get("department")
    if department:
        qs = qs.filter(employee__department_id=department)
    employee = request.query_params.get("employee")
    if employee:
        qs = qs.filter(employee_id=employee)
    date_from = request.query_params.get("date_from")
    if date_from:
        qs = qs.filter(work_date__gte=date_from)
    date_to = request.query_params.get("date_to")
    if date_to:
        qs = qs.filter(work_date__lte=date_to)

    pairs = list(
        qs.values_list("employee_id", "work_date").distinct().order_by("-work_date", "employee_id")
    )
    total = len(pairs)
    page_pairs = pairs[pagination["offset"]: pagination["offset"] + pagination["limit"]]

    page_q = Q()
    for emp_id, wd in page_pairs:
        page_q |= Q(employee_id=emp_id, work_date=wd)
    events = qs.filter(page_q) if page_pairs else qs.none()

    grouped: dict = {}
    for ev in events:
        grouped.setdefault((ev.employee_id, ev.work_date), []).append(ev)

    def sub(ev):
        if ev is None:
            return None
        return {
            "event_id": ev.id,
            "occurred_at": ev.occurred_at.isoformat(),
            "distance_m": ev.distance_m,
            "location_name": ev.location.name if ev.location else "",
            "latitude": ev.latitude,
            "longitude": ev.longitude,
            "photo_url": f"/api/hr/attendance/events/{ev.id}/photo" if ev.photo_key else "",
        }

    rows = []
    for emp_id, wd in page_pairs:
        evs = grouped.get((emp_id, wd), [])
        check_in = next((e for e in evs if e.kind == "check_in"), None)
        check_out = next((e for e in evs if e.kind == "check_out"), None)
        ref = check_in or check_out or evs[0]
        emp = ref.employee
        rows.append({
            "employee_id": emp_id,
            "employee_code": emp.employee_code,
            "employee_name": emp.full_name,
            "department_name": emp.department.name if emp.department_id else "",
            "work_date": wd.isoformat(),
            "check_in": sub(check_in),
            "check_out": sub(check_out),
        })

    return Response({
        "items": rows,
        "total": total,
        "limit": pagination["limit"],
        "offset": pagination["offset"],
    })


# Trả về ảnh nhị phân (image/*) qua HttpResponse, không phải JSON → loại khỏi schema.
@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
@never_cache
def attendance_event_photo(request, event_id):
    if not can(request, "employees:view"):
        return forbidden()
    ev = AttendanceEvent.objects.filter(pk=event_id).first()
    if not ev or not ev.photo_key:
        return Response({"detail": "Không tìm thấy ảnh."}, status=404)
    data, content_type = get_object_bytes(ev.photo_key)
    safe_type = content_type if content_type in _ALLOWED_PHOTO_TYPES else "application/octet-stream"
    resp = HttpResponse(data, content_type=safe_type)
    resp["Content-Disposition"] = 'inline; filename="photo"'
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Security-Policy"] = "default-src 'none'; img-src 'self'"
    return resp
