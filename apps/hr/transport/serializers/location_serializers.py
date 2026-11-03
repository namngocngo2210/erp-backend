from __future__ import annotations

from rest_framework import serializers

from apps.hr.models import CompanyLocation


class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompanyLocation
        fields = ("id", "name", "latitude", "longitude", "radius_m", "is_active",
                  "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_name(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Tên vị trí không được để trống.")
        return value
