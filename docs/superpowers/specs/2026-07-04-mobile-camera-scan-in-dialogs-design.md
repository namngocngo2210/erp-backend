# Quét bằng camera trực tiếp trong dialog kho trên mobile (+ gỡ luồng QR relay)

**Ngày:** 2026-07-04 (cập nhật scope cùng ngày: gỡ hẳn QR relay theo yêu cầu user)
**Branch:** `feat/mobile-camera-scan-in-dialogs`

## Vấn đề

Nhân viên kho dùng điện thoại phải đi vòng: mở dialog quét trên desktop → desktop
sinh mã QR phiên relay → điện thoại quét QR đó → mã vận đơn mới đổ về desktop xử lý.
Khi chỉ có điện thoại trong tay (đứng ở kho), không có desktop để tạo phiên QR
→ không quét được.

## Giải pháp

Trên viewport mobile, hai dialog quét của kho nhúng thẳng camera của điện thoại
và đổ mã vào handler quét hiện có. **Luồng QR relay bị gỡ bỏ hoàn toàn** (quyết
định của user 2026-07-04): dùng điện thoại thì quét trực tiếp trên điện thoại,
không cần bắn mã về desktop nữa. Desktop còn ô nhập tay + súng quét Bluetooth
(`useScannerGun`) — không đổi.

Các API nghiệp vụ không đổi: lookup (`lookupByShippingCode`), xuất kho hàng loạt
(`useDispatchBatch`), tạo phiếu trả (`useCreateReturn`) — RBAC enforce ở backend.
Backend **gỡ app `scan_relay`** (API phiên `/api/scan-sessions*` + WebSocket
`ws/scan/<token>/`) vì không còn lối vào.

## Phạm vi

**Thêm camera scan (mobile):**
- **Quét xuất kho** — `frontend/src/features/inventory/scan/ScanDispatchDialog.tsx`
- **Quét nhập hoàn** — `frontend/src/features/inventory/pages/CreateReturnDialog.tsx`

**Gỡ QR relay (mọi nơi):**
- Nút `PhoneScanButton` ở cả 3 chỗ (2 dialog trên + `StockDocumentsPage`).
- `StockDocumentsPage`: gỡ trơn, KHÔNG gắn camera thay thế (user chốt: phiếu kho
  nhập tay/súng quét là đủ). Ô nhập quét + súng quét hiện có giữ nguyên.
- Frontend: `features/scan-relay/` (ScanRelayApp, ScanRelayPage), route
  `/scan-relay` trong `main.tsx`, `features/inventory/scan/phone/` (PhoneScanButton,
  use-phone-scan-session, scan-session-queries + tests), dep `qrcode.react`.
- Backend: app `apps/scan_relay` (urls, views, consumers, services, dto, routing),
  wiring trong `config/asgi.py`, `config/urls.py`, `INSTALLED_APPS`; regen
  `schema.yaml` + `frontend/src/api/schema.d.ts`.
- KHÔNG gỡ `qrcode==8.2` trong requirements backend — `apps/orders/invoice.py`
  vẫn dùng để sinh QR phiếu giao.

## Thiết kế chi tiết

### 1. Di chuyển `CameraScanner` về `features/inventory/scan/` (ĐÃ XONG — Task 1)

- `CameraScanner.tsx` + `barcode-detector.d.ts` giờ nằm ở
  `frontend/src/features/inventory/scan/`. Không sửa logic bên trong.

### 2. Component `CameraScanButton` (ĐÃ XONG — Task 3)

`frontend/src/features/inventory/scan/CameraScanButton.tsx`

- Props: `{ onScan: (code: string) => void | Promise<void> }`.
- Nút "Quét bằng camera" → overlay full-screen (portal vào `document.body`,
  z-50): camera nền, khung ngắm, đếm "Đã quét N mã" (reset khi mở lại),
  nút "Xong" đóng overlay và unmount camera.
- Beep/toast thành-bại do handler của dialog phụ trách.

### 3. Gắn vào 2 dialog + gỡ PhoneScanButton

- `isMobile = useMediaQuery(MOBILE_VIEWPORT_QUERY)` (constant chung từ
  `@/lib/constants/layout`, khớp breakpoint 768 của ResponsiveList — Task 2).
- Mobile: hiện `CameraScanButton`. Desktop: KHÔNG hiện gì ở vị trí đó
  (PhoneScanButton bị xóa; desktop dùng ô nhập + súng quét).
- Trên mobile, `ScanShipmentInput` không autofocus (prop `autoFocusOnMount`,
  default `true` — desktop giữ hành vi cũ).
- `useScannerGun` giữ nguyên, chạy song song ở cả 2 viewport.

### 4. Gỡ luồng QR relay

- FE: xóa `features/scan-relay/`, `features/inventory/scan/phone/`, nhánh route
  `/scan-relay` trong `main.tsx`, `npm uninstall qrcode.react`. Test nào mock/
  import các module này thì gỡ mock (StockDocumentsPage.lines.test.tsx).
- BE: xóa `apps/scan_relay/`; gỡ `apps.scan_relay` khỏi `INSTALLED_APPS`
  (`config/settings_parts/base.py:61`), gỡ include khỏi `config/urls.py:20`,
  gỡ import + `scan_ws` khỏi `config/asgi.py:14`.
- Schema: regen `backend/schema.yaml` (drf-spectacular, chạy trong docker image
  test theo runbook nội bộ) rồi `npm run gen:api` để `schema.d.ts` hết các path
  `/api/scan-sessions*`. Nếu không regen được trong môi trường hiện tại, xóa tay
  3 path block trong `schema.yaml` + chạy `gen:api`, ghi chú lại.

### 5. Ràng buộc môi trường

- Camera trình duyệt yêu cầu HTTPS — prod đã HTTPS. Chrome Android đi
  `BarcodeDetector`; iOS Safari đi fallback `@zxing` (sẵn trong CameraScanner).

## Kiểm thử

- Unit test `CameraScanButton` (ĐÃ XONG — Task 3).
- Test 2 dialog theo viewport: mobile → có "Quét bằng camera", không có nút QR;
  desktop → KHÔNG có cả hai (chỉ còn ô nhập + gun badge).
- StockDocumentsPage: test hiện có chạy sạch sau khi gỡ mock PhoneScanButton.
- BE: suite backend chạy sạch sau khi gỡ app (import/urls/asgi không vỡ).
- Baseline known-fail (không phải regression): OrderFormDialog + 2 test
  mobile-viewport FE; 7 lỗi typecheck pre-existing (dashboard/recharts/@sentry).

## Không làm (YAGNI)

- Không thêm chọn camera trước/sau, đèn flash, zoom.
- Không gắn camera scan cho StockDocumentsPage (user chốt: nhập tay/súng quét).
- Không gỡ `qrcode` (Python) — invoice vẫn dùng.
