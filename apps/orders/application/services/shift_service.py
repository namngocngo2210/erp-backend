"""Xác định ca làm việc hiện tại theo khung giờ cấu hình (ShiftWindow).

Khung giờ không bắt buộc phủ kín 24h và mỗi ca có thể tắt. Một thời điểm rơi ngoài
mọi khung giờ đã cấu hình ⇒ "ngoài giờ làm việc" (``None``) ⇒ không chia đơn tự động.
"""
from __future__ import annotations

import logging
import time as _time_mod
from datetime import date, datetime, time, timedelta

from django.core.cache import cache
from django.utils import timezone

from apps.orders.models import ShiftWindow

logger = logging.getLogger(__name__)

# end_time == 00:00 nghĩa là "cuối ngày" (24:00), không phải nửa đêm đầu ngày.
_DAY_END_MINUTES = 24 * 60

# Generation-counter trên Redis: mỗi lần khung giờ đổi, counter tăng. Mọi process
# đọc counter (1 GET rẻ) ở đầu _windows(); counter khác local ⇒ nạp lại NGAY, không
# phải chờ TTL. Bọc try/except để Redis lỗi không làm vỡ luồng (vẫn còn TTL fallback).
_GEN_CACHE_KEY = "lead_assignment:shift_windows_gen"


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _end_minute_inclusive(end: time) -> int:
    """Phút cuối (inclusive) mà ca còn hiệu lực trong ngày.

    Giờ kết thúc bao trọn cả phút đó: end=11:59 phủ tới 11:59:59 (phút 719).
    end=00:00 hiểu là "cuối ngày" ⇒ phút cuối là 23:59.
    """
    m = _minutes(end)
    return _DAY_END_MINUTES - 1 if m == 0 else m


def shift_intervals(start: time, end: time) -> list[tuple[int, int]]:
    """Các đoạn [phút_đầu, phút_cuối] (đều inclusive) mà ca phủ trên trục 24h.

    Ca thường (start ≤ end) → 1 đoạn. Ca cuộn qua đêm (start > end) → 2 đoạn:
    ``[start, 23:59]`` và ``[00:00, end]``. Số lượng đoạn = 2 cũng chính là dấu
    hiệu "ca cuộn qua đêm" dùng cho validate.
    """
    s = _minutes(start)
    e = _end_minute_inclusive(end)
    if s <= e:
        return [(s, e)]
    return [(s, _DAY_END_MINUTES - 1), (0, e)]


class ShiftService:
    # cache list[(start: time, end: time, shift)] chỉ gồm ca đã cấu hình (đủ start & end),
    # sắp tăng dần theo start_time; None = chưa nạp.
    _windows_cache: list[tuple[time, time, str]] | None = None
    # Thời điểm (monotonic) cache được nạp; dùng để tính tuổi cache cho TTL.
    _cache_loaded_at: float | None = None
    # Generation đã nạp cùng cache hiện tại (để so với gen Redis phát hiện thay đổi).
    _cache_gen: int | None = None
    # TTL ngắn: fallback nếu Redis không khả dụng (lúc đó không có gen). Khi Redis
    # hoạt động, invalidate đẩy gen mới ⇒ mọi process nạp lại gần như tức thì.
    _CACHE_TTL_SECONDS = 60

    @staticmethod
    def _read_gen() -> int | None:
        try:
            return cache.get(_GEN_CACHE_KEY)
        except Exception:  # Redis lỗi → coi như không có gen, dùng TTL fallback.
            return None

    @classmethod
    def _bump_gen(cls) -> None:
        """Tăng generation trên Redis (atomic). Khởi tạo nếu key chưa tồn tại."""
        try:
            try:
                cache.incr(_GEN_CACHE_KEY)
            except ValueError:
                # Key chưa có: tạo mới. add() không ghi đè nếu process khác vừa tạo.
                cache.add(_GEN_CACHE_KEY, 1)
        except Exception:
            pass

    @classmethod
    def invalidate_cache(cls) -> None:
        cls._windows_cache = None
        cls._cache_loaded_at = None
        cls._cache_gen = None
        cls._bump_gen()

    @classmethod
    def _windows(cls) -> list[tuple[time, time, str]]:
        # Cache cố tình không khóa: việc rebuild là idempotent và gán dưới GIL,
        # nên đua giữa thread (cùng process) cùng lắm chỉ rebuild thừa, không sai.
        now = _time_mod.monotonic()
        gen = cls._read_gen()
        gen_changed = gen is not None and gen != cls._cache_gen
        is_stale = (
            cls._cache_loaded_at is None
            or now - cls._cache_loaded_at >= cls._CACHE_TTL_SECONDS
        )
        if cls._windows_cache is None or is_stale or gen_changed:
            rows = ShiftWindow.objects.filter(
                start_time__isnull=False, end_time__isnull=False
            ).values_list("start_time", "end_time", "shift")
            cls._windows_cache = sorted(rows, key=lambda r: r[0])
            cls._cache_loaded_at = _time_mod.monotonic()
            cls._cache_gen = gen
        return cls._windows_cache

    @classmethod
    def _match(cls, t: time) -> tuple[str, bool] | None:
        """(ca, có_phải_đoạn_trước_nửa_đêm_của_ca_cuộn_qua_đêm) chứa ``t``; None nếu ngoài giờ."""
        tm = _minutes(t)
        for start, end, shift in cls._windows():
            intervals = shift_intervals(start, end)
            for idx, (lo, hi) in enumerate(intervals):
                if lo <= tm <= hi:
                    return shift, len(intervals) == 2 and idx == 0
        return None

    @classmethod
    def shift_for_time(cls, t: time) -> str | None:
        """Ca chứa thời điểm ``t``; None nếu rơi ngoài mọi khung giờ đã cấu hình.

        Giờ kết thúc bao trọn phút đó (11:59 ⇒ tới 11:59:59) và hỗ trợ ca cuộn
        qua đêm (start > end). So khớp ở mức phút.
        """
        matched = cls._match(t)
        return matched[0] if matched else None

    @classmethod
    def shift_and_date_for(cls, dt: datetime) -> tuple[str | None, date]:
        """Ca chứa ``dt`` và NGÀY cấu hình chia đơn tương ứng.

        Ca cuộn qua đêm neo theo ngày chứa phần kết thúc ca: thời điểm rơi vào
        đoạn trước nửa đêm (start→23:59) thuộc ca của NGÀY HÔM SAU (ví dụ ca
        sáng 21:30→11:59 thì 22:00 ngày 05/07 ăn config ngày 06/07).
        """
        matched = cls._match(dt.time())
        if matched is None:
            return None, dt.date()
        shift, pre_midnight = matched
        config_date = dt.date() + timedelta(days=1) if pre_midnight else dt.date()
        return shift, config_date

    @classmethod
    def current_shift(cls) -> str | None:
        return cls.shift_for_time(timezone.localtime().time())

    @classmethod
    def current_shift_and_date(cls) -> tuple[str | None, date]:
        """Ca hiện tại + ngày cấu hình chia đơn (đã xử lý ca cuộn qua đêm)."""
        return cls.shift_and_date_for(timezone.localtime())
