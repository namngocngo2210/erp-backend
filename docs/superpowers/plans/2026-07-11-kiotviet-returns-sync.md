# KiotViet Returns Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kéo danh sách phiếu trả hàng từ KiotViet (`GET /returns`), map về Order theo hóa đơn, điền mảng mã phiếu trả vào `OrderRecall.return_slip_code`.

**Architecture:** `OrderRecall.return_slip_code` đổi từ `CharField(64)` sang `ArrayField(CharField(64))` ở DB, nhưng **contract API giữ nguyên string** (serializer join `", "`, PATCH nhận string rồi tách dấu phẩy) → frontend không phải sửa. Một infrastructure service mới trong app `kiotviet` phân trang `/returns`, map `invoiceId → Order.invoice_id` (fallback `/invoices/{id}` lấy `code`), và ghi theo policy **UNION** (nối mã KiotViet mới vào mảng hiện có, không xoá/đè mã cũ — xem cập nhật sau review bên dưới). Chạy qua Celery beat 15 phút + management command để backfill.

> **CẬP NHẬT (whole-branch review, sau khi implement xong):** quyết định gốc "chỉ điền khi
> mảng đang rỗng" ở dưới đây là **sai và đã bị thay thế bởi UNION**. Lý do: với rule "chỉ điền
> khi rỗng", sau lần sync đầu tiên ghi `["TH001"]`, phiếu trả thứ 2 `TH002` đến sau vài ngày sẽ
> bị bỏ qua vĩnh viễn — mảng không bao giờ có quá 1 phần tử từ sync, phản tác dụng của việc
> đổi field sang mảng. Code thật (`returns_sync_service.py`) đã sửa theo UNION; phần code mẫu
> bên dưới trong plan này GIỮ NGUYÊN như bản ghi lịch sử của lần implement đầu, **không phản
> ánh hành vi hiện tại** — xem `docs/erp-kiotviet-returns-sync.md` để biết hành vi thật.

**Tech Stack:** Django 5 / DRF, Postgres (`django.contrib.postgres.fields.ArrayField`), Celery + Redis, `requests` qua `_kiotviet_request`.

**Spec:** `docs/superpowers/specs/2026-07-11-kiotviet-returns-sync-design.md`

## Global Constraints

- Branch: `feat/kiotviet-returns-sync`, worktree `../desi-order-wt-returns-sync`. Commit message tiếng Anh, ngắn, **không** kèm `Co-Authored-By`.
- Python 3.13 / Django. Lint: `ruff`. Code theo clean architecture của app `kiotviet`: `infrastructure/services/` (gọi API + ghi DB), `tasks.py` (celery), `management/commands/` (CLI). **Không** đặt logic trong command.
- Mọi HTTP call KiotViet đi qua `_kiotviet_request` từ `apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook` (signature: `_kiotviet_request(order, channel, method, endpoint, **kwargs)` — `order` positional, có thể `None`).
- **Không nuốt lỗi KiotViet**: HTTP ≠ 200 → raise `RuntimeError` kèm `status` + `body` nguyên văn.
- Ghi dữ liệu: **UNION** — nối mã KiotViet mới vào cuối mảng hiện có, dedupe, giữ nguyên mã/thứ
  tự cũ; không bao giờ xoá hay ghi đè mã nhân viên nhập tay. *(Xem cập nhật ở đầu file — rule gốc
  "chỉ điền khi rỗng" viết bên dưới đã bị thay thế.)*
- Nguồn: **tất cả** `KiotVietChannel` có `deleted_at__isnull=True`.
- `pageSize=100` (max KiotViet), phân trang bằng `currentItem`, dừng khi trang trả về < `pageSize`.
- Chạy test backend (Windows, per memory `running-backend-tests`):
  `docker run --rm --network desi-order_default -e POSTGRES_DB=desi_returns_sync -e REDIS_PASSWORD=testpassword desi-order-web-test pytest <path> -v`
  Nếu image chưa có: `docker build -f backend/Dockerfile.test -t desi-order-web-test backend` (kiểm tra tên Dockerfile test thực tế trong repo trước khi build).
- Frontend **không** sửa trong plan này.

## File Structure

| File | Trách nhiệm |
| --- | --- |
| `backend/apps/orders/migrations/0101_orderrecall_return_slip_code_array.py` | (Tạo) Đổi cột `order_recalls.return_slip_code` sang `varchar(64)[]`, chuyển dữ liệu cũ |
| `backend/apps/orders/models.py` | (Sửa) `OrderRecall.return_slip_code` → `ArrayField` |
| `backend/apps/orders/application/services/order_service_helpers.py` | (Sửa) `_upsert_order_recall`: string phẩy → list |
| `backend/apps/orders/repositories.py` | (Sửa) filter `has_return_slip` dùng `__len__gt=0` |
| `backend/apps/orders/transport/serializers/.../order_core_serializers.py` | (Sửa) serialize mảng → string join `", "` |
| `backend/apps/kiotviet/infrastructure/services/returns_sync_service.py` | (Tạo) Fetch `/returns`, map order, ghi `return_slip_code` |
| `backend/apps/kiotviet/tasks.py` | (Sửa) Thêm `sync_kiotviet_returns_task` |
| `backend/config/settings_parts/celery.py` | (Sửa) Route queue + beat entry 15' |
| `backend/apps/kiotviet/management/commands/sync_kiotviet_returns.py` | (Tạo) CLI backfill |
| `backend/apps/kiotviet/tests/test_returns_sync_service.py` | (Tạo) Test service |
| `backend/apps/kiotviet/tests/test_sync_kiotviet_returns_command.py` | (Tạo) Test command |
| `backend/apps/orders/tests/test_return_slip_filter.py` | (Sửa) Regression filter với ArrayField |
| `backend/apps/orders/tests/test_return_slip_array.py` | (Tạo) Test serialize + PATCH string→mảng |

---

### Task 1: Đổi `return_slip_code` sang ArrayField (contract API giữ string)

**Files:**
- Modify: `backend/apps/orders/models.py:569`
- Create: `backend/apps/orders/migrations/0101_orderrecall_return_slip_code_array.py`
- Modify: `backend/apps/orders/application/services/order_service_helpers.py:610-635`
- Modify: `backend/apps/orders/repositories.py:632-636`
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py:335`
- Modify: `backend/apps/orders/tests/test_return_slip_filter.py`
- Test: `backend/apps/orders/tests/test_return_slip_array.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `OrderRecall.return_slip_code: list[str]` (ArrayField, `default=list`, `blank=True`)
  - `OrderServiceCommands._upsert_order_recall(order, recall_payload, actor)` — với `recall_payload["return_slip_code"]` là **string** (phẩy) hoặc **list**, đều chuẩn hoá thành list.
  - `_parse_slip_codes(value) -> list[str]` (module-level trong `order_service_helpers.py`) — Task 2 dùng lại để chuẩn hoá mã trước khi ghi.

- [ ] **Step 1: Viết test thất bại cho serialize + PATCH**

Tạo `backend/apps/orders/tests/test_return_slip_array.py`. Copy đúng setup của `backend/apps/orders/tests/test_return_slip_filter.py` (đọc file đó trước để lấy cách tạo Channel/Customer/Order — không tự bịa).

```python
"""return_slip_code là ArrayField ở DB nhưng contract API vẫn là string phẩy."""
from django.test import TestCase

from apps.orders.application.services.order_service_helpers import _parse_slip_codes
from apps.orders.models import OrderRecall


class ParseSlipCodesTests(TestCase):
    def test_splits_comma_string_and_dedupes(self):
        self.assertEqual(_parse_slip_codes("TH001, TH002 ,TH001"), ["TH001", "TH002"])

    def test_empty_string_becomes_empty_list(self):
        self.assertEqual(_parse_slip_codes(""), [])
        self.assertEqual(_parse_slip_codes(None), [])

    def test_accepts_list_input(self):
        self.assertEqual(_parse_slip_codes([" TH001 ", "TH002", ""]), ["TH001", "TH002"])

    def test_truncates_to_64_chars(self):
        long_code = "X" * 80
        self.assertEqual(_parse_slip_codes(long_code), ["X" * 64])
```

Thêm test PATCH + serialize (dùng lại setup của `test_return_slip_filter.py`, `self.order` là một Order đã tạo):

```python
class ReturnSlipArrayWriteTests(TestCase):
    # setUp: tạo channel/customer/order giống test_return_slip_filter.py

    def test_upsert_recall_stores_list_from_comma_string(self):
        from apps.orders.application.services.order_service_commands import OrderServiceCommands

        OrderServiceCommands._upsert_order_recall(
            self.order, {"return_slip_code": "TH001, TH002"}, None
        )
        recall = OrderRecall.objects.get(order=self.order)
        self.assertEqual(recall.return_slip_code, ["TH001", "TH002"])

    def test_serializer_joins_array_into_string(self):
        from apps.orders.transport.serializers.order_serializers_impl.order_core_serializers import (
            OrderSerializer,
        )

        OrderRecall.objects.create(order=self.order, return_slip_code=["TH001", "TH002"])
        self.order.refresh_from_db()
        data = OrderSerializer(self.order).data
        self.assertEqual(data["recall"]["return_slip_code"], "TH001, TH002")
```

Lưu ý: mở `order_core_serializers.py` quanh dòng 335 để lấy **đúng tên serializer** đang build dict `recall` (nếu không phải `OrderSerializer` thì dùng tên thật, đừng đoán).

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `docker run --rm --network desi-order_default -e POSTGRES_DB=desi_returns_sync -e REDIS_PASSWORD=testpassword desi-order-web-test pytest apps/orders/tests/test_return_slip_array.py -v`
Expected: FAIL — `ImportError: cannot import name '_parse_slip_codes'`.

- [ ] **Step 3: Đổi model sang ArrayField**

`backend/apps/orders/models.py` — thêm import ở đầu file (cạnh các import django khác):

```python
from django.contrib.postgres.fields import ArrayField
```

Sửa dòng 569:

```python
    return_slip_code = ArrayField(                                                 # Phiếu trả hàng (nhiều mã)
        models.CharField(max_length=64),
        default=list,
        blank=True,
    )
```

- [ ] **Step 4: Viết migration đổi kiểu cột**

Tạo `backend/apps/orders/migrations/0101_orderrecall_return_slip_code_array.py`.
Postgres **không** tự cast `varchar` → `varchar[]`, nên phải `SeparateDatabaseAndState`: state dùng `AlterField`, database dùng `RunSQL` có `USING`.

```python
import django.contrib.postgres.fields
from django.db import migrations, models

FORWARD_SQL = """
ALTER TABLE order_recalls
    ALTER COLUMN return_slip_code DROP DEFAULT,
    ALTER COLUMN return_slip_code TYPE varchar(64)[]
        USING CASE
            WHEN return_slip_code IS NULL OR btrim(return_slip_code) = '' THEN ARRAY[]::varchar(64)[]
            ELSE ARRAY[left(btrim(return_slip_code), 64)]::varchar(64)[]
        END;
ALTER TABLE order_recalls ALTER COLUMN return_slip_code SET DEFAULT '{}';
ALTER TABLE order_recalls ALTER COLUMN return_slip_code SET NOT NULL;
"""

REVERSE_SQL = """
ALTER TABLE order_recalls
    ALTER COLUMN return_slip_code DROP DEFAULT,
    ALTER COLUMN return_slip_code TYPE varchar(64)
        USING left(COALESCE(array_to_string(return_slip_code, ', '), ''), 64);
ALTER TABLE order_recalls ALTER COLUMN return_slip_code SET DEFAULT '';
"""


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0100_alter_order_sku"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name="orderrecall",
                    name="return_slip_code",
                    field=django.contrib.postgres.fields.ArrayField(
                        base_field=models.CharField(max_length=64),
                        blank=True,
                        default=list,
                        size=None,
                    ),
                ),
            ],
        ),
    ]
```

- [ ] **Step 5: Sửa `_upsert_order_recall` — string phẩy → list**

`backend/apps/orders/application/services/order_service_helpers.py`. Thêm hàm module-level (đặt trên class chứa `_upsert_order_recall`):

```python
def _parse_slip_codes(value) -> list[str]:
    """Chuẩn hoá mã phiếu trả về list: nhận string phẩy (inline edit / import Excel)
    hoặc list (sync KiotViet). Strip, bỏ rỗng, dedupe giữ thứ tự, cắt 64 ký tự."""
    if value is None:
        return []
    raw = value if isinstance(value, (list, tuple)) else str(value).split(",")
    codes: list[str] = []
    for item in raw:
        code = str(item).strip()[:64]
        if code and code not in codes:
            codes.append(code)
    return codes
```

Sửa thân `_upsert_order_recall` (dòng ~621-630):

```python
        char_fields = {"recall_code"}
        list_fields = {"return_slip_code"}
        allowed = ("recall_code", "recall_created_date", "recall_received_date", "return_slip_code")
        changed: list[str] = []
        for field_name in allowed:
            if field_name not in recall_payload:
                continue
            value = recall_payload[field_name]
            if field_name in char_fields and value is None:
                value = ""
            elif field_name in list_fields:
                value = _parse_slip_codes(value)
            setattr(recall, field_name, value)
            changed.append(field_name)
```

- [ ] **Step 6: Sửa serializer — join mảng thành string**

`order_core_serializers.py` dòng 335:

```python
            "return_slip_code": ", ".join(recall.return_slip_code or []),
```

- [ ] **Step 7: Sửa filter `has_return_slip`**

`backend/apps/orders/repositories.py` (khối `has_return_slip`, ~dòng 632-636):

```python
            slip_exists = Exists(
                OrderRecall.objects.filter(order_id=OuterRef("pk"), return_slip_code__len__gt=0)
            )
```

(bỏ `.exclude(return_slip_code="")` — so sánh chuỗi rỗng không còn hợp lệ với ArrayField.)

- [ ] **Step 8: Sửa test filter hiện có sang mảng**

`backend/apps/orders/tests/test_return_slip_filter.py`: đổi mọi chỗ khởi tạo
`return_slip_code="PT-001"` → `return_slip_code=["PT-001"]`, `return_slip_code=""` → `return_slip_code=[]`,
`return_slip_code="PT-KOC-1"` → `return_slip_code=["PT-KOC-1"]`. Kỳ vọng yes/no giữ nguyên.

- [ ] **Step 9: Chạy test, xác nhận PASS**

Run:
```
docker run --rm --network desi-order_default -e POSTGRES_DB=desi_returns_sync -e REDIS_PASSWORD=testpassword desi-order-web-test pytest apps/orders/tests/test_return_slip_array.py apps/orders/tests/test_return_slip_filter.py -v
```
Expected: PASS toàn bộ (migration 0101 chạy khi tạo test DB).

- [ ] **Step 10: Lint + commit**

```bash
cd ../desi-order-wt-returns-sync
ruff check backend/apps/orders
git add backend/apps/orders
git commit -m "feat(orders): store return slip codes as array"
```

---

### Task 2: Service đồng bộ `/returns` từ KiotViet

**Files:**
- Create: `backend/apps/kiotviet/infrastructure/services/returns_sync_service.py`
- Test: `backend/apps/kiotviet/tests/test_returns_sync_service.py`

**Interfaces:**
- Consumes: `_parse_slip_codes` (Task 1), `OrderRecall.return_slip_code: list[str]` (Task 1),
  `_kiotviet_request(order, channel, method, endpoint, **kwargs) -> requests.Response`.
- Produces:
  - `iter_returns(channel: KiotVietChannel, *, last_modified_from: str | None = None) -> Iterator[list[dict]]` (yield từng trang = list bản ghi trả hàng)
  - `sync_returns_for_channel(channel: KiotVietChannel, *, last_modified_from: str | None = None, date_from: str | None = None, date_to: str | None = None, dry_run: bool = False) -> dict`
  - `sync_returns_all_channels(*, last_modified_from: str | None = None, date_from: str | None = None, date_to: str | None = None, dry_run: bool = False) -> dict`
  - Summary keys: `read`, `matched`, `updated`, `skipped_no_order`, `skipped_no_code`, `skipped_already_set`, `pages`; `sync_returns_all_channels` thêm `channels`, `errors`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/kiotviet/tests/test_returns_sync_service.py`. Đọc `backend/apps/kiotviet/tests/test_import_kiotviet_invoices_command.py` (setUp) trước để copy đúng cách tạo `KiotVietChannel` / `Channel` / `Customer` / `Order` trong repo này — không tự bịa field.

```python
from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.kiotviet.infrastructure.services.returns_sync_service import (
    sync_returns_all_channels,
    sync_returns_for_channel,
)
from apps.orders.models import Order, OrderRecall


def _resp(status=200, payload=None):
    response = MagicMock()
    response.status_code = status
    response.content = b"{}"
    response.text = ""
    response.json.return_value = payload or {}
    return response


def _ret(code, invoice_id, return_date="2026-07-01T10:00:00"):
    return {"id": 1, "code": code, "invoiceId": invoice_id, "returnDate": return_date}


class SyncReturnsForChannelTests(TestCase):
    def setUp(self):
        # tạo self.kv_channel (KiotVietChannel), self.order (Order với
        # kiotviet_channel=self.kv_channel, invoice_id=555, invoice_code="HD555")
        ...

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_maps_return_to_order_by_invoice_id(self, mock_req):
        mock_req.side_effect = [_resp(payload={"data": [_ret("TH001", 555)], "total": 1})]

        summary = sync_returns_for_channel(self.kv_channel)

        recall = OrderRecall.objects.get(order=self.order)
        self.assertEqual(recall.return_slip_code, ["TH001"])
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(summary["matched"], 1)

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_multiple_returns_same_invoice_sorted_and_deduped(self, mock_req):
        mock_req.side_effect = [
            _resp(payload={"data": [
                _ret("TH002", 555, "2026-07-02T10:00:00"),
                _ret("TH001", 555, "2026-07-01T10:00:00"),
                _ret("TH001", 555, "2026-07-01T10:00:00"),
            ]})
        ]

        sync_returns_for_channel(self.kv_channel)

        recall = OrderRecall.objects.get(order=self.order)
        self.assertEqual(recall.return_slip_code, ["TH001", "TH002"])

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_does_not_overwrite_existing_codes(self, mock_req):
        OrderRecall.objects.create(order=self.order, return_slip_code=["MANUAL-1"])
        mock_req.side_effect = [_resp(payload={"data": [_ret("TH001", 555)]})]

        summary = sync_returns_for_channel(self.kv_channel)

        recall = OrderRecall.objects.get(order=self.order)
        self.assertEqual(recall.return_slip_code, ["MANUAL-1"])
        self.assertEqual(summary["skipped_already_set"], 1)
        self.assertEqual(summary["updated"], 0)

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_falls_back_to_invoice_code_lookup(self, mock_req):
        self.order.invoice_id = None
        self.order.save(update_fields=["invoice_id"])
        mock_req.side_effect = [
            _resp(payload={"data": [_ret("TH001", 555)]}),          # /returns
            _resp(payload={"code": "HD555", "id": 555}),             # /invoices/555
        ]

        sync_returns_for_channel(self.kv_channel)

        recall = OrderRecall.objects.get(order=self.order)
        self.assertEqual(recall.return_slip_code, ["TH001"])

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_paginates_until_short_page(self, mock_req):
        page1 = [_ret(f"TH{i:03d}", 555) for i in range(100)]
        mock_req.side_effect = [
            _resp(payload={"data": page1}),
            _resp(payload={"data": [_ret("TH999", 555)]}),
        ]

        summary = sync_returns_for_channel(self.kv_channel)

        self.assertEqual(summary["pages"], 2)
        self.assertEqual(summary["read"], 101)

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_unknown_invoice_is_skipped(self, mock_req):
        mock_req.side_effect = [
            _resp(payload={"data": [_ret("TH001", 999999)]}),   # /returns
            _resp(status=404),                                   # /invoices/999999 fallback
        ]

        summary = sync_returns_for_channel(self.kv_channel)

        self.assertEqual(summary["skipped_no_order"], 1)
        self.assertFalse(OrderRecall.objects.filter(order=self.order).exists())

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_dry_run_does_not_write(self, mock_req):
        mock_req.side_effect = [_resp(payload={"data": [_ret("TH001", 555)]})]

        summary = sync_returns_for_channel(self.kv_channel, dry_run=True)

        self.assertEqual(summary["updated"], 1)
        self.assertFalse(OrderRecall.objects.filter(order=self.order).exists())

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service._kiotviet_request")
    def test_http_error_raises(self, mock_req):
        mock_req.side_effect = [_resp(status=500)]

        with self.assertRaises(RuntimeError):
            sync_returns_for_channel(self.kv_channel)


class SyncReturnsAllChannelsTests(TestCase):
    def setUp(self):
        # tạo self.kv_ok và self.kv_bad (2 KiotVietChannel active)
        ...

    @patch("apps.kiotviet.infrastructure.services.returns_sync_service.sync_returns_for_channel")
    def test_one_channel_error_does_not_block_others(self, mock_sync):
        def side_effect(channel, **kwargs):
            if channel.id == self.kv_bad.id:
                raise RuntimeError("boom")
            return {"read": 1, "matched": 1, "updated": 1, "skipped_no_order": 0,
                    "skipped_no_code": 0, "skipped_already_set": 0, "pages": 1}

        mock_sync.side_effect = side_effect

        summary = sync_returns_all_channels()

        self.assertEqual(summary["updated"], 1)
        self.assertEqual(len(summary["errors"]), 1)
        self.assertIn("boom", summary["errors"][0]["error"])
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `docker run --rm --network desi-order_default -e POSTGRES_DB=desi_returns_sync -e REDIS_PASSWORD=testpassword desi-order-web-test pytest apps/kiotviet/tests/test_returns_sync_service.py -v`
Expected: FAIL — `ModuleNotFoundError: apps.kiotviet.infrastructure.services.returns_sync_service`.

- [ ] **Step 3: Viết service**

Tạo `backend/apps/kiotviet/infrastructure/services/returns_sync_service.py`:

```python
"""Đồng bộ phiếu trả hàng KiotViet (GET /returns) → OrderRecall.return_slip_code.

Map theo invoiceId → Order.invoice_id (fallback /invoices/{id} lấy code khi đơn cũ
thiếu invoice_id). CHỈ điền khi return_slip_code đang rỗng — không đụng mã nhập tay.
"""

import logging
from typing import Iterator

from django.utils.dateparse import parse_datetime

from apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook import _kiotviet_request
from apps.kiotviet.models import KiotVietChannel
from apps.orders.application.services.order_service_helpers import _parse_slip_codes
from apps.orders.models import Order, OrderRecall

logger = logging.getLogger(__name__)

PAGE_SIZE = 100


def _empty_summary() -> dict:
    return {
        "read": 0,
        "matched": 0,
        "updated": 0,
        "skipped_no_order": 0,
        "skipped_no_code": 0,
        "skipped_already_set": 0,
        "pages": 0,
    }


def iter_returns(channel: KiotVietChannel, *, last_modified_from: str | None = None) -> Iterator[list[dict]]:
    """Phân trang GET /returns. Raise RuntimeError khi KiotViet trả != 200 (không nuốt lỗi)."""
    current_item = 0
    while True:
        params: dict = {"pageSize": PAGE_SIZE, "currentItem": current_item}
        if last_modified_from:
            params["lastModifiedFrom"] = last_modified_from

        response = _kiotviet_request(
            None, channel, "GET", "/returns", params=params
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"KiotViet /returns failed (channel_id={channel.id}): "
                f"status={response.status_code}, body={(response.text or '').strip()}"
            )

        payload = response.json() if response.content else {}
        items = payload.get("data") or []
        yield items
        if len(items) < PAGE_SIZE:
            return
        current_item += len(items)


def _lookup_invoice_code(channel: KiotVietChannel, invoice_id: int, cache: dict) -> str:
    """Fallback cho đơn cũ thiếu invoice_id: gọi /invoices/{id} lấy code. Cache trong 1 run."""
    if invoice_id in cache:
        return cache[invoice_id]

    response = _kiotviet_request(None, channel, "GET", f"/invoices/{invoice_id}")
    code = ""
    if response.status_code == 200:
        payload = response.json() if response.content else {}
        code = str(payload.get("code") or "").strip()
    else:
        logger.info(
            "KiotViet /invoices/%s lookup failed (channel_id=%s): status=%s",
            invoice_id, channel.id, response.status_code,
        )
    cache[invoice_id] = code
    return code


def _find_order(channel: KiotVietChannel, invoice_id: int, code_cache: dict) -> Order | None:
    order = Order.objects.filter(
        kiotviet_channel=channel, invoice_id=invoice_id, deleted_at__isnull=True
    ).first()
    if order is not None:
        return order

    invoice_code = _lookup_invoice_code(channel, invoice_id, code_cache)
    if not invoice_code:
        return None
    return Order.objects.filter(
        kiotviet_channel=channel, invoice_code=invoice_code, deleted_at__isnull=True
    ).first()


def _in_date_window(raw_date: str, date_from: str | None, date_to: str | None) -> bool:
    """Lọc client-side theo returnDate (KiotViet /returns không nhận khoảng ngày trả)."""
    if not date_from and not date_to:
        return True
    parsed = parse_datetime(raw_date) if raw_date else None
    if parsed is None:
        return False
    day = parsed.date().isoformat()
    if date_from and day < date_from:
        return False
    if date_to and day > date_to:
        return False
    return True


def sync_returns_for_channel(
    channel: KiotVietChannel,
    *,
    last_modified_from: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    dry_run: bool = False,
) -> dict:
    summary = _empty_summary()
    code_cache: dict[int, str] = {}
    # order_id -> list[(returnDate, code)] để sort theo ngày rồi dedupe
    by_order: dict[int, list[tuple[str, str]]] = {}
    orders: dict[int, Order] = {}

    for items in iter_returns(channel, last_modified_from=last_modified_from):
        summary["pages"] += 1
        for item in items:
            summary["read"] += 1
            code = str(item.get("code") or "").strip()
            invoice_id = item.get("invoiceId")
            return_date = str(item.get("returnDate") or "")
            if not code or not invoice_id:
                summary["skipped_no_code"] += 1
                continue
            if not _in_date_window(return_date, date_from, date_to):
                continue

            order = _find_order(channel, int(invoice_id), code_cache)
            if order is None:
                summary["skipped_no_order"] += 1
                continue

            summary["matched"] += 1
            orders[order.id] = order
            by_order.setdefault(order.id, []).append((return_date, code))

    for order_id, entries in by_order.items():
        codes = _parse_slip_codes([code for _date, code in sorted(entries)])
        recall = OrderRecall.objects.filter(order_id=order_id).first()
        if recall is not None and recall.return_slip_code:
            summary["skipped_already_set"] += 1
            continue

        summary["updated"] += 1
        if dry_run:
            logger.info("DRY-RUN order_id=%s return_slip_code -> %s", order_id, codes)
            continue

        if recall is None:
            OrderRecall.objects.create(order_id=order_id, return_slip_code=codes)
        else:
            recall.return_slip_code = codes
            recall.save(update_fields=["return_slip_code", "updated_at"])

    return summary


def sync_returns_all_channels(
    *,
    last_modified_from: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Lặp mọi KiotVietChannel active. Một kênh lỗi KHÔNG chặn kênh còn lại."""
    total = _empty_summary()
    total["channels"] = 0
    total["errors"] = []

    for channel in KiotVietChannel.objects.filter(deleted_at__isnull=True):
        total["channels"] += 1
        try:
            summary = sync_returns_for_channel(
                channel,
                last_modified_from=last_modified_from,
                date_from=date_from,
                date_to=date_to,
                dry_run=dry_run,
            )
        except Exception as exc:  # noqa: BLE001 — lỗi 1 kênh không được chặn kênh khác
            logger.exception("sync_returns failed for channel_id=%s", channel.id)
            total["errors"].append({"channel_id": channel.id, "error": str(exc)})
            continue

        for key in _empty_summary():
            total[key] += summary.get(key, 0)

    return total
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `docker run --rm --network desi-order_default -e POSTGRES_DB=desi_returns_sync -e REDIS_PASSWORD=testpassword desi-order-web-test pytest apps/kiotviet/tests/test_returns_sync_service.py -v`
Expected: PASS (9 test).

- [ ] **Step 5: Lint + commit**

```bash
ruff check backend/apps/kiotviet
git add backend/apps/kiotviet
git commit -m "feat(kiotviet): sync return slip codes from /returns"
```

---

### Task 3: Management command backfill

**Files:**
- Create: `backend/apps/kiotviet/management/commands/sync_kiotviet_returns.py`
- Test: `backend/apps/kiotviet/tests/test_sync_kiotviet_returns_command.py`

**Interfaces:**
- Consumes: `sync_returns_for_channel`, `sync_returns_all_channels` (Task 2).
- Produces: command `sync_kiotviet_returns` với option `--channel-id`, `--from`, `--to`, `--dry-run`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/kiotviet/tests/test_sync_kiotviet_returns_command.py`:

```python
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.kiotviet.models import KiotVietChannel


class SyncKiotVietReturnsCommandTests(TestCase):
    def setUp(self):
        # tạo self.kv_channel giống setUp của test_returns_sync_service.py
        ...

    @patch("apps.kiotviet.management.commands.sync_kiotviet_returns.sync_returns_all_channels")
    def test_defaults_to_all_channels(self, mock_all):
        mock_all.return_value = {"read": 0, "updated": 0, "errors": []}
        out = StringIO()

        call_command("sync_kiotviet_returns", stdout=out)

        mock_all.assert_called_once()
        self.assertEqual(mock_all.call_args.kwargs["dry_run"], False)
        self.assertIn('"updated": 0', out.getvalue())

    @patch("apps.kiotviet.management.commands.sync_kiotviet_returns.sync_returns_for_channel")
    def test_single_channel_with_date_window_and_dry_run(self, mock_one):
        mock_one.return_value = {"read": 0, "updated": 0}
        out = StringIO()

        call_command(
            "sync_kiotviet_returns",
            "--channel-id", str(self.kv_channel.id),
            "--from", "2026-07-01",
            "--to", "2026-07-05",
            "--dry-run",
            stdout=out,
        )

        kwargs = mock_one.call_args.kwargs
        self.assertEqual(kwargs["date_from"], "2026-07-01")
        self.assertEqual(kwargs["date_to"], "2026-07-05")
        self.assertTrue(kwargs["dry_run"])

    def test_unknown_channel_raises(self):
        with self.assertRaises(CommandError):
            call_command("sync_kiotviet_returns", "--channel-id", "999999")
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `docker run --rm --network desi-order_default -e POSTGRES_DB=desi_returns_sync -e REDIS_PASSWORD=testpassword desi-order-web-test pytest apps/kiotviet/tests/test_sync_kiotviet_returns_command.py -v`
Expected: FAIL — `Unknown command: 'sync_kiotviet_returns'`.

- [ ] **Step 3: Viết command**

Tạo `backend/apps/kiotviet/management/commands/sync_kiotviet_returns.py`:

```python
import json

from django.core.management.base import BaseCommand, CommandError

from apps.kiotviet.infrastructure.services.returns_sync_service import (
    sync_returns_all_channels,
    sync_returns_for_channel,
)
from apps.kiotviet.models import KiotVietChannel


class Command(BaseCommand):
    help = "Đồng bộ mã phiếu trả hàng từ KiotViet (/returns) vào OrderRecall.return_slip_code."

    def add_arguments(self, parser):
        parser.add_argument("--channel-id", type=int, default=None, help="Chỉ chạy 1 KiotVietChannel")
        parser.add_argument("--from", dest="date_from", default="", help="Lọc returnDate từ (YYYY-MM-DD)")
        parser.add_argument("--to", dest="date_to", default="", help="Lọc returnDate đến (YYYY-MM-DD)")
        parser.add_argument("--dry-run", action="store_true", help="Chỉ log, không ghi DB")

    def handle(self, *args, **options):
        channel_id = options.get("channel_id")
        date_from = str(options.get("date_from") or "").strip() or None
        date_to = str(options.get("date_to") or "").strip() or None
        dry_run = bool(options.get("dry_run"))

        if channel_id is None:
            summary = sync_returns_all_channels(date_from=date_from, date_to=date_to, dry_run=dry_run)
        else:
            try:
                channel = KiotVietChannel.objects.get(id=channel_id, deleted_at__isnull=True)
            except KiotVietChannel.DoesNotExist as exc:
                raise CommandError(f"Không tìm thấy KiotVietChannel id={channel_id}") from exc
            summary = sync_returns_for_channel(
                channel, date_from=date_from, date_to=date_to, dry_run=dry_run
            )

        self.stdout.write(self.style.SUCCESS("Sync KiotViet returns completed."))
        self.stdout.write(json.dumps(summary, ensure_ascii=False))
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `docker run --rm --network desi-order_default -e POSTGRES_DB=desi_returns_sync -e REDIS_PASSWORD=testpassword desi-order-web-test pytest apps/kiotviet/tests/test_sync_kiotviet_returns_command.py -v`
Expected: PASS (3 test).

- [ ] **Step 5: Lint + commit**

```bash
ruff check backend/apps/kiotviet
git add backend/apps/kiotviet
git commit -m "feat(kiotviet): add sync_kiotviet_returns command"
```

---

### Task 4: Celery task + beat schedule 15 phút

**Files:**
- Modify: `backend/apps/kiotviet/tasks.py` (thêm task ở cuối file)
- Modify: `backend/config/settings_parts/celery.py:27-46` (route) và `:71-180` (beat)
- Test: `backend/apps/kiotviet/tests/test_returns_sync_task.py`

**Interfaces:**
- Consumes: `sync_returns_all_channels(*, last_modified_from, date_from, date_to, dry_run)` (Task 2).
- Produces: task `apps.kiotviet.tasks.sync_kiotviet_returns` (`sync_kiotviet_returns_task(lookback_hours: int = 2)`).

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/kiotviet/tests/test_returns_sync_task.py`:

```python
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from apps.kiotviet.tasks import sync_kiotviet_returns_task

LOCK_KEY = "kv:sync_returns:lock"


class SyncKiotVietReturnsTaskTests(TestCase):
    def setUp(self):
        cache.delete(LOCK_KEY)

    def tearDown(self):
        cache.delete(LOCK_KEY)

    @patch("apps.kiotviet.tasks.sync_returns_all_channels")
    def test_passes_lookback_window(self, mock_sync):
        mock_sync.return_value = {"updated": 2, "errors": []}

        result = sync_kiotviet_returns_task(lookback_hours=2)

        self.assertEqual(result["updated"], 2)
        last_modified_from = mock_sync.call_args.kwargs["last_modified_from"]
        self.assertRegex(last_modified_from, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    @patch("apps.kiotviet.tasks.sync_returns_all_channels")
    def test_skips_when_lock_held(self, mock_sync):
        cache.add(LOCK_KEY, 1, 60)

        result = sync_kiotviet_returns_task()

        self.assertEqual(result, {"skipped": "lock_held"})
        mock_sync.assert_not_called()
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `docker run --rm --network desi-order_default -e POSTGRES_DB=desi_returns_sync -e REDIS_PASSWORD=testpassword desi-order-web-test pytest apps/kiotviet/tests/test_returns_sync_task.py -v`
Expected: FAIL — `ImportError: cannot import name 'sync_kiotviet_returns_task'`.

- [ ] **Step 3: Thêm task**

`backend/apps/kiotviet/tasks.py` — thêm import ở đầu file (cạnh các import service khác):

```python
from apps.kiotviet.infrastructure.services.returns_sync_service import sync_returns_all_channels
```

và thêm task ở cuối file:

```python
@shared_task(
    bind=True,
    name="apps.kiotviet.tasks.sync_kiotviet_returns",
    soft_time_limit=240,
    time_limit=300,
)
def sync_kiotviet_returns_task(self, lookback_hours: int = 2) -> dict:
    """Cron entrypoint: kéo phiếu trả hàng mới/cập nhật từ MỌI KiotViet channel.

    Cửa sổ lastModifiedFrom = now - lookback_hours (chồng lấn để không mất bản ghi khi
    lệch giờ / KiotViet cập nhật trễ). Lock chống chồng tick 15'."""
    lock_key = "kv:sync_returns:lock"
    if not cache.add(lock_key, 1, 300):
        logger.info("sync_kiotviet_returns skip: lock held (đang chạy)")
        return {"skipped": "lock_held"}

    try:
        last_modified_from = (
            timezone.localtime() - timedelta(hours=lookback_hours)
        ).strftime("%Y-%m-%d %H:%M:%S")
        summary = sync_returns_all_channels(last_modified_from=last_modified_from)
        if summary.get("errors"):
            logger.warning("sync_kiotviet_returns finished with errors: %s", summary["errors"])
        return summary
    finally:
        cache.delete(lock_key)
```

Thêm `from datetime import timedelta` vào import đầu file nếu chưa có (`timezone` và `cache` đã được import sẵn).

- [ ] **Step 4: Đăng ký route + beat**

`backend/config/settings_parts/celery.py` — thêm vào `CELERY_TASK_ROUTES` (sau dòng `import_create_only_one_channel`):

```python
    "apps.kiotviet.tasks.sync_kiotviet_returns": {"queue": CELERY_KIOTVIET_QUEUE},
```

Thêm vào `CELERY_BEAT_SCHEDULE` (sau entry `kv-import-create-only`):

```python
    # Kéo phiếu trả hàng KiotViet (/returns) của MỌI kênh → điền
    # OrderRecall.return_slip_code (mảng mã). Cửa sổ lastModifiedFrom = now-2h,
    # chồng lấn để không mất bản ghi; chỉ điền khi đơn chưa có mã (không đè nhập tay).
    "kv-sync-returns": {
        "task": "apps.kiotviet.tasks.sync_kiotviet_returns",
        "schedule": 15 * 60.0,
    },
```

- [ ] **Step 5: Chạy test, xác nhận PASS**

Run: `docker run --rm --network desi-order_default -e POSTGRES_DB=desi_returns_sync -e REDIS_PASSWORD=testpassword desi-order-web-test pytest apps/kiotviet/tests/test_returns_sync_task.py -v`
Expected: PASS (2 test).

- [ ] **Step 6: Chạy full test 2 app + lint**

Run:
```
docker run --rm --network desi-order_default -e POSTGRES_DB=desi_returns_sync_full -e REDIS_PASSWORD=testpassword desi-order-web-test pytest apps/kiotviet apps/orders -q
```
Expected: PASS (trừ các baseline fail đã biết — xem memory `known-failing-baseline-tests`; nếu có fail lạ, dừng và báo).

```bash
ruff check backend
```

- [ ] **Step 7: Commit**

```bash
git add backend/apps/kiotviet backend/config
git commit -m "feat(kiotviet): schedule returns sync every 15 minutes"
```

---

### Task 5: Tài liệu vận hành + MR

**Files:**
- Create: `docs/erp-kiotviet-returns-sync.md`

- [ ] **Step 1: Viết doc runbook**

Tạo `docs/erp-kiotviet-returns-sync.md`:

```markdown
# Đồng bộ mã phiếu trả hàng từ KiotViet

## Luồng
- Celery beat `kv-sync-returns` chạy mỗi 15 phút → `apps.kiotviet.tasks.sync_kiotviet_returns`
  → `sync_returns_all_channels(last_modified_from=now-2h)`.
- Gọi `GET /returns` (pageSize=100, phân trang `currentItem`) cho mọi `KiotVietChannel` active.
- Map `invoiceId` → `Order.invoice_id` (fallback `GET /invoices/{id}` lấy `code` → `Order.invoice_code`).
- Ghi mảng mã vào `OrderRecall.return_slip_code`, **chỉ khi đang rỗng** (không đè mã nhân viên nhập tay).

## Backfill lịch sử
```bash
docker compose exec web python manage.py sync_kiotviet_returns --from 2026-01-01 --to 2026-07-11 --dry-run
docker compose exec web python manage.py sync_kiotviet_returns --from 2026-01-01 --to 2026-07-11
```
Chạy 1 kênh: thêm `--channel-id <id>`.

## Ghi chú
- `return_slip_code` là `varchar(64)[]` ở DB (migration `orders/0101`), nhưng API vẫn trả/nhận
  string phẩy (`"TH001, TH002"`) → frontend không đổi.
- Đơn không tìm thấy hóa đơn tương ứng được đếm vào `skipped_no_order` và bỏ qua (không tạo đơn mới).
```

- [ ] **Step 2: Commit + push + tạo MR**

```bash
git add docs/erp-kiotviet-returns-sync.md
git commit -m "docs: kiotviet returns sync runbook"
git fetch origin && git rebase origin/master   # resolve conflict nếu có
git push -u origin feat/kiotviet-returns-sync
```
Gửi link MR cho user (GitLab in link trong output của `git push`).

---

## Ghi chú triển khai prod

- Migration `orders/0101` đổi kiểu cột — chạy trên bảng `order_recalls` (nhỏ, không phải `orders`),
  `ALTER TABLE ... TYPE` sẽ lock bảng trong thời gian rewrite. Chấp nhận được với kích thước hiện tại,
  nhưng kiểm tra `SELECT count(*) FROM order_recalls;` trước khi deploy.
- Sau deploy: chạy `sync_kiotviet_returns --dry-run` cho một khoảng ngày nhỏ để đối chiếu summary
  (`matched` / `skipped_no_order`) trước khi để cron chạy tự do.
