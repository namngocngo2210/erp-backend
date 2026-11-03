from rest_framework import serializers

from apps.channels_core.models import Channel
from apps.customers.services import normalize_phone
# Nợ phân tầng transport→kiotviet (khối B .importlinter): trước đây tên này chảy
# qua chuỗi star-import, nay import thẳng.
from apps.kiotviet.models import KiotVietChannel
from apps.orders.address_catalog import normalize_codes
from apps.orders.models import OrderSource, OrderStatus, OrderType, OrderVat
from apps.users.models import User
from apps.warehouses.models import Warehouse

from .kiotviet_landing_serializers import _extract_kiotviet_branch_pool, _normalize_branch_identifier
from .order_core_serializers import OrderCRMInputSerializer
from .product_item_crm_serializers import OrderItemInputSerializer

class OrderCreateSerializer(serializers.Serializer):
    channel_code = serializers.SlugRelatedField(slug_field="code", queryset=Channel.objects.filter(deleted_at__isnull=True), source="channel")
    name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=30)
    # Mã KH (chỉ dùng cho import KOC). Nếu có và khớp Customer.code thì gắn đơn vào KH đó.
    customer_code = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    raw_address = serializers.CharField(required=False, allow_blank=True, default="")
    address = serializers.CharField(required=False, allow_blank=True, default="")
    product = serializers.CharField(required=False, allow_blank=True, default="")
    product_raw = serializers.CharField(required=False, allow_blank=True, default="")
    created_at_override = serializers.DateTimeField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=OrderStatus.choices, required=False, default=OrderStatus.NEW)
    discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    booking_voucher_discount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    weight = serializers.DecimalField(max_digits=10, decimal_places=3, required=False, default=0)
    dimensions = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    branch_id = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    order_type = serializers.ChoiceField(choices=OrderType.choices, required=False, default=OrderType.NORMAL)
    order_source = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    vat = serializers.ChoiceField(choices=OrderVat.choices, required=False, default=OrderVat.NO)
    # MỘT mã thu hồi (không phải chuỗi phẩy N mã như inline edit) — luồng tạo đơn
    # tạo đúng một OrderRecallEntry với mã này (Task 2 ruling). max_length=64 khớp
    # OrderRecallEntry.recall_code, hạ từ 1024 (dành cho chuỗi phẩy N mã cũ).
    recall_code = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        default="",
        help_text="Một mã đơn thu hồi.",
    )
    show_orders = serializers.BooleanField(required=False, default=True)
    other_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    customer_prepaid = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    result = serializers.CharField(required=False, allow_blank=True, default="")
    order_code = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    invoice_code = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    invoice_id = serializers.IntegerField(required=False, allow_null=True)
    feedback = serializers.CharField(required=False, allow_blank=True, default="")
    total_after_discount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    shipping_fee = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    payment_method = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    shipping_code = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    sku = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    quantity = serializers.IntegerField(min_value=0, required=False)
    items = OrderItemInputSerializer(many=True, required=False, default=list)
    channel_owner = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    url_page = serializers.URLField(required=False, allow_blank=True, default="")
    kiotviet_channel_id = serializers.PrimaryKeyRelatedField(
        source="kiotviet_channel",
        queryset=KiotVietChannel.objects.filter(deleted_at__isnull=True),
        required=False,
        allow_null=True,
    )
    staff_id = serializers.PrimaryKeyRelatedField(
        source="staff",
        # NOTE: loại admin (role hệ thống) thay cho filter role=STAFF cũ.
        queryset=User.objects.filter(is_active=True, deleted_at__isnull=True)
        .exclude(role_assignments__role__is_system=True)
        .distinct(),
        required=False,
        allow_null=True,
    )
    # Checkbox "Chia đơn tự động" trên form tạo đơn: bỏ trống staff và để
    # LeadAssignmentService chia theo trọng số (ngày, ca, sheet) đã cấu hình.
    # Default False ở BE là CỐ Ý: import Excel / KiotViet / client cũ không gửi
    # field này và phải giữ nguyên hành vi, chỉ form mới bật cờ.
    auto_assign_staff = serializers.BooleanField(required=False, default=False)
    ward_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    district_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    province_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    customer_gender = serializers.IntegerField(required=False, allow_null=True)
    crm = OrderCRMInputSerializer(required=False, allow_null=True)
    # Kho xuất chọn ngay ở form tạo đơn. Thiếu field này thì DRF nuốt im lặng giá trị
    # FE gửi lên → đơn luôn null kho, và bước chuyển "Đã xuất kho" (đường xuất kho tay
    # của đơn bán trực tiếp, không quét) bị chặn vì `update_order_inline` đòi có kho.
    # KHÔNG trừ tồn ở đây: gán kho chỉ là chọn nguồn xuất, tồn chỉ trừ khi đơn sang
    # trạng thái giao vận "dispatched".
    fulfillment_warehouse = serializers.PrimaryKeyRelatedField(
        queryset=Warehouse.objects.filter(deleted_at__isnull=True),
        required=False,
        allow_null=True,
    )

    def validate_phone(self, value: str) -> str:
        normalized = normalize_phone(value)
        if not normalized:
            raise serializers.ValidationError("Số điện thoại không hợp lệ.")
        return normalized

    def validate_order_source(self, value):
        return OrderSource.normalize(value)

    def validate(self, attrs):
        province_code = str(attrs.get("province_code") or "").strip()
        district_code = str(attrs.get("district_code") or "").strip()
        ward_code = str(attrs.get("ward_code") or "").strip()
        if province_code or district_code or ward_code:
            mapped = normalize_codes(province_code, district_code, ward_code)
            attrs["province_code"] = mapped["province_code"]
            attrs["district_code"] = mapped["district_code"]
            attrs["ward_code"] = mapped["ward_code"]
            attrs["province"] = mapped["province"]
            attrs["district"] = mapped["district"]
            attrs["ward"] = mapped["ward"]

        branch_id = _normalize_branch_identifier(attrs.get("branch_id"))
        if branch_id:
            attrs["branch_id"] = branch_id
        kiotviet = attrs.get("kiotviet_channel")
        if branch_id and kiotviet is not None:
            branch_pool = _extract_kiotviet_branch_pool(kiotviet)
            if branch_pool and branch_id not in branch_pool:
                raise serializers.ValidationError({"branch_id": "Branch ID không thuộc kênh KiotViet đã chọn."})

        items = attrs.get("items") or []
        if items:
            normalized_items = []
            for item in items:
                normalized_item = dict(item)
                if "sku" in normalized_item:
                    normalized_item["sku"] = str(normalized_item.get("sku") or "").strip()
                normalized_items.append(normalized_item)
            attrs["items"] = normalized_items

        return attrs


class OrderBulkDeleteSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False)

    def validate_ids(self, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class OrderKiotVietActionSerializer(serializers.Serializer):
    ACTION_PUSH_SELECTED = "push_selected"
    ACTION_PUSH_UNSENT = "push_unsent"
    ACTION_SYNC_SHIPPING_SELECTED = "sync_shipping_selected"
    ACTION_SYNC_STATUS_SELECTED = "sync_status_selected"
    ACTION_SYNC_STATUS_HAS_SHIPPING = "sync_status_has_shipping"
    ACTION_QUICK_SALE_SELECTED = "quick_sale_selected"
    ACTION_QUICK_SALE_DEFERRED_SELECTED = "quick_sale_deferred_selected"
    STEP_PRECHECK = "precheck"
    STEP_EXECUTE = "execute"

    action = serializers.ChoiceField(
        choices=[
            ACTION_PUSH_SELECTED,
            ACTION_PUSH_UNSENT,
            ACTION_SYNC_SHIPPING_SELECTED,
            ACTION_SYNC_STATUS_SELECTED,
            ACTION_SYNC_STATUS_HAS_SHIPPING,
            ACTION_QUICK_SALE_SELECTED,
            ACTION_QUICK_SALE_DEFERRED_SELECTED,
        ]
    )
    step = serializers.ChoiceField(
        choices=[STEP_PRECHECK, STEP_EXECUTE],
        required=False,
        default=STEP_EXECUTE,
    )
    ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=True,
        default=list,
    )
    order_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=True,
        default=list,
    )

    def validate_ids(self, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))

    def validate_order_ids(self, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))

    def validate(self, attrs):
        attrs = super().validate(attrs)
        action = attrs["action"]
        step = attrs["step"]
        ids = attrs.get("ids", [])
        order_ids = attrs.get("order_ids", [])
        is_push_action = action in {
            self.ACTION_PUSH_SELECTED,
            self.ACTION_PUSH_UNSENT,
        }

        if step == self.STEP_PRECHECK and not is_push_action:
            raise serializers.ValidationError({"step": "Thao tác này chỉ hỗ trợ step=execute."})

        if step == self.STEP_PRECHECK and action == self.ACTION_PUSH_SELECTED and not ids:
            raise serializers.ValidationError({"ids": "Vui lòng chọn ít nhất 1 đơn hàng."})

        if step == self.STEP_EXECUTE and action == self.ACTION_PUSH_SELECTED and not (order_ids or ids):
            raise serializers.ValidationError({"ids": "Vui lòng chọn ít nhất 1 đơn hàng."})

        if action in {
            self.ACTION_PUSH_SELECTED,
            self.ACTION_SYNC_SHIPPING_SELECTED,
            self.ACTION_SYNC_STATUS_SELECTED,
            self.ACTION_QUICK_SALE_SELECTED,
            self.ACTION_QUICK_SALE_DEFERRED_SELECTED,
        } and not ids and not order_ids:
            raise serializers.ValidationError({"ids": "Vui lòng chọn ít nhất 1 đơn hàng."})
        return attrs


class OrderKshipOptionsSerializer(serializers.Serializer):
    ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=True,
        allow_empty=False,
    )

    def validate_ids(self, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class OrderCreateKshipDeliverySerializer(serializers.Serializer):
    ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=True,
        allow_empty=False,
    )
    provider_code = serializers.CharField(required=True, allow_blank=False, max_length=50)
    service_code = serializers.CharField(required=True, allow_blank=False, max_length=50)
    notes = serializers.DictField(child=serializers.CharField(allow_blank=True, max_length=500), required=False, default=dict)
    # Quy định xem hàng theo TỪNG đơn. Cố ý không whitelist mã ở đây: bảng mã thuộc về
    # apps.kiotviet, orders chỉ được đi qua port (ràng buộc .importlinter). Mã lạ sẽ bị
    # payload builder loại và rơi về mặc định.
    shipper_notes = serializers.DictField(
        child=serializers.CharField(allow_blank=True, max_length=32), required=False, default=dict
    )
    # Lựa chọn cho SERVICE_EXTRA hãng bắt buộc ({"PickShift": "<ca lấy hàng>"}), chung
    # cho cả lô vì provider/service cũng gửi ở mức lô. Không whitelist code/giá trị ở
    # đây: danh sách hợp lệ do KShip trả về và đổi theo ngày — gateway đối chiếu lại
    # ngay lúc tạo vận đơn, giá trị lạ bị bỏ và rơi về lựa chọn đầu tiên.
    service_extra = serializers.DictField(
        child=serializers.CharField(allow_blank=True, max_length=100), required=False, default=dict
    )
    sender_address_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def validate_ids(self, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))
