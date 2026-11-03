from __future__ import annotations

from rest_framework import serializers

from apps.inventory.models import (
    StockDocument,
    StockDocumentAttachment,
    StockDocumentLine,
)


class StockDocumentLineSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = StockDocumentLine
        fields = ["id", "product", "product_sku", "product_name", "quantity"]


class StockDocumentAttachmentSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.CharField(
        source="uploaded_by.username", read_only=True, default=None
    )
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = StockDocumentAttachment
        fields = [
            "id",
            "content_type",
            "original_name",
            "size_bytes",
            "uploaded_by_name",
            "uploaded_at",
            "file_url",
        ]

    def get_file_url(self, obj) -> str:
        return f"/api/inventory/documents/attachments/{obj.id}/file"


class StockDocumentSerializer(serializers.ModelSerializer):
    lines = StockDocumentLineSerializer(many=True, read_only=True)
    attachments = StockDocumentAttachmentSerializer(many=True, read_only=True)
    source_warehouse_name = serializers.CharField(
        source="source_warehouse.name", read_only=True, default=None
    )
    dest_warehouse_name = serializers.CharField(
        source="dest_warehouse.name", read_only=True, default=None
    )
    submitted_by_name = serializers.CharField(
        source="submitted_by.username", read_only=True, default=None
    )
    approved_by_name = serializers.CharField(
        source="approved_by.username", read_only=True, default=None
    )

    class Meta:
        model = StockDocument
        fields = [
            "id",
            "code",
            "doc_type",
            "status",
            "source_warehouse",
            "source_warehouse_name",
            "dest_warehouse",
            "dest_warehouse_name",
            "note",
            "created_at",
            "posted_at",
            "submitted_by",
            "submitted_by_name",
            "submitted_at",
            "approved_by",
            "approved_by_name",
            "approved_at",
            "rejected_reason",
            "lines",
            "attachments",
        ]


class _LineInput(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)


class CreateStockDocumentSerializer(serializers.Serializer):
    doc_type = serializers.ChoiceField(
        choices=["receipt", "issue", "adjustment", "transfer"]
    )
    source_warehouse_id = serializers.IntegerField(required=False, allow_null=True)
    dest_warehouse_id = serializers.IntegerField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, default="")
    created_at = serializers.DateTimeField(required=False, allow_null=True)
    lines = _LineInput(many=True)

    def validate(self, attrs):
        dt = attrs["doc_type"]
        if dt == "issue" and not attrs.get("source_warehouse_id"):
            raise serializers.ValidationError("issue cần source_warehouse_id.")
        if dt in ("receipt", "adjustment") and not attrs.get("dest_warehouse_id"):
            raise serializers.ValidationError(f"{dt} cần dest_warehouse_id.")
        if dt == "transfer":
            if not attrs.get("source_warehouse_id") or not attrs.get(
                "dest_warehouse_id"
            ):
                raise serializers.ValidationError(
                    "transfer cần cả source và dest warehouse."
                )
            if attrs["source_warehouse_id"] == attrs["dest_warehouse_id"]:
                raise serializers.ValidationError(
                    "Kho nguồn và kho đích phải khác nhau."
                )
        if not attrs.get("lines"):
            raise serializers.ValidationError("Phiếu phải có ít nhất 1 dòng.")
        product_ids = [line["product_id"] for line in attrs["lines"]]
        if len(product_ids) != len(set(product_ids)):
            raise serializers.ValidationError(
                "Mỗi sản phẩm chỉ được xuất hiện 1 dòng trên phiếu."
            )
        return attrs
