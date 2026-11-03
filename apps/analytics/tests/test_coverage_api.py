import pytest
from django.db import connection
from django.db.utils import OperationalError
from rest_framework.test import APIClient

from apps.analytics.application.mart_refresh_service import refresh_all_marts
from apps.analytics.tests.helpers import make_assessment, make_customer, make_order
from apps.orders.tests.helpers import make_scoped_user


@pytest.fixture
def client(db):
    user = make_scoped_user("analytics_user", "dashboard")
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.mark.django_db
def test_coverage_splits_declared_and_ai(client):
    c1 = make_customer()
    make_assessment(make_order(customer=c1), age_text="65_plus")
    c2 = make_customer()
    make_assessment(
        make_order(customer=c2), age_text="45_54", ai_filled_fields={"age_text": True}
    )
    make_customer_with_no_data = make_customer()
    make_order(customer=make_customer_with_no_data)
    refresh_all_marts()

    resp = client.get("/api/analytics/coverage")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_customers"] == 3
    age = next(f for f in body["fields"] if f["field"] == "age_text")
    assert age["declared"] == 1
    assert age["ai"] == 1
    assert age["total"] == 2
    assert age["pct"] == pytest.approx(66.7, abs=0.1)


@pytest.mark.django_db
def test_coverage_requires_authentication():
    resp = APIClient().get("/api/analytics/coverage")
    assert resp.status_code in (401, 403)


@pytest.mark.django_db(transaction=True)
def test_returns_503_when_mart_not_refreshed(client):
    """SQLSTATE 55000 (matview WITH NO DATA) → 503, không phải 500.

    Test DUY NHẤT trong file giữ `transaction=True`: lỗi 55000 là lỗi DB thật,
    làm transaction Postgres bị abort — nếu chạy trong transaction test thì mọi
    truy vấn sau đó (middleware, dọn dẹp) đều đổ "current transaction is
    aborted". Chạy autocommit như prod là cách trung thực nhất.
    """
    with connection.cursor() as cur:
        cur.execute("REFRESH MATERIALIZED VIEW dwh.customer_360 WITH NO DATA")
    try:
        resp = client.get("/api/analytics/coverage")

        assert resp.status_code == 503
        assert "refresh_dwh_marts" in resp.json()["detail"]
    finally:
        # Không để lại matview rỗng cho các test chạy sau trong cùng session
        # (transaction=True commit thật, không tự rollback).
        refresh_all_marts()


class _FakeDriverError(Exception):
    """Đứng thay `psycopg.errors.QueryCanceled` — mang `.sqlstate` như driver thật,
    KHÔNG phải 55000."""

    sqlstate = "57014"


class _FakeCursor:
    """Cursor giả — execute luôn ném OperationalError SQLSTATE khác 55000
    (vd. 57014 query_canceled), mô phỏng lỗi DB thật không phải 'chưa refresh'."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def execute(self, *args, **kwargs):
        raise OperationalError("simulated query_canceled") from _FakeDriverError("x")


class _FakeConnection:
    def cursor(self):
        return _FakeCursor()


@pytest.mark.django_db
def test_does_not_mask_other_database_errors(monkeypatch):
    """Lỗi DB không phải 'chưa refresh' (vd. query_canceled) KHÔNG được báo 503 —
    nếu bị bọc lại thành MartNotReady, operator sẽ đi chạy refresh trong khi vấn
    đề thật là cái khác."""
    import apps.analytics.infrastructure.mart_queries as mart_queries

    monkeypatch.setattr(mart_queries, "connection", _FakeConnection())

    user = make_scoped_user("analytics_user_masking", "dashboard")
    api = APIClient(raise_request_exception=False)
    api.force_authenticate(user=user)

    resp = api.get("/api/analytics/coverage")

    # KHÔNG được là 503 "chưa refresh" — đây là lỗi DB thật (query_canceled),
    # phải lộ ra như một lỗi thật (500), không bị MartNotReady nuốt mất.
    assert resp.status_code != 503
    assert resp.status_code == 500
