# KOC: nhiều mã thu hồi + loại đơn KOC — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đơn KOC lưu được nhiều mã đơn thu hồi, và có trường phân loại (Tặng mẫu / Gia hạn / Thu hồi) lọc được trên trang danh sách.

**Architecture:** `OrderRecall.recall_code` đổi từ `CharField` sang `ArrayField` theo đúng khuôn migration `0101` đã làm cho `return_slip_code` (contract API giữ dạng chuỗi phẩy). Thêm `Order.koc_order_type` là `TextChoices` theo đúng khuôn `Order.shipping_type` (MR trước), kèm bộ lọc multi-value và cột bảng chỉ hiện ở trang KOC.

**Tech Stack:** Django 5 + DRF + PostgreSQL (ArrayField, GIN, partial index), React + TypeScript + vitest.

**Spec:** `docs/superpowers/specs/2026-08-13-koc-order-type-and-multi-recall-codes-design.md`

## Global Constraints

- Mọi comment/nhãn người dùng thấy: **tiếng Việt**. Comment giải thích "tại sao", không mô tả lại code.
- Không tạo file test mới rời rạc cho từng thay đổi nhỏ — chèn vào file test sẵn có theo bảng ở mục Kiểm thử của spec. Ngoại lệ duy nhất: `frontend/src/features/orders/__tests__/koc-order-type.test.tsx` (mirror `shipping-type.test.tsx` đã tồn tại).
- Backend lint bắt buộc: `ruff check .` phải sạch (ruleset trong `backend/pyproject.toml`).
- **Không** backfill dữ liệu từ ô ghi chú. **Không** đụng nội dung ghi chú cũ.
- Contract API của `recall_code` **giữ nguyên** dạng chuỗi phẩy (`"RC-01, RC-02"`), **giữ nguyên tên field**.
- Repo **không có CI** (`.gitlab-ci.yml` đã bị xoá ở `eb15fe28`) — mọi lint/test phải chạy tay trước khi push.

### Lệnh chạy test

Backend (container `desi-test-pg` / `desi-test-redis` / network `desi-test-net` đã sống sẵn; **đổi `POSTGRES_DB` và `REDIS_CACHE_DB` mỗi lần chạy song song**):

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-wt-koc-type && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_koc_t1 \
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

Frontend (`npm ci` đã chạy xong trong worktree — **đừng** dùng junction node_modules, vitest 3 vỡ dual-instance):

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-wt-koc-type/frontend && npx vitest run <file>
```

---

## File Structure

**Backend — sửa:**

| File | Trách nhiệm sau thay đổi |
|---|---|
| `backend/apps/orders/models.py` | `OrderRecall.recall_code` là `ArrayField` + GIN index; `KocOrderType` + `Order.koc_order_type` + partial index |
| `backend/apps/orders/migrations/0124_orderrecall_recall_code_array.py` | **Tạo mới** — đổi kiểu cột + đổi index |
| `backend/apps/orders/migrations/0125_order_koc_order_type.py` | **Tạo mới** — thêm cột + partial index |
| `…/serializers/order_serializers_impl/order_core_serializers.py` | `get_recall` join mảng; `koc_order_type` vào field list |
| `…/serializers/order_serializers_impl/kiotviet_landing_serializers.py` | `OrderRecallInputSerializer.recall_code` nới 1024 |
| `…/serializers/order_serializers_impl/order_action_serializers.py` | `recall_code` lúc tạo đơn nới 1024 |
| `…/application/services/order_service_helpers.py` | `recall_code` đi qua `_parse_slip_codes` |
| `…/views/shipping_lookup_view.py` | Tra cứu bằng `__contains` |
| `backend/apps/orders/export_excel.py` | Join mã thu hồi; nhãn tiếng Việt cho `koc_order_type` |
| `backend/apps/orders/repositories.py` | `has_recall_code` theo `__len__gt`; filter `koc_order_type` |
| `…/views/order_views_impl/_view_helpers.py` + `sheet3_view.py` | Parse filter `koc_order_type` (2 nơi, parity) |

**Frontend — sửa:** `order-types.ts`, `order-storage.ts`, `use-orders-filters.ts`, `use-orders-data.ts`, `orders-page/use-orders-filter-options.ts`, `orders-page/use-orders-filter-handlers.ts`, `OrdersFilterPanel.tsx`, `OrdersTable/OrderCell.tsx`, `order-import-format.ts`, `pages/OrdersPage.tsx`, `app/routed-pages.tsx`, `i18n/messages/orders.ts`.

**Frontend — tạo:** `frontend/src/features/orders/__tests__/koc-order-type.test.tsx`.

---

### Task 1: `recall_code` → ArrayField (model + migration)

**Files:**
- Modify: `backend/apps/orders/models.py:915` (field) và `:936` (index)
- Create: `backend/apps/orders/migrations/0124_orderrecall_recall_code_array.py`

**Interfaces:**
- Consumes: không có (task đầu).
- Produces: `OrderRecall.recall_code: list[str]` (trước là `str`). Index GIN tên `order_recall_code_gin`. Task 2 dựa vào kiểu mảng này.

- [ ] **Step 1: Xác nhận leaf migration hiện tại chỉ có 1**

Run: `cd /c/Users/MYPC/Desktop/workspace/desi-order-wt-koc-type/backend/apps/orders/migrations && ls | tail -5`
Expected: file mới nhất là `0123_merge_shipping_type_product_components.py`. Nếu thấy 2 leaf khác nhau, DỪNG và báo — master vừa có sự cố trùng leaf (`93c59be9`).

- [ ] **Step 2: Đổi field + index trong `models.py`**

Trong `class OrderRecall`, đổi dòng `recall_code`:

```python
    # Nhiều mã: một đơn KOC có thể phát sinh nhiều đơn thu hồi (thu hồi nhiều đợt,
    # hoặc mã bị huỷ-tạo lại). Contract API vẫn là chuỗi phẩy — xem `get_recall`.
    recall_code = ArrayField(                                                      # Mã đơn thu hồi (nhiều mã)
        models.CharField(max_length=64),
        default=list,
        blank=True,
    )
```

Trong `class Meta` của `OrderRecall`, thay dòng index cũ:

```python
        indexes = [
            # GIN chứ không btree: btree vô dụng với kiểu mảng, mà tra cứu mã vận đơn
            # (shipping_lookup_view) filter `recall_code__contains` trên hot path quét kho.
            GinIndex(fields=["recall_code"], name="order_recall_code_gin"),
            models.Index(fields=["created_at"], name="order_recall_created_idx"),
        ]
```

Thêm import ở đầu file nếu chưa có: `from django.contrib.postgres.indexes import GinIndex`. `ArrayField` đã được import sẵn (dùng cho `return_slip_code`) — kiểm tra dòng import hiện có, không import trùng.

- [ ] **Step 3: Tạo migration**

Create `backend/apps/orders/migrations/0124_orderrecall_recall_code_array.py`:

```python
import django.contrib.postgres.fields
from django.contrib.postgres.indexes import GinIndex
from django.db import migrations, models

# Cùng lý do với 0101 (return_slip_code): Postgres cấm subquery trong transform
# expression của ALTER COLUMN ... USING, nên phần split/trim/drop-empty/truncate
# (khớp semantics _parse_slip_codes) phải nằm trong 1 SQL function riêng, gọi từ
# USING, rồi drop ngay sau khi dùng — không để lại rác trong schema.
FORWARD_SQL = """
-- LOCAL: chỉ trong transaction của migration này. SET thường sẽ dính lại trên
-- connection sau COMMIT và làm các migration sau đó abort sớm khi chờ khoá.
SET LOCAL lock_timeout = '5s';

CREATE OR REPLACE FUNCTION __split_recall_code_0124(input_value varchar)
    RETURNS varchar(64)[] AS $$
        SELECT CASE
            WHEN input_value IS NULL OR btrim(input_value) = '' THEN ARRAY[]::varchar(64)[]
            ELSE COALESCE(
                (
                    SELECT array_agg(left(btrim(part), 64))
                    FROM unnest(string_to_array(input_value, ',')) AS part
                    WHERE btrim(part) <> ''
                ),
                ARRAY[]::varchar(64)[]
            )
        END;
    $$ LANGUAGE sql IMMUTABLE;

DROP INDEX IF EXISTS order_recall_code_idx;

ALTER TABLE order_recalls
    ALTER COLUMN recall_code DROP DEFAULT,
    ALTER COLUMN recall_code TYPE varchar(64)[]
        USING __split_recall_code_0124(recall_code);
ALTER TABLE order_recalls ALTER COLUMN recall_code SET DEFAULT '{}';
ALTER TABLE order_recalls ALTER COLUMN recall_code SET NOT NULL;

CREATE INDEX order_recall_code_gin ON order_recalls USING GIN (recall_code);

DROP FUNCTION __split_recall_code_0124(varchar);
"""

REVERSE_SQL = """
SET LOCAL lock_timeout = '5s';

DROP INDEX IF EXISTS order_recall_code_gin;

ALTER TABLE order_recalls
    ALTER COLUMN recall_code DROP DEFAULT,
    ALTER COLUMN recall_code TYPE varchar(64)
        USING left(COALESCE(array_to_string(recall_code, ', '), ''), 64);
ALTER TABLE order_recalls ALTER COLUMN recall_code SET DEFAULT '';

CREATE INDEX order_recall_code_idx ON order_recalls (recall_code);
"""


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0123_merge_shipping_type_product_components"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
            ],
            state_operations=[
                migrations.RemoveIndex(
                    model_name="orderrecall",
                    name="order_recall_code_idx",
                ),
                migrations.AlterField(
                    model_name="orderrecall",
                    name="recall_code",
                    field=django.contrib.postgres.fields.ArrayField(
                        base_field=models.CharField(max_length=64),
                        blank=True,
                        default=list,
                        size=None,
                    ),
                ),
                migrations.AddIndex(
                    model_name="orderrecall",
                    index=GinIndex(fields=["recall_code"], name="order_recall_code_gin"),
                ),
            ],
        ),
    ]
```

- [ ] **Step 4: Xác nhận state model khớp DB — không sinh thêm migration**

Run:
```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-wt-koc-type && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_koc_t1 -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= \
  -e REDIS_CACHE_DB=7 -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest python manage.py makemigrations --check --dry-run
```
Expected: `No changes detected`. Nếu báo có thay đổi → state_operations chưa khớp `models.py`, sửa cho khớp rồi chạy lại.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/models.py backend/apps/orders/migrations/0124_orderrecall_recall_code_array.py
git commit -m "feat(orders): store recall codes as array"
```

---

### Task 2: Cập nhật mọi điểm đọc/ghi `recall_code`

**Files:**
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py:351`
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py:264`
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/order_action_serializers.py:26-28`
- Modify: `backend/apps/orders/application/services/order_service_helpers.py:952-964`
- Modify: `backend/apps/orders/transport/views/shipping_lookup_view.py:133`
- Modify: `backend/apps/orders/export_excel.py:118-119`
- Modify: `backend/apps/orders/repositories.py:853-861`
- Test: `backend/apps/orders/tests/test_order_recall.py`, `backend/apps/orders/tests/test_shipping_lookup.py`, `backend/apps/orders/tests/test_orders_export_definition.py`

**Interfaces:**
- Consumes: `OrderRecall.recall_code: list[str]` (Task 1).
- Produces: contract API `recall_code` là chuỗi phẩy cả chiều đọc lẫn chiều ghi. Task 5 (FE) dựa vào contract này — FE **không** đổi kiểu dữ liệu.

- [ ] **Step 1: Viết test đỏ cho nhiều mã**

Thêm vào `backend/apps/orders/tests/test_order_recall.py` (giữ nguyên style/khuôn của các test đã có trong file — đọc file trước khi thêm):

```python
def test_inline_update_stores_multiple_recall_codes(self):
    """Chuỗi phẩy vào → mảng đã chuẩn hoá trong DB, chuỗi phẩy ra ở response."""
    order = self._make_order()
    resp = self.client.patch(
        f"/api/orders/{order.id}",
        {"recall": {"recall_code": " RC-01 , ,RC-02,RC-01 "}},
        format="json",
    )
    self.assertEqual(resp.status_code, 200)
    order.refresh_from_db()
    # strip, bỏ phần tử rỗng, dedupe giữ thứ tự — semantics _parse_slip_codes
    self.assertEqual(order.recall.recall_code, ["RC-01", "RC-02"])
    self.assertEqual(resp.data["recall"]["recall_code"], "RC-01, RC-02")


def test_patch_without_recall_code_keeps_existing_codes(self):
    """PATCH chỉ đổi ngày không được xoá mảng mã đang có."""
    order = self._make_order()
    OrderRecall.objects.create(order=order, recall_code=["RC-KEEP"])
    resp = self.client.patch(
        f"/api/orders/{order.id}",
        {"recall": {"recall_created_date": "2026-08-13"}},
        format="json",
    )
    self.assertEqual(resp.status_code, 200)
    order.recall.refresh_from_db()
    self.assertEqual(order.recall.recall_code, ["RC-KEEP"])
```

Nếu helper `self._make_order()` trong file có tên khác, dùng đúng tên đang có.

- [ ] **Step 2: Chạy test để xác nhận ĐỎ**

Run: lệnh pytest ở mục "Lệnh chạy test" với `<paths>` = `apps/orders/tests/test_order_recall.py -k "multiple_recall_codes or keeps_existing_codes"`
Expected: FAIL (service vẫn ghi chuỗi thô vào cột mảng).

- [ ] **Step 3: Cho `recall_code` đi qua `_parse_slip_codes`**

Trong `order_service_helpers.py`, `_upsert_order_recall`: bỏ hẳn `char_fields` (không còn field char nào) và đưa `recall_code` vào `list_fields`:

```python
        # Cả 2 field mã đều là mảng, nhận chuỗi phẩy từ inline edit / import và list
        # từ sync KiotViet — `_parse_slip_codes` chuẩn hoá chung một chỗ.
        list_fields = {"recall_code", "return_slip_code"}
        allowed = ("recall_code", "recall_created_date", "recall_received_date", "return_slip_code")
        changed: list[str] = []
        for field_name in allowed:
            if field_name not in recall_payload:
                continue
            value = recall_payload[field_name]
            if field_name in list_fields:
                value = _parse_slip_codes(value)
            setattr(recall, field_name, value)
            changed.append(field_name)
```

- [ ] **Step 4: Trả chuỗi phẩy ở serializer đọc**

Trong `order_core_serializers.py`, hàm `get_recall`, đổi dòng `recall_code`:

```python
            # Mảng N mã → chuỗi phẩy: giữ nguyên contract cũ và đồng nhất với
            # return_slip_code ngay dưới (cùng là ô text nhiều mã trên UI).
            "recall_code": ", ".join(recall.recall_code or []),
```

- [ ] **Step 5: Nới `max_length` ở 2 serializer ghi**

`kiotviet_landing_serializers.py`, `OrderRecallInputSerializer`:

```python
    # Contract giữ dạng chuỗi phẩy nhưng nay chứa N mã (mỗi mã <=64 ký tự, cắt ở
    # _parse_slip_codes) — 1024 đủ rộng cho danh sách mã dài.
    recall_code = serializers.CharField(max_length=1024, required=False, allow_blank=True, allow_null=True)
```

`order_action_serializers.py` (payload tạo đơn):

```python
    # Chuỗi phẩy N mã, cùng contract với inline edit (xem OrderRecallInputSerializer).
    recall_code = serializers.CharField(
        max_length=1024, required=False, allow_blank=True, default=""
    )
```

- [ ] **Step 6: Sửa tra cứu mã vận đơn và filter `has_recall_code`**

`shipping_lookup_view.py`:

```python
    recall_qs = (
        # recall_code là mảng — `__contains=[code]` khớp phần tử và ăn GIN index
        # order_recall_code_gin (equality trên mảng sẽ chỉ khớp mảng 1 phần tử).
        Order.objects.filter(recall__recall_code__contains=[code], deleted_at__isnull=True)
        .select_related("kiotviet_channel", "assessment")
        .prefetch_related("items")
        .order_by("-id")[:20]
    )
```

`repositories.py`, khối `has_recall_code`:

```python
            recall_exists = Exists(
                OrderRecall.objects.filter(order_id=OuterRef("pk"), recall_code__len__gt=0)
            )
```

(cùng dạng với `has_return_slip` ngay phía trên — sửa luôn comment "recall_code rỗng = chưa có" thành "mảng rỗng = chưa có")

- [ ] **Step 7: Sửa export**

`export_excel.py`, nhánh `recall_code`:

```python
                if key == "recall_code":
                    val = ", ".join(recall.recall_code or [])
```

- [ ] **Step 8: Thêm test cho lookup và export**

`test_shipping_lookup.py` — sửa các chỗ tạo dữ liệu `OrderRecall.objects.create(order=..., recall_code="RC-SCAN-1")` thành `recall_code=["RC-SCAN-1"]`, và thêm:

```python
    def test_lookup_matches_second_recall_code(self):
        """Mã thứ 2 trong mảng cũng phải tra ra đơn — không chỉ phần tử đầu."""
        order = self._make_order("VD-MULTI-RECALL")
        OrderRecall.objects.create(order=order, recall_code=["RC-A", "RC-B"])
        resp = self.client.get("/api/shipping-lookup", {"code": "RC-B"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["orders"][0]["id"], order.id)
```

Đọc file trước để dùng đúng tên helper `_make_order` và đúng shape response (`resp.data["orders"]` có thể khác — bám theo test `test_lookup_matches_recall_code` đã có).

`test_orders_export_definition.py` — trong `test_recall_columns_export_full_data_including_multiple_return_slips`, đổi dữ liệu `recall_code` sang mảng nhiều mã và assert ô xuất ra là `"RC-1, RC-2"`.

- [ ] **Step 9: Chạy test — phải XANH**

Run: pytest với `<paths>` = `apps/orders/tests/test_order_recall.py apps/orders/tests/test_shipping_lookup.py apps/orders/tests/test_orders_export_definition.py apps/orders/tests/test_order_create_api.py apps/orders/tests/test_order_list_exists_filters.py`
Expected: PASS toàn bộ. `test_order_create_api.py` có `test_create_order_with_order_type_and_recall_code` assert `order.recall.recall_code == "RC-OUT-001"` — sửa thành `["RC-OUT-001"]`.

- [ ] **Step 10: Ruff + commit**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
git add backend/apps/orders
git commit -m "feat(orders): accept multiple recall codes end to end"
```

---

### Task 3: `Order.koc_order_type` (model + migration + serializer + export)

**Files:**
- Modify: `backend/apps/orders/models.py` (thêm `KocOrderType` cạnh `OrderType` ở dòng ~34; thêm field cạnh `order_type` dòng ~443; thêm index vào `Order.Meta.indexes`)
- Create: `backend/apps/orders/migrations/0125_order_koc_order_type.py`
- Modify: `…/serializers/order_serializers_impl/order_core_serializers.py` (thêm `"koc_order_type"` vào tuple `fields`)
- Modify: `backend/apps/orders/export_excel.py`
- Test: `backend/apps/orders/tests/test_order_list_contract.py`

**Interfaces:**
- Consumes: không phụ thuộc Task 1-2 (khác field, khác bảng) — chạy song song được.
- Produces: `Order.koc_order_type: str` ∈ `{"", "sample_gift", "renewal", "recall"}`; `KocOrderType` import được từ `apps.orders.models`. Task 4 và 5 dùng đúng 3 mã này.

- [ ] **Step 1: Thêm TextChoices + field + index vào `models.py`**

Ngay dưới `class OrderType`:

```python
class KocOrderType(models.TextChoices):
    """Phân loại đơn KOC. Rỗng = đơn KOC thường (không phải một lựa chọn)."""

    SAMPLE_GIFT = "sample_gift", "Tặng mẫu"
    RENEWAL = "renewal", "Gia hạn"
    RECALL = "recall", "Thu hồi"
```

Trong `class Order`, ngay dưới `order_type`:

```python
    # Nhãn nghiệp vụ do nhân viên gán tay, CHỈ dùng cho đơn KOC. Trước đây viết tay
    # vào ô ghi chú nên không lọc được; tách ra field riêng để ghi chú quay về đúng
    # vai trò ghi chú vấn đề phát sinh.
    koc_order_type = models.CharField(
        max_length=32, choices=KocOrderType.choices, blank=True, default=""
    )
```

Trong `Order.Meta.indexes`, cạnh `order_shiptype_created_idx`:

```python
            # Partial: gần như toàn bộ orders có koc_order_type rỗng (chỉ đơn KOC được
            # gán nhãn mới có giá trị) — index đầy đủ chỉ tốn write overhead. Ghép
            # created_at DESC vì bộ lọc luôn đi kèm khoảng thời gian và list sắp xếp theo nó.
            models.Index(
                fields=["koc_order_type", "-created_at"],
                condition=~Q(koc_order_type=""),
                name="order_koc_type_created_idx",
            ),
```

`Q` đã được import sẵn trong `models.py` (dùng ở các index khác) — kiểm tra, không import trùng.

- [ ] **Step 2: Sinh migration bằng Django rồi sửa sang CONCURRENTLY**

Run:
```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-wt-koc-type && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_koc_t3 -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= \
  -e REDIS_CACHE_DB=8 -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  python manage.py makemigrations orders --name order_koc_type
```

Sau đó sửa file vừa sinh: tách `AddIndex` sang `AddIndexConcurrently` và đặt `atomic = False`, theo đúng khuôn `0122_order_shipping_type_index.py`. File cuối cùng phải là:

```python
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    """Thêm cột phân loại đơn KOC + index cho bộ lọc.

    CONCURRENTLY vì bảng orders lớn: CREATE INDEX thường khoá ghi suốt thời gian quét
    bảng. Đổi lại migration này KHÔNG chạy trong transaction (atomic = False) — hỏng
    giữa chừng sẽ để lại index INVALID, phải DROP rồi chạy lại.

    AddField chạy được cùng file: thêm cột có default hằng là thao tác metadata-only
    trên PG 11+, không viết lại bảng.
    """

    atomic = False

    dependencies = [
        ("orders", "0124_orderrecall_recall_code_array"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="koc_order_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("sample_gift", "Tặng mẫu"),
                    ("renewal", "Gia hạn"),
                    ("recall", "Thu hồi"),
                ],
                default="",
                max_length=32,
            ),
        ),
        AddIndexConcurrently(
            model_name="order",
            index=models.Index(
                fields=["koc_order_type", "-created_at"],
                condition=~Q(koc_order_type=""),
                name="order_koc_type_created_idx",
            ),
        ),
    ]
```

Đặt tên file `0125_order_koc_order_type.py` (đổi tên nếu Django sinh khác), và sửa `dependencies` trỏ `0124_orderrecall_recall_code_array` nếu Task 1 đã merge; nếu làm song song và Task 1 chưa có, trỏ `0123_merge_shipping_type_product_components` rồi Task 6 sẽ hợp leaf.

- [ ] **Step 3: Đưa field vào serializer đọc**

Trong `order_core_serializers.py`, thêm `"koc_order_type"` vào tuple `fields` (ngay cạnh `"order_type"`). Field nằm trên `Order` nên `ModelSerializer` tự đọc/ghi và tự validate theo `choices` — không cần khai báo thêm.

- [ ] **Step 4: Viết test đỏ cho contract + export**

Thêm vào `backend/apps/orders/tests/test_order_list_contract.py` (bám khuôn test đã có trong file):

```python
def test_order_list_exposes_koc_order_type(self):
    """Field phải có mặt trong response list, mặc định rỗng."""
    order = self._make_order()
    resp = self.client.post("/api/orders", {}, format="json")
    self.assertEqual(resp.status_code, 200)
    row = next(r for r in resp.data["results"] if r["id"] == order.id)
    self.assertEqual(row["koc_order_type"], "")


def test_inline_update_sets_koc_order_type(self):
    order = self._make_order()
    resp = self.client.patch(
        f"/api/orders/{order.id}", {"koc_order_type": "sample_gift"}, format="json"
    )
    self.assertEqual(resp.status_code, 200)
    order.refresh_from_db()
    self.assertEqual(order.koc_order_type, "sample_gift")


def test_inline_update_rejects_unknown_koc_order_type(self):
    """Giá trị ngoài 3 mã phải bị từ chối — choices trên model lo việc này."""
    order = self._make_order()
    resp = self.client.patch(
        f"/api/orders/{order.id}", {"koc_order_type": "xyz"}, format="json"
    )
    self.assertEqual(resp.status_code, 400)
```

- [ ] **Step 5: Chạy test — xác nhận đỏ rồi xanh sau khi có field**

Run: pytest với `<paths>` = `apps/orders/tests/test_order_list_contract.py -k koc_order_type` (dùng `POSTGRES_DB=desi_koc_t3`, `REDIS_CACHE_DB=8`)
Expected: PASS sau khi Step 1-3 xong.

- [ ] **Step 6: Xuất nhãn tiếng Việt trong Excel**

Trong `export_excel.py`, ngay dưới nhánh `shipping_type`:

```python
        elif key == "koc_order_type":
            # Xuất NHÃN tiếng Việt, không xuất mã: file Excel là để người đọc.
            val = {"sample_gift": "Tặng mẫu", "renewal": "Gia hạn", "recall": "Thu hồi"}.get(
                order.koc_order_type, ""
            )
```

- [ ] **Step 7: Test export + ruff + commit**

Thêm vào `test_orders_export_definition.py`:

```python
def test_koc_order_type_column_exports_vietnamese_label(order_factory):
    """Cột xuất nhãn tiếng Việt, không xuất mã."""
    order = order_factory(koc_order_type="renewal")
    rows = order_row_values(order, [{"key": "koc_order_type", "header": "Loại đơn KOC"}])
    assert rows == ["Gia hạn"]
```

Đọc file để dùng đúng chữ ký `order_row_values` và đúng fixture đang có.

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
git add backend/apps/orders
git commit -m "feat(orders): add koc order type field"
```

---

### Task 4: Bộ lọc `koc_order_type` (backend)

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/_view_helpers.py:321` (khối `build_orders_filters`)
- Modify: `backend/apps/orders/transport/views/order_views_impl/sheet3_view.py:103-106`
- Modify: `backend/apps/orders/repositories.py:768-775` (cạnh khối `order_type`)
- Test: `backend/apps/orders/tests/test_order_list_filters_api.py`

**Interfaces:**
- Consumes: `Order.koc_order_type` (Task 3).
- Produces: tham số filter `koc_order_type` nhận list hoặc chuỗi phẩy, ngữ nghĩa OR. Task 5 (FE) gửi đúng key này trong body request.

- [ ] **Step 1: Viết test đỏ**

Thêm vào `backend/apps/orders/tests/test_order_list_filters_api.py`:

```python
def test_filter_koc_order_type_single_value(self):
    sample = self._make_order(koc_order_type="sample_gift")
    self._make_order(koc_order_type="renewal")
    resp = self.client.post("/api/orders", {"koc_order_type": ["sample_gift"]}, format="json")
    self.assertEqual(resp.status_code, 200)
    self.assertEqual([r["id"] for r in resp.data["results"]], [sample.id])


def test_filter_koc_order_type_multiple_values_is_or(self):
    sample = self._make_order(koc_order_type="sample_gift")
    renewal = self._make_order(koc_order_type="renewal")
    self._make_order(koc_order_type="recall")
    resp = self.client.post(
        "/api/orders", {"koc_order_type": ["sample_gift", "renewal"]}, format="json"
    )
    self.assertEqual(resp.status_code, 200)
    self.assertEqual(
        sorted(r["id"] for r in resp.data["results"]), sorted([sample.id, renewal.id])
    )


def test_filter_koc_order_type_blank_means_no_filter(self):
    """Chuỗi/list rỗng = không lọc, KHÔNG phải lọc ra đơn có nhãn rỗng."""
    a = self._make_order(koc_order_type="sample_gift")
    b = self._make_order()
    resp = self.client.post("/api/orders", {"koc_order_type": []}, format="json")
    self.assertEqual(resp.status_code, 200)
    self.assertEqual(sorted(r["id"] for r in resp.data["results"]), sorted([a.id, b.id]))
```

Dùng đúng tên helper tạo đơn đang có trong file (đọc trước khi thêm).

- [ ] **Step 2: Chạy test để xác nhận ĐỎ**

Run: pytest với `<paths>` = `apps/orders/tests/test_order_list_filters_api.py -k koc_order_type`
Expected: FAIL — filter chưa được parse nên trả về cả 3 đơn.

- [ ] **Step 3: Parse filter ở CẢ HAI nơi**

`_view_helpers.py`, trong dict trả về của `build_orders_filters`, ngay dưới `"order_type"`:

```python
        "koc_order_type": _parse_multi_query_values(request, "koc_order_type"),
```

`sheet3_view.py`, trong dict filter (ngay cạnh `"has_recall_code"`):

```python
        "koc_order_type": _parse_multi_query_values(request, "koc_order_type"),
```

Đọc `sheet3_view.py:95-110` trước: file này parse filter theo cách riêng và đã có comment cảnh báo `has_recall_code` từng bị bỏ sót ở đây. Dùng đúng helper mà file đó đang dùng, đừng copy mù.

- [ ] **Step 4: Áp filter trong repository**

`repositories.py`, ngay dưới khối `order_type_filter`:

```python
        koc_type_filter = filters.get("koc_order_type")
        if koc_type_filter:
            kt_values = koc_type_filter if isinstance(koc_type_filter, list) else [koc_type_filter]
            kt_values = [str(v).strip() for v in kt_values if str(v).strip()]
            if kt_values:
                queryset = queryset.filter(koc_order_type__in=kt_values)
            else:
                queryset = queryset.none()
```

- [ ] **Step 5: Chạy test — phải XANH**

Run: pytest với `<paths>` = `apps/orders/tests/test_order_list_filters_api.py`
Expected: PASS toàn bộ file (không chỉ 3 test mới).

- [ ] **Step 6: Ruff + commit**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
git add backend/apps/orders
git commit -m "feat(orders): filter orders by koc order type"
```

---

### Task 5: Frontend — cột + ô sửa `koc_order_type`

**Files:**
- Modify: `frontend/src/features/orders/order-types.ts` (`OrderRow`, `OrderColumnKey`, `ORDER_COLUMN_OPTIONS`, `ORDER_COLUMN_FIELD_BY_KEY`)
- Modify: `frontend/src/features/orders/order-import-format.ts:96-101` (`RECALL_COLUMN_KEYS`)
- Modify: `frontend/src/features/orders/OrdersTable/OrderCell.tsx`
- Modify: `frontend/src/features/orders/BlockRecall.tsx`
- Modify: `frontend/src/i18n/messages/orders.ts` (cả khối `vi` và `en`)
- Create: `frontend/src/features/orders/__tests__/koc-order-type.test.tsx`

**Interfaces:**
- Consumes: contract `koc_order_type: "" | "sample_gift" | "renewal" | "recall"` (Task 3).
- Produces: hằng `KOC_ORDER_TYPE_VALUES` export từ `frontend/src/features/orders/orders-page/use-orders-filter-options.ts` — Task 6 dùng lại cho options bộ lọc.

- [ ] **Step 1: Viết test đỏ**

Create `frontend/src/features/orders/__tests__/koc-order-type.test.tsx` — mirror cấu trúc của `shipping-type.test.tsx` cùng thư mục (đọc file đó trước để copy đúng `BASE_PROPS` và cách import):

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ORDER_COLUMN_OPTIONS, type OrderRow } from "@/features/orders/order-types";
import { RECALL_COLUMN_KEYS } from "@/features/orders/order-import-format";
import { DEFAULT_ORDER_COLUMNS } from "@/features/orders/order-storage";
import { OrderCell } from "@/features/orders/OrdersTable/OrderCell";
import { I18nProvider } from "@/i18n";

const BASE_PROPS = {
  kiotvietOptions: [],
  addressCatalog: { provinces: [], districts_by_province: {}, wards_by_district_key: {} },
  channelStaffByCode: {},
} as never;

describe("cột Loại đơn KOC", () => {
  it("chọn được nhưng không bật sẵn, và chỉ hiện ở trang KOC", () => {
    expect(ORDER_COLUMN_OPTIONS.some((c) => c.key === "koc_order_type")).toBe(true);
    expect(DEFAULT_ORDER_COLUMNS).not.toContain("koc_order_type");
    // Nằm trong nhóm cột KOC → OrdersPage/OrdersColumnPicker tự lọc bỏ ở trang khác.
    expect(RECALL_COLUMN_KEYS).toContain("koc_order_type");
  });

  it("hiện nhãn tiếng Việt", () => {
    const row = { id: 1, koc_order_type: "renewal" } as unknown as OrderRow;
    render(
      <I18nProvider>
        <OrderCell {...BASE_PROPS} columnKey="koc_order_type" row={row} />
      </I18nProvider>,
    );
    expect(screen.getByText("Gia hạn")).toBeInTheDocument();
  });

  it("đơn chưa gán nhãn hiện gạch ngang", () => {
    const row = { id: 2, koc_order_type: "" } as unknown as OrderRow;
    render(
      <I18nProvider>
        <OrderCell {...BASE_PROPS} columnKey="koc_order_type" row={row} />
      </I18nProvider>,
    );
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Chạy test — xác nhận ĐỎ**

Run: `cd /c/Users/MYPC/Desktop/workspace/desi-order-wt-koc-type/frontend && npx vitest run src/features/orders/__tests__/koc-order-type.test.tsx`
Expected: FAIL (chưa có column key).

- [ ] **Step 3: Khai báo kiểu + cột trong `order-types.ts`**

Trong `OrderRow`, cạnh `shipping_type`:

```ts
  /** Nhãn nghiệp vụ đơn KOC, nhân viên gán tay. "" = đơn KOC thường. */
  koc_order_type?: "" | "sample_gift" | "renewal" | "recall";
```

Thêm `| "koc_order_type"` vào union `OrderColumnKey`; thêm vào `ORDER_COLUMN_OPTIONS`:

```ts
  { key: "koc_order_type", label: "orders.columns.kocOrderType" },
```

Thêm vào `ORDER_COLUMN_FIELD_BY_KEY`:

```ts
  koc_order_type: "koc_order_type",
```

- [ ] **Step 4: Đưa vào nhóm cột chỉ-KOC**

`order-import-format.ts`:

```ts
export const RECALL_COLUMN_KEYS: OrderColumnKey[] = [
  "koc_order_type",
  "recall_code",
  "recall_created_date",
  "recall_received_date",
  "return_slip_code",
];
```

- [ ] **Step 5: Render ô + i18n**

Trong `OrderCell.tsx`, thêm nhánh render cho `koc_order_type` — đọc nhánh `shipping_type` trong file rồi làm cùng kiểu (badge + `t(\`kocOrderType.${value}\`, value)`, rỗng thì hiện `—`).

`i18n/messages/orders.ts`, khối `vi`:

```ts
    "orders.columns.kocOrderType": "Loại đơn KOC",
    "kocOrderType.sample_gift": "Tặng mẫu",
    "kocOrderType.renewal": "Gia hạn",
    "kocOrderType.recall": "Thu hồi",
    "orders.filters.kocOrderType": "Loại đơn KOC",
```

khối `en`:

```ts
    "orders.columns.kocOrderType": "KOC Order Type",
    "kocOrderType.sample_gift": "Sample gift",
    "kocOrderType.renewal": "Renewal",
    "kocOrderType.recall": "Recall",
    "orders.filters.kocOrderType": "KOC Order Type",
```

- [ ] **Step 6: Ô sửa trong khối Thu hồi**

`BlockRecall.tsx` — thêm `ERow` dạng select **lên đầu tiên** trong `SectionCard` (trên "Mã đơn thu hồi"), patch thẳng lên `order` chứ không vào `recall` vì field nằm trên `Order`:

```tsx
      <ERow
        label={t("orders.columns.kocOrderType")}
        rawValue={order.koc_order_type ?? ""}
        type="select"
        options={[
          { value: "", label: "—" },
          { value: "sample_gift", label: t("kocOrderType.sample_gift") },
          { value: "renewal", label: t("kocOrderType.renewal") },
          { value: "recall", label: t("kocOrderType.recall") },
        ]}
        onSave={(v) => patch({ koc_order_type: String(v) as OrderRow["koc_order_type"] })}
      />
```

Đọc `order-detail-shared.tsx` để dùng ĐÚNG tên prop options của `ERow` (`type="select"` có thể nhận prop tên khác, ví dụ `selectOptions`) — không đoán.

Cập nhật cả label "Mã đơn thu hồi" thành gợi ý nhiều mã nếu `ERow` có prop placeholder; nếu không có thì bỏ qua, không thêm prop mới.

- [ ] **Step 7: Chạy test — phải XANH**

Run: `npx vitest run src/features/orders/__tests__/koc-order-type.test.tsx src/features/orders/__tests__/shipping-type.test.tsx`
Expected: PASS cả hai file.

- [ ] **Step 8: tsc + commit**

```bash
cd frontend && npx tsc --noEmit
git add frontend/src
git commit -m "feat(orders): show and edit koc order type column"
```

---

### Task 6: Frontend — bộ lọc "Loại đơn KOC"

**Files:**
- Modify: `frontend/src/features/orders/orders-page/use-orders-filter-options.ts` (thêm `KOC_ORDER_TYPE_VALUES` + `kocOrderTypeFilterOptions`)
- Modify: `frontend/src/features/orders/use-orders-filters.ts`, `order-storage.ts`, `use-orders-data.ts`, `orders-page/use-orders-filter-handlers.ts`
- Modify: `frontend/src/features/orders/OrdersFilterPanel.tsx`
- Modify: `frontend/src/pages/OrdersPage.tsx`, `frontend/src/app/routed-pages.tsx`
- Test: `frontend/src/features/orders/__tests__/use-orders-filters.test.tsx`, `frontend/src/features/orders/__tests__/use-orders-data.filter-overrides.test.tsx`

**Interfaces:**
- Consumes: tham số backend `koc_order_type` (Task 4); hằng nhãn i18n (Task 5).
- Produces: state filter `kocOrderType: string[]`, gửi lên body request dưới key `koc_order_type`.

- [ ] **Step 1: Viết test đỏ**

Thêm vào `frontend/src/features/orders/__tests__/use-orders-filters.test.tsx`: thêm `kocOrderType: []` vào object filter mặc định trong file (dòng ~24, cạnh `shippingType: []`) — TypeScript sẽ đỏ cho tới khi kiểu được mở rộng.

Thêm `"koc_order_type"` vào mảng key trong `use-orders-data.filter-overrides.test.tsx` (dòng ~91, cạnh `"shipping_type"`) — test `clearFilters xoá MỌI filter khỏi request` sẽ kiểm luôn key mới.

- [ ] **Step 2: Chạy test — xác nhận ĐỎ**

Run: `npx vitest run src/features/orders/__tests__/use-orders-filters.test.tsx src/features/orders/__tests__/use-orders-data.filter-overrides.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Nối dây state filter**

Với **mỗi** file dưới đây, tìm mọi vị trí có `shippingType` / `shipping_type` và thêm một dòng song song cho `kocOrderType` / `koc_order_type` (cùng kiểu `string[]`, mặc định `[]`):

- `use-orders-filters.ts` — kiểu state + giá trị mặc định + serialize/deserialize
- `order-storage.ts` — `StoredOrderFilters` + đọc/ghi localStorage
- `use-orders-data.ts` — dựng body request (`koc_order_type`) + `clearFilters`
- `orders-page/use-orders-filter-handlers.ts` — `handleKocOrderTypeChange`
- `pages/OrdersPage.tsx` — 2 khai báo kiểu props (dòng ~97 và ~154), destructure (~295), đếm số filter đang bật (~422), 2 dependency array (~446, ~507), truyền props xuống `OrdersFilterPanel` (~794)
- `app/routed-pages.tsx` — map state xuống page (~111)

Đây là plumbing thuần, không có logic mới. Bám sát `shippingType` — nếu thấy chỗ nào `shippingType` xuất hiện mà `kocOrderType` không hợp lý ở đó, DỪNG và báo thay vì tự chế.

- [ ] **Step 4: Options + UI bộ lọc**

`use-orders-filter-options.ts`:

```ts
/** 3 nhãn nghiệp vụ đơn KOC. Không có token "rỗng" — bỏ trống = không lọc. */
export const KOC_ORDER_TYPE_VALUES = ["sample_gift", "renewal", "recall"] as const;
```

và trong hook, cạnh `shippingTypeFilterOptions`:

```ts
  const kocOrderTypeFilterOptions: FilterSelectOption[] = useMemo(
    () =>
      KOC_ORDER_TYPE_VALUES.map((v) => ({
        value: v,
        label: t(`kocOrderType.${v}`, v),
      })),
    [t],
  );
```

Nhớ trả `kocOrderTypeFilterOptions` ra khỏi hook.

`OrdersFilterPanel.tsx` — thêm một multi-select giống hệt ô "Loại xuất hàng", nhưng **chỉ render khi đang ở trang KOC**. Đọc file để xem panel đã nhận cờ nào (nếu chưa có prop `isKoc` thì thêm prop `isKoc: boolean` và truyền từ `OrdersPage.tsx`, nơi biến `isKoc` đã tồn tại).

- [ ] **Step 5: Chạy test — phải XANH**

Run: `npx vitest run src/features/orders/__tests__/use-orders-filters.test.tsx src/features/orders/__tests__/use-orders-data.filter-overrides.test.tsx src/features/orders/__tests__/koc-order-type.test.tsx`
Expected: PASS.

- [ ] **Step 6: tsc + lint + commit**

```bash
cd frontend && npx tsc --noEmit && npm run lint:strict
git add frontend/src
git commit -m "feat(orders): filter koc orders by order type"
```

---

### Task 7: Chốt — schema, migration leaf, suite đầy đủ, MR

**Files:**
- Modify: `backend/schema.yaml`
- Modify: `docs/superpowers/plans/2026-08-13-koc-order-type-and-multi-recall-codes.md` (đánh dấu sprint xong trong spec)

**Interfaces:**
- Consumes: toàn bộ Task 1-6.
- Produces: nhánh sẵn sàng mở MR.

- [ ] **Step 1: Xác nhận chỉ có MỘT leaf migration**

Run:
```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-wt-koc-type && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_koc_final -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= \
  -e REDIS_CACHE_DB=9 -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'python manage.py makemigrations --check --dry-run && python manage.py showmigrations orders | tail -6'
```
Expected: `No changes detected`, và 2 migration mới nằm nối tiếp nhau (không phải 2 nhánh song song). Nếu có 2 leaf → tạo migration merge, KHÔNG sửa lịch sử.

- [ ] **Step 2: Sửa tay `schema.yaml`**

Thêm `koc_order_type` (enum 3 giá trị + `""`) vào schema `Order`, và cập nhật mô tả `recall_code` thành "chuỗi các mã cách nhau dấu phẩy". **Sửa tay, không regen toàn file** — `schema.yaml` đang stale sẵn, regen sẽ kéo nhiễu của feature khác vào MR.

Ba bẫy khi sửa tay: (1) `ChoiceField` bị hoist thành `$ref` tới một `Enum` component riêng, phải thêm cả component đó; (2) mảng `required` sắp xếp theo alphabet; (3) không param nào có `explode`/`style`.

- [ ] **Step 3: Chạy suite backend app orders**

Run: pytest với `<paths>` = `apps/orders` (`POSTGRES_DB=desi_koc_final`, `REDIS_CACHE_DB=9`). Suite này dài — chạy bằng `docker run -d --name koc-final-test …` rồi chờ, đừng chạy foreground.
Expected: không có failure nào liên quan `recall`/`koc`. So với baseline master nếu có nghi ngờ.

- [ ] **Step 4: Chạy suite frontend đầy đủ**

Run: `cd frontend && npm run test:ci`
Expected: chỉ còn các failure đã có sẵn trên master. **Đo baseline master tại thời điểm chạy** trước khi kết luận cái nào là hồi quy — ghi chú baseline cũ đã sai nhiều lần.

- [ ] **Step 5: Cập nhật bảng Sprint trong spec**

Đổi 4 dòng trạng thái trong `docs/superpowers/specs/2026-08-13-koc-order-type-and-multi-recall-codes-design.md` từ `⬜ Chưa làm` sang `✅ Xong`, ghi ngắn gọn cái gì đã làm / còn nợ.

- [ ] **Step 6: Push + mở MR**

```bash
git add -A && git commit -m "chore(orders): update openapi schema for koc order type"
git push -u origin feat/koc-order-type-multi-recall \
  -o merge_request.create \
  -o merge_request.title="feat(orders): multiple recall codes and KOC order type" \
  -o merge_request.remove_source_branch
```

Không có `glab`/token trên máy này — dùng push options. Nếu branch đã up-to-date, phải xoá remote branch rồi push lại thì MR mới được tạo.

- [ ] **Step 7: Ghi chú vận hành trong mô tả MR**

Nêu rõ: migration `0124` đổi kiểu cột nên **migrate và roll code phải đi liền nhau, chạy giờ thấp điểm**; `0125` chạy `CONCURRENTLY` nên không nằm trong transaction, hỏng giữa chừng phải DROP index INVALID rồi chạy lại.

---

## Self-Review

**Spec coverage:**

| Yêu cầu trong spec | Task |
|---|---|
| `recall_code` → ArrayField + GIN | 1 |
| 8 điểm đọc/ghi `recall_code` | 2 |
| Contract chuỗi phẩy giữ nguyên | 2 (Step 4-5) |
| `Order.koc_order_type` + partial index | 3 |
| Export nhãn tiếng Việt | 3 (Step 6) |
| Filter 2 nơi parse (parity `sheet3_view`) | 4 (Step 3) |
| `has_recall_code` giữ nguyên, sửa theo kiểu mới | 2 (Step 6) |
| Cột chỉ hiện ở trang KOC | 5 (Step 4) |
| Inline edit | 5 (Step 6) |
| Bộ lọc chỉ hiện ở trang KOC | 6 (Step 4) |
| i18n vi + en | 5 (Step 5) |
| Không backfill | Ràng buộc toàn cục — không task nào backfill |
| `schema.yaml` sửa tay | 7 (Step 2) |
| Migrate + roll code đi liền | 7 (Step 7) |

**Type consistency:** 3 mã `sample_gift` / `renewal` / `recall` dùng thống nhất ở Task 3 (`KocOrderType`), Task 4 (test filter), Task 5 (`OrderRow`, i18n key `kocOrderType.*`), Task 6 (`KOC_ORDER_TYPE_VALUES`). Column key `koc_order_type` thống nhất giữa `ORDER_COLUMN_OPTIONS`, `RECALL_COLUMN_KEYS`, `OrderCell`, và nhánh export backend.

**Điểm plan cố tình để executor tự đọc file** (không phải placeholder — là chỗ plan KHÔNG được đoán): tên helper tạo đơn trong từng file test; tên prop options của `ERow`; helper parse filter trong `sheet3_view.py`; cờ `isKoc` đã có sẵn ở `OrdersFilterPanel` hay chưa. Mọi chỗ này đều kèm chỉ dẫn "đọc file trước, đừng đoán".
