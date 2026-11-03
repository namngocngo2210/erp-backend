# Bộ lọc ngày & "tình trạng lạnh" cho trang Theo dõi Resale

## Bối cảnh & vấn đề

Trang `ResaleTrackingPage` ("Theo dõi resale - khách cũ") hiện có 4 bộ lọc: Sale,
Phân khúc, Trạng thái liên hệ, và "ngày-không-mua" (Quá 30/60/180 ngày — chính là
**tình trạng lạnh của khách**). Tất cả đều **lọc phía client trên biến `rows`**.

Nhưng `rows` chỉ là **một trang** dữ liệu do backend phân trang (`limit/offset`):
backend `CustomerResaleService.list_resale_tracking` **chỉ nhận tham số `q`**, mọi
filter khác không được đẩy xuống. Hệ quả:

- **Bộ lọc "tình trạng lạnh" (days) không dùng được**: chọn "Ngủ đông +180 ngày"
  chỉ lọc trong ~50 dòng đang hiển thị → thường ra rỗng dù toàn hệ thống có nhiều
  khách ngủ đông.
- **Chưa có bộ lọc theo ngày** (ngày giao thành công).

## Quyết định thiết kế

1. **Đẩy toàn bộ filter xuống backend** rồi mới phân trang. Mỗi row chứa cả
   `order_assessment` (lịch sử đơn) khá nặng nên không thể tải hết về client để lọc.
2. **Bộ lọc ngày lọc theo `delivered_at`** (= `Order.updated_at`, đúng trường trang
   đang hiển thị). Đây là cột DB → lọc rẻ ở tầng SQL.
3. **Tránh "kẹt dropdown"**: khi lọc server-side, dropdown Sale phải vẫn liệt kê đủ
   các sale trong phạm vi ngày đang xem (không chỉ sale đã chọn). Backend trả thêm
   `facets.sales` (distinct staff trong phạm vi ngày + tìm kiếm) và `facets.segments`
   (catalog phân khúc khả dĩ S2–S6).
4. **Giữ fast-path để không làm chậm lần tải mặc định**:
   - `segment` và `min_days` (tình trạng lạnh) là **trường tính toán** (suy ra từ lịch
     sử mua của khách), không có sẵn trong SQL → khi lọc theo chúng phải tính toàn bộ
     phạm vi rồi lọc + phân trang trong Python (**full-scan path**).
   - `date_from/date_to`, `q`, `sale`, `contact_status` đều map được xuống SQL → khi
     **không** có `segment`/`min_days`, vẫn phân trang ở DB và chỉ tính row cho trang
     hiện tại (**fast path**, giữ nguyên hiệu năng như hiện tại).

## Hợp đồng API

`GET /api/customers/resale`

| Query param      | Ý nghĩa                                                        | Tầng lọc |
|------------------|---------------------------------------------------------------|----------|
| `q`              | Tìm tên/SĐT/mã đơn (giữ nguyên)                                | SQL |
| `date_from`      | `YYYY-MM-DD` — giao thành công từ ngày (theo `updated_at`)     | SQL |
| `date_to`        | `YYYY-MM-DD` — đến hết ngày (nửa mở: `< date_to + 1`)          | SQL |
| `sale`           | `staff_id` của sale                                           | SQL |
| `contact_status` | `none` / `contacted` / `completed`                           | SQL |
| `segment`        | Mã phân khúc (S2–S6)                                          | Python (full-scan) |
| `min_days`       | 30 / 60 / 180 — số ngày tối thiểu kể từ lần mua cuối          | Python (full-scan) |
| `limit`,`offset` | Phân trang (giữ nguyên)                                       | — |

Response bổ sung `facets`:

```json
{
  "results": [ ... ],
  "total": 123,
  "facets": {
    "sales": [{ "id": 5, "name": "Nguyễn A" }],
    "segments": [{ "code": "S4", "name": "Ngủ đông" }]
  }
}
```

`total` giờ phản ánh **số dòng sau khi lọc** (KPI & phân trang đều chuẩn theo filter).

## Phạm vi thay đổi

- `backend/apps/customers/customer_resale_service.py`: thêm tham số lọc + facets +
  dual-path (fast / full-scan).
- `backend/apps/customers/views.py`: đọc query params mới, trả `facets`.
- `frontend/src/lib/api/customers.ts`: thêm tham số + type `facets`.
- `frontend/src/pages/ResaleTrackingPage.tsx`: thêm UI lọc ngày, nối filter vào API,
  bỏ lọc client-side (đã chuyển server-side), lấy options từ `facets`.
- Test: `backend/apps/customers/tests/test_resale_tracking_filters.py`.

## Ghi chú hiệu năng / hướng phát triển

- Full-scan path tính lịch sử mua cho toàn bộ khách trong phạm vi ngày+`q`. Có bộ lọc
  ngày giúp giới hạn tập này. Trường hợp nặng nhất: chọn "tình trạng lạnh" mà không
  giới hạn ngày → quét toàn bộ đơn giao thành công (đúng bản chất "liệt kê mọi khách
  lạnh").
- Hướng tối ưu sau này: denormalize `last_purchase_at` / `segment_code` xuống bảng
  `customers` (cập nhật khi đơn đổi trạng thái) để mọi filter đều ở SQL và bỏ full-scan.

## Tiến độ

### Sprint 1 — Backend filter + facets
- [x] Thêm tham số lọc + dual-path + facets vào service
- [x] Cập nhật view đọc params & trả facets
- [x] Unit test cho service (date, min_days, segment, contact_status, sale, facets)

### Sprint 2 — Frontend
- [x] API client: tham số mới + type facets
- [x] UI bộ lọc ngày (Từ ngày / Đến ngày) theo delivered_at
- [x] Nối tất cả filter vào API, bỏ lọc client-side, options lấy từ facets
