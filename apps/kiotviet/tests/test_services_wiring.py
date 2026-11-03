"""Smoke tests cho wiring command → service sau WP8.1 (move business logic
khỏi management command).

Xác nhận:
- import_kiotviet_invoices chỉ delegate sang service (parse args đúng, option truyền đủ).
- Celery task helpers gọi method CÔNG KHAI của ImportInvoicesService.
- Service chạy standalone (không cần BaseCommand) với output mặc định.

Lưu ý: sync_invoices_between_channels đã hợp nhất về SyncInvoicesService (BE-1);
hành vi CLI được ghim bởi characterization tests ở
test_sync_between_channels_command.py. Ở đây chỉ khóa wiring CLI → engine
(SyncCliEngineTests). Cờ --legacy + monolith cũ đã xoá 2026-09-23 (audit C2).
"""
import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command, get_commands
from django.core.management.base import CommandError
from django.test import TestCase

from apps.kiotviet.infrastructure.services.import_invoices_service import ImportInvoicesService
from apps.kiotviet.infrastructure.services.sync_invoices_service import SyncInvoicesService
from apps.kiotviet.models import KiotVietChannel


class _MockResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        if payload is None:
            self.content = b""
            self.text = ""
        else:
            self.text = json.dumps(payload, ensure_ascii=False)
            self.content = self.text.encode("utf-8")

    def json(self):
        return self._payload


class ImportCommandDelegationTests(TestCase):
    """import_kiotviet_invoices = transport mỏng: parse args rồi gọi service.run."""

    @patch.object(ImportInvoicesService, "run")
    def test_command_delegates_parsed_options_to_service_run(self, mock_run):
        call_command(
            "import_kiotviet_invoices",
            "--channel-id", "7",
            "--branch-ids", "231174,231175",
            "--dry-run",
            "--from", "2026-04-01",
            stdout=StringIO(),
        )
        mock_run.assert_called_once()
        options = mock_run.call_args.kwargs
        self.assertEqual(options["channel_id"], 7)
        self.assertEqual(options["branch_ids"], "231174,231175")
        self.assertTrue(options["dry_run"])
        self.assertEqual(options["from_dt"], "2026-04-01")

    def test_service_run_unknown_channel_raises_command_error(self):
        service = ImportInvoicesService(stdout=StringIO(), stderr=StringIO())
        with self.assertRaises(CommandError):
            service.run(channel_id=999999, branch_ids="231174")


class SyncCliEngineTests(TestCase):
    """Khóa wiring của sync_invoices_between_channels → SyncInvoicesService.run."""

    _BASE_ARGS = [
        "sync_invoices_between_channels",
        "--source-kiotviet-channel-id", "1",
        "--source-branch-id", "231174",
        "--target-kiotviet-channel-id", "2",
        "--target-branch-id", "415922",
        "--from", "2026-04-01 00:00:00",
        "--to", "2026-04-09 23:59:59",
    ]

    @patch.object(SyncInvoicesService, "run")
    def test_cli_delegates_to_unified_service_engine(self, mock_run):
        call_command(*self._BASE_ARGS, "--check-missing", stdout=StringIO())

        mock_run.assert_called_once()
        options = mock_run.call_args.kwargs
        self.assertEqual(options["source_kiotviet_channel_id"], 1)
        self.assertEqual(options["target_kiotviet_channel_id"], 2)
        self.assertEqual(options["source_branch_id"], "231174")
        self.assertEqual(options["target_branch_id"], "415922")
        self.assertEqual(options["from_dt"], "2026-04-01 00:00:00")
        self.assertEqual(options["to_dt"], "2026-04-09 23:59:59")
        self.assertTrue(options["check_missing"])
        # CLI ép exact_mirror=False (hành vi patch-status của command lịch sử,
        # không đọc RECONCILE_DELIVERY_EXACT_MIRROR).
        self.assertIs(options["exact_mirror"], False)
        self.assertNotIn("legacy", options)

    def test_legacy_flag_is_gone(self):
        # Audit C2 (2026-09-23): --legacy + _sync_invoices_legacy đã xoá; cờ lạ phải lỗi.
        with self.assertRaises((CommandError, SystemExit)):
            call_command(*self._BASE_ARGS, "--check-missing", "--legacy", stdout=StringIO())
        self.assertNotIn("_sync_invoices_legacy", get_commands())
        self.assertIn("sync_invoices_between_channels", get_commands())


class ImportServiceStandaloneTests(TestCase):
    """Service chạy độc lập (đường Celery task/webhook) không cần BaseCommand."""

    def setUp(self):
        self.channel = KiotVietChannel.objects.create(
            name="KV Wiring",
            branch_id="231174",
            branch_ids=["231174"],
            client_id="kv-client-id",
            client_secret="kv-client-secret",
            retailer="kv-retailer",
            old=False,
        )

    @patch("apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request")
    def test_import_invoices_public_method_returns_summary(self, mock_request):
        mock_request.return_value = _MockResponse({"data": [], "total": 0})
        service = ImportInvoicesService()  # output mặc định — không truyền stdout
        summary = service.import_invoices(
            kv_channel=self.channel,
            branch_ids=["231174"],
            dry_run=False,
            create_only=True,
            list_missing=False,
            from_dt="2026-04-01 00:00:00",
            to_dt="2026-04-01 23:59:59",
            target_sale_channel_value="",
        )
        self.assertEqual(summary["created"], 0)
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(summary["pages"], 1)
        # đúng window được đẩy xuống API params
        params = mock_request.call_args.kwargs["params"]
        self.assertEqual(params["fromPurchaseDate"], "2026-04-01 00:00:00")
        self.assertEqual(params["toPurchaseDate"], "2026-04-01 23:59:59")

    @patch("apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request")
    def test_import_invoices_pins_stable_sort_for_pagination(self, mock_request):
        # Regression: bỏ orderBy=id/Asc thì /invoices mặc định sort theo modifiedDate —
        # đơn bị sửa trong lúc quét (carrier cập nhật trạng thái giao) làm phân trang
        # currentItem xê dịch → vừa lặp vừa bỏ sót; cron create-only sót cả đơn trong
        # ngày (cùng bài học pre-scan reconcile ~449/15485 đơn).
        mock_request.return_value = _MockResponse({"data": [], "total": 0})
        service = ImportInvoicesService()
        service.import_invoices(
            kv_channel=self.channel,
            branch_ids=["231174"],
            dry_run=False,
            create_only=True,
            list_missing=False,
            from_dt="2026-04-01 00:00:00",
            to_dt="2026-04-01 23:59:59",
            target_sale_channel_value="",
        )
        params = mock_request.call_args.kwargs["params"]
        self.assertEqual(params["orderBy"], "id")
        self.assertEqual(params["orderDirection"], "Asc")

    @patch("apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request")
    def test_update_status_phone_public_method_runs(self, mock_request):
        mock_request.return_value = _MockResponse({"data": [], "total": 0})
        service = ImportInvoicesService(stdout=StringIO(), stderr=StringIO())
        service.update_status_phone(
            kv_channel=self.channel,
            branch_ids=["231174"],
            invoice_codes=[],
            dry_run=False,
            do_status=True,
            do_phone=False,
            from_dt="2026-04-01 00:00:00",
            to_dt="2026-04-30 23:59:59",
        )
        mock_request.assert_called_once()

    def test_upsert_invoice_public_classmethod_dry_run(self):
        # dry_run=True: parse/map nhưng không ghi DB — invoice mới → "created"
        invoice = {
            "id": 991,
            "code": "INV-WIRING-1",
            "customerName": "Wiring Test",
            "customerPhone": "0909000111",
            "invoiceDelivery": {"address": "1 Test St", "price": 10000, "status": 1},
            "invoiceDetails": [
                {"productCode": "SKU-W1", "productName": "P1", "price": 50000, "quantity": 1}
            ],
        }
        outcome = ImportInvoicesService.upsert_invoice(
            invoice=invoice, kv_channel=self.channel, dry_run=True
        )
        self.assertEqual(outcome, "created")
        from apps.orders.models import Order
        self.assertFalse(Order.objects.filter(invoice_code="INV-WIRING-1").exists())


class TaskWiringTests(TestCase):
    """Celery task helpers gọi method công khai của service — không đụng private của command."""

    def setUp(self):
        self.channel = KiotVietChannel.objects.create(
            name="KV Task Wiring",
            branch_id="231174",
            branch_ids=["231174"],
            client_id="kv-client-id",
            client_secret="kv-client-secret",
            retailer="kv-retailer",
            old=False,
        )

    @patch.object(ImportInvoicesService, "import_invoices", return_value={"created": 1, "skipped": 0, "errors": 0})
    def test_import_invoices_for_target_calls_public_service_method(self, mock_import):
        from apps.kiotviet.tasks import _import_invoices_for_target

        result = _import_invoices_for_target(
            channel_id=self.channel.id,
            branch_ids=["231174"],
            create_only=True,
            from_dt="2026-04-01 00:00:00",
            to_dt="2026-04-01 23:59:59",
        )
        self.assertEqual(result["created"], 1)
        mock_import.assert_called_once_with(
            kv_channel=self.channel,
            branch_ids=["231174"],
            dry_run=False,
            create_only=True,
            list_missing=False,
            from_dt="2026-04-01 00:00:00",
            to_dt="2026-04-01 23:59:59",
            target_sale_channel_value="",
        )

    @patch.object(ImportInvoicesService, "update_status_phone")
    def test_update_status_for_target_calls_public_service_method(self, mock_update):
        from apps.kiotviet.tasks import _update_status_for_target

        result = _update_status_for_target(
            channel_id=self.channel.id,
            branch_ids=["231174"],
            from_dt="2026-04-01 00:00:00",
            to_dt="2026-04-30 23:59:59",
        )
        self.assertEqual(result["channel_id"], self.channel.id)
        mock_update.assert_called_once_with(
            kv_channel=self.channel,
            branch_ids=["231174"],
            invoice_codes=[],
            dry_run=False,
            do_status=True,
            do_phone=False,
            from_dt="2026-04-01 00:00:00",
            to_dt="2026-04-30 23:59:59",
        )
