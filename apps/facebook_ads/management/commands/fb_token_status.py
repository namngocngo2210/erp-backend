"""Xem / kiểm tra vòng đời token của các Facebook App.

    python manage.py fb_token_status                 # đọc trạng thái đã lưu
    python manage.py fb_token_status --check         # gọi debug_token, cập nhật lại
    python manage.py fb_token_status --check --extend  # kèm đổi sang long-lived nếu sắp hết hạn
    python manage.py fb_token_status --app 2         # chỉ một app

Dùng sau khi dán token mới để xác nhận `token_status = ok` và `token_expires_at`
rỗng (System User token — không hết hạn).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.facebook_ads.infrastructure.token_service import (
    refresh_token_metadata,
    try_extend_token,
)
from apps.facebook_ads.models import FacebookApp

_STATUS_LABEL = {
    FacebookApp.TOKEN_OK: "ok",
    FacebookApp.TOKEN_EXPIRING: "expiring",
    FacebookApp.TOKEN_INVALID: "invalid",
    FacebookApp.TOKEN_UNKNOWN: "unknown",
}


class Command(BaseCommand):
    help = "Liệt kê trạng thái token của các Facebook App (tuỳ chọn: kiểm tra lại với Graph API)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check", action="store_true",
            help="Gọi Graph API /debug_token và cập nhật lại trạng thái.",
        )
        parser.add_argument(
            "--extend", action="store_true",
            help="Thử đổi sang long-lived token cho app sắp hết hạn (cần --check).",
        )
        parser.add_argument("--app", type=int, default=None, help="Chỉ xử lý app id này.")

    def handle(self, *args, **options):
        queryset = FacebookApp.objects.filter(deleted_at__isnull=True).order_by("id")
        if options["app"] is not None:
            queryset = queryset.filter(pk=options["app"])

        apps = list(queryset)
        if not apps:
            self.stdout.write("Không có Facebook App nào.")
            return

        for app in apps:
            if options["check"]:
                refresh_token_metadata(app)
                if options["extend"] and app.token_status == FacebookApp.TOKEN_EXPIRING:
                    if try_extend_token(app):
                        self.stdout.write(self.style.SUCCESS(f"  → đã gia hạn token app {app.pk}"))

            self.stdout.write(self._describe(app))

    def _describe(self, app: FacebookApp) -> str:
        status = _STATUS_LABEL.get(app.token_status, app.token_status)
        if app.token_never_expires:
            expiry = "không hết hạn (System User token)"
        elif app.token_expires_at:
            remaining = app.token_expires_at - timezone.now()
            expiry = (
                f"hết hạn {timezone.localtime(app.token_expires_at):%d/%m/%Y %H:%M} "
                f"(còn {remaining.days} ngày)"
            )
        else:
            expiry = "chưa rõ hạn (chạy --check để kiểm tra)"

        line = f"[{app.pk}] {app.name} — token: {status} — {expiry}"
        if app.token_error:
            line += f"\n      lỗi: {app.token_error}"
        if app.token_invalid_since:
            line += f"\n      hỏng từ: {timezone.localtime(app.token_invalid_since):%d/%m/%Y %H:%M}"
        return line
