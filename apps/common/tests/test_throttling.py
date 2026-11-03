"""Tests cho throttling dùng chung: IP client thật, guard fail-open, trần mặc định.

Cô lập state: test chạy trên Redis THẬT dùng chung nên KHÔNG BAO GIỜ `cache.clear()`.
Các test trần mặc định dùng fixture `isolated_cache` (backend/conftest.py): LocMemCache
riêng từng test qua settings.CACHES.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework.test import APIRequestFactory

from apps.common import rate_limiter
from apps.common.throttling import (
    ClientIpMixin,
    DefaultAnonRateThrottle,
    DefaultUserRateThrottle,
    ThrottleGuardMixin,
)
from apps.users.views import LoginRateThrottle
from apps.warranty.views import WarrantyRegisterThrottle

factory = APIRequestFactory()


class _Ident(ClientIpMixin):
    pass


def _ident(**extra):
    request = factory.post("/api/auth/login", REMOTE_ADDR="10.0.0.9", **extra)
    return _Ident().get_ident(request)


def test_cf_connecting_ip_wins():
    assert (
        _ident(
            HTTP_CF_CONNECTING_IP="203.0.113.7",
            HTTP_X_FORWARDED_FOR="198.51.100.1, 172.16.0.2",
        )
        == "203.0.113.7"
    )


@pytest.mark.parametrize(
    ("trusted_proxies", "xff", "expected"),
    [
        # 1 hop tin cậy (nginx) → entry cuối là IP mà nginx nhìn thấy.
        pytest.param(1, "198.51.100.1, 172.16.0.2", "172.16.0.2", id="one_trusted_hop"),
        pytest.param(2, "203.0.113.9, 198.51.100.1, 172.16.0.2", "198.51.100.1", id="two_trusted_hops"),
        # Số hop tin cậy lớn hơn chiều dài chuỗi → lấy entry đầu, không IndexError.
        pytest.param(5, "198.51.100.1", "198.51.100.1", id="capped_at_chain_length"),
    ],
)
def test_xff_picks_ip_by_trusted_hops(settings, trusted_proxies, xff, expected):
    settings.THROTTLE_TRUSTED_PROXIES = trusted_proxies
    assert _ident(HTTP_X_FORWARDED_FOR=xff) == expected


def test_fallback_remote_addr():
    assert _ident() == "10.0.0.9"


def test_login_throttle_keys_differ_by_cf_header():
    throttle = LoginRateThrottle()
    req_a = factory.post("/api/auth/login", REMOTE_ADDR="10.0.0.9")
    req_b = factory.post(
        "/api/auth/login", REMOTE_ADDR="10.0.0.9", HTTP_CF_CONNECTING_IP="203.0.113.7"
    )
    key_a = throttle.get_cache_key(req_a, None)
    key_b = throttle.get_cache_key(req_b, None)
    assert key_a != key_b
    assert "203.0.113.7" in key_b


def test_warranty_throttle_uses_client_ip():
    throttle = WarrantyRegisterThrottle()
    request = factory.post(
        "/api/warranty/register", REMOTE_ADDR="10.0.0.9", HTTP_CF_CONNECTING_IP="203.0.113.7"
    )
    assert "203.0.113.7" in throttle.get_cache_key(request, None)


# ── ThrottleGuardMixin: fail-open + metric ────────────────────────────────────


class _Denying:
    scope = "deny-scope"

    def allow_request(self, request, view):
        return False


class _Exploding:
    scope = "boom-scope"

    def allow_request(self, request, view):
        raise ConnectionError("redis down")


class _GuardedDeny(ThrottleGuardMixin, _Denying):
    pass


class _GuardedBoom(ThrottleGuardMixin, _Exploding):
    pass


def test_guard_lets_request_through_when_cache_explodes():
    """Redis chết ⇒ mất rate limit, KHÔNG được biến mọi endpoint có throttle thành 500."""
    assert _GuardedBoom().allow_request(factory.get("/api/orders"), None) is True


def test_guard_records_metric_on_deny():
    with patch.object(rate_limiter, "record_throttled") as record:
        assert _GuardedDeny().allow_request(factory.get("/api/orders"), None) is False
    record.assert_called_once_with("deny-scope")


def test_guard_stays_silent_when_allowed():
    class _Allowing:
        scope = "ok-scope"

        def allow_request(self, request, view):
            return True

    class _Guarded(ThrottleGuardMixin, _Allowing):
        pass

    with patch.object(rate_limiter, "record_throttled") as record:
        assert _Guarded().allow_request(factory.get("/api/orders"), None) is True
    record.assert_not_called()


def test_business_throttles_carry_the_guard():
    """Throttle nghiệp vụ nào cũng phải fail-open + có metric, không chỉ trần mặc định."""
    for throttle_cls in (LoginRateThrottle, WarrantyRegisterThrottle):
        assert issubclass(throttle_cls, ThrottleGuardMixin)


# ── Trần mặc định (DefaultUser/DefaultAnon) ───────────────────────────────────


@pytest.fixture
def rates(settings):
    """Ghi đè DEFAULT_THROTTLE_RATES qua đúng đường DRF đọc (api_settings tự reload)."""

    def _set(**scopes):
        settings.REST_FRAMEWORK = {
            **settings.REST_FRAMEWORK,
            "DEFAULT_THROTTLE_RATES": {
                **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
                **scopes,
            },
        }

    return _set


def _request(*, user=None, ip="203.0.113.7", view_name="orders-list"):
    request = factory.get("/api/orders", HTTP_CF_CONNECTING_IP=ip)
    request.user = user or SimpleNamespace(is_authenticated=False)
    request.resolver_match = SimpleNamespace(view_name=view_name)
    return request


_LOGGED_IN = SimpleNamespace(pk=42, is_authenticated=True)


def test_user_throttle_denies_past_limit(isolated_cache, rates):
    rates(user="2/min")
    throttle = DefaultUserRateThrottle()
    request = _request(user=_LOGGED_IN)
    assert [throttle.allow_request(request, None) for _ in range(3)] == [True, True, False]


def test_user_throttle_separates_users(isolated_cache, rates):
    rates(user="1/min")
    throttle = DefaultUserRateThrottle()
    assert throttle.allow_request(_request(user=_LOGGED_IN), None) is True
    other = SimpleNamespace(pk=43, is_authenticated=True)
    assert throttle.allow_request(_request(user=other), None) is True


def test_user_throttle_ignores_anonymous(isolated_cache, rates):
    """Request ẩn danh là việc của DefaultAnonRateThrottle — không đếm hai lần."""
    rates(user="1/min")
    throttle = DefaultUserRateThrottle()
    assert [throttle.allow_request(_request(), None) for _ in range(3)] == [True, True, True]


def test_anon_throttle_keys_by_real_client_ip(isolated_cache, rates):
    rates(anon="1/min")
    throttle = DefaultAnonRateThrottle()
    assert throttle.allow_request(_request(ip="203.0.113.7"), None) is True
    assert throttle.allow_request(_request(ip="203.0.113.7"), None) is False
    # IP khác = bucket khác: client không gộp chung vì cùng đi qua một proxy.
    assert throttle.allow_request(_request(ip="198.51.100.4"), None) is True


def test_anon_throttle_ignores_authenticated(isolated_cache, rates):
    rates(anon="1/min")
    throttle = DefaultAnonRateThrottle()
    assert [
        throttle.allow_request(_request(user=_LOGGED_IN), None) for _ in range(3)
    ] == [True, True, True]


@pytest.mark.parametrize(
    "view_name",
    [
        "orders-metrics",  # Prometheus scrape 240 req/h
        "orders-ingest",  # landing page
        "users-token-refresh",  # cả văn phòng chung một IP NAT
        "kiotviet-invoice-webhook",
    ],
)
def test_machine_endpoints_are_exempt(isolated_cache, rates, view_name):
    rates(anon="1/min")
    throttle = DefaultAnonRateThrottle()
    assert [
        throttle.allow_request(_request(view_name=view_name), None) for _ in range(5)
    ] == [True] * 5


def test_env_exempt_list_extends_and_never_replaces_defaults(isolated_cache, rates, settings):
    """Đặt nhầm env mà mất `orders-metrics` thì Prometheus chết lặng lẽ — không cho phép."""
    rates(anon="1/min")
    settings.THROTTLE_EXEMPT_VIEWS = ("some-custom-view",)
    throttle = DefaultAnonRateThrottle()
    assert throttle.allow_request(_request(view_name="some-custom-view"), None) is True
    assert throttle.allow_request(_request(view_name="some-custom-view"), None) is True
    assert throttle.allow_request(_request(view_name="orders-metrics"), None) is True
    assert throttle.allow_request(_request(view_name="orders-metrics"), None) is True


def test_empty_rate_disables_the_default_ceiling(isolated_cache, rates):
    rates(anon="")
    throttle = DefaultAnonRateThrottle()
    assert [throttle.allow_request(_request(), None) for _ in range(5)] == [True] * 5


def test_wait_reports_seconds_until_window_reopens(isolated_cache, rates):
    rates(anon="1/min")
    throttle = DefaultAnonRateThrottle()
    throttle.allow_request(_request(), None)
    throttle.allow_request(_request(), None)
    assert 0 < throttle.wait() <= 60


def test_denied_default_throttle_is_counted(isolated_cache, rates):
    rates(anon="1/min")
    throttle = DefaultAnonRateThrottle()
    with patch.object(rate_limiter, "record_throttled") as record:
        throttle.allow_request(_request(), None)
        throttle.allow_request(_request(), None)
    record.assert_called_once_with("anon")


def test_settings_wire_the_default_throttles(settings):
    """Có class mà quên rate (hoặc ngược lại) = trần mặc định im lặng không chạy."""
    rest = settings.REST_FRAMEWORK
    assert set(rest["DEFAULT_THROTTLE_CLASSES"]) == {
        "apps.common.throttling.DefaultUserRateThrottle",
        "apps.common.throttling.DefaultAnonRateThrottle",
    }
    for scope in (DefaultUserRateThrottle.scope, DefaultAnonRateThrottle.scope):
        assert scope in rest["DEFAULT_THROTTLE_RATES"]
