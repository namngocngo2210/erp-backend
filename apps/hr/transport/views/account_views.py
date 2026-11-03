from __future__ import annotations

from django.db.models import Q
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.hr.application.services.employee_service import (
    AccountAlreadyLinkedError,
    EmployeeService,
)
from apps.hr.models import EmployeeAccount
from apps.hr.repositories import EmployeeRepository
from apps.hr.transport.serializers.employee_serializers import EmployeeSerializer
from apps.hr.transport.views._helpers import can as _can
from apps.hr.transport.views._helpers import forbidden as _forbidden
from apps.users.models import User
from apps.users.rbac import resolve_effective_permissions
from apps.users.serializers import AdminUserCreateSerializer

_ASSIGNABLE_LIMIT = 50


# Tạo + gắn tài khoản mới cho nhân sự, trả về nhân sự đã làm mới (201).
@extend_schema(
    request=AdminUserCreateSerializer,
    responses={201: EmployeeSerializer},
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@never_cache
def employee_accounts(request, employee_id: int):
    if not _can(request, "employees:edit"):
        return _forbidden()
    emp = EmployeeRepository.get_or_none(employee_id)
    if not emp:
        return Response({"detail": "Không tìm thấy nhân viên."}, status=404)
    ser = AdminUserCreateSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = dict(ser.validated_data)
    if not resolve_effective_permissions(request.user).is_superuser:
        data.pop("role_ids", None)
        data.pop("department_ids", None)
    EmployeeService.add_account(employee_id, data)
    fresh = EmployeeRepository.get_or_none(employee_id)
    return Response(EmployeeSerializer(fresh, context={'request': request}).data, status=status.HTTP_201_CREATED)


# Trả về list phẳng các tài khoản chưa gắn nhân sự (id, username, display_name).
@extend_schema(
    responses=inline_serializer(
        "HrAssignableUser",
        many=True,
        fields={
            "id": drf_serializers.IntegerField(),
            "username": drf_serializers.CharField(),
            "display_name": drf_serializers.CharField(),
        },
    ),
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
@never_cache
def assignable_users(request):
    """Danh sách User chưa gắn nhân sự nào (để gán vào một nhân sự)."""
    if not _can(request, "employees:edit"):
        return _forbidden()
    qs = User.objects.filter(deleted_at__isnull=True, employee_account__isnull=True)
    q = request.query_params.get("q", "").strip()
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(display_name__icontains=q))
    qs = qs.order_by("username")[:_ASSIGNABLE_LIMIT]
    return Response(
        [
            {"id": u.id, "username": u.username, "display_name": u.display_name}
            for u in qs
        ]
    )


# Gắn tài khoản có sẵn (body {user_id}) vào nhân sự, trả về nhân sự đã làm mới (201).
@extend_schema(
    request=inline_serializer(
        "HrEmployeeAccountLinkRequest",
        fields={"user_id": drf_serializers.IntegerField()},
    ),
    responses={201: EmployeeSerializer},
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@never_cache
def employee_account_link(request, employee_id: int):
    """Gắn một tài khoản đã tồn tại vào nhân sự."""
    if not _can(request, "employees:edit"):
        return _forbidden()
    emp = EmployeeRepository.get_or_none(employee_id)
    if not emp:
        return Response({"detail": "Không tìm thấy nhân viên."}, status=404)
    user_id = request.data.get("user_id")
    if not user_id:
        return Response({"detail": "Thiếu user_id."}, status=400)
    try:
        EmployeeService.link_existing_account(employee_id, user_id)
    except User.DoesNotExist:
        return Response({"detail": "Không tìm thấy tài khoản."}, status=404)
    except AccountAlreadyLinkedError as exc:
        return Response({"detail": str(exc)}, status=400)
    fresh = EmployeeRepository.get_or_none(employee_id)
    return Response(EmployeeSerializer(fresh, context={'request': request}).data, status=status.HTTP_201_CREATED)


# Gỡ liên kết tài khoản khỏi nhân sự (204).
@extend_schema(responses={204: None})
@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
@never_cache
def employee_account_detail(request, employee_id: int, account_id: int):
    if not _can(request, "employees:edit"):
        return _forbidden()
    try:
        EmployeeService.unlink_account(employee_id, account_id)
    except EmployeeAccount.DoesNotExist:
        return Response({"detail": "Không tìm thấy tài khoản."}, status=404)
    return Response(status=status.HTTP_204_NO_CONTENT)
