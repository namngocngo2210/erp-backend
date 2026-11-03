from __future__ import annotations

from rest_framework import serializers

from apps.hr.models import Employee, LeaveType


class MeProfileSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True, default="")
    manager_name = serializers.CharField(source="manager.full_name", read_only=True, default="")
    job_title_display = serializers.CharField(source="get_job_title_display", read_only=True)
    employment_status_display = serializers.CharField(
        source="get_employment_status_display", read_only=True
    )
    username = serializers.SerializerMethodField()

    def get_username(self, obj) -> str:
        user = obj.primary_user
        return user.username if user else ""

    class Meta:
        model = Employee
        # KHÔNG lộ lương/id_number/address ở ESS — chỉ thông tin định danh cơ bản.
        fields = (
            "id", "employee_code", "full_name", "department_name",
            "manager_name", "job_title", "job_title_display",
            "employment_status", "employment_status_display",
            "hire_date", "phone", "personal_email", "username",
        )


class MeLeaveCreateSerializer(serializers.Serializer):
    """Như LeaveCreateSerializer nhưng KHÔNG nhận employee_id (server tự gán)."""

    leave_type = serializers.ChoiceField(choices=LeaveType.choices)
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    reason = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if attrs["end_date"] < attrs["start_date"]:
            raise serializers.ValidationError("Ngày kết thúc không được trước ngày bắt đầu.")
        return attrs
