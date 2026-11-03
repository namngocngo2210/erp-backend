import re

from rest_framework import serializers

from apps.channels_core.models import Channel
from apps.common.masking import mask_keep_last
from apps.customers.services import normalize_phone
from apps.users.phone_visibility import strip_masked_phone
# Nợ phân tầng transport→kiotviet (khối B .importlinter): trước đây tên này chảy
# từ product_item_crm_serializers qua chuỗi star-import, nay import thẳng.
from apps.kiotviet.models import KiotVietChannel
from apps.orders.address_catalog import normalize_codes
from apps.orders.models import (
    Order,
    OrderKiotVietJob,
    OrderSource,
    OrderStatus,
    OrderType,
    OrderVat,
)
from apps.users.field_policy import FieldPolicyWriteMixin
from apps.users.models import User

from .order_core_serializers import OrderCRMInputSerializer
from .product_item_crm_serializers import OrderItemInputSerializer

_URL_SPLIT_PATTERN = re.compile(r"(?=https?://)")


def _parse_link_list(raw) -> list[str]:
    """Split a raw link string into individual URLs.

    Landing payloads sometimes concatenate multiple URLs without delimiter
    ("https://a.comhttps://b.com"). Split on the "http(s)://" boundary and
    strip trailing punctuation/whitespace. Returns [] for empty input.
    """
    text = str(raw or "").strip()
    if not text:
        return []
    parts = _URL_SPLIT_PATTERN.split(text)
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = part.strip().strip(",").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _normalize_branch_identifier(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.strip("{}[]()")
    text = text.strip("'").strip('"').strip()
    if text.endswith(".0"):
        integer_part = text.split(".", 1)[0].strip()
        if integer_part.lstrip("+-").isdigit():
            return integer_part
    return text


def _extract_kiotviet_branch_pool(kiotviet) -> list[str]:
    branch_pool: list[str] = []
    raw_branch_ids = kiotviet.branch_ids or []
    if isinstance(raw_branch_ids, str):
        raw_branch_ids = [raw_branch_ids]
    for raw_item in raw_branch_ids:
        raw_text = str(raw_item or "").strip()
        if not raw_text:
            continue
        normalized_raw = raw_text.strip("{}[]()")
        parts = [normalized_raw]
        if "," in normalized_raw:
            parts = [part.strip() for part in normalized_raw.split(",")]
        for part in parts:
            normalized_part = _normalize_branch_identifier(part)
            if normalized_part:
                branch_pool.append(normalized_part)
    if not branch_pool:
        fallback = _normalize_branch_identifier(kiotviet.branch_id)
        if fallback:
            branch_pool = [fallback]
    return list(dict.fromkeys(branch_pool))


class OrderKiotVietJobSerializer(serializers.ModelSerializer):
    order_name = serializers.CharField(source="order.name", read_only=True)
    # SĐT MASK giữ 3 số cuối (khuôn `flow_history_views` cho màn tra cứu đơn của kho).
    #
    # Vì sao mask thay vì trả thô: `list_kiotviet_jobs` trước đây ép
    # `filters["requested_by"] = request.user.id` cho mọi non-superuser nên user chỉ
    # thấy job DO CHÍNH MÌNH bấm. Fix T3 thay bằng record-scope thật (trưởng nhóm xem
    # được job của nhân viên — đúng nghiệp vụ) nhưng làm mất lớp chắn PII đi kèm, trong
    # khi endpoint KHÔNG áp field_policy.
    #
    # Không dùng field_policy ở đây: `drop_hidden_fields` khớp theo TÊN KEY output, mà
    # key ở serializer này là `order_phone` chứ không phải `phone` — một rule
    # `{"orders": {"phone": "hidden"}}` sẽ im lặng không có tác dụng (đúng kiểu fail-open
    # vừa bịt ở I4), và field_policy vốn là cấu hình opt-in nên mặc định vẫn rò.
    # Màn này là log vận hành: SĐT chỉ để ĐỐI SOÁT đơn nào, 3 số cuối + tên + mã đơn/
    # hoá đơn là đủ; không có UI nào đang hiển thị `order_phone`.
    order_phone = serializers.SerializerMethodField()
    order_channel_code = serializers.CharField(source="order.channel.code", read_only=True)
    order_external_id = serializers.CharField(source="order.external_order_id", read_only=True)
    order_order_code = serializers.CharField(source="order.order_code", read_only=True)
    order_invoice_code = serializers.CharField(source="order.invoice_code", read_only=True)
    order_invoice_id = serializers.IntegerField(source="order.invoice_id", read_only=True, allow_null=True)
    order_old_invoice_code = serializers.CharField(source="order.old_invoice_code", read_only=True)
    order_old_id = serializers.IntegerField(source="order.old_id", read_only=True, allow_null=True)
    order_shipping_code = serializers.CharField(source="order.shipping_code", read_only=True)
    requested_by_id = serializers.IntegerField(source="requested_by.id", read_only=True, allow_null=True)
    requested_by_username = serializers.SerializerMethodField()
    requested_by_display_name = serializers.SerializerMethodField()

    class Meta:
        model = OrderKiotVietJob
        fields = (
            "id",
            "order_id",
            "order_name",
            "order_phone",
            "order_channel_code",
            "order_external_id",
            "order_order_code",
            "order_invoice_code",
            "order_invoice_id",
            "order_old_invoice_code",
            "order_old_id",
            "order_shipping_code",
            "action",
            "status",
            "requested_by_id",
            "requested_by_username",
            "requested_by_display_name",
            "attempt_count",
            "error_message",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
        )

    def get_order_phone(self, obj) -> str:
        order = getattr(obj, "order", None)
        return mask_keep_last(getattr(order, "phone", "") or "")

    def get_requested_by_username(self, obj) -> str | None:
        requested_by = getattr(obj, "requested_by", None)
        if not requested_by:
            return None
        return requested_by.username

    def get_requested_by_display_name(self, obj) -> str | None:
        requested_by = getattr(obj, "requested_by", None)
        if not requested_by:
            return None
        return requested_by.display_name or requested_by.username


class _ProductIdField(serializers.Field):
    """Accepts int (Product.id) or string "SKU" / "SKU1,SKU2" (Product.sku lookup).

    Preserves the JSON type so the service layer can distinguish id vs sku lookup.
    """

    def to_internal_value(self, data):
        if data is None:
            return None
        if isinstance(data, bool):
            raise serializers.ValidationError("product_id phải là số nguyên hoặc chuỗi SKU.")
        if isinstance(data, int):
            return data
        if isinstance(data, str):
            stripped = data.strip()
            return stripped or None
        raise serializers.ValidationError("product_id phải là số nguyên hoặc chuỗi SKU.")

    def to_representation(self, value):
        return value


class _ChannelRefField(serializers.Field):
    """Accepts int (Channel.id) hoặc chuỗi text (tên/mã kênh).

    Giữ nguyên kiểu JSON để view phân biệt resolve theo id vs theo code.
    """

    def to_internal_value(self, data):
        if data is None or isinstance(data, bool):
            raise serializers.ValidationError("Kênh bán không hợp lệ.")
        if isinstance(data, int):
            return data
        if isinstance(data, str):
            stripped = data.strip()
            if not stripped:
                raise serializers.ValidationError("Kênh bán không được để trống.")
            return stripped
        raise serializers.ValidationError("Kênh bán phải là số ID hoặc tên kênh.")

    def to_representation(self, value):
        return value


class LandingOrderSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    address = serializers.CharField(required=False, allow_blank=True, default="")
    product = serializers.CharField(required=False, allow_blank=True, default="")
    product_id = _ProductIdField(required=False, allow_null=True)
    channel_id = _ChannelRefField()
    quantity = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    total_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True, min_value=0
    )
    # Tiền do bên gửi chốt. Cả ba ô đều optional: gửi ô nào áp ô đó, thiếu thì
    # `create_order_from_landing` tính bù. KHÔNG khai `default` — phải phân biệt
    # được "không gửi" (tính bù) với "gửi số 0" (chốt bằng 0).
    discount_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True, min_value=0
    )
    total_after_discount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True, min_value=0
    )
    link = serializers.CharField(required=False, allow_blank=True, default="")
    created_at = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    order_source = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    # Sale phụ trách do bên gửi chỉ định. CHỈ có hiệu lực với đơn nguồn khác
    # "landing" — xem `validate()`. KHÔNG khai max_length ở field: field này là
    # best-effort (đồng bộ lark-ingest, sai tên không đáng mất lead) nên tên quá
    # dài phải bị CẮT ở validate(), không được làm is_valid() = False và làm mất
    # cả đơn — 150 khớp `AbstractUser.username` chỉ còn là độ dài cắt.
    staff_username = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def to_internal_value(self, data):
        if hasattr(data, "copy"):
            try:
                data = data.copy()
            except Exception:
                data = dict(data)
        if "product" in data and isinstance(data["product"], list):
            data["product"] = data["product"][0] if data["product"] else ""
        return super().to_internal_value(data)

    def validate_phone(self, value: str) -> str:
        # Accept empty phone to allow partial ingest payloads. When provided,
        # normalize best-effort; invalid digits become empty rather than rejecting.
        if not value:
            return ""
        return normalize_phone(value) or ""

    def validate(self, attrs):
        # Giảm giá vượt tổng tiền chỉ chặn được khi bên gửi chốt CẢ tổng tiền:
        # thiếu `total_amount` thì tổng phải chờ task chạy nền suy ra từ dòng
        # hàng/bảng giá, lúc đó đã trả 200 nên không còn đường 400 (service kẹp
        # về 0 + log). Có `total_after_discount` tường minh thì bỏ qua kiểm tra:
        # bên gửi đã tự chốt số cuối, CRM không phán xét bộ ba của họ.
        total_amount = attrs.get("total_amount")
        discount_amount = attrs.get("discount_amount")
        if (
            discount_amount is not None
            and total_amount is not None
            and attrs.get("total_after_discount") is None
            and discount_amount > total_amount
        ):
            raise serializers.ValidationError(
                {"discount_amount": "Giảm giá không được lớn hơn tổng tiền."}
            )

        link_list = _parse_link_list(attrs.pop("link", ""))
        attrs["url_page"] = ",".join(link_list)
        attrs["url_page_list"] = link_list
        attrs["raw_address"] = attrs.pop("address", "") or ""

        # Lazy import to avoid circular dependency: import_landing_excel imports
        # OrderCreateSerializer from this serializer package.
        from apps.orders.import_landing_excel import _parse_created_at

        raw_created_at = attrs.pop("created_at", "")
        try:
            created_at_value = _parse_created_at(raw_created_at)
        except ValueError as exc:
            raise serializers.ValidationError({"created_at": str(exc)})
        if created_at_value is not None:
            attrs["created_at_override"] = created_at_value

        # Luồng landing: thiếu/không hợp lệ → default "landing" (đồng bộ Lark/Excel).
        attrs["order_source"] = OrderSource.normalize(
            attrs.get("order_source"), default=OrderSource.LANDING
        )

        # Chỉ định sale chỉ dành cho đơn nguồn KHÁC landing: đơn từ landing page
        # luôn phải đi qua bộ chia tự động. `normalize` phía trên đã gộp cả ba ca
        # "thiếu field" / "rỗng" / "giá trị lạ" về "landing", nên một phép so sánh
        # ở đây là đủ — không cần đọc lại giá trị thô. Key bị bỏ hẳn (không phải
        # để rỗng) để tầng dưới chỉ cần kiểm tra sự tồn tại.
        # Cắt còn 150 ký tự thay vì để CharField từ chối (max_length) — sai/dài
        # tên sale là lỗi của bên gửi, không đáng để 400 cả payload và mất đơn.
        staff_username = str(attrs.pop("staff_username", "") or "").strip()[:150]
        if staff_username and attrs["order_source"] != OrderSource.LANDING:
            attrs["staff_username"] = staff_username
        return attrs


class OrderRecallInputSerializer(serializers.Serializer):
    # Contract giữ dạng chuỗi phẩy nhưng chứa N mã (mỗi mã <=64 ký tự, cắt ở
    # _parse_slip_codes) — 1024 đủ rộng cho danh sách mã dài.
    return_slip_code = serializers.CharField(max_length=1024, required=False, allow_blank=True, allow_null=True)


class OrderRecallEntryInputSerializer(serializers.Serializer):
    """Một nhóm thu hồi. Cả 3 ô đều optional vì nhân viên nhập dần: gõ mã hôm nay,
    điền ngày nhận vài hôm sau."""

    recall_code = serializers.CharField(
        max_length=64, required=False, allow_blank=True, allow_null=True
    )
    recall_created_date = serializers.DateField(required=False, allow_null=True)
    recall_received_date = serializers.DateField(required=False, allow_null=True)


class OrderInlineUpdateSerializer(FieldPolicyWriteMixin, serializers.ModelSerializer):
    # field_policy (từ context) strip field hidden|readonly khỏi payload ghi (mặc định
    # strip + log, không 403). Default role không có field rule → passthrough.
    field_section = "orders"

    channel_code = serializers.SlugRelatedField(
        slug_field="code",
        source="channel",
        queryset=Channel.objects.filter(deleted_at__isnull=True),
        required=False,
    )
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
    ward_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    district_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    province_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    items = OrderItemInputSerializer(many=True, required=False)
    customer_gender = serializers.IntegerField(required=False, allow_null=True)
    invoice_id = serializers.IntegerField(required=False, allow_null=True)
    crm = OrderCRMInputSerializer(required=False, allow_null=True)
    recall = OrderRecallInputSerializer(required=False, allow_null=True)
    # many=True + full-replace, cùng khuôn `items` ngay trên.
    recall_entries = OrderRecallEntryInputSerializer(many=True, required=False)
    # `created_at` is `auto_now_add=True` on the model, which makes it
    # non-editable through ModelSerializer fields by default. Declare it
    # explicitly so admins/sales can backfill or correct the order date
    # (e.g. import-after-the-fact).
    created_at = serializers.DateTimeField(required=False)

    class Meta:
        model = Order
        fields = (
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
            "other_price",
            "customer_prepaid",
            "result",
            "order_code",
            "invoice_code",
            "invoice_id",
            "feedback",
            "customer_return",
            "total_after_discount",
            "shipping_fee",
            "payment_method",
            "shipping_code",
            "total_amount",
            "channel_code",
            "kiotviet_channel_id",
            "staff_id",
            "name",
            "phone",
            "raw_address",
            "address",
            "ward",
            "district",
            "province",
            "ward_code",
            "district_code",
            "province_code",
            "kiotviet_location",
            "kiotviet_ward",
            # Chỉ nhận chuỗi 2 cấp sale sửa tay; 4 cột tỉnh/phường/mã 2 cấp còn lại
            # service tự suy (FE gửi kèm để preview thì bị bỏ qua).
            "address_level2",
            "sku",
            "quantity",
            "items",
            "customer_gender",
            "crm",
            "recall",
            "recall_entries",
            "created_at",
            "fulfillment_warehouse",
        )
        extra_kwargs = {
            "fulfillment_warehouse": {"required": False, "allow_null": True},
        }

    def to_internal_value(self, data):
        # Bỏ `phone` nếu client gửi lên bản ĐÃ CHE (091****678): sale không có quyền
        # `customer-phone:view` mở form sửa đơn của sale khác thì ô SĐT mang sẵn chuỗi
        # che, mọi lần lưu (dù chỉ sửa địa chỉ) sẽ gửi nó lên. Không strip thì
        # `validate_phone` trả 400 "Số điện thoại không hợp lệ." giữa luồng sửa đơn —
        # và nếu một ngày validate nới ra thì thành GHI ĐÈ hỏng số gốc.
        return super().to_internal_value(strip_masked_phone(dict(data)))

    def validate_status(self, value):
        if value not in OrderStatus.values:
            raise serializers.ValidationError("Trạng thái không hợp lệ")
        return value

    def validate_order_type(self, value):
        if value not in OrderType.values:
            raise serializers.ValidationError("Loại đơn không hợp lệ")
        return value

    def validate_order_source(self, value):
        return OrderSource.normalize(value)

    def validate_vat(self, value):
        if value not in OrderVat.values:
            raise serializers.ValidationError("Hình thức xuất VAT không hợp lệ")
        return value

    def validate_phone(self, value: str) -> str:
        normalized = normalize_phone(value)
        if not normalized:
            raise serializers.ValidationError("Số điện thoại không hợp lệ.")
        return normalized

    def validate(self, attrs):
        attrs = super().validate(attrs)
        order = self.instance
        code_fields = {"province_code", "district_code", "ward_code"}
        has_code_update = any(field in attrs for field in code_fields)
        if order is not None and has_code_update:
            province_code = attrs.get("province_code", order.province_code)
            district_code = attrs.get("district_code", order.district_code)
            ward_code = attrs.get("ward_code", order.ward_code)

            if "province_code" in attrs and "district_code" not in attrs:
                district_code = ""
            if ("province_code" in attrs or "district_code" in attrs) and "ward_code" not in attrs:
                ward_code = ""

            mapped = normalize_codes(
                str(province_code or "").strip(),
                str(district_code or "").strip(),
                str(ward_code or "").strip(),
            )
            attrs["province_code"] = mapped["province_code"]
            attrs["district_code"] = mapped["district_code"]
            attrs["ward_code"] = mapped["ward_code"]
            attrs["province"] = mapped["province"]
            attrs["district"] = mapped["district"]
            attrs["ward"] = mapped["ward"]

        branch_id = _normalize_branch_identifier(attrs.get("branch_id"))
        if branch_id:
            attrs["branch_id"] = branch_id
        kiotviet = attrs.get("kiotviet_channel") if "kiotviet_channel" in attrs else (order.kiotviet_channel if order else None)
        if branch_id and kiotviet is not None:
            branch_pool = _extract_kiotviet_branch_pool(kiotviet)
            if branch_pool and branch_id not in branch_pool:
                raise serializers.ValidationError({"branch_id": "Branch ID không thuộc kênh KiotViet đã chọn."})

        return attrs
