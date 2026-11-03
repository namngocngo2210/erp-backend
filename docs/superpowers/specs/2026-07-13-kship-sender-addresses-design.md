# Bảng địa chỉ gửi KShip (kship_sender_addresses)

**Ngày:** 2026-07-13
**Branch:** `feat/kship-sender-addresses`
**Trạng thái:** Thiết kế đã duyệt, chờ implementation plan

## Vấn đề

Khi tạo mã vận đơn KShip, thông tin người gửi (`SENDER_LOCATION_ID`, `SENDER_WARD_ID`,
`SENDER_ADDRESS`, `SENDER_MOBILE`) đang hardcode trong
`backend/apps/kiotviet/infrastructure/services/service_actions.py` (nhánh
`ACTION_CREATE_KSHIP_DELIVERY`):

- Kênh mới hoặc chi nhánh Đại Mỗ legacy `1000000477` → location 238 / ward 260 (Phường Văn Quán)
- Kênh cũ (branch `89084`) → location 255 / ward 209 (Phường Đại Mỗ)
- Address mặc định `"số 60 Đại Mỗ"`, SĐT `+84 359 529 280`

Các field `sender_*` đọc từ `KiotVietChannel` qua `getattr` **không tồn tại trên model**
— 100% đơn luôn rơi về default cứng. Thêm điểm gửi mới phải sửa code.

## Mục tiêu

Tách sender ra một bảng địa chỉ gửi, cho phép chọn địa chỉ khi tạo vận đơn trên dialog,
và quản lý danh sách qua UI. Không đổi hành vi mặc định của các đơn hiện tại.

## Quyết định thiết kế (đã chốt với user)

1. **Nơi chọn:** dropdown trong `KshipDeliveryDialog` (cạnh provider/service/note).
2. **Nhập ID:** admin nhập tay `location_id`/`ward_id` khi thêm địa chỉ mới; migration
   seed sẵn 2 địa chỉ đang hardcode. Không build picker gọi API KiotViet (tần suất thêm
   kho mới rất thấp, ID public API chưa chắc khớp ID KShip dùng).
3. **Mặc định:** giữ rule theo kênh/branch như hiện tại, nhưng resolve qua bản ghi trong
   bảng thay vì hardcode ID. Dialog pre-select đúng địa chỉ đó.
4. **Quản lý:** popup CRUD inline trong dialog (pattern giống note preset picker); đọc =
   ai có quyền orders, sửa = `kiotviet:edit` (section có sẵn trong RBAC catalog).

## Data model

Bảng `kship_sender_addresses`, model `KshipSenderAddress` đặt trong
`apps/orders/models.py` (theo precedent `KshipNotePreset` / `kship_note_presets`):

| Field | Kiểu | Ghi chú |
|---|---|---|
| `name` | CharField(100) | Tên hiển thị, vd "Kho Văn Quán" |
| `code` | CharField(50), null=True, unique | Slug ổn định chỉ dùng cho bản ghi seed (`van-quan`, `dai-mo`) để rule mặc định trỏ vào; bản ghi thường lưu NULL (không lưu chuỗi rỗng để không vướng unique); UI không cho sửa/đặt |
| `location_id` | IntegerField | ID location nội bộ KiotViet (KShip) |
| `ward_id` | IntegerField | ID ward nội bộ KiotViet (KShip) |
| `address` | CharField(255) | Địa chỉ text người gửi |
| `mobile` | CharField(20) | SĐT người gửi |
| `sort_order` | IntegerField, default 0 | Thứ tự trong dropdown |
| `created_by` | FK User, SET_NULL | Giống note preset |
| `created_at` / `updated_at` | auto | |

Index: `(sort_order, id)` giống note preset.

**Migration seed 2 bản ghi** đúng giá trị đang hardcode:

| code | name | location_id | ward_id | address | mobile |
|---|---|---|---|---|---|
| `van-quan` | Kho Văn Quán | 238 | 260 | số 60 Đại Mỗ | +84 359 529 280 |
| `dai-mo` | Kho Đại Mỗ | 255 | 209 | số 60 Đại Mỗ | +84 359 529 280 |

(Address/mobile hai bản ghi giống nhau vì hiện trạng chỉ khác ID — user sửa lại sau qua UI.)

## Backend

### API CRUD — `/api/orders/kship-sender-addresses`

Theo đúng mẫu `kship_note_preset_view.py`:

- `GET` — permission `require_section(*ORDER_SECTIONS)`. Trả `{items, total, default_id}`.
  - Query param optional `ids` (comma-separated order ids): BE tính `default_id` theo rule
    kênh/branch của lô đơn (dùng đơn đầu tiên trong lô; lô cùng kênh khác branch rất hiếm).
    Không có `ids` → `default_id = null`.
- `POST` / `PATCH` / `DELETE` — permission `require_permission("kiotviet:edit")`.
  - Validate: `name`, `address`, `mobile` không rỗng; `location_id`, `ward_id` là số nguyên dương.
  - `code` là read-only qua API (không nhận từ request).
  - DELETE bản ghi seed (`code` khác rỗng) vẫn cho phép — rule fallback xử lý (xem dưới).

### Luồng tạo vận đơn

- `OrderCreateKshipDeliverySerializer` thêm `sender_address_id` (IntegerField, optional).
  View `create_kship_delivery` validate id tồn tại (400 nếu không) rồi lưu vào ingest job.
- Model job ingest (bảng đang chứa `kship_provider_code`, `kship_note` — xem
  `apps/orders/models.py` quanh `ACTION_CREATE_KSHIP_DELIVERY`) thêm cột
  `kship_sender_address_id` (IntegerField, null) — lưu id, không FK cứng để job cũ/bản ghi
  bị xóa không chặn queue.
- `ingest_job_tasks` truyền `sender_address_id` vào `KiotVietService.perform(...)`.
- `service_actions.py` nhánh `ACTION_CREATE_KSHIP_DELIVERY` — resolve `sender_info`:
  1. Có `sender_address_id` và bản ghi tồn tại → dùng bản ghi đó cho cả lô.
  2. Không có id (job cũ đang chạy dở / caller khác) hoặc bản ghi đã bị xóa → rule
     kênh/branch như hiện tại nhưng lookup bản ghi theo `code` (`van-quan`/`dai-mo`).
  3. Bản ghi seed cũng không còn → fallback hằng số cũ (giữ trong code như safety net)
     + log warning. Không bao giờ chặn đơn vì thiếu config.
- Logic resolve tách thành hàm riêng (vd `resolve_kship_sender_info(order, sender_address_id)`)
  để unit test độc lập, không phình thêm `service_actions.py`.

### Thay đổi hành vi có chủ đích

Hiện tại sender được tính **per-order** trong lô (theo branch từng đơn). Sau thay đổi,
dialog luôn gửi `sender_address_id` → cả lô dùng **một** địa chỉ. Lô cùng kênh khác
branch rất hiếm (chỉ trong kênh legacy) và một lô một điểm gửi dễ đoán hơn — chấp nhận.

## Frontend

- `KshipDeliveryDialog.tsx`: thêm select "Địa chỉ gửi" (components/ui select) cạnh
  provider/service. Fetch qua React Query (`queries.ts` per-feature) từ endpoint mới với
  `ids` của lô đơn; pre-select `default_id`; submit luôn kèm `sender_address_id`.
- Nút quản lý (icon) mở popup CRUD theo pattern `KshipNotePresetPicker`, chỉ render khi
  user có `kiotviet:edit` (permission list từ `/me`). Form thêm/sửa có cảnh báo:
  "Nhập sai location_id/ward_id sẽ tạo vận đơn sai điểm gửi."
- API module mới trong `src/lib/api/` typed từ `schema.d.ts`. Sau khi đổi BE schema:
  `npm run gen:api` rồi `npx prettier --write src/api/schema.d.ts` (gotcha đã biết).
- File mới nếu cần page/component đặt theo convention `features/orders/`.

## Testing

Backend (pytest, image `desi-order-web-test`, DB `-e POSTGRES_DB=desi_<unique>`):

- `resolve_kship_sender_info`: có id hợp lệ / id bị xóa / không id + kênh mới / không id +
  kênh cũ / bản ghi seed bị xóa → fallback hằng số.
- CRUD endpoint: GET với `ids` trả đúng `default_id`; user chỉ có `orders:view` GET được
  nhưng POST/PATCH/DELETE bị 403; user có `kiotviet:edit` CRUD được; `code` không sửa được
  qua API.
- `create_kship_delivery` view: `sender_address_id` không tồn tại → 400; id hợp lệ được lưu
  vào job và truyền tới payload `SENDER_*` (mock gateway).

Frontend: test dialog render select + pre-select default (theo baseline test hiện có của
orders feature).

## Ngoài phạm vi

- Picker chọn tỉnh/phường bằng tên (gọi API KiotViet) — làm sau nếu nhu cầu thêm địa chỉ
  tăng.
- Gán địa chỉ mặc định per-channel qua UI config kênh.
- Đụng vào `sender_address` của phiếu giao (`delivery_slip.html` / `invoice.py`) — luồng
  in phiếu lấy sender từ nguồn khác, không liên quan.
