from rest_framework import serializers

from apps.exports.models import ExportJob


class ExportCreateSerializer(serializers.Serializer):
    kind = serializers.CharField(max_length=50)
    # Kiểu dict tự do: mỗi definition tự validate phần của mình (registry.validate).
    params = serializers.DictField(required=False, default=dict)


class ExportJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExportJob
        fields = (
            "id", "kind", "status", "total_rows", "progress_rows",
            "file_name", "file_size", "error_message", "created_at", "finished_at",
        )
        read_only_fields = fields
