# Quét mã bằng camera điện thoại — Handoff desktop ↔ phone

- **Ngày:** 2026-06-25
- **Trạng thái:** Đã triển khai (Tasks 1–9 hoàn tất, gates xanh; còn manual smoke test trên HTTPS + điện thoại thật)
- **Branch dự kiến:** tạo mới từ `master`

## 1. Mục tiêu

Cho phép nhân viên đứng ở **desktop** mở một dialog quét (xuất kho / tạo phiếu kho / tạo phiếu trả), bấm nút **"Dùng điện thoại để quét"**, rồi dùng **camera điện thoại** làm đầu quét. Mỗi mã vận đơn quét được trên điện thoại sẽ hiện **realtime** vào danh sách trên desktop. Quét liên tục nhiều mã, sau đó người ở desktop xác nhận như luồng hiện tại.

Lý do: nhiều nhân viên không có súng quét chuyên dụng, nhưng ai cũng có điện thoại. Màn hình desktop lớn để rà soát/xác nhận; điện thoại nhỏ gọn để cầm đi quét.

## 2. Phạm vi

Áp dụng cho **cả 3 điểm quét** đang có trên desktop:

| # | Luồng | Điểm tích hợp (FE hiện có) | Handler nhận mã |
|---|-------|----------------------------|-----------------|
| 1 | Quét xuất kho (giao hàng) | `ScanDispatchDialog.tsx` | `handleScan(code)` |
| 2 | Quét thêm dòng vào phiếu kho | `StockDocumentsPage.tsx` (dialog tạo phiếu, ô quét ~L414) | `handleScanCode(code)` |
| 3 | Quét tạo phiếu trả hàng | `CreateReturnDialog.tsx` (~L274) | `handleScanCode(code)` |

Cả 3 đều đã dùng chung component `ScanShipmentInput` + API `lookupByShippingCode`. Tính năng mới **bổ sung song song** một nguồn mã thứ hai (điện thoại) feed vào đúng các handler đó. **Không thay đổi** logic nghiệp vụ hiện có.

### Ngoài phạm vi (YAGNI)
- Không làm pairing nhiều điện thoại cùng lúc vào 1 phiên.
- Không lưu lịch sử phiên quét vào DB (chỉ ephemeral qua Redis cache + TTL).
- Không làm app native; chỉ web (PWA/responsive trên trình duyệt điện thoại).
- Không làm offline/queue khi mất mạng (báo lỗi + cho quét lại).

## 3. Bối cảnh & hạ tầng tái dùng

Codebase đã có sẵn (xác minh ngày 2026-06-25):
- **Django Channels 4.3 + channels-redis + daphne/uvicorn (ASGI)** đang chạy: `config/asgi.py`, app `apps/channels_core` (consumer `ws/crm/`, `routing.py`, `jwt_middleware.py` đọc `?token=<jwt>`).
- **FE WebSocket client** `frontend/src/services/websocket.ts` (`WebSocketManager`, auth bằng `?token=`, tự reconnect, hỗ trợ `VITE_WS_BACKEND_URL` hoặc same-origin `/ws`).
- **Redis cache** (django_redis) đã cấu hình (dùng ở `customers/customer_loyalty_service.py`).
- API nghiệp vụ sẵn có: `GET /api/orders/lookup-by-shipping-code`, `POST /api/orders/dispatch-batch` (quyền `shipments:view`), `POST /api/inventory/documents`, `POST /api/inventory/returns`.
- Helper FE: `merge-order-lines.ts` (`mergeOrderItemsIntoLines`, `orderItemsAsLines`), `scan-sound.ts` (`beepSuccess`/`beepError`).

→ Tận dụng tối đa: WebSocket cho chiều server→desktop, REST cho chiều phone→server.

## 4. Quyết định thiết kế

1. **Transport realtime = WebSocket (Channels)**, KHÔNG polling. Thêm consumer mới `ws/scan/<token>/` để desktop lắng nghe.
2. **Điện thoại PHẢI đăng nhập** (JWT như desktop). Reuse `jwt_middleware` cho WS và `IsAuthenticated` cho REST.
3. **Token phiên = mã ghép cặp (pairing id)**, không phải secret cấp quyền. Vì cả hai đầu đều đã auth, token chỉ để **định danh đúng phiên nào** mã cần đẩy về. uuid4, lưu Redis, TTL 15 phút (refresh khi có hoạt động).
4. **Ràng buộc người dùng (đã chốt):** điện thoại **bắt buộc là cùng user** đã tạo phiên trên desktop. Consumer/view kiểm tra `session.user_id == request.user.id`; khác user → từ chối (WS `close(4403)`, REST `403`). Không hỗ trợ quét hộ giữa 2 người.
5. **Phân quyền theo mục đích phiên:** tạo/đẩy mã yêu cầu đúng quyền của luồng đó:
   - `dispatch` → `shipments:view`
   - `stock_document` → `stock-documents:create`
   - `return` → `returns:create`
   Thao tác ghi sổ thật (dispatch-batch / tạo phiếu) vẫn re-check quyền ở desktop như cũ.
6. **Thư viện đọc mã:** `@zxing/browser` (đa nền tảng, iOS Safari + Android, đọc Code128/QR…), có **feature-detect `BarcodeDetector` native** (Chrome Android) để nhanh hơn khi khả dụng. **Render QR:** `qrcode.react`. Cả hai **lazy-load**, không ảnh hưởng bundle desktop chính.

## 5. Kiến trúc tổng thể

```
[DESKTOP]                         [SERVER]                      [ĐIỆN THOẠI]
ScanDispatchDialog
  └ <PhoneScanButton>
     1. POST /api/scan-sessions ──► tạo token (Redis, TTL 15')
        {purpose:"dispatch"}        trả {token, relay_url}
     2. render QR(relay_url)
     3. mở WS ws/scan/<token>/ ───► ScanRelayConsumer
        (JWT, join group)            validate token+user → group_add
                                                                4. quét QR → mở
                                                                   /scan-relay/<token>
                                                                   (yêu cầu đăng nhập)
                                                                5. GET /api/scan-sessions/<token>
                                     validate ◄───────────────    → hiện nhãn mục đích
                                                                6. camera bật, quét mã
                                     7. POST .../<token>/codes ◄─   {code} (mỗi lần quét)
        9. onmessage scan.code ◄──── 8. group_send("scan.code")
        → handleScan(code)              (refresh TTL, rate-limit)
        → mã vào danh sách
  ...quét liên tục (lặp 6–9)...
  10. "Xác nhận xuất kho (N)" ─────► dispatch-batch (như cũ, quyền desktop)
```

## 6. Thiết kế Backend

App mới **`apps/scan_relay`** (module độc lập theo CLAUDE.md), gồm:

```
apps/scan_relay/
  __init__.py
  apps.py
  consumers.py          # ScanRelayConsumer
  routing.py            # websocket_urlpatterns cho ws/scan/<token>/
  services.py           # ScanSessionService (Redis cache, không có model DB)
  dto.py                # dataclass ScanSession
  transport/
    __init__.py
    views.py            # 3 REST endpoint
    serializers.py      # request/response serializers
  urls.py
  tests/
    test_session_service.py
    test_session_views.py
    test_consumer.py
```

### 6.1 ScanSessionService (`services.py`)
Lưu phiên trong Redis qua `django.core.cache` (django_redis). Không tạo model DB.

- `create(user_id: int, purpose: str) -> ScanSession`
  - validate `purpose ∈ {"dispatch","stock_document","return"}`.
  - `token = uuid4().hex`; key `scan:session:{token}` = `{user_id, purpose, created_at}`; `cache.set(key, data, timeout=900)`.
- `get(token: str) -> ScanSession | None` — đọc + None nếu hết hạn.
- `touch(token)` — gia hạn TTL 900s.
- `push_code(token, user_id, code)` — validate phiên + quyền user (cùng user) → `group_send(f"scan_relay_{token}", {"type":"scan.code","code":code})` + `touch`. Có **rate-limit** (đếm trong cache `scan:rl:{token}` ≤ ~10 mã/giây) để chống spam.
- `delete(token)` — xoá khi đóng dialog / xác nhận xong (best-effort; TTL tự dọn).

`group_send` lấy qua `channels.layers.get_channel_layer()` (async → bọc `async_to_sync`).

Map quyền theo `purpose` để view kiểm tra: `_PERMISSION_BY_PURPOSE = {"dispatch":"shipments:view","stock_document":"stock-documents:create","return":"returns:create"}`.

### 6.2 REST endpoints (`transport/views.py`)
Tất cả `@permission_classes([IsAuthenticated])` + kiểm tra quyền theo purpose trong handler.

| Method | Path | Auth | Chức năng |
|--------|------|------|-----------|
| POST | `/api/scan-sessions` | JWT + quyền theo purpose | Tạo phiên. Body `{purpose}`. Trả `{token, relay_url}` (relay_url = `<FE_ORIGIN>/scan-relay/<token>`; FE cũng có thể tự dựng từ `window.location.origin`, server trả kèm để tiện). |
| GET | `/api/scan-sessions/<token>` | JWT (cùng user) | Điện thoại kiểm tra phiên: trả `{purpose, purpose_label, valid:true}` hoặc 404. |
| POST | `/api/scan-sessions/<token>/codes` | JWT (cùng user) + quyền | Đẩy 1 mã: body `{code}`. group_send về desktop. Trả `{ok:true}`. |

Lỗi: 403 nếu sai quyền/khác user; 404 nếu token hết hạn/không tồn tại; 429 nếu vượt rate-limit.

### 6.3 ScanRelayConsumer (`consumers.py`)
`AsyncJsonWebsocketConsumer` (giống `CRMConsumer`):
- `connect`: lấy `user` từ scope; nếu chưa auth → `close(4401)`. Lấy `token` từ URL route. `session = ScanSessionService.get(token)`; nếu None hoặc `session.user_id != user.id` → `close(4403)`. Ngược lại `group_add(f"scan_relay_{token}")` + `accept()`.
- `scan_code(event)`: `await self.send_json({"type":"scan.code","code":event["code"]})`.
- `disconnect`: `group_discard`.

### 6.4 Routing / wiring
- `apps/scan_relay/routing.py`: `path("ws/scan/<str:token>/", ScanRelayConsumer.as_asgi())`.
- `config/asgi.py`: gộp `websocket_urlpatterns` của `channels_core` + `scan_relay` trong `URLRouter`.
- `config/urls.py`: include `apps.scan_relay.urls` dưới `/api/`.
- Đăng ký app trong `INSTALLED_APPS`.

## 7. Thiết kế Frontend

### 7.1 Dependencies mới
`@zxing/browser`, `qrcode.react` (lazy-load qua dynamic import / `React.lazy`).

### 7.2 Phía desktop — component dùng lại
Thư mục `frontend/src/features/inventory/scan/phone/`:

- **`scan-session-queries.ts`** — `createScanSession(purpose)`, `pushCode(token, code)`, `getScanSession(token)`.
- **`use-phone-scan-session.ts`** — hook:
  - input: `{ purpose, onCode }`.
  - `start()`: gọi `createScanSession` → nhận `{token, relay_url}` → mở `WebSocketManager("/ws/scan/<token>/")` → trên message `scan.code` gọi `onCode(code)`.
  - state phơi ra: `{ status: "idle"|"waiting"|"connected"|"error", relayUrl, scannedCount, start, stop }`.
  - `stop()`: đóng WS + `delete` phiên.
  - Tái dùng `WebSocketManager` (tách instance riêng, không đụng CRM singleton). Có thể cần generalize `services/websocket.ts` để tạo instance theo path tuỳ ý (đã có constructor nhận `baseUrl`).
- **`PhoneScanButton.tsx`** — nút "📱 Dùng điện thoại để quét" + Dialog hiển thị:
  - QR (`qrcode.react`) chứa `relayUrl`.
  - Trạng thái: "Đang chờ điện thoại kết nối…" → "Đã kết nối · đã quét N mã".
  - Nút Dừng/Đóng.
  - Props: `{ purpose, onCode }` → truyền thẳng `onCode` vào hook.

### 7.3 Phía điện thoại — trang camera
- **Route** `/scan-relay/:token` (public route nhưng **bọc auth guard**: nếu chưa đăng nhập → chuyển tới đăng nhập rồi quay lại URL này). Lazy-load.
- **`ScanRelayPage.tsx`**:
  - Gọi `getScanSession(token)` để validate + hiện nhãn mục đích ("Quét xuất kho" / …). Token hỏng → màn báo lỗi.
  - Render `<CameraScanner onCode={...} />` full-screen, mobile-first.
  - Mỗi mã đọc được: `beepSuccess()` + `pushCode(token, code)`; đếm "Đã quét N"; chống quét trùng (bỏ qua mã giống nhau trong ~1.5s).
  - **Ô nhập tay dự phòng**: input + nút "Gửi mã" để gõ/dán khi camera đọc không được.
- **`CameraScanner.tsx`**:
  - Feature-detect: `'BarcodeDetector' in window` → dùng native; ngược lại dynamic import `@zxing/browser` (`BrowserMultiFormatReader`).
  - `getUserMedia({ video: { facingMode: "environment" } })`.
  - Toggle đèn flash nếu `track.getCapabilities().torch`.
  - Xử lý từ chối quyền camera / không có camera → hiện hướng dẫn + dựa vào ô nhập tay.

### 7.4 Tích hợp vào 3 dialog
Cắm `<PhoneScanButton purpose=... onCode={<handler hiện có>} />` cạnh `ScanShipmentInput`:
- `ScanDispatchDialog.tsx` → `purpose="dispatch"`, `onCode={handleScan}`.
- `StockDocumentsPage.tsx` (dialog tạo phiếu) → `purpose="stock_document"`, `onCode={handleScanCode}`.
- `CreateReturnDialog.tsx` → `purpose="return"`, `onCode={handleScanCode}`.

## 8. Bảo mật

- Cả hai đầu (desktop, điện thoại) đều yêu cầu JWT hợp lệ; token_version được kiểm tra (reuse middleware).
- Token phiên uuid4, TTL ngắn, ràng buộc đúng user → người khác không nghe/đẩy nhầm phiên.
- Endpoint đẩy mã có rate-limit; thao tác ghi sổ thật vẫn qua RBAC ở desktop (điện thoại không tự xuất kho được).
- WS `/ws/scan/<token>/` từ chối (4401/4403) khi chưa auth hoặc sai chủ phiên.
- **HTTPS bắt buộc** cho `getUserMedia` (prod đã có nginx/https). Dev: dùng `localhost`/https hoặc tunnel.

## 9. Edge cases & lỗi

- Mất WS → `WebSocketManager` tự reconnect (đã có). Trong lúc rớt, mã từ điện thoại có thể trượt → chấp nhận (MVP); người dùng thấy "đã quét N" trên điện thoại nhưng desktop thiếu → quét lại. (Nâng cấp tương lai: buffer Redis + replay khi reconnect.)
- Mã không tra ra đơn → desktop hiện lỗi như luồng cũ (`handleScan` đã có `try/catch`).
- Quét trùng → desktop đã chặn trùng theo `order_id`; điện thoại chặn trùng theo chuỗi mã trong khoảng thời gian ngắn.
- Phiên hết hạn giữa chừng → endpoint trả 404, điện thoại báo "Phiên đã hết hạn, vui lòng quét lại QR".

## 10. Kế hoạch test

**Backend (pytest, theo memory: chạy qua image test trên Windows):**
- `ScanSessionService`: create/get/expire/permission-by-purpose/rate-limit.
- REST: tạo phiên (đúng/sai quyền), get token (cùng/khác user, hết hạn), push code (group_send được gọi — mock channel layer; 403/404/429).
- Consumer: connect auth ok/khước từ, nhận `scan.code` → send_json (dùng `ChannelsLiveServerTestCase`/`WebsocketCommunicator`).

**Frontend (vitest):**
- `use-phone-scan-session`: start → tạo phiên + nối WS (mock) → message `scan.code` gọi `onCode`.
- `CameraScanner`: nhánh feature-detect (mock `BarcodeDetector` có/không); chống trùng.
- `ScanRelayPage`: token hỏng → màn lỗi; ô nhập tay gửi mã gọi `pushCode`.
- `PhoneScanButton`: render QR đúng `relayUrl`, hiển thị trạng thái.

## 11. Open questions

Không còn (đã chốt: điện thoại phải cùng user — xem §4.4).

## 12. Tài liệu/đầu mối tham chiếu

- `apps/channels_core/consumers.py`, `routing.py`, `jwt_middleware.py`, `config/asgi.py`
- `apps/orders/transport/views/dispatch_batch_view.py` (perm `shipments:view`), `shipping_lookup_view.py`
- `frontend/src/services/websocket.ts`
- `frontend/src/features/inventory/scan/*` (ScanDispatchDialog, ScanShipmentInput, scan-queries, merge-order-lines, scan-sound)
- `frontend/src/features/inventory/pages/StockDocumentsPage.tsx`, `CreateReturnDialog.tsx`
