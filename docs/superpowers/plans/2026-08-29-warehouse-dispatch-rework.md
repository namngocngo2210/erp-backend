# Gỡ hàng đợi "Chờ xuất kho" — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gỡ bỏ hàng đợi "Chờ xuất kho" và thay bằng hai cửa xuất kho mới — quét mã hoá đơn ở màn quét xuất, và nút "Xuất kho" có popup chọn kho ở chi tiết đơn.

**Architecture:** `DirectDispatchService` được **đổi tên** thành `ManualDispatchService` chứ không xoá đi viết lại — khối xử lý verdict KiotViet trong đó (prefetch song song NGOÀI transaction, verdict-là-exception thì skip ngay) là tài sản đã tôi luyện qua sự cố 2026-07-31 và tính năng mới cần đúng nó. Hàng đợi mất đi (`list_pending`), thay bằng một endpoint `POST` nhận `warehouse_id`. Ở luồng quét, chỉ sửa đúng một câu query dò đơn.

**Tech Stack:** Django 5 + DRF, pytest, PostgreSQL; React + TypeScript + vitest + shadcn/ui.

**Spec:** [docs/superpowers/specs/2026-08-29-warehouse-dispatch-rework-design.md](../specs/2026-08-29-warehouse-dispatch-rework-design.md)

## Global Constraints

- Worktree: `.claude/worktrees/warehouse-dispatch-rework`, nhánh `feat/warehouse-dispatch-rework`. Mọi lệnh chạy từ đây.
- **KHÔNG migration, KHÔNG backfill, KHÔNG đụng dữ liệu đơn đang tồn đọng.**
- **KHÔNG xoá cột `Order.direct_dispatch_deferred`, KHÔNG bỏ dòng ghi cờ `=True` ở `order_action_view.py`, KHÔNG sửa `apps/inventory/subscribers._is_direct_sale`.** Xem spec §4 — đây là thứ duy nhất phân biệt đơn `direct` cũ (đã trừ tồn) với đơn mới (chưa trừ tồn); phá nó là tồn phồng im lặng trên prod.
- **KHÔNG đụng luồng quét HOÀN** (`PendingScanKind.RETURN`). Mã hoá đơn chỉ áp cho `kind=DISPATCH`.
- Không đẻ permission mới. Đọc gate bằng `require_section("stock")`, ghi gate bằng `require_permission("stock:edit")`.
- Ruff là bắt buộc (ruleset ở `backend/pyproject.toml`). Frontend: `npx tsc --noEmit` + `npm run lint:strict` phải sạch.
- Commit message tiếng Anh, ngắn gọn, **không** kèm dòng `Co-Authored-By`.

## Chuẩn bị môi trường (làm một lần, trước Task 1)

- [ ] **Bước 0.1: Khởi động Docker Desktop và container test**

Docker daemon đang TẮT tại thời điểm viết plan. Bật Docker Desktop tại
`C:\Users\MYPC\AppData\Local\Programs\DockerDesktop\Docker Desktop.exe`, đợi daemon lên, rồi:

```bash
docker start desi-test-pg desi-test-redis
docker ps --filter "name=desi-test" --format "{{.Names}}\t{{.Status}}"
```

Nếu container không tồn tại (không phải chỉ `Exited`) thì dựng lại:

```bash
docker network create desi-test-net 2>/dev/null
docker run -d --name desi-test-pg --network desi-test-net \
  -e POSTGRES_USER=desi_order -e POSTGRES_PASSWORD=desi_order -e POSTGRES_DB=desi_order \
  public.ecr.aws/docker/library/postgres:16-alpine
docker run -d --name desi-test-redis --network desi-test-net \
  public.ecr.aws/docker/library/redis:7-alpine
```

- [ ] **Bước 0.2: Định nghĩa lệnh chạy test backend**

Dùng nguyên mẫu này cho mọi bước "Run" ở backend. `POSTGRES_DB` phải riêng cho mỗi lần
chạy đồng thời, nếu không sẽ ra lỗi giả `DuplicateDatabase`.

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/warehouse-dispatch-rework
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_wdr \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <PATHS> -q --create-db'
```

- [ ] **Bước 0.3: Cài node_modules cho frontend**

Thử junction trước (mất 1s), chỉ `npm ci` khi vitest vỡ:

```bash
cd frontend && cmd //c "mklink /J node_modules ..\\..\\..\\..\\frontend\\node_modules"
npx vitest run src/features/inventory/__tests__/flow-note-cell.test.tsx
```

Thấy `No test suite found` hoặc `Vitest failed to find the runner` thì:

```bash
cmd //c "rmdir node_modules" && npm ci --prefer-offline --no-audit --no-fund
```

- [ ] **Bước 0.4: Đo baseline test frontend**

Đừng chép số baseline cũ — đo lại tại thời điểm bắt đầu:

```bash
cd frontend && npm run test:ci 2>&1 | tail -20
```

Ghi lại số file/test pass để phân biệt hồi quy của nhánh mình với đỏ sẵn của master.

- [ ] **Bước 0.5: Chụp danh sách đơn đang chờ trên prod (nhắc nghiệp vụ, không phải code)**

Ghi vào ghi chú deploy: trước khi roll code phải gọi
`GET /api/inventory/direct-dispatch/pending` trên prod và lưu kết quả, vì sau deploy
endpoint đó không còn. Kho cần bản này để đối chiếu.

---

## File Structure

**Backend — đổi tên (giữ nội dung theo §6.2 của spec):**

| Từ | Thành | Trách nhiệm sau đổi |
|---|---|---|
| `apps/inventory/application/services/direct_dispatch_service.py` | `manual_dispatch_service.py` | Xuất kho tay cho một/nhiều đơn theo kho chỉ định, có xác minh KiotViet |
| `apps/inventory/transport/views/direct_dispatch_views.py` | `manual_dispatch_views.py` | Đúng 1 endpoint POST |

**Backend — sửa tại chỗ:**

- `apps/inventory/urls.py` — bỏ 2 route cũ, thêm 1 route mới.
- `apps/inventory/application/services/pending_scan_service.py` — query dò đơn + chọn kho theo đường khớp.
- `apps/inventory/application/services/stock_query_service.py` — đổi import module đã đổi tên.
- `apps/orders/infrastructure/tasks/order_tasks_impl/ingest_job_tasks.py` — docstring + thông điệp Lark.
- `apps/inventory/subscribers.py` — **chỉ docstring**, không đổi logic.
- `config/settings_parts/base.py` — bỏ `DIRECT_DISPATCH_WARN_DAYS`.
- `backend/.importlinter` — 4 dòng gọi tên module cũ.

**Backend — test:**

- `apps/inventory/tests/test_direct_deferred_dispatch.py` → đổi tên `test_manual_dispatch.py`.
- `apps/orders/tests/test_direct_deferred_dispatch.py` → đổi tên `test_quick_sale_invoice_only.py`.
- `apps/inventory/tests/test_pending_scan_match.py` — thêm class cho khớp mã hoá đơn.

**Frontend — xoá:**

- `src/features/inventory/pages/DirectDispatchPage.tsx`
- `src/features/inventory/direct-dispatch-queries.ts`
- `src/features/inventory/__tests__/direct-dispatch.test.tsx`

**Frontend — tạo:**

- `src/features/inventory/manual-dispatch-queries.ts` — hook mutation gọi endpoint mới.
- `src/features/orders/ManualDispatchDialog.tsx` — popup chọn kho. Đặt ở `features/orders` vì nó sống trên trang chi tiết đơn; file cùng đổi thì nằm cùng chỗ.
- `src/features/orders/__tests__/manual-dispatch.test.tsx`

**Frontend — sửa tại chỗ:**

- `src/app/routes.tsx`, `src/app/routed-pages.tsx`, `src/app/modules.ts`, `src/lib/routes.ts`, `src/app/__tests__/modules.test.ts` — gỡ entry `direct-dispatch`.
- `src/pages/OrderDetailPage.tsx` — thêm nút.
- `src/features/orders/order-form/OrderFormStockSummary.tsx` — đổi nhãn.
- `src/i18n/messages/orders.ts` — chuỗi mới.

---

## Task 1: Backend — biến hàng đợi thành endpoint xuất kho tay

**Files:**
- Rename: `backend/apps/inventory/application/services/direct_dispatch_service.py` → `manual_dispatch_service.py`
- Rename: `backend/apps/inventory/transport/views/direct_dispatch_views.py` → `manual_dispatch_views.py`
- Modify: `backend/apps/inventory/urls.py`
- Modify: `backend/apps/inventory/application/services/stock_query_service.py` (import + docstring)
- Modify: `backend/config/settings_parts/base.py` (bỏ `DIRECT_DISPATCH_WARN_DAYS`)
- Modify: `backend/.importlinter` (3 dòng ~249)
- Rename + Modify test: `backend/apps/inventory/tests/test_direct_deferred_dispatch.py` → `test_manual_dispatch.py`

**Interfaces:**
- Produces: `ManualDispatchService.dispatch(*, order_ids: list[int], warehouse_id: int, actor) -> dict` trả `{"confirmed": list[int], "skipped": list[dict]}`; hằng `SKIP_NOT_FOUND = "not_found"`, `SKIP_CANCELLED = "cancelled"`, `SKIP_ALREADY_DISPATCHED = "already_dispatched"`; hàm module-level `_dispatched_order_ids(order_ids: list[int]) -> set[int]` (giữ nguyên tên, `stock_query_service` đang import).
- Produces: endpoint `POST /api/inventory/orders/dispatch`, body `{"order_ids": [int], "warehouse_id": int}`.

- [ ] **Step 1: Đổi tên hai file bằng `git mv` (giữ lịch sử)**

```bash
cd backend
git mv apps/inventory/application/services/direct_dispatch_service.py \
       apps/inventory/application/services/manual_dispatch_service.py
git mv apps/inventory/transport/views/direct_dispatch_views.py \
       apps/inventory/transport/views/manual_dispatch_views.py
git mv apps/inventory/tests/test_direct_deferred_dispatch.py \
       apps/inventory/tests/test_manual_dispatch.py
```

- [ ] **Step 2: Viết test thất bại cho `ManualDispatchService.dispatch`**

Trong `backend/apps/inventory/tests/test_manual_dispatch.py`, **xoá** class
`DirectDispatchQueueTests` (mọi test về `list_pending`) và **thêm** class dưới đây.
Giữ nguyên các class về huỷ đơn đang có trong file — chúng khoá spec §4 và vẫn đúng.

Sửa import ở đầu file: bỏ `from apps.inventory.application.services.direct_dispatch_service import ...`,
thêm:

```python
from unittest.mock import patch

from apps.inventory.application.services.manual_dispatch_service import (
    SKIP_ALREADY_DISPATCHED,
    ManualDispatchService,
)
```

```python
class ManualDispatchTests(TestCase):
    """Nút "Xuất kho" ở chi tiết đơn: chọn kho, ghi đè kho của đơn, trừ tồn một lần."""

    def setUp(self):
        self.actor = make_admin("manual-dispatch-actor")
        self.channel = Channel.objects.create(code="md-ch", name="Manual Dispatch")
        self.wh_order = Warehouse.objects.create(name="Kho theo don", code="KHO-MD1")
        self.wh_picked = Warehouse.objects.create(name="Kho chon tay", code="KHO-MD2")
        # make_order nạp sẵn tồn vào kho truyền vào; nạp thêm cho kho thứ hai để
        # test "ghi đè kho" không bị nhiễu bởi tồn âm.
        self.order = make_order(self.channel, self.wh_order, "41", shipping_code="")
        self.item = self.order.items.first()
        StockCommandService.record_movement(
            warehouse_id=self.wh_picked.id,
            product_id=self.item.product_id,
            qty_delta=self.item.quantity,
            movement_type=MovementType.RECEIPT,
        )

    def test_dispatch_overwrites_order_warehouse_and_deducts_there(self):
        before_picked = sellable(self.wh_picked.id, self.item.product_id)
        before_order_wh = sellable(self.wh_order.id, self.item.product_id)

        result = ManualDispatchService.dispatch(
            order_ids=[self.order.id], warehouse_id=self.wh_picked.id, actor=self.actor
        )

        self.assertEqual(result["confirmed"], [self.order.id])
        self.assertEqual(result["skipped"], [])
        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_warehouse_id, self.wh_picked.id)
        self.assertEqual(
            sellable(self.wh_picked.id, self.item.product_id),
            before_picked - self.item.quantity,
        )
        # Kho cũ của đơn KHÔNG bị đụng tới.
        self.assertEqual(sellable(self.wh_order.id, self.item.product_id), before_order_wh)

    def test_second_dispatch_skips_and_does_not_deduct_twice(self):
        ManualDispatchService.dispatch(
            order_ids=[self.order.id], warehouse_id=self.wh_picked.id, actor=self.actor
        )
        after_first = sellable(self.wh_picked.id, self.item.product_id)

        result = ManualDispatchService.dispatch(
            order_ids=[self.order.id], warehouse_id=self.wh_picked.id, actor=self.actor
        )

        self.assertEqual(result["confirmed"], [])
        self.assertEqual(
            result["skipped"], [{"order_id": self.order.id, "reason": SKIP_ALREADY_DISPATCHED}]
        )
        self.assertEqual(sellable(self.wh_picked.id, self.item.product_id), after_first)

    def test_dispatch_works_for_order_without_kiotviet_invoice(self):
        # Đơn chưa từng đẩy sang KiotViet: verifier trả UNVERIFIABLE (DB là nguồn sự
        # thật), thao tác vẫn phải chạy được — không kẹt vì "chưa xác minh".
        self.assertEqual(self.order.invoice_code, "")

        result = ManualDispatchService.dispatch(
            order_ids=[self.order.id], warehouse_id=self.wh_picked.id, actor=self.actor
        )

        self.assertEqual(result["confirmed"], [self.order.id])

    def test_cancelled_order_is_skipped(self):
        self.order.status = OrderStatus.CANCELLED
        self.order.save(update_fields=["status"])
        before = sellable(self.wh_picked.id, self.item.product_id)

        result = ManualDispatchService.dispatch(
            order_ids=[self.order.id], warehouse_id=self.wh_picked.id, actor=self.actor
        )

        self.assertEqual(result["confirmed"], [])
        self.assertEqual(result["skipped"][0]["reason"], "cancelled")
        self.assertEqual(sellable(self.wh_picked.id, self.item.product_id), before)


class ManualDispatchApiTests(TestCase):
    """Endpoint POST /api/inventory/orders/dispatch — quyền và validate."""

    def setUp(self):
        self.channel = Channel.objects.create(code="md-api", name="Manual Dispatch API")
        self.warehouse = Warehouse.objects.create(name="Kho API", code="KHO-MDAPI")
        self.order = make_order(self.channel, self.warehouse, "42", shipping_code="")
        self.client = APIClient()

    def test_requires_stock_edit_permission(self):
        user = make_user_with_permissions("md-no-perm", ["orders:view"])
        self.client.force_authenticate(user)

        resp = self.client.post(
            "/api/inventory/orders/dispatch",
            {"order_ids": [self.order.id], "warehouse_id": self.warehouse.id},
            format="json",
        )

        self.assertEqual(resp.status_code, 403)

    def test_rejects_inactive_warehouse(self):
        self.warehouse.is_active = False
        self.warehouse.save(update_fields=["is_active"])
        self.client.force_authenticate(make_admin("md-admin"))

        resp = self.client.post(
            "/api/inventory/orders/dispatch",
            {"order_ids": [self.order.id], "warehouse_id": self.warehouse.id},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_dispatch_succeeds_for_admin(self):
        self.client.force_authenticate(make_admin("md-admin-ok"))

        resp = self.client.post(
            "/api/inventory/orders/dispatch",
            {"order_ids": [self.order.id], "warehouse_id": self.warehouse.id},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["confirmed"], [self.order.id])
```

Thêm vào import block đầu file (nếu chưa có):

```python
from apps.inventory.application.services.stock_command_service import StockCommandService
```

- [ ] **Step 3: Chạy test để xác nhận nó ĐỎ**

Run (thay `<PATHS>` trong nguyên mẫu ở Bước 0.2):

```
apps/inventory/tests/test_manual_dispatch.py -q
```

Expected: FAIL — `ImportError: cannot import name 'ManualDispatchService'` /
`ModuleNotFoundError`.

- [ ] **Step 4: Sửa `manual_dispatch_service.py`**

Sửa module docstring thành:

```python
"""Xuất kho tay cho một hoặc nhiều đơn, theo kho do người dùng chỉ định.

Nuôi nút "Xuất kho" trên trang chi tiết đơn. Trước 29/08/2026 module này là hàng đợi
"Chờ xuất kho" (`DirectDispatchService`); hàng đợi đã bị gỡ nhưng khối xử lý verdict
KiotViet bên dưới được giữ NGUYÊN VẸN — nó là lời giải cho đúng bài toán mà tính năng
mới cũng gặp, và đã trả giá một lần ở sự cố 2026-07-31.

Khoá chống trừ tồn hai lần nằm sẵn ở `StockSalesService.issue_for_order`
(`@transaction.atomic`, tự gọi `lock_order_stock` rồi guard theo outstanding). KHÔNG tự
mở thêm transaction/lock ở tầng này — xác minh KiotViet (HTTP) chạy qua
`OrderService._prefetch_dispatch_verdicts` NGOÀI transaction, TRƯỚC khi đưa verdict vào
`update_order_inline`; bọc thêm lock ở đây chỉ tạo một khoá no-op nhả ngay cuối câu
lệnh, đọc như có bảo vệ mà thực ra không có.
"""
```

Đổi hằng số skip — bỏ `SKIP_NOT_WAITING`, thêm `SKIP_NOT_FOUND`:

```python
# Lý do bỏ qua khi xuất kho — trả thẳng cho FE thay vì im lặng bỏ dòng.
SKIP_NOT_FOUND = "not_found"
SKIP_CANCELLED = "cancelled"
SKIP_ALREADY_DISPATCHED = "already_dispatched"
```

Giữ nguyên hàm module-level `_dispatched_order_ids` (không đổi tên — `stock_query_service`
đang import nó).

**Xoá hẳn** `list_pending`. Đổi `class DirectDispatchService` → `class ManualDispatchService`
và đổi chữ ký `confirm` → `dispatch`:

```python
class ManualDispatchService:
    @staticmethod
    def dispatch(*, order_ids: list[int], warehouse_id: int, actor) -> dict:
```

Giữ NGUYÊN toàn bộ docstring dài của hàm (khối giải thích prefetch verdict / verdict-là-
exception) — chỉ đổi câu mở đầu thành:

```python
        """Xuất kho tay: ghi kho đã chọn vào đơn rồi trừ tồn với mốc là thời điểm bấm.
```

Trong vòng 1 (lọc theo trạng thái DB), thay nhánh `not order.shipping_type == DIRECT ...`.
Khối mới:

```python
        candidates: list[Order] = []
        for order_id in order_ids:
            order = orders_by_id.get(order_id)
            if order is None:
                skipped.append({"order_id": order_id, "reason": SKIP_NOT_FOUND})
                continue
            if order.status == OrderStatus.CANCELLED:
                skipped.append({"order_id": order_id, "reason": SKIP_CANCELLED})
                continue
            candidates.append(order)
```

Bỏ import `ShippingType` khỏi hàm (không còn dùng) — giữ `Order, OrderStatus`.

Trong vòng ghi sổ, **trước** lời gọi `update_order_inline`, ghi kho đã chọn:

```python
            if order.fulfillment_warehouse_id != warehouse_id:
                # Ghi đè có chủ đích: sổ kho và đơn phải nói cùng một chuyện. Ghi TRƯỚC
                # khi ghi sổ vì `update_order_inline` đọc `order.fulfillment_warehouse_id`
                # để biết trừ ở kho nào.
                order.fulfillment_warehouse_id = warehouse_id
                order.save(update_fields=["fulfillment_warehouse", "updated_at"])
```

- [ ] **Step 5: Sửa `manual_dispatch_views.py`**

Thay toàn bộ nội dung file bằng:

```python
"""Xuất kho tay từ trang chi tiết đơn.

Phân quyền theo đúng luật đã ghi ở `flow_pending_views.py`: hành động GHI (trừ tồn) gate
bằng "stock:edit". Không đẻ permission mới — permission mới lại phải nhớ gán role.
"""
from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.inventory.application.services.manual_dispatch_service import ManualDispatchService
from apps.users.permissions import require_permission


class _DispatchInputSerializer(drf.Serializer):
    order_ids = drf.ListField(
        child=drf.IntegerField(min_value=1), allow_empty=False, max_length=200
    )
    warehouse_id = drf.IntegerField(min_value=1)

    def validate_warehouse_id(self, value: int) -> int:
        # Kho đã tắt/xoá mềm vẫn còn id hợp lệ trong DB — không chặn ở đây thì sổ ghi
        # vào một kho không ai còn nhìn tới.
        from apps.warehouses.models import Warehouse

        exists = Warehouse.objects.filter(
            pk=value, is_active=True, deleted_at__isnull=True
        ).exists()
        if not exists:
            raise drf.ValidationError("Kho không tồn tại hoặc đã ngừng hoạt động.")
        return value


@extend_schema(
    request=_DispatchInputSerializer,
    responses={
        200: inline_serializer(
            "ManualDispatchResult",
            fields={
                "confirmed": drf.ListField(child=drf.IntegerField()),
                "skipped": inline_serializer(
                    "ManualDispatchSkipped",
                    many=True,
                    fields={"order_id": drf.IntegerField(), "reason": drf.CharField()},
                ),
            },
        ),
        400: OpenApiResponse(description="Danh sách đơn hoặc kho không hợp lệ"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("stock:edit")])
def manual_dispatch(request):
    serializer = _DispatchInputSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    result = ManualDispatchService.dispatch(
        order_ids=serializer.validated_data["order_ids"],
        warehouse_id=serializer.validated_data["warehouse_id"],
        actor=request.user,
    )
    return Response(result, status=status.HTTP_200_OK)
```

- [ ] **Step 6: Sửa `apps/inventory/urls.py`**

Thay import cũ:

```python
from apps.inventory.transport.views.direct_dispatch_views import (
    direct_dispatch_confirm,
    direct_dispatch_pending,
)
```

bằng:

```python
from apps.inventory.transport.views.manual_dispatch_views import manual_dispatch
```

Xoá 2 entry `direct-dispatch/pending` và `direct-dispatch/confirm`, thêm:

```python
    path(
        "orders/dispatch",
        manual_dispatch,
        name="inventory-manual-dispatch",
    ),
```

- [ ] **Step 7: Sửa import ở `stock_query_service.py`**

Trong `_pending_direct_by_product`, đổi:

```python
        from apps.inventory.application.services.direct_dispatch_service import (
            _dispatched_order_ids,
        )
```

thành:

```python
        from apps.inventory.application.services.manual_dispatch_service import (
            _dispatched_order_ids,
        )
```

Và sửa docstring của hàm: cụm "đang chờ kho xác nhận xuất" → "đã lên hoá đơn nhưng sổ
chưa trừ tồn"; cụm "xem `direct_dispatch_service.py`" → "xem
`manual_dispatch_service.py`"; cụm "`_dispatched_order_ids` của Task 5" → "`_dispatched_order_ids`".

- [ ] **Step 8: Bỏ setting `DIRECT_DISPATCH_WARN_DAYS`**

Xoá dòng khai báo trong `backend/config/settings_parts/base.py`. Xác nhận không còn ai đọc:

```bash
grep -rn "DIRECT_DISPATCH_WARN_DAYS" backend/ || echo "clean"
```

- [ ] **Step 9: Sửa `backend/.importlinter`**

Đổi 3 dòng ở khối ~249 từ `apps.inventory.application.services.direct_dispatch_service`
sang `apps.inventory.application.services.manual_dispatch_service` (giữ nguyên 3 đích
`apps.orders.crm_catalog`, `apps.orders.models`, `apps.orders.services`).

- [ ] **Step 10: Chạy test cho tới khi XANH**

Run: `apps/inventory/tests/test_manual_dispatch.py -q`
Expected: PASS toàn bộ.

- [ ] **Step 11: Chạy import-linter và ruff**

```bash
MSYS_NO_PATHCONV=1 docker run --rm -e HOME=/tmp -v "$(pwd -W)/backend:/app" -w //app \
  desi-backend-test:latest \
  sh -c 'pip install -q --user import-linter && /tmp/.local/bin/lint-imports --config .importlinter'
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

Expected: cả hai sạch.

- [ ] **Step 12: Commit**

```bash
git add -A backend
git commit -m "feat(inventory): replace pending dispatch queue with manual dispatch endpoint"
```

---

## Task 2: Backend — quét kho khớp thêm mã hoá đơn

**Files:**
- Modify: `backend/apps/inventory/application/services/pending_scan_service.py` (`_process_one` ~dòng 606, `_dispatch_and_finalize`, `_complete_dispatch` ~dòng 1228)
- Test: `backend/apps/inventory/tests/test_pending_scan_match.py`

**Interfaces:**
- Consumes: `ManualDispatchService` (Task 1) — không gọi trực tiếp, chỉ dùng chung định nghĩa "đã xuất".
- Produces: `_complete_dispatch(scan, order, actor=None, *, warehouse_id: int)` — tham số `warehouse_id` **bắt buộc, keyword-only**, do người gọi quyết định theo đường khớp.

- [ ] **Step 1: Viết test thất bại**

Thêm vào cuối `backend/apps/inventory/tests/test_pending_scan_match.py`:

```python
class TestInvoiceCodeMatching:
    """Quét XUẤT khớp thêm theo mã hoá đơn — đơn bán trực tiếp không có mã vận đơn."""

    def test_invoice_code_matches_and_deducts_at_order_warehouse(
        self, channel, warehouse, staff_user
    ):
        other_wh = Warehouse.objects.create(name="Kho quet", code="KT-SCAN")
        order = _make_order(channel, warehouse, "80", shipping_code="", qty=2)
        order.invoice_code = "HD8000"
        order.save(update_fields=["invoice_code"])
        item = order.items.first()
        before = _sellable_qty(warehouse.id, item.product_id)
        # Mã quét ở kho KHÁC kho của đơn: sổ phải ghi theo KHO CỦA ĐƠN.
        PendingShipmentScan.objects.create(
            code="HD8000", kind=PendingScanKind.DISPATCH,
            warehouse=other_wh, scanned_by=staff_user,
        )

        with patch(
            "apps.kiotviet.application.services.cancellation_verifier"
            ".InvoiceCancellationVerifier.verify_not_cancelled",
            return_value=CancelCheck.OK,
        ):
            PendingScanService.match_pending_scans()

        scan = PendingShipmentScan.objects.get(code="HD8000")
        assert scan.status == PendingScanStatus.MATCHED
        assert scan.matched_order_id == order.id
        assert _sellable_qty(warehouse.id, item.product_id) == before - item.quantity
        assert _sellable_qty(other_wh.id, item.product_id) == 0

    def test_shipping_code_wins_over_invoice_code(self, channel, warehouse, staff_user):
        order_a = _make_order(channel, warehouse, "81", shipping_code="DUP001", qty=1)
        order_b = _make_order(channel, warehouse, "82", shipping_code="", qty=1)
        order_b.invoice_code = "DUP001"
        order_b.save(update_fields=["invoice_code"])
        PendingShipmentScan.objects.create(
            code="DUP001", kind=PendingScanKind.DISPATCH,
            warehouse=warehouse, scanned_by=staff_user,
        )

        with patch(
            "apps.kiotviet.application.services.cancellation_verifier"
            ".InvoiceCancellationVerifier.verify_not_cancelled",
            return_value=CancelCheck.OK,
        ):
            PendingScanService.match_pending_scans()

        scan = PendingShipmentScan.objects.get(code="DUP001")
        assert scan.matched_order_id == order_a.id

    def test_invoice_match_without_order_warehouse_fails_loudly(
        self, channel, warehouse, staff_user
    ):
        order = _make_order(channel, warehouse, "83", shipping_code="", qty=1)
        order.invoice_code = "HD8300"
        order.fulfillment_warehouse = None
        order.save(update_fields=["invoice_code", "fulfillment_warehouse"])
        item = order.items.first()
        before = _sellable_qty(warehouse.id, item.product_id)
        PendingShipmentScan.objects.create(
            code="HD8300", kind=PendingScanKind.DISPATCH,
            warehouse=warehouse, scanned_by=staff_user,
        )

        with patch(
            "apps.kiotviet.application.services.cancellation_verifier"
            ".InvoiceCancellationVerifier.verify_not_cancelled",
            return_value=CancelCheck.OK,
        ):
            PendingScanService.match_pending_scans()

        scan = PendingShipmentScan.objects.get(code="HD8300")
        assert scan.status == PendingScanStatus.FAILED
        assert "kho xuat" in scan.error.lower() or "kho xuất" in scan.error.lower()
        # Không trừ nhầm vào kho của màn quét.
        assert _sellable_qty(warehouse.id, item.product_id) == before

    def test_return_scan_does_not_match_invoice_code(self, channel, warehouse, staff_user):
        order = _make_order(channel, warehouse, "84", shipping_code="", qty=1)
        order.invoice_code = "HD8400"
        order.save(update_fields=["invoice_code"])
        PendingShipmentScan.objects.create(
            code="HD8400", kind=PendingScanKind.RETURN,
            warehouse=warehouse, scanned_by=staff_user,
        )

        PendingScanService.match_pending_scans()

        scan = PendingShipmentScan.objects.get(code="HD8400")
        assert scan.status == PendingScanStatus.PENDING
        assert scan.matched_order_id is None

    def test_shipping_code_path_still_uses_scan_warehouse(
        self, channel, warehouse, staff_user
    ):
        # Khoá hành vi cũ: đơn giao vận vẫn trừ ở kho của MÀN QUÉT, không phải kho đơn.
        order_wh = Warehouse.objects.create(name="Kho don cu", code="KT-OLD")
        order = _make_order(channel, order_wh, "85", shipping_code="SPX85", qty=1)
        item = order.items.first()
        StockCommandService.record_movement(
            warehouse_id=warehouse.id, product_id=item.product_id,
            qty_delta=5, movement_type=MovementType.RECEIPT,
        )
        before_scan_wh = _sellable_qty(warehouse.id, item.product_id)
        PendingShipmentScan.objects.create(
            code="SPX85", kind=PendingScanKind.DISPATCH,
            warehouse=warehouse, scanned_by=staff_user,
        )

        with patch(
            "apps.kiotviet.application.services.cancellation_verifier"
            ".InvoiceCancellationVerifier.verify_not_cancelled",
            return_value=CancelCheck.OK,
        ):
            PendingScanService.match_pending_scans()

        assert _sellable_qty(warehouse.id, item.product_id) == before_scan_wh - item.quantity
```

- [ ] **Step 2: Chạy test để xác nhận ĐỎ**

Run: `apps/inventory/tests/test_pending_scan_match.py::TestInvoiceCodeMatching -q`
Expected: FAIL — mã hoá đơn không khớp được đơn nào nên scan ở `PENDING`.

- [ ] **Step 3: Sửa query dò đơn trong `_process_one`**

Thêm import ở đầu file (khối `from django.db.models import ...`): thêm `Case`, `When`
vào danh sách đang có `Q, Sum`.

Thay khối `orders = list(...)` (hiện `Order.objects.filter(shipping_code=scan.code, ...)`) bằng:

```python
            # Quét XUẤT khớp thêm theo MÃ HOÁ ĐƠN: đơn bán trực tiếp không có mã vận
            # đơn nên mã hoá đơn là thứ duy nhất kho quét được. Quét HOÀN giữ nguyên
            # chỉ theo mã vận đơn — `_complete_return` ghi `scan.code` thẳng vào
            # `ReturnEntry.shipping_code` và 4 chỗ khác đọc lại cột đó để chống ghi
            # nhận trùng; cho mã hoá đơn vào đấy là phá đối chiếu phiếu hoàn.
            code_filter = Q(shipping_code=scan.code)
            if scan.kind == PendingScanKind.DISPATCH:
                code_filter |= Q(invoice_code=scan.code)
            orders = list(
                Order.objects.filter(code_filter, deleted_at__isnull=True)
                # select_related như lookup/dispatch batch: mọi nhánh đọc trạng thái kênh
                # KiotViet của đơn (verify/ghi sổ/log) không tốn thêm 1 SELECT lazy mỗi mã.
                # `fulfillment_warehouse` thêm vào cho nhánh khớp theo mã hoá đơn.
                .select_related("kiotviet_channel", "fulfillment_warehouse")
                .prefetch_related("items")
                # Mã vận đơn THẮNG tuyệt đối: một mã trùng cả `shipping_code` đơn A lẫn
                # `invoice_code` đơn B phải khớp A, để không đơn giao vận nào đổi hành vi.
                .annotate(
                    _by_shipping=Case(
                        When(shipping_code=scan.code, then=1), default=0
                    )
                )
                .order_by("-_by_shipping", "-id")[:2]
            )
```

Ngay sau khi chọn `order = orders[0]`, tính kho ghi sổ và chặn sớm đơn thiếu kho:

```python
            order = orders[0]
            multi_note = "Nhiều đơn cùng mã — lấy đơn mới nhất. " if len(orders) > 1 else ""
            # Kho ghi sổ tách theo ĐƯỜNG KHỚP: mã vận đơn → kho của màn quét (hành vi
            # cũ, không đổi); mã hoá đơn → kho xuất của ĐƠN (quyết định nghiệp vụ
            # 29/08/2026 — kho do sale chọn lúc tạo đơn là chủ đích, người quét có thể
            # đang để kho mặc định).
            matched_by_shipping = str(order.shipping_code or "") == scan.code
            dispatch_warehouse_id = (
                scan.warehouse_id if matched_by_shipping else order.fulfillment_warehouse_id
            )
            if scan.kind == PendingScanKind.DISPATCH and not dispatch_warehouse_id:
                # Rơi về kho màn quét là trừ nhầm kho — sai số im lặng. FAILED thì kho
                # nhìn thấy và sửa được.
                scan.status = PendingScanStatus.FAILED
                scan.matched_order = order
                scan.error = (
                    multi_note
                    + "Đơn thiếu kho xuất — bổ sung kho trên đơn rồi quét lại."
                ).strip()
                scan.save()
                return "failed", False
```

Truyền `dispatch_warehouse_id` xuống nhánh dispatch: đổi `dispatch_ctx` thành 4 phần tử.

```python
                dispatch_ctx = (scan, order, multi_note, dispatch_warehouse_id)
```

Và ở cuối hàm:

```python
        scan, order, multi_note, dispatch_warehouse_id = dispatch_ctx
        result = PendingScanService._dispatch_and_finalize(
            scan=scan,
            order=order,
            actor=None,
            multi_note=multi_note,
            allowed_statuses=[PendingScanStatus.PENDING],
            warehouse_id=dispatch_warehouse_id,
        )
```

- [ ] **Step 4: Cho `warehouse_id` chảy qua `_dispatch_and_finalize` → `_complete_dispatch`**

`_dispatch_and_finalize`: thêm tham số keyword-only `warehouse_id: int` vào chữ ký, và
truyền xuống:

```python
            result_ref, note, warnings = PendingScanService._complete_dispatch(
                scan, order, actor=actor, warehouse_id=warehouse_id
            )
```

`_complete_dispatch`: thêm `*, warehouse_id: int` vào chữ ký, và thay
`warehouse_id=scan.warehouse_id` bằng `warehouse_id=warehouse_id` trong lời gọi
`OrderService.dispatch_orders_batch`. Cập nhật docstring:

```python
        """Xuất kho cho 1 đơn tại `warehouse_id` do người gọi quyết định. GỌI NGOÀI
        TRANSACTION: bên trong có HTTP tới KiotViet (xác minh đơn chưa hủy) — xem
        `_dispatch_and_finalize`.

        `warehouse_id` KHÔNG mặc định về `scan.warehouse_id`: từ 29/08/2026 mã khớp theo
        hoá đơn ghi sổ ở kho của ĐƠN, còn mã vận đơn giữ kho của màn quét. Để mặc định ở
        đây thì nhánh mới sẽ âm thầm trừ sai kho.

        Trả về ``(result_ref, thông điệp, danh sách warning thô)``. Nhánh hàng gửi bù cần
        LIST gốc để so `already_dispatched`; khớp chuỗi trong thông điệp là mong manh."""
```

- [ ] **Step 5: Sửa hai lời gọi `_dispatch_and_finalize` còn lại**

`manual_match` và `unmatch_dispatch`/các chỗ khác trong file cũng gọi
`_dispatch_and_finalize`. Tìm hết và truyền `warehouse_id=scan.warehouse_id` cho chúng —
khớp TAY là người dùng chỉ định đơn từ màn quét nên kho của màn quét là đúng:

```bash
grep -n "_dispatch_and_finalize(" backend/apps/inventory/application/services/pending_scan_service.py
```

Mỗi lời gọi ngoài `_process_one` thêm `warehouse_id=scan.warehouse_id,`.

- [ ] **Step 6: Chạy test cho tới khi XANH**

Run: `apps/inventory/tests/test_pending_scan_match.py apps/inventory/tests/test_pending_scan_manual.py -q`
Expected: PASS toàn bộ (cả test cũ — chúng khoá hành vi mã vận đơn).

- [ ] **Step 7: Commit**

```bash
git add -A backend
git commit -m "feat(inventory): match dispatch scans by invoice code as well as shipping code"
```

---

## Task 3: Backend — dọn docstring và khoá lại luật huỷ đơn

**Files:**
- Modify: `backend/apps/orders/infrastructure/tasks/order_tasks_impl/ingest_job_tasks.py` (docstring `_apply_shipping_type_after_job` ~287-338, thông điệp Lark ~576-580)
- Modify: `backend/apps/inventory/subscribers.py` (**chỉ docstring** `_is_direct_sale`)
- Modify: `backend/apps/orders/models.py` (comment cạnh `direct_dispatch_deferred`)
- Rename + Modify test: `backend/apps/orders/tests/test_direct_deferred_dispatch.py` → `test_quick_sale_invoice_only.py`
- Modify: `backend/.importlinter` (dòng 72)

**Interfaces:**
- Consumes: không có gì mới. Task này chỉ đồng bộ tài liệu trong code với hành vi thật, cộng một test mới khoá spec §4.

- [ ] **Step 1: Viết test thất bại khoá luật huỷ đơn direct CŨ**

Trong `backend/apps/inventory/tests/test_manual_dispatch.py`, class `DeferredCancelTests`
đã có `test_counter_sale_still_releases_immediately`. **Đổi tên** nó cho đúng nghĩa mới và
thêm assert về note — đây là test khoá spec §4:

```python
    def test_legacy_direct_order_still_releases_stock_immediately_on_cancel(self):
        """Đơn direct CŨ (cờ False, tạo trước 28/08/2026, ĐÃ bị trừ tồn lúc lên hoá đơn).

        Test này khoá spec §4. Nó đỏ nghĩa là ai đó vừa xoá cột
        `direct_dispatch_deferred` hoặc ngừng ghi cờ `=True` cho đơn mới — và hệ quả
        trên prod là cộng trả tồn cho hàng CHƯA BAO GIỜ bị trừ. Đọc spec §4 trước khi
        sửa test này.
        """
        from apps.inventory.application.services.stock_sales_service import (
            CANCEL_RELEASE_NOTE,
        )

        self.order.direct_dispatch_deferred = False
        self.order.save(update_fields=["direct_dispatch_deferred"])
        StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.item.product_id, quantity=self.item.quantity)],
        )
        after_issue = sellable(self.warehouse.id, self.item.product_id)

        self._cancel()

        self.assertGreater(sellable(self.warehouse.id, self.item.product_id), after_issue)
        self.assertFalse(CancelledDispatchWatch.objects.filter(order_id=self.order.id).exists())
        self.assertTrue(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=self.order.id,
                note=CANCEL_RELEASE_NOTE,
            ).exists()
        )
```

Trước khi chạy, xác nhận tên hằng và vị trí của nó:

```bash
grep -rn "CANCEL_RELEASE_NOTE" backend/apps/inventory/ | head -5
```

Nếu hằng nằm ở module khác thì sửa import cho đúng — **không** hard-code chuỗi note.

- [ ] **Step 2: Chạy test để xác nhận XANH (test này khoá hành vi ĐANG ĐÚNG)**

Run: `apps/inventory/tests/test_manual_dispatch.py -q`
Expected: PASS. Đây là test hồi quy, không phải TDD cho code mới — nó phải xanh ngay.
Nếu ĐỎ thì dừng lại: nghĩa là Task 1 đã vô tình đụng vào luật huỷ.

- [ ] **Step 3: Đổi tên file test phía orders và sửa nội dung**

```bash
cd backend
git mv apps/orders/tests/test_direct_deferred_dispatch.py \
       apps/orders/tests/test_quick_sale_invoice_only.py
```

Thay module docstring bằng:

```python
"""Quick-sale KiotViet CHỈ lên hoá đơn — nửa phía ORDERS.

Hai mảnh thuộc sở hữu app `orders`: hành động API ghi cờ `direct_dispatch_deferred=True`,
và worker gắn nhãn `shipping_type='direct'` sau khi tạo hoá đơn thành công. Worker KHÔNG
ghi sổ tồn — hàng rời kho qua một trong hai cửa của app inventory (quét mã hoá đơn, hoặc
nút "Xuất kho" ở chi tiết đơn), xem `apps.inventory.tests.test_manual_dispatch`.

Cờ `direct_dispatch_deferred` vẫn phải được ghi `=True`: nó phân biệt đơn direct CŨ (đã
trừ tồn lúc lên hoá đơn) với đơn mới. Xem
docs/superpowers/specs/2026-08-29-warehouse-dispatch-rework-design.md §4.
"""
```

Sửa `.importlinter` dòng 72: `apps.orders.tests.test_direct_deferred_dispatch` →
`apps.orders.tests.test_quick_sale_invoice_only`.

- [ ] **Step 4: Sửa docstring `_apply_shipping_type_after_job`**

Trong `ingest_job_tasks.py`, thay đoạn từ `` `direct` KHÔNG trừ tồn ở đây nữa. `` tới hết
đoạn "Cột cờ vẫn tồn tại..." bằng:

```
    `direct` KHÔNG trừ tồn ở đây. Đơn quick-sale chỉ có hoá đơn KiotViet và cái nhãn
    này; hàng rời kho qua một trong hai cửa của app inventory:
    (1) kho quét MÃ HOÁ ĐƠN ở màn quét xuất (`pending_scan_service`), hoặc
    (2) người dùng bấm nút "Xuất kho" ở chi tiết đơn (`manual_dispatch_service`).
    Tồn phồng trong quãng chờ là HÀNH VI CÓ CHỦ ĐÍCH, không phải job hỏng.

    Hàm này KHÔNG đọc `direct_dispatch_deferred` — tầng API
    (`order_action_view._QUICK_SALE_ACTIONS`) ghi cờ True cho mọi đơn quick-sale nên rẽ
    nhánh theo cờ ở đây chỉ là đường chết. Cột cờ vẫn PHẢI được ghi ở tầng API: nó phân
    biệt đơn direct CŨ (cờ False, tạo trước 28/08/2026, đã bị trừ tồn ngay) với đơn mới,
    và `apps.inventory.subscribers._is_direct_sale` đọc nó để chọn luật huỷ. Xem
    docs/superpowers/specs/2026-08-29-warehouse-dispatch-rework-design.md §4.
```

- [ ] **Step 5: Sửa thông điệp Lark ở nhánh lỗi hậu xử lý**

Thay chuỗi cảnh báo (~dòng 576-580) — bỏ cụm "trừ tồn thủ công theo kho xuất của đơn"
(nay đã có nút chính thức):

```python
                    send_stock_negative_alert(
                        f"⚠️ Đơn #{order.id} (hoá đơn {order.invoice_code or '?'}) đã tạo "
                        "hoá đơn KiotViet THÀNH CÔNG nhưng hậu xử lý lỗi nên đơn chưa được "
                        f"gắn nhãn hình thức xuất hàng (job_id={job.id}, xem log để biết "
                        "nguyên nhân). Kiểm tra đơn rồi dùng nút \"Xuất kho\" ở chi tiết "
                        "đơn khi hàng thật rời kho."
                    )
```

- [ ] **Step 6: Sửa docstring `_is_direct_sale`**

Trong `backend/apps/inventory/subscribers.py`, **không đổi thân hàm**, chỉ docstring:

```python
def _is_direct_sale(order_id: int) -> bool:
    """Đơn bán quầy đã trừ tồn NGAY lúc lên hoá đơn — chỉ nhóm đơn CŨ.

    `direct_dispatch_deferred=False` + `shipping_type='direct'` = đơn tạo TRƯỚC
    28/08/2026, thời kỳ worker còn trừ tồn ngay khi hoá đơn KiotViet tạo xong. Huỷ những
    đơn đó thì hàng còn trong kho nên cộng trả tồn ngay là đúng.

    Đơn từ 28/08/2026 trở đi mang cờ True và CHƯA bị trừ tồn lúc lên hoá đơn, nên rơi
    xuống nhánh "chờ hàng về" — nhánh đó phán quyết bằng SỔ chứ không bằng nhãn nên tự
    đúng ở cả hai trạng thái (chưa xuất → không mở watch; đã xuất → mở watch).

    ĐỪNG bỏ điều kiện `direct_dispatch_deferred=False`. Xem
    docs/superpowers/specs/2026-08-29-warehouse-dispatch-rework-design.md §4.
    """
```

- [ ] **Step 7: Sửa comment cạnh cột trong `apps/orders/models.py`**

Tìm `direct_dispatch_deferred` và đặt comment ngay trên nó:

```python
    # "Đơn bán trực tiếp KHÔNG trừ tồn tại thời điểm lên hoá đơn." Từ 28/08/2026 mọi đơn
    # quick-sale đều mang cờ True. Cờ KHÔNG trả lời "đã xuất chưa" — câu đó do sổ tồn và
    # `delivery_business_status_code` trả lời. Nó phân biệt hai lớp đơn direct có hai luật
    # huỷ khác nhau; xem specs/2026-08-29-warehouse-dispatch-rework-design.md §4.
    direct_dispatch_deferred = models.BooleanField(default=False)
```

- [ ] **Step 8: Chạy test hai app + ruff + import-linter**

Run: `apps/inventory/tests/ apps/orders/tests/test_quick_sale_invoice_only.py -q`
Expected: PASS.

Rồi chạy lại ruff + import-linter như Task 1 Step 11.

- [ ] **Step 9: Commit**

```bash
git add -A backend
git commit -m "docs(inventory): align dispatch comments with the two new entry points"
```

---

## Task 4: Frontend — gỡ trang "Chờ xuất kho"

**Files:**
- Delete: `frontend/src/features/inventory/pages/DirectDispatchPage.tsx`
- Delete: `frontend/src/features/inventory/direct-dispatch-queries.ts`
- Delete: `frontend/src/features/inventory/__tests__/direct-dispatch.test.tsx`
- Modify: `frontend/src/app/routes.tsx` (dòng 78, 270-273)
- Modify: `frontend/src/app/routed-pages.tsx` (dòng 59, 564-566)
- Modify: `frontend/src/app/modules.ts` (dòng ~142-147)
- Modify: `frontend/src/lib/routes.ts` (dòng 34, 66, 232-236)
- Modify: `frontend/src/app/__tests__/modules.test.ts` (dòng 44)
- Modify: `frontend/src/i18n/messages/*` — bỏ khoá `topnav.tabs.directDispatch`

**Interfaces:**
- Produces: không còn route `inventory/direct-dispatch` và không còn section key `direct-dispatch`.

- [ ] **Step 1: Sửa test trước — bỏ `direct-dispatch` khỏi danh sách kỳ vọng**

Trong `frontend/src/app/__tests__/modules.test.ts`, xoá dòng `"direct-dispatch",` khỏi
mảng section keys.

- [ ] **Step 2: Chạy test để xác nhận ĐỎ**

```bash
cd frontend && npx vitest run src/app/__tests__/modules.test.ts
```

Expected: FAIL — module thật vẫn còn section `direct-dispatch` nên không khớp danh sách.

- [ ] **Step 3: Xoá 3 file**

```bash
cd frontend
git rm src/features/inventory/pages/DirectDispatchPage.tsx \
       src/features/inventory/direct-dispatch-queries.ts \
       src/features/inventory/__tests__/direct-dispatch.test.tsx
```

- [ ] **Step 4: Gỡ wiring**

- `src/app/routes.tsx`: xoá `DirectDispatchRoute,` khỏi import block và xoá cả entry route
  `{ path: "inventory/direct-dispatch", ... }`.
- `src/app/routed-pages.tsx`: xoá dòng `const DirectDispatchPage = lazy(...)` và hàm
  `export function DirectDispatchRoute()`.
- `src/app/modules.ts`: xoá object `{ key: "direct-dispatch", labelKey: "topnav.tabs.directDispatch", icon: Clock, requires: "stock" }`. **Kiểm tra icon `Clock` còn ai dùng không** — không thì xoá khỏi import block, nếu không sẽ đỏ `lint:strict`.
- `src/lib/routes.ts`: xoá `| "direct-dispatch"` (dòng 34), `"direct-dispatch": "/inventory/direct-dispatch",` (dòng 66), và cả entry `"direct-dispatch": "stock",` cùng khối comment 3 dòng phía trên nó (232-236) — comment đó chỉ nói về entry sắp xoá.
- Bỏ khoá i18n `topnav.tabs.directDispatch`:

```bash
grep -rn "directDispatch" src/i18n/
```

Xoá mọi dòng tìm được.

- [ ] **Step 5: Chạy test + tsc**

```bash
cd frontend
npx vitest run src/app/__tests__/modules.test.ts src/app/__tests__/nav-layout.test.ts
npx tsc --noEmit
```

Expected: test PASS; `tsc` sạch (bỏ qua lỗi đỏ sẵn `audit-logs-api.test.ts` nếu còn — đó
là baseline của master, xem ghi chú `master-red-tsc-audit-logs-test`).

- [ ] **Step 6: Commit**

```bash
git add -A frontend
git commit -m "feat(inventory): remove pending dispatch queue page"
```

---

## Task 5: Frontend — nút "Xuất kho" + popup chọn kho ở chi tiết đơn

**Files:**
- Create: `frontend/src/features/inventory/manual-dispatch-queries.ts`
- Create: `frontend/src/features/orders/ManualDispatchDialog.tsx`
- Create: `frontend/src/features/orders/__tests__/manual-dispatch.test.tsx`
- Modify: `frontend/src/pages/OrderDetailPage.tsx`
- Modify: `frontend/src/i18n/messages/orders.ts`

**Interfaces:**
- Consumes: `POST /api/inventory/orders/dispatch` (Task 1), body `{order_ids, warehouse_id}`, trả `{confirmed: number[], skipped: {order_id: number, reason: string}[]}`.
- Consumes: `useWarehousesList({ isActive: true, limit: 200, offset: 0 })` từ `@/features/warehouses/queries`.
- Consumes: `usePermission` từ `@/hooks/usePermission`.
- Produces: `useManualDispatch()` — mutation nhận `{orderIds: number[], warehouseId: number}`.
- Produces: `<ManualDispatchDialog open onOpenChange orderId defaultWarehouseId items />`.

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/features/orders/__tests__/manual-dispatch.test.tsx`. Bám khuôn của
`src/features/inventory/__tests__/direct-dispatch.test.tsx` cũ (đọc nó trong git history
nếu cần: `git show HEAD~1:frontend/src/features/inventory/__tests__/direct-dispatch.test.tsx`).

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setDesktopViewport } from "@/test/viewport";

import { ManualDispatchDialog } from "../ManualDispatchDialog";

const fetchJson = vi.fn();
vi.mock("@/services/http", () => ({ fetchJson: (...a: unknown[]) => fetchJson(...a) }));
vi.mock("@/features/warehouses/queries", () => ({
  useWarehousesList: () => ({
    data: {
      items: [
        { id: 1, code: "K1", name: "Kho 1", is_active: true },
        { id: 2, code: "K2", name: "Kho 2", is_active: true },
      ],
      total: 2,
    },
    isLoading: false,
  }),
}));

function renderDialog(defaultWarehouseId: number | null = 1) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ManualDispatchDialog
        open
        onOpenChange={() => {}}
        orderId={99}
        defaultWarehouseId={defaultWarehouseId}
        items={[{ product_name: "SP A", sku: "SKU-A", quantity: 2 }]}
      />
    </QueryClientProvider>,
  );
}

describe("ManualDispatchDialog", () => {
  beforeEach(() => {
    setDesktopViewport();
    fetchJson.mockReset();
    fetchJson.mockResolvedValue({ confirmed: [99], skipped: [] });
  });

  it("mặc định chọn kho xuất hiện tại của đơn", async () => {
    renderDialog(2);
    await waitFor(() => {
      expect(screen.getByRole("combobox")).toHaveValue("2");
    });
  });

  it("cảnh báo rõ thao tác này trừ tồn kho", () => {
    renderDialog();
    expect(screen.getByText(/trừ tồn kho/i)).toBeInTheDocument();
  });

  it("gửi đúng payload order_ids + warehouse_id", async () => {
    renderDialog(1);
    await userEvent.click(screen.getByRole("button", { name: /xuất kho/i }));

    await waitFor(() => {
      expect(fetchJson).toHaveBeenCalledWith(
        "/api/inventory/orders/dispatch",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ order_ids: [99], warehouse_id: 1 }),
        }),
      );
    });
  });

  it("chặn bấm khi chưa chọn kho", () => {
    renderDialog(null);
    expect(screen.getByRole("button", { name: /xuất kho/i })).toBeDisabled();
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận ĐỎ**

```bash
cd frontend && npx vitest run src/features/orders/__tests__/manual-dispatch.test.tsx
```

Expected: FAIL — không tìm thấy module `../ManualDispatchDialog`.

- [ ] **Step 3: Tạo `manual-dispatch-queries.ts`**

```ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { fetchJson } from "@/services/http";
import { getApiErrorMessage } from "@/lib/errors";

export type ManualDispatchResult = {
  confirmed: number[];
  skipped: { order_id: number; reason: string }[];
};

// Backend trả mã máy tiếng Anh cho 3 nhánh skip biết trước (hợp đồng API — xem
// `SKIP_NOT_FOUND`/`SKIP_CANCELLED`/`SKIP_ALREADY_DISPATCHED` ở manual_dispatch_service.py,
// ĐỪNG đổi giá trị các mã này). Nhánh còn lại đi qua `str(exc)` nên đã LÀ câu tiếng Việt
// hoàn chỉnh — map dưới đây chỉ dịch 3 mã máy, còn lại hiển thị nguyên văn.
export const MANUAL_DISPATCH_SKIP_LABELS: Record<string, string> = {
  not_found: "Không tìm thấy đơn.",
  cancelled: "Đơn đã bị hủy.",
  already_dispatched: "Đơn đã được xuất kho trước đó.",
};

/**
 * Xuất kho tay cho đơn — TRỪ TỒN thật, không phải đánh dấu suông và không hoàn tác dễ
 * dàng. Chỉ gọi sau khi người dùng xác nhận qua dialog.
 */
export function useManualDispatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ orderIds, warehouseId }: { orderIds: number[]; warehouseId: number }) =>
      fetchJson("/api/inventory/orders/dispatch", {
        method: "POST",
        body: JSON.stringify({ order_ids: orderIds, warehouse_id: warehouseId }),
      }) as Promise<ManualDispatchResult>,
    onSuccess: (result) => {
      if (result.confirmed.length > 0) toast.success("Đã xuất kho.");
      for (const row of result.skipped) {
        toast.warning(MANUAL_DISPATCH_SKIP_LABELS[row.reason] ?? row.reason);
      }
      qc.invalidateQueries({ queryKey: ["orders"] });
      qc.invalidateQueries({ queryKey: ["inventory"] });
    },
    onError: (err) => {
      toast.error(getApiErrorMessage(err, "Xuất kho thất bại."));
    },
  });
}
```

- [ ] **Step 4: Tạo `ManualDispatchDialog.tsx`**

Dùng `Dialog`/`DialogContent`/`DialogHeader`/`DialogTitle`/`DialogDescription`/`DialogFooter`
từ `@/components/ui/dialog`, `Button`, và một `<select>` gốc gắn `role="combobox"` (test
đọc theo role này). Yêu cầu bắt buộc của component:

- `defaultWarehouseId` khởi tạo state chọn kho; `null` thì để rỗng và **disable** nút.
- Liệt kê `items` (tên SP, SKU, SL) để người bấm thấy đúng cái mình sắp trừ.
- Có câu cảnh báo chứa cụm **"trừ tồn kho"** (test khớp regex này).
- Nút chính nhãn chứa **"Xuất kho"**, `disabled` khi chưa chọn kho hoặc mutation đang chạy.
- Bấm xong gọi `useManualDispatch().mutate({ orderIds: [orderId], warehouseId })`, thành
  công thì `onOpenChange(false)`.

- [ ] **Step 5: Chạy test cho tới khi XANH**

```bash
cd frontend && npx vitest run src/features/orders/__tests__/manual-dispatch.test.tsx
```

Expected: 4 test PASS.

- [ ] **Step 6: Gắn nút vào `OrderDetailPage.tsx`**

`OrderDetailPage` đã import `useWarehousesList`. Thêm:

```tsx
import { PackageCheck } from "lucide-react";
import { usePermission } from "@/hooks/usePermission";
import { ManualDispatchDialog } from "@/features/orders/ManualDispatchDialog";
```

Trong component, cạnh các state khác:

```tsx
const perm = usePermission();
const [dispatchOpen, setDispatchOpen] = useState(false);
// Cờ này CHỈ để dựng UI. Chốt chặn thật là guard backend (trả skipped
// `already_dispatched`) — nó đọc thêm sổ tồn, thứ payload đơn không mang theo.
const alreadyDispatched =
  (effectiveOrder || order)?.delivery_business_status_code === "dispatched";
const canDispatch = perm("stock:edit") && !alreadyDispatched;
```

Trong khối nút ở topbar (cạnh nút Lưu, ~dòng 774-796):

```tsx
{canDispatch && (
  <Button variant="outline" size="sm" onClick={() => setDispatchOpen(true)} className="gap-1.5">
    <PackageCheck className="h-4 w-4" />
    <span className="hidden sm:inline">{t("orders.detail.dispatch", "Xuất kho")}</span>
  </Button>
)}
```

Và render dialog cuối component:

```tsx
{dispatchOpen && order && (
  <ManualDispatchDialog
    open={dispatchOpen}
    onOpenChange={setDispatchOpen}
    orderId={order.id}
    defaultWarehouseId={(effectiveOrder || order).fulfillment_warehouse ?? null}
    items={(products ?? []).map((p) => ({
      product_name: p.product_name ?? "",
      sku: p.sku ?? "",
      quantity: p.quantity ?? 0,
    }))}
  />
)}
```

Kiểm tên biến danh sách sản phẩm thật trong file (`products` là phỏng đoán) trước khi dán:

```bash
grep -n "ProductRow\|products" frontend/src/pages/OrderDetailPage.tsx | head -10
```

- [ ] **Step 7: Thêm chuỗi i18n**

Trong `frontend/src/i18n/messages/orders.ts` thêm `"orders.detail.dispatch": "Xuất kho"`
theo đúng khuôn các khoá lân cận.

- [ ] **Step 8: Chạy test + tsc + lint**

```bash
cd frontend
npx vitest run src/features/orders/__tests__/ src/pages
npx tsc --noEmit
npm run lint:strict
```

Expected: PASS / sạch.

- [ ] **Step 9: Commit**

```bash
git add -A frontend
git commit -m "feat(orders): add manual dispatch button with warehouse picker on order detail"
```

---

## Task 6: Đổi nhãn `pending_direct`, regen schema, chạy toàn bộ kiểm tra

**Files:**
- Modify: `frontend/src/features/orders/order-form/OrderFormStockSummary.tsx`
- Modify: `frontend/src/i18n/messages/orders.ts` (khoá `orders.stock.pendingDirectLabel`)
- Modify: `backend/schema.yaml`, `frontend/src/api/schema.d.ts`

**Interfaces:**
- Consumes: mọi thay đổi endpoint của Task 1.

- [ ] **Step 1: Đổi nhãn hiển thị**

Trong `OrderFormStockSummary.tsx` đổi fallback của khoá i18n:

```tsx
                    ({t("orders.stock.pendingDirectLabel", "đã lên HĐ, chưa xuất")}{" "}
```

Và sửa comment JSDoc phía trên (dòng ~16-18):

```
 * `pending_direct` (nếu > 0) hiện thêm số lượng đã lên hoá đơn nhưng sổ chưa trừ tồn
 * (đơn bán trực tiếp chờ kho quét mã hoá đơn hoặc bấm "Xuất kho") — CHỈ LÀ CẢNH BÁO
 * tham khảo, không chặn chọn hàng: hệ thống cố ý không có khái niệm giữ chỗ trong sổ.
```

Cập nhật giá trị khoá `orders.stock.pendingDirectLabel` trong `src/i18n/messages/orders.ts`.

- [ ] **Step 2: Chạy test form đơn**

```bash
cd frontend && npx vitest run src/features/orders/__tests__/OrderFormDialog.stock-availability.test.tsx
```

Expected: PASS. Nếu test assert chuỗi "chờ xuất" thì sửa test theo nhãn mới.

- [ ] **Step 3: Regen schema**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/warehouse-dispatch-rework
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_wdr_schema \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  python manage.py spectacular --file schema.yaml
```

Rồi sinh lại type FE theo lệnh của repo (kiểm `frontend/package.json` script nào gọi
`openapi-typescript`).

**Bẫy:** `schema.yaml` đã cũ sẵn ~444 dòng nên regen sẽ kéo theo nhiễu của feature khác.
Xem diff và **chỉ giữ** phần thuộc MR này:

```bash
git diff --stat backend/schema.yaml frontend/src/api/schema.d.ts
git diff backend/schema.yaml | head -100
```

Phần thuộc MR này: bỏ `DirectDispatchPending*` / `DirectDispatchConfirm*` và 2 path
`direct-dispatch/*`; thêm `ManualDispatchResult` / `ManualDispatchSkipped` và path
`/api/inventory/orders/dispatch`. Mọi thay đổi khác → `git checkout -p` bỏ đi.

- [ ] **Step 4: Chạy toàn bộ suite backend hai app**

Suite dài — chạy nền, redirect ra file log (shell nền của harness timeout 10 phút nhưng
container vẫn chạy tiếp):

```bash
MSYS_NO_PATHCONV=1 docker run -d --name wdr-suite --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_wdr_suite \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=8 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory apps/orders apps/common -q --create-db'
```

Theo dõi bằng `docker logs -f wdr-suite`, xong thì `docker rm wdr-suite`.

Expected: PASS. `apps/common` có trong danh sách vì chạy thiếu nó là bỏ sót nợ đỏ.

- [ ] **Step 5: Chạy toàn bộ suite frontend**

```bash
cd frontend && npm run test:ci 2>&1 | tail -20
```

So với baseline đo ở Bước 0.4. Chỉ chấp nhận đỏ nếu **giống hệt** baseline.

- [ ] **Step 6: Chạy ruff + import-linter + lint FE lần cuối**

Như Task 1 Step 11, cộng:

```bash
cd frontend && npx tsc --noEmit && npm run lint:strict
```

- [ ] **Step 7: Commit và push, tạo MR**

```bash
git add -A
git commit -m "chore: relabel pending direct stock hint and regenerate api schema"
git push -o merge_request.create -o merge_request.target=master \
  -o merge_request.title="Remove pending dispatch queue, add invoice-code scan and manual dispatch" \
  origin feat/warehouse-dispatch-rework
```

Gửi link MR cho chủ dự án kèm 3 điểm cần biết trước khi merge:
1. Đây là **đổi quy trình kho** — phải báo bộ phận kho trước khi deploy.
2. Trước khi roll code phải chụp `GET /api/inventory/direct-dispatch/pending` trên prod.
3. Không có migration; roll code BE rồi FE.

---

## Self-Review

**Spec coverage:**

| Spec | Task |
|---|---|
| §4 giữ cột + luật huỷ | Task 3 Step 1/6/7 (test khoá + docstring) |
| §5.1 query dò đơn | Task 2 Step 3 |
| §5.2 kho theo đường khớp | Task 2 Step 3/4 |
| §5.3 không áp cho quét HOÀN | Task 2 Step 1 (test) + Step 3 (`if kind == DISPATCH`) |
| §5.4 không đụng khớp tay | Task 2 Step 5 (truyền `scan.warehouse_id`) |
| §6.1 phạm vi nút | Task 1 Step 4 (bỏ guard `shipping_type`) |
| §6.2 service | Task 1 Step 4 |
| §6.3 endpoint | Task 1 Step 5/6 |
| §6.4 frontend | Task 5 |
| §7 gỡ bỏ BE | Task 1 Step 6/8/9 |
| §7 gỡ bỏ FE | Task 4 |
| §7 giữ `pending_direct` (đổi nhãn) | Task 6 Step 1 |
| §7 regen schema | Task 6 Step 3 |
| §8 test | Task 1 Step 2, Task 2 Step 1, Task 3 Step 1, Task 5 Step 1 |
| §9 thứ tự deploy | Task 6 Step 7 |

**Điểm còn phải kiểm tại chỗ khi code (đã ghi ngay trong step, không phải placeholder):**
- Tên biến danh sách sản phẩm ở `OrderDetailPage.tsx` (Task 5 Step 6 có lệnh grep).
- Vị trí hằng `CANCEL_RELEASE_NOTE` (Task 3 Step 1 có lệnh grep).
- Script sinh type FE từ `schema.yaml` (Task 6 Step 3).
- Icon `Clock` sau khi gỡ entry modules (Task 4 Step 4).

---

## Task 7: Màn quét live nhận mã hoá đơn (phát sinh sau review Task 2)

> **Vì sao có task này.** Review Task 2 phát hiện màn quét kho gọi `GET /api/orders/lookup-by-shipping-code`
> ngay mỗi lần quét, và endpoint đó chỉ tra theo mã vận đơn. Sau Task 2 cron VẪN khớp
> được mã hoá đơn và tồn VẪN trừ đúng — nhưng ngay lúc quét kho nhận tiếng bíp lỗi và
> toast đỏ "Không tìm thấy đơn", không thấy tên đơn cũng không thấy dòng hàng. Theo đúng
> chữ của yêu cầu #3 ("màn quét kho giờ khớp theo cả mã hoá đơn") thì màn quét vẫn chưa
> khớp. Task này đóng nốt.

**Files:**
- Modify: `backend/apps/orders/transport/views/shipping_lookup_view.py`
- Modify: `frontend/src/features/inventory/scan/ScanDispatchDialog.tsx`
- Modify: `frontend/src/features/inventory/scan/scan-types.ts`
- Test: `backend/apps/orders/tests/` (file mới hoặc mở rộng test lookup hiện có)
- Test: `frontend/src/features/inventory/scan/__tests__/`

**Interfaces:**
- Consumes: `POST /api/inventory/orders/dispatch` từ Task 1 — `{order_ids, warehouse_id}` → `{confirmed, skipped}`.
- Produces: `_serialize` thêm trường `matched_by: "shipping" | "recall" | "invoice"`.

### Quyết định thiết kế đã chốt

| Quyết định | Lý do |
|---|---|
| Chỉ áp cho `mode=dispatch` | `mode=return`/`return_scan` nuôi đường phiếu hoàn, vốn ghi `scan.code` vào `ReturnEntry.shipping_code` — cùng lý do §5.3 |
| `invoice_qs` xếp SAU `ship_qs` và `recall_qs` | Giữ nguyên mọi thứ tự ưu tiên đang có; mã vận đơn vẫn thắng tuyệt đối, đúng như cron ở Task 2 |
| Backend trả `matched_by` tường minh | FE dùng nó để quyết định trừ tồn ở kho nào — suy luận ngầm từ việc so `shipping_code` là chỗ dễ sai im lặng nhất |
| Đơn khớp mã hoá đơn đi qua endpoint của Task 1 | Endpoint đó đã ghi kho vào đơn rồi mới ghi sổ, đúng luật "kho xuất trên đơn"; `dispatch-batch` chỉ nhận MỘT kho cho cả lô nên không diễn tả được |
| Đơn khớp mã hoá đơn mà thiếu kho xuất → báo lỗi từng dòng | Cùng luật fail-loud với cron; tuyệt đối không rơi về kho của màn quét |

### Bước

- [ ] **Step 1: Test backend thất bại**

Thêm test cho `lookup_by_shipping_code`:
1. `mode=dispatch`, mã trùng `invoice_code` của một đơn → 200, trả đúng đơn đó, `matched_by == "invoice"`, có `fulfillment_warehouse_id`.
2. Mã trùng `shipping_code` đơn A và `invoice_code` đơn B → trả **đơn A trước**, `matched_by == "shipping"`.
3. `mode=return` với mã hoá đơn → vẫn 404 `not_found` (không đổi hành vi đường hoàn).
4. Đơn khớp mã hoá đơn nhưng `fulfillment_warehouse` null → vẫn trả đơn, `fulfillment_warehouse_id` là `null` (FE tự chặn, backend không giấu đơn).

- [ ] **Step 2: Chạy để xác nhận ĐỎ**

- [ ] **Step 3: Sửa `shipping_lookup_view.py`**

Thêm `invoice_qs` (chỉ khi `mode` là dispatch), gộp sau `ship_qs` + `recall_qs`, dedup theo id.
`_serialize` nhận thêm tham số `matched_by` và trả nó ra. Cập nhật `_LookupOrderSerializer`
để schema không lệch. Giữ nguyên bình luận giải thích vì sao query tách rời thay vì OR-join —
lý do đó (Postgres không dùng được index khi OR-expand qua join, gây seq-scan trên hot path
quét kho) vẫn đúng và áp cho cả `invoice_qs`.

- [ ] **Step 4: Test FE thất bại + sửa `ScanDispatchDialog`**

`handleConfirm` chia `matched` thành hai nhóm theo `matched_by`:
- `shipping` / `recall` → `dispatchBatch` với kho của màn quét (nguyên hành vi cũ).
- `invoice` → gọi `POST /api/inventory/orders/dispatch` cho từng đơn với chính
  `fulfillment_warehouse_id` của đơn đó.

Đơn nhóm `invoice` mà `fulfillment_warehouse_id` null → không gửi đi, đánh dòng đó lỗi với
câu "Đơn thiếu kho xuất — bổ sung kho trên đơn rồi quét lại." (cùng chữ với cron).

Dòng khớp bằng mã hoá đơn phải hiện rõ trên UI là sẽ trừ ở kho CỦA ĐƠN, không phải kho đang
chọn ở đầu màn hình — nếu không người quét sẽ tưởng mọi dòng đều trừ cùng một kho.

- [ ] **Step 5: Chạy đủ backend + frontend, ruff, import-linter, tsc, lint:strict**

- [ ] **Step 6: Commit** (`feat(orders): match dispatch scan lookup by invoice code`)
