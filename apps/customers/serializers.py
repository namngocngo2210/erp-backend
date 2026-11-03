from rest_framework import serializers

from apps.orders.crm_catalog import (
    CALL_DURATION_LABELS,
    CUSTOMER_CLASSIFICATION_LABELS,
    NOT_CLOSED_REASON_LABELS,
    OCCUPATION_LABELS,
    PAIN_DURATION_LABELS,
    PAIN_LEVEL_LABELS,
    POST_PURCHASE_RATING_LABELS,
    SALES_STATUS_LABELS,
)

from .models import Customer
from .services import CustomerPhoneMasker
from apps.users.phone_visibility import PhoneVisibility
from apps.users.models import User

GENDER_LABELS = {0: "Nữ", 1: "Nam"}


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ("id", "code", "full_name", "phone_raw", "phone_normalized", "gender", "created_at", "updated_at")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Che SĐT khách KHÔNG có đơn nào do người xem phụ trách — FAIL-CLOSED khi view
        # quên truyền context `phone_masker` (cùng lý lẽ với OrderSerializer: quên một
        # call site phải lộ thành "admin thấy số bị che", không được im lặng rò).
        masker = self.context.get("phone_masker")
        if masker is None:
            masker = CustomerPhoneMasker(
                visibility=PhoneVisibility(user_id=None, can_view_full=False),
                owned_customer_ids=frozenset(),
            )
        for field_name in ("phone_raw", "phone_normalized"):
            if field_name in data:
                data[field_name] = masker.apply(data[field_name], instance.id)
        return data


class CustomerUpsertSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    phone_raw = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    gender = serializers.IntegerField(required=False, allow_null=True, min_value=0, max_value=1)


class CustomerBulkDeleteSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False)

    def validate_ids(self, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class CustomerOrderAggregateSerializer(serializers.Serializer):
    total_orders = serializers.IntegerField()
    delivered_count = serializers.IntegerField()
    in_delivery_count = serializers.IntegerField()
    bombed_count = serializers.IntegerField()
    unreachable_count = serializers.IntegerField()
    current_customer_classification_code = serializers.CharField(allow_null=True)
    current_customer_classification_label = serializers.CharField(allow_null=True)


class LoyalCustomerProductSerializer(serializers.Serializer):
    sku = serializers.CharField()
    quantity = serializers.IntegerField()


class LoyalCustomerSerializer(serializers.Serializer):
    rank = serializers.IntegerField()
    customer_id = serializers.IntegerField()
    code = serializers.CharField()
    full_name = serializers.CharField(allow_blank=True)
    phone = serializers.CharField(allow_blank=True)
    purchase_count = serializers.IntegerField()
    days_since_last_purchase = serializers.IntegerField(allow_null=True)
    total_spent = serializers.CharField()
    products = LoyalCustomerProductSerializer(many=True)
    last_purchase_at = serializers.DateTimeField(allow_null=True)
    sale_staff_id = serializers.IntegerField(allow_null=True)
    sale_staff_name = serializers.CharField(allow_blank=True)
    last_care_call_at = serializers.DateField(allow_null=True)
    purchase_channels = serializers.ListField(child=serializers.CharField())
    segment_code = serializers.CharField(allow_blank=True)
    segment_name = serializers.CharField(allow_blank=True)
    segment_care_priority = serializers.CharField(allow_blank=True)
    sale_action = serializers.CharField(allow_blank=True)
    special_note = serializers.CharField(allow_blank=True)


class LoyalCustomerNoteUpdateSerializer(serializers.Serializer):
    sale_action = serializers.CharField(required=False, allow_blank=True)
    special_note = serializers.CharField(required=False, allow_blank=True)
    sale_staff_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    last_care_call_at = serializers.DateField(required=False, allow_null=True)

    def validate_sale_staff_id(self, value):
        if value is None:
            return None
        # NOTE: loại admin (role hệ thống) thay cho filter role=STAFF cũ.
        exists = (
            User.objects.filter(id=value, is_active=True, deleted_at__isnull=True)
            .exclude(role_assignments__role__is_system=True)
            .exists()
        )
        if not exists:
            raise serializers.ValidationError("Sale phụ trách không hợp lệ.")
        return value

    def validate(self, attrs):
        allowed = {"sale_action", "special_note", "sale_staff_id", "last_care_call_at"}
        if not any(field in attrs for field in allowed):
            raise serializers.ValidationError("Cần ít nhất một trường để cập nhật.")
        return attrs


class CustomerResaleNoteUpdateSerializer(serializers.Serializer):
    resale_status = serializers.CharField(required=False, allow_blank=True)
    resale_suggestion = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if "resale_status" not in attrs and "resale_suggestion" not in attrs:
            raise serializers.ValidationError("Cần ít nhất một trường để cập nhật.")
        return attrs


class ResaleContactUpdateSerializer(serializers.Serializer):
    contact_no = serializers.IntegerField(min_value=1, max_value=3)
    checked = serializers.BooleanField()
    channel = serializers.CharField(required=False, allow_blank=True)
    result = serializers.CharField(required=False, allow_blank=True)
    customer_status = serializers.CharField(required=False, allow_blank=True)


class ResaleAppointmentUpdateSerializer(serializers.Serializer):
    appointment_at = serializers.DateTimeField()
    channel = serializers.CharField(required=False, allow_blank=True)
    purpose = serializers.CharField(required=False, allow_blank=True)
    note = serializers.CharField(required=False, allow_blank=True)


class ResaleOrderCreateSerializer(serializers.Serializer):
    product = serializers.CharField()
    quantity = serializers.IntegerField(min_value=1)
    order_value = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    channel = serializers.CharField(required=False, allow_blank=True)
    note = serializers.CharField(required=False, allow_blank=True)


class ResaleRecipientSerializer(serializers.Serializer):
    name = serializers.CharField(allow_blank=True)
    phone = serializers.CharField(allow_blank=True)
    address = serializers.CharField(allow_blank=True)
    province = serializers.CharField(allow_blank=True)
    district = serializers.CharField(allow_blank=True)
    ward = serializers.CharField(allow_blank=True)


class ResaleCustomerSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    full_name = serializers.CharField(allow_blank=True)
    phone = serializers.CharField(allow_blank=True)


class ResaleAssessmentSerializer(serializers.Serializer):
    customer_classification = serializers.CharField(allow_blank=True)
    sales_status = serializers.CharField(allow_blank=True)
    not_closed_reason = serializers.CharField(allow_blank=True)
    delivery_business_status = serializers.CharField(allow_blank=True)
    post_purchase_rating = serializers.CharField(allow_blank=True)
    note = serializers.CharField(allow_blank=True)


class ResaleHistoryOrderSerializer(serializers.Serializer):
    order_id = serializers.IntegerField()
    order_code = serializers.CharField()
    created_at = serializers.DateTimeField()
    status = serializers.IntegerField()
    products = serializers.ListField(child=serializers.CharField())
    total_amount = serializers.CharField()
    assessment = ResaleAssessmentSerializer()


class ResaleTrackingRowSerializer(serializers.Serializer):
    order_id = serializers.IntegerField()
    order_code = serializers.CharField()
    shipping_code = serializers.CharField(allow_blank=True)
    delivered_at = serializers.DateTimeField()
    recipient = ResaleRecipientSerializer()
    customer = ResaleCustomerSerializer()
    channel = serializers.CharField(allow_blank=True)
    sale_staff_id = serializers.IntegerField(allow_null=True)
    sale_staff_name = serializers.CharField(allow_blank=True)
    segment_code = serializers.CharField(allow_blank=True)
    segment_name = serializers.CharField(allow_blank=True)
    segment_care_priority = serializers.CharField(allow_blank=True)
    last_purchase_at = serializers.DateTimeField(allow_null=True)
    days_since_last_purchase = serializers.IntegerField(allow_null=True)
    products = serializers.ListField(child=serializers.CharField())
    order_value = serializers.CharField()
    order_assessment = ResaleHistoryOrderSerializer(many=True)
    contact_1_at = serializers.DateTimeField(allow_null=True)
    contact_2_at = serializers.DateTimeField(allow_null=True)
    contact_3_at = serializers.DateTimeField(allow_null=True)
    contact_logs = serializers.ListField(child=serializers.DictField(), required=False)
    appointment = serializers.DictField(required=False, allow_null=True)
    resale_orders = serializers.ListField(child=serializers.DictField(), required=False)


class CustomerProfileIdentitySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    full_name = serializers.CharField(allow_blank=True)
    phone = serializers.CharField(allow_blank=True)


class CustomerProfileDemographicsSerializer(serializers.Serializer):
    gender = serializers.IntegerField(allow_null=True)
    gender_label = serializers.CharField(allow_null=True)
    province = serializers.CharField(allow_blank=True)
    address = serializers.CharField(allow_blank=True)
    age_text = serializers.CharField(allow_blank=True)
    age_group_label = serializers.CharField(allow_null=True)
    occupation_code = serializers.CharField(allow_null=True)
    occupation_label = serializers.CharField(allow_null=True)


class CustomerProfileChannelSerializer(serializers.Serializer):
    channel_id = serializers.IntegerField(allow_null=True)
    channel_name = serializers.CharField(allow_blank=True)


class CustomerProfileSegmentSerializer(serializers.Serializer):
    segment_code = serializers.CharField()
    segment_name = serializers.CharField(allow_blank=True)


class CustomerProfileNeedItemSerializer(serializers.Serializer):
    group_code = serializers.CharField()
    group_name = serializers.CharField()
    type_code = serializers.CharField()
    type_name = serializers.CharField()


class CustomerProfileNeedsSerializer(serializers.Serializer):
    need_group_code = serializers.CharField(allow_blank=True)
    need_group_name = serializers.CharField(allow_blank=True)
    items = CustomerProfileNeedItemSerializer(many=True)


class CustomerProfilePurchaseSerializer(serializers.Serializer):
    first_purchase_at = serializers.DateTimeField(allow_null=True)
    last_purchase_at = serializers.DateTimeField(allow_null=True)
    total_orders = serializers.IntegerField()
    total_spent = serializers.CharField()
    distinct_skus = serializers.ListField(child=serializers.CharField())


class CustomerProfileLatestOrderSerializer(serializers.Serializer):
    sku = serializers.CharField(allow_null=True)
    product = serializers.CharField(allow_blank=True)
    last_order_amount = serializers.CharField(allow_null=True)
    last_order_status = serializers.IntegerField(allow_null=True)
    sales_status_code = serializers.CharField(allow_null=True)
    sales_status_label = serializers.CharField(allow_null=True)
    not_closed_reason_code = serializers.CharField(allow_null=True)
    not_closed_reason_label = serializers.CharField(allow_null=True)


class CustomerProfilePersonaSerializer(serializers.Serializer):
    customer_classification_code = serializers.CharField(allow_null=True)
    customer_classification_label = serializers.CharField(allow_null=True)
    pain_level_code = serializers.CharField(allow_null=True)
    pain_level_label = serializers.CharField(allow_null=True)
    pain_duration_code = serializers.CharField(allow_null=True)
    pain_duration_label = serializers.CharField(allow_null=True)
    lifestyle_habit_codes = serializers.ListField(
        child=serializers.CharField(), allow_null=True
    )
    used_massage_before = serializers.BooleanField(allow_null=True)
    relax_time_codes = serializers.ListField(child=serializers.CharField(), allow_null=True)
    purchase_barrier_codes = serializers.ListField(
        child=serializers.CharField(), allow_null=True
    )
    delax_source_codes = serializers.ListField(child=serializers.CharField(), allow_null=True)
    comparing_brand_text = serializers.CharField(allow_null=True, allow_blank=True)
    call_duration_code = serializers.CharField(allow_null=True)
    call_duration_label = serializers.CharField(allow_null=True)
    post_purchase_feedback_text = serializers.CharField(allow_null=True, allow_blank=True)
    post_purchase_rating_code = serializers.CharField(allow_null=True)
    post_purchase_rating_label = serializers.CharField(allow_null=True)


class CustomerProfileResaleSerializer(serializers.Serializer):
    last_resale_at = serializers.DateTimeField(allow_null=True)
    resale_status = serializers.CharField(allow_blank=True)
    resale_suggestion = serializers.CharField(allow_blank=True)


class CustomerProfileSerializer(serializers.Serializer):
    """1 dòng/khách. Nhận dict do CustomerProfileService.list_profiles tạo ra.

    Thêm các *_label phẳng vào trong nhóm con tương ứng để frontend hiển thị trực tiếp.

    Các field khai báo dưới đây CHỈ mô tả shape cho OpenAPI/schema (WP6.2);
    ``to_representation`` override toàn bộ nên response thật không đổi.
    """

    identity = CustomerProfileIdentitySerializer()
    demographics = CustomerProfileDemographicsSerializer()
    channel = CustomerProfileChannelSerializer()
    segment = CustomerProfileSegmentSerializer()
    needs = CustomerProfileNeedsSerializer()
    purchase = CustomerProfilePurchaseSerializer()
    latest_order = CustomerProfileLatestOrderSerializer()
    persona = CustomerProfilePersonaSerializer()
    resale = CustomerProfileResaleSerializer()

    def to_representation(self, instance):
        data = dict(instance)  # shallow copy các nhóm con

        demo = dict(data["demographics"])
        demo["gender_label"] = GENDER_LABELS.get(demo.get("gender"))
        demo["occupation_label"] = OCCUPATION_LABELS.get(demo.get("occupation_code"))
        data["demographics"] = demo

        latest = dict(data["latest_order"])
        latest["sales_status_label"] = SALES_STATUS_LABELS.get(latest.get("sales_status_code"))
        latest["not_closed_reason_label"] = NOT_CLOSED_REASON_LABELS.get(latest.get("not_closed_reason_code"))
        data["latest_order"] = latest

        persona = dict(data["persona"])
        persona["customer_classification_label"] = CUSTOMER_CLASSIFICATION_LABELS.get(
            persona.get("customer_classification_code")
        )
        persona["pain_level_label"] = PAIN_LEVEL_LABELS.get(persona.get("pain_level_code"))
        persona["pain_duration_label"] = PAIN_DURATION_LABELS.get(persona.get("pain_duration_code"))
        persona["call_duration_label"] = CALL_DURATION_LABELS.get(persona.get("call_duration_code"))
        persona["post_purchase_rating_label"] = POST_PURCHASE_RATING_LABELS.get(
            persona.get("post_purchase_rating_code")
        )
        data["persona"] = persona
        return data
