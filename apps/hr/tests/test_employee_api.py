from django.test import TestCase
from rest_framework.test import APIClient

from apps.hr.repositories import EmployeeRepository
from apps.users.models import Department
from apps.users.tests.helpers import make_admin, make_user_with_sections


class EmployeeApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("hr-admin")
        self.dept = Department.objects.create(name="Sales")

    def test_create_requires_permission(self):
        nobody = make_user_with_sections("nobody", ["orders"])
        self.client.force_authenticate(user=nobody)
        r = self.client.post("/api/hr/employees",
                             {"full_name": "A", "username": "nva", "department_id": self.dept.id},
                             format="json")
        self.assertEqual(r.status_code, 403)

    def test_admin_create_and_list(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.post("/api/hr/employees",
                             {"full_name": "A", "username": "nva", "department_id": self.dept.id},
                             format="json")
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.data["employee_code"].startswith("NV"))
        r2 = self.client.get("/api/hr/employees?limit=20&offset=0")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.data["total"], 1)

    def test_org_chart(self):
        self.client.force_authenticate(user=self.admin)
        u = self.client.post("/api/hr/employees",
                             {"full_name": "Mgr", "username": "mgr", "department_id": self.dept.id},
                             format="json").data
        self.client.post("/api/hr/employees",
                         {"full_name": "Report", "username": "rep",
                          "department_id": self.dept.id, "manager_id": u["id"]},
                         format="json")
        r = self.client.get(f"/api/hr/employees/{u['id']}/org-chart")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["reports"]), 1)

    def test_patch_department_fires_sync(self):
        self.client.force_authenticate(user=self.admin)
        dept_b = Department.objects.create(name="Booking")
        emp = self.client.post(
            "/api/hr/employees",
            {"full_name": "Mover", "username": "mover", "department_id": self.dept.id},
            format="json",
        ).data
        # Use the READ-shape key ``department`` to prove the alias works.
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.patch(
                f"/api/hr/employees/{emp['id']}",
                {"department": dept_b.id},
                format="json",
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["department"], dept_b.id)
        emp_obj = EmployeeRepository.get_or_none(emp["id"])
        self.assertEqual(emp_obj.department_id, dept_b.id)
        # The EmployeeDepartmentChanged event fired and the users subscriber synced.
        self.assertEqual(emp_obj.primary_user.department_id, dept_b.id)

    def test_job_title_choice(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.post(
            "/api/hr/employees",
            {
                "full_name": "Mgr",
                "username": "mgr",
                "department_id": self.dept.id,
                "job_title": "manager",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["job_title"], "manager")
        self.assertEqual(r.data["job_title_display"], "Trưởng phòng")

        bad = self.client.post(
            "/api/hr/employees",
            {
                "full_name": "Bad",
                "username": "bad",
                "department_id": self.dept.id,
                "job_title": "ceo",
            },
            format="json",
        )
        self.assertEqual(bad.status_code, 400)

    def test_custom_and_duplicate_code(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.post(
            "/api/hr/employees",
            {
                "full_name": "A",
                "username": "nva",
                "department_id": self.dept.id,
                "employee_code": "API001",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["employee_code"], "API001")
        dup = self.client.post(
            "/api/hr/employees",
            {
                "full_name": "B",
                "username": "nvb",
                "department_id": self.dept.id,
                "employee_code": "API001",
            },
            format="json",
        )
        self.assertEqual(dup.status_code, 400)

    def test_delete_deactivates_user(self):
        self.client.force_authenticate(user=self.admin)
        emp = self.client.post(
            "/api/hr/employees",
            {
                "full_name": "Gone",
                "username": "gone",
                "department_id": self.dept.id,
                "create_login": True,
                "password": "secret123",
            },
            format="json",
        ).data
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.delete(f"/api/hr/employees/{emp['id']}")
        self.assertEqual(r.status_code, 204)
        self.assertIsNone(EmployeeRepository.active().filter(pk=emp["id"]).first())
        emp_obj = EmployeeRepository.all_with_deleted().get(pk=emp["id"])
        self.assertIsNotNone(emp_obj.deleted_at)
        self.assertFalse(emp_obj.primary_user.is_active)

    def test_resign_sets_status_and_deactivates_user(self):
        self.client.force_authenticate(user=self.admin)
        emp = self.client.post(
            "/api/hr/employees",
            {
                "full_name": "Quit",
                "username": "quit",
                "department_id": self.dept.id,
                "create_login": True,
                "password": "secret123",
            },
            format="json",
        ).data
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(f"/api/hr/employees/{emp['id']}/resign")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["employment_status"], "resigned")
        self.assertIsNotNone(r.data["resign_date"])
        emp_obj = EmployeeRepository.active().get(pk=emp["id"])
        self.assertFalse(emp_obj.primary_user.is_active)

    def test_list_filter_by_state(self):
        self.client.force_authenticate(user=self.admin)
        working = self.client.post(
            "/api/hr/employees",
            {"full_name": "Stay", "username": "stay", "department_id": self.dept.id},
            format="json",
        ).data
        gone = self.client.post(
            "/api/hr/employees",
            {"full_name": "Left", "username": "left", "department_id": self.dept.id},
            format="json",
        ).data
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f"/api/hr/employees/{gone['id']}/resign")

        r_working = self.client.get("/api/hr/employees?limit=20&offset=0&state=working")
        self.assertEqual(r_working.status_code, 200)
        working_ids = {e["id"] for e in r_working.data["items"]}
        self.assertIn(working["id"], working_ids)
        self.assertNotIn(gone["id"], working_ids)

        r_resigned = self.client.get("/api/hr/employees?limit=20&offset=0&state=resigned")
        resigned_ids = {e["id"] for e in r_resigned.data["items"]}
        self.assertEqual(resigned_ids, {gone["id"]})

    def test_list_filter_by_department(self):
        self.client.force_authenticate(user=self.admin)
        dept_b = Department.objects.create(name="Booking")
        in_a = self.client.post(
            "/api/hr/employees",
            {"full_name": "InA", "username": "ina", "department_id": self.dept.id},
            format="json",
        ).data
        self.client.post(
            "/api/hr/employees",
            {"full_name": "InB", "username": "inb", "department_id": dept_b.id},
            format="json",
        )
        r = self.client.get(f"/api/hr/employees?limit=20&offset=0&department={self.dept.id}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual({e["id"] for e in r.data["items"]}, {in_a["id"]})

    def test_update_profile_fields(self):
        self.client.force_authenticate(user=self.admin)
        emp = self.client.post(
            "/api/hr/employees",
            {"full_name": "P", "username": "pp", "department_id": self.dept.id},
            format="json",
        ).data
        r = self.client.patch(
            f"/api/hr/employees/{emp['id']}",
            {
                "position_level": "team_lead",
                "bank_name": "Vietcombank",
                "marital_status": "married",
                "id_issued_date": "2020-01-02",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["position_level"], "team_lead")
        self.assertEqual(r.data["bank_name"], "Vietcombank")
        self.assertEqual(r.data["marital_status"], "married")
        self.assertEqual(r.data["id_issued_date"], "2020-01-02")
