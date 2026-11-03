"""Healthcheck sâu cho giám sát ngoài (UptimeRobot/Better Stack) — GET /api/healthz.

Khác ``/healthz`` của nginx (trả 200 tĩnh ngay tại edge, không chứng minh app
sống): endpoint này chạm DB (SELECT 1) + Redis cache round-trip, trả 503 khi có
thành phần chết — probe HTTP bên ngoài VPS nhờ đó phát hiện được app/hạ tầng
gãy kể cả khi Prometheus (chạy cùng VPS) đã chết theo.

Không auth (probe ngoài không có JWT) nhưng không lộ dữ liệu: body chỉ có trạng
thái ok/fail từng thành phần. Nằm trong SKIP_PATHS của AuditLogMiddleware để
probe định kỳ không spam bảng audit_logs.
"""

import logging

from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)

_REDIS_PING_KEY = "healthz:ping"


def check_db() -> bool:
    """Public: apps.daily_report tái dùng cho /api/daily-report/health."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            row = cursor.fetchone()
        return bool(row) and row[0] == 1
    except Exception:
        logger.exception("healthz: DB check failed")
        return False


def check_redis() -> bool:
    """Public: apps.daily_report tái dùng cho /api/daily-report/health."""
    try:
        cache.set(_REDIS_PING_KEY, "1", timeout=10)
        return cache.get(_REDIS_PING_KEY) == "1"
    except Exception:
        logger.exception("healthz: Redis check failed")
        return False


@never_cache
@require_GET
def healthz(request):
    checks = {"db": check_db(), "redis": check_redis()}
    ok = all(checks.values())
    return JsonResponse(
        {"status": "ok" if ok else "degraded", "checks": checks},
        status=200 if ok else 503,
    )
