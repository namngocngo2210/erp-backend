from __future__ import annotations

from rest_framework import serializers

from apps.inventory.models import (
    ReturnEntry,
    ReturnNote,
    ReturnNoteLine,
    ReturnType,
)


class ReturnNoteLineSerializer(serializers.ModelSerializer):
    """Chỉ dùng để ĐỌC (lồng trong entry/phiếu ngày).

    Loại hoàn bắt buộc ở dòng, nhưng ràng buộc nằm ở `_ReturnLineInput` (input)
    và `build_lines` (service) — serializer này không validate gì.
    """

    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = ReturnNoteLine
        fields = [
            "id", "product", "product_sku", "product_name",
            "quantity", "condition_note", "return_type",
        ]


class ReturnNoteBriefSerializer(serializers.ModelSerializer):
    """Phiếu ngày rút gọn — lồng trong mỗi nhóm đơn hoàn."""

    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)

    class Meta:
        model = ReturnNote
        fields = ["id", "code", "doc_date", "status", "warehouse", "warehouse_name"]


class ReturnEntrySerializer(serializers.ModelSerializer):
    """Nhóm đơn hoàn. `note` = ghi chú của nhóm đơn; phiếu ngày nằm ở `return_note`."""

    lines = ReturnNoteLineSerializer(many=True, read_only=True)
    return_note = ReturnNoteBriefSerializer(read_only=True)
    created_by_name = serializers.SerializerMethodField()
    source_order_invoice_code = serializers.SerializerMethodField()

    class Meta:
        model = ReturnEntry
        fields = [
            "id", "status",
            "source_order", "source_order_invoice_code",
            "shipping_code",
            "customer_name", "customer_phone", "source_note", "note",
            "created_by_name", "created_at", "posted_at", "scan_batch_id",
            "lines", "return_note",
        ]

    def get_created_by_name(self, obj) -> str:
        user = obj.created_by
        if user is None:
            return ""
        return user.display_name or user.username

    def get_source_order_invoice_code(self, obj) -> str:
        order = obj.source_order
        return order.invoice_code if order is not None else ""


class _AdjustLineInput(serializers.Serializer):
    product_id = serializers.IntegerField()
    # 0 = gỡ dòng khỏi phiếu (SP không về). Thêm SP mới không hỗ trợ — điều chỉnh
    # số thực nhận, không phải sửa nội dung phiếu.
    quantity = serializers.IntegerField(min_value=0)


class AdjustReturnEntrySerializer(serializers.Serializer):
    """Điều chỉnh số lượng dòng của nhóm đơn ĐÃ GHI SỔ (Lịch sử nhập xuất)."""

    lines = _AdjustLineInput(many=True)

    def validate(self, attrs):
        lines = attrs.get("lines") or []
        if not lines:
            raise serializers.ValidationError("Cần ít nhất 1 dòng điều chỉnh.")
        product_ids = [line["product_id"] for line in lines]
        if len(product_ids) != len(set(product_ids)):
            raise serializers.ValidationError(
                "Mỗi sản phẩm chỉ được xuất hiện 1 dòng."
            )
        return attrs


class _ReturnLineInput(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)
    condition_note = serializers.CharField(required=False, allow_blank=True, default="")
    return_type = serializers.ChoiceField(choices=ReturnType.values, required=True)


class CreateReturnNoteSerializer(serializers.Serializer):
    warehouse_id = serializers.IntegerField()
    source_order_id = serializers.IntegerField(required=False, allow_null=True)
    shipping_code = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=64
    )
    customer_name = serializers.CharField(required=False, allow_blank=True, default="")
    customer_phone = serializers.CharField(required=False, allow_blank=True, default="")
    source_note = serializers.CharField(required=False, allow_blank=True, default="")
    note = serializers.CharField(required=False, allow_blank=True, default="")
    # Đợt quét hàng loạt sinh ra nhóm đơn; None = tạo lẻ.
    scan_batch_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    lines = _ReturnLineInput(many=True, required=False, default=list)
    # Luồng quét khớp mã: ghi sổ ngay khi tạo (create_and_post_entry).
    auto_post = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        lines = attrs.get("lines") or []
        # BẮT BUỘC có dòng. Nhánh "nhóm đơn chờ khớp 0 dòng" đã bị gỡ: sau khi MR
        # remove-return-note-ui xoá list/PATCH/post, nhóm nháp 0 dòng không còn cửa nào
        # khớp hay ghi sổ, trong khi guard chống-trùng vẫn giữ mã vận đơn đó → quét lại
        # bị chặn, hàng hoàn không bao giờ được cộng tồn. Mã chưa khớp giờ đi đường
        # PendingShipmentScan (pending_scan_service) — audit 2026-07-14 vòng 3.
        if not lines:
            raise serializers.ValidationError("Phiếu trả hàng phải có ít nhất 1 dòng.")
        product_ids = [line["product_id"] for line in lines]
        if len(product_ids) != len(set(product_ids)):
            raise serializers.ValidationError(
                "Mỗi sản phẩm chỉ được xuất hiện 1 dòng trên phiếu."
            )
        return attrs
