"""Gán sản phẩm cho chiến dịch quảng cáo NGAY, không phải chờ lịch beat.

Một lệnh cho mọi nguồn (trước là hai bản chép ``backfill_fb_campaign_products`` /
``backfill_google_campaign_products``, vẫn còn làm alias). Mặc định chỉ XEM THỬ
và in mẫu để soát trước khi ghi::

    python manage.py backfill_ads_campaign_products --provider facebook                  # xem thử, luật chặt
    python manage.py backfill_ads_campaign_products --provider facebook --loose          # xem thử, thêm luật đoán
    python manage.py backfill_ads_campaign_products --provider google --loose --apply    # ghi thật

Nguồn được resolve qua registry ``apps.orders.contracts.ads_campaign_backfill_port``
— orders không import facebook_ads/google_ads (hai app đó ở tầng TRÊN).

Phủ tự động ở Google thấp hơn hẳn Facebook (~41% chi phí) vì chiến dịch
Shopping/Pmax đặt tên theo nhóm hàng, chạy cả feed — chúng CỐ Ý nằm ở nhóm chưa
gán thay vì bị ép về một SKU.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.catalog.sku_text_match import assign_products_to_campaigns, format_backfill_report
from apps.orders.contracts.ads_campaign_backfill_port import (
    ads_campaign_backfill_provider,
    registered_sources,
)
from apps.orders.contracts.ads_spend_port import AdsSpendSource
from apps.orders.reporting.product_sales_rank import product_revenue_rank

PROVIDER_CHOICES = tuple(source.value for source in AdsSpendSource)


class Command(BaseCommand):
    help = "Đọc tên chiến dịch quảng cáo → gán sản phẩm cho các dòng chưa ai gán tay."

    # Alias theo nguồn (lệnh cũ) đặt sẵn giá trị này và bỏ tuỳ chọn --provider.
    fixed_provider: str | None = None

    def add_arguments(self, parser):
        if self.fixed_provider is None:
            parser.add_argument(
                "--provider",
                required=True,
                choices=PROVIDER_CHOICES,
                help="Nguồn quảng cáo cần gán.",
            )
        parser.add_argument(
            "--apply", action="store_true", help="Ghi thật. Không có cờ này thì chỉ đếm và in mẫu.",
        )
        parser.add_argument(
            "--loose",
            action="store_true",
            help=(
                "Bật thêm luật đoán: tên nhắc nhiều sản phẩm thì lấy mã đầu tiên, và học bí "
                "danh token từ các chiến dịch đã gán. Dòng đoán mang nguồn 'auto2' để lọc "
                "riêng ra soát."
            ),
        )

    def handle(self, *args, **options):
        source = self.fixed_provider or options["provider"]
        self.run_backfill(source, loose=options["loose"], apply=options["apply"])

    def run_backfill(self, source: str, *, loose: bool, apply: bool) -> dict:
        try:
            provider = ads_campaign_backfill_provider(source)
        except RuntimeError as exc:
            raise CommandError(f"{exc} Nguồn đã đăng ký: {registered_sources()}") from exc

        result = assign_products_to_campaigns(
            provider.campaigns(),
            # Bảng xếp hạng doanh thu là BẮT BUỘC: thiếu nó thì mã gốc `TMS01` ra SKU rác
            # cùng tên thay vì `TMS01-PROMAX` (657 chiến dịch Facebook trên prod).
            rank=product_revenue_rank(),
            loose=loose,
            dry_run=not apply,
        )
        self.stdout.write(
            format_backfill_report(result, label=provider.label, loose=loose, applied=apply)
        )
        if apply:
            self.stdout.write(
                self.style.SUCCESS(
                    "\nĐã ghi. Dòng người gán tay không bị đụng tới. Lọc nguồn "
                    "'Máy đoán' trên tab Gán sản phẩm để soát lại."
                )
            )
        else:
            self.stdout.write(self.style.WARNING("\nChưa ghi gì. Thêm --apply để gán thật."))
        return result
