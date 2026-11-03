# Thiết kế: Trang Khách hàng tổng hợp (Database tổng hợp)

- **Ngày**: 2026-05-21
- **Branch**: loyal-customers-20260520
- **Nguồn yêu cầu**: `database.xlsx` — "DATABASE KHÁCH HÀNG TỔNG HỢP — DELAX", mỗi khách 1 dòng, khoá chính SĐT, gồm 39 cột chia 8 nhóm.

## 1. Mục tiêu & phạm vi

Nâng cấp **trang "Khách hàng"** hiện tại (`CustomersPage`) thành bảng tổng hợp 1 dòng/khách theo cấu trúc file. Trang "Khách hàng trung thành" (`LoyalCustomersPage`) **giữ nguyên, không đụng tới**.

Bản chất: tổng hợp dữ liệu **assessment từ các đơn của khách** + các chỉ số mua hàng đã tính + phân khúc + ghi chú resale nhập tay, về một dòng duy nhất.

### Ngoài phạm vi (YAGNI)
- Cột **Email**: bỏ hẳn (không có nguồn dữ liệu).
- Không tạo bảng denormalized / materialized view (để dành khi quy mô khách rất lớn).
- Không xây recommendation engine cho gợi ý resale — chuyển sang nhập tay.

## 2. Ánh xạ 39 cột → nguồn dữ liệu

| # | Cột (Excel) | Nguồn | Loại |
|---|---|---|---|
| 1–3 | Mã KH, Họ tên, SĐT | `customers.code/full_name/phone_raw` | có sẵn |
| 4 | ~~Email~~ | — | **bỏ cột** |
| 5 | Địa chỉ | `Order.address` (đơn mới nhất) | đơn mới nhất |
| 6 | Kênh bán | `Order.channel` (đơn mới nhất) | đơn mới nhất |
| 7 | Trạng thái sale | `OrderAssessment.sales_status_code` | đơn mới nhất |
| 8 | Lý do không chốt | `OrderAssessment.not_closed_reason_code` | đơn mới nhất |
| 9 | Giới tính | `customers.gender` | có sẵn |
| 10 | Tỉnh/TP | `Order.province` (đơn mới nhất) | đơn mới nhất |
| 11 | Nhóm tuổi | `OrderAssessment.age_text` | đơn mới nhất |
| 12 | Nghề nghiệp | `OrderAssessment.occupation_code` | đơn mới nhất |
| 13 | Phân khúc | `segmentation.py` (S1–S7) | tính |
| 14 | Nhóm nhu cầu chính | `OrderAssessment.needs.group` | đơn mới nhất |
| 15 | Ngày mua đầu | aggregate orders (MIN created_at) | tính |
| 16 | Ngày mua cuối | aggregate orders (MAX created_at) | tính |
| 17 | Tổng đơn | aggregate orders (COUNT) | tính |
| 18 | Tổng chi tiêu | aggregate orders (SUM `total_after_discount`) | tính |
| 19 | SP (đã mua) | distinct SKU từ orders | tính |
| 20 | SP mua gần nhất | `Order.sku/product` (đơn mới nhất) | đơn mới nhất |
| 21 | Giá đơn gần nhất | `Order.total_after_discount` (đơn mới nhất) | đơn mới nhất |
| 22 | Trạng thái đơn gần nhất | `Order.status` (đơn mới nhất) | đơn mới nhất |
| 23 | Phân loại KH | `OrderAssessment.customer_classification_code` | đơn mới nhất |
| 24 | Nhóm Nhu cầu | `OrderAssessment.needs.group` | đơn mới nhất |
| 25 | Chi tiết nhu cầu | `OrderAssessment.needs.type` | đơn mới nhất |
| 26 | Mức độ đau | `OrderAssessment.pain_level_code` | đơn mới nhất |
| 27 | Thời gian bị đau nhức | `OrderAssessment.pain_duration_code` | đơn mới nhất |
| 28 | Thói quen sinh hoạt | `OrderAssessment.lifestyle_habit_codes` | đơn mới nhất |
| 29 | Đã từng dùng massage | `OrderAssessment.used_massage_before` | đơn mới nhất |
| 30 | Thời điểm thư giãn chính | `OrderAssessment.relax_time_codes` | đơn mới nhất |
| 31 | Rào cản khi mua | `OrderAssessment.purchase_barrier_codes` | đơn mới nhất |
| 32 | Nguồn biết đến Delax | `OrderAssessment.delax_source_codes` | đơn mới nhất |
| 33 | Hãng khách đang so | `OrderAssessment.comparing_brand_text` | đơn mới nhất |
| 34 | Thời lượng cuộc gọi | `OrderAssessment.call_duration_code` | đơn mới nhất |
| 35 | Cảm nhận sau dùng | `OrderAssessment.post_purchase_feedback_text` | đơn mới nhất |
| 36 | Đánh giá về SP sau mua | `OrderAssessment.post_purchase_rating_code` | đơn mới nhất |
| 37 | Lần resale gần nhất | suy ra: `last_purchase_at` nếu tổng đơn ≥ 2, else trống | suy ra (read-only) |
| 38 | Trạng thái resale | `loyal_customer_notes.resale_status` | **nhập tay, inline** |
| 39 | Gợi ý SP resale tiếp theo | `loyal_customer_notes.resale_suggestion` | **nhập tay, inline** |

**Quy ước loại trừ**: các metrics (tổng đơn, tổng chi tiêu, ngày mua, SP) **bỏ đơn hủy/hoàn** — nhất quán với commit "Exclude cancelled-invoice orders from loyal customers metrics".

**Quy tắc tổng hợp chân dung**: hiển thị theo **assessment của đơn mới nhất**. Khi click vào dòng → xổ dropdown xem **toàn bộ đơn** của khách.

## 3. Kiến trúc — Hướng A: làm giàu dữ liệu theo từng trang

Giữ `GET /api/customers` phân trang + tìm kiếm. Sau khi lấy danh sách trang hiện tại (≤100 dòng), làm giàu **chỉ cho các `customer_id` đang hiển thị** (dùng `customer_id IN (...)`), tránh quét toàn bảng và tránh N+1.

### 3.1 Backend (Django — `apps/customers/`)

**Migration**
- Thêm 2 cột vào model `LoyalCustomerNote` (`loyal_customer_notes`):
  - `resale_status` (TextField, blank, default "")
  - `resale_suggestion` (TextField, blank, default "")
- Đảm bảo index phục vụ "đơn mới nhất theo khách" + aggregate: partial index `orders(customer_id, -created_at) WHERE deleted_at IS NULL`. Kiểm tra index hiện có trước, chỉ thêm nếu thiếu.

**Repository / Service** — thêm các hàm làm giàu (đặt trong `repositories.py` + `services.py`, không phá `list_customers` cũ):
1. `aggregate_metrics(customer_ids)` → 1 query `GROUP BY customer_id`: `total_orders`, `total_spent`, `first_purchase_at`, `last_purchase_at`, `distinct_skus` — loại đơn hủy/hoàn.
2. `latest_order_map(customer_ids)` → 1 query lấy 1 đơn mới nhất/khách (window function hoặc `DISTINCT ON (customer_id) ... ORDER BY customer_id, created_at DESC`), `select_related("channel", "assessment")` + prefetch `assessment.needs`. Trả về: address, province, channel, sku/product, total_after_discount, status, và toàn bộ trường assessment.
3. Phân khúc: gọi lại logic `segmentation.py` với `(purchase_count, total_spent, days_since_last_purchase)`.
4. `last_resale_at`: = `last_purchase_at` nếu `total_orders ≥ 2`, else `None`.
5. Notes: lấy `loyal_customer_notes` theo `customer_id IN (...)` (sale_action, special_note, resale_status, resale_suggestion).

**Serializer** — `CustomerProfileSerializer` (mới), 1 object/khách gồm nhóm con:
```
{
  identity:    { id, code, full_name, phone },
  demographics:{ gender, gender_label, province, address, age_text, occupation_code, occupation_label },
  channel:     { channel_id, channel_name },
  segment:     { segment_code, segment_name, segment_care_priority },
  needs:       { need_group_code, need_group_name, need_type_code, need_type_name },
  purchase:    { first_purchase_at, last_purchase_at, total_orders, total_spent, distinct_skus[] },
  latest_order:{ sku, product, last_order_amount, last_order_status, last_order_status_label,
                 sales_status_code, sales_status_label, not_closed_reason_code, not_closed_reason_label },
  persona:     { customer_classification_code(+label), pain_level_code(+label), pain_duration_code(+label),
                 lifestyle_habit_codes[], used_massage_before, relax_time_codes[], purchase_barrier_codes[],
                 delax_source_codes[], comparing_brand_text, call_duration_code(+label),
                 post_purchase_feedback_text, post_purchase_rating_code(+label) },
  resale:      { last_resale_at, resale_status, resale_suggestion }
}
```
Tái dùng các hàm/label đã có ở `OrderCRMSerializer` (đặt label-map ra chỗ dùng chung nếu cần).

**Endpoints**
- `GET /api/customers/profiles` — **endpoint mới**, trả `{results, total}` của `CustomerProfileSerializer`. Tham số: `q`, `limit`, `offset` + filter tùy chọn `segment`, `channel`, `sales_status`.
- `PATCH /api/customers/<id>/note` — endpoint mới cập nhật `resale_status` + `resale_suggestion`, ghi `updated_by`. Tái dùng cơ chế của `upsert_loyal_note`.
- Giữ nguyên: `GET /api/customers/<id>/orders` (dùng cho dropdown lịch sử đơn), create/update/delete/bulk-delete.

> **Quyết định tương thích (đã chốt)**: `GET /api/customers` được order-creation autocomplete dùng trực tiếp (`use-orders-data.ts` parse `CustomerRow[]`). Vì vậy **KHÔNG đổi shape** `/api/customers`; tạo **endpoint riêng `/api/customers/profiles`** cho trang tổng hợp. Chỉ `CustomersPage.tsx` tiêu thụ endpoint mới này.

### 3.2 Frontend (`frontend/src/`)

**Types** (`lib/api/customers.ts`): thêm `CustomerProfile` (+ nhóm con) khớp serializer; thêm `updateCustomerNote(customerId, { resale_status, resale_suggestion })`.

**Trang `CustomersPage.tsx`**:
- Bảng **header 2 tầng**: hàng trên là 8 nhóm (📋 Định danh / Trạng thái xử lý / 👤 Nhân khẩu học / 🏷️ Phân khúc & nhu cầu / 🛒 Hành vi mua / 📦 SP đã mua / 💬 Chân dung / 🔄 Resale), hàng dưới là tên cột.
- **Hiển thị mặc định nhóm cốt lõi**: Định danh, Phân khúc, Hành vi mua, Trạng thái sale. Còn lại (Nhân khẩu học, Chân dung, Resale, SP) bật/tắt qua **menu column-visibility** (`DropdownMenu`). Sticky 2 cột đầu (Mã KH, Họ tên) khi cuộn ngang.
- **Expandable row**: click dòng → `Collapsible` xổ ra bảng **toàn bộ đơn của khách** (gọi `GET /api/customers/<id>/orders`, lazy khi mở). Thay cho modal lịch sử đơn hiện tại.
- **Render code/label**: trường code → nhãn tiếng Việt (`*_label`); trường mảng (rào cản, thói quen, nguồn biết đến…) → chip `Badge`.
- **Badge màu**: phân khúc S1–S7 và (nếu có) trạng thái resale → badge tông xanh da trời theo style hệ thống.
- **Inline edit**: 2 ô `resale_status`, `resale_suggestion` click-to-edit, lưu khi blur/Enter (tái dùng pattern inline edit của `LoyalCustomersPage`).
- **Filter bar**: ô tìm kiếm (tên/SĐT/mã, debounce 300ms) + `Select` Phân khúc / Kênh bán / Trạng thái sale; phân trang 10/50/100.
- shadcn/ui: `Table`, `DropdownMenu`, `Collapsible`, `Badge`, `Select`, `Input`.

## 4. Hiệu năng

- Mỗi trang ≤100 dòng → ~3 query phụ dạng `customer_id IN (...)` (metrics, latest order, notes) + 1 query danh sách gốc. Không N+1.
- Index `orders(customer_id, -created_at) WHERE deleted_at IS NULL` cho latest-order và aggregate.
- Dropdown lịch sử đơn lazy-load khi mở từng dòng.

## 5. Kiểm thử

**Backend (pytest)**
- Aggregate metrics: nhiều đơn (gồm đơn hủy/hoàn) → tổng đơn/chi tiêu/ngày mua đúng, loại đơn hủy/hoàn.
- Latest order map: lấy đúng đơn mới nhất; khách không có đơn → các trường đơn mới nhất rỗng.
- `last_resale_at`: tổng đơn = 1 → trống; ≥ 2 → bằng `last_purchase_at`.
- Phân khúc: khớp `segmentation.py`.
- PATCH note: cập nhật `resale_status`/`resale_suggestion`, set `updated_by`; quyền `IsAuthenticated`.
- Filter `segment`/`channel`/`sales_status` lọc đúng.
- Lưu ý: có sẵn 1 test aggregate fail trên master (memory `preexisting-failing-aggregate-test`) — phân biệt với lỗi mới.

**Frontend**
- Render bảng với profile, toggle nhóm cột, expandable row gọi orders, inline edit gọi PATCH note.

## 6. Rủi ro & quyết định
- **Tương thích endpoint (đã chốt)**: tạo `/api/customers/profiles` riêng, không đụng `/api/customers` (autocomplete order phụ thuộc shape cũ).
- Bề rộng 38 cột: xử lý bằng nhóm cột mặc định + toggle + sticky cột; nếu vẫn nặng UX có thể bổ sung chế độ "card" sau (ngoài phạm vi).
