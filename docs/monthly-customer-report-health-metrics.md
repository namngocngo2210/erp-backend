# Spec — Báo cáo docx KH hàng tháng: bổ sung chỉ số sức khỏe tập khách hàng

> Mở rộng command `generate_monthly_customer_report` (MR !810): file .docx hiện chỉ nói về
> **chất lượng ghi note + phân bố assessment** (độ phủ ~2.2%) — chưa trả lời được câu hỏi
> của BGĐ về **sức khỏe tập khách hàng**. Bổ sung 7 mảng chỉ số tính từ dữ liệu đơn hàng
> (GTC), chuyển nội dung cũ xuống phụ lục.
>
> - Ngày tạo: 2026-07-06
> - Nền: `backend/apps/customers/report_doc/` (service, trend, tiers, charts, docx_builder),
>   `report_trends.py`, `report_cohort.py`, `channel_quality.py`, `behavior_classify.py`.
> - Đối tượng đọc: Ban giám đốc (chiến lược, đọc offline hàng tháng).
> - Bài học phải giữ: vụ runaway query 2026 — mọi query mới là **1 lượt SQL có window**,
>   bọc `statement_timeout`, KHÔNG gọi `summary()` lặp theo tháng.

---

## 1. Quyết định kiến trúc

| # | Quyết định |
|---|---|
| Q1 | **Filter GTC đồng nhất** cho mọi chỉ số mới: `status=3 AND invoice_status IS DISTINCT FROM 2 AND deleted_at IS NULL AND branch_id NOT IN EXCLUDED_BRANCH_IDS` — trùng định nghĩa `report_trends` / behavior report. |
| Q2 | **Anchor theo tháng báo cáo, không theo now()**: các query mới trong `health.py` nhận `end_month` làm mốc → chạy lại cho tháng cũ vẫn ra đúng số (khác `report_trends.compute` đang anchor `now()`, không reuse trực tiếp mà **adapt SQL** sang anchor tham số). |
| Q3 | **Vòng đời + value-at-risk tự tính as-of cuối tháng** trong `health.py` (bucket theo `days_since_last_gtc_order` so với mốc `ref_date`), KHÔNG reuse `CustomerBehaviorReportService`/`ReactivationService` (2 service này anchor now()) → có được cả số cuối tháng này lẫn cuối tháng trước ⇒ delta MoM "tốc độ rò rỉ" chính xác, báo cáo reproducible. Ngưỡng bucket import từ `behavior_classify` (ACTIVE/COOLING/AT_RISK). |
| Q4 | **Pareto tính trên trailing 12 tháng** doanh thu (không phải 1 tháng) — ổn định, ít nhiễu mùa vụ. |
| Q5 | **Cohort + chất lượng kênh reuse nguyên trạng** `report_cohort` / `channel_quality` (compute trực tiếp, không phụ thuộc snapshot Redis vì command chạy offline). Chấp nhận 2 mục này anchor theo thời điểm chạy lệnh — ghi chú ngay trong docx. |
| Q6 | **Nội dung cũ giữ nguyên logic, chuyển xuống Phụ lục** kèm badge độ phủ dữ liệu (`% đơn có assessment`) để người đọc tự cân nhắc độ tin cậy. Mẫu số tier vẫn = TẤT CẢ đơn (chủ đích cũ, không đổi). |
| Q7 | `docx_builder.py` sẽ vượt 300 dòng → tách: `sections_health.py` (§3.1–3.7) + `sections_appendix.py` (§3.9), `docx_builder.py` chỉ giữ `ReportContext` + orchestrate. Chart mới đặt ở `charts_health.py`. |
| Q8 | **Ngoài phạm vi**: đưa command vào celery beat (vẫn chạy tay); LTV dự báo (predictive); thay đổi trang web báo cáo KH. |

---

## 2. Data layer mới — `report_doc/health.py`

Tất cả hàm bọc `statement_timeout` (dùng `apps.common.db_guards`), trả dataclass/dict thuần.

### 2.1 `kpi_series(end_month, months=13) -> list[MonthKpi]`
Adapt SQL của `report_trends.compute` sang anchor `end_month`. Mỗi tháng:
`revenue, orders, buyers, new_customers` (KH có đơn GTC ĐẦU TIÊN trong tháng — CTE `first_orders`
tính trên **toàn lịch sử**, không giới hạn window), `repeat_buyers` (≥2 đơn GTC trong tháng),
`aov = revenue/orders`. 13 tháng để có Δ YoY cho tháng báo cáo.

### 2.2 `new_vs_returning(end_month, months=12) -> list[MonthSplit]`
Mỗi tháng: `revenue_new / revenue_returning`, `buyers_new / buyers_returning`.
KH "mới" trong tháng m ⇔ tháng đơn GTC đầu tiên == m (cùng CTE `first_orders` với 2.1 —
gộp chung 1 query với 2.1 nếu gọn, tách hàm ở tầng Python).

### 2.3 `lifecycle_at(ref_date) -> LifecycleSnapshot`
1 query: per-customer `last_gtc = max(created_at)`, `total_spent = sum(total_after_discount)`
(toàn lịch sử GTC), bucket theo `ref_date - last_gtc`:
`active ≤ ACTIVE_MAX_DAYS < cooling ≤ COOLING_MAX_DAYS < at_risk ≤ AT_RISK_MAX_DAYS < dormant`.
Trả: count per bucket + `value_at_risk = sum(total_spent)` của at_risk + dormant.
Gọi 2 lần: cuối tháng báo cáo + cuối tháng trước → delta.

### 2.4 `repurchase_cycle(end_month) -> CycleStats`
Cặp đơn GTC liên tiếp per customer (`lag() OVER (PARTITION BY customer_id ORDER BY created_at)`),
lấy các cặp có đơn sau nằm trong 24 tháng trước `end_month`:
`median_days` (`percentile_cont(0.5)`), `p25`, `p75`, `n_pairs`.

### 2.5 `pareto(end_month) -> ParetoStats`
Doanh thu GTC trailing 12 tháng per customer → % doanh thu của top 10% / top 20% KH
(xếp hạng bằng `cume_dist()` hoặc `ntile`), kèm `total_customers`, `total_revenue`.

---

## 3. Cấu trúc file docx mới (9 phần)

| § | Nội dung | Nguồn |
|---|---|---|
| 3.1 | **KPI sức khỏe tháng** — bảng: doanh thu, buyers, KH mới, KH mua lại, tỷ lệ mua lại (%), AOV; cột tháng này / Δ MoM / Δ YoY | `health.kpi_series` |
| 3.2 | **Xu hướng 12 tháng** — chart 1: doanh thu (bar) + buyers (line, trục phụ); chart 2: tỷ lệ mua lại (line) | `health.kpi_series` |
| 3.3 | **Doanh thu KH mới vs cũ** — stacked bar 12 tháng + bảng tháng này (% doanh thu từ mua lại, Δ MoM) | `health.new_vs_returning` |
| 3.4 | **Cơ cấu vòng đời + value-at-risk** — bảng 4 bucket (count, %, Δ MoM) + dòng VAR (₫, Δ) | `health.lifecycle_at` ×2 |
| 3.5 | **Cohort quay lại** — bảng 6 cohort gần nhất × tỷ lệ quay lại M+1/M+2/M+3 (ghi chú anchor thời điểm chạy) | `report_cohort` |
| 3.6 | **Chu kỳ mua lại & độ tập trung** — median/p25/p75 ngày giữa 2 đơn; Pareto top 10%/20% chiếm X% doanh thu 12T | `health.repurchase_cycle`, `health.pareto` |
| 3.7 | **Chất lượng kênh** — top 5 kênh theo doanh thu/khách: acquired, repeat %, revenue/customer, dormant % (ghi chú anchor) | `channel_quality` |
| 3.8 | **Nhận xét & khuyến nghị** — rule-based mở rộng (giữ rule note cũ, thêm §5) | context |
| 3.9 | **Phụ lục: chân dung KH** — toàn bộ nội dung cũ (tier note + 4 bảng phân bố assessment + tier pie/trend) + badge độ phủ | code cũ |

Small-sample warning (`< 30` assessment) chỉ còn áp cho Phụ lục (KPI mới không phụ thuộc assessment).

## 4. Charts mới — `charts_health.py` (matplotlib, PNG bytes, style đồng nhất `charts.py`)

`revenue_buyers_chart`, `repeat_rate_line`, `new_vs_returning_stacked`, `lifecycle_bar` (grouped bar tháng này vs trước).

## 5. Rule nhận xét tự động bổ sung (`_auto_insights` mở rộng, tách hàm riêng theo mảng)

- Doanh thu / buyers MoM đổi > ±10% → nêu số.
- Tỷ lệ mua lại giảm ≥ 2 điểm% MoM → cảnh báo giữ chân.
- % doanh thu từ KH cũ < 20% → "tăng trưởng phụ thuộc khách mới".
- Dormant tăng > 1 điểm% MoM hoặc VAR tăng > 10% → cảnh báo rò rỉ.
- Pareto top 10% > 50% doanh thu → cảnh báo tập trung.
- Kênh có dormant_pct cao nhất & repeat_pct thấp nhất → nêu tên.

## 6. Test

- Unit DB-backed (pytest, image `desi-order-web-test`, `POSTGRES_DB=desi_<unique>`):
  - `health.py`: mỗi hàm 1 test với fixture đơn GTC dựng tay (new vs returning, bucket ranh giới, median, pareto, loại đơn cancelled-invoice/branch excluded).
  - `lifecycle_at` reproducibility: cùng data, 2 `ref_date` khác nhau ra bucket khác nhau đúng kỳ vọng.
- Smoke test builder: docx bytes mở được bằng `python-docx`, đủ 9 heading, chạy được khi `prev_stats=None` và khi data rỗng.
- Test command hiện có (`test_generate_monthly_customer_report_command.py`) cập nhật theo cấu trúc mới.

## 7. Sprint

### Sprint 1 — Data layer (`health.py` + tests)
- [x] `health.py`: kpi_series, new_vs_returning, lifecycle_at, repurchase_cycle, pareto (+ dataclasses)
- [x] Unit tests DB-backed cho 5 hàm
- **Tiến độ**: hoàn thành 2026-07-06

### Sprint 2 — Trình bày (charts, sections, insights, rewire)
- [x] `charts_health.py` (4 chart)
- [x] `sections_health.py` (§3.1–3.7), `sections_appendix.py` (§3.9), rewire `docx_builder.py` + `service.py`
- [x] Mở rộng insights (§5)
- [x] Cập nhật smoke test + test command; chạy full test module customers
- **Tiến độ**: hoàn thành 2026-07-06

### Ghi chú sau mỗi sprint
- Bỏ hàm `new_vs_returning`/dataclass `MonthSplit` riêng như spec 2.2 dự kiến — gộp thẳng
  `revenue_new` (và các property dẫn xuất `revenue_returning`, `buyers_returning`,
  `pct_revenue_returning`) vào `MonthKpi` trong `kpi_series`, vì cùng 1 CTE `firsts` với 2.1 nên
  tách hàm riêng chỉ thêm 1 query thừa không cần thiết.
- `health.py` phải trim còn 297 dòng để giữ ràng buộc < 300 dòng/file (Q7) — refactor ở task 4,
  không đổi hành vi.
- Test `health_insights` (Sprint 2) phủ đủ cả 6 rule cảnh báo ở mục 5 (doanh thu/buyers MoM,
  mua lại giảm ≥2 điểm%, %doanh thu KH cũ <20%, dormant/VAR tăng, Pareto top10% >50%, kênh
  dormant cao nhất) sau khi sửa theo code review ở task 6.
- Task 9 (verify toàn cục): full suite `apps/customers/tests` phát hiện
  `test_report_doc_charts.py` (4 test, có từ trước Sprint 1) bị vỡ do rewire `docx_builder.py`
  ở Sprint 2 (`_distribution_table` chuyển sang `sections_appendix`, `ReportContext` thêm field
  `health` bắt buộc) — task rewire chỉ chạy 2 file test liên quan nên không phát hiện; đã sửa
  test để khớp API mới (không đổi production code). Sau fix: toàn bộ `apps/customers/tests`
  pass trừ 2 fail baseline đã biết trước (`test_report_trends`, không liên quan feature này).
  Ruff sạch trên `report_doc/` + `test_report_doc_health.py`.
- Rule kênh xấu trong `health_insights` chỉ xét `dormant_pct` cao nhất (spec §5 ghi thêm
  repeat_pct thấp nhất — simplification chủ đích).
- §3.7 chọn top 5 kênh theo số KH acquired (kế thừa sort của `channel_quality`), docx có ghi
  chú tiêu chí; spec ghi "theo doanh thu/khách".
- Review toàn nhánh (task 9): sửa 2 lệch chuẩn trong `health.py` — (1) `kpi_series` truncate
  tháng theo UTC của connection trong khi window biên theo giờ VN (+07), đơn 00:00–07:00 giờ
  VN ngày mùng 1 bị lệch sang tháng trước → chuẩn hóa `date_trunc` theo TZ hệ thống
  (`AT TIME ZONE`) ở cả 2 CTE `gtc`/`firsts`; (2) `lifecycle_at` lệch 1 ngày so với
  `behavior_classify.classify_lifecycle` (mốc `ref` là nửa đêm SAU ref_date nên ngưỡng
  ≤89/179/364 thay vì ≤90/180/365) → cộng thêm 1 ngày vào mỗi `make_interval` để khớp biên
  active/cooling/at_risk.
