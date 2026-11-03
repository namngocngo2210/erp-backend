"""Import t4_assessment_mapping.csv vào OrderAssessment.

Usage:
    python manage.py import_assessment_csv path/to/t4_assessment_mapping.csv
    python manage.py import_assessment_csv path/to/t4_assessment_mapping.csv --dry-run
    python manage.py import_assessment_csv path/to/t4_assessment_mapping.csv --overwrite

Match Order theo:
    1. shipping_code (ưu tiên) - khớp Order.shipping_code (apply cho TẤT CẢ order khớp)
    2. phone (fallback) - khớp Order.phone (so sánh sau khi normalize chỉ chữ số)
        Nếu nhiều order cùng phone -> chọn order mới nhất theo created_at.

Mặc định KHÔNG ghi đè field đã có dữ liệu (chỉ điền vào field rỗng).
Dùng --overwrite để ghi đè toàn bộ.
"""
import csv
import re
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.customers.services import CustomerService, normalize_phone
from apps.orders.age_group_backfill import parse_age_group
from apps.orders.models import NeedType, Order, OrderAssessment, OrderNeed
from apps.users.models import User


SCALAR_FIELDS = (
    "sales_status_code",
    "age_text",
    "occupation_code",
    "pain_level_code",
    "pain_duration_code",
    "used_massage_before",
    "comparing_brand_text",
    "customer_classification_code",
    "internal_note",
)

ARRAY_FIELDS = (
    "delax_source_codes",
    "lifestyle_habit_codes",
    "relax_time_codes",
    "purchase_barrier_codes",
)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _split_codes(value: str) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _coerce_used_massage(value: str):
    v = (value or "").strip().lower()
    if v == "yes":
        return True
    if v == "no":
        return False
    return None


class Command(BaseCommand):
    help = "Import CSV mapping (shipping_code/phone -> CRM fields) vào OrderAssessment."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", help="Đường dẫn tới t4_assessment_mapping.csv")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Không ghi DB, chỉ in báo cáo.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Ghi đè cả field đã có dữ liệu (mặc định chỉ điền vào field rỗng).",
        )
        parser.add_argument(
            "--delimiter",
            default="|",
            help="Ký tự phân tách CSV (mặc định '|').",
        )
        parser.add_argument(
            "--unmatched-out",
            default="",
            help="Đường dẫn file output (CSV) ghi danh sách dòng unmatched. Mặc định không ghi.",
        )
        parser.add_argument(
            "--total-csv",
            default="",
            help=(
                "Đường dẫn t4_total.csv chứa full order data (channel_id, name, phone, "
                "raw_address, product_raw, staff username, sale_status, shipping_code, "
                "internal_note). Khi unmatched, sẽ tạo Order + Customer từ file này."
            ),
        )

    def handle(self, *args, **opts):
        csv_path = opts["csv_path"]
        dry_run = opts["dry_run"]
        overwrite = opts["overwrite"]
        delimiter = opts["delimiter"]
        unmatched_out = opts["unmatched_out"]
        total_csv = opts["total_csv"]

        total_by_shipping: dict[str, dict] = {}
        total_by_phone: dict[str, dict] = {}
        if total_csv:
            total_by_shipping, total_by_phone = self._load_total_csv(total_csv, delimiter)
            self.stdout.write(
                f"Loaded total CSV: {len(total_by_shipping)} by shipping_code, "
                f"{len(total_by_phone)} by phone (last9)"
            )

        try:
            f = open(csv_path, newline="", encoding="utf-8-sig")
        except OSError as exc:
            raise CommandError(f"Không mở được file: {exc}")

        need_type_by_code = {nt.code: nt for nt in NeedType.objects.all()}

        stats = Counter()
        unmatched_examples = []
        unmatched_rows: list[dict] = []
        create_errors: list[dict] = []
        unknown_need_types = Counter()

        with f:
            reader = csv.DictReader(f, delimiter=delimiter)
            with transaction.atomic():
                sid = transaction.savepoint()
                for row in reader:
                    stats["total"] += 1
                    orders = self._find_orders(row)
                    if not orders:
                        # Try create from t4_total.csv
                        if total_by_shipping or total_by_phone:
                            create_sid = transaction.savepoint()
                            try:
                                new_order = self._create_order_from_total(
                                    row, total_by_shipping, total_by_phone, stats
                                )
                                if new_order is not None:
                                    transaction.savepoint_commit(create_sid)
                                    orders = [new_order]
                                else:
                                    transaction.savepoint_commit(create_sid)
                            except Exception as exc:
                                transaction.savepoint_rollback(create_sid)
                                stats["create_errors"] += 1
                                create_errors.append({
                                    "shipping_code": row.get("shipping_code", ""),
                                    "phone": row.get("phone", ""),
                                    "error": str(exc),
                                })
                                self.stderr.write(
                                    self.style.WARNING(
                                        f"Create failed shipping={row.get('shipping_code','')!r} "
                                        f"phone={row.get('phone','')!r}: {exc}"
                                    )
                                )
                        if not orders:
                            stats["unmatched"] += 1
                            unmatched_rows.append(row)
                            if len(unmatched_examples) < 10:
                                unmatched_examples.append(
                                    f"shipping={row.get('shipping_code','')!r} "
                                    f"phone={row.get('phone','')!r}"
                                )
                            continue

                    matched_by = "shipping_code" if (row.get("shipping_code") or "").strip() else "phone"
                    stats[f"matched_{matched_by}"] += 1
                    if len(orders) > 1:
                        stats["matched_multiple_siblings"] += 1

                    need_codes = _split_codes(row.get("need_type_codes", ""))

                    for order in orders:
                        if order.order_source != "landing":
                            order.order_source = "landing"
                            order.save(update_fields=["order_source", "updated_at"])
                            stats["order_source_updated"] += 1

                        assessment, created = OrderAssessment.objects.get_or_create(order=order)
                        if created:
                            stats["assessment_created"] += 1
                        else:
                            stats["assessment_existing"] += 1

                        changed_fields = self._apply_scalar(assessment, row, overwrite)
                        changed_fields += self._apply_arrays(assessment, row, overwrite)

                        if changed_fields:
                            assessment.save(update_fields=changed_fields + ["updated_at"])
                            stats["assessment_updated"] += 1

                        if need_codes:
                            added = self._sync_needs(
                                assessment, need_codes, need_type_by_code, unknown_need_types
                            )
                            stats["need_links_added"] += added

                if dry_run:
                    transaction.savepoint_rollback(sid)
                    self.stdout.write(self.style.WARNING("DRY-RUN: đã rollback mọi thay đổi."))
                else:
                    transaction.savepoint_commit(sid)

        if unmatched_out and unmatched_rows:
            self._write_unmatched_csv(unmatched_out, unmatched_rows, delimiter)

        self._print_report(
            stats, unmatched_examples, unknown_need_types, dry_run,
            unmatched_out, len(unmatched_rows), create_errors,
        )

    @staticmethod
    def _find_orders(row: dict) -> list[Order]:
        shipping = (row.get("shipping_code") or "").strip()
        if shipping:
            # Apply cho mọi order khớp shipping_code (sub-invoice .01/.02, mirror cross-channel...).
            orders = list(
                Order.objects.filter(shipping_code=shipping, deleted_at__isnull=True)
                .order_by("-created_at")
            )
            if orders:
                return orders
            # Fall through to phone nếu shipping không match (vd CSV ghi nhầm invoice_code vào shipping_code).

        phone_raw = row.get("phone", "")
        phone = _digits(phone_raw)
        if not phone:
            return []
        # 1) Match cuối phone trên Order.phone để tránh prefix khác (e.g. +84 vs 0).
        candidate = (
            Order.objects.filter(
                Q(phone__endswith=phone[-9:]) if len(phone) >= 9 else Q(phone=phone),
                deleted_at__isnull=True,
            )
            .order_by("-created_at")
            .first()
        )
        if candidate:
            return [candidate]
        # 2) Fallback: tìm Customer theo phone_normalized → lấy order mới nhất của customer đó
        #    (Order.phone có thể trống/sai nhưng customer link có thể đúng).
        phone_normalized = normalize_phone(phone_raw)
        if not phone_normalized:
            return []
        customer = Customer.objects.filter(
            phone_normalized=phone_normalized, deleted_at__isnull=True
        ).first()
        if customer is None:
            return []
        candidate = (
            Order.objects.filter(customer=customer, deleted_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        return [candidate] if candidate else []

    @staticmethod
    def _apply_scalar(assessment: OrderAssessment, row: dict, overwrite: bool) -> list[str]:
        changed: list[str] = []
        for field in SCALAR_FIELDS:
            new_raw = (row.get(field) or "").strip()
            if not new_raw:
                continue

            if field == "used_massage_before":
                new_value = _coerce_used_massage(new_raw)
                if new_value is None:
                    continue
            elif field == "age_text":
                # File CSV cũ ghi tuổi dạng số; chuyển sang mã nhóm để không
                # ghi giá trị ngoài whitelist vào cột.
                new_value = parse_age_group(new_raw)
                if not new_value:
                    continue
            else:
                new_value = new_raw

            current = getattr(assessment, field)
            # Boolean: chỉ None là "chưa có"; False ("chưa dùng massage") là dữ
            # liệu thật, không được để CSV ghi đè khi thiếu --overwrite.
            current_empty = current is None if field == "used_massage_before" else not current
            if not overwrite and not current_empty:
                continue
            if current == new_value:
                continue
            setattr(assessment, field, new_value)
            changed.append(field)
        return changed

    @staticmethod
    def _apply_arrays(assessment: OrderAssessment, row: dict, overwrite: bool) -> list[str]:
        changed: list[str] = []
        for field in ARRAY_FIELDS:
            new_codes = _split_codes(row.get(field, ""))
            if not new_codes:
                continue
            current: list[str] = list(getattr(assessment, field) or [])
            if not overwrite and current:
                continue
            merged = current + [c for c in new_codes if c not in current]
            if merged != current:
                setattr(assessment, field, merged)
                changed.append(field)
        return changed

    @staticmethod
    def _sync_needs(
        assessment: OrderAssessment,
        need_codes: list[str],
        catalog: dict[str, NeedType],
        unknown_counter: Counter,
    ) -> int:
        existing = set(
            OrderNeed.objects.filter(order_assessment=assessment).values_list("need_type__code", flat=True)
        )
        added = 0
        for code in need_codes:
            need_type = catalog.get(code)
            if need_type is None:
                unknown_counter[code] += 1
                continue
            if code in existing:
                continue
            OrderNeed.objects.create(order_assessment=assessment, need_type=need_type)
            added += 1
        return added

    @staticmethod
    def _load_total_csv(path: str, delimiter: str) -> tuple[dict, dict]:
        by_shipping: dict[str, dict] = {}
        by_phone: dict[str, dict] = {}
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            for row in reader:
                shipping = (row.get("shipping_code") or "").strip()
                if shipping:
                    by_shipping[shipping] = row
                phone = _digits(row.get("phone", ""))
                if len(phone) >= 9:
                    by_phone[phone[-9:]] = row
        return by_shipping, by_phone

    @staticmethod
    def _create_order_from_total(
        row: dict,
        total_by_shipping: dict,
        total_by_phone: dict,
        stats: Counter,
    ) -> Order | None:
        # Tìm full data trong t4_total.csv: ưu tiên shipping_code, fallback phone (last 9).
        shipping = (row.get("shipping_code") or "").strip()
        total_row = total_by_shipping.get(shipping) if shipping else None
        if total_row is None:
            phone = _digits(row.get("phone", ""))
            if len(phone) >= 9:
                total_row = total_by_phone.get(phone[-9:])
        if total_row is None:
            return None

        channel_id_str = (total_row.get("channel_id") or "").strip()
        if not channel_id_str.isdigit():
            stats["created_skip_no_channel"] += 1
            return None
        channel = Channel.objects.filter(
            id=int(channel_id_str), deleted_at__isnull=True
        ).first()
        if channel is None:
            stats["created_skip_no_channel"] += 1
            return None

        phone_raw = (total_row.get("phone") or "").strip()
        name = (total_row.get("name") or "").strip() or "Unknown"
        if phone_raw:
            customer = CustomerService.get_or_create_by_phone(phone=phone_raw, full_name=name)
        else:
            customer = Customer.objects.create(full_name=name, phone_raw="", phone_normalized="")

        staff_username = (total_row.get("staff") or "").strip()
        staff = None
        if staff_username:
            staff = User.objects.filter(
                username=staff_username, deleted_at__isnull=True
            ).first()

        order = Order.objects.create(
            channel=channel,
            customer=customer,
            staff=staff,
            name=name,
            phone=phone_raw,
            raw_address=(total_row.get("raw_address") or "").strip(),
            product_raw=(total_row.get("product_raw") or "").strip(),
            shipping_code=(total_row.get("shipping_code") or "").strip(),
            order_source="landing",
        )
        stats["created_from_total"] += 1
        return order

    @staticmethod
    def _write_unmatched_csv(path: str, rows: list[dict], delimiter: str) -> None:
        if not rows:
            return
        fieldnames = list(rows[0].keys())
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=delimiter)
            writer.writeheader()
            for r in rows:
                writer.writerow(r)

    def _print_report(self, stats, unmatched_examples, unknown_need_types, dry_run,
                       unmatched_out="", unmatched_count=0, create_errors=None):
        out = self.stdout.write
        style = self.style
        out("")
        out(style.MIGRATE_HEADING("=== KẾT QUẢ IMPORT ==="))
        for k in (
            "total",
            "matched_shipping_code",
            "matched_phone",
            "matched_multiple_siblings",
            "created_from_total",
            "created_skip_no_channel",
            "create_errors",
            "unmatched",
            "order_source_updated",
            "assessment_created",
            "assessment_existing",
            "assessment_updated",
            "need_links_added",
        ):
            out(f"  {k:<24} : {stats.get(k, 0)}")

        if unmatched_examples:
            out("")
            out(style.WARNING(f"Mẫu {len(unmatched_examples)} dòng không match được:"))
            for line in unmatched_examples:
                out(f"  - {line}")

        if unmatched_out and unmatched_count:
            out("")
            out(style.SUCCESS(f"Đã ghi {unmatched_count} dòng unmatched vào: {unmatched_out}"))

        if unknown_need_types:
            out("")
            out(style.WARNING("NeedType code không tồn tại trong DB (đã bỏ qua):"))
            for code, n in unknown_need_types.most_common():
                out(f"  - {code}: {n}")

        if create_errors:
            out("")
            out(style.ERROR(f"Lỗi tạo order ({len(create_errors)}):"))
            for err in create_errors[:20]:
                out(
                    f"  - shipping={err['shipping_code']!r} "
                    f"phone={err['phone']!r}: {err['error']}"
                )
            if len(create_errors) > 20:
                out(f"  ... và {len(create_errors) - 20} lỗi khác")

        if dry_run:
            out("")
            out(style.WARNING("** DRY-RUN ** Không có thay đổi nào được lưu."))
