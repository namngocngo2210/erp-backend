from collections import Counter
from datetime import date

from django.test import TestCase, override_settings

from apps.daily_report.domain import definitions as d
from apps.daily_report.domain import paging
from apps.users.tests.helpers import make_admin

from .helpers import TOKEN, client, fetch_all, rebuild, seed_order

URL = "/api/daily-report/leads/stats"
D = date(2026, 9, 2)
P = {"from": "2026-09-02", "to": "2026-09-02"}


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class LeadRowsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sale_a = make_admin("sale-a")
        cls.sale_a.last_name, cls.sale_a.first_name = "Nguyễn", "A"
        cls.sale_a.save(update_fields=["last_name", "first_name"])
        seed_order(D, sales_status="closed", amount=300_000, staff=cls.sale_a, order_source="landing")
        seed_order(D, sales_status="closed_scheduled_date", amount=200_000, staff=cls.sale_a, order_source="landing")
        seed_order(D, sales_status="no_answer_2", staff=cls.sale_a, order_source="messenger")
        seed_order(D, sales_status="unreachable", order_source="messenger")
        seed_order(D, sales_status="call_back_later")
        seed_order(D, sales_status="spam")
        seed_order(D, sales_status="underlying_condition")
        seed_order(D, sales_status="wants_store_visit")
        seed_order(D)  # chưa đánh giá, chưa gán
        seed_order(date(2026, 9, 1), sales_status="closed", amount=999_000)  # ngoài kỳ
        seed_order(D, sales_status="closed", order_source="foxia")  # mirror: mart loại
        rebuild()

    def test_envelope_meta_and_paging_keys(self):
        body = client().get(URL, {**P, "limit": "4"}).json()
        meta = body["meta"]
        self.assertEqual(meta["timezone"], "Asia/Ho_Chi_Minh")
        self.assertEqual(meta["range"], {"from": "2026-09-02", "to": "2026-09-02"})
        self.assertEqual(meta["data_status"], "Bình thường")
        self.assertTrue(meta["data_as_of"].endswith("+07:00"))
        self.assertEqual(meta["definitions_url"], "/api/daily-report/definitions")
        self.assertEqual(body["limit"], 4)
        self.assertEqual(len(body["data"]), 4)
        self.assertEqual(body["next_cursor"], body["data"][-1]["cursor"])

    def test_rows_recompose_old_totals(self):
        rows = fetch_all(URL, P)
        self.assertEqual(len(rows), 9)                                              # leads
        self.assertEqual(sum(1 for r in rows if r["staff"] is None), 6)             # unassigned
        buckets = Counter(r["sales_status"]["bucket"] for r in rows)
        self.assertEqual(buckets[None], 1)                                          # unassessed
        self.assertEqual(buckets["closed"], 2)
        self.assertEqual(buckets["calling"], 1)
        self.assertEqual(buckets["unreached"], 1)
        self.assertEqual(buckets["nurture"], 1)
        self.assertEqual(buckets["dirty"], 1)
        self.assertEqual(buckets["underlying_condition"], 1)
        self.assertEqual(buckets["wants_store_visit"], 1)
        # closed_revenue cũ: closed_scheduled_date KHÔNG tính
        closed_rev = sum(r["amounts"]["total"] for r in rows if r["sales_status"]["code"] in d.CLOSED_REVENUE_CODES)
        self.assertEqual(closed_rev, 300_000)
        # close_rate cũ = 2 / (9 − underlying_condition − wants_store_visit)
        denom = sum(1 for r in rows if r["sales_status"]["code"] not in d.CLOSE_RATE_EXCLUDED_CODES)
        self.assertAlmostEqual(buckets["closed"] / denom, round(2 / 7, 4), places=4)

    def test_row_fields(self):
        row = next(r for r in fetch_all(URL, P) if r["staff"] and r["source"]["code"] == "landing" and r["amounts"]["total"] == 300_000)
        self.assertEqual(row["staff"]["name"], "Nguyễn A")
        self.assertEqual(row["source"], {"code": "landing", "label": "Landing"})
        self.assertEqual(row["created_date"], "2026-09-02")
        self.assertTrue(row["created_at"].startswith("2026-09-02T10:00:00+07:00"))
        self.assertIn("phone_masked", row["customer"])
        self.assertNotIn("phone", row["customer"])
        self.assertIsNone(row["invoice_status"]["code"])

    def test_keyset_no_dup_and_sorted(self):
        ids = [r["id"] for r in fetch_all(URL, P, limit=2)]
        self.assertEqual(ids, sorted(set(ids)))
        self.assertEqual(len(ids), 9)

    def test_limit_clamped_to_max(self):
        body = client().get(URL, {**P, "limit": "99999"}).json()
        self.assertEqual(body["limit"], paging.ORDER_MAX_LIMIT)

    def test_empty_period_returns_empty_list_not_404(self):
        body = client().get(URL, {"from": "2025-01-01", "to": "2025-01-01"}).json()
        self.assertEqual(body["data"], [])
        self.assertIsNone(body["next_cursor"])

    def test_bad_params_are_400(self):
        self.assertEqual(client().get(URL, {"from": "2026-09-03", "to": "2026-09-01"}).status_code, 400)
        self.assertEqual(client().get(URL, {"from": "2026-01-01", "to": "2026-06-30"}).status_code, 400)
        self.assertEqual(client().get(URL, {**P, "limit": "0"}).status_code, 400)
        self.assertEqual(client().get(URL, {**P, "cursor": "x"}).status_code, 400)
        resp = client().get(URL, {**P, "group_by": "staff"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"], paging.GROUP_BY_GONE)
