"""Cây lỗi module Media. Mỗi lỗi mang sẵn (status, code, detail) để transport trả
`{"detail", "code"}` mà không phải map lại (spec §4.6)."""

from __future__ import annotations

from apps.media.domain.errors import upstream_detail


class MediaError(Exception):
    status: int = 500
    code: str = "media_error"

    def __init__(self, detail: str = "", *, status: int | None = None, code: str | None = None):
        self.detail = detail or self.default_detail()
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code
        super().__init__(self.detail)

    def default_detail(self) -> str:
        return "Lỗi module Media."


class MediaDisabled(MediaError):
    status, code = 503, "media_disabled"

    def default_detail(self) -> str:
        return "Chưa cấu hình kết nối Media hoặc kết nối đang tắt."


class MediaStagingBlocked(MediaError):
    status, code = 503, "media_staging_blocked"

    def default_detail(self) -> str:
        return "Media outbound bị chặn trên staging (APP_ENV=staging)."


class MediaNotProvisioned(MediaError):
    status, code = 403, "media_not_provisioned"

    def default_detail(self) -> str:
        return "Bạn chưa được cấp quyền Media. Nhờ admin gán vai trò Media hoặc nhập API key riêng."


class MediaForbidden(MediaError):
    status, code = 403, "media_forbidden"

    def default_detail(self) -> str:
        return "Vai trò Media của bạn không đủ quyền cho thao tác này."


class MediaNotOwner(MediaError):
    status, code = 403, "media_not_owner"

    def default_detail(self) -> str:
        return "Chỉ người tạo (hoặc admin Media) mới sửa được bản ghi này."


class AdsCrmUnavailable(MediaError):
    status, code = 502, "media_upstream_unavailable"

    def default_detail(self) -> str:
        return "Hệ thống Media (ads-crm) không phản hồi."


class AdsCrmApiError(MediaError):
    """Lỗi nghiệp vụ ads-crm trả về dạng {"error": code}. Giữ nguyên HTTP status,
    TRỪ 401: đổi thành 502 media_key_rejected để frontend không logout phiên desi-order."""

    def __init__(self, code: str, status: int):
        if status == 401:
            super().__init__(
                "API key Media bị ads-crm từ chối (sai/hết hạn/tài khoản khoá). Nhờ admin cập nhật key.",
                status=502,
                code="media_key_rejected",
            )
        else:
            super().__init__(upstream_detail(code), status=status, code=code)
