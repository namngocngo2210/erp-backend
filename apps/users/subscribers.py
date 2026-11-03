from __future__ import annotations

import logging

from apps.common.events import event_bus
from apps.hr.contracts.hr_events import EmployeeDepartmentChanged

logger = logging.getLogger(__name__)


def _on_department_changed(event: EmployeeDepartmentChanged) -> None:
    from apps.users.models import User, UserDepartment
    from apps.users.rbac import bump_rbac_cache_version

    user = User.objects.filter(pk=event.user_id).first()
    if not user:
        return
    user.department_id = event.department_id
    user.save(update_fields=["department"])
    if event.department_id is not None:
        UserDepartment.objects.update_or_create(
            user=user,
            department_id=event.department_id,
            defaults={"is_primary": True},
        )
        UserDepartment.objects.filter(user=user).exclude(
            department_id=event.department_id
        ).update(is_primary=False)
    # Chuyển phòng thì vai trò trong đội nhóm của phòng cũ phải rụng theo, nếu không
    # người đã sang Ads vẫn là trưởng nhóm bên Sales.
    from apps.users.team_services import TeamService

    TeamService.sync_after_department_change(user)
    bump_rbac_cache_version()


def register() -> None:
    if not event_bus.is_subscribed(
        EmployeeDepartmentChanged.event_type, _on_department_changed
    ):
        event_bus.subscribe(
            EmployeeDepartmentChanged.event_type, _on_department_changed
        )
