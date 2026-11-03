# Order Milestone Snapshots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chụp bất biến thông tin đơn tại 2 mốc (`created` lúc ingest, `invoiced` lúc gán hóa đơn lần đầu) vào bảng riêng — chấm dứt backfill suy ngược.

**Architecture:** Bảng `OrderMilestoneSnapshot` unique (order, milestone), service 2 hàm `capture_*` dùng `get_or_create` (idempotent, lỗi không vỡ luồng chính), hook tại ingest/clone và 4 điểm gán invoice_code phía kiotviet, API đọc theo đơn mirror `get_order_journey_events`, seed command một lần cho đơn cũ. Spec: `docs/superpowers/specs/2026-08-04-order-milestone-snapshots-design.md`.

**Tech Stack:** Django 5 + DRF + pytest.

## Global Constraints

- Worktree `.claude/worktrees/feat-milestone-snapshots`, nhánh `feat/order-milestone-snapshots`.
- Test bằng Docker (lệnh chuẩn của repo, POSTGRES_DB riêng mỗi lần chạy); ruff bắt buộc.
- KHÔNG dùng `QuerySet.iterator()` (PgBouncer); keyset pagination theo id.
- Lỗi ghi snapshot chỉ log error, không raise xuyên luồng nghiệp vụ.
- Commit tiếng Anh, không Co-Authored-By; push kèm `-o merge_request.create`.

---

### Task 1: Model `OrderMilestoneSnapshot` + migration 0115

**Files:** Modify `backend/apps/orders/models.py` (sau class `OrderJourneyEvent` hoặc cuối nhóm model phụ của Order); Create `backend/apps/orders/migrations/0115_order_milestone_snapshot.py`.

**Produces:** `OrderMilestone` (TextChoices `CREATED="created"`, `INVOICED="invoiced"`), model `OrderMilestoneSnapshot` (fields như spec §3.1, related_name `milestone_snapshots`, unique `uniq_order_milestone`).

- [ ] Thêm model đúng spec §3.1 vào models.py; viết migration tay (dependencies `0114_order_landing_confirmed_amount`) với `CreateModel` + `AddConstraint`.
- [ ] Chạy `python manage.py makemigrations --check --dry-run` + 1 test bất kỳ với `--create-db` → "No changes detected" + PASS.
- [ ] Commit `feat: add order milestone snapshot model`.

### Task 2: Service capture + hook `created` (ingest, clone)

**Files:** Create `backend/apps/orders/application/services/milestone_snapshot_service.py`; Modify `order_service_commands.py` (`create_order_from_landing`), `order_service_helpers.py` (`duplicate_order`); Test: append `backend/apps/orders/tests/test_milestone_snapshots.py`.

**Produces:**
```python
def capture_created_snapshot(order, *, amount_source: str) -> None
def capture_invoice_snapshot(order) -> None
```

- [ ] Test fail trước (file mới `test_milestone_snapshots.py`):

```python
"""Snapshot mốc vòng đời đơn (spec 2026-08-04-order-milestone-snapshots)."""
from decimal import Decimal

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.orders.application.services.milestone_snapshot_service import (
    capture_invoice_snapshot,
)
from apps.orders.models import Order, OrderMilestone, OrderMilestoneSnapshot, Product
from apps.orders.services import OrderService


class CreatedSnapshotTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="fb-ms", name="FB MS", owner="FB MS")

    def _ingest(self, phone, **extra):
        return OrderService.create_order_from_landing(
            {"channel_id": self.channel.id, "phone": phone, **extra}
        )

    def test_ingest_writes_created_snapshot_with_price_book_source(self):
        Product.objects.create(name="Bồn ngâm chân", sku="BNC11", price="1500000")
        order = self._ingest("0912000001", product_id="BNC11", quantity=2)
        snap = order.milestone_snapshots.get(milestone=OrderMilestone.CREATED)
        self.assertEqual(snap.total_amount, Decimal("3000000"))
        self.assertEqual(snap.quantity, 2)
        self.assertEqual(snap.sku, "BNC11")
        self.assertEqual(snap.amount_source, "price_book")

    def test_ingest_text_and_payload_sources(self):
        o1 = self._ingest("0912000002", product="Mua 1 Thảm: 1.290.000 VNĐ")
        self.assertEqual(
            o1.milestone_snapshots.get(milestone="created").amount_source, "text"
        )
        o2 = self._ingest("0912000003", total_amount=2550000)
        self.assertEqual(
            o2.milestone_snapshots.get(milestone="created").amount_source, "payload"
        )
        o3 = self._ingest("0912000004")
        self.assertEqual(
            o3.milestone_snapshots.get(milestone="created").amount_source, "none"
        )

    def test_clone_copies_created_snapshot_amounts(self):
        Product.objects.create(name="Tủ", sku="SQA11", price="1500000")
        order = self._ingest("0912000005", product_id="SQA11", quantity=1)
        clone = OrderService.duplicate_order(order, None)
        snap = clone.milestone_snapshots.get(milestone="created")
        self.assertEqual(snap.total_amount, Decimal("1500000"))
        self.assertEqual(snap.amount_source, "cloned")


class InvoiceSnapshotTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="fb-ms2", name="FB MS2")

    def _order(self, **kwargs):
        from apps.customers.models import Customer

        customer = Customer.objects.create(
            full_name="KH", phone_raw="0912000010", phone_normalized="0912000010"
        )
        return Order.objects.create(
            channel=self.channel, customer=customer, name="X", phone="0912000010", **kwargs
        )

    def test_capture_invoice_snapshot_freezes_current_values(self):
        order = self._order(
            total_amount=Decimal("4200000"),
            total_after_discount=Decimal("4200000"),
            quantity=3,
            sku="MSC06",
            invoice_code="HD-1",
        )
        capture_invoice_snapshot(order)
        order.total_after_discount = Decimal("9999999")
        order.save()
        snap = order.milestone_snapshots.get(milestone=OrderMilestone.INVOICED)
        self.assertEqual(snap.total_after_discount, Decimal("4200000"))
        self.assertEqual(snap.amount_source, "invoice")

    def test_capture_invoice_snapshot_idempotent(self):
        order = self._order(total_after_discount=Decimal("1000"), invoice_code="HD-2")
        capture_invoice_snapshot(order)
        order.total_after_discount = Decimal("2000")
        order.save()
        capture_invoice_snapshot(order)
        self.assertEqual(
            OrderMilestoneSnapshot.objects.filter(order=order).count(), 1
        )
        self.assertEqual(
            order.milestone_snapshots.get(milestone="invoiced").total_after_discount,
            Decimal("1000"),
        )
```

- [ ] Implement service (get_or_create, try/except log); trong `create_order_from_landing` xác định `amount_source`: `"payload"` nếu explicit_total_amount, `"text"` nếu derive_total_amount(product_raw) > 0 (trước khi bị items_total ghi đè), `"price_book"` nếu dùng items_total, còn lại `"none"`; gọi `capture_created_snapshot(order, amount_source=...)` sau create. Trong `duplicate_order`: sau create, copy dòng created của đơn gốc (nếu có) với amount_source="cloned" và số của DÒNG GỐC (không phải số hiện tại của đơn gốc).
- [ ] Chạy test → PASS; chạy kèm `test_ingest_api.py` chống hồi quy.
- [ ] Commit `feat: capture created milestone snapshot at ingest`.

### Task 3: Hook `invoiced` tại 4 điểm kiotviet

**Files:** Modify `apps/kiotviet/infrastructure/services/service_actions.py` (~98, ~231), `_import_service_handlers_mixin.py` (~326), `_import_service_upsert_mixin.py` (~382).

**Consumes:** `capture_invoice_snapshot(order)` (Task 2).

- [ ] Tại mỗi điểm, sau khi `order.invoice_code` gán + save/update_fields hoàn tất trong luồng đó, thêm:

```python
if not previous_invoice_code and current_invoice_code:
    from apps.orders.application.services.milestone_snapshot_service import (
        capture_invoice_snapshot,
    )
    capture_invoice_snapshot(order)
```

(biến điều kiện theo tên local từng file: handlers/upsert mixin dùng biến invoice_code mới gán và giá trị cũ trước gán — đọc context tại chỗ; nguyên tắc: CHỈ khi trước rỗng và sau khác rỗng.)
- [ ] Chạy regression các suite kiotviet liên quan: `apps/kiotviet/tests/test_invoice_webhook.py apps/kiotviet/tests/test_import_kiotviet_invoices_command.py apps/kiotviet/tests/test_services_wiring.py` + `apps/orders/tests/test_milestone_snapshots.py` → PASS.
- [ ] Commit `feat: capture invoiced milestone snapshot on invoice attach`.

### Task 4: API đối soát

**Files:** Create view `get_order_milestone_snapshots` trong `backend/apps/orders/transport/views/order_views_impl/contact_log_view.py` (cạnh `get_order_journey_events`, cùng pattern `scoped_order_or_error(request, order_id, INLINE_SCOPE_SECTIONS)`); serializer inline (ModelSerializer fields milestone/total_amount/total_after_discount/quantity/sku/amount_source/captured_at, tiền ép str); Modify `urls.py` thêm `path("<int:order_id>/milestone-snapshots", ...)`; export qua `order_views_impl/__init__.py` + `views.py` nếu cần (theo cách journey-events đang export). Test: thêm vào `test_milestone_snapshots.py`.

- [ ] Test: admin gọi trả đủ 2 dòng đúng thứ tự milestone; user ngoài scope → status != 200.
- [ ] Implement; chạy test PASS.
- [ ] Regenerate schema: `python manage.py spectacular --file schema.yaml` trong container + `npm run gen:api` (frontend chính worktree này không có node_modules — chạy `npm ci --prefer-offline` trước).
- [ ] Commit `feat: add order milestone snapshots endpoint`.

### Task 5: Seed command

**Files:** Create `backend/apps/orders/management/commands/seed_order_milestone_snapshots.py`; Test thêm vào `test_milestone_snapshots.py`.

- [ ] Test: đơn ingest có landing_confirmed_amount>0 → seed created (amount_source="seeded", total=landing_confirmed_amount); đơn có invoice_code → seed invoiced (total=total_after_discount hiện tại); dry-run không ghi; chạy 2 lần không nhân đôi; đơn đã có snapshot thật không bị đè.
- [ ] Implement: scope ingest tái dùng điều kiện `Q(order_source=LANDING) | Q(landing_ingest_traces__status=CREATED)` + distinct; keyset theo id; `get_or_create`; stats + DRY-RUN mặc định như `backfill_landing_confirmed_amount`.
- [ ] Chạy test PASS; commit `feat: add milestone snapshot seed command`.

### Task 6: Verify + push + MR

- [ ] Ruff toàn backend; suite: `test_milestone_snapshots.py test_ingest_api.py test_backfill_landing_confirmed_amount.py test_digital_and_sale_staff_reports.py` + 3 file kiotviet ở Task 3 → PASS toàn bộ.
- [ ] Push `-o merge_request.create`, gửi link MR + hướng dẫn chạy seed (dry-run → --apply giờ thấp điểm, rebuild cả web lẫn worker).
