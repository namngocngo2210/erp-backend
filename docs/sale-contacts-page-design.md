# Trang "Danh sách contact" (mục Sale) — Thiết kế

Ngày: 2026-07-15 · Branch: `feat/sale-contacts-page` · Trạng thái: **CHỜ REVIEW SPEC**

## 1. Mục tiêu

Trang mới trong mục Sale, trục hiển thị là **khách hàng (contact)** thay vì đơn hàng:
1 dòng = 1 khách, gộp từ các đơn đổ về luồng sale landing. Sale nhìn vào biết ngay
khách nào cần tác nghiệp gì, thao tác (gọi, ghi kết quả, ghi chú) ngay trên dòng,
và xem được lịch sử liên hệ đầy đủ của khách.

Tham chiếu UI: ảnh CRM mẫu (chips đếm theo tác nghiệp, bảng contact, dropdown Kết
quả inline, icon lịch sử cạnh badge tác nghiệp).

## 2. Quyết định kiến trúc (đã chốt)

**Hybrid — list là view tổng hợp, lịch sử là log append-only:**

- **KHÔNG** tạo bảng state materialized cho contact. Danh sách contact = query
  GROUP theo `customer_id` trên bảng `orders` (đơn mới nhất mỗi khách qua
  `DISTINCT ON`), join `order_assessments`. Tránh hoàn toàn bài toán drift đã
  gặp với các trường denormalize (`is_new_customer` phải vá signal+cron).
- **"Kết quả" ghi vào assessment của ĐƠN MỚI NHẤT** của khách — tái dùng nguyên
  engine hiện có: PATCH assessment, `SaleReminder` reschedule, báo cáo sale.
  Trang đơn hàng và trang contact luôn thấy CÙNG một trạng thái.
- **`ContactActivity`** — bảng log append-only (không phải state) cho lịch sử
  liên hệ. Phạm vi log (phương án C, user đã chốt):
  1. Thay đổi Kết quả / trạng thái sale (tự động, tại choke point backend).
  2. Cuộc gọi Zenify (gọi đi / kết quả nghe máy / thời lượng).
  3. Ghi chú tay tự do (sale bấm "+ ghi chú" không cần đổi trạng thái).

### Assumption đã chốt mặc định (user không phản đối khi review đề xuất)

| # | Assumption |
|---|---|
| A1 | Phạm vi contact: chỉ khách từ luồng sale landing (`order_source` ∉ {shopee, tiktok, koc} — dùng lại `EXCLUDED_MARKETPLACE_SOURCES` + loại koc như các trang Landing/Sheet). |
| A2 | "Kết quả" ghi vào assessment đơn mới nhất của khách. |
| A3 | Nhãn "Tác nghiệp cần" derive bằng rule cứng từ `sales_status_code` + trạng thái đơn (bảng mapping §5). Chip hiển thị `x/y` = *chưa tương tác / tổng* trong bộ lọc. |
| A4 | Quick actions phase 1: Gọi (Zenify), sửa Kết quả inline, xem lịch sử liên hệ, "+ ghi chú", xem danh sách đơn của khách. **Thêm mới / Tạo lịch hẹn / Xóa contacts / Thu hồi contact** → phase 2. |

## 3. Backend

Đặt trong `apps/orders` (cùng domain với lead assignment, sheet rules), theo layering
hiện có: `transport/views` → `application/services` → models.

### 3.1. Model mới: `ContactActivity`

```
contact_activities
- id
- customer        FK customers (CASCADE) — trục chính của timeline
- order           FK orders (SET_NULL, null=True) — đơn ngữ cảnh (nếu có)
- staff           FK users (SET_NULL, null=True) — người thực hiện
- kind            CharField choices: call | note   (KHÔNG có status_change — xem §3.2)
- note            TextField blank — ghi chú tay
- call_meta       JSONField null — kind=call: {direction, answered, duration_s, zenify_call_id}
- created_at      auto_now_add

Index: (customer, -created_at), (staff, created_at)
```

Append-only: không có endpoint sửa/xóa activity.

### 3.2. Điểm ghi log

| Nguồn | Cách ghi |
|---|---|
| Đổi Kết quả / trạng thái sale | **Không ghi vào `ContactActivity`.** Phát hiện khi implement S2: `_upsert_order_assessment` đã ghi `OrderJourneyEvent` sẵn (`event_type=assessment_created` khi tạo assessment lần đầu, `sales_status_changed` khi `sales_status_code` đổi) — đây là choke point duy nhất mọi luồng PATCH assessment đi qua từ trước. Timeline API đọc UNION 2 nguồn thay vì hook ghi thêm 1 bản sao → bỏ hẳn nhánh `kind=status_change` và management command backfill đã dự tính ban đầu. |
| Cuộc gọi Zenify | FE bridge (WebphoneProvider đã có event lifecycle) POST `/api/sale/contacts/{customer_id}/activities` kind=call khi cuộc gọi kết thúc. |
| Ghi chú tay | POST cùng endpoint, kind=note. |

### 3.3. API

Tất cả trong namespace `/api/sale/contacts`, section RBAC mới **`contacts`**
(scope OWN = chỉ contact có đơn mới nhất do mình phụ trách — theo pattern
`scope_for`; lưu ý gotcha mặc định OWN cho section chưa cấp).

| Endpoint | Mô tả |
|---|---|
| `GET /api/sale/contacts` | List contact phân trang. Params: `date_from/date_to` (ngày data về — created_at đơn mới nhất), `q` (tên/SĐT — dùng GIN trgm sẵn có trên customers), `channel[]`, `order_source[]`, `staff[]`, `action_needed[]` (khóa chip), `interaction` (none/recent), `customer_type` (new/old), `limit/offset`. Response: `{items, total, action_counts}`. |
| `GET /api/sale/contacts/{customer_id}/activities` | Timeline lịch sử liên hệ (phân trang, mới nhất trước). Params: `limit` (mặc định 20, tối đa 100), `offset`. Response: `{items, total}`. |
| `POST /api/sale/contacts/{customer_id}/activities` | Ghi activity `kind=note` hoặc `kind=call` (whitelist 2 kind này; `status_change` không phải giá trị hợp lệ — timeline đọc thẳng từ `OrderJourneyEvent`, không có gì để POST). Body: `{kind, note?, order_id?, call_meta?}`. Trả 201 + item vừa tạo. |

Ghi "Kết quả": **dùng lại PATCH assessment hiện có** trên đơn mới nhất (FE đã có
sẵn luồng này ở trang đơn) — không endpoint ghi mới.

Scope OWN cho 2 endpoint activities: 404 (không lộ tồn tại) nếu user không phụ
trách (`staff_id`) đơn landing nào của khách đó — cùng danh sách loại trừ nguồn
sàn/koc với `ContactListService`.

Shape 1 item danh sách contact:

```
{
  customer_id, customer_code, full_name, phone_raw, phone_carrier,
  is_new_customer,
  latest_order: { id, created_at, order_source, channel_name, status,
                  sales_status_code, sales_status_label, next_followup_at,
                  staff_id, staff_name },
  order_count, delivered_count,
  action_needed,            // khóa nhãn tác nghiệp (§5)
  waiting_since,            // mốc tính "thời gian chờ" (ISO) hoặc null
  last_activity_at          // activity gần nhất (mọi kind) hoặc null
}
```

Shape 1 item timeline activities (UNION `OrderJourneyEvent` + `ContactActivity`,
merge-sort `at` DESC trong Python sau khi mỗi nguồn fetch thô `offset+limit` dòng;
`total` = tổng count 2 nguồn):

```
{
  type,          // "status_change" | "call" | "note"
  at,            // ISO datetime
  actor_name,    // "" nếu không có actor
  note,
  from_code, to_code,        // null trừ type=status_change
  from_label, to_label,      // SALES_STATUS_LABELS[code] fallback code thô; null trừ type=status_change
  order_id,      // null nếu activity không gắn đơn cụ thể
  call_meta      // dict hoặc null; chỉ có giá trị khi type=call
}
```

### 3.4. Hiệu năng

- Đơn mới nhất mỗi khách: `DISTINCT ON (customer_id) ORDER BY customer_id, created_at DESC`
  trên tập đơn đã lọc nguồn + khoảng ngày. Materialize ids ra Python list trước khi
  query trang (bài học order search perf — tránh lazy union subquery seq-scan).
- `action_counts` tính trong 1 query aggregate riêng trên cùng tập lọc (pattern
  `status_counts` của trang đơn).
- Index hiện có đủ (`orders.customer`, `orders.created_at`, GIN phone). Sau khi code
  xong chạy EXPLAIN trên DB thật, chỉ thêm index nếu có bằng chứng.

## 4. Frontend

Theo convention bắt buộc: `features/contacts/` mới.

```
frontend/src/features/contacts/
  pages/ContactsPage.tsx        — trang /sale/contacts
  queries.ts                    — React Query keys + hooks
  contact-actions.ts            — map khóa action_needed → nhãn/màu badge
  components/
    ContactsTable.tsx           — bảng chính
    ContactActionChips.tsx      — dải chip đếm x/y
    ContactHistoryPopover.tsx   — timeline lịch sử (icon cạnh badge tác nghiệp)
    ContactNoteDialog.tsx       — dialog "+ ghi chú" (FormDialogShell)
frontend/src/lib/api/contacts.ts — typed từ schema.d.ts (regen gen:api + prettier)
```

- Cột: Khách hàng · SĐT (+nhà mạng, reuse carrier label) · Thao tác nhanh (gọi
  Zenify, + ghi chú) · Nguồn khách (channel/landing) · Ngày data về · Loại khách
  (badge Khách mới/cũ — reuse) · **Tác nghiệp cần (badge + icon lịch sử → popover
  timeline)** · Kết quả (searchable-select inline, options từ CRM catalog) ·
  Thời gian chờ · Thao tác (mở chi tiết khách + đơn).
- Chips đầu trang: Tất cả + từng nhãn tác nghiệp, đếm `x/y` từ `action_counts`.
- Bộ lọc: khoảng ngày data về, search tên/SĐT, nguồn/channel, chưa/mới tương tác.
- Phân trang: `PaginationPages` (numbered) + chọn page size.
- Sidebar: thêm entry vào zone Sale trong `nav-layout.ts`, gate quyền `contacts:view`.
- KHÔNG dùng `react-select`; GET qua `useQuery`, mutation qua `useMutation` + invalidate
  (riêng sửa Kết quả: vá row tại chỗ setQueryData như pattern resale, tránh refetch bảng).

## 5. Rule "Tác nghiệp cần" (derive từ đơn mới nhất)

| Điều kiện (đơn mới nhất của khách) | Nhãn | Khóa |
|---|---|---|
| Chưa có assessment / `sales_status_code` rỗng, khách mới | KH mới | `new_customer` |
| Chưa có assessment / rỗng, khách cũ | Lead mới | `new_lead` |
| `no_answer_1` | Gọi lần 2 | `call_2` |
| `no_answer_2` | Gọi lần 3 | `call_3` |
| `no_answer_3` | Gọi lần 4 | `call_4` |
| `no_answer_4` | Gọi lần 5 | `call_5` |
| `no_answer_5` | Gọi lần 6 | `call_6` |
| `no_answer_6` / `unreachable` / `wrong_number` | Check lại số | `recheck_phone` |
| `need_more_consulting` | Chăm sóc thêm | `nurture` |
| `scheduled` / `call_back_later` / `closed_scheduled_date` | Gọi theo hẹn (due = `next_followup_at`) | `follow_up` |
| `closed` / `closed_express` / `created_order`, đơn chưa giao xong | Chờ giao | `awaiting_delivery` |
| Đơn `status = 3` (giao thành công) | Chăm sóc sau bán | `post_sale` |
| `not_buying` / `underlying_condition` / `spam` / `duplicate` / `customer_bombed` / `cancelled` | (không tác nghiệp — ẩn khỏi chips) | `none` |

- Rule đặt trong module thuần `apps/orders/domain/contact_rules.py` — dễ unit test,
  đổi mapping chỉ sửa 1 chỗ. Nhãn hiển thị đặt ở FE (`contact-actions.ts`).
- Chip `x/y`: `y` = tổng contact thuộc nhóm trong bộ lọc; `x` = số contact trong nhóm
  **chưa có activity nào kể từ khi đơn mới nhất đổ về** ("chưa tương tác").
- "Thời gian chờ" = now − max(created_at đơn mới nhất, activity cuối) khi còn thuộc
  nhóm cần tác nghiệp; nhóm `none` để trống.

## 6. Testing

- BE: unit test `contact_rules` (mọi nhánh mapping); API test list (lọc, counts,
  RBAC OWN/ALL, loại nguồn sàn/koc); API test activities (timeline UNION
  `OrderJourneyEvent` + `ContactActivity` từ luồng `update_order_inline` thật,
  merge-sort, total, RBAC OWN/ALL); test POST note/call + validate (kind lạ,
  note rỗng/quá dài, order_id sai khách).
- FE: test derive nhãn + chips render counts; test popover timeline; giữ nguyên
  baseline fail đã biết, không thêm file vào allowlist eslint.

## 7. Sprint & tiến độ

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| S1 | BE: `ContactActivity` model + migration, `contact_rules`, endpoint list + counts + RBAC section `contacts`, tests | ✅ Xong |
| S2 | BE: activities GET/POST (`ContactActivityService`), schema.yaml + gen:api | ✅ Xong |
| S3 | FE: trang contacts (bảng, chips, bộ lọc, phân trang), Kết quả inline, sidebar + quyền | ✅ Xong |
| S4 | FE: popover lịch sử, + ghi chú, log cuộc gọi Zenify từ WebphoneProvider; QA + polish | ✅ Xong |

**Trạng thái tổng:** toàn bộ code + test xong trên branch `feat/sale-contacts-page`
(đã merge master mới nhất, không conflict). BE 70/70 test contacts; FE full suite
500/500; lint + tsc sạch. Whole-branch review: READY TO MERGE (0 Critical/Important).
Chờ tạo MR + smoke test prod (đặc biệt nút gọi Zenify — cần webphone thật).

Phase 2 (ngoài phạm vi lần này): Thêm contact thủ công, Tạo lịch hẹn theo contact,
Xóa/Thu hồi contact (reuse `lead-assignment/transfer`), preset "Xem kiểu 1/2/3".

## 8. Ghi chú sau mỗi sprint

- **S2 (Task 6):** Khi implement hook ghi `status_change` dự tính ban đầu (§3.2
  bản gốc), phát hiện `OrderJourneyEvent` đã ghi sẵn đúng dữ liệu cần
  (`event_type=assessment_created|sales_status_changed`) tại choke point
  `_upsert_order_assessment` — mọi luồng PATCH assessment (kể cả `create_order`,
  import, cron) đều đi qua đây từ trước, không phải code mới. Quyết định: bỏ
  hẳn `kind=status_change` khỏi `ContactActivity` (model đã giữ đúng 2 kind
  call/note ngay từ S1) và bỏ management command backfill từ
  `SaleReminder.sales_status_code_snapshot` — timeline API đọc UNION 2 nguồn
  trực tiếp thay vì duplicate dữ liệu. Hệ quả phụ: 1 lần lưu assessment đầu
  tiên có `sales_status_code` sẽ sinh **2** journey event
  (`assessment_created` + `sales_status_changed`) vì field này vừa là field
  "mới tạo" vừa nằm trong danh sách field theo dõi đổi — timeline sẽ hiện 2
  dòng "status_change" liền nhau cho lần chốt Kết quả đầu tiên; chấp nhận, vì
  đúng phản ánh 2 sự kiện đã xảy ra ở tầng dữ liệu, không phải bug hiển thị.

- **S3/S4 (Task 10 — Kết quả inline + nút gọi Zenify + log cuộc gọi):**
  `WebphoneProvider` (đã merge, headless SDK v2) expose đủ để dùng vòng đời
  cuộc gọi THẬT thay vì fallback "log ngay khi bấm gọi": `useWebphone()` trả
  `callState` (`idle|dialing|ringing|in-call`) + `currentCall.startedAt` (set ở
  event `answer`). `ContactsPage` tự dial qua `dialCall(phone_raw)`, nhớ
  `{customerId, dialedAt}` vào ref, rồi `useEffect` theo dõi `callState` bằng
  cặp ref "trạng thái trước đó": khi `callState` chuyển VỀ `idle` từ một trạng
  thái khác VÀ ref còn cuộc gọi đang theo dõi → cuộc gọi vừa kết thúc (mọi
  nhánh outcome — bắt máy xong cúp, không nghe, lỗi outcall — reducer
  `webphoneCallReducer` đều reset về `idle`, xem `webphone-call-state.ts`).
  `answered` suy từ status ngay trước khi về idle (`"in-call"` = đã bắt máy);
  `duration_s` tính từ `currentCall.startedAt` nếu có (mốc bắt máy thật, chính
  xác hơn), fallback về `dialedAt` của ref nếu thiếu. Activity `kind=call`
  ghi ngay sau đó với `call_meta: {direction: "outbound", duration_s,
  answered}`. KHÔNG dùng fallback "log tức thì không duration" vì provider đủ
  dữ liệu. Giới hạn đã biết: ref theo dõi cuộc gọi sống ở `ContactsPage`
  (không phải `WebphoneProvider` tầng shell) — nếu user điều hướng khỏi trang
  Contacts giữa cuộc gọi đang dial từ đây, activity kết thúc cuộc gọi đó sẽ
  KHÔNG được ghi (ref bị unmount cùng trang). Chấp nhận cho phạm vi task này;
  nếu cần bền hơn, chuyển việc theo dõi "cuộc gọi do trang nào khởi tạo" lên
  `WebphoneProvider` ở Phase 2.

  *Fix round 1 (review):* log giờ BUỘC khớp danh tính cuộc gọi đã dial —
  quyết định tách ra hàm thuần `evaluateTrackedCallEnd`
  (`contact-call-log.ts`, có unit test): chỉ log khi cuộc vừa kết thúc có
  `direction === "out"` VÀ SĐT (đã sanitize) trùng cuộc đã dial. Chọn key =
  phone-sau-sanitize thay vì `callId` vì reducer chỉ có callId sau event
  `ring` outbound (SDK có thể không echo), còn phone có ngay từ action
  `dial`. Ref clear ở MỌI transition về idle (cuộc gọi đến chen ngang đã thay
  thế state cuộc mình dial → giữ ref chỉ gây log nhầm cuộc sau). `handleCall`
  sanitize + check sẵn sàng TRƯỚC khi arm ref (dialCall no-op không được để
  ref treo). Nút gọi disable thêm khi `callState !== "idle"`.

- **Đóng branch (merge master + final review):** đã merge `origin/master` mới nhất
  (gồm HR move departments) — không conflict; chỉ chỉnh 2 assertion test
  `routes.test.ts` do master đổi (19→20 legacy redirect + thêm module `hr` vào
  regex). Whole-branch review: 0 Critical/0 Important, READY TO MERGE. Sửa nốt 2
  Minor của review: (1) `action_counts` trên chip nay tôn trọng filter
  `interaction`/`customer_type` (vẫn KHÔNG áp `action_needed` để chip switch được)
  — khớp §5; (2) thêm tiebreaker `(at, type, order_id)` cho merge sort timeline.
  BE 70/70, FE 500/500, lint+tsc sạch.

## 9. Xuất Excel danh sách contact (2026-08-19)

Bổ sung sau khi trang đã chạy ổn định. Không dựng đường xuất riêng: tái dùng engine
export chạy nền `apps.exports` (job + `ExportDock`) như các trang Đơn hàng / Kho /
Bảo hành.

**Quyền `contacts:export` (mới).** Tách khỏi `contacts:view`: file xuất là toàn bộ
danh bạ kèm SĐT trong scope người xuất — đúng thứ mục "tài sản cạnh tranh giữa sale"
đã ghi ở `audit_rbac_scope`. Migration `users/0038` chỉ seed quyền, **cố ý không gán
cho role nào**; sau deploy admin phải cấp tay thì nút Xuất Excel mới hiện.

**Một đường lọc duy nhất.** Phần dựng + lọc tập contact tách thành
`ContactListService._resolve_rows()`; `list_contacts()` (màn hình) và
`count_contacts()` / `iter_contact_batches()` (job export) đều gọi nó. Lý do: 11 trục
lọc, trong đó 4 trục lọc trong Python sau query — nhân đôi đường lọc là cách chắc
chắn nhất để file xuất lệch màn hình khi thêm trục mới (bài học "4 dict filter" của
trang Đơn). `ContactsExport.validate()` nhận ĐÚNG tên query param của list endpoint
(`channel`/`order_source`/`staff`/`product`) để frontend gửi thẳng bộ lọc đang áp,
không phải dịch sang bộ tên thứ hai.

**Trần 20.000 dòng** (`CONTACT_EXPORT_MAX_ROWS`), thấp hơn hẳn 50.000 của trang Đơn:
pipeline contact materialize toàn bộ dòng ra Python để lọc, cùng số dòng thì tốn RAM
worker hơn nhiều so với `queryset.iterator()` của Orders. Vượt trần bị chặn ngay ở
`estimate()` (trong request), chưa tốn slot worker.

**Bộ cột** cố định 13 cột, mirror bảng trên màn hình (bỏ "#" và "Thao tác nhanh").
Ba khác biệt có chủ đích: cột SKU đổ đủ dòng hàng (màn hình chỉ hiện dòng đầu +
badge "+n"); "Giá đơn"/"SL đơn" ghi dạng số để Excel cộng được; "Tác nghiệp" gộp tên
bước + giờ hẹn + nhãn bucket vào một ô. Nhãn tiếng Việt của `action_needed` và bucket
phải có bản backend (`ACTION_LABELS` ở `domain/contact_rules.py`,
`_BUCKET_LABELS` ở `contact_export_excel.py`) vì file do worker sinh, không đi qua
frontend — đổi nhãn phải sửa cả hai chỗ.

**Còn nợ sau deploy:** (1) cấp `contacts:export` cho role sale/CSKH cần dùng;
(2) đo thời gian `estimate()` trên prod với cửa sổ ngày rộng — nó chạy đúng pipeline
của một lần tải trang, nhưng chưa có số đo thật ở tập lớn nhất.

### 9.1. Tích chọn dòng để xuất (19/08/2026)

Thêm cột checkbox đầu bảng + checkbox "chọn cả trang" ở header. Có dòng đã tích thì nút
Xuất Excel đổi nhãn thành "Xuất Excel (N)" và gửi kèm `customer_ids`.

- **`customer_ids` là MỘT TRỤC LỌC NỮA**, giao với `filters` chứ không thay thế. Đi qua
  đúng `ContactListService._resolve_rows` nên record-scope RBAC + cửa sổ ngày vẫn áp
  nguyên: khách bị tích nhưng ngoài quyền người xuất **im lặng rớt khỏi file**. Mở một
  đường lấy dữ liệu thứ hai theo id là đúng chỗ rò dữ liệu ngoài scope.
- **Selection giữ qua các trang, reset khi đổi bộ lọc.** Giữ qua trang để gom khách rồi
  xuất một lần; reset khi đổi lọc vì dòng đã tích có thể không còn trong tập kết quả —
  giữ lại là xuất ra file những khách người dùng không còn nhìn thấy. `filterKey` không
  chứa số trang nên lật trang không kích hoạt reset.
- **Không có nút "chọn toàn bộ N kết quả"** — checkbox header chỉ tích dòng đang hiển thị.
  Muốn xuất tất cả thì bỏ tích hết rồi bấm Xuất Excel (đúng hành vi xuất-theo-bộ-lọc).
