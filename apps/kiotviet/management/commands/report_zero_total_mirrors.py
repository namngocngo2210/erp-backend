"""Báo cáo (CHỈ ĐỌC): mirror B có total = 0 trong khi hoá đơn nguồn A có total > 0.

Gốc lỗi: từ 2026-06-23 (commit 7313882d) builder chỉ đọc ``priceAfterTax`` của tax row, không
fallback. KiotViet trả ``priceAfterTax = 0`` cho một số hoá đơn (gặp ở HĐ đã phát hành eInvoice)
— giá thật khi đó chỉ còn ở ``subTotalAfterTax`` — nên mọi dòng của B được tạo với giá 0. Builder
đã fix (MR !1241) nhưng các mirror tạo sai trong khoảng đó vẫn nằm trên B.

Lệnh này KHÔNG sửa/xoá gì: chỉ liệt kê đơn cần dựng lại để người vận hành tự quyết cách xử lý.

Guard: chỉ tính đơn có A.total > 0. Đơn A thật sự 0đ (hàng tặng) thì B total = 0 là ĐÚNG.

Ví dụ:
  python manage.py report_zero_total_mirrors
  python manage.py report_zero_total_mirrors --pair 4:5 --to "2026-08-17 23:59:59"
"""
from __future__ import annotations

import re

from django.core.management.base import BaseCommand

from apps.kiotviet.infrastructure.services.reconcile import iter_reconcile_pairs
from apps.kiotviet.infrastructure.services.sync_invoices_service import SyncInvoicesService
from apps.kiotviet.models import KiotVietChannel

_SYNC_DESC = re.compile(r"SYNC từ kênh \S+ - (\S+)")
# Ngày commit gỡ chain fallback giá — mọi mirror sai đều tạo từ mốc này trở đi.
_DEFAULT_FROM = "2026-06-23 00:00:00"
_PAGE = 500


def _g(inv: dict, *keys):
    for k in keys:
        v = inv.get(k)
        if v not in (None, ""):
            return v
    return None


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class Command(BaseCommand):
    help = "Liệt kê (chỉ đọc) mirror B có total=0 trong khi A>0 — hệ quả bug priceAfterTax=0."

    def add_arguments(self, parser):
        parser.add_argument("--from", dest="from_dt", default=_DEFAULT_FROM,
                            help="fromPurchaseDate quét A/B. Mặc định = ngày phát sinh bug (2026-06-23).")
        parser.add_argument("--to", dest="to_dt", default=None, help="toPurchaseDate quét A/B (mặc định: đến nay).")
        parser.add_argument("--pair", default=None, help="Giới hạn 1 cặp 'source_kv:target_kv', vd 4:5.")

    # ------------------------------------------------------------------
    def handle(self, *args, **opts):
        from_dt = str(opts["from_dt"]).strip()
        to_dt = (opts.get("to_dt") or "").strip() or None
        pair_filter = (opts.get("pair") or "").strip()

        self.stdout.write(
            f"=== report_zero_total_mirrors (chỉ đọc) | from={from_dt} | to={to_dt or 'nay'} ==="
        )

        svc = SyncInvoicesService(log_callback=lambda ch, m: None)
        grand_total = 0
        grand_amount = 0.0
        grand_skipped = 0

        for pair in iter_reconcile_pairs():
            if pair["skip"]:
                continue
            s_kv, t_kv = pair["source_kv_id"], pair["target_kv_id"]
            if pair_filter and pair_filter != f"{s_kv}:{t_kv}":
                continue
            s_branch, t_branch = pair["source_branch_id"], pair["target_branch_id"]
            src_ch = KiotVietChannel.objects.get(id=s_kv)
            tgt_ch = KiotVietChannel.objects.get(id=t_kv)

            # A: code -> (total, ngày mua) — bỏ đơn huỷ.
            a_info: dict[str, tuple[float, str]] = {}
            for inv in self._list_all(svc, src_ch, s_branch, from_dt, to_dt):
                if int(_g(inv, "status", "Status") or 0) == 2:
                    continue
                code = _g(inv, "code", "Code")
                if code:
                    day = str(_g(inv, "purchaseDate", "PurchaseDate") or "")[:10]
                    a_info[str(code)] = (_num(_g(inv, "total", "Total")), day)

            rows: list[tuple[str, str, str, float, str]] = []
            skipped_zero_a = 0
            for inv in self._list_all(svc, tgt_ch, t_branch, from_dt, to_dt):
                if int(_g(inv, "status", "Status") or 0) == 2:
                    continue
                if _num(_g(inv, "total", "Total")) > 0:
                    continue
                m = _SYNC_DESC.search(str(_g(inv, "description", "Description") or ""))
                if not m:
                    continue
                src_code = m.group(1)
                a_total, a_day = a_info.get(src_code, (0.0, ""))
                if a_total <= 0:
                    # A cũng 0đ (hàng tặng) hoặc A nằm ngoài cửa sổ quét → không phải nạn nhân.
                    skipped_zero_a += 1
                    continue
                b_id = _g(inv, "id", "Id")
                if b_id and b_id not in (-1, 0):
                    rows.append((src_code, str(_g(inv, "code", "Code") or ""), str(b_id), a_total, a_day))

            pair_amount = sum(r[3] for r in rows)
            self.stdout.write(
                f"\n[kv{s_kv}->kv{t_kv}] {len(rows)} mirror total=0 (A>0), "
                f"tổng tiền hụt {pair_amount:,.0f} | bỏ qua (A cũng 0đ/ngoài cửa sổ)={skipped_zero_a}"
            )
            for src_code, b_code, b_id, a_total, a_day in sorted(rows, key=lambda r: r[4]):
                self.stdout.write(f"  {a_day}  A:{src_code}  ->  B:{b_code}(id={b_id})  A.total={a_total:,.0f}")

            grand_total += len(rows)
            grand_amount += pair_amount
            grand_skipped += skipped_zero_a

        self.stdout.write(
            f"\n=== TỔNG: {grand_total} mirror cần dựng lại | tiền hụt {grand_amount:,.0f} "
            f"| bỏ qua {grand_skipped} ==="
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _list_all(svc: SyncInvoicesService, channel, branch_id, from_dt: str, to_dt: str | None) -> list[dict]:
        out: list[dict] = []
        skip = 0
        while True:
            total, page = svc._web_list_invoices(
                channel=channel, branch_id=int(branch_id),
                from_dt=from_dt, to_dt=to_dt, top=_PAGE, skip=skip,
            )
            if not page:
                break
            out += page
            skip += len(page)
            if len(page) < _PAGE or (total and skip >= int(total)):
                break
        return out
