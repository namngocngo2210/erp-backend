"""Trần dòng `?limit=` cho GET /api/hr/employees/<id>/attendance khi không lọc tháng
(audit 2026-09-23 L6) — response giữ `{items}`, mới nhất trước."""

from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase
from rest_framework.test import APIClient

from apps.hr.application.services.attendance_service import AttendanceService
from apps.hr.tests.factories import create_employee
from apps.users.tests.helpers import make_admin


class EmployeeAttendanceLimitTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=make_admin("att-limit-admin"))
        self.emp = create_employee(employee_code="NV-ATT-LIM", full_name="Limit")
        base = date(2026, 6, 1)
        for i in range(3):
            AttendanceService.record(self.emp.id, work_date=base + timedelta(days=i))
        self.url = f"/api/hr/employees/{self.emp.id}/attendance"

    def test_limit_keeps_newest_days(self):
        resp = self.client.get(f"{self.url}?limit=2")
        self.assertEqual(resp.status_code, 200, resp.content)
        items = resp.json()["items"]
        self.assertEqual([i["work_date"] for i in items], ["2026-06-03", "2026-06-02"])

    def test_default_returns_all_when_small(self):
        self.assertEqual(len(self.client.get(self.url).json()["items"]), 3)

    def test_invalid_limit_is_400(self):
        self.assertEqual(self.client.get(f"{self.url}?limit=x").status_code, 400)
        self.assertEqual(self.client.get(f"{self.url}?limit=501").status_code, 400)

    def test_month_filter_ignores_limit(self):
        resp = self.client.get(f"{self.url}?year=2026&month=6&limit=1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["items"]), 3)
