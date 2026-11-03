# Kho phân tích `dwh` — Khối 2B (S3: API + trang Khám phá dữ liệu) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Hai endpoint danh sách đọc từ mart, hai định nghĩa xuất Excel, và trang `/reports/customer-explorer` hai tab (Đơn hàng / Khách hàng) với bộ lọc dùng chung, chọn cột, sắp xếp và phân trang phía server.

**Architecture:** Endpoint DRF function-based theo khuôn `loyalty_view.py`, đọc mart bằng SQL thuần qua `mart_queries.py`. Frontend theo khuôn `warranty-claims` (ColumnManager + localStorage + bảng thủ công) và `foxia-report` (queries.ts + lib/api).

**Tech Stack:** Django 5 + PostgreSQL 16 + DRF; React + TS + react-query + Tailwind.

**Spec:** `docs/dwh-customer-analytics.md`

## Global Constraints

- Python ≥ 3.12 ruff; TypeScript strict.
- Container backend: **`dwh2b-suite`** (KHÔNG dùng `dwh-suite`/`dwh2-suite`/`kvstar-suite`).
  - Test BE: `docker exec dwh2b-suite sh -c 'pytest apps/analytics -q > /tmp/t 2>&1; echo "EXIT=$?"; tail -25 /tmp/t'`
  - Ruff: `docker exec dwh2b-suite sh -c 'PATH=/app/.local/bin:$PATH ruff check apps/analytics > /tmp/r 2>&1; echo "EXIT=$?"; cat /tmp/r'`
  - Import-linter: `docker exec dwh2b-suite sh -c 'PATH=/app/.local/bin:$PATH lint-imports --config .importlinter > /tmp/l 2>&1; echo "EXIT=$?"; tail -6 /tmp/l'`
- Test FE chạy trên host trong `frontend/`: `npm test -- <path>`. Nếu `node_modules` thiếu, thử junction từ worktree anh em trước khi `npm ci`.
- Chạy pytest MỘT lượt, foreground, không song song (`DuplicateDatabase`).
- Không đọc `$?` sau khi pipe qua `tail`.
- `ruff`/`lint-imports` chỉ ở `/app/.local/bin`; `python -m ruff` không chạy.
- Matview không có trong `information_schema.columns` — tra `pg_attribute`.
- Vitest: `globals: false` → phải `import { describe, it, expect } from "vitest"`. Bắt buộc
  `mockViewport(false)` + `restoreViewport()` trong `afterEach` khi assert bảng desktop.
- **KHÔNG regen `backend/schema.yaml`** trong khối này (nợ có chủ đích, xem spec §8.4). FE
  tự khai type response trong `lib/api/`, đúng như `lib/api/foxia-report.ts` đang làm.

---

## Quyết định thiết kế (đã chốt — không tự đổi)

| # | Quyết định | Lý do |
|---|---|---|
| **D1** | Tham số sắp xếp là **`sort`** (tên cột) + **`sort_dir`** (`asc`/`desc`), KHÔNG dùng `-field` kiểu DRF. | Repo chưa có quy ước server-side sort nào — khối này đặt ra nó và các trang sau sẽ sao chép. Hai tham số tách rời đọc URL là hiểu, và whitelist cột hợp lệ dễ hơn parse tiền tố. |
| **D2** | `sort` phải được **whitelist** theo danh sách cột cho phép; giá trị lạ → 400, KHÔNG im lặng bỏ qua. | Nối thẳng tên cột vào `ORDER BY` là SQL injection. Whitelist là hàng rào duy nhất. Im lặng bỏ qua thì người dùng tưởng đã sắp xếp. |
| **D3** | Tab Khách hàng có bộ lọc **`era`**, mặc định **`current`**. | 87% khách trong kho là ngành hàng cũ, tỉ lệ chuyển đổi 0,14% (spec §11.1). Mặc định hiển thị toàn bộ nghĩa là mặc định cho người ta xem một bức tranh bị pha loãng — và số mặc định là số người ta nhớ. |
| **D4** | Bộ lọc ngày dùng chung cho cả hai tab, mặc định `last_30_days`. | Quen thuộc, nhanh, khớp mọi trang báo cáo khác. |
| **D5** | Phân trang bắt buộc `limit`+`offset` qua `parse_required_pagination`, `max_limit=100`. | Khuôn sẵn có; chặn query quét cả mart từ URL. |
| **D6** | Endpoint trả `{"results": [...], "total": n}`. | Envelope chuẩn của repo. |
| **D7** | Khi mart chưa refresh → **503** kèm thông báo hành động, không phải 500. | Giống `/coverage` đã làm. Bắt `MartNotReady`. |
| **D8** | Quyền: `require_section("customer-explorer")`, và section này dùng chung permission `dashboard`. | Khuôn của `customer-report`. |

---

## File Structure

| File | Trách nhiệm |
|---|---|
| `backend/apps/analytics/infrastructure/mart_queries.py` | thêm `list_orders()`, `list_customers()` |
| `backend/apps/analytics/application/explorer_sort.py` | whitelist cột + dựng mệnh đề ORDER BY |
| `backend/apps/analytics/transport/views.py` | 2 endpoint danh sách |
| `backend/apps/analytics/transport/serializers.py` | 2 serializer dòng + 2 envelope |
| `backend/apps/analytics/urls.py` | 2 route |
| `backend/apps/analytics/exports_definitions.py` | 2 `ExportDefinition` |
| `backend/apps/analytics/apps.py` | `ready()` import exports_definitions |
| `frontend/src/lib/api/customer-explorer.ts` | client + type response |
| `frontend/src/features/customer-explorer/queries.ts` | key factory + hooks |
| `frontend/src/features/customer-explorer/explorer-columns.ts` | catalogue cột 2 tab |
| `frontend/src/features/customer-explorer/explorer-storage.ts` | localStorage cột |
| `frontend/src/features/customer-explorer/useExplorerColumns.ts` | state cột |
| `frontend/src/features/customer-explorer/pages/CustomerExplorerPage.tsx` | trang |
| `frontend/src/features/customer-explorer/components/{ExplorerFilterBar,ExplorerTable,ExplorerColumnPicker}.tsx` | thành phần |
| `frontend/src/lib/routes.ts`, `app/routes.tsx`, `app/routed-pages.tsx`, `app/modules.ts` | đăng ký trang |

---

## Task 1 — Tầng đọc mart + whitelist sắp xếp

**Files:** create `application/explorer_sort.py`; modify `infrastructure/mart_queries.py`; test `tests/test_explorer_queries.py`

**Interfaces produced:**
- `ORDER_SORT_COLUMNS: dict[str, str]`, `CUSTOMER_SORT_COLUMNS: dict[str, str]` — khoá là tên client gửi, giá trị là tên cột SQL.
- `build_order_by(sort: str | None, sort_dir: str | None, allowed: dict, default: str) -> str` — raise `ValueError` nếu `sort` không nằm trong `allowed` hoặc `sort_dir` không thuộc `{asc, desc}`.
- `list_orders(*, limit, offset, date_from, date_to, channel, status, sort, sort_dir) -> dict` trả `{"results": [...], "total": n}`.
- `list_customers(*, limit, offset, date_from, date_to, era, segment, recency_bucket, sort, sort_dir) -> dict`.

- [ ] **Step 1: test trước**

`tests/test_explorer_queries.py`:

```python
import pytest
from apps.analytics.application.explorer_sort import (
    CUSTOMER_SORT_COLUMNS, ORDER_SORT_COLUMNS, build_order_by,
)


def test_rejects_unknown_sort_column():
    with pytest.raises(ValueError):
        build_order_by("; DROP TABLE orders", "asc", ORDER_SORT_COLUMNS, "created_at")


def test_rejects_unknown_direction():
    with pytest.raises(ValueError):
        build_order_by("created_at", "sideways", ORDER_SORT_COLUMNS, "created_at")


def test_maps_client_name_to_sql_column():
    assert build_order_by("created_at", "desc", ORDER_SORT_COLUMNS, "created_at") == (
        "f.created_at DESC"
    )


def test_falls_back_to_default_when_sort_absent():
    assert build_order_by(None, None, ORDER_SORT_COLUMNS, "created_at").startswith("f.created_at")


def test_every_whitelisted_column_is_a_bare_identifier():
    """Hàng rào cuối: không giá trị nào trong whitelist được chứa ký tự lạ."""
    import re
    for mapping in (ORDER_SORT_COLUMNS, CUSTOMER_SORT_COLUMNS):
        for sql_name in mapping.values():
            assert re.fullmatch(r"[a-z_]+\.[a-z_]+", sql_name), sql_name
```

Thêm test DB cho `list_orders`/`list_customers`: lọc theo ngày, lọc `era`, phân trang (`total` không đổi khi đổi `offset`), và sắp xếp đảo chiều thì dòng đầu đổi.

- [ ] **Step 2: chạy — FAIL** (`ModuleNotFoundError`)
- [ ] **Step 3: implement.** SQL dùng tham số ràng buộc cho MỌI giá trị; chỉ mệnh đề `ORDER BY` được nội suy, và chỉ từ whitelist. `total` đếm bằng `count(*) OVER ()` trong cùng query để tránh chạy hai lượt.
- [ ] **Step 4: chạy — PASS**
- [ ] **Step 5: kiểm chứng whitelist thật sự chặn.** Tạm bỏ kiểm tra whitelist, chạy `test_rejects_unknown_sort_column`, XEM nó FAIL, khôi phục. Báo cáo cả hai quan sát.
- [ ] **Step 6: commit** `feat(analytics): add explorer mart queries with sort whitelist`

---

## Task 2 — Hai endpoint danh sách

**Files:** modify `transport/views.py`, `transport/serializers.py`, `urls.py`; test `tests/test_explorer_api.py`

**Consumes:** Task 1.

- [ ] **Step 1: test trước** — theo khuôn `apps/customers/tests/test_customer_profiles_api.py`:
  `test_requires_auth` (401), shape `{results,total}`, mỗi tham số lọc một test, `sort` lạ → **400**,
  `limit` vượt trần → 400, và mart chưa refresh → **503** (dựng bằng `REFRESH ... WITH NO DATA`).
- [ ] **Step 2: chạy — FAIL** (404)
- [ ] **Step 3: implement** theo `loyalty_view.py`: `@extend_schema(responses=inline_serializer(...))`,
  `@api_view(["GET"])`, `@permission_classes([IsAuthenticated, require_section("customer-explorer")])`,
  `parse_required_pagination(request, max_limit=100)`, đọc param bằng
  `(request.query_params.get("x") or "").strip() or None`. Bắt `ValueError` từ `build_order_by` → 400
  với thông báo nêu rõ các giá trị hợp lệ. Bắt `MartNotReady` → 503.
- [ ] **Step 4: chạy — PASS**
- [ ] **Step 5: commit** `feat(analytics): add explorer list endpoints`

---

## Task 3 — Hai định nghĩa xuất Excel

**Files:** create `exports_definitions.py`; modify `apps.py`; test `tests/test_explorer_exports.py`

**Interfaces:** `kind="analytics_orders"`, `kind="analytics_customers"`.

> ⚠️ `estimate()` và `write()` **KHÔNG nhận `user`** (`registry.py`). Phải lưu `user_id` vào
> params trong `validate()` rồi nạp lại user trong `write()` — đúng như `AuditLogsExport` làm.
> Đây là quy ước bắt buộc sao chép, không phải tuỳ chọn.

- [ ] **Step 1: test trước** — `validate` trả dict JSON-serializable và ổn định (gọi hai lần cùng
  input cho ra kết quả bằng nhau, vì nó nuôi `params_hash` dedupe); `check_permission` từ chối user
  thiếu quyền; `estimate` trả số dòng; `write` ghi đúng số dòng và đúng cột đã chọn.
- [ ] **Step 2: chạy — FAIL**
- [ ] **Step 3: implement** theo `apps/audit/exports_definitions.py`. `AnalyticsConfig.ready()` import
  module. Trần dòng theo `EXPORT_MAX_ROWS`.
- [ ] **Step 4: chạy — PASS**
- [ ] **Step 5: commit** `feat(analytics): add explorer excel exports`

---

## Task 4 — Tầng API frontend

**Files:** create `lib/api/customer-explorer.ts`, `features/customer-explorer/queries.ts`; test `lib/api/__tests__/customer-explorer-api.test.ts`

- [ ] **Step 1: test trước** — mock `@/services/http`, assert URL sinh ra: camelCase vào,
  **snake_case ra** (`date_from`, `date_to`, `sort`, `sort_dir`, `era`, `limit`, `offset`), và tham số
  rỗng thì **không xuất hiện** trong query string.
- [ ] **Step 2: chạy — FAIL**
- [ ] **Step 3: implement** theo `lib/api/foxia-report.ts`. Type response khai tay (không regen schema).
  `queries.ts` theo `features/foxia-report/queries.ts`: key factory + `keepPreviousData`.
- [ ] **Step 4: chạy — PASS**
- [ ] **Step 5: commit** `feat(analytics): add customer explorer api client`

---

## Task 5 — Trang Khám phá dữ liệu

**Files:** create `features/customer-explorer/{explorer-columns.ts,explorer-storage.ts,useExplorerColumns.ts}`, `pages/CustomerExplorerPage.tsx`, `components/{ExplorerFilterBar,ExplorerTable,ExplorerColumnPicker}.tsx`; modify `lib/routes.ts`, `app/routes.tsx`, `app/routed-pages.tsx`, `app/modules.ts`

> **Năm chỗ phải khai trong `lib/routes.ts`** — bỏ sót chỗ nào cũng hỏng câm lặng:
> `AppSection`, `SECTION_TO_PATH`, **KHÔNG khai `SECTION_PERMISSION`** (xem dưới),
> `FULL_HEIGHT_SECTIONS` (trang này dùng `h-full` cho bảng cuộn nội bộ), và **kiểm tra**
> `sectionFromPath()` — path khớp chính xác nên không cần regex, nhưng nếu sai thì sidebar rơi
> về `"orders"` mà không báo gì.
> Cộng thêm mục sidebar ở `app/modules.ts` (module `reports`).
>
> ⚠️ **KHÔNG map `SECTION_PERMISSION["customer-explorer"] = "dashboard"`.** Task 2 đã tạo
> quyền riêng `customer-explorer:view` / `:export` trong `rbac_catalog.py` + migration 0040.
> Nếu FE dùng chung quyền `dashboard` thì ai có dashboard sẽ THẤY MENU rồi nhận 403 ở mọi
> thao tác. Để trống `SECTION_PERMISSION` nghĩa là guard đòi đúng section `customer-explorer`
> — menu ẩn cho tới khi role được cấp quyền, đó là hành vi đúng.
>
> **Nợ vận hành phải ghi vào MR:** quyền mới CHƯA gán cho role nào. Sau deploy phải cấp,
> nếu không không ai thấy trang. Tiền lệ đã cắn: `contacts:export` (MR !1253).

- [ ] **Step 1: test trước** — render trang với API đã mock: hai tab đổi được; đổi bộ lọc ngày gọi
  lại query với param mới; bộ lọc `era` mặc định là `current`; đổi cột hiển thị thì bảng đổi cột;
  bấm sắp xếp gửi `sort`+`sort_dir`. Dùng `renderWithClient`, `mockViewport(false)`, và
  `restoreViewport()` trong `afterEach`.
- [ ] **Step 2: chạy — FAIL**
- [ ] **Step 3: implement.** ColumnManager + localStorage theo khuôn `warranty-claims`
  (`useWarrantyColumns.ts`, `warranty-claims-storage.ts` — khoá localStorage có hậu tố phiên bản,
  `readKeys` lọc khoá lạ, ghi bọc try/catch). Bảng thủ công trong `data-table-shell`, phân trang bằng
  `components/ui/table-pagination.tsx`. Header sắp xếp: tự dựng, kèm chỉ báo chiều.
- [ ] **Step 4: chạy — PASS**
- [ ] **Step 5: commit** `feat(analytics): add customer explorer page`

---

## Task 6 — Nút xuất Excel + doc

**Files:** modify `components/ExplorerTable.tsx` (hoặc toolbar); modify `docs/dwh-customer-analytics.md`

- [ ] **Step 1:** nút Xuất Excel gọi `useExportJobs().startExport(kind, {columns, filters, user_id})`.
  Chụp snapshot bộ lọc **tại thời điểm bấm** — có tiền lệ lỗi ở repo này khi ref bị memo hoá.
  Không tự bắt lỗi: provider đã tự toast.
- [ ] **Step 2:** test: bấm nút gọi `startExport` đúng `kind` và đúng bộ lọc đang áp.
- [ ] **Step 3:** doc §12 — mô tả trang, quy ước `sort`/`sort_dir` mới (ghi rõ đây là quy ước **mới
  của repo**, trang sau nên theo), mặc định `era=current` và lý do.
- [ ] **Step 4:** cập nhật bảng tiến độ: S3 ✅.
- [ ] **Step 5: commit**

---

## Self-Review

**Spec coverage:** §4 (API) → Task 1-2; §4.1 (xuất Excel) → Task 3, 6; §5.1 (trang) → Task 5.

**Placeholder scan:** không có TBD; mọi bước có lệnh hoặc code cụ thể.

**Type consistency:** `build_order_by` và hai dict whitelist định nghĩa ở Task 1, dùng ở Task 2.
`list_orders`/`list_customers` định nghĩa Task 1, dùng Task 2 và Task 3. Tên tham số trên dây
(`date_from`/`date_to`/`sort`/`sort_dir`/`era`) nhất quán giữa Task 2 và Task 4.

**Khoảng trống có chủ đích:** không regen `schema.yaml` nên FE khai type tay — nợ đã ghi ở spec
§8.4. Màn hồ sơ 360 (S4) và chuyển trang cũ (S5) thuộc khối sau.
