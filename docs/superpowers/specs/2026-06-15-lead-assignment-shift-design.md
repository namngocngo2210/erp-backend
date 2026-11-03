# Chia đơn tự động theo Ca làm việc (Lead Assignment by Shift)

- **Ngày:** 2026-06-15
- **Branch:** `feat/lead-assignment-shift`
- **Trạng thái:** Đã triển khai (2026-06-15)
- **Liên quan:** Mở rộng tính năng chia đơn tự động hiện có (`LeadAssignmentShare`, `LeadAssignmentService`, `LeadAssignmentPage`).

## 1. Bối cảnh & Vấn đề

Hệ thống đã có chia đơn tự động (lead auto-assignment) theo `(ngày, sheet)`:

- Bảng `lead_assignment_shares`: mỗi dòng = `(date, sheet, user, weight, assigned_count)`.
- Khi đơn mới được tạo (landing ingest hoặc tạo tay) → `classify_sheet()` xác định sheet1/2/3
  → `LeadAssignmentService.pick_staff_for(date, sheet)` chọn nhân viên theo thuật toán
  **largest-remainder** (cân theo trọng số `weight`), tăng `assigned_count`.
- UI cấu hình ma trận `NV × sheet` theo ngày tại `/settings/lead-assignment`.

**Yêu cầu mới:** Công ty có 3 ca làm việc — **Sáng / Chiều / Tối**. Cần chia đơn đúng theo ca:
mỗi ca có thể có nhóm nhân viên trực và tỉ lệ nhận đơn riêng. Đơn về trong ca nào thì chia
theo cấu hình của ca đó.

## 2. Quyết định thiết kế (đã chốt với CTO)

| # | Quyết định | Lựa chọn |
|---|-----------|----------|
| D1 | Cách xác định ca của một đơn | **Theo ca đang trực hiện tại** — dùng `timezone.localtime()` lúc auto-assign, dò xem rơi vào khung giờ ca nào. |
| D2 | Khung giờ 3 ca | **Cấu hình được trong UI** (không hardcode), lưu ở bảng global `shift_windows`. |
| D3 | Cấu hình trọng số theo ca | **Ma trận riêng cho từng ca** — `assigned_count` cân bằng độc lập theo từng `(ngày, ca, sheet)`. |
| D4 | Phạm vi cấu hình | **Vẫn theo từng ngày** như hiện tại, chỉ thêm chiều `shift`. |
| D5 | Phạm vi `workload`/`transfer` | **Giữ ở mức sheet (xuyên ca)** — KHÔNG thêm cột `shift` vào Order. Lý do bên dưới. |

### Lý do D5 (quan trọng)

`workload` = số đơn đang chờ (chưa có trạng thái sale) theo từng nhân viên; `transfer` = chuyển
đơn pending giữa nhân viên. Cả hai thao tác trên **Order thực tế**. Order **không lưu** ca được
gán lúc tạo. Một đơn đã gán thì nhân viên xử lý nó bất kể ca — "ca" chỉ có ý nghĩa tại thời điểm
chia. Vì vậy phân rã workload/transfer theo ca sẽ buộc phải thêm cột `assigned_shift` vào Order
(mở rộng phạm vi, migrate bảng lớn) mà không mang lại giá trị vận hành rõ ràng. → Giữ
workload/transfer ở mức sheet như hiện tại. Chỉ **cấu hình + bộ đếm cân bằng** là theo ca.

## 3. Mô hình Ca & Khung giờ

3 ca cố định (mã + nhãn):

```python
SHIFT_CHOICES = [("morning", "Sáng"), ("afternoon", "Chiều"), ("evening", "Tối")]
```

### Bảng mới `shift_windows` (global, không theo ngày)

| cột | kiểu | ghi chú |
|-----|------|---------|
| shift | varchar(16), PK/unique | một trong morning/afternoon/evening |
| start_time | time | giờ bắt đầu ca (local) |
| end_time | time | giờ kết thúc ca (local) |
| updated_at | timestamptz | |

- Đúng **3 dòng cố định**. Admin chỉ sửa `start_time`/`end_time`.
- Giá trị mặc định khi seed: Sáng `start=00:00 end=12:00`, Chiều `start=12:00 end=18:00`,
  Tối `start=18:00 end=00:00` (`end_time = 00:00` của ca tối nghĩa là nửa đêm/24:00).

### Ràng buộc phủ kín 24h

3 ca phải **liền mạch, phủ kín 24h**: sắp theo `start_time` (ca đầu phải bắt đầu `00:00`), ca sau
bắt đầu đúng lúc ca trước kết thúc; ca cuối có `end_time = 00:00` (nửa đêm). Validate ở serializer
khi PUT (chặn khe hở/chồng lấn) → đảm bảo mọi thời điểm rơi đúng 1 ca duy nhất.

### Hàm `current_shift()` / `shift_for_time(t)`

- Đặt trong service mới `ShiftService` (`apps/orders/application/services/shift_service.py`).
- Logic (không cần xử lý `end_time`, không cần cờ phụ): lấy `now = timezone.localtime().time()`,
  sắp 3 ca theo `start_time` tăng dần, chọn ca có `start_time` **lớn nhất mà ≤ now**. Nhờ ràng
  buộc phủ-kín-24h bắt đầu từ `00:00`, mọi `now` luôn match đúng 1 ca; ca tối tự nhiên kéo dài tới
  nửa đêm. Biên: `12:00` thuộc ca chiều, `18:00` thuộc ca tối (nửa-mở `[start, next_start)`).
- Có cache nhẹ in-process cho 3 dòng `shift_windows` (TTL ngắn ~30s) — đọc rất thường xuyên lúc
  ingest. Cache invalidation: PUT shift-windows xoá cache.
- Nếu vì lý do nào đó không match (cấu hình lỗi/chưa seed) → fallback `morning` + log warning.

## 4. Data Model — `LeadAssignmentShare`

Thêm chiều `shift`:

```python
shift = models.CharField(max_length=16, choices=SHIFT_CHOICES)   # cột mới
# Meta:
unique_together = ("date", "shift", "sheet", "user")
indexes = [Index(fields=["date", "shift", "sheet"], name="lead_assign_d_sh_sheet_idx")]
```

`assigned_count` giữ nguyên trên mỗi dòng → largest-remainder chạy độc lập trong từng
`(ngày, ca, sheet)`.

### Migration (an toàn, không gãy chia đơn ngày deploy)

1. Tạo bảng `shift_windows`, seed 3 dòng mặc định (mục 3).
2. Thêm cột `shift` vào `lead_assignment_shares`:
   - Bước a: thêm cột nullable.
   - Bước b (data migration): với mỗi dòng cũ, **nhân thành 3 ca** cùng `weight`;
     `assigned_count` của dòng cũ giữ ở ca `morning`, 2 dòng nhân bản (afternoon/evening)
     đặt `assigned_count = 0`. → Bất kể deploy vào ca nào, tỉ lệ chia vẫn đúng ngay.
   - Bước c: đổi cột `shift` thành NOT NULL; cập nhật `unique_together` + index mới
     (drop index/unique cũ `(date, sheet)` / `(date, sheet, user)`).

> Ghi chú vận hành: nên deploy đầu ngày làm việc; sau deploy admin rà lại ma trận 3 ca.

## 5. Service — `LeadAssignmentService`

```python
# Thêm tham số shift; phần thuật toán (largest-remainder, select_for_update,
# transaction nguyên tử) GIỮ NGUYÊN — chỉ thêm 1 chiều filter.
def pick_staff_for(date, shift, sheet): ...
    .filter(date=date, shift=shift, sheet=sheet, weight__gt=0)

def assign_if_unassigned(order):
    sheet = classify_sheet(...)
    if sheet is None: return
    shift = ShiftService.current_shift()
    user = pick_staff_for(timezone.localdate(), shift, sheet)
    ...  # phần còn lại như cũ
```

`workload(sheet)` và `transfer(sheet, ...)`: **không đổi** (theo D5).

Hai điểm gọi `assign_if_unassigned` (landing ingest + tạo đơn tay) **không cần sửa** — chữ ký
`assign_if_unassigned(order)` giữ nguyên, shift được lấy bên trong.

## 6. API

Tất cả dưới prefix `/api/orders/lead-assignment`, guard `require_section("lead-assignment")`.

### Sửa endpoint hiện có

- `GET /shares?date=&shift=` — trả ma trận `NV × sheet` cho `(date, shift)`. `shift` mặc định
  = `ShiftService.current_shift()` nếu không truyền. Response thêm field `shift` + `current_shift`.
- `PUT /shares` — payload thêm `shift`; reconcile chỉ trong phạm vi `(date, shift)`
  (xoá/ghi các dòng của ngày+ca đó, không đụng ca khác).

### Endpoint mới

- `GET /shift-windows` — trả 3 ca + khung giờ + `current_shift`.
- `PUT /shift-windows` — cập nhật khung giờ 3 ca; validate phủ-kín-24h; xoá cache.

### Không đổi

- `GET /staff`, `GET /workload?sheet=`, `POST /transfer` — giữ nguyên (D5).

## 7. Frontend — `LeadAssignmentPage.tsx`

- **Selector/tab chọn ca** (Sáng/Chiều/Tối) phía trên ma trận; đổi ca → fetch lại `shares`
  cho `(date, shift)`. Badge **"Ca hiện tại: …"** dựa trên `current_shift` từ API.
- Ma trận `NV × sheet` hoạt động như cũ nhưng theo ca đang chọn; tổng cột mỗi sheet vẫn nên = 100%.
- Khối **cấu hình mốc giờ 3 ca**: 3 cặp time-input (start/end) + nút lưu; hiển thị lỗi nếu
  không phủ kín 24h (đọc lỗi validate từ BE; có thể validate sơ bộ ở FE).
- Khối **xem tải + chuyển đơn**: giữ nguyên (theo sheet, không theo ca).
- API client `lib/api/lead-assignment.ts`: thêm tham số `shift` cho shares; thêm
  `getShiftWindows`/`putShiftWindows`.

## 8. Test

**Backend (`tests/`):**

- `ShiftService.current_shift` / `shift_for_time`: đúng biên (12:00→afternoon, 18:00→evening,
  23:59→evening, 00:00→morning); fallback khi thiếu config.
- Validate phủ-kín-24h: chấp nhận cấu hình liền mạch; từ chối khe hở/chồng lấn.
- `pick_staff_for`: cô lập theo ca — cấu hình ca sáng không ảnh hưởng kết quả khi `shift=evening`;
  largest-remainder vẫn đúng trong từng ca.
- `assign_if_unassigned`: gán đúng theo ca hiện tại (mock `current_shift`); no-op khi ca chưa cấu hình.
- API: GET/PUT `/shares` theo `(date, shift)` reconcile không rò sang ca khác; GET/PUT
  `/shift-windows`.
- Migration data: dòng cũ được nhân thành 3 ca đúng weight.

**Frontend:** smoke test render selector ca + đổi ca refetch (theo mức test FE hiện có của trang).

## 9. Edge cases

- Ca hiện tại chưa cấu hình share (weight rỗng) → `pick_staff_for` trả `None` → đơn không
  auto-gán (đúng hành vi hiện tại khi thiếu config). Không lỗi.
- `shift_windows` chưa seed / lỗi → fallback `morning` + log warning, không chặn ingest.
- Đổi khung giờ ca giữa ngày: chỉ ảnh hưởng đơn về **sau** thời điểm đổi (đơn cũ đã gán giữ nguyên).
- Concurrency: `select_for_update` trong `pick_staff_for` đã serialize theo `(date, shift, sheet)`.

## 10. Phạm vi KHÔNG làm (YAGNI)

- Không thêm cột `shift` vào bảng `orders`.
- Không làm cấu hình mặc định/lặp lại (recurring template) — vẫn per-date (D4).
- Không phân rã workload/transfer theo ca (D5).
- Không xử lý ca qua-nửa-đêm phức tạp ngoài ca tối kết thúc tại 24:00.
