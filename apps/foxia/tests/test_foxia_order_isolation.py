"""Đơn mirror KHÔNG được lọt vào bất kỳ số liệu nội bộ nào.

Đây là bộ test quan trọng nhất của cả tích hợp. Quyết định để đơn Foxia nằm
chung bảng `orders` đổi bài toán "dựng kho dữ liệu mới" thành "chèn dữ liệu
ngoại lai vào bảng nóng nhất hệ thống" — và cái giá là hàng rào cô lập phải
đúng ở mọi lối ra, không chỉ ở những lối ta nhớ tới.

Cách kiểm chứng: bơm đơn Foxia vào cạnh đơn nội bộ rồi khẳng định **mọi con số
nội bộ không đổi**. Test này phải đỏ nếu ai đó gỡ manager hoặc thêm báo cáo mới
mà quên hàng rào.
"""

from decimal import Decimal

import pytest
from django.db import connection
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.common.order_scope import MIRROR_ORDER_SOURCES, sql_exclude_mirror_orders
from apps.customers.models import Customer
from apps.orders.models import Order, OrderSource, OrderStatus

pytestmark = pytest.mark.django_db


def _refresh_mart(name: str) -> None:
    """Xem `apps/analytics/tests/test_marts.py::refresh` — cùng cơ chế.

    REFRESH MATERIALIZED VIEW chạy trong transaction test thấy dữ liệu đã ghi
    của chính transaction đó và rollback theo test — không cần `transaction=True`
    (bản cũ khai riêng ở 2 test, mỗi test flush toàn bộ bảng 5–6 s).
    """
    with connection.cursor() as cur:
        cur.execute(f"REFRESH MATERIALIZED VIEW dwh.{name}")


@pytest.fixture
def channel():
    return Channel.objects.create(code="int", name="Nội bộ")


@pytest.fixture
def customer():
    return Customer.objects.create(code="C1", full_name="Khách thật", phone_normalized="0900000001")


def _order(channel, customer, *, source=None, **overrides):
    values = {
        "channel": channel,
        "customer": customer,
        "order_source": source,
        "status": OrderStatus.DELIVERED_SUCCESS,
        "total_amount": Decimal("100000"),
        "total_after_discount": Decimal("100000"),
    }
    values.update(overrides)
    order = Order(**values)
    order.save()
    return order


# ── manager ──────────────────────────────────────────────────────────────────


def test_default_manager_hides_mirror_orders(channel, customer):
    _order(channel, customer, source=OrderSource.LANDING)
    _order(channel, customer, source=OrderSource.FOXIA)

    assert Order.objects.count() == 1
    assert Order.all_objects.count() == 2


def test_default_manager_keeps_orders_with_null_source(channel, customer):
    """Bẫy chí mạng: `order_source` nullable và phần lớn đơn cũ để trống.

    Trong SQL `NULL <> 'foxia'` cho ra NULL chứ không phải TRUE — viết điều kiện
    thiếu nhánh IS NULL là mất sạch đơn cũ khỏi mọi báo cáo, mà báo cáo vẫn ra
    số nên không ai biết.
    """
    _order(channel, customer, source=None)

    assert Order.objects.count() == 1


def test_related_manager_from_customer_hides_mirror(channel, customer):
    _order(channel, customer, source=OrderSource.LANDING)
    _order(channel, customer, source=OrderSource.FOXIA)

    # Báo cáo khách hàng đi qua quan hệ này.
    assert customer.orders.count() == 1


def test_base_manager_still_loads_relation_of_mirror_order(channel, customer):
    """Base manager KHÔNG được lọc, nếu không nạp quan hệ sẽ raise DoesNotExist."""
    from apps.orders.models import OrderItem
    from apps.catalog.models import Product

    order = _order(channel, customer, source=OrderSource.FOXIA)
    product = Product.objects.create(name="SP", sku="SKU1")
    item = OrderItem.objects.create(order=order, product=product, quantity=1)

    item.refresh_from_db()
    assert item.order.pk == order.pk


def test_mirror_sources_match_enum():
    """Hằng ở `apps.common` và enum ở `apps.orders` không được trôi khỏi nhau."""
    assert set(MIRROR_ORDER_SOURCES) <= set(OrderSource.values)
    assert OrderSource.FOXIA in MIRROR_ORDER_SOURCES


def test_sql_guard_handles_null_source():
    clause = sql_exclude_mirror_orders("o")
    assert "IS NULL" in clause
    assert "'foxia'" in clause
    assert clause.count("o.") == 2


# ── báo cáo dùng SQL thô ─────────────────────────────────────────────────────


def _seed_pair(channel, customer):
    """Một đơn nội bộ + một đơn Foxia, cùng trạng thái giao thành công."""
    now = timezone.now()
    internal = _order(channel, customer, source=OrderSource.LANDING, created_at=now)
    mirror_customer = Customer.objects.create(code="FOXIA-12", full_name="[Foxia] sentinel")
    mirror = _order(channel, mirror_customer, source=OrderSource.FOXIA, created_at=now)
    return internal, mirror


def test_customer_type_reconcile_ignores_mirror_orders(channel, customer):
    """Sentinel Foxia có đơn giao thành công không được biến nó thành 'khách cũ'."""
    from apps.customers.customer_type import reconcile_customer_type

    _, mirror = _seed_pair(channel, customer)
    sentinel = mirror.customer
    Customer.objects.filter(pk=sentinel.pk).update(is_new_customer=True)

    reconcile_customer_type()

    sentinel.refresh_from_db()
    assert sentinel.is_new_customer is True


def _mart_count(sql: str, params: list) -> int:
    with connection.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


def test_customer_mart_counts_only_internal_buyers(channel, customer):
    """Sentinel Foxia không được đếm là khách mua hàng trong `dwh.customer_360`.

    Đây là bài kiểm tra ở TẦNG HẰNG SỐ, không phải tầng bộ lọc của mart.
    `customer_360` dựng từ `fct_order_enriched`, mà mart đó loại đơn mirror qua
    `gtc_where()` → `sql_exclude_mirror_orders` → `MIRROR_ORDER_SOURCES`. Nói
    cách khác test này đỏ khi hằng số nguồn sai, còn test dưới đỏ khi bộ lọc
    row-level của mart bị gỡ. Hai đầu khác nhau của cùng một bất biến — đừng
    xoá cái này vì thấy "trùng" với cái kia.
    """
    _, mirror = _seed_pair(channel, customer)
    _refresh_mart("fct_order_enriched")
    _refresh_mart("dim_customer_profile")
    _refresh_mart("customer_360")

    assert _mart_count(
        "SELECT count(*) FROM dwh.customer_360 WHERE customer_id = %s", [mirror.customer_id]
    ) == 0
    assert _mart_count(
        "SELECT count(*) FROM dwh.customer_360 WHERE customer_id = %s", [customer.pk]
    ) == 1


def test_order_mart_excludes_mirror_orders(channel, customer):
    """Đơn mirror không có mặt trong `dwh.fct_order_enriched`.

    Gộp từ hai test trước đó (`..._from_channel_totals` và
    `..._from_monthly_totals`) vốn thay cho `channel_quality.compute()` và
    `report_trends.compute()`. Khi cả hai chuyển sang đếm thẳng trên matview,
    chúng chỉ khác nhau ở mệnh đề WHERE nhưng cùng dò đúng MỘT dòng lọc trong
    migration 0003 — giữ cả hai không thêm khả năng phát hiện lỗi nào. Bản gộp
    này khẳng định mạnh hơn cả hai bản cũ: không chỉ tổng bằng 1, mà đúng id
    của đơn mirror phải vắng mặt.

    Test truy vấn thẳng matview thay vì đi qua một hàm dịch vụ: bất biến thuộc
    về mart, không thuộc về tầng đọc mart. Đây là lần thứ ba bộ test này phải
    dời chỗ theo tầng đọc; neo vào matview để không còn lần thứ tư.
    """
    internal, mirror = _seed_pair(channel, customer)
    _refresh_mart("fct_order_enriched")

    today = timezone.localdate()
    assert _mart_count(
        "SELECT count(*) FROM dwh.fct_order_enriched WHERE channel_id = %s", [channel.pk]
    ) == 1
    assert _mart_count(
        "SELECT count(*) FROM dwh.fct_order_enriched WHERE order_id = %s", [mirror.pk]
    ) == 0
    assert _mart_count(
        "SELECT count(*) FROM dwh.fct_order_enriched"
        " WHERE created_date >= %s AND created_date <= %s",
        [today.replace(day=1), today],
    ) == 1
    assert _mart_count(
        "SELECT count(*) FROM dwh.fct_order_enriched WHERE order_id = %s", [internal.pk]
    ) == 1


# ── side effect ──────────────────────────────────────────────────────────────


def test_mirror_order_does_not_enqueue_cnv_sync(
    channel, customer, settings, monkeypatch, django_capture_on_commit_callbacks
):
    """Không được đẩy đơn của đối tác sang hệ thống loyalty thật.

    Đi qua `.save()` thật (không gọi handler tay) để kiểm đúng thứ chạy ở
    production: subscriber `orders.saved` đã đăng ký sẵn từ AppConfig.ready. Event
    phát sau commit nên phải chạy callback on_commit tay trong test.
    """
    from apps.cnv_loyalty import subscribers

    settings.CNV_SYNC_ENABLED = True
    enqueued = []
    monkeypatch.setattr(subscribers, "_enqueue", lambda task, pk: enqueued.append(pk))

    with django_capture_on_commit_callbacks(execute=True):
        mirror = _order(channel, customer, source=OrderSource.FOXIA)
    assert enqueued == [], "đơn mirror không được đẩy ra hệ thống ngoài"

    with django_capture_on_commit_callbacks(execute=True):
        internal = _order(channel, customer, source=OrderSource.LANDING)
    assert enqueued == [internal.pk]
    assert mirror.pk not in enqueued
