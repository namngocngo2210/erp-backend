"""Đẩy tồn đầu kỳ Hàng hoá CRM → Pancake POS theo (kho, SKU) (two-way-sync §7 A1).

    manage.py pancake_pos_initial_stock --dry-run                  # so sánh CRM vs Pancake
    manage.py pancake_pos_initial_stock --dry-run --warehouse DM1 --sku OHI01-DEN
    manage.py pancake_pos_initial_stock                            # ghi thật (cần PANCAKE_POS_ENABLED)

`--warehouse` nhận id hoặc mã kho CRM (lặp được). Ghi thật đặt tồn TUYỆT ĐỐI
(`is_actual_remain_quantity=true`) theo lô 100 → chạy lại cho cùng kết quả.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.common.db_guards import single_flight
from apps.pancake_pos.application.services.factory import build_initial_stock_service
from apps.pancake_pos.application.services.initial_stock_jobs import LOCK_KEY, LOCK_TTL_SECONDS
from apps.pancake_pos.infrastructure.pancake_pos_client import PancakePosError
from apps.pancake_pos.infrastructure.stock_reader import SqlWarehouseReader

MAX_PRINTED = 30


def resolve_warehouses(values: list[str]) -> list[int] | None:
    if not values:
        return None
    by_code = {}
    ids = set()
    for warehouse in SqlWarehouseReader().list_warehouses():
        by_code[warehouse.code.strip().upper()] = warehouse.id
        ids.add(warehouse.id)
    out: list[int] = []
    for value in values:
        text = value.strip()
        if text.isdigit() and int(text) in ids:
            out.append(int(text))
        elif text.upper() in by_code:
            out.append(by_code[text.upper()])
        else:
            raise CommandError(f"Không có kho CRM '{value}'")
    return out


class Command(BaseCommand):
    help = "Đẩy tồn đầu kỳ Hàng hoá CRM lên Pancake POS (--dry-run chỉ so sánh, không ghi)"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Chỉ so sánh, không gọi API ghi")
        parser.add_argument("--warehouse", action="append", default=[], help="Id hoặc mã kho CRM (lặp được)")
        parser.add_argument("--sku", action="append", default=[], help="Chỉ SKU này (lặp được)")

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        warehouse_ids = resolve_warehouses(options["warehouse"])
        with single_flight(LOCK_KEY, LOCK_TTL_SECONDS) as acquired:
            if not acquired:
                raise CommandError("Đang có lượt tồn đầu kỳ khác chạy")
            try:
                service = build_initial_stock_service(dry_run=dry_run)
                report = service.run(dry_run=dry_run, warehouse_ids=warehouse_ids, skus=options["sku"] or None)
            except PancakePosError as exc:
                raise CommandError(str(exc)) from exc

        t = report.totals
        names = ", ".join(f"{w['warehouse_name']}→{w['pancake_warehouse_name'] or w['pancake_warehouse_id']}"
                          for w in report.warehouses)
        self.stdout.write(f"Lượt #{report.run_id} ({'dry-run' if dry_run else 'ghi'}) · kho: {names}")
        self.stdout.write(
            f"Dòng (kho×SKU) {t.get('lines', 0)} · SKU {t.get('skus', 0)} · cần ghi {t.get('to_write', 0)} · "
            f"khớp {t.get('unchanged', 0)} · chưa rõ trên Pancake {t.get('unknown_on_pancake', 0)} · "
            f"âm→0 {t.get('negative', 0)} · chưa liên kết {t.get('missing_link', 0)} · lô {t.get('batches', 0)}"
        )
        for diff in report.diffs[:MAX_PRINTED]:
            pancake = "?" if diff["pancake_qty"] is None else diff["pancake_qty"]
            self.stdout.write(
                f"  {diff['sku']} @ {diff['warehouse_name']}: CRM {diff['crm_qty']} → đặt {diff['push_qty']} "
                f"(Pancake {pancake})"
            )
        for item in report.missing_links[:MAX_PRINTED]:
            self.stdout.write(self.style.WARNING(f"  chưa liên kết {item['sku']}: tồn {item['quantity']}"))
        for item in report.errors:
            self.stdout.write(self.style.ERROR(f"  lỗi lô {item['batch']}: {item['message']}"))
        if dry_run:
            self.stdout.write(self.style.SUCCESS("Xong (dry-run, không ghi gì lên Pancake)."))
        else:
            style = self.style.WARNING if report.errors else self.style.SUCCESS
            self.stdout.write(style(f"Xong: đã ghi {t.get('written', 0)}, lỗi {t.get('failed', 0)}."))
