# Tồn kho theo mốc ngày — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cho phép chọn một ngày trên trang Tồn kho để xem tồn cuối ngày đó, kèm xuất Excel theo cùng mốc.

**Architecture:** Dựng lại tồn tại mốc bằng cách **trừ ngược** từ `stock_items` (projection của sổ cái): `tồn(D) = quantity − Σ qty_delta của movement có occurred_at ≥ 00:00 ngày D+1`. Tận dụng bất biến `occurred_at ≤ created_at` để thêm điều kiện `created_at ≥ end` — không đổi kết quả nhưng cắt sạch partition cũ hơn mốc. Không thêm bảng, không migration, không cron.

**Tech Stack:** Django 5 + DRF (backend, Python 3.12), React + TypeScript + TanStack Query + shadcn/ui (frontend), PostgreSQL (bảng `stock_movements` RANGE-partition theo tháng trên `created_at`), pytest + vitest.

**Spec:** `docs/superpowers/specs/2026-08-12-stock-as-of-date-design.md`

**Nhánh:** `feat/stock-as-of-date` — worktree `c:\Users\MYPC\Desktop\workspace\desi-order-stock-as-of` (worktree ANH EM của repo chính).

## Global Constraints

- Python 3.12+, Django 5, clean architecture: logic nghiệp vụ ở `application/services/`, view chỉ parse/validate/serialize.
- `ruff check .` phải sạch (ruleset `E4,E7,E9,F,B,C4,DTZ,LOG,PIE,RET,SIM,T20,UP` theo `backend/pyproject.toml`). Lưu ý `DTZ`: mọi `datetime` phải aware.
- Sổ cái `StockMovement` là **append-only**. Tính năng này CHỈ ĐỌC — không được ghi, sửa, xoá movement nào.
- Luật cắt partition nằm ở **một nơi duy nhất**: `apps/inventory/application/services/movement_window.py`. Dùng lại `local_day_bounds`, TUYỆT ĐỐI không chép luật ra file mới.
- So sánh ngày trên cột datetime phải dùng **cột trần** (`__gte`/`__lt`), không được cast `__date` — cast làm Postgres mất khả năng cắt partition.
- Parse ngày bằng `datetime.strptime(value, "%Y-%m-%d")`, không dùng `date.fromisoformat` (đồng bộ với `_valid_date` ở `flow_history_views.py`).
- **Fixture test không được cắm ngày cứng.** Mọi mốc dựng tương đối so với `timezone.localdate()`, nếu không suite sẽ tự đỏ vào ngày 1 hàng tháng.
- Frontend: TypeScript strict, không thêm dependency mới, dùng component sẵn có trong `@/components/ui`.
- Commit message tiếng Anh, ngắn gọn, không kèm dòng `Co-Authored-By`.

## Môi trường chạy test

**Backend** (Docker; container test có thể đã bị xoá, dựng lại nếu `docker ps` không thấy):

```bash
docker network create desi-test-net 2>/dev/null
docker run -d --name desi-test-pg --network desi-test-net \
  -e POSTGRES_USER=desi_order -e POSTGRES_PASSWORD=desi_order -e POSTGRES_DB=desi_order \
  public.ecr.aws/docker/library/postgres:16-alpine
docker run -d --name desi-test-redis --network desi-test-net \
  public.ecr.aws/docker/library/redis:7-alpine
```

Lệnh chạy test (thay `<paths>`):

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-stock-as-of && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_stock_asof \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
```

Lint backend (image test không có ruff):

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

**Frontend** — worktree này là worktree ANH EM nên dùng junction được (chạy MỘT LẦN, trong PowerShell):

```powershell
New-Item -ItemType Junction -Path "C:\Users\MYPC\Desktop\workspace\desi-order-stock-as-of\frontend\node_modules" `
         -Target "C:\Users\MYPC\Desktop\workspace\desi-order\frontend\node_modules"
```

Sau đó: `cd frontend && npx vitest run <file>` và `npx tsc --noEmit`.

**Baseline frontend**: đo tại thời điểm bắt đầu bằng `npm run test:ci`, ĐỪNG tin con số chép sẵn ở đâu đó. Ngày 2026-08-12 trên master `ba82fb59` baseline là 3 test đỏ ở `src/app/__tests__/modules.test.ts` + `src/app/__tests__/nav-layout.test.ts` (do `foxia-report` không nằm trong sidebar zone) — không liên quan tính năng này, nhưng baseline đổi rất nhanh nên phải đo lại.

## File Structure

| File | Trách nhiệm | Task |
|---|---|---|
| `backend/apps/inventory/application/services/stock_as_of_service.py` (tạo mới) | Dựng tồn tại một mốc từ sổ cái. Không biết gì về HTTP. | 1 |
| `backend/apps/inventory/tests/test_stock_as_of.py` (tạo mới) | Toàn bộ test của tính năng, cả service lẫn API lẫn export. | 1–5 |
| `backend/apps/inventory/application/services/stock_query_service.py` (sửa) | `sales_run_rates` nhận thêm mốc kết thúc; `list_movements` nhận thêm `as_of`. | 2, 4 |
| `backend/apps/inventory/transport/views/stock_views.py` (sửa) | Parse/validate `as_of`, guard trần, phân trang trong Python. | 3, 4 |
| `backend/apps/inventory/exports_definitions.py` (sửa) | Export kind `stock` nhận `as_of`. | 5 |
| `frontend/src/features/inventory/types.ts` (sửa) | Kiểu response thêm `as_of`. | 6 |
| `frontend/src/features/inventory/queries.ts` (sửa) | Hai hook nhận `asOf`, đưa vào query key. | 6 |
| `frontend/src/features/inventory/stock-as-of.ts` (tạo mới) | Helper ngày local (`toIsoDate`, `isPastDate`) — tách khỏi component để test thuần. | 6 |
| `frontend/src/features/inventory/pages/StockPage.tsx` (sửa) | Ô chọn ngày, dải cảnh báo, ẩn nút thao tác, truyền mốc xuống export. | 7 |
| `frontend/src/features/inventory/__tests__/stock-as-of.test.tsx` (tạo mới) | Test UI chế độ mốc. | 7 |
| `frontend/src/features/inventory/__tests__/stock-export.test.tsx` (sửa) | Cập nhật kỳ vọng params export (thêm `as_of`). | 7 |

**Cố ý KHÔNG đụng:** `backend/schema.yaml`. Hai endpoint `/api/inventory/stock` và `/api/inventory/stock/movements` hiện **không khai báo query param nào** trong schema (view không dùng `@extend_schema(parameters=...)`). Thêm mỗi `as_of` vào sẽ tạo ra một file schema nửa vời, mâu thuẫn với chính nó. Ghi vào mục nợ ở Task 8 thay vì làm dở dang.

---

### Task 1: Service dựng tồn tại mốc

**Files:**
- Create: `backend/apps/inventory/application/services/stock_as_of_service.py`
- Create: `backend/apps/inventory/tests/test_stock_as_of.py`

**Interfaces:**
- Consumes: `local_day_bounds(date_from, date_to)` từ `apps.inventory.application.services.movement_window` — trả `(start, end)`, `end` = 00:00 giờ VN của ngày `date_to + 1`.
- Produces:
  - `StockAsOfService.end_bound(as_of: date) -> datetime`
  - `StockAsOfService.deltas_after(as_of: date, *, warehouse_id: int | None = None, product_ids: set[int] | None = None) -> dict[tuple[int, int, str], int]`
  - `StockAsOfService.apply(items: list[StockItem], as_of: date, *, warehouse_id: int | None = None) -> list[StockItem]`

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_stock_as_of.py`:

```python
"""Tồn kho tại một mốc ngày — service, API, export.

Cách dựng số: trừ ngược từ `stock_items` (xem
docs/superpowers/specs/2026-08-12-stock-as-of-date-design.md §3). Test đối chiếu
chéo với cách cộng xuôi từ sổ là chốt chặn quan trọng nhất ở đây.

MỌI mốc thời gian trong file này dựng TƯƠNG ĐỐI so với hôm nay — không được cắm
ngày cứng, nếu không suite tự đỏ vào ngày 1 hàng tháng.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.inventory.application.services.stock_as_of_service import StockAsOfService
from apps.inventory.models import MovementType, StockItem, StockMovement, StockState
from apps.orders.models import Product
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


def today() -> date:
    return timezone.localdate()


def at(day: date, hour: int = 12, minute: int = 0):
    """Datetime aware theo GIỜ VN của một ngày tương đối."""
    naive = timezone.datetime.combine(day, timezone.datetime.min.time()).replace(
        hour=hour, minute=minute
    )
    return timezone.make_aware(naive)


def move(warehouse, product, qty, occurred, created=None, state=StockState.SELLABLE):
    """Ghi thẳng một dòng sổ + cập nhật projection, KHÔNG qua service.

    Cố ý đi đường thấp để dựng được ca `created_at` lệch `occurred_at` (quét khớp
    trễ) — `StockCommandService` luôn ép `created_at = now()`.
    """
    mv = StockMovement.objects.create(
        warehouse=warehouse,
        product=product,
        qty_delta=qty,
        movement_type=MovementType.RECEIPT if qty > 0 else MovementType.ISSUE,
        state=state,
        balance_after=0,
        occurred_at=occurred,
    )
    if created is not None:
        StockMovement.objects.filter(pk=mv.pk).update(created_at=created)
    item, _ = StockItem.objects.get_or_create(
        warehouse=warehouse, product=product, state=state, defaults={"quantity": 0}
    )
    item.quantity += qty
    item.save(update_fields=["quantity"])
    return mv


@pytest.fixture
def warehouse():
    return Warehouse.objects.create(name="Kho mốc", code="WH-ASOF")


@pytest.fixture
def product():
    return Product.objects.create(name="SP mốc", sku="SKU-ASOF-1")


def items_of(warehouse):
    return list(
        StockItem.objects.filter(warehouse=warehouse).select_related("warehouse", "product")
    )


class TestDeltasAfter:
    def test_subtracts_only_movements_after_the_mark(self, warehouse, product):
        d5 = today() - timedelta(days=5)
        move(warehouse, product, 10, at(d5))
        move(warehouse, product, -3, at(today() - timedelta(days=2)))

        deltas = StockAsOfService.deltas_after(d5)

        assert deltas == {(warehouse.id, product.id, StockState.SELLABLE): -3}

    def test_movement_on_the_mark_day_is_included_in_balance(self, warehouse, product):
        """Bút toán lúc 23:30 ngày D thuộc về ngày D — không bị trừ ra."""
        d = today() - timedelta(days=3)
        move(warehouse, product, 10, at(d, hour=23, minute=30))

        assert StockAsOfService.deltas_after(d) == {}

    def test_movement_just_after_midnight_is_excluded(self, warehouse, product):
        d = today() - timedelta(days=3)
        move(warehouse, product, 10, at(d, hour=23, minute=30))
        move(warehouse, product, 4, at(d + timedelta(days=1), hour=0, minute=30))

        assert StockAsOfService.deltas_after(d) == {
            (warehouse.id, product.id, StockState.SELLABLE): 4
        }

    def test_late_posted_movement_lands_on_its_business_day(self, warehouse, product):
        """occurred_at ngày D, created_at ngày D+3 (quét khớp trễ) → thuộc ngày D.

        Đây là ca mà điều kiện cắt partition `created_at >= end` có thể âm thầm
        loại nhầm nếu đặt sai chiều.
        """
        d = today() - timedelta(days=6)
        move(warehouse, product, 7, at(d), created=at(d + timedelta(days=3)))

        assert StockAsOfService.deltas_after(d) == {}
        assert StockAsOfService.deltas_after(d - timedelta(days=1)) == {
            (warehouse.id, product.id, StockState.SELLABLE): 7
        }


class TestApply:
    def test_quantity_matches_forward_sum_from_ledger(self, warehouse, product):
        """Đối chiếu chéo: trừ ngược phải bằng cộng xuôi từ đầu sổ."""
        d = today() - timedelta(days=4)
        move(warehouse, product, 20, at(today() - timedelta(days=9)))
        move(warehouse, product, -5, at(today() - timedelta(days=6)))
        move(warehouse, product, 8, at(d))
        move(warehouse, product, -11, at(today() - timedelta(days=1)))

        rows = StockAsOfService.apply(items_of(warehouse), d)

        end = StockAsOfService.end_bound(d)
        forward = sum(
            m.qty_delta
            for m in StockMovement.objects.filter(
                warehouse=warehouse, product=product, state=StockState.SELLABLE
            )
            if m.occurred_at < end
        )
        assert [r.quantity for r in rows] == [forward]
        assert forward == 23

    def test_drops_pair_with_zero_total_at_the_mark(self, warehouse, product):
        """Mã chỉ nhập về SAU mốc thì không hiện ở mốc đó."""
        d = today() - timedelta(days=5)
        move(warehouse, product, 12, at(today() - timedelta(days=2)))

        assert StockAsOfService.apply(items_of(warehouse), d) == []

    def test_keeps_all_three_states_when_pair_has_any_stock(self, warehouse, product):
        d = today() - timedelta(days=2)
        move(warehouse, product, 5, at(today() - timedelta(days=4)))
        # Trạng thái lỗi về 0 tại mốc, nhưng cặp (kho, mã) vẫn có tồn → giữ đủ 3 dòng.
        move(warehouse, product, 2, at(today() - timedelta(days=4)), state=StockState.DEFECT)
        move(warehouse, product, -2, at(today() - timedelta(days=4)), state=StockState.DEFECT)
        StockItem.objects.get_or_create(
            warehouse=warehouse, product=product, state=StockState.WARRANTY,
            defaults={"quantity": 0},
        )

        rows = StockAsOfService.apply(items_of(warehouse), d)

        assert len(rows) == 3
        assert {r.state for r in rows} == {
            StockState.SELLABLE, StockState.WARRANTY, StockState.DEFECT
        }

    def test_does_not_persist_the_rewritten_quantity(self, warehouse, product):
        """apply() sửa quantity TRONG BỘ NHỚ để serializer đọc — cấm chạm DB."""
        d = today() - timedelta(days=3)
        move(warehouse, product, 10, at(today() - timedelta(days=5)))
        move(warehouse, product, 6, at(today() - timedelta(days=1)))

        StockAsOfService.apply(items_of(warehouse), d)

        assert StockItem.objects.get(
            warehouse=warehouse, product=product, state=StockState.SELLABLE
        ).quantity == 16
```

- [ ] **Step 2: Chạy test để chắc chắn nó đỏ**

Run:
```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-stock-as-of && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_stock_asof \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory/tests/test_stock_as_of.py -q --create-db'
```
Expected: FAIL ở bước collect — `ModuleNotFoundError: No module named 'apps.inventory.application.services.stock_as_of_service'`.

- [ ] **Step 3: Viết service**

Tạo `backend/apps/inventory/application/services/stock_as_of_service.py`:

```python
"""Dựng lại tồn kho tại một MỐC NGÀY từ sổ cái append-only.

Cách làm: TRỪ NGƯỢC từ `stock_items` (vốn là projection của toàn bộ
`stock_movements`, xem `management/commands/rebuild_stock_items.py`):

    tồn(D) = stock_items.quantity − Σ qty_delta của movement có occurred_at ≥ 00:00 ngày D+1

Vì sao không cộng xuôi từ đầu sổ (`Σ qty_delta WHERE occurred_at < end`)? Bảng
`stock_movements` RANGE-partition theo THÁNG trên `created_at`, nên cộng xuôi
luôn quét toàn bộ partition lịch sử — kể cả khi người dùng chỉ xem hôm qua, tức
ca dùng phổ biến nhất. Trừ ngược thì càng gần hiện tại càng ít partition.

Điều kiện `created_at >= end` đi kèm là mẹo cắt partition, KHÔNG đổi kết quả:
nhờ bất biến `occurred_at <= created_at` (ép ở `StockCommandService`), mọi dòng
bị nó loại thêm đều có `created_at < end` ⇒ `occurred_at < end` ⇒ đằng nào cũng
bị loại. Luật đầy đủ ở `movement_window` — module đó là NƠI DUY NHẤT giữ luật,
đừng chép sang đây.

Service này CHỈ ĐỌC. Sổ cái append-only, tính năng tra cứu không được ghi gì.
"""
from __future__ import annotations

from datetime import date, datetime

from django.db.models import Sum

from apps.inventory.application.services.movement_window import local_day_bounds
from apps.inventory.models import StockItem, StockMovement


class StockAsOfService:
    @staticmethod
    def end_bound(as_of: date) -> datetime:
        """Cận trên nửa mở: 00:00 giờ VN của ngày `as_of + 1`."""
        _, end = local_day_bounds(date_to=as_of)
        return end

    @staticmethod
    def deltas_after(
        as_of: date,
        *,
        warehouse_id: int | None = None,
        product_ids: set[int] | None = None,
    ) -> dict[tuple[int, int, str], int]:
        """Tổng biến động SAU mốc, gộp theo (kho, mã, trạng thái)."""
        end = StockAsOfService.end_bound(as_of)
        qs = StockMovement.objects.filter(occurred_at__gte=end, created_at__gte=end)
        if warehouse_id:
            qs = qs.filter(warehouse_id=warehouse_id)
        if product_ids is not None:
            qs = qs.filter(product_id__in=product_ids)
        rows = qs.values("warehouse_id", "product_id", "state").annotate(
            delta=Sum("qty_delta")
        )
        return {
            (r["warehouse_id"], r["product_id"], r["state"]): r["delta"] or 0
            for r in rows
        }

    @staticmethod
    def apply(
        items: list[StockItem],
        as_of: date,
        *,
        warehouse_id: int | None = None,
    ) -> list[StockItem]:
        """Ghi đè `quantity` của từng item = tồn tại mốc, bỏ cặp (kho, mã) tổng 0.

        Ghi đè TRONG BỘ NHỚ để `StockItemSerializer` (một ModelSerializer) dùng
        lại được nguyên xi. Các instance trả về KHÔNG được `.save()` ở bất cứ
        đâu — làm thế là ghi số quá khứ đè lên tồn hiện tại.

        Cặp (kho, mã) có cả ba trạng thái = 0 tại mốc thì bị loại: sản phẩm chưa
        nhập về thời điểm đó không nên chiếm chỗ trong bảng. Loại theo CẶP, không
        theo từng dòng trạng thái — bảng web pivot ba trạng thái thành một dòng.
        """
        if not items:
            return []
        deltas = StockAsOfService.deltas_after(
            as_of,
            warehouse_id=warehouse_id,
            product_ids={item.product_id for item in items},
        )
        totals: dict[tuple[int, int], int] = {}
        for item in items:
            key = (item.warehouse_id, item.product_id)
            item.quantity -= deltas.get((*key, item.state), 0)
            totals[key] = totals.get(key, 0) + item.quantity
        return [
            item for item in items if totals[(item.warehouse_id, item.product_id)] != 0
        ]
```

- [ ] **Step 4: Chạy test để chắc chắn nó xanh**

Run: lệnh y hệt Step 2.
Expected: PASS, 8 test.

- [ ] **Step 5: Lint**

Run:
```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-stock-as-of && docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache apps/inventory
```
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-stock-as-of
git add backend/apps/inventory/application/services/stock_as_of_service.py backend/apps/inventory/tests/test_stock_as_of.py
git commit -m "feat(inventory): rebuild stock balance as of a given date"
```

---

### Task 2: RR3/RR7 tính theo mốc

**Files:**
- Modify: `backend/apps/inventory/application/services/stock_query_service.py:62-103` (`sales_run_rates`)
- Test: `backend/apps/inventory/tests/test_stock_as_of.py` (thêm class mới vào cuối file)

**Interfaces:**
- Consumes: `StockAsOfService.end_bound` (Task 1).
- Produces: `StockQueryService.sales_run_rates(pairs, *, as_of_end: datetime | None = None)` — `None` giữ nguyên hành vi hiện tại (cửa sổ tính tới `timezone.now()`, không có cận trên).

- [ ] **Step 1: Viết test thất bại**

Thêm vào cuối `backend/apps/inventory/tests/test_stock_as_of.py`:

```python
class TestRunRateAtMark:
    def test_window_ends_at_the_mark_not_today(self, warehouse, product):
        """Xem lại ngày D thì phải thấy tốc độ bán CỦA ngày D."""
        from apps.inventory.application.services.stock_query_service import (
            StockQueryService,
        )

        d = today() - timedelta(days=10)
        # 6 chiếc bán trong 3 ngày trước mốc.
        move(warehouse, product, 30, at(today() - timedelta(days=30)))
        move(warehouse, product, -6, at(d - timedelta(days=1)))
        # Bán rầm rộ SAU mốc — không được lọt vào cửa sổ của mốc.
        move(warehouse, product, -90, at(today() - timedelta(days=1)))

        rates = StockQueryService.sales_run_rates(
            {(warehouse.id, product.id)},
            as_of_end=StockAsOfService.end_bound(d),
        )

        assert rates[(warehouse.id, product.id)] == {"rr3": 2.0, "rr7": round(6 / 7, 2)}

    def test_default_call_is_unchanged(self, warehouse, product):
        from apps.inventory.application.services.stock_query_service import (
            StockQueryService,
        )

        move(warehouse, product, 30, at(today() - timedelta(days=30)))
        move(warehouse, product, -6, at(today() - timedelta(days=1)))

        rates = StockQueryService.sales_run_rates({(warehouse.id, product.id)})

        assert rates[(warehouse.id, product.id)]["rr3"] == 2.0
```

- [ ] **Step 2: Chạy test để chắc chắn nó đỏ**

Run: lệnh Task 1 Step 2, đổi `<paths>` thành `apps/inventory/tests/test_stock_as_of.py::TestRunRateAtMark`.
Expected: FAIL — `TypeError: sales_run_rates() got an unexpected keyword argument 'as_of_end'`.

- [ ] **Step 3: Sửa `sales_run_rates`**

Trong `backend/apps/inventory/application/services/stock_query_service.py`, thay toàn bộ thân hàm `sales_run_rates` bằng:

```python
    @staticmethod
    def sales_run_rates(
        pairs: set[tuple[int, int]],
        *,
        as_of_end: datetime | None = None,
    ) -> dict[tuple[int, int], dict[str, float]]:
        """RR3/RR7: số bán trung bình mỗi ngày trong 3/7 ngày gần nhất, theo (kho, mã).

        Đọc net SALE + SALE_REVERSAL từ sổ movement (cùng quy ước outstanding) —
        các cặp bút toán hành chính cùng cửa sổ (chuyển sổ đơn anh em, huỷ→hồi
        sinh) tự triệt tiêu. Reversal của đơn bán TRƯỚC cửa sổ có thể kéo net
        âm → clamp về 0.

        `as_of_end` dời điểm KẾT THÚC cửa sổ về một mốc quá khứ (màn hình xem tồn
        theo ngày): xem lại ngày D thì phải thấy tốc độ bán của ngày D, không
        phải của hôm nay. Chỉ khi đó mới thêm cận trên `occurred_at < end` —
        đường mặc định giữ nguyên hành vi cũ, không thêm điều kiện nào.
        """
        if not pairs:
            return {}
        end = as_of_end or timezone.now()
        since_3d = end - timedelta(days=3)
        since_7d = end - timedelta(days=7)
        qs = StockMovement.objects.filter(
            movement_type__in=[MovementType.SALE, MovementType.SALE_REVERSAL],
            # Tốc độ bán tính theo ngày hàng RỜI KHO. `created_at` đi kèm chỉ
            # để cắt partition — hợp lệ nhờ bất biến occurred_at <= created_at
            # (xem movement_window).
            occurred_at__gte=since_7d,
            created_at__gte=since_7d,
            warehouse_id__in={wh for wh, _ in pairs},
            product_id__in={prod for _, prod in pairs},
        )
        if as_of_end is not None:
            # Cận TRÊN không có điều kiện `created_at` đối xứng: bút toán ghi sổ
            # trễ có created_at > occurred_at, thêm vào là loại nhầm đúng những
            # dòng cần đếm (xem docstring movement_window).
            qs = qs.filter(occurred_at__lt=end)
        rows = qs.values("warehouse_id", "product_id").annotate(
            net_7d=Sum("qty_delta"),
            net_3d=Sum("qty_delta", filter=Q(occurred_at__gte=since_3d)),
        )
        rates: dict[tuple[int, int], dict[str, float]] = {}
        for row in rows:
            key = (row["warehouse_id"], row["product_id"])
            if key not in pairs:
                continue
            sold_3d = max(0, -(row["net_3d"] or 0))
            sold_7d = max(0, -(row["net_7d"] or 0))
            rates[key] = {"rr3": round(sold_3d / 3, 2), "rr7": round(sold_7d / 7, 2)}
        return rates
```

Sửa import ở đầu file — dòng `from datetime import timedelta` thành:

```python
from datetime import datetime, timedelta
```

- [ ] **Step 4: Chạy test để chắc chắn nó xanh**

Run: lệnh Task 1 Step 2 với `<paths>` = `apps/inventory/tests/test_stock_as_of.py apps/inventory/tests/test_stock_query.py`.
Expected: PASS toàn bộ — `test_stock_query.py` là bộ test RR cũ, phải còn xanh nguyên.

- [ ] **Step 5: Commit**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-stock-as-of
git add backend/apps/inventory/application/services/stock_query_service.py backend/apps/inventory/tests/test_stock_as_of.py
git commit -m "feat(inventory): allow run-rate window to end at a past mark"
```

---

### Task 3: API `GET /api/inventory/stock?as_of=`

**Files:**
- Modify: `backend/apps/inventory/transport/views/stock_views.py:25-79`
- Test: `backend/apps/inventory/tests/test_stock_as_of.py` (thêm class mới)

**Interfaces:**
- Consumes: `StockAsOfService.apply` (Task 1), `StockQueryService.sales_run_rates(..., as_of_end=...)` (Task 2).
- Produces: response JSON `{items, total, limit, offset, as_of}` — `as_of` là chuỗi `YYYY-MM-DD` đã chuẩn hoá, hoặc `null` khi không truyền.

- [ ] **Step 1: Viết test thất bại**

Thêm vào cuối `backend/apps/inventory/tests/test_stock_as_of.py`:

```python
class TestStockListApi:
    @pytest.fixture(autouse=True)
    def _client(self):
        from rest_framework.test import APIClient

        from apps.users.test_helpers import make_admin

        self.client = APIClient()
        self.client.force_authenticate(make_admin("stock-asof-actor"))

    def _get(self, **params):
        query = {"limit": 50, "offset": 0, **params}
        return self.client.get("/api/inventory/stock", query)

    def test_returns_balance_at_the_mark(self, warehouse, product):
        d = today() - timedelta(days=3)
        move(warehouse, product, 10, at(today() - timedelta(days=5)))
        move(warehouse, product, -4, at(today() - timedelta(days=1)))

        resp = self._get(warehouse_id=warehouse.id, as_of=d.isoformat())

        assert resp.status_code == 200
        assert resp.data["as_of"] == d.isoformat()
        assert [i["quantity"] for i in resp.data["items"]] == [10]

    def test_without_param_returns_current_balance(self, warehouse, product):
        move(warehouse, product, 10, at(today() - timedelta(days=5)))
        move(warehouse, product, -4, at(today() - timedelta(days=1)))

        resp = self._get(warehouse_id=warehouse.id)

        assert resp.data["as_of"] is None
        assert [i["quantity"] for i in resp.data["items"]] == [6]

    def test_today_returns_current_balance(self, warehouse, product):
        move(warehouse, product, 10, at(today() - timedelta(days=5)))
        move(warehouse, product, -4, at(today(), hour=9))

        resp = self._get(warehouse_id=warehouse.id, as_of=today().isoformat())

        assert resp.data["as_of"] == today().isoformat()
        assert [i["quantity"] for i in resp.data["items"]] == [6]

    def test_future_mark_is_rejected(self, warehouse):
        resp = self._get(
            warehouse_id=warehouse.id,
            as_of=(today() + timedelta(days=1)).isoformat(),
        )

        assert resp.status_code == 400
        assert "tương lai" in resp.data["detail"]

    def test_malformed_mark_is_rejected(self, warehouse):
        resp = self._get(warehouse_id=warehouse.id, as_of="12-08-2026")

        assert resp.status_code == 400
        assert "YYYY-MM-DD" in resp.data["detail"]

    def test_pairs_with_zero_total_are_hidden(self, warehouse, product):
        d = today() - timedelta(days=5)
        move(warehouse, product, 12, at(today() - timedelta(days=2)))

        resp = self._get(warehouse_id=warehouse.id, as_of=d.isoformat())

        assert resp.data["total"] == 0
        assert resp.data["items"] == []

    def test_rejects_when_filtered_set_exceeds_cap(self, warehouse, product, monkeypatch):
        from apps.inventory.transport.views import stock_views

        move(warehouse, product, 5, at(today() - timedelta(days=4)))
        monkeypatch.setattr(stock_views, "_AS_OF_MAX_ROWS", 0)

        resp = self._get(
            warehouse_id=warehouse.id,
            as_of=(today() - timedelta(days=1)).isoformat(),
        )

        assert resp.status_code == 400
        assert "thu hẹp" in resp.data["detail"]
```

- [ ] **Step 2: Chạy test để chắc chắn nó đỏ**

Run: lệnh Task 1 Step 2 với `<paths>` = `apps/inventory/tests/test_stock_as_of.py::TestStockListApi`.
Expected: FAIL — `KeyError: 'as_of'` / `assert 200 == 400`.

- [ ] **Step 3: Sửa view**

Trong `backend/apps/inventory/transport/views/stock_views.py`:

Thêm import ở đầu file (sau các import Django sẵn có):

```python
from datetime import date, datetime

from django.utils import timezone

from apps.inventory.application.services.stock_as_of_service import StockAsOfService
```

Thêm hằng số + hàm parse ngay sau `_MAX_LIMIT = 200`:

```python
# Trần dòng cho chế độ xem theo mốc. Ở chế độ này không LIMIT/OFFSET ở DB được
# (số tồn tại mốc không nằm trong bảng), phải kéo cả tập đã lọc lên Python — nên
# cần một trần cứng. Cùng con số với `exports_definitions._STOCK_MAX_ROWS`; cố ý
# khai riêng thay vì import để `transport` không phụ thuộc ngược vào tầng export.
_AS_OF_MAX_ROWS = 20000


class _InvalidAsOf(ValueError):
    """Tham số `as_of` sai định dạng hoặc ở tương lai."""


def _parse_as_of(raw: str) -> date | None:
    """Chuẩn hoá `as_of`; None = không lọc theo mốc.

    `strptime` chứ không `date.fromisoformat`: giữ đúng độ chặt của `_valid_date`
    ở `flow_history_views`, vốn chấp nhận cả thành phần không đệm số 0.

    Mốc = HÔM NAY trả về None có chủ đích: tồn cuối ngày hôm nay chính là tồn
    hiện tại, khỏi tốn một vòng trừ ngược. Chuỗi ngày vẫn được echo lại nguyên
    vẹn cho client (xem `list_stock`).
    """
    if not raw:
        return None
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise _InvalidAsOf("as_of phải dạng YYYY-MM-DD.") from exc
    today = timezone.localdate()
    if parsed > today:
        raise _InvalidAsOf("as_of không được ở tương lai.")
    return None if parsed == today else parsed
```

Thay toàn bộ thân `list_stock` bằng:

```python
def list_stock(request):
    pagination, error_response = parse_required_pagination(
        request, max_limit=_MAX_LIMIT, default_limit=_DEFAULT_LIMIT
    )
    if error_response is not None:
        return error_response
    raw_as_of = request.query_params.get("as_of", "")
    try:
        as_of = _parse_as_of(raw_as_of)
    except _InvalidAsOf as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    warehouse_id = _int_or_none(request.query_params.get("warehouse_id"))
    state = request.query_params.get("state") or None
    qs = StockQueryService.list_stock(
        warehouse_id=warehouse_id,
        search=request.query_params.get("search", ""),
        state=state,
    )
    offset, limit = pagination["offset"], pagination["limit"]
    if as_of is None:
        total = qs.count()
        items = list(qs[offset : offset + limit])
        run_rates = StockQueryService.sales_run_rates(
            {(item.warehouse_id, item.product_id) for item in items}
        )
    else:
        # Chế độ mốc: số tồn không nằm trong bảng nên phải tính xong cả tập đã
        # lọc rồi mới cắt trang. Thứ tự của `list_stock` được giữ nguyên qua
        # `apply`, nên trang vẫn ổn định.
        if qs.count() > _AS_OF_MAX_ROWS:
            return Response(
                {
                    "detail": (
                        f"Kho có hơn {_AS_OF_MAX_ROWS} dòng tồn — thu hẹp bằng ô "
                        "tìm kiếm rồi xem lại theo ngày."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        rows = StockAsOfService.apply(list(qs), as_of, warehouse_id=warehouse_id)
        total = len(rows)
        items = rows[offset : offset + limit]
        run_rates = StockQueryService.sales_run_rates(
            {(item.warehouse_id, item.product_id) for item in items},
            as_of_end=StockAsOfService.end_bound(as_of),
        )
    return Response(
        {
            "items": StockItemSerializer(
                items, many=True, context={"run_rates": run_rates}
            ).data,
            "total": total,
            "limit": limit,
            "offset": offset,
            # Echo NGUYÊN chuỗi client gửi (đã qua validate) chứ không phải `as_of`
            # đã chuẩn hoá: mốc = hôm nay được quy về None ở trên, client vẫn cần
            # biết yêu cầu của mình đã được chấp nhận.
            "as_of": raw_as_of or None,
        },
        status=status.HTTP_200_OK,
    )
```

Cập nhật khai báo schema của response cho khớp — trong `@extend_schema` ngay trên `list_stock`, thêm vào `fields`:

```python
                "as_of": drf_serializers.CharField(allow_null=True),
```

- [ ] **Step 4: Chạy test để chắc chắn nó xanh**

Run: lệnh Task 1 Step 2 với `<paths>` = `apps/inventory/tests/test_stock_as_of.py`.
Expected: PASS toàn bộ.

- [ ] **Step 5: Lint + commit**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-stock-as-of
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache apps/inventory
git add backend/apps/inventory/transport/views/stock_views.py backend/apps/inventory/tests/test_stock_as_of.py
git commit -m "feat(inventory): serve stock list as of a chosen date"
```

---

### Task 4: Lịch sử biến động cắt tại mốc

**Files:**
- Modify: `backend/apps/inventory/application/services/stock_query_service.py:105-114` (`list_movements`)
- Modify: `backend/apps/inventory/transport/views/stock_views.py` (`list_movements` view)
- Test: `backend/apps/inventory/tests/test_stock_as_of.py` (thêm class mới)

**Interfaces:**
- Consumes: `StockAsOfService.end_bound` (Task 1), `_parse_as_of` (Task 3).
- Produces: `StockQueryService.list_movements(*, warehouse_id=None, product_id=None, as_of: date | None = None)`.

- [ ] **Step 1: Viết test thất bại**

Thêm vào cuối `backend/apps/inventory/tests/test_stock_as_of.py`:

```python
class TestMovementsApi:
    @pytest.fixture(autouse=True)
    def _client(self):
        from rest_framework.test import APIClient

        from apps.users.test_helpers import make_admin

        self.client = APIClient()
        self.client.force_authenticate(make_admin("stock-asof-mv-actor"))

    def test_history_stops_at_the_mark(self, warehouse, product):
        d = today() - timedelta(days=3)
        move(warehouse, product, 10, at(today() - timedelta(days=5)))
        move(warehouse, product, 3, at(d, hour=23, minute=30))
        move(warehouse, product, -4, at(today() - timedelta(days=1)))

        resp = self.client.get(
            "/api/inventory/stock/movements",
            {
                "limit": 10,
                "offset": 0,
                "warehouse_id": warehouse.id,
                "product_id": product.id,
                "as_of": d.isoformat(),
            },
        )

        assert resp.status_code == 200
        assert sorted(m["qty_delta"] for m in resp.data["items"]) == [3, 10]

    def test_without_mark_returns_full_history(self, warehouse, product):
        move(warehouse, product, 10, at(today() - timedelta(days=5)))
        move(warehouse, product, -4, at(today() - timedelta(days=1)))

        resp = self.client.get(
            "/api/inventory/stock/movements",
            {"limit": 10, "offset": 0, "warehouse_id": warehouse.id, "product_id": product.id},
        )

        assert resp.data["total"] == 2

    def test_future_mark_is_rejected(self, warehouse, product):
        resp = self.client.get(
            "/api/inventory/stock/movements",
            {
                "limit": 10,
                "offset": 0,
                "warehouse_id": warehouse.id,
                "product_id": product.id,
                "as_of": (today() + timedelta(days=1)).isoformat(),
            },
        )

        assert resp.status_code == 400
```

- [ ] **Step 2: Chạy test để chắc chắn nó đỏ**

Run: lệnh Task 1 Step 2 với `<paths>` = `apps/inventory/tests/test_stock_as_of.py::TestMovementsApi`.
Expected: FAIL — `assert [-4, 3, 10] == [3, 10]`.

- [ ] **Step 3: Sửa service + view**

Trong `stock_query_service.py`, thay `list_movements`:

```python
    @staticmethod
    def list_movements(
        *,
        warehouse_id: int | None = None,
        product_id: int | None = None,
        as_of: date | None = None,
    ) -> QuerySet[StockMovement]:
        qs = StockMovementRepository.base()
        if warehouse_id:
            qs = qs.filter(warehouse_id=warehouse_id)
        if product_id:
            qs = qs.filter(product_id=product_id)
        if as_of is not None:
            # Thẻ sổ phải dừng đúng ở mốc mà bảng tồn đang hiển thị — nếu không,
            # người dùng thấy tồn ngày 05/08 mà bên dưới liệt kê bút toán 10/08.
            # KHÔNG kèm `created_at` ở cận trên: bút toán ghi sổ trễ có
            # created_at > occurred_at, thêm vào là loại nhầm chính chúng.
            from apps.inventory.application.services.stock_as_of_service import (
                StockAsOfService,
            )

            qs = qs.filter(occurred_at__lt=StockAsOfService.end_bound(as_of))
        return qs.order_by("-created_at", "-id")
```

Thêm import `date` ở đầu file:

```python
from datetime import date, datetime, timedelta
```

Trong `stock_views.py`, thay phần đầu thân `list_movements`:

```python
    if error_response is not None:
        return error_response
    try:
        as_of = _parse_as_of(request.query_params.get("as_of", ""))
    except _InvalidAsOf as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    qs = StockQueryService.list_movements(
        warehouse_id=_int_or_none(request.query_params.get("warehouse_id")),
        product_id=_int_or_none(request.query_params.get("product_id")),
        as_of=as_of,
    )
```

- [ ] **Step 4: Chạy test để chắc chắn nó xanh**

Run: lệnh Task 1 Step 2 với `<paths>` = `apps/inventory/tests/test_stock_as_of.py apps/inventory/tests/test_stock_query.py`.
Expected: PASS toàn bộ.

- [ ] **Step 5: Lint + commit**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-stock-as-of
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache apps/inventory
git add backend/apps/inventory/application/services/stock_query_service.py backend/apps/inventory/transport/views/stock_views.py backend/apps/inventory/tests/test_stock_as_of.py
git commit -m "feat(inventory): cut movement history at the selected date"
```

---

### Task 5: Xuất Excel theo mốc

**Files:**
- Modify: `backend/apps/inventory/exports_definitions.py:105-207` (class `StockExport`)
- Test: `backend/apps/inventory/tests/test_stock_as_of.py` (thêm class mới)

**Interfaces:**
- Consumes: `StockAsOfService.apply`, `StockAsOfService.end_bound` (Task 1), `sales_run_rates(as_of_end=...)` (Task 2), `_valid_date` (sẵn có trong file).
- Produces: `StockExport.validate` trả thêm khoá `"as_of"` (chuỗi `YYYY-MM-DD` hoặc `""`).

- [ ] **Step 1: Viết test thất bại**

Thêm vào cuối `backend/apps/inventory/tests/test_stock_as_of.py`:

```python
class TestStockExportAsOf:
    def _definition(self):
        from apps.exports.application.registry import get_definition

        return get_definition("stock")

    def test_validate_rejects_malformed_mark(self, warehouse):
        from rest_framework.exceptions import ValidationError

        with pytest.raises(ValidationError):
            self._definition().validate(
                None, {"warehouse_id": warehouse.id, "as_of": "12-08-2026"}
            )

    def test_validate_rejects_future_mark(self, warehouse):
        from rest_framework.exceptions import ValidationError

        with pytest.raises(ValidationError):
            self._definition().validate(
                None,
                {
                    "warehouse_id": warehouse.id,
                    "as_of": (today() + timedelta(days=1)).isoformat(),
                },
            )

    def test_file_name_carries_the_mark(self, warehouse):
        d = today() - timedelta(days=2)
        definition = self._definition()
        params = definition.validate(
            None, {"warehouse_id": warehouse.id, "as_of": d.isoformat()}
        )

        assert definition.file_name(params) == f"ton-kho-wh-asof-{d:%Y%m%d}.xlsx"

    def test_rows_hold_balance_at_the_mark(self, warehouse, product):
        d = today() - timedelta(days=3)
        move(warehouse, product, 10, at(today() - timedelta(days=5)))
        move(warehouse, product, -4, at(today() - timedelta(days=1)))
        definition = self._definition()
        params = definition.validate(
            None, {"warehouse_id": warehouse.id, "as_of": d.isoformat()}
        )
        rows = []

        definition.write(params, _CollectingWriter(rows), lambda _n: None)

        # [Kho, SKU, Tên, Bán được, Bảo hành, Lỗi, Tổng, RR3, RR7]
        assert [r[1] for r in rows] == [product.sku]
        assert rows[0][3] == 10


class _CollectingWriter:
    """Writer tối giản, chỉ gom dòng dữ liệu — đủ để khẳng định số trong file."""

    def __init__(self, sink: list):
        self.sink = sink
        self.headers: list | None = None

    def write_header(self, headers):
        self.headers = headers

    def write_row(self, row):
        self.sink.append(row)
```

- [ ] **Step 2: Chạy test để chắc chắn nó đỏ**

Run: lệnh Task 1 Step 2 với `<paths>` = `apps/inventory/tests/test_stock_as_of.py::TestStockExportAsOf`.
Expected: FAIL — `DID NOT RAISE ValidationError` ở test đầu.

- [ ] **Step 3: Sửa `StockExport`**

Trong `backend/apps/inventory/exports_definitions.py`, thêm import:

```python
from apps.inventory.application.services.stock_as_of_service import StockAsOfService
```

Trong `StockExport.validate`, thêm ngay trước câu `return`:

```python
        as_of = str(raw_params.get("as_of") or "")
        if not _valid_date(as_of):
            raise ValidationError({"detail": "as_of phải dạng YYYY-MM-DD."})
        if as_of and datetime.strptime(as_of, "%Y-%m-%d").date() > timezone.localdate():
            raise ValidationError({"detail": "as_of không được ở tương lai."})
```

và thêm `"as_of": as_of,` vào dict trả về.

Thay `file_name`:

```python
    def file_name(self, params: dict) -> str:
        slug = _slug(params["warehouse_code"])
        # Có mốc thì lấy CHÍNH mốc đó làm hậu tố, không phải ngày chạy job: hai
        # lần xuất cùng một mốc ở hai ngày khác nhau phải ra cùng tên file.
        stamp = (
            params["as_of"].replace("-", "")
            if params.get("as_of")
            else f"{timezone.localtime(timezone.now()):%Y%m%d}"
        )
        return f"ton-kho-{slug}-{stamp}.xlsx"
```

Trong `write`, thay khối tính `run_rates` (ngay sau khối `if truncated:`) bằng:

```python
        as_of = params.get("as_of") or ""
        as_of_date = (
            datetime.strptime(as_of, "%Y-%m-%d").date() if as_of else None
        )
        if as_of_date is not None and as_of_date < timezone.localdate():
            # Mốc = hôm nay thì tồn hiện tại đã đúng, khỏi trừ ngược.
            items = StockAsOfService.apply(
                items, as_of_date, warehouse_id=params["warehouse_id"]
            )
            run_rates = StockQueryService.sales_run_rates(
                {(item.warehouse_id, item.product_id) for item in items},
                as_of_end=StockAsOfService.end_bound(as_of_date),
            )
        else:
            run_rates = StockQueryService.sales_run_rates(
                {(item.warehouse_id, item.product_id) for item in items}
            )
```

(xoá khối `run_rates = StockQueryService.sales_run_rates(...)` cũ ngay bên dưới, đừng để hai bản).

- [ ] **Step 4: Chạy test để chắc chắn nó xanh**

Run: lệnh Task 1 Step 2 với `<paths>` = `apps/inventory/tests/test_stock_as_of.py apps/inventory/tests/test_inventory_export_definitions.py`.
Expected: PASS toàn bộ — bộ test export cũ phải còn xanh nguyên.

- [ ] **Step 5: Chạy toàn bộ suite inventory**

Run: lệnh Task 1 Step 2 với `<paths>` = `apps/inventory`.
Expected: PASS. Nếu có đỏ, đối chiếu với master trước khi kết luận là hồi quy.

- [ ] **Step 6: Lint + commit**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-stock-as-of
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache apps/inventory
git add backend/apps/inventory/exports_definitions.py backend/apps/inventory/tests/test_stock_as_of.py
git commit -m "feat(inventory): export stock excel as of a chosen date"
```

---

### Task 6: Lớp dữ liệu frontend

**Files:**
- Create: `frontend/src/features/inventory/stock-as-of.ts`
- Modify: `frontend/src/features/inventory/queries.ts`
- Modify: `frontend/src/features/inventory/types.ts`
- Test: `frontend/src/features/inventory/__tests__/stock-as-of.test.tsx` (phần helper)

**Interfaces:**
- Produces:
  - `toIsoDate(d: Date): string` — `YYYY-MM-DD` theo lịch **địa phương**.
  - `isPastDate(iso: string, todayIso: string): boolean`
  - `useStockList(warehouseId: number | null, search: string, asOf?: string)`
  - `useStockMovements(warehouseId: number | null, productId: number | null, enabled: boolean, asOf?: string)`

- [ ] **Step 1: Chuẩn bị node_modules (chạy một lần)**

Run (PowerShell):
```powershell
if (-not (Test-Path "C:\Users\MYPC\Desktop\workspace\desi-order-stock-as-of\frontend\node_modules")) {
  New-Item -ItemType Junction -Path "C:\Users\MYPC\Desktop\workspace\desi-order-stock-as-of\frontend\node_modules" -Target "C:\Users\MYPC\Desktop\workspace\desi-order\frontend\node_modules"
}
```

- [ ] **Step 2: Viết test thất bại**

Tạo `frontend/src/features/inventory/__tests__/stock-as-of.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";

import { isPastDate, toIsoDate } from "@/features/inventory/stock-as-of";

describe("stock-as-of helpers", () => {
  it("đọc ngày theo LỊCH ĐỊA PHƯƠNG, không qua chuỗi ISO UTC", () => {
    // 23:30 giờ máy ngày 05 — toISOString() sẽ nhảy sang ngày 06 ở múi giờ VN.
    expect(toIsoDate(new Date(2026, 7, 5, 23, 30))).toBe("2026-08-05");
  });

  it("đệm số 0 cho tháng và ngày một chữ số", () => {
    expect(toIsoDate(new Date(2026, 0, 9))).toBe("2026-01-09");
  });

  it("chỉ coi là quá khứ khi mốc nhỏ hơn hôm nay", () => {
    expect(isPastDate("2026-08-04", "2026-08-05")).toBe(true);
    expect(isPastDate("2026-08-05", "2026-08-05")).toBe(false);
    expect(isPastDate("", "2026-08-05")).toBe(false);
  });
});
```

- [ ] **Step 3: Chạy test để chắc chắn nó đỏ**

Run:
```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-stock-as-of/frontend && npx vitest run src/features/inventory/__tests__/stock-as-of.test.tsx
```
Expected: FAIL — `Failed to resolve import "@/features/inventory/stock-as-of"`.

- [ ] **Step 4: Viết helper**

Tạo `frontend/src/features/inventory/stock-as-of.ts`:

```ts
/**
 * Helper ngày cho chế độ "xem tồn đến ngày".
 *
 * Cố ý KHÔNG dùng `toISOString()`: hàm đó quy về UTC, nên 23:30 giờ VN ngày 05
 * ra chuỗi ngày 06 — mốc người dùng chọn sẽ lệch một ngày vào buổi tối. Đọc
 * bằng getter địa phương là cách duy nhất khớp với `timezone.localdate()` ở
 * backend.
 */
export function toIsoDate(d: Date): string {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/** Mốc đã chọn có nằm TRƯỚC hôm nay không. Chuỗi `YYYY-MM-DD` so sánh được trực tiếp. */
export function isPastDate(iso: string, todayIso: string): boolean {
  return iso !== "" && iso < todayIso;
}
```

- [ ] **Step 5: Chạy test để chắc chắn nó xanh**

Run: lệnh Step 3.
Expected: PASS, 3 test.

- [ ] **Step 6: Cho hai hook nhận mốc**

Trong `frontend/src/features/inventory/queries.ts`, thay `inventoryKeys`, `useStockList`, `useStockMovements`:

```ts
export const inventoryKeys = {
  // `asOf` NẰM TRONG key: thiếu nó thì đổi mốc sẽ trả về cache của mốc cũ.
  stock: (warehouseId: number | null, search: string, asOf: string) =>
    ["inventory", "stock", warehouseId, search, asOf] as const,
  movements: (warehouseId: number | null, productId: number | null, asOf: string) =>
    ["inventory", "movements", warehouseId, productId, asOf] as const,
};

export function useStockList(warehouseId: number | null, search: string, asOf = "") {
  const params = new URLSearchParams({ limit: "200", offset: "0" });
  if (warehouseId) params.set("warehouse_id", String(warehouseId));
  if (search.trim()) params.set("search", search.trim());
  if (asOf) params.set("as_of", asOf);
  return useQuery({
    queryKey: inventoryKeys.stock(warehouseId, search, asOf),
    queryFn: () =>
      fetchJson(`/api/inventory/stock?${params.toString()}`) as Promise<StockListResponse>,
  });
}

export function useStockMovements(
  warehouseId: number | null,
  productId: number | null,
  enabled: boolean,
  asOf = "",
) {
  const params = new URLSearchParams({ limit: "10", offset: "0" });
  if (warehouseId) params.set("warehouse_id", String(warehouseId));
  if (productId) params.set("product_id", String(productId));
  if (asOf) params.set("as_of", asOf);
  return useQuery({
    queryKey: inventoryKeys.movements(warehouseId, productId, asOf),
    queryFn: () =>
      fetchJson(
        `/api/inventory/stock/movements?${params.toString()}`,
      ) as Promise<StockMovementListResponse>,
    enabled: enabled && warehouseId != null && productId != null,
  });
}
```

Trong `frontend/src/features/inventory/types.ts`, thêm trường vào `StockListResponse`:

```ts
  /** Mốc ngày server đã áp dụng; null = tồn hiện tại. */
  as_of?: string | null;
```

- [ ] **Step 7: Kiểm tra kiểu**

Run:
```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-stock-as-of/frontend && npx tsc --noEmit
```
Expected: không lỗi. (`useReclassifyStock` vẫn invalidate theo tiền tố `["inventory","stock"]` nên vẫn quét đủ mọi mốc — không cần sửa.)

- [ ] **Step 8: Commit**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-stock-as-of
git add frontend/src/features/inventory/stock-as-of.ts frontend/src/features/inventory/queries.ts frontend/src/features/inventory/types.ts frontend/src/features/inventory/__tests__/stock-as-of.test.tsx
git commit -m "feat(inventory): thread as-of date through stock queries"
```

---

### Task 7: Giao diện trang Tồn kho

**Files:**
- Modify: `frontend/src/features/inventory/pages/StockPage.tsx`
- Modify: `frontend/src/features/inventory/__tests__/stock-export.test.tsx` (cập nhật kỳ vọng params)
- Test: `frontend/src/features/inventory/__tests__/stock-as-of.test.tsx` (thêm phần UI)

**Interfaces:**
- Consumes: `toIsoDate`, `isPastDate` (Task 6), `useStockList(..., asOf)`, `useStockMovements(..., asOf)` (Task 6).
- Produces: không có API mới cho task sau.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `frontend/src/features/inventory/__tests__/stock-as-of.test.tsx` — **đặt phần mock lên TRÊN CÙNG file**, trước mọi `import` component (vitest hoist `vi.mock`, nhưng `vi.hoisted` phải khai trước khi dùng):

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "@/i18n";

const { startExport, permissions, stockListArgs } = vi.hoisted(() => ({
  startExport: vi.fn().mockResolvedValue(undefined),
  permissions: new Set<string>(),
  stockListArgs: [] as unknown[][],
}));

vi.mock("@/features/exports/ExportJobsProvider", () => ({
  useExportJobs: () => ({
    jobs: [],
    activeCount: 0,
    isOpen: false,
    setOpen: vi.fn(),
    startExport: (...args: unknown[]) => startExport(...args),
    cancel: vi.fn(),
    dismiss: vi.fn(),
  }),
}));
vi.mock("@/hooks/usePermission", () => ({
  usePermission: () => (code: string) => permissions.has(code),
}));
vi.mock("@/lib/api/warehouses", () => ({
  getWarehouses: vi.fn(() =>
    Promise.resolve({
      items: [{ id: 7, code: "WH-A", name: "Kho A", is_active: true }],
      total: 1,
    }),
  ),
}));
vi.mock("@/features/inventory/queries", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/features/inventory/queries")>();
  return {
    ...actual,
    useStockList: (...args: unknown[]) => {
      stockListArgs.push(args);
      return {
        data: {
          items: [
            {
              id: 1,
              warehouse: 7,
              warehouse_code: "WH-A",
              warehouse_name: "Kho A",
              product: 11,
              product_sku: "SKU-A",
              product_name: "Áo",
              quantity: 3,
              state: "defect",
              updated_at: "2026-08-01T00:00:00Z",
              rr3: 0,
              rr7: 0,
            },
          ],
          total: 1,
          limit: 200,
          offset: 0,
        },
        isLoading: false,
      };
    },
    useStockMovements: () => ({ data: { items: [] }, isLoading: false }),
  };
});

import StockPage from "@/features/inventory/pages/StockPage";

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <I18nProvider>{children}</I18nProvider>
    </QueryClientProvider>
  );
  return render(<StockPage />, { wrapper });
}

/** Ngày hôm kia theo lịch địa phương — tương đối, không cắm cứng. */
function twoDaysAgoIso(): string {
  const d = new Date();
  d.setDate(d.getDate() - 2);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

beforeEach(() => {
  startExport.mockClear();
  permissions.clear();
  stockListArgs.length = 0;
});
afterEach(() => cleanup());

describe("StockPage — xem tồn theo mốc ngày", () => {
  it("mặc định không gửi mốc nào", async () => {
    renderPage();
    await screen.findByText("SKU-A");

    expect(stockListArgs.at(-1)?.[2]).toBe("");
    expect(screen.queryByText(/Đang xem tồn cuối ngày/)).toBeNull();
  });

  it("chọn ngày quá khứ thì hiện dải cảnh báo và ẩn nút Xử lý", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("SKU-A");
    expect(screen.getByRole("button", { name: /Xử lý/ })).toBeTruthy();

    const picker = screen.getByLabelText("Xem tồn đến ngày");
    await user.clear(picker);
    await user.type(picker, twoDaysAgoIso());

    await waitFor(() => expect(screen.getByText(/Đang xem tồn cuối ngày/)).toBeTruthy());
    expect(screen.queryByRole("button", { name: /Xử lý/ })).toBeNull();
    expect(stockListArgs.at(-1)?.[2]).toBe(twoDaysAgoIso());
  });

  it("nút Hiện tại đưa bảng về tồn hiện tại", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("SKU-A");
    const picker = screen.getByLabelText("Xem tồn đến ngày");
    await user.clear(picker);
    await user.type(picker, twoDaysAgoIso());
    await waitFor(() => expect(screen.getByText(/Đang xem tồn cuối ngày/)).toBeTruthy());

    await user.click(screen.getByRole("button", { name: "Hiện tại" }));

    await waitFor(() => expect(screen.queryByText(/Đang xem tồn cuối ngày/)).toBeNull());
    expect(stockListArgs.at(-1)?.[2]).toBe("");
    expect(screen.getByRole("button", { name: /Xử lý/ })).toBeTruthy();
  });

  it("xuất Excel kèm mốc đang xem", async () => {
    permissions.add("stock:export");
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("SKU-A");
    const picker = screen.getByLabelText("Xem tồn đến ngày");
    await user.clear(picker);
    await user.type(picker, twoDaysAgoIso());

    await user.click(screen.getByRole("button", { name: "Xuất Excel" }));

    await waitFor(() => expect(startExport).toHaveBeenCalled());
    const [, params] = startExport.mock.calls.at(-1) as [string, Record<string, unknown>];
    expect(params.as_of).toBe(twoDaysAgoIso());
  });
});
```

Phần `describe("stock-as-of helpers")` viết ở Task 6 giữ nguyên trong cùng file, đặt bên dưới.

- [ ] **Step 2: Chạy test để chắc chắn nó đỏ**

Run:
```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-stock-as-of/frontend && npx vitest run src/features/inventory/__tests__/stock-as-of.test.tsx
```
Expected: FAIL — `Unable to find a label with the text of: Xem tồn đến ngày`.

- [ ] **Step 3: Sửa `StockPage.tsx`**

Thêm import:

```tsx
import { isPastDate, toIsoDate } from "@/features/inventory/stock-as-of";
```

Trong `StockPage`, thêm state và giá trị dẫn xuất ngay sau `const [expandedKey, setExpandedKey] = useState<string | null>(null);`:

```tsx
  const [asOf, setAsOf] = useState("");
  const todayIso = useMemo(() => toIsoDate(new Date()), []);
  // Mốc = hôm nay CHÍNH LÀ tồn hiện tại, nên không tính là "xem quá khứ": không
  // cảnh báo, không khoá thao tác.
  const viewingPast = isPastDate(asOf, todayIso);
```

Đổi lời gọi hook:

```tsx
  const { data, isLoading } = useStockList(warehouseId, search, asOf);
```

Thêm ô chọn ngày vào `actions` của `PageHeader`, ngay sau ô tìm kiếm:

```tsx
            <div className="flex items-center gap-1">
              <input
                type="date"
                aria-label="Xem tồn đến ngày"
                value={asOf}
                max={todayIso}
                onChange={(e) => {
                  setAsOf(e.target.value);
                  setExpandedKey(null);
                }}
                className="rounded-lg border border-border bg-white px-3 py-2 text-sm outline-hidden focus:border-primary"
              />
              {asOf !== "" && (
                <Button variant="ghost" size="sm" onClick={() => setAsOf("")}>
                  Hiện tại
                </Button>
              )}
            </div>
```

Sửa lời gọi export:

```tsx
                  startExport("stock", {
                    warehouse_id: selectedWarehouse.id,
                    search: search.trim(),
                    as_of: asOf,
                  })
```

Thêm dải cảnh báo ngay sau `</PageHeader>` (trước `<ResponsiveList ...>`):

```tsx
      {viewingPast && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          Đang xem tồn cuối ngày {asOf.split("-").reverse().join("/")} — số liệu tra cứu,
          không phản ánh tồn hiện tại.
        </div>
      )}
```

Truyền `asOf`/`viewingPast` xuống hai component dòng. Trong `ExpandableStockRow`, bổ sung prop và dùng:

```tsx
function ExpandableStockRow({
  row,
  warehouseId,
  expanded,
  onToggle,
  onResolve,
  colSpan,
  asOf,
  readOnly,
}: {
  row: PivotRow;
  warehouseId: number | null;
  expanded: boolean;
  onToggle: () => void;
  onResolve: (r: PivotRow) => void;
  colSpan: number;
  asOf: string;
  readOnly: boolean;
}) {
  const { data, isLoading } = useStockMovements(warehouseId, row.product, expanded, asOf);
```

và đổi điều kiện hiện nút Xử lý:

```tsx
          {!readOnly && (row.warranty > 0 || row.defect > 0) && (
```

Làm y hệt cho `ExpandableStockCard` (thêm hai prop `asOf`, `readOnly`; truyền `asOf` vào `useStockMovements`; chuyển `onResolve` thành `undefined` khi `readOnly`):

```tsx
    <StockCard
      row={row}
      onResolve={readOnly ? undefined : onResolve}
      onToggleHistory={onToggle}
      expanded={expanded}
    >
```

Nếu `StockCard` khai `onResolve` là bắt buộc, đổi kiểu prop của nó thành `onResolve?: (r: StockPivotRow) => void` và bọc chỗ render nút bằng `{onResolve && ...}`.

Cuối cùng truyền prop ở cả hai chỗ dựng dòng:

```tsx
          <ExpandableStockCard
            row={r}
            warehouseId={warehouseId}
            expanded={expandedKey === r.key}
            onToggle={() => setExpandedKey((c) => (c === r.key ? null : r.key))}
            onResolve={handleResolve}
            asOf={asOf}
            readOnly={viewingPast}
          />
```

```tsx
                  <ExpandableStockRow
                    key={r.key}
                    row={r}
                    warehouseId={warehouseId}
                    expanded={expandedKey === r.key}
                    onToggle={() => setExpandedKey((cur) => (cur === r.key ? null : r.key))}
                    onResolve={handleResolve}
                    colSpan={colSpan}
                    asOf={asOf}
                    readOnly={viewingPast}
                  />
```

- [ ] **Step 4: Chạy test để chắc chắn nó xanh**

Run: lệnh Step 2.
Expected: PASS, 7 test (3 helper + 4 UI).

- [ ] **Step 5: Sửa test export cũ đang đỏ**

Hai test trong `frontend/src/features/inventory/__tests__/stock-export.test.tsx` đang khẳng định params **bằng đúng** `{ warehouse_id: 7, search: "" }`; giờ có thêm `as_of`. Sửa hai dòng `expect`:

```tsx
    expect(params).toEqual({ warehouse_id: 7, search: "", as_of: "" });
```

```tsx
    expect(params).toEqual({ warehouse_id: 7, search: "SKU-A", as_of: "" });
```

- [ ] **Step 6: Chạy cả hai file test + kiểm tra kiểu**

Run:
```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-stock-as-of/frontend && npx vitest run src/features/inventory/__tests__/stock-as-of.test.tsx src/features/inventory/__tests__/stock-export.test.tsx && npx tsc --noEmit && npm run lint:strict
```
Expected: PASS cả hai file, `tsc` không lỗi, lint sạch.

- [ ] **Step 7: Chạy toàn bộ suite frontend**

Run:
```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-stock-as-of/frontend && npm run test:ci
```
Expected: chỉ còn đúng những test đỏ đã có ở baseline đo lúc bắt đầu. Bất kỳ file đỏ mới nào cũng là hồi quy phải sửa.

- [ ] **Step 8: Commit**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-stock-as-of
git add frontend/src/features/inventory/pages/StockPage.tsx frontend/src/features/inventory/components/StockCard.tsx frontend/src/features/inventory/__tests__/stock-as-of.test.tsx frontend/src/features/inventory/__tests__/stock-export.test.tsx
git commit -m "feat(inventory): pick a date to view stock balance at that mark"
```

---

### Task 8: Tài liệu và gửi MR

**Files:**
- Create: `docs/superpowers/specs/2026-08-12-stock-as-of-date-notes.md`

- [ ] **Step 1: Ghi lại phần đã làm và phần còn nợ**

Tạo `docs/superpowers/specs/2026-08-12-stock-as-of-date-notes.md`:

```markdown
# Tồn kho theo mốc ngày — ghi chú sau triển khai

Ngày: 2026-08-12 · Nhánh: `feat/stock-as-of-date`

## Đã làm

- `StockAsOfService` dựng tồn tại mốc bằng cách trừ ngược từ `stock_items`.
- `GET /api/inventory/stock?as_of=YYYY-MM-DD` và `GET /api/inventory/stock/movements?as_of=`.
- Export kind `stock` nhận `as_of`; tên file lấy theo mốc, không theo ngày chạy job.
- Trang Tồn kho: ô "Xem tồn đến ngày", dải cảnh báo, ẩn nút Xử lý ở chế độ quá khứ.
- RR3/RR7 tính theo cửa sổ 3/7 ngày TRƯỚC mốc.

## Còn nợ

1. **Đo `EXPLAIN` trên prod** với mốc 30/90/180 ngày trước, rồi mới quyết định có
   thêm index `(occurred_at, warehouse, product)` cho `stock_movements` hay không.
   Thêm index vào bảng partition là thao tác tốn — không quyết bằng phỏng đoán.
2. **`backend/schema.yaml` chưa khai `as_of`.** Cố ý: hai endpoint này vốn không
   khai query param nào (view không dùng `@extend_schema(parameters=...)`), thêm
   mỗi param mới vào sẽ tạo file schema nửa vời. Trả nợ bằng một MR chore khai
   đủ tham số cho cả hai endpoint.
3. **Phân trang đếm theo dòng trạng thái** trong khi bảng web pivot ba trạng thái
   thành một dòng, nên trang luôn ngắn hơn `limit`. Hành vi CÓ SẴN từ trước, không
   phát sinh từ tính năng này.
```

- [ ] **Step 2: Commit và push, tạo MR**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-stock-as-of
git add docs/superpowers/specs/2026-08-12-stock-as-of-date-notes.md
git commit -m "docs: post-implementation notes for stock as-of date"
git push -o merge_request.create -o merge_request.title="Xem tồn kho theo mốc ngày" -u origin feat/stock-as-of-date
```

Expected: output của `git push` chứa link MR — gửi link đó cho người dùng.

---

## Self-Review

**Spec coverage:**

| Mục spec | Task |
|---|---|
| §3 trừ ngược + §3.3 cắt partition | 1 |
| §4.1 service riêng | 1 |
| §4.2 API `as_of`, 5 nhánh đầu vào, echo | 3 |
| §4.3 phân trang Python + guard trần | 3 |
| §4.4.1 ẩn dòng tổng 0 theo cặp | 1 (service) + 3 (API) |
| §4.4.2 RR3/RR7 theo mốc | 2 |
| §4.4.3 export theo mốc + tên file | 5 |
| §4.5 không migration/index | toàn bộ; ghi nợ ở 8 |
| §5.1 ô chọn ngày | 7 |
| §5.2.1 dải cảnh báo | 7 |
| §5.2.2 ẩn nút Xử lý | 7 |
| §5.2.3 cắt lịch sử biến động | 4 (API) + 7 (UI) |
| §5.2.4 export truyền mốc | 7 |
| §6 toàn bộ ca test | 1–5, 7 |
| §8 nợ kỹ thuật | 8 |

**Placeholder scan:** không có "TBD"/"TODO"/"tương tự Task N". Mọi bước code đều có khối code thật.

**Type consistency:** `StockAsOfService.end_bound` / `deltas_after` / `apply` dùng đúng tên ở Task 1, 2, 3, 4, 5. `sales_run_rates(..., as_of_end=...)` keyword-only, khớp giữa Task 2, 3, 5. `toIsoDate` / `isPastDate` khớp giữa Task 6 và 7. Prop `asOf: string` + `readOnly: boolean` khớp giữa hai component dòng ở Task 7.
