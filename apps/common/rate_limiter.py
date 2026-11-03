"""Rate limiter cửa sổ cố định (fixed window) trên Redis — dùng chung cho cả
trần HTTP vào (DRF throttle mặc định) lẫn trần gọi ra ngoài (KiotViet).

Vì sao KHÔNG dùng thuật toán của ``SimpleRateThrottle``
------------------------------------------------------
DRF lưu **danh sách timestamp của từng request** trong cache rồi cắt đuôi mỗi lần
gọi. Ở mức trần thấp (10/min cho login) thì không sao, nhưng trần mặc định toàn
hệ thống phải đặt cao (nghìn request/giờ mỗi user) — khi đó mỗi request phải đọc
+ ghi lại một list vài nghìn phần tử qua network. Trần mặc định không được phép
tốn kém hơn chính công việc mà nó bảo vệ.

Cửa sổ cố định chỉ cần ``INCR`` (1 lệnh Redis ở trạng thái thường) và O(1) bộ nhớ.

Đánh đổi đã biết: ở ranh giới hai cửa sổ, client có thể đạt gần gấp đôi trần
(cuối cửa sổ N + đầu cửa sổ N+1). Chấp nhận được vì đây là **trần chống chạy
loạn**, không phải hạn ngạch tính tiền.

Fail-open
---------
Redis chết thì hệ thống mất rate limit — KHÔNG được biến thành 500 cho toàn bộ
API. Mọi lỗi cache đều bị nuốt, ghi log, và request được cho qua.

Xem docs/rate-limiting-hardening-2026-07.md.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass

from django.core.cache import cache

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter

except Exception:  # pragma: no cover - prometheus là optional, giống http_metrics.py

    class _Noop:
        def labels(self, *_args, **_kwargs):
            return self

        def inc(self, *_args, **_kwargs):
            return None

    def Counter(*_args, **_kwargs):  # type: ignore[no-redef]
        return _Noop()


throttled_requests_total = Counter(
    "desi_throttled_requests_total",
    "Số request HTTP bị từ chối vì vượt trần tần suất, theo scope throttle.",
    ["scope"],
)

outbound_rate_limit_waits_total = Counter(
    "desi_outbound_rate_limit_waits_total",
    "Số lần một lệnh gọi ra ngoài phải chờ vì chạm trần tần suất tự áp.",
    ["service"],
)

outbound_rate_limit_wait_seconds_total = Counter(
    "desi_outbound_rate_limit_wait_seconds_total",
    "Tổng thời gian đã chờ trước khi gọi ra ngoài, theo dịch vụ.",
    ["service"],
)

outbound_rate_limit_exceeded_total = Counter(
    "desi_outbound_rate_limit_exceeded_total",
    "Số lần chờ quá max_wait mà vẫn chưa tới lượt (limiter quá chặt hoặc job chạy loạn).",
    ["service"],
)

_PERIOD_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

# Nhiều tiến trình cùng bị chặn sẽ cùng thức dậy đúng đầu cửa sổ sau và lại đâm
# vào nhau. Cộng một nhiễu nhỏ để rải chúng ra.
_WAKE_JITTER_SECONDS = 0.05


@dataclass(frozen=True)
class Decision:
    """Kết quả một lượt đếm.

    ``retry_after`` là số giây tới khi cửa sổ hiện tại đóng — dùng cho header
    ``Retry-After`` và cho vòng chờ của :func:`acquire`.
    """

    allowed: bool
    count: int
    retry_after: float


def parse_rate(rate: str | None) -> tuple[int, int] | None:
    """``"120/min"`` → ``(120, 60)``. Trả None nếu rỗng/sai định dạng (= không giới hạn).

    Chấp nhận đúng cú pháp của DRF (``s``/``sec``, ``m``/``min``, ``h``/``hour``,
    ``d``/``day``) để đổi qua lại giữa hai thuật toán không phải sửa cấu hình.
    """
    if not rate:
        return None
    num, _, period = str(rate).partition("/")
    try:
        limit = int(num)
        window = _PERIOD_SECONDS[period[0].lower()]
    except (ValueError, KeyError, IndexError):
        logger.error("rate_limiter: rate không hợp lệ %r — bỏ qua giới hạn", rate)
        return None
    if limit <= 0:
        return None
    return limit, window


def hit(key: str, limit: int, window: int) -> Decision:
    """Đếm một lượt cho ``key`` trong cửa sổ hiện tại.

    Cửa sổ được neo theo mốc thời gian tuyệt đối (``epoch // window``) chứ không
    theo lần chạm đầu tiên, nên mọi tiến trình/worker đều thống nhất cùng một
    cửa sổ mà không cần đồng bộ gì thêm.
    """
    if limit <= 0 or window <= 0:
        return Decision(allowed=True, count=0, retry_after=0.0)

    now = time.time()
    window_start = int(now // window) * window
    cache_key = f"rl:{key}:{window_start}"
    # +1 để key sống hết cửa sổ kể cả khi đồng hồ lệch chút ít, và tự dọn ngay sau đó.
    ttl = window + 1

    try:
        count = cache.incr(cache_key)
    except ValueError:
        # Cửa sổ mới (key chưa tồn tại). `add` là SET NX nguyên tử nên hai tiến
        # trình cùng khởi tạo thì chỉ một cái thắng, cái kia vẫn đếm đúng.
        try:
            cache.add(cache_key, 0, timeout=ttl)
            count = cache.incr(cache_key)
        except Exception:
            logger.exception("rate_limiter: không khởi tạo được cửa sổ %s — cho qua", cache_key)
            return Decision(allowed=True, count=0, retry_after=0.0)
    except Exception:
        logger.exception("rate_limiter: cache lỗi khi đếm %s — cho qua", cache_key)
        return Decision(allowed=True, count=0, retry_after=0.0)

    retry_after = max(0.0, (window_start + window) - now)
    return Decision(allowed=count <= limit, count=count, retry_after=retry_after)


def acquire(
    key: str,
    limit: int,
    window: int = 1,
    max_wait: float = 10.0,
    service: str = "outbound",
) -> bool:
    """Chờ tới lượt rồi trả True; trả False nếu quá ``max_wait`` mà vẫn chưa tới.

    Dùng cho chiều GỌI RA (KiotViet): khác với throttle HTTP vào — nơi từ chối
    thẳng là đúng — ở đây **chờ** mới là hành vi đúng, vì bên gọi là job đồng bộ
    chứ không phải người dùng đang ngồi đợi.

    Trả False = fail-open có chủ đích: bên gọi vẫn nên đi tiếp. Chặn hẳn một job
    đồng bộ để bảo vệ trần do CHÍNH TA tự đặt là đổi một sự cố nhỏ lấy một sự cố
    lớn hơn. Metric ``desi_outbound_rate_limit_exceeded_total`` để biết mà nới.
    """
    if limit <= 0:
        return True

    deadline = time.monotonic() + max(0.0, max_wait)
    waited = 0.0

    while True:
        decision = hit(key, limit, window)
        if decision.allowed:
            if waited > 0:
                outbound_rate_limit_waits_total.labels(service).inc()
                outbound_rate_limit_wait_seconds_total.labels(service).inc(waited)
            return True

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            outbound_rate_limit_exceeded_total.labels(service).inc()
            logger.warning(
                "rate_limiter: %s chờ quá %.1fs vẫn chạm trần (%s, %d/%ds) — vẫn gọi tiếp",
                service,
                max_wait,
                key,
                limit,
                window,
            )
            if waited > 0:
                outbound_rate_limit_waits_total.labels(service).inc()
                outbound_rate_limit_wait_seconds_total.labels(service).inc(waited)
            return False

        sleep_for = min(
            decision.retry_after + random.uniform(0, _WAKE_JITTER_SECONDS),  # noqa: S311
            remaining,
        )
        time.sleep(sleep_for)
        waited += sleep_for


def record_throttled(scope: str) -> None:
    """Ghi nhận một request bị throttle (mọi scope, kể cả trần mặc định)."""
    throttled_requests_total.labels(scope or "unknown").inc()
