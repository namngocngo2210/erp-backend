"""Serializers cấu hình Tác nghiệp CSKH: Stage / Result / Transition CRUD.

Validate nghiệp vụ đặt ở đây (KHÔNG đặt trong models, theo task S1-4):
- Stage `is_start=true`: chỉ 1 stage ACTIVE được là điểm khởi đầu.
- Transition: có `to_stage` thì bắt buộc `delay_minutes >= 1`; không có `to_stage`
  thì `delay_minutes` phải để trống.
"""
from __future__ import annotations

from rest_framework import serializers

from apps.contact_flow.models import OperationResult, OperationStage, OperationTransition


class StageSerializer(serializers.ModelSerializer):
    class Meta:
        model = OperationStage
        fields = [
            "id", "name", "color", "is_start", "allow_edit_due",
            "pool_enabled", "pool_minutes", "customer_label", "is_active", "position",
        ]

    def validate(self, attrs):
        is_start = attrs["is_start"] if "is_start" in attrs else getattr(self.instance, "is_start", False)
        if is_start:
            qs = OperationStage.objects.filter(is_start=True, is_active=True)
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError("Chỉ được 1 bước khởi đầu.")
        return attrs


class ResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = OperationResult
        fields = ["id", "name", "color", "is_close", "requires_schedule", "is_active", "position"]


class TransitionSerializer(serializers.ModelSerializer):
    from_stage_id = serializers.PrimaryKeyRelatedField(
        source="from_stage", queryset=OperationStage.objects.all()
    )
    from_stage_name = serializers.CharField(source="from_stage.name", read_only=True)
    result_id = serializers.PrimaryKeyRelatedField(
        source="result", queryset=OperationResult.objects.all()
    )
    result_name = serializers.CharField(source="result.name", read_only=True)
    to_stage_id = serializers.PrimaryKeyRelatedField(
        source="to_stage", queryset=OperationStage.objects.all(), required=False, allow_null=True
    )
    to_stage_name = serializers.SerializerMethodField()

    class Meta:
        model = OperationTransition
        fields = [
            "id", "from_stage_id", "from_stage_name", "result_id", "result_name",
            "to_stage_id", "to_stage_name", "delay_minutes",
        ]

    def get_to_stage_name(self, obj) -> str | None:
        return obj.to_stage.name if obj.to_stage_id else None

    def validate(self, attrs):
        to_stage = attrs["to_stage"] if "to_stage" in attrs else getattr(self.instance, "to_stage", None)
        delay = (
            attrs["delay_minutes"] if "delay_minutes" in attrs
            else getattr(self.instance, "delay_minutes", None)
        )
        if to_stage is not None:
            if not delay or delay < 1:
                raise serializers.ValidationError(
                    "Có bước kế tiếp thì bắt buộc nhập thời gian chờ (delay_minutes) từ 1 phút trở lên."
                )
        elif delay is not None:
            raise serializers.ValidationError("Không có bước kế tiếp thì không được nhập thời gian chờ.")
        return attrs
