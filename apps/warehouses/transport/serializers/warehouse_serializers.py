from rest_framework import serializers

from apps.warehouses.models import Warehouse


class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = ("id", "code", "name", "is_active", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_code(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Mã kho không được để trống.")
        return value

    def validate_name(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Tên kho không được để trống.")
        return value
