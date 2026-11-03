"""Auth phụ CHỈ cho `content_video_stream`: chấp nhận cookie phiên `refresh_token`
khi request không mang header `Authorization`.

Vì sao cần: trình duyệt streaming qua thẻ `<video src="…">` không cho JS gắn
header tuỳ ý, nên `http.ts` không thể đính Bearer access token (giữ trong RAM,
xem `frontend/src/services/http.ts`) vào request video. Trước đây FE phải
`fetch(..., {Authorization})` rồi tải hết file thành blob (~10s trước khi phát
được, không tua được) chỉ để có chỗ gắn header. Lớp auth này cho phép quay lại
`<video src=…>` thuần, trình duyệt tự stream tiến độ + tua bằng Range.

An toàn — vì sao chấp nhận cookie ở một endpoint đọc là ổn:
- Cookie `refresh_token` được set `httponly`, `samesite="Lax"`
  (`apps/users/transport/views/users_views_impl/_view_helpers.py::_set_refresh_cookie`).
  `SameSite=Lax` KHÔNG gửi cookie kèm request tải tài nguyên nhúng từ site khác
  (ảnh/video/iframe qua thẻ HTML) — chỉ gửi khi điều hướng top-level bằng GET.
  Vậy một trang lạ nhúng `<video src="https://erp…/video-stream">` sẽ không có
  cookie kèm theo → 401/403, không lộ video của người dùng đang đăng nhập ERP.
- Endpoint chỉ GET, không đổi state gì.
- Cookie này vốn đã đủ quyền tự mint access token mới ở `/api/users/refresh`
  (`auth_views.token_refresh_view`) — chấp nhận nó thẳng ở đây không mở thêm
  quyền nào so với việc FE tự gọi refresh trước rồi mới stream.

Không đăng ký vào `DEFAULT_AUTHENTICATION_CLASSES` (config/settings_parts/base.py)
— chỉ gắn thủ công qua `@authentication_classes` ở `content_video_stream`, mọi
endpoint Media khác giữ nguyên chỉ nhận Bearer.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import authentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.jwt import raw_refresh_token_is_current
from apps.users.models import User


class RefreshCookieAuthentication(authentication.BaseAuthentication):
    """Xác thực bằng cookie `refresh_token` khi không có header `Authorization`."""

    def authenticate(self, request: Request) -> tuple[User, None] | None:
        if request.headers.get("Authorization"):
            # Có Bearer → nhường hẳn cho VersionedJWTAuthentication (đứng trước
            # trong DEFAULT_AUTHENTICATION_CLASSES), tránh xử lý chồng chéo.
            return None

        raw_refresh = request.COOKIES.get("refresh_token")
        if not raw_refresh:
            return None

        # `raw_refresh_token_is_current` tự bắt token hỏng/hết hạn (TokenError →
        # False) LẪN version claim lệch (đổi mật khẩu) — dùng lại đúng một chỗ,
        # không tách hai đường lỗi khác code để khỏi re-implement JWT verify.
        if not raw_refresh_token_is_current(raw_refresh):
            raise AuthenticationFailed(
                "Phiên đăng nhập đã hết hiệu lực.", code="token_not_valid"
            )

        # Tới đây token chắc chắn parse được (nếu không đã False ở trên) — decode
        # lại (RefreshToken tự verify chữ ký, không tự triển khai lại) chỉ để đọc
        # claim user_id.
        token = RefreshToken(raw_refresh)
        user_id = token.get("user_id")
        if user_id is None:
            raise AuthenticationFailed("Refresh token thiếu user_id.", code="token_not_valid")

        try:
            user = get_user_model().objects.get(id=user_id)
        except get_user_model().DoesNotExist as exc:
            raise AuthenticationFailed("Không tìm thấy người dùng.", code="user_not_found") from exc

        if not user.is_active or user.deleted_at is not None:
            raise AuthenticationFailed("Tài khoản không còn hoạt động.", code="user_inactive")

        return user, None

    def authenticate_header(self, request: Request) -> str:
        return 'Bearer realm="api"'
