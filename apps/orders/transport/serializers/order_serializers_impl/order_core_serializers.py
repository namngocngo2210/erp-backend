from rest_framework import serializers

from apps.orders.assessment_fields import ASSESSMENT_FIELDS
from apps.orders.crm_catalog import (
    BOMB_REASON_GROUP_LABELS,
    BOMB_REASON_TYPE_GROUP_MAP,
    BOMB_REASON_TYPE_LABELS,
    CRM_CODE_LABELS,
)
from apps.orders.models import NeedType, Order
from apps.users.field_policy import FieldPolicyOutputMixin
from apps.users.phone_visibility import PhoneVisibility, is_public_phone_source

from apps.orders.invoice_lock import is_invoice_locked

from .product_item_crm_serializers import OrderCRMSerializer, OrderItemSerializer


class OrderCRMNeedInputSerializer(serializers.Serializer):
    group_code = serializers.CharField(required=False, allow_blank=True)
    type_code = serializers.CharField()

    def validate(self, attrs):
        attrs = super().validate(attrs)
        type_code = str(attrs.get("type_code") or "").strip()
        group_code = str(attrs.get("group_code") or "").strip()
        need_type = NeedType.objects.select_related("group").filter(code=type_code, is_active=True).first()
        if need_type is None:
            raise serializers.ValidationError({"type_code": "Nhu cầu không hợp lệ."})
        if group_code and need_type.group.code != group_code:
            raise serializers.ValidationError({"group_code": "Nhóm nhu cầu không khớp với loại nhu cầu."})
        attrs["need_type"] = need_type
        attrs["type_code"] = need_type.code
        attrs["group_code"] = need_type.group.code
        return attrs


def _build_assessment_input_field(spec):
    """Build a DRF input field for an AssessmentField spec.

    Per-field validation (whitelist, max_length, type) is enforced in the
    service layer via `coerce_and_validate`, so here we only need permissive
    pass-through that accepts null/blank from the client.
    """
    if spec.kind == "text":
        return serializers.CharField(required=False, allow_blank=True, allow_null=True)
    if spec.kind == "code":
        return serializers.CharField(required=False, allow_null=True, allow_blank=True)
    if spec.kind == "code_array":
        return serializers.ListField(
            child=serializers.CharField(allow_blank=True),
            required=False,
            allow_null=True,
        )
    if spec.kind == "bool":
        return serializers.BooleanField(required=False, allow_null=True)
    if spec.kind == "int":
        return serializers.IntegerField(required=False, allow_null=True)
    if spec.kind == "datetime":
        return serializers.DateTimeField(required=False, allow_null=True)
    return None


class OrderCRMInputSerializerMeta(serializers.SerializerMetaclass):
    """Auto-inject DRF fields for every ASSESSMENT_FIELDS entry not explicitly
    declared on the serializer, keeping `assessment_fields.py` as the single
    source of truth for the CRM payload shape."""

    def __new__(mcs, name, bases, attrs):
        for spec in ASSESSMENT_FIELDS:
            if spec.name in attrs:
                continue
            field = _build_assessment_input_field(spec)
            if field is not None:
                attrs[spec.name] = field
        return super().__new__(mcs, name, bases, attrs)


class OrderCRMInputSerializer(serializers.Serializer, metaclass=OrderCRMInputSerializerMeta):
    # Fields with stricter/custom validation are declared explicitly;
    # every other ASSESSMENT_FIELDS entry is injected by the metaclass above.
    potential_level = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=5)
    needs = OrderCRMNeedInputSerializer(many=True, required=False)

    def to_internal_value(self, data):
        attrs = super().to_internal_value(data)
        if isinstance(data, dict):
            # Key động (registry assessment_field_defs) không có field DRF khai báo —
            # chuyển nguyên trạng, validate ở tầng service (_apply_dynamic_assessment_payload)
            # để không tồn tại nhánh validate thứ hai lệch với luồng nhập tay.
            for key, value in data.items():
                if key not in self.fields:
                    attrs[key] = value
        return attrs

    def _normalize_code(self, value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    def _validate_code(self, attrs: dict, field_name: str):
        code = self._normalize_code(attrs.get(field_name))
        if not code:
            attrs[field_name] = None
            return
        allowed_codes = CRM_CODE_LABELS.get(field_name, {})
        if code not in allowed_codes:
            raise serializers.ValidationError({field_name: "Giá trị không hợp lệ."})
        attrs[field_name] = code

    def validate(self, attrs):
        attrs = super().validate(attrs)
        for field_name in (
            "customer_classification_code",
            "sales_status_code",
            "not_closed_reason_code",
            "delivery_business_status_code",
        ):
            if field_name in attrs:
                self._validate_code(attrs, field_name)
        for field_name in ("customer_attitude_raw", "bomb_reason_text", "internal_note"):
            if field_name in attrs:
                attrs[field_name] = str(attrs.get(field_name) or "").strip()

        if "bomb_reason_group_code" in attrs:
            attrs["bomb_reason_group_code"] = self._normalize_code(attrs.get("bomb_reason_group_code"))
            if attrs["bomb_reason_group_code"] and attrs["bomb_reason_group_code"] not in BOMB_REASON_GROUP_LABELS:
                raise serializers.ValidationError({"bomb_reason_group_code": "Nhóm lý do bom không hợp lệ."})

        if "bomb_reason_type_code" in attrs:
            attrs["bomb_reason_type_code"] = self._normalize_code(attrs.get("bomb_reason_type_code"))
            if attrs["bomb_reason_type_code"] and attrs["bomb_reason_type_code"] not in BOMB_REASON_TYPE_LABELS:
                raise serializers.ValidationError({"bomb_reason_type_code": "Loại lý do bom không hợp lệ."})

        bomb_group_code = attrs.get("bomb_reason_group_code")
        bomb_type_code = attrs.get("bomb_reason_type_code")
        if bomb_type_code:
            expected_group = BOMB_REASON_TYPE_GROUP_MAP.get(bomb_type_code)
            if bomb_group_code and bomb_group_code != expected_group:
                raise serializers.ValidationError({"bomb_reason_group_code": "Nhóm lý do bom không khớp với loại đã chọn."})
            attrs["bomb_reason_group_code"] = expected_group
        if "needs" in attrs:
            deduped_needs = []
            seen = set()
            for item in attrs.get("needs") or []:
                type_code = item["need_type"].code
                if type_code in seen:
                    continue
                seen.add(type_code)
                deduped_needs.append(item)
            attrs["needs"] = deduped_needs
        return attrs


class OrderSerializer(FieldPolicyOutputMixin, serializers.ModelSerializer):
    field_section = "orders"
    customer_phone = serializers.CharField(source="customer.phone_raw", read_only=True)
    customer_code = serializers.CharField(source="customer.code", read_only=True)
    customer_gender = serializers.CharField(source="customer.gender", read_only=True)
    # Loại khách: True = khách mới (≤1 đơn), False = khách cũ (>1 đơn).
    customer_is_new = serializers.BooleanField(source="customer.is_new_customer", read_only=True)
    channel_code = serializers.CharField(source="channel.code", read_only=True)
    channel_name = serializers.CharField(source="channel.name", read_only=True)
    channel_owner = serializers.CharField(source="channel.owner", read_only=True)
    # CODE loại kênh (không phải id/tên): FE đối chiếu bộ lọc "Loại kênh" phía
    # client cho hàng realtime upsert bằng chính giá trị gửi lên server.
    channel_type = serializers.CharField(
        source="channel.channel_type.code", read_only=True, allow_null=True, default=None
    )
    channel_type_name = serializers.CharField(
        source="channel.channel_type.name", read_only=True, allow_null=True, default=None
    )
    kiotviet_channel_id = serializers.IntegerField(source="kiotviet_channel.id", read_only=True, allow_null=True)
    kiotviet_name = serializers.CharField(source="kiotviet_channel.name", read_only=True, allow_null=True)
    staff_id = serializers.IntegerField(source="staff.id", read_only=True, allow_null=True)
    staff_name = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    is_invoice_locked = serializers.SerializerMethodField()
    is_kiotviet_processing = serializers.SerializerMethodField()
    latest_kiotviet_job_status = serializers.SerializerMethodField()
    latest_kiotviet_job_action = serializers.SerializerMethodField()
    latest_kiotviet_job_error = serializers.SerializerMethodField()
    items = OrderItemSerializer(many=True, read_only=True)
    crm = serializers.SerializerMethodField()
    recall = serializers.SerializerMethodField()
    recall_entries = serializers.SerializerMethodField()
    fulfillment_warehouse_name = serializers.CharField(
        source="fulfillment_warehouse.name", read_only=True, default=None
    )

    class Meta:
        model = Order
        fields = (
            "id",
            "name",
            "phone",
            "raw_address",
            "address",
            "has_address",
            "ward",
            "district",
            "province",
            "ward_code",
            "district_code",
            "province_code",
            "kiotviet_location",
            "kiotviet_ward",
            "address_level2",
            "ward_level2",
            "province_level2",
            "ward_code_level2",
            "province_code_level2",
            "product",
            "product_raw",
            "sku",
            "quantity",
            "items",
            "channel_owner",
            "url_page",
            "channel_code",
            "channel_name",
            "channel_type",
            "channel_type_name",
            "kiotviet_channel_id",
            "kiotviet_name",
            "staff_id",
            "staff_name",
            "external_order_id",
            "status",
            "discount_amount",
            "booking_voucher_discount",
            "weight",
            "dimensions",
            "branch_id",
            "order_type",
            "koc_order_type",
            "order_source",
            "vat",
            "is_export_vat",
            "vat_link",
            "return_date",
            "show_orders",
            "other_price",
            "customer_prepaid",
            "result",
            "order_code",
            "invoice_code",
            "invoice_id",
            "invoice_status",
            "is_delivery_invoice_printed",
            "customer_return",
            "old_invoice_code",
            "old_id",
            "feedback",
            "total_after_discount",
            "total_amount",
            "shipping_fee",
            "payment_method",
            "shipping_code",
            "customer_phone",
            "customer_code",
            "customer_gender",
            "customer_is_new",
            "crm",
            "recall",
            "recall_entries",
            "created_at",
            # Mốc sale chốt đơn — nuôi cột "Ngày chốt" và tuỳ chọn sắp xếp
            # "Ngày chốt: Mới/Cũ" của trang đơn. NULL với đơn chưa chốt hoặc đơn
            # không đi qua luồng CRM (đơn sàn / sync KiotViet).
            "sales_closed_at",
            "can_edit",
            "is_invoice_locked",
            "is_kiotviet_processing",
            "latest_kiotviet_job_status",
            "latest_kiotviet_job_action",
            "latest_kiotviet_job_error",
            "shipping_provider",
            "fulfillment_warehouse",
            "fulfillment_warehouse_name",
            "shipping_type",
            # Trạng thái THÔ theo hệ đối tác (Foxia/CNV/Pancake POS) — dòng phụ của cột
            # "Trạng thái vận chuyển thực". Chỉ hệ thống ghi (xem Order model).
            "partner_delivery_status",
            "partner_dvvc_delivery_status",
        )
        # `sales_closed_at` do tầng service ghi theo luật chuyển trạng thái sale
        # (OrderServiceHelpers._touch_sales_closed_at), KHÔNG được nhận từ client:
        # serializer này cũng phục vụ luồng ghi, để trống là mở cửa cho PATCH tay
        # đè lên mốc chốt.
        read_only_fields = ("sales_closed_at", "partner_delivery_status", "partner_dvvc_delivery_status")

    # SĐT khách trên đơn KHÔNG do mình phụ trách bị che (091****678) với user thiếu
    # quyền `customer-phone:view` — xem apps/users/phone_visibility.py.
    #
    # FAIL-CLOSED khi context thiếu key `phone_visibility`: che hết, kể cả với admin.
    # Cố ý — serializer này được dựng ở ~10 call site, quên truyền context ở một chỗ
    # mới thì lỗi phải lộ ra thành "admin thấy số bị che" (báo ngay) chứ không được
    # im lặng rò cả CSDL khách. Mọi view dựng context qua `order_base_context` /
    # `_build_order_list_context` (apps/orders/.../_view_helpers.py) nên đã có sẵn key.
    _MASKED_PHONE_FIELDS = ("phone", "customer_phone")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        visibility = self.context.get("phone_visibility")
        if visibility is None:
            visibility = PhoneVisibility(user_id=None, can_view_full=False)
        owner_id = getattr(instance, "staff_id", None)
        # Đơn sàn TMĐT: sàn đã che sẵn SĐT nên miễn che (xem PhoneVisibility.apply).
        public_source = is_public_phone_source(getattr(instance, "order_source", ""))
        for field_name in self._MASKED_PHONE_FIELDS:
            if field_name in data:
                data[field_name] = visibility.apply(
                    data[field_name], owner_id, public_source=public_source
                )
        return data

    def get_can_edit(self, obj):
        # Đơn đã hủy (invoice_status=2 hoặc status=6) chỉ xem, không sửa.
        if obj.invoice_status == 2 or obj.status == 6:
            return False
        can_edit_fn = self.context.get("can_edit_fn")
        if not can_edit_fn:
            return False
        return can_edit_fn(obj.channel.code)

    def get_is_invoice_locked(self, obj) -> bool:
        # Đơn đã có mã hoá đơn: KHOÁ khối sản phẩm + tài chính, phần còn lại vẫn sửa
        # được. Cố tình KHÔNG hạ `can_edit` xuống False — `can_edit` đang gate cả cột
        # "Trạng thái vận chuyển thực" của trang Vận đơn, tắt nó là chết luồng kho.
        return is_invoice_locked(obj)

    def get_staff_name(self, obj) -> str | None:
        staff = getattr(obj, "staff", None)
        if not staff:
            return None
        return staff.display_name or staff.username

    def _latest_kiotviet_job(self, obj):
        # List views inject a batched {order_id: job} map (see _build_order_list_context)
        # to avoid 4 per-row subqueries; other callers still rely on query annotations.
        job_map = self.context.get("kiotviet_job_map")
        return job_map.get(obj.id) if job_map is not None else None

    def _kiotviet_owner_id(self, obj):
        row = self._latest_kiotviet_job(obj)
        if row is not None:
            return row.get("requested_by_id")
        return getattr(obj, "latest_kiotviet_job_requested_by", None)

    def _kiotviet_hidden_for_viewer(self, obj) -> bool:
        # Trạng thái job KiotViet chỉ hiện cho ĐÚNG người thực hiện job mới nhất —
        # không lộ badge sang user khác. Chỉ gate khi view truyền `current_user_id`
        # vào context (list views); caller khác giữ nguyên hành vi cũ để tránh regress.
        viewer_id = self.context.get("current_user_id")
        if viewer_id is None:
            return False
        return self._kiotviet_owner_id(obj) != viewer_id

    def get_is_kiotviet_processing(self, obj) -> bool:
        if self._kiotviet_hidden_for_viewer(obj):
            return False
        active_ids = self.context.get("kiotviet_active_ids")
        if active_ids is not None:
            return obj.id in active_ids
        return bool(getattr(obj, "is_kiotviet_processing", False))

    def get_latest_kiotviet_job_status(self, obj) -> str | None:
        if self._kiotviet_hidden_for_viewer(obj):
            return None
        if self.context.get("kiotviet_job_map") is not None:
            row = self._latest_kiotviet_job(obj)
            return row["status"] if row else None
        return getattr(obj, "latest_kiotviet_job_status", None)

    def get_latest_kiotviet_job_action(self, obj) -> str | None:
        if self._kiotviet_hidden_for_viewer(obj):
            return None
        if self.context.get("kiotviet_job_map") is not None:
            row = self._latest_kiotviet_job(obj)
            return row["action"] if row else None
        return getattr(obj, "latest_kiotviet_job_action", None)

    def get_latest_kiotviet_job_error(self, obj) -> str | None:
        if self._kiotviet_hidden_for_viewer(obj):
            return None
        if self.context.get("kiotviet_job_map") is not None:
            row = self._latest_kiotviet_job(obj)
            return row["error_message"] if row else None
        return getattr(obj, "latest_kiotviet_job_error", None)

    def get_crm(self, obj) -> dict | None:
        # List endpoints pre-serialize the whole page's CRM in ONE many=True pass
        # (see _build_order_list_context) so DRF binds OrderCRMSerializer's ~40 fields
        # + nested needs serializer once, not per row. Fast path: just look it up.
        crm_map = self.context.get("crm_map")
        if crm_map is not None:
            return crm_map.get(obj.id)
        assessment = getattr(obj, "assessment", None)
        if assessment is None:
            return None
        # List endpoints opt out of the journey-events payload (only the detail dialog renders
        # the timeline). Skipping it avoids both the prefetch and per-row event serialization.
        if self.context.get("skip_journey_events"):
            events: list = []
        else:
            events = list(obj.journey_events.all())
        child_ctx = {"journey_events": events}
        next_reminder_map = self.context.get("next_reminder_map")
        if next_reminder_map is not None:
            child_ctx["next_reminder_map"] = next_reminder_map
        return OrderCRMSerializer(assessment, context=child_ctx).data

    def get_recall(self, obj) -> dict | None:
        recall = getattr(obj, "recall", None)
        if recall is None:
            return None
        # Mã/ngày thu hồi đã tách sang `recall_entries` (1-N). Ở đây chỉ còn phiếu
        # trả hàng do job KiotViet ghi ở mức đơn.
        return {"return_slip_code": ", ".join(recall.return_slip_code or [])}

    def get_recall_entries(self, obj) -> list[dict]:
        # Luôn trả mảng (rỗng khi chưa có nhóm) chứ không None: lưới và trang chi
        # tiết đều render danh sách, khỏi phải kiểm null ở mọi chỗ dùng.
        return [
            {
                "id": entry.id,
                "recall_code": entry.recall_code,
                "recall_created_date": (
                    entry.recall_created_date.isoformat() if entry.recall_created_date else None
                ),
                "recall_received_date": (
                    entry.recall_received_date.isoformat() if entry.recall_received_date else None
                ),
            }
            for entry in obj.recall_entries.all()
        ]


# Field CHỈ trang chi tiết đọc — audit FE 2026-08-04 rà toàn bộ cell/filter-mirror/
# realtime-merge/dialog của 6 trang list (docs/order-list-fanout-hardening-2026-08-04.md).
# Cắt khỏi payload list để hạ serialize (~70 field/row là nguồn chính của
# serialize=460ms/100 rows). Trang chi tiết LUÔN fetch GET /api/orders/{id}
# (OrderSerializer đầy đủ — FE đổi kèm cùng MR) nên cắt ở list không mất dữ liệu.
# THÊM FIELD MỚI VÀO ĐÂY chỉ khi chắc chắn không bảng list nào đọc nó.
LIST_EXCLUDED_ORDER_FIELDS = (
    "ward",
    "district",
    "province",
    "ward_code",
    "district_code",
    "province_code",
    "address_level2",
    "ward_level2",
    "province_level2",
    "ward_code_level2",
    "province_code_level2",
    "product_raw",
    "discount_amount",
    "booking_voucher_discount",
    "other_price",
    "result",
    "feedback",
    "shipping_fee",
    "old_id",
    "channel_owner",
    "url_page",
    "external_order_id",
    "order_code",
    "invoice_id",
    "latest_kiotviet_job_action",
    "fulfillment_warehouse_name",
)


class OrderListSerializer(OrderSerializer):
    """Bản gọn của OrderSerializer cho các trang danh sách đơn.

    Pop field trong __init__ thay vì khai Meta.fields riêng: DRF không cho subclass
    bỏ field đã declare khỏi Meta.fields, còn pop khỏi ``self.fields`` thì field bị
    loại khỏi cả vòng to_representation (tiết kiệm CPU thật) lẫn schema OpenAPI.
    Với many=True child chỉ bind một lần nên pop cũng chỉ chạy một lần mỗi trang.
    """

    # Cảnh báo trùng SĐT chỉ tồn tại ở payload LIST, cố ý KHÔNG có ở
    # OrderSerializer đầy đủ: response PATCH được FE merge đè vào row đang hiển
    # thị, nên một field rỗng ở đó sẽ xoá cảnh báo khỏi bảng sau mỗi lần sửa
    # inline. Vắng key = FE giữ nguyên giá trị cũ.
    duplicate_order_ids = serializers.SerializerMethodField()
    returning_order_ids = serializers.SerializerMethodField()

    class Meta(OrderSerializer.Meta):
        fields = OrderSerializer.Meta.fields + (
            "duplicate_order_ids",
            "returning_order_ids",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in LIST_EXCLUDED_ORDER_FIELDS:
            self.fields.pop(field_name, None)

    def get_duplicate_order_ids(self, obj) -> list[int]:
        """Id các đơn khớp ĐANG VẬN CHUYỂN (cùng SĐT + cùng sản phẩm, ≤30 ngày).

        Map dựng sẵn MỘT lần cho cả trang trong `_build_order_list_context`
        (xem `order_duplicate_warning_service`); ở đây chỉ tra cứu — KHÔNG tự
        query, để một chỗ nào đó serialize many=True thiếu context không biến
        thành N+1.
        """
        warning = self.context.get("duplicate_map", {}).get(obj.id)
        return list(warning.shipping) if warning else []

    def get_returning_order_ids(self, obj) -> list[int]:
        """Id các đơn khớp ĐANG/ĐÃ HOÀN — cảnh báo nguy cơ bom hàng, khác nghiệp
        vụ với `duplicate_order_ids` nên tách hẳn field thay vì gộp một danh sách."""
        warning = self.context.get("duplicate_map", {}).get(obj.id)
        return list(warning.returning) if warning else []

