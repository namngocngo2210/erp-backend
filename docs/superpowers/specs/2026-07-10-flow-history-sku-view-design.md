# Lịch sử nhập xuất — Chế độ "Xem theo SKU"

- **Ngày:** 2026-07-10
- **Branch:** `feat/flow-history-sku-view`
- **Trang:** Lịch sử nhập xuất (`/inventory/dispatch-history`, section RBAC `stock`)
- **Liên quan:** MR !868 (trang gốc), spec `2026-07-08-inventory-flow-history-design.md`

## Bối cảnh

Trang hiện tại hiển thị lịch sử **theo đơn/phiếu**: mỗi dòng là một đơn (tab Xuất) hoặc một phiếu trả (tab Nhập hoàn), bấm vào drill-down ra các dòng SKU. Người dùng muốn thêm một góc nhìn **theo SKU**: gộp số lượng của từng SKU qua tất cả đơn, để trả lời "SKU này đã xuất/hoàn tổng bao nhiêu, ở những đơn nào".

## Mục tiêu

Thêm toggle **"Theo đơn / Theo SKU"** áp dụng cho **cả hai tab**. Ở chế độ SKU, bảng **đảo chiều gộp** so với hiện tại: mỗi dòng là một SKU (gộp qua mọi đơn), drill-down ra danh sách đơn/phiếu chứa SKU đó.

Không thay đổi hành vi mặc định: trang vẫn mở ở chế độ "Theo đơn".

## Quyết định thiết kế (đã chốt với người dùng)

1. **Ý nghĩa SKU view:** gộp theo SKU, có drill-down ra đơn (đảo chiều đúng với view hiện tại) — không phải bảng phẳng.
2. **Phạm vi:** cả 2 tab (Xuất hàng ra + Nhập hàng hoàn về), toggle **chung** đặt trên cùng cạnh bộ lọc.
3. **Mã chờ khớp (pending scan):** **ẩn** ở SKU view (không có thông tin SKU nên không gộp được). Chỉ hiện ở view Theo đơn như hiện tại.
4. **Excel:** nút "Xuất Excel" xuất **theo view đang xem** — khi ở SKU view thì xuất bảng gộp theo SKU.

## Backend

### `DispatchHistoryService` (tab Xuất) — thêm 2 method

Tái dùng `base_queryset(**filters)` sẵn có (đã hỗ trợ `product_id`, các filter ngày/kho/product/order).

- **`skus_page(*, pagination, **filters) -> dict`**
  - Gộp: `qs.values("product_id", "product__sku", "product__name").annotate(sale_sum=Coalesce(Sum(qty_delta, filter=movement_type=sale), 0), rev_sum=Coalesce(Sum(qty_delta, filter=movement_type=sale_reversal), 0), order_count=Count("reference_id", distinct=True), last_at=Max("created_at")).order_by("-last_at", "-product_id")`.
  - Cột item: `product_id · sku · name · order_count · dispatched · reversed · net (= dispatched − reversed) · last_at`.
  - `total` = số SKU distinct khớp filter. `total_quantity` = `-qs.aggregate(Sum(qty_delta))` (giữ nguyên công thức `orders_page`, giá trị bằng nhau vì cùng movements, chỉ khác chiều gộp).
- **`sku_orders(*, product_id, **filters) -> list[dict]`**
  - Drill-down đối xứng với `order_lines`: `base_queryset(product_id=product_id, **filters)` gộp theo `reference_id`, trả các đơn (`order_id · order_code · invoice_code · shipping_code · warehouse_name · net · last_at`), sort theo net giảm dần.
  - Bỏ `product` (text filter) khi drill để tránh double-filter; giữ các filter khác.

### `ReturnHistoryService` (tab Nhập hoàn) — thêm 2 method

- **`skus_page(*, pagination, **filters) -> dict`**
  - Gộp `ReturnNoteLine` của phiếu `status=POSTED` khớp `_base(**filters)` theo `product_id`: `Sum(quantity)`, `return_count=Count("return_note", distinct=True)`.
  - Cột item: `product_id · sku · name · return_count · qty_total`.
  - `total` = số SKU distinct. `total_quantity` = tổng `quantity` toàn bộ (giữ nguyên như `list_page`).
- **`sku_returns(*, product_id, **filters) -> list[dict]`**
  - Drill-down: các phiếu trả (`POSTED`, khớp filter) chứa `product_id`, trả `return_id · code · source_order_invoice_code/code · shipping_code · warehouse_name · return_type_label · quantity · created_at`.

### Endpoints (transport `flow_history_views.py`)

Thêm **4 GET endpoint mới**, mỗi cái có `inline_serializer` riêng (để FE typed từ `schema.d.ts` — theo convention CLAUDE.md, không tự khai type tay):

| Endpoint | Service |
|---|---|
| `GET issue-history/skus` | `DispatchHistoryService.skus_page` |
| `GET issue-history/skus/orders?product_id=` | `DispatchHistoryService.sku_orders` |
| `GET return-history/skus` | `ReturnHistoryService.skus_page` |
| `GET return-history/skus/returns?product_id=` | `ReturnHistoryService.sku_returns` |

- Tất cả `@permission_classes([IsAuthenticated, require_section("stock")])` + `@never_cache`, dùng `_common_filters` như các endpoint hiện có.
- Drill-down validate `product_id` bắt buộc (giống `order_id`/`return_id` hiện tại).
- URL đăng ký trong `apps/inventory/urls.py` cạnh các route flow-history hiện có.

### Export

- Thêm query `?group=sku` vào **2 endpoint export sẵn có** (`issue_export`, `return_export`) — response binary `.xlsx` (đã `@extend_schema(exclude=True)` nên không ảnh hưởng schema).
  - `group != "sku"` → hành vi cũ (export theo đơn/phiếu).
  - `group == "sku"` → gọi `skus_page(limit=5000)` + hàm Excel mới, **không** kèm pending (SKU view vốn ẩn pending).
- `flow_history_excel.py`: thêm `export_issue_skus(items, total_quantity)` và `export_return_skus(items, total_quantity)` — cột theo bảng SKU, có dòng Tổng như hàm hiện tại.

## Frontend

### Toggle & shell

- `DispatchHistoryPage.tsx` giữ vai trò **shell**: `PageHeader` + hàng filter + toggle + `Tabs`.
  - Thêm state `viewMode: "order" | "sku"` (mặc định `"order"`), render segmented control bằng `components/ui` (không tự style, không `react-select`), đặt cạnh hàng filter.
  - Truyền `viewMode` xuống cả `IssueTab` và `ReturnTab`.

### Tách file (cải thiện có chủ đích)

`DispatchHistoryPage.tsx` hiện 631 dòng; nhồi thêm SKU view sẽ vượt ~1000. Tách theo trách nhiệm (giữ đúng vị trí `features/inventory`):

- `pages/DispatchHistoryPage.tsx` — shell (như trên).
- `components/IssueTab.tsx` — tab Xuất, chọn render `OrderView` hoặc `SkuView` theo `viewMode`.
- `components/ReturnTab.tsx` — tab Nhập hoàn, tương tự.
- `components/flow-shared.tsx` — primitive dùng chung: `InlineNoteCell`, `ReturnNoteCell`, `PendingNoteCell`, `Pager`, `ExportButton`, `PendingBadge`, `PendingTime`, `PendingActions`.

Không đổi route/chunk name; không rename `DispatchHistoryPage`.

### Render SKU view (mỗi tab)

- Bảng SKU: cột `SKU · Tên · Số đơn (Số phiếu) · SL`. Bấm dòng → xổ sub-table các đơn/phiếu (`mã hóa đơn · mã vận đơn · kho · SL`).
- **Không** dòng pending, **không** cột Ghi chú ở SKU view.
- `tfoot`: "Tổng SL (N SKU)".
- Phân trang `Pager` như hiện tại (limit 50).

### Query hooks & types

- `issue-history-queries.ts`: `useIssueSkus(filters, pagination)`, `useIssueSkuOrders(productId, filters, enabled)`, `downloadIssueExport(filters, group)`.
- `return-history-queries.ts`: `useReturnSkus(...)`, `useReturnSkuReturns(...)`, `downloadReturnExport(filters, group)`.
- `lib/api/inventory-flow.ts`: type SKU item + drill-down, regenerate từ `schema.d.ts` bằng `npm run gen:api` sau khi BE có endpoint.
- Nút Excel đọc `viewMode` → truyền `group` tương ứng.

## Test

### Backend (`tests/`)
- `test_flow_history_views.py`: 4 endpoint SKU — gộp đúng qua nhiều đơn, `net`/`qty_total` đúng, `order_count`/`return_count` distinct đúng, drill-down trả đúng đơn/phiếu, filter `product`/`order`/kho/ngày áp dụng, `product_id` thiếu → 400.
- `test_flow_history_export.py`: `?group=sku` cho cả 2 tab trả `.xlsx` với các dòng SKU + dòng Tổng.

### Frontend (`__tests__/`)
- Toggle đổi `viewMode` → đổi bảng.
- SKU view **ẩn** dòng pending.
- Drill-down SKU gọi đúng endpoint `skus/orders` / `skus/returns`.

## Ngoài phạm vi (YAGNI)

- Không thêm filter/sort mới ngoài các filter sẵn có.
- Không đổi hành vi mặc định (vẫn mở view Theo đơn).
- Không gộp pending vào SKU view.
- Không sửa view Theo đơn hiện tại (chỉ tách file, không đổi logic).
