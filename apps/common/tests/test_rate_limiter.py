"""Tests cho apps.common.rate_limiter — cửa sổ cố định, fail-open, vòng chờ.

Cô lập state: test chạy trên Redis THẬT dùng chung nên KHÔNG BAO GIỜ `cache.clear()`
(RedisCache.clear() = FLUSHDB). Mỗi test dùng fixture `isolated_cache` (backend/conftest.py)
bơm một LocMemCache riêng qua settings.CACHES.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.common import rate_limiter


class _Clock:
    """Đồng hồ giả: `sleep` đẩy thời gian tới thay vì chờ thật."""

    def __init__(self, start: float = 1_000_000.0):
        self.now = start
        self.slept = 0.0

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.slept += seconds


@pytest.fixture
def clock():
    fake = _Clock()
    with patch.object(rate_limiter, "time", fake):
        yield fake


# ── parse_rate ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("rate", "expected"),
    [
        ("120/min", (120, 60)),
        ("10/s", (10, 1)),
        ("10/sec", (10, 1)),
        ("5/hour", (5, 3600)),
        ("3/day", (3, 86400)),
    ],
)
def test_parse_rate_accepts_drf_syntax(rate, expected):
    """Cùng cú pháp với DRF để đổi qua lại giữa hai thuật toán không phải sửa config."""
    assert rate_limiter.parse_rate(rate) == expected


@pytest.mark.parametrize("rate", [None, "", "abc", "10/năm", "0/min", "-5/min"])
def test_parse_rate_invalid_means_no_limit(rate):
    """Rate hỏng/rỗng = KHÔNG giới hạn, không phải chặn hết — cấu hình sai không được thành sự cố."""
    assert rate_limiter.parse_rate(rate) is None


# ── hit ───────────────────────────────────────────────────────────────────────


def test_hit_allows_up_to_limit_then_denies(isolated_cache):
    decisions = [rate_limiter.hit("k1", limit=3, window=60) for _ in range(4)]
    assert [d.allowed for d in decisions] == [True, True, True, False]
    assert [d.count for d in decisions] == [1, 2, 3, 4]


def test_hit_isolates_keys(isolated_cache):
    rate_limiter.hit("user-a", limit=1, window=60)
    assert rate_limiter.hit("user-b", limit=1, window=60).allowed is True


def test_hit_resets_at_window_boundary(isolated_cache, clock):
    assert rate_limiter.hit("k2", limit=1, window=60).allowed is True
    assert rate_limiter.hit("k2", limit=1, window=60).allowed is False

    clock.now += 60  # sang cửa sổ kế tiếp
    assert rate_limiter.hit("k2", limit=1, window=60).allowed is True


def test_hit_retry_after_is_time_left_in_window(isolated_cache, clock):
    # Neo đồng hồ vào giữa cửa sổ 60s để retry_after không phải một con số tròn tình cờ.
    clock.now = 1_000_020.0
    decision = rate_limiter.hit("k3", limit=1, window=60)
    assert 0 < decision.retry_after <= 60


def test_hit_without_limit_is_noop(isolated_cache):
    assert rate_limiter.hit("k4", limit=0, window=60).allowed is True


def test_hit_fails_open_when_cache_is_down():
    """Redis chết ⇒ mất rate limit, KHÔNG được thành 500 cho toàn hệ thống."""
    broken = SimpleNamespace(
        incr=lambda *a, **k: (_ for _ in ()).throw(ConnectionError("redis down")),
        add=lambda *a, **k: (_ for _ in ()).throw(ConnectionError("redis down")),
    )
    with patch.object(rate_limiter, "cache", broken):
        decision = rate_limiter.hit("k5", limit=1, window=60)
    assert decision.allowed is True


def test_hit_fails_open_when_key_vanishes_between_add_and_incr():
    """Key hết hạn xen giữa add và incr: hiếm, nhưng vẫn phải cho qua chứ không nổ."""
    broken = SimpleNamespace(
        incr=lambda *a, **k: (_ for _ in ()).throw(ValueError("Key not found")),
        add=lambda *a, **k: True,
    )
    with patch.object(rate_limiter, "cache", broken):
        assert rate_limiter.hit("k6", limit=5, window=60).allowed is True


# ── acquire ───────────────────────────────────────────────────────────────────


def test_acquire_returns_immediately_under_limit(isolated_cache, clock):
    assert rate_limiter.acquire("out-1", limit=5, window=1, max_wait=10) is True
    assert clock.slept == 0


def test_acquire_waits_for_next_window_then_proceeds(isolated_cache, clock):
    assert rate_limiter.acquire("out-2", limit=1, window=1, max_wait=10) is True
    assert rate_limiter.acquire("out-2", limit=1, window=1, max_wait=10) is True
    # Lần hai phải CHỜ tới cửa sổ sau chứ không được đi ngay.
    assert 0 < clock.slept <= 1.1


def test_acquire_gives_up_after_max_wait_but_still_allows(isolated_cache, clock):
    """Fail-open có chủ đích: chặn hẳn job đồng bộ để giữ trần TỰ ĐẶT là đổi sự cố nhỏ lấy sự cố to."""
    rate_limiter.acquire("out-3", limit=1, window=60, max_wait=0)
    assert rate_limiter.acquire("out-3", limit=1, window=60, max_wait=0) is False


def test_acquire_without_limit_is_noop(isolated_cache, clock):
    assert rate_limiter.acquire("out-4", limit=0, window=1, max_wait=10) is True
    assert clock.slept == 0
