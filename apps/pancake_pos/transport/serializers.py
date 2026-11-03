"""Serializer vào/ra của API Pancake POS (khai để drf-spectacular sinh schema cho FE)."""

from __future__ import annotations

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.pancake_pos.models import PancakePosSyncRun, PancakePosVariationLink, SyncRunMode

MAX_SKUS = 200


class PancakePosSyncRequestSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=[SyncRunMode.DRY_RUN, SyncRunMode.FULL])
    limit = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=100_000)
    skus = serializers.ListField(
        child=serializers.CharField(max_length=100, trim_whitespace=True),
        required=False,
        allow_empty=True,
        max_length=MAX_SKUS,
    )


class PancakePosSyncAcceptedSerializer(serializers.Serializer):
    run_id = serializers.IntegerField()


class PancakePosUserRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


def _user_ref(user) -> dict | None:
    if user is None:
        return None
    name = (getattr(user, "display_name", "") or user.get_full_name() or user.get_username()).strip()
    return {"id": user.id, "name": name}


class PancakePosSyncRunSerializer(serializers.ModelSerializer):
    # Chuỗi thường (không enum) — tránh va tên enum `mode` với app khác trong schema.
    mode = serializers.CharField(help_text="full | incremental | dry_run")
    triggered_by = serializers.SerializerMethodField()
    fatal = serializers.SerializerMethodField()

    class Meta:
        model = PancakePosSyncRun
        fields = (
            "id", "mode", "started_at", "finished_at",
            "created", "updated", "linked", "skipped", "conflicts", "errors", "hidden",
            "triggered_by", "fatal",
        )

    @extend_schema_field(PancakePosUserRefSerializer(allow_null=True))
    def get_triggered_by(self, obj) -> dict | None:
        return _user_ref(obj.triggered_by)

    def get_fatal(self, obj) -> str:
        report = obj.report if isinstance(obj.report, dict) else {}
        return str(report.get("fatal") or "")


class PancakePosSyncRunDetailSerializer(PancakePosSyncRunSerializer):
    report = serializers.JSONField()

    class Meta(PancakePosSyncRunSerializer.Meta):
        fields = (*PancakePosSyncRunSerializer.Meta.fields, "report")


class PancakePosSyncRunPageSerializer(serializers.Serializer):
    items = PancakePosSyncRunSerializer(many=True)
    total = serializers.IntegerField()
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()


class PancakePosLinkSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField()
    sku = serializers.CharField(source="product.sku")
    name = serializers.CharField(source="product.name")
    group_key = serializers.CharField(source="product_link.group_key")
    pancake_product_id = serializers.CharField(source="product_link.pancake_product_id", allow_null=True)

    class Meta:
        model = PancakePosVariationLink
        fields = (
            "id", "product_id", "sku", "name", "group_key", "pancake_product_id", "pancake_variation_id",
            "status", "last_error", "attempts", "synced_at", "updated_at",
        )


class PancakePosLinkPageSerializer(serializers.Serializer):
    items = PancakePosLinkSerializer(many=True)
    total = serializers.IntegerField()
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()


class PancakePosShopSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class PancakePosHealthSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    error = serializers.CharField(allow_blank=True)
    shops = PancakePosShopSerializer(many=True)
    shop_id = serializers.IntegerField()
    shop_found = serializers.BooleanField()


class PancakePosStatusSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    api_key_set = serializers.BooleanField()
    shop_id = serializers.IntegerField()
    dirty_count = serializers.IntegerField()
    link_counts = serializers.DictField(child=serializers.IntegerField())
    active_run_id = serializers.IntegerField(allow_null=True)
    push_orders_enabled = serializers.BooleanField(help_text="PANCAKE_POS_PUSH_ORDERS_ENABLED (cờ riêng)")
    order_link_counts = serializers.DictField(child=serializers.IntegerField())



# ── ghép kho (two-way-sync §3) ──────────────────────────────────────────────
class PancakePosWarehouseLinkInSerializer(serializers.Serializer):
    warehouse_id = serializers.IntegerField(min_value=1)
    pancake_warehouse_id = serializers.CharField(max_length=64, trim_whitespace=True)
    pancake_warehouse_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    is_default_for_push = serializers.BooleanField(required=False, default=False)
    priority = serializers.IntegerField(required=False, min_value=0, max_value=32767, default=0)


class PancakePosWarehouseLinksUpdateSerializer(serializers.Serializer):
    links = PancakePosWarehouseLinkInSerializer(many=True, max_length=50)


class PancakePosWarehouseRowSerializer(serializers.Serializer):
    """Một kho CRM kèm ghép (nếu có) — UI hiện đủ kho CRM để chọn."""

    warehouse_id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    is_active = serializers.BooleanField()
    pancake_warehouse_id = serializers.CharField(allow_null=True)
    pancake_warehouse_name = serializers.CharField(allow_blank=True)
    is_default_for_push = serializers.BooleanField()
    priority = serializers.IntegerField(allow_null=True)


class PancakePosWarehouseLinksSerializer(serializers.Serializer):
    items = PancakePosWarehouseRowSerializer(many=True)


class PancakePosRemoteWarehouseSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField(allow_blank=True)


class PancakePosRemoteWarehousesSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    error = serializers.CharField(allow_blank=True)
    warehouses = PancakePosRemoteWarehouseSerializer(many=True)


# ── tồn đầu kỳ (two-way-sync §7 A1) ─────────────────────────────────────────
INITIAL_STOCK_MODES = ("dry_run", "apply")


class PancakePosInitialStockRequestSerializer(serializers.Serializer):
    # CharField (không ChoiceField): enum `mode` thứ hai sẽ làm drf-spectacular đổi tên
    # `ModeEnum` của API đồng bộ sản phẩm → vỡ kiểu FE đang dùng.
    mode = serializers.CharField(help_text="dry_run | apply")
    warehouse_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, allow_empty=True, max_length=50
    )
    skus = serializers.ListField(
        child=serializers.CharField(max_length=100, trim_whitespace=True),
        required=False,
        allow_empty=True,
        max_length=MAX_SKUS,
    )
    # Ghi thật: id lượt chạy thử đã xem — phạm vi lấy từ lượt đó (bỏ qua warehouse_ids/skus).
    dry_run_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def validate_mode(self, value: str) -> str:
        if value not in INITIAL_STOCK_MODES:
            raise serializers.ValidationError("mode phải là dry_run hoặc apply.")
        return value

    def validate(self, attrs):
        if attrs["mode"] == "apply" and not attrs.get("dry_run_id"):
            raise serializers.ValidationError({"dry_run_id": "Ghi thật cần id lượt chạy thử."})
        return attrs


class PancakePosInitialStockStatusSerializer(serializers.Serializer):
    latest_dry_run = PancakePosSyncRunSerializer(allow_null=True)
    apply_allowed = serializers.BooleanField()
    apply_deadline = serializers.DateTimeField(allow_null=True)
    active_run_id = serializers.IntegerField(allow_null=True)
    warehouse_links = serializers.IntegerField(help_text="Số kho đã ghép")


# ── đẩy đơn (two-way-sync §4.1, A2) ─────────────────────────────────────────
# `status`/`origin` là CharField (không ChoiceField): thêm enum `status` thứ hai làm
# drf-spectacular đổi tên `PancakePosLinkStatusEnum` FE đang dùng.
ORDER_LINK_STATUS_HELP = (
    "pending | pushing | pushed | updated | cancelled | error | needs_routing | skipped_duplicate | linked"
)


class PancakePosOrderLinkSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    order_id = serializers.IntegerField()
    part_no = serializers.IntegerField()
    pancake_order_id = serializers.CharField(allow_null=True)
    origin = serializers.CharField(help_text="crm_push | pancake_native")
    status = serializers.CharField(help_text=ORDER_LINK_STATUS_HELP)
    pancake_status = serializers.IntegerField(allow_null=True)
    warehouse_id = serializers.IntegerField(allow_null=True)
    warehouse_name = serializers.CharField(allow_blank=True)
    last_error = serializers.CharField(allow_blank=True)
    attempts = serializers.IntegerField()
    needs_lookup = serializers.BooleanField()
    pushed_at = serializers.DateTimeField(allow_null=True)
    updated_at = serializers.DateTimeField()
    order_name = serializers.CharField(allow_blank=True)
    order_source = serializers.CharField(allow_blank=True)


class PancakePosOrderLinkPageSerializer(serializers.Serializer):
    items = PancakePosOrderLinkSerializer(many=True)
    total = serializers.IntegerField()
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()


class PancakePosOrderRefSerializer(serializers.Serializer):
    """Mã đơn Pancake hiện ở trang chi tiết đơn CRM."""

    part_no = serializers.IntegerField()
    pancake_order_id = serializers.CharField(allow_null=True)
    status = serializers.CharField(help_text=ORDER_LINK_STATUS_HELP)


class PancakePosOrderRefsSerializer(serializers.Serializer):
    items = PancakePosOrderRefSerializer(many=True)


class PancakePosOrderRetryRequestSerializer(serializers.Serializer):
    force = serializers.BooleanField(
        required=False, default=False,
        help_text="Vẫn đẩy đơn bị luật chống trùng landing ↔ Foxia chặn (chỉ skipped_duplicate)",
    )


class PancakePosOrderRetryResultSerializer(serializers.Serializer):
    order_id = serializers.IntegerField()


class PancakePosRoutingLineSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    sku = serializers.CharField(allow_blank=True)
    name = serializers.CharField(allow_blank=True)
    quantity = serializers.IntegerField()


class PancakePosRoutingWarehouseSerializer(serializers.Serializer):
    warehouse_id = serializers.IntegerField()
    name = serializers.CharField(allow_blank=True)
    pancake_warehouse_id = serializers.CharField()
    enough = serializers.BooleanField()
    stock = serializers.DictField(child=serializers.IntegerField(), help_text="product_id → tồn Hàng hoá CRM")


class PancakePosRoutingLinkSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    part_no = serializers.IntegerField()
    status = serializers.CharField()


class PancakePosRoutingContextSerializer(serializers.Serializer):
    order_id = serializers.IntegerField()
    name = serializers.CharField(allow_blank=True)
    order_source = serializers.CharField(allow_blank=True)
    lines = PancakePosRoutingLineSerializer(many=True)
    warehouses = PancakePosRoutingWarehouseSerializer(many=True)
    links = PancakePosRoutingLinkSerializer(many=True)


class PancakePosRouteItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1, max_value=100_000)


class PancakePosRoutePartSerializer(serializers.Serializer):
    warehouse_id = serializers.IntegerField(min_value=1)
    items = PancakePosRouteItemSerializer(many=True, required=False, max_length=200)


class PancakePosRouteRequestSerializer(serializers.Serializer):
    """Một kho: `[{warehouse_id}]` (không items). Tách kiện: mỗi part {warehouse_id, items}."""

    parts = PancakePosRoutePartSerializer(many=True, min_length=1, max_length=10)


class PancakePosRouteResultSerializer(serializers.Serializer):
    link_ids = serializers.ListField(child=serializers.IntegerField())
