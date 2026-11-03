# Phiếu bảo hành nhiều hành trình (Warranty Claim + Journeys)

- Ngày: 2026-07-08
- Nhánh: `feat/warranty-claim-journeys`
- App backend: `apps/warranty`
- Feature frontend: `frontend/src/features/warranty-claims`

## 1. Bối cảnh & mục tiêu

Model bảo hành hiện tại (`WarrantyClaim`, MR !764) là bảng **phẳng 1-1 với Order**: một phiếu chỉ giữ một tập trường nghiệp vụ (contact_channel, send_reason, classification, recall_code, accounting_note...). Thực tế một ca bảo hành đi qua **nhiều hành trình**: gửi hàng cho khách → khách không đồng ý → gửi lại lần nữa → ... Mỗi lần là một vòng đầy đủ: gửi đi → kho nhận hàng hoàn → CSKH chốt phương án → gửi đơn mới về khách.

Mục tiêu: tái cấu trúc thành **1 phiếu (1-1 Order) chứa N hành trình**, mỗi hành trình có đầy đủ thông tin người nhận / sản phẩm / vận chuyển / kiểm hàng / phương án / gửi lại, có trạng thái xử lý riêng. Phiếu có nút **Hoàn thành** để đóng.

### Quyết định đã chốt (brainstorming)
1. **Phạm vi hành trình**: section 2–7 lặp lại theo **từng hành trình**; section 1 (thông tin đơn gốc) là cấp phiếu (1 lần).
2. **Dữ liệu cũ**: migrate — mỗi phiếu cũ → 1 phiếu mới + 1 hành trình `seq=1`.
3. **Trạng thái hành trình**: `Mới`/`Đã lên đơn` auto suy ra từ mã vận đơn mới; `Đã đóng hàng` từ quét kho; `Đang giao`/`Thất lạc`/`GTC` chọn tay.
4. **Kiến trúc**: Hướng A — 2 bảng `warranty_claims` + `warranty_journeys` (bảng hành trình dạng cột rộng), không chuẩn hoá sâu, không JSONB.

## 2. Mô hình dữ liệu (Hướng A)

### 2.1 `warranty_claims` (cấp phiếu, 1-1 Order)

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `order` | OneToOneField(orders.Order, PROTECT, related_name="warranty_claim") | giữ như hiện tại |
| `customer_report_date` | DateField(null=True) | §1a Ngày khách báo lỗi (input) |
| `status` | PositiveSmallInteger choices | `1` Đang xử lý / `2` Hoàn thành / `3` Hủy |
| `completed_at` | DateTimeField(null=True) | set khi bấm Hoàn thành |
| `created_by` | FK(users.User, SET_NULL, null=True) | audit |
| `created_at` / `updated_at` | DateTime auto | |
| `deleted_at` | DateTimeField(null=True) | soft-delete |

**Đọc live từ Order (KHÔNG lưu trùng):** §1b Mã đơn gốc = `order.invoice_code`; §1c Ngày gửi đơn = `order.created_at`; §1d Kênh KiotViet = trường kênh KV của Order. Serializer nhúng subset Order read-only như hiện tại.

`db_table = "warranty_claims"`. Index: `status`, `created_at`, `deleted_at`.

### 2.2 `warranty_journeys` (N/phiếu)

| Nhóm | Cột | Kiểu / enum (code → nhãn) |
|---|---|---|
| — | `claim` | FK(WarrantyClaim, CASCADE, related_name="journeys") |
| — | `seq` | PositiveSmallInteger — thứ tự hành trình trong phiếu (1..n) |
| **§2 Người nhận** | `receiver_name` | CharField |
| | `receiver_phone` | CharField |
| | `receiver_raw_address` | TextField (raw như tạo đơn) |
| | `province_code` / `district_code` / `ward_code` | CharField — dùng AddressCatalog của Order |
| **§3 SP & lý do** | `product` | FK(products.Product, SET_NULL, null=True) |
| | `send_reason` | TextField |
| | `classification` | `same_product` Cùng SP / `different_product` Khác SP / `accessory` Phụ kiện |
| | `error_category` | `product` Sản phẩm / `customer` Khách / `shipping` Vận chuyển / `warehouse` Kho |
| **§4 Vận chuyển & chi phí** | `shipping_form` | `one_way` Đơn 1 chiều / `exchange` Đơn đổi hàng / `warranty_recall` Đơn thu hồi BH / `return_recall` Đơn thu hồi trả hàng / `warranty_return` Đơn trả BH |
| | `declared_value` | DecimalField(12,2) — khai giá |
| | `cod_amount` | DecimalField(12,2) |
| | `carrier` | `ghn` / `best` / `vtp` |
| | `shipping_fee` | DecimalField(12,2) |
| | `order_date` | DateField — ngày lên đơn |
| | `new_tracking_code` | CharField — mã vận đơn mới |
| | `recall_code` | CharField — mã thu hồi |
| | `note` | TextField |
| **Trạng thái** | `status` | `new` Mới / `created` Đã lên đơn / `packed` Đã đóng hàng / `shipping` Đang giao / `lost` Thất lạc / `delivered` GTC |
| **§5 Kho nhận hàng BH** | `warehouse_received_at` | DateField — ngày nhận hàng hoàn |
| | `inspection_result` | `broken_unrepairable` Hỏng-Ko sửa được / `broken_repairable` Hỏng-sửa được / `normal` Dùng BT / `wrong_item` Sai hàng |
| **§6 CSKH chốt phương án** | `resolution` | `new_machine` Gửi máy mới / `rewarranty` Gửi lại máy BH / `exchange` Đổi SP khác / `refund` Hoàn tiền |
| | `resolution_declared_value` | DecimalField(12,2) |
| | `resolution_cod` | DecimalField(12,2) |
| | `resolution_note` | TextField |
| **§7 Gửi đơn mới về khách** | `return_carrier` | `ghn` / `best` / `vtp` |
| | `return_shipping_fee` | DecimalField(12,2) |
| | `return_order_date` | DateField |
| | `return_tracking_code` | CharField |
| audit | `created_by` | FK(users.User, SET_NULL, null=True) |
| | `created_at` / `updated_at` | DateTime auto |

`db_table = "warranty_journeys"`. Index: `(claim, seq)` (unique_together để tránh trùng thứ tự), `status`, `new_tracking_code`, `return_tracking_code`.

Các trường enum lưu dạng `CharField(choices=...)`; text/tiền dùng default rỗng/`0`, blank cho phép để form lưu nháp từng phần.

## 3. Trạng thái hành trình (auto/manual)

Quy tắc set `status` khi service lưu journey:
- `new_tracking_code` rỗng → `new` (Mới).
- `new_tracking_code` có giá trị **và** status đang ở `new` → nâng lên `created` (Đã lên đơn).
- **Không tự hạ cấp**: nếu status đã ở mức cao hơn (`packed`/`shipping`/`lost`/`delivered`) thì auto-rule không đụng vào.
- `packed` (Đã đóng hàng): set qua **quét kho** — matcher quét khớp theo `new_tracking_code` của journey (xem §5.2).
- `shipping` / `lost` / `delivered`: người dùng chọn tay qua dropdown.

Thứ tự cấp bậc để so sánh "cao hơn": `new(0) < created(1) < packed(2) < shipping(3) < lost/delivered(4)`.

## 4. Kiến trúc backend (clean, giữ layering hiện có)

App `apps/warranty` giữ tách lớp: `models.py`, `claim_repository.py` + `journey_repository.py`, `claim_service.py` + `journey_service.py`, `serializers.py`, `claim_views.py` + `journey_views.py`, `urls.py`.

### 4.1 Endpoints (RBAC `require_section("warranty")`)

| Method | Path | Chức năng |
|---|---|---|
| GET | `/warranty/claims` | list phiếu (paginated, search theo order như hiện tại) |
| POST | `/warranty/claims` | tạo phiếu (order_id + customer_report_date) + tự tạo journey `seq=1` rỗng |
| GET | `/warranty/claims/{id}` | chi tiết phiếu + danh sách journeys nhúng |
| PATCH | `/warranty/claims/{id}` | sửa cấp phiếu (customer_report_date, status) |
| DELETE | `/warranty/claims/{id}` | soft-delete |
| POST | `/warranty/claims/{id}/complete` | set status=Hoàn thành + completed_at |
| POST | `/warranty/claims/{id}/journeys` | thêm hành trình mới (`seq = max+1`) |
| PATCH | `/warranty/journeys/{id}` | sửa 1 hành trình (partial — lưu từng section) |
| DELETE | `/warranty/journeys/{id}` | xóa hành trình |

Ràng buộc: 1 order tối đa 1 claim (giữ OneToOne + xử lý IntegrityError → 409 như hiện tại). Cho phép xóa journey tự do; phiếu có thể còn 0 journey — UI khi đó chỉ hiện nút "Thêm hành trình".

### 4.2 Ingest public
Giữ `POST /warranty/ingest` (token dùng chung `ORDERS_INGEST_API_TOKEN`): tạo claim theo `shipping_code` + tạo journey `seq=1` với các trường ingest map vào journey. Không đổi cơ chế auth.

## 5. Tích hợp quét kho ("Đã đóng hàng")

### 5.1 Điểm nối
Hệ thống quét hiện có (`apps/inventory`, `PendingShipmentScan` + matcher/cron) khớp theo `shipping_code` của Order. Bổ sung: khi kho quét một mã trùng `new_tracking_code` của một journey (chưa `packed`) → set journey.status = `packed`.

### 5.2 Phạm vi v1
Thêm bước tra journey theo mã quét trong luồng matcher (sau khi thử khớp Order). Nếu khớp journey → nâng trạng thái, ghi audit. Chi tiết wiring để ở sprint tích hợp (Sprint 4), không chặn model + CRUD.

## 6. Migration dữ liệu cũ

Data migration (Django) theo thứ tự:
1. Tạo bảng mới `warranty_journeys` + thêm cột mới vào `warranty_claims` (`customer_report_date`, `completed_at`, đổi choices `status`).
2. Với mỗi `WarrantyClaim` cũ (kể cả đã soft-delete): tạo 1 `WarrantyJourney` `seq=1` map:
   - `send_reason` → `send_reason`
   - `classification` (text tự do cũ) → `send_reason`/`note` (best-effort; enum mới không map 1-1 nên để trống `classification`, ghi giá trị cũ vào `note`)
   - `recall_code` → `recall_code`
   - `return_status` → `note`
   - `contact_channel` / `social_nickname` / `accounting_check` / `accounting_note` → gộp vào `resolution_note`
   - `require_carrier_call` → ghi chú trong `note`
   - `status` cũ (1 Mới/2 Đang xử lý/3 Hoàn tất/4 Hủy) → claim.status (1 Đang xử lý cho 1,2; 2 Hoàn thành cho 3; 3 Hủy cho 4) và journey.status = `new`.
3. Bỏ các cột nghiệp vụ cũ khỏi `warranty_claims` sau khi dữ liệu đã chuyển sang journey (drop trong migration sau, khi đã verify trên prod). Có thể giữ lại 1 migration "drop legacy columns" tách riêng để rollback an toàn.

Reversible ở mức tạo bảng; phần map dữ liệu ghi rõ không đảm bảo reverse hoàn hảo (documented).

## 7. Frontend

### 7.1 Trang danh sách (giữ, mở rộng nhẹ)
`WarrantyClaimsPage` + `WarrantyClaimsTable`: giữ search/filter/column-picker. Cột trạng thái đổi sang trạng thái **phiếu** (Đang xử lý/Hoàn thành/Hủy). Card mobile giữ nguyên bố cục.

### 7.2 Trang chi tiết `/sales/warranty/:id`
- **Header Section 1**: hiển thị Ngày khách báo lỗi (input), Mã đơn gốc / Ngày gửi / Kênh KV (read-only từ order), trạng thái phiếu, nút **Hoàn thành**.
- **Danh sách hành trình**: accordion (mỗi item = 1 hành trình, tiêu đề "Hành trình #seq" + badge trạng thái). Nút **Thêm hành trình**.
- **Form mỗi hành trình**: 6 nhóm §2–§7 theo `FormDialogShell`/section layout, auto-PATCH khi blur/thay đổi (như pattern draft hiện có). Tái dùng:
  - `AddressFields` (province/district/ward + raw) cho §2.
  - product-select async (`onSearchProducts`) cho §3.
  - dropdown carrier GHN/BEST/VTP, các enum dùng `components/ui/select` (KHÔNG react-select cho code mới).
- Data: React Query per-feature (`queries.ts`) — `useWarrantyClaim(id)`, `useCreateJourney`, `useUpdateJourney`, `useDeleteJourney`, `useCompleteClaim`; invalidate sau mutation.
- Types: typed từ `src/api/schema.d.ts` (openapi-typescript) sau khi cập nhật OpenAPI. Không khai báo type tay.

### 7.3 Convention
- Page trong `features/warranty-claims/pages/` (chuyển dần từ `src/pages/WarrantyClaimsPage.tsx` nếu thuận tiện, hoặc giữ vị trí cũ + thêm page chi tiết trong feature).
- ESLint: không thêm file mới vào allowlist react-select.

## 8. Testing

- **Backend (bắt buộc)**: unit test service (`create_claim` tạo journey seq=1, `add_journey` tăng seq, auto-status theo tracking code, `complete` set completed_at, migration mapping) + endpoint test (list/create/patch/complete/journeys CRUD, RBAC 403, 409 trùng order).
- **Frontend**: test cơ bản trang chi tiết render sections + thêm/sửa hành trình (theo baseline test hiện có; tránh viewport-mobile flaky đã biết).

## 9. Phân chia Sprint

- **Sprint 1 — Model & migration**: models `WarrantyClaim`(mở rộng) + `WarrantyJourney`, migration schema + data migration map dữ liệu cũ, unit test model/migration.
- **Sprint 2 — API & service**: repository/service/serializer/views cho claim + journey + complete + ingest adapt; OpenAPI schema; endpoint tests.
- **Sprint 3 — Frontend**: typed api module + queries, trang chi tiết accordion hành trình, form §2–§7, nút Hoàn thành; danh sách cập nhật cột trạng thái phiếu.
- **Sprint 4 — Tích hợp quét kho**: nối matcher set `packed` theo `new_tracking_code`; audit; test.

Mỗi sprint kết thúc: note đã làm / chưa làm, chạy test, commit.

## 10. Ngoài phạm vi (v1)
- Webhook hãng vận chuyển tự set `shipping`/`lost` (để manual).
- Báo cáo/analytics bảo hành theo hành trình.
- Notification tự động cho khách.
