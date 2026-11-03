"""Serializers cho cơ chế lương (EmployeeCompensation)."""

from __future__ import annotations

from rest_framework import serializers

from apps.hr.models import EmployeeCompensation


class CompensationRowSerializer(serializers.Serializer):
    """Một dòng trên trang Cơ chế lương: nhân sự + tham số lương hiện hành."""

    employee_id = serializers.IntegerField()
    employee_code = serializers.CharField()
    full_name = serializers.CharField()
    department = serializers.CharField(allow_blank=True)
    base_salary = serializers.DecimalField(
        max_digits=12, decimal_places=0, allow_null=True
    )
    standard_workdays = serializers.DecimalField(max_digits=4, decimal_places=1)
    attendance_bonus = serializers.DecimalField(max_digits=12, decimal_places=0)
    responsibility_allowance = serializers.DecimalField(max_digits=12, decimal_places=0)
    kpi_rate = serializers.DecimalField(
        max_digits=10, decimal_places=5, allow_null=True
    )
    lunch_per_meal = serializers.DecimalField(max_digits=12, decimal_places=0)
    other_allowance = serializers.DecimalField(max_digits=12, decimal_places=0)
    social_insurance = serializers.DecimalField(
        max_digits=12, decimal_places=0, allow_null=True
    )
    note = serializers.CharField(allow_blank=True)


class CompensationUpdateSerializer(serializers.ModelSerializer):
    # Lương cơ bản sống ở SalaryRecord (append-only) — gửi kèm thì view gọi
    # SalaryService.set_salary; bỏ trống = không đổi lương.
    base_salary = serializers.DecimalField(
        max_digits=12, decimal_places=0, required=False, allow_null=True, min_value=0
    )

    class Meta:
        model = EmployeeCompensation
        fields = [
            "base_salary",
            "standard_workdays",
            "attendance_bonus",
            "responsibility_allowance",
            "kpi_rate",
            "lunch_per_meal",
            "other_allowance",
            "social_insurance",
            "note",
        ]
