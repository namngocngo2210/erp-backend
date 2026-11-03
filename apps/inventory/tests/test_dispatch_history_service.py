"""Test gộp theo SKU của DispatchHistoryService (view Theo SKU)."""
from __future__ import annotations

import pytest

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.dispatch_history_service import (
    DispatchHistoryService,
)
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import MovementType, PendingShipmentScan
from apps.orders.models import Order
from apps.catalog.models import Product
from apps.users.models import User
from apps.warehouses.models import Warehouse
from datetime import UTC

pytestmark = pytest.mark.django_db


def _seed_two_orders_one_sku():
    ch = Channel.objects.create(code="lp-sku", name="SKU")
    cust = Customer.objects.create(
        full_name="K", phone_raw="0900000009", phone_normalized="0900000009"
    )
    wh = Warehouse.objects.create(code="WH-SKU", name="Kho A")
    p1 = Product.objects.create(sku="SKU1", name="Áo")
    p2 = Product.objects.create(sku="SKU2", name="Quần")
    o1 = Order.objects.create(
        channel=ch, customer=cust, name="K", phone="0900000009",
        order_code="DH001", invoice_code="HD001", shipping_code="VD001",
        total_amount="1000",
    )
    o2 = Order.objects.create(
        channel=ch, customer=cust, name="K", phone="0900000009",
        order_code="DH002", invoice_code="HD002", shipping_code="VD002",
        total_amount="1000",
    )
    # SKU1 xuất ở cả 2 đơn (3 + 5), hoàn 1 ở đơn 1 → net = 8 - 1 = 7, order_count = 2
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=-3,
        movement_type=MovementType.SALE, reference_type="order", reference_id=o1.id,
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=1,
        movement_type=MovementType.SALE_REVERSAL, reference_type="order", reference_id=o1.id,
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=-5,
        movement_type=MovementType.SALE, reference_type="order", reference_id=o2.id,
    )
    # SKU2 chỉ ở đơn 2 (xuất 2)
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p2.id, qty_delta=-2,
        movement_type=MovementType.SALE, reference_type="order", reference_id=o2.id,
    )
    return wh, o1, o2, p1, p2


def test_skus_page_groups_by_sku():
    _seed_two_orders_one_sku()
    data = DispatchHistoryService.skus_page(pagination={"limit": 50, "offset": 0})
    assert data["total"] == 2  # SKU1, SKU2
    by_sku = {r["sku"]: r for r in data["items"]}
    assert by_sku["SKU1"]["order_count"] == 2
    assert by_sku["SKU1"]["net"] == 7
    assert by_sku["SKU2"]["order_count"] == 1
    assert by_sku["SKU2"]["net"] == 2
    # total_quantity = tổng net toàn bộ = 7 + 2 = 9
    assert data["total_quantity"] == 9


def test_sku_orders_drilldown_lists_orders():
    wh, o1, o2, p1, p2 = _seed_two_orders_one_sku()
    rows = DispatchHistoryService.sku_orders(product_id=p1.id)
    by_order = {r["order_id"]: r for r in rows}
    assert set(by_order) == {o1.id, o2.id}
    assert by_order[o1.id]["net"] == 2  # 3 - 1
    assert by_order[o2.id]["net"] == 5
    assert by_order[o2.id]["invoice_code"] == "HD002"


def test_skus_page_product_filter():
    _seed_two_orders_one_sku()
    data = DispatchHistoryService.skus_page(
        pagination={"limit": 50, "offset": 0}, product="SKU2"
    )
    assert data["total"] == 1
    assert data["items"][0]["sku"] == "SKU2"


def test_orders_page_includes_sku_lines():
    _, o1, o2, p1, p2 = _seed_two_orders_one_sku()
    data = DispatchHistoryService.orders_page(pagination={"limit": 50, "offset": 0})
    by_order = {i["order_id"]: i for i in data["items"]}

    # Đơn 1: SKU1 xuất 3, hoàn 1 → qty là NET = 2, gross giữ nguyên 3 đã rời kho
    assert by_order[o1.id]["lines"] == [
        {"product_id": p1.id, "sku": "SKU1", "qty": 2, "gross": 3}
    ]
    # Đơn 2: 2 SKU, sắp xếp SL giảm dần (SKU1=5 trước SKU2=2)
    assert by_order[o2.id]["lines"] == [
        {"product_id": p1.id, "sku": "SKU1", "qty": 5, "gross": 5},
        {"product_id": p2.id, "sku": "SKU2", "qty": 2, "gross": 2},
    ]


def test_orders_page_performed_by_uses_latest_movement_user():
    wh, o1, o2, *_ = _seed_two_orders_one_sku()  # movement seed không có user
    p3 = Product.objects.create(sku="SKU3", name="Váy")
    scanner = User.objects.create_user(
        username="scanner", password="x", display_name="Nam Kho"
    )
    # Đơn 1 có thêm movement MỚI NHẤT gắn user → lấy tên user đó.
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p3.id, qty_delta=-1,
        movement_type=MovementType.SALE, reference_type="order", reference_id=o1.id,
        user_id=scanner.id,
    )
    data = DispatchHistoryService.orders_page(pagination={"limit": 50, "offset": 0})
    by_order = {i["order_id"]: i for i in data["items"]}
    assert by_order[o1.id]["performed_by"] == "Nam Kho"
    # Đơn 2 chỉ có movement không user → chuỗi rỗng, không lỗi.
    assert by_order[o2.id]["performed_by"] == ""


def test_orders_page_lines_respect_product_filter():
    _seed_two_orders_one_sku()
    data = DispatchHistoryService.orders_page(
        pagination={"limit": 50, "offset": 0}, product="SKU2"
    )
    items = data["items"]
    assert len(items) == 1
    assert [line["sku"] for line in items[0]["lines"]] == ["SKU2"]


def _cancel_revive_pair(wh, order, product, qty, *, at):
    """Mô phỏng KV hủy-xuất lại hoá đơn (X → X.01) tại thời điểm ``at``:
    release hoàn tồn + reissue trừ lại — cặp bút toán hành chính net 0."""
    from apps.inventory.application.services.stock_sales_service import (
        CANCEL_RELEASE_NOTE,
        REVIVE_REISSUE_NOTE,
    )
    from apps.inventory.models import StockMovement

    before = set(StockMovement.objects.values_list("id", flat=True))
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=product.id, qty_delta=qty,
        movement_type=MovementType.SALE_REVERSAL, reference_type="order",
        reference_id=order.id, note=CANCEL_RELEASE_NOTE,
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=product.id, qty_delta=-qty,
        movement_type=MovementType.SALE, reference_type="order",
        reference_id=order.id, note=REVIVE_REISSUE_NOTE,
    )
    StockMovement.objects.exclude(id__in=before).update(created_at=at, occurred_at=at)


def _seed_dispatch_then_revive():
    """Đơn xuất kho ngày 14, bị hủy-xuất lại (.01) ngày 17."""
    from datetime import datetime

    from apps.inventory.models import StockMovement

    ch = Channel.objects.create(code="lp-revive", name="Revive")
    cust = Customer.objects.create(
        full_name="K", phone_raw="0900000010", phone_normalized="0900000010"
    )
    wh = Warehouse.objects.create(code="WH-RV", name="Kho A")
    p = Product.objects.create(sku="SKURV", name="Áo")
    o = Order.objects.create(
        channel=ch, customer=cust, name="K", phone="0900000010",
        order_code="DH010", invoice_code="HD010.01", shipping_code="VD010",
        total_amount="1000",
    )
    dispatched_at = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    revived_at = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p.id, qty_delta=-3,
        movement_type=MovementType.SALE, reference_type="order", reference_id=o.id,
    )
    StockMovement.objects.filter(reference_id=o.id).update(
        created_at=dispatched_at, occurred_at=dispatched_at
    )
    _cancel_revive_pair(wh, o, p, 3, at=revived_at)
    return wh, o, p, dispatched_at, revived_at


def test_orders_page_last_at_keeps_real_dispatch_date_after_revive():
    """KV hủy-xuất lại (X → X.01) không được làm ngày xuất nhảy sang ngày hồi sinh."""
    _, o, _, dispatched_at, _ = _seed_dispatch_then_revive()
    data = DispatchHistoryService.orders_page(pagination={"limit": 50, "offset": 0})
    by_order = {i["order_id"]: i for i in data["items"]}
    assert by_order[o.id]["net"] == 3
    assert by_order[o.id]["last_at"] == dispatched_at


def test_skus_page_last_at_keeps_real_dispatch_date_after_revive():
    _, o, p, dispatched_at, _ = _seed_dispatch_then_revive()
    data = DispatchHistoryService.skus_page(pagination={"limit": 50, "offset": 0})
    by_sku = {r["sku"]: r for r in data["items"]}
    assert by_sku["SKURV"]["net"] == 3
    assert by_sku["SKURV"]["last_at"] == dispatched_at


def test_sku_orders_last_at_keeps_real_dispatch_date_after_revive():
    _, o, p, dispatched_at, _ = _seed_dispatch_then_revive()
    rows = DispatchHistoryService.sku_orders(product_id=p.id)
    by_order = {r["order_id"]: r for r in rows}
    assert by_order[o.id]["last_at"] == dispatched_at


def test_orders_page_last_at_falls_back_when_only_admin_movements_in_range():
    """Lọc đúng ngày hồi sinh: nhóm chỉ còn cặp bút toán hành chính (net 0) —
    last_at không được null (giữ Max(created_at) làm fallback)."""
    _, o, _, _, revived_at = _seed_dispatch_then_revive()
    data = DispatchHistoryService.orders_page(
        pagination={"limit": 50, "offset": 0},
        date_from="2026-07-17", date_to="2026-07-17",
    )
    by_order = {i["order_id"]: i for i in data["items"]}
    assert by_order[o.id]["net"] == 0
    assert by_order[o.id]["last_at"] == revived_at


def _seed_dispatch_then_transfer_to_sibling():
    """Đơn gốc xuất kho 14/7, huỷ HÔM NAY và chuyển sổ sang đơn `.01`.

    Khác ca hồi sinh: đơn `.01` là BẢN GHI ĐƠN KHÁC, không có bút toán SALE thật
    nào để `_dispatch_last_at` bám vào — ngày phải nằm sẵn trên chính bút toán
    chuyển sổ.
    """
    from datetime import datetime

    from apps.inventory.application.services.stock_sales_service import (
        StockSalesService,
    )
    from apps.inventory.models import StockMovement

    ch = Channel.objects.create(code="lp-sib", name="Sibling")
    cust = Customer.objects.create(
        full_name="K", phone_raw="0900000011", phone_normalized="0900000011"
    )
    wh = Warehouse.objects.create(code="WH-SB", name="Kho A")
    p = Product.objects.create(sku="SKUSB", name="Áo")
    common = dict(
        channel=ch, customer=cust, name="K", phone="0900000011",
        order_code="DH011", shipping_code="VD011", total_amount="1000",
    )
    old = Order.objects.create(invoice_code="HD011", **common)
    new = Order.objects.create(invoice_code="HD011.01", **common)
    dispatched_at = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p.id, qty_delta=-3,
        movement_type=MovementType.SALE, reference_type="order", reference_id=old.id,
    )
    StockMovement.objects.filter(reference_type="order", reference_id=old.id).update(
        created_at=dispatched_at, occurred_at=dispatched_at
    )
    StockSalesService.transfer_issue_to_sibling(
        order_id=old.id, sibling_order_id=new.id
    )
    return wh, old, new, p, dispatched_at


def test_sibling_order_keeps_real_dispatch_date_in_orders_page():
    """Ca prod HD013469 → HD013469.01: ngày xuất phải là ngày hàng rời kho."""
    _, _, new, _, dispatched_at = _seed_dispatch_then_transfer_to_sibling()
    data = DispatchHistoryService.orders_page(
        pagination={"limit": 50, "offset": 0},
        date_from="2026-07-14", date_to="2026-07-14",
    )
    by_order = {i["order_id"]: i for i in data["items"]}
    assert by_order[new.id]["net"] == 3
    assert by_order[new.id]["last_at"] == dispatched_at


def test_sibling_order_absent_from_the_day_the_ledger_moved():
    """Không chỉ sai cột ngày: lọc theo ngày chuyển sổ cũng không được thấy đơn."""
    from django.utils import timezone

    _, _, new, _, _ = _seed_dispatch_then_transfer_to_sibling()
    today = timezone.localdate().isoformat()
    data = DispatchHistoryService.orders_page(
        pagination={"limit": 50, "offset": 0}, date_from=today, date_to=today
    )
    assert new.id not in {i["order_id"] for i in data["items"]}


def test_resync_movements_do_not_shift_last_at():
    """Sửa items sau xuất kho (resync) cũng là bút toán hành chính — không đổi ngày xuất."""
    from datetime import datetime

    from apps.inventory.application.services.stock_sales_service import RESYNC_NOTE
    from apps.inventory.models import StockMovement

    wh, o, p, dispatched_at, _ = _seed_dispatch_then_revive()
    before = set(StockMovement.objects.values_list("id", flat=True))
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p.id, qty_delta=-2,
        movement_type=MovementType.SALE, reference_type="order", reference_id=o.id,
        note=RESYNC_NOTE,
    )
    resync_at = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
    StockMovement.objects.exclude(id__in=before).update(
        created_at=resync_at, occurred_at=resync_at
    )
    data = DispatchHistoryService.orders_page(pagination={"limit": 50, "offset": 0})
    by_order = {i["order_id"]: i for i in data["items"]}
    assert by_order[o.id]["net"] == 5
    assert by_order[o.id]["last_at"] == dispatched_at


# ── Số lượng hiển thị luôn dương ─────────────────────────────────────────────
# Đơn huỷ đã chuyển sổ sang đơn `.NN` có net = 0 (xuất 3, đảo 3). Màn Lịch sử
# nhập xuất không được hiện số 0 — cần số ĐÃ TỪNG XUẤT (gộp, luôn dương) đi kèm
# để FE lấy làm giá trị thay thế.


def test_orders_page_exposes_gross_quantity_for_fully_reversed_order():
    _, old, _, _, _ = _seed_dispatch_then_transfer_to_sibling()
    data = DispatchHistoryService.orders_page(pagination={"limit": 50, "offset": 0})
    row = {i["order_id"]: i for i in data["items"]}[old.id]
    assert row["net"] == 0
    assert row["dispatched"] == 3
    assert [line["qty"] for line in row["lines"]] == [0]
    assert [line["gross"] for line in row["lines"]] == [3]


def test_sku_orders_drilldown_exposes_gross_quantity():
    _, old, _, p, _ = _seed_dispatch_then_transfer_to_sibling()
    rows = {r["order_id"]: r for r in DispatchHistoryService.sku_orders(product_id=p.id)}
    assert rows[old.id]["net"] == 0
    assert rows[old.id]["dispatched"] == 3


# ── Huỷ đơn KHÔNG phải một lần xuất ──────────────────────────────────────────
# Ca prod 999800020077015: xuất 23/7, huỷ 27/7 để hạ COD rồi xuất lại `.01`.
# Lọc đúng ngày 27/7, khoảng lọc chỉ chứa bút toán đảo do huỷ → trước đây
# `_dispatch_last_at` rơi vào fallback và dựng một dòng "xuất" mang ngày huỷ,
# SL âm (-2). Đảo sổ là nghiệp vụ hoàn tồn, không phải hàng rời kho.


def _seed_dispatch_then_cancel():
    from datetime import datetime

    from apps.inventory.application.services.stock_sales_service import (
        CANCEL_RELEASE_NOTE,
    )
    from apps.inventory.models import StockMovement

    ch = Channel.objects.create(code="lp-cxl", name="Cancel")
    cust = Customer.objects.create(
        full_name="K", phone_raw="0900000012", phone_normalized="0900000012"
    )
    wh = Warehouse.objects.create(code="WH-CXL", name="Kho A")
    p = Product.objects.create(sku="SKUCXL", name="Áo")
    o = Order.objects.create(
        channel=ch, customer=cust, name="K", phone="0900000012",
        order_code="DH012", invoice_code="HD012", shipping_code="VD012",
        total_amount="1000",
    )
    dispatched_at = datetime(2026, 7, 23, 9, 41, tzinfo=UTC)
    cancelled_at = datetime(2026, 7, 27, 15, 35, tzinfo=UTC)
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p.id, qty_delta=-2,
        movement_type=MovementType.SALE, reference_type="order", reference_id=o.id,
    )
    StockMovement.objects.filter(reference_type="order", reference_id=o.id).update(
        created_at=dispatched_at, occurred_at=dispatched_at
    )
    before = set(StockMovement.objects.values_list("id", flat=True))
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p.id, qty_delta=2,
        movement_type=MovementType.SALE_REVERSAL, reference_type="order",
        reference_id=o.id, note=CANCEL_RELEASE_NOTE,
    )
    StockMovement.objects.exclude(id__in=before).update(
        created_at=cancelled_at, occurred_at=cancelled_at
    )
    return wh, o, p, dispatched_at


def test_cancel_day_has_no_issue_row():
    _, o, _, _ = _seed_dispatch_then_cancel()
    data = DispatchHistoryService.orders_page(
        pagination={"limit": 50, "offset": 0},
        date_from="2026-07-27", date_to="2026-07-27",
    )
    assert data["items"] == []
    assert data["total"] == 0
    # Tile "Tổng SL xuất" của ngày huỷ không được ra số âm.
    assert data["total_quantity"] == 0


def test_dispatch_day_still_lists_the_order():
    _, o, _, dispatched_at = _seed_dispatch_then_cancel()
    data = DispatchHistoryService.orders_page(
        pagination={"limit": 50, "offset": 0},
        date_from="2026-07-23", date_to="2026-07-23",
    )
    row = {i["order_id"]: i for i in data["items"]}[o.id]
    assert row["last_at"] == dispatched_at
    assert row["dispatched"] == 2


def test_cancel_day_has_no_sku_row():
    _, _, p, _ = _seed_dispatch_then_cancel()
    data = DispatchHistoryService.skus_page(
        pagination={"limit": 50, "offset": 0},
        date_from="2026-07-27", date_to="2026-07-27",
    )
    assert data["items"] == []
    assert data["total_quantity"] == 0


# ── Xuất rồi huỷ trong CÙNG kỳ vẫn là một lần xuất ───────────────────────────
# Ca prod HDTTS_585205879163881436: xuất 09:22 27/7 rồi huỷ ngay trong ngày.
# Tồn về 0 là đúng, nhưng sản lượng xuất của ngày đó phải ghi nhận 1 — nếu không
# dòng hiện SL 1 mà footer tổng lại ra 0, mâu thuẫn ngay trên một màn hình.


def _seed_dispatch_and_cancel_same_day():
    from datetime import datetime

    from apps.inventory.application.services.stock_sales_service import (
        CANCEL_RELEASE_NOTE,
    )
    from apps.inventory.models import StockMovement

    ch = Channel.objects.create(code="lp-sd", name="SameDay")
    cust = Customer.objects.create(
        full_name="K", phone_raw="0900000013", phone_normalized="0900000013"
    )
    wh = Warehouse.objects.create(code="WH-SD", name="Kho A")
    p = Product.objects.create(sku="SKUSD", name="Áo")
    o = Order.objects.create(
        channel=ch, customer=cust, name="K", phone="0900000013",
        order_code="DH013", invoice_code="HD013", shipping_code="VD013",
        total_amount="1000",
    )
    issued_at = datetime(2026, 7, 27, 2, 22, tzinfo=UTC)
    cancelled_at = datetime(2026, 7, 27, 8, 0, tzinfo=UTC)
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p.id, qty_delta=-1,
        movement_type=MovementType.SALE, reference_type="order", reference_id=o.id,
    )
    StockMovement.objects.filter(reference_type="order", reference_id=o.id).update(
        created_at=issued_at, occurred_at=issued_at
    )
    before = set(StockMovement.objects.values_list("id", flat=True))
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p.id, qty_delta=1,
        movement_type=MovementType.SALE_REVERSAL, reference_type="order",
        reference_id=o.id, note=CANCEL_RELEASE_NOTE,
    )
    StockMovement.objects.exclude(id__in=before).update(
        created_at=cancelled_at, occurred_at=cancelled_at
    )
    return wh, o, p


def test_dispatched_then_cancelled_same_day_counts_as_issued():
    _, o, _ = _seed_dispatch_and_cancel_same_day()
    data = DispatchHistoryService.orders_page(
        pagination={"limit": 50, "offset": 0},
        date_from="2026-07-27", date_to="2026-07-27",
    )
    row = {i["order_id"]: i for i in data["items"]}[o.id]
    assert row["net"] == 0
    assert row["dispatched"] == 1
    # Footer/tile phải khớp con số dòng đang hiện.
    assert data["total_quantity"] == 1


# ── Dòng SKU phải nói cùng con số với phần bung dòng ─────────────────────────
# Ca prod BNC02 ngày 12/8 (kho Đại Mỗ 1): dòng SKU hiện "11 đơn · 9 chiếc" trong
# khi bung dòng ra đúng 10 đơn × 1 và file Excel theo đơn cũng ra 10. Đơn thứ 11
# xuất từ NGÀY KHÁC, trong kỳ lọc chỉ còn bút toán ĐẢO (huỷ đơn): `sku_orders`
# loại nó theo `_HAS_REAL_SALE` per-đơn, còn `sku_groups` gộp thẳng theo product
# nên bút toán đảo đó vừa +1 vào "Số đơn" vừa −1 vào "SL xuất".


def _add_order_with_sale(wh, p, *, code: str, qty: int, at):
    """Thêm một đơn xuất `qty` sản phẩm `p` tại thời điểm `at`."""
    from apps.inventory.models import StockMovement

    ch = Channel.objects.create(code=f"lp-{code}", name=code)
    cust = Customer.objects.create(
        full_name="K", phone_raw=f"09000{code[-5:]}", phone_normalized=f"09000{code[-5:]}"
    )
    o = Order.objects.create(
        channel=ch, customer=cust, name="K", phone=f"09000{code[-5:]}",
        order_code=f"DH{code}", invoice_code=f"HD{code}", shipping_code=f"VD{code}",
        total_amount="1000",
    )
    before = set(StockMovement.objects.values_list("id", flat=True))
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p.id, qty_delta=-qty,
        movement_type=MovementType.SALE, reference_type="order", reference_id=o.id,
    )
    StockMovement.objects.exclude(id__in=before).update(created_at=at, occurred_at=at)
    return o


def test_cancel_of_older_dispatch_does_not_inflate_sku_row():
    from datetime import datetime

    wh, _, p, _ = _seed_dispatch_then_cancel()  # xuất 23/7, huỷ 27/7
    other = _add_order_with_sale(
        wh, p, code="90001", qty=1,
        at=datetime(2026, 7, 27, 3, 0, tzinfo=UTC),
    )
    window = {"date_from": "2026-07-27", "date_to": "2026-07-27"}

    data = DispatchHistoryService.skus_page(
        pagination={"limit": 50, "offset": 0}, **window
    )
    row = {r["sku"]: r for r in data["items"]}["SKUCXL"]
    drill = DispatchHistoryService.sku_orders(product_id=p.id, **window)

    # Đơn bị huỷ (xuất ngày khác) không có mặt ở phần bung dòng…
    assert [r["order_id"] for r in drill] == [other.id]
    # …nên cũng không được đếm vào dòng SKU, và không được trừ SL của ngày này.
    assert row["order_count"] == 1
    assert row["net"] == 1
    assert data["total_quantity"] == 1


def test_sku_row_counts_dispatch_cancelled_within_window():
    """Xuất rồi huỷ trong CÙNG kỳ: hàng đã rời kho nên vẫn là một lần xuất.

    Tile/footer (``total_issued``) và tab Theo đơn đã theo luật này; dòng SKU
    trước đây cộng thẳng net nên tự trừ mất phần đã huỷ và nói khác cả hai.
    """
    from datetime import datetime

    wh, _, p = _seed_dispatch_and_cancel_same_day()  # xuất 1 rồi huỷ, cùng 27/7
    _add_order_with_sale(
        wh, p, code="90002", qty=2,
        at=datetime(2026, 7, 27, 4, 0, tzinfo=UTC),
    )
    window = {"date_from": "2026-07-27", "date_to": "2026-07-27"}

    data = DispatchHistoryService.skus_page(
        pagination={"limit": 50, "offset": 0}, **window
    )
    row = {r["sku"]: r for r in data["items"]}["SKUSD"]
    assert row["order_count"] == 2
    assert row["net"] == 3
    assert data["total_quantity"] == 3


def test_undone_dispatch_drops_out_of_sku_drilldown():
    """Bấm nhầm "đã xuất" rồi bỏ đánh dấu (đảo note rỗng): hàng CHƯA rời kho.

    Tab Theo đơn đã ẩn đơn này (``issued = 0`` và không có SALE hành chính);
    phần bung dòng của view Theo SKU phải ẩn theo, không thì hai tab của cùng
    một bộ lọc liệt kê khác nhau.
    """
    from datetime import datetime

    from apps.inventory.models import StockMovement

    wh, o, p = _seed_dispatch_and_cancel_same_day()
    StockMovement.objects.filter(
        reference_id=o.id, movement_type=MovementType.SALE_REVERSAL
    ).update(note="")
    kept = _add_order_with_sale(
        wh, p, code="90003", qty=2,
        at=datetime(2026, 7, 27, 4, 0, tzinfo=UTC),
    )
    window = {"date_from": "2026-07-27", "date_to": "2026-07-27"}

    orders = DispatchHistoryService.orders_page(
        pagination={"limit": 50, "offset": 0}, **window
    )
    drill = DispatchHistoryService.sku_orders(product_id=p.id, **window)
    assert [i["order_id"] for i in orders["items"]] == [kept.id]
    assert [r["order_id"] for r in drill] == [kept.id]


def test_partially_returned_order_total_stays_net():
    """Hoàn một phần thì tổng vẫn là net — không phồng lên thành SL gộp."""
    _seed_two_orders_one_sku()  # SKU1: xuất 3 hoàn 1 (đơn 1) + xuất 5; SKU2: xuất 2
    data = DispatchHistoryService.orders_page(pagination={"limit": 50, "offset": 0})
    assert data["total_quantity"] == 9


def test_summary_counts_dispatched_then_cancelled_order():
    from apps.inventory.application.services.flow_summary_service import (
        FlowSummaryService,
    )

    _seed_dispatch_and_cancel_same_day()
    summary = FlowSummaryService.summary(
        date_from="2026-07-27", date_to="2026-07-27",
        warehouse_ids=None, product="", product_ids=None, order="", carrier="",
    )
    assert summary["issue"]["sale_quantity"] == 1
    assert sum(c["order_count"] for c in summary["carriers"]) == 1


# ── Tìm theo mã: chỉ được dò trigram MỘT lần cho mỗi lần dựng bảng ───────────
# EXPLAIN trên prod: BitmapOr 3 cột trigram tốn ~60ms và trả về 2 dòng. Mỗi lần
# tải bảng gọi `base_queryset` ~5 lần → lặp lại đúng chừng ấy lần cái 60ms đó.


def _ilike_queries(ctx):
    return [q["sql"] for q in ctx.captured_queries if " ILIKE " in q["sql"]]


def test_order_search_resolves_once_per_page():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    _seed_two_orders_one_sku()
    with CaptureQueriesContext(connection) as ctx:
        data = DispatchHistoryService.orders_page(
            pagination={"limit": 50, "offset": 0}, order="HD001"
        )
    assert [i["invoice_code"] for i in data["items"]] == ["HD001"]
    assert len(_ilike_queries(ctx)) == 1, _ilike_queries(ctx)


def test_sku_search_resolves_once_per_page():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    _seed_two_orders_one_sku()
    with CaptureQueriesContext(connection) as ctx:
        DispatchHistoryService.skus_page(
            pagination={"limit": 50, "offset": 0}, order="HD001"
        )
    assert len(_ilike_queries(ctx)) == 1, _ilike_queries(ctx)


# ── Tìm theo mã QUÉT khác shipping_code của đơn ──────────────────────────────
# Ca prod 84859721026220: kiện đổi bảo hành gửi ĐVVC khác, kho quét mã trên nhãn
# rồi khớp tay vào đơn mang shipping_code SPXVN... Tìm theo mã quét phải ra đơn —
# không thì tiles/footer về 0 trong khi dòng mã quét vẫn hiện, đọc thành "xuất
# nhưng không trừ tồn".


def test_order_search_finds_order_via_matched_scan_code():
    wh, o1, o2, *_ = _seed_two_orders_one_sku()
    PendingShipmentScan.objects.create(
        code="84859721026220", kind="dispatch", warehouse=wh,
        status="matched", matched_order=o1,
    )
    data = DispatchHistoryService.orders_page(
        pagination={"limit": 50, "offset": 0}, order="84859721026220"
    )
    assert [i["order_id"] for i in data["items"]] == [o1.id]
    assert data["total_quantity"] == 2  # net đơn 1: xuất 3 hoàn 1


def test_order_search_via_scan_code_does_not_duplicate_order():
    """Mã quét TRÙNG shipping_code (ca thường): đơn chỉ hiện một dòng."""
    wh, o1, *_ = _seed_two_orders_one_sku()
    PendingShipmentScan.objects.create(
        code="VD001", kind="dispatch", warehouse=wh,
        status="matched", matched_order=o1,
    )
    data = DispatchHistoryService.orders_page(
        pagination={"limit": 50, "offset": 0}, order="VD001"
    )
    assert [i["order_id"] for i in data["items"]] == [o1.id]


def test_unmatched_scan_code_yields_no_orders():
    """Mã quét chưa khớp (matched_order null) không được kéo đơn rác vào kết quả."""
    wh, *_ = _seed_two_orders_one_sku()
    PendingShipmentScan.objects.create(
        code="99999999999999", kind="dispatch", warehouse=wh, status="pending",
    )
    data = DispatchHistoryService.orders_page(
        pagination={"limit": 50, "offset": 0}, order="99999999999999"
    )
    assert data["items"] == []
    assert data["total"] == 0


# ── Mã QUÉT khác shipping_code phải lộ ra trên dòng đơn ─────────────────────
# Ca prod 84859795173645 / 84859797173814 (16/09/2026): hàng bảo hành gửi lại
# bằng vận đơn MỚI, kho quét rồi khớp tay vào đơn tháng 5. Tồn trừ đúng nhưng
# dòng đơn (màn hình + Excel) chỉ in mã cũ của đơn → tra mã trên nhãn kiện
# không thấy đâu, tưởng "chưa xuất".


def test_orders_page_exposes_matched_scan_codes_that_differ_from_order():
    wh, o1, o2, *_ = _seed_two_orders_one_sku()
    PendingShipmentScan.objects.create(
        code="84859795173645", kind="dispatch", warehouse=wh,
        status="matched", matched_order=o1,
    )
    data = DispatchHistoryService.orders_page(pagination={"limit": 50, "offset": 0})
    by_id = {i["order_id"]: i for i in data["items"]}
    assert by_id[o1.id]["shipping_code"] == "VD001"
    assert by_id[o1.id]["scan_codes"] == ["84859795173645"]
    assert by_id[o2.id]["scan_codes"] == []


def test_orders_page_scan_codes_skip_codes_equal_to_order_codes():
    """Mã quét trùng mã vận đơn / mã hoá đơn của đơn (ca thường) không lặp lại."""
    wh, o1, *_ = _seed_two_orders_one_sku()
    PendingShipmentScan.objects.create(
        code="VD001", kind="dispatch", warehouse=wh, status="matched", matched_order=o1,
    )
    PendingShipmentScan.objects.create(
        code="HD001", kind="dispatch", warehouse=wh, status="matched", matched_order=o1,
    )
    data = DispatchHistoryService.orders_page(pagination={"limit": 50, "offset": 0})
    by_id = {i["order_id"]: i for i in data["items"]}
    assert by_id[o1.id]["scan_codes"] == []


def test_orders_page_scan_codes_ignore_voided_and_unmatched_scans():
    from django.utils import timezone

    wh, o1, *_ = _seed_two_orders_one_sku()
    PendingShipmentScan.objects.create(
        code="VOIDED0001", kind="dispatch", warehouse=wh,
        status="matched", matched_order=o1, voided_at=timezone.now(),
    )
    PendingShipmentScan.objects.create(
        code="FAILED0001", kind="dispatch", warehouse=wh,
        status="failed", matched_order=o1, error="đã xuất kho trước đó",
    )
    PendingShipmentScan.objects.create(
        code="RET0001", kind="return", warehouse=wh, status="matched", matched_order=o1,
    )
    data = DispatchHistoryService.orders_page(pagination={"limit": 50, "offset": 0})
    by_id = {i["order_id"]: i for i in data["items"]}
    assert by_id[o1.id]["scan_codes"] == []
