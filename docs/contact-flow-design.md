# Module Tác nghiệp CRM (contact_flow) — Thiết kế

> Ngày: 2026-07-18 · Trạng thái: **ĐÃ CODE XONG 2 SPRINT — chờ merge MR + smoke prod** (xem §9)
> Nguồn yêu cầu: file `ImportTacNghiep.xlsx` (template import của phần mềm CRM cũ)
> + trang Danh sách contact hiện có (`docs/sale-contacts-page-design.md`).

## 1. Bài toán

Trang Danh sách contact hiện chưa có khái niệm "tác nghiệp": sale không biết khách
đang ở bước chăm sóc nào, bao giờ phải gọi lại, và quy trình nằm trong đầu từng
người. Công ty đã có sẵn kịch bản telesale + CSKH + resell dạng máy trạng thái
(34 bước, 12 kết quả, 131 rule chuyển tiếp trong file Excel) và **kịch bản này
đổi liên tục** — không được hardcode.

Mục tiêu: module cấu hình được hoàn toàn qua UI/import Excel, kèm động cơ runtime
trên trang contact: sale ghi kết quả → hệ thống tự chuyển khách sang bước kế +
hẹn giờ; đến giờ thì khách nổi lên danh sách cần gọi.

## 2. Các quyết định đã chốt (với CEO/user 18/07)

| # | Quyết định | Chọn |
|---|---|---|
| 1 | Phạm vi | Full: cấu hình + động cơ runtime (chia 2 sprint) |
| 2 | Quan hệ với `sales_status_code` (19 mã hardcode) | **Tách độc lập**, không đụng cột "Kết quả" cũ, không đụng báo cáo/journey. Mapping để sau. |
| 3 | Khách vào flow lúc nào | Tự động khi có đơn/lead landing mới + lazy entry khi sale ghi kết quả trên khách cũ |
| 4 | Số flow | 1 flow toàn công ty (schema chừa đường thêm `flow_id` sau) |
| 5 | UX chính | Nhúng vào trang Danh sách contact hiện tại (cột + chip lọc), không tạo trang làm việc mới |
| 6 | Cờ chạy thật v1 | **Sửa giờ** + **Đặt lịch**. "Kho số" (floating pool) và "Nhãn khách hàng" chỉ lưu, chưa có hành vi |
| 7 | Ngữ nghĩa import | **Upsert theo Tên, không xóa**; bỏ dùng = deactivate qua UI |
| 8 | Preview flow | Tab "Sơ đồ flow" trong trang cấu hình, vẽ tương tác bằng `@xyflow/react` + dagre |
| 9 | Kiến trúc | Bảng quan hệ chuẩn + engine service đồng bộ (không JSON blob, không workflow engine ngoài) |

## 3. Data model — app mới `backend/apps/contact_flow/`

Cấu trúc app theo pattern hiện hành: `models.py`, `application/services/`,
`transport/views` + `transport/serializers`, `migrations/`, `tests/`.

### 3.1 `OperationStage` — bảng `operation_stages` ("Tác nghiệp")

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `name` | varchar(120) **unique** | Khóa upsert khi import |
| `color` | varchar(7), default `''` | Mã hex nền |
| `is_start` | bool | Cờ KhoiDau — tối đa 1 bản ghi active `true` (validate ở service, không constraint DB) |
| `allow_edit_due` | bool | Cờ "Sửa giờ" |
| `pool_enabled` | bool | Cờ "Kho số" — v1 lưu thô |
| `pool_minutes` | int, default 0 | "Kho số thời gian (phút)" — v1 lưu thô |
| `customer_label` | varchar(120), default `''` | "Nhãn khách hàng" — v1 lưu thô |
| `is_active` | bool, default true | Deactivate thay xóa cứng |
| `position` | int | Thứ tự hiển thị (STT trong file) |
| `created_at` / `updated_at` | datetime | |

### 3.2 `OperationResult` — bảng `operation_results` ("Kết quả tác nghiệp")

`name` (unique, khóa upsert), `color`, `is_close` (cờ "Chốt đơn"),
`requires_schedule` (cờ "Đặt lịch"), `is_active`, `position`, timestamps.

### 3.3 `OperationTransition` — bảng `operation_transitions` (131 rule)

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `from_stage` | FK OperationStage, **PROTECT** | "Nếu" |
| `result` | FK OperationResult, **PROTECT** | "Kết quả" |
| `to_stage` | FK OperationStage null, **PROTECT** | "Thì" — null = kết thúc nhánh |
| `delay_minutes` | int null | "Sau bao lâu" quy hết về phút; null khi `to_stage` null |

`unique_together (from_stage, result)` — khóa upsert import. PROTECT cả 3 FK:
không xóa được stage/result đang có rule dùng (phải xóa rule trước hoặc deactivate).
Validate (cả import lẫn CRUD): `to_stage` khác null ⇒ `delay_minutes` bắt buộc ≥ 1;
`to_stage` null ⇒ `delay_minutes` phải null.

**Không có bảng "Sau bao lâu"** — sheet `tacnghiepsaubaolau` chỉ là danh mục
dropdown của phần mềm cũ. FE cho nhập số + đơn vị (phút/giờ/ngày), lưu int phút.

### 3.4 `CustomerOperationState` — bảng `customer_operation_states` (runtime, 1 dòng/khách)

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `customer` | OneToOne `customers.Customer`, CASCADE | |
| `current_stage` | FK OperationStage, PROTECT | |
| `due_at` | datetime null | null = flow kết thúc / không có bước kế |
| `entered_at` | datetime | Vào bước hiện tại lúc nào |
| `last_result` | FK OperationResult null, SET_NULL | |
| `updated_at` | datetime | |

Index: `(due_at)`, `(current_stage, due_at)`.

### 3.5 Lịch sử — không thêm bảng

`ContactActivity` (app customers) thêm choice kind = `operation` (không đổi cột DB).
Mỗi lần chuyển bước ghi 1 activity với JSON meta (dùng lại cột `call_meta`, đặt tên
key rõ ràng): `{from_stage, result, to_stage, due_at}`. Timeline/popover trang
contact thêm 1 case render: "Gọi lần đầu —(KNM/TB)→ KNM lần 1, hẹn 14:30".

## 4. Engine — `application/services/operation_engine.py`

Service thuần, đồng bộ trong request, `select_for_update` dòng state trong
`transaction.atomic`. 3 hàm public:

### 4.1 `enter_start(customer)`
Gán bước `is_start` + `due_at = now` (đến hạn ngay). No-op nếu đã có state hoặc
chưa cấu hình bước khởi đầu.

### 4.2 `apply_result(customer, result, *, actor, scheduled_at=None)`

| Tình huống | Hành vi |
|---|---|
| Có rule, có `to_stage` | `current_stage = to_stage`, `entered_at = now`, `due_at = now + delay_minutes` |
| Có rule, `to_stage` null | Giữ bước, `due_at = null` → kết thúc nhánh, rơi khỏi danh sách cần gọi |
| **Không có rule** cho (bước, kết quả) | Giữ bước, `due_at = null`, vẫn ghi log — không chặn sale, không đoán bước kế |
| Kết quả `requires_schedule` (Đặt lịch) | `scheduled_at` bắt buộc (400 nếu thiếu/quá khứ); `due_at = scheduled_at` **đè** delay rule; bước kế vẫn theo rule (không rule → đứng yên) |
| Khách **chưa có state** | Lazy entry: tự `enter_start` rồi apply tiếp trong cùng transaction |

Mọi nhánh đều ghi 1 `ContactActivity` kind=`operation`.

### 4.3 `edit_due(customer, new_due_at, actor)`
Chỉ khi `current_stage.allow_edit_due`; chỉ đổi `due_at`; ghi log; 400 nếu quá khứ.

### 4.4 Entry tự động — 2 tầng (bài học lead-assignment)
1. **Hook tại choke point ingest đơn landing** (cùng chỗ lead-assignment
   sync-at-ingest, cùng danh sách loại trừ đơn sàn/koc): khách chưa có state →
   `enter_start`.
2. **Sweep cron 5 phút** nhặt sót: khách có đơn landing tạo trong 24h chưa có state.
3. **Không backfill khách cũ hàng loạt** — khách cũ vào flow qua lazy entry (4.2).

### 4.5 Bucket đến hạn — tính lúc query, không cần cron
`overdue` = `due_at < now` · `today` = trong ngày (+07) · `upcoming` = tương lai
sau hôm nay · `ended` = `due_at` null · `none` = chưa có state.

### 4.6 Edge case
- Đổi config giữa chừng: khách giữ bước đang đứng (kể cả bước bị deactivate —
  hiện badge "ngưng dùng"); rule mới chỉ áp cho lần ghi kết quả sau.
- 2 sale ghi cùng lúc 1 khách: `select_for_update` tuần tự hóa, cả 2 có log.
- Thiếu / có >1 bước `is_start` active: import & UI chặn; nếu data cũ vẫn rơi vào
  → entry no-op + cảnh báo đỏ ở trang cấu hình.

## 5. API — prefix `/api/contact-flow/` (app tự đăng ký urls)

### 5.1 Cấu hình (RBAC section mới `contact_flow`)
- `GET/POST /stages`, `PATCH /stages/{id}`
- `GET/POST /results`, `PATCH /results/{id}`
- `GET/POST /transitions`, `PATCH/DELETE /transitions/{id}`
- `POST /import` — multipart xlsx:
  - Parse 3 sheet theo tên `tacnghiepcan`, `ketquatacnghiep`, `tacnghiepcauhinh`
    (sheet khác bỏ qua); header dòng 3; "30 phút / 1 giờ / 10 ngày" → phút.
  - Thứ tự: stages → results → transitions, tất cả trong 1 transaction
    **atomic all-or-nothing**: có lỗi dòng nào → 400 kèm danh sách
    `{sheet, dòng, lý do}`, không ghi gì; thành công → `{created, updated}` theo loại.
- Tab sơ đồ không cần endpoint riêng — FE ghép `GET /stages` + `GET /transitions`,
  tự tính cảnh báo (bước không ai trỏ tới, bước cụt, thiếu/thừa bước khởi đầu).

### 5.2 Runtime (quyền section `contacts` hiện có + scope check tái dùng)
- `POST /customers/{id}/result` `{result_id, scheduled_at?}` → `apply_result`
- `POST /customers/{id}/due` `{due_at}` → `edit_due`
- Scope: tái dùng `user_owns_customer` / `customer_in_channels` của
  ContactActivityService (bài học audit 2026-07-15b — mọi scope đều phải kiểm).

### 5.3 Sửa `GET /api/sale/contacts` (contact_list_service)
- Thêm block `operation {stage_name, stage_color, due_at, bucket}` per item —
  join 1 lần qua `CustomerOperationState` (không N+1).
- Param lọc `operation_bucket` (overdue/today/upcoming/ended/none) +
  `operation_counts` trong response cho hàng chip.
- Sau khi đổi contract: `npm run gen:api` + `prettier --write` (gotcha đã biết).

## 6. Frontend

### 6.1 Trang cấu hình `features/contact-flow/pages/ContactFlowSettingsPage.tsx`
4 tab: **Tác nghiệp / Kết quả / Rule / Sơ đồ flow** + nút "Import Excel"
(dialog upload → báo cáo thành công hoặc bảng lỗi từng dòng).
Convention bắt buộc: React Query (`queries.ts` per-feature), API module
`src/lib/api/contact-flow.ts` typed từ `schema.d.ts`, components/ui, FormDialogShell
cho dialog form.

### 6.2 Tab Sơ đồ flow (preview sau import)
- `@xyflow/react` + dagre auto-layout (2 dependency FE mới).
- Node = tác nghiệp (đúng màu config, badge ▶ cho bước khởi đầu, badge "ngưng dùng").
- Cạnh = rule, nhãn "kết quả · thời gian"; `to_stage` null vẽ node chìm "Kết thúc".
- Click node → highlight cạnh vào/ra; dropdown lọc theo kết quả để giảm rối.
- Panel cảnh báo cấu hình (dead-end, unreachable, is_start sai) — logic thuần
  tách file util có unit test.

### 6.3 Trang Danh sách contact (sửa)
- Cột "Tác nghiệp": badge màu bước + giờ đến hạn (đỏ khi quá hạn).
- Hàng chip 4 bucket + số đếm (cạnh hàng chip action hiện có).
- Dropdown "Ghi kết quả TN" per dòng → gọi `POST /result`; kết quả có cờ Đặt lịch
  → dialog chọn ngày giờ. Cột "Kết quả" (sales_status) cũ **giữ nguyên**.
- Icon sửa giờ khi bước hiện tại `allow_edit_due`.

## 7. RBAC & vận hành

- Section mới `contact_flow` (trang cấu hình): thêm `rbac_catalog.py` **và
  RolesPage.tsx** (bài học sale-floating-pool: quên RolesPage là không hiện).
- Route + sidebar: mục "Cấu hình tác nghiệp" trong nhóm cấu hình (nav-layout.ts).
- Migration: 1 migration app mới (4 bảng + index); customers thêm choice kind
  (no-op DB). Cron sweep đăng ký cùng cơ chế cron hiện có.
- Prod rollout: chạy migration → import file Excel thật → bật cron. Không cần
  backfill.

## 8. Test

- **Engine (unit, quan trọng nhất):** đủ các nhánh bảng 4.2 + lazy entry +
  edit_due (cờ cho phép/không) + tuần tự hóa 2 request.
- **Import service:** parse thời gian, upsert (trùng tên → update), lỗi dòng,
  atomic (lỗi 1 dòng → không ghi gì), file thật `ImportTacNghiep.xlsx` làm fixture.
- **Contact list:** filter `operation_bucket`, counts, không N+1 (assertNumQueries).
- **API:** scope OWN/CHANNEL cho 2 endpoint runtime; quyền section cho config.
- **FE:** util tính bucket, util cảnh báo đồ thị, test component dialog Đặt lịch.

## 9. Sprint

### Sprint 1 — Backend + trang cấu hình (kèm sơ đồ)
- [x] App `contact_flow`: 4 model + migration
- [x] `OperationEngine` + unit test
- [x] Import service + endpoint + test (fixture file thật)
- [x] CRUD API stages/results/transitions + RBAC section mới
- [x] Hook entry ingest + sweep cron
- [x] Trang cấu hình 4 tab + import dialog + sơ đồ React Flow
- [x] gen:api + prettier

### Sprint 2 — Tích hợp trang Contact
- [x] `GET /api/sale/contacts`: block operation + filter bucket + counts
- [x] 2 endpoint runtime + scope test
- [x] Cột Tác nghiệp + chip bucket + dropdown ghi kết quả + dialog Đặt lịch + sửa giờ
- [x] Timeline render kind `operation`
- [ ] Smoke prod + import file Excel thật (chưa làm — cần môi trường prod/staging)

### Ngoài phạm vi (ghi nhận, chưa làm)
- Hành vi cờ "Kho số" (nối floating pool) và "Nhãn khách hàng".
- Mapping Kết quả tác nghiệp → `sales_status_code`.
- Multi-flow theo kênh/sản phẩm (schema đã chừa đường: thêm cột `flow_id`).
- Nhắc/cảnh báo Lark khi quá hạn.

#### Ghi chú sau thực hiện (18/07/2026)
- `StageTransition` dùng hard-delete (không có `is_active`) — đúng theo model, transition không cần soft-delete vì chỉ là cạnh đồ thị, xoá là xoá hẳn.
- Chip đếm ở trang cấu hình hiển thị theo **tổng số** (không tách theo bucket) — đơn giản hoá so với ý ban đầu, đủ dùng cho MVP.
- Scope helper cho 2 endpoint runtime được tách chung sang `apps/orders/contact_scope.py` (dùng lại logic scope OWN/CHANNEL sẵn có của orders thay vì viết lại trong `contact_flow`).
- File vượt ~300 dòng đã tách: `contact_list_operation.py` tách riêng khỏi view chính của `apps/sale` để giữ file gọn.
- Section key RBAC dùng `contact_flow` (underscore) trong khi convention catalog hiện tại đa số dùng hyphen — chưa đổi vì đụng tới migration RBAC catalog, **chờ quyết định** có cần đổi tên section không trước khi merge.
- BE full-suite (`contact_flow` + `orders` + `customers` + `users`): 749 passed, 2 failed (cả 2 đều do "Administrator role seed collision" baseline sẵn có, không liên quan module này).
- FE: 574/574 test pass, `tsc --noEmit` sạch, `npm run build` thành công.

## 10. Ghi chú dữ liệu file mẫu (đã validate 18/07)

131 rule sạch: không tham chiếu lỗi, không trùng cặp (bước, kết quả). Cảnh báo:
2 bước **"Resell nhắc hàng tháng (2 hộp)" / "(3 hộp)" không có rule đi ra**
(bản Gói và 1 hộp có) — cần user xác nhận là chủ đích dừng hay thiếu config;
tab Sơ đồ sẽ tự hiện cảnh báo này sau import.

## 11. Đặt bước tay từ cột Tác nghiệp + 5 bước chăm sóc (19/08/2026)

Sale phản hồi: cột Tác nghiệp chỉ hiển thị, muốn **chọn được bước trong danh sách**, và
cần thêm 5 bước **HDSD, D3, D7, D15, D30**.

**`OperationEngine.set_stage(customer, stage_id, due_at, *, actor)`** — đường đi TẮT có
chủ đích quanh máy trạng thái: không cần tồn tại rule `(from_stage, result) → to_stage`.
Ba điểm đã chốt:
- **`due_at` bắt buộc và phải ở tương lai**, cùng luật `edit_due`. Không có transition thì
  không có `delay_minutes` để suy ra hạn; để trống thì bước mới rơi thẳng vào nhóm
  "Kết thúc" — không phải ý của thao tác này. Frontend vì vậy hỏi giờ hẹn bằng dialog
  TRƯỚC khi gửi, và gửi ĐÚNG MỘT request mang cả `stage_id` lẫn `due_at`.
- **Lazy-create state** như `apply_result`: khách chưa vào flow (cột đang hiện "—") vẫn
  chọn được bước — thiếu nhánh này thì tính năng vô dụng đúng với nhóm khách cần nó nhất.
- **`last_result` giữ nguyên.** Đổi bước tay không phải một "kết quả"; kết quả gần nhất
  vẫn là cái sale đã ghi thật. Mọi lần đổi ghi 1 `ContactActivity` kind=operation,
  `event="set_stage"` (kèm `from_stage`, `to_stage`, `due_at`) để lịch sử truy được.

**`GET /api/contact-flow/stages` mở thêm cho section `contacts`** (trước đây chỉ
`contact_flow`). Dropdown cần danh sách bước mà sale không có section `contact_flow`;
quyền GHI không đổi — POST/PATCH vẫn đòi `contact_flow:edit`. Cùng cách `results` đã mở
sẵn cho dropdown cột Kết quả.

**Migration `contact_flow/0002`** seed 5 bước, idempotent theo `name`, `allow_edit_due=True`,
KHÔNG sinh transition nào. Lưu ý vận hành: **"HDSD" đã có sẵn trong file cấu hình flow đang
dùng** (`ImportTacNghiep.xlsx`), nên trên prod seed sẽ bỏ qua đúng bước đó và chỉ thêm 4 bước
D3/D7/D15/D30. Muốn chuỗi HDSD → D3 → D7 → D15 → D30 tự nhảy sau khi ghi kết quả thì phải
cấu hình rule ở trang Cấu hình tác nghiệp — chưa chốt dùng Kết quả nào làm điều kiện.

**Bẫy đã cắn khi thêm seed:** migration này chạy trên MỌI database test, nên mọi test đếm
tuyệt đối số `OperationStage` (import workbook = 34 bước, rollback = 0 bước) đều đỏ. Đã xử lý
bằng `apps/contact_flow/care_stages.py::SEEDED_CARE_STAGE_NAMES` + xoá đúng nhóm đó ở
`setUp`/fixture của các test đếm tuyệt đối. Fixture nào tự tạo bước trùng tên seed cũng nổ
`UniqueViolation` — `OperationStage.name` là unique.
