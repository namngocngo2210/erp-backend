from decimal import Decimal, InvalidOperation

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

# 2026-08-20: gỡ chuỗi star-import — `KiotVietChannel` nay được import THẲNG ở
# `kiotviet_landing_serializers` và `order_action_serializers` (2 nơi dùng nó),
# nên import tại đây đã xoá được thật. Nợ phân tầng chuyển theo trong khối B
# .importlinter (1 entry cũ tách thành 2).
from apps.users.models import User
from apps.channels_core.models import Channel
from apps.customers.services import normalize_phone

# Mọi ORM-write/transaction của 3 cặp create/update trong file này nằm ở service —
# serializer chỉ validate + shape (audit backend-architecture 4.6; lưới chặn tái
# phát: apps/users/tests/test_serializer_orm_write_guard.py).
from apps.orders.application.services import catalog_write_service

from apps.orders.address_catalog import normalize_codes
from apps.orders.assessment_fields import ASSESSMENT_FIELDS
from apps.orders.crm_catalog import (
    BOMB_REASON_GROUP_LABELS,
    BOMB_REASON_TYPE_GROUP_MAP,
    BOMB_REASON_TYPE_LABELS,
    CRM_CODE_LABELS,
    DELIVERY_BUSINESS_STATUS_LABELS,
    EVENT_TYPE_LABELS,
    POTENTIAL_LEVEL_LABELS,
    build_crm_catalog_payload,
)
from apps.orders.models import (
    NeedType,
    Order,
    OrderAssessment,
    OrderItem,
    OrderJourneyEvent,
    OrderKiotVietJob,
    OrderNeed,
    OrderSource,
    OrderStatus,
    OrderType,
    OrderVat,
)
from apps.catalog.models import PriceBook, Product, ProductCategory, ProductGroup, ProductPackage


class PriceBookSerializer(serializers.ModelSerializer):
    # Khai tường minh để DRF không tự gắn UniqueValidator từ partial unique constraint
    # (validator sẽ chặn trước khi logic hạ cờ ở update kịp chạy) — cùng lý do với
    # ProductCategorySerializer.is_loyal_default.
    is_default = serializers.BooleanField(required=False)

    class Meta:
        model = PriceBook
        fields = ("id", "code", "name", "is_default", "sort_order", "is_active", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_code(self, value: str) -> str:
        text = str(value or "").strip().lower()
        if not text:
            raise serializers.ValidationError("Mã bảng giá không được rỗng.")
        qs = PriceBook.objects.filter(code=text, deleted_at__isnull=True)
        if self.instance is not None:
            qs = qs.exclude(id=self.instance.id)
        if qs.exists():
            raise serializers.ValidationError(f"Mã bảng giá '{text}' đã tồn tại.")
        return text

    def validate(self, attrs):
        """Chặn PATCH làm hệ thống mất bảng giá mặc định.

        DELETE đã có guard tương đương (không cho xoá bảng mặc định) vì nó là đích
        fallback của mọi bảng khác (``price_book_service.default_price_book`` /
        ``_active_books`` lọc ``is_active=True``). PATCH đi thẳng qua serializer
        này với ``partial=True`` nên phải tự chặn ở đây — cùng tinh thần với message
        chặn DELETE, không để lộ hệ quả (catalog trả giá 0) rồi mới biết.
        """
        attrs = super().validate(attrs)
        if self.instance is not None:
            # Đang là mặc định mà request này bỏ cờ mặc định (không kèm việc đặt bảng
            # khác làm mặc định — đó là một request PATCH khác, tự nó đã demote bảng
            # này qua _demote_other_defaults) → chặn thẳng, không cho hệ thống rơi vào
            # trạng thái không có bảng mặc định nào.
            if self.instance.is_default and "is_default" in attrs and not attrs["is_default"]:
                raise serializers.ValidationError(
                    {
                        "is_default": (
                            "Không thể bỏ cờ mặc định của bảng giá đang là mặc định. "
                            "Hãy đặt một bảng giá khác làm mặc định trước."
                        )
                    }
                )
            resolved_is_default = attrs.get("is_default", self.instance.is_default)
            resolved_is_active = attrs.get("is_active", self.instance.is_active)
            if resolved_is_default and not resolved_is_active:
                raise serializers.ValidationError(
                    {
                        "is_active": (
                            "Không thể tắt bảng giá đang là mặc định — tắt là hết bảng "
                            "fallback cho sản phẩm chưa khai giá ở nền tảng đang chọn. "
                            "Hãy đặt một bảng giá khác làm mặc định trước."
                        )
                    }
                )
        return attrs

    def create(self, validated_data):
        return catalog_write_service.create_price_book(validated_data)

    def update(self, instance, validated_data):
        return catalog_write_service.update_price_book(instance, validated_data)


class ProductPackageSerializer(serializers.ModelSerializer):
    # Map code bảng giá -> giá, dữ liệu THÔ (không fallback). Vắng key = nền tảng đó
    # chưa khai giá; FE hiển thị "kế thừa từ bảng mặc định". Nếu fallback ở đây thì
    # trang quản trị không phân biệt được "khai trùng giá" với "chưa khai".
    prices = serializers.DictField(child=serializers.CharField(), required=False)

    class Meta:
        model = ProductPackage
        fields = ("id", "quantity", "price", "prices", "weight", "length", "width", "height")
        read_only_fields = ("id", "price")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["prices"] = {
            row.price_book.code: str(row.price)
            for row in instance.book_prices.all()
            if row.price_book.deleted_at is None
        }
        return data


class ProductGroupRefSerializer(serializers.ModelSerializer):
    """Compact group reference nested inside a product payload."""

    class Meta:
        model = ProductGroup
        fields = ("id", "code", "name")


class ProductSerializer(serializers.ModelSerializer):
    packages = ProductPackageSerializer(many=True, required=False)
    group = ProductGroupRefSerializer(read_only=True)
    group_id = serializers.PrimaryKeyRelatedField(
        queryset=ProductGroup.objects.filter(deleted_at__isnull=True),
        source="group",
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "short_name",
            "sku",
            "price",
            "weight",
            "length",
            "width",
            "height",
            "is_active",
            "pancake_sync_mode",
            "group",
            "group_id",
            "packages",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    @staticmethod
    def _default_book_code() -> str:
        """Code bảng giá mặc định — điều kiện bắt buộc-có-giá khi validate packages.

        Logic (kèm fallback "facebook" cho cửa sổ bootstrap/rollback) nằm ở
        ``catalog_write_service.default_price_book_code`` để validate ở đây và
        phần ghi ở service luôn dùng CÙNG một tiêu chí.
        """
        return catalog_write_service.default_price_book_code()

    @staticmethod
    def _parse_price(raw, *, qty: int, code: str) -> Decimal:
        """Parse một giá trị giá thô (string từ `prices`) về Decimal.

        `prices` chỉ khai `DictField(child=CharField())` nên DRF không tự validate
        được đây có phải số hay không — nếu không bắt ở đây, giá trị sai định dạng
        (vd. "abc") sẽ làm `Decimal()` raise `InvalidOperation` không ai bắt ở tận
        `_replace_packages`, rơi thành 500 thay vì 400 (finding review Task 4).
        """
        text = str(raw).strip() if raw not in (None, "") else "0"
        try:
            return Decimal(text)
        except InvalidOperation as exc:
            raise serializers.ValidationError(
                {"packages": f"Giá '{raw}' ở bảng giá '{code}' của gói số lượng {qty} không phải là số hợp lệ."}
            ) from exc

    def _validate_packages(self, packages):
        if not packages:
            return packages
        default_code = self._default_book_code()
        seen_qty: set[int] = set()
        for pkg in packages:
            qty = int(pkg.get("quantity") or 0)
            if qty <= 0:
                raise serializers.ValidationError({"packages": "Số lượng package phải lớn hơn 0."})
            if qty in seen_qty:
                raise serializers.ValidationError({"packages": f"Trùng số lượng {qty} trong danh sách package."})
            seen_qty.add(qty)
            # Validate định dạng số cho MỌI bảng giá đã khai (không chỉ bảng mặc định) —
            # để lỗi định dạng luôn bị chặn ở đây, trước khi tới _replace_packages.
            prices = pkg.get("prices") or {}
            parsed_prices = {
                code: self._parse_price(raw, qty=qty, code=code) for code, raw in prices.items()
            }
            # Chỉ bảng giá MẶC ĐỊNH bắt buộc có giá; nền tảng khác để trống = kế thừa.
            if parsed_prices.get(default_code, Decimal("0")) <= 0:
                raise serializers.ValidationError(
                    {"packages": f"Gói số lượng {qty} thiếu giá ở bảng giá mặc định ({default_code})."}
                )
        if 1 not in seen_qty:
            raise serializers.ValidationError({"packages": "Bắt buộc có package quantity=1."})
        return packages

    def create(self, validated_data):
        packages = validated_data.pop("packages", None)
        explicit_group = "group" in validated_data
        if packages is None:
            # Không có mảng packages tường minh: suy ra gói qty=1 từ field base của
            # Product rồi CHẠY QUA CÙNG một _validate_packages như nhánh có packages.
            # Trước đây nhánh này bỏ qua validate hoàn toàn — Product.price có
            # default=0 nên có thể tạo được sản phẩm với giá 0 ở bảng mặc định,
            # phá đúng bất biến "bảng mặc định luôn phải có giá > 0" (finding review
            # Task 4). Validate chạy TRƯỚC super().create() nên không có row Product
            # mồ côi nào được ghi xuống DB nếu validation raise.
            packages = [
                {
                    "quantity": 1,
                    "prices": {self._default_book_code(): str(validated_data.get("price", 0))},
                    "weight": validated_data.get("weight", 0),
                    "length": validated_data.get("length", 0),
                    "width": validated_data.get("width", 0),
                    "height": validated_data.get("height", 0),
                }
            ]
        self._validate_packages(packages)
        return catalog_write_service.create_product(
            validated_data, packages=packages, explicit_group=explicit_group
        )

    def update(self, instance, validated_data):
        packages = validated_data.pop("packages", None)
        explicit_group = "group" in validated_data
        # The auto-group depends ONLY on the sku, so the service re-runs the hook
        # only when the sku is actually being changed (and the caller didn't set
        # group_id) — no unrelated side effect on a PATCH that just edits price etc.
        sku_changed = "sku" in validated_data
        if packages is not None:
            self._validate_packages(packages)
        return catalog_write_service.update_product(
            instance,
            validated_data,
            packages=packages,
            explicit_group=explicit_group,
            sku_changed=sku_changed,
        )


class ProductCategoryProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ("id", "name", "sku")


class ProductCategorySerializer(serializers.ModelSerializer):
    products = ProductCategoryProductSerializer(many=True, read_only=True)
    product_count = serializers.IntegerField(read_only=True)
    # Khai báo tường minh để DRF không tự gắn UniqueValidator từ partial unique constraint
    # (nó sẽ chặn ngay ở validation, trước khi logic hạ cờ ở create/update kịp chạy).
    # Tính duy nhất do catalog_write_service (hạ cờ các danh mục khác trong cùng
    # transaction) + partial unique index DB bảo đảm.
    is_loyal_default = serializers.BooleanField(required=False)
    product_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        write_only=True,
        required=False,
        queryset=Product.objects.filter(deleted_at__isnull=True),
    )

    class Meta:
        model = ProductCategory
        fields = (
            "id",
            "code",
            "name",
            "sort_order",
            "is_active",
            "is_loyal_default",
            "products",
            "product_count",
            "product_ids",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def create(self, validated_data):
        product_ids = validated_data.pop("product_ids", None)
        return catalog_write_service.create_product_category(
            validated_data, product_ids=product_ids
        )

    def update(self, instance, validated_data):
        product_ids = validated_data.pop("product_ids", None)
        return catalog_write_service.update_product_category(
            instance, validated_data, product_ids=product_ids
        )


class ProductGroupVariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ("id", "name", "sku", "price", "is_active")


class ProductGroupSerializer(serializers.ModelSerializer):
    variants = serializers.SerializerMethodField()
    variant_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ProductGroup
        fields = (
            "id",
            "code",
            "name",
            "image_url",
            "description",
            "is_active",
            "variants",
            "variant_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    @extend_schema_field(ProductGroupVariantSerializer(many=True))
    def get_variants(self, obj) -> list[dict]:
        # Only active (not soft-deleted) products belong to a group's variant list.
        products = [p for p in obj.products.all() if p.deleted_at is None]
        return ProductGroupVariantSerializer(products, many=True).data


class OrderItemSerializer(serializers.ModelSerializer):
    # Read product_id from the FK column directly to avoid lazily loading the related Product per item.
    product_id = serializers.IntegerField(read_only=True)
    product_name = serializers.CharField(read_only=True)
    sku = serializers.CharField(read_only=True)

    class Meta:
        model = OrderItem
        fields = (
            "id",
            "product_id",
            "product_name",
            "sku",
            "quantity",
            "unit_price",
            "discount",
            "discount_percent",
            "line_total",
        )
        read_only_fields = fields


class OrderItemInputSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(required=False)
    sku = serializers.CharField(max_length=100, required=False, allow_blank=True)
    quantity = serializers.IntegerField(min_value=1)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    # Tiền giảm cho MỖI sản phẩm. Không gửi = không giảm (khác unit_price: không
    # gửi = tự tính theo bảng giá).
    discount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, min_value=0)
    # Có % thì backend tự quy ra `discount` từ unit_price; gửi null để bỏ chế độ %.
    discount_percent = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True, min_value=0, max_value=100
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        product_id = attrs.get("product_id")
        sku = str(attrs.get("sku") or "").strip()
        if not product_id and not sku:
            raise serializers.ValidationError("Cần product_id hoặc sku.")
        return attrs


class OrderJourneyEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()
    event_type_label = serializers.SerializerMethodField()

    class Meta:
        model = OrderJourneyEvent
        fields = (
            "id",
            "event_type",
            "event_type_label",
            "from_code",
            "to_code",
            "note",
            "actor_name",
            "payload_json",
            "created_at",
        )

    def get_actor_name(self, obj) -> str | None:
        actor = getattr(obj, "actor", None)
        if not actor:
            return None
        return actor.display_name or actor.username

    def get_event_type_label(self, obj) -> str | None:
        return EVENT_TYPE_LABELS.get(obj.event_type)


class OrderNeedSerializer(serializers.ModelSerializer):
    group_code = serializers.CharField(source="need_type.group.code", read_only=True)
    group_name = serializers.CharField(source="need_type.group.name_vi", read_only=True)
    type_code = serializers.CharField(source="need_type.code", read_only=True)
    type_name = serializers.CharField(source="need_type.name_vi", read_only=True)

    class Meta:
        model = OrderNeed
        fields = (
            "group_code",
            "group_name",
            "type_code",
            "type_name",
        )


# Code fields whose label is rendered via CRM_CODE_LABELS lookup (auto-generated).
_LABELLED_CODE_FIELDS: tuple[str, ...] = tuple(
    f.name for f in ASSESSMENT_FIELDS
    if f.kind == "code" and f.name in CRM_CODE_LABELS
)


def _make_label_getter(code_field: str):
    def _getter(self, obj) -> str | None:
        code = getattr(obj, code_field, None)
        if not code:
            return None
        return CRM_CODE_LABELS.get(code_field, {}).get(code)
    _getter.__name__ = f"get_{code_field.removesuffix('_code')}_label"
    return _getter


class OrderCRMSerializerMeta(serializers.SerializerMetaclass):
    """Inject one SerializerMethodField per code field listed in _LABELLED_CODE_FIELDS."""

    def __new__(mcs, name, bases, attrs):
        for code_field in _LABELLED_CODE_FIELDS:
            label_field = f"{code_field.removesuffix('_code')}_label"
            attrs.setdefault(label_field, serializers.SerializerMethodField())
            attrs.setdefault(f"get_{label_field}", _make_label_getter(code_field))
        return super().__new__(mcs, name, bases, attrs)


def _build_meta_fields() -> tuple[str, ...]:
    fields: list[str] = []
    for spec in ASSESSMENT_FIELDS:
        fields.append(spec.name)
        if spec.name in _LABELLED_CODE_FIELDS:
            fields.append(f"{spec.name.removesuffix('_code')}_label")
    # Custom labels not derived from CRM_CODE_LABELS:
    fields += [
        "bomb_reason_group_label",
        "bomb_reason_type_label",
        "potential_level_label",
        "needs",
        "events",
        "updated_by_name",
        "next_reminder_at",
        "ai_filled_fields",
        "extra_fields",
        "ai_open_insights",
        "created_at",
        "updated_at",
    ]
    return tuple(fields)


class OrderCRMSerializer(serializers.ModelSerializer, metaclass=OrderCRMSerializerMeta):
    bomb_reason_group_label = serializers.SerializerMethodField()
    bomb_reason_type_label = serializers.SerializerMethodField()
    potential_level_label = serializers.SerializerMethodField()
    needs = OrderNeedSerializer(many=True, read_only=True)
    events = serializers.SerializerMethodField()
    updated_by_name = serializers.SerializerMethodField()
    next_reminder_at = serializers.SerializerMethodField()
    # Không phải cột client ghi được — chỉ dùng để hiển thị "AI đã điền field này".
    ai_filled_fields = serializers.JSONField(read_only=True)
    # Client không ghi trực tiếp — mọi ghi đi qua crm payload → _upsert_order_assessment.
    extra_fields = serializers.JSONField(read_only=True)
    ai_open_insights = serializers.JSONField(read_only=True)

    class Meta:
        model = OrderAssessment
        fields = _build_meta_fields()

    def get_bomb_reason_group_label(self, obj) -> str | None:
        if not obj.bomb_reason_group_code:
            return None
        return BOMB_REASON_GROUP_LABELS.get(obj.bomb_reason_group_code)

    def get_bomb_reason_type_label(self, obj) -> str | None:
        if not obj.bomb_reason_type_code:
            return None
        return BOMB_REASON_TYPE_LABELS.get(obj.bomb_reason_type_code)

    def get_potential_level_label(self, obj) -> str | None:
        if obj.potential_level is None:
            return None
        return POTENTIAL_LEVEL_LABELS.get(obj.potential_level)

    def get_updated_by_name(self, obj) -> str | None:
        updated_by = getattr(obj, "updated_by", None)
        if not updated_by:
            return None
        return updated_by.display_name or updated_by.username

    def get_events(self, obj):
        events = self.context.get("journey_events")
        if events is None:
            events = list(obj.order.journey_events.all())
        return OrderJourneyEventSerializer(events, many=True).data

    def get_next_reminder_at(self, obj):
        next_reminder_map = self.context.get("next_reminder_map")
        if next_reminder_map is not None:
            return next_reminder_map.get(obj.order_id)
        from apps.orders.application.services.sale_reminder_service import get_next_reminder_at

        return get_next_reminder_at(obj.order_id)


# crm.* mà KHÔNG bảng list nào đọc (audit FE 2026-08-04): events/updated_by/timestamps
# chỉ dành cho timeline (sheet3 fetch riêng qua journey-events API), các *_label này
# không có cell nào render, còn ai_*/extra_fields là JSON blob to nhất payload — chỉ
# panel sheet3 (BlockCrmRating) cần nên sheet3 giữ OrderCRMSerializer đầy đủ.
LIST_EXCLUDED_CRM_FIELDS = (
    "events",
    "updated_by_name",
    "created_at",
    "updated_at",
    "occupation_label",
    "potential_level_label",
    "bomb_reason_group_label",
    "bomb_reason_type_label",
    "ai_filled_fields",
    "extra_fields",
    "ai_open_insights",
)


class OrderCRMListSerializer(OrderCRMSerializer):
    """Bản gọn của OrderCRMSerializer cho crm_map của các trang list (trừ sheet3 —
    panel đánh giá của sheet3 dùng chung BlockCrmRating với trang chi tiết nên cần
    crm đầy đủ). Pop trong __init__ — xem OrderListSerializer."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in LIST_EXCLUDED_CRM_FIELDS:
            self.fields.pop(field_name, None)


