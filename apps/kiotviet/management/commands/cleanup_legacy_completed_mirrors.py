"""One-time cleanup: mirror B bị KiotViet auto-complete (inv=1) trong khi A còn "Đang xử lý"
(inv=3), tạo TRƯỚC ngày cap-guard (2026-06-23). Delete (huỷ mềm) + tạo lại từ A qua luồng sync
hiện tại — patch sẽ cap delivery 3→2 nên mirror mới về (inv=3, del=2) khớp A.

KHÔNG phải logic thường trực: cap-guard (cb9a9902/ccaf29c0) đã chặn phát sinh mới; lệnh này chỉ
dọn tồn đọng lịch sử. MẶC ĐỊNH dry-run; cần --apply --yes để thực thi.

Ví dụ:
  # liệt kê (an toàn, không sửa gì)
  python manage.py cleanup_legacy_completed_mirrors
  # chạy thật, lô 50 đơn cho cặp kv4->kv5
  python manage.py cleanup_legacy_completed_mirrors --apply --yes --limit 50 --pair 4:5
"""
from __future__ import annotations

import re

from django.core.management.base import BaseCommand

from apps.kiotviet.infrastructure.services.invoice_mutations_push import delete_invoice_by_id
from apps.kiotviet.infrastructure.services.reconcile import iter_reconcile_pairs
from apps.kiotviet.infrastructure.services.sync_invoices_service import SyncInvoicesService
from apps.kiotviet.models import KiotVietChannel

_SYNC_DESC = re.compile(r"SYNC từ kênh \S+ - (\S+)")
_WINDOW_FROM = "2026-06-01 00:00:00"


def _g(inv: dict, *keys):
    for k in keys:
        v = inv.get(k)
        if v not in (None, ""):
            return v
    return None


class Command(BaseCommand):
    help = (
        "Dọn 1 lần mirror B auto-complete (inv=1) khi A đang xử lý (inv=3), tạo trước cap-guard. "
        "Dry-run mặc định; --apply --yes để thực thi."
    )

    def add_arguments(self, parser):
        parser.add_argument("--before", default="2026-06-23",
                            help="Chỉ dọn mirror B tạo TRƯỚC ngày này (YYYY-MM-DD). Mặc định = ngày cap-guard.")
        parser.add_argument("--to", default=None, help="toPurchaseDate quét A/B. Mặc định = --before.")
        parser.add_argument("--limit", type=int, default=0, help="Tối đa số đơn xử lý (0 = không giới hạn).")
        parser.add_argument("--pair", default=None, help="Giới hạn 1 cặp 'source_kv:target_kv', vd 4:5.")
        parser.add_argument("--apply", action="store_true", help="Thực thi delete+recreate (mặc định chỉ liệt kê).")
        parser.add_argument("--yes", action="store_true", help="Bắt buộc kèm --apply để chạy thật.")

    # ------------------------------------------------------------------
    def handle(self, *args, **opts):
        before = str(opts["before"]).strip()
        to_dt = (opts.get("to") or f"{before} 23:59:59").strip()
        limit = int(opts["limit"] or 0)
        apply = bool(opts["apply"])
        yes = bool(opts["yes"])
        pair_filter = (opts.get("pair") or "").strip()

        if apply and not yes:
            self.stderr.write("Từ chối: --apply cần kèm --yes để thực thi delete+recreate.")
            return

        mode = "APPLY (delete+recreate)" if apply else "DRY-RUN (chỉ liệt kê)"
        self.stdout.write(f"=== cleanup_legacy_completed_mirrors — {mode} | before<{before} | limit={limit or '∞'} ===")

        svc = SyncInvoicesService(log_callback=lambda ch, m: None)
        total_found = 0
        total_done = 0
        total_fail = 0

        for pair in iter_reconcile_pairs():
            if pair["skip"]:
                continue
            s_kv, t_kv = pair["source_kv_id"], pair["target_kv_id"]
            if pair_filter and pair_filter != f"{s_kv}:{t_kv}":
                continue
            s_branch, t_branch = pair["source_branch_id"], pair["target_branch_id"]
            src_ch = KiotVietChannel.objects.get(id=s_kv)
            tgt_ch = KiotVietChannel.objects.get(id=t_kv)

            # A: code -> invoice status (bỏ đơn huỷ)
            a_status: dict[str, int] = {}
            for inv in self._list_all(svc, src_ch, s_branch, to_dt):
                st = int(_g(inv, "status", "Status") or 0)
                if st == 2:
                    continue
                code = _g(inv, "code", "Code")
                if code:
                    a_status[code] = st

            # B mirror: chỉ lấy đơn inv=1, tạo trước `before`, có description SYNC, A tương ứng đang inv=3
            candidates: list[tuple[str, int, str]] = []  # (src_code, b_id, b_code)
            for inv in self._list_all(svc, tgt_ch, t_branch, to_dt):
                if int(_g(inv, "status", "Status") or 0) != 1:
                    continue
                created = str(_g(inv, "createdDate", "CreatedDate", "purchaseDate", "PurchaseDate") or "")[:10]
                if not created or created >= before:
                    continue
                m = _SYNC_DESC.search(str(_g(inv, "description", "Description") or ""))
                if not m:
                    continue
                src_code = m.group(1)
                if a_status.get(src_code) != 3:
                    continue
                b_id = _g(inv, "id", "Id")
                b_code = _g(inv, "code", "Code")
                if b_id and b_id not in (-1, 0):
                    candidates.append((src_code, int(b_id), str(b_code or "")))

            self.stdout.write(
                f"\n[kv{s_kv}->kv{t_kv}] tìm thấy {len(candidates)} mirror tồn đọng (B inv=1, A inv=3, tạo <{before})"
            )
            total_found += len(candidates)

            for src_code, b_id, b_code in candidates:
                if limit and total_done + total_fail >= limit:
                    self.stdout.write(f"  …dừng vì đạt --limit {limit}")
                    break
                if not apply:
                    self.stdout.write(f"  [dry-run] sẽ delete B:{b_code}(id={b_id}) rồi tạo lại từ A:{src_code}")
                    continue
                try:
                    delete_invoice_by_id(invoice_id=b_id, order=None, channel=tgt_ch)
                except Exception as exc:  # noqa: BLE001
                    total_fail += 1
                    self.stderr.write(f"  DELETE FAIL B:{b_code}(id={b_id}) <- A:{src_code}: {exc}")
                    continue
                try:
                    svc.run(
                        source_kiotviet_channel_id=s_kv, source_branch_id=s_branch,
                        target_kiotviet_channel_id=t_kv, target_branch_id=t_branch,
                        from_dt=_WINDOW_FROM, to_dt=to_dt,
                        invoice_codes=src_code, yes=True, dry_run=False,
                    )
                    total_done += 1
                    self.stdout.write(f"  OK deleted+recreated A:{src_code} (B cũ id={b_id})")
                except Exception as exc:  # noqa: BLE001
                    total_fail += 1
                    self.stderr.write(
                        f"  RECREATE FAIL A:{src_code} (B cũ id={b_id} đã huỷ; lần reconcile tới sẽ tạo lại): {exc}"
                    )

        self.stdout.write(
            f"\n=== TỔNG: found={total_found} | {'recreated' if apply else 'dry-run'}={total_done} | fail={total_fail} ==="
        )
        if not apply and total_found:
            self.stdout.write("Chạy thật: thêm --apply --yes (nên kèm --limit nhỏ + --pair để thử lô đầu).")

    # ------------------------------------------------------------------
    @staticmethod
    def _list_all(svc: SyncInvoicesService, channel, branch_id, to_dt: str) -> list[dict]:
        out: list[dict] = []
        skip = 0
        while True:
            total, page = svc._web_list_invoices(
                channel=channel, branch_id=int(branch_id),
                from_dt=_WINDOW_FROM, to_dt=to_dt, top=500, skip=skip,
            )
            if not page:
                break
            out += page
            skip += len(page)
            if len(page) < 500 or (total and skip >= int(total)):
                break
        return out
