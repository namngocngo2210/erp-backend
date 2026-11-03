from datetime import date, datetime, UTC

from django.test import SimpleTestCase

from apps.daily_report.domain import periods
from apps.daily_report.domain.periods import ParamError


class PeriodTests(SimpleTestCase):
    def test_yesterday_uses_vietnam_day_not_utc(self):
        # 2026-09-03 00:30 giờ VN = 2026-09-02 17:30 UTC. Hôm qua theo VN là 02/09.
        now = datetime(2026, 9, 2, 17, 30, tzinfo=UTC)
        self.assertEqual(periods.today_vn(now), date(2026, 9, 3))
        self.assertEqual(periods.yesterday_vn(now), date(2026, 9, 2))

    def test_default_range_is_yesterday(self):
        now = datetime(2026, 9, 2, 17, 30, tzinfo=UTC)
        self.assertEqual(periods.parse_range({}, now=now), (date(2026, 9, 2), date(2026, 9, 2)))

    def test_explicit_range(self):
        got = periods.parse_range({"from": "2026-08-01", "to": "2026-08-31"})
        self.assertEqual(got, (date(2026, 8, 1), date(2026, 8, 31)))

    def test_from_after_to_rejected(self):
        with self.assertRaises(ParamError):
            periods.parse_range({"from": "2026-08-02", "to": "2026-08-01"})

    def test_range_over_92_days_rejected(self):
        with self.assertRaises(ParamError):
            periods.parse_range({"from": "2026-01-01", "to": "2026-04-03"})  # 93 ngày
        periods.parse_range({"from": "2026-01-01", "to": "2026-04-02"})  # 92 ngày: ok

    def test_bad_date_rejected_with_field_name(self):
        with self.assertRaises(ParamError) as ctx:
            periods.parse_range({"from": "01/08/2026"})
        self.assertIn("`from`", str(ctx.exception))

    def test_non_yyyy_mm_dd_format_rejected(self):
        # Test that only YYYY-MM-DD format is accepted, not other ISO formats
        invalid_formats = ["20260101", "2026-W01-2", "2026-W01", "2026-1-5"]
        for fmt in invalid_formats:
            with self.assertRaises(ParamError) as ctx:
                periods.parse_range({"from": fmt, "to": "2026-01-10"})
            self.assertIn("`from`", str(ctx.exception), f"Field name should be in error for format {fmt}")
        # Verify that the correct format still works
        self.assertEqual(periods.parse_range({"from": "2026-01-05", "to": "2026-01-10"}), (date(2026, 1, 5), date(2026, 1, 10)))

    def test_choice_and_int(self):
        self.assertEqual(periods.parse_choice({}, "group_by", ("none", "day"), default="none"), "none")
        self.assertEqual(periods.parse_choice({"group_by": "day"}, "group_by", ("none", "day"), default="none"), "day")
        with self.assertRaises(ParamError):
            periods.parse_choice({"group_by": "week"}, "group_by", ("none", "day"), default="none")
        self.assertEqual(periods.parse_int({"lookback_days": "45"}, "lookback_days", default=30, minimum=1, maximum=92), 45)
        with self.assertRaises(ParamError):
            periods.parse_int({"lookback_days": "0"}, "lookback_days", default=30, minimum=1, maximum=92)
        with self.assertRaises(ParamError):
            periods.parse_int({"lookback_days": "abc"}, "lookback_days", default=30, minimum=1, maximum=92)
