from __future__ import annotations

from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.hr.transport.views._helpers import can, forbidden
from apps.users.models import Department


# Trả về danh sách phòng ban active dạng {items: [{id, name}]}.
@extend_schema(
    responses=inline_serializer(
        "HrDepartmentList",
        fields={
            "items": inline_serializer(
                "HrDepartmentItem",
                many=True,
                fields={
                    "id": drf_serializers.IntegerField(),
                    "name": drf_serializers.CharField(),
                },
            ),
        },
    ),
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
@never_cache
def list_departments(request):
    if not can(request, "employees:view"):
        return forbidden()
    rows = Department.objects.filter(is_active=True).order_by("name").values("id", "name")
    return Response({"items": list(rows)})
