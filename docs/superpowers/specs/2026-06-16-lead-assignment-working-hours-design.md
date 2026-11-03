# Thiết kế: Sửa & tái cấu trúc "Chia đơn tự động" (working hours + workload theo ngày)

- **Ngày:** 2026-06-16
- **Branch:** `feature/lead-assignment-working-hours`
- **Tiền đề:** `docs/superpowers/specs/2026-06-15-lead-assignment-shift-design.md` (thiết kế gốc)

## 1. Bối cảnh & vấn đề

Tính năng "Chia đơn tự động" (lead auto-assignment) hiện có 3 phần trên một trang
(`frontend/src/pages/LeadAssignmentPage.tsx`):

1. **Chia đơn tự động** — ma trận `NV × sheet`, cấu hình `% nhận đơn` theo `(ngày, ca, sheet)`.
2. **Khung giờ ca làm việc** — 3 ca (Sáng/Chiều/Tối), mỗi ca `start–end`.
3. **Tải & chuyển đơn** — bảng đơn pending theo NV trong 1 sheet + công cụ chuyển đơn.

Người dùng (CTO) báo 3 vấn đề:

1. **Khung giờ bắt buộc phủ kín 24h.** Backend validate: đủ 3 ca, ca đầu bắt đầu `00:00`,
   liền mạch (`end` ca này = `start` ca kế), ca cuối `end = 00:00`. Công ty không làm 24/7 →
   không cấu hình được. Yêu cầu: **ngoài giờ làm việc thì không chia tự động**.
2. **"Tải & chuyển đơn" không đổi theo ngày.** Đổi ngày nhưng số lượng giữ nguyên. Nguyên nhân:
   `workload(sheet)` đếm **toàn bộ** đơn pending theo sheet, không hề filter ngày (cố ý theo thiết
   kế gốc vì `Order` không lưu ca/ngày-được-gán). FE cũng chỉ refetch workload khi đổi `sheet`.
3. **Bố cục chưa hợp lý.** Cần: nửa trên = khung giờ + chuyển đơn; nửa dưới = chia đơn tự động.

## 2. Quyết định thiết kế (đã chốt với người dùng)

- **D1.** Workload & transfer **dùng chung `date`** với phần chia đơn (1 ngày, mặc định hôm nay).
  Không thêm bộ lọc ngày riêng. Lọc theo `Order.created_at::date = date`.
- **D2.** Transfer **giới hạn theo ngày đang lọc** — chỉ chuyển đơn pending tạo trong ngày đó
  (nhất quán với số hiển thị trong bảng workload).
- **D3.** **Cho phép bỏ trống ca** — ca không cấu hình giờ = ca tắt, không hoạt động.
- **D4.** Không hỗ trợ ca qua đêm kiểu `22:00 → 02:00`. Cho phép `end = 00:00` hiểu là `24:00`
  (cuối ngày). Nếu cần ca đêm thực sự thì tách thành 2 ca — ngoài phạm vi sprint này.

## 3. Thay đổi backend

### 3.1 Model `ShiftWindow` (`apps/orders/models.py`)

`start_time`, `end_time` → `null=True, blank=True`. Một ca "tắt" = không có dòng `ShiftWindow`
hoặc dòng có cả hai trường null. Chuẩn hóa: **ca tắt thì không lưu dòng** (xóa nếu có) để
`shift_for_time` đơn giản. Cần migration.

### 3.2 `ShiftService` (`apps/orders/application/services/shift_service.py`)

- `_windows()` cache đổi thành `list[tuple[start: time, end: time, shift: str]]`, chỉ gồm các ca
  đã cấu hình (có đủ start & end).
- `shift_for_time(t) -> str | None`: trả ca **đầu tiên** thỏa `start ≤ t < end_norm`, với
  `end_norm = 24:00 nếu end == 00:00 else end`. Không khớp ca nào → `None`. Không còn fallback
  `morning`, không còn logic wrap.
- `current_shift() -> str | None`.

### 3.3 `LeadAssignmentService` (`.../lead_assignment_service.py`)

- `assign_if_unassigned(order)`: nếu `ShiftService.current_shift()` là `None` → **return** (không
  chia tự động, để trống staff). Log mức INFO "ngoài giờ làm việc, bỏ qua".
- `_pending_base(sheet, date=None)`: nếu `date` không null → thêm `.filter(created_at__date=date)`.
- `workload(sheet, date)`: bắt buộc truyền `date`, đếm pending theo NV **trong ngày đó**.
- `transfer(sheet, from_user_id, to_user_id, count, date)`: chỉ chọn đơn pending của `from_user`
  **tạo trong `date`** (thêm filter vào nhánh `_pending_base`).

### 3.4 Views (`.../lead_assignment_view.py`)

- `_ShiftWindowRow`: `start_time`/`end_time` cho phép `required=False, allow_null=True`.
- `validate_windows` — luật mới:
  - Mỗi ca: hoặc **cả hai trường null** (tắt), hoặc **đủ cả hai** (bật).
  - Ca bật: `start < end_norm` (với `end == 00:00` ⇒ `24:00`).
  - Các ca bật **không chồng lấn** (sort theo start, `end_norm[i] ≤ start[i+1]`).
  - Bỏ luật "đủ 3 ca / bắt đầu 00:00 / phủ kín 24h".
- Lưu: ca tắt → `ShiftWindow.objects.filter(shift=...).delete()`; ca bật → `update_or_create`.
- `_serialize_windows`: trả `start_time/end_time = None` cho ca tắt; `current_shift` có thể `None`.
- `lead_assignment_shares` (GET): `shift = qp.shift or current_shift() or SHIFT_ORDER[0]`
  (current_shift có thể None ngoài giờ → mặc định ca đầu để hiển thị).
- `lead_assignment_workload` (GET): nhận thêm `?date=` (mặc định `localdate()`), truyền vào service.
- `_TransferSerializer`: thêm field `date = DateField(required=False)` (default `localdate()`),
  truyền vào `transfer`.

## 4. Thay đổi frontend (`LeadAssignmentPage.tsx` + `lib/api/lead-assignment.ts`)

### 4.1 API client
- `getWorkload(sheet, date)` → `GET /workload?sheet=&date=`.
- `transferOrders({ sheet, from_user_id, to_user_id, count, date })`.

### 4.2 Trang — bố cục mới
- **Header:** tiêu đề "Chia đơn tự động" + **1 date picker dùng chung** (state `date`).
- **Nửa trên (`grid md:grid-cols-2`, mobile xếp dọc theo chuẩn responsive):**
  - Cột trái: **Khung giờ làm việc** — 3 ca, mỗi ca `start–end` có thể để trống; mô tả mới
    ("để trống = ca không hoạt động, đơn vào ngoài giờ sẽ không tự chia"). Nút "Lưu khung giờ".
  - Cột phải: **Tải & chuyển đơn** — selector sheet, bảng đơn chờ **theo `date`**, công cụ chuyển.
- **Nửa dưới:** **Phân bổ %** — selector ca (Sáng/Chiều/Tối) + badge "Ca hiện tại"
  (hoặc "Ngoài giờ làm việc" nếu `current_shift == null`) + ma trận + Thêm NV/Lưu.
- `useEffect` workload đổi dependency `[tSheet]` → `[tSheet, date]`.
- Khi `currentShift` null: không auto-set ca; mặc định giữ ca đang chọn (khởi tạo `morning`).

## 5. Ranh giới & rủi ro

- **N+1 / index:** `created_at__date` dùng được index `order_created_at_idx`. workload group-by
  `staff_id` vẫn 1 query. Không phát sinh N+1.
- **Cache đa-process của `ShiftService`:** giữ TTL 60s + `invalidate_cache()` như cũ.
- **Tương thích dữ liệu cũ:** ShiftWindow hiện có 3 dòng phủ 24h vẫn hợp lệ theo luật mới
  (không chồng lấn, end=00:00 hiểu là 24:00). Không cần backfill.
- **Callers của `current_shift()`/`workload()`:** rà toàn repo, cập nhật mọi nơi giả định trả `str`.

## 6. Kế hoạch sprint

### Sprint 1 — Backend: khung giờ linh hoạt + ngoài giờ không chia (TDD)
- [ ] Migration `ShiftWindow.start_time/end_time` nullable.
- [ ] `ShiftService.shift_for_time/current_shift` trả `Optional`; bỏ fallback & wrap. + test.
- [ ] `assign_if_unassigned` bỏ qua khi ngoài giờ. + test.
- [ ] `validate_windows` luật mới (tắt ca, không chồng lấn, không bắt 24h). + test API.
- [ ] Cập nhật `_serialize_windows`, `lead_assignment_shares` GET default shift.

### Sprint 2 — Backend: workload & transfer theo ngày (TDD)
- [ ] `_pending_base(sheet, date)`, `workload(sheet, date)`, `transfer(..., date)`. + test.
- [ ] `lead_assignment_workload` nhận `?date=`; `_TransferSerializer` thêm `date`.

### Sprint 3 — Frontend: API client + bố cục mới
- [ ] `getWorkload(sheet, date)`, `transferOrders(..., date)` + test client.
- [ ] Tái cấu trúc `LeadAssignmentPage`: header date dùng chung, nửa trên (khung giờ + chuyển đơn),
      nửa dưới (ma trận). Responsive theo chuẩn.
- [ ] workload refetch theo `[tSheet, date]`; xử lý `currentShift == null`.

### Sprint 4 — Kiểm thử & hoàn tất
- [ ] Chạy test BE (ephemeral docker) + FE; sửa test cũ bị ảnh hưởng.
- [ ] Cập nhật mục "đã làm / chưa làm" cuối tài liệu; push + gửi link MR.

## 7. Nhật ký thực hiện

**2026-06-16 — hoàn tất Sprint 1→4:**
- **BE:** `ShiftWindow.start_time/end_time` nullable (migration `0087`). `ShiftService` trả
  `Optional[str]`, membership `[start, end)` (end=00:00 ⇒ 24:00), bỏ wrap & fallback.
  `assign_if_unassigned` bỏ qua khi ngoài giờ. `validate_windows` luật mới (tắt ca / không
  chồng lấn / không bắt 24h). `workload(sheet, date)` & `transfer(..., date)` lọc theo
  `created_at::date`. API `workload` nhận `?date=`, transfer nhận `date` trong body, GET shares
  default ca đầu khi ngoài giờ.
- **FE:** API client `getWorkload(sheet, date)` + `transferOrders(..., date)`, `current_shift`
  nullable, `putShiftWindows` cho phép null. Tái cấu trúc `LeadAssignmentPage`: header date dùng
  chung; nửa trên = khung giờ + tải/chuyển đơn (2 cột, responsive); nửa dưới = ma trận phân bổ %.
  Badge "Hiện đang ngoài giờ làm việc" khi `current_shift == null`.
- **Test:** BE 35 pass (gồm `test_workload_filters_by_date`, `test_skip_assign_outside_working_hours`,
  test tắt ca / chồng lấn / cho phép gap). FE client test 4 pass. tsc + eslint sạch.
- **Đã làm:** toàn bộ phạm vi spec. **Chưa làm:** chạy migration `0087` trên prod (để bước deploy);
  chưa thêm test render component cho layout mới (chỉ test API client).
