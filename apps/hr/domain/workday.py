"""Tính công ngày từ giờ vào/ra — rule chốt với CEO 2026-07-13.

Ngày công 8h gồm 2 buổi (sáng 08:00–12:00, chiều 13:30–17:30). Buổi nào được
phủ ĐỦ bởi [giờ vào, giờ ra] thì auto 0.5 công; buổi thiếu thì tính phần giờ
làm thực tế trong buổi đó / 8h. Đi muộn/về sớm đếm theo phút so với mốc
08:00 / 17:30.

Pure module: không phụ thuộc Django. Giờ làm lấy từ settings do application
truyền vào (``attendance_export_service.schedule_from_settings`` →
``schedule_from_values``) — audit 2026-09-23 T3/P2-2.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from decimal import ROUND_HALF_UP, Decimal

FULL_DAY_MINUTES = 480  # 8h


@dataclass(frozen=True)
class WorkSchedule:
    morning_start: time
    morning_end: time
    afternoon_start: time
    afternoon_end: time

    @property
    def sessions(self) -> tuple[tuple[time, time], tuple[time, time]]:
        return (
            (self.morning_start, self.morning_end),
            (self.afternoon_start, self.afternoon_end),
        )


@dataclass(frozen=True)
class WorkdayResult:
    late_minutes: int
    early_minutes: int
    worked_minutes: int
    workday_credit: Decimal  # 0 → 1.00, bội của phần giờ làm / 8h, đủ buổi = 0.5


def _parse_hhmm(raw: str, fallback: str) -> time:
    value = (raw or fallback).strip()
    hour, _, minute = value.partition(":")
    return time(int(hour), int(minute or 0))


def schedule_from_values(
    *,
    morning_start: str = "",
    morning_end: str = "",
    afternoon_start: str = "",
    afternoon_end: str = "",
) -> WorkSchedule:
    """Dựng lịch làm từ chuỗi "HH:MM" (rỗng → mặc định rule CEO 2026-07-13)."""
    return WorkSchedule(
        morning_start=_parse_hhmm(morning_start, "08:00"),
        morning_end=_parse_hhmm(morning_end, "12:00"),
        afternoon_start=_parse_hhmm(afternoon_start, "13:30"),
        afternoon_end=_parse_hhmm(afternoon_end, "17:30"),
    )


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def evaluate_workday(
    check_in: time | None, check_out: time | None, schedule: WorkSchedule
) -> WorkdayResult:
    """Tính muộn/sớm/giờ làm/công cho một ngày. Thiếu vào HOẶC ra → 0 công."""
    if check_in is None or check_out is None:
        return WorkdayResult(0, 0, 0, Decimal("0"))

    start = _minutes(check_in)
    end = max(_minutes(check_out), start)

    late = max(0, start - _minutes(schedule.morning_start))
    early = max(0, _minutes(schedule.afternoon_end) - end)

    worked = 0
    credit = Decimal("0")
    for session_start, session_end in schedule.sessions:
        s, e = _minutes(session_start), _minutes(session_end)
        overlap = max(0, min(end, e) - max(start, s))
        worked += overlap
        if overlap >= e - s:
            credit += Decimal("0.5")
        else:
            credit += Decimal(overlap) / Decimal(FULL_DAY_MINUTES)

    credit = credit.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return WorkdayResult(
        late_minutes=late, early_minutes=early, worked_minutes=worked, workday_credit=credit
    )
