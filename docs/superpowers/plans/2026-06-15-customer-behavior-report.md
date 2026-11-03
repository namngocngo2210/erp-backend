# Customer Behavior Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sửa trang báo cáo khách hàng `/customers/report` thành báo cáo hành vi 2 trạng thái (Trước mua / Sau mua) trên toàn bộ 740k KH, tính từ bảng `orders` (RFM) với `order_assessment` làm lớp phủ.

**Architecture:** Backend thêm 1 module hàm thuần phân loại + 1 service aggregation **thuần SQL** (`count FILTER` trên CTE, không loop toàn tập Python), mở rộng response của endpoint `customer_report_summary` sẵn có với 3 khối mới (`overview`, `pre_purchase`, `post_purchase`). Frontend tái cấu trúc `CustomerReportPage` thành 3 tab dùng `Tabs` shadcn, tái dùng các chart hiện có, mỗi trạng thái có CTA link sang danh sách KH.

**Tech Stack:** Django 5 / DRF, PostgreSQL 16 (raw SQL CTE qua `connection.cursor`), Redis cache, React + TypeScript + recharts + shadcn/ui.

**Spec:** `docs/superpowers/specs/2026-06-15-customer-behavior-report-design.md`

---

## File Structure

**Backend (create):**
- `backend/apps/customers/behavior_classify.py` — hàm thuần + hằng phân loại (lifecycle, lead stage, code groups). Một trách nhiệm: phân loại.
- `backend/apps/customers/customer_behavior_report.py` — `CustomerBehaviorReportService`: aggregation SQL → dict.
- `backend/apps/customers/tests/test_behavior_classify.py`
- `backend/apps/customers/tests/test_customer_behavior_report.py`

**Backend (modify):**
- `backend/apps/customers/views.py:401-409` — `customer_report_summary` gộp thêm khối behavior.
- `backend/apps/customers/tests/test_customer_report_api.py` — assert shape mới.

**Frontend (modify):**
- `frontend/src/lib/api/customers.ts` — thêm types `CustomerBehaviorBlocks` + field vào `CustomerReportSummary`.
- `frontend/src/pages/CustomerReportPage.tsx` — Tabs + render khối mới + CTA.
- `frontend/src/components/report/` — (create) `FunnelStat.tsx` nếu cần; tái dùng `DistributionTable`/`StackedBarChart`.

**Lệnh chạy test backend** (image prod thiếu tests/ruff — chạy container tạm, theo memory `running-backend-tests`):

```bash
cd /home/desi/desi-order
docker inspect desi-web -f '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E '^[A-Z_]+=' | grep -vE '^PATH=|^LANG=|^GPG|^PYTHON' > /tmp/web.env
RUN_PYTEST() { docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c "mkdir -p /tmp/prom_multiproc && rm -f /tmp/prom_multiproc/* && python -m pytest $1 --reuse-db -q"; }
```
Dùng: `RUN_PYTEST "apps/customers/tests/test_behavior_classify.py -v"`.
Ruff: `uv run --no-project --with ruff ruff check backend/apps/customers/<file>.py`.

---

## Task 1: Module phân loại hành vi (pure functions)

**Files:**
- Create: `backend/apps/customers/behavior_classify.py`
- Test: `backend/apps/customers/tests/test_behavior_classify.py`

- [ ] **Step 1: Viết test thất bại**

```python
# backend/apps/customers/tests/test_behavior_classify.py
from decimal import Decimal

from django.test import SimpleTestCase

from apps.customers import behavior_classify as bc


class ClassifyLifecycleTests(SimpleTestCase):
    def test_recency_boundaries(self):
        self.assertEqual(bc.classify_lifecycle(0), bc.POST_ACTIVE)
        self.assertEqual(bc.classify_lifecycle(90), bc.POST_ACTIVE)
        self.assertEqual(bc.classify_lifecycle(91), bc.POST_COOLING)
        self.assertEqual(bc.classify_lifecycle(180), bc.POST_COOLING)
        self.assertEqual(bc.classify_lifecycle(181), bc.POST_AT_RISK)
        self.assertEqual(bc.classify_lifecycle(365), bc.POST_AT_RISK)
        self.assertEqual(bc.classify_lifecycle(366), bc.POST_DORMANT)


class ClassifyLeadStageTests(SimpleTestCase):
    def test_stage_mapping(self):
        self.assertEqual(bc.classify_lead_stage("need_more_consulting"), bc.LEAD_CONSULTING)
        self.assertEqual(bc.classify_lead_stage("scheduled"), bc.LEAD_CONSULTING)
        self.assertEqual(bc.classify_lead_stage("no_answer_2"), bc.LEAD_NO_CONTACT)
        self.assertEqual(bc.classify_lead_stage("not_buying"), bc.LEAD_REJECTED)
        self.assertEqual(bc.classify_lead_stage("spam"), bc.LEAD_SPAM)
        self.assertEqual(bc.classify_lead_stage("created_order"), bc.LEAD_NOT_CONTACTED)
        self.assertEqual(bc.classify_lead_stage(""), bc.LEAD_NOT_CONTACTED)
        self.assertEqual(bc.classify_lead_stage(None), bc.LEAD_NOT_CONTACTED)
        # mã không thuộc nhóm nào (vd 'closed') → mặc định chưa tiếp cận
        self.assertEqual(bc.classify_lead_stage("closed"), bc.LEAD_NOT_CONTACTED)


class IsLoyalTests(SimpleTestCase):
    def test_loyal_by_count_or_spend(self):
        self.assertTrue(bc.is_loyal(2, Decimal("0")))
        self.assertTrue(bc.is_loyal(1, Decimal("2000000")))
        self.assertFalse(bc.is_loyal(1, Decimal("1999999")))
        self.assertFalse(bc.is_loyal(1, None))
```

- [ ] **Step 2: Chạy test xác nhận FAIL**

Run: `RUN_PYTEST "apps/customers/tests/test_behavior_classify.py -v"`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.customers.behavior_classify'`.

- [ ] **Step 3: Viết module**

```python
# backend/apps/customers/behavior_classify.py
"""Phân loại hành vi khách hàng cho báo cáo 2 trạng thái (trước/sau mua).

Hàm thuần — nhận số liệu RFM đã tổng hợp, trả mã trạng thái. Ngưỡng tái dùng từ
apps/customers/segmentation.py để nhất quán với phân khúc S1–S7. Các nhóm mã pipeline
(CONSULTING_CODES...) cũng là single source of truth cho cả SQL builder ở service.
"""
from __future__ import annotations

from decimal import Decimal

from . import segmentation

# --- Sau mua: vòng đời theo recency (đơn GTC cuối, so với now) ---
POST_ACTIVE = "active"
POST_COOLING = "cooling"
POST_AT_RISK = "at_risk"
POST_DORMANT = "dormant"

ACTIVE_MAX_DAYS = 90
COOLING_MAX_DAYS = 180
AT_RISK_MAX_DAYS = 365

POST_LIFECYCLE_LABELS = {
    POST_ACTIVE: "Đang hoạt động",
    POST_COOLING: "Nguội",
    POST_AT_RISK: "Nguy cơ rời",
    POST_DORMANT: "Ngủ đông / Mất",
}
POST_LIFECYCLE_ORDER = [POST_ACTIVE, POST_COOLING, POST_AT_RISK, POST_DORMANT]

# --- Trước mua: bước phễu theo sales_status_code đơn mới nhất ---
LEAD_NOT_CONTACTED = "not_contacted"
LEAD_CONSULTING = "consulting"
LEAD_NO_CONTACT = "no_contact"
LEAD_REJECTED = "rejected"
LEAD_SPAM = "spam"

LEAD_STAGE_LABELS = {
    LEAD_NOT_CONTACTED: "Chưa tiếp cận",
    LEAD_CONSULTING: "Đang tư vấn",
    LEAD_NO_CONTACT: "Không liên hệ được",
    LEAD_REJECTED: "Từ chối",
    LEAD_SPAM: "Rác / Trùng",
}
LEAD_STAGE_ORDER = [
    LEAD_NOT_CONTACTED, LEAD_CONSULTING, LEAD_NO_CONTACT, LEAD_REJECTED, LEAD_SPAM,
]

CONSULTING_CODES = ("need_more_consulting", "scheduled", "call_back_later", "closed_scheduled_date")
NO_CONTACT_CODES = ("no_answer_1", "no_answer_2", "no_answer_3", "unreachable", "wrong_number")
REJECTED_CODES = ("not_buying", "cancelled")
SPAM_CODES = ("spam", "duplicate")

_STAGE_BY_CODE = {
    **{c: LEAD_CONSULTING for c in CONSULTING_CODES},
    **{c: LEAD_NO_CONTACT for c in NO_CONTACT_CODES},
    **{c: LEAD_REJECTED for c in REJECTED_CODES},
    **{c: LEAD_SPAM for c in SPAM_CODES},
}


def classify_lifecycle(days_since_last_purchase: int) -> str:
    if days_since_last_purchase <= ACTIVE_MAX_DAYS:
        return POST_ACTIVE
    if days_since_last_purchase <= COOLING_MAX_DAYS:
        return POST_COOLING
    if days_since_last_purchase <= AT_RISK_MAX_DAYS:
        return POST_AT_RISK
    return POST_DORMANT


def classify_lead_stage(sales_status_code: str | None) -> str:
    code = (sales_status_code or "").strip()
    return _STAGE_BY_CODE.get(code, LEAD_NOT_CONTACTED)


def is_loyal(delivered_count: int, total_spent: Decimal | int | float | None) -> bool:
    spent = Decimal(total_spent) if total_spent is not None else Decimal("0")
    return (
        delivered_count >= segmentation.LOYAL_MIN_DELIVERED
        or spent >= segmentation.LOYAL_TOTAL_SPENT_THRESHOLD
    )
```

- [ ] **Step 4: Chạy test xác nhận PASS**

Run: `RUN_PYTEST "apps/customers/tests/test_behavior_classify.py -v"`
Expected: PASS (9 assertions trong 3 test).

- [ ] **Step 5: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/customers/behavior_classify.py backend/apps/customers/tests/test_behavior_classify.py
git add backend/apps/customers/behavior_classify.py backend/apps/customers/tests/test_behavior_classify.py
git commit -m "feat(customers): add behavior classify helpers for 2-state report"
```

---

## Task 2: Service aggregation SQL (`CustomerBehaviorReportService`)

**Files:**
- Create: `backend/apps/customers/customer_behavior_report.py`
- Test: `backend/apps/customers/tests/test_customer_behavior_report.py`

**Response shape (hợp đồng cố định):**

```python
{
  "overview": {
    "total_customers": int, "buyers": int, "leads": int,
    "conversion_rate": float,   # buyers / total_customers * 100, 1 chữ số
    "repeat_rate": float,       # repeat_buyers / buyers * 100
    "dormant_rate": float,      # dormant / buyers * 100
    "avg_order_value": float,   # total_monetary / total_orders (đồng)
  },
  "pre_purchase": {
    "total_leads": int,
    "stage_distribution": [ {"code","label","count","percentage"}, ... ],  # theo LEAD_STAGE_ORDER
  },
  "post_purchase": {
    "buyers": int,
    "lifecycle_distribution": [ {"code","label","count","percentage"}, ... ],  # theo POST_LIFECYCLE_ORDER
    "repeat_split": [ {"code","label","count","percentage"}, ... ],  # first_time / repeat
    "loyal_count": int, "problem_count": int,
    "total_orders": int, "total_spent": float,
  },
}
```

- [ ] **Step 1: Viết test thất bại**

```python
# backend/apps/customers/tests/test_customer_behavior_report.py
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.customer_behavior_report import CustomerBehaviorReportService
from apps.customers.models import Customer
from apps.orders.models import Order, OrderAssessment


class CustomerBehaviorReportServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(name="CH-A", code="ch-a")
        now = timezone.now()

        def buyer(days_ago, freq=1, total=Decimal("100000")):
            c = Customer.objects.create(full_name="b")
            for _ in range(freq):
                o = Order.objects.create(customer=c, channel=cls.channel, status=3,
                                         total_after_discount=total)
                Order.objects.filter(pk=o.pk).update(created_at=now - timedelta(days=days_ago))
            return c

        # 1 active, 1 cooling, 1 at_risk, 1 dormant
        cls.c_active = buyer(10)
        cls.c_cooling = buyer(120)
        cls.c_at_risk = buyer(250)
        cls.c_dormant = buyer(400)
        # 1 repeat + loyal (>=2 đơn GTC)
        cls.c_repeat = buyer(10, freq=2)
        # 2 leads: 1 đang tư vấn (có order chưa GTC + assessment), 1 không order
        cls.lead_consulting = Customer.objects.create(full_name="l1")
        lo = Order.objects.create(customer=cls.lead_consulting, channel=cls.channel, status=1)
        OrderAssessment.objects.create(order=lo, sales_status_code="need_more_consulting")
        cls.lead_bare = Customer.objects.create(full_name="l2")

    def test_overview_counts(self):
        data = CustomerBehaviorReportService.summary()
        ov = data["overview"]
        self.assertEqual(ov["total_customers"], 7)
        self.assertEqual(ov["buyers"], 5)
        self.assertEqual(ov["leads"], 2)

    def test_lifecycle_distribution(self):
        data = CustomerBehaviorReportService.summary()
        by_code = {d["code"]: d["count"] for d in data["post_purchase"]["lifecycle_distribution"]}
        self.assertEqual(by_code["active"], 2)   # c_active + c_repeat
        self.assertEqual(by_code["cooling"], 1)
        self.assertEqual(by_code["at_risk"], 1)
        self.assertEqual(by_code["dormant"], 1)

    def test_repeat_and_loyal(self):
        data = CustomerBehaviorReportService.summary()
        self.assertEqual(data["post_purchase"]["loyal_count"], 1)
        split = {d["code"]: d["count"] for d in data["post_purchase"]["repeat_split"]}
        self.assertEqual(split["repeat"], 1)
        self.assertEqual(split["first_time"], 4)

    def test_lead_stage_distribution(self):
        data = CustomerBehaviorReportService.summary()
        by_code = {d["code"]: d["count"] for d in data["pre_purchase"]["stage_distribution"]}
        self.assertEqual(by_code["consulting"], 1)
        self.assertEqual(by_code["not_contacted"], 1)  # lead_bare (không order)
```

- [ ] **Step 2: Chạy test xác nhận FAIL**

Run: `RUN_PYTEST "apps/customers/tests/test_customer_behavior_report.py -v"`
Expected: FAIL — `ModuleNotFoundError ... customer_behavior_report`.

- [ ] **Step 3: Viết service**

```python
# backend/apps/customers/customer_behavior_report.py
"""Aggregation thuần SQL cho báo cáo khách hàng 2 trạng thái.

Tất cả số liệu lấy bằng count(*) FILTER trên CTE — KHÔNG kéo từng KH về Python.
Đơn vị = customer. Ranh giới sau-mua = có ≥1 đơn GTC (status=3, invoice_status<>2,
loại EXCLUDED_BRANCH_IDS) đồng bộ CustomerProfileService._metrics_map.
"""
from __future__ import annotations

from django.db import connection
from django.utils import timezone

from apps.orders.repositories import EXCLUDED_BRANCH_IDS

from . import behavior_classify as bc
from .order_status_constants import CANCELLED_INVOICE_STATUS, DELIVERED_SUCCESS_ORDER_STATUS
from .models import Customer
from .customer_loyalty_service import CustomerLoyaltyService

_ACTIVE = bc.ACTIVE_MAX_DAYS
_COOLING = bc.COOLING_MAX_DAYS
_AT_RISK = bc.AT_RISK_MAX_DAYS


def _pct(part: int, whole: int) -> float:
    return round(part * 100 / whole, 1) if whole else 0.0


class CustomerBehaviorReportService:
    @staticmethod
    def summary(*, date_from: str | None = None, date_to: str | None = None,
                channel: str | None = None, segment: str | None = None) -> dict:
        now = timezone.now()
        excluded = list(EXCLUDED_BRANCH_IDS)
        params: dict = {
            "now": now, "excluded": excluded,
            "gtc": DELIVERED_SUCCESS_ORDER_STATUS, "cancelled_inv": CANCELLED_INVOICE_STATUS,
        }
        # Bộ lọc tuỳ chọn áp lên orders (created_at trong kỳ + channel).
        order_filter = ""
        if channel:
            order_filter += " AND o.channel_id = %(channel)s"
            params["channel"] = channel
        if date_from:
            order_filter += " AND o.created_at >= %(date_from)s"
            params["date_from"] = date_from
        if date_to:
            order_filter += " AND o.created_at < (%(date_to)s::date + interval '1 day')"
            params["date_to"] = date_to

        total_customers = Customer.objects.filter(deleted_at__isnull=True).count()

        # --- SAU MUA: RFM theo người mua, đếm bucket bằng FILTER ---
        post_sql = f"""
        WITH buyers AS (
            SELECT o.customer_id,
                   count(*)                              AS freq,
                   coalesce(sum(o.total_after_discount), 0) AS monetary,
                   max(o.created_at)                     AS last_buy
            FROM orders o
            WHERE o.deleted_at IS NULL
              AND o.status = %(gtc)s
              AND (o.invoice_status IS DISTINCT FROM %(cancelled_inv)s)
              AND (o.branch_id IS NULL OR o.branch_id <> ALL(%(excluded)s))
              {order_filter}
            GROUP BY o.customer_id
        )
        SELECT
            count(*) AS buyers,
            count(*) FILTER (WHERE last_buy >= %(now)s - interval '{_ACTIVE} days') AS active,
            count(*) FILTER (WHERE last_buy <  %(now)s - interval '{_ACTIVE} days'
                              AND last_buy >= %(now)s - interval '{_COOLING} days') AS cooling,
            count(*) FILTER (WHERE last_buy <  %(now)s - interval '{_COOLING} days'
                              AND last_buy >= %(now)s - interval '{_AT_RISK} days') AS at_risk,
            count(*) FILTER (WHERE last_buy <  %(now)s - interval '{_AT_RISK} days') AS dormant,
            count(*) FILTER (WHERE freq >= 2) AS repeat_buyers,
            count(*) FILTER (WHERE freq = 1)  AS first_time,
            count(*) FILTER (WHERE freq >= 2 OR monetary >= 2000000) AS loyal,
            coalesce(sum(freq), 0)     AS total_orders,
            coalesce(sum(monetary), 0) AS total_spent
        FROM buyers;
        """
        with connection.cursor() as cur:
            cur.execute(post_sql, params)
            row = cur.fetchone()
        cols = ["buyers", "active", "cooling", "at_risk", "dormant", "repeat_buyers",
                "first_time", "loyal", "total_orders", "total_spent"]
        p = dict(zip(cols, row))

        # --- TRƯỚC MUA: phễu lead theo sales_status đơn mới nhất ---
        pre_sql = f"""
        WITH latest AS (
            SELECT DISTINCT ON (o.customer_id) o.customer_id, oa.sales_status_code AS code
            FROM orders o
            LEFT JOIN order_assessments oa ON oa.order_id = o.id
            WHERE o.deleted_at IS NULL {order_filter}
            ORDER BY o.customer_id, o.created_at DESC
        ),
        leads AS (
            SELECT l.code FROM latest l
            WHERE NOT EXISTS (
                SELECT 1 FROM orders b
                WHERE b.customer_id = l.customer_id AND b.deleted_at IS NULL
                  AND b.status = %(gtc)s
                  AND (b.invoice_status IS DISTINCT FROM %(cancelled_inv)s)
                  AND (b.branch_id IS NULL OR b.branch_id <> ALL(%(excluded)s))
            )
        )
        SELECT
            count(*) AS leads_with_orders,
            count(*) FILTER (WHERE code = ANY(%(consulting)s)) AS consulting,
            count(*) FILTER (WHERE code = ANY(%(no_contact)s)) AS no_contact,
            count(*) FILTER (WHERE code = ANY(%(rejected)s))   AS rejected,
            count(*) FILTER (WHERE code = ANY(%(spam)s))       AS spam
        FROM leads;
        """
        params.update({
            "consulting": list(bc.CONSULTING_CODES), "no_contact": list(bc.NO_CONTACT_CODES),
            "rejected": list(bc.REJECTED_CODES), "spam": list(bc.SPAM_CODES),
        })
        with connection.cursor() as cur:
            cur.execute(pre_sql, params)
            lrow = cur.fetchone()
        leads_with_orders, consulting, no_contact, rejected, spam = lrow
        total_leads = total_customers - p["buyers"]
        # Chưa tiếp cận = lead không có order + lead có order nhưng không rơi nhóm nào.
        contacted = consulting + no_contact + rejected + spam
        not_contacted = total_leads - contacted

        # --- Có vấn đề (set nhỏ, đếm) ---
        problem_count = CustomerBehaviorReportService._problem_count(channel, date_from, date_to)

        return {
            "overview": {
                "total_customers": total_customers,
                "buyers": p["buyers"], "leads": total_leads,
                "conversion_rate": _pct(p["buyers"], total_customers),
                "repeat_rate": _pct(p["repeat_buyers"], p["buyers"]),
                "dormant_rate": _pct(p["dormant"], p["buyers"]),
                "avg_order_value": round(float(p["total_spent"]) / p["total_orders"], 0)
                                   if p["total_orders"] else 0.0,
            },
            "pre_purchase": {
                "total_leads": total_leads,
                "stage_distribution": _dist([
                    (bc.LEAD_NOT_CONTACTED, max(not_contacted, 0)),
                    (bc.LEAD_CONSULTING, consulting),
                    (bc.LEAD_NO_CONTACT, no_contact),
                    (bc.LEAD_REJECTED, rejected),
                    (bc.LEAD_SPAM, spam),
                ], bc.LEAD_STAGE_LABELS, total_leads),
            },
            "post_purchase": {
                "buyers": p["buyers"],
                "lifecycle_distribution": _dist([
                    (bc.POST_ACTIVE, p["active"]), (bc.POST_COOLING, p["cooling"]),
                    (bc.POST_AT_RISK, p["at_risk"]), (bc.POST_DORMANT, p["dormant"]),
                ], bc.POST_LIFECYCLE_LABELS, p["buyers"]),
                "repeat_split": _dist([
                    ("first_time", p["first_time"]), ("repeat", p["repeat_buyers"]),
                ], {"first_time": "Mua lần đầu", "repeat": "Mua lại"}, p["buyers"]),
                "loyal_count": p["loyal"], "problem_count": problem_count,
                "total_orders": p["total_orders"], "total_spent": float(p["total_spent"]),
            },
        }

    @staticmethod
    def _problem_count(channel, date_from, date_to) -> int:
        """Số KH có tín hiệu vấn đề. Tái dùng helper sẵn có (giới hạn theo người mua thì tốn,
        nên đếm trực tiếp qua queryset distinct customer)."""
        from django.db.models import Q
        from apps.orders.models import Order
        from . import segmentation
        qs = Order.objects.filter(deleted_at__isnull=True)
        if channel:
            qs = qs.filter(channel_id=channel)
        if date_from:
            qs = qs.filter(created_at__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        qs = qs.filter(
            Q(status__in=segmentation.PROBLEM_ORDER_STATUSES)
            | Q(assessment__sales_status_code__in=segmentation.PROBLEM_SALES_STATUS_CODES)
            | Q(assessment__delivery_business_status_code__in=segmentation.PROBLEM_DELIVERY_STATUS_CODES)
            | Q(assessment__post_purchase_rating_code__in=segmentation.PROBLEM_POST_PURCHASE_RATING_CODES)
        )
        return qs.values("customer_id").distinct().count()


def _dist(pairs, labels, total) -> list[dict]:
    return [
        {"code": code, "label": labels.get(code, code), "count": count,
         "percentage": _pct(count, total)}
        for code, count in pairs
    ]
```

- [ ] **Step 4: Chạy test xác nhận PASS**

Run: `RUN_PYTEST "apps/customers/tests/test_customer_behavior_report.py -v"`
Expected: PASS (4 test). Nếu FAIL về branch null / invoice_status, kiểm lại điều kiện SQL khớp mô tả trên.

- [ ] **Step 5: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/customers/customer_behavior_report.py backend/apps/customers/tests/test_customer_behavior_report.py
git add backend/apps/customers/customer_behavior_report.py backend/apps/customers/tests/test_customer_behavior_report.py
git commit -m "feat(customers): add SQL aggregation service for behavior report"
```

---

## Task 3: Gộp khối behavior vào endpoint + cache Redis

**Files:**
- Modify: `backend/apps/customers/views.py` (hàm `customer_report_summary`, dòng ~401-409)
- Modify: `backend/apps/customers/tests/test_customer_report_api.py`

- [ ] **Step 1: Viết test thất bại (shape mới + cache)**

Thêm vào `test_customer_report_api.py` trong class `CustomerReportApiTests`:

```python
    def test_summary_includes_behavior_blocks(self):
        from apps.users.test_helpers import make_admin
        admin = make_admin("admin_beh")
        self.client.force_authenticate(user=admin)
        resp = self.client.get("/api/customers/report/summary")
        self.assertEqual(resp.status_code, 200)
        for key in ("overview", "pre_purchase", "post_purchase"):
            self.assertIn(key, resp.data)
        self.assertIn("total_customers", resp.data["overview"])
        self.assertIn("lifecycle_distribution", resp.data["post_purchase"])
        self.assertIn("stage_distribution", resp.data["pre_purchase"])
```

- [ ] **Step 2: Chạy test xác nhận FAIL**

Run: `RUN_PYTEST "apps/customers/tests/test_customer_report_api.py::CustomerReportApiTests::test_summary_includes_behavior_blocks -v"`
Expected: FAIL — `KeyError/AssertionError: 'overview' not in response`.

- [ ] **Step 3: Sửa view — gộp behavior + cache 10 phút**

Sửa `customer_report_summary` thành (giữ nguyên decorator quyền `require_section("dashboard")`):

```python
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("dashboard")])
def customer_report_summary(request):
    from django.core.cache import cache
    from apps.customers.customer_behavior_report import CustomerBehaviorReportService

    params = dict(
        date_from=request.query_params.get("date_from", "") or None,
        date_to=request.query_params.get("date_to", "") or None,
        channel=request.query_params.get("channel", "") or None,
        segment=request.query_params.get("segment", "") or None,
    )
    cache_key = "cust_report:" + "|".join(f"{k}={v}" for k, v in sorted(params.items()))
    data = cache.get(cache_key)
    if data is None:
        data = CustomerReportService.summary(**params)
        behavior = CustomerBehaviorReportService.summary(**params)
        data.update(behavior)  # thêm overview / pre_purchase / post_purchase
        cache.set(cache_key, data, timeout=600)
    return Response(data)
```

- [ ] **Step 4: Chạy test xác nhận PASS + không vỡ test cũ**

Run: `RUN_PYTEST "apps/customers/tests/test_customer_report_api.py -v"`
Expected: PASS toàn bộ (test shape cũ + test mới).

- [ ] **Step 5: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/customers/views.py
git add backend/apps/customers/views.py backend/apps/customers/tests/test_customer_report_api.py
git commit -m "feat(customers): merge behavior blocks into report summary + cache"
```

---

## Task 4: Frontend types + fetch

**Files:**
- Modify: `frontend/src/lib/api/customers.ts` (interface `CustomerReportSummary` ~dòng 515)

- [ ] **Step 1: Thêm types**

Thêm trước `export interface CustomerReportSummary {`:

```typescript
export interface BehaviorOverview {
  total_customers: number;
  buyers: number;
  leads: number;
  conversion_rate: number;
  repeat_rate: number;
  dormant_rate: number;
  avg_order_value: number;
}
export interface PrePurchaseBlock {
  total_leads: number;
  stage_distribution: ReportDistributionItem[];
}
export interface PostPurchaseBlock {
  buyers: number;
  lifecycle_distribution: ReportDistributionItem[];
  repeat_split: ReportDistributionItem[];
  loyal_count: number;
  problem_count: number;
  total_orders: number;
  total_spent: number;
}
```

Thêm vào trong `CustomerReportSummary` (cuối interface, đều optional để không vỡ build nếu BE chưa deploy):

```typescript
  overview?: BehaviorOverview;
  pre_purchase?: PrePurchaseBlock;
  post_purchase?: PostPurchaseBlock;
```

- [ ] **Step 2: Build kiểm tra type**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS (không lỗi type mới).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api/customers.ts
git commit -m "feat(customers-fe): add behavior report types"
```

---

## Task 5: Frontend — Tabs + render 3 khối

**Files:**
- Modify: `frontend/src/pages/CustomerReportPage.tsx`

- [ ] **Step 1: Đổi default date filter sang toàn thời gian**

Báo cáo hành vi là ảnh chụp toàn tập → default không nên là `last_30_days`. Sửa `buildDefaultDateFilter`:

```typescript
function buildDefaultDateFilter(): DateFilterValue {
  return { option: "all_time", from: "", to: "" };
}
```
(Nếu `DateFilterValue`/`getDateRange` chưa có option `all_time`, để `from`/`to` rỗng và bỏ gọi `getDateRange` — kiểm `@/components/ui/date-filter` cho giá trị hợp lệ; mục tiêu: mặc định không lọc ngày.)

- [ ] **Step 2: Bọc nội dung trong Tabs**

Thêm import:
```typescript
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
```

Bọc khối render hiện có (từ sau `<Card>` filter, tức các `<ReportSection>`) vào 3 tab. Cấu trúc JSX:

```tsx
<Tabs defaultValue="overview" className="space-y-4">
  <TabsList>
    <TabsTrigger value="overview">Tổng quan</TabsTrigger>
    <TabsTrigger value="pre">Trước mua (Lead)</TabsTrigger>
    <TabsTrigger value="post">Sau mua (Khách hàng)</TabsTrigger>
  </TabsList>

  <TabsContent value="overview" className="space-y-4">
    {summary?.overview && <OverviewKpis ov={summary.overview} />}
  </TabsContent>

  <TabsContent value="pre" className="space-y-4">
    {summary?.pre_purchase && (
      <ReportSection title="Phễu Lead theo trạng thái tư vấn">
        <DistributionBar title="Bước pipeline" data={summary.pre_purchase.stage_distribution} />
        <DistributionTable title="Chi tiết" data={summary.pre_purchase.stage_distribution} />
      </ReportSection>
    )}
    <ReportSection title="Lý do chưa chốt / rào cản">
      <DistributionBar title="Lý do chưa chốt" data={summary?.not_closed_reason_distribution ?? []} />
      <DistributionBar title="Rào cản mua hàng" data={summary?.barrier_distribution ?? []} />
    </ReportSection>
  </TabsContent>

  <TabsContent value="post" className="space-y-4">
    {summary?.post_purchase && (
      <ReportSection title="Vòng đời khách hàng (RFM)">
        <DistributionBar title="Vòng đời theo recency" data={summary.post_purchase.lifecycle_distribution} />
        <DistributionDonut title="Mua lần đầu vs Mua lại" data={summary.post_purchase.repeat_split} />
      </ReportSection>
    )}
    {/* các ReportSection persona hiện có (Nhân khẩu học, nhu cầu, phản hồi…) chuyển vào đây */}
  </TabsContent>
</Tabs>
```

Di chuyển toàn bộ `<ReportSection>` persona đang có (Nhân khẩu học, occupation, pain, lifestyle, feedback, crosstabs…) vào trong `TabsContent value="post"` (chân dung = người đã mua). KPI card "Lượt đánh giá trong kỳ" giữ ở tab Tổng quan.

- [ ] **Step 3: Thêm component `OverviewKpis`**

Thêm trong cùng file (gần `DistributionBar`):

```tsx
function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm text-muted-foreground">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold text-foreground">{value}</div>
        {sub && <div className="text-xs text-muted-foreground mt-1">{sub}</div>}
      </CardContent>
    </Card>
  );
}

function OverviewKpis({ ov }: { ov: NonNullable<CustomerReportSummary["overview"]> }) {
  const fmt = (n: number) => n.toLocaleString("vi-VN");
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
      <Kpi label="Tổng khách hàng" value={fmt(ov.total_customers)} />
      <Kpi label="Đã mua" value={fmt(ov.buyers)} sub={`Chuyển đổi ${ov.conversion_rate}%`} />
      <Kpi label="Lead (chưa mua)" value={fmt(ov.leads)} />
      <Kpi label="Tỷ lệ mua lại" value={`${ov.repeat_rate}%`} />
      <Kpi label="Tỷ lệ ngủ đông" value={`${ov.dormant_rate}%`} sub=">365 ngày" />
      <Kpi label="AOV" value={`${fmt(Math.round(ov.avg_order_value))}đ`} />
    </div>
  );
}
```

- [ ] **Step 4: Build + kiểm tra**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: PASS. Mở `/customers/report` (qua môi trường local) xác nhận 3 tab hiển thị, tab Tổng quan có 6 KPI, tab Sau mua có vòng đời + persona.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/CustomerReportPage.tsx
git commit -m "feat(customers-fe): restructure report into 3 behavior tabs"
```

---

## Task 6: CTA điều hướng sang danh sách KH lọc sẵn

**Files:**
- Modify: `frontend/src/pages/CustomerReportPage.tsx`

> Mục tiêu: mỗi trạng thái có nút mở `/customers` với query lọc tương ứng (vd ngủ đông). Trang `/customers` đã hỗ trợ filter `segment`/`sales_status` (xem `CustomerProfileService.list_profiles`). Map trạng thái → filter khả dụng; nếu danh sách KH chưa hỗ trợ filter "lifecycle", chỉ thêm CTA cho các trạng thái đã có filter (vd `segment=S4` cho ngủ đông) và ghi chú phần còn lại là cải tiến sau.

- [ ] **Step 1: Xác minh filter danh sách KH hỗ trợ**

Run: `grep -n "segment\|sales_status\|lifecycle" frontend/src/pages/CustomerListPage.tsx frontend/src/lib/api/customers.ts | head`
Ghi lại các filter query param trang `/customers` chấp nhận.

- [ ] **Step 2: Thêm nút CTA trên bar vòng đời**

Trong `TabsContent value="post"`, dưới `DistributionBar` vòng đời, thêm hàng nút dùng `react-router` `Link` (kiểm import sẵn có; nếu chưa, `import { Link } from "react-router-dom";`). Chỉ map các trạng thái có filter tương ứng đã xác minh ở Step 1, ví dụ:

```tsx
<div className="flex flex-wrap gap-2">
  <Link to="/customers?segment=S4" className="text-sm text-primary underline">
    → Mở danh sách KH ngủ đông để chạy reactivation
  </Link>
</div>
```

(Map đầy đủ: Active→S2/S3 tuỳ filter có sẵn; nếu không có param phù hợp thì bỏ qua, KHÔNG bịa param.)

- [ ] **Step 3: Build + commit**

```bash
cd frontend && npx tsc --noEmit && npm run build
git add frontend/src/pages/CustomerReportPage.tsx
git commit -m "feat(customers-fe): add CTA links from report states to customer list"
```

---

## Task 7: Đo hiệu năng & index (xác minh)

**Files:** không sửa code trừ khi cần thêm index.

- [ ] **Step 1: EXPLAIN ANALYZE 2 query chính trên DB thật**

Chạy `post_sql` và `pre_sql` (thay param thật) qua:
```bash
docker exec desi-postgres psql -U desi -d desi -c "EXPLAIN ANALYZE <post_sql>"
```
Expected: dùng index trên `orders(status)` / `orders(customer_id)`; thời gian < ~2s không cache.

- [ ] **Step 2: Nếu thiếu index → thêm migration index-only**

Nếu `EXPLAIN` cho thấy seq scan đắt trên `orders(status, customer_id)`, tạo migration:
```bash
cd backend && python manage.py makemigrations orders --name add_orders_status_customer_idx --empty
```
Thêm `AddIndex` `models.Index(fields=["status", "customer_id"], name="ord_status_customer_idx")` (dùng `AddIndexConcurrently` + `atomic=False` nếu chạy prod nóng). Chỉ làm nếu Step 1 chứng minh cần.

- [ ] **Step 3: Commit (nếu có index)**

```bash
git add backend/apps/orders/migrations/*
git commit -m "perf(orders): add status+customer index for behavior report"
```

---

## Self-Review notes (đã kiểm)

- **Spec coverage:** §3 đơn vị/ranh giới → Task 2; §4.1 pipeline lead → Task 1+2; §4.2 lifecycle+flags → Task 1+2; §5 tabs/CTA → Task 5+6; §6.2 SQL no-loop → Task 2; §6.3 date filter → Task 2 (order_filter) + Task 5 (default all-time); §6.4 cache+index → Task 3+7; §7 test → Task 1-3.
- **Type consistency:** keys `overview/pre_purchase/post_purchase`, `lifecycle_distribution`, `stage_distribution`, `repeat_split` thống nhất giữa BE (Task 2/3) và FE (Task 4/5). Mã trạng thái (`active/cooling/at_risk/dormant`, `not_contacted/...`) khớp `behavior_classify` (Task 1).
- **Lỗ hổng dữ liệu sau-mua** (rating/feedback rỗng) — ngoài phạm vi, đã ghi ở spec §8.

---

## Sprint note — kết quả thực thi (2026-06-15)

**Đã hoàn thành 7/7 task** (TDD, 2-stage review mỗi task), nhánh `feat/customer-behavior-report`.

- **Backend:** `behavior_classify.py` (4 test), `customer_behavior_report.py` (5 test, SQL `count FILTER` thuần, không loop toàn tập), endpoint gộp 3 khối + cache Redis 600s. Toàn bộ test backend pass.
- **Frontend:** types + 3 tab (Tổng quan / Trước mua / Sau mua) trên `Tabs` tùy biến của repo, default date = toàn thời gian (để lộ khối ngủ đông), tái dùng chart sẵn có. `tsc` clean, build OK.
- **CTA (Task 6):** trang danh sách KH `/crm/customers` **chưa đọc filter từ URL** → chỉ thêm link chung + ghi chú. *Future work:* thêm hỗ trợ query-param filter (vd `?segment=S4`) cho trang danh sách để deep-link theo trạng thái.

**Task 7 — Hiệu năng (kết luận: KHÔNG thêm index).**
`EXPLAIN ANALYZE` trên prod (no cache):
- Post-purchase aggregate: ~2.0s — Seq Scan (đọc 421k/513k = 82% bảng → seq scan là tối ưu, index không giúp).
- Pre-purchase (DISTINCT ON + anti-join): ~1.56s — chi phí chính là sort tràn disk 13MB cho DISTINCT ON.
- Index phù hợp **đã tồn tại**: `order_cust_recent_idx (customer_id, created_at DESC) WHERE deleted_at IS NULL` và `ord_delivered_cust_cr_idx (customer_id, created_at) WHERE status=3`. Planner cố tình chọn seq scan cho full-table aggregation.
- → Không thêm index mới (thừa + write-amplification trên bảng orders ghi nóng). ~1.5–2s chưa cache là chấp nhận được với cache 10'. Đúng tinh thần CLAUDE.md "index chuẩn nhưng không lạm dụng".

**Lưu ý vận hành:** lifecycle là ảnh chụp `now`; date filter áp cho kỳ phát sinh đơn (spec §6.3). Báo cáo cache 600s — dữ liệu mới phản ánh sau ≤10 phút.
