"""Tests cho apps.common.cache_backend — ranh giới fail-soft/fail-loud + metric.

Thứ đang được khoá ở đây KHÔNG phải "cache chạy được", mà là **quyết định thiết
kế**: thao tác nào được phép nuốt lỗi Redis và thao tác nào bắt buộc phải để lỗi
nổ ra. Nhầm ranh giới đó biến "Redis hỏng" thành "hai worker cùng chạy một job
sync" hoặc "rate limit biến mất" — xem docstring của module.

Không chạm Redis thật: `RedisCache` mở connection lười (property `_cache`), nên
chỉ cần patch phương thức của lớp cha là dựng được cả hai kịch bản.
"""

from unittest.mock import patch

import pytest
from django.core.cache.backends.redis import RedisCache
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.common.cache_backend import (
    InstrumentedRedisCache,
    cache_metrics_snapshot,
)


@pytest.fixture
def cache():
    # Params rỗng: constructor không nối Redis, mọi test đều patch lớp cha.
    return InstrumentedRedisCache("redis://localhost:6379/0", {})


@pytest.fixture(autouse=True)
def reset_error_log_throttle():
    """Bộ hãm log dùng state module-level; xoá để test không phụ thuộc thứ tự chạy."""
    from apps.common import cache_backend

    cache_backend._last_error_log.clear()
    yield
    cache_backend._last_error_log.clear()


def _count(label: str) -> float:
    """Giá trị counter theo nhãn `operation:result`, 0 nếu chưa từng được ghi."""
    return cache_metrics_snapshot().get(label, 0.0)


class TestFailSoftReadPath:
    """Redis chết → hệ thống CHẬM (chạy như cache miss), không NGÃ."""

    def test_get_swallows_redis_error_and_returns_default(self, cache):
        with patch.object(RedisCache, "get", side_effect=RedisConnectionError("down")):
            assert cache.get("k", "fallback") == "fallback"

    def test_get_many_swallows_redis_error_and_returns_empty(self, cache):
        with patch.object(RedisCache, "get_many", side_effect=RedisConnectionError("down")):
            assert cache.get_many(["a", "b"]) == {}

    def test_set_swallows_redis_error(self, cache):
        with patch.object(RedisCache, "set", side_effect=RedisConnectionError("down")):
            assert cache.set("k", "v") is None

    def test_set_many_returns_all_keys_as_failed_on_error(self, cache):
        # Hợp đồng của BaseCache.set_many là trả về danh sách key GHI KHÔNG ĐƯỢC.
        with patch.object(RedisCache, "set_many", side_effect=RedisConnectionError("down")):
            assert sorted(cache.set_many({"a": 1, "b": 2})) == ["a", "b"]

    def test_delete_swallows_redis_error(self, cache):
        # `delete` thường nằm trong `finally` của khối khoá: ném lỗi ở đó sẽ CHE MẤT
        # exception thật của công việc bên trong, mà khoá thì đã có TTL tự hết hạn.
        with patch.object(RedisCache, "delete", side_effect=RedisConnectionError("down")):
            assert cache.delete("lock") is False

    def test_touch_and_has_key_swallow_redis_error(self, cache):
        with patch.object(RedisCache, "touch", side_effect=RedisConnectionError("down")):
            assert cache.touch("k") is False
        with patch.object(RedisCache, "has_key", side_effect=RedisConnectionError("down")):
            assert cache.has_key("k") is False


class TestFailLoudLockPath:
    """`add`/`incr` là primitive của khoá phân tán và rate limit — lỗi PHẢI nổ."""

    def test_add_propagates_redis_error(self, cache):
        # Nuốt lỗi ở đây = job sync im lặng không bao giờ chạy (nếu trả False), hoặc
        # hai worker cùng vào một job (nếu trả True). Cả hai đều tệ hơn task fail.
        with patch.object(RedisCache, "add", side_effect=RedisConnectionError("down")):
            with pytest.raises(RedisConnectionError):
                cache.add("lock", 1, 60)

    def test_incr_propagates_redis_error(self, cache):
        # Nuốt lỗi ở đây = trần rate limit (Gemini RPM/RPD, login) biến mất.
        with patch.object(RedisCache, "incr", side_effect=RedisConnectionError("down")):
            with pytest.raises(RedisConnectionError):
                cache.incr("quota")

    def test_add_still_returns_value_when_redis_healthy(self, cache):
        with patch.object(RedisCache, "add", return_value=True):
            assert cache.add("lock", 1, 60) is True


class TestMetrics:
    def test_hit_and_miss_counted_separately(self, cache):
        before_hit = _count("get:hit")
        before_miss = _count("get:miss")

        with patch.object(RedisCache, "get", return_value="value"):
            cache.get("k")
        # Miss = lớp cha trả về đúng sentinel đã truyền xuống, không phải trả None.
        with patch.object(RedisCache, "get", side_effect=lambda key, default, version: default):
            cache.get("k")

        assert _count("get:hit") == before_hit + 1
        assert _count("get:miss") == before_miss + 1

    def test_cached_none_counts_as_hit_not_miss(self, cache):
        # Phân biệt "key không tồn tại" với "key tồn tại, giá trị là None". Dùng
        # `default` của caller làm mốc so sánh sẽ đếm nhầm ca thứ hai thành miss.
        before_hit = _count("get:hit")
        with patch.object(RedisCache, "get", return_value=None):
            assert cache.get("k", "fallback") is None
        assert _count("get:hit") == before_hit + 1

    def test_get_many_counts_per_key_not_per_call(self, cache):
        before_hit = _count("get_many:hit")
        before_miss = _count("get_many:miss")

        with patch.object(RedisCache, "get_many", return_value={"a": 1, "b": 2}):
            cache.get_many(["a", "b", "c", "d"])

        assert _count("get_many:hit") == before_hit + 2
        assert _count("get_many:miss") == before_miss + 2

    def test_error_counted_on_both_fail_soft_and_fail_loud(self, cache):
        before_get = _count("get:error")
        before_add = _count("add:error")

        with patch.object(RedisCache, "get", side_effect=RedisConnectionError("down")):
            cache.get("k")
        with patch.object(RedisCache, "add", side_effect=RedisConnectionError("down")):
            with pytest.raises(RedisConnectionError):
                cache.add("k", 1)

        assert _count("get:error") == before_get + 1
        assert _count("add:error") == before_add + 1


class TestDecrNotOverridden:
    def test_decr_delegates_to_incr_and_counts_once(self, cache):
        # `RedisCache` không tự cài `decr`; `BaseCache.decr` gọi lại `self.incr`.
        # Override thêm `decr` sẽ đếm mỗi lần decr thành 2 thao tác.
        before = _count("incr:ok")
        with patch.object(RedisCache, "incr", return_value=4):
            assert cache.decr("counter") == 4
        assert _count("incr:ok") == before + 1
        assert _count("decr:ok") == 0
