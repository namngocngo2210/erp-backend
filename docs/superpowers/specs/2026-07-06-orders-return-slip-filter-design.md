# Bộ lọc "Phiếu trả hàng" cho bảng Orders

**Ngày:** 2026-07-06
**Branch:** `feat/orders-return-slip-filter`
**Trạng thái:** Hoàn thành 2026-07-06 — BE filter + parse ở 4 endpoint (orders/koc/landing/sheet3), FE dropdown dùng chung 3 trang; 3 test BE + test hook FE xanh.

## Bối cảnh & mục tiêu

Cột "Phiếu trả hàng" trên bảng orders hiển thị field `return_slip_code` — text nhập tay
trên `OrderRecall` (bảng `order_recalls`, quan hệ 1-1 với `Order`, block Thu hồi của đơn
KOC, xem spec `2026-06-03-koc-order-recall-design.md`). Hiện chưa có cách lọc đơn theo
việc đã có / chưa có phiếu trả hàng.

Mục tiêu: thêm bộ lọc dạng **Có / Không có** (dropdown 3 trạng thái: Tất cả / Có phiếu
trả / Chưa có phiếu trả) vào panel filter dùng chung, hoạt động trên **cả 3 trang**:
Đơn hàng chính, Đơn hàng Landing (mọi tab, kể cả Sheet 3 Tư vấn), Đơn hàng KOC.

## Ngữ nghĩa filter

Param mới `has_return_slip`, giá trị `"yes"` / `"no"` / rỗng (không lọc):

- `yes` — tồn tại bản ghi `order_recalls` của đơn với `return_slip_code != ""`.
- `no` — phần bù: đơn không có bản ghi recall, HOẶC có recall nhưng `return_slip_code`
  rỗng.

## Phương án đã chọn

Mirror 100% filter `has_internal_note` (cùng bản chất: yes/no trên field text của bảng
phụ 1-1):

- **BE:** Exists semi-/anti-join sang `order_recalls` — planner drive từ tập đơn đã lọc
  rồi probe qua unique index `order_id` (OneToOne), tránh join quét toàn bảng (bài học
  perf seq-scan `order_assessments`).
- Phương án bị loại: join thẳng `recall__return_slip_code` qua ORM (đi ngược pattern
  perf đã chuẩn hóa); lọc client-side (bảng phân trang server-side).

Không cần index mới: probe dùng unique index `order_id` sẵn có; bảng `order_recalls`
nhỏ (chỉ đơn KOC nhập tay).

## Thay đổi Backend

1. `backend/apps/orders/repositories.py` — `_apply_list_filters`: thêm block cạnh
   `has_internal_note`:

   ```python
   has_return_slip = str(filters.get("has_return_slip") or "").strip()
   if has_return_slip in ("yes", "no"):
       slip_exists = Exists(
           OrderRecall.objects.filter(order_id=OuterRef("pk")).exclude(return_slip_code="")
       )
       queryset = queryset.filter(slip_exists if has_return_slip == "yes" else ~slip_exists)
   ```

2. Parse param ở **cả 4 chỗ** build filter dict (filter parity — từng lệch `vat`):
   - `_view_helpers.py::build_orders_filters` — trang Đơn hàng chính + export Excel
     theo bộ lọc (dùng chung nên tự khớp).
   - `order_list_view.py::list_koc_orders`.
   - `order_list_view.py::list_landing_orders`.
   - `sheet3_view.py::list_sheet3_orders` — panel filter dùng chung hiển thị ở tab
     Tư vấn, phải parse để không rơi vào lỗi "UI có mà không lọc".

## Thay đổi Frontend

State mới `hasReturnSlip: string` (`""` / `"yes"` / `"no"`), đi theo đúng chuỗi của
`hasInternalNote` (thêm 1 lần, dùng chung cho cả 3 trang vì `buildBody` +
`OrdersFilterPanel` là shared):

1. `order-storage.ts` — thêm vào `StoredOrderFilters`: default `""`, parse từ
   localStorage, reset.
2. `use-orders-filters.ts` — state + seed từ stored + `reset()`.
3. `use-orders-data.ts`:
   - `StoredOrderFilters`/defaults + overrides type.
   - `buildBody`: `if (hasReturnSlipValue) body.has_return_slip = hasReturnSlipValue;`
   - Client-side keep-row filter (realtime update revalidate): `yes`/`no` theo
     `row.recall?.return_slip_code` (trim, khớp ngữ nghĩa BE).
   - Snapshot/apply saved-filters + các check emptiness "bộ lọc mặc định".
4. `OrdersPage.tsx` — thread props `hasReturnSlip` / `onHasReturnSlipChange` xuống
   panel.
5. `OrdersFilterPanel.tsx` — dropdown "Phiếu trả hàng" đặt cạnh "Ghi chú nội bộ":
   Tất cả / Có phiếu trả / Chưa có phiếu trả.
6. i18n `orders.ts` — key vi/en: `orders.filters.hasReturnSlip*`.

Không cần regen `schema.d.ts`: body list request là `Record<string, unknown>`, các
list endpoint khai báo inline_serializer field optional (giữ nguyên pattern hiện tại).

## Testing

- **BE** (`backend/apps/orders/tests/`): test filter qua endpoint / repository với 3
  case dữ liệu: đơn có recall + `return_slip_code` khác rỗng; đơn có recall nhưng
  `return_slip_code=""`; đơn không có recall. Assert `yes` chỉ trả case 1, `no` trả
  case 2+3, rỗng trả cả 3. Thêm assert param được parse ở koc/landing endpoint.
- **FE**: cập nhật `use-orders-filters.test.tsx` (default seed + reset về `""`).
- Baseline: các test fail sẵn trên master (return-rate BE, OrderFormDialog + 2
  mobile-viewport FE) không tính là regression.

## Ngoài phạm vi

- Tìm kiếm theo mã phiếu trả (text search) — user đã chọn chỉ cần Có/Không.
- Trang Vận đơn (`/api/orders/shipments`) — không nằm trong yêu cầu.
- Index mới cho `return_slip_code` — không cần với ngữ nghĩa yes/no.
