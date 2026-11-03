# Lead Auto-Assignment + Transfer by Sheet — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tự động gán nhân viên (`Order.staff`) cho đơn ingest theo tỉ lệ % cấu hình theo ngày tách riêng theo 3 sheet (Landing/Messenger/Tư vấn), chỉ khi đơn chưa có staff; kèm công cụ xem tải & chuyển đơn giữa nhân viên.

**Architecture:** Bảng `lead_assignment_shares (date, sheet, user, weight, assigned_count)`. Helper `sheet_rules.classify_sheet` phân loại đơn → sheet. `LeadAssignmentService.pick_staff_for(date, sheet)` dùng largest-remainder + `select_for_update` (atomic). Móc vào 2 điểm ingest. Workload/transfer thao tác trên đơn "chưa có trạng thái sale" theo sheet, FIFO. Admin cấu hình qua trang Settings dạng ma trận (NV × sheet).

**Tech Stack:** Django 5 / DRF (Python 3.12), PostgreSQL, React SPA (Vite) + TypeScript + shadcn/ui, react-router.

---

## Spec

Xem `docs/superpowers/specs/2026-06-13-lead-auto-assignment-ingest-design.md`.

## File Structure

**Backend (`backend/`):**
- `apps/orders/sheet_rules.py` — MODIFY: thêm `SHEET_CHOICES`, `classify_sheet()`, `sheet_q()`.
- `apps/orders/models.py` — MODIFY: thêm model `LeadAssignmentShare` (append cuối file).
- `apps/orders/migrations/0083_lead_assignment_share.py` — CREATE (số tiếp theo sau `0082`).
- `apps/orders/application/services/lead_assignment_service.py` — CREATE: `LeadAssignmentService`.
- `apps/orders/application/services/order_service_commands.py` — MODIFY: hook trong `create_order_from_landing`.
- `apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py` — MODIFY: hook trong `lark_ingest`.
- `apps/orders/transport/views/order_views_impl/lead_assignment_view.py` — CREATE: API shares/workload/transfer.
- `apps/orders/transport/views/order_views_impl/__init__.py` — MODIFY: re-export view mới.
- `apps/orders/urls.py` — MODIFY: routes + import.
- `apps/users/models.py` — MODIFY: thêm `"lead-assignment"` vào `VALID_SECTIONS`.
- `apps/orders/tests/test_sheet_rules_classify.py` — CREATE.
- `apps/orders/tests/test_lead_assignment_service.py` — CREATE.
- `apps/orders/tests/test_lead_assignment_api.py` — CREATE (gồm ingest hook + API).

**Frontend (`frontend/`):**
- `src/lib/api/lead-assignment.ts` — CREATE: API client.
- `src/lib/routes.ts` — MODIFY: section `"lead-assignment"` + path.
- `src/app/modules.ts` — MODIFY: nav entry trong group `settings`.
- `src/app/routed-pages.tsx` — MODIFY: lazy import page.
- `src/app/routes.tsx` — MODIFY: route `settings/lead-assignment`.
- `src/pages/LeadAssignmentPage.tsx` — CREATE: ma trận cấu hình + khối chuyển đơn.
- `src/lib/api/__tests__/lead-assignment.test.ts` — CREATE.

## Cách chạy test (theo memory project)

Backend (ephemeral container, KHÔNG dùng container prod `desi-web`):
```bash
cd /home/desi/desi-order
docker inspect desi-web -f '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E '^[A-Z_]+=' | grep -vE '^PATH=|^LANG=|^GPG|^PYTHON' > /tmp/web.env
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c 'mkdir -p /tmp/prom_multiproc && rm -f /tmp/prom_multiproc/* && python -m pytest <PATHS> --reuse-db -q'
```
Ruff (host): `uv run --no-project --with ruff ruff check <files>`
Frontend: `cd frontend && npx vitest run <path>` ; build: `npm run build`.

---

## Task 1: Sheet classification helpers + test

**Files:**
- Modify: `backend/apps/orders/sheet_rules.py`
- Test: `backend/apps/orders/tests/test_sheet_rules_classify.py`

- [ ] **Step 1: Viết test thất bại**

Create `backend/apps/orders/tests/test_sheet_rules_classify.py`:

```python
from django.test import SimpleTestCase

from apps.orders.sheet_rules import SHEET_CHOICES, classify_sheet


class ClassifySheetTests(SimpleTestCase):
    def test_complete_messenger_is_sheet2(self):
        self.assertEqual(
            classify_sheet(order_source="messenger", name="A", phone="09", raw_address="HN"),
            "sheet2",
        )

    def test_complete_landing_is_sheet1(self):
        self.assertEqual(
            classify_sheet(order_source="landing", name="A", phone="09", address="HN"),
            "sheet1",
        )

    def test_incomplete_is_sheet3(self):
        self.assertEqual(
            classify_sheet(order_source="landing", name="A", phone="09"),
            "sheet3",
        )
        self.assertEqual(
            classify_sheet(order_source="messenger", name="A", phone="09"),
            "sheet3",
        )

    def test_koc_returns_none(self):
        self.assertIsNone(
            classify_sheet(order_source="koc", name="A", phone="09", address="HN")
        )

    def test_choices_constant(self):
        self.assertEqual(SHEET_CHOICES, ("sheet1", "sheet2", "sheet3"))
```

- [ ] **Step 2: Chạy test cho chắc nó fail**

Run:
```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders/tests/test_sheet_rules_classify.py --reuse-db -q'
```
Expected: FAIL — `ImportError: cannot import name 'classify_sheet'`.

- [ ] **Step 3: Thêm helper vào `sheet_rules.py`**

Append vào cuối `backend/apps/orders/sheet_rules.py`:

```python
SHEET_CHOICES = ("sheet1", "sheet2", "sheet3")

SHEET_LABELS = {
    "sheet1": "Landing xác nhận đơn",
    "sheet2": "Messenger xác nhận đơn",
    "sheet3": "Tư vấn",
}


def classify_sheet(*, order_source, name, phone, raw_address="", address="") -> str | None:
    """Phân loại 1 đơn vào sheet1/sheet2/sheet3, hoặc None nếu không thuộc 3 sheet.

    Dùng chung cho auto-assign (lúc ingest) và workload/transfer. Khớp đúng quy tắc
    phân Sheet 1/2 ở ``order_list_view`` và Sheet 3.
    """
    complete = is_info_complete(name=name, phone=phone, raw_address=raw_address, address=address)
    src = (order_source or "").strip().lower()
    if complete and src in SHEET2_SOURCES:
        return "sheet2"
    if complete and src in SHEET1_SOURCES:
        return "sheet1"
    if (not complete) and src in SHEET3_SOURCES:
        return "sheet3"
    return None


def sheet_q(sheet: str):
    """Trả về ``django.db.models.Q`` lọc Order thuộc ``sheet`` (mirror ``classify_sheet``)."""
    from django.db.models import Q

    complete_q = (
        ~Q(name="") & ~Q(phone="") & (~Q(raw_address="") | ~Q(address=""))
    )
    if sheet == "sheet1":
        return Q(order_source__in=SHEET1_SOURCES) & complete_q
    if sheet == "sheet2":
        return Q(order_source__in=SHEET2_SOURCES) & complete_q
    if sheet == "sheet3":
        return Q(order_source__in=SHEET3_SOURCES) & ~complete_q
    raise ValueError(f"Sheet không hợp lệ: {sheet}")
```

- [ ] **Step 4: Chạy test cho chắc nó pass**

Run lệnh ở Step 2. Expected: PASS (5 passed).

- [ ] **Step 5: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/sheet_rules.py backend/apps/orders/tests/test_sheet_rules_classify.py
git add backend/apps/orders/sheet_rules.py backend/apps/orders/tests/test_sheet_rules_classify.py
git commit -m "feat(orders): add sheet classification helpers"
```

---

## Task 2: Model `LeadAssignmentShare` + migration

**Files:**
- Modify: `backend/apps/orders/models.py` (append cuối file)
- Create: `backend/apps/orders/migrations/0083_lead_assignment_share.py`

- [ ] **Step 1: Thêm model vào cuối `models.py`**

Append vào cuối `backend/apps/orders/models.py`:

```python
class LeadAssignmentShare(models.Model):
    """Cấu hình tỉ lệ chia đơn (lead) cho từng nhân viên theo (NGÀY, SHEET).

    Mỗi dòng = (ngày, sheet, nhân viên, trọng số). Không có dòng nào cho một
    (ngày, sheet) ⇒ luồng đó KHÔNG auto-chia ngày đó. ``assigned_count`` là bộ
    đếm số đơn đã chia trong (ngày, sheet), dùng cho largest-remainder.
    """

    SHEET_CHOICES = [
        ("sheet1", "Landing xác nhận đơn"),
        ("sheet2", "Messenger xác nhận đơn"),
        ("sheet3", "Tư vấn"),
    ]

    date = models.DateField()
    sheet = models.CharField(max_length=16, choices=SHEET_CHOICES)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="lead_assignment_shares",
    )
    weight = models.PositiveSmallIntegerField()
    assigned_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "lead_assignment_shares"
        unique_together = ("date", "sheet", "user")
        indexes = [models.Index(fields=["date", "sheet"], name="lead_assign_date_sheet_idx")]

    def __str__(self):
        return f"{self.date} {self.sheet} u={self.user_id} w={self.weight} n={self.assigned_count}"
```

> `settings` đã được import sẵn trong `models.py`.

- [ ] **Step 2: Sinh migration**

Run:
```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  python manage.py makemigrations orders --name lead_assignment_share
```
Expected: tạo `apps/orders/migrations/0083_lead_assignment_share.py` với `CreateModel`.

- [ ] **Step 3: Apply migration**

Run:
```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -v "$(pwd)/backend:/app" -w /app desi-order-web python manage.py migrate orders
```
Expected: `Applying orders.0083_lead_assignment_share... OK`

- [ ] **Step 4: Commit**

```bash
git add backend/apps/orders/models.py backend/apps/orders/migrations/0083_lead_assignment_share.py
git commit -m "feat(orders): add LeadAssignmentShare model (date, sheet, user)"
```

---

## Task 3: `LeadAssignmentService` picker + assign + unit test

**Files:**
- Create: `backend/apps/orders/application/services/lead_assignment_service.py`
- Test: `backend/apps/orders/tests/test_lead_assignment_service.py`

- [ ] **Step 1: Viết test thất bại**

Create `backend/apps/orders/tests/test_lead_assignment_service.py`:

```python
from collections import Counter

from django.test import TestCase
from django.utils import timezone

from apps.orders.application.services.lead_assignment_service import LeadAssignmentService
from apps.orders.models import LeadAssignmentShare
from apps.users.models import User


class PickStaffTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.a = User.objects.create(username="sale_a")
        self.b = User.objects.create(username="sale_b")
        self.c = User.objects.create(username="sale_c")

    def test_empty_returns_none(self):
        self.assertIsNone(LeadAssignmentService.pick_staff_for(self.today, "sheet1"))

    def test_distribution_follows_weights_per_sheet(self):
        LeadAssignmentShare.objects.create(date=self.today, sheet="sheet1", user=self.a, weight=5)
        LeadAssignmentShare.objects.create(date=self.today, sheet="sheet1", user=self.b, weight=3)
        LeadAssignmentShare.objects.create(date=self.today, sheet="sheet1", user=self.c, weight=2)
        # sheet2 cấu hình khác → không ảnh hưởng sheet1.
        LeadAssignmentShare.objects.create(date=self.today, sheet="sheet2", user=self.a, weight=1)

        counts = Counter()
        for _ in range(100):
            counts[LeadAssignmentService.pick_staff_for(self.today, "sheet1").username] += 1
        self.assertEqual(counts["sale_a"], 50)
        self.assertEqual(counts["sale_b"], 30)
        self.assertEqual(counts["sale_c"], 20)
        # sheet2 vẫn nguyên (0 đơn đã chia cho sheet2 ngoài vòng trên).
        s2 = LeadAssignmentShare.objects.get(date=self.today, sheet="sheet2", user=self.a)
        self.assertEqual(s2.assigned_count, 0)
```

- [ ] **Step 2: Chạy test cho chắc nó fail**

Run:
```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders/tests/test_lead_assignment_service.py --reuse-db -q'
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Viết service**

Create `backend/apps/orders/application/services/lead_assignment_service.py`:

```python
import logging

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.orders.models import LeadAssignmentShare, Order
from apps.orders.sheet_rules import classify_sheet, sheet_q

logger = logging.getLogger(__name__)

# Đơn "chưa có trạng thái sale": assessment null hoặc sales_status_code rỗng.
_NO_SALES_STATUS_Q = (
    Q(assessment__isnull=True)
    | Q(assessment__sales_status_code__isnull=True)
    | Q(assessment__sales_status_code="")
)


class LeadAssignmentService:
    """Chia đơn tự động theo (ngày, sheet) + công cụ xem tải & chuyển đơn."""

    @staticmethod
    def pick_staff_for(date, sheet):
        """Chọn 1 nhân viên cho (date, sheet) theo largest-remainder, tăng bộ đếm.

        Trả None nếu chưa cấu hình. ``select_for_update`` serialize ingest đồng thời.
        """
        with transaction.atomic():
            shares = list(
                LeadAssignmentShare.objects.select_for_update()
                .filter(date=date, sheet=sheet, weight__gt=0)
                .order_by("id")
            )
            if not shares:
                return None
            total_weight = sum(s.weight for s in shares)
            total_assigned = sum(s.assigned_count for s in shares) + 1

            def deficit(s):
                target = s.weight / total_weight * total_assigned
                return (target - s.assigned_count, s.weight, -s.id)

            best = max(shares, key=deficit)
            best.assigned_count += 1
            best.save(update_fields=["assigned_count", "updated_at"])
            return best.user

    @staticmethod
    def assign_if_unassigned(order) -> None:
        """Gán staff cho order nếu đang trống. No-op nếu đã có staff hoặc không thuộc sheet."""
        if order.staff_id is not None:
            return
        sheet = classify_sheet(
            order_source=order.order_source,
            name=order.name,
            phone=order.phone,
            raw_address=order.raw_address,
            address=order.address,
        )
        if sheet is None:
            return
        user = LeadAssignmentService.pick_staff_for(timezone.localdate(), sheet)
        if user is None:
            return
        order.staff = user
        order.save(update_fields=["staff", "updated_at"])
        logger.info("Lead auto-assigned: order_id=%s sheet=%s staff_id=%s", order.id, sheet, user.id)

    @staticmethod
    def _pending_base(sheet):
        """Queryset đơn thuộc sheet, chưa có trạng thái sale, chưa xoá."""
        return (
            Order.objects.filter(sheet_q(sheet))
            .filter(_NO_SALES_STATUS_Q)
            .filter(deleted_at__isnull=True)
        )

    @staticmethod
    def workload(sheet):
        """Số đơn đang chờ (no sales_status) theo từng nhân viên trong sheet."""
        rows = (
            LeadAssignmentService._pending_base(sheet)
            .filter(staff__isnull=False)
            .values("staff_id")
            .annotate(pending_count=Count("id"))
        )
        return {r["staff_id"]: r["pending_count"] for r in rows}

    @staticmethod
    def transfer(sheet, from_user_id, to_user_id, count) -> int:
        """Chuyển N đơn cũ nhất (FIFO) của from_user trong sheet sang to_user.

        Chỉ đơn chưa có trạng thái sale. KHÔNG sửa assigned_count.
        """
        if from_user_id == to_user_id or count < 1:
            return 0
        with transaction.atomic():
            ids = list(
                LeadAssignmentService._pending_base(sheet)
                .filter(staff_id=from_user_id)
                .order_by("created_at", "id")
                .select_for_update()
                .values_list("id", flat=True)[:count]
            )
            if not ids:
                return 0
            Order.objects.filter(id__in=ids).update(staff_id=to_user_id, updated_at=timezone.now())
            return len(ids)
```

- [ ] **Step 4: Chạy test cho chắc nó pass**

Run lệnh ở Step 2. Expected: PASS.

- [ ] **Step 5: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/application/services/lead_assignment_service.py backend/apps/orders/tests/test_lead_assignment_service.py
git add backend/apps/orders/application/services/lead_assignment_service.py backend/apps/orders/tests/test_lead_assignment_service.py
git commit -m "feat(orders): LeadAssignmentService picker, workload, transfer"
```

---

## Task 4: Móc hook vào 2 luồng ingest + test

**Files:**
- Modify: `backend/apps/orders/application/services/order_service_commands.py`
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py`
- Test: `backend/apps/orders/tests/test_lead_assignment_api.py` (phần ingest)

- [ ] **Step 1: Viết test thất bại (ingest gán staff theo sheet)**

Create `backend/apps/orders/tests/test_lead_assignment_api.py`:

```python
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks import process_landing_ingest
from apps.orders.models import LeadAssignmentShare, Order
from apps.users.models import User


def _ingest_headers():
    headers = {}
    if settings.ORDERS_INGEST_API_TOKEN:
        headers["HTTP_X_API_TOKEN"] = settings.ORDERS_INGEST_API_TOKEN
    return headers


class LandingIngestAutoAssignTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(code="fb-team", name="FB Team", owner="FB Team")
        self.sale = User.objects.create(username="sale_x")

    def _post_ingest(self, phone, address):
        payload = {
            "channel_id": "fb-team",
            "name": "Nguyen Van A",
            "phone": phone,
            "address": address,
            "order_source": "landing",
        }

        def _run_task(_task_name, **kwargs):
            process_landing_ingest(payload=kwargs["payload"], trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        with patch(
            "apps.orders.transport.views.order_views_impl.order_ingest_product_dashboard_view.dispatch",
            side_effect=_run_task,
        ):
            return self.client.post("/api/orders/ingest", payload, format="json", **_ingest_headers())

    def test_complete_landing_assigns_sheet1_staff(self):
        LeadAssignmentShare.objects.create(
            date=timezone.localdate(), sheet="sheet1", user=self.sale, weight=1
        )
        resp = self._post_ingest("0909123456", "1 Nguyen Trai, Q1")
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.get(phone="0909123456")
        self.assertEqual(order.staff_id, self.sale.id)

    def test_incomplete_uses_sheet3_not_sheet1(self):
        # Cấu hình chỉ sheet1; đơn thiếu địa chỉ → sheet3 → không có pool → để trống.
        LeadAssignmentShare.objects.create(
            date=timezone.localdate(), sheet="sheet1", user=self.sale, weight=1
        )
        resp = self._post_ingest("0909123457", "")
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.get(phone="0909123457")
        self.assertIsNone(order.staff_id)

    def test_no_config_leaves_null(self):
        resp = self._post_ingest("0909123458", "1 Nguyen Trai, Q1")
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.get(phone="0909123458")
        self.assertIsNone(order.staff_id)
```

- [ ] **Step 2: Chạy test cho chắc nó fail**

Run:
```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders/tests/test_lead_assignment_api.py::LandingIngestAutoAssignTests --reuse-db -q'
```
Expected: FAIL — `test_complete_landing_assigns_sheet1_staff` (staff_id None).

- [ ] **Step 3: Móc hook vào `create_order_from_landing`**

Trong `backend/apps/orders/application/services/order_service_commands.py`, trong `create_order_from_landing`, định vị block `OrderServiceCommands.push_event(` (khoảng dòng 240) và chèn NGAY TRƯỚC nó:

```python
        from apps.orders.application.services.lead_assignment_service import LeadAssignmentService

        LeadAssignmentService.assign_if_unassigned(order)

```

> Import cục bộ trong hàm để tránh vòng import (giống `LeadFilterService` trong file).

- [ ] **Step 4: Móc hook vào `lark_ingest`**

Trong `backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py`, trong `lark_ingest`, định vị block `LeadFilterService.register_claim(` và chèn NGAY TRƯỚC nó:

```python
    from apps.orders.application.services.lead_assignment_service import LeadAssignmentService

    LeadAssignmentService.assign_if_unassigned(order)

```

- [ ] **Step 5: Thêm test lark không ghi đè staff đã có**

Thêm vào cuối `test_lead_assignment_api.py`:

```python
class LarkIngestAutoAssignTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(code="lark-ch", name="Lark Ch", owner="Lark Ch")
        self.explicit = User.objects.create(username="explicit_sale")
        self.pool = User.objects.create(username="pool_sale")
        LeadAssignmentShare.objects.create(
            date=timezone.localdate(), sheet="sheet1", user=self.pool, weight=1
        )

    def _post_lark(self, body):
        return self.client.post("/api/orders/lark-ingest", body, format="json", **_ingest_headers())

    def test_lark_respects_explicit_staff_username(self):
        body = {
            "channel_code": "lark-ch",
            "name": "Tran B",
            "phone": "0911222333",
            "address": "2 Le Loi",
            "order_source": "landing",
            "staff_username": "explicit_sale",
        }
        resp = self._post_lark(body)
        self.assertEqual(resp.status_code, 200, resp.content)
        order = Order.objects.get(phone="0911222333")
        self.assertEqual(order.staff_id, self.explicit.id)

    def test_lark_auto_assigns_when_no_staff_username(self):
        body = {
            "channel_code": "lark-ch",
            "name": "Tran C",
            "phone": "0911222334",
            "address": "3 Le Loi",
            "order_source": "landing",
        }
        resp = self._post_lark(body)
        self.assertEqual(resp.status_code, 200, resp.content)
        order = Order.objects.get(phone="0911222334")
        self.assertEqual(order.staff_id, self.pool.id)
```

> Nếu `lark-ingest` báo thiếu field bắt buộc, điều chỉnh body theo lỗi validation (xem `build_landing_payload_from_fields`); mục tiêu test là so sánh `staff_id`.

- [ ] **Step 6: Chạy lại toàn file**

Run:
```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders/tests/test_lead_assignment_api.py --reuse-db -q'
```
Expected: PASS (5 passed).

- [ ] **Step 7: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/application/services/order_service_commands.py backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py backend/apps/orders/tests/test_lead_assignment_api.py
git add backend/apps/orders/application/services/order_service_commands.py backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py backend/apps/orders/tests/test_lead_assignment_api.py
git commit -m "feat(orders): auto-assign staff by sheet at landing & lark ingest"
```

---

## Task 5: API view (shares matrix + workload + transfer) + section + test

**Files:**
- Modify: `backend/apps/users/models.py` (thêm `"lead-assignment"` vào `VALID_SECTIONS`)
- Create: `backend/apps/orders/transport/views/order_views_impl/lead_assignment_view.py`
- Modify: `backend/apps/orders/transport/views/order_views_impl/__init__.py`
- Modify: `backend/apps/orders/urls.py`
- Test: `backend/apps/orders/tests/test_lead_assignment_api.py` (thêm class API)

- [ ] **Step 1: Thêm section vào `VALID_SECTIONS`**

Trong `backend/apps/users/models.py`, list `VALID_SECTIONS`, thêm sau `"users",`:

```python
    "lead-assignment",
```

- [ ] **Step 2: Viết test API thất bại**

Thêm vào cuối `backend/apps/orders/tests/test_lead_assignment_api.py`:

```python
class SharesMatrixApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create(username="admin1", role="admin")
        self.client.force_authenticate(self.admin)
        self.today = timezone.localdate().isoformat()
        self.u1 = User.objects.create(username="s1")
        self.u2 = User.objects.create(username="s2")

    def test_put_then_get_matrix_roundtrip(self):
        resp = self.client.put(
            "/api/orders/lead-assignment/shares",
            {"date": self.today, "rows": [
                {"user_id": self.u1.id, "weights": {"sheet1": 50, "sheet2": 0, "sheet3": 20}},
                {"user_id": self.u2.id, "weights": {"sheet1": 50, "sheet2": 100, "sheet3": 80}},
            ]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        # weight 0 cho (u1, sheet2) → không tạo dòng.
        self.assertFalse(
            LeadAssignmentShare.objects.filter(
                date=timezone.localdate(), sheet="sheet2", user=self.u1
            ).exists()
        )
        body = self.client.get(f"/api/orders/lead-assignment/shares?date={self.today}").json()
        self.assertEqual({s["key"] for s in body["sheets"]}, {"sheet1", "sheet2", "sheet3"})
        row_u1 = next(r for r in body["rows"] if r["user_id"] == self.u1.id)
        self.assertEqual(row_u1["cells"]["sheet1"]["weight"], 50)
        self.assertEqual(row_u1["cells"]["sheet1"]["target_pct"], 50.0)
        self.assertEqual(row_u1["cells"]["sheet2"]["weight"], 0)

    def test_put_keeps_assigned_count(self):
        LeadAssignmentShare.objects.create(
            date=timezone.localdate(), sheet="sheet1", user=self.u1, weight=3, assigned_count=4
        )
        self.client.put(
            "/api/orders/lead-assignment/shares",
            {"date": self.today, "rows": [
                {"user_id": self.u1.id, "weights": {"sheet1": 9, "sheet2": 0, "sheet3": 0}},
            ]},
            format="json",
        )
        share = LeadAssignmentShare.objects.get(date=timezone.localdate(), sheet="sheet1", user=self.u1)
        self.assertEqual(share.weight, 9)
        self.assertEqual(share.assigned_count, 4)

    def test_put_rejects_duplicate_user(self):
        resp = self.client.put(
            "/api/orders/lead-assignment/shares",
            {"date": self.today, "rows": [
                {"user_id": self.u1.id, "weights": {"sheet1": 1}},
                {"user_id": self.u1.id, "weights": {"sheet1": 2}},
            ]},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)


class WorkloadTransferApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create(username="admin2", role="admin")
        self.client.force_authenticate(self.admin)
        self.a = User.objects.create(username="wa")
        self.b = User.objects.create(username="wb")
        self.channel = Channel.objects.create(code="c1", name="C1", owner="C1")
        # 3 đơn sheet1 (đủ TT, landing) cho A, chưa có assessment.
        for i in range(3):
            Order.objects.create(
                channel=self.channel,
                customer=self._customer(f"099900{i}"),
                name="KH",
                phone=f"099900{i}",
                raw_address="HN",
                order_source="landing",
                staff=self.a,
            )

    def _customer(self, phone):
        from apps.customers.models import Customer
        return Customer.objects.create(full_name="KH", phone_raw=phone, phone_normalized=phone)

    def test_workload_counts_pending(self):
        body = self.client.get("/api/orders/lead-assignment/workload?sheet=sheet1").json()
        item = next(it for it in body["items"] if it["user_id"] == self.a.id)
        self.assertEqual(item["pending_count"], 3)

    def test_transfer_moves_fifo(self):
        resp = self.client.post(
            "/api/orders/lead-assignment/transfer",
            {"sheet": "sheet1", "from_user_id": self.a.id, "to_user_id": self.b.id, "count": 2},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["transferred_count"], 2)
        self.assertEqual(Order.objects.filter(staff=self.b).count(), 2)
        self.assertEqual(Order.objects.filter(staff=self.a).count(), 1)
```

- [ ] **Step 3: Chạy test cho chắc nó fail**

Run:
```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders/tests/test_lead_assignment_api.py::SharesMatrixApiTests apps/orders/tests/test_lead_assignment_api.py::WorkloadTransferApiTests --reuse-db -q'
```
Expected: FAIL — 404 (routes chưa tồn tại).

- [ ] **Step 4: Viết view**

Create `backend/apps/orders/transport/views/order_views_impl/lead_assignment_view.py`:

```python
"""API cấu hình chia đơn tự động (ma trận NV × sheet) + workload + transfer."""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orders.application.services.lead_assignment_service import LeadAssignmentService
from apps.orders.models import LeadAssignmentShare
from apps.orders.sheet_rules import SHEET_CHOICES, SHEET_LABELS
from apps.users.models import User
from apps.users.permissions import require_section


def _display(user) -> str:
    return getattr(user, "display_name", "") or user.username


def _serialize_day(date):
    shares = list(
        LeadAssignmentShare.objects.select_related("user").filter(date=date)
    )
    # Tổng weight / tổng assigned theo từng sheet (cho target_pct / actual_pct).
    total_w = {s: 0 for s in SHEET_CHOICES}
    total_a = {s: 0 for s in SHEET_CHOICES}
    for sh in shares:
        total_w[sh.sheet] += sh.weight
        total_a[sh.sheet] += sh.assigned_count

    by_user: dict[int, dict] = {}
    for sh in shares:
        row = by_user.setdefault(
            sh.user_id,
            {
                "user_id": sh.user_id,
                "username": sh.user.username,
                "display_name": _display(sh.user),
                "cells": {
                    s: {"weight": 0, "assigned_count": 0, "target_pct": 0.0, "actual_pct": 0.0}
                    for s in SHEET_CHOICES
                },
            },
        )
        cell = row["cells"][sh.sheet]
        cell["weight"] = sh.weight
        cell["assigned_count"] = sh.assigned_count
        cell["target_pct"] = round(sh.weight / total_w[sh.sheet] * 100, 1) if total_w[sh.sheet] else 0.0
        cell["actual_pct"] = (
            round(sh.assigned_count / total_a[sh.sheet] * 100, 1) if total_a[sh.sheet] else 0.0
        )

    return {
        "date": date.isoformat(),
        "sheets": [
            {"key": s, "label": SHEET_LABELS[s], "total_weight": total_w[s]} for s in SHEET_CHOICES
        ],
        "rows": sorted(by_user.values(), key=lambda r: r["username"]),
    }


class _RowSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    weights = serializers.DictField(child=serializers.IntegerField(min_value=0))


class _UpsertSerializer(serializers.Serializer):
    date = serializers.DateField()
    rows = _RowSerializer(many=True)

    def validate_rows(self, rows):
        user_ids = [r["user_id"] for r in rows]
        if len(user_ids) != len(set(user_ids)):
            raise serializers.ValidationError("Trùng nhân viên trong danh sách.")
        existing = set(
            User.objects.filter(id__in=user_ids, deleted_at__isnull=True).values_list("id", flat=True)
        )
        missing = [uid for uid in user_ids if uid not in existing]
        if missing:
            raise serializers.ValidationError(f"Nhân viên không tồn tại: {missing}")
        for r in rows:
            bad = [k for k in r["weights"] if k not in SHEET_CHOICES]
            if bad:
                raise serializers.ValidationError(f"Sheet không hợp lệ: {bad}")
        return rows


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated, require_section("lead-assignment")])
def lead_assignment_shares(request):
    if request.method == "GET":
        raw = request.query_params.get("date")
        date = serializers.DateField().to_internal_value(raw) if raw else timezone.localdate()
        return Response(_serialize_day(date))

    serializer = _UpsertSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    date = serializer.validated_data["date"]
    rows = serializer.validated_data["rows"]

    with transaction.atomic():
        for r in rows:
            uid = r["user_id"]
            for sheet in SHEET_CHOICES:
                weight = int(r["weights"].get(sheet, 0) or 0)
                if weight > 0:
                    LeadAssignmentShare.objects.update_or_create(
                        date=date, sheet=sheet, user_id=uid, defaults={"weight": weight}
                    )
                else:
                    LeadAssignmentShare.objects.filter(
                        date=date, sheet=sheet, user_id=uid
                    ).delete()
    return Response(_serialize_day(date), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("lead-assignment")])
def lead_assignment_workload(request):
    sheet = request.query_params.get("sheet", "")
    if sheet not in SHEET_CHOICES:
        return Response({"detail": "sheet không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)
    counts = LeadAssignmentService.workload(sheet)
    users = User.objects.filter(id__in=counts.keys())
    name_by_id = {u.id: (u.username, _display(u)) for u in users}
    items = [
        {
            "user_id": uid,
            "username": name_by_id.get(uid, ("", ""))[0],
            "display_name": name_by_id.get(uid, ("", ""))[1],
            "pending_count": cnt,
        }
        for uid, cnt in counts.items()
    ]
    items.sort(key=lambda it: -it["pending_count"])
    return Response({"sheet": sheet, "items": items})


class _TransferSerializer(serializers.Serializer):
    sheet = serializers.ChoiceField(choices=SHEET_CHOICES)
    from_user_id = serializers.IntegerField()
    to_user_id = serializers.IntegerField()
    count = serializers.IntegerField(min_value=1)

    def validate(self, attrs):
        if attrs["from_user_id"] == attrs["to_user_id"]:
            raise serializers.ValidationError("Nhân viên nguồn và đích phải khác nhau.")
        return attrs


@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("lead-assignment")])
def lead_assignment_transfer(request):
    serializer = _TransferSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    v = serializer.validated_data
    moved = LeadAssignmentService.transfer(
        v["sheet"], v["from_user_id"], v["to_user_id"], v["count"]
    )
    return Response({"transferred_count": moved}, status=status.HTTP_200_OK)
```

- [ ] **Step 5: Re-export view trong `__init__.py`**

Trong `backend/apps/orders/transport/views/order_views_impl/__init__.py`, thêm:

```python
from .lead_assignment_view import (
    lead_assignment_shares,
    lead_assignment_transfer,
    lead_assignment_workload,
)
```
Nếu file có list `__all__`, thêm cả 3 tên vào đó.

- [ ] **Step 6: Thêm routes trong `urls.py`**

Trong `backend/apps/orders/urls.py`:
- Thêm vào block import `from .views import (...)`:
```python
    lead_assignment_shares,
    lead_assignment_transfer,
    lead_assignment_workload,
```
- Thêm vào `urlpatterns` (cạnh `"lark-ingest"`):
```python
    path("lead-assignment/shares", lead_assignment_shares, name="orders-lead-assignment-shares"),
    path("lead-assignment/workload", lead_assignment_workload, name="orders-lead-assignment-workload"),
    path("lead-assignment/transfer", lead_assignment_transfer, name="orders-lead-assignment-transfer"),
```

> Nếu `apps/orders/views.py` liệt kê export tường minh thay vì `*`, thêm 3 tên đó ở file re-export tương ứng.

- [ ] **Step 7: Chạy test cho chắc nó pass**

Run lệnh ở Step 3. Expected: PASS (5 passed).

- [ ] **Step 8: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/transport/views/order_views_impl/lead_assignment_view.py backend/apps/users/models.py backend/apps/orders/urls.py
git add backend/apps/users/models.py backend/apps/orders/transport/views/order_views_impl/lead_assignment_view.py backend/apps/orders/transport/views/order_views_impl/__init__.py backend/apps/orders/urls.py backend/apps/orders/tests/test_lead_assignment_api.py
git commit -m "feat(orders): lead-assignment matrix config + workload + transfer API"
```

---

## Task 6: Frontend API client + test

**Files:**
- Create: `frontend/src/lib/api/lead-assignment.ts`
- Test: `frontend/src/lib/api/__tests__/lead-assignment.test.ts`

- [ ] **Step 1: Viết test thất bại**

Create `frontend/src/lib/api/__tests__/lead-assignment.test.ts`:

```typescript
import { describe, expect, it, vi, beforeEach } from "vitest";

import {
  getLeadAssignment,
  saveLeadAssignment,
  getWorkload,
  transferOrders,
} from "@/lib/api/lead-assignment";

vi.mock("@/services/http", () => ({ fetchJson: vi.fn() }));
import { fetchJson } from "@/services/http";

describe("lead-assignment api", () => {
  beforeEach(() => vi.clearAllMocks());

  it("getLeadAssignment hits shares url with date", async () => {
    (fetchJson as ReturnType<typeof vi.fn>).mockResolvedValue({ date: "2026-06-13", sheets: [], rows: [] });
    await getLeadAssignment("2026-06-13");
    expect(fetchJson).toHaveBeenCalledWith("/api/orders/lead-assignment/shares?date=2026-06-13");
  });

  it("saveLeadAssignment PUTs rows", async () => {
    (fetchJson as ReturnType<typeof vi.fn>).mockResolvedValue({ date: "2026-06-13", sheets: [], rows: [] });
    await saveLeadAssignment({ date: "2026-06-13", rows: [{ user_id: 1, weights: { sheet1: 50 } }] });
    expect(fetchJson).toHaveBeenCalledWith("/api/orders/lead-assignment/shares", {
      method: "PUT",
      body: JSON.stringify({ date: "2026-06-13", rows: [{ user_id: 1, weights: { sheet1: 50 } }] }),
    });
  });

  it("getWorkload hits workload url", async () => {
    (fetchJson as ReturnType<typeof vi.fn>).mockResolvedValue({ sheet: "sheet1", items: [] });
    await getWorkload("sheet1");
    expect(fetchJson).toHaveBeenCalledWith("/api/orders/lead-assignment/workload?sheet=sheet1");
  });

  it("transferOrders POSTs body", async () => {
    (fetchJson as ReturnType<typeof vi.fn>).mockResolvedValue({ transferred_count: 2 });
    await transferOrders({ sheet: "sheet1", from_user_id: 1, to_user_id: 2, count: 2 });
    expect(fetchJson).toHaveBeenCalledWith("/api/orders/lead-assignment/transfer", {
      method: "POST",
      body: JSON.stringify({ sheet: "sheet1", from_user_id: 1, to_user_id: 2, count: 2 }),
    });
  });
});
```

- [ ] **Step 2: Chạy test cho chắc nó fail**

Run: `cd frontend && npx vitest run src/lib/api/__tests__/lead-assignment.test.ts`
Expected: FAIL — không resolve module.

- [ ] **Step 3: Viết API client**

Create `frontend/src/lib/api/lead-assignment.ts`:

```typescript
import { fetchJson } from "@/services/http";

const BASE = "/api/orders/lead-assignment";
export type SheetKey = "sheet1" | "sheet2" | "sheet3";

export interface SheetMeta {
  key: SheetKey;
  label: string;
  total_weight: number;
}
export interface Cell {
  weight: number;
  assigned_count: number;
  target_pct: number;
  actual_pct: number;
}
export interface MatrixRow {
  user_id: number;
  username: string;
  display_name: string;
  cells: Record<SheetKey, Cell>;
}
export interface LeadAssignmentDay {
  date: string;
  sheets: SheetMeta[];
  rows: MatrixRow[];
}
export interface SaveRow {
  user_id: number;
  weights: Partial<Record<SheetKey, number>>;
}
export interface WorkloadItem {
  user_id: number;
  username: string;
  display_name: string;
  pending_count: number;
}

export async function getLeadAssignment(date: string): Promise<LeadAssignmentDay> {
  return fetchJson(`${BASE}/shares?date=${encodeURIComponent(date)}`);
}
export async function saveLeadAssignment(payload: {
  date: string;
  rows: SaveRow[];
}): Promise<LeadAssignmentDay> {
  return fetchJson(`${BASE}/shares`, { method: "PUT", body: JSON.stringify(payload) });
}
export async function getWorkload(sheet: SheetKey): Promise<{ sheet: SheetKey; items: WorkloadItem[] }> {
  return fetchJson(`${BASE}/workload?sheet=${sheet}`);
}
export async function transferOrders(payload: {
  sheet: SheetKey;
  from_user_id: number;
  to_user_id: number;
  count: number;
}): Promise<{ transferred_count: number }> {
  return fetchJson(`${BASE}/transfer`, { method: "POST", body: JSON.stringify(payload) });
}
```

- [ ] **Step 4: Chạy test cho chắc nó pass**

Run: `cd frontend && npx vitest run src/lib/api/__tests__/lead-assignment.test.ts`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api/lead-assignment.ts frontend/src/lib/api/__tests__/lead-assignment.test.ts
git commit -m "feat(fe): lead-assignment api client"
```

---

## Task 7: Frontend page + route + nav

**Files:**
- Modify: `frontend/src/lib/routes.ts`
- Modify: `frontend/src/app/modules.ts`
- Modify: `frontend/src/app/routed-pages.tsx`
- Modify: `frontend/src/app/routes.tsx`
- Create: `frontend/src/pages/LeadAssignmentPage.tsx`

- [ ] **Step 1: Section + path trong `routes.ts`**

Trong `frontend/src/lib/routes.ts`: thêm `| "lead-assignment"` vào union `AppSection` (cạnh `"users"`), và vào `SECTION_TO_PATH`:
```typescript
  "lead-assignment": "/settings/lead-assignment",
```

- [ ] **Step 2: Nav entry trong `modules.ts`**

Trong module `key: "settings"`, mảng `sections`, thêm sau `users`:
```typescript
      { key: "lead-assignment", labelKey: "topnav.tabs.leadAssignment", icon: Shuffle },
```
Thêm `Shuffle` vào import `lucide-react` đầu file.

- [ ] **Step 3: Label i18n**

Tìm file chứa `topnav.tabs.users`:
```bash
grep -rln "topnav.tabs.users\"\|leadAssignment\|\"users\":" frontend/src/i18n | head
```
Thêm key `leadAssignment: "Chia đơn tự động"` cạnh `users` trong object `topnav.tabs` cho TẤT CẢ locale (vi bắt buộc; en = "Lead Assignment" nếu có).

- [ ] **Step 4: Lazy import trong `routed-pages.tsx`**

Thêm:
```typescript
const LeadAssignmentPage = lazy(() => import("@/pages/LeadAssignmentPage"));
```
Export theo cách các page khác trong file được export (xem cuối file — thêm vào danh sách export nếu có).

- [ ] **Step 5: Route trong `routes.tsx`**

Cạnh route `settings/users` (dòng ~276), thêm — mirror đúng wrapper guard mà `settings/users` dùng:
```tsx
      {
        path: "settings/lead-assignment",
        element: (
          <RouteGuard section="lead-assignment">
            <LeadAssignmentPage />
          </RouteGuard>
        ),
      },
```
> Thay `RouteGuard` bằng đúng component bọc của `settings/users` trong file (mở xem). Import `LeadAssignmentPage` theo cách các page khác được import.

- [ ] **Step 6: Viết trang `LeadAssignmentPage.tsx`**

Create `frontend/src/pages/LeadAssignmentPage.tsx`:

```tsx
import { useEffect, useMemo, useState } from "react";
import { Plus, Trash2 } from "lucide-react";

import { useAppShell } from "@/app/AppShellContext";
import {
  getLeadAssignment,
  saveLeadAssignment,
  getWorkload,
  transferOrders,
  type LeadAssignmentDay,
  type SheetKey,
  type WorkloadItem,
} from "@/lib/api/lead-assignment";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const SHEETS: Array<{ key: SheetKey; label: string }> = [
  { key: "sheet1", label: "Landing xác nhận đơn" },
  { key: "sheet2", label: "Messenger xác nhận đơn" },
  { key: "sheet3", label: "Tư vấn" },
];

interface Row {
  user_id: number;
  weights: Record<SheetKey, number>;
}

function todayIso(): string {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

export default function LeadAssignmentPage() {
  const shell = useAppShell();
  const staffUsers: Array<{ id: number; username: string; display_name?: string }> =
    (shell as { staffUsers?: Array<{ id: number; username: string; display_name?: string }> })
      .staffUsers ?? [];

  const [date, setDate] = useState(todayIso());
  const [rows, setRows] = useState<Row[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Transfer state
  const [tSheet, setTSheet] = useState<SheetKey>("sheet1");
  const [workload, setWorkload] = useState<WorkloadItem[]>([]);
  const [fromId, setFromId] = useState<number | "">("");
  const [toId, setToId] = useState<number | "">("");
  const [count, setCount] = useState(1);
  const [transferMsg, setTransferMsg] = useState<string | null>(null);

  const nameOf = useMemo(() => {
    const m = new Map<number, string>();
    for (const u of staffUsers) m.set(u.id, u.display_name || u.username);
    return m;
  }, [staffUsers]);

  function applyDay(data: LeadAssignmentDay) {
    setRows(
      data.rows.map((r) => ({
        user_id: r.user_id,
        weights: {
          sheet1: r.cells.sheet1.weight,
          sheet2: r.cells.sheet2.weight,
          sheet3: r.cells.sheet3.weight,
        },
      })),
    );
  }

  async function loadDay(d: string) {
    setError(null);
    try {
      applyDay(await getLeadAssignment(d));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Lỗi tải cấu hình");
    }
  }

  async function loadWorkload(sheet: SheetKey) {
    try {
      setWorkload((await getWorkload(sheet)).items);
    } catch {
      setWorkload([]);
    }
  }

  useEffect(() => {
    void loadDay(date);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date]);
  useEffect(() => {
    void loadWorkload(tSheet);
  }, [tSheet]);

  const colTotal = (sheet: SheetKey) => rows.reduce((s, r) => s + (r.weights[sheet] || 0), 0);

  function addRow() {
    const used = new Set(rows.map((r) => r.user_id));
    const next = staffUsers.find((u) => !used.has(u.id));
    if (!next) return;
    setRows([...rows, { user_id: next.id, weights: { sheet1: 0, sheet2: 0, sheet3: 0 } }]);
  }
  function setCell(idx: number, sheet: SheetKey, val: number) {
    setRows(rows.map((r, i) => (i === idx ? { ...r, weights: { ...r.weights, [sheet]: val } } : r)));
  }
  function removeRow(idx: number) {
    setRows(rows.filter((_, i) => i !== idx));
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      applyDay(
        await saveLeadAssignment({
          date,
          rows: rows.map((r) => ({ user_id: r.user_id, weights: r.weights })),
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Lưu thất bại");
    } finally {
      setSaving(false);
    }
  }

  async function doTransfer() {
    if (fromId === "" || toId === "" || fromId === toId) return;
    setTransferMsg(null);
    try {
      const res = await transferOrders({
        sheet: tSheet,
        from_user_id: Number(fromId),
        to_user_id: Number(toId),
        count,
      });
      setTransferMsg(`Đã chuyển ${res.transferred_count} đơn.`);
      await loadWorkload(tSheet);
    } catch (e) {
      setTransferMsg(e instanceof Error ? e.message : "Chuyển thất bại");
    }
  }

  return (
    <div className="p-6 space-y-8">
      {/* KHỐI 1: ma trận cấu hình */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold text-sky-900">Chia đơn tự động</h1>
          <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="w-44" />
        </div>
        <p className="text-sm text-muted-foreground">
          % nhận đơn của từng nhân viên trong mỗi sheet (chia theo cột, mỗi cột nên = 100%). Ô 0 = không nhận.
        </p>
        {error && <div className="text-sm text-red-600">{error}</div>}
        <table className="w-full text-sm border rounded">
          <thead className="bg-sky-50 text-sky-900">
            <tr>
              <th className="text-left p-2">Nhân viên</th>
              {SHEETS.map((s) => (
                <th key={s.key} className="text-center p-2 w-44">{s.label}</th>
              ))}
              <th className="p-2 w-12"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, idx) => (
              <tr key={r.user_id} className="border-t">
                <td className="p-2">
                  <select
                    className="border rounded px-2 py-1 w-full"
                    value={r.user_id}
                    onChange={(e) =>
                      setRows(rows.map((rr, i) => (i === idx ? { ...rr, user_id: Number(e.target.value) } : rr)))
                    }
                  >
                    {staffUsers
                      .filter((u) => u.id === r.user_id || !rows.some((rr) => rr.user_id === u.id))
                      .map((u) => (
                        <option key={u.id} value={u.id}>{u.display_name || u.username}</option>
                      ))}
                  </select>
                </td>
                {SHEETS.map((s) => (
                  <td key={s.key} className="p-2 text-center">
                    <Input
                      type="number"
                      min={0}
                      className="w-24 mx-auto text-right"
                      value={r.weights[s.key]}
                      onChange={(e) => setCell(idx, s.key, Math.max(0, Number(e.target.value) || 0))}
                    />
                  </td>
                ))}
                <td className="p-2 text-right">
                  <button onClick={() => removeRow(idx)} aria-label="Xoá">
                    <Trash2 className="h-4 w-4 text-red-500" />
                  </button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={5} className="p-4 text-center text-muted-foreground">Chưa cấu hình.</td></tr>
            )}
          </tbody>
          <tfoot>
            <tr className="border-t bg-slate-50 font-medium">
              <td className="p-2">Tổng cột</td>
              {SHEETS.map((s) => (
                <td key={s.key} className={`p-2 text-center ${colTotal(s.key) === 100 ? "text-emerald-600" : "text-amber-600"}`}>
                  {colTotal(s.key)}%
                </td>
              ))}
              <td></td>
            </tr>
          </tfoot>
        </table>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={addRow} disabled={rows.length >= staffUsers.length}>
            <Plus className="h-4 w-4 mr-1" /> Thêm nhân viên
          </Button>
          <Button onClick={save} disabled={saving}>{saving ? "Đang lưu..." : "Lưu"}</Button>
        </div>
      </section>

      {/* KHỐI 2: tải & chuyển đơn */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-sky-900">Tải & chuyển đơn</h2>
        <div className="flex items-center gap-2">
          <span className="text-sm">Sheet:</span>
          <select className="border rounded px-2 py-1" value={tSheet} onChange={(e) => setTSheet(e.target.value as SheetKey)}>
            {SHEETS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </div>
        <table className="w-full max-w-md text-sm border rounded">
          <thead className="bg-sky-50 text-sky-900">
            <tr><th className="text-left p-2">Nhân viên</th><th className="text-right p-2 w-32">Đơn đang chờ</th></tr>
          </thead>
          <tbody>
            {workload.map((it) => (
              <tr key={it.user_id} className="border-t">
                <td className="p-2">{it.display_name || it.username}</td>
                <td className="p-2 text-right">{it.pending_count}</td>
              </tr>
            ))}
            {workload.length === 0 && (
              <tr><td colSpan={2} className="p-4 text-center text-muted-foreground">Không có đơn chờ.</td></tr>
            )}
          </tbody>
        </table>
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-sm">Từ NV
            <select className="border rounded px-2 py-1 ml-1" value={fromId} onChange={(e) => setFromId(e.target.value ? Number(e.target.value) : "")}>
              <option value="">--</option>
              {workload.map((it) => <option key={it.user_id} value={it.user_id}>{it.display_name || it.username} ({it.pending_count})</option>)}
            </select>
          </label>
          <label className="text-sm">Đến NV
            <select className="border rounded px-2 py-1 ml-1" value={toId} onChange={(e) => setToId(e.target.value ? Number(e.target.value) : "")}>
              <option value="">--</option>
              {staffUsers.map((u) => <option key={u.id} value={u.id}>{u.display_name || u.username}</option>)}
            </select>
          </label>
          <label className="text-sm">Số lượng
            <Input type="number" min={1} className="w-24 ml-1 inline-block" value={count} onChange={(e) => setCount(Math.max(1, Number(e.target.value) || 1))} />
          </label>
          <Button onClick={doTransfer} disabled={fromId === "" || toId === "" || fromId === toId}>Chuyển</Button>
          {transferMsg && <span className="text-sm text-muted-foreground">{transferMsg}</span>}
        </div>
      </section>
    </div>
  );
}
```

> **Integration:** xác nhận `useAppShell()` expose `staffUsers` (xem `routed-pages.tsx` ~46 dùng `shell.staffUsers`). Nếu shape khác, lấy danh sách user active từ nguồn shell tương ứng. Kiểm tra import `@/components/ui/button`, `@/components/ui/input` khớp shadcn/ui (xem `ProductCategoriesPage.tsx`). `nameOf` dùng cho fallback tên nếu cần.

- [ ] **Step 7: Build FE**

Run: `cd frontend && npm run build`
Expected: build OK. Sửa import/shape theo lỗi (staffUsers, ui imports, guard component) tới khi pass. Nếu `nameOf` báo unused, bỏ nó.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/routes.ts frontend/src/app/modules.ts frontend/src/app/routed-pages.tsx frontend/src/app/routes.tsx frontend/src/pages/LeadAssignmentPage.tsx frontend/src/i18n
git commit -m "feat(fe): lead-assignment matrix config + transfer page"
```

---

## Task 8: Chạy đủ test + đẩy nhánh

- [ ] **Step 1: Toàn bộ test backend liên quan**

Run:
```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders/tests/test_sheet_rules_classify.py apps/orders/tests/test_lead_assignment_service.py apps/orders/tests/test_lead_assignment_api.py apps/orders/tests/test_ingest_api.py --reuse-db -q'
```
Expected: tất cả PASS (gồm test ingest cũ — chống hồi quy).

- [ ] **Step 2: Test FE + build**

Run: `cd frontend && npx vitest run src/lib/api/__tests__/lead-assignment.test.ts && npm run build`
Expected: PASS + build OK.

- [ ] **Step 3: Push + MR**

```bash
git push -u origin feat/lead-auto-assignment-ingest
```
Gửi link MR (theo CLAUDE.md).

---

## Self-Review Notes (đã đối chiếu spec)

- §2 sheet rules ↔ Task 1 (`classify_sheet`, `sheet_q`). §5 model (date,sheet,user) ↔ Task 2.
  §6 classify ↔ Task 1+3. §7 service pick/assign/workload/transfer ↔ Task 3. §8 hooks ↔ Task 4.
  §9 API shares/workload/transfer + section ↔ Task 5. §10 FE ma trận + chuyển đơn ↔ Task 6+7. §11 tests phủ Task 1–7.
- Tên thống nhất: `classify_sheet`, `sheet_q`, `SHEET_CHOICES`, `SHEET_LABELS`, `pick_staff_for(date, sheet)`,
  `assign_if_unassigned`, `workload(sheet)`, `transfer(...)`; endpoints `lead_assignment_shares`/`_workload`/`_transfer`
  dưới `/api/orders/lead-assignment/{shares,workload,transfer}`; FE `getLeadAssignment/saveLeadAssignment/getWorkload/transferOrders`.
- Sheet key thống nhất `sheet1|sheet2|sheet3` xuyên BE↔FE. weight 0 = xoá dòng (PUT) ↔ test `test_put_then_get_matrix_roundtrip`.
- Transfer độc lập counter, FIFO ↔ `test_transfer_moves_fifo`; pending = no sales_status ↔ `_NO_SALES_STATUS_Q`.
```
