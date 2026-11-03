# Kho phân tích `dwh` — Khối 2A (R1 + S2b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Sửa `dwh_reconcile` để hết báo động giả, và thêm chiều "thời kỳ ngành hàng" + chân dung suy luận từ hành vi vào `customer_360`.

**Architecture:** Thêm bảng `dwh.mart_refresh_log` (bảng THẬT, không phải matview) để lưu mốc refresh; `dwh_reconcile` đối chiếu *as-of* mốc đó. Thay `customer_360` bằng phiên bản có thêm 7 cột suy luận.

**Tech Stack:** Django 5 + PostgreSQL 16. Không thêm dependency.

**Spec:** `docs/dwh-customer-analytics.md` (§10 là kết quả nghiệm thu prod và mô tả khiếm khuyết cần sửa)

## Global Constraints

- Python ≥ 3.12, ruff. Tất cả lệnh chạy trong container `dwh2-suite` (KHÔNG dùng `dwh-suite` hay `kvstar-suite` — worktree khác).
  - Test: `docker exec dwh2-suite sh -c 'pytest apps/analytics -q > /tmp/t 2>&1; echo "EXIT=$?"; tail -25 /tmp/t'`
  - Migrate: `docker exec dwh2-suite python manage.py migrate analytics`
  - Ruff: `docker exec dwh2-suite sh -c 'PATH=/app/.local/bin:$PATH ruff check apps/analytics > /tmp/r 2>&1; echo "EXIT=$?"; cat /tmp/r'`
  - Import-linter: `docker exec dwh2-suite sh -c 'PATH=/app/.local/bin:$PATH lint-imports --config .importlinter > /tmp/l 2>&1; echo "EXIT=$?"; tail -6 /tmp/l'`
- **Chạy pytest MỘT lượt, foreground, không bao giờ hai lượt song song** — race `DuplicateDatabase`. Suite ~2-3 phút; đó là bình thường.
- **Không đọc `$?` sau khi pipe qua `tail`** — nó trả exit của `tail`.
- `ruff`/`lint-imports` chỉ có ở `/app/.local/bin`, `python -m ruff` KHÔNG chạy được.
- **Materialized view KHÔNG xuất hiện trong `information_schema.columns`** — tra qua `pg_attribute`/`pg_class`/`pg_namespace`.
- **Sửa file migration KHÔNG đổi DB đã áp.** Dùng `migrate analytics <số trước>` rồi `migrate analytics`.
- **`Order.created_at` là `auto_now_add=True`** — truyền `created_at=` vào factory IM LẶNG không có tác dụng. Test cần mốc thời gian cụ thể phải dùng `.update()` sau khi tạo.
- Mốc chia thời kỳ ngành hàng: **`2025-01-01`**, suy từ dữ liệu prod (giá trị đơn TB nhảy từ ~297k năm 2024 lên ~1.455k năm 2025). Khai thành hằng số dùng chung, không rải literal.
- Múi giờ nghiệp vụ: dùng `BUSINESS_TZ` sẵn có ở `apps/analytics/sql/tz.py`. Không viết literal mới.
- Bộ đếm `filled_field_count`/`ai_field_count` tính trên hàng ĐÃ HỢP NHẤT; `ai_field_count` là tập con. Không đụng vào.

---

## File Structure

| File | Trách nhiệm |
|---|---|
| `backend/apps/analytics/migrations/0005_mart_refresh_log.py` | Bảng `dwh.mart_refresh_log` |
| `backend/apps/analytics/migrations/0006_customer_360_behavior.py` | Thay `customer_360` — thêm `era` + 6 cột suy luận |
| `backend/apps/analytics/sql/eras.py` | Hằng số mốc thời kỳ + ngưỡng phân nhóm |
| `backend/apps/analytics/application/mart_refresh_service.py` | Ghi `mart_refresh_log` sau mỗi lần refresh |
| `backend/apps/analytics/management/commands/dwh_reconcile.py` | Đối chiếu as-of mốc refresh |
| `backend/apps/analytics/tests/test_refresh_service.py` | Test ghi log |
| `backend/apps/analytics/tests/test_reconcile.py` | Test as-of |
| `backend/apps/analytics/tests/test_marts.py` | Test era + chân dung hành vi |

---

## Task 1: Bảng `dwh.mart_refresh_log` + service ghi mốc

**Files:**
- Create: `backend/apps/analytics/migrations/0005_mart_refresh_log.py`
- Modify: `backend/apps/analytics/application/mart_refresh_service.py`
- Modify: `backend/apps/analytics/tests/test_refresh_service.py`

**Interfaces:**
- Produces: bảng `dwh.mart_refresh_log(id, mart_name, started_at, finished_at, duration_seconds)`;
  hàm `last_refresh_boundary() -> datetime | None` trả `min(started_at)` của lượt refresh đầy đủ gần nhất.

> **Vì sao là BẢNG chứ không phải matview:** nó lưu sự kiện, không phải dẫn xuất. Và nó phải
> ghi được trong lúc matview đang refresh.

> **Vì sao lấy `started_at` chứ không phải `finished_at`:** matview chụp ảnh dữ liệu tại thời
> điểm BẮT ĐẦU. Một bản ghi tạo giữa lúc refresh chạy sẽ không lọt vào mart — đúng ca đã xảy
> ra trên prod (khách 758542, đơn tạo 7 giây sau khi refresh khởi động). Dùng `finished_at`
> sẽ vẫn báo lệch giả.

- [ ] **Step 1: Write the failing test**

Thêm vào `backend/apps/analytics/tests/test_refresh_service.py`:

```python
from apps.analytics.application.mart_refresh_service import (
    MART_ORDER,
    last_refresh_boundary,
    refresh_all_marts,
)


@pytest.mark.django_db(transaction=True)
def test_refresh_records_a_log_row_per_mart():
    refresh_all_marts()
    with connection.cursor() as cur:
        cur.execute(
            "SELECT mart_name, started_at, finished_at, duration_seconds"
            " FROM dwh.mart_refresh_log ORDER BY id DESC LIMIT %s",
            [len(MART_ORDER)],
        )
        rows = cur.fetchall()
    assert {r[0] for r in rows} == set(MART_ORDER)
    for _, started, finished, duration in rows:
        assert started is not None and finished is not None
        assert finished >= started
        assert duration >= 0


@pytest.mark.django_db(transaction=True)
def test_boundary_is_the_earliest_start_of_the_last_full_run():
    refresh_all_marts()
    boundary = last_refresh_boundary()
    with connection.cursor() as cur:
        cur.execute(
            "SELECT min(started_at) FROM dwh.mart_refresh_log"
            " WHERE mart_name = ANY(%s)",
            [list(MART_ORDER)],
        )
        expected = cur.fetchone()[0]
    assert boundary == expected


@pytest.mark.django_db(transaction=True)
def test_boundary_is_none_before_any_refresh():
    with connection.cursor() as cur:
        cur.execute("DELETE FROM dwh.mart_refresh_log")
    assert last_refresh_boundary() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec dwh2-suite sh -c 'pytest apps/analytics/tests/test_refresh_service.py -q > /tmp/t 2>&1; echo "EXIT=$?"; tail -20 /tmp/t'`
Expected: FAIL — `ImportError: cannot import name 'last_refresh_boundary'`

- [ ] **Step 3: Tạo migration**

`backend/apps/analytics/migrations/0005_mart_refresh_log.py`:

```python
"""Bảng ghi mốc refresh của từng mart.

Là BẢNG THẬT, không phải matview: nó lưu sự kiện chứ không phải dẫn xuất, và phải
ghi được ngay trong lúc matview đang refresh.

`started_at` là thứ `dwh_reconcile` dùng làm mốc as-of, KHÔNG phải `finished_at`:
matview chụp ảnh dữ liệu tại thời điểm BẮT ĐẦU, nên bản ghi sinh ra giữa lúc refresh
chạy sẽ không lọt vào mart.
"""

from django.db import migrations

FORWARD_SQL = """
CREATE TABLE IF NOT EXISTS dwh.mart_refresh_log (
    id               bigserial PRIMARY KEY,
    mart_name        text        NOT NULL,
    started_at       timestamptz NOT NULL,
    finished_at      timestamptz NOT NULL,
    duration_seconds numeric(10, 3) NOT NULL
);
CREATE INDEX IF NOT EXISTS mart_refresh_log_name_started_idx
    ON dwh.mart_refresh_log (mart_name, started_at DESC);
"""

REVERSE_SQL = "DROP TABLE IF EXISTS dwh.mart_refresh_log;"


class Migration(migrations.Migration):
    dependencies = [("analytics", "0004_customer_360")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)
            ]
        )
    ]
```

- [ ] **Step 4: Sửa service để ghi log**

Trong `mart_refresh_service.py`, `refresh_mart()` phải ghi một dòng vào
`dwh.mart_refresh_log` sau khi refresh xong, với `started_at` là thời điểm ngay TRƯỚC khi
gọi `REFRESH` (dùng `django.utils.timezone.now()`), `finished_at` sau khi xong.

Thêm hàm:

```python
def last_refresh_boundary():
    """Mốc as-of cho việc đối chiếu: thời điểm SỚM NHẤT trong lượt refresh gần nhất.

    Trả None nếu chưa từng refresh. Dùng `started_at` chứ không phải `finished_at` —
    xem docstring migration 0005.
    """
    with connection.cursor() as cur:
        cur.execute(
            "SELECT min(started_at) FROM ("
            "  SELECT DISTINCT ON (mart_name) mart_name, started_at"
            "  FROM dwh.mart_refresh_log"
            "  ORDER BY mart_name, started_at DESC"
            ") t"
        )
        row = cur.fetchone()
    return row[0] if row else None
```

Ghi log KHÔNG được làm hỏng refresh: nếu ghi log lỗi thì log `warning` và đi tiếp, vì mart
đã refresh xong rồi — mất một dòng log không đáng để đánh hỏng cả lượt.

- [ ] **Step 5: Run tests**

Run: `docker exec dwh2-suite python manage.py migrate analytics` rồi lệnh pytest ở Step 2.
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/apps/analytics/migrations/0005_mart_refresh_log.py backend/apps/analytics/application/mart_refresh_service.py backend/apps/analytics/tests/test_refresh_service.py
git commit -m "feat(analytics): record mart refresh boundary"
```

---

## Task 2: `dwh_reconcile` đối chiếu as-of mốc refresh

**Files:**
- Modify: `backend/apps/analytics/management/commands/dwh_reconcile.py`
- Modify: `backend/apps/analytics/tests/test_reconcile.py`

**Interfaces:**
- Consumes: `last_refresh_boundary()` từ Task 1.

> **Bản chất lỗi đang sửa:** lệnh so ảnh chụp lúc T với nguồn lúc T+n. Prod tạo ~10.000
> đơn/tháng nên gần như lần nào chạy cũng báo lệch. Một cổng an toàn báo động giả liên tục
> sẽ bị phớt lờ, và đến lúc nó báo động THẬT thì không ai nhìn.

- [ ] **Step 1: Write the failing test**

Thay thế test cũ trong `test_reconcile.py` (giữ test "khớp thì không raise") và thêm:

```python
@pytest.mark.django_db(transaction=True)
def test_order_created_after_refresh_does_not_trip_reconcile():
    """Ca đã xảy ra trên prod: đơn sinh ra SAU khi refresh khởi động không được
    coi là lệch — mart đúng, chỉ là ảnh chụp cũ hơn nguồn."""
    customer = make_customer()
    make_order(customer=customer, status=3, total_after_discount=1_000_000)
    refresh_all_marts()

    later = make_order(status=3, total_after_discount=2_000_000)
    Order.objects.filter(pk=later.pk).update(
        created_at=timezone.now() + timedelta(minutes=5)
    )

    call_command("dwh_reconcile")  # không raise


@pytest.mark.django_db(transaction=True)
def test_stale_mart_still_trips_reconcile():
    """Đơn sinh ra TRƯỚC mốc refresh mà mart không có = mart hỏng thật, phải raise."""
    customer = make_customer()
    make_order(customer=customer, status=3, total_after_discount=1_000_000)
    refresh_all_marts()

    missed = make_order(status=3, total_after_discount=2_000_000)
    Order.objects.filter(pk=missed.pk).update(
        created_at=timezone.now() - timedelta(hours=1)
    )

    with pytest.raises(CommandError):
        call_command("dwh_reconcile")


@pytest.mark.django_db(transaction=True)
def test_reconcile_errors_clearly_when_never_refreshed():
    with connection.cursor() as cur:
        cur.execute("DELETE FROM dwh.mart_refresh_log")
    with pytest.raises(CommandError, match="chưa refresh"):
        call_command("dwh_reconcile")
```

Cặp test 1+2 là điểm mấu chốt: chúng phân biệt "mart cũ hơn nguồn một cách hợp lệ" với
"mart hỏng thật". Nếu chỉ có test 1 thì một lệnh luôn trả OK cũng qua.

- [ ] **Step 2: Run test to verify it fails**

Expected: `test_order_created_after_refresh_does_not_trip_reconcile` FAIL với `CommandError`
(đúng lỗi đang sửa).

- [ ] **Step 3: Sửa command**

- Lấy `boundary = last_refresh_boundary()`. Nếu `None` → `raise CommandError` nói rõ chưa
  refresh lần nào, kèm hướng dẫn chạy `refresh_dwh_marts`.
- Mọi query PHÍA NGUỒN thêm điều kiện `o.created_at < %(boundary)s`, truyền tham số hoá.
- In thêm dòng đầu: `Đối chiếu tính đến: <boundary>` để người đọc biết mốc.
- Giữ nguyên 3 phép đo và việc `raise CommandError` khi lệch.

- [ ] **Step 4: Run tests** — Expected: PASS

- [ ] **Step 5: Verify test phân biệt được**

Tạm bỏ điều kiện `created_at < boundary` khỏi một query nguồn, chạy lại, XEM
`test_order_created_after_refresh_does_not_trip_reconcile` ĐỎ, rồi khôi phục. Báo cáo cả hai
quan sát — test chưa từng thấy đỏ là test chưa được kiểm chứng.

- [ ] **Step 6: Commit**

```bash
git commit -am "fix(analytics): reconcile as of the refresh boundary"
```

---

## Task 3: `era` + chân dung suy luận từ hành vi

**Files:**
- Create: `backend/apps/analytics/sql/eras.py`
- Create: `backend/apps/analytics/migrations/0006_customer_360_behavior.py`
- Modify: `backend/apps/analytics/tests/test_marts.py`

**Interfaces:**
- Produces: `customer_360` thêm `era`, `buyer_role`, `price_tier`, `purchase_rhythm`,
  `channel_affinity`, `product_affinity`, `recency_bucket`.

> **Vì sao cần `era`** — đo trên prod 20/08: 353.967 khách CHỈ mua ngành hàng cũ (trước
> 2025), 52.985 CHỈ mua máy massage, và chỉ **485** khách mua cả hai. Tỉ lệ chuyển đổi
> 0,14%. Gộp hai tệp này vào một phân tích chân dung là làm loãng số liệu bằng 87% người
> không phải khách của sản phẩm hiện tại.

- [ ] **Step 1: Tạo hằng số**

`backend/apps/analytics/sql/eras.py`:

```python
"""Mốc chia thời kỳ ngành hàng và các ngưỡng phân nhóm hành vi.

Mốc 2025-01-01 SUY TỪ DỮ LIỆU, không phải quy ước: giá trị đơn trung bình theo năm
trên prod là 75k (2020), 198k (2021), 163k (2022), 184k (2023), 297k (2024) rồi nhảy
lên 1.455k (2025) và 1.240k (2026) — đứt gãy khi chuyển sang máy massage Delax.

Ngưỡng giá suy từ phân bố của CHÍNH thời kỳ hiện tại (53.470 khách): dưới 1tr 29,6%,
1-2tr 64,2%, từ 2tr 6,2%. Không dùng phân vị động — ngưỡng phải ổn định giữa các lần
refresh, nếu không khách sẽ nhảy nhóm mà bản thân họ không thay đổi gì.
"""

CURRENT_ERA_START = "2025-01-01"

PRICE_TIER_MID_FLOOR = 1_000_000
PRICE_TIER_HIGH_FLOOR = 2_000_000

RECENCY_ACTIVE_MAX_DAYS = 90
RECENCY_COOLING_MAX_DAYS = 180
RECENCY_AT_RISK_MAX_DAYS = 365
```

- [ ] **Step 2: Write the failing tests**

Thêm vào `test_marts.py` (dùng `.update()` cho `created_at` vì `auto_now_add`):

```python
@pytest.mark.django_db(transaction=True)
def test_era_legacy_current_and_both():
    old = make_customer()
    o1 = make_order(customer=old, status=3, total_after_discount=50_000)
    Order.objects.filter(pk=o1.pk).update(created_at=timezone.make_aware(datetime(2023, 5, 1, 10, 0)))

    new = make_customer()
    make_order(customer=new, status=3, total_after_discount=1_500_000)

    both = make_customer()
    o2 = make_order(customer=both, status=3, total_after_discount=50_000)
    Order.objects.filter(pk=o2.pk).update(created_at=timezone.make_aware(datetime(2023, 6, 1, 10, 0)))
    make_order(customer=both, status=3, total_after_discount=1_500_000)

    refresh_all()

    assert fetch_360_col(old.id, "era") == "legacy"
    assert fetch_360_col(new.id, "era") == "current"
    assert fetch_360_col(both.id, "era") == "both"


@pytest.mark.django_db(transaction=True)
def test_price_tier_uses_fixed_thresholds():
    low = make_customer(); make_order(customer=low, status=3, total_after_discount=600_000)
    mid = make_customer(); make_order(customer=mid, status=3, total_after_discount=1_500_000)
    high = make_customer(); make_order(customer=high, status=3, total_after_discount=2_500_000)
    refresh_all()
    assert fetch_360_col(low.id, "price_tier") == "low"
    assert fetch_360_col(mid.id, "price_tier") == "mid"
    assert fetch_360_col(high.id, "price_tier") == "high"


@pytest.mark.django_db(transaction=True)
def test_purchase_rhythm_and_recency():
    once = make_customer(); make_order(customer=once, status=3, total_after_discount=1_500_000)
    twice = make_customer()
    make_order(customer=twice, status=3, total_after_discount=1_500_000)
    make_order(customer=twice, status=3, total_after_discount=1_500_000)
    refresh_all()
    assert fetch_360_col(once.id, "purchase_rhythm") == "one_time"
    assert fetch_360_col(twice.id, "purchase_rhythm") == "repeat"
    assert fetch_360_col(once.id, "recency_bucket") == "active"


@pytest.mark.django_db(transaction=True)
def test_cancelled_only_customer_has_null_behavior_not_wrong_labels():
    """Khách chỉ có đơn huỷ: era/price_tier/rhythm phải NULL, KHÔNG được gán nhãn sai."""
    c = make_customer(); make_order(customer=c, status=6, total_after_discount=1_500_000)
    refresh_all()
    assert fetch_360_col(c.id, "era") is None
    assert fetch_360_col(c.id, "price_tier") is None
    assert fetch_360_col(c.id, "purchase_rhythm") is None
```

Thêm helper `fetch_360_col(customer_id, col)` đọc một cột từ `dwh.customer_360`.

- [ ] **Step 3: Run — Expected FAIL** (`column "era" does not exist`)

- [ ] **Step 4: Migration 0006**

`DROP MATERIALIZED VIEW IF EXISTS dwh.customer_360;` rồi tạo lại y hệt 0004 nhưng thêm vào
final SELECT:

```sql
CASE
    WHEN a.orders_gtc = 0 THEN NULL
    WHEN a.gtc_legacy > 0 AND a.gtc_current > 0 THEN 'both'
    WHEN a.gtc_current > 0 THEN 'current'
    ELSE 'legacy'
END AS era,
CASE
    WHEN a.orders_gtc = 0 THEN NULL
    WHEN a.avg_order_value >= {PRICE_TIER_HIGH_FLOOR} THEN 'high'
    WHEN a.avg_order_value >= {PRICE_TIER_MID_FLOOR} THEN 'mid'
    ELSE 'low'
END AS price_tier,
CASE
    WHEN a.orders_gtc = 0 THEN NULL
    WHEN a.orders_gtc >= 2 THEN 'repeat'
    ELSE 'one_time'
END AS purchase_rhythm,
CASE
    WHEN a.last_purchase_at IS NULL THEN NULL
    WHEN (now()::date - a.last_purchase_at::date) <= {RECENCY_ACTIVE_MAX_DAYS} THEN 'active'
    WHEN (now()::date - a.last_purchase_at::date) <= {RECENCY_COOLING_MAX_DAYS} THEN 'cooling'
    WHEN (now()::date - a.last_purchase_at::date) <= {RECENCY_AT_RISK_MAX_DAYS} THEN 'at_risk'
    ELSE 'dormant'
END AS recency_bucket,
a.channel_affinity,
a.product_affinity,
CASE
    WHEN p.need_group_codes IS NULL OR p.need_group_codes = '{{}}' THEN NULL
    WHEN 'gift' = ANY(p.need_group_codes) AND 'buy_for_self' = ANY(p.need_group_codes) THEN 'mixed'
    WHEN 'gift' = ANY(p.need_group_codes) THEN 'gift'
    WHEN 'buy_for_self' = ANY(p.need_group_codes) THEN 'self'
    ELSE NULL
END AS buyer_role
```

Trong CTE `agg` thêm:

```sql
count(*) FILTER (WHERE f.is_gtc AND f.created_date <  date '{CURRENT_ERA_START}') AS gtc_legacy,
count(*) FILTER (WHERE f.is_gtc AND f.created_date >= date '{CURRENT_ERA_START}') AS gtc_current,
(array_agg(f.channel_name ORDER BY f.created_at DESC) FILTER (WHERE f.is_gtc AND f.channel_name IS NOT NULL))[1] AS channel_affinity,
(array_agg(f.primary_sku  ORDER BY f.created_at DESC) FILTER (WHERE f.is_gtc AND f.primary_sku  IS NOT NULL))[1] AS product_affinity,
```

⚠️ SQL nằm trong f-string Python → mọi dấu ngoặc nhọn của SQL (`'{}'` mảng rỗng) phải nhân
đôi thành `{{}}`. Đây là chỗ dễ vỡ nhất của task này.

Thêm index: `customer_360_era_idx ON (era)`, `customer_360_recency_idx ON (recency_bucket)`.

`dependencies = [("analytics", "0005_mart_refresh_log")]`.

- [ ] **Step 5: Migrate + run tests** — Expected: PASS

- [ ] **Step 6: Commit**

```bash
git commit -am "feat(analytics): add era and behavioural profile to customer_360"
```

---

## Task 4: Cập nhật doc

**Files:** Modify `docs/dwh-customer-analytics.md`

- [ ] **Step 1:** §9 bảng tiến độ: R1 và S2b → ✅.
- [ ] **Step 2:** Thêm §11 mô tả 7 cột mới, mốc `2025-01-01` và lý do suy từ dữ liệu, các
  ngưỡng và lý do dùng ngưỡng cố định thay vì phân vị động.
- [ ] **Step 3:** Ghi vào §10.4 rằng `dwh_reconcile` giờ in mốc as-of, và runbook §8.2 phải
  chạy refresh TRƯỚC reconcile (nếu không sẽ báo "chưa refresh lần nào").
- [ ] **Step 4: Commit**

---

## Self-Review

**Spec coverage:** §10.3 (khiếm khuyết reconcile) → Task 1+2. §10 phần era → Task 3. Cập
nhật tiến độ → Task 4.

**Placeholder scan:** không có TBD/TODO; mọi bước code có code thật.

**Type consistency:** `last_refresh_boundary()` định nghĩa ở Task 1, dùng ở Task 2. Hằng số
`CURRENT_ERA_START`/`PRICE_TIER_*`/`RECENCY_*` định nghĩa ở Task 3 Step 1, dùng ở Step 4.
Tên cột `era`/`price_tier`/`purchase_rhythm`/`recency_bucket`/`channel_affinity`/
`product_affinity`/`buyer_role` nhất quán giữa test và migration.

**Khoảng trống có chủ đích:** `buyer_role` chỉ suy được cho khách có `need_group_codes` —
độ phủ thấp (8.150 đơn có ghi nhu cầu). Đó là chấp nhận được: nó là cột phụ, và cột `era`
mới là chiều phân tích chính của task này.
