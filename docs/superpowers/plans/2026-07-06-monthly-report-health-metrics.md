# Monthly Customer Report Health Metrics — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mở rộng báo cáo docx KH hàng tháng (`generate_monthly_customer_report`) với 7 mảng chỉ số sức khỏe tập KH tính từ đơn GTC; nội dung assessment cũ chuyển xuống phụ lục.

**Architecture:** Module mới `report_doc/health.py` chứa 5 query SQL thuần anchor theo tháng báo cáo (KHÔNG anchor now()) + dataclass kết quả; `charts_health.py` vẽ 4 chart matplotlib; `sections_health.py`/`sections_appendix.py` dựng section docx; `health_insights.py` sinh nhận xét rule-based thuần; `docx_builder.py` chỉ còn orchestrate. Spec: `docs/monthly-customer-report-health-metrics.md`.

**Tech Stack:** Django 5 + raw SQL (psycopg), python-docx, matplotlib (Agg), pytest-django.

## Global Constraints

- **Worktree:** mọi file dưới `c:/Users/MYPC/Desktop/workspace/desi-order-wt-report-docx` (branch `feat/monthly-report-health-metrics`). ĐỪNG tự tạo branch mới, đừng đụng checkout chính.
- **Filter GTC đồng nhất** (copy verbatim): `o.deleted_at IS NULL AND o.status = %(gtc)s AND (o.invoice_status IS DISTINCT FROM %(cancelled_inv)s) AND NOT (o.branch_id = ANY(%(excluded_list)s))` với `gtc=3`, `cancelled_inv=2`, `excluded_list=list(EXCLUDED_BRANCH_IDS)` (= `["1000000139"]`, kiểu **string** — `Order.branch_id` là CharField).
- Mọi query mới: **1 lượt SQL**, bọc `statement_timeout(getattr(settings, "REPORT_QUERY_TIMEOUT_SECONDS", 0))` từ `apps.common.db_guards`.
- File < 300 dòng. Import tuyệt đối `from apps.customers...` cho module ngoài `report_doc`, tương đối `from .` trong package.
- Commit message tiếng Anh, ngắn, **không** dòng Co-Authored-By.
- **Lệnh test** (Git Bash, từ bất kỳ đâu — path tuyệt đối; DB riêng tránh test_desi hỏng):

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file c:/Users/MYPC/Desktop/workspace/desi-order-wt-report-docx/.env \
  --env-file c:/Users/MYPC/Desktop/workspace/desi-order-wt-report-docx/backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -e POSTGRES_DB=desi_rpthealth \
  -v "c:/Users/MYPC/Desktop/workspace/desi-order-wt-report-docx/backend:/app" -w /app \
  desi-order-web-test:latest sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <PATHS> -q'
```

- KHÔNG bao giờ gọi `cache.clear()` trong test (FLUSHDB vào Redis chung).
- Không có migration nào trong feature này (không đổi model).

---

### Task 1: `health.py` — dataclasses + `kpi_series`

**Files:**
- Create: `backend/apps/customers/report_doc/health.py`
- Test: `backend/apps/customers/tests/test_report_doc_health.py`

**Interfaces:**
- Produces: `MonthKpi` (fields: `month: date, revenue: float, orders: int, buyers: int, new_customers: int, repeat_buyers: int, revenue_new: float`; properties: `aov, repeat_rate, revenue_returning, buyers_returning, pct_revenue_returning`), `kpi_series(end_month: date, months: int = 13) -> list[MonthKpi]` (ascending, pad tháng trống bằng 0, phần tử cuối = tháng báo cáo).
- Ghi chú thiết kế: `MonthKpi.revenue_new` gộp luôn nhu cầu "new vs returning" (spec §2.2) — KHÔNG cần dataclass `MonthSplit` riêng (một query nuôi cả §3.1–3.3).

- [ ] **Step 1: Viết test fail**

```python
# backend/apps/customers/tests/test_report_doc_health.py
from datetime import date, datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.common.business_constants import EXCLUDED_BRANCH_IDS
from apps.customers.models import Customer
from apps.customers.report_doc import health
from apps.orders.models import Order


def _dt(y, m, d, h=9):
    return datetime(y, m, d, h, tzinfo=timezone.get_current_timezone())


class HealthQueryBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(name="CH-H", code="ch-h")

    def _gtc(self, customer, created, total="100", status=3, invoice_status=None,
             branch_id=""):
        order = Order.objects.create(
            customer=customer, channel=self.channel, status=status,
            invoice_status=invoice_status, branch_id=branch_id,
            total_after_discount=Decimal(total))
        Order.objects.filter(pk=order.pk).update(created_at=created)
        return order

    def _customer(self, name="KH"):
        return Customer.objects.create(full_name=name)


class KpiSeriesTests(HealthQueryBase):
    def test_kpi_series_new_repeat_and_padding(self):
        a = self._customer("A")  # mới trong 06/2026, mua 2 đơn (repeat buyer)
        self._gtc(a, _dt(2026, 6, 5), total="100")
        self._gtc(a, _dt(2026, 6, 20), total="50")
        b = self._customer("B")  # cũ: đơn đầu 2025, quay lại 06/2026
        self._gtc(b, _dt(2025, 1, 10), total="70")
        self._gtc(b, _dt(2026, 6, 10), total="30")

        series = health.kpi_series(date(2026, 6, 1), months=13)
        assert len(series) == 13
        assert series[0].month == date(2025, 6, 1)
        cur = series[-1]
        assert cur.month == date(2026, 6, 1)
        assert cur.revenue == 180.0          # 100+50+30
        assert cur.orders == 3
        assert cur.buyers == 2
        assert cur.new_customers == 1        # chỉ A
        assert cur.repeat_buyers == 1        # A có 2 đơn trong tháng
        assert cur.revenue_new == 150.0      # doanh thu của A
        assert cur.revenue_returning == 30.0
        assert cur.buyers_returning == 1
        assert round(cur.repeat_rate, 1) == 50.0
        assert cur.aov == 60.0
        # tháng trống pad 0
        assert series[-2].orders == 0 and series[-2].revenue == 0.0

    def test_kpi_series_excludes_non_gtc(self):
        c = self._customer("C")
        self._gtc(c, _dt(2026, 6, 5), status=1)                    # chưa GTC
        self._gtc(c, _dt(2026, 6, 6), invoice_status=2)            # hóa đơn hủy
        self._gtc(c, _dt(2026, 6, 7), branch_id=EXCLUDED_BRANCH_IDS[0])
        cur = health.kpi_series(date(2026, 6, 1), months=2)[-1]
        assert cur.orders == 0 and cur.buyers == 0
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run (thay `<PATHS>` trong lệnh test toàn cục): `apps/customers/tests/test_report_doc_health.py -q`
Expected: FAIL/ERROR `ModuleNotFoundError`/`AttributeError` (chưa có `health.py`).

- [ ] **Step 3: Implement `health.py`**

```python
# backend/apps/customers/report_doc/health.py
"""Chỉ số sức khỏe tập KH cho báo cáo docx hàng tháng.

Anchor theo THÁNG BÁO CÁO (không anchor now()) → chạy lại cho tháng cũ vẫn
reproducible. Mỗi hàm = 1 lượt SQL, filter GTC đồng nhất report_trends, bọc
statement_timeout (bài học runaway query).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db import connection
from django.utils import timezone

from apps.common.business_constants import EXCLUDED_BRANCH_IDS
from apps.common.db_guards import statement_timeout
from apps.customers.behavior_classify import (
    ACTIVE_MAX_DAYS, AT_RISK_MAX_DAYS, COOLING_MAX_DAYS,
    POST_ACTIVE, POST_AT_RISK, POST_COOLING, POST_DORMANT,
)
from apps.customers.order_status_constants import (
    CANCELLED_INVOICE_STATUS, DELIVERED_SUCCESS_ORDER_STATUS,
)

from .trend import month_floor

# Điều kiện GTC chuẩn — giữ verbatim đồng bộ report_trends/report_cohort.
# customer_id IS NOT NULL: các chỉ số này đếm theo KHÁCH, đơn không gắn khách bỏ qua.
_GTC_WHERE = """
        o.deleted_at IS NULL
        AND o.status = %(gtc)s
        AND (o.invoice_status IS DISTINCT FROM %(cancelled_inv)s)
        AND NOT (o.branch_id = ANY(%(excluded_list)s))
        AND o.customer_id IS NOT NULL
"""


def _base_params() -> dict:
    return {
        "gtc": DELIVERED_SUCCESS_ORDER_STATUS,
        "cancelled_inv": CANCELLED_INVOICE_STATUS,
        "excluded_list": list(EXCLUDED_BRANCH_IDS),
    }


def _aware(d: date) -> datetime:
    return timezone.make_aware(datetime(d.year, d.month, d.day),
                               timezone.get_current_timezone())


def _run(sql: str, params: dict) -> list[tuple]:
    timeout = getattr(settings, "REPORT_QUERY_TIMEOUT_SECONDS", 0)
    with statement_timeout(timeout), connection.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


@dataclass(frozen=True)
class MonthKpi:
    month: date
    revenue: float
    orders: int
    buyers: int
    new_customers: int
    repeat_buyers: int
    revenue_new: float

    @property
    def aov(self) -> float:
        return self.revenue / self.orders if self.orders else 0.0

    @property
    def repeat_rate(self) -> float:
        return self.repeat_buyers * 100.0 / self.buyers if self.buyers else 0.0

    @property
    def revenue_returning(self) -> float:
        return self.revenue - self.revenue_new

    @property
    def buyers_returning(self) -> int:
        return self.buyers - self.new_customers

    @property
    def pct_revenue_returning(self) -> float:
        return self.revenue_returning * 100.0 / self.revenue if self.revenue else 0.0


def kpi_series(end_month: date, months: int = 13) -> list[MonthKpi]:
    """KPI theo tháng, ascending, pad tháng trống. 13 tháng ⇒ [0] = cùng kỳ năm trước.

    new_customers: KH có đơn GTC ĐẦU TIÊN (toàn lịch sử) rơi vào tháng đó.
    repeat_buyers: KH có >=2 đơn GTC trong tháng. revenue_new: doanh thu từ KH mới.
    """
    end_first = month_floor(end_month)
    start_first = end_first - relativedelta(months=months - 1)
    params = {**_base_params(),
              "start": _aware(start_first),
              "end_exclusive": _aware(end_first + relativedelta(months=1))}
    sql = f"""
    WITH gtc AS (
        SELECT o.customer_id, o.total_after_discount,
               date_trunc('month', o.created_at) AS m
        FROM orders o
        WHERE {_GTC_WHERE}
          AND o.created_at >= %(start)s AND o.created_at < %(end_exclusive)s
    ),
    firsts AS (
        SELECT o.customer_id, date_trunc('month', min(o.created_at)) AS first_m
        FROM orders o
        WHERE {_GTC_WHERE}
        GROUP BY o.customer_id
    ),
    per_cm AS (
        SELECT g.m, g.customer_id, count(*) AS cnt, sum(g.total_after_discount) AS rev
        FROM gtc g
        GROUP BY g.m, g.customer_id
    )
    SELECT p.m,
           sum(p.rev)                                             AS revenue,
           sum(p.cnt)                                             AS orders,
           count(*)                                               AS buyers,
           count(*) FILTER (WHERE f.first_m = p.m)                AS new_customers,
           count(*) FILTER (WHERE p.cnt >= 2)                     AS repeat_buyers,
           coalesce(sum(p.rev) FILTER (WHERE f.first_m = p.m), 0) AS revenue_new
    FROM per_cm p
    JOIN firsts f ON f.customer_id = p.customer_id
    GROUP BY p.m
    ORDER BY p.m
    """
    by_month = {}
    for r in _run(sql, params):
        by_month[r[0].date().replace(day=1)] = r
    out: list[MonthKpi] = []
    cursor = start_first
    while cursor <= end_first:
        r = by_month.get(cursor)
        if r is None:
            out.append(MonthKpi(cursor, 0.0, 0, 0, 0, 0, 0.0))
        else:
            out.append(MonthKpi(cursor, float(r[1]), int(r[2]), int(r[3]),
                                int(r[4]), int(r[5]), float(r[6])))
        cursor += relativedelta(months=1)
    return out
```

- [ ] **Step 4: Chạy lại test, xác nhận PASS**

Run: `apps/customers/tests/test_report_doc_health.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-report-docx && \
git add backend/apps/customers/report_doc/health.py backend/apps/customers/tests/test_report_doc_health.py && \
git commit -m "feat(report-doc): kpi_series health query for monthly docx"
```

---

### Task 2: `health.lifecycle_at` — vòng đời + value-at-risk as-of ngày bất kỳ

**Files:**
- Modify: `backend/apps/customers/report_doc/health.py` (append)
- Test: `backend/apps/customers/tests/test_report_doc_health.py` (append)

**Interfaces:**
- Consumes: `_GTC_WHERE`, `_base_params`, `_aware`, `_run` (Task 1).
- Produces: `LifecycleSnapshot` (fields `ref_date: date, counts: dict[str, int]` key = `POST_ACTIVE/POST_COOLING/POST_AT_RISK/POST_DORMANT`, `value_at_risk: float`; property `total: int`), `lifecycle_at(ref_date: date) -> LifecycleSnapshot`.

- [ ] **Step 1: Viết test fail** (append vào test file)

```python
class LifecycleAtTests(HealthQueryBase):
    def test_buckets_and_value_at_risk(self):
        from apps.customers.behavior_classify import (
            POST_ACTIVE, POST_AT_RISK, POST_COOLING, POST_DORMANT)
        ref = date(2026, 6, 30)
        self._gtc(self._customer("act"), _dt(2026, 6, 20), total="10")    # 10 ngày → active
        self._gtc(self._customer("cool"), _dt(2026, 3, 22), total="20")   # ~100 ngày → cooling
        self._gtc(self._customer("risk"), _dt(2025, 12, 12), total="40")  # ~200 ngày → at_risk
        self._gtc(self._customer("dorm"), _dt(2025, 5, 26), total="80")   # ~400 ngày → dormant
        snap = health.lifecycle_at(ref)
        assert snap.counts == {POST_ACTIVE: 1, POST_COOLING: 1,
                               POST_AT_RISK: 1, POST_DORMANT: 1}
        assert snap.value_at_risk == 120.0   # 40 + 80
        assert snap.total == 4

    def test_reproducible_for_older_ref(self):
        from apps.customers.behavior_classify import POST_ACTIVE, POST_DORMANT
        self._gtc(self._customer("x"), _dt(2025, 5, 26), total="80")
        old = health.lifecycle_at(date(2025, 6, 30))   # 35 ngày sau đơn → active
        new = health.lifecycle_at(date(2026, 6, 30))   # ~400 ngày → dormant
        assert old.counts[POST_ACTIVE] == 1 and old.value_at_risk == 0.0
        assert new.counts[POST_DORMANT] == 1 and new.value_at_risk == 80.0
```

- [ ] **Step 2: Chạy test, xác nhận FAIL** — `AttributeError: ... 'lifecycle_at'`.

- [ ] **Step 3: Implement** (append vào `health.py`)

```python
@dataclass(frozen=True)
class LifecycleSnapshot:
    ref_date: date
    counts: dict[str, int]
    value_at_risk: float  # tổng chi tiêu lịch sử của at_risk + dormant

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def lifecycle_at(ref_date: date) -> LifecycleSnapshot:
    """Bucket vòng đời theo ngày-từ-đơn-GTC-cuối tính đến CUỐI ngày ref_date."""
    params = {**_base_params(),
              "ref": _aware(ref_date + relativedelta(days=1)),
              "active": ACTIVE_MAX_DAYS, "cooling": COOLING_MAX_DAYS,
              "at_risk": AT_RISK_MAX_DAYS}
    sql = f"""
    WITH lifetime AS (
        SELECT o.customer_id, max(o.created_at) AS last_at,
               sum(o.total_after_discount) AS spent
        FROM orders o
        WHERE {_GTC_WHERE}
          AND o.created_at < %(ref)s
        GROUP BY o.customer_id
    )
    SELECT
        count(*) FILTER (WHERE last_at >= %(ref)s - make_interval(days => %(active)s)),
        count(*) FILTER (WHERE last_at <  %(ref)s - make_interval(days => %(active)s)
                           AND last_at >= %(ref)s - make_interval(days => %(cooling)s)),
        count(*) FILTER (WHERE last_at <  %(ref)s - make_interval(days => %(cooling)s)
                           AND last_at >= %(ref)s - make_interval(days => %(at_risk)s)),
        count(*) FILTER (WHERE last_at <  %(ref)s - make_interval(days => %(at_risk)s)),
        coalesce(sum(spent) FILTER (
            WHERE last_at < %(ref)s - make_interval(days => %(cooling)s)), 0)
    FROM lifetime
    """
    row = _run(sql, params)[0]
    counts = {POST_ACTIVE: int(row[0]), POST_COOLING: int(row[1]),
              POST_AT_RISK: int(row[2]), POST_DORMANT: int(row[3])}
    return LifecycleSnapshot(ref_date=ref_date, counts=counts,
                             value_at_risk=float(row[4]))
```

- [ ] **Step 4: Chạy test, xác nhận PASS** — 4 passed (cả Task 1).

- [ ] **Step 5: Commit** — `git add -A backend/apps/customers && git commit -m "feat(report-doc): lifecycle_at snapshot query"`

---

### Task 3: `health.repurchase_cycle` + `health.pareto`

**Files:**
- Modify: `backend/apps/customers/report_doc/health.py` (append)
- Test: `backend/apps/customers/tests/test_report_doc_health.py` (append)

**Interfaces:**
- Produces: `CycleStats` (`median_days/p25_days/p75_days: float | None, n_pairs: int`), `repurchase_cycle(end_month: date, window_months: int = 24) -> CycleStats`; `ParetoStats` (`total_customers: int, total_revenue: float, top10_share: float, top20_share: float` — share là % doanh thu trailing-12M), `pareto(end_month: date, window_months: int = 12) -> ParetoStats`.

- [ ] **Step 1: Viết test fail** (append)

```python
class RepurchaseCycleTests(HealthQueryBase):
    def test_median_of_gaps(self):
        c = self._customer("cyc")
        self._gtc(c, _dt(2026, 5, 1))
        self._gtc(c, _dt(2026, 5, 11))   # gap 10 ngày
        self._gtc(c, _dt(2026, 5, 31))   # gap 20 ngày
        stats = health.repurchase_cycle(date(2026, 6, 1))
        assert stats.n_pairs == 2
        assert stats.median_days == 15.0
        assert stats.p25_days == 12.5 and stats.p75_days == 17.5

    def test_empty_returns_none(self):
        stats = health.repurchase_cycle(date(2026, 6, 1))
        assert stats.n_pairs == 0 and stats.median_days is None


class ParetoTests(HealthQueryBase):
    def test_top10_share(self):
        # LƯU Ý: doanh thu các KH nhỏ phải KHÁC NHAU hoặc chấp nhận ngữ nghĩa tie của
        # cume_dist (cả nhóm tie nhận cd của phần tử cuối) — ở đây dùng 910/50/5×8.
        self._gtc(self._customer("big"), _dt(2026, 5, 1), total="910")
        self._gtc(self._customer("second"), _dt(2026, 5, 1), total="50")
        for i in range(8):
            self._gtc(self._customer(f"s{i}"), _dt(2026, 5, 2), total="5")
        stats = health.pareto(date(2026, 6, 1))
        assert stats.total_customers == 10
        assert stats.total_revenue == 1000.0
        assert stats.top10_share == 91.0            # KH "big": cume_dist 0.1
        assert stats.top20_share == 96.0            # big + second = 960
```

- [ ] **Step 2: Chạy test, xác nhận FAIL** — `AttributeError`.

- [ ] **Step 3: Implement** (append vào `health.py`)

```python
@dataclass(frozen=True)
class CycleStats:
    median_days: float | None
    p25_days: float | None
    p75_days: float | None
    n_pairs: int


def repurchase_cycle(end_month: date, window_months: int = 24) -> CycleStats:
    """Median/p25/p75 số ngày giữa 2 đơn GTC liên tiếp của cùng KH.

    Lấy các cặp có đơn SAU rơi trong ``window_months`` tháng trước cuối tháng
    báo cáo (đơn trước được nhìn toàn lịch sử để không cụt gap đầu cửa sổ).
    """
    end_exclusive = month_floor(end_month) + relativedelta(months=1)
    params = {**_base_params(),
              "end_exclusive": _aware(end_exclusive),
              "window_start": _aware(end_exclusive - relativedelta(months=window_months))}
    sql = f"""
    WITH gtc AS (
        SELECT o.customer_id, o.created_at
        FROM orders o
        WHERE {_GTC_WHERE}
          AND o.created_at < %(end_exclusive)s
    ),
    gaps AS (
        SELECT created_at,
               extract(epoch FROM created_at - lag(created_at) OVER (
                   PARTITION BY customer_id ORDER BY created_at)) / 86400.0 AS gap_days
        FROM gtc
    )
    SELECT percentile_cont(0.5)  WITHIN GROUP (ORDER BY gap_days),
           percentile_cont(0.25) WITHIN GROUP (ORDER BY gap_days),
           percentile_cont(0.75) WITHIN GROUP (ORDER BY gap_days),
           count(*)
    FROM gaps
    WHERE gap_days IS NOT NULL AND created_at >= %(window_start)s
    """
    row = _run(sql, params)[0]
    to_f = lambda v: float(v) if v is not None else None  # noqa: E731
    return CycleStats(median_days=to_f(row[0]), p25_days=to_f(row[1]),
                      p75_days=to_f(row[2]), n_pairs=int(row[3]))


@dataclass(frozen=True)
class ParetoStats:
    total_customers: int
    total_revenue: float
    top10_share: float
    top20_share: float


def pareto(end_month: date, window_months: int = 12) -> ParetoStats:
    """% doanh thu trailing-``window_months`` thuộc top 10% / 20% KH chi nhiều nhất."""
    end_exclusive = month_floor(end_month) + relativedelta(months=1)
    params = {**_base_params(),
              "start": _aware(end_exclusive - relativedelta(months=window_months)),
              "end_exclusive": _aware(end_exclusive)}
    sql = f"""
    WITH rev AS (
        SELECT o.customer_id, sum(o.total_after_discount) AS r
        FROM orders o
        WHERE {_GTC_WHERE}
          AND o.created_at >= %(start)s AND o.created_at < %(end_exclusive)s
        GROUP BY o.customer_id
    ),
    ranked AS (
        SELECT r, cume_dist() OVER (ORDER BY r DESC) AS cd FROM rev
    )
    SELECT count(*), coalesce(sum(r), 0),
           coalesce(sum(r) FILTER (WHERE cd <= 0.10), 0),
           coalesce(sum(r) FILTER (WHERE cd <= 0.20), 0)
    FROM ranked
    """
    row = _run(sql, params)[0]
    total = float(row[1])
    share = lambda part: float(part) * 100.0 / total if total else 0.0  # noqa: E731
    return ParetoStats(total_customers=int(row[0]), total_revenue=total,
                       top10_share=share(row[2]), top20_share=share(row[3]))
```

- [ ] **Step 4: Chạy test, xác nhận PASS** — 7 passed.

- [ ] **Step 5: Commit** — `git commit -am "feat(report-doc): repurchase cycle and pareto queries"`

---

### Task 4: `HealthContext` + `collect()`

**Files:**
- Modify: `backend/apps/customers/report_doc/health.py` (append)
- Test: `backend/apps/customers/tests/test_report_doc_health.py` (append)

**Interfaces:**
- Consumes: `apps.customers.report_cohort.retention(None, None, None) -> {"rows": [{"cohort": "YYYY-MM", "size": int, "retained": [int,...]}]}`; `apps.customers.channel_quality.compute() -> {"channels": [{"channel_id", "channel_name", "acquired", "repeat_pct", "orders_per_customer", "revenue_per_customer", "dormant_pct"}]}`.
- Produces: `HealthContext` (frozen dataclass: `kpis: list[MonthKpi]` (13, asc), `lifecycle_cur: LifecycleSnapshot` (cuối tháng báo cáo), `lifecycle_prev: LifecycleSnapshot` (cuối tháng trước), `cycle: CycleStats`, `pareto: ParetoStats`, `cohort_rows: list[dict]` (≤6 cohort gần nhất), `channels: list[dict]` (top 5 theo acquired)); `collect(month_start: date) -> HealthContext`.
- LƯU Ý spec Q5: cohort + channels anchor theo THỜI ĐIỂM CHẠY (limitation chấp nhận, sections sẽ ghi chú trong docx).

- [ ] **Step 1: Viết test fail** (append)

```python
class CollectTests(HealthQueryBase):
    def test_collect_smoke_on_small_data(self):
        a = self._customer("A")
        self._gtc(a, _dt(2026, 6, 5), total="100")
        ctx = health.collect(date(2026, 6, 1))
        assert len(ctx.kpis) == 13
        assert ctx.kpis[-1].month == date(2026, 6, 1)
        assert ctx.lifecycle_cur.ref_date == date(2026, 6, 30)
        assert ctx.lifecycle_prev.ref_date == date(2026, 5, 31)
        assert ctx.cycle.n_pairs == 0
        assert ctx.pareto.total_customers == 1
        assert isinstance(ctx.cohort_rows, list)
        assert isinstance(ctx.channels, list) and len(ctx.channels) <= 5
```

- [ ] **Step 2: Chạy test, xác nhận FAIL.**

- [ ] **Step 3: Implement** (append vào `health.py`)

```python
@dataclass(frozen=True)
class HealthContext:
    kpis: list[MonthKpi]
    lifecycle_cur: LifecycleSnapshot
    lifecycle_prev: LifecycleSnapshot
    cycle: CycleStats
    pareto: ParetoStats
    cohort_rows: list[dict]
    channels: list[dict]


def collect(month_start: date) -> HealthContext:
    """Gom toàn bộ số liệu sức khỏe cho tháng báo cáo ``month_start`` (mùng 1).

    cohort_rows/channels reuse engine web (anchor theo thời điểm chạy lệnh —
    ghi chú ngay trong docx; các chỉ số còn lại anchor tháng báo cáo).
    """
    from apps.customers import channel_quality, report_cohort

    m_start = month_floor(month_start)
    m_end = m_start + relativedelta(months=1) - relativedelta(days=1)
    prev_end = m_start - relativedelta(days=1)
    channels = channel_quality.compute().get("channels", [])
    return HealthContext(
        kpis=kpi_series(m_start, months=13),
        lifecycle_cur=lifecycle_at(m_end),
        lifecycle_prev=lifecycle_at(prev_end),
        cycle=repurchase_cycle(m_start),
        pareto=pareto(m_start),
        cohort_rows=report_cohort.retention(None, None, None).get("rows", [])[-6:],
        channels=channels[:5],
    )
```

- [ ] **Step 4: Chạy test, xác nhận PASS** — 8 passed.

- [ ] **Step 5: Commit** — `git commit -am "feat(report-doc): HealthContext collect"`

---

### Task 5: `charts_health.py` — 4 chart matplotlib

**Files:**
- Create: `backend/apps/customers/report_doc/charts_health.py`
- Test: `backend/apps/customers/tests/test_report_doc_health.py` (append)

**Interfaces:**
- Consumes: `MonthKpi`, `LifecycleSnapshot` (Task 1–2); `_fig_to_png`, `_BLUE`, `_BLUE_LIGHT` từ `charts.py`.
- Produces: `revenue_buyers_chart(kpis: list[MonthKpi]) -> bytes`, `repeat_rate_line(kpis: list[MonthKpi]) -> bytes`, `new_vs_returning_stacked(kpis: list[MonthKpi]) -> bytes`, `lifecycle_bar(cur: LifecycleSnapshot, prev: LifecycleSnapshot) -> bytes` — tất cả trả PNG bytes.

- [ ] **Step 1: Viết test fail** (append; PNG magic = `\x89PNG`)

```python
class ChartsHealthTests(HealthQueryBase):
    def test_all_charts_render_png(self):
        from apps.customers.report_doc import charts_health
        a = self._customer("A")
        self._gtc(a, _dt(2026, 6, 5), total="100")
        ctx = health.collect(date(2026, 6, 1))
        pngs = [
            charts_health.revenue_buyers_chart(ctx.kpis[-12:]),
            charts_health.repeat_rate_line(ctx.kpis[-12:]),
            charts_health.new_vs_returning_stacked(ctx.kpis[-12:]),
            charts_health.lifecycle_bar(ctx.lifecycle_cur, ctx.lifecycle_prev),
        ]
        for png in pngs:
            assert png[:4] == b"\x89PNG"
```

- [ ] **Step 2: Chạy test, xác nhận FAIL** — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `charts_health.py`**

```python
# backend/apps/customers/report_doc/charts_health.py
"""Chart sức khỏe tập KH cho docx — style đồng nhất charts.py, PNG bytes."""
from __future__ import annotations

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from apps.customers.behavior_classify import (  # noqa: E402
    POST_LIFECYCLE_LABELS, POST_LIFECYCLE_ORDER,
)

from .charts import _BLUE, _BLUE_LIGHT, _fig_to_png  # noqa: E402
from .health import LifecycleSnapshot, MonthKpi  # noqa: E402

_GRAY = "#e2e8f0"


def _months_x(kpis: list[MonthKpi]) -> list[str]:
    return [k.month.strftime("%m/%y") for k in kpis]


def revenue_buyers_chart(kpis: list[MonthKpi]) -> bytes:
    fig, ax = plt.subplots(figsize=(7, 3.4))
    x = _months_x(kpis)
    ax.bar(x, [k.revenue / 1e6 for k in kpis], color=_BLUE_LIGHT, label="Doanh thu (triệu ₫)")
    ax.set_ylabel("Triệu ₫")
    ax2 = ax.twinx()
    ax2.plot(x, [k.buyers for k in kpis], marker="o", color=_BLUE, label="Người mua")
    ax2.set_ylabel("Người mua")
    ax.set_title("Doanh thu & người mua theo tháng")
    ax.tick_params(axis="x", rotation=30)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left")
    return _fig_to_png(fig)


def repeat_rate_line(kpis: list[MonthKpi]) -> bytes:
    fig, ax = plt.subplots(figsize=(7, 3.0))
    ax.plot(_months_x(kpis), [k.repeat_rate for k in kpis], marker="o",
            color=_BLUE, label="% người mua ≥2 đơn trong tháng")
    ax.set_ylabel("%")
    ax.set_ylim(bottom=0)
    ax.set_title("Tỷ lệ mua lại trong tháng")
    ax.grid(True, axis="y", alpha=0.3)
    ax.tick_params(axis="x", rotation=30)
    ax.legend()
    return _fig_to_png(fig)


def new_vs_returning_stacked(kpis: list[MonthKpi]) -> bytes:
    fig, ax = plt.subplots(figsize=(7, 3.4))
    x = _months_x(kpis)
    new = np.array([k.revenue_new / 1e6 for k in kpis])
    ret = np.array([k.revenue_returning / 1e6 for k in kpis])
    ax.bar(x, new, color=_BLUE_LIGHT, label="KH mới")
    ax.bar(x, ret, bottom=new, color=_BLUE, label="KH cũ (mua lại)")
    ax.set_ylabel("Triệu ₫")
    ax.set_title("Doanh thu KH mới vs KH cũ")
    ax.tick_params(axis="x", rotation=30)
    ax.legend()
    return _fig_to_png(fig)


def lifecycle_bar(cur: LifecycleSnapshot, prev: LifecycleSnapshot) -> bytes:
    fig, ax = plt.subplots(figsize=(7, 3.2))
    labels = [POST_LIFECYCLE_LABELS[c] for c in POST_LIFECYCLE_ORDER]
    idx = np.arange(len(labels))
    width = 0.38
    ax.bar(idx - width / 2, [prev.counts.get(c, 0) for c in POST_LIFECYCLE_ORDER],
           width, label=f"Cuối {prev.ref_date:%m/%Y}", color=_GRAY)
    ax.bar(idx + width / 2, [cur.counts.get(c, 0) for c in POST_LIFECYCLE_ORDER],
           width, label=f"Cuối {cur.ref_date:%m/%Y}", color=_BLUE)
    ax.set_xticks(idx)
    ax.set_xticklabels(labels)
    ax.set_title("Cơ cấu vòng đời tập khách hàng")
    ax.legend()
    return _fig_to_png(fig)
```

- [ ] **Step 4: Chạy test, xác nhận PASS** — 9 passed.

- [ ] **Step 5: Commit** — `git add -A backend/apps/customers && git commit -m "feat(report-doc): health charts"`

---

### Task 6: `health_insights.py` — nhận xét rule-based (thuần)

**Files:**
- Create: `backend/apps/customers/report_doc/health_insights.py`
- Test: `backend/apps/customers/tests/test_report_doc_health.py` (append)

**Interfaces:**
- Consumes: `HealthContext`, `MonthKpi`, `LifecycleSnapshot`, `CycleStats`, `ParetoStats`.
- Produces: `build(h: HealthContext) -> list[str]` — thuần, không I/O.

- [ ] **Step 1: Viết test fail** (append; dựng HealthContext tay, không cần DB)

```python
class HealthInsightsTests(TestCase):
    def _kpi(self, month, revenue, buyers, new, repeat, rev_new, orders=None):
        return health.MonthKpi(month=month, revenue=revenue,
                               orders=orders if orders is not None else buyers,
                               buyers=buyers, new_customers=new,
                               repeat_buyers=repeat, revenue_new=rev_new)

    def _ctx(self, kpis, dormant_delta=0.0, top10=30.0):
        from apps.customers.behavior_classify import (
            POST_ACTIVE, POST_AT_RISK, POST_COOLING, POST_DORMANT)
        base = {POST_ACTIVE: 50, POST_COOLING: 20, POST_AT_RISK: 15, POST_DORMANT: 15}
        cur = dict(base)
        cur[POST_DORMANT] = int(base[POST_DORMANT] + dormant_delta)
        return health.HealthContext(
            kpis=kpis,
            lifecycle_cur=health.LifecycleSnapshot(date(2026, 6, 30), cur, 1000.0),
            lifecycle_prev=health.LifecycleSnapshot(date(2026, 5, 31), base, 1000.0),
            cycle=health.CycleStats(60.0, 30.0, 90.0, 10),
            pareto=health.ParetoStats(100, 10000.0, top10, 45.0),
            cohort_rows=[], channels=[])

    def test_flags_revenue_drop_and_low_returning_share(self):
        from apps.customers.report_doc import health_insights
        kpis = [self._kpi(date(2026, 5, 1), 1000.0, 10, 5, 2, 500.0),
                self._kpi(date(2026, 6, 1), 700.0, 10, 9, 2, 650.0)]
        lines = health_insights.build(self._ctx(kpis))
        text = " ".join(lines)
        assert "Doanh thu" in text and "giảm" in text       # -30% MoM
        assert "khách mới" in text                          # % mua lại < 20%

    def test_flags_concentration_and_dormant_growth(self):
        from apps.customers.report_doc import health_insights
        kpis = [self._kpi(date(2026, 5, 1), 1000.0, 10, 5, 3, 500.0),
                self._kpi(date(2026, 6, 1), 1000.0, 10, 5, 3, 500.0)]
        lines = health_insights.build(self._ctx(kpis, dormant_delta=5, top10=55.0))
        text = " ".join(lines)
        assert "top 10%" in text.lower()
        assert "ngủ đông" in text.lower() or "Dormant" in text
```

- [ ] **Step 2: Chạy test, xác nhận FAIL.**

- [ ] **Step 3: Implement `health_insights.py`**

```python
# backend/apps/customers/report_doc/health_insights.py
"""Nhận xét tự động từ chỉ số sức khỏe (rule-based, hàm thuần).

Ngưỡng theo spec §5 docs/monthly-customer-report-health-metrics.md.
"""
from __future__ import annotations

from apps.customers.behavior_classify import POST_DORMANT

from .health import HealthContext

_MOM_PCT = 10.0          # doanh thu / buyers đổi > ±10% MoM
_REPEAT_DROP_PTS = 2.0   # tỷ lệ mua lại giảm >= 2 điểm%
_RETURNING_SHARE_MIN = 20.0
_DORMANT_PTS = 1.0       # dormant tăng > 1 điểm%
_VAR_PCT = 10.0          # value-at-risk tăng > 10%
_TOP10_MAX = 50.0


def _pct_change(cur: float, prev: float) -> float | None:
    if not prev:
        return None
    return (cur - prev) * 100.0 / prev


def build(h: HealthContext) -> list[str]:
    out: list[str] = []
    if len(h.kpis) < 2:
        return out
    cur, prev = h.kpis[-1], h.kpis[-2]

    rev = _pct_change(cur.revenue, prev.revenue)
    if rev is not None and abs(rev) > _MOM_PCT:
        verb = "tăng" if rev > 0 else "giảm"
        out.append(f"Doanh thu GTC {verb} {abs(rev):.1f}% so với tháng trước "
                   f"({prev.revenue / 1e6:,.0f} → {cur.revenue / 1e6:,.0f} triệu ₫).")
    buyers = _pct_change(cur.buyers, prev.buyers)
    if buyers is not None and abs(buyers) > _MOM_PCT:
        verb = "tăng" if buyers > 0 else "giảm"
        out.append(f"Số người mua {verb} {abs(buyers):.1f}% ({prev.buyers} → {cur.buyers}).")

    repeat_d = cur.repeat_rate - prev.repeat_rate
    if repeat_d <= -_REPEAT_DROP_PTS:
        out.append(f"Tỷ lệ mua lại trong tháng giảm {abs(repeat_d):.1f} điểm% "
                   f"({prev.repeat_rate:.1f}% → {cur.repeat_rate:.1f}%) — cần chương trình giữ chân.")

    if cur.revenue and cur.pct_revenue_returning < _RETURNING_SHARE_MIN:
        out.append(f"Chỉ {cur.pct_revenue_returning:.1f}% doanh thu đến từ KH cũ — "
                   "tăng trưởng đang phụ thuộc khách mới.")

    cur_lc, prev_lc = h.lifecycle_cur, h.lifecycle_prev
    if cur_lc.total and prev_lc.total:
        dorm_pts = (cur_lc.counts.get(POST_DORMANT, 0) * 100.0 / cur_lc.total
                    - prev_lc.counts.get(POST_DORMANT, 0) * 100.0 / prev_lc.total)
        if dorm_pts > _DORMANT_PTS:
            out.append(f"Tỷ trọng KH ngủ đông tăng {dorm_pts:.1f} điểm% trong tháng — rò rỉ tập khách.")
    var_pct = _pct_change(cur_lc.value_at_risk, prev_lc.value_at_risk)
    if var_pct is not None and var_pct > _VAR_PCT:
        out.append(f"Giá trị KH đang gặp rủi ro tăng {var_pct:.1f}% "
                   f"(hiện {cur_lc.value_at_risk / 1e6:,.0f} triệu ₫).")

    if h.pareto.top10_share > _TOP10_MAX:
        out.append(f"Top 10% khách hàng chiếm {h.pareto.top10_share:.1f}% doanh thu 12 tháng — "
                   "doanh thu tập trung rủi ro cao.")

    if h.channels:
        worst = max(h.channels, key=lambda c: float(c.get("dormant_pct") or 0))
        if float(worst.get("dormant_pct") or 0) > 0:
            out.append(f"Kênh \"{worst['channel_name']}\" có tỷ lệ KH ngủ đông cao nhất "
                       f"({float(worst['dormant_pct']):.1f}%) — xem lại chất lượng khách từ kênh này.")
    return out
```

- [ ] **Step 4: Chạy test, xác nhận PASS** — 11 passed.

- [ ] **Step 5: Commit** — `git add -A backend/apps/customers && git commit -m "feat(report-doc): health insights rules"`

---

### Task 7: `sections_health.py` — dựng §3.1–3.7 vào Document

**Files:**
- Create: `backend/apps/customers/report_doc/sections_health.py`
- Test: `backend/apps/customers/tests/test_report_doc_health.py` (append)

**Interfaces:**
- Consumes: `HealthContext` (Task 4); chart PNG được truyền qua dict `charts` (key: `health_revenue_buyers`, `health_repeat_rate`, `health_new_vs_returning`, `health_lifecycle` — Task 8 sẽ điền).
- Produces: `HEALTH_HEADINGS: tuple[str, ...]` (7 heading, dùng lại trong test) và `add_health_sections(doc: Document, h: HealthContext, charts: dict[str, bytes]) -> None`.

- [ ] **Step 1: Viết test fail** (append)

```python
class SectionsHealthTests(HealthQueryBase):
    def test_adds_all_seven_headings(self):
        from docx import Document
        from apps.customers.report_doc import sections_health
        a = self._customer("A")
        self._gtc(a, _dt(2026, 6, 5), total="100")
        ctx = health.collect(date(2026, 6, 1))
        doc = Document()
        sections_health.add_health_sections(doc, ctx, charts={})
        headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
        for h_text in sections_health.HEALTH_HEADINGS:
            assert h_text in headings
```

- [ ] **Step 2: Chạy test, xác nhận FAIL.**

- [ ] **Step 3: Implement `sections_health.py`**

```python
# backend/apps/customers/report_doc/sections_health.py
"""Section §3.1–3.7 (spec health-metrics) cho docx. Chỉ dựng bảng/đoạn — không query."""
from __future__ import annotations

import io

from docx import Document
from docx.shared import Inches

from apps.customers.behavior_classify import POST_LIFECYCLE_LABELS, POST_LIFECYCLE_ORDER

from .health import HealthContext, MonthKpi

HEALTH_HEADINGS = (
    "Chỉ số sức khỏe tháng",
    "Xu hướng 12 tháng",
    "Doanh thu khách mới vs khách cũ",
    "Vòng đời khách hàng & giá trị rủi ro",
    "Cohort quay lại",
    "Chu kỳ mua lại & độ tập trung doanh thu",
    "Chất lượng kênh acquisition",
)

_ANCHOR_NOTE = "Số liệu mục này tính tại thời điểm chạy báo cáo (không phải cuối tháng báo cáo)."


def _money(v: float) -> str:
    return f"{v:,.0f}".replace(",", ".") + " ₫"


def _delta_pct(cur: float, prev: float | None) -> str:
    if prev in (None, 0):
        return "—"
    return f"{(cur - prev) * 100.0 / prev:+.1f}%"


def _delta_pts(cur: float, prev: float | None) -> str:
    if prev is None:
        return "—"
    return f"{cur - prev:+.1f}đ%"


def _table(doc: Document, headers: list[str]):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Light Grid Accent 1"
    for cell, text in zip(table.rows[0].cells, headers):
        cell.text = text
    return table


def _img(doc: Document, charts: dict[str, bytes], key: str, width_in: float = 6.0) -> None:
    if key in charts:
        doc.add_picture(io.BytesIO(charts[key]), width=Inches(width_in))


def add_health_sections(doc: Document, h: HealthContext, charts: dict[str, bytes]) -> None:
    cur: MonthKpi = h.kpis[-1]
    prev: MonthKpi | None = h.kpis[-2] if len(h.kpis) >= 2 else None
    yoy: MonthKpi | None = h.kpis[0] if len(h.kpis) >= 13 else None

    # §3.1 KPI
    doc.add_heading(HEALTH_HEADINGS[0], level=1)
    table = _table(doc, ["Chỉ số", "Tháng này", "Δ MoM", "Δ YoY"])
    rows = [
        ("Doanh thu GTC", _money(cur.revenue),
         _delta_pct(cur.revenue, prev.revenue if prev else None),
         _delta_pct(cur.revenue, yoy.revenue if yoy else None)),
        ("Số người mua", str(cur.buyers),
         _delta_pct(cur.buyers, prev.buyers if prev else None),
         _delta_pct(cur.buyers, yoy.buyers if yoy else None)),
        ("Khách hàng mới", str(cur.new_customers),
         _delta_pct(cur.new_customers, prev.new_customers if prev else None),
         _delta_pct(cur.new_customers, yoy.new_customers if yoy else None)),
        ("KH mua ≥2 đơn trong tháng", str(cur.repeat_buyers),
         _delta_pct(cur.repeat_buyers, prev.repeat_buyers if prev else None),
         _delta_pct(cur.repeat_buyers, yoy.repeat_buyers if yoy else None)),
        ("Tỷ lệ mua lại trong tháng", f"{cur.repeat_rate:.1f}%",
         _delta_pts(cur.repeat_rate, prev.repeat_rate if prev else None),
         _delta_pts(cur.repeat_rate, yoy.repeat_rate if yoy else None)),
        ("Giá trị đơn trung bình (AOV)", _money(cur.aov),
         _delta_pct(cur.aov, prev.aov if prev else None),
         _delta_pct(cur.aov, yoy.aov if yoy else None)),
    ]
    for label, c, mom, yy in rows:
        r = table.add_row().cells
        r[0].text, r[1].text, r[2].text, r[3].text = label, c, mom, yy

    # §3.2 Trend
    doc.add_heading(HEALTH_HEADINGS[1], level=1)
    _img(doc, charts, "health_revenue_buyers")
    _img(doc, charts, "health_repeat_rate")

    # §3.3 New vs returning
    doc.add_heading(HEALTH_HEADINGS[2], level=1)
    doc.add_paragraph(
        f"Tháng này: {cur.pct_revenue_returning:.1f}% doanh thu đến từ KH cũ "
        f"({_money(cur.revenue_returning)}), còn lại từ {cur.new_customers} KH mới "
        f"({_money(cur.revenue_new)})."
        + (f" Tháng trước: {prev.pct_revenue_returning:.1f}% từ KH cũ." if prev else ""))
    _img(doc, charts, "health_new_vs_returning")

    # §3.4 Lifecycle + VAR
    doc.add_heading(HEALTH_HEADINGS[3], level=1)
    lc_cur, lc_prev = h.lifecycle_cur, h.lifecycle_prev
    table = _table(doc, ["Nhóm", f"Cuối {lc_cur.ref_date:%m/%Y}", "%",
                         f"Cuối {lc_prev.ref_date:%m/%Y}", "Δ điểm%"])
    for code in POST_LIFECYCLE_ORDER:
        c_n, p_n = lc_cur.counts.get(code, 0), lc_prev.counts.get(code, 0)
        c_pct = c_n * 100.0 / lc_cur.total if lc_cur.total else 0.0
        p_pct = p_n * 100.0 / lc_prev.total if lc_prev.total else 0.0
        r = table.add_row().cells
        r[0].text = POST_LIFECYCLE_LABELS[code]
        r[1].text, r[2].text = str(c_n), f"{c_pct:.1f}%"
        r[3].text, r[4].text = str(p_n), _delta_pts(c_pct, p_pct)
    doc.add_paragraph(
        f"Giá trị đang gặp rủi ro (tổng chi tiêu lịch sử của nhóm Nguy cơ rời + Ngủ đông): "
        f"{_money(lc_cur.value_at_risk)} "
        f"({_delta_pct(lc_cur.value_at_risk, lc_prev.value_at_risk)} so với cuối tháng trước).")
    _img(doc, charts, "health_lifecycle")

    # §3.5 Cohort
    doc.add_heading(HEALTH_HEADINGS[4], level=1)
    doc.add_paragraph(_ANCHOR_NOTE)
    table = _table(doc, ["Cohort (tháng mua đầu)", "Số KH", "Quay lại M+1", "M+2", "M+3"])
    for row in h.cohort_rows:
        retained = row.get("retained", [])
        size = row.get("size", 0)

        def _r(i: int) -> str:
            n = retained[i] if i < len(retained) else 0
            return f"{n} ({n * 100.0 / size:.1f}%)" if size else "0"

        r = table.add_row().cells
        r[0].text, r[1].text = str(row.get("cohort", "")), str(size)
        r[2].text, r[3].text, r[4].text = _r(0), _r(1), _r(2)

    # §3.6 Cycle + Pareto
    doc.add_heading(HEALTH_HEADINGS[5], level=1)
    cyc = h.cycle
    if cyc.median_days is not None:
        doc.add_paragraph(
            f"Chu kỳ mua lại (median, 24 tháng gần nhất): {cyc.median_days:.0f} ngày "
            f"(p25 {cyc.p25_days:.0f} – p75 {cyc.p75_days:.0f}; n={cyc.n_pairs} cặp đơn).",
            style="List Bullet")
    else:
        doc.add_paragraph("Chưa đủ dữ liệu cặp đơn mua lại trong 24 tháng.", style="List Bullet")
    doc.add_paragraph(
        f"Doanh thu 12 tháng: {_money(h.pareto.total_revenue)} từ {h.pareto.total_customers} KH; "
        f"top 10% KH chiếm {h.pareto.top10_share:.1f}%, top 20% chiếm {h.pareto.top20_share:.1f}%.",
        style="List Bullet")

    # §3.7 Channels
    doc.add_heading(HEALTH_HEADINGS[6], level=1)
    doc.add_paragraph(_ANCHOR_NOTE + " Top 5 kênh theo số KH có đơn GTC đầu tiên trên kênh.")
    table = _table(doc, ["Kênh", "KH acquire", "% mua lại", "Đơn/KH", "Doanh thu/KH", "% ngủ đông"])
    for ch in h.channels:
        r = table.add_row().cells
        r[0].text = str(ch.get("channel_name", ""))
        r[1].text = str(ch.get("acquired", 0))
        r[2].text = f"{float(ch.get('repeat_pct') or 0):.1f}%"
        r[3].text = f"{float(ch.get('orders_per_customer') or 0):.2f}"
        r[4].text = _money(float(ch.get("revenue_per_customer") or 0))
        r[5].text = f"{float(ch.get('dormant_pct') or 0):.1f}%"
```

- [ ] **Step 4: Chạy test, xác nhận PASS** — 12 passed.

- [ ] **Step 5: Commit** — `git add -A backend/apps/customers && git commit -m "feat(report-doc): health docx sections"`

---

### Task 8: Phụ lục + rewire `docx_builder.py` + `service.py` + cập nhật test command

**Files:**
- Create: `backend/apps/customers/report_doc/sections_appendix.py`
- Modify: `backend/apps/customers/report_doc/docx_builder.py` (viết lại phần build)
- Modify: `backend/apps/customers/report_doc/service.py`
- Modify: `backend/apps/customers/tests/test_generate_monthly_customer_report_command.py`

**Interfaces:**
- Consumes: `add_health_sections`, `HEALTH_HEADINGS` (Task 7); `health.collect` (Task 4); `charts_health.*` (Task 5); `health_insights.build` (Task 6).
- Produces: `sections_appendix.APPENDIX_HEADING = "Phụ lục — Chân dung khách hàng (dữ liệu note)"`, `add_appendix(doc, ctx) -> None`; `ReportContext` thêm field `health: HealthContext`; chart keys mới trong `ctx.charts`: `health_revenue_buyers`, `health_repeat_rate`, `health_new_vs_returning`, `health_lifecycle`.

- [ ] **Step 1: Cập nhật test command (fail trước)** — thay `test_generates_docx_file` để assert đủ heading mới:

```python
    def test_generates_docx_file(self):
        import tempfile, os
        tz = timezone.get_current_timezone()
        self._order(datetime(2026, 6, 10, 9, tzinfo=tz), dict(
            customer_classification_code="c", sales_status_code="closed", pain_level_code="high"))
        out_dir = tempfile.mkdtemp()
        out_path = os.path.join(out_dir, "report.docx")
        call_command("generate_monthly_customer_report", "--month", "2026-06",
                     "--out", out_path, "--months-trend", "3")
        assert os.path.exists(out_path)
        doc = Document(out_path)
        texts = [p.text for p in doc.paragraphs]
        assert any("tháng 06/2026" in t for t in texts)
        from apps.customers.report_doc.sections_appendix import APPENDIX_HEADING
        from apps.customers.report_doc.sections_health import HEALTH_HEADINGS
        headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
        for h_text in HEALTH_HEADINGS:
            assert h_text in headings, h_text
        assert "Nhận xét & khuyến nghị" in headings
        assert APPENDIX_HEADING in headings
```

Lưu ý: test `_order` không truyền customer riêng — giữ nguyên helper cũ của file đó.

- [ ] **Step 2: Chạy test command, xác nhận FAIL** — `apps/customers/tests/test_generate_monthly_customer_report_command.py -q` → ImportError.

- [ ] **Step 3: Tạo `sections_appendix.py`** (chuyển nguyên trạng nội dung cũ từ `docx_builder.py`, thêm badge độ phủ):

```python
# backend/apps/customers/report_doc/sections_appendix.py
"""Phụ lục chân dung KH (tier note + phân bố assessment) — nội dung docx cũ, kèm badge độ phủ."""
from __future__ import annotations

import io

from docx import Document
from docx.shared import Inches

APPENDIX_HEADING = "Phụ lục — Chân dung khách hàng (dữ liệu note)"

_DISTRIBUTIONS = [
    ("classification_distribution", "Phân loại khách hàng", "classification_bar"),
    ("need_type_distribution", "Nhu cầu khách hàng", "need_bar"),
    ("delax_source_distribution", "Trước mua — nguồn biết đến", "source_bar"),
    ("feedback_rating_distribution", "Sau mua — đánh giá", "rating_bar"),
]


def _pct(part: int, whole: int) -> float:
    return (part / whole * 100.0) if whole else 0.0


def _delta_pct_points(cur: float, prev: float | None) -> str:
    if prev is None:
        return "—"
    return f"{cur - prev:+.1f}đ%"


def _add_image(doc: Document, png: bytes, width_in: float = 6.0) -> None:
    doc.add_picture(io.BytesIO(png), width=Inches(width_in))


def add_appendix(doc: Document, ctx) -> None:  # ctx: docx_builder.ReportContext
    doc.add_heading(APPENDIX_HEADING, level=1)
    cur, prev = ctx.cur_stats, ctx.prev_stats
    coverage = _pct(cur.with_assessment, cur.total_orders)
    doc.add_paragraph(
        f"Độ phủ dữ liệu: {cur.with_assessment}/{cur.total_orders} đơn có đánh giá "
        f"({coverage:.1f}%). Các phân tích dưới đây chỉ phản ánh nhóm đơn được note.")
    if ctx.small_sample:
        doc.add_paragraph(
            f"⚠ Mẫu nhỏ: số đơn có dữ liệu đánh giá < {ctx.small_sample_threshold}. "
            "Các phân tích nhu cầu/sau mua chỉ mang tính tham khảo.")

    doc.add_heading("Tỉ lệ note chân dung", level=2)
    table = doc.add_table(rows=1, cols=4)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text = "Mức độ", "Tháng này", "Tháng trước", "Δ"
    tier_rows = [
        ("Đầy đủ", cur.day_du, prev.day_du if prev else None, cur.total_orders,
         prev.total_orders if prev else None),
        ("Cơ bản", cur.co_ban, prev.co_ban if prev else None, cur.total_orders,
         prev.total_orders if prev else None),
        ("Chưa note", cur.chua_note, prev.chua_note if prev else None, cur.total_orders,
         prev.total_orders if prev else None),
    ]
    for label, c, p, ct, pt in tier_rows:
        row = table.add_row().cells
        row[0].text = label
        row[1].text = f"{c} ({_pct(c, ct):.1f}%)"
        row[2].text = "—" if p is None else f"{p} ({_pct(p, pt):.1f}%)"
        row[3].text = _delta_pct_points(_pct(c, ct), None if p is None else _pct(p, pt))
    if "tier_pie" in ctx.charts:
        _add_image(doc, ctx.charts["tier_pie"], 3.5)
    if "tier_trend" in ctx.charts:
        _add_image(doc, ctx.charts["tier_trend"])

    for key, title, chart_key in _DISTRIBUTIONS:
        doc.add_heading(title, level=2)
        _distribution_table(doc, ctx.cur_summary.get(key, []),
                            (ctx.prev_summary or {}).get(key, []))
        if chart_key in ctx.charts:
            _add_image(doc, ctx.charts[chart_key])


def _distribution_table(doc: Document, cur_items: list[dict], prev_items: list[dict]) -> None:
    prev_by_code = {i["code"]: i for i in prev_items}
    cur_codes = {i["code"] for i in cur_items}
    vanished_items = [p for p in prev_items if p["code"] not in cur_codes]
    combined = [(it, prev_by_code.get(it["code"])) for it in cur_items]
    combined += [(None, p) for p in vanished_items]
    combined = combined[:12]

    table = doc.add_table(rows=1, cols=4)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text = "Nhóm", "Tháng này", "Tháng trước", "Δ%"
    for it, p in combined:
        row = table.add_row().cells
        label = (it or {}).get("label") or (p or {}).get("label") or (it or p)["code"]
        row[0].text = str(label)
        if it is None:
            row[1].text = "0 (0.0%)"
            row[2].text = f'{p["count"]} ({p["percentage"]:.1f}%)'
            row[3].text = f'{-p["percentage"]:.1f}'
        else:
            row[1].text = f'{it["count"]} ({it["percentage"]:.1f}%)'
            row[2].text = "—" if p is None else f'{p["count"]} ({p["percentage"]:.1f}%)'
            row[3].text = "—" if p is None else f'{it["percentage"] - p["percentage"]:+.1f}'
```

- [ ] **Step 4: Viết lại `docx_builder.py`** (giữ `ReportContext` + insights note cũ, phần thân mới):

```python
# backend/apps/customers/report_doc/docx_builder.py
"""Assemble the monthly customer report .docx from a prepared ReportContext."""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date

from docx import Document

from . import health_insights
from .health import HealthContext
from .sections_appendix import add_appendix
from .sections_health import add_health_sections
from .trend import MonthStats


@dataclass
class ReportContext:
    month: date
    cur_summary: dict
    prev_summary: dict | None
    cur_stats: MonthStats
    prev_stats: MonthStats | None
    series: list[MonthStats]
    charts: dict[str, bytes]
    small_sample: bool
    small_sample_threshold: int
    health: HealthContext


def build(ctx: ReportContext) -> bytes:
    doc = Document()
    doc.add_heading(f"Báo cáo khách hàng tháng {ctx.month:%m/%Y}", level=0)
    doc.add_paragraph(
        f"Tổng đơn tháng này: {ctx.cur_stats.total_orders} | "
        f"tháng trước: {ctx.prev_stats.total_orders if ctx.prev_stats else '—'}"
    )

    add_health_sections(doc, ctx.health, ctx.charts)

    doc.add_heading("Nhận xét & khuyến nghị", level=1)
    lines = health_insights.build(ctx.health) + _note_insights(ctx)
    if not lines:
        lines = ["Không có thay đổi đáng kể so với tháng trước."]
    for line in lines:
        doc.add_paragraph(line, style="List Bullet")

    add_appendix(doc, ctx)

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _note_insights(ctx: ReportContext) -> list[str]:
    out: list[str] = []
    cur, prev = ctx.cur_stats, ctx.prev_stats
    if prev is not None:
        d = cur.pct_noted - prev.pct_noted
        verb = "tăng" if d >= 0 else "giảm"
        out.append(f"Tỉ lệ đơn được note {verb} {abs(d):.1f} điểm% so với tháng trước "
                   f"({prev.pct_noted:.1f}% → {cur.pct_noted:.1f}%).")
    if cur.pct_noted < 50:
        out.append("Tỉ lệ note còn thấp (<50%) — cần thúc đội sale nhập đủ chân dung khách.")
    if ctx.small_sample:
        out.append("Dữ liệu sau mua quá thưa để kết luận xu hướng; ưu tiên tăng tỉ lệ ghi nhận.")
    return out
```

(`_delta_pct_points`, `_add_image`, `_pct`, `_distribution_table` đã CHUYỂN sang `sections_appendix.py` — xóa khỏi builder.)

- [ ] **Step 5: Cập nhật `service.py`** — thêm health collect + charts (đoạn build, giữ nguyên phần còn lại):

```python
# backend/apps/customers/report_doc/service.py  (thay thân build())
from . import charts, charts_health, health           # cập nhật import đầu file

# ... trong MonthlyReportDocService.build(), sau khi có series/cur_stats/prev_stats:
        health_ctx = health.collect(m_start)

        chart_pngs = {
            "tier_pie": charts.tier_pie(cur_stats),
            "tier_trend": charts.tier_trend_line(series),
            "health_revenue_buyers": charts_health.revenue_buyers_chart(health_ctx.kpis[-12:]),
            "health_repeat_rate": charts_health.repeat_rate_line(health_ctx.kpis[-12:]),
            "health_new_vs_returning": charts_health.new_vs_returning_stacked(health_ctx.kpis[-12:]),
            "health_lifecycle": charts_health.lifecycle_bar(
                health_ctx.lifecycle_cur, health_ctx.lifecycle_prev),
        }
        # ... vòng for _compare_chart giữ nguyên ...
        ctx = ReportContext(
            month=m_start, cur_summary=cur_summary, prev_summary=prev_summary,
            cur_stats=cur_stats, prev_stats=prev_stats, series=series,
            charts=chart_pngs, small_sample=small_sample, small_sample_threshold=SMALL_SAMPLE,
            health=health_ctx,
        )
```

- [ ] **Step 6: Chạy test, xác nhận PASS**

Run: `apps/customers/tests/test_generate_monthly_customer_report_command.py apps/customers/tests/test_report_doc_health.py -q`
Expected: tất cả pass (2 + 12).

- [ ] **Step 7: Commit** — `git add -A backend && git commit -m "feat(report-doc): health sections in monthly docx, assessment content to appendix"`

---

### Task 9: Verify toàn cục + cập nhật tiến độ + push

**Files:**
- Modify: `docs/monthly-customer-report-health-metrics.md` (đánh dấu sprint done + ghi chú)

- [ ] **Step 1: Chạy full test module customers**

Run: `apps/customers/tests -q`
Expected: pass toàn bộ TRỪ baseline fail đã biết (`test_report_trends` ×2 — pre-existing trên master sạch, xem memory known-failing; nếu có fail MỚI thì phải sửa trước khi tiếp).

- [ ] **Step 2: Ruff trên host**

Run: `cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-report-docx && /c/Users/MYPC/.local/bin/uv run --no-project --with ruff ruff check backend/apps/customers/report_doc backend/apps/customers/tests/test_report_doc_health.py`
Expected: `All checks passed!`

- [ ] **Step 3: Smoke thật (tùy chọn nếu DB dev có data)** — chạy command trong container test:

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file c:/Users/MYPC/Desktop/workspace/desi-order-wt-report-docx/.env \
  --env-file c:/Users/MYPC/Desktop/workspace/desi-order-wt-report-docx/backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "c:/Users/MYPC/Desktop/workspace/desi-order-wt-report-docx/backend:/app" -w /app \
  desi-order-web-test:latest python manage.py generate_monthly_customer_report \
  --month 2026-06 --out /app/_smoke_report.docx
```

Mở `backend/_smoke_report.docx` kiểm tra mắt thường rồi XÓA file (đừng commit).

- [ ] **Step 4: Cập nhật spec** — sửa `docs/monthly-customer-report-health-metrics.md` mục Sprint: tick các checkbox đã xong, ghi "Tiến độ: hoàn thành <ngày>" + note những gì lệch spec (vd: bỏ `MonthSplit`, gộp vào `MonthKpi`).

- [ ] **Step 5: Commit + push + MR**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-report-docx && \
git add docs && git commit -m "docs: mark health metrics sprints done" && \
git push -u origin feat/monthly-report-health-metrics
```

Lấy link MR từ output push (GitLab in sẵn "remote: ... merge_request") và báo lại user.
