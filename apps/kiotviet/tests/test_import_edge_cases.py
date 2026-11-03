"""Regression test cho các bug prod của luồng import/sync KiotViet.

Mỗi class là một sự cố prod riêng — docstring từng class giải thích lý do tồn tại.
"""
from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.kiotviet.infrastructure.services.import_invoices_service import ImportInvoicesService
from apps.kiotviet.management.commands.sync_kiotviet_customers import Command
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order


class ImportCustomerPhoneConflictTests(TestCase):
    """Import KiotViet invoice không được fail khi số điện thoại đã thuộc customer active khác.

    Bug prod: `Invoice failed ... duplicate key value violates unique constraint
    "cust_phone_norm_active_uniq"`. Xảy ra khi invoice match customer theo customerCode,
    nhưng phone trên invoice lại đang thuộc về một customer active KHÁC → khối update ghi đè
    phone_normalized và UPDATE vi phạm partial unique index.
    """

    def setUp(self):
        self.channel = KiotVietChannel.objects.create(
            name="KV Conflict",
            branch_id="231174",
            branch_ids=["231174"],
            client_id="kv-client-id",
            client_secret="kv-client-secret",
            retailer="kv-retailer",
            old=False,
        )

    def test_code_match_does_not_steal_phone_from_other_active_customer(self):
        # Customer B đang giữ SĐT này (active).
        phone_owner = Customer.objects.create(
            code="KV-OWNER", full_name="Owner", phone_normalized="0792223759"
        )
        # Customer A được invoice tham chiếu qua customerCode, phone khác.
        code_match = Customer.objects.create(
            code="KV-TARGET", full_name="Target", phone_normalized="0700000000"
        )

        invoice = {"customerCode": "KV-TARGET", "customerPhone": "0792223759"}

        # Không được raise IntegrityError.
        customer = ImportInvoicesService._upsert_customer(invoice, {})

        # Vẫn resolve đúng customer theo code.
        self.assertEqual(customer.id, code_match.id)
        # KHÔNG cướp phone của owner: owner giữ nguyên phone, active.
        phone_owner.refresh_from_db()
        self.assertEqual(phone_owner.phone_normalized, "0792223759")
        self.assertIsNone(phone_owner.deleted_at)
        # code_match không bị ghi đè phone gây trùng.
        code_match.refresh_from_db()
        self.assertNotEqual(code_match.phone_normalized, "0792223759")
        # Bất biến: đúng 1 customer active giữ SĐT.
        self.assertEqual(
            Customer.objects.filter(
                phone_normalized="0792223759", deleted_at__isnull=True
            ).count(),
            1,
        )

    def test_invoice_imports_despite_phone_owned_by_another_customer(self):
        Customer.objects.create(
            code="KV-OWNER", full_name="Owner", phone_normalized="0977980585"
        )
        Customer.objects.create(
            code="KV-TARGET", full_name="Target", phone_normalized="0700000001"
        )

        invoice = {
            "id": 231788635,
            "code": "HD008329",
            "customerCode": "KV-TARGET",
            "customerName": "Target",
            "customerPhone": "0977980585",
            "total": 150000,
            "invoiceDelivery": {"address": "1 Test St", "price": 15000, "status": 1},
            "invoiceDetails": [
                {"productCode": "SKU-1", "productName": "P1", "price": 150000, "quantity": 1}
            ],
        }

        outcome = ImportInvoicesService.upsert_invoice(
            invoice=invoice, kv_channel=self.channel, dry_run=False
        )

        self.assertEqual(outcome, "created")
        self.assertTrue(Order.objects.filter(invoice_code="HD008329").exists())


class ImportLongSkuSummaryTests(TestCase):
    """Import KiotViet invoice không được fail khi chuỗi tổng hợp SKU vượt 100 ký tự.

    Bug prod (đơn Shopee HDSPE_...): `Invoice failed ... value too long for type
    character varying(100)`. Order.sku giữ chuỗi join SKU của MỌI dòng; đơn sàn nhiều
    dòng → chuỗi tổng hợp dễ vượt 100 dù từng SKU đơn lẻ ngắn.
    """

    def setUp(self):
        self.channel = KiotVietChannel.objects.create(
            name="KV Long SKU",
            branch_id="231174",
            branch_ids=["231174"],
            client_id="kv-client-id",
            client_secret="kv-client-secret",
            retailer="kv-retailer",
            old=False,
        )

    def test_invoice_with_long_joined_sku_imports(self):
        # 8 dòng, mỗi SKU ngắn (<100) nhưng join lại > 100 ký tự.
        details = [
            {
                "productCode": f"SPE-PRODUCT-CODE-{idx:04d}",
                "productName": f"P{idx}",
                "price": 10000,
                "quantity": 1,
            }
            for idx in range(8)
        ]
        joined_len = len(", ".join(d["productCode"] for d in details))
        self.assertGreater(joined_len, 100)  # sanity: chuỗi tổng hợp thật sự > 100

        invoice = {
            "id": 115429242,
            "code": "HDSPE_26051149651HQF",
            "customerName": "Shopee Buyer",
            "customerPhone": "0900000123",
            "total": 80000,
            "invoiceDelivery": {"address": "1 Test St", "price": 15000, "status": 1},
            "invoiceDetails": details,
        }

        outcome = ImportInvoicesService.upsert_invoice(
            invoice=invoice, kv_channel=self.channel, dry_run=False
        )

        self.assertEqual(outcome, "created")
        order = Order.objects.get(invoice_code="HDSPE_26051149651HQF")
        # Toàn bộ SKU được lưu, không bị cắt.
        self.assertEqual(order.items.count(), 8)
        for detail in details:
            self.assertIn(detail["productCode"], order.sku)


class ParseDtTimezoneTests(SimpleTestCase):
    """_parse_dt phải trả datetime aware (+07) — KiotViet trả giờ VN không kèm offset.

    Trước fix: parse_datetime trả naive → gán vào Order.kiotviet_purchase_date/created_at
    sinh RuntimeWarning "naive datetime ... while time zone support is active" và lệch 7h
    khi so sánh/hiển thị theo UTC.
    """

    def test_naive_kiotviet_datetime_becomes_aware_plus7(self):
        result = ImportInvoicesService._parse_dt("2026-05-11 08:11:16")
        self.assertIsNotNone(result)
        self.assertTrue(timezone.is_aware(result))
        # TIME_ZONE = Asia/Bangkok (+07): 08:11:16 local == 01:11:16 UTC.
        self.assertEqual(result.utcoffset().total_seconds(), 7 * 3600)

    def test_iso_naive_datetime_becomes_aware_plus7(self):
        result = ImportInvoicesService._parse_dt("2026-05-11T08:11:16")
        self.assertTrue(timezone.is_aware(result))
        self.assertEqual(result.utcoffset().total_seconds(), 7 * 3600)

    def test_already_aware_datetime_preserved(self):
        result = ImportInvoicesService._parse_dt("2026-05-11T08:11:16+00:00")
        self.assertTrue(timezone.is_aware(result))
        self.assertEqual(result.utcoffset().total_seconds(), 0)

    def test_empty_returns_none(self):
        self.assertIsNone(ImportInvoicesService._parse_dt(""))
        self.assertIsNone(ImportInvoicesService._parse_dt(None))


def _invoice(code: str, *, invoice_id: int, status: int) -> dict:
    return {
        "id": invoice_id,
        "code": code,
        "orderCode": "DH-700",
        "status": status,
        "customerName": "Khach Reattach",
        "customerPhone": "0909777888",
        "invoiceDelivery": {
            "receiver": "Khach Reattach",
            "contactNumber": "0909777888",
            "address": "12 Reattach Street",
            "deliveryCode": "SHIP-700",
            "status": 3,
        },
        "invoiceDetails": [
            {
                "productCode": "SKU-RE-01",
                "productName": "SP Reattach",
                "quantity": 1,
                "price": 100000,
                "subTotal": 100000,
            }
        ],
    }


class ImportReattachedInvoiceTests(TestCase):
    """Import KiotViet không được tạo lại dòng đơn huỷ cho mã hoá đơn ĐÃ reattach.

    Sau khi webhook nối `X` → `X.01` vào đơn cũ, không còn Order nào mang mã `X`. Cron
    import (5'/lần, hoá đơn trong ngày) gặp lại payload huỷ của `X` sẽ tạo dòng đơn huỷ
    MỚI — đúng "đơn trùng" mà reattach muốn diệt (audit 2026-07-14).
    """

    def setUp(self):
        self.sales_channel = Channel.objects.create(code="ch-re", name="Reattach Channel")
        self.customer = Customer.objects.create(
            full_name="Khach Reattach",
            phone_raw="0909777888",
            phone_normalized="0909777888",
        )
        self.kv_channel = KiotVietChannel.objects.create(
            name="KV Reattach",
            branch_id="B7",
            retailer="Retailer RE",
            client_id="client-re",
            client_secret="secret-re",
        )
        # Đơn đã được reattach: giữ mã mới, lưu vết mã cũ.
        self.order = Order.objects.create(
            channel=self.sales_channel,
            kiotviet_channel=self.kv_channel,
            customer=self.customer,
            order_code="DH-700",
            invoice_code="HD-700.01",
            invoice_id=7002,
            shipping_code="SHIP-700",
            status=3,
            invoice_status=1,
            reattached_invoice_codes=["HD-700"],
        )

    def test_cancelled_base_invoice_is_skipped_not_recreated(self):
        outcome = ImportInvoicesService.upsert_invoice(
            invoice=_invoice("HD-700", invoice_id=7001, status=2),
            kv_channel=self.kv_channel,
            dry_run=False,
        )

        self.assertEqual(outcome, "skipped")
        self.assertEqual(
            Order.objects.filter(
                kiotviet_channel=self.kv_channel, deleted_at__isnull=True
            ).count(),
            1,
            "Mã gốc đã reattach không được sinh dòng đơn huỷ trùng",
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.invoice_code, "HD-700.01")
        self.assertEqual(self.order.status, 3, "Đơn kế thừa vẫn sống")
        self.assertEqual(self.order.invoice_status, 1)

    def test_new_invoice_code_still_imports_normally(self):
        """Guard chỉ áp cho mã đã reattach — hoá đơn khác vẫn import bình thường."""
        outcome = ImportInvoicesService.upsert_invoice(
            invoice=_invoice("HD-800", invoice_id=8001, status=1),
            kv_channel=self.kv_channel,
            dry_run=False,
        )

        self.assertEqual(outcome, "created")
        self.assertTrue(
            Order.objects.filter(
                kiotviet_channel=self.kv_channel, invoice_code="HD-800"
            ).exists()
        )


class SyncCustomersPhoneRaceTests(TestCase):
    """sync_kiotviet_customers._upsert_customer phải chịu được race trên phone unique.

    Trước fix: khi lookup không thấy (một tiến trình song song vừa tạo customer cùng SĐT)
    rồi INSERT → vi phạm cust_phone_norm_active_uniq → customer đó bị đánh dấu failed.
    Fix: bắt IntegrityError, re-fetch bản thắng cuộc và coi như "updated".
    """

    def test_create_race_refetches_existing_instead_of_failing(self):
        existing = Customer.objects.create(
            code="KV-1", full_name="Old", phone_normalized="0944444444"
        )

        orig_filter = Customer.objects.filter
        calls = {"n": 0}

        def fake_filter(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                # Giả lập race: lượt lookup đầu không thấy customer.
                return Customer.objects.none()
            return orig_filter(*args, **kwargs)

        kv_customer = {"code": "KV-1", "name": "New", "contactNumber": "0944444444"}

        with mock.patch.object(Customer.objects, "filter", side_effect=fake_filter):
            result = Command._upsert_customer(kv_customer, dry_run=False)

        self.assertEqual(result, "updated")
        # Không tạo bản trùng — vẫn đúng 1 customer active giữ SĐT.
        self.assertEqual(
            Customer.objects.filter(
                phone_normalized="0944444444", deleted_at__isnull=True
            ).count(),
            1,
        )
        self.assertEqual(
            Customer.objects.filter(phone_normalized="0944444444").first().id,
            existing.id,
        )
