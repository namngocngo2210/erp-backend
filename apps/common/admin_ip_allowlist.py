"""Allowlist IP cho Django admin (`/admin`) — audit 2026-09-23 L1.

`/admin` đang lộ Internet (vhost host proxy thẳng mọi path về backend). Trước khi
vhost nginx có `allow/deny`, chặn ở ứng dụng: chỉ IP/CIDR trong env
`DJANGO_ADMIN_ALLOWED_CIDRS` (phân cách dấu phẩy, vd `10.0.0.0/8,203.0.113.7`) mới
thấy admin; IP khác nhận 404 (không lộ sự tồn tại — cùng khuôn gate /api/docs).

Rỗng = cho phép tất cả (giữ hành vi cũ, dev/local không phải cấu hình); trên
production settings ghi WARNING để vận hành biết đang mở. IP client đọc theo đúng
thứ tự repo đã chốt cho throttle (CF-Connecting-IP → XFF hop tin cậy →
REMOTE_ADDR; `apps.common.throttling.ClientIpMixin`).
"""

from __future__ import annotations

import ipaddress
import logging

from django.conf import settings
from django.http import HttpResponseNotFound

from apps.common.throttling import ClientIpMixin

logger = logging.getLogger(__name__)

ADMIN_PATH = "/admin"


def parse_cidrs(raw: str | list | tuple | None) -> tuple:
    """Chuỗi/list CIDR → tuple ip_network. Entry sai cú pháp bị bỏ + log (không crash)."""
    if raw is None:
        return ()
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    networks = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        try:
            networks.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            logger.error("DJANGO_ADMIN_ALLOWED_CIDRS: bỏ qua entry không hợp lệ %r", text)
    return tuple(networks)


def is_admin_path(path: str) -> bool:
    """config/urls.py đăng ký `path("admin", admin.site.urls)` KHÔNG có dấu `/`
    nên URL admin thật là `/admin`, `/adminlogin/`, `/adminusers/user/`… — phải
    match theo tiền tố `/admin` (không đòi `/admin/`). Không có route nội bộ nào
    khác bắt đầu bằng `/admin` (API đều dưới `/api/`)."""
    return path.startswith(ADMIN_PATH)


def ip_allowed(ip: str, networks: tuple) -> bool:
    if not networks:
        return True
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    return any(addr in net for net in networks)


class AdminIpAllowlistMiddleware(ClientIpMixin):
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if is_admin_path(request.path):
            # Đọc settings mỗi request để override_settings trong test có tác dụng;
            # /admin gần như không có lưu lượng nên chi phí parse không đáng kể.
            networks = parse_cidrs(getattr(settings, "DJANGO_ADMIN_ALLOWED_CIDRS", ""))
            if networks and not ip_allowed(self.get_ident(request), networks):
                return HttpResponseNotFound()
        return self.get_response(request)
