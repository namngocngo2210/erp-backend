"""Thử kết nối Foxia và in số bản ghi từng tài nguyên.

Đây là công cụ nghiệm thu Sprint 0/1: chạy được lệnh này nghĩa là bộ 4 mảnh
(endpoint, database, username, api_key) đúng và tài khoản đọc được dữ liệu.

    manage.py foxia_probe
    manage.py foxia_probe --fields sale.order   # in metadata field của một model
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.foxia.exceptions import FoxiaError

# Các tài nguyên đáng đếm khi dò kết nối: danh mục + giao dịch.
_PROBE_RESOURCES = (
    "congtycon",
    "doinhommarketing",
    "kenhmarketing",
    "thuonghieu",
    "giaidoandonhang",
    "crm.team",
    "res.users",
    "utm.source",
    "cohoi",
    "sale.order",
    "sale.order.line",
    "donvanchuyen",
)


class Command(BaseCommand):
    help = "Kiểm tra kết nối Foxia: authenticate + search_count từng tài nguyên"

    def add_arguments(self, parser):
        parser.add_argument(
            "--fields",
            metavar="RESOURCE",
            help="In metadata field (fields_get) của một tài nguyên thay vì đếm bản ghi",
        )

    def handle(self, *args, **options):
        from apps.foxia.tasks import build_client

        # require_enabled=False: probe là lệnh người vận hành cố ý gõ, phải chạy
        # được TRƯỚC khi bật FOXIA_SYNC_ENABLED.
        client = build_client(require_enabled=False)
        if client is None:
            raise CommandError("Chưa có FoxiaConnection active — tạo ở Django admin trước.")

        try:
            uid = client.authenticate()
        except FoxiaError as exc:
            raise CommandError(f"Xác thực thất bại: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"Xác thực OK — uid={uid}"))

        resource = options.get("fields")
        if resource:
            self._print_fields(client, resource)
            return

        for name in _PROBE_RESOURCES:
            try:
                count = client.search_count(name)
            except FoxiaError as exc:
                self.stdout.write(self.style.WARNING(f"{name:<20} lỗi: {exc}"))
                continue
            self.stdout.write(f"{name:<20} {count:>10,} bản ghi")

    def _print_fields(self, client, resource: str) -> None:
        try:
            meta = client.fields_get(resource)
        except FoxiaError as exc:
            raise CommandError(f"fields_get({resource}) thất bại: {exc}") from exc

        self.stdout.write(self.style.SUCCESS(f"{resource}: {len(meta)} field"))
        for name in sorted(meta):
            info = meta[name] or {}
            line = f"  {name:<32} {info.get('type', '?'):<12} {info.get('string', '')}"
            selection = info.get("selection")
            if selection:
                # Đây chính là thứ cần để dựng bảng ánh xạ trạng thái sau này.
                values = ", ".join(str(item[0]) for item in selection if item)
                line += f"  [{values}]"
            self.stdout.write(line)
