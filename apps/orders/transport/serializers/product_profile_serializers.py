"""Serializer cho API quản trị nội dung trang sản phẩm (CRM).

Toàn bộ trường text lưu PLAIN TEXT — không có xử lý HTML nào ở đây, giữ đúng bất
biến của ProductProfile (xem docstring model). Không có trường giá nào.
"""
from __future__ import annotations

from rest_framework import serializers

from apps.catalog.models import PROFILE_SECTIONS, ProductProfile, ProductProfileImage


class ProductProfileImageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = ProductProfileImage
        fields = ("id", "url", "alt_text", "sort_order")

    def get_url(self, obj) -> str:
        return f"/api/warranty-portal/product-images/{obj.id}"


class ProductProfileSerializer(serializers.ModelSerializer):
    # SerializerMethodField thay vì nested many=True trực tiếp: GET trả về một
    # ProductProfile() CHƯA lưu khi sản phẩm chưa có profile (xem view), và
    # `.images.all()` trên instance chưa có PK ném ValueError — phải tự chặn.
    images = serializers.SerializerMethodField()

    # Tên sản phẩm: trang public in nó thành `<h1>`, nhưng resource profile trước
    # đây không trả về — màn "Xem trước" trong CRM vì thế không dựng lại được
    # đúng tiêu đề thật. Đọc qua `source="product.name"` (read-only) rẻ hơn là
    # bắt frontend gọi thêm một request danh sách sản phẩm chỉ để lấy một chuỗi.
    product_name = serializers.CharField(source="product.name", read_only=True)

    def get_images(self, obj) -> list[dict]:
        if obj.pk is None:
            return []
        return ProductProfileImageSerializer(obj.images.all(), many=True).data

    class Meta:
        model = ProductProfile
        fields = (
            "display_name", "short_description", "highlights", "specs", "audience",
            "usage_steps", "troubleshooting", "faqs", "videos", "visible_sections",
            "is_published", "images", "updated_at", "product_name",
        )
        read_only_fields = ("images", "updated_at", "product_name")

    def validate_visible_sections(self, value):
        unknown = [s for s in value if s not in PROFILE_SECTIONS]
        if unknown:
            raise serializers.ValidationError(f"Khối không hợp lệ: {', '.join(unknown)}")
        return value

    def validate_videos(self, value):
        for item in value:
            # Mỗi phần tử phải là object {"url": ..., ...} — item.get() trên một
            # chuỗi (vd. "javascript:alert(1)" gửi thẳng, không bọc object) ném
            # AttributeError và biến thành 500 thay vì lỗi validate 400 bình thường.
            if not isinstance(item, dict):
                raise serializers.ValidationError("Mỗi video phải là object có trường url.")
            url = str(item.get("url", ""))
            if not url.startswith(("http://", "https://")):
                # Chặn javascript:/data: ngay tại đây — trang public render URL này
                # thành link, nên URL bẩn lọt vào DB là XSS lưu trữ.
                raise serializers.ValidationError(
                    "URL video phải bắt đầu bằng http:// hoặc https://"
                )
        return value
