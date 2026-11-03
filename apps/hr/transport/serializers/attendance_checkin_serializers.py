from __future__ import annotations

import base64
import binascii

from rest_framework import serializers

_MAX_PHOTO_BYTES = 800 * 1024


def _sniff_content_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


class CheckinSerializer(serializers.Serializer):
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)
    photo = serializers.CharField()  # data URL base64

    def validate_photo(self, value: str) -> bytes:
        raw = value.split(",", 1)[1] if value.startswith("data:") else value
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise serializers.ValidationError("Ảnh không hợp lệ.") from exc
        if not data:
            raise serializers.ValidationError("Ảnh rỗng.")
        if len(data) > _MAX_PHOTO_BYTES:
            raise serializers.ValidationError("Ảnh quá lớn (tối đa 800KB).")
        return data

    def validate(self, attrs):
        content_type = _sniff_content_type(attrs["photo"])
        if content_type is None:
            raise serializers.ValidationError("Ảnh phải là JPEG hoặc PNG.")
        attrs["content_type"] = content_type
        return attrs
