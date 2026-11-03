"""Truy vấn tồn kho: nhãn chứng từ nguồn, run-rate RR3/RR7.

Gộp từ test_stock_query_labels.py, test_stock_run_rate.py (tái cấu trúc test
2026-08-05). Phần xuất Excel (gốc test_stock_export.py) đã chuyển sang
`apps.inventory.tests.test_inventory_export_definitions.py::TestStockExportWrite`
(Task 18 — xoá endpoint `GET /api/inventory/stock/export` đồng bộ, xem
docs/.superpowers/sdd/2026-08-06-async-export-jobs/task-18-report.md).
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_query_service import StockQueryService
from apps.inventory.models import (
    DocStatus,
    DocType,
    MovementType,
    StockDocument,
    StockItem,
    StockMovement,
    StockState,
)
from apps.catalog.models import Product
from apps.users.tests.helpers import (
    make_user_with_permissions,
    make_user_with_sections,
)
from apps.warehouses.models import Warehouse

# ---------------------------------------------------------------------------
# Nhãn chứng từ nguồn của StockMovement (cột "Chứng từ" ở trang Nhập xuất kho).
#
# Chạy trên StockMovement THẬT do service sinh ra — nhánh return_entry mới (nhãn =
# mã phiếu ngày + mã vận đơn) chỉ lộ sai ở prod nếu lookup ``return_note__code`` sai
# chính tả, nên phải có test chốt đúng chuỗi render.
# ---------------------------------------------------------------------------


@pytest.fixture
def order(make_order, make_channel, warehouse, product):
    channel = make_channel(code="lp-stock-label", name="Stock Label Channel")
    return make_order(
        channel=channel, warehouse=warehouse, product=product, quantity=2,
        name="Khach nhãn", phone="0929111222", customer_code="CUST-LBL-1",
        order_code="DH-LBL-1", shipping_code="SPX-LBL-1",
    )


def _labels():
    movements = list(StockMovement.objects.all())
    return StockQueryService.resolve_reference_labels(movements)


@pytest.mark.django_db
def test_return_entry_label_is_daily_note_code_plus_shipping_code(
    warehouse, product, order, staff_user
):
    entry = ReturnEntryService.create_and_post_entry(
        warehouse_id=warehouse.id,
        lines=[
            {"product_id": product.id, "quantity": 2, "return_type": "delivery_returned"}
        ],
        source_order_id=order.id,
        shipping_code="SPX-LBL-1",
        customer_name=order.name,
        customer_phone=order.phone,
        user_id=staff_user.id,
    )
    labels = _labels()
    note_code = entry.return_note.code
    assert labels[("return_entry", entry.id)] == f"{note_code} / SPX-LBL-1"


@pytest.mark.django_db
def test_return_entry_label_without_shipping_code_falls_back_to_note_code(
    warehouse, product, staff_user
):
    # Nhóm đơn tạo tay (không có mã vận đơn) → nhãn chỉ còn mã phiếu ngày,
    # không được dính " / " thừa.
    entry = ReturnEntryService.create_and_post_entry(
        warehouse_id=warehouse.id,
        lines=[{"product_id": product.id, "quantity": 1, "return_type": "warranty"}],
        user_id=staff_user.id,
    )
    labels = _labels()
    assert labels[("return_entry", entry.id)] == entry.return_note.code


@pytest.mark.django_db
def test_document_and_order_labels(warehouse, product, order, staff_user):
    doc = StockDocument.objects.create(
        code="PN-LBL-1",
        doc_type=DocType.RECEIPT,
        dest_warehouse=warehouse,
        status=DocStatus.POSTED,
    )
    StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=5,
        movement_type=MovementType.RECEIPT,
        reference_type="document",
        reference_id=doc.id,
    )
    StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=-2,
        movement_type=MovementType.SALE,
        reference_type="order",
        reference_id=order.id,
    )
    labels = _labels()
    assert labels[("document", doc.id)] == "PN-LBL-1"
    assert labels[("order", order.id)] == "DH-LBL-1"


@pytest.mark.django_db
def test_missing_reference_row_degrades_without_raising(warehouse, product):
    # Chứng từ nguồn đã bị xoá (data cũ / xoá cứng ngoài hệ thống): resolver phải
    # bỏ qua, không nổ KeyError/DoesNotExist — sổ kho vẫn phải xem được.
    StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=3,
        movement_type=MovementType.RECEIPT,
        reference_type="return_entry",
        reference_id=999_999,
    )
    StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=1,
        movement_type=MovementType.RECEIPT,
        reference_type="document",
        reference_id=999_998,
    )
    labels = _labels()
    assert ("return_entry", 999_999) not in labels
    assert ("document", 999_998) not in labels


# ---------------------------------------------------------------------------
# Tests RR3/RR7 (run-rate bán) trên danh sách tồn kho.
# ---------------------------------------------------------------------------


def _movement(warehouse, product, qty_delta, movement_type, days_ago=0):
    StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=qty_delta,
        movement_type=movement_type,
        state=StockState.SELLABLE,
        reference_type="order",
        reference_id=1,
    )
    if days_ago:
        # created_at là auto_now_add — lùi ngày trực tiếp trên bản ghi mới nhất.
        # RR3/RR7 (Task 5) lọc theo occurred_at nên phải lùi cả hai cột, giữ
        # đúng bất biến occurred_at <= created_at.
        latest = StockMovement.objects.filter(
            warehouse=warehouse, product=product
        ).latest("id")
        backdated_at = timezone.now() - timedelta(days=days_ago)
        StockMovement.objects.filter(id=latest.id).update(
            created_at=backdated_at, occurred_at=backdated_at
        )


class SalesRunRateServiceTests(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-RR", name="Kho RR")
        self.product = Product.objects.create(name="SP RR", sku="SKU-RR")
        self.key = (self.warehouse.id, self.product.id)

    def _rates(self):
        return StockQueryService.sales_run_rates({self.key})

    def test_recent_sales_counted_in_both_windows(self):
        _movement(self.warehouse, self.product, -6, MovementType.SALE, days_ago=1)
        rates = self._rates()
        self.assertEqual(rates[self.key]["rr3"], 2.0)  # 6 / 3 ngày
        self.assertEqual(rates[self.key]["rr7"], round(6 / 7, 2))

    def test_sale_5_days_ago_only_in_rr7(self):
        _movement(self.warehouse, self.product, -7, MovementType.SALE, days_ago=5)
        rates = self._rates()
        self.assertEqual(rates[self.key]["rr3"], 0.0)
        self.assertEqual(rates[self.key]["rr7"], 1.0)

    def test_sale_outside_7_days_ignored(self):
        _movement(self.warehouse, self.product, -9, MovementType.SALE, days_ago=8)
        self.assertEqual(self._rates(), {})

    def test_reversal_nets_against_sale_in_window(self):
        # Huỷ→hồi sinh trong cùng cửa sổ: cặp reversal/sale triệt tiêu.
        _movement(self.warehouse, self.product, -3, MovementType.SALE, days_ago=2)
        _movement(self.warehouse, self.product, 3, MovementType.SALE_REVERSAL, days_ago=1)
        _movement(self.warehouse, self.product, -3, MovementType.SALE, days_ago=1)
        rates = self._rates()
        self.assertEqual(rates[self.key]["rr3"], 1.0)

    def test_reversal_of_old_sale_clamped_to_zero(self):
        # Đơn bán ngoài cửa sổ bị huỷ hôm nay → net dương, clamp về 0.
        _movement(self.warehouse, self.product, -4, MovementType.SALE, days_ago=10)
        _movement(self.warehouse, self.product, 4, MovementType.SALE_REVERSAL, days_ago=0)
        rates = self._rates()
        self.assertEqual(rates[self.key]["rr3"], 0.0)
        self.assertEqual(rates[self.key]["rr7"], 0.0)

    def test_non_sale_movements_ignored(self):
        _movement(self.warehouse, self.product, -5, MovementType.ISSUE, days_ago=1)
        _movement(self.warehouse, self.product, 5, MovementType.RETURN_IN, days_ago=1)
        self.assertEqual(self._rates(), {})

    def test_empty_pairs_returns_empty(self):
        self.assertEqual(StockQueryService.sales_run_rates(set()), {})


class StockListRunRateApiTests(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-RRA", name="Kho RR API")
        self.product = Product.objects.create(name="SP RR API", sku="SKU-RRA")
        self.user = make_user_with_sections("rr-user", ["stock"])
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_list_stock_returns_rr_fields(self):
        _movement(self.warehouse, self.product, 10, MovementType.RECEIPT)
        _movement(self.warehouse, self.product, -6, MovementType.SALE, days_ago=1)
        resp = self.client.get(
            f"/api/inventory/stock?warehouse_id={self.warehouse.id}&limit=50&offset=0"
        )
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["items"]
        self.assertTrue(items)
        for item in items:
            self.assertEqual(item["rr3"], 2.0)
            self.assertEqual(item["rr7"], round(6 / 7, 2))


class StockAvailabilityApiTests(TestCase):
    """`GET /api/inventory/stock/availability` — tồn bán được cho form đơn hàng.

    Endpoint này là ngoại lệ về quyền so với phần còn lại của module kho (gác
    bằng `orders:view` chứ không phải `stock:view`), nên ranh giới quyền được
    chốt bằng test chứ không dựa vào quy ước.
    """

    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-AVL", name="Kho tồn form")
        self.other_warehouse = Warehouse.objects.create(code="WH-AVL2", name="Kho khác")
        self.product = Product.objects.create(name="SP tồn form", sku="SKU-AVL-1")
        self.empty_product = Product.objects.create(name="SP chưa nhập", sku="SKU-AVL-2")
        self.user = make_user_with_permissions("availability-user", ["orders:view"])
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _get(self, warehouse_id, skus):
        return self.client.get(
            f"/api/inventory/stock/availability?warehouse_id={warehouse_id}&skus={skus}"
        )

    def _stock(self, warehouse, product, quantity, state=StockState.SELLABLE):
        StockItem.objects.create(
            warehouse=warehouse, product=product, quantity=quantity, state=state
        )

    def test_orders_view_alone_is_enough_and_sums_only_sellable(self):
        self._stock(self.warehouse, self.product, 12)
        self._stock(self.warehouse, self.product, 5, state=StockState.WARRANTY)
        self._stock(self.warehouse, self.product, 3, state=StockState.DEFECT)
        resp = self._get(self.warehouse.id, self.product.sku)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["warehouse_id"], self.warehouse.id)
        self.assertEqual(
            body["items"],
            [
                {
                    "sku": self.product.sku,
                    "product_name": self.product.name,
                    "sellable": 12,
                    "pending_direct": 0,
                }
            ],
        )

    def test_counts_only_the_requested_warehouse(self):
        self._stock(self.warehouse, self.product, 4)
        self._stock(self.other_warehouse, self.product, 99)
        resp = self._get(self.warehouse.id, self.product.sku)
        self.assertEqual(resp.json()["items"][0]["sellable"], 4)

    def test_product_without_stock_row_returns_zero_in_request_order(self):
        self._stock(self.warehouse, self.product, 7)
        resp = self._get(self.warehouse.id, f"{self.empty_product.sku},{self.product.sku}")
        self.assertEqual(
            [(row["sku"], row["sellable"]) for row in resp.json()["items"]],
            [(self.empty_product.sku, 0), (self.product.sku, 7)],
        )

    def test_unknown_sku_is_skipped_and_blank_skus_return_empty(self):
        self._stock(self.warehouse, self.product, 7)
        resp = self._get(self.warehouse.id, f"KHONG-TON-TAI,{self.product.sku}")
        self.assertEqual([row["sku"] for row in resp.json()["items"]], [self.product.sku])
        self.assertEqual(self._get(self.warehouse.id, "").json()["items"], [])

    def test_missing_warehouse_or_too_many_skus_is_rejected(self):
        self.assertEqual(self.client.get("/api/inventory/stock/availability").status_code, 400)
        too_many = ",".join(f"SKU-{index}" for index in range(51))
        self.assertEqual(self._get(self.warehouse.id, too_many).status_code, 400)

    def test_user_without_orders_view_is_forbidden(self):
        outsider = make_user_with_permissions("availability-outsider", ["stock:view"])
        client = APIClient()
        client.force_authenticate(outsider)
        resp = client.get(
            f"/api/inventory/stock/availability?warehouse_id={self.warehouse.id}&skus=X"
        )
        self.assertEqual(resp.status_code, 403)
