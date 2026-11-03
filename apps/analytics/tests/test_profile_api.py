"""Màn Hồ sơ 360: ô tìm khách + hồ sơ một khách.

Bộ test này thay `test_explorer_api.py` (hai tab bảng đã gỡ). Trọng tâm khác
hẳn bản cũ: bản cũ kiểm phân trang/sắp xếp/lọc, bản này kiểm *một hồ sơ nói
đúng sự thật về một người* — đặc biệt là các ca dễ nói dối: khách chỉ có đơn
huỷ, khách không có chân dung khai báo, và khách của đối tác mirror.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from apps.analytics.tests.helpers import make_channel, make_customer, make_order
from apps.customers.models import CustomerSegmentCategory, CustomerSegmentSnapshot
from apps.orders.models import OrderSource
from apps.orders.tests.helpers import make_scoped_user
from apps.users.rbac_catalog import sync_permissions


def refresh_all() -> None:
    """REFRESH thường chạy trong transaction test, rollback theo test — không
    cần `transaction=True` (xem test_marts.py)."""
    with connection.cursor() as cur:
        for name in ("dim_customer_profile", "fct_order_enriched", "customer_360"):
            cur.execute(f"REFRESH MATERIALIZED VIEW dwh.{name}")


@pytest.fixture
def client(db):
    # Test `transaction=True` ở app khác chạy trước có thể đã flush cả bảng
    # catalog quyền seed bởi migration — seed lại (idempotent) cho chắc.
    sync_permissions()
    api = APIClient()
    api.force_authenticate(user=make_scoped_user("profile_user", "customer-explorer"))
    return api


def _outsider() -> APIClient:
    """Đã xác thực nhưng KHÔNG có section — chứng minh require_section thật sự chặn."""
    sync_permissions()
    api = APIClient()
    api.force_authenticate(user=make_scoped_user("outsider", "orders"))
    return api


@pytest.mark.django_db
class TestCustomerSearch:
    def test_requires_auth(self):
        assert APIClient().get("/api/analytics/customers?q=abc").status_code in (401, 403)

    def test_user_without_section_is_forbidden(self):
        assert _outsider().get("/api/analytics/customers?q=abc").status_code == 403

    def test_short_query_returns_empty_not_error(self, client):
        """Người dùng gõ dở 1 ký tự là trạng thái bình thường, không phải lỗi."""
        resp = client.get("/api/analytics/customers?q=a")
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_finds_customer_by_name_and_code(self, client):
        cus = make_customer(full_name="Nguyen Van Kho", code="KH-9001")
        make_order(customer=cus)
        refresh_all()

        by_name = client.get("/api/analytics/customers?q=Van Kho").json()["results"]
        by_code = client.get("/api/analytics/customers?q=KH-9001").json()["results"]

        assert [r["customer_id"] for r in by_name] == [cus.pk]
        assert [r["customer_id"] for r in by_code] == [cus.pk]
        assert by_name[0]["orders_gtc"] == 1

    def test_customer_without_orders_is_not_offered(self, client):
        """Mart chỉ chứa khách đã từng có đơn — hồ sơ 360 không có gì để nói
        về khách chưa mua, nên họ không được hiện trong ô chọn."""
        make_customer(full_name="Chua Mua Gi", code="KH-9002")
        refresh_all()

        assert client.get("/api/analytics/customers?q=Chua Mua").json()["results"] == []


@pytest.mark.django_db
class TestCustomerProfile:
    def test_user_without_section_is_forbidden(self):
        assert _outsider().get("/api/analytics/customers/1").status_code == 403

    def test_unknown_customer_returns_404(self, client):
        refresh_all()
        assert client.get("/api/analytics/customers/999999").status_code == 404

    def test_profile_reports_spend_and_timeline(self, client):
        cus = make_customer(full_name="Khach Mua Nhieu")
        channel = make_channel(name="Kênh A")
        make_order(customer=cus, channel=channel, total_after_discount=Decimal("500000"))
        make_order(customer=cus, channel=channel, total_after_discount=Decimal("300000"))
        refresh_all()

        body = client.get(f"/api/analytics/customers/{cus.pk}").json()

        assert body["profile"]["full_name"] == "Khach Mua Nhieu"
        assert body["profile"]["orders_gtc"] == 2
        assert Decimal(body["profile"]["total_spent"]) == Decimal("800000")
        assert len(body["timeline"]) == 2
        assert body["timeline_truncated"] is False

    def test_cancelled_only_customer_still_has_a_profile(self, client):
        """Ca dễ nói dối nhất: khách huỷ sạch đơn.

        Hồ sơ PHẢI tồn tại và PHẢI hiện đơn huỷ trong dòng thời gian. Nếu lọc
        chúng đi, một khách huỷ liên tiếp trông y hệt khách chưa từng đặt gì —
        đúng lúc sale cần thấy điều ngược lại.
        """
        cus = make_customer(full_name="Khach Huy Het")
        make_order(customer=cus, status=6)
        refresh_all()

        body = client.get(f"/api/analytics/customers/{cus.pk}").json()

        assert body["profile"]["orders_gtc"] == 0
        assert body["profile"]["orders_cancelled"] == 1
        assert Decimal(body["profile"]["total_spent"]) == Decimal("0")
        assert [row["is_gtc"] for row in body["timeline"]] == [False]

    def test_profile_without_declared_portrait_returns_empty_not_missing(self, client):
        """98% khách không có chân dung khai báo — các trường đó phải là rỗng
        hợp lệ để tầng hiển thị ẩn khối đi, chứ không được vắng mặt khỏi payload."""
        cus = make_customer(full_name="Khach Khong Khai Bao")
        make_order(customer=cus)
        refresh_all()

        profile = client.get(f"/api/analytics/customers/{cus.pk}").json()["profile"]

        assert profile["age_text"] is None
        assert profile["need_codes"] == []
        assert profile["filled_field_count"] == 0

    def test_mirror_customer_has_no_profile(self, client):
        """Khách của đối tác mirror không được có hồ sơ — cùng bất biến với
        `apps/foxia/tests/test_foxia_order_isolation.py`, kiểm ở lối ra này."""
        cus = make_customer(full_name="[Foxia] sentinel")
        make_order(customer=cus, order_source=OrderSource.FOXIA)
        refresh_all()

        assert client.get(f"/api/analytics/customers/{cus.pk}").status_code == 404

    def test_profile_of_customer_with_segment_returns_200(self, client):
        """Ca đã gây 500 trên prod ngày 23/08/2026.

        `latest_sales_status_code` và `care_priority` là CHUỖI, nhưng serializer
        khai `IntegerField`. Mọi test trước đó xanh vì factory không seed phân
        khúc, nên hai trường luôn NULL và không lần nào phải chuyển đổi giá trị
        thật — trong khi trên prod 423k khách mang `latest_sales_status_code =
        'created_order'`, tức gần như toàn bộ tệp.

        Giá trị dùng ở đây lấy đúng từ prod, không phải chuỗi tự nghĩ ra.
        """
        cus = make_customer(full_name="Khach Co Phan Khuc")
        make_order(customer=cus)
        # Migration seed sẵn S1 (bản cũ không thấy vì test transaction=True đã
        # flush) — ghi đè để test tự chủ giá trị, không dựa vào seed.
        CustomerSegmentCategory.objects.update_or_create(
            code="S1", defaults={"name": "Khách trung thành", "care_priority": "Chốt đơn"}
        )
        CustomerSegmentSnapshot.objects.create(
            customer=cus,
            segment_code="S1",
            latest_sales_status_code="created_order",
            has_problem=False,
        )
        refresh_all()

        resp = client.get(f"/api/analytics/customers/{cus.pk}")

        assert resp.status_code == 200, resp.content
        profile = resp.json()["profile"]
        assert profile["latest_sales_status_code"] == "created_order"
        assert profile["care_priority"] == "Chốt đơn"
        assert profile["segment_name"] == "Khách trung thành"

    def test_timeline_is_newest_first(self, client):
        cus = make_customer(full_name="Khach Thu Tu")
        now = timezone.now()
        old = make_order(customer=cus, created_at=now - timedelta(days=10))
        new = make_order(customer=cus, created_at=now)
        refresh_all()

        timeline = client.get(f"/api/analytics/customers/{cus.pk}").json()["timeline"]

        assert [row["order_id"] for row in timeline] == [new.pk, old.pk]
