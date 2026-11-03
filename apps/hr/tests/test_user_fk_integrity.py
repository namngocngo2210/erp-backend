"""Tests FK thật cho SalaryRecord.created_by / LeaveRequest.approver (audit 2026-07 mục 3.8)."""
from datetime import date

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.hr.models import LeaveRequest, SalaryRecord
from apps.hr.tests.factories import create_employee
from apps.users.models import User


class HrUserFkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="hr_fk_user", password="x-not-used-1")
        cls.emp = create_employee(employee_code="FK001", full_name="FK Test")

    def test_created_by_id_attname_still_works(self):
        rec = SalaryRecord.objects.create(
            employee=self.emp, amount=1000000, effective_from=date(2026, 1, 1),
            created_by_id=self.user.id,
        )
        rec.refresh_from_db()
        self.assertEqual(rec.created_by_id, self.user.id)
        self.assertEqual(rec.created_by, self.user)

    def test_orphan_created_by_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SalaryRecord.objects.create(
                    employee=self.emp, amount=1, effective_from=date(2026, 1, 2),
                    is_current=False, created_by_id=99_999_999,
                )
                # Constraint DEFERRABLE INITIALLY DEFERRED → ép check ngay trong test.
                with transaction.get_connection().cursor() as cur:
                    cur.execute("SET CONSTRAINTS ALL IMMEDIATE")

    def test_approver_fk_and_attname(self):
        lr = LeaveRequest.objects.create(
            employee=self.emp, leave_type="annual",
            start_date=date(2026, 7, 1), end_date=date(2026, 7, 2), days=2,
            approver_id=self.user.id,
        )
        lr.refresh_from_db()
        self.assertEqual(lr.approver_id, self.user.id)
        self.assertEqual(lr.approver, self.user)
        # update_fields với attname vẫn hợp lệ (LeaveService.approve dùng pattern này)
        lr.approver_id = None
        lr.save(update_fields=["approver_id"])
        lr.refresh_from_db()
        self.assertIsNone(lr.approver_id)
