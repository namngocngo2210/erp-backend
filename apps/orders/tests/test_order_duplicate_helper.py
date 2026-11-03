"""Test helper OrderService.duplicate_order.

Khóa 3 nhóm hành vi:
- nhóm field nghiệp vụ phải theo đơn gốc (VAT, thanh toán, ...);
- 3 tham số phục vụ luồng khôi phục: show_orders / emit_event / restored_from;
- tính lại giá (unit_price/line_total/total_amount) CHỈ khi khôi phục
  (restored_from != None) — luồng nhân đôi (restored_from=None) giữ giá y
  nguyên. Xem docs/superpowers/specs (feat/restore-reprice-current-book).
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import (
    KocOrderType,
    Order,
    OrderItem,
    OrderSource,
    OrderStatus,
    OrderVat,
)
from apps.catalog.models import PriceBook, Product, ProductPackage, ProductPackagePrice
from apps.orders.application.services.order_service_impl import OrderService
from apps.users.tests.helpers import make_admin

PUSH_EVENT_PATH = (
    "apps.orders.application.services.order_service_helpers."
    "OrderServiceHelpers.push_event"
)



class ClonedDispatchFlagBackfillTests(TestCase):
    """Backfill dọn cờ "đã xuất kho" mà nút Nhân bản copy nhầm (migration 0135).

    Bộ lọc phải CHẶT: "không có bút toán tồn kho" một mình không đủ để kết luận
    đơn chưa xuất (đơn không khớp sản phẩm nào xuất kho với lines=[]), nên các
    lớp lọc còn lại được khoá ở đây từng cái một.
    """

    def setUp(self):
        self.channel = Channel.objects.create(code="lp-bf", name="Backfill Channel")
        self.product = Product.objects.create(name="SP BF", sku="SKU-BF-01")
        self.customer = Customer.objects.create(
            full_name="Khach BF",
            phone_raw="0926600222",
            phone_normalized="0926600222",
            code="CUST-BF-01",
        )

    def _order_with_dispatched_flag(self, *, assessment_delay_seconds: int = 0):
        """Đơn + assessment mang cờ `dispatched`; delay = khoảng cách với lúc tạo đơn."""
        from datetime import timedelta

        from apps.orders.models import OrderAssessment

        order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach BF",
            phone="0926600222",
            total_amount="100000",
            status=OrderStatus.NEW,
        )
        assessment = OrderAssessment.objects.create(
            order=order, delivery_business_status_code="dispatched"
        )
        # auto_now_add khiến mọi gán created_at lúc create bị override — phải UPDATE.
        OrderAssessment.objects.filter(pk=assessment.pk).update(
            created_at=order.created_at + timedelta(seconds=assessment_delay_seconds)
        )
        return order

    def _flag_of(self, order) -> str | None:
        order.refresh_from_db()
        return order.assessment.delivery_business_status_code

    def _run_backfill(self) -> list[int]:
        from apps.orders.application.services.cloned_dispatch_flag_backfill import (
            clear_cloned_dispatch_flags,
        )

        return clear_cloned_dispatch_flags(connection)

    def test_clears_flag_of_clone_that_never_left_the_warehouse(self):
        order = self._order_with_dispatched_flag()

        self.assertEqual(self._run_backfill(), [order.id])
        self.assertIsNone(self._flag_of(order))

    def test_keeps_flag_when_order_has_stock_movements(self):
        """Đơn đã trừ tồn thật là đơn xuất kho thật — không được đụng vào."""
        from apps.inventory.application.services.stock_command_service import (
            StockCommandService,
        )
        from apps.inventory.application.services.stock_sales_service import (
            SaleLine,
            StockSalesService,
        )
        from apps.inventory.models import MovementType
        from apps.warehouses.models import Warehouse

        order = self._order_with_dispatched_flag()
        warehouse = Warehouse.objects.create(code="WH-BF", name="Kho BF")
        StockCommandService.record_movement(
            warehouse_id=warehouse.id,
            product_id=self.product.id,
            qty_delta=5,
            movement_type=MovementType.RECEIPT,
        )
        StockSalesService.issue_for_order(
            order_id=order.id,
            warehouse_id=warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=1)],
        )

        self.assertEqual(self._run_backfill(), [])
        self.assertEqual(self._flag_of(order), "dispatched")

    def test_keeps_flag_when_a_scan_matched_the_order(self):
        """Mã quét đã khớp = kho đã xử lý đơn này, kể cả khi sổ tồn trống
        (đơn xuất với lines=[] không sinh bút toán nào)."""
        from apps.inventory.models import (
            PendingScanKind,
            PendingScanStatus,
            PendingShipmentScan,
        )
        from apps.warehouses.models import Warehouse

        order = self._order_with_dispatched_flag()
        PendingShipmentScan.objects.create(
            code="BF-SCAN-1",
            kind=PendingScanKind.DISPATCH,
            status=PendingScanStatus.MATCHED,
            matched_order=order,
            warehouse=Warehouse.objects.create(code="WH-BF-2", name="Kho BF 2"),
        )

        self.assertEqual(self._run_backfill(), [])
        self.assertEqual(self._flag_of(order), "dispatched")

    def test_keeps_flag_set_by_a_human_after_the_order_was_created(self):
        """Assessment sinh muộn = người bấm chọn trạng thái, không phải clone."""
        order = self._order_with_dispatched_flag(assessment_delay_seconds=60)

        self.assertEqual(self._run_backfill(), [])
        self.assertEqual(self._flag_of(order), "dispatched")


class DuplicateOrderHelperTests(TestCase):
    def setUp(self):
        self.actor = make_admin("duplicate-helper-actor")
        self.staff_user = make_admin("duplicate-helper-staff")
        self.channel = Channel.objects.create(code="lp-dup", name="Dup Channel")
        self.product = Product.objects.create(name="SP Dup", sku="SKU-DUP-01")
        self.customer = Customer.objects.create(
            full_name="Khach Dup",
            phone_raw="0926600111",
            phone_normalized="0926600111",
            code="CUST-DUP-01",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach Dup",
            phone="0926600111",
            total_amount="200000",
            status=OrderStatus.NEW,
            order_source=OrderSource.LANDING,
            show_orders=True,
            payment_method="cod",
            vat=OrderVat.COMPANY,
            is_export_vat=True,
            vat_link="https://hoadon.example/abc",
            has_address=False,
            shipping_fee="30000",
            staff=self.staff_user,
            branch_id="BR-DUP-01",
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name=self.product.name,
            sku=self.product.sku,
            quantity=2,
            unit_price=100000,
            line_total=200000,
        )

    def test_copies_business_attributes(self):
        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(self.order, self.actor)

        self.assertEqual(clone.payment_method, "cod")
        self.assertEqual(clone.vat, OrderVat.COMPANY)
        self.assertFalse(clone.has_address)

    def test_does_not_copy_vat_invoice_markers(self):
        """is_export_vat/vat_link là marker "đã xuất hóa đơn của đơn gốc" — bản
        nhân đôi KHÔNG được thừa hưởng, phải về default (False / rỗng)."""
        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(self.order, self.actor)

        self.assertFalse(clone.is_export_vat)
        self.assertEqual(clone.vat_link, "")

    def test_staff_and_branch_id_are_copied_by_default(self):
        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(self.order, self.actor)

        self.assertEqual(clone.staff_id, self.order.staff_id)
        self.assertEqual(clone.branch_id, self.order.branch_id)

    def test_fb_attribution_is_copied(self):
        """Đơn khôi phục/nhân bản giữ campaign/ad id — báo cáo theo campaign đọc
        đơn sống, đơn gốc huỷ đã bị loại khỏi scope."""
        self.order.fb_campaign_id = "120249877608640636"
        self.order.fb_ad_id = "120249877608630636"
        self.order.save(update_fields=["fb_campaign_id", "fb_ad_id"])

        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(self.order, self.actor)

        self.assertEqual(clone.fb_campaign_id, "120249877608640636")
        self.assertEqual(clone.fb_ad_id, "120249877608630636")

    def test_product_raw_is_copied(self):
        self.order.product_raw = "Áo thun x2"
        self.order.save(update_fields=["product_raw"])

        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(self.order, self.actor)

        self.assertEqual(clone.product_raw, "Áo thun x2")

    def test_order_source_is_copied(self):
        """Nhân bản giữ nguyên nguồn đơn. Bỏ trống thì clone rơi khỏi rổ kênh bán
        của Báo cáo tổng và lọt ngược vào trang Landing/Sheet."""
        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(self.order, self.actor)

        self.assertEqual(clone.order_source, OrderSource.LANDING)

    def test_order_source_is_copied_for_marketplace_orders(self):
        """Đơn sàn là ca đau nhất: clone bị xoá `invoice_code` nên mất luôn manh
        mối prefix HDSPE_/HDTTS_ — chỉ còn `order_source` nhận diện nội sàn."""
        self.order.order_source = OrderSource.SHOPEE
        self.order.save(update_fields=["order_source"])

        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(self.order, self.actor)

        self.assertEqual(clone.order_source, OrderSource.SHOPEE)
        self.assertEqual(clone.invoice_code, "")

    def test_order_source_none_stays_none(self):
        self.order.order_source = None
        self.order.save(update_fields=["order_source"])

        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(self.order, self.actor)

        self.assertIsNone(clone.order_source)

    def test_koc_order_type_is_copied(self):
        self.order.order_source = OrderSource.KOC
        self.order.koc_order_type = KocOrderType.RECALL
        self.order.save(update_fields=["order_source", "koc_order_type"])

        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(self.order, self.actor)

        self.assertEqual(clone.order_source, OrderSource.KOC)
        self.assertEqual(clone.koc_order_type, KocOrderType.RECALL)

    def test_reset_progress_still_copies_order_source(self):
        """Luồng khôi phục vốn giữ nguồn đơn qua `preserve_source` (đã gỡ) —
        khoá lại để việc gỡ tham số không âm thầm làm mất nguồn đơn."""
        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(
                self.order,
                self.actor,
                reset_progress=True,
            )

        self.assertEqual(clone.order_source, OrderSource.LANDING)

    def test_show_orders_override_wins_over_reset_progress(self):
        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(
                self.order,
                self.actor,
                reset_progress=True,
                show_orders=True,
            )

        self.assertTrue(clone.show_orders)

    def test_show_orders_none_keeps_legacy_behaviour(self):
        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(
                self.order,
                self.actor,
                reset_progress=True,
            )

        self.assertFalse(clone.show_orders)

    def test_emit_event_false_does_not_push(self):
        with patch(PUSH_EVENT_PATH) as push_event:
            OrderService.duplicate_order(self.order, self.actor, emit_event=False)

        push_event.assert_not_called()

    def test_emit_event_default_pushes_once(self):
        with patch(PUSH_EVENT_PATH) as push_event:
            OrderService.duplicate_order(self.order, self.actor)

        self.assertEqual(push_event.call_count, 1)

    def test_restored_from_is_stored(self):
        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(
                self.order, self.actor, restored_from=self.order
            )

        self.assertEqual(clone.restored_from_id, self.order.id)

    def test_restored_from_default_is_null(self):
        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(self.order, self.actor)

        self.assertIsNone(clone.restored_from_id)

    def test_reset_progress_clears_shipping_fee(self):
        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(
                self.order, self.actor, reset_progress=True
            )

        self.assertEqual(clone.shipping_fee, 0)

    def test_default_keeps_shipping_fee(self):
        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(self.order, self.actor)

        self.assertEqual(clone.shipping_fee, self.order.shipping_fee)

    def _source_assessment(self, **overrides):
        from apps.orders.models import OrderAssessment

        return OrderAssessment.objects.create(
            order=self.order,
            customer_classification_code="new",
            sales_status_code="closed",
            **overrides,
        )

    def test_clone_does_not_inherit_dispatched_delivery_state(self):
        """Trạng thái giao hàng là marker "đơn gốc ĐÃ rời kho" — bản sao chưa
        xuất bao giờ, kế thừa nó khiến kho quét mã vận đơn mới bị chặn với cảnh
        báo "đã xuất kho trước đó" (guard `_has_dispatched_delivery_state` đọc
        thẳng field này, kể cả khi sổ tồn trống)."""
        self._source_assessment(delivery_business_status_code="dispatched")

        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(self.order, self.actor)

        self.assertIsNone(clone.assessment.delivery_business_status_code)

    def test_clone_still_copies_other_assessment_fields(self):
        """Chỉ trạng thái giao hàng bị loại; chân dung khách + trạng thái chốt
        sale vẫn theo đơn gốc như trước."""
        self._source_assessment(delivery_business_status_code="dispatched")

        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(self.order, self.actor)

        self.assertEqual(clone.assessment.customer_classification_code, "new")
        self.assertEqual(clone.assessment.sales_status_code, "closed")


class RestoreReprintsCurrentPriceBookTests(TestCase):
    """Khôi phục (restored_from != None) phải tính lại unit_price/line_total theo
    bảng giá HIỆN TẠI của kênh — không giữ giá cũ chụp lúc chốt đơn."""

    def setUp(self):
        self.actor = make_admin("restore-reprice-actor")
        self.facebook = PriceBook.objects.get(code="facebook")
        self.web = PriceBook.objects.get(code="web")
        self.customer = Customer.objects.create(
            full_name="Khach Reprice",
            phone_raw="0926600200",
            phone_normalized="0926600200",
            code="CUST-REPRICE-01",
        )

    def _make_order(self, channel, *, quantity: int, old_unit_price, discount_amount=0):
        old_line_total = Decimal(str(old_unit_price)) * quantity
        order = Order.objects.create(
            channel=channel,
            customer=self.customer,
            name="Khach Reprice",
            phone="0926600200",
            status=OrderStatus.NEW,
            total_amount=old_line_total,
            discount_amount=discount_amount,
            booking_voucher_discount=0,
            total_after_discount=old_line_total - discount_amount,
        )
        return order

    def test_koc_channel_restore_uses_web_price_not_old_facebook_price(self):
        product = Product.objects.create(name="SP Reprice KOC", sku="SKU-REPRICE-KOC-01")
        pkg1 = ProductPackage.objects.create(product=product, quantity=1)
        ProductPackagePrice.objects.create(package=pkg1, price_book=self.facebook, price=Decimal("100"))
        ProductPackagePrice.objects.create(package=pkg1, price_book=self.web, price=Decimal("130"))
        channel = Channel.objects.create(code="koc-reprice", name="KOC Reprice", price_book=self.web)
        order = self._make_order(channel, quantity=2, old_unit_price=Decimal("100"))
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            quantity=2,
            unit_price=Decimal("100"),
            line_total=Decimal("200"),
        )

        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(order, self.actor, restored_from=order)

        item = clone.items.get()
        self.assertEqual(item.unit_price, Decimal("130.00"))
        self.assertEqual(item.line_total, Decimal("260.00"))
        self.assertEqual(clone.total_amount, Decimal("260.00"))
        self.assertEqual(clone.total_after_discount, Decimal("260.00"))

    def test_restore_uses_matching_package_tier_price(self):
        """quantity=2 và bảng giá có khai gói quantity=2 (240đ/gói) → unit_price =
        240 / 2 = 120, không phải giá gói quantity=1."""
        product = Product.objects.create(name="SP Reprice Tier", sku="SKU-REPRICE-TIER-01")
        pkg1 = ProductPackage.objects.create(product=product, quantity=1)
        pkg2 = ProductPackage.objects.create(product=product, quantity=2)
        ProductPackagePrice.objects.create(package=pkg1, price_book=self.web, price=Decimal("130"))
        ProductPackagePrice.objects.create(package=pkg2, price_book=self.web, price=Decimal("240"))
        channel = Channel.objects.create(code="koc-tier", name="KOC Tier", price_book=self.web)
        order = self._make_order(channel, quantity=2, old_unit_price=Decimal("100"))
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            quantity=2,
            unit_price=Decimal("100"),
            line_total=Decimal("200"),
        )

        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(order, self.actor, restored_from=order)

        item = clone.items.get()
        self.assertEqual(item.unit_price, Decimal("120.00"))
        self.assertEqual(item.line_total, Decimal("240.00"))
        self.assertEqual(clone.total_amount, Decimal("240.00"))

    def test_restore_line_total_matches_package_price_when_not_evenly_divisible(self):
        """Giá gói không chia hết cho quantity (VD gói quantity=3 giá 100đ →
        unit_price làm tròn 33.33) → line_total PHẢI bằng đúng giá gói đã khai
        (100), không được suy ngược từ unit_price đã làm tròn (33.33*3=99.99,
        thiếu 1 xu). unit_price vẫn hiển thị giá trị đã làm tròn."""
        product = Product.objects.create(name="SP Reprice Lẻ", sku="SKU-REPRICE-ODD-01")
        pkg3 = ProductPackage.objects.create(product=product, quantity=3)
        ProductPackagePrice.objects.create(package=pkg3, price_book=self.web, price=Decimal("100"))
        channel = Channel.objects.create(code="koc-odd-price", name="KOC Odd Price", price_book=self.web)
        order = self._make_order(channel, quantity=3, old_unit_price=Decimal("25"))
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            quantity=3,
            unit_price=Decimal("25"),
            line_total=Decimal("75"),
        )

        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(order, self.actor, restored_from=order)

        item = clone.items.get()
        self.assertEqual(item.unit_price, Decimal("33.33"))
        self.assertEqual(item.line_total, Decimal("100.00"))
        self.assertEqual(clone.total_amount, Decimal("100.00"))

    def test_restore_clamps_negative_total_after_discount_and_logs_warning(self):
        """Bảng giá mới rẻ hơn giá cũ trong khi discount_amount/booking_voucher
        vẫn copy nguyên từ đơn gốc → total_after_discount có thể âm. Phải kẹp về
        0 (không để âm âm thầm) và ghi log cảnh báo để truy vết được."""
        product = Product.objects.create(name="SP Reprice Rẻ", sku="SKU-REPRICE-CHEAP-01")
        pkg1 = ProductPackage.objects.create(product=product, quantity=1)
        ProductPackagePrice.objects.create(package=pkg1, price_book=self.web, price=Decimal("100"))
        channel = Channel.objects.create(code="koc-cheap-price", name="KOC Cheap Price", price_book=self.web)
        order = self._make_order(
            channel, quantity=1, old_unit_price=Decimal("1000"), discount_amount=Decimal("500")
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            quantity=1,
            unit_price=Decimal("1000"),
            line_total=Decimal("1000"),
        )

        logger_path = "apps.orders.application.services.order_service_helpers"
        with patch(PUSH_EVENT_PATH), self.assertLogs(logger_path, level="WARNING") as logs:
            clone = OrderService.duplicate_order(order, self.actor, restored_from=order)

        item = clone.items.get()
        self.assertEqual(item.unit_price, Decimal("100.00"))
        self.assertEqual(clone.total_amount, Decimal("100.00"))
        # total_after_discount lẽ ra là 100 - 500 = -400 nhưng phải bị kẹp về 0.
        self.assertEqual(clone.total_after_discount, Decimal("0"))
        self.assertTrue(any("negative total_after_discount" in message for message in logs.output))
        self.assertTrue(any(str(clone.id) in message for message in logs.output))

    def test_restore_keeps_old_price_when_product_has_no_price_book_entry(self):
        """Sản phẩm không tra được giá ở bảng nào (kể cả bảng mặc định) → PHẢI giữ
        nguyên unit_price cũ, tuyệt đối không được ra giá 0."""
        product = Product.objects.create(name="SP Reprice Missing", sku="SKU-REPRICE-MISSING-01")
        channel = Channel.objects.create(code="koc-missing-price", name="KOC Missing", price_book=self.web)
        order = self._make_order(channel, quantity=3, old_unit_price=Decimal("75"))
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            quantity=3,
            unit_price=Decimal("75"),
            line_total=Decimal("225"),
        )

        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(order, self.actor, restored_from=order)

        item = clone.items.get()
        self.assertEqual(item.unit_price, Decimal("75"))
        self.assertNotEqual(item.unit_price, Decimal("0"))
        self.assertEqual(item.line_total, Decimal("225"))
        self.assertEqual(clone.total_amount, Decimal("225"))

    def test_restore_with_no_source_items_keeps_totals_untouched(self):
        """Đơn gốc không có dòng hàng (đơn sync KiotViet chỉ có text
        product/sku/quantity) → không tự chế thêm dòng hàng, tổng tiền giữ
        nguyên như copy — không được đụng vào."""
        channel = Channel.objects.create(code="koc-no-items", name="KOC No Items", price_book=self.web)
        order = self._make_order(channel, quantity=1, old_unit_price=Decimal("50"), discount_amount=5)

        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(order, self.actor, restored_from=order)

        self.assertEqual(clone.items.count(), 0)
        self.assertEqual(clone.total_amount, order.total_amount)
        self.assertEqual(clone.total_after_discount, order.total_after_discount)

    def test_duplicate_flow_without_restored_from_copies_price_verbatim(self):
        """Khoá luồng NHÂN ĐÔI (restored_from=None): giá phải copy y nguyên dù bảng
        giá hiện tại của kênh đã khác giá đơn gốc — chỉ nút Khôi phục mới tính lại."""
        product = Product.objects.create(name="SP Reprice Dup", sku="SKU-REPRICE-DUP-01")
        pkg1 = ProductPackage.objects.create(product=product, quantity=1)
        ProductPackagePrice.objects.create(package=pkg1, price_book=self.web, price=Decimal("999"))
        channel = Channel.objects.create(code="koc-dup-lock", name="KOC Dup Lock", price_book=self.web)
        order = self._make_order(channel, quantity=2, old_unit_price=Decimal("100"))
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            quantity=2,
            unit_price=Decimal("100"),
            line_total=Decimal("200"),
        )

        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(order, self.actor)

        item = clone.items.get()
        self.assertEqual(item.unit_price, Decimal("100"))
        self.assertEqual(item.line_total, Decimal("200"))
        self.assertEqual(clone.total_amount, order.total_amount)


class RestoreReprintsQueryCountTests(TestCase):
    """Chống tái diễn N+1: tra giá khi khôi phục phải theo LÔ (package_prices một
    lần cho toàn bộ dòng hàng), không gọi tra giá riêng cho từng dòng."""

    def setUp(self):
        self.actor = make_admin("restore-reprice-qc-actor")
        self.web = PriceBook.objects.get(code="web")
        self.channel = Channel.objects.create(code="koc-reprice-qc", name="KOC Reprice QC", price_book=self.web)
        self.customer = Customer.objects.create(
            full_name="Khach Reprice QC",
            phone_raw="0926600300",
            phone_normalized="0926600300",
            code="CUST-REPRICE-QC-01",
        )

    def _make_order_with_items(self, code: str, item_count: int) -> Order:
        order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach Reprice QC",
            phone="0926600300",
            status=OrderStatus.NEW,
            total_amount=Decimal(str(100 * item_count)),
            total_after_discount=Decimal(str(100 * item_count)),
        )
        for i in range(item_count):
            product = Product.objects.create(name=f"SP QC {code}-{i}", sku=f"SKU-REPRICE-QC-{code}-{i}")
            pkg = ProductPackage.objects.create(product=product, quantity=1)
            ProductPackagePrice.objects.create(package=pkg, price_book=self.web, price=Decimal("150"))
            OrderItem.objects.create(
                order=order,
                product=product,
                product_name=product.name,
                sku=product.sku,
                quantity=1,
                unit_price=Decimal("100"),
                line_total=Decimal("100"),
            )
        return order

    def test_restore_query_count_does_not_scale_with_line_items(self):
        order_one = self._make_order_with_items("one", 1)
        order_five = self._make_order_with_items("five", 5)

        with patch(PUSH_EVENT_PATH):
            with CaptureQueriesContext(connection) as ctx_one:
                OrderService.duplicate_order(order_one, self.actor, restored_from=order_one)
            query_count_one_item = len(ctx_one.captured_queries)

            with self.assertNumQueries(query_count_one_item):
                OrderService.duplicate_order(order_five, self.actor, restored_from=order_five)
