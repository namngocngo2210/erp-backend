from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models

from apps.common.fields import EncryptedCharField


VALID_SECTIONS = [
    "orders",
    "landing-orders",
    "koc-orders",
    "customers",
    "contacts",
    "loyal-customers",
    "resale-tracking",
    "warranty",
    "warranty-codes",
    "dashboard",
    "ads-report",
    "overview-report",
    "call-quality",
    # Trang Khám phá dữ liệu (khối 2B). Không map sang "dashboard" như
    # customer-report — cố ý tách riêng, nên PHẢI có mặt ở đây tự thân, cùng vết
    # salaries/contact_flow (audit whole-branch 2026-08-20).
    "customer-explorer",
    "channels",
    "kiotviet",
    "facebook-ads",
    "google-ads",
    "foxia-report",
    # Trang Cấu hình liên kết Foxia — superuser-only, CỐ Ý không có trong
    # PERMISSION_CATALOG (cùng khuôn media-*). Vẫn phải có mặt ở đây vì
    # `UserSerializer.get_allowed_sections` trả `list(VALID_SECTIONS)` cho
    # superuser — thiếu là chính Administrator bị FE ẩn menu.
    "foxia-link",
    # Cài đặt → Pancake — superuser-only, CỐ Ý không có trong PERMISSION_CATALOG
    # (cùng khuôn foxia-link). Trang lead Pancake thì có quyền riêng bên dưới.
    "pancake",
    "pancake-leads",
    "pancake-pos",
    # Cài đặt → Cấu hình hệ thống (apps.sysconfig) — superuser-only, CỐ Ý không có
    # trong PERMISSION_CATALOG (cùng khuôn pancake/foxia-link). Phải có ở đây để
    # superuser không bị FE ẩn menu.
    "system-settings",
    "products",
    "product-categories",
    "warehouses",
    "stock",
    "stock-documents",
    "returns",
    "shipments",
    "users",
    "employees",
    # Lương/hợp đồng — section RBAC riêng, không có route riêng (trang Cơ chế lương
    # nằm trong module HR nhưng gate bằng section này). PHẢI có mặt ở đây:
    # `UserSerializer.get_allowed_sections` trả `list(VALID_SECTIONS)` cho superuser,
    # thiếu nó thì chính Administrator bị FE ẩn trang lương (audit 2026-07-14 vòng 2).
    "salaries",
    "lead-assignment",
    # Quyền đổi sale phụ trách (đơn hàng + khách thân thiết). Section RBAC riêng,
    # KHÔNG có route riêng — cố ý, để một chính sách công ty nằm ở một chỗ thay vì
    # rải thành 5 action trên 5 section đơn.
    "sale-assignment",
    # Xem đầy đủ SĐT khách (đơn của sale khác). Section RBAC riêng, KHÔNG có route
    # riêng — cùng khuôn sale-assignment ngay trên.
    "customer-phone",
    "floating-pool",
    # Cấu hình tác nghiệp CRM — thiếu ở đây thì superuser bị FE ẩn menu và role
    # không gán scope được (cùng vết salaries phía trên; tái diễn 2026-07-19).
    "contact_flow",
    "audit",
    # Tiện ích › Tra cứu địa chỉ (2 cấp / 3 cấp). Section riêng chỉ có "view".
    "address-lookup",
    # Module Media: menu hiện theo provisioning từng user (spec Media D5), KHÔNG có
    # trong PERMISSION_CATALOG. Vẫn phải có ở đây vì superuser nhận list(VALID_SECTIONS)
    # — thiếu là Administrator bị FE ẩn menu (cùng vết salaries/contact_flow).
    "media-explore",
    "media-contents",
    "media-proposals",
    "media-reports",
    "media-taxonomy",
    "media-settings",
]


class MediaRole(models.TextChoices):
    """Vai trò Media khi user dùng KEY CHUNG của ads-crm (spec Media §5.2).

    Giá trị phải khớp `apps.media.domain.roles.ROLES` — domain thuần không import
    Django nên enum nằm ở đây (users là tầng đáy, media import được users).
    """

    NONE = "", "Không có"
    VIEWER = "viewer", "Viewer"
    MARKETING = "marketing", "Marketing"
    UPLOADER = "uploader", "Uploader"
    ADMIN = "admin", "Admin"


class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "departments"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Team(models.Model):
    """Đội nhóm trong một phòng ban — gắn TÀI KHOẢN (`User`), không gắn hồ sơ nhân sự.

    Model do `users` sở hữu chứ không phải `hr`: đội nhóm là khái niệm vận hành
    (chia đơn, báo cáo), và 15/38 tài khoản không có hồ sơ `hr.Employee` nào nên
    neo vào hồ sơ là tự khoá mình khỏi phần lớn người dùng (đo prod 2026-08-23).
    Xem `docs/department-teams.md`.
    """

    # `teams` đang bị `hr.Team` chiếm (nhánh cũ không còn dùng ở giao diện nhưng
    # cố ý giữ nguyên — xem docs/department-teams.md §2.5). Đổi lại thành `teams`
    # khi nào dọn `hr`.
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, related_name="staff_teams"
    )
    name = models.CharField(max_length=100)
    # Trưởng NHÓM — khác hẳn trưởng PHÒNG (`hr.DepartmentHead`), đừng gộp hai khái niệm.
    # SET_NULL chứ không PROTECT: vô hiệu hoá tài khoản trưởng nhóm là việc thường
    # ngày của HCNS, không được để nó nổ giữa chừng. Nhóm mất lead vẫn hợp lệ.
    lead = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leading_teams",
    )
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "teams"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["department"], name="team_dept_idx"),
            models.Index(fields=["deleted_at"], name="team_deleted_idx"),
        ]
        constraints = [
            # Nhóm đã xoá mềm KHÔNG chiếm chỗ tên: xoá "Nhóm 1" rồi tạo lại phải được.
            models.UniqueConstraint(
                fields=["department", "name"],
                condition=models.Q(deleted_at__isnull=True),
                name="team_unique_name_per_dept",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} (dept={self.department_id})"


class TeamMember(models.Model):
    """Thành viên nhóm. Một người được ở NHIỀU nhóm — quyết định nghiệp vụ đã chốt."""

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="team_memberships"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "team_members"
        indexes = [
            models.Index(fields=["team"], name="team_member_team_idx"),
            models.Index(fields=["user"], name="team_member_user_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["team", "user"], name="team_member_unique"),
        ]

    def __str__(self) -> str:
        return f"team={self.team_id} ↔ user={self.user_id}"


class User(AbstractUser):
    department = models.ForeignKey(
        Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="members"
    )
    display_name = models.CharField(max_length=150, blank=True, default="")
    avatar_data_url = models.TextField(blank=True, default="")
    old_kiot_id = models.CharField(max_length=100, blank=True, default="")
    new_kiot_id = models.CharField(max_length=100, blank=True, default="")
    lark_open_id = models.CharField(max_length=128, blank=True, default="")
    # Email agent trên tổng đài Zenify (admin nhập ở form Người dùng). Rỗng →
    # callcenter fallback env ZENIFY_AGENT_EMAIL rồi tới ``email`` ERP.
    zenify_email = models.EmailField(blank=True, default="")
    # ID tài khoản bên Foxia (res.users) ứng với user này — admin nhập tay ở form
    # Người dùng để đối chiếu đơn/lead Foxia (user_id, nhanvienmarketing_id) về
    # người nội bộ. Rỗng = chưa liên kết. Không ràng buộc unique ở DB vì rỗng
    # là số đông; serializer chặn trùng giữa các user chưa xoá.
    foxia_id = models.CharField(max_length=64, blank=True, default="")
    # Module Media (spec 2026-09-04 §4.2). Có key riêng → bỏ qua media_role.
    media_role = models.CharField(max_length=16, choices=MediaRole.choices, blank=True, default="")
    media_api_key = EncryptedCharField(max_length=1024, blank=True, default="")
    last_no_answer_reminder_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    # Tăng mỗi khi đổi mật khẩu để vô hiệu hóa toàn bộ token đã phát hành trước
    # đó (đăng xuất các phiên khác). Xem apps/users/jwt.py.
    token_version = models.PositiveIntegerField(default=0)

    class Meta(AbstractUser.Meta):
        db_table = "users"


class UserKiotvietMapping(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="kiotviet_mappings")
    kiotviet_channel = models.ForeignKey(
        "kiotviet.KiotVietChannel",
        on_delete=models.CASCADE,
        related_name="user_mappings",
    )
    external_kiot_user_id = models.CharField(
        max_length=100,
        help_text="ID user bên KiotViet (soldById) tương ứng với cặp (User, KiotVietChannel) này",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_kiotviet_mappings"
        unique_together = ("user", "kiotviet_channel")
        indexes = [
            models.Index(
                fields=["kiotviet_channel", "external_kiot_user_id"],
                name="ukvm_kv_extid_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} ↔ {self.kiotviet_channel_id} = {self.external_kiot_user_id}"


class PermissionAction(models.TextChoices):
    VIEW = "view", "Xem"
    CREATE = "create", "Tạo"
    EDIT = "edit", "Sửa"
    DELETE = "delete", "Xóa"
    APPROVE = "approve", "Duyệt"
    EXPORT = "export", "Xuất"


class DataScope(models.TextChoices):
    ALL = "all", "Toàn bộ"
    DEPARTMENT = "department", "Theo phòng ban"
    CHANNEL = "channel", "Theo kênh"
    OWN = "own", "Của tôi"


class FieldRuleType(models.TextChoices):
    HIDDEN = "hidden", "Ẩn"
    READONLY = "readonly", "Chỉ đọc"


class Permission(models.Model):
    """Catalog quyền tĩnh, seed từ rbac_catalog. Không cho user tự tạo."""

    code = models.CharField(max_length=100, unique=True)  # dạng "orders:edit"
    section = models.CharField(max_length=50)
    action = models.CharField(max_length=20, choices=PermissionAction.choices)
    label = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rbac_permissions"
        unique_together = ("section", "action")
        ordering = ["section", "action"]
        indexes = [
            models.Index(fields=["section"], name="rbacperm_section_idx"),
        ]

    def __str__(self) -> str:
        return self.code


class Role(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255, blank=True, default="")
    # is_system=True → role hệ thống (Administrator), short-circuit full quyền, khóa sửa/xóa.
    is_system = models.BooleanField(default=False)
    scope_default = models.CharField(
        max_length=20, choices=DataScope.choices, default=DataScope.OWN
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rbac_roles"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class RolePermission(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_permissions")
    permission = models.ForeignKey(
        Permission, on_delete=models.CASCADE, related_name="role_links"
    )

    class Meta:
        db_table = "rbac_role_permissions"
        unique_together = ("role", "permission")
        indexes = [
            models.Index(fields=["role", "permission"], name="rbac_rp_role_perm_idx"),
        ]


class RoleSectionScope(models.Model):
    """ABAC override data-scope theo section cho 1 role (đè scope_default của role)."""

    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="section_scopes")
    section = models.CharField(max_length=50)
    scope = models.CharField(max_length=20, choices=DataScope.choices)

    class Meta:
        db_table = "rbac_role_section_scopes"
        unique_together = ("role", "section")
        indexes = [
            models.Index(fields=["role", "section"], name="rbac_rss_role_sec_idx"),
        ]


class RoleFieldRule(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="field_rules")
    section = models.CharField(max_length=50)
    field = models.CharField(max_length=100)
    rule = models.CharField(max_length=20, choices=FieldRuleType.choices)

    class Meta:
        db_table = "rbac_role_field_rules"
        unique_together = ("role", "section", "field")


class RoleFieldGrant(models.Model):
    """Allow-list: role được SỬA field nào. Vắng mặt = KHÔNG được sửa (fail-closed).

    Khác `RoleFieldRule` ở ngay chiều ngữ nghĩa: `RoleFieldRule` là deny-list cho
    việc XEM (ẩn field khỏi output), bảng này là allow-list cho việc GHI. Cố ý tách
    bảng thay vì thêm rule type — nhét hai chiều ngược nhau vào cùng một map
    field→rule là mời `drop_hidden_fields` đọc nhầm, và không test nào bắt được.

    Lưu tên field THẬT (đã bung khỏi nhóm nghiệp vụ ở UI). Lưu tên nhóm thì thêm
    field mới vào nhóm là mọi role có nhóm đó tự động sửa được field mới —
    fail-open lẻn vào cửa sau.
    """

    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="field_grants")
    section = models.CharField(max_length=50)
    field = models.CharField(max_length=100)

    class Meta:
        db_table = "rbac_role_field_grants"
        unique_together = ("role", "section", "field")
        indexes = [
            models.Index(fields=["role", "section"], name="rbac_rfg_role_sec_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.role_id}:{self.section}.{self.field}"


class UserRoleAssignment(models.Model):
    """Gán role cho user (spec gọi là UserRole; đổi tên Python để khỏi đụng enum UserRole)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="role_assignments"
    )
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="user_assignments")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rbac_user_roles"
        unique_together = ("user", "role")
        indexes = [
            models.Index(fields=["user"], name="rbac_ur_user_idx"),
        ]


class UserDepartment(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="department_links"
    )
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, related_name="user_links"
    )
    is_primary = models.BooleanField(default=False)

    class Meta:
        db_table = "rbac_user_departments"
        unique_together = ("user", "department")
        indexes = [
            models.Index(fields=["user"], name="rbac_ud_user_idx"),
        ]
