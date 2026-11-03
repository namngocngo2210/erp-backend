# Báo cáo Sale — Tách "Doanh thu xác nhận đơn" (landing) và "Doanh thu chốt đơn" (sale tư vấn)

- Ngày: 2026-08-04
- Nhánh: `feat/landing-confirmed-revenue`
- Trạng thái: đã chốt thiết kế với chủ sở hữu nghiệp vụ (3 quyết định ở mục 3)

## 1. Bối cảnh

Tab "Báo cáo theo sale" (`/api/orders/reports/sale-by-staff`) đang có 2 cột tiền
gần như trùng nhau:

- `confirmed_revenue` = SUM(`total_after_discount`) của đơn có đánh giá thuộc đủ
  4 mã chốt (`CLOSED_SALES_STATUS_CODES`).
- `closed_revenue` = như trên nhưng 3 mã (bỏ "hẹn ngày").

Cả hai đều đo **giá trị cuối** của đơn nên không trả lời được câu hỏi nghiệp vụ:
trong số tiền chốt được, bao nhiêu là **khách tự đăng ký trên landing** (trước
khi sale tư vấn) và bao nhiêu là **sale tư vấn thêm được** (upsell).

Ví dụ chuẩn của nghiệp vụ: khách đăng ký landing mua 1 sản phẩm 1.500.000; sale
tư vấn, khách mua 3 cái thành 4.200.000. Kỳ vọng:

- Doanh thu xác nhận đơn = 1.500.000 (giá trị lúc đơn đổ về)
- Doanh thu chốt đơn = 4.200.000 − 1.500.000 = 2.700.000 (công tư vấn của sale)

Giá trị lúc đổ về hiện **bị mất**: ingest tính được (parse `product_raw` +
`product_id` × bảng giá kênh) và ghi vào `total_amount`, nhưng field này bị ghi
đè khi sale sửa đơn.

## 2. Yêu cầu

Trong tab "Báo cáo theo sale" (và tab "Báo cáo chi tiết theo sale" dùng chung
endpoint):

- Cột **Doanh thu xác nhận đơn** = tổng giá trị khách tự đăng ký lúc đơn đổ về.
- Cột **Doanh thu chốt đơn** = doanh thu đơn chốt − doanh thu xác nhận đơn.
- Các cột còn lại (Doanh thu tổng, Tỉ lệ chốt, Số data nhận, Số đơn hàng, AOV)
  giữ nguyên con số như hiện tại.

## 3. Quyết định nghiệp vụ (chốt với user 2026-08-04)

1. **Tập đơn**: giữ nguyên bộ lọc đánh giá hiện tại — cả 2 cột tính trên đơn có
   `assessment.sales_status_code ∈ CLOSED_SALES_STATUS_CODES` (đủ 4 mã, gồm hẹn
   ngày). KHÔNG dùng mốc "đã tạo hóa đơn + kship thành công" (doanh thu giao
   thành công đã có Nhóm 4/5 của dashboard phản ánh). Hệ quả: từ nay 2 cột của
   tab này dùng CÙNG tập đơn; phân biệt 3-mã/4-mã trước đây của tab bị bỏ (các
   báo cáo khác không đổi).
2. **Đơn không có giá trị đăng ký ban đầu** (tạo tay, fanpage, sàn, landing
   không bóc được sản phẩm/giá): xác nhận = 0, toàn bộ tiền vào cột chốt. Nhờ đó
   `xác nhận + chốt` luôn = doanh thu của toàn bộ đơn chốt — bảng tự cân.
3. **Đơn cũ**: backfill bằng management command parse lại `product_raw` × giá
   catalog hiện tại (chấp nhận sai số nếu giá đã đổi), chạy tay giờ thấp điểm.

## 4. Thiết kế

### 4.1 Model — field snapshot bất biến

`Order.landing_confirmed_amount` — `DecimalField(max_digits=12,
decimal_places=2, default=0, db_default=0)`.

- Ý nghĩa: tiền khách tự đăng ký trên landing TRƯỚC khi sale tư vấn. Ghi một lần
  lúc ingest, không bao giờ sửa lại (cùng họ với `has_address`).
- Không cần index riêng: chỉ xuất hiện trong `SUM(...)` của query báo cáo vốn
  quét theo `created_at`/`staff` sẵn có.
- Migration `0114_order_landing_confirmed_amount`, thuần `ADD COLUMN ... DEFAULT
  0` (db_default) — không rewrite bảng, an toàn chạy giờ hành chính.

### 4.2 Ghi nhận lúc ingest

`OrderServiceCommands.create_order_from_landing()` — sau khối tính
`derived_total_amount` hiện có, gán `landing_confirmed_amount =
derived_total_amount` vào `Order.objects.create(...)`.

Tận dụng nguyên thứ tự ưu tiên đang chạy, không viết lại logic bóc tách:

1. `total_amount` tường minh trong payload (nếu có);
2. số tiền parse từ `product_raw` (`derive_total_amount`);
3. `items_total` = số lượng (payload `quantity` / `derive_quantity`) × đơn giá
   theo bảng giá kênh (`resolve_price_book` + `package_prices`, fallback
   `product.price`) khi `product_id`/SKU khớp catalog.

Không khớp được gì → 0 (quyết định 2).

### 4.3 Tính bất biến

- Không thêm field vào bất kỳ serializer update/PATCH nào — API không cho sửa.
- Luồng nhân đôi/khôi phục đơn (`OrderServiceHelpers` — hàm clone dùng
  `create_kwargs` tường minh): thêm
  `landing_confirmed_amount=order.landing_confirmed_amount` để đơn khôi phục giữ
  đúng phần tách xác nhận/chốt của đơn gốc.
- Các đường tạo khác (tạo tay, Excel, đơn sàn, gửi ngoài) không gán → mặc định 0.

### 4.4 Báo cáo `summary_sale_daily_by_staff`

Chỉ sửa method này (`backend/apps/orders/_repositories_reports.py`) — nguồn duy
nhất của 2 tab. Các báo cáo khác (Nhóm 1–9 dashboard, Ads, Khách hàng) không
đụng tới.

```python
confirmed_expr = Least(F("landing_confirmed_amount"), F("total_after_discount"))
...
confirmed_revenue=Sum(
    confirmed_expr,
    filter=Q(assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES),
),
closed_revenue=Sum(
    F("total_after_discount") - confirmed_expr,
    filter=Q(assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES),
),
```

- `Least(...)` chặn ca down-sell (khách đăng ký 3tr, sale chốt 1tr5): xác nhận
  bị cắt về giá trị cuối, cột chốt không âm; bất biến `xác nhận + chốt =
  SUM(total_after_discount)` của tập đơn chốt luôn đúng.
- `total_revenue`, `data_count`, `orders` giữ nguyên. Cùng một `GROUP BY`,
  không thêm join/round-trip; `assessment` đã join sẵn.
- Tên field API giữ nguyên (`confirmed_revenue`, `closed_revenue`) — schema
  không đổi, chỉ đổi ngữ nghĩa; docstring của method và mô tả OpenAPI trong
  `dashboard_report_views.py` cập nhật lại.

### 4.5 Frontend

| File | Thay đổi |
|---|---|
| `features/sale-report/sale-report-selectors.ts` | `aov = (confirmedRevenue + closedRevenue) / orders` (= giá trị thực của đơn chốt, giữ đúng con số cũ). Comment 2 field theo ngữ nghĩa mới. |
| `features/sale-report/components/SaleMetricTable.tsx` + i18n | Cập nhật chú thích cột: "Xác nhận = tiền khách tự đăng ký trên landing lúc đơn đổ về; Chốt = phần sale tư vấn thêm; đơn không qua landing tính cả vào Chốt". |

Không đổi shape dữ liệu, không đổi component/bảng.

### 4.6 Backfill

Management command `backfill_landing_confirmed_amount`:

- Phạm vi: `order_source = "landing"`, `landing_confirmed_amount = 0`,
  `product_raw != ""`.
- Mỗi đơn, tính theo cùng thứ tự fallback của ingest (mục 4.2, bỏ bước payload):
  `derive_total_amount(product_raw)` → nếu 0 thì `quantity` đã lưu (fallback
  `derive_quantity`) × đơn giá bảng giá kênh theo `sku` đã lưu; không ra → giữ 0.
- `--dry-run` in thống kê (số đơn khớp theo từng nguồn giá) không ghi DB;
  idempotent (chỉ đụng đơn đang = 0); update theo batch `.update()` từng id —
  không bắn signal/WS event.
- Chạy tay giờ thấp điểm trên prod (theo tiền lệ migration 0032), không nhét vào
  migration.

Sai số chấp nhận: giá catalog hiện tại có thể khác giá lúc đơn cũ đổ về.

## 5. Test

### Backend (pytest)

- Ingest: payload có `total_amount` tường minh / chỉ `product_raw` parse được
  tiền / chỉ `product_id` + quantity (giá bảng giá kênh) → snapshot đúng theo
  từng nguồn; payload không bóc được gì → 0.
- Bất biến: PATCH đơn đổi `total_amount`/`total_after_discount` → snapshot giữ
  nguyên; clone/khôi phục đơn → snapshot copy theo.
- Báo cáo `sale-by-staff`:
  - đơn chốt snapshot 1tr5, final 4tr2 → confirmed 1tr5, closed 2tr7;
  - đơn chốt snapshot 0, final 3tr → confirmed +0, closed +3tr;
  - down-sell snapshot 3tr, final 1tr5 → confirmed 1tr5, closed 0;
  - đơn chưa chốt (`need_more_consulting`) → không vào 2 cột, vẫn vào
    `total_revenue`;
  - bất biến `confirmed + closed = SUM(total_after_discount)` của đơn chốt.
- Backfill command: dry-run không ghi; chạy thật chỉ sửa đơn = 0, đúng fallback.

### Frontend (vitest)

- Selector: AOV = (confirmed + closed) / orders; hàng Tổng cộng đúng.

## 6. Ngoài phạm vi

- Không thêm cột theo mốc "tạo hóa đơn + kship thành công" (quyết định 1).
- Không đổi định nghĩa Nhóm 1–9 dashboard, báo cáo Ads, báo cáo Khách hàng.
- Không hiển thị `landing_confirmed_amount` ở màn chi tiết đơn (có thể làm sau
  nếu sale cần đối soát từng đơn).
- Không nâng cấp bộ parse `product_raw` (regex tĩnh hiện tại giữ nguyên).
