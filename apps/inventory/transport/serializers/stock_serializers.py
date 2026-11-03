from __future__ import annotations

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.inventory.models import StockItem, StockMovement


class StockItemSerializer(serializers.ModelSerializer):
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    # Run-rate bán (chiếc/ngày) — view tính sẵn theo (kho, mã) và truyền qua
    # context["run_rates"]; mọi dòng state của cùng cặp mang cùng giá trị.
    rr3 = serializers.SerializerMethodField()
    rr7 = serializers.SerializerMethodField()

    class Meta:
        model = StockItem
        fields = [
            "id", "warehouse", "warehouse_code", "warehouse_name",
            "product", "product_sku", "product_name", "quantity", "state", "updated_at",
            "rr3", "rr7",
        ]

    def _rate(self, obj, key: str) -> float:
        rates = self.context.get("run_rates") or {}
        return (rates.get((obj.warehouse_id, obj.product_id)) or {}).get(key, 0.0)

    @extend_schema_field(serializers.FloatField())
    def get_rr3(self, obj):
        return self._rate(obj, "rr3")

    @extend_schema_field(serializers.FloatField())
    def get_rr7(self, obj):
        return self._rate(obj, "rr7")


class StockMovementSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    reference_label = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = [
            "id", "warehouse", "warehouse_name", "product", "product_sku",
            "qty_delta", "movement_type", "state", "balance_after", "reference_type",
            "reference_id", "reference_label", "note", "created_at",
            # Ngày NGHIỆP VỤ. Thẻ sổ vẫn sắp theo `created_at` (thứ tự ghi sổ),
            # nên phải trả kèm cột này: không có nó, người dùng thấy thẻ ghi
            # 22/07 trong khi tab Xuất xếp chính dòng đó vào 20/07 mà không có
            # cách nào biết vì sao.
            "occurred_at",
        ]

    def get_reference_label(self, obj):
        labels = self.context.get("reference_labels") or {}
        return labels.get((obj.reference_type, obj.reference_id))


class ReclassInputSerializer(serializers.Serializer):
    warehouse_id = serializers.IntegerField()
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)
    from_state = serializers.ChoiceField(choices=["warranty", "defect"])
    action = serializers.ChoiceField(choices=["to_sellable", "send_back", "scrap"])
    note = serializers.CharField(required=False, allow_blank=True, default="")


class StockAvailabilityRowSerializer(serializers.Serializer):
    """Một dòng "tồn bán được" của form đơn hàng (xem `stock_availability`)."""

    sku = serializers.CharField()
    product_name = serializers.CharField()
    sellable = serializers.IntegerField()
    # SL đơn bán trực tiếp "xuất sau" đang chờ kho xác nhận — CẢNH BÁO cạnh
    # `sellable`, không trừ vào nó (xem `StockQueryService._pending_direct_by_product`).
    pending_direct = serializers.IntegerField()
