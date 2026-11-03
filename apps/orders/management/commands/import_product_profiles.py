"""Import nội dung trang sản phẩm từ file phẳng (xem scripts/README-product-excel.md).

Chỉ đọc định dạng phẳng 8 sheet — KHÔNG parse file gốc của công ty. File gốc được
chuyển sang định dạng này một lần bằng scripts/convert_product_excel.py; tách như
vậy để command này đơn giản, chạy lại được, và test được bằng fixture nhỏ.
"""
from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalog.models import PROFILE_SECTIONS, Product, ProductProfile


def _rows(workbook, sheet_name: str) -> list[dict]:
    if sheet_name not in workbook.sheetnames:
        return []
    worksheet = workbook[sheet_name]
    values = list(worksheet.iter_rows(values_only=True))
    if not values:
        return []
    headers = [str(h).strip() if h is not None else "" for h in values[0]]
    out = []
    for row in values[1:]:
        record = {h: row[i] if i < len(row) else None for i, h in enumerate(headers) if h}
        if any(v is not None and str(v).strip() for v in record.values()):
            out.append(record)
    return out


def _text(value) -> str:
    return str(value).strip() if value is not None else ""


def _flag(value) -> bool:
    text = _text(value)
    if text in {"1", "true", "True", "x", "X"}:
        return True
    # openpyxl trả số float cho ô người dùng gõ số nguyên (vd 1 -> 1.0), so
    # bằng chuỗi sẽ bỏ sót nên fallback sang parse số.
    try:
        return float(text) == 1
    except (TypeError, ValueError):
        return False


def _group_by_sku(rows: list[dict], sheet_name: str) -> tuple[dict[str, list[dict]], list[str]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    warnings: list[str] = []
    for row in rows:
        sku = _text(row.get("sku")).upper()
        if sku:
            grouped[sku].append(row)

    def _sort_key(row: dict) -> int:
        raw = row.get("thu_tu")
        try:
            return int(raw or 0)
        except (TypeError, ValueError):
            # File này do nhân viên không kỹ thuật chỉnh tay — 1 ô thu_tu gõ
            # sai không được phép làm sập cả lần import 22 SKU. Coi như 0
            # (xếp đầu) và báo để người chỉnh sửa tìm ra đúng ô.
            warnings.append(f"{sheet_name}: thu_tu không hợp lệ ({raw!r}), coi như 0")
            return 0

    for items in grouped.values():
        items.sort(key=_sort_key)
    return grouped, warnings


class Command(BaseCommand):
    help = "Import nội dung trang sản phẩm (/sp/) từ file Excel phẳng."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="Đường dẫn file xlsx phẳng")
        parser.add_argument("--dry-run", action="store_true", dest="dry_run")
        parser.add_argument("--only-sku", action="append", dest="only_sku", default=None)

    def handle(self, *args, **options):
        try:
            import openpyxl
        except ImportError as exc:  # pragma: no cover
            raise CommandError("Thiếu openpyxl trong image.") from exc

        workbook = openpyxl.load_workbook(options["file"], data_only=True)
        products = _rows(workbook, "san_pham")
        if not products:
            raise CommandError("Sheet 'san_pham' rỗng — sai file?")

        only = {s.upper() for s in (options.get("only_sku") or [])} or None
        blocks: dict[str, dict[str, list[dict]]] = {}
        thu_tu_warnings: list[str] = []
        for name in ("diem_noi_bat", "thong_so", "doi_tuong", "huong_dan", "su_co", "faq", "video"):
            grouped, warnings = _group_by_sku(_rows(workbook, name), name)
            blocks[name] = grouped
            thu_tu_warnings.extend(warnings)
        for warning in thu_tu_warnings:
            self.stdout.write(self.style.WARNING(warning))

        matched, skipped = 0, []
        with transaction.atomic():
            for row in products:
                sku = _text(row.get("sku")).upper()
                if not sku or (only and sku not in only):
                    continue
                # sku__iexact: SKU trong CRM không ép buộc chữ hoa (đến từ
                # KiotViet sync lẫn tay người), khớp theo cùng pattern đã
                # dùng ở import_excel.py / order_service_helpers.py.
                product = Product.objects.filter(sku__iexact=sku, deleted_at__isnull=True).first()
                if product is None:
                    # Danh mục CRM là nguồn sự thật; file nội dung không được đẻ
                    # sản phẩm ma.
                    skipped.append(sku)
                    continue
                self._upsert(product, row, blocks, sku)
                matched += 1

            if options["dry_run"]:
                transaction.set_rollback(True)

        self.stdout.write(f"Khớp: {matched} SKU")
        if skipped:
            self.stdout.write(self.style.WARNING(f"Không có trong danh mục, bỏ qua: {', '.join(skipped)}"))
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("--dry-run: không ghi gì vào DB."))

    def _upsert(self, product, row, blocks, sku) -> None:
        sections = [s.strip() for s in _text(row.get("khoi_hien_thi")).split(",") if s.strip()]
        unknown = [s for s in sections if s not in PROFILE_SECTIONS]
        if unknown:
            self.stdout.write(self.style.WARNING(f"{sku}: khối lạ bị bỏ qua: {unknown}"))
        audience_rows = blocks["doi_tuong"].get(sku, [])

        ProductProfile.objects.update_or_create(
            product=product,
            defaults={
                "short_description": _text(row.get("mo_ta_ngan"))[:500],
                "is_published": _flag(row.get("cong_bo")),
                "visible_sections": [s for s in sections if s in PROFILE_SECTIONS],
                "highlights": [
                    {"title": _text(r.get("tieu_de")), "detail": _text(r.get("chi_tiet"))}
                    for r in blocks["diem_noi_bat"].get(sku, [])
                ],
                "specs": [
                    {"label": _text(r.get("nhan")), "value": _text(r.get("gia_tri"))}
                    for r in blocks["thong_so"].get(sku, [])
                ],
                "audience": {
                    "suitable": [_text(r.get("noi_dung")) for r in audience_rows
                                 if _text(r.get("loai")) == "nen_dung"],
                    "caution": [_text(r.get("noi_dung")) for r in audience_rows
                                if _text(r.get("loai")) == "can_hoi_bac_si"],
                },
                "usage_steps": [
                    {"title": _text(r.get("tieu_de")), "detail": _text(r.get("chi_tiet"))}
                    for r in blocks["huong_dan"].get(sku, [])
                ],
                "troubleshooting": [
                    {"symptom": _text(r.get("hien_tuong")), "cause": _text(r.get("nguyen_nhan")),
                     "fix": _text(r.get("cach_xu_ly"))}
                    for r in blocks["su_co"].get(sku, [])
                ],
                "faqs": [
                    {"question": _text(r.get("cau_hoi")), "answer": _text(r.get("tra_loi")),
                     "visible": _flag(r.get("hien_thi"))}
                    for r in blocks["faq"].get(sku, [])
                ],
                "videos": [
                    {"label": _text(r.get("nhan")), "url": _text(r.get("url"))}
                    for r in blocks["video"].get(sku, [])
                    if _text(r.get("url")).startswith(("http://", "https://"))
                ],
            },
        )
