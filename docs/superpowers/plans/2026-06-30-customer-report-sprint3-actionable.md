# Customer Report Sprint 3 — Actionable blocks Implementation Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** 3 khối hành động-được: Tái kích hoạt (A), Xu hướng theo kỳ (C), Chất lượng kênh (B). Tất cả từ orders/RFM + snapshot, precompute cron 2h.

**Tech:** Django + raw SQL/ORM trên `customer_segment_snapshot` & `orders`, recharts FE, pytest/vitest.

## Global Constraints
- Branch `feat/customer-report-redesign`. KHÔNG tạo branch mới. Commit/task. English commit msg, no Co-Authored-By. ruff không có trong image test — bỏ qua.
- Dữ liệu từ orders/snapshot (KHÔNG assessment). RBAC: các endpoint mới dùng cùng permission như `customer_report_summary` (IsAuthenticated + require_section customers/ORDER_SECTIONS — xem view hiện có).
- Backend test: `MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default --env-file backend/.env -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -v "/c/Users/MYPC/Desktop/workspace/desi-order/backend:/app" -w /app desi-order-web-test:latest pytest <path> -q` (start postgres/redis trước nếu cần: `docker compose -f docker-compose.local.yml up -d postgres redis`).
- FE: `cd frontend && npx vitest run <path>` ; `npx tsc --noEmit`.
- Snapshot fields có sẵn: customer_id, segment_code, total_orders, total_spent, last_purchase_at, has_problem, latest_sales_status_code, is_problem_order_customer.

---

## PHASE A — Tái kích hoạt

### Task A1: reactivation_service + endpoint
**Files:** create `backend/apps/customers/reactivation_service.py`; modify `backend/apps/customers/views.py` (+ view) & `urls.py` (+ route); test `backend/apps/customers/tests/test_reactivation_service.py`.

**Interfaces:**
- `ReactivationService.value_at_risk() -> {"at_risk":{"customers":int,"value":float}, "dormant":{...}, "total_value":float}` — từ snapshot, lifecycle theo `last_purchase_at` vs now (at_risk: 180–365 ngày; dormant: >365), chỉ buyers (`total_orders>0`).
- `ReactivationService.worklist(lifecycle:str, limit:int, offset:int) -> {"results":[...], "total":int}` — khách thuộc `lifecycle` (at_risk|dormant), order by `total_spent` desc; mỗi item `{customer_id, code, full_name, phone, total_spent, total_orders, last_purchase_at, days_since, last_product}`. Rank/đếm từ snapshot; join Customer + đơn mới nhất theo TRANG (như `CustomerProfileService.list_profiles`).
- View `reactivation_report(request)` → đọc query `lifecycle` (default "dormant"), `limit/offset` (dùng `parse_required_pagination` hoặc default 50/0); permission như `customer_report_summary`. Route: `path("report/reactivation", reactivation_report, name="customers-reactivation")`.

- [ ] Step 1: test thất bại — seed: 1 khách dormant chi 5tr (đơn cuối 400 ngày trước), 1 khách at_risk chi 1tr (200 ngày), 1 active. rebuild snapshot. `value_at_risk()` → dormant.value==5tr, at_risk.value==1tr. `worklist("dormant",50,0)` → 1 kết quả, total_spent đúng, days_since≈400.
- [ ] Step 2: chạy → FAIL.
- [ ] Step 3: cài service. value_at_risk dùng 1 query FILTER trên snapshot (giống behavior_snapshot_query). worklist:
  - ids xếp hạng: `CustomerSegmentSnapshot.objects.filter(total_orders__gt=0).filter(<lifecycle window on last_purchase_at>).order_by("-total_spent").values_list("customer_id","total_spent","total_orders","last_purchase_at")` → slice trang.
  - join Customer cho trang (id__in, giữ thứ tự); đơn mới nhất cho `last_product` (DISTINCT ON customer_id ORDER BY created_at DESC, lấy product/sku) — chỉ cho ids của trang.
  - days_since = (now - last_purchase_at).days.
- [ ] Step 4: cài view + route (permission copy từ `customer_report_summary`).
- [ ] Step 5: chạy test → pass.
- [ ] Step 6: commit `feat(customers): reactivation worklist + value-at-risk endpoint`.

### Task A2: FE tab "Tái kích hoạt"
**Files:** create `frontend/src/components/report/ReactivationPanel.tsx`; modify `frontend/src/lib/api/customers.ts` (api fn + types) & `frontend/src/pages/CustomerReportPage.tsx` (+ tab); test `__tests__/ReactivationPanel.test.tsx`.

**Interfaces:**
- `getReactivation({lifecycle,limit,offset})` → typed `ReactivationResponse {value_at_risk, results, total}`.
- `ReactivationPanel`: thẻ value-at-risk (tổng tiền + #khách at_risk/dormant) + bảng worklist (tên, SĐT, tổng chi, ngày mua cuối, ngày ngủ đông, SP gần nhất, link `/crm/customers?...` hoặc hồ sơ) + lifecycle toggle + phân trang. Empty/loading states.

- [ ] Step 1: type + api fn trong customers.ts.
- [ ] Step 2: ReactivationPanel + test (render value-at-risk + 1 dòng worklist từ mock).
- [ ] Step 3: thêm tab "Tái kích hoạt" vào `tabItems` của CustomerReportPage (lazy-load data khi tab active; tự fetch qua getReactivation).
- [ ] Step 4: `npx vitest run ...ReactivationPanel` + `npx tsc --noEmit` + `npm run build` → pass.
- [ ] Step 5: commit `feat(report): reactivation tab`.

---

## PHASE C — Xu hướng theo kỳ

### Task C1: report_trends precompute + endpoint
**Files:** create `backend/apps/customers/report_trends.py`; model `ReportTrendSnapshot` trong models.py + migration; modify `tasks.py` (rebuild gọi thêm), `views.py`+`urls.py` (endpoint); test `tests/test_report_trends.py`.

**Interfaces:**
- Model `ReportTrendSnapshot(scope CharField pk, payload JSONField default dict, updated_at auto_now)` db_table `report_trend_snapshot`.
- `report_trends.compute(months=12) -> {"months":[{"month":"YYYY-MM","new_customers":n,"buyers":n,"orders":n,"revenue":f,"aov":f,"repeat_buyers":n}], "deltas":{"revenue_pct":f,"repeat_rate_pct":f,...}}` — SQL trên orders (GTC filter chuẩn: status=3, invoice_status IS DISTINCT FROM 2, NOT branch=ANY(excluded)). new_customers = khách có đơn GTC đầu tiên trong tháng (date_trunc month của min(created_at)).
- `report_trends.rebuild_snapshot()` upsert scope="all"; `report_trends.cached(months=12)` đọc snapshot, fallback live.
- View `report_trends_view` → `cached()`; route `report/trends`. `rebuild_segment_snapshot` task gọi thêm `report_trends.rebuild_snapshot()`.

- [ ] Step 1: test — seed đơn 2 tháng khác nhau → `compute(3)` trả months có revenue/new_customers đúng; rebuild_snapshot + cached trả payload.
- [ ] Step 2: FAIL.
- [ ] Step 3: cài model+migration, compute (raw SQL, dùng ANY(list) cho excluded), rebuild_snapshot/cached.
- [ ] Step 4: view+route+wire task.
- [ ] Step 5: pass. commit `feat(customers): monthly report trends precompute + endpoint`.

### Task C2: FE tab "Xu hướng"
**Files:** `frontend/src/components/report/TrendsPanel.tsx` + test; api fn/types; CustomerReportPage tab.
- [ ] Biểu đồ đường (recharts) revenue + buyers + repeat theo tháng; KPI Δ% so kỳ trước (▲▼). test render từ mock. tsc+build pass. commit `feat(report): trends tab`.

---

## PHASE B — Chất lượng kênh

### Task B1: channel_quality precompute + endpoint
**Files:** `backend/apps/customers/channel_quality.py`; reuse `ReportTrendSnapshot` pattern (scope="channel_quality") hoặc model riêng `ReportChannelQualitySnapshot`; tasks/views/urls; test.

**Interfaces:**
- `channel_quality.compute() -> {"channels":[{"channel_id":id,"channel_name":str,"acquired":n,"repeat_pct":f,"orders_per_customer":f,"revenue_per_customer":f,"dormant_pct":f}]}` — theo kênh của đơn GTC ĐẦU TIÊN mỗi khách (acquire channel). SQL: firsts CTE (min created_at per customer + its channel), join metrics.
- `rebuild_snapshot()` + `cached()`; view `channel_quality_view`; route `report/channel-quality`; task wired.

- [ ] Step 1: test — 2 khách acquire kênh A (1 mua lại, 1 không) → channel A repeat_pct=50%. 
- [ ] Step 2: FAIL. Step 3: cài. Step 4: wire. Step 5: pass. commit `feat(customers): channel acquisition quality precompute + endpoint`.

### Task B2: FE tab "Chất lượng kênh"
**Files:** `ChannelQualityPanel.tsx` + test; api; CustomerReportPage tab.
- [ ] Bảng theo kênh, sort theo repeat_pct/revenue_per_customer, highlight tốt/xấu. test + tsc + build. commit `feat(report): channel quality tab`.

---

## Self-review
- 3 endpoint mới đều precompute/đọc snapshot → không scan nặng mỗi request (trừ worklist join per-page, bounded).
- Tất cả từ orders/snapshot, không assessment.
- Tasks/cron: `rebuild_segment_snapshot` gọi thêm rebuild của trends + channel_quality (cùng nhịp 2h).
- RBAC: copy permission từ customer_report_summary cho 4 endpoint mới.
