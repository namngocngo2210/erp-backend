"""Xử lý sự kiện `orders` từ webhook Pancake (two-way-sync §5.1, §6b, B1 + MR D) trên DB thật:
đơn CRM đẩy (liên kết) → 2 field partner + mã vận đơn (KHÔNG ghi status); đơn sàn khớp mã
sàn / mã vận đơn; tạo đơn khi cờ bật; chờ khớp / khớp nhiều / quá hạn → Chờ khớp + Lark;
thứ tự phiên bản; đơn tách kiện hiện kiện chậm nhất; đơn mirror Foxia/CNV không bị ghi."""

from __future__ import annotations

import itertools
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderItem
from apps.pancake_pos.application.services.inbound_order_service import InboundConfig, InboundOrderService
from apps.pancake_pos.domain import inbound_order as rules
from apps.pancake_pos.inbound_repositories import WebhookEventRepository
from apps.pancake_pos.infrastructure.crm_order_gateway import SqlCrmOrderGateway
from apps.pancake_pos.models import (
    InboundMatchMethod,
    InboundMatchStatus,
    OrderLinkOrigin,
    OrderLinkStatus,
    PancakePosInboundOrder,
    PancakePosOrderLink,
    PancakePosProductLink,
    PancakePosVariationLink,
    PancakePosWebhookEvent,
    WebhookEventStatus,
)
from apps.pancake_pos.tests.test_inbound_domain import order_payload

pytestmark = pytest.mark.django_db

_seq = itertools.count(1)


class Alerts:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def __call__(self, *, key: str, title: str, lines=()) -> bool:
        self.sent.append({"key": key, "title": title, "lines": list(lines)})
        return True


@pytest.fixture
def alerts():
    return Alerts()


class Matched(list):
    """Ghi lại `on_matched` (chạy sau commit) — đơn CRM cần đồng bộ mã vận chuyển."""

    def __call__(self, order_id: int) -> None:
        self.append(order_id)


def make_service(alerts, matched=None, **config) -> InboundOrderService:
    return InboundOrderService(crm=SqlCrmOrderGateway(), alert=alerts, config=InboundConfig(**config),
                               on_matched=matched)


@pytest.fixture
def matched():
    return Matched()


@pytest.fixture
def service(alerts, matched):
    return make_service(alerts, matched, source_map={"-3": "shopee", "-9": "tiktok", "-1": "messenger"})


@pytest.fixture
def channel():
    return Channel.objects.create(code="DELAX MASSAGER VN", name="DELAX MASSAGER VN")


def crm_order(channel, **kw) -> Order:
    n = next(_seq)
    customer = Customer.objects.create(full_name=f"K{n}", phone_raw=f"09200{n:05d}",
                                       phone_normalized=f"09200{n:05d}", code=f"C-IN-{n}")
    defaults = {"order_source": "landing", "status": 1, "total_amount": 1, "total_after_discount": 1}
    return Order.objects.create(channel=channel, customer=customer, name=f"K{n}", phone=f"09200{n:05d}",
                                **{**defaults, **kw})


def event(payload) -> int:
    ev = rules.classify_payload(payload)
    return WebhookEventRepository.store(event_type=ev.event_type, record_id=ev.record_id, version=ev.version,
                                        payload=payload, payload_hash=rules.payload_hash(payload))


@pytest.fixture
def run(django_capture_on_commit_callbacks):
    def _run(service, payload) -> str:
        with django_capture_on_commit_callbacks(execute=True):
            return service.process_event(event(payload))

    return _run


def partner(order: Order) -> tuple[str, str]:
    order.refresh_from_db()
    return order.partner_delivery_status, order.partner_dvvc_delivery_status


# ── đơn CRM đẩy (liên kết A2) ───────────────────────────────────────────────
def test_don_crm_day_ghi_partner_khong_dong_status(service, channel, matched, run):
    order = crm_order(channel)
    before = Order.all_objects.filter(id=order.id).values_list("updated_at", flat=True).get()
    PancakePosOrderLink.objects.create(order=order, pancake_order_id="P-100", status=OrderLinkStatus.PUSHED)
    payload = order_payload(id="P-100", order_sources="-7", marketplace_id=None, status=2,
                            warehouse_id="wh-1", warehouse_info={"name": "Đại Mỗ 1"},
                            partner={"extend_code": "SPXVN0001", "partner_name": "Shopee Xpress",
                                     "partner_status": "on_the_way", "updated_at": "2026-09-30T01:00:00"})
    assert run(service, payload) == "updated"
    assert partner(order) == ("Đã gửi hàng", "Đang trung chuyển")
    # Pancake KHÔNG ghi status; mã vận đơn trống thì điền; updated_at giữ nguyên (A2 không PUT lại).
    assert (order.status, order.shipping_code, order.shipping_provider) == (1, "SPXVN0001", "SPX")
    assert order.updated_at == before
    assert matched == [order.id]
    row = PancakePosInboundOrder.objects.get(pancake_order_id="P-100")
    assert (row.order_id, row.match_status, row.match_method) == (order.id, "matched", "order_link")
    assert (row.status_name, row.dvvc_status, row.warehouse_name, row.pancake_warehouse_id) == (
        "Đã gửi hàng", "Đang trung chuyển", "Đại Mỗ 1", "wh-1")
    assert row.shipped_at is not None and row.status_changed_at is not None and row.last_partner_status_at
    assert PancakePosOrderLink.objects.get(pancake_order_id="P-100").pancake_status == 2
    assert PancakePosWebhookEvent.objects.get().status == WebhookEventStatus.PROCESSED


def test_ma_van_don_da_co_khong_bi_ghi_de(service, channel, run):
    order = crm_order(channel, shipping_code="KV-123", shipping_provider="GHN")
    PancakePosOrderLink.objects.create(order=order, pancake_order_id="P-99", status=OrderLinkStatus.PUSHED)
    run(service, order_payload(id="P-99", order_sources="-7", marketplace_id=None, status=2))
    order.refresh_from_db()
    assert (order.shipping_code, order.shipping_provider) == ("KV-123", "GHN")


def test_idempotent_va_bo_ban_cu(service, channel, run):
    order = crm_order(channel)
    PancakePosOrderLink.objects.create(order=order, pancake_order_id="P-101", status=OrderLinkStatus.PUSHED)
    newer = order_payload(id="P-101", order_sources="-7", marketplace_id=None, status=3,
                          updated_at="2026-09-30T10:00:00")
    older = order_payload(id="P-101", order_sources="-7", marketplace_id=None, status=2,
                          updated_at="2026-09-30T08:00:00")
    assert run(service, newer) == "updated"
    assert run(service, newer) == "busy"  # cùng payload → trùng, không lưu lần 2 (store None)
    assert run(service, older).startswith("ignored")
    assert partner(order)[0] == "Đã nhận"  # bản cũ tới sau không lật lại
    assert order.status == 1
    stale = PancakePosWebhookEvent.objects.order_by("-id").first()
    assert stale.status == WebhookEventStatus.IGNORED and "cũ" in stale.error


def test_ap_lai_cung_ban_khong_ghi(service, channel, run):
    order = crm_order(channel)
    PancakePosOrderLink.objects.create(order=order, pancake_order_id="P-104", status=OrderLinkStatus.PUSHED)
    run(service, order_payload(id="P-104", order_sources="-7", marketplace_id=None, status=2))
    # Bản khác hash (thêm trường) nhưng cùng trạng thái → không UPDATE.
    again = order_payload(id="P-104", order_sources="-7", marketplace_id=None, status=2, note="x")
    assert run(service, again) == "matched"


def test_khop_theo_custom_id_khi_chua_luu_id_pancake(service, channel, run):
    order = crm_order(channel)
    PancakePosOrderLink.objects.create(order=order, part_no=1, status=OrderLinkStatus.PUSHING)
    run(service, order_payload(id="P-102", custom_id=str(order.id), order_sources="-7", marketplace_id=None,
                               status=12))
    row = PancakePosInboundOrder.objects.get(pancake_order_id="P-102")
    assert (row.order_id, row.match_method) == (order.id, "order_link")
    # Không tạo liên kết native song song với dòng A2 đang đẩy.
    assert PancakePosOrderLink.objects.filter(order=order).count() == 1
    assert partner(order)[0] == "Chờ in"


def test_crm_da_huy_van_giu_huy(service, channel, run):
    order = crm_order(channel, status=6)
    PancakePosOrderLink.objects.create(order=order, pancake_order_id="P-103", status=OrderLinkStatus.PUSHED)
    run(service, order_payload(id="P-103", order_sources="-7", marketplace_id=None, status=2))
    assert partner(order)[0] == "Đã gửi hàng"
    assert order.status == 6  # KiotViet quyết định; lệch vào báo cáo


def test_don_tach_kien_hien_kien_cham_nhat(service, channel, run):
    order = crm_order(channel)
    PancakePosOrderLink.objects.create(order=order, part_no=1, pancake_order_id="K1", status=OrderLinkStatus.PUSHED)
    PancakePosOrderLink.objects.create(order=order, part_no=2, pancake_order_id="K2", status=OrderLinkStatus.PUSHED)
    base = {"order_sources": "-7", "marketplace_id": None}
    run(service, order_payload(id="K2", status=12, inserted_at="2026-09-29T20:07:00", **base))
    run(service, order_payload(id="K1", status=2, **base))
    assert partner(order)[0] == "Chờ in"  # kiện 2 còn chờ in → đơn chưa "đã gửi"
    assert order.shipping_code == ""  # đơn nhiều kiện không ghi một mã vận đơn
    run(service, order_payload(id="K2", status=2, inserted_at="2026-09-29T20:07:00",
                               updated_at="2026-09-30T00:00:00", **base))
    assert partner(order)[0] == "Đã gửi hàng"
    # Một kiện huỷ → bỏ qua kiện đó.
    run(service, order_payload(id="K1", status=6, updated_at="2026-09-30T01:00:00", **base))
    run(service, order_payload(id="K2", status=3, inserted_at="2026-09-29T20:07:00",
                               updated_at="2026-09-30T02:00:00", **base))
    assert partner(order)[0] == "Đã nhận"
    assert order.status == 1


# ── đơn gốc Pancake: khớp mã sàn / mã vận đơn ───────────────────────────────
def test_khop_ma_san_don_kiotviet_ghi_partner(service, channel, run):
    kv = crm_order(channel, order_source="shopee", invoice_code="HDSPE_260930BY9FBX6H",
                   order_code="DHSPE_260930BY9FBX6H", status=1)
    assert run(service, order_payload(status=2)) == "updated"
    assert partner(kv)[0] == "Đã gửi hàng"
    assert kv.status == 1  # trạng thái đơn vẫn do KiotViet
    link = PancakePosOrderLink.objects.get(pancake_order_id="260930BY9FBX6H")
    assert (link.order_id, link.origin, link.status) == (kv.id, OrderLinkOrigin.PANCAKE_NATIVE, OrderLinkStatus.LINKED)
    row = PancakePosInboundOrder.objects.get(pancake_order_id="260930BY9FBX6H")
    assert row.match_method == InboundMatchMethod.MARKETPLACE_CODE


def test_khop_ma_san_lan_sau_theo_lien_ket(service, channel, run):
    kv = crm_order(channel, order_source="shopee", order_code="DHSPE_260930BY9FBX6H", invoice_code="HDSPE_X")
    run(service, order_payload(status=3, updated_at="2026-09-30T01:00:00"))
    # Liên kết native đã có → sự kiện sau khớp theo liên kết, không tạo dòng thứ hai.
    run(service, order_payload(status=16, updated_at="2026-09-30T02:00:00"))
    assert PancakePosOrderLink.objects.filter(order=kv).count() == 1
    assert partner(kv)[0] == "Đã thu tiền"


@pytest.mark.parametrize("source", ["foxia", "cnv"])
def test_don_mirror_doi_tac_khong_bi_ghi_partner(service, channel, run, source):
    order = crm_order(channel, order_source=source, shipping_code="GHN777",
                      partner_delivery_status="đã chốt")
    run(service, order_payload(id="FX-1", order_sources="-1", marketplace_id=None, status=2,
                               partner={"extend_code": "GHN777", "partner_name": "Giao hàng nhanh"}))
    assert partner(order) == ("đã chốt", "")


def test_khop_ma_van_don(service, channel, run):
    order = crm_order(channel, order_source="messenger", shipping_code="GHN123")
    run(service, order_payload(id="FB-1", order_sources="-1", marketplace_id=None, status=2,
                               partner={"extend_code": "GHN123", "partner_name": "Giao hàng nhanh"}))
    row = PancakePosInboundOrder.objects.get(pancake_order_id="FB-1")
    assert (row.order_id, row.match_method) == (order.id, "tracking_code")
    order.refresh_from_db()
    assert (order.status, order.shipping_provider, order.partner_delivery_status) == (1, "GHN", "Đã gửi hàng")


def test_ma_khop_nhieu_don_vao_cho_khop_va_bao_lark(service, channel, alerts, run):
    a = crm_order(channel, shipping_code="DUP1")
    b = crm_order(channel, shipping_code="DUP1")
    assert run(service, order_payload(id="FB-2", order_sources="-1", marketplace_id=None,
                                      partner={"extend_code": "DUP1"})) == "unmatched"
    row = PancakePosInboundOrder.objects.get(pancake_order_id="FB-2")
    assert row.match_status == "unmatched" and row.candidates == [a.id, b.id]
    assert len(alerts.sent) == 1 and "FB-2" in alerts.sent[0]["title"]


def test_don_crm_da_day_ma_khac_la_nghi_trung(service, channel, alerts, run):
    order = crm_order(channel, shipping_code="GHN9")
    PancakePosOrderLink.objects.create(order=order, pancake_order_id="P-OTHER", status=OrderLinkStatus.PUSHED)
    run(service, order_payload(id="FB-3", order_sources="-1", marketplace_id=None, partner={"extend_code": "GHN9"}))
    row = PancakePosInboundOrder.objects.get(pancake_order_id="FB-3")
    assert row.match_status == "unmatched" and "trùng" in row.reason
    assert alerts.sent


# ── không khớp: chờ / quá hạn / tạo đơn ─────────────────────────────────────
def test_khong_khop_cho_roi_qua_han_vao_cho_khop(service, channel, alerts, run):
    assert run(service, order_payload(id="260930NEW", status=1)) == "waiting"
    assert not alerts.sent and Order.all_objects.count() == 0  # cờ tạo đơn tắt → không tạo
    row = PancakePosInboundOrder.objects.get(pancake_order_id="260930NEW")
    PancakePosInboundOrder.objects.filter(id=row.id).update(first_seen_at=timezone.now() - timedelta(hours=7))
    assert service.rematch() == {"unmatched": 1}
    row.refresh_from_db()
    assert row.match_status == "unmatched" and len(alerts.sent) == 1


def test_khop_lai_khi_don_kiotviet_ve_sau(service, channel, run):
    run(service, order_payload(id="260930LATE", status=2))
    kv = crm_order(channel, order_source="shopee", invoice_code="HDSPE_260930LATE")
    assert service.rematch() == {"updated": 1}
    assert PancakePosInboundOrder.objects.get(pancake_order_id="260930LATE").order_id == kv.id


def test_don_huy_chua_khop_thi_bo_qua(service, channel, run):
    assert run(service, order_payload(id="260930CXL", status=6)).startswith("ignored")
    assert PancakePosInboundOrder.objects.get(pancake_order_id="260930CXL").match_status == "ignored"


def test_tao_don_khi_bat_co(alerts, channel, run):
    service = make_service(alerts, create_orders=True, source_map={"-3": "shopee"})
    product = Product.objects.create(name="Máy massage", sku="DLX-01")
    other = Product.objects.create(name="Gối", sku="GOI-1")
    group = PancakePosProductLink.objects.create(group_key="g-goi", pancake_product_id="pp-goi")
    PancakePosVariationLink.objects.create(product=other, product_link=group, pancake_variation_id="var-goi")
    payload = order_payload(status=2, items=[
        {"variation_id": None, "quantity": 1, "discount_each_product": 1001000,
         "variation_info": {"display_id": "DLX-01", "name": "Máy massage", "retail_price": 3000000}},
        {"variation_id": "var-goi", "quantity": 2, "discount_each_product": 0,
         "variation_info": {"display_id": "", "name": "Gối", "retail_price": 100000}},
    ])
    assert run(service, payload) == "updated"
    created = Order.all_objects.get(external_order_id="260930BY9FBX6H")
    # Đơn tạo luôn "Chờ xử lý" — Pancake không quyết orders.status.
    assert (created.channel_id, created.order_source, created.status) == (channel.id, "shopee", 1)
    assert created.partner_delivery_status == "Đã gửi hàng"
    assert (created.shipping_code, created.shipping_provider) == ("SPXVN0001", "SPX")
    items = {i.product_id: (i.quantity, i.discount) for i in OrderItem.objects.filter(order=created)}
    assert items == {product.id: (1, 1001000), other.id: (2, 0)}
    link = PancakePosOrderLink.objects.get(pancake_order_id="260930BY9FBX6H")
    assert (link.order_id, link.origin) == (created.id, OrderLinkOrigin.PANCAKE_NATIVE)
    row = PancakePosInboundOrder.objects.get(pancake_order_id="260930BY9FBX6H")
    assert row.match_method == InboundMatchMethod.CREATED
    # Sự kiện sau của cùng đơn không tạo lần hai.
    run(service, order_payload(status=3, updated_at="2026-09-30T05:00:00"))
    assert Order.all_objects.filter(external_order_id="260930BY9FBX6H").count() == 1
    assert Order.all_objects.get(id=created.id).status == 1
    assert Order.all_objects.get(id=created.id).partner_delivery_status == "Đã nhận"


def test_tao_don_thieu_kenh_thi_cho(alerts, run):
    service = make_service(alerts, create_orders=True, source_map={"-3": "shopee"})
    assert run(service, order_payload(account_name="Shop lạ")) == "waiting"
    row = PancakePosInboundOrder.objects.get(pancake_order_id="260930BY9FBX6H")
    assert "kênh" in row.reason and Order.all_objects.count() == 0


def test_tao_don_kenh_theo_cau_hinh(alerts, channel, run):
    other = Channel.objects.create(code="x", name="Khác")
    service = make_service(alerts, create_orders=True, source_map={"-3": "shopee"},
                           channel_map={"922017502": other.id})
    run(service, order_payload())
    assert Order.all_objects.get(external_order_id="260930BY9FBX6H").channel_id == other.id


# ── lỗi, khớp tay ───────────────────────────────────────────────────────────
def test_loi_xu_ly_ghi_error_va_dem_luot(service, channel):
    event_id = event(order_payload(id="ERR-1"))
    with patch.object(SqlCrmOrderGateway, "find_by_codes", side_effect=RuntimeError("DB bận")):
        assert service.process_event(event_id) == "error"
    ev = PancakePosWebhookEvent.objects.get(id=event_id)
    assert (ev.status, ev.attempts) == (WebhookEventStatus.ERROR, 1) and "DB bận" in ev.error
    # Không để lại dòng inbound dở dang (savepoint rollback).
    assert not PancakePosInboundOrder.objects.filter(pancake_order_id="ERR-1").exists()
    assert service.process_event(event_id) == "waiting"  # lượt sau xử lý lại được
    assert PancakePosWebhookEvent.objects.get(id=event_id).attempts == 2


def test_khop_tay_va_bo_qua(service, channel, run, matched):
    run(service, order_payload(id="260930MAN", status=2))
    row = PancakePosInboundOrder.objects.get(pancake_order_id="260930MAN")
    order = crm_order(channel, order_source="shopee")
    service.resolve_manual(row.id, order_id=order.id)
    row.refresh_from_db()
    assert (row.order_id, row.match_status, row.match_method) == (order.id, "matched", InboundMatchMethod.MANUAL)
    assert partner(order)[0] == "Đã gửi hàng" and order.status == 1
    with pytest.raises(ValueError):
        service.resolve_manual(row.id, order_id=999999)
    other = crm_order(channel)
    with pytest.raises(ValueError):
        service.resolve_manual(row.id, order_id=other.id)  # đã liên kết đơn khác
    service.resolve_manual(row.id, order_id=None, ignore=True)
    assert PancakePosInboundOrder.objects.get(id=row.id).match_status == InboundMatchStatus.IGNORED
