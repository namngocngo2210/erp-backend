from rest_framework import serializers

from apps.warranty_codes.models import PortalChannel


def _normalized_phone_or_raise(value: str) -> str:
    from apps.common.phone_utils import normalize_phone

    normalized = normalize_phone(value)
    if not normalized:
        raise serializers.ValidationError("Số điện thoại không hợp lệ.")
    return normalized


class ActivateSerializer(serializers.Serializer):
    keyword = serializers.CharField(max_length=64)
    # Từ bản sửa đổi 08/08: SĐT quay lại làm ô BẮT BUỘC ở tab Bảo hành, nhưng với
    # mục đích khác bản trước — đăng ký bảo hành điện tử theo SĐT cho CSKH, KHÔNG
    # phải xác thực quyền sở hữu (mã dưới lớp cào đã đủ xác thực điều đó). Xem
    # docs/warranty-portal-design.md §4.1.
    phone = serializers.CharField(max_length=20)
    channel = serializers.ChoiceField(choices=PortalChannel.values, default=PortalChannel.WEB)

    def validate_phone(self, value: str) -> str:
        return _normalized_phone_or_raise(value)


class TrackSerializer(serializers.Serializer):
    """Tra cứu vận đơn — bản sửa đổi 24/08: MỘT trong hai ô là đủ.

    Trước đây cả `shipping_code` lẫn `phone` đều bắt buộc, và cặp đôi đó đóng vai
    "chìa khoá" của tab Vận đơn. Chủ dự án chốt bỏ ràng buộc đó (khách thường chỉ
    nhớ một trong hai), nên cả hai ô thành tuỳ chọn và `validate()` chỉ đòi có ít
    nhất một. Xem docs/warranty-portal-design.md §4.2.

    `order_code` KHÔNG phải một lối vào mới: nó chỉ là bộ lọc phụ để frontend
    chọn đúng đơn khi tra bằng SĐT ra nhiều đơn, nên một mình nó không thoả điều
    kiện của `validate()`.
    """

    shipping_code = serializers.CharField(
        max_length=100, required=False, allow_blank=True, default=""
    )
    order_code = serializers.CharField(
        max_length=100, required=False, allow_blank=True, default=""
    )
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")

    def validate_shipping_code(self, value: str) -> str:
        return (value or "").strip().upper()

    def validate_order_code(self, value: str) -> str:
        # KHÔNG upper: mã đơn do frontend gửi lại nguyên văn từ danh sách vừa trả
        # về, đổi hoa/thường chỉ tạo cơ hội lệch với dữ liệu trong DB.
        return (value or "").strip()

    def validate_phone(self, value: str) -> str:
        # Ô trống là hợp lệ (khách chỉ nhập mã vận đơn); chỉ số nhập sai định dạng
        # mới báo lỗi.
        if not (value or "").strip():
            return ""
        return _normalized_phone_or_raise(value)

    def validate(self, attrs: dict) -> dict:
        if not attrs.get("shipping_code") and not attrs.get("phone"):
            raise serializers.ValidationError(
                {"detail": "Vui lòng nhập mã vận đơn hoặc số điện thoại."}
            )
        return attrs
