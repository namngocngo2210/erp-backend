from django.http import QueryDict
from django.test import SimpleTestCase

from apps.daily_report.domain import definitions as d
from apps.daily_report.domain import paging
from apps.daily_report.domain.periods import ParamError


def q(s: str) -> QueryDict:
    return QueryDict(s)


class PagingTests(SimpleTestCase):
    def test_limit_default_and_clamp(self):
        self.assertEqual(paging.parse_limit(q(""), max_limit=1000), 200)
        self.assertEqual(paging.parse_limit(q("limit=50"), max_limit=1000), 50)
        self.assertEqual(paging.parse_limit(q("limit=999999"), max_limit=1000), 1000)
        self.assertEqual(paging.parse_limit(q(""), max_limit=100), 100)  # trần nhỏ hơn mặc định

    def test_limit_invalid(self):
        for raw in ("limit=0", "limit=-1", "limit=abc", "limit=1.5"):
            with self.assertRaises(ParamError, msg=raw):
                paging.parse_limit(q(raw), max_limit=1000)

    def test_int_cursor(self):
        self.assertIsNone(paging.parse_int_cursor(q("")))
        self.assertEqual(paging.parse_int_cursor(q("cursor=123")), 123)
        with self.assertRaises(ParamError):
            paging.parse_int_cursor(q("cursor=abc"))

    def test_group_by_rejected_even_when_empty(self):
        paging.reject_group_by(q(""))  # không ném
        for raw in ("group_by=day", "group_by="):
            with self.assertRaises(ParamError) as ctx:
                paging.reject_group_by(q(raw))
            self.assertEqual(str(ctx.exception), paging.GROUP_BY_GONE)

    def test_slice_page(self):
        self.assertEqual(paging.slice_page([1, 2, 3, 4], 3), ([1, 2, 3], True))
        self.assertEqual(paging.slice_page([1, 2, 3], 3), ([1, 2, 3], False))
        self.assertEqual(paging.slice_page([], 3), ([], False))


class OrderBucketTests(SimpleTestCase):
    def test_buckets(self):
        self.assertIsNone(d.order_bucket_of(None))
        self.assertEqual(d.order_bucket_of(d.CANCELLED_STATUS), "cancelled")
        self.assertEqual(d.order_bucket_of(d.DELIVERED_STATUS), "delivered")
        self.assertEqual(d.order_bucket_of(d.RETURNED_STATUSES[0]), "returned")
        self.assertEqual(d.order_bucket_of(d.RETURN_RISK_STATUSES[0]), "return_risk")
        self.assertEqual(d.order_bucket_of(99), "awaiting")  # mã lạ rơi vào phần bù
