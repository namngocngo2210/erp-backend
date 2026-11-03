"""Integration tests: stock deduction on dispatched CRM status.

Task 2 — deduct stock when delivery_business_status_code becomes "dispatched"
(not on confirm), and release via OrderStatusChanged event on RETURNED/CANCELLED.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, UserChannel
from apps.customers.models import Customer
from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import MovementType, StockItem, StockMovement
from apps.orders.models import Order, OrderAssessment, OrderItem, OrderStatus
from apps.catalog.models import Product
from apps.orders.application.services.order_service_impl import OrderService
from apps.orders.tests.helpers import make_scoped_user
from apps.users.models import DataScope, User
from apps.users.rbac import bump_rbac_cache_version
from apps.users.rbac_catalog import sync_permissions
from apps.users.tests.helpers import make_admin
from apps.warehouses.models import Warehouse


def _make_actor() -> User:
    return make_admin("dispatch-stock-actor")


def _make_channel() -> Channel:
    return Channel.objects.create(code="lp-dispatch-stock", name="Dispatch Stock Channel")


def _make_customer(suffix: str = "") -> Customer:
    return Customer.objects.create(
        full_name=f"Khach Dispatch {suffix}",
        phone_raw=f"091900000{suffix}",
        phone_normalized=f"091900000{suffix}",
        code=f"CUST-DISP-{suffix}",
    )


def _make_product(suffix: str = "D1") -> Product:
    return Product.objects.create(name=f"SP Dispatch {suffix}", sku=f"SKU-DISP-{suffix}")


def _seed_stock(warehouse: Warehouse, product: Product, qty: int) -> None:
    StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=qty,
        movement_type=MovementType.RECEIPT,
    )


class DispatchStockTests(TestCase):
    def setUp(self):
        # Ensure inventory subscribers are registered so event → release works.
        from apps.inventory import subscribers
        subscribers.register()

        self.actor = _make_actor()
        self.channel = _make_channel()
        self.warehouse = Warehouse.objects.create(code="WH-DISP-INT", name="Kho Dispatch Int")
        self.product = _make_product("A")

    def _make_order_with_warehouse(self, suffix: str = "w1") -> Order:
        customer = _make_customer(suffix)
        order = Order.objects.create(
            channel=self.channel,
            customer=customer,
            name=f"Khach Dispatch {suffix}",
            phone=f"091900000{suffix}",
            total_amount="200000",
            fulfillment_warehouse=self.warehouse,
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            sku=self.product.sku,
            quantity=2,
            unit_price=100000,
            line_total=200000,
        )
        return order

    def _make_order_without_warehouse(self, suffix: str = "nw1") -> Order:
        customer = _make_customer(suffix)
        order = Order.objects.create(
            channel=self.channel,
            customer=customer,
            name=f"Khach No WH {suffix}",
            phone=f"091900000{suffix}",
            total_amount="200000",
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            sku=self.product.sku,
            quantity=2,
            unit_price=100000,
            line_total=200000,
        )
        return order

    # -----------------------------------------------------------------------
    # (1) dispatched + có kho → trừ tồn
    # -----------------------------------------------------------------------
    def test_dispatch_deducts_stock(self):
        """Setting delivery_business_status_code='dispatched' with a warehouse → deducts stock."""
        _seed_stock(self.warehouse, self.product, 10)
        order = self._make_order_with_warehouse("w1")

        OrderService.update_order_inline(
            order,
            {"crm": {"delivery_business_status_code": "dispatched"}},
            self.actor,
        )

        item = StockItem.objects.get(warehouse=self.warehouse, product=self.product)
        self.assertEqual(item.quantity, 8, "Stock should drop from 10 to 8 after dispatched")

        mv = StockMovement.objects.get(
            reference_type="order",
            reference_id=order.id,
            movement_type=MovementType.SALE,
        )
        self.assertEqual(mv.qty_delta, -2)

    # -----------------------------------------------------------------------
    # (2) dispatched + KHÔNG kho → ValidationError, rollback
    # -----------------------------------------------------------------------
    def test_dispatch_without_warehouse_blocks(self):
        """dispatched without fulfillment_warehouse → ValidationError, no movement, status not saved."""
        from rest_framework.exceptions import ValidationError

        _seed_stock(self.warehouse, self.product, 10)
        order = self._make_order_without_warehouse("nw1")

        with self.assertRaises(ValidationError):
            OrderService.update_order_inline(
                order,
                {"crm": {"delivery_business_status_code": "dispatched"}},
                self.actor,
            )

        # Transaction must have rolled back: OrderAssessment must NOT exist
        # because it was not created before the failed dispatch attempt.
        # If it somehow got created inside the rolled-back txn, the rollback failed.
        self.assertFalse(
            OrderAssessment.objects.filter(order=order).exists(),
            "Transaction must roll back: no OrderAssessment should be persisted after failed dispatch",
        )

        # No sale movement created
        self.assertFalse(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=order.id,
                movement_type=MovementType.SALE,
            ).exists()
        )

    # -----------------------------------------------------------------------
    # (3) dispatched 2 lần → chỉ trừ 1 lần (idempotent)
    # -----------------------------------------------------------------------
    def test_dispatch_idempotent(self):
        """Calling dispatched twice should only deduct stock once (issue_for_order is idempotent)."""
        _seed_stock(self.warehouse, self.product, 10)
        order = self._make_order_with_warehouse("w2")

        OrderService.update_order_inline(
            order,
            {"crm": {"delivery_business_status_code": "dispatched"}},
            self.actor,
        )
        # Call again — should not double-deduct
        OrderService.update_order_inline(
            order,
            {"crm": {"delivery_business_status_code": "dispatched"}},
            self.actor,
        )

        item = StockItem.objects.get(warehouse=self.warehouse, product=self.product)
        self.assertEqual(item.quantity, 8, "Stock should be deducted only once")

        sale_movements = StockMovement.objects.filter(
            reference_type="order",
            reference_id=order.id,
            movement_type=MovementType.SALE,
        ).count()
        self.assertEqual(sale_movements, 1, "Should only have 1 SALE movement even after 2 dispatched calls")

    # -----------------------------------------------------------------------
    # (4) confirm (status → PENDING_PROCESSING) KHÔNG còn trừ tồn
    # -----------------------------------------------------------------------
    def test_confirm_no_longer_deducts(self):
        """Confirming an order (status→PENDING_PROCESSING) must NOT deduct stock anymore."""
        _seed_stock(self.warehouse, self.product, 10)
        order = self._make_order_with_warehouse("w3")

        OrderService.update_order_inline(
            order,
            {"status": OrderStatus.PENDING_PROCESSING},
            self.actor,
        )

        # No sale movement should exist
        self.assertFalse(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=order.id,
                movement_type=MovementType.SALE,
            ).exists(),
            "Confirm must no longer create a SALE movement",
        )

        # Stock untouched
        item = StockItem.objects.get(warehouse=self.warehouse, product=self.product)
        self.assertEqual(item.quantity, 10, "Stock must remain at 10 after confirm")

    # -----------------------------------------------------------------------
    # (5) status → RETURNED KHÔNG còn auto-release stock
    # -----------------------------------------------------------------------
    def test_returned_no_longer_releases_via_event(self):
        """RETURNED must NOT auto-release stock (physical ReturnNote handles restock)."""
        _seed_stock(self.warehouse, self.product, 10)
        order = self._make_order_with_warehouse("w4")

        OrderService.update_order_inline(
            order,
            {"crm": {"delivery_business_status_code": "dispatched"}},
            self.actor,
        )
        item = StockItem.objects.get(warehouse=self.warehouse, product=self.product)
        self.assertEqual(item.quantity, 8, "Should be 8 after dispatched")

        with self.captureOnCommitCallbacks(execute=True):
            OrderService.update_order_inline(
                order,
                {"status": OrderStatus.RETURNED},
                self.actor,
            )

        item.refresh_from_db()
        self.assertEqual(item.quantity, 8, "RETURNED must NOT restore stock anymore")
        self.assertFalse(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=order.id,
                movement_type=MovementType.SALE_REVERSAL,
            ).exists(),
            "No SALE_REVERSAL should be created on RETURNED",
        )

    # -----------------------------------------------------------------------
    # (6) đơn ĐÃ xuất kho → không đổi được kho xuất
    # -----------------------------------------------------------------------
    # Tồn đã trừ ở kho cũ; đổi FK không dời sổ tồn sang kho mới nên cả hai kho cùng
    # lệch. Trang chi tiết đơn khoá sẵn ô chọn, đây là chốt chặn cho mọi đường PATCH.
    def test_change_warehouse_after_dispatch_blocks(self):
        from rest_framework.exceptions import ValidationError

        _seed_stock(self.warehouse, self.product, 10)
        order = self._make_order_with_warehouse("w5")
        OrderService.update_order_inline(
            order,
            {"crm": {"delivery_business_status_code": "dispatched"}},
            self.actor,
        )
        other = Warehouse.objects.create(code="WH-DISP-INT-2", name="Kho Dispatch Int 2")

        with self.assertRaises(ValidationError):
            OrderService.update_order_inline(order, {"fulfillment_warehouse": other}, self.actor)

        order.refresh_from_db()
        self.assertEqual(
            order.fulfillment_warehouse_id,
            self.warehouse.id,
            "Kho xuất phải giữ nguyên sau khi guard chặn",
        )
        item = StockItem.objects.get(warehouse=self.warehouse, product=self.product)
        self.assertEqual(item.quantity, 8, "Tồn kho cũ không được đụng vào")

    def test_resend_same_warehouse_after_dispatch_passes(self):
        """Gửi lại ĐÚNG kho hiện tại không phải là đổi kho → không chặn.

        Form chi tiết đơn PATCH nhiều field một lượt, kho cũ thường đi kèm.
        """
        _seed_stock(self.warehouse, self.product, 10)
        order = self._make_order_with_warehouse("w6")
        OrderService.update_order_inline(
            order,
            {"crm": {"delivery_business_status_code": "dispatched"}},
            self.actor,
        )

        OrderService.update_order_inline(
            order,
            {"fulfillment_warehouse": self.warehouse, "shipping_fee": "30000"},
            self.actor,
        )

        order.refresh_from_db()
        self.assertEqual(order.fulfillment_warehouse_id, self.warehouse.id)
        self.assertEqual(str(order.shipping_fee), "30000.00")

    def test_change_warehouse_before_dispatch_passes(self):
        """Đơn chưa rời kho (kho đang xử lý) thì vẫn đổi kho thoải mái."""
        order = self._make_order_with_warehouse("w7")
        OrderService.update_order_inline(
            order,
            {"crm": {"delivery_business_status_code": "warehouse_processing"}},
            self.actor,
        )
        other = Warehouse.objects.create(code="WH-DISP-INT-3", name="Kho Dispatch Int 3")

        OrderService.update_order_inline(order, {"fulfillment_warehouse": other}, self.actor)

        order.refresh_from_db()
        self.assertEqual(order.fulfillment_warehouse_id, other.id)


class DispatchStockCycleTests(TestCase):
    """Integration tests: vòng đời xuất/hoàn tồn qua update_order_inline.

    Bao phủ các fix hardening:
    - un-dispatch (bỏ trạng thái "Đã xuất kho") hoàn tồn;
    - xuất lại sau chu kỳ huỷ/hoàn vẫn trừ tồn;
    - sửa items sau xuất kho đồng bộ lại tồn;
    - huỷ đơn sau khi hàng hoàn đã quét nhập → không cộng tồn 2 lần.
    """

    def setUp(self):
        from apps.inventory import subscribers

        subscribers.register()
        self.actor = make_admin("dispatch-cycle-actor")
        self.channel = Channel.objects.create(code="lp-cycle", name="Cycle Channel")
        self.warehouse = Warehouse.objects.create(code="WH-CYC-INT", name="Kho Cycle Int")
        self.product = Product.objects.create(name="SP Cycle Int", sku="SKU-CYC-INT")
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=10,
            movement_type=MovementType.RECEIPT,
        )

    def _make_order(self, suffix: str, qty: int = 2) -> Order:
        customer = Customer.objects.create(
            full_name=f"Khach Cycle {suffix}",
            phone_raw=f"09188000{suffix}",
            phone_normalized=f"09188000{suffix}",
            code=f"CUST-CYC-{suffix}",
        )
        order = Order.objects.create(
            channel=self.channel,
            customer=customer,
            name=f"Khach Cycle {suffix}",
            phone=f"09188000{suffix}",
            total_amount="200000",
            fulfillment_warehouse=self.warehouse,
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            sku=self.product.sku,
            quantity=qty,
            unit_price=100000,
            line_total=100000 * qty,
        )
        return order

    def _qty(self) -> int:
        item = StockItem.objects.filter(
            warehouse=self.warehouse, product=self.product, state="sellable"
        ).first()
        return item.quantity if item else 0

    def _dispatch(self, order: Order):
        OrderService.update_order_inline(
            order,
            {"crm": {"delivery_business_status_code": "dispatched"}},
            self.actor,
        )

    def test_undispatch_restores_stock(self):
        order = self._make_order("01")
        self._dispatch(order)
        self.assertEqual(self._qty(), 8)

        OrderService.update_order_inline(
            order,
            {"crm": {"delivery_business_status_code": "warehouse_processing"}},
            self.actor,
        )
        self.assertEqual(self._qty(), 10, "Bỏ trạng thái xuất kho phải hoàn tồn")

    def test_undispatch_to_received_does_not_release(self):
        """dispatched → received là giao thành công, không được hoàn tồn."""
        order = self._make_order("02")
        self._dispatch(order)
        OrderService.update_order_inline(
            order,
            {"crm": {"delivery_business_status_code": "received"}},
            self.actor,
        )
        self.assertEqual(self._qty(), 8)

    def test_redispatch_after_undispatch_deducts_again(self):
        order = self._make_order("03")
        self._dispatch(order)
        OrderService.update_order_inline(
            order,
            {"crm": {"delivery_business_status_code": "warehouse_processing"}},
            self.actor,
        )
        self.assertEqual(self._qty(), 10)

        self._dispatch(order)
        self.assertEqual(self._qty(), 8, "Xuất lại sau un-dispatch phải trừ tồn")

    def test_items_edit_after_dispatch_resyncs_stock(self):
        order = self._make_order("04", qty=2)
        self._dispatch(order)
        self.assertEqual(self._qty(), 8)

        OrderService.update_order_inline(
            order,
            {"items": [{"product_id": self.product.id, "quantity": 1, "unit_price": 100000}]},
            self.actor,
        )
        self.assertEqual(self._qty(), 9, "Giảm items 2→1 sau xuất phải hoàn 1")

        OrderService.update_order_inline(
            order,
            {"items": [{"product_id": self.product.id, "quantity": 3, "unit_price": 100000}]},
            self.actor,
        )
        self.assertEqual(self._qty(), 7, "Tăng items 1→3 phải trừ thêm 2")

    def test_items_edit_before_dispatch_does_not_touch_stock(self):
        order = self._make_order("05", qty=2)
        OrderService.update_order_inline(
            order,
            {"items": [{"product_id": self.product.id, "quantity": 5, "unit_price": 100000}]},
            self.actor,
        )
        self.assertEqual(self._qty(), 10)

    def test_cancel_after_scanned_return_does_not_double_credit(self):
        """Kho đã quét nhập hoàn (ReturnEntry posted) rồi sale mới huỷ đơn →
        auto-release không được cộng tồn lần 2."""
        order = self._make_order("06", qty=2)
        self._dispatch(order)
        self.assertEqual(self._qty(), 8)

        ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=[{
                "product_id": self.product.id,
                "quantity": 2,
                "return_type": "delivery_returned",
            }],
            source_order_id=order.id,
        )
        self.assertEqual(self._qty(), 10)

        with self.captureOnCommitCallbacks(execute=True):
            OrderService.update_order_inline(
                order,
                {"status": OrderStatus.CANCELLED},
                self.actor,
            )
        self.assertEqual(self._qty(), 10, "Không được cộng tồn 2 lần khi huỷ sau hoàn")
        self.assertFalse(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=order.id,
                movement_type=MovementType.SALE_REVERSAL,
            ).exists()
        )

    def test_cancel_without_return_keeps_stock_deducted(self):
        """Huỷ đơn đã xuất mà hàng chưa về: tồn GIỮ NGUYÊN đã trừ.

        Hàng đang trên đường về kho — chỉ khi kho quét nhập hoàn mới được cộng lại
        (spec 2026-07-29-cancelled-after-dispatch-return-design.md §3).
        """
        order = self._make_order("07", qty=2)
        self._dispatch(order)
        self.assertEqual(self._qty(), 8)

        with self.captureOnCommitCallbacks(execute=True):
            OrderService.update_order_inline(
                order,
                {"status": OrderStatus.CANCELLED},
                self.actor,
            )
        self.assertEqual(self._qty(), 8, "Hàng còn ngoài kho → không hoàn tồn khi huỷ")
        self.assertFalse(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=order.id,
                movement_type=MovementType.SALE_REVERSAL,
            ).exists()
        )


# Verifier được import cục bộ trong `dispatch_orders_batch` → patch trên class nguồn.
_VERIFY = (
    "apps.kiotviet.application.services.cancellation_verifier."
    "InvoiceCancellationVerifier.verify_not_cancelled"
)


class DispatchBatchRecordScopeTests(TestCase):
    """Record-scope cho `dispatch_batch` (POST /api/orders/dispatch-batch).

    Trước fix, endpoint nhận thẳng `order_ids` từ body rồi đưa vào
    `OrderService.dispatch_orders_batch` — gate duy nhất là `require_section("shipments")`.
    Nhưng `UserService.can_edit_order` trả `True` VÔ ĐIỀU KIỆN cho scope OWN/ALL, nên một
    user scope OWN chỉ cần đoán id là XUẤT KHO (trừ tồn thật + đổi trạng thái giao hàng)
    được đơn của bất kỳ ai (audit 2026-07-26 N1).

    Mọi test assert vào TRẠNG THÁI DB (`StockMovement` loại SALE + `delivery_business_status`
    của đơn), không chỉ payload: `results` là list per-order nên chỉ đọc `error` là chưa
    chứng minh được tồn kho KHÔNG bị trừ.

    Verifier KiotViet luôn được patch để đơn "sạch" theo mọi tiêu chí khác — nếu request vẫn
    bị chặn thì chỉ có thể do record-scope, không phải do guard đơn hủy chen ngang.

    Mock trả `None` (verdict "lạ") chứ KHÔNG trả `CancelCheck.OK`: import
    `apps.kiotviet.*` từ `apps.orders.tests` sẽ phá contract phân tầng của import-linter
    (`apps.orders` không được import `apps.kiotviet`) và đợt này KHÔNG thêm entry
    `ignore_imports` mới. Hành vi vẫn đúng ý đồ: theo docstring `_assert_dispatch_allowed`,
    verdict lạ → guard TỰ đi hỏi lại KiotViet, và lời gọi thứ hai cũng đã bị patch → không
    CANCELLED → đơn được xuất. Tức là mock này còn CHẶT hơn `CancelCheck.OK` vì không cho
    phép bỏ qua round-trip xác minh.
    """

    def setUp(self):
        # KHÔNG cache.clear() — RedisCache.clear() FLUSHDB cả redis dùng chung.
        sync_permissions()
        self.client = APIClient()
        self.ch_a = Channel.objects.create(code="dispatch-a", name="Dispatch A")
        self.ch_b = Channel.objects.create(code="dispatch-b", name="Dispatch B")
        self.product = Product.objects.create(name="SP Dispatch", sku="SKU-DISPATCH-1")
        self.warehouse = Warehouse.objects.create(code="WH-DISPATCH", name="Kho Dispatch")
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=100,
            movement_type=MovementType.RECEIPT,
        )

        self.owner = make_scoped_user("dispatch-owner", "shipments", scope=DataScope.OWN)
        self.teammate = make_scoped_user("dispatch-teammate", "shipments", scope=DataScope.OWN)
        self.boss = make_scoped_user("dispatch-boss", "shipments", scope=DataScope.ALL)

        self.own_order = self._order(self.ch_a, self.owner)
        self.other_order = self._order(self.ch_a, self.teammate)

    def _order(self, channel: Channel, staff: User) -> Order:
        suffix = uuid.uuid4().hex[:8]
        customer = Customer.objects.create(
            full_name=f"KH {suffix}",
            phone_raw=f"09{suffix[:8]}",
            phone_normalized=f"09{suffix[:8]}",
            code=f"CUST-DISPATCH-{suffix}",
        )
        order = Order.objects.create(
            channel=channel,
            customer=customer,
            staff=staff,
            order_code=f"ORD-{suffix}",
            name=f"KH {suffix}",
            phone=f"09{suffix[:8]}",
            total_amount="200000",
            status=OrderStatus.IN_DELIVERY,
            fulfillment_warehouse=self.warehouse,
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            sku=self.product.sku,
            quantity=2,
            unit_price=100000,
            line_total=200000,
        )
        return order

    def _dispatch(self, user: User, *orders: Order):
        self.client.force_authenticate(user=user)
        # `return_value=None`: xem docstring class — cố ý không import CancelCheck.
        with patch(_VERIFY, return_value=None):
            return self.client.post(
                "/api/orders/dispatch-batch",
                {
                    "fulfillment_warehouse_id": self.warehouse.id,
                    "order_ids": [o.id for o in orders],
                },
                format="json",
            )

    @staticmethod
    def _sale_movements(order: Order) -> int:
        return StockMovement.objects.filter(
            reference_type="order",
            reference_id=order.id,
            movement_type=MovementType.SALE,
        ).count()

    def _assert_not_dispatched(self, order: Order):
        """Tồn KHÔNG bị trừ và trạng thái giao hàng của đơn không đổi."""
        self.assertEqual(self._sale_movements(order), 0)
        assessment = getattr(Order.objects.get(id=order.id), "assessment", None)
        if assessment is not None:
            self.assertNotEqual(assessment.delivery_business_status_code, "dispatched")

    def _assert_dispatched(self, order: Order):
        self.assertEqual(self._sale_movements(order), 1)

    # ── chiều CHẶN ──────────────────────────────────────────────────────────
    def test_own_scope_cannot_dispatch_order_of_other_staff(self):
        resp = self._dispatch(self.owner, self.other_order)
        self.assertEqual(resp.status_code, 200, resp.content)
        result = resp.json()["results"][0]
        self.assertEqual(result["order_id"], self.other_order.id)
        self.assertFalse(result["ok"])
        # "not_found" chứ không phải "forbidden": id ngoài scope phải im lặng như id
        # không tồn tại, không làm oracle dò xem đơn có thật hay không.
        self.assertEqual(result["error"], "not_found")
        self._assert_not_dispatched(self.other_order)

    def test_channel_scope_cannot_dispatch_order_of_other_channel(self):
        order_b = self._order(self.ch_b, self.teammate)
        user = make_scoped_user("dispatch-channel-a", "shipments", scope=DataScope.CHANNEL)
        UserChannel.objects.create(user=user, channel=self.ch_a, is_active=True)
        bump_rbac_cache_version()
        resp = self._dispatch(user, order_b)
        self.assertEqual(resp.json()["results"][0]["error"], "not_found")
        self._assert_not_dispatched(order_b)

    def test_channel_scope_without_any_channel_dispatches_nothing(self):
        """Scope CHANNEL nhưng chưa gán kênh nào → không được xuất đơn nào."""
        user = make_scoped_user("dispatch-channel-none", "shipments", scope=DataScope.CHANNEL)
        resp = self._dispatch(user, self.own_order, self.other_order)
        errors = {r["order_id"]: r["error"] for r in resp.json()["results"]}
        self.assertEqual(errors[self.own_order.id], "not_found")
        self.assertEqual(errors[self.other_order.id], "not_found")
        self._assert_not_dispatched(self.own_order)
        self._assert_not_dispatched(self.other_order)

    def test_scope_is_per_order_not_whole_batch(self):
        """Lô trộn đơn trong/ngoài scope: đơn hợp lệ vẫn xuất, đơn ngoài scope bị bỏ."""
        resp = self._dispatch(self.owner, self.own_order, self.other_order)
        by_id = {r["order_id"]: r for r in resp.json()["results"]}
        self.assertTrue(by_id[self.own_order.id]["ok"])
        self.assertFalse(by_id[self.other_order.id]["ok"])
        self._assert_dispatched(self.own_order)
        self._assert_not_dispatched(self.other_order)

    # ── chiều CHO PHÉP ──────────────────────────────────────────────────────
    def test_own_scope_dispatches_own_order(self):
        resp = self._dispatch(self.owner, self.own_order)
        self.assertTrue(resp.json()["results"][0]["ok"], resp.content)
        self._assert_dispatched(self.own_order)

    def test_all_scope_dispatches_any_order(self):
        """Kho scope ALL vẫn xuất được đơn của mọi sale — không chặn oan nghiệp vụ kho."""
        resp = self._dispatch(self.boss, self.other_order)
        self.assertTrue(resp.json()["results"][0]["ok"], resp.content)
        self._assert_dispatched(self.other_order)

    def test_channel_scope_dispatches_order_of_assigned_channel(self):
        user = make_scoped_user("dispatch-channel-ok", "shipments", scope=DataScope.CHANNEL)
        UserChannel.objects.create(user=user, channel=self.ch_a, is_active=True)
        bump_rbac_cache_version()
        resp = self._dispatch(user, self.other_order)
        self.assertTrue(resp.json()["results"][0]["ok"], resp.content)
        self._assert_dispatched(self.other_order)

    def test_unknown_order_id_still_reported_as_not_found(self):
        """Id không tồn tại vẫn phải có một dòng kết quả (FE map theo order_id)."""
        self.client.force_authenticate(user=self.boss)
        resp = self.client.post(
            "/api/orders/dispatch-batch",
            {"fulfillment_warehouse_id": self.warehouse.id, "order_ids": [99999999]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(
            resp.json()["results"], [{"order_id": 99999999, "ok": False, "error": "not_found"}]
        )
