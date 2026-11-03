# Thiết kế: Chọn quy định xem hàng (ShipperNote) khi tạo mã vận đơn KShip

- Ngày: 2026-08-10
- Trạng thái: **đã làm xong cả hai bước.**
  - Bước 1 — nhánh `feat/kship-shipper-note` (MR !1197, đã merge): đổi mặc định
    `CHOTHUHANG` → `CHOXEMHANGKHONGTHU`. Xem mục 8.
  - Bước 2 — nhánh `feat/kship-shipper-note-select`: cho chọn theo từng đơn, đúng
    thiết kế mục 5–6. Chênh lệch so với bản thiết kế ghi ở mục 10.

## 1. Bối cảnh

Khi tạo mã vận đơn KShip, backend dựng `SERVICE_EXTRA` với **ba giá trị hard-code**
trong `build_kship_create_order_payload()`
(`backend/apps/kiotviet/infrastructure/services/gateway_kship_customer_webhook.py`):

| Code | Value | Ý nghĩa |
|---|---|---|
| `PaymentBy` | `NGUOIGUI` | Người gửi trả phí |
| `GBH` | `product_price` | Khai giá |
| `ShipperNote` | `CHOTHUHANG` | **Cho thử hàng** |

Nghĩa là **mọi đơn, mọi hãng vận chuyển đều cho khách thử hàng**. Không xử lý được
hàng dễ vỡ, hàng giá trị cao, hay đơn có rủi ro boom. Dialog "Tạo mã vận đơn KShip"
trên FE chỉ cho chọn nhà vận chuyển / dịch vụ / ghi chú / địa chỉ gửi.

## 2. Mục tiêu

1. Đổi mặc định từ `CHOTHUHANG` sang `CHOXEMHANGKHONGTHU` (cho xem hàng, không cho thử).
2. Cho phép chọn quy định xem hàng **theo từng đơn** trong dialog tạo vận đơn.
3. Lưu lựa chọn vào DB để tra cứu khi có tranh chấp.

**Ngoài phạm vi:** `PaymentBy` và `GBH` giữ nguyên hard-code. Nếu sau này cần cho
chọn bên trả phí thì mở rộng theo đúng cơ chế của tài liệu này.

## 3. Hai phát hiện trong quá trình khảo sát

### 3.1. Provider/service hiện đang là batch-only

`KshipDeliveryDialog.tsx` cho chọn nhà vận chuyển và dịch vụ **theo từng dòng**,
nhưng `use-kship-delivery.ts` chỉ lấy lựa chọn của **đơn đầu tiên** rồi gửi lên
(comment tại chỗ ghi rõ "Assuming all orders use the same provider and service for
now"). Serializer nhận `provider_code` / `service_code` là field phẳng; chỉ `notes`
là dict theo `order_id`.

→ ShipperNote đi theo pattern của `notes` (dict per-order), không lặp lại hạn chế này.
Việc sửa provider/service thành per-order nằm ngoài phạm vi tài liệu này.

### 3.2. Có thể lấy được danh sách mã thật thay vì đoán

API `setting-service` của KShip trả về `data.clients.<provider>.services[]`, nhưng
`_normalize_kship_service()` đang **vứt bỏ mọi field ngoài `id` / `code` / `name`**.
Nhiều khả năng payload gốc chứa luôn định nghĩa dropdown `ShipperNote` của từng hãng.

→ Thêm management command dump payload thô để xác minh. Nếu payload có sẵn options,
lần sau nâng cấp sang đọc động rất rẻ.

## 4. Quyết định thiết kế

### 4.1. Nguồn danh sách mã: hằng số ở backend, đẩy ra qua API

Đã cân nhắc:

| Hướng | Đánh giá |
|---|---|
| **A. Hằng số BE, trả qua `/api/orders/kship-options`** | **Chọn.** Sai mã → sửa 1 chỗ ở BE, deploy backend, không cần build lại FE. |
| B. Hardcode ở FE | Sai mã phải build + deploy FE; BE vẫn cần bảng đối chiếu → hai nguồn sự thật. |
| C. Đọc động từ setting-service | Đúng nhất về lâu dài nhưng chưa xác minh được payload có field đó không. |

### 4.2. Rủi ro đã biết: ba mã vẫn là suy đoán

Codebase hiện chỉ có duy nhất `CHOTHUHANG` được xác nhận là mã thật. Hai mã còn lại
(`CHOXEMHANGKHONGTHU`, `KHONGCHOXEMHANG`) suy ra từ quy ước đặt tên của KShip, **chưa
đối chiếu tài liệu**.

Nếu mã sai, KShip có thể **im lặng bỏ qua** — đơn vẫn tạo được, nhưng shipper không
nhận đúng chỉ dẫn và khách vẫn được thử hàng như cũ. Đây là kịch bản nguy hiểm hơn cả
bị từ chối thẳng.

Giảm thiểu:
1. Management command `dump_kship_setting_service` để đọc payload thô, xác minh mã
   mà không cần đụng đơn khách.
2. Sau khi deploy, tạo thử **một** đơn thật và kiểm tra vận đơn phía KShip trước khi
   phổ biến cho vận hành.
3. Mã nằm trong hằng số backend → sửa nhanh, không cần build FE.

## 5. Kiến trúc

```
KshipDeliveryDialog.tsx        (chọn per-order + "Áp dụng tất cả")
        │
use-kship-delivery.ts          (state kshipShipperNotes, prefill mặc định)
        │
use-orders-bulk-actions.ts     POST /api/orders/kship-delivery { shipper_notes: {order_id: code} }
        │
order_crud_view.create_kship_delivery
        │                      → OrderKiotVietJob.kship_shipper_note
        │
ingest_job_tasks               → execute_order_action(shipper_note=...)
        │                        (qua KiotVietOrderFlowPort)
        │
service_actions                → build_kship_create_order_payload(shipper_note=...)
        │
        └──────────────────────→ SERVICE_EXTRA[ShipperNote] → KShip create-order API
```

Đi đúng đường sẵn có của `kship_note`, không mở thêm biên nào giữa hai app.

### 5.1. `apps/kiotviet` — chủ sở hữu giao thức KShip

Trong `gateway_kship_customer_webhook.py`:

```python
SHIPPER_NOTE_DEFAULT = "CHOXEMHANGKHONGTHU"
SHIPPER_NOTE_OPTIONS = (
    {"code": "CHOXEMHANGKHONGTHU", "name": "Cho xem hàng không thử", "is_default": True},
    {"code": "CHOTHUHANG",         "name": "Cho thử hàng",           "is_default": False},
    {"code": "KHONGCHOXEMHANG",    "name": "Không cho xem hàng",     "is_default": False},
)
```

`build_kship_create_order_payload(..., shipper_note: str = "")`:

- Mã rỗng hoặc **không nằm trong bảng** → dùng `SHIPPER_NOTE_DEFAULT` và ghi
  `logger.warning`. **Không bao giờ ném exception** — tạo vận đơn không được chết vì
  một trường phụ.
- `Name` trong entry `SERVICE_EXTRA` lấy từ bảng để `Code` và `Name` luôn khớp nhau
  (hiện tại đang ghép tay, dễ lệch).

`get_kship_providers_and_services()` trả thêm khóa `shipper_notes` là danh sách trên
→ FE có một nguồn sự thật duy nhất.

### 5.2. `apps/orders`

**Migration `0117_orderkiotvietjob_kship_shipper_note`**

```python
kship_shipper_note = models.CharField(max_length=32, blank=True, default="")
```

Không backfill. Job cũ để rỗng, đọc ra hiểu là "tạo trước khi có tính năng".

**Serializer** `OrderCreateKshipDeliverySerializer`:

```python
shipper_notes = serializers.DictField(
    child=serializers.CharField(allow_blank=True, max_length=32),
    required=False,
    default=dict,
)
```

Cố ý **không** whitelist ở tầng orders: hằng số thuộc về `apps.kiotviet`, orders chỉ
được đi qua port (ràng buộc `.importlinter`). Tầng payload builder là nơi phán quyết.

**View** `create_kship_delivery`: `kship_shipper_note=shipper_notes.get(str(order_id), "")`
khi dựng `OrderKiotVietJob`, y hệt cách `kship_note` đang làm.

**Port** `KiotVietOrderFlowPort.execute_order_action`: thêm tham số
`shipper_note: str = ""`; adapter và `service_actions` chuyển tiếp xuống payload builder.

**Task** `ingest_job_tasks`: truyền `shipper_note=job.kship_shipper_note or ""`.

### 5.3. Frontend

- `use-orders-data.ts`: type `KshipShipperNoteOption { code, name, is_default }`, đọc
  từ response `kship-options`.
- `use-kship-delivery.ts`: state `kshipShipperNotes: Record<number, string>` và
  `kshipBulkShipperNote`. Khi tải options xong thì **prefill mọi đơn bằng option
  `is_default`** — hành vi mặc định đúng ngay cả khi người dùng không đụng gì.
- `KshipDeliveryDialog.tsx`: một `Select` ở hàng "Áp dụng tất cả" (nút "Áp dụng tất cả"
  set luôn cột này) và một cột **"Quy định xem hàng"** rộng ~180px trong bảng. Bảng
  đang `min-w-[1220px]` → nâng lên `1400px`.
- `i18n/messages/orders.ts`: khóa `orders.kiotviet.kshipShipperNote` +
  `kshipShipperNotePlaceholder`. Tên option lấy từ API, không dịch ở FE.

### 5.4. Management command (xác minh mã)

`backend/apps/kiotviet/management/commands/dump_kship_setting_service.py` — nhận
`--channel-id`, gọi `_request_kship_setting_service()` rồi in JSON thô (đã che token).
Theo pattern của `debug_invoice_status.py`.

## 6. Test

Bám cấu trúc test sẵn có, **không tạo file test lẻ cho từng hàm**.

**`apps/kiotviet/tests/test_invoice_payload_builder.py`**
- Không truyền `shipper_note` → payload mang `SHIPPER_NOTE_DEFAULT`.
- Truyền mã hợp lệ → payload giữ đúng mã, `Name` khớp bảng.
- Truyền mã lạ → fallback về default và có warning (assert qua `assertLogs`).

**`apps/orders` — test view `kship-delivery`**
- `shipper_notes` per-order vào đúng `OrderKiotVietJob` tương ứng.
- Thiếu key cho một đơn → job đó lưu chuỗi rỗng (payload builder tự lấy default).
- Không gửi `shipper_notes` → toàn bộ job rỗng, request vẫn 200 (tương thích ngược).

**`frontend/src/features/orders/__tests__/order-mutations.test.tsx`**
- Body POST có `shipper_notes` đúng dạng `{ "<id>": "<code>" }`.
- Mở dialog → mọi dòng được prefill bằng option `is_default`.

## 7. Ảnh hưởng vận hành

- **Đổi hành vi mặc định**: sau khi deploy, đơn mới **không còn cho khách thử hàng**.
  Cần báo trước cho vận hành và CS.
- Không cần biến môi trường mới → không phải sửa docker compose.
- Migration chỉ thêm cột có default, chạy được giờ hành chính, không khóa bảng lâu.
- `backend/schema.yaml` sẽ đổi do serializer đổi. Regen trong MR này vì thay đổi thuộc
  đúng phạm vi; nếu diff kéo theo nhiễu của feature khác thì tách MR chore riêng.

## 8. Bước 1 đã làm: đổi mặc định

Phạm vi MR !1197 — **chỉ đổi hằng số, chưa có UI chọn**:

- `gateway_kship_customer_webhook.py`: tách hai hằng số `KSHIP_SHIPPER_NOTE_CODE` /
  `KSHIP_SHIPPER_NOTE_NAME` ra khỏi thân hàm, đặt giá trị `CHOXEMHANGKHONGTHU` /
  "Cho xem hàng không thử". `SERVICE_EXTRA` đọc từ hằng số nên `Code` và `Name` không
  còn lệch nhau được.
- `test_gateway_kship_invoice_lookup.py`: chốt giá trị `ShipperNote`, khẳng định
  `PaymentBy` / `GBH` không đổi, và `NOTE` / `MONEY_COLLECTION` / `ORDER_SERVICE` vẫn
  đi đúng vào payload.

Không migration, không đổi API, không đổi FE.

**Vẫn còn nguyên rủi ro ở mục 4.2**: `CHOXEMHANGKHONGTHU` chưa được xác minh với tài
liệu KShip. Bắt buộc kiểm tra một đơn thật ngay sau khi deploy — nếu KShip bỏ qua mã
lạ trong im lặng thì nhìn từ hệ thống mình mọi thứ vẫn "thành công".

## 9. Bước 2 đã làm: cho chọn theo từng đơn

Đúng thiết kế mục 5–6. Danh sách file:

| Lớp | File | Thay đổi |
|---|---|---|
| kiotviet | `gateway_kship_customer_webhook.py` | `KSHIP_SHIPPER_NOTE_OPTIONS` + `list_shipper_note_options()` + `resolve_shipper_note()`; builder nhận `shipper_note`; `get_kship_providers_and_services` trả thêm `shipper_notes` |
| kiotviet | `service_actions.py`, `adapters.py` | chuyển tiếp `shipper_note` xuống builder |
| orders | `contracts/kiotviet_order_flow_port.py` | thêm tham số `shipper_note` vào port |
| orders | `models.py` + migration `0117` | cột `kship_shipper_note` |
| orders | `order_action_serializers.py`, `order_crud_view.py` | nhận dict `shipper_notes`, ghi vào job |
| orders | `ingest_job_tasks.py` | truyền `job.kship_shipper_note` sang port |
| FE | `use-orders-bulk-actions.ts` | type `KshipShipperNoteOption`, parse options, gửi `shipper_notes` |
| FE | `use-kship-delivery.ts` | state + prefill theo `is_default` |
| FE | `KshipDeliveryDialog.tsx`, `OrdersKshipDialogs.tsx` | dropdown bulk + cột trong bảng |

## 10. Chênh lệch so với bản thiết kế

- **Không regen `backend/schema.yaml`.** File đang lệch sẵn 444 dòng so với code trên
  master; regen ở đây sẽ kéo nhiễu của các feature khác vào MR. Trả nợ bằng MR chore
  riêng — ghi vào mục 11.
- **Không thêm khóa i18n.** Dialog KShip hiện dùng `t(key, fallback)` với khóa chưa
  từng được khai báo trong `i18n/messages/orders.ts` (`kshipProvider`, `kshipNote`…);
  thêm riêng hai khóa mới sẽ lệch với phần còn lại của chính màn hình đó.
- **Test đặt ở `apps/orders/tests/test_kship_shipper_note.py`** thay vì nhét vào
  `test_order_bulk_action_permissions.py` (file đó chỉ lo phân quyền) hay
  `test_kship_sender_address.py` (tên file nói về địa chỉ gửi).
- **Không làm management command `dump_kship_setting_service`** (mục 5.4) — đã chốt
  xác minh mã bằng một đơn thật sau deploy thay vì dump payload.

## 11. Việc còn nợ

- [ ] **Xác minh 3 mã bằng đơn thật.** Vẫn là rủi ro số một, xem mục 4.2.
- [ ] Regen `schema.yaml` (MR chore riêng, cùng đợt với 444 dòng lệch sẵn).
- [ ] Đọc động options từ `setting-service` (hướng C, mục 4.1) nếu payload có sẵn.
- [ ] Cho chọn `PaymentBy` (bên trả phí) khi có nhu cầu chương trình khách trả ship.
- [ ] Sửa provider/service thành per-order thật, hoặc bỏ dropdown per-order khỏi UI cho
      khỏi hiểu nhầm — nay cột "Quy định xem hàng" per-order chạy thật, nên hai cột
      provider/service nằm cạnh mà chỉ ăn dòng đầu càng dễ gây hiểu nhầm hơn trước.
