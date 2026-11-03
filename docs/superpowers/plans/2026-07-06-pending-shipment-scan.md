# Pending Shipment Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mã vận đơn quét không tìm thấy (chưa sync từ KiotViet) được ghi vào bảng chờ `PendingShipmentScan`; cron 60s tự khớp (xuất kho / tạo phiếu trả); danh sách chờ hiển thị ở trang Phiếu kho.

**Architecture:** Model + service + celery task mới trong app `inventory` (không đụng app `kiotviet`). Khi khớp, tái dùng nguyên đường nghiệp vụ hiện có: `OrderService.dispatch_orders_batch` (xuất) và `ReturnNoteService.create_return` (hoàn). FE sửa 2 dialog quét + thêm khối "Chờ khớp đơn" ở `StockDocumentsPage`.

**Tech Stack:** Django 6 + DRF + drf-spectacular, Celery beat (Redis), React 19 + TypeScript + React Query + shadcn/ui, vitest.

**Spec:** `docs/erp-inventory-pending-scan.md` (đọc trước khi làm task).

## Global Constraints

- **Branch: `feat/pending-shipment-scan`, worktree `C:\Users\MYPC\Desktop\workspace\desi-order\.claude\worktrees\feat-pending-shipment-scan`. Subagent PHẢI làm trên branch này trong worktree này, KHÔNG tự tạo branch/worktree mới, KHÔNG cd sang checkout chính.**
- Commit message tiếng Anh, ngắn, KHÔNG footer Co-Authored-By.
- BE test chạy qua docker (Git Bash, từ root worktree; xác minh network bằng `docker ps`):
  ```bash
  MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
    --env-file .env --env-file backend/.env \
    -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
    -e POSTGRES_DB=desi_pendingscan \
    -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
    -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
    sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
  ```
  Sau khi thêm migration mới phải chạy với `--create-db`. ⚠️ KHÔNG BAO GIỜ `cache.clear()` trong test (FLUSHDB đè Redis thật) — cleanup bằng `cache.delete(key)` từng key.
- FE: `cd frontend && npm run lint && npm run typecheck && npm run test:ci`. Baseline fail đã biết (KHÔNG phải regression): OrderFormDialog + 2 mobile-viewport + `modules.test.ts`; BE: return-rate + 18 fail baseline 2026-07-03 (users RBAC, report_trends flake, kiotviet cũ), flake `test_dispatch_stock_cycle::test_cancel_without_return_still_releases` khi chạy full-suite.
- UI copy tiếng Việt; components từ `components/ui` (CẤM react-select); GET mới dùng React Query; API module mới typed từ `src/api/schema.d.ts`.
- RBAC: mọi endpoint pending-scans dùng `require_section("shipments")` (cùng section với lookup + dispatch-batch — mọi người quét đều đã có).

---

## Sprint S1 — Backend

### Task 1: Model `PendingShipmentScan` + migration

**Files:**
- Modify: `backend/apps/inventory/models.py` (thêm cuối file)
- Create: `backend/apps/inventory/migrations/0017_pendingshipmentscan.py` (makemigrations tự sinh)
- Test: `backend/apps/inventory/tests/test_pending_scan_model.py`

**Interfaces:**
- Produces: model `PendingShipmentScan`, choices `PendingScanKind` (`dispatch`/`return_`→value `"return"`), `PendingScanStatus` (`pending`/`matched`/`failed`), db_table `pending_shipment_scans`, partial unique `uniq_pending_scan_code_kind` trên `(code, kind)` WHERE `status='pending'`.

- [ ] **Step 1: Viết test fail**

```python
# backend/apps/inventory/tests/test_pending_scan_model.py
import pytest
from django.db import IntegrityError, transaction

from apps.inventory.models import PendingScanKind, PendingScanStatus, PendingShipmentScan
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


@pytest.fixture
def warehouse():
    return Warehouse.objects.create(name="Kho test", code="KT01")


def test_create_pending_scan_defaults(warehouse):
    scan = PendingShipmentScan.objects.create(
        code="SPX123", kind=PendingScanKind.DISPATCH, warehouse=warehouse
    )
    assert scan.status == PendingScanStatus.PENDING
    assert scan.attempts == 0
    assert scan.error == ""


def test_duplicate_pending_same_code_kind_rejected(warehouse):
    PendingShipmentScan.objects.create(
        code="SPX123", kind=PendingScanKind.DISPATCH, warehouse=warehouse
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        PendingShipmentScan.objects.create(
            code="SPX123", kind=PendingScanKind.DISPATCH, warehouse=warehouse
        )


def test_same_code_different_kind_allowed(warehouse):
    PendingShipmentScan.objects.create(
        code="SPX123", kind=PendingScanKind.DISPATCH, warehouse=warehouse
    )
    PendingShipmentScan.objects.create(
        code="SPX123", kind=PendingScanKind.RETURN, warehouse=warehouse
    )
    assert PendingShipmentScan.objects.count() == 2


def test_same_code_allowed_after_matched(warehouse):
    PendingShipmentScan.objects.create(
        code="SPX123", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
    )
    PendingShipmentScan.objects.create(
        code="SPX123", kind=PendingScanKind.DISPATCH, warehouse=warehouse
    )
    assert PendingShipmentScan.objects.count() == 2
```

Lưu ý: kiểm tra field bắt buộc của `Warehouse` trước khi viết fixture (`backend/apps/warehouses/models.py`) — nếu không có field `code` thì bỏ.

- [ ] **Step 2: Chạy test xác nhận fail** — `pytest apps/inventory/tests/test_pending_scan_model.py -q --create-db` (qua docker, lệnh ở Global Constraints). Expected: FAIL `ImportError: cannot import name 'PendingShipmentScan'`.

- [ ] **Step 3: Thêm model** vào cuối `backend/apps/inventory/models.py` (theo style ReturnNote sẵn có trong file — đã có sẵn `from django.db.models import Q`, `settings`; kiểm tra imports đầu file):

```python
class PendingScanKind(models.TextChoices):
    DISPATCH = "dispatch", "Xuất giao"
    RETURN = "return", "Nhập hoàn"


class PendingScanStatus(models.TextChoices):
    PENDING = "pending", "Chờ khớp"
    MATCHED = "matched", "Đã khớp"
    FAILED = "failed", "Lỗi"


class PendingShipmentScan(models.Model):
    """Mã vận đơn đã quét nhưng chưa có đơn trong hệ thống (chờ cron KiotViet sync).

    Cron ``match_pending_shipment_scans`` quét bảng này mỗi 60s: có đơn →
    tự hoàn tất xuất kho / tạo phiếu trả nháp như thao tác tay lúc quét.
    """

    code = models.CharField(max_length=64)
    kind = models.CharField(max_length=10, choices=PendingScanKind.choices)
    warehouse = models.ForeignKey(
        "warehouses.Warehouse", on_delete=models.PROTECT, related_name="pending_scans"
    )
    # Chỉ dùng cho kind=return; trống = mặc định delivery_returned khi tạo phiếu.
    return_type = models.CharField(
        max_length=20, choices=ReturnType.choices, blank=True, default=""
    )
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    status = models.CharField(
        max_length=10, choices=PendingScanStatus.choices, default=PendingScanStatus.PENDING
    )
    matched_order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="pending_scans",
    )
    # Mã phiếu trả tạo ra khi khớp (kind=return); rỗng với kind=dispatch.
    result_ref = models.CharField(max_length=32, blank=True, default="")
    error = models.TextField(blank=True, default="")
    attempts = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pending_shipment_scans"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="pendingscan_status_created"),
            models.Index(fields=["code"], name="pendingscan_code_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["code", "kind"],
                condition=Q(status="pending"),
                name="uniq_pending_scan_code_kind",
            ),
        ]

    def __str__(self):
        return f"{self.code} ({self.kind}/{self.status})"
```

- [ ] **Step 4: Sinh migration** — qua docker: `sh -c 'python manage.py makemigrations inventory'`. File sinh ra phải là `0017_*.py` (inventory hiện dừng ở 0016). KHÔNG sửa app `orders`.

- [ ] **Step 5: Chạy test xác nhận pass** — cùng lệnh Step 2. Expected: 4 passed.

- [ ] **Step 6: Commit** — `git add backend/apps/inventory/models.py backend/apps/inventory/migrations/0017_*.py backend/apps/inventory/tests/test_pending_scan_model.py && git commit -m "feat: add PendingShipmentScan model"`

---

### Task 2: Service — `create_batch`

**Files:**
- Create: `backend/apps/inventory/application/services/pending_scan_service.py`
- Test: `backend/apps/inventory/tests/test_pending_scan_service_create.py`

**Interfaces:**
- Consumes: model Task 1.
- Produces: `PendingScanService.create_batch(*, kind: str, warehouse_id: int, codes: list[str], return_type: str = "", user_id: int | None = None) -> list[dict]` — mỗi phần tử `{"code": str, "ok": bool, "reason"?: "duplicate"|"empty"}`.

- [ ] **Step 1: Viết test fail**

```python
# backend/apps/inventory/tests/test_pending_scan_service_create.py
import pytest

from apps.inventory.application.services.pending_scan_service import PendingScanService
from apps.inventory.models import PendingScanKind, PendingShipmentScan
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


@pytest.fixture
def warehouse():
    return Warehouse.objects.create(name="Kho test", code="KT01")


def test_create_batch_creates_pending_rows(warehouse):
    results = PendingScanService.create_batch(
        kind=PendingScanKind.DISPATCH, warehouse_id=warehouse.id,
        codes=["SPX1", "  SPX2  "],
    )
    assert all(r["ok"] for r in results)
    codes = set(PendingShipmentScan.objects.values_list("code", flat=True))
    assert codes == {"SPX1", "SPX2"}  # code được strip


def test_create_batch_duplicate_reports_per_code(warehouse):
    PendingScanService.create_batch(
        kind=PendingScanKind.DISPATCH, warehouse_id=warehouse.id, codes=["SPX1"]
    )
    results = PendingScanService.create_batch(
        kind=PendingScanKind.DISPATCH, warehouse_id=warehouse.id, codes=["SPX1", "SPX3"]
    )
    by_code = {r["code"]: r for r in results}
    assert by_code["SPX1"] == {"code": "SPX1", "ok": False, "reason": "duplicate"}
    assert by_code["SPX3"]["ok"] is True


def test_create_batch_return_kind_keeps_return_type(warehouse):
    PendingScanService.create_batch(
        kind=PendingScanKind.RETURN, warehouse_id=warehouse.id,
        codes=["SPX9"], return_type="delivery_returned",
    )
    scan = PendingShipmentScan.objects.get(code="SPX9")
    assert scan.return_type == "delivery_returned"


def test_create_batch_dispatch_kind_ignores_return_type(warehouse):
    PendingScanService.create_batch(
        kind=PendingScanKind.DISPATCH, warehouse_id=warehouse.id,
        codes=["SPX8"], return_type="defect",
    )
    assert PendingShipmentScan.objects.get(code="SPX8").return_type == ""
```

- [ ] **Step 2: Chạy test xác nhận fail** — Expected: FAIL `ModuleNotFoundError`.

- [ ] **Step 3: Viết service**

```python
# backend/apps/inventory/application/services/pending_scan_service.py
"""Bảng chờ mã vận đơn quét chưa khớp đơn + cron tự khớp.

Xem spec: docs/erp-inventory-pending-scan.md.
"""
from __future__ import annotations

import logging

from django.db import IntegrityError, transaction

from apps.inventory.models import PendingScanKind, PendingShipmentScan

logger = logging.getLogger(__name__)


class PendingScanService:
    @staticmethod
    def create_batch(
        *,
        kind: str,
        warehouse_id: int,
        codes: list[str],
        return_type: str = "",
        user_id: int | None = None,
    ) -> list[dict]:
        """Ghi từng mã vào bảng chờ; mã trùng (đang pending cùng kind) không chặn cả lô."""
        results: list[dict] = []
        for raw in codes:
            code = (raw or "").strip()
            if not code:
                results.append({"code": raw, "ok": False, "reason": "empty"})
                continue
            try:
                with transaction.atomic():
                    PendingShipmentScan.objects.create(
                        code=code,
                        kind=kind,
                        warehouse_id=warehouse_id,
                        return_type=return_type if kind == PendingScanKind.RETURN else "",
                        scanned_by_id=user_id,
                    )
                results.append({"code": code, "ok": True})
            except IntegrityError:
                results.append({"code": code, "ok": False, "reason": "duplicate"})
        return results
```

- [ ] **Step 4: Chạy test xác nhận pass** — Expected: 4 passed.

- [ ] **Step 5: Commit** — `git commit -m "feat: pending scan create_batch service"` (add 2 file mới).

---

### Task 3: Service — `match_pending_scans` (lõi cron)

**Files:**
- Modify: `backend/apps/inventory/application/services/pending_scan_service.py`
- Test: `backend/apps/inventory/tests/test_pending_scan_service_match.py`

**Interfaces:**
- Consumes: `OrderService.dispatch_orders_batch(*, order_ids, warehouse_id, actor) -> list[dict]` (`apps.orders.services`, kết quả `{"order_id", "ok", "warnings"?, "stock_warnings"?, "error"?}`); `ReturnNoteService.create_return(*, return_type, warehouse_id, lines, source_order_id, shipping_code, customer_name, customer_phone, user_id) -> ReturnNote` (`apps.inventory.application.services.return_note_service`).
- Produces: `PendingScanService.match_pending_scans(limit: int = 200) -> dict` — summary `{"processed", "matched", "failed", "unmatched"}`.

**Hành vi (bám spec Q2/Q6/Q7):**
- Đơn tra theo `Order.objects.filter(shipping_code=code, deleted_at__isnull=True)`, nhiều đơn → lấy `-id` mới nhất, ghi chú vào `error`.
- `dispatch`: gọi `dispatch_orders_batch` với `actor=scan.scanned_by` — ok → `matched` (warnings như `already_dispatched` lưu vào `error` dạng ghi chú, vẫn matched vì idempotent không trừ đôi); ok=False → `failed` + error.
- `return`: merge items theo product (như `ReturnNoteService.update_draft` làm khi khớp tay), tạo phiếu nháp `return_type = scan.return_type or "delivery_returned"`; `ValueError` (vd trùng shipping_code phiếu khác) → `failed`.
- Chưa có đơn → tăng `attempts`, giữ `pending`.
- Mỗi bản ghi 1 transaction riêng + `select_for_update(skip_locked=True)` re-check status (an toàn khi 2 run chồng dù đã có Redis lock ở task).

- [ ] **Step 1: Viết test fail** — dựng dữ liệu thật (Order + OrderItem + Product + Warehouse). Đọc `backend/apps/orders/tests/test_dispatch_stock.py` để lấy đúng cách dựng Order/OrderItem/Product tối thiểu của repo (fixture, field bắt buộc) rồi viết các test sau (điều chỉnh khởi tạo theo mẫu đó, giữ nguyên assert):

```python
# backend/apps/inventory/tests/test_pending_scan_service_match.py
# (imports + fixtures dựng theo mẫu test_dispatch_stock.py)
import pytest

from apps.inventory.application.services.pending_scan_service import PendingScanService
from apps.inventory.models import (
    MovementType,
    PendingScanKind,
    PendingScanStatus,
    PendingShipmentScan,
    ReturnNote,
    StockMovement,
)

pytestmark = pytest.mark.django_db


def test_no_order_yet_increments_attempts(warehouse):
    scan = PendingShipmentScan.objects.create(
        code="NOPE", kind=PendingScanKind.DISPATCH, warehouse=warehouse
    )
    summary = PendingScanService.match_pending_scans()
    scan.refresh_from_db()
    assert summary["unmatched"] == 1
    assert scan.status == PendingScanStatus.PENDING
    assert scan.attempts == 1
    assert scan.last_attempt_at is not None


def test_dispatch_scan_matches_and_issues_stock(order_awaiting_shipment, warehouse, staff_user):
    # order_awaiting_shipment: Order status AWAITING_SHIPMENT(11), shipping_code="SPX1",
    # có 1 OrderItem (product, qty=2) và tồn kho đủ ở `warehouse`.
    scan = PendingShipmentScan.objects.create(
        code="SPX1", kind=PendingScanKind.DISPATCH, warehouse=warehouse, scanned_by=staff_user
    )
    summary = PendingScanService.match_pending_scans()
    scan.refresh_from_db()
    assert summary["matched"] == 1
    assert scan.status == PendingScanStatus.MATCHED
    assert scan.matched_order_id == order_awaiting_shipment.id
    assert StockMovement.objects.filter(
        reference_type="order", reference_id=order_awaiting_shipment.id,
        movement_type=MovementType.SALE,
    ).exists()


def test_return_scan_creates_draft_note_with_lines(order_awaiting_shipment, warehouse, staff_user):
    scan = PendingShipmentScan.objects.create(
        code="SPX1", kind=PendingScanKind.RETURN, warehouse=warehouse,
        return_type="delivery_returned", scanned_by=staff_user,
    )
    PendingScanService.match_pending_scans()
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED
    note = ReturnNote.objects.get(code=scan.result_ref)
    assert note.status == "draft"
    assert note.source_order_id == order_awaiting_shipment.id
    assert note.shipping_code == "SPX1"
    assert note.lines.count() >= 1


def test_multiple_orders_picks_newest_and_notes(two_orders_same_code, warehouse, staff_user):
    # two_orders_same_code: 2 Order cùng shipping_code="SPX2", đơn tạo sau có id lớn hơn.
    newest = max(two_orders_same_code, key=lambda o: o.id)
    scan = PendingShipmentScan.objects.create(
        code="SPX2", kind=PendingScanKind.DISPATCH, warehouse=warehouse, scanned_by=staff_user
    )
    PendingScanService.match_pending_scans()
    scan.refresh_from_db()
    assert scan.matched_order_id == newest.id
    assert "Nhiều đơn" in scan.error


def test_business_error_marks_failed(order_awaiting_shipment, warehouse, staff_user, monkeypatch):
    scan = PendingShipmentScan.objects.create(
        code="SPX1", kind=PendingScanKind.DISPATCH, warehouse=warehouse, scanned_by=staff_user
    )
    from apps.orders.services import OrderService

    monkeypatch.setattr(
        OrderService, "dispatch_orders_batch",
        staticmethod(lambda **kw: [{"order_id": kw["order_ids"][0], "ok": False, "error": "boom"}]),
    )
    summary = PendingScanService.match_pending_scans()
    scan.refresh_from_db()
    assert summary["failed"] == 1
    assert scan.status == PendingScanStatus.FAILED
    assert "boom" in scan.error


def test_matched_and_failed_not_reprocessed(warehouse):
    PendingShipmentScan.objects.create(
        code="A", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
    )
    PendingShipmentScan.objects.create(
        code="B", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.FAILED,
    )
    summary = PendingScanService.match_pending_scans()
    assert summary["processed"] == 0
```

- [ ] **Step 2: Chạy test xác nhận fail** — Expected: FAIL `AttributeError: ... has no attribute 'match_pending_scans'`.

- [ ] **Step 3: Bổ sung service** (thêm vào `pending_scan_service.py`):

```python
    @staticmethod
    def match_pending_scans(limit: int = 200) -> dict:
        """Chạy bởi cron 60s: khớp các mã pending với Order đã sync về."""
        ids = list(
            PendingShipmentScan.objects.filter(status=PendingScanStatus.PENDING)
            .order_by("created_at")
            .values_list("id", flat=True)[:limit]
        )
        summary = {"processed": 0, "matched": 0, "failed": 0, "unmatched": 0}
        for scan_id in ids:
            outcome = PendingScanService._process_one(scan_id)
            if outcome is not None:
                summary["processed"] += 1
                summary[outcome] += 1
        # unmatched không tính processed — đơn chưa về là trạng thái chờ bình thường.
        summary["processed"] -= summary["unmatched"]
        return summary

    @staticmethod
    def _process_one(scan_id: int) -> str | None:
        from django.utils import timezone

        from apps.orders.models import Order

        with transaction.atomic():
            scan = (
                PendingShipmentScan.objects.select_for_update(skip_locked=True)
                .filter(id=scan_id, status=PendingScanStatus.PENDING)
                .first()
            )
            if scan is None:
                return None
            orders = list(
                Order.objects.filter(shipping_code=scan.code, deleted_at__isnull=True)
                .prefetch_related("items")
                .order_by("-id")[:2]
            )
            scan.attempts += 1
            scan.last_attempt_at = timezone.now()
            if not orders:
                scan.save(update_fields=["attempts", "last_attempt_at", "updated_at"])
                return "unmatched"
            order = orders[0]
            multi_note = "Nhiều đơn cùng mã — lấy đơn mới nhất. " if len(orders) > 1 else ""
            try:
                if scan.kind == PendingScanKind.DISPATCH:
                    result_ref, note = PendingScanService._complete_dispatch(scan, order)
                else:
                    result_ref, note = PendingScanService._complete_return(scan, order)
            except Exception as exc:  # noqa: BLE001 — lỗi nghiệp vụ → failed, chờ xử lý tay
                logger.warning("pending scan %s failed matching order %s: %s", scan.code, order.id, exc)
                scan.status = PendingScanStatus.FAILED
                scan.matched_order = order
                scan.error = (multi_note + str(exc)).strip()
                scan.save()
                return "failed"
            scan.status = PendingScanStatus.MATCHED
            scan.matched_order = order
            scan.result_ref = result_ref
            scan.error = (multi_note + note).strip()
            scan.save()
            return "matched"

    @staticmethod
    def _complete_dispatch(scan: PendingShipmentScan, order) -> tuple[str, str]:
        # Đúng đường xuất kho tay: set dispatched + issue_for_order (idempotent).
        from apps.orders.services import OrderService

        results = OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=scan.warehouse_id, actor=scan.scanned_by
        )
        result = results[0]
        if not result.get("ok"):
            raise ValueError(str(result.get("error") or "Xuất kho thất bại."))
        return "", ", ".join(result.get("warnings") or [])

    @staticmethod
    def _complete_return(scan: PendingShipmentScan, order) -> tuple[str, str]:
        from collections import defaultdict

        from apps.inventory.application.services.return_note_service import ReturnNoteService

        merged: dict[int, int] = defaultdict(int)
        for item in order.items.all():
            if item.product_id:
                merged[item.product_id] += item.quantity
        if not merged:
            raise ValueError("Đơn không có dòng sản phẩm để tạo phiếu trả.")
        rn = ReturnNoteService.create_return(
            return_type=scan.return_type or "delivery_returned",
            warehouse_id=scan.warehouse_id,
            lines=[{"product_id": pid, "quantity": qty} for pid, qty in merged.items()],
            source_order_id=order.id,
            shipping_code=scan.code,
            customer_name=order.name or "",
            customer_phone=order.phone or "",
            user_id=scan.scanned_by_id,
        )
        return rn.code, ""
```

Import `PendingScanStatus` bổ sung ở đầu file. Lưu ý `summary["processed"] -= summary["unmatched"]` làm test `test_no_order_yet_increments_attempts` expect `unmatched==1`, processed=0 — giữ nhất quán với test cuối (`processed == 0` khi không có gì pending). Nếu chọn semantics khác thì sửa CẢ test lẫn code cho khớp, miễn summary có đủ 4 khóa.

- [ ] **Step 4: Chạy test xác nhận pass** — cả file test Task 2 + 3. Expected: pass hết.

- [ ] **Step 5: Commit** — `git commit -m "feat: pending scan matching service"`

---

### Task 4: Celery task + beat 60s

**Files:**
- Modify: `backend/apps/inventory/tasks.py`
- Modify: `backend/config/settings_parts/celery.py` (thêm 1 entry `CELERY_BEAT_SCHEDULE`)
- Test: `backend/apps/inventory/tests/test_pending_scan_task.py`

**Interfaces:**
- Consumes: `PendingScanService.match_pending_scans()` (Task 3).
- Produces: task `apps.inventory.tasks.match_pending_shipment_scans`, beat key `inventory-match-pending-scans`.

- [ ] **Step 1: Viết test fail**

```python
# backend/apps/inventory/tests/test_pending_scan_task.py
import pytest
from django.core.cache import cache

from apps.inventory import tasks

pytestmark = pytest.mark.django_db

LOCK_KEY = "inventory:match_pending_scans:lock"


def test_task_runs_service_and_returns_summary(monkeypatch):
    called = {}

    def fake_match(limit=200):
        called["yes"] = True
        return {"processed": 0, "matched": 0, "failed": 0, "unmatched": 0}

    monkeypatch.setattr(
        "apps.inventory.application.services.pending_scan_service.PendingScanService.match_pending_scans",
        staticmethod(fake_match),
    )
    cache.delete(LOCK_KEY)
    result = tasks.match_pending_shipment_scans.apply().result
    assert called.get("yes")
    assert result["matched"] == 0
    assert cache.get(LOCK_KEY) is None  # lock được nhả


def test_task_skips_when_lock_held():
    cache.add(LOCK_KEY, 1, 30)
    try:
        result = tasks.match_pending_shipment_scans.apply().result
        assert result == {"skipped": "lock_held"}
    finally:
        cache.delete(LOCK_KEY)
```

- [ ] **Step 2: Chạy test xác nhận fail** — Expected: FAIL `AttributeError: module ... has no attribute 'match_pending_shipment_scans'`.

- [ ] **Step 3: Viết task** (thêm vào `backend/apps/inventory/tasks.py`; pattern lock giống `apps/kiotviet/tasks.py::import_create_only_one_channel`):

```python
from django.core.cache import cache

from apps.inventory.application.services.pending_scan_service import PendingScanService


@shared_task(
    bind=True,
    name="apps.inventory.tasks.match_pending_shipment_scans",
    soft_time_limit=100,
    time_limit=120,
)
def match_pending_shipment_scans(self) -> dict:
    """Cron 60s: khớp mã vận đơn trong bảng chờ với Order đã sync từ KiotViet.

    Single-flight lock (cache.add) chống tick chồng khi lần chạy trước còn dang dở
    — hai run cùng xử lý một scan có thể tạo phiếu trả TRÙNG. Lock tự hết hạn
    nếu worker chết giữa chừng."""
    lock_key = "inventory:match_pending_scans:lock"
    if not cache.add(lock_key, 1, 130):  # ttl > time_limit
        logger.info("match_pending_shipment_scans skip: lock held")
        return {"skipped": "lock_held"}
    try:
        summary = PendingScanService.match_pending_scans()
    finally:
        cache.delete(lock_key)
    logger.info("match_pending_shipment_scans done: %s", summary)
    return summary
```

Và entry beat trong `backend/config/settings_parts/celery.py` (thêm vào `CELERY_BEAT_SCHEDULE`, cạnh `inventory-ensure-stock-partition`):

```python
    # Khớp mã vận đơn quét chưa có đơn (bảng pending_shipment_scans) với Order
    # đã được cron KiotViet 5' kéo về. Xem docs/erp-inventory-pending-scan.md.
    "inventory-match-pending-scans": {
        "task": "apps.inventory.tasks.match_pending_shipment_scans",
        "schedule": 60.0,
    },
```

- [ ] **Step 4: Chạy test xác nhận pass.**

- [ ] **Step 5: Commit** — `git commit -m "feat: cron task matching pending shipment scans"`

---

### Task 5: API pending-scans (list / batch / retry / delete)

**Files:**
- Create: `backend/apps/inventory/transport/serializers/pending_scan_serializers.py`
- Create: `backend/apps/inventory/transport/views/pending_scan_views.py`
- Modify: `backend/apps/inventory/urls.py`
- Test: `backend/apps/inventory/tests/test_pending_scan_api.py`

**Interfaces:**
- Produces:
  - `GET /api/inventory/pending-scans?status=&kind=&limit=&offset=` → `{items,total,limit,offset}`; **không truyền `status` → chỉ trả `pending` + `failed`** (mặc định cho UI kho); `status=matched` xem lịch sử.
  - `POST /api/inventory/pending-scans/batch` body `{"kind","warehouse_id","return_type"?,"codes":[...]}` → `{results:[{code,ok,reason?}]}` (201).
  - `POST /api/inventory/pending-scans/<id>/retry` — chỉ dòng `failed` → reset `pending`, xoá error/matched_order/result_ref (200, trả item).
  - `DELETE /api/inventory/pending-scans/<id>` — chỉ `pending`/`failed` (204).
- Item serializer fields: `id, code, kind, warehouse, warehouse_name, return_type, status, matched_order, matched_order_code, result_ref, error, attempts, last_attempt_at, scanned_by_name, created_at`.

- [ ] **Step 1: Viết test fail** — dùng APIClient pattern của repo (đọc `backend/apps/inventory/tests/test_return_note_shipping_code_unique.py` để lấy cách tạo user + gán section RBAC "shipments" rồi force_authenticate). Test:

```python
# backend/apps/inventory/tests/test_pending_scan_api.py
# fixtures: api_client đã auth user có section "shipments"; warehouse
import pytest

from apps.inventory.models import PendingScanKind, PendingScanStatus, PendingShipmentScan

pytestmark = pytest.mark.django_db


def test_batch_create_and_duplicate(api_client, warehouse):
    resp = api_client.post(
        "/api/inventory/pending-scans/batch",
        {"kind": "dispatch", "warehouse_id": warehouse.id, "codes": ["A1", "A2"]},
        format="json",
    )
    assert resp.status_code == 201
    assert all(r["ok"] for r in resp.data["results"])

    resp2 = api_client.post(
        "/api/inventory/pending-scans/batch",
        {"kind": "dispatch", "warehouse_id": warehouse.id, "codes": ["A1"]},
        format="json",
    )
    assert resp2.data["results"][0]["reason"] == "duplicate"


def test_list_default_excludes_matched(api_client, warehouse):
    PendingShipmentScan.objects.create(code="P", kind="dispatch", warehouse=warehouse)
    PendingShipmentScan.objects.create(
        code="F", kind="dispatch", warehouse=warehouse, status=PendingScanStatus.FAILED
    )
    PendingShipmentScan.objects.create(
        code="M", kind="dispatch", warehouse=warehouse, status=PendingScanStatus.MATCHED
    )
    resp = api_client.get("/api/inventory/pending-scans?limit=50&offset=0")
    assert resp.status_code == 200
    codes = {i["code"] for i in resp.data["items"]}
    assert codes == {"P", "F"}
    assert resp.data["total"] == 2


def test_retry_only_failed(api_client, warehouse):
    failed = PendingShipmentScan.objects.create(
        code="F", kind="dispatch", warehouse=warehouse,
        status=PendingScanStatus.FAILED, error="boom",
    )
    pending = PendingShipmentScan.objects.create(code="P", kind="dispatch", warehouse=warehouse)
    ok = api_client.post(f"/api/inventory/pending-scans/{failed.id}/retry")
    assert ok.status_code == 200
    failed.refresh_from_db()
    assert failed.status == PendingScanStatus.PENDING
    assert failed.error == ""
    bad = api_client.post(f"/api/inventory/pending-scans/{pending.id}/retry")
    assert bad.status_code == 400


def test_delete_blocks_matched(api_client, warehouse):
    matched = PendingShipmentScan.objects.create(
        code="M", kind="dispatch", warehouse=warehouse, status=PendingScanStatus.MATCHED
    )
    pending = PendingShipmentScan.objects.create(code="P", kind="dispatch", warehouse=warehouse)
    assert api_client.delete(f"/api/inventory/pending-scans/{pending.id}").status_code == 204
    assert api_client.delete(f"/api/inventory/pending-scans/{matched.id}").status_code == 400


def test_requires_shipments_section(api_client_no_section):
    resp = api_client_no_section.get("/api/inventory/pending-scans?limit=50&offset=0")
    assert resp.status_code == 403
```

- [ ] **Step 2: Chạy test xác nhận fail** — Expected: 404 (route chưa có).

- [ ] **Step 3: Viết serializer + view + url.** View theo đúng pattern `return_views.py` (`@api_view` + `@permission_classes([IsAuthenticated, require_section("shipments")])` + `@never_cache`, pagination qua `parse_required_pagination` — import cùng chỗ `return_views.py` đang import). Batch view: validate serializer, check `Warehouse.objects.filter(id=...).exists()` → 400 nếu không; gọi `PendingScanService.create_batch(..., user_id=request.user.id)` → 201. Retry: `status != "failed"` → 400 `{"detail": "Chỉ dòng lỗi mới thử lại được."}`; reset `status="pending"`, `error=""`, `matched_order=None`, `result_ref=""`, `attempts=0`. Delete: `status == "matched"` → 400. List: filter `status` param (whitelist 3 giá trị, sai → 400), `kind` param tương tự; mặc định `.filter(status__in=["pending", "failed"])`; `select_related("warehouse", "scanned_by", "matched_order")`; annotate schema bằng `@extend_schema` như `shipping_lookup_view.py`.

URLs thêm vào `backend/apps/inventory/urls.py`:

```python
from apps.inventory.transport.views.pending_scan_views import (
    delete_pending_scan,
    list_pending_scans,
    create_pending_scans_batch,
    retry_pending_scan,
)
# ... trong urlpatterns:
    path("pending-scans", list_pending_scans, name="inventory-pending-scans"),
    path("pending-scans/batch", create_pending_scans_batch, name="inventory-pending-scans-batch"),
    path("pending-scans/<int:scan_id>", delete_pending_scan, name="inventory-pending-scan-delete"),
    path("pending-scans/<int:scan_id>/retry", retry_pending_scan, name="inventory-pending-scan-retry"),
```

- [ ] **Step 4: Chạy test xác nhận pass.**

- [ ] **Step 5: Commit** — `git commit -m "feat: pending scans API"`

---

### Task 6: Lookup trả thông tin mã đang chờ (check trùng qua bảng tạm)

**Files:**
- Modify: `backend/apps/orders/transport/views/shipping_lookup_view.py` (nhánh not-found, ~dòng 92-93)
- Test: `backend/apps/orders/tests/test_shipping_lookup_pending.py`

**Interfaces:**
- Produces: 404 body `{"detail": "not_found", "pending_scan": {"kind": "dispatch"|"return", "created_at": "<iso>"}}` khi mã đang có bản ghi `pending`; giữ nguyên `{"detail": "not_found"}` khi không.

- [ ] **Step 1: Viết test fail**

```python
# backend/apps/orders/tests/test_shipping_lookup_pending.py
# fixtures: api_client user có section "shipments"; warehouse
import pytest

from apps.inventory.models import PendingScanKind, PendingShipmentScan

pytestmark = pytest.mark.django_db


def test_lookup_not_found_plain(api_client):
    resp = api_client.get("/api/orders/lookup-by-shipping-code?code=NOPE")
    assert resp.status_code == 404
    assert "pending_scan" not in resp.data


def test_lookup_not_found_with_pending_scan(api_client, warehouse):
    PendingShipmentScan.objects.create(
        code="WAIT1", kind=PendingScanKind.DISPATCH, warehouse=warehouse
    )
    resp = api_client.get("/api/orders/lookup-by-shipping-code?code=WAIT1")
    assert resp.status_code == 404
    assert resp.data["pending_scan"]["kind"] == "dispatch"
```

- [ ] **Step 2: Chạy test xác nhận fail.**

- [ ] **Step 3: Sửa view** — thay `return Response({"detail": "not_found"}, ...)` bằng:

```python
    if not orders:
        body: dict = {"detail": "not_found"}
        # Mã đã nằm bảng chờ → FE báo "đã ghi nhận, đang chờ khớp" thay vì thêm dòng mới.
        from apps.inventory.models import PendingScanStatus, PendingShipmentScan

        pending = (
            PendingShipmentScan.objects.filter(code=code, status=PendingScanStatus.PENDING)
            .order_by("-created_at")
            .first()
        )
        if pending is not None:
            body["pending_scan"] = {
                "kind": pending.kind,
                "created_at": pending.created_at.isoformat(),
            }
        return Response(body, status=status.HTTP_404_NOT_FOUND)
```

(import cục bộ trong hàm — giữ ranh giới module như `_dispatched_order_ids` đang làm). Cập nhật `@extend_schema` responses 404 mô tả body mới.

- [ ] **Step 4: Chạy test xác nhận pass.** Chạy thêm toàn bộ `apps/orders/tests/ apps/inventory/tests/` đảm bảo không vỡ gì.

- [ ] **Step 5: Regen OpenAPI schema** — qua docker: `sh -c 'python manage.py spectacular --file schema.yaml'` (file `backend/schema.yaml` commit vào repo).

- [ ] **Step 6: Commit** — `git commit -m "feat: shipping lookup reports pending scan"` (kèm schema.yaml).

---

## Sprint S2 — Frontend

### Task 7: FE plumbing — `error.body`, API module + React Query hooks

**Files:**
- Modify: `frontend/src/services/http.ts` (fetchJson gắn `body` vào Error)
- Modify: `frontend/src/api/schema.d.ts` (regen — KHÔNG sửa tay)
- Create: `frontend/src/lib/api/pending-scans.ts`
- Create: `frontend/src/features/inventory/pending-scans-queries.ts`
- Test: `frontend/src/features/inventory/scan/__tests__/pending-scans-api.test.ts` (nếu logic thuần đủ để test; hooks test qua dialog tests Task 8-10)

**Interfaces:**
- Consumes: endpoints Task 5/6, `backend/schema.yaml` đã regen.
- Produces:
  - `fetchJson` throw `Error & { status?: number; body?: unknown }`.
  - `lib/api/pending-scans.ts`: types `PendingScanRow`, `PendingScanListResponse`, `CreatePendingScansPayload`, `PendingScanBatchResult` typed từ `schema.d.ts` (theo mẫu `src/lib/api/orders.ts`) + hàm `listPendingScans(params)`, `createPendingScans(payload)`, `retryPendingScan(id)`, `deletePendingScan(id)`.
  - `pending-scans-queries.ts`: `pendingScanKeys.list(filters)` = `["inventory","pending-scans",filters]`; hooks `usePendingScans(filters)`, `useCreatePendingScans()`, `useRetryPendingScan()`, `useDeletePendingScan()` — mutation onSuccess invalidate `["inventory","pending-scans"]`.

- [ ] **Step 1: Regen schema types** — `cd frontend && npm run gen:api` (đọc `backend/schema.yaml` từ Task 6). Kiểm tra `git diff src/api/schema.d.ts` có paths `/api/inventory/pending-scans*`.

- [ ] **Step 2: Sửa `fetchJson`** — trong nhánh `if (!response.ok)` (http.ts:215-220), parse body JSON qua `response.clone()` TRƯỚC khi `buildHttpErrorMessage` tiêu thụ response:

```typescript
  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.clone().json();
    } catch {
      // body không phải JSON — bỏ qua
    }
    const message = await buildHttpErrorMessage(response);
    const error = new Error(message || `HTTP ${response.status}`) as Error & {
      status?: number;
      body?: unknown;
    };
    error.status = response.status;
    error.body = body;
    throw error;
  }
```

- [ ] **Step 3: Viết `lib/api/pending-scans.ts` + `pending-scans-queries.ts`** theo Interfaces trên (mẫu type-from-schema: mở `src/lib/api/orders.ts`; mẫu hooks: `returns-queries.ts`).

- [ ] **Step 4: Verify** — `npm run typecheck && npm run lint` sạch (không warning mới).

- [ ] **Step 5: Commit** — `git commit -m "feat: pending scans FE api + queries, fetchJson error body"`

---

### Task 8: ScanDispatchDialog — ghi nhận mã chưa khớp

**Files:**
- Modify: `frontend/src/features/inventory/scan/scan-dispatch-rows.ts`
- Modify: `frontend/src/features/inventory/scan/ScanDispatchDialog.tsx`
- Test: `frontend/src/features/inventory/scan/__tests__/scan-dispatch-rows.test.ts` (mở rộng file test hiện có nếu đã tồn tại, không thì tạo)

**Interfaces:**
- Consumes: `useCreatePendingScans` (Task 7), `lookupByShippingCode` (hiện có).
- Produces (trong `scan-dispatch-rows.ts`):

```typescript
export type ScanDispatchRow =
  | { kind: "matched"; code: string; order: LookupOrder }
  | { kind: "unmatched"; code: string };

export function addDispatchScanRow(
  rows: ScanDispatchRow[],
  code: string,
  order: LookupOrder | null,
): { rows: ScanDispatchRow[]; result: "added" | "duplicate" | "already-dispatched" };
```

**Hành vi:**
- `addDispatchScanRow`: trùng `code` hoặc trùng `order_id` (với matched) → `"duplicate"`; `order?.already_dispatched` → `"already-dispatched"` (không thêm); còn lại prepend row. Giữ `checkDispatchScan`/`dispatchResultBadge` hiện có cho phần matched.
- `handleScan` 404: nếu `(err as {body?: {pending_scan?: unknown}}).body?.pending_scan` → beepError + toast `Mã ${code} đã được ghi nhận trước đó — đang chờ khớp đơn.`, KHÔNG thêm dòng. Ngược lại → thêm dòng unmatched (beepError + toast `Không tìm thấy đơn cho mã ${code} — đã ghi nhận, hệ thống sẽ tự khớp khi đơn về.`). Lỗi khác 404 → toast lỗi như cũ, không thêm dòng.
- State `scanned: LookupOrder[]` đổi thành `rows: ScanDispatchRow[]` (row unmatched render nền đỏ + badge "Chưa khớp đơn" — copy pattern JSX từ `ScanReturnDialog.tsx:255-273`). Cột "Mã sản phẩm"/"Trạng thái" hiển thị "—" cho unmatched. `removeOrder(id)` đổi thành `removeRow(code)`.
- `handleConfirm`: tách `matched`/`unmatched`; matched → `dispatchBatch` như cũ; unmatched → `createPendingScans.mutateAsync({ kind: "dispatch", warehouse_id: Number(warehouseId), codes: unmatched.map(r => r.code) })`; hiển thị kết quả per-row: matched dùng `dispatchResultBadge` như cũ, unmatched hiện `✓ Đã ghi nhận — chờ khớp` (ok) / `✗ <reason>` (reason `duplicate` → "đã ghi nhận trước đó"). Toast tổng: `Đã xuất kho X/Y đơn — ghi nhận Z mã chờ khớp.`
- Row unmatched đã ghi nhận thành công thì lần Xác nhận sau bỏ qua (giữ pattern `results` như `ScanReturnDialog.handleConfirm`).

- [ ] **Step 1: Viết test fail cho `addDispatchScanRow`** (thuần logic — theo mẫu `__tests__/scan-return-rows.test.ts`):

```typescript
import { describe, expect, it } from "vitest";
import { addDispatchScanRow } from "../scan-dispatch-rows";
import type { LookupOrder } from "../scan-types";

const order = (id: number, dispatched = false): LookupOrder => ({
  order_id: id, order_code: `DH${id}`, shipping_code: `SPX${id}`,
  status: 11, status_label: "Chờ giao", customer_name: "A", customer_phone: "0",
  fulfillment_warehouse_id: null, already_dispatched: dispatched, items: [],
});

describe("addDispatchScanRow", () => {
  it("thêm dòng matched và unmatched", () => {
    const r1 = addDispatchScanRow([], "SPX1", order(1));
    expect(r1.result).toBe("added");
    const r2 = addDispatchScanRow(r1.rows, "SPX2", null);
    expect(r2.result).toBe("added");
    expect(r2.rows[0]).toEqual({ kind: "unmatched", code: "SPX2" });
  });

  it("chặn trùng mã và trùng đơn", () => {
    const r1 = addDispatchScanRow([], "SPX1", order(1));
    expect(addDispatchScanRow(r1.rows, "SPX1", order(1)).result).toBe("duplicate");
    expect(addDispatchScanRow(r1.rows, "KHAC", order(1)).result).toBe("duplicate");
  });

  it("chặn đơn đã xuất kho", () => {
    expect(addDispatchScanRow([], "SPX1", order(1, true)).result).toBe("already-dispatched");
  });
});
```

- [ ] **Step 2: Chạy test fail** — `npm run test:ci -- scan-dispatch-rows`. Expected: FAIL.
- [ ] **Step 3: Implement `addDispatchScanRow`** + sửa dialog theo Hành vi trên.
- [ ] **Step 4: Verify** — `npm run typecheck && npm run test:ci -- scan-dispatch` (gồm cả `ScanDispatchDialog.viewport.test.tsx` hiện có — sửa test đó nếu đổi state làm vỡ, hành vi mobile giữ nguyên).
- [ ] **Step 5: Commit** — `git commit -m "feat: dispatch scan records unmatched codes"`

---

### Task 9: ScanReturnDialog — dòng unmatched đi qua bảng chờ

**Files:**
- Modify: `frontend/src/features/inventory/scan/scan-return-rows.ts`
- Modify: `frontend/src/features/inventory/scan/ScanReturnDialog.tsx`
- Test: `frontend/src/features/inventory/scan/__tests__/scan-return-rows.test.ts` (sửa)

**Interfaces:**
- Consumes: `useCreatePendingScans` (Task 7).
- Produces: `buildReturnPayloads(rows, warehouseId)` CHỈ trả payload cho rows matched (giữ nguyên shape phần tử); thêm `unmatchedCodes(rows: ScanReturnRow[]): string[]`.

**Hành vi:**
- `handleScan` 404 + `err.body?.pending_scan` → beepError + toast "đã ghi nhận trước đó — đang chờ khớp", KHÔNG thêm dòng (giống Task 8).
- `handleConfirm`: matched rows → `createReturn` từng dòng như cũ; unmatched rows (chưa ok ở lần trước) → 1 call `createPendingScans.mutateAsync({ kind: "return", warehouse_id, return_type: "delivery_returned", codes: unmatchedCodes(rows) })`; map kết quả batch về `results` per-code: ok → `{ ok: true, noteCode: "chờ khớp" }` hiển thị `✓ Đã ghi nhận — chờ khớp`; `duplicate` → `{ ok: false, error: "đã ghi nhận trước đó" }`. KHÔNG còn POST return-note 0 dòng.
- Toast tổng: `Đã tạo X phiếu trả nháp — ghi nhận Z mã chờ khớp.`

- [ ] **Step 1: Sửa test `scan-return-rows.test.ts`** — case unmatched: `buildReturnPayloads` không trả payload cho row unmatched; `unmatchedCodes` trả đúng list. Chạy fail.
- [ ] **Step 2: Implement + sửa dialog.**
- [ ] **Step 3: Verify** — `npm run typecheck && npm run test:ci -- scan-return`.
- [ ] **Step 4: Commit** — `git commit -m "feat: return scan routes unmatched codes to pending scans"`

---

### Task 10: Khối "Chờ khớp đơn" ở trang Phiếu kho

**Files:**
- Create: `frontend/src/features/inventory/scan/PendingScansDialog.tsx`
- Modify: `frontend/src/features/inventory/pages/StockDocumentsPage.tsx`
- Test: `frontend/src/features/inventory/scan/__tests__/PendingScansDialog.test.tsx`

**Interfaces:**
- Consumes: `usePendingScans`, `useRetryPendingScan`, `useDeletePendingScan` (Task 7).
- Produces: `<PendingScansDialog open onOpenChange />`.

**Hành vi:**
- `StockDocumentsPage` PageHeader actions: thêm Button outline `Chờ khớp đơn (N)` (icon `Clock` của lucide) TRƯỚC nút "Tải file Excel"; N = `usePendingScans({}).data?.total ?? 0` (endpoint mặc định trả pending+failed); ẩn nút khi `!perm("shipments:view")`. Click mở `PendingScansDialog`.
- Dialog: `FormDialogShell` (size mặc định), bảng cột: Mã vận đơn / Loại (Xuất giao | Nhập hoàn) / Kho / Người quét / Thời gian quét (`toLocaleString("vi-VN")`) / Trạng thái / thao tác. Trạng thái: `pending` → badge vàng "Chờ khớp" + số lần thử (`attempts` lần); `failed` → badge đỏ "Lỗi" + `title={error}` (tooltip) + text error rút gọn 1 dòng. Thao tác: `failed` → nút "Thử lại" (useRetryPendingScan); mọi dòng → icon Trash2 xoá (useDeletePendingScan, `window.confirm("Xoá mã ... khỏi danh sách chờ?")`). Empty state: "Không có mã nào chờ khớp.".
- KHÔNG thêm nút quét mới trong dialog này — chỉ theo dõi/xử lý.

- [ ] **Step 1: Viết test fail** — render dialog với QueryClientProvider + mock `fetchJson` (theo mẫu test dialog hiện có trong `frontend/src/features/inventory/__tests__/` — đọc 1 file ở đó lấy pattern setup): assert hiển thị dòng pending + failed, nút Thử lại chỉ ở dòng failed, click Thử lại gọi đúng endpoint `/api/inventory/pending-scans/<id>/retry`.
- [ ] **Step 2: Chạy test fail.**
- [ ] **Step 3: Implement dialog + gắn vào page.**
- [ ] **Step 4: Verify** — `npm run typecheck && npm run test:ci -- PendingScans && npm run lint`.
- [ ] **Step 5: Commit** — `git commit -m "feat: pending scans panel on stock documents page"`

---

### Task 11: Verify tổng + cập nhật docs + push + MR

**Files:**
- Modify: `docs/erp-inventory-pending-scan.md` (bảng Sprint + Ghi chú sau sprint)

- [ ] **Step 1: BE full app tests** — docker pytest `apps/inventory/tests/ apps/orders/tests/` với `--create-db` + `-e POSTGRES_DB=desi_pendingscan_final`. So kết quả fail (nếu có) với baseline đã biết ở Global Constraints — chỉ chấp nhận fail thuộc baseline.
- [ ] **Step 2: ruff** — `sh -c 'ruff check apps/inventory apps/orders config'` trong docker image test.
- [ ] **Step 3: FE** — `npm run lint && npm run typecheck && npm run test:ci` (đối chiếu baseline).
- [ ] **Step 4: Cập nhật spec** — tick bảng Sprint (S1/S2 ✅), ghi "Ghi chú sau sprint": những gì đã làm/lệch (vd semantics `matched` khi `already_dispatched`).
- [ ] **Step 5: Kiểm tra conflict master** — `git fetch origin master && git merge origin/master` (resolve nếu có, chạy lại test khi conflict đụng code).
- [ ] **Step 6: Push + MR** — `git push -u origin feat/pending-shipment-scan`, gửi link tạo MR: `https://gitlab.com/desi3512912/desi-order/-/merge_requests/new?merge_request%5Bsource_branch%5D=feat/pending-shipment-scan`
