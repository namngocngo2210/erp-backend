"""Tầng service của đội nhóm trong phòng ban (`apps.users.team_services`)."""

from django.test import TestCase

from apps.users.models import Department, Team, TeamMember, User, UserDepartment
from apps.users.team_services import TeamService


class AssignableUsersTests(TestCase):
    """Ai xuất hiện trong ô chọn thành viên của một phòng ban.

    Đây là chỗ lỗi cũ: ô chọn lấy từ hồ sơ `hr.Employee` nên phòng Sales có 14 tài
    khoản mà chỉ hiện 1 người (đo prod 2026-08-23).
    """

    def setUp(self):
        self.sales = Department.objects.create(name="Sales Test")
        self.ads = Department.objects.create(name="Ads Test")

    def test_includes_user_whose_primary_department_matches(self):
        user = User.objects.create_user(username="sale-1", password="x", department=self.sales)

        result = TeamService.assignable_users(self.sales.id)

        self.assertEqual([u.id for u in result], [user.id])

    def test_includes_user_who_only_holds_the_department_as_secondary(self):
        """Người kiêm nhiệm: phòng chính là Ads, phòng kiêm là Sales.

        Lý do `department_member_q` tồn tại — NV Sale kiêm chạy ads. Nếu chỉ so
        `User.department_id` thì người này biến mất khỏi ô chọn của Sales.
        """
        user = User.objects.create_user(username="kiem-nhiem", password="x", department=self.ads)
        UserDepartment.objects.create(user=user, department=self.sales)

        result = TeamService.assignable_users(self.sales.id)

        self.assertEqual([u.id for u in result], [user.id])

    def test_excludes_user_from_another_department(self):
        User.objects.create_user(username="ads-only", password="x", department=self.ads)

        self.assertEqual(list(TeamService.assignable_users(self.sales.id)), [])

    def test_excludes_deactivated_and_soft_deleted_accounts(self):
        User.objects.create_user(
            username="da-khoa", password="x", department=self.sales, is_active=False
        )
        deleted = User.objects.create_user(username="da-xoa", password="x", department=self.sales)
        deleted.deleted_at = "2026-08-01T00:00:00Z"
        deleted.save(update_fields=["deleted_at"])

        self.assertEqual(list(TeamService.assignable_users(self.sales.id)), [])

    def test_does_not_duplicate_user_holding_department_both_ways(self):
        """Join M2M nhân dòng — thiếu `.distinct()` là ô chọn hiện tên hai lần."""
        user = User.objects.create_user(username="ca-hai", password="x", department=self.sales)
        UserDepartment.objects.create(user=user, department=self.sales)

        self.assertEqual([u.id for u in TeamService.assignable_users(self.sales.id)], [user.id])


class CreateTeamTests(TestCase):
    def setUp(self):
        self.sales = Department.objects.create(name="Sales Test")
        self.ads = Department.objects.create(name="Ads Test")
        self.lead = User.objects.create_user(username="lead-1", password="x", department=self.sales)
        self.member = User.objects.create_user(
            username="member-1", password="x", department=self.sales
        )
        self.outsider = User.objects.create_user(
            username="outsider", password="x", department=self.ads
        )

    def test_creates_team_with_lead_and_members(self):
        team = TeamService.create_team(
            self.sales.id,
            {
                "name": "Nhóm 1",
                "lead_user_id": self.lead.id,
                "member_user_ids": [self.lead.id, self.member.id],
            },
        )

        self.assertEqual(team.department_id, self.sales.id)
        self.assertEqual(team.lead_id, self.lead.id)
        self.assertEqual(
            set(TeamMember.objects.filter(team=team).values_list("user_id", flat=True)),
            {self.lead.id, self.member.id},
        )

    def test_creates_team_without_lead(self):
        team = TeamService.create_team(self.sales.id, {"name": "Nhóm chưa có trưởng"})

        self.assertIsNone(team.lead_id)
        self.assertEqual(TeamMember.objects.filter(team=team).count(), 0)

    def test_rejects_lead_outside_the_department(self):
        with self.assertRaises(ValueError) as ctx:
            TeamService.create_team(
                self.sales.id, {"name": "Nhóm 1", "lead_user_id": self.outsider.id}
            )

        self.assertEqual(str(ctx.exception), TeamService.NOT_IN_DEPARTMENT_MESSAGE)
        self.assertFalse(Team.objects.filter(department_id=self.sales.id).exists())

    def test_rejects_member_outside_the_department(self):
        with self.assertRaises(ValueError) as ctx:
            TeamService.create_team(
                self.sales.id,
                {"name": "Nhóm 1", "member_user_ids": [self.member.id, self.outsider.id]},
            )

        self.assertEqual(str(ctx.exception), TeamService.NOT_IN_DEPARTMENT_MESSAGE)

    def test_rejects_duplicate_name_within_the_same_department(self):
        TeamService.create_team(self.sales.id, {"name": "Nhóm 1"})

        with self.assertRaises(ValueError) as ctx:
            TeamService.create_team(self.sales.id, {"name": "Nhóm 1"})

        self.assertEqual(str(ctx.exception), TeamService.DUPLICATE_NAME_MESSAGE)

    def test_allows_the_same_name_in_another_department(self):
        TeamService.create_team(self.sales.id, {"name": "Nhóm 1"})

        team = TeamService.create_team(self.ads.id, {"name": "Nhóm 1"})

        self.assertEqual(team.department_id, self.ads.id)

    def test_allows_one_user_to_join_several_teams(self):
        """Quyết định nghiệp vụ đã chốt: không giới hạn số nhóm mỗi người."""
        first = TeamService.create_team(
            self.sales.id, {"name": "Nhóm 1", "member_user_ids": [self.member.id]}
        )
        second = TeamService.create_team(
            self.sales.id, {"name": "Nhóm 2", "member_user_ids": [self.member.id]}
        )

        self.assertEqual(
            set(
                TeamMember.objects.filter(user=self.member).values_list("team_id", flat=True)
            ),
            {first.id, second.id},
        )


class UpdateTeamTests(TestCase):
    def setUp(self):
        self.sales = Department.objects.create(name="Sales Test")
        self.ads = Department.objects.create(name="Ads Test")
        self.lead = User.objects.create_user(username="lead-1", password="x", department=self.sales)
        self.member = User.objects.create_user(
            username="member-1", password="x", department=self.sales
        )
        self.newcomer = User.objects.create_user(
            username="newcomer", password="x", department=self.sales
        )
        self.outsider = User.objects.create_user(
            username="outsider", password="x", department=self.ads
        )
        self.team = TeamService.create_team(
            self.sales.id,
            {
                "name": "Nhóm 1",
                "lead_user_id": self.lead.id,
                "member_user_ids": [self.lead.id, self.member.id],
            },
        )

    def test_renames_team(self):
        TeamService.update_team(self.team, {"name": "Nhóm A"})

        self.team.refresh_from_db()
        self.assertEqual(self.team.name, "Nhóm A")

    def test_keeping_its_own_name_is_not_a_duplicate(self):
        TeamService.update_team(self.team, {"name": "Nhóm 1", "lead_user_id": None})

        self.team.refresh_from_db()
        self.assertEqual(self.team.name, "Nhóm 1")

    def test_rejects_name_taken_by_another_team_in_the_department(self):
        TeamService.create_team(self.sales.id, {"name": "Nhóm 2"})

        with self.assertRaises(ValueError) as ctx:
            TeamService.update_team(self.team, {"name": "Nhóm 2"})

        self.assertEqual(str(ctx.exception), TeamService.DUPLICATE_NAME_MESSAGE)

    def test_clears_lead_when_set_to_none(self):
        TeamService.update_team(self.team, {"lead_user_id": None})

        self.team.refresh_from_db()
        self.assertIsNone(self.team.lead_id)

    def test_members_are_replaced_not_merged(self):
        """`member_user_ids` là replace-set — gửi lên gì thì nhóm còn đúng thế."""
        TeamService.update_team(self.team, {"member_user_ids": [self.newcomer.id]})

        self.assertEqual(
            set(TeamMember.objects.filter(team=self.team).values_list("user_id", flat=True)),
            {self.newcomer.id},
        )

    def test_leaves_members_untouched_when_key_absent(self):
        """PATCH chỉ đổi tên thì KHÔNG được xoá sạch thành viên."""
        TeamService.update_team(self.team, {"name": "Nhóm A"})

        self.assertEqual(TeamMember.objects.filter(team=self.team).count(), 2)

    def test_rejects_member_outside_the_department(self):
        with self.assertRaises(ValueError) as ctx:
            TeamService.update_team(self.team, {"member_user_ids": [self.outsider.id]})

        self.assertEqual(str(ctx.exception), TeamService.NOT_IN_DEPARTMENT_MESSAGE)


class DeleteTeamTests(TestCase):
    def setUp(self):
        self.sales = Department.objects.create(name="Sales Test")
        self.member = User.objects.create_user(
            username="member-1", password="x", department=self.sales
        )

    def test_delete_is_soft(self):
        team = TeamService.create_team(
            self.sales.id, {"name": "Nhóm 1", "member_user_ids": [self.member.id]}
        )

        TeamService.delete_team(team)

        team.refresh_from_db()
        self.assertIsNotNone(team.deleted_at)

    def test_deleted_team_is_excluded_from_listing(self):
        team = TeamService.create_team(self.sales.id, {"name": "Nhóm 1"})
        TeamService.create_team(self.sales.id, {"name": "Nhóm 2"})

        TeamService.delete_team(team)

        self.assertEqual(
            [t.name for t in TeamService.teams_for_department(self.sales.id)], ["Nhóm 2"]
        )

    def test_name_of_a_deleted_team_can_be_reused(self):
        """Xoá "Nhóm 1" rồi tạo lại "Nhóm 1" phải được — unique có điều kiện."""
        team = TeamService.create_team(self.sales.id, {"name": "Nhóm 1"})
        TeamService.delete_team(team)

        again = TeamService.create_team(self.sales.id, {"name": "Nhóm 1"})

        self.assertNotEqual(again.id, team.id)


class DepartmentChangeCleanupTests(TestCase):
    """Đổi phòng ban thì vai trò ở phòng cũ phải rụng theo.

    Không dọn thì thành viên mồ côi: người đã chuyển sang Ads vẫn là trưởng nhóm
    của một nhóm bên Sales. Bài học đã trả giá một lần ở `hr` (audit 2026-07-15b).
    """

    def setUp(self):
        self.sales = Department.objects.create(name="Sales Test")
        self.ads = Department.objects.create(name="Ads Test")
        self.user = User.objects.create_user(username="nguoi-chuyen", password="x",
                                             department=self.sales)
        self.team = TeamService.create_team(
            self.sales.id,
            {"name": "Nhóm 1", "lead_user_id": self.user.id, "member_user_ids": [self.user.id]},
        )

    def _move_primary_to_ads(self):
        self.user.department = self.ads
        self.user.save(update_fields=["department"])

    def test_membership_is_dropped_when_user_leaves_the_department(self):
        self._move_primary_to_ads()

        TeamService.sync_after_department_change(self.user)

        self.assertFalse(TeamMember.objects.filter(team=self.team, user=self.user).exists())

    def test_lead_is_cleared_when_user_leaves_the_department(self):
        self._move_primary_to_ads()

        TeamService.sync_after_department_change(self.user)

        self.team.refresh_from_db()
        self.assertIsNone(self.team.lead_id)

    def test_membership_survives_when_user_still_holds_the_department_as_secondary(self):
        """Chuyển phòng chính nhưng vẫn kiêm nhiệm phòng cũ thì KHÔNG gỡ."""
        UserDepartment.objects.create(user=self.user, department=self.sales)
        self._move_primary_to_ads()

        TeamService.sync_after_department_change(self.user)

        self.assertTrue(TeamMember.objects.filter(team=self.team, user=self.user).exists())

    def test_membership_survives_when_nothing_changed(self):
        TeamService.sync_after_department_change(self.user)

        self.team.refresh_from_db()
        self.assertEqual(self.team.lead_id, self.user.id)
        self.assertTrue(TeamMember.objects.filter(team=self.team, user=self.user).exists())

    def test_revoking_a_secondary_department_prunes_its_teams(self):
        """Đi qua đường ghi thật `RoleService.set_user_departments`, không gọi tay."""
        from apps.users.services import RoleService

        kiem = User.objects.create_user(username="kiem", password="x", department=self.ads)
        UserDepartment.objects.create(user=kiem, department=self.sales)
        TeamService.update_team(self.team, {"member_user_ids": [self.user.id, kiem.id]})

        RoleService.set_user_departments(kiem, [self.ads.id])

        self.assertFalse(TeamMember.objects.filter(team=self.team, user=kiem).exists())
        self.assertTrue(TeamMember.objects.filter(team=self.team, user=self.user).exists())


class HrEventDepartmentChangeTests(TestCase):
    """Đường ghi phòng ban thứ hai: sự kiện `hr.employee.department_changed`.

    Handler chỉ đọc `user_id` và `department_id`, nên test dựng sự kiện bằng
    `SimpleNamespace` thay vì import model của `hr` — giữ test của `users` không
    kéo thêm phụ thuộc sang module khác.
    """

    def setUp(self):
        self.sales = Department.objects.create(name="Sales Test")
        self.ads = Department.objects.create(name="Ads Test")
        self.user = User.objects.create_user(
            username="nguoi-chuyen", password="x", department=self.sales
        )
        self.team = TeamService.create_team(
            self.sales.id,
            {"name": "Nhóm 1", "lead_user_id": self.user.id, "member_user_ids": [self.user.id]},
        )

    def test_hr_moving_user_to_another_department_prunes_team_roles(self):
        from types import SimpleNamespace

        from apps.users.subscribers import _on_department_changed

        _on_department_changed(
            SimpleNamespace(user_id=self.user.id, department_id=self.ads.id)
        )

        self.team.refresh_from_db()
        self.assertIsNone(self.team.lead_id)
        self.assertFalse(TeamMember.objects.filter(team=self.team, user=self.user).exists())
