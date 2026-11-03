# Thiết kế: Trang "Đơn hàng gửi ngoài" + hình thức gửi + quét mã thu hồi

- Ngày: 2026-07-06
- Trạng thái: Đã triển khai (chờ QA)
- Branch: `feat/outside-orders-page`

## Bối cảnh & mục tiêu

Sale cần tạo đơn ở hệ thống bên ngoài rồi nhập thông tin vào CRM để theo dõi.
Yêu cầu gồm 4 phần:

1. Bỏ block "Lịch sử mua hàng theo SĐT" ở form **tạo** đơn (giữ khi sửa).
2. Trang mới **Đơn hàng gửi ngoài** (block Sale) — tạo tay đơn gửi ngoài, có
   phân quyền riêng.
3. Bảng `orders` thêm field **hình thức gửi** (`shipping_form`) và field
   **`outside`** (bool); field **loại đơn hàng** (`order_type`) thêm 4 giá trị.
4. Quét tạo phiếu trả hàng tìm thêm theo **mã thu hồi** (`order_recalls.recall_code`);
   thêm bộ lọc hình thức gửi + loại đơn hàng vào thanh filter các trang đơn hiện có.

## Quyết định đã chốt (Q&A với anh Nam)

| Câu hỏi | Quyết định |
|---|---|
| Cách nhập đơn ở trang mới | Chỉ form tạo tay (không import hàng loạt) |
| Đánh dấu đơn gửi ngoài | Field `outside` (bool) trong DB, default `false`; trang mới chỉ **lọc** `outside=true`; đơn tạo từ trang này set `true` |
| Phạm vi field hình thức gửi | Chỉ hiện/nhập ở trang Đơn gửi ngoài; các trang khác không đổi form |
| Loại đơn hàng | Thêm 4 giá trị vào enum chung, giữ 3 giá trị cũ |
| Mã thu hồi | Nhập ngay trên form tạo đơn gửi ngoài (không bắt buộc), lưu `order_recalls.recall_code` |
| Vị trí bộ lọc mới | Thêm vào thanh filter hiện có trên các trang đơn |
| Form tạo đơn | Tái dùng `OrderFormDialog` đầy đủ + 3 field mới |
| Bỏ block lịch sử | Chỉ ở chế độ TẠO đơn, chế độ SỬA giữ nguyên |
| Phân quyền | Section RBAC riêng `outside-orders`, gán theo vai trò |

## 1. Database (app `orders`)

### 1.1 Field mới trên `Order`

- `outside = models.BooleanField(default=False, db_default=False)` — đơn tạo từ
  trang Đơn gửi ngoài. Mọi luồng khác (landing/Lark/Excel/KOC/tạo tay thường)
  giữ `false`.
- `shipping_form = models.CharField(max_length=30, choices=ShippingForm.choices, blank=True, default="")`

```python
class ShippingForm(models.TextChoices):
    ONE_WAY = "one_way", "Đơn 1 chiều"
    TWO_WAY = "two_way", "Đơn 2 chiều"
    WARRANTY_RECALL = "warranty_recall", "Đơn thu hồi bảo hành"
    CUSTOMER_RETURN = "customer_return", "Đơn trả hàng"
    RECALL = "recall", "Đơn thu hồi"
```

(`customer_return` thay vì `return` — tránh trùng keyword Python.)

### 1.2 Mở rộng `OrderType` (giữ 3 giá trị cũ)

```python
SAME_PRODUCT = "same_product", "Cùng sản phẩm"
DIFFERENT_PRODUCT = "different_product", "Khác sản phẩm"
PURCHASE = "purchase", "Mua hàng"
WARRANTY_RETURN = "warranty_return", "Trả bảo hành"
```

### 1.3 Index

Partial index phục vụ trang mới (tập outside nhỏ so với toàn bảng):

```python
models.Index(
    fields=["-created_at"],
    condition=Q(outside=True, deleted_at__isnull=True),
    name="ord_outside_cr_idx",
)
```

Bộ lọc `shipping_form` dùng chung index hiện có theo created_at (tập kết quả
nhỏ, không cần index riêng ở giai đoạn này — đo lại sau khi có dữ liệu).

### 1.4 Mã thu hồi

Tái dùng bảng `order_recalls` (OneToOne với Order, đã có index `recall_code`).
Cập nhật docstring model: không còn là "chỉ đơn KOC".

Một migration duy nhất cho toàn bộ thay đổi schema.

## 2. Backend API

### 2.1 Tạo / sửa đơn

- Serializer create (POST `/api/orders`) nhận thêm `outside` (bool),
  `shipping_form`, `recall_code` (string, optional → ghi `OrderRecall`).
- PATCH đơn cho phép sửa `shipping_form`, `order_type` (7 giá trị),
  `recall_code` từ trang mới.
- Các luồng ingest cũ không truyền field mới → hành vi không đổi.

### 2.2 List đơn — bộ lọc mới

Thêm vào filter builder dùng chung của các endpoint list đơn:

- `outside` (true/false) — trang mới khóa `outside=true`.
- `shipping_form[]` (multi).
- `order_type[]` (multi, đủ 7 giá trị).

**Bắt buộc parity**: áp cho mọi endpoint list dùng chung thanh filter
(orders / landing / koc / outside) — tránh lặp lại lỗi lệch filter giữa các view
(bài học `orders-list-endpoints-filter-parity`).

Response list/serializer bổ sung `outside`, `shipping_form` (recall_code đã có
sẵn cho hàng KOC — mở rộng trả về cho đơn outside).

### 2.3 Quét trả hàng — lookup theo mã thu hồi

`GET /api/orders/lookup-by-shipping-code` mở rộng match:

```python
Order.objects.filter(
    Q(shipping_code=code) | Q(recall__recall_code=code),
    deleted_at__isnull=True,
)
```

Phần còn lại của luồng quét giữ nguyên (khớp nhiều đơn → chọn; mã lạ →
phiếu nháp unmatched).

## 3. RBAC

- Section mới: `outside-orders` ("Đơn hàng gửi ngoài", block Sale).
- Backend: thêm vào tuple `ORDER_SECTIONS` (endpoint dùng chung giữa các trang
  đơn chấp nhận section mới); endpoint riêng của trang (nếu có) gate bằng
  `require_section("outside-orders")`.
- Scope OWN/ALL hoạt động như các trang đơn khác; list helper phải truyền đúng
  section của chính trang (gotcha `rbac-scope-by-section-gotcha`).
- FE + seed: thêm section vào catalog phân quyền để gán theo vai trò; superadmin
  mặc định có. KHÔNG tự gán cho vai trò hiện có — admin gán tay.

## 4. Frontend

### 4.1 Trang mới

- Route `/sales/outside-orders`, section `outside-orders`, sidebar block Sale:
  "Đơn hàng gửi ngoài". Chỉ hiện khi có quyền.
- `OrdersPage` thêm chế độ thứ 4 `isOutside` theo đúng pattern Landing/KOC:
  - Khóa filter `outside=true` khi load list.
  - Bảng thêm 3 cột: Hình thức gửi, Loại đơn hàng, Mã thu hồi.
  - Nút tạo đơn mở `OrderFormDialog` với mode outside.

### 4.2 OrderFormDialog

- Mode outside: thêm 3 field — Hình thức gửi (select 5 giá trị), Loại đơn hàng
  (select 7 giá trị), Mã thu hồi (input, optional); submit kèm `outside=true`.
- Chế độ TẠO (mọi trang): bỏ block "Lịch sử mua hàng theo SĐT" + không fetch
  lịch sử; chế độ SỬA giữ nguyên block.

### 4.3 Bộ lọc trên các trang đơn hiện có

- Thêm 2 dropdown filter: Hình thức gửi (5 giá trị), Loại đơn hàng (7 giá trị)
  vào thanh filter Orders / Landing / KOC / Outside.
- i18n: bổ sung message vi/en cho label + options mới.

## 5. Kiểm thử

Backend:
- Tạo đơn `outside=true` kèm `shipping_form` + `recall_code` → ghi đúng Order + OrderRecall.
- Luồng tạo cũ không truyền field mới → `outside=false`, `shipping_form=""`.
- Lookup: quét trúng `recall_code` → trả đơn; trùng cả shipping_code lẫn recall_code ở 2 đơn → nhánh `multiple`.
- Filter `shipping_form[]` / `order_type[]` / `outside` trên các endpoint list (parity test).
- RBAC: user không có section `outside-orders` bị chặn; scope OWN chỉ thấy đơn mình.

Frontend:
- OrderFormDialog: block lịch sử ẩn khi tạo, hiện khi sửa; mode outside render 3 field mới.
- Options loại đơn 7 giá trị; filter mới render và truyền đúng query param.

## 6. Ngoài phạm vi

- Import hàng loạt cho đơn gửi ngoài (đã chốt không làm).
- Hiện field hình thức gửi trên form các trang đơn khác.
- Đồng bộ đơn outside sang KiotViet (không đổi hành vi hiện tại).
- Index riêng cho `shipping_form` (đo sau khi có dữ liệu thật).
