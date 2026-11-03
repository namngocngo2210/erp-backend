import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Initialize Django app registry first to avoid AppRegistryNotReady
# when websocket middleware imports auth-related modules.
django_asgi_app = get_asgi_application()

from django.conf import settings  # noqa: E402

from apps.channels_core.jwt_middleware import JwtAuthMiddlewareStack  # noqa: E402
from apps.channels_core.routing import websocket_urlpatterns as crm_ws  # noqa: E402
from apps.common.asgi_concurrency import HttpConcurrencyLimiter  # noqa: E402
from apps.common.log_scrub import install_uvicorn_scrub_filters  # noqa: E402

# Che `token=` trong access log của uvicorn (gunicorn UvicornWorker gắn handler
# riêng cho uvicorn.access/uvicorn.error nên LOGGING của Django không phủ tới) —
# audit 2026-09-23 H2. Gọi ở đây vì asgi.py là điểm nạp duy nhất của worker web.
install_uvicorn_scrub_filters()

# Gate concurrency HTTP (sự cố 2026-08-03) — chỉ bọc nhánh "http"; websocket đã
# có semaphore riêng trong handshake/consumer. Bypass healthz (probe ngoài) và
# /api/orders/metrics (healthcheck compose): probe kẹt hàng đợi lúc burst là
# container bị đánh unhealthy → restart giữa bão.
# Bypass thêm refresh token (bão 2026-08-04 09:13: refresh dính 503 → user văng
# phiên giữa burst, retry đăng nhập chỉ đổ thêm dầu). Refresh rẻ (verify JWT +
# 1 write nhỏ, PgBouncer cap connection) — KHÔNG bypass /api/users/token (login):
# hash mật khẩu nặng CPU, thả không trần lúc bão là tự bắn vào chân.
application = ProtocolTypeRouter(
    {
        "http": HttpConcurrencyLimiter(
            django_asgi_app,
            max_concurrency=settings.WEB_HTTP_CONCURRENCY,
            queue_timeout=settings.WEB_HTTP_QUEUE_TIMEOUT,
            bypass_prefixes=(
                "/api/healthz",
                "/api/orders/metrics",
                "/api/users/token/refresh",
            ),
        ),
        "websocket": JwtAuthMiddlewareStack(URLRouter(crm_ws)),
    }
)
