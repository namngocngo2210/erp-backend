"""View quản trị tài khoản người dùng (danh sách/tạo/sửa/xoá mềm)."""

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import User
from apps.users.permissions import require_method_permission, require_section
from apps.users.serializers import (
    AdminUserCreateSerializer,
    AdminUserListResponseSerializer,
    AdminUserUpdateSerializer,
    UserSerializer,
)
from apps.users.services import RoleService, UserService

from ._view_helpers import _is_admin, _parse_page_param, _replace_kiotviet_mappings


@extend_schema_view(
    get=extend_schema(
        operation_id="admin_users_list",
        parameters=[
            OpenApiParameter("q", str, description="Lọc icontains theo username/display_name"),
            OpenApiParameter("limit", int, description="Số dòng mỗi trang (mặc định 20, tối đa 100)"),
            OpenApiParameter("offset", int, description="Vị trí bắt đầu (mặc định 0)"),
        ],
        responses={
            200: AdminUserListResponseSerializer,
            403: OpenApiResponse(description="Không có quyền quản lý user"),
        },
    ),
    post=extend_schema(
        operation_id="admin_users_create",
        request=AdminUserCreateSerializer,
        responses={
            201: UserSerializer,
            403: OpenApiResponse(description="Không có quyền quản lý user"),
        },
    ),
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_method_permission(GET="users:view", POST="users:create")])
def admin_users_collection(request):
    if not _is_admin(request.user):
        return Response({"detail": "Ban khong co quyen quan ly user."}, status=status.HTTP_403_FORBIDDEN)

    if request.method == "GET":
        q = (request.query_params.get("q") or "").strip()
        limit = _parse_page_param(request.query_params.get("limit"), default=20, minimum=1, maximum=100)
        offset = _parse_page_param(request.query_params.get("offset"), default=0, minimum=0)
        users = UserService.admin_list_users(q=q)
        total = users.count()
        items = users[offset : offset + limit]
        return Response(
            {"items": UserSerializer(items, many=True).data, "total": total}
        )

    serializer = AdminUserCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    user = UserService.create_admin_user(serializer.validated_data)
    return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    patch=extend_schema(
        operation_id="admin_users_update",
        request=AdminUserUpdateSerializer,
        responses={
            200: UserSerializer,
            400: OpenApiResponse(description="Mật khẩu không hợp lệ"),
            403: OpenApiResponse(description="Không có quyền quản lý user"),
        },
    ),
    delete=extend_schema(
        operation_id="admin_users_delete",
        responses={
            204: OpenApiResponse(description="Đã xóa mềm user"),
            400: OpenApiResponse(description="Không thể xóa chính bản thân"),
            403: OpenApiResponse(description="Không có quyền quản lý user"),
        },
    ),
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_method_permission(PATCH="users:edit", DELETE="users:delete")])
def admin_user_detail(request, user_id: int):
    if not _is_admin(request.user):
        return Response({"detail": "Ban khong co quyen quan ly user."}, status=status.HTTP_403_FORBIDDEN)

    user = get_object_or_404(User, id=user_id, deleted_at__isnull=True)

    if request.method == "DELETE":
        if user.id == request.user.id:
            return Response({"detail": "Khong the xoa chinh ban."}, status=status.HTTP_400_BAD_REQUEST)
        user.is_active = False
        user.deleted_at = timezone.now()
        user.save(update_fields=["is_active", "deleted_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = AdminUserUpdateSerializer(
        data=request.data, partial=True, context={"user_id": user.id}
    )
    serializer.is_valid(raise_exception=True)

    if "display_name" in serializer.validated_data:
        user.display_name = serializer.validated_data["display_name"]
    if "is_active" in serializer.validated_data:
        user.is_active = serializer.validated_data["is_active"]
    if "department_id" in serializer.validated_data:
        user.department_id = serializer.validated_data["department_id"]
    if "lark_open_id" in serializer.validated_data:
        user.lark_open_id = serializer.validated_data["lark_open_id"]
    if "zenify_email" in serializer.validated_data:
        user.zenify_email = serializer.validated_data["zenify_email"]
    if "foxia_id" in serializer.validated_data:
        user.foxia_id = serializer.validated_data["foxia_id"]
    if "media_role" in serializer.validated_data:
        user.media_role = serializer.validated_data["media_role"]
    if "media_api_key" in serializer.validated_data:
        # "" = giữ nguyên (ô password để trống), None = xoá key (spec §4.7).
        new_key = serializer.validated_data["media_api_key"]
        if new_key is None:
            user.media_api_key = ""
        elif new_key:
            user.media_api_key = new_key

    update_fields = [
        "display_name",
        "is_active",
        "department_id",
        "lark_open_id",
        "zenify_email",
        "foxia_id",
        "media_role",
        "media_api_key",
    ]

    password = serializer.validated_data.get("password")
    if password:
        try:
            validate_password(password, user)
        except DjangoValidationError as exc:
            # `detail` phải là CHUỖI: FE chỉ đọc detail dạng string, list bị bỏ qua
            # và người dùng chỉ thấy "HTTP 400" mà không biết vì sao.
            return Response({"detail": " ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(password)
        # Admin đổi mật khẩu user → đăng xuất tất cả phiên của user đó.
        user.token_version = (user.token_version or 0) + 1
        user.save(update_fields=[*update_fields, "password", "token_version"])
    else:
        user.save(update_fields=update_fields)

    if "kiotviet_mappings" in serializer.validated_data:
        _replace_kiotviet_mappings(user, serializer.validated_data["kiotviet_mappings"])

    if "role_ids" in serializer.validated_data:
        RoleService.assign_roles_to_user(user, serializer.validated_data["role_ids"])

    if "department_ids" in serializer.validated_data:
        RoleService.set_user_departments(user, serializer.validated_data["department_ids"])

    return Response(UserSerializer(user).data)
