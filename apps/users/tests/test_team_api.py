"""API đội nhóm trong phòng ban (`/api/users/departments/{id}/teams`, `/api/users/teams/{id}`)."""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.models import Department, Team, TeamMember, User, UserDepartment
from apps.users.team_services import TeamService
from apps.users.tests.helpers import make_admin, make_user_with_permissions


class TeamApiTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # User/phòng ban bất biến dựng MỘT lần cho cả class (rollback từng test về
        # savepoint của class) — trước đây tạo lại 5 user mỗi test (3 s/test).
        cls.admin = make_admin("team-admin")
        cls.sales = Department.objects.create(name="Sales Test")
        cls.ads = Department.objects.create(name="Ads Test")
        cls.lead = User.objects.create_user(
            username="lead-1", password="x", department=cls.sales, display_name="Trưởng nhóm"
        )
        cls.member = User.objects.create_user(
            username="member-1", password="x", department=cls.sales
        )
        cls.outsider = User.objects.create_user(
            username="outsider", password="x", department=cls.ads
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)


class ListTeamsApiTests(TeamApiTestCase):
    def test_lists_teams_with_lead_and_members(self):
        TeamService.create_team(
            self.sales.id,
            {
                "name": "Nhóm 1",
                "lead_user_id": self.lead.id,
                "member_user_ids": [self.lead.id, self.member.id],
            },
        )

        resp = self.client.get(f"/api/users/departments/{self.sales.id}/teams")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["name"], "Nhóm 1")
        self.assertEqual(body[0]["lead"]["id"], self.lead.id)
        self.assertEqual(body[0]["lead"]["display_name"], "Trưởng nhóm")
        self.assertEqual(
            {m["id"] for m in body[0]["members"]}, {self.lead.id, self.member.id}
        )

    def test_excludes_soft_deleted_teams(self):
        team = TeamService.create_team(self.sales.id, {"name": "Nhóm 1"})
        TeamService.create_team(self.sales.id, {"name": "Nhóm 2"})
        TeamService.delete_team(team)

        resp = self.client.get(f"/api/users/departments/{self.sales.id}/teams")

        self.assertEqual([t["name"] for t in resp.json()], ["Nhóm 2"])

    def test_unknown_department_returns_404(self):
        resp = self.client.get("/api/users/departments/999999/teams")

        self.assertEqual(resp.status_code, 404)


class CreateTeamApiTests(TeamApiTestCase):
    def test_creates_team(self):
        resp = self.client.post(
            f"/api/users/departments/{self.sales.id}/teams",
            {
                "name": "Nhóm 1",
                "lead_user_id": self.lead.id,
                "member_user_ids": [self.member.id],
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["lead"]["id"], self.lead.id)
        self.assertEqual([m["id"] for m in resp.json()["members"]], [self.member.id])

    def test_duplicate_name_returns_400_with_message(self):
        TeamService.create_team(self.sales.id, {"name": "Nhóm 1"})

        resp = self.client.post(
            f"/api/users/departments/{self.sales.id}/teams", {"name": "Nhóm 1"}, format="json"
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"], TeamService.DUPLICATE_NAME_MESSAGE)

    def test_member_outside_department_returns_400(self):
        resp = self.client.post(
            f"/api/users/departments/{self.sales.id}/teams",
            {"name": "Nhóm 1", "member_user_ids": [self.outsider.id]},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"], TeamService.NOT_IN_DEPARTMENT_MESSAGE)


class UpdateDeleteTeamApiTests(TeamApiTestCase):
    def setUp(self):
        super().setUp()
        self.team = TeamService.create_team(
            self.sales.id,
            {"name": "Nhóm 1", "member_user_ids": [self.lead.id, self.member.id]},
        )

    def test_renames_team(self):
        resp = self.client.patch(
            f"/api/users/teams/{self.team.id}", {"name": "Nhóm A"}, format="json"
        )

        self.assertEqual(resp.status_code, 200)
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, "Nhóm A")

    def test_replaces_members(self):
        resp = self.client.patch(
            f"/api/users/teams/{self.team.id}",
            {"member_user_ids": [self.member.id]},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            set(TeamMember.objects.filter(team=self.team).values_list("user_id", flat=True)),
            {self.member.id},
        )

    def test_renaming_does_not_wipe_members(self):
        """PATCH không gửi `member_user_ids` thì thành viên phải còn nguyên."""
        self.client.patch(f"/api/users/teams/{self.team.id}", {"name": "Nhóm A"}, format="json")

        self.assertEqual(TeamMember.objects.filter(team=self.team).count(), 2)

    def test_delete_is_soft_and_returns_204(self):
        resp = self.client.delete(f"/api/users/teams/{self.team.id}")

        self.assertEqual(resp.status_code, 204)
        self.team.refresh_from_db()
        self.assertIsNotNone(self.team.deleted_at)

    def test_patching_an_already_deleted_team_returns_404(self):
        TeamService.delete_team(self.team)

        resp = self.client.patch(
            f"/api/users/teams/{self.team.id}", {"name": "Nhóm A"}, format="json"
        )

        self.assertEqual(resp.status_code, 404)


class AssignableUsersApiTests(TeamApiTestCase):
    def test_returns_department_accounts_including_secondary_membership(self):
        """Đây là lỗi cũ: ô chọn cũ lấy từ hồ sơ HR nên Sales 14 người chỉ hiện 1."""
        UserDepartment.objects.create(user=self.outsider, department=self.sales)

        resp = self.client.get(f"/api/users/departments/{self.sales.id}/assignable-users")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            {u["id"] for u in resp.json()},
            {self.lead.id, self.member.id, self.outsider.id},
        )

    def test_payload_stays_slim(self):
        resp = self.client.get(f"/api/users/departments/{self.sales.id}/assignable-users")

        self.assertEqual(set(resp.json()[0]), {"id", "display_name", "username"})


class DepartmentListTeamCountTests(TeamApiTestCase):
    def test_department_list_carries_team_count(self):
        TeamService.create_team(self.sales.id, {"name": "Nhóm 1"})
        TeamService.create_team(self.sales.id, {"name": "Nhóm 2"})

        resp = self.client.get("/api/users/departments")

        by_id = {row["id"]: row for row in resp.json()}
        self.assertEqual(by_id[self.sales.id]["team_count"], 2)
        self.assertEqual(by_id[self.ads.id]["team_count"], 0)

    def test_team_count_ignores_deleted_teams(self):
        team = TeamService.create_team(self.sales.id, {"name": "Nhóm 1"})
        TeamService.delete_team(team)

        resp = self.client.get("/api/users/departments")

        by_id = {row["id"]: row for row in resp.json()}
        self.assertEqual(by_id[self.sales.id]["team_count"], 0)


class TeamApiPermissionTests(TeamApiTestCase):
    def test_read_permission_alone_cannot_create(self):
        """Quyền ghi phải là `users:edit`, không được gác bằng `users:view`."""
        reader = make_user_with_permissions("chi-doc", ["users:view"])
        self.client.force_authenticate(user=reader)

        resp = self.client.post(
            f"/api/users/departments/{self.sales.id}/teams", {"name": "Nhóm 1"}, format="json"
        )

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Team.objects.filter(name="Nhóm 1").exists())

    def test_read_permission_can_list(self):
        reader = make_user_with_permissions("chi-doc", ["users:view"])
        self.client.force_authenticate(user=reader)

        resp = self.client.get(f"/api/users/departments/{self.sales.id}/teams")

        self.assertEqual(resp.status_code, 200)

    def test_unrelated_permission_cannot_read(self):
        stranger = make_user_with_permissions("nguoi-la", ["orders:view"])
        self.client.force_authenticate(user=stranger)

        resp = self.client.get(f"/api/users/departments/{self.sales.id}/teams")

        self.assertEqual(resp.status_code, 403)

    def test_edit_permission_can_delete(self):
        editor = make_user_with_permissions("bien-tap", ["users:view", "users:edit"])
        team = TeamService.create_team(self.sales.id, {"name": "Nhóm 1"})
        self.client.force_authenticate(user=editor)

        resp = self.client.delete(f"/api/users/teams/{team.id}")

        self.assertEqual(resp.status_code, 204)


class AssignableUsersLimitTests(TeamApiTestCase):
    """Trần dòng `?limit=` (audit 2026-09-23 L6) — response vẫn là list."""

    def test_limit_caps_rows(self):
        resp = self.client.get(f"/api/users/departments/{self.sales.id}/assignable-users?limit=1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)

    def test_invalid_limit_is_400(self):
        url = f"/api/users/departments/{self.sales.id}/assignable-users"
        self.assertEqual(self.client.get(f"{url}?limit=0").status_code, 400)
        self.assertEqual(self.client.get(f"{url}?limit=999").status_code, 400)
