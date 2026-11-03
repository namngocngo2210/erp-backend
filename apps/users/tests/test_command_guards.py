"""Guard cho management command seed_initial_data + staging_neutralize.

seed_initial_data — hardening sau audit 2026-07:
- Production (settings.IS_PRODUCTION) → CommandError, không seed gì.
- Password user tạo mới đọc từ env SEED_ADMIN_PASSWORD / SEED_STAFF_PASSWORD.
- Chạy lại lần 2 KHÔNG BAO GIỜ reset password user đã tồn tại.

staging_neutralize — docs/staging-environment.md §3:
- Production (settings.IS_PRODUCTION) → CommandError, không đụng dữ liệu.
- Thiếu --yes → CommandError.
- Chạy với --yes: xóa secrets KiotViet/Best/GoogleAds/Facebook + tắt PeriodicTask.
"""

import warnings

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from django_celery_beat.models import IntervalSchedule, PeriodicTask

from apps.audit.models import AuditLog
from apps.best_express.models import BestExpressAccount
from apps.facebook_ads.models import FacebookApp
from apps.google_ads.models import GoogleAdsConfig
from apps.kiotviet.models import KiotVietChannel

# `seed_initial_data` chạy thật ~6 s/test. CI chạy đủ; local bỏ qua bằng -m "not slow".
pytestmark = [pytest.mark.django_db, pytest.mark.slow]

User = get_user_model()


# ---------------------------------------------------------------------------
# seed_initial_data
# ---------------------------------------------------------------------------


def test_seed_initial_data_refuses_on_production(settings):
    settings.IS_PRODUCTION = True
    with pytest.raises(CommandError):
        call_command("seed_initial_data")
    assert not User.objects.filter(username="admin").exists()


def test_new_users_get_env_password(settings, monkeypatch):
    settings.IS_PRODUCTION = False
    monkeypatch.setenv("SEED_ADMIN_PASSWORD", "env-admin-secret-1")
    monkeypatch.setenv("SEED_STAFF_PASSWORD", "env-staff-secret-1")

    call_command("seed_initial_data")

    admin = User.objects.get(username="admin")
    staff1 = User.objects.get(username="staff1")
    assert admin.check_password("env-admin-secret-1")
    assert not admin.check_password("admin123")
    assert staff1.check_password("env-staff-secret-1")
    assert not staff1.check_password("staff123")


def test_seed_khong_ghi_datetime_naive(settings):
    """USE_TZ=True: seed từng tạo `base_time = datetime.now()` (naive) cho AuditLog
    → Django cảnh báo và hiểu là giờ VN, lệch 7 tiếng so với các mốc UTC khác."""
    settings.IS_PRODUCTION = False
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        call_command("seed_initial_data")

    assert AuditLog.objects.exists()
    assert all(timezone.is_aware(ts) for ts in AuditLog.objects.values_list("timestamp", flat=True))


def test_rerun_never_resets_existing_password(settings):
    settings.IS_PRODUCTION = False
    call_command("seed_initial_data")

    admin = User.objects.get(username="admin")
    admin.set_password("changed-by-operator")
    admin.save(update_fields=["password"])
    staff1 = User.objects.get(username="staff1")
    staff1.set_password("staff-changed")
    staff1.save(update_fields=["password"])

    call_command("seed_initial_data")

    admin.refresh_from_db()
    staff1.refresh_from_db()
    assert admin.check_password("changed-by-operator")
    assert staff1.check_password("staff-changed")


# ---------------------------------------------------------------------------
# staging_neutralize
# ---------------------------------------------------------------------------


def _seed_secrets():
    channel = KiotVietChannel.objects.create(
        retailer="shop-test", client_id="cid", client_secret="super-secret", password="kv-pass"
    )
    best = BestExpressAccount.objects.create(
        name="best", environment="production", username="best-user",
        password="best-pass", token="tok", ems_key="ems", webhook_token="hook",
    )
    gads = GoogleAdsConfig.objects.create(
        name="mcc", developer_token="dev-tok", client_id="gcid",
        client_secret="gsecret", refresh_token="refresh", login_customer_id="1234567890",
    )
    fb = FacebookApp.objects.create(
        name="fb", app_id="fb-app-1", app_secret="fb-secret", access_token="fb-token"
    )
    schedule = IntervalSchedule.objects.create(every=1, period=IntervalSchedule.HOURS)
    cron = PeriodicTask.objects.create(
        name="reconcile", task="apps.kiotviet.tasks.noop", interval=schedule, enabled=True
    )
    return channel, best, gads, fb, cron


def test_staging_neutralize_refuses_on_production(settings):
    settings.IS_PRODUCTION = True
    channel, *_ = _seed_secrets()
    with pytest.raises(CommandError, match="staging-only"):
        call_command("staging_neutralize", "--yes")
    channel.refresh_from_db()
    assert channel.client_secret == "super-secret"


def test_requires_yes_flag(settings):
    settings.IS_PRODUCTION = False
    settings.IS_STAGING = True
    with pytest.raises(CommandError, match="--yes"):
        call_command("staging_neutralize")


def test_non_staging_refuses_to_run(settings):
    """Máy dev chạy nhầm không được wipe creds dev (audit 2026-07-18)."""
    settings.IS_PRODUCTION = False
    settings.IS_STAGING = False
    with pytest.raises(CommandError, match="APP_ENV=staging"):
        call_command("staging_neutralize", "--yes")


def test_neutralize_clears_secrets_and_disables_cron(settings):
    settings.IS_PRODUCTION = False
    settings.IS_STAGING = True
    channel, best, gads, fb, cron = _seed_secrets()
    # Token KV plaintext trong dump prod — PHẢI bị xoá sạch (audit 2026-07-18).
    from apps.kiotviet.models import KiotVietAccessToken

    KiotVietAccessToken.objects.create(
        channel=channel, access_token="prod-live-token"
    )

    call_command("staging_neutralize", "--yes")

    assert KiotVietAccessToken.objects.count() == 0, "token KV prod phải bị xoá"

    channel.refresh_from_db()
    assert channel.client_secret is None
    assert channel.password is None
    assert channel.retailer == "shop-test"  # channel giữ nguyên để UI/test

    best.refresh_from_db()
    assert best.is_active is False
    assert best.password == ""
    assert best.token == ""
    assert best.ems_key == ""
    assert best.webhook_token == ""

    gads.refresh_from_db()
    assert gads.developer_token == ""
    assert gads.client_secret == ""
    assert gads.refresh_token == ""

    fb.refresh_from_db()
    assert fb.app_secret == ""
    assert fb.access_token == ""

    cron.refresh_from_db()
    assert cron.enabled is False
