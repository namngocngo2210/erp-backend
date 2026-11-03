# Kho phân tích `dwh` — Khối 1 (S1 + S2a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dựng schema `dwh` với 3 materialized view (`dim_customer_profile`, `fct_order_enriched`, `customer_360`), task Celery refresh hằng đêm, và endpoint đo độ phủ chân dung — tất cả chạy được và đối chiếu khớp số với báo cáo hiện có.

**Architecture:** App Django mới `apps/analytics` theo clean architecture. Matview tạo bằng `RunSQL` trong `SeparateDatabaseAndState` (không có model Django). Refresh bằng Celery task trên queue `slow`, khoá Redis `single_flight`, bọc `statement_timeout`. Đọc mart bằng SQL thuần qua `infrastructure/mart_queries.py`.

**Tech Stack:** Django 5 + PostgreSQL 16 + Celery + pytest. Không thêm dependency mới.

**Spec:** `docs/dwh-customer-analytics.md`

## Global Constraints

- Python ≥ 3.12, ruff. Postgres 16.14.
- **Định nghĩa GTC — 5 mệnh đề, sinh từ hằng số, không chép cứng** (spec §2.0):
  `o.deleted_at IS NULL`, `o.status = 3`, `o.invoice_status IS DISTINCT FROM 2`,
  `NOT (o.branch_id = ANY(EXCLUDED_BRANCH_IDS))`,
  `(o.order_source IS NULL OR o.order_source NOT IN MIRROR_ORDER_SOURCES)`.
  Nguồn hằng số: `apps.common.business_constants.EXCLUDED_BRANCH_IDS` = `("1000000139",)`,
  `apps.common.order_scope.MIRROR_ORDER_SOURCES` = `("foxia", "cnv")` và hàm
  `sql_exclude_mirror_orders(alias)`.
- **Bẫy chuỗi rỗng**: `order_assessments` dùng `''` chứ không phải NULL. Mọi kiểm tra
  "có giá trị" phải là `NULLIF(btrim(coalesce(col,'')), '') IS NOT NULL`. Dùng
  `col IS NOT NULL` là sai — 481.523/485.242 dòng `age_text` là chuỗi rỗng.
- **Không hard-code id kênh trong SQL của mart** — `backend/conftest.py` có fixture autouse
  `_isolate_hardcoded_channel_ids` đẩy `KOC_CHANNEL_ID` / `SALE_FB_CHANNEL_IDS` sang giá trị
  giả; hard-code sẽ làm test lệch câm lặng.
- **Task Celery phải để lỗi thoát ra** — gom lỗi rồi `raise`, không nuốt. Bài học sự cố
  2026-07-22.
- Tên task Celery khai tường minh qua `name=`, và **bắt buộc** có entry trong
  `CELERY_TASK_ROUTES`, nếu không `apps/common/tests/test_celery_routes.py` sẽ đỏ.
- Migration chạm bảng app khác **phải khai `dependencies`** sang app đó.
- Không `cache.clear()` trong test (Redis dùng chung giữa các worktree).
- Lệnh chạy test (worktree này): xem Task 0.

---

## File Structure

| File | Trách nhiệm |
|---|---|
| `backend/apps/analytics/__init__.py` | rỗng |
| `backend/apps/analytics/apps.py` | `AnalyticsConfig` |
| `backend/apps/analytics/tasks.py` | **Facade** re-export task từ `infrastructure.tasks` |
| `backend/apps/analytics/sql/gtc.py` | Sinh mệnh đề GTC từ hằng số — dùng chung cho cả 3 matview |
| `backend/apps/analytics/migrations/0001_dwh_schema.py` | `CREATE SCHEMA dwh` |
| `backend/apps/analytics/migrations/0002_dim_customer_profile.py` | matview 1 |
| `backend/apps/analytics/migrations/0003_fct_order_enriched.py` | matview 2 |
| `backend/apps/analytics/migrations/0004_customer_360.py` | matview 3 |
| `backend/apps/analytics/application/mart_refresh_service.py` | Chọn CONCURRENTLY vs plain, refresh đúng thứ tự, gom lỗi |
| `backend/apps/analytics/infrastructure/tasks.py` | Task Celery thật |
| `backend/apps/analytics/infrastructure/mart_queries.py` | SQL đọc mart (Task 7) |
| `backend/apps/analytics/transport/views.py` | Endpoint `/coverage` |
| `backend/apps/analytics/transport/serializers.py` | Serializer cho `/coverage` |
| `backend/apps/analytics/urls.py` | urlpatterns |
| `backend/apps/analytics/management/commands/dwh_reconcile.py` | Đối chiếu số mart vs báo cáo cũ |
| `backend/apps/analytics/tests/helpers.py` | Factory order/customer/assessment cho app này |
| `backend/apps/analytics/tests/test_marts.py` | Test nội dung 3 matview |
| `backend/apps/analytics/tests/test_refresh_service.py` | Test service refresh |
| `backend/apps/analytics/tests/test_coverage_api.py` | Test endpoint |

**Lý do tách 4 migration thay vì 1**: mỗi matview là một deliverable độc lập có test riêng;
một reviewer có thể bác matview này mà chấp nhận matview kia. Cũng tránh một file RunSQL
400 dòng.

**Sai lệch có chủ đích so với spec**: cột `region` trong `fct_order_enriched` (spec §2.3)
**bị hoãn sang khối S2b**. Lý do: bảng ánh xạ `province_code → vùng` hiện chỉ tồn tại ở
frontend (`frontend/src/features/overview-report/regions.ts`), đưa vào SQL sẽ là chép cứng
một danh sách sẽ lệch. S2b sẽ chuyển nó thành nguồn dùng chung ở backend.

---

## Task 0: Chuẩn bị worktree và xác định lệnh test

**Files:** không sửa file nào.

- [ ] **Step 1: Xác định cách chạy test backend**

Container test có thể đã bị xoá. Kiểm tra trước:

```bash
docker ps -a --format '{{.Names}}' | grep -i desi
```

Nếu không có container test, dựng lại theo `docs/` hoặc chạy pytest trong container backend
đang chạy. Ghi lại lệnh chạy được vào đầu file này trước khi sang Task 1.

- [ ] **Step 2: Xác nhận baseline xanh**

Chạy: `pytest apps/common/tests/test_celery_routes.py -q`
Expected: PASS. Nếu đỏ sẵn thì dừng lại và báo — không được xây lên nền đỏ.

- [ ] **Step 3: Xác nhận import-linter chạy được**

Chạy: `cd backend && lint-imports --config .importlinter`
Expected: PASS (contract `App layering`).

---

## Task 1: Scaffold app `analytics` và đăng ký

**Files:**
- Create: `backend/apps/analytics/__init__.py`, `apps.py`, `urls.py`, `tests/__init__.py`
- Modify: `backend/config/settings_parts/base.py` (INSTALLED_APPS)
- Modify: `backend/config/urls.py`
- Modify: `backend/.importlinter`
- Test: `backend/apps/analytics/tests/test_app_registration.py`

**Interfaces:**
- Produces: app label `analytics`, `AnalyticsConfig.name = "apps.analytics"`, url prefix `/api/analytics/`.

- [ ] **Step 1: Write the failing test**

`backend/apps/analytics/tests/test_app_registration.py`:

```python
from django.apps import apps


def test_analytics_app_is_installed():
    config = apps.get_app_config("analytics")
    assert config.name == "apps.analytics"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/analytics/tests/test_app_registration.py -q`
Expected: FAIL — `LookupError: No installed app with label 'analytics'`.

- [ ] **Step 3: Tạo app**

`backend/apps/analytics/__init__.py` — file rỗng.

`backend/apps/analytics/apps.py`:

```python
from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.analytics"
    verbose_name = "Kho phân tích"
```

`backend/apps/analytics/urls.py`:

```python
urlpatterns: list = []
```

`backend/apps/analytics/tests/__init__.py` — file rỗng.

- [ ] **Step 4: Đăng ký INSTALLED_APPS**

Trong `backend/config/settings_parts/base.py`, thêm `"apps.analytics",` vào cuối khối app
nội bộ (ngay sau `"apps.exports",`).

- [ ] **Step 5: Đăng ký urls**

Trong `backend/config/urls.py`, thêm **TRƯỚC** dòng catch-all
`re_path(r"^api/", include("apps.audit.urls"))`:

```python
re_path(r"^api/analytics/?", include("apps.analytics.urls")),
```

- [ ] **Step 6: Khai tầng import-linter**

Trong `backend/.importlinter`, contract `App layering`, thêm `analytics` thành **một dòng
riêng nằm trên cùng**, ngay phía trên dòng `callcenter | contact_flow | warranty_codes`.

Không nhập chung dòng với `callcenter`: các app cùng dòng là độc lập và **cấm import nhau**,
mà `analytics` cần đọc dữ liệu `callcenter`.

⚠️ **KHÔNG thêm entry miễn trừ KHỐI A1b ở task này.** Config đặt
`unmatched_ignore_imports_alerting = error`, nghĩa là một entry `ignore_imports` **chưa
khớp** import thật nào sẽ làm `lint-imports` thoát mã 1 *trước cả khi chạy contract*. Entry
miễn trừ phải vào **cùng commit với import mà nó miễn trừ** — tức ở Task 3, khi
`tests/helpers.py` ra đời. (Đã kiểm chứng thực tế trong khối này.)

**KHÔNG** thêm dòng nào vào KHỐI B (nợ, chỉ được giảm).

- [ ] **Step 7: Run tests**

Run: `pytest apps/analytics/tests/test_app_registration.py -q`
Expected: PASS

Run: `cd backend && lint-imports --config .importlinter`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/apps/analytics backend/config/settings_parts/base.py backend/config/urls.py backend/.importlinter
git commit -m "feat(analytics): scaffold analytics app"
```

---

## Task 2: Migration schema `dwh` + helper GTC

**Files:**
- Create: `backend/apps/analytics/sql/__init__.py`, `backend/apps/analytics/sql/gtc.py`
- Create: `backend/apps/analytics/migrations/__init__.py`, `0001_dwh_schema.py`
- Test: `backend/apps/analytics/tests/test_gtc_sql.py`

**Interfaces:**
- Produces: `gtc_where(alias: str = "o") -> str` — trả về chuỗi SQL 5 mệnh đề nối bằng `AND`, không có từ khoá `WHERE`.

- [ ] **Step 1: Write the failing test**

`backend/apps/analytics/tests/test_gtc_sql.py`:

```python
from apps.analytics.sql.gtc import gtc_where


def test_gtc_where_has_all_five_clauses():
    sql = gtc_where("o")
    assert "o.deleted_at IS NULL" in sql
    assert "o.status = 3" in sql
    assert "o.invoice_status IS DISTINCT FROM 2" in sql
    assert "1000000139" in sql
    assert "'foxia'" in sql and "'cnv'" in sql


def test_gtc_where_handles_null_order_source():
    """order_source nullable: viết mỗi <> sẽ mất sạch đơn cũ."""
    sql = gtc_where("o")
    assert "o.order_source IS NULL OR" in sql


def test_gtc_where_respects_alias():
    sql = gtc_where("ord")
    assert "ord.deleted_at IS NULL" in sql
    assert "o.deleted_at" not in sql
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/analytics/tests/test_gtc_sql.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.analytics.sql'`

- [ ] **Step 3: Implement**

`backend/apps/analytics/sql/__init__.py` — file rỗng.

`backend/apps/analytics/sql/gtc.py`:

```python
"""Mệnh đề GTC dùng chung cho mọi matview trong schema `dwh`.

Sinh từ hằng số nguồn (`EXCLUDED_BRANCH_IDS`, `MIRROR_ORDER_SOURCES`) thay vì
chép cứng: thêm chi nhánh loại trừ hay đối tác mirror mới thì mart tự phủ theo,
không phải sửa 3 chỗ.

Định nghĩa gốc: apps/customers/report_trends.py:85-90.
"""

from apps.common.business_constants import EXCLUDED_BRANCH_IDS
from apps.common.order_scope import sql_exclude_mirror_orders

GTC_STATUS = 3          # OrderStatus.DELIVERED_SUCCESS
CANCELLED_INVOICE = 2


def gtc_where(alias: str = "o") -> str:
    """Trả 5 mệnh đề GTC nối bằng AND. KHÔNG kèm từ khoá WHERE."""
    prefix = f"{alias}." if alias else ""
    branches = ", ".join(f"'{b}'" for b in EXCLUDED_BRANCH_IDS)
    return (
        f"{prefix}deleted_at IS NULL"
        f" AND {prefix}status = {GTC_STATUS}"
        f" AND ({prefix}invoice_status IS DISTINCT FROM {CANCELLED_INVOICE})"
        f" AND NOT ({prefix}branch_id = ANY(ARRAY[{branches}]))"
        f" AND {sql_exclude_mirror_orders(alias)}"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/analytics/tests/test_gtc_sql.py -q`
Expected: PASS (3 test)

- [ ] **Step 5: Tạo migration schema**

`backend/apps/analytics/migrations/__init__.py` — file rỗng.

`backend/apps/analytics/migrations/0001_dwh_schema.py`:

```python
"""Tạo schema `dwh` — nơi chứa toàn bộ materialized view phân tích.

Tách riêng khỏi migration matview để `CREATE SCHEMA` chạy đúng một lần và các
migration matview sau chỉ phụ thuộc vào nó.
"""

from django.db import migrations


class Migration(migrations.Migration):
    initial = True
    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="CREATE SCHEMA IF NOT EXISTS dwh;",
                    reverse_sql="DROP SCHEMA IF EXISTS dwh CASCADE;",
                )
            ]
        )
    ]
```

- [ ] **Step 6: Verify migration applies**

Run: `python manage.py migrate analytics`
Expected: `Applying analytics.0001_dwh_schema... OK`

Kiểm chứng: `python manage.py dbshell -c "\dn dwh"` → thấy schema `dwh`.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/analytics/sql backend/apps/analytics/migrations backend/apps/analytics/tests/test_gtc_sql.py
git commit -m "feat(analytics): add dwh schema and shared GTC clause"
```

---

## Task 3: Matview `dwh.dim_customer_profile`

**Files:**
- Create: `backend/apps/analytics/migrations/0002_dim_customer_profile.py`
- Create: `backend/apps/analytics/tests/helpers.py`
- Create: `backend/apps/analytics/tests/test_marts.py`
- Modify: `backend/.importlinter` (KHỐI A1b — xem Step 0)

**Interfaces:**
- Consumes: `gtc_where()` từ Task 2 (chỉ dùng để lọc đơn khi tính, xem ghi chú dưới).
- Produces: matview `dwh.dim_customer_profile` với UNIQUE index `dim_customer_profile_pk` trên `customer_id`.

> **Ghi chú thiết kế**: matview này **KHÔNG lọc GTC**. Chân dung khai báo lấy từ mọi phiếu
> đánh giá, kể cả đơn huỷ — một khách khai tuổi ở đơn bị huỷ thì tuổi đó vẫn đúng. Chỉ loại
> đơn đã xoá mềm (`deleted_at IS NULL`) và đơn mirror.

- [ ] **Step 0: Khai miễn trừ import-linter — CÙNG commit với `helpers.py`**

Trong `backend/.importlinter`, **KHỐI A1b** (miễn trừ cho test fixture), thêm kèm comment
`# analytics: test dựng Order/Customer thật để kiểm chứng nội dung matview`:

```
apps.analytics.tests.* -> apps.customers.models
apps.analytics.tests.* -> apps.orders.models
apps.analytics.tests.* -> apps.channels_core.models
```

Thứ tự này là bắt buộc và không được đảo: `unmatched_ignore_imports_alerting = error` làm
`lint-imports` thoát mã 1 nếu entry miễn trừ **chưa** khớp import thật nào — nên entry và
import phải xuất hiện trong cùng một commit. Thêm sớm ở Task 1 đã thử và làm linter đỏ;
thêm muộn sau `helpers.py` thì linter đỏ vì vi phạm tầng.

Chỉ khai 3 dòng ở đây. Dòng `apps.analytics.tests.* -> apps.orders.tests.helpers` để
**Task 8** thêm, cũng theo đúng nguyên tắc trên.

**KHÔNG** thêm dòng nào vào KHỐI B.

- [ ] **Step 1: Write the failing test**

`backend/apps/analytics/tests/helpers.py`:

```python
"""Factory tối thiểu cho test của app analytics.

Không dùng lại `apps/orders/tests/conftest.py` — fixture ở đó chỉ có phạm vi
trong app `orders`.
"""

from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderAssessment

_seq = {"n": 0}


def _next() -> int:
    _seq["n"] += 1
    return _seq["n"]


def make_customer(**kwargs) -> Customer:
    n = _next()
    defaults = {"full_name": f"Khach {n}", "phone_normalized": f"09000000{n:02d}"}
    defaults.update(kwargs)
    return Customer.objects.create(**defaults)


def make_channel(**kwargs) -> Channel:
    n = _next()
    defaults = {"code": f"an-ch-{n}", "name": f"Kenh {n}"}
    defaults.update(kwargs)
    return Channel.objects.create(**defaults)


def make_order(customer=None, channel=None, **kwargs) -> Order:
    """Đơn GTC theo mặc định (status=3, invoice_status=None, order_source=None)."""
    defaults = {
        "customer": customer or make_customer(),
        "channel": channel or make_channel(),
        "status": 3,
        "total_after_discount": 1_000_000,
        "created_at": timezone.now(),
    }
    defaults.update(kwargs)
    return Order.objects.create(**defaults)


def make_assessment(order, **kwargs) -> OrderAssessment:
    return OrderAssessment.objects.create(order=order, **kwargs)
```

`backend/apps/analytics/tests/test_marts.py`:

```python
import pytest
from django.db import connection

from apps.analytics.tests.helpers import make_assessment, make_customer, make_order


def refresh(name: str) -> None:
    with connection.cursor() as cur:
        cur.execute(f"REFRESH MATERIALIZED VIEW dwh.{name}")


def fetch_profile(customer_id: int) -> dict | None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT age_text, age_text_src, filled_field_count, ai_field_count"
            " FROM dwh.dim_customer_profile WHERE customer_id = %s",
            [customer_id],
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "age_text": row[0],
        "age_text_src": row[1],
        "filled_field_count": row[2],
        "ai_field_count": row[3],
    }


@pytest.mark.django_db(transaction=True)
def test_empty_string_is_not_treated_as_a_value():
    """Bẫy chính: order_assessments dùng '' chứ không phải NULL."""
    customer = make_customer()
    order = make_order(customer=customer)
    make_assessment(order, age_text="", occupation_code="")

    refresh("dim_customer_profile")

    profile = fetch_profile(customer.id)
    assert profile is not None
    assert profile["age_text"] is None
    assert profile["filled_field_count"] == 0


@pytest.mark.django_db(transaction=True)
def test_takes_latest_non_empty_value():
    customer = make_customer()
    old = make_order(customer=customer)
    make_assessment(old, age_text="45_54")
    new = make_order(customer=customer)
    make_assessment(new, age_text="65_plus")

    refresh("dim_customer_profile")

    assert fetch_profile(customer.id)["age_text"] == "65_plus"


@pytest.mark.django_db(transaction=True)
def test_latest_empty_does_not_override_older_value():
    """Phiếu mới để trống thì vẫn giữ giá trị cũ, không xoá mất."""
    customer = make_customer()
    old = make_order(customer=customer)
    make_assessment(old, age_text="65_plus")
    new = make_order(customer=customer)
    make_assessment(new, age_text="")

    refresh("dim_customer_profile")

    assert fetch_profile(customer.id)["age_text"] == "65_plus"


@pytest.mark.django_db(transaction=True)
def test_provenance_marks_ai_filled_fields():
    customer = make_customer()
    order = make_order(customer=customer)
    make_assessment(order, age_text="65_plus", ai_filled_fields={"age_text": True})

    refresh("dim_customer_profile")

    profile = fetch_profile(customer.id)
    assert profile["age_text_src"] == "ai"
    assert profile["ai_field_count"] == 1
    assert profile["filled_field_count"] == 1


@pytest.mark.django_db(transaction=True)
def test_declared_value_is_marked_declared():
    customer = make_customer()
    order = make_order(customer=customer)
    make_assessment(order, age_text="65_plus")

    refresh("dim_customer_profile")

    profile = fetch_profile(customer.id)
    assert profile["age_text_src"] == "declared"
    assert profile["ai_field_count"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/analytics/tests/test_marts.py -q`
Expected: FAIL — `relation "dwh.dim_customer_profile" does not exist`

- [ ] **Step 3: Implement migration**

`backend/apps/analytics/migrations/0002_dim_customer_profile.py`:

```python
"""Matview chân dung khách hàng — 1 dòng/khách.

Mỗi trường lấy GIÁ TRỊ NON-EMPTY MỚI NHẤT theo `orders.created_at`, kèm cột
`*_src` cho biết giá trị đó do sale nhập (`declared`) hay AI bóc từ transcript
(`ai`, suy từ `order_assessments.ai_filled_fields`).

KHÔNG lọc GTC: khách khai tuổi ở đơn bị huỷ thì tuổi đó vẫn đúng.

Tạo WITH NO DATA để `migrate` không treo trên prod — lần nạp đầu do task
`refresh_dwh_marts` làm (và lần đầu bắt buộc refresh KHÔNG CONCURRENTLY).
"""

from django.db import migrations

from apps.common.order_scope import sql_exclude_mirror_orders

# Trường vô hướng: (tên cột nguồn, tên cột đích, khoá trong ai_filled_fields)
_SCALARS = [
    ("age_text", "age_text", "age_text"),
    ("occupation_code", "occupation_code", "occupation_code"),
    ("pain_level_code", "pain_level_code", "pain_level_code"),
    ("pain_duration_code", "pain_duration_code", "pain_duration_code"),
    ("comparing_brand_text", "comparing_brand_text", "comparing_brand_text"),
]


def _latest_non_empty(src: str, dst: str) -> str:
    val = f"NULLIF(btrim(coalesce(oa.{src}, '')), '')"
    return (
        f"(array_agg({val} ORDER BY o.created_at DESC, o.id DESC)"
        f" FILTER (WHERE {val} IS NOT NULL))[1] AS {dst}"
    )


def _latest_src(src: str, dst: str, ai_key: str) -> str:
    val = f"NULLIF(btrim(coalesce(oa.{src}, '')), '')"
    expr = (
        f"CASE WHEN coalesce(oa.ai_filled_fields, '{{}}'::jsonb) ? '{ai_key}'"
        f" THEN 'ai' ELSE 'declared' END"
    )
    return (
        f"(array_agg({expr} ORDER BY o.created_at DESC, o.id DESC)"
        f" FILTER (WHERE {val} IS NOT NULL))[1] AS {dst}_src"
    )


_SCALAR_SELECT = ",\n        ".join(
    [_latest_non_empty(s, d) for s, d, _ in _SCALARS]
    + [_latest_src(s, d, k) for s, d, k in _SCALARS]
)

_FILLED_COUNT = " + ".join(
    f"(CASE WHEN NULLIF(btrim(coalesce(oa.{s}, '')), '') IS NOT NULL THEN 1 ELSE 0 END)"
    for s, _, _ in _SCALARS
)

_AI_COUNT = " + ".join(
    f"(CASE WHEN NULLIF(btrim(coalesce(oa.{s}, '')), '') IS NOT NULL"
    f" AND coalesce(oa.ai_filled_fields, '{{}}'::jsonb) ? '{k}' THEN 1 ELSE 0 END)"
    for s, _, k in _SCALARS
)

FORWARD_SQL = f"""
CREATE MATERIALIZED VIEW IF NOT EXISTS dwh.dim_customer_profile AS
WITH src AS (
    SELECT o.customer_id, o.id AS order_id, o.created_at, oa.*
    FROM orders o
    JOIN order_assessments oa ON oa.order_id = o.id
    WHERE o.deleted_at IS NULL
      AND o.customer_id IS NOT NULL
      AND {sql_exclude_mirror_orders("o")}
),
scalars AS (
    SELECT
        o.customer_id,
        {_SCALAR_SELECT},
        max(GREATEST({_FILLED_COUNT}, 0)) AS filled_field_count,
        max(GREATEST({_AI_COUNT}, 0)) AS ai_field_count,
        bool_or(oa.used_massage_before) AS used_massage_before
    FROM orders o
    JOIN order_assessments oa ON oa.order_id = o.id
    WHERE o.deleted_at IS NULL
      AND o.customer_id IS NOT NULL
      AND {sql_exclude_mirror_orders("o")}
    GROUP BY o.customer_id
),
habits AS (
    SELECT s.customer_id, array_agg(DISTINCT h) AS lifestyle_habit_codes
    FROM src s, LATERAL unnest(s.lifestyle_habit_codes) AS h
    GROUP BY s.customer_id
),
relax AS (
    SELECT s.customer_id, array_agg(DISTINCT r) AS relax_time_codes
    FROM src s, LATERAL unnest(s.relax_time_codes) AS r
    GROUP BY s.customer_id
),
barriers AS (
    SELECT s.customer_id, array_agg(DISTINCT b) AS purchase_barrier_codes
    FROM src s, LATERAL unnest(s.purchase_barrier_codes) AS b
    GROUP BY s.customer_id
),
sources AS (
    SELECT s.customer_id, array_agg(DISTINCT d) AS delax_source_codes
    FROM src s, LATERAL unnest(s.delax_source_codes) AS d
    GROUP BY s.customer_id
),
needs AS (
    SELECT s.customer_id,
           array_agg(DISTINCT nt.code) AS need_codes,
           array_remove(array_agg(DISTINCT ng.code), NULL) AS need_group_codes
    FROM src s
    JOIN order_needs onn ON onn.order_assessment_id = s.id
    JOIN need_types nt ON nt.id = onn.need_type_id
    LEFT JOIN need_groups ng ON ng.id = nt.group_id
    GROUP BY s.customer_id
)
SELECT
    sc.customer_id,
    sc.age_text, sc.age_text_src,
    sc.occupation_code, sc.occupation_code_src,
    sc.pain_level_code, sc.pain_level_code_src,
    sc.pain_duration_code, sc.pain_duration_code_src,
    sc.comparing_brand_text, sc.comparing_brand_text_src,
    sc.used_massage_before,
    sc.filled_field_count::smallint AS filled_field_count,
    sc.ai_field_count::smallint AS ai_field_count,
    coalesce(h.lifestyle_habit_codes, '{{}}') AS lifestyle_habit_codes,
    coalesce(r.relax_time_codes, '{{}}') AS relax_time_codes,
    coalesce(b.purchase_barrier_codes, '{{}}') AS purchase_barrier_codes,
    coalesce(d.delax_source_codes, '{{}}') AS delax_source_codes,
    coalesce(n.need_codes, '{{}}') AS need_codes,
    coalesce(n.need_group_codes, '{{}}') AS need_group_codes
FROM scalars sc
LEFT JOIN habits h ON h.customer_id = sc.customer_id
LEFT JOIN relax r ON r.customer_id = sc.customer_id
LEFT JOIN barriers b ON b.customer_id = sc.customer_id
LEFT JOIN sources d ON d.customer_id = sc.customer_id
LEFT JOIN needs n ON n.customer_id = sc.customer_id
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS dim_customer_profile_pk
    ON dwh.dim_customer_profile (customer_id);
"""

REVERSE_SQL = "DROP MATERIALIZED VIEW IF EXISTS dwh.dim_customer_profile;"


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0001_dwh_schema"),
        # RunSQL chạm bảng của các app dưới đây → chúng phải migrate xong trước,
        # nếu không test dựng DB theo thứ tự khác sẽ nổ vì bảng chưa tồn tại.
        ("orders", "0133_drop_old_id_index"),
        ("customers", "0035_search_trgm_codify_drop_dead"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)
            ]
        )
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/analytics/tests/test_marts.py -q`
Expected: PASS (5 test)

Nếu đỏ ở `filled_field_count`: nhớ rằng `max(...)` trong `scalars` chạy trên biểu thức
per-row, cho ra số trường có giá trị của **phiếu giàu nhất**, không phải tổng gộp. Đó là
chủ đích — đọc lại assert trước khi sửa SQL.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/analytics/migrations/0002_dim_customer_profile.py backend/apps/analytics/tests/
git commit -m "feat(analytics): add dim_customer_profile matview"
```

---

## Task 4: Matview `dwh.fct_order_enriched`

**Files:**
- Create: `backend/apps/analytics/migrations/0003_fct_order_enriched.py`
- Modify: `backend/apps/analytics/tests/test_marts.py` (thêm class test mới vào file sẵn có)

**Interfaces:**
- Consumes: `gtc_where("o")` từ Task 2.
- Produces: matview `dwh.fct_order_enriched`, UNIQUE index trên `order_id`, index phụ trên `created_date` và `customer_id`.

- [ ] **Step 1: Write the failing test**

Thêm vào cuối `backend/apps/analytics/tests/test_marts.py`:

```python
def fetch_order_row(order_id: int) -> dict | None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT is_gtc, item_count, primary_sku, call_count, has_transcript"
            " FROM dwh.fct_order_enriched WHERE order_id = %s",
            [order_id],
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "is_gtc": row[0],
        "item_count": row[1],
        "primary_sku": row[2],
        "call_count": row[3],
        "has_transcript": row[4],
    }


@pytest.mark.django_db(transaction=True)
def test_gtc_flag_true_for_delivered_order():
    order = make_order(status=3)
    refresh("fct_order_enriched")
    assert fetch_order_row(order.id)["is_gtc"] is True


@pytest.mark.django_db(transaction=True)
def test_gtc_flag_false_for_cancelled_order():
    order = make_order(status=6)
    refresh("fct_order_enriched")
    assert fetch_order_row(order.id)["is_gtc"] is False


@pytest.mark.django_db(transaction=True)
def test_mirror_orders_are_excluded_entirely():
    order = make_order(order_source="foxia")
    refresh("fct_order_enriched")
    assert fetch_order_row(order.id) is None


@pytest.mark.django_db(transaction=True)
def test_null_order_source_is_kept():
    """Bẫy NULL: viết `order_source <> 'foxia'` sẽ làm mất đơn này."""
    order = make_order(order_source=None)
    refresh("fct_order_enriched")
    assert fetch_order_row(order.id) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/analytics/tests/test_marts.py -q -k order`
Expected: FAIL — `relation "dwh.fct_order_enriched" does not exist`

- [ ] **Step 3: Implement migration**

`backend/apps/analytics/migrations/0003_fct_order_enriched.py`:

```python
"""Matview 1 dòng/đơn — bảng rộng phục vụ tab Đơn hàng và phân tích theo thời gian.

`is_gtc` là CỜ, không phải bộ lọc: mart giữ mọi đơn nội bộ (kể cả huỷ) để tab
Đơn hàng xem được đơn huỷ, còn mọi phép tính doanh thu lọc `WHERE is_gtc`.
Đơn mirror (`order_source IN ('foxia','cnv')`) bị loại hẳn — chúng là bản sao.
"""

from django.db import migrations

from apps.analytics.sql.gtc import gtc_where
from apps.common.order_scope import sql_exclude_mirror_orders

FORWARD_SQL = f"""
CREATE MATERIALIZED VIEW IF NOT EXISTS dwh.fct_order_enriched AS
WITH items AS (
    SELECT oi.order_id,
           count(*)::int AS item_count,
           array_remove(array_agg(oi.sku), NULL) AS product_skus,
           array_remove(array_agg(oi.product_name), NULL) AS product_names,
           (array_agg(oi.sku ORDER BY oi.line_total DESC NULLS LAST))[1] AS primary_sku
    FROM order_items oi
    GROUP BY oi.order_id
),
calls AS (
    SELECT r.order_id,
           count(*)::int AS call_count,
           coalesce(sum(r.acd_time), 0)::int AS call_total_seconds,
           bool_or(NULLIF(btrim(coalesce(r.transcript, '')), '') IS NOT NULL)
               AS has_transcript,
           bool_or(r.extract_payload IS NOT NULL
                   AND r.extract_payload <> '{{}}'::jsonb) AS ai_extracted,
           max(s.total_score) AS quality_total_score
    FROM callcenter_call_records r
    LEFT JOIN callcenter_call_quality_scores s ON s.call_record_id = r.id
    WHERE r.order_id IS NOT NULL
    GROUP BY r.order_id
),
needs AS (
    SELECT oa.order_id,
           array_agg(DISTINCT nt.code) AS need_codes,
           array_remove(array_agg(DISTINCT ng.code), NULL) AS need_group_codes
    FROM order_assessments oa
    JOIN order_needs onn ON onn.order_assessment_id = oa.id
    JOIN need_types nt ON nt.id = onn.need_type_id
    LEFT JOIN need_groups ng ON ng.id = nt.group_id
    GROUP BY oa.order_id
)
SELECT
    o.id AS order_id,
    o.order_code,
    o.created_at,
    (o.created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS created_date,
    o.status,
    o.invoice_status,
    ({gtc_where("o")}) AS is_gtc,
    o.order_type,
    o.order_source,
    o.shipping_type,
    o.payment_method,
    o.total_after_discount,
    o.discount_amount,
    o.shipping_fee,
    o.quantity,
    o.customer_id,
    c.code AS customer_code,
    c.gender,
    c.is_new_customer,
    css.segment_code,
    cat.name AS segment_name,
    o.channel_id,
    ch.name AS channel_name,
    o.channel_owner,
    o.staff_id,
    nullif(btrim(concat_ws(' ', u.last_name, u.first_name)), '') AS staff_name,
    o.fb_ad_id,
    o.province,
    o.district,
    coalesce(i.item_count, 0) AS item_count,
    coalesce(i.product_skus, '{{}}') AS product_skus,
    coalesce(i.product_names, '{{}}') AS product_names,
    i.primary_sku,
    oa.sales_status_code,
    oa.not_closed_reason_code,
    oa.delivery_business_status_code,
    oa.bomb_reason_group_code,
    oa.customer_classification_code,
    NULLIF(btrim(coalesce(oa.age_text, '')), '') AS age_text,
    CASE
        WHEN NULLIF(btrim(coalesce(oa.age_text, '')), '') IS NULL THEN NULL
        WHEN coalesce(oa.ai_filled_fields, '{{}}'::jsonb) ? 'age_text' THEN 'ai'
        ELSE 'declared'
    END AS age_text_src,
    NULLIF(btrim(coalesce(oa.occupation_code, '')), '') AS occupation_code,
    NULLIF(btrim(coalesce(oa.pain_level_code, '')), '') AS pain_level_code,
    coalesce(n.need_codes, '{{}}') AS need_codes,
    coalesce(n.need_group_codes, '{{}}') AS need_group_codes,
    coalesce(cl.call_count, 0) AS call_count,
    coalesce(cl.call_total_seconds, 0) AS call_total_seconds,
    coalesce(cl.has_transcript, false) AS has_transcript,
    coalesce(cl.ai_extracted, false) AS ai_extracted,
    cl.quality_total_score
FROM orders o
LEFT JOIN customers c ON c.id = o.customer_id
LEFT JOIN customer_segment_snapshot css ON css.customer_id = o.customer_id
LEFT JOIN customer_segment_categories cat ON cat.code = css.segment_code
LEFT JOIN channels ch ON ch.id = o.channel_id
LEFT JOIN users u ON u.id = o.staff_id
LEFT JOIN order_assessments oa ON oa.order_id = o.id
LEFT JOIN items i ON i.order_id = o.id
LEFT JOIN calls cl ON cl.order_id = o.id
LEFT JOIN needs n ON n.order_id = o.id
WHERE o.deleted_at IS NULL
  AND {sql_exclude_mirror_orders("o")}
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS fct_order_enriched_pk
    ON dwh.fct_order_enriched (order_id);
CREATE INDEX IF NOT EXISTS fct_order_enriched_created_date_idx
    ON dwh.fct_order_enriched (created_date);
CREATE INDEX IF NOT EXISTS fct_order_enriched_customer_idx
    ON dwh.fct_order_enriched (customer_id);
CREATE INDEX IF NOT EXISTS fct_order_enriched_gtc_date_idx
    ON dwh.fct_order_enriched (created_date) WHERE is_gtc;
"""

REVERSE_SQL = "DROP MATERIALIZED VIEW IF EXISTS dwh.fct_order_enriched;"


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0002_dim_customer_profile"),
        ("callcenter", "0003_callqualityreview_callqualityscore_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)
            ]
        )
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/analytics/tests/test_marts.py -q`
Expected: PASS (9 test)

- [ ] **Step 5: Commit**

```bash
git add backend/apps/analytics/migrations/0003_fct_order_enriched.py backend/apps/analytics/tests/test_marts.py
git commit -m "feat(analytics): add fct_order_enriched matview"
```

---

## Task 5: Matview `dwh.customer_360`

**Files:**
- Create: `backend/apps/analytics/migrations/0004_customer_360.py`
- Modify: `backend/apps/analytics/tests/test_marts.py`

**Interfaces:**
- Consumes: `dwh.fct_order_enriched` (Task 4), `dwh.dim_customer_profile` (Task 3).
- Produces: matview `dwh.customer_360`, UNIQUE index trên `customer_id`.

> **Quan trọng**: matview này đọc **hai matview kia**, nên thứ tự refresh là bắt buộc
> (Task 6). Postgres không tự suy ra phụ thuộc giữa matview.

- [ ] **Step 1: Write the failing test**

Thêm vào `backend/apps/analytics/tests/test_marts.py`:

```python
def fetch_360(customer_id: int) -> dict | None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT orders_gtc, orders_all, total_spent, avg_order_value,"
            " days_since_last_purchase"
            " FROM dwh.customer_360 WHERE customer_id = %s",
            [customer_id],
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "orders_gtc": row[0],
        "orders_all": row[1],
        "total_spent": row[2],
        "avg_order_value": row[3],
        "days_since_last_purchase": row[4],
    }


def refresh_all() -> None:
    for name in ("dim_customer_profile", "fct_order_enriched", "customer_360"):
        refresh(name)


@pytest.mark.django_db(transaction=True)
def test_cancelled_orders_counted_but_not_spent():
    customer = make_customer()
    make_order(customer=customer, status=3, total_after_discount=1_000_000)
    make_order(customer=customer, status=6, total_after_discount=9_000_000)

    refresh_all()

    row = fetch_360(customer.id)
    assert row["orders_gtc"] == 1
    assert row["orders_all"] == 2
    assert int(row["total_spent"]) == 1_000_000


@pytest.mark.django_db(transaction=True)
def test_avg_order_value_uses_gtc_only():
    customer = make_customer()
    make_order(customer=customer, status=3, total_after_discount=1_000_000)
    make_order(customer=customer, status=3, total_after_discount=3_000_000)
    make_order(customer=customer, status=6, total_after_discount=9_000_000)

    refresh_all()

    assert int(fetch_360(customer.id)["avg_order_value"]) == 2_000_000


@pytest.mark.django_db(transaction=True)
def test_customer_without_gtc_order_still_present():
    """Khách chỉ có đơn huỷ vẫn phải có mặt — nếu không sẽ mất khỏi tệp remarketing."""
    customer = make_customer()
    make_order(customer=customer, status=6, total_after_discount=1_000_000)

    refresh_all()

    row = fetch_360(customer.id)
    assert row is not None
    assert row["orders_gtc"] == 0
    assert int(row["total_spent"]) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/analytics/tests/test_marts.py -q -k 360 or cancelled`
Expected: FAIL — `relation "dwh.customer_360" does not exist`

- [ ] **Step 3: Implement migration**

`backend/apps/analytics/migrations/0004_customer_360.py`:

```python
"""Matview 1 dòng/khách — nguồn cho màn Hồ sơ 360 và tab Khách hàng.

Đọc từ hai matview kia nên THỨ TỰ REFRESH là bắt buộc:
dim_customer_profile → fct_order_enriched → customer_360.
Postgres không tự suy ra phụ thuộc giữa các matview.

Khách chỉ có đơn huỷ VẪN có mặt (orders_gtc = 0) — họ là tệp remarketing.
"""

from django.db import migrations

FORWARD_SQL = """
CREATE MATERIALIZED VIEW IF NOT EXISTS dwh.customer_360 AS
WITH agg AS (
    SELECT
        f.customer_id,
        count(*) FILTER (WHERE f.is_gtc)::int AS orders_gtc,
        count(*)::int AS orders_all,
        count(*) FILTER (WHERE f.status = 6)::int AS orders_cancelled,
        coalesce(sum(f.total_after_discount) FILTER (WHERE f.is_gtc), 0)
            AS total_spent,
        avg(f.total_after_discount) FILTER (WHERE f.is_gtc) AS avg_order_value,
        min(f.created_at) FILTER (WHERE f.is_gtc) AS first_purchase_at,
        max(f.created_at) FILTER (WHERE f.is_gtc) AS last_purchase_at,
        array_remove(array_agg(DISTINCT f.channel_name), NULL) AS channels,
        array_remove(array_agg(DISTINCT f.province), NULL) AS provinces,
        array_remove(array_agg(DISTINCT f.primary_sku), NULL) AS top_skus,
        count(DISTINCT f.primary_sku)::int AS distinct_products,
        coalesce(sum(f.call_count), 0)::int AS call_count,
        avg(f.quality_total_score) AS avg_quality_score
    FROM dwh.fct_order_enriched f
    WHERE f.customer_id IS NOT NULL
    GROUP BY f.customer_id
),
gaps AS (
    SELECT customer_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_days) AS repurchase_gap_median_days
    FROM (
        SELECT f.customer_id,
               EXTRACT(EPOCH FROM (
                   f.created_at - lag(f.created_at) OVER (
                       PARTITION BY f.customer_id ORDER BY f.created_at
                   )
               )) / 86400.0 AS gap_days
        FROM dwh.fct_order_enriched f
        WHERE f.is_gtc AND f.customer_id IS NOT NULL
    ) p
    WHERE gap_days IS NOT NULL
    GROUP BY customer_id
)
SELECT
    a.customer_id,
    c.code,
    c.gender,
    c.created_at,
    c.is_new_customer,
    css.segment_code,
    cat.name AS segment_name,
    cat.care_priority,
    css.has_problem,
    css.latest_sales_status_code,
    a.orders_gtc,
    a.orders_all,
    a.orders_cancelled,
    a.total_spent,
    a.avg_order_value,
    a.first_purchase_at,
    a.last_purchase_at,
    CASE
        WHEN a.last_purchase_at IS NULL THEN NULL
        ELSE (now()::date - a.last_purchase_at::date)
    END AS days_since_last_purchase,
    g.repurchase_gap_median_days,
    a.channels,
    a.provinces,
    a.top_skus,
    a.distinct_products,
    a.call_count,
    a.avg_quality_score,
    p.age_text, p.age_text_src,
    p.occupation_code, p.occupation_code_src,
    p.pain_level_code, p.pain_level_code_src,
    p.pain_duration_code,
    p.used_massage_before,
    p.lifestyle_habit_codes,
    p.relax_time_codes,
    p.purchase_barrier_codes,
    p.delax_source_codes,
    p.need_codes,
    p.need_group_codes,
    coalesce(p.filled_field_count, 0)::smallint AS filled_field_count,
    coalesce(p.ai_field_count, 0)::smallint AS ai_field_count
FROM agg a
LEFT JOIN customers c ON c.id = a.customer_id
LEFT JOIN customer_segment_snapshot css ON css.customer_id = a.customer_id
LEFT JOIN customer_segment_categories cat ON cat.code = css.segment_code
LEFT JOIN dwh.dim_customer_profile p ON p.customer_id = a.customer_id
LEFT JOIN gaps g ON g.customer_id = a.customer_id
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS customer_360_pk
    ON dwh.customer_360 (customer_id);
CREATE INDEX IF NOT EXISTS customer_360_segment_idx
    ON dwh.customer_360 (segment_code);
CREATE INDEX IF NOT EXISTS customer_360_last_purchase_idx
    ON dwh.customer_360 (last_purchase_at);
"""

REVERSE_SQL = "DROP MATERIALIZED VIEW IF EXISTS dwh.customer_360;"


class Migration(migrations.Migration):
    dependencies = [("analytics", "0003_fct_order_enriched")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)
            ]
        )
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/analytics/tests/test_marts.py -q`
Expected: PASS (12 test)

- [ ] **Step 5: Commit**

```bash
git add backend/apps/analytics/migrations/0004_customer_360.py backend/apps/analytics/tests/test_marts.py
git commit -m "feat(analytics): add customer_360 matview"
```

---

## Task 6: Service refresh + task Celery + lịch

**Files:**
- Create: `backend/apps/analytics/application/__init__.py`, `mart_refresh_service.py`
- Create: `backend/apps/analytics/infrastructure/__init__.py`, `tasks.py`
- Create: `backend/apps/analytics/tasks.py` (facade)
- Modify: `backend/config/settings_parts/celery.py` (routes + beat)
- Modify: `backend/config/settings_parts/base.py` (`DWH_REFRESH_TIMEOUT_SECONDS`)
- Modify: `docker-compose.yml`, `.env.example`
- Test: `backend/apps/analytics/tests/test_refresh_service.py`

**Interfaces:**
- Produces:
  - `MART_ORDER: tuple[str, ...] = ("dim_customer_profile", "fct_order_enriched", "customer_360")`
  - `is_populated(name: str) -> bool`
  - `refresh_mart(name: str) -> float` — trả số giây, raise nếu lỗi
  - `refresh_all_marts() -> dict[str, float]` — raise `MartRefreshFailed` nếu có bước hỏng
  - `MartRefreshFailed(RuntimeError)`
  - Task `apps.analytics.tasks.refresh_dwh_marts`

- [ ] **Step 1: Write the failing test**

`backend/apps/analytics/tests/test_refresh_service.py`:

```python
import pytest
from django.db import connection

from apps.analytics.application.mart_refresh_service import (
    MART_ORDER,
    is_populated,
    refresh_all_marts,
)


def test_mart_order_puts_dependencies_first():
    """customer_360 đọc 2 matview kia nên phải refresh sau cùng."""
    assert MART_ORDER.index("dim_customer_profile") < MART_ORDER.index("customer_360")
    assert MART_ORDER.index("fct_order_enriched") < MART_ORDER.index("customer_360")


@pytest.mark.django_db(transaction=True)
def test_first_refresh_works_on_unpopulated_matview():
    """Matview tạo WITH NO DATA: lần đầu KHÔNG được dùng CONCURRENTLY."""
    with connection.cursor() as cur:
        for name in MART_ORDER:
            cur.execute(f"REFRESH MATERIALIZED VIEW dwh.{name} WITH NO DATA")

    assert is_populated("customer_360") is False
    refresh_all_marts()
    assert is_populated("customer_360") is True


@pytest.mark.django_db(transaction=True)
def test_second_refresh_uses_concurrently_and_succeeds():
    refresh_all_marts()
    assert is_populated("customer_360") is True
    refresh_all_marts()
    assert is_populated("customer_360") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/analytics/tests/test_refresh_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.analytics.application'`

- [ ] **Step 3: Implement service**

`backend/apps/analytics/application/__init__.py` — file rỗng.

`backend/apps/analytics/application/mart_refresh_service.py`:

```python
"""Refresh các materialized view trong schema `dwh`.

Hai điều dễ sai và đều gây chết ngay lần chạy đầu:

1. Matview tạo `WITH NO DATA` CHƯA được nạp lần nào thì
   `REFRESH ... CONCURRENTLY` báo lỗi. Phải đọc `pg_class.relispopulated`
   để chọn nhánh.
2. `customer_360` đọc hai matview kia — Postgres KHÔNG tự suy ra phụ thuộc,
   thứ tự trong `MART_ORDER` là bắt buộc.
"""

import logging
import time

from django.conf import settings
from django.db import connection

from apps.common.db_guards import statement_timeout

logger = logging.getLogger(__name__)

MART_ORDER: tuple[str, ...] = (
    "dim_customer_profile",
    "fct_order_enriched",
    "customer_360",
)


class MartRefreshFailed(RuntimeError):
    """Có ít nhất một matview refresh hỏng."""


def is_populated(name: str) -> bool:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT c.relispopulated FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'dwh' AND c.relname = %s",
            [name],
        )
        row = cur.fetchone()
    if row is None:
        raise MartRefreshFailed(f"matview dwh.{name} không tồn tại")
    return bool(row[0])


def refresh_mart(name: str) -> float:
    """Refresh một matview, trả về số giây đã chạy."""
    concurrently = "CONCURRENTLY " if is_populated(name) else ""
    started = time.monotonic()
    timeout = getattr(settings, "DWH_REFRESH_TIMEOUT_SECONDS", 900)
    with statement_timeout(timeout), connection.cursor() as cur:
        cur.execute(f"REFRESH MATERIALIZED VIEW {concurrently}dwh.{name}")
    elapsed = time.monotonic() - started
    logger.info(
        "refreshed dwh.%s in %.1fs (concurrently=%s)",
        name, elapsed, bool(concurrently),
    )
    return elapsed


def refresh_all_marts() -> dict[str, float]:
    """Refresh đúng thứ tự. Chạy hết rồi mới raise, để một matview hỏng không
    che mất kết quả của các matview còn lại."""
    timings: dict[str, float] = {}
    failed: list[str] = []
    for name in MART_ORDER:
        try:
            timings[name] = refresh_mart(name)
        except Exception:
            logger.exception("refresh dwh.%s failed", name)
            failed.append(name)
    if failed:
        raise MartRefreshFailed(f"refresh hỏng: {', '.join(failed)}")
    return timings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/analytics/tests/test_refresh_service.py -q`
Expected: PASS (3 test)

- [ ] **Step 5: Implement task Celery**

`backend/apps/analytics/infrastructure/__init__.py` — file rỗng.

`backend/apps/analytics/infrastructure/tasks.py`:

```python
import logging

from celery import shared_task

from apps.analytics.application.mart_refresh_service import refresh_all_marts
from apps.common.db_guards import single_flight

logger = logging.getLogger(__name__)

_MART_LOCK_KEY = "lock:refresh_dwh_marts"
_MART_LOCK_TTL = 2400  # > time_limit=1800


@shared_task(
    name="apps.analytics.tasks.refresh_dwh_marts",
    soft_time_limit=1500,
    time_limit=1800,
)
def refresh_dwh_marts() -> dict:
    """Refresh toàn bộ mart `dwh`.

    Lỗi PHẢI thoát ra ngoài: nuốt lỗi khiến Celery báo `succeeded` trong khi
    bước con đã chết, celery-exporter không đếm được và alert không nổ
    (sự cố 2026-07-22).
    """
    with single_flight(_MART_LOCK_KEY, _MART_LOCK_TTL) as acquired:
        if not acquired:
            logger.warning("refresh_dwh_marts already running — skip this cycle")
            return {"skipped": True, "timings": {}}
        timings = refresh_all_marts()
    return {"skipped": False, "timings": timings}
```

`backend/apps/analytics/tasks.py` (**facade — bắt buộc**):

```python
"""Facade cho Celery autodiscover.

`app.autodiscover_tasks()` chỉ import `apps.<app>.tasks` ở gốc app, không quét
module lồng. Thiếu file này thì task không đăng ký và job kẹt PENDING.
"""

from .infrastructure.tasks import *  # noqa: F401,F403
```

- [ ] **Step 6: Đăng ký route và lịch**

Trong `backend/config/settings_parts/celery.py`, thêm vào `CELERY_TASK_ROUTES`:

```python
"apps.analytics.tasks.refresh_dwh_marts": {"queue": CELERY_SLOW_QUEUE},
```

Thêm vào `CELERY_BEAT_SCHEDULE`:

```python
# 04:25 — giờ chẵn, phút lẻ. Sau `customers-rebuild-segment-snapshot-full` (03:20)
# 65 phút (lần đo 22/07 nó mất 426s), và lệch khỏi nhịp giờ lẻ phút 00/10 của
# cụm rebuild-segment-snapshot / warm-report-cache. Nguyên tắc lệch pha ở đầu file.
"analytics-refresh-dwh-marts": {
    "task": "apps.analytics.tasks.refresh_dwh_marts",
    "schedule": crontab(hour=4, minute=25),
},
```

- [ ] **Step 7: Thêm env mới**

Trong `backend/config/settings_parts/base.py`, cạnh `REPORT_QUERY_TIMEOUT_SECONDS`:

```python
DWH_REFRESH_TIMEOUT_SECONDS = int(os.getenv("DWH_REFRESH_TIMEOUT_SECONDS") or "900")
```

Thêm `DWH_REFRESH_TIMEOUT_SECONDS=900` vào `.env.example` và vào khối `environment`
của service backend/worker trong `docker-compose.yml`.

- [ ] **Step 8: Run tests**

Run: `pytest apps/analytics apps/common/tests/test_celery_routes.py -q`
Expected: PASS. Nếu `test_every_beat_task_has_explicit_route_or_is_known_default` đỏ thì
thiếu entry route ở Step 6.

- [ ] **Step 9: Commit**

```bash
git add backend/apps/analytics backend/config/settings_parts/celery.py backend/config/settings_parts/base.py .env.example docker-compose.yml
git commit -m "feat(analytics): add dwh mart refresh task and schedule"
```

---

## Task 7: Lệnh đối chiếu số — tiêu chí hoàn thành S1

**Files:**
- Create: `backend/apps/analytics/management/__init__.py`, `commands/__init__.py`, `commands/dwh_reconcile.py`
- Test: `backend/apps/analytics/tests/test_reconcile.py`

**Interfaces:**
- Produces: lệnh `python manage.py dwh_reconcile` in bảng so sánh mart vs nguồn OLTP và thoát mã 1 nếu lệch.

> Đây là **tiêu chí hoàn thành của S1** (spec §7): số của mart phải khớp nguồn. Nếu lệch,
> mọi việc phía sau đều xây trên số sai.

- [ ] **Step 1: Write the failing test**

`backend/apps/analytics/tests/test_reconcile.py`:

```python
import pytest
from django.core.management import call_command

from apps.analytics.application.mart_refresh_service import refresh_all_marts
from apps.analytics.tests.helpers import make_customer, make_order


@pytest.mark.django_db(transaction=True)
def test_reconcile_passes_when_mart_matches_source():
    customer = make_customer()
    make_order(customer=customer, status=3, total_after_discount=1_000_000)
    make_order(customer=customer, status=6, total_after_discount=5_000_000)
    refresh_all_marts()

    call_command("dwh_reconcile")  # không raise = khớp


@pytest.mark.django_db(transaction=True)
def test_reconcile_fails_when_mart_is_stale():
    """Thêm đơn mà chưa refresh → mart lệch → lệnh phải báo lỗi."""
    from django.core.management.base import CommandError

    customer = make_customer()
    make_order(customer=customer, status=3, total_after_discount=1_000_000)
    refresh_all_marts()

    make_order(customer=customer, status=3, total_after_discount=2_000_000)

    with pytest.raises(CommandError):
        call_command("dwh_reconcile")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/analytics/tests/test_reconcile.py -q`
Expected: FAIL — `Unknown command: 'dwh_reconcile'`

- [ ] **Step 3: Implement**

`backend/apps/analytics/management/__init__.py` và
`backend/apps/analytics/management/commands/__init__.py` — file rỗng.

`backend/apps/analytics/management/commands/dwh_reconcile.py`:

```python
"""Đối chiếu số của mart `dwh` với nguồn OLTP.

Tiêu chí hoàn thành Sprint 1. Chạy sau mỗi lần refresh trên prod.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.analytics.sql.gtc import gtc_where
from apps.common.order_scope import sql_exclude_mirror_orders

_CHECKS = {
    "so_don_gtc": (
        f"SELECT count(*) FROM orders o WHERE {gtc_where('o')}",
        "SELECT count(*) FROM dwh.fct_order_enriched WHERE is_gtc",
    ),
    "doanh_thu_gtc": (
        f"SELECT coalesce(sum(o.total_after_discount), 0) FROM orders o"
        f" WHERE {gtc_where('o')}",
        "SELECT coalesce(sum(total_after_discount), 0)"
        " FROM dwh.fct_order_enriched WHERE is_gtc",
    ),
    "so_khach_co_don": (
        f"SELECT count(DISTINCT o.customer_id) FROM orders o"
        f" WHERE o.deleted_at IS NULL AND o.customer_id IS NOT NULL"
        f" AND {sql_exclude_mirror_orders('o')}",
        "SELECT count(*) FROM dwh.customer_360",
    ),
}


class Command(BaseCommand):
    help = "Đối chiếu số của mart dwh với nguồn OLTP"

    def handle(self, *args, **options):
        mismatches = []
        with connection.cursor() as cur:
            for label, (src_sql, mart_sql) in _CHECKS.items():
                cur.execute(src_sql)
                src = cur.fetchone()[0]
                cur.execute(mart_sql)
                mart = cur.fetchone()[0]
                ok = src == mart
                self.stdout.write(
                    f"{label:20} nguồn={src:>15}  mart={mart:>15}  "
                    f"{'OK' if ok else 'LỆCH'}"
                )
                if not ok:
                    mismatches.append(f"{label}: nguồn={src} mart={mart}")
        if mismatches:
            raise CommandError("Mart lệch nguồn — " + "; ".join(mismatches))
        self.stdout.write(self.style.SUCCESS("Mart khớp nguồn."))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/analytics/tests/test_reconcile.py -q`
Expected: PASS (2 test)

- [ ] **Step 5: Commit**

```bash
git add backend/apps/analytics/management backend/apps/analytics/tests/test_reconcile.py
git commit -m "feat(analytics): add dwh reconcile command"
```

---

## Task 8: Endpoint `/api/analytics/coverage` (S2a)

**Files:**
- Create: `backend/apps/analytics/infrastructure/mart_queries.py`
- Create: `backend/apps/analytics/transport/__init__.py`, `views.py`, `serializers.py`
- Modify: `backend/apps/analytics/urls.py`
- Modify: `backend/.importlinter` — thêm **đúng một** dòng vào KHỐI A1b:
  `apps.analytics.tests.* -> apps.orders.tests.helpers`, **cùng commit** với file test dưới
  đây (test import `make_scoped_user` từ đó). Thêm sớm hơn sẽ làm `lint-imports` thoát mã 1
  vì `unmatched_ignore_imports_alerting = error`.
- Test: `backend/apps/analytics/tests/test_coverage_api.py`

**Interfaces:**
- Consumes: `dwh.customer_360` (Task 5).
- Produces: `GET /api/analytics/coverage` trả
  `{"total_customers": int, "fields": [{"field": str, "declared": int, "ai": int, "total": int, "pct": float}]}`

- [ ] **Step 1: Write the failing test**

`backend/apps/analytics/tests/test_coverage_api.py`:

```python
import pytest
from rest_framework.test import APIClient

from apps.analytics.application.mart_refresh_service import refresh_all_marts
from apps.analytics.tests.helpers import make_assessment, make_customer, make_order
from apps.orders.tests.helpers import make_scoped_user


@pytest.fixture
def client(db):
    user = make_scoped_user("analytics_user", "dashboard")
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.mark.django_db(transaction=True)
def test_coverage_splits_declared_and_ai(client):
    c1 = make_customer()
    make_assessment(make_order(customer=c1), age_text="65_plus")
    c2 = make_customer()
    make_assessment(
        make_order(customer=c2), age_text="45_54", ai_filled_fields={"age_text": True}
    )
    make_customer_with_no_data = make_customer()
    make_order(customer=make_customer_with_no_data)
    refresh_all_marts()

    resp = client.get("/api/analytics/coverage")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_customers"] == 3
    age = next(f for f in body["fields"] if f["field"] == "age_text")
    assert age["declared"] == 1
    assert age["ai"] == 1
    assert age["total"] == 2
    assert age["pct"] == pytest.approx(66.7, abs=0.1)


@pytest.mark.django_db(transaction=True)
def test_coverage_requires_authentication():
    resp = APIClient().get("/api/analytics/coverage")
    assert resp.status_code in (401, 403)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/analytics/tests/test_coverage_api.py -q`
Expected: FAIL — 404 (url chưa có)

- [ ] **Step 3: Implement query layer**

`backend/apps/analytics/infrastructure/mart_queries.py`:

```python
"""Đọc mart bằng SQL thuần — matview không có model Django."""

from django.conf import settings
from django.db import connection
from django.db.utils import OperationalError

from apps.common.db_guards import statement_timeout

# Chỉ phơi các trường có ý nghĩa nghiệp vụ nhất. `dim_customer_profile` sinh
# cột `_src` cho cả 5 trường vô hướng — mở rộng tuple này khi có nhu cầu.
_COVERAGE_FIELDS = ("age_text", "occupation_code", "pain_level_code")


class MartNotReady(RuntimeError):
    """Matview chưa được refresh lần nào."""


def coverage() -> dict:
    """Độ phủ từng trường chân dung, tách nguồn declared / ai."""
    selects = ", ".join(
        f"count(*) FILTER (WHERE {f} IS NOT NULL) AS {f}_total,"
        f" count(*) FILTER (WHERE {f}_src = 'declared') AS {f}_declared,"
        f" count(*) FILTER (WHERE {f}_src = 'ai') AS {f}_ai"
        for f in _COVERAGE_FIELDS
    )
    sql = f"SELECT count(*) AS total, {selects} FROM dwh.customer_360"
    timeout = getattr(settings, "REPORT_QUERY_TIMEOUT_SECONDS", 120)
    try:
        with statement_timeout(timeout), connection.cursor() as cur:
            cur.execute(sql)
            cols = [c[0] for c in cur.description]
            row = dict(zip(cols, cur.fetchone()))
    except OperationalError as exc:
        raise MartNotReady(
            "Mart dwh.customer_360 chưa sẵn sàng — chạy refresh_dwh_marts trước."
        ) from exc

    total = row["total"] or 0
    fields = []
    for f in _COVERAGE_FIELDS:
        n = row[f"{f}_total"]
        fields.append(
            {
                "field": f,
                "declared": row[f"{f}_declared"],
                "ai": row[f"{f}_ai"],
                "total": n,
                "pct": round(100.0 * n / total, 1) if total else 0.0,
            }
        )
    return {"total_customers": total, "fields": fields}
```

- [ ] **Step 4: Implement transport**

`backend/apps/analytics/transport/__init__.py` — file rỗng.

`backend/apps/analytics/transport/serializers.py`:

```python
from rest_framework import serializers


class CoverageFieldSerializer(serializers.Serializer):
    field = serializers.CharField()
    declared = serializers.IntegerField()
    ai = serializers.IntegerField()
    total = serializers.IntegerField()
    pct = serializers.FloatField()


class CoverageSerializer(serializers.Serializer):
    total_customers = serializers.IntegerField()
    fields = CoverageFieldSerializer(many=True)
```

`backend/apps/analytics/transport/views.py`:

```python
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.analytics.infrastructure.mart_queries import MartNotReady, coverage
from apps.analytics.transport.serializers import CoverageSerializer


@extend_schema(
    responses=CoverageSerializer,
    description="Độ phủ từng trường chân dung khách hàng, tách nguồn declared / ai.",
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def coverage_view(request):
    try:
        data = coverage()
    except MartNotReady as exc:
        return Response({"detail": str(exc)}, status=503)
    return Response(CoverageSerializer(data).data)
```

`backend/apps/analytics/urls.py`:

```python
from django.urls import path

from apps.analytics.transport import views

urlpatterns = [
    path("coverage", views.coverage_view, name="analytics-coverage"),
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest apps/analytics/tests/test_coverage_api.py -q`
Expected: PASS (2 test)

- [ ] **Step 6: Cập nhật schema.yaml và type FE**

```bash
cd backend && python manage.py spectacular --file schema.yaml
cd ../frontend && npm run gen:api
```

`npm run gen:api` đã tự chạy prettier. Kiểm tra `git diff backend/schema.yaml` chỉ chứa
phần `/api/analytics/coverage` — nếu lôi theo nhiễu của feature khác thì **revert phần
nhiễu**, đó là nợ của MR chore riêng.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/analytics backend/schema.yaml frontend/src/api/schema.d.ts
git commit -m "feat(analytics): add coverage endpoint"
```

---

## Task 9: Chạy thật, đo, cập nhật spec

**Files:**
- Modify: `docs/dwh-customer-analytics.md` (§2.3 ước lượng dung lượng, §7 mốc đổi ý, §8 tiến độ)

- [ ] **Step 1: Chạy migrate và refresh trên môi trường có dữ liệu thật**

```bash
python manage.py migrate analytics
python manage.py shell -c "from apps.analytics.application.mart_refresh_service import refresh_all_marts; print(refresh_all_marts())"
```

Ghi lại số giây từng matview.

- [ ] **Step 2: Đo dung lượng thật**

```sql
SELECT c.relname, pg_size_pretty(pg_total_relation_size(c.oid)) AS size
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'dwh' ORDER BY pg_total_relation_size(c.oid) DESC;
```

- [ ] **Step 3: Đối chiếu số**

```bash
python manage.py dwh_reconcile
```

Expected: `Mart khớp nguồn.` Nếu LỆCH — **dừng lại**, đây là tiêu chí hoàn thành S1, không
được đi tiếp với số sai.

- [ ] **Step 4: Cập nhật spec**

Trong `docs/dwh-customer-analytics.md`:
- §2.3: thay ước lượng "250–350 MB" bằng số đo thật
- §7 "Mốc đổi ý": ghi thời gian refresh đo được, đối chiếu ngưỡng 10 phút
- §8 Tiến độ: đổi S1 và S2a thành ✅ kèm ngày

- [ ] **Step 5: Commit**

```bash
git add docs/dwh-customer-analytics.md
git commit -m "docs(analytics): record measured refresh time and mart size"
```

---

## Self-Review

**1. Spec coverage:**

| Spec | Task |
|---|---|
| §2.0 GTC 5 mệnh đề | Task 2 (`gtc.py`) + test |
| §2.1 cây thư mục | Task 1, 6, 8 |
| §2.2 `dim_customer_profile` | Task 3 |
| §2.3 `fct_order_enriched` | Task 4 — **trừ cột `region`, hoãn sang S2b** (đã ghi lý do ở File Structure) |
| §2.4 `customer_360` | Task 5 — **trừ khối chân dung suy luận**, đúng phạm vi (S2b) |
| §2.5 migration | Task 2–5 |
| §2.6 import-linter | Task 1 Step 6 |
| §3 refresh + lịch + route | Task 6 |
| §4 API | Task 8 (chỉ `/coverage`; `orders`/`customers`/`profile` thuộc S3–S4) |
| §4.1 xuất Excel | S3 — ngoài khối này |
| §5 frontend | S3–S4 — ngoài khối này |
| §6 R1/R3 đo lường | Task 9 |
| §6 R4 matview chưa refresh | Task 6 (`is_populated`) + Task 8 (`MartNotReady` → 503) |
| §6 R7 đối chiếu số | Task 7 + Task 9 Step 3 |
| §7 tiêu chí S1 | Task 9 |

**Khoảng trống đã biết, có chủ đích**: `contraindications` và `competitors_mentioned`
trong `customer_360` (spec §2.4) chưa làm ở khối này — chúng nằm trong
`callcenter_call_quality_scores.contraindications` và `extract_payload`, sẽ thêm ở S2b
cùng lúc với khối chân dung suy luận, để tránh sửa cùng một matview hai lần.

**2. Placeholder scan:** không còn "TBD"/"TODO"/"tương tự Task N". Mọi bước code đều có
code thật.

**3. Type consistency:** `MART_ORDER`, `is_populated`, `refresh_mart`, `refresh_all_marts`,
`MartRefreshFailed` dùng nhất quán giữa Task 6, 7, 8, 9. Tên matview
(`dim_customer_profile`, `fct_order_enriched`, `customer_360`) nhất quán giữa migration,
service và test. Tên cột `*_src` sinh bởi Task 3 được Task 5 và Task 8 dùng lại đúng dạng
`<field>_src`.
