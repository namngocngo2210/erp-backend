import base64
import json

from django.core.management.base import BaseCommand, CommandError

from apps.kiotviet.models import KiotVietChannel
from apps.kiotviet.services import register_webhook
from apps.sysconfig import sysconfig


class Command(BaseCommand):
    help = "Đăng ký webhook KiotViet theo channel."

    def add_arguments(self, parser):
        parser.add_argument("--channel-id", type=int, required=True, help="ID của KiotVietChannel")
        parser.add_argument("--type", dest="webhook_type", required=True, help="Kiểu sự kiện webhook")
        parser.add_argument("--url", dest="webhook_url", required=True, help="Webhook endpoint URL")
        parser.add_argument("--description", default="", help="Mô tả webhook")
        parser.add_argument("--inactive", action="store_true", help="Tạo webhook ở trạng thái inactive")

    def handle(self, *args, **options):
        channel_id = int(options["channel_id"])
        try:
            channel = KiotVietChannel.objects.get(id=channel_id, deleted_at__isnull=True)
        except KiotVietChannel.DoesNotExist as exc:
            raise CommandError(f"Không tìm thấy KiotVietChannel id={channel_id}") from exc

        secret = self._resolve_secret()

        try:
            result = register_webhook(
                channel=channel,
                webhook_type=options["webhook_type"],
                webhook_url=options["webhook_url"],
                is_active=not bool(options.get("inactive")),
                description=options.get("description") or "",
                secret=secret,
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS("Webhook registered successfully."))
        self.stdout.write(json.dumps(result, ensure_ascii=False))

    @staticmethod
    def _resolve_secret() -> str:
        raw_secret = sysconfig.get("KIOTVIET_WEBHOOK_SECRET")
        if not raw_secret:
            raise CommandError(
                "Thiếu secret webhook. Cần cấu hình KIOTVIET_WEBHOOK_SECRET trong cài đặt hệ thống."
            )

        if len(raw_secret) < 8:
            raise CommandError("KIOTVIET_WEBHOOK_SECRET quá ngắn (yêu cầu tối thiểu 8 ký tự).")

        return base64.b64encode(raw_secret.encode("utf-8")).decode("ascii")
