"""Trần request cho portal public — theo IP thật, chặt hơn trần chung."""

from rest_framework.throttling import SimpleRateThrottle

from apps.common.throttling import ClientIpMixin, ThrottleGuardMixin


class _IpScopedThrottle(ThrottleGuardMixin, ClientIpMixin, SimpleRateThrottle):
    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class PortalActivateThrottle(_IpScopedThrottle):
    scope = "warranty_portal_activate"


class PortalReadThrottle(_IpScopedThrottle):
    scope = "warranty_portal_read"


class PortalMediaThrottle(_IpScopedThrottle):
    """Trần riêng cho ảnh sản phẩm — KHÔNG dùng chung bucket `anon` mặc định.

    Một trang sản phẩm kéo tới 8 ảnh + 1 lượt gọi payload = 9 request. Nếu view
    này không tự khai `@throttle_classes`, DRF rơi về `DEFAULT_THROTTLE_CLASSES`
    (`DefaultAnonRateThrottle`, scope `anon`) — bucket 600/giờ dùng CHUNG với mọi
    endpoint ẩn danh khác trong hệ thống. Vài chục lượt xem trang từ một IP NAT
    văn phòng là đủ cạn bucket đó và làm 429 lây sang các endpoint public khác.
    Scope riêng cách ly đúng blast radius vào tính năng này.
    """

    scope = "warranty_portal_media"
