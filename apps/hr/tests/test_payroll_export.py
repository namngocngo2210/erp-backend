"""Tests cơ chế lương + sinh bảng lương tháng."""

from datetime import date, time
from decimal import Decimal
from io import BytesIO

import openpyxl
from django.test import TestCase
from rest_framework.test import APIClient

from apps.hr.application.services.payroll_export_service import build_month_payroll
from apps.hr.models import (
    AttendanceRecord,
    EmployeeCompensation,
    EmploymentStatus,
    SalaryRecord,
)
from apps.hr.tests.factories import create_employee
from apps.users.models import Department
from apps.users.tests.helpers import make_admin, make_user_with_sections


def _attend(employee, day, cin=time(8, 0), cout=time(17, 30)):
    AttendanceRecord.objects.create(
        employee=employee, work_date=day, check_in=cin, check_out=cout
    )


class PayrollServiceTests(TestCase):
    def setUp(self):
        dept = Department.objects.create(name="Kho")
        self.emp = create_employee(
            employee_code="D010",
            full_name="Nguyễn Văn B",
            department=dept,
            employment_status=EmploymentStatus.ACTIVE,
            bank_account_no="0123456789",
            bank_name="Techcombank",
        )
        SalaryRecord.objects.create(
            employee=self.emp, amount=Decimal("8000000"), effective_from=date(2026, 1, 1)
        )

    def test_salary_is_the_one_effective_in_the_exported_month(self):
        """Chốt bảng lương T7 vào đầu T8, sau khi đã có đợt tăng lương hiệu lực 01/08:
        cột lương cơ bản phải là lương CỦA THÁNG 7, không phải lương hiện tại."""
        SalaryRecord.objects.filter(employee=self.emp).update(is_current=False)
        SalaryRecord.objects.create(
            employee=self.emp,
            amount=Decimal("12000000"),
            effective_from=date(2026, 8, 1),
            is_current=True,
        )
        _attend(self.emp, date(2026, 7, 1))

        row = build_month_payroll("2026-07")[0]

        self.assertEqual(row.base_salary, Decimal("8000000"))

    def test_salary_uses_latest_record_effective_within_month(self):
        """Tăng lương giữa tháng → lấy bản ghi mới nhất còn ≤ cuối tháng."""
        SalaryRecord.objects.filter(employee=self.emp).update(is_current=False)
        SalaryRecord.objects.create(
            employee=self.emp,
            amount=Decimal("9500000"),
            effective_from=date(2026, 7, 15),
            is_current=True,
        )
        _attend(self.emp, date(2026, 7, 1))

        row = build_month_payroll("2026-07")[0]

        self.assertEqual(row.base_salary, Decimal("9500000"))

    def test_row_uses_salary_attendance_and_defaults(self):
        _attend(self.emp, date(2026, 7, 1))
        _attend(self.emp, date(2026, 7, 2), cin=time(8, 0), cout=time(12, 0))

        rows = build_month_payroll("2026-07")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.base_salary, Decimal("8000000"))
        self.assertEqual(row.official_workdays, Decimal("1.50"))
        self.assertEqual(row.probation_workdays, Decimal("0"))
        self.assertEqual(row.lunch_meals, 2)
        # Defaults khi chưa có bản ghi cơ chế
        self.assertEqual(row.standard_workdays, Decimal("26"))
        self.assertEqual(row.attendance_bonus, Decimal("300000"))
        self.assertEqual(row.bank_account_no, "0123456789")

    def test_probation_split_by_official_date(self):
        self.emp.official_date = date(2026, 7, 3)
        self.emp.save(update_fields=["official_date"])
        _attend(self.emp, date(2026, 7, 1))
        _attend(self.emp, date(2026, 7, 2))
        _attend(self.emp, date(2026, 7, 3))

        row = build_month_payroll("2026-07")[0]
        self.assertEqual(row.probation_workdays, Decimal("2.00"))
        self.assertEqual(row.official_workdays, Decimal("1.00"))

    def test_probation_without_official_date_goes_to_probation_column(self):
        self.emp.employment_status = EmploymentStatus.PROBATION
        self.emp.save(update_fields=["employment_status"])
        _attend(self.emp, date(2026, 7, 1))

        row = build_month_payroll("2026-07")[0]
        self.assertEqual(row.probation_workdays, Decimal("1.00"))
        self.assertEqual(row.official_workdays, Decimal("0"))

    def test_compensation_record_overrides_defaults(self):
        EmployeeCompensation.objects.create(
            employee=self.emp,
            standard_workdays=Decimal("24"),
            attendance_bonus=Decimal("0"),
            responsibility_allowance=Decimal("500000"),
            kpi_rate=Decimal("0.005"),
            social_insurance=Decimal("557550"),
        )
        _attend(self.emp, date(2026, 7, 1))
        row = build_month_payroll("2026-07")[0]
        self.assertEqual(row.standard_workdays, Decimal("24"))
        self.assertEqual(row.attendance_bonus, Decimal("0"))
        self.assertEqual(row.responsibility_allowance, Decimal("500000"))
        self.assertEqual(row.kpi_rate, Decimal("0.005"))
        self.assertEqual(row.social_insurance, Decimal("557550"))


class CompensationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        dept = Department.objects.create(name="HCNS")
        self.emp = create_employee(
            employee_code="D011", full_name="Trần Thị C", department=dept
        )

    def test_list_requires_salaries_permission(self):
        viewer = make_user_with_sections("viewer", ["orders"])
        self.client.force_authenticate(user=viewer)
        self.assertEqual(self.client.get("/api/hr/compensations").status_code, 403)

    def test_employees_section_alone_cannot_read_or_write_salaries(self):
        """Role HCNS có TOÀN BỘ action của section `employees` vẫn không được đụng lương:
        trang Cơ chế lương lộ lương cơ bản/BHXH nên phải gate bằng section `salaries`
        (audit 2026-07-14)."""
        hr_staff = make_user_with_sections("hr-staff", ["employees"])
        self.client.force_authenticate(user=hr_staff)

        self.assertEqual(self.client.get("/api/hr/compensations").status_code, 403)
        r = self.client.put(
            f"/api/hr/compensations/{self.emp.id}",
            {"standard_workdays": "26", "note": ""},
            format="json",
        )
        self.assertEqual(r.status_code, 403)

    def test_salaries_section_can_read_and_write(self):
        payroll_user = make_user_with_sections("payroll-user", ["salaries"])
        self.client.force_authenticate(user=payroll_user)

        self.assertEqual(self.client.get("/api/hr/compensations").status_code, 200)
        r = self.client.put(
            f"/api/hr/compensations/{self.emp.id}",
            {
                "standard_workdays": "26.0",
                "attendance_bonus": "0",
                "responsibility_allowance": "0",
                "lunch_per_meal": "0",
                "other_allowance": "0",
                "note": "",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200)

    def test_list_returns_defaults_when_no_record(self):
        self.client.force_authenticate(user=make_admin("hr-admin"))
        r = self.client.get("/api/hr/compensations")
        self.assertEqual(r.status_code, 200)
        item = r.data["items"][0]
        self.assertEqual(item["employee_code"], "D011")
        self.assertEqual(Decimal(item["standard_workdays"]), Decimal("26"))
        self.assertIsNone(item["base_salary"])

    def test_upsert_and_reread(self):
        self.client.force_authenticate(user=make_admin("hr-admin2"))
        payload = {
            "standard_workdays": "26.0",
            "attendance_bonus": "300000",
            "responsibility_allowance": "500000",
            "kpi_rate": "0.002",
            "lunch_per_meal": "30000",
            "other_allowance": "120000",
            "social_insurance": "557550",
            "note": "",
        }
        r = self.client.put(
            f"/api/hr/compensations/{self.emp.id}", payload, format="json"
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Decimal(r.data["responsibility_allowance"]), Decimal("500000"))
        # PUT lần 2 update bản ghi cũ (không tạo trùng — OneToOne).
        payload["responsibility_allowance"] = "600000"
        r2 = self.client.put(
            f"/api/hr/compensations/{self.emp.id}", payload, format="json"
        )
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(EmployeeCompensation.objects.count(), 1)
        self.assertEqual(
            EmployeeCompensation.objects.get().responsibility_allowance,
            Decimal("600000"),
        )

    def test_list_hides_resigned_and_deleted_employees(self):
        from django.utils import timezone

        create_employee(
            employee_code="D090",
            full_name="Đã nghỉ có ngày",
            employment_status=EmploymentStatus.RESIGNED,
            resign_date=date(2026, 6, 30),
        )
        # Data backfill: RESIGNED nhưng thiếu resign_date — vẫn phải ẩn.
        create_employee(
            employee_code="D091",
            full_name="Đã nghỉ thiếu ngày",
            employment_status=EmploymentStatus.RESIGNED,
        )
        create_employee(
            employee_code="D092", full_name="Đã xoá mềm", deleted_at=timezone.now()
        )
        self.client.force_authenticate(user=make_admin("hr-admin-filter"))
        r = self.client.get("/api/hr/compensations")
        codes = [item["employee_code"] for item in r.data["items"]]
        self.assertEqual(codes, ["D011"])

    def test_update_base_salary_creates_salary_record(self):
        self.client.force_authenticate(user=make_admin("hr-admin-sal1"))
        r = self.client.put(
            f"/api/hr/compensations/{self.emp.id}",
            {"base_salary": "7000000"},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Decimal(r.data["base_salary"]), Decimal("7000000"))
        record = SalaryRecord.objects.get(employee=self.emp, is_current=True)
        self.assertEqual(record.amount, Decimal("7000000"))
        self.assertEqual(record.effective_from, date.today())

    def test_update_base_salary_same_day_corrects_in_place(self):
        # Thêm lương hôm nay xong nhập nhầm → sửa lại phải ăn, không nhân đôi bản ghi.
        self.client.force_authenticate(user=make_admin("hr-admin-sal2"))
        for amount in ("80000000", "8000000"):
            r = self.client.put(
                f"/api/hr/compensations/{self.emp.id}",
                {"base_salary": amount},
                format="json",
            )
            self.assertEqual(r.status_code, 200)
        self.assertEqual(SalaryRecord.objects.filter(employee=self.emp).count(), 1)
        record = SalaryRecord.objects.get(employee=self.emp)
        self.assertEqual(record.amount, Decimal("8000000"))
        self.assertTrue(record.is_current)

    def test_update_without_base_salary_keeps_salary(self):
        SalaryRecord.objects.create(
            employee=self.emp, amount=Decimal("6000000"), effective_from=date(2026, 1, 1)
        )
        self.client.force_authenticate(user=make_admin("hr-admin-sal3"))
        r = self.client.put(
            f"/api/hr/compensations/{self.emp.id}",
            {"standard_workdays": "24"},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Decimal(r.data["base_salary"]), Decimal("6000000"))
        self.assertEqual(SalaryRecord.objects.filter(employee=self.emp).count(), 1)

    def test_update_missing_employee_404(self):
        self.client.force_authenticate(user=make_admin("hr-admin3"))
        r = self.client.put("/api/hr/compensations/999999", {}, format="json")
        self.assertEqual(r.status_code, 404)


class PayrollExportApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_requires_export_permission(self):
        nobody = make_user_with_sections("nobody", ["orders"])
        self.client.force_authenticate(user=nobody)
        r = self.client.get("/api/hr/payroll/export?month=2026-07")
        self.assertEqual(r.status_code, 403)

    def test_employees_export_alone_cannot_download_payroll(self):
        """File bảng lương chứa lương + BHXH + SỐ TÀI KHOẢN ngân hàng toàn công ty →
        gate `salaries:export`, không phải `employees:export` (audit 2026-07-14)."""
        hr_staff = make_user_with_sections("hr-exporter", ["employees"])
        self.client.force_authenticate(user=hr_staff)
        r = self.client.get("/api/hr/payroll/export?month=2026-07")
        self.assertEqual(r.status_code, 403)

    def test_salaries_export_can_download_payroll(self):
        payroll_user = make_user_with_sections("payroll-exporter", ["salaries"])
        self.client.force_authenticate(user=payroll_user)
        r = self.client.get("/api/hr/payroll/export?month=2026-07")
        self.assertEqual(r.status_code, 200)

    def test_export_workbook_structure(self):
        dept = Department.objects.create(name="Sale")
        emp = create_employee(
            employee_code="D012",
            full_name="Phạm Văn D",
            department=dept,
            employment_status=EmploymentStatus.ACTIVE,
        )
        SalaryRecord.objects.create(
            employee=emp, amount=Decimal("9000000"), effective_from=date(2026, 1, 1)
        )
        _attend(emp, date(2026, 7, 1))

        self.client.force_authenticate(user=make_admin("hr-admin4"))
        r = self.client.get("/api/hr/payroll/export?month=2026-07")
        self.assertEqual(r.status_code, 200)
        self.assertIn("bang-luong-2026-07.xlsx", r["Content-Disposition"])

        wb = openpyxl.load_workbook(BytesIO(r.content))
        ws = wb["T07.2026"]
        self.assertEqual(ws.cell(row=2, column=1).value, "STT")
        self.assertEqual(ws.cell(row=3, column=3).value, "Phạm Văn D")
        self.assertEqual(ws.cell(row=3, column=7).value, 9000000.0)
        self.assertEqual(ws.cell(row=3, column=5).value, 1.0)
        self.assertEqual(ws.cell(row=3, column=12).value, 1)
        self.assertEqual(ws.cell(row=3, column=13).value, "=L3*30000")
        self.assertTrue(str(ws.cell(row=3, column=17).value).startswith("=G3/F3*D3*0.85"))
        self.assertEqual(ws.cell(row=4, column=3).value, "Tổng")
        # Khối doanh thu: nhãn neo cho công thức KPI
        self.assertEqual(ws.cell(row=7, column=15).value, "Tổng doanh thu thuần")

    def test_invalid_month_400(self):
        self.client.force_authenticate(user=make_admin("hr-admin5"))
        r = self.client.get("/api/hr/payroll/export?month=xx")
        self.assertEqual(r.status_code, 400)

    def test_user_controlled_text_is_not_evaluated_as_formula(self):
        """Tên NV / tên ngân hàng bắt đầu bằng '=' không được thành công thức Excel
        (audit 2026-07-14); công thức CHỦ ĐÍCH của bảng lương vẫn giữ nguyên."""
        dept = Department.objects.create(name="Sale2")
        emp = create_employee(
            employee_code="D013",
            full_name="=HYPERLINK(\"http://evil\",\"click\")",
            department=dept,
            employment_status=EmploymentStatus.ACTIVE,
            bank_name="=cmd|'/c calc'!A1",
        )
        SalaryRecord.objects.create(
            employee=emp, amount=Decimal("9000000"), effective_from=date(2026, 1, 1)
        )
        _attend(emp, date(2026, 7, 1))

        self.client.force_authenticate(user=make_admin("hr-admin6"))
        r = self.client.get("/api/hr/payroll/export?month=2026-07")
        self.assertEqual(r.status_code, 200)

        ws = openpyxl.load_workbook(BytesIO(r.content))["T07.2026"]
        name_cell = ws.cell(row=3, column=3)
        bank_cell = ws.cell(row=3, column=25)
        self.assertEqual(name_cell.data_type, "s", "tên NV phải là text, không phải formula")
        self.assertEqual(bank_cell.data_type, "s")
        self.assertEqual(name_cell.value, '=HYPERLINK("http://evil","click")', "giá trị giữ nguyên")
        # Công thức do code sinh vẫn là công thức thật.
        self.assertEqual(ws.cell(row=3, column=23).data_type, "f")
