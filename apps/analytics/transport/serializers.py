from rest_framework import serializers


class CoverageFieldSerializer(serializers.Serializer):
    field = serializers.CharField()
    declared = serializers.IntegerField()
    ai = serializers.IntegerField()
    total = serializers.IntegerField()
    pct = serializers.FloatField()


class CoverageSerializer(serializers.Serializer):
    total_customers = serializers.IntegerField()
    fields = CoverageFieldSerializer(many=True)


class CustomerSearchHitSerializer(serializers.Serializer):
    """Một gợi ý trong ô tìm khách — mirror SELECT của `search_customers`."""

    customer_id = serializers.IntegerField()
    code = serializers.CharField(allow_null=True)
    full_name = serializers.CharField(allow_null=True)
    orders_gtc = serializers.IntegerField()
    total_spent = serializers.DecimalField(max_digits=14, decimal_places=2)
    last_purchase_at = serializers.DateTimeField(allow_null=True)
    segment_code = serializers.CharField(allow_null=True)
    segment_name = serializers.CharField(allow_null=True)
    era = serializers.CharField(allow_null=True)


class TimelineOrderSerializer(serializers.Serializer):
    """Một đơn trong dòng thời gian — mirror `_TIMELINE_COLUMNS`."""

    order_id = serializers.IntegerField()
    order_code = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()
    created_date = serializers.DateField()
    status = serializers.IntegerField()
    is_gtc = serializers.BooleanField()
    total_after_discount = serializers.DecimalField(max_digits=12, decimal_places=2)
    channel_name = serializers.CharField(allow_null=True)
    staff_name = serializers.CharField(allow_null=True)
    item_count = serializers.IntegerField()
    primary_sku = serializers.CharField(allow_null=True)
    province = serializers.CharField(allow_null=True)
    district = serializers.CharField(allow_null=True)
    call_count = serializers.IntegerField()
    quality_total_score = serializers.FloatField(allow_null=True)


class CustomerProfileSerializer(serializers.Serializer):
    """Hồ sơ 360 của một khách — mirror `_PROFILE_COLUMNS` + `full_name`.

    Ba nhóm trường xếp theo độ phủ giảm dần: định danh & hành vi mua (phủ ~100%),
    chân dung suy từ hành vi (phủ theo số đơn GTC), rồi chân dung khai báo
    (phủ dưới 2% — xem `coverage()`). Tầng hiển thị ẩn hẳn nhóm cuối khi rỗng.
    """

    customer_id = serializers.IntegerField()
    code = serializers.CharField(allow_null=True)
    full_name = serializers.CharField(allow_null=True)
    gender = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField(allow_null=True)
    is_new_customer = serializers.BooleanField(allow_null=True)

    segment_code = serializers.CharField(allow_null=True)
    segment_name = serializers.CharField(allow_null=True)
    # Cả hai trường dưới là CHUỖI, không phải số — `care_priority` là câu tiếng
    # Việt ("Chốt đơn", "Tái kích hoạt ngay") lấy từ `CustomerSegmentCategory.
    # care_priority` (TextField), còn `latest_sales_status_code` là mã chữ
    # ("created_order", "no_answer_3") từ `CustomerSegmentSnapshot` (CharField).
    # Khai nhầm thành IntegerField làm mọi hồ sơ có phân khúc trả 500 — 423k
    # khách mang giá trị "created_order", tức gần như toàn bộ tệp.
    care_priority = serializers.CharField(allow_null=True)
    has_problem = serializers.BooleanField(allow_null=True)
    latest_sales_status_code = serializers.CharField(allow_null=True)

    orders_gtc = serializers.IntegerField()
    orders_all = serializers.IntegerField()
    orders_cancelled = serializers.IntegerField()
    total_spent = serializers.DecimalField(max_digits=14, decimal_places=2)
    avg_order_value = serializers.FloatField(allow_null=True)
    first_purchase_at = serializers.DateTimeField(allow_null=True)
    last_purchase_at = serializers.DateTimeField(allow_null=True)
    days_since_last_purchase = serializers.IntegerField(allow_null=True)
    repurchase_gap_median_days = serializers.FloatField(allow_null=True)
    channels = serializers.ListField(child=serializers.CharField())
    provinces = serializers.ListField(child=serializers.CharField())
    top_skus = serializers.ListField(child=serializers.CharField())
    distinct_products = serializers.IntegerField()
    call_count = serializers.IntegerField()
    avg_quality_score = serializers.FloatField(allow_null=True)

    era = serializers.CharField(allow_null=True)
    price_tier_lifetime = serializers.CharField(allow_null=True)
    purchase_rhythm_lifetime = serializers.CharField(allow_null=True)
    recency_bucket = serializers.CharField(allow_null=True)
    channel_affinity = serializers.CharField(allow_null=True)
    product_affinity = serializers.CharField(allow_null=True)
    buyer_role = serializers.CharField(allow_null=True)

    age_text = serializers.CharField(allow_null=True)
    age_text_src = serializers.CharField(allow_null=True)
    occupation_code = serializers.CharField(allow_null=True)
    occupation_code_src = serializers.CharField(allow_null=True)
    pain_level_code = serializers.CharField(allow_null=True)
    pain_level_code_src = serializers.CharField(allow_null=True)
    pain_duration_code = serializers.CharField(allow_null=True)
    used_massage_before = serializers.BooleanField(allow_null=True)
    lifestyle_habit_codes = serializers.ListField(child=serializers.CharField())
    relax_time_codes = serializers.ListField(child=serializers.CharField())
    purchase_barrier_codes = serializers.ListField(child=serializers.CharField())
    delax_source_codes = serializers.ListField(child=serializers.CharField())
    need_codes = serializers.ListField(child=serializers.CharField())
    need_group_codes = serializers.ListField(child=serializers.CharField())
    filled_field_count = serializers.IntegerField()
    ai_field_count = serializers.IntegerField()


class CustomerProfileResponseSerializer(serializers.Serializer):
    profile = CustomerProfileSerializer()
    timeline = TimelineOrderSerializer(many=True)
    timeline_truncated = serializers.BooleanField()
