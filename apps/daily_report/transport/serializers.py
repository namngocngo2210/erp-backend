"""Serializer CHỈ để drf-spectacular vẽ schema. Không dùng để validate/serialize ở runtime."""

from rest_framework import serializers

from apps.daily_report.domain import definitions as d


class ErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()


class MetaSerializer(serializers.Serializer):
    timezone = serializers.CharField()
    range = serializers.DictField(child=serializers.DateField())
    data_as_of = serializers.DateTimeField(allow_null=True, help_text="Mốc dữ liệu mart, giờ VN")
    data_status = serializers.CharField()
    definitions_url = serializers.CharField()
    generated_at = serializers.DateTimeField()
    spend_as_of = serializers.DictField(
        child=serializers.DateTimeField(allow_null=True), required=False,
        help_text="Chỉ /ads: mốc sync chi tiêu mỗi nền tảng",
    )
    attribution = serializers.DictField(
        child=serializers.IntegerField(), required=False,
        help_text="Chỉ /ads: độ phủ quy đơn về chiến dịch trong kỳ — leads, leads_with_campaign, landing_leads, landing_leads_with_campaign",
    )


class StaffRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField(allow_null=True)


class CodeLabelSerializer(serializers.Serializer):
    code = serializers.CharField(allow_null=True)
    label = serializers.CharField(allow_null=True)


class ChannelSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField(allow_null=True)
    type = serializers.CharField(allow_null=True, help_text="Loại kênh (Facebook, Google…)")


class IntCodeLabelSerializer(serializers.Serializer):
    code = serializers.IntegerField(allow_null=True)
    label = serializers.CharField(allow_null=True)


class OrderStatusSerializer(IntCodeLabelSerializer):
    bucket = serializers.ChoiceField(
        choices=list(d.ORDER_BUCKET_LABELS), allow_null=True,
        help_text="Rổ Báo cáo tổng; cộng theo đây thay cho total/delivered/… cũ",
    )


class SalesStatusSerializer(CodeLabelSerializer):
    bucket = serializers.ChoiceField(
        choices=list(d.LEAD_BUCKETS) + ["other"], allow_null=True,
        help_text="Rổ lead; null = chưa đánh giá (unassessed cũ)",
    )


class CustomerSerializer(serializers.Serializer):
    id = serializers.IntegerField(allow_null=True)
    code = serializers.CharField(allow_null=True)
    name = serializers.CharField(allow_null=True)
    phone_masked = serializers.CharField(allow_null=True, help_text="SĐT đã che, vd 0912***678")
    province = serializers.CharField(allow_null=True)
    district = serializers.CharField(allow_null=True)
    is_new = serializers.BooleanField(allow_null=True)


class AmountsSerializer(serializers.Serializer):
    goods = serializers.IntegerField(allow_null=True, help_text="VND, tiền hàng")
    discount = serializers.IntegerField(allow_null=True)
    shipping = serializers.IntegerField(allow_null=True)
    total = serializers.IntegerField(allow_null=True, help_text="VND, tổng sau giảm — cột doanh thu")
    quantity = serializers.IntegerField(allow_null=True)


class ItemsSerializer(serializers.Serializer):
    line_count = serializers.IntegerField(allow_null=True)
    skus = serializers.ListField(child=serializers.CharField())
    skus_label = serializers.CharField(allow_null=True)
    main_sku = serializers.CharField(allow_null=True)


class NeedsSerializer(serializers.Serializer):
    codes = serializers.ListField(child=serializers.CharField())
    label = serializers.CharField(allow_null=True)


class AgeSerializer(serializers.Serializer):
    label = serializers.CharField(allow_null=True)
    source = serializers.CharField(allow_null=True)


class CallsSerializer(serializers.Serializer):
    count = serializers.IntegerField(allow_null=True)
    seconds = serializers.IntegerField(allow_null=True)
    has_transcript = serializers.BooleanField(allow_null=True)
    ai_extracted = serializers.BooleanField(allow_null=True)
    quality_score = serializers.FloatField(allow_null=True)


class OpenLeadRowSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField(allow_null=True)
    created_date = serializers.DateField(allow_null=True)
    source = CodeLabelSerializer()
    channel = ChannelSerializer(allow_null=True)
    channel_group = serializers.CharField(allow_null=True)
    fb_campaign_id = serializers.CharField(allow_null=True, help_text="ID chiến dịch Facebook (utm_id trên URL landing) — nối `campaign_id` bên /ads; null = không nối được")
    fb_ad_id = serializers.CharField(allow_null=True, help_text="ID quảng cáo Facebook (utm_content); chỉ để tra cứu, /ads không có grain ad")
    staff = StaffRefSerializer(allow_null=True)
    customer = CustomerSerializer()
    amounts = AmountsSerializer()
    items = ItemsSerializer()
    sales_status = SalesStatusSerializer()
    calls = CallsSerializer()
    synced_at = serializers.DateTimeField(allow_null=True)
    age_days = serializers.IntegerField(help_text="as_of − created_date")
    cursor = serializers.IntegerField()


class OrderRowSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField(allow_null=True)
    created_date = serializers.DateField(allow_null=True)
    closed_at = serializers.DateTimeField(allow_null=True)
    closed_date = serializers.DateField(allow_null=True)
    order_status = OrderStatusSerializer()
    invoice_status = IntCodeLabelSerializer()
    is_valid_revenue = serializers.BooleanField(help_text="la_don_gtc — cờ duy nhất hợp lệ để tính doanh thu")
    order_type = CodeLabelSerializer()
    koc_type = CodeLabelSerializer()
    source = CodeLabelSerializer()
    delivery_method = CodeLabelSerializer()
    payment_method = serializers.CharField(allow_null=True)
    channel = ChannelSerializer(allow_null=True, help_text="Kênh = Digital, không phải fanpage")
    channel_group = serializers.CharField(allow_null=True)
    fb_campaign_id = serializers.CharField(allow_null=True, help_text="ID chiến dịch Facebook (utm_id trên URL landing) — nối `campaign_id` bên /ads; null = không nối được")
    fb_ad_id = serializers.CharField(allow_null=True, help_text="ID quảng cáo Facebook (utm_content); chỉ để tra cứu, /ads không có grain ad")
    branch_id = serializers.CharField(allow_null=True)
    staff = StaffRefSerializer(allow_null=True)
    customer = CustomerSerializer()
    amounts = AmountsSerializer()
    items = ItemsSerializer()
    sales_status = SalesStatusSerializer()
    reason_not_closed = CodeLabelSerializer()
    delivery_business_status = CodeLabelSerializer()
    bounce_reason_group = CodeLabelSerializer()
    customer_class = CodeLabelSerializer()
    needs = NeedsSerializer()
    age = AgeSerializer()
    occupation = CodeLabelSerializer()
    pain_level = CodeLabelSerializer()
    calls = CallsSerializer()
    synced_at = serializers.DateTimeField(allow_null=True)
    cursor = serializers.IntegerField()


class OrderLineRowSerializer(serializers.Serializer):
    line_id = serializers.IntegerField()
    order_id = serializers.IntegerField(help_text="Nối với `id` bên /orders/stats")
    order_code = serializers.CharField(allow_null=True)
    created_date = serializers.DateField(allow_null=True)
    sku = serializers.CharField(allow_null=True)
    product_name = serializers.CharField(allow_null=True)
    product_group = serializers.CharField(allow_null=True)
    quantity = serializers.IntegerField(allow_null=True)
    unit_price = serializers.IntegerField(allow_null=True)
    amount = serializers.IntegerField(allow_null=True, help_text="VND, thành tiền dòng")
    cursor = serializers.IntegerField()


class AdSpendRowSerializer(serializers.Serializer):
    day = serializers.DateField()
    platform = serializers.ChoiceField(choices=["facebook", "google"])
    account_id = serializers.CharField(help_text="Id TKQC bên nền tảng")
    account_name = serializers.CharField(allow_blank=True)
    channels = serializers.ListField(child=serializers.CharField(), help_text="Mã kênh (Digital) TKQC được gắn; rỗng = chưa map")
    campaign_id = serializers.CharField(allow_null=True, help_text="null = dòng dư của TKQC (is_remainder)")
    campaign_name = serializers.CharField(allow_null=True, allow_blank=True)
    sku = serializers.CharField(allow_null=True, help_text="SKU đã gán cho chiến dịch; null = chưa gán")
    is_remainder = serializers.BooleanField(help_text="true = chi tiêu TKQC − Σ chiến dịch trong ngày; có thể âm")
    spend = serializers.IntegerField(help_text="VND, CHƯA nhân hệ số")
    multiplier = serializers.FloatField(help_text="Hệ số VAT/phụ phí chọn theo `from` của kỳ (xem /definitions.cost_multiplier)")
    spend_adjusted = serializers.IntegerField(help_text="round(spend × multiplier)")
    impressions = serializers.IntegerField()
    clicks = serializers.IntegerField()
    leads = serializers.IntegerField(allow_null=True, help_text="Lead TẠO trong `day` có fb_campaign_id = campaign_id (cùng tập /leads/stats); null ở dòng dư")
    closed = serializers.IntegerField(allow_null=True, help_text="Trong `leads`, sales_status.bucket = closed")
    orders = serializers.IntegerField(allow_null=True, help_text="Đơn có hoá đơn tạo trong `day` quy về campaign (cùng tập /orders/stats)")
    revenue = serializers.IntegerField(allow_null=True, help_text="VND, Σ amounts.total của `orders` (luật total_revenue)")
    cursor = serializers.CharField(help_text="`day|platform|account_id|campaign_id`; account_id rỗng = chiến dịch chưa sync danh mục")


class DefinitionsSerializer(serializers.Serializer):
    order_status = serializers.ListField(child=serializers.DictField())
    invoice_status = serializers.ListField(child=serializers.DictField())
    sales_status = serializers.ListField(child=serializers.DictField())
    order_source = CodeLabelSerializer(many=True)
    channel_group = serializers.ListField(child=serializers.CharField())
    order_buckets = serializers.DictField(child=serializers.CharField())
    lead_buckets = serializers.ListField(child=serializers.CharField())
    excluded_branches = serializers.ListField(child=serializers.CharField())
    metrics = serializers.DictField(child=serializers.CharField())
    ad_account_channels = serializers.ListField(child=serializers.DictField(), help_text="Bản đồ TKQC → mã kênh (Digital)")
    cost_multiplier = serializers.DictField(child=serializers.DictField(), help_text="Luật hệ số VAT/phụ phí theo nền tảng")


class MartCheckSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    data_as_of = serializers.DateTimeField(allow_null=True)
    lag_minutes = serializers.IntegerField(allow_null=True)
    status = serializers.CharField()


class HealthChecksSerializer(serializers.Serializer):
    db = serializers.BooleanField()
    redis = serializers.BooleanField()
    mart = MartCheckSerializer()


class HealthSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok", "degraded"])
    checks = HealthChecksSerializer()


def envelope_of(data_serializer, *, many: bool = False, name: str):
    """Envelope {meta, data} — sinh class động để mỗi endpoint có tên component riêng."""
    return type(
        name,
        (serializers.Serializer,),
        {"meta": MetaSerializer(), "data": data_serializer(many=many)},
    )


def page_envelope_of(row_serializer, *, name: str, cursor_field):
    """Envelope trang {meta, data[], limit, next_cursor}. ``cursor_field`` là lớp Field
    (IntegerField cho id, CharField cho cursor tuple của /ads)."""
    return type(
        name,
        (serializers.Serializer,),
        {
            "meta": MetaSerializer(),
            "data": row_serializer(many=True),
            "limit": serializers.IntegerField(),
            "next_cursor": cursor_field(allow_null=True, help_text="null = hết"),
        },
    )
