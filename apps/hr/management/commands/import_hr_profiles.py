"""Backfill hồ sơ nhân sự từ seed JSON (sinh từ file HCNS_DESI.xlsx).

Idempotent: khớp theo ``employee_code`` (mã D đã khử trùng), chạy lại sẽ cập
nhật đúng bản ghi cũ, không tạo trùng. Tạo Department theo tên nếu chưa có.
KHÔNG tạo ``User`` — hồ sơ nhập về chưa gắn tài khoản (quan hệ 1-N qua
``EmployeeAccount`` sẽ gắn sau khi đối soát).

Usage::

    python manage.py import_hr_profiles            # dùng seed mặc định
    python manage.py import_hr_profiles --path /tmp/seed.json
    python manage.py import_hr_profiles --dry-run
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.hr.models import Employee, EmployeeChild
from apps.users.models import Department

DEFAULT_SEED = Path(__file__).resolve().parents[2] / "data" / "employee_profiles_seed.json"

# Field vô hướng ánh xạ 1-1 từ key JSON -> field model.
SCALAR_FIELDS = (
    "full_name", "lark_account", "gender", "position_level", "position_title",
    "work_type", "employment_status", "phone", "personal_email", "id_number",
    "id_issued_place", "permanent_address", "address", "education_level",
    "major", "school", "tax_code", "social_insurance_no", "bank_account_no",
    "bank_name", "emergency_contact_name", "emergency_contact_relation",
    "emergency_contact_phone", "emergency_contact_address", "marital_status",
    "facebook_url", "vehicle_info", "resign_reason",
)
DATE_FIELDS = ("hire_date", "official_date", "resign_date", "dob", "id_issued_date")


def _date(value: str | None) -> datetime.date | None:
    if not value:
        return None
    return datetime.date.fromisoformat(value)


class Command(BaseCommand):
    help = "Backfill hồ sơ nhân sự từ seed JSON (file HCNS chuẩn)."

    def add_arguments(self, parser):
        parser.add_argument("--path", default=str(DEFAULT_SEED), help="Đường dẫn seed JSON")
        parser.add_argument("--dry-run", action="store_true", help="Chỉ in thống kê, không ghi DB")

    def handle(self, *args, **opts):
        path = Path(opts["path"])
        if not path.exists():
            raise CommandError(f"Không tìm thấy seed: {path}")
        records = json.loads(path.read_text(encoding="utf-8"))
        self.stdout.write(f"Đọc {len(records)} bản ghi từ {path}")

        created = updated = children_total = 0
        dept_cache: dict[str, Department] = {}

        with transaction.atomic():
            for rec in records:
                dept = None
                dept_name = (rec.get("department_name") or "").strip()
                if dept_name:
                    dept = dept_cache.get(dept_name)
                    if dept is None:
                        dept, _ = Department.objects.get_or_create(name=dept_name)
                        dept_cache[dept_name] = dept

                fields = {k: rec.get(k, "") or "" for k in SCALAR_FIELDS}
                fields.update({k: _date(rec.get(k)) for k in DATE_FIELDS})
                fields["department"] = dept

                emp, was_created = Employee.objects.update_or_create(
                    employee_code=rec["employee_code"], defaults=fields
                )
                created += was_created
                updated += not was_created

                # Con cái: thay thế toàn bộ (idempotent).
                emp.children.all().delete()
                kids = [
                    EmployeeChild(
                        employee=emp,
                        name=(c.get("name") or "").strip(),
                        birth_year=c.get("birth_year"),
                    )
                    for c in rec.get("children", [])
                ]
                EmployeeChild.objects.bulk_create(kids)
                children_total += len(kids)

            if opts["dry_run"]:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("DRY-RUN: rollback, không ghi DB."))

        self.stdout.write(
            self.style.SUCCESS(
                f"Tạo mới {created}, cập nhật {updated}, con cái {children_total}, "
                f"phòng ban {len(dept_cache)}."
            )
        )
