"""Tests export bảng chấm công: domain workday + service tổng hợp + endpoint."""

from datetime import date, time
from decimal import Decimal
from io import BytesIO

import openpyxl
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.hr.application.services.attendance_export_service import build_month_export
from apps.hr.domain.workday import WorkSchedule, evaluate_workday
from apps.hr.models import AttendanceRecord, LeaveRequest, LeaveStatus, LeaveType
from apps.hr.tests.factories import create_employee
from apps.users.models import Department
from apps.users.tests.helpers import make_admin, make_user_with_sections

SCHEDULE = WorkSchedule(
    morning_start=time(8, 0),
    morning_end=time(12, 0),
    afternoon_start=time(13, 30),
    afternoon_end=time(17, 30),
)


class WorkdayDomainTests(SimpleTestCase):
    def test_full_day(self):
        r = evaluate_workday(time(7, 55), time(17, 35), SCHEDULE)
        self.assertEqual((r.late_minutes, r.early_minutes), (0, 0))
        self.assertEqual(r.worked_minutes, 480)
        self.assertEqual(r.workday_credit, Decimal("1.00"))

    def test_late_morning_deducts_partial_session(self):
        # Vào 8:20: sáng thiếu → 220'/480 = 0.46; chiều đủ 0.5 → 0.96
        r = evaluate_workday(time(8, 20), time(17, 30), SCHEDULE)
        self.assertEqual(r.late_minutes, 20)
        self.assertEqual(r.early_minutes, 0)
        self.assertEqual(r.worked_minutes, 460)
        self.assertEqual(r.workday_credit, Decimal("0.96"))

    def test_morning_only_is_half_day(self):
        r = evaluate_workday(time(8, 0), time(12, 0), SCHEDULE)
        self.assertEqual(r.workday_credit, Decimal("0.50"))
        self.assertEqual(r.early_minutes, 330)

    def test_afternoon_only_is_half_day(self):
        r = evaluate_workday(time(13, 30), time(17, 30), SCHEDULE)
        self.assertEqual(r.workday_credit, Decimal("0.50"))
        self.assertEqual(r.late_minutes, 330)

    def test_lunch_break_not_counted(self):
        # 11:00 → 14:00: sáng 60' + chiều 30' = 90'; công = 90/480 = 0.19
        r = evaluate_workday(time(11, 0), time(14, 0), SCHEDULE)
        self.assertEqual(r.worked_minutes, 90)
        self.assertEqual(r.workday_credit, Decimal("0.19"))

    def test_missing_punch_gives_zero(self):
        r = evaluate_workday(time(8, 0), None, SCHEDULE)
        self.assertEqual((r.worked_minutes, r.workday_credit), (0, Decimal("0")))

    def test_checkout_before_checkin_clamped(self):
        r = evaluate_workday(time(17, 0), time(8, 0), SCHEDULE)
        self.assertEqual(r.worked_minutes, 0)


class AttendanceExportServiceTests(TestCase):
    def setUp(self):
        dept = Department.objects.create(name="HCNS")
        self.emp = create_employee(
            employee_code="D001", full_name="Lê Thị Thương", department=dept
        )

    def test_every_day_of_month_has_a_row(self):
        summaries = build_month_export("2026-07")
        self.assertEqual(len(summaries), 1)
        self.assertEqual(len(summaries[0].day_rows), 31)

    def test_summary_totals(self):
        AttendanceRecord.objects.create(
            employee=self.emp, work_date=date(2026, 7, 1), check_in=time(8, 0), check_out=time(17, 30)
        )
        AttendanceRecord.objects.create(
            employee=self.emp, work_date=date(2026, 7, 2), check_in=time(8, 30), check_out=time(17, 0)
        )
        # Quên chấm ra
        AttendanceRecord.objects.create(
            employee=self.emp, work_date=date(2026, 7, 3), check_in=time(8, 0), check_out=None
        )
        LeaveRequest.objects.create(
            employee=self.emp,
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 7, 6),
            end_date=date(2026, 7, 7),
            days=2,
            status=LeaveStatus.APPROVED,
        )

        summary = build_month_export("2026-07")[0]
        self.assertEqual(summary.attended_days, 2)
        self.assertEqual(summary.total_late_minutes, 30)
        self.assertEqual(summary.total_early_minutes, 30)
        self.assertEqual(summary.missing_punch_days, 1)
        self.assertEqual(summary.leave_days, 2)
        # 01/07 đủ 1.0; 02/07 muộn 30' + về sớm 30' → (210+210)/480... sáng thiếu
        # 210/480=0.44, chiều thiếu 210/480=0.44 → 0.88; tổng 1.88
        self.assertEqual(summary.total_credit, Decimal("1.88"))

        leave_row = summary.day_rows[5]  # 06/07
        self.assertIn("Phép", leave_row["notes"])
        missing_row = summary.day_rows[2]  # 03/07
        self.assertIn("Quên chấm công", missing_row["notes"])
        sunday_row = summary.day_rows[4]  # 05/07/2026 là Chủ nhật
        self.assertIn("CN", sunday_row["notes"])

    def test_excludes_resigned_before_month(self):
        self.emp.resign_date = date(2026, 6, 30)
        self.emp.save(update_fields=["resign_date"])
        self.assertEqual(build_month_export("2026-07"), [])

    def test_excludes_resigned_without_date_and_soft_deleted(self):
        from django.utils import timezone

        from apps.hr.models import EmploymentStatus

        create_employee(
            employee_code="D098",
            full_name="Nghỉ thiếu ngày",
            employment_status=EmploymentStatus.RESIGNED,
        )
        create_employee(
            employee_code="D099", full_name="Xoá mềm", deleted_at=timezone.now()
        )
        codes = [s.employee_code for s in build_month_export("2026-07")]
        self.assertEqual(codes, ["D001"])


class AttendanceExportApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_requires_export_permission(self):
        nobody = make_user_with_sections("nobody", ["orders"])
        self.client.force_authenticate(user=nobody)
        r = self.client.get("/api/hr/attendance/export?month=2026-07")
        self.assertEqual(r.status_code, 403)

    def test_invalid_month_400(self):
        self.client.force_authenticate(user=make_admin("hr-admin"))
        r = self.client.get("/api/hr/attendance/export?month=07-2026")
        self.assertEqual(r.status_code, 400)

    def test_exports_workbook(self):
        dept = Department.objects.create(name="Kho")
        emp = create_employee(employee_code="D002", full_name="Nguyễn Văn A", department=dept)
        AttendanceRecord.objects.create(
            employee=emp, work_date=date(2026, 7, 1), check_in=time(8, 0), check_out=time(17, 30)
        )
        self.client.force_authenticate(user=make_admin("hr-admin2"))
        r = self.client.get("/api/hr/attendance/export?month=2026-07")
        self.assertEqual(r.status_code, 200)
        self.assertIn("cham-cong-2026-07.xlsx", r["Content-Disposition"])

        wb = openpyxl.load_workbook(BytesIO(r.content))
        self.assertEqual(wb.sheetnames, ["Chi tiết", "Tổng hợp"])
        detail = wb["Chi tiết"]
        self.assertEqual(detail.max_row, 1 + 31)  # header + đủ 31 ngày
        first = [c.value for c in detail[2]]
        self.assertEqual(first[0], "D002")
        self.assertEqual(first[5], "08:00")
        self.assertEqual(first[10], 1.0)
        summary_ws = wb["Tổng hợp"]
        srow = [c.value for c in summary_ws[2]]
        self.assertEqual(srow[3], 1.0)
        self.assertEqual(srow[4], 1)
