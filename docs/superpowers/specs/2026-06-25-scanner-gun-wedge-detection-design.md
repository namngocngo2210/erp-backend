# Phát hiện súng quét (keyboard-wedge) + badge trạng thái

- **Ngày:** 2026-06-25
- **Trạng thái:** Đã duyệt thiết kế
- **Branch:** feat/phone-camera-scan-handoff (tiếp nối)

## Bối cảnh
Súng quét HID = bàn phím với trình duyệt → không có API biết "súng đã kết nối". Hiện 3 dialog quét chỉ bắt mã khi `ScanShipmentInput` đang focus; mất focus là bắn rơi. Mục tiêu: (1) quét ăn dù focus ở đâu trong dialog, (2) badge sáng khi phát hiện súng bắn để người dùng biết hệ thống đang nhận.

## Phạm vi
Cả 3 dialog: Quét xuất kho (`ScanDispatchDialog`), Tạo phiếu kho (`StockDocumentsPage` → CreateDocumentDialog), Tạo phiếu trả hàng (`CreateReturnDialog`).

### Ngoài phạm vi (YAGNI)
- Không dùng WebHID/Web Serial (chỉ Chromium, cần quyền, phải tự decode HID).
- Không phát hiện "đã cắm súng" trước khi bắn (bất khả thi với HID keyboard).
- Không đụng luồng camera/handoff điện thoại.

## Thành phần

### 1. Hook `useScannerGun({ enabled, onScan })`
File: `frontend/src/features/inventory/scan/useScannerGun.ts`
- Gắn `document` `keydown` listener **chỉ khi `enabled === true`** (dialog mở); gỡ khi disabled/unmount.
- Buffer ký tự in được (key.length === 1, không Ctrl/Meta/Alt, không IME `isComposing`).
- Nhịp: lưu `lastAt`; nếu `now - lastAt > FAST_GAP_MS` (50ms) → reset buffer rồi mới thêm ký tự. → buffer chỉ chứa chuỗi phím "cực nhanh" liên tiếp.
- Khi `Enter`: nếu `buffer.length >= MIN_LEN` (3):
  - Set `active = true`, hẹn giờ tự tắt sau `ACTIVE_MS` (4000ms).
  - Nếu `document.activeElement` KHÔNG phải input/textarea/contenteditable → gọi `onScan(buffer)`. (Khi đang focus ô quét, `ScanShipmentInput` đã tự xử lý → không emit để tránh bắn đôi.)
  - Reset buffer.
- Trả về `{ active }`.
- Hằng số: `FAST_GAP_MS = 50`, `MIN_LEN = 3`, `ACTIVE_MS = 4000`.
- Dùng ref cho `onScan` (tránh stale + tránh re-bind listener mỗi render).

### 2. Component `ScannerGunBadge({ active })`
File: `frontend/src/features/inventory/scan/ScannerGunBadge.tsx`
- `active` → xanh "Súng quét: đang hoạt động"; mặc định xám "Súng quét: chờ tín hiệu". Icon lucide `ScanBarcode`/`ScanLine`.

### 3. Tích hợp (3 dialog)
Mỗi dialog: `const { active } = useScannerGun({ enabled: <open>, onScan: <handler> })` + render `<ScannerGunBadge active={active} />` cạnh `ScanShipmentInput`/`PhoneScanButton`.
- `ScanDispatchDialog`: enabled = prop `open`; onScan = `handleScan`.
- `StockDocumentsPage` CreateDocumentDialog: enabled = dialog open state; onScan = `handleScanCode`.
- `CreateReturnDialog`: enabled = prop `open`; onScan = `handleScanCode`.

## Luồng dữ liệu
keydown(document) → hook đo nhịp/buffer → Enter + burst nhanh → `active=true` (badge); nếu focus ngoài ô nhập → `onScan(code)` → handler dialog (lookup/dispatch/merge…).

## Rủi ro & xử lý
- **Bắn đôi** với `ScanShipmentInput`: không emit khi focus ở editable element.
- **Người gõ nhanh nhầm**: ngưỡng `FAST_GAP_MS` + `MIN_LEN`; chỉ ảnh hưởng khi gõ nhanh vào vùng-không-input (hiếm).
- **Bắt nhầm khi dialog đóng**: gate `enabled`.

## Test (vitest)
- `useScannerGun` (fake timers): (a) burst nhanh + Enter, focus ngoài input → `onScan(code)` gọi + `active=true`; (b) gõ chậm (gap>50ms) → không gọi; (c) focus trong `<input>` → `onScan` KHÔNG gọi, `active=true`; (d) `active` tự tắt sau `ACTIVE_MS`; (e) `enabled=false` → không nghe.
- `ScannerGunBadge`: render 2 trạng thái.

## File
- Mới: `useScannerGun.ts`, `ScannerGunBadge.tsx`, `__tests__/useScannerGun.test.ts`, `__tests__/ScannerGunBadge.test.tsx`.
- Sửa: `ScanDispatchDialog.tsx`, `pages/StockDocumentsPage.tsx`, `pages/CreateReturnDialog.tsx`.
