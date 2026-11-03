"""Serializers cho REST API Best Express (transport layer).

Payload gửi Best (Order/Add, EstimateFee, UpdateAddress…) giữ dạng JSON
passthrough nguyên bản theo tài liệu V3 — nghiệp vụ do Best validate, mình
không re-implement (xem shipment_service).
"""

from rest_framework import serializers

from apps.best_express.models import BestExpressAccount, BestShipment


class BestAccountSerializer(serializers.ModelSerializer):
    """Thông tin account an toàn để hiển thị — KHÔNG bao giờ trả password/token."""

    class Meta:
        model = BestExpressAccount
        fields = ["id", "name", "environment", "username", "is_active", "created_at"]


class BestShipmentSerializer(serializers.ModelSerializer):
    account_name = serializers.CharField(source="account.name", read_only=True)

    class Meta:
        model = BestShipment
        fields = [
            "id",
            "account",
            "account_name",
            "order",
            "merchant_order_code",
            "code_best",
            "route_code",
            "status",
            "best_status_code",
            "status_content",
            "cod",
            "product_price",
            "freight_fee",
            "cod_fee",
            "insurance_fee",
            "vat_fee",
            "return_fee",
            "discount",
            "total_fee",
            "weight",
            "length",
            "width",
            "height",
            "journey_type",
            "service_id",
            "pickup_type",
            "last_event_at",
            "created_at",
            "updated_at",
        ]


class AccountScopedRequestSerializer(serializers.Serializer):
    """account_id bỏ trống được khi hệ thống chỉ có đúng 1 account active."""

    account_id = serializers.IntegerField(required=False, allow_null=True)


# DictField (không phải JSONField): JSONField nhận cả chuỗi/list → `{**value}` ném
# TypeError thành HTTP 500 thay vì 400. Payload gửi Best luôn là object.
class EstimateFeeRequestSerializer(AccountScopedRequestSerializer):
    params = serializers.DictField(
        help_text="Payload Service/EstimateFee nguyên bản theo tài liệu Best V3"
    )


class CreateShipmentRequestSerializer(AccountScopedRequestSerializer):
    order_id = serializers.IntegerField(required=False, allow_null=True)
    payload = serializers.DictField(
        help_text="Payload Order/Add nguyên bản theo tài liệu Best V3"
    )


class UpdateShipmentRequestSerializer(serializers.Serializer):
    changes = serializers.DictField(
        help_text=(
            "Các trường Order/Update cần đổi (CollectAmount, Weight…). Khoá `Code` bị bỏ "
            "qua: mã vận đơn luôn lấy từ shipment_id trên URL."
        )
    )


class UpdateAddressRequestSerializer(serializers.Serializer):
    address = serializers.DictField(
        help_text=(
            "Payload Order/UpdateAddress (địa chỉ nhận mới) theo tài liệu Best V3. Khoá "
            "`Code` bị bỏ qua: mã vận đơn luôn lấy từ shipment_id trên URL."
        )
    )


class CodesRequestSerializer(AccountScopedRequestSerializer):
    # max_length: 1 request vài chục nghìn mã sẽ ép BE gọi Best hàng trăm lần (chunk
    # theo TRACE/PRINT_MAX_CODES) — giữ worker + abuse upstream.
    codes = serializers.ListField(
        child=serializers.CharField(),
        allow_empty=False,
        max_length=500,
        help_text="Danh sách mã vận đơn Best (tối đa 500)",
    )
