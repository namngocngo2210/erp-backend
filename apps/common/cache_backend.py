"""Cache backend Redis có đo đạc và không kéo cả hệ thống chết theo mình.

Vì sao cần (audit cache/latency 2026-08-11)
-------------------------------------------
1. **Cache đang mù.** ~80 điểm gọi ``cache.get``/``cache.set`` rải khắp
   ``apps/*`` mà không có một metric nào: không biết hit rate, không biết cache
   có đang hỏng hay không. Gắn metric ở tầng **backend** phủ trọn cả 80 điểm đó
   và mọi điểm thêm về sau, không phải sửa một dòng nào ở tầng nghiệp vụ.

2. **Cache không được phép là SPOF.** Từ 2026-08-11 cache chạy trên instance
   Redis RIÊNG (xem ``docs/cache-latency-optimization-2026-08-11.md`` §3.1). Một
   instance mới là một thứ mới có thể chết, và ``RedisCache`` mặc định của Django
   ném thẳng ``ConnectionError`` ra view → 500 hàng loạt. Đúng ra, cache chết thì
   hệ thống phải **chậm đi**, không phải **ngã**.

Ranh giới fail-soft / fail-loud
-------------------------------
Đây là quyết định thiết kế chính của module, và nó KHÔNG đồng nhất cho mọi thao
tác — vì cache trong codebase này gánh hai vai trò khác hẳn nhau:

* **Vai trò tăng tốc** — ``get``/``set``/``get_many``/``set_many``/``touch``/
  ``delete``: mất cache chỉ mất tốc độ. Nuốt lỗi, đếm ``result="error"``, chạy
  tiếp như cache miss.

* **Vai trò đúng đắn** — ``add``/``incr``/``decr``: đây là primitive của **khoá
  phân tán** (``cache.add(lock_key, 1, ttl)`` ở ``kiotviet/tasks.py``,
  ``inventory/tasks.py``, ``contact_flow/tasks.py``, ``common/db_guards.py``) và
  của **rate limit** (``common/rate_limiter.py``, ``callcenter/transcribe.py``).
  Nuốt lỗi ở đây biến "Redis hỏng" thành "hai worker cùng chạy một job sync" hoặc
  "trần gọi Gemini biến mất" — hỏng dữ liệu và hỏng hoá đơn, tệ hơn nhiều so với
  hỏng dịch vụ. Lỗi phải nổ để Celery retry và để alert nhìn thấy.

``delete`` nằm ở nhóm fail-soft dù nó thường đứng trong ``finally`` của khối
khoá: khoá luôn có TTL nên tự hết hạn, trong khi ném lỗi từ ``finally`` sẽ **che
mất** exception thật của công việc bên trong.

Ranh giới này được khoá bằng test (``apps/common/tests/test_cache_backend.py``).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.redis import RedisCache
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Histogram

    _ENABLED = True
except Exception:  # pragma: no cover - prometheus là optional, giống apps/common/http_metrics.py
    _ENABLED = False

    class _Noop:
        def labels(self, *_args, **_kwargs):
            return self

        def inc(self, *_args, **_kwargs):
            return None

        def observe(self, *_args, **_kwargs):
            return None

    def Counter(*_args, **_kwargs):  # type: ignore[no-redef]
        return _Noop()

    def Histogram(*_args, **_kwargs):  # type: ignore[no-redef]
        return _Noop()


# `result` cố tình gộp cả hit/miss lẫn ok/error vào MỘT counter: hit rate là phép
# chia giữa hai label của cùng một metric, không phải phép chia giữa hai metric
# khác nhau (dễ lệch khi một trong hai chưa từng được ghi).
cache_operations_total = Counter(
    "desi_cache_operations_total",
    "Số thao tác cache theo loại và kết quả.",
    ["operation", "result"],  # result: hit | miss | ok | error
)

# Bucket dày ở dải dưới: cache lành mạnh phải nằm dưới 5ms. Chạm 100ms nghĩa là
# Redis đang nghẽn và cache đã thành gánh nặng thay vì tăng tốc.
cache_operation_duration_seconds = Histogram(
    "desi_cache_operation_duration_seconds",
    "Thời gian một thao tác cache.",
    ["operation"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
)

# Redis chết thì mọi request đều lỗi — log mỗi lần sẽ nhấn chìm log file và làm
# chậm chính tiến trình đang cứu. Chỉ log một dòng mỗi khoảng này cho mỗi thao tác.
_ERROR_LOG_INTERVAL_SECONDS = 60.0
_last_error_log: dict[str, float] = {}

# Phân biệt "key không tồn tại" với "key tồn tại và giá trị là None". Không dùng
# `default` của caller làm mốc so sánh vì caller có quyền truyền default khác None.
_MISSING = object()


def _log_error(operation: str, exc: Exception) -> None:
    now = time.monotonic()
    last = _last_error_log.get(operation, 0.0)
    if now - last < _ERROR_LOG_INTERVAL_SECONDS:
        return
    _last_error_log[operation] = now
    logger.warning("cache %s lỗi Redis (fail-soft, chạy tiếp như cache miss): %s", operation, exc)


class InstrumentedRedisCache(RedisCache):
    """``RedisCache`` + metric Prometheus + fail-soft cho đường đọc/ghi tăng tốc."""

    # ── Nhóm fail-soft ────────────────────────────────────────────────────────

    def get(self, key, default=None, version=None):
        start = time.monotonic()
        try:
            value = super().get(key, _MISSING, version)
        except RedisError as exc:
            cache_operations_total.labels("get", "error").inc()
            _log_error("get", exc)
            return default
        finally:
            cache_operation_duration_seconds.labels("get").observe(time.monotonic() - start)

        if value is _MISSING:
            cache_operations_total.labels("get", "miss").inc()
            return default
        cache_operations_total.labels("get", "hit").inc()
        return value

    def get_many(self, keys, version=None):
        keys = list(keys)
        start = time.monotonic()
        try:
            found = super().get_many(keys, version)
        except RedisError as exc:
            cache_operations_total.labels("get_many", "error").inc()
            _log_error("get_many", exc)
            return {}
        finally:
            cache_operation_duration_seconds.labels("get_many").observe(time.monotonic() - start)

        # Đếm theo KEY chứ không theo lời gọi: một `get_many` 50 key trúng 49 phải
        # đọc ra hit rate 98%, không phải "1 lời gọi thành công".
        hits = len(found)
        if hits:
            cache_operations_total.labels("get_many", "hit").inc(hits)
        misses = len(keys) - hits
        if misses:
            cache_operations_total.labels("get_many", "miss").inc(misses)
        return found

    def set(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        start = time.monotonic()
        try:
            result = super().set(key, value, timeout, version)
        except RedisError as exc:
            cache_operations_total.labels("set", "error").inc()
            _log_error("set", exc)
            return None
        finally:
            cache_operation_duration_seconds.labels("set").observe(time.monotonic() - start)
        cache_operations_total.labels("set", "ok").inc()
        return result

    def set_many(self, data, timeout=DEFAULT_TIMEOUT, version=None):
        start = time.monotonic()
        try:
            result = super().set_many(data, timeout, version)
        except RedisError as exc:
            cache_operations_total.labels("set_many", "error").inc()
            _log_error("set_many", exc)
            # Hợp đồng của BaseCache.set_many: trả danh sách key KHÔNG ghi được.
            return list(data)
        finally:
            cache_operation_duration_seconds.labels("set_many").observe(time.monotonic() - start)
        cache_operations_total.labels("set_many", "ok").inc()
        return result

    def touch(self, key, timeout=DEFAULT_TIMEOUT, version=None):
        start = time.monotonic()
        try:
            result = super().touch(key, timeout, version)
        except RedisError as exc:
            cache_operations_total.labels("touch", "error").inc()
            _log_error("touch", exc)
            return False
        finally:
            cache_operation_duration_seconds.labels("touch").observe(time.monotonic() - start)
        cache_operations_total.labels("touch", "ok").inc()
        return result

    def delete(self, key, version=None):
        start = time.monotonic()
        try:
            result = super().delete(key, version)
        except RedisError as exc:
            cache_operations_total.labels("delete", "error").inc()
            _log_error("delete", exc)
            return False
        finally:
            cache_operation_duration_seconds.labels("delete").observe(time.monotonic() - start)
        cache_operations_total.labels("delete", "ok").inc()
        return result

    def delete_many(self, keys, version=None):
        # RedisCache tự cài `delete_many` (xoá cả lô bằng một lệnh) thay vì lặp
        # `self.delete`, nên KHÔNG override ở đây là để lọt cả metric lẫn fail-soft.
        keys = list(keys)
        start = time.monotonic()
        try:
            result = super().delete_many(keys, version)
        except RedisError as exc:
            cache_operations_total.labels("delete_many", "error").inc()
            _log_error("delete_many", exc)
            return None
        finally:
            cache_operation_duration_seconds.labels("delete_many").observe(time.monotonic() - start)
        cache_operations_total.labels("delete_many", "ok").inc()
        return result

    def has_key(self, key, version=None):
        start = time.monotonic()
        try:
            result = super().has_key(key, version)
        except RedisError as exc:
            cache_operations_total.labels("has_key", "error").inc()
            _log_error("has_key", exc)
            return False
        finally:
            cache_operation_duration_seconds.labels("has_key").observe(time.monotonic() - start)
        cache_operations_total.labels("has_key", "hit" if result else "miss").inc()
        return result

    # ── Nhóm fail-loud (khoá phân tán + rate limit) ───────────────────────────
    #
    # Chỉ đo, KHÔNG bắt RedisError. Xem khối docstring đầu module để biết vì sao
    # nuốt lỗi ở đây nguy hiểm hơn để lỗi nổ.

    def add(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        start = time.monotonic()
        try:
            result = super().add(key, value, timeout, version)
        except RedisError:
            cache_operations_total.labels("add", "error").inc()
            raise
        finally:
            cache_operation_duration_seconds.labels("add").observe(time.monotonic() - start)
        cache_operations_total.labels("add", "ok").inc()
        return result

    def incr(self, key, delta=1, version=None):
        # `decr` KHÔNG được override: `RedisCache` không tự cài nó, `BaseCache.decr`
        # gọi `self.incr(key, -delta)` — override thêm sẽ đếm mỗi lần decr hai lần.
        try:
            result = super().incr(key, delta, version)
        except RedisError:
            cache_operations_total.labels("incr", "error").inc()
            raise
        cache_operations_total.labels("incr", "ok").inc()
        return result


def cache_metrics_snapshot() -> dict[str, Any]:
    """Ảnh chụp counter cache — dùng cho test và cho `/api/health` khi cần soi nhanh."""
    if not _ENABLED:
        return {}
    snapshot: dict[str, Any] = {}
    for metric in cache_operations_total.collect():
        for sample in metric.samples:
            if not sample.name.endswith("_total"):
                continue
            label = f"{sample.labels.get('operation')}:{sample.labels.get('result')}"
            snapshot[label] = sample.value
    return snapshot
