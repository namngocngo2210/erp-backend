"""Serializer API webhook Pancake → CRM (two-way-sync §5.1, §7 B1) — khai cho schema FE.

Trạng thái/loại là CharField (không ChoiceField) để drf-spectacular không sinh enum mới
trùng tên với enum FE đang dùng (cùng lý do như serializers đẩy đơn A2)."""

from __future__ import annotations

from rest_framework import serializers

MAX_TYPES = 4


class PancakePosWebhookCountSerializer(serializers.Serializer):
    event_type = serializers.CharField(help_text="orders | variations_warehouses | products | customers | unknown")
    status = serializers.CharField(help_text="pending | processed | ignored | error")
    count = serializers.IntegerField()


class PancakePosWebhookErrorSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    event_type = serializers.CharField()
    record_id = serializers.CharField(allow_blank=True)
    error = serializers.CharField(allow_blank=True)
    attempts = serializers.IntegerField()
    received_at = serializers.DateTimeField()


class PancakePosWebhookConfigSerializer(serializers.Serializer):
    url = serializers.CharField(allow_blank=True)
    secret_set = serializers.BooleanField()
    header = serializers.CharField()
    types = serializers.ListField(child=serializers.CharField())
    create_orders = serializers.BooleanField()
    dispatch_enabled = serializers.BooleanField()
    unmatched_hours = serializers.IntegerField()


class PancakePosWebhookStatusSerializer(serializers.Serializer):
    config = PancakePosWebhookConfigSerializer()
    counts_24h = PancakePosWebhookCountSerializer(many=True)
    last_received_at = serializers.DateTimeField(allow_null=True)
    error_count = serializers.IntegerField()
    last_error = PancakePosWebhookErrorSerializer(allow_null=True)
    inbound_counts = serializers.DictField(child=serializers.IntegerField(), help_text="match_status → số đơn")


class PancakePosWebhookConfigureRequestSerializer(serializers.Serializer):
    dry_run = serializers.BooleanField(default=True)
    url = serializers.CharField(required=False, allow_blank=True, max_length=500)
    types = serializers.ListField(
        child=serializers.CharField(max_length=40), required=False, allow_empty=False, max_length=MAX_TYPES
    )


class PancakePosWebhookConfigureResultSerializer(serializers.Serializer):
    dry_run = serializers.BooleanField()
    body = serializers.DictField(help_text="Body PUT /shops/{id} (secret đã che)")
    response = serializers.DictField(allow_null=True, help_text="Phần webhook_* Pancake trả về (secret đã che)")


class PancakePosWebhookRetryResultSerializer(serializers.Serializer):
    count = serializers.IntegerField()


class PancakePosInboundOrderSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    pancake_order_id = serializers.CharField()
    order_id = serializers.IntegerField(allow_null=True)
    match_status = serializers.CharField(help_text="matched | waiting | unmatched | ignored")
    match_method = serializers.CharField(
        allow_blank=True, help_text="order_link | marketplace_code | tracking_code | created | manual"
    )
    candidates = serializers.ListField(child=serializers.IntegerField())
    reason = serializers.CharField(allow_blank=True)
    pancake_status = serializers.IntegerField(allow_null=True)
    source_name = serializers.CharField(allow_blank=True)
    account_name = serializers.CharField(allow_blank=True)
    tracking_code = serializers.CharField(allow_blank=True)
    partner_name = serializers.CharField(allow_blank=True)
    total_price = serializers.IntegerField(allow_null=True)
    inserted_at = serializers.DateTimeField(allow_null=True)
    shipped_at = serializers.DateTimeField(allow_null=True)
    delivered_at = serializers.DateTimeField(allow_null=True)
    returned_at = serializers.DateTimeField(allow_null=True)
    first_seen_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class PancakePosInboundOrderPageSerializer(serializers.Serializer):
    items = PancakePosInboundOrderSerializer(many=True)
    total = serializers.IntegerField()
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()


class PancakePosInboundResolveRequestSerializer(serializers.Serializer):
    action = serializers.CharField(help_text="match | ignore")
    order_id = serializers.IntegerField(required=False, min_value=1)

    def validate(self, attrs):
        if attrs["action"] not in ("match", "ignore"):
            raise serializers.ValidationError({"action": "Chỉ nhận match | ignore."})
        if attrs["action"] == "match" and not attrs.get("order_id"):
            raise serializers.ValidationError({"order_id": "Cần mã đơn CRM để khớp."})
        return attrs
