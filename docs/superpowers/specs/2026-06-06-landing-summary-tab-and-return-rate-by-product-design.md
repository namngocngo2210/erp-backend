# Thiết kế: Tab "Tổng hợp" (Landing) + "Nhóm 7 — Tỷ lệ hoàn theo sản phẩm" (Báo cáo Sale)

- **Ngày:** 2026-06-06
- **Trạng thái:** Đã duyệt thiết kế → chờ writing-plans
- **Phạm vi:** 2 tính năng độc lập trong cùng một đợt.

## 1. Mục tiêu

1. **Trang Đơn hàng Landing:** thêm tab đầu tiên tên **"Tổng hợp"** hiển thị toàn bộ đơn có
   `order_source != ''` **VÀ** `order_source != 'koc'` (mọi trạng thái **trừ đã hủy**), đặt làm
   tab mặc định khi mở trang.
2. **Trang Báo cáo Sale (Dashboard):** thêm **"Nhóm 7 — Tỷ lệ hoàn theo sản phẩm"** — thống kê
   các đơn đã hoàn (status 5 "Đã chuyển hoàn") theo **%**, gom theo **sản phẩm**, mẫu số =
   **đơn đã gửi**.

Hai tính năng không phụ thuộc nhau; có thể làm/test song song.

## 2. Bối cảnh đã khảo sát (nguồn chân lý)

- Trang Landing render tab từ `LANDING_SHEET_TABS` trong
  [OrdersPage.tsx](frontend/src/pages/OrdersPage.tsx#L110). Tab hiện tại:
  `Sheet 1 | Sheet 2 | Tư vấn | Giao thành công | Đã hủy | Blocklist`.
- Ánh xạ tab → scope → tham số request ở
  [orders-tab.ts](frontend/src/app/orders-tab.ts) và
  [use-orders-data.ts](frontend/src/features/orders/use-orders-data.ts#L1354). Hiện
  tab mặc định `"orders"` của trang Landing ánh xạ về `landing-sheet1`.
- Endpoint backend
  [list_landing_orders](backend/apps/orders/transport/views/order_views_impl/order_list_view.py#L150):
  **khi KHÔNG có `sheet`** đã đặt sẵn `order_source_present="true"` + `order_source_exclude=["koc"]`,
  và base queryset loại đơn đã hủy (`include_cancelled=False`). → Đây **đúng** định nghĩa "Tổng hợp".
  Khi `sheet=1|2` mới thêm `info_complete`/lọc nguồn.
- "Báo cáo Sale" = trang **Dashboard** (`topnav.tabs.dashboard` = "Báo cáo Sale",
  [topnav.ts](frontend/src/i18n/messages/topnav.ts#L12)).
- Dashboard đã có **Nhóm 5 — Hiệu quả giao hàng** với `returned = Count(status=5)` và
  `% bom = returned / sent`, gom theo **staff** và **kênh**
  ([_repositories_reports.py](backend/apps/orders/_repositories_reports.py#L231)). Hằng số:
  `RETURNED_ORDER_STATUS = 5`; `SENT_OUT_ORDER_STATUSES = (2,3,4,5,7,8,9,10,11,12,13)`.
  > **HẾT HIỆU LỰC 2026-08-15.** Công thức `returned / sent` đã bị bỏ ở CẢ Nhóm 5a
  > lẫn 5b: tử số hụt (đơn đang quay đầu — status 4/12/13 — chưa được tính) và mẫu
  > số phồng (gồm cả đơn đang giao, chưa biết kết quả), khiến số ra thấp hơn thực
  > tế nhiều lần. Nay dùng `bomb / (bomb + delivered)` với
  > `BOMB_ORDER_STATUSES = (4, 5, 12, 13)` — xem helper `settledDeliveries()` ở
  > `frontend/src/features/dashboard/dashboard-utils.ts`. Mẫu số CỘNG hai rổ chứ
  > không suy ra bằng `total − in_delivery`: hai cách chỉ bằng nhau khi mọi đơn
  > có `status` nằm trong enum, mà `choices` của Django không sinh CHECK dưới DB.
  > Nhóm 7 (% hoàn theo sản phẩm) vẫn giữ `returned / sent` — đó là "tỷ lệ hoàn",
  > câu hỏi khác.
- Sản phẩm gắn với đơn qua **`OrderItem`** (FK `Order` `related_name="items"`, FK `Product`,
  unique theo `(order, product)`). `OrderItem` chỉ được tạo khi SKU lúc tạo/import **khớp** một
  `Product` ([order_service_commands.py](backend/apps/orders/application/services/order_service_commands.py#L196)).
  Đơn có SKU không khớp danh mục chỉ có text ở `Order.sku`/`Order.product`.
- Response dashboard lắp ráp ở
  [dashboard_summary](backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py#L573).

## 3. Tính năng 1 — Tab "Tổng hợp" (Landing)

### 3.1. Hành vi
- Tab **"Tổng hợp"** là **mục đầu tiên** của thanh tab Landing và là **tab mặc định** khi mở
  `/landing-orders` không kèm `?tab`.
- Nội dung: mọi đơn `order_source != ''` và `!= 'koc'`, **trừ đơn đã hủy** (đơn đã hủy vẫn xem ở
  tab "Đã hủy"). Bao gồm đơn mới / đang giao / giao TC / đã hoàn...
- Hiển thị bằng `OrdersTable` thường (cột mặc định của đơn). **Không** có thanh `SheetStatsRow`,
  **không** highlight SLA đỏ (đó là cơ chế riêng Sheet 1/2). Danh sách phẳng.

### 3.2. Thay đổi (chủ yếu frontend)

| File | Thay đổi |
|---|---|
| [OrdersPage.tsx](frontend/src/pages/OrdersPage.tsx) | `LandingTabKey` thêm `"summary"`. Thêm `{ key: "summary", label: "Tổng hợp" }` làm phần tử **đầu** của `LANDING_SHEET_TABS`. |
| [OrdersPage.tsx](frontend/src/pages/OrdersPage.tsx#L1879) | `activeTabKey`: trang Landing với `landingTab === "orders"` → map sang **`"summary"`** (thay vì `"sheet1"`). Điều kiện hiện `SheetStatsRow`/SLA: chỉ bật cho `sheet1`/`sheet2` (bỏ `"orders"` khỏi nhánh stats). |
| [use-orders-data.ts](frontend/src/features/orders/use-orders-data.ts#L359) | `FilterScope` thêm `"landing-summary"`. Trong `buildBody`: scope `landing-summary` → **không set `body.sheet`** (giữ nguyên hành vi mặc định). |
| [orders-tab.ts](frontend/src/app/orders-tab.ts#L18) | `getOrderTabScope("landing-orders", tab)`: `tab === "summary"` (và mặc định `"orders"`) → `"landing-summary"`. Sheet 1 chỉ khi `tab === "sheet1"`. |
| [orders-tab.ts](frontend/src/app/orders-tab.ts#L40) | `getOrderTabFallbackDefaults("summary")` → `{ status: [], invoiceStatus: [] }`. |

- **Backend:** không đổi. Endpoint không-`sheet` đã đúng.
- **Lưu ý regression:** trang Đơn hàng thường & KOC **không** dùng tab `summary` → không ảnh hưởng.
  Cần rà soát mọi chỗ đang giả định "Landing mặc định = Sheet 1" (đặc biệt nhánh hiện `SheetStatsRow`
  và `getOrderTabScope`) để đảm bảo mặc định mới = Tổng hợp.

## 4. Tính năng 2 — "Nhóm 7 — Tỷ lệ hoàn theo sản phẩm" (Dashboard)

> Đánh số **Nhóm 7** vì Dashboard đã có sẵn các nhóm 1, 2, 3, 4, 5a, 5b, 5c, 6 (Tỷ lệ note chân dung KH). Card mới nằm sau nhóm 6 hiện có.

### 4.1. Định nghĩa chỉ số
- Gom theo **sản phẩm** = `OrderItem.product` (id, sku, name).
- Với mỗi SP P:
  - `sent_P` = số đơn ở trạng thái `SENT_OUT_ORDER_STATUSES` có chứa P.
  - `returned_P` = số đơn `status = 5` có chứa P.
  - `% hoàn_P = returned_P / sent_P`.
- Do unique `(order, product)`, mỗi đơn xuất hiện tối đa 1 lần trong nhóm của 1 SP; đơn nhiều SP
  sẽ được tính ở mỗi SP tương ứng (đúng bản chất "tỷ lệ hoàn theo SP").
- Cùng khung thời gian/kỳ với các nhóm khác (`_delivery_quality_base_queryset` + lọc `created_at`).

### 4.2. Tradeoff đã chốt
- Đơn **không có `OrderItem` khớp Product** (SKU không khớp danh mục) **không** nằm trong bảng theo
  SP này. Chấp nhận để giữ định danh sản phẩm sạch. Ghi rõ giới hạn trong phần mô tả (hint) của nhóm.

### 4.3. Backend

| File | Thay đổi |
|---|---|
| [_repositories_reports.py](backend/apps/orders/_repositories_reports.py) | Thêm `summary_return_rate_by_product(date_from, date_to)`: từ `_delivery_quality_base_queryset`, `.filter(items__product__isnull=False)`, `.values("items__product", "items__product__sku", "items__product__name")`, annotate `total/sent/returned` bằng `Count("id", filter=Q(...))` (tái dùng ý của `_delivery_quality_aggregations`), `.order_by(...)` (ưu tiên SP có `returned` cao / % hoàn cao). |
| [dashboard_summary](backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py#L573) | Gọi method mới, thêm key `"return_rate_by_product"` vào response. |

### 4.4. Frontend

| File | Thay đổi |
|---|---|
| [DashboardPage.tsx](frontend/src/pages/DashboardPage.tsx) | Thêm type `ReturnRateByProductRow` (`product_id`, `items__product__sku`, `items__product__name`, `total`, `sent`, `returned`) vào `Props.summary`. |
| [DashboardPage.tsx](frontend/src/pages/DashboardPage.tsx) | Thêm card **"Nhóm 7 — Tỷ lệ hoàn theo sản phẩm"**: `KpiHero` tổng `% hoàn` (tính từ tổng `returned`/`sent` toàn nhóm) + breakdown (đơn gửi / đơn hoàn); `VerticalRateBar` % hoàn theo SP (cảnh báo đỏ khi vượt ngưỡng — tái dùng pattern ngưỡng sẵn có); `CollapsibleTable` bảng chi tiết (SP · đơn gửi · đơn hoàn · % hoàn) + dòng Tổng. Hint nêu rõ công thức và giới hạn "chỉ tính đơn có dòng SP khớp danh mục". |

- Tái dùng helpers sẵn có: `formatPercent`, `safePercent`, `KpiHero`, `VerticalRateBar`,
  `CollapsibleTable`.

## 5. Kiểm thử

**Backend (tính năng 2):**
- `summary_return_rate_by_product`: đơn 1 SP, đơn nhiều SP (tính ở từng SP), đơn không có item
  (bị loại), đếm đúng `sent`/`returned` theo định nghĩa status, lọc theo `date_from/date_to`, đơn
  đã hủy bị loại khỏi base.
- `dashboard_summary` trả về key `return_rate_by_product` đúng cấu trúc.

**Frontend:**
- Tính năng 1: render thanh tab có "Tổng hợp" đầu tiên; mở `/landing-orders` mặc định active
  "Tổng hợp"; scope `landing-summary` không gửi `sheet`; tab Sheet 1/2 vẫn hiện `SheetStatsRow`.
- Tính năng 2: render card Nhóm 7, `% hoàn` đúng công thức, cảnh báo đỏ khi vượt ngưỡng, bảng chi
  tiết khớp dữ liệu; trạng thái rỗng hiển thị "No data yet".

**Lưu ý môi trường:** pytest đang hỏng (BOM trong `pytest.ini`) → chạy backend test bằng
`manage.py test` với Postgres/Redis tạm (theo ghi chú spec trước).

## 6. Thứ tự triển khai (1 đợt)
1. FE tính năng 1: tab "Tổng hợp" + scope `landing-summary` + đặt mặc định + tách điều kiện
   SheetStatsRow/SLA.
2. BE tính năng 2: `summary_return_rate_by_product` + wire `dashboard_summary` + unit test.
3. FE tính năng 2: type + card Nhóm 7 + test render.
4. QA trực quan với data thật (cả 2 trang).

## 7. Câu hỏi mở
- Ngưỡng cảnh báo đỏ cho `% hoàn` theo SP: chốt một hằng số khi writing-plans (gợi ý mặc định
  tham khảo `UNREACHED_WARNING_THRESHOLD = 30`, nhưng % hoàn nên thấp hơn — đề xuất ~10–15%, xác
  nhận lúc lập plan).
