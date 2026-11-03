"""Test cho mode `reconcile_sync` trong SyncInvoicesService.run() (Task 6).

reconcile_sync: prescan B (kèm invoiceStatus) -> với mỗi đơn A, decide_reconcile_action
quyết định create/delete_recreate/skip -> thực thi qua delete_invoice_by_id + _sync_invoice.

Mock ở tầng service (decide_reconcile_action, delete_invoice_by_id, _sync_invoice,
_paginate_source, _prescan_target) để test thuần wiring logic, không cần I/O KiotViet thật.

KiotVietChannel source/target dùng DB thật (django_db, TestCase) — giống pattern
SyncServiceTestBase ở test_sync_service_check_purge.py — thay vì monkeypatch
KiotVietChannel.objects.get, vì run() lookup qua ORM thật và project đã có sẵn pattern
này cho các mode khác.
"""
from unittest.mock import patch

from django.test import TestCase

import apps.kiotviet.infrastructure.services.sync_invoices_service as S
from apps.kiotviet.models import KiotVietChannel

MODULE = "apps.kiotviet.infrastructure.services.sync_invoices_service"


class ReconcileSyncModeTests(TestCase):
    def setUp(self):
        self.source = KiotVietChannel.objects.create(
            name="KV New A", branch_id="231174", branch_ids=["231174"],
            client_id="cid-a", client_secret="sec-a", retailer="ret-a", old=False,
        )
        self.target = KiotVietChannel.objects.create(
            name="KV Old B", branch_id="415922", branch_ids=["415922"],
            client_id="cid-b", client_secret="sec-b", retailer="ret-b", old=True,
        )
        self.service = S.SyncInvoicesService()

    # invoiceDetails tối thiểu để build_invoice_details(...) trả về non-empty (FIX 3 build-safety
    # guard cần nguồn "dựng được" trước khi cho phép delete_recreate).
    _DETAILS = [{"productCode": "SKU1", "quantity": 1, "price": 1000}]

    @staticmethod
    def _fake_paginate(self, *, on_invoice, summary, **kw):
        invoices = [
            {
                "code": "HDA1", "status": 3, "invoiceDelivery": {"status": 2, "price": 20},
                "total": 1000, "invoiceDetails": ReconcileSyncModeTests._DETAILS,
            },
            {
                "code": "HDA2", "status": 3, "invoiceDelivery": {"status": 2, "price": 20},
                "total": 1000, "invoiceDetails": ReconcileSyncModeTests._DETAILS,
            },
            {
                "code": "HDA3", "status": 3, "invoiceDelivery": {"status": 2, "price": 20},
                "total": 1000, "invoiceDetails": ReconcileSyncModeTests._DETAILS,
            },
        ]
        for inv in invoices:
            on_invoice(inv)

    @staticmethod
    def _fake_prescan(self, **kw):
        # HDA1: khớp code nhưng lệch (total 999 != 1000) -> decide sẽ trả delete_recreate.
        # HDA2: chưa có mirror -> decide trả create.
        # HDA3: khớp mọi mặt -> decide trả skip.
        return {
            "HDA1": {"id": 11, "invoiceStatus": 3, "deliveryStatus": 2, "total": 999, "deliveryPrice": 20},
            "HDA3": {"id": 33, "invoiceStatus": 3, "deliveryStatus": 2, "total": 1000, "deliveryPrice": 20},
        }

    def test_reconcile_sync_actions(self):
        """A1 lệch B -> delete+recreate; A2 chưa có B -> create; A3 khớp -> skip."""
        seq = {"HDA1": "delete_recreate", "HDA2": "create", "HDA3": "skip"}
        deleted, created = [], []

        def fake_decide(*, a, b, exact_mirror):
            return seq[a["_code"]]

        def fake_delete(**kw):
            deleted.append(kw["invoice_id"])

        def fake_sync_invoice(self, **kw):
            created.append(kw["source_invoice"]["code"])
            return "created"

        with patch(f"{MODULE}.decide_reconcile_action", side_effect=fake_decide), \
                patch(f"{MODULE}.delete_invoice_by_id", side_effect=fake_delete), \
                patch.object(S.SyncInvoicesService, "_sync_invoice", fake_sync_invoice), \
                patch.object(S.SyncInvoicesService, "_paginate_source", self._fake_paginate), \
                patch.object(S.SyncInvoicesService, "_prescan_target", self._fake_prescan):
            out = self.service.run(
                source_kiotviet_channel_id=self.source.id,
                target_kiotviet_channel_id=self.target.id,
                source_branch_id="231174",
                target_branch_id="415922",
                from_dt="2026-06-01 00:00:00",
                reconcile_sync=True,
                yes=True,
            )

        self.assertEqual(deleted, [11])
        self.assertEqual(created, ["HDA1", "HDA2"])
        self.assertEqual(out["reconcile_recreated"], 1)
        self.assertEqual(out["reconcile_created"], 1)
        self.assertEqual(out["reconcile_skipped"], 1)
        self.assertEqual(out["reconcile_errors"], 0)

    def test_dry_run_counts_but_never_deletes_or_creates(self):
        """dry_run=True: vẫn quyết định + đếm counters nhưng KHÔNG gọi delete/_sync_invoice."""
        seq = {"HDA1": "delete_recreate", "HDA2": "create", "HDA3": "skip"}
        deleted, created = [], []

        def fake_decide(*, a, b, exact_mirror):
            return seq[a["_code"]]

        def fake_delete(**kw):
            deleted.append(kw["invoice_id"])

        def fake_sync_invoice(self, **kw):
            created.append(kw["source_invoice"]["code"])
            return "created"

        with patch(f"{MODULE}.decide_reconcile_action", side_effect=fake_decide), \
                patch(f"{MODULE}.delete_invoice_by_id", side_effect=fake_delete), \
                patch.object(S.SyncInvoicesService, "_sync_invoice", fake_sync_invoice), \
                patch.object(S.SyncInvoicesService, "_paginate_source", self._fake_paginate), \
                patch.object(S.SyncInvoicesService, "_prescan_target", self._fake_prescan):
            out = self.service.run(
                source_kiotviet_channel_id=self.source.id,
                target_kiotviet_channel_id=self.target.id,
                source_branch_id="231174",
                target_branch_id="415922",
                from_dt="2026-06-01 00:00:00",
                reconcile_sync=True,
                dry_run=True,
                yes=True,
            )

        self.assertEqual(deleted, [])
        self.assertEqual(created, [])
        self.assertEqual(out["reconcile_recreated"], 1)
        self.assertEqual(out["reconcile_created"], 1)
        self.assertEqual(out["reconcile_skipped"], 1)
        self.assertEqual(out["reconcile_errors"], 0)

    def test_exact_mirror_flag_passed_through_from_settings(self):
        """self._exact_mirror phải phản ánh settings.RECONCILE_DELIVERY_EXACT_MIRROR."""
        seen_exact_mirror = []

        def fake_decide(*, a, b, exact_mirror):
            seen_exact_mirror.append(exact_mirror)
            return "skip"

        with patch(f"{MODULE}.decide_reconcile_action", side_effect=fake_decide), \
                patch.object(S.SyncInvoicesService, "_paginate_source", self._fake_paginate), \
                patch.object(S.SyncInvoicesService, "_prescan_target", self._fake_prescan), \
                self.settings(RECONCILE_DELIVERY_EXACT_MIRROR=True):
            self.service.run(
                source_kiotviet_channel_id=self.source.id,
                target_kiotviet_channel_id=self.target.id,
                source_branch_id="231174",
                target_branch_id="415922",
                from_dt="2026-06-01 00:00:00",
                reconcile_sync=True,
                yes=True,
            )

        self.assertTrue(self.service._exact_mirror)
        self.assertTrue(all(seen_exact_mirror))

    def test_reconcile_sync_skips_cancelled_source_invoice(self):
        """FIX 1: A đã hủy (invoice_status=2) -> bỏ qua hoàn toàn, KHÔNG delete/create B,
        và decide_reconcile_action không được gọi tới (guard chạy trước)."""
        def fake_paginate(self, *, on_invoice, summary, **kw):
            on_invoice({
                "code": "HDA_CANCELLED", "status": 2,
                "invoiceDelivery": {"status": 2, "price": 20},
                "total": 1000, "invoiceDetails": ReconcileSyncModeTests._DETAILS,
            })

        def fake_prescan(self, **kw):
            return {"HDA_CANCELLED": {"id": 77, "invoiceStatus": 3, "deliveryStatus": 2, "total": 1000, "deliveryPrice": 20}}

        deleted, created = [], []

        def fake_decide(*, a, b, exact_mirror):
            raise AssertionError("decide_reconcile_action should not be called for cancelled A")

        def fake_delete(**kw):
            deleted.append(kw["invoice_id"])

        def fake_sync_invoice(self, **kw):
            created.append(kw["source_invoice"]["code"])
            return "created"

        with patch(f"{MODULE}.decide_reconcile_action", side_effect=fake_decide), \
                patch(f"{MODULE}.delete_invoice_by_id", side_effect=fake_delete), \
                patch.object(S.SyncInvoicesService, "_sync_invoice", fake_sync_invoice), \
                patch.object(S.SyncInvoicesService, "_paginate_source", fake_paginate), \
                patch.object(S.SyncInvoicesService, "_prescan_target", fake_prescan):
            out = self.service.run(
                source_kiotviet_channel_id=self.source.id,
                target_kiotviet_channel_id=self.target.id,
                source_branch_id="231174",
                target_branch_id="415922",
                from_dt="2026-06-01 00:00:00",
                reconcile_sync=True,
                yes=True,
            )

        self.assertEqual(deleted, [])
        self.assertEqual(created, [])
        self.assertEqual(out["reconcile_skipped"], 1)
        self.assertEqual(out["reconcile_created"], 0)
        self.assertEqual(out["reconcile_recreated"], 0)
        self.assertEqual(out["reconcile_errors"], 0)

    def test_delete_recreate_guarded_when_source_not_buildable(self):
        """FIX 3: decide=delete_recreate nhưng A không dựng được invoiceDetails (thiếu) ->
        KHÔNG xóa B (mất dữ liệu vĩnh viễn nếu xóa trước), land ở reconcile_skipped."""
        def fake_paginate(self, *, on_invoice, summary, **kw):
            on_invoice({
                "code": "HDA_NO_DETAILS", "status": 3,
                "invoiceDelivery": {"status": 2, "price": 20}, "total": 1000,
                # invoiceDetails cố ý thiếu -> build_invoice_details(...) trả về [].
            })

        def fake_prescan(self, **kw):
            return {
                "HDA_NO_DETAILS": {
                    "id": 99, "invoiceStatus": 3, "deliveryStatus": 2, "total": 999, "deliveryPrice": 20,
                },
            }

        deleted, created = [], []

        def fake_decide(*, a, b, exact_mirror):
            return "delete_recreate"

        def fake_delete(**kw):
            deleted.append(kw["invoice_id"])

        def fake_sync_invoice(self, **kw):
            created.append(kw["source_invoice"]["code"])
            return "created"

        with patch(f"{MODULE}.decide_reconcile_action", side_effect=fake_decide), \
                patch(f"{MODULE}.delete_invoice_by_id", side_effect=fake_delete), \
                patch.object(S.SyncInvoicesService, "_sync_invoice", fake_sync_invoice), \
                patch.object(S.SyncInvoicesService, "_paginate_source", fake_paginate), \
                patch.object(S.SyncInvoicesService, "_prescan_target", fake_prescan):
            out = self.service.run(
                source_kiotviet_channel_id=self.source.id,
                target_kiotviet_channel_id=self.target.id,
                source_branch_id="231174",
                target_branch_id="415922",
                from_dt="2026-06-01 00:00:00",
                reconcile_sync=True,
                yes=True,
            )

        self.assertEqual(deleted, [], "B must NOT be deleted when source can't be rebuilt")
        self.assertEqual(created, [])
        self.assertEqual(out["reconcile_skipped"], 1)
        self.assertEqual(out["reconcile_recreated"], 0)
        self.assertEqual(out["reconcile_created"], 0)
        self.assertEqual(out["reconcile_errors"], 0)
