"""Serializer (chỉ schema/đầu ra) cho API trạng thái vận chuyển Pancake (MR D)."""

from __future__ import annotations

from rest_framework import serializers


class PancakePosDeliveryPartSerializer(serializers.Serializer):
    pancake_order_id = serializers.CharField()
    pancake_status = serializers.IntegerField(allow_null=True)
    status_name = serializers.CharField()
    dvvc_status = serializers.CharField()
    tracking_code = serializers.CharField()
    partner_name = serializers.CharField()
    warehouse_name = serializers.CharField()
    match_method = serializers.CharField()
    inserted_at = serializers.DateTimeField(allow_null=True)
    shipped_at = serializers.DateTimeField(allow_null=True)
    delivered_at = serializers.DateTimeField(allow_null=True)
    returned_at = serializers.DateTimeField(allow_null=True)
    last_partner_status_at = serializers.DateTimeField(allow_null=True)
    status_changed_at = serializers.DateTimeField(allow_null=True)


class PancakePosOrderDeliverySerializer(serializers.Serializer):
    order_id = serializers.IntegerField()
    partner_delivery_status = serializers.CharField()
    partner_dvvc_delivery_status = serializers.CharField()
    parts = PancakePosDeliveryPartSerializer(many=True)


class PancakePosStuckWarehouseSerializer(serializers.Serializer):
    warehouse_name = serializers.CharField()
    count = serializers.IntegerField()


class PancakePosStuckItemSerializer(serializers.Serializer):
    pancake_order_id = serializers.CharField()
    order_id = serializers.IntegerField(allow_null=True)
    order_code = serializers.CharField()
    status_name = serializers.CharField()
    dvvc_status = serializers.CharField()
    warehouse_name = serializers.CharField()
    tracking_code = serializers.CharField()
    partner_name = serializers.CharField()
    inserted_at = serializers.DateTimeField(allow_null=True)
    shipped_at = serializers.DateTimeField(allow_null=True)
    age_hours = serializers.FloatField()


class PancakePosStuckBucketSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    by_warehouse = PancakePosStuckWarehouseSerializer(many=True)
    items = PancakePosStuckItemSerializer(many=True)


class PancakePosStuckReportSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    not_sent_hours = serializers.IntegerField()
    not_delivered_days = serializers.IntegerField()
    not_sent = PancakePosStuckBucketSerializer()
    not_delivered = PancakePosStuckBucketSerializer()


class PancakePosDeliveryMismatchSerializer(serializers.Serializer):
    order_id = serializers.IntegerField()
    order_code = serializers.CharField()
    crm_status = serializers.IntegerField()
    crm_status_label = serializers.CharField()
    delivery_business_status_code = serializers.CharField(allow_blank=True)
    partner_delivery_status = serializers.CharField()
    partner_dvvc_delivery_status = serializers.CharField(allow_blank=True)
    rule = serializers.CharField()
    rule_label = serializers.CharField()
    pancake_order_ids = serializers.ListField(child=serializers.CharField())
    updated_at = serializers.DateTimeField(allow_null=True)


class PancakePosDeliveryMismatchReportSerializer(serializers.Serializer):
    window_days = serializers.IntegerField()
    count = serializers.IntegerField()
    items = PancakePosDeliveryMismatchSerializer(many=True)
