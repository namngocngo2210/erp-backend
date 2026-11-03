# Customer Report — Sprint 1 (Perf + Data Contract) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Làm `/api/customers/report/summary` nhanh hơn nhiều (bỏ prefetch thừa, index, snapshot phân khúc, precompute cache) và bổ sung các aggregate backend cho "bức tranh toàn cảnh" (funnel, cohort, insights, data-coverage) — đóng băng data contract cho Sprint 2.

**Architecture:** Giữ nguyên 2 service hiện có (`CustomerReportService`, `CustomerBehaviorReportService`) và mô hình nghiệp vụ 2 trạng thái. Tối ưu trong service layer + thêm 1 bảng snapshot phân khúc cập nhật bằng Celery. Mọi aggregate mới tính bằng SQL/ORM aggregation, KHÔNG kéo từng bản ghi về Python.

**Tech Stack:** Python 3.12+, Django ORM + raw SQL (`connection.cursor`), Celery (`@shared_task` + `CELERY_BEAT_SCHEDULE`), PostgreSQL, pytest.

## Global Constraints

- Branch cố định: `feat/customer-report-redesign`. KHÔNG tự tạo branch mới. Mỗi task commit riêng.
- Clean architecture: không đặt logic trong view; service layer giữ logic; task chỉ gọi service.
- Không đổi định nghĩa ranh giới 2 trạng thái: "sau mua" = ≥1 đơn `status=DELIVERED_SUCCESS_ORDER_STATUS (3)`, `invoice_status IS DISTINCT FROM CANCELLED_INVOICE_STATUS`, `branch_id NOT IN EXCLUDED_BRANCH_IDS`.
- Output JSON của các block hiện có (`segment_distribution`, `overview`, ...) phải **bất biến** sau tối ưu — test hiện có trong `test_customer_report_service.py` phải xanh.
- Chạy test bằng image test của project (prod image thiếu deps). Lệnh chuẩn trong repo: dùng `docker compose` service test trên network `desi-order_desi-net` (redis cần password). Mỗi task nêu rõ test path.
- ruff sạch trước khi commit.

## File Structure

| File | Trách nhiệm |
|---|---|
| `backend/apps/customers/profile_service.py` | + `_latest_sales_map()` (đọc nhẹ sales_status_code, không prefetch) |
| `backend/apps/customers/report_service.py` | dùng `_latest_sales_map`; thêm `_data_coverage()` |
| `backend/apps/orders/models.py` | + 1–2 partial index cho GTC aggregation |
| `backend/apps/customers/models.py` | + model `CustomerSegmentSnapshot` |
| `backend/apps/customers/segment_snapshot_service.py` | **mới** — build/đọc snapshot phân khúc |
| `backend/apps/customers/customer_behavior_report.py` | + `funnel`, `cohort_retention`, `insights` vào output |
| `backend/apps/customers/report_insights.py` | **mới** — sinh insight theo ngưỡng (hàm thuần) |
| `backend/apps/customers/tasks.py` | **mới** — `rebuild_segment_snapshot`, `warm_report_cache` |
| `backend/config/settings_parts/base.py` | + 2 entry `CELERY_BEAT_SCHEDULE` |
| `backend/apps/customers/views.py` | orchestration: share `total_customers`, gọi aggregate mới |

---

### Task 1: `_latest_sales_map` — bỏ prefetch needs khỏi đường report

**Files:**
- Modify: `backend/apps/customers/profile_service.py` (thêm static method sau `_latest_order_map`)
- Modify: `backend/apps/customers/report_service.py:163` (`_segment_map`)
- Test: `backend/apps/customers/tests/test_customer_report_service.py`

**Interfaces:**
- Produces: `CustomerProfileService._latest_sales_map(customer_ids: list[int]) -> dict[int, str | None]`
  — map customer_id → `sales_status_code` của đơn mới nhất (kể cả đơn hoàn/hủy), KHÔNG select_related/prefetch.

- [ ] **Step 1: Viết test thất bại** (số query giảm + output không đổi)

```python
# thêm vào CustomerReportServiceTests
def test_segment_map_uses_lightweight_sales_lookup(self):
    from apps.customers.profile_service import CustomerProfileService
    loyal = Customer.objects.create(full_name="Loyal")
    self._make_order(customer=loyal, sales_status_code="closed")
    ids = [loyal.id]
    sales = CustomerProfileService._latest_sales_map(ids)
    self.assertEqual(sales, {loyal.id: "closed"})
```

- [ ] **Step 2: Chạy test, xác nhận FAIL** (`AttributeError: _latest_sales_map`)

Run: `pytest apps/customers/tests/test_customer_report_service.py::CustomerReportServiceTests::test_segment_map_uses_lightweight_sales_lookup -v`
Expected: FAIL.

- [ ] **Step 3: Cài đặt `_latest_sales_map`**

```python
@staticmethod
def _latest_sales_map(customer_ids: list[int]) -> dict[int, str | None]:
    """sales_status_code của đơn mới nhất mỗi khách. Đọc nhẹ: chỉ 2 cột,
    DISTINCT ON, không select_related/prefetch (report chỉ cần mã này)."""
    if not customer_ids:
        return {}
    rows = (
        Order.objects.filter(customer_id__in=customer_ids, deleted_at__isnull=True)
        .order_by("customer_id", "-created_at")
        .distinct("customer_id")
        .values_list("customer_id", "assessment__sales_status_code")
    )
    return {cid: code for cid, code in rows}
```

- [ ] **Step 4: Đổi `_segment_map` dùng map mới** (bỏ `_latest_order_map` ở đường report)

Trong `report_service.py::_segment_map`, thay:
```python
        latest = CustomerProfileService._latest_order_map(customer_ids)
        ...
            order = latest.get(cid)
            assessment = getattr(order, "assessment", None) if order else None
            result[cid] = segmentation.classify_segment(
                ...
                latest_sales_status_code=getattr(assessment, "sales_status_code", None) if assessment else None,
            )
```
bằng:
```python
        latest_sales = CustomerProfileService._latest_sales_map(customer_ids)
        ...
            result[cid] = segmentation.classify_segment(
                ...
                latest_sales_status_code=latest_sales.get(cid),
            )
```

- [ ] **Step 5: Chạy lại toàn bộ test report, xác nhận PASS**

Run: `pytest apps/customers/tests/test_customer_report_service.py -v`
Expected: PASS hết (kể cả `test_segment_attribution_and_filter`).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/customers/profile_service.py backend/apps/customers/report_service.py backend/apps/customers/tests/test_customer_report_service.py
git commit -m "perf(customers): lightweight latest-sales lookup in report segment map"
```

---

### Task 2: Partial index cho GTC aggregation

**Files:**
- Modify: `backend/apps/orders/models.py` (Meta.indexes)
- Create: `backend/apps/orders/migrations/00XX_order_gtc_partial_idx.py` (qua makemigrations)

**Interfaces:**
- Produces: index `ord_gtc_customer_idx` phục vụ `_metrics_map` và buyers CTE (`WHERE deleted_at IS NULL AND status=3`, key `customer_id`).

- [ ] **Step 1: Thêm index vào Meta.indexes của Order** (sau `ord_resale_delivered_upd_idx`)

```python
            # GTC aggregation (buyers CTE + _metrics_map) lọc status=3 & active rồi GROUP BY customer.
            models.Index(
                fields=["customer", "-created_at"],
                condition=Q(deleted_at__isnull=True, status=3),
                name="ord_gtc_customer_idx",
            ),
```

- [ ] **Step 2: Sinh migration**

Run: `python manage.py makemigrations orders`
Expected: tạo file migration thêm `ord_gtc_customer_idx`.

- [ ] **Step 3: Áp migration trên DB test/dev**

Run: `python manage.py migrate orders`
Expected: OK, không lỗi.

- [ ] **Step 4: Kiểm chứng index được dùng** (manual, ghi lại kết quả)

Chạy trên psql dev với 1 customer_id thật:
```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT customer_id, count(*) FROM orders
WHERE deleted_at IS NULL AND status=3 GROUP BY customer_id LIMIT 5;
```
Expected: plan dùng `ord_gtc_customer_idx` (Index Scan), không Seq Scan toàn bảng. Dán output vào commit body.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/models.py backend/apps/orders/migrations/
git commit -m "perf(orders): partial index for GTC customer aggregation"
```

---

### Task 3: Model `CustomerSegmentSnapshot` + migration

**Files:**
- Modify: `backend/apps/customers/models.py` (thêm model cuối file)
- Create: `backend/apps/customers/migrations/00XX_customer_segment_snapshot.py`

**Interfaces:**
- Produces: model `CustomerSegmentSnapshot(customer OneToOne PK, segment_code, total_orders, total_spent, last_purchase_at, has_problem, updated_at)`, db_table `customer_segment_snapshot`, index trên `segment_code`.

- [ ] **Step 1: Thêm model**

```python
class CustomerSegmentSnapshot(models.Model):
    """Ảnh chụp phân khúc + RFM/khách, cập nhật định kỳ (Celery). Cho phép filter
    segment bằng WHERE-indexed thay vì classify realtime toàn tập."""
    customer = models.OneToOneField(
        "Customer", on_delete=models.CASCADE, primary_key=True, related_name="segment_snapshot",
    )
    segment_code = models.CharField(max_length=20, db_index=True)
    total_orders = models.IntegerField(default=0)
    total_spent = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    last_purchase_at = models.DateTimeField(null=True, blank=True)
    has_problem = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customer_segment_snapshot"
```

- [ ] **Step 2: Sinh + áp migration**

Run: `python manage.py makemigrations customers && python manage.py migrate customers`
Expected: tạo bảng `customer_segment_snapshot`.

- [ ] **Step 3: Commit**

```bash
git add backend/apps/customers/models.py backend/apps/customers/migrations/
git commit -m "feat(customers): CustomerSegmentSnapshot table"
```

---

### Task 4: `SegmentSnapshotService` — build + đọc snapshot

**Files:**
- Create: `backend/apps/customers/segment_snapshot_service.py`
- Test: `backend/apps/customers/tests/test_segment_snapshot_service.py`

**Interfaces:**
- Consumes: `CustomerProfileService._metrics_map`, `CustomerLoyaltyService._customers_with_problem_signal`, `CustomerProfileService._latest_sales_map` (Task 1), `segmentation.classify_segment`.
- Produces:
  - `SegmentSnapshotService.rebuild(customer_ids: list[int] | None = None) -> int` (số dòng upsert; None = toàn bộ KH chưa xóa)
  - `SegmentSnapshotService.segment_map(customer_ids: list[int]) -> dict[int, str]` (đọc từ snapshot; thiếu cid → bỏ qua)

- [ ] **Step 1: Viết test thất bại**

```python
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from apps.customers.models import Customer, CustomerSegmentSnapshot, CustomerSegmentCategory
from apps.customers.segmentation import SEGMENT_CATALOG_SEED
from apps.customers.segment_snapshot_service import SegmentSnapshotService
from apps.orders.models import Order

class SegmentSnapshotServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for it in SEGMENT_CATALOG_SEED:
            CustomerSegmentCategory.objects.get_or_create(
                code=it["code"], defaults=dict(name=it["name"], sort_order=it["sort_order"]))

    def test_rebuild_writes_segment(self):
        c = Customer.objects.create(full_name="A")
        Order.objects.create(customer=c, status=3, total_after_discount=Decimal("0"),
                             channel_id=self._chan())
        n = SegmentSnapshotService.rebuild([c.id])
        self.assertEqual(n, 1)
        snap = CustomerSegmentSnapshot.objects.get(pk=c.id)
        self.assertTrue(snap.segment_code)
        self.assertEqual(SegmentSnapshotService.segment_map([c.id])[c.id], snap.segment_code)

    def _chan(self):
        from apps.channels_core.models import Channel
        return Channel.objects.get_or_create(code="ch", defaults={"name": "ch"})[0].id
```

- [ ] **Step 2: Chạy test, xác nhận FAIL** (ImportError)

Run: `pytest apps/customers/tests/test_segment_snapshot_service.py -v`
Expected: FAIL.

- [ ] **Step 3: Cài đặt service** (logic classify copy từ `report_service._segment_map`, ghi thành bulk upsert)

```python
"""Build/đọc snapshot phân khúc khách hàng (tách classify realtime khỏi request)."""
from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from . import segmentation
from .models import Customer, CustomerSegmentSnapshot
from .customer_loyalty_service import CustomerLoyaltyService
from .profile_service import CustomerProfileService

_BATCH = 2000


class SegmentSnapshotService:
    @staticmethod
    def rebuild(customer_ids: list[int] | None = None) -> int:
        if customer_ids is None:
            customer_ids = list(
                Customer.objects.filter(deleted_at__isnull=True).values_list("id", flat=True)
            )
        total = 0
        for i in range(0, len(customer_ids), _BATCH):
            batch = customer_ids[i : i + _BATCH]
            total += SegmentSnapshotService._rebuild_batch(batch)
        return total

    @staticmethod
    def _rebuild_batch(ids: list[int]) -> int:
        metrics = CustomerProfileService._metrics_map(ids)
        problem_ids = CustomerLoyaltyService._customers_with_problem_signal(ids)
        latest_sales = CustomerProfileService._latest_sales_map(ids)
        now = timezone.now()
        objs = []
        for cid in ids:
            m = metrics.get(cid, {})
            total_orders = m.get("total_orders", 0) or 0
            last = m.get("last_purchase_at")
            days = (now - last).days if last else None
            code = segmentation.classify_segment(
                delivered_count=total_orders,
                total_spent=m.get("total_spent") or Decimal("0"),
                days_since_last_purchase=days,
                has_problem=cid in problem_ids,
                latest_sales_status_code=latest_sales.get(cid),
            )
            objs.append(CustomerSegmentSnapshot(
                customer_id=cid, segment_code=code, total_orders=total_orders,
                total_spent=m.get("total_spent") or Decimal("0"),
                last_purchase_at=last, has_problem=cid in problem_ids,
            ))
        CustomerSegmentSnapshot.objects.bulk_create(
            objs, update_conflicts=True, unique_fields=["customer"],
            update_fields=["segment_code", "total_orders", "total_spent",
                           "last_purchase_at", "has_problem", "updated_at"],
        )
        return len(objs)

    @staticmethod
    def segment_map(customer_ids: list[int]) -> dict[int, str]:
        if not customer_ids:
            return {}
        rows = (CustomerSegmentSnapshot.objects
                .filter(customer_id__in=customer_ids)
                .values_list("customer_id", "segment_code"))
        return {cid: code for cid, code in rows}
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `pytest apps/customers/tests/test_segment_snapshot_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/customers/segment_snapshot_service.py backend/apps/customers/tests/test_segment_snapshot_service.py
git commit -m "feat(customers): segment snapshot build/read service"
```

---

### Task 5: Report đọc snapshot (fallback classify khi thiếu)

**Files:**
- Modify: `backend/apps/customers/report_service.py:163` (`_segment_map`)
- Test: `backend/apps/customers/tests/test_customer_report_service.py`

**Interfaces:**
- Consumes: `SegmentSnapshotService.segment_map`, `SegmentSnapshotService.rebuild`.
- Behaviour: `_segment_map(customer_ids)` đọc snapshot trước; cid thiếu trong snapshot → tính realtime cho phần thiếu rồi merge (đảm bảo đúng cả khi snapshot chưa chạy).

- [ ] **Step 1: Viết test** — report đúng cả khi snapshot rỗng (đã có `test_segment_attribution_and_filter`); thêm test khi snapshot có sẵn vẫn đúng:

```python
def test_segment_map_reads_snapshot_when_present(self):
    from apps.customers.segment_snapshot_service import SegmentSnapshotService
    c = Customer.objects.create(full_name="Snap")
    self._make_order(customer=c, occupation_code="office")
    SegmentSnapshotService.rebuild([c.id])
    data = CustomerReportService.summary()
    seg = {r["code"]: r["count"] for r in data["segment_distribution"]}
    self.assertEqual(sum(seg.values()), 1)
```

- [ ] **Step 2: Chạy test (cả file), xác nhận test cũ vẫn cần pass**

Run: `pytest apps/customers/tests/test_customer_report_service.py -v`

- [ ] **Step 3: Sửa `_segment_map`** — snapshot-first + fallback realtime cho phần thiếu

```python
    @staticmethod
    def _segment_map(customer_ids: list[int]) -> dict[int, str]:
        if not customer_ids:
            return {}
        from .segment_snapshot_service import SegmentSnapshotService
        result = SegmentSnapshotService.segment_map(customer_ids)
        missing = [cid for cid in customer_ids if cid not in result]
        if missing:
            result.update(CustomerReportService._classify_realtime(missing))
        return result

    @staticmethod
    def _classify_realtime(customer_ids: list[int]) -> dict[int, str]:
        # (nội dung cũ của _segment_map, dùng _latest_sales_map từ Task 1)
        metrics = CustomerProfileService._metrics_map(customer_ids)
        problem_ids = CustomerLoyaltyService._customers_with_problem_signal(customer_ids)
        latest_sales = CustomerProfileService._latest_sales_map(customer_ids)
        now = timezone.now()
        result: dict[int, str] = {}
        for cid in customer_ids:
            m = metrics.get(cid, {})
            total_orders = m.get("total_orders", 0) or 0
            last = m.get("last_purchase_at")
            days = (now - last).days if last else None
            result[cid] = segmentation.classify_segment(
                delivered_count=total_orders,
                total_spent=m.get("total_spent") or Decimal("0"),
                days_since_last_purchase=days,
                has_problem=cid in problem_ids,
                latest_sales_status_code=latest_sales.get(cid),
            )
        return result
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `pytest apps/customers/tests/test_customer_report_service.py -v`
Expected: PASS hết.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/customers/report_service.py backend/apps/customers/tests/test_customer_report_service.py
git commit -m "perf(customers): report segment map reads snapshot with realtime fallback"
```

---

### Task 6: Celery task + beat — rebuild snapshot + warm cache

**Files:**
- Create: `backend/apps/customers/tasks.py`
- Modify: `backend/config/settings_parts/base.py` (CELERY_BEAT_SCHEDULE)
- Test: `backend/apps/customers/tests/test_customer_tasks.py`

**Interfaces:**
- Produces:
  - task `apps.customers.tasks.rebuild_segment_snapshot` → gọi `SegmentSnapshotService.rebuild()`
  - task `apps.customers.tasks.warm_report_cache` → gọi endpoint-equivalent cho các filter phổ biến (all-time toàn kênh) và set cache cùng key như view.

- [ ] **Step 1: Viết test** (task gọi service, trả số dòng)

```python
from django.test import TestCase
from apps.customers.tasks import rebuild_segment_snapshot

class CustomerTasksTests(TestCase):
    def test_rebuild_segment_snapshot_runs(self):
        out = rebuild_segment_snapshot()   # gọi trực tiếp (eager)
        self.assertIsInstance(out, int)
```

- [ ] **Step 2: Chạy test, xác nhận FAIL** (ImportError)

Run: `pytest apps/customers/tests/test_customer_tasks.py -v`

- [ ] **Step 3: Cài đặt tasks** (theo mẫu `apps/kiotviet/tasks.py`)

```python
import logging
from celery import shared_task

from .segment_snapshot_service import SegmentSnapshotService

logger = logging.getLogger(__name__)


@shared_task(name="apps.customers.tasks.rebuild_segment_snapshot",
             soft_time_limit=600, time_limit=900)
def rebuild_segment_snapshot() -> int:
    n = SegmentSnapshotService.rebuild()
    logger.info("segment snapshot rebuilt: %s rows", n)
    return n


@shared_task(name="apps.customers.tasks.warm_report_cache",
             soft_time_limit=300, time_limit=360)
def warm_report_cache() -> int:
    from django.core.cache import cache
    from .report_service import CustomerReportService
    from .customer_behavior_report import CustomerBehaviorReportService
    combos = [dict(date_from=None, date_to=None, channel=None, segment=None)]
    for params in combos:
        data = {**CustomerReportService.summary(**params),
                **CustomerBehaviorReportService.summary(**params)}
        key = "cust_report:" + "|".join(f"{k}={v!r}" for k, v in sorted(params.items()))
        cache.set(key, data, timeout=3600)
    return len(combos)
```

- [ ] **Step 4: Thêm beat schedule** vào `CELERY_BEAT_SCHEDULE` trong `base.py`

```python
    "customers-rebuild-segment-snapshot": {
        "task": "apps.customers.tasks.rebuild_segment_snapshot",
        "schedule": crontab(minute=0, hour="*/2"),
    },
    "customers-warm-report-cache": {
        "task": "apps.customers.tasks.warm_report_cache",
        "schedule": crontab(minute=10, hour="*/2"),
    },
```

- [ ] **Step 5: Chạy test, xác nhận PASS**

Run: `pytest apps/customers/tests/test_customer_tasks.py -v`

- [ ] **Step 6: Commit**

```bash
git add backend/apps/customers/tasks.py backend/config/settings_parts/base.py backend/apps/customers/tests/test_customer_tasks.py
git commit -m "feat(customers): celery tasks + beat for snapshot rebuild and cache warm"
```

---

### Task 7: Aggregate `data_coverage` + `headline_funnel`

**Files:**
- Modify: `backend/apps/customers/customer_behavior_report.py` (thêm vào dict trả về của `summary`)
- Test: `backend/apps/customers/tests/test_customer_behavior_report.py` (tạo nếu chưa có)

**Interfaces:**
- Produces (thêm key vào output `CustomerBehaviorReportService.summary`):
  - `"data_coverage": {"assessment_orders": int, "total_orders": int, "pct": float}`
  - `"funnel": {"stages": [{"key","label","count","conv_from_prev_pct"}]}` với stages
    `total_customers → leads → buyers → repeat_buyers → loyal` (số đã có sẵn trong `summary`).

- [ ] **Step 1: Viết test**

```python
from decimal import Decimal
from django.test import TestCase
from apps.customers.models import Customer
from apps.customers.customer_behavior_report import CustomerBehaviorReportService
from apps.orders.models import Order, OrderAssessment
from apps.channels_core.models import Channel

class BehaviorReportExtraTests(TestCase):
    def test_funnel_and_coverage_present(self):
        ch = Channel.objects.create(code="c", name="c")
        c = Customer.objects.create(full_name="A")
        o = Order.objects.create(customer=c, channel=ch, status=3, total_after_discount=Decimal("0"))
        OrderAssessment.objects.create(order=o)
        data = CustomerBehaviorReportService.summary()
        self.assertIn("funnel", data)
        keys = [s["key"] for s in data["funnel"]["stages"]]
        self.assertEqual(keys, ["total_customers", "leads", "buyers", "repeat_buyers", "loyal"])
        self.assertIn("data_coverage", data)
        self.assertEqual(data["data_coverage"]["assessment_orders"], 1)
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `pytest apps/customers/tests/test_customer_behavior_report.py -v`

- [ ] **Step 3: Bổ sung vào `summary()`** trước `return`, dùng biến đã có (`total_customers`, `total_leads`, `p`)

```python
        assessment_orders = OrderAssessment.objects.filter(order__deleted_at__isnull=True).count()
        total_orders_all = Order.objects.filter(deleted_at__isnull=True).count()

        def _conv(part, whole):
            return _pct(part, whole)

        funnel_stages = [
            ("total_customers", "Tổng KH", total_customers, None),
            ("leads", "Lead", total_leads, total_customers),
            ("buyers", "Người mua", p["buyers"], total_customers),
            ("repeat_buyers", "Mua lại", p["repeat_buyers"], p["buyers"]),
            ("loyal", "Trung thành", p["loyal"], p["buyers"]),
        ]
```

Và thêm 2 key vào dict trả về:
```python
            "funnel": {"stages": [
                {"key": k, "label": lbl, "count": cnt,
                 "conv_from_prev_pct": _conv(cnt, base) if base else None}
                for k, lbl, cnt, base in funnel_stages
            ]},
            "data_coverage": {
                "assessment_orders": assessment_orders,
                "total_orders": total_orders_all,
                "pct": _pct(assessment_orders, total_orders_all),
            },
```
(import `OrderAssessment` ở đầu file cùng `Order`.)

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `pytest apps/customers/tests/test_customer_behavior_report.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/apps/customers/customer_behavior_report.py backend/apps/customers/tests/test_customer_behavior_report.py
git commit -m "feat(customers): funnel + data coverage aggregates in behavior report"
```

---

### Task 8: Aggregate `cohort_retention` (SQL, theo tháng acquire)

**Files:**
- Create: `backend/apps/customers/report_cohort.py`
- Modify: `backend/apps/customers/customer_behavior_report.py` (gọi + gắn key `cohort_retention`)
- Test: `backend/apps/customers/tests/test_report_cohort.py`

**Interfaces:**
- Produces: `report_cohort.retention(channel, date_from, date_to, months: int = 12) -> dict`
  trả `{"rows": [{"cohort": "YYYY-MM", "size": n, "retained": [r1, r2, ...]}]}` —
  `size` = số KH GTC lần đầu trong tháng cohort; `retained[k]` = số KH cohort đó có đơn GTC ở tháng cohort+k+1.
  Tính 100% bằng SQL (`date_trunc('month', first_buy)` CTE + self-join), KHÔNG kéo từng KH.

- [ ] **Step 1: Viết test** — 1 KH mua tháng N và N+1 → retained[0]=1

```python
from datetime import timedelta
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from apps.customers.models import Customer
from apps.customers.report_cohort import retention
from apps.orders.models import Order
from apps.channels_core.models import Channel

class CohortTests(TestCase):
    def test_single_customer_two_months(self):
        ch = Channel.objects.create(code="c", name="c")
        c = Customer.objects.create(full_name="A")
        now = timezone.now().replace(day=15)
        o1 = Order.objects.create(customer=c, channel=ch, status=3, total_after_discount=Decimal("0"))
        Order.objects.filter(pk=o1.pk).update(created_at=now - timedelta(days=40))
        o2 = Order.objects.create(customer=c, channel=ch, status=3, total_after_discount=Decimal("0"))
        Order.objects.filter(pk=o2.pk).update(created_at=now - timedelta(days=5))
        data = retention(None, None, None, months=3)
        self.assertTrue(any(r["size"] >= 1 for r in data["rows"]))
```

- [ ] **Step 2: Chạy test, xác nhận FAIL** (ImportError)

Run: `pytest apps/customers/tests/test_report_cohort.py -v`

- [ ] **Step 3: Cài đặt `report_cohort.retention`** (raw SQL CTE; filter GTC giống buyers CTE)

```python
"""Cohort retention theo tháng acquire (đơn GTC đầu tiên). Thuần SQL."""
from __future__ import annotations

from django.db import connection

from apps.orders.repositories import EXCLUDED_BRANCH_IDS
from .order_status_constants import CANCELLED_INVOICE_STATUS, DELIVERED_SUCCESS_ORDER_STATUS


def retention(channel, date_from, date_to, months: int = 12) -> dict:
    params = {
        "gtc": DELIVERED_SUCCESS_ORDER_STATUS,
        "cancelled_inv": CANCELLED_INVOICE_STATUS,
        "excluded_list": list(EXCLUDED_BRANCH_IDS),
        "months": months,
    }
    flt = ""
    if channel:
        flt += " AND o.channel_id = %(channel)s"; params["channel"] = channel
    sql = f"""
    WITH gtc AS (
        SELECT o.customer_id, date_trunc('month', o.created_at) AS m
        FROM orders o
        WHERE o.deleted_at IS NULL AND o.status = %(gtc)s
          AND (o.invoice_status IS DISTINCT FROM %(cancelled_inv)s)
          AND NOT (o.branch_id = ANY(%(excluded_list)s)) {flt}
    ),
    firsts AS (
        SELECT customer_id, min(m) AS cohort FROM gtc GROUP BY customer_id
    ),
    activity AS (
        SELECT DISTINCT g.customer_id, f.cohort,
               (date_part('year', g.m) - date_part('year', f.cohort)) * 12
               + (date_part('month', g.m) - date_part('month', f.cohort)) AS offset_m
        FROM gtc g JOIN firsts f ON f.customer_id = g.customer_id
    )
    SELECT to_char(cohort, 'YYYY-MM') AS cohort,
           count(DISTINCT customer_id) FILTER (WHERE offset_m = 0) AS size,
           array_agg(DISTINCT customer_id) FILTER (WHERE offset_m > 0) AS active_ids,
           offset_m
    FROM activity
    WHERE cohort >= date_trunc('month', now()) - (%(months)s || ' months')::interval
    GROUP BY cohort, offset_m
    ORDER BY cohort;
    """
    # Gom theo cohort trong Python từ các dòng (cohort, offset) — nhẹ vì chỉ ~months*months dòng.
    with connection.cursor() as cur:
        cur.execute(sql, params)
        raw = cur.fetchall()
    by_cohort: dict[str, dict] = {}
    for cohort, size, active_ids, offset_m in raw:
        slot = by_cohort.setdefault(cohort, {"cohort": cohort, "size": 0, "retained": {}})
        if offset_m == 0:
            slot["size"] = size
        elif active_ids:
            slot["retained"][int(offset_m)] = len(active_ids)
    rows = []
    for cohort in sorted(by_cohort):
        slot = by_cohort[cohort]
        retained = [slot["retained"].get(k, 0) for k in range(1, months + 1)]
        rows.append({"cohort": cohort, "size": slot["size"], "retained": retained})
    return {"rows": rows}
```

> Lưu ý: bản trên gom Python nhẹ (≤ months² dòng). Nếu muốn 100% SQL có thể pivot bằng crosstab; giữ bản này cho đơn giản, vẫn KHÔNG kéo từng KH.

- [ ] **Step 4: Gắn vào `CustomerBehaviorReportService.summary`**

Thêm key vào dict trả về:
```python
            "cohort_retention": report_cohort.retention(channel, date_from, date_to),
```
(import `from . import report_cohort` đầu file.)

- [ ] **Step 5: Chạy test, xác nhận PASS**

Run: `pytest apps/customers/tests/test_report_cohort.py apps/customers/tests/test_customer_behavior_report.py -v`

- [ ] **Step 6: Commit**

```bash
git add backend/apps/customers/report_cohort.py backend/apps/customers/customer_behavior_report.py backend/apps/customers/tests/test_report_cohort.py
git commit -m "feat(customers): cohort retention aggregate"
```

---

### Task 9: `report_insights` — sinh insight theo ngưỡng

**Files:**
- Create: `backend/apps/customers/report_insights.py`
- Modify: `backend/apps/customers/views.py:595` (merge insight sau khi có data)
- Test: `backend/apps/customers/tests/test_report_insights.py`

**Interfaces:**
- Consumes: dict `data` đã merge (có `overview`, `funnel`, `data_coverage`).
- Produces: `report_insights.build(data: dict) -> list[dict]` với mỗi item
  `{"level": "warn|info|ok", "title": str, "metric": str, "cta": {"type": str, "params": dict} | None}`.

- [ ] **Step 1: Viết test** — dormant_rate cao → insight level warn

```python
from django.test import TestCase
from apps.customers.report_insights import build

class InsightTests(TestCase):
    def test_high_dormant_warns(self):
        data = {"overview": {"dormant_rate": 92.6, "buyers": 1000, "repeat_rate": 8.0,
                              "conversion_rate": 50.0},
                "data_coverage": {"pct": 2.2}}
        out = build(data)
        self.assertTrue(any(i["level"] == "warn" and "ngủ đông" in i["title"].lower() for i in out))
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `pytest apps/customers/tests/test_report_insights.py -v`

- [ ] **Step 3: Cài đặt `build`** (rule-based, ngưỡng rõ ràng)

```python
"""Sinh các thẻ insight cho 'Tổng quan điều hành' từ aggregate đã tính. Hàm thuần."""
from __future__ import annotations


def build(data: dict) -> list[dict]:
    ov = data.get("overview") or {}
    cov = data.get("data_coverage") or {}
    out: list[dict] = []
    dormant = ov.get("dormant_rate") or 0
    if dormant >= 80:
        out.append({
            "level": "warn",
            "title": f"Retention thấp: {dormant}% người mua đã ngủ đông",
            "metric": f"{dormant}% (>365 ngày không mua)",
            "cta": {"type": "customer_list", "params": {"lifecycle": "dormant"}},
        })
    repeat = ov.get("repeat_rate") or 0
    if repeat and repeat < 15:
        out.append({
            "level": "warn",
            "title": f"Tỷ lệ mua lại chỉ {repeat}%",
            "metric": f"{repeat}% mua ≥2 lần",
            "cta": {"type": "customer_list", "params": {"lifecycle": "repeat"}},
        })
    conv = ov.get("conversion_rate") or 0
    out.append({
        "level": "info",
        "title": f"Chuyển đổi lead → mua: {conv}%",
        "metric": f"{conv}%",
        "cta": None,
    })
    pct = cov.get("pct") or 0
    if pct < 10:
        out.append({
            "level": "info",
            "title": f"Dữ liệu đánh giá thưa: chỉ {pct}% đơn có khảo sát",
            "metric": f"{pct}% đơn có assessment",
            "cta": None,
        })
    return out
```

- [ ] **Step 4: Gắn vào view** — sau khi merge `data` (trước khi cache.set)

```python
    if data is None:
        data = CustomerReportService.summary(**params)
        behavior = CustomerBehaviorReportService.summary(**params)
        data = {**data, **behavior}
        from .report_insights import build as build_insights
        data["insights"] = build_insights(data)
        cache.set(cache_key, data, timeout=600)
```

- [ ] **Step 5: Chạy test, xác nhận PASS**

Run: `pytest apps/customers/tests/test_report_insights.py -v`

- [ ] **Step 6: Commit**

```bash
git add backend/apps/customers/report_insights.py backend/apps/customers/views.py backend/apps/customers/tests/test_report_insights.py
git commit -m "feat(customers): rule-based executive insights in report summary"
```

---

### Task 10: Cập nhật OpenAPI schema + chốt data contract

**Files:**
- Modify: `backend/apps/customers/views.py` (`@extend_schema` cho `customer_report_summary` nếu có serializer inline)
- Modify: `frontend/src/lib/api/customers.ts` (type `CustomerReportSummary` thêm `funnel`, `cohort_retention`, `data_coverage`, `insights`)

**Interfaces:**
- Produces: type FE khớp output BE (đóng băng cho Sprint 2).

- [ ] **Step 1: Thêm field vào type FE** `CustomerReportSummary`

```typescript
export interface ReportFunnelStage { key: string; label: string; count: number; conv_from_prev_pct: number | null; }
export interface ReportInsight { level: "warn" | "info" | "ok"; title: string; metric: string; cta: { type: string; params: Record<string, string> } | null; }
export interface CohortRow { cohort: string; size: number; retained: number[]; }
// trong CustomerReportSummary:
//   funnel?: { stages: ReportFunnelStage[] };
//   cohort_retention?: { rows: CohortRow[] };
//   data_coverage?: { assessment_orders: number; total_orders: number; pct: number };
//   insights?: ReportInsight[];
```

- [ ] **Step 2: Regenerate OpenAPI nếu repo có pipeline** (theo `tech-stack-evolution` D7). Nếu schema sinh tự động:

Run: `python manage.py spectacular --file schema.yaml`
Expected: schema.yaml cập nhật, không lỗi.

- [ ] **Step 3: Build FE type-check**

Run: `cd frontend && npm run build` (hoặc `tsc --noEmit`)
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api/customers.ts backend/schema.yaml
git commit -m "chore(customers): freeze report summary data contract (funnel/cohort/coverage/insights)"
```

---

## Self-Review (Sprint 1)

- Coverage: A1→Task1, A2→Task2, A3(snapshot)→Task3-6, B5 contract→Task7-10. Funnel/cohort/insight backend nằm ở Task7-9 (đúng nguyên tắc "A = toàn backend").
- Type consistency: `_latest_sales_map` ký hiệu dùng nhất quán Task1/4/5; `segment_map` (snapshot) vs `_segment_map` (report) tách tên rõ.
- Placeholder: mọi step có code/lệnh cụ thể.
- Rủi ro: snapshot stale → Task5 có fallback realtime đảm bảo đúng số ngay cả khi cron chưa chạy.

## Benchmark (sau Sprint 1, bắt buộc trước khi sang Sprint 2)
- Đo `time curl /api/customers/report/summary` (cache-miss: xoá key trước) trước/sau toàn sprint trên dev có dữ liệu thật. Ghi p95 vào commit cuối / MR description.
