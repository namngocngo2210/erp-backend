# Trang "Lịch sử xuất kho" (Dispatch History)

Ngày: 2026-07-04
Trạng thái: Đã duyệt thiết kế, chờ lập kế hoạch triển khai.

## 1. Mục tiêu

Thêm một trang tổng hợp trong phần Kho cho phép nhân viên xem **lịch sử xuất kho theo đơn giao hàng** trong một khoảng thời gian, với bộ lọc và cả góc nhìn tổng hợp (đã xuất bao nhiêu sản phẩm) lẫn chi tiết từng lần xuất.

Phạm vi "xuất kho" = chỉ các bút toán phát sinh do đơn giao hàng:
`StockMovement.movement_type in ("sale", "sale_reversal")` và `reference_type = "order"`.
KHÔNG bao gồm xuất thủ công qua phiếu kho (`issue`/`reference_type="document"`), điều chỉnh, chuyển kho...

## 2. Quyết định đã chốt

- **Phạm vi**: chỉ đơn giao hàng (SALE), kèm hoàn/huỷ (SALE_REVERSAL) để tính ròng.
- **Kiểu hiển thị**: cả tổng hợp theo sản phẩm + nhật ký chi tiết.
- **Bộ lọc**: ngày/khoảng thời gian, theo kho, tìm theo sản phẩm (SKU/tên), tìm theo mã đơn/mã vận đơn.
- **Hoàn/huỷ**: hiển thị cả hai cột — "Đã xuất" (gross) và "Hoàn lại", cộng cột "Còn lại ròng" = Đã xuất − Hoàn lại.
- **Kiến trúc**: module backend riêng (Hướng A), không đụng `list_movements` / `StockMovementHistory` cũ.
- **Quyền**: tái dùng `require_section("stock")` — không tạo section/quyền RBAC mới, không migration.
- **Ngoài phạm vi (YAGNI)**: không có nút Xuất Excel/CSV ở trang này.

## 3. Backend

Tạo module riêng, cô lập:
- View: `backend/apps/inventory/transport/views/dispatch_history_views.py`
- Service: `backend/apps/inventory/application/services/dispatch_history_service.py`
- Đăng ký URL trong `backend/apps/inventory/urls.py`.

Cả hai endpoint là function view `@api_view(["GET"])`, `@permission_classes([IsAuthenticated, require_section("stock")])`, `@never_cache`, có `@extend_schema` + `inline_serializer`.

### 3.1 Bộ lọc dùng chung

| Param | Ý nghĩa | Xử lý |
|---|---|---|
| `date_from` | Từ ngày (YYYY-MM-DD) | `created_at__date__gte` (mẫu `ledger_views.py`) |
| `date_to` | Đến ngày (YYYY-MM-DD) | `created_at__date__lte` |
| `warehouse_id` | Lọc kho | `warehouse_id=` khi có |
| `product` | Tìm sản phẩm | `Q(product__sku__icontains) \| Q(product__name__icontains)` |
| `order` | Tìm mã đơn/mã vận đơn | resolve `Order.objects.filter(Q(order_code__icontains) \| Q(shipping_code__icontains)).values_list("id")` → `reference_id__in` |

Điều kiện cố định luôn áp dụng: `movement_type__in=["sale","sale_reversal"]`, `reference_type="order"`.

Ngày rỗng thì bỏ qua vế đó (không mặc định ở backend; frontend tự truyền khoảng mặc định). Ngày sai định dạng → 400.

### 3.2 `GET /api/inventory/dispatch-history/summary`

Gộp theo sản phẩm bằng conditional aggregation:
- `dispatched` = `-Sum(qty_delta, filter=type="sale")` (SL đã xuất, số dương)
- `reversed` = `Sum(qty_delta, filter=type="sale_reversal")` (SL hoàn lại, số dương)
- `net` = `dispatched - reversed`

Response:
```json
{
  "items": [
    { "product_id": 10, "sku": "SKU-10", "name": "SP 10",
      "dispatched": 120, "reversed": 5, "net": 115 }
  ],
  "totals": { "dispatched": 120, "reversed": 5, "net": 115, "product_count": 1 }
}
```
Sắp xếp `items` theo `dispatched` giảm dần. Không phân trang (số sản phẩm phân biệt trong khoảng là hữu hạn); nếu cần chặn biên sẽ giới hạn ở tầng service — ghi log nếu cắt.

### 3.3 `GET /api/inventory/dispatch-history` (chi tiết)

Cùng bộ lọc + phân trang qua `parse_required_pagination` (`limit`/`offset`, mặc định 50, max 200).

Mỗi dòng dựa trên `StockMovement`, tái dùng `resolve_reference_labels` để lấy nhãn đơn:
```json
{
  "items": [
    { "id": 999, "created_at": "2026-07-01T09:30:00Z",
      "movement_type": "sale", "movement_type_label": "Bán hàng",
      "order_id": 555, "order_code": "ORD-555", "shipping_code": "VD-555",
      "warehouse_name": "Kho A", "product_sku": "SKU-10", "product_name": "SP 10",
      "quantity": 3, "balance_after": 42 }
  ],
  "total": 1, "limit": 50, "offset": 0
}
```
- `quantity` = `abs(qty_delta)`; loại bút toán phân biệt xuất (sale) / hoàn (sale_reversal) qua `movement_type`.
- Sắp `-created_at, -id`.
- `order_code`/`shipping_code` resolve từ `reference_id`; nếu đơn đã xoá thì để trống nhãn nhưng vẫn hiện dòng.

## 4. Frontend

### 4.1 Định tuyến & menu
- Route mới `/inventory/dispatch-history`, guard `<Guard section="stock">`.
  - `routed-pages.tsx`: `lazy(() => import(...))` + `DispatchHistoryRoute()`.
  - `routes.tsx`: thêm entry trong nhánh `inventory/*`.
- Menu Kho (`app/modules.ts`): thêm section (icon `History` từ lucide) trỏ tới route mới, gated theo quyền `stock` (dùng `requires`/`SECTION_PERMISSION` như pattern hiện có để tái dùng quyền `stock`).
- `lib/routes.ts`: thêm `"dispatch-history"` vào `AppSection` và `SECTION_TO_PATH`, ánh xạ quyền về `stock` qua `SECTION_PERMISSION`.
- i18n: khóa `topnav.tabs.dispatchHistory` = "Lịch sử xuất kho".

### 4.2 Trang `DispatchHistoryPage`
Đặt tại `frontend/src/features/inventory/pages/DispatchHistoryPage.tsx`.

Bố cục:
1. `PageHeader` tiêu đề "Lịch sử xuất kho".
2. Thanh lọc (một hàng, responsive):
   - `DateFilter` (component sẵn có, `inline`), mặc định preset **tháng này**.
   - `<Select>` chọn kho (từ `/api/warehouses?limit=200`), có tùy chọn "Tất cả kho".
   - `<Input>` tìm sản phẩm (SKU/tên), debounce ~300ms.
   - `<Input>` tìm mã đơn/mã vận đơn, debounce ~300ms.
3. **Bảng tổng hợp**: cột `SKU | Tên | Đã xuất | Hoàn lại | Còn lại ròng`, dòng cuối là Tổng (từ `totals`).
4. **Bảng chi tiết**: cột `Thời gian | Mã đơn | Mã vận đơn | Sản phẩm (SKU + tên) | Loại | SL | Tồn sau`, kèm nút Trước/Sau dựa trên `total/limit/offset`.
   - "Loại": badge "Xuất" (sale) / "Hoàn" (sale_reversal).
   - Mã đơn có thể link tới trang đơn (theo pattern `StockMovementHistory` hiện có, tùy chọn — không bắt buộc).

### 4.3 Data & types
- `frontend/src/features/inventory/dispatch-history-queries.ts`: `useDispatchSummary(filters)`, `useDispatchDetail(filters)` theo pattern react-query + `fetchJson`.
- Types `DispatchSummaryRow`, `DispatchSummaryResponse`, `DispatchDetailRow`, `DispatchDetailResponse`, `DispatchHistoryFilters` (đặt trong `dispatch-history-queries.ts` hoặc `types.ts`).
- Tách hàm thuần `buildDispatchParams(filters)` (dựng `URLSearchParams`, bỏ field rỗng) để test độc lập.

## 5. Kiểm thử (TDD)

### Backend (test container pg:5433 / redis:6380)
File `apps/inventory/tests/test_dispatch_history.py`:
- summary: gross `dispatched`, `reversed`, `net` đúng khi có cả sale và sale_reversal.
- summary: `totals` cộng đúng, `product_count` đúng.
- lọc `date_from`/`date_to` theo biên (bao gồm/loại trừ đúng ngày).
- lọc `warehouse_id` chỉ lấy đúng kho.
- tìm `product` theo SKU và theo tên (icontains).
- tìm `order` theo `order_code` và theo `shipping_code`.
- KHÔNG lẫn movement type khác (issue/adjustment/return_in bị loại).
- detail: phân trang `limit/offset`, `total` đúng, sắp `-created_at`.
- detail: nhãn `order_code`/`shipping_code` resolve đúng; đơn đã xoá vẫn hiện dòng.

### Frontend (vitest)
- `buildDispatchParams`: bỏ field rỗng, giữ field có giá trị, map đúng tên param.
- (nếu có) hàm tính tổng/định dạng thuần.
- `tsc --noEmit` sạch, eslint 0 warning.

## 6. Rủi ro / lưu ý
- `reference_id` là FK lỏng (BigInteger) → search theo mã đơn phải resolve qua bảng Order rồi lọc `reference_id__in`; cẩn thận N+1 khi hiển thị nhãn (dùng `resolve_reference_labels` gộp sẵn).
- Aggregation trên khoảng ngày rộng: đảm bảo có index `(warehouse, product, created_at)` (đã có) được tận dụng; cân nhắc giới hạn khoảng ngày tối đa nếu cần (chưa bắt buộc).
- Regenerate `backend/schema.yaml` sau khi thêm endpoint; KHÔNG commit lại `frontend/src/api/schema.d.ts` nếu tạo diff lớn do lệch version tool (type dùng là hand-written).
- Không sửa `list_movements` / `StockMovementHistory` để tránh ảnh hưởng ngoài phạm vi.
