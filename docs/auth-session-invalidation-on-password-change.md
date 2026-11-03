# Đăng xuất các phiên khác khi đổi mật khẩu

## Bối cảnh & vấn đề
Hệ thống dùng JWT stateless (SimpleJWT). Access token cấu hình sống rất lâu
(`JWT_ACCESS_MINUTES` mặc định = 525600 phút ≈ 365 ngày). Vì vậy khi một user đổi
mật khẩu, các phiên đăng nhập khác (thiết bị/trình duyệt khác) vẫn dùng access
token cũ bình thường — blacklist refresh token **không** giải quyết được do access
token chưa hết hạn.

Yêu cầu: **đổi mật khẩu → tất cả phiên khác của cùng tài khoản bị đăng xuất.**

## Giải pháp: token_version claim
Cơ chế stateless, không cần lưu danh sách token:

- Thêm `User.token_version` (PositiveIntegerField, default 0).
- Mọi token phát hành mang claim `token_version` = giá trị hiện tại của user.
- Mọi request xác thực so khớp claim với `user.token_version`. Lệch → 401.
- Đổi mật khẩu → tăng `token_version` → toàn bộ token cũ lệch version, chết ngay
  (kể cả access token còn hạn). Phiên đang thao tác nhận token mới mang version
  mới nên **không** bị đăng xuất.

Claim được copy tự động sang access token khi `refresh.access_token` /
khi refresh/rotate, nên không phải xử lý riêng cho access token.

## Các điểm chạm
| File | Thay đổi |
|------|----------|
| `apps/users/models.py` | Thêm field `token_version` |
| `apps/users/migrations/0012_user_token_version.py` | Migration AddField |
| `apps/users/jwt.py` (mới) | `VersionedRefreshToken`, `VersionedJWTAuthentication`, `raw_refresh_token_is_current` |
| `apps/users/views.py` | Login/refresh/change-password/admin-reset dùng versioned token; bump version khi đổi mật khẩu; set lại refresh cookie |
| `config/settings_parts/base.py` | `DEFAULT_AUTHENTICATION_CLASSES` → `VersionedJWTAuthentication` |
| `apps/cron_jobs/views.py` | `authentication_classes` → `VersionedJWTAuthentication` |
| `apps/channels_core/jwt_middleware.py` | WebSocket cũng kiểm tra `token_version` |

## Hành vi theo luồng
- **Self đổi mật khẩu** (`POST /api/users/change-password`): bump version, phiên
  hiện tại nhận access mới + refresh cookie mới (giữ đăng nhập); các phiên khác bị
  401 ở request kế tiếp và khi gọi refresh.
- **Admin reset mật khẩu user** (`PATCH /api/users/admin/{id}` có `password`): bump
  version của user đó → user bị đăng xuất ở **mọi** thiết bị.
- **Refresh token cũ**: `token/refresh` chặn sớm (401 + xóa cookie) thay vì cấp
  access token rồi mới bị từ chối → frontend tự dọn token và quay về màn login.
- **WebSocket**: kết nối mở bằng token cũ bị coi là AnonymousUser.

## Tương thích khi deploy
Token đang lưu ở các phiên hiện tại (trước khi deploy) **không** có claim
`token_version`. Ta coi claim thiếu = 0, khớp với mặc định `token_version = 0`,
nên **không gây đăng xuất hàng loạt** khi release. Token cũ chỉ mất hiệu lực kể từ
lần đổi mật khẩu đầu tiên của từng user.

## Kiểm thử
`apps/users/tests/test_password_session_invalidation.py`:
- Token cũ bị 401 sau khi self đổi mật khẩu; token mới vẫn 200.
- Refresh cookie cũ bị 401 sau khi version đổi.
- Admin reset mật khẩu → token cũ của user mục tiêu bị 401.
