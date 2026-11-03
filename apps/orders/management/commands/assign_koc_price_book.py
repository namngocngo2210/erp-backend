"""Gán kênh KOC sang bảng giá web (cutover pha 2).

Tách thành command thay vì nhét vào migration: đây là quyết định NGHIỆP VỤ, cần
chạy có kiểm soát và chạy lại được, không phải một phần lược đồ DB.
"""

from django.core.management.base import BaseCommand

from apps.channels_core.models import Channel
from apps.catalog.models import PriceBook


class Command(BaseCommand):
    help = "Gán mọi kênh KOC sang bảng giá web."

    def add_arguments(self, parser):
        parser.add_argument("--book", default="web", help="Code bảng giá đích (mặc định: web).")
        parser.add_argument("--dry-run", action="store_true", help="Chỉ in ra, không ghi DB.")

    def handle(self, *args, **options):
        code = str(options["book"]).strip().lower()
        book = PriceBook.objects.filter(code=code, deleted_at__isnull=True, is_active=True).first()
        if book is None:
            self.stderr.write(f"Không tìm thấy bảng giá active với code '{code}'.")
            return

        channels = [
            channel
            for channel in Channel.objects.filter(deleted_at__isnull=True)
            if channel.is_koc
        ]
        for channel in channels:
            self.stdout.write(f"{channel.code} ({channel.id}) -> {book.code}")
        if options["dry_run"]:
            self.stdout.write(f"[dry-run] {len(channels)} kênh sẽ được gán.")
            return

        updated = Channel.objects.filter(id__in=[c.id for c in channels]).update(price_book=book)
        self.stdout.write(f"Đã gán {updated} kênh KOC sang bảng giá '{book.code}'.")
