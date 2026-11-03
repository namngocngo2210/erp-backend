"""Vô hiệu hóa secrets + cron sau khi restore dump prod lên staging.

Chạy tự động trong scripts/staging-refresh.sh (docs/staging-environment.md §3).
Dump prod mang theo credentials bên thứ ba và lịch cron đang bật — staging
tuyệt đối không được giữ chúng:

- KiotVietChannel: xóa client_secret + password (channel giữ nguyên để UI/test).
- KiotVietAccessToken: XÓA SẠCH — bảng này là TextField PLAINTEXT (không mã hóa),
  dump prod mang theo access/refresh token KiotViet CÒN HẠN (~24h). Không xóa thì
  (a) ai có quyền staging đọc được chìa khóa KV thật, (b) get_access_token trả
  token cache còn hạn → mọi đường sync bắn THẬT lên KV prod (audit 2026-07-18).
- BestExpressAccount: is_active=False + xóa password/token/ems_key/webhook_token.
- GoogleAdsConfig / FacebookApp: xóa token + secret.
- django_celery_beat.PeriodicTask: enabled=False toàn bộ (staging không chạy beat,
  nhưng tắt trong DB để restore không mang lịch prod theo nếu sau này có beat).
- SystemSetting (apps.sysconfig): xoá mọi dòng `staging_forbidden` + nhóm inbound_tokens
  (staging phải đặt token riêng); ghi SystemSettingChange changed_by=None.

Lưu ý: ENCRYPTION_KEY staging đã khác prod nên ciphertext restore vốn decrypt
fail, nhưng EncryptedCharField fallback trả raw khi decrypt lỗi → vẫn phải xóa
tường minh ở đây (không dựa vào mỗi việc đổi key).
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_celery_beat.models import PeriodicTask

from apps.best_express.models import BestExpressAccount
from apps.facebook_ads.models import FacebookApp
from apps.google_ads.models import GoogleAdsConfig
from apps.kiotviet.models import KiotVietAccessToken, KiotVietChannel
from apps.sysconfig.models import SystemSetting, SystemSettingChange
from apps.sysconfig.registry import SETTINGS


class Command(BaseCommand):
    help = "Vô hiệu hóa secrets bên thứ ba + tắt cron trong DB (chạy trên STAGING sau restore)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Xác nhận thực thi (command ghi đè dữ liệu, bắt buộc flag này)",
        )

    def handle(self, *args, **options):
        # Chặn cứng prod: command này xóa credentials thật + tắt toàn bộ cron.
        if settings.IS_PRODUCTION:
            raise CommandError(
                "staging_neutralize is staging-only; refusing to run with APP_ENV=production"
            )
        # Và chỉ chạy đúng nơi nó thuộc về: máy dev chạy nhầm sẽ wipe creds dev.
        if not settings.IS_STAGING:
            raise CommandError(
                "staging_neutralize yêu cầu APP_ENV=staging (đang: "
                f"{settings.APP_ENV!r})."
            )
        if not options["yes"]:
            raise CommandError("Thêm --yes để xác nhận (command ghi đè credentials trong DB).")

        with transaction.atomic():
            kv_count = KiotVietChannel.objects.update(client_secret=None, password=None)
            kv_token_count, _ = KiotVietAccessToken.objects.all().delete()
            best_count = BestExpressAccount.objects.update(
                is_active=False, password="", token="", ems_key="", webhook_token=""
            )
            gads_count = GoogleAdsConfig.objects.update(
                developer_token="", client_secret="", refresh_token=""
            )
            fb_count = FacebookApp.objects.update(app_secret="", access_token="")
            cron_count = PeriodicTask.objects.filter(enabled=True).update(enabled=False)

            wipe_keys = [
                s.key for s in SETTINGS if s.staging_forbidden or s.group == "inbound_tokens"
            ]
            wiped_rows = list(SystemSetting.objects.filter(key__in=wipe_keys))
            SystemSettingChange.objects.bulk_create(
                SystemSettingChange(key=r.key, old_value="***", new_value="", changed_by=None)
                for r in wiped_rows
            )
            sysconfig_count = len(wiped_rows)
            SystemSetting.objects.filter(key__in=wipe_keys).delete()

        from apps.sysconfig.service import bump_version

        bump_version()

        self.stdout.write(
            self.style.SUCCESS(
                "staging_neutralize OK: "
                f"kiotviet_channels={kv_count}, kiotviet_tokens_deleted={kv_token_count}, "
                f"best_express_accounts={best_count}, "
                f"google_ads_configs={gads_count}, facebook_apps={fb_count}, "
                f"periodic_tasks_disabled={cron_count}, "
                f"system_settings_deleted={sysconfig_count}"
            )
        )
