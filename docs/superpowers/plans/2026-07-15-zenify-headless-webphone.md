# Zenify Headless Webphone + ERP Call UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chuyển iframe Zenify từ UI vendor (Bước 3.1) sang SDK headless (Bước 3.2), tự build UI gọi cơ bản (dialpad / cuộc gọi đến / trong cuộc gọi) bằng shadcn theme ERP, bridge nối đủ 100% function doc v2.3.

**Architecture:** Iframe same-origin vô hình chỉ còn là engine (jssip/socket/webphone.js), forward nguyên mọi event qua postMessage. Reducer thuần `webphone-call-state.ts` dựng state machine cuộc gọi; `WebphoneProvider` dispatch event + side effect (toast sonner, postMessage); `WebphonePanel` render UI theo state. Spec: `docs/zenify-sdk-full-bridge.md`.

**Tech Stack:** React 19 + TS, shadcn/ui Button + Input, lucide-react, sonner, vitest + @testing-library/react.

## Global Constraints

- Không đổi backend/swagger — chỉ frontend + `frontend/public/webphone.html`.
- Message type cũ (`webphone:init/dial/activate/logout`, `webphone:ready/initialized/logged-out/sdk-error/event`) giữ nguyên ngữ nghĩa — chỉ THÊM (cache webphone.html không hash tên).
- File mới < 300 dòng; component nhỏ, tách file.
- Test đặt trong `frontend/src/features/callcenter/__tests__/`.
- KHÔNG dùng react-select; icon dùng lucide-react.
- Commit message tiếng Anh, ngắn, không Co-Authored-By.
- Chạy lệnh FE trong `frontend/` (`npm run test`, `npm run lint`, `npm run build`).

---

### Task 1: Bridge types + bảng message lỗi TS

**Files:**
- Modify: `frontend/src/features/callcenter/webphone-bridge.ts`
- Create: `frontend/src/features/callcenter/webphone-messages.ts`
- Test: `frontend/src/features/callcenter/__tests__/webphone-messages.test.ts`

**Interfaces:**
- Produces: `WebphoneCommandMethod`, `ZenifyAgentStatus`, `WebphoneEventKind`, union mở rộng `WebphoneOutboundMessage`/`WebphoneInboundMessage`; `outcallErrorMessage(code): {message, type} | undefined`, `outcallResponseMessage(code): string | undefined`, `NOT_READY_MESSAGE`, `AGENT_REJECT_MESSAGE`.

- [ ] **Step 1: Viết test fail** — `__tests__/webphone-messages.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { isWebphoneInboundMessage } from "../webphone-bridge";
import { outcallErrorMessage, outcallResponseMessage } from "../webphone-messages";

describe("outcallErrorMessage", () => {
  it("maps documented codes with type", () => {
    expect(outcallErrorMessage(402)).toEqual({ message: "Lỗi 402: hết hạn mức cước!", type: "error" });
    expect(outcallErrorMessage(430)).toEqual({
      message: "Info 430: cuộc gọi được chuyển hướng voicemail!",
      type: "info",
    });
  });
  it("returns undefined for unknown code", () => {
    expect(outcallErrorMessage(999)).toBeUndefined();
  });
});

describe("outcallResponseMessage", () => {
  it("maps documented request errors", () => {
    expect(outcallResponseMessage("IN_BLACKLIST")).toBe("Khách hàng trong blacklist!");
  });
  it("returns fallback for unknown non-OK code", () => {
    expect(outcallResponseMessage("WHATEVER")).toBe("Không thể kết nối tới WebPhone!");
  });
  it("returns undefined for OK", () => {
    expect(outcallResponseMessage("OK")).toBeUndefined();
  });
});

describe("isWebphoneInboundMessage", () => {
  it("accepts new event kinds", () => {
    expect(
      isWebphoneInboundMessage({ type: "webphone:event", kind: "agent-status", data: { status: "MEETING" } }),
    ).toBe(true);
  });
});
```

- [ ] **Step 2: Chạy fail** — `cd frontend && npx vitest run src/features/callcenter/__tests__/webphone-messages.test.ts` → FAIL (module `webphone-messages` chưa tồn tại).

- [ ] **Step 3: Implement.** `webphone-bridge.ts` — thay 2 union type:

```ts
/** 15 function SDK stateless — whitelist trong iframe (webphone.html). */
export type WebphoneCommandMethod =
  | "answerCall"
  | "terminate"
  | "hold"
  | "unhold"
  | "mute"
  | "unmute"
  | "register"
  | "unregister"
  | "transferToAgent"
  | "transferToAcd"
  | "inviteAgent"
  | "invitePhone"
  | "transferPhone"
  | "spy"
  | "join";

/** Trạng thái agent theo doc v2.3 (CHANGE_AGENT_STATUS_RESPOND_ID). */
export type ZenifyAgentStatus =
  | "AVAILABLE"
  | "NOT AVAILABLE"
  | "NOACD"
  | "ONCAMPAIGN"
  | "TYPING"
  | "ATLUNCH"
  | "MEETING"
  | "TRAINING"
  | "GOOUT"
  | "LOGOUT";

/** Tên ngữ nghĩa map từ ZenifyConfig trong iframe (app không thấy ZenifyConfig). */
export type WebphoneEventKind =
  | "ring"
  | "answer"
  | "end"
  | "outcall-error"
  | "outcall-response"
  | "notification"
  | "agent-status"
  | "phone-type";

/** App → iframe. */
export type WebphoneOutboundMessage =
  | { type: "webphone:init"; token: string }
  | { type: "webphone:dial"; phone: string }
  | { type: "webphone:activate" }
  | { type: "webphone:logout" }
  | { type: "webphone:command"; method: WebphoneCommandMethod; args?: (string | number)[] }
  | { type: "webphone:agent-status"; status: ZenifyAgentStatus }
  | { type: "webphone:phone-type"; phoneType: 0 | 1 };

/** Iframe → app. */
export type WebphoneInboundMessage =
  | { type: "webphone:ready" }
  | { type: "webphone:initialized" }
  | { type: "webphone:logged-out" }
  | { type: "webphone:sdk-error"; message: string }
  | { type: "webphone:event"; kind?: WebphoneEventKind; objectId?: string; data?: Record<string, unknown> };
```

(`isWebphoneInboundMessage`, `sanitizePhoneForDial` giữ nguyên.)

`webphone-messages.ts` (mới) — copy nguyên văn message từ doc v2.3:

```ts
/**
 * Bảng message lỗi/thông báo Zenify theo doc v2.3 — iframe headless không còn
 * toastr, provider toast bằng sonner với các message này.
 */

const OUTCALL_ERRORS: Record<number, string> = {
  401: "Lỗi 401: không thể gọi ra, do đầu số/IP tổng đài của khách hàng chưa đăng ký với Hub!",
  402: "Lỗi 402: hết hạn mức cước!",
  403: "Lỗi 403: hub không chấp nhận cuộc gọi do chưa khai ip hoặc lý do khác!",
  404: "Lỗi 404: không có routing rule nào trên Hub, hoặc không còn số để gọi ra!",
  408: "Lỗi 408: không có phản hồi!",
  420: "Lỗi 420: thuê bao không đúng định dạng 09xx (10 chữ số) và 02xxx (11 hoặc 12 chữ số)!",
  430: "Info 430: cuộc gọi được chuyển hướng voicemail!",
  480: "Lỗi 480: thuê bao không liên lạc được!",
  485: "Lỗi 485: thuê bao không tồn tại!",
  486: "Lỗi 486: người nhận từ chối trả lời cuộc gọi!",
  487: "Lỗi 487: cuộc gọi bị ngắt!",
  488: "Lỗi 488: thuê bao bận!",
  500: "Lỗi 500: hệ thống Telco đang gặp sự cố, vui lòng thử lại sau!",
  503: "Lỗi 503: kết nối đến thống Hub tổng đài gặp sự cố, vui lòng thử lại sau!",
  603: "Lỗi 603: đầu số đã bị khóa bởi mạng của người nhận!",
  607: "Lỗi 607: khách hàng đăng ký không nhận cuộc gọi này!",
  608: "Lỗi 608: hệ thống đang quá tải, vui lòng thử lại sau!",
};

const OUTCALL_REQUEST_ERRORS: Record<string, string> = {
  IN_BLACKLIST: "Khách hàng trong blacklist!",
  IN_INVALID_QUEUE: "Đầu số gọi ra không được phép!",
  GATEWAY_INACTIVE: "Không thể kết nối tới SipTrunk!",
  PERMISSION_DENY: "Không được phép gọi ra!",
  INCALL: "Bạn đang trong cuộc gọi khác!",
};

export const NOT_READY_MESSAGE = "Webphone chưa sẵn sàng, thử lại sau vài giây.";
export const AGENT_REJECT_MESSAGE =
  "Bạn đã từ chối cuộc gọi nhiều lần! Hệ thống tự chuyển trạng thái Không sẵn sàng.";

/** Lỗi gọi ra (OUTCALL_ERROR_RESPONSE_ID); 430 là info (voicemail). */
export function outcallErrorMessage(code: number): { message: string; type: "error" | "info" } | undefined {
  const message = OUTCALL_ERRORS[code];
  if (!message) return undefined;
  return { message, type: code === 430 ? "info" : "error" };
}

/** Kết quả yêu cầu gọi ra (OUT_CALL_RESPONSE_ID) — undefined nếu OK. */
export function outcallResponseMessage(code: string): string | undefined {
  if (code === "OK") return undefined;
  return OUTCALL_REQUEST_ERRORS[code] ?? "Không thể kết nối tới WebPhone!";
}
```

- [ ] **Step 4: Chạy pass** — cùng lệnh Step 2 → PASS.
- [ ] **Step 5: Commit** — `git add frontend/src/features/callcenter && git commit -m "feat(callcenter): bridge command types and zenify error message table"`

---

### Task 2: webphone.html headless (Bước 3.2)

**Files:**
- Rewrite: `frontend/public/webphone.html`

**Interfaces:**
- Consumes: message type outbound Task 1.
- Produces: forward event `webphone:event {kind, objectId, data}` với kind Task 1; giữ `webphone:ready/initialized/logged-out/sdk-error`.

- [ ] **Step 1: Viết lại file** (không unit test được file html — kiểm bằng build + smoke test Task 5; giữ logic tối giản, mọi lời gọi ZenifyApp bọc try/catch):

```html
<!doctype html>
<html lang="vi">

<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Webphone</title>
  <!--
    Iframe HEADLESS (Bước 3.2 tài liệu HuongDanTichHopWebSDKv2.3): chỉ còn là
    engine kết nối/âm thanh của Zenify SDK, KHÔNG render UI — UI gọi do app
    React dựng (WebphonePanel). Chạy trong iframe same-origin ẩn, giữ mounted
    xuyên điều hướng SPA để không rớt cuộc gọi.
    Giao tiếp với app qua postMessage — xem src/features/callcenter/webphone-bridge.ts.
    jQuery giữ lại vì webphone.js phụ thuộc; toastr/bootstrap/webphone.css đã bỏ
    (iframe vô hình, thông báo lỗi do app toast bằng sonner).
  -->
  <script src="https://ajax.googleapis.com/ajax/libs/jquery/3.6.4/jquery.min.js"></script>
  <script src="https://cdn.zenify.biz/socket.io-2.5.0.js"></script>
  <script src="https://cdn.zenify.biz/jssip-3.10.0.min.js"></script>
  <script src="https://cdn.zenify.biz/webphone.js"></script>
  <script src="https://cdn.zenify.biz/sdk.min.js"></script>
  <style>
    body { margin: 0; background: transparent; }
    /* Phòng khi webphone.js vẫn render gì đó vào #phoneui — không được hiện. */
    #phoneui { display: none; }
  </style>
</head>

<body>
  <div id="phoneui"></div>

  <script type="text/javascript">
    (function () {
      var ORIGIN = window.location.origin; // iframe same-origin với app
      var SDK_LOAD_ERROR = "Không tải được Zenify SDK (kiểm tra kết nối tới cdn.zenify.biz).";
      var initialized = false;

      // 15 function stateless cho message webphone:command — ngoài danh sách bỏ qua.
      var COMMANDS = {
        answerCall: 1, terminate: 1, hold: 1, unhold: 1, mute: 1, unmute: 1,
        register: 1, unregister: 1,
        transferToAgent: 1, transferToAcd: 1, inviteAgent: 1, invitePhone: 1, transferPhone: 1,
        spy: 1, join: 1
      };

      function send(msg) {
        try { window.parent.postMessage(msg, ORIGIN); } catch (e) { /* ignore */ }
      }

      function sendEvent(kind, objectId, data) {
        send({ type: "webphone:event", kind: kind, objectId: objectId, data: data || {} });
      }

      function eventKind(msg) {
        if (!window.ZenifyConfig || !msg) return undefined;
        switch (msg.objectId) {
          case ZenifyConfig.RING_AGENT_RESPOND_ID: return "ring";
          case ZenifyConfig.AGENT_ANSWER_RESPOND_ID: return "answer";
          case ZenifyConfig.AGENT_END_CALL_RESPOND_ID:
          case ZenifyConfig.USER_END_CALL_RESPOND_ID:
          case ZenifyConfig.END_CALL_RESPOND_ID: return "end";
          case ZenifyConfig.OUTCALL_ERROR_RESPONSE_ID: return "outcall-error";
          case ZenifyConfig.OUT_CALL_RESPONSE_ID: return "outcall-response";
          case ZenifyConfig.USER_PHONE_SELECT: return "phone-type";
          default: return undefined;
        }
      }

      function onAppEvent(msg) {
        if (!msg || !window.ZenifyConfig) return;

        // 2 case cần lọc theo agent hiện tại trước khi forward.
        if (msg.objectId === ZenifyConfig.NEW_NOTIFICATION_ID) {
          var d = msg.data || {};
          if (d.type === "phone_failed" && d.title) {
            sendEvent("notification", msg.objectId, { type: "phone_failed", title: String(d.title) });
          }
          if (d.type === "agent_reject" && window.ZenifyAgent && ZenifyAgent.agentId === d.agentId) {
            sendEvent("notification", msg.objectId, { type: "agent_reject" });
          }
          return;
        }
        if (msg.objectId === ZenifyConfig.CHANGE_AGENT_STATUS_RESPOND_ID) {
          var data = msg.data || {};
          var mine = window.ZenifyAgent && ZenifyAgent.agentId === data.agentId;
          if (Number(data.errorcode) === 0 && mine) {
            if (data.status === "LOGOUT") {
              // Bị ép đăng xuất từ hệ thống Zenify.
              try { ZenifyApp.logout(); } catch (e) { /* ignore */ }
              initialized = false;
              send({ type: "webphone:logged-out" });
            } else {
              sendEvent("agent-status", msg.objectId, { status: data.status });
            }
          }
          return;
        }

        var kind = eventKind(msg);
        if (kind) sendEvent(kind, msg.objectId, msg.data || {});
      }

      window.addEventListener("message", function (event) {
        if (event.origin !== ORIGIN) return;
        var msg = event.data;
        if (!msg || typeof msg !== "object") return;

        if (!window.ZenifyApp) {
          send({ type: "webphone:sdk-error", message: SDK_LOAD_ERROR });
          return;
        }

        switch (msg.type) {
          case "webphone:init": {
            if (initialized) break;
            try {
              ZenifyApp.subscribe(onAppEvent);
              ZenifyApp.init(String(msg.token || ""));
              initialized = true;
              send({ type: "webphone:initialized" });
            } catch (e) {
              send({ type: "webphone:sdk-error", message: "Init webphone lỗi: " + (e && e.message ? e.message : e) });
            }
            break;
          }
          case "webphone:dial": {
            if (!initialized) {
              sendEvent("notification", undefined, { type: "not_ready" });
              break;
            }
            try { ZenifyApp.dialCall(String(msg.phone || "")); } catch (e) {
              send({ type: "webphone:sdk-error", message: "Không gọi được: " + (e && e.message ? e.message : e) });
            }
            break;
          }
          case "webphone:command": {
            if (!initialized) {
              sendEvent("notification", undefined, { type: "not_ready" });
              break;
            }
            if (!COMMANDS[msg.method] || typeof ZenifyApp[msg.method] !== "function") break;
            try { ZenifyApp[msg.method].apply(ZenifyApp, msg.args || []); } catch (e) { /* ignore */ }
            break;
          }
          case "webphone:agent-status": {
            if (!initialized) break;
            try { ZenifyApp.changeAgentStatus({ status: String(msg.status || "") }); } catch (e) { /* ignore */ }
            break;
          }
          case "webphone:phone-type": {
            if (!initialized) break;
            try { ZenifyApp.voiceSetting({ phoneType: Number(msg.phoneType) }); } catch (e) { /* ignore */ }
            break;
          }
          case "webphone:activate": {
            // Chuyển tab này thành tab chính để nghe cuộc gọi (nhiều tab ERP).
            try { ZenifyApp.activateVoice(); } catch (e) { /* ignore */ }
            break;
          }
          case "webphone:logout": {
            try { ZenifyApp.logout(); } catch (e) { /* ignore */ }
            initialized = false;
            break;
          }
          default:
            break;
        }
      });

      window.addEventListener("load", function () {
        if (window.ZenifyApp) {
          send({ type: "webphone:ready" });
        } else {
          send({ type: "webphone:sdk-error", message: SDK_LOAD_ERROR });
        }
      });
    })();
  </script>
</body>

</html>
```

- [ ] **Step 2: Commit** — `git add frontend/public/webphone.html && git commit -m "feat(callcenter): headless webphone iframe, forward all sdk events"`

---

### Task 3: Reducer state machine cuộc gọi

**Files:**
- Create: `frontend/src/features/callcenter/webphone-call-state.ts`
- Test: `frontend/src/features/callcenter/__tests__/webphone-call-state.test.ts`

**Interfaces:**
- Consumes: `WebphoneEventKind` (Task 1).
- Produces:

```ts
export type CallStatus = "idle" | "dialing" | "ringing" | "in-call";
export type CallInfo = { direction: "in" | "out"; phone: string; callId?: string; startedAt?: number };
export type CallMachineState = { status: CallStatus; call: CallInfo | null; muted: boolean; held: boolean };
export type CallMachineAction =
  | { type: "dial"; phone: string }
  | { type: "event"; kind: WebphoneEventKind; data: Record<string, unknown>; now: number }
  | { type: "toggle-mute" }
  | { type: "toggle-hold" }
  | { type: "reset" };
export const initialCallState: CallMachineState;
export function webphoneCallReducer(state: CallMachineState, action: CallMachineAction): CallMachineState;
```

- [ ] **Step 1: Viết test fail** — `__tests__/webphone-call-state.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import type { WebphoneEventKind } from "../webphone-bridge";
import {
  initialCallState,
  webphoneCallReducer,
  type CallMachineAction,
  type CallMachineState,
} from "../webphone-call-state";

const NOW = 1_752_500_000_000;

function run(state: CallMachineState, ...actions: CallMachineAction[]): CallMachineState {
  return actions.reduce(webphoneCallReducer, state);
}

const event = (kind: WebphoneEventKind, data: Record<string, unknown> = {}): CallMachineAction => ({
  type: "event",
  kind,
  data,
  now: NOW,
});

describe("webphoneCallReducer", () => {
  it("dial → dialing with outbound call", () => {
    const s = run(initialCallState, { type: "dial", phone: "0912345678" });
    expect(s).toEqual({
      status: "dialing",
      call: { direction: "out", phone: "0912345678" },
      muted: false,
      held: false,
    });
  });

  it("inbound ring → ringing with caller info", () => {
    const s = run(initialCallState, event("ring", { callId: "c1", caller: "0900000001", callType: "CALL_INBOUND" }));
    expect(s.status).toBe("ringing");
    expect(s.call).toEqual({ direction: "in", phone: "0900000001", callId: "c1" });
  });

  it("outbound ring keeps dialing and attaches callId", () => {
    const s = run(
      initialCallState,
      { type: "dial", phone: "0912345678" },
      event("ring", { callId: "c2", caller: "0912345678", callType: "CALL_OUTBOUND" }),
    );
    expect(s.status).toBe("dialing");
    expect(s.call?.callId).toBe("c2");
  });

  it("answer → in-call with startedAt", () => {
    const s = run(
      initialCallState,
      event("ring", { callId: "c1", caller: "0900000001", callType: "CALL_INBOUND" }),
      event("answer", { callId: "c1" }),
    );
    expect(s.status).toBe("in-call");
    expect(s.call?.startedAt).toBe(NOW);
  });

  it("end resets to idle (and clears mute/hold)", () => {
    const inCall = run(
      initialCallState,
      event("ring", { callId: "c1", caller: "0900000001", callType: "CALL_INBOUND" }),
      event("answer", { callId: "c1" }),
      { type: "toggle-mute" },
    );
    expect(inCall.muted).toBe(true);
    expect(run(inCall, event("end", { callId: "c1" }))).toEqual(initialCallState);
  });

  it("end of a DIFFERENT callId is ignored", () => {
    const inCall = run(
      initialCallState,
      event("ring", { callId: "c1", caller: "0900000001", callType: "CALL_INBOUND" }),
      event("answer", { callId: "c1" }),
    );
    expect(run(inCall, event("end", { callId: "other" }))).toBe(inCall);
  });

  it("outcall-error ends the attempt, except 430 voicemail info", () => {
    const dialing = run(initialCallState, { type: "dial", phone: "0912345678" });
    expect(run(dialing, event("outcall-error", { errorCode: "486" }))).toEqual(initialCallState);
    expect(run(dialing, event("outcall-error", { errorCode: "430" }))).toBe(dialing);
  });

  it("outcall-response non-OK ends the attempt, OK keeps dialing", () => {
    const dialing = run(initialCallState, { type: "dial", phone: "0912345678" });
    expect(run(dialing, event("outcall-response", { errorCode: "INCALL" }))).toEqual(initialCallState);
    expect(run(dialing, event("outcall-response", { errorCode: "OK" }))).toBe(dialing);
  });

  it("toggle-mute/hold only flip while in a call", () => {
    expect(run(initialCallState, { type: "toggle-mute" })).toBe(initialCallState);
    const inCall = run(
      initialCallState,
      event("ring", { callId: "c1", caller: "0900000001", callType: "CALL_INBOUND" }),
      event("answer", {}),
    );
    const muted = run(inCall, { type: "toggle-mute" });
    expect(muted.muted).toBe(true);
    expect(run(muted, { type: "toggle-hold" }).held).toBe(true);
  });

  it("reset returns initial state", () => {
    const dialing = run(initialCallState, { type: "dial", phone: "0912345678" });
    expect(run(dialing, { type: "reset" })).toEqual(initialCallState);
  });
});
```

- [ ] **Step 2: Chạy fail** — `npx vitest run src/features/callcenter/__tests__/webphone-call-state.test.ts` → FAIL.

- [ ] **Step 3: Implement** `webphone-call-state.ts`:

```ts
/**
 * State machine cuộc gọi — reducer THUẦN (side effect toast/postMessage nằm ở
 * WebphoneProvider). `now` truyền qua action để reducer test được không cần mock Date.
 */
import type { WebphoneEventKind } from "./webphone-bridge";

export type CallStatus = "idle" | "dialing" | "ringing" | "in-call";

export type CallInfo = {
  direction: "in" | "out";
  phone: string;
  callId?: string;
  startedAt?: number;
};

export type CallMachineState = {
  status: CallStatus;
  call: CallInfo | null;
  muted: boolean;
  held: boolean;
};

export type CallMachineAction =
  | { type: "dial"; phone: string }
  | { type: "event"; kind: WebphoneEventKind; data: Record<string, unknown>; now: number }
  | { type: "toggle-mute" }
  | { type: "toggle-hold" }
  | { type: "reset" };

export const initialCallState: CallMachineState = {
  status: "idle",
  call: null,
  muted: false,
  held: false,
};

function onEvent(state: CallMachineState, kind: WebphoneEventKind, data: Record<string, unknown>, now: number): CallMachineState {
  switch (kind) {
    case "ring": {
      const callId = data.callId ? String(data.callId) : undefined;
      if (data.callType === "CALL_INBOUND") {
        return {
          ...initialCallState,
          status: "ringing",
          call: { direction: "in", phone: String(data.caller ?? ""), callId },
        };
      }
      // CALL_OUTBOUND: đầu xa đổ chuông — giữ dialing, ghi nhận callId.
      if (state.call) return { ...state, call: { ...state.call, callId } };
      return state;
    }
    case "answer":
      if (!state.call) return state;
      return { ...state, status: "in-call", call: { ...state.call, startedAt: now } };
    case "end": {
      if (!state.call) return state;
      const endedId = data.callId ? String(data.callId) : undefined;
      // Event end của cuộc gọi khác (nhiều tab/cuộc trước dồn về) — bỏ qua.
      if (endedId && state.call.callId && endedId !== state.call.callId) return state;
      return initialCallState;
    }
    case "outcall-error":
      // 430 = voicemail (info), cuộc gọi vẫn tiếp diễn.
      if (Number(data.errorCode) === 430) return state;
      return state.call ? initialCallState : state;
    case "outcall-response":
      if (data.errorCode === "OK") return state;
      return state.call ? initialCallState : state;
    default:
      return state;
  }
}

export function webphoneCallReducer(state: CallMachineState, action: CallMachineAction): CallMachineState {
  switch (action.type) {
    case "dial":
      return { ...initialCallState, status: "dialing", call: { direction: "out", phone: action.phone } };
    case "event":
      return onEvent(state, action.kind, action.data, action.now);
    case "toggle-mute":
      return state.status === "in-call" || state.status === "dialing" ? { ...state, muted: !state.muted } : state;
    case "toggle-hold":
      return state.status === "in-call" || state.status === "dialing" ? { ...state, held: !state.held } : state;
    case "reset":
      return initialCallState;
    default:
      return state;
  }
}
```

Lưu ý: test "toggle-mute/hold only flip while in a call" dùng state in-call; reducer cho phép cả dialing (mute sớm khi đang đổ chuông đi) — test đầu (`idle` không flip) vẫn đúng.

- [ ] **Step 4: Chạy pass** — cùng lệnh Step 2 → PASS.
- [ ] **Step 5: Commit** — `git add frontend/src/features/callcenter && git commit -m "feat(callcenter): call state machine reducer"`

---

### Task 4: Context tách file + WebphoneProvider nối reducer/toast/command

**Files:**
- Create: `frontend/src/features/callcenter/webphone-context.ts`
- Modify: `frontend/src/features/callcenter/WebphoneProvider.tsx`
- Modify: `frontend/src/features/callcenter/CallPhoneButton.tsx` (đổi import `useWebphone`)
- Test: `frontend/src/features/callcenter/__tests__/webphone-context.test.tsx`

**Interfaces:**
- Consumes: Task 1 types + messages, Task 3 reducer.
- Produces: `webphone-context.ts`:

```ts
export type WebphoneContextValue = {
  canUse: boolean;
  enabled: boolean;
  visible: boolean;
  setVisible: (next: boolean) => void;
  dialCall: (phone: string) => void;
  // state machine
  callState: CallStatus;
  currentCall: CallInfo | null;
  muted: boolean;
  held: boolean;
  answer: () => void;
  hangup: () => void; // terminate — dùng cho cả từ chối cuộc gọi đến
  toggleMute: () => void;
  toggleHold: () => void;
  // đường ống function còn lại của doc (chưa có UI)
  sendCommand: (method: WebphoneCommandMethod, args?: (string | number)[]) => void;
  setAgentStatus: (status: ZenifyAgentStatus) => void;
  setPhoneType: (phoneType: 0 | 1) => void;
  agentStatus: ZenifyAgentStatus | null;
  phoneType: number | null;
};
export const WebphoneContext: React.Context<WebphoneContextValue>; // default no-op values
export function useWebphone(): WebphoneContextValue;
```

- [ ] **Step 1: Viết test fail** — `__tests__/webphone-context.test.tsx` (context tách khỏi provider để WebphonePanel test được bằng Provider giả):

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useWebphone } from "../webphone-context";

function Probe() {
  const { callState, canUse } = useWebphone();
  return <div>{`${callState}|${String(canUse)}`}</div>;
}

describe("webphone-context", () => {
  it("provides safe defaults outside provider", () => {
    render(<Probe />);
    expect(screen.getByText("idle|false")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Chạy fail** — `npx vitest run src/features/callcenter/__tests__/webphone-context.test.tsx` → FAIL (module chưa có).

- [ ] **Step 3: Implement.** `webphone-context.ts`: type + `createContext` default (mọi hàm no-op, `callState: "idle"`, `currentCall/agentStatus/phoneType: null`, `muted/held/canUse/enabled/visible: false`) + hook `useWebphone` (chuyển từ WebphoneProvider.tsx sang, giữ nguyên eslint-disable react-refresh nếu cần — file này không export component nên không cần).

`WebphoneProvider.tsx` sửa:
1. Bỏ `WebphoneContextValue`/`WebphoneContext`/`useWebphone` nội bộ → import từ `./webphone-context`.
2. Thêm `useReducer(webphoneCallReducer, initialCallState)`; thêm state `agentStatus`, `phoneType`.
3. Trong `onMessage` case `"webphone:event"` — thay logic cũ:

```ts
case "webphone:event": {
  const kind = msg.kind;
  const data = msg.data ?? {};
  if (kind) dispatch({ type: "event", kind, data, now: Date.now() });
  switch (kind) {
    case "ring":
      if (data.callType === "CALL_INBOUND") setVisible(true);
      break;
    case "outcall-error": {
      const err = outcallErrorMessage(Number(data.errorCode));
      if (err) (err.type === "info" ? toast.info : toast.error)(err.message);
      setVisible(true);
      break;
    }
    case "outcall-response": {
      const message = outcallResponseMessage(String(data.errorCode ?? ""));
      if (message) {
        toast.error(message);
        setVisible(true);
      }
      break;
    }
    case "notification":
      if (data.type === "not_ready") toast.error(NOT_READY_MESSAGE);
      if (data.type === "agent_reject") toast.warning(AGENT_REJECT_MESSAGE);
      if (data.type === "phone_failed" && data.title) toast.error(String(data.title));
      break;
    case "agent-status":
      setAgentStatus((data.status as ZenifyAgentStatus) ?? null);
      break;
    case "phone-type":
      setPhoneType(typeof data.phoneType === "number" ? data.phoneType : Number(data.phoneType ?? NaN) || 0);
      break;
  }
  break;
}
```

4. `dialCall` thêm `dispatch({ type: "dial", phone: clean })` sau khi post.
5. Callback mới (đều `useCallback`, gate `enabled` trừ sendCommand gate `initialized`):

```ts
const sendCommand = useCallback(
  (method: WebphoneCommandMethod, args?: (string | number)[]) => {
    post({ type: "webphone:command", method, args });
  },
  [post],
);
const answer = useCallback(() => sendCommand("answerCall"), [sendCommand]);
const hangup = useCallback(() => sendCommand("terminate"), [sendCommand]);
const toggleMute = useCallback(() => {
  sendCommand(callMachine.muted ? "unmute" : "mute");
  dispatch({ type: "toggle-mute" });
}, [sendCommand, callMachine.muted]);
const toggleHold = useCallback(() => {
  sendCommand(callMachine.held ? "unhold" : "hold");
  dispatch({ type: "toggle-hold" });
}, [sendCommand, callMachine.held]);
const setAgentStatusCmd = useCallback(
  (status: ZenifyAgentStatus) => post({ type: "webphone:agent-status", status }),
  [post],
);
const setPhoneTypeCmd = useCallback(
  (phoneType: 0 | 1) => post({ type: "webphone:phone-type", phoneType }),
  [post],
);
```

6. Khi `sessionEnded`/`recover()`/`webphone:logged-out` → `dispatch({ type: "reset" })`, `setAgentStatus(null)`, `setPhoneType(null)`.
7. `contextValue` useMemo mở rộng đủ field mới (giữ useMemo — không phá memo OrderTableRow).
8. Render: giữ khung nổi + header + màn hình lỗi/Kết nối lại; thay `<iframe className="w-[340px] ... h-[560px]">` → iframe LUÔN ẩn `className="h-0 w-0 border-0"` (bỏ điều kiện h-0 theo sessionEnded); thân panel render `<WebphonePanel />` (Task 5 — tạm để placeholder `null` ở task này, wire ở Task 5) khi `!sessionEnded && !sdkError`.
9. `CallPhoneButton.tsx`: đổi `import { useWebphone } from "./WebphoneProvider"` → `from "./webphone-context"`.
10. `RootLayout.tsx` không đổi (vẫn import `WebphoneProvider`).

- [ ] **Step 4: Chạy pass** — `npx vitest run src/features/callcenter` → PASS (3 file test).
- [ ] **Step 5: Lint** — `npm run lint -- --quiet` sạch cho các file sửa.
- [ ] **Step 6: Commit** — `git add frontend/src && git commit -m "feat(callcenter): provider call state machine, sonner toasts, sdk command api"`

---

### Task 5: UI WebphonePanel / Dialpad / CallScreen + wire + docs

**Files:**
- Create: `frontend/src/features/callcenter/Dialpad.tsx`
- Create: `frontend/src/features/callcenter/CallScreen.tsx`
- Create: `frontend/src/features/callcenter/WebphonePanel.tsx`
- Modify: `frontend/src/features/callcenter/WebphoneProvider.tsx` (render `<WebphonePanel />`)
- Modify: `docs/zenify-webphone-integration.md` (note kiến trúc mới, trỏ sang `docs/zenify-sdk-full-bridge.md`)
- Test: `frontend/src/features/callcenter/__tests__/WebphonePanel.test.tsx`

**Interfaces:**
- Consumes: `useWebphone()` (Task 4), `WebphoneContext` để bơm value giả trong test.

- [ ] **Step 1: Viết test fail** — `__tests__/WebphonePanel.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { WebphoneContext, type WebphoneContextValue } from "../webphone-context";
import { WebphonePanel } from "../WebphonePanel";

const base: WebphoneContextValue = {
  canUse: true,
  enabled: true,
  visible: true,
  setVisible: () => {},
  dialCall: () => {},
  callState: "idle",
  currentCall: null,
  muted: false,
  held: false,
  answer: () => {},
  hangup: () => {},
  toggleMute: () => {},
  toggleHold: () => {},
  sendCommand: () => {},
  setAgentStatus: () => {},
  setPhoneType: () => {},
  agentStatus: null,
  phoneType: null,
};

function renderPanel(value: Partial<WebphoneContextValue>) {
  return render(
    <WebphoneContext.Provider value={{ ...base, ...value }}>
      <WebphonePanel />
    </WebphoneContext.Provider>,
  );
}

describe("WebphonePanel", () => {
  it("idle: dialpad dials typed number", async () => {
    const dialCall = vi.fn();
    renderPanel({ dialCall });
    await userEvent.click(screen.getByRole("button", { name: "9" }));
    await userEvent.click(screen.getByRole("button", { name: "1" }));
    await userEvent.click(screen.getByRole("button", { name: /gọi đi/i }));
    expect(dialCall).toHaveBeenCalledWith("91");
  });

  it("ringing inbound: shows caller and answer/reject", async () => {
    const answer = vi.fn();
    const hangup = vi.fn();
    renderPanel({
      callState: "ringing",
      currentCall: { direction: "in", phone: "0900000001", callId: "c1" },
      answer,
      hangup,
    });
    expect(screen.getByText("0900000001")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /nghe máy/i }));
    expect(answer).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /từ chối/i }));
    expect(hangup).toHaveBeenCalled();
  });

  it("in-call: shows controls and hangup works", async () => {
    const hangup = vi.fn();
    const toggleMute = vi.fn();
    renderPanel({
      callState: "in-call",
      currentCall: { direction: "out", phone: "0912345678", callId: "c2", startedAt: Date.now() - 65_000 },
      hangup,
      toggleMute,
    });
    expect(screen.getByText("0912345678")).toBeInTheDocument();
    expect(screen.getByText(/01:0\d/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /tắt tiếng/i }));
    expect(toggleMute).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /tắt máy/i }));
    expect(hangup).toHaveBeenCalled();
  });

  it("dialing: shows ringing label", () => {
    renderPanel({
      callState: "dialing",
      currentCall: { direction: "out", phone: "0912345678" },
    });
    expect(screen.getByText(/đang đổ chuông/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Chạy fail** — `npx vitest run src/features/callcenter/__tests__/WebphonePanel.test.tsx` → FAIL.

- [ ] **Step 3: Implement 3 component.**

`Dialpad.tsx`:

```tsx
import { useState } from "react";
import { Delete, Phone } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"] as const;

/** Bàn phím gọi ra khi webphone rảnh (idle). */
export function Dialpad({ onDial }: { onDial: (phone: string) => void }) {
  const [value, setValue] = useState("");
  const submit = () => {
    const phone = value.trim();
    if (phone) onDial(phone);
  };
  return (
    <div className="flex w-full flex-col gap-3">
      <div className="flex items-center gap-1.5">
        <Input
          value={value}
          onChange={(e) => setValue(e.target.value.replace(/[^\d*#+]/g, ""))}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder="Nhập số điện thoại"
          inputMode="tel"
          aria-label="Số điện thoại"
          className="text-center text-base tracking-wider"
        />
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => setValue((v) => v.slice(0, -1))}
          disabled={!value}
          aria-label="Xóa số"
        >
          <Delete className="h-4 w-4" />
        </Button>
      </div>
      <div className="grid grid-cols-3 gap-1.5">
        {KEYS.map((key) => (
          <Button
            key={key}
            variant="outline"
            className="h-10 text-base font-medium"
            onClick={() => setValue((v) => v + key)}
          >
            {key}
          </Button>
        ))}
      </div>
      <Button onClick={submit} disabled={!value.trim()} className="gap-2" aria-label="Gọi đi">
        <Phone className="h-4 w-4" />
        Gọi đi
      </Button>
    </div>
  );
}
```

`CallScreen.tsx`:

```tsx
import { useEffect, useState } from "react";
import { Mic, MicOff, Pause, Phone, PhoneOff, Play } from "lucide-react";

import { Button } from "@/components/ui/button";

import type { CallInfo, CallStatus } from "./webphone-call-state";

function formatDuration(startedAt: number, now: number): string {
  const total = Math.max(0, Math.floor((now - startedAt) / 1000));
  const mm = String(Math.floor(total / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

/** Đồng hồ MM:SS tự đếm từ startedAt (thay easytimer của bộ UI Zenify cũ). */
function CallTimer({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return <span className="font-mono text-lg text-foreground">{formatDuration(startedAt, now)}</span>;
}

type CallScreenProps = {
  status: Exclude<CallStatus, "idle">;
  call: CallInfo;
  muted: boolean;
  held: boolean;
  onAnswer: () => void;
  onHangup: () => void;
  onToggleMute: () => void;
  onToggleHold: () => void;
};

/** Màn hình cuộc gọi: đến (ringing) / đang gọi ra (dialing) / trong cuộc gọi. */
export function CallScreen({ status, call, muted, held, onAnswer, onHangup, onToggleMute, onToggleHold }: CallScreenProps) {
  return (
    <div className="flex w-full flex-col items-center gap-4 py-2">
      <div className="flex flex-col items-center gap-1">
        <span className="text-xs text-muted-foreground">
          {status === "ringing" ? "Cuộc gọi đến" : call.direction === "out" ? "Gọi đi" : "Cuộc gọi"}
        </span>
        <span className="text-lg font-semibold text-foreground">{call.phone || "Không rõ số"}</span>
        {status === "in-call" && call.startedAt ? (
          <CallTimer startedAt={call.startedAt} />
        ) : (
          <span className="text-sm text-muted-foreground">
            {status === "ringing" ? "đang đổ chuông vào…" : "Đang đổ chuông…"}
          </span>
        )}
      </div>

      {status === "ringing" ? (
        <div className="flex items-center gap-3">
          <Button onClick={onAnswer} className="gap-2 bg-emerald-600 hover:bg-emerald-700" aria-label="Nghe máy">
            <Phone className="h-4 w-4" />
            Nghe máy
          </Button>
          <Button variant="destructive" onClick={onHangup} className="gap-2" aria-label="Từ chối">
            <PhoneOff className="h-4 w-4" />
            Từ chối
          </Button>
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <Button
            variant={muted ? "secondary" : "outline"}
            size="icon"
            onClick={onToggleMute}
            title={muted ? "Bật tiếng" : "Tắt tiếng"}
            aria-label={muted ? "Bật tiếng" : "Tắt tiếng"}
          >
            {muted ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
          </Button>
          <Button
            variant={held ? "secondary" : "outline"}
            size="icon"
            onClick={onToggleHold}
            title={held ? "Tiếp tục" : "Giữ máy"}
            aria-label={held ? "Tiếp tục" : "Giữ máy"}
          >
            {held ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
          </Button>
          <Button variant="destructive" onClick={onHangup} className="gap-2" aria-label="Tắt máy">
            <PhoneOff className="h-4 w-4" />
            Tắt máy
          </Button>
        </div>
      )}
    </div>
  );
}
```

`WebphonePanel.tsx`:

```tsx
import { Dialpad } from "./Dialpad";
import { CallScreen } from "./CallScreen";
import { useWebphone } from "./webphone-context";

/** Thân panel "Tổng đài" — UI gọi tự build (headless SDK, Bước 3.2). */
export function WebphonePanel() {
  const { callState, currentCall, muted, held, dialCall, answer, hangup, toggleMute, toggleHold } = useWebphone();

  return (
    <div className="w-[340px] px-4 py-4">
      {callState === "idle" || !currentCall ? (
        <Dialpad onDial={dialCall} />
      ) : (
        <CallScreen
          status={callState}
          call={currentCall}
          muted={muted}
          held={held}
          onAnswer={answer}
          onHangup={hangup}
          onToggleMute={toggleMute}
          onToggleHold={toggleHold}
        />
      )}
    </div>
  );
}
```

Wire vào `WebphoneProvider.tsx` (chỗ placeholder Task 4): trong khung nổi, sau block lỗi:

```tsx
{!sessionEnded && !sdkError ? <WebphonePanel /> : null}
<iframe key={frameKey} ref={iframeRef} src="/webphone.html" title="Zenify Webphone"
  allow="microphone; autoplay" className="h-0 w-0 border-0" aria-hidden="true" />
```

- [ ] **Step 4: Chạy pass** — `npx vitest run src/features/callcenter` → PASS toàn bộ.
- [ ] **Step 5: Cập nhật `docs/zenify-webphone-integration.md`** — đầu file thêm khối:

```markdown
> **Cập nhật 2026-07-15:** UI lõi gọi đã chuyển từ bộ UI Zenify (Bước 3.1) sang
> SDK headless + UI tự build theo theme ERP (Bước 3.2) — kiến trúc, state machine
> và độ phủ function xem `docs/zenify-sdk-full-bridge.md`. Phần token flow,
> iframe cô lập, env, RBAC bên dưới vẫn đúng.
```

- [ ] **Step 6: Toàn cục** — `npm run test` (full suite, baseline fail đã biết: OrderFormDialog + 2 mobile-viewport — không phải regression), `npm run lint`, `npm run build` → sạch.
- [ ] **Step 7: Commit + push** —

```bash
git add frontend/src frontend/public docs
git commit -m "feat(callcenter): erp-themed call ui with dialpad and call screen"
git push -u origin feat/zenify-sdk-full-bridge
```

---

## Điều kiện merge (ngoài plan)

Smoke test prod credentials theo mục 6 spec: gọi ra nghe rõ 2 chiều, nghe vào (đổ chuông + answer), mute/hold có tác dụng thật, từ chối, khách cúp máy, toast lỗi 486/488. Nếu webphone.js headless không tự phát chuông cuộc gọi đến → follow-up phát ringtone từ app.
