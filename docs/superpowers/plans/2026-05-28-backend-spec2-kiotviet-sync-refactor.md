# Backend Spec 2 — KiotViet Sync Refactor & Speedup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hợp nhất command CLI và service KiotViet thành một nguồn duy nhất, loại N+1 DB/API trong vòng lặp sync, và thêm xử lý song song có kiểm soát — **bảo toàn hành vi**.

**Architecture:** Service `sync_invoices_service.py` là nguồn sự thật; command trở thành wrapper mỏng. Pre-load `ChannelKiotvietMapping` + `Customer` theo lô trước vòng lặp; chạy mỗi invoice trong `ThreadPoolExecutor` với worker count cấu hình được; tổng kết qua return value (không shared mutation).

**Tech Stack:** Django 5, Python 3.12, `concurrent.futures`, `tenacity` (tùy chọn cho retry), `requests` (đã có timeout), `unittest.mock` cho characterization tests.

**Spec gốc:** [docs/superpowers/specs/2026-05-28-backend-spec2-kiotviet-sync-refactor-design.md](../specs/2026-05-28-backend-spec2-kiotviet-sync-refactor-design.md)

**Phụ thuộc:** Spec 1 đã merge.

---

## Nguyên tắc thực thi

- **Bảo toàn hành vi tuyệt đối** — payload gửi KiotViet không đổi. Mỗi task có "golden snapshot" hoặc characterization test khóa hành vi.
- Refactor từng bước, chạy test sau mỗi bước.
- Mỗi commit nhỏ, mô tả rõ. Branch phải đúng trước commit.
- Khi đụng tới file 2000–3000 dòng: **không di chuyển hàm hàng loạt trong một commit**. Mỗi commit chỉ extract 1–2 hàm.

---

## Phase A — Bộ phòng vệ (characterization tests)

Mục tiêu: trước khi đụng vào logic, có lưới an toàn.

### Task A1: Golden snapshot payload build

**Files:**
- Test: `backend/apps/kiotviet/tests/test_invoice_payload_golden.py` (tạo)
- Fixture: `backend/apps/kiotviet/tests/fixtures/invoice_input_minimal.json`, `expected_payload_minimal.json` (tạo)

- [ ] **Step 1: Đọc `invoice_payload_builder.py` và xác định hàm entry build payload**

```bash
grep -n "^def \|^class " backend/apps/kiotviet/infrastructure/services/invoice_payload_builder.py | head
```
Mục đích: chọn entry point ổn định (vd `build_target_invoice_payload`) cho snapshot.

- [ ] **Step 2: Dump payload thực với 1 invoice mẫu**

Tạo script tạm `backend/scripts/_dump_payload_sample.py`:
```python
"""Dump current payload for snapshotting. One-off; remove after snapshot is captured."""
import json, sys, os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from apps.kiotviet.infrastructure.services.invoice_payload_builder import build_target_invoice_payload  # adjust

sample_invoice = json.load(open(sys.argv[1]))
ctx = {}  # any required context — fill from real call sites
payload, *_ = build_target_invoice_payload(sample_invoice, **ctx)
print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
```
Chạy với một invoice mẫu (lấy từ test mock có sẵn, không từ DB thật):
```bash
cd backend && python scripts/_dump_payload_sample.py apps/kiotviet/tests/fixtures/invoice_input_minimal.json > apps/kiotviet/tests/fixtures/expected_payload_minimal.json
```

- [ ] **Step 3: Viết golden test**

Create: `backend/apps/kiotviet/tests/test_invoice_payload_golden.py`
```python
import json
import os
from pathlib import Path
from django.test import SimpleTestCase

from apps.kiotviet.infrastructure.services.invoice_payload_builder import (
    build_target_invoice_payload,  # adjust if name differs
)

FIX = Path(__file__).parent / "fixtures"


class InvoicePayloadGoldenTests(SimpleTestCase):
    def test_minimal_invoice_payload_matches_golden(self):
        invoice = json.loads((FIX / "invoice_input_minimal.json").read_text(encoding="utf-8"))
        expected = json.loads((FIX / "expected_payload_minimal.json").read_text(encoding="utf-8"))
        payload, *_ = build_target_invoice_payload(invoice)  # adjust signature
        self.assertEqual(
            json.dumps(payload, sort_keys=True),
            json.dumps(expected, sort_keys=True),
        )
```

- [ ] **Step 4: Xóa script tạm**

```bash
git rm backend/scripts/_dump_payload_sample.py 2>/dev/null || rm backend/scripts/_dump_payload_sample.py
```

- [ ] **Step 5: Chạy test, xác nhận PASS (snapshot khớp)**

```bash
cd backend && python manage.py test apps.kiotviet.tests.test_invoice_payload_golden -v 2
```

- [ ] **Step 6: Commit**

```bash
git add backend/apps/kiotviet/tests/test_invoice_payload_golden.py backend/apps/kiotviet/tests/fixtures/
git commit -m "test(kiotviet): golden snapshot for build_target_invoice_payload"
```

---

### Task A2: Characterization test cho `service.run(--dry-run)`

**Files:**
- Test: `backend/apps/kiotviet/tests/test_sync_service_characterization.py` (tạo)

- [ ] **Step 1: Đọc test có sẵn `test_kiotviet_service_mock.py` để mượn pattern mock**

```bash
grep -n "def test_\|mock\|patch" backend/apps/kiotviet/tests/test_kiotviet_service_mock.py | head -30
```

- [ ] **Step 2: Viết test characterization cho dry-run**

Create: `backend/apps/kiotviet/tests/test_sync_service_characterization.py`
```python
from unittest import mock
from django.test import TestCase

from apps.kiotviet.infrastructure.services.sync_invoices_service import SyncInvoicesService  # adjust


class SyncServiceDryRunTests(TestCase):
    @mock.patch("apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook._kiotviet_request")
    def test_dry_run_returns_summary_without_pushing(self, mock_request):
        # Arrange: mock_request returns a paginated empty invoice list.
        mock_request.return_value.status_code = 200
        mock_request.return_value.json.return_value = {"data": [], "total": 0}
        svc = SyncInvoicesService()
        result = svc.run(dry_run=True, limit=0)  # adjust kw names
        # Assert: no POST calls (only GETs).
        for call in mock_request.call_args_list:
            self.assertNotEqual(call.kwargs.get("method", "GET"), "POST")
        self.assertIn("fetched", result)
```

- [ ] **Step 3: Chạy test, điều chỉnh tên/keyword cho khớp service thật, đến khi PASS**

```bash
cd backend && python manage.py test apps.kiotviet.tests.test_sync_service_characterization -v 2
```

- [ ] **Step 4: Commit**

```bash
git add backend/apps/kiotviet/tests/test_sync_service_characterization.py
git commit -m "test(kiotviet): characterization for SyncInvoicesService.run(dry_run)"
```

---

## Phase B — Hợp nhất command vào service

### Task B1: Đối chiếu signature command vs service

**Files:**
- Read-only: `backend/apps/kiotviet/management/commands/sync_invoices_between_channels.py` (đặc biệt `Command.add_arguments`, `Command.handle`), `sync_invoices_service.py` (`SyncInvoicesService.run`)

- [ ] **Step 1: Xuất danh sách args**

```bash
grep -nE "add_argument|--" backend/apps/kiotviet/management/commands/sync_invoices_between_channels.py | head -60
grep -nE "def run|options\.|kwargs\.get" backend/apps/kiotviet/infrastructure/services/sync_invoices_service.py | head -60
```

- [ ] **Step 2: Lập bảng mapping arg-command ↔ kwarg-service**

Tạo: `docs/superpowers/notes/2026-05-28-kiotviet-sync-arg-mapping.md` với bảng:

| Command CLI flag | Service kwarg | Loại | Default | Ghi chú |
|------------------|---------------|------|---------|---------|
| `--dry-run`      | `dry_run`     | bool | False   |         |
| ... (điền hết)   | ...           |      |         |         |

- [ ] **Step 3: Commit notes**

```bash
git add docs/superpowers/notes/2026-05-28-kiotviet-sync-arg-mapping.md
git commit -m "docs(kiotviet): map CLI args -> service kwargs for refactor"
```

---

### Task B2: Bổ sung kwarg còn thiếu cho service (nếu có)

**Files:**
- Modify: `backend/apps/kiotviet/infrastructure/services/sync_invoices_service.py`
- Test: `backend/apps/kiotviet/tests/test_sync_service_args_coverage.py` (tạo)

- [ ] **Step 1: Với mỗi arg CLI chưa có kwarg tương ứng, viết test khẳng định service nhận nó**

Mẫu:
```python
def test_service_accepts_update_status_flag(self):
    svc = SyncInvoicesService()
    # phải không raise TypeError
    svc.run(dry_run=True, update_status=True)  # tinh chỉnh keyword
```

- [ ] **Step 2: Chạy test → FAIL nếu service chưa nhận**

- [ ] **Step 3: Thêm kwarg với default tương đương hành vi cũ**

Trong `SyncInvoicesService.run`, mở rộng signature/`**options`. KHÔNG thay đổi luồng xử lý ở task này — chỉ chấp nhận và pass-through.

- [ ] **Step 4: Test PASS + golden test (A1) + characterization (A2) vẫn xanh**

```bash
cd backend && python manage.py test apps.kiotviet -v 1
```

- [ ] **Step 5: Commit**

```bash
git add backend/apps/kiotviet/infrastructure/services/sync_invoices_service.py backend/apps/kiotviet/tests/test_sync_service_args_coverage.py
git commit -m "feat(kiotviet): align service.run kwargs with CLI args"
```

---

### Task B3: Biến command thành wrapper

**Files:**
- Modify: `backend/apps/kiotviet/management/commands/sync_invoices_between_channels.py` (toàn bộ `Command.handle`)

- [ ] **Step 1: Tạo nhánh dự phòng (snapshot file cũ)**

```bash
cp backend/apps/kiotviet/management/commands/sync_invoices_between_channels.py /tmp/sync_cmd_backup.py
```

- [ ] **Step 2: Viết lại `Command` thành wrapper**

Thay toàn bộ class `Command` bằng:
```python
from django.core.management.base import BaseCommand
from apps.kiotviet.infrastructure.services.sync_invoices_service import SyncInvoicesService


class Command(BaseCommand):
    help = "Đồng bộ invoice giữa các kênh KiotViet (wrapper gọi SyncInvoicesService)."

    def add_arguments(self, parser):
        # Giữ NGUYÊN danh sách arg cũ (copy y nguyên block hiện tại sang đây).
        ...  # GIỮ ORIGINAL — copy y nguyên

    def handle(self, *args, **options):
        result = SyncInvoicesService().run(**options)
        self.stdout.write(self.style.SUCCESS(str(result)))
```

> Quy tắc: KHÔNG bỏ arg nào, KHÔNG thêm arg mới. Toàn bộ định nghĩa flag giữ y nguyên từ bản cũ.

- [ ] **Step 3: Chạy command với `--help` để xác minh args còn nguyên**

```bash
cd backend && python manage.py sync_invoices_between_channels --help 2>&1 | tail -40
```
Expected: danh sách flag giống hệt trước (so với `/tmp/sync_cmd_backup.py`).

- [ ] **Step 4: Chạy command `--dry-run` end-to-end với KiotViet mock**

Tạo test integration nhỏ:
Create: `backend/apps/kiotviet/tests/test_command_wrapper.py`
```python
from unittest import mock
from io import StringIO
from django.core.management import call_command
from django.test import TestCase


class SyncCommandWrapperTests(TestCase):
    @mock.patch("apps.kiotviet.infrastructure.services.sync_invoices_service.SyncInvoicesService.run")
    def test_command_delegates_to_service(self, mock_run):
        mock_run.return_value = {"fetched": 0}
        out = StringIO()
        call_command("sync_invoices_between_channels", "--dry-run", stdout=out)
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        self.assertTrue(kwargs.get("dry_run"))
```

```bash
cd backend && python manage.py test apps.kiotviet.tests.test_command_wrapper -v 2
```

- [ ] **Step 5: Verify golden + characterization vẫn xanh**

```bash
cd backend && python manage.py test apps.kiotviet -v 1
```

- [ ] **Step 6: Xóa code cũ thừa trong command**

Sau khi wrapper xanh, xóa toàn bộ method `_sync_invoice`, `_process_one`, ... còn trong command (vì đã ở service). Mỗi commit chỉ xóa 1–2 hàm rồi chạy test.

- [ ] **Step 7: Cleanup backup**

```bash
rm /tmp/sync_cmd_backup.py
```

- [ ] **Step 8: Commit**

```bash
git add backend/apps/kiotviet/management/commands/sync_invoices_between_channels.py backend/apps/kiotviet/tests/test_command_wrapper.py
git commit -m "refactor(kiotviet): make sync command a thin wrapper over SyncInvoicesService"
```

---

## Phase C — Pre-loading (loại N+1)

### Task C1: `ChannelMappingCache`

**Files:**
- Create: `backend/apps/kiotviet/infrastructure/services/mapping_cache.py`
- Test: `backend/apps/kiotviet/tests/test_mapping_cache.py`
- Modify (later step): nơi gọi `id_mappers.map_sale_channel_id` trong service

- [ ] **Step 1: Viết test cho cache**

Create: `backend/apps/kiotviet/tests/test_mapping_cache.py`
```python
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.channels_core.models import Channel, ChannelKiotvietMapping  # adjust
from apps.kiotviet.models import KiotVietChannel
from apps.kiotviet.infrastructure.services.mapping_cache import ChannelMappingCache


class ChannelMappingCacheTests(TestCase):
    def setUp(self):
        self.src_kv = KiotVietChannel.objects.create(name="A", retailer="r1", branch_id="1")
        self.tgt_kv = KiotVietChannel.objects.create(name="B", retailer="r2", branch_id="2")
        self.ch = Channel.objects.create(code="X", name="X")
        ChannelKiotvietMapping.objects.create(
            channel=self.ch, kiotviet_channel=self.src_kv, external_sale_channel_id="100"
        )
        ChannelKiotvietMapping.objects.create(
            channel=self.ch, kiotviet_channel=self.tgt_kv, external_sale_channel_id="200"
        )

    def test_single_query_then_lookup(self):
        with CaptureQueriesContext(connection) as ctx:
            cache = ChannelMappingCache.preload(source_kv=self.src_kv, target_kv=self.tgt_kv)
        self.assertLessEqual(len(ctx.captured_queries), 2)

        with CaptureQueriesContext(connection) as ctx2:
            self.assertEqual(cache.map_external_id("100"), "200")
            self.assertIsNone(cache.map_external_id("999"))
        self.assertEqual(len(ctx2.captured_queries), 0)
```

- [ ] **Step 2: Chạy test → FAIL (module chưa tồn tại)**

- [ ] **Step 3: Viết module `mapping_cache.py`**

```python
"""Pre-load channel sale-channel mappings for a sync session."""
from dataclasses import dataclass
from typing import Optional

from apps.channels_core.models import ChannelKiotvietMapping  # adjust import path


@dataclass(frozen=True)
class ChannelMappingCache:
    _by_source_ext: dict[str, str]  # source external_id -> target external_id

    def map_external_id(self, source_ext_id: str) -> Optional[str]:
        if not source_ext_id:
            return None
        return self._by_source_ext.get(str(source_ext_id))

    @classmethod
    def preload(cls, *, source_kv, target_kv) -> "ChannelMappingCache":
        # 1 query: load all source mappings + their channel; another to load target mappings.
        source_rows = list(
            ChannelKiotvietMapping.objects
            .filter(kiotviet_channel=source_kv)
            .values("channel_id", "external_sale_channel_id")
        )
        target_rows = list(
            ChannelKiotvietMapping.objects
            .filter(kiotviet_channel=target_kv)
            .values("channel_id", "external_sale_channel_id")
        )
        channel_to_target = {r["channel_id"]: str(r["external_sale_channel_id"]) for r in target_rows}
        by_src = {
            str(r["external_sale_channel_id"]): channel_to_target[r["channel_id"]]
            for r in source_rows
            if r["channel_id"] in channel_to_target
        }
        return cls(_by_source_ext=by_src)
```

- [ ] **Step 4: Test PASS**

```bash
cd backend && python manage.py test apps.kiotviet.tests.test_mapping_cache -v 2
```

- [ ] **Step 5: Commit**

```bash
git add backend/apps/kiotviet/infrastructure/services/mapping_cache.py backend/apps/kiotviet/tests/test_mapping_cache.py
git commit -m "feat(kiotviet): ChannelMappingCache to preload sale-channel mappings"
```

---

### Task C2: Tích hợp `ChannelMappingCache` vào service

**Files:**
- Modify: `backend/apps/kiotviet/infrastructure/services/sync_invoices_service.py` (nơi gọi `map_sale_channel_id`)

- [ ] **Step 1: Tìm tất cả call site `map_sale_channel_id`**

```bash
grep -n "map_sale_channel_id" backend/apps/kiotviet/infrastructure/services/sync_invoices_service.py
```

- [ ] **Step 2: Thêm preload đầu `run()`**

Trong `SyncInvoicesService.run`, trước vòng lặp xử lý invoice:
```python
from apps.kiotviet.infrastructure.services.mapping_cache import ChannelMappingCache
self._mapping_cache = ChannelMappingCache.preload(source_kv=source_channel, target_kv=target_channel)
```

- [ ] **Step 3: Thay call site dùng cache**

Thay:
```python
mapped = map_sale_channel_id(src_id, source_kv=..., target_kv=...)
```
Bằng:
```python
mapped = self._mapping_cache.map_external_id(src_id)
```

- [ ] **Step 4: Verify golden + characterization vẫn xanh + thêm test query-count**

Append vào `test_sync_service_characterization.py`:
```python
def test_sync_does_not_query_mapping_per_invoice(self):
    # Mock pagination trả 3 invoice mock; sau preload, không query thêm ChannelKiotvietMapping.
    ...  # implement với CaptureQueriesContext + assert ChannelKiotvietMapping query count <= 2
```

- [ ] **Step 5: Commit**

```bash
git add backend/apps/kiotviet/infrastructure/services/sync_invoices_service.py backend/apps/kiotviet/tests/test_sync_service_characterization.py
git commit -m "perf(kiotviet): use ChannelMappingCache to remove per-invoice mapping queries"
```

---

### Task C3: `CustomerResolverCache` (phone → Customer dict)

**Files:**
- Create: `backend/apps/kiotviet/infrastructure/services/customer_cache.py`
- Test: `backend/apps/kiotviet/tests/test_customer_cache.py`
- Modify: `sync_invoices_service.py` (nơi gọi `find_customer_by_phone` cho phần tra cứu — KHÔNG đụng phần tạo mới)

- [ ] **Step 1: Test trước**

Create: `test_customer_cache.py`
```python
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.customers.models import Customer
from apps.kiotviet.infrastructure.services.customer_cache import CustomerResolverCache


class CustomerResolverCacheTests(TestCase):
    def setUp(self):
        for i in range(3):
            Customer.objects.create(name=f"C{i}", phone_normalized=f"0900000{i:03d}")

    def test_preload_one_query(self):
        phones = [f"0900000{i:03d}" for i in range(3)] + ["nonexistent"]
        with CaptureQueriesContext(connection) as ctx:
            cache = CustomerResolverCache.preload(phones)
        self.assertEqual(len(ctx.captured_queries), 1)
        with CaptureQueriesContext(connection) as ctx2:
            self.assertIsNotNone(cache.get("0900000001"))
            self.assertIsNone(cache.get("nonexistent"))
        self.assertEqual(len(ctx2.captured_queries), 0)
```

- [ ] **Step 2: Viết `customer_cache.py`**

```python
from dataclasses import dataclass
from typing import Iterable, Optional

from apps.customers.models import Customer


@dataclass(frozen=True)
class CustomerResolverCache:
    _by_phone: dict[str, Customer]

    def get(self, phone: str) -> Optional[Customer]:
        if not phone:
            return None
        return self._by_phone.get(str(phone))

    @classmethod
    def preload(cls, phones: Iterable[str]) -> "CustomerResolverCache":
        unique = {str(p) for p in phones if p}
        if not unique:
            return cls(_by_phone={})
        qs = Customer.objects.filter(phone_normalized__in=unique)
        return cls(_by_phone={c.phone_normalized: c for c in qs})
```

- [ ] **Step 3: Tích hợp vào service**

Trong `run()`, sau khi đã fetch batch invoice phía A:
```python
phones = [_extract_phone(inv) for inv in batch]
self._customer_cache = CustomerResolverCache.preload(phones)
```
Thay phần "tra cứu tồn tại theo phone" dùng `self._customer_cache.get(phone)`. **Giữ nguyên** phần fallback tạo customer mới qua API KiotViet.

- [ ] **Step 4: Test golden + characterization + query-count**

```bash
cd backend && python manage.py test apps.kiotviet -v 1
```

- [ ] **Step 5: Commit**

```bash
git add backend/apps/kiotviet/infrastructure/services/customer_cache.py backend/apps/kiotviet/tests/test_customer_cache.py backend/apps/kiotviet/infrastructure/services/sync_invoices_service.py
git commit -m "perf(kiotviet): CustomerResolverCache to batch phone -> customer lookup"
```

---

## Phase D — Tách `_sync_invoice` thành các bước có tên

### Task D1: Identify boundaries

- [ ] **Step 1: Đọc `_sync_invoice` hiện tại**

```bash
sed -n '2584,3029p' backend/apps/kiotviet/management/commands/sync_invoices_between_channels.py | wc -l
```

- [ ] **Step 2: Liệt kê các bước logic ra notes**

Cập nhật `docs/superpowers/notes/2026-05-28-kiotviet-sync-arg-mapping.md` thêm section "Sync invoice steps":
- resolve_target_customer
- build_target_payload
- push_invoice
- patch_status_after_create
- record_summary

Cam kết: mỗi bước sẽ tách thành 1 method tên rõ, < ~80 dòng.

- [ ] **Step 3: Commit notes**

```bash
git add docs/superpowers/notes/2026-05-28-kiotviet-sync-arg-mapping.md
git commit -m "docs(kiotviet): identify sync_invoice step boundaries"
```

---

### Task D2: Extract `resolve_target_customer` (đầu tiên, nhỏ nhất)

**Files:**
- Modify: `backend/apps/kiotviet/infrastructure/services/sync_invoices_service.py`

- [ ] **Step 1: Tạo method `_resolve_target_customer_v2` (tên `v2` để chạy song song bản cũ)**

Copy logic resolve customer hiện có sang method mới, thuần & nhận đủ input.

- [ ] **Step 2: Viết unit test cho method mới**

Mock API, assert output (customer_block, ...) khớp expected.

- [ ] **Step 3: Trong `_sync_invoice`, GỌI method mới thay vì inline logic**

- [ ] **Step 4: Golden + characterization + unit test xanh**

- [ ] **Step 5: Commit**

```bash
git commit -am "refactor(kiotviet): extract _resolve_target_customer step"
```

(Lặp lại pattern này cho từng bước: `build_target_payload`, `push_invoice`, `patch_status_after_create`. **Mỗi bước một commit riêng.** Sau khi tất cả tách xong, `_sync_invoice` chỉ còn dispatch.)

---

## Phase E — Song song hóa

### Task E1: Bộ chạy song song có kiểm soát

**Files:**
- Create: `backend/apps/kiotviet/infrastructure/services/parallel_runner.py`
- Test: `backend/apps/kiotviet/tests/test_parallel_runner.py`

- [ ] **Step 1: Test**

```python
from django.test import TestCase
from apps.kiotviet.infrastructure.services.parallel_runner import run_parallel


class ParallelRunnerTests(TestCase):
    def test_runs_all_items_and_collects_results(self):
        def task(x):
            return x * 2
        results = run_parallel(task, range(10), workers=4)
        self.assertEqual(sorted(results), [0, 2, 4, 6, 8, 10, 12, 14, 16, 18])

    def test_collects_exceptions_per_item(self):
        def task(x):
            if x == 3:
                raise ValueError("bad")
            return x
        results = run_parallel(task, range(5), workers=2, swallow=True)
        # Kết quả chứa cả exception cho phần lỗi.
        errors = [r for r in results if isinstance(r, Exception)]
        self.assertEqual(len(errors), 1)
```

- [ ] **Step 2: Viết module**

```python
"""Bounded parallel runner for sync tasks. Each worker closes its DB connection on exit."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, List, TypeVar

from django.db import connection

T = TypeVar("T")
R = TypeVar("R")


def run_parallel(
    task: Callable[[T], R],
    items: Iterable[T],
    *,
    workers: int = 5,
    swallow: bool = False,
) -> List[R]:
    items = list(items)
    if workers <= 1:
        out: List[R] = []
        for x in items:
            try:
                out.append(task(x))
            except Exception as exc:
                if not swallow:
                    raise
                out.append(exc)
        return out

    def _wrap(x: T) -> R:
        try:
            return task(x)
        finally:
            # Avoid leaking DB connections from worker threads.
            connection.close()

    results: List[R] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_wrap, x) for x in items]
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:
                if not swallow:
                    raise
                results.append(exc)
    return results
```

- [ ] **Step 3: Test PASS**

- [ ] **Step 4: Commit**

```bash
git add backend/apps/kiotviet/infrastructure/services/parallel_runner.py backend/apps/kiotviet/tests/test_parallel_runner.py
git commit -m "feat(kiotviet): bounded parallel runner with per-task connection cleanup"
```

---

### Task E2: Bật song song trong sync (gated bởi env)

**Files:**
- Modify: `sync_invoices_service.py`

- [ ] **Step 1: Thêm `workers` kwarg cho `run()`**

```python
def run(self, *, workers: Optional[int] = None, **options):
    workers = workers if workers is not None else int(os.getenv("KIOTVIET_SYNC_WORKERS", "5"))
    ...
```

- [ ] **Step 2: Vòng lặp invoice gọi `run_parallel`**

```python
from apps.kiotviet.infrastructure.services.parallel_runner import run_parallel

# Pre-scan / pre-load đã chạy xong ở trên (Phase C). Mỗi invoice độc lập:
results = run_parallel(self._sync_invoice, batch, workers=workers, swallow=True)
# Gom summary từ results (không mutate shared state).
```

> Quan trọng: pre-scan dedup phải chạy TRƯỚC pool (đơn-luồng). Sau đó pool chỉ làm phần create/push.

- [ ] **Step 3: Test characterization với `workers=1` (tuần tự) vẫn xanh**

- [ ] **Step 4: Test với `workers=4` — kiểm tra summary tổng cộng đúng**

Viết test mock 10 invoice → expect 10 result, không mất, không trùng.

- [ ] **Step 5: Commit**

```bash
git commit -am "perf(kiotviet): run per-invoice sync in bounded thread pool"
```

---

## Phase F — Tách helper trùng

### Task F1: Gom `_first_non_empty`, `_to_decimal`

**Files:**
- Create: `backend/apps/kiotviet/infrastructure/services/_payload_utils.py`
- Modify: 3 file đang định nghĩa lại

- [ ] **Step 1: Tạo module shared**

Copy 2 hàm này sang `_payload_utils.py` với type hint chuẩn + docstring.

- [ ] **Step 2: Test cho hàm util**

Tạo `test_payload_utils.py` với các case (empty/None/blank → bỏ; decimal parsing).

- [ ] **Step 3: Sửa 3 file import từ module mới, xóa định nghĩa cục bộ**

`sync_invoices_between_channels.py`, `import_kiotviet_invoices.py`, `invoice_payload_builder.py`.

- [ ] **Step 4: Test toàn bộ app kiotviet xanh**

```bash
cd backend && python manage.py test apps.kiotviet -v 1
```

- [ ] **Step 5: Commit**

```bash
git commit -am "refactor(kiotviet): centralize _first_non_empty/_to_decimal helpers"
```

---

## Phase G — Verify cuối + push

### Task G1: Tổng kiểm

- [ ] **Step 1: Chạy bộ test rộng**

```bash
cd backend && python manage.py test apps.kiotviet apps.orders apps.customers -v 1 2>&1 | tail -40
```

- [ ] **Step 2: Golden snapshot vẫn khớp**

```bash
cd backend && python manage.py test apps.kiotviet.tests.test_invoice_payload_golden -v 2
```

- [ ] **Step 3: Đo số dòng còn lại**

```bash
wc -l backend/apps/kiotviet/management/commands/sync_invoices_between_channels.py
```
Expected: < 200 dòng (wrapper).

- [ ] **Step 4: Đo số query/invoice (bằng test mới ở C2)**

Đảm bảo không phụ thuộc N.

- [ ] **Step 5: Push & in URL**

```bash
git branch --show-current   # phải là backend-perf-safety-improvements-20260528 (cùng branch của 3 spec)
git push 2>&1 | tail -5
```

---

## Self-Review

Spec 2 mục tiêu:
- Hợp nhất command→service → Phase B ✓
- Loại N+1 DB (mapping + customer) → Phase C ✓
- Tách `_sync_invoice` → Phase D ✓
- Song song hóa → Phase E ✓
- Gom helper → Phase F ✓
- Characterization an toàn → Phase A ✓ (chạy trước)

Không có placeholder ngoài các chỗ đánh dấu rõ "adjust if name differs" — đây là điểm phải verify khi đụng vào file thật vì command/service hiện có nhiều method tên cụ thể; vì tôn trọng nguyên tắc bảo toàn hành vi, executor sẽ điều chỉnh tên khớp code thực, không bịa.
