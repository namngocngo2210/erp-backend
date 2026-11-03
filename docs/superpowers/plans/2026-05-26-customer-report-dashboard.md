# Customer Report Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm trang dashboard phân tích khách hàng tại `/customers/report`, hiển thị phân bố phân khúc (S1–S7), độ tuổi, nghề nghiệp, phản hồi sản phẩm, mục đích mua (nhóm nhu cầu) và rào cản, có bộ lọc thời gian / kênh / phân khúc.

**Architecture:** Backend thêm endpoint tổng hợp riêng `GET /api/customers/report/summary` (service `CustomerReportService` tính toàn bộ phân bố server-side, tái dùng `classify_segment` + helper của `profile_service`). Frontend thêm trang React self-contained (Recharts), tự fetch + cache, gắn vào nav nhóm Khách hàng, dùng chung quyền `dashboard`.

**Tech Stack:** Django + DRF (ORM aggregation), React 18 + Vite SPA, Recharts 2.13, shadcn-style UI, lucide-react.

**Spec:** `docs/superpowers/specs/2026-05-26-customer-report-dashboard-design.md`

**Đơn vị phân tích:** mỗi *đơn có assessment* trong kỳ. Phân khúc là thuộc tính cấp khách, gán cho từng đơn. Rào cản đếm theo *lượt*. Nhóm nhu cầu đếm distinct (đơn, nhóm).

---

## File Structure

**Backend (`backend/`):**
- Create `apps/customers/age_buckets.py` — hàm thuần parse `age_text` → khoảng tuổi.
- Create `apps/customers/report_service.py` — `CustomerReportService.summary(...)`.
- Modify `apps/customers/views.py` — thêm view `customer_report_summary`.
- Modify `apps/customers/urls.py` — thêm route `report/summary`.
- Create `apps/customers/tests/test_age_buckets.py`
- Create `apps/customers/tests/test_customer_report_service.py`
- Create `apps/customers/tests/test_customer_report_api.py`

**Frontend (`frontend/src/`):**
- Modify `lib/api/customers.ts` — type `CustomerReportSummary` + `getCustomerReportSummary(...)`.
- Modify `lib/routes.ts` — section `customer-report`, path, `requiredPermissionFor(...)`.
- Create `pages/CustomerReportPage.tsx` — trang dashboard self-contained.
- Modify `components/Sidebar.tsx` — nav item + lọc theo permission ánh xạ.
- Modify `App.tsx` — lazy import, render branch, guard dùng `requiredPermissionFor`, page title.
- Modify `i18n/messages.ts` — keys `topnav.tabs.customerReport`, `page.title.customerReport` (vi + en).

---

## Backend test runner (pytest hỏng — dùng Django runner)

`backend/pytest.ini` có BOM nên `pytest` lỗi. Chạy test bằng `manage.py test`. Cần Postgres + Redis tạm:

```bash
docker run -d --name desi-test-pg -e POSTGRES_DB=desi_order -e POSTGRES_USER=desi_order -e POSTGRES_PASSWORD=desi_order -p 5432:5432 public.ecr.aws/docker/library/postgres:16-alpine
docker run -d --name desi-test-redis -p 6379:6379 public.ecr.aws/docker/library/redis:7-alpine redis-server --requirepass 'Desi@2026'
```

Lệnh chạy test (interpreter là `.venv/bin/python`):

```bash
cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 REDIS_PASSWORD='Desi@2026' .venv/bin/python manage.py test <dotted.path> -v2
```

---

## Task 1: Age bucketing helper (thuần, TDD)

**Files:**
- Create: `backend/apps/customers/age_buckets.py`
- Test: `backend/apps/customers/tests/test_age_buckets.py`

- [ ] **Step 1: Viết test thất bại**

```python
# backend/apps/customers/tests/test_age_buckets.py
from django.test import SimpleTestCase

from apps.customers.age_buckets import AGE_BUCKET_LABELS, AGE_BUCKET_ORDER, bucket_age


class BucketAgeTests(SimpleTestCase):
    def test_plain_number_buckets(self):
        self.assertEqual(bucket_age("22"), "18-25")
        self.assertEqual(bucket_age("30"), "26-35")
        self.assertEqual(bucket_age("40"), "36-45")
        self.assertEqual(bucket_age("50"), "46-55")
        self.assertEqual(bucket_age("60"), "55+")

    def test_number_embedded_in_text(self):
        self.assertEqual(bucket_age("28 tuổi"), "26-35")
        self.assertEqual(bucket_age("khoảng 45t"), "36-45")

    def test_boundaries(self):
        self.assertEqual(bucket_age("18"), "18-25")
        self.assertEqual(bucket_age("25"), "18-25")
        self.assertEqual(bucket_age("26"), "26-35")
        self.assertEqual(bucket_age("55"), "46-55")
        self.assertEqual(bucket_age("56"), "55+")

    def test_unparseable_or_out_of_range_is_other(self):
        self.assertEqual(bucket_age(""), "other")
        self.assertEqual(bucket_age("trung niên"), "other")
        self.assertEqual(bucket_age("10"), "other")  # < 18
        self.assertEqual(bucket_age(None), "other")

    def test_labels_and_order_cover_all_codes(self):
        for code in AGE_BUCKET_ORDER:
            self.assertIn(code, AGE_BUCKET_LABELS)
        self.assertEqual(AGE_BUCKET_LABELS["other"], "Khác")
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `cd backend && .venv/bin/python manage.py test apps.customers.tests.test_age_buckets -v2`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.customers.age_buckets'`

- [ ] **Step 3: Viết implementation tối thiểu**

```python
# backend/apps/customers/age_buckets.py
"""Parse age_text (free text) → khoảng tuổi cố định cho báo cáo khách hàng."""
from __future__ import annotations

import re

# (code, lo, hi) — khoảng đóng [lo, hi].
_RANGES = (
    ("18-25", 18, 25),
    ("26-35", 26, 35),
    ("36-45", 36, 45),
    ("46-55", 46, 55),
)
_OVER_CODE = "55+"
_OTHER_CODE = "other"

AGE_BUCKET_ORDER = ["18-25", "26-35", "36-45", "46-55", "55+", "other"]
AGE_BUCKET_LABELS = {
    "18-25": "18-25",
    "26-35": "26-35",
    "36-45": "36-45",
    "46-55": "46-55",
    "55+": "55+",
    "other": "Khác",
}


def bucket_age(age_text: str | None) -> str:
    """Trả về mã khoảng tuổi. Không parse được / ngoài 18+ → 'other'."""
    if not age_text:
        return _OTHER_CODE
    match = re.search(r"\d+", str(age_text))
    if not match:
        return _OTHER_CODE
    age = int(match.group())
    if age > 55:
        return _OVER_CODE
    for code, lo, hi in _RANGES:
        if lo <= age <= hi:
            return code
    return _OTHER_CODE
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `cd backend && .venv/bin/python manage.py test apps.customers.tests.test_age_buckets -v2`
Expected: PASS (5 tests OK)

- [ ] **Step 5: Commit**

```bash
git add backend/apps/customers/age_buckets.py backend/apps/customers/tests/test_age_buckets.py
git commit -m "Add age_text bucketing helper for customer report"
```

---

## Task 2: CustomerReportService.summary (TDD)

**Files:**
- Create: `backend/apps/customers/report_service.py`
- Test: `backend/apps/customers/tests/test_customer_report_service.py`

Bối cảnh mô hình (đã xác minh):
- `Order.assessment` = OneToOne reverse từ `OrderAssessment.order` (related_name="assessment").
- `OrderAssessment`: `age_text` (CharField), `occupation_code`, `post_purchase_rating_code`, `purchase_barrier_codes` (ArrayField), `post_purchase_feedback_text` (TextField default "").
- `OrderNeed.order_assessment` → FK `OrderAssessment`; `OrderNeed.need_type` → `NeedType` có `group` → `NeedGroup(code, name_vi)`.
- Phân khúc: `apps.customers.segmentation.classify_segment(...)`; helper tái dùng: `CustomerProfileService._metrics_map`, `CustomerProfileService._latest_order_map`, `CustomerService._customers_with_problem_signal`.
- `DELIVERED_SUCCESS_ORDER_STATUS = 3` (từ `apps.customers.services`); `EXCLUDED_BRANCH_IDS` và `OrderRepository._apply_created_at_date_filters` từ `apps.orders.repositories`.
- Catalog nhãn: `apps.orders.crm_catalog` có `OCCUPATION_LABELS`, `POST_PURCHASE_RATING_LABELS`, `PURCHASE_BARRIER_LABELS` (dict code→label).

- [ ] **Step 1: Viết test thất bại**

```python
# backend/apps/customers/tests/test_customer_report_service.py
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import Customer, CustomerSegmentCategory
from apps.customers.report_service import CustomerReportService
from apps.customers.segmentation import SEGMENT_CATALOG_SEED
from apps.orders.models import NeedGroup, NeedType, Order, OrderAssessment, OrderNeed


class CustomerReportServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for item in SEGMENT_CATALOG_SEED:
            CustomerSegmentCategory.objects.create(
                code=item["code"], name=item["name"],
                criteria=item.get("criteria", ""), care_priority=item.get("care_priority", ""),
                sort_order=item["sort_order"],
            )
        cls.channel = Channel.objects.create(name="CH-A", code="ch-a")
        cls.other_channel = Channel.objects.create(name="CH-B", code="ch-b")
        cls.group = NeedGroup.objects.create(code="pain_relief", name_vi="Giảm đau nhức", sort_order=10)
        cls.ntype = NeedType.objects.create(code="knee_pain", group=cls.group, name_vi="Đau đầu gối", sort_order=10)

    def _make_order(self, *, customer, channel=None, status=3, created_at=None,
                    age_text="", occupation_code=None, rating_code=None,
                    barrier_codes=None, feedback_text="", total=Decimal("0"),
                    needs=False):
        order = Order.objects.create(
            customer=customer, channel=channel or self.channel, status=status,
            total_after_discount=total,
        )
        if created_at is not None:
            Order.objects.filter(pk=order.pk).update(created_at=created_at)
            order.refresh_from_db()
        assessment = OrderAssessment.objects.create(
            order=order, age_text=age_text, occupation_code=occupation_code,
            post_purchase_rating_code=rating_code,
            purchase_barrier_codes=barrier_codes or [],
            post_purchase_feedback_text=feedback_text,
        )
        if needs:
            OrderNeed.objects.create(order_assessment=assessment, need_type=self.ntype)
        return order

    def test_distributions_count_records(self):
        c1 = Customer.objects.create(full_name="A")
        c2 = Customer.objects.create(full_name="B")
        self._make_order(customer=c1, age_text="30", occupation_code="office",
                         rating_code="good", barrier_codes=["price", "brand"],
                         feedback_text="Tốt lắm", needs=True)
        self._make_order(customer=c2, age_text="50", occupation_code="office",
                         rating_code="poor", barrier_codes=["price"], needs=True)

        data = CustomerReportService.summary()

        self.assertEqual(data["totals"]["total_records"], 2)
        self.assertEqual(data["totals"]["total_customers"], 2)

        occ = {row["code"]: row["count"] for row in data["occupation_distribution"]}
        self.assertEqual(occ["office"], 2)

        age = {row["code"]: row["count"] for row in data["age_distribution"]}
        self.assertEqual(age["26-35"], 1)
        self.assertEqual(age["46-55"], 1)

        rating = {row["code"]: row["count"] for row in data["feedback_rating_distribution"]}
        self.assertEqual(rating["good"], 1)
        self.assertEqual(rating["poor"], 1)

        # Rào cản đếm theo LƯỢT: price xuất hiện 2 lần, brand 1 lần.
        barrier = {row["code"]: row["count"] for row in data["barrier_distribution"]}
        self.assertEqual(barrier["price"], 2)
        self.assertEqual(barrier["brand"], 1)

        # Nhóm nhu cầu: 2 đơn cùng chạm group pain_relief → đếm 2 (distinct theo đơn).
        need = {row["code"]: row["count"] for row in data["need_group_distribution"]}
        self.assertEqual(need["pain_relief"], 2)

        # Chỉ 1 đơn có feedback text.
        self.assertEqual(len(data["feedback_samples"]), 1)
        self.assertEqual(data["feedback_samples"][0]["text"], "Tốt lắm")

    def test_orders_without_assessment_excluded(self):
        c = Customer.objects.create(full_name="C")
        Order.objects.create(customer=c, channel=self.channel, status=3)  # không có assessment
        self._make_order(customer=c, age_text="30", occupation_code="teacher")
        data = CustomerReportService.summary()
        self.assertEqual(data["totals"]["total_records"], 1)

    def test_segment_attribution_and_filter(self):
        # Khách loyal: 2 đơn GTC → S3.
        loyal = Customer.objects.create(full_name="Loyal")
        self._make_order(customer=loyal, occupation_code="office")
        self._make_order(customer=loyal, occupation_code="office")
        # Khách mới mua 1 đơn gần đây → S2.
        newbie = Customer.objects.create(full_name="New")
        self._make_order(customer=newbie, occupation_code="teacher",
                         created_at=timezone.now() - timedelta(days=5))

        data = CustomerReportService.summary()
        seg = {row["code"]: row["count"] for row in data["segment_distribution"]}
        self.assertEqual(seg.get("S3"), 2)  # 2 đơn của khách loyal
        self.assertEqual(seg.get("S2"), 1)

        filtered = CustomerReportService.summary(segment="S3")
        self.assertEqual(filtered["totals"]["total_records"], 2)
        seg2 = {row["code"]: row["count"] for row in filtered["segment_distribution"]}
        self.assertEqual(set(seg2), {"S3"})

    def test_channel_filter(self):
        c = Customer.objects.create(full_name="D")
        self._make_order(customer=c, channel=self.channel, occupation_code="office")
        self._make_order(customer=c, channel=self.other_channel, occupation_code="driver")
        data = CustomerReportService.summary(channel=str(self.channel.id))
        self.assertEqual(data["totals"]["total_records"], 1)
        occ = {row["code"]: row["count"] for row in data["occupation_distribution"]}
        self.assertEqual(occ.get("office"), 1)
        self.assertNotIn("driver", occ)

    def test_empty_returns_zero_totals(self):
        data = CustomerReportService.summary()
        self.assertEqual(data["totals"]["total_records"], 0)
        self.assertEqual(data["totals"]["total_customers"], 0)
        self.assertEqual(data["segment_distribution"], [])
        self.assertEqual(data["feedback_samples"], [])
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 REDIS_PASSWORD='Desi@2026' .venv/bin/python manage.py test apps.customers.tests.test_customer_report_service -v2`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.customers.report_service'`

(Nếu `Channel`/`Order`/`OrderAssessment` yêu cầu field bắt buộc khác trong test factory, bổ sung giá trị tối thiểu cho đúng schema — kiểm tra model trước khi sửa.)

- [ ] **Step 3: Viết implementation**

```python
# backend/apps/customers/report_service.py
"""Tổng hợp số liệu cho dashboard báo cáo khách hàng.

Đơn vị phân tích: mỗi đơn (Order) có assessment trong kỳ. Phân khúc là thuộc tính
cấp khách (tính qua segmentation.classify_segment) được gán cho từng đơn. Rào cản
đếm theo lượt; nhóm nhu cầu đếm distinct theo (đơn, nhóm).
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal

from django.utils import timezone

from apps.orders.crm_catalog import (
    OCCUPATION_LABELS,
    POST_PURCHASE_RATING_LABELS,
    PURCHASE_BARRIER_LABELS,
)
from apps.orders.models import Order, OrderNeed
from apps.orders.repositories import EXCLUDED_BRANCH_IDS, OrderRepository

from . import segmentation
from .age_buckets import AGE_BUCKET_LABELS, AGE_BUCKET_ORDER, bucket_age
from .models import CustomerSegmentCategory
from .profile_service import CustomerProfileService
from .services import CustomerService

UNKNOWN_CODE = "unknown"
UNKNOWN_LABEL = "Chưa xác định"
FEEDBACK_SAMPLE_LIMIT = 20


class CustomerReportService:
    @staticmethod
    def summary(*, date_from: str | None = None, date_to: str | None = None,
                channel: str | None = None, segment: str | None = None) -> dict:
        base = (
            Order.objects.filter(deleted_at__isnull=True, assessment__isnull=False)
            .exclude(branch_id__in=EXCLUDED_BRANCH_IDS)
        )
        base = OrderRepository._apply_created_at_date_filters(base, date_from, date_to)
        if channel:
            base = base.filter(channel_id=channel)

        customer_ids = list(base.values_list("customer_id", flat=True).distinct())
        segment_by_customer = CustomerReportService._segment_map(customer_ids)

        if segment:
            allowed = {cid for cid, code in segment_by_customer.items() if code == segment}
            base = base.filter(customer_id__in=allowed)

        rows = list(
            base.values(
                "id",
                "customer_id",
                "assessment__age_text",
                "assessment__occupation_code",
                "assessment__post_purchase_rating_code",
                "assessment__purchase_barrier_codes",
            )
        )
        total_records = len(rows)
        total_customers = len({r["customer_id"] for r in rows})

        seg_counter: Counter = Counter()
        age_counter: Counter = Counter()
        occ_counter: Counter = Counter()
        rating_counter: Counter = Counter()
        barrier_counter: Counter = Counter()
        for r in rows:
            seg_counter[segment_by_customer.get(r["customer_id"]) or UNKNOWN_CODE] += 1
            age_counter[bucket_age(r["assessment__age_text"])] += 1
            occ_counter[r["assessment__occupation_code"] or UNKNOWN_CODE] += 1
            rating_counter[r["assessment__post_purchase_rating_code"] or UNKNOWN_CODE] += 1
            for code in (r["assessment__purchase_barrier_codes"] or []):
                if code:
                    barrier_counter[code] += 1

        # Nhóm nhu cầu — distinct (đơn, nhóm).
        order_ids = [r["id"] for r in rows]
        need_counter: Counter = Counter()
        need_labels: dict[str, str] = {}
        if order_ids:
            need_rows = (
                OrderNeed.objects.filter(order_assessment__order_id__in=order_ids)
                .values(
                    "order_assessment__order_id",
                    "need_type__group__code",
                    "need_type__group__name_vi",
                )
                .distinct()
            )
            for nr in need_rows:
                code = nr["need_type__group__code"]
                need_counter[code] += 1
                need_labels[code] = nr["need_type__group__name_vi"]

        segment_labels = dict(CustomerSegmentCategory.objects.values_list("code", "name"))
        segment_labels[UNKNOWN_CODE] = UNKNOWN_LABEL

        total_barriers = sum(barrier_counter.values())

        feedback_samples = CustomerReportService._feedback_samples(base)

        return {
            "totals": {"total_records": total_records, "total_customers": total_customers},
            "segment_distribution": CustomerReportService._distribution(
                seg_counter, segment_labels, total_records,
                fixed_order=[item["code"] for item in segmentation.SEGMENT_CATALOG_SEED],
            ),
            "age_distribution": CustomerReportService._distribution(
                age_counter, AGE_BUCKET_LABELS, total_records, fixed_order=AGE_BUCKET_ORDER,
            ),
            "occupation_distribution": CustomerReportService._distribution(
                occ_counter, {**OCCUPATION_LABELS, UNKNOWN_CODE: UNKNOWN_LABEL}, total_records,
            ),
            "feedback_rating_distribution": CustomerReportService._distribution(
                rating_counter, {**POST_PURCHASE_RATING_LABELS, UNKNOWN_CODE: UNKNOWN_LABEL}, total_records,
            ),
            "need_group_distribution": CustomerReportService._distribution(
                need_counter, need_labels, total_records,
            ),
            "barrier_distribution": CustomerReportService._distribution(
                barrier_counter, PURCHASE_BARRIER_LABELS, total_barriers,
            ),
            "feedback_samples": feedback_samples,
        }

    @staticmethod
    def _segment_map(customer_ids: list[int]) -> dict[int, str]:
        if not customer_ids:
            return {}
        metrics = CustomerProfileService._metrics_map(customer_ids)
        problem_ids = CustomerService._customers_with_problem_signal(customer_ids)
        latest = CustomerProfileService._latest_order_map(customer_ids)
        now = timezone.now()
        result: dict[int, str] = {}
        for cid in customer_ids:
            m = metrics.get(cid, {})
            total_orders = m.get("total_orders", 0) or 0
            last = m.get("last_purchase_at")
            days = (now - last).days if last else None
            order = latest.get(cid)
            assessment = getattr(order, "assessment", None) if order else None
            result[cid] = segmentation.classify_segment(
                delivered_count=total_orders,
                total_spent=m.get("total_spent") or Decimal("0"),
                days_since_last_purchase=days,
                has_problem=cid in problem_ids,
                latest_sales_status_code=getattr(assessment, "sales_status_code", None) if assessment else None,
            )
        return result

    @staticmethod
    def _distribution(counter: Counter, label_map: dict, total: int,
                      fixed_order: list[str] | None = None) -> list[dict]:
        def make(code: str) -> dict:
            count = counter[code]
            return {
                "code": code,
                "label": label_map.get(code, code),
                "count": count,
                "percentage": round(count * 100 / total, 1) if total else 0.0,
            }

        items: list[dict] = []
        seen: set[str] = set()
        if fixed_order is not None:
            for code in fixed_order:
                if counter.get(code):
                    items.append(make(code))
                    seen.add(code)
        leftover = sorted((c for c in counter if c not in seen), key=lambda c: (-counter[c], c))
        items.extend(make(code) for code in leftover)
        return items

    @staticmethod
    def _feedback_samples(base) -> list[dict]:
        rows = list(
            base.exclude(assessment__post_purchase_feedback_text="")
            .exclude(assessment__post_purchase_feedback_text__isnull=True)
            .order_by("-created_at")
            .values(
                "created_at",
                "customer__code",
                "assessment__post_purchase_feedback_text",
                "assessment__post_purchase_rating_code",
            )[:FEEDBACK_SAMPLE_LIMIT]
        )
        return [
            {
                "text": r["assessment__post_purchase_feedback_text"],
                "created_at": r["created_at"],
                "customer_code": r["customer__code"],
                "rating_code": r["assessment__post_purchase_rating_code"],
                "rating_label": POST_PURCHASE_RATING_LABELS.get(r["assessment__post_purchase_rating_code"]),
            }
            for r in rows
        ]
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 REDIS_PASSWORD='Desi@2026' .venv/bin/python manage.py test apps.customers.tests.test_customer_report_service -v2`
Expected: PASS (5 tests OK)

- [ ] **Step 5: Commit**

```bash
git add backend/apps/customers/report_service.py backend/apps/customers/tests/test_customer_report_service.py
git commit -m "Add CustomerReportService aggregations for customer report dashboard"
```

---

## Task 3: API view + URL (TDD)

**Files:**
- Modify: `backend/apps/customers/views.py`
- Modify: `backend/apps/customers/urls.py`
- Test: `backend/apps/customers/tests/test_customer_report_api.py`

- [ ] **Step 1: Viết test thất bại**

```python
# backend/apps/customers/tests/test_customer_report_api.py
from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.models import User, UserRole


class CustomerReportApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_requires_dashboard_section(self):
        # Staff không có department → không có quyền dashboard.
        user = User.objects.create_user(username="staff1", password="x", role=UserRole.STAFF)
        self.client.force_authenticate(user=user)
        resp = self.client.get("/api/customers/report/summary")
        self.assertEqual(resp.status_code, 403)

    def test_admin_gets_summary_shape(self):
        admin = User.objects.create_user(username="admin1", password="x", role=UserRole.ADMIN)
        self.client.force_authenticate(user=admin)
        resp = self.client.get("/api/customers/report/summary")
        self.assertEqual(resp.status_code, 200)
        for key in (
            "totals", "segment_distribution", "age_distribution", "occupation_distribution",
            "feedback_rating_distribution", "need_group_distribution", "barrier_distribution",
            "feedback_samples",
        ):
            self.assertIn(key, resp.data)
        self.assertEqual(resp.data["totals"], {"total_records": 0, "total_customers": 0})
```

(Kiểm tra `User.objects.create_user` signature thực tế trong `apps/users/models.py` — nếu cần email/khác thì bổ sung.)

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 REDIS_PASSWORD='Desi@2026' .venv/bin/python manage.py test apps.customers.tests.test_customer_report_api -v2`
Expected: FAIL — 404 (route chưa tồn tại)

- [ ] **Step 3: Thêm view**

Trong `backend/apps/customers/views.py`, thêm import gần các import service hiện có (cạnh `from .profile_service import CustomerProfileService`):

```python
from .report_service import CustomerReportService
```

Thêm view (đặt cuối file, cạnh các view list khác):

```python
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def customer_report_summary(request):
    if not request.user.has_section_access("dashboard"):
        return Response(
            {"detail": "Bạn không có quyền xem báo cáo khách hàng."},
            status=status.HTTP_403_FORBIDDEN,
        )
    data = CustomerReportService.summary(
        date_from=request.query_params.get("date_from", "") or None,
        date_to=request.query_params.get("date_to", "") or None,
        channel=request.query_params.get("channel", "") or None,
        segment=request.query_params.get("segment", "") or None,
    )
    return Response(data)
```

- [ ] **Step 4: Thêm route**

Trong `backend/apps/customers/urls.py`, thêm `customer_report_summary` vào khối import từ `.views` và thêm path (đặt trước `path("<int:customer_id>", ...)` để không bị nuốt bởi pattern động):

```python
    path("report/summary", customer_report_summary, name="customers-report-summary"),
```

- [ ] **Step 5: Chạy test, xác nhận PASS**

Run: `cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 REDIS_PASSWORD='Desi@2026' .venv/bin/python manage.py test apps.customers.tests.test_customer_report_api -v2`
Expected: PASS (2 tests OK)

- [ ] **Step 6: Commit**

```bash
git add backend/apps/customers/views.py backend/apps/customers/urls.py backend/apps/customers/tests/test_customer_report_api.py
git commit -m "Expose GET /api/customers/report/summary endpoint"
```

---

## Task 4: Frontend API client

**Files:**
- Modify: `frontend/src/lib/api/customers.ts`

- [ ] **Step 1: Thêm types + hàm fetch (cuối file)**

```typescript
export interface ReportDistributionItem {
  code: string;
  label: string;
  count: number;
  percentage: number;
}

export interface ReportFeedbackSample {
  text: string;
  created_at: string;
  customer_code: string;
  rating_code: string | null;
  rating_label: string | null;
}

export interface CustomerReportSummary {
  totals: { total_records: number; total_customers: number };
  segment_distribution: ReportDistributionItem[];
  age_distribution: ReportDistributionItem[];
  occupation_distribution: ReportDistributionItem[];
  feedback_rating_distribution: ReportDistributionItem[];
  need_group_distribution: ReportDistributionItem[];
  barrier_distribution: ReportDistributionItem[];
  feedback_samples: ReportFeedbackSample[];
}

export interface CustomerReportFilters {
  dateFrom?: string;
  dateTo?: string;
  channel?: string;
  segment?: string;
}

export async function getCustomerReportSummary(
  filters: CustomerReportFilters = {},
): Promise<CustomerReportSummary> {
  const query = new URLSearchParams();
  if (filters.dateFrom) query.set("date_from", filters.dateFrom);
  if (filters.dateTo) query.set("date_to", filters.dateTo);
  if (filters.channel) query.set("channel", filters.channel);
  if (filters.segment) query.set("segment", filters.segment);
  const qs = query.toString();
  return fetchJson(`${API_BASE}/report/summary${qs ? `?${qs}` : ""}`);
}
```

- [ ] **Step 2: Kiểm tra biên dịch**

Run: `cd frontend && npx tsc --noEmit`
Expected: không có lỗi liên quan `customers.ts` (lỗi tồn sẵn ở file khác — nếu có — bỏ qua, nhưng file này phải sạch).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api/customers.ts
git commit -m "Add customer report summary API client"
```

---

## Task 5: Routing + permission mapping

**Files:**
- Modify: `frontend/src/lib/routes.ts`

Mục tiêu: thêm section `customer-report` (path `/customers/report`) nhưng dùng chung quyền `dashboard`.

- [ ] **Step 1: Thêm section vào union `AppSection`**

Sửa dòng `export type AppSection = ...` thêm `"customer-report"`:

```typescript
export type AppSection = "orders" | "landing-orders" | "customers" | "customer-report" | "loyal-customers" | "resale-tracking" | "dashboard" | "ads-report" | "channels" | "kiotviet" | "facebook-ads" | "users" | "products" | "product-categories" | "audit" | "cron-jobs" | "sync" | "zalo-accounts";
```

- [ ] **Step 2: Thêm path vào `SECTION_TO_PATH`**

Thêm dòng (sau `customers: "/customers",`):

```typescript
  "customer-report": "/customers/report",
```

- [ ] **Step 3: Thêm map quyền + helper (cuối file)**

```typescript
/** Một số section dùng chung quyền của section khác. */
const SECTION_PERMISSION: Partial<Record<AppSection, AppSection>> = {
  "customer-report": "dashboard",
};

/** Section key cần kiểm tra trong allowed_sections cho một section. */
export function requiredPermissionFor(section: AppSection): AppSection {
  return SECTION_PERMISSION[section] ?? section;
}
```

- [ ] **Step 4: Cho `sectionFromPath` nhận `/customers/report`**

Trong `sectionFromPath`, thêm check `startsWith` TRƯỚC dòng exact-match `find` (để `/customers/report` không rơi vào nhánh khác và ổn định nếu sau này có sub-path):

```typescript
  if (normalized === "/customers/report") return "customer-report";
```

Đặt ngay sau dòng `if (normalized.startsWith("/orders/")) return "orders";`.

- [ ] **Step 5: Kiểm tra biên dịch**

Run: `cd frontend && npx tsc --noEmit`
Expected: routes.ts sạch lỗi.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/routes.ts
git commit -m "Add customer-report route reusing dashboard permission"
```

---

## Task 6: i18n keys

**Files:**
- Modify: `frontend/src/i18n/messages.ts`

- [ ] **Step 1: Thêm key tiếng Việt**

Trong khối vi (gần dòng `"topnav.tabs.loyalCustomers": "Khách hàng thân thiết",`) thêm:

```typescript
    "topnav.tabs.customerReport": "Báo cáo khách hàng",
```

Và gần dòng `"page.title.loyalCustomers": "Khách hàng thân thiết",` thêm:

```typescript
    "page.title.customerReport": "Báo cáo khách hàng",
```

- [ ] **Step 2: Thêm key tiếng Anh**

Trong khối en (gần `"topnav.tabs.loyalCustomers": "Loyal Customers",`) thêm:

```typescript
    "topnav.tabs.customerReport": "Customer Report",
```

Và gần `"page.title.customers": "Customers",` thêm:

```typescript
    "page.title.customerReport": "Customer Report",
```

- [ ] **Step 3: Kiểm tra biên dịch**

Run: `cd frontend && npx tsc --noEmit`
Expected: messages.ts sạch lỗi.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/i18n/messages.ts
git commit -m "Add i18n keys for customer report page"
```

---

## Task 7: CustomerReportPage component

**Files:**
- Create: `frontend/src/pages/CustomerReportPage.tsx`

Trang self-contained: tự quản filter state, tự fetch qua `getCustomerReportSummary`, cache đơn giản bằng `useRef`. Dùng Recharts (như `DashboardPage.tsx`). Lấy danh sách kênh từ `/api/channels` qua `fetchJson`; phân khúc từ `getSegmentCategories()`.

- [ ] **Step 1: Tạo component**

```tsx
// frontend/src/pages/CustomerReportPage.tsx
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DateFilter, type DateFilterValue } from "@/components/ui/date-filter";
import {
  getCustomerReportSummary,
  getSegmentCategories,
  type CustomerReportSummary,
  type ReportDistributionItem,
} from "@/lib/api/customers";
import { fetchJson } from "@/services/http";

const CHART_COLORS = ["#3b82f6", "#22c55e", "#f59e0b", "#8b5cf6", "#06b6d4", "#ef4444", "#64748b"];

interface ChannelOption {
  id: number;
  name: string;
}

interface SegmentOption {
  code: string;
  name: string;
}

function toQuery(date: DateFilterValue, channel: string, segment: string) {
  return {
    dateFrom: date.from ?? "",
    dateTo: date.to ?? "",
    channel,
    segment,
  };
}

function DistributionBar({
  title,
  data,
  horizontal = false,
  note,
}: {
  title: string;
  data: ReportDistributionItem[];
  horizontal?: boolean;
  note?: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        {note ? <p className="text-xs text-muted-foreground">{note}</p> : null}
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="py-12 text-center text-sm text-muted-foreground">Không có dữ liệu</p>
        ) : (
          <ResponsiveContainer width="100%" height={Math.max(260, data.length * 40 + 60)}>
            <BarChart
              data={data}
              layout={horizontal ? "vertical" : "horizontal"}
              margin={{ top: 8, right: 24, left: horizontal ? 16 : 0, bottom: 8 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              {horizontal ? (
                <>
                  <XAxis type="number" allowDecimals={false} />
                  <YAxis type="category" dataKey="label" width={130} tick={{ fontSize: 12 }} />
                </>
              ) : (
                <>
                  <XAxis dataKey="label" tick={{ fontSize: 12 }} interval={0} />
                  <YAxis allowDecimals={false} />
                </>
              )}
              <Tooltip
                formatter={(value: number, _name, entry) => [
                  `${value} (${(entry?.payload as ReportDistributionItem)?.percentage ?? 0}%)`,
                  "Số lượng",
                ]}
              />
              <Bar dataKey="count" radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]}>
                {data.map((row, i) => (
                  <Cell key={row.code} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}

function DistributionDonut({ title, data }: { title: string; data: ReportDistributionItem[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="py-12 text-center text-sm text-muted-foreground">Không có dữ liệu</p>
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            <PieChart>
              <Pie
                data={data}
                dataKey="count"
                nameKey="label"
                innerRadius={60}
                outerRadius={100}
                paddingAngle={2}
                label={(entry) => `${entry.label} (${entry.percentage}%)`}
              >
                {data.map((row, i) => (
                  <Cell key={row.code} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip formatter={(value: number) => [`${value}`, "Số lượng"]} />
            </PieChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}

export default function CustomerReportPage() {
  const [dateFilter, setDateFilter] = useState<DateFilterValue>({ option: "all", from: null, to: null });
  const [channel, setChannel] = useState("");
  const [segment, setSegment] = useState("");
  const [channels, setChannels] = useState<ChannelOption[]>([]);
  const [segments, setSegments] = useState<SegmentOption[]>([]);
  const [summary, setSummary] = useState<CustomerReportSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const reqIdRef = useRef(0);

  useEffect(() => {
    fetchJson("/api/channels")
      .then((data: unknown) => {
        const list = Array.isArray(data) ? data : (data as { results?: ChannelOption[] }).results ?? [];
        setChannels(list as ChannelOption[]);
      })
      .catch(() => setChannels([]));
    getSegmentCategories()
      .then((items) => setSegments(items.map((s) => ({ code: s.code, name: s.name }))))
      .catch(() => setSegments([]));
  }, []);

  useEffect(() => {
    const reqId = ++reqIdRef.current;
    setLoading(true);
    getCustomerReportSummary(toQuery(dateFilter, channel, segment))
      .then((data) => {
        if (reqId === reqIdRef.current) setSummary(data);
      })
      .catch(() => {
        if (reqId === reqIdRef.current) setSummary(null);
      })
      .finally(() => {
        if (reqId === reqIdRef.current) setLoading(false);
      });
  }, [dateFilter, channel, segment]);

  const totals = summary?.totals ?? { total_records: 0, total_customers: 0 };
  const samples = summary?.feedback_samples ?? [];

  const selectClass =
    "h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring";

  return (
    <div className="space-y-6 p-1">
      <div className="flex flex-wrap items-end gap-3">
        <DateFilter value={dateFilter} onChange={setDateFilter} />
        <select className={selectClass} value={channel} onChange={(e) => setChannel(e.target.value)}>
          <option value="">Tất cả kênh</option>
          {channels.map((c) => (
            <option key={c.id} value={String(c.id)}>
              {c.name}
            </option>
          ))}
        </select>
        <select className={selectClass} value={segment} onChange={(e) => setSegment(e.target.value)}>
          <option value="">Tất cả phân khúc</option>
          {segments.map((s) => (
            <option key={s.code} value={s.code}>
              {s.code} — {s.name}
            </option>
          ))}
        </select>
        {loading ? <span className="text-sm text-muted-foreground">Đang tải…</span> : null}
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm text-muted-foreground">Lượt đánh giá trong kỳ</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-semibold">{totals.total_records.toLocaleString("vi-VN")}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm text-muted-foreground">Khách hàng (không trùng)</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-semibold">{totals.total_customers.toLocaleString("vi-VN")}</p>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <DistributionDonut title="Phân khúc khách hàng" data={summary?.segment_distribution ?? []} />
        <DistributionBar title="Độ tuổi" data={summary?.age_distribution ?? []} />
        <DistributionBar title="Nghề nghiệp" data={summary?.occupation_distribution ?? []} horizontal />
        <DistributionDonut title="Phản hồi về sản phẩm" data={summary?.feedback_rating_distribution ?? []} />
        <DistributionBar title="Mục đích mua (nhóm nhu cầu)" data={summary?.need_group_distribution ?? []} horizontal />
        <DistributionBar
          title="Rào cản mua hàng"
          data={summary?.barrier_distribution ?? []}
          horizontal
          note="Đếm theo lượt (một khách có thể có nhiều rào cản)."
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Phản hồi gần đây của khách</CardTitle>
        </CardHeader>
        <CardContent>
          {samples.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">Chưa có phản hồi</p>
          ) : (
            <ul className="divide-y">
              {samples.map((s, i) => (
                <li key={`${s.customer_code}-${i}`} className="py-3">
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span>{s.customer_code}</span>
                    <span>{s.rating_label ?? ""}</span>
                  </div>
                  <p className="mt-1 text-sm">{s.text}</p>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
```

- [ ] **Step 2: Đối chiếu API thực tế trước khi tin code**

Kiểm tra các điểm sau và sửa cho khớp nếu lệch (đừng giả định):
- `DateFilterValue` shape thực tế trong `@/components/ui/date-filter` — đặc biệt giá trị khởi tạo `{ option: "all", from: null, to: null }` có đúng field không. Mở file và khớp đúng kiểu khởi tạo + field `from`/`to`.
- `Card`/`CardHeader`/`CardContent`/`CardTitle` import path (`@/components/ui/card`) tồn tại.
- `getSegmentCategories()` trả `SegmentCategory[]` có `code`, `name`.
- Response `/api/channels`: mảng hay `{results}` — đã xử lý cả hai, nhưng xác nhận field `id`/`name`.

- [ ] **Step 3: Kiểm tra biên dịch**

Run: `cd frontend && npx tsc --noEmit`
Expected: `CustomerReportPage.tsx` sạch lỗi.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/CustomerReportPage.tsx
git commit -m "Add CustomerReportPage dashboard component"
```

---

## Task 8: Gắn trang vào App + Sidebar

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`

- [ ] **Step 1: Lazy import trong App.tsx**

Cạnh `const DashboardPage = lazy(() => import("@/pages/DashboardPage"));` thêm:

```typescript
const CustomerReportPage = lazy(() => import("@/pages/CustomerReportPage"));
```

- [ ] **Step 2: Sửa guard quyền dùng `requiredPermissionFor`**

Trong App.tsx, import `requiredPermissionFor` từ `@/lib/routes` (thêm vào import sẵn có của `pathFromSection`/`sectionFromPath`). Sau đó sửa effect kiểm tra quyền (đoạn `const allowed = user.allowed_sections ...; if (!allowed.includes(section)) {`):

```typescript
    const allowed = user.allowed_sections ?? ["orders", "landing-orders", "customers"];
    if (!allowed.includes(requiredPermissionFor(section))) {
```

- [ ] **Step 3: Render trang theo section**

Cạnh `{section === "loyal-customers" && <LoyalCustomersPage />}` thêm:

```tsx
              {section === "customer-report" && <CustomerReportPage />}
```

- [ ] **Step 4: Page title**

Trong chuỗi ternary tính title (đoạn có `section === "loyal-customers" ? ... : section === "dashboard" ? t("page.title.dashboard")`), thêm một nhánh cho `customer-report`, ví dụ ngay trước nhánh `customers`/`loyal-customers`:

```tsx
        : section === "customer-report"
          ? t("page.title.customerReport")
```

(Đặt nhánh sao cho cú pháp ternary hợp lệ — chèn liền mạch vào chuỗi `: section === X ? t(...)` hiện có.)

- [ ] **Step 5: Sidebar — thêm nav item + lọc theo permission ánh xạ**

Trong `frontend/src/components/Sidebar.tsx`:

(a) Cho phép NavItem khai báo quyền dùng chung. Sửa type `NavItem`:

```typescript
type NavItem = {
  key: AppSection;
  labelKey: string;
  icon: LucideIcon;
  requires?: AppSection;
};
```

(Nếu `AppSection` chưa import thì thêm `import { requiredPermissionFor, type AppSection } from "@/lib/routes";` — kiểm tra import hiện có để không trùng.)

(b) Thêm item vào nhóm `customers` (sau item `customers`):

```typescript
      { key: "customer-report", labelKey: "topnav.tabs.customerReport", icon: BarChart3, requires: "dashboard" },
```

(`BarChart3` đã được import sẵn trong file.)

(c) Sửa bộ lọc hiển thị để dùng quyền ánh xạ:

```typescript
      items: group.items.filter((item) => allowedSet.has(item.requires ?? item.key)),
```

- [ ] **Step 6: Kiểm tra biên dịch + build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: build thành công, không lỗi type ở các file đã sửa.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/Sidebar.tsx
git commit -m "Wire customer report page into navigation"
```

---

## Task 9: Xác minh thủ công (verification)

**Files:** không sửa code.

- [ ] **Step 1: Chạy toàn bộ test backend của app customers**

Run: `cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 REDIS_PASSWORD='Desi@2026' .venv/bin/python manage.py test apps.customers -v1`
Expected: chỉ test mới + cũ PASS; lưu ý test `customers aggregate` đã fail sẵn trên master (pre-existing, không phải do thay đổi này).

- [ ] **Step 2: Chạy app, đăng nhập admin, mở `/customers/report`**

Dùng skill `run` hoặc quy trình dev hiện có (frontend `npm run dev`, backend `manage.py runserver`). Kiểm tra:
- Mục "Báo cáo khách hàng" xuất hiện trong nhóm Khách hàng ở sidebar (với user có quyền `dashboard`).
- Trang hiển thị 2 KPI + 6 chart + panel feedback.
- Đổi DateFilter / kênh / phân khúc → số liệu cập nhật.
- User không có quyền `dashboard`: không thấy menu, và truy cập trực tiếp `/customers/report` bị redirect về `/orders`.

- [ ] **Step 3: Cập nhật tiến độ vào spec**

Thêm mục "Đã hoàn thành" vào cuối `docs/superpowers/specs/2026-05-26-customer-report-dashboard-design.md` ghi các task đã làm.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-05-26-customer-report-dashboard-design.md
git commit -m "Note customer report dashboard completion in spec"
```

---

## Self-Review Notes

- **Spec coverage:** phân khúc (Task 2 segment_distribution + filter), độ tuổi (Task 1+2), nghề nghiệp (Task 2), phản hồi SP định lượng (feedback_rating) + định tính (feedback_samples) (Task 2), mục đích mua (need_group, Task 2), rào cản theo lượt (Task 2). Filter date/channel/segment (Task 2+3). Quyền dùng chung dashboard (Task 3 backend + Task 5/8 frontend). Route `/customers/report` (Task 5/8). UI (Task 7). ✅
- **Đơn vị đếm nhất quán:** đơn cho hầu hết khối; rào cản theo lượt (`total_barriers`); nhóm nhu cầu distinct (đơn, nhóm) — đã có test khẳng định.
- **Type khớp:** `CustomerReportSummary`/`ReportDistributionItem` (Task 4) dùng đồng nhất ở Task 7. Backend keys ↔ frontend interface khớp 1-1.
- **Điểm cần đối chiếu khi code (không giả định):** `DateFilterValue` shape (Task 7 Step 2), `User.objects.create_user` signature (Task 3), factory field bắt buộc của `Order`/`OrderAssessment`/`Channel` (Task 2 Step 2). Plan đã ghi rõ phải verify.
