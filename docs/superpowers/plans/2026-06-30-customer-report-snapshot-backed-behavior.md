# Customer Report — Snapshot-backed behavior report (perf follow-up)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Loại bỏ 2 lần seq-scan `orders` (Q1 buyers/funnel/lifecycle ~1.6s + Q2 leads ~2.2s) trong `CustomerBehaviorReportService.summary` cho view **không filter** (all-time, mọi kênh) bằng cách aggregate bảng `customer_segment_snapshot` (744k dòng hẹp, đã populate). Giữ nguyên đường SQL cũ cho query có filter kênh/ngày.

**Architecture:** Snapshot đã có `total_orders, total_spent, last_purchase_at, has_problem, segment_code` mỗi khách. Thêm 1 cột `latest_sales_status_code` (rebuild đã tính sẵn qua `_latest_sales_map`). Khi `channel/date_from/date_to` đều None → tính overview/funnel/pre/post bằng GROUP BY trên snapshot; ngược lại fallback SQL cũ. `total_customers` vẫn lấy bằng `Customer.count()` (rẻ, indexed, luôn đúng). `cohort_retention` + `data_coverage` giữ nguyên (ngoài phạm vi).

**Tech:** Django, raw SQL trên customer_segment_snapshot, pytest.

## Global Constraints
- Branch `feat/customer-report-redesign` (gộp MR !786). KHÔNG tạo branch mới. Commit/task. English commit msg, no Co-Authored-By.
- ruff không có trong image test — bỏ qua.
- **Tương đương bắt buộc:** với cùng dữ liệu và view không-filter, output snapshot-backed phải KHỚP output đường SQL cũ (overview, funnel, pre_purchase.stage_distribution, post_purchase). Đây là gate chính.
- Boundary GTC giữ nguyên (snapshot.total_orders đã = GTC count từ `_metrics_map`).
- Test backend: `MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default --env-file backend/.env -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -v "/c/Users/MYPC/Desktop/workspace/desi-order/backend:/app" -w /app desi-order-web-test:latest pytest <path> -q`

---

### Task 1: Thêm `latest_sales_status_code` vào snapshot + populate

**Files:**
- Modify: `backend/apps/customers/models.py` (CustomerSegmentSnapshot)
- Create: migration `customers/00XX_snapshot_latest_sales`
- Modify: `backend/apps/customers/segment_snapshot_service.py` (`_rebuild_batch` ghi cột mới)
- Test: `backend/apps/customers/tests/test_segment_snapshot_service.py`

**Interfaces:**
- Snapshot field mới: `latest_sales_status_code = models.CharField(max_length=64, null=True, blank=True)`.
- `_rebuild_batch` set `latest_sales_status_code=latest_sales.get(cid)` (biến `latest_sales` đã có sẵn trong hàm).

- [ ] Step 1: thêm field vào model (sau `has_problem`)
```python
    latest_sales_status_code = models.CharField(max_length=64, null=True, blank=True)
```
- [ ] Step 2: makemigrations customers; migrate (fresh test DB applies it).
- [ ] Step 3: cập nhật `_rebuild_batch` — trong vòng tạo `CustomerSegmentSnapshot(...)` thêm kwarg `latest_sales_status_code=latest_sales.get(cid)`; và thêm `"latest_sales_status_code"` vào `update_fields` của `bulk_create(update_conflicts=True, ...)`.
- [ ] Step 4: test — rebuild 1 khách có đơn với assessment.sales_status_code="closed" → snapshot.latest_sales_status_code == "closed".
```python
def test_rebuild_writes_latest_sales(self):
    from apps.orders.models import Order, OrderAssessment
    c = Customer.objects.create(full_name="X")
    o = Order.objects.create(customer=c, status=3, total_after_discount=Decimal("0"), channel_id=self._chan())
    OrderAssessment.objects.create(order=o, sales_status_code="closed")
    SegmentSnapshotService.rebuild([c.id])
    self.assertEqual(CustomerSegmentSnapshot.objects.get(pk=c.id).latest_sales_status_code, "closed")
```
- [ ] Step 5: chạy `pytest apps/customers/tests/test_segment_snapshot_service.py -q` → pass.
- [ ] Step 6: commit `feat(customers): store latest sales status in segment snapshot`.

---

### Task 2: Snapshot-backed behavior summary (unfiltered) + equivalence test

**Files:**
- Create: `backend/apps/customers/behavior_snapshot_query.py` (aggregate snapshot → cùng cấu trúc dict các block của summary)
- Modify: `backend/apps/customers/customer_behavior_report.py` (`summary`: nếu không filter → dùng snapshot path)
- Test: `backend/apps/customers/tests/test_behavior_snapshot_equiv.py`

**Interfaces:**
- `behavior_snapshot_query.summary_from_snapshot(total_customers: int) -> dict` trả về **đúng các key** mà `CustomerBehaviorReportService.summary` đang trả cho phần: `overview`, `pre_purchase`, `post_purchase`, `funnel`. (KHÔNG bao gồm `cohort_retention`, `data_coverage` — caller tự thêm.)
- Trong `summary()`: `is_unfiltered = not channel and not date_from and not date_to`. Nếu `is_unfiltered` → lấy `overview/pre_purchase/post_purchase/funnel` từ snapshot; vẫn gọi cohort + data_coverage như cũ; merge lại. Ngược lại → đường SQL cũ y nguyên.

**Aggregation (1 query trên customer_segment_snapshot):**
```sql
SELECT
  count(*) FILTER (WHERE total_orders > 0) AS buyers,
  count(*) FILTER (WHERE total_orders > 0 AND last_purchase_at >= now()-interval '90 days') AS active,
  count(*) FILTER (WHERE total_orders > 0 AND last_purchase_at < now()-interval '90 days'
                    AND last_purchase_at >= now()-interval '180 days') AS cooling,
  count(*) FILTER (WHERE total_orders > 0 AND last_purchase_at < now()-interval '180 days'
                    AND last_purchase_at >= now()-interval '365 days') AS at_risk,
  count(*) FILTER (WHERE total_orders > 0 AND last_purchase_at < now()-interval '365 days') AS dormant,
  count(*) FILTER (WHERE total_orders >= 2) AS repeat_buyers,
  count(*) FILTER (WHERE total_orders = 1) AS first_time,
  count(*) FILTER (WHERE total_orders >= 2 OR total_spent >= 2000000) AS loyal,
  coalesce(sum(total_orders) FILTER (WHERE total_orders > 0), 0) AS total_orders,
  coalesce(sum(total_spent)  FILTER (WHERE total_orders > 0), 0) AS total_spent,
  count(*) FILTER (WHERE has_problem) AS problem_count,
  count(*) FILTER (WHERE total_orders = 0 AND latest_sales_status_code = ANY(%(consulting)s)) AS consulting,
  count(*) FILTER (WHERE total_orders = 0 AND latest_sales_status_code = ANY(%(no_contact)s)) AS no_contact,
  count(*) FILTER (WHERE total_orders = 0 AND latest_sales_status_code = ANY(%(rejected)s))   AS rejected,
  count(*) FILTER (WHERE total_orders = 0 AND latest_sales_status_code = ANY(%(spam)s))        AS spam
FROM customer_segment_snapshot;
```
- `leads = total_customers - buyers`; `not_contacted = max(leads - (consulting+no_contact+rejected+spam), 0)`.
- Dùng đúng các hằng `bc.CONSULTING_CODES...`, `bc.LEAD_*`, `bc.POST_*`, `_pct`, `_dist`, ngưỡng `_ACTIVE/_COOLING/_AT_RISK`, `_LOYAL_*` từ module hiện có (import lại) để dict trả về **giống hệt** đường cũ.
- `avg_order_value = round(total_spent/total_orders,0) if total_orders else 0.0`.
- funnel.stages = `[total_customers, leads, buyers, repeat_buyers, loyal]` y như Task7 cũ.

**⚠ problem_count tương đương:** đường cũ dùng `_problem_count()` (query orders riêng); snapshot dùng `has_problem` (từ `_customers_with_problem_signal`). NẾU equivalence test phát hiện lệch → KHÔNG dùng has_problem, thay bằng gọi `_problem_count(None,None,None)` trong snapshot path (vẫn đúng, chỉ là 1 query phụ). Quyết định theo test.

- [ ] Step 1: viết equivalence test — seed vài khách (buyer GTC freq1/freq2/loyal, lead có sales_status_code các nhóm, problem), rebuild snapshot, rồi:
```python
def test_snapshot_matches_sql(self):
    SegmentSnapshotService.rebuild()  # toàn bộ
    sql = CustomerBehaviorReportService._summary_sql()      # đường cũ (xem Step 3)
    snap = CustomerBehaviorReportService.summary()          # đường mới (unfiltered)
    for key in ("overview", "pre_purchase", "post_purchase", "funnel"):
        self.assertEqual(snap[key], sql[key])
```
- [ ] Step 2: chạy test → FAIL (chưa có path).
- [ ] Step 3: refactor `summary()`: tách phần SQL hiện tại thành `_summary_sql(channel,date_from,date_to)` (giữ nguyên logic), `summary()` quyết định snapshot vs sql, rồi luôn gắn `cohort_retention` + `data_coverage` (giữ nguyên). Cài `behavior_snapshot_query.summary_from_snapshot`.
- [ ] Step 4: chạy test equivalence + `test_customer_behavior_report.py` + `test_customer_report_service.py` → tất cả pass. Nếu lệch problem_count → áp ghi chú ⚠ ở trên.
- [ ] Step 5: commit `perf(customers): serve unfiltered behavior report from snapshot`.

---

### Task 3: Rebuild + đo lại (ops, ngoài code)
- [ ] Sau merge/deploy: chạy task `rebuild_segment_snapshot` (đã có) để điền `latest_sales_status_code`.
- [ ] Đo lại endpoint cache-miss view mặc định; kỳ vọng <300ms.
- [ ] (Ops tách riêng, KHÔNG trong code) cân nhắc `work_mem` cao hơn cho query CÒN filter (vẫn scan): giảm tràn đĩa Q1/Q2.

## Self-review
- Equivalence test là gate: nếu snapshot path lệch SQL path ở bất kỳ block nào → fail, buộc sửa.
- Stale: snapshot trễ ≤2h; total_customers vẫn lấy live nên tổng luôn đúng; buyers/lifecycle trễ ≤2h (chấp nhận cho báo cáo, đã ghi badge "cập nhật lúc").
- Filtered query: không đổi (đường SQL cũ) — chỉ view mặc định được tăng tốc.

---

## Round 2 — drop residual scans (problem_count, cohort) + fix cache TTL gap

### RT1: problem_count from snapshot (drop Q5 on fast path)
- Add column `is_problem_order_customer = models.BooleanField(default=False)` to CustomerSegmentSnapshot + migration.
- In `SegmentSnapshotService._rebuild_batch`: compute the problem set for the batch using EXACTLY `_problem_count`'s criteria (unfiltered), set the flag:
  ```python
  from django.db.models import Q
  from apps.orders.models import Order
  from . import segmentation
  problem_orders = set(
      Order.objects.filter(customer_id__in=ids, deleted_at__isnull=True)
      .exclude(branch_id__in=EXCLUDED_BRANCH_IDS)
      .filter(
          Q(status__in=segmentation.PROBLEM_ORDER_STATUSES)
          | Q(assessment__sales_status_code__in=segmentation.PROBLEM_SALES_STATUS_CODES)
          | Q(assessment__delivery_business_status_code__in=segmentation.PROBLEM_DELIVERY_STATUS_CODES)
          | Q(assessment__post_purchase_rating_code__in=segmentation.PROBLEM_POST_PURCHASE_RATING_CODES)
      ).values_list("customer_id", flat=True).distinct()
  )
  # is_problem_order_customer=cid in problem_orders ; add to update_fields
  ```
- `behavior_snapshot_query`: add `count(*) FILTER (WHERE is_problem_order_customer) AS problem_count` to the snapshot SQL; `summary_from_snapshot` no longer takes problem_count param — compute it from snapshot. Remove the `_problem_count` call on the unfiltered path in `summary()`.
- KEEP `has_problem` untouched (it feeds classify_segment; do not change segment semantics).
- Equivalence test (existing test_behavior_snapshot_equiv) must stay green — it already compares snapshot vs SQL problem_count.

### RT2: cohort snapshot (drop Q3 on unfiltered path)
- New model `CohortRetentionSnapshot(scope CharField pk, payload JSONField, updated_at)` + migration.
- `report_cohort.rebuild_snapshot()`: compute `retention(None,None,None)` and upsert under scope="all".
- `report_cohort.cached_retention(channel,date_from,date_to)`: if all None → read scope="all" payload (fallback to live `retention()` if missing); else live.
- `CustomerBehaviorReportService.summary` uses `cached_retention(...)` instead of `retention(...)`.
- `rebuild_segment_snapshot` task also calls `report_cohort.rebuild_snapshot()` so cohort refreshes with the 2h cron.

### RT3: fix warm-cache TTL gap
- In `tasks.py::warm_report_cache`, the beat runs every 2h but cache timeout=3600 (1h) → 1h gap. Set `timeout=10800` (3h > 2h interval) so the unfiltered view is always a cache hit. (View's own miss-path keeps timeout=600.)
