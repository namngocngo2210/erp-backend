"""Redis connection basics dùng chung cho base.py và celery.py (settings parts)."""

import os
from urllib.parse import quote

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

# ── Instance RIÊNG cho cache (audit cache/latency 2026-08-11, mục 2.1) ─────────
# Cache, Celery broker và channel layer từng nằm chung một Redis. Hai vai trò đó
# có yêu cầu NGƯỢC NHAU và không hoà giải được bằng cấu hình:
#   - cache muốn `allkeys-lru` (đầy thì đẩy key cũ ra) và không cần bền vững;
#   - broker muốn `noeviction` (mất message là mất job) và cần AOF.
# Để chung với `noeviction` nghĩa là một đợt cache phình sẽ làm Redis từ chối mọi
# lệnh ghi → Celery và WebSocket chết theo. Tách ra là cách duy nhất.
#
# Mặc định RƠI VỀ instance dùng chung: không đặt env mới thì hành vi y hệt trước
# 2026-08-11. Đây là điều kiện để rollback chỉ bằng env, không cần deploy lại.
# `or` thay vì default của getenv: compose passthrough có thể truyền chuỗi rỗng.
REDIS_CACHE_HOST = os.getenv("REDIS_CACHE_HOST", "").strip() or REDIS_HOST
REDIS_CACHE_PORT = os.getenv("REDIS_CACHE_PORT", "").strip() or REDIS_PORT
# Password đọc riêng để instance cache có thể dùng mật khẩu khác (hoặc không đặt
# mật khẩu khi nó chỉ nghe trong mạng nội bộ compose). Không đặt → dùng chung.
REDIS_CACHE_PASSWORD = os.getenv("REDIS_CACHE_PASSWORD", "").strip() or REDIS_PASSWORD


def _build_url(host: str, port: str, password: str, db: str) -> str:
    auth = f":{quote(password, safe='')}@" if password else ""
    return f"redis://{auth}{host}:{port}/{db}"


def build_redis_url(db: str) -> str:
    """URL tới Redis dùng chung (broker, channel layer)."""
    return _build_url(REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, db)


def build_redis_cache_url(db: str) -> str:
    """URL tới Redis cache — trùng với `build_redis_url` nếu chưa tách instance."""
    return _build_url(REDIS_CACHE_HOST, REDIS_CACHE_PORT, REDIS_CACHE_PASSWORD, db)
