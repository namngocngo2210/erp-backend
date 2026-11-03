"""Đơn huỷ chờ hàng về: watch mở/đóng, reopen theo sổ, API tab chờ hoàn, cảnh báo Lark."""
from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.common.events import event_bus
from apps.customers.models import Customer
from apps.inventory.application.services.awaiting_return_service import (
    AwaitingReturnService,
)
from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import (
    SaleLine,
    StockSalesService,
)
from apps.inventory.models import (
    CancelledDispatchWatch,
    CloseReason,
    MovementType,
    StockMovement,
    WatchStatus,
)
from apps.inventory.tasks import alert_awaiting_return_overdue
from apps.orders.contracts.order_events import OrderStatusChanged
from apps.orders.infrastructure.tasks.order_tasks_impl.lark_notification_tasks import (
    notify_lark_cancelled_after_dispatch,
)
from apps.orders.models import Order, OrderItem, OrderStatus
from apps.catalog.models import Product
from apps.users.tests.helpers import make_admin
from apps.warehouses.models import Warehouse


class AwaitingReturnWatchTests(TestCase):
    """Hàng đợi 'đơn huỷ chờ hàng về': mở khi huỷ, tự đóng khi hàng về, đóng tay khi mất hàng."""

    def setUp(self):
        from apps.inventory import subscribers

        subscribers.register()
        self.actor = make_admin("awaiting-return-actor")
        self.warehouse = Warehouse.objects.create(code="WH-AWR", name="Kho AWR")
        self.product = Product.objects.create(name="SP AWR", sku="SKU-AWR1")
        self.channel = Channel.objects.create(code="ch-awr", name="AWR Channel")
        self.customer = Customer.objects.create(
            full_name="Khach AWR",
            phone_raw="0912345702",
            phone_normalized="0912345702",
            code="CUST-AWR",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            order_code="ORD-AWR-1",
            shipping_code="TRACK-AWR-1",
            name="Khach AWR",
            phone="0912345702",
            status=OrderStatus.IN_DELIVERY,
            fulfillment_warehouse=self.warehouse,
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name="SP AWR",
            sku="SKU-AWR1",
            quantity=2,
            unit_price=50000,
            line_total=100000,
        )
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=10,
            movement_type=MovementType.RECEIPT,
        )

    def _dispatch(self, qty: int = 2) -> None:
        StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=qty)],
        )

    def _cancel(self) -> None:
        previous = int(self.order.status)
        Order.objects.filter(pk=self.order.id).update(status=OrderStatus.CANCELLED)
        self.order.refresh_from_db()
        event_bus.publish(
            OrderStatusChanged(
                order_id=self.order.id,
                old_status=previous,
                new_status=int(OrderStatus.CANCELLED),
            )
        )

    def _clear_outstanding(self) -> None:
        """Giả lập hàng về kho: hoàn hết phần outstanding của đơn."""
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=2,
            movement_type=MovementType.SALE_REVERSAL,
            reference_type="order",
            reference_id=self.order.id,
            note="test-clear",
        )

    def _make_sibling(self) -> Order:
        """Đơn xuất lại `<mã>.01`: cùng mã vận đơn, đang sống, chưa có sổ xuất."""
        return Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            order_code="ORD-AWR-1.01",
            invoice_code="HD-AWR-1.01",
            shipping_code=self.order.shipping_code,
            name="Khach AWR",
            phone="0912345702",
            status=OrderStatus.IN_DELIVERY,
            fulfillment_warehouse=self.warehouse,
        )

    def test_cancel_after_dispatch_opens_a_waiting_row(self):
        self._dispatch(2)
        self._cancel()

        watch = CancelledDispatchWatch.objects.get(order_id=self.order.id)
        self.assertEqual(watch.status, WatchStatus.WAITING)
        self.assertEqual(watch.warehouse_id, self.warehouse.id)
        self.assertIsNotNone(watch.cancelled_at)

    def test_cancel_before_dispatch_opens_nothing(self):
        self._cancel()
        self.assertFalse(CancelledDispatchWatch.objects.exists())

    def test_open_is_idempotent(self):
        self._dispatch(2)
        self._cancel()
        AwaitingReturnService.open_for_order(self.order.id)
        self.assertEqual(CancelledDispatchWatch.objects.count(), 1)

    def test_settle_marks_returned_when_outstanding_is_cleared(self):
        self._dispatch(2)
        self._cancel()
        self._clear_outstanding()

        AwaitingReturnService.settle_if_returned(self.order.id)

        watch = CancelledDispatchWatch.objects.get(order_id=self.order.id)
        self.assertEqual(watch.status, WatchStatus.RETURNED)

    def test_settle_keeps_waiting_while_stock_is_still_out(self):
        self._dispatch(2)
        self._cancel()

        AwaitingReturnService.settle_if_returned(self.order.id)

        watch = CancelledDispatchWatch.objects.get(order_id=self.order.id)
        self.assertEqual(watch.status, WatchStatus.WAITING)

    def test_return_scan_closes_the_row(self):
        """Đường THẬT: quét nhập hoàn → dòng theo dõi tự chuyển 'hàng đã về'.

        `return_in` ghi dưới reference_type='return_entry' nên KHÔNG làm giảm
        outstanding của đơn — nếu settle chỉ đọc outstanding thì dòng này kẹt
        'waiting' vĩnh viễn dù hàng đã về đủ.
        """
        self._dispatch(2)
        self._cancel()

        ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=[
                {
                    "product_id": self.product.id,
                    "quantity": 2,
                    "return_type": "delivery_returned",
                }
            ],
            source_order_id=self.order.id,
            shipping_code=self.order.shipping_code,
        )

        watch = CancelledDispatchWatch.objects.get(order_id=self.order.id)
        self.assertEqual(watch.status, WatchStatus.RETURNED)

    def test_partial_return_keeps_the_row_waiting(self):
        """Hoàn một phần: vẫn còn hàng ngoài kho → dòng theo dõi giữ nguyên 'chờ'."""
        self._dispatch(2)
        self._cancel()

        ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=[
                {
                    "product_id": self.product.id,
                    "quantity": 1,
                    "return_type": "delivery_returned",
                }
            ],
            source_order_id=self.order.id,
            shipping_code=self.order.shipping_code,
        )

        watch = CancelledDispatchWatch.objects.get(order_id=self.order.id)
        self.assertEqual(watch.status, WatchStatus.WAITING)

    def test_cancel_after_goods_already_returned_opens_nothing(self):
        """Giao thất bại → hàng về kho qua phiếu hoàn → SAU ĐÓ mới huỷ đơn.

        Không còn gì ngoài kho nên không có việc gì để theo dõi.
        """
        self._dispatch(2)
        ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=[
                {
                    "product_id": self.product.id,
                    "quantity": 2,
                    "return_type": "delivery_returned",
                }
            ],
            source_order_id=self.order.id,
            shipping_code=self.order.shipping_code,
        )

        self._cancel()

        self.assertFalse(CancelledDispatchWatch.objects.exists())

    def test_sibling_transfer_closes_the_waiting_row(self):
        """Chuyển sổ sang đơn xuất lại → đơn cũ hết nợ, dòng theo dõi phải tự đóng.

        Ca prod 20/08: huỷ đơn lúc chưa có đơn anh em → mở watch; vài giờ sau
        sweep `repair_reissue_ledger` chuyển sổ xuất sang bản `.NN` → đơn cũ
        không còn nợ kho, nhưng watch kẹt 'waiting' vĩnh viễn (16 dòng rác) và
        kho đi đòi hàng không tồn tại. Đóng bằng lý do RIÊNG, không phải
        RETURNED: hàng không hề quay về kho, chỉ đổi đơn đứng tên.
        """
        self._dispatch(2)
        self._cancel()
        self.assertEqual(
            CancelledDispatchWatch.objects.get(order_id=self.order.id).status,
            WatchStatus.WAITING,
        )
        sibling = self._make_sibling()

        moved = StockSalesService.transfer_issue_to_sibling(
            order_id=self.order.id, sibling_order_id=sibling.id
        )

        self.assertTrue(moved)
        watch = CancelledDispatchWatch.objects.get(order_id=self.order.id)
        self.assertEqual(watch.status, WatchStatus.CLOSED)
        self.assertEqual(watch.closed_reason, CloseReason.REISSUED)
        self.assertIn(str(sibling.id), watch.closed_note)
        # Đơn anh em đang SỐNG, không có gì để theo dõi.
        self.assertFalse(
            CancelledDispatchWatch.objects.filter(order_id=sibling.id).exists()
        )

    def test_sibling_transfer_without_a_watch_is_a_noop(self):
        """Đơn chưa từng huỷ-sau-xuất (không có watch) → chuyển sổ không tạo watch mới."""
        self._dispatch(2)
        sibling = self._make_sibling()

        moved = StockSalesService.transfer_issue_to_sibling(
            order_id=self.order.id, sibling_order_id=sibling.id
        )

        self.assertTrue(moved)
        self.assertFalse(CancelledDispatchWatch.objects.exists())

    def test_reissued_reason_is_not_offered_to_humans(self):
        """Lý do hệ thống: người dùng không được tự chọn khi đóng tay."""
        self._dispatch(2)
        self._cancel()

        with self.assertRaises(ValueError):
            AwaitingReturnService.close(
                order_id=self.order.id,
                reason=CloseReason.REISSUED,
                user_id=self.actor.id,
            )

    def _force_stale_waiting(self) -> None:
        """Dựng lại đúng dữ liệu CŨ trên prod: sổ đã đổi nhưng watch còn 'waiting'."""
        CancelledDispatchWatch.objects.filter(order_id=self.order.id).update(
            status=WatchStatus.WAITING,
            closed_reason="",
            closed_note="",
            closed_at=None,
        )

    def test_reissue_label_survives_a_later_refresh(self):
        """Phiếu hoàn của đơn cũ bị sửa/huỷ sau đó → không được lật nhãn thành 'hàng đã về'."""
        self._dispatch(2)
        self._cancel()
        sibling = self._make_sibling()
        StockSalesService.transfer_issue_to_sibling(
            order_id=self.order.id, sibling_order_id=sibling.id
        )

        AwaitingReturnService.refresh_for_order(self.order.id)
        AwaitingReturnService.settle_if_returned(self.order.id)

        watch = CancelledDispatchWatch.objects.get(order_id=self.order.id)
        self.assertEqual(watch.status, WatchStatus.CLOSED)
        self.assertEqual(watch.closed_reason, CloseReason.REISSUED)

    def test_reconcile_closes_rows_whose_ledger_moved_to_a_sibling(self):
        """Backfill 16 dòng rác trên prod: hết nợ vì chuyển sổ → đóng, KHÔNG phải 'hàng đã về'."""
        self._dispatch(2)
        self._cancel()
        sibling = self._make_sibling()
        StockSalesService.transfer_issue_to_sibling(
            order_id=self.order.id, sibling_order_id=sibling.id
        )
        self._force_stale_waiting()

        call_command("reconcile_awaiting_return", stdout=StringIO())

        watch = CancelledDispatchWatch.objects.get(order_id=self.order.id)
        self.assertEqual(watch.status, WatchStatus.CLOSED)
        self.assertEqual(watch.closed_reason, CloseReason.REISSUED)

    def test_reconcile_marks_returned_when_goods_really_came_back(self):
        """Hết nợ vì phiếu hoàn đã ghi sổ → 'hàng đã về', không đóng nhầm lý do."""
        self._dispatch(2)
        self._cancel()
        ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=[
                {
                    "product_id": self.product.id,
                    "quantity": 2,
                    "return_type": "delivery_returned",
                }
            ],
            source_order_id=self.order.id,
            shipping_code=self.order.shipping_code,
        )
        self._force_stale_waiting()

        call_command("reconcile_awaiting_return", stdout=StringIO())

        watch = CancelledDispatchWatch.objects.get(order_id=self.order.id)
        self.assertEqual(watch.status, WatchStatus.RETURNED)

    def test_reconcile_keeps_rows_that_still_owe_stock(self):
        self._dispatch(2)
        self._cancel()

        call_command("reconcile_awaiting_return", stdout=StringIO())

        watch = CancelledDispatchWatch.objects.get(order_id=self.order.id)
        self.assertEqual(watch.status, WatchStatus.WAITING)

    def test_reconcile_dry_run_writes_nothing(self):
        self._dispatch(2)
        self._cancel()
        sibling = self._make_sibling()
        StockSalesService.transfer_issue_to_sibling(
            order_id=self.order.id, sibling_order_id=sibling.id
        )
        self._force_stale_waiting()

        call_command("reconcile_awaiting_return", "--dry-run", stdout=StringIO())

        watch = CancelledDispatchWatch.objects.get(order_id=self.order.id)
        self.assertEqual(watch.status, WatchStatus.WAITING)

    def test_close_records_the_decision_without_touching_stock(self):
        self._dispatch(2)
        self._cancel()
        before = StockMovement.objects.count()

        watch = AwaitingReturnService.close(
            order_id=self.order.id,
            reason="customer_kept",
            note="Khách xác nhận giữ hàng",
            user_id=self.actor.id,
        )

        self.assertEqual(watch.status, WatchStatus.CLOSED)
        self.assertEqual(watch.closed_reason, "customer_kept")
        self.assertEqual(watch.closed_by_id, self.actor.id)
        self.assertIsNotNone(watch.closed_at)
        # Đóng = ghi nhận quyết định, KHÔNG phải bút toán kho.
        self.assertEqual(StockMovement.objects.count(), before)

    def test_close_requires_note_for_other_reason(self):
        self._dispatch(2)
        self._cancel()
        with self.assertRaises(ValueError):
            AwaitingReturnService.close(
                order_id=self.order.id, reason="other", note="", user_id=self.actor.id
            )

    def test_goods_arriving_after_close_reopens_as_returned(self):
        self._dispatch(2)
        self._cancel()
        AwaitingReturnService.close(
            order_id=self.order.id,
            reason="lost_in_transit",
            note="",
            user_id=self.actor.id,
        )
        self._clear_outstanding()

        AwaitingReturnService.settle_if_returned(self.order.id)

        watch = CancelledDispatchWatch.objects.get(order_id=self.order.id)
        self.assertEqual(watch.status, WatchStatus.RETURNED)

    # -- Cảnh báo Lark tức thời khi đơn đã xuất kho bị huỷ ------------------
    #
    # Kho cần biết NGAY để đi đòi hàng, không đợi bản tin quá hạn 14 ngày
    # (`alert_awaiting_return_overdue`). Điều kiện bắn là "watch vừa được MỞ
    # MỚI" chứ không phải "đơn chuyển sang huỷ": chỉ dòng mở mới mới đồng
    # nghĩa hàng đang thật sự ngoài kho theo sổ.

    @override_settings(LARK_CANCEL_DISPATCH_BOT_WEBHOOK_URL="https://hook.example/cancel")
    def test_cancel_after_dispatch_pushes_lark_alert(self):
        self._dispatch(2)

        with patch("apps.dispatch.dispatch") as dispatch_mock:
            self._cancel()

        dispatch_mock.assert_called_once()
        self.assertEqual(
            dispatch_mock.call_args[0][0], "lark.notify_cancelled_after_dispatch"
        )
        self.assertEqual(dispatch_mock.call_args.kwargs["order_id"], self.order.id)

    @override_settings(LARK_CANCEL_DISPATCH_BOT_WEBHOOK_URL="https://hook.example/cancel")
    def test_alert_is_delayed_so_a_late_reissued_invoice_can_still_cancel_it(self):
        """Tin được hoãn: hoá đơn xuất lại `.NN` có thể về sau webhook huỷ (cron import),
        cửa chặn thật nằm ở task ngay trước lúc gửi nên phải cho nó thời gian."""
        self._dispatch(2)

        with patch("apps.dispatch.dispatch") as dispatch_mock:
            self._cancel()

        self.assertEqual(dispatch_mock.call_args.kwargs["countdown"], 600)

    @override_settings(LARK_CANCEL_DISPATCH_BOT_WEBHOOK_URL="https://hook.example/cancel")
    def test_cancel_before_dispatch_pushes_no_alert(self):
        with patch("apps.dispatch.dispatch") as dispatch_mock:
            self._cancel()

        dispatch_mock.assert_not_called()

    @override_settings(LARK_CANCEL_DISPATCH_BOT_WEBHOOK_URL="https://hook.example/cancel")
    def test_repeated_cancel_event_pushes_only_one_alert(self):
        """Webhook KiotViet bắn lại sự kiện huỷ → watch đã có, không báo động lần hai."""
        self._dispatch(2)
        self._cancel()

        with patch("apps.dispatch.dispatch") as dispatch_mock:
            self._cancel()

        dispatch_mock.assert_not_called()

    @override_settings(LARK_CANCEL_DISPATCH_BOT_WEBHOOK_URL="")
    def test_no_alert_when_bot_is_not_configured(self):
        self._dispatch(2)

        with patch("apps.dispatch.dispatch") as dispatch_mock:
            self._cancel()

        dispatch_mock.assert_not_called()
        # Webhook trống chỉ tắt cảnh báo — hàng đợi đòi hàng vẫn phải mở.
        self.assertTrue(CancelledDispatchWatch.objects.filter(order_id=self.order.id).exists())


class WatchReopenTests(TestCase):
    """Watch 'đơn huỷ chờ hàng về' phải phản ánh SỰ THẬT của sổ sau huỷ/điều chỉnh phiếu hoàn.

    Hai đường làm watch nói dối vĩnh viễn:
    - Huỷ nhóm đơn hoàn đã ghi sổ: bút toán đảo → hàng lại "ngoài kho" nhưng
      ``open_for_order`` (get_or_create) không reset RETURNED → đơn biến mất khỏi
      danh sách đòi hàng dù hàng vẫn nợ kho.
    - ``adjust_entry`` không settle/reopen: hoàn thiếu rồi điều chỉnh đủ → kẹt
      WAITING; hoàn đủ rồi điều chỉnh hạ → giữ RETURNED sai.
    """

    def setUp(self):
        from apps.inventory import subscribers

        subscribers.register()
        self.warehouse = Warehouse.objects.create(code="WH-RWO", name="Kho RWO")
        self.product = Product.objects.create(name="SP RWO", sku="SKU-RWO1")
        self.channel = Channel.objects.create(code="ch-rwo", name="RWO Channel")
        self.customer = Customer.objects.create(
            full_name="Khach RWO",
            phone_raw="0912345705",
            phone_normalized="0912345705",
            code="CUST-RWO",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            order_code="ORD-RWO-1",
            shipping_code="TRACK-RWO-1",
            name="Khach RWO",
            phone="0912345705",
            status=OrderStatus.IN_DELIVERY,
            fulfillment_warehouse=self.warehouse,
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name="SP RWO",
            sku="SKU-RWO1",
            quantity=2,
            unit_price=50000,
            line_total=100000,
        )
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=10,
            movement_type=MovementType.RECEIPT,
        )
        StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=2)],
        )
        previous = int(self.order.status)
        Order.objects.filter(pk=self.order.id).update(status=OrderStatus.CANCELLED)
        event_bus.publish(
            OrderStatusChanged(
                order_id=self.order.id,
                old_status=previous,
                new_status=int(OrderStatus.CANCELLED),
            )
        )

    def _watch_status(self) -> str:
        return CancelledDispatchWatch.objects.get(order_id=self.order.id).status

    def _post_return(self, qty: int):
        return ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=[
                {
                    "product_id": self.product.id,
                    "quantity": qty,
                    "return_type": "delivery_returned",
                }
            ],
            source_order_id=self.order.id,
            shipping_code="TRACK-RWO-1",
        )

    def test_cancel_posted_entry_reopens_watch(self):
        entry = self._post_return(2)
        assert self._watch_status() == WatchStatus.RETURNED

        ReturnEntryService.cancel_entry(entry_id=entry.id)

        assert self._watch_status() == WatchStatus.WAITING

    def test_adjust_up_to_full_settles_watch(self):
        entry = self._post_return(1)
        assert self._watch_status() == WatchStatus.WAITING

        ReturnEntryService.adjust_entry(
            entry_id=entry.id,
            lines=[{"product_id": self.product.id, "quantity": 2}],
        )

        assert self._watch_status() == WatchStatus.RETURNED

    def test_adjust_down_reopens_watch(self):
        entry = self._post_return(2)
        assert self._watch_status() == WatchStatus.RETURNED

        ReturnEntryService.adjust_entry(
            entry_id=entry.id,
            lines=[{"product_id": self.product.id, "quantity": 1}],
        )

        assert self._watch_status() == WatchStatus.WAITING


# ---------------------------------------------------------------------------
# API tab 'Chờ hàng hoàn về': danh sách đọc số từ sổ, đóng đơn cần quyền
# returns:approve. (gốc: test_awaiting_return_api.py)
# ---------------------------------------------------------------------------

LIST_URL = "/api/inventory/dispatch-history/awaiting-return"


@pytest.fixture
def setup_watch():
    warehouse = Warehouse.objects.create(code="WH-API", name="Kho API")
    product = Product.objects.create(name="SP API", sku="SKU-API1")
    channel = Channel.objects.create(code="ch-api", name="API Channel")
    customer = Customer.objects.create(
        full_name="Khach API",
        phone_raw="0912345704",
        phone_normalized="0912345704",
        code="CUST-API",
    )
    order = Order.objects.create(
        channel=channel,
        customer=customer,
        order_code="ORD-API-1",
        invoice_code="HD-API-1",
        shipping_code="TRACK-API-1",
        name="Khach API",
        phone="0912345704",
        status=OrderStatus.CANCELLED,
        fulfillment_warehouse=warehouse,
    )
    StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=10,
        movement_type=MovementType.RECEIPT,
    )
    StockSalesService.issue_for_order(
        order_id=order.id,
        warehouse_id=warehouse.id,
        lines=[SaleLine(product_id=product.id, quantity=2)],
    )
    CancelledDispatchWatch.objects.create(
        order_id=order.id,
        warehouse_id=warehouse.id,
        cancelled_at=timezone.now(),
        status=WatchStatus.WAITING,
    )
    return {"order": order, "product": product, "warehouse": warehouse}


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_list_returns_row_with_quantity_from_ledger(setup_watch):
    admin = make_admin("awaiting-api-admin")
    response = _client(admin).get(LIST_URL)

    assert response.status_code == 200
    assert response.data["count"] == 1
    row = response.data["results"][0]
    assert row["order_id"] == setup_watch["order"].id
    assert row["shipping_code"] == "TRACK-API-1"
    assert row["status"] == "waiting"
    assert row["lines"] == [
        {"product_id": setup_watch["product"].id, "sku": "SKU-API1", "qty": 2}
    ]
    assert row["days_waiting"] == 0


@pytest.mark.django_db
def test_list_filters_by_warehouse(setup_watch):
    admin = make_admin("awaiting-api-admin-2")
    other = Warehouse.objects.create(code="WH-API-2", name="Kho khác")
    response = _client(admin).get(f"{LIST_URL}?warehouse_id={other.id}")
    assert response.data["count"] == 0


@pytest.mark.django_db
def test_close_marks_row_closed(setup_watch):
    admin = make_admin("awaiting-api-admin-3")
    order_id = setup_watch["order"].id
    response = _client(admin).post(
        f"{LIST_URL}/{order_id}/close",
        {"reason": "customer_kept", "note": "Khách giữ hàng"},
        format="json",
    )

    assert response.status_code == 200
    watch = CancelledDispatchWatch.objects.get(order_id=order_id)
    assert watch.status == WatchStatus.CLOSED
    assert watch.closed_reason == "customer_kept"
    assert watch.closed_by_id == admin.id


@pytest.mark.django_db
def test_close_rejects_other_reason_without_note(setup_watch):
    admin = make_admin("awaiting-api-admin-4")
    response = _client(admin).post(
        f"{LIST_URL}/{setup_watch['order'].id}/close",
        {"reason": "other", "note": ""},
        format="json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_close_unknown_order_is_404(setup_watch):
    admin = make_admin("awaiting-api-admin-5")
    response = _client(admin).post(
        f"{LIST_URL}/999999/close",
        {"reason": "customer_kept", "note": ""},
        format="json",
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_list_requires_authentication(setup_watch):
    response = APIClient().get(LIST_URL)
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Cảnh báo Lark: đơn huỷ đã xuất kho mà hàng chưa về sau 14 ngày.
#
# Task chỉ NHẮC, không tự đóng đơn — quyết định coi như mất hàng phải có người
# chịu trách nhiệm (spec §5). (gốc: test_awaiting_return_alert.py)
# ---------------------------------------------------------------------------

_SEND = "apps.inventory.tasks.send_stock_negative_alert"


@pytest.fixture
def alert_warehouse():
    return Warehouse.objects.create(code="WH-ALR", name="Kho ALR")


@pytest.fixture
def alert_orders():
    channel = Channel.objects.create(code="ch-alr", name="ALR Channel")
    built = []
    for index in range(3):
        customer = Customer.objects.create(
            full_name=f"Khach ALR {index}",
            phone_raw=f"09123458{index:02d}",
            phone_normalized=f"09123458{index:02d}",
            code=f"CUST-ALR-{index}",
        )
        built.append(
            Order.objects.create(
                channel=channel,
                customer=customer,
                order_code=f"ORD-ALR-{index}",
                shipping_code=f"TRACK-ALR-{index}",
                name=f"Khach ALR {index}",
                phone=f"09123458{index:02d}",
                status=OrderStatus.CANCELLED,
            )
        )
    return built


def _watch(order, warehouse, *, days: int, status=WatchStatus.WAITING):
    return CancelledDispatchWatch.objects.create(
        order_id=order.id,
        warehouse_id=warehouse.id,
        cancelled_at=timezone.now() - timedelta(days=days),
        status=status,
    )


@pytest.mark.django_db
def test_alert_lists_only_overdue_waiting_rows(alert_orders, alert_warehouse):
    _watch(alert_orders[0], alert_warehouse, days=20)
    _watch(alert_orders[1], alert_warehouse, days=3)
    _watch(alert_orders[2], alert_warehouse, days=30, status=WatchStatus.CLOSED)

    with patch(_SEND) as send:
        count = alert_awaiting_return_overdue()

    assert count == 1
    send.assert_called_once()
    message = send.call_args[0][0]
    assert alert_orders[0].shipping_code in message
    assert alert_orders[1].shipping_code not in message
    assert alert_orders[2].shipping_code not in message


@pytest.mark.django_db
def test_no_alert_when_nothing_overdue(alert_orders, alert_warehouse):
    _watch(alert_orders[0], alert_warehouse, days=2)

    with patch(_SEND) as send:
        count = alert_awaiting_return_overdue()

    assert count == 0
    send.assert_not_called()


@pytest.mark.django_db
def test_alert_does_not_close_anything(alert_orders, alert_warehouse):
    _watch(alert_orders[0], alert_warehouse, days=40)

    with patch(_SEND):
        alert_awaiting_return_overdue()

    watch = CancelledDispatchWatch.objects.get(order_id=alert_orders[0].id)
    assert watch.status == WatchStatus.WAITING


# ---------------------------------------------------------------------------
# Cảnh báo Lark tức thời: nội dung tin bắn cho kho ngay khi đơn đã xuất bị huỷ.
#
# Kho chỉ cần đủ để đi đòi hàng: mã đơn, mã vận đơn, kho giao, và danh sách SKU
# theo dòng hàng của đơn (bổ sung 2026-08-26).
# ---------------------------------------------------------------------------

_SEND_CANCEL = (
    "apps.orders.infrastructure.tasks.order_tasks_impl.lark_notification_tasks"
    ".send_cancelled_dispatch_alert"
)


@pytest.fixture
def cancelled_order(alert_warehouse):
    channel = Channel.objects.create(code="ch-cxl", name="CXL Channel")
    customer = Customer.objects.create(
        full_name="Khach CXL",
        phone_raw="0912345810",
        phone_normalized="0912345810",
        code="CUST-CXL",
    )
    return Order.objects.create(
        channel=channel,
        customer=customer,
        order_code="ORD-CXL-1",
        invoice_code="HD-CXL-1",
        shipping_code="TRACK-CXL-1",
        name="Khach CXL",
        phone="0912345810",
        status=OrderStatus.CANCELLED,
        fulfillment_warehouse=alert_warehouse,
    )


@pytest.mark.django_db
def test_alert_message_carries_order_shipping_and_warehouse(cancelled_order, alert_warehouse):
    with patch(_SEND_CANCEL) as send:
        notify_lark_cancelled_after_dispatch(cancelled_order.id)

    send.assert_called_once()
    message = send.call_args[0][0]
    assert cancelled_order.invoice_code in message
    assert cancelled_order.shipping_code in message
    assert alert_warehouse.name in message


@pytest.mark.django_db
def test_alert_renders_dash_when_shipping_code_is_missing(cancelled_order):
    Order.objects.filter(pk=cancelled_order.id).update(shipping_code="")

    with patch(_SEND_CANCEL) as send:
        notify_lark_cancelled_after_dispatch(cancelled_order.id)

    assert "Mã vận đơn: —" in send.call_args[0][0]


@pytest.mark.django_db
def test_alert_is_skipped_for_an_unknown_order():
    with patch(_SEND_CANCEL) as send:
        notify_lark_cancelled_after_dispatch(999_999_999)

    send.assert_not_called()


def _similar_invoice_order(order, *, invoice_code: str, shipping_code: str, status):
    """Đơn 'hoá đơn tương tự' — bản `<gốc>.NN` mà KiotViet sinh ra khi đổi hoá đơn."""
    return Order.objects.create(
        channel=order.channel,
        customer=order.customer,
        order_code=f"{order.order_code}-SIB",
        invoice_code=invoice_code,
        shipping_code=shipping_code,
        name=order.name,
        phone=order.phone,
        status=status,
        fulfillment_warehouse=order.fulfillment_warehouse,
    )


@pytest.mark.django_db
def test_no_alert_when_a_similar_invoice_is_live(cancelled_order):
    """Ca giảm giá COD: đơn không thật sự bị huỷ sau xuất kho, KiotViet chỉ đổi sang
    hoá đơn `.01` cùng mã vận đơn — hàng vẫn đang giao, không có gì để đòi về."""
    _similar_invoice_order(
        cancelled_order,
        invoice_code="HD-CXL-1.01",
        shipping_code="TRACK-CXL-1",
        status=OrderStatus.IN_DELIVERY,
    )

    with patch(_SEND_CANCEL) as send:
        notify_lark_cancelled_after_dispatch(cancelled_order.id)

    send.assert_not_called()


@pytest.mark.django_db
def test_alert_fires_when_the_similar_invoice_is_cancelled_too(cancelled_order):
    """Cả hai hoá đơn cùng huỷ = không còn đơn nào đang giao → hàng thật sự ngoài kho."""
    _similar_invoice_order(
        cancelled_order,
        invoice_code="HD-CXL-1.01",
        shipping_code="TRACK-CXL-1",
        status=OrderStatus.CANCELLED,
    )

    with patch(_SEND_CANCEL) as send:
        notify_lark_cancelled_after_dispatch(cancelled_order.id)

    send.assert_called_once()
    message = send.call_args[0][0]
    assert cancelled_order.invoice_code in message
    assert cancelled_order.shipping_code in message


@pytest.mark.django_db
def test_alert_fires_when_the_similar_invoice_has_another_shipping_code(cancelled_order):
    """Trùng gốc hoá đơn nhưng KHÁC mã vận đơn = hai lô hàng khác nhau, không phải đổi
    hoá đơn — giữ nguyên cảnh báo (cùng luật với nhánh chuyển sổ)."""
    _similar_invoice_order(
        cancelled_order,
        invoice_code="HD-CXL-1.01",
        shipping_code="TRACK-CXL-9",
        status=OrderStatus.IN_DELIVERY,
    )

    with patch(_SEND_CANCEL) as send:
        notify_lark_cancelled_after_dispatch(cancelled_order.id)

    send.assert_called_once()
    message = send.call_args[0][0]
    assert cancelled_order.invoice_code in message
    assert cancelled_order.shipping_code in message
    assert "TRACK-CXL-9" not in message  # lô khác không bị kéo vào cảnh báo của lô này


def _add_item(order, *, sku: str, quantity: int):
    product = Product.objects.create(name=f"SP {sku}", sku=sku)
    return OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        sku=sku,
        quantity=quantity,
        unit_price=10000,
        line_total=10000 * quantity,
    )


@pytest.mark.django_db
def test_alert_lists_every_sku_with_its_quantity(cancelled_order):
    _add_item(cancelled_order, sku="SKU-CXL-A", quantity=2)
    _add_item(cancelled_order, sku="SKU-CXL-B", quantity=1)

    with patch(_SEND_CANCEL) as send:
        notify_lark_cancelled_after_dispatch(cancelled_order.id)

    message = send.call_args[0][0]
    assert "SKU-CXL-A x2" in message
    assert "SKU-CXL-B x1" in message


@pytest.mark.django_db
def test_alert_truncates_a_very_long_item_list(cancelled_order):
    for index in range(12):
        _add_item(cancelled_order, sku=f"SKU-CXL-{index:02d}", quantity=1)

    with patch(_SEND_CANCEL) as send:
        notify_lark_cancelled_after_dispatch(cancelled_order.id)

    message = send.call_args[0][0]
    assert "SKU-CXL-00 x1" in message
    assert "SKU-CXL-11" not in message
    assert "… và 2 SKU nữa" in message


@pytest.mark.django_db
def test_alert_renders_dash_when_the_order_has_no_items(cancelled_order):
    with patch(_SEND_CANCEL) as send:
        notify_lark_cancelled_after_dispatch(cancelled_order.id)

    assert "Hàng: —" in send.call_args[0][0]
