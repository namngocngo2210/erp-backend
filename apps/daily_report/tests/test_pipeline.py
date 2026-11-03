from collections import Counter
from datetime import date

from django.test import TestCase, override_settings

from apps.users.tests.helpers import make_admin

from .helpers import TOKEN, client, fetch_all, rebuild, seed_order

URL = "/api/daily-report/pipeline"
P = {"as_of": "2026-09-02"}


def _age_bucket(days: int) -> str:
    if days <= 1:
        return "0-1d"
    if days <= 3:
        return "2-3d"
    if days <= 7:
        return "4-7d"
    return "8d+"


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class PipelineRowsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sale = make_admin("sale-p")
        seed_order(date(2026, 9, 2), sales_status="call_back_later", staff=sale)   # tuổi 0
        seed_order(date(2026, 8, 31), sales_status="no_answer_3", staff=sale)     # tuổi 2
        seed_order(date(2026, 8, 27), sales_status=None)                           # tuổi 6, chưa gán, chưa đánh giá
        seed_order(date(2026, 8, 20), sales_status="scheduled")                    # tuổi 13
        seed_order(date(2026, 9, 1), sales_status="closed")                        # kết thúc: không tính
        seed_order(date(2026, 9, 1), sales_status="spam")                          # kết thúc
        seed_order(date(2026, 9, 1), sales_status="call_back_later", invoice_status=1)  # đã lên HĐ
        seed_order(date(2026, 9, 1), sales_status="call_back_later", status=6)     # huỷ
        seed_order(date(2026, 7, 1), sales_status="call_back_later")               # ngoài lookback 30
        seed_order(date(2026, 9, 3), sales_status="call_back_later")               # sau as_of
        rebuild()

    def test_rows_recompose_old_snapshot(self):
        body = client().get(URL, {**P, "limit": "100"}).json()
        self.assertEqual(body["meta"]["range"], {"from": "2026-08-03", "to": "2026-09-02"})
        rows = body["data"]
        self.assertEqual(len(rows), 4)                                                  # open_leads
        self.assertEqual(sum(1 for r in rows if r["staff"] is None), 2)                 # unassigned
        self.assertEqual(sum(1 for r in rows if r["sales_status"]["code"] is None), 1)  # unassessed
        by_status = Counter(r["sales_status"]["code"] for r in rows)
        self.assertEqual(by_status["call_back_later"], 1)
        self.assertEqual(by_status[None], 1)
        labels = {r["sales_status"]["code"]: r["sales_status"]["label"] for r in rows}
        self.assertEqual(labels["call_back_later"], "Gọi lại sau")
        self.assertEqual(sorted(r["age_days"] for r in rows), [0, 2, 6, 13])
        self.assertEqual(Counter(_age_bucket(r["age_days"]) for r in rows), {"0-1d": 1, "2-3d": 1, "4-7d": 1, "8d+": 1})
        self.assertNotIn("order_status", rows[0])

    def test_keyset(self):
        rows = fetch_all(URL, P, limit=2)
        ids = [r["id"] for r in rows]
        self.assertEqual(ids, sorted(set(ids)))
        self.assertEqual(len(ids), 4)

    def test_lookback_param(self):
        rows = fetch_all(URL, {**P, "lookback_days": "5"})
        self.assertEqual(len(rows), 2)
        self.assertEqual(client().get(URL, {"lookback_days": "0"}).status_code, 400)
        self.assertEqual(client().get(URL, {"lookback_days": "93"}).status_code, 400)
        self.assertEqual(client().get(URL, {**P, "group_by": "staff"}).status_code, 400)
