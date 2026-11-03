# Zenify Web SDK v2.3 — headless SDK + UI gọi theo theme ERP

**Ngày:** 2026-07-15 · **Branch:** `feat/zenify-sdk-full-bridge` · **Tài liệu gốc:** HuongDanTichHopWebSDKv2.3_update_2026.docx
**Nền tảng:** thay lớp UI của tích hợp iframe hiện có — xem `docs/zenify-webphone-integration.md`

## 1. Mục tiêu & phạm vi (chốt 2026-07-15)

1. Chuyển iframe từ **Bước 3.1** (nguyên bộ UI Zenify) sang **Bước 3.2** (SDK headless),
   tự build UI lõi gọi bằng shadcn theo theme ERP.
2. UI phạm vi **cơ bản**: dialpad gọi ra, màn hình cuộc gọi đến (nghe/từ chối),
   trong cuộc gọi mute/hold/tắt máy + đồng hồ. Chuyển máy/mời/giám sát/trạng thái
   agent: **chưa làm UI**, nhưng bridge nối sẵn đủ 100% function của doc.
3. Backend/swagger không đổi (doc chỉ có 1 API server sign-in, đã có).
4. Credentials Zenify đã test được trên prod → smoke test thật là bước chốt trước merge.

## 2. Kiến trúc

```
[CallPhoneButton / WebphonePanel (React, shadcn)]
        │ useWebphone(): callState, answer/hangup/toggleMute/toggleHold/dialCall...
        ▼
[WebphoneProvider] — state machine cuộc gọi + toast lỗi (sonner, message TS constants)
        │ postMessage: webphone:init/dial/command/agent-status/phone-type/activate/logout
        ▼
[iframe /webphone.html — VÔ HÌNH, headless]  jQuery + socket.io + jssip + webphone.js + sdk.min.js
        │ forward NGUYÊN data mọi event onAppEvent → webphone:event {kind, objectId, data}
        ▲ giữ mounted xuyên điều hướng: WebRTC audio + socket sống trong iframe
```

- Iframe vẫn same-origin, `allow="microphone; autoplay"`, mount 1 lần ở app shell,
  **ẩn hẳn** (kích thước 0) — chỉ còn là engine âm thanh/kết nối.
- Bỏ khỏi webphone.html: Bootstrap CSS/JS, popper, Font Awesome, webphone.css,
  easytimer, toastr, `#phoneui`. Giữ jQuery (webphone.js phụ thuộc `$`, doc dặn giữ).
- Token flow giữ nguyên (`POST /api/callcenter/token`, refresh trước hạn 5').

## 3. Bridge protocol (webphone-bridge.ts)

Outbound giữ 4 message cũ, thêm:

```ts
| { type: "webphone:command"; method: WebphoneCommandMethod; args?: (string | number)[] }
| { type: "webphone:agent-status"; status: ZenifyAgentStatus }   // changeAgentStatus({status})
| { type: "webphone:phone-type"; phoneType: 0 | 1 }              // voiceSetting({phoneType})
```

`WebphoneCommandMethod` (whitelist trong iframe, đủ các function còn lại của doc):
`answerCall`, `terminate`, `hold`, `unhold`, `mute`, `unmute`, `register`,
`unregister`, `transferToAgent`, `transferToAcd`, `inviteAgent`, `invitePhone`,
`transferPhone`, `spy`, `join`.

Inbound `webphone:event` mở rộng `kind`:
`ring | answer | end | outcall-error | outcall-response | notification | agent-status | phone-type`
— forward **nguyên `msg.data`** (ring: callId/caller/callType/queueId/called…).
Iframe không còn toastr: mapping mã lỗi → message tiếng Việt (17 mã OUTCALL,
5 mã OUT_CALL_RESPONSE, agent_reject/phone_failed) chuyển thành constants TS,
toast bằng sonner trong provider.

## 4. State machine cuộc gọi (WebphoneProvider)

```
idle ──dialCall──▶ dialing ──answer event──▶ in-call ──end/lỗi──▶ idle
idle ◀──ring event── ringing ──answerCall──▶ in-call
                     ringing ──terminate/end──▶ idle
```

- `currentCall: { direction: "in" | "out"; phone; callId?; startedAt? }`;
  `startedAt` set khi answer → đồng hồ React tự đếm (thay easytimer).
- Mute/hold là state lạc quan phía app (SDK không có event xác nhận riêng theo doc);
  reset khi kết thúc cuộc gọi.
- `useWebphone()` expose thêm: `callState`, `currentCall`, `answer()`, `hangup()`
  (= terminate, dùng cho cả từ chối), `toggleMute()`, `toggleHold()`,
  `sendCommand(method, args?)`, `setAgentStatus()`, `setPhoneType()`,
  `agentStatus`, `phoneType` (từ event, chưa có UI — để cụm sau).
- Forced logout / hết hạn token: giữ cơ chế hiện có (sessionEnded + "Kết nối lại").
- Cuộc gọi đến tự bung panel (giữ hành vi cũ).

## 5. UI panel "Tổng đài" (WebphonePanel.tsx — thay iframe hiển thị)

| Trạng thái | Nội dung |
|---|---|
| idle | Dialpad: ô nhập số + phím 0-9*# + nút Gọi (primary) |
| ringing (đến) | SĐT khách nổi bật + nút Nghe máy (xanh) / Từ chối (đỏ) |
| dialing / in-call | SĐT, nhãn "Đang đổ chuông…" / đồng hồ MM:SS, hàng nút Mute · Hold · Tắt máy |

- shadcn Button + lucide icons, tông xanh ERP; panel giữ khung nổi + launcher hiện có.
- File tách nhỏ: `WebphonePanel.tsx` (khung + switch state), `Dialpad.tsx`,
  `CallScreen.tsx`, `webphone-messages.ts` (constants lỗi), giữ mỗi file <300 dòng.
- `CallPhoneButton` và luồng click-to-call từ bảng đơn giữ nguyên API.

## 6. Rủi ro & biện pháp

| Rủi ro | Biện pháp |
|---|---|
| Headless chưa từng chạy thật (UI Zenify từng lo edge case) | Credentials prod đã có → smoke test bắt buộc trước merge: gọi ra, nghe vào, mute/hold, từ chối, khách cúp máy, mất mạng |
| Autoplay chuông cuộc gọi đến bị browser chặn | Giữ `allow="autoplay"`; nếu webphone.js không tự phát chuông headless → phát ringtone từ app (đã có user gesture từ phiên làm việc) — kiểm chứng lúc smoke test |
| webphone.js có thể vẫn cần `#phoneui`/jQuery | Giữ jQuery + giữ `<div id="phoneui">` rỗng ẩn trong iframe (vô hại, tránh lỗi selector null) |
| Cache webphone.html lệch bundle sau deploy | Chỉ THÊM message type; type cũ (`init/dial/activate/logout`) giữ nguyên ngữ nghĩa |
| App không phân biệt được end-của-cuộc-nào khi event dồn | So `callId` trong event end với `currentCall.callId` trước khi reset state |

## 7. Test

- `webphone-bridge` unit: type guard kind mới, mapping constants lỗi.
- `WebphoneProvider` (mock postMessage, pattern test hiện có): state machine đủ
  các chuyển tiếp (ring→answer→end, dial→end, reject, lỗi outcall về idle),
  command post đúng payload, toast gọi đúng message.
- `Dialpad`/`CallScreen` render test cơ bản.
- Smoke test thủ công trên prod credentials (mục 6) — điều kiện merge.

## 8. Sprint

| # | Hạng mục | Trạng thái |
|---|---|---|
| 1 | Design doc v2 (file này) | ✅ |
| 2 | Bridge: types + iframe headless + forward full event + command whitelist | ✅ |
| 3 | Provider: state machine + toast constants + API mới | ✅ |
| 4 | UI: WebphonePanel/Dialpad/CallScreen | ✅ |
| 5 | Tests + lint + build (21 test callcenter mới; full suite 438 pass; build sạch) | ✅ |
| 6 | Cập nhật docs/zenify-webphone-integration.md trỏ sang kiến trúc mới | ✅ |
| 7 | Smoke test prod credentials → merge | ⏳ điều kiện merge |
