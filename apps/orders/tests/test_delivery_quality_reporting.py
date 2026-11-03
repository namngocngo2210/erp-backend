"""Unit test trực tiếp `DeliveryQualityReportsMixin` (`orders/reporting/delivery_quality.py`).

Dashboard summary đã có 3 assert gián tiếp qua HTTP; ở đây gọi thẳng classmethod
trên `OrderRepository` để khoá từng công thức: rổ trạng thái của "% bom"
(bomb/(total − in_delivery), FE tự chia), tồn quá 7 ngày, lý do bom chỉ đếm đơn
ĐÃ hoàn (status 5), tỷ lệ hoàn theo SP dùng rổ 4/5/12/13 và đếm distinct theo
đơn, chất lượng data chỉ đếm đơn có nguồn, nhà mạng normalize ngay trong SQL.

Phạm vi kênh = kênh có NV phòng Ads gán (`sale_report_channel_ids`), staff phải
thuộc phòng Sales — hai điều kiện đó dựng ở fixture `scope`.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.catalog.models import Product
from apps.channels_core.models import Channel, UserChannel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderAssessment, OrderItem
from apps.orders.repositories import OrderRepository
from apps.orders.reporting.shared import (
    BOMB_ORDER_STATUSES,
    IN_DELIVERY_ORDER_STATUSES,
    RETURN_ORDER_STATUSES,
    SENT_OUT_ORDER_STATUSES,
    sale_report_channel_ids,
)
from apps.users.constants import DepartmentID
from apps.users.models import Department, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def scope():
    """Kênh trong phạm vi báo cáo sale + 2 NV sale + 1 kênh ngoài phạm vi."""
    Department.objects.get_or_create(id=DepartmentID.SALES, defaults={"name": "Sales"})
    Department.objects.get_or_create(id=DepartmentID.ADS, defaults={"name": "Ads"})
    channel = Channel.objects.create(code="dq-lp", name="DQ Landing")
    empty_channel = Channel.objects.create(code="dq-empty", name="DQ Empty")
    outside = Channel.objects.create(code="dq-outside", name="DQ Outside")
    ads = User.objects.create_user(username="dq-ads", password="x")
    User.objects.filter(pk=ads.pk).update(department_id=DepartmentID.ADS)
    UserChannel.objects.create(user=ads, channel=channel, is_active=True)
    UserChannel.objects.create(user=ads, channel=empty_channel, is_active=True)
    alice = User.objects.create_user(username="dq-alice", password="x", display_name="Alice")
    bob = User.objects.create_user(username="dq-bob", password="x", display_name="Bob")
    User.objects.filter(pk__in=[alice.pk, bob.pk]).update(department_id=DepartmentID.SALES)
    not_sales = User.objects.create_user(username="dq-ads-staff", password="x")
    User.objects.filter(pk=not_sales.pk).update(department_id=DepartmentID.ADS)
    customer = Customer.objects.create(full_name="Khach DQ", phone_raw="0909000000", phone_normalized="0909000000")
    return {
        "channel": channel,
        "empty_channel": empty_channel,
        "outside": outside,
        "alice": alice,
        "bob": bob,
        "not_sales": not_sales,
        "customer": customer,
        "ids": [channel.id, empty_channel.id],
    }


def _order(scope, status, *, staff=None, channel=None, phone="0909000000", days_ago=0, source="landing", **extra):
    order = Order.objects.create(
        channel=channel or scope["channel"],
        customer=scope["customer"],
        staff=staff or scope["alice"],
        name="X",
        phone=phone,
        status=status,
        order_source=source,
        **extra,
    )
    if days_ago:
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
    return order


def _by(rows, key):
    return {row[key]: row for row in rows}


# ── rổ trạng thái ────────────────────────────────────────────────────────────


def test_ro_trang_thai_nhom_5a_roi_nhau_va_phu_het_don_da_gui():
    """in_delivery + bomb + delivered phải phủ hết 0..13 trừ huỷ (6); bom = rổ hoàn."""
    all_statuses = set(range(14)) - {6}
    assert set(IN_DELIVERY_ORDER_STATUSES) | set(BOMB_ORDER_STATUSES) | {3} == all_statuses
    assert not set(IN_DELIVERY_ORDER_STATUSES) & set(BOMB_ORDER_STATUSES)
    assert set(BOMB_ORDER_STATUSES) == set(RETURN_ORDER_STATUSES)
    assert set(RETURN_ORDER_STATUSES) <= set(SENT_OUT_ORDER_STATUSES)


def test_delivery_quality_by_staff_phan_loai_dung_tung_ro(scope):
    bob = scope["bob"]
    for status in (2, 3, 4, 5, 7, 12, 13, 0):
        _order(scope, status, staff=bob)
    _order(scope, 2, staff=bob, days_ago=10)  # tồn > 7 ngày
    _order(scope, 11, staff=bob, days_ago=8)  # tồn > 7 ngày
    _order(scope, 2, staff=bob, days_ago=3)  # chưa quá ngưỡng
    _order(scope, 3, staff=bob, days_ago=30)  # đã giao, dù cũ vẫn không phải tồn
    _order(scope, 6, staff=bob)  # huỷ → loại khỏi báo cáo
    _order(scope, 3, staff=scope["alice"])

    rows = _by(OrderRepository.summary_delivery_quality_by_staff(sale_channel_ids=scope["ids"]), "staff__username")

    assert list(rows) == ["dq-alice", "dq-bob"]  # order_by username
    bob_row = rows["dq-bob"]
    assert bob_row["staff__display_name"] == "Bob"
    assert bob_row["total"] == 12
    assert bob_row["sent"] == 11  # tất cả trừ status 0
    assert bob_row["delivered"] == 2
    assert bob_row["bomb"] == 4  # 4, 5, 12, 13
    assert bob_row["returned"] == 4  # cùng rổ với bomb (cố ý, xem comment trong code)
    assert bob_row["in_delivery"] == 6  # 2, 7, 0, 2(10d), 11(8d), 2(3d)
    assert bob_row["pending_over_threshold"] == 2
    assert bob_row["in_delivery"] + bob_row["bomb"] + bob_row["delivered"] == bob_row["total"]
    # "% bom" FE tính = bomb / (total − in_delivery) → mẫu số là đơn đã có kết quả.
    assert bob_row["total"] - bob_row["in_delivery"] == bob_row["bomb"] + bob_row["delivered"]
    assert rows["dq-alice"]["total"] == 1


def test_pending_over_threshold_dung_nguong_7_ngay_theo_created_at(scope):
    _order(scope, 2, days_ago=6)
    just_over = _order(scope, 2)
    Order.objects.filter(pk=just_over.pk).update(created_at=timezone.now() - timedelta(days=7, minutes=1))

    row = OrderRepository.summary_delivery_quality_by_staff(sale_channel_ids=scope["ids"])[0]

    assert row["pending_over_threshold"] == 1


# ── phạm vi ──────────────────────────────────────────────────────────────────


def test_pham_vi_kenh_staff_va_khoang_ngay(scope):
    _order(scope, 3)  # trong phạm vi
    _order(scope, 3, channel=scope["outside"])  # kênh không có NV Ads gán
    _order(scope, 3, staff=scope["not_sales"])  # staff không thuộc phòng Sales
    _order(scope, 3, days_ago=40)  # ngoài khoảng ngày

    today = timezone.localdate()
    date_from = (today - timedelta(days=10)).isoformat()
    in_range = OrderRepository.summary_delivery_quality_by_staff(
        date_from, today.isoformat(), sale_channel_ids=scope["ids"]
    )
    default_scope = OrderRepository.summary_delivery_quality_by_staff(date_from, today.isoformat())
    wide = OrderRepository.summary_delivery_quality_by_staff(sale_channel_ids=scope["ids"])

    assert [(r["staff__username"], r["total"]) for r in in_range] == [("dq-alice", 1)]
    # sale_channel_ids=None → tự tính theo NV Ads gán: cùng kết quả.
    assert sorted(sale_report_channel_ids()) == sorted(scope["ids"])
    assert in_range == default_scope
    assert wide[0]["total"] == 2
    # Đóng khoảng theo ngày local: date_to là ngày hôm nay vẫn lấy đơn tạo hôm nay.
    assert OrderRepository.summary_delivery_quality_by_staff(
        today.isoformat(), today.isoformat(), sale_channel_ids=scope["ids"]
    )[0]["total"] == 1


def test_delivery_quality_by_channel_them_dong_0_cho_kenh_trong_scope_khong_co_don(scope):
    _order(scope, 3)
    _order(scope, 5)

    rows = OrderRepository.summary_delivery_quality_by_channel(sale_channel_ids=scope["ids"])

    assert [(r["channel__code"], r["total"]) for r in rows] == [("dq-lp", 2), ("dq-empty", 0)]
    empty = rows[1]
    assert empty["channel_id"] == scope["empty_channel"].id
    assert empty["channel__name"] == "DQ Empty"
    for key in ("sent", "returned", "delivered", "in_delivery", "bomb", "pending_over_threshold"):
        assert empty[key] == 0
    assert (rows[0]["delivered"], rows[0]["bomb"], rows[0]["sent"]) == (1, 1, 2)


def test_by_channel_kenh_ngoai_scope_khong_duoc_them_dong_0(scope):
    rows = OrderRepository.summary_delivery_quality_by_channel(sale_channel_ids=[scope["channel"].id])

    assert [r["channel__code"] for r in rows] == ["dq-lp"]


# ── lý do bom ────────────────────────────────────────────────────────────────


def test_bomb_reasons_chi_dem_don_da_hoan_va_gan_nhan(scope):
    for code in ("changed_mind_not_buying", "changed_mind_not_buying", "delivery_too_slow", "ma-la", None):
        order = _order(scope, 5)
        OrderAssessment.objects.create(order=order, bomb_reason_type_code=code)
    # Đang hoàn (4) và giao TC (3) không vào lý do bom dù có mã.
    for status in (4, 3):
        OrderAssessment.objects.create(order=_order(scope, status), bomb_reason_type_code="delivery_too_slow")
    _order(scope, 5)  # hoàn nhưng không có assessment → nhóm None

    rows = OrderRepository.summary_bomb_reasons(sale_channel_ids=scope["ids"])

    assert [r["count"] for r in rows] == sorted((r["count"] for r in rows), reverse=True)
    by_code = _by(rows, "assessment__bomb_reason_type_code")
    assert by_code["changed_mind_not_buying"] == {
        "assessment__bomb_reason_type_code": "changed_mind_not_buying",
        "count": 2,
        "label": "Khách đổi ý/không còn nhu cầu",
    }
    assert by_code["delivery_too_slow"]["count"] == 1
    assert by_code["ma-la"]["label"] == "ma-la"  # mã ngoài catalog: giữ nguyên mã
    assert by_code[None] == {"assessment__bomb_reason_type_code": None, "count": 2, "label": "Chưa phân loại"}
    assert sum(r["count"] for r in rows) == 6


# ── tỷ lệ hoàn theo SP ───────────────────────────────────────────────────────


def test_return_rate_by_product_dem_distinct_theo_don_va_ro_hoan_4_5_12_13(scope):
    product_a = Product.objects.create(name="Ghế A", sku="DQ-A")
    product_b = Product.objects.create(name="Ghế B", sku="DQ-B")

    def make(status, *products):
        order = _order(scope, status)
        for product in products:
            OrderItem.objects.create(order=order, product=product)
        return order

    make(5, product_a)
    make(3, product_a)
    make(0, product_a)  # chưa gửi: total tăng, sent không
    make(4, product_a)
    make(12, product_a, product_b)  # đơn 2 SP xuất hiện ở cả 2, mỗi SP đếm 1 đơn
    make(13, product_b)
    make(3, product_b)
    make(5)  # không có item → loại khỏi báo cáo
    make(6, product_b)  # huỷ → loại

    rows = OrderRepository.summary_return_rate_by_product(sale_channel_ids=scope["ids"])

    assert [r["items__product__sku"] for r in rows] == ["DQ-A", "DQ-B"]  # -returned rồi tên
    by_sku = _by(rows, "items__product__sku")
    assert (by_sku["DQ-A"]["total"], by_sku["DQ-A"]["sent"], by_sku["DQ-A"]["returned"]) == (5, 4, 3)
    assert (by_sku["DQ-B"]["total"], by_sku["DQ-B"]["sent"], by_sku["DQ-B"]["returned"]) == (3, 3, 2)
    assert by_sku["DQ-A"]["items__product__name"] == "Ghế A"
    assert by_sku["DQ-A"]["items__product"] == product_a.id
    # Bất biến: returned ≤ sent ≤ total (tỷ lệ không vượt 100 %).
    for row in rows:
        assert row["returned"] <= row["sent"] <= row["total"]


def test_return_rate_by_product_rong_khi_khong_co_item(scope):
    _order(scope, 5)
    assert OrderRepository.summary_return_rate_by_product(sale_channel_ids=scope["ids"]) == []


# ── chất lượng data theo kênh ────────────────────────────────────────────────


def test_data_quality_by_channel_chi_dem_don_co_nguon_va_phan_loai_unreached_dirty(scope):
    def with_code(code, **kwargs):
        order = _order(scope, 0, **kwargs)
        if code:
            OrderAssessment.objects.create(order=order, sales_status_code=code)
        return order

    with_code("no_answer_6")
    with_code("unreachable")
    with_code("spam")
    with_code("wrong_number")
    with_code("closed")
    with_code(None)
    with_code("spam", source="")  # không có nguồn → không tính
    with_code("spam", source=None)
    with_code("spam", source="messenger")  # nguồn khác landing vẫn tính

    rows = OrderRepository.summary_data_quality_by_channel(sale_channel_ids=scope["ids"])

    assert [(r["channel__code"], r["total"], r["unreached"], r["dirty"]) for r in rows] == [
        ("dq-lp", 7, 2, 3),
        ("dq-empty", 0, 0, 0),
    ]


def test_data_quality_by_channel_loc_theo_ngay(scope):
    _order(scope, 0, days_ago=20)
    _order(scope, 0)
    today = timezone.localdate()

    rows = OrderRepository.summary_data_quality_by_channel(
        (today - timedelta(days=5)).isoformat(), today.isoformat(), sale_channel_ids=[scope["channel"].id]
    )

    assert [(r["channel__code"], r["total"]) for r in rows] == [("dq-lp", 1)]


# ── nhà mạng ─────────────────────────────────────────────────────────────────


def test_data_by_carrier_normalize_sdt_trong_sql_va_map_nha_mang(scope):
    phones = [
        "0961234567",  # viettel
        "+84 96 123 4568",  # 84 → 0, bỏ ký tự lạ → viettel
        "84961234569",  # viettel
        "961234570",  # mất số 0 đầu (Excel) → viettel
        "0912345678",  # vinaphone
        "090-123-4567",  # mobifone
        "0521234567",  # vietnamobile
        "0871234567",  # itel
        "0123456789",  # đầu số không có → unknown
        "",  # rỗng → unknown
    ]
    for phone in phones:
        _order(scope, 0, phone=phone)
    _order(scope, 0, phone="0961234567", channel=scope["outside"])  # ngoài scope
    _order(scope, 6, phone="0961234567")  # huỷ

    rows = OrderRepository.summary_data_by_carrier(sale_channel_ids=scope["ids"])

    totals = {r["carrier"]: r["total"] for r in rows}
    assert totals == {
        "viettel": 4,
        "vinaphone": 1,
        "mobifone": 1,
        "vietnamobile": 1,
        "itel": 1,
        "gmobile": 0,
        "wintel": 0,
        "unknown": 2,
    }
    assert sum(totals.values()) == len(phones)
    # Sắp xếp: nhiều nhất trước, hoà thì theo nhãn; nhãn người đọc kèm theo.
    assert [r["carrier"] for r in rows][:2] == ["viettel", "unknown"]
    assert rows[0]["label"] == "Viettel"
    assert {r["carrier"]: r["label"] for r in rows}["unknown"] == "Không xác định"
    labels_of_ones = [r["label"] for r in rows if r["total"] == 1]
    assert labels_of_ones == sorted(labels_of_ones)


def test_data_by_carrier_khong_co_don_tra_du_nha_mang_bang_0(scope):
    rows = OrderRepository.summary_data_by_carrier(sale_channel_ids=scope["ids"])

    assert len(rows) == 8
    assert all(r["total"] == 0 for r in rows)
    assert [r["label"] for r in rows] == sorted(r["label"] for r in rows)
