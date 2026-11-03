# Gộp phiếu trả hàng theo ngày — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mỗi ngày / mỗi kho đúng 1 phiếu trả hàng có mã (`PTH-20260711-K1`), mọi đơn hoàn trong ngày là "nhóm đơn" (entry) bên trong phiếu; trang Phiếu kho có bộ lọc khoảng ngày + phân trang.

**Architecture:** Tách `ReturnNote` (1 đơn) hiện tại thành 2 tầng: `ReturnNote` = **phiếu ngày** (code, warehouse, doc_date, status mở/đã chốt) và `ReturnEntry` (mới) = **nhóm đơn** giữ toàn bộ ngữ nghĩa cũ (source_order, shipping_code, status draft/posted/cancelled, order_status_before, scan_batch_id). `ReturnNoteLine` đổi FK sang entry. `StockMovement.reference_type` đổi `"return"` → `"return_entry"` để huỷ lẻ 1 đơn không đụng đơn khác. Ghi sổ vẫn xảy ra **ngay khi quét**, ở mức entry.

**Tech Stack:** Django 5 + DRF + PostgreSQL (advisory lock), pytest; React 19 + TS + React Query + shadcn/ui, vitest.

**Spec:** `docs/superpowers/specs/2026-07-11-return-note-daily-batch-design.md`

## Global Constraints

- Branch: `feat/return-note-daily-batch`; worktree `c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/return-note-daily-batch`. **Không tự tạo branch mới** — commit thẳng vào branch này.
- Backend: clean architecture của `apps/inventory` — `models.py` → `application/services/*` → `transport/views|serializers`. View **không** chứa logic nghiệp vụ.
- `doc_date` luôn tính theo **giờ VN** (`django.utils.timezone.localtime()` với `TIME_ZONE="Asia/Ho_Chi_Minh"`), **không** dùng `created_at__date` (UTC).
- Mọi thao tác đọc-rồi-ghi tồn của 1 đơn phải giữ `lock_order_stock(order_id)` (advisory class 61001) — giữ nguyên bất biến hiện có.
- Comment code bằng tiếng Việt theo phong cách file hiện tại; commit message tiếng Anh, ngắn.
- File > ~300 dòng thì tách module.
- Không thêm `react-select`; FE dùng `components/ui`.

### Lệnh chạy test backend (Windows, Git Bash, từ repo root worktree)

Trước hết copy env (chỉ làm 1 lần, ở Task 1):

```bash
cp c:/Users/MYPC/Desktop/workspace/desi-order/.env c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/return-note-daily-batch/.env
cp c:/Users/MYPC/Desktop/workspace/desi-order/backend/.env c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/return-note-daily-batch/backend/.env
```

Template chạy pytest (thay `<PATHS>`):

```bash
WT=c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/return-note-daily-batch
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file $WT/.env --env-file $WT/backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -e POSTGRES_DB=desi_rnd -e DJANGO_SETTINGS_MODULE=config.settings \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$WT/backend:/app" -w /app desi-order-web-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <PATHS> -q --create-db'
```

(`--create-db` bắt buộc ở lần chạy đầu sau khi thêm migration; các lần sau có thể bỏ.)

Ruff (host): `uv run --no-project --with ruff ruff check backend/apps/inventory`
FE: `cd frontend && npm run typecheck && npx vitest run <paths>`

---

## File Structure

**Backend — tạo mới**
- `apps/inventory/application/services/return_entry_service.py` — nghiệp vụ nhóm đơn: tạo/ghi sổ/sửa/huỷ entry (kế thừa nguyên logic `ReturnNoteService` cũ).
- `apps/inventory/application/services/return_note_service.py` — **viết lại**: chỉ còn nghiệp vụ **phiếu ngày** (resolve phiếu mở, sinh mã, chốt, huỷ cả phiếu).
- `apps/inventory/transport/views/return_note_views.py` — endpoint phiếu ngày (detail / close / cancel).
- `apps/inventory/management/commands/merge_return_notes_by_day.py` — lệnh gộp hồi tố.
- `apps/inventory/migrations/0022_return_entry.py` — schema + data migration.
- Tests: `apps/inventory/tests/test_return_entry_service.py`, `test_return_note_daily.py`, `test_merge_return_notes_by_day.py`.

**Backend — sửa**
- `apps/inventory/models.py` (ReturnNote, ReturnEntry, ReturnNoteLine)
- `apps/inventory/application/services/stock_sales_service.py` (`returned_by_product` theo entry)
- `apps/inventory/application/services/pending_scan_service.py` (trỏ entry)
- `apps/inventory/application/services/return_history_service.py` (pivot sang entry)
- `apps/inventory/application/services/stock_query_service.py` (nhãn reference `return_entry`)
- `apps/inventory/transport/views/return_views.py`, `transport/serializers/return_serializers.py`, `transport/views/ledger_views.py`, `urls.py`
- **Xoá:** `transport/views/return_bulk_views.py`, `management/commands/post_matched_return_drafts.py` giữ nhưng đổi sang entry; `find_duplicate_return_notes.py` đổi sang entry.

**Frontend — sửa**
- `features/inventory/returns-types.ts`, `returns-queries.ts`, `ledger-types.ts`, `ledger-queries.ts`
- `features/inventory/pages/StockDocumentsPage.tsx` (lọc ngày + phân trang), `pages/CreateReturnDialog.tsx`
- `features/inventory/components/ReturnNoteDetailDialog.tsx` (viết lại: bảng entry)
- `features/inventory/components/ReturnCard.tsx`, `ReturnTab.tsx`, `flow-shared.tsx`, `scan/scan-return-rows.ts`
- **Xoá:** `features/inventory/ledger-grouping.ts` + test của nó.

---

## Task 1: Model `ReturnEntry` + migration tách tầng

**Files:**
- Modify: `backend/apps/inventory/models.py` (ReturnNote 259-322, ReturnNoteLine 365-385)
- Create: `backend/apps/inventory/migrations/0022_return_entry.py`
- Test: `backend/apps/inventory/tests/test_return_entry_model.py`

**Interfaces:**
- Produces: `ReturnNote(code, warehouse, doc_date, status∈{open,closed}, closed_at, closed_by, created_by, created_at)` + related_name `entries`; `ReturnEntry(return_note, source_order, shipping_code, customer_name, customer_phone, source_note, note, status∈{draft,posted,cancelled}, posted_at, order_status_before, scan_batch_id, created_by, created_at)` + related_name `lines`; `ReturnNoteLine(entry, product, quantity, return_type, condition_note)`.
- Consumes: `ReturnType`, `ReturnStatus` (giữ nguyên).

- [ ] **Step 1: Copy env vào worktree** (xem Global Constraints) và xác nhận `docker ps` có `desi-order-postgres-1`.

- [ ] **Step 2: Viết test model (fail trước)**

Tạo `backend/apps/inventory/tests/test_return_entry_model.py`:

```python
import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.inventory.models import (
    ReturnEntry,
    ReturnNote,
    ReturnNoteLine,
    ReturnNoteStatus,
    ReturnStatus,
    ReturnType,
)
from apps.warehouses.models import Warehouse


@pytest.fixture
def warehouse(db):
    return Warehouse.objects.create(name="Kho HN", code="HN")


@pytest.mark.django_db
def test_only_one_open_note_per_day_and_warehouse(warehouse):
    today = timezone.localdate()
    ReturnNote.objects.create(
        code="PTH-1", warehouse=warehouse, doc_date=today, status=ReturnNoteStatus.OPEN
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        ReturnNote.objects.create(
            code="PTH-2", warehouse=warehouse, doc_date=today,
            status=ReturnNoteStatus.OPEN,
        )


@pytest.mark.django_db
def test_closed_note_does_not_block_new_open_note(warehouse):
    today = timezone.localdate()
    ReturnNote.objects.create(
        code="PTH-1", warehouse=warehouse, doc_date=today,
        status=ReturnNoteStatus.CLOSED, closed_at=timezone.now(),
    )
    note = ReturnNote.objects.create(
        code="PTH-2", warehouse=warehouse, doc_date=today, status=ReturnNoteStatus.OPEN
    )
    assert note.pk is not None


@pytest.mark.django_db
def test_entry_shipping_code_unique_when_draft_unmatched(warehouse):
    note = ReturnNote.objects.create(
        code="PTH-1", warehouse=warehouse, doc_date=timezone.localdate()
    )
    ReturnEntry.objects.create(
        return_note=note, shipping_code="SPX1", status=ReturnStatus.DRAFT
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        ReturnEntry.objects.create(
            return_note=note, shipping_code="SPX1", status=ReturnStatus.DRAFT
        )


@pytest.mark.django_db
def test_line_requires_return_type(warehouse, django_assert_num_queries):
    note = ReturnNote.objects.create(
        code="PTH-1", warehouse=warehouse, doc_date=timezone.localdate()
    )
    entry = ReturnEntry.objects.create(return_note=note, status=ReturnStatus.DRAFT)
    from apps.orders.models import Product

    product = Product.objects.create(sku="SKU1", name="SP 1")
    with pytest.raises(IntegrityError), transaction.atomic():
        ReturnNoteLine.objects.create(
            entry=entry, product=product, quantity=1, return_type=""
        )
    line = ReturnNoteLine.objects.create(
        entry=entry, product=product, quantity=1,
        return_type=ReturnType.DELIVERY_RETURNED,
    )
    assert line.entry_id == entry.id
```

- [ ] **Step 3: Chạy test → phải FAIL** (`ImportError: cannot import name 'ReturnEntry'`)

Run: template pytest với `<PATHS>` = `apps/inventory/tests/test_return_entry_model.py`

- [ ] **Step 4: Sửa `models.py`**

Thay khối `ReturnNote` (dòng 259-322) bằng:

```python
class ReturnNoteStatus(models.TextChoices):
    OPEN = "open", "Đang mở"
    CLOSED = "closed", "Đã chốt"


class ReturnNote(models.Model):
    """Phiếu trả hàng NGÀY: 1 phiếu / ngày / kho. Chứng từ gom; mọi ngữ nghĩa
    theo đơn (tồn, trạng thái đơn) nằm ở ReturnEntry bên dưới."""

    code = models.CharField(max_length=32, unique=True)
    warehouse = models.ForeignKey(
        "warehouses.Warehouse", on_delete=models.PROTECT, related_name="return_notes"
    )
    # Ngày chứng từ theo GIỜ VN — không suy ra từ created_at (UTC) khi đọc.
    doc_date = models.DateField()
    status = models.CharField(
        max_length=20, choices=ReturnNoteStatus.choices, default=ReturnNoteStatus.OPEN
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closed_return_notes",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "stock_return_notes"
        ordering = ["-doc_date", "-id"]
        indexes = [
            models.Index(fields=["doc_date"], name="returnnote_docdate_idx"),
            models.Index(
                fields=["warehouse", "doc_date"], name="returnnote_wh_date_idx"
            ),
        ]
        constraints = [
            # Mỗi (ngày, kho) tối đa 1 phiếu ĐANG MỞ — chặn ở DB để race lúc quét
            # đồng loạt không đẻ 2 phiếu. Phiếu đã chốt không tính.
            models.UniqueConstraint(
                fields=["doc_date", "warehouse"],
                condition=Q(status="open"),
                name="returnnote_open_day_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.code} ({self.status})"


class ReturnEntry(models.Model):
    """Nhóm đơn hoàn trong phiếu ngày — đơn vị ghi sổ/huỷ thật sự.

    Giữ nguyên ngữ nghĩa của ReturnNote cũ (1 đơn ↔ 1 lần hoàn), chỉ không còn
    mã phiếu riêng.
    """

    return_note = models.ForeignKey(
        ReturnNote, on_delete=models.CASCADE, related_name="entries"
    )
    source_order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="return_entries",
    )
    shipping_code = models.CharField(max_length=64, blank=True, default="")
    customer_name = models.CharField(max_length=255, blank=True, default="")
    customer_phone = models.CharField(max_length=32, blank=True, default="")
    source_note = models.TextField(blank=True, default="")
    note = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=ReturnStatus.choices, default=ReturnStatus.DRAFT
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    # Trạng thái đơn nguồn ngay TRƯỚC khi ghi sổ đặt đơn = RETURNED.
    order_status_before = models.IntegerField(null=True, blank=True)
    scan_batch_id = models.UUIDField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "stock_return_entries"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["return_note"], name="returnentry_note_idx"),
            models.Index(fields=["source_order"], name="returnentry_order_idx"),
            models.Index(fields=["shipping_code"], name="returnentry_shipcode_idx"),
            models.Index(fields=["status"], name="returnentry_status_idx"),
            models.Index(fields=["scan_batch_id"], name="returnentry_batch_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["shipping_code"],
                condition=Q(shipping_code__gt="")
                & Q(source_order__isnull=True)
                & Q(status="draft"),
                name="returnentry_shipcode_draft_uniq",
            ),
        ]

    def __str__(self):
        return f"entry#{self.id} note{self.return_note_id} ({self.status})"
```

Thay `ReturnNoteLine` (dòng 365-385) bằng:

```python
class ReturnNoteLine(models.Model):
    entry = models.ForeignKey(
        ReturnEntry, on_delete=models.CASCADE, related_name="lines"
    )
    product = models.ForeignKey(
        "orders.Product", on_delete=models.PROTECT, related_name="return_lines"
    )
    quantity = models.PositiveIntegerField()
    # Loại hoàn BẮT BUỘC ở dòng — phiếu ngày không còn loại để kế thừa.
    return_type = models.CharField(max_length=20, choices=ReturnType.choices)
    condition_note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "stock_return_note_lines"
        constraints = [
            models.UniqueConstraint(fields=["entry", "product"], name="uniq_return_line"),
            models.CheckConstraint(
                condition=Q(return_type__in=ReturnType.values),
                name="returnline_return_type_valid",
            ),
        ]
```

- [ ] **Step 5: Viết migration `0022_return_entry.py`**

Migration **phải** làm đủ 5 việc theo thứ tự (dùng `RunPython` cho phần data):

```python
from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings
from django.db.models import Q


def forwards(apps, schema_editor):
    ReturnNote = apps.get_model("inventory", "ReturnNote")
    ReturnEntry = apps.get_model("inventory", "ReturnEntry")
    ReturnNoteLine = apps.get_model("inventory", "ReturnNoteLine")
    StockMovement = apps.get_model("inventory", "StockMovement")

    # 1 phiếu cũ → 1 entry (giữ nguyên dữ liệu; chưa gộp ngày ở bước này).
    for note in ReturnNote.objects.all().iterator():
        entry = ReturnEntry.objects.create(
            return_note_id=note.id,
            source_order_id=note.source_order_id,
            shipping_code=note.shipping_code,
            customer_name=note.customer_name,
            customer_phone=note.customer_phone,
            source_note=note.source_note,
            note=note.note,
            status=note.status,
            posted_at=note.posted_at,
            order_status_before=note.order_status_before,
            scan_batch_id=note.scan_batch_id,
            created_by_id=note.created_by_id,
            created_at=note.created_at,
        )
        # Dòng trỏ sang entry; return_type rỗng → lấy của phiếu (mặc định hàng hoàn).
        ReturnNoteLine.objects.filter(return_note_id=note.id).update(entry_id=entry.id)
        ReturnNoteLine.objects.filter(entry_id=entry.id, return_type="").update(
            return_type=note.return_type or "delivery_returned"
        )
        # Movement trỏ sang entry.
        StockMovement.objects.filter(
            reference_type="return", reference_id=note.id
        ).update(reference_type="return_entry", reference_id=entry.id)


def backwards(apps, schema_editor):
    raise RuntimeError("Migration 0022 không hỗ trợ rollback tự động — restore backup.")
```

Thứ tự operations trong `Migration.operations`:
1. `CreateModel("ReturnEntry", ...)` (không constraint unique shipping_code — thêm sau khi data đã vào để tránh vướng dữ liệu bẩn; nếu `find_duplicate_return_notes` đã dọn thì vẫn an toàn).
2. `AddField("ReturnNoteLine", "entry", FK null=True)`.
3. `AddField("ReturnNote", "doc_date", DateField(null=True))`, `closed_at`, `closed_by`.
4. `RunPython(forwards, backwards)` — copy data (như trên) **và** set `doc_date`:

   Thêm vào cuối `forwards`:
   ```python
   from zoneinfo import ZoneInfo

   VN = ZoneInfo("Asia/Ho_Chi_Minh")
   for note in ReturnNote.objects.all().iterator():
       note.doc_date = note.created_at.astimezone(VN).date()
       note.status = "closed"
       note.closed_at = note.created_at
       note.save(update_fields=["doc_date", "status", "closed_at"])
   ```
   (Phiếu cũ đặt `closed` để không vướng ràng buộc "1 phiếu mở / ngày / kho".)
5. `AlterField("ReturnNote", "doc_date", DateField())` (bỏ null), `AlterField("ReturnNoteLine", "entry", FK non-null)`.
6. `RemoveField("ReturnNoteLine", "return_note")`.
7. `RemoveField` trên `ReturnNote`: `return_type`, `source_order`, `shipping_code`, `customer_name`, `customer_phone`, `source_note`, `note`, `posted_at`, `order_status_before`, `scan_batch_id`.
8. `RemoveConstraint("ReturnNote", "returnnote_shipcode_draft_uniq")`, `RemoveConstraint("ReturnNote", "returnnote_return_type_valid")`, xoá các index cũ (`returnnote_type_status_idx`, `returnnote_order_idx`, `returnnote_created_idx`, `returnnote_shipcode_idx`, `returnnote_scanbatch_idx`).
9. `AlterField("ReturnNote", "status", ...)` sang `ReturnNoteStatus.choices`.
10. `AddConstraint` mới: `returnnote_open_day_uniq`, `returnentry_shipcode_draft_uniq`, `returnline_return_type_valid`; `AddIndex` mới theo `Meta` ở Step 4.
11. `RemoveConstraint("ReturnNoteLine", "uniq_return_line")` (cũ theo return_note) trước khi `AddConstraint` bản mới theo entry.

Sinh migration bằng `makemigrations` rồi **sửa tay** để chèn `RunPython` đúng vị trí (giữa AddField và RemoveField) — đừng để makemigrations tự sắp xếp.

- [ ] **Step 6: Chạy test → PASS**

Run: template pytest, `<PATHS>` = `apps/inventory/tests/test_return_entry_model.py`, có `--create-db`.
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/inventory/models.py backend/apps/inventory/migrations/0022_return_entry.py backend/apps/inventory/tests/test_return_entry_model.py
git commit -m "feat(inventory): split return notes into daily notes and per-order entries"
```

---

## Task 2: `ReturnNoteService` (phiếu ngày) — resolve/sinh mã/chốt

**Files:**
- Rewrite: `backend/apps/inventory/application/services/return_note_service.py`
- Test: `backend/apps/inventory/tests/test_return_note_daily.py`

**Interfaces:**
- Produces:
  - `ReturnNoteService.resolve_open_note(*, warehouse_id: int, doc_date: date, user_id: int | None = None) -> ReturnNote` — advisory lock `(62001, hash(doc_date, warehouse_id))`, trả phiếu mở sẵn hoặc tạo mới.
  - `ReturnNoteService.close_note(*, return_note_id: int, user_id: int | None = None) -> ReturnNote`
  - `ReturnNoteService.cancel_note(*, return_note_id: int, user_id: int | None = None) -> ReturnNote` (huỷ mọi entry posted; gọi `ReturnEntryService.cancel_entry`)
  - `ReturnNoteService.today_vn() -> date`
- Consumes: `ReturnEntryService.cancel_entry` (Task 3) — import cục bộ trong hàm để tránh vòng lặp import.

- [ ] **Step 1: Test (fail trước)** — `backend/apps/inventory/tests/test_return_note_daily.py`

```python
import pytest
from django.utils import timezone

from apps.inventory.application.services.return_note_service import ReturnNoteService
from apps.inventory.models import ReturnNote, ReturnNoteStatus
from apps.warehouses.models import Warehouse


@pytest.fixture
def warehouse(db):
    return Warehouse.objects.create(name="Kho HN", code="HN")


@pytest.mark.django_db
def test_resolve_open_note_reuses_same_note(warehouse):
    today = timezone.localdate()
    a = ReturnNoteService.resolve_open_note(warehouse_id=warehouse.id, doc_date=today)
    b = ReturnNoteService.resolve_open_note(warehouse_id=warehouse.id, doc_date=today)
    assert a.id == b.id
    assert a.code == f"PTH-{today:%Y%m%d}-K{warehouse.id}"
    assert ReturnNote.objects.count() == 1


@pytest.mark.django_db
def test_closed_note_forces_new_note_with_suffix(warehouse):
    today = timezone.localdate()
    first = ReturnNoteService.resolve_open_note(warehouse_id=warehouse.id, doc_date=today)
    ReturnNoteService.close_note(return_note_id=first.id)
    second = ReturnNoteService.resolve_open_note(warehouse_id=warehouse.id, doc_date=today)
    assert second.id != first.id
    assert second.code == f"PTH-{today:%Y%m%d}-K{warehouse.id}-02"
    first.refresh_from_db()
    assert first.status == ReturnNoteStatus.CLOSED
    assert first.closed_at is not None


@pytest.mark.django_db
def test_close_twice_raises(warehouse):
    note = ReturnNoteService.resolve_open_note(
        warehouse_id=warehouse.id, doc_date=timezone.localdate()
    )
    ReturnNoteService.close_note(return_note_id=note.id)
    with pytest.raises(ValueError):
        ReturnNoteService.close_note(return_note_id=note.id)
```

- [ ] **Step 2: Chạy → FAIL** (`AttributeError: resolve_open_note`)

- [ ] **Step 3: Viết `return_note_service.py` mới**

```python
from __future__ import annotations

from datetime import date

from django.db import connection, transaction
from django.utils import timezone

from apps.inventory.models import ReturnNote, ReturnNoteStatus, ReturnStatus

# Advisory lock class riêng cho phiếu ngày — 2 người quét cùng lúc không đẻ 2 phiếu.
_NOTE_LOCK_CLASS = 62001


def _lock_day(doc_date: date, warehouse_id: int) -> None:
    key = (int(doc_date.strftime("%Y%m%d")) * 1000 + warehouse_id) & 0x7FFFFFFF
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, %s);", [_NOTE_LOCK_CLASS, key]
        )


class ReturnNoteService:
    @staticmethod
    def today_vn() -> date:
        """Ngày chứng từ theo giờ VN — quét lúc 23h30 VN vẫn thuộc ngày hôm nay."""
        return timezone.localdate()

    @staticmethod
    def _next_code(doc_date: date, warehouse_id: int) -> str:
        base = f"PTH-{doc_date:%Y%m%d}-K{warehouse_id}"
        if not ReturnNote.objects.filter(code=base).exists():
            return base
        # Phiếu trước đã chốt → phiếu tiếp theo trong ngày: -02, -03, ...
        seq = ReturnNote.objects.filter(code__startswith=f"{base}-").count() + 2
        return f"{base}-{seq:02d}"

    @staticmethod
    @transaction.atomic
    def resolve_open_note(
        *, warehouse_id: int, doc_date: date, user_id: int | None = None
    ) -> ReturnNote:
        """Phiếu ngày ĐANG MỞ của (ngày, kho); tạo mới nếu chưa có."""
        _lock_day(doc_date, warehouse_id)
        note = ReturnNote.objects.filter(
            warehouse_id=warehouse_id,
            doc_date=doc_date,
            status=ReturnNoteStatus.OPEN,
        ).first()
        if note is not None:
            return note
        return ReturnNote.objects.create(
            code=ReturnNoteService._next_code(doc_date, warehouse_id),
            warehouse_id=warehouse_id,
            doc_date=doc_date,
            status=ReturnNoteStatus.OPEN,
            created_by_id=user_id,
        )

    @staticmethod
    @transaction.atomic
    def close_note(*, return_note_id: int, user_id: int | None = None) -> ReturnNote:
        note = ReturnNote.objects.select_for_update().get(id=return_note_id)
        if note.status != ReturnNoteStatus.OPEN:
            raise ValueError(f"Phiếu {note.code} đã chốt trước đó.")
        note.status = ReturnNoteStatus.CLOSED
        note.closed_at = timezone.now()
        note.closed_by_id = user_id
        note.save(update_fields=["status", "closed_at", "closed_by"])
        return note

    @staticmethod
    @transaction.atomic
    def cancel_note(*, return_note_id: int, user_id: int | None = None) -> ReturnNote:
        """Huỷ cả phiếu ngày = huỷ tuần tự mọi nhóm đơn ĐÃ ghi sổ."""
        from apps.inventory.application.services.return_entry_service import (
            ReturnEntryService,
        )

        note = ReturnNote.objects.select_for_update().get(id=return_note_id)
        entry_ids = list(
            note.entries.filter(status=ReturnStatus.POSTED)
            .order_by("id")
            .values_list("id", flat=True)
        )
        for entry_id in entry_ids:
            ReturnEntryService.cancel_entry(entry_id=entry_id, user_id=user_id)
        if note.status == ReturnNoteStatus.OPEN:
            note.status = ReturnNoteStatus.CLOSED
            note.closed_at = timezone.now()
            note.closed_by_id = user_id
            note.save(update_fields=["status", "closed_at", "closed_by"])
        return note
```

- [ ] **Step 4: Chạy test → PASS** (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory/application/services/return_note_service.py backend/apps/inventory/tests/test_return_note_daily.py
git commit -m "feat(inventory): daily return note service with open-note resolution"
```

---

## Task 3: `ReturnEntryService` — tạo / ghi sổ / sửa / huỷ nhóm đơn

**Files:**
- Create: `backend/apps/inventory/application/services/return_entry_service.py`
- Modify: `backend/apps/inventory/application/services/stock_sales_service.py:60-75` (`returned_by_product`)
- Test: `backend/apps/inventory/tests/test_return_entry_service.py`

**Interfaces:**
- Produces:
  - `ReturnEntryService.create_entry(*, warehouse_id, lines: list[dict], doc_date: date | None = None, source_order_id=None, shipping_code="", customer_name="", customer_phone="", source_note="", note="", user_id=None, scan_batch_id=None) -> ReturnEntry`
  - `ReturnEntryService.post_entry(*, entry_id: int, user_id=None) -> ReturnEntry`
  - `ReturnEntryService.create_and_post_entry(*, user_id=None, **kwargs) -> ReturnEntry`
  - `ReturnEntryService.update_draft(*, entry_id: int, data: dict) -> ReturnEntry`
  - `ReturnEntryService.update_note(*, entry_id: int, note: str) -> ReturnEntry`
  - `ReturnEntryService.cancel_entry(*, entry_id: int, user_id=None) -> ReturnEntry`
  - Hằng `RETURN_ENTRY_REFERENCE = "return_entry"`
  - `lines` mỗi phần tử: `{"product_id": int, "quantity": int, "return_type": str, "condition_note": str}` — `return_type` **bắt buộc**; nếu thiếu, service điền `delivery_returned`.
- Consumes: `ReturnNoteService.resolve_open_note`, `ReturnNoteService.today_vn` (Task 2); `lock_order_stock`, `order_has_sales`, `outstanding_by_key`, `returned_by_product` (stock_sales_service); `StockCommandService.record_movement`; `mark_order_returned`, `revert_order_status`.

- [ ] **Step 1: Test (fail trước)** — `backend/apps/inventory/tests/test_return_entry_service.py`

```python
import pytest
from django.utils import timezone

from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.return_note_service import ReturnNoteService
from apps.inventory.models import ReturnNote, ReturnStatus, ReturnType, StockItem
from apps.orders.models import Product
from apps.warehouses.models import Warehouse


@pytest.fixture
def setup(db):
    wh = Warehouse.objects.create(name="Kho HN", code="HN")
    p1 = Product.objects.create(sku="SKU1", name="SP 1")
    p2 = Product.objects.create(sku="SKU2", name="SP 2")
    return wh, p1, p2


def _lines(product, qty=1):
    return [{
        "product_id": product.id,
        "quantity": qty,
        "return_type": ReturnType.DELIVERY_RETURNED,
    }]


@pytest.mark.django_db
def test_two_entries_same_day_share_one_note(setup):
    wh, p1, p2 = setup
    e1 = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p1), shipping_code="SPX1"
    )
    e2 = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p2), shipping_code="SPX2"
    )
    assert e1.return_note_id == e2.return_note_id
    assert ReturnNote.objects.count() == 1
    assert e1.status == ReturnStatus.POSTED


@pytest.mark.django_db
def test_entry_goes_to_new_note_after_close(setup):
    wh, p1, p2 = setup
    e1 = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p1), shipping_code="SPX1"
    )
    ReturnNoteService.close_note(return_note_id=e1.return_note_id)
    e2 = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p2), shipping_code="SPX2"
    )
    assert e2.return_note_id != e1.return_note_id
    assert ReturnNote.objects.count() == 2


@pytest.mark.django_db
def test_post_entry_adds_stock(setup):
    wh, p1, _ = setup
    ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p1, qty=3), shipping_code="SPX1"
    )
    item = StockItem.objects.get(warehouse=wh, product=p1, state="sellable")
    assert item.quantity == 3


@pytest.mark.django_db
def test_cancel_entry_only_reverses_that_entry(setup):
    wh, p1, p2 = setup
    e1 = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p1, qty=3), shipping_code="SPX1"
    )
    ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p2, qty=5), shipping_code="SPX2"
    )
    ReturnEntryService.cancel_entry(entry_id=e1.id)

    e1.refresh_from_db()
    assert e1.status == ReturnStatus.CANCELLED
    assert StockItem.objects.get(
        warehouse=wh, product=p1, state="sellable"
    ).quantity == 0
    assert StockItem.objects.get(
        warehouse=wh, product=p2, state="sellable"
    ).quantity == 5


@pytest.mark.django_db
def test_duplicate_shipping_code_rejected(setup):
    wh, p1, p2 = setup
    ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p1), shipping_code="SPX1"
    )
    with pytest.raises(ValueError, match="SPX1"):
        ReturnEntryService.create_entry(
            warehouse_id=wh.id, lines=_lines(p2), shipping_code="SPX1"
        )


@pytest.mark.django_db
def test_cancel_allowed_after_note_closed(setup):
    wh, p1, _ = setup
    e1 = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, lines=_lines(p1, qty=2), shipping_code="SPX1"
    )
    ReturnNoteService.close_note(return_note_id=e1.return_note_id)
    ReturnEntryService.cancel_entry(entry_id=e1.id)
    e1.refresh_from_db()
    assert e1.status == ReturnStatus.CANCELLED
```

- [ ] **Step 2: Chạy → FAIL** (module chưa tồn tại)

- [ ] **Step 3: Viết `return_entry_service.py`**

Chuyển nguyên logic từ `ReturnNoteService` cũ (xem `git show HEAD~2 -- backend/apps/inventory/application/services/return_note_service.py` nếu cần), đổi 4 điểm:
1. Đối tượng là `ReturnEntry`, phiếu ngày lấy từ `ReturnNoteService.resolve_open_note`.
2. `_RETURN_STATE[effective]` → `effective` giờ là `line.return_type` (không kế thừa; nếu rỗng → `delivery_returned`).
3. Reference movement: `reference_type=RETURN_ENTRY_REFERENCE`, `reference_id=entry.id`.
4. Guard trùng `shipping_code` và `_guard_double_credit` truy vấn theo `ReturnEntry` (dùng `exclude_entry_id`).

```python
from __future__ import annotations

from collections import defaultdict
from datetime import date

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.inventory.application.services.return_note_service import ReturnNoteService
from apps.inventory.application.services.return_order_sync import (
    mark_order_returned,
    revert_order_status,
)
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import (
    lock_order_stock,
    order_has_sales,
    outstanding_by_key,
    returned_by_product,
)
from apps.inventory.models import (
    MovementType,
    ReturnEntry,
    ReturnNoteLine,
    ReturnStatus,
    ReturnType,
    StockMovement,
    StockState,
)

RETURN_ENTRY_REFERENCE = "return_entry"
_RETURN_STATE = {
    ReturnType.DELIVERY_RETURNED: StockState.SELLABLE,
    ReturnType.EXTERNAL_RETURN: StockState.SELLABLE,
    ReturnType.WARRANTY: StockState.WARRANTY,
    ReturnType.DEFECT: StockState.DEFECT,
}


class ReturnEntryService:
    @staticmethod
    def _guard_double_credit(entry: ReturnEntry) -> None:
        """Chặn ghi sổ khi các đơn vị này đã được cộng lại tồn cho đơn nguồn rồi."""
        if not entry.source_order_id:
            return
        lock_order_stock(entry.source_order_id)
        if not order_has_sales(entry.source_order_id):
            return
        deducted: dict[int, int] = defaultdict(int)
        for (_wh, product_id), qty in outstanding_by_key(entry.source_order_id).items():
            deducted[product_id] += qty
        already_returned = returned_by_product(
            entry.source_order_id, exclude_entry_id=entry.id
        )
        for line in entry.lines.all():
            available = deducted.get(line.product_id, 0) - already_returned.get(
                line.product_id, 0
            )
            if line.quantity > available:
                raise ValueError(
                    f"Sản phẩm #{line.product_id}: đơn nguồn chỉ còn {max(available, 0)} "
                    f"đơn vị chưa được cộng lại tồn (phiếu nhập {line.quantity}). "
                    "Đơn đã hoàn kho khi huỷ hoặc đã có phiếu trả khác ghi sổ."
                )

    @staticmethod
    @transaction.atomic
    def create_entry(
        *,
        warehouse_id: int,
        lines: list[dict],
        doc_date: date | None = None,
        source_order_id: int | None = None,
        shipping_code: str = "",
        customer_name: str = "",
        customer_phone: str = "",
        source_note: str = "",
        note: str = "",
        user_id: int | None = None,
        scan_batch_id: str | None = None,
    ) -> ReturnEntry:
        if shipping_code:
            # Invariant: 1 mã vận đơn ↔ 1 lần hoàn chưa huỷ (chống khớp hoàn 2 lần).
            dup = (
                ReturnEntry.objects.filter(shipping_code=shipping_code)
                .exclude(status=ReturnStatus.CANCELLED)
                .select_related("return_note")
                .order_by("-id")
                .first()
            )
            if dup is not None:
                raise ValueError(
                    f"Mã {shipping_code} đã có trong phiếu {dup.return_note.code} "
                    "(chưa huỷ) — không tạo trùng."
                )
        note_obj = ReturnNoteService.resolve_open_note(
            warehouse_id=warehouse_id,
            doc_date=doc_date or ReturnNoteService.today_vn(),
            user_id=user_id,
        )
        try:
            entry = ReturnEntry.objects.create(
                return_note=note_obj,
                source_order_id=source_order_id,
                shipping_code=shipping_code,
                customer_name=customer_name,
                customer_phone=customer_phone,
                source_note=source_note,
                note=note,
                created_by_id=user_id,
                scan_batch_id=scan_batch_id,
            )
        except IntegrityError as exc:
            if "returnentry_shipcode_draft_uniq" in str(exc):
                raise ValueError(
                    f"Mã {shipping_code} đã được ghi nhận ở phiếu trả khác."
                ) from exc
            raise
        ReturnNoteLine.objects.bulk_create([
            ReturnNoteLine(
                entry=entry,
                product_id=line["product_id"],
                quantity=line["quantity"],
                return_type=line.get("return_type") or ReturnType.DELIVERY_RETURNED,
                condition_note=line.get("condition_note", ""),
            )
            for line in lines
        ])
        return entry

    @staticmethod
    @transaction.atomic
    def post_entry(*, entry_id: int, user_id: int | None = None) -> ReturnEntry:
        entry = ReturnEntry.objects.select_for_update().get(id=entry_id)
        if entry.status != ReturnStatus.DRAFT:
            raise ValueError(f"Chỉ nhóm đơn nháp mới ghi sổ được (đang {entry.status}).")
        lines = list(entry.lines.select_related("product"))
        if not lines:
            raise ValueError("Chưa có dòng hàng — hãy khớp đơn trước khi ghi sổ.")
        ReturnEntryService._guard_double_credit(entry)
        note_obj = entry.return_note
        for line in lines:
            StockCommandService.record_movement(
                warehouse_id=note_obj.warehouse_id,
                product_id=line.product_id,
                qty_delta=line.quantity,
                movement_type=MovementType.RETURN_IN,
                state=_RETURN_STATE[line.return_type],
                reference_type=RETURN_ENTRY_REFERENCE,
                reference_id=entry.id,
                user_id=user_id,
            )
        entry.status = ReturnStatus.POSTED
        entry.posted_at = timezone.now()
        update_fields = ["status", "posted_at"]

        has_delivery_returned = any(
            line.return_type == ReturnType.DELIVERY_RETURNED for line in lines
        )
        if has_delivery_returned and entry.source_order_id:
            from apps.orders.models import Order  # local import: giữ ranh giới module

            order = (
                Order.objects.select_for_update()
                .filter(pk=entry.source_order_id)
                .first()
            )
            if order is not None:
                previous = mark_order_returned(order, user_id=user_id)
                if previous is not None:
                    entry.order_status_before = previous
                    update_fields.append("order_status_before")

        entry.save(update_fields=update_fields)
        return entry

    @staticmethod
    @transaction.atomic
    def create_and_post_entry(*, user_id: int | None = None, **create_kwargs) -> ReturnEntry:
        """Tạo + ghi sổ trong 1 transaction (luồng quét khớp mã)."""
        entry = ReturnEntryService.create_entry(user_id=user_id, **create_kwargs)
        return ReturnEntryService.post_entry(entry_id=entry.id, user_id=user_id)

    @staticmethod
    @transaction.atomic
    def cancel_entry(*, entry_id: int, user_id: int | None = None) -> ReturnEntry:
        entry = ReturnEntry.objects.select_for_update().select_related(
            "return_note"
        ).get(id=entry_id)
        if entry.status != ReturnStatus.POSTED:
            raise ValueError(f"Chỉ nhóm đơn đã ghi sổ mới huỷ được (đang {entry.status}).")
        if entry.source_order_id:
            lock_order_stock(entry.source_order_id)
        movements = StockMovement.objects.filter(
            reference_type=RETURN_ENTRY_REFERENCE, reference_id=entry.id
        )
        for mv in movements:
            StockCommandService.record_movement(
                warehouse_id=mv.warehouse_id,
                product_id=mv.product_id,
                qty_delta=-mv.qty_delta,
                movement_type=mv.movement_type,
                state=mv.state,
                reference_type=RETURN_ENTRY_REFERENCE,
                reference_id=entry.id,
                note=f"Huỷ nhóm đơn #{entry.id} trong phiếu {entry.return_note.code}",
                user_id=user_id,
            )
        entry.status = ReturnStatus.CANCELLED
        update_fields = ["status"]

        if entry.source_order_id and entry.order_status_before is not None:
            from apps.orders.models import Order, OrderStatus  # local import

            order = (
                Order.objects.select_for_update()
                .filter(pk=entry.source_order_id)
                .first()
            )
            if order is not None and int(order.status) == int(OrderStatus.RETURNED):
                revert_order_status(order, entry.order_status_before, user_id=user_id)
                entry.order_status_before = None
                update_fields.append("order_status_before")

        entry.save(update_fields=update_fields)
        return entry
```

`update_draft` và `update_note` bê nguyên từ `ReturnNoteService` cũ (dòng 209-283 của bản cũ), đổi `ReturnNote`→`ReturnEntry`, `return_note_id`→`entry_id`, và khi tự nạp dòng từ đơn thì `return_type` mặc định `ReturnType.DELIVERY_RETURNED` (không còn kế thừa từ phiếu). **Nếu file vượt ~300 dòng**, tách `update_draft`/`update_note` sang `return_entry_edit_service.py`.

- [ ] **Step 4: Sửa `returned_by_product` trong `stock_sales_service.py`**

```python
def returned_by_product(order_id: int, *, exclude_entry_id: int | None = None) -> dict[int, int]:
    """Số lượng đã nhập lại tồn qua ReturnEntry ĐÃ ghi sổ của đơn, theo product."""
    qs = ReturnNoteLine.objects.filter(
        entry__source_order_id=order_id,
        entry__status=ReturnStatus.POSTED,
    )
    if exclude_entry_id is not None:
        qs = qs.exclude(entry_id=exclude_entry_id)
    rows = qs.values("product_id").annotate(total=Sum("quantity"))
    return {row["product_id"]: row["total"] for row in rows}
```

Sửa mọi call-site còn lại của `returned_by_product(..., exclude_return_id=...)` (grep) sang `exclude_entry_id`.

- [ ] **Step 5: Chạy test → PASS**

Run: `<PATHS>` = `apps/inventory/tests/test_return_entry_service.py` → 6 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/inventory/application/services/ backend/apps/inventory/tests/test_return_entry_service.py
git commit -m "feat(inventory): return entry service posting into daily notes"
```

---

## Task 4: Pending scan + reference label + lệnh dọn dữ liệu trỏ sang entry

**Files:**
- Modify: `backend/apps/inventory/application/services/pending_scan_service.py:130-147, 391-430`
- Modify: `backend/apps/inventory/application/services/stock_query_service.py:35-66`
- Modify: `backend/apps/inventory/management/commands/find_duplicate_return_notes.py`, `post_matched_return_drafts.py`
- Test: `backend/apps/inventory/tests/test_pending_scan_service_match.py` (cập nhật), `test_pending_scan_note.py` (cập nhật)

**Interfaces:**
- Consumes: `ReturnEntryService.create_and_post_entry`, `RETURN_ENTRY_REFERENCE` (Task 3).
- Produces: `PendingScanService._complete_return` trả `(note_code, warning)` — **`note_code` giờ là mã phiếu ngày**.

- [ ] **Step 1: Cập nhật test hiện có cho kỳ vọng mới**

Trong `test_pending_scan_service_match.py`, đổi mọi assert dựa vào `ReturnNote.objects.get(shipping_code=...)` thành `ReturnEntry.objects.get(shipping_code=...)`; thêm test mới:

```python
@pytest.mark.django_db
def test_matched_scan_creates_entry_in_daily_note(...):
    # (dựng scan pending + đơn như test cũ)
    PendingScanService.match_pending(...)
    entry = ReturnEntry.objects.get(shipping_code=scan.code)
    assert entry.status == ReturnStatus.POSTED
    assert entry.return_note.code.startswith("PTH-")
```

- [ ] **Step 2: Chạy → FAIL**

- [ ] **Step 3: Sửa `pending_scan_service.py`**

- Khối `_attach_return_info` (dòng ~130): đổi `ReturnNote.objects.filter(shipping_code__in=codes)` → `ReturnEntry.objects.filter(shipping_code__in=codes)`, `it["return_id"] = rn["id"]` giữ tên key nhưng **giá trị là entry id** (FE dùng nó để PATCH khớp đơn — Task 6 giữ nguyên tên field trên API để không vỡ FE).
- `_complete_return` (dòng ~393): `existing = ReturnEntry.objects.filter(shipping_code=scan.code).exclude(status=CANCELLED).select_related("return_note").order_by("-id").first()` → nếu có, trả `existing.return_note.code`.
- Gọi `ReturnEntryService.create_and_post_entry(...)` với `doc_date=timezone.localtime(scan.created_at).date()` (phiếu ngày của **ngày quét gốc**, không phải ngày cron chạy), `lines=[{"product_id": pid, "quantity": qty, "return_type": scan.return_type or "delivery_returned"}]`, và trả `entry.return_note.code`.

- [ ] **Step 4: Sửa `stock_query_service.resolve_reference_labels`**

```python
        entry_ids = ids_by_type.get("return_entry")
        if entry_ids:
            rows = ReturnEntry.objects.filter(id__in=entry_ids).values(
                "id", "return_note__code", "shipping_code"
            )
            for row in rows:
                label = row["return_note__code"] or f"#{row['id']}"
                if row["shipping_code"]:
                    label = f"{label} / {row['shipping_code']}"
                labels[("return_entry", row["id"])] = label
```

Giữ nhánh `"return"` cũ (dữ liệu lịch sử trước migration đã được đổi hết, nhưng để nguyên vài dòng cho an toàn nếu có movement lạ) — nếu grep xác nhận không còn `reference_type="return"` sau migration thì **xoá hẳn** nhánh cũ.

- [ ] **Step 5: Sửa 2 management command**

- `find_duplicate_return_notes.py`: đổi truy vấn nhóm trùng sang `ReturnEntry` (`shipping_code`, loại `cancelled`), in ra `entry.id` + mã phiếu ngày.
- `post_matched_return_drafts.py`: quét `ReturnEntry` `draft` có `source_order` + lines → gọi `ReturnEntryService.post_entry`.

- [ ] **Step 6: Chạy test → PASS**

Run: `<PATHS>` = `apps/inventory/tests/test_pending_scan_service_match.py apps/inventory/tests/test_pending_scan_note.py apps/inventory/tests/test_find_duplicate_return_notes.py apps/inventory/tests/test_post_matched_return_drafts_command.py`

- [ ] **Step 7: Commit**

```bash
git add backend/apps/inventory
git commit -m "refactor(inventory): point pending scans and stock labels at return entries"
```

---

## Task 5: Lịch sử nhập xuất (`return_history_service`) pivot sang entry

**Files:**
- Modify: `backend/apps/inventory/application/services/return_history_service.py` (toàn bộ)
- Modify: `backend/apps/inventory/transport/views/flow_history_views.py` (chỉ chỗ đọc field nếu có)
- Test: `backend/apps/inventory/tests/test_return_history_service.py`, `test_return_history.py`, `test_flow_history_views.py` (cập nhật)

**Interfaces:**
- Produces: response giữ nguyên shape hiện tại (`id`, `code`, `source_order_id`, `source_order_code`, `shipping_code`, `warehouse_name`, `qty`, `created_at`, ...) — **`id` = entry id**, **`code` = mã phiếu ngày** (thêm field mới `note_code` = mã phiếu ngày, giữ `code` cho tương thích FE thì đặt `code = note.code`).
- Consumes: `ReturnEntry`, `ReturnNoteLine.entry`.

- [ ] **Step 1: Cập nhật test kỳ vọng** — trong `test_return_history_service.py`, dựng dữ liệu qua `ReturnEntryService.create_and_post_entry` thay vì `ReturnNoteService.create_return`; assert `row["code"].startswith("PTH-")` và `row["shipping_code"]` đúng.

- [ ] **Step 2: Chạy → FAIL**

- [ ] **Step 3: Sửa `return_history_service.py`**

Đổi toàn bộ pivot:
- `_base`: `ReturnEntry.objects.filter(status=ReturnStatus.POSTED)`; lọc ngày theo `return_note__doc_date__gte/lte` (thay `created_at__date` — chuẩn giờ VN); `warehouse_id` → `return_note__warehouse_id`; product filter dùng `ReturnNoteLine.objects.filter(entry_id=OuterRef("pk"))`.
- `list_page` / lines / export: `select_related("return_note", "return_note__warehouse", "source_order")`; `qty_map` theo `entry_id`.
- Row dict: `"code": n.return_note.code`, `"warehouse_name": n.return_note.warehouse.name`, giữ nguyên các key còn lại.

- [ ] **Step 4: Chạy test → PASS**

Run: `<PATHS>` = `apps/inventory/tests/test_return_history_service.py apps/inventory/tests/test_return_history.py apps/inventory/tests/test_flow_history_views.py`

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory
git commit -m "refactor(inventory): flow history reads return entries"
```

---

## Task 6: API — endpoint entry + phiếu ngày, gỡ bulk-post

**Files:**
- Modify: `backend/apps/inventory/transport/serializers/return_serializers.py`
- Modify: `backend/apps/inventory/transport/views/return_views.py`
- Create: `backend/apps/inventory/transport/views/return_note_views.py`
- Delete: `backend/apps/inventory/transport/views/return_bulk_views.py`
- Modify: `backend/apps/inventory/urls.py`
- Test: `backend/apps/inventory/tests/test_return_api.py` (cập nhật), `test_return_note_api.py` (mới)

**Interfaces:**
- Produces (đường dẫn giữ prefix cũ để FE đổi ít):
  - `POST /api/inventory/returns` → 201 `ReturnEntrySerializer` (có field lồng `note: {id, code, doc_date, status, warehouse_name}`)
  - `GET /api/inventory/returns` → list entry (filter `status`, `return_note`)
  - `GET|PATCH /api/inventory/returns/{entry_id}` → chi tiết / sửa nháp
  - `POST /api/inventory/returns/{entry_id}/post` → ghi sổ (section `returns`)
  - `POST /api/inventory/returns/{entry_id}/cancel` → huỷ entry (`returns:approve`)
  - `GET /api/inventory/return-notes/{id}` → `ReturnNoteDetailSerializer` = phiếu ngày + `entries[]` (mỗi entry kèm `lines[]`)
  - `POST /api/inventory/return-notes/{id}/close` → chốt (`returns:approve`)
  - `POST /api/inventory/return-notes/{id}/cancel` → huỷ cả phiếu (`returns:approve`)
- **Xoá:** `POST /api/inventory/returns/bulk-post`.

- [ ] **Step 1: Test API (fail trước)** — `backend/apps/inventory/tests/test_return_note_api.py`

```python
import pytest
from rest_framework.test import APIClient

from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.models import ReturnNoteStatus, ReturnStatus, ReturnType
from apps.orders.models import Product
from apps.warehouses.models import Warehouse


@pytest.fixture
def api(db, django_user_model):
    user = django_user_model.objects.create_user(username="kho", password="x")
    # cấp quyền returns:view + returns:approve theo helper sẵn có trong test_return_api.py
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


@pytest.mark.django_db
def test_create_return_returns_entry_with_daily_note(api):
    client, _ = api
    wh = Warehouse.objects.create(name="Kho HN", code="HN")
    p = Product.objects.create(sku="SKU1", name="SP")
    res = client.post(
        "/api/inventory/returns",
        {
            "warehouse_id": wh.id,
            "shipping_code": "SPX1",
            "auto_post": True,
            "lines": [
                {"product_id": p.id, "quantity": 2,
                 "return_type": ReturnType.DELIVERY_RETURNED},
            ],
        },
        format="json",
    )
    assert res.status_code == 201, res.data
    assert res.data["status"] == ReturnStatus.POSTED
    # Key phiếu ngày là "return_note" — "note" đã là ghi chú của entry.
    assert res.data["return_note"]["code"].startswith("PTH-")


@pytest.mark.django_db
def test_close_note_then_cancel_entry(api):
    client, user = api
    wh = Warehouse.objects.create(name="Kho HN", code="HN")
    p = Product.objects.create(sku="SKU1", name="SP")
    entry = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, shipping_code="SPX1",
        lines=[{"product_id": p.id, "quantity": 1,
                "return_type": ReturnType.DELIVERY_RETURNED}],
    )
    note_id = entry.return_note_id

    res = client.post(f"/api/inventory/return-notes/{note_id}/close")
    assert res.status_code == 200
    assert res.data["status"] == ReturnNoteStatus.CLOSED

    res = client.post(f"/api/inventory/returns/{entry.id}/cancel")
    assert res.status_code == 200
    assert res.data["status"] == ReturnStatus.CANCELLED


@pytest.mark.django_db
def test_note_detail_lists_entries(api):
    client, _ = api
    wh = Warehouse.objects.create(name="Kho HN", code="HN")
    p1 = Product.objects.create(sku="SKU1", name="SP1")
    p2 = Product.objects.create(sku="SKU2", name="SP2")
    e1 = ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, shipping_code="SPX1",
        lines=[{"product_id": p1.id, "quantity": 1,
                "return_type": ReturnType.DELIVERY_RETURNED}],
    )
    ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, shipping_code="SPX2",
        lines=[{"product_id": p2.id, "quantity": 3,
                "return_type": ReturnType.WARRANTY}],
    )
    res = client.get(f"/api/inventory/return-notes/{e1.return_note_id}")
    assert res.status_code == 200
    assert len(res.data["entries"]) == 2
    assert res.data["entry_count"] == 2
    assert res.data["qty_total"] == 4
```

(Dùng đúng helper cấp quyền của `test_return_api.py` hiện có — đọc file đó trước khi viết fixture.)

- [ ] **Step 2: Chạy → FAIL**

- [ ] **Step 3: Serializers**

Trong `return_serializers.py`:
- `ReturnNoteLineSerializer`: `return_type` bắt buộc (`required=True`).
- Đổi `ReturnNoteSerializer` → `ReturnEntrySerializer` (`Meta.model = ReturnEntry`), field: `id, status, source_order, source_order_invoice_code, shipping_code, customer_name, customer_phone, source_note, note, created_by_name, created_at, posted_at, scan_batch_id, lines, return_note`. **`note` là GHI CHÚ của entry** (giữ nguyên nghĩa cũ); phiếu ngày lồng vào key **`return_note`** = `ReturnNoteBriefSerializer(read_only=True)`. Không đặt tên khác — FE (Task 9) và test (Step 1) đều dùng `return_note`.
- `ReturnNoteBriefSerializer`: `id, code, doc_date, status, warehouse, warehouse_name`.
- `ReturnNoteDetailSerializer`: brief + `entries` (`ReturnEntrySerializer(many=True)`) + `entry_count` + `qty_total` (SerializerMethodField, tính bằng `Sum` prefetch — không N+1: view prefetch `entries__lines__product`).
- `CreateReturnNoteSerializer`: bỏ `return_type` cấp phiếu; `_ReturnLineInput.return_type` thành `required=True`; giữ `warehouse_id`, `source_order_id`, `shipping_code`, `customer_*`, `note`, `scan_batch_id`, `auto_post`, `lines`; validate giữ nguyên (cho phép 0 dòng khi có shipping_code + chưa có đơn).
- `UpdateReturnNoteSerializer` → `UpdateReturnEntrySerializer`: bỏ `return_type` cấp phiếu.

- [ ] **Step 4: Views + urls**

- `return_views.py`: `list_create_returns` gọi `ReturnEntryService.create_entry`/`create_and_post_entry`; `return_detail` (GET/PATCH) theo `entry_id`; `post_return_view` → `post_entry`; `cancel_return_view` → `cancel_entry` (giữ `require_permission("returns:approve")`). Queryset prefetch: `.select_related("return_note", "return_note__warehouse", "source_order", "created_by").prefetch_related("lines__product")`.
- `return_note_views.py` (mới): `return_note_detail` (GET, section `returns`), `close_return_note` (POST, `returns:approve`), `cancel_return_note` (POST, `returns:approve`). Bắt `ReturnNote.DoesNotExist` → 404, `ValueError` → 400 `{"detail": str(exc)}` (theo đúng pattern file cũ).
- `urls.py`: xoá `returns/bulk-post` + import `bulk_post_returns`; thêm 3 path `return-notes/...`. Xoá file `return_bulk_views.py` và test `test_return_scan_batch.py` phần bulk-post.

- [ ] **Step 5: Chạy test → PASS**

Run: `<PATHS>` = `apps/inventory/tests/test_return_note_api.py apps/inventory/tests/test_return_api.py`

- [ ] **Step 6: Commit**

```bash
git add backend/apps/inventory && git rm backend/apps/inventory/transport/views/return_bulk_views.py
git commit -m "feat(inventory): return entry and daily note endpoints"
```

---

## Task 7: `ledger-documents` — dòng phiếu trả = phiếu ngày

**Files:**
- Modify: `backend/apps/inventory/transport/views/ledger_views.py`
- Test: `backend/apps/inventory/tests/test_ledger_documents.py` (tạo mới nếu chưa có; grep trước)

**Interfaces:**
- Produces: item `kind="return"` có `id` (note id), `code`, `type="return"`, `type_label="Phiếu trả hàng"`, `status` ∈ `{open, closed}`, `status_label`, `warehouse_label`, `created_at`, **mới**: `doc_date`, `entry_count`, `qty_total`, `cancelled_count`. Bỏ: `return_type`, `shipping_code`, `source_order`, `scan_batch_id` khỏi dòng return.
- Filter `date_from`/`date_to` với dòng return giờ so theo `doc_date` (không phải `created_at`).

- [ ] **Step 1: Test (fail trước)**

```python
@pytest.mark.django_db
def test_ledger_lists_daily_return_note_with_counts(api_with_returns_view):
    client, wh, p = api_with_returns_view
    ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, shipping_code="SPX1",
        lines=[{"product_id": p.id, "quantity": 2,
                "return_type": ReturnType.DELIVERY_RETURNED}],
    )
    ReturnEntryService.create_and_post_entry(
        warehouse_id=wh.id, shipping_code="SPX2",
        lines=[{"product_id": p.id, "quantity": 3,
                "return_type": ReturnType.DELIVERY_RETURNED}],
    )
    res = client.get("/api/inventory/ledger-documents?type=return")
    assert res.status_code == 200
    rows = [r for r in res.data["items"] if r["kind"] == "return"]
    assert len(rows) == 1
    assert rows[0]["entry_count"] == 2
    assert rows[0]["qty_total"] == 5
    assert rows[0]["status"] == "open"
```

- [ ] **Step 2: Chạy → FAIL**

- [ ] **Step 3: Sửa `ledger_views.py`**

Nhánh return trong union đổi sang `ReturnNote` (phiếu ngày):

```python
    if can_return and want_return:
        r = ReturnNote.objects.all()
        if statuses:
            r = r.filter(status__in=statuses)
        if q:
            r = r.filter(code__icontains=q)
        if date_from:
            r = r.filter(doc_date__gte=date_from)
        if date_to:
            r = r.filter(doc_date__lte=date_to)
        r = r.annotate(
            kind=Value("return", output_field=CharField()),
            type_code=Value("return", output_field=CharField()),
            src_wh=Value(None, output_field=IntegerField()),
            dest_wh=F("warehouse_id"),
            has_att=Value(False, output_field=BooleanField()),
        ).values(*_COLS)
        parts.append(r)
```

Sau khi paginate, resolve-extra cho các note id trong trang (**không** thêm cột vào union):

```python
    return_ids = [it["id"] for it in items if it["kind"] == "return"]
    if return_ids:
        agg = {
            row["return_note_id"]: row
            for row in ReturnEntry.objects.filter(return_note_id__in=return_ids)
            .values("return_note_id")
            .annotate(
                entry_count=Count("id"),
                cancelled_count=Count("id", filter=Q(status=ReturnStatus.CANCELLED)),
            )
        }
        qty = {
            row["entry__return_note_id"]: row["total"]
            for row in ReturnNoteLine.objects.filter(
                entry__return_note_id__in=return_ids,
            )
            .exclude(entry__status=ReturnStatus.CANCELLED)
            .values("entry__return_note_id")
            .annotate(total=Sum("quantity"))
        }
        dates = dict(
            ReturnNote.objects.filter(id__in=return_ids).values_list("id", "doc_date")
        )
        for it in items:
            if it["kind"] != "return":
                continue
            row = agg.get(it["id"], {})
            it["entry_count"] = row.get("entry_count", 0)
            it["cancelled_count"] = row.get("cancelled_count", 0)
            it["qty_total"] = qty.get(it["id"], 0)
            it["doc_date"] = dates.get(it["id"])
```

Nhãn: `_RETURN_STATUS_LABELS = dict(ReturnNoteStatus.choices)`; `type_label` dòng return = `"Phiếu trả hàng"`. Xoá phần resolve `scan_batch_id`/`shipping_code`/`source_order` cho dòng return.

Cập nhật `@extend_schema` inline serializer: thêm `doc_date`, `entry_count`, `qty_total`, `cancelled_count`; bỏ `return_type`, `shipping_code`, `source_order`, `scan_batch_id`.

- [ ] **Step 4: Chạy test → PASS**

- [ ] **Step 5: Chạy toàn bộ suite inventory + orders**

Run: `<PATHS>` = `apps/inventory apps/orders` — sửa mọi test đỏ do đổi model (kỳ vọng: các test cũ dùng `ReturnNoteService.create_return` phải chuyển sang `ReturnEntryService`). Baseline pre-existing fail: xem memory `known-failing-baseline-tests` — không debug lại.

- [ ] **Step 6: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/inventory
git add backend && git commit -m "feat(inventory): ledger lists daily return notes with aggregates"
```

---

## Task 8: Lệnh gộp hồi tố `merge_return_notes_by_day`

**Files:**
- Create: `backend/apps/inventory/management/commands/merge_return_notes_by_day.py`
- Test: `backend/apps/inventory/tests/test_merge_return_notes_by_day.py`

**Interfaces:**
- Produces: `python manage.py merge_return_notes_by_day [--dry-run] [--date-from YYYY-MM-DD]`
- Bất biến: **tổng tồn theo (warehouse, product, state) trước và sau phải giống hệt**; chỉ đổi `ReturnEntry.return_note_id` + tạo/xoá `ReturnNote`, **không** đụng `StockMovement.qty_delta` (movement trỏ entry nên không cần sửa gì).

- [ ] **Step 1: Test (fail trước)** — `test_merge_return_notes_by_day.py`

```python
import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.inventory.models import (
    ReturnEntry,
    ReturnNote,
    ReturnNoteStatus,
    ReturnStatus,
    ReturnType,
    StockItem,
)
from apps.orders.models import Product
from apps.warehouses.models import Warehouse


@pytest.mark.django_db
def test_merge_groups_legacy_notes_by_day_without_touching_stock():
    wh = Warehouse.objects.create(name="Kho HN", code="HN")
    p = Product.objects.create(sku="SKU1", name="SP")
    today = timezone.localdate()

    # Dữ liệu "cũ": 3 phiếu ngày riêng lẻ (mỗi phiếu 1 entry) — như sau migration 0022.
    entries = []
    for i in range(3):
        note = ReturnNote.objects.create(
            code=f"TH{today:%Y%m%d}-{i + 1:04d}",
            warehouse=wh,
            doc_date=today,
            status=ReturnNoteStatus.CLOSED,
        )
        entry = ReturnEntry.objects.create(
            return_note=note, shipping_code=f"SPX{i}", status=ReturnStatus.POSTED
        )
        entries.append(entry)

    before = list(
        StockItem.objects.values_list("warehouse_id", "product_id", "state", "quantity")
    )

    call_command("merge_return_notes_by_day")

    assert ReturnNote.objects.count() == 1
    note = ReturnNote.objects.get()
    assert note.code == f"PTH-{today:%Y%m%d}-K{wh.id}"
    assert note.entries.count() == 3
    after = list(
        StockItem.objects.values_list("warehouse_id", "product_id", "state", "quantity")
    )
    assert before == after


@pytest.mark.django_db
def test_dry_run_changes_nothing():
    wh = Warehouse.objects.create(name="Kho HN", code="HN")
    today = timezone.localdate()
    for i in range(2):
        note = ReturnNote.objects.create(
            code=f"TH{today:%Y%m%d}-{i + 1:04d}", warehouse=wh, doc_date=today,
            status=ReturnNoteStatus.CLOSED,
        )
        ReturnEntry.objects.create(return_note=note, status=ReturnStatus.POSTED)

    call_command("merge_return_notes_by_day", "--dry-run")
    assert ReturnNote.objects.count() == 2
```

- [ ] **Step 2: Chạy → FAIL** (`Unknown command`)

- [ ] **Step 3: Viết command**

```python
"""Gộp hồi tố phiếu trả hàng cũ (1 phiếu ↔ 1 đơn) thành phiếu NGÀY (1 phiếu / ngày / kho).

An toàn: chỉ đổi con trỏ ReturnEntry.return_note_id; movement trỏ entry nên tồn
kho không thể đổi. Vẫn đối soát trước/sau và abort nếu lệch.
"""
from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from apps.inventory.models import (
    ReturnEntry,
    ReturnNote,
    ReturnNoteStatus,
    StockItem,
)


def _stock_snapshot() -> dict[tuple[int, int, str], int]:
    return {
        (r["warehouse_id"], r["product_id"], r["state"]): r["total"]
        for r in StockItem.objects.values("warehouse_id", "product_id", "state")
        .annotate(total=Sum("quantity"))
    }


class Command(BaseCommand):
    help = "Gộp phiếu trả hàng cũ theo (ngày, kho) thành 1 phiếu ngày."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--date-from", default="")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        before = _stock_snapshot()

        notes = ReturnNote.objects.all()
        if opts["date_from"]:
            notes = notes.filter(doc_date__gte=opts["date_from"])

        groups: dict[tuple[str, int], list[ReturnNote]] = defaultdict(list)
        for note in notes.order_by("id"):
            groups[(note.doc_date, note.warehouse_id)].append(note)

        merged = 0
        with transaction.atomic():
            for (doc_date, warehouse_id), items in groups.items():
                if len(items) < 2 and items[0].code.startswith("PTH-"):
                    continue  # đã là phiếu ngày, bỏ qua
                target_code = f"PTH-{doc_date:%Y%m%d}-K{warehouse_id}"
                keep = items[0]
                keep.code = target_code
                keep.status = ReturnNoteStatus.CLOSED
                keep.save(update_fields=["code", "status"])
                drop_ids = [n.id for n in items[1:]]
                if drop_ids:
                    ReturnEntry.objects.filter(return_note_id__in=drop_ids).update(
                        return_note_id=keep.id
                    )
                    ReturnNote.objects.filter(id__in=drop_ids).delete()
                merged += 1
                self.stdout.write(
                    f"{target_code}: gộp {len(items)} phiếu → "
                    f"{keep.entries.count()} nhóm đơn"
                )

            after = _stock_snapshot()
            if before != after:
                raise RuntimeError(
                    "LỆCH TỒN sau khi gộp — rollback. "
                    f"before={len(before)} keys, after={len(after)} keys"
                )
            if dry:
                self.stdout.write(self.style.WARNING("--dry-run: rollback."))
                transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(f"Đã gộp {merged} phiếu ngày. Tồn không đổi.")
        )
```

**Lưu ý:** phiếu ngày mới nhận `status=closed` (lịch sử), trừ phiếu của **hôm nay** — nếu `doc_date == today` thì giữ `OPEN` để kho quét tiếp vào đúng phiếu đó. Thêm nhánh:
```python
                from apps.inventory.application.services.return_note_service import (
                    ReturnNoteService,
                )
                keep.status = (
                    ReturnNoteStatus.OPEN
                    if doc_date == ReturnNoteService.today_vn()
                    else ReturnNoteStatus.CLOSED
                )
```

- [ ] **Step 4: Chạy test → PASS** (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory/management/commands/merge_return_notes_by_day.py backend/apps/inventory/tests/test_merge_return_notes_by_day.py
git commit -m "feat(inventory): command to merge legacy return notes by day"
```

---

## Task 9: FE — types, queries, sinh lại schema

**Files:**
- Modify: `frontend/src/features/inventory/returns-types.ts`, `returns-queries.ts`, `ledger-types.ts`, `ledger-queries.ts`
- Delete: `frontend/src/features/inventory/ledger-grouping.ts` + `frontend/src/features/inventory/__tests__/ledger-grouping.test.ts` (grep tên chính xác)
- Modify: `frontend/src/api/schema.d.ts` (sinh lại)

**Interfaces:**
- Produces: `ReturnEntryRow` (`id`, `status`, `source_order`, `shipping_code`, `customer_name`, `note`, `lines[]`, `return_note: {id, code, doc_date, status, warehouse_name}`), `ReturnNoteDetail` (`id`, `code`, `doc_date`, `status`, `warehouse_name`, `entries: ReturnEntryRow[]`, `entry_count`, `qty_total`), `LedgerRow` return thêm `doc_date`, `entry_count`, `qty_total`, `cancelled_count`.
- Hooks: `useReturnNoteDetail(id)`, `useCloseReturnNote()`, `useCancelReturnNote()`, `useCancelReturnEntry()`; **gỡ** `useBulkPostReturns`.

- [ ] **Step 1: Sinh lại OpenAPI types**

```bash
cd frontend && npm run gen:api
```
(Backend phải chạy được — nếu script gọi server, dùng đúng cách repo đang làm; xem `package.json`.)

- [ ] **Step 2: Cập nhật types + queries**

`returns-queries.ts`: thêm

```ts
export function useReturnNoteDetail(id: number | null) {
  return useQuery({
    queryKey: ["inventory", "return-notes", id],
    queryFn: () =>
      fetchJson(`/api/inventory/return-notes/${id}`) as Promise<ReturnNoteDetail>,
    enabled: id != null,
  });
}

export function useCloseReturnNote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      fetchJson(`/api/inventory/return-notes/${id}/close`, { method: "POST" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["inventory", "ledger"] });
      void qc.invalidateQueries({ queryKey: ["inventory", "return-notes"] });
    },
  });
}

export function useCancelReturnNote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      fetchJson(`/api/inventory/return-notes/${id}/cancel`, { method: "POST" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["inventory", "ledger"] });
      void qc.invalidateQueries({ queryKey: ["inventory", "return-notes"] });
    },
  });
}

export function useCancelReturnEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (entryId: number) =>
      fetchJson(`/api/inventory/returns/${entryId}/cancel`, { method: "POST" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["inventory", "ledger"] });
      void qc.invalidateQueries({ queryKey: ["inventory", "return-notes"] });
    },
  });
}
```

Gỡ `useBulkPostReturns` + import của nó.

- [ ] **Step 3: Xoá `ledger-grouping.ts` + test**

```bash
git rm frontend/src/features/inventory/ledger-grouping.ts
git rm frontend/src/features/inventory/__tests__/ledger-grouping.test.ts
```

- [ ] **Step 4: `npm run typecheck`** — sẽ đỏ ở `StockDocumentsPage`/dialog (Task 10 sửa). Chấp nhận đỏ tạm, **không commit** cho tới hết Task 10.

---

## Task 10: FE — trang Phiếu kho (lọc ngày + phân trang) + dialog phiếu ngày

**Files:**
- Modify: `frontend/src/features/inventory/pages/StockDocumentsPage.tsx`
- Rewrite: `frontend/src/features/inventory/components/ReturnNoteDetailDialog.tsx`
- Modify: `frontend/src/features/inventory/pages/CreateReturnDialog.tsx`, `components/ReturnCard.tsx`, `components/ReturnTab.tsx`, `components/flow-shared.tsx`, `scan/scan-return-rows.ts`
- Test: `frontend/src/features/inventory/__tests__/ReturnNoteDetailDialog.test.tsx` (viết lại), `__tests__/stock-documents-date-filter.test.tsx` (mới)

**Interfaces:**
- Consumes: hooks Task 9; `DateFilter` từ `@/components/ui/date-filter` (props `value: DateFilterValue`, `onChange`, `inline`).

- [ ] **Step 1: Test lọc ngày (fail trước)** — `__tests__/stock-documents-date-filter.test.tsx`

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// mock useLedgerDocuments để bắt filters truyền vào
const useLedgerDocuments = vi.fn(() => ({ data: { items: [], total: 0 }, isLoading: false, refetch: vi.fn() }));
vi.mock("@/features/inventory/ledger-queries", () => ({ useLedgerDocuments }));

describe("StockDocumentsPage date filter", () => {
  it("truyền date_from/date_to vào query khi chọn khoảng ngày", async () => {
    // render page (dùng đúng wrapper QueryClient + permissions của test hiện có)
    // chọn preset "Hôm nay" trên DateFilter
    await waitFor(() => {
      const filters = useLedgerDocuments.mock.calls.at(-1)?.[0];
      expect(filters).toHaveProperty("date_from");
      expect(filters).toHaveProperty("date_to");
    });
  });
});
```

(Đọc `__tests__/ReturnNoteDetailDialog.test.tsx` hiện có để tái dùng wrapper render + mock permissions.)

- [ ] **Step 2: Chạy → FAIL** (`npx vitest run src/features/inventory/__tests__/stock-documents-date-filter.test.tsx`)

- [ ] **Step 3: Sửa `StockDocumentsPage.tsx`**

- State mới:
```tsx
const [dateFilter, setDateFilter] = useState<DateFilterValue>({ option: "this_month" });
const [offset, setOffset] = useState(0);
const LIMIT = 50;
```
- `filters` gộp thêm:
```tsx
  const filters: Record<string, string | string[]> = {
    ...(typeFilter ? { type: typeFilter } : {}),
    ...(statusFilter ? { status: statusFilter } : {}),
    ...(search ? { q: search } : {}),
    ...(dateFilter.from ? { date_from: dateFilter.from } : {}),
    ...(dateFilter.to ? { date_to: dateFilter.to } : {}),
    limit: String(LIMIT),
    offset: String(offset),
  };
```
  (`DateFilter` với `option !== "custom"` tự tính `from`/`to` — kiểm tra lại `date-filter.tsx` và bắt chước đúng cách `ShipmentsPage`/`DispatchHistoryPage` đang dùng.)
- Thanh lọc: chèn `<DateFilter value={dateFilter} onChange={(v) => { setDateFilter(v); setOffset(0); }} inline />` cạnh ô tìm kiếm.
- Phân trang: dưới bảng, nút Trước/Sau + "Hiển thị X–Y / total" (dùng `data.total`); đổi trang thì `setOffset`.
- Gỡ toàn bộ `expandedBatches`, `groupByBatch`, `postableBatchRows`, nút "Ghi sổ cả đợt" và import `ledger-grouping`.
- Dòng `kind === "return"` hiển thị: `code` · `warehouse_label` · `{entry_count} đơn` · `{qty_total} SP` · badge trạng thái (`open` → "Đang mở" xanh, `closed` → "Đã chốt" xám) + `cancelled_count > 0` → badge phụ "{n} đơn đã huỷ". Click dòng → mở `ReturnNoteDetailDialog` với `noteId`.
- Menu thao tác dòng return: **Xem** (mở dialog), **Chốt phiếu** (chỉ khi `status === "open"` và có `returns:approve`), **Huỷ phiếu** (`returns:approve`, `window.confirm` có đếm số đơn: `Huỷ cả phiếu ${code}? ${entry_count} đơn hoàn sẽ bị đảo tồn.`).

- [ ] **Step 4: Viết lại `ReturnNoteDetailDialog.tsx`**

Props: `{ noteId: number | null; onClose: () => void }`. Dùng `useReturnNoteDetail(noteId)`.
Nội dung:
- Header: mã phiếu, ngày (`doc_date`), kho, badge trạng thái, `entry_count` đơn / `qty_total` SP.
- Bảng entry: cột **Đơn** (`source_order_invoice_code` hoặc mã VĐ), **Mã vận đơn**, **Khách**, **SL** (tổng lines), **Trạng thái**, **Thao tác** → nút **Huỷ** (chỉ khi `status === "posted"` và có `returns:approve`, `window.confirm`).
- Mỗi entry bung được để xem lines (SKU / tên / SL / loại hoàn).
- Footer: **Chốt phiếu** (khi `open` + `returns:approve`), **Huỷ phiếu** (`returns:approve`).
- Dùng `FormDialogShell` nếu dialog là form-shell theo chuẩn repo — dialog này là **xem/thao tác**, giữ `DialogContent` thường (xem memory `dialog-form-shell-standard`).

- [ ] **Step 5: Sửa các chỗ đọc field cũ**

- `CreateReturnDialog.tsx`: bỏ chọn "Loại phiếu" cấp phiếu; mỗi dòng **bắt buộc** chọn loại hoàn (mặc định "Hàng hoàn"); payload gửi `lines[].return_type`. Sau khi tạo, toast hiện `Đã thêm vào phiếu ${res.return_note.code}`.
- `scan/scan-return-rows.ts` + toast quét: hiện `mã phiếu ngày + mã vận đơn` thay vì mã phiếu cũ.
- `ReturnCard.tsx`, `ReturnTab.tsx`, `flow-shared.tsx`: field `code` từ API lịch sử giờ là mã phiếu ngày (Task 5 giữ nguyên key) → chỉ cần chỉnh nhãn cột nếu đang ghi "Mã phiếu trả".

- [ ] **Step 6: Chạy test FE**

```bash
cd frontend && npm run typecheck && npx vitest run src/features/inventory
```
Expected: typecheck sạch; test inventory pass (baseline fail đã biết: xem memory `known-failing-baseline-tests`).

- [ ] **Step 7: Commit**

```bash
git add frontend
git commit -m "feat(inventory): daily return note UI with date range filter and pagination"
```

---

## Task 11: Chốt cuối — full suite, lint, cập nhật docs

**Files:**
- Modify: `docs/erp-inventory-*.md` (mục nói "1 phiếu ↔ 1 đơn" — grep `1 phiếu`), `docs/superpowers/specs/2026-07-11-return-note-daily-batch-design.md` (đánh dấu đã triển khai)

- [ ] **Step 1: Full suite backend**

Run: `<PATHS>` = `apps/` (dùng `-e POSTGRES_DB=desi_rnd_full` để có DB sạch).
So với baseline 6 fail pre-existing trong memory `running-backend-tests` — **không** được thêm fail mới.

- [ ] **Step 2: Ruff + FE build**

```bash
uv run --no-project --with ruff ruff check backend/apps/inventory
cd frontend && npm run typecheck && npm run build && npx vitest run
```

- [ ] **Step 3: Cập nhật doc kiến trúc**

Trong `docs/` , sửa mọi mô tả "mỗi đơn hoàn = 1 phiếu" thành mô hình phiếu ngày + entry; ghi rõ 2 bước migration prod (migrate `0022` → chạy `merge_return_notes_by_day --dry-run` → chạy thật sau khi backup).

- [ ] **Step 4: Kiểm tra conflict với master**

```bash
git fetch origin && git merge origin/master
```
Resolve nếu có, chạy lại suite inventory.

- [ ] **Step 5: Commit + push**

```bash
git add -A && git commit -m "docs: update inventory docs for daily return notes"
git push -u origin feat/return-note-daily-batch
```
Gửi link tạo MR cho user.

---

## Runbook deploy prod (người thực hiện: CTO)

1. Backup DB.
2. Deploy code + `python manage.py migrate inventory` (chạy `0022`) — sau bước này hệ thống **đã chạy đúng**, phiếu cũ vẫn dạng 1-đơn.
3. `python manage.py merge_return_notes_by_day --dry-run` → đọc log, xác nhận "Tồn không đổi".
4. `python manage.py merge_return_notes_by_day` → chạy thật.
5. Đối chiếu 1 phiếu ngày bất kỳ trên UI với KiotViet/kho thực tế.
