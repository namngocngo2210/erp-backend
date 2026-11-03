"""Gate truy cập tài liệu API (Swagger UI / OpenAPI schema) theo môi trường.

Ngoài production docs mở tự do cho dev. Trên production chỉ superuser xem được
(qua session Django — set lúc login ERP, hoặc JWT Bearer cho tooling); người
không đủ quyền nhận 404 để không lộ sự tồn tại của endpoint — giữ đúng hành vi
404 trước đây khi docs không được đăng ký trên prod.
"""

from __future__ import annotations

from functools import wraps

from django.conf import settings
from django.http import Http404


def _can_view_docs(user) -> bool:
    return bool(
        user is not None
        and getattr(user, "is_authenticated", False)
        and user.is_active
        and user.is_superuser
    )


def _jwt_user(request):
    """Xác thực JWT Bearer từ header (cho curl/tooling gọi thẳng /api/schema)."""
    # Import trong hàm để tránh import app khi Django chưa load xong app registry.
    from apps.users.jwt import VersionedJWTAuthentication

    try:
        result = VersionedJWTAuthentication().authenticate(request)
    except Exception:
        return None
    return result[0] if result else None


def gate_docs_in_production(view):
    """Bọc view docs: dev mở tự do, production yêu cầu superuser.

    Đọc ``settings.IS_PRODUCTION`` tại thời điểm request (không phải lúc import)
    để test override được mà không cần reload urlconf.
    """

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not settings.IS_PRODUCTION:
            return view(request, *args, **kwargs)
        if _can_view_docs(getattr(request, "user", None)):
            return view(request, *args, **kwargs)
        if _can_view_docs(_jwt_user(request)):
            return view(request, *args, **kwargs)
        raise Http404

    return wrapped
