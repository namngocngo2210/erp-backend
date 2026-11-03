from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings

from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import (
    SaleLine,
    StockSalesService,
)
from apps.inventory.contracts.stock_events import StockWentNegative
from apps.inventory.models import MovementType, StockItem, StockMovement, StockState
from apps.inventory.subscribers import _describe_stock_negative
from apps.catalog.models import Product
from apps.warehouses.models import Warehouse


class RecordMovementTests(TestCase):
    def setUp(self):
        self.wh = Warehouse.objects.create(code="W-CMD", name="Kho CMD")
        self.product = Product.objects.create(name="SP CMD", sku="CMD-1")

    def _record(self, qty_delta, **kw):
        return StockCommandService.record_movement(
            warehouse_id=self.wh.id,
            product_id=self.product.id,
            qty_delta=qty_delta,
            movement_type=kw.pop("movement_type", MovementType.RECEIPT),
            **kw,
        )

    def test_first_movement_creates_stock_item_and_matches_balance(self):
        result = self._record(10)
        item = StockItem.objects.get(
            warehouse=self.wh, product=self.product, state=StockState.SELLABLE
        )
        assert item.quantity == 10
        assert result.balance_after == 10
        assert result.went_negative is False
        assert StockMovement.objects.get(id=result.movement_id).balance_after == 10

    def test_accumulates_across_movements(self):
        self._record(10)
        self._record(5)
        r = self._record(-3)
        assert r.balance_after == 12
        item = StockItem.objects.get(
            warehouse=self.wh, product=self.product, state=StockState.SELLABLE
        )
        assert item.quantity == 12

    def test_zero_delta_raises(self):
        with pytest.raises(ValueError):
            self._record(0)

    def test_oversell_sets_went_negative(self):
        r = self._record(-4)
        assert r.went_negative is True
        assert r.balance_after == -4

    def test_state_isolation(self):
        self._record(10, state=StockState.SELLABLE)
        self._record(2, state=StockState.DEFECT)
        assert StockItem.objects.get(
            warehouse=self.wh, product=self.product, state=StockState.SELLABLE
        ).quantity == 10
        assert StockItem.objects.get(
            warehouse=self.wh, product=self.product, state=StockState.DEFECT
        ).quantity == 2


@pytest.mark.slow  # race/timeout thật ≥5 s — CI vẫn chạy
class RecordMovementConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.wh = Warehouse.objects.create(code="W-CC", name="Kho CC")
        self.product = Product.objects.create(name="SP CC", sku="CC-1")
        StockCommandService.record_movement(
            warehouse_id=self.wh.id, product_id=self.product.id,
            qty_delta=100, movement_type=MovementType.RECEIPT,
        )

    def test_concurrent_decrements_no_lost_update(self):
        from django.db import connection

        threads = []
        errors = []

        def worker():
            try:
                StockCommandService.record_movement(
                    warehouse_id=self.wh.id, product_id=self.product.id,
                    qty_delta=-1, movement_type=MovementType.SALE,
                    state=StockState.SELLABLE,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                connection.close()

        for _ in range(20):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors
        item = StockItem.objects.get(
            warehouse=self.wh, product=self.product, state=StockState.SELLABLE
        )
        assert item.quantity == 80  # 100 - 20*1, không mất update


# ---------------------------------------------------------------------------
# Tests cho luồng cảnh báo tồn âm (StockWentNegative → Lark alert).
#
# Kho vẫn cho phép ghi nhận tồn âm (thiết kế: xuất-trước-nhập-sau), nhưng mỗi lần
# tồn xuống âm phải phát cảnh báo tới team kho/kế toán — luồng này trước đây chỉ
# log warning, không có test bao phủ.
# ---------------------------------------------------------------------------


class StockNegativeAlertTests(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-NEG", name="Kho Âm")
        self.product = Product.objects.create(name="SP Âm", sku="SKU-NEG")
        # Chỉ nhập 1 để lần xuất kế tiếp chắc chắn làm tồn xuống âm.
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=1,
            movement_type=MovementType.RECEIPT,
        )

    def test_issue_beyond_stock_triggers_alert(self):
        """Xuất vượt tồn → phát StockWentNegative → subscriber gửi Lark alert."""
        with patch(
            "apps.inventory.subscribers.send_stock_negative_alert"
        ) as mock_alert:
            with self.captureOnCommitCallbacks(execute=True):
                warnings = StockSalesService.issue_for_order(
                    order_id=9001,
                    warehouse_id=self.warehouse.id,
                    lines=[SaleLine(product_id=self.product.id, quantity=3)],
                )

        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].balance_after, -2)
        mock_alert.assert_called_once()
        message = mock_alert.call_args.args[0]
        self.assertIn("Kho Âm", message)
        self.assertIn("SKU-NEG", message)
        self.assertIn("-2", message)

    def test_no_alert_when_stock_stays_non_negative(self):
        """Xuất trong tồn khả dụng → không phát cảnh báo."""
        with patch(
            "apps.inventory.subscribers.send_stock_negative_alert"
        ) as mock_alert:
            with self.captureOnCommitCallbacks(execute=True):
                StockSalesService.issue_for_order(
                    order_id=9002,
                    warehouse_id=self.warehouse.id,
                    lines=[SaleLine(product_id=self.product.id, quantity=1)],
                )
        mock_alert.assert_not_called()

    def test_describe_resolves_labels(self):
        event = StockWentNegative(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            balance_after=-5,
        )
        message = _describe_stock_negative(event)
        self.assertIn("Kho Âm", message)
        self.assertIn("SKU-NEG", message)
        self.assertIn("-5", message)

    def test_describe_falls_back_to_ids_when_missing(self):
        event = StockWentNegative(warehouse_id=999999, product_id=888888, balance_after=-1)
        message = _describe_stock_negative(event)
        self.assertIn("#999999", message)
        self.assertIn("#888888", message)


class StockNegativeNotifierRoutingTests(SimpleTestCase):
    @override_settings(
        LARK_INVENTORY_BOT_WEBHOOK_URL="https://lark.example/inventory-hook",
        LARK_INVENTORY_BOT_SECRET="",
        LARK_INVENTORY_BOT_TIMEOUT_SECONDS=5,
    )
    def test_alert_posts_to_inventory_webhook(self):
        from apps.orders.infrastructure import lark_notifier

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"code": 0}

        with patch.object(lark_notifier, "_client") as mock_client:
            mock_client.post.return_value = response
            ok = lark_notifier.send_stock_negative_alert("tồn âm test")

        self.assertTrue(ok)
        mock_client.post.assert_called_once()
        url = mock_client.post.call_args.args[0]
        self.assertEqual(url, "https://lark.example/inventory-hook")
        body = mock_client.post.call_args.kwargs["json"]
        self.assertEqual(body["content"]["text"], "tồn âm test")

    @override_settings(LARK_INVENTORY_BOT_WEBHOOK_URL="")
    def test_alert_noop_when_webhook_unset(self):
        from apps.orders.infrastructure import lark_notifier

        with patch.object(lark_notifier, "_client") as mock_client:
            ok = lark_notifier.send_stock_negative_alert("tồn âm test")

        self.assertFalse(ok)
        mock_client.post.assert_not_called()
