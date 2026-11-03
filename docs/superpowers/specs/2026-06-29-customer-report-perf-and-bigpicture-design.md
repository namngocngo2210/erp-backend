# Thiết kế: Tối ưu hiệu năng + "bức tranh toàn cảnh" cho Báo cáo khách hàng

**Ngày:** 2026-06-29
**Branch:** `feat/customer-report-redesign`
**Trạng thái:** Spec — chờ duyệt để chuyển sang implementation plan
**Tiền thân:** `2026-06-15-customer-behavior-report-design.md` (mô hình 2 trạng thái hiện hành).
Doc này KHÔNG đổi định nghĩa nghiệp vụ đã chốt ở tiền thân; chỉ sửa **cách tính (perf)**
và **cách kể chuyện (UX)**.

## 1. Bối cảnh & vấn đề

Trang báo cáo (`apps/customers/views.py::customer_report_summary` →
`/api/customers/report/summary` → `frontend/src/pages/CustomerReportPage.tsx`) đang chạy
production và đúng về nghiệp vụ, nhưng có 2 vấn đề người dùng phản ánh:

### 1.1. Query rất chậm

Endpoint trên **mỗi cache-miss** chạy *tuần tự 2 service nặng* rồi merge
(`CustomerReportService.summary` + `CustomerBehaviorReportService.summary`), cache chỉ
TTL 600s, không precompute → đổi `date`/`channel`/`segment` một lần là miss toàn bộ.

Bốn nguồn chậm thực sự (theo mức độ):

| # | Nguồn chậm | Vị trí | Bản chất |
|---|---|---|---|
| 1 | **Mặc định all-time** | `CustomerReportPage::buildDefaultDateFilter` gửi `from/to` rỗng | Mọi truy vấn quét toàn bộ lịch sử `orders`, không chặn kỳ |
| 2 | **`_segment_map` tính realtime** | `report_service.py:163` | Với *mọi* customer trong range: 3 query (`_metrics_map`, `_customers_with_problem_signal`, `_latest_order_map`) + **loop Python** gọi `classify_segment` từng người |
| 3 | **Prefetch thừa** | `profile_service.py::_latest_order_map` | `prefetch_related("assessment__needs__need_type__group")` — report chỉ cần `sales_status_code` nhưng kéo cả cây needs của từng khách |
| 4 | **Materialize + Counter** | `report_service.py:66` | `rows = list(base.values(...))` kéo toàn bộ order-có-assessment về Python rồi ~15 `Counter` (giới tính/tỉnh/tuổi/segment/brand/crosstabs) đếm trong vòng lặp Python thay vì `GROUP BY` trong SQL |

Phụ: filter theo `segment` (giá trị suy ra, không index được) buộc tính phân khúc cho
*toàn bộ* id rồi mới lọc — cùng bẫy với `list_profiles:47`.

### 1.2. "Chưa thấy bức tranh toàn cảnh"

Trang hiện là một **atlas of distributions**: đúng số, nhưng chỉ gom chỉ số rồi nhét vào
2 tab rời rạc (Trước mua / Sau mua). Nó trả lời *"các con số là gì"* chứ không trả lời
*"đang xảy ra chuyện gì và phải làm gì"*. Thiếu:

- **Phễu vòng đời hợp nhất** nối 2 tab thành một dòng chảy: `Lead → mua lần đầu → mua lại →
  trung thành → nguội → ngủ đông`, kèm **% chuyển đổi giữa các bước**.
- **Xu hướng / cohort retention** thay vì chỉ ảnh chụp một thời điểm.
- **Headline insight + cảnh báo** ở đầu trang (vd "92,6% người mua đã ngủ đông >365 ngày").
- **Drill-down**: click một nhóm → mở danh sách KH đã lọc (CTA hiện chỉ link `/crm/customers`
  trống vì list page chưa nhận URL param).
- **Tách độ tin dữ liệu**: chỉ số RFM/orders (tin được) vs chỉ số assessment (~2,2% đơn được
  điền — chỉ là mẫu) phải được phân biệt rõ để không gây hiểu nhầm là toàn cảnh.

## 2. Mục tiêu

1. **Perf:** p95 thời gian phản hồi cache-miss của `/report/summary` ≤ ~1,5s ở all-time;
   cache-hit ≤ 50ms. Bỏ toàn bộ vòng lặp Python trên tập order; phân khúc đọc từ snapshot.
2. **Bức tranh toàn cảnh:** trang mở ra là thấy ngay phễu vòng đời + 3–5 headline insight,
   mỗi nhóm số liệu drill-down được sang danh sách KH.
3. Không phá nghiệp vụ đã chốt (ranh giới 2 trạng thái, đơn vị = Customer).

## 3. Phần A — Tối ưu hiệu năng (data layer)

### A1. SQL-first: thay materialize+Counter bằng aggregation

Chuyển toàn bộ distribution (giới tính, tỉnh, tuổi-bucket, potential, used_massage,
CODE_FIELDS, ARRAY_FIELDS, brand, need, crosstabs) sang **một/vài câu `GROUP BY` +
`count(*) FILTER`** trên DB — đúng kiểu `customer_behavior_report.py` đang làm. Không kéo
`rows` về Python. Age-bucket và normalize-brand đẩy vào SQL `CASE`/`lower(trim())`.

> Trường hợp khó đẩy SQL (vd nhóm tuổi theo regex `age_text`): cân nhắc cột chuẩn hóa
> `assessment.age_years` (smallint) đánh sẵn khi ghi assessment, thay vì parse runtime.

### A2. Snapshot phân khúc (bỏ classify realtime)

Tạo bảng `customer_segment_snapshot(customer_id PK, segment_code, total_orders,
total_spent, last_purchase_at, has_problem, updated_at)` cập nhật bằng **cron/Celery**
(vd mỗi 1–4h, hoặc incremental theo order mới). Report và `list_profiles` JOIN/đọc thẳng
cột `segment_code` → filter `segment` thành **WHERE có index**, không còn tính toàn tập.

> Lựa chọn thay thế nhẹ hơn: materialized view `REFRESH ... CONCURRENTLY`. Chọn bảng
> snapshot nếu cần incremental; chọn MV nếu chấp nhận refresh toàn phần định kỳ.

### A3. Quick wins (làm trước, độc lập)

- Tách `latest_sales_map(customer_ids) -> {cid: sales_status_code}` bằng `.values()` +
  `DISTINCT ON`, **bỏ prefetch needs** khỏi đường report (giữ `_latest_order_map` cũ cho
  `list_profiles`).
- Gộp 2 service: tránh chạy `Customer.objects.count()` 2 lần; chia sẻ `total_customers`,
  `params`, và tập `customer_ids` giữa report & behavior.
- Rà index: `orders(customer_id, created_at)`, partial index
  `WHERE status=3 AND invoice_status<>2`, `order_assessments(order_id)`.

### A4. Precompute + cache phân tầng

- Cron warm cache cho tổ hợp filter phổ biến: all-time toàn kênh, all-time × từng kênh.
- Giữ cache key hiện tại; nâng TTL khi đã precompute. Cân nhắc invalidate theo sự kiện
  (order mới trong ngày) thay vì chỉ TTL.

## 4. Phần B — Bức tranh toàn cảnh (UX + data contract)

### B1. Khối "Tổng quan điều hành" (đầu trang, luôn hiện)

- **Phễu vòng đời hợp nhất** (1 biểu đồ funnel/sankey): `Tổng KH → Lead → Người mua →
  Mua lại → Trung thành`, song song nhánh suy giảm `Active → Nguội → Nguy cơ → Ngủ đông`.
  Mỗi cạnh ghi **% chuyển đổi**.
- **3–5 thẻ headline insight** tự sinh từ ngưỡng (vd dormant_rate > 80% → cảnh báo đỏ
  "Retention thấp: X% người mua ngủ đông >365 ngày → ưu tiên tái kích hoạt").
- Mỗi KPI/segment có nút **→ Xem danh sách** (drill-down, B3).

### B2. Cohort retention (tab mới hoặc khối trong Tổng quan)

Bảng cohort theo tháng acquire × tháng mua lại (heatmap %): trả lời "khách mua tháng N có
bao nhiêu % quay lại ở N+1, N+2...". Đây là thứ snapshot không cho thấy.

### B3. Drill-down sang danh sách KH

Cho `CustomersPage` đọc **URL param** (`?segment=`, `?lifecycle=`, `?channel=`, kỳ) và
pre-apply filter. Gỡ TODO/CTA link trống hiện tại. Đây là mảnh nối "báo cáo → hành động".

### B4. Phân lớp độ tin dữ liệu

- Nhóm chỉ số **RFM/orders** (tin được) tách khối riêng, là phần chính.
- Nhóm **persona/assessment** đặt dưới nhãn rõ "Dựa trên N đơn có đánh giá (X% tổng đơn)"
  + badge độ phủ, để người đọc không nhầm là toàn cảnh.
- Giữ lại 2 tab Trước/Sau mua như "chi tiết", nhưng Tổng quan mới là mặt tiền.

### B5. Data contract mới (BE trả thêm)

```jsonc
{
  "headline_funnel": { "stages": [{ "key", "label", "count", "conv_from_prev_pct" }] },
  "insights": [{ "level": "warn|info|ok", "title", "metric", "cta": { "type", "params" } }],
  "cohort_retention": { "rows": [{ "cohort": "2026-01", "sizes": [n0, n1, ...] }] },
  "data_coverage": { "assessment_orders": 11292, "total_orders": 513514, "pct": 2.2 }
  // ... giữ nguyên các block distribution hiện có, chuyển sang nguồn SQL-first
}
```

## 5. Phạm vi & không làm

- **Không** đổi định nghĩa ranh giới 2 trạng thái, đơn vị phân tích, ngưỡng RFM.
- **Không** đụng các trang khác ngoài report + (mở rộng nhỏ) `CustomersPage` để nhận param.
- Cohort/insight chỉ trên dữ liệu `orders` (tin được); không bịa insight từ assessment thưa.

## 6. Kế hoạch Sprint

### Sprint 1 — Nền hiệu năng + data contract (perf-first)
- [ ] A3 Quick wins: bỏ prefetch thừa, gộp service, rà index.
- [ ] A1 SQL-first: chuyển distribution + crosstabs sang `GROUP BY`/`FILTER`.
- [ ] A2 Snapshot phân khúc + cron cập nhật; chuyển filter `segment` sang WHERE-indexed.
- [ ] A4 Precompute warm-cache + invalidate theo sự kiện.
- [ ] Chốt & freeze **data contract B5** (kể cả field cho Sprint 2) để FE/BE không sửa 2 lần.
- [ ] Unit test cho từng aggregator; benchmark p95 trước/sau.

### Sprint 2 — Bức tranh toàn cảnh (UX)
- [ ] B1 Khối Tổng quan điều hành: funnel hợp nhất + headline insight.
- [ ] B2 Cohort retention.
- [ ] B3 Drill-down: `CustomersPage` nhận URL param + CTA từ report.
- [ ] B4 Phân lớp độ tin dữ liệu + badge độ phủ.
- [ ] Test FE (render funnel/insight rỗng & có data), e2e drill-down.

## 7. Rủi ro & mở
- Snapshot phân khúc có thể lệch tới ~vài giờ so realtime — chấp nhận cho báo cáo (ghi rõ
  "cập nhật lúc HH:mm"). Nếu cần realtime tuyệt đối cho 1 KH, đã có trang profile riêng.
- Funnel/cohort cần kiểm định số với SQL thủ công trước khi tin (tránh lặp lỗi "lệch filter").
- **Mở:** chu kỳ refresh snapshot (1h vs 4h vs incremental) — chốt ở đầu Sprint 1 theo tải DB.
- **Mở:** funnel dạng sankey hay bar-funnel — chốt khi dựng UI Sprint 2.
