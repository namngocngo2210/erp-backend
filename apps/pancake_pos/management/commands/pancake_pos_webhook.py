"""Cấu hình webhook Pancake POS → CRM (two-way-sync §5.1, B1).

    manage.py pancake_pos_webhook show                  # cấu hình phía CRM + sự kiện 24h
    manage.py pancake_pos_webhook configure --dry-run   # in body PUT /shops/{id} (secret che)
    manage.py pancake_pos_webhook configure --yes       # ghi lên Pancake

Chuẩn bị: đặt `PANCAKE_POS_WEBHOOK_SECRET` (≥ 16 ký tự) và `PANCAKE_POS_WEBHOOK_URL`
(https://<domain prod>/api/pancake-pos/webhook) ở Cài đặt hệ thống. Ghi thật cần
`PANCAKE_POS_ENABLED`. Đây là lệnh vận hành gõ tay — KHÔNG có job nào tự chạy.
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.pancake_pos.application.services import webhook_config
from apps.pancake_pos.application.services.factory import PancakePosDisabled
from apps.pancake_pos.inbound_repositories import InboundOrderRepository, WebhookEventRepository
from apps.pancake_pos.infrastructure.pancake_pos_client import PancakePosError


class Command(BaseCommand):
    help = "Xem / cấu hình webhook Pancake POS gửi về CRM."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["show", "configure"])
        parser.add_argument("--dry-run", action="store_true", help="Chỉ in body, không gọi Pancake")
        parser.add_argument("--url", default="", help="Ghi đè PANCAKE_POS_WEBHOOK_URL")
        parser.add_argument(
            "--types", default="", help="Loại webhook, cách dấu phẩy (mặc định orders,products,variations_warehouses)"
        )
        parser.add_argument("--yes", action="store_true", help="Bỏ qua xác nhận khi ghi thật")

    def handle(self, *args, **options):
        if options["action"] == "show":
            self._show()
            return
        types = [t.strip() for t in options["types"].split(",") if t.strip()] or None
        dry_run = bool(options["dry_run"])
        try:
            preview = webhook_config.configure(dry_run=True, url=options["url"] or None, types=types)
            self._dump(preview["body"])
            if dry_run:
                self.stdout.write(self.style.WARNING("Dry-run: chưa gọi Pancake."))
                return
            self._confirm(options["yes"])
            result = webhook_config.configure(dry_run=False, url=options["url"] or None, types=types)
        except webhook_config.WebhookConfigError as exc:
            raise CommandError(str(exc)) from exc
        except (PancakePosDisabled, PancakePosError) as exc:
            raise CommandError(str(exc)) from exc
        self._dump(result["response"])
        self.stdout.write(self.style.SUCCESS(
            "Đã cấu hình. Kiểm tra `pancake_pos_webhook show` thấy sự kiện mới khi Pancake bắn về."
        ))

    def _show(self) -> None:
        self._dump({
            "config": webhook_config.local_state(),
            "counts_24h": WebhookEventRepository.stats_since(timezone.now() - timedelta(hours=24)),
            "summary": WebhookEventRepository.summary(),
            "inbound": InboundOrderRepository.counts(),
        })

    def _confirm(self, skip: bool) -> None:
        if skip:
            return
        answer = input("GHI cấu hình webhook lên shop Pancake. Gõ 'yes' để tiếp tục: ").strip().lower()
        if answer != "yes":
            raise CommandError("Đã hủy.")

    def _dump(self, data) -> None:
        self.stdout.write(json.dumps(data, ensure_ascii=False, indent=2, default=str))
