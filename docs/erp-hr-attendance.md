# Chấm công self-service (Attendance Check-in) — Thiết kế

> Trạng thái: **Draft thiết kế** · Ngày: 2026-06-16 · Thuộc module HR (`apps/hr`)
> Liên quan: `erp-hr.md` (đã có `AttendanceRecord` + `AttendanceService` từ Sprint 3), `architecture-standard.md` (ranh giới module, clean arch).

## 1. Mục tiêu

Nhân viên vào một trang hàng ngày để **tự chấm công**: chụp ảnh khuôn mặt + lấy vị trí; hệ thống chỉ ghi nhận khi ở **gần công ty** (trong bán kính cấu hình được). Ảnh lưu làm bằng chứng để HR đối chiếu.

### Quyết định nền tảng (đã chốt)

| Mã | Quyết định | Lý do |
|----|-----------|-------|
| A1 | **Đăng nhập tài khoản sẵn có** (JWT), trang nội bộ `/hr/check-in` | Biết chính xác ai chấm công; tái dùng auth hiện có; không tự quản token. |
| A2 | **Ảnh chụp & lưu làm bằng chứng** (KHÔNG nhận diện ML) | Đơn giản, không cần hạ tầng ML; HR đối chiếu thủ công. |
| A3 | **Lưu ảnh trên MinIO** (object storage) | Ảnh hàng ngày tích luỹ lớn; tách khỏi DB; thêm 1 service docker. |
| A4 | **Chặn khi ngoài bán kính** | Đúng yêu cầu "phải gần công ty"; xác minh server-side. |
| A5 | **Auto in/out:** lần đầu trong ngày = check_in, lần sau = check_out | 1 nút duy nhất, đơn giản cho NV. |
| A6 | **Nhiều điểm chấm công** (CompanyLocation), hợp lệ nếu gần BẤT KỲ điểm active | Hỗ trợ nhiều VP/chi nhánh; chi phí code không đáng kể. |

### Phạm vi & sprint
- **Sprint A — Backend + hạ tầng:** MinIO, model, service, API self check-in + locations CRUD.
- **Sprint B — Frontend:** trang `/hr/check-in` (camera + GPS), trang cấu hình vị trí.

Đây là 1 feature gắn kết; tách 2 sprint để BE/hạ tầng ổn trước khi làm FE.

---

## 2. Data model (apps/hr)

```python
class CompanyLocation(models.Model):
    name = CharField(max_length=150)
    latitude = FloatField()
    longitude = FloatField()
    radius_m = PositiveIntegerField(default=150)   # bán kính cho phép (mét)
    is_active = BooleanField(default=True)
    created_at = DateTimeField(auto_now_add=True)
    updated_at = DateTimeField(auto_now=True)
    class Meta: db_table = "hr_company_locations"


class AttendanceEventKind(TextChoices):
    CHECK_IN = "check_in", "Vào"
    CHECK_OUT = "check_out", "Ra"


class AttendanceEvent(models.Model):
    """Append-only: mỗi lần ấn chấm công = 1 event (có ảnh + GPS). Không sửa/xoá."""
    employee = FK(Employee, on_delete=PROTECT, related_name="attendance_events")
    kind = CharField(choices=AttendanceEventKind.choices)
    occurred_at = DateTimeField()                  # server time (timezone.now)
    work_date = DateField()                         # ngày làm (theo TZ server) để query nhanh
    photo_key = CharField(max_length=300)           # object key trong MinIO bucket
    latitude = FloatField()
    longitude = FloatField()
    distance_m = IntegerField()                     # khoảng cách tới điểm khớp gần nhất
    location = FK(CompanyLocation, null=True, on_delete=SET_NULL, related_name="events")
    created_at = DateTimeField(auto_now_add=True)
    class Meta:
        db_table = "hr_attendance_events"
        indexes = [Index(fields=["employee", "work_date"], name="hr_att_evt_emp_date_idx")]
```

**Quan hệ với `AttendanceRecord` (đã có):** `AttendanceEvent` là nguồn sự thật của self check-in. Service vẫn **cập nhật `AttendanceRecord`** daily (check_in = giờ event check_in đầu, check_out = giờ event check_out) qua `AttendanceService.record(...)` để danh sách chấm công HR hiện có dùng tiếp. `AttendanceRecord.source` set `"self"`.

---

## 3. Backend

### 3.1 Domain
`backend/apps/hr/domain/geo.py`:
- `haversine_m(lat1, lng1, lat2, lng2) -> float` — khoảng cách mét (thuần Python, test được).
- `nearest_location(lat, lng, locations) -> (location, distance_m) | (None, None)` — chọn điểm gần nhất trong danh sách.

### 3.2 Service
`AttendanceCheckinService.check_in(*, employee_id, latitude, longitude, photo_bytes, content_type) -> AttendanceEvent` (`@transaction.atomic`):
1. Xác định `kind`: đếm event hôm nay của NV — chưa có `check_in` → `check_in`; đã có `check_in` chưa có `check_out` → `check_out`; đã đủ cả 2 → raise `AlreadyCompleteError` (409).
2. Lấy `CompanyLocation` active; `nearest_location(...)`. Nếu không có điểm nào hoặc `distance > radius_m` của điểm gần nhất → raise `OutsideGeofenceError(distance, nearest)` (422).
3. Upload `photo_bytes` lên MinIO (key `attendance/{employee_id}/{work_date}/{kind}-{ts}.jpg`) qua `PhotoStorage`.
4. Tạo `AttendanceEvent` + cập nhật `AttendanceRecord` daily (`AttendanceService.record`, source="self").
5. Return event.

Lỗi domain đặt ở service module: `OutsideGeofenceError`, `AlreadyCompleteError`, `NoEmployeeError`.

### 3.3 Storage (MinIO)
`backend/apps/hr/infrastructure/photo_storage.py` — wrapper quanh boto3/django-storages S3 client trỏ MinIO:
- `put(key, data, content_type) -> key`
- `presigned_url(key, expires=300) -> str` (cho admin xem lại).
Cấu hình endpoint/bucket/credentials qua env (xem §4). Bucket `attendance` private.

### 3.4 API (`/api/hr/attendance/...`)
| Method | Path | Quyền | Mô tả |
|--------|------|-------|-------|
| GET | `/api/hr/attendance/config` | IsAuthenticated | Trả `{locations: [{id,name,latitude,longitude,radius_m}], today: {checked_in_at, checked_out_at}}` của user hiện tại. FE dùng để hiển thị/hướng dẫn. |
| POST | `/api/hr/attendance/check-in` | IsAuthenticated + **phải có `request.user.employee`** | Body `{latitude, longitude, photo}` (`photo` = data URL base64 JPEG). Server xử lý theo §3.2. Trả `{kind, occurred_at, distance_m, location_name}`. Lỗi: 422 ngoài vùng (kèm distance + radius), 409 đã đủ, 404 chưa có Employee. **KHÔNG cần quyền `employees:*`.** |
| GET | `/api/hr/attendance/locations` | `employees:view` | List điểm (admin). |
| POST | `/api/hr/attendance/locations` | `employees:edit` | Tạo điểm. |
| PATCH | `/api/hr/attendance/locations/{id}` | `employees:edit` | Sửa điểm. |
| DELETE | `/api/hr/attendance/locations/{id}` | `employees:edit` | Xoá/tắt điểm. |
| GET | `/api/hr/employees/{id}/attendance-events` | `employees:view` | (tuỳ chọn Sprint A) admin xem event + ảnh (presigned URL). |

Self check-in **không** dùng `_helpers.can(...)`; chỉ cần đăng nhập + có `request.user.employee` (regular NV không có quyền `employees:*`).

### 3.5 Ảnh trong request
FE gửi ảnh **base64 data URL** trong JSON (tái dùng `fetchJson` + auth). Server decode → bytes → upload MinIO. Giới hạn kích thước (vd ≤ 400KB sau nén) validate ở serializer.

---

## 4. Hạ tầng MinIO

- **docker-compose:** thêm service `minio` (image `minio/minio`), volume `minio-data`, env `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`, expose cổng nội bộ; tạo bucket `attendance` (private) lúc khởi tạo (init job hoặc tự tạo khi `put` lần đầu).
- **Backend deps:** `boto3` (+ `django-storages` nếu dùng FileField; ở đây dùng boto3 client trực tiếp trong `photo_storage.py` cho gọn).
- **Settings (`settings_parts/base.py`):** `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET_ATTENDANCE`, `MINIO_SECURE` đọc từ env (mặc định trỏ service `minio:9000`).
- Ảnh đọc lại qua **presigned URL** ngắn hạn (admin), không public bucket.

---

## 5. Frontend

### 5.1 Trang `/hr/check-in` (NV) — mobile-first
- **Route chỉ chặn theo đăng nhập**, KHÔNG theo section (mọi NV có tài khoản đều vào được). Reachable qua link nổi bật trong app (vd nút trên top-nav / trang chủ).
- Khi mở: gọi `GET /api/hr/attendance/config`; xin quyền **vị trí** (`navigator.geolocation.watchPosition`) và **camera** (`getUserMedia`).
- Hiển thị: trạng thái hôm nay (Chưa chấm / Đã vào lúc HH:mm / Đã ra lúc HH:mm), khoảng cách tới điểm gần nhất, biển báo trong/ngoài bán kính.
- Chụp ảnh: video → canvas → nén JPEG nhỏ (vd cạnh dài ≤ 640px, quality ~0.6) → data URL.
- Nút **"Chấm công"**: **khoá khi ngoài bán kính** hoặc thiếu ảnh/vị trí (server vẫn chặn lần cuối). Submit `POST /check-in`; hiện kết quả (Vào/Ra + giờ) hoặc lỗi (ngoài vùng → "Bạn đang cách công ty Xm, cần ≤ Ym").
- `feature/slice`: `src/features/hr/pages/AttendanceCheckInPage.tsx` + hooks `useGeolocation`, `useCamera`, query `useAttendanceConfig` + mutation `useCheckIn`.

### 5.2 Trang cấu hình vị trí (admin) — section `employees`
- `src/features/hr/pages/AttendanceLocationsPage.tsx`: bảng điểm + form (tên, lat, lng, bán kính, bật/tắt). Nút **"Lấy vị trí hiện tại"** điền nhanh toạ độ. Route `/hr/attendance-locations` guard section `employees`.
- (Tuỳ chọn) Trong trang chi tiết NV: tab/section xem lịch sử event + ảnh (presigned URL).

---

## 6. Lỗi & bảo mật
- Ngoài bán kính → 422 kèm `distance_m` + `radius_m`. Đã chấm đủ → 409. Chưa có Employee → 404. Thiếu camera/GPS → FE hướng dẫn cấp quyền trình duyệt.
- Ảnh: validate content-type (image/jpeg|png) + size; key gắn employee_id + ngày (không cho client tự đặt key).
- Geofence **xác minh server-side** (FE chỉ hỗ trợ UX); toạ độ do client gửi (chấp nhận giới hạn: GPS có thể giả mạo — ghi nhận MVP, ảnh là lớp bằng chứng bổ sung).
- Self check-in chỉ ghi cho `request.user.employee` (không nhận `employee_id` từ client).

## 7. Testing
- **Domain:** `haversine_m` (điểm đã biết), `nearest_location`.
- **Service:** auto kind (in→out→đủ); chặn ngoài bán kính (raise); upload gọi storage (mock); cập nhật `AttendanceRecord` daily; idempotent/transaction.
- **API:** check-in trong vùng → 201 + tạo event + ảnh lên storage (mock); ngoài vùng → 422; chưa có Employee → 404; check-in lần 2 → check_out; lần 3 → 409. Locations CRUD theo quyền (`employees:edit` mới sửa được; NV thường 403). Self check-in: NV không có quyền `employees` vẫn chấm được.
- **FE:** render trạng thái hôm nay; nút khoá khi ngoài bán kính; gọi mutation đúng payload (mock geolocation + getUserMedia + api).

## 8. Definition of Done
- [ ] MinIO chạy trong compose; ảnh lưu/đọc được qua presigned URL.
- [ ] NV thường (không quyền `employees:*`) chấm công được; ngoài bán kính bị chặn (cả FE lẫn BE).
- [ ] Cập nhật `AttendanceRecord` daily nhất quán với event.
- [ ] Test BE + FE xanh; ruff/tsc/build sạch.
- [ ] Không import ORM chéo module; ảnh không lưu trong DB.

---

## 8b. Sprint A — Tiến độ (cập nhật 2026-06-16, branch `feature/hr-attendance-checkin`)

**Đã làm (BE + hạ tầng):**
- Hạ tầng MinIO: service `minio` trong `docker-compose.yml` + `boto3` + settings `MINIO_*` + `DATA_UPLOAD_MAX_MEMORY_SIZE`; `.env.example` có block MinIO.
- Domain `geo.py` (haversine + nearest_location); models `CompanyLocation` + `AttendanceEvent` (migration 0007) với **unique `(employee, work_date, kind)`** (migration 0008) chống race tạo 2 event cùng loại.
- `photo_storage.py` (boto3 lazy + tự tạo bucket khi thiếu); repositories; `AttendanceCheckinService` (auto in/out, chặn geofence trước khi ghi, upload ảnh, cập nhật `AttendanceRecord` daily).
- API: `POST /api/hr/attendance/check-in` (chỉ cần đăng nhập + có hồ sơ NV, ghi cho chính mình), `GET /api/hr/attendance/config`, CRUD `/api/hr/attendance/locations` (quyền `employees:edit`).
- Test: backend **94 pass**.

**Review tìm & sửa:** (Critical) race double check-in → unique constraint + bắt `IntegrityError`→409; (Important) ảnh không kiểm loại → sniff magic-byte JPEG/PNG, dùng đúng content-type; (Important) chặn lat/lng ngoài [-90,90]/[-180,180]; (Minor) tự tạo bucket MinIO + `.env.example`.

**Nợ/lưu ý deploy:**
- **Rebuild image backend** để cài `boto3`; đặt env `MINIO_ROOT_USER/PASSWORD` = `MINIO_ACCESS_KEY/SECRET_KEY`; bucket `attendance` tự tạo ở lần upload đầu.
- IntegrityError lúc race → ảnh đã upload thành **orphan** trong MinIO (chấp nhận; có thể thêm job dọn rác sau).
- Geofence: `distance > radius` (đúng tại mép = cho phép).

## 8c. Sprint B — Tiến độ (cập nhật 2026-06-16, branch `feature/hr-attendance-checkin`)

**Đã làm (FE):**
- Types + api client (`lib/api/hr-attendance.ts`) + queries (`useAttendanceConfig/useCheckIn/useLocations/useCreate|Update|DeleteLocation`).
- Hooks `useGeolocation` (watchPosition), `useCamera` (getUserMedia + capture), util `compressDataUrl` (nén JPEG).
- **Trang `/hr/check-in`** (mobile-first): trạng thái hôm nay, preview camera, khoảng cách tới điểm gần nhất, nút "Chấm công" **khoá khi ngoài bán kính / thiếu vị trí / thiếu camera**; route **login-only** (`LoginOnly` wrapper, không gate theo section); link "Chấm công" trong dropdown avatar — hiện cho MỌI user đăng nhập.
- **Trang `/hr/attendance-locations`** (admin, quyền `employees`): bảng + form CRUD vị trí, nút "Lấy vị trí hiện tại"; section `attendance-locations` đăng ký trong module HR.
- Test: FE **13 pass** (`src/features/hr`); tsc + build sạch; `modules.test` chỉ còn lỗi pre-existing `lead-assignment`/`roles`.

## 8d. Tổng kết tính năng chấm công (Sprint A+B)

Self check-in hoàn chỉnh: NV đăng nhập → `/hr/check-in` (link ở dropdown avatar) → chụp ảnh + GPS → server xác minh trong bán kính (chặn nếu ngoài) → lưu ảnh MinIO + ghi `AttendanceEvent` + cập nhật `AttendanceRecord` daily. Admin cấu hình nhiều điểm chấm công. Branch `feature/hr-attendance-checkin` (1 MR). BE 94 test, FE 13 test.

**Lưu ý deploy:** rebuild image backend (boto3); set env MinIO (`MINIO_ROOT_USER/PASSWORD`=`MINIO_ACCESS_KEY/SECRET_KEY`); bucket `attendance` tự tạo lần upload đầu. **Cần HTTPS** ở prod để trình duyệt cho phép camera + geolocation.

## 8e. Sửa UX sau khi dùng thật (cập nhật 2026-06-16, branch `feature/hr-attendance-ux-fixes`)

Phản hồi từ dùng thật trên iPhone:
- **Camera trước bị lật trái↔phải** → mirror video preview (`-scale-x-100`) + mirror canvas khi `capture()` để ảnh lưu khớp preview.
- **Mobile: khung camera quá to, không bấm được nút xác nhận** → giới hạn camera `max-h-[45vh]`, trang cuộn được (`min-h-dvh`), nút "Chấm công" thành **sticky footer** (an toàn vùng `safe-area-inset`).
- **Cấu hình vị trí bằng bản đồ** thay vì gõ lat/long → thêm `LocationMapPicker` (Leaflet + OpenStreetMap, KHÔNG cần API key): click/kéo marker, vòng tròn bán kính, ô **tìm địa chỉ** (Nominatim, tìm khi bấm/Enter), nút "Lấy vị trí hiện tại". Lat/lng đồng bộ 2 chiều với form.

Lưu ý: geocoding gọi thẳng Nominatim từ FE (có `eslint-disable no-restricted-globals` kèm giải thích) — nếu cần kiểm soát, sau này proxy qua backend. Map chỉ mount khi dialog mở (test list không đụng Leaflet trong jsdom).

## 8f. Lịch sử chấm công + chuyển Vị trí sang Cài đặt (2026-06-16, branch `feature/hr-attendance-history`)

- **Backend:** endpoint `GET /api/hr/attendance/events` (quyền `employees:view`) — lọc `department`/`employee`/`date_from`/`date_to`, phân trang; trả mỗi event kèm tên NV, phòng ban, loại, giờ, khoảng cách, tên điểm, **ảnh presigned URL** (MinIO, hết hạn 600s). `select_related` chống N+1.
- **FE — trang Lịch sử chấm công** (`/hr/attendance-history`, module Nhân sự, quyền `employees`): bộ lọc phòng ban + khoảng ngày + bảng (mã/tên NV, phòng ban, ngày, Vào/Ra, giờ, khoảng cách, điểm, **thumbnail ảnh**). Bấm dòng → dialog **ảnh phóng to + bản đồ Leaflet read-only** đánh dấu toạ độ chấm công.
- **Chuyển "Vị trí chấm công"** từ module HR → **module Cài đặt** (`/settings/attendance-locations`), giữ quyền `employees`; thêm redirect `/hr/attendance-locations` → `/settings/...` cho link cũ.
- Test: backend 98 pass, FE 14 pass; tsc/build sạch.

**Nợ nhỏ:** `presigned_get_url` tạo boto3 client mỗi lần gọi → 1 trang 50 dòng = 50 lần khởi tạo client (chỉ ms, không network); có thể cache client ở module nếu cần tối ưu. Bộ lọc lịch sử chưa có ô tìm NV theo tên (backend lọc `employee` theo ID) — bổ sung sau nếu cần.

## 8g. Sửa hiển thị ảnh + gộp Vào/Ra (2026-06-16, branch `feature/hr-attendance-history`)

- **Ảnh không hiện (ERR_NAME_NOT_RESOLVED + Mixed Content):** presigned URL trỏ `http://minio:9000` (hostname nội bộ + http). Bỏ presigned; **backend stream ảnh** qua `GET /api/hr/attendance/events/{id}/photo` (cùng origin HTTPS, có auth). FE dùng component **`AuthImage`** (fetch blob kèm JWT → object URL). MinIO không cần expose ra ngoài.
- **Bảo mật (security review HIGH):** endpoint ảnh giới hạn content-type allowlist (jpeg/png/webp) + `X-Content-Type-Options: nosniff` + CSP + `Content-Disposition: inline` → chống stored-XSS khi serve trên app-origin.
- **Gộp Vào + Ra:** endpoint trả **1 dòng/nhân viên/ngày** (nhóm theo `employee + work_date`), mỗi dòng có `check_in`/`check_out` (giờ, khoảng cách, vị trí, ảnh). FE: bảng cột Giờ vào / Giờ ra + thumbnail; dialog chi tiết hiện cả 2 (ảnh to + bản đồ).
- Test: backend 100 pass, FE green; tsc/build sạch.

## 9. Liên kết
- `erp-hr.md` — module HR, `AttendanceRecord`/`AttendanceService` Sprint 3.
- `architecture-standard.md` — clean architecture, ranh giới module.
