"""Tests cho logic check/purge của SyncInvoicesService (WP8.2 audit hardening).

QUAN TRỌNG NHẤT: xác nhận KHÔNG purge nhầm trên kênh B (mirror):
- Chỉ xóa mirror match qua description "SYNC từ kênh ... - {src_code}".
- Đơn B thủ công (không có SYNC desc) / mirror của source khác → KHÔNG xóa.
- Mirror đã hủy sẵn → bỏ qua (KiotViet trả 420 nếu hủy lại).
- Đơn ngoài window / đơn hủy / vận đơn hủy (delivery status=6) → không đưa vào danh sách xóa.
- Thiếu yes_flag (không dry-run) → chặn DELETE bằng SyncError.
- Guard hoàn thành/hủy cross-channel: hóa đơn HỦY của kênh CŨ không được lật
  invoice_status=2 lên đơn thật của kênh MỚI (memory kiotviet-sync-single-flow).
- Phân trang KHÔNG được dừng ở trang đầu khi KiotViet cắt pageSize về trần 100
  (bug 27/06→26/08: purge/check xin top=500 nên chỉ quét 100 đơn cũ nhất window).
"""
import json
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.kiotviet.infrastructure.services.import_invoices_service import ImportInvoicesService
from apps.kiotviet.infrastructure.services._sync_service_shared import (
    KIOTVIET_LIST_MAX_PAGE_SIZE,
)
from apps.kiotviet.infrastructure.services.sync_invoices_service import (
    SyncError,
    SyncInvoicesService,
)
from apps.kiotviet.models import KiotVietChannel

CHECK_MIXIN = "apps.kiotviet.infrastructure.services._sync_service_check_mixin"
PURGE_MIXIN = "apps.kiotviet.infrastructure.services._sync_service_purge_mixin"
SYNC_SERVICE = "apps.kiotviet.infrastructure.services.sync_invoices_service"

FROM_DT = "2026-04-01 00:00:00"
TO_DT = "2026-04-30 23:59:59"


class _MockResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)
        self.content = self.text.encode("utf-8")

    def json(self):
        return self._payload


def _sync_desc(src_code: str) -> str:
    return f"SYNC từ kênh kv1 - {src_code}"


class SyncServiceTestBase(TestCase):
    def setUp(self):
        self.source = KiotVietChannel.objects.create(
            name="KV New A", branch_id="231174", branch_ids=["231174"],
            client_id="cid-a", client_secret="sec-a", retailer="ret-a", old=False,
        )
        self.target = KiotVietChannel.objects.create(
            name="KV Old B", branch_id="415922", branch_ids=["415922"],
            client_id="cid-b", client_secret="sec-b", retailer="ret-b", old=True,
        )
        self.service = SyncInvoicesService()

    def _patch_web_list(self, source_rows, target_rows):
        """Mock self._web_list_invoices: trả 1 trang theo channel rồi hết."""
        def fake(*, channel, branch_id, from_dt, to_dt, top, skip):
            rows = source_rows if channel.id == self.source.id else target_rows
            if skip:
                return len(rows), []
            return len(rows), list(rows)

        return patch.object(SyncInvoicesService, "_web_list_invoices", side_effect=fake)

    def _patch_web_list_capped(self, source_rows, target_rows):
        """Mock _web_list_invoices GIỐNG SERVER THẬT: cắt mỗi trang về trần 100.

        KiotViet không báo lỗi khi caller xin pageSize lớn hơn — nó lặng lẽ trả 100.
        Caller nào dừng vì "trang ngắn hơn số đã xin" sẽ mất toàn bộ dữ liệu từ trang 2.
        """
        def fake(*, channel, branch_id, from_dt, to_dt, top, skip):
            rows = source_rows if channel.id == self.source.id else target_rows
            served = min(int(top), KIOTVIET_LIST_MAX_PAGE_SIZE)
            return len(rows), list(rows[skip:skip + served])

        return patch.object(SyncInvoicesService, "_web_list_invoices", side_effect=fake)


class CheckMissingTests(SyncServiceTestBase):
    def test_reports_only_unsynced_codes_and_excludes_cancelled_source(self):
        source_rows = [
            {"Id": 1, "Code": "HD001", "Status": 1},
            {"Id": 2, "Code": "HD002", "Status": 1},          # chưa sync
            {"Id": 3, "Code": "HD003", "Status": 2},          # A đã hủy → loại
        ]
        target_rows = [
            {"Id": 91, "Code": "HDB01", "Status": 1, "Description": _sync_desc("HD001")},
        ]
        with self._patch_web_list(source_rows, target_rows):
            result = self.service._check_missing(
                source_channel=self.source, source_branch_id=231174,
                target_channel=self.target, target_branch_id=415922,
                from_dt=FROM_DT, to_dt=TO_DT, page_size=100, sleep_pages=0,
            )
        self.assertEqual(result["missing"], ["HD002"])
        self.assertEqual(result["extras_to_delete"], [])
        self.assertEqual(result["duplicates_to_delete"], [])

    def test_flags_orphan_mirror_and_duplicate_mirrors(self):
        source_rows = [{"Id": 1, "Code": "HD001", "Status": 1}]
        target_rows = [
            # duplicate mirror của HD001: giữ code lớn nhất (HDB12), xóa HDB11
            {"Id": 911, "Code": "HDB11", "Status": 1, "Description": _sync_desc("HD001")},
            {"Id": 912, "Code": "HDB12", "Status": 1, "Description": _sync_desc("HD001")},
            # orphan: mirror của source code ngoài window A
            {"Id": 920, "Code": "HDB20", "Status": 1, "Description": _sync_desc("HD999")},
            # đơn B thủ công (không SYNC desc) → chỉ báo cáo, KHÔNG vào danh sách xóa
            {"Id": 930, "Code": "HDB30", "Status": 1, "Description": "đơn tay"},
        ]
        with self._patch_web_list(source_rows, target_rows):
            result = self.service._check_missing(
                source_channel=self.source, source_branch_id=231174,
                target_channel=self.target, target_branch_id=415922,
                from_dt=FROM_DT, to_dt=TO_DT, page_size=100, sleep_pages=0,
            )
        self.assertEqual(result["missing"], [])
        self.assertEqual(
            result["extras_to_delete"],
            [{"id": 920, "code": "HDB20", "src_code": "HD999"}],
        )
        self.assertEqual(
            result["duplicates_to_delete"],
            [{"id": 911, "code": "HDB11", "src_code": "HD001"}],
        )
        # Đơn tay HDB30 tuyệt đối không nằm trong bất kỳ danh sách xóa nào.
        all_delete_ids = {e["id"] for e in result["extras_to_delete"] + result["duplicates_to_delete"]}
        self.assertNotIn(930, all_delete_ids)


class PurgeCancelledTests(SyncServiceTestBase):
    def test_deletes_only_mirrors_of_cancelled_source_invoices(self):
        source_rows = [
            {"Id": 1, "Code": "HDX", "Status": 2},   # A hủy
            {"Id": 2, "Code": "HDY", "Status": 1},   # A còn sống
        ]
        target_rows = [
            {"Id": 901, "Code": "HDBX", "Status": 1, "Description": _sync_desc("HDX")},
            {"Id": 902, "Code": "HDBY", "Status": 1, "Description": _sync_desc("HDY")},
            {"Id": 903, "Code": "HDBM", "Status": 1, "Description": "đơn tay không SYNC"},
        ]
        with self._patch_web_list(source_rows, target_rows), \
                patch(f"{PURGE_MIXIN}.delete_invoice_by_id") as mock_delete:
            self.service._purge_cancelled(
                source_channel=self.source, source_branch_id=231174,
                target_channel=self.target, target_branch_id=415922,
                from_dt=FROM_DT, to_dt=TO_DT, page_size=100,
                dry_run=False, yes_flag=True, sleep_pages=0, sleep_between=0,
            )
        mock_delete.assert_called_once_with(
            invoice_id=901, order=None, channel=self.target
        )

    def test_skips_mirror_already_cancelled_on_target(self):
        source_rows = [{"Id": 1, "Code": "HDX", "Status": 2}]
        target_rows = [
            # Mirror đã hủy sẵn → cố hủy lại sẽ 420, phải bỏ qua
            {"Id": 901, "Code": "HDBX", "Status": 2, "Description": _sync_desc("HDX")},
        ]
        with self._patch_web_list(source_rows, target_rows), \
                patch(f"{PURGE_MIXIN}.delete_invoice_by_id") as mock_delete:
            self.service._purge_cancelled(
                source_channel=self.source, source_branch_id=231174,
                target_channel=self.target, target_branch_id=415922,
                from_dt=FROM_DT, to_dt=TO_DT, page_size=100,
                dry_run=False, yes_flag=True, sleep_pages=0, sleep_between=0,
            )
        mock_delete.assert_not_called()

    def test_requires_yes_flag_before_actual_delete(self):
        source_rows = [{"Id": 1, "Code": "HDX", "Status": 2}]
        target_rows = [
            {"Id": 901, "Code": "HDBX", "Status": 1, "Description": _sync_desc("HDX")},
        ]
        with self._patch_web_list(source_rows, target_rows), \
                patch(f"{PURGE_MIXIN}.delete_invoice_by_id") as mock_delete:
            with self.assertRaises(SyncError):
                self.service._purge_cancelled(
                    source_channel=self.source, source_branch_id=231174,
                    target_channel=self.target, target_branch_id=415922,
                    from_dt=FROM_DT, to_dt=TO_DT, page_size=100,
                    dry_run=False, yes_flag=False, sleep_pages=0, sleep_between=0,
                )
        mock_delete.assert_not_called()

    def test_dry_run_never_deletes(self):
        source_rows = [{"Id": 1, "Code": "HDX", "Status": 2}]
        target_rows = [
            {"Id": 901, "Code": "HDBX", "Status": 1, "Description": _sync_desc("HDX")},
        ]
        with self._patch_web_list(source_rows, target_rows), \
                patch(f"{PURGE_MIXIN}.delete_invoice_by_id") as mock_delete:
            self.service._purge_cancelled(
                source_channel=self.source, source_branch_id=231174,
                target_channel=self.target, target_branch_id=415922,
                from_dt=FROM_DT, to_dt=TO_DT, page_size=100,
                dry_run=True, yes_flag=False, sleep_pages=0, sleep_between=0,
            )
        mock_delete.assert_not_called()


class ListPaginationCapTests(SyncServiceTestBase):
    """Trần pageSize=100 của KiotViet không được làm vòng quét dừng ở trang đầu."""

    # 250 đơn/kênh: 3 đơn hủy nằm trong trang 1 (đúng con số 3/55 bắt được trên prod),
    # 5 đơn hủy còn lại ở trang 2-3 — chỉ thấy được nếu phân trang chạy tiếp.
    CANCELLED_IDX = (5, 40, 99, 100, 137, 180, 201, 249)

    def _rows(self):
        source_rows = [
            {
                "Id": i + 1,
                "Code": f"HD{i:05d}",
                "Status": 2 if i in self.CANCELLED_IDX else 1,
            }
            for i in range(250)
        ]
        target_rows = [
            {
                "Id": 90000 + i,
                "Code": f"HDB{i:05d}",
                "Status": 1,
                "Description": _sync_desc(f"HD{i:05d}"),
            }
            for i in range(250)
        ]
        return source_rows, target_rows

    def test_purge_cancelled_scans_every_page(self):
        source_rows, target_rows = self._rows()
        with self._patch_web_list_capped(source_rows, target_rows), \
                patch(f"{PURGE_MIXIN}.delete_invoice_by_id") as mock_delete:
            self.service._purge_cancelled(
                source_channel=self.source, source_branch_id=231174,
                target_channel=self.target, target_branch_id=415922,
                from_dt=FROM_DT, to_dt=TO_DT, page_size=100,
                dry_run=False, yes_flag=True, sleep_pages=0, sleep_between=0,
            )
        deleted_ids = sorted(c.kwargs["invoice_id"] for c in mock_delete.call_args_list)
        self.assertEqual(deleted_ids, sorted(90000 + i for i in self.CANCELLED_IDX))

    def test_check_missing_scans_every_page(self):
        source_rows, target_rows = self._rows()
        # Bỏ mirror của HD00200 (đơn còn sống, nằm ở trang 3): dừng sớm ở trang 1
        # thì không bao giờ thấy nó thiếu.
        target_rows = [r for r in target_rows if r["Description"] != _sync_desc("HD00200")]
        with self._patch_web_list_capped(source_rows, target_rows):
            result = self.service._check_missing(
                source_channel=self.source, source_branch_id=231174,
                target_channel=self.target, target_branch_id=415922,
                from_dt=FROM_DT, to_dt=TO_DT, page_size=100, sleep_pages=0,
            )
        self.assertEqual(result["missing"], ["HD00200"])
        # Mirror của đơn A đã hủy bị coi là "extra" — có đủ cả HDB00249 ở trang cuối
        # nghĩa là vòng quét đã đi hết, không dừng ở trang 1.
        self.assertEqual(
            sorted(x["id"] for x in result["extras_to_delete"]),
            sorted(90000 + i for i in self.CANCELLED_IDX),
        )
        self.assertEqual(result["duplicates_to_delete"], [])

    def test_web_list_never_requests_more_than_cap(self):
        """Caller xin 500 thì request thật vẫn phải là pageSize=100."""
        captured = {}

        def fake_request(*, order, channel, method, endpoint, params):
            captured.update(params)
            return _MockResponse({"total": 0, "data": []})

        with patch(f"{SYNC_SERVICE}._kiotviet_request_with_retry", side_effect=fake_request):
            self.service._web_list_invoices(
                channel=self.source, branch_id=231174,
                from_dt=FROM_DT, to_dt=TO_DT, top=500, skip=0,
            )
        self.assertEqual(captured["pageSize"], KIOTVIET_LIST_MAX_PAGE_SIZE)


class PurgeWrongDateTests(SyncServiceTestBase):
    def test_deletes_only_out_of_window_mirrors_of_known_sources(self):
        source_rows = [{"Id": 1, "Code": "HD001", "Status": 1}]
        target_rows = [
            # mirror HD001 sai ngày (ngoài [from, to]) → xóa
            {"Id": 901, "Code": "HDB01", "Status": 1,
             "Description": _sync_desc("HD001"), "PurchaseDate": "2026-03-15 10:00:00"},
            # mirror HD001 đúng window → giữ
            {"Id": 902, "Code": "HDB02", "Status": 1,
             "Description": _sync_desc("HD001"), "PurchaseDate": "2026-04-10 10:00:00"},
            # mirror của source KHÁC (không thuộc window A) dù sai ngày → giữ
            {"Id": 903, "Code": "HDB03", "Status": 1,
             "Description": _sync_desc("HD777"), "PurchaseDate": "2026-03-15 10:00:00"},
            # đơn tay không SYNC desc → giữ
            {"Id": 904, "Code": "HDB04", "Status": 1,
             "Description": "", "PurchaseDate": "2026-03-15 10:00:00"},
        ]
        with self._patch_web_list(source_rows, target_rows), \
                patch(f"{PURGE_MIXIN}.delete_invoice_by_id") as mock_delete:
            self.service._purge_wrong_date(
                source_channel=self.source, source_branch_id=231174,
                target_channel=self.target, target_branch_id=415922,
                from_dt=FROM_DT, to_dt=TO_DT, page_size=100,
                dry_run=False, yes_flag=True, sleep_pages=0, sleep_between=0,
            )
        mock_delete.assert_called_once_with(
            invoice_id=901, order=None, channel=self.target
        )


class PurgeTargetBranchTests(SyncServiceTestBase):
    @patch(f"{PURGE_MIXIN}.delete_invoice_by_id")
    # BE-5: vòng retry tay đã hợp nhất về _kiotviet_request_with_retry (gateway).
    @patch(f"{PURGE_MIXIN}._kiotviet_request_with_retry")
    def test_skips_cancelled_delivery_cancelled_and_foreign_mirrors(self, mock_request, mock_delete):
        invoices = [
            # mirror đúng source code → xóa
            {"id": 901, "code": "HDB01", "status": 1,
             "description": _sync_desc("HD001"), "purchaseDate": "2026-04-05 09:00:00"},
            # đơn đã hủy → bỏ
            {"id": 902, "code": "HDB02", "status": 2,
             "description": _sync_desc("HD001"), "purchaseDate": "2026-04-05 09:00:00"},
            # vận đơn đã hủy (delivery status=6) → bỏ
            {"id": 903, "code": "HDB03", "status": 1,
             "invoiceDelivery": {"status": 6},
             "description": _sync_desc("HD001"), "purchaseDate": "2026-04-05 09:00:00"},
            # mirror của source khác → bỏ (only_source_codes lọc)
            {"id": 904, "code": "HDB04", "status": 1,
             "description": _sync_desc("HD999"), "purchaseDate": "2026-04-05 09:00:00"},
            # đơn tay không desc → bỏ
            {"id": 905, "code": "HDB05", "status": 1,
             "description": "", "purchaseDate": "2026-04-05 09:00:00"},
        ]
        mock_request.return_value = _MockResponse({"data": invoices, "total": len(invoices)})
        summary = {"purged": 0, "purge_errors": 0}

        self.service._purge_target_branch(
            target_channel=self.target, target_branch_id=415922,
            from_dt=FROM_DT, to_dt=TO_DT, page_size=100,
            dry_run=False, yes_flag=True, sleep_pages=0, sleep_between=0,
            summary=summary, only_source_codes={"HD001"},
        )

        mock_delete.assert_called_once_with(
            invoice_id=901, order=None, channel=self.target
        )
        self.assertEqual(summary["purged"], 1)
        self.assertEqual(summary["purge_errors"], 0)


class SelectDuplicatesTests(SimpleTestCase):
    def test_keeps_oldest_by_created_date_and_deletes_rest(self):
        entries = {
            "HD001": [
                {"id": 2, "code": "HDB2", "created_date": "2026-04-02 10:00:00"},
                {"id": 1, "code": "HDB1", "created_date": "2026-04-01 10:00:00"},
                {"id": 3, "code": "HDB3", "created_date": ""},  # thiếu ngày → đẩy xuống cuối
            ],
            "HD002": [  # nhóm 1 đơn → không đụng
                {"id": 9, "code": "HDB9", "created_date": "2026-04-01 08:00:00"},
            ],
        }
        to_keep, to_delete = SyncInvoicesService._select_duplicates_to_delete(entries)
        self.assertEqual([e["id"] for e in to_keep], [1])
        self.assertEqual(sorted(e["id"] for e in to_delete), [2, 3])
        self.assertTrue(all(e["src"] == "HD001" for e in to_delete))


class CrossChannelCancelGuardTests(TestCase):
    """Guard theo memory kiotviet-sync-single-flow: hóa đơn HỦY từ kênh CŨ (mirror)
    match cross-channel qua shipping_code KHÔNG được lật invoice_status=2 lên đơn
    thật của kênh MỚI."""

    def setUp(self):
        from apps.channels_core.models import Channel
        from apps.customers.models import Customer
        from apps.orders.models import Order

        self.kv_new = KiotVietChannel.objects.create(
            name="KV New", branch_id="231174", branch_ids=["231174"],
            client_id="cid-n", client_secret="sec-n", retailer="ret-n", old=False,
        )
        self.kv_old = KiotVietChannel.objects.create(
            name="KV Old", branch_id="415922", branch_ids=["415922"],
            client_id="cid-o", client_secret="sec-o", retailer="ret-o", old=True,
        )
        channel = Channel.objects.create(code="guard-ch", name="Guard", owner="kiotviet")
        customer = Customer.objects.create(
            full_name="Guard User", phone_raw="0909000333", phone_normalized="0909000333"
        )
        # Đơn THẬT thuộc kênh MỚI, đã có shipping_code
        self.order = Order.objects.create(
            channel=channel,
            kiotviet_channel=self.kv_new,
            customer=customer,
            invoice_code="HD-NEW-1",
            shipping_code="SHIP-GUARD-1",
            status=3,
            invoice_status=1,
        )

    def test_cancelled_old_mirror_does_not_flip_new_channel_order(self):
        cancelled_mirror = {
            "id": 555001,
            "code": "HD-OLD-MIRROR-1",
            "status": 2,  # đã hủy bên kênh cũ
            "invoiceDelivery": {"deliveryCode": "SHIP-GUARD-1"},
        }
        outcome = ImportInvoicesService.upsert_invoice(
            invoice=cancelled_mirror, kv_channel=self.kv_old, dry_run=False
        )
        self.assertEqual(outcome, "skipped")
        self.order.refresh_from_db()
        # Đơn thật của kênh mới giữ nguyên trạng thái hoàn thành.
        self.assertEqual(self.order.invoice_status, 1)
        self.assertEqual(self.order.status, 3)

    def test_cancelled_invoice_of_same_channel_updates_own_order(self):
        from apps.orders.models import Order

        own_order = Order.objects.create(
            channel=self.order.channel,
            kiotviet_channel=self.kv_old,
            customer=self.order.customer,
            invoice_code="HD-OLD-2",
            invoice_id=555002,
            shipping_code="",
            status=1,
            invoice_status=1,
        )
        cancelled = {"id": 555002, "code": "HD-OLD-2", "status": 2}
        outcome = ImportInvoicesService.upsert_invoice(
            invoice=cancelled, kv_channel=self.kv_old, dry_run=False
        )
        self.assertEqual(outcome, "updated")
        own_order.refresh_from_db()
        self.assertEqual(own_order.invoice_status, 2)
