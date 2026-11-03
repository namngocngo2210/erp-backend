"""Tests cho tầng HTTP của apps.common: request-id, RED metrics, healthz.

Gộp từ: test_request_id.py, test_http_metrics.py, test_health.py
(restructure 2026-08-05).
"""

import asyncio
import logging
from unittest import mock

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase
from rest_framework.test import APIClient

import apps.common.health as health_mod
from apps.common.http_metrics import (
    HttpMetricsMiddleware,
    _route_label,
    http_requests_total,
)
from apps.common.request_id import (
    REQUEST_ID_HEADER,
    RequestIdFilter,
    RequestIdMiddleware,
    get_request_id,
)

# ---------------------------------------------------------------------------
# Request-id middleware + logging filter (audit 2026-07 mục 2.4).
# ---------------------------------------------------------------------------

factory = RequestFactory()


def _run(request):
    seen: dict[str, str | None] = {}

    def view(req):
        seen["rid"] = get_request_id()
        seen["request_attr"] = getattr(req, "request_id", None)
        return HttpResponse("ok")

    response = RequestIdMiddleware(view)(request)
    return response, seen


def test_inbound_header_is_echoed():
    request = factory.get("/api/x", HTTP_X_REQUEST_ID="abc-123_XYZ.7")
    response, seen = _run(request)
    assert response[REQUEST_ID_HEADER] == "abc-123_XYZ.7"
    assert seen["rid"] == "abc-123_XYZ.7"
    assert seen["request_attr"] == "abc-123_XYZ.7"


def test_missing_header_generates_uuid():
    response, seen = _run(factory.get("/api/x"))
    rid = response[REQUEST_ID_HEADER]
    assert len(rid) == 32  # uuid4().hex
    assert seen["rid"] == rid


def test_hostile_header_replaced():
    request = factory.get("/api/x", HTTP_X_REQUEST_ID='bad"value\nwith spaces')
    response, _ = _run(request)
    assert response[REQUEST_ID_HEADER] != 'bad"value\nwith spaces'
    assert len(response[REQUEST_ID_HEADER]) == 32


def test_oversized_header_replaced():
    request = factory.get("/api/x", HTTP_X_REQUEST_ID="a" * 200)
    response, _ = _run(request)
    assert len(response[REQUEST_ID_HEADER]) == 32


def test_context_reset_after_response():
    _run(factory.get("/api/x", HTTP_X_REQUEST_ID="req-1"))
    assert get_request_id() == "-"


def test_logging_filter_injects_request_id():
    captured = {}

    def view(req):
        record = logging.LogRecord("t", logging.INFO, __file__, 1, "msg", None, None)
        assert RequestIdFilter().filter(record) is True
        # request_id là attr động do RequestIdFilter gắn vào record.
        captured["record_rid"] = record.request_id  # type: ignore[attr-defined]
        return HttpResponse("ok")

    RequestIdMiddleware(view)(factory.get("/api/x", HTTP_X_REQUEST_ID="rid-log-1"))
    assert captured["record_rid"] == "rid-log-1"


# ---------------------------------------------------------------------------
# RED metrics tầng HTTP (audit tải 2026-07-24).
#
# Điểm cần bảo vệ nhất KHÔNG phải "có đếm hay không" mà là **cardinality**: label
# ``route`` phải là URL pattern. Nếu ai đó đổi sang ``request.path``, mỗi đơn hàng
# sẽ đẻ một time series và Prometheus chết trước hệ thống — test dưới đây chốt điều đó.
#
# Cố ý KHÔNG đăng nhập: middleware đo MỌI request bất kể kết quả xác thực, nên
# request 401 vẫn đủ để kiểm chứng nhãn và bộ đếm. Nhờ vậy nhóm test này không phải
# import ``apps.users`` — ``apps.common`` là tầng đáy trong contract phân tầng
# (backend/.importlinter), import ngược lên tầng trên là thêm nợ kiến trúc mới.
# ---------------------------------------------------------------------------


def _samples():
    """Mọi sample hiện có của counter, dạng (method, route, status) → giá trị."""
    return {
        (s.labels.get("method"), s.labels.get("route"), s.labels.get("status")): s.value
        for metric in http_requests_total.collect()
        for s in metric.samples
        if s.name.endswith("_total")
    }


def _routes_seen():
    return {route for (_m, route, _s) in _samples()}


class HttpMetricsRouteLabelTests(TestCase):
    """Label route phải gộp theo pattern, không tách theo id."""

    def setUp(self):
        self.client = APIClient()

    def test_detail_urls_with_different_ids_share_one_label(self):
        # Hai id khác nhau trên cùng một endpoint chi tiết phải rơi vào CÙNG label.
        self.client.get("/api/orders/424242")
        self.client.get("/api/orders/525252")

        for route in _routes_seen():
            self.assertNotIn(
                "424242",
                route or "",
                f"route label chứa id thật ({route}) → mỗi đơn hàng là một time "
                "series riêng. Phải dùng URL pattern.",
            )
            self.assertNotIn("525252", route or "")

    def test_request_is_counted(self):
        # Chưa đăng nhập → 401, nhưng middleware vẫn phải ghi nhận request.
        def orders_total():
            return sum(
                value
                for (method, route, _status), value in _samples().items()
                if method == "POST" and "orders" in (route or "")
            )

        before = orders_total()
        response = self.client.post(
            "/api/orders", {"limit": "5", "offset": "0"}, format="json"
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(orders_total() - before, 1)

    def test_unmatched_url_collapses_to_single_label(self):
        # Bot quét URL rác không được phép sinh series mới cho mỗi đường dẫn.
        self.client.get("/khong-ton-tai-1")
        self.client.get("/khong-ton-tai-2")

        routes = _routes_seen()
        self.assertIn("<unmatched>", routes)
        self.assertNotIn("/khong-ton-tai-1", routes)


class RouteLabelUnitTests(SimpleTestCase):
    """_route_label xử lý được request chưa/không resolve."""

    def test_returns_unmatched_when_no_resolver_match(self):
        class _Req:
            resolver_match = None

        self.assertEqual(_route_label(_Req()), "<unmatched>")

    def test_prefers_route_pattern_over_view_name(self):
        class _Match:
            route = "api/orders/<int:order_id>"
            view_name = "orders-detail"

        class _Req:
            resolver_match = _Match()

        self.assertEqual(_route_label(_Req()), "api/orders/<int:order_id>")

    def test_falls_back_to_view_name_for_re_path(self):
        # re_path không có thuộc tính `route` hữu ích → dùng view_name.
        class _Match:
            route = None
            view_name = "legacy-view"

        class _Req:
            resolver_match = _Match()

        self.assertEqual(_route_label(_Req()), "legacy-view")


class ClientDisconnectStatusTests(SimpleTestCase):
    """Client ngắt kết nối (CancelledError) không được đếm thành 5xx (sự cố 2026-09-23)."""

    def test_cancelled_request_counted_as_499_not_500(self):
        def cancelled(_req):
            raise asyncio.CancelledError

        before = _samples()
        with self.assertRaises(asyncio.CancelledError):
            HttpMetricsMiddleware(cancelled)(factory.get("/client-dong-tab"))
        after = _samples()

        key_499 = ("GET", "<unmatched>", "499")
        key_500 = ("GET", "<unmatched>", "500")
        self.assertEqual(after.get(key_499, 0) - before.get(key_499, 0), 1)
        self.assertEqual(after.get(key_500, 0), before.get(key_500, 0))


# ---------------------------------------------------------------------------
# GET /api/healthz — healthcheck sâu cho giám sát ngoài
# (docs/ops-hardening-2026-07.md sprint 2).
# ---------------------------------------------------------------------------

HEALTHZ = "/api/healthz"


@pytest.mark.django_db
def test_healthz_ok(client):
    response = client.get(HEALTHZ)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"db": True, "redis": True}


@pytest.mark.django_db
def test_healthz_requires_get(client):
    assert client.post(HEALTHZ).status_code == 405


@pytest.mark.django_db
def test_healthz_no_auth_required(client):
    # Probe ngoài không có JWT — endpoint phải mở (không 401/403).
    response = client.get(HEALTHZ)
    assert response.status_code == 200


@pytest.mark.django_db
def test_healthz_degraded_when_redis_fails(client):
    with mock.patch.object(
        health_mod.cache, "get", side_effect=RuntimeError("redis down")
    ):
        response = client.get(HEALTHZ)
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"] is False
    assert body["checks"]["db"] is True


@pytest.mark.django_db
def test_healthz_degraded_when_db_fails(client):
    with mock.patch.object(
        health_mod, "check_db", return_value=False
    ):
        response = client.get(HEALTHZ)
    assert response.status_code == 503
    assert response.json()["checks"]["db"] is False


def test_healthz_skipped_by_audit_middleware():
    # Probe mỗi 1-5' không được spam bảng audit_logs.
    # Cố ý couple sang apps.audit: healthz phải nằm trong SKIP_PATHS của
    # AuditLogMiddleware, đổi bên đó thì test này phải đỏ.
    from apps.audit.middleware_impl import AuditLogMiddleware

    assert any("/healthz" in p for p in AuditLogMiddleware.SKIP_PATHS)
