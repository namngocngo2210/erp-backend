# Khóa chỉnh sửa Sản phẩm + Tài chính khi đơn đã có mã hóa đơn

Ngày: 2026-08-11 · MR !1208 (bản 1) → `feat/shipping-lock-products-only` (bản 2)

## Vấn đề

Đơn đã có mã hóa đơn (`Order.invoice_code`) là đơn đã được đẩy lên KiotViet và chốt hóa
đơn ở đó. Từ thời điểm này, dòng hàng và số tiền đã nằm trong sổ của KiotViet; sửa ở hệ
thống mình làm hai bên lệch nhau — trừ tồn sai, đối chiếu hoàn sai, lệch doanh thu.

Cần: **đơn đã có mã hóa đơn thì không sửa sản phẩm và tài chính được nữa.**

Mã hóa đơn là mốc **sớm hơn** mã vận đơn: `create_kship_delivery` bắt buộc đơn phải có
`invoice_code` trước khi tạo vận đơn, nên khóa theo hóa đơn bao trùm luôn giai đoạn đã có
vận đơn.

## Phạm vi

**Bản 2 (thu hẹp từ bản 1 sau phản hồi vận hành).** Bản 1 khóa theo *mã vận đơn* và khóa
cả nội dung đơn (khách, địa chỉ, kênh/sale, định danh). Thực tế đó lại là những thứ
**thường xuyên phải vá sau khi đơn đã lên đường** — sai SĐT/địa chỉ khi hãng gọi giao, dán
sai mã vận đơn, chuyển sale phụ trách. Khóa chúng gây tắc nhiều hơn là bảo vệ.

Ràng buộc kèm theo: `PATCH /api/orders/{id}` là endpoint DÙNG CHUNG cho form đơn, sửa
inline bảng đơn, và cập nhật "Trạng thái vận chuyển thực" ở **trang Vận đơn** + luồng
quét kho (`frontend/src/features/inventory/shipments/use-shipments.ts`). Đơn có hóa đơn
CHÍNH LÀ đơn kho/telesale phải cập nhật hằng ngày, nên phạm vi khóa phải hẹp.

### Nhóm KHÓA — đúng hai khối trên trang chi tiết đơn

| Khối UI | Field |
| --- | --- |
| Sản phẩm & thông số hàng hóa | `items[]` (sản phẩm/SL/đơn giá), `sku`, `quantity`, `weight`, `dimensions` |
| Tài chính | `total_amount`, `discount_amount`, `booking_voucher_discount`, `other_price`, `customer_prepaid`, `total_after_discount` |

### Nhóm MỞ — mọi thứ còn lại

- Khách: `name`, `phone`, `customer_gender`.
- Địa chỉ: `raw_address`, `address`, `ward/district/province` (+ `*_code`),
  `kiotviet_location`, `kiotviet_ward`.
- Vận chuyển: `shipping_fee`, `payment_method` (nằm ngoài khối Tài chính).
- Quy thuộc: `channel_code`, `kiotviet_channel_id`, `staff_id`, `branch_id`,
  `order_type`, `order_source`, `shipping_form`, `created_at`, `fulfillment_warehouse`.
- Định danh: `order_code`, `invoice_code`, `invoice_id`, `shipping_code` — nhờ vậy ca
  "dán sai mã" tự sửa được, không phải hủy đơn tạo lại như bản 1.
- Vận hành: `status`, `crm.*`, `recall.*`, `vat`, `is_export_vat`, `vat_link`, `result`,
  `feedback`, `customer_return`, `return_date`.

`invoice_code` vừa là **điều kiện kích hoạt** khóa vừa nằm trong nhóm mở, nên xóa nó là
mở khóa được. Đây là đánh đổi có ý thức: mã hóa đơn là mối nối sang KiotViet, xóa nó gây
hậu quả rõ ràng và hiện trên lịch sử thao tác, nên không đáng chặn cứng. Nếu sau này thấy
bị lạm dụng thì chỉ cần thêm `invoice_code` vào `INVOICE_LOCKED_FIELD_LABELS`.

## Thiết kế

### 1. Nguồn chân lý: `apps/orders/invoice_lock.py`

Module thuần (không import DRF, không I/O), 3 thứ:

- `INVOICE_LOCKED_FIELDS: frozenset[str]` — tên field theo `OrderInlineUpdateSerializer`.
- `is_invoice_locked(order) -> bool` — `bool(str(order.invoice_code or "").strip())`.
- `locked_field_changes(order, validated_data) -> list[str]` — trả về các field
  **thực sự đổi giá trị**, kèm `describe_locked_fields()` để dựng câu tiếng Việt.

**So sánh giá trị, không chặn theo sự có mặt trong payload.** Form đơn gửi lại full
object; chặn theo "field xuất hiện trong payload" sẽ 403 oan cho mọi cập nhật khác đi
kèm. Đây đúng khuôn guard `staff_id` đang có trong `order_action_view.py`.

Chuẩn hóa khi so sánh:

- Tiền + `weight` → `Decimal` (tránh `"100"` != `100`, `"100.00"` != `Decimal("100")`).
- `quantity` → `int`; giá trị không parse nổi rơi vào sentinel khác MỌI giá trị hợp lệ
  (fail-closed → chặn), không được rơi về `None` vì `None` trùng với đơn để trống.
- `sku`, `dimensions` → `strip().lower()`, `None` ≡ `""`.
- `items` → khớp dòng theo `product_id` (thiếu thì `sku`), so `quantity` và `unit_price`.
  `unit_price` không gửi = "để hệ thống tính theo bảng giá" → không tính là đổi giá.

### 2. Chốt chặn: tầng transport, một chỗ duy nhất

`update_order_inline` (`order_action_view.py`), sau `serializer.is_valid()` để so sánh
trên `validated_data` đã coerce type, trước khi gọi service:

```
403 {"detail": "Đơn đã có mã hóa đơn nên không sửa được: <nhãn field>.", "locked_fields": [...]}
```

Guard đặt ở **view** ⇒ chỉ chặn hành vi NGƯỜI DÙNG. Các luồng hệ thống đi qua service
nên không bị ảnh hưởng — **chủ ý**, không phải kẽ hở:

- Job đẩy KiotViet ghi `invoice_code` (chính nó tạo ra khóa), job KShip ghi
  `shipping_code`.
- Webhook/đồng bộ KiotViet, ingest landing.
- `dispatch_orders_batch` (quét kho xuất hàng) — chỉ ghi trạng thái CRM.
- Restore / clone đơn hủy.

### 3. Cờ cho frontend

`OrderSerializer` thêm `is_invoice_locked` (`SerializerMethodField`, dùng chung hàm
domain).

KHÔNG hạ `can_edit` xuống `False`: `can_edit` đang gate cả ô CRM lẫn cột "Trạng thái
vận chuyển thực" trên `OrdersTable`, tắt nó là chết trang Vận đơn.

### 4. Frontend

- `features/orders/invoice-lock.ts`: `INVOICE_LOCK_HINT`,
  `INVOICE_LOCKED_PATCH_FIELDS` (mirror BE), `isInvoiceLocked(row)`,
  `pickInvoiceLockedFields(payload)`.
- `pages/OrderDetailPage.tsx`: banner cảnh báo; `patch()` lọc field khóa + toast;
  `BlockProducts` + `BlockFinancials` bọc trong `fieldset disabled` và nhận
  `contentLocked`.
- **KHÔNG gate `OrdersTable/OrderCell.tsx`**: bảng đơn chỉ HIỂN THỊ các field này, không
  có ô inline nào cho chúng (`weight`/`dimensions` còn không có cột). Thêm gate ở đó chỉ
  tạo code chết. Hai block trên là surface DUY NHẤT sửa được nhóm này.
- `order-detail-shared.tsx`: prop `disabled` cho `ERow`/`EditableCell`. Ô click-to-edit là
  `<span role="button">` nên **không** bị `fieldset disabled` chặn — không có prop này thì
  ô khóa vẫn mở được input rồi âm thầm mất dữ liệu.

## Test

Backend — `apps/orders/tests/test_order_inline_update_lock.py` (một module cho cả luật):

- Mỗi field của hai khối khóa đổi giá trị → 403 kèm `locked_fields`.
- `items`: đổi số lượng / đơn giá / sản phẩm / thêm dòng → 403; gửi lại y nguyên → 200.
- Payload thật của UI (items + tổng tiền cùng lúc) → 403 liệt kê đủ ba field.
- Gửi lại ĐÚNG giá trị cũ cho cả nhóm khóa (full object) → không bị chặn.
- Mọi field NGOÀI hai khối → 200 (khách, địa chỉ, phí vận chuyển, kênh/sale/kho, mã
  đơn/hóa đơn/vận đơn, `status`, `crm`, `recall`, VAT, `feedback`).
- Đơn chưa có mã hóa đơn (kể cả chỉ có mã vận đơn, hoặc mã toàn khoảng trắng) → không đổi.
- Luồng hệ thống (`dispatch_orders_batch`, service ghi trực tiếp) → không bị chặn.

Frontend:

- `features/orders/__tests__/invoice-lock.test.ts` — helper thuần.
- `features/orders/__tests__/OrderDetailPage.invoice-lock.test.tsx` — banner hiện, khối
  Sản phẩm + Tài chính chỉ đọc, SĐT/VAT vẫn sửa được.

Không có migration, không thêm biến môi trường.

## Tiến độ

- [x] Sprint 1 — Backend: module domain + guard + cờ serializer + test.
- [x] Sprint 2 — Frontend: helper + trang chi tiết + test.
- [x] Sprint 3 — Chạy test, cập nhật `schema.yaml` cho field mới, MR !1208 (bản 1).
- [x] Sprint 4 — Bản 2: đổi điều kiện sang mã hóa đơn, thu phạm vi về hai khối Sản phẩm
      + Tài chính, đổi tên `shipping_lock` → `invoice_lock`.

### Ghi chú sau khi làm

- `schema.yaml` + `schema.d.ts` sửa tay đúng phần `is_invoice_locked` (kiểm chứng bằng
  cách regen ra file tạm rồi `diff`) để không kéo nhiễu ~160 dòng nợ cũ của repo.
- Bản 2 đã mở lại so với bản 1: khách, địa chỉ, kênh/kênh KiotViet/sale/chi nhánh/kho,
  ngày tạo, phí vận chuyển, mã đơn/hóa đơn/mã vận đơn.
- Chưa làm / cố ý bỏ:
  - Không thêm quyền admin để mở khóa (không có đường lách chính thức).
  - Không chặn `invoice_code` — xem lý giải ở mục Phạm vi.
  - Không siết `import Excel` vì luồng đó chỉ TẠO đơn mới, không cập nhật đơn cũ.
  - Không đổi hành vi read-only của đơn đã hủy (ngoài phạm vi), dù prop `disabled` của
    `ERow` có thể dùng lại để vá điểm yếu "click vào ô vẫn mở input" của luồng đó.
