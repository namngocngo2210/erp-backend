"""Luật thuần đẩy đơn (two-way-sync §4.1, §6b, A2): đủ điều kiện, chống trùng landing ↔
Foxia, chọn kho, tách kiện, chia tiền, payload, quyết định sửa/huỷ."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.pancake_pos.domain import order_push as rules
from apps.pancake_pos.domain.order_push import (
    ChangeAction,
    CrmOrder,
    OrderLine,
    PartMoney,
    PartPlan,
    RoutingError,
    Verdict,
    WarehouseOption,
)

NOW = datetime(2026, 9, 30, 10, tzinfo=UTC)
SOURCES = rules.DEFAULT_PUSH_SOURCES


def _line(pid: int = 1, qty: int = 1, price: int = 100_000, discount: int = 0, variation: str = "v1") -> OrderLine:
    return OrderLine(product_id=pid, sku=f"SKU{pid}", name=f"SP{pid}", quantity=qty,
                     unit_price=Decimal(price), discount=Decimal(discount), variation_id=variation)


def _order(**kw) -> CrmOrder:
    base: dict = {
        "id": 10, "order_source": "landing", "status": 0, "sales_status_code": "closed", "closed_at": NOW,
        "name": "Khách A", "phone": "0912345678", "address": "1 Trần Phú, Hà Đông, Hà Nội",
        "total_after_discount": Decimal(100_000), "lines": (_line(),),
    }
    return CrmOrder(**{**base, **kw})


def _eval(order: CrmOrder, **kw):
    return rules.evaluate(order, sources=kw.pop("sources", SOURCES), **kw)


# ── đủ điều kiện ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("code", ["closed", "closed_express", "created_order"])
def test_don_chot_du_dieu_kien(code):
    assert _eval(_order(sales_status_code=code)).verdict == Verdict.ELIGIBLE


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"is_deleted": True}, "deleted"),
        ({"order_source": "shopee"}, "source_not_enabled"),
        ({"order_source": "koc"}, "source_not_enabled"),
        ({"status": 6}, "cancelled"),
        ({"sales_status_code": "cancelled"}, "cancelled"),
        ({"status": 2}, "status_not_pending"),
        ({"status": 3}, "status_not_pending"),
        ({"sales_status_code": "closed_scheduled_date"}, "not_closed"),
        ({"sales_status_code": "need_more_consulting"}, "not_closed"),
        ({"sales_status_code": ""}, "not_closed"),
    ],
)
def test_bo_qua(kw, reason):
    result = _eval(_order(**kw))
    assert (result.verdict, result.reason) == (Verdict.IGNORE, reason)


def test_nguon_cau_hinh_duoc():
    assert _eval(_order(order_source="zalo"), sources=("landing",)).reason == "source_not_enabled"
    assert _eval(_order(order_source="Landing"), sources=(" LANDING ",)).verdict == Verdict.ELIGIBLE


def test_cua_so_quet():
    since = NOW - timedelta(hours=72)
    assert _eval(_order(closed_at=NOW - timedelta(hours=73)), closed_since=since).reason == "too_old"
    assert _eval(_order(closed_at=None), closed_since=since).reason == "too_old"
    assert _eval(_order(closed_at=NOW - timedelta(hours=1)), closed_since=since).verdict == Verdict.ELIGIBLE


def test_moc_bat_dau_day_don_theo_moc_chot_dau():
    """`PANCAKE_POS_PUSH_ORDERS_SINCE`: so với mốc CHỐT (`first_closed_at`), không phải lần sửa
    đánh giá gần nhất — đơn chốt trước mốc mà sau đó sửa đánh giá vẫn không đẩy."""
    since = NOW - timedelta(hours=1)
    old = _order(first_closed_at=NOW - timedelta(hours=2), closed_at=NOW)
    assert _eval(old, pushed_after=since).reason == "before_push_since"
    new = _order(first_closed_at=NOW - timedelta(minutes=5), closed_at=NOW)
    assert _eval(new, pushed_after=since).verdict == Verdict.ELIGIBLE
    # Không có first_closed_at (đơn Foxia/test cũ) → dùng closed_at.
    assert _eval(_order(closed_at=NOW - timedelta(hours=2)), pushed_after=since).reason == "before_push_since"
    assert _eval(_order(closed_at=NOW), pushed_after=None).verdict == Verdict.ELIGIBLE


def test_trang_thai_cho_xu_ly_va_cho_giao_duoc_day():
    for status in (0, 1, 11):
        assert _eval(_order(status=status)).verdict == Verdict.ELIGIBLE


def test_don_foxia_khong_can_danh_gia_sale():
    assert _eval(_order(order_source="foxia", sales_status_code="")).verdict == Verdict.ELIGIBLE


def test_thieu_dong_hang_hoac_chua_ghep_sku_la_blocked():
    assert _eval(_order(lines=())).reason == "no_items"
    result = _eval(_order(lines=(_line(1), _line(2, variation=""))))
    assert (result.verdict, result.reason) == (Verdict.BLOCKED, "missing_variation")
    assert "SKU2" in result.detail and "SKU1" not in result.detail


# ── chống trùng landing ↔ Foxia ─────────────────────────────────────────────
def test_landing_co_don_foxia_thi_bo_qua_va_canh_bao():
    result = _eval(_order(foxia_order_ids=(99,)))
    assert (result.verdict, result.reason, result.alert) == (Verdict.DUPLICATE, "foxia_order_exists", True)
    assert "#99" in result.detail


def test_landing_chi_co_co_hoi_foxia_van_day():
    """Số đo prod: mọi đơn landing đều có cơ hội Foxia — cơ hội KHÔNG phải đơn thật."""
    assert _eval(_order(foxia_order_ids=())).verdict == Verdict.ELIGIBLE


def test_don_foxia_ma_don_goc_da_day_thi_bo_qua():
    order = _order(id=99, order_source="foxia", sales_status_code="", origin_order_ids=(10,))
    result = _eval(order, pushed_order_ids={10})
    assert (result.verdict, result.reason, result.alert) == (Verdict.DUPLICATE, "origin_pushed", True)
    # Đơn gốc chưa đẩy → đơn Foxia là đơn thật, được đẩy.
    assert _eval(order, pushed_order_ids=set()).verdict == Verdict.ELIGIBLE


def test_trung_xet_truoc_thieu_du_lieu():
    """Đơn trùng mà còn thiếu SKU vẫn là `duplicate` — không để người vận hành sửa SKU rồi đẩy trùng."""
    assert _eval(_order(foxia_order_ids=(99,), lines=())).verdict == Verdict.DUPLICATE


# ── chọn kho ────────────────────────────────────────────────────────────────
OPTS = [
    WarehouseOption(1, "p1", priority=1),
    WarehouseOption(2, "p2", priority=0),
    WarehouseOption(3, "p3", priority=0, is_default=True),
]


def test_thu_tu_kho_priority_roi_mac_dinh():
    assert [o.warehouse_id for o in rules.ordered_options(OPTS)] == [3, 2, 1]


def test_chon_kho_dau_tien_du_moi_dong():
    demand = {10: 2, 11: 1}
    stock = {(3, 10): 2, (3, 11): 0, (2, 10): 5, (2, 11): 1, (1, 10): 9, (1, 11): 9}
    assert rules.choose_warehouse(demand, OPTS, stock).warehouse_id == 2


def test_khong_kho_nao_du_thi_none():
    demand = {10: 2, 11: 1}
    stock = {(3, 10): 2, (2, 11): 1, (1, 10): 1}
    assert rules.choose_warehouse(demand, OPTS, stock) is None
    assert rules.choose_warehouse({}, OPTS, stock) is None


def test_nhu_cau_no_combo_ra_ma_le():
    lines = [_line(1, qty=2), _line(5, qty=1)]
    assert rules.demand_of(lines, {5: [(20, 1), (21, 2)]}) == {1: 2, 20: 1, 21: 2}
    assert rules.demand_of([(1, 2), (1, 3)]) == {1: 5}


# ── tách kiện ───────────────────────────────────────────────────────────────
LINES = (_line(1, qty=3), _line(2, qty=1), _line(1, qty=1, price=0))  # dòng 3 = tặng cùng SP 1


def test_mot_kho_hop_le():
    rules.validate_parts(LINES, [PartPlan(1)], [1, 2])


@pytest.mark.parametrize(
    ("parts", "message"),
    [
        ([], "Chưa chọn"),
        ([PartPlan(9)], "chưa ghép"),
        ([PartPlan(1, ((1, 4),)), PartPlan(2)], "ít nhất một dòng"),
        ([PartPlan(1, ((1, 3),)), PartPlan(2, ((1, 1), (2, 1)))], None),
        ([PartPlan(1, ((1, 2),)), PartPlan(2, ((1, 1), (2, 1)))], "đúng bằng"),
        ([PartPlan(1, ((1, 4), (7, 1))), PartPlan(2, ((2, 1),))], "không có trong đơn"),
        ([PartPlan(1, ((1, 0),)), PartPlan(2, ((2, 1),))], "> 0"),
        ([PartPlan(1, ((1, 2), (1, 2))), PartPlan(2, ((2, 1),))], "lặp"),
    ],
)
def test_validate_tach_kien(parts, message):
    if message is None:
        rules.validate_parts(LINES, parts, [1, 2])
        return
    with pytest.raises(RoutingError, match=message):
        rules.validate_parts(LINES, parts, [1, 2])


def test_toi_da_so_kien():
    with pytest.raises(RoutingError, match="Tối đa"):
        rules.validate_parts(LINES, [PartPlan(1, ((1, 1),))] * 11, [1])


def test_chia_dong_giu_gia_tung_dong():
    part_a, part_b = rules.split_lines(LINES, [((1, 3),), ((1, 1), (2, 1))])
    assert [(line.product_id, line.quantity, line.unit_price) for line in part_a] == [(1, 3, Decimal(100_000))]
    # Kiện 2 nhận nốt dòng tặng giá 0 của SP 1.
    assert [(line.product_id, line.quantity, line.unit_price) for line in part_b] == [
        (1, 1, Decimal(0)), (2, 1, Decimal(100_000)),
    ]


def test_chia_vuot_so_luong_bao_loi():
    with pytest.raises(RoutingError):
        rules.split_lines(LINES, [((2, 2),)])


# ── tiền ────────────────────────────────────────────────────────────────────
def test_allocate_tong_luon_khop():
    assert rules.allocate(100, [1, 1, 1]) == [33, 33, 34]
    assert rules.allocate(0, [5, 5]) == [0, 0]
    assert rules.allocate(50, [0, 0]) == [0, 50]
    assert rules.allocate(10, []) == []


def test_tien_mot_kien_cod_bang_tong_sau_giam_tru_tra_truoc():
    order = _order(discount_amount=Decimal(20_000), total_after_discount=Decimal(280_000),
                   customer_prepaid=Decimal(50_000), lines=(_line(1, qty=3),))
    assert rules.part_money(order, [order.lines]) == [PartMoney(total_discount=20_000, cod=230_000, prepaid=50_000)]


def test_tien_tra_truoc_du_thi_cod_0():
    order = _order(total_after_discount=Decimal(100_000), customer_prepaid=Decimal(150_000))
    assert rules.part_money(order, [order.lines])[0].cod == 0


def test_tien_tach_kien_chia_theo_gia_tri():
    lines = (_line(1, qty=1, price=300_000), _line(2, qty=1, price=100_000))
    order = _order(discount_amount=Decimal(40_000), total_after_discount=Decimal(360_000), lines=lines)
    money = rules.part_money(order, [(lines[0],), (lines[1],)])
    assert [m.total_discount for m in money] == [30_000, 10_000]
    assert [m.cod for m in money] == [270_000, 90_000]
    assert sum(m.cod for m in money) == 360_000


# ── payload ─────────────────────────────────────────────────────────────────
def test_payload_day_du():
    order = _order(sales_status_code="closed_express", channel_name="Landing A",
                   lines=(_line(1, qty=2, price=150_000, discount=10_000, variation="var-1"), _line(2, price=0)))
    payload = rules.build_payload(
        order, order.lines, pancake_warehouse_id="wh-1", money=PartMoney(5_000, 275_000, 0),
        source_ref=("-7", "123"),
    )
    assert payload["bill_phone_number"] == "0912345678"  # SĐT đầy đủ, không che
    assert payload["shipping_address"] == {
        "full_name": "Khách A", "phone_number": "0912345678",
        "address": "1 Trần Phú, Hà Đông, Hà Nội", "full_address": "1 Trần Phú, Hà Đông, Hà Nội",
    }
    assert "province_id" not in payload["shipping_address"]  # Pancake tự tách từ chữ
    assert payload["warehouse_id"] == "wh-1"
    assert payload["items"][0] == {
        "variation_id": "var-1", "quantity": 2, "discount_each_product": 10_000, "is_discount_percent": False,
        "is_bonus_product": False, "variation_info": {"retail_price": 150_000},
    }
    assert payload["items"][1]["is_bonus_product"] is True
    assert (payload["shipping_fee"], payload["total_discount"], payload["cod"]) == (0, 5_000, 275_000)
    assert payload["custom_id"] == "10"
    assert payload["order_sources"] == "-7" and payload["account"] == 123
    assert "transfer_money" not in payload
    assert payload["note"] == "CRM #10 · landing · Landing A · HỎA TỐC"


def test_payload_kien_tach_va_tra_truoc():
    payload = rules.build_payload(_order(), _order().lines, pancake_warehouse_id="w", money=PartMoney(0, 0, 70_000),
                                  part_no=2, part_count=3)
    assert payload["custom_id"] == "10-2"
    assert "kiện 2/3" in payload["note"]
    assert payload["transfer_money"] == 70_000
    assert "order_sources" not in payload


def test_parse_source_map():
    assert rules.parse_source_map("landing=-7:123, messenger=-1;zalo=;bad, =x") == {
        "landing": ("-7", "123"), "messenger": ("-1", ""),
    }
    assert rules.parse_source_map("") == {}


# ── sửa / huỷ ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("cancelled", "changed", "status", "expected"),
    [
        (False, False, 0, ChangeAction.NONE),
        (False, True, 0, ChangeAction.UPDATE),
        (False, True, 1, ChangeAction.UPDATE),
        (True, False, 0, ChangeAction.CANCEL),
        (False, True, 2, ChangeAction.LOCKED),
        (True, False, 8, ChangeAction.LOCKED),
        (True, False, 6, ChangeAction.GONE),
        (False, True, 7, ChangeAction.GONE),
    ],
)
def test_plan_change(cancelled, changed, status, expected):
    locked = {13, 8, 9, 2, 3, 16, 4, 15, 5}
    assert rules.plan_change(crm_cancelled=cancelled, payload_changed=changed, pancake_status=status,
                             locked_statuses=locked) == expected
