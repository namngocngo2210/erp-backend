from __future__ import annotations

from rest_framework import serializers

from apps.hr.models import ContractType, EmploymentContract, SalaryRecord


class SalaryRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalaryRecord
        fields = (
            "id", "amount", "effective_from", "effective_to",
            "is_current", "reason", "created_at",
        )


class SalarySetSerializer(serializers.Serializer):
    amount = serializers.IntegerField(min_value=0)
    effective_from = serializers.DateField()
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class ContractSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmploymentContract
        fields = (
            "id", "contract_no", "contract_type", "start_date", "end_date",
            "base_salary", "file_url", "note", "created_at",
        )


class ContractSignSerializer(serializers.Serializer):
    contract_type = serializers.ChoiceField(choices=ContractType.choices)
    start_date = serializers.DateField()
    end_date = serializers.DateField(required=False, allow_null=True)
    base_salary = serializers.IntegerField(required=False, default=0, min_value=0)
    contract_no = serializers.CharField(required=False, allow_blank=True, default="")
    file_url = serializers.CharField(required=False, allow_blank=True, default="")
    note = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        end_date = attrs.get("end_date")
        if end_date is not None and end_date < attrs["start_date"]:
            raise serializers.ValidationError(
                {"end_date": "end_date must not be earlier than start_date"})
        return attrs
