# Lịch sử nhập xuất (Inventory Flow History)

- **Ngày:** 2026-07-08
- **Nhánh:** `feat/inventory-flow-history`
- **Trạng thái:** Đã duyệt thiết kế

## 1. Bối cảnh & mục tiêu

Trang hiện tại **"Lịch sử xuất kho"** (`DispatchHistoryPage`) là view **theo sản phẩm**:
bảng tổng hợp SKU + số lượng đã xuất, bấm vào drill-down chi tiết từng chuyển động.
Dữ liệu lấy từ `StockMovement` với `movement_type in [sale, sale_reversal]`,
`reference_type = "order"`.

Yêu cầu: đổi thành trang **"Lịch sử nhập xuất"** với 2 danh sách nhóm-theo-đơn:

1. **Xuất hàng ra khỏi kho** — các đơn đã giao (xuất kho).
2. **Nhập hàng hoàn về** — hàng vật lý hoàn về kho qua phiếu trả.

Kèm chức năng **xuất Excel** danh sách theo bộ lọc (khoảng ngày).

## 2. Nguồn dữ liệu (đã xác minh trong codebase)

Trong `apps/inventory` có 3 loại chuyển động liên quan:

| movement_type | reference_type | Ý nghĩa | Dùng cho |
|---|---|---|---|
| `sale` | `order` | Xuất kho khi giao đơn (delta âm) | Tab **Xuất** |
| `sale_reversal` | `order` | Đảo ngược lần giao (đơn hủy/un-dispatch nhả tồn, delta dương) | Trừ net trong tab **Xuất** |
| `RETURN_IN` | `return` | Hàng vật lý hoàn về kho, tạo khi **ghi sổ** `ReturnNote` (phiếu TH…) | Tab **Nhập hoàn** |

**Quyết định:** Tab "Nhập hàng hoàn về" lấy từ `ReturnNote` (không dùng `sale_reversal`),
vì đó mới là hàng vật lý hoàn về kho. `sale_reversal` chỉ dùng để trừ net trong tab Xuất.

## 3. Kiến trúc

### 3.1 Backend (`apps/inventory`, clean architecture)

**Service layer:**

- `application/services/dispatch_history_service.py` (mở rộng):
  - `base_queryset(...)` — bổ sung hỗ trợ lọc theo `order_id` (cho drill-down).
  - `orders_page(*, pagination, **filters) -> dict` — gộp `StockMovement (sale/sale_reversal)`
    theo `reference_id` (order), trả list phân trang. Mỗi item:
    `{order_id, order_code, shipping_code, warehouse_name, sku_count, dispatched, reversed, net, last_at}`.
    Gộp bằng `.values("reference_id").annotate(...)`; nhãn order_code/shipping_code/warehouse
    resolve theo batch (không N+1).
  - `order_lines(*, order_id, **filters) -> list` — chi tiết SKU của 1 đơn (drill-down),
    dùng lại `detail_queryset` với filter `order_id`.

- `application/services/return_history_service.py` (mới):
  - `list_page(*, pagination, **filters) -> dict` — `ReturnNote` `status = posted`, lọc theo
    date/warehouse/product/order, trả list phân trang. Mỗi item:
    `{return_id, code, source_order_code, shipping_code, warehouse_name, return_type,
    return_type_label, status, status_label, qty_total, created_at}`.
    Lọc product: qua `ReturnNoteLine.product` (Exists subquery, tránh N+1).
    Lọc order: theo `source_order__order_code` / `shipping_code`.
  - `lines(*, return_id) -> list` — `ReturnNoteLine` của 1 phiếu (SKU · SL · loại dòng).

- `infrastructure/flow_history_excel.py` (mới) — hàm dựng workbook openpyxl,
  theo pattern `apps/orders/export_excel.py`, trả `HttpResponse` xlsx. 2 hàm:
  `export_issue_orders(rows) `, `export_return_notes(rows)`. Mỗi dòng = 1 đơn/phiếu khớp danh sách.

**Transport layer:** `transport/views/flow_history_views.py` (mới) — tách khỏi
`dispatch_history_views.py` cũ (giữ lại 2 endpoint cũ để không phá schema đột ngột;
xoá sau khi FE chuyển xong — xem mục 6). Tất cả `@permission_classes([IsAuthenticated, require_section("stock")])`.

Endpoints (đăng ký trong `apps/inventory/urls.py`):

| Method | Path | Mô tả |
|---|---|---|
| GET | `/api/inventory/dispatch-history/orders` | Danh sách đơn xuất (phân trang) |
| GET | `/api/inventory/dispatch-history/orders/lines?order_id=` | Drill-down SKU 1 đơn |
| GET | `/api/inventory/dispatch-history/export` | Xuất Excel danh sách đơn xuất |
| GET | `/api/inventory/return-history` | Danh sách phiếu hoàn (phân trang) |
| GET | `/api/inventory/return-history/lines?return_id=` | Drill-down SKU 1 phiếu hoàn |
| GET | `/api/inventory/return-history/export` | Xuất Excel danh sách phiếu hoàn |

Query params dùng chung: `date_from`, `date_to` (YYYY-MM-DD), `warehouse_id`, `product`, `order`,
`limit`, `offset`. Validate ngày như `_valid_date` hiện có; pagination qua `parse_required_pagination`.

### 3.2 Frontend (`frontend/src`, theo convention)

- `features/inventory/pages/DispatchHistoryPage.tsx` — rework:
  - Đổi `PageHeader title` → "Lịch sử nhập xuất".
  - Bộ lọc dùng chung giữ nguyên (DateFilter, Kho, SP, Đơn).
  - `components/ui/tabs`: 2 tab `Xuất hàng ra` | `Nhập hàng hoàn về`.
  - Mỗi tab: bảng danh sách (phân trang) + nút "Xuất Excel" + drill-down row.
  - Bỏ bảng tổng hợp theo sản phẩm cũ.
- `features/inventory/issue-history-queries.ts` (mới) — React Query cho orders list + order lines.
- `features/inventory/return-history-queries.ts` (mới) — React Query cho return list + return lines.
- `lib/api/inventory-flow.ts` (mới) — typed từ `api/schema.d.ts` (chạy `pnpm gen:api` sau khi BE có schema).
- Nút Xuất Excel: `fetch` blob rồi trigger download (không qua React Query).
- `i18n/messages/topnav.ts` — đổi `topnav.tabs.dispatchHistory` → "Lịch sử nhập xuất".
- Route key `dispatch-history` + section `stock` giữ nguyên (không đổi URL để không phá bookmark).
- Xoá `dispatch-history-queries.ts` cũ + test liên quan sau khi rework xong.

## 4. Chi tiết cột hiển thị

**Tab Xuất hàng ra:** Thời gian xuất (last_at) · Mã đơn · Mã vận đơn · Kho · Số SKU · Tổng SL xuất (net).
Drill-down: SKU · Tên · SL · Tồn sau.

**Tab Nhập hàng hoàn về:** Thời gian · Mã phiếu trả · Mã đơn nguồn · Mã vận đơn · Kho ·
Loại hoàn · Trạng thái · Tổng SL hoàn.
Drill-down: SKU · Tên · SL · Loại dòng.

## 5. Quyết định đã chốt

1. **Nhập hoàn chỉ hiển thị phiếu đã ghi sổ (posted)** — hàng thực nhập kho.
2. **Excel ở mức 1 dòng/đơn** khớp danh sách; chưa tách sheet chi tiết SKU (YAGNI, thêm sau nếu cần).
3. **RBAC `stock`** cho cả 2 tab để trang thống nhất (giống endpoint dispatch-history hiện tại).
4. **Giữ URL `/inventory/dispatch-history`** và route key `dispatch-history` (chỉ đổi tiêu đề/nội dung).

## 6. Dọn dẹp endpoint cũ

`dispatch_history_summary` + `dispatch_history_detail` (view theo sản phẩm) sẽ **không còn dùng** ở FE.
Sau khi FE chuyển hẳn sang endpoint mới, xoá:
- View `dispatch_history_summary`, `dispatch_history_detail` + route.
- `dispatch-history-queries.ts`, `dispatch-history-queries.test.ts`, `test_dispatch_history_summary.py`,
  `test_dispatch_history_detail.py` (thay bằng test mới).

Làm trong cùng MR để tránh dead code.

## 7. Test

**Backend (`apps/inventory/tests/`):**
- `test_issue_orders_history.py` — gộp theo đơn đúng, net trừ `sale_reversal`, lọc ngày/kho/SP/đơn, phân trang.
- `test_return_history.py` — chỉ lấy posted, gộp qty theo phiếu, lọc product qua line, drill-down lines.
- Export: smoke test 200 + content-type xlsx.

**Frontend:**
- Test builder params (query string) cho 2 file queries mới.

## 8. Rủi ro / lưu ý

- **Gộp theo order + phân trang trên UNION/aggregate:** đảm bảo `count()` phân trang tính trên số
  nhóm (đơn), không phải số movement. Dùng subquery/`.values().annotate()` rồi count distinct đơn.
- **N+1:** resolve nhãn order/warehouse/return theo batch cho trang hiện tại.
- **product filter ở tab hoàn:** lọc qua `ReturnNoteLine` bằng `Exists` để không nhân dòng.
- **openpyxl** đã có sẵn (dùng ở `apps/orders/export_excel.py`).
