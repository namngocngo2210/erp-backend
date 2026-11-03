from django.test import SimpleTestCase

from apps.daily_report.domain import definitions as d
from apps.orders import crm_catalog


class DefinitionTests(SimpleTestCase):
    def test_every_catalog_sales_status_has_a_bucket(self):
        """Mã sale nào trong crm_catalog cũng phải rơi vào ĐÚNG một bucket — thêm mã
        mới mà quên xếp bucket thì test này đỏ, thay vì mã đó âm thầm thành 'other'."""
        codes = [o.code for o in crm_catalog.SALES_STATUS_OPTIONS]
        for code in codes:
            self.assertNotEqual(d.bucket_of(code), "other", code)

    def test_buckets_are_disjoint(self):
        seen = set()
        for bucket, codes in d.BUCKET_CODES:
            for code in codes:
                self.assertNotIn(code, seen, f"{code} nằm ở 2 bucket")
                seen.add(code)
        self.assertEqual(seen, set(d.ALL_BUCKETED_CODES))

    def test_order_buckets_cover_all_statuses_except_cancelled(self):
        from apps.orders.models import OrderStatus

        covered = {d.DELIVERED_STATUS, *d.RETURNED_STATUSES, *d.RETURN_RISK_STATUSES, *d.AWAITING_STATUSES}
        self.assertEqual(covered, {s.value for s in OrderStatus} - {d.CANCELLED_STATUS})

    def test_excluded_branches_include_overview_ones(self):
        self.assertIn("1000000139", d.EXCLUDED_BRANCHES)
        self.assertIn("1000000477", d.EXCLUDED_BRANCHES)

    def test_pipeline_terminal_codes(self):
        for code in ("closed", "spam", "not_buying", "cancelled", "archived", "warranty", "underlying_condition"):
            self.assertIn(code, d.PIPELINE_TERMINAL_CODES)
        for code in ("no_answer_3", "call_back_later", "scheduled", "wants_store_visit"):
            self.assertNotIn(code, d.PIPELINE_TERMINAL_CODES)

    def test_poor_quality_data_is_dirty_and_terminal(self):
        """"Data không chất lượng" là data bẩn: loại khỏi mẫu số Năng lực Sale,
        rời pipeline và bị coi là lead dead trên sheet — cùng nhóm với spam."""
        from apps.orders.sheet_rules import DEAD_SALES_STATUSES

        self.assertEqual(d.bucket_of("poor_quality_data"), "dirty")
        self.assertIn("poor_quality_data", d.PIPELINE_TERMINAL_CODES)
        self.assertIn("poor_quality_data", DEAD_SALES_STATUSES)

    def test_glossary_covers_every_metric_key(self):
        for key in ("rows", "lead", "closed", "closed_revenue", "close_rate", "orders", "total_revenue", "net_revenue", "open_leads", "ads_rows", "remainder_row"):
            self.assertIn(key, d.METRIC_GLOSSARY)
