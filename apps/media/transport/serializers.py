from __future__ import annotations

from rest_framework import serializers

from apps.users.serializers import validate_media_api_key


class MediaSettingsUpdateSerializer(serializers.Serializer):
    base_url = serializers.URLField(required=False)
    shared_api_key = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=1024)
    is_active = serializers.BooleanField(required=False)

    def validate_shared_api_key(self, value):
        return validate_media_api_key(value)
