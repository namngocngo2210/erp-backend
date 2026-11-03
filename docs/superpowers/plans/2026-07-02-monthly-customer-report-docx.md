# Monthly Customer Report (.docx) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Django management command that generates a Word (.docx) monthly customer-portrait report from `order_assessment`, centered on this-month-vs-last-month comparison plus a 6–12 month trend.

**Architecture:** A thin management command orchestrates a `report_doc` package. Pure business logic (`tiers`) and data aggregation (`trend`) are separate from rendering (`charts`, `docx_builder`) and orchestration (`service`). Distribution numbers reuse the existing `CustomerReportService.summary()` (called twice: this month + previous month); note-completeness tiers and the long trend come from `report_doc` queries over ALL orders (left-join assessment). No HTTP, no FE.

**Tech Stack:** Python 3.12 / Django 6, `python-docx`, `matplotlib` (Agg backend), `python-dateutil` (already present), pytest / pytest-django.

## Global Constraints

- Clean architecture; each file < 300 lines; layers independent (command → service → {tiers, trend, charts, docx_builder}); no layer imports HTTP.
- `tiers.py` is pure (no ORM, no I/O). `service.py` must not import matplotlib directly (only via `charts`).
- Report scope matches existing reports: `deleted_at__isnull=True`, `.exclude(branch_id__in=EXCLUDED_BRANCH_IDS)` (from `apps.orders.repositories`).
- Tier denominator = ALL orders in the month (left-join assessment) — different from `CustomerReportService.summary()` which uses `assessment__isnull=False`.
- Do NOT call `CustomerReportService.summary()` per-month for the trend (runaway-query risk). Trend = ONE windowed query, bucketed in Python.
- `matplotlib.use("Agg")` before importing `pyplot`.
- Tier tuning constants (`PRE_FULL_MIN=5`, field groups) live in `tiers.py`.
- Small-sample warning threshold: `SMALL_SAMPLE = 30` orders-with-assessment.
- Commit messages in English, short, no Co-Authored-By trailer (project rule).

**Running tests (Windows):** the prod image lacks tests/deps. Run pytest inside the test image on the compose network, from the `backend/` working dir inside the container, e.g.:
`docker run --rm --network desi-order_desi-net -w /app desi-order-web-test pytest apps/customers/tests/<file> -v`
(See memory "running-backend-tests" for the exact image/redis-password details if Django settings require redis at import.)

## File Structure

```
backend/apps/customers/report_doc/
  __init__.py                              # empty package marker
  tiers.py                                 # pure tier classification + field groups
  trend.py                                 # MonthStats + monthly_series() windowed query
  charts.py                                # matplotlib PNG builders (Agg)
  docx_builder.py                          # ReportContext + build() -> docx bytes
  service.py                               # MonthlyReportDocService.build()
backend/apps/customers/management/__init__.py            # create if missing
backend/apps/customers/management/commands/__init__.py   # create if missing
backend/apps/customers/management/commands/generate_monthly_customer_report.py
backend/apps/customers/tests/test_report_doc_tiers.py
backend/apps/customers/tests/test_report_doc_trend.py
backend/apps/customers/tests/test_report_doc_charts.py
backend/apps/customers/tests/test_generate_monthly_customer_report_command.py
backend/requirements.txt                   # add python-docx, matplotlib
```

---

### Task 1: Tier classification (pure logic)

**Files:**
- Create: `backend/apps/customers/report_doc/__init__.py` (empty)
- Create: `backend/apps/customers/report_doc/tiers.py`
- Test: `backend/apps/customers/tests/test_report_doc_tiers.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - Constants: `TIER_CHUA_NOTE="chua_note"`, `TIER_CO_BAN="co_ban"`, `TIER_DAY_DU="day_du"`, `TIER_ORDER=(TIER_DAY_DU, TIER_CO_BAN, TIER_CHUA_NOTE)`, `TIER_LABELS: dict[str,str]`, `PRE_FULL_MIN=5`, `ASSESSMENT_VALUE_FIELDS: tuple[str,...]` (bare assessment field names needed to classify a row).
  - `is_filled(value) -> bool`
  - `classify(row: dict) -> str` — `row` keyed by BARE assessment field names; returns a `TIER_*` constant.

- [ ] **Step 1: Write the failing test**

```python
# backend/apps/customers/tests/test_report_doc_tiers.py
from apps.customers.report_doc import tiers


def _core_row(**extra):
    row = {"customer_classification_code": "easy_close", "sales_status_code": "closed",
           "pain_level_code": "high"}  # satisfies CORE (classification + sales + 1 core-any)
    row.update(extra)
    return row


def test_empty_row_is_chua_note():
    assert tiers.classify({}) == tiers.TIER_CHUA_NOTE


def test_classification_and_sales_only_without_core_any_is_chua_note():
    row = {"customer_classification_code": "easy_close", "sales_status_code": "closed"}
    assert tiers.classify(row) == tiers.TIER_CHUA_NOTE


def test_core_group_is_co_ban():
    assert tiers.classify(_core_row()) == tiers.TIER_CO_BAN


def test_core_plus_five_pre_plus_post_is_day_du():
    row = _core_row(
        pain_duration_code="months", lifestyle_habit_codes=["sit_long"],
        relax_time_codes=["evening"], delax_source_codes=["fb"],
        purchase_barrier_codes=["price"],  # pain_level (from _core_row) + these = 6 pre fields
        post_purchase_rating_code="good",
    )
    assert tiers.classify(row) == tiers.TIER_DAY_DU


def test_core_plus_four_pre_plus_post_is_co_ban():
    row = _core_row(
        pain_duration_code="months", lifestyle_habit_codes=["sit_long"],
        delax_source_codes=["fb"],  # pain_level + 3 = 4 pre fields (< 5)
        post_purchase_rating_code="good",
    )
    assert tiers.classify(row) == tiers.TIER_CO_BAN


def test_core_plus_five_pre_without_post_is_co_ban():
    row = _core_row(
        pain_duration_code="months", lifestyle_habit_codes=["sit_long"],
        relax_time_codes=["evening"], delax_source_codes=["fb"],
        purchase_barrier_codes=["price"],  # 6 pre, no post
    )
    assert tiers.classify(row) == tiers.TIER_CO_BAN


def test_used_massage_false_counts_as_filled():
    assert tiers.is_filled(False) is True
    assert tiers.is_filled(None) is False
    assert tiers.is_filled("") is False
    assert tiers.is_filled("  ") is False
    assert tiers.is_filled([]) is False
    assert tiers.is_filled(["x"]) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/customers/tests/test_report_doc_tiers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.customers.report_doc'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/apps/customers/report_doc/__init__.py` (empty file), then:

```python
# backend/apps/customers/report_doc/tiers.py
"""Pure note-completeness tier classification for a single order assessment row.

No ORM / I/O. `row` is a plain dict keyed by BARE OrderAssessment field names.
Tuning lives here (PRE_FULL_MIN, field groups) so the business rule is one place.
"""
from __future__ import annotations

TIER_CHUA_NOTE = "chua_note"
TIER_CO_BAN = "co_ban"
TIER_DAY_DU = "day_du"
TIER_ORDER = (TIER_DAY_DU, TIER_CO_BAN, TIER_CHUA_NOTE)
TIER_LABELS = {
    TIER_DAY_DU: "Đầy đủ",
    TIER_CO_BAN: "Cơ bản",
    TIER_CHUA_NOTE: "Chưa note",
}

PRE_FULL_MIN = 5

# CORE = classification + sales_status + at least one of these portrait fields.
_CORE_REQUIRED = ("customer_classification_code", "sales_status_code")
_CORE_ANY = ("pain_level_code", "delax_source_codes", "occupation_code", "age_text")

# 8 "before purchase" fields; >= PRE_FULL_MIN of them (plus a post field) -> Đầy đủ.
_PRE_FULL_FIELDS = (
    "pain_level_code", "pain_duration_code", "lifestyle_habit_codes",
    "relax_time_codes", "delax_source_codes", "purchase_barrier_codes",
    "comparing_brand_text", "used_massage_before",
)
_POST_FIELDS = ("post_purchase_feedback_text", "post_purchase_rating_code")

ASSESSMENT_VALUE_FIELDS = tuple(dict.fromkeys(
    _CORE_REQUIRED + _CORE_ANY + _PRE_FULL_FIELDS + _POST_FIELDS
))


def is_filled(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return len(value) > 0
    return True  # bool (True/False both "answered") / int / etc.


def classify(row: dict) -> str:
    has_core = (
        all(is_filled(row.get(f)) for f in _CORE_REQUIRED)
        and any(is_filled(row.get(f)) for f in _CORE_ANY)
    )
    if not has_core:
        return TIER_CHUA_NOTE
    pre_count = sum(1 for f in _PRE_FULL_FIELDS if is_filled(row.get(f)))
    has_post = any(is_filled(row.get(f)) for f in _POST_FIELDS)
    if pre_count >= PRE_FULL_MIN and has_post:
        return TIER_DAY_DU
    return TIER_CO_BAN
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/customers/tests/test_report_doc_tiers.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/apps/customers/report_doc/__init__.py backend/apps/customers/report_doc/tiers.py backend/apps/customers/tests/test_report_doc_tiers.py
git commit -m "feat: note-completeness tier classification for customer report"
```

---

### Task 2: Monthly trend aggregation

**Files:**
- Create: `backend/apps/customers/report_doc/trend.py`
- Test: `backend/apps/customers/tests/test_report_doc_trend.py`

**Interfaces:**
- Consumes: `tiers.classify`, `tiers.ASSESSMENT_VALUE_FIELDS`, `tiers.TIER_*`.
- Produces:
  - `@dataclass(frozen=True) MonthStats`: `month: date` (first-of-month), `total_orders: int`, `chua_note: int`, `co_ban: int`, `day_du: int`, `with_assessment: int`. Property `noted(self) -> int` = `co_ban + day_du`; `pct_noted(self) -> float`.
  - `month_floor(d: date) -> date`
  - `monthly_series(end_month: date, n: int) -> list[MonthStats]` — ascending by month, length `n`, missing months zero-filled. `end_month` is any date within the last (report) month; it is floored internally.

- [ ] **Step 1: Write the failing test**

```python
# backend/apps/customers/tests/test_report_doc_trend.py
from datetime import date, datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.customers.report_doc import trend
from apps.orders.models import Order, OrderAssessment


class MonthlySeriesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(name="CH-A", code="ch-a")

    def _order(self, *, created: datetime, assessment: dict | None):
        customer = Customer.objects.create(full_name="X")
        order = Order.objects.create(
            customer=customer, channel=cls_channel(self), status=3,
            total_after_discount=Decimal("0"),
        )
        Order.objects.filter(pk=order.pk).update(created_at=created)
        if assessment is not None:
            OrderAssessment.objects.create(order=order, **assessment)
        return order

    def test_series_length_and_order(self):
        end = date(2026, 6, 1)
        series = trend.monthly_series(end, 3)
        assert [m.month for m in series] == [date(2026, 4, 1), date(2026, 5, 1), date(2026, 6, 1)]

    def test_buckets_and_tiers(self):
        tz = timezone.get_current_timezone()
        # June: 1 day_du, 1 co_ban, 1 chua_note (no assessment)
        self._order(created=datetime(2026, 6, 10, 9, 0, tzinfo=tz), assessment=dict(
            customer_classification_code="c", sales_status_code="closed",
            pain_level_code="high", pain_duration_code="m", lifestyle_habit_codes=["a"],
            relax_time_codes=["e"], delax_source_codes=["fb"], post_purchase_rating_code="good",
        ))
        self._order(created=datetime(2026, 6, 11, 9, 0, tzinfo=tz), assessment=dict(
            customer_classification_code="c", sales_status_code="closed", pain_level_code="high",
        ))
        self._order(created=datetime(2026, 6, 12, 9, 0, tzinfo=tz), assessment=None)
        # May: 1 chua_note (empty assessment row present but no portrait)
        self._order(created=datetime(2026, 5, 5, 9, 0, tzinfo=tz), assessment=dict())

        series = {m.month: m for m in trend.monthly_series(date(2026, 6, 15), 2)}
        june = series[date(2026, 6, 1)]
        assert june.total_orders == 3
        assert june.day_du == 1
        assert june.co_ban == 1
        assert june.chua_note == 1
        assert june.with_assessment == 2
        assert june.noted == 2
        may = series[date(2026, 5, 1)]
        assert may.total_orders == 1
        assert may.chua_note == 1
```

> Note: replace the `cls_channel(self)` placeholder — use `self.channel`. (Kept explicit so the implementer wires the fixture channel.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/customers/tests/test_report_doc_trend.py -v`
Expected: FAIL — `ModuleNotFoundError` / `AttributeError: module 'apps.customers.report_doc.trend' has no attribute 'monthly_series'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/apps/customers/report_doc/trend.py
"""Monthly note-completeness trend via ONE windowed query, bucketed in Python.

Never calls CustomerReportService.summary() per month (runaway-query risk).
Denominator = ALL in-scope orders (left-join assessment) so "Chưa note" is counted.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from dateutil.relativedelta import relativedelta
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.orders.models import Order
from apps.orders.repositories import EXCLUDED_BRANCH_IDS

from . import tiers

_ASSESSMENT_PREFIX = "assessment__"


@dataclass(frozen=True)
class MonthStats:
    month: date
    total_orders: int
    chua_note: int
    co_ban: int
    day_du: int
    with_assessment: int

    @property
    def noted(self) -> int:
        return self.co_ban + self.day_du

    @property
    def pct_noted(self) -> float:
        return (self.noted / self.total_orders * 100.0) if self.total_orders else 0.0


def month_floor(d: date) -> date:
    return d.replace(day=1)


def monthly_series(end_month: date, n: int) -> list[MonthStats]:
    end_first = month_floor(end_month)
    start_first = end_first - relativedelta(months=n - 1)
    end_exclusive = end_first + relativedelta(months=1)

    tz = timezone.get_current_timezone()
    qs = (
        Order.objects.filter(deleted_at__isnull=True)
        .exclude(branch_id__in=EXCLUDED_BRANCH_IDS)
        .filter(created_at__gte=_aware(start_first, tz), created_at__lt=_aware(end_exclusive, tz))
        .annotate(_m=TruncMonth("created_at", tzinfo=tz))
    )
    value_cols = [f"{_ASSESSMENT_PREFIX}{f}" for f in tiers.ASSESSMENT_VALUE_FIELDS]
    rows = qs.values("_m", "assessment__id", *value_cols)

    buckets: dict[date, dict] = {}
    for r in rows:
        m = r["_m"].date().replace(day=1)
        b = buckets.setdefault(m, {"total": 0, tiers.TIER_CHUA_NOTE: 0,
                                   tiers.TIER_CO_BAN: 0, tiers.TIER_DAY_DU: 0, "with_assessment": 0})
        b["total"] += 1
        if r["assessment__id"] is not None:
            b["with_assessment"] += 1
        bare = {f: r[f"{_ASSESSMENT_PREFIX}{f}"] for f in tiers.ASSESSMENT_VALUE_FIELDS}
        b[tiers.classify(bare)] += 1

    result: list[MonthStats] = []
    cursor = start_first
    while cursor <= end_first:
        b = buckets.get(cursor)
        if b is None:
            result.append(MonthStats(cursor, 0, 0, 0, 0, 0))
        else:
            result.append(MonthStats(
                cursor, b["total"], b[tiers.TIER_CHUA_NOTE], b[tiers.TIER_CO_BAN],
                b[tiers.TIER_DAY_DU], b["with_assessment"],
            ))
        cursor += relativedelta(months=1)
    return result


def _aware(d: date, tz):
    from datetime import datetime

    return timezone.make_aware(datetime(d.year, d.month, d.day), tz)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/customers/tests/test_report_doc_trend.py -v`
Expected: PASS (2 tests). Fix the `cls_channel(self)` placeholder to `self.channel` first.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/customers/report_doc/trend.py backend/apps/customers/tests/test_report_doc_trend.py
git commit -m "feat: monthly note-completeness trend aggregation"
```

---

### Task 3: Chart builders (matplotlib)

**Files:**
- Modify: `backend/requirements.txt` (add `matplotlib`)
- Create: `backend/apps/customers/report_doc/charts.py`
- Test: `backend/apps/customers/tests/test_report_doc_charts.py`

**Interfaces:**
- Consumes: `trend.MonthStats`, `tiers.TIER_*`/`TIER_LABELS`.
- Produces (each returns non-empty PNG `bytes`):
  - `tier_pie(stats: MonthStats) -> bytes`
  - `tier_trend_line(series: list[MonthStats]) -> bytes`
  - `compare_bar(title: str, labels: list[str], cur: list[float], prev: list[float]) -> bytes`

- [ ] **Step 1: Add matplotlib to requirements**

Add one line to `backend/requirements.txt` (keep alphabetical-ish, near `kombu`/`msgpack`):

```
matplotlib==3.10.0
```

Then in the test container: `pip install matplotlib==3.10.0` (or rebuild the test image). Document that the backend image must be rebuilt for the command to run in deployment.

- [ ] **Step 2: Write the failing test**

```python
# backend/apps/customers/tests/test_report_doc_charts.py
from datetime import date

from apps.customers.report_doc import charts
from apps.customers.report_doc.trend import MonthStats


def _series():
    return [
        MonthStats(date(2026, 5, 1), 100, 60, 30, 10, 40),
        MonthStats(date(2026, 6, 1), 120, 50, 50, 20, 70),
    ]


def test_tier_pie_returns_png_bytes():
    out = charts.tier_pie(_series()[-1])
    assert isinstance(out, bytes) and out[:8] == b"\x89PNG\r\n\x1a\n"


def test_tier_trend_line_returns_png_bytes():
    out = charts.tier_trend_line(_series())
    assert isinstance(out, bytes) and len(out) > 100


def test_compare_bar_returns_png_bytes():
    out = charts.compare_bar("Phân loại", ["A", "B"], [10.0, 20.0], [12.0, 8.0])
    assert isinstance(out, bytes) and len(out) > 100
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest apps/customers/tests/test_report_doc_charts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.customers.report_doc.charts'`

- [ ] **Step 4: Write minimal implementation**

```python
# backend/apps/customers/report_doc/charts.py
"""matplotlib chart builders returning PNG bytes. Agg backend (no GUI)."""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from . import tiers  # noqa: E402
from .trend import MonthStats  # noqa: E402

_BLUE = "#0284c7"
_BLUE_LIGHT = "#7dd3fc"


def _fig_to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def tier_pie(stats: MonthStats) -> bytes:
    fig, ax = plt.subplots(figsize=(4, 4))
    values = [stats.day_du, stats.co_ban, stats.chua_note]
    labels = [tiers.TIER_LABELS[tiers.TIER_DAY_DU], tiers.TIER_LABELS[tiers.TIER_CO_BAN],
              tiers.TIER_LABELS[tiers.TIER_CHUA_NOTE]]
    if sum(values) == 0:
        values, labels = [1], ["Không có dữ liệu"]
    ax.pie(values, labels=labels, autopct="%1.0f%%", colors=[_BLUE, _BLUE_LIGHT, "#e2e8f0"])
    ax.set_title("Tỉ lệ note chân dung")
    return _fig_to_png(fig)


def tier_trend_line(series: list[MonthStats]) -> bytes:
    fig, ax = plt.subplots(figsize=(7, 3.2))
    x = [m.month.strftime("%m/%y") for m in series]
    ax.plot(x, [m.pct_noted for m in series], marker="o", color=_BLUE, label="% đơn được note")
    ax.set_ylim(0, 100)
    ax.set_ylabel("%")
    ax.set_title("Xu hướng tỉ lệ note theo tháng")
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend()
    return _fig_to_png(fig)


def compare_bar(title: str, labels: list[str], cur: list[float], prev: list[float]) -> bytes:
    fig, ax = plt.subplots(figsize=(7, 3.6))
    import numpy as np

    idx = np.arange(len(labels))
    width = 0.38
    ax.bar(idx - width / 2, prev, width, label="Tháng trước", color=_BLUE_LIGHT)
    ax.bar(idx + width / 2, cur, width, label="Tháng này", color=_BLUE)
    ax.set_xticks(idx)
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_title(title)
    ax.legend()
    return _fig_to_png(fig)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest apps/customers/tests/test_report_doc_charts.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/requirements.txt backend/apps/customers/report_doc/charts.py backend/apps/customers/tests/test_report_doc_charts.py
git commit -m "feat: matplotlib chart builders for customer report"
```

---

### Task 4: Word document builder

**Files:**
- Modify: `backend/requirements.txt` (add `python-docx`)
- Create: `backend/apps/customers/report_doc/docx_builder.py`
- Test: `backend/apps/customers/tests/test_report_doc_charts.py` is charts-only; add doc test to a new block in `test_generate_monthly_customer_report_command.py` (Task 6). No standalone unit test here beyond a smoke assert folded into Task 6, because building requires a full context assembled by the service.

**Interfaces:**
- Consumes: `trend.MonthStats`, chart PNG bytes, and `CustomerReportService.summary()` dicts.
- Produces:
  - `@dataclass ReportContext`: `month: date`, `cur_summary: dict`, `prev_summary: dict | None`, `cur_stats: MonthStats`, `prev_stats: MonthStats | None`, `series: list[MonthStats]`, `charts: dict[str, bytes]`, `small_sample: bool`, `small_sample_threshold: int`.
  - `build(ctx: ReportContext) -> bytes`

- [ ] **Step 1: Add python-docx to requirements**

Add to `backend/requirements.txt`:

```
python-docx==1.1.2
```

Install in the test container: `pip install python-docx==1.1.2` (or rebuild test image).

- [ ] **Step 2: Write the failing test (folded, minimal)**

Add to `backend/apps/customers/tests/test_report_doc_charts.py`:

```python
def test_docx_builder_produces_openable_document():
    import io
    from datetime import date

    from docx import Document

    from apps.customers.report_doc import charts, docx_builder
    from apps.customers.report_doc.trend import MonthStats

    stats = MonthStats(date(2026, 6, 1), 120, 50, 50, 20, 70)
    ctx = docx_builder.ReportContext(
        month=date(2026, 6, 1),
        cur_summary={"totals": {"total_records": 70, "total_customers": 60}},
        prev_summary={"totals": {"total_records": 40, "total_customers": 35}},
        cur_stats=stats,
        prev_stats=MonthStats(date(2026, 5, 1), 100, 60, 30, 10, 40),
        series=[MonthStats(date(2026, 5, 1), 100, 60, 30, 10, 40), stats],
        charts={"tier_pie": charts.tier_pie(stats),
                "tier_trend": charts.tier_trend_line([stats])},
        small_sample=False,
        small_sample_threshold=30,
    )
    out = docx_builder.build(ctx)
    assert isinstance(out, bytes) and len(out) > 1000
    doc = Document(io.BytesIO(out))
    headings = [p.text for p in doc.paragraphs]
    assert any("Tỉ lệ note" in h for h in headings)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest apps/customers/tests/test_report_doc_charts.py::test_docx_builder_produces_openable_document -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.customers.report_doc.docx_builder'`

- [ ] **Step 4: Write minimal implementation**

```python
# backend/apps/customers/report_doc/docx_builder.py
"""Assemble the monthly customer report .docx from a prepared ReportContext."""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date

from docx import Document
from docx.shared import Inches

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


def _delta_pct_points(cur: float, prev: float | None) -> str:
    if prev is None:
        return "—"
    d = cur - prev
    return f"{d:+.1f}đ%"


def _add_image(doc: Document, png: bytes, width_in: float = 6.0) -> None:
    doc.add_picture(io.BytesIO(png), width=Inches(width_in))


def _pct(part: int, whole: int) -> float:
    return (part / whole * 100.0) if whole else 0.0


def build(ctx: ReportContext) -> bytes:
    doc = Document()
    doc.add_heading(f"Báo cáo khách hàng tháng {ctx.month:%m/%Y}", level=0)
    doc.add_paragraph(
        f"Tổng đơn tháng này: {ctx.cur_stats.total_orders} | "
        f"tháng trước: {ctx.prev_stats.total_orders if ctx.prev_stats else '—'}"
    )
    if ctx.small_sample:
        doc.add_paragraph(
            f"⚠ Mẫu nhỏ: số đơn có dữ liệu đánh giá < {ctx.small_sample_threshold}. "
            "Các phân tích nhu cầu/sau mua chỉ mang tính tham khảo."
        )

    # Section 2: note completeness tiers
    doc.add_heading("Tỉ lệ note chân dung", level=1)
    cur, prev = ctx.cur_stats, ctx.prev_stats
    table = doc.add_table(rows=1, cols=4)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text = "Mức độ", "Tháng này", "Tháng trước", "Δ"
    tier_rows = [
        ("Đầy đủ", cur.day_du, prev.day_du if prev else None, cur.total_orders, prev.total_orders if prev else None),
        ("Cơ bản", cur.co_ban, prev.co_ban if prev else None, cur.total_orders, prev.total_orders if prev else None),
        ("Chưa note", cur.chua_note, prev.chua_note if prev else None, cur.total_orders, prev.total_orders if prev else None),
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

    # Sections 3-6: distribution comparisons rendered from summary() dicts + charts.
    for key, title, chart_key in [
        ("classification_distribution", "Phân loại khách hàng", "classification_bar"),
        ("need_type_distribution", "Nhu cầu khách hàng", "need_bar"),
        ("delax_source_distribution", "Trước mua — nguồn biết đến", "source_bar"),
        ("feedback_rating_distribution", "Sau mua — đánh giá", "rating_bar"),
    ]:
        doc.add_heading(title, level=1)
        _distribution_table(doc, ctx.cur_summary.get(key, []), (ctx.prev_summary or {}).get(key, []))
        if chart_key in ctx.charts:
            _add_image(doc, ctx.charts[chart_key])

    # Section 7: notes
    doc.add_heading("Nhận xét & khuyến nghị", level=1)
    for line in _auto_insights(ctx):
        doc.add_paragraph(line, style="List Bullet")

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _distribution_table(doc: Document, cur_items: list[dict], prev_items: list[dict]) -> None:
    prev_by_code = {i["code"]: i for i in prev_items}
    table = doc.add_table(rows=1, cols=4)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text = "Nhóm", "Tháng này", "Tháng trước", "Δ%"
    for it in cur_items[:12]:
        p = prev_by_code.get(it["code"])
        row = table.add_row().cells
        row[0].text = str(it.get("label") or it["code"])
        row[1].text = f'{it["count"]} ({it["percentage"]:.1f}%)'
        row[2].text = "—" if p is None else f'{p["count"]} ({p["percentage"]:.1f}%)'
        row[3].text = "—" if p is None else f'{it["percentage"] - p["percentage"]:+.1f}'


def _auto_insights(ctx: ReportContext) -> list[str]:
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
    if not out:
        out.append("Không có thay đổi đáng kể so với tháng trước.")
    return out
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest apps/customers/tests/test_report_doc_charts.py::test_docx_builder_produces_openable_document -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/requirements.txt backend/apps/customers/report_doc/docx_builder.py backend/apps/customers/tests/test_report_doc_charts.py
git commit -m "feat: word document builder for monthly customer report"
```

---

### Task 5: Report orchestration service

**Files:**
- Create: `backend/apps/customers/report_doc/service.py`
- Test: covered by the command smoke test in Task 6 (service has no logic beyond wiring; a dedicated unit test would duplicate Task 6). If desired, add a focused test asserting `build()` returns `(bytes, dict)` — included in Task 6 file.

**Interfaces:**
- Consumes: `CustomerReportService.summary`, `trend.monthly_series`, `charts.*`, `docx_builder.ReportContext`/`build`, `tiers`.
- Produces:
  - `MonthlyReportDocService.build(month: date, months_trend: int = 12) -> tuple[bytes, dict]` — returns (docx bytes, stdout-summary dict with keys `month`, `total_orders`, `day_du`, `co_ban`, `chua_note`, `pct_noted`).
  - `SMALL_SAMPLE = 30`

- [ ] **Step 1: Write implementation**

```python
# backend/apps/customers/report_doc/service.py
"""Orchestrate the monthly customer report: numbers -> charts -> docx bytes."""
from __future__ import annotations

from datetime import date

from dateutil.relativedelta import relativedelta

from apps.customers.report_service import CustomerReportService

from . import charts, tiers
from .docx_builder import ReportContext, build as build_docx
from .trend import MonthStats, month_floor, monthly_series

SMALL_SAMPLE = 30


class MonthlyReportDocService:
    @staticmethod
    def build(month: date, months_trend: int = 12) -> tuple[bytes, dict]:
        m_start = month_floor(month)
        m_end = m_start + relativedelta(months=1) - relativedelta(days=1)
        p_start = m_start - relativedelta(months=1)
        p_end = m_start - relativedelta(days=1)

        cur_summary = CustomerReportService.summary(
            date_from=m_start.isoformat(), date_to=m_end.isoformat())
        prev_summary = CustomerReportService.summary(
            date_from=p_start.isoformat(), date_to=p_end.isoformat())

        series = monthly_series(m_start, months_trend)
        cur_stats = next((s for s in series if s.month == m_start), MonthStats(m_start, 0, 0, 0, 0, 0))
        prev_stats = next((s for s in series if s.month == p_start), None)

        small_sample = cur_stats.with_assessment < SMALL_SAMPLE

        chart_pngs = {
            "tier_pie": charts.tier_pie(cur_stats),
            "tier_trend": charts.tier_trend_line(series),
        }
        for key, chart_key in [
            ("classification_distribution", "classification_bar"),
            ("need_type_distribution", "need_bar"),
            ("delax_source_distribution", "source_bar"),
            ("feedback_rating_distribution", "rating_bar"),
        ]:
            chart_pngs[chart_key] = _compare_chart(key, cur_summary, prev_summary)

        ctx = ReportContext(
            month=m_start, cur_summary=cur_summary, prev_summary=prev_summary,
            cur_stats=cur_stats, prev_stats=prev_stats, series=series,
            charts=chart_pngs, small_sample=small_sample, small_sample_threshold=SMALL_SAMPLE,
        )
        doc_bytes = build_docx(ctx)
        summary = {
            "month": m_start.isoformat(),
            "total_orders": cur_stats.total_orders,
            "day_du": cur_stats.day_du,
            "co_ban": cur_stats.co_ban,
            "chua_note": cur_stats.chua_note,
            "pct_noted": round(cur_stats.pct_noted, 1),
        }
        return doc_bytes, summary


def _compare_chart(key: str, cur: dict, prev: dict) -> bytes:
    cur_items = cur.get(key, [])[:8]
    prev_by = {i["code"]: i for i in prev.get(key, [])}
    labels = [str(i.get("label") or i["code"]) for i in cur_items]
    cur_vals = [float(i["percentage"]) for i in cur_items]
    prev_vals = [float(prev_by.get(i["code"], {}).get("percentage", 0.0)) for i in cur_items]
    title = key.replace("_distribution", "").replace("_", " ")
    return charts.compare_bar(title, labels, cur_vals, prev_vals)
```

- [ ] **Step 2: Commit** (tested via Task 6)

```bash
git add backend/apps/customers/report_doc/service.py
git commit -m "feat: monthly customer report orchestration service"
```

---

### Task 6: Management command + end-to-end smoke test

**Files:**
- Create: `backend/apps/customers/management/__init__.py` (empty, if missing)
- Create: `backend/apps/customers/management/commands/__init__.py` (empty, if missing)
- Create: `backend/apps/customers/management/commands/generate_monthly_customer_report.py`
- Test: `backend/apps/customers/tests/test_generate_monthly_customer_report_command.py`

**Interfaces:**
- Consumes: `MonthlyReportDocService.build`.
- Produces: command `generate_monthly_customer_report` with args `--month`, `--out`, `--months-trend`.

- [ ] **Step 1: Write the failing test**

```python
# backend/apps/customers/tests/test_generate_monthly_customer_report_command.py
import io
from datetime import datetime
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from docx import Document

from apps.channels_core.models import Channel
from apps.customers.models import Customer, CustomerSegmentCategory
from apps.customers.segmentation import SEGMENT_CATALOG_SEED
from apps.orders.models import Order, OrderAssessment


class GenerateMonthlyReportCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for item in SEGMENT_CATALOG_SEED:
            CustomerSegmentCategory.objects.get_or_create(
                code=item["code"],
                defaults=dict(name=item["name"], criteria=item.get("criteria", ""),
                              care_priority=item.get("care_priority", ""), sort_order=item["sort_order"]))
        cls.channel = Channel.objects.create(name="CH-A", code="ch-a")

    def _order(self, created, assessment):
        customer = Customer.objects.create(full_name="X")
        order = Order.objects.create(customer=customer, channel=self.channel, status=3,
                                     total_after_discount=Decimal("0"))
        Order.objects.filter(pk=order.pk).update(created_at=created)
        if assessment is not None:
            OrderAssessment.objects.create(order=order, **assessment)

    def test_bad_month_raises(self):
        with self.assertRaises(CommandError):
            call_command("generate_monthly_customer_report", "--month", "2026/06")

    def test_generates_docx_file(self, tmp_path=None):
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
        assert any("tháng 06/2026" in p.text for p in doc.paragraphs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/customers/tests/test_generate_monthly_customer_report_command.py -v`
Expected: FAIL — `CommandError: Unknown command: 'generate_monthly_customer_report'`

- [ ] **Step 3: Write minimal implementation**

Create the two `__init__.py` files (empty) if they do not exist, then:

```python
# backend/apps/customers/management/commands/generate_monthly_customer_report.py
import os
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from apps.customers.report_doc.service import MonthlyReportDocService


class Command(BaseCommand):
    help = "Sinh báo cáo khách hàng tháng (.docx) từ order_assessment, so sánh với tháng trước."

    def add_arguments(self, parser):
        parser.add_argument("--month", required=True, help="Tháng báo cáo YYYY-MM")
        parser.add_argument("--out", default=None, help="Đường dẫn file .docx xuất ra")
        parser.add_argument("--months-trend", type=int, default=12,
                            help="Số tháng cho biểu đồ xu hướng (mặc định 12)")

    def handle(self, *args, **options):
        raw = options["month"]
        try:
            month = datetime.strptime(raw, "%Y-%m").date().replace(day=1)
        except ValueError:
            raise CommandError(f"--month không hợp lệ: {raw!r}. Định dạng đúng: YYYY-MM (vd 2026-06).")

        months_trend = max(1, options["months_trend"])
        doc_bytes, summary = MonthlyReportDocService.build(month, months_trend)

        out_path = options["out"] or os.path.join("reports", f"bao-cao-khach-hang-{month:%Y-%m}.docx")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(doc_bytes)

        self.stdout.write(self.style.SUCCESS(f"Đã ghi: {out_path}"))
        self.stdout.write(
            f"Tổng đơn={summary['total_orders']} | Đầy đủ={summary['day_du']} "
            f"| Cơ bản={summary['co_ban']} | Chưa note={summary['chua_note']} "
            f"| %được note={summary['pct_noted']}"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/customers/tests/test_generate_monthly_customer_report_command.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the whole report_doc suite**

Run: `pytest apps/customers/tests/test_report_doc_tiers.py apps/customers/tests/test_report_doc_trend.py apps/customers/tests/test_report_doc_charts.py apps/customers/tests/test_generate_monthly_customer_report_command.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/customers/management backend/apps/customers/tests/test_generate_monthly_customer_report_command.py
git commit -m "feat: generate_monthly_customer_report management command"
```

---

## Self-Review

**Spec coverage:**
- §4 command interface → Task 6 (args, default out, stdout summary). ✔
- §5 module layout → Tasks 1–6 map 1:1 to the files. ✔
- §7 tier rule + all-orders denominator → Task 1 (`classify`) + Task 2 (`monthly_series` counts all orders, left-join assessment). ✔
- §8 doc sections → Task 4 `build()` (bìa, tỉ lệ note, phân loại, nhu cầu, trước mua, sau mua, nhận xét). ✔
- §9 error/edge (bad month, no data, prev missing → "—", makedirs, small-sample) → Task 6 (bad month, makedirs) + Task 4 (`—` deltas, small-sample banner) + Task 5 (`small_sample`). ✔
- §10 deps → Tasks 3 & 4 add matplotlib / python-docx. ✔
- §11 tests → Tasks 1,2,3,4,6. ✔

**Placeholder scan:** Two intentional inline notes remain in test scaffolding (`cls_channel(self)` → use `self.channel`; the `tmp_path` arg unused — implementer uses `tempfile`). Both are called out at their step. No TODO/TBD in production code.

**Type consistency:** `MonthStats` fields/properties (`noted`, `pct_noted`, `with_assessment`) used identically across trend/charts/docx/service. `ReportContext` fields match between `docx_builder` definition and `service` construction. `classify` returns `TIER_*` constants consumed as dict keys in trend/charts. `summary()` distribution items use keys `code/label/count/percentage` (verified against existing `ReportDistributionItem`).

**Note on "no data" month:** if `total_orders == 0`, `tier_pie` renders a "Không có dữ liệu" slice and tables render header-only — command still writes a valid file (§9). Confirmed by code paths in Tasks 3–4.
