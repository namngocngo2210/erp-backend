"""Nghiệp vụ đội nhóm trong phòng ban — tách khỏi view giống `DepartmentService`."""

from __future__ import annotations

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from .department_membership import department_member_q
from .models import Team, TeamMember, User, UserDepartment


class TeamService:
    DUPLICATE_NAME_MESSAGE = "Tên đội nhóm đã tồn tại trong phòng ban này."
    NOT_IN_DEPARTMENT_MESSAGE = "Người được chọn không thuộc phòng ban này."

    @staticmethod
    def assignable_users(department_id: int) -> QuerySet[User]:
        """Tài khoản gán được vào nhóm của phòng ``department_id``.

        Nguồn là `users.User`, KHÔNG phải hồ sơ `hr.Employee`: 15/38 tài khoản không
        có hồ sơ nhân sự nào nên lọc theo hồ sơ là ô chọn rỗng (đo prod 2026-08-23).

        `department_member_q` bắt cả phòng chính lẫn phòng kiêm nhiệm, nên phải
        `.distinct()` — join M2M nhân dòng.
        """
        return (
            User.objects.filter(department_member_q(department_id))
            .filter(is_active=True, deleted_at__isnull=True)
            .order_by("display_name", "username")
            .distinct()
        )

    @classmethod
    def _assert_all_in_department(cls, department_id: int, user_ids) -> None:
        """Lead và thành viên phải thuộc chính phòng ban của nhóm.

        Kiểm bằng cùng một nguồn với `assignable_users` để giao diện và tầng ghi
        không bao giờ lệch nhau: thứ ô chọn cho chọn đúng bằng thứ service nhận.
        """
        wanted = {uid for uid in user_ids if uid is not None}
        if not wanted:
            return
        allowed = set(
            cls.assignable_users(department_id).values_list("id", flat=True)
        )
        if wanted - allowed:
            raise ValueError(cls.NOT_IN_DEPARTMENT_MESSAGE)

    @classmethod
    @transaction.atomic
    def create_team(cls, department_id: int, validated: dict) -> Team:
        name = validated["name"].strip()
        lead_user_id = validated.get("lead_user_id")
        member_user_ids = list(dict.fromkeys(validated.get("member_user_ids") or []))

        cls._assert_all_in_department(department_id, [lead_user_id, *member_user_ids])
        if Team.objects.filter(
            department_id=department_id, name=name, deleted_at__isnull=True
        ).exists():
            raise ValueError(cls.DUPLICATE_NAME_MESSAGE)

        team = Team.objects.create(
            department_id=department_id, name=name, lead_id=lead_user_id
        )
        TeamMember.objects.bulk_create(
            [TeamMember(team=team, user_id=uid) for uid in member_user_ids]
        )
        return team

    @staticmethod
    def teams_for_department(department_id: int) -> QuerySet[Team]:
        """Nhóm còn sống của một phòng ban, đã nạp sẵn lead + thành viên (tránh N+1)."""
        return (
            Team.objects.filter(department_id=department_id, deleted_at__isnull=True)
            .select_related("lead")
            .prefetch_related("members__user")
        )

    @classmethod
    @transaction.atomic
    def update_team(cls, team: Team, validated: dict) -> Team:
        """Sửa nhóm. Chỉ đụng tới key CÓ MẶT trong ``validated`` (ngữ nghĩa PATCH)."""
        if "name" in validated:
            name = validated["name"].strip()
            if (
                Team.objects.filter(
                    department_id=team.department_id, name=name, deleted_at__isnull=True
                )
                .exclude(id=team.id)
                .exists()
            ):
                raise ValueError(cls.DUPLICATE_NAME_MESSAGE)
            team.name = name

        if "lead_user_id" in validated:
            cls._assert_all_in_department(team.department_id, [validated["lead_user_id"]])
            team.lead_id = validated["lead_user_id"]

        team.save()

        if "member_user_ids" in validated:
            member_ids = list(dict.fromkeys(validated["member_user_ids"] or []))
            cls._assert_all_in_department(team.department_id, member_ids)
            # Bớt rồi thêm phần còn thiếu, KHÔNG xoá sạch rồi tạo lại: giữ nguyên
            # `created_at` của người đã ở sẵn trong nhóm.
            TeamMember.objects.filter(team=team).exclude(user_id__in=member_ids).delete()
            existing = set(
                TeamMember.objects.filter(team=team).values_list("user_id", flat=True)
            )
            TeamMember.objects.bulk_create(
                [TeamMember(team=team, user_id=uid) for uid in member_ids if uid not in existing]
            )
        return team

    @staticmethod
    def delete_team(team: Team) -> None:
        """Xoá mềm: giữ lại dòng để tra cứu, nhưng nhả tên cho nhóm mới dùng lại."""
        team.deleted_at = timezone.now()
        team.save(update_fields=["deleted_at", "updated_at"])

    @classmethod
    @transaction.atomic
    def sync_after_department_change(cls, user) -> None:
        """Đối soát vai trò nhóm với phòng ban HIỆN TẠI của ``user``.

        Cố ý viết kiểu đối soát (nhìn trạng thái hiện tại) chứ không kiểu bắt sự
        kiện "đổi từ X sang Y": có hai đường ghi phòng ban (view quản trị và sự
        kiện từ `hr`), nên hàm idempotent gọi được ở cả hai mà không sợ gọi thừa,
        đồng thời tự sửa được dữ liệu đã lệch từ trước.
        """
        dept_ids = set(
            UserDepartment.objects.filter(user=user).values_list("department_id", flat=True)
        )
        if getattr(user, "department_id", None):
            dept_ids.add(user.department_id)

        TeamMember.objects.filter(user=user).exclude(
            team__department_id__in=dept_ids
        ).delete()
        Team.objects.filter(lead=user).exclude(department_id__in=dept_ids).update(lead=None)
