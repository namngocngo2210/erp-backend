"""Audit 2026-09-23 C8: employee_code chỉ unique trong tập nhân sự chưa xoá mềm."""

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.hr.models import Employee


class EmployeeCodePartialUniqueTests(TestCase):
    def test_soft_deleted_code_can_be_reused_but_two_active_cannot(self):
        first = Employee.objects.create(employee_code="NV001", full_name="A")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Employee.objects.create(employee_code="NV001", full_name="B")
        Employee.objects.filter(pk=first.pk).update(deleted_at=timezone.now())
        second = Employee.objects.create(employee_code="NV001", full_name="C")
        self.assertNotEqual(first.pk, second.pk)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Employee.objects.create(employee_code="NV001", full_name="D")
