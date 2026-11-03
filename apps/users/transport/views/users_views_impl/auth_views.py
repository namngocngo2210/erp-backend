"""View xác thực và tài khoản cá nhân: login/refresh/logout, /me, profile, đổi mật khẩu."""

from django.contrib.auth import authenticate
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.exceptions import Throttled
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer

from apps.common.throttling import ClientIpMixin, ThrottleGuardMixin

from apps.users import login_lockout
from apps.users.jwt import VersionedRefreshToken, raw_refresh_token_is_current
from apps.users.serializers import (
    ChangePasswordSerializer,
    DepartmentSerializer,
    UserKiotvietMappingSerializer,
    UserProfileUpdateSerializer,
    UserSerializer,
)

from ._view_helpers import _me_payload, _set_refresh_cookie


class LoginRateThrottle(ThrottleGuardMixin, ClientIpMixin, SimpleRateThrottle):
    """Throttle theo IP cho login, chống brute-force (rate ở REST_FRAMEWORK['login']).

    Dùng SimpleRateThrottle (KHÔNG phải ScopedRateThrottle) vì view dạng hàm
    @api_view không có thuộc tính `throttle_scope`; ScopedRateThrottle sẽ đọc
    scope=None và bỏ qua throttle. SimpleRateThrottle dùng `scope` cố định ở
    class nên kích hoạt đúng. ClientIpMixin key theo IP client thật sau
    Cloudflare+nginx (CF-Connecting-IP → XFF hop tin cậy → REMOTE_ADDR).
    """

    scope = "login"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class LoginUserThrottle(ThrottleGuardMixin, ClientIpMixin, SimpleRateThrottle):
    """Throttle theo (username, IP) cho login — lớp thứ hai sau trần IP.

    Trần IP (LoginRateThrottle) phải rộng vì cả văn phòng chung một IP NAT; trần
    này siết đúng cặp tài khoản-đang-bị-thử từ một máy (rate ở
    REST_FRAMEWORK['login_user'], mặc định 5/phút). Kẻ đổi IP liên tục bị chặn
    tiếp bởi lockout theo username (apps/users/login_lockout.py). Thiếu username
    trong body → không tính (trần IP vẫn áp).
    """

    scope = "login_user"

    def get_cache_key(self, request, view):
        username = login_lockout.normalize_username(_login_username(request))
        if not username:
            return None
        return self.cache_format % {
            "scope": self.scope,
            "ident": f"{username}:{self.get_ident(request)}",
        }


def _login_username(request) -> str:
    data = getattr(request, "data", None)
    try:
        return str((data or {}).get("username", "") or "")
    except AttributeError:  # body không phải dict (vd list JSON)
        return ""


class MeResponseSerializer(drf_serializers.Serializer):
    """Schema payload user đầy đủ (UserSerializer + RBAC) — dùng chung /me và /login.

    Một class duy nhất (không dùng inline_serializer 2 lần) để tránh trùng tên
    component trong OpenAPI schema.
    """

    id = drf_serializers.IntegerField()
    username = drf_serializers.CharField()
    display_name = drf_serializers.CharField()
    avatar_data_url = drf_serializers.CharField(allow_null=True)
    is_active = drf_serializers.BooleanField()
    department = DepartmentSerializer(allow_null=True)
    allowed_sections = drf_serializers.ListField(child=drf_serializers.CharField())
    editable_order_fields = drf_serializers.ListField(child=drf_serializers.CharField())
    kiotviet_mappings = UserKiotvietMappingSerializer(many=True)
    lark_open_id = drf_serializers.CharField(allow_blank=True)
    role_ids = drf_serializers.ListField(child=drf_serializers.IntegerField())
    department_ids = drf_serializers.ListField(child=drf_serializers.IntegerField())
    permissions = drf_serializers.ListField(child=drf_serializers.CharField())
    scopes = drf_serializers.DictField(child=drf_serializers.CharField())
    field_policy = drf_serializers.DictField()
    is_superuser = drf_serializers.BooleanField()


@extend_schema(
    operation_id="auth_login",
    request=inline_serializer(
        "LoginRequest",
        fields={
            "username": drf_serializers.CharField(),
            "password": drf_serializers.CharField(),
        },
    ),
    responses={
        200: inline_serializer(
            "LoginResponse",
            fields={
                # access token + payload user đầy đủ (gồm permissions RBAC, y hệt /me);
                # refresh token đặt ở httpOnly cookie (không trả body).
                "user": MeResponseSerializer(),
                "access": drf_serializers.CharField(),
            },
        ),
        400: OpenApiResponse(description="Sai tài khoản hoặc mật khẩu"),
        # 429 (throttle IP / (username, IP) / lockout tài khoản) cố ý KHÔNG khai vào
        # schema: schema.yaml ↔ frontend/src/api/schema.d.ts đang khoá drift-check,
        # đổi hợp đồng phải đi cùng MR frontend regen typed client.
    },
)
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([LoginRateThrottle, LoginUserThrottle])
def login_view(request):
    username = _login_username(request)
    password = request.data.get("password", "")
    # Khoá tạm theo username (audit 2026-09-23 M5): kiểm TRƯỚC authenticate để
    # không tốn hash mật khẩu cho tài khoản đang bị khoá, và để mật khẩu đúng cũng
    # không lọt trong cửa sổ khoá. Raise Throttled → cùng dạng 429 + Retry-After
    # với throttle của DRF (một hợp đồng 429 cho FE).
    if login_lockout.is_locked(username):
        raise Throttled(
            wait=login_lockout.lock_seconds_remaining(username),
            detail="Tài khoản tạm bị khoá do đăng nhập sai nhiều lần. Vui lòng thử lại sau.",
        )
    user = authenticate(request, username=username, password=password)
    if not user:
        login_lockout.record_failure(username)
        return Response({"detail": "Sai tai khoan hoac mat khau"}, status=status.HTTP_400_BAD_REQUEST)
    login_lockout.reset(username)

    # Set session Django cho superuser để mở được Swagger UI trên production
    # (/api/docs — gate ở config/docs_access.py). API nghiệp vụ vẫn auth bằng
    # JWT thuần, session này không cấp thêm quyền gọi API.
    if user.is_superuser:
        django_login(request._request, user)

    refresh = VersionedRefreshToken.for_user(user)

    response = Response(
        {
            # Payload đầy đủ như /me (gồm permissions cấp hành động) để FE dùng được
            # ngay sau login, không phải chờ /me hydrate.
            "user": _me_payload(user),
            "access": str(refresh.access_token),
        }
    )

    _set_refresh_cookie(response, str(refresh))
    return response


@extend_schema(
    operation_id="auth_token_refresh",
    # Refresh token đọc từ httpOnly cookie (không phải body) → không có request body.
    request=None,
    responses={
        200: inline_serializer(
            "TokenRefreshResponse",
            fields={"access": drf_serializers.CharField()},
        ),
        401: OpenApiResponse(description="Thiếu/không hợp lệ refresh token hoặc phiên hết hiệu lực"),
    },
)
@api_view(["POST"])
@permission_classes([AllowAny])
def token_refresh_view(request):
    # Get refresh token from httpOnly cookie
    refresh_token = request.COOKIES.get("refresh_token")
    if not refresh_token:
        return Response({"detail": "Refresh token not provided."}, status=status.HTTP_401_UNAUTHORIZED)

    # Từ chối refresh token được phát hành trước khi mật khẩu thay đổi.
    if not raw_refresh_token_is_current(refresh_token):
        response = Response(
            {"detail": "Phiên đăng nhập đã hết hiệu lực do mật khẩu thay đổi."},
            status=status.HTTP_401_UNAUTHORIZED,
        )
        response.delete_cookie("refresh_token")
        return response

    # Create a mock data dict with the refresh token
    data = {"refresh": refresh_token}
    serializer = TokenRefreshSerializer(data=data)
    try:
        serializer.is_valid(raise_exception=True)
    except TokenError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)

    response = Response(serializer.validated_data)

    refresh = serializer.validated_data.get("refresh")
    if refresh:
        _set_refresh_cookie(response, str(refresh))

    return response


@extend_schema(
    operation_id="auth_logout",
    request=None,
    responses={
        200: inline_serializer(
            "LogoutResponse",
            fields={"detail": drf_serializers.CharField()},
        ),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout_view(request):
    # Huỷ luôn session Django (login ERP có set để gate /api/docs). Chỉ xoá cookie
    # refresh_token là chưa đủ: `sessionid` sống tiếp cả tuần và KHÔNG chịu cơ chế thu
    # hồi token_version → ai lấy được cookie vẫn đọc được hợp đồng API nội bộ sau khi
    # người dùng đã bấm Đăng xuất (audit 2026-07-14 vòng 2).
    django_logout(request._request)

    response = Response({"detail": "Logged out successfully."})
    response.delete_cookie("refresh_token")
    return response


@extend_schema(
    operation_id="auth_me",
    responses={
        # Các field UserSerializer gộp phẳng cùng field RBAC (permissions/scopes/...) từ _me_payload.
        200: MeResponseSerializer,
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
@never_cache
def me(request):
    return Response(_me_payload(request.user))


@extend_schema(
    operation_id="auth_profile_update",
    request=UserProfileUpdateSerializer,
    responses={200: UserSerializer},
)
@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def profile_view(request):
    serializer = UserProfileUpdateSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)

    user = request.user
    if "display_name" in serializer.validated_data:
        user.display_name = serializer.validated_data["display_name"]
    if "avatar_data_url" in serializer.validated_data:
        user.avatar_data_url = serializer.validated_data["avatar_data_url"]

    user.save(update_fields=["display_name", "avatar_data_url"])
    return Response(UserSerializer(user).data)


@extend_schema(
    operation_id="auth_change_password",
    request=ChangePasswordSerializer,
    responses={
        200: inline_serializer(
            "ChangePasswordResponse",
            fields={
                # Trả access token mới (mang token_version mới) để phiên hiện tại không bị đăng xuất.
                "detail": drf_serializers.CharField(),
                "access": drf_serializers.CharField(),
            },
        ),
        400: OpenApiResponse(description="Mật khẩu hiện tại sai hoặc mật khẩu mới không hợp lệ"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def change_password_view(request):
    serializer = ChangePasswordSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    user = request.user
    current_password = serializer.validated_data["current_password"]
    new_password = serializer.validated_data["new_password"]

    if not user.check_password(current_password):
        return Response({"detail": "Mật khẩu hiện tại không đúng."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        validate_password(new_password, user)
    except DjangoValidationError as exc:
        # Chuỗi, không phải list — xem admin_user_detail.
        return Response({"detail": " ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)

    user.set_password(new_password)
    # Tăng token_version để vô hiệu hóa token của các phiên khác; phiên hiện tại
    # nhận token mới mang version mới ngay bên dưới nên không bị đăng xuất.
    user.token_version = (user.token_version or 0) + 1
    user.save(update_fields=["password", "token_version"])

    refresh = VersionedRefreshToken.for_user(user)
    response = Response(
        {
            "detail": "Doi mat khau thanh cong.",
            "access": str(refresh.access_token),
        }
    )
    _set_refresh_cookie(response, str(refresh))
    return response
