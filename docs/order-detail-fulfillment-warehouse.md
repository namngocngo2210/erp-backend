# Chọn kho xuất trên trang chi tiết đơn hàng

Ngày: 2026-08-13 · nhánh `feat/order-detail-fulfillment-warehouse`

## Vấn đề

Kho xuất (`Order.fulfillment_warehouse`) chỉ chọn được ở form **tạo đơn**
(`OrderFormDialog`). Đơn tạo thiếu kho — đơn import, đơn từ landing, đơn tạo trước khi
form có ô kho — không có chỗ nào bổ sung: nhân viên phải chờ tới bước quét kho, còn đơn
bán trực tiếp (không quét) thì kẹt hẳn ở thông báo *"Hãy chọn kho xuất trước khi chuyển
sang Đã xuất kho"*.

Backend đã sẵn sàng từ trước: `OrderSerializer` trả `fulfillment_warehouse` +
`fulfillment_warehouse_name`, `OrderInlineUpdateSerializer` đã nhận field này trong PATCH.
Chỉ thiếu phần giao diện.

## Giải pháp

Thêm ô **Kho xuất** vào mục *Vận chuyển* của khối "Thông tin đơn hàng"
(`frontend/src/pages/OrderDetailPage.tsx`), dùng đúng `ERow type="select"` như các ô còn
lại của trang: sửa vào hàng chờ, bấm **Lưu** mới gọi PATCH.

Danh sách kho lấy qua `useWarehousesList` — chung cache React Query với trang Tồn kho, không
thêm endpoint mới. Lỗi tải danh sách được bỏ qua: select rỗng, các ô khác vẫn lưu bình
thường.

Khi chọn, FE patch kèm `fulfillment_warehouse_name` để ô hiện ngay tên kho mới (BE coi
field này là read-only và bỏ qua khi ghi) — cùng cách cặp `staff_id`/`staff_name` đang làm.

## Khóa khi đơn đã xuất kho

Tồn chỉ bị trừ khi trạng thái giao vận chuyển sang `dispatched`, và chỉ được hoàn khi mã
quay về `""` / `warehouse_processing` (un-dispatch). Vì vậy **đổi kho sau khi đã trừ tồn
làm lệch sổ cả hai kho**: kho cũ giữ nguyên phần đã trừ, kho mới không trừ gì.

Quy ước: đơn coi như "đã trừ tồn" khi mã giao vận KHÔNG thuộc
`PRE_DISPATCH_DELIVERY_CODES = {"", "warehouse_processing"}` (`backend/apps/orders/crm_catalog.py`).
Các mã còn lại — `dispatched`, `received`, `not_received`, `stalled_complaint`,
`post_office_lost` — đều nằm sau bước xuất kho.

Hai lớp:

1. **UI** — `frontend/src/features/orders/fulfillment-warehouse-lock.ts`: ô kho chuyển
   sang chỉ đọc kèm tooltip giải thích.
2. **Service** — guard trong `OrderServiceCommands.update_order_inline`: PATCH đổi kho
   trên đơn đã xuất → `ValidationError`. Đây mới là chốt chặn thật, áp cho mọi đường PATCH
   chứ không riêng trang chi tiết.

Gửi lại **đúng** kho hiện tại không bị chặn (form PATCH nhiều field một lượt thường kèm
theo kho cũ). Muốn đổi kho thật thì bỏ trạng thái "Đã xuất kho" trước — nhánh un-dispatch
sẽ hoàn tồn, rồi chọn kho mới.

Tác động lên luồng quét kho: quét lại một đơn đã xuất ở **kho khác** giờ trả lỗi cho riêng
đơn đó trong lô (`dispatch_orders_batch` đã bắt lỗi per-order) thay vì âm thầm ghi đè kho.
Quét lại ở cùng kho không đổi gì so với trước.

## Test

- `frontend/src/features/orders/__tests__/OrderDetailPage.fulfillment-warehouse.test.tsx`
  — hiện kho hiện tại, chọn kho khác rồi Lưu, bỏ chọn kho, đơn đã xuất thì chỉ đọc.
- `backend/apps/orders/tests/test_dispatch_stock.py` (`DispatchStockTests`) — chặn đổi kho
  sau dispatch, cho gửi lại kho cũ, cho đổi kho khi chưa xuất.
