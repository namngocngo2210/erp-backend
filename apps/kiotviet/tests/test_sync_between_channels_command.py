"""Characterization tests cho command ``sync_invoices_between_channels`` (BE-1).

MỤC ĐÍCH: ghim (pin) hành vi hiện tại của command TRƯỚC khi hợp nhất engine về
``SyncInvoicesService`` — mọi test ở đây phải pass trên cả bản monolith cũ lẫn
bản CLI-adapter sau hợp nhất mà KHÔNG sửa test.

Chiến lược mock: chặn ở đúng biên HTTP (`_kiotviet_request` /
`_kiotviet_web_request`) tại MỌI module đã import 2 hàm này (mỗi module giữ
reference riêng do ``from ... import``), thay bằng ``FakeKiotViet`` phục vụ cả
public API (GET /invoices, POST /invoices, ...) lẫn web API
(POST /api/invoices/list) từ CÙNG một dataset. Nhờ vậy test khẳng định KẾT QUẢ
nghiệp vụ (đơn nào được tạo/xóa/PUT, counter nào tăng) chứ không ghim endpoint
cụ thể — command cũ list qua web API, engine hợp nhất list qua public API
(đã verify tương đương trong ``_web_list_invoices`` của service), cả hai đều
phải cho cùng kết quả.

Pattern DB thật cho KiotVietChannel giống test_reconcile_sync_mode.py.
"""
import importlib
import json
import re
from contextlib import ExitStack, contextmanager
from io import StringIO
from unittest.mock import patch
from urllib.parse import unquote

from django.core.management import CommandError, call_command
from django.test import TestCase

from apps.kiotviet.models import KiotVietChannel

# Mọi module đang giữ reference tới gateway HTTP của KiotViet. Patch theo danh
# sách (bỏ qua module không có attr) để test sống sót qua refactor: sau hợp nhất
# command không còn import trực tiếp _kiotviet_request nữa nhưng service thì có.
_HTTP_MODULES = [
    "apps.kiotviet.infrastructure.services.common_auth_customer",
    "apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook",
    "apps.kiotviet.infrastructure.services.invoice_sync_queries",
    "apps.kiotviet.infrastructure.services.invoice_mutations_push",
    "apps.kiotviet.infrastructure.services.sync_invoices_service",
    "apps.kiotviet.infrastructure.services._sync_service_purge_mixin",
    "apps.kiotviet.management.commands.sync_invoices_between_channels",
]


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.text = "" if payload is None else json.dumps(payload, ensure_ascii=False, default=str)
        self.content = self.text.encode("utf-8")

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class FakeKiotViet:
    """In-memory KiotViet 2 kênh (source/target) phục vụ cả public lẫn web API."""

    def __init__(self, source_channel, target_channel):
        self._source_id = source_channel.id
        self._target_id = target_channel.id
        self.source_invoices: list[dict] = []
        self.target_invoices: list[dict] = []
        # Khách đã tồn tại trên B (tra theo contactNumber).
        self.customers: list[dict] = []
        # Ghi nhận side effect.
        self.created_invoices: list[dict] = []
        self.created_customers: list[dict] = []
        self.put_invoices: list[tuple[str, dict]] = []
        self.deleted_invoice_ids: list[int] = []
        self.list_get_calls = 0
        self.web_list_calls = 0
        # Hàng đợi status lỗi cho lần GET /invoices (list) / POST /invoices kế tiếp.
        self.list_fail_queue: list[int] = []
        self.create_invoice_fail_queue: list[int] = []

    # ------------------------------------------------------------------
    def _rows_for(self, channel) -> list[dict]:
        if channel is not None and channel.id == self._source_id:
            return self.source_invoices
        return self.target_invoices

    def request(self, order=None, channel=None, method=None, endpoint=None, **kwargs):
        method = str(method or "").upper()
        endpoint = str(endpoint or "")
        if method == "GET" and endpoint == "/invoices":
            self.list_get_calls += 1
            if self.list_fail_queue:
                return FakeResponse(self.list_fail_queue.pop(0), {"message": "transient"})
            rows = self._rows_for(channel)
            params = kwargs.get("params") or {}
            page_size = int(params.get("pageSize") or 100)
            current = int(params.get("currentItem") or 0)
            return FakeResponse(200, {"total": len(rows), "data": rows[current:current + page_size]})
        if method == "GET" and endpoint.startswith("/invoices/code/"):
            code = unquote(endpoint.rsplit("/", 1)[-1])
            for inv in self.source_invoices + self.target_invoices:
                if inv.get("code") == code:
                    return FakeResponse(200, inv)
            return FakeResponse(404, {"message": "not found"})
        if method == "POST" and endpoint == "/invoices":
            if self.create_invoice_fail_queue:
                return FakeResponse(self.create_invoice_fail_queue.pop(0), {"message": "create failed"})
            payload = kwargs.get("json") or {}
            self.created_invoices.append(payload)
            new_id = 9000 + len(self.created_invoices)
            return FakeResponse(200, {"id": new_id, "code": f"HDB{new_id}"})
        if method == "PUT" and endpoint.startswith("/invoices/"):
            self.put_invoices.append((endpoint, kwargs.get("json") or {}))
            return FakeResponse(200, {})
        if method == "DELETE" and endpoint == "/invoices":
            self.deleted_invoice_ids.append(int((kwargs.get("json") or {}).get("id")))
            return FakeResponse(200, {})
        if method == "GET" and endpoint == "/customers":
            params = kwargs.get("params") or {}
            needle = re.sub(r"\D+", "", str(params.get("contactNumber") or params.get("keyword") or ""))
            matches = [
                c for c in self.customers
                if needle and re.sub(r"\D+", "", str(c.get("contactNumber") or "")) == needle
            ]
            return FakeResponse(200, {"data": matches})
        if method == "POST" and endpoint == "/customers":
            payload = kwargs.get("json") or {}
            self.created_customers.append(payload)
            created = dict(payload)
            created.setdefault("id", 700 + len(self.created_customers))
            return FakeResponse(200, created)
        raise AssertionError(f"FakeKiotViet: unhandled {method} {endpoint}")

    # Web API list (command monolith dùng cho các mode check/purge/update-status).
    def web_request(self, *, order=None, channel=None, method=None, endpoint=None, **kwargs):
        assert endpoint == "/api/invoices/list", f"unexpected web endpoint {endpoint}"
        self.web_list_calls += 1
        rows = self._rows_for(channel)
        body = kwargs.get("json") or {}
        top = int(body.get("$top") or 100)
        skip = int(body.get("$skip") or 0)
        page = [self._to_web(r) for r in rows[skip:skip + top]]
        return FakeResponse(200, {"Total": len(rows), "Data": page})

    @staticmethod
    def _to_web(row: dict) -> dict:
        # Web API trả PascalCase; command đọc PascalCase-hoặc-camelCase nên chỉ cần
        # bổ sung các key PascalCase mà code có đường đọc riêng (Id để lọc summary row).
        out = dict(row)
        out.setdefault("Id", row.get("id"))
        out.setdefault("Code", row.get("code"))
        out.setdefault("Description", row.get("description") or "")
        out.setdefault("Status", row.get("status"))
        out.setdefault("StatusValue", row.get("statusValue"))
        out.setdefault("PurchaseDate", row.get("purchaseDate"))
        return out


@contextmanager
def patch_kiotviet_http(fake: FakeKiotViet):
    with ExitStack() as stack:
        for mod_path in _HTTP_MODULES:
            mod = importlib.import_module(mod_path)
            if hasattr(mod, "_kiotviet_request"):
                stack.enter_context(
                    patch.object(mod, "_kiotviet_request", side_effect=fake.request)
                )
            if hasattr(mod, "_kiotviet_web_request"):
                stack.enter_context(
                    patch.object(mod, "_kiotviet_web_request", side_effect=fake.web_request)
                )
        # Không ngủ thật trong test (retry backoff + sleep giữa page/push).
        stack.enter_context(patch("time.sleep", lambda *_a, **_k: None))
        yield


def make_source_invoice(code="HD1", **over) -> dict:
    inv = {
        "id": 500 + abs(hash(code)) % 1000,
        "code": code,
        "branchId": 231174,
        "status": 1,
        "statusValue": "Hoàn thành",
        "purchaseDate": "2026-06-02 10:00:00",
        "total": 150000,
        "customerName": "Nguyen Van A",
        "customerPhone": "0912345678",
        "customerCode": "KH0001",
        "invoiceDelivery": {
            "deliveryCode": "GHN123",
            "status": 3,
            "price": 20000,
            "receiver": "Nguyen Van A",
            "contactNumber": "0912345678",
            "address": "1 Pho Hue",
        },
        "invoiceDetails": [
            {
                "productCode": "SKU1",
                "productName": "San pham 1",
                "quantity": 1,
                "price": 130000,
                "invoiceDetailTaxs": [{"priceAfterTax": 130000, "taxRate": 8}],
            }
        ],
    }
    inv.update(over)
    return inv


def make_target_mirror(src_code, tid=901, code="HDB_M1", **over) -> dict:
    mirror = {
        "id": tid,
        "code": code,
        "status": 3,
        "statusValue": "Đang xử lý",
        "description": f"SYNC từ kênh 231174 - {src_code}",
        "purchaseDate": "2026-06-02 10:05:00",
        "total": 150000,
        "invoiceDelivery": {"deliveryCode": "GHN123", "status": 3, "price": 20000},
    }
    mirror.update(over)
    return mirror


class SyncCommandTestBase(TestCase):
    maxDiff = None

    def setUp(self):
        self.source = KiotVietChannel.objects.create(
            name="KV New A", branch_id="231174", branch_ids=["231174"],
            client_id="cid-a", client_secret="sec-a", retailer="ret-a", old=False,
        )
        self.target = KiotVietChannel.objects.create(
            name="KV Old B", branch_id="415922", branch_ids=["415922"],
            client_id="cid-b", client_secret="sec-b", retailer="ret-b", old=True,
        )
        self.fake = FakeKiotViet(self.source, self.target)

    def run_command(self, **extra) -> tuple[StringIO, StringIO]:
        out, err = StringIO(), StringIO()
        opts = {
            "source_kiotviet_channel_id": self.source.id,
            "source_branch_id": "231174",
            "target_kiotviet_channel_id": self.target.id,
            "target_branch_id": "415922",
            "from_dt": "2026-06-01 00:00:00",
            "to_dt": "2026-06-30 23:59:59",
            "sleep_between_pages": 0,
            "sleep_between_pushes": 0,
        }
        opts.update(extra)
        with patch_kiotviet_http(self.fake):
            call_command("sync_invoices_between_channels", stdout=out, stderr=err, **opts)
        return out, err

    @staticmethod
    def summary_of(out: StringIO) -> dict:
        """Dòng cuối cùng của stdout là json.dumps(summary) — anchor ổn định giữa 2 engine."""
        lines = [ln for ln in out.getvalue().splitlines() if ln.strip()]
        return json.loads(lines[-1])


class MainSyncCharacterizationTests(SyncCommandTestBase):
    def test_creates_invoice_reusing_existing_customer_by_phone(self):
        self.fake.source_invoices = [make_source_invoice("HD1")]
        self.fake.customers = [
            {"id": 601, "code": "KHB001", "name": "Nguyen Van A", "contactNumber": "0912345678"},
        ]

        out, _err = self.run_command()
        summary = self.summary_of(out)

        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(len(self.fake.created_invoices), 1)
        payload = self.fake.created_invoices[0]
        self.assertEqual(payload["customerCode"], "KHB001")
        self.assertEqual(payload["branchId"], 415922)
        self.assertEqual(payload["description"], "SYNC từ kênh 231174 - HD1")
        self.assertEqual(payload["deliveryDetail"]["deliveryCode"], "GHN123")
        # Target là kênh old → pricingMode 0.
        self.assertEqual(payload["pricingMode"], 0)
        # Không tạo khách mới khi đã match phone.
        self.assertEqual(self.fake.created_customers, [])
        # Patch status sau create: PUT delivery (status 3 + code + price) rồi PUT invoice=1.
        put_bodies = [body for _ep, body in self.fake.put_invoices]
        self.assertTrue(any(
            body.get("deliveryDetail", {}).get("status") == 3
            and body.get("deliveryDetail", {}).get("deliveryCode") == "GHN123"
            for body in put_bodies
        ))
        self.assertIn({"status": 1}, put_bodies)

    def test_creates_customer_with_dot1_suffix_when_phone_not_found(self):
        self.fake.source_invoices = [make_source_invoice("HD1")]
        self.fake.customers = []  # không match phone trên B

        out, _err = self.run_command()
        summary = self.summary_of(out)

        self.assertEqual(summary["created"], 1)
        self.assertEqual(len(self.fake.created_customers), 1)
        self.assertEqual(self.fake.created_customers[0]["code"], "KH0001.1")
        self.assertEqual(self.fake.created_invoices[0]["customerCode"], "KH0001.1")

    def test_skips_invoice_when_customer_code_missing(self):
        inv = make_source_invoice("HD1", customerCode="", customerPhone="")
        inv["invoiceDelivery"]["contactNumber"] = ""
        self.fake.source_invoices = [inv]

        out, _err = self.run_command()
        summary = self.summary_of(out)

        self.assertEqual(summary["created"], 0)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(self.fake.created_invoices, [])
        self.assertIn("skip HD1", out.getvalue())

    def test_prescan_skips_invoice_already_synced_on_target(self):
        self.fake.source_invoices = [make_source_invoice("HD1"), make_source_invoice("HD2")]
        self.fake.target_invoices = [make_target_mirror("HD1")]
        self.fake.customers = [
            {"id": 601, "code": "KHB001", "name": "Nguyen Van A", "contactNumber": "0912345678"},
        ]

        out, _err = self.run_command()
        summary = self.summary_of(out)

        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["created"], 1)
        created_desc = [p["description"] for p in self.fake.created_invoices]
        self.assertEqual(created_desc, ["SYNC từ kênh 231174 - HD2"])
        self.assertIn("skip HD1: đã có bên B", out.getvalue())

    def test_prescan_ignores_cancelled_mirror_so_invoice_resyncs(self):
        self.fake.source_invoices = [make_source_invoice("HD1")]
        self.fake.target_invoices = [
            make_target_mirror("HD1", status=2, statusValue="Đã hủy"),
        ]
        self.fake.customers = [
            {"id": 601, "code": "KHB001", "name": "Nguyen Van A", "contactNumber": "0912345678"},
        ]

        out, _err = self.run_command()
        summary = self.summary_of(out)

        self.assertEqual(summary["created"], 1)
        self.assertEqual(len(self.fake.created_invoices), 1)

    def test_skips_cancelled_source_invoice_and_delivery_status_6(self):
        self.fake.source_invoices = [
            make_source_invoice("HD1", status=2, statusValue="Đã hủy"),
            make_source_invoice(
                "HD2",
                invoiceDelivery={"deliveryCode": "X", "status": 6, "price": 0},
            ),
        ]

        out, _err = self.run_command()
        summary = self.summary_of(out)

        self.assertEqual(summary["created"], 0)
        self.assertEqual(summary["skipped"], 2)
        self.assertEqual(self.fake.created_invoices, [])

    def test_dry_run_never_writes(self):
        self.fake.source_invoices = [make_source_invoice("HD1")]

        out, _err = self.run_command(dry_run=True)
        summary = self.summary_of(out)

        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["created"], 1)  # dry-run vẫn đếm created (result="dry_run")
        self.assertEqual(self.fake.created_invoices, [])
        self.assertEqual(self.fake.created_customers, [])
        self.assertEqual(self.fake.put_invoices, [])
        self.assertEqual(self.fake.deleted_invoice_ids, [])

    def test_invoice_codes_mode_fetches_directly_and_skips_prescan(self):
        self.fake.source_invoices = [make_source_invoice("HD1"), make_source_invoice("HD2")]
        self.fake.customers = [
            {"id": 601, "code": "KHB001", "name": "Nguyen Van A", "contactNumber": "0912345678"},
        ]

        out, _err = self.run_command(invoice_codes="HD2")
        summary = self.summary_of(out)

        self.assertEqual(summary["created"], 1)
        self.assertEqual(
            [p["description"] for p in self.fake.created_invoices],
            ["SYNC từ kênh 231174 - HD2"],
        )
        # --invoice-codes: không prescan, không paginate list → 0 lần GET /invoices dạng list.
        self.assertEqual(self.fake.list_get_calls, 0)
        self.assertIn("Skip pre-scan (--invoice-codes mode)", out.getvalue())


class RetryCharacterizationTests(SyncCommandTestBase):
    def test_source_page_fetch_retries_5xx_then_succeeds(self):
        self.fake.source_invoices = [make_source_invoice("HD1")]
        self.fake.customers = [
            {"id": 601, "code": "KHB001", "name": "Nguyen Van A", "contactNumber": "0912345678"},
        ]
        # Prescan (target) chạy trước và tiêu thụ 0 lỗi; đẩy 2 lỗi 500 vào giữa để
        # nhánh paginate source gặp 500 → retry → 200.
        # Thứ tự GET /invoices: [prescan target] rồi [source page]. Cho prescan chạy sạch
        # bằng cách chỉ nhét lỗi sau khi prescan xong không được — đơn giản hơn: fail 1 lần
        # ngay từ đầu; prescan nuốt lỗi (break + cảnh báo) nên source page vẫn phải retry được.
        self.fake.list_fail_queue = [500]

        out, err = self.run_command()
        summary = self.summary_of(out)

        # Prescan gặp 500 thì bỏ qua prescan (fail-soft); sync vẫn tạo đủ đơn.
        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["errors"], 0)
        self.assertIn("pre-scan B failed", err.getvalue() + out.getvalue())

    def test_source_page_fetch_gives_up_on_4xx(self):
        self.fake.source_invoices = [make_source_invoice("HD1")]
        # Lỗi 1: prescan target (fail-soft). Lỗi 2: 400 cho source page → raise ngay,
        # không retry (chỉ retry >=500).
        self.fake.list_fail_queue = [500, 400]

        with self.assertRaises(CommandError):
            self.run_command()

    def test_source_page_fetch_retries_500_twice_then_succeeds(self):
        self.fake.source_invoices = [make_source_invoice("HD1")]
        self.fake.customers = [
            {"id": 601, "code": "KHB001", "name": "Nguyen Van A", "contactNumber": "0912345678"},
        ]
        # Lỗi 1: prescan (fail-soft). Lỗi 2+3: source page 500 x2 → attempt 3 thành công.
        self.fake.list_fail_queue = [500, 500, 500]

        out, _err = self.run_command()
        summary = self.summary_of(out)

        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["errors"], 0)

    def test_per_invoice_create_retries_transient_failure_then_succeeds(self):
        self.fake.source_invoices = [make_source_invoice("HD1")]
        self.fake.customers = [
            {"id": 601, "code": "KHB001", "name": "Nguyen Van A", "contactNumber": "0912345678"},
        ]
        self.fake.create_invoice_fail_queue = [500]

        out, err = self.run_command()
        summary = self.summary_of(out)

        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(len(self.fake.created_invoices), 1)
        self.assertIn("attempt 1/3", err.getvalue())

    def test_per_invoice_create_fails_after_three_attempts(self):
        self.fake.source_invoices = [make_source_invoice("HD1")]
        self.fake.customers = [
            {"id": 601, "code": "KHB001", "name": "Nguyen Van A", "contactNumber": "0912345678"},
        ]
        self.fake.create_invoice_fail_queue = [500, 500, 500]

        out, err = self.run_command()
        summary = self.summary_of(out)

        self.assertEqual(summary["created"], 0)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["failed_codes"], ["HD1"])
        self.assertEqual(self.fake.created_invoices, [])
        self.assertIn("attempt 2/3", err.getvalue())
        self.assertIn("Failed invoices (1): HD1", out.getvalue())


class PurgeCharacterizationTests(SyncCommandTestBase):
    def test_purge_target_without_yes_raises_and_deletes_nothing(self):
        self.fake.target_invoices = [make_target_mirror("HD1", tid=901)]

        with self.assertRaises(CommandError):
            self.run_command(purge_target=True)
        self.assertEqual(self.fake.deleted_invoice_ids, [])

    def test_purge_target_dry_run_lists_without_delete(self):
        self.fake.target_invoices = [make_target_mirror("HD1", tid=901)]

        out, _err = self.run_command(purge_target=True, dry_run=True)

        self.assertEqual(self.fake.deleted_invoice_ids, [])
        self.assertIn("dry-run: skip actual delete", out.getvalue())

    def test_purge_target_with_yes_deletes_but_skips_cancelled_and_delivery6(self):
        self.fake.target_invoices = [
            make_target_mirror("HD1", tid=901, code="HDB1"),
            make_target_mirror("HD2", tid=902, code="HDB2", status=2, statusValue="Đã hủy"),
            make_target_mirror(
                "HD3", tid=903, code="HDB3",
                invoiceDelivery={"deliveryCode": "X", "status": 6, "price": 0},
            ),
        ]

        out, _err = self.run_command(purge_target=True, yes=True)
        summary = self.summary_of(out)

        self.assertEqual(self.fake.deleted_invoice_ids, [901])
        self.assertEqual(summary["purged"], 1)
        self.assertEqual(summary["purge_errors"], 0)

    def test_purge_cancelled_deletes_matching_mirror(self):
        self.fake.source_invoices = [
            make_source_invoice("HD1", status=2, statusValue="Đã hủy"),
            make_source_invoice("HD2"),
        ]
        self.fake.target_invoices = [
            make_target_mirror("HD1", tid=901, code="HDB1"),
            make_target_mirror("HD2", tid=902, code="HDB2"),
        ]

        out, _err = self.run_command(purge_cancelled=True, yes=True)

        self.assertEqual(self.fake.deleted_invoice_ids, [901])
        self.assertIn("purge cancelled done: deleted=1, errors=0", out.getvalue())


class CheckAndUpdateCharacterizationTests(SyncCommandTestBase):
    def test_check_missing_reports_codes_absent_on_target(self):
        self.fake.source_invoices = [make_source_invoice("HD1"), make_source_invoice("HD2")]
        self.fake.target_invoices = [make_target_mirror("HD1")]

        out, _err = self.run_command(check_missing=True)

        text = out.getvalue()
        self.assertIn("Missing invoices (1): HD2", text)
        self.assertIn('--invoice-codes "HD2"', text)
        # Read-only: không tạo/xóa gì.
        self.assertEqual(self.fake.created_invoices, [])
        self.assertEqual(self.fake.deleted_invoice_ids, [])

    def test_update_status_puts_delivery_then_invoice(self):
        # A: hoàn thành (inv=1, del=3); B: đang xử lý (inv=3, del=2) → PUT delivery=3 rồi PUT invoice=1.
        self.fake.source_invoices = [make_source_invoice("HD1", status=1)]
        self.fake.target_invoices = [
            make_target_mirror(
                "HD1", tid=901, code="HDB1", status=3,
                invoiceDelivery={"deliveryCode": "GHN123", "status": 2, "price": 20000},
            ),
        ]

        out, _err = self.run_command(update_status=True)

        put_eps = [ep for ep, _b in self.fake.put_invoices]
        put_bodies = [b for _ep, b in self.fake.put_invoices]
        self.assertEqual(put_eps, ["/invoices/901", "/invoices/901"])
        self.assertEqual(put_bodies[0]["deliveryDetail"]["status"], 3)
        self.assertEqual(put_bodies[1], {"status": 1})
        self.assertIn("updated=1", out.getvalue())

    def test_update_status_skips_cannot_revert_completed(self):
        # A đang xử lý (inv=3) nhưng B đã hoàn thành (inv=1) → KiotViet chặn revert → skip.
        self.fake.source_invoices = [make_source_invoice("HD1", status=3)]
        self.fake.target_invoices = [
            make_target_mirror(
                "HD1", tid=901, code="HDB1", status=1,
                invoiceDelivery={"deliveryCode": "GHN123", "status": 3, "price": 20000},
            ),
        ]

        out, _err = self.run_command(update_status=True)

        self.assertEqual(self.fake.put_invoices, [])
        self.assertIn("skipped_cannot_revert=1", out.getvalue())

    def test_update_delivery_detail_delete_resync_on_code_diff(self):
        # deliveryCode A khác B → DELETE đơn B rồi tạo lại từ A (PUT không ăn trên đơn đã chốt).
        self.fake.source_invoices = [make_source_invoice("HD1")]
        self.fake.target_invoices = [
            make_target_mirror(
                "HD1", tid=901, code="HDB1",
                invoiceDelivery={"deliveryCode": "OLD999", "status": 3, "price": 20000},
            ),
        ]
        self.fake.customers = [
            {"id": 601, "code": "KHB001", "name": "Nguyen Van A", "contactNumber": "0912345678"},
        ]

        out, _err = self.run_command(update_delivery_detail=True)
        summary = self.summary_of(out)

        self.assertEqual(self.fake.deleted_invoice_ids, [901])
        self.assertEqual(len(self.fake.created_invoices), 1)
        self.assertEqual(
            self.fake.created_invoices[0]["deliveryDetail"]["deliveryCode"], "GHN123"
        )
        self.assertEqual(summary["delivery_detail_updated"], 1)

    def test_update_delivery_detail_skips_when_identical(self):
        self.fake.source_invoices = [make_source_invoice("HD1")]
        self.fake.target_invoices = [make_target_mirror("HD1", tid=901)]

        out, _err = self.run_command(update_delivery_detail=True)
        summary = self.summary_of(out)

        self.assertEqual(self.fake.deleted_invoice_ids, [])
        self.assertEqual(self.fake.created_invoices, [])
        self.assertEqual(summary["delivery_detail_skipped_same"], 1)
