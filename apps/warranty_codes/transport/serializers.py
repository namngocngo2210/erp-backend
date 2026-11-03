from rest_framework import serializers

from apps.warranty_codes.application.services.serial_format import format_serial
from apps.warranty_codes.models import WarrantyCode, WarrantyCodeBatch, WarrantyCodeScanLog


class WarrantyCodeBatchSerializer(serializers.ModelSerializer):
    # DRF không tự nhận "<fk>_id" trong Meta.fields — phải khai tường minh.
    product_id = serializers.IntegerField(read_only=True, allow_null=True)
    parent_id = serializers.IntegerField(read_only=True, allow_null=True)
    start_serial = serializers.SerializerMethodField()
    end_serial = serializers.SerializerMethodField()
    product_name = serializers.CharField(source="product.name", read_only=True, default=None)
    product_sku = serializers.CharField(source="product.sku", read_only=True, default=None)
    created_by_name = serializers.CharField(source="created_by.username", read_only=True, default=None)
    assigned_by_name = serializers.CharField(source="assigned_by.username", read_only=True, default=None)
    is_generating = serializers.BooleanField(read_only=True)
    generated_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = WarrantyCodeBatch
        fields = (
            "id", "batch_code", "start_seq", "end_seq", "start_serial", "end_serial",
            "quantity", "status", "is_generating", "generated_count",
            "product_id", "product_name", "product_sku", "parent_id",
            "created_by_name", "assigned_by_name", "assigned_at", "exported_at", "created_at",
        )

    def get_start_serial(self, obj) -> str:
        return format_serial(obj.start_seq)

    def get_end_serial(self, obj) -> str:
        return format_serial(obj.end_seq)


class CreateBatchSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1)
    # Bỏ trống = nối tiếp counter toàn cục; có giá trị = tạo đúng khoảng số mong muốn.
    start_seq = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    product_id = serializers.IntegerField(required=False, allow_null=True)


class AssignBatchSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()


class SplitBatchSerializer(serializers.Serializer):
    cut_seq = serializers.IntegerField()


class WarrantyCodeSerializer(serializers.ModelSerializer):
    batch_id = serializers.IntegerField(read_only=True)
    product_id = serializers.IntegerField(read_only=True, allow_null=True)
    batch_code = serializers.CharField(source="batch.batch_code", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True, default=None)
    product_sku = serializers.CharField(source="product.sku", read_only=True, default=None)
    status = serializers.SerializerMethodField()

    class Meta:
        model = WarrantyCode
        fields = ("id", "seq", "serial", "code", "batch_id", "batch_code", "product_id", "product_name", "product_sku", "status", "created_at")

    def get_status(self, obj) -> str:
        return "assigned" if obj.product_id else "reserved"


class WarrantyCodeScanLogSerializer(serializers.ModelSerializer):
    code_value = serializers.CharField(source="code.code", read_only=True, default="")
    serial = serializers.CharField(source="code.serial", read_only=True, default="")
    result_label = serializers.CharField(source="get_result_display", read_only=True)
    phone_masked = serializers.SerializerMethodField()

    class Meta:
        model = WarrantyCodeScanLog
        fields = (
            "id", "keyword", "code_value", "serial", "product_sku", "phone_masked",
            "result", "result_label", "channel", "ip_address", "user_agent", "created_at",
        )

    def get_phone_masked(self, obj) -> str:
        phone = obj.phone or ""
        if len(phone) < 7:
            return phone
        return f"{phone[:3]}****{phone[-3:]}"
