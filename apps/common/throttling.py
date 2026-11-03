"""Throttle helpers dùng chung — IP client thật sau Cloudflare + nginx, guard
fail-open + metric, và trần mặc định cho MỌI endpoint DRF.

Thiết kế đầy đủ: docs/rate-limiting-hardening-2026-07.md

Vấn đề IP (audit 2026-07 mục 1.4): DRF ``SimpleRateThrottle.get_ident()`` mặc định
dùng REMOTE_ADDR (= IP của nginx/CF edge khi đứng sau proxy) hoặc tin toàn bộ
X-Forwarded-For → mọi client bị gộp chung 1 bucket, hoặc client tự spoof XFF
để né throttle.

Thứ tự ưu tiên:
1. ``CF-Connecting-IP`` — Cloudflare set, không thể spoof khi traffic bắt buộc
   đi qua CF (origin chỉ nhận từ CF).
2. ``X-Forwarded-For`` — lấy hop thứ ``THROTTLE_TRUSTED_PROXIES`` tính từ cuối
   (semantics như ``NUM_PROXIES`` của DRF; env ``THROTTLE_TRUSTED_PROXIES``,
   default 1 = chỉ tin nginx trước app).
3. ``REMOTE_ADDR`` — kết nối trực tiếp (dev/local).
"""

from __future__ import annotations

import logging

from django.conf import settings
from rest_framework.settings import api_settings
from rest_framework.throttling import BaseThrottle

from apps.common import rate_limiter

logger = logging.getLogger(__name__)


class ClientIpMixin:
    """Mixin cho throttle: override ``get_ident`` theo IP client thật."""

    def get_ident(self, request):
        cf_ip = request.META.get("HTTP_CF_CONNECTING_IP", "").strip()
        if cf_ip:
            return cf_ip

        xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if xff:
            addrs = [addr.strip() for addr in xff.split(",") if addr.strip()]
            trusted = int(getattr(settings, "THROTTLE_TRUSTED_PROXIES", 1))
            if addrs and trusted > 0:
                return addrs[-min(trusted, len(addrs))]

        return request.META.get("REMOTE_ADDR", "")


class ThrottleGuardMixin:
    """Fail-open + metric cho mọi throttle. Đặt ĐẦU TIÊN trong danh sách base class.

    Hai việc, đều là chuyện vận hành chứ không phải nghiệp vụ throttle:

    1. **Fail-open.** Throttle của DRF đọc cache; Redis chết là mọi endpoint có
       throttle trả 500. Rate limit là lớp phòng thủ — mất nó phải là suy giảm
       lặng lẽ, không phải sự cố toàn hệ thống.
    2. **Metric.** ``desi_throttled_requests_total{scope}`` là cách duy nhất biết
       ta đang chặn nhầm nhân viên hay đang chặn đúng script rác. Không có nó thì
       mọi lần siết trần đều là đoán.
    """

    def allow_request(self, request, view):
        try:
            # Mixin: allow_request có thật trong MRO (SimpleRateThrottle của DRF),
            # mypy không thấy vì mixin không khai base.
            allowed = super().allow_request(request, view)  # type: ignore[misc]
        except Exception:
            logger.exception(
                "throttle %s lỗi khi kiểm tra — cho request đi tiếp (fail-open)",
                type(self).__name__,
            )
            return True

        if not allowed:
            rate_limiter.record_throttled(getattr(self, "scope", "") or type(self).__name__)
        return allowed


# Endpoint máy-tới-máy / hạ tầng: KHÔNG áp trần mặc định. Chúng đã có cơ chế xác
# thực riêng (token trong header) và lưu lượng đi theo cụm, nên trần "người dùng"
# chỉ làm gãy tích hợp chứ không tăng an toàn.
_DEFAULT_EXEMPT_VIEWS = frozenset(
    {
        # Prometheus scrape 15s/lần = 240 req/h — vượt trần anon.
        "orders-metrics",
        # Landing page + Lark đẩy đơn theo cụm; chặn nhầm = mất đơn thật.
        "orders-ingest",
        "orders-lark-ingest",
        # Ingest bảo hành máy-tới-máy (khác form đăng ký public — cái đó có trần riêng).
        "warranty-ingest",
        # Webhook đối tác: họ tự retry theo cụm, ta không kiểm soát được nhịp.
        "best-express-webhook",
        "kiotviet-invoice-webhook",
        # QUAN TRỌNG: refresh token chạy khi CHƯA có access token → request là anon,
        # key theo IP. Cả văn phòng ra internet chung một IP NAT: 150 nhân sự x ~4
        # lần refresh/giờ = 600 req/h, chạm đúng trần anon và đá cả công ty ra khỏi
        # phiên. Chống flood ở đây là việc của zone `desi_api_auth` bên nginx.
        "users-token-refresh",
    }
)


def _exempt_views() -> frozenset[str]:
    """Danh sách mặc định CỘNG THÊM ``THROTTLE_EXEMPT_VIEWS`` (không thay thế).

    Cộng thêm chứ không ghi đè: đặt nhầm biến env mà mất `orders-metrics` thì
    Prometheus chết lặng lẽ — đúng loại sự cố khó tìm nhất.
    """
    extra = getattr(settings, "THROTTLE_EXEMPT_VIEWS", ()) or ()
    return _DEFAULT_EXEMPT_VIEWS | frozenset(
        str(name).strip() for name in extra if str(name).strip()
    )


class _FixedWindowThrottle(BaseThrottle):
    """Trần mặc định theo cửa sổ cố định (:mod:`apps.common.rate_limiter`).

    Cố ý KHÔNG kế thừa ``SimpleRateThrottle``: ở mức nghìn request/giờ, thuật toán
    danh-sách-timestamp của DRF phải đọc + ghi lại cả nghìn phần tử qua network
    trên MỖI request. Lý do đầy đủ ở docstring của ``rate_limiter``.

    Rate đọc từ ``DEFAULT_THROTTLE_RATES`` (qua ``api_settings`` để test dùng
    ``override_settings`` được). Rate rỗng = tắt hẳn trần đó.
    """

    scope = ""

    def get_rate(self) -> str | None:
        return (api_settings.DEFAULT_THROTTLE_RATES or {}).get(self.scope)

    def get_cache_key(self, request, view) -> str | None:
        """Khoá đếm, hoặc None để bỏ qua request này."""
        raise NotImplementedError

    def allow_request(self, request, view) -> bool:
        if self.get_view_name(request) in _exempt_views():
            return True

        parsed = rate_limiter.parse_rate(self.get_rate())
        if parsed is None:
            return True

        key = self.get_cache_key(request, view)
        if key is None:
            return True

        limit, window = parsed
        decision = rate_limiter.hit(key, limit, window)
        self._retry_after = decision.retry_after
        return decision.allowed

    def wait(self) -> float:
        """Số giây tới khi cửa sổ mở lại — DRF đưa vào header ``Retry-After``."""
        return getattr(self, "_retry_after", 0.0)

    @staticmethod
    def get_view_name(request) -> str:
        """Tên URL đã resolve (``name=`` trong urls.py).

        Dùng tên URL chứ không dùng tiền tố path: đổi cấu trúc URL thì miễn trừ
        phải gãy ồn ào (test đỏ) chứ không được biến mất im lặng.
        ``resolver_match`` chắc chắn đã có ở đây vì Django resolve xong mới gọi
        view, còn DRF kiểm tra throttle trong ``initial()``.
        """
        match = getattr(request, "resolver_match", None)
        return getattr(match, "view_name", "") or ""


class DefaultUserRateThrottle(ThrottleGuardMixin, _FixedWindowThrottle):
    """Trần mặc định cho user đã đăng nhập — van chống chạy loạn, không phải hạn ngạch.

    Cố ý đặt RỘNG: mục tiêu là cắt vòng lặp vô hạn của một tab kẹt hoặc script,
    chứ không phải bó thao tác của nhân viên. Chặn oan một người đang làm việc
    đắt hơn nhiều so với để một script rác chạy thêm vài phút.
    """

    scope = "user"

    def get_cache_key(self, request, view) -> str | None:
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return None  # request ẩn danh đã có DefaultAnonRateThrottle lo
        return f"thr:user:{user.pk}"


class DefaultAnonRateThrottle(ThrottleGuardMixin, ClientIpMixin, _FixedWindowThrottle):
    """Trần mặc định cho request ẩn danh, key theo IP client thật.

    Nhớ: cả văn phòng ra internet chung MỘT IP NAT, nên trần này phải đủ rộng cho
    toàn công ty. Các endpoint ẩn danh có lưu lượng thật (refresh token, webhook,
    ingest) đều nằm trong danh sách miễn trừ ở trên.
    """

    scope = "anon"

    def get_cache_key(self, request, view) -> str | None:
        user = getattr(request, "user", None)
        if getattr(user, "is_authenticated", False):
            return None  # đã có DefaultUserRateThrottle lo
        ident = self.get_ident(request)
        return f"thr:anon:{ident}" if ident else None


class HermesApiThrottle(ThrottleGuardMixin, ClientIpMixin, _FixedWindowThrottle):
    """Trần riêng cho kênh máy-tới-máy Hermes (transcript cuộc gọi).

    Scope riêng chứ không dùng bucket ``anon`` dùng chung: agent kéo dữ liệu theo
    cụm hàng trăm request liên tiếp, nhét chung bucket với các endpoint public
    khác thì hai bên tự đá nhau ra. Key theo IP client — endpoint không có user.
    """

    scope = "hermes"

    def get_cache_key(self, request, view) -> str | None:
        ident = self.get_ident(request)
        return f"thr:hermes:{ident}" if ident else None


class DailyReportApiThrottle(ThrottleGuardMixin, ClientIpMixin, _FixedWindowThrottle):
    """Trần riêng cho trợ lý Daily Report (apps.daily_report). Cùng lý do tách
    scope như Hermes: không nhét agent vào bucket ``anon`` dùng chung."""

    scope = "daily_report"

    def get_cache_key(self, request, view) -> str | None:
        ident = self.get_ident(request)
        return f"thr:daily_report:{ident}" if ident else None


class ConversionFeedApiThrottle(ThrottleGuardMixin, ClientIpMixin, _FixedWindowThrottle):
    """Trần riêng cho feed conversion (apps.conversion_feed). Cùng lý do tách
    scope như Hermes: bên gọi kéo theo cụm, không nhét chung bucket ``anon``."""

    scope = "conversion_feed"

    def get_cache_key(self, request, view) -> str | None:
        ident = self.get_ident(request)
        return f"thr:conversion_feed:{ident}" if ident else None


class PancakePosWebhookThrottle(ThrottleGuardMixin, ClientIpMixin, _FixedWindowThrottle):
    """Trần riêng cho webhook Pancake POS (apps.pancake_pos). Pancake bắn theo cụm
    (kiểm kê/nhập hàng = hàng trăm sự kiện tồn trong một phút) nên không dùng bucket
    ``anon``; trần rộng — là van chống spam khi lộ URL, không phải hạn ngạch."""

    scope = "pancake_pos_webhook"
    DEFAULT_RATE = "30000/hour"

    def get_rate(self) -> str | None:
        # Mặc định trong code (settings base.py đã chạm trần ratchet kích thước file);
        # vẫn ghi đè được qua DEFAULT_THROTTLE_RATES["pancake_pos_webhook"].
        return super().get_rate() or self.DEFAULT_RATE

    def get_cache_key(self, request, view) -> str | None:
        ident = self.get_ident(request)
        return f"thr:pancake_pos_webhook:{ident}" if ident else None
