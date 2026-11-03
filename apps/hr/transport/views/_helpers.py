from __future__ import annotations

from rest_framework.response import Response


def can(request, code: str) -> bool:
    from apps.users.rbac import resolve_effective_permissions

    return resolve_effective_permissions(request.user).can(code)


def forbidden():
    return Response(
        {"detail": "Bạn không có quyền thực hiện hành động này."}, status=403
    )
