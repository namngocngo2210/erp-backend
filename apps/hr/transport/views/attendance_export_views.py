"""Export Excel bảng chấm công tháng — xem docs/erp-hr-attendance-export.md."""

from __future__ import annotations

from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.hr.application.services.attendance_export_service import (
    AttendanceExportError,
    build_month_export,
)
from apps.hr.infrastructure.attendance_export_excel import build_attendance_workbook
from apps.hr.transport.views._helpers import can as _can
from apps.hr.transport.views._helpers import forbidden as _forbidden

_XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# Trả file xlsx nhị phân qua HttpResponse, không phải JSON → loại khỏi schema
# (cùng convention với attendance_event_photo).
@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def attendance_export(request):
    """Xuất bảng chấm công tháng (2 sheet Chi tiết/Tổng hợp) — gate employees:export."""
    if not _can(request, "employees:export"):
        return _forbidden()

    month = (request.query_params.get("month") or "").strip()
    try:
        summaries = build_month_export(month)
    except AttendanceExportError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    payload = build_attendance_workbook(month, summaries)
    response = HttpResponse(payload, content_type=_XLSX_CONTENT_TYPE)
    response["Content-Disposition"] = f'attachment; filename="cham-cong-{month}.xlsx"'
    return response
