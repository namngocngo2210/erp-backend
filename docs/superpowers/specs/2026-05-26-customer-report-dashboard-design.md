# Thiết kế: Trang báo cáo khách hàng (Customer Report Dashboard)

**Ngày:** 2026-05-26
**Branch:** `customer-report-dashboard-20260526`
**Trạng thái:** Spec — chờ duyệt để chuyển sang implementation plan

## 1. Mục tiêu

Xây dựng một trang dashboard phân tích khách hàng (tương tự dashboard báo cáo Sale tại `/dashboard`), trả lời các câu hỏi nghiệp vụ:

1. Khách hàng phần lớn rơi vào **phân khúc** nào (S1–S7), **độ tuổi** nào, **nghề nghiệp** gì.
2. **Phản hồi** của khách về sản phẩm như thế nào (định lượng + định tính).
3. **Mục đích mua** (nhóm nhu cầu) phổ biến.
4. **Rào cản** mua hàng chủ yếu.

## 2. Phạm vi & quyết định đã chốt

- **Đơn vị phân tích:** mỗi *đơn có assessment* trong kỳ. Lọc theo `Order.created_at`, kênh. Đơn không có assessment bị loại khỏi các chart dựa trên persona. Giá trị rỗng/null gom vào nhóm `"unknown"` ("Chưa xác định").
- **Phân nhóm độ tuổi:** parse số trong `age_text` (free text) → các khoảng `18-25`, `26-35`, `36-45`, `46-55`, `55+`. Giá trị không parse được → `"other"` ("Khác").
- **Bộ lọc:** khoảng thời gian (theo `created_at`), kênh (channel), phân khúc (S1–S7).
- **Vị trí & quyền:** route mới `/customers/report`, dùng chung quyền `has_section_access("dashboard")` (ai xem được báo cáo Sale thì xem được).
- **Kiến trúc:** Hướng A — endpoint tổng hợp riêng ở backend, frontend chỉ render. Không tổng hợp ở frontend.

## 3. Nguồn dữ liệu (đã xác minh trong codebase)

| Thuộc tính | Vị trí | Trường | Kiểu |
|---|---|---|---|
| Phân khúc S1–S7 | `apps/customers/segmentation.py` | `classify_segment(...)` (tính, không lưu) | computed |
| Độ tuổi | `OrderAssessment` | `age_text` | CharField(32), free text |
| Nghề nghiệp | `OrderAssessment` | `occupation_code` | CharField, có index, 8 mã (`OCCUPATION_OPTIONS`) |
| Phản hồi SP (định lượng) | `OrderAssessment` | `post_purchase_rating_code` | CharField, có index, 3 mã (`POST_PURCHASE_RATING_OPTIONS`: good/average/poor) |
| Phản hồi SP (định tính) | `OrderAssessment` | `post_purchase_feedback_text` | TextField, free text |
| Mục đích mua | `OrderNeed → NeedType → NeedGroup` | `needs` (M2M qua `OrderNeed`) | FK |
| Rào cản | `OrderAssessment` | `purchase_barrier_codes` | ArrayField, 8 mã (`PURCHASE_BARRIER_OPTIONS`) |

Catalog mã→nhãn tiếng Việt: `apps/orders/crm_catalog.py`.

## 4. Backend

### 4.1 Endpoint

```
GET /api/customers/report/summary
```

- Decorator: `@api_view(["GET"])`, `@permission_classes([IsAuthenticated])`.
- Chặn quyền: nếu `not request.user.has_section_access("dashboard")` → 403 `{"detail": "Bạn không có quyền xem báo cáo khách hàng."}`.
- Query params: `date_from` (YYYY-MM-DD), `date_to` (YYYY-MM-DD), `channel` (channel_id), `segment` (S1–S7). Tất cả optional.

### 4.2 Service

File mới: `apps/customers/report_service.py` — class `CustomerReportService` với method tĩnh `summary(*, date_from, date_to, channel, segment) -> dict`.

**Tập đơn cơ sở (base queryset):**
- `Order.objects.filter(deleted_at__isnull=True, assessment__isnull=False)`
- Áp dụng filter ngày trên `created_at` (tái dùng pattern `_apply_created_at_date_filters` nếu phù hợp, hoặc filter trực tiếp).
- Nếu có `channel`: filter `channel_id=channel`.
- Loại branch theo `EXCLUDED_BRANCH_IDS` (đồng bộ với `profile_service`).

**Gán phân khúc cho từng đơn:**
- Lấy `customer_ids` distinct từ base queryset.
- Tái dùng `CustomerProfileService._metrics_map(customer_ids)`, `CustomerService._customers_with_problem_signal(customer_ids)`, và sales_status mới nhất để gọi `segmentation.classify_segment(...)` cho mỗi khách → `segment_by_customer: dict[int, str]`.
- Mỗi đơn kế thừa phân khúc của khách sở hữu.
- Nếu có filter `segment`: chỉ giữ các đơn có `segment_by_customer[customer_id] == segment`.

**Các phân bố trả về** (mỗi item: `{code, label, count, percentage}`; `percentage` làm tròn theo tổng đơn của khối tương ứng):

| Key | Cách đếm | Ghi chú |
|---|---|---|
| `segment_distribution` | đếm đơn theo phân khúc của khách | nhãn lấy từ `CustomerSegmentCategory` |
| `age_distribution` | đếm đơn theo khoảng tuổi parse từ `age_text` | bucket cố định + `other` |
| `occupation_distribution` | `GROUP BY occupation_code` | null/"" → `unknown` |
| `feedback_rating_distribution` | `GROUP BY post_purchase_rating_code` | null → `unknown` |
| `need_group_distribution` | đếm **distinct (đơn, nhóm nhu cầu)** | 1 đơn nhiều nhóm → góp vào mỗi nhóm 1 lần |
| `barrier_distribution` | đếm **lượt** trong `purchase_barrier_codes` | 1 đơn nhiều rào cản → đếm nhiều lần (đơn vị: lượt, không phải đơn) |

**Định tính:** `feedback_samples`: list ~20 mẫu gần nhất, mỗi mẫu `{text, created_at, customer_code, rating_code}` — lọc `post_purchase_feedback_text != ""`, order by `created_at desc`.

**Totals:** `{total_records, total_customers}` — `total_records` = số đơn có assessment sau filter; `total_customers` = số khách distinct.

**Hiệu năng:**
- Phân bố cho trường có cấu trúc (`occupation_code`, `post_purchase_rating_code`, need group) dùng `values().annotate(Count())` — `GROUP BY` ở DB.
- `age_distribution` và `barrier_distribution`: lấy danh sách `age_text` / `purchase_barrier_codes` của các đơn rồi gom ở Python (parse/duyệt array). Bị giới hạn theo tập đơn đã filter nên không nặng.
- Phân khúc: 1 query metrics gộp + 1 query problem-signal cho tập khách distinct (giống `profile_service`).
- Tránh N+1: prefetch/`values` thay vì lặp object.

### 4.3 Route & view

- Thêm route trong `apps/customers/urls.py`: `path("report/summary", views.customer_report_summary)`.
- Thêm view `customer_report_summary(request)` trong `apps/customers/views.py` → gọi `CustomerReportService.summary(...)` và trả `Response(data)`.

### 4.4 Test

File mới `apps/customers/tests/test_customer_report_api.py`:
- 403 khi user không có quyền `dashboard`.
- Phân bố occupation/rating/need/barrier đúng số đếm với dữ liệu seed.
- Age bucketing: các `age_text` mẫu ("30", "28 tuổi", "trung niên") rơi đúng bucket / `other`.
- Barrier đếm theo lượt (đơn có 2 rào cản → +2).
- Filter date/channel/segment thu hẹp tập đúng.
- `totals` đúng.

## 5. Frontend

(React 18 + Vite SPA, Recharts 2.13, shadcn-style UI, lucide-react — đồng bộ `DashboardPage.tsx`.)

### 5.1 Định tuyến & điều hướng
- Thêm route `/customers/report` trong `frontend/src/lib/routes.ts` và wiring trong `App.tsx`.
- Thêm mục nav trong nhóm "Khách hàng".

### 5.2 API client
- Thêm hàm trong `frontend/src/lib/api/customers.ts`: `fetchCustomerReportSummary(filters)` gọi `/api/customers/report/summary?...` qua `fetchJson`, kèm cache TTL ở tầng gọi (giống cách `App.tsx` cache dashboard summary).
- Khai báo type `CustomerReportSummary` khớp response.

### 5.3 Trang `CustomerReportPage.tsx`
Bố cục từ trên xuống:
1. Tiêu đề + thanh filter: `DateFilter` (tái dùng) + select Kênh + select Phân khúc (S1–S7).
2. 2 KPI card: tổng đơn đánh giá (`total_records`), tổng khách distinct (`total_customers`).
3. Lưới chart (mỗi chart trong `Card`):
   - **Phân khúc** — donut (PieChart).
   - **Độ tuổi** — bar (BarChart).
   - **Nghề nghiệp** — bar ngang.
   - **Phản hồi SP** — donut (good/average/poor) + panel danh sách `feedback_samples` (text cuộn).
   - **Mục đích mua** — bar.
   - **Rào cản** — bar ngang (ghi chú "theo lượt").
- Dùng `CHART_COLORS` đồng bộ với DashboardPage. Loading state + empty state cho từng chart.

## 6. Các đơn vị (units) và ranh giới

- `CustomerReportService` (backend): nhận filter → trả dict phân bố. Phụ thuộc: `segmentation`, `CustomerProfileService._metrics_map`, `CustomerService._customers_with_problem_signal`, `crm_catalog`, `Order`/`OrderAssessment`/`OrderNeed`. Test độc lập qua DB seed.
- View `customer_report_summary`: chỉ lo HTTP + quyền, ủy quyền tính toán cho service.
- `fetchCustomerReportSummary` (frontend): chỉ lo gọi mạng + cache.
- `CustomerReportPage`: chỉ lo render từ data đã chuẩn hóa; không tự tổng hợp.

## 7. Ngoài phạm vi (YAGNI)

- Không phân tích cảm xúc/NLP cho `post_purchase_feedback_text` — chỉ hiển thị mẫu thô + rating có cấu trúc.
- Không export Excel/PDF (có thể bổ sung sprint sau).
- Không thêm section quyền mới (dùng chung `dashboard`).
- Không thay đổi model/migration (toàn bộ trường đã có sẵn).

## 8. Đã hoàn thành (2026-05-26)

Triển khai theo plan `docs/superpowers/plans/2026-05-26-customer-report-dashboard.md` (subagent-driven, TDD ở backend).

**Backend:**
- `apps/customers/age_buckets.py` — parse `age_text` → khoảng tuổi (test riêng).
- `apps/customers/report_service.py` — `CustomerReportService.summary()` tổng hợp 6 phân bố + feedback samples + totals; tái dùng `classify_segment` và helper của `profile_service`.
- `apps/customers/views.py` + `urls.py` — `GET /api/customers/report/summary`, gate quyền `dashboard`.
- Tests: `test_age_buckets`, `test_customer_report_service`, `test_customer_report_api` — 12/12 PASS.

**Frontend:**
- `lib/api/customers.ts` — type `CustomerReportSummary` + `getCustomerReportSummary`.
- `lib/routes.ts` — section `customer-report` (`/customers/report`), `requiredPermissionFor` dùng chung quyền `dashboard`.
- `i18n/messages.ts` — keys vi + en.
- `pages/CustomerReportPage.tsx` — trang self-contained (Recharts): 2 KPI + 6 chart + panel feedback, filter ngày/kênh/phân khúc (mặc định 30 ngày gần nhất).
- `App.tsx` + `Sidebar.tsx` (+ MobileNavDrawer) — lazy import, render branch, page title, guard quyền, nav item.

**Review:** final code review APPROVED_WITH_MINOR_ISSUES (không có vấn đề Critical/Important). Đếm rào cản theo lượt, nhóm nhu cầu distinct (đơn, nhóm), % base đúng, thứ tự URL đúng.

**Còn lại (chưa làm):** xác minh UI trên app chạy thật; minor: `_latest_order_map` prefetch `needs` hơi thừa trong nhánh tính phân khúc (helper dùng chung, chưa tối ưu riêng).
