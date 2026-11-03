from __future__ import annotations

from rest_framework import serializers

from apps.inventory.models import PendingScanKind, PendingShipmentScan, ReturnType


class PendingShipmentScanSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    matched_order_code = serializers.CharField(
        source="matched_order.order_code", read_only=True, default=""
    )
    scanned_by_name = serializers.SerializerMethodField()

    class Meta:
        model = PendingShipmentScan
        fields = [
            "id", "code", "kind", "warehouse", "warehouse_name", "return_type",
            "status", "matched_order", "matched_order_code", "result_ref", "error",
            "attempts", "last_attempt_at", "scanned_by_name", "created_at",
        ]

    def get_scanned_by_name(self, obj) -> str:
        user = obj.scanned_by
        if user is None:
            return ""
        return user.display_name or user.username


class CreatePendingScansBatchSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=PendingScanKind.choices)
    warehouse_id = serializers.IntegerField()
    return_type = serializers.ChoiceField(
        choices=ReturnType.choices, required=False, allow_blank=True, default=""
    )
    codes = serializers.ListField(
        child=serializers.CharField(allow_blank=True), allow_empty=False
    )
    # Đợt quét gốc — phiếu trả tạo ra khi cron khớp sẽ kế thừa batch id này.
    scan_batch_id = serializers.UUIDField(required=False, allow_null=True, default=None)
