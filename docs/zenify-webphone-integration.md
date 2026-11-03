# Tích hợp Zenify Voice SDK (webphone) — trang Đơn hàng Landing Page

> **Cập nhật 2026-07-15:** UI lõi gọi đã chuyển từ bộ UI Zenify (Bước 3.1) sang
> SDK headless + UI tự build theo theme ERP (Bước 3.2) — kiến trúc, state machine
> và độ phủ function xem `docs/zenify-sdk-full-bridge.md`. Phần token flow,
> iframe cô lập, env, RBAC bên dưới vẫn đúng; các mô tả về bộ UI/CSS vendor
> (Bootstrap/toastr/webphone.css) không còn áp dụng.

**Ngày:** 2026-07-14 · **Branch:** `feat/zenify-webphone` · **Tài liệu gốc:** HuongDanTichHopWebSDKv2.3_update_2026.docx

## 1. Mục tiêu

- Sale phụ trách đơn landing gọi điện cho khách ngay trên web ERP (click-to-call từ cột SĐT).
- Dùng **nguyên bộ UI/UX webphone của Zenify** (Bước 3.1 tài liệu) — không tự build dialpad.
- Không lộ `api-key`/`api-secret` ra client (bắt buộc theo tài liệu).

## 2. Ràng buộc kỹ thuật & quyết định kiến trúc

Bộ UI Zenify kéo theo jQuery + Bootstrap 4 CSS + Font Awesome + toastr load global.
Frontend hiện tại là React SPA + Tailwind 4: CSS ngoài `@layer` sẽ đè utilities
(bài học MR !843) → nhúng thẳng vào `index.html` sẽ vỡ giao diện toàn app.

**Quyết định: cô lập toàn bộ vendor stack trong iframe same-origin.**

- `frontend/public/webphone.html` — trang tĩnh chứa đúng bộ CDN + `<div id="phoneui">` theo tài liệu, kèm bridge script giao tiếp `postMessage` với app.
- Iframe mount **một lần ở app shell** (không phải trong trang landing) → đổi route SPA không unmount iframe, **không rớt cuộc gọi** giữa chừng.
- Iframe có `allow="microphone"`; ẩn/hiện bằng CSS, không remove khỏi DOM.

## 3. Kiến trúc

```
[OrderCell nút gọi] → useWebphone().dialCall(phone)
        │ postMessage {type:"webphone:dial", phone}
        ▼
[iframe /webphone.html]  ← CDN Zenify (jQuery/Bootstrap/jssip/webphone.js/sdk.min.js)
        │ postMessage {type:"webphone:event", msg} (ring/answer/end/error)
        ▼
[WebphoneProvider] — trạng thái cuộc gọi, toast, (phase 2: popup lịch sử đơn theo SĐT)

Token flow:
FE → POST /api/callcenter/token (JWT ERP, RBAC landing-orders:view)
BE → POST https://api.zenify.vn/api/v1/sdk/sign-in (domain/api-key/api-secret + email agent)
BE cache token Redis theo email (~71h, TTL Zenify 72h) → trả {token, ttl}
FE gửi token vào iframe → ZenifyApp.init(token)
```

## 4. Backend — app mới `apps/callcenter`

| Thành phần | Nội dung |
|---|---|
| `services.py` | `ZenifyError` + `sign_in(email)` (HttpClient timeout 10s, retry 1 — endpoint interactive) + `get_token(email, refresh)`: cache Redis key `zenify:token:{email}` lưu `expires_at`, trả **ttl còn lại**; single-flight bằng `cache.add` chống thundering-herd khi dùng chung 1 agent email; lỗi Zenify passthrough nguyên văn |
| `views.py` | `POST /api/callcenter/token` — `IsAuthenticated` + `require_section("landing-orders")`; view mỏng, chỉ resolve email + parse `?refresh=1` rồi gọi `services.get_token` |
| `urls.py` + `config/urls.py` | route `api/callcenter/` |
| Env (settings_parts/base.py) | `ZENIFY_API_BASE_URL` (default `https://api.zenify.vn`), `ZENIFY_DOMAIN`, `ZENIFY_API_KEY`, `ZENIFY_API_SECRET`, `ZENIFY_AGENT_EMAIL` (email agent dùng chung — chỉ dùng khi user chưa có `zenify_email`; cả hai trống thì dùng email đăng nhập ERP của user), `ZENIFY_TOKEN_TTL_SECONDS` (default 259200) |

Chưa cấu hình env → endpoint trả 503 kèm message rõ ràng; FE ẩn toàn bộ tính năng.

**Khách cúp máy / kết thúc cuộc gọi (2026-09-04):** SDK có 2 kênh event. Socket (`ZenifyApp.subscribe`) trả object `{objectId, data}` — iframe map RING/ANSWER/END_CALL… Tầng SIP (`ZenifyPhone.subscribe`) trả **chuỗi** `"ended"`/`"failed"` khi JsSIP session kết thúc (BYE từ đầu xa) — bộ UI gốc của vendor reset màn hình từ đúng event này, còn socket không chắc gửi `USER_END_CALL`. Iframe `webphone.html` subscribe cả hai; event SIP forward thành `kind:"end"` không `callId` nên reducer luôn về idle. Khi máy trạng thái về idle, `WebphoneProvider` tự thu gọn panel (không bật lại bàn phím); muốn quay số lạ dùng nút "Quay số" trên toolbar trang đơn (`OpenDialpadButton`, bên trái "Nhập đơn") hoặc launcher góc phải.

**"Tút liên hồi dù không có cuộc gọi" (2026-09-04):** gọi ra = socket OUT_CALL → PBX INVITE vào WebRTC của agent (leg SIP, auto-answer sau 2s) → PBX mới quay số khách và phát ringback/tút bận vào leg đó. Khách không bắt máy / lỗi 480, 408… → socket báo `OUTCALL_ERROR`, reducer về idle và panel không còn nút Tắt máy, nhưng leg SIP vẫn mở nên PBX phát tút bận cho tới khi agent BYE. Fix: khi máy trạng thái rời trạng thái gọi về idle (mọi lý do), provider gửi `webphone:ensure-hangup`; iframe kiểm tra `ZenifyPhone.session`, còn sống thì `ZenifyApp.terminate()` + `ZenifyPhone.terminate_call()` (BYE). Test giao thức iframe chạy script inline của `webphone.html` trong jsdom: `__tests__/webphone-iframe.test.ts`.

**Nhận cuộc gọi đến (2026-09-04):** luồng UI đã có (RING `CALL_INBOUND` → panel bung, Nghe máy = `answerCall`), nhưng PBX chỉ đổ chuông vào (1) agent trạng thái `AVAILABLE` và (2) tab đang giữ quyền nghe gọi (`activateVoice` → server `VOICE_ACTIVATED` → SDK register SIP). Trước đây activate chỉ chạy lúc bấm gọi ra và panel không hiện trạng thái. Nay iframe forward `logged-in` (trạng thái agent từ LOGIN_RESPOND, NOACD → AVAILABLE) và `voice-activated`; provider tự activate 1 lần ~1.5s sau login nếu server chưa báo tab đã activated (không lặp, tránh 2 tab giành qua lại); `AgentStatusBar` trong panel idle cho chọn Sẵn sàng / Không nhận cuộc gọi và hiện banner "Kích hoạt" khi tab khác đang giữ quyền. Lưu ý: mở tab ERP mới sẽ giành quyền nghe gọi về tab đó.

**Đồng hồ cuộc gọi:** `startedAt` đặt tại event socket `AGENT_ANSWER_RESPOND` (`kind:"answer"`) — cùng mốc bộ UI gốc của vendor bật timer; với gọi ra đây là lúc cuộc gọi được nối (khách nhấc máy), không phải lúc leg SIP của agent auto-answer. Provider `console.debug("[webphone]", kind, objectId, data)` mọi event (DevTools mức Verbose) để đối chiếu mốc thật khi cần.

**Kết quả cuộc gọi & launcher (2026-09-04):** cuộc gọi kết thúc khi CHƯA được nghe → panel giữ mở, hiện `CallOutcomeBanner` (Máy bận / Khách từ chối / Không bắt máy / Cuộc gọi nhỡ…) kèm Gọi lại, tự thu gọn sau 8s. Lý do lấy theo `call-outcome.ts`: mã OUTCALL_ERROR (486/488/480/408…) → từ khoá trong data event end (status/reason/cause) → mặc định theo hướng gọi. Iframe gắn listener lên `ZenifyPhone.session` lúc SDK báo `newRTCSession` để lấy cause JsSIP ("Busy", "Rejected", "Canceled"…) đưa vào `data.reason` của event SIP. Sau login, agent đang NOT AVAILABLE/LOGOUT được tự đưa về AVAILABLE 1 lần (`shouldForceAvailableOnLogin`). Đã bỏ nút launcher nổi góc phải: mở panel bằng "Quay số" trên toolbar trang đơn, icon gọi cạnh SĐT, hoặc khi có cuộc gọi đến / kết quả cuộc gọi.

**Đa tab ERP — một tab chủ (2026-09-04):** log prod cho thấy mỗi tab ERP tự đăng nhập Zenify với cùng agent → tab sau bị "Voice tab is not activated" hoặc server ngắt phiên cũ ("io server disconnect", socket.io không tự nối lại). Nay `WebphoneProvider` bầu tab chủ bằng Web Locks (`desi-webphone-leader`): tab chủ chạy `WebphoneLeaderProvider` (iframe Zenify, đăng nhập, phát `SharedWebphoneState` qua BroadcastChannel `desi-webphone`); các tab khác chạy `WebphoneFollowerProvider` (không iframe, hiển thị state nhận được, chuyển thao tác gọi/nghe/tắt/đổi trạng thái sang tab chủ, panel ghi "Âm thanh cuộc gọi phát ở tab ERP đang giữ tổng đài"). Tab chủ đóng → khoá nhả → tab phụ đang chờ tự lên làm chủ và đăng nhập lại. Không có Web Locks (http dev) → mọi tab làm chủ như cũ. Xem `webphone-tabs.ts`, test `webphone-tabs.test.ts` + `WebphoneFollowerProvider.test.tsx` (BroadcastChannel/Web Locks giả trong `__tests__/fake-tabs.ts`).

**Nhật ký chẩn đoán (2026-09-07):** `webphone-diag.ts` giữ ring buffer 400 dòng trong tab: mọi message iframe ↔ app, `console.*` của SDK trong iframe (webphone.html chuyển tiếp qua `webphone:log`), vai tab chủ/phụ, thao tác liên tab. Nút 📋 "Sao chép nhật ký tổng đài" trên header khung Tổng đài copy ra clipboard (fallback in `console.warn`); DevTools: `__webphoneLog()`. Event `logged-in` kèm `agentId/autoAnswer/phoneType` từ `ZenifyAgent` — `autoAnswer=0` nghĩa là leg SIP gọi ra không tự nhấc (không nghe chuông chờ), `phoneType≠0` nghĩa là âm thanh không đi qua trình duyệt.

**Mapping user → agent Zenify:** thứ tự ưu tiên `user.zenify_email` (admin nhập ở Người dùng → Thêm/Sửa, ô "Email Zenify", từ 2026-09-04) → env `ZENIFY_AGENT_EMAIL` (nếu set) → `user.email`. Không có email nào → API token trả 400 với hướng dẫn nhờ admin nhập.
Follow-up (khi mỗi sale có tài khoản Zenify riêng): thêm field `zenify_email` per-user.

## 5. Frontend

| Thành phần | Nội dung |
|---|---|
| `public/webphone.html` | CDN theo Bước 3.1 + `#phoneui` + bridge: nhận `webphone:init{token}` / `webphone:dial{phone}` / `webphone:logout`; subscribe `onAppEvent` → forward `webphone:event` lên parent; map lỗi OUTCALL (401/402/…/608) → toastr trong iframe |
| `lib/api/callcenter.ts` | typed từ `schema.d.ts` (openapi-typescript) |
| `features/callcenter/queries.ts` | `callcenterKeys` + `useWebphoneToken` + `useRefreshWebphoneToken` (theo mẫu orders/queries.ts) |
| `features/callcenter/WebphoneProvider.tsx` | context `{enabled, visible, setVisible, dialCall}` (useMemo — không phá memo của OrderTableRow); iframe chỉ mount **sau khi có token** (không tải ~1MB vendor CDN khi chưa cấu hình); `enabled` yêu cầu SDK đã init xong; forced-logout/hết hạn → dừng hẳn + nút "Kết nối lại" (refresh=1, remount iframe); tự làm mới phiên trước hạn token 5'; `dialCall` gửi `activate` trước `dial` (giành tab nghe gọi khi mở nhiều tab) |
| Mount | `WebphoneProvider` bọc RootLayout (app shell) — iframe sống xuyên điều hướng, cuộc gọi không rớt |
| Click-to-call | `OrderCell`: helper `PhoneWithCall` dùng ở cột SĐT + cell gộp tên/SĐT; prop `canCall` chỉ bật ở trang landing (`isLandingPage`), markup cũ giữ nguyên cho các trang khác |
| SĐT khi gọi | `sanitizePhoneForDial` đổi `+84`/`84` → `0` (Zenify chỉ nhận định dạng nội địa — lỗi 420) |

## 6. Hạ tầng / deploy notes

- **Env prod cần thêm:** `ZENIFY_DOMAIN`, `ZENIFY_API_KEY`, `ZENIFY_API_SECRET` (+ `ZENIFY_AGENT_EMAIL` nếu dùng chung 1 agent). Chưa có → tính năng tự ẩn, không ảnh hưởng gì.
- Nginx: nếu bật CSP phải whitelist `cdn.zenify.biz`, `cdn.zenify.vn`, `ajax.googleapis.com`, `cdnjs.cloudflare.com`, `cdn.jsdelivr.net` + websocket wss tới Zenify. Hiện chưa thấy CSP header → không cần đổi.
- `webphone.html` được nginx trả `no-cache` (giống index.html) — file không có hash tên, cache stale sẽ lệch giao thức postMessage với bundle mới sau deploy.
- **Rủi ro chấp nhận (vendor trust):** iframe same-origin không cách ly bảo mật — script từ CDN Zenify/cdnjs chạy cùng origin ERP. Đây là bản chất của mọi SDK nhúng (tài liệu vendor còn yêu cầu nhúng thẳng vào trang chính — iframe đã là phương án chặt hơn). Follow-up nếu muốn siết: serve webphone từ subdomain riêng + đổi postMessage sang targetOrigin cụ thể.
- Mic yêu cầu HTTPS (prod đã có), localhost dev OK.
- Smoke test thật cần api-key/secret + tài khoản agent Zenify (demo vendor: `https://cdn.zenify.vn/test-sdk.html`).

## 7. Sprint & tiến độ

| # | Hạng mục | Trạng thái |
|---|---|---|
| 1 | Design doc | ✅ |
| 2 | BE `apps/callcenter` + tests | ✅ |
| 3 | `webphone.html` bridge | ✅ |
| 4 | FE provider/iframe/launcher + typed API | ✅ |
| 5 | Click-to-call cột SĐT landing | ✅ |
| 6 | Test BE + lint/build FE | ✅ |
| 7 | Smoke test với credentials thật | ⏳ chờ anh điền env |
| 8 | Phase 2: popup lịch sử đơn theo SĐT khi có cuộc gọi đến | ⏳ đề xuất, chưa làm |

## 8. Chưa làm / rủi ro treo

- Chưa smoke test được với Zenify thật (chưa có credentials) — bridge viết theo đúng tài liệu + demo test-sdk.html, cần 1 buổi test thật khi có key.
- Mapping per-user `zenify_email` để dành follow-up.
- Nhiều tab ERP mở cùng lúc: Zenify có `activateVoice()` để chọn tab chính — bridge đã expose, UI của họ tự xử lý; cần quan sát khi test thật.
