import re

from drf_spectacular.utils import extend_schema_field, inline_serializer
from rest_framework import serializers

from .models import WarrantyRegistration
from apps.warranty.journey_service import JOURNEY_EDITABLE_FIELDS
from apps.warranty.models import WarrantyClaim, WarrantyClaimStatus, WarrantyJourney
from apps.warranty.tracking_status import journey_vc_groups

_PHONE_RE = re.compile(r"0\d{9,10}")


class WarrantyRegistrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = WarrantyRegistration
        fields = (
            "id",
            "source",
            "invoice_code",
            "full_name",
            "phone",
            "email",
            "note",
            "created_at",
        )
        read_only_fields = ("id", "created_at")

    def validate_invoice_code(self, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Mã hóa đơn không được để trống.")
        return value

    def validate_full_name(self, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Họ tên không được để trống.")
        return value

    def validate_phone(self, value: str) -> str:
        value = (value or "").strip()
        if not _PHONE_RE.fullmatch(value):
            raise serializers.ValidationError("Số điện thoại không hợp lệ.")
        return value

    def validate_note(self, value: str) -> str:
        return (value or "").strip()[:1000]


class WarrantyClaimOrderSerializer(serializers.ModelSerializer):
    """Thông tin đơn hiển thị trên phiếu (read-only, đọc live từ Order)."""

    kiotviet_channel_name = serializers.CharField(
        source="kiotviet_channel.name", read_only=True, default=""
    )

    class Meta:
        from apps.orders.models import Order

        model = Order
        fields = (
            "id",
            "name",
            "phone",
            "address",
            "product",
            "sku",
            "quantity",
            "total_after_discount",
            "shipping_code",
            "order_code",
            "invoice_code",
            "created_at",
            "kiotviet_channel_name",
        )
        read_only_fields = fields


class WarrantyJourneySerializer(serializers.ModelSerializer):
    """Đọc 1 hành trình bảo hành (section 2-7), kèm tên/mã SP và nhãn trạng thái."""

    product_name = serializers.CharField(source="product.name", read_only=True, default="")
    product_sku = serializers.CharField(source="product.sku", read_only=True, default="")
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    vc_groups = serializers.SerializerMethodField()

    class Meta:
        model = WarrantyJourney
        fields = (
            "id", "seq", "name", "status", "status_label",
            "receiver_name", "receiver_phone", "receiver_raw_address",
            "product", "product_name", "product_sku",
            "send_reason", "classification", "error_category",
            "shipping_form", "declared_value", "cod_amount", "carrier", "shipping_fee",
            "order_date", "new_tracking_code", "recall_code", "note",
            "warehouse_received_at", "inspection_result", "warehouse_check",
            "resolution", "resolution_declared_value", "resolution_cod", "resolution_note",
            "return_carrier", "return_shipping_fee", "return_order_date", "return_tracking_code",
            "return_status",
            "feedback_agree_review", "feedback_rating", "feedback_note",
            "claim_date", "claim_note", "carrier_response",
            "compensation_status", "compensation_amount",
            "new_tracking_best_status", "recall_best_status", "return_tracking_best_status",
            "tracking_synced_at", "vc_groups",
            "created_at", "updated_at",
        )
        read_only_fields = fields

    @extend_schema_field(
        inline_serializer(
            "WarrantyJourneyVcGroups",
            fields={
                "new": serializers.CharField(allow_null=True),
                "recall": serializers.CharField(allow_null=True),
                "return": serializers.CharField(allow_null=True),
                "main": serializers.CharField(),
            },
        )
    )
    def get_vc_groups(self, obj) -> dict:
        """Nhóm trạng thái VC tự động theo Best — per-code + main (xem tracking_status)."""
        return journey_vc_groups(obj)


class WarrantyJourneyWriteSerializer(serializers.ModelSerializer):
    """Ghi 1 hành trình bảo hành — mọi field optional để hỗ trợ tạo/PATCH từng phần.

    KHÔNG nhận `status`/`return_status`: trạng thái VC hiển thị suy tự động từ mã
    Best, trạng thái nội bộ do quét kho/auto đặt.
    """

    class Meta:
        model = WarrantyJourney
        fields = (
            "name",
            "receiver_name", "receiver_phone", "receiver_raw_address",
            "product", "send_reason", "classification", "error_category",
            "shipping_form", "declared_value", "cod_amount", "carrier", "shipping_fee",
            "order_date", "new_tracking_code", "recall_code", "note",
            "warehouse_received_at", "inspection_result", "warehouse_check",
            "resolution", "resolution_declared_value", "resolution_cod", "resolution_note",
            "return_carrier", "return_shipping_fee", "return_order_date", "return_tracking_code",
            "feedback_agree_review", "feedback_rating", "feedback_note",
            "claim_date", "claim_note", "carrier_response",
            "compensation_status", "compensation_amount",
        )
        extra_kwargs = {f: {"required": False} for f in fields}


class WarrantyClaimSerializer(serializers.ModelSerializer):
    order = WarrantyClaimOrderSerializer(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    created_by_name = serializers.SerializerMethodField()
    journeys = WarrantyJourneySerializer(many=True, read_only=True)
    # Dẫn xuất từ activation_date + warranty_months (property model) — khai báo tường
    # minh để OpenAPI sinh đúng kiểu date/int nullable thay vì string.
    expiry_date = serializers.DateField(read_only=True, allow_null=True)
    warranty_days_left = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = WarrantyClaim
        fields = (
            "id",
            "order",
            "customer_report_date",
            "activation_date",
            "warranty_months",
            "expiry_date",
            "warranty_days_left",
            "status",
            "status_label",
            "completed_at",
            "journeys",
            "created_by_name",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_created_by_name(self, obj) -> str:
        user = obj.created_by
        if not user:
            return ""
        return user.display_name or user.username


class WarrantyClaimWriteSerializer(serializers.ModelSerializer):
    order_id = serializers.IntegerField(write_only=True, required=False)

    class Meta:
        model = WarrantyClaim
        fields = (
            "order_id",
            "customer_report_date",
            "activation_date",
            "warranty_months",
            "status",
        )
        extra_kwargs = {
            "customer_report_date": {"required": False},
            "activation_date": {"required": False, "allow_null": True},
            "warranty_months": {"required": False},
            "status": {"required": False},
        }

    def validate_warranty_months(self, value):
        if value < 1 or value > 120:
            raise serializers.ValidationError("Số tháng bảo hành phải từ 1 đến 120.")
        return value

    def validate_status(self, value):
        if value not in WarrantyClaimStatus.values:
            raise serializers.ValidationError("Trạng thái không hợp lệ.")
        return value


class WarrantyClaimIngestSerializer(serializers.ModelSerializer):
    """Payload ingest công khai: `shipping_code` để tìm đơn + các field journey seq=1.

    Model nguồn là `WarrantyJourney` (không phải `WarrantyClaim`) vì mọi field
    nghiệp vụ ingest (send_reason, recall_code, receiver_*, ...) đã chuyển sang
    journey từ Sprint 1 (Task 4 migration). Serializer này chỉ dùng để validate
    + coerce dữ liệu (`.validated_data`) — view không gọi `.save()`, `claim_service`
    map dict kết quả vào journey qua `WarrantyJourneyService.update_journey`.
    """

    shipping_code = serializers.CharField(write_only=True)

    class Meta:
        model = WarrantyJourney
        fields = ("shipping_code", *JOURNEY_EDITABLE_FIELDS)
        extra_kwargs = {f: {"required": False} for f in JOURNEY_EDITABLE_FIELDS}

    def validate_shipping_code(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Thiếu mã vận đơn.")
        return value
