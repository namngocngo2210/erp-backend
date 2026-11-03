# Thiết kế: Báo cáo khách hàng theo 2 trạng thái (Trước mua / Sau mua)

**Ngày:** 2026-06-15
**Branch:** `feat/customer-behavior-report`
**Trạng thái:** Spec — chờ duyệt để chuyển sang implementation plan
**Tiền thân:** Sửa lại trang báo cáo tại `2026-05-26-customer-report-dashboard-design.md`
(`CustomerReportService.summary`) và phần mở rộng `2026-05-26-customer-portrait-dashboard-expansion-design.md`.

## 1. Bối cảnh & vấn đề

Trang báo cáo khách hàng hiện tại (`apps/customers/report_service.py` →
`/api/customers/report/summary` → `frontend/src/pages/CustomerReportPage.tsx`) lấy
**đơn vị phân tích = "mỗi đơn có assessment"** (`Order.objects.filter(assessment__isnull=False)`).

Chẩn đoán dữ liệu production (2026-06-15) cho thấy cách tiếp cận này sai gốc cho mục tiêu
phân tích hành vi:

| Chỉ số | Giá trị | Ghi chú |
|---|---|---|
| Tổng KH (chưa xóa) | 740.686 | |
| KH có ≥1 đơn GTC (status=3) | 422.407 | "đã mua" |
| KH chưa từng có đơn GTC | 318.279 | **43%** — lead thuần |
| KH mua lại ≥2 đơn GTC | 32.868 | **8% người mua** |
| `order_assessments` tồn tại | 11.292 / 513.514 đơn | **2,2%** đơn có assessment |
| `sales_status_code = created_order` | 8.288 | **73%** assessment chỉ là "đã tạo đơn" |
| `post_purchase_rating_code` có giá trị | 26 | gần như rỗng |
| `post_purchase_feedback_text` có giá trị | 6 | gần như rỗng |

**Recency của 422.407 người mua (theo đơn GTC cuối):**

| Nhóm recency | Số KH | % người mua |
|---|---|---|
| ≤90 ngày (Active) | 14.749 | 3,5% |
| 90–180 ngày (Nguội) | 7.249 | 1,7% |
| 180–365 ngày (Nguy cơ rời) | 9.179 | 2,2% |
| >365 ngày (Ngủ đông/Mất) | **391.230** | **92,6%** |

Median chi tiêu 149k; chỉ 4.968 KH chi ≥2tr.

**Kết luận:**
1. `order_assessment` quá thưa và lệch (73% là `created_order`, trường sau-mua gần như rỗng) →
   không đủ làm nguồn phân tích hành vi; báo cáo hiện tại chỉ là **bảng đếm phân bố persona trên mẫu 2,2%**.
2. Nguồn hành vi sau-mua thật nằm ở bảng **`orders`** (RFM). Câu chuyện chính: mô hình **gần như
   mua-một-lần, retention cực thấp** (92,6% ngủ đông >365 ngày). Cơ hội lớn nhất = tái kích hoạt/resale.
3. Báo cáo cần tách rõ **2 trạng thái khách hàng**: trước mua (phễu lead/chuyển đổi) và sau mua
   (vòng đời RFM/giữ chân).

## 2. Mục tiêu

Sửa trang báo cáo khách hàng để trả lời:

**Trước mua (Lead):** Có bao nhiêu lead? Đang nằm ở bước nào của pipeline? Lý do chưa chốt / rào cản
chính là gì? Tỷ lệ chuyển đổi lead → mua? Pain/nhu cầu nào nổi bật để target?

**Sau mua (Khách hàng):** Phân bố vòng đời (Active/Nguội/Nguy cơ rời/Ngủ đông)? Tỷ lệ mua lại? Nhóm
VIP/giá trị cao là ai? Bao nhiêu KH có vấn đề (hoàn/khiếu nại)? Chân dung persona *của người đã mua*?

## 3. Phạm vi & quyết định đã chốt

- **Đơn vị phân tích:** **Customer** (740k), thay cho "đơn có assessment".
- **Ranh giới 2 trạng thái:**
  - **Sau mua** = có ≥1 đơn GTC (`status = DELIVERED_SUCCESS_ORDER_STATUS = 3`, loại `invoice_status =
    CANCELLED_INVOICE_STATUS`, loại `branch_id ∈ EXCLUDED_BRANCH_IDS`) — đồng bộ với
    `CustomerProfileService._metrics_map`.
  - **Trước mua** = chưa có đơn GTC nào.
  - Hai nhóm bù trừ kín toàn bộ 740k.
- **Nguồn dữ liệu:** hành vi (RFM, pipeline) tính từ `orders`; `order_assessment` là **lớp phủ làm giàu**
  (pipeline stage, persona, rào cản, pain) — chỉ áp dụng nơi có dữ liệu, không lọc bỏ KH thiếu assessment.
- **Filter:** giữ **channel**; **date range** áp theo *kỳ acquire/active* (mục 6). Lifecycle là "ảnh chụp
  hiện tại" (recency so với `now`).
- **Hiệu năng:** aggregation **thuần SQL** (`count(*) FILTER (...)`, `GROUP BY customer_id`), trả về số
  liệu nhỏ — **không loop toàn tập trong Python** (đối lập với nhánh `segment` của
  `CustomerProfileService.list_profiles` đang materialize toàn bộ id, vốn là điểm chậm đã biết). Kết quả
  cache Redis 10 phút.
- **Vị trí & quyền:** giữ nguyên route `/customers/report` và quyền hiện hành của
  `customer_report_summary` (`@permission_classes([IsAuthenticated, require_section("dashboard")])`).
- **Không đổi schema DB.** Không thêm bảng/cột. Chỉ thêm index nếu đo thấy thiếu.

## 4. Định nghĩa trạng thái

### 4.1 Trước mua — pipeline lead

Phân loại theo `sales_status_code` của **đơn mới nhất** của KH (qua
`CustomerProfileService._latest_order_map`). Nhóm mã (tái dùng/đồng bộ `crm_catalog` &
`segmentation.NURTURE_SALES_STATUS_CODES`):

| Bước pipeline | Mã `sales_status_code` |
|---|---|
| Chưa tiếp cận | không có assessment, hoặc `created_order` |
| Đang tư vấn | `need_more_consulting`, `scheduled`, `call_back_later`, `closed_scheduled_date` |
| Không liên hệ được | `no_answer_1/2/3`, `unreachable`, `wrong_number` |
| Từ chối | `not_buying`, `cancelled` |
| Rác | `spam`, `duplicate` |

Lưu ý trung thực: phần lớn lead rơi vào "Chưa tiếp cận" — bản thân đó là phát hiện về **lỗ hổng nhập liệu**,
hiển thị rõ chứ không ẩn.

### 4.2 Sau mua — vòng đời RFM

**Trạng thái chính theo recency** (đơn GTC cuối so với `now`), mutually exclusive:

| Trạng thái | Tiêu chí | Số thực (toàn tập) |
|---|---|---|
| Active | ≤90 ngày | 14.749 |
| Nguội | 90–180 ngày | 7.249 |
| Nguy cơ rời | 180–365 ngày | 9.179 |
| Ngủ đông / Mất | >365 ngày | 391.230 |

**Cross-cut flags** (độc lập với recency, một KH có thể mang nhiều cờ):
- **VIP/Trung thành:** `delivered_count ≥ 2` **hoặc** `total_spent ≥ 2.000.000` (ngưỡng
  `segmentation.LOYAL_MIN_DELIVERED` / `LOYAL_TOTAL_SPENT_THRESHOLD`).
- **Mua lần đầu** (`delivered_count = 1`) vs **Mua lại** (`≥2`).
- **Có vấn đề:** tái dùng `CustomerLoyaltyService._customers_with_problem_signal` (hoàn/khiếu nại/rating
  xấu/bảo hành).

Ngưỡng lấy từ `segmentation.py` để nhất quán với phân khúc S1–S7 đã có.

## 5. Cấu trúc trang (frontend)

`CustomerReportPage.tsx` thêm `Tabs` (shadcn), tái dùng `DistributionTable`,
`DistributionBar/Donut`, `StackedBarChart` sẵn có.

- **Tab "Tổng quan":** KPI toàn tập (tổng KH, % trước/sau mua, tỷ lệ chuyển đổi lead→mua, tỷ lệ mua lại,
  % ngủ đông, AOV) + 1 phễu lớn: Lead → Đã mua → Mua lại → còn Active.
- **Tab "Trước mua (Lead)":** bar phân bố pipeline (4.1); top lý do chưa chốt (`not_closed_reason`); top
  rào cản (`purchase_barrier_codes`); top pain/nhu cầu (`pain_level`, `OrderNeed`). KPI: tổng lead, tỷ lệ
  có assessment, tỷ lệ chuyển đổi.
- **Tab "Sau mua (Khách hàng)":** bar lifecycle (4.2) + cohort recency; donut mua lần đầu vs mua lại; thẻ
  VIP/giá trị; thẻ "có vấn đề"; mức hài lòng (`post_purchase_rating`, nêu rõ độ phủ thấp); **persona chân
  dung người đã mua** = các distribution hiện có (giới tính, tuổi, nghề, lifestyle, nguồn…) đặt vào đây.

Mỗi trạng thái có **CTA + link sang `/customers` lọc sẵn theo trạng thái đó** (biến report thành điểm khởi
đầu hành động, ví dụ "391k KH ngủ đông → mở danh sách để chạy chiến dịch reactivation").

## 6. Backend

### 6.1 Endpoint

Giữ `GET /api/customers/report/summary` nhưng mở rộng response (thêm các khối `overview`, `pre_purchase`,
`post_purchase`). Cân nhắc thêm endpoint phụ `GET /api/customers/report/behavior` nếu payload quá lớn —
**quyết định trong implementation plan**; mặc định gộp vào summary để FE 1 lần gọi.

Query params giữ nguyên: `date_from`, `date_to`, `channel`, `segment` (optional).

### 6.2 Service

File mới `apps/customers/customer_behavior_report.py` — `CustomerBehaviorReportService.summary(...)`.
`report_service.py` (persona distributions) được giữ và **gọi vào tab Sau mua** thay vì là toàn bộ trang.

**Thuật toán (thuần SQL, không loop toàn tập):**

1. **RFM theo người mua** — 1 query `GROUP BY customer_id` trên orders GTC:
   `Count(id)`, `Sum(total_after_discount)`, `Max(created_at)`, `Min(created_at)`. Bọc trong CTE/subquery
   rồi đếm lifecycle + flags bằng `count(*) FILTER (WHERE ...)` ở SQL → trả ~20 con số. (Pattern đã đo chạy
   tức thì trên 422k.)
2. **Tổng lead** = `total_customers − distinct buyers` (2 query đếm).
3. **Pipeline lead** = group `sales_status_code` của đơn mới nhất, chỉ trên KH không-mua. Dùng
   `DISTINCT ON (customer_id) ... ORDER BY created_at DESC` hoặc subquery tương quan (đã có ở
   `_latest_order_map`).
4. **Persona distributions** (tab Sau mua) — tái dùng `CustomerReportService` nhưng giới hạn vào người mua.
5. **Có vấn đề** — `CustomerLoyaltyService._customers_with_problem_signal` (đếm).

Bộ lọc channel/date áp ở bước (1)–(3). Loại `EXCLUDED_BRANCH_IDS` xuyên suốt.

### 6.3 Xử lý date filter (quyết định đã chốt)

- Lifecycle/recency = ảnh chụp `now` (không phụ thuộc date range).
- Khi có `date_from/date_to`: phạm vi áp cho **kỳ phát sinh đơn** — KPI "chuyển đổi", "lead mới", "đơn
  trong kỳ" tính theo `created_at` trong khoảng; còn phân bố lifecycle giữ as-of-now nhưng chỉ trên tập KH
  có hoạt động trong kỳ. UI ghi chú rõ "Vòng đời tính tại thời điểm hiện tại".

### 6.4 Hiệu năng

- Chỉ aggregate query trả số nhỏ; xác minh dùng index `orders(status, customer_id)` /
  `customer_created_at_idx`. Đo bằng `EXPLAIN ANALYZE`, thêm index nếu thiếu (migration index-only).
- Cache Redis key gồm `(date_from, date_to, channel, segment)` TTL 600s. Invalidate theo TTL (report không
  cần realtime).

## 7. Kiểm thử

- Unit test `classify_lifecycle()` + `classify_lead_stage()`: bảng case (boundary 90/180/365 ngày, ngưỡng
  2tr, 1 vs ≥2 đơn).
- Test aggregation service với factory orders/assessment: dựng vài KH ở mỗi trạng thái → assert đếm.
- Test endpoint: quyền (`require_section("dashboard")`), shape response, filter channel/date.
- Test hiệu năng nhẹ: assert số query không tăng theo số KH (no N+1 / no Python loop toàn tập).

## 8. Ngoài phạm vi (YAGNI / Phase sau)

- Bảng/materialized view `customer_behavior` tính nền (Hướng C) — chỉ làm nếu report đo thấy chậm hoặc cần
  nuôi luôn trang danh sách KH.
- Vá lỗ hổng nhập liệu sau-mua (bắt buộc nhập rating/feedback) — vấn đề quy trình riêng, không thuộc spec
  báo cáo này (sẽ ghi nhận như khuyến nghị).
- Export báo cáo, lịch gửi Lark.

## 9. Tiêu chí hoàn thành

- Trang `/customers/report` hiển thị 3 tab; số liệu khớp truy vấn kiểm chứng (Active 14.749, Ngủ đông
  391.230… ± do filter/thời điểm).
- Báo cáo chạy trên toàn 740k KH, không loop Python toàn tập; thời gian phản hồi (có cache) chấp nhận được.
- Mỗi trạng thái có link điều hướng sang danh sách KH lọc sẵn.
- Unit test pass; không đổi schema.
