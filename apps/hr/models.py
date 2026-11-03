from __future__ import annotations

from django.conf import settings
from django.db import models


class EmploymentStatus(models.TextChoices):
    PROBATION = "probation", "Thử việc"
    ACTIVE = "active", "Chính thức"
    ON_LEAVE = "on_leave", "Tạm nghỉ"
    RESIGNED = "resigned", "Đã nghỉ"


class Gender(models.TextChoices):
    MALE = "male", "Nam"
    FEMALE = "female", "Nữ"
    OTHER = "other", "Khác"


class Position(models.TextChoices):
    STAFF = "staff", "Nhân viên"
    MANAGER = "manager", "Trưởng phòng"
    INTERN = "intern", "Thực tập sinh"


class PositionLevel(models.TextChoices):
    """Cấp chức danh (cột "Chức danh" trong file HCNS)."""

    DIRECTOR = "director", "Ban giám đốc"
    TEAM_LEAD = "team_lead", "Trưởng nhóm"
    SPECIALIST = "specialist", "Chuyên viên"
    STAFF = "staff", "Nhân viên"
    COLLABORATOR = "collaborator", "Cộng tác viên"


class WorkType(models.TextChoices):
    FULLTIME = "fulltime", "Toàn thời gian"
    PARTTIME = "parttime", "Bán thời gian"
    REMOTE = "remote", "Remote"


class MaritalStatus(models.TextChoices):
    SINGLE = "single", "Độc thân"
    MARRIED = "married", "Đã kết hôn"
    DIVORCED = "divorced", "Đã ly hôn"
    OTHER = "other", "Khác"


class Employee(models.Model):
    # Quan hệ với tài khoản đăng nhập là 1-N: một nhân sự có thể gắn nhiều
    # ``User`` (xem ``EmployeeAccount``). FK do module ``hr`` sở hữu để giữ chiều
    # phụ thuộc hr → users (users là module nền tảng, không phụ thuộc ngược).
    # Partial unique trên nhân sự chưa xoá mềm (audit 2026-09-23 C8) — xem
    # Meta.constraints. Lưu ý: employee_service vẫn CHỦ ĐỘNG chặn trùng cả với
    # bản ghi đã xoá (all_with_deleted) — đó là chính sách nghiệp vụ HR, còn
    # ràng buộc DB chỉ bảo vệ tập đang sống.
    employee_code = models.CharField(max_length=20)
    full_name = models.CharField(max_length=150)
    department = models.ForeignKey(
        "users.Department", on_delete=models.PROTECT, related_name="employees",
        null=True, blank=True,
    )
    manager = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reports"
    )
    job_title = models.CharField(
        max_length=150, choices=Position.choices, blank=True, default=""
    )
    # --- Chức danh / hình thức làm việc (file HCNS) ---
    position_level = models.CharField(
        max_length=20, choices=PositionLevel.choices, blank=True, default=""
    )
    position_title = models.CharField(max_length=150, blank=True, default="")
    work_type = models.CharField(
        max_length=20, choices=WorkType.choices, blank=True, default=""
    )
    lark_account = models.CharField(max_length=150, blank=True, default="")
    employment_status = models.CharField(
        max_length=20, choices=EmploymentStatus.choices, default=EmploymentStatus.PROBATION
    )
    hire_date = models.DateField(null=True, blank=True)
    official_date = models.DateField(null=True, blank=True)
    resign_date = models.DateField(null=True, blank=True)
    resign_reason = models.CharField(max_length=500, blank=True, default="")
    personal_email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="")
    dob = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=Gender.choices, blank=True, default="")
    marital_status = models.CharField(
        max_length=20, choices=MaritalStatus.choices, blank=True, default=""
    )
    # --- Giấy tờ tuỳ thân ---
    id_number = models.CharField(max_length=20, blank=True, default="")
    id_issued_date = models.DateField(null=True, blank=True)
    id_issued_place = models.CharField(max_length=255, blank=True, default="")
    # --- Địa chỉ ---
    permanent_address = models.CharField(max_length=255, blank=True, default="")
    address = models.CharField(max_length=255, blank=True, default="")
    # --- Học vấn ---
    education_level = models.CharField(max_length=150, blank=True, default="")
    major = models.CharField(max_length=150, blank=True, default="")
    school = models.CharField(max_length=255, blank=True, default="")
    # --- Thuế / BHXH / ngân hàng ---
    tax_code = models.CharField(max_length=30, blank=True, default="")
    social_insurance_no = models.CharField(max_length=30, blank=True, default="")
    bank_account_no = models.CharField(max_length=40, blank=True, default="")
    bank_name = models.CharField(max_length=100, blank=True, default="")
    # --- Liên hệ khẩn cấp ---
    emergency_contact_name = models.CharField(max_length=150, blank=True, default="")
    emergency_contact_relation = models.CharField(max_length=50, blank=True, default="")
    emergency_contact_phone = models.CharField(max_length=20, blank=True, default="")
    emergency_contact_address = models.CharField(max_length=255, blank=True, default="")
    # --- Khác ---
    facebook_url = models.CharField(max_length=300, blank=True, default="")
    vehicle_info = models.CharField(max_length=255, blank=True, default="")
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "hr_employees"
        indexes = [
            models.Index(fields=["employment_status"], name="hr_emp_status_idx"),
            models.Index(fields=["deleted_at"], name="hr_emp_deleted_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["employee_code"],
                condition=models.Q(deleted_at__isnull=True),
                name="hr_emp_code_active_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee_code} · {self.full_name}"

    @property
    def primary_user(self):
        """Tài khoản chính (nếu có) — dùng để hiển thị/đăng nhập đại diện.

        Chọn account ``is_primary`` trước, sau đó account cũ nhất. Trả ``None``
        nếu nhân sự chưa gắn tài khoản nào (vd hồ sơ người đã nghỉ).
        """
        accounts = list(self.accounts.all())
        if not accounts:
            return None
        accounts.sort(key=lambda a: (not a.is_primary, a.id))
        return accounts[0].user


class EmployeeAccount(models.Model):
    """Bảng nối Employee 1-N User: một nhân sự gắn nhiều tài khoản đăng nhập.

    Mỗi ``User`` chỉ thuộc tối đa một nhân sự (``user`` là OneToOne). Bảng do
    module ``hr`` sở hữu nên chiều phụ thuộc luôn là hr → users.
    """

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="accounts"
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="employee_account",
    )
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "hr_employee_accounts"
        constraints = [
            models.UniqueConstraint(
                fields=["employee"],
                condition=models.Q(is_primary=True),
                name="hr_emp_acc_one_primary",
            ),
        ]

    def __str__(self) -> str:
        return f"emp={self.employee_id} ↔ user={self.user_id}"


class EmployeeChild(models.Model):
    """Thông tin con của nhân sự (0-N), tách bảng vì là quan hệ 1-nhiều."""

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="children"
    )
    name = models.CharField(max_length=150, blank=True, default="")
    birth_year = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "hr_employee_children"

    def __str__(self) -> str:
        return f"{self.name} ({self.birth_year or '?'})"


class ContractType(models.TextChoices):
    PROBATION = "probation", "Thử việc"
    OFFICIAL = "official", "Chính thức"
    SEASONAL = "seasonal", "Thời vụ"
    COLLABORATOR = "collaborator", "Cộng tác viên"


class EmploymentContract(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="contracts")
    contract_no = models.CharField(max_length=50, blank=True, default="")
    contract_type = models.CharField(max_length=20, choices=ContractType.choices)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    base_salary = models.DecimalField(max_digits=12, decimal_places=0, default=0)
    file_url = models.CharField(max_length=500, blank=True, default="")
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "hr_contracts"


class DepartmentHead(models.Model):
    """Trưởng phòng — 1 nhân sự phụ trách 1 phòng ban (``users.Department``).

    Model do ``hr`` sở hữu để trỏ tới ``Employee`` mà vẫn giữ chiều phụ thuộc
    hr → users (``users.Department`` không được biết ``hr.Employee``).
    """

    department = models.OneToOneField(
        "users.Department", on_delete=models.CASCADE, related_name="hr_head"
    )
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="heading")
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "hr_department_heads"

    def __str__(self) -> str:
        return f"dept={self.department_id} ← head emp={self.employee_id}"


class Team(models.Model):
    """Nhóm nhỏ trong một phòng ban (tên, team lead, thành viên)."""

    department = models.ForeignKey(
        "users.Department", on_delete=models.CASCADE, related_name="teams"
    )
    name = models.CharField(max_length=100)
    lead = models.ForeignKey(
        Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="leading_teams"
    )
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "hr_teams"
        indexes = [
            models.Index(fields=["deleted_at"], name="hr_team_deleted_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["department", "name"],
                condition=models.Q(deleted_at__isnull=True),
                name="hr_team_unique_name_per_dept",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} (dept={self.department_id})"


class TeamMember(models.Model):
    """Thành viên team (N-N Team↔Employee qua bảng nối)."""

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="members")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="team_memberships")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "hr_team_members"
        constraints = [
            models.UniqueConstraint(fields=["team", "employee"], name="hr_team_member_unique"),
        ]

    def __str__(self) -> str:
        return f"team={self.team_id} ↔ emp={self.employee_id}"


class SalaryRecord(models.Model):
    """Append-only: thay lương = tạo bản ghi mới, không sửa/xoá bản cũ."""

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="salaries")
    amount = models.DecimalField(max_digits=12, decimal_places=0)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    is_current = models.BooleanField(default=True)
    reason = models.CharField(max_length=255, blank=True, default="")
    # FK thật thay BigIntegerField trần (audit 2026-07 mục 3.8); giữ db_column cũ
    # nên attname vẫn là created_by_id — code gán created_by_id=... không đổi.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="created_by_id",
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "hr_salary_records"
        indexes = [
            models.Index(fields=["employee", "is_current"], name="hr_salary_cur_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["employee"], condition=models.Q(is_current=True),
                name="hr_salary_one_current_per_emp",
            ),
        ]


class LeaveType(models.TextChoices):
    ANNUAL = "annual", "Phép năm"
    SICK = "sick", "Nghỉ ốm"
    UNPAID = "unpaid", "Không lương"
    OTHER = "other", "Khác"


class LeaveStatus(models.TextChoices):
    PENDING = "pending", "Chờ duyệt"
    APPROVED = "approved", "Đã duyệt"
    REJECTED = "rejected", "Từ chối"
    CANCELLED = "cancelled", "Đã huỷ"


class LeaveRequest(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="leaves")
    leave_type = models.CharField(max_length=20, choices=LeaveType.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    reason = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=20, choices=LeaveStatus.choices, default=LeaveStatus.PENDING
    )
    # FK thật thay BigIntegerField trần (audit 2026-07 mục 3.8); giữ db_column cũ
    # nên attname vẫn là approver_id — code gán approver_id=... không đổi.
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="approver_id",
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "hr_leave_requests"
        indexes = [models.Index(fields=["employee", "status"], name="hr_leave_emp_idx")]


class AttendanceSource(models.TextChoices):
    """Nguồn ghi công (audit 2026-07 mục 3.7). Python-level choices, không constraint DB."""

    MANUAL = "manual", "Nhập tay"
    SELF = "self", "Tự chấm công (ESS)"
    LARK = "lark", "Đồng bộ Lark"


class AttendanceRecord(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="attendance")
    work_date = models.DateField()
    check_in = models.TimeField(null=True, blank=True)
    check_out = models.TimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True, default="")
    source = models.CharField(
        max_length=20, choices=AttendanceSource.choices, default=AttendanceSource.MANUAL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "hr_attendance"
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "work_date"], name="hr_attendance_unique_day"
            ),
        ]


class CompanyLocation(models.Model):
    name = models.CharField(max_length=150)
    latitude = models.FloatField()
    longitude = models.FloatField()
    radius_m = models.PositiveIntegerField(default=150)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "hr_company_locations"
        ordering = ["name"]


class AttendanceEventKind(models.TextChoices):
    CHECK_IN = "check_in", "Vào"
    CHECK_OUT = "check_out", "Ra"


class AttendanceEvent(models.Model):
    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="attendance_events"
    )
    kind = models.CharField(max_length=20, choices=AttendanceEventKind.choices)
    occurred_at = models.DateTimeField()
    work_date = models.DateField()
    photo_key = models.CharField(max_length=300)
    latitude = models.FloatField()
    longitude = models.FloatField()
    distance_m = models.IntegerField()
    location = models.ForeignKey(
        CompanyLocation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "hr_attendance_events"
        indexes = [
            models.Index(fields=["employee", "work_date"], name="hr_att_evt_emp_date_idx")
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "work_date", "kind"],
                name="hr_att_evt_unique_kind_per_day",
            ),
        ]


class EmployeeCompensation(models.Model):
    """Cơ chế lương hiện hành per nhân sự — input sinh bảng lương tháng.

    Lương cơ bản KHÔNG nằm ở đây (lấy từ ``SalaryRecord.is_current``). Các cột
    HCNS điền thủ công theo tháng (lương KPI, phạt đi muộn) cũng không lưu —
    xem docs/erp-hr-attendance-export.md phần Sprint 2.
    """

    employee = models.OneToOneField(
        Employee, on_delete=models.CASCADE, related_name="compensation"
    )
    standard_workdays = models.DecimalField(max_digits=4, decimal_places=1, default=26)
    # Chuyên cần: thưởng khi đủ công (sheet dùng IF(công>=24, mức, 0)); 0 = không có.
    attendance_bonus = models.DecimalField(max_digits=12, decimal_places=0, default=300000)
    responsibility_allowance = models.DecimalField(max_digits=12, decimal_places=0, default=0)
    # "Mức doanh số / tỉ lệ KPI" (cột J) — ngữ nghĩa tuỳ NV (tỉ lệ trên doanh thu
    # hoặc hệ số), chỉ prefill để HCNS sửa.
    kpi_rate = models.DecimalField(max_digits=10, decimal_places=5, null=True, blank=True)
    lunch_per_meal = models.DecimalField(max_digits=12, decimal_places=0, default=30000)
    other_allowance = models.DecimalField(max_digits=12, decimal_places=0, default=0)
    # BHXH khấu trừ hàng tháng; null = chưa đóng → ô để trống.
    social_insurance = models.DecimalField(
        max_digits=12, decimal_places=0, null=True, blank=True
    )
    note = models.CharField(max_length=255, blank=True, default="")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "hr_employee_compensations"
