"""Cập nhật hàng loạt tên ngắn sản phẩm từ CSV (docs/product-short-name.md).

    manage.py set_product_short_names --file short_names.csv --dry-run   # xem trước
    manage.py set_product_short_names --file short_names.csv             # ghi thật

CSV UTF-8 (chấp nhận BOM của Excel), có dòng tiêu đề `sku,short_name`. SKU so khớp
không phân biệt hoa/thường, chỉ trên sản phẩm chưa xoá mềm. `short_name` rỗng = xoá
tên ngắn (Pancake quay về dùng tên CRM).

Ghi qua `Product.save(update_fields=[...])` từng dòng (không `bulk_update`) để phát
`ProductSaved` → đồng bộ Pancake tăng dần tự đẩy SKU đã đổi. Cả lô trong một
transaction: lỗi giữa chừng thì không ghi gì, event chỉ phát khi commit.
"""

from __future__ import annotations

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models.functions import Upper

from apps.catalog.models import Product

MAX_LEN = Product._meta.get_field("short_name").max_length
REQUIRED_COLUMNS = ("sku", "short_name")


def _read_rows(path: Path) -> dict[str, tuple[str, str]]:
    """Đọc CSV → {SKU viết hoa: (SKU gốc, short_name)}. Dòng sau ghi đè dòng trước."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            headers = [h.strip().lower() for h in (reader.fieldnames or [])]
            missing = [c for c in REQUIRED_COLUMNS if c not in headers]
            if missing:
                raise CommandError(f"CSV thiếu cột: {', '.join(missing)} (cần tiêu đề 'sku,short_name')")
            reader.fieldnames = headers
            rows: dict[str, tuple[str, str]] = {}
            for line_no, raw in enumerate(reader, start=2):
                sku = (raw.get("sku") or "").strip()
                if not sku:
                    continue
                short_name = " ".join((raw.get("short_name") or "").split())
                if len(short_name) > MAX_LEN:
                    raise CommandError(f"Dòng {line_no} ({sku}): tên ngắn dài {len(short_name)} > {MAX_LEN} ký tự")
                rows[sku.upper()] = (sku, short_name)
            return rows
    except OSError as exc:
        raise CommandError(f"Không đọc được file: {exc}") from exc


class Command(BaseCommand):
    help = "Cập nhật hàng loạt products.short_name từ CSV sku,short_name (--dry-run không ghi)"

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="Đường dẫn CSV (tiêu đề sku,short_name)")
        parser.add_argument("--dry-run", action="store_true", help="Chỉ báo cáo, không ghi DB")

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        rows = _read_rows(Path(options["file"]))
        products = list(
            Product.objects.filter(deleted_at__isnull=True)
            .annotate(sku_upper=Upper("sku"))
            .filter(sku_upper__in=list(rows))
            .order_by("id")
        )
        found = {p.sku_upper for p in products}
        not_found = sorted(rows[key][0] for key in rows.keys() - found)
        changed = [p for p in products if p.short_name != rows[p.sku_upper][1]]

        for product in changed[:50]:
            self.stdout.write(f"  {product.sku}: {product.short_name!r} → {rows[product.sku_upper][1]!r}")
        if not dry_run:
            with transaction.atomic():
                for product in changed:
                    product.short_name = rows[product.sku_upper][1]
                    product.save(update_fields=["short_name", "updated_at"])

        self.stdout.write(
            f"Dòng hợp lệ {len(rows)} · Khớp {len(products)} · Đổi {len(changed)} · "
            f"Không đổi {len(products) - len(changed)} · Không tìm thấy {len(not_found)}"
        )
        if not_found:
            self.stdout.write(self.style.WARNING(f"SKU không tìm thấy: {', '.join(not_found)}"))
        self.stdout.write(self.style.SUCCESS("Xong (dry-run, không ghi gì)." if dry_run else "Xong."))
