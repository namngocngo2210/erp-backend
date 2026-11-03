from __future__ import annotations

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.hr.models import Employee, Team


class EmployeeSummarySerializer(serializers.ModelSerializer):
    """Tóm tắt nhân sự cho tổ chức phòng ban — KHÔNG lộ PII."""

    class Meta:
        model = Employee
        fields = ("id", "employee_code", "full_name", "position_title")


class TeamSerializer(serializers.ModelSerializer):
    lead = EmployeeSummarySerializer(read_only=True, allow_null=True)
    members = serializers.SerializerMethodField()

    class Meta:
        model = Team
        fields = ("id", "name", "lead", "members")

    @extend_schema_field(EmployeeSummarySerializer(many=True))
    def get_members(self, obj: Team) -> list[dict]:
        return EmployeeSummarySerializer(
            [m.employee for m in obj.members.all()], many=True
        ).data


class DepartmentOrgItemSerializer(serializers.Serializer):
    """1 phòng ban kèm trưởng phòng + team (dựng từ dict trong view)."""

    department_id = serializers.IntegerField()
    head = EmployeeSummarySerializer(allow_null=True)
    teams = TeamSerializer(many=True)


class HeadSetSerializer(serializers.Serializer):
    employee_id = serializers.IntegerField(allow_null=True)


class TeamCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    lead_employee_id = serializers.IntegerField(required=False, allow_null=True)
    member_employee_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list
    )


class TeamUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=False)
    lead_employee_id = serializers.IntegerField(required=False, allow_null=True)
    member_employee_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False
    )
