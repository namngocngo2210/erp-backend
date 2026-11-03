"""`SqlCatalogReader`: đúng sự kiện cho luật chọn (tồn, bán gần đây, combo, nhóm) và
số query cố định — không N+1 theo số sản phẩm."""

from __future__ import annotations

from datetime import timedelta
from itertools import count

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.catalog.models import Product, ProductComponent, ProductGroup
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.models import StockItem
from apps.orders.models import Order, OrderItem
from apps.pancake_pos.domain.eligibility import is_syncable
from apps.pancake_pos.infrastructure.catalog_reader import SqlCatalogReader
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db
_seq = count(1)


@pytest.fixture
def env():
    return {
        "channel": Channel.objects.create(code="ppos-test", name="Kênh test"),
        "warehouse": Warehouse.objects.create(code="PPOS-W1", name="Kho 1"),
    }


def _sell(env, product, *, days_ago: int, source: str | None = None) -> None:
    n = next(_seq)
    phone = f"09{n:08d}"
    customer = Customer.objects.create(full_name="Khách", phone_raw=phone, phone_normalized=phone)
    order = Order.all_objects.create(channel=env["channel"], customer=customer, name="Khách", phone=phone,
                                     order_source=source)
    Order.all_objects.filter(id=order.id).update(created_at=timezone.now() - timedelta(days=days_ago))
    OrderItem.objects.create(order=order, product=product, sku=product.sku)


def _read(**kw):
    since = timezone.now() - timedelta(days=180)
    return {p.sku: p for p in SqlCatalogReader().list_candidates(sold_since=since, **kw)}


def test_su_kien_cho_luat_chon(env):
    group = ProductGroup.objects.create(code="OHI01", name="OHI01")
    dead_group = ProductGroup.objects.create(code="DEAD", name="DEAD", deleted_at=timezone.now())
    in_stock = Product.objects.create(sku="OHI01-DEN", name="Ghế Đen", group=group, weight=1200)
    zero = Product.objects.create(sku="OHI01-TRANG", name="Ghế Trắng", group=group)
    sold = Product.objects.create(sku="GOI01", name="Gối", pancake_sync_mode="always", short_name="Gối cổ")
    old = Product.objects.create(sku="MU01", name="Mũ", group=dead_group)
    mirror = Product.objects.create(sku="FX01", name="Hàng Foxia")
    combo = Product.objects.create(sku="COMBO01", name="Combo")
    Product.objects.create(sku="XOA01", name="Đã xoá", deleted_at=timezone.now())
    ProductComponent.objects.create(parent=combo, component=sold)
    StockItem.objects.create(warehouse=env["warehouse"], product=in_stock, quantity=-3)
    StockItem.objects.create(warehouse=env["warehouse"], product=zero, quantity=0)
    _sell(env, sold, days_ago=10)
    _sell(env, sold, days_ago=30)
    _sell(env, old, days_ago=200)
    _sell(env, mirror, days_ago=5, source="foxia")

    rows = _read()
    assert "XOA01" not in rows
    assert rows["OHI01-DEN"].has_stock is True  # tồn âm ≠ 0 vẫn tính
    assert rows["OHI01-DEN"].group_code == "OHI01"
    assert rows["OHI01-DEN"].weight == 1200
    assert rows["OHI01-TRANG"].has_stock is False
    assert rows["GOI01"].last_sold_at is not None
    assert rows["GOI01"].last_sold_at > timezone.now() - timedelta(days=11)  # lần bán MỚI NHẤT
    assert rows["GOI01"].sync_mode == "always"
    assert rows["GOI01"].short_name == "Gối cổ" and rows["GOI01"].display_name == "Gối cổ"
    assert rows["OHI01-DEN"].short_name == "" and rows["OHI01-DEN"].display_name == "Ghế Đen"
    assert rows["MU01"].last_sold_at is None  # ngoài 180 ngày
    assert rows["MU01"].group_code is None  # nhóm đã xoá mềm
    assert rows["FX01"].last_sold_at is None  # đơn mirror không tính
    assert rows["COMBO01"].is_combo is True and rows["GOI01"].is_combo is False

    now = timezone.now()
    eligible = {sku for sku, p in rows.items() if is_syncable(p, now=now)}
    assert eligible == {"OHI01-DEN", "GOI01"}


def test_loc_sku_keo_ca_nhom(env):
    group = ProductGroup.objects.create(code="OHI01", name="OHI01")
    Product.objects.create(sku="OHI01-DEN", name="Đen", group=group)
    Product.objects.create(sku="OHI01-TRANG", name="Trắng", group=group)
    Product.objects.create(sku="GOI01", name="Gối")
    assert set(_read(skus=["ohi01-den"])) == {"OHI01-DEN", "OHI01-TRANG"}
    assert set(_read(skus=["GOI01"])) == {"GOI01"}
    assert _read(skus=["KHONG-CO"]) == {}


def test_so_query_khong_phu_thuoc_so_san_pham(env):
    def measure() -> int:
        with CaptureQueriesContext(connection) as ctx:
            _read()
        return len(ctx.captured_queries)

    Product.objects.create(sku="A1", name="A1")
    small = measure()
    group = ProductGroup.objects.create(code="G", name="G")
    for i in range(20):
        p = Product.objects.create(sku=f"G-{i}", name=f"G {i}", group=group)
        StockItem.objects.create(warehouse=env["warehouse"], product=p, quantity=1)
        _sell(env, p, days_ago=1)
    assert measure() == small
    assert len(_read()) == 21


def test_loc_theo_id_keo_ca_nhom_ke_ca_id_da_xoa_mem(env):
    group = ProductGroup.objects.create(code="OHI01", name="OHI01")
    gone = Product.objects.create(sku="OHI01-DEN", name="Ghế Đen", group=group, deleted_at=timezone.now())
    Product.objects.create(sku="OHI01-TRANG", name="Ghế Trắng", group=group)
    lone = Product.objects.create(sku="GOI01", name="Gối")
    Product.objects.create(sku="MU01", name="Mũ")
    assert set(_read(product_ids=[gone.id])) == {"OHI01-TRANG"}
    assert set(_read(product_ids=[lone.id])) == {"GOI01"}
    assert _read(product_ids=[]) == {}
