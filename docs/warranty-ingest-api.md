# API ingest tạo phiếu bảo hành — Thiết kế

Ngày: 2026-06-24
Branch: `feat/warranty-ingest-api`

## 1. Mục tiêu

Cung cấp một endpoint **public/external** để hệ thống ngoài (Lark / form bảo hành) tạo
**phiếu bảo hành** (`WarrantyClaim`), mô phỏng theo API ingest tạo đơn hàng
(`POST /api/orders/ingest`). Phiếu bảo hành gắn 1-1 với một `Order` đã tồn tại, được
resolve nghiêm ngặt theo mã vận đơn.

## 2. Quyết định đã chốt

- Endpoint: `POST /api/warranty/ingest` — **đồng bộ**, trả `200` + phiếu vừa tạo.
- Auth: token dùng chung **`ORDERS_INGEST_API_TOKEN`** (giống order ingest).
- Resolve đơn **chỉ theo `shipping_code`** (nghiêm ngặt): phải khớp đúng 1 đơn chưa xóa.
- Trùng phiếu (1 phiếu/đơn): `409`. Không tìm thấy / nhiều đơn / thiếu shipping_code: `400`.
- Giữ nguyên model `WarrantyClaim` (OneToOne bắt buộc Order). Không async, không throttle riêng.

## 3. Endpoint & Auth

`POST /api/warranty/ingest`

- `@authentication_classes([])` + `@permission_classes([AllowAny])` (không auth class).
- Token lấy từ header `X-Api-Token: <token>` hoặc `Authorization: Bearer <token>`.
- So khớp hằng-thời-gian (`secrets.compare_digest`) với `settings.ORDERS_INGEST_API_TOKEN`.
- Mirror hành vi order ingest:
  - Token chưa cấu hình + `IS_PRODUCTION=True` → `503` (fail-closed).
  - Token chưa cấu hình + non-prod → cho qua (nới lỏng dev).
  - Token có cấu hình nhưng sai/thiếu → `401`.
- Helper trích token **nhân bản nội bộ trong app `warranty`** (~8 dòng), không import internals
  của app `orders` (giữ module độc lập theo CLAUDE.md).

## 4. Payload (JSON phẳng)

| Field | Kiểu | Bắt buộc | Ghi chú |
|---|---|---|---|
| `shipping_code` | string | **Có** | Định danh resolve đơn |
| `status` | int | Không | 1..4, mặc định 1 (Mới) |
| `contact_channel` | string(100) | Không | |
| `require_carrier_call` | bool | Không | mặc định false |
| `social_nickname` | string(255) | Không | |
| `send_reason` | text | Không | |
| `send_method` | string(100) | Không | |
| `classification` | string(100) | Không | |
| `recall_code` | string(100) | Không | |
| `return_status` | string(100) | Không | |
| `accounting_check` | string(100) | Không | |
| `accounting_note` | text | Không | |

Validate qua `WarrantyClaimIngestSerializer` (reuse field nghiệp vụ + `shipping_code` required,
`validate_status` như `WarrantyClaimWriteSerializer`).

## 5. Luồng xử lý & mã trạng thái

1. Token thiếu/sai → `401`; prod chưa cấu hình token → `503`.
2. Payload không hợp lệ (thiếu `shipping_code`, status sai kiểu/khoảng) → `400`.
3. Resolve: `Order.objects.filter(shipping_code=shipping_code, deleted_at__isnull=True)`.
   - 0 đơn → `400` `{"detail": "Không tìm thấy đơn theo mã vận đơn."}`.
   - >1 đơn → `400` `{"detail": "Nhiều đơn cùng mã vận đơn, không thể xác định."}`.
   - đúng 1 → tiếp.
4. Đơn đã có phiếu chưa xóa (`WarrantyClaim` OneToOne) → `409` `{"detail": "Đơn hàng này đã có phiếu bảo hành."}`.
5. OK → tạo `WarrantyClaim(order=..., created_by=None, **fields)` → `200` + `WarrantyClaimSerializer`.

## 6. Clean layering

- `backend/apps/warranty/serializers.py`: thêm `WarrantyClaimIngestSerializer`.
- `backend/apps/warranty/claim_service.py`: thêm exception classes
  `WarrantyOrderNotFound` / `WarrantyOrderAmbiguous` / `WarrantyClaimExists`, và method
  `ingest_claim_by_shipping_code(shipping_code: str, data: dict) -> WarrantyClaim` thực hiện
  resolve + dup-check + tạo, raise exception tương ứng. (Tái dùng `_EDITABLE_FIELDS` hiện có.)
- `backend/apps/warranty/ingest_views.py` (mới): view `warranty_claim_ingest` + token helper,
  map exception → HTTP code.
- `backend/apps/warranty/urls.py`: thêm `path("ingest", warranty_claim_ingest, name="warranty-ingest")`.

## 7. Test (BE, qua harness docker `desi-order-web-test` trên net `desi-order_desi-net`)

- `200` tạo thành công (kèm header `X-Api-Token` hợp lệ; set `ORDERS_INGEST_API_TOKEN` qua override settings).
- `400` thiếu `shipping_code`.
- `400` không có đơn khớp.
- `400` nhiều đơn cùng `shipping_code`.
- `409` đơn đã có phiếu.
- `401` token sai (khi token đã cấu hình).

## 8. Không làm (YAGNI)

Không Celery/async, không throttle riêng (đã có token gate), không đổi schema model, không
resolve theo phone/order_code (chỉ shipping_code nghiêm ngặt).

## 9. Ghi chú tiến độ

- (cập nhật sau khi triển khai)
