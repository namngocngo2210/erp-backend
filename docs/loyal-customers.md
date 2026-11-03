# Khách hàng thân thiết (Loyal Customers)

## 1. Mục tiêu
Bổ sung trang "Khách hàng thân thiết" trong nhóm **Khách hàng** ở sidebar, hiển thị
Top 100 khách hàng có tổng chi tiêu cao nhất, kèm các thông tin phục vụ sale chăm sóc.

## 2. Cột hiển thị
| Cột | Nguồn dữ liệu |
|-----|---------------|
| STT | Chỉ số dòng theo trang |
| Hạng | Hạng tuyệt đối trong top 100. Top 3 hiển thị huy chương Vàng/Bạc/Đồng |
| Họ tên | `customers.full_name` |
| SĐT | `customers.phone_raw` (fallback `phone_normalized`) |
| Số lần mua | Số đơn **giao thành công** (`orders.status = 3`) |
| Tổng chi tiêu (đ) | `SUM(orders.total_after_discount)` của đơn `status = 3` |
| Sản phẩm đã mua | Top 5 SKU theo tổng số lượng (gộp `order_items` của đơn `status = 3`), mỗi SKU một dòng, kèm số lượng khi >1 |
| Ngày mua cuối | `MAX(orders.created_at)` của đơn `status = 3` |
| Phân khúc | Tự động phân loại S1–S7 theo tiêu chí (xem mục 3.1) |
| Hành động sale cần làm ngay | Ô nhập tay, lưu DB (`loyal_customer_notes.sale_action`) |
| Ghi chú đặc biệt | Ô nhập tay, lưu DB (`loyal_customer_notes.special_note`) |

### Quyết định nghiệp vụ (đã chốt)
- **Sort**: Tổng chi tiêu giảm dần (tie-break: số lần mua giảm dần, rồi `customer_id`).
- **Đơn được tính**: chỉ đơn **giao thành công** (GTC, `status = 3`) — đồng bộ với cách tính
  doanh thu trong báo cáo. **Loại** đơn đã hủy hóa đơn (`invoice_status=2`), giống `_base_orders_queryset`.
- **Sản phẩm**: top 5 SKU theo tổng số lượng mua.
- **Phân khúc**: tự động phân loại S1–S7 theo tiêu chí (mục 3.1).
- **2 cột hành động/ghi chú**: nhập tay, lưu DB.

### 3.1 Phân loại khách hàng S1–S7
Catalog lưu ở bảng `customer_segment_categories` (Mã, Tên, Tiêu chí, Ưu tiên chăm sóc),
seed qua data migration. Logic tự động ở `apps/customers/segmentation.py`, đánh giá theo
**thứ tự ưu tiên** (match đầu tiên thắng):

1. **S5 – Có vấn đề**: đơn hoàn (`status` 4/5) hoặc assessment `warranty` / `stalled_complaint` / rating `poor`.
2. **S6 – B2B/Đối tác**: ≥3 đơn GTC.
3. **S3 – Trung thành**: ≥2 đơn GTC hoặc tổng chi tiêu ≥2 triệu.
4. **S4 – Ngủ đông**: có ≥1 đơn GTC và >180 ngày kể từ đơn cuối.
5. **S2 – Mới mua 1 lần**: đúng 1 đơn GTC, ≤90 ngày.
6. **S1 – Tiềm năng chưa mua**: 0 đơn GTC, trạng thái gọi ∈ {Tư vấn thêm, Hẹn lịch, Gọi lại sau}.
7. **S7 – Từ chối**: 0 đơn GTC, trạng thái `not_buying`.
8. Không khớp → để trống.

S5 ưu tiên cao nhất (xử lý trước resale); bậc quan hệ/giá trị (S6, S3) trên trạng thái
độ mới (S4, S2). Engine dùng được cho mọi khách có đơn + assessment, không chỉ top 10.

## 3. Thiết kế dữ liệu
### Bảng mới `loyal_customer_notes`
- `customer` OneToOne → `customers` (on_delete CASCADE)
- `sale_action` TEXT, mặc định rỗng
- `special_note` TEXT, mặc định rỗng
- `updated_by` FK user (null)
- `created_at`, `updated_at`

Tách bảng riêng để tránh thêm cột vào `customers` (vốn dùng cho nhiều nghiệp vụ),
và để chỉ ghi note cho phần nhỏ khách hàng (sparse) → tiết kiệm dung lượng.

### Query Top 100 (1 truy vấn aggregate + 2 truy vấn phụ, không N+1)
1. Aggregate trên `orders` (status=3, chưa xóa, loại branch ẩn): `total_spent`, `purchase_count`,
   `last_purchase_at`, group theo `customer_id`, order theo `-total_spent`, lấy 100 dòng đầu.
2. Cắt trang `[offset:offset+limit]` → lấy `customer_id` của trang.
3. Truy vấn phụ theo các `customer_id` của trang: thông tin `Customer`, danh sách sản phẩm
   (`OrderItem.product_name` distinct), và `loyal_customer_notes`.

Hạng = `offset + index + 1` (do danh sách đã sort toàn cục trong top 100).

### Tối ưu truy vấn
- **Sản phẩm**: gộp `SUM(quantity)` theo SKU ngay trong DB (`values().annotate(Sum)`), không kéo
  từng dòng `OrderItem` về Python.
- **Index xếp hạng**: partial index `ord_delivered_cust_cr_idx` trên `orders (customer_id, created_at)
  WHERE deleted_at IS NULL AND status = 3` (migration `0059`, tạo `CONCURRENTLY`, `atomic=False`)
  — phục vụ GROUP BY theo khách, lọc khoảng thời gian và `Max(created_at)` trên tập đơn giao
  thành công (nhỏ hơn nhiều toàn bảng).
- Không có N+1: mọi truy vấn phụ đều gom theo lô (`id__in` / aggregate / `distinct`).

## 4. API
- `GET /api/customers/loyal?limit=&offset=` → danh sách dòng + tổng số (≤ 100).
- `PATCH /api/customers/loyal/<customer_id>/note` body `{ sale_action?, special_note? }`.

## 5. Phân quyền
- Thêm `loyal-customers` vào `VALID_SECTIONS` (admin tự động có; department có thể cấp).
- Frontend: section mới `loyal-customers`, route `/loyal-customers`, item trong nhóm sidebar `customers`.

## 6. Frontend
- `LoyalCustomersPage.tsx`: bảng (desktop) + card (mobile), phân trang giống `CustomersPage`.
- Huy chương top 3 bằng icon `Medal` (lucide) với màu vàng/bạc/đồng.
- 2 cột hành động/ghi chú: inline edit (textarea) → gọi PATCH, toast kết quả.

## 7. Cache (Redis)
Bảng xếp hạng (GROUP BY toàn bộ đơn giao thành công) là phần nặng và giống nhau cho
mọi trang nên được cache qua Django cache framework (backend Redis đã cấu hình sẵn):
- Key `loyal_customers:ranking:v5:{from}:{to}`, TTL 300s (5 phút). Bump version để vô hiệu hóa thủ công.
- Chỉ cache phần ranking; thông tin khách hàng, sản phẩm và **ghi chú lấy tươi** mỗi request
  (ghi chú có thể vừa được sale chỉnh sửa).
- `GET /api/customers/loyal?refresh=1` để tính lại ngay, bỏ qua cache.

## 7.1 Bộ lọc thời gian
`GET /api/customers/loyal?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD` lọc theo `created_at`
của đơn (nửa mở `[from, to+1d)`). Khi có lọc:
- Số lần mua, tổng chi tiêu, sản phẩm, ngày mua cuối, **và phân khúc** đều tính trong khoảng.
- Độ mới khi phân loại (S2 ≤90 ngày, S4 >180 ngày) lấy mốc **cuối khoảng** (`date_to`), nếu
  không có `date_to` thì lấy hiện tại.
- Cache Redis tách riêng theo từng khoảng (`loyal_customers:ranking:v5:{from}:{to}`).
- Frontend: preset (Toàn thời gian / Tháng này / Quý này / Năm nay) + 2 ô ngày tự chọn. Đổi
  ngày **tự áp dụng ngay** (không có nút Áp dụng).

## 7.2 Bộ lọc phân khúc
`GET /api/customers/loyal?segment=S3` lọc theo mã phân khúc trong phạm vi top 100. Toàn bộ top
được phân loại trước, lọc theo phân khúc, rồi mới phân trang — **hạng giữ vị trí tuyệt đối**
(không đánh lại số). Catalog cho dropdown: `GET /api/customers/loyal/segments`.
Lưu ý: trang là "khách thân thiết" (chỉ khách đã mua) nên S1/S7 (chưa mua) sẽ không xuất hiện.

## 7.3 Danh mục sản phẩm mặc định
Mục tiêu: trang mặc định lọc sẵn theo **một nhóm sản phẩm chủ lực** (vd "máy massage")
để bảng "khách thân thiết" chỉ còn khách mua nhóm đó — thay vì phải chọn tay mỗi lần.

**Cơ chế (chốt phương án A — flag trên catalog):**
- Thêm cột `product_categories.is_loyal_default` (boolean, mặc định `False`).
- **Ràng buộc tối đa một danh mục mặc định**: partial unique index trên `is_loyal_default`
  với điều kiện `is_loyal_default = TRUE AND deleted_at IS NULL`. Khi PATCH/POST đặt một danh
  mục thành mặc định, serializer **hạ cờ** các danh mục còn lại trong cùng transaction
  (tránh lỗi unique, đảm bảo chuyển mặc định "nguyên tử").
- API `GET /api/orders/product-categories` trả thêm `is_loyal_default`; PATCH/POST nhận field này.
- Frontend: khi nạp danh sách danh mục, nếu có danh mục `is_loyal_default = true` thì **khởi tạo**
  bộ lọc "Danh mục sản phẩm" bằng danh mục đó (thay vì "Tất cả danh mục"). Vẫn cho người dùng
  đổi sang nhóm khác / "Tất cả". Query khách thân thiết chỉ chạy **sau khi** đã áp mặc định
  (cờ `filtersReady`) → tránh fetch thừa lần đầu (all → default).

**Lưu ý nghiệp vụ (không đổi):** cơ chế lọc vẫn là *xếp hạng top 100 theo tổng chi tiêu trên
toàn bộ sản phẩm rồi mới lọc theo danh mục* (mục 7 + `_customers_in_product_category`). Do đó
khi mặc định một nhóm, bảng có thể hiển thị **ít hơn 100 dòng**, và khách mua nhiều sản phẩm
nhóm đó nhưng tổng chi tiêu nằm ngoài top 100 toàn hệ thống sẽ không xuất hiện. Nếu sau này
cần "xếp hạng trong phạm vi nhóm" thì phải lọc ở tầng ranking query (`_compute_loyal_ranking`),
là một thay đổi riêng.

## 8. Sprint
- **Sprint 1 (xong)**: Backend model + migration + service + API + section quyền.
- **Sprint 2 (xong)**: Frontend page + sidebar + route + i18n + wiring App.
- **Sprint 3 (xong)**: cache aggregate qua Redis (TTL + refresh override).
- **Sprint 4 (xong)**: phân loại S1–S7, top-5 SKU theo số lượng, bộ lọc thời gian.
- **Sprint 5 (đề xuất sau)**: export Excel; áp phân khúc lên trang Danh sách khách hàng.
- **Sprint 6 (đang làm)**: danh mục sản phẩm mặc định (`is_loyal_default`) — mục 7.3.

## 9. Đã làm / chưa làm
- Đã làm: Sprint 1–4.
- Chưa làm: export Excel; surface phân khúc trên trang khách hàng tổng (Sprint 5 khi cần).
