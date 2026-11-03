"""Quản lý đăng ký webhook với CNV (spec 2026-08-19).

    manage.py cnv_webhook list       # CNV đang gửi về đâu, topic nào
    manage.py cnv_webhook register   # đăng ký theo CNV_WEBHOOK_* trong env
    manage.py cnv_webhook delete     # gỡ đăng ký

**Chạy `list` TRƯỚC `register`.** Swagger CNV khai `topics` là `array<string>`
KHÔNG kèm danh sách giá trị hợp lệ, tài liệu công khai cũng không có — `list` là
cách duy nhất biết chắc tên topic CNV chấp nhận. Đăng ký bằng topic đoán sai thì
CNV nhận 200 nhưng không bao giờ gửi gì về, và không có tín hiệu nào báo sai.

Chạy được cả khi CNV_INBOUND_ENABLED=0: đây là lệnh vận hành cố ý gõ, không phải
luồng tự động — đăng ký xong rồi mới bật cờ mới đúng thứ tự.
"""

from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.cnv_loyalty.exceptions import CnvError
from apps.cnv_loyalty.infrastructure.cnv_client import CnvLoyaltyClient
from apps.cnv_loyalty.models import CnvConnection


class Command(BaseCommand):
    help = "Xem/đăng ký/gỡ webhook CNV Loyalty."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["list", "register", "delete"])
        parser.add_argument(
            "--callback-url",
            default="",
            help="Ghi đè CNV_WEBHOOK_CALLBACK_URL (phải là HTTPS public CNV gọi tới được)",
        )
        parser.add_argument(
            "--topics",
            default="",
            help="Ghi đè CNV_WEBHOOK_TOPICS, phân tách bằng dấu phẩy",
        )
        parser.add_argument(
            "--yes", action="store_true", help="Bỏ qua xác nhận cho register/delete"
        )

    def handle(self, *args, **options):
        connection = CnvConnection.get_active()
        if connection is None:
            raise CommandError("Chưa có CnvConnection active.")
        client = CnvLoyaltyClient(connection)
        action = options["action"]

        try:
            if action == "list":
                self._dump(client.list_webhooks())
                return
            if action == "delete":
                self._confirm("GỠ TOÀN BỘ đăng ký webhook CNV", options["yes"])
                self._dump(client.delete_webhook())
                return
            self._register(client, options)
        except CnvError as exc:
            raise CommandError(str(exc)) from exc

    def _register(self, client, options):
        callback_url = options["callback_url"] or getattr(
            settings, "CNV_WEBHOOK_CALLBACK_URL", ""
        )
        verify_token = getattr(settings, "CNV_WEBHOOK_VERIFY_TOKEN", "")
        topics = (
            [topic.strip() for topic in options["topics"].split(",") if topic.strip()]
            or list(getattr(settings, "CNV_WEBHOOK_TOPICS", []))
        )

        if not callback_url:
            raise CommandError("Thiếu CNV_WEBHOOK_CALLBACK_URL (hoặc --callback-url).")
        if not callback_url.startswith("https://"):
            # CNV gửi verify_token trong request — http là gửi token qua đường trần.
            raise CommandError("callback_url phải là HTTPS.")
        if not verify_token:
            raise CommandError(
                "Thiếu CNV_WEBHOOK_VERIFY_TOKEN — endpoint nhận webhook sẽ từ chối "
                "mọi request, đăng ký lúc này là đăng ký một đường chết."
            )
        if not topics:
            raise CommandError("Thiếu topics. Chạy `cnv_webhook list` để xem tên hợp lệ.")

        self.stdout.write(f"callback_url = {callback_url}")
        self.stdout.write(f"topics       = {', '.join(topics)}")
        self._confirm("ĐĂNG KÝ webhook với CNV bằng cấu hình trên", options["yes"])
        self._dump(client.register_webhook(callback_url, verify_token, topics))
        self.stdout.write(
            self.style.WARNING(
                "Đăng ký xong. Bật CNV_INBOUND_ENABLED=1 rồi kiểm tra bảng "
                "cnv_webhook_events có dòng mới khi CNV bắn về."
            )
        )

    def _confirm(self, what: str, skip: bool) -> None:
        if skip:
            return
        answer = input(f"{what}. Gõ 'yes' để tiếp tục: ").strip().lower()
        if answer != "yes":
            raise CommandError("Đã hủy.")

    def _dump(self, data) -> None:
        self.stdout.write(json.dumps(data, ensure_ascii=False, indent=2, default=str))
