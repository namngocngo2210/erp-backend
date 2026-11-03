from rest_framework import serializers
from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = AuditLog
        fields = [
            'id',
            'user',
            'action_type',
            'entity_type',
            'entity_id',
            'method',
            'timestamp',
            'ip_address',
            'duration_ms',
            'source',
            'response_data',
            'curl_command'
        ]
        read_only_fields = ['id', 'timestamp']


class AuditLogListSerializer(serializers.ModelSerializer):
    """Serializer nhẹ cho danh sách - chỉ trả về curl_command"""

    # Trần ký tự curl_command ở LIST: TextField không giới hạn (body request nằm
    # nguyên trong lệnh curl) × 100 dòng từng đẩy payload danh sách lên nhiều MB.
    # FE hiện không render field này ở trang Nhật ký; bản đầy đủ vẫn có qua
    # AuditLogSerializer (detail) (audit 2026-07-28 DB-12).
    _CURL_COMMAND_MAX_CHARS = 2000

    user = serializers.StringRelatedField(read_only=True)
    curl_command = serializers.SerializerMethodField()

    def get_curl_command(self, obj) -> str | None:
        value = obj.curl_command
        # Giữ nguyên None/"" như CharField cũ để không đổi shape response.
        if not value or len(value) <= self._CURL_COMMAND_MAX_CHARS:
            return value
        return value[: self._CURL_COMMAND_MAX_CHARS] + "…"

    class Meta:
        model = AuditLog
        fields = [
            'id',
            'user',
            'action_type',
            'entity_type',
            'entity_id',
            'method',
            'timestamp',
            'duration_ms',
            'source',
            'curl_command'
        ]
        read_only_fields = ['id', 'timestamp']
