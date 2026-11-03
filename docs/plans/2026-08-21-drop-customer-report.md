# Gỡ trang Báo cáo khách hàng và cụm xử lý liên quan — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Xoá trang `/reports/customer-report`, 4 endpoint báo cáo, cụm service phía sau, báo cáo Word hàng tháng, và các bảng cache chỉ phục vụ chúng — mà không làm hỏng những thứ dùng chung.

**Architecture:** Xoá theo thứ tự từ ngoài vào trong: frontend → endpoint → service → báo cáo docx → task/bảng. Mỗi bước để lại cây import hợp lệ.

**Spec:** quyết định của chủ sở hữu ngày 2026-08-21: bỏ trang, bỏ luôn báo cáo Word, không dựng lại các phân tích tổng hợp.

## Global Constraints

- Container test: **`drop-suite`** (KHÔNG dùng `dwh-suite`/`dwh2-suite`/`dwh2b-suite`/`kvstar-suite`).
  - Test: `docker exec drop-suite sh -c 'pytest apps/customers -q > /tmp/t 2>&1; echo "EXIT=$?"; tail -25 /tmp/t'`
  - Ruff: `docker exec drop-suite sh -c 'PATH=/app/.local/bin:$PATH ruff check apps > /tmp/r 2>&1; echo "EXIT=$?"; cat /tmp/r'`
  - Import-linter: `docker exec drop-suite sh -c 'PATH=/app/.local/bin:$PATH lint-imports --config .importlinter > /tmp/l 2>&1; echo "EXIT=$?"; tail -6 /tmp/l'`
- Frontend trên host: `cd frontend && npx vitest run <path>`, `npx tsc --noEmit`.
- **Chạy pytest MỘT lượt, FOREGROUND, timeout rộng.** Không đẩy nền — 5 agent trong dự án này đã kẹt vì thế. Không chạy hai lượt song song.
- Không đọc `$?` sau khi pipe qua `tail`. `ruff`/`lint-imports` chỉ có ở `/app/.local/bin`.

---

## ⛔ TUYỆT ĐỐI KHÔNG XOÁ

Đây là phần quan trọng nhất của plan. Mỗi mục dưới đây trông như thuộc cụm báo cáo nhưng có người khác dùng:

| Giữ | Ai dùng |
|---|---|
| `customer_segment_snapshot` (bảng + model) | Trang Theo dõi tái mua; **và cả hai matview `dwh.fct_order_enriched`, `dwh.customer_360`** |
| `segment_snapshot_service.py` | Ghi bảng trên |
| **Bước 1 và bước 5** của `rebuild_segment_snapshot` | Bước 1 nuôi bảng trên; bước 5 (`reconcile_customer_type`) ghi `customers.is_new_customer` |
| Hai beat `customers-rebuild-segment-snapshot` và `...-full` | Chạy task trên |
| `segmentation.py` | `profile_service`, `customer_loyalty_service`, `behavior_classify`… |
| `segment_catalog.py` | `customer_loyalty_service`, `customer_resale_service` |
| `order_status_constants.py` | `customer_loyalty_service`, `customer_resale_service`, `profile_service` |
| `components/report/CollapsibleTable.tsx`, `DonutChart.tsx` | `features/ads/*`, `features/overview-report/*` |
| Section RBAC `dashboard` | Trang Dashboard, ads-report, `VALID_SECTIONS` |

> Xoá nhầm `customer_segment_snapshot` hoặc bước 1 của task sẽ làm cột phân khúc trong kho
> `dwh` **đóng băng ở giá trị cũ mà không báo lỗi gì** — matview vẫn refresh thành công, chỉ
> là dữ liệu nguồn ngừng cập nhật. Đây là kiểu hỏng im lặng tệ nhất trong toàn bộ dự án này.

---

## Task 1 — Frontend

**Xoá hẳn:**
- `frontend/src/pages/CustomerReportPage.tsx`
- `frontend/src/components/report/` — **10 file**: `CohortHeatmap`, `DataCoverageBadge`, `DistributionTable`, `InsightCards`, `LifecycleFunnel`, `ChannelQualityPanel`, `ReactivationPanel`, `TrendsPanel`, `ReportSection`, `StackedBarChart`. **GIỮ `CollapsibleTable.tsx` và `DonutChart.tsx`.**
- `frontend/src/lib/reportLinks.ts`
- `frontend/src/components/report/__tests__/customer-report-panels.test.tsx`
- `frontend/src/components/report/__tests__/report-primitives.test.tsx` (nếu nó còn phủ `CollapsibleTable`/`DonutChart` thì giữ lại phần đó thay vì xoá cả file)

**Sửa:**
- `lib/api/customers.ts` — xoá vùng report (~dòng 407-679): các type `Report*`, `CrossTab*`, `BehaviorOverview`, `PrePurchaseBlock`, `PostPurchaseBlock`, `CustomerReportSummary`, `CustomerReportFilters`, và hàm `getCustomerReportSummary`, `getReactivation`, `getTrends`, `getChannelQuality`. **GIỮ `getChannels`, `ChannelOption`, `getCrmCatalogs`.**
- `features/customers/queries.ts` — xoá `useCustomerReportSummary`, `useReportChannels`, key `reportSummary`, `reportChannels`. **GIỮ `useSegmentCategories`.**
- `lib/routes.ts` — bỏ `"customer-report"` khỏi `AppSection`, `SECTION_TO_PATH`, `SECTION_PERMISSION`, và redirect `/customers/report`.
- `app/routes.tsx` — bỏ import, route, `Guard`, redirect `crm/customer-report`.
- `app/routed-pages.tsx` — bỏ lazy import và `CustomerReportRoute`.
- `app/modules.ts`, `app/nav-layout.ts`, `app/use-page-title.ts` — bỏ mục.
- `i18n/messages/page.ts`, `i18n/messages/topnav.ts` — bỏ 2 khoá.
- Sửa assertion trong `app/__tests__/modules.test.ts`, `nav-layout.test.ts`, `lib/__tests__/routes.test.ts`.

**Kiểm chứng:** `npx tsc --noEmit` phải sạch — đây là lưới chính bắt import mồ côi. Rồi chạy toàn bộ `npx vitest run`.

**Commit:** `chore(frontend): remove customer report page`

---

## Task 2 — Bốn endpoint

- Xoá `backend/apps/customers/transport/views/customers_views_impl/report_views.py` (cả 4 view).
- Xoá 4 dòng route tương ứng trong `backend/apps/customers/urls.py`.
- Xoá `backend/apps/customers/report_serializers.py`.
- Xoá mọi import mồ côi ở `transport/views/__init__.py` nếu có.

**Kiểm chứng:** `pytest apps/customers -q` sẽ đỏ ở các test gọi endpoint — đó là dự kiến, Task 5 dọn test. Miễn là **không có ImportError lúc collect**.

**Commit:** `chore(customers): remove customer report endpoints`

---

## Task 3 — Cụm service

Xoá hẳn, theo thứ tự này (dưới lên trên để không để lại import gãy giữa chừng):

`report_insights.py` · `behavior_snapshot_query.py` · `customer_behavior_report.py` · `reactivation_service.py` · `report_trends.py` · `report_cohort.py` · `channel_quality.py` · `report_crosstabs.py` · `report_fields.py` · `report_service.py` · **`behavior_classify.py`**

> `behavior_classify.py` chỉ được import bởi chính các file trên và `report_doc/` (Task 4) —
> sau khi xoá hết, nó là code chết. Đã kiểm bằng grep toàn backend.

**Sau mỗi lần xoá**, chạy `lint-imports` để bắt import mồ côi sớm thay vì dồn cuối.

**Commit:** `chore(customers): remove customer report service layer`

---

## Task 4 — Báo cáo Word hàng tháng

Chủ sở hữu xác nhận **không còn dùng**.

- Xoá thư mục `backend/apps/customers/report_doc/`.
- Xoá `backend/apps/customers/management/commands/generate_monthly_customer_report.py`.
- Xoá `backend/apps/customers/tests/test_report_doc.py` (~33 test).
- Không có beat entry nào cho nó — kiểm chứng lại bằng grep `generate_monthly` trong `settings_parts/celery.py`.

**Commit:** `chore(customers): remove monthly docx report`

---

## Task 5 — Task, beat, bảng cache, và dọn test

**Cắt task `rebuild_segment_snapshot`:**
- Bỏ bước 2 (`report_cohort.rebuild_snapshot`), bước 3 (`report_trends.rebuild_snapshot`), bước 4 (`channel_quality.rebuild_snapshot`).
- **GIỮ bước 1** (`SegmentSnapshotService.rebuild` / `rebuild_incremental`) **và bước 5** (`reconcile_customer_type`).
- Giữ nguyên `single_flight`, giữ nguyên việc **raise khi có bước hỏng** — đó là bài học sự cố 22/07, đừng đơn giản hoá nó đi khi dọn.

**Xoá hẳn task `warm_report_cache`** + beat `customers-warm-report-cache` + entry route của nó trong `CELERY_TASK_ROUTES`.

**Xoá 3 model cache** `CohortRetentionSnapshot`, `ReportTrendSnapshot`, `ReportChannelQualitySnapshot` trong `apps/customers/models.py`, kèm **một migration** `DROP TABLE`. Migration phải có `reverse_sql` tạo lại bảng rỗng (Django đòi reversible; và nếu phải rollback thì bảng rỗng là đủ — chúng chỉ là cache).

**Sửa test:**
- `tests/test_customer_tasks.py` — assertion về danh sách 3 bước precompute và về hai nhịp beat.
- `apps/common/tests/test_celery_routes.py` — bỏ `warm_report_cache`.
- `apps/foxia/tests/test_foxia_order_isolation.py` — 3 test dùng `CustomerBehaviorReportService.summary`, `channel_quality.compute`, `report_trends.compute` làm phép thử cô lập đơn mirror. **Đừng xoá chúng** — chúng kiểm một bất biến thật (đơn mirror không lọt vào báo cáo). Trỏ lại sang query còn sống, ưu tiên `dwh.fct_order_enriched` vốn cũng loại đơn mirror.
- Xoá `tests/test_reactivation_service.py`, `test_report_trends.py`, `test_channel_quality.py`, `test_report_cohort.py`, `test_customer_report_service.py`.
- `tests/test_behavior_report.py` — **giữ** `SegmentSnapshotServiceTests`, `SegmentSnapshotFullRebuildSkipTests`, `SegmentSnapshotIncrementalTests`; xoá phần còn lại.
- `tests/test_report_query_guards.py` — giữ các ca `statement_timeout`/`single_flight` (generic), bỏ phần trỏ vào `report_cohort`.

**Sửa doc:** `docs/dwh-customer-analytics.md` — hai chỗ (§0.6 và §10.2) dùng `GET /api/customers/report/summary` làm mốc đối chiếu cho mart. Endpoint đó không còn. Thay bằng `python manage.py dwh_reconcile`, vốn đã là cổng nghiệm thu chính thức.

**Kiểm chứng cuối:** `pytest apps/customers apps/foxia apps/common apps/analytics -q` một lượt, foreground. Cộng ruff, import-linter, và `npx tsc --noEmit` + `npx vitest run` phía frontend.

**Commit:** `chore(customers): trim precompute task and drop report cache tables`

---

## Self-Review

**Coverage:** khảo sát liệt kê 7 nhóm; Task 1 phủ nhóm 1 và 7 (frontend + RBAC — không cần migration vì `customer-report` chưa từng là section backend), Task 2-3 phủ nhóm 2, Task 4 phủ nhóm 5, Task 5 phủ nhóm 3, 4 và 6.

**Rủi ro chính đã ghi rõ:** bảng `customer_segment_snapshot` và bước 1/5 của task phải sống — mục "TUYỆT ĐỐI KHÔNG XOÁ" ở đầu plan.

**Không có placeholder.** Mọi file cần xoá đều được nêu đích danh.
