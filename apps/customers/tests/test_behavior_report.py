"""Segment snapshot writer: rebuild, full-rebuild skip-unchanged, incremental.

Gộp từ test_segment_snapshot_service.py (tái cấu trúc test 2026-08-05).
Phần phủ customer_behavior_report / report_insights đã bị xoá cùng cụm
service báo cáo khách hàng (2026-08-21) — segment_snapshot_service.py sống
sót vì còn nuôi trang Theo dõi tái mua và hai matview `dwh`.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import (
    Customer,
    CustomerSegmentCategory,
    CustomerSegmentSnapshot,
)
from apps.customers.segment_snapshot_service import SegmentSnapshotService
from apps.customers.segmentation import SEGMENT_CATALOG_SEED
from apps.orders.models import Order, OrderAssessment


def _seed_segment_catalog():
    """Seed segment categories (required by snapshot service)."""
    for it in SEGMENT_CATALOG_SEED:
        CustomerSegmentCategory.objects.get_or_create(
            code=it["code"],
            defaults=dict(name=it["name"], sort_order=it["sort_order"]),
        )


class SegmentSnapshotServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _seed_segment_catalog()

    def test_rebuild_writes_segment(self):
        c = Customer.objects.create(full_name="A")
        Order.objects.create(customer=c, status=3, total_after_discount=Decimal("0"),
                             channel_id=self._chan())
        n = SegmentSnapshotService.rebuild([c.id])
        self.assertEqual(n, 1)
        snap = CustomerSegmentSnapshot.objects.get(pk=c.id)
        self.assertTrue(snap.segment_code)
        self.assertEqual(SegmentSnapshotService.segment_map([c.id])[c.id], snap.segment_code)

    def test_rebuild_writes_latest_sales(self):
        c = Customer.objects.create(full_name="X")
        o = Order.objects.create(customer=c, status=3, total_after_discount=Decimal("0"), channel_id=self._chan())
        OrderAssessment.objects.create(order=o, sales_status_code="closed")
        SegmentSnapshotService.rebuild([c.id])
        self.assertEqual(CustomerSegmentSnapshot.objects.get(pk=c.id).latest_sales_status_code, "closed")

    def _chan(self):
        return Channel.objects.get_or_create(code="ch", defaults={"name": "ch"})[0].id


class SegmentSnapshotFullRebuildSkipTests(TestCase):
    """Full rebuild không được ghi lại dòng có giá trị y hệt.

    Đo prod 2026-08-17 — số dòng `customer_segment_snapshot` thực ghi mỗi chu kỳ:

        16/08 20:00 UTC (full rebuild 03:20 giờ VN) : 755.219
        16/08 21:00 (incremental)                   :       2
        16/08 23:00                                 :       1
        17/08 01:00                                 :       7
        17/08 03:00                                 :     301

    Đường tăng dần đã rất gọn; toàn bộ dead tuple đến từ lần full hằng ngày ghi đè
    cả 755k dòng để thay đổi vài trăm dòng (tỷ lệ lãng phí ~2.500:1). Đó là nguồn
    gốc của bloat heap 6,4× (523MB thực tế / ~81MB theo kích thước dòng thật).

    `bulk_create(update_conflicts=True)` ghi đè VÔ ĐIỀU KIỆN, nên phải lọc trước
    khi đưa vào `objs`.
    """

    def setUp(self):
        _seed_segment_catalog()
        self.channel = Channel.objects.create(code="skip", name="skip")
        self.alice = Customer.objects.create(full_name="Alice")
        self.bob = Customer.objects.create(full_name="Bob")
        for cust in (self.alice, self.bob):
            order = Order.objects.create(
                customer=cust, channel=self.channel, status=3,
                total_after_discount=Decimal("100"),
            )
            OrderAssessment.objects.create(order=order)
        SegmentSnapshotService.rebuild()

    def _updated_at(self, customer):
        return CustomerSegmentSnapshot.objects.get(customer_id=customer.id).updated_at

    def test_full_rebuild_skips_rows_whose_values_did_not_change(self):
        before = {c.id: self._updated_at(c) for c in (self.alice, self.bob)}

        written = SegmentSnapshotService.rebuild(skip_unchanged=True)

        self.assertEqual(written, 0)
        for cust in (self.alice, self.bob):
            self.assertEqual(self._updated_at(cust), before[cust.id])

    def test_full_rebuild_writes_row_whose_values_changed(self):
        bob_before = self._updated_at(self.bob)
        Order.objects.create(
            customer=self.alice, channel=self.channel, status=3,
            total_after_discount=Decimal("250"),
        )

        written = SegmentSnapshotService.rebuild(skip_unchanged=True)

        self.assertEqual(written, 1)
        snap = CustomerSegmentSnapshot.objects.get(customer_id=self.alice.id)
        self.assertEqual(snap.total_orders, 2)
        self.assertEqual(snap.total_spent, Decimal("350"))
        self.assertEqual(self._updated_at(self.bob), bob_before)

    def test_full_rebuild_creates_snapshot_for_customer_without_one(self):
        carol = Customer.objects.create(full_name="Carol")

        written = SegmentSnapshotService.rebuild(skip_unchanged=True)

        self.assertEqual(written, 1)
        self.assertTrue(
            CustomerSegmentSnapshot.objects.filter(customer_id=carol.id).exists()
        )

    def test_skip_is_off_by_default_so_incremental_still_advances_watermark(self):
        """Mốc nước = MAX(updated_at) của chính bảng snapshot.

        Nếu đường TĂNG DẦN cũng bỏ qua dòng không đổi thì một thay đổi không chạm
        giá trị snapshot (vd sửa ghi chú đơn) sẽ khiến mốc nước đứng im vĩnh viễn,
        và cửa sổ quét của mọi chu kỳ sau mở rộng dần không giới hạn. Nên `skip`
        phải TẮT mặc định và chỉ bật cho đường full.
        """
        before = self._updated_at(self.alice)

        written = SegmentSnapshotService.rebuild([self.alice.id])

        self.assertEqual(written, 1)
        self.assertGreater(self._updated_at(self.alice), before)


class SegmentSnapshotIncrementalTests(TestCase):
    """Rebuild TĂNG DẦN: chỉ tính lại khách có thay đổi, và phải cho ra đúng cùng
    một tập với đường rebuild toàn bộ.

    Vì sao cần khoá bằng test (đo pg_stat_statements 2026-08-11): rebuild toàn bộ
    mỗi 2h là ~23% thời gian của cả database. Cái giá của việc cắt nó là một mốc
    nước — và mốc nước sai thì snapshot đứng im trong im lặng, không có lỗi nào
    nổ ra. Bốn test dưới đây khoá đúng bốn cách mốc nước có thể sai.

    Mọi test đều ép `updated_at` bằng `.update()` (bỏ qua `auto_now`) thay vì
    `sleep`, nên không phụ thuộc đồng hồ thật.
    """

    def setUp(self):
        _seed_segment_catalog()
        self.channel = Channel.objects.create(code="inc", name="inc")
        self.alice = Customer.objects.create(full_name="Alice")
        self.bob = Customer.objects.create(full_name="Bob")
        for cust in (self.alice, self.bob):
            order = Order.objects.create(
                customer=cust, channel=self.channel, status=3,
                total_after_discount=Decimal("100"),
            )
            OrderAssessment.objects.create(order=order)
        SegmentSnapshotService.rebuild()

    @staticmethod
    def _age_all(ts):
        """Đẩy mọi nguồn thay đổi về quá khứ để không có gì nằm trong cửa sổ quét."""
        Order.objects.update(updated_at=ts)
        OrderAssessment.objects.update(updated_at=ts)
        Customer.objects.update(updated_at=ts)

    def _freeze_watermark(self):
        """Đặt mốc nước ở 1 ngày trước, mọi dữ liệu ở 2 ngày trước."""
        now = timezone.now()
        self._age_all(now - timedelta(days=2))
        CustomerSegmentSnapshot.objects.update(updated_at=now - timedelta(days=1))
        return now

    def _snapshot_updated_at(self, customer):
        return CustomerSegmentSnapshot.objects.get(customer_id=customer.id).updated_at

    def test_only_changed_customer_is_recomputed(self):
        now = self._freeze_watermark()
        bob_before = self._snapshot_updated_at(self.bob)
        Order.objects.filter(customer=self.alice).update(updated_at=now)

        rows, fell_back = SegmentSnapshotService.rebuild_incremental()

        self.assertFalse(fell_back)
        self.assertEqual(rows, 1)
        self.assertGreater(self._snapshot_updated_at(self.alice), now - timedelta(minutes=1))
        self.assertEqual(self._snapshot_updated_at(self.bob), bob_before)

    def test_assessment_change_alone_marks_customer_affected(self):
        """Sửa đánh giá KHÔNG chạm `orders.updated_at`.

        Phân khúc phụ thuộc `sales_status_code` / `delivery_business_status_code` /
        `post_purchase_rating_code` — đều nằm ở `order_assessments`. Bỏ nguồn này
        thì thao tác thường gặp nhất của sale (chấm đánh giá) trở nên vô hình với
        snapshot.
        """
        now = self._freeze_watermark()
        OrderAssessment.objects.filter(order__customer=self.alice).update(updated_at=now)

        rows, fell_back = SegmentSnapshotService.rebuild_incremental()

        self.assertFalse(fell_back)
        self.assertEqual(rows, 1)
        self.assertGreater(self._snapshot_updated_at(self.alice), now - timedelta(minutes=1))

    def test_soft_deleted_customer_excluded_like_full_rebuild(self):
        """Đường tăng dần lấy `customer_id` từ đơn nên dễ kéo theo khách đã xoá mềm,
        trong khi đường full loại hẳn nhóm đó. Hai đường lệch tập là lỗi chỉ lộ ra
        sau nhiều tuần, khi báo cáo full và báo cáo tăng dần vênh nhau."""
        now = self._freeze_watermark()
        Customer.objects.filter(pk=self.alice.pk).update(
            deleted_at=now, updated_at=now - timedelta(days=2)
        )
        Order.objects.filter(customer=self.alice).update(updated_at=now)

        affected = SegmentSnapshotService.affected_customer_ids(now - timedelta(minutes=30))

        self.assertNotIn(self.alice.id, affected)

    def test_falls_back_to_full_when_no_watermark(self):
        """Bảng snapshot rỗng (lần chạy đầu / vừa truncate) thì không có mốc nước —
        phải quét toàn bộ chứ không được coi là 'không có gì đổi'."""
        CustomerSegmentSnapshot.objects.all().delete()

        rows, fell_back = SegmentSnapshotService.rebuild_incremental()

        self.assertTrue(fell_back)
        self.assertEqual(rows, Customer.objects.filter(deleted_at__isnull=True).count())
