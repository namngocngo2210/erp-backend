"""Error tracking (Sentry) — env-gated, no-op hoàn toàn khi SENTRY_DSN trống.

Được import từ settings_parts/base.py (cuối file). Vận hành bật bằng cách set
SENTRY_DSN (RB.8, self-host GlitchTip hoặc Sentry SaaS) cho web/worker/beat.

Env:
- SENTRY_DSN                  — trống = tắt (default).
- SENTRY_TRACES_SAMPLE_RATE   — tỉ lệ trace performance, default 0 (tắt tracing).
- SENTRY_RELEASE              — optional, gắn version release.
- METRICS_ALLOW_TOKEN         — trống = tắt; xem ghi chú ở dưới.
"""

import os

# Phòng thủ LỚP HAI cho /api/orders/metrics. Lớp một là nginx (chặn theo dải IP nội
# bộ), nhưng repo có hai file nginx và trước 2026-07-26 chỉ file của host mới chặn —
# môi trường nào dùng file còn lại thì metrics phơi ra internet (audit C3). Đặt biến
# này để việc chặn không phụ thuộc hoàn toàn vào chuyện chọn đúng file nginx.
# Trống = giữ nguyên hành vi cũ (scraper nội bộ không cần sửa gì).
METRICS_ALLOW_TOKEN = os.getenv("METRICS_ALLOW_TOKEN", "").strip()

SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()

if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0")),
        # Không gửi PII (user, cookies, IP) — dữ liệu khách hàng không được rời hệ thống.
        send_default_pii=False,
        environment=os.getenv("APP_ENV", "development").strip().lower(),
        release=os.getenv("SENTRY_RELEASE", "").strip() or None,
    )
