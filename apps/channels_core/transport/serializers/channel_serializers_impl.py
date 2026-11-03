from __future__ import annotations

from typing import TYPE_CHECKING

from django.apps import apps as django_apps
from drf_spectacular.utils import extend_schema_field, inline_serializer
from rest_framework import serializers

from apps.channels_core.models import Channel, ChannelKiotvietMapping, ChannelType
from apps.channels_core.repositories import ChannelRepository
from apps.users.models import Department, User

if TYPE_CHECKING:
    from apps.kiotviet.models import KiotVietChannel


def _kiotviet_channel_model():
    # Lazy get_model: cắt phụ thuộc import-time channels_core → kiotviet.
    # TODO(kiến trúc): thay bằng registry pattern ở sprint tách module sau.
    return django_apps.get_model("kiotviet", "KiotVietChannel")


def _price_book_model():
    # Lazy get_model (cùng pattern _kiotviet_channel_model ở trên): orders nằm
    # tầng TRÊN channels_core theo backend/.importlinter, nên không import
    # apps.orders.models trực tiếp ở top-level của module này.
    return django_apps.get_model("catalog", "PriceBook")


class PairedOldKiotVietChannelField(serializers.PrimaryKeyRelatedField):
    """PK field cho kênh Kiot cũ — queryset resolve lúc runtime (lazy).

    ``pk_field=IntegerField`` giữ schema OpenAPI là ``integer`` như khi field
    còn khai báo queryset trực tiếp (spectacular không suy được PK type từ
    ``get_queryset``).
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("pk_field", serializers.IntegerField())
        super().__init__(**kwargs)

    def get_queryset(self):
        return _kiotviet_channel_model().objects.filter(deleted_at__isnull=True, old=True)


class PriceBookField(serializers.PrimaryKeyRelatedField):
    """PK field cho PriceBook — queryset resolve LÚC REQUEST (lazy), y hệt lý do
    ``PairedOldKiotVietChannelField`` ở trên: gọi ``_price_book_model()`` ngay
    trong ``queryset=`` ở class body sẽ chạy lúc IMPORT MODULE, không lazy thật —
    rủi ro ``AppRegistryNotReady`` nếu module này bị import trước khi app registry
    populate xong. Dùng chung field này cho cả field đọc trên ``ChannelSerializer``
    lẫn field ghi trên ``ChannelUpdateSerializer`` để không lặp ``queryset``/
    ``help_text`` ở hai nơi.

    ``pk_field=IntegerField`` giữ schema OpenAPI là ``integer`` (spectacular
    không suy được PK type từ ``get_queryset`` động).
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("pk_field", serializers.IntegerField())
        kwargs.setdefault(
            "help_text",
            "Bảng giá gán cho kênh này; gửi null để gỡ gán (dùng bảng mặc định).",
        )
        super().__init__(**kwargs)

    def get_queryset(self):
        return _price_book_model().objects.filter(deleted_at__isnull=True)


class ChannelTypeField(serializers.PrimaryKeyRelatedField):
    """PK field cho ChannelType — chỉ nhận loại kênh đang bật.

    ``get_queryset`` (không phải ``queryset=`` ở class body) để lọc ``is_active``
    LÚC REQUEST: tắt một loại kênh giữa chừng thì form ngừng nhận ngay, không
    phải restart process. ``pk_field=IntegerField`` giữ schema OpenAPI là
    ``integer`` — cùng lý do đã ghi ở ``PriceBookField``.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("pk_field", serializers.IntegerField())
        kwargs.setdefault(
            "help_text",
            "Loại kênh bán (Shopee/TikTok/Facebook/Web); gửi null để bỏ phân loại.",
        )
        super().__init__(**kwargs)

    def get_queryset(self):
        return ChannelType.objects.filter(is_active=True)


class ChannelTypeSerializer(serializers.ModelSerializer):
    """Danh mục loại kênh cho dropdown ở trang Quản lý kênh."""

    channel_count = serializers.SerializerMethodField()

    class Meta:
        model = ChannelType
        fields = ["id", "code", "name", "sort_order", "is_active", "channel_count"]

    def get_channel_count(self, obj: ChannelType) -> int:
        """Số kênh đang gán loại này — FE dùng để cảnh báo trước khi xoá.

        Đọc từ annotate của repository khi có (danh sách), fallback query lẻ cho
        instance rời (response của POST/PATCH chỉ có 1 dòng nên không phải N+1).
        """
        annotated = getattr(obj, "channel_count_annotated", None)
        if annotated is not None:
            return annotated
        return obj.channels.filter(deleted_at__isnull=True).count()


class ChannelTypeCreateSerializer(serializers.ModelSerializer):
    """Tạo loại kênh. ``code`` bắt buộc và duy nhất; đặt xong là KHOÁ (xem update)."""

    class Meta:
        model = ChannelType
        fields = ["code", "name", "sort_order", "is_active"]

    def validate_code(self, value: str) -> str:
        code = (value or "").strip().lower()
        if not code:
            raise serializers.ValidationError("Mã loại kênh không được rỗng.")
        if ChannelType.objects.filter(code=code).exists():
            raise serializers.ValidationError(f"Mã loại kênh '{code}' đã tồn tại.")
        return code


class ChannelTypeUpdateSerializer(serializers.ModelSerializer):
    """Sửa loại kênh — CỐ Ý không có ``code``.

    ``code`` là khoá nghiệp vụ: nó trùng giá trị ``SALE_CHANNEL_*`` mà báo cáo sẽ
    dựa vào, và hàm reverse của migration 0024 xoá seed theo đúng mã này. Đổi mã
    sau khi tạo là làm hỏng cả hai một cách âm thầm, nên chỉ cho sửa phần hiển thị.
    """

    class Meta:
        model = ChannelType
        fields = ["name", "sort_order", "is_active"]


def normalize_source_links(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [line.strip() for line in value.replace(",", "\n").splitlines()]
        return [item for item in parts if item]
    if isinstance(value, list):
        normalized = []
        for item in value:
            if not isinstance(item, str):
                continue
            cleaned = item.strip()
            if cleaned:
                normalized.append(cleaned)
        return normalized
    return []


def normalize_fb_ads_accounts(value) -> list[int]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [chunk.strip() for chunk in value.replace(",", "\n").splitlines()]
        raw_items = parts
    elif isinstance(value, list):
        raw_items = value
    else:
        return []
    seen: set[int] = set()
    result: list[int] = []
    for item in raw_items:
        try:
            account_id = int(item)
        except (TypeError, ValueError):
            continue
        if account_id <= 0 or account_id in seen:
            continue
        seen.add(account_id)
        result.append(account_id)
    return result


def normalize_branch_ids(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [line.strip() for line in value.replace(",", "\n").splitlines()]
        seen: set[str] = set()
        result: list[str] = []
        for item in parts:
            if not item or item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result
    if isinstance(value, list):
        seen: set[str] = set()
        result: list[str] = []
        for item in value:
            cleaned = str(item).strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            result.append(cleaned)
        return result
    return []


def normalize_branch_names(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [line.strip() for line in value.replace(",", "\n").splitlines()]
    if isinstance(value, list):
        return [str(item).strip() for item in value]
    return []


def align_branch_names(branch_ids: list[str], branch_names: list[str]) -> list[str]:
    """Return branch_names aligned 1:1 with branch_ids (same order, same length).

    Missing names are filled with an empty string; extra names are dropped so the
    invariant branch_names[i] == name of branch_ids[i] always holds.
    """
    if not branch_ids:
        return []
    aligned = list(branch_names[: len(branch_ids)])
    aligned.extend([""] * (len(branch_ids) - len(aligned)))
    return aligned


def extract_channel_branch_ids(channel: KiotVietChannel | None) -> list[str]:
    if channel is None:
        return []
    normalized = normalize_branch_ids(channel.branch_ids)
    branch_id = (channel.branch_id or "").strip()
    if branch_id and branch_id not in normalized:
        normalized.append(branch_id)
    return normalized


class OldSyncBranchPairSerializer(serializers.Serializer):
    """1 cặp kho đồng bộ new→old: kho nguồn trên kênh mới → kho đích trên Kiot cũ."""

    source_branch_id = serializers.CharField(max_length=100)
    target_branch_id = serializers.CharField(max_length=100)


def validate_old_sync_branch_pairs(
    value,
    *,
    source_branch_ids: list[str],
    old_channel_branch_ids: list[str],
) -> list[dict]:
    """Chuẩn hoá + validate danh sách cặp kho old_sync_branch_map.

    - source phải thuộc branch_ids của kênh mới, không trùng nhau.
    - target phải thuộc branch của kênh Kiot cũ (khi biết danh sách).
    Raise ValidationError key ``old_sync_branch_map``.
    """
    pairs: list[dict] = []
    seen_sources: set[str] = set()
    for entry in value or []:
        source = str(entry.get("source_branch_id") or "").strip()
        target = str(entry.get("target_branch_id") or "").strip()
        if not source or not target:
            raise serializers.ValidationError(
                {"old_sync_branch_map": "Mỗi cặp kho phải có đủ mã kho nguồn và mã kho đích."}
            )
        if source in seen_sources:
            raise serializers.ValidationError(
                {"old_sync_branch_map": f"Kho nguồn {source} bị lặp trong danh sách cặp kho."}
            )
        if source_branch_ids and source not in source_branch_ids:
            raise serializers.ValidationError(
                {"old_sync_branch_map": f"Kho nguồn {source} không thuộc danh sách branch của kênh."}
            )
        if old_channel_branch_ids and target not in old_channel_branch_ids:
            raise serializers.ValidationError(
                {"old_sync_branch_map": f"Kho đích {target} không thuộc branch của kênh Kiot cũ đã chọn."}
            )
        seen_sources.add(source)
        pairs.append({"source_branch_id": source, "target_branch_id": target})
    return pairs


def derive_fallback_sync_branch_id(branch_map: list[dict], primary_branch_id: str) -> str:
    """old_sync_branch_id fallback khi user chỉ nhập map: target của kho chính, không có thì cặp đầu."""
    if not branch_map:
        return ""
    primary = (primary_branch_id or "").strip()
    for entry in branch_map:
        if entry["source_branch_id"] == primary:
            return entry["target_branch_id"]
    return branch_map[0]["target_branch_id"]


class ChannelKiotvietMappingSerializer(serializers.ModelSerializer):
    kiotviet_channel_id = serializers.IntegerField()
    kiotviet_channel_name = serializers.CharField(source="kiotviet_channel.name", read_only=True)
    external_sale_channel_id = serializers.CharField()

    class Meta:
        model = ChannelKiotvietMapping
        fields = ["kiotviet_channel_id", "kiotviet_channel_name", "external_sale_channel_id"]


class ChannelKiotvietMappingInputSerializer(serializers.Serializer):
    kiotviet_channel_id = serializers.IntegerField(min_value=1)
    external_sale_channel_id = serializers.CharField(max_length=255)

    def validate_external_sale_channel_id(self, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise serializers.ValidationError("external_sale_channel_id không được rỗng.")
        return text


def _validate_mapping_list(value):
    if not value:
        return []
    inner = ChannelKiotvietMappingInputSerializer(data=value, many=True)
    inner.is_valid(raise_exception=True)
    items = list(inner.validated_data)
    seen: set[int] = set()
    deduped: list[dict] = []
    for entry in items:
        kv_id = entry["kiotviet_channel_id"]
        if kv_id in seen:
            raise serializers.ValidationError(
                f"Trùng kiotviet_channel_id={kv_id} trong danh sách mapping."
            )
        seen.add(kv_id)
        deduped.append(entry)
    if seen:
        valid_ids = set(
            _kiotviet_channel_model().objects
            .filter(id__in=seen, deleted_at__isnull=True)
            .values_list("id", flat=True)
        )
        missing = seen - valid_ids
        if missing:
            raise serializers.ValidationError(
                f"kiotviet_channel_id không hợp lệ: {sorted(missing)}"
            )
    return deduped


class ChannelSerializer(serializers.ModelSerializer):
    assigned_user_count = serializers.SerializerMethodField()
    assigned_users = serializers.SerializerMethodField()
    kiotviet_mappings = serializers.SerializerMethodField()
    department_ids = serializers.SerializerMethodField()
    department_names = serializers.SerializerMethodField()
    # 2 field dưới đọc từ property model (bảng nối ChannelAdAccount). Khai báo
    # tường minh với bound int4 + help_text y hệt ArrayField cũ để schema OpenAPI
    # KHÔNG đổi một byte nào (contract giữ nguyên — audit 2026-07 DB #6).
    fb_ads_accounts = serializers.ListField(
        child=serializers.IntegerField(max_value=2147483647, min_value=-2147483648),
        required=False,
        help_text="ID các FacebookAdAccount được map vào kênh này (dùng cho báo cáo Ads).",
    )
    google_ads_accounts = serializers.ListField(
        child=serializers.IntegerField(max_value=2147483647, min_value=-2147483648),
        required=False,
        help_text="ID các GoogleAdsAccount được map vào kênh này (dùng cho báo cáo Ads).",
    )
    channel_type = serializers.SerializerMethodField()
    price_book = serializers.SerializerMethodField()
    price_book_id = PriceBookField(
        source="price_book",
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Channel
        fields = [
            "id",
            "code",
            "name",
            "owner",
            "landingpage_source",
            "fb_ads_accounts",
            "google_ads_accounts",
            "is_active",
            "created_at",
            "department_ids",
            "department_names",
            "assigned_user_count",
            "assigned_users",
            "kiotviet_mappings",
            "channel_type",
            "price_book",
            "price_book_id",
        ]

    def get_department_ids(self, obj: Channel) -> list[int]:
        return [dept.id for dept in obj.departments.all()]

    def get_department_names(self, obj: Channel) -> list[str]:
        return [dept.name for dept in obj.departments.all()]

    def get_assigned_user_count(self, obj: Channel) -> int:
        return len(self.get_assigned_users(obj))

    def get_assigned_users(self, obj: Channel) -> list[str]:
        if hasattr(obj, "prefetched_user_links"):
            return [link.user.username for link in obj.prefetched_user_links]
        return list(ChannelRepository.list_assigned_staff(obj).values_list("username", flat=True))

    def get_kiotviet_mappings(self, obj: Channel):
        mappings = ChannelRepository.list_kiotviet_mappings(obj)
        return ChannelKiotvietMappingSerializer(mappings, many=True).data

    @extend_schema_field(
        inline_serializer(
            "ChannelTypeRef",
            fields={
                "id": serializers.IntegerField(),
                "code": serializers.CharField(),
                "name": serializers.CharField(),
            },
            # Kênh chưa phân loại là trạng thái mặc định hợp lệ (4 loại seed sẵn
            # chỉ phủ được phần kênh khớp luật backfill của migration 0024).
            allow_null=True,
        )
    )
    def get_channel_type(self, obj: Channel) -> dict | None:
        channel_type = getattr(obj, "channel_type", None)
        if channel_type is None:
            return None
        return {"id": channel_type.id, "code": channel_type.code, "name": channel_type.name}

    @extend_schema_field(
        inline_serializer(
            "ChannelPriceBookRef",
            fields={
                "id": serializers.IntegerField(),
                "code": serializers.CharField(),
                "name": serializers.CharField(),
            },
            # allow_null=True: kênh chưa gán bảng giá là trạng thái mặc định (trả
            # về None, xem get_price_book bên dưới) — khai rõ để OpenAPI sinh
            # "nullable: true" đúng thực tế, không chỉ dựa vào docstring.
            allow_null=True,
        )
    )
    def get_price_book(self, obj: Channel) -> dict | None:
        # Bảng giá đã soft-delete không được lộ ra (giống ProductSerializer.group) —
        # channel coi như chưa gán, resolve_price_book sẽ tự fallback về bảng mặc định.
        book = getattr(obj, "price_book", None)
        if book is None or book.deleted_at is not None:
            return None
        return {"id": book.id, "code": book.code, "name": book.name}


class ChannelCreateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=50)
    name = serializers.CharField(max_length=255)
    owner = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    landingpage_source = serializers.ListField(child=serializers.CharField(max_length=2048), required=False, default=list)
    fb_ads_accounts = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, default=list)
    google_ads_accounts = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False, default=list)
    kiotviet_mappings = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    is_active = serializers.BooleanField(default=True)
    channel_type_id = ChannelTypeField(
        source="channel_type",
        required=False,
        allow_null=True,
    )
    department_ids = serializers.PrimaryKeyRelatedField(
        source="departments",
        queryset=Department.objects.all(),
        many=True,
        required=False,
    )

    def validate_landingpage_source(self, value):
        return normalize_source_links(value)

    def validate_fb_ads_accounts(self, value):
        return normalize_fb_ads_accounts(value)

    def validate_google_ads_accounts(self, value):
        return normalize_fb_ads_accounts(value)

    def validate_kiotviet_mappings(self, value):
        return _validate_mapping_list(value)


class ChannelUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    owner = serializers.CharField(max_length=255, required=False, allow_blank=True)
    landingpage_source = serializers.ListField(child=serializers.CharField(max_length=2048), required=False)
    fb_ads_accounts = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False)
    google_ads_accounts = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False)
    kiotviet_mappings = serializers.ListField(child=serializers.DictField(), required=False)
    is_active = serializers.BooleanField(required=False)
    department_ids = serializers.PrimaryKeyRelatedField(
        source="departments",
        queryset=Department.objects.all(),
        many=True,
        required=False,
    )
    channel_type_id = ChannelTypeField(
        source="channel_type",
        required=False,
        allow_null=True,
    )
    price_book_id = PriceBookField(
        source="price_book",
        required=False,
        allow_null=True,
    )

    def validate_landingpage_source(self, value):
        return normalize_source_links(value)

    def validate_fb_ads_accounts(self, value):
        return normalize_fb_ads_accounts(value)

    def validate_google_ads_accounts(self, value):
        return normalize_fb_ads_accounts(value)

    def validate_kiotviet_mappings(self, value):
        return _validate_mapping_list(value)


class ChannelAssignmentSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()


class ChannelBulkDeleteSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False)

    def validate_ids(self, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class StaffUserSerializer(serializers.ModelSerializer):
    department_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = User
        fields = ["id", "username", "display_name", "department_id"]


class KiotVietChannelSerializer(serializers.ModelSerializer):
    paired_old_channel_id = serializers.IntegerField(source="paired_old_channel.id", read_only=True, allow_null=True)
    paired_old_channel_name = serializers.CharField(source="paired_old_channel.name", read_only=True, allow_null=True)

    class Meta:
        model = django_apps.get_model("kiotviet", "KiotVietChannel")
        fields = [
            "id",
            "name",
            "branch_id",
            "branch_ids",
            "branch_names",
            "old_sync_branch_id",
            "old_sync_branch_map",
            "client_id",
            "client_secret",
            "retailer",
            "username",
            "password",
            "old",
            "vat_tax_id",
            "paired_old_channel_id",
            "paired_old_channel_name",
            "created_at",
        ]


class KiotVietChannelCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    branch_id = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    branch_ids = serializers.ListField(child=serializers.CharField(max_length=100), required=False, default=list)
    branch_names = serializers.ListField(
        child=serializers.CharField(max_length=255, allow_blank=True), required=False, default=list
    )
    old_sync_branch_id = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    old_sync_branch_map = OldSyncBranchPairSerializer(many=True, required=False, default=list)
    client_id = serializers.CharField(max_length=255)
    client_secret = serializers.CharField(max_length=255)
    retailer = serializers.CharField(max_length=255)
    username = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    password = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    old = serializers.BooleanField(required=False, default=False, help_text="Kiot cũ")
    vat_tax_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        default=None,
        min_value=1,
        help_text="Mã thuế KiotViet gắn cho dòng hàng khi đẩy đơn (vd 3 = VAT 8%). Trống = không áp VAT.",
    )
    paired_old_channel_id = PairedOldKiotVietChannelField(
        source="paired_old_channel",
        required=False,
        allow_null=True,
    )

    def validate_branch_ids(self, value):
        return normalize_branch_ids(value)

    def validate_branch_names(self, value):
        return normalize_branch_names(value)

    def validate(self, attrs):
        if attrs.get("old") and attrs.get("paired_old_channel") is not None:
            raise serializers.ValidationError({"paired_old_channel_id": "Kênh cũ không thể ghép thêm kênh cũ khác."})
        branch_ids = attrs.get("branch_ids") or []
        branch_id = (attrs.get("branch_id") or "").strip()
        if not branch_ids and branch_id:
            branch_ids = [branch_id]
        if branch_ids and not branch_id:
            branch_id = branch_ids[0]
        if not branch_ids:
            raise serializers.ValidationError({"branch_ids": "Vui lòng nhập ít nhất 1 branch id."})
        attrs["branch_ids"] = branch_ids
        attrs["branch_id"] = branch_id
        attrs["branch_names"] = align_branch_names(branch_ids, attrs.get("branch_names") or [])

        if attrs.get("old"):
            attrs["old_sync_branch_id"] = ""
            attrs["old_sync_branch_map"] = []
        elif attrs.get("paired_old_channel") is not None:
            paired_old_channel = attrs.get("paired_old_channel")
            old_channel_branch_ids = extract_channel_branch_ids(paired_old_channel)
            branch_map = validate_old_sync_branch_pairs(
                attrs.get("old_sync_branch_map") or [],
                source_branch_ids=branch_ids,
                old_channel_branch_ids=old_channel_branch_ids,
            )
            attrs["old_sync_branch_map"] = branch_map
            old_sync_branch_id = (attrs.get("old_sync_branch_id") or "").strip()
            if not old_sync_branch_id and branch_map:
                old_sync_branch_id = derive_fallback_sync_branch_id(branch_map, branch_id)
            if not old_sync_branch_id:
                raise serializers.ValidationError(
                    {"old_sync_branch_map": "Vui lòng nhập ít nhất 1 cặp kho đồng bộ sang kênh Kiot cũ."}
                )
            if old_channel_branch_ids and old_sync_branch_id not in old_channel_branch_ids:
                raise serializers.ValidationError(
                    {"old_sync_branch_id": "Branch id đồng bộ phải thuộc danh sách branch của kênh Kiot cũ đã chọn."}
                )
            attrs["old_sync_branch_id"] = old_sync_branch_id
        else:
            attrs["old_sync_branch_map"] = []
        return attrs


class KiotVietChannelUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    branch_id = serializers.CharField(max_length=100, required=False)
    branch_ids = serializers.ListField(child=serializers.CharField(max_length=100), required=False)
    branch_names = serializers.ListField(
        child=serializers.CharField(max_length=255, allow_blank=True), required=False
    )
    old_sync_branch_id = serializers.CharField(max_length=100, required=False, allow_blank=True)
    old_sync_branch_map = OldSyncBranchPairSerializer(many=True, required=False)
    client_id = serializers.CharField(max_length=255, required=False)
    client_secret = serializers.CharField(max_length=255, required=False)
    retailer = serializers.CharField(max_length=255, required=False)
    username = serializers.CharField(max_length=255, required=False, allow_blank=True)
    password = serializers.CharField(max_length=255, required=False, allow_blank=True)
    old = serializers.BooleanField(required=False, help_text="Kiot cũ")
    vat_tax_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
        help_text="Mã thuế KiotViet gắn cho dòng hàng khi đẩy đơn (vd 3 = VAT 8%). null = không áp VAT.",
    )
    paired_old_channel_id = PairedOldKiotVietChannelField(
        source="paired_old_channel",
        required=False,
        allow_null=True,
    )

    def validate_branch_ids(self, value):
        return normalize_branch_ids(value)

    def validate_branch_names(self, value):
        return normalize_branch_names(value)

    def validate(self, attrs):
        instance: KiotVietChannel | None = self.context.get("instance")
        if attrs.get("old") is True and "paired_old_channel" in attrs and attrs.get("paired_old_channel") is not None:
            raise serializers.ValidationError({"paired_old_channel_id": "Kênh cũ không thể ghép thêm kênh cũ khác."})
        has_branch_ids = "branch_ids" in attrs
        has_branch_id = "branch_id" in attrs
        if has_branch_ids:
            branch_ids = attrs.get("branch_ids") or []
            if not branch_ids:
                raise serializers.ValidationError({"branch_ids": "Vui lòng nhập ít nhất 1 branch id."})
            if not has_branch_id:
                attrs["branch_id"] = branch_ids[0]
        if has_branch_id and not has_branch_ids:
            branch_id = (attrs.get("branch_id") or "").strip()
            attrs["branch_ids"] = [branch_id] if branch_id else []
        # Keep branch_names aligned with branch_ids whenever branch_ids changes.
        # If branch_names is sent without branch_ids, ignore it (cannot align safely).
        if "branch_ids" in attrs:
            attrs["branch_names"] = align_branch_names(
                attrs.get("branch_ids") or [], attrs.get("branch_names") or []
            )
        else:
            attrs.pop("branch_names", None)
        effective_old = attrs.get("old", instance.old if instance else False)
        effective_paired_old_channel = attrs.get(
            "paired_old_channel",
            instance.paired_old_channel if instance else None,
        )

        if effective_old and effective_paired_old_channel is not None:
            raise serializers.ValidationError({"paired_old_channel_id": "Kênh cũ không thể ghép thêm kênh cũ khác."})

        # `branch_ids` PHẢI nằm trong đây: thu hẹp branch_ids mà không gửi lại map sẽ
        # để map "mồ côi" một kho đã gỡ — reconcile vẫn tiếp tục sync kho đó sang kênh cũ
        # (admin tưởng đã ngừng). Re-validate map theo branch_ids mới: nếu kho bị gỡ đang
        # được map, validate_old_sync_branch_pairs raise buộc admin sửa map; nếu map không
        # đụng kho đó thì pass im lặng, không đổi gì (audit 2026-07-15).
        pairing_touched = any(
            key in attrs
            for key in ("paired_old_channel", "old_sync_branch_id", "old_sync_branch_map", "branch_ids")
        )
        if effective_old:
            attrs["old_sync_branch_id"] = ""
            attrs["old_sync_branch_map"] = []
        elif pairing_touched and effective_paired_old_channel is not None:
            old_channel_branch_ids = extract_channel_branch_ids(effective_paired_old_channel)
            effective_branch_ids = (
                attrs.get("branch_ids")
                if "branch_ids" in attrs
                else extract_channel_branch_ids(instance)
            )
            if "old_sync_branch_map" in attrs:
                raw_map = attrs.get("old_sync_branch_map") or []
            else:
                raw_map = (instance.old_sync_branch_map or []) if instance else []
            branch_map = validate_old_sync_branch_pairs(
                raw_map,
                source_branch_ids=effective_branch_ids or [],
                old_channel_branch_ids=old_channel_branch_ids,
            )
            attrs["old_sync_branch_map"] = branch_map
            if "old_sync_branch_id" in attrs:
                old_sync_branch_id = (attrs.get("old_sync_branch_id") or "").strip()
            else:
                old_sync_branch_id = ((instance.old_sync_branch_id if instance else "") or "").strip()
            if not old_sync_branch_id and branch_map:
                effective_branch_id = (
                    attrs.get("branch_id")
                    or (instance.branch_id if instance else "")
                    or ""
                ).strip()
                old_sync_branch_id = derive_fallback_sync_branch_id(branch_map, effective_branch_id)
            if not old_sync_branch_id:
                raise serializers.ValidationError(
                    {"old_sync_branch_map": "Vui lòng nhập ít nhất 1 cặp kho đồng bộ sang kênh Kiot cũ."}
                )
            if old_channel_branch_ids and old_sync_branch_id not in old_channel_branch_ids:
                raise serializers.ValidationError(
                    {"old_sync_branch_id": "Branch id đồng bộ phải thuộc danh sách branch của kênh Kiot cũ đã chọn."}
                )
            attrs["old_sync_branch_id"] = old_sync_branch_id
        elif effective_paired_old_channel is None and "old_sync_branch_map" in attrs:
            # Không ghép Kiot cũ thì map không có ý nghĩa — clear thay vì lưu rác.
            attrs["old_sync_branch_map"] = []
        if "old_sync_branch_id" in attrs:
            attrs["old_sync_branch_id"] = (attrs.get("old_sync_branch_id") or "").strip()
        return attrs
