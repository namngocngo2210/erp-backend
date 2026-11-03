"""Versioned JWT — cho phép vô hiệu hóa toàn bộ token cũ của một user.

Access token trong hệ thống sống rất lâu (xem ``JWT_ACCESS_MINUTES``), nên chỉ
blacklist refresh token là không đủ để "đăng xuất các phiên khác". Ở đây mỗi
token mang theo claim ``token_version`` bằng với ``User.token_version`` tại thời
điểm phát hành; mọi request đều so khớp claim này với giá trị hiện tại của user.
Khi đổi mật khẩu ta tăng ``token_version`` → tất cả token phát hành trước đó lệch
version và bị từ chối ngay lập tức, kể cả access token còn hạn.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, TokenError
from rest_framework_simplejwt.tokens import RefreshToken

TOKEN_VERSION_CLAIM = "token_version"


def _user_token_version(user) -> int:
    return int(getattr(user, "token_version", 0) or 0)


class VersionedRefreshToken(RefreshToken):
    """RefreshToken nhúng sẵn claim ``token_version`` của user.

    Claim được copy sang access token (kể cả khi refresh/rotate) nên không cần
    can thiệp riêng cho access token.
    """

    @classmethod
    def for_user(cls, user) -> VersionedRefreshToken:
        token = super().for_user(user)
        token[TOKEN_VERSION_CLAIM] = _user_token_version(user)
        return token


def token_version_matches(user, validated_token) -> bool:
    # Token "đời cũ" trước khi tính năng này tồn tại không mang claim; coi như 0
    # để khớp với mặc định token_version=0, tránh đăng xuất hàng loạt khi deploy.
    claim = validated_token.get(TOKEN_VERSION_CLAIM, 0)
    return int(claim) == _user_token_version(user)


class VersionedJWTAuthentication(JWTAuthentication):
    """JWTAuthentication kèm kiểm tra ``token_version``."""

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if not token_version_matches(user, validated_token):
            raise AuthenticationFailed(
                "Phiên đăng nhập đã hết hiệu lực do mật khẩu thay đổi.",
                code="token_version_mismatch",
            )
        return user


def raw_refresh_token_is_current(raw_refresh: str) -> bool:
    """True nếu refresh token (chuỗi) còn khớp ``token_version`` của user.

    Dùng ở endpoint refresh để chặn sớm refresh token cũ thay vì phát hành
    access token rồi mới bị từ chối ở tầng authentication.
    """

    try:
        token = RefreshToken(raw_refresh)
    except TokenError:
        return False

    user_id = token.get("user_id")
    if user_id is None:
        return False

    user_model = get_user_model()
    user = user_model.objects.filter(id=user_id).only("id", "token_version").first()
    if user is None:
        return False
    return token_version_matches(user, token)
