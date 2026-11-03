from __future__ import annotations

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.hr.models import (
    Employee,
    EmployeeChild,
    EmploymentStatus,
    MaritalStatus,
    Position,
    PositionLevel,
    WorkType,
)


class EmployeeChildSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeChild
        fields = ("id", "name", "birth_year")


class EmployeeAccountItemSerializer(serializers.Serializer):
    """Schema-only (OpenAPI): 1 tài khoản gắn với nhân sự (EmployeeSerializer.get_accounts)."""

    account_id = serializers.IntegerField()
    user_id = serializers.IntegerField()
    username = serializers.CharField()
    display_name = serializers.CharField(allow_blank=True)
    is_primary = serializers.BooleanField()
    is_active = serializers.BooleanField()


# Trường định danh/tài chính cá nhân: CHỈ người quản lý hồ sơ (`employees:edit`) hoặc
# chính chủ mới được đọc. `employees:view` được cấp rộng (sơ đồ tổ chức, danh bạ, chọn
# nhân sự trong form) — trả nguyên CCCD, ngày sinh, địa chỉ thường trú, số tài khoản,
# MST, sổ BHXH cho ngần ấy người là rò rỉ PII, và làm cho gate `salaries` (chặn đường
# lương) chỉ đúng một nửa vì số tài khoản vẫn lấy được qua /api/hr/employees
# (audit 2026-07-14 vòng 3).
SENSITIVE_EMPLOYEE_FIELDS = (
    "id_number",
    "id_issued_date",
    "id_issued_place",
    "permanent_address",
    "dob",
    "tax_code",
    "social_insurance_no",
    "bank_account_no",
    "bank_name",
)


class EmployeeSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True, default="")
    manager_name = serializers.CharField(source="manager.full_name", read_only=True, default="")
    username = serializers.SerializerMethodField()
    usernames = serializers.SerializerMethodField()
    accounts = serializers.SerializerMethodField()
    job_title_display = serializers.CharField(source="get_job_title_display", read_only=True)
    children = EmployeeChildSerializer(many=True, read_only=True)

    def get_username(self, obj) -> str:
        user = obj.primary_user
        return user.username if user else ""

    def get_usernames(self, obj) -> list[str]:
        return [a.user.username for a in obj.accounts.all()]

    @extend_schema_field(EmployeeAccountItemSerializer(many=True))
    def get_accounts(self, obj) -> list[dict]:
        return [
            {
                "account_id": a.id,
                "user_id": a.user_id,
                "username": a.user.username,
                "display_name": a.user.display_name,
                "is_primary": a.is_primary,
                "is_active": a.user.is_active,
            }
            for a in obj.accounts.all()
        ]

    def _may_read_sensitive(self, obj) -> bool:
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return False

        from apps.users.rbac import resolve_effective_permissions

        if resolve_effective_permissions(user).can("employees:edit"):
            return True
        # Chính chủ luôn xem được hồ sơ của mình (khớp /api/hr/me/*).
        return any(account.user_id == user.id for account in obj.accounts.all())

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self._may_read_sensitive(instance):
            for field in SENSITIVE_EMPLOYEE_FIELDS:
                data[field] = None
        return data

    class Meta:
        model = Employee
        fields = (
            "id", "employee_code", "full_name", "department", "department_name",
            "manager", "manager_name", "job_title", "job_title_display",
            "position_level", "position_title", "work_type", "lark_account",
            "employment_status", "hire_date", "official_date", "resign_date",
            "resign_reason", "personal_email", "phone", "dob", "gender",
            "marital_status", "id_number", "id_issued_date", "id_issued_place",
            "permanent_address", "address", "education_level", "major", "school",
            "tax_code", "social_insurance_no", "bank_account_no", "bank_name",
            "emergency_contact_name", "emergency_contact_relation",
            "emergency_contact_phone", "emergency_contact_address",
            "facebook_url", "vehicle_info", "children",
            "username", "usernames", "accounts", "created_at", "updated_at",
        )
        read_only_fields = ("id", "employee_code", "created_at", "updated_at")


class EmployeeCreateSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=150)
    department_id = serializers.IntegerField(required=False, allow_null=True)
    username = serializers.CharField(max_length=150)
    create_login = serializers.BooleanField(default=False)
    password = serializers.CharField(required=False, allow_blank=True, default="")
    employee_code = serializers.CharField(
        max_length=20, required=False, allow_blank=True, default=""
    )
    job_title = serializers.ChoiceField(
        choices=Position.choices, required=False, allow_blank=True, default=""
    )
    manager_id = serializers.IntegerField(required=False, allow_null=True)
    phone = serializers.CharField(required=False, allow_blank=True, default="")
    personal_email = serializers.EmailField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if attrs.get("create_login") and not attrs.get("password"):
            raise serializers.ValidationError("Cần mật khẩu khi tạo tài khoản đăng nhập.")
        return attrs


class EmployeeUpdateSerializer(serializers.Serializer):
    """Validates PATCH payloads for an employee and normalizes them to the
    field names expected by ``EmployeeService.update``'s whitelist.

    The read serializer (``EmployeeSerializer``) exposes the relations as
    ``department`` / ``manager`` (FK ids), so a client that GETs then PATCHes
    back would send those read-shape keys. To make the round-trip work we
    accept both shapes: ``to_internal_value`` maps incoming ``department`` ->
    ``department_id`` and ``manager`` -> ``manager_id`` (only when the canonical
    key is absent) before normal field validation runs.
    """

    full_name = serializers.CharField(required=False)
    employee_code = serializers.CharField(max_length=20, required=False)
    department_id = serializers.IntegerField(required=False, allow_null=True)
    manager_id = serializers.IntegerField(required=False, allow_null=True)
    job_title = serializers.ChoiceField(
        choices=Position.choices, required=False, allow_blank=True
    )
    employment_status = serializers.ChoiceField(
        choices=EmploymentStatus.choices, required=False
    )
    position_level = serializers.ChoiceField(
        choices=PositionLevel.choices, required=False, allow_blank=True
    )
    position_title = serializers.CharField(required=False, allow_blank=True)
    work_type = serializers.ChoiceField(
        choices=WorkType.choices, required=False, allow_blank=True
    )
    lark_account = serializers.CharField(required=False, allow_blank=True)
    hire_date = serializers.DateField(required=False, allow_null=True)
    official_date = serializers.DateField(required=False, allow_null=True)
    resign_date = serializers.DateField(required=False, allow_null=True)
    resign_reason = serializers.CharField(required=False, allow_blank=True)
    personal_email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    dob = serializers.DateField(required=False, allow_null=True)
    gender = serializers.CharField(required=False, allow_blank=True)
    marital_status = serializers.ChoiceField(
        choices=MaritalStatus.choices, required=False, allow_blank=True
    )
    id_number = serializers.CharField(required=False, allow_blank=True)
    id_issued_date = serializers.DateField(required=False, allow_null=True)
    id_issued_place = serializers.CharField(required=False, allow_blank=True)
    permanent_address = serializers.CharField(required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    education_level = serializers.CharField(required=False, allow_blank=True)
    major = serializers.CharField(required=False, allow_blank=True)
    school = serializers.CharField(required=False, allow_blank=True)
    tax_code = serializers.CharField(required=False, allow_blank=True)
    social_insurance_no = serializers.CharField(required=False, allow_blank=True)
    bank_account_no = serializers.CharField(required=False, allow_blank=True)
    bank_name = serializers.CharField(required=False, allow_blank=True)
    emergency_contact_name = serializers.CharField(required=False, allow_blank=True)
    emergency_contact_relation = serializers.CharField(required=False, allow_blank=True)
    emergency_contact_phone = serializers.CharField(required=False, allow_blank=True)
    emergency_contact_address = serializers.CharField(required=False, allow_blank=True)
    facebook_url = serializers.CharField(required=False, allow_blank=True)
    vehicle_info = serializers.CharField(required=False, allow_blank=True)

    def to_internal_value(self, data):
        # Accept the read-shape aliases ``department`` / ``manager`` too, but
        # never override an explicitly-provided canonical key.
        if hasattr(data, "copy"):
            data = data.copy()
        else:
            data = dict(data)
        if "department" in data and "department_id" not in data:
            data["department_id"] = data.pop("department")
        if "manager" in data and "manager_id" not in data:
            data["manager_id"] = data.pop("manager")
        return super().to_internal_value(data)
