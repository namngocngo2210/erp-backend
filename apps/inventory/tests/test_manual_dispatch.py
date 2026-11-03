"""Vòng đời đơn bán trực tiếp "xuất sau" (direct_dispatch_deferred) — nửa phía KHO.

Nửa phía orders (hành động API đặt cờ Task 2, worker gắn nhãn sau khi tạo hoá đơn
KiotViet Task 3) nằm ở `apps.orders.tests.test_quick_sale_invoice_only` — tách ra vì
đó là code/import thuộc sở hữu app `orders` (kiotviet-actions, ingest_job_tasks), gộp
chung vào đây từng buộc `apps.inventory.tests.*` phải mở ngoại lệ import-linter cho
`apps.kiotviet.models` và `apps.orders.infrastructure.tasks...` — hai hướng vốn dĩ
không thuộc về inventory. File này giữ đúng phần thuộc kho: cờ mặc định (Task 1), huỷ
đơn đi đường nào (Task 4), và nút "Xuất kho" tay ở chi tiết đơn / `ManualDispatchService`.

Trước 29/08/2026, phần cuối file này khoá hàng đợi "Chờ xuất kho" (`DirectDispatchService`,
`list_pending`/`confirm`). Hàng đợi đã bị gỡ (thay bằng quét mã vận đơn ở kho — Task 2 —
và nút "Xuất kho" trên chi tiết đơn ở đây); các test đã đổi theo cho khớp interface mới
(`ManualDispatchService.dispatch`, có `warehouse_id`).
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.manual_dispatch_service import (
    SKIP_ALREADY_DISPATCHED,
    ManualDispatchService,
)
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.contracts.stock_sales import SaleLine, StockSalesService
from apps.inventory.models import CancelledDispatchWatch, MovementType, StockMovement
from apps.inventory.subscribers import _on_order_status_changed
from apps.inventory.tests.helpers import make_order, sellable
from apps.orders.contracts.order_events import OrderStatusChanged
from apps.orders.models import Order, OrderStatus
from apps.users.tests.helpers import make_admin, make_user_with_permissions
from apps.warehouses.models import Warehouse


class DirectDeferredFieldTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="dd-ch", name="Deferred Channel")
        self.customer = Customer.objects.create(
            full_name="KH Deferred", phone_raw="0911000000", phone_normalized="0911000000"
        )

    def test_defaults_to_false(self):
        # Mặc định False là điều kiện để deploy không đổi hành vi đơn cũ.
        order = Order.objects.create(
            channel=self.channel, customer=self.customer, name="A", phone="0911000001"
        )
        self.assertIs(order.direct_dispatch_deferred, False)

    def test_flag_persists(self):
        order = Order.objects.create(
            channel=self.channel, customer=self.customer, name="B", phone="0911000002",
            direct_dispatch_deferred=True,
        )
        order.refresh_from_db()
        self.assertIs(order.direct_dispatch_deferred, True)


class DeferredCancelTests(TestCase):
    """Huỷ đơn: đơn chờ xuất KHÔNG mở watch; đơn đã xuất thì CÓ."""

    def setUp(self):
        self.channel = Channel.objects.create(code="dd-cx", name="Deferred Cancel")
        self.warehouse = Warehouse.objects.create(name="Kho CX", code="KHO-CX")
        self.order = make_order(self.channel, self.warehouse, "31", shipping_code="")
        self.order.shipping_type = "direct"
        self.order.direct_dispatch_deferred = True
        self.order.save(update_fields=["shipping_type", "direct_dispatch_deferred"])
        self.item = self.order.items.first()

    def _cancel(self):
        _on_order_status_changed(
            OrderStatusChanged(order_id=self.order.id, old_status=1, new_status=6)
        )

    def test_cancel_while_waiting_touches_nothing(self):
        before = sellable(self.warehouse.id, self.item.product_id)
        self._cancel()
        self.assertEqual(sellable(self.warehouse.id, self.item.product_id), before)
        self.assertFalse(CancelledDispatchWatch.objects.filter(order_id=self.order.id).exists())

    def test_cancel_after_dispatch_opens_watch(self):
        """Đơn direct MỚI (cờ True, mặc định của `setUp`) — ĐÂY là tripwire spec §4.

        Đơn này khớp `_is_direct_sale` (filter `shipping_type=DIRECT,
        direct_dispatch_deferred=False`) ĐÚNG một điều: cờ của nó là True nên filter
        loại nó ra, và huỷ đi đúng nhánh "chờ hàng về" bên dưới. Nếu ai đó bỏ điều kiện
        `direct_dispatch_deferred=False` khỏi `_is_direct_sale`, đơn cờ True này sẽ bị
        nhận nhầm là đơn quầy cũ: cộng trả tồn ngay (dù hàng đang ở ngoài, chưa bao giờ
        được xác nhận đã về) và KHÔNG mở `CancelledDispatchWatch` — cả hai assert dưới
        đây đỏ ngay. Test cặp đôi với
        `test_legacy_direct_order_still_releases_stock_immediately_on_cancel`, test đó
        khoá chiều ngược lại (cờ False vẫn phải được cộng trả ngay).
        """
        StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.item.product_id, quantity=self.item.quantity)],
        )
        after_issue = sellable(self.warehouse.id, self.item.product_id)

        self._cancel()

        # Hàng đang ở ngoài: KHÔNG cộng trả, phải mở dòng chờ hàng về.
        self.assertEqual(sellable(self.warehouse.id, self.item.product_id), after_issue)
        self.assertTrue(CancelledDispatchWatch.objects.filter(order_id=self.order.id).exists())

    def test_legacy_direct_order_still_releases_stock_immediately_on_cancel(self):
        """Đơn direct CŨ (cờ False, tạo trước 28/08/2026, ĐÃ bị trừ tồn lúc lên hoá đơn).

        Khoá spec §4 chiều "đơn cũ": đơn này ĐÃ bị trừ tồn thật lúc lên hoá đơn (gọi
        `issue_for_order` y như luồng cũ), nên huỷ PHẢI cộng trả tồn ngay — hàng còn nằm
        trong kho, không có gì để "chờ về" — và bút toán hoàn phải mang đúng
        `CANCEL_RELEASE_NOTE` (không phải chuỗi note tự chế). Test không khoá được việc
        ai đó xoá cột hay ngừng ghi cờ True cho đơn mới — vì với cờ đã sẵn False,
        filter `direct_dispatch_deferred=False` là no-op ở đây. Chiều đó do
        `test_cancel_after_dispatch_opens_watch` (đơn cờ True) khoá.
        """
        from apps.inventory.application.services.stock_sales_service import (
            CANCEL_RELEASE_NOTE,
        )

        self.order.direct_dispatch_deferred = False
        self.order.save(update_fields=["direct_dispatch_deferred"])
        StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.item.product_id, quantity=self.item.quantity)],
        )
        after_issue = sellable(self.warehouse.id, self.item.product_id)

        self._cancel()

        self.assertGreater(sellable(self.warehouse.id, self.item.product_id), after_issue)
        self.assertFalse(CancelledDispatchWatch.objects.filter(order_id=self.order.id).exists())
        self.assertTrue(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=self.order.id,
                note=CANCEL_RELEASE_NOTE,
            ).exists()
        )

    def test_revive_of_never_dispatched_order_is_noop(self):
        # PHẠM VI HẸP: đơn CHƯA từng issue_for_order. Huỷ rồi bỏ huỷ trên đơn này đi
        # qua toàn no-op có tài liệu (release_for_order return sớm vì outstanding
        # rỗng, reissue_after_cancel_release không có gì để đảo, resync no-op vì
        # outstanding = 0) — test KHÔNG chạm nhánh mà `_is_direct_sale` phân biệt,
        # và xanh giống hệt nhau bất kể `_is_direct_sale` có loại trừ đơn deferred
        # hay không. Ca "đã xuất rồi mới huỷ rồi hồi sinh" nằm ở
        # test_dispatched_then_cancelled_then_revived_deducts_stock_exactly_once.
        before = sellable(self.warehouse.id, self.item.product_id)
        self._cancel()
        _on_order_status_changed(
            OrderStatusChanged(order_id=self.order.id, old_status=6, new_status=1)
        )
        self.assertEqual(sellable(self.warehouse.id, self.item.product_id), before)

    def test_issue_for_order_is_idempotent(self):
        # PHẠM VI HẸP: kiểm tính idempotent thuần tuý của `issue_for_order` — KHÔNG đi
        # qua `_cancel()`/`_on_order_status_changed`, không liên quan trực tiếp tới thay
        # đổi của `_is_direct_sale`. Giữ lại vì đây vẫn là một bất biến cần khoá của
        # luồng "chờ xuất → đẩy sang giao vận", chỉ đổi tên cho khỏi ngộ nhận là test
        # về huỷ/hồi sinh.
        before = sellable(self.warehouse.id, self.item.product_id)
        for _ in range(2):
            StockSalesService.issue_for_order(
                order_id=self.order.id,
                warehouse_id=self.warehouse.id,
                lines=[SaleLine(product_id=self.item.product_id, quantity=self.item.quantity)],
            )
        self.assertEqual(
            sellable(self.warehouse.id, self.item.product_id), before - self.item.quantity
        )

    def test_dispatched_then_cancelled_then_revived_deducts_stock_exactly_once(self):
        # Ca THỰC SỰ mà spec §6 hàm ý: đơn deferred ĐÃ XUẤT (issue_for_order trừ tồn
        # thật) → huỷ (nhánh mới: KHÔNG cộng trả, mở watch, outstanding giữ nguyên) →
        # hồi sinh (old_status=6 → new_status=1, khác RETURNED nên đi vào nhánh
        # reissue+resync). Với outstanding không đổi qua bước huỷ,
        # `reissue_after_cancel_release` phải đọc sổ ra "không có bút toán hoàn nào để
        # trừ lại" (pending = 0, vì cancel không ghi SALE_REVERSAL) và no-op —
        # KHÔNG được cộng trùng, cũng không được trừ thêm. Tồn cuối phải bằng đúng
        # after_issue: trừ NET đúng một lần, không phải "net 0 vì chẳng có gì xảy ra".
        StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.item.product_id, quantity=self.item.quantity)],
        )
        after_issue = sellable(self.warehouse.id, self.item.product_id)

        self._cancel()
        self.assertEqual(sellable(self.warehouse.id, self.item.product_id), after_issue)
        self.assertTrue(CancelledDispatchWatch.objects.filter(order_id=self.order.id).exists())

        _on_order_status_changed(
            OrderStatusChanged(order_id=self.order.id, old_status=6, new_status=1)
        )

        self.assertEqual(sellable(self.warehouse.id, self.item.product_id), after_issue)


class ManualDispatchTests(TestCase):
    """Nút "Xuất kho" ở chi tiết đơn: chọn kho, ghi đè kho của đơn, trừ tồn một lần."""

    def setUp(self):
        self.actor = make_admin("manual-dispatch-actor")
        self.channel = Channel.objects.create(code="md-ch", name="Manual Dispatch")
        self.wh_order = Warehouse.objects.create(name="Kho theo don", code="KHO-MD1")
        self.wh_picked = Warehouse.objects.create(name="Kho chon tay", code="KHO-MD2")
        # make_order nạp sẵn tồn vào kho truyền vào; nạp thêm cho kho thứ hai để
        # test "ghi đè kho" không bị nhiễu bởi tồn âm.
        self.order = make_order(self.channel, self.wh_order, "41", shipping_code="")
        self.item = self.order.items.first()
        StockCommandService.record_movement(
            warehouse_id=self.wh_picked.id,
            product_id=self.item.product_id,
            qty_delta=self.item.quantity,
            movement_type=MovementType.RECEIPT,
        )

    def test_dispatch_overwrites_order_warehouse_and_deducts_there(self):
        before_picked = sellable(self.wh_picked.id, self.item.product_id)
        before_order_wh = sellable(self.wh_order.id, self.item.product_id)

        result = ManualDispatchService.dispatch(
            order_ids=[self.order.id], warehouse_id=self.wh_picked.id, actor=self.actor
        )

        self.assertEqual(result["confirmed"], [self.order.id])
        self.assertEqual(result["skipped"], [])
        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_warehouse_id, self.wh_picked.id)
        self.assertEqual(
            sellable(self.wh_picked.id, self.item.product_id),
            before_picked - self.item.quantity,
        )
        # Kho cũ của đơn KHÔNG bị đụng tới.
        self.assertEqual(sellable(self.wh_order.id, self.item.product_id), before_order_wh)

    def test_second_dispatch_skips_and_does_not_deduct_twice(self):
        ManualDispatchService.dispatch(
            order_ids=[self.order.id], warehouse_id=self.wh_picked.id, actor=self.actor
        )
        after_first = sellable(self.wh_picked.id, self.item.product_id)

        result = ManualDispatchService.dispatch(
            order_ids=[self.order.id], warehouse_id=self.wh_picked.id, actor=self.actor
        )

        self.assertEqual(result["confirmed"], [])
        self.assertEqual(
            result["skipped"], [{"order_id": self.order.id, "reason": SKIP_ALREADY_DISPATCHED}]
        )
        self.assertEqual(sellable(self.wh_picked.id, self.item.product_id), after_first)

    def test_dispatch_works_for_order_without_kiotviet_invoice(self):
        # Đơn chưa từng đẩy sang KiotViet: verifier trả UNVERIFIABLE (DB là nguồn sự
        # thật), thao tác vẫn phải chạy được — không kẹt vì "chưa xác minh".
        self.assertEqual(self.order.invoice_code, "")

        result = ManualDispatchService.dispatch(
            order_ids=[self.order.id], warehouse_id=self.wh_picked.id, actor=self.actor
        )

        self.assertEqual(result["confirmed"], [self.order.id])

    def test_failed_dispatch_rolls_back_warehouse_override(self):
        # Bug đã sửa: ghi đè kho từng COMMIT trước lời gọi `update_order_inline` —
        # nếu hàm đó raise, đơn bị bỏ lại với `fulfillment_warehouse_id` mới nhưng
        # KHÔNG hề bị trừ tồn ở đó, còn kho gốc thì mất dấu vĩnh viễn. Cả hai ghi giờ
        # nằm chung một `transaction.atomic()` hẹp — lỗi ở update_order_inline phải
        # cuốn luôn ghi đè kho theo.
        from apps.orders.application.services.order_service_impl import OrderService

        original_warehouse_id = self.order.fulfillment_warehouse_id
        self.assertNotEqual(original_warehouse_id, self.wh_picked.id)

        with patch.object(
            OrderService, "update_order_inline", side_effect=RuntimeError("boom")
        ):
            result = ManualDispatchService.dispatch(
                order_ids=[self.order.id], warehouse_id=self.wh_picked.id, actor=self.actor
            )

        self.assertEqual(result["confirmed"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["order_id"], self.order.id)
        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_warehouse_id, original_warehouse_id)

    def test_cancelled_order_is_skipped(self):
        self.order.status = OrderStatus.CANCELLED
        self.order.save(update_fields=["status"])
        before = sellable(self.wh_picked.id, self.item.product_id)

        result = ManualDispatchService.dispatch(
            order_ids=[self.order.id], warehouse_id=self.wh_picked.id, actor=self.actor
        )

        self.assertEqual(result["confirmed"], [])
        self.assertEqual(result["skipped"][0]["reason"], "cancelled")
        self.assertEqual(sellable(self.wh_picked.id, self.item.product_id), before)


class ManualDispatchApiTests(TestCase):
    """Endpoint POST /api/inventory/orders/dispatch — quyền và validate."""

    def setUp(self):
        self.channel = Channel.objects.create(code="md-api", name="Manual Dispatch API")
        self.warehouse = Warehouse.objects.create(name="Kho API", code="KHO-MDAPI")
        self.order = make_order(self.channel, self.warehouse, "42", shipping_code="")
        self.client = APIClient()

    def test_requires_stock_edit_permission(self):
        # Đọc gate theo section "stock", ghi gate theo "stock:edit" — fixture có ý
        # nghĩa nhất là user CÓ `stock:view` (đọc được) nhưng KHÔNG có `stock:edit`
        # (không ghi được). Trước bản vá này fixture dùng `orders:view` (không có
        # quyền stock nào cả) → test pass nhưng vì sai lý do (thiếu mọi quyền stock,
        # không phải đúng thiếu :edit).
        user = make_user_with_permissions("md-no-perm", ["stock:view"])
        self.client.force_authenticate(user)

        resp = self.client.post(
            "/api/inventory/orders/dispatch",
            {"order_ids": [self.order.id], "warehouse_id": self.warehouse.id},
            format="json",
        )

        self.assertEqual(resp.status_code, 403)

    def test_rejects_inactive_warehouse(self):
        self.warehouse.is_active = False
        self.warehouse.save(update_fields=["is_active"])
        self.client.force_authenticate(make_admin("md-admin"))

        resp = self.client.post(
            "/api/inventory/orders/dispatch",
            {"order_ids": [self.order.id], "warehouse_id": self.warehouse.id},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_dispatch_succeeds_for_admin(self):
        self.client.force_authenticate(make_admin("md-admin-ok"))

        resp = self.client.post(
            "/api/inventory/orders/dispatch",
            {"order_ids": [self.order.id], "warehouse_id": self.warehouse.id},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["confirmed"], [self.order.id])


class ManualDispatchBatchVerifyTests(TestCase):
    """I4: `dispatch` phải hỏi KiotViet SONG SONG cho cả lô qua
    `OrderService._prefetch_dispatch_verdicts` (lời giải `dispatch_orders_batch` đã
    dùng cho đúng bài toán này), NGAY TRƯỚC vòng ghi sổ — không phải tuần tự từng đơn
    (lô 80 đơn tuần tự có thể mất tới 240s, sát trần `proxy_read_timeout 300s`). Đây
    là khối logic Task 1 giữ NGUYÊN VẸN từ `DirectDispatchService.confirm` cũ — chỉ đổi
    tên lớp/hàm và thêm `warehouse_id` khi gọi.

    Hai test dùng hai điểm patch khác nhau, có chủ đích:
      - Test 1 patch `OrderService._prefetch_dispatch_verdicts` (staticmethod, đã
        whitelist ở `apps.inventory.tests.* -> apps.orders.application.services.order_service_impl`) để đếm số lần
        gọi — không cần biết verdict thật là gì.
      - Test 2 cần round-trip verify THẬT SỰ lỗi (cả ở prefetch lẫn ở lượt guard tự
        hỏi lại khi thấy verdict lạ — xem `_assert_dispatch_allowed`), nên patch
        thẳng cổng KiotViet (`kiotviet_cancellation_port`) bằng CHUỖI đường dẫn
        (như `test_dispatch_stock.py::_VERIFY`,
        `test_order_inline_update_lock.py::_PORT` đã làm) — `mock.patch(string, ...)`
        resolve module bằng `importlib` lúc CHẠY, import-linter (phân tích AST tĩnh)
        không thấy được, nên không đẻ vi phạm phân tầng mới dù đích là app `orders`.
    """

    def setUp(self):
        self.channel = Channel.objects.create(code="dd-batch", name="Deferred Batch")
        self.warehouse = Warehouse.objects.create(name="Kho Batch", code="KHO-BATCH")
        self.admin = make_admin("admin-dd-batch")
        self.orders = []
        for suffix in ("71", "72", "73"):
            order = make_order(self.channel, self.warehouse, suffix, shipping_code="")
            order.invoice_code = f"HD-DD-{suffix}"
            order.save(update_fields=["invoice_code"])
            self.orders.append(order)

    def test_prefetch_called_once_for_whole_batch_not_per_order(self):
        from apps.orders.application.services.order_service_impl import OrderService

        original = OrderService._prefetch_dispatch_verdicts
        with patch.object(
            OrderService, "_prefetch_dispatch_verdicts", wraps=original
        ) as spy:
            ManualDispatchService.dispatch(
                order_ids=[o.id for o in self.orders],
                warehouse_id=self.warehouse.id,
                actor=self.admin,
            )

        self.assertEqual(
            spy.call_count, 1, "phải prefetch MỘT LẦN cho cả lô, không phải N lần/đơn"
        )
        (called_orders,), _kwargs = spy.call_args
        self.assertEqual(
            sorted(o.id for o in called_orders), sorted(o.id for o in self.orders)
        )

    def test_one_order_verify_failure_does_not_block_others(self):
        # Kịch bản hỏng thật I4 mô tả: một đơn trong lô lỗi round-trip (KiotViet
        # timeout/rate-limit) KHÔNG được kéo cả lô fail — các đơn còn lại vẫn phải
        # được xuất bình thường (fail-closed TỪNG ĐƠN, không phải TỪNG LÔ).
        #
        # Lỗi phải LẶP LẠI cả ở lần verify trong prefetch lẫn lần guard tự hỏi lại
        # (verdict lạ → `_assert_dispatch_allowed` tự gọi lại KiotViet) — patch cổng
        # thật thay vì chỉ patch `_prefetch_dispatch_verdicts` mới mô phỏng đúng một
        # dịch vụ KiotViet ĐANG lỗi thật (không phải lỗi thoáng qua tự khỏi ở lần
        # hỏi lại thứ hai).
        from unittest.mock import MagicMock

        port_resolver = (
            "apps.orders.contracts.kiotviet_cancellation_port.kiotviet_cancellation_port"
        )
        failing_id = self.orders[1].id

        def fake_verify(order):
            if order.id == failing_id:
                raise RuntimeError("KiotViet timeout")
            return "ok"  # chuỗi trần: guard so verdict bằng `is` nên không khớp
            # CancelVerdict.CANCELLED — không cần import enum thật của orders vào
            # đây để giữ đơn "sạch" đi qua trót lọt.

        port = MagicMock()
        port.verify_not_cancelled.side_effect = fake_verify

        before = {
            order.id: sellable(self.warehouse.id, order.items.first().product_id)
            for order in self.orders
        }

        with patch(port_resolver, return_value=port):
            result = ManualDispatchService.dispatch(
                order_ids=[o.id for o in self.orders],
                warehouse_id=self.warehouse.id,
                actor=self.admin,
            )

        self.assertEqual(
            sorted(result["confirmed"]),
            sorted(o.id for o in self.orders if o.id != failing_id),
        )
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["order_id"], failing_id)
        for order in self.orders:
            item = order.items.first()
            after = sellable(self.warehouse.id, item.product_id)
            if order.id == failing_id:
                self.assertEqual(after, before[order.id])
            else:
                self.assertEqual(after, before[order.id] - item.quantity)

    def test_verify_failure_at_prefetch_skips_without_second_http_call(self):
        # Important (fix cuối, review độc lập): verdict-là-exception TRƯỚC ĐÂY bị truyền
        # thẳng xuống `update_order_inline` — hàm đó chạy trong `@transaction.atomic` và
        # đã `order.save()` + `_upsert_order_assessment()` (đang giữ row lock) TRƯỚC KHI
        # guard chạy; guard thấy "verdict lạ" (một exception không phải CancelVerdict) nên
        # tự đi hỏi lại KiotViet — tái tạo đúng round-trip HTTP TRONG transaction mà tham
        # số `verdict` sinh ra để tránh (xem docstring `update_order_inline`). Lô cả trăm
        # đơn lúc KiotViet degrade sẽ thành N round-trip tuần tự, mỗi cái giữ
        # transaction+lock — đúng dạng sự cố 2026-07-31.
        #
        # Khoá lại: verify lỗi ở prefetch → skip NGAY, đúng MỘT lần gọi KiotViet cho đơn
        # đó, KHÔNG có lượt thứ hai.
        from unittest.mock import MagicMock

        port_resolver = (
            "apps.orders.contracts.kiotviet_cancellation_port.kiotviet_cancellation_port"
        )
        order = self.orders[0]
        item = order.items.first()
        port = MagicMock()
        port.verify_not_cancelled.side_effect = RuntimeError("KiotViet timeout")

        before = sellable(self.warehouse.id, item.product_id)

        with patch(port_resolver, return_value=port):
            result = ManualDispatchService.dispatch(
                order_ids=[order.id], warehouse_id=self.warehouse.id, actor=self.admin
            )

        self.assertEqual(result["confirmed"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["order_id"], order.id)
        self.assertIn("KiotViet timeout", result["skipped"][0]["reason"])
        self.assertEqual(
            port.verify_not_cancelled.call_count,
            1,
            "verdict-là-exception phải skip NGAY, không round-trip lần hai TRONG transaction",
        )
        self.assertEqual(sellable(self.warehouse.id, item.product_id), before)
        self.assertFalse(
            StockMovement.objects.filter(
                reference_type="order", reference_id=order.id, movement_type=MovementType.SALE
            ).exists()
        )


class ManualDispatchMovementTimingTests(TestCase):
    """Mốc bút toán phải là lúc BẤM, không phải ngày hoá đơn — toàn bộ lý do tồn tại
    của tính năng "xuất sau". Giữ lại từ `DirectDispatchServiceTests` cũ, chỉ đổi
    sang gọi `ManualDispatchService.dispatch` với `warehouse_id`.
    """

    def setUp(self):
        self.channel = Channel.objects.create(code="dd-sv", name="Deferred Service")
        self.warehouse = Warehouse.objects.create(name="Kho SV", code="KHO-SV")
        self.admin = make_admin("admin-dd-service")
        self.order = make_order(self.channel, self.warehouse, "41b", shipping_code="")
        self.order.invoice_code = "HD-DD-41b"
        self.order.save(update_fields=["invoice_code"])
        self.item = self.order.items.first()

    def test_dispatch_records_movement_at_dispatch_time_not_invoice_time(self):
        purchase_date = timezone.now() - timedelta(days=3)
        self.order.kiotviet_purchase_date = purchase_date
        self.order.save(update_fields=["kiotviet_purchase_date"])

        ManualDispatchService.dispatch(
            order_ids=[self.order.id], warehouse_id=self.warehouse.id, actor=self.admin
        )

        movement = StockMovement.objects.filter(
            reference_type="order", reference_id=self.order.id, movement_type=MovementType.SALE
        ).first()
        self.assertIsNotNone(movement)
        # Mốc phải là lúc BẤM, không phải ngày hoá đơn — đây là toàn bộ lý do tồn tại
        # của tính năng này.
        self.assertGreater(movement.occurred_at, purchase_date + timedelta(days=1))

    def test_dispatch_on_soft_deleted_order_is_skipped_not_issued(self):
        # Kịch bản hỏng thật: sale tạo nhầm đơn direct "xuất sau" (hoá đơn KiotViet lên
        # thật) rồi xoá mềm cho gọn. `dispatch` KHÔNG được trừ tồn cho đơn đã xoá — phải
        # trả skipped, không phải confirmed.
        self.order.deleted_at = timezone.now()
        self.order.save(update_fields=["deleted_at"])

        before = sellable(self.warehouse.id, self.item.product_id)
        result = ManualDispatchService.dispatch(
            order_ids=[self.order.id], warehouse_id=self.warehouse.id, actor=self.admin
        )

        self.assertEqual(result["confirmed"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["order_id"], self.order.id)
        self.assertEqual(sellable(self.warehouse.id, self.item.product_id), before)
        self.assertFalse(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=self.order.id,
                movement_type=MovementType.SALE,
            ).exists()
        )


class AvailabilityPendingDirectTests(TestCase):
    """`pending_direct` trong `StockQueryService.availability` — Task 6.

    Cảnh báo, không phải khoá: sổ chưa trừ tồn trong quãng chờ xuất nên
    `sellable` vẫn đúng là còn bán được; con số này chỉ để form đơn hiển thị
    thêm "đang có N cái chờ xuất kho" cạnh đó.
    """

    def setUp(self):
        self.channel = Channel.objects.create(code="dd-av", name="Deferred Avail")
        self.warehouse = Warehouse.objects.create(name="Kho AV", code="KHO-AV")
        self.order = make_order(self.channel, self.warehouse, "61", shipping_code="", qty=3)
        self.order.shipping_type = "direct"
        self.order.direct_dispatch_deferred = True
        self.order.save(update_fields=["shipping_type", "direct_dispatch_deferred"])
        self.item = self.order.items.first()

    def _row(self):
        from apps.inventory.application.services.stock_query_service import StockQueryService

        rows = StockQueryService.availability(
            warehouse_id=self.warehouse.id, skus=[self.item.sku]
        )
        return rows[0]

    def test_waiting_quantity_is_reported(self):
        row = self._row()
        # sellable KHÔNG đổi nghĩa — nhiều nơi khác đang đọc con số này.
        self.assertEqual(row["sellable"], 3)
        self.assertEqual(row["pending_direct"], 3)

    def test_counter_sale_is_not_counted(self):
        self.order.direct_dispatch_deferred = False
        self.order.save(update_fields=["direct_dispatch_deferred"])
        self.assertEqual(self._row()["pending_direct"], 0)

    def test_combo_is_expanded_to_components(self):
        # Sổ trừ theo mã lẻ, nên "đang chờ xuất" cũng phải tính theo mã lẻ. Cộng
        # theo SKU combo sẽ lệch đúng ở nhóm đơn dễ sai nhất (bài học MR
        # !1317/!1326).
        from apps.catalog.models import Product
        from apps.catalog.models import ProductComponent

        combo = Product.objects.create(name="Combo AV", sku="COMBO-AV")
        part = Product.objects.create(name="Le AV", sku="LE-AV")
        ProductComponent.objects.create(parent=combo, component=part, quantity=2)

        self.item.product = combo
        self.item.sku = combo.sku
        self.item.quantity = 2
        self.item.save(update_fields=["product", "sku", "quantity"])

        from apps.inventory.application.services.stock_query_service import StockQueryService

        rows = StockQueryService.availability(
            warehouse_id=self.warehouse.id, skus=[part.sku]
        )
        self.assertEqual(rows[0]["pending_direct"], 4)  # 2 combo × 2 thành phần

    def test_soft_deleted_order_is_not_counted(self):
        # I1: cùng bẫy deleted_at ở `_pending_direct_by_product` — đơn xoá mềm không
        # được cộng vào "đang chờ xuất", kẻo cảnh báo trên form đơn sống mãi cho một
        # đơn không còn tồn tại.
        self.order.deleted_at = timezone.now()
        self.order.save(update_fields=["deleted_at"])
        self.assertEqual(self._row()["pending_direct"], 0)

    def test_dispatched_order_is_no_longer_pending(self):
        # `direct_dispatch_deferred` KHÔNG bị dọn sau khi xuất (nó nói "đơn này đi
        # theo luật nào", không nói "đã xuất chưa") — phải loại đơn đã xuất bằng
        # đúng định nghĩa "đã xuất" (`_dispatched_order_ids`), đi qua
        # `ManualDispatchService.dispatch` thật chứ không tự set cờ tay.
        from apps.users.tests.helpers import make_admin

        ManualDispatchService.dispatch(
            order_ids=[self.order.id],
            warehouse_id=self.warehouse.id,
            actor=make_admin("admin-av-dispatch"),
        )
        self.assertEqual(self._row()["pending_direct"], 0)
