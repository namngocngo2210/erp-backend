from __future__ import annotations

from rest_framework import serializers

from apps.hr.models import AttendanceRecord, LeaveRequest, LeaveType


class LeaveRequestSerializer(serializers.ModelSerializer):
    # approver giờ là FK (db_column=approver_id) — khai báo tường minh để giữ
    # nguyên key `approver_id` (int) trong response như trước.
    # allow_null: đơn chưa duyệt → approver_id = null (spec khớp response thật).
    approver_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = LeaveRequest
        fields = (
            "id", "employee", "leave_type", "start_date", "end_date",
            "days", "reason", "status", "approver_id", "decided_at", "created_at",
        )


class LeaveCreateSerializer(serializers.Serializer):
    employee_id = serializers.IntegerField()
    leave_type = serializers.ChoiceField(choices=LeaveType.choices)
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class AttendanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceRecord
        fields = (
            "id", "work_date", "check_in", "check_out", "note", "source", "created_at",
        )


class AttendanceSetSerializer(serializers.Serializer):
    work_date = serializers.DateField()
    check_in = serializers.TimeField(required=False, allow_null=True)
    check_out = serializers.TimeField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, default="")
