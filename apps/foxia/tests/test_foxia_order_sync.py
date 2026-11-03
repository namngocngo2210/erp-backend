"""Đồng bộ đơn Foxia: upsert idempotent, enrich marketing, trạng thái thô, phí ship."""

from decimal import Decimal

import pytest

from apps.foxia.application.services.order_sync_service import FoxiaOrderSyncService
from apps.foxia.domain.order_specs import OrderSyncMode
from apps.foxia.models import (
    FoxiaBranch,
    FoxiaBrand,
    FoxiaChannel,
    FoxiaOrderLine,
    FoxiaOrderLink,
    FoxiaShipment,
    FoxiaSource,
    FoxiaStage,
    FoxiaSyncCursor,
    FoxiaSyncRun,
    FoxiaSyncStatus,
    FoxiaUser,
)
from apps.orders.models import Order, OrderSource, OrderStatus

pytestmark = pytest.mark.django_db


class FakeFoxiaClient:
    """Trả dữ liệu đúng hình dạng tài liệu Foxia mô tả (many2one = [id, "tên"])."""

    def __init__(self, orders=None, leads=None, lines=None, shipments=None):
        self.data = {
            "sale.order": orders or [],
            "cohoi": leads or [],
            "sale.order.line": lines or [],
            "donvanchuyen": shipments or [],
        }
        self.requests: list[tuple] = []

    def iter_all(self, resource, conditions=None, fields=None, order="id asc", page_size=500):
        self.requests.append((resource, conditions))
        rows = self._match(resource, conditions)
        for start in range(0, len(rows), page_size):
            yield rows[start : start + page_size]

    def search_read(self, resource, conditions=None, fields=None, offset=0, limit=500, order=""):
        self.requests.append((resource, conditions))
        return self._match(resource, conditions)[offset : offset + limit]

    def _match(self, resource, conditions):
        rows = self.data.get(resource, [])
        for cond in conditions or []:
            field, op, value = cond
            if op == "in":
                rows = [r for r in rows if _raw_id(r.get(field)) in set(value)]
        return rows


def _raw_id(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


@pytest.fixture
def catalog():
    branch = FoxiaBranch.objects.create(foxia_id=12, name="CN Hà Nội")
    marketer = FoxiaUser.objects.create(foxia_id=100, login="mkt.an", name="An")
    sale = FoxiaUser.objects.create(foxia_id=200, login="sale.binh", name="Bình")
    stage = FoxiaStage.objects.create(foxia_id=5, name="Đã chốt", phanloai="confirmed")
    channel = FoxiaChannel.objects.create(foxia_id=7, name="Facebook")
    brand = FoxiaBrand.objects.create(foxia_id=9, name="Delax")
    source = FoxiaSource.objects.create(foxia_id=55, name="FB-LP-01", channel=channel, brand=brand)
    return {
        "branch": branch, "marketer": marketer, "sale": sale,
        "stage": stage, "channel": channel, "brand": brand, "source": source,
    }


def _order_row(**overrides):
    row = {
        "id": 73102,
        "name": "SO0001",
        "cohoi_id": [900, "Lead 900"],
        "ngaytao": "2026-07-20 03:00:00",
        "ngayxacnhan": "2026-07-27 17:00:00",
        "ngayhoanthanh": False,
        "nhanvienmarketing_id": [100, "An"],
        "user_id": [200, "Bình"],
        "team_id": False,
        "congtycon_id": [12, "CN Hà Nội"],
        "giaidoan_id": [5, "Đã chốt"],
        "phanloaigiaidoan": "confirmed",
        "amount_total": 1500000,
        "tongsauchietkhaubosung": 1400000,
        "tongtiendathu": 1400000,
        "trangthaihoantra": False,
    }
    row.update(overrides)
    return row


def _lead_row(**overrides):
    row = {
        "id": 900,
        "nguonkhachhang_id": [55, "FB-LP-01"],
        "kenhmarketing_id": [7, "Facebook"],
        "thuonghieu_id": [9, "Delax"],
        "nhanvienmarketing_id": [100, "An"],
    }
    row.update(overrides)
    return row


def _sync(client, mode=OrderSyncMode.CONFIRMED):
    return FoxiaOrderSyncService(client).sync(mode)


# ── upsert đơn ───────────────────────────────────────────────────────────────


def test_creates_order_with_foxia_source_and_link(catalog):
    result = _sync(FakeFoxiaClient(orders=[_order_row()], leads=[_lead_row()]))

    assert result.created == 1
    order = Order.all_objects.get()
    assert order.order_source == OrderSource.FOXIA
    # `status` không mang ý nghĩa cho đơn mirror — trạng thái thật nằm ở cột thô.
    assert order.status == OrderStatus.NEW
    link = FoxiaOrderLink.objects.get()
    assert link.foxia_id == 73102
    assert link.order_id == order.pk
    assert link.amount_collected == Decimal("1400000")


def test_sync_twice_does_not_duplicate(catalog):
    client = FakeFoxiaClient(orders=[_order_row()], leads=[_lead_row()])
    _sync(client)
    _sync(client)

    assert Order.all_objects.count() == 1
    assert FoxiaOrderLink.objects.count() == 1


def test_second_sync_updates_changed_fields(catalog):
    client = FakeFoxiaClient(orders=[_order_row()], leads=[_lead_row()])
    _sync(client)

    client.data["sale.order"] = [_order_row(tongtiendathu=1450000, giaidoan_id=[5, "Đã giao"])]
    result = _sync(client)

    assert result.updated == 1
    assert FoxiaOrderLink.objects.get().amount_collected == Decimal("1450000")
    assert Order.all_objects.get().partner_delivery_status == "đã giao"


def test_mirror_order_uses_sentinel_customer_and_channel(catalog):
    _sync(FakeFoxiaClient(orders=[_order_row()], leads=[_lead_row()]))

    order = Order.all_objects.select_related("customer", "channel").get()
    # Foxia không cấp dữ liệu khách hàng — sentinel theo chi nhánh, không phải
    # mỗi đơn một khách rỗng.
    assert order.customer.code == "FOXIA-12"
    assert order.channel.code == "foxia"
    assert order.channel.is_active is False


def test_two_orders_same_branch_share_one_sentinel(catalog):
    orders = [_order_row(), _order_row(id=73103, name="SO0002")]
    _sync(FakeFoxiaClient(orders=orders, leads=[_lead_row()]))

    from apps.customers.models import Customer

    assert Order.all_objects.count() == 2
    assert Customer.objects.filter(code="FOXIA-12").count() == 1


# ── enrich chiều marketing ───────────────────────────────────────────────────


def test_marketing_dimensions_come_from_lead(catalog):
    """Đơn KHÔNG mang nguồn/kênh/thương hiệu — chúng chỉ có trên `cohoi`."""
    _sync(FakeFoxiaClient(orders=[_order_row()], leads=[_lead_row()]))

    link = FoxiaOrderLink.objects.get()
    assert link.source.foxia_id == 55
    assert link.channel.foxia_id == 7
    assert link.brand.foxia_id == 9
    assert link.marketer.login == "mkt.an"
    assert link.salesperson.login == "sale.binh"


def test_lead_is_fetched_by_id_not_by_date(catalog):
    """Enrich là tra theo danh sách id, KHÔNG phải đồng bộ lead theo ngày."""
    client = FakeFoxiaClient(orders=[_order_row()], leads=[_lead_row()])
    _sync(client)

    cohoi_requests = [cond for res, cond in client.requests if res == "cohoi"]
    assert cohoi_requests == [[["id", "in", [900]]]]


def test_order_survives_when_lead_lookup_fails(catalog):
    """Mất chiều marketing thì báo cáo nghèo đi; mất cả lô đơn thì tệ hơn nhiều."""

    class BrokenLeadClient(FakeFoxiaClient):
        def search_read(self, resource, **kwargs):
            if resource == "cohoi":
                raise RuntimeError("Foxia 500")
            return super().search_read(resource, **kwargs)

    _sync(BrokenLeadClient(orders=[_order_row()], leads=[_lead_row()]))

    link = FoxiaOrderLink.objects.get()
    assert link.source is None
    assert Order.all_objects.count() == 1


# ── trạng thái ───────────────────────────────────────────────────────────────


def test_two_status_columns_do_not_mix_sources(catalog):
    shipments = [
        {
            "id": 5001, "name": "VD001", "donhang_id": [73102, "SO0001"],
            "trangthai": "Giao thành công", "phivanchuyenthucte": 30000,
            "ngayhoanthanhdon": "2026-07-30 10:00:00", "ngayhoantradon": False,
        }
    ]
    _sync(FakeFoxiaClient(orders=[_order_row()], leads=[_lead_row()], shipments=shipments))

    order = Order.all_objects.get()
    # Foxia nói "đã chốt", DVVC nói "giao thành công" — đúng loại lệch cần giữ
    # lại được, gộp một cột là mất.
    assert order.partner_delivery_status == "đã chốt"
    assert order.partner_dvvc_delivery_status == "giao thành công"


def test_latest_shipment_wins_for_carrier_status(catalog):
    shipments = [
        {"id": 5001, "donhang_id": [73102, "SO0001"], "trangthai": "Đang giao",
         "phivanchuyenthucte": 30000, "name": "VD1"},
        {"id": 5002, "donhang_id": [73102, "SO0001"], "trangthai": "Đã nhận hoàn",
         "phivanchuyenthucte": 25000, "name": "VD2"},
    ]
    _sync(FakeFoxiaClient(orders=[_order_row()], leads=[_lead_row()], shipments=shipments))

    assert Order.all_objects.get().partner_dvvc_delivery_status == "đã nhận hoàn"


def test_unknown_stage_is_stored_and_reported_not_dropped(catalog):
    row = _order_row(giaidoan_id=[999, "Trạng thái lạ chưa có trong danh mục"])
    result = _sync(FakeFoxiaClient(orders=[row], leads=[_lead_row()]))

    order = Order.all_objects.get()
    assert order.partner_delivery_status == "trạng thái lạ chưa có trong danh mục"
    assert result.unknown_statuses
    run = FoxiaSyncRun.objects.filter(resource=OrderSyncMode.CONFIRMED).first()
    assert "chưa có trong danh mục" in run.error_message


# ── dòng sản phẩm & phí ship ─────────────────────────────────────────────────


def test_lines_are_stored_without_touching_order_items(catalog):
    lines = [
        {"id": 1, "order_id": [73102, "SO0001"], "product_id": [77, "Máy A"],
         "product_uom_qty": 2, "price_subtotal": 700000},
        {"id": 2, "order_id": [73102, "SO0001"], "product_id": [78, "Máy B"],
         "product_uom_qty": 1, "price_subtotal": 700000},
    ]
    _sync(FakeFoxiaClient(orders=[_order_row()], leads=[_lead_row()], lines=lines))

    assert FoxiaOrderLine.objects.count() == 2
    # OrderItem.product là FK PROTECT NOT NULL còn dòng Foxia không có SKU —
    # ép khớp là đoán, mà đoán sai thì doanh thu gán nhầm sản phẩm.
    assert Order.all_objects.get().items.count() == 0
    assert "Máy A" in Order.all_objects.get().product


def test_shipping_fee_sums_every_shipment(catalog):
    shipments = [
        {"id": 5001, "donhang_id": [73102, "SO0001"], "trangthai": "Giao lại",
         "phivanchuyenthucte": 30000, "name": "VD1"},
        {"id": 5002, "donhang_id": [73102, "SO0001"], "trangthai": "Đã nhận hoàn",
         "phivanchuyenthucte": 25000, "name": "VD2"},
    ]
    _sync(FakeFoxiaClient(orders=[_order_row()], leads=[_lead_row()], shipments=shipments))

    # Giao lại rồi hoàn là hai lần trả phí — shipping_fee là chi phí THỰC.
    assert Order.all_objects.get().shipping_fee == Decimal("55000")
    assert FoxiaShipment.objects.count() == 2


def test_shipping_fee_is_recomputed_not_accumulated(catalog):
    shipments = [
        {"id": 5001, "donhang_id": [73102, "SO0001"], "trangthai": "Đang giao",
         "phivanchuyenthucte": 30000, "name": "VD1"},
    ]
    client = FakeFoxiaClient(orders=[_order_row()], leads=[_lead_row()], shipments=shipments)
    _sync(client)
    _sync(client)

    # Chạy lại đồng bộ không được nhân đôi con số.
    assert Order.all_objects.get().shipping_fee == Decimal("30000")


def test_order_without_shipment_keeps_zero_fee(catalog):
    _sync(FakeFoxiaClient(orders=[_order_row()], leads=[_lead_row()]))

    assert Order.all_objects.get().shipping_fee == Decimal("0")


# ── cửa sổ quét & cursor ─────────────────────────────────────────────────────


def test_confirmed_and_delivered_use_different_date_fields(catalog):
    client = FakeFoxiaClient(orders=[_order_row()], leads=[_lead_row()])
    _sync(client, OrderSyncMode.CONFIRMED)
    _sync(client, OrderSyncMode.DELIVERED)

    order_conditions = [cond for res, cond in client.requests if res == "sale.order"]
    assert order_conditions[0][0][0] == "ngayxacnhan"
    assert order_conditions[1][0][0] == "ngayhoanthanh"


def test_cursor_advances_only_on_success(catalog):
    client = FakeFoxiaClient(orders=[_order_row()], leads=[_lead_row()])
    _sync(client)
    first = FoxiaSyncCursor.objects.get(resource=OrderSyncMode.CONFIRMED).synced_until

    class ExplodingClient(FakeFoxiaClient):
        def iter_all(self, resource, **kwargs):
            if resource == "sale.order":
                raise RuntimeError("Foxia 500")
            yield from super().iter_all(resource, **kwargs)

    with pytest.raises(RuntimeError):
        _sync(ExplodingClient(orders=[_order_row()]))

    assert FoxiaSyncCursor.objects.get(resource=OrderSyncMode.CONFIRMED).synced_until == first
    assert FoxiaSyncRun.objects.filter(status=FoxiaSyncStatus.FAILED).exists()


def test_window_condition_is_sent_as_utc_string(catalog):
    client = FakeFoxiaClient(orders=[_order_row()], leads=[_lead_row()])
    _sync(client)

    conditions = next(cond for res, cond in client.requests if res == "sale.order")
    # Foxia nhận chuỗi UTC "YYYY-MM-DD HH:MM:SS", không có ký hiệu múi giờ.
    assert conditions[0][1] == ">="
    assert len(conditions[0][2]) == 19 and "T" not in conditions[0][2]
