"""Đẩy đơn CRM → Pancake (two-way-sync §4.1, §6b, A2) trên DB thật + Pancake giả:
phát hiện đơn chốt, chống trùng landing ↔ Foxia, chọn kho / cần chọn cách xuất, tách
kiện, idempotent khi mất response, tranh chấp worker, sửa/huỷ trước/sau khi kho xử lý."""

from __future__ import annotations

import itertools
from datetime import timedelta
from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.models import StockItem
from apps.orders.models import Order, OrderAssessment, OrderItem
from apps.pancake_pos.application.services import order_link_admin
from apps.pancake_pos.application.services.order_push_service import OrderPushService, PushConfig
from apps.pancake_pos.domain.order_push import PartPlan, RoutingError
from apps.pancake_pos.infrastructure.order_reader import SqlOrderReader
from apps.pancake_pos.infrastructure.pancake_pos_client import (
    PancakePosApiError,
    PancakePosRateLimited,
    PancakePosTransportError,
)
from apps.pancake_pos.models import (
    OrderLinkStatus,
    PancakePosOrderLink,
    PancakePosProductLink,
    PancakePosStatusMap,
    PancakePosVariationLink,
    PancakePosWarehouseLink,
)
from apps.pancake_pos.repositories import OrderLinkConflict, OrderLinkRepository
from apps.pancake_pos.tests.fakes import FakePancakeOrders
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db

_seq = itertools.count(1)


class Alerts:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def __call__(self, *, key: str, title: str, lines=()) -> bool:
        self.sent.append({"key": key, "title": title, "lines": list(lines)})
        return True


@pytest.fixture
def fake():
    return FakePancakeOrders()


@pytest.fixture
def alerts():
    return Alerts()


@pytest.fixture
def service(fake, alerts):
    return OrderPushService(gateway=fake, reader=SqlOrderReader(), alert=alerts,
                            config=PushConfig(source_map={"landing": ("-7", "")}))


@pytest.fixture
def warehouses():
    dm1 = Warehouse.objects.create(code="DM1", name="Đại Mỗ 1")
    dm2 = Warehouse.objects.create(code="DM2", name="Đại Mỗ 2")
    PancakePosWarehouseLink.objects.create(warehouse=dm1, pancake_warehouse_id="pw-1", priority=0)
    PancakePosWarehouseLink.objects.create(warehouse=dm2, pancake_warehouse_id="pw-2", priority=1)
    return dm1, dm2


@pytest.fixture
def channel():
    return Channel.objects.create(code="ch-ppos", name="Landing A")


def _product(sku: str, *, variation: str | None = "auto") -> Product:
    product = Product.objects.create(name=f"SP {sku}", sku=sku)
    if variation:
        group = PancakePosProductLink.objects.create(group_key=f"g-{sku}", pancake_product_id=f"pp-{sku}")
        PancakePosVariationLink.objects.create(
            product=product, product_link=group, status="synced",
            pancake_variation_id=f"var-{sku}" if variation == "auto" else variation,
        )
    return product


def _stock(warehouse: Warehouse, product: Product, qty: int) -> None:
    StockItem.objects.create(warehouse=warehouse, product=product, state="sellable", quantity=qty)


def _order(channel, lines, *, source="landing", sales="closed", status=0, closed_ago=timedelta(minutes=5),
           invoice="", prepaid=0, discount=0, assessed_ago: timedelta | None = None) -> Order:
    n = next(_seq)
    customer = Customer.objects.create(full_name=f"Khách {n}", phone_raw=f"09100{n:05d}",
                                       phone_normalized=f"09100{n:05d}", code=f"CUST-PPOS-{n}")
    total = sum(qty * price for _, qty, price in lines)
    order = Order.objects.create(
        channel=channel, customer=customer, name=f"Khách {n}", phone=f"09100{n:05d}",
        address="1 Trần Phú, Hà Đông, Hà Nội", order_source=source, status=status, invoice_code=invoice,
        total_amount=total, discount_amount=discount, total_after_discount=total - discount,
        customer_prepaid=prepaid,
    )
    Order.all_objects.filter(id=order.id).update(sales_closed_at=timezone.now() - closed_ago)
    if sales:
        OrderAssessment.objects.update_or_create(order=order, defaults={"sales_status_code": sales})
        # Như prod: đánh giá sửa lần cuối lúc chốt (trừ khi test đặt khác).
        OrderAssessment.objects.filter(order=order).update(
            updated_at=timezone.now() - (closed_ago if assessed_ago is None else assessed_ago)
        )
    for product, qty, price in lines:
        OrderItem.objects.create(order=order, product=product, product_name=product.name, sku=product.sku,
                                 quantity=qty, unit_price=price, line_total=qty * price)
    return order


def _link(order: Order, part_no: int = 1) -> PancakePosOrderLink:
    return PancakePosOrderLink.objects.get(order_id=order.id, part_no=part_no)


# ── phát hiện + chọn kho ────────────────────────────────────────────────────
def test_don_chot_chon_kho_uu_tien_du_hang(service, warehouses, channel):
    dm1, dm2 = warehouses
    a, b = _product("A"), _product("B")
    _stock(dm1, a, 5)  # DM1 thiếu B
    _stock(dm2, a, 5)
    _stock(dm2, b, 1)
    order = _order(channel, [(a, 2, 100_000), (b, 1, 50_000)])

    result = service.discover()

    link = _link(order)
    assert (link.status, link.warehouse_id, link.pancake_warehouse_id) == ("pending", dm2.id, "pw-2")
    assert result["queued"] == 1


def test_khong_kho_nao_du_hang_thi_can_chon_cach_xuat(service, warehouses, channel):
    dm1, dm2 = warehouses
    a, b = _product("A"), _product("B")
    _stock(dm1, a, 5)
    _stock(dm2, b, 5)
    order = _order(channel, [(a, 1, 100_000), (b, 1, 100_000)])
    service.discover()
    assert _link(order).status == OrderLinkStatus.NEEDS_ROUTING
    assert service.push() == {}  # không tự đẩy


def test_combo_tinh_ton_theo_ma_le(service, warehouses, channel):
    dm1, _ = warehouses
    combo, part = _product("COMBO"), _product("LE", variation=None)
    django_apps.get_model("catalog", "ProductComponent").objects.create(parent=combo, component=part, quantity=2)
    _stock(dm1, part, 4)
    ok = _order(channel, [(combo, 2, 300_000)])
    too_many = _order(channel, [(combo, 3, 300_000)])
    service.discover()
    assert _link(ok).status == OrderLinkStatus.PENDING
    assert _link(too_many).status == OrderLinkStatus.NEEDS_ROUTING


def test_bo_qua_don_khong_du_dieu_kien_khong_tao_lien_ket(service, warehouses, channel):
    dm1, _ = warehouses
    a = _product("A")
    _stock(dm1, a, 99)
    orders = [
        _order(channel, [(a, 1, 1)], sales="need_more_consulting"),
        _order(channel, [(a, 1, 1)], sales="closed_scheduled_date"),
        _order(channel, [(a, 1, 1)], status=2),
        _order(channel, [(a, 1, 1)], source="shopee"),
        _order(channel, [(a, 1, 1)], closed_ago=timedelta(hours=80)),
    ]
    service.discover()
    assert not PancakePosOrderLink.objects.filter(order_id__in=[o.id for o in orders]).exists()


def test_don_da_co_hoa_don_kiotviet_van_duoc_day(service, warehouses, channel):
    """KiotViet vẫn xuất hoá đơn song song với Pancake: `invoice_code` có ngay khi chốt,
    không phải tín hiệu "đã xuất kho"."""
    dm1, _ = warehouses
    a = _product("A")
    _stock(dm1, a, 9)
    order = _order(channel, [(a, 1, 1)], sales="created_order", invoice="HD001")
    service.discover()
    assert _link(order).status == OrderLinkStatus.PENDING


def test_chi_day_don_chot_sau_moc_bat_dau(fake, alerts, warehouses, channel):
    """`PANCAKE_POS_PUSH_ORDERS_SINCE`: đơn chốt trước mốc (kể cả vừa sửa đánh giá) không đẩy;
    người vận hành chủ động đẩy (ignore_lookback) thì bỏ qua mốc."""
    dm1, _ = warehouses
    a = _product("A")
    _stock(dm1, a, 9)
    svc = OrderPushService(gateway=fake, reader=SqlOrderReader(), alert=alerts,
                           config=PushConfig(push_since=timezone.now() - timedelta(minutes=30)))
    before = _order(channel, [(a, 1, 1)], closed_ago=timedelta(hours=1))
    edited = _order(channel, [(a, 1, 1)], closed_ago=timedelta(hours=1), assessed_ago=timedelta(minutes=1))
    after = _order(channel, [(a, 1, 1)], closed_ago=timedelta(minutes=5))

    result = svc.discover()

    # `before` rơi khỏi cửa sổ quét ngay trong SQL; `edited` lọt SQL (sửa đánh giá) nhưng bị luật chặn.
    assert result["before_push_since"] == 1
    assert not PancakePosOrderLink.objects.filter(order_id__in=[before.id, edited.id]).exists()
    assert _link(after).status == OrderLinkStatus.PENDING
    svc.discover([before.id], ignore_lookback=True)
    assert _link(before).status == OrderLinkStatus.PENDING


def test_hen_ngay_roi_chot_sau_nhieu_ngay_van_duoc_quet(service, warehouses, channel):
    """closed_scheduled_date → closed đi trong nội bộ nhóm chốt: `sales_closed_at` giữ mốc
    cũ (5 ngày) — quét phải bắt theo `order_assessments.updated_at`."""
    dm1, _ = warehouses
    a = _product("A")
    _stock(dm1, a, 9)
    order = _order(channel, [(a, 1, 1)], closed_ago=timedelta(days=5), assessed_ago=timedelta(minutes=1))
    service.discover()
    assert _link(order).status == OrderLinkStatus.PENDING


def test_chua_ghep_sku_la_loi_hien_tren_ui(service, warehouses, channel):
    order = _order(channel, [(_product("NOVAR", variation=None), 1, 1)])
    service.discover()
    link = _link(order)
    assert link.status == OrderLinkStatus.ERROR and "NOVAR" in link.last_error
    assert link.pancake_warehouse_id == ""  # không bị tự thử lại
    assert service.push() == {}


def test_chua_ghep_kho_thi_khong_lam_gi(fake, alerts, channel):
    order = _order(channel, [(_product("A"), 1, 1)])
    svc = OrderPushService(gateway=fake, reader=SqlOrderReader(), alert=alerts)
    assert svc.discover() == {"no_warehouse_links": 1}
    assert not PancakePosOrderLink.objects.filter(order_id=order.id).exists()


def test_discover_idempotent(service, warehouses, channel):
    dm1, _ = warehouses
    a = _product("A")
    _stock(dm1, a, 9)
    order = _order(channel, [(a, 1, 1)])
    service.discover()
    service.discover([order.id])
    assert PancakePosOrderLink.objects.filter(order_id=order.id).count() == 1


def test_doc_don_theo_lo_khong_n_cong_1(warehouses, channel):
    a, b = _product("A"), _product("B")
    ids = [_order(channel, [(a, 1, 1), (b, 2, 2)]).id for _ in range(5)]
    with CaptureQueriesContext(connection) as ctx:
        orders = SqlOrderReader().load_orders(ids)
    assert len(orders) == 5 and all(len(o.lines) == 2 for o in orders.values())
    assert len(ctx.captured_queries) == 3


# ── chống trùng landing ↔ Foxia ─────────────────────────────────────────────
def _foxia_pair(channel, landing: Order, *, foxia_lines, cohoi: int) -> Order:
    lead_model = django_apps.get_model("foxia", "FoxiaLeadLink")
    link_model = django_apps.get_model("foxia", "FoxiaOrderLink")
    lead_model.objects.create(order=landing, cohoi_id=cohoi)
    foxia = _order(channel, foxia_lines, source="foxia", sales="")
    link_model.objects.create(order=foxia, foxia_id=cohoi + 1, cohoi_id=cohoi)
    return foxia


def test_landing_chi_co_co_hoi_foxia_van_day(service, warehouses, channel):
    dm1, _ = warehouses
    a = _product("A")
    _stock(dm1, a, 9)
    landing = _order(channel, [(a, 1, 1)])
    django_apps.get_model("foxia", "FoxiaLeadLink").objects.create(order=landing, cohoi_id=555)
    service.discover()
    assert _link(landing).status == OrderLinkStatus.PENDING


def test_landing_da_thanh_don_foxia_thi_bo_qua_va_canh_bao(service, alerts, warehouses, channel):
    dm1, _ = warehouses
    a = _product("A")
    _stock(dm1, a, 9)
    landing = _order(channel, [(a, 1, 1)])
    foxia = _foxia_pair(channel, landing, foxia_lines=[(a, 1, 1)], cohoi=777)

    service.discover()

    assert _link(landing).status == OrderLinkStatus.SKIPPED_DUPLICATE
    assert f"#{foxia.id}" in _link(landing).last_error
    assert _link(foxia).status == OrderLinkStatus.PENDING  # đơn Foxia là đơn thật
    assert len(alerts.sent) == 1 and str(landing.id) in alerts.sent[0]["lines"][0]
    service.discover()
    assert len(alerts.sent) == 1  # không kêu lại


def test_van_day_bo_luat_chong_trung(service, alerts, warehouses, channel):
    dm1, _ = warehouses
    a = _product("A")
    _stock(dm1, a, 9)
    landing = _order(channel, [(a, 1, 1)])
    _foxia_pair(channel, landing, foxia_lines=[(a, 1, 1)], cohoi=778)
    service.discover()
    link = _link(landing)
    assert link.status == OrderLinkStatus.SKIPPED_DUPLICATE
    # Thử lại thường: đánh giá lại → vẫn trùng.
    order_link_admin.retry_link(link.id)
    service.discover([landing.id], ignore_lookback=True)
    assert _link(landing).status == OrderLinkStatus.SKIPPED_DUPLICATE
    # "Vẫn đẩy": bỏ luật chống trùng.
    order_link_admin.retry_link(_link(landing).id, force=True)
    service.discover([landing.id], ignore_lookback=True, force_ids=[landing.id])
    assert _link(landing).status == OrderLinkStatus.PENDING
    with pytest.raises(OrderLinkConflict):
        order_link_admin.retry_link(_link(landing).id, force=True)  # chỉ cho skipped_duplicate


def test_don_foxia_ma_landing_goc_da_day_thi_bo_qua(service, alerts, warehouses, channel, fake):
    dm1, _ = warehouses
    a = _product("A")
    _stock(dm1, a, 9)
    landing = _order(channel, [(a, 1, 1)])
    service.discover()
    service.push()
    assert _link(landing).status == OrderLinkStatus.PUSHED
    foxia = _foxia_pair(channel, landing, foxia_lines=[(a, 1, 1)], cohoi=888)

    service.discover([foxia.id])

    assert _link(foxia).status == OrderLinkStatus.SKIPPED_DUPLICATE
    assert len(fake.creates()) == 1
    assert alerts.sent and "Foxia" in alerts.sent[0]["title"]


def test_don_foxia_da_huy_khong_chan_landing(service, warehouses, channel):
    dm1, _ = warehouses
    a = _product("A")
    _stock(dm1, a, 9)
    landing = _order(channel, [(a, 1, 1)])
    foxia = _foxia_pair(channel, landing, foxia_lines=[(a, 1, 1)], cohoi=999)
    Order.all_objects.filter(id=foxia.id).update(status=6)
    service.discover([landing.id])
    assert _link(landing).status == OrderLinkStatus.PENDING


# ── đẩy + idempotent ────────────────────────────────────────────────────────
def test_day_don_thanh_cong(service, fake, warehouses, channel):
    dm1, _ = warehouses
    a = _product("A")
    _stock(dm1, a, 9)
    order = _order(channel, [(a, 2, 150_000)], prepaid=50_000, discount=20_000)
    service.discover()
    assert service.push() == {"pushed": 1}
    link = _link(order)
    assert link.status == OrderLinkStatus.PUSHED and link.pancake_order_id == "1001"
    assert link.payload_hash and link.pushed_at and link.attempts == 1
    payload = fake.creates()[0]
    assert payload["custom_id"] == str(order.id)
    assert payload["warehouse_id"] == "pw-1"
    assert payload["items"] == [{
        "variation_id": "var-A", "quantity": 2, "discount_each_product": 0, "is_discount_percent": False,
        "is_bonus_product": False, "variation_info": {"retail_price": 150_000},
    }]
    assert (payload["total_discount"], payload["cod"], payload["transfer_money"]) == (20_000, 230_000, 50_000)
    assert payload["order_sources"] == "-7"
    assert payload["bill_phone_number"] == order.phone


@pytest.mark.parametrize(
    "error",
    [PancakePosTransportError("ReadTimeout"), PancakePosApiError("HTTP 502", status_code=502)],
)
def test_mat_response_khong_tao_trung(service, fake, warehouses, channel, error):
    dm1, _ = warehouses
    a = _product("A")
    _stock(dm1, a, 9)
    order = _order(channel, [(a, 1, 1)])
    service.discover()
    fake.lose_response_on_create = error  # Pancake ĐÃ tạo nhưng CRM không nhận response

    assert service.push() == {"uncertain": 1}
    link = _link(order)
    assert link.status == OrderLinkStatus.ERROR and link.needs_lookup
    assert str(order.id) in link.last_error

    PancakePosOrderLink.objects.filter(id=link.id).update(attempted_at=timezone.now() - timedelta(minutes=10))
    assert service.push() == {"relinked": 1}
    link = _link(order)
    assert link.status == OrderLinkStatus.PUSHED and link.pancake_order_id == "1001"
    assert len(fake.creates()) == 1  # KHÔNG tạo đơn thứ hai
    assert link.checked_at is None and link.payload_hash == ""  # pha đối chiếu so lại


def test_mat_response_ma_pancake_that_su_chua_tao_thi_tao_lai(service, fake, warehouses, channel):
    dm1, _ = warehouses
    a = _product("A")
    _stock(dm1, a, 9)
    order = _order(channel, [(a, 1, 1)])
    service.discover()
    fake.create_error = PancakePosTransportError("ReadTimeout")
    service.push()
    fake.create_error = None
    PancakePosOrderLink.objects.filter(order_id=order.id).update(attempted_at=timezone.now() - timedelta(minutes=10))
    assert service.push() == {"pushed": 1}
    assert [c[0] for c in fake.calls] == ["create", "find", "create"]


def test_tra_lai_loi_thi_khong_tao(service, fake, warehouses, channel):
    dm1, _ = warehouses
    a = _product("A")
    _stock(dm1, a, 9)
    order = _order(channel, [(a, 1, 1)])
    service.discover()
    fake.create_error = PancakePosTransportError("ReadTimeout")
    service.push()
    fake.find_error = PancakePosTransportError("ReadTimeout")
    PancakePosOrderLink.objects.filter(order_id=order.id).update(attempted_at=timezone.now() - timedelta(minutes=10))
    assert service.push() == {"lookup_failed": 1}
    assert len(fake.creates()) == 1  # chỉ lần POST đầu
    assert _link(order).needs_lookup


def test_4xx_la_loi_ro_rang_tu_thu_lai_co_gioi_han(service, fake, warehouses, channel):
    dm1, _ = warehouses
    a = _product("A")
    _stock(dm1, a, 9)
    order = _order(channel, [(a, 1, 1)])
    service.discover()
    fake.create_error = PancakePosApiError("HTTP 400 — warehouse_id sai", status_code=400)
    assert service.push() == {"rejected": 1}
    assert not _link(order).needs_lookup
    for _ in range(5):
        PancakePosOrderLink.objects.filter(order_id=order.id).update(
            attempted_at=timezone.now() - timedelta(minutes=10)
        )
        service.push()
    assert _link(order).attempts == 3 and len(fake.creates()) == 3


def test_429_chac_chan_chua_tao(service, fake, warehouses, channel):
    dm1, _ = warehouses
    a = _product("A")
    _stock(dm1, a, 9)
    order = _order(channel, [(a, 1, 1)])
    service.discover()
    fake.create_error = PancakePosRateLimited("HTTP 429", status_code=429)
    assert service.push() == {"rate_limited": 1}
    assert not _link(order).needs_lookup


def test_hai_worker_khong_cung_day_mot_don(warehouses, channel):
    dm1, _ = warehouses
    a = _product("A")
    _stock(dm1, a, 9)
    order = _order(channel, [(a, 1, 1)])
    OrderLinkRepository.create_first(order.id, status="pending", warehouse_id=dm1.id, pancake_warehouse_id="pw-1")
    kw = {"stale_seconds": 600, "retry_after_seconds": 300, "max_auto_attempts": 3, "limit": 10}
    first = OrderLinkRepository.claim_pushable(**kw)
    second = OrderLinkRepository.claim_pushable(**kw)
    assert len(first) == 1 and second == []
    assert _link(order).status == OrderLinkStatus.PUSHING
    assert not OrderLinkRepository.create_first(order.id, status="pending")  # UNIQUE (order, part)


def test_pushing_qua_han_thi_tra_lai_truoc_khi_tao(service, fake, warehouses, channel):
    """Worker chết sau POST, trước khi ghi kết quả → dòng kẹt `pushing`."""
    dm1, _ = warehouses
    a = _product("A")
    _stock(dm1, a, 9)
    order = _order(channel, [(a, 1, 1)])
    service.discover()
    fake.orders["555"] = {"custom_id": str(order.id), "bill_phone_number": order.phone, "status": 0}
    PancakePosOrderLink.objects.filter(order_id=order.id).update(
        status="pushing", attempted_at=timezone.now() - timedelta(minutes=30)
    )
    assert service.push() == {"relinked": 1}
    assert _link(order).pancake_order_id == "555" and fake.creates() == []


def test_huy_truoc_khi_day(service, fake, warehouses, channel):
    dm1, _ = warehouses
    a = _product("A")
    _stock(dm1, a, 9)
    order = _order(channel, [(a, 1, 1)])
    service.discover()
    Order.all_objects.filter(id=order.id).update(status=6)
    assert service.push() == {"cancelled_before_push": 1}
    assert _link(order).status == OrderLinkStatus.CANCELLED and fake.creates() == []


# ── chọn cách xuất / tách kiện ──────────────────────────────────────────────
def test_tach_kien_thanh_n_don_pancake(service, fake, warehouses, channel):
    dm1, dm2 = warehouses
    a, b = _product("A"), _product("B")
    _stock(dm1, a, 5)
    _stock(dm2, b, 5)
    order = _order(channel, [(a, 1, 300_000), (b, 1, 100_000)], discount=40_000)
    service.discover()
    assert _link(order).status == OrderLinkStatus.NEEDS_ROUTING

    ids = order_link_admin.route_order(order.id, [PartPlan(dm1.id, ((a.id, 1),)), PartPlan(dm2.id, ((b.id, 1),))])
    assert len(ids) == 2
    assert service.push([order.id]) == {"pushed": 2}

    first, second = fake.creates()
    assert (first["custom_id"], first["warehouse_id"]) == (f"{order.id}-1", "pw-1")
    assert (second["custom_id"], second["warehouse_id"]) == (f"{order.id}-2", "pw-2")
    assert [i["variation_id"] for i in first["items"]] == ["var-A"]
    assert (first["total_discount"], second["total_discount"]) == (30_000, 10_000)
    assert first["cod"] + second["cod"] == 360_000
    assert PancakePosOrderLink.objects.filter(order_id=order.id, status="pushed").count() == 2


def test_chon_mot_kho_chi_dinh(service, fake, warehouses, channel):
    dm1, dm2 = warehouses
    a, b = _product("A"), _product("B")
    order = _order(channel, [(a, 1, 1), (b, 1, 1)])
    service.discover()
    order_link_admin.route_order(order.id, [PartPlan(dm2.id)])
    service.push([order.id])
    assert fake.creates()[0]["warehouse_id"] == "pw-2"
    assert fake.creates()[0]["custom_id"] == str(order.id)


def test_chon_cach_xuat_sai_hoac_da_day(service, warehouses, channel):
    dm1, dm2 = warehouses
    a = _product("A")
    _stock(dm1, a, 9)
    order = _order(channel, [(a, 2, 1)])
    with pytest.raises(RoutingError):
        order_link_admin.route_order(order.id, [PartPlan(dm1.id, ((a.id, 1),)), PartPlan(dm2.id, ((a.id, 2),))])
    service.discover()
    service.push()
    with pytest.raises(OrderLinkConflict):
        order_link_admin.route_order(order.id, [PartPlan(dm2.id)])


def test_ngu_canh_chon_cach_xuat(warehouses, channel):
    dm1, dm2 = warehouses
    a, b = _product("A"), _product("B")
    _stock(dm1, a, 3)
    _stock(dm2, b, 1)
    order = _order(channel, [(a, 2, 1), (b, 1, 1)])
    ctx = order_link_admin.routing_context(order.id)
    assert [line["sku"] for line in ctx["lines"]] == ["A", "B"]
    by_wh = {w["warehouse_id"]: w for w in ctx["warehouses"]}
    assert by_wh[dm1.id]["stock"] == {str(a.id): 3, str(b.id): 0} and not by_wh[dm1.id]["enough"]
    assert [w["warehouse_id"] for w in ctx["warehouses"]] == [dm1.id, dm2.id]


def test_thu_lai_danh_gia_lai(service, warehouses, channel):
    dm1, _ = warehouses
    a, b = _product("A"), _product("B")
    _stock(dm1, a, 9)
    order = _order(channel, [(a, 1, 1), (b, 1, 1)])
    service.discover()
    assert _link(order).status == OrderLinkStatus.NEEDS_ROUTING
    _stock(dm1, b, 9)  # hàng về
    order_link_admin.retry_link(_link(order).id)
    assert not PancakePosOrderLink.objects.filter(order_id=order.id).exists()
    service.discover([order.id], ignore_lookback=True)
    assert _link(order).status == OrderLinkStatus.PENDING


# ── sửa / huỷ sau khi đẩy ───────────────────────────────────────────────────
def _pushed(service, fake, warehouses, channel):
    dm1, _ = warehouses
    a = _product(f"S{next(_seq)}")
    _stock(dm1, a, 9)
    order = _order(channel, [(a, 1, 100_000)])
    service.discover()
    service.push()
    link = _link(order)
    PancakePosOrderLink.objects.filter(id=link.id).update(checked_at=timezone.now() - timedelta(minutes=1))
    return order, link


def test_sua_dia_chi_truoc_khi_kho_xu_ly_thi_put(service, fake, warehouses, channel):
    order, link = _pushed(service, fake, warehouses, channel)
    Order.objects.filter(id=order.id).update(address="99 Lê Lợi, Hà Nội", updated_at=timezone.now())
    assert service.sync_changes() == {"updated": 1}
    kind, (pancake_id, payload) = fake.calls[-1]
    assert (kind, pancake_id) == ("update", link.pancake_order_id)
    assert payload["shipping_address"]["address"] == "99 Lê Lợi, Hà Nội"
    assert _link(order).status == OrderLinkStatus.UPDATED
    assert service.sync_changes() == {}  # đã đối chiếu, không PUT lại


def test_khong_doi_gi_thi_khong_goi_pancake(service, fake, warehouses, channel):
    order, _ = _pushed(service, fake, warehouses, channel)
    Order.objects.filter(id=order.id).update(updated_at=timezone.now())  # chạm nhưng payload không đổi
    before = len(fake.calls)
    assert service.sync_changes() == {"unchanged": 1}
    assert len(fake.calls) == before


def test_sua_sau_khi_kho_da_xu_ly_chi_canh_bao(service, fake, alerts, warehouses, channel):
    order, link = _pushed(service, fake, warehouses, channel)
    fake.orders[link.pancake_order_id]["status"] = 2  # Đã gửi hàng
    OrderItem.objects.filter(order=order).update(quantity=3, updated_at=timezone.now())
    assert service.sync_changes() == {"locked": 1}
    assert not [c for c in fake.calls if c[0] == "update"]
    assert "đã xử lý" in _link(order).last_error
    assert alerts.sent


def test_huy_truoc_khi_kho_xu_ly_thi_huy_tren_pancake(service, fake, warehouses, channel):
    order, link = _pushed(service, fake, warehouses, channel)
    Order.objects.filter(id=order.id).update(status=6, updated_at=timezone.now())
    assert service.sync_changes([order.id]) == {"cancelled": 1}
    assert fake.calls[-1] == ("update", (link.pancake_order_id, {"status": 6}))
    assert _link(order).status == OrderLinkStatus.CANCELLED


def test_huy_sau_khi_kho_xu_ly_chi_canh_bao(service, fake, alerts, warehouses, channel):
    order, link = _pushed(service, fake, warehouses, channel)
    fake.orders[link.pancake_order_id]["status"] = 8  # Đang đóng hàng
    OrderAssessment.objects.filter(order=order).update(sales_status_code="cancelled", updated_at=timezone.now())
    assert service.sync_changes() == {"locked": 1}
    assert "huỷ" in _link(order).last_error and alerts.sent


def test_doc_pancake_loi_thi_thu_lai_luot_sau(service, fake, warehouses, channel):
    order, _ = _pushed(service, fake, warehouses, channel)
    Order.objects.filter(id=order.id).update(address="X", updated_at=timezone.now())
    fake.get_error = PancakePosTransportError("timeout")
    assert service.sync_changes() == {"get_failed": 1}
    fake.get_error = None
    assert service.sync_changes() == {"updated": 1}


def test_status_map_seed():
    assert PancakePosStatusMap.objects.count() == 17
    locked = set(PancakePosStatusMap.objects.filter(locks_edit=True).values_list("pancake_status", flat=True))
    assert {2, 3, 8, 9, 13}.issubset(locked) and not locked & {0, 1, 6, 7, 17}
    assert PancakePosStatusMap.objects.get(pancake_status=5).crm_status == 5


def test_decimal_tien_tu_db(warehouses, channel):
    a = _product("A")
    order = _order(channel, [(a, 1, 99_999)], prepaid=1)
    crm = SqlOrderReader().load_orders([order.id])[order.id]
    assert crm.customer_prepaid == Decimal(1) and crm.lines[0].unit_price == Decimal(99_999)


def test_moc_bat_dau_lay_tu_cau_hinh_hoac_lan_dau_bat_co():
    from apps.pancake_pos.application.services.factory import push_since
    from apps.sysconfig import override_sysconfig
    from apps.sysconfig.models import SystemSettingChange

    assert push_since() is None  # chưa cấu hình, chưa từng bật → chỉ còn lookback
    first = SystemSettingChange.objects.create(key="PANCAKE_POS_PUSH_ORDERS_ENABLED", old_value="", new_value="1")
    SystemSettingChange.objects.create(key="PANCAKE_POS_PUSH_ORDERS_ENABLED", old_value="1", new_value="0")
    SystemSettingChange.objects.create(key="PANCAKE_POS_PUSH_ORDERS_ENABLED", old_value="0", new_value="1")
    assert push_since() == first.changed_at
    with override_sysconfig(PANCAKE_POS_PUSH_ORDERS_SINCE="2026-10-01 08:00"):
        value = push_since()
    assert value is not None and value.isoformat() == "2026-10-01T08:00:00+07:00"
