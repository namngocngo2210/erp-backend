"""Hợp đồng của điều phối: mốc lấy TRƯỚC khi đọc (không mất dòng ghi trong lúc
chạy), biên lùi 5 phút, thứ tự bảng, và LỖI PHẢI THOÁT RA (sự cố 2026-07-22)."""

from unittest import mock

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from apps.ai_mart.application import etl_service
from apps.orders.models import Order

from .helpers import mk_order


def _don_hang_row(order_id):
    with connection.cursor() as cur:
        cur.execute(
            "SELECT trang_thai_don_ma, trang_thai_don_text"
            " FROM ai.don_hang WHERE don_id = %s",
            [order_id],
        )
        cols = [c[0] for c in cur.description]
        r = cur.fetchone()
    return dict(zip(cols, r)) if r else None


class EtlServiceTests(TestCase):
    def test_sync_hot_picks_up_queryset_update(self):
        """Chứng minh pipeline sync CHUYỂN dữ liệu nguồn đã đổi vào mart, không
        chỉ "chạy không lỗi". Việc candidate_ids bắt được dòng .update() nhờ
        trigger dw_touch + biên OVERLAP 5 phút đã được chứng minh riêng ở
        test_triggers (TransactionTestCase) và test_loaders::test_candidate_ids
        — dưới TestCase, biên 5 phút khiến candidate query đúng cho MỌI dòng
        trong transaction test nên bản thân nó không phân biệt được; test này
        không dựa vào đó để pass, mà so KẾT QUẢ nội dung trong ai.don_hang
        trước/sau để chứng minh đường ống thật sự lan truyền thay đổi."""
        order = mk_order(status=2)
        etl_service.rebuild_all()
        row_before = _don_hang_row(order.id)
        self.assertEqual(row_before["trang_thai_don_ma"], 2)
        self.assertEqual(row_before["trang_thai_don_text"], "Đang giao hàng")

        Order.objects.filter(id=order.id).update(status=3)
        result = etl_service.sync_hot()
        self.assertGreaterEqual(result["tables"]["ai.don_hang"], 1)

        row_after = _don_hang_row(order.id)
        self.assertEqual(row_after["trang_thai_don_ma"], 3)
        self.assertEqual(row_after["trang_thai_don_text"], "Giao thành công")

    def test_sync_hot_advances_watermark(self):
        from apps.ai_mart.infrastructure import loaders

        etl_service.rebuild_all()
        before = timezone.now()
        etl_service.sync_hot()
        moc = loaders.read_watermark("__hot__")
        self.assertIsNotNone(moc)
        self.assertGreaterEqual(moc, before)

    def test_failure_propagates_and_watermark_not_advanced(self):
        from apps.ai_mart.infrastructure import loaders

        etl_service.rebuild_all()
        etl_service.sync_hot()
        moc_truoc = loaders.read_watermark("__hot__")
        with mock.patch(
            "apps.ai_mart.infrastructure.loaders.upsert_by_ids",
            side_effect=RuntimeError("boom"),
        ):
            mk_order(status=0)
            with self.assertRaises(etl_service.EtlFailed):
                etl_service.sync_hot()
        self.assertEqual(loaders.read_watermark("__hot__"), moc_truoc)
