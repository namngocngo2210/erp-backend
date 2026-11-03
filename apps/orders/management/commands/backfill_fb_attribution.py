"""Backfill Order.fb_campaign_id / fb_ad_id cho đơn cũ từ url_page.

Đơn tạo trước migration 0143 có url_page mang `utm_id`/`utm_content` (Meta điền
campaign.id/ad.id) nhưng hai cột id rỗng. Quét mọi đơn còn sống có url_page chứa
'utm_' mà THIẾU ít nhất một trong hai id, bóc lại bằng đúng parser của luồng tạo
đơn (meta_attribution.parse_meta_attribution). Chỉ ghi cột đang rỗng — không đè
giá trị đã có.

Mặc định DRY-RUN, --apply để ghi. Update bằng queryset.update() — không bắn
signal/WS event, không đụng updated_at. Keyset theo id (không dùng iterator():
prod qua PgBouncer transaction pooling, server-side cursor chết giữa chừng).
"""

from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.orders.meta_attribution import parse_meta_attribution
from apps.orders.models import Order


class Command(BaseCommand):
    help = "Backfill fb_campaign_id/fb_ad_id từ url_page cho đơn cũ. Mặc định dry-run; --apply để ghi."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Ghi thật vào DB.")
        parser.add_argument("--batch-size", type=int, default=1000, help="Số đơn mỗi lượt.")

    def handle(self, *args, **options):
        apply = options["apply"]
        batch_size = options["batch_size"]
        qs = (
            Order.objects.filter(deleted_at__isnull=True, url_page__contains="utm_")
            .filter(Q(fb_campaign_id="") | Q(fb_ad_id=""))
            .only("id", "url_page", "fb_campaign_id", "fb_ad_id")
        )

        scanned = 0
        updates: list[tuple[int, dict[str, str]]] = []
        last_id = 0
        while True:
            batch = list(qs.filter(id__gt=last_id).order_by("id")[:batch_size])
            if not batch:
                break
            last_id = batch[-1].id
            for order in batch:
                scanned += 1
                campaign_id, ad_id = parse_meta_attribution(order.url_page)
                fields: dict[str, str] = {}
                if campaign_id and not order.fb_campaign_id:
                    fields["fb_campaign_id"] = campaign_id
                if ad_id and not order.fb_ad_id:
                    fields["fb_ad_id"] = ad_id
                if fields:
                    updates.append((order.id, fields))

        self.stdout.write(f"Đơn có utm_ mà thiếu id: {scanned}")
        self.stdout.write(f"  - bóc được id từ url_page: {len(updates)}")
        self.stdout.write(f"  - url không mang id Meta (giữ rỗng): {scanned - len(updates)}")

        if not apply:
            self.stdout.write("DRY-RUN: chưa ghi gì. Thêm --apply để cập nhật.")
            return

        updated = 0
        for order_id, fields in updates:
            updated += Order.objects.filter(id=order_id).update(**fields)
        self.stdout.write(self.style.SUCCESS(f"Đã backfill {updated} đơn."))
