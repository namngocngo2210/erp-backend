"""Tests cho các hành vi CLI được CHUẨN HÓA CÓ CHỦ ĐÍCH khi hợp nhất engine (BE-1).

Khác với test_sync_between_channels_command.py (characterization — hành vi giữ
nguyên qua hợp nhất), file này ghim các điểm mà bản monolith cũ và engine chung
KHÁC nhau, và quyết định hợp nhất đã chọn phía service — mỗi test ghi rõ WHY:

1. Cancelled numeric = 2, KHÔNG phải 5 — bản monolith (revert bc2dd1e5 về snapshot
   12/05) còn giữ ``== 5``; e59650dc (28/05) đã chốt mapping public API:
   2 = "Đã hủy", 5 = "Không giao được" ở CẢ command lẫn service. ``== 5`` của
   monolith là regression do revert nguyên snapshot, không phải chủ đích.
2. --update-delivery-detail chỉ lệch GIÁ ship → PUT giá 1 lần (980e8602) thay vì
   delete+resync: delete+resync không sửa được giá (B tự suy giá theo deliveryCode)
   và phá payments của đơn B.
3. --check-price chỉ so ``total`` — builder chung sau a782572d giữ giảm giá theo
   dòng nên discount/discountAfterTax của A và B cố tình khác nhau dù total khớp;
   so discountAfterTax (như monolith) sinh lệch giả.
4. --purge-cancelled bỏ qua mirror B đã hủy sẵn — KiotViet DELETE = hủy mềm, hủy
   lại đơn đã hủy trả 420 gây noise lặp (53 lỗi/đêm trong log reconcile 21h).
"""
from apps.kiotviet.tests.test_sync_between_channels_command import (
    SyncCommandTestBase,
    make_source_invoice,
    make_target_mirror,
)


class CancelledStatusMappingTests(SyncCommandTestBase):
    def test_status_5_khong_giao_duoc_is_synced_not_skipped(self):
        # status=5 = "Không giao được" — monolith cũ nhầm là "Đã hủy" và bỏ qua;
        # engine hợp nhất sync bình thường (e59650dc).
        self.fake.source_invoices = [
            make_source_invoice("HD1", status=5, statusValue="Không giao được"),
        ]
        self.fake.customers = [
            {"id": 601, "code": "KHB001", "name": "Nguyen Van A", "contactNumber": "0912345678"},
        ]

        out, _err = self.run_command()
        summary = self.summary_of(out)

        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["skipped"], 0)
        self.assertEqual(len(self.fake.created_invoices), 1)

    def test_status_2_numeric_only_is_skipped_as_cancelled(self):
        # status=2 không kèm statusValue (một số response list rút gọn) vẫn phải bị
        # coi là đã hủy — monolith cũ chỉ bắt ==5 nên sẽ sync nhầm đơn hủy.
        inv = make_source_invoice("HD1", status=2)
        inv.pop("statusValue", None)
        self.fake.source_invoices = [inv]

        out, _err = self.run_command()
        summary = self.summary_of(out)

        self.assertEqual(summary["created"], 0)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(self.fake.created_invoices, [])


class DeliveryDetailPriceOnlyTests(SyncCommandTestBase):
    def test_price_only_diff_puts_once_instead_of_delete_resync(self):
        # code + delivery status giống nhau, chỉ lệch giá ship → PUT giá 1 lần,
        # KHÔNG delete+resync (monolith cũ delete+resync mọi diff — phá payments
        # mà không sửa được giá).
        self.fake.source_invoices = [make_source_invoice("HD1")]  # ship 20000
        self.fake.target_invoices = [
            make_target_mirror(
                "HD1", tid=901, code="HDB1",
                invoiceDelivery={"deliveryCode": "GHN123", "status": 3, "price": 15000},
            ),
        ]

        out, _err = self.run_command(update_delivery_detail=True)
        summary = self.summary_of(out)

        self.assertEqual(self.fake.deleted_invoice_ids, [])
        self.assertEqual(self.fake.created_invoices, [])
        price_puts = [
            (ep, body) for ep, body in self.fake.put_invoices
            if "deliveryDetail" in body and "price" in body["deliveryDetail"]
        ]
        self.assertEqual(len(price_puts), 1)
        self.assertEqual(price_puts[0][1]["deliveryDetail"]["price"], 20000)
        self.assertEqual(summary["delivery_detail_updated"], 1)


class CheckPriceTotalOnlyTests(SyncCommandTestBase):
    def test_discount_after_tax_diff_alone_is_not_flagged(self):
        # total khớp nhưng discountAfterTax khác → KHÔNG lệch (a782572d: A tách giảm
        # giá dòng/header, B gộp header — các field discount cố tình khác nhau).
        self.fake.source_invoices = [
            make_source_invoice("HD1", total=150000, discountAfterTax=5000),
        ]
        self.fake.target_invoices = [
            make_target_mirror("HD1", total=150000, discountAfterTax=0),
        ]

        out, _err = self.run_command(check_price=True)

        text = out.getvalue()
        self.assertIn("Không có đơn nào lệch.", text)
        self.assertIn("matched_same=1", text)

    def test_total_diff_is_still_flagged(self):
        self.fake.source_invoices = [make_source_invoice("HD1", total=150000)]
        self.fake.target_invoices = [make_target_mirror("HD1", total=149000)]

        out, _err = self.run_command(check_price=True)

        self.assertIn("mismatch=1", out.getvalue())
        self.assertIn("[total]", out.getvalue())


class PurgeCancelledSkipsCancelledMirrorTests(SyncCommandTestBase):
    def test_already_cancelled_mirror_is_not_deleted_again(self):
        # B đã hủy sẵn = đã ở trạng thái đích; hủy lại trả 420 → bỏ qua (hết noise
        # lặp mỗi đêm). Monolith cũ vẫn cố xóa lại.
        self.fake.source_invoices = [
            make_source_invoice("HD1", status=2, statusValue="Đã hủy"),
        ]
        self.fake.target_invoices = [
            make_target_mirror("HD1", tid=901, code="HDB1", status=2, statusValue="Đã hủy"),
        ]

        out, _err = self.run_command(purge_cancelled=True, yes=True)

        self.assertEqual(self.fake.deleted_invoice_ids, [])
        self.assertIn("Không cần xóa gì.", out.getvalue())
