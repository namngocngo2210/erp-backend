# Mobile Self-Service (ESS) — PWA `/m`

- **Ngày:** 2026-06-16
- **Trạng thái:** ✅ Hoàn thành (S1–S4)
- **Branch:** `feature/mobile-ess-self-service`
- **Kiến trúc liên quan:** Modular Monolith (D2), React SPA + react-router (D3) — xem `docs/erp-master-plan.md`, `docs/architecture-standard.md`.

## 1. Bối cảnh & Mục tiêu

Nhân viên hiện phải vào toàn bộ vỏ ERP (app-shell Odoo, sidebar nặng) chỉ để chấm công / xin nghỉ.
Cần một trải nghiệm **mobile self-service (ESS)** gọn, cài được ra màn hình chính (PWA), tách khỏi vỏ ERP.

Backend **đã có sẵn** phần lớn nghiệp vụ:
- Auth JWT versioned: `POST /api/users/login`, `GET /api/users/me`, refresh cookie httpOnly, `POST /api/users/change-password`.
- Chấm công: `GET /api/hr/attendance/config`, `POST /api/hr/attendance/check-in` (camera + GPS geofence) — **đã auth-only**, resolve `request.user.employee`.
- Nghỉ phép: model + service `LeaveService.request/approve/reject` đầy đủ.
- Hồ sơ nhân sự: model `Employee` (gắn 1:1 với `User`).

**Điểm thiếu chính:** các endpoint đọc/tạo dữ liệu cá nhân hiện bị gate bởi RBAC `employees:view/edit` — nhân viên thường không có. Cần thêm nhóm endpoint **self** (`/api/hr/me/*`) chỉ yêu cầu đăng nhập và tự resolve employee từ token.

### Phạm vi v1
- Chấm công (check-in/out) + cấu hình geofence.
- Lịch sử chấm công của chính mình.
- Xin nghỉ phép + xem trạng thái đơn của mình.
- Thông tin cá nhân + đổi mật khẩu + đăng xuất.

### Ngoài phạm vi v1 (YAGNI)
- Quản lý duyệt/từ chối nghỉ phép trên mobile (giữ thuần self-service).
- Offline caching dữ liệu, push notification.
- Bảng lương / hợp đồng / phiếu lương trên mobile.

## 2. Kiến trúc tổng thể

Mobile shell sống **trong cùng SPA**, tách nhánh tại `main.tsx` (đã có tiền lệ với `/warranty`):

```
main.tsx
 ├─ path /warranty → WarrantyPage (lazy, public, đã có)
 ├─ path /m/*      → MobileApp   (lazy)  ← MỚI
 └─ còn lại        → App (ERP đầy đủ)
```

- Tách nhánh ở `main.tsx` ⇒ route `/m` **code-split**, không kéo bundle ERP nặng.
- `MobileApp` **dùng lại** hạ tầng auth hiện có: `useAuthBootstrap`, `services/http.ts` (access token in-memory + refresh cookie httpOnly), `queryClient`, `I18nProvider`. Không viết lại auth.
- Layout = **bottom tab nav** 4 mục, KHÔNG dùng `RootLayout`/sidebar ERP.

Ranh giới module (theo `architecture-standard.md`): ESS frontend là một feature-slice độc lập (`features/ess`), chỉ phụ thuộc vào: hạ tầng dùng chung (`services/http`, `hooks/use-auth-bootstrap`, `lib/query-client`) và **lõi check-in tái sử dụng** tách ra từ `features/hr`. Backend chỉ thêm view/route mới trong app `hr`, tái dùng service/repository sẵn có — không sửa nghiệp vụ lõi.

## 3. Backend — nhóm endpoint "self" (`/api/hr/me/...`)

File mới: `backend/apps/hr/transport/views/self_service_views.py`.
Tất cả: `@permission_classes([IsAuthenticated])`, employee lấy từ `getattr(request.user, "employee", None)`; nếu None → `404 {"detail": "Tài khoản chưa gắn hồ sơ nhân viên."}`. **Bỏ qua mọi `employee_id` client gửi lên.**

| Method | Route | Mô tả | Tái dùng |
|---|---|---|---|
| GET | `/api/hr/me/profile` | Hồ sơ của chính mình | `EmployeeRepository` + `EmployeeSerializer` (hoặc serializer rút gọn cho ESS) |
| GET | `/api/hr/me/attendance?month=YYYY-MM` | Lịch sử chấm công của mình | `AttendanceRepository.for_month` + `AttendanceSerializer` |
| GET | `/api/hr/me/leave-requests` | Đơn nghỉ của mình | `LeaveRepository.active().filter(employee_id=emp.id)` + `LeaveRequestSerializer` |
| POST | `/api/hr/me/leave-requests` | Tạo đơn nghỉ cho mình | `LeaveService.request(emp.id, ...)` |

Chi tiết:
- **`/me/profile`**: trả `employee_code, full_name, department, job_title, manager (tên), employment_status, hire_date, phone, personal_email` + `username/email` từ user. Tránh lộ trường nhạy cảm không cần (lương, id_number) — dùng serializer riêng `MeProfileSerializer`.
- **`/me/attendance`**: tham số `month` (mặc định tháng hiện tại); trả list ngày + giờ vào/ra. Không trả ảnh inline (ảnh vẫn qua endpoint photo có sẵn nếu cần, nhưng v1 chỉ cần giờ).
- **`/me/leave-requests` GET**: chỉ đơn của mình, sort `-start_date`.
- **`/me/leave-requests` POST**: body `{leave_type, start_date, end_date, reason}` — **không** nhận `employee_id`; server gán `emp.id`. Validate qua `LeaveCreateSerializer` (bỏ field employee_id hoặc serializer mới `MeLeaveCreateSerializer`). Bắt `LeaveRuleError` → 400.

Đăng ký route trong `backend/apps/hr/urls.py`:
```python
path("me/profile", me_profile, name="hr-me-profile"),
path("me/attendance", me_attendance, name="hr-me-attendance"),
path("me/leave-requests", me_leave_requests, name="hr-me-leave-requests"),
```

Check-in/config (`/api/hr/attendance/config`, `/check-in`) và đổi mật khẩu (`/api/users/change-password`) dùng nguyên trạng.

### Bảo mật
- Mọi endpoint self **luôn** derive employee từ token, không bao giờ từ input ⇒ chống IDOR/xem chéo.
- Tài khoản đã nghỉ việc (user deactivated / `employment_status=resigned`): login đã bị chặn ở tầng auth — thêm test khẳng định.

## 4. Frontend — feature slice `features/ess/`

```
features/ess/
├─ MobileApp.tsx          # bootstrap auth + <RouterProvider> riêng (router con của /m)
├─ MobileLayout.tsx       # khung full-screen + <BottomTabNav> + <Outlet>
├─ BottomTabNav.tsx       # 4 tab: Chấm công · Lịch sử · Nghỉ phép · Cá nhân
├─ MobileLoginView.tsx    # login full-screen (dùng API /api/users/login như cũ)
├─ pages/
│  ├─ CheckInPage.tsx        # bọc lõi check-in tái sử dụng
│  ├─ MyAttendancePage.tsx   # list theo tháng, chọn tháng
│  ├─ MyLeavePage.tsx        # list đơn + nút tạo (sheet/dialog)
│  └─ ProfilePage.tsx        # hồ sơ + đổi mật khẩu + đăng xuất
├─ queries.ts             # useMyProfile / useMyAttendance / useMyLeave / useCreateMyLeave
├─ ess-types.ts
└─ __tests__/
```

### Routing con (trong MobileApp)
- `/m/login` → MobileLoginView (khi chưa auth).
- `/m` (index) → CheckInPage.
- `/m/lich-su` → MyAttendancePage.
- `/m/nghi-phep` → MyLeavePage.
- `/m/ca-nhan` → ProfilePage.

(Đường dẫn tiếng Việt không dấu cho thân thiện; có thể đổi sang en nếu muốn nhất quán — quyết định lúc implement, mặc định tiếng Việt.)

### Tái sử dụng lõi check-in
Tách phần lõi của `features/hr/pages/AttendanceCheckInPage.tsx` (logic `useCamera`, `useGeolocation`, tính khoảng cách Haversine, gọi `useAttendanceConfig`/`useCheckIn`) thành một component dùng chung, ví dụ `features/hr/components/CheckInCore.tsx`. Cả ERP `/hr/check-in` lẫn ESS `/m` cùng render component này ⇒ không duplicate logic camera/GPS.

### UI/UX
- Full-screen, `100dvh`, nút lớn, tối ưu chạm; light theme tông xanh da trời như ERP; shadcn/ui; lucide-react icons.
- Không nhúng AG Grid; lịch sử dùng list/card đơn giản.
- Bottom nav cố định đáy; mỗi tab có icon + label.

## 5. PWA

- Thêm `vite-plugin-pwa` vào `frontend/vite.config.ts`.
- Manifest: `name="DESI Nhân viên"`, `short_name="DESI NV"`, `start_url="/m"`, `scope="/m"`, `display="standalone"`, theme/background color theo tông xanh, icon 192/512 (+ maskable).
- Service worker **tối thiểu**: precache app-shell (HTML/JS/CSS) để mở nhanh. **Không** cache response API chấm công/nghỉ phép (cần realtime; camera/GPS cần mạng). `registerType: 'autoUpdate'`.
- Đảm bảo SW scope `/m` không can thiệp route ERP và `/warranty`.

## 6. Test

**Backend (unit, theo memory `running-backend-tests`):**
- `/me/profile`, `/me/attendance`, `/me/leave-requests` GET/POST: 200 cho user có employee; 404 khi user không gắn employee.
- **IDOR**: user A truyền `employee_id` của B (qua query/body) vẫn chỉ nhận dữ liệu A.
- POST `/me/leave-requests`: tạo đúng cho chính mình; vi phạm rule → 400.
- Không yêu cầu quyền `employees:*` (user thường không có quyền vẫn gọi được).

**Frontend (vitest + RTL):**
- MobileLoginView: submit gọi login, lỗi hiển thị.
- BottomTabNav: điều hướng 4 tab.
- CheckInPage/MyAttendancePage/MyLeavePage/ProfilePage: render với mock query, trạng thái loading/empty.
- Lưu ý baseline có sẵn test FE fail ở mobile-viewport (memory `known-failing-baseline-tests`) — không nhầm là regression.

## 7. Sprint plan & tiến độ

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| **S1 — Backend self API** | 4 endpoint `/api/hr/me/*` + serializer (`MeProfileSerializer`, `MeLeaveCreateSerializer`) + route + unit test (gồm IDOR). | ✅ Hoàn thành |
| **S2 — Mobile shell** | Tách `CheckInCore` từ hr; `MobileApp` + bootstrap auth + `MobileLayout` + `BottomTabNav` + `MobileLoginView`; nhánh `/m` trong `main.tsx`; routing con. | ✅ Hoàn thành |
| **S3 — 4 màn hình** | CheckInPage, MyAttendancePage, MyLeavePage (+ form tạo đơn), ProfilePage (đổi MK + logout); `queries.ts`; FE test. | ✅ Hoàn thành |
| **S4 — PWA + hoàn thiện** | `vite-plugin-pwa` + manifest + icon + SW tối thiểu; kiểm tra cài đặt PWA; test E2E luồng nhân viên; cập nhật tài liệu. | ✅ Hoàn thành |

> Sau mỗi sprint: cập nhật cột trạng thái + note "đã làm / chưa làm / vướng mắc" ở cuối file này.

## 8. Nhật ký thực hiện

### S1 — Backend self API
- Thêm 4 endpoint `/api/hr/me/*` (`me/profile`, `me/attendance`, `me/leave-requests` GET/POST) trong app `hr`, đều `IsAuthenticated`, resolve employee từ token, bỏ qua mọi `employee_id` client gửi.
- Serializer riêng `MeProfileSerializer` (ẩn trường nhạy cảm) + `MeLeaveCreateSerializer` (không nhận `employee_id`).
- Unit test gồm cả case **IDOR**: user A gửi `employee_id` của B vẫn chỉ nhận dữ liệu A; user không gắn employee → 404. Kết quả: **6 test pass**.

### S2 — Mobile shell
- Tách lõi check-in từ `features/hr/pages/AttendanceCheckInPage.tsx` thành `CheckInCore` dùng chung (camera/GPS/Haversine), cả ERP `/hr/check-in` và ESS `/m` cùng render — không duplicate logic.
- Dựng `MobileApp` (bootstrap auth + router con), `MobileLayout`, `BottomTabNav` (4 tab), `MobileLoginView`; thêm nhánh `/m/*` code-split tại `main.tsx` (cùng mẫu với `/warranty`).

### S3 — 4 màn hình
- `CheckInPage`, `MyAttendancePage` (chọn tháng), `MyLeavePage` (list + form tạo đơn), `ProfilePage` (hồ sơ + đổi mật khẩu + đăng xuất); `queries.ts` (useMyProfile/useMyAttendance/useMyLeave/useCreateMyLeave).
- FE test cho các trang ESS. Kết quả ess: **3 test pass** (ProfilePage, MyLeavePage, MyAttendancePage).

### S4 — PWA + hoàn thiện
- Thêm `vite-plugin-pwa` (`registerType: autoUpdate`) vào `vite.config.ts`; manifest `DESI Nhân viên` / `start_url=/m` / `scope=/m` / `display=standalone`, theme `#2563eb`.
- Icon 192/512 + maskable-512 trong `public/icons/`.
- Build sinh `dist/sw.js` + `dist/workbox-*.js` + `dist/manifest.webmanifest`. `registerSW.js` đăng ký `navigator.serviceWorker.register('/sw.js', { scope: '/m' })` ⇒ SW **chỉ kiểm soát `/m`**, không hijack vỏ ERP hay `/warranty` (thêm `navigateFallbackDenylist` cho `/api`, `/warranty`).
- Test regression: ess + hr toàn bộ **17 test pass** (gồm CheckInCore/AttendanceCheckInPage), không có regression.

### TODO còn lại
- Thay 3 icon PWA placeholder (ô màu đặc `#2563eb`) bằng asset production thật từ thương hiệu.
- Smoke test cài đặt PWA + luồng nhân viên trên thiết bị thật (Android/iOS): add-to-home-screen, mở standalone, check-in camera/GPS.
