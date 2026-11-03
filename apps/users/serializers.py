from django.db import models
from drf_spectacular.utils import extend_schema_field, inline_serializer
from rest_framework import serializers

from apps.kiotviet.models import KiotVietChannel

from .models import (
    VALID_SECTIONS,
    DataScope,
    Department,
    FieldRuleType,
    MediaRole,
    Permission,
    Role,
    RoleFieldRule,
    RolePermission,
    RoleSectionScope,
    Team,
    User,
    UserKiotvietMapping,
)
from .rbac import resolve_effective_permissions, resolve_effective_permissions_bulk
from .rbac_catalog import ALL_ORDER_FIELDS, ORDER_GRANT_SECTION

# Section nội dung Media (spec Media §4.7) — chỉ mở khi user được provision
# (có media_api_key riêng hoặc media_role), không qua permission catalog.
MEDIA_CONTENT_SECTIONS = frozenset(
    {"media-explore", "media-contents", "media-proposals", "media-reports", "media-taxonomy"}
)


def _is_media_provisioned(user) -> bool:
    # Trùng logic apps.media.application.services.credential_resolver.is_provisioned,
    # nhân bản tại đây vì `users` là tầng đáy, không được import `apps.media`
    # (import-linter contract `App layering` chặn cả import cục bộ trong hàm).
    return bool((getattr(user, "media_api_key", "") or "").strip() or (getattr(user, "media_role", "") or "").strip())


def validate_media_api_key(value):
    if value is None or value == "":
        return value
    value = value.strip()
    if not value.startswith("dak_") or len(value) < 12:
        raise serializers.ValidationError("API key Media phải bắt đầu bằng 'dak_'.")
    return value


class DepartmentSerializer(serializers.ModelSerializer):
    channel_ids = serializers.SerializerMethodField()
    team_count = serializers.SerializerMethodField()

    class Meta:
        model = Department
        fields = ("id", "name", "is_active", "created_at", "channel_ids", "team_count")
        read_only_fields = ("id", "created_at")

    def get_team_count(self, obj: Department) -> int:
        # Cùng lý do với `get_channel_ids`: duyệt Python trên prefetch, không `.filter()`.
        return sum(1 for team in obj.staff_teams.all() if team.deleted_at is None)

    def get_channel_ids(self, obj: Department) -> list[int]:
        # .all() + lọc bằng Python để dùng prefetch cache của admin_list_users; .filter()
        # trên related manager LUÔN đi DB lại, kể cả khi đã prefetch — đó chính là N+1
        # cũ (audit 2026-07-26 T1). Không prefetch vẫn đúng, chỉ chậm hơn: đây là
        # fallback an toàn cho các endpoint dùng DepartmentSerializer một mình.
        return [channel.id for channel in obj.channels.all() if channel.deleted_at is None]


class DepartmentCreateUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    is_active = serializers.BooleanField(default=True)
    # Danh sách kênh bán hàng gán cho phòng ban; chỉ áp dụng khi được gửi lên (partial).
    channel_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False)

    def validate_channel_ids(self, value):
        return list(dict.fromkeys(value))


class UserKiotvietMappingSerializer(serializers.ModelSerializer):
    kiotviet_channel_id = serializers.IntegerField()
    kiotviet_channel_name = serializers.CharField(source="kiotviet_channel.name", read_only=True)
    external_kiot_user_id = serializers.CharField()

    class Meta:
        model = UserKiotvietMapping
        fields = ("kiotviet_channel_id", "kiotviet_channel_name", "external_kiot_user_id")


class UserKiotvietMappingInputSerializer(serializers.Serializer):
    kiotviet_channel_id = serializers.IntegerField(min_value=1)
    external_kiot_user_id = serializers.CharField(max_length=100)

    def validate_external_kiot_user_id(self, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise serializers.ValidationError("external_kiot_user_id không được rỗng.")
        return text


def _validate_unique_foxia_id(value: str, *, exclude_user_id: int | None = None) -> str:
    """Trim + chặn hai user (chưa xoá mềm) cùng trỏ về một tài khoản Foxia."""
    text = (value or "").strip()
    if not text:
        return ""
    qs = User.objects.filter(foxia_id=text, deleted_at__isnull=True)
    if exclude_user_id is not None:
        qs = qs.exclude(id=exclude_user_id)
    clash = qs.values_list("username", flat=True).first()
    if clash:
        raise serializers.ValidationError(f"Foxia ID {text} đã gán cho user '{clash}'.")
    return text


def _validate_kiot_mapping_list(value):
    if not value:
        return []
    inner = UserKiotvietMappingInputSerializer(data=value, many=True)
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
            KiotVietChannel.objects
            .filter(id__in=seen, deleted_at__isnull=True)
            .values_list("id", flat=True)
        )
        missing = seen - valid_ids
        if missing:
            raise serializers.ValidationError(
                f"kiotviet_channel_id không hợp lệ: {sorted(missing)}"
            )
    return deduped


# Khoá trong `context` chứa map {user_id: EffectivePermissions} cho danh sách user.
EFFECTIVE_PERMISSIONS_CONTEXT_KEY = "effective_permissions_map"


class _UserListSerializer(serializers.ListSerializer):
    """Danh sách user: resolve RBAC theo LÔ một lần cho cả trang (audit 2026-09-23
    T1 §2.2). ``UserSerializer`` đọc hai lần `resolve_effective_permissions` mỗi user
    (allowed_sections + editable_order_fields) → cache lạnh = 5 SQL/user, 50-100 user
    = 250-500 query/request. Ở đây dựng map một lần qua
    ``resolve_effective_permissions_bulk`` (số query hằng) rồi đặt vào ``context``
    dùng chung với child; child thiếu map (dùng lẻ ở endpoint khác) tự resolve như cũ.
    """

    def to_representation(self, data):
        iterable = data.all() if isinstance(data, models.Manager) else data
        users = list(iterable)
        # `self.context` là dict `_context` của root — child đọc cùng dict này.
        self.context.setdefault(EFFECTIVE_PERMISSIONS_CONTEXT_KEY, {}).update(
            resolve_effective_permissions_bulk(users)
        )
        return super().to_representation(users)


class UserSerializer(serializers.ModelSerializer):
    department = DepartmentSerializer(read_only=True)
    allowed_sections = serializers.SerializerMethodField()
    editable_order_fields = serializers.SerializerMethodField()
    kiotviet_mappings = serializers.SerializerMethodField()
    role_ids = serializers.SerializerMethodField()
    department_ids = serializers.SerializerMethodField()
    media_api_key_set = serializers.SerializerMethodField()
    media_api_key_prefix = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "display_name",
            "avatar_data_url",
            "is_active",
            "department",
            "allowed_sections",
            "editable_order_fields",
            "kiotviet_mappings",
            "lark_open_id",
            "zenify_email",
            "foxia_id",
            "media_role",
            "media_api_key_set",
            "media_api_key_prefix",
            "role_ids",
            "department_ids",
        )
        list_serializer_class = _UserListSerializer

    def _effective(self, obj):
        """Quyền hiệu dụng của `obj`: ưu tiên map theo lô trong context (danh sách),
        fallback resolve lẻ (instance rời — response POST/PATCH chỉ có 1 dòng)."""
        eff_map = self.context.get(EFFECTIVE_PERMISSIONS_CONTEXT_KEY) or {}
        eff = eff_map.get(obj.id)
        if eff is None:
            eff = resolve_effective_permissions(obj)
        return eff

    # Các method dưới đọc qua .all() để dùng prefetch cache khi queryset đã
    # prefetch_related (admin_list_users — audit 2026-07 mục 3.5); sort/lọc bằng
    # Python thay vì .filter()/.order_by() mới (sẽ query lại DB từng user).
    # Không prefetch vẫn hoạt động đúng, chỉ chậm hơn (fallback an toàn).
    def get_role_ids(self, obj) -> list[int]:
        return [ra.role_id for ra in obj.role_assignments.all()]

    def get_department_ids(self, obj) -> list[int]:
        links = sorted(
            obj.department_links.all(),
            key=lambda link: (0 if link.is_primary else 1, link.department_id),
        )
        return [link.department_id for link in links]

    def get_media_api_key_set(self, obj) -> bool:
        return bool((obj.media_api_key or "").strip())

    def get_media_api_key_prefix(self, obj) -> str:
        key = (obj.media_api_key or "").strip()
        return key[:12] if key else ""

    def get_allowed_sections(self, obj) -> list[str]:
        eff = self._effective(obj)
        sections = set(VALID_SECTIONS) if eff.is_superuser else {
            code.split(":", 1)[0] for code in eff.permission_codes
        }
        # Module Media hiện theo provisioning từng user, không qua role (spec Media D5).
        # Superuser cũng cần provisioning mới gọi được ads-crm, nhưng menu đã có sẵn
        # qua VALID_SECTIONS — trang sẽ hiện "chưa được cấp quyền".
        if _is_media_provisioned(obj):
            sections |= MEDIA_CONTENT_SECTIONS
        return sorted(sections)

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_editable_order_fields(self, obj) -> list[str]:
        eff = self._effective(obj)
        if eff.is_superuser:
            return sorted(ALL_ORDER_FIELDS)
        return sorted(eff.editable_fields.get(ORDER_GRANT_SECTION, frozenset()))

    @extend_schema_field(UserKiotvietMappingSerializer(many=True))
    def get_kiotviet_mappings(self, obj):
        mappings = sorted(
            obj.kiotviet_mappings.all(),
            key=lambda m: (m.kiotviet_channel.name or "", m.kiotviet_channel_id),
        )
        return UserKiotvietMappingSerializer(mappings, many=True).data


class UserProfileUpdateSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=150, allow_blank=True, required=False)
    avatar_data_url = serializers.CharField(allow_blank=True, required=False)

    def validate_avatar_data_url(self, value: str) -> str:
        if not value:
            return ""
        allowed_prefixes = ("data:image/png", "data:image/jpeg", "data:image/webp", "data:image/jpg")
        if not value.startswith(allowed_prefixes):
            raise serializers.ValidationError("Avatar phải là ảnh PNG/JPEG/WEBP.")
        if len(value) > 2_000_000:
            raise serializers.ValidationError("Avatar quá lớn.")
        return value


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField()
    # Độ dài tối thiểu do AUTH_PASSWORD_VALIDATORS (view gọi validate_password) quyết
    # định — không lặp min_length ở đây để khỏi lệch chính sách (từng là 8 vs 12).
    new_password = serializers.CharField()
    confirm_password = serializers.CharField()

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Mật khẩu xác nhận không khớp."})
        return attrs


class AdminUserCreateSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    display_name = serializers.CharField(max_length=150, allow_blank=True, required=False, default="")
    is_active = serializers.BooleanField(default=True)
    kiotviet_mappings = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    password = serializers.CharField(min_length=8)
    department_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    lark_open_id = serializers.CharField(max_length=128, allow_blank=True, required=False, default="")
    zenify_email = serializers.EmailField(allow_blank=True, required=False, default="", trim_whitespace=True)
    foxia_id = serializers.CharField(max_length=64, allow_blank=True, required=False, default="")
    media_role = serializers.ChoiceField(choices=MediaRole.choices, required=False, default="")
    media_api_key = serializers.CharField(
        max_length=1024, allow_blank=True, allow_null=True, required=False, default=""
    )
    role_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False)
    department_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False
    )

    def validate_username(self, value: str) -> str:
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Username đã tồn tại.")
        return value

    def validate_department_id(self, value):
        if value is not None and not Department.objects.filter(id=value, is_active=True).exists():
            raise serializers.ValidationError("Phòng ban không tồn tại.")
        return value

    def validate_kiotviet_mappings(self, value):
        return _validate_kiot_mapping_list(value)

    def validate_foxia_id(self, value):
        return _validate_unique_foxia_id(value)

    def validate_media_api_key(self, value):
        return validate_media_api_key(value) or ""


class AdminUserUpdateSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=150, allow_blank=True, required=False)
    is_active = serializers.BooleanField(required=False)
    kiotviet_mappings = serializers.ListField(child=serializers.DictField(), required=False)
    # `allow_blank`: client gửi "" nghĩa là "không đổi mật khẩu" (form sửa user luôn
    # có ô "Mật khẩu mới" để trống); view bỏ qua bằng `if password:` → KHÔNG bao giờ
    # set_password("") ghi đè mật khẩu đang dùng. Độ dài/độ mạnh do validate_password
    # trong view kiểm tra theo AUTH_PASSWORD_VALIDATORS.
    password = serializers.CharField(required=False, allow_blank=True)
    department_id = serializers.IntegerField(required=False, allow_null=True)
    lark_open_id = serializers.CharField(max_length=128, allow_blank=True, required=False)
    zenify_email = serializers.EmailField(allow_blank=True, required=False, trim_whitespace=True)
    foxia_id = serializers.CharField(max_length=64, allow_blank=True, required=False)
    media_role = serializers.ChoiceField(choices=MediaRole.choices, required=False)
    media_api_key = serializers.CharField(max_length=1024, allow_blank=True, allow_null=True, required=False)
    role_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False)
    department_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False
    )

    def validate_department_id(self, value):
        if value is not None and not Department.objects.filter(id=value, is_active=True).exists():
            raise serializers.ValidationError("Phòng ban không tồn tại.")
        return value

    def validate_kiotviet_mappings(self, value):
        return _validate_kiot_mapping_list(value)

    def validate_foxia_id(self, value):
        # View truyền user đang sửa qua context để không tự báo trùng với chính mình.
        return _validate_unique_foxia_id(value, exclude_user_id=self.context.get("user_id"))

    def validate_media_api_key(self, value):
        return validate_media_api_key(value)


class AdminUserListResponseSerializer(serializers.Serializer):
    """Response GET /api/users/admin (phân trang) — chỉ dùng khai OpenAPI."""

    items = UserSerializer(many=True)
    total = serializers.IntegerField()


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ("id", "code", "section", "action", "label")


class RoleSerializer(serializers.ModelSerializer):
    permission_codes = serializers.SerializerMethodField()
    section_scopes = serializers.SerializerMethodField()
    field_rules = serializers.SerializerMethodField()
    field_grants = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = (
            "id",
            "name",
            "description",
            "is_system",
            "scope_default",
            "is_active",
            "created_at",
            "permission_codes",
            "section_scopes",
            "field_rules",
            "field_grants",
        )
        read_only_fields = ("id", "is_system", "created_at")

    # Bốn method dưới đọc qua .all() + sắp xếp bằng Python để dùng prefetch cache của
    # list_roles; .filter()/.order_by() mới trên related manager luôn đi DB lại, thành
    # 4 query MỖI role trên trang Phân quyền (audit 2026-07-26 T1). Không prefetch vẫn
    # đúng, chỉ chậm hơn — fallback an toàn cho endpoint tạo/sửa role trả về một bản ghi.
    def get_permission_codes(self, obj: Role) -> list[str]:
        return sorted(link.permission.code for link in obj.role_permissions.all())

    def get_section_scopes(self, obj: Role) -> dict[str, str]:
        return {scope.section: scope.scope for scope in obj.section_scopes.all()}

    @extend_schema_field(
        inline_serializer(
            "RoleFieldRuleItem",
            fields={
                "section": serializers.CharField(),
                "field": serializers.CharField(),
                "rule": serializers.CharField(),
            },
            many=True,
        )
    )
    def get_field_rules(self, obj: Role) -> list[dict]:
        rules = sorted(obj.field_rules.all(), key=lambda r: (r.section, r.field))
        return [
            {"section": r.section, "field": r.field, "rule": r.rule} for r in rules
        ]

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_field_grants(self, obj: Role) -> list[str]:
        return sorted(
            g.field for g in obj.field_grants.all() if g.section == ORDER_GRANT_SECTION
        )


class RoleWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=False)
    description = serializers.CharField(max_length=255, allow_blank=True, required=False)
    scope_default = serializers.ChoiceField(choices=DataScope.choices, required=False)
    is_active = serializers.BooleanField(required=False)
    permission_codes = serializers.ListField(child=serializers.CharField(), required=False)
    section_scopes = serializers.ListField(child=serializers.DictField(), required=False)
    field_rules = serializers.ListField(child=serializers.DictField(), required=False)
    field_grants = serializers.ListField(child=serializers.CharField(), required=False)

    def validate_field_grants(self, value):
        unknown = sorted(set(value) - ALL_ORDER_FIELDS)
        if unknown:
            raise serializers.ValidationError(f"Field không hợp lệ: {', '.join(unknown)}.")
        return value

    def validate_field_rules(self, value):
        valid_rules = {c for c, _ in FieldRuleType.choices}
        for item in value:
            if item.get("section") not in VALID_SECTIONS:
                raise serializers.ValidationError(
                    f"section không hợp lệ: {item.get('section')}"
                )
            if not item.get("field"):
                raise serializers.ValidationError("Thiếu field.")
            if item.get("rule") not in valid_rules:
                raise serializers.ValidationError(f"rule không hợp lệ: {item.get('rule')}")
        return value

    def validate_permission_codes(self, value):
        valid = set(Permission.objects.filter(code__in=value).values_list("code", flat=True))
        invalid = [c for c in value if c not in valid]
        if invalid:
            raise serializers.ValidationError(f"Permission code không hợp lệ: {invalid}")
        return list(dict.fromkeys(value))

    def validate_section_scopes(self, value):
        valid_scopes = {c for c, _ in DataScope.choices}
        for item in value:
            if item.get("section") not in VALID_SECTIONS:
                raise serializers.ValidationError(f"section không hợp lệ: {item.get('section')}")
            if item.get("scope") not in valid_scopes:
                raise serializers.ValidationError(f"scope không hợp lệ: {item.get('scope')}")
        return value


class TeamUserSerializer(serializers.ModelSerializer):
    """Payload gọn cho lead/thành viên — ô select không cần roles, kênh, mapping KV."""

    class Meta:
        model = User
        fields = ("id", "display_name", "username")


class DepartmentTeamSerializer(serializers.ModelSerializer):
    """Tên KHÔNG được là `TeamSerializer`: `hr` đã chiếm component `Team` trong
    `schema.yaml`. Trùng tên thì drf-spectacular âm thầm cho endpoint này trỏ vào
    schema của `hr` (lead là `EmployeeSummary`), frontend sinh sai kiểu mà không
    có lỗi nào báo.
    """

    # `allow_null` + `extend_schema_field`: thiếu chúng thì hợp đồng OpenAPI khai
    # `lead` non-null và `members` là `unknown[]`, frontend sinh type sai mà backend
    # vẫn chạy đúng — tsc bắt được lần này, đừng gỡ ra.
    lead = TeamUserSerializer(read_only=True, allow_null=True)
    members = serializers.SerializerMethodField()

    class Meta:
        model = Team
        fields = ("id", "name", "department", "lead", "members", "created_at")
        read_only_fields = ("id", "department", "created_at")

    @extend_schema_field(TeamUserSerializer(many=True))
    def get_members(self, obj: Team) -> list[dict]:
        # `.all()` + duyệt bằng Python để dùng prefetch `members__user`; `.filter()`
        # trên related manager LUÔN đi DB lại — đúng cái N+1 mà `get_channel_ids`
        # ở trên đã dẫm phải một lần.
        return TeamUserSerializer([link.user for link in obj.members.all()], many=True).data


class DepartmentTeamCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    lead_user_id = serializers.IntegerField(required=False, allow_null=True)
    member_user_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False
    )


class DepartmentTeamUpdateSerializer(serializers.Serializer):
    """Mọi field optional — chỉ key CÓ MẶT mới được ghi (ngữ nghĩa PATCH)."""

    name = serializers.CharField(max_length=100, required=False)
    lead_user_id = serializers.IntegerField(required=False, allow_null=True)
    member_user_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False
    )
