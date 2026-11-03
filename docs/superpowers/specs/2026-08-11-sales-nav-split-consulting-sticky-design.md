# Tách sidebar Sale, khóa tab Tư vấn, Nhóm 10 theo sheet

Ngày: 2026-08-11
Branch: `feat/sales-nav-split`

## Bối cảnh

Bốn thay đổi độc lập nhưng dính nhau qua khái niệm "sheet" của trang Đơn hàng landing:

1. Sidebar: module `sales` gộp cả đơn hàng lẫn CSKH, 7 mục trong một accordion.
2. Trang Ngoại sàn (`outside-orders`) đã ẩn khỏi nav từ lâu, không còn dùng.
3. Tab "Tư vấn" (Sheet 3) đánh rơi đơn khi sale điền địa chỉ.
4. Báo cáo sale Nhóm 10 chia theo `order_source`, không khớp 3 tab mà sale làm việc.

## Phần 1 — Tách sidebar thành Đơn hàng / CSKH

### Hiện trạng

`APP_MODULES` (`frontend/src/app/modules.ts`) có một module `sales` với 7 section:
`orders`, `landing-orders`, `contacts`, `floating-pool`, `koc-orders`,
`outside-orders` (hidden), `resale-tracking`.

### Thiết kế

Tách thành hai module:

| Module key | labelKey → nhãn vi | Section |
| --- | --- | --- |
| `sales` (giữ key) | `sidebar.group.orders` → "Đơn hàng" | `orders`, `landing-orders`, `koc-orders`, `outside-orders` (hidden) |
| `cskh` (mới) | `sidebar.group.cskh` → "CSKH" | `contacts`, `floating-pool`, `resale-tracking` |

Quyết định và lý do:

- **Giữ key `sales` cho nhóm Đơn hàng.** `RootLayout` ghi
  `localStorage["desi.lastModule"]` và `route-guards` đọc lại để chọn trang mặc
  định. Đổi key làm giá trị đã lưu của người đang dùng thành rác.
- **URL không đổi.** `SECTION_TO_PATH` (`frontend/src/lib/routes.ts`) khai báo path
  tường minh, không suy từ `module.basePath`, nên các mục CSKH vẫn ở
  `/sales/contacts`, `/sales/floating-pool`, `/sales/resale-tracking`. Không cần
  redirect, không hỏng link đã lưu. `basePath` của module `cskh` đặt `/sales` cho
  khớp thực tế.
- **Nhãn `topnav.tabs.orders`**: "Đơn hàng" → "Đơn hàng chung" (vi),
  "Orders" → "General Orders" (en). Ba nhãn còn lại của nhóm CSKH đã đúng sẵn
  ("Danh sách contact", "Kho số thả nổi", "Theo dõi resale").
- **Icon/accent module `cskh`**: `Headphones` + accent cyan
  (`chip: "bg-cyan-400"`, `tile: "bg-cyan-500/15 text-cyan-600 dark:text-cyan-400"`).
  Chưa module nào dùng cyan nên không đụng màu nhận diện sẵn có.
- **`SIDEBAR_ZONES`** (`frontend/src/app/nav-layout.ts`): zone `ops` thành
  `["sales", "cskh", "crm", "inventory", "catalog", "marketing", "aftersales"]`.
- **RBAC không đổi.** Quyền gán theo *section*, module chỉ là lớp hiển thị;
  `accessibleModules` và `buildNavModel` tự lọc theo `allowed_sections`.

## Phần 2 — Xóa trang Ngoại sàn, gồm cả cột DB

### Quyết định

Xóa toàn bộ: FE, endpoint, RBAC section, và cột DB. Đã xác nhận rõ đây là thao
tác **không hoàn tác được**: `Order.outside` và `Order.shipping_form` của đơn gửi
ngoài cũ mất vĩnh viễn.

Bốn giá trị `OrderType` chỉ dùng cho luồng gửi ngoài (`same_product`,
`different_product`, `purchase`, `warranty_return`) cũng gỡ khỏi `choices`.
Django `TextChoices` không ràng buộc ở tầng DB, nên **đơn cũ mang các giá trị này
vẫn giữ nguyên chuỗi trong DB và sẽ hiển thị mã thô** (vd `same_product`) thay vì
nhãn tiếng Việt. Đây là đánh đổi đã chấp nhận, không phải lỗi.

### Không có tác dụng phụ rò đơn

`list_orders` hiện **không** khóa `outside=False` — đơn gửi ngoài vốn đã xuất
hiện ở trang Đơn hàng chung. Bỏ cột không làm đơn nào "lọt vào" chỗ mới.

### Phạm vi xóa

Frontend:

- Route `sales/outside-orders` và `sales/outside-orders/:orderId`
  (`app/routes.tsx`), `OutsideOrdersRoute` (`app/routed-pages.tsx`).
- Section `outside-orders` khỏi `AppSection`, `SECTION_TO_PATH`,
  `parseOrdersView`, `sectionFromPath`, `outsideOrderDetailPath`
  (`lib/routes.ts`); khỏi `modules.ts`.
- Nhánh `outside` trong `App.tsx`, `app/RootLayout.tsx`, `app/orders-tab.ts`,
  `hooks/use-crm-websocket.ts`, `features/orders/OrdersTable/useOrdersTableData.ts`,
  `pages/OrdersPage.tsx`.
- Scope `"outside" | "outside-delivered" | "outside-cancelled"` khỏi
  `features/orders/order-storage.ts`.
- `outsideMode`, `SHIPPING_FORM_VALUES`, `OUTSIDE_ORDER_TYPE_VALUES` khỏi
  `OrderFormDialog.tsx` / `order-form/order-form-helpers.ts`; các giá trị
  `OrderType` chỉ-dùng-gửi-ngoài khỏi bảng nhãn.
- Cột `shipping_form` khỏi `order-types.ts` (`OrderColumnKey`, danh sách cột,
  map field), khỏi `OrdersTable/OrderCell.tsx`, khỏi bộ lọc dùng chung
  `orders-page/use-orders-filter-options.ts`, khỏi `order-import-format.ts`
  (`OUTSIDE_COLUMN_KEYS`), khỏi `order-payload.ts`.
- Dòng `outside-orders` trong `pages/RolesPage.tsx`.

Backend:

- `list_outside_orders` + route `outside` (`urls.py`, `views.py`,
  `transport/views/order_views_impl/__init__.py`, `order_list_view.py`).
- `"outside-orders"` khỏi `ORDER_SECTIONS` (`apps/users/permissions.py`),
  khỏi `_SECTION_ORDER_IDENTITY_Q` trong `_view_helpers.py`, khỏi
  `VALID_SECTIONS` (`apps/users/models.py`).
- Nhánh `section == "outside-orders"` trong `_section_filter_overrides`
  (`repositories.py`), filter `outside` và `shipping_form` trong
  `_apply_list_filters`, key `shipping_form` trong `build_orders_filters`.
- `ShippingForm` choices và 4 giá trị `OrderType` nói trên (`models.py`);
  đối số `outside=bool(payload.get("outside", False))` khi tạo đơn
  (`order_service_commands.py`); `shipping_form` khỏi `OrderSerializer` và
  `order_service_helpers.py`.

Migration (app `orders`, nối sau `0118`):

1. Xóa index `ord_outside_cr_idx`.
2. `RemoveField` `outside`, `shipping_form`.
3. Sửa `choices` của `order_type` (AlterField, không đụng dữ liệu).

Migration (app `users`, nối sau `0033`) — data-migration, có `reverse` no-op:

1. Xóa `Permission` có `section="outside-orders"` (cascade sang
   `rbac_role_permissions`).
2. Xóa `RoleSectionScope` có `section="outside-orders"`.

### Quy trình chạy trên prod

Trước khi apply migration `orders`:

```sql
SELECT count(*) FROM orders WHERE outside;
\copy (SELECT id, order_code, outside, shipping_form, order_type
       FROM orders WHERE outside OR shipping_form <> '')
  TO 'outside_orders_backup_20260811.csv' CSV HEADER
```

Giữ file CSV lại; chạy migration vào giờ thấp điểm.

## Phần 3 — Tab Tư vấn giữ đơn ở lại

### Nguyên nhân gốc

Sheet 3 định nghĩa bằng `info_complete = False` — thiếu **tên hoặc SĐT hoặc địa
chỉ** (`sheet_rules.is_info_complete`). Đây là thuộc tính *động*: sale điền địa
chỉ vào đơn đang tư vấn thì `info_complete` thành `True`, đơn rơi khỏi Sheet 3 và
nhảy sang Sheet 1/Sheet 2. Sale mất dấu đơn mình đang theo.

Cột bất biến đúng nghĩa đã tồn tại: `Order.has_address` (migration `0094`), gắn
một lần lúc ingest (`order_ingest_views.py`), không đổi khi sale sửa đơn.

Ghi nhận: commit `64d3b0f1` ("lock sheet3 consulting tab to no-address orders",
13/07) đã dùng đúng cột này nhưng **không có trong master** — bị commit audit
`134af9aa` (15/07) ghi đè. Thiết kế dưới đây khôi phục ý đồ đó và mở rộng.

### Quy tắc mới

```
Sheet 3 (Tư vấn)          = nguồn thuộc SHEET3_SOURCES
                            AND (has_address = False OR info_complete = False)
Sheet 1 (Landing XN đơn)  = nguồn landing/hotline/zalo
                            AND info_complete AND has_address
Sheet 2 (Mess XN đơn)     = nguồn messenger
                            AND info_complete AND has_address
```

Ba tập rời nhau, phủ đúng tập đơn landing như trước. Hệ quả:

- Đơn đổ về không có địa chỉ **ở lại tab Tư vấn vĩnh viễn**, kể cả khi sale điền
  đủ tên/SĐT/địa chỉ.
- Đơn đó **không bao giờ** xuất hiện ở Landing XN đơn / Mess XN đơn — không đếm
  trùng khi đối soát.
- Khi đơn chốt/lên đơn thì rời board Tư vấn như hiện nay
  (`sheet3_view` loại `CLOSED_SALES_STATUSES`). Đơn vẫn thấy đầy đủ ở tab **Tổng
  hợp**, **Giao thành công**, **Đã hủy** — ba tab này không lọc theo sheet
  (`orders-tab.ts` map tab `summary` → scope `landing-summary`, không gửi
  `sheet`). Không có đơn nào biến mất khỏi giao diện.
- Đơn thiếu tên/SĐT nhưng ingest **có** địa chỉ vẫn nằm ở Tư vấn như hiện tại.

### Dữ liệu cũ

Đơn tạo trước migration `0094` có `has_address = True` (default). Đơn tư vấn cũ
vì thế vẫn hành xử như trước: rời Tư vấn khi được điền đủ thông tin. Không
backfill được vì không còn dấu vết địa chỉ tại thời điểm đổ đơn. Chỉ đơn ingest
sau `0094` mới sticky.

### Nơi đặt luật

Luật Sheet 1/2 hiện **chép tay ở ba chỗ** phải khớp nhau mà không có gì ép:

- `order_list_view.py` (~dòng 413) — list Sheet 1/2 trên màn hình.
- `repositories.py::_section_filter_overrides` (~dòng 150) — job xuất Excel.
- `sheet_rules.py::sheet_q()` / `classify_sheet()` — bộ chia đơn tự động.

Gom về `sheet_rules` làm nguồn sự thật duy nhất:

- `is_info_complete(...)` giữ nguyên.
- `classify_sheet(...)` nhận thêm tham số `has_address: bool = True`.
- `sheet_q(sheet)` cập nhật theo quy tắc mới.
- Ba nơi trên gọi vào `sheet_q()` thay vì tự dựng filter.
- `sheet3_view.py` bỏ cặp `info_complete="false"` + `has_address` passthrough,
  thay bằng `sheet_q("sheet3")`; vẫn parse `has_address` từ body như một bộ lọc
  chip thường (AND trong phạm vi board — vẫn có nghĩa vì board chứa cả đơn thiếu
  tên/SĐT mà có địa chỉ).

Đây là dọn dẹp nằm đúng trong phạm vi đang sửa, không phải refactor lạc đề: nếu
để nguyên ba bản chép tay thì thay đổi này phải sửa cả ba và lần sau vẫn thế.

### Hiệu năng

Không thêm cột, không thêm index. Sheet 3 thêm một nhánh `OR has_address = False`
— tập nhỏ, đã có partial index `ord_no_address_created_idx` (`WHERE NOT
has_address`) cho nhánh đó. Sheet 1/2 thêm điều kiện `has_address = True` lọc bỏ
một phần rất nhỏ, không đổi kế hoạch truy vấn.

## Phần 4 — Báo cáo sale Nhóm 10 chia theo tab

### Hiện trạng

`DashboardPage` → `SaleSourceRevenueSection`, dữ liệu từ
`OrderRepository.summary_closed_revenue_by_staff_source` (`_repositories_reports.py`).
Ma trận `sale × 4 nguồn` (`landing`, `zalo`, `messenger`, `hotline`), mỗi ô 4 chỉ
số: `closed_orders`, `closed_revenue`, `returned_orders`, `net_revenue`.

### Thiết kế

Đổi trục cột thành `sale × 3 sheet`: **Landing xác nhận đơn**, **Messenger xác
nhận đơn**, **Tư vấn**, cộng nhóm cột **Tổng**.

Backend:

- Đổi tên `summary_closed_revenue_by_staff_source` →
  `summary_closed_revenue_by_staff_sheet`; key payload
  `closed_revenue_by_staff_source` → `closed_revenue_by_staff_sheet`
  (`dashboard_report_views.py`).
- Thay `.values(..., "order_source")` bằng annotate:

  ```python
  Case(
      When(sheet_q("sheet3"), then=Value("sheet3")),
      When(sheet_q("sheet2"), then=Value("sheet2")),
      When(sheet_q("sheet1"), then=Value("sheet1")),
      default=Value(""),
      output_field=CharField(),
  )
  ```

  Thứ tự `sheet3` trước là bắt buộc — ba `Q` rời nhau nên thứ tự không đổi kết
  quả, nhưng đặt vậy để đọc khớp với thứ tự ưu tiên nghiệp vụ.
- Giữ nguyên `order_source__in=SALE_SOURCE_REPORT_ORDER_SOURCES` và tập đơn
  `CLOSED_REVENUE_SALES_STATUS_CODES`, nên tổng Nhóm 10 vẫn nhỏ hơn Nhóm 9 đúng
  bằng phần đơn sàn/KOC/chưa gắn nguồn — không đổi so với hiện tại.
- Vẫn một query group-by, ~25 sale × 3 = 75 hàng (trước là ×4 = 100).

Frontend (`features/dashboard/sale-source-revenue.ts` → đổi tên file thành
`sale-sheet-revenue.ts`):

- `SALE_SOURCE_REPORT_SOURCES` → `SALE_SHEET_REPORT_KEYS = ["sheet1", "sheet2", "sheet3"]`.
- `SaleSourceRevenueRow.order_source` → `.sheet`.
- Nhãn cột **dùng chung một const với tab trang Landing**. Nhãn 3 tab hiện là
  chuỗi hard-code trong `features/orders/landing-tabs.ts` (không có khoá i18n),
  nên tách ra `SHEET_LABELS` và cho cả `LANDING_SHEET_TABS` lẫn bảng Nhóm 10
  đọc từ đó — hai chỗ không lệch chữ.
- Giữ nguyên hành vi "giá trị ngoài danh sách vẫn cộng vào ô Tổng" — hàng có
  `sheet = ""` (đơn không rơi vào sheet nào) không mất khỏi tổng bảng.

Ghi chú OpenAPI: `closed_revenue_by_staff_source` khai báo là `JSONField` trong
`@extend_schema`, không mô tả field con, nên đổi tên key **có** đổi `schema.yaml`
(tên property) nhưng không kéo theo thay đổi cấu trúc. Chỉ cập nhật đúng khối
này, không regen toàn file (`schema.yaml` đang lệch sẵn ~444 dòng so với code —
trả nợ đó bằng MR chore riêng).

### Lợi ích kèm theo

Vì Phần 3 làm sheet trở nên sticky, cột "Tư vấn" của Nhóm 10 chính là doanh thu
sale chốt được từ data không có địa chỉ — con số trước đây không tách ra được vì
đơn đã nhảy sang Sheet 1/2 trước khi chốt.

## Kiểm thử

Bổ sung vào các file suite sẵn có, không tạo file lẻ cho từng chức năng:

| File | Nội dung thêm |
| --- | --- |
| `backend/apps/orders/tests/test_order_list_contract.py` | Class mới `LandingSheetRoutingTests`: đơn `has_address=False` ở lại Sheet 3 sau khi điền đủ tên/SĐT/địa chỉ; đơn đó không xuất hiện ở Sheet 1 và Sheet 2; đơn `has_address=True` thiếu SĐT vẫn ở Sheet 3 |
| `backend/apps/orders/tests/test_sale_reports.py` | Nhóm 10 trả `sheet` thay `order_source`; đơn tư vấn đã chốt rơi vào cột `sheet3`; tổng ba cột khớp cột Tổng |
| `backend/apps/orders/tests/test_lead_assignment.py` | `classify_sheet` với `has_address=False` trả `sheet3` dù đủ thông tin |
| `backend/apps/users/tests/test_rbac_sections.py` | `outside-orders` không còn trong `VALID_SECTIONS`; không còn `Permission`/`RoleSectionScope` nào mang section đó sau migration |
| `frontend/src/app/__tests__/modules.test.ts` | Hai module `sales`/`cskh` đúng section; không còn `outside-orders` |
| `frontend/src/app/__tests__/nav-layout.test.ts` | Zone `ops` có `cskh` ngay sau `sales`; nhóm CSKH lọc đúng theo `allowed_sections` |
| `frontend/src/lib/__tests__/routes.test.ts` | Gỡ case `outside-orders`; path CSKH không đổi |
| `frontend/src/features/dashboard/__tests__/sale-source-revenue.test.tsx` | Ma trận 3 cột sheet; hàng `sheet=""` vẫn vào ô Tổng |

Backend chạy bằng Docker (repo không có CI — `.gitlab-ci.yml` đã bị xóa ở
`eb15fe28`, mọi lint/test chạy tay). Frontend chạy trong worktree anh em nên
junction `node_modules` dùng được.

## Thứ tự triển khai và cắt MR

Ba MR độc lập, tách vì Phần 2 có migration phá dữ liệu, không nên nằm chung với
thay đổi giao diện:

1. **MR 1 — Sidebar** (Phần 1). Thuần FE, rủi ro thấp, merge trước.
   Plan: `docs/superpowers/plans/2026-08-11-sales-nav-split.md`.
2. **MR 2 — Sheet 3 sticky + Nhóm 10** (Phần 3 + 4). Đi chung vì Nhóm 10 dùng
   lại `sheet_q()` do Phần 3 sửa; tách ra sẽ phải merge tuần tự mà vẫn dính nhau.
   Plan: `docs/superpowers/plans/2026-08-11-consulting-tab-sticky.md`.
3. **MR 3 — Xóa Ngoại sàn** (Phần 2). Kèm hướng dẫn backup + chạy migration giờ
   thấp điểm. Phải merge SAU MR 1 — cả hai cùng sửa `app/modules.ts`.
   Plan: `docs/superpowers/plans/2026-08-11-remove-outside-orders.md`.

## Rủi ro

| Rủi ro | Giảm thiểu |
| --- | --- |
| Drop cột `outside`/`shipping_form` không hoàn tác được | Dump CSV trước khi chạy; chạy giờ thấp điểm |
| Đơn cũ mang `order_type` đã gỡ khỏi choices hiển thị mã thô | Đã chấp nhận; nêu rõ trong mô tả MR |
| Đơn trước migration `0094` không sticky được | Không backfill được; nêu rõ trong mô tả MR để sale không báo lỗi nhầm |
| Ba bản chép tay luật Sheet 1/2 lệch nhau sau khi sửa | Gom về `sheet_q()`; test phủ cả list view lẫn export |
| `desi.lastModule` trỏ module không còn tồn tại | Giữ key `sales`; `route-guards` đã xử lý `moduleByKey` trả `undefined` |
