# Che SĐT khách của đơn không phải của mình

**Chốt 2026-09-22.** Sale được cấp scope rộng (thấy toàn bộ đơn công ty) nhưng SĐT
khách trên đơn KHÔNG do mình phụ trách chỉ hiện **3 số đầu + 3 số cuối**:
`0912345678` → `091****678`. Tìm kiếm không đổi: gõ số đầy đủ vẫn ra đúng đơn, chỉ
phần **hiển thị** bị che.

## Ai thấy số thật

Thoả **bất kỳ** điều kiện nào:

1. Có quyền `customer-phone:view` ("Xem đầy đủ SĐT khách" trên trang Vai trò &
   Phân quyền, nhóm Quản trị). Administrator (role `is_system`) luôn có.
2. Đơn do chính user phụ trách (`Order.staff_id == user.id`). Với các màn hình cấp
   KHÁCH HÀNG (danh bạ, KH thân thiết, resale, hồ sơ 360, danh sách khách): khách có
   ít nhất một đơn do user phụ trách.

Quyền là một **section RBAC không có trang riêng**, cùng khuôn `sale-assignment` —
một chính sách công ty nằm ở một chỗ thay vì rải thành field rule trên 5 section đơn.

## Bật thật cho role Sale

Migration `users/0050_seed_customer_phone_permission` **grandfather**: mọi role đang
tồn tại được cấp `customer-phone:view` nên hành vi ngày deploy KHÔNG đổi (kho, CSKH,
quản lý không bị gián đoạn). Siết thật = vào **Cài đặt → Vai trò & Phân quyền → role
Sale → bỏ tick "Xem đầy đủ SĐT khách" → Lưu**. Có hiệu lực ngay (resolver bump cache).

## Không che (miễn trừ)

Đơn **sàn TMĐT** (`order_source` ∈ Shopee/TikTok) KHÔNG bị che: sàn đã trả SĐT che sẵn
(TikTok Shop: `(+84)037*****54`), che thêm một lớp nữa chỉ ăn mất mấy chữ số còn lại mà
không giấu thêm gì — và số của sàn không phải tệp khách do sale gây dựng nên không thuộc
diện "tài sản cạnh tranh giữa sale". Cờ đi qua `PhoneVisibility.apply(..., public_source=)`;
điều kiện nhận dạng ở `is_public_phone_source`.

Ngoài ra `mask_phone_middle` trả NGUYÊN chuỗi đã chứa `*` — chặn che chồng ở những màn
cấp khách hàng (không biết `order_source` của từng dòng) khi SĐT lưu trong `customers`
vốn đã là bản che của sàn.

Hằng `MARKETPLACE_ORDER_SOURCE_CODES` nằm ở `apps/common/business_constants.py` (tầng đáy)
vì consumer WebSocket của `channels_core` cũng cần; test
`test_marketplace_source_codes_match_orders_catalog` khoá nó khớp với
`apps.orders.models.MARKETPLACE_ORDER_SOURCES`.

## Nơi đã áp

| Luồng | Chỗ áp |
| --- | --- |
| 4 trang danh sách đơn + sheet3 + chi tiết đơn + lịch sử đơn theo SĐT + nhân bản/action | `OrderSerializer.to_representation` (`phone`, `customer_phone`) |
| Tìm kiếm đơn (`GET /api/orders/search`) | `order_search_view` |
| Xuất Excel đơn hàng | `order_row_values` + `OrdersExport.write` |
| Realtime WebSocket (row đính kèm `order.created/updated`) | `CRMConsumer.crm_event` che per-connection (publisher giữ số thật vì row serialize chung cho mọi client) |
| Danh sách contact (màn hình + file Excel) | `ContactListService._build_items` |
| KH thân thiết · Theo dõi resale · Hồ sơ khách 360 · Danh sách khách hàng | `CustomerPhoneMasker` (`apps/customers/services.py`) |
| Tra mã vận đơn (màn quét kho) | `shipping_lookup_view._serialize` |
| In hoá đơn HTML (`GET /api/orders/<id>/invoice`, `POST /api/orders/bulk-invoices`) | `invoice._invoice_phone_display` — view truyền `phone_visibility_for(request.user)` vào `invoice_response`/`bulk_invoice_response` (audit 2026-09-23 M3; thiếu visibility = che). Phiếu giao hàng vốn luôn che (`_mask_phone`) cho shipper, không đổi. |
| Action hàng loạt KiotViet (danh sách đơn bị bỏ qua) | `_skipped_orders_payload` |

## Hai quy ước bắt buộc khi thêm chỗ mới

1. **Fail-closed.** `OrderSerializer`, `CustomerSerializer`, `order_row_values`,
   `_resale_row`, `shipping_lookup._serialize`, `invoice.build_invoice_html_from_kiotviet`
   đều che HẾT khi thiếu context/tham số visibility. Quên truyền ở một call site mới phải lộ thành "admin thấy số bị che"
   (báo ngay), không được im lặng rò dữ liệu. Ngoại lệ DUY NHẤT là row realtime — khai
   báo tường minh `_UNMASKED_PHONE` kèm lý do trong `order_event_publisher.py`.
2. **Luồng GHI không nhận bản che.** `OrderInlineUpdateSerializer.to_internal_value`
   bỏ key `phone` nếu giá trị chứa `*` (`strip_masked_phone`): FE nạp form từ chính
   payload đã che nên mọi lần lưu đều gửi lại chuỗi đó; không bỏ thì 400 giữa luồng
   sửa đơn, hoặc tệ hơn là ghi đè hỏng số gốc.

## Mã nguồn

- `backend/apps/common/masking.py` — `mask_phone_middle`, `is_masked_phone`, hằng
  `CUSTOMER_PHONE_PERMISSION` (đặt ở tầng đáy vì consumer WebSocket của
  `channels_core` không được import `apps.users` ngoài 3 module đã miễn trừ).
- `backend/apps/users/phone_visibility.py` — `PhoneVisibility`, `phone_visibility_for`,
  `strip_masked_phone`.
- `backend/apps/customers/services.py` — `CustomerPhoneMasker` cho màn cấp khách hàng
  (1 query/trang qua `OrderRepository.customer_ids_assigned_to`).
- Test: `backend/apps/orders/tests/test_customer_phone_masking.py`.

## Ngoài phạm vi (cố ý)

- **Bảo hành** (`apps/warranty`): phiếu bảo hành hiển thị SĐT người nhận để gọi khách
  mang máy; không record-scope theo sale ngay từ đầu.
- **Chất lượng cuộc gọi**, **Pancake leads**, **Kho số thả nổi**: đã có luật che/quyền
  riêng của mình (`floating-pool` dùng cờ `hide_phone` trong cấu hình kho số).
