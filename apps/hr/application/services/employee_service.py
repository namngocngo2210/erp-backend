from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.common.events import event_bus
from apps.hr.contracts.hr_events import (
    EmployeeCreated,
    EmployeeDepartmentChanged,
    EmployeeResigned,
)
from apps.hr.domain.employee_code import next_employee_code
from apps.hr.domain.org_chart import assert_no_cycle
from apps.hr.models import Employee, EmployeeAccount, EmploymentStatus
from apps.hr.repositories import EmployeeRepository


class DuplicateEmployeeCodeError(ValueError):
    pass


class AccountAlreadyLinkedError(ValueError):
    """Tài khoản đã được gắn cho một nhân sự khác (quan hệ user↔employee là 1-1)."""

    pass


def _primary_user_id(emp: Employee) -> int:
    """ID tài khoản chính của nhân sự (0 nếu chưa gắn tài khoản nào)."""
    user = emp.primary_user
    return user.id if user else 0


def _create_user(
    *, username: str, create_login: bool, password: str | None, display_name: str
):
    from apps.users.models import User  # local import: tránh phụ thuộc cứng lúc import

    user = User(username=username, display_name=display_name)
    if create_login and password:
        user.is_active = True
        user.set_password(password)
    else:
        user.is_active = False
        user.set_unusable_password()
    user.save()
    return user


class EmployeeService:
    @staticmethod
    @transaction.atomic
    def create(
        *,
        full_name: str,
        department_id: int | None,
        username: str,
        create_login: bool = False,
        password: str | None = None,
        job_title: str = "",
        manager_id: int | None = None,
        employment_status: str = EmploymentStatus.PROBATION,
        employee_code: str | None = None,
        **extra: Any,
    ) -> Employee:
        user = _create_user(
            username=username,
            create_login=create_login,
            password=password,
            display_name=full_name,
        )
        custom_code = (employee_code or "").strip()
        if custom_code:
            if (
                EmployeeRepository.all_with_deleted()
                .filter(employee_code=custom_code)
                .exists()
            ):
                raise DuplicateEmployeeCodeError("Mã nhân viên đã tồn tại.")
            code = custom_code
        else:
            code = next_employee_code(EmployeeRepository.latest_code())
        if manager_id is not None:
            assert_no_cycle(
                employee_id=-1,
                new_manager_id=manager_id,
                parent_of=EmployeeRepository.parent_map(),
            )
        emp = Employee.objects.create(
            employee_code=code,
            full_name=full_name,
            department_id=department_id,
            manager_id=manager_id,
            job_title=job_title,
            employment_status=employment_status,
            personal_email=extra.get("personal_email", ""),
            phone=extra.get("phone", ""),
            id_number=extra.get("id_number", ""),
            address=extra.get("address", ""),
            gender=extra.get("gender", ""),
            dob=extra.get("dob"),
            hire_date=extra.get("hire_date"),
        )
        # Tài khoản đầu tiên là tài khoản chính (quan hệ 1-N với User).
        EmployeeAccount.objects.create(employee=emp, user=user, is_primary=True)
        event_bus.publish_on_commit(
            EmployeeCreated(
                employee_id=emp.id, user_id=user.id, department_id=department_id
            )
        )
        return emp

    @staticmethod
    @transaction.atomic
    def update(employee_id: int, data: dict[str, Any]) -> Employee:
        emp = EmployeeRepository.active().select_for_update().get(pk=employee_id)
        dept_changed = (
            "department_id" in data and data["department_id"] != emp.department_id
        )
        if "manager_id" in data:
            assert_no_cycle(
                employee_id=emp.id,
                new_manager_id=data["manager_id"],
                parent_of=EmployeeRepository.parent_map(),
            )
        if "employee_code" in data:
            new_code = (data["employee_code"] or "").strip()
            if not new_code:
                raise DuplicateEmployeeCodeError("Mã nhân viên không được để trống.")
            if new_code != emp.employee_code:
                if (
                    EmployeeRepository.all_with_deleted()
                    .filter(employee_code=new_code)
                    .exclude(pk=employee_id)
                    .exists()
                ):
                    raise DuplicateEmployeeCodeError("Mã nhân viên đã tồn tại.")
                data["employee_code"] = new_code
        allowed = {
            "full_name",
            "employee_code",
            "department_id",
            "manager_id",
            "job_title",
            "position_level",
            "position_title",
            "work_type",
            "lark_account",
            "employment_status",
            "hire_date",
            "official_date",
            "resign_date",
            "resign_reason",
            "personal_email",
            "phone",
            "dob",
            "gender",
            "marital_status",
            "id_number",
            "id_issued_date",
            "id_issued_place",
            "permanent_address",
            "address",
            "education_level",
            "major",
            "school",
            "tax_code",
            "social_insurance_no",
            "bank_account_no",
            "bank_name",
            "emergency_contact_name",
            "emergency_contact_relation",
            "emergency_contact_phone",
            "emergency_contact_address",
            "facebook_url",
            "vehicle_info",
        }
        for key, value in data.items():
            if key in allowed:
                setattr(emp, key, value)
        emp.save()
        if dept_changed:
            # Dọn vai trò tổ chức GẮN VỚI PHÒNG CŨ ngay trong transaction: không dọn thì
            # DepartmentHead/TeamMember mồ côi — trưởng phòng Sales chuyển sang Booking
            # vẫn duyệt được đơn nghỉ của Sales, vẫn là lead/member team phòng cũ, phá
            # bất biến "thành viên phải thuộc phòng ban" (audit 2026-07-15b).
            from apps.hr.models import DepartmentHead, Team, TeamMember

            DepartmentHead.objects.filter(employee=emp).exclude(
                department_id=emp.department_id
            ).delete()
            TeamMember.objects.filter(employee=emp).exclude(
                team__department_id=emp.department_id
            ).delete()
            Team.objects.filter(lead=emp).exclude(
                department_id=emp.department_id
            ).update(lead=None)

            event_bus.publish_on_commit(
                EmployeeDepartmentChanged(
                    employee_id=emp.id,
                    user_id=_primary_user_id(emp),
                    department_id=emp.department_id,
                )
            )
        return emp

    @staticmethod
    @transaction.atomic
    def delete(employee_id: int) -> Employee:
        from apps.users.models import User  # local import: giữ ranh giới module

        emp = EmployeeRepository.active().select_for_update().get(pk=employee_id)
        emp.deleted_at = timezone.now()
        emp.save(update_fields=["deleted_at"])
        User.objects.filter(employee_account__employee_id=emp.id).update(is_active=False)
        event_bus.publish_on_commit(
            EmployeeResigned(employee_id=emp.id, user_id=_primary_user_id(emp))
        )
        return emp

    @staticmethod
    @transaction.atomic
    def add_account(employee_id: int, validated: dict) -> EmployeeAccount:
        from apps.users.services import UserService  # local import: tránh phụ thuộc cứng

        user = UserService.create_admin_user(validated)
        is_primary = not EmployeeAccount.objects.filter(employee_id=employee_id).exists()
        return EmployeeAccount.objects.create(
            employee_id=employee_id, user=user, is_primary=is_primary
        )

    @staticmethod
    @transaction.atomic
    def link_existing_account(employee_id: int, user_id: int) -> EmployeeAccount:
        """Gắn một User đã tồn tại (chưa thuộc nhân sự nào) vào nhân sự này.

        Raise ``User.DoesNotExist`` nếu user không tồn tại/đã xoá,
        ``AccountAlreadyLinkedError`` nếu user đã gắn nhân sự khác.
        Tài khoản đầu tiên của nhân sự là primary, còn lại là phụ.
        """
        from apps.users.models import User

        user = User.objects.filter(pk=user_id, deleted_at__isnull=True).first()
        if user is None:
            raise User.DoesNotExist
        if EmployeeAccount.objects.filter(user_id=user_id).exists():
            raise AccountAlreadyLinkedError(
                "Tài khoản đã được gắn cho một nhân sự khác."
            )
        is_primary = not EmployeeAccount.objects.filter(employee_id=employee_id).exists()
        return EmployeeAccount.objects.create(
            employee_id=employee_id, user=user, is_primary=is_primary
        )

    @staticmethod
    @transaction.atomic
    def unlink_account(employee_id: int, account_id: int) -> None:
        acc = EmployeeAccount.objects.get(id=account_id, employee_id=employee_id)
        was_primary = acc.is_primary
        acc.delete()  # chỉ gỡ liên kết; User được giữ nguyên
        if was_primary:
            nxt = (
                EmployeeAccount.objects.filter(employee_id=employee_id)
                .order_by("created_at")
                .first()
            )
            if nxt:
                nxt.is_primary = True
                nxt.save(update_fields=["is_primary"])

    @staticmethod
    @transaction.atomic
    def resign(employee_id: int) -> Employee:
        from apps.users.models import User

        emp = EmployeeRepository.active().select_for_update().get(pk=employee_id)
        emp.employment_status = EmploymentStatus.RESIGNED
        if emp.resign_date is None:
            emp.resign_date = timezone.localdate()
        emp.save(update_fields=["employment_status", "resign_date", "updated_at"])
        User.objects.filter(employee_account__employee_id=emp.id).update(is_active=False)
        event_bus.publish_on_commit(
            EmployeeResigned(employee_id=emp.id, user_id=_primary_user_id(emp))
        )
        return emp
