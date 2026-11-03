"""Helper dùng chung cho test API Daily Report."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from rest_framework.test import APIClient

from apps.ai_mart.application import etl_service
from apps.ai_mart.tests.helpers import mk_order
from apps.analytics.sql.tz import BUSINESS_TZ
from apps.orders.models import Order, OrderAssessment

TOKEN = "daily-report-test-token"
TZ = ZoneInfo(BUSINESS_TZ)


def client() -> APIClient:
    c = APIClient()
    c.credentials(HTTP_X_API_TOKEN=TOKEN)
    return c


def seed_order(day: date, *, sales_status: str | None = None, amount: int = 100_000, **kw) -> Order:
    """Đơn nội bộ tạo lúc 10:00 giờ VN của ``day``. ``kw`` đi thẳng vào mk_order
    (status, invoice_status, staff, branch_id, order_source, invoice_code, channel...)."""
    order = mk_order(total_after_discount=amount, **kw)
    Order.objects.filter(id=order.id).update(
        created_at=datetime.combine(day, time(10, 0), tzinfo=TZ)
    )
    if sales_status is not None:
        OrderAssessment.objects.update_or_create(
            order=order, defaults={"sales_status_code": sales_status}
        )
    return order


def rebuild() -> None:
    """Nạp lại toàn bộ mart + ghi watermark/nhật ký → freshness 'Bình thường'."""
    etl_service.rebuild_all()


def fetch_all(url: str, params: dict, *, limit: int = 3) -> list[dict]:
    """Kéo hết trang với limit nhỏ để mọi test đều đi qua đường keyset."""
    items, cursor = [], None
    while True:
        q = {**params, "limit": str(limit)}
        if cursor is not None:
            q["cursor"] = str(cursor)
        resp = client().get(url, q)
        assert resp.status_code == 200, resp.content
        body = resp.json()
        items += body["data"]
        cursor = body["next_cursor"]
        if cursor is None:
            return items
