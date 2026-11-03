# KOC: nhóm thu hồi (mã + ngày tạo + ngày nhận) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Một đơn KOC có N nhóm thu hồi, mỗi nhóm là bộ ba (mã đơn thu hồi, ngày tạo, ngày nhận hàng), sửa được cả trên lưới danh sách lẫn trang chi tiết.

**Architecture:** Ba trường thu hồi rời khỏi `OrderRecall` (1-1) sang bảng mới `OrderRecallEntry` (1-N). `OrderRecall` chỉ còn `return_slip_code` để job sync KiotViet không phải đổi. Contract ghi là full-replace danh sách, đúng khuôn `items` đã có. Ba cột trên lưới giữ nguyên key nhưng mỗi ô render N input xếp chồng.

**Tech Stack:** Django 5 + DRF + PostgreSQL, React + TypeScript + vitest.

**Spec:** `docs/superpowers/specs/2026-08-14-koc-recall-entry-groups-design.md`

## Global Constraints

- Comment/nhãn người dùng thấy: **tiếng Việt**. Comment giải thích "tại sao", không mô tả lại code.
- Ba column key `recall_code` / `recall_created_date` / `recall_received_date` **giữ nguyên tên** — chúng là key lưu trong `localStorage` của từng người dùng và là key cột export. Đổi tên là mọi người mất cấu hình bảng.
- `return_slip_code` và job `returns_sync_service` **không được đụng tới**.
- `koc_order_type` **không được đụng tới**.
- Nhóm rỗng hoàn toàn (cả 3 ô trống) bị loại khi lưu — đó là cách xoá nhóm.
- Backend lint `ruff check .` phải sạch. Repo **không có CI** (`.gitlab-ci.yml` đã bị xoá ở `eb15fe28`) — mọi lint/test chạy tay.
- Không tạo file test mới, trừ **một** ngoại lệ: `frontend/src/features/orders/__tests__/recall-entries.test.tsx`.
- Chỉ được có MỘT leaf migration trong app `orders` (master từng dính sự cố trùng leaf ở `93c59be9`). Master đang ở `0126_order_koc_type_index`.

### Lệnh chạy test

Backend (container `desi-test-pg` / `desi-test-redis` / network `desi-test-net` đã chạy sẵn; đổi `POSTGRES_DB` + `REDIS_CACHE_DB` cho mỗi lần chạy song song; tạo DB trước nếu chưa có bằng `docker exec desi-test-pg psql -U desi_order -c "CREATE DATABASE <db> OWNER desi_order"`):

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-wt-recall-groups && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_rg_t1 \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
```

Ruff (image test không có ruff):

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

Frontend (`npm ci` đã chạy xong trong worktree; **đừng** dùng junction node_modules — vitest 3 vỡ dual-instance):

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-wt-recall-groups/frontend && npx vitest run <file>
```

Chạy test **foreground**. Nếu sợ timeout 10 phút thì chia nhỏ theo từng file test, đừng chạy nền rồi chờ thông báo.

---

## File Structure

**Backend — tạo:**

| File | Trách nhiệm |
|---|---|
| `backend/apps/orders/migrations/0127_orderrecallentry.py` | Tạo bảng `order_recall_entries` |
| `backend/apps/orders/migrations/0128_copy_recall_entries.py` | Chuyển dữ liệu 1-1 → 1-N (kèm hàm thuần để test được) |
| `backend/apps/orders/migrations/0129_remove_legacy_recall_fields.py` | Xoá 3 cột cũ khỏi `order_recalls` |

**Backend — sửa:** `models.py`, `order_core_serializers.py`, `kiotviet_landing_serializers.py`, `order_service_helpers.py`, `order_service_commands.py`, `export_excel.py`, `repositories.py`, `shipping_lookup_view.py`.

**Frontend — sửa:** `order-types.ts`, `OrdersTable/cell-renderers.tsx`, `BlockRecall.tsx`, `i18n/messages/orders.ts`.

**Frontend — tạo:** `frontend/src/features/orders/__tests__/recall-entries.test.tsx`.

---

### Task 1: Bảng `OrderRecallEntry` + 3 migration

**Files:**
- Modify: `backend/apps/orders/models.py` (thêm class `OrderRecallEntry` ngay dưới `OrderRecall`; xoá 3 field khỏi `OrderRecall`)
- Create: `backend/apps/orders/migrations/0127_orderrecallentry.py`
- Create: `backend/apps/orders/migrations/0128_copy_recall_entries.py`
- Create: `backend/apps/orders/migrations/0129_remove_legacy_recall_fields.py`
- Test: `backend/apps/orders/tests/test_order_recall.py`

**Interfaces:**
- Consumes: không có (task đầu).
- Produces: `OrderRecallEntry` import được từ `apps.orders.models`, quan hệ `Order.recall_entries` (1-N, `ordering = ["id"]`). `OrderRecall` chỉ còn `return_slip_code` + `updated_by` + timestamps. Task 2 dựa vào cả hai.

- [ ] **Step 1: Xác nhận leaf migration hiện tại**

Run: `cd /c/Users/MYPC/Desktop/workspace/desi-order-wt-recall-groups/backend/apps/orders/migrations && ls | tail -5`
Expected: file mới nhất là `0126_order_koc_type_index.py`. Nếu thấy 2 leaf khác nhau, DỪNG và báo.

- [ ] **Step 2: Thêm model, xoá 3 field cũ**

Trong `models.py`, thêm ngay dưới `class OrderRecall`:

```python
class OrderRecallEntry(models.Model):
    """Một đợt thu hồi của đơn KOC. Một đơn có N đợt, mỗi đợt một mã và cặp ngày
    của riêng nó — trước đây N mã dùng chung một cặp ngày nên nhìn vào không biết
    mã nào ứng với ngày nào."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="recall_entries")
    recall_code = models.CharField(max_length=64, blank=True, default="")          # Mã đơn thu hồi
    recall_created_date = models.DateField(null=True, blank=True)                  # Ngày tạo đơn thu hồi
    recall_received_date = models.DateField(null=True, blank=True)                 # Ngày nhận hàng thu hồi
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_order_recall_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "order_recall_entries"
        # Thứ tự nhóm = thứ tự tạo. Không có cột sort_order: không có nhu cầu sắp
        # xếp lại, mà một cột thứ tự không ai đổi là cột chết.
        ordering = ["id"]
        indexes = [
            models.Index(fields=["order", "id"], name="recall_entry_order_idx"),
            # btree, KHÔNG phải GIN: mỗi dòng một mã nên equality trên btree vừa
            # nhanh hơn vừa nhỏ hơn (đảo ngược có chủ đích lựa chọn GIN của MR !1228,
            # vốn chỉ cần khi cột còn là mảng).
            models.Index(fields=["recall_code"], name="recall_entry_code_idx"),
        ]
```

Trong `class OrderRecall`: **xoá** ba field `recall_code`, `recall_created_date`, `recall_received_date`. Giữ nguyên `return_slip_code`, `updated_by`, `created_at`, `updated_at`, và `class Meta` — nhưng trong `Meta.indexes` xoá dòng `GinIndex(fields=["recall_code"], name="order_recall_code_gin")`, chỉ giữ `models.Index(fields=["created_at"], name="order_recall_created_idx")`. Cập nhật docstring của `OrderRecall` thành:

```python
class OrderRecall(models.Model):
    """Phiếu trả hàng của đơn (1-1 với Order). Chỉ còn `return_slip_code` —
    job returns_sync_service (KiotViet) ghi UNION vào đây theo invoice_id.
    Mã/ngày thu hồi đã tách sang OrderRecallEntry (1-N)."""
```

Nếu `GinIndex` không còn được dùng ở chỗ nào khác trong file thì xoá luôn dòng import của nó.

- [ ] **Step 3: Tạo migration `0127` (bảng mới)**

Create `backend/apps/orders/migrations/0127_orderrecallentry.py`:

```python
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("orders", "0126_order_koc_type_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrderRecallEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("recall_code", models.CharField(blank=True, default="", max_length=64)),
                ("recall_created_date", models.DateField(blank=True, null=True)),
                ("recall_received_date", models.DateField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recall_entries",
                        to="orders.order",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_order_recall_entries",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "order_recall_entries",
                "ordering": ["id"],
            },
        ),
        migrations.AddIndex(
            model_name="orderrecallentry",
            index=models.Index(fields=["order", "id"], name="recall_entry_order_idx"),
        ),
        migrations.AddIndex(
            model_name="orderrecallentry",
            index=models.Index(fields=["recall_code"], name="recall_entry_code_idx"),
        ),
    ]
```

Nếu `BigAutoField` không khớp `DEFAULT_AUTO_FIELD` của project thì để Django tự sinh migration này rồi đối chiếu — cách chắc chắn nhất là chạy `makemigrations orders --name orderrecallentry` và so với khối trên.

- [ ] **Step 4: Tạo migration `0128` (chuyển dữ liệu)**

Create `backend/apps/orders/migrations/0128_copy_recall_entries.py`:

```python
from django.db import migrations

_BATCH = 2000


def split_recall_into_entries(codes, created_date, received_date):
    """Một bản ghi OrderRecall cũ → danh sách (code, created, received) của các nhóm.

    Hàm THUẦN, tách riêng để test được mà không phải dựng lại state migration.
    Luật: mỗi mã thành một nhóm dùng chung cặp ngày; không mã nhưng có ít nhất một
    ngày thì vẫn ra một nhóm (giữ lại dữ liệu ngày, không âm thầm vứt đi); trống
    hoàn toàn thì không ra nhóm nào.
    """
    cleaned = [str(code).strip()[:64] for code in (codes or []) if str(code).strip()]
    if not cleaned:
        if created_date is None and received_date is None:
            return []
        return [("", created_date, received_date)]
    return [(code, created_date, received_date) for code in cleaned]


def copy_forward(apps, schema_editor):
    OrderRecall = apps.get_model("orders", "OrderRecall")
    OrderRecallEntry = apps.get_model("orders", "OrderRecallEntry")

    pending = []
    for recall in OrderRecall.objects.all().iterator(chunk_size=_BATCH):
        for code, created, received in split_recall_into_entries(
            recall.recall_code, recall.recall_created_date, recall.recall_received_date
        ):
            pending.append(
                OrderRecallEntry(
                    order_id=recall.order_id,
                    recall_code=code,
                    recall_created_date=created,
                    recall_received_date=received,
                )
            )
        if len(pending) >= _BATCH:
            OrderRecallEntry.objects.bulk_create(pending)
            pending = []
    if pending:
        OrderRecallEntry.objects.bulk_create(pending)


def copy_backward(apps, schema_editor):
    """Gộp ngược N nhóm về 1 mảng mã + cặp ngày của NHÓM ĐẦU TIÊN.

    MẤT DỮ LIỆU có chủ đích: ngày của các nhóm sau không có chỗ chứa ở mô hình cũ.
    Runbook trong spec bắt dump `order_recall_entries` trước khi lùi.
    """
    OrderRecall = apps.get_model("orders", "OrderRecall")
    OrderRecallEntry = apps.get_model("orders", "OrderRecallEntry")

    by_order: dict[int, dict] = {}
    for entry in OrderRecallEntry.objects.order_by("order_id", "id").iterator(chunk_size=_BATCH):
        bucket = by_order.get(entry.order_id)
        if bucket is None:
            # Nhóm đầu tiên của đơn quyết định cặp ngày.
            bucket = {
                "codes": [],
                "created": entry.recall_created_date,
                "received": entry.recall_received_date,
            }
            by_order[entry.order_id] = bucket
        if entry.recall_code:
            bucket["codes"].append(entry.recall_code)

    for order_id, bucket in by_order.items():
        OrderRecall.objects.update_or_create(
            order_id=order_id,
            defaults={
                "recall_code": bucket["codes"],
                "recall_created_date": bucket["created"],
                "recall_received_date": bucket["received"],
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0127_orderrecallentry"),
    ]

    operations = [
        migrations.RunPython(copy_forward, copy_backward),
    ]
```

- [ ] **Step 5: Tạo migration `0129` (xoá 3 cột cũ)**

Create `backend/apps/orders/migrations/0129_remove_legacy_recall_fields.py`:

```python
import django.contrib.postgres.fields
from django.contrib.postgres.indexes import GinIndex
from django.db import migrations, models


class Migration(migrations.Migration):
    """Xoá 3 cột thu hồi khỏi order_recalls sau khi 0128 đã chuyển sang bảng mới.

    Reverse dựng lại nguyên trạng cột + index GIN (rỗng) để 0128 có chỗ ghi ngược.
    """

    dependencies = [
        ("orders", "0128_copy_recall_entries"),
    ]

    operations = [
        migrations.RemoveIndex(model_name="orderrecall", name="order_recall_code_gin"),
        migrations.RemoveField(model_name="orderrecall", name="recall_code"),
        migrations.RemoveField(model_name="orderrecall", name="recall_created_date"),
        migrations.RemoveField(model_name="orderrecall", name="recall_received_date"),
    ]
```

`RemoveField` tự có reverse là `AddField` với đúng định nghĩa cũ, và `RemoveIndex` tự reverse thành `AddIndex` — nên không cần viết tay reverse. Import `django.contrib.postgres.fields`, `GinIndex`, `models` chỉ giữ lại nếu ruff không báo unused; nếu ruff báo thì xoá import thừa.

- [ ] **Step 6: Viết test cho hàm chuyển dữ liệu**

Thêm vào `backend/apps/orders/tests/test_order_recall.py` (đọc file trước để đặt đúng chỗ, dùng đúng style import của file):

```python
from importlib import import_module


def _split():
    # Tên module bắt đầu bằng số nên phải import bằng chuỗi.
    return import_module("apps.orders.migrations.0128_copy_recall_entries").split_recall_into_entries


class SplitRecallIntoEntriesTests(TestCase):
    def test_each_code_becomes_a_group_sharing_the_dates(self):
        split = _split()
        result = split(["RC-01", "RC-02"], date(2026, 8, 12), date(2026, 8, 15))
        self.assertEqual(
            result,
            [
                ("RC-01", date(2026, 8, 12), date(2026, 8, 15)),
                ("RC-02", date(2026, 8, 12), date(2026, 8, 15)),
            ],
        )

    def test_dates_without_code_still_produce_one_group(self):
        split = _split()
        self.assertEqual(split([], date(2026, 8, 12), None), [("", date(2026, 8, 12), None)])

    def test_completely_empty_produces_no_group(self):
        split = _split()
        self.assertEqual(split([], None, None), [])
        self.assertEqual(split(None, None, None), [])

    def test_blank_and_whitespace_codes_are_dropped(self):
        split = _split()
        self.assertEqual(split(["  ", "RC-01 "], None, None), [("RC-01", None, None)])
```

Nếu `TestCase` / `date` chưa được import trong file thì thêm `from datetime import date` và dùng đúng base class mà các test khác trong file đang dùng.

- [ ] **Step 7: Chạy test + xác nhận không drift**

Run: pytest với `<paths>` = `apps/orders/tests/test_order_recall.py -k SplitRecallIntoEntries` (dùng `POSTGRES_DB=desi_rg_t1`, `REDIS_CACHE_DB=7`)
Expected: PASS.

Run tiếp trong cùng container:
```bash
python manage.py makemigrations --check --dry-run && python manage.py showmigrations orders | tail -6
```
Expected: `No changes detected`, và `0126 → 0127 → 0128 → 0129` tuyến tính, một leaf duy nhất.

Lưu ý: cả file test khác trong `test_order_recall.py` sẽ ĐỎ ở bước này vì serializer/service chưa đổi — đó là Task 2. Chỉ cần nhóm test mới xanh.

- [ ] **Step 8: Ruff + commit**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
git add backend/apps/orders/models.py backend/apps/orders/migrations backend/apps/orders/tests/test_order_recall.py
git commit -m "feat(orders): add recall entry table and migrate legacy fields"
```

---

### Task 2: Backend contract — đọc, ghi full-replace, export, filter, tra cứu

**Files:**
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py` (`get_recall`, thêm `get_recall_entries`, thêm vào tuple `fields`)
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py` (`OrderRecallInputSerializer`, thêm `OrderRecallEntryInputSerializer`, thêm field vào `OrderInlineUpdateSerializer`)
- Modify: `backend/apps/orders/application/services/order_service_helpers.py` (`_upsert_order_recall`, thêm `_replace_order_recall_entries`)
- Modify: `backend/apps/orders/application/services/order_service_commands.py` (gọi hàm mới trong luồng inline update + luồng tạo đơn)
- Modify: `backend/apps/orders/export_excel.py`
- Modify: `backend/apps/orders/repositories.py` (prefetch + filter `has_recall_code`)
- Modify: `backend/apps/orders/transport/views/shipping_lookup_view.py`
- Test: `backend/apps/orders/tests/test_order_recall.py`, `test_shipping_lookup.py`, `test_order_list_exists_filters.py`, `test_orders_export_definition.py`

**Interfaces:**
- Consumes: `OrderRecallEntry`, `Order.recall_entries` (Task 1).
- Produces: response có `recall_entries: [{id, recall_code, recall_created_date, recall_received_date}]` (mảng, luôn có mặt, rỗng khi không có nhóm) và `recall: {"return_slip_code": "..."}`. Chiều ghi nhận `recall_entries` là mảng full-replace. Task 3 và 4 (frontend) dựa vào đúng hình dạng này.

- [ ] **Step 1: Viết test đỏ cho contract đọc/ghi**

Thêm vào `backend/apps/orders/tests/test_order_recall.py` (đọc file trước, dùng đúng helper tạo đơn sẵn có — ĐỪNG đoán tên):

```python
def test_patch_creates_multiple_recall_entries(self):
    """Full-replace: gửi 2 nhóm thì đơn có đúng 2 nhóm, đúng thứ tự."""
    order = self._make_order()
    resp = self.client.patch(
        f"/api/orders/{order.id}",
        {
            "recall_entries": [
                {"recall_code": "RC-01", "recall_created_date": "2026-08-12", "recall_received_date": "2026-08-15"},
                {"recall_code": "RC-02", "recall_created_date": "2026-08-20", "recall_received_date": None},
            ]
        },
        format="json",
    )
    self.assertEqual(resp.status_code, 200)
    entries = list(order.recall_entries.all())
    self.assertEqual([e.recall_code for e in entries], ["RC-01", "RC-02"])
    self.assertEqual(entries[1].recall_received_date, None)
    self.assertEqual([e["recall_code"] for e in resp.data["recall_entries"]], ["RC-01", "RC-02"])


def test_patch_replaces_the_whole_entry_list(self):
    order = self._make_order()
    OrderRecallEntry.objects.create(order=order, recall_code="OLD-1")
    OrderRecallEntry.objects.create(order=order, recall_code="OLD-2")
    resp = self.client.patch(
        f"/api/orders/{order.id}",
        {"recall_entries": [{"recall_code": "NEW-1"}]},
        format="json",
    )
    self.assertEqual(resp.status_code, 200)
    self.assertEqual([e.recall_code for e in order.recall_entries.all()], ["NEW-1"])


def test_empty_group_is_dropped(self):
    """Nhóm cả 3 ô trống = nhóm bị xoá trên UI, không được lưu."""
    order = self._make_order()
    resp = self.client.patch(
        f"/api/orders/{order.id}",
        {
            "recall_entries": [
                {"recall_code": "RC-01", "recall_created_date": None, "recall_received_date": None},
                {"recall_code": "", "recall_created_date": None, "recall_received_date": None},
            ]
        },
        format="json",
    )
    self.assertEqual(resp.status_code, 200)
    self.assertEqual([e.recall_code for e in order.recall_entries.all()], ["RC-01"])


def test_patch_without_recall_entries_keeps_existing_groups(self):
    order = self._make_order()
    OrderRecallEntry.objects.create(order=order, recall_code="KEEP-1")
    resp = self.client.patch(f"/api/orders/{order.id}", {"note": "x"}, format="json")
    self.assertEqual(resp.status_code, 200)
    self.assertEqual([e.recall_code for e in order.recall_entries.all()], ["KEEP-1"])


def test_return_slip_code_still_works_independently(self):
    """Phiếu trả hàng ở mức đơn, không bị full-replace nhóm đụng tới."""
    order = self._make_order()
    self.client.patch(
        f"/api/orders/{order.id}", {"recall": {"return_slip_code": "PT-1, PT-2"}}, format="json"
    )
    resp = self.client.patch(
        f"/api/orders/{order.id}", {"recall_entries": [{"recall_code": "RC-01"}]}, format="json"
    )
    self.assertEqual(resp.status_code, 200)
    self.assertEqual(resp.data["recall"]["return_slip_code"], "PT-1, PT-2")
```

Xoá/sửa các test cũ trong file đang assert `recall.recall_code` là mảng trên `OrderRecall` — 3 field đó không còn tồn tại ở bảng cũ nữa. Test nào chuyển được sang `recall_entries` thì chuyển; test nào đã hết ý nghĩa (VD test `max_length=1024` của chuỗi nhiều mã) thì xoá và ghi lý do trong report.

Nhớ thêm `OrderRecallEntry` vào khối import của file.

- [ ] **Step 2: Chạy test để xác nhận ĐỎ**

Run: pytest với `<paths>` = `apps/orders/tests/test_order_recall.py` (`POSTGRES_DB=desi_rg_t2`, `REDIS_CACHE_DB=8`)
Expected: FAIL — `recall_entries` chưa có ở serializer.

- [ ] **Step 3: Serializer đọc**

Trong `order_core_serializers.py`:

```python
    recall_entries = serializers.SerializerMethodField()
```

Thêm `"recall_entries"` vào tuple `fields` (ngay cạnh `"recall"`), và thêm method:

```python
    def get_recall_entries(self, obj) -> list[dict]:
        # Luôn trả mảng (rỗng khi chưa có nhóm) chứ không None: lưới và trang chi
        # tiết đều render danh sách, khỏi phải kiểm null ở mọi chỗ dùng.
        return [
            {
                "id": entry.id,
                "recall_code": entry.recall_code,
                "recall_created_date": (
                    entry.recall_created_date.isoformat() if entry.recall_created_date else None
                ),
                "recall_received_date": (
                    entry.recall_received_date.isoformat() if entry.recall_received_date else None
                ),
            }
            for entry in obj.recall_entries.all()
        ]
```

Sửa `get_recall` để chỉ còn phiếu trả hàng:

```python
    def get_recall(self, obj) -> dict | None:
        recall = getattr(obj, "recall", None)
        if recall is None:
            return None
        # Mã/ngày thu hồi đã tách sang `recall_entries` (1-N). Ở đây chỉ còn phiếu
        # trả hàng do job KiotViet ghi ở mức đơn.
        return {"return_slip_code": ", ".join(recall.return_slip_code or [])}
```

- [ ] **Step 4: Serializer ghi**

Trong `kiotviet_landing_serializers.py`, `OrderRecallInputSerializer` **chỉ còn**:

```python
class OrderRecallInputSerializer(serializers.Serializer):
    # Contract giữ dạng chuỗi phẩy nhưng chứa N mã (mỗi mã <=64 ký tự, cắt ở
    # _parse_slip_codes) — 1024 đủ rộng cho danh sách mã dài.
    return_slip_code = serializers.CharField(max_length=1024, required=False, allow_blank=True, allow_null=True)
```

Thêm serializer mới ngay dưới nó:

```python
class OrderRecallEntryInputSerializer(serializers.Serializer):
    """Một nhóm thu hồi. Cả 3 ô đều optional vì nhân viên nhập dần: gõ mã hôm nay,
    điền ngày nhận vài hôm sau."""

    recall_code = serializers.CharField(
        max_length=64, required=False, allow_blank=True, allow_null=True
    )
    recall_created_date = serializers.DateField(required=False, allow_null=True)
    recall_received_date = serializers.DateField(required=False, allow_null=True)
```

Trong `OrderInlineUpdateSerializer`, cạnh dòng `recall = OrderRecallInputSerializer(...)`:

```python
    # many=True + full-replace, cùng khuôn `items` ngay trên.
    recall_entries = OrderRecallEntryInputSerializer(many=True, required=False)
```

và thêm `"recall_entries"` vào `Meta.fields`.

- [ ] **Step 5: Service — full-replace**

Trong `order_service_helpers.py`, `_upsert_order_recall`: bỏ 3 field khỏi `allowed` và `list_fields`, chỉ còn `return_slip_code`:

```python
        list_fields = {"return_slip_code"}
        allowed = ("return_slip_code",)
```

Thêm hàm mới ngay dưới `_upsert_order_recall`:

```python
    @staticmethod
    def _replace_order_recall_entries(order: Order, entries_payload: list[dict] | None, actor) -> None:
        """Full-replace tập nhóm thu hồi của đơn. None = không đụng tới.

        Xoá-rồi-tạo-lại thay vì đối chiếu theo id: payload luôn đến từ UI gửi trọn
        danh sách, nên diff theo id chỉ thêm nhánh mà không thêm thông tin. Cùng
        khuôn `_replace_order_items`.
        """
        if entries_payload is None:
            return

        rows: list[OrderRecallEntry] = []
        for item in entries_payload:
            code = str(item.get("recall_code") or "").strip()[:64]
            created = item.get("recall_created_date")
            received = item.get("recall_received_date")
            # Nhóm trống cả 3 ô = nhóm người dùng vừa xoá trên UI, không lưu.
            if not code and created is None and received is None:
                continue
            rows.append(
                OrderRecallEntry(
                    order=order,
                    recall_code=code,
                    recall_created_date=created,
                    recall_received_date=received,
                    updated_by=actor,
                )
            )

        order.recall_entries.all().delete()
        if rows:
            OrderRecallEntry.objects.bulk_create(rows)
        # Sau delete+bulk_create, cache prefetch do view nạp đã cũ và sẽ rò giá trị
        # cũ ngược vào response — mirror đúng cách `items` xử lý.
        if hasattr(order, "_prefetched_objects_cache"):
            order._prefetched_objects_cache.pop("recall_entries", None)
```

Thêm `OrderRecallEntry` vào khối import model ở đầu file.

- [ ] **Step 6: Gọi từ luồng cập nhật inline**

Trong `order_service_commands.py`, ở luồng `update_order_inline`, ngay cạnh chỗ gọi `_upsert_order_recall`, thêm:

```python
        OrderServiceCommands._replace_order_recall_entries(order, payload.get("recall_entries"), actor)
```

Đọc hàm đó trước để đặt đúng vị trí (phải nằm trong cùng `transaction.atomic()` với phần còn lại của cập nhật) và dùng đúng tên biến payload/actor mà hàm đang có. Nếu `payload` là `validated_data` thì `payload.get("recall_entries")` trả `None` khi client không gửi — đúng ý "không đụng tới".

- [ ] **Step 7: Export**

Trong `export_excel.py`, thay khối 4 key cũ bằng:

```python
        elif key in ("recall_code", "recall_created_date", "recall_received_date"):
            # 3 cột nay nằm trên N bản ghi OrderRecallEntry (1-N, "recall_entries").
            # Nối theo ĐÚNG thứ tự nhóm và GIỮ vị trí rỗng (không dồn): dồn lại sẽ
            # làm 3 cột lệch nhau, mất tính tương ứng theo vị trí giữa mã và ngày.
            entries = list(order.recall_entries.all())
            if key == "recall_code":
                val = ", ".join(entry.recall_code for entry in entries)
            elif key == "recall_created_date":
                val = ", ".join(
                    entry.recall_created_date.isoformat() if entry.recall_created_date else ""
                    for entry in entries
                )
            else:
                val = ", ".join(
                    entry.recall_received_date.isoformat() if entry.recall_received_date else ""
                    for entry in entries
                )
        elif key == "return_slip_code":
            # getattr thay vì order.recall trực tiếp: đơn không có bản ghi recall sẽ
            # ném RelatedObjectDoesNotExist nếu truy cập thẳng.
            recall = getattr(order, "recall", None)
            val = ", ".join(recall.return_slip_code or []) if recall else ""
```

- [ ] **Step 8: Prefetch + filter + tra cứu mã**

`repositories.py`:
- Thêm `"recall_entries"` vào **cả ba** danh sách `prefetch_related` đang có `"recall"` (khoảng dòng 191, 276, 1023). `recall` vẫn phải giữ trong `select_related` cho `return_slip_code`.
- Khối `has_recall_code` đổi sang bảng mới:

```python
            recall_exists = Exists(
                OrderRecallEntry.objects.filter(order_id=OuterRef("pk")).exclude(recall_code="")
            )
```

Đổi import `OrderRecall` → thêm `OrderRecallEntry` (giữ `OrderRecall` nếu còn dùng cho `has_return_slip`).

`shipping_lookup_view.py`:

```python
    recall_qs = (
        # Mỗi nhóm một mã nên equality là đủ (đảo ngược `__contains` của MR !1228,
        # vốn chỉ cần khi cột còn là mảng). distinct(): một đơn có 2 nhóm trùng mã
        # sẽ ra 2 dòng sau join.
        Order.objects.filter(recall_entries__recall_code=code, deleted_at__isnull=True)
        .select_related("kiotviet_channel", "assessment")
        .prefetch_related("items")
        .distinct()
        .order_by("-id")[:20]
    )
```

- [ ] **Step 9: Cập nhật test của các file liên quan**

- `test_shipping_lookup.py`: đổi mọi `OrderRecall.objects.create(order=..., recall_code=[...])` sang `OrderRecallEntry.objects.create(order=..., recall_code="...")`; giữ test tra trúng mã của nhóm thứ 2 (nay là entry thứ 2).
- `test_order_list_exists_filters.py`: đổi dữ liệu dựng cho `has_recall_code` sang `OrderRecallEntry`.
- `test_orders_export_definition.py`: đổi dữ liệu và assert — thêm case **nhóm thiếu ô để rỗng đúng vị trí**:

```python
def test_recall_columns_keep_positional_alignment(order_factory):
    """Nhóm 2 chưa có ngày nhận → ô ngày nhận là "15/08, " chứ không dồn lại."""
    order = order_factory()
    OrderRecallEntry.objects.create(
        order=order, recall_code="RC-1",
        recall_created_date=date(2026, 8, 12), recall_received_date=date(2026, 8, 15),
    )
    OrderRecallEntry.objects.create(
        order=order, recall_code="RC-2", recall_created_date=date(2026, 8, 20),
    )
    rows = order_row_values(order, [
        {"key": "recall_code", "header": "Mã đơn thu hồi"},
        {"key": "recall_received_date", "header": "Ngày nhận hàng thu hồi"},
    ])
    assert rows == ["RC-1, RC-2", "2026-08-15, "]
```

Đọc file để dùng đúng chữ ký `order_row_values` và đúng fixture đang có.

- [ ] **Step 10: Chạy test — phải XANH**

Run: pytest với `<paths>` = `apps/orders/tests/test_order_recall.py apps/orders/tests/test_shipping_lookup.py apps/orders/tests/test_order_list_exists_filters.py apps/orders/tests/test_orders_export_definition.py apps/orders/tests/test_order_create_api.py apps/orders/tests/test_order_inline_update_lock.py` (`POSTGRES_DB=desi_rg_t2`, `REDIS_CACHE_DB=8`)
Expected: PASS toàn bộ.

- [ ] **Step 11: Ruff + commit**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
git add backend/apps
git commit -m "feat(orders): expose recall entry groups through api and export"
```

---

### Task 3: Frontend — lưới 3 cột xếp chồng theo nhóm

**Files:**
- Modify: `frontend/src/features/orders/order-types.ts`
- Modify: `frontend/src/features/orders/OrdersTable/cell-renderers.tsx`
- Modify: `frontend/src/i18n/messages/orders.ts`
- Create: `frontend/src/features/orders/__tests__/recall-entries.test.tsx`

**Interfaces:**
- Consumes: contract `recall_entries` (Task 2).
- Produces: type `OrderRecallEntryRow` export từ `order-types.ts`; hàm `nextRecallEntries(entries, index, field, value)` export từ `cell-renderers.tsx` — Task 4 KHÔNG dùng lại (trang chi tiết có luồng patch riêng), nhưng test dùng.

> **RULING 2026-08-14 (đổi so với nội dung Step 1/4/5 bên dưới):** bỏ hẳn nút
> "+ thêm nhóm". Cả BA cột luôn render `entries.length + 1` input — luôn thừa
> đúng một dòng trống ở cuối để gõ thẳng vào, không cần bấm gì. Lý do: ba cột
> là ba cell render độc lập (không component cha chung), state cục bộ kiểu
> `useState` đếm dòng trống ở một cell không đồng bộ được sang hai cell kia —
> bấm nút chỉ cột đó dài ra, phá bất biến "ba cột luôn cao bằng nhau". Suy ra
> số dòng từ `entries.length` (giống nhau ở cả ba cell) thì ba cột căn hàng
> theo cấu trúc, không nhờ state chia sẻ. Snippet code, test case, và key i18n
> `orders.recall.addGroup` trong Step 1/4/5 dưới đây là bản GỐC trước ruling —
> giữ lại làm hồ sơ, xem `task-3-report.md` cho code thật đã chạy.

- [ ] **Step 1: Viết test đỏ**

Create `frontend/src/features/orders/__tests__/recall-entries.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import type { OrderRow } from "@/features/orders/order-types";
import { nextRecallEntries } from "@/features/orders/OrdersTable/cell-renderers";
import { OrderCell } from "@/features/orders/OrdersTable/OrderCell";
import { I18nProvider } from "@/i18n";

const BASE_PROPS = {
  kiotvietOptions: [],
  addressCatalog: { provinces: [], districts_by_province: {}, wards_by_district_key: {} },
  channelStaffByCode: {},
};

const ROW = {
  id: 1,
  can_edit: true,
  recall_entries: [
    { id: 1, recall_code: "RC-01", recall_created_date: "2026-08-12", recall_received_date: "2026-08-15" },
    { id: 2, recall_code: "RC-02", recall_created_date: "2026-08-20", recall_received_date: null },
  ],
} as unknown as OrderRow;

describe("nextRecallEntries", () => {
  it("đổi đúng một ô, giữ nguyên các nhóm khác", () => {
    const entries = [
      { recall_code: "A", recall_created_date: null, recall_received_date: null },
      { recall_code: "B", recall_created_date: null, recall_received_date: null },
    ];
    expect(nextRecallEntries(entries, 1, "recall_code", "B2")).toEqual([
      { recall_code: "A", recall_created_date: null, recall_received_date: null },
      { recall_code: "B2", recall_created_date: null, recall_received_date: null },
    ]);
  });

  it("index vượt độ dài thì thêm nhóm mới ở cuối", () => {
    expect(nextRecallEntries([], 0, "recall_code", "A")).toEqual([
      { recall_code: "A", recall_created_date: null, recall_received_date: null },
    ]);
  });
});

describe("cột thu hồi trên lưới", () => {
  it("render một input cho mỗi nhóm", () => {
    render(
      <I18nProvider>
        <OrderCell {...BASE_PROPS} columnKey="recall_code" row={ROW} onPatch={vi.fn()} />
      </I18nProvider>,
    );
    expect(screen.getAllByRole("textbox")).toHaveLength(2);
  });

  it("sửa một ô gửi lại CẢ danh sách nhóm", async () => {
    const onPatch = vi.fn().mockResolvedValue(undefined);
    render(
      <I18nProvider>
        <OrderCell {...BASE_PROPS} columnKey="recall_code" row={ROW} onPatch={onPatch} />
      </I18nProvider>,
    );
    const inputs = screen.getAllByRole("textbox");
    await userEvent.clear(inputs[0]);
    await userEvent.type(inputs[0], "RC-99");
    await userEvent.tab();
    expect(onPatch).toHaveBeenCalledWith(1, {
      recall_entries: [
        { recall_code: "RC-99", recall_created_date: "2026-08-12", recall_received_date: "2026-08-15" },
        { recall_code: "RC-02", recall_created_date: "2026-08-20", recall_received_date: null },
      ],
    });
  });

  it("nút thêm nhóm chỉ ở cột mã", () => {
    render(
      <I18nProvider>
        <OrderCell {...BASE_PROPS} columnKey="recall_code" row={ROW} onPatch={vi.fn()} />
      </I18nProvider>,
    );
    expect(screen.getByRole("button", { name: /thêm nhóm/i })).toBeInTheDocument();
  });
});
```

Đọc `shipping-type.test.tsx` cùng thư mục trước để copy đúng khuôn `BASE_PROPS` và cách import — nếu `OrderCell` cần prop khác thì bám file đó, đừng bám snippet này.

- [ ] **Step 2: Chạy test — xác nhận ĐỎ**

Run: `cd frontend && npx vitest run src/features/orders/__tests__/recall-entries.test.tsx`
Expected: FAIL (`nextRecallEntries` chưa tồn tại).

- [ ] **Step 3: Kiểu dữ liệu**

Trong `order-types.ts`:

```ts
/** Một đợt thu hồi. `id` chỉ có ở dữ liệu server trả về; payload ghi không cần. */
export type OrderRecallEntryRow = {
  id?: number;
  recall_code: string;
  recall_created_date: string | null;
  recall_received_date: string | null;
};
```

Trong `OrderRow`, cạnh `recall`:

```ts
  recall_entries?: OrderRecallEntryRow[];
```

Trong `OrderRecallRow`, **xoá** `recall_code`, `recall_created_date`, `recall_received_date`; chỉ giữ `return_slip_code`.

- [ ] **Step 4: Hàm dựng payload + renderer**

Trong `cell-renderers.tsx`, thay `renderRecallText` và `renderRecallDate` bằng:

```tsx
type RecallEntryField = "recall_code" | "recall_created_date" | "recall_received_date";

/**
 * Dựng mảng nhóm mới từ một ô vừa sửa. Backend nhận full-replace nên mọi lần sửa
 * đều phải gửi trọn danh sách — trả về mảng đã bỏ `id` (backend xoá-tạo lại).
 */
export function nextRecallEntries(
  entries: OrderRecallEntryRow[],
  index: number,
  field: RecallEntryField,
  value: string | null,
): OrderRecallEntryRow[] {
  const base = entries.map((entry) => ({
    recall_code: entry.recall_code ?? "",
    recall_created_date: entry.recall_created_date ?? null,
    recall_received_date: entry.recall_received_date ?? null,
  }));
  while (base.length <= index) {
    base.push({ recall_code: "", recall_created_date: null, recall_received_date: null });
  }
  base[index] = { ...base[index], [field]: value };
  return base;
}

function renderRecallEntryColumn(
  row: OrderRow,
  onPatch:
    | ((id: number, payload: Partial<OrderRow>, silent?: boolean) => Promise<void>)
    | undefined,
  field: RecallEntryField,
  t: (key: string, fallback?: string) => string,
) {
  const canEdit = !!row.can_edit && !!onPatch;
  const entries = row.recall_entries ?? [];
  // Luôn có ít nhất một dòng để nhân viên gõ ngay vào đơn chưa có nhóm nào.
  const rowsToRender = entries.length > 0 ? entries : [null];
  const isCodeColumn = field === "recall_code";
  const inputType = isCodeColumn ? "text" : "date";

  return (
    <div className="flex flex-col gap-1">
      {rowsToRender.map((entry, index) => {
        const raw = entry ? entry[field] : null;
        const value = isCodeColumn ? (raw ?? "") : raw ? String(raw).slice(0, 10) : "";
        return (
          <input
            key={entry?.id ?? `new-${index}`}
            type={inputType}
            className={`h-7 w-full ${isCodeColumn ? "min-w-[130px]" : "min-w-[140px]"} rounded border border-border bg-transparent px-1.5 text-xs focus:outline-hidden focus:ring-1 focus:ring-ring disabled:cursor-default disabled:opacity-60`}
            defaultValue={value}
            disabled={!canEdit}
            onClick={(e) => e.stopPropagation()}
            onBlur={(e) => {
              const next = isCodeColumn ? e.target.value.trim() : e.target.value;
              if (next === value) return;
              onPatch?.(row.id, {
                recall_entries: nextRecallEntries(entries, index, field, next || null),
              } as Partial<OrderRow>);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            }}
          />
        );
      })}
      {isCodeColumn && canEdit ? (
        <button
          type="button"
          className="h-6 w-full rounded border border-dashed border-border text-[11px] text-muted-foreground hover:bg-muted/50"
          onClick={(e) => {
            e.stopPropagation();
            onPatch?.(row.id, {
              recall_entries: [
                ...entries.map((entry) => ({
                  recall_code: entry.recall_code ?? "",
                  recall_created_date: entry.recall_created_date ?? null,
                  recall_received_date: entry.recall_received_date ?? null,
                })),
                { recall_code: "", recall_created_date: null, recall_received_date: null },
              ],
            } as Partial<OrderRow>);
          }}
        >
          {t("orders.recall.addGroup", "+ thêm nhóm")}
        </button>
      ) : null}
    </div>
  );
}
```

**Lưu ý về nút thêm nhóm:** nhóm mới rỗng hoàn toàn nên backend sẽ loại nó khi lưu. Vì vậy nút này **không** gọi `onPatch` để lưu một nhóm rỗng — thay vào đó nó chỉ có ý nghĩa khi kèm dữ liệu. Cách xử lý: giữ nguyên như trên (gửi luôn), backend loại nhóm rỗng, và ô input thứ N+1 vẫn hiện ra ở lần render kế nhờ `rowsToRender`. Nếu cách này làm nhóm mới biến mất sau khi server trả về, chuyển sang state cục bộ trong component (`useState<number>` đếm số dòng trống thêm) và **báo lại trong report** — đây là điểm plan không chốt được nếu không chạy thật.

Cập nhật `switch` trong `renderCell`:

```tsx
    case "recall_code":
      return renderRecallEntryColumn(row, onPatch, "recall_code", t);
    case "recall_created_date":
      return renderRecallEntryColumn(row, onPatch, "recall_created_date", t);
    case "recall_received_date":
      return renderRecallEntryColumn(row, onPatch, "recall_received_date", t);
    case "return_slip_code":
      return renderReturnSlipText(row, onPatch);
```

`renderReturnSlipText` là bản rút gọn của `renderRecallText` cũ, chỉ phục vụ `return_slip_code` (vẫn patch `{ recall: { return_slip_code: ... } }`) — giữ nguyên thân hàm cũ, chỉ bỏ tham số `field`.

Nếu `renderCell` chưa có sẵn biến `t` trong phạm vi thì lấy từ đúng chỗ mà các nhánh khác trong file đang lấy — đọc file trước.

- [ ] **Step 5: i18n**

`frontend/src/i18n/messages/orders.ts`, khối `vi`: `"orders.recall.addGroup": "+ thêm nhóm",`
khối `en`: `"orders.recall.addGroup": "+ add group",`

- [ ] **Step 6: Chạy test — phải XANH**

Run: `npx vitest run src/features/orders/__tests__/recall-entries.test.tsx`
Expected: PASS.

- [ ] **Step 7: tsc + commit**

```bash
cd frontend && npx tsc --noEmit
git add frontend/src
git commit -m "feat(orders): stack recall entry groups in orders grid"
```

---

### Task 4: Frontend — trang chi tiết (`BlockRecall`)

**Files:**
- Modify: `frontend/src/features/orders/BlockRecall.tsx`
- Modify: `frontend/src/i18n/messages/orders.ts`
- Test: `frontend/src/features/orders/__tests__/recall-entries.test.tsx`

**Interfaces:**
- Consumes: `OrderRecallEntryRow`, `OrderRow.recall_entries` (Task 3); `patch: (p: Partial<OrderRow>) => void` do `OrderDetailPage` truyền vào.
- Produces: không có task nào phía sau dựa vào.

- [ ] **Step 1: Viết test đỏ**

Thêm vào `frontend/src/features/orders/__tests__/recall-entries.test.tsx`:

```tsx
describe("BlockRecall — trang chi tiết", () => {
  it("hiện một cụm cho mỗi nhóm, đánh số theo thứ tự", () => {
    render(
      <I18nProvider>
        <BlockRecall order={ROW} patch={vi.fn()} />
      </I18nProvider>,
    );
    expect(screen.getByText(/Đợt thu hồi #1/)).toBeInTheDocument();
    expect(screen.getByText(/Đợt thu hồi #2/)).toBeInTheDocument();
  });

  it("nút thêm đợt gửi thêm một nhóm rỗng vào cuối", async () => {
    const patch = vi.fn();
    render(
      <I18nProvider>
        <BlockRecall order={ROW} patch={patch} />
      </I18nProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: /Thêm đợt thu hồi/i }));
    expect(patch).toHaveBeenCalledWith({
      recall_entries: [
        { recall_code: "RC-01", recall_created_date: "2026-08-12", recall_received_date: "2026-08-15" },
        { recall_code: "RC-02", recall_created_date: "2026-08-20", recall_received_date: null },
        { recall_code: "", recall_created_date: null, recall_received_date: null },
      ],
    });
  });

  it("nút xoá bỏ đúng nhóm đó khỏi danh sách", async () => {
    const patch = vi.fn();
    render(
      <I18nProvider>
        <BlockRecall order={ROW} patch={patch} />
      </I18nProvider>,
    );
    await userEvent.click(screen.getAllByRole("button", { name: /Xoá đợt/i })[0]);
    expect(patch).toHaveBeenCalledWith({
      recall_entries: [
        { recall_code: "RC-02", recall_created_date: "2026-08-20", recall_received_date: null },
      ],
    });
  });
});
```

Thêm `import { BlockRecall } from "@/features/orders/BlockRecall";` vào đầu file test.

- [ ] **Step 2: Chạy test — xác nhận ĐỎ**

Run: `npx vitest run src/features/orders/__tests__/recall-entries.test.tsx`
Expected: FAIL cho 3 test mới.

- [ ] **Step 3: Viết lại `BlockRecall`**

```tsx
import { memo } from "react";
import { Plus, Trash2, Undo2 } from "lucide-react";
import {
  type OrderRecallEntryRow,
  type OrderRecallRow,
  type OrderRow,
} from "@/features/orders/order-types";
import { ERow, SectionCard, type SectionVariant } from "@/features/orders/order-detail-shared";
import { useI18n } from "@/i18n";

/** Bỏ `id` trước khi gửi: backend full-replace (xoá-tạo lại) nên id không có nghĩa. */
function toPayload(entries: OrderRecallEntryRow[]): OrderRecallEntryRow[] {
  return entries.map((entry) => ({
    recall_code: entry.recall_code ?? "",
    recall_created_date: entry.recall_created_date ?? null,
    recall_received_date: entry.recall_received_date ?? null,
  }));
}

function BlockRecallImpl({
  order,
  patch,
  variant,
}: {
  order: OrderRow;
  patch: (p: Partial<OrderRow>) => void;
  variant?: SectionVariant;
}) {
  const { t } = useI18n();
  const entries = order.recall_entries ?? [];

  const patchEntries = (next: OrderRecallEntryRow[]) => {
    patch({ recall_entries: next } as Partial<OrderRow>);
  };

  const updateField = (index: number, field: keyof OrderRecallEntryRow, value: string | null) => {
    const next = toPayload(entries);
    next[index] = { ...next[index], [field]: value };
    patchEntries(next);
  };

  // Chỉ gửi đúng field user vừa sửa; OrderDetailPage deep-merge `recall` vào pendingRows.
  const patchRecall = (fields: Partial<OrderRecallRow>) => {
    patch({ recall: fields as OrderRecallRow });
  };

  return (
    <SectionCard
      icon={<Undo2 className="h-4 w-4" />}
      title={t("orders.recall.title")}
      variant={variant}
    >
      {entries.map((entry, index) => (
        <div key={entry.id ?? `new-${index}`} className="rounded border border-border/60 p-2">
          <div className="mb-1 flex items-center justify-between">
            <span className="text-xs font-medium text-muted-foreground">
              {t("orders.recall.groupTitle", "Đợt thu hồi")} #{index + 1}
            </span>
            <button
              type="button"
              aria-label={`${t("orders.recall.removeGroup", "Xoá đợt")} #${index + 1}`}
              className="text-muted-foreground hover:text-destructive"
              onClick={() => patchEntries(toPayload(entries.filter((_, i) => i !== index)))}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
          <ERow
            label={t("orders.columns.recallCode")}
            rawValue={entry.recall_code ?? ""}
            type="text"
            onSave={(v) => updateField(index, "recall_code", String(v))}
          />
          <ERow
            label={t("orders.columns.recallCreatedDate")}
            rawValue={entry.recall_created_date ?? ""}
            type="date"
            onSave={(v) => updateField(index, "recall_created_date", v ? String(v) : null)}
          />
          <ERow
            label={t("orders.columns.recallReceivedDate")}
            rawValue={entry.recall_received_date ?? ""}
            type="date"
            onSave={(v) => updateField(index, "recall_received_date", v ? String(v) : null)}
          />
        </div>
      ))}

      <button
        type="button"
        className="flex items-center gap-1 text-xs text-primary hover:underline"
        onClick={() =>
          patchEntries([
            ...toPayload(entries),
            { recall_code: "", recall_created_date: null, recall_received_date: null },
          ])
        }
      >
        <Plus className="h-3.5 w-3.5" />
        {t("orders.recall.addEntry", "Thêm đợt thu hồi")}
      </button>

      <ERow
        label={t("orders.columns.returnSlipCode")}
        rawValue={order.recall?.return_slip_code ?? ""}
        type="text"
        onSave={(v) => patchRecall({ return_slip_code: String(v) })}
      />
    </SectionCard>
  );
}

export const BlockRecall = memo(BlockRecallImpl);
```

Đọc `order-detail-shared.tsx` trước để xác nhận `SectionCard` chấp nhận children hỗn hợp (không chỉ `ERow`) — nếu nó ép layout dạng bảng thì bọc phần nhóm trong đúng phần tử mà file đó dùng, ĐỪNG tự chế markup mới.

Ô "Loại đơn KOC" hiện có trong `BlockRecall`: **giữ nguyên vị trí trên cùng** — đoạn code trên bỏ sót nó vì mỗi bản repo một khác; đọc file hiện tại và giữ lại y nguyên khối `ERow` của `koc_order_type` ở đầu `SectionCard`.

- [ ] **Step 4: i18n**

Khối `vi`:
```ts
    "orders.recall.groupTitle": "Đợt thu hồi",
    "orders.recall.addEntry": "Thêm đợt thu hồi",
    "orders.recall.removeGroup": "Xoá đợt",
```
Khối `en`:
```ts
    "orders.recall.groupTitle": "Recall batch",
    "orders.recall.addEntry": "Add recall batch",
    "orders.recall.removeGroup": "Remove batch",
```

- [ ] **Step 5: Chạy test — phải XANH**

Run: `npx vitest run src/features/orders/__tests__/recall-entries.test.tsx`
Expected: PASS toàn bộ file.

- [ ] **Step 6: tsc + lint + commit**

```bash
cd frontend && npx tsc --noEmit && npm run lint:strict
git add frontend/src
git commit -m "feat(orders): edit recall entry groups on order detail"
```

---

### Task 5: Chốt — schema, migration leaf, suite đầy đủ

**Files:**
- Modify: `backend/schema.yaml`
- Modify: `docs/superpowers/specs/2026-08-14-koc-recall-entry-groups-design.md` (bảng Sprint)

**Interfaces:**
- Consumes: toàn bộ Task 1-4.
- Produces: nhánh sẵn sàng mở MR.

- [ ] **Step 1: Xác nhận một leaf migration + không drift**

Run trong container test:
```bash
python manage.py makemigrations --check --dry-run && python manage.py showmigrations orders | tail -6
```
Expected: `No changes detected`, chuỗi `0126 → 0127 → 0128 → 0129` tuyến tính. Nếu có 2 leaf → tạo migration merge, KHÔNG sửa lịch sử.

- [ ] **Step 2: Chạy thử migrate + rollback trên DB sạch**

Run trong container test:
```bash
python manage.py migrate orders 0129 && python manage.py migrate orders 0126 && python manage.py migrate orders 0129
```
Expected: cả ba lượt thành công. Bước này xác nhận `copy_backward` chạy được thật, không chỉ tồn tại trên giấy.

- [ ] **Step 3: Sửa tay `schema.yaml`**

Thêm component `OrderRecallEntry` (3 field + `id` readOnly) và `PatchedOrderRecallEntryInput`; thêm `recall_entries` vào `Order`, `OrderList`, `PatchedOrderInlineUpdate`; **xoá** `recall_code` / `recall_created_date` / `recall_received_date` khỏi component `OrderRecallInput` và `OrderRecall`. Đừng đụng các field cùng tên `recall_code` của app `warranty` (model khác, vẫn là chuỗi đơn).

**Sửa tay, tối thiểu, KHÔNG regen toàn file** — `schema.yaml` đang stale sẵn, regen sẽ kéo nhiễu của feature khác vào MR. Ba bẫy: (1) `ChoiceField` bị hoist thành `$ref` tới component `Enum` riêng; (2) mảng `required` sắp xếp theo alphabet; (3) không param nào có `explode`/`style`.

- [ ] **Step 4: Suite backend app `orders`**

Chạy bằng `docker run -d --name rg-final-test ...` rồi vòng lặp chờ `docker inspect -f '{{.State.Running}}' rg-final-test` = `false`, rồi `docker logs`. Dùng `POSTGRES_DB=desi_rg_final`, `REDIS_CACHE_DB=9`. Dọn container cũ trước (`docker ps -a | grep rg-`).
Expected: không failure nào liên quan `recall`.

- [ ] **Step 5: Suite frontend đầy đủ**

Run: `cd frontend && npm run test:ci`
Expected: chỉ còn failure đã có sẵn trên master. **Đo baseline master tại thời điểm chạy** trước khi kết luận cái nào là hồi quy — ghi chú baseline cũ đã sai nhiều lần.

- [ ] **Step 6: Ruff + cập nhật bảng Sprint + commit**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

Đổi 5 dòng trạng thái trong bảng Sprint của spec từ `⬜ Chưa làm` sang `✅ Xong`, ghi ngắn gọn cái gì đã làm / còn nợ.

```bash
git add -A && git commit -m "chore(orders): update openapi schema for recall entry groups"
```

**KHÔNG push, KHÔNG tạo MR** — controller làm sau khi review toàn nhánh.

---

## Self-Review

**Spec coverage:**

| Yêu cầu trong spec | Task |
|---|---|
| Bảng `order_recall_entries` 1-N, `ordering=["id"]`, 2 index btree | 1 |
| `OrderRecall` chỉ còn `return_slip_code`, bỏ GIN index | 1 |
| Data migration 4 luật (nhiều mã / chỉ ngày / trống / mã rỗng) | 1 (Step 4 + test Step 6) |
| Reverse gộp về nhóm đầu tiên | 1 (Step 4), kiểm thật ở 5 (Step 2) |
| Response `recall_entries` + `recall` chỉ còn phiếu trả hàng | 2 (Step 3) |
| Ghi full-replace, nhóm rỗng bị loại, vắng mặt = không đụng | 2 (Step 1, 4, 5) |
| Export giữ vị trí rỗng, không dồn | 2 (Step 7, 9) |
| `has_recall_code` sang bảng mới | 2 (Step 8) |
| Tra cứu mã equality + distinct | 2 (Step 8) |
| Lưới 3 cột xếp chồng, luôn thừa 1 dòng trống cuối để gõ thêm nhóm | 3 |
| Trang chi tiết: danh sách nhóm, thêm/xoá, phiếu trả hàng riêng | 4 |
| Không đụng `koc_order_type`, `return_slip_code`, job sync | Ràng buộc toàn cục; Task 4 Step 3 nhắc giữ nguyên ô `koc_order_type` |
| `schema.yaml` sửa tay | 5 (Step 3) |
| Runbook migrate/rollback | Đã nằm trong spec §4; Task 5 Step 2 kiểm rollback chạy được |

**Type consistency:** `OrderRecallEntryRow` (`{id?, recall_code, recall_created_date, recall_received_date}`) dùng thống nhất ở Task 3 (`order-types.ts`, `nextRecallEntries`) và Task 4 (`BlockRecall`, `toPayload`). Key backend `recall_entries` thống nhất giữa serializer đọc (Task 2 Step 3), serializer ghi (Step 4), service (Step 5), prefetch (Step 8), và payload frontend (Task 3, 4).

**Điểm plan cố tình để executor tự đọc file** (không phải placeholder — là chỗ plan KHÔNG được đoán): tên helper tạo đơn trong từng file test; vị trí gọi `_upsert_order_recall` trong `order_service_commands.py`; `SectionCard` có ép layout không; `renderCell` lấy `t` từ đâu; khối `ERow` của `koc_order_type` hiện đang nằm ở đâu trong `BlockRecall`. Mọi chỗ đều kèm chỉ dẫn "đọc file trước, đừng đoán".

**Một điểm plan không chốt được (ĐÃ CHỐT khi thực thi, xem ruling 2026-08-14 ở Task 3):** hành vi của nút "+ thêm nhóm" trên lưới sau khi server loại nhóm rỗng (Task 3 Step 4) — executor ban đầu chuyển sang state cục bộ (`useState` đếm dòng trống) như plan gợi ý, nhưng self-review phát hiện đây là 3 cell render ĐỘC LẬP (một switch case riêng mỗi cột) nên state không đồng bộ được giữa 3 cột: bấm nút chỉ cột mã dài ra, hai cột ngày đứng yên — phá bất biến "ba cột luôn cao bằng nhau" (spec §3.3). Ruling cuối: **bỏ hẳn nút**, cả ba cột luôn render `entries.length + 1` input (Task 3 Step 4 cập nhật tương ứng).
