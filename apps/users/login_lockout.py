"""Khoá tạm tài khoản sau N lần đăng nhập sai liên tiếp (audit 2026-09-23 M5).

Lớp chống brute-force thứ HAI, bổ sung cho throttle theo IP (`LoginRateThrottle`)
và theo (username, IP) (`LoginUserThrottle`): kẻ tấn công đổi IP liên tục vẫn bị
chặn ở đây vì khoá gắn với USERNAME. Đếm trên cache dùng chung (Redis) để mọi
worker/web replica thấy cùng một bộ đếm.

Luật (đọc từ settings, có env):
- `LOGIN_LOCKOUT_THRESHOLD` (mặc định 10) lần sai liên tiếp → khoá
  `LOGIN_LOCKOUT_SECONDS` (mặc định 900 = 15 phút).
- Đăng nhập ĐÚNG xoá bộ đếm; khi đang khoá thì kể cả mật khẩu đúng cũng bị 429
  (không thì kẻ tấn công thử tiếp được ngay khi đoán trúng).
- Fail-open như mọi throttle của repo: cache lỗi → không khoá, ghi log
  (`apps.common.throttling.ThrottleGuardMixin` cùng triết lý).

Không dùng django-axes: thêm middleware + bảng riêng cho một luật 40 dòng là quá
tay, và axes khoá theo IP mặc định — đúng thứ ta đã có ở nginx zone.
"""

from __future__ import annotations

import hashlib
import logging

from django.conf import settings
from django.core.cache import cache

from apps.common import rate_limiter

logger = logging.getLogger(__name__)

_FAIL_KEY = "login_fail:{}"
_LOCK_KEY = "login_lock:{}"

# Scope ghi vào metric desi_throttled_requests_total khi một tài khoản bị khoá.
LOCKOUT_METRIC_SCOPE = "login_lockout"


def normalize_username(username) -> str:
    """Key theo username đã hạ chữ/thu gọn — hash để không đưa username thô vào key
    cache (log Redis, `KEYS *`) và để key có độ dài cố định."""
    text = str(username or "").strip().lower()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _threshold() -> int:
    return max(int(getattr(settings, "LOGIN_LOCKOUT_THRESHOLD", 10)), 1)


def _lock_seconds() -> int:
    return max(int(getattr(settings, "LOGIN_LOCKOUT_SECONDS", 900)), 1)


def is_locked(username) -> bool:
    key = normalize_username(username)
    if not key:
        return False
    try:
        return cache.get(_LOCK_KEY.format(key)) is not None
    except Exception:
        logger.exception("login lockout: cache lỗi khi đọc khoá — bỏ qua (fail-open)")
        return False


def lock_seconds_remaining(username) -> int:
    """Số giây còn khoá (ước lượng: luôn trả trọn cửa sổ nếu cache không hỗ trợ TTL)."""
    key = normalize_username(username)
    if not key:
        return 0
    try:
        client_ttl = getattr(cache, "ttl", None)
        if callable(client_ttl):
            ttl = client_ttl(_LOCK_KEY.format(key))
            if ttl:
                return int(ttl)
        return _lock_seconds() if cache.get(_LOCK_KEY.format(key)) is not None else 0
    except Exception:
        return _lock_seconds()


def record_failure(username) -> int:
    """Ghi một lần sai; trả số lần sai liên tiếp hiện tại. Đạt ngưỡng → đặt khoá."""
    key = normalize_username(username)
    if not key:
        return 0
    fail_key = _FAIL_KEY.format(key)
    window = _lock_seconds()
    try:
        # add + incr: atomic trên Redis; cửa sổ đếm = cửa sổ khoá (sai rải rác quá
        # 15 phút thì tự quên, không tích luỹ vô hạn).
        cache.add(fail_key, 0, timeout=window)
        try:
            count = int(cache.incr(fail_key))
        except ValueError:
            # Key vừa hết hạn giữa add và incr — bắt đầu lại từ 1.
            cache.set(fail_key, 1, timeout=window)
            count = 1
        if count >= _threshold():
            cache.set(_LOCK_KEY.format(key), 1, timeout=window)
            cache.delete(fail_key)
            rate_limiter.record_throttled(LOCKOUT_METRIC_SCOPE)
            logger.warning("login lockout: tài khoản bị khoá %ss sau %s lần sai", window, count)
        return count
    except Exception:
        logger.exception("login lockout: cache lỗi khi ghi lần sai — bỏ qua (fail-open)")
        return 0


def reset(username) -> None:
    """Đăng nhập đúng → xoá bộ đếm (khoá đang hiệu lực thì login_view đã chặn trước)."""
    key = normalize_username(username)
    if not key:
        return
    try:
        cache.delete(_FAIL_KEY.format(key))
    except Exception:
        logger.exception("login lockout: cache lỗi khi reset — bỏ qua (fail-open)")
