import datetime as dt

from django.test import TestCase
from rest_framework.test import APIClient

from apps.hr.models import DepartmentHead, LeaveRequest, LeaveStatus, LeaveType, Team
from apps.users.models import Department, User
from apps.users.tests.helpers import make_admin


class DepartmentOrgApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("org-admin")
        self.client.force_authenticate(user=self.admin)
        self.dept_a = Department.objects.create(name="Sales")
        self.dept_b = Department.objects.create(name="Booking")

    def _make_emp(self, name, username, dept_id, login=False):
        return self.client.post(
            "/api/hr/employees",
            {
                "full_name": name,
                "username": username,
                "department_id": dept_id,
                "create_login": login,
                "password": "secret123" if login else "",
            },
            format="json",
        ).data

    # ── Trưởng phòng ─────────────────────────────────────────────────────────
    def test_set_and_clear_head(self):
        emp = self._make_emp("Head A", "heada", self.dept_a.id)
        r = self.client.put(
            f"/api/hr/departments/{self.dept_a.id}/head",
            {"employee_id": emp["id"]},
            format="json",
        )
        self.assertEqual(r.status_code, 204)
        self.assertTrue(DepartmentHead.objects.filter(department_id=self.dept_a.id).exists())
        # clear
        r2 = self.client.put(
            f"/api/hr/departments/{self.dept_a.id}/head",
            {"employee_id": None},
            format="json",
        )
        self.assertEqual(r2.status_code, 204)
        self.assertFalse(DepartmentHead.objects.filter(department_id=self.dept_a.id).exists())

    def test_head_must_be_in_department(self):
        emp_b = self._make_emp("In B", "inb", self.dept_b.id)
        r = self.client.put(
            f"/api/hr/departments/{self.dept_a.id}/head",
            {"employee_id": emp_b["id"]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    # ── Team ─────────────────────────────────────────────────────────────────
    def test_create_team_with_lead_and_members(self):
        lead = self._make_emp("Lead", "lead", self.dept_a.id)
        mem = self._make_emp("Mem", "mem", self.dept_a.id)
        r = self.client.post(
            f"/api/hr/departments/{self.dept_a.id}/teams",
            {"name": "Team 1", "lead_employee_id": lead["id"], "member_employee_ids": [mem["id"]]},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["name"], "Team 1")
        self.assertEqual(r.data["lead"]["id"], lead["id"])
        # lead tự động là thành viên → 2 thành viên
        member_ids = {m["id"] for m in r.data["members"]}
        self.assertEqual(member_ids, {lead["id"], mem["id"]})

    def test_create_team_duplicate_name(self):
        self.client.post(
            f"/api/hr/departments/{self.dept_a.id}/teams", {"name": "Dup"}, format="json"
        )
        r = self.client.post(
            f"/api/hr/departments/{self.dept_a.id}/teams", {"name": "Dup"}, format="json"
        )
        self.assertEqual(r.status_code, 400)

    def test_team_member_must_be_in_department(self):
        outsider = self._make_emp("Out", "out", self.dept_b.id)
        r = self.client.post(
            f"/api/hr/departments/{self.dept_a.id}/teams",
            {"name": "X", "member_employee_ids": [outsider["id"]]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_update_team_replace_members(self):
        m1 = self._make_emp("M1", "m1", self.dept_a.id)
        m2 = self._make_emp("M2", "m2", self.dept_a.id)
        team = self.client.post(
            f"/api/hr/departments/{self.dept_a.id}/teams",
            {"name": "T", "member_employee_ids": [m1["id"]]},
            format="json",
        ).data
        r = self.client.patch(
            f"/api/hr/teams/{team['id']}",
            {"member_employee_ids": [m2["id"]]},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual({m["id"] for m in r.data["members"]}, {m2["id"]})

    def test_delete_team_soft(self):
        team = self.client.post(
            f"/api/hr/departments/{self.dept_a.id}/teams", {"name": "Gone"}, format="json"
        ).data
        r = self.client.delete(f"/api/hr/teams/{team['id']}")
        self.assertEqual(r.status_code, 204)
        obj = Team.objects.get(pk=team["id"])
        self.assertIsNotNone(obj.deleted_at)
        # không còn trong org list
        org = self.client.get("/api/hr/department-org").data
        dept_a_item = next(i for i in org["items"] if i["department_id"] == self.dept_a.id)
        self.assertEqual(dept_a_item["teams"], [])

    def test_org_bulk_shape(self):
        emp = self._make_emp("H", "h", self.dept_a.id)
        self.client.put(
            f"/api/hr/departments/{self.dept_a.id}/head",
            {"employee_id": emp["id"]},
            format="json",
        )
        self.client.post(
            f"/api/hr/departments/{self.dept_a.id}/teams", {"name": "T1"}, format="json"
        )
        org = self.client.get("/api/hr/department-org").data
        item = next(i for i in org["items"] if i["department_id"] == self.dept_a.id)
        self.assertEqual(item["head"]["id"], emp["id"])
        self.assertEqual(len(item["teams"]), 1)

    def test_requires_admin(self):
        from apps.users.tests.helpers import make_user_with_sections

        nobody = make_user_with_sections("nobody-org", ["orders"])
        self.client.force_authenticate(user=nobody)
        r = self.client.get("/api/hr/department-org")
        self.assertEqual(r.status_code, 403)


class LeaveApprovalByHeadTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("leave-admin")
        self.dept_a = Department.objects.create(name="Sales")
        self.dept_b = Department.objects.create(name="Booking")

    def _make_emp(self, name, username, dept_id, login=False):
        self.client.force_authenticate(user=self.admin)
        return self.client.post(
            "/api/hr/employees",
            {
                "full_name": name,
                "username": username,
                "department_id": dept_id,
                "create_login": login,
                "password": "secret123" if login else "",
            },
            format="json",
        ).data

    def _leave_for(self, employee_id):
        return LeaveRequest.objects.create(
            employee_id=employee_id,
            leave_type=LeaveType.SICK,
            start_date=dt.date(2026, 8, 1),
            end_date=dt.date(2026, 8, 2),
            days=2,
            status=LeaveStatus.PENDING,
        )

    def test_head_can_approve_own_department_leave(self):
        head = self._make_emp("Head", "head", self.dept_a.id, login=True)
        staff = self._make_emp("Staff", "staff", self.dept_a.id)
        self.client.force_authenticate(user=self.admin)
        self.client.put(
            f"/api/hr/departments/{self.dept_a.id}/head",
            {"employee_id": head["id"]},
            format="json",
        )
        leave = self._leave_for(staff["id"])
        head_user = User.objects.get(username="head")
        self.assertFalse(head_user.is_superuser)
        self.client.force_authenticate(user=head_user)
        r = self.client.post(f"/api/hr/leave-requests/{leave.id}/approve")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], LeaveStatus.APPROVED)

    def test_head_cannot_approve_other_department_leave(self):
        head = self._make_emp("Head", "head2", self.dept_a.id, login=True)
        staff_b = self._make_emp("StaffB", "staffb", self.dept_b.id)
        self.client.force_authenticate(user=self.admin)
        self.client.put(
            f"/api/hr/departments/{self.dept_a.id}/head",
            {"employee_id": head["id"]},
            format="json",
        )
        leave = self._leave_for(staff_b["id"])
        self.client.force_authenticate(user=User.objects.get(username="head2"))
        r = self.client.post(f"/api/hr/leave-requests/{leave.id}/approve")
        self.assertEqual(r.status_code, 403)

    def test_transferred_head_loses_old_department_approval(self):
        """Chuyển phòng phải dọn DepartmentHead/TeamMember — không dọn thì trưởng
        phòng Sales chuyển sang Booking vẫn duyệt được đơn nghỉ của Sales
        (audit 2026-07-15b)."""
        head = self._make_emp("Head", "head3", self.dept_a.id, login=True)
        staff = self._make_emp("Staff", "staff3", self.dept_a.id)
        self.client.force_authenticate(user=self.admin)
        self.client.put(
            f"/api/hr/departments/{self.dept_a.id}/head",
            {"employee_id": head["id"]},
            format="json",
        )

        # Chuyển head sang phòng B qua API sửa hồ sơ (luồng thật của admin).
        r = self.client.patch(
            f"/api/hr/employees/{head['id']}",
            {"department_id": self.dept_b.id},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(
            DepartmentHead.objects.filter(employee_id=head["id"]).exists(),
            "DepartmentHead phòng cũ phải bị dọn khi chuyển phòng",
        )

        leave = self._leave_for(staff["id"])
        self.client.force_authenticate(user=User.objects.get(username="head3"))
        r = self.client.post(f"/api/hr/leave-requests/{leave.id}/approve")
        self.assertEqual(r.status_code, 403)

    def test_stale_head_row_of_other_department_cannot_approve(self):
        """Phòng thủ chiều sâu: row DepartmentHead trỏ tới nhân sự KHÔNG còn thuộc
        phòng (dữ liệu cũ/đường ghi khác) không được cấp quyền duyệt."""
        head = self._make_emp("Head", "head4", self.dept_b.id, login=True)  # thuộc phòng B
        staff = self._make_emp("Staff", "staff4", self.dept_a.id)
        # Row stale: head của phòng A nhưng hồ sơ đang thuộc phòng B.
        DepartmentHead.objects.create(department_id=self.dept_a.id, employee_id=head["id"])

        leave = self._leave_for(staff["id"])
        self.client.force_authenticate(user=User.objects.get(username="head4"))
        r = self.client.post(f"/api/hr/leave-requests/{leave.id}/approve")
        self.assertEqual(r.status_code, 403)
