"""Command báo cáo mirror B total=0: chỉ đếm đơn A>0, không tính đơn A cũng 0đ (hàng tặng)."""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.kiotviet.models import KiotVietChannel

_CMD = "report_zero_total_mirrors"
_PATH = "apps.kiotviet.management.commands.report_zero_total_mirrors"


def _a(code, total, status=1, day="2026-08-11"):
    return {"id": 1, "code": code, "total": total, "status": status, "purchaseDate": f"{day}T09:00:00"}


def _b(bid, code, src_code, total, status=1):
    return {
        "id": bid, "code": code, "total": total, "status": status,
        "description": f"SYNC từ kênh 231174 - {src_code}",
    }


class ReportZeroTotalMirrorsTests(TestCase):
    def setUp(self):
        self.old = KiotVietChannel.objects.create(
            name="KV Old", branch_id="1000000477", client_id="c", client_secret="s",
            retailer="r-old", old=True,
        )
        self.new = KiotVietChannel.objects.create(
            name="KV New", branch_id="231174", client_id="c", client_secret="s",
            retailer="r-new", old=False, paired_old_channel=self.old,
        )

    def _run(self, a_rows, b_rows, **kwargs):
        pair = {
            "skip": False, "pair": "kv-test",
            "source_kv_id": self.new.id, "source_branch_id": "231174",
            "target_kv_id": self.old.id, "target_branch_id": "1000000477",
        }

        def fake_list(self_svc, *, channel, branch_id, from_dt, to_dt, top, skip):
            if skip:
                return (0, [])
            rows = a_rows if channel.id == self.new.id else b_rows
            return (len(rows), list(rows))

        out = StringIO()
        with patch(f"{_PATH}.iter_reconcile_pairs", return_value=iter([pair])), \
             patch(
                 "apps.kiotviet.infrastructure.services.sync_invoices_service"
                 ".SyncInvoicesService._web_list_invoices",
                 new=fake_list,
             ):
            call_command(_CMD, stdout=out, **kwargs)
        return out.getvalue()

    def test_reports_mirror_with_zero_total_when_source_has_amount(self):
        output = self._run(
            [_a("HD015121", 2130300.0)],
            [_b(131961606, "HD146858", "HD015121", 0.0)],
        )
        assert "A:HD015121" in output
        assert "B:HD146858(id=131961606)" in output
        assert "1 mirror cần dựng lại" in output
        assert "2,130,300" in output

    def test_skips_mirror_when_source_invoice_is_also_zero(self):
        # Đơn tặng 0đ: B total=0 là ĐÚNG, không được báo là hỏng.
        output = self._run(
            [_a("HD000777", 0.0)],
            [_b(999, "HD900777", "HD000777", 0.0)],
        )
        assert "0 mirror cần dựng lại" in output
        assert "bỏ qua 1" in output

    def test_ignores_cancelled_mirror_and_healthy_mirror(self):
        output = self._run(
            [_a("HD000001", 500000.0), _a("HD000002", 700000.0)],
            [
                _b(11, "HD900001", "HD000001", 0.0, status=2),   # đã huỷ
                _b(12, "HD900002", "HD000002", 700000.0),        # giá đúng
            ],
        )
        assert "0 mirror cần dựng lại" in output
