# Best Express Direct Integration — Design

**Date:** 2026-07-13
**Branch:** `feat/best-express-integration`
**Status:** Approved (build the integration layer first; do not touch the KShip flow yet)

## 1. Bối cảnh & mục tiêu

Hiện tại vận đơn BEST được tạo qua KShip/KiotViet
(`apps/kiotviet/infrastructure/services/gateway_kship_customer_webhook.py`), trạng thái
giao hàng phụ thuộc sync từ KiotViet (trễ, thiếu chi tiết phí). Best Express cung cấp
API trực tiếp (tài liệu V3, host `sgp-seaedi.800best.com`).

**Mục tiêu giai đoạn này:** xây tầng đấu nối (integration layer) hoàn chỉnh với Best
Express — client đủ 8 endpoint, webhook nhận trạng thái, models lưu tài khoản/vận
đơn/sự kiện — **chưa** gắn vào luồng tạo đơn hiện có và **không** thay đổi luồng KShip.
Việc chọn vai trò (chạy song song hay thay thế KShip) quyết định ở giai đoạn sau.

## 2. Kiến trúc

App Django mới **`apps/best_express`**, độc lập hoàn toàn (không import từ
`apps.kiotviet`; chỉ tham chiếu `apps.orders.Order` qua FK nullable để link vận đơn).

```
apps/best_express/
├── __init__.py
├── apps.py
├── admin.py                      # quản trị account/shipment/event
├── models.py                     # BestExpressAccount, BestShipment, BestWebhookEvent
├── migrations/
├── domain/
│   └── status_map.py             # bảng mã trạng thái Best → enum nội bộ (pure, không I/O)
├── infrastructure/
│   ├── best_client.py            # HTTP client 8 endpoint + quản lý token
│   └── token_store.py            # cache token trên account + Redis lock chống spam login
├── application/
│   └── services/
│       ├── shipment_service.py   # create/update/cancel/update-address + ghi BestShipment
│       ├── tracking_service.py   # TraceQuery, EstimateFee, PrintWayBill
│       └── webhook_service.py    # xử lý event: idempotent update BestShipment
├── transport/
│   ├── webhook_views.py          # POST /api/best-express/webhook
│   └── serializers.py
├── urls.py
├── management/commands/
│   └── best_smoke_test.py        # smoke test tài khoản test (login → estimate → create → cancel)
└── tests/
```

Dependency direction: transport → application → infrastructure/domain → models.

## 3. Models

### 3.1 `BestExpressAccount`

Theo mẫu `KiotVietChannel` (credentials dùng `EncryptedCharField`).

| Field | Kiểu | Ghi chú |
|---|---|---|
| `name` | CharField | tên hiển thị |
| `environment` | CharField choices `test`/`production` | |
| `base_url` | CharField | mặc định theo environment; override được |
| `username` | CharField | |
| `password` | EncryptedCharField | |
| `token` | EncryptedTextField | JWT Best, hạn 30 ngày |
| `token_expires_at` | DateTimeField null | set = now + 29 ngày khi login |
| `ems_key` | CharField blank | header `ems-key` (optional theo doc) |
| `webhook_token` | EncryptedCharField | bearer tĩnh mình sinh, gửi sale Best cấu hình |
| `is_active` | BooleanField | |
| `created_at` / `updated_at` | auto | |

Không có Django admin page nào bắt buộc cấu hình ngoài admin chuẩn — config qua
admin/shell (giống KiotVietChannel).

### 3.2 `BestShipment`

Một dòng ↔ một vận đơn Best.

| Field | Kiểu | Ghi chú |
|---|---|---|
| `account` | FK BestExpressAccount (PROTECT) | |
| `order` | FK orders.Order null (SET_NULL) | link khi tạo từ order hoặc khớp sau |
| `merchant_order_code` | CharField unique | mã đơn phía mình (field `Code` gửi Best) |
| `code_best` | CharField unique null | mã tracking Best trả về |
| `route_code` | CharField blank | |
| `status` | CharField choices (enum nội bộ, §5) | |
| `best_status_code` | CharField blank | mã thô gần nhất từ Best (`666`, `604`, …) |
| `status_content` | TextField blank | lý do từ Best |
| `cod`, `product_price`, `freight_fee`, `cod_fee`, `insurance_fee`, `vat_fee`, `return_fee`, `discount`, `total_fee` | DecimalField(14,2) default 0 | |
| `weight`, `length`, `width`, `height` | IntegerField default 0 | gram / mm |
| `journey_type`, `service_id`, `pickup_type` | IntegerField | tham số đã gửi |
| `request_payload` | JSONField | payload Order/Add gần nhất (đối soát) |
| `last_event_at` | DateTimeField null | `modified_at` webhook mới nhất đã áp dụng |
| `created_at` / `updated_at` | auto | |

Index: `code_best`, `merchant_order_code`, `status`, `order_id`.

### 3.3 `BestWebhookEvent`

Log thô mọi webhook — phục vụ idempotency, đối soát, replay.

| Field | Kiểu | Ghi chú |
|---|---|---|
| `account` | FK null | account khớp theo webhook token |
| `code_best`, `merchant_order_code` | CharField (index) | |
| `best_status_code` | CharField | |
| `payload` | JSONField | body thô |
| `modified_at_ms` | BigIntegerField | `modified_at` từ Best (epoch ms) |
| `processed` | BooleanField default False | |
| `processing_error` | TextField blank | |
| `received_at` | auto_now_add | |

Unique constraint chống ghi trùng: (`code_best`, `best_status_code`, `modified_at_ms`).

## 4. HTTP Client (`best_client.py`)

Bọc 8 endpoint (đường dẫn cố định dưới `{base_url}/VietNamV3/v3/api/process/sears/`):

| Method | Endpoint | Ghi chú |
|---|---|---|
| `login()` | `User/Login` | trả token |
| `create_order(payload)` | `Order/Add` | |
| `update_order(payload)` | `Order/Update` | |
| `update_address(payload)` | `Order/UpdateAddress` | |
| `cancel_order(code)` | `Order/Cancel` | chỉ trước trạng thái 202 |
| `trace_query(codes, lang)` | `Order/Query` | chặn >10 mã/lần ngay tại client |
| `estimate_fee(payload)` | `Service/EstimateFee` | |
| `print_waybill(codes)` | `Print/PrintWayBill` | chặn >50 mã/lần; trả URL PDF |

Quy tắc:

- **Token:** dùng `account.token` nếu còn hạn; nếu thiếu/hết hạn hoặc call bị 401 →
  login lại **một lần** rồi retry request. Login bọc Redis lock + rate-limit
  (tối đa 5 login/phút/account — hạn chế của Best).
- **Error passthrough:** `Result == 2` → raise `BestExpressError(message)` giữ nguyên
  văn `Message` của Best (theo chuẩn passthrough đã áp dụng với KiotViet). Không nuốt,
  không dịch.
- Timeout 30s, `Content-Type: application/json`, `Authorization: Bearer <token>`,
  `ems-key` nếu account có.
- Không log credentials/token; log method + url + mã đơn.

## 5. Bảng map trạng thái (`domain/status_map.py`)

Enum nội bộ `BestShipmentStatus` + map từ mã Best:

| Enum nội bộ | Mã Best |
|---|---|
| `CREATED` | (sau Order/Add thành công, chưa có event) |
| `PICKING_UP` | 211 |
| `PICKUP_FAILED` | 203 |
| `PICKED_UP` | 202 |
| `IN_TRANSIT` | 301, 302, 303, 304, 309 |
| `OUT_FOR_DELIVERY` | 601 |
| `DELIVERY_FAILED` | 604 |
| `DELIVERED` | 666 |
| `RETURNING` | 605, 701, 702, 703, 704, 705 |
| `RETURN_FAILED` | 707 |
| `RETURNED` | 708 |
| `LOST` | 777 |
| `DAMAGED` | 1000 |
| `CANCELLED` | 2, `customer Cancel` |

Mã lạ → giữ nguyên `best_status_code`, status nội bộ không đổi, log warning
(không drop event).

## 6. Webhook

`POST /api/best-express/webhook` (public, ngoài auth session/JWT nội bộ):

1. Xác thực `Authorization: Bearer <webhook_token>` → tìm `BestExpressAccount` active
   khớp token. Sai/thiếu → 401.
2. Ghi `BestWebhookEvent` (bỏ qua nếu trùng unique constraint → vẫn trả Result 1).
3. Xử lý **đồng bộ trong request** (nhẹ — một update row): tìm `BestShipment` theo
   `code_best`, fallback `merchant_order_code`; áp dụng event **chỉ khi**
   `modified_at` ≥ `last_event_at` (chống out-of-order); cập nhật status + phí +
   weight/kích thước. Không tìm thấy shipment → vẫn lưu event (`processed=False`,
   ghi chú "unmatched") để đối soát sau.
4. Luôn phản hồi `{"Result": 1, "Message": ""}` khi đã ghi được event; lỗi hệ thống
   thật sự → `{"Result": 2, "Message": "..."}` để Best retry.

Lưu ý vận hành: URL webhook + `webhook_token` phải gửi cho sale/bưu cục Best để họ
cấu hình trên trang V9 (mình không tự đăng ký qua API được).

### 6.1 Note vận hành webhook

- URL chính xác `POST https://<domain>/api/best-express/webhook` (KHÔNG trailing
  slash — có slash sẽ 404).
- `webhook_token` phải tự sinh (vd
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`) và set vào
  `BestExpressAccount` qua shell/admin.
- Gửi cặp URL + token cho sale Best cấu hình trên trang V9 (mục Hồ sơ khách hàng →
  Thông tin khác → Mở API Shop lẻ).

## 7. Application services

- **`shipment_service.py`**
  - `create_shipment(account, data) -> BestShipment`: build payload Order/Add, gọi
    client, lưu `BestShipment` (code_best, route_code, request_payload). Hỗ trợ địa
    chỉ 2 cấp mới: nếu caller không truyền District → gửi chuỗi rỗng đúng chuẩn Best.
    `merchant_order_code` trùng → chặn trước bằng unique check, báo lỗi rõ.
  - `cancel_shipment(shipment)`: gọi Cancel, thành công → status `CANCELLED`.
  - `update_shipment(shipment, changes)` / `update_shipment_address(shipment, addr)`:
    gọi Update / UpdateAddress, lưu payload mới. Giới hạn nghiệp vụ của Best (sửa địa
    chỉ trước 303, số lần sửa, hạ COD…) **không** re-implement phía mình — gọi thẳng và
    passthrough lỗi Best trả về.
- **`tracking_service.py`**: `trace(codes)` (chunk 10 mã/call), `estimate_fee(params)`,
  `print_waybills(codes)` (chunk 50). Trace kết quả đồng thời sync ngược vào
  `BestShipment` nếu khớp mã (dùng chung logic áp event với webhook).
- **`webhook_service.py`**: `apply_event(event)` — hàm idempotent duy nhất mà cả
  webhook view lẫn trace-sync dùng chung.

Transaction: mỗi lần áp event là một transaction ngắn
(`select_for_update` trên BestShipment).

## 8. Cấu hình & bảo mật

- Settings mới trong `config/settings_parts/base.py`:
  `BEST_EXPRESS_BASE_URL_TEST` / `BEST_EXPRESS_BASE_URL_PROD` (default theo doc),
  dùng làm default khi account không override.
- Credentials nằm trong DB (encrypted), không nằm trong env — nhất quán với
  KiotVietChannel.
- Webhook endpoint thêm vào urls công khai; chỉ nhận POST, giới hạn body size mặc
  định của Django.

## 9. Testing

- **Unit (mock `requests`)**: token reuse/re-login-on-401/rate-limit login; từng
  endpoint build đúng payload; `Result=2` → raise nguyên văn; chunk 10/50.
- **Webhook endpoint tests**: auth sai → 401; event mới → shipment update + Result 1;
  event trùng (unique) → Result 1 không đổi dữ liệu; event out-of-order (modified_at
  cũ) → không ghi đè status; unmatched code → event lưu, Result 1.
- **Status map tests**: đủ bảng §5 + mã lạ.
- **Smoke test thủ công**: `manage.py best_smoke_test` chạy trên env test với tài
  khoản test trong tài liệu (login → estimate_fee → create → trace → cancel).

## 10. Ngoài phạm vi (giai đoạn sau)

- UI/endpoint nội bộ cho FE (nút "Gửi Best trực tiếp" trên trang đơn).
- Tự động link `BestShipment` ↔ `Order.shipping_code` hàng loạt / backfill.
- Thay thế hoặc chạy song song với KShip — cần quyết định nghiệp vụ riêng.
- Đơn Cargo (hàng nặng, `NumberOfProducts > 1`) — client cho phép truyền field nhưng
  không có logic chia kiện phía mình.
