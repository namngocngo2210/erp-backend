# Thiết kế: Trang Tồn kho theo kho + lịch sử cộng/trừ tồn

- **Ngày:** 2026-06-17
- **Branch:** `feature/inventory-stock-by-warehouse-history`
- **Module:** `inventory` (BE: `apps/inventory`, FE: `frontend/src/features/inventory`)
- **Trạng thái:** Đã chốt thiết kế, chờ viết plan

## 1. Bối cảnh & vấn đề

Trang tồn kho hiện tại (`frontend/src/features/inventory/pages/StockPage.tsx`, route
`/inventory/stock`) hiển thị **gộp tất cả các kho**, pivot theo `(kho, sản phẩm)` với 4 cột
số lượng (Bán được / Bảo hành / Lỗi / Tổng) và một ô tìm kiếm SKU/tên. Không có bộ lọc chọn
kho, và không có cách xem lịch sử cộng/trừ tồn của một sản phẩm.

Yêu cầu mới:
1. Có **bộ lọc kho** — chọn 1 kho thì hiển thị tồn của các sản phẩm trong kho đó.
2. Click vào một sản phẩm → mở **dropdown lịch sử** 10 giao dịch cộng/trừ tồn gần nhất.
3. Dropdown có các cột: **thời gian, mã hoá đơn, loại giao dịch, số lượng (+/-), tồn cuối**.

## 2. Hiện trạng codebase (không cần đổi model/migration)

Backend đã có đủ dữ liệu:

- `GET /api/inventory/stock` — `apps/inventory/transport/views/stock_views.py:30` (`list_stock`),
  nhận `warehouse_id`, `search`, `state`, phân trang. Đã hỗ trợ lọc theo kho.
- `GET /api/inventory/stock/movements` — `stock_views.py:81` (`list_movements`), nhận
  `warehouse_id`, `product_id`, phân trang; service `StockQueryService.list_movements` đã
  `order_by("-created_at", "-id")` → `limit=10` cho ra 10 giao dịch gần nhất. **Hiện chưa
  được dùng trong UI.**
- Model `StockMovement` (`apps/inventory/models.py:27`): `qty_delta`, `movement_type`,
  `state`, `balance_after`, `reference_type`, `reference_id`, `created_at`. `balance_after`
  được lưu **riêng theo từng `state`** (sellable/warranty/defect) của `(warehouse, product)`.
- Danh sách kho: `GET /api/warehouses` → FE đã có `getWarehouses()` trong
  `frontend/src/lib/api/warehouses.ts`.

### Mapping `reference_type` → mã hiển thị ("mã hoá đơn")

`reference_id` trong ledger chỉ là **id nội bộ**. Hằng số `_REFERENCE_TYPE` theo từng service:

| `reference_type` | Nguồn | Mã hiển thị (`reference_label`) |
|---|---|---|
| `order` | `Order` (id) | `order_code` → fallback `invoice_code` → fallback `#<id>` |
| `document` | `StockDocument` (id) | `code` |
| `return` | `ReturnNote` (id) | `code` |
| `reclass` / khác | xử lý nội bộ | `null` → FE hiển thị "—" |

## 3. Quyết định thiết kế (đã chốt với user)

- **D1 — Bộ lọc kho:** mặc định chọn **kho đầu tiên** (sắp theo tên), luôn xem đúng 1 kho.
- **D2 — Mã hoá đơn:** **enrich mã thật ở backend** (resolve `reference_id` → mã người đọc),
  vì `balance_after`/`reference_id` thô không có ý nghĩa với người dùng. Chỉ ~10 dòng/lần mở
  nên không lo N+1 nếu batch-query.
- **D3 — Trạng thái trong lịch sử:** lịch sử gồm cả 3 trạng thái; **thêm badge trạng thái**
  (Bán được / Bảo hành / Lỗi) cạnh loại giao dịch để cột "tồn cuối" nhất quán theo từng
  trạng thái (vì `balance_after` lưu riêng theo state).

## 4. Thiết kế chi tiết

### 4.1 Backend — enrich `reference_label` (backward-compatible)

`StockMovementSerializer` (`apps/inventory/transport/serializers/stock_serializers.py:22`):
- Thêm `reference_label = serializers.SerializerMethodField()`, đọc từ
  `self.context["reference_labels"]` theo key `(reference_type, reference_id)`; trả `None`
  nếu không có. Giữ nguyên `reference_type` + `reference_id` (FE dùng để tạo link).

Logic resolve đặt trong một helper (đề xuất: `StockQueryService.resolve_reference_labels(items)`
trong `apps/inventory/application/services/stock_query_service.py`):
- Gom `reference_id` theo từng `reference_type` trong trang hiện tại.
- **Mỗi loại 1 query batch** (`Order`, `StockDocument`, `ReturnNote` — chỉ `values("id", ...)`):
  - `order`: `{id → order_code or invoice_code or f"#{id}"}`
  - `document`, `return`: `{id → code}`
- Trả `dict[(type, id) -> label]`.

Trong view `list_movements`: sau khi cắt trang, gọi helper với danh sách items, đưa map vào
`StockMovementSerializer(items, many=True, context={"reference_labels": labels})`.

> Lưu ý: `Order` có thể bị soft-delete; chỉ cần lấy `code` nên không lọc trạng thái —
> ưu tiên hiển thị đúng mã của giao dịch lịch sử.

### 4.2 Frontend — `StockPage.tsx`

**Bộ lọc kho:**
- Dùng `<Select>` (shadcn/ui) liệt kê kho từ `getWarehouses()` (chỉ kho `is_active`, sort theo
  tên). State `warehouseId: number | null`; khi danh sách kho load xong, mặc định chọn kho đầu.
- Truyền `warehouseId` vào `useStockList(warehouseId, search)` (hook đã nhận `warehouseId`).
- Ô tìm SKU/tên giữ nguyên, lọc trong phạm vi kho đang chọn.

**Bảng tồn (desktop):**
- Vì chỉ 1 kho → **bỏ cột "Kho"**, thêm **cột mũi tên expand** ở đầu. Các cột số lượng
  (Bán được / Bảo hành / Lỗi / Tổng) và nút "Xử lý" giữ nguyên hành vi hiện tại.
- Click vào dòng sản phẩm (hoặc mũi tên) → toggle mở **một dòng con full-width** (accordion).
  Click nút "Xử lý" không được trigger expand (stopPropagation).

**Dòng mở rộng — lịch sử giao dịch:** component mới `StockMovementHistory.tsx`, bảng con:

| Thời gian | Mã hoá đơn | Loại giao dịch | Số lượng | Tồn cuối |
|---|---|---|---|---|

- **Thời gian:** `created_at` → `dd/MM/yyyy HH:mm`.
- **Mã hoá đơn:** `reference_label`; nếu `reference_type === "order"` render link mở đơn
  (theo cách điều hướng đơn hàng hiện có); rỗng → "—".
- **Loại giao dịch:** nhãn tiếng Việt từ `MOVEMENT_TYPE_LABELS` + **badge trạng thái** nhỏ
  (`STATE_LABELS`).
- **Số lượng:** `qty_delta` có dấu, `+` xanh / `−` đỏ (tái dùng quy ước màu của `qtyClass`).
- **Tồn cuối:** `balance_after`.
- Trạng thái: loading ("Đang tải…"), rỗng ("Chưa có giao dịch.").

### 4.3 Hooks & types (`queries.ts`, `types.ts`)

- Type mới `StockMovementRow`:
  `{ id, warehouse, warehouse_name, product, product_sku, qty_delta, movement_type, state,
  balance_after, reference_type, reference_id, reference_label, note, created_at }`.
- Hằng `MOVEMENT_TYPE_LABELS` (vi) và `STATE_LABELS` (vi):
  - `receipt`=Nhập kho, `issue`=Xuất kho, `adjustment`=Điều chỉnh,
    `transfer_in`=Nhập chuyển kho, `transfer_out`=Xuất chuyển kho, `sale`=Bán hàng,
    `sale_reversal`=Hoàn bán, `return_in`=Nhập trả hàng, `reclass_in`=Xử lý (vào),
    `reclass_out`=Xử lý (ra), `disposal`=Xuất huỷ.
  - `sellable`=Bán được, `warranty`=Bảo hành, `defect`=Lỗi.
- Hook `useStockMovements(warehouseId, productId)`:
  `GET /api/inventory/stock/movements?warehouse_id=&product_id=&limit=10&offset=0`,
  `enabled` chỉ khi dòng được mở (lazy), cache theo query key
  `["inventory", "movements", warehouseId, productId]`.

### 4.4 Responsive (theo `responsive-standard`)

- Desktop: bảng + dòng accordion.
- Mobile: `StockCard` thêm nút mở lịch sử; phần lịch sử render dạng **list dọc** (mỗi giao
  dịch một khối label/value), không bảng tràn ngang. Giữ input ≥16px (iOS), full-screen
  dialog nếu cần.

## 5. Ranh giới & rủi ro

- Không thêm bảng/migration; chỉ enrich serializer → **tương thích ngược** với các nơi khác
  gọi movements (nếu có) vì chỉ thêm field.
- N+1: helper batch-query theo loại → tối đa 3 query phụ/trang, độc lập số dòng. Có test khẳng định.
- `balance_after` theo state đã được làm rõ bằng badge trạng thái (D3) — không gây hiểu nhầm.

## 6. Testing

- **BE:** `resolve_reference_labels` trả đúng mã cho `order`/`document`/`return`, `None` cho
  `reclass`; assert số query (batch, không N+1) với ≥2 giao dịch cùng loại; `order` fallback
  `invoice_code` rồi `#id`.
- **FE:** render `StockMovementHistory` với dữ liệu mẫu — dấu +/- & màu, badge trạng thái,
  "—" khi không có `reference_label`, format thời gian. Render `StockPage` đổi kho → gọi
  `useStockList` đúng `warehouseId`.
- Chạy test BE theo `running-backend-tests` (ephemeral docker run, không dùng image prod).

## 7. Việc KHÔNG làm (YAGNI)

- Không phân trang / "xem thêm" trong lịch sử (chỉ 10 gần nhất theo yêu cầu).
- Không thêm bộ lọc theo loại giao dịch / khoảng thời gian trong dropdown.
- Không export, không sửa luồng ghi sổ (record_movement) hiện có.
