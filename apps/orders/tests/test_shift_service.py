"""Kiểm thử khung giờ ca: kết thúc bao-trọn-phút + ca cuộn qua đêm.

Quy ước nghiệp vụ (ví dụ đội sale dùng):
- Sáng   21:30 → 11:59  (cuộn qua nửa đêm)
- Chiều  12:00 → 17:29
- Tối    17:30 → 21:29
Giờ kết thúc bao trọn cả phút đó (11:59 = tới 11:59:59). Không thời điểm nào
rơi vào 2 ca, và (với cấu hình này) phủ kín 24h.
"""
from datetime import date, datetime, time

from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.orders.application.services.shift_service import (
    ShiftService,
    _end_minute_inclusive,
    shift_intervals,
)
from apps.orders.models import ShiftWindow
from apps.users.tests.helpers import make_admin


def _set_windows(mapping):
    """mapping: {shift: (start "HH:MM", end "HH:MM")}. None value = tắt ca."""
    ShiftWindow.objects.all().delete()
    for shift, val in mapping.items():
        if val is None:
            continue
        s, e = val
        sh, sm = map(int, s.split(":"))
        eh, em = map(int, e.split(":"))
        ShiftWindow.objects.create(shift=shift, start_time=time(sh, sm), end_time=time(eh, em))
    ShiftService.invalidate_cache()


OVERNIGHT = {
    "morning": ("21:30", "11:59"),
    "afternoon": ("12:00", "17:29"),
    "evening": ("17:30", "21:29"),
}


class ShiftIntervalHelperTests(SimpleTestCase):
    def test_inclusive_end_minute(self):
        self.assertEqual(_end_minute_inclusive(time(11, 59)), 11 * 60 + 59)
        # end=00:00 hiểu là cuối ngày → phút cuối 23:59.
        self.assertEqual(_end_minute_inclusive(time(0, 0)), 24 * 60 - 1)

    def test_normal_shift_single_interval(self):
        self.assertEqual(shift_intervals(time(12, 0), time(17, 29)), [(720, 1049)])

    def test_overnight_shift_two_intervals(self):
        self.assertEqual(
            shift_intervals(time(21, 30), time(11, 59)),
            [(1290, 1439), (0, 719)],
        )


class ShiftForTimeOvernightTests(TestCase):
    def setUp(self):
        _set_windows(OVERNIGHT)

    def _shift(self, h, m, s=0):
        return ShiftService.shift_for_time(time(h, m, s))

    def test_boundaries_map_to_expected_shift(self):
        cases = {
            (0, 0, 0): "morning",
            (2, 0, 0): "morning",
            (11, 59, 30): "morning",   # :59:59 vẫn thuộc sáng (bao trọn phút)
            (12, 0, 0): "afternoon",
            (17, 29, 59): "afternoon",
            (17, 30, 0): "evening",
            (21, 29, 59): "evening",
            (21, 30, 0): "morning",
            (23, 0, 0): "morning",
        }
        for tm, expected in cases.items():
            self.assertEqual(self._shift(*tm), expected, tm)

    def test_covers_every_minute_exactly_once(self):
        # Cấu hình này phủ kín 24h → không phút nào là None (ngoài giờ).
        counts = {}
        for total in range(24 * 60):
            sh = self._shift(total // 60, total % 60)
            self.assertIsNotNone(sh, total)
            counts[sh] = counts.get(sh, 0) + 1
        self.assertEqual(sum(counts.values()), 24 * 60)
        # Sáng = 21:30→23:59 (150') + 00:00→11:59 (720') = 870'.
        self.assertEqual(counts["morning"], 870)
        self.assertEqual(counts["afternoon"], 17 * 60 + 30 - 12 * 60)   # 12:00→17:29 = 330'
        self.assertEqual(counts["evening"], 21 * 60 + 30 - (17 * 60 + 30))  # 17:30→21:29 = 240'


class ShiftForTimeNormalTests(TestCase):
    def test_gap_returns_none_and_inclusive_end(self):
        _set_windows(
            {
                "morning": ("08:00", "11:59"),
                "afternoon": ("13:30", "17:29"),
                "evening": ("17:30", "21:29"),
            }
        )
        # Khe nghỉ trưa 12:00–13:29 không thuộc ca nào → None.
        self.assertIsNone(ShiftService.shift_for_time(time(12, 30)))
        self.assertEqual(ShiftService.shift_for_time(time(8, 0)), "morning")
        # Kết thúc bao trọn phút: 11:59:59 vẫn là sáng.
        self.assertEqual(ShiftService.shift_for_time(time(11, 59, 59)), "morning")
        # Đúng mốc giao ca 17:30 thuộc tối (không dính chiều).
        self.assertEqual(ShiftService.shift_for_time(time(17, 30)), "evening")


class ShiftAndDateForOvernightTests(TestCase):
    """Ngày cấu hình chia đơn: ca cuộn qua đêm neo theo ngày KẾT THÚC ca.

    Đoạn trước nửa đêm (21:30→23:59) thuộc ca sáng của NGÀY HÔM SAU — đơn vào
    22:00 ngày 05/07 phải ăn config ngày 06/07.
    """

    def setUp(self):
        _set_windows(OVERNIGHT)

    def _at(self, h, m, s=0):
        return ShiftService.shift_and_date_for(datetime(2026, 7, 5, h, m, s))

    def test_overnight_shift_maps_each_segment_to_its_business_day(self):
        # Đoạn trước nửa đêm thuộc ngày HÔM SAU, đoạn sau nửa đêm giữ nguyên ngày.
        for (h, m, sec), expected_day in (
            ((21, 30, 0), date(2026, 7, 6)),
            ((23, 59, 59), date(2026, 7, 6)),
            ((0, 0, 0), date(2026, 7, 5)),
            ((11, 59, 59), date(2026, 7, 5)),
        ):
            with self.subTest(time=f"{h:02d}:{m:02d}:{sec:02d}"):
                self.assertEqual(self._at(h, m, sec), ("morning", expected_day))

    def test_normal_shifts_keep_same_day(self):
        self.assertEqual(self._at(12, 0), ("afternoon", date(2026, 7, 5)))
        self.assertEqual(self._at(17, 30), ("evening", date(2026, 7, 5)))

    def test_outside_all_windows_returns_none_same_day(self):
        _set_windows(
            {
                "morning": ("08:00", "11:59"),
                "afternoon": ("13:30", "17:29"),
                "evening": None,
            }
        )
        self.assertEqual(self._at(12, 30), (None, date(2026, 7, 5)))


class ShiftWindowsOvernightApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin_sw")
        self.client.force_authenticate(self.admin)

    def _put(self, windows):
        return self.client.put(
            "/api/orders/lead-assignment/shift-windows",
            {"windows": windows},
            format="json",
        )

    def test_accepts_one_overnight_shift(self):
        resp = self._put(
            [
                {"shift": "morning", "start_time": "21:30", "end_time": "11:59"},
                {"shift": "afternoon", "start_time": "12:00", "end_time": "17:29"},
                {"shift": "evening", "start_time": "17:30", "end_time": "21:29"},
            ]
        )
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_rejects_invalid_overnight_layouts(self):
        for label, morning, afternoon in (
            ("two_overnight_shifts", ("21:30", "11:59"), ("23:00", "05:00")),
            # morning kết thúc 12:00 (bao tới 12:00:59) chồng lên afternoon bắt đầu 12:00.
            ("overlap_at_inclusive_boundary", ("21:30", "12:00"), ("12:00", "17:29")),
        ):
            with self.subTest(label):
                resp = self._put(
                    [
                        {"shift": "morning", "start_time": morning[0], "end_time": morning[1]},
                        {"shift": "afternoon", "start_time": afternoon[0], "end_time": afternoon[1]},
                        {"shift": "evening", "start_time": "17:30", "end_time": "21:29"},
                    ]
                )
                self.assertEqual(resp.status_code, 400)
