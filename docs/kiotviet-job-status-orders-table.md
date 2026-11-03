# Hiển thị trạng thái job KiotViet trên bảng đơn (fix lỗi push không hiện)

## 1. Bối cảnh & vấn đề

Khi user bấm **Push to KiotViet** trên trang Đơn hàng (đặc biệt **trang KOC**), giao diện
chỉ hiện toast "Đã đưa vào hàng đợi KiotViet" — đây chỉ là xác nhận **enqueue Celery
thành công**, không phải kết quả xử lý. Khi Celery xử lý job và **lỗi**, lỗi không hiện
ra trên danh sách đơn.

## 2. Root cause (đã trace toàn bộ chuỗi)

Backend **không** mất lỗi:

| Tầng | Trạng thái | Vị trí |
|---|---|---|
| Task `process_kiotviet_job` bắt exception → lưu `status=FAILED` + `error_message` | ✅ | `apps/orders/infrastructure/tasks/order_tasks_impl/ingest_job_tasks.py:511-565` |
| `error_message` luôn khác rỗng (có fallback) | ✅ | `_build_error_message`, `_resolve_kv_failure_message` |
| Broadcast WebSocket `latest_kiotviet_job_error` qua group `crm_updates` | ✅ | `apps/orders/contracts/order_events.py` |
| Handler WS frontend có `toast.error` khi `status==="failed"` | ✅ | `frontend/src/App.tsx:1072-1083` |
| **Bảng `OrdersTable` KHÔNG có cột nào render job state** | ❌ | `frontend/src/components/OrdersTable.tsx` |
| Component biết vẽ "Thất bại" (`KiotVietStateCellRenderer`) là **dead code** + cột `hide:true` | ❌ | `frontend/src/features/orders/OrdersGridDesktop.tsx` (không được import), `order-grid-columns.tsx:1526` |

**Kết luận:** Trên danh sách đơn, toàn bộ vòng đời job KiotViet (running → success/failed)
là **vô hình**. Phản hồi lỗi duy nhất là một `toast.error` thoáng qua vài giây rồi mất,
lại dedup 1 lần/đơn — push hàng loạt hoặc rời trang là mất. Trạng thái thật chỉ xem được
ở trang chi tiết đơn hoặc dialog KiotViet Logs (mở thủ công).

## 3. Phạm vi fix (phương án đã chốt)

1. **Wiring lại cột trạng thái KiotViet vào `OrdersTable`** — badge bền vững theo từng dòng:
   - `running`/`pending`/`is_kiotviet_processing` → spinner "Đang xử lý"
   - `success` → ✓ "Thành công" (xanh)
   - `failed` → ⚠ "Thất bại" (đỏ) + tooltip lỗi đã humanize
   - Dữ liệu đã có sẵn trong `OrderRow` (`OrderSerializer` đã trả; realtime patch đã chạy).
2. **Toast lỗi failed sticky** — `toast.error(..., { duration: Infinity })` để không tự
   biến mất, user phải tự đóng → không bỏ lỡ lỗi.

Không động backend (đã đúng). Không thêm endpoint mới.

## 4. Thiết kế chi tiết

### 4.1. Cột mới `kiotviet_state`
- `order-types.ts`: thêm key `"kiotviet_state"` vào `OrderColumnKey`, `ORDER_COLUMN_OPTIONS`
  (label `orders.columns.kiotvietState`) và `ORDER_COLUMN_FIELD_BY_KEY`
  (`kiotviet_state: "latest_kiotviet_job_status"`).
- `OrdersTable.tsx`: thêm `case "kiotviet_state"` trong `OrderCell` render switch, tái dùng
  logic của `KiotVietStateCellRenderer` (icon Loader2/CheckCircle2/AlertTriangle +
  `humanizeKiotVietError`).
- `i18n/messages/orders.ts`: thêm label "Trạng thái KiotViet".

### 4.2. Hiển thị mặc định + migration
- Thêm `kiotviet_state` vào `DEFAULT_ORDER_COLUMNS` (user mới thấy ngay).
- Migration một lần cho user cũ (đã lưu cột trong localStorage): inject `kiotviet_state`
  vào danh sách cột đang lưu đúng **1 lần** (cờ riêng theo storage key). Sau đó tôn trọng
  lựa chọn ẩn/hiện của user.

### 4.3. Toast sticky
- `App.tsx`: với event `status==="failed"` + có error → `toast.error(msg, { duration: Infinity })`.

## 5. Sprints

- **Sprint 1 — Cột trạng thái KiotViet (frontend):** types, field map, i18n, render cell,
  default columns + migration. ✅
- **Sprint 2 — Toast sticky:** App.tsx duration Infinity cho lỗi failed. ✅
- **Sprint 3 — Test + kiểm thử:** unit test migration + render trạng thái; build typecheck. ✅

## 6. Ghi chú sau khi thực hiện

### Đã làm
- `order-types.ts`: thêm key `kiotviet_state` vào `OrderColumnKey`, `ORDER_COLUMN_OPTIONS`,
  `ORDER_COLUMN_FIELD_BY_KEY` (map sang field `latest_kiotviet_job_status`).
- `OrdersTable.tsx`: thêm `case "kiotviet_state"` trong `OrderCell` — badge spinner "Đang
  xử lý" / ✓ "Thành công" / ⚠ "Thất bại" + tooltip lỗi đã `humanizeKiotVietError`.
- `order-storage.ts`: thêm `kiotviet_state` vào `DEFAULT_ORDER_COLUMNS`; migration 1 lần
  (`applyColumnMigrations`) inject cột cho user cũ, sau đó tôn trọng lựa chọn ẩn/hiện.
- `i18n/messages/orders.ts`: label vi "Trạng thái KiotViet" / en "KiotViet status".
- `App.tsx`: toast lỗi failed → `{ id: kiotviet-error-<orderId>, duration: Infinity }` (sticky).
- Test: `order-storage.test.ts` thêm 3 case cho migration (default / inject 1 lần / tôn trọng ẩn).

### Kiểm thử
- `tsc --noEmit`: 0 lỗi. `eslint src`: 0 error. `vitest`: 140/140 pass. `vite build`: OK.

### Chưa làm / có thể mở rộng sau
- Khu "Nhật ký lỗi KiotViet" tập trung (phương án 3) — chưa làm theo phạm vi đã chốt.
- Cột áp dụng cho cả Landing/KOC/Orders qua `DEFAULT_ORDER_COLUMNS` chung; nếu muốn ẩn ở
  Landing có thể tách default theo scope sau.

## 7. Bổ sung: chỉ báo lỗi cho người thực hiện (không spam toàn bộ user)

### Vấn đề
Event `order.updated` được broadcast tới group `crm_updates` (mọi user đang login), nên
toast lỗi push KiotViet hiện cho **tất cả** user, không chỉ người bấm push.

### Giải pháp (theo pattern có sẵn của `restore.completed`)
Không tách per-user group. Giữ broadcast (để mọi người vẫn đồng bộ trạng thái dòng đơn —
dữ liệu CRM dùng chung), nhưng **kèm `requested_by_id`** trong payload và **frontend chỉ
toast khi `requested_by_id === user hiện tại`** — đúng convention `restore_completed`
(consumers.py: "FE chỉ hiện toast nếu user_id trùng").

- `contracts/order_events.py`: thêm field optional `requested_by_id` vào `OrderEvent`,
  `validate_payload` (normalize int), `build_order_updated`. `to_payload()` tự loại khi None
  (job auto-push không có người thực hiện → không toast cho ai).
- `ingest_job_tasks.py`: truyền `requested_by_id=job.requested_by_id` cho cả 3 event
  (started / finished / failed) — lưu ý `finished_event` cũng có thể mang `status=FAILED`
  qua đường `is_failed_payload`.
- `App.tsx`: tính `isKiotvietActor = requested_by_id === userRef.current?.id`; chỉ toast khi
  là actor. Trạng thái "Thất bại" vẫn được patch vào dòng cho mọi người.
- Test: `test_order_event_contract.py` thêm 2 case (mang requested_by_id / loại khi absent).

### Kiểm thử bổ sung
- Backend: `py_compile` OK (thay đổi cơ học theo pattern int field sẵn có; full test chạy ở CI).
- Frontend: `tsc` 0 lỗi, `vite build` OK.

## 8. Bổ sung 2: ẩn cả BADGE với người không thực hiện (chỉ actor thấy)

### Yêu cầu
Không chỉ toast — badge trạng thái KiotViet trên dòng đơn cũng chỉ hiện cho người thực
hiện job mới nhất; user khác không thấy gì.

### Quy tắc nhất quán
Hiện trạng thái KiotViet ⇔ **job mới nhất của đơn có `requested_by_id == user hiện tại`**.
Gate ở cả 2 nguồn dữ liệu:

1. **Backend serializer (`OrderSerializer`)** — lần load đầu của danh sách:
   - `repositories.py`: `kiotviet_job_maps` thêm `requested_by_id` vào `.values()`; 3 block
     annotation thêm `latest_kiotviet_job_requested_by` (Subquery).
   - `_view_helpers.py`: `_build_order_list_context` thêm `current_user_id` (+ TypedDict).
   - `order_core_serializers.py`: helper `_kiotviet_hidden_for_viewer` — nếu owner job mới
     nhất ≠ `current_user_id` thì 4 getter (status/action/error/is_processing) trả None/False.
     **Opt-in**: chỉ gate khi context có `current_user_id` (list views); caller khác giữ
     nguyên hành vi cũ → tránh regress. Trang chi tiết vốn không annotate nên đã trả None sẵn.
2. **Frontend realtime (`App.tsx`)** — cập nhật khi đang mở trang:
   - Khi event mang tín hiệu kiotviet: nếu là actor → patch trạng thái như cũ; nếu KHÔNG
     phải actor → xóa trạng thái kiotviet khỏi dòng (job mới nhất không thuộc về họ).

### Lưu ý ngữ nghĩa
Khi user B push lại đơn mà user A từng push: job mới nhất đổi chủ sang B → A không còn là
actor → badge của A bị ẩn/clear (đúng ý "chỉ người thực hiện job mới nhất thấy"). Job
auto-push (không có người thực hiện, `requested_by_id` null) sẽ không hiện cho ai ở list
views — hợp lý.

### Kiểm thử bổ sung 2
- Backend: thêm `test_kiotviet_state_visibility.py` (actor thấy / user khác bị ẩn / không
  truyền current_user_id thì giữ legacy). `py_compile` OK; chạy đầy đủ ở CI.
- Frontend: `tsc` 0 lỗi, `eslint` 0 error, `vitest` 140/140, `vite build` OK.
