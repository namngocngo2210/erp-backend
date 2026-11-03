# Thiết kế: Tự động xử lý thanh toán/COD theo loại đơn KOC

- **Ngày:** 2026-06-02
- **Phạm vi:** Chỉ đơn thuộc kênh KOC (`order.channel_id == settings.KOC_CHANNEL_ID`). Các kênh khác giữ nguyên hành vi hiện tại.

## 1. Bối cảnh & vấn đề

Trang Đơn hàng KOC tạo đơn gửi hàng mẫu cho KOC/KOL. Có 2 loại đơn cần xử lý thanh toán khác nhau (field `order_type` trên `Order`):

- `no_return` ("Không trả lại"): KOC giữ hàng, không trả → coi như **đã thanh toán đủ**.
- `has_return` ("Có trả lại"): đơn vẫn lên KiotViet nhưng **không thu COD** của khách, để lại như **công nợ** (khoản phải thu chưa thu).

Hiện trạng code:
- `no_return`: **chưa có xử lý** — `customer_prepaid` theo input (mặc định 0), nên khi đẩy KiotViet vẫn bị tính COD.
- `has_return`: nhánh tại [invoice_mutations_push.py:557](../../../backend/apps/kiotviet/infrastructure/services/invoice_mutations_push.py) đang set `totalPayment = full` (đánh **đã trả đủ**) cho **mọi kênh**, và **chưa ép `isCod=false`** → thực tế **vẫn đang thu COD**. Đây là điểm sai cần sửa.

Cơ chế COD khi đẩy KiotViet (đường `POST /orders`): `isCod = customer_prepaid < total_after_discount`, `codAmount = max(0, total - prepaid)`. Đường `POST /invoices` không gửi COD.

## 2. Hành vi mục tiêu

### ① Đơn KHÔNG TRẢ (`no_return`) → thanh toán luôn
Khi **tạo đơn** KOC với `order_type = no_return`: tự set `customer_prepaid = total_after_discount`.

Hệ quả tự nhiên khi đẩy KiotViet (không cần đụng push):
- `isCod = prepaid < total = false` → **không thu COD**.
- `totalPayment = full` (qua nhánh `customer_prepaid > 0`) → đánh **đã trả đủ**.

### ② Đơn CÓ TRẢ (`has_return`) → để công nợ, không thu COD
Khi **đẩy KiotViet** đơn KOC `has_return`:
- `order_delivery`: ép `isCod=false`, `codAmount=0`, `usingCod=false`, `usingPriceCod=false`, `receiveMoney=0` → **không thu COD**.
- **Không** đánh đã trả đủ: đặt `totalPayment=0` cho nhánh `has_return` (hiện đang `=full`) → để **công nợ** (full amount còn nợ trên KiotViet).

**Diễn giải "âm công nợ" (đã chốt):** đơn vẫn lên, full amount để lại như khoản phải thu chưa thu (`totalPayment=0` + `isCod=false`). Không tạo return/credit invoice âm.

### ③ Đơn KOC → `order_source = "koc"`
Mọi đơn tạo ở kênh KOC ghi `order_source = "koc"`, **ép cứng** kể cả khi client gửi `order_source` khác.
- `OrderSource` enum chưa có `koc` → thêm `KOC = "koc", "KOC"` và tạo migration.

### ④ Khách MỚI ở luồng KOC → mã prefix "KOC"
Khách hàng **tạo mới** trong luồng tạo đơn KOC nhận mã `KOC<id>` thay vì `KH<id>`.
- Chỉ khách mới; khách đã tồn tại giữ nguyên mã cũ (vd `KH123`).
- `Customer.save()` hiện hardcode `KH<pk>` → cho phép prefix cấu hình được.

## 3. Các điểm sửa

### Backend — tạo đơn
- File: `backend/apps/orders/application/services/order_service_commands.py`, hàm `create_order_manual`.
- Sau khi tính `payload_total_after_discount` (≈ dòng 445-448), trước khi `Order.objects.create(...)`:
  - Nếu `channel.id == settings.KOC_CHANNEL_ID` **và** `payload.get("order_type") == "no_return"` → đặt `customer_prepaid = payload_total_after_discount`, ghi đè giá trị từ payload.
- Gói điều kiện này thành một helper nhỏ thuần (vd `_koc_prepaid_override(channel, order_type, total_after_discount, customer_prepaid)`) để dễ test độc lập.

### Backend — đẩy KiotViet
- File: `backend/apps/kiotviet/infrastructure/services/invoice_mutations_push.py`, hàm `push_order_to_kiotviet` (đường `order_data` / `POST /orders`).
- Xác định `is_koc = (getattr(order, "channel_id", None) == settings.KOC_CHANNEL_ID)` và `is_has_return = str(getattr(order, "order_type", "")) == "has_return"`.
- Tại block `order_delivery` (≈ dòng 462-476): nếu `is_koc and is_has_return` → ép `is_cod=False`, `cod_amount=0` (kéo theo `usingCod/usingPriceCod=false`, `receiveMoney=0`).
- Tại nhánh `has_return` (≈ dòng 557-559): chỉ áp dụng cho KOC; thay vì `totalPayment = full`, đặt `totalPayment = 0` (để công nợ). `has_return` ngoài kênh KOC: **giữ nguyên** hành vi cũ (totalPayment=full) để không thay đổi ngoài phạm vi.

### Backend — order_source & mã khách (③ ④)
- `backend/apps/orders/models.py`: thêm `KOC = "koc", "KOC"` vào `OrderSource`; chạy `makemigrations orders`.
- `backend/apps/orders/application/services/order_service_commands.py` (`create_order_manual`): tính `is_koc = channel.id == settings.KOC_CHANNEL_ID` một lần (dùng chung cho ①/③/④); nếu `is_koc` → `order_source = "koc"` (ưu tiên trước payload).
- `backend/apps/customers/models.py` (`Customer.save`): đọc prefix per-instance `getattr(self, "_code_prefix", "KH")`, final code = `{prefix}{pk}`.
- `backend/apps/customers/services.py` (`get_or_create_by_phone`): thêm tham số `code_prefix="KH"`; tạo khách mới bằng cách set `_code_prefix` trước khi `save()`.
- `create_order_manual`: gọi `get_or_create_by_phone(..., code_prefix="KOC" if is_koc else "KH")`.
- Frontend `order-source.ts` / `order-types.ts`: thêm `"koc"` vào type `OrderSource`, `ORDER_SOURCE_LABELS` ("KOC"), và map badge class — để badge hiển thị đúng. **Không** thêm vào `ORDER_SOURCES` (không cho chọn tay trong dropdown).

### Frontend (trong scope, UX)
- File: `frontend/src/features/orders/OrderFormDialog.tsx`.
- Khi đang ở chế độ KOC (`kocMode`) và user chọn `order_type = no_return` ở ô loại đơn ([dòng 1014-1025]): tự set `customer_prepaid = total_after_discount` (tương đương tick "Đã thanh toán") để user thấy ngay. Backend vẫn là nguồn chân lý; đây là phản hồi UI cho khớp.
- Cần xác định cách form biết đang ở chế độ KOC (prop/flag truyền vào dialog, hoặc `kocModeRef` từ `use-orders-data`). Plan sẽ xác minh nguồn KOC-mode chính xác trước khi code.
- Cân nhắc: khi user đổi từ `no_return` sang loại khác, không tự xóa prepaid đã nhập tay (tránh phá dữ liệu user); chỉ auto-set khi chọn `no_return`.

## 4. Edge cases
- `total_after_discount` âm/None: helper trả về giá trị an toàn (không set prepaid nếu total không hợp lệ).
- Đơn `no_return` chỉnh sửa sau (đổi type qua edit): phạm vi v1 chỉ xử lý lúc **tạo**. Push đọc `order_type` hiện tại nên phần COD/công nợ của `has_return` vẫn đúng kể cả khi đổi qua edit.
- Non-KOC `has_return`: không đổi.

## 5. Kiểm thử
- Unit test create: đơn KOC `no_return` → `customer_prepaid == total_after_discount`; đơn KOC `has_return` → `customer_prepaid` giữ nguyên input; đơn KOC `normal` và đơn kênh khác → không bị override.
- Unit test push payload (KOC `has_return`): `order_delivery.isCod == False`, `codAmount == 0`, `order_data.totalPayment == 0`.
- Unit test push payload (KOC `no_return` với prepaid=full): `isCod == False`, `totalPayment == full`.
- Regression: non-KOC `has_return` giữ `totalPayment=full` như cũ.
- Frontend test: ở chế độ KOC, chọn `no_return` → ô `customer_prepaid` tự điền `= total_after_discount`; đổi sang loại khác không xóa giá trị user nhập tay.
- Create KOC → `order.order_source == "koc"`; đơn kênh khác không bị ép koc.
- Create KOC với phone mới → `customer.code` bắt đầu bằng `KOC`; create kênh khác → `KH`; khách đã tồn tại đặt đơn KOC → giữ mã cũ.
