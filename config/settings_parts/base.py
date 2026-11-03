import hashlib
import json
import logging
import os
import socket
import sys
from datetime import timedelta
from pathlib import Path

from corsheaders.defaults import default_headers
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# Load environment variables from .env file. Ba import tương đối phía dưới
# (`._redis`, `.celery`, `.observability`) đọc os.getenv lúc import nên BẮT BUỘC
# đứng sau lời gọi này — đó là 3 chỗ E402 còn lại có chủ đích.
load_dotenv()

_settings_log = logging.getLogger(__name__)


def _require_production_env(name: str, *, why: str) -> None:
    """Fail-fast khi production thiếu biến env nghiệp vụ (audit 2026-09-23 T3/P2-7).

    Trước đây 3 biến id kênh/kho prod được hard-code làm default trong code và chỉ
    WARNING khi thiếu — staging/restore DB khác thứ tự pk sẽ âm thầm trỏ nhầm kênh.
    Deploy thiếu env phải chết ngay lúc nạp settings, cùng triết lý
    PRODUCTION_FORBIDDEN_SECRETS. Giá trị prod hiện hành ghi ở
    docs/runbooks/2026-09-23-audit-ops.md mục "Backend".
    """
    if IS_PRODUCTION and not (os.environ.get(name) or "").strip():
        raise ImproperlyConfigured(
            f"{name} chưa khai trong env production ({why}). "
            "Xem docs/runbooks/2026-09-23-audit-ops.md mục Backend."
        )


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-secret-key-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"
IS_STAGING = APP_ENV == "staging"
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "channels",
    "django_filters",
    "drf_spectacular",
    # Beat scheduler bền vững (audit 7.5): DatabaseScheduler lưu lịch trong DB,
    # sync CELERY_BEAT_SCHEDULE code-defined lúc beat start.
    "django_celery_beat",
    "apps.users",
    "apps.sysconfig",
    "apps.channels_core",
    "apps.catalog",
    "apps.customers",
    "apps.orders",
    "apps.kiotviet",
    "apps.audit",
    "apps.facebook_ads",
    "apps.google_ads",
    "apps.warranty",
    "apps.dispatch",
    "apps.warehouses",
    "apps.inventory",
    "apps.hr",
    "apps.best_express",
    "apps.cnv_loyalty",
    "apps.foxia",
    "apps.callcenter",
    "apps.contact_flow",
    "apps.warranty_codes",
    "apps.exports",
    "apps.analytics",
    "apps.ai_mart",
    "apps.daily_report",
    "apps.conversion_feed",
    "apps.media",
    "apps.pancake",
    "apps.pancake_pos",
]

MIDDLEWARE = [
    # Đặt đầu chuỗi để mọi log phát sinh trong request đều mang request_id.
    "apps.common.request_id.RequestIdMiddleware",
    # Ngay sau request_id: đo trọn thời gian server xử lý (gồm cả các middleware
    # bên dưới). RED metrics theo route pattern — xem apps/common/http_metrics.py.
    "apps.common.http_metrics.HttpMetricsMiddleware",
    # Allowlist IP cho /admin (env DJANGO_ADMIN_ALLOWED_CIDRS; rỗng = mở) — trước
    # SecurityMiddleware để IP lạ nhận 404 sớm nhất, không chạm session/CSRF/audit.
    "apps.common.admin_ip_allowlist.AdminIpAllowlistMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # Phục vụ /static/ (CSS/JS của Django admin, Swagger) ngay trong app khi
    # DEBUG=0. nginx prod là config trên HOST, không có `location /static/`, mọi
    # path đều proxy về web → trước đây admin hiện ra không CSS (2026-09-03).
    # WhiteNoise yêu cầu đứng ngay sau SecurityMiddleware để request static không
    # đi qua session/csrf/audit.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.audit.middleware.AuditLogMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# ── Nén JSON: Python hay nginx? (audit cache/latency 2026-08-11, mục 2.4) ──────
# `desi-web` bị bó 1.5 CPU và nút cổ chai lúc bão đã được xác định là CPU (sự cố
# 2026-08-04), trong khi GZipMiddleware nén mọi response bằng zlib CỦA PYTHON,
# giữ nguyên thread ASGI suốt lúc nén. nginx nén cùng việc đó bằng C và không
# đụng tới thread nào của app.
#
# Cutover cố ý làm HAI BƯỚC vì nginx prod là config trên HOST, không nằm trong
# `scripts/deploy-prod.sh` — gộp một bước thì có cửa sổ API trả JSON không nén:
#   B1 (MR này): nginx thêm `gzip_proxied any`. Cờ dưới đây vẫn mặc định "1" nên
#      KHÔNG có gì đổi — nginx bỏ qua response đã có `Content-Encoding`, tức
#      `gzip_proxied` nằm im cho tới bước 2.
#   B2 (thao tác vận hành, không cần deploy code): đặt WEB_GZIP_IN_DJANGO=0 rồi
#      restart web, verify bằng runbook §8. Sai thì trả về "1" và restart lại.
if os.getenv("WEB_GZIP_IN_DJANGO", "1") == "1":
    # Đặt ngay sau CorsMiddleware, đúng vị trí cũ: nén phải bọc NGOÀI mọi
    # middleware sinh nội dung, nhưng nằm TRONG middleware đo thời gian.
    MIDDLEWARE.insert(
        MIDDLEWARE.index("corsheaders.middleware.CorsMiddleware") + 1,
        "django.middleware.gzip.GZipMiddleware",
    )

ROOT_URLCONF = "config.urls"
APPEND_SLASH = False

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Runaway-query ceiling toàn cục (audit 2026-07-06 N2). Report dài hạn tự nâng
# cục bộ qua apps.common.db_guards.statement_timeout. 0 = tắt. Các lệnh quản trị
# chạy migration cần build index dài → không áp.
_STMT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "30000"))
_MIGRATION_COMMANDS = {"migrate", "makemigrations", "squashmigrations", "sqlmigrate"}
_IS_MIGRATION_CMD = len(sys.argv) > 1 and sys.argv[1] in _MIGRATION_COMMANDS

# PgBouncer ở pool_mode=transaction (audit 2026-07-23 mục C2). MẶC ĐỊNH TẮT —
# bật bằng DB_PGBOUNCER=1 sau khi đã validate trên staging (xem runbook
# docs/runbooks/2026-07-24-pgbouncer-cutover.md). Ba điều BẮT BUỘC khi bật:
#
# 1. prepare_threshold=None — psycopg3 tự dùng server-side prepared statement sau
#    5 lần chạy cùng một câu. Với transaction pooling, câu PREPARE nằm ở connection
#    server A còn EXECUTE có thể rơi vào connection B → lỗi "prepared statement
#    ... does not exist" xuất hiện NGẪU NHIÊN dưới tải. Đây là bẫy phổ biến nhất
#    của Django+psycopg3+PgBouncer.
# 2. KHÔNG gửi statement_timeout qua startup `options` — đó là setting mức SESSION,
#    mà session ở transaction pooling bị dùng chung giữa nhiều client. Thay vào đó
#    đặt ở tầng PgBouncer/role. Migration phải nối THẲNG Postgres, không qua pooler.
# 3. DISABLE_SERVER_SIDE_CURSORS=True — QuerySet.iterator() mặc định DECLARE một
#    named cursor rồi FETCH nhiều lượt; ở autocommit mỗi lượt là một transaction
#    riêng nên PgBouncer đảo backend connection giữa chừng → "cursor ... does not
#    exist" (sự cố backfill_landing_confirmed_amount 2026-08-04; các call site
#    khác: autoclose_followups, auto_sync_invoice_full, export audit CSV,
#    backfill_order_phone_normalized). Tắt server-side cursor thì iterator() kéo
#    trọn kết quả về driver một lần — các call site hiện tại đều đã cap số dòng
#    (export 10k) hoặc tập nhỏ; job quét bảng lớn nên tự phân trang keyset theo id.
DB_PGBOUNCER = os.getenv("DB_PGBOUNCER", "0") == "1"

# lock_timeout cho tiến trình web/worker (audit 2026-09-23 T2/P2-11): một request
# chờ khoá quá 5s (thường vì DDL/ALTER đang chạy hoặc transaction treo) phải chết
# sớm thay vì xếp hàng đến statement_timeout 30s và kéo theo cả pool. 0 = tắt.
# KHÔNG áp cho lệnh migration (cùng bộ _MIGRATION_COMMANDS như statement_timeout):
# migrate cần đợi khoá dài — ALTER TABLE trên bảng nóng phải chờ được transaction
# đang chạy xong. Với PgBouncer cũng KHÔNG gửi qua startup options (bẫy #2 ở
# trên): đặt ở tầng role — `ALTER ROLE desi SET lock_timeout = '5s'` và role
# migrate riêng để 0 (docs/runbooks/2026-09-23-audit-ops.md mục Database).
_LOCK_TIMEOUT_MS = int(os.getenv("DB_LOCK_TIMEOUT_MS", "5000"))

_db_options = {"connect_timeout": 10}
if DB_PGBOUNCER:
    _db_options["prepare_threshold"] = None
if not _IS_MIGRATION_CMD and not DB_PGBOUNCER:
    _session_settings = []
    if _STMT_TIMEOUT_MS > 0:
        _session_settings.append(f"-c statement_timeout={_STMT_TIMEOUT_MS}")
    if _LOCK_TIMEOUT_MS > 0:
        _session_settings.append(f"-c lock_timeout={_LOCK_TIMEOUT_MS}")
    if _session_settings:
        _db_options["options"] = " ".join(_session_settings)

# Mỗi connection tự khai danh tính trong pg_stat_activity.application_name. Điều tra
# sự cố 2026-07-23 ("too many clients already") mất 4 vòng lấy bằng chứng chỉ vì cột
# này rỗng — không biết ~96 connection đến từ web, worker hay tiến trình nào khác.
# Compose đặt DB_APPLICATION_NAME riêng cho từng service; fallback là tên container.
_db_options["application_name"] = os.getenv("DB_APPLICATION_NAME") or socket.gethostname()

# Trần số truy vấn DB đồng thời cho bắt tay WebSocket — xem
# apps/channels_core/handshake.py. Trần thực tế = giá trị này × số gunicorn worker.
WS_HANDSHAKE_DB_CONCURRENCY = int(os.getenv("WS_HANDSHAKE_DB_CONCURRENCY", "8"))

# Trần cho truy vấn DB fallback lúc phân quyền event broadcast — xem
# apps/channels_core/consumers.py._is_authorized. Đường chính không chạm DB
# (payload tự chứa staff_id/channel_code), nên trần này chỉ để một call site còn
# sót field không kéo số connection tỉ lệ theo số client (sự cố 2026-07-29).
WS_BROADCAST_DB_CONCURRENCY = int(os.getenv("WS_BROADCAST_DB_CONCURRENCY", "4"))

# Trần request HTTP xử lý đồng thời, MỖI gunicorn worker — xem
# apps/common/asgi_concurrency.py (sự cố 2026-08-03: burst 75 POST /api/orders
# trong 3 phút → ~90 thread asgiref = ~90 connection → cạn max_connections).
# Budget: 100 conn − (celery ~10 + beat + exporter + WS 24+12) ≈ 40 cho HTTP
# → 12 × 3 worker = 36. Request thừa xếp hàng (await), quá hạn chờ mới 503.
# 12→8 sau bão 2026-08-04 09:13: connection đã có PgBouncer lo, nút cổ chai thật
# là 1.5 CPU của desi-web — 36 thread tranh 1.5 CPU làm request phình 2.3s→15-30s
# và slot không nhả. 8 × 3 worker = 24 thread: mỗi request xong nhanh hơn, tổng
# throughput cao hơn, hàng đợi (await, không tốn thread) hấp thụ phần dư.
WEB_HTTP_CONCURRENCY = int(os.getenv("WEB_HTTP_CONCURRENCY", "8"))
WEB_HTTP_QUEUE_TIMEOUT = float(os.getenv("WEB_HTTP_QUEUE_TIMEOUT", "15"))

# CONN_MAX_AGE mặc định SUY TỪ việc có pooler hay không, thay vì tin vào env bên
# ngoài. Trước đây mặc định là 600 và prod chỉ an toàn nhờ compose ép về 0: sót
# biến trong anchor whitelist, chạy container ngoài compose, hay copy nhầm .env
# đều cho ra 600 — với ASGI + Channels thì mỗi thread ôm một connection persistent
# và cạn max_connections. Giá trị đúng suy được từ chính hệ thống:
#   - Không pooler → 0: connection đóng sau mỗi request.
#   - Qua PgBouncer → 600: giữ connection phía Django là rẻ, pooler mới là chỗ
#     giữ connection thật tới Postgres.
# Env vẫn override được khi cần, nhưng mặc định không còn là cái bẫy.
_DEFAULT_CONN_MAX_AGE = "600" if DB_PGBOUNCER else "0"
# `or` thay vì default của getenv: compose passthrough `${DJANGO_CONN_MAX_AGE:-}`
# gửi CHUỖI RỖNG khi .env không đặt — int("") sẽ nổ lúc boot. Rỗng = chưa đặt.
CONN_MAX_AGE = int(os.getenv("DJANGO_CONN_MAX_AGE") or _DEFAULT_CONN_MAX_AGE)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "desi_order"),
        "USER": os.getenv("POSTGRES_USER", "desi_order"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "desi_order"),
        "HOST": os.getenv("POSTGRES_HOST", "postgres"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": CONN_MAX_AGE,
        "CONN_HEALTH_CHECKS": True,
        # Điều bắt buộc #3 của PgBouncer (xem khối comment DB_PGBOUNCER ở trên).
        "DISABLE_SERVER_SIDE_CURSORS": DB_PGBOUNCER,
        "OPTIONS": _db_options,
    }
}

# ── Prod secret guard: fail-fast lúc nạp settings nếu secret rỗng/default ──
# (audit 2026-07 mục 1.3). Deploy sai cấu hình phải chết sớm, không chạy âm thầm
# với credential mặc định.

PRODUCTION_FORBIDDEN_SECRETS = {
    "DJANGO_SECRET_KEY": {"", "dev-secret-key-change-me"},
    "POSTGRES_PASSWORD": {"", "desi_order"},
    "REDIS_PASSWORD": {""},
    "MINIO_ACCESS_KEY": {"", "minioadmin"},
    "MINIO_SECRET_KEY": {"", "minioadmin"},
    "INTERNAL_AUTH_TOKEN": {""},
    "ENCRYPTION_KEY": {"", "your-32-byte-encryption-key-here"},
}


def find_insecure_production_secrets(getenv=os.getenv):
    """Trả về danh sách env key đang mang giá trị rỗng/default (cấm ở production)."""
    return [
        key
        for key, bad_values in PRODUCTION_FORBIDDEN_SECRETS.items()
        if getenv(key, "").strip() in bad_values
    ]


def assert_production_secrets(getenv=os.getenv):
    insecure = find_insecure_production_secrets(getenv)
    if insecure:
        raise ImproperlyConfigured(
            "Insecure/default secrets with APP_ENV=production: "
            + ", ".join(insecure)
            + " — set non-default values via environment."
        )


def assert_production_debug_off(getenv=os.getenv):
    if getenv("DJANGO_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}:
        raise ImproperlyConfigured("DJANGO_DEBUG must be 0 when APP_ENV=production.")


if IS_PRODUCTION:
    assert_production_secrets()
    assert_production_debug_off()

# ── Staging outbound guard: fail-fast lúc nạp settings khi APP_ENV=staging ──
# Staging chạy snapshot dữ liệu prod (docs/staging-environment.md) — tuyệt đối
# không được cầm chìa khóa outbound thật: KiotViet live, Lark bot/Bitable,
# Best Express, Zenify. Cấu hình lọt vào phải chết sớm, không chạy âm thầm
# rồi ghi lên hệ thống thật.
STAGING_FORBIDDEN_OUTBOUND_ENVS = (
    # Heartbeat healthchecks: staging ping cùng URL prod sẽ che tín hiệu prod chết.
    "OPS_HEARTBEAT_URL",
    # Một số khóa outbound khác đã chuyển vào backend:
    # — Zenify, Gemini, mật khẩu đọc crawler: chuyển vào apps.sysconfig registry
    #   (staging_forbidden=True, guard trong assert_no_registry_env_leftovers).
    # — Best Express username/password, CNV SSO secret: chuyển vào DB tables
    #   (BestExpressAccount, CnvConnection); staging_neutralize xoá bỏ chúng.
)

# LARK_*: quét theo PREFIX thay vì liệt kê tay — danh sách tay đã từng sót
# LARK_INVENTORY_BOT_WEBHOOK_URL + LARK_VAT_BOT_WEBHOOK_URL (cả hai có consumer thật,
# bắn theo hành động user trên staging) và sẽ tiếp tục sót mỗi khi thêm bot mới
# (audit 2026-07-18). Mọi env LARK_ non-empty đều bị cấm ở staging.
_STAGING_FORBIDDEN_PREFIXES = ("LARK_",)


def find_staging_forbidden_env(getenv=os.getenv, environ=None):
    """Trả về danh sách env outbound đang mang giá trị (cấm ở staging)."""
    env_map = os.environ if environ is None else environ
    flagged = [key for key in STAGING_FORBIDDEN_OUTBOUND_ENVS if getenv(key, "").strip()]
    flagged += sorted(
        key
        for key in env_map
        if key.startswith(_STAGING_FORBIDDEN_PREFIXES) and str(env_map.get(key, "")).strip()
    )
    if getenv("KIOTVIET_API_MODE", "mock").strip().lower() == "live":
        flagged.insert(0, "KIOTVIET_API_MODE")
    return flagged


def assert_staging_outbound_disabled(getenv=os.getenv, environ=None):
    flagged = find_staging_forbidden_env(getenv, environ=environ)
    if flagged:
        raise ImproperlyConfigured(
            "Outbound integrations must be disabled with APP_ENV=staging: "
            + ", ".join(flagged)
            + " — unset these (KIOTVIET_API_MODE must not be 'live')."
        )


if IS_STAGING:
    assert_staging_outbound_disabled()

# ── Guard registry: khóa đã chuyển vào DB (apps.sysconfig) KHÔNG được còn trong env ──
# DB là nguồn duy nhất (spec 2026-09-19 D2). Env còn mang khóa = ai đó tưởng .env
# còn tác dụng → fail-fast mọi môi trường. Cửa thoát SYSCONFIG_ALLOW_ENV_KEYS=1 chỉ
# dành cho cửa sổ deploy chuyển đổi (docs/system-settings.md §deploy), phải gỡ sau.
def find_registry_env_leftovers(environ=None) -> list[str]:
    from apps.sysconfig.registry import SETTINGS  # module thuần, không import model

    env_map = os.environ if environ is None else environ
    return [s.key for s in SETTINGS if str(env_map.get(s.key, "") or "").strip()]


def assert_no_registry_env_leftovers(environ=None) -> None:
    env_map = os.environ if environ is None else environ
    leftovers = find_registry_env_leftovers(env_map)
    if not leftovers:
        return
    message = (
        "Các khóa sau đã chuyển vào DB (Cài đặt → Cấu hình hệ thống) và phải xoá khỏi .env: "
        + ", ".join(leftovers)
    )
    if str(env_map.get("SYSCONFIG_ALLOW_ENV_KEYS", "")).strip() == "1":
        import warnings

        warnings.warn(message + " (SYSCONFIG_ALLOW_ENV_KEYS=1 — chỉ cảnh báo)", stacklevel=2)
        return
    raise ImproperlyConfigured(message)


assert_no_registry_env_leftovers()

# Redis host/port/password + builder dùng chung với settings_parts/celery.py.
from ._redis import (  # noqa: E402
    REDIS_CACHE_HOST,  # noqa: F401 — re-export: health check/runbook cần biết cache nằm đâu
    REDIS_HOST,  # noqa: F401 — re-export: giữ settings.REDIS_HOST như cũ
    REDIS_PASSWORD,  # noqa: F401
    REDIS_PORT,  # noqa: F401
    build_redis_cache_url as _build_redis_cache_url,
    build_redis_url as _build_redis_url,
)

# Mặc định "1" là DB cache trên instance DÙNG CHUNG. Khi đã tách instance riêng
# (REDIS_CACHE_HOST=redis-cache) thì compose đặt lại "0": instance chỉ chứa cache
# nên không cần tách DB nữa. Redis chỉ có DB 0-15.
REDIS_CACHE_DB = os.getenv("REDIS_CACHE_DB", "1")
REDIS_CHANNEL_DB = os.getenv("REDIS_CHANNEL_DB", "2")

REDIS_CACHE_URL = os.getenv("REDIS_CACHE_URL", _build_redis_cache_url(REDIS_CACHE_DB))
REDIS_CHANNEL_URL = os.getenv("REDIS_CHANNEL_URL", _build_redis_url(REDIS_CHANNEL_DB))

# ── Celery config + beat schedule: tách sang settings_parts/celery.py (mục 4.7) ──
# Mọi tên CELERY_* / REDIS_CELERY_* vẫn truy cập được từ config.settings như cũ.
from .celery import *  # noqa: E402,F403

# Trần thời gian (giây) cho các query báo cáo khách hàng full-history (cohort/trends/
# channel-quality). Chặn query runaway chạy mồ côi ở tầng DB (statement_timeout) —
# xem apps.common.db_guards.statement_timeout. 0 = không giới hạn.
REPORT_QUERY_TIMEOUT_SECONDS = int(os.getenv("REPORT_QUERY_TIMEOUT_SECONDS", "120"))

# Trần thời gian (giây) cho mỗi câu REFRESH MATERIALIZED VIEW của schema `dwh`
# (apps.analytics.application.mart_refresh_service). Cùng cơ chế statement_timeout
# với REPORT_QUERY_TIMEOUT_SECONDS ở trên.
#
# 900 → 480: task refresh_dwh_marts (apps/analytics/infrastructure/tasks.py) refresh
# TUẦN TỰ 3 mart trong CÙNG MỘT task với time_limit=1800. Ở 900/mart, 3 × 900 = 2700 >
# 1800 — worker có thể bị hard-kill giữa chừng mart thứ ba (2 mart tươi, 1 mart cũ,
# không có lỗi matview nào được raise). 3 × 480 = 1440, còn dư khoảng cách an toàn dưới
# 1800. Đổi timeout này thì PHẢI đối chiếu lại với time_limit của task, không được lệch
# hai chỗ như trước.
DWH_REFRESH_TIMEOUT_SECONDS = int(os.getenv("DWH_REFRESH_TIMEOUT_SECONDS") or "480")

# Trần tuổi (giờ) của mốc refresh mà `dwh_reconcile` chấp nhận
# (apps.analytics.management.commands.dwh_reconcile). Job refresh_dwh_marts chạy
# MỖI ĐÊM một lần (04:25) — 36h vì bỏ lỡ MỘT đêm (tới ~24h) vẫn chấp nhận được,
# bỏ lỡ HAI đêm liên tiếp mới là vấn đề thật (kho dữ liệu chết) cần người can
# thiệp. Không đặt bằng 24h đúng khớp lịch chạy: một job chạy trễ vài giờ do tải
# hệ thống không nên tự động biến thành lỗi.
DWH_RECONCILE_MAX_BOUNDARY_AGE_HOURS = int(
    os.getenv("DWH_RECONCILE_MAX_BOUNDARY_AGE_HOURS") or "36"
)


# ── Heartbeat giám sát ngoài (docs/ops-hardening-2026-07.md) ──
# URL ping healthchecks.io; beat bắn GET mỗi 5' (apps.common.tasks.ops_heartbeat).
# Trống = tắt. CHỈ đặt ở prod — staging guard bên trên cấm biến này (staging
# ping cùng URL sẽ che mất tín hiệu prod chết).
OPS_HEARTBEAT_URL = os.getenv("OPS_HEARTBEAT_URL", "").strip()

# Zenify Contact Center (webphone SDK) — chưa điền credentials thì API trả 503,
# FE tự ẩn tính năng gọi điện. api-key/secret CHỈ dùng server-side.
ZENIFY_API_BASE_URL = os.getenv("ZENIFY_API_BASE_URL", "https://api.zenify.vn").strip()
# Host được phép tải file ghi âm (SSRF guard, audit 2026-09-23 L5). Rỗng = suy từ
# ZENIFY_API_BASE_URL (host + mọi subdomain của domain mẹ). Phân cách dấu phẩy.
ZENIFY_RECORDING_HOSTS = [
    h.strip() for h in os.getenv("ZENIFY_RECORDING_HOSTS", "").split(",") if h.strip()
]
# Email agent Zenify dùng chung — để trống thì mỗi user đăng nhập bằng email ERP của mình.
ZENIFY_AGENT_EMAIL = os.getenv("ZENIFY_AGENT_EMAIL", "").strip()
ZENIFY_TOKEN_TTL_SECONDS = int(os.getenv("ZENIFY_TOKEN_TTL_SECONDS", "259200"))

ZENIFY_CALL_SYNC_WINDOW_MINUTES = int(os.getenv("ZENIFY_CALL_SYNC_WINDOW_MINUTES", "120"))
ZENIFY_CALL_MATCH_WINDOW_DAYS = int(os.getenv("ZENIFY_CALL_MATCH_WINDOW_DAYS", "30"))
ZENIFY_CALL_MIN_ACD_SECONDS = int(os.getenv("ZENIFY_CALL_MIN_ACD_SECONDS", "5"))
ZENIFY_CALL_MATCH_MAX_ATTEMPTS = int(os.getenv("ZENIFY_CALL_MATCH_MAX_ATTEMPTS", "7"))
GEMINI_MAX_ATTEMPTS = int(os.getenv("GEMINI_MAX_ATTEMPTS", "3"))
GEMINI_TRANSCRIBE_BATCH = int(os.getenv("GEMINI_TRANSCRIBE_BATCH", "8"))
# Gemini trả 429 = quota thật cạn (trần local chỉ là phỏng đoán). Nghỉ nguyên chu
# kỳ này rồi tự probe lại — beat 5' mà cứ thử là đốt tải file vô ích cả ngày.
GEMINI_QUOTA_COOLDOWN_MINUTES = int(os.getenv("GEMINI_QUOTA_COOLDOWN_MINUTES", "60"))

GEMINI_EXTRACT_BATCH = int(os.getenv("GEMINI_EXTRACT_BATCH", "20"))
GEMINI_EXTRACT_MAX_ATTEMPTS = int(os.getenv("GEMINI_EXTRACT_MAX_ATTEMPTS", "3"))
# Ngắn hơn ngưỡng = cuộc gọi không có nội dung tư vấn → SKIPPED, không đốt request.
GEMINI_EXTRACT_MIN_TRANSCRIPT_CHARS = int(os.getenv("GEMINI_EXTRACT_MIN_TRANSCRIPT_CHARS", "200"))

# Cầu postgres_fdw sang DB của desi-crawl-conversations (spec 2026-09-04 C1).
# Host mặc định là tên container trong mạng deploy_default — chính tiến trình
# Postgres của desi-postgres mở kết nối, KHÔNG phải Django, nên phải là tên mà
# container postgres phân giải được.
CHAT_CRAWLER_DB_HOST = os.getenv("CHAT_CRAWLER_DB_HOST", "deploy-postgres-1").strip()
CHAT_CRAWLER_DB_PORT = os.getenv("CHAT_CRAWLER_DB_PORT", "5432").strip()
CHAT_CRAWLER_DB_NAME = os.getenv("CHAT_CRAWLER_DB_NAME", "crawler").strip()
# KHÔNG dùng superuser `crawler` cho user mapping — role này chỉ cần SELECT.
CHAT_CRAWLER_DB_USER = os.getenv("CHAT_CRAWLER_DB_USER", "chat_ro").strip()

AI_CHAT_EXTRACT_BATCH = int(os.getenv("AI_CHAT_EXTRACT_BATCH", "20"))
AI_CHAT_EXTRACT_MAX_ATTEMPTS = int(os.getenv("AI_CHAT_EXTRACT_MAX_ATTEMPTS", "3"))
# Dưới ngưỡng này thì hội thoại không đủ chữ để đọc ra nhu cầu — bỏ qua, đừng đốt token.
AI_CHAT_EXTRACT_MIN_CHARS = int(os.getenv("AI_CHAT_EXTRACT_MIN_CHARS", "80"))
# Trần độ dài hội thoại gửi cho Gemini. Dài hơn thì cắt giữ ĐUÔI (xem run_batch).
# Đo prod 07/09/2026: hội thoại dài nhất gán nhãn THÀNH CÔNG là 16.831 ký tự
# (trung vị 2.029); cái 82.891 ký tự làm Gemini trả 504 và chặn đầu hàng đợi vĩnh
# viễn, hai cái khác trả JSON bị cắt giữa chừng. 30.000 chỉ đụng 5/60.564 hội thoại.
AI_CHAT_EXTRACT_MAX_CHARS = int(os.getenv("AI_CHAT_EXTRACT_MAX_CHARS", "30000"))

# Số record mỗi LÔ. Task chạy cuốn chiếu nhiều lô liên tiếp cho tới khi hết hàng
# đợi, nên đây không phải trần mỗi ngày — chỉ là kích thước lô claim mỗi vòng.
QA_SCORING_BATCH = int(os.getenv("QA_SCORING_BATCH", "20"))
# Trần thời gian một lượt chấm cuốn chiếu (phút). 0 = không giới hạn (nguy hiểm
# trên beat: một lô hỏng kéo dài sẽ ngốn worker `slow` tới sáng). Chạm trần thì
# phần chưa chấm nằm nguyên PENDING và lượt sau chạy tiếp.
QA_SCORING_MAX_MINUTES = int(os.getenv("QA_SCORING_MAX_MINUTES", "60"))
QA_SCORING_MAX_ATTEMPTS = int(os.getenv("QA_SCORING_MAX_ATTEMPTS", "3"))

KIOTVIET_API_MODE = os.getenv("KIOTVIET_API_MODE", "mock").strip().lower()
KIOTVIET_API_BASE_URL = os.getenv("KIOTVIET_API_BASE_URL", "https://public.kiotapi.com")
KIOTVIET_KSHIP_SETTING_SERVICE_ENDPOINT = os.getenv(
    "KIOTVIET_KSHIP_SETTING_SERVICE_ENDPOINT",
    "https://shipping.kiotapi.com/api/v3/setting-service",
).strip()
KIOTVIET_ACCESS_TOKEN_TTL = int(os.getenv("KIOTVIET_ACCESS_TOKEN_TTL", "86400"))
KIOTVIET_MOCK_DELAY_SECONDS = float(os.getenv("KIOTVIET_MOCK_DELAY_SECONDS", "0.4"))
KIOTVIET_MOCK_CONFIRM_STATUS = os.getenv("KIOTVIET_MOCK_CONFIRM_STATUS", "confirmed").strip().lower()
KIOTVIET_MOCK_RANDOM_STATUS = os.getenv("KIOTVIET_MOCK_RANDOM_STATUS", "0") == "1"
KIOTVIET_MOCK_SUCCESS_RATE = float(os.getenv("KIOTVIET_MOCK_SUCCESS_RATE", "0.6"))
KIOTVIET_MOCK_WARNING_RATE = float(os.getenv("KIOTVIET_MOCK_WARNING_RATE", "0.25"))
KIOTVIET_MOCK_FAILED_RATE = float(os.getenv("KIOTVIET_MOCK_FAILED_RATE", "0.15"))
KIOTVIET_JOB_ACTIVE_TIMEOUT_SECONDS = int(os.getenv("KIOTVIET_JOB_ACTIVE_TIMEOUT_SECONDS", "300"))
KIOTVIET_JOB_STAGGER_SECONDS = int(os.getenv("KIOTVIET_JOB_STAGGER_SECONDS", "3"))
KIOTVIET_TASK_RATE_LIMIT = os.getenv("KIOTVIET_TASK_RATE_LIMIT", "12/m")

# ── Trần gọi RA KiotViet Public API (apps.common.rate_limiter, key theo retailer) ──
# Khác KIOTVIET_TASK_RATE_LIMIT (đó là nhịp *task Celery*, mỗi task lại bắn hàng
# trăm request): đây là trần tính trên TỪNG lệnh gọi HTTP, dùng chung giữa mọi
# worker/tiến trình qua Redis. Không có nó thì nhiều job đồng bộ chạy song song đủ
# sức đốt hạn mức KiotViet của cả công ty.
# Hạn mức theo gian hàng nên key theo retailer, không theo tiến trình.
# 10 r/s là điểm khởi đầu rộng — siết bằng env sau khi có số liệu từ Prometheus.
KIOTVIET_RATE_LIMIT_PER_SECOND = int(os.getenv("KIOTVIET_RATE_LIMIT_PER_SECOND", "10"))
# Chờ tối đa bao lâu trước khi bỏ qua trần và cứ gọi (fail-open): chặn hẳn một job
# đồng bộ để bảo vệ trần do CHÍNH TA đặt là đổi sự cố nhỏ lấy sự cố lớn hơn.
KIOTVIET_RATE_LIMIT_MAX_WAIT = float(os.getenv("KIOTVIET_RATE_LIMIT_MAX_WAIT", "10"))

# Quét xuất kho: xác minh realtime với KiotViet rằng đơn chưa bị hủy (DB sync trễ tới 30').
# Fail-closed: API lỗi → chặn quét. Tắt cờ này khi KiotViet sự cố kéo dài để kho không đứng hình.
SCAN_VERIFY_CANCEL_REALTIME = os.getenv("SCAN_VERIFY_CANCEL_REALTIME", "1") == "1"
SCAN_VERIFY_TIMEOUT_SECONDS = float(os.getenv("SCAN_VERIFY_TIMEOUT_SECONDS", "3"))
# 300s: phủ trọn cửa sổ "quét từng kiện → bấm Xác nhận xuất kho" của một phiên quét lớn.
# TTL 30s cũ khiến lô 50 kiện phải gọi lại KiotViet 50 lần TUẦN TỰ lúc bấm Xác nhận
# (KiotViet chậm → treo web worker). Đánh đổi: đơn hủy ngay sau lần verify OK gần nhất có
# thể lọt tối đa 5 phút — vẫn tốt hơn 30' của cron sync, và chốt cuối dispatch vẫn đọc DB tươi.
SCAN_VERIFY_CACHE_TTL_OK = int(os.getenv("SCAN_VERIFY_CACHE_TTL_OK", "300"))
SCAN_VERIFY_CACHE_TTL_CANCELLED = int(os.getenv("SCAN_VERIFY_CACHE_TTL_CANCELLED", "300"))

# ── Cron cập nhật trạng thái đơn KiotViet (update_invoice_status_targets, mỗi 30') ──
# Mỗi target = {"channel_id": <KiotVietChannel.id>, "branch_ids": [<branchId>, ...]}.
# Cấu hình qua env KIOTVIET_STATUS_SYNC_TARGETS (JSON) — BẮT BUỘC ở production
# (fail-fast, audit 2026-09-23 T3/P2-7; trước đó default hard-code id kênh/kho prod
# 4/5/6 trong code). Giá trị PHẢI phủ mọi (kênh, kho) mà
# KIOTVIET_IMPORT_CREATE_ONLY_TARGETS tạo đơn (test StatusTargetsCoverImportTargetsTests):
# sự cố 2026-09-16 default thiếu kênh 5 (30965) và kho 241190 của kênh 4 nên đơn kẹt
# "Chờ xử lý" tới khi chạy tay. Chuỗi rỗng = chưa set (compose passthrough `${VAR:-}`
# đưa "" vào container). Ngoài production: rỗng → cron không quét gì.
_require_production_env("KIOTVIET_STATUS_SYNC_TARGETS", why="cron cập nhật trạng thái đơn KiotViet 30'")
try:
    KIOTVIET_STATUS_SYNC_TARGETS = json.loads(os.getenv("KIOTVIET_STATUS_SYNC_TARGETS") or "[]")
except (ValueError, TypeError) as _exc:
    if IS_PRODUCTION:
        raise ImproperlyConfigured(f"KIOTVIET_STATUS_SYNC_TARGETS không phải JSON hợp lệ: {_exc}") from _exc
    _settings_log.warning("KIOTVIET_STATUS_SYNC_TARGETS không phải JSON hợp lệ — bỏ qua: %s", _exc)
    KIOTVIET_STATUS_SYNC_TARGETS = []

# ── Cron import create-only (iter_import_channels, mỗi 15') ──
# Format "channel_id:branch_id,channel_id:branch_id,...". Danh sách CỐ ĐỊNH theo
# yêu cầu vận hành thay vì quét mọi kênh old=False — gồm cả kênh old (id=5) nên
# KHÔNG suy ra được từ cờ old/branch_id trong DB. Override qua env
# KIOTVIET_IMPORT_CREATE_ONLY_TARGETS; chuỗi rỗng = không import kênh nào.
KIOTVIET_IMPORT_CREATE_ONLY_TARGETS = os.getenv(
    "KIOTVIET_IMPORT_CREATE_ONLY_TARGETS", "4:231174,6:89084,5:30965"
)
# Window cron 5' lùi thêm N ngày (0 = chỉ hôm nay). Filter API theo purchaseDate
# (ngày mua) chứ không phải ngày tạo trong KiotViet, nên hóa đơn backdate (đơn sàn
# sync trễ, quanh nửa đêm, hủy-xuất lại) cần window lùi mới bắt được — backdate sâu
# hơn nữa do beat entry kv-import-create-only-deep (lookback 31 ngày, 01:30) vớt.
KIOTVIET_IMPORT_CREATE_ONLY_LOOKBACK_DAYS = int(
    os.getenv("KIOTVIET_IMPORT_CREATE_ONLY_LOOKBACK_DAYS", "1")
)

# ── Async task backend selection (Celery by default; "go" kept for emergency rollback) ──
# NOTE (RB.6): xác nhận env prod không còn dùng "go" trước khi gỡ code path này.
TASK_BACKEND = os.getenv("TASK_BACKEND", "celery").strip().lower()
TASK_BACKEND_OVERRIDE = {
    k.strip(): v.strip().lower()
    for k, v in (
        pair.split("=", 1)
        for pair in os.getenv("TASK_BACKEND_OVERRIDE", "").split(",")
        if "=" in pair
    )
}
AUDIT_BACKEND = os.getenv("AUDIT_BACKEND", "celery").strip().lower()
# ── Batch audit task (sự cố 2026-08-03/04) ──
# write_audit gom entry per-process: đầy AUDIT_BATCH_SIZE thì enqueue 1 task
# audit.create_batch; batch lửng được timer flush sau AUDIT_BATCH_MAX_DELAY_SECONDS.
# SIZE=1 = hành vi cũ (mỗi entry một task, không buffer) — van thoát khi cần.
AUDIT_BATCH_SIZE = int(os.getenv("AUDIT_BATCH_SIZE", "20"))
AUDIT_BATCH_MAX_DELAY_SECONDS = float(os.getenv("AUDIT_BATCH_MAX_DELAY_SECONDS", "2"))
# ── Retention audit_logs (audit hardening 2026-07 mục 3.1) ──
# Số ngày giữ audit_logs; <=0 = giữ vĩnh viễn (tắt purge). Task purge chạy beat 03:30.
AUDIT_LOG_RETENTION_DAYS = int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "90"))
# Mặc định KHÔNG lưu tóm tắt response body cho GET 2xx (giảm dung lượng audit_logs).
AUDIT_LOG_GET_RESPONSE = os.getenv("AUDIT_LOG_GET_RESPONSE", "0") == "1"
# ── Bỏ ghi audit cho request ĐỌC thành công (audit 2026-09-23 T1 §2.10) ──
# Middleware ghi 1 dòng audit_logs cho MỌI request API kể cả list poll (FE refetch
# → INSERT); bảng 2,23M dòng/2,8GB, ~80% là GET/POST-list 2xx. Mặc định bỏ qua
# GET 2xx và POST tới các endpoint list (AUDIT_LOG_POST_LIST_PATHS), TRỪ đường đọc
# nhạy cảm (AUDIT_LOG_SENSITIVE_READ_PATHS — so khớp substring, không phân biệt
# hoa/thường) vẫn ghi đủ. Ghi/sửa/xoá và mọi response ngoài 2xx không đổi.
# AUDIT_LOG_SKIP_READS=0 = hành vi cũ (ghi tất cả).
AUDIT_LOG_SKIP_READS = os.getenv("AUDIT_LOG_SKIP_READS", "1") == "1"
AUDIT_LOG_SENSITIVE_READ_PATHS = [
    p.strip()
    for p in os.getenv(
        "AUDIT_LOG_SENSITIVE_READ_PATHS",
        # reveal SĐT khách; export/tải file; in hoá đơn; đọc audit log; quản trị
        "customer-phone,/export,/invoice,/print,/api/audit-logs,/admin,/api/users",
    ).split(",")
    if p.strip()
]
# Endpoint list dùng POST (body lọc) — về bản chất là đọc.
AUDIT_LOG_POST_LIST_PATHS = [
    p.strip()
    for p in os.getenv(
        "AUDIT_LOG_POST_LIST_PATHS",
        "/api/orders,/api/orders/landing,/api/orders/sheet3,/api/orders/shipments",
    ).split(",")
    if p.strip()
]
# ── Retention bảng log orders (mục 3.4) ──
# landing_ingest_traces: debug trace, default giữ 120 ngày; <=0 = giữ vĩnh viễn.
LANDING_INGEST_TRACE_RETENTION_DAYS = int(
    os.getenv("LANDING_INGEST_TRACE_RETENTION_DAYS", "120")
)
# order_journey_events: default 0 = KHÔNG xoá (an toàn); bật qua env khi cần.
JOURNEY_EVENT_RETENTION_DAYS = int(os.getenv("JOURNEY_EVENT_RETENTION_DAYS", "0"))
# ── Retention PendingShipmentScan đã khớp (audit followups 2026-07 mục 3.2) ──
# Số ngày giữ dòng status=matched; <=0 = giữ vĩnh viễn. pending/failed/invalid/
# cancelled KHÔNG BAO GIỜ bị task này xoá (còn cần người xử lý / đối chiếu).
PENDING_SHIPMENT_SCAN_RETENTION_DAYS = int(
    os.getenv("PENDING_SHIPMENT_SCAN_RETENTION_DAYS", "90")
)
# ── Sweep chia đơn tự động (lưới đỡ skip im lặng, điều tra 2026-07-18) ──
# Cửa sổ lookback (giờ) đơn còn trống staff được cron sweep thử chia lại;
# 48h phủ cả weekend quên cấu hình (đơn thứ 7 vẫn được vớt sáng thứ 2).
LEAD_ASSIGNMENT_SWEEP_LOOKBACK_HOURS = int(
    os.getenv("LEAD_ASSIGNMENT_SWEEP_LOOKBACK_HOURS", "48")
)
# ── Retention BestWebhookEvent đã xử lý (audit 2026-07-14) ──
# Mỗi vận đơn BEST sinh ~10 event kèm payload jsonb; <=0 = giữ vĩnh viễn.
# Event processed=False KHÔNG BAO GIỜ bị xoá (chưa được áp vào shipment).
BEST_WEBHOOK_EVENT_RETENTION_DAYS = int(os.getenv("BEST_WEBHOOK_EVENT_RETENTION_DAYS", "90"))
# Sau ngần này ngày mà event vẫn không khớp shipment nào thì thôi thử lại (đánh dấu
# processed + lý do để purge dọn) — nếu không, event chết chiếm hết slot retry và
# event mới không bao giờ tới lượt. <=0 = thử lại vô hạn.
BEST_WEBHOOK_EVENT_RETRY_MAX_AGE_DAYS = int(
    os.getenv("BEST_WEBHOOK_EVENT_RETRY_MAX_AGE_DAYS", "3")
)
INTERNAL_AUTH_TOKEN = os.getenv("INTERNAL_AUTH_TOKEN", "").strip()
AUDIT_WRITER_BASE_URL = os.getenv("AUDIT_WRITER_BASE_URL", "http://audit-writer:8080").rstrip("/")
KIOTVIET_DEBUG_CREATE_ORDER_PAYLOAD = os.getenv("KIOTVIET_DEBUG_CREATE_ORDER_PAYLOAD", "0") == "1"

# Best Express direct integration (apps.best_express). Credentials nằm trong DB
# (EncryptedCharField trên BestExpressAccount); đây chỉ là base URL default theo env.
# Vendor chỉ công bố endpoint http:// trong tài liệu V3; nếu Best cấp https, override qua env.
BEST_EXPRESS_BASE_URL_TEST = os.getenv(
    "BEST_EXPRESS_BASE_URL_TEST", "http://sgp-seaedi-test.800best.com"
).rstrip("/")
BEST_EXPRESS_BASE_URL_PROD = os.getenv(
    "BEST_EXPRESS_BASE_URL_PROD", "http://sgp-seaedi.800best.com"
).rstrip("/")

# Tài khoản Best Express (username/password/ems_key/environment/webhook_token) quản
# lý trực tiếp trong bảng BestExpressAccount qua admin/UI — không còn seed từ .env
# (lệnh `best_sync_account` đã xoá, spec 2026-09-19 D2).

# ── CNV Loyalty sync (docs/superpowers/specs/2026-08-05-cnv-loyalty-sync-design.md) ──
# Tắt mặc định: signal/cron/task đều no-op tới khi bật cài đặt hệ thống + kết nối SSO.
# CNV_SYNC_ENABLED/CNV_SSO_SERVER_URI/CNV_API_BASE_URL/CNV_SSO_REDIRECT_URI/
# CNV_ACCOUNT_NAME/CNV_ACCOUNT_ID đọc từ apps.sysconfig (registry nhóm "integrations").
# client_id/client_secret KHÔNG có ở đây — sống trong CnvConnection (DB, admin/UI),
# lệnh `cnv_sync_connection` seed từ env đã xoá cùng đợt.
CNV_SYNC_MAX_ATTEMPTS = int(os.getenv("CNV_SYNC_MAX_ATTEMPTS", "10"))

# ── CNV chiều NGƯỢC: kéo đơn từ CNV về (spec 2026-08-19) ──────────────────────
# Tách cờ khỏi CNV_SYNC_ENABLED để bật/tắt hai chiều độc lập: đẩy lên đang chạy
# ổn không có nghĩa là sẵn sàng nhận về, và ngược lại.
CNV_INBOUND_ENABLED = os.getenv("CNV_INBOUND_ENABLED", "0") == "1"
# Token mình tự sinh, khai với CNV lúc register webhook rồi CNV gửi lại ở header
# Authorization. Rỗng = từ chối MỌI webhook (không có token thì không xác thực
# được người gọi, mà endpoint này là AllowAny).
CNV_WEBHOOK_VERIFY_TOKEN = os.getenv("CNV_WEBHOOK_VERIFY_TOKEN", "").strip()
CNV_WEBHOOK_CALLBACK_URL = os.getenv("CNV_WEBHOOK_CALLBACK_URL", "").strip()
# Swagger CNV khai `topics` là array<string> KHÔNG kèm danh sách giá trị hợp lệ,
# tài liệu công khai cũng không có. Để ở env thay vì hằng số trong code: biết tên
# thật (qua `manage.py cnv_webhook list`) thì sửa env, không phải sửa code.
CNV_WEBHOOK_TOPICS = [
    topic.strip()
    for topic in os.getenv("CNV_WEBHOOK_TOPICS", "orders/create,orders/updated").split(",")
    if topic.strip()
]
# Poll lùi lại bấy nhiêu phút mỗi lần chạy. Quét chồng lặp vô hại (cnv_order_id
# unique), còn bỏ sót đơn thì không ai biết.
CNV_INBOUND_LOOKBACK_MINUTES = int(os.getenv("CNV_INBOUND_LOOKBACK_MINUTES", "120"))
# Payload webhook ĐÃ xử lý giữ bấy nhiêu ngày rồi xoá. Event CHƯA xử lý không bao
# giờ bị xoá theo tuổi — đó là bằng chứng để phá án.
CNV_WEBHOOK_EVENT_RETENTION_DAYS = int(os.getenv("CNV_WEBHOOK_EVENT_RETENTION_DAYS", "30"))

# ── Foxia (docs/foxia-integration-design.md) ──
# Tắt mặc định: cron no-op tới khi bật env + tạo FoxiaConnection ở admin. Lệnh
# chạy tay (foxia_probe) vẫn hoạt động khi tắt — dò kết nối phải làm được TRƯỚC.
# Endpoint/database/username/api_key KHÔNG để ở env: chúng là secret của đối tác,
# sống trong DB với api_key mã hoá (cùng cách BestExpressAccount/CnvConnection làm).
FOXIA_SYNC_ENABLED = os.getenv("FOXIA_SYNC_ENABLED", "0") == "1"

# Job tự đóng lead đã lên hóa đơn (close_converted_leads). TẮT mặc định từ
# 2026-09-08: luật khớp cũ (chỉ SĐT + thời gian) đã hủy oan 119/268 lead — khách
# mua hai món khác nhau trong cùng cửa sổ bị coi là ghi đôi. Luật mới đòi trùng
# SKU, nhưng job chỉ chạy lại khi bật cờ này một cách có chủ ý; chạy tay bằng
# `manage.py close_converted_leads --dry-run` không cần cờ.
LEAD_AUTO_CLOSE_ENABLED = os.getenv("LEAD_AUTO_CLOSE_ENABLED", "0") == "1"
# 30s: JSON-RPC search_read 500 bản ghi chậm hơn hẳn REST thường; 10s mặc định
# của HttpClient là quá ngắn cho trang đầy.
FOXIA_HTTP_TIMEOUT = float(os.getenv("FOXIA_HTTP_TIMEOUT", "30"))

# ── Foxia — chiều GHI: đẩy đơn landing sang Foxia (spec 2026-09-09) ──
# FOXIA_PUSH_ENABLED/FOXIA_PUSH_BASE_URL/FOXIA_PUSH_TIMEOUT đọc từ apps.sysconfig
# (registry nhóm "integrations").

# ── Pancake — kéo số điện thoại từ hội thoại → đẩy cơ hội Foxia (spec 2026-09-16) ──
# PANCAKE_POLL_ENABLED/PANCAKE_HTTP_TIMEOUT đọc từ apps.sysconfig. Lưu token /
# đồng bộ page / bật page / đẩy lại chạy tay KHÔNG bị gate bởi cờ này.

# Module Media (ads-crm proxy) — spec 2026-09-04 §7. Host/key nằm trong DB.
MEDIA_HTTP_TIMEOUT = float(os.getenv("MEDIA_HTTP_TIMEOUT", "30"))
MEDIA_DOWNLOAD_TIMEOUT = float(os.getenv("MEDIA_DOWNLOAD_TIMEOUT", "120"))
MEDIA_TAXONOMY_CACHE_TTL = int(os.getenv("MEDIA_TAXONOMY_CACHE_TTL", "300"))

# ── Mã bảo hành (docs/warranty-codes-design.md) ──
WARRANTY_CODE_SERIAL_PREFIX = os.getenv("WARRANTY_CODE_SERIAL_PREFIX", "DX-").strip()
WARRANTY_CODE_SERIAL_PAD = int(os.getenv("WARRANTY_CODE_SERIAL_PAD", "6"))
# URL in lên tem QR. Trỏ về trang sản phẩm của CHÍNH hệ thống này (/sp/<mã>) chứ
# không phải landing của iCheck như bản đầu — xem docs/product-scan-page-design.md §1.
# WARRANTY_QR_BASE_URL/WARRANTY_PORTAL_MONTHS/WARRANTY_PORTAL_SCAN_LOG_RETENTION_DAYS/
# WARRANTY_PORTAL_ZALO_TOKEN đọc từ apps.sysconfig (registry nhóm "orders_portal");
# use site (apps/warranty_codes/exports_definitions.py) tự đảm bảo dấu `/` cuối
# vì tem đã in sai thì không sửa lại được.
# Không còn trần số lượng/lô. Lô tới ngưỡng này sinh mã ngay trong request
# (~3.500 mã/s trên prod → 20k ≈ 6s, dưới timeout 30s của gunicorn); lớn hơn
# đẩy Celery queue `slow` sinh nền theo chunk (task generate_batch_codes).
WARRANTY_CODE_SYNC_GENERATE_MAX = int(os.getenv("WARRANTY_CODE_SYNC_GENERATE_MAX", "20000"))

# Thông tin doanh nghiệp hiển thị ở footer trang sản phẩm public /sp/<mã> —
# PORTAL_COMPANY_* đọc từ apps.sysconfig. Rỗng thì frontend ẩn dòng tương ứng.

# SALE_REMINDER_FOLLOWUP_HOUR (giờ nhắc follow-up D+2/D+4/D+7) và
# FB_TOKEN_EXPIRY_WARN_DAYS (ngưỡng cảnh báo sớm token Facebook sắp hết hạn) đọc
# từ apps.sysconfig.

# Mốc go-live doanh thu theo page (YYYY-MM-DD). Rỗng = giai đoạn A, báo cáo chỉ có
# chi phí và mọi trường doanh thu trả null.
FB_PAGE_REPORT_START_DATE = os.getenv("FB_PAGE_REPORT_START_DATE", "")

# Giờ làm chuẩn (export bảng chấm công): 2 buổi, ngày công 8h.
# Xem docs/erp-hr-attendance-export.md — rule chốt 2026-07-13.
HR_WORK_MORNING_START = os.getenv("HR_WORK_MORNING_START", "08:00").strip()
HR_WORK_MORNING_END = os.getenv("HR_WORK_MORNING_END", "12:00").strip()
HR_WORK_AFTERNOON_START = os.getenv("HR_WORK_AFTERNOON_START", "13:30").strip()
HR_WORK_AFTERNOON_END = os.getenv("HR_WORK_AFTERNOON_END", "17:30").strip()

# MinIO (S3-compatible) — lưu ảnh chấm công
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000").strip()
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin").strip()
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin").strip()
MINIO_BUCKET_ATTENDANCE = os.getenv("MINIO_BUCKET_ATTENDANCE", "attendance").strip()
MINIO_BUCKET_DOCUMENTS = os.getenv("MINIO_BUCKET_DOCUMENTS", "stock-documents").strip()
MINIO_BUCKET_EXPORTS = os.getenv("MINIO_BUCKET_EXPORTS", "desi-exports").strip()
MINIO_BUCKET_PRODUCTS = os.getenv("MINIO_BUCKET_PRODUCTS", "product-media").strip()
MINIO_USE_SSL = os.getenv("MINIO_USE_SSL", "0") == "1"
# ── Retention ảnh chấm công (audit followups 2026-07 mục 3.1) ──
# Số ngày giữ ảnh chấm công trên MinIO; <=0 = giữ vĩnh viễn (tắt purge). Chỉ object
# ảnh + `photo_key` bị xoá — AttendanceEvent (giờ vào/ra) LUÔN được giữ nguyên.
ATTENDANCE_PHOTO_RETENTION_DAYS = int(os.getenv("ATTENDANCE_PHOTO_RETENTION_DAYS", "180"))
# Cho phép payload ảnh base64 (~vài trăm KB)
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DATA_UPLOAD_MAX_MEMORY_SIZE", str(10 * 1024 * 1024)))

# BACKEND là lớp con của `RedisCache` (apps/common/cache_backend.py) làm hai việc
# mà backend gốc không làm: đếm hit/miss/error ra Prometheus, và fail-soft trên
# đường đọc/ghi tăng tốc để Redis cache chết chỉ làm hệ thống CHẬM chứ không NGÃ.
# Khoá phân tán + rate limit (`add`/`incr`) vẫn cố tình fail-loud — xem docstring
# của module để biết vì sao ranh giới nằm ở đó.
#
# Timeout socket: không đặt thì một Redis TREO (còn nghe cổng nhưng không trả lời)
# sẽ giữ thread ASGI vô hạn — fail-soft thành vô nghĩa vì chẳng có lỗi nào để bắt.
# 0.5s là rất rộng cho Redis cùng mạng docker (bình thường < 5ms).
# LƯU Ý viết thường: Django nhét thẳng OPTIONS thành **kwargs của
# `redis.ConnectionPool.from_url` (xem RedisCacheClient.__init__), nên tên tham số
# là của redis-py. Viết hoa kiểu django-redis sẽ nổ TypeError lúc mở connection.
CACHES = {
    "default": {
        "BACKEND": "apps.common.cache_backend.InstrumentedRedisCache",
        "LOCATION": REDIS_CACHE_URL,
        "KEY_PREFIX": "desi",
        "OPTIONS": {
            "socket_timeout": float(os.getenv("REDIS_CACHE_SOCKET_TIMEOUT", "0.5")),
            "socket_connect_timeout": float(os.getenv("REDIS_CACHE_CONNECT_TIMEOUT", "0.5")),
        },
    }
}

# Trang Đơn hàng chạy COUNT toàn tập khớp bộ lọc mỗi request; khi lật trang / re-fetch
# cùng một bộ lọc, COUNT bị lặp lại vô ích (~120ms+ mỗi lần, tăng theo khoảng ngày).
# Cache kết quả COUNT theo hash bộ lọc (đã gồm scope user) trong Redis với TTL ngắn.
# Mặc định 0 = TẮT (dev/test dùng chung Redis nên bật sẽ lẫn count giữa các test);
# prod bật qua env ORDERS_LIST_COUNT_CACHE_TTL (compose đặt 30).
ORDERS_LIST_COUNT_CACHE_TTL = int(os.getenv("ORDERS_LIST_COUNT_CACHE_TTL", "0"))

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_CHANNEL_URL]},
    }
}

# Khoá tạm tài khoản sau N lần đăng nhập sai liên tiếp (apps/users/login_lockout.py,
# audit 2026-09-23 M5). Lớp 3 sau throttle IP và throttle (username, IP): kẻ đổi IP
# liên tục vẫn bị chặn theo username. 0 = tắt (không khuyến nghị trên prod).
LOGIN_LOCKOUT_THRESHOLD = int(os.getenv("LOGIN_LOCKOUT_THRESHOLD", "10"))
LOGIN_LOCKOUT_SECONDS = int(os.getenv("LOGIN_LOCKOUT_SECONDS", "900"))

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    # Tối thiểu 12 ký tự (audit 2026-09-23 M5; mặc định Django 8). Chỉ áp cho mật
    # khẩu MỚI (đổi/đặt lại/tạo tài khoản) — mật khẩu đang dùng không bị ép đổi.
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": int(os.getenv("PASSWORD_MIN_LENGTH", "12"))},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "vi"
TIME_ZONE = "Asia/Bangkok"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# WhiteNoise đọc từ STATIC_ROOT lúc khởi động (compose chạy `collectstatic` trước
# gunicorn). Cố ý dùng storage KHÔNG manifest: static ở đây chỉ là admin/Swagger,
# không cần cache-bust vĩnh viễn, và manifest bắt buộc phải có collectstatic
# trước MỌI lần render template `{% static %}` — kể cả trong test.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "users.User"

# Số hop proxy tin cậy đứng trước app (nginx=1) khi đọc X-Forwarded-For cho
# throttling — xem apps.common.throttling.ClientIpMixin.
THROTTLE_TRUSTED_PROXIES = int(os.getenv("THROTTLE_TRUSTED_PROXIES", "1"))

# View (theo `name=` trong urls.py) được MIỄN trần mặc định — CỘNG THÊM vào danh
# sách máy-tới-máy đã cứng trong apps.common.throttling, không thay thế nó.
THROTTLE_EXEMPT_VIEWS = tuple(
    name.strip() for name in os.getenv("THROTTLE_EXEMPT_VIEWS", "").split(",") if name.strip()
)

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "apps.users.jwt.VersionedJWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    # Trần MẶC ĐỊNH cho mọi endpoint DRF không tự khai `@throttle_classes`.
    # Trước 2026-07-28 chỉ có DEFAULT_THROTTLE_RATES mà thiếu dòng này, nghĩa là
    # ~200 endpoint không có trần nào: một token hợp lệ đủ để spam vô hạn.
    # Endpoint tự khai throttle riêng (login/warranty/print) GHI ĐÈ danh sách này
    # — DRF không cộng dồn — nên không có chuyện bị đếm hai lần.
    "DEFAULT_THROTTLE_CLASSES": (
        "apps.common.throttling.DefaultUserRateThrottle",
        "apps.common.throttling.DefaultAnonRateThrottle",
    ),
    # Giới hạn tần suất theo scope. Mặc định throttle dùng IP client (anon).
    "DEFAULT_THROTTLE_RATES": {
        # ── Trần mặc định (van chống chạy loạn, KHÔNG phải hạn ngạch) ──
        # Theo USER: ~50 req/phút duy trì suốt một giờ. Mở một trang ERP bắn
        # 10-20 request, nhưng con người không giữ được nhịp đó cả giờ; một tab
        # kẹt vòng lặp thì chạm trần trong chưa đầy một phút. Đặt rỗng để tắt.
        "user": os.getenv("USER_THROTTLE_RATE", "3000/hour"),
        # Theo IP client thật. Nhớ: cả văn phòng chung MỘT IP NAT nên phải rộng.
        # Các endpoint ẩn danh có lưu lượng thật (refresh token, webhook, ingest)
        # đã nằm trong danh sách miễn trừ ở apps.common.throttling.
        "anon": os.getenv("ANON_THROTTLE_RATE", "600/hour"),
        # Chống brute-force đăng nhập: tối đa 10 lần/phút trên mỗi IP.
        # Đưa ra env để siết/nới không cần deploy — giá trị mặc định giữ nguyên.
        "login": os.getenv("LOGIN_THROTTLE_RATE", "10/min"),
        # Lớp 2 chống brute-force: theo cặp (username, IP) — audit 2026-09-23 M5.
        # Siết hơn trần IP vì một người thật không gõ sai 5 lần/phút cho MỘT tài
        # khoản; trần IP phải rộng vì văn phòng chung NAT.
        "login_user": os.getenv("LOGIN_USER_THROTTLE_RATE", "5/min"),
        # Form đăng ký bảo hành public (không auth): chống spam tạo bản ghi giả.
        "warranty_register": "20/hour",
        # Agent Hermes kéo transcript theo cụm. Rộng vì mỗi trang tối đa 200 dòng
        # nên kéo 30 ngày dữ liệu chỉ tốn ~13 request; trần này là van chống vòng
        # lặp hỏng bên agent, không phải hạn ngạch.
        "hermes": os.getenv("HERMES_THROTTLE_RATE", "600/hour"),
        # Trợ lý Daily Report: một phiên báo cáo ≤ 10 request. Van chống vòng lặp
        # hỏng bên agent, không phải hạn ngạch.
        "daily_report": os.getenv("DAILY_REPORT_THROTTLE_RATE", "300/hour"),
        "conversion_feed": os.getenv("CONVERSION_FEED_THROTTLE_RATE", "600/hour"),
        # Portal tra cứu bảo hành public (tra-cuu.delax.asia) — kích hoạt + xem lại.
        "warranty_portal_activate": os.getenv("WARRANTY_PORTAL_ACTIVATE_RATE", "10/hour"),
        "warranty_portal_read": os.getenv("WARRANTY_PORTAL_READ_RATE", "120/hour"),
        # Ảnh sản phẩm public — scope RIÊNG, tách khỏi bucket `anon` dùng chung
        # (xem docstring PortalMediaThrottle). Rộng vì 1 trang = tới 9 request.
        "warranty_portal_media": os.getenv("WARRANTY_PORTAL_MEDIA_RATE", "600/hour"),
        # 4 endpoint in hóa đơn / phiếu giao (apps.orders...OrderPrintRateThrottle).
        # Mỗi request in tối đa PRINT_MAX_IDS=200 round-trip HTTP TUẦN TỰ sang KiotViet,
        # nên đây là endpoint đắt nhất hệ thống tính theo request.
        #
        # Chọn 120/min/USER — cố ý RỘNG để không chặn oan nhân viên kho (họ in nhiều lô
        # liên tiếp, mỗi lô 1 request; đóng gói xong lô nào in lô đó). 120/phút = 2
        # request/giây liên tục trong cả phút: thao tác tay qua UI không thể chạm tới,
        # nhưng vẫn cắt được vòng lặp/script (không giới hạn thì 1 client đủ giữ hết
        # worker). Siết dần sau khi có số liệu thật từ Prometheus — đổi bằng env, không
        # cần deploy code.
        "order_print": os.getenv("ORDER_PRINT_THROTTLE_RATE", "120/min"),
        # Tạo job export (apps.exports). Mỗi job chiếm một slot worker hàng chục giây
        # nên đây là hành vi đắt; 30/giờ đủ rộng cho thao tác tay, đủ chặt để một
        # script không lấp kín queue.
        "export_create": os.getenv("EXPORT_CREATE_THROTTLE_RATE", "30/hour"),
    },
    # Sinh schema OpenAPI 3.1 từ chính DRF hiện có (D7) — không sửa endpoint.
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Bọc drf exception_handler: giữ nguyên body cũ, bổ sung key "error" chuẩn hoá
    # + log 500 kèm request_id (audit 2026-07 mục 4.8).
    "EXCEPTION_HANDLER": "apps.common.exceptions.api_exception_handler",
}

# OpenAPI / drf-spectacular (D7). Schema là hợp đồng FE↔BE; FE sinh type từ đây.
SPECTACULAR_SETTINGS = {
    "TITLE": "Desi-Order ERP API",
    "DESCRIPTION": "Hợp đồng API nội bộ — sinh tự động từ DRF. Xem docs/tech-stack-evolution-2026.md (D7).",
    "VERSION": "1.0.0",
    # UI/JSON schema chỉ phục vụ dev & FE codegen, không nhúng UI vào response.
    "SERVE_INCLUDE_SCHEMA": False,
    # JWT Bearer: phản ánh đúng cơ chế auth thực tế (apps.users.jwt).
    "SECURITY": [{"BearerAuth": []}],
    "APPEND_COMPONENTS": {
        "securitySchemes": {
            "BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
        }
    },
    # Function-based @api_view thiếu metadata → spectacular cảnh báo nhiều. Hạ ồn
    # ở log nhưng vẫn để CI bắt drift; nâng độ phủ dần bằng @extend_schema.
    "DISABLE_ERRORS_AND_WARNINGS": False,
    # API Daily Report có file OpenAPI tĩnh riêng (docs/openapi/daily-report.yaml);
    # loại khỏi schema chính để schema chính đúng là hợp đồng FE↔BE (D7).
    "PREPROCESSING_HOOKS": ["config.openapi_hooks.drop_daily_report_paths"],
}

LOG_LEVEL = os.getenv("DJANGO_LOG_LEVEL", "INFO")
ADDRESS_PARSE_LOG_LEVEL = os.getenv("DJANGO_ADDRESS_PARSE_LOG_LEVEL", LOG_LEVEL)
SQL_LOG_ENABLED = os.getenv("DJANGO_SQL_LOG_ENABLE", "0") == "1"
SQL_LOG_LEVEL = os.getenv("DJANGO_SQL_LOG_LEVEL", "DEBUG" if SQL_LOG_ENABLED else "CRITICAL")
# LOG_FORMAT=json bật JSON log có request_id (prod/aggregator); default giữ
# plain-text để dev đọc console dễ (audit 2026-07 mục 2.4).
LOG_FORMAT = os.getenv("LOG_FORMAT", "text").strip().lower()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        # Gắn request_id (từ RequestIdMiddleware) vào mọi record.
        "request_id": {"()": "apps.common.request_id.RequestIdFilter"},
        # Che `token=...` (JWT trong query string WS) trước khi ghi — audit 2026-09-23 H2.
        "scrub_token": {"()": "apps.common.log_scrub.TokenScrubFilter"},
    },
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        },
        # Formatter json chỉ khai báo khi bật (dictConfig khởi tạo MỌI formatter
        # được liệt kê — khai báo vô điều kiện sẽ bắt buộc python-json-logger
        # ngay cả ở môi trường plain-text).
        **(
            {
                "json": {
                    "()": "pythonjsonlogger.json.JsonFormatter",
                    "format": "%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s",
                }
            }
            if LOG_FORMAT == "json"
            else {}
        ),
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json" if LOG_FORMAT == "json" else "standard",
            "filters": ["request_id", "scrub_token"],
        },
    },
    # Root logger bắt MỌI logger (thay danh sách app liệt kê tay trước đây —
    # app mới không còn bị rơi log). Chỉ khai báo riêng logger cần level khác.
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "apps.orders.address_parser": {
            "handlers": ["console"],
            "level": ADDRESS_PARSE_LOG_LEVEL,
            "propagate": False,
        },
        "django.db.backends": {
            "handlers": ["console"],
            "level": SQL_LOG_LEVEL,
            "propagate": False,
        },
        "django.db.backends.schema": {
            "handlers": ["console"],
            "level": SQL_LOG_LEVEL,
            "propagate": False,
        },
    },
}



# Vòng đời token mặc định ở mức an toàn (access ngắn, refresh có rotation+blacklist).
# Trước đây mặc định là 1 năm / 365 ngày — token lộ sẽ dùng được rất lâu.
# Có thể override qua env; prod nên đặt access <= 60', refresh <= 30 ngày.
# Trần cứng trên production (audit 2026-09-23 N2/H1: prod từng đặt access 525600'
# = 1 năm, refresh 365d — token rò qua log dùng được 12 tháng). Vượt trần → KHÔNG
# crash (đang giữa deploy) mà kéo về mặc định an toàn + log ERROR để vận hành sửa
# .env. Ngoài production giữ nguyên giá trị env (dev/test cần token dài).
JWT_ACCESS_MAX_MINUTES = 1440
JWT_REFRESH_MAX_DAYS = 30
JWT_ACCESS_DEFAULT_MINUTES = 60
JWT_REFRESH_DEFAULT_DAYS = 14


def clamp_jwt_lifetimes(access_minutes: int, refresh_days: int, *, is_production: bool):
    """Trả (access_minutes, refresh_days) đã kẹp trần khi production; log ERROR khi kẹp."""
    if not is_production:
        return access_minutes, refresh_days
    log = _settings_log
    if access_minutes > JWT_ACCESS_MAX_MINUTES:
        log.error(
            "JWT_ACCESS_MINUTES=%s vượt trần %s trên production — kẹp về %s. Sửa .env.",
            access_minutes, JWT_ACCESS_MAX_MINUTES, JWT_ACCESS_DEFAULT_MINUTES,
        )
        access_minutes = JWT_ACCESS_DEFAULT_MINUTES
    if refresh_days > JWT_REFRESH_MAX_DAYS:
        log.error(
            "JWT_REFRESH_DAYS=%s vượt trần %s trên production — kẹp về %s. Sửa .env.",
            refresh_days, JWT_REFRESH_MAX_DAYS, JWT_REFRESH_DEFAULT_DAYS,
        )
        refresh_days = JWT_REFRESH_DEFAULT_DAYS
    return access_minutes, refresh_days


JWT_ACCESS_MINUTES, JWT_REFRESH_DAYS = clamp_jwt_lifetimes(
    int(os.getenv("JWT_ACCESS_MINUTES", str(JWT_ACCESS_DEFAULT_MINUTES))),
    int(os.getenv("JWT_REFRESH_DAYS", str(JWT_REFRESH_DEFAULT_DAYS))),
    is_production=IS_PRODUCTION,
)
JWT_ROTATE_REFRESH_TOKENS = os.getenv("JWT_ROTATE_REFRESH_TOKENS", "1") == "1"
JWT_BLACKLIST_AFTER_ROTATION = os.getenv("JWT_BLACKLIST_AFTER_ROTATION", "1") == "1"
JWT_UPDATE_LAST_LOGIN = os.getenv("JWT_UPDATE_LAST_LOGIN", "0" if IS_PRODUCTION else "1") == "1"
JWT_SIGNING_KEY = os.getenv("JWT_SIGNING_KEY", SECRET_KEY)

if len(JWT_SIGNING_KEY.encode("utf-8")) < 32:
    if IS_PRODUCTION:
        raise RuntimeError(
            "JWT_SIGNING_KEY must be at least 32 bytes for HS256 in production."
        )
    # Keep local/dev usable without weak-key warnings from PyJWT.
    JWT_SIGNING_KEY = hashlib.sha256(JWT_SIGNING_KEY.encode("utf-8")).hexdigest()

SIMPLE_JWT = {
    "ALGORITHM": "HS256",
    "SIGNING_KEY": JWT_SIGNING_KEY,
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=JWT_ACCESS_MINUTES),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=JWT_REFRESH_DAYS),
    "ROTATE_REFRESH_TOKENS": JWT_ROTATE_REFRESH_TOKENS,
    "BLACKLIST_AFTER_ROTATION": JWT_BLACKLIST_AFTER_ROTATION,
    "UPDATE_LAST_LOGIN": JWT_UPDATE_LAST_LOGIN,
}

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
CORS_ALLOW_CREDENTIALS = False
CORS_ALLOW_HEADERS = (*default_headers, "x-api-token")

# Kênh bán hàng "hàng mẫu koc" — dùng để khóa scope trang Đơn hàng KOC/KOL.
# BẮT BUỘC ở production (fail-fast, audit 2026-09-23; trước đó default 15 = id prod
# hard-code). Ngoài production: None → nhận diện KOC chỉ theo mã kênh `koc`
# (`Channel.is_koc`); test có autouse fixture đặt giá trị riêng (backend/conftest.py).
_require_production_env("KOC_CHANNEL_ID", why="khoá scope trang Đơn hàng KOC/KOL")
KOC_CHANNEL_ID = int(os.environ["KOC_CHANNEL_ID"]) if (os.environ.get("KOC_CHANNEL_ID") or "").strip() else None

# Giai đoạn fanout WS theo kênh (audit 2026-08-19, MR !1289): 1 = bản sao diện
# rộng vào group chung (an toàn khi còn consumer cũ); 2 = vào CRM_BROAD_GROUP
# (sale scope channels hết nhận traffic kênh ngoài phạm vi). CHỈ bật 2 sau khi
# mọi web prod đã chạy consumer join CRM_BROAD_GROUP (restart đủ web + tab reload).
WS_CHANNEL_FANOUT_PHASE = int(os.environ.get("WS_CHANNEL_FANOUT_PHASE", "1"))

# Kênh "Sale FB" — doanh thu sale chạy trên Facebook, KHÔNG phải chi phí ads.
# Trước 2026-08-19 hard-code (24,) ở 2 module rời (mìn đã ghi nhận: va chạm pk tự
# tăng giữa môi trường làm kênh thật trúng id này biến mất khỏi báo cáo ads).
# BẮT BUỘC ở production (fail-fast, audit 2026-09-23; trước đó default "24" trong
# code). LƯU Ý: docker-compose.prod.yml còn passthrough `${SALE_FB_CHANNEL_IDS:-24}`
# — guard chỉ có nghĩa khi ops bỏ default đó ở compose (xem runbook). Ngoài
# production: rỗng → không loại kênh nào khỏi báo cáo ads.
_require_production_env("SALE_FB_CHANNEL_IDS", why="loại kênh Sale FB khỏi báo cáo ads/resale")
SALE_FB_CHANNEL_IDS = tuple(
    int(x) for x in os.environ.get("SALE_FB_CHANNEL_IDS", "").split(",") if x.strip()
)

# ── Allowlist IP cho Django admin (apps/common/admin_ip_allowlist.py) ──
# "10.0.0.0/8,203.0.113.7"; rỗng = mở cho mọi IP (hành vi cũ). Audit 2026-09-23 L1.
DJANGO_ADMIN_ALLOWED_CIDRS = [
    c.strip() for c in os.getenv("DJANGO_ADMIN_ALLOWED_CIDRS", "").split(",") if c.strip()
]
if IS_PRODUCTION and not DJANGO_ADMIN_ALLOWED_CIDRS:
    _settings_log.warning(
        "DJANGO_ADMIN_ALLOWED_CIDRS is empty in production — /admin is reachable from "
        "any IP. Set the env var (or add allow/deny in the nginx vhost)."
    )

# ── Error tracking (Sentry) — env-gated, xem settings_parts/observability.py ──
from .observability import SENTRY_DSN  # noqa: E402,F401

if IS_PRODUCTION:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = os.getenv("DJANGO_SSL_REDIRECT", "0") == "1"

# ── Xuất file chạy nền (apps.exports) ──
# TTL 24h: job export chạy nền nên xuất lại rất rẻ, không có lý do giữ file chứa
# thông tin cá nhân khách hàng (họ tên/điện thoại/địa chỉ) trên đĩa lâu hơn một ngày.
EXPORT_FILE_TTL_HOURS = int(os.getenv("EXPORT_FILE_TTL_HOURS", "24"))
# Phải override CELERY_TASK_SOFT_TIME_LIMIT toàn cục (30s) — xem apps/exports/tasks.
EXPORT_SOFT_TIME_LIMIT = int(os.getenv("EXPORT_SOFT_TIME_LIMIT", "600"))
# Trần số job đang chạy của MỘT người: chặn kiểu lạm dụng mà dedupe không bắt được
# (đổi một tham số nhỏ mỗi lần bấm thì params_hash khác nhau nên không dedupe).
EXPORT_MAX_ACTIVE_JOBS_PER_USER = int(os.getenv("EXPORT_MAX_ACTIVE_JOBS_PER_USER", "5"))
