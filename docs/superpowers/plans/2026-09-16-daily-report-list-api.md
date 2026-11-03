# Daily Report API trả danh sách dòng — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đổi 6 endpoint `/api/daily-report/*` từ trả số gộp sang trả từng dòng (đơn, dòng hàng, lead mở, chi tiêu ads) có phân trang keyset; `/summary` → 410; `/definitions` thêm bản đồ TKQC→Digital và luật hệ số. Hermes tự cộng.

**Architecture:** Giữ nguyên app `backend/apps/daily_report` (domain / infrastructure / application / transport). SQL đọc mart `ai.don_hang`, `ai.dong_don_hang` bằng cursor tham số hoá, `WHERE id > cursor ORDER BY id LIMIT n+1`. Một module thuần `application/rows.py` dịch dict SQL → JSON tiếng Anh. `/ads` ghép hai grain OLTP có sẵn (`ads_spend.account_spend`, `campaign_spend`) thành dòng chiến dịch + dòng dư, sắp tuple, cắt trang trong Python.

**Tech Stack:** Django 5 + DRF + drf-spectacular; Postgres (mart schema `ai`, không có model Django — SQL thô qua `connection.cursor()`); pytest chạy trên container test.

**Spec:** `docs/superpowers/specs/2026-09-16-daily-report-list-api-design.md`

## Global Constraints

- Không migration, không env mới, không đụng frontend, không đụng `apps/ai_mart`, `apps/orders`.
- URL 8 endpoint giữ nguyên; header `X-Api-Token` / `DAILY_REPORT_API_TOKEN` giữ nguyên; `transport/auth.py` không đổi.
- `from`/`to` mặc định hôm qua, tối đa 92 ngày — `domain/periods.py` không đổi.
- `group_by` gửi lên → 400 với detail: ``Tham số `group_by` không còn; API trả từng dòng, tự nhóm phía gọi.``
- `limit` mặc định 200; trần: dòng đơn/lead 1000, dòng hàng 2000, ads 2000; vượt trần → kẹp, không lỗi; không phải số nguyên dương → 400.
- Response danh sách: `{"meta": {...như cũ...}, "data": [rows], "limit": N, "next_cursor": X | null}`.
- Tiền: số nguyên VND (`int(round(x))`); thời điểm ISO 8601 `+07:00`; ngày `YYYY-MM-DD`.
- PII: chỉ `ten_khach` và `sdt_che` (đã che ở mart).
- Mọi số Hermes cộng lại từ dòng phải bằng số gộp cũ trên cùng fixture — test đối chiếu với `OrderRepository.summary_overview` (màn hình CRM), không với chính API.
- Commit message tiếng Việt theo kiểu repo (`feat(daily-report): …`), kết bằng dòng `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## Chạy test

Worktree: `/home/desi/desi-order/.claude/worktrees/feat-daily-report-list-api`. Mọi lệnh chạy từ `backend/` trong worktree. Container test phải đang chạy (`docker ps | grep desi-test`; nếu Exited: `docker start desi-test-pg desi-test-redis`). KHÔNG chạy hai lượt pytest song song (VPS chật RAM).

```bash
cd /home/desi/desi-order/.claude/worktrees/feat-daily-report-list-api/backend
export POSTGRES_DB=desi_test_drlist POSTGRES_USER=desi POSTGRES_PASSWORD=ci-postgres-pass \
  POSTGRES_HOST=localhost POSTGRES_PORT=5433 REDIS_HOST=localhost REDIS_PORT=6380 REDIS_PASSWORD= \
  APP_ENV=development DJANGO_DEBUG=0 LOG_FORMAT=text ZENIFY_DOMAIN= ZENIFY_API_KEY= ZENIFY_API_SECRET= \
  ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
PYTEST=/home/desi/desi-order/.venv/bin/pytest
$PYTEST apps/daily_report -q --reuse-db -p no:cacheprovider
```

DB test `test_desi_test_drlist` đã tồn tại (baseline 69 passed). Ghi output dài ra `/home/desi/.cache/dr-*.txt`, không pipe qua `tail` khi cần danh sách FAILED.

## Cấu trúc file

| File | Trách nhiệm sau khi xong |
|---|---|
| `domain/paging.py` (mới) | parse `limit`/`cursor`, từ chối `group_by`, cắt trang n+1 |
| `domain/definitions.py` | + `order_bucket_of`, `cost_multiplier_rules`, glossary mới |
| `infrastructure/queries.py` | SQL dòng: `order_rows`, `order_line_rows`, `open_lead_rows`, `dictionary_rows` |
| `infrastructure/ads_spend.py` | + `account_directory()` |
| `application/rows.py` (mới) | dict SQL → dòng JSON (`order_row`, `open_lead_row`, `order_line_row`) |
| `application/stats_service.py` | `envelope`, `page_envelope`, 4 payload danh sách, `definitions` |
| `application/ads_service.py` | `ad_spend_rows`, cursor tuple, `ads_payload` |
| `transport/serializers.py` | serializer OpenAPI cho dòng + envelope trang |
| `transport/views.py` | 8 view; `summary` → 410 |
| `tests/` | viết lại theo dòng |

---

### Task 1: `domain/paging.py` + `order_bucket_of`

**Files:**
- Create: `backend/apps/daily_report/domain/paging.py`
- Modify: `backend/apps/daily_report/domain/definitions.py` (thêm hàm cuối file, trước `ORDER_BUCKET_LABELS`)
- Test: `backend/apps/daily_report/tests/test_paging.py` (mới)

**Interfaces:**
- Produces: `paging.DEFAULT_LIMIT = 200`, `paging.ORDER_MAX_LIMIT = 1000`, `paging.LINE_MAX_LIMIT = 2000`, `paging.ADS_MAX_LIMIT = 2000`, `paging.GROUP_BY_GONE: str`, `paging.reject_group_by(params) -> None`, `paging.parse_limit(params, *, max_limit: int) -> int`, `paging.parse_int_cursor(params) -> int | None`, `paging.slice_page(rows: list, limit: int) -> tuple[list, bool]`, `definitions.order_bucket_of(code: int | None) -> str | None`.

- [ ] **Step 1: Viết test**

```python
# backend/apps/daily_report/tests/test_paging.py
from django.http import QueryDict
from django.test import SimpleTestCase

from apps.daily_report.domain import definitions as d
from apps.daily_report.domain import paging
from apps.daily_report.domain.periods import ParamError


def q(s: str) -> QueryDict:
    return QueryDict(s)


class PagingTests(SimpleTestCase):
    def test_limit_default_and_clamp(self):
        self.assertEqual(paging.parse_limit(q(""), max_limit=1000), 200)
        self.assertEqual(paging.parse_limit(q("limit=50"), max_limit=1000), 50)
        self.assertEqual(paging.parse_limit(q("limit=999999"), max_limit=1000), 1000)
        self.assertEqual(paging.parse_limit(q(""), max_limit=100), 100)  # trần nhỏ hơn mặc định

    def test_limit_invalid(self):
        for raw in ("limit=0", "limit=-1", "limit=abc", "limit=1.5"):
            with self.assertRaises(ParamError, msg=raw):
                paging.parse_limit(q(raw), max_limit=1000)

    def test_int_cursor(self):
        self.assertIsNone(paging.parse_int_cursor(q("")))
        self.assertEqual(paging.parse_int_cursor(q("cursor=123")), 123)
        with self.assertRaises(ParamError):
            paging.parse_int_cursor(q("cursor=abc"))

    def test_group_by_rejected_even_when_empty(self):
        paging.reject_group_by(q(""))  # không ném
        for raw in ("group_by=day", "group_by="):
            with self.assertRaises(ParamError) as ctx:
                paging.reject_group_by(q(raw))
            self.assertEqual(str(ctx.exception), paging.GROUP_BY_GONE)

    def test_slice_page(self):
        self.assertEqual(paging.slice_page([1, 2, 3, 4], 3), ([1, 2, 3], True))
        self.assertEqual(paging.slice_page([1, 2, 3], 3), ([1, 2, 3], False))
        self.assertEqual(paging.slice_page([], 3), ([], False))


class OrderBucketTests(SimpleTestCase):
    def test_buckets(self):
        self.assertIsNone(d.order_bucket_of(None))
        self.assertEqual(d.order_bucket_of(d.CANCELLED_STATUS), "cancelled")
        self.assertEqual(d.order_bucket_of(d.DELIVERED_STATUS), "delivered")
        self.assertEqual(d.order_bucket_of(d.RETURNED_STATUSES[0]), "returned")
        self.assertEqual(d.order_bucket_of(d.RETURN_RISK_STATUSES[0]), "return_risk")
        self.assertEqual(d.order_bucket_of(99), "awaiting")  # mã lạ rơi vào phần bù
```

- [ ] **Step 2: Chạy test, phải fail**

Run: `$PYTEST apps/daily_report/tests/test_paging.py -q --reuse-db -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: apps.daily_report.domain.paging`

- [ ] **Step 3: Viết `domain/paging.py`**

```python
"""Phân trang keyset cho API Daily Report.

Hermes kéo toàn bộ theo cụm rồi tự cộng. Keyset thay offset vì mart ETL 30 phút
chèn dòng mới giữa lúc đang kéo — offset sâu vừa chậm dần vừa nhảy sót/lặp.
"""

from __future__ import annotations

from apps.daily_report.domain.periods import ParamError

DEFAULT_LIMIT = 200
# Một dòng đơn ~1,5KB → 1000 dòng ≈ 1,5MB; dòng hàng và dòng ads nhẹ hơn nhiều.
ORDER_MAX_LIMIT = 1000
LINE_MAX_LIMIT = 2000
ADS_MAX_LIMIT = 2000

GROUP_BY_GONE = "Tham số `group_by` không còn; API trả từng dòng, tự nhóm phía gọi."
_BAD_LIMIT = "Tham số `limit` phải là số nguyên dương."
_BAD_CURSOR = "Tham số `cursor` không hợp lệ — dùng `next_cursor` của trang trước."


def reject_group_by(params) -> None:
    """Không lặng lẽ bỏ qua: bên gọi phải biết hình dạng response đã đổi."""
    if "group_by" in params:
        raise ParamError(GROUP_BY_GONE)


def parse_limit(params, *, max_limit: int) -> int:
    raw = (params.get("limit") or "").strip()
    if not raw:
        return min(DEFAULT_LIMIT, max_limit)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ParamError(_BAD_LIMIT) from exc
    if value < 1:
        raise ParamError(_BAD_LIMIT)
    return min(value, max_limit)


def parse_int_cursor(params) -> int | None:
    raw = (params.get("cursor") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ParamError(_BAD_CURSOR) from exc


def slice_page(rows: list, limit: int) -> tuple[list, bool]:
    """``rows`` đã fetch ``limit + 1`` → (trang, còn trang sau?)."""
    return rows[:limit], len(rows) > limit
```

- [ ] **Step 4: Thêm `order_bucket_of` vào `domain/definitions.py`**

Chèn ngay sau dòng `CHANNEL_GROUPS = SALE_CHANNEL_GROUPS`:

```python


def order_bucket_of(code: int | None) -> str | None:
    """Rổ giao hàng của Báo cáo tổng. Mã lạ rơi vào 'awaiting' (phần bù) để không
    rớt khỏi tổng; None = đơn chưa có trạng thái."""
    if code is None:
        return None
    if code == CANCELLED_STATUS:
        return "cancelled"
    if code == DELIVERED_STATUS:
        return "delivered"
    if code in RETURNED_STATUSES:
        return "returned"
    if code in RETURN_RISK_STATUSES:
        return "return_risk"
    return "awaiting"
```

Trong `application/stats_service.py`: xoá hàm `_order_bucket` (dòng ~`def _order_bucket(code: int) -> str:` tới `return "awaiting"`) và trong `definitions()` thay `_order_bucket(int(x["code"]))` bằng `d.order_bucket_of(int(x["code"]))`.

- [ ] **Step 5: Chạy test, phải pass**

Run: `$PYTEST apps/daily_report/tests/test_paging.py apps/daily_report/tests/test_definitions_api.py -q --reuse-db -p no:cacheprovider`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/apps/daily_report/domain/paging.py backend/apps/daily_report/domain/definitions.py backend/apps/daily_report/application/stats_service.py backend/apps/daily_report/tests/test_paging.py
git commit -m "feat(daily-report): helper phân trang keyset + order_bucket_of ở domain

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: SQL trả dòng trong `infrastructure/queries.py`

**Files:**
- Modify: `backend/apps/daily_report/infrastructure/queries.py`
- Test: `backend/apps/daily_report/tests/test_queries_rows.py` (mới)

**Interfaces:**
- Produces: `queries.ORDER_COLUMNS: tuple[str, ...]`, `queries.order_rows(d_from, d_to, *, invoiced_only: bool, after: int | None, limit: int) -> list[dict]`, `queries.order_line_rows(d_from, d_to, *, after: int | None, limit: int) -> list[dict]`, `queries.open_lead_rows(as_of, lookback_days, *, after: int | None, limit: int) -> list[dict]` (dict có mọi cột `ORDER_COLUMNS` + `tuoi`), `queries.dictionary_rows()` (giữ).
- Hàm cũ `leads_rows`, `orders_rows`, `_group_clauses`, `_bucket_select`, `GROUP_COLUMNS` **giữ lại ở task này** (stats_service còn dùng); xoá ở Task 7.

- [ ] **Step 1: Viết test**

```python
# backend/apps/daily_report/tests/test_queries_rows.py
"""SQL dòng: đúng tập lọc của số gộp cũ, keyset đi hết không sót/lặp."""

from datetime import date

from django.test import TestCase

from apps.catalog.models import Product
from apps.daily_report.infrastructure import queries
from apps.orders.models import OrderItem

from .helpers import rebuild, seed_order

D = date(2026, 9, 2)


class OrderRowsQueryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.delivered = seed_order(D, status=3, invoice_status=1, amount=500_000, sales_status="closed")
        seed_order(D, status=2, invoice_status=1, amount=200_000)                 # awaiting
        seed_order(D, status=6, invoice_status=1, amount=999)                      # huỷ POS → không phải đơn
        cls.lead_only = seed_order(D, status=3, invoice_status=None, amount=999)  # lead chưa HĐ
        seed_order(D, status=3, invoice_status=1, amount=999, branch_id="1000000139")  # chi nhánh ẩn
        seed_order(date(2026, 9, 1), status=3, invoice_status=1, amount=1)        # ngoài kỳ
        prod = Product.objects.create(name="Ghế MSC06", sku="MSC06")
        OrderItem.objects.create(order=cls.delivered, product=prod, sku="MSC06", quantity=1, unit_price=500_000, line_total=500_000)
        OrderItem.objects.create(order=cls.lead_only, product=prod, sku="MSC06", quantity=1, unit_price=999, line_total=999)
        rebuild()

    def test_leads_vs_orders_filter(self):
        leads = queries.order_rows(D, D, invoiced_only=False, after=None, limit=100)
        orders = queries.order_rows(D, D, invoiced_only=True, after=None, limit=100)
        self.assertEqual(len(leads), 5)
        order_ids = {r["don_id"] for r in orders}
        self.assertEqual(len(orders), 2)
        self.assertIn(self.delivered.id, order_ids)
        self.assertNotIn(self.lead_only.id, order_ids)
        self.assertEqual(set(queries.ORDER_COLUMNS), set(leads[0].keys()))
        self.assertIn("nhom_kenh_ban", leads[0])

    def test_keyset_walks_all_without_dup(self):
        seen, after = [], None
        while True:
            page = queries.order_rows(D, D, invoiced_only=False, after=after, limit=2)
            seen += [r["don_id"] for r in page]
            if len(page) < 2:
                break
            after = page[-1]["don_id"]
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(len(seen), 5)

    def test_order_lines_follow_orders_filter(self):
        lines = queries.order_line_rows(D, D, after=None, limit=100)
        self.assertEqual([r["don_id"] for r in lines], [self.delivered.id])
        self.assertEqual(lines[0]["thanh_tien"], 500_000)
        self.assertEqual(lines[0]["sku"], "MSC06")
        self.assertIn("dong_id", lines[0])

    def test_open_leads_have_full_columns_and_age(self):
        rows = queries.open_lead_rows(D, 30, after=None, limit=100)
        self.assertEqual([r["don_id"] for r in rows], [self.lead_only.id])
        self.assertEqual(rows[0]["tuoi"], 0)
        self.assertIn("ten_khach", rows[0])
```

- [ ] **Step 2: Chạy test, phải fail**

Run: `$PYTEST apps/daily_report/tests/test_queries_rows.py -q --reuse-db -p no:cacheprovider`
Expected: FAIL — `TypeError: order_rows() got an unexpected keyword argument` / `AttributeError: ORDER_COLUMNS`

- [ ] **Step 3: Thêm vào `queries.py`** (sau `dictionary_rows` hoặc trước `open_lead_rows` cũ; THAY hàm `open_lead_rows` cũ bằng bản mới)

```python
# Mọi cột nghiệp vụ của ai.don_hang (migration 0005 + 0013). Không SELECT * để
# cột thêm sau này không lọt ra API mà chưa được dịch tên ở application/rows.py.
ORDER_COLUMNS: tuple[str, ...] = (
    "don_id", "ma_don", "ngay_tao", "ngay_tao_ngay", "ngay_chot", "ngay_chot_ngay",
    "trang_thai_don_ma", "trang_thai_don_text", "trang_thai_hoa_don_ma", "trang_thai_hoa_don_text",
    "la_don_gtc", "loai_don_ma", "loai_don_text", "loai_don_koc_ma", "loai_don_koc_text",
    "nguon_don_ma", "nguon_don_text", "hinh_thuc_giao_ma", "hinh_thuc_giao_text", "phuong_thuc_tt",
    "kenh_id", "kenh_ten", "loai_kenh_text", "nhom_kenh_ban", "chi_nhanh_id",
    "nhan_vien_id", "nhan_vien_ten",
    "khach_id", "ma_khach", "ten_khach", "sdt_che", "tinh_thanh", "quan_huyen", "la_khach_moi",
    "tien_hang", "giam_gia", "phi_ship", "tong_sau_giam", "so_luong",
    "so_dong_sp", "danh_sach_sku", "danh_sach_sku_text", "sku_chinh",
    "trang_thai_sale_ma", "trang_thai_sale_text", "ly_do_khong_chot_ma", "ly_do_khong_chot_text",
    "trang_thai_giao_nghiep_vu_ma", "trang_thai_giao_nghiep_vu_text",
    "nhom_ly_do_bom_ma", "nhom_ly_do_bom_text", "phan_loai_khach_ma", "phan_loai_khach_text",
    "nhu_cau_ma", "nhu_cau_text", "tuoi_text", "tuoi_nguon_text",
    "nghe_nghiep_ma", "nghe_nghiep_text", "muc_do_dau_ma", "muc_do_dau_text",
    "so_cuoc_goi", "tong_giay_goi", "co_transcript", "da_boc_ai", "diem_chat_luong", "dong_bo_luc",
)


def _select(alias: str = "") -> str:
    return ", ".join(f"{alias}{c}" for c in ORDER_COLUMNS)


def _invoiced_filter(alias: str = "") -> tuple[str, dict]:
    """Tập đơn = có hoá đơn, không huỷ, loại chi nhánh ẩn — đúng total_q của Báo cáo tổng."""
    sql = f"""
  AND {alias}trang_thai_hoa_don_ma IS NOT NULL
  AND {alias}trang_thai_don_ma IS DISTINCT FROM %(cancelled)s
  AND {alias}trang_thai_hoa_don_ma IS DISTINCT FROM %(cancelled_invoice)s
  AND coalesce({alias}chi_nhanh_id, '') <> ALL(%(excluded_branches)s)"""
    params = {
        "cancelled": d.CANCELLED_STATUS,
        "cancelled_invoice": d.CANCELLED_INVOICE,
        "excluded_branches": list(d.EXCLUDED_BRANCHES),
    }
    return sql, params


def _after_clause(column: str, after: int | None, params: dict) -> str:
    """Keyset: không dùng `%(after)s IS NULL OR …` vì psycopg không suy được kiểu NULL."""
    if after is None:
        return ""
    params["after"] = after
    return f"\n  AND {column} > %(after)s"


def order_rows(d_from: date, d_to: date, *, invoiced_only: bool, after: int | None, limit: int) -> list[dict]:
    """Từng đơn tạo trong kỳ. ``invoiced_only=False`` = tập lead (mọi trạng thái),
    ``True`` = tập đơn có hoá đơn của Báo cáo tổng."""
    params: dict = {"from": d_from, "to": d_to, "limit": limit}
    extra = ""
    if invoiced_only:
        extra, extra_params = _invoiced_filter()
        params.update(extra_params)
    sql = f"""
SELECT {_select()}
FROM ai.don_hang
WHERE ngay_tao_ngay BETWEEN %(from)s AND %(to)s{extra}{_after_clause("don_id", after, params)}
ORDER BY don_id
LIMIT %(limit)s
"""
    return run(sql, params)


def order_line_rows(d_from: date, d_to: date, *, after: int | None, limit: int) -> list[dict]:
    """Từng dòng hàng của tập đơn có hoá đơn (cùng bộ lọc với order_rows(invoiced_only=True)).
    Lọc theo ngày của ĐƠN để hai endpoint chắc chắn cùng tập đơn."""
    extra, params = _invoiced_filter("h.")
    params.update({"from": d_from, "to": d_to, "limit": limit})
    sql = f"""
SELECT l.dong_id, l.don_id, l.ma_don, h.ngay_tao_ngay,
       l.sku, l.ten_sp, l.nhom_sp_text, l.so_luong, l.don_gia, l.thanh_tien
FROM ai.dong_don_hang l
JOIN ai.don_hang h ON h.don_id = l.don_id
WHERE h.ngay_tao_ngay BETWEEN %(from)s AND %(to)s{extra}{_after_clause("l.dong_id", after, params)}
ORDER BY l.dong_id
LIMIT %(limit)s
"""
    return run(sql, params)


def open_lead_rows(as_of: date, lookback_days: int, *, after: int | None, limit: int) -> list[dict]:
    """Lead còn có thể chốt tính đến cuối ngày as_of, kèm ``tuoi`` (ngày)."""
    params: dict = {
        "from": as_of - timedelta(days=lookback_days),
        "as_of": as_of,
        "cancelled": d.CANCELLED_STATUS,
        "terminal": list(d.PIPELINE_TERMINAL_CODES),
        "limit": limit,
    }
    sql = f"""
SELECT {_select()},
       (%(as_of)s::date - ngay_tao_ngay)::int AS tuoi
FROM ai.don_hang
WHERE ngay_tao_ngay BETWEEN %(from)s AND %(as_of)s
  AND trang_thai_hoa_don_ma IS NULL
  AND trang_thai_don_ma IS DISTINCT FROM %(cancelled)s
  AND (trang_thai_sale_ma IS NULL OR NOT (trang_thai_sale_ma = ANY(%(terminal)s))){_after_clause("don_id", after, params)}
ORDER BY don_id
LIMIT %(limit)s
"""
    return run(sql, params)
```

Vì `open_lead_rows` đổi chữ ký, sửa tạm chỗ gọi trong `stats_service.pipeline`: `rows = queries.open_lead_rows(as_of, lookback_days, after=None, limit=100_000)` (sẽ viết lại ở Task 5).

- [ ] **Step 4: Chạy test, phải pass (cả test cũ)**

Run: `$PYTEST apps/daily_report -q --reuse-db -p no:cacheprovider`
Expected: PASS (69 cũ + 4 mới + Task 1)

- [ ] **Step 5: Commit**

```bash
git add backend/apps/daily_report/infrastructure/queries.py backend/apps/daily_report/application/stats_service.py backend/apps/daily_report/tests/test_queries_rows.py
git commit -m "feat(daily-report): SQL trả từng dòng đơn/dòng hàng/lead mở với keyset

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `application/rows.py` — dịch dict SQL sang dòng JSON

**Files:**
- Create: `backend/apps/daily_report/application/rows.py`
- Test: `backend/apps/daily_report/tests/test_rows.py` (mới)

**Interfaces:**
- Consumes: `queries.ORDER_COLUMNS`, `definitions.order_bucket_of`, `definitions.bucket_of`, `periods.TZ`, `periods.iso`.
- Produces: `rows.order_row(r: dict) -> dict`, `rows.open_lead_row(r: dict) -> dict`, `rows.order_line_row(r: dict) -> dict`. Mỗi dict trả về có khoá `"cursor"`.

- [ ] **Step 1: Viết test**

```python
# backend/apps/daily_report/tests/test_rows.py
"""rows.py thuần: dict SQL → dict JSON. Mọi cột NULL vẫn ra object con đủ khoá."""

from datetime import date, datetime, timezone
from decimal import Decimal

from django.test import SimpleTestCase

from apps.daily_report.application import rows
from apps.daily_report.infrastructure.queries import ORDER_COLUMNS


def _blank(**overrides) -> dict:
    base = {c: None for c in ORDER_COLUMNS}
    base.update(don_id=1, la_don_gtc=False)
    base.update(overrides)
    return base


class OrderRowTests(SimpleTestCase):
    def test_all_null_gives_stable_shape(self):
        out = rows.order_row(_blank())
        self.assertEqual(out["id"], 1)
        self.assertEqual(out["cursor"], 1)
        self.assertIsNone(out["staff"])
        self.assertIsNone(out["channel"])
        self.assertEqual(out["order_status"], {"code": None, "label": None, "bucket": None})
        self.assertEqual(out["sales_status"], {"code": None, "label": None, "bucket": None})
        self.assertEqual(out["items"], {"line_count": None, "skus": [], "skus_label": None, "main_sku": None})
        self.assertEqual(set(out["customer"]), {"id", "code", "name", "phone_masked", "province", "district", "is_new"})
        self.assertEqual(set(out["amounts"]), {"goods", "discount", "shipping", "total", "quantity"})
        self.assertEqual(set(out["calls"]), {"count", "seconds", "has_transcript", "ai_extracted", "quality_score"})
        self.assertFalse(out["is_valid_revenue"])

    def test_values_mapped(self):
        out = rows.order_row(_blank(
            ma_don="DL1", ngay_tao=datetime(2026, 9, 15, 3, 0, tzinfo=timezone.utc), ngay_tao_ngay=date(2026, 9, 15),
            trang_thai_don_ma=3, trang_thai_don_text="Giao thành công",
            trang_thai_sale_ma="closed", trang_thai_sale_text="Đã chốt",
            kenh_id=12, kenh_ten="FB Cường", loai_kenh_text="Facebook", nhom_kenh_ban="facebook",
            nhan_vien_id=45, nhan_vien_ten="A", sdt_che="0912***678",
            tong_sau_giam=Decimal("1430000.4"), danh_sach_sku=["MSC06", "GEL01"], diem_chat_luong=Decimal("7.5"),
        ))
        self.assertEqual(out["code"], "DL1")
        self.assertEqual(out["created_at"], "2026-09-15T10:00:00+07:00")
        self.assertEqual(out["created_date"], "2026-09-15")
        self.assertEqual(out["order_status"], {"code": 3, "label": "Giao thành công", "bucket": "delivered"})
        self.assertEqual(out["sales_status"]["bucket"], "closed")
        self.assertEqual(out["channel"], {"id": 12, "name": "FB Cường", "type": "Facebook"})
        self.assertEqual(out["channel_group"], "facebook")
        self.assertEqual(out["staff"], {"id": 45, "name": "A"})
        self.assertEqual(out["customer"]["phone_masked"], "0912***678")
        self.assertEqual(out["amounts"]["total"], 1430000)
        self.assertIsInstance(out["amounts"]["total"], int)
        self.assertEqual(out["items"]["skus"], ["MSC06", "GEL01"])
        self.assertEqual(out["calls"]["quality_score"], 7.5)

    def test_open_lead_row_is_subset_with_age(self):
        out = rows.open_lead_row({**_blank(nhan_vien_id=1, nhan_vien_ten="A"), "tuoi": 6})
        self.assertEqual(out["age_days"], 6)
        self.assertEqual(out["staff"], {"id": 1, "name": "A"})
        self.assertNotIn("order_status", out)
        self.assertNotIn("invoice_status", out)
        self.assertEqual(out["cursor"], 1)

    def test_order_line_row(self):
        out = rows.order_line_row({
            "dong_id": 9, "don_id": 1, "ma_don": "DL1", "ngay_tao_ngay": date(2026, 9, 15),
            "sku": "MSC06", "ten_sp": "Ghế", "nhom_sp_text": None, "so_luong": 2,
            "don_gia": Decimal("700000"), "thanh_tien": Decimal("1400000"),
        })
        self.assertEqual(out, {
            "line_id": 9, "order_id": 1, "order_code": "DL1", "created_date": "2026-09-15",
            "sku": "MSC06", "product_name": "Ghế", "product_group": None, "quantity": 2,
            "unit_price": 700000, "amount": 1400000, "cursor": 9,
        })
```

- [ ] **Step 2: Chạy test, phải fail**

Run: `$PYTEST apps/daily_report/tests/test_rows.py -q --reuse-db -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'rows'`

- [ ] **Step 3: Viết `application/rows.py`**

```python
"""Dịch dòng SQL của mart ``ai.*`` sang dòng JSON tiếng Anh cho Hermes.

Thuần dict → dict, không chạm DB. Cột ``*_ma`` + ``*_text`` gộp thành
``{"code", "label"}``; nơi CRM có luật rổ (trạng thái đơn, trạng thái sale) kèm
``"bucket"`` để Hermes cộng theo rổ đúng như màn hình mà không phải tra
``/definitions`` từng dòng. Object con có mọi trường NULL vẫn trả object đủ khoá
(đường dẫn cố định); ngoại lệ ``staff`` và ``channel`` trả ``null`` khi không có id —
đúng cách API cũ biểu diễn "chưa gán".
"""

from __future__ import annotations

from datetime import date, datetime

from apps.daily_report.domain import definitions as d
from apps.daily_report.domain.periods import TZ, iso


def _ts(value: datetime | None) -> str | None:
    return value.astimezone(TZ).isoformat() if value else None


def _day(value: date | None) -> str | None:
    return iso(value) if value else None


def _vnd(value) -> int | None:
    return int(round(value)) if value is not None else None


def _num(value) -> float | None:
    return float(value) if value is not None else None


def _code_label(code, label) -> dict:
    return {"code": code, "label": label}


def _ref(id_, name) -> dict | None:
    return {"id": id_, "name": name} if id_ is not None else None


def _channel(r: dict) -> dict | None:
    if r["kenh_id"] is None:
        return None
    return {"id": r["kenh_id"], "name": r["kenh_ten"], "type": r["loai_kenh_text"]}


def _sales_status(r: dict) -> dict:
    code = r["trang_thai_sale_ma"]
    # NULL = chưa đánh giá (số gộp cũ gọi là unassessed), không phải rổ "other".
    return {**_code_label(code, r["trang_thai_sale_text"]), "bucket": d.bucket_of(code) if code else None}


def _common(r: dict) -> dict:
    """Phần dùng chung giữa dòng đơn và dòng lead mở."""
    return {
        "id": r["don_id"],
        "code": r["ma_don"],
        "created_at": _ts(r["ngay_tao"]),
        "created_date": _day(r["ngay_tao_ngay"]),
        "source": _code_label(r["nguon_don_ma"], r["nguon_don_text"]),
        "channel": _channel(r),
        "channel_group": r["nhom_kenh_ban"],
        "staff": _ref(r["nhan_vien_id"], r["nhan_vien_ten"]),
        "customer": {
            "id": r["khach_id"],
            "code": r["ma_khach"],
            "name": r["ten_khach"],
            "phone_masked": r["sdt_che"],
            "province": r["tinh_thanh"],
            "district": r["quan_huyen"],
            "is_new": r["la_khach_moi"],
        },
        "amounts": {
            "goods": _vnd(r["tien_hang"]),
            "discount": _vnd(r["giam_gia"]),
            "shipping": _vnd(r["phi_ship"]),
            "total": _vnd(r["tong_sau_giam"]),
            "quantity": r["so_luong"],
        },
        "items": {
            "line_count": r["so_dong_sp"],
            "skus": list(r["danh_sach_sku"] or []),
            "skus_label": r["danh_sach_sku_text"],
            "main_sku": r["sku_chinh"],
        },
        "sales_status": _sales_status(r),
        "calls": {
            "count": r["so_cuoc_goi"],
            "seconds": r["tong_giay_goi"],
            "has_transcript": r["co_transcript"],
            "ai_extracted": r["da_boc_ai"],
            "quality_score": _num(r["diem_chat_luong"]),
        },
        "synced_at": _ts(r["dong_bo_luc"]),
        "cursor": r["don_id"],
    }


def order_row(r: dict) -> dict:
    """Dòng của /leads/stats và /orders/stats (cùng shape, khác bộ lọc)."""
    status = r["trang_thai_don_ma"]
    return {
        **_common(r),
        "closed_at": _ts(r["ngay_chot"]),
        "closed_date": _day(r["ngay_chot_ngay"]),
        "order_status": {**_code_label(status, r["trang_thai_don_text"]), "bucket": d.order_bucket_of(status)},
        "invoice_status": _code_label(r["trang_thai_hoa_don_ma"], r["trang_thai_hoa_don_text"]),
        "is_valid_revenue": bool(r["la_don_gtc"]),
        "order_type": _code_label(r["loai_don_ma"], r["loai_don_text"]),
        "koc_type": _code_label(r["loai_don_koc_ma"], r["loai_don_koc_text"]),
        "delivery_method": _code_label(r["hinh_thuc_giao_ma"], r["hinh_thuc_giao_text"]),
        "payment_method": r["phuong_thuc_tt"],
        "branch_id": r["chi_nhanh_id"],
        "reason_not_closed": _code_label(r["ly_do_khong_chot_ma"], r["ly_do_khong_chot_text"]),
        "delivery_business_status": _code_label(r["trang_thai_giao_nghiep_vu_ma"], r["trang_thai_giao_nghiep_vu_text"]),
        "bounce_reason_group": _code_label(r["nhom_ly_do_bom_ma"], r["nhom_ly_do_bom_text"]),
        "customer_class": _code_label(r["phan_loai_khach_ma"], r["phan_loai_khach_text"]),
        "needs": {"codes": list(r["nhu_cau_ma"] or []), "label": r["nhu_cau_text"]},
        "age": {"label": r["tuoi_text"], "source": r["tuoi_nguon_text"]},
        "occupation": _code_label(r["nghe_nghiep_ma"], r["nghe_nghiep_text"]),
        "pain_level": _code_label(r["muc_do_dau_ma"], r["muc_do_dau_text"]),
    }


def open_lead_row(r: dict) -> dict:
    """Dòng của /pipeline: bản rút gọn + tuổi lead (ngày) tính đến as_of."""
    return {**_common(r), "age_days": r["tuoi"]}


def order_line_row(r: dict) -> dict:
    """Dòng của /revenue: 1 sản phẩm trong 1 đơn."""
    return {
        "line_id": r["dong_id"],
        "order_id": r["don_id"],
        "order_code": r["ma_don"],
        "created_date": _day(r["ngay_tao_ngay"]),
        "sku": r["sku"],
        "product_name": r["ten_sp"],
        "product_group": r["nhom_sp_text"],
        "quantity": r["so_luong"],
        "unit_price": _vnd(r["don_gia"]),
        "amount": _vnd(r["thanh_tien"]),
        "cursor": r["dong_id"],
    }
```

- [ ] **Step 4: Chạy test, phải pass**

Run: `$PYTEST apps/daily_report/tests/test_rows.py -q --reuse-db -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/apps/daily_report/application/rows.py backend/apps/daily_report/tests/test_rows.py
git commit -m "feat(daily-report): rows.py dịch dòng mart sang JSON cho Hermes

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `/leads/stats`, `/orders/stats`, `/revenue` trả danh sách

**Files:**
- Modify: `backend/apps/daily_report/application/stats_service.py`
- Modify: `backend/apps/daily_report/transport/views.py`
- Modify: `backend/apps/daily_report/transport/serializers.py`
- Modify: `backend/apps/daily_report/tests/helpers.py` (thêm `fetch_all`)
- Rewrite: `backend/apps/daily_report/tests/test_leads.py`, `backend/apps/daily_report/tests/test_orders_revenue.py`
- Delete: `backend/apps/daily_report/tests/test_cache.py`

**Interfaces:**
- Consumes: `paging.*`, `queries.order_rows`, `queries.order_line_rows`, `rows.order_row`, `rows.order_line_row`.
- Produces: `stats_service.page_envelope(items, *, d_from, d_to, fresh, limit, next_cursor) -> dict`; `stats_service.leads_payload(d_from, d_to, *, after, limit)`, `orders_payload(...)`, `revenue_payload(...)` cùng chữ ký; `views._paging(request, max_limit) -> tuple[int | None, int]`; serializer `OrderRowSerializer`, `OrderLineRowSerializer`, `page_envelope_of(row_serializer, *, name, cursor_field)`; `helpers.fetch_all(url, params, limit=3) -> list[dict]`.

- [ ] **Step 1: Thêm `fetch_all` vào `tests/helpers.py`** (cuối file)

```python


def fetch_all(url: str, params: dict, *, limit: int = 3) -> list[dict]:
    """Kéo hết trang với limit nhỏ để mọi test đều đi qua đường keyset."""
    items, cursor = [], None
    while True:
        q = {**params, "limit": str(limit)}
        if cursor is not None:
            q["cursor"] = str(cursor)
        resp = client().get(url, q)
        assert resp.status_code == 200, resp.content
        body = resp.json()
        items += body["data"]
        cursor = body["next_cursor"]
        if cursor is None:
            return items
```

- [ ] **Step 2: Viết lại `tests/test_leads.py`**

```python
from collections import Counter
from datetime import date

from django.test import TestCase, override_settings

from apps.daily_report.domain import definitions as d
from apps.daily_report.domain import paging
from apps.users.test_helpers import make_admin

from .helpers import TOKEN, client, fetch_all, rebuild, seed_order

URL = "/api/daily-report/leads/stats"
D = date(2026, 9, 2)
P = {"from": "2026-09-02", "to": "2026-09-02"}


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class LeadRowsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sale_a = make_admin("sale-a")
        cls.sale_a.last_name, cls.sale_a.first_name = "Nguyễn", "A"
        cls.sale_a.save(update_fields=["last_name", "first_name"])
        seed_order(D, sales_status="closed", amount=300_000, staff=cls.sale_a, order_source="landing")
        seed_order(D, sales_status="closed_scheduled_date", amount=200_000, staff=cls.sale_a, order_source="landing")
        seed_order(D, sales_status="no_answer_2", staff=cls.sale_a, order_source="messenger")
        seed_order(D, sales_status="unreachable", order_source="messenger")
        seed_order(D, sales_status="call_back_later")
        seed_order(D, sales_status="spam")
        seed_order(D, sales_status="underlying_condition")
        seed_order(D, sales_status="wants_store_visit")
        seed_order(D)  # chưa đánh giá, chưa gán
        seed_order(date(2026, 9, 1), sales_status="closed", amount=999_000)  # ngoài kỳ
        seed_order(D, sales_status="closed", order_source="foxia")  # mirror: mart loại
        rebuild()

    def test_envelope_meta_and_paging_keys(self):
        body = client().get(URL, {**P, "limit": "4"}).json()
        meta = body["meta"]
        self.assertEqual(meta["timezone"], "Asia/Ho_Chi_Minh")
        self.assertEqual(meta["range"], {"from": "2026-09-02", "to": "2026-09-02"})
        self.assertEqual(meta["data_status"], "Bình thường")
        self.assertTrue(meta["data_as_of"].endswith("+07:00"))
        self.assertEqual(meta["definitions_url"], "/api/daily-report/definitions")
        self.assertEqual(body["limit"], 4)
        self.assertEqual(len(body["data"]), 4)
        self.assertEqual(body["next_cursor"], body["data"][-1]["cursor"])

    def test_rows_recompose_old_totals(self):
        rows = fetch_all(URL, P)
        self.assertEqual(len(rows), 9)                                              # leads
        self.assertEqual(sum(1 for r in rows if r["staff"] is None), 6)             # unassigned
        buckets = Counter(r["sales_status"]["bucket"] for r in rows)
        self.assertEqual(buckets[None], 1)                                          # unassessed
        self.assertEqual(buckets["closed"], 2)
        self.assertEqual(buckets["calling"], 1)
        self.assertEqual(buckets["unreached"], 1)
        self.assertEqual(buckets["nurture"], 1)
        self.assertEqual(buckets["dirty"], 1)
        self.assertEqual(buckets["underlying_condition"], 1)
        self.assertEqual(buckets["wants_store_visit"], 1)
        # closed_revenue cũ: closed_scheduled_date KHÔNG tính
        closed_rev = sum(r["amounts"]["total"] for r in rows if r["sales_status"]["code"] in d.CLOSED_REVENUE_CODES)
        self.assertEqual(closed_rev, 300_000)
        # close_rate cũ = 2 / (9 − underlying_condition − wants_store_visit)
        denom = sum(1 for r in rows if r["sales_status"]["code"] not in d.CLOSE_RATE_EXCLUDED_CODES)
        self.assertAlmostEqual(buckets["closed"] / denom, round(2 / 7, 4), places=4)

    def test_row_fields(self):
        row = next(r for r in fetch_all(URL, P) if r["staff"] and r["source"]["code"] == "landing" and r["amounts"]["total"] == 300_000)
        self.assertEqual(row["staff"]["name"], "Nguyễn A")
        self.assertEqual(row["source"], {"code": "landing", "label": "Landing"})
        self.assertEqual(row["created_date"], "2026-09-02")
        self.assertTrue(row["created_at"].startswith("2026-09-02T10:00:00+07:00"))
        self.assertIn("phone_masked", row["customer"])
        self.assertNotIn("phone", row["customer"])
        self.assertIsNone(row["invoice_status"]["code"])

    def test_keyset_no_dup_and_sorted(self):
        ids = [r["id"] for r in fetch_all(URL, P, limit=2)]
        self.assertEqual(ids, sorted(set(ids)))
        self.assertEqual(len(ids), 9)

    def test_limit_clamped_to_max(self):
        body = client().get(URL, {**P, "limit": "99999"}).json()
        self.assertEqual(body["limit"], paging.ORDER_MAX_LIMIT)

    def test_empty_period_returns_empty_list_not_404(self):
        body = client().get(URL, {"from": "2025-01-01", "to": "2025-01-01"}).json()
        self.assertEqual(body["data"], [])
        self.assertIsNone(body["next_cursor"])

    def test_bad_params_are_400(self):
        self.assertEqual(client().get(URL, {"from": "2026-09-03", "to": "2026-09-01"}).status_code, 400)
        self.assertEqual(client().get(URL, {"from": "2026-01-01", "to": "2026-06-30"}).status_code, 400)
        self.assertEqual(client().get(URL, {**P, "limit": "0"}).status_code, 400)
        self.assertEqual(client().get(URL, {**P, "cursor": "x"}).status_code, 400)
        resp = client().get(URL, {**P, "group_by": "staff"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"], paging.GROUP_BY_GONE)
```

- [ ] **Step 3: Viết lại `tests/test_orders_revenue.py`**

```python
"""Khoá Q2 của spec: dòng của API cộng lại phải BẰNG OrderRepository.summary_overview cùng kỳ."""

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.orders.models import OrderItem
from apps.orders.repositories import OrderRepository

from .helpers import TOKEN, client, fetch_all, rebuild, seed_order

ORDERS_URL = "/api/daily-report/orders/stats"
REVENUE_URL = "/api/daily-report/revenue"
D = date(2026, 9, 2)
P = {"from": "2026-09-02", "to": "2026-09-02"}


def _money(s: str | None) -> int:
    return int(Decimal(s or "0"))


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class OrderRowsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fb = Channel.objects.create(code="facebook-page-t", name="FB T")
        web = Channel.objects.create(code="google-ads-web", name="Web")
        cls.delivered = seed_order(D, status=3, invoice_status=1, amount=500_000, channel=fb, sales_status="closed")
        seed_order(D, status=2, invoice_status=1, amount=200_000, sales_status="closed")   # awaiting
        seed_order(D, status=5, invoice_status=1, amount=150_000)                          # returned
        seed_order(D, status=10, invoice_status=1, amount=120_000)                         # return risk
        seed_order(D, status=99, invoice_status=3, amount=70_000)                          # mã lạ → awaiting
        seed_order(D, status=6, invoice_status=1, amount=999)                              # huỷ POS
        seed_order(D, status=3, invoice_status=2, amount=999)                              # huỷ web KV
        cls.lead_only = seed_order(D, status=3, invoice_status=None, amount=999, sales_status="closed")  # lead, chưa HĐ
        seed_order(D, status=3, invoice_status=1, amount=999, branch_id="1000000139")      # chi nhánh ẩn
        seed_order(D, status=3, invoice_status=1, amount=999, branch_id="1000000477")      # Đại Mỗ
        seed_order(D, status=3, invoice_status=1, amount=999, order_source="foxia")        # mirror
        original = seed_order(D, status=6, invoice_status=1, amount=999)
        seed_order(D, status=3, invoice_status=1, amount=80_000, restored_from=original)   # bản khôi phục sống
        seed_order(D, status=3, invoice_status=1, amount=60_000, invoice_code="HDSPE_777", channel=fb)  # shopee trên kênh FB
        seed_order(D, status=3, invoice_status=None, amount=45_000, channel=web, sales_status="closed")  # lead-only
        prod = Product.objects.create(name="Ghế MSC06", sku="MSC06")
        gel = Product.objects.create(name="Gel", sku="GEL01")
        OrderItem.objects.create(order=cls.delivered, product=prod, sku="MSC06", quantity=1, unit_price=400_000, line_total=400_000)
        OrderItem.objects.create(order=cls.delivered, product=gel, sku="GEL01", quantity=2, unit_price=50_000, line_total=100_000)
        OrderItem.objects.create(order=cls.lead_only, product=prod, sku="MSC06", quantity=1, unit_price=999, line_total=999)
        rebuild()

    def test_matches_summary_overview(self):
        rows = fetch_all(ORDERS_URL, P)
        ref = OrderRepository.summary_overview("2026-09-02", "2026-09-02")
        by_bucket = Counter(r["order_status"]["bucket"] for r in rows)
        money = defaultdict(int)
        for r in rows:
            money[r["order_status"]["bucket"]] += r["amounts"]["total"]
        self.assertEqual(len(rows), ref["total_orders"])
        self.assertEqual(by_bucket["delivered"], ref["delivered_orders"])
        self.assertEqual(by_bucket["awaiting"], ref["awaiting_orders"])
        self.assertEqual(by_bucket["returned"], ref["returned_orders"])
        self.assertEqual(by_bucket["return_risk"], ref["return_risk_orders"])
        self.assertEqual(sum(money.values()), _money(ref["total_revenue"]))
        self.assertEqual(money["delivered"], _money(ref["delivered_revenue"]))
        self.assertEqual(money["awaiting"], _money(ref["awaiting_revenue"]))
        self.assertEqual(money["returned"], _money(ref["returned_revenue"]))
        self.assertEqual(money["return_risk"], _money(ref["return_risk_revenue"]))
        # net_revenue cũ = total − returned
        self.assertEqual(sum(money.values()) - money["returned"], _money(ref["total_revenue"]) - _money(ref["returned_revenue"]))

    def test_expected_numbers(self):
        rows = fetch_all(ORDERS_URL, P)
        self.assertEqual(len(rows), 7)
        self.assertEqual(Counter(r["order_status"]["bucket"] for r in rows), {"delivered": 3, "awaiting": 2, "returned": 1, "return_risk": 1})
        self.assertEqual(sum(r["amounts"]["total"] for r in rows), 500_000 + 200_000 + 150_000 + 120_000 + 70_000 + 80_000 + 60_000)
        self.assertNotIn("cancelled", {r["order_status"]["bucket"] for r in rows})
        by_group = Counter(r["channel_group"] for r in rows)
        self.assertEqual(by_group["facebook"], 1)
        self.assertEqual(by_group["shopee"], 1)
        self.assertEqual(by_group["off_other"], 5)

    def test_revenue_lines_follow_orders_set(self):
        lines = fetch_all(REVENUE_URL, P, limit=1)
        self.assertEqual({l["order_id"] for l in lines}, {self.delivered.id})     # lead_only bị loại
        self.assertEqual(sorted(l["sku"] for l in lines), ["GEL01", "MSC06"])
        self.assertEqual(sum(l["amount"] for l in lines), 500_000)
        order = next(r for r in fetch_all(ORDERS_URL, P) if r["id"] == self.delivered.id)
        self.assertEqual(sum(l["amount"] for l in lines), order["amounts"]["total"])  # không ship/giảm → bằng
        self.assertEqual(order["items"]["line_count"], 2)
        self.assertEqual(sorted(order["items"]["skus"]), ["GEL01", "MSC06"])
        self.assertEqual(lines[0]["order_code"], order["code"])
        self.assertEqual(set(lines[0]), {"line_id", "order_id", "order_code", "created_date", "sku", "product_name", "product_group", "quantity", "unit_price", "amount", "cursor"})

    def test_group_by_gone(self):
        self.assertEqual(client().get(ORDERS_URL, {**P, "group_by": "staff"}).status_code, 400)
        self.assertEqual(client().get(REVENUE_URL, {**P, "group_by": "day"}).status_code, 400)
```

- [ ] **Step 4: Xoá `tests/test_cache.py`**

```bash
git rm backend/apps/daily_report/tests/test_cache.py
```

- [ ] **Step 5: Chạy test, phải fail**

Run: `$PYTEST apps/daily_report/tests/test_leads.py apps/daily_report/tests/test_orders_revenue.py -q --reuse-db -p no:cacheprovider`
Expected: FAIL — `data` là dict không có `next_cursor`, `group_by` vẫn 200…

- [ ] **Step 6: Sửa `application/stats_service.py`**

Thay toàn bộ phần từ `LEAD_GROUPS = ...` tới hết `revenue_payload` (giữ `envelope`, `cached`, `CACHE_TTL_SECONDS`, `DEFINITIONS_URL`) bằng:

```python
def page_envelope(items: list[dict], *, d_from: date, d_to: date, fresh: Freshness, limit: int, next_cursor) -> dict:
    body = envelope(items, d_from=d_from, d_to=d_to, fresh=fresh)
    body["limit"] = limit
    body["next_cursor"] = next_cursor
    return body


def _page(fetch: Callable[[int], list[dict]], mapper: Callable[[dict], dict], limit: int) -> tuple[list[dict], object]:
    """Fetch limit+1 → biết còn trang sau mà không count(*) toàn tập."""
    raw, has_more = paging.slice_page(fetch(limit + 1), limit)
    items = [mapper(r) for r in raw]
    return items, (items[-1]["cursor"] if has_more else None)


def leads_payload(d_from: date, d_to: date, *, after: int | None, limit: int) -> dict:
    fresh = read_freshness()
    items, nxt = _page(
        lambda n: queries.order_rows(d_from, d_to, invoiced_only=False, after=after, limit=n), rows.order_row, limit
    )
    return page_envelope(items, d_from=d_from, d_to=d_to, fresh=fresh, limit=limit, next_cursor=nxt)


def orders_payload(d_from: date, d_to: date, *, after: int | None, limit: int) -> dict:
    fresh = read_freshness()
    items, nxt = _page(
        lambda n: queries.order_rows(d_from, d_to, invoiced_only=True, after=after, limit=n), rows.order_row, limit
    )
    return page_envelope(items, d_from=d_from, d_to=d_to, fresh=fresh, limit=limit, next_cursor=nxt)


def revenue_payload(d_from: date, d_to: date, *, after: int | None, limit: int) -> dict:
    fresh = read_freshness()
    items, nxt = _page(
        lambda n: queries.order_line_rows(d_from, d_to, after=after, limit=n), rows.order_line_row, limit
    )
    return page_envelope(items, d_from=d_from, d_to=d_to, fresh=fresh, limit=limit, next_cursor=nxt)
```

Import thêm ở đầu file: `from apps.daily_report.application import rows` và `from apps.daily_report.domain import paging`. `daily_summary`/`_compare_block` còn gọi `leads_stats`, `orders_stats`, `revenue` — tạm để chúng lỗi import? KHÔNG: xoá luôn `_compare_block`, `daily_summary`, `summary_payload`, `TOP_STAFF`, `format_key`, `_key_tuple`, `_sort_key`, `_COUNT_KEYS`, `_MONEY_KEYS` ở task này (view `summary` sửa ở Task 6; tạm thời view `summary` gọi hàm không tồn tại sẽ lỗi import → sửa view `summary` NGAY trong Step 7 để trả 410).

- [ ] **Step 7: Sửa `transport/views.py`**

Thay `_group_param` bằng hai tham số OpenAPI và helper:

```python
_CURSOR = OpenApiParameter(
    "cursor", str,
    description="Giá trị `next_cursor` của trang trước (keyset). Bỏ trống để lấy trang đầu.",
)
_LIMIT = OpenApiParameter(
    "limit", int,
    description="Số dòng mỗi trang, mặc định 200. Vượt trần bị kẹp về trần, không lỗi.",
)


def _paging(request, max_limit: int) -> tuple[int | None, int]:
    """Từ chối group_by (API không còn số gộp), đọc cursor số + limit."""
    paging.reject_group_by(request.query_params)
    return paging.parse_int_cursor(request.query_params), paging.parse_limit(request.query_params, max_limit=max_limit)
```

Import: `from apps.daily_report.domain import paging, periods`. Ba view:

```python
@extend_schema(
    auth=[{"ApiKeyAuth": []}],
    tags=["daily-report"],
    summary="Từng lead (đơn nội bộ tạo trong kỳ, mọi trạng thái)",
    description="Mỗi dòng = 1 đơn. Đếm theo `sales_status.bucket` để ra các rổ lead; `bucket` null = chưa đánh giá.",
    parameters=[_FROM, _TO, _CURSOR, _LIMIT],
    responses={200: s.page_envelope_of(s.OrderRowSerializer, name="LeadRowsPage", cursor_field=serializers.IntegerField), **_ERRORS},
)
@daily_report_view
def leads_stats(request):
    try:
        d_from, d_to = periods.parse_range(request.query_params)
        after, limit = _paging(request, paging.ORDER_MAX_LIMIT)
    except ParamError as exc:
        return _bad_request(exc)
    return _run_payload(lambda: stats_service.leads_payload(d_from, d_to, after=after, limit=limit))


@extend_schema(
    auth=[{"ApiKeyAuth": []}],
    tags=["daily-report"],
    summary="Từng đơn có hoá đơn (tập total_orders của Báo cáo tổng)",
    description="Mỗi dòng = 1 đơn đã lên hoá đơn, không huỷ, loại chi nhánh ẩn. Đếm theo `order_status.bucket` để ra delivered/awaiting/returned/return_risk; Σ `amounts.total` = total_revenue.",
    parameters=[_FROM, _TO, _CURSOR, _LIMIT],
    responses={200: s.page_envelope_of(s.OrderRowSerializer, name="OrderRowsPage", cursor_field=serializers.IntegerField), **_ERRORS},
)
@daily_report_view
def orders_stats(request):
    try:
        d_from, d_to = periods.parse_range(request.query_params)
        after, limit = _paging(request, paging.ORDER_MAX_LIMIT)
    except ParamError as exc:
        return _bad_request(exc)
    return _run_payload(lambda: stats_service.orders_payload(d_from, d_to, after=after, limit=limit))


@extend_schema(
    auth=[{"ApiKeyAuth": []}],
    tags=["daily-report"],
    summary="Từng dòng hàng (sản phẩm trong đơn) của tập đơn có hoá đơn",
    description="Cùng bộ lọc với /orders/stats; nối `order_id`. Σ `amount` theo đơn có thể khác `amounts.total` bên /orders/stats vì phí ship/giảm giá cấp đơn.",
    parameters=[_FROM, _TO, _CURSOR, _LIMIT],
    responses={200: s.page_envelope_of(s.OrderLineRowSerializer, name="OrderLineRowsPage", cursor_field=serializers.IntegerField), **_ERRORS},
)
@daily_report_view
def revenue(request):
    try:
        d_from, d_to = periods.parse_range(request.query_params)
        after, limit = _paging(request, paging.LINE_MAX_LIMIT)
    except ParamError as exc:
        return _bad_request(exc)
    return _run_payload(lambda: stats_service.revenue_payload(d_from, d_to, after=after, limit=limit))
```

Thêm `from rest_framework import serializers` ở đầu views.py. View `summary` tạm sửa (bản chính thức ở Task 6):

```python
@extend_schema(exclude=True)
@daily_report_view
def summary(request):
    return Response({"detail": "Endpoint đã bỏ; ghép từ /leads/stats, /orders/stats, /pipeline."}, status=status.HTTP_410_GONE)
```

- [ ] **Step 8: Sửa `transport/serializers.py`**

Xoá `LeadMetricsSerializer` (+ vòng `for _bucket`), `OrderMetricsSerializer`, `RevenueMetricsSerializer`, `CompareRowSerializer` (+ dòng `_declared_fields["from"]`), `TopStaffSerializer`, `SummarySerializer`. Sửa `CodeLabelSerializer.code` thành `serializers.CharField(allow_null=True)`. Thêm sau `ChannelRefSerializer`:

```python
class ChannelSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField(allow_null=True)
    type = serializers.CharField(allow_null=True, help_text="Loại kênh (Facebook, Google…)")


class IntCodeLabelSerializer(serializers.Serializer):
    code = serializers.IntegerField(allow_null=True)
    label = serializers.CharField(allow_null=True)


class OrderStatusSerializer(IntCodeLabelSerializer):
    bucket = serializers.ChoiceField(
        choices=list(d.ORDER_BUCKET_LABELS), allow_null=True,
        help_text="Rổ Báo cáo tổng; cộng theo đây thay cho total/delivered/… cũ",
    )


class SalesStatusSerializer(CodeLabelSerializer):
    bucket = serializers.ChoiceField(
        choices=list(d.LEAD_BUCKETS) + ["other"], allow_null=True,
        help_text="Rổ lead; null = chưa đánh giá (unassessed cũ)",
    )


class CustomerSerializer(serializers.Serializer):
    id = serializers.IntegerField(allow_null=True)
    code = serializers.CharField(allow_null=True)
    name = serializers.CharField(allow_null=True)
    phone_masked = serializers.CharField(allow_null=True, help_text="SĐT đã che, vd 0912***678")
    province = serializers.CharField(allow_null=True)
    district = serializers.CharField(allow_null=True)
    is_new = serializers.BooleanField(allow_null=True)


class AmountsSerializer(serializers.Serializer):
    goods = serializers.IntegerField(allow_null=True, help_text="VND, tiền hàng")
    discount = serializers.IntegerField(allow_null=True)
    shipping = serializers.IntegerField(allow_null=True)
    total = serializers.IntegerField(allow_null=True, help_text="VND, tổng sau giảm — cột doanh thu")
    quantity = serializers.IntegerField(allow_null=True)


class ItemsSerializer(serializers.Serializer):
    line_count = serializers.IntegerField(allow_null=True)
    skus = serializers.ListField(child=serializers.CharField())
    skus_label = serializers.CharField(allow_null=True)
    main_sku = serializers.CharField(allow_null=True)


class NeedsSerializer(serializers.Serializer):
    codes = serializers.ListField(child=serializers.CharField())
    label = serializers.CharField(allow_null=True)


class AgeSerializer(serializers.Serializer):
    label = serializers.CharField(allow_null=True)
    source = serializers.CharField(allow_null=True)


class CallsSerializer(serializers.Serializer):
    count = serializers.IntegerField(allow_null=True)
    seconds = serializers.IntegerField(allow_null=True)
    has_transcript = serializers.BooleanField(allow_null=True)
    ai_extracted = serializers.BooleanField(allow_null=True)
    quality_score = serializers.FloatField(allow_null=True)


class OpenLeadRowSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField(allow_null=True)
    created_date = serializers.DateField(allow_null=True)
    source = CodeLabelSerializer()
    channel = ChannelSerializer(allow_null=True)
    channel_group = serializers.CharField(allow_null=True)
    staff = StaffRefSerializer(allow_null=True)
    customer = CustomerSerializer()
    amounts = AmountsSerializer()
    items = ItemsSerializer()
    sales_status = SalesStatusSerializer()
    calls = CallsSerializer()
    synced_at = serializers.DateTimeField(allow_null=True)
    age_days = serializers.IntegerField(help_text="as_of − created_date")
    cursor = serializers.IntegerField()


class OrderRowSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField(allow_null=True)
    created_date = serializers.DateField(allow_null=True)
    closed_at = serializers.DateTimeField(allow_null=True)
    closed_date = serializers.DateField(allow_null=True)
    order_status = OrderStatusSerializer()
    invoice_status = IntCodeLabelSerializer()
    is_valid_revenue = serializers.BooleanField(help_text="la_don_gtc — cờ duy nhất hợp lệ để tính doanh thu")
    order_type = CodeLabelSerializer()
    koc_type = CodeLabelSerializer()
    source = CodeLabelSerializer()
    delivery_method = CodeLabelSerializer()
    payment_method = serializers.CharField(allow_null=True)
    channel = ChannelSerializer(allow_null=True, help_text="Kênh = Digital, không phải fanpage")
    channel_group = serializers.CharField(allow_null=True)
    branch_id = serializers.CharField(allow_null=True)
    staff = StaffRefSerializer(allow_null=True)
    customer = CustomerSerializer()
    amounts = AmountsSerializer()
    items = ItemsSerializer()
    sales_status = SalesStatusSerializer()
    reason_not_closed = CodeLabelSerializer()
    delivery_business_status = CodeLabelSerializer()
    bounce_reason_group = CodeLabelSerializer()
    customer_class = CodeLabelSerializer()
    needs = NeedsSerializer()
    age = AgeSerializer()
    occupation = CodeLabelSerializer()
    pain_level = CodeLabelSerializer()
    calls = CallsSerializer()
    synced_at = serializers.DateTimeField(allow_null=True)
    cursor = serializers.IntegerField()


class OrderLineRowSerializer(serializers.Serializer):
    line_id = serializers.IntegerField()
    order_id = serializers.IntegerField(help_text="Nối với `id` bên /orders/stats")
    order_code = serializers.CharField(allow_null=True)
    created_date = serializers.DateField(allow_null=True)
    sku = serializers.CharField(allow_null=True)
    product_name = serializers.CharField(allow_null=True)
    product_group = serializers.CharField(allow_null=True)
    quantity = serializers.IntegerField(allow_null=True)
    unit_price = serializers.IntegerField(allow_null=True)
    amount = serializers.IntegerField(allow_null=True, help_text="VND, thành tiền dòng")
    cursor = serializers.IntegerField()
```

Và cuối file, cạnh `envelope_of`:

```python
def page_envelope_of(row_serializer, *, name: str, cursor_field):
    """Envelope trang {meta, data[], limit, next_cursor}. ``cursor_field`` là lớp Field
    (IntegerField cho id, CharField cho cursor tuple của /ads)."""
    return type(
        name,
        (serializers.Serializer,),
        {
            "meta": MetaSerializer(),
            "data": row_serializer(many=True),
            "limit": serializers.IntegerField(),
            "next_cursor": cursor_field(allow_null=True, help_text="null = hết"),
        },
    )
```

- [ ] **Step 9: Chạy test target, phải pass**

Run: `$PYTEST apps/daily_report/tests/test_leads.py apps/daily_report/tests/test_orders_revenue.py apps/daily_report/tests/test_rows.py -q --reuse-db -p no:cacheprovider`
Expected: PASS. (`test_pipeline`, `test_summary`, `test_ads`, `test_payload_shapes`, `test_openapi` còn đỏ — xử lý ở task sau; KHÔNG chạy cả thư mục để kết luận ở bước này.)

- [ ] **Step 10: Commit**

```bash
git add -A backend/apps/daily_report
git commit -m "feat(daily-report): leads/orders/revenue trả từng dòng có phân trang keyset

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `/pipeline` trả từng lead mở

**Files:**
- Modify: `backend/apps/daily_report/application/stats_service.py` (`pipeline`, `pipeline_payload`)
- Modify: `backend/apps/daily_report/transport/views.py` (view `pipeline`)
- Rewrite: `backend/apps/daily_report/tests/test_pipeline.py`

**Interfaces:**
- Produces: `stats_service.pipeline_payload(as_of, lookback_days, *, after, limit)`; giữ `stats_service.DEFAULT_LOOKBACK_DAYS = 30`.

- [ ] **Step 1: Viết lại `tests/test_pipeline.py`**

```python
from collections import Counter
from datetime import date

from django.test import TestCase, override_settings

from apps.users.test_helpers import make_admin

from .helpers import TOKEN, client, fetch_all, rebuild, seed_order

URL = "/api/daily-report/pipeline"
P = {"as_of": "2026-09-02"}


def _age_bucket(days: int) -> str:
    if days <= 1:
        return "0-1d"
    if days <= 3:
        return "2-3d"
    if days <= 7:
        return "4-7d"
    return "8d+"


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class PipelineRowsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sale = make_admin("sale-p")
        seed_order(date(2026, 9, 2), sales_status="call_back_later", staff=sale)   # tuổi 0
        seed_order(date(2026, 8, 31), sales_status="no_answer_3", staff=sale)     # tuổi 2
        seed_order(date(2026, 8, 27), sales_status=None)                           # tuổi 6, chưa gán, chưa đánh giá
        seed_order(date(2026, 8, 20), sales_status="scheduled")                    # tuổi 13
        seed_order(date(2026, 9, 1), sales_status="closed")                        # kết thúc: không tính
        seed_order(date(2026, 9, 1), sales_status="spam")                          # kết thúc
        seed_order(date(2026, 9, 1), sales_status="call_back_later", invoice_status=1)  # đã lên HĐ
        seed_order(date(2026, 9, 1), sales_status="call_back_later", status=6)     # huỷ
        seed_order(date(2026, 7, 1), sales_status="call_back_later")               # ngoài lookback 30
        seed_order(date(2026, 9, 3), sales_status="call_back_later")               # sau as_of
        rebuild()

    def test_rows_recompose_old_snapshot(self):
        body = client().get(URL, {**P, "limit": "100"}).json()
        self.assertEqual(body["meta"]["range"], {"from": "2026-08-03", "to": "2026-09-02"})
        rows = body["data"]
        self.assertEqual(len(rows), 4)                                                  # open_leads
        self.assertEqual(sum(1 for r in rows if r["staff"] is None), 2)                 # unassigned
        self.assertEqual(sum(1 for r in rows if r["sales_status"]["code"] is None), 1)  # unassessed
        by_status = Counter(r["sales_status"]["code"] for r in rows)
        self.assertEqual(by_status["call_back_later"], 1)
        self.assertEqual(by_status[None], 1)
        labels = {r["sales_status"]["code"]: r["sales_status"]["label"] for r in rows}
        self.assertEqual(labels["call_back_later"], "Gọi lại sau")
        self.assertEqual(sorted(r["age_days"] for r in rows), [0, 2, 6, 13])
        self.assertEqual(Counter(_age_bucket(r["age_days"]) for r in rows), {"0-1d": 1, "2-3d": 1, "4-7d": 1, "8d+": 1})
        self.assertNotIn("order_status", rows[0])

    def test_keyset(self):
        rows = fetch_all(URL, P, limit=2)
        ids = [r["id"] for r in rows]
        self.assertEqual(ids, sorted(set(ids)))
        self.assertEqual(len(ids), 4)

    def test_lookback_param(self):
        rows = fetch_all(URL, {**P, "lookback_days": "5"})
        self.assertEqual(len(rows), 2)
        self.assertEqual(client().get(URL, {"lookback_days": "0"}).status_code, 400)
        self.assertEqual(client().get(URL, {"lookback_days": "93"}).status_code, 400)
        self.assertEqual(client().get(URL, {**P, "group_by": "staff"}).status_code, 400)
```

- [ ] **Step 2: Chạy test, phải fail**

Run: `$PYTEST apps/daily_report/tests/test_pipeline.py -q --reuse-db -p no:cacheprovider`
Expected: FAIL — `data` là dict

- [ ] **Step 3: Sửa `stats_service`**

Xoá `AGE_BUCKETS`, `_age_bucket`, hàm `pipeline` (dạng gộp). Thay `pipeline_payload`:

```python
DEFAULT_LOOKBACK_DAYS = 30


def pipeline_payload(as_of: date, lookback_days: int, *, after: int | None, limit: int) -> dict:
    fresh = read_freshness()
    items, nxt = _page(
        lambda n: queries.open_lead_rows(as_of, lookback_days, after=after, limit=n), rows.open_lead_row, limit
    )
    return page_envelope(
        items, d_from=as_of - timedelta(days=lookback_days), d_to=as_of, fresh=fresh, limit=limit, next_cursor=nxt
    )
```

Xoá import `Counter`, `defaultdict` nếu không còn dùng.

- [ ] **Step 4: Sửa view `pipeline`**

```python
@extend_schema(
    auth=[{"ApiKeyAuth": []}],
    tags=["daily-report"],
    summary="Từng lead còn mở tính đến as_of",
    description="Lead tạo trong lookback_days ngày, chưa lên hoá đơn, chưa huỷ, trạng thái sale chưa kết thúc. `age_days` = as_of − ngày tạo; rổ tuổi cũ: 0-1d / 2-3d / 4-7d / 8d+.",
    parameters=[
        OpenApiParameter("as_of", str, description="YYYY-MM-DD, mặc định hôm nay giờ VN"),
        OpenApiParameter("lookback_days", int, description="1..92, mặc định 30"),
        _CURSOR, _LIMIT,
    ],
    responses={200: s.page_envelope_of(s.OpenLeadRowSerializer, name="OpenLeadRowsPage", cursor_field=serializers.IntegerField), **_ERRORS},
)
@daily_report_view
def pipeline(request):
    try:
        as_of = periods.parse_date_param(request.query_params, "as_of", default=periods.today_vn())
        lookback = periods.parse_int(
            request.query_params, "lookback_days",
            default=stats_service.DEFAULT_LOOKBACK_DAYS, minimum=1, maximum=periods.MAX_RANGE_DAYS,
        )
        after, limit = _paging(request, paging.ORDER_MAX_LIMIT)
    except ParamError as exc:
        return _bad_request(exc)
    return _run_payload(lambda: stats_service.pipeline_payload(as_of, lookback, after=after, limit=limit))
```

- [ ] **Step 5: Chạy test, phải pass**

Run: `$PYTEST apps/daily_report/tests/test_pipeline.py -q --reuse-db -p no:cacheprovider`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/apps/daily_report
git commit -m "feat(daily-report): /pipeline trả từng lead mở kèm age_days

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `/summary` → 410 Gone

**Files:**
- Modify: `backend/apps/daily_report/transport/views.py` (view `summary` — bản chính thức, có trong OpenAPI)
- Rewrite: `backend/apps/daily_report/tests/test_summary.py`
- Modify: `backend/apps/daily_report/tests/test_openapi.py` (summary có 410, không 200)

- [ ] **Step 1: Viết lại `tests/test_summary.py`**

```python
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .helpers import TOKEN, client

URL = "/api/daily-report/summary"


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class SummaryGoneTests(TestCase):
    def test_410_points_to_replacements(self):
        resp = client().get(URL, {"date": "2026-09-02"})
        self.assertEqual(resp.status_code, 410)
        detail = resp.json()["detail"]
        for path in ("/leads/stats", "/orders/stats", "/pipeline"):
            self.assertIn(path, detail)

    def test_still_requires_token(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)
```

- [ ] **Step 2: Sửa `test_openapi.py::test_all_eight_endpoints_documented_with_api_key`**

Thay vòng `for path, ops in schema["paths"].items():` bằng:

```python
        for path, ops in schema["paths"].items():
            self.assertEqual(ops["get"]["security"], [{"ApiKeyAuth": []}], path)
            expected = "410" if path == "/summary" else "200"
            self.assertIn(expected, ops["get"]["responses"], path)
```

- [ ] **Step 3: Chạy test, phải fail**

Run: `$PYTEST apps/daily_report/tests/test_summary.py apps/daily_report/tests/test_openapi.py::OpenApiTests::test_all_eight_endpoints_documented_with_api_key -q --reuse-db -p no:cacheprovider`
Expected: `test_summary` PASS (đã tạm sửa ở Task 4) nhưng `test_openapi` FAIL vì `/summary` bị `exclude=True`.

- [ ] **Step 4: View `summary` chính thức**

```python
SUMMARY_GONE = "Endpoint đã bỏ (16/09/2026): API trả từng dòng, không còn gói số gộp. Ghép từ /leads/stats, /orders/stats, /pipeline."


@extend_schema(
    auth=[{"ApiKeyAuth": []}],
    tags=["daily-report"],
    summary="[Đã bỏ] Gói báo cáo ngày — luôn 410",
    description=SUMMARY_GONE,
    responses={410: OpenApiResponse(s.ErrorSerializer, description=SUMMARY_GONE), 401: _ERRORS[401]},
)
@daily_report_view
def summary(request):
    return Response({"detail": SUMMARY_GONE}, status=status.HTTP_410_GONE)
```

- [ ] **Step 5: Chạy lại, phải pass**

Run: `$PYTEST apps/daily_report/tests/test_summary.py apps/daily_report/tests/test_openapi.py::OpenApiTests::test_all_eight_endpoints_documented_with_api_key -q --reuse-db -p no:cacheprovider`
Expected: PASS (`test_committed_file_matches_code` vẫn đỏ tới Task 9)

- [ ] **Step 6: Commit**

```bash
git add backend/apps/daily_report
git commit -m "feat(daily-report): /summary trả 410 chỉ sang các endpoint dòng

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `/ads` trả dòng chiến dịch + dòng dư

**Files:**
- Modify: `backend/apps/daily_report/infrastructure/ads_spend.py` (+ `AccountRef`, `account_directory`)
- Rewrite phần lớn: `backend/apps/daily_report/application/ads_service.py`
- Modify: `backend/apps/daily_report/transport/views.py` (view `ads`), `transport/serializers.py` (+ `AdSpendRowSerializer`, `MetaSerializer.spend_as_of`)
- Modify: `backend/apps/daily_report/infrastructure/queries.py` — xoá `leads_rows`, `orders_rows`, `_group_clauses`, `_bucket_select`, `GROUP_COLUMNS` (không còn ai dùng)
- Rewrite: `backend/apps/daily_report/tests/test_ads.py`

**Interfaces:**
- Produces: `ads_spend.AccountRef(platform, account_id, name, channels)`, `ads_spend.account_directory() -> list[AccountRef]`; `ads_service.parse_cursor(raw: str | None) -> tuple[str, str, str, str] | None`, `ads_service.ad_spend_rows(d_from, d_to) -> tuple[list[dict], dict[str, datetime | None]]`, `ads_service.ads_payload(d_from, d_to, *, after, limit) -> dict`; `ads_service.CURSOR_SEP = "|"`.

- [ ] **Step 1: Viết lại `tests/test_ads.py`** — GIỮ NGUYÊN toàn bộ `setUpTestData` (fixture TKQC/chiến dịch/đơn), docstring đầu file và import; thay phần test từ `def setUp` trở xuống:

```python
    def setUp(self):
        cache.clear()

    def _rows(self, **params):
        return fetch_all(URL, {"from": DAY.isoformat(), "to": DAY.isoformat(), **params}, limit=2)

    def test_rows_sorted_with_remainder_first_in_each_account(self):
        rows = self._rows()
        self.assertEqual(
            [(r["platform"], r["account_id"], r["campaign_id"]) for r in rows],
            [
                ("facebook", "111", None), ("facebook", "111", "c1"),
                ("facebook", "222", None), ("facebook", "222", "c2"),
                ("google", "9999", None), ("google", "9999", "g1"),
            ],
        )
        self.assertEqual([r["is_remainder"] for r in rows], [True, False, True, False, True, False])
        self.assertEqual(rows[0]["cursor"], f"{DAY.isoformat()}|facebook|111|")
        self.assertEqual(rows[1]["cursor"], f"{DAY.isoformat()}|facebook|111|c1")

    def test_campaign_rows_match_old_by_campaign(self):
        by_id = {r["campaign_id"]: r for r in self._rows() if r["campaign_id"]}
        self.assertEqual(
            by_id["c1"],
            {
                "day": DAY.isoformat(), "platform": "facebook",
                "account_id": "111", "account_name": "TK 111", "channels": ["fb-x"],
                "campaign_id": "c1", "campaign_name": "MSC06 - Tết", "sku": "MSC06", "is_remainder": False,
                "spend": 70_000, "multiplier": 1.1, "spend_adjusted": 77_000,
                "impressions": 700, "clicks": 7, "cursor": f"{DAY.isoformat()}|facebook|111|c1",
            },
        )
        # Hai bản sao MCC cùng thấy g1 → tính MỘT lần (20.000 × 1.08).
        self.assertEqual((by_id["g1"]["spend"], by_id["g1"]["spend_adjusted"], by_id["g1"]["impressions"], by_id["g1"]["clicks"]), (20_000, 21_600, 200, 10))
        self.assertIsNone(by_id["g1"]["sku"])
        self.assertEqual(by_id["g1"]["channels"], ["gg-x"])
        self.assertEqual((by_id["c2"]["spend_adjusted"], by_id["c2"]["sku"], by_id["c2"]["channels"]), (11_000, None, []))

    def test_remainder_rows_make_account_totals_match_old_by_account(self):
        rows = self._rows()
        per_account = {}
        for r in rows:
            per_account[(r["platform"], r["account_id"])] = per_account.get((r["platform"], r["account_id"]), 0) + r["spend_adjusted"]
        # by_account cũ: 111 → 110.000 (Max bản sao × 1.1), 222 → 33.000, 9999 → 54.000
        self.assertEqual(per_account, {("facebook", "111"): 110_000, ("facebook", "222"): 33_000, ("google", "9999"): 54_000})
        remainders = {r["account_id"]: r for r in rows if r["is_remainder"]}
        self.assertEqual((remainders["111"]["spend"], remainders["111"]["impressions"], remainders["111"]["clicks"]), (30_000, 300, 3))
        self.assertEqual((remainders["9999"]["spend"], remainders["9999"]["impressions"], remainders["9999"]["clicks"]), (30_000, 300, 15))
        self.assertIsNone(remainders["111"]["campaign_name"])
        # Σ dòng có campaign_id = by_campaign cũ; Σ mọi dòng − Σ chiến dịch = cost_outside_campaigns cũ (87.400)
        campaign_total = sum(r["spend_adjusted"] for r in rows if not r["is_remainder"])
        self.assertEqual(campaign_total, 77_000 + 11_000 + 21_600)
        self.assertEqual(sum(per_account.values()) - campaign_total, 87_400)

    def test_negative_remainder_is_returned_not_clamped(self):
        # Chiến dịch tiêu nhiều hơn TKQC ghi nhận (sync lệch nhịp) → dòng dư âm, nói ra không giấu.
        FacebookAdDailySpend.objects.filter(ad__fb_ad_id="ad3").update(spend=Decimal("50000"))
        rem = next(r for r in self._rows() if r["is_remainder"] and r["account_id"] == "222")
        self.assertEqual(rem["spend"], -20_000)
        self.assertEqual(rem["spend_adjusted"], -22_000)

    def test_multiplier_by_period_start_and_meta_spend_as_of(self):
        body = client().get(URL, {"from": DAY.isoformat(), "to": DAY.isoformat()}).json()
        self.assertEqual({r["multiplier"] for r in body["data"] if r["platform"] == "facebook"}, {1.1})
        self.assertEqual({r["multiplier"] for r in body["data"] if r["platform"] == "google"}, {1.08})
        self.assertIn("facebook", body["meta"]["spend_as_of"])
        self.assertIn("google", body["meta"]["spend_as_of"])

    def test_cursor_walk_and_bad_cursor(self):
        first = client().get(URL, {"from": DAY.isoformat(), "to": DAY.isoformat(), "limit": "4"}).json()
        self.assertEqual(len(first["data"]), 4)
        self.assertEqual(first["next_cursor"], first["data"][-1]["cursor"])
        rest = client().get(URL, {"from": DAY.isoformat(), "to": DAY.isoformat(), "cursor": first["next_cursor"]}).json()
        self.assertEqual(len(rest["data"]), 2)
        self.assertIsNone(rest["next_cursor"])
        self.assertEqual(client().get(URL, {"from": DAY.isoformat(), "to": DAY.isoformat(), "cursor": "bad"}).status_code, 400)

    def test_empty_period(self):
        body = client().get(URL, {"from": "2026-08-01", "to": "2026-08-01"}).json()
        self.assertEqual(body["data"], [])
        self.assertIsNone(body["next_cursor"])

    def test_group_by_gone(self):
        self.assertEqual(client().get(URL, {"group_by": "day"}).status_code, 400)

    def test_requires_token(self):
        from rest_framework.test import APIClient

        self.assertEqual(APIClient().get(URL).status_code, 401)
```

Sửa import: thêm `fetch_all` vào `from .helpers import TOKEN, client, seed_order` → `from .helpers import TOKEN, client, fetch_all, seed_order`; xoá `from apps.orders.repositories import OrderRepository` (không còn dùng). Cập nhật docstring đầu file thành:

```python
"""GET /ads — từng dòng chi tiêu (ngày × nền tảng × TKQC × chiến dịch) + dòng dư mỗi TKQC.

Ba bất biến khoá ở đây:
* Σ spend_adjusted theo TKQC (mọi dòng) = by_account.cost cũ — bản sao tài khoản
  (cùng account_id, khác app) chỉ tính MỘT lần (max), cùng luật ``AdsSpendPort.total_spend``.
* Σ spend_adjusted dòng có campaign_id = by_campaign.cost cũ (FB cộng từ ad, Google Max bản sao MCC).
* Dòng dư = TKQC − Σ chiến dịch, có thể ÂM — nói ra, không giấu (cost_outside_campaigns cũ).
"""
```

- [ ] **Step 2: Chạy test, phải fail**

Run: `$PYTEST apps/daily_report/tests/test_ads.py -q --reuse-db -p no:cacheprovider`
Expected: FAIL

- [ ] **Step 3: `ads_spend.account_directory`** — thêm cuối `infrastructure/ads_spend.py`:

```python


@dataclass(frozen=True)
class AccountRef:
    platform: str
    account_id: str
    name: str
    channels: tuple[str, ...]


def account_directory() -> list[AccountRef]:
    """Mọi TKQC hiện có (không theo kỳ) kèm mã kênh đã map — bản đồ TKQC → Digital."""
    out: list[AccountRef] = []
    sources = (
        (FACEBOOK, ChannelAdAccount.Provider.FACEBOOK, FacebookAdAccount.objects.all(), "account_id"),
        (GOOGLE, ChannelAdAccount.Provider.GOOGLE, GoogleAdsAccount.objects.all(), "customer_id"),
    )
    for platform, provider, qs, external_field in sources:
        names: dict[str, str] = {}
        pk_to_external: dict[int, str] = {}
        for pk, ext, name in qs.order_by("id").values_list("id", external_field, "name"):
            names.setdefault(ext, name or "")
            pk_to_external[pk] = ext
        channels = _channels_by_external(provider, pk_to_external)
        out += [AccountRef(platform, ext, name, channels.get(ext, ())) for ext, name in names.items()]
    return sorted(out, key=lambda a: (a.platform, a.account_id))
```

- [ ] **Step 4: Viết lại `application/ads_service.py`**

Giữ docstring module (cập nhật câu đầu), giữ `_vnd`, `_multipliers`. Xoá `_pct`, `_per`, `_channel_block`, `_account_block`, `_campaign_block`, `_block`, `ads_stats`, `ADS_GROUPS`, import `OrderRepository`, `defaultdict`. Thêm:

```python
CURSOR_SEP = "|"
_BAD_CURSOR = "Tham số `cursor` không hợp lệ — dùng `next_cursor` của trang trước."

RowKey = tuple[str, str, str, str]  # (day ISO, platform, account_id, campaign_id hoặc "")


def _key(day: date, platform: str, account_id: str, campaign_id: str | None) -> RowKey:
    return (iso(day), platform, account_id, campaign_id or "")


def parse_cursor(raw: str | None) -> RowKey | None:
    if not raw:
        return None
    parts = raw.split(CURSOR_SEP)
    if len(parts) != 4:
        raise ParamError(_BAD_CURSOR)
    return (parts[0], parts[1], parts[2], parts[3])


def _row(
    key: RowKey, ref: ads_spend.AccountRef | None, *, campaign_name: str | None, sku: str | None,
    is_remainder: bool, spend: Decimal, impressions: int, clicks: int, multiplier: Decimal,
) -> dict:
    day, platform, account_id, campaign_id = key
    return {
        "day": day,
        "platform": platform,
        "account_id": account_id,
        "account_name": ref.name if ref else "",
        "channels": list(ref.channels) if ref else [],
        "campaign_id": campaign_id or None,
        "campaign_name": campaign_name,
        "sku": sku,
        "is_remainder": is_remainder,
        "spend": _vnd(spend),
        "multiplier": float(multiplier),
        "spend_adjusted": _vnd(spend * multiplier),
        "impressions": impressions,
        "clicks": clicks,
        "cursor": CURSOR_SEP.join(key),
    }


def ad_spend_rows(d_from: date, d_to: date) -> tuple[list[dict], dict[str, datetime | None]]:
    """Mọi dòng của kỳ, đã sắp theo key. Mỗi (ngày × nền tảng × TKQC) có đúng MỘT dòng dư
    = chi tiêu TKQC (grain tài khoản, Max bản sao) − Σ chi tiêu chiến dịch; có thể âm."""
    multipliers = _multipliers(d_from)
    refs = {(a.platform, a.account_id): a for a in ads_spend.account_directory()}
    spend = ads_spend.account_spend(d_from, d_to)
    campaigns = ads_spend.campaign_spend(d_from, d_to)

    account_level: dict[tuple[str, str, str], ads_spend.AccountDay] = {
        (iso(r.day), r.platform, r.account_id): r for r in spend.rows
    }
    used: dict[tuple[str, str, str], list[Decimal | int]] = {}  # → [spend, impressions, clicks]
    rows: list[dict] = []
    for c in campaigns:
        group = (iso(c.day), c.platform, c.account_id)
        acc = used.setdefault(group, [Decimal("0"), 0, 0])
        acc[0] += c.spend
        acc[1] += c.impressions
        acc[2] += c.clicks
        rows.append(
            _row(
                _key(c.day, c.platform, c.account_id, c.campaign_id), refs.get((c.platform, c.account_id)),
                campaign_name=c.name, sku=c.sku, is_remainder=False,
                spend=c.spend, impressions=c.impressions, clicks=c.clicks, multiplier=multipliers[c.platform],
            )
        )
    for group in set(account_level) | set(used):
        day, platform, account_id = group
        a = account_level.get(group)
        u = used.get(group, [Decimal("0"), 0, 0])
        rows.append(
            _row(
                (day, platform, account_id, ""), refs.get((platform, account_id)),
                campaign_name=None, sku=None, is_remainder=True,
                spend=(a.spend if a else Decimal("0")) - u[0],
                impressions=(a.impressions if a else 0) - int(u[1]),
                clicks=(a.clicks if a else 0) - int(u[2]),
                multiplier=multipliers[platform],
            )
        )
    rows.sort(key=_row_key)
    return rows, spend.synced_at


def _row_key(r: dict) -> RowKey:
    return (r["day"], r["platform"], r["account_id"], r["campaign_id"] or "")


def ads_payload(d_from: date, d_to: date, *, after: RowKey | None, limit: int) -> dict:
    fresh = read_freshness()
    all_rows, synced_at = ad_spend_rows(d_from, d_to)
    if after is not None:
        all_rows = [r for r in all_rows if _row_key(r) > after]
    page, has_more = paging.slice_page(all_rows[: limit + 1], limit)
    body = page_envelope(
        page, d_from=d_from, d_to=d_to, fresh=fresh, limit=limit,
        next_cursor=page[-1]["cursor"] if has_more else None,
    )
    body["meta"]["spend_as_of"] = {k: (v.astimezone(TZ).isoformat() if v else None) for k, v in synced_at.items()}
    return body
```

Import cần: `from datetime import date, datetime`, `from decimal import Decimal`, `from apps.daily_report.application.stats_service import page_envelope, read_freshness` (hoặc `from apps.daily_report.infrastructure.freshness import read_freshness`), `from apps.daily_report.domain import paging`, `from apps.daily_report.domain.periods import TZ, ParamError, iso`, `from apps.daily_report.infrastructure import ads_spend`, giữ `facebook_ads_cost_multiplier`, `GOOGLE_ADS_DAILY_COST_MULTIPLIER`.

- [ ] **Step 5: View `ads` + serializer**

`serializers.py`: thêm vào `MetaSerializer`:

```python
    spend_as_of = serializers.DictField(
        child=serializers.DateTimeField(allow_null=True), required=False,
        help_text="Chỉ /ads: mốc sync chi tiêu mỗi nền tảng",
    )
```

Xoá `AdsTotalSerializer`, `AdsChannelSerializer`, `AdsAccountSerializer`, `AdsCampaignSerializer`, `AdsSerializer`, `ChannelRefSerializer` (không còn ai dùng). Thêm:

```python
class AdSpendRowSerializer(serializers.Serializer):
    day = serializers.DateField()
    platform = serializers.ChoiceField(choices=["facebook", "google"])
    account_id = serializers.CharField(help_text="Id TKQC bên nền tảng")
    account_name = serializers.CharField(allow_blank=True)
    channels = serializers.ListField(child=serializers.CharField(), help_text="Mã kênh (Digital) TKQC được gắn; rỗng = chưa map")
    campaign_id = serializers.CharField(allow_null=True, help_text="null = dòng dư của TKQC (is_remainder)")
    campaign_name = serializers.CharField(allow_null=True, allow_blank=True)
    sku = serializers.CharField(allow_null=True, help_text="SKU đã gán cho chiến dịch; null = chưa gán")
    is_remainder = serializers.BooleanField(help_text="true = chi tiêu TKQC − Σ chiến dịch trong ngày; có thể âm")
    spend = serializers.IntegerField(help_text="VND, CHƯA nhân hệ số")
    multiplier = serializers.FloatField(help_text="Hệ số VAT/phụ phí chọn theo `from` của kỳ (xem /definitions.cost_multiplier)")
    spend_adjusted = serializers.IntegerField(help_text="round(spend × multiplier)")
    impressions = serializers.IntegerField()
    clicks = serializers.IntegerField()
    cursor = serializers.CharField(help_text="`day|platform|account_id|campaign_id`")
```

`views.py` view `ads`:

```python
@extend_schema(
    auth=[{"ApiKeyAuth": []}],
    tags=["daily-report"],
    summary="Từng dòng chi tiêu quảng cáo: ngày × nền tảng × TKQC × chiến dịch, kèm dòng dư mỗi TKQC",
    description=(
        "Σ `spend_adjusted` mọi dòng theo (platform, account_id) = chi tiêu TKQC (bản sao gộp Max). "
        "Dòng `is_remainder` = TKQC − Σ chiến dịch, có thể âm khi sync lệch nhịp — nêu ra, đừng cộng vào chiến dịch. "
        "Muốn theo Digital: nối `channels` với `channel` bên /orders/stats. Không có ad set / ad riêng lẻ. "
        "Mốc sync mỗi nền tảng ở `meta.spend_as_of`."
    ),
    parameters=[_FROM, _TO, _CURSOR, _LIMIT],
    responses={200: s.page_envelope_of(s.AdSpendRowSerializer, name="AdSpendRowsPage", cursor_field=serializers.CharField), **_ERRORS},
)
@daily_report_view
def ads(request):
    try:
        d_from, d_to = periods.parse_range(request.query_params)
        paging.reject_group_by(request.query_params)
        after = ads_service.parse_cursor(request.query_params.get("cursor"))
        limit = paging.parse_limit(request.query_params, max_limit=paging.ADS_MAX_LIMIT)
    except ParamError as exc:
        return _bad_request(exc)
    return _run_payload(lambda: ads_service.ads_payload(d_from, d_to, after=after, limit=limit))
```

- [ ] **Step 6: Dọn `queries.py`** — xoá `GROUP_COLUMNS`, `_group_clauses`, `_bucket_select`, `leads_rows`, `orders_rows`. Xoá `cached` trong `stats_service.py` nếu không còn ai gọi (definitions dùng `cache.get_or_set`, không dùng `cached`) — kiểm tra bằng `grep -rn "cached(" apps/daily_report`.

- [ ] **Step 7: Chạy test, phải pass**

Run: `$PYTEST apps/daily_report/tests/test_ads.py apps/daily_report/tests/test_queries_rows.py -q --reuse-db -p no:cacheprovider`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/apps/daily_report
git commit -m "feat(daily-report): /ads trả từng dòng chiến dịch + dòng dư mỗi TKQC, cursor tuple

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `/definitions` mở rộng + glossary viết lại

**Files:**
- Modify: `backend/apps/daily_report/domain/definitions.py` (`cost_multiplier_rules`, `METRIC_GLOSSARY`)
- Modify: `backend/apps/daily_report/application/stats_service.py` (`definitions()`)
- Modify: `backend/apps/daily_report/transport/serializers.py` (`DefinitionsSerializer`)
- Modify: `backend/apps/daily_report/tests/test_definitions_api.py`

**Interfaces:**
- Produces: `definitions.cost_multiplier_rules() -> dict`; `data.ad_account_channels: list[dict]`, `data.cost_multiplier: dict` trong `/definitions`.

- [ ] **Step 1: Thêm test vào `test_definitions_api.py`**

```python
    def test_ad_account_channels_and_multiplier_rules(self):
        from decimal import Decimal

        from apps.channels_core.models import Channel, ChannelAdAccount
        from apps.facebook_ads.models import FacebookAdAccount, FacebookApp

        ch = Channel.objects.create(code="fb-def", name="FB Def")
        app = FacebookApp.objects.create(name="A", app_id="a", app_secret="s", access_token="t")
        acc = FacebookAdAccount.objects.create(app=app, name="TK 1", account_id="1", fb_id="act_1")
        ChannelAdAccount.objects.create(channel=ch, provider=ChannelAdAccount.Provider.FACEBOOK, account_id=acc.id)
        cache.clear()

        data = client().get(URL).json()["data"]
        self.assertIn({"platform": "facebook", "account_id": "1", "name": "TK 1", "channels": ["fb-def"]}, data["ad_account_channels"])
        fb = data["cost_multiplier"]["facebook"]
        self.assertEqual(fb["schedule"][0], {"effective_from": "2026-09-01", "multiplier": 1.1})
        self.assertEqual(fb["schedule"][1], {"effective_from": "2026-08-01", "multiplier": 1.08})
        self.assertEqual(fb["schedule"][-1], {"effective_from": None, "multiplier": 1.1})
        self.assertIn("rule", fb)
        self.assertEqual(data["cost_multiplier"]["google"]["schedule"], [{"effective_from": None, "multiplier": 1.08}])
        for key in ("rows", "bucket", "close_rate", "net_revenue", "closed_revenue", "remainder_row", "channel", "cost_multiplier"):
            self.assertIn(key, data["metrics"], key)
        for gone in ("ads_by_channel", "ads_by_account", "ads_by_campaign", "cost_outside_channels", "total_orders"):
            self.assertNotIn(gone, data["metrics"], gone)
```

- [ ] **Step 2: Chạy test, phải fail**

Run: `$PYTEST apps/daily_report/tests/test_definitions_api.py -q --reuse-db -p no:cacheprovider`
Expected: FAIL — `KeyError: 'ad_account_channels'`

- [ ] **Step 3: `definitions.py`** — import thêm `ADS_DAILY_COST_MULTIPLIER, FACEBOOK_ADS_COST_MULTIPLIER_SCHEDULE, GOOGLE_ADS_DAILY_COST_MULTIPLIER` từ `apps.orders.reporting.shared`; thêm sau `order_bucket_of`:

```python


def cost_multiplier_rules() -> dict:
    """Phát biểu luật hệ số để Hermes hiểu `multiplier` trong dòng /ads. Không chép số."""
    facebook = [
        {"effective_from": eff.isoformat(), "multiplier": float(m)} for eff, m in FACEBOOK_ADS_COST_MULTIPLIER_SCHEDULE
    ]
    facebook.append({"effective_from": None, "multiplier": float(ADS_DAILY_COST_MULTIPLIER)})
    return {
        "facebook": {
            "schedule": facebook,
            "rule": "Chọn theo ngày BẮT ĐẦU kỳ (`from`): mốc effective_from gần nhất ≤ from; cả kỳ dùng một hệ số. "
                    "effective_from null = hệ số gốc khi from trước mọi mốc.",
        },
        "google": {
            "schedule": [{"effective_from": None, "multiplier": float(GOOGLE_ADS_DAILY_COST_MULTIPLIER)}],
            "rule": "Cố định.",
        },
    }
```

Thay toàn bộ `METRIC_GLOSSARY` bằng:

```python
METRIC_GLOSSARY: dict[str, str] = {
    "rows": "API trả TỪNG DÒNG, không trả số gộp (từ 16/09/2026). Mọi chỉ số cũ tính lại bằng cách đếm/cộng dòng theo các luật dưới đây. Kéo hết trang bằng `next_cursor` trước khi cộng.",
    "lead": "Một dòng của /leads/stats = một đơn nội bộ (không tính đơn mirror foxia/cnv, không tính đơn gốc đã được khôi phục thành bản sao) tạo trong kỳ, theo ngày tạo giờ Việt Nam — mọi kênh, mọi nhân viên, kể cả lead chưa gán. Rộng hơn phạm vi Báo cáo sale (chỉ kênh có nhân viên Ads phụ trách và nhân viên phòng Sale).",
    "bucket": "`sales_status.bucket` (lead) và `order_status.bucket` (đơn) là luật rổ của CRM, tính ở server. Cộng theo bucket, không tự tra mã. `sales_status.bucket` null = chưa đánh giá (unassessed); `staff` null = chưa gán (unassigned).",
    "closed": "Số dòng /leads/stats có `sales_status.bucket = closed` (closed, closed_express, closed_scheduled_date, created_order).",
    "closed_revenue": "Σ `amounts.total` của dòng /leads/stats có `sales_status.code` ∈ {closed, closed_express, created_order} — KHÔNG tính closed_scheduled_date, đúng luật Báo cáo sale. Góc nhìn sale; khác `total_revenue` (đã lên hoá đơn) — không cộng vào nhau.",
    "close_rate": "closed / (số dòng /leads/stats − dòng có sales_status.code ∈ {underlying_condition, wants_store_visit}). Cùng CÔNG THỨC mẫu số với Báo cáo sale nhưng tính trên toàn bộ đơn nội bộ, nên có thể THẤP hơn màn hình. Không chia khi mẫu số 0.",
    "calling": "Không nghe máy lần 1..5 — còn nhịp gọi tiếp.",
    "unreached": "Không nghe máy lần 6 hoặc không liên hệ được.",
    "nurture": "Tư vấn thêm / hẹn lịch / gọi lại sau.",
    "dirty": "Spam, lặp, sai số, khách bom hàng.",
    "other": "Có trạng thái sale nhưng mã không thuộc từ điển hiện tại.",
    "orders": "Một dòng của /orders/stats = một đơn đã lên hoá đơn KiotViet (invoice_status khác null), không huỷ (status ≠ 6, invoice_status ≠ 2), tạo trong kỳ, loại chi nhánh nội bộ và kho Đại Mỗ. Số dòng = total_orders cũ; đếm theo `order_status.bucket` = delivered / awaiting / returned / return_risk cũ.",
    "total_revenue": "Σ `amounts.total` (VND) của mọi dòng /orders/stats. Σ theo bucket cộng lại đúng bằng tổng (bất biến).",
    "net_revenue": "total_revenue − Σ `amounts.total` của dòng có `order_status.bucket = returned` (status 4, 5, 12, 13).",
    "revenue_lines": "Một dòng của /revenue = một sản phẩm trong một đơn thuộc tập /orders/stats (nối `order_id`). Doanh thu theo sản phẩm = Σ `amount` theo `sku`. Σ `amount` theo đơn có thể khác `amounts.total` bên đơn vì phí ship / giảm giá cấp đơn.",
    "channel_group": "shopee / tiktok (theo nguồn đơn hoặc prefix mã hoá đơn HDSPE_/HDTTS_) → facebook (kênh facebook-*) → web (google-ads-web) → off_other. Sàn xét trước kênh.",
    "channel": "Kênh (`channel`) trong CRM = một Digital: nhân viên/đội Ads phụ trách, ví dụ `facebook-cuong` = Facebook Cường, `google-ads-web` = Google Ads/Web. KHÔNG phải fanpage. Hỏi 'theo Digital' thì nhóm dòng đơn theo `channel.id`/`channel.name` và nối chi phí qua `channels` của dòng /ads (hoặc `ad_account_channels`). Hỏi 'theo fanpage' thì API này không có — hội thoại theo fanpage đọc ở /api/ai/hermes/conversations?page=.",
    "open_leads": "Một dòng của /pipeline = lead tạo trong lookback_days ngày, chưa lên hoá đơn, chưa huỷ, trạng thái sale chưa thuộc nhóm kết thúc (closed, dirty, not_buying, underlying_condition, warranty, cancelled, archived). Rổ tuổi cũ theo `age_days`: 0-1d / 2-3d / 4-7d / 8d+.",
    "ads_rows": "Một dòng của /ads = (ngày × nền tảng × TKQC × chiến dịch). Facebook cộng từ grain ad (ad unique toàn cục), Google Max theo (ngày, customer, campaign) vì hai MCC lặp cùng số tiền. `spend` CHƯA nhân hệ số; `spend_adjusted` = round(spend × multiplier). Không có ad set / ad riêng lẻ, không có lead/doanh thu theo chiến dịch (đơn gắn kênh, không gắn chiến dịch).",
    "remainder_row": "Mỗi (ngày × nền tảng × TKQC) có đúng MỘT dòng `is_remainder = true`, `campaign_id = null`: chi tiêu TKQC (bản sao gộp Max) − Σ chiến dịch. Có thể ÂM khi grain ad sync lệch nhịp. Σ mọi dòng theo TKQC = chi tiêu TKQC (by_account cũ); Σ dòng có campaign_id = by_campaign cũ; Σ dòng dư = cost_outside_campaigns cũ. Đừng cộng dòng dư vào chiến dịch.",
    "cost_multiplier": "Hệ số VAT/phụ phí: xem `cost_multiplier` ở cấp `data` của /definitions. Chọn theo ngày BẮT ĐẦU kỳ (`from`), cả kỳ một hệ số — cố ý khác mart `ai.chi_phi_quang_cao` (áp theo từng ngày), nên kỳ bắc qua mốc 01/08 hay 01/09/2026 cho hai tổng khác nhau.",
    "ad_account_channels": "Bản đồ TKQC → Digital hiện hành (không theo kỳ). `channels` rỗng = TKQC chưa map kênh — tiền vẫn trong tổng TKQC, nêu ra khi cộng theo Digital. TKQC map nhiều kênh: API không chia, để nguyên mảng (màn Báo cáo Ads chia đều).",
}
```

- [ ] **Step 4: `stats_service.definitions()`** — thêm hai khoá:

```python
        "ad_account_channels": [
            {"platform": a.platform, "account_id": a.account_id, "name": a.name, "channels": list(a.channels)}
            for a in ads_spend.account_directory()
        ],
        "cost_multiplier": d.cost_multiplier_rules(),
```

Import `from apps.daily_report.infrastructure import ads_spend, queries`.

- [ ] **Step 5: `DefinitionsSerializer`** — thêm:

```python
    ad_account_channels = serializers.ListField(child=serializers.DictField(), help_text="Bản đồ TKQC → mã kênh (Digital)")
    cost_multiplier = serializers.DictField(child=serializers.DictField(), help_text="Luật hệ số VAT/phụ phí theo nền tảng")
```

- [ ] **Step 6: Chạy test, phải pass**

Run: `$PYTEST apps/daily_report/tests/test_definitions_api.py -q --reuse-db -p no:cacheprovider`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/apps/daily_report
git commit -m "feat(daily-report): /definitions thêm bản đồ TKQC→Digital, luật hệ số, glossary theo dòng

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Payload shapes + OpenAPI sinh lại + dọn code chết + chạy toàn bộ test

**Files:**
- Rewrite: `backend/apps/daily_report/tests/test_payload_shapes.py`
- Regenerate: `docs/openapi/daily-report.yaml`
- Modify: `backend/apps/daily_report/openapi.py` (DESCRIPTION, VERSION)
- Modify: `backend/apps/daily_report/application/stats_service.py`, `transport/views.py` (xoá import/hàm chết)

- [ ] **Step 1: Viết lại `tests/test_payload_shapes.py`**

```python
"""Khoá payload THỰC với serializer định nghĩa schema (transport/serializers.py):
lệch giữa hai bên nghĩa là OpenAPI đang nói dối trợ lý. Serializer chỉ phục vụ
drf-spectacular — nếu test này đỏ, SỬA SERIALIZER (schema theo payload), không
sửa test để "cho qua"."""

from datetime import date
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.catalog.models import Product
from apps.daily_report.transport import serializers as s
from apps.facebook_ads.models import FacebookAdAccount, FacebookAdAccountDailySpend, FacebookApp
from apps.orders.models import OrderItem

from .helpers import TOKEN, client, rebuild, seed_order

D = date(2026, 9, 2)
P = {"from": "2026-09-02", "to": "2026-09-02"}
PAGE_KEYS = {"meta", "data", "limit", "next_cursor"}


def _fields(serializer, *, exclude: tuple[str, ...] = ()) -> set[str]:
    inst = serializer() if isinstance(serializer, type) else serializer
    return set(inst.fields.keys()) - set(exclude)


def _nested_fields(serializer_cls, name: str) -> set[str]:
    return set(serializer_cls().fields[name].fields.keys())


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class PayloadShapeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        order = seed_order(D, status=3, invoice_status=1, sales_status="closed")
        prod = Product.objects.create(name="Ghế", sku="MSC06")
        OrderItem.objects.create(order=order, product=prod, sku="MSC06", quantity=1, unit_price=1, line_total=1)
        app = FacebookApp.objects.create(name="A", app_id="a", app_secret="s", access_token="t")
        acc = FacebookAdAccount.objects.create(app=app, name="TK", account_id="1", fb_id="act_1")
        FacebookAdAccountDailySpend.objects.create(ad_account=acc, date=D, spend=Decimal("1000"))
        rebuild()

    def setUp(self):
        cache.clear()

    def _first_row(self, url, params=P):
        body = client().get(url, params).json()
        self.assertEqual(set(body.keys()), PAGE_KEYS, url)
        self.assertTrue(body["data"], f"{url} không có dòng để so shape")
        return body["data"][0]

    def test_order_row_matches_serializer(self):
        for url in ("/api/daily-report/leads/stats", "/api/daily-report/orders/stats"):
            row = self._first_row(url)
            self.assertEqual(set(row.keys()), _fields(s.OrderRowSerializer), url)
            for nested in ("order_status", "invoice_status", "customer", "amounts", "items", "sales_status", "needs", "age", "calls"):
                self.assertEqual(set(row[nested].keys()), _nested_fields(s.OrderRowSerializer, nested), f"{url} {nested}")
            self.assertEqual(set(row["channel"].keys()), _nested_fields(s.OrderRowSerializer, "channel"))

    def test_revenue_row_matches_serializer(self):
        self.assertEqual(set(self._first_row("/api/daily-report/revenue").keys()), _fields(s.OrderLineRowSerializer))

    def test_pipeline_row_matches_serializer(self):
        seed_order(D, sales_status="call_back_later")
        rebuild()
        row = self._first_row("/api/daily-report/pipeline", {"as_of": "2026-09-02"})
        self.assertEqual(set(row.keys()), _fields(s.OpenLeadRowSerializer))

    def test_ads_row_matches_serializer(self):
        body = client().get("/api/daily-report/ads", P).json()
        self.assertEqual(set(body["data"][0].keys()), _fields(s.AdSpendRowSerializer))
        self.assertEqual(set(body["meta"].keys()), _fields(s.MetaSerializer))

    def test_definitions_matches_serializer(self):
        data = client().get("/api/daily-report/definitions").json()["data"]
        self.assertEqual(set(data.keys()), _fields(s.DefinitionsSerializer))

    def test_health_matches_serializer(self):
        body = client().get("/api/daily-report/health").json()
        self.assertEqual(set(body.keys()), _fields(s.HealthSerializer))

    def test_meta_matches_serializer_outside_ads(self):
        meta = client().get("/api/daily-report/leads/stats", P).json()["meta"]
        self.assertEqual(set(meta.keys()), _fields(s.MetaSerializer, exclude=("spend_as_of",)))
```

- [ ] **Step 2: Chạy, sửa serializer cho khớp nếu đỏ**

Run: `$PYTEST apps/daily_report/tests/test_payload_shapes.py -q --reuse-db -p no:cacheprovider`
Expected: PASS. Nếu FAIL vì lệch khoá → sửa serializer (không sửa payload, không sửa test).

- [ ] **Step 3: `openapi.py`** — đổi `VERSION` thành `"2.0.0"` và `DESCRIPTION`:

```python
    "DESCRIPTION": (
        "API chỉ đọc cho trợ lý báo cáo ngày. Xác thực bằng header X-Api-Token. "
        "Từ 16/09/2026 mọi endpoint trả TỪNG DÒNG (đơn, dòng hàng, lead mở, chi tiêu ads) với "
        "phân trang keyset `cursor`/`limit`, không còn số gộp và không còn `group_by` — bên gọi "
        "tự đếm/cộng theo `bucket`. Ngày theo múi giờ Asia/Ho_Chi_Minh; tiền là số nguyên VND; "
        "mọi response có meta.data_as_of là mốc dữ liệu (mart cập nhật mỗi 30 phút). "
        "Cách tính lại từng chỉ số cũ: GET /definitions → metrics."
    ),
```

- [ ] **Step 4: Sinh lại OpenAPI**

```bash
/home/desi/desi-order/.venv/bin/python manage.py spectacular --urlconf apps.daily_report.urls \
  --custom-settings apps.daily_report.openapi.SPECTACULAR_SETTINGS \
  --file ../docs/openapi/daily-report.yaml
git diff --stat ../docs/openapi/daily-report.yaml
```

Kiểm tra file mới không còn `LeadMetrics`, `by_channel`, `SummaryEnvelope`; có `OrderRowsPage`, `AdSpendRowsPage`, `/summary` với `410`.

- [ ] **Step 5: Dọn code chết**

```bash
grep -n "^from\|^import" apps/daily_report/application/stats_service.py apps/daily_report/application/ads_service.py apps/daily_report/transport/views.py apps/daily_report/infrastructure/queries.py
grep -rn "cached(\|format_key\|_sort_key\|LEAD_GROUPS\|ORDER_GROUPS\|REVENUE_GROUPS\|ADS_GROUPS\|AGE_BUCKETS\|hashlib\|json\b" apps/daily_report --include=*.py
```

Xoá mọi import/hằng không còn được tham chiếu (`hashlib`, `json`, `Counter`, `defaultdict`, `cache` nếu `definitions_payload` là nơi duy nhất còn dùng thì giữ `cache`). Sửa docstring đầu `views.py` thành: `"""8 endpoint GET của API Daily Report — 5 trả danh sách dòng, /summary 410. Mọi view đi qua ``daily_report_view``."""`. Chạy import-linter:

```bash
cd /home/desi/desi-order/.claude/worktrees/feat-daily-report-list-api/backend && /home/desi/desi-order/.venv/bin/lint-imports
```

Expected: `Contracts: N kept, 0 broken.`

- [ ] **Step 6: Chạy TOÀN BỘ test daily_report + ai_mart (ai_mart để chắc không đụng)**

```bash
$PYTEST apps/daily_report apps/ai_mart/tests/test_don_hang.py -q --reuse-db -p no:cacheprovider > /home/desi/.cache/dr-final.txt 2>&1; tail -3 /home/desi/.cache/dr-final.txt
```

Expected: `N passed, 0 failed`. Nếu đỏ: đọc đủ danh sách FAILED trong file, sửa, chạy lại.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/daily_report docs/openapi/daily-report.yaml
git commit -m "feat(daily-report): OpenAPI 2.0.0 theo dòng, test shape payload, dọn code số gộp

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Tài liệu — `daily-report-api.md`, brief Hermes mục 0.6/0.7, `hermes-directory.md`

**Files:**
- Rewrite: `docs/daily-report-api.md`
- Modify: `docs/ai-schema-hermes-brief.md` (mục 0.6 bảng endpoint; mục 0.7 các dòng nhắc `group_by`, `by_account`, `by_campaign`, `by_channel`)
- Modify: `docs/hermes-directory.md` (dòng 5 và dòng 35 nhắc `by_account`/`by_channel`)

- [ ] **Step 1: Viết lại `docs/daily-report-api.md`** — giữ nguyên các mục "Những quyết định cố ý" (bỏ dòng "Cache 30 phút…"), "Triển khai", "Cấu hình", "Sinh lại OpenAPI"; thay phần đầu tới hết "Cạm bẫy đã biết" bằng:

````markdown
# API Daily Report cho trợ lý báo cáo ngày

Kênh **máy-tới-máy chỉ đọc** cho trợ lý Daily Report (Hermes, profile `lich-task`,
Mac mini). Nguồn: mart `ai.don_hang` / `ai.dong_don_hang` (apps/ai_mart, cập nhật :07 và
:37 mỗi giờ) và OLTP chi tiêu quảng cáo. Thiết kế gốc (số gộp, 03/09/2026):
docs/superpowers/specs/2026-09-03-daily-report-read-api-design.md. **Từ 16/09/2026 API trả
TỪNG DÒNG, không trả số gộp** — quyết định chủ dự án, spec
docs/superpowers/specs/2026-09-16-daily-report-list-api-design.md. Hermes tự đếm/cộng.

## Hợp đồng

Tiền tố `/api/daily-report/`, header `X-Api-Token: <DAILY_REPORT_API_TOKEN>` (hoặc
`Authorization: Bearer`). File OpenAPI: `docs/openapi/daily-report.yaml` — gửi được
cho trợ lý, không chứa bí mật.

| Endpoint | 1 dòng = | Tham số | Trần `limit` |
|---|---|---|---|
| `GET /health` | — | — | — |
| `GET /definitions` | — | — | — |
| `GET /leads/stats` | 1 lead = đơn nội bộ tạo trong kỳ, mọi trạng thái | `from`, `to`, `cursor`, `limit` | 1000 |
| `GET /orders/stats` | 1 đơn có hoá đơn, không huỷ, loại chi nhánh ẩn (tập total_orders cũ) | `from`, `to`, `cursor`, `limit` | 1000 |
| `GET /revenue` | 1 dòng hàng (sản phẩm trong đơn) của tập `/orders/stats` | `from`, `to`, `cursor`, `limit` | 2000 |
| `GET /pipeline` | 1 lead còn mở tính đến `as_of`, kèm `age_days` | `as_of`, `lookback_days` (1..92, mặc định 30), `cursor`, `limit` | 1000 |
| `GET /ads` | 1 (ngày × nền tảng × TKQC × chiến dịch) + 1 dòng dư mỗi TKQC/ngày | `from`, `to`, `cursor`, `limit` | 2000 |
| `GET /summary` | — | luôn **410 Gone** | — |

Ngày `YYYY-MM-DD` theo `Asia/Ho_Chi_Minh`, bao gồm hai đầu, mặc định hôm qua, tối đa
92 ngày. Tiền: số nguyên VND. `group_by` **không còn** — gửi lên là 400.

### Phân trang (keyset)

```
{"meta": {timezone, range, data_as_of, data_status, definitions_url, generated_at[, spend_as_of]},
 "data": [ {...dòng..., "cursor": ...} ],
 "limit": 200,
 "next_cursor": <giá trị | null>}
```

`limit` mặc định 200; vượt trần bị kẹp về trần (không lỗi). Kéo trang sau bằng
`cursor=<next_cursor>`; `null` = hết. Cursor là `id` dòng (số) — riêng `/ads` là chuỗi
`day|platform|account_id|campaign_id`. Keyset chứ không offset: mart nạp thêm dòng giữa lúc
kéo không làm sót/lặp trang đã lấy. Throttle 300 request/giờ theo IP: 92 ngày ≈ 30 trang
đơn, đủ; `/ads` không cache, mỗi trang tính lại cả kỳ — kéo `/ads` theo tuần thay vì 92 ngày.

### Tính lại số gộp cũ từ dòng

| Số cũ | Cách tính từ dòng |
|---|---|
| `leads`, `unassigned`, `unassessed` | `len(/leads/stats)`; đếm `staff == null`; đếm `sales_status.bucket == null` |
| 11 rổ lead, `other` | đếm `/leads/stats` theo `sales_status.bucket` |
| `closed_revenue` | Σ `amounts.total` của `/leads/stats` có `sales_status.code` ∈ {closed, closed_express, created_order} |
| `close_rate` | closed / (leads − dòng có code ∈ {underlying_condition, wants_store_visit}) |
| `total_orders`, `delivered`, `awaiting`, `returned`, `return_risk` | `len(/orders/stats)`; đếm theo `order_status.bucket` |
| `total_revenue`, 4 rổ tiền, `net_revenue` | Σ `amounts.total` của `/orders/stats` (theo bucket); net = total − returned |
| doanh thu theo sản phẩm | Σ `amount` của `/revenue` theo `sku` |
| `open_leads`, `by_status`, `by_age`, `by_staff` | `/pipeline`: đếm; rổ tuổi 0-1d/2-3d/4-7d/8d+ từ `age_days` |
| `ads.by_account[].cost` | Σ `spend_adjusted` mọi dòng `/ads` theo (`platform`, `account_id`) |
| `ads.by_campaign[].cost` | Σ `spend_adjusted` dòng có `campaign_id` |
| `ads.total.cost_outside_campaigns` | Σ `spend_adjusted` dòng `is_remainder` |
| theo Digital | nhóm `/orders/stats` theo `channel`; chi phí: dòng `/ads` có mã kênh trong `channels` (hoặc `definitions.ad_account_channels`) |
| `summary` | không còn; ghép từ ba endpoint trên |

Chi tiết từng luật: `GET /definitions` → `metrics`.

## Định nghĩa số liệu (khớp màn hình CRM)
- Rổ `order_status.bucket`, `sales_status.bucket` tính ở server từ `apps/orders/reporting/shared.py`;
  API chỉ ghép lại. Nguồn luật không đổi so với bản số gộp.
- Dòng `/orders/stats` cộng lại khớp Báo cáo tổng (`summary_overview`) — test
  `apps/daily_report/tests/test_orders_revenue.py::test_matches_summary_overview` khoá điều này.
- `/ads`: dòng chiến dịch + dòng dư cộng lại khớp `by_account` / `by_campaign` cũ — test
  `apps/daily_report/tests/test_ads.py`. `by_channel` (bảng "Báo cáo theo digital" của app)
  **không còn**: TKQC map nhiều kênh thì app chia đều, API để nguyên mảng `channels`.

## Cạm bẫy đã biết
- `data_status` ≠ "Bình thường" nghĩa là số có thể cũ — trợ lý phải nói rõ mốc `data_as_of`.
- `closed_revenue` (sale chốt, từ `/leads/stats`) và `total_revenue` (đã lên hoá đơn, từ
  `/orders/stats`) là hai góc nhìn khác nhau, không cộng vào nhau.
- `sales_status.bucket = null` là chưa đánh giá, KHÔNG phải rổ `other` (`other` = có mã nhưng
  mã lạ). `order_status.bucket = cancelled` không xuất hiện trong `/orders/stats` (đã lọc) nhưng
  có trong `/leads/stats`.
- `/revenue` lọc theo ngày tạo ĐƠN (không theo ngày dòng hàng) để cùng tập với `/orders/stats`.
  Σ `amount` theo `order_id` ≠ `amounts.total` là bình thường (ship/giảm giá cấp đơn).
- `/ads`: dòng dư có thể ÂM (grain ad và grain TKQC sync lệch nhịp) — trả nguyên, không kẹp 0.
  Hệ số FB chọn theo NGÀY BẮT ĐẦU kỳ (docs/ads-facebook-cost-multiplier.md), khác mart
  `ai.chi_phi_quang_cao` áp theo ngày; `meta.spend_as_of` là mốc sync chi tiêu.
- **"Kênh" = Digital, không phải fanpage.** `channel` trên dòng đơn và `channels` trên dòng ads
  đều là mã Digital (`facebook-cuong` = Facebook Cường…). Bảng tra câu hỏi → endpoint nằm ở
  `docs/ai-schema-hermes-brief.md` mục 0.7.
- `ngay_chot` chưa backfill — `closed_at` thường null; nhóm theo `created_date`.
- Staging có `IS_PRODUCTION=false` → không fail-closed khi thiếu token (mở, không
  503). Vẫn phải đặt `DAILY_REPORT_API_TOKEN` ở staging để không lộ số liệu.
- Allowlist IP Tailscale nằm ở `nginx/default.conf` và `nginx/conf.d/default.conf`
  — KHÔNG phải trong các vhost portal `*.conf.prod-template`.
````

Mục "Test" cuối file đổi câu đầu thành: `apps/daily_report/tests/ — cửa xác thực, tham số, phân trang keyset, tương đương Báo cáo tổng từ dòng, dòng dư /ads, shape payload, drift OpenAPI.`

- [ ] **Step 2: Sửa `docs/ai-schema-hermes-brief.md`**

Mục 0.6, dòng bảng "Báo cáo ngày": thay ô Endpoint bằng `GET /api/daily-report/{health,definitions,leads/stats,orders/stats,revenue,pipeline,ads}` — **mỗi dòng = một đơn / dòng hàng / lead mở / dòng chi tiêu; phân trang `cursor`/`limit`; KHÔNG có số gộp, KHÔNG có `group_by`** (`/summary` = 410). Ngay dưới bảng thêm đoạn:

```markdown
> **Từ 16/09/2026 API Daily Report trả TỪNG DÒNG, không trả số.** Muốn "bao nhiêu đơn giao
> thành công" thì kéo hết trang `/orders/stats` rồi đếm dòng có `order_status.bucket = delivered`;
> muốn doanh thu thì Σ `amounts.total`. Bảng "số cũ → cách tính từ dòng" nằm ở
> `docs/daily-report-api.md`; luật từng chỉ số ở `GET /definitions → metrics`.
```

Mục 0.7: sửa các dòng bảng (theo nội dung hiện tại ở dòng ~70–74):
- Chi tiêu theo TKQC: `GET /api/daily-report/ads?from&to` → **Σ `spend_adjusted` mọi dòng theo (`platform`, `account_id`)**; `channels` trên dòng = bản đồ TKQC → Digital (hoặc `/definitions.ad_account_channels`).
- Chiến dịch: dòng `/ads` có `campaign_id` (lọc `account_id` = TKQC đang hỏi); dòng `is_remainder` là chênh grain — nêu ra, không cộng vào chiến dịch.
- Hiệu quả theo Digital: nhóm dòng `/orders/stats` (và `/leads/stats`) theo `channel.id`; chi phí = Σ `spend_adjusted` dòng `/ads` có mã kênh đó trong `channels`. Ghi chú: app chỉ hiện kênh có nhân viên Ads; TKQC map nhiều kênh app chia đều, API để nguyên mảng.
- Theo nguồn / nhóm kênh: nhóm dòng `/leads/stats`, `/orders/stats`, `/revenue` (qua `order_id`) theo `source.code` hoặc `channel_group` — thay cho `group_by=source|channel_group` cũ.

Grep để không sót: `grep -n "group_by\|by_account\|by_campaign\|by_channel\|/summary" docs/ai-schema-hermes-brief.md docs/hermes-directory.md docs/hermes-chat-api.md docs/hermes-call-transcript-api.md` — sửa mọi chỗ còn lại theo tinh thần trên.

- [ ] **Step 3: `docs/hermes-directory.md`** — dòng 5: `/api/daily-report/ads → by_account[].channels` → `/api/daily-report/definitions → ad_account_channels[].channels (hoặc cột channels trên từng dòng /ads)`. Dòng 35: "tập kênh xuất hiện trong `by_channel` của `/ads`" → "tập kênh có nhân viên Ads — màn Báo cáo Ads của app; API `/ads` không lọc theo đây, để nguyên mọi TKQC".

- [ ] **Step 4: Commit**

```bash
git add docs/daily-report-api.md docs/ai-schema-hermes-brief.md docs/hermes-directory.md
git commit -m "docs(daily-report): tài liệu API theo dòng + brief Hermes 0.6/0.7 cách tự cộng

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Kiểm tra cuối, bàn giao

- [ ] **Step 1: Chạy lại toàn bộ test daily_report lần cuối** (Task 9 Step 6) và `lint-imports`. Ghi số passed.

- [ ] **Step 2: Đối chiếu spec** — mở `docs/superpowers/specs/2026-09-16-daily-report-list-api-design.md` mục 4, tick từng URL: hình dạng dòng, trần limit, keyset, 410, definitions 2 khoá mới.

- [ ] **Step 3: Không push, không tạo MR** — báo chủ dự án: nhánh `feat/daily-report-list-api` trong worktree, breaking đối với Hermes, cần cập nhật system prompt (dán brief mới) cùng lúc deploy; MR để riêng. Cách tạo MR khi được duyệt: `git push -o merge_request.create -o merge_request.target=master origin feat/daily-report-list-api` (không có glab/token).

---

## Self-review (đã chạy khi viết plan)

- **Spec coverage:** §3 (hợp đồng chung) → Task 1, 4; §4 bảng → Task 4 (leads/orders/revenue), 5 (pipeline), 6 (summary 410), 7 (ads), 8 (definitions); §4.1–4.3 shape → Task 3 + serializer Task 4/5; §4.4 bucket → Task 1, 3; §4.5 dòng dư/cursor tuple/spend_as_of lên meta → Task 7; §4.6 → Task 8; §5 code đụng → Task 2, 4, 7, 9; §6 test → từng task + Task 9; §7 docs → Task 9 (OpenAPI), 10; §8 triển khai → Task 11.
- **Type consistency:** `page_envelope(items, *, d_from, d_to, fresh, limit, next_cursor)` dùng ở Task 4, 5, 7; `_paging(request, max_limit) -> (after, limit)` Task 4, 5; `queries.order_rows(..., invoiced_only=, after=, limit=)` Task 2, 4; `rows.order_row/open_lead_row/order_line_row` Task 3, 4, 5; `ads_service.parse_cursor` Task 7 view; `page_envelope_of(row_serializer, *, name, cursor_field)` Task 4, 5, 7; `fetch_all(url, params, limit=3)` Task 4, 5, 7.
- **Placeholder scan:** không có TBD/TODO; mọi bước code có nội dung.
