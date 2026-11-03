"""Token bucket điều tốc request ra Pancake POS (tài liệu không công bố trần gọi, §2).

Thread-safe; đồng hồ và hàm ngủ tiêm được để test không phải chờ thật.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class TokenBucket:
    def __init__(
        self,
        rate: float,
        capacity: float | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate phải > 0")
        self.rate = float(rate)
        # Mặc định burst = 1: không dồn request đầu lượt, đều nhịp ngay từ đầu.
        self.capacity = float(capacity if capacity is not None else 1.0)
        self._tokens = self.capacity
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(now - self._last, 0.0)
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)

    def acquire(self) -> float:
        """Lấy 1 token, ngủ nếu cần. Trả tổng thời gian đã chờ (giây)."""
        waited = 0.0
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return waited
                wait = (1.0 - self._tokens) / self.rate
            self._sleep(wait)
            waited += wait
