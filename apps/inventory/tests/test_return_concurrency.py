"""Concurrency của luồng nhập hoàn: race quét trùng mã vận đơn và thứ tự khoá.

Gộp từ test_return_shipcode_race.py (TransactionTestCase + threading) và
test_return_entry_lock_order.py (pytest django_db(transaction=True) + threading).
Giữ nguyên cả hai style — KHÔNG gộp threading test vào TestCase thường.
"""
from __future__ import annotations

import threading
import time
import uuid

import pytest
from django.db import connection, transaction
from django.test import TransactionTestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.return_entry_service import (
    ReturnEntryService,
)
from apps.inventory.application.services.stock_sales_service import (
    SaleLine,
    StockSalesService,
)
from apps.inventory.models import (
    MovementType,
    ReturnEntry,
    ReturnStatus,
    ReturnType,
    StockItem,
    StockMovement,
    StockState,
)
from apps.orders.models import Order
from apps.catalog.models import Product
from apps.warehouses.models import Warehouse

_SHIP_CODE = "RACE-EXT-001"


def _wait_until_blocked_or_done(pid: int, done, *, timeout: float = 5.0) -> None:
    """Chờ backend `pid` kẹt ở khoá DB (wait_event_type='Lock') hoặc đã xong.

    Thay cho `time.sleep(1.0)` cố định: đọc trạng thái thật của request kia qua
    pg_stat_activity, thường xong trong vài chục ms; quá `timeout` thì cứ đi tiếp
    để test vẫn ra kết luận (assert phía dưới sẽ nói đúng/sai).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not done():
        with connection.cursor() as cur:
            cur.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [pid])
            row = cur.fetchone()
        if row and row[0] == "Lock":
            return
        time.sleep(0.01)


@pytest.mark.slow  # race/timeout thật ≥5 s — CI vẫn chạy
class ExternalReturnShipcodeRaceTests(TransactionTestCase):
    """Race 2 request quét cùng mã vận đơn KHÔNG có đơn nguồn (trả ngoài).

    Constraint `returnentry_shipcode_draft_uniq` chỉ phủ status=draft; luồng quét
    tạo-rồi-ghi-sổ trong 1 transaction nên tại thời điểm commit dòng đã POSTED,
    không còn thoả predicate của partial index → 2 transaction song song đều lọt
    → RETURN_IN cộng tồn 2 lần. Advisory lock theo shipping_code phải serialize
    được ca này: request sau thấy row posted của request trước → ValueError.
    """

    reset_sequences = True

    def setUp(self):
        self.wh = Warehouse.objects.create(code="W-RACE", name="Kho race")
        self.product = Product.objects.create(name="SP race", sku="RACE-1")

    def _payload(self):
        return dict(
            warehouse_id=self.wh.id,
            lines=[
                {
                    "product_id": self.product.id,
                    "quantity": 2,
                    "return_type": ReturnType.EXTERNAL_RETURN,
                }
            ],
            shipping_code=_SHIP_CODE,
            source_order_id=None,
        )

    def test_concurrent_external_return_scans_credit_stock_once(self):
        entry_created = threading.Event()
        results: dict[str, object] = {}

        fast_pid_ready = threading.Event()

        def slow_scanner():
            # Mô phỏng request 1: tạo entry (draft, chưa commit) rồi giữ cửa sổ
            # chưa-commit cho tới khi request 2 đã vượt qua check trùng app-level
            # và ĐANG kẹt ở DB (INSERT draft chờ unique partial index của dòng draft
            # chưa commit này), rồi mới ghi sổ + commit. Trước đây `sleep(1.0)`;
            # giờ chờ đúng trạng thái thật qua pg_stat_activity — không đoán thời gian.
            try:
                with transaction.atomic():
                    entry = ReturnEntryService.create_entry(**self._payload())
                    entry_created.set()
                    assert fast_pid_ready.wait(timeout=5), "request 2 chưa lấy được pid"
                    _wait_until_blocked_or_done(results["pid_b"], lambda: "b" in results)
                    ReturnEntryService.post_entry(entry_id=entry.id)
                results["a"] = "ok"
            except ValueError as exc:
                results["a"] = exc
            finally:
                entry_created.set()
                connection.close()

        def fast_scanner():
            # `entry_created` chỉ được set SAU create_entry trong atomic của request 1
            # → tới đây request 1 chắc chắn đang ở cửa sổ chưa-commit, không cần chờ thêm.
            entry_created.wait(timeout=5)
            with connection.cursor() as cur:
                cur.execute("SELECT pg_backend_pid()")
                results["pid_b"] = cur.fetchone()[0]
            fast_pid_ready.set()
            try:
                ReturnEntryService.create_and_post_entry(**self._payload())
                results["b"] = "ok"
            except ValueError as exc:
                results["b"] = exc
            finally:
                connection.close()

        t1 = threading.Thread(target=slow_scanner)
        t2 = threading.Thread(target=fast_scanner)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        outcomes = [results.get("a"), results.get("b")]
        oks = [o for o in outcomes if o == "ok"]
        assert len(oks) == 1, f"đúng 1 request được ghi sổ, thực tế: {outcomes}"

        posted = ReturnEntry.objects.filter(
            shipping_code=_SHIP_CODE, status="posted"
        )
        assert posted.count() == 1

        credited = StockMovement.objects.filter(
            movement_type=MovementType.RETURN_IN, product=self.product
        ).count()
        assert credited == 1, "1 kiện hàng chỉ được cộng tồn đúng 1 lần"
        item = StockItem.objects.get(
            warehouse=self.wh, product=self.product, state=StockState.SELLABLE
        )
        assert item.quantity == 2


# ── Thứ tự khoá (từ test_return_entry_lock_order.py) ──────────────────────────
# post_entry/cancel_entry phải khoá Order row TRƯỚC advisory 61001 (lock_order_stock),
# cùng thứ tự với luồng dispatch (Order.select_for_update() rồi mới vào issue_for_order lấy
# advisory). Đảo ngược ở 1 trong 2 luồng tạo chu trình chờ chéo (deadlock) khi dispatch và
# post-return chạy đúng lúc trên cùng đơn — xem apps/inventory/application/services/
# return_entry_service.py (post_entry/cancel_entry) và order_service_commands.py:515.


def _setup_order_with_outstanding():
    channel = Channel.objects.create(
        code=f"return-lock-{uuid.uuid4().hex[:8]}", name="Return Lock Order",
    )
    customer = Customer.objects.create(
        full_name="KH Return Lock",
        phone_raw=f"09{uuid.uuid4().hex[:8]}",
        phone_normalized=f"09{uuid.uuid4().hex[:8]}",
    )
    warehouse = Warehouse.objects.create(
        code=f"WH-RL-{uuid.uuid4().hex[:6]}", name="Kho Return Lock"
    )
    product = Product.objects.create(sku=f"SKU-RL-{uuid.uuid4().hex[:8]}", name="SP Return Lock")
    from apps.inventory.application.services.stock_command_service import StockCommandService
    from apps.inventory.models import MovementType

    StockCommandService.record_movement(
        warehouse_id=warehouse.id, product_id=product.id, qty_delta=10,
        movement_type=MovementType.RECEIPT,
    )
    order = Order.objects.create(
        channel=channel, customer=customer, name="KH Return Lock", phone="0909000111",
        fulfillment_warehouse=warehouse, order_source="landing",
    )
    StockSalesService.issue_for_order(
        order_id=order.id, warehouse_id=warehouse.id,
        lines=[SaleLine(product_id=product.id, quantity=2)],
    )
    entry = ReturnEntryService.create_entry(
        warehouse_id=warehouse.id,
        source_order_id=order.id,
        lines=[{
            "product_id": product.id,
            "quantity": 2,
            "return_type": ReturnType.DELIVERY_RETURNED,
        }],
    )
    return order, entry


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
def test_post_entry_queues_behind_a_held_order_row_lock():
    """Nếu post_entry khoá advisory TRƯỚC Order row (thứ tự cũ), nó sẽ chạy xong ngay
    (không đụng Order row) thay vì đứng chờ — test này lộ ra hồi quy đó."""
    order, entry = _setup_order_with_outstanding()

    holding = threading.Event()  # đã giữ khoá Order row, CHƯA commit
    release = threading.Event()
    errors: list[Exception] = []

    def order_row_holder():
        try:
            with transaction.atomic():
                Order.objects.select_for_update().filter(pk=order.id).first()
                holding.set()
                release.wait(10)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    def poster():
        try:
            ReturnEntryService.post_entry(entry_id=entry.id)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    t1 = threading.Thread(target=order_row_holder)
    t1.start()
    assert holding.wait(10), "thread giữ khoá Order row không chạy được"

    t2 = threading.Thread(target=poster)
    t2.start()
    t2.join(1.0)
    assert t2.is_alive(), (
        "post_entry phải đứng chờ khoá Order row (đã bị giữ) TRƯỚC khi ghi sổ — "
        "nếu nó chạy xong ngay nghĩa là advisory đang bị lấy TRƯỚC Order row (thứ tự cũ)."
    )

    release.set()
    t1.join(10)
    t2.join(10)
    assert not errors, errors

    entry.refresh_from_db()
    assert entry.status == ReturnStatus.POSTED


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
def test_cancel_entry_queues_behind_a_held_order_row_lock():
    order, entry = _setup_order_with_outstanding()
    entry = ReturnEntryService.post_entry(entry_id=entry.id)

    holding = threading.Event()
    release = threading.Event()
    errors: list[Exception] = []

    def order_row_holder():
        try:
            with transaction.atomic():
                Order.objects.select_for_update().filter(pk=order.id).first()
                holding.set()
                release.wait(10)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    def canceller():
        try:
            ReturnEntryService.cancel_entry(entry_id=entry.id)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    t1 = threading.Thread(target=order_row_holder)
    t1.start()
    assert holding.wait(10), "thread giữ khoá Order row không chạy được"

    t2 = threading.Thread(target=canceller)
    t2.start()
    t2.join(1.0)
    assert t2.is_alive(), (
        "cancel_entry phải đứng chờ khoá Order row (đã bị giữ) trước khi hoàn tồn."
    )

    release.set()
    t1.join(10)
    t2.join(10)
    assert not errors, errors

    entry.refresh_from_db()
    assert entry.status == ReturnStatus.CANCELLED
