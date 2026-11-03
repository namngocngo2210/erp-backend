"""Tests cho GET /api/audit-logs/ — phân trang limit/offset chuẩn repo.

Thay hardcode `[:100]` + response dạng array bằng {items,total,limit,offset}
(default limit 100, max 500) qua `apps.common.pagination.parse_required_pagination`.
Kèm kích hoạt lookup `timestamp__gte`/`timestamp__lte` (trước đây FE gửi nhưng
filterset dạng list chỉ sinh exact nên bị bỏ qua lặng lẽ).
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.users.tests.helpers import make_admin, make_user_with_sections

pytestmark = pytest.mark.django_db

URL = "/api/audit-logs/"


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _make_log(
    user=None,
    days_old: int = 0,
    action_type: str = "API_REQUEST",
    entity_id: str | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    log = AuditLog.objects.create(
        user=user,
        action_type=action_type,
        entity_type="OTHER",
        entity_id=entity_id,
        method="GET",
        ip_address=ip_address,
        source="api_request",
    )
    if days_old:
        # timestamp là auto_now_add → chỉnh lùi qua queryset update.
        AuditLog.objects.filter(pk=log.pk).update(
            timestamp=timezone.now() - timedelta(days=days_old)
        )
    return log


class TestAuditLogsPagination:
    def test_response_shape_and_defaults(self):
        admin = make_admin("audit-page-admin-shape")
        for _ in range(3):
            _make_log(user=admin)

        response = _client(admin).get(URL)

        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) == {"items", "total", "limit", "offset"}
        assert body["total"] == 3
        assert body["limit"] == 100
        assert body["offset"] == 0
        assert len(body["items"]) == 3

    def test_limit_offset_slices_newest_first(self):
        admin = make_admin("audit-page-admin-slice")
        logs = [_make_log(user=admin, days_old=days) for days in (3, 2, 1)]

        response = _client(admin).get(URL, {"limit": 2, "offset": 1})

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        assert body["limit"] == 2
        assert body["offset"] == 1
        # ordering -timestamp: mới nhất trước → offset 1 bỏ log 1-ngày,
        # trả log 2-ngày rồi 3-ngày.
        assert [item["id"] for item in body["items"]] == [logs[1].id, logs[0].id]

    def test_limit_above_max_rejected(self):
        admin = make_admin("audit-page-admin-max")
        response = _client(admin).get(URL, {"limit": 501})
        assert response.status_code == 400

    def test_non_integer_pagination_rejected(self):
        admin = make_admin("audit-page-admin-nan")
        response = _client(admin).get(URL, {"limit": "abc"})
        assert response.status_code == 400

    def test_total_scoped_to_own_logs_for_non_admin(self):
        staff = make_user_with_sections("audit-page-staff", ["audit"])
        other = make_admin("audit-page-other")
        mine = _make_log(user=staff)
        _make_log(user=other)

        response = _client(staff).get(URL)

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert [item["id"] for item in body["items"]] == [mine.id]

    def test_timestamp_range_filters_apply(self):
        admin = make_admin("audit-page-admin-ts")
        old = _make_log(user=admin, days_old=10)
        recent = _make_log(user=admin)

        since = (timezone.now() - timedelta(days=5)).isoformat()
        response = _client(admin).get(URL, {"timestamp__gte": since})

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert [item["id"] for item in body["items"]] == [recent.id]

        until = (timezone.now() - timedelta(days=5)).isoformat()
        response = _client(admin).get(URL, {"timestamp__lte": until})
        body = response.json()
        assert body["total"] == 1
        assert [item["id"] for item in body["items"]] == [old.id]

    def test_exact_filters_still_apply_with_pagination(self):
        admin = make_admin("audit-page-admin-filter")
        login = _make_log(user=admin, action_type="LOGIN")
        _make_log(user=admin, action_type="API_REQUEST")

        response = _client(admin).get(URL, {"action_type": "LOGIN", "limit": 50})

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert [item["id"] for item in body["items"]] == [login.id]


class TestAuditLogsSearch:
    """`search` là bounded scan: bắt buộc kèm timestamp__gte, cửa sổ tối đa 31 ngày.

    Bảng audit_logs prod 4.1GB/2.19M dòng — ILIKE không chặn cửa sổ từng timeout,
    nên thiếu khoảng ngày là 400 chứ không lặng lẽ quét cả bảng.
    """

    @staticmethod
    def _since(days: int) -> str:
        return (timezone.now() - timedelta(days=days)).isoformat()

    def test_search_without_date_range_rejected(self):
        admin = make_admin("audit-search-admin-nodate")
        _make_log(user=admin, entity_id="ORD-123")

        response = _client(admin).get(URL, {"search": "ORD-123"})

        assert response.status_code == 400
        assert "timestamp__gte" in response.json()["detail"]

    def test_search_window_over_31_days_rejected(self):
        admin = make_admin("audit-search-admin-wide")

        # 40 ngày tính đến hiện tại (không có lte) → quá trần.
        response = _client(admin).get(URL, {"search": "x", "timestamp__gte": self._since(40)})
        assert response.status_code == 400
        assert "31" in response.json()["detail"]

        # gte cũ nhưng cửa sổ [gte, lte] chỉ 5 ngày → hợp lệ.
        response = _client(admin).get(
            URL,
            {
                "search": "x",
                "timestamp__gte": self._since(40),
                "timestamp__lte": self._since(35),
            },
        )
        assert response.status_code == 200

    def test_search_invalid_timestamp_rejected(self):
        admin = make_admin("audit-search-admin-badts")
        response = _client(admin).get(URL, {"search": "x", "timestamp__gte": "hom-qua"})
        assert response.status_code == 400

    def test_search_matches_entity_id_case_insensitive(self):
        admin = make_admin("audit-search-admin-entity")
        hit = _make_log(user=admin, entity_id="ORD-2026-001")
        _make_log(user=admin, entity_id="INV-999")

        response = _client(admin).get(URL, {"search": "ord-2026", "timestamp__gte": self._since(1)})

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert [item["id"] for item in body["items"]] == [hit.id]

    def test_search_matches_username(self):
        alice = make_admin("audit-search-alice")
        bob = make_admin("audit-search-bob")
        hit = _make_log(user=alice)
        _make_log(user=bob)

        response = _client(alice).get(URL, {"search": "alice", "timestamp__gte": self._since(1)})

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert [item["id"] for item in body["items"]] == [hit.id]

    def test_search_matches_ip_address(self):
        admin = make_admin("audit-search-admin-ip")
        hit = _make_log(user=admin, ip_address="192.168.1.77")
        _make_log(user=admin, ip_address="10.0.0.5")

        response = _client(admin).get(URL, {"search": "192.168", "timestamp__gte": self._since(1)})

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert [item["id"] for item in body["items"]] == [hit.id]

    def test_search_respects_timestamp_window(self):
        """Log khớp chuỗi nhưng NGOÀI cửa sổ ngày phải bị loại — search áp trên
        queryset đã lọc timestamp, không phải toàn bảng."""
        admin = make_admin("audit-search-admin-window")
        _make_log(user=admin, entity_id="ORD-OLD", days_old=10)
        recent = _make_log(user=admin, entity_id="ORD-NEW")

        response = _client(admin).get(URL, {"search": "ORD-", "timestamp__gte": self._since(5)})

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert [item["id"] for item in body["items"]] == [recent.id]

    def test_search_with_pagination(self):
        admin = make_admin("audit-search-admin-page")
        logs = [
            _make_log(user=admin, entity_id=f"ORD-P{i}", days_old=days)
            for i, days in enumerate((3, 2, 1))
        ]
        _make_log(user=admin, entity_id="OTHER-1")

        response = _client(admin).get(
            URL,
            {"search": "ORD-P", "timestamp__gte": self._since(7), "limit": 2, "offset": 1},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        assert body["limit"] == 2
        assert body["offset"] == 1
        # -timestamp: mới nhất trước → offset 1 bỏ log 1-ngày, trả 2-ngày rồi 3-ngày.
        assert [item["id"] for item in body["items"]] == [logs[1].id, logs[0].id]

    def test_search_scoped_to_own_logs_for_non_admin(self):
        staff = make_user_with_sections("audit-search-staff", ["audit"])
        other = make_admin("audit-search-other")
        mine = _make_log(user=staff, entity_id="ORD-MINE")
        _make_log(user=other, entity_id="ORD-THEIRS")

        response = _client(staff).get(URL, {"search": "ORD-", "timestamp__gte": self._since(1)})

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert [item["id"] for item in body["items"]] == [mine.id]
