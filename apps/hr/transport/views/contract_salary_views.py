from __future__ import annotations

from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.hr.application.services.contract_service import ContractService
from apps.hr.application.services.salary_service import SalaryService
from apps.hr.repositories import (
    ContractRepository,
    EmployeeRepository,
    SalaryRepository,
)
from apps.hr.transport.serializers.contract_salary_serializers import (
    ContractSerializer,
    ContractSignSerializer,
    SalaryRecordSerializer,
    SalarySetSerializer,
)
from apps.hr.transport.views._helpers import can as _can
from apps.hr.transport.views._helpers import forbidden as _forbidden


# GET: lịch sử lương {items}; POST: đặt mức lương mới (201).
@extend_schema(
    methods=["GET"],
    responses=inline_serializer(
        "HrSalaryHistory",
        fields={"items": SalaryRecordSerializer(many=True)},
    ),
)
@extend_schema(
    methods=["POST"],
    request=SalarySetSerializer,
    responses={201: SalaryRecordSerializer},
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
@never_cache
def employee_salaries(request, employee_id: int):
    if not EmployeeRepository.get_or_none(employee_id):
        return Response({"detail": "Không tìm thấy nhân viên."}, status=404)

    if request.method == "GET":
        if not _can(request, "salaries:view"):
            return _forbidden()
        history = SalaryRepository.history(employee_id)
        return Response(
            {"items": SalaryRecordSerializer(history, many=True).data}
        )

    # Ghi lương cũng thuộc section salaries: employees:edit là quyền quản lý hồ sơ,
    # không được phép đặt lương (audit 2026-07-14).
    if not _can(request, "salaries:edit"):
        return _forbidden()
    ser = SalarySetSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    record = SalaryService.set_salary(
        employee_id,
        amount=ser.validated_data["amount"],
        effective_from=ser.validated_data["effective_from"],
        reason=ser.validated_data["reason"],
        created_by_id=request.user.id,
    )
    return Response(
        SalaryRecordSerializer(record).data, status=status.HTTP_201_CREATED
    )


# GET: danh sách hợp đồng {items}; POST: ký hợp đồng mới (201).
@extend_schema(
    methods=["GET"],
    responses=inline_serializer(
        "HrContractList",
        fields={"items": ContractSerializer(many=True)},
    ),
)
@extend_schema(
    methods=["POST"],
    request=ContractSignSerializer,
    responses={201: ContractSerializer},
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
@never_cache
def employee_contracts(request, employee_id: int):
    if not EmployeeRepository.get_or_none(employee_id):
        return Response({"detail": "Không tìm thấy nhân viên."}, status=404)

    if request.method == "GET":
        if not _can(request, "salaries:view"):
            return _forbidden()
        contracts = ContractRepository.for_employee(employee_id)
        return Response(
            {"items": ContractSerializer(contracts, many=True).data}
        )

    if not _can(request, "salaries:edit"):
        return _forbidden()
    ser = ContractSignSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    contract = ContractService.sign(employee_id, **ser.validated_data)
    return Response(
        ContractSerializer(contract).data, status=status.HTTP_201_CREATED
    )
