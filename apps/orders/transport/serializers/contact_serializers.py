"""Serializer response cho GET /api/sale/contacts — chỉ dùng khai OpenAPI (extend_schema).

View build data trực tiếp từ dict do ContactListService trả về (không instantiate
serializer để render) — theo pattern AdminUserListResponseSerializer
(apps/users/serializers.py).
"""
from rest_framework import serializers

from apps.contact_flow.transport.serializers.runtime_serializers import BUCKETS


class ContactOrderItemSerializer(serializers.Serializer):
    """1 dòng hàng của đơn. Đơn không có OrderItem (đơn import cũ / đơn tạo
    trước khi có OrderItem — đơn sàn TMĐT đã bị loại khỏi danh sách contact)
    được suy từ chuỗi gộp Order.sku nên product_name rỗng và quantity = 0."""

    sku = serializers.CharField(allow_blank=True)
    product_name = serializers.CharField(allow_blank=True)
    quantity = serializers.IntegerField()


class ContactLatestOrderSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    order_source = serializers.CharField()
    channel_id = serializers.IntegerField(allow_null=True)
    channel_name = serializers.CharField(allow_blank=True)
    address = serializers.CharField(allow_blank=True)
    status = serializers.IntegerField()
    items = ContactOrderItemSerializer(many=True)
    total_after_discount = serializers.DecimalField(max_digits=12, decimal_places=2)
    sales_status_code = serializers.CharField(allow_null=True)
    next_followup_at = serializers.DateTimeField(allow_null=True)
    staff_id = serializers.IntegerField(allow_null=True)
    staff_name = serializers.CharField(allow_blank=True)


class ContactOperationSerializer(serializers.Serializer):
    """Block state tác nghiệp CSKH của khách (module contact_flow) — khách chưa vào
    flow trả stage_name/stage_color rỗng, allow_edit_due=False, due_at=None,
    bucket="none" (KHÔNG dùng bucket_of(None)="ended", xem ContactListService)."""

    stage_name = serializers.CharField(allow_blank=True)
    stage_color = serializers.CharField(allow_blank=True)
    allow_edit_due = serializers.BooleanField()
    due_at = serializers.DateTimeField(allow_null=True)
    bucket = serializers.ChoiceField(choices=BUCKETS)


class ContactItemSerializer(serializers.Serializer):
    customer_id = serializers.IntegerField()
    customer_code = serializers.CharField()
    full_name = serializers.CharField()
    phone_raw = serializers.CharField()
    is_new_customer = serializers.BooleanField()
    latest_order = ContactLatestOrderSerializer()
    order_count = serializers.IntegerField()
    action_needed = serializers.CharField()
    waiting_since = serializers.DateTimeField(allow_null=True)
    # "gtc" = đếm từ mốc chuyển Giao thành công LẤY TỪ journey event (chính xác);
    # "gtc_estimated" = đơn GTC không có journey event, đếm từ Order.updated_at
    # (chỉ là ước lượng, có thể bị đẩy muộn bởi thao tác ghi khác lên đơn);
    # "activity" = đếm từ ngày data về / lần tương tác cuối. FE cần biết để
    # render tooltip đúng ngữ nghĩa — cùng một cột nhưng ba cách đọc.
    waiting_basis = serializers.ChoiceField(choices=["gtc", "gtc_estimated", "activity"])
    last_activity_at = serializers.DateTimeField(allow_null=True)
    operation = ContactOperationSerializer()


class ContactActionCountSerializer(serializers.Serializer):
    pending = serializers.IntegerField()
    total = serializers.IntegerField()


class ContactListResponseSerializer(serializers.Serializer):
    """Response GET /api/sale/contacts (phân trang) — chỉ dùng khai OpenAPI."""

    items = ContactItemSerializer(many=True)
    total = serializers.IntegerField()
    action_counts = serializers.DictField(child=ContactActionCountSerializer())
    operation_counts = serializers.DictField(child=ContactActionCountSerializer())


class ContactActivityItemSerializer(serializers.Serializer):
    """1 dòng timeline — status_change (từ OrderJourneyEvent) hoặc call/note (từ
    ContactActivity). Chỉ dùng khai OpenAPI, view build dict trực tiếp."""

    type = serializers.ChoiceField(choices=["status_change", "call", "note", "operation"])
    at = serializers.DateTimeField()
    actor_name = serializers.CharField(allow_blank=True)
    note = serializers.CharField(allow_blank=True)
    from_code = serializers.CharField(allow_null=True)
    to_code = serializers.CharField(allow_null=True)
    from_label = serializers.CharField(allow_null=True)
    to_label = serializers.CharField(allow_null=True)
    order_id = serializers.IntegerField(allow_null=True)
    call_meta = serializers.DictField(allow_null=True)


class ContactActivityListResponseSerializer(serializers.Serializer):
    """Response GET /api/sale/contacts/{customer_id}/activities."""

    items = ContactActivityItemSerializer(many=True)
    total = serializers.IntegerField()


class ContactActivityCreateRequestSerializer(serializers.Serializer):
    """Body POST /api/sale/contacts/{customer_id}/activities — chỉ dùng khai OpenAPI;
    validate thật nằm trong ContactActivityService.create_activity."""

    kind = serializers.ChoiceField(choices=["note", "call"])
    note = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    order_id = serializers.IntegerField(required=False, allow_null=True)
    call_meta = serializers.DictField(required=False, allow_null=True)
