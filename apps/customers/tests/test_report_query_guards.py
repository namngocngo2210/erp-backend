"""Guard rails dùng chung cho query nặng (nhánh gốc fix/customer-report-runaway-query):
- statement_timeout: hủy query chạy quá lâu ở tầng DB, reset sau khi thoát, no-op khi <=0.
- single_flight: chống chạy chồng (lock Redis).

Phần cohort retention (report_cohort.retention) đã bị xoá cùng cụm service báo
cáo khách hàng (2026-08-21) — hai guard trên là hạ tầng chung, không riêng gì
báo cáo, nên vẫn giữ lại ở đây.
"""
import pytest
from django.db import connection
from django.core.cache import cache
from django.db.utils import OperationalError
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from apps.common.db_guards import single_flight, statement_timeout


@pytest.mark.slow  # race/timeout thật ≥5 s — CI vẫn chạy
class StatementTimeoutTests(TransactionTestCase):
    """TransactionTestCase: query bị cancel làm abort transaction, không dùng được lớp
    transaction bao ngoài của TestCase."""

    def test_cancels_slow_query_then_resets(self):
        with self.assertRaises(OperationalError):
            with statement_timeout(1):  # 1s
                with connection.cursor() as cur:
                    cur.execute("SELECT pg_sleep(3)")
        # Sau khi thoát context, timeout đã reset → query bình thường chạy được.
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_zero_is_noop(self):
        # <=0 = tắt: không được đổi statement_timeout hiện tại của session
        # (có thể là default toàn cục từ DATABASES OPTIONS, không nhất thiết "0").
        with connection.cursor() as cur:
            cur.execute("SHOW statement_timeout")
            before = cur.fetchone()[0]
        with statement_timeout(0):
            with connection.cursor() as cur:
                cur.execute("SHOW statement_timeout")
                self.assertEqual(cur.fetchone()[0], before)


class SingleFlightTests(SimpleTestCase):
    def setUp(self):
        self.key = "test:lock:single-flight-guard"
        cache.delete(self.key)

    def tearDown(self):
        cache.delete(self.key)

    def test_second_acquire_is_blocked_until_release(self):
        with single_flight(self.key, 60) as first:
            self.assertTrue(first)
            with single_flight(self.key, 60) as second:
                self.assertFalse(second)  # đang bị giữ → bỏ qua
        # Sau khi nhả, giành lại được.
        with single_flight(self.key, 60) as third:
            self.assertTrue(third)

    def test_release_does_not_clobber_a_newer_owner_after_stale_ttl(self):
        """Nếu TTL hết hạn trong lúc tiến trình cũ vẫn đang chạy VÀ một tiến trình
        khác đã giành lại khoá TRƯỚC khi tiến trình cũ tới bước release, release
        của tiến trình cũ (token không khớp) không được xoá khoá của chủ mới."""
        with single_flight(self.key, 60):
            # Giả lập TTL hết hạn giữa chừng + tiến trình khác giành lại khoá.
            cache.delete(self.key)
            cache.add(self.key, "other-owner-token", 60)
        self.assertEqual(cache.get(self.key), "other-owner-token")
