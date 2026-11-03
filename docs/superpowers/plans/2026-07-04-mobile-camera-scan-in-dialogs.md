# Mobile Camera Scan in Warehouse Dialogs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trên viewport mobile, hai dialog quét kho (Quét xuất kho, Tạo phiếu trả hàng) nhúng camera điện thoại quét trực tiếp; luồng QR relay bị gỡ bỏ hoàn toàn (FE + BE) theo quyết định user 2026-07-04.

**Architecture:** Di chuyển `CameraScanner` (đã có, BarcodeDetector + zxing fallback) từ feature `scan-relay` về `features/inventory/scan/`. Component mới `CameraScanButton` mở overlay full-screen chứa camera, đổ mã vào handler quét sẵn có của dialog. Mobile → `CameraScanButton`; desktop chỉ còn ô nhập + súng quét (`PhoneScanButton` bị xóa mọi nơi). Gỡ toàn bộ code relay: FE (`features/scan-relay/`, `features/inventory/scan/phone/`, route `/scan-relay`, dep `qrcode.react`) + BE (app `apps/scan_relay`, wiring urls/asgi/INSTALLED_APPS, regen schema).

**Tech Stack:** React + TypeScript + Vite, vitest + @testing-library/react (jsdom), shadcn-style components trong `components/ui`, lucide-react.

**Spec:** `docs/superpowers/specs/2026-07-04-mobile-camera-scan-in-dialogs-design.md`

## Global Constraints

- Branch: `feat/mobile-camera-scan-in-dialogs` (đã tạo từ master). KHÔNG tạo branch mới — commit thẳng lên branch này.
- Mọi lệnh npm chạy trong thư mục `frontend/`.
- Test: `npm test` (vitest run). Typecheck: `npm run typecheck`. Lint: `npm run lint:strict`.
- Baseline đã biết fail trên master sạch: return-rate BE, OrderFormDialog + 2 test mobile-viewport FE — KHÔNG phải regression, không cần sửa.
- Commit message tiếng Anh, ngắn gọn, KHÔNG kèm footer "Co-Authored-By".
- Backend CHỈ sửa trong phạm vi gỡ app `scan_relay` (Task 8). `StockDocumentsPage` chỉ gỡ nút QR (Task 6), giữ nguyên ô nhập + súng quét.
- KHÔNG gỡ `qrcode==8.2` trong `backend/requirements.txt` — `apps/orders/invoice.py` vẫn dùng.
- Typecheck có 7 lỗi pre-existing (dashboard/recharts/@sentry, env-related) — gate = không lỗi MỚI.
- UI text tiếng Việt hardcode (theo pattern hiện có của các dialog kho — không dùng i18n ở đây).

---

### Task 1: Move `CameraScanner` về `features/inventory/scan/`

**Files:**
- Move: `frontend/src/features/scan-relay/CameraScanner.tsx` → `frontend/src/features/inventory/scan/CameraScanner.tsx`
- Move: `frontend/src/features/scan-relay/barcode-detector.d.ts` → `frontend/src/features/inventory/scan/barcode-detector.d.ts`
- Modify: `frontend/src/features/scan-relay/ScanRelayPage.tsx:9`

**Interfaces:**
- Consumes: —
- Produces: `CameraScanner` importable từ `@/features/inventory/scan/CameraScanner`, props `{ onCode: (code: string) => void }`. Task 3 dùng.

- [ ] **Step 1: Di chuyển 2 file bằng git mv**

```bash
cd frontend
git mv src/features/scan-relay/CameraScanner.tsx src/features/inventory/scan/CameraScanner.tsx
git mv src/features/scan-relay/barcode-detector.d.ts src/features/inventory/scan/barcode-detector.d.ts
```

Không sửa nội dung 2 file (logic giữ nguyên; `barcode-detector.d.ts` là ambient type, nằm đâu trong `src/` cũng được tsc pick up).

- [ ] **Step 2: Sửa import trong ScanRelayPage**

Trong `frontend/src/features/scan-relay/ScanRelayPage.tsx`, thay dòng 9:

```tsx
// CŨ:
import { CameraScanner } from "./CameraScanner";
// MỚI:
import { CameraScanner } from "@/features/inventory/scan/CameraScanner";
```

- [ ] **Step 3: Verify typecheck + test suite**

Run: `npm run typecheck` trong `frontend/` — Expected: exit 0, không lỗi.
Run: `npm test` — Expected: pass (trừ baseline fails đã biết ở Global Constraints).

- [ ] **Step 4: Commit**

```bash
git add -A src/features/scan-relay src/features/inventory/scan
git commit -m "refactor(inventory): move CameraScanner into inventory scan feature"
```

---

### Task 2: Constant breakpoint mobile dùng chung

**Files:**
- Modify: `frontend/src/lib/constants/layout.ts`
- Modify: `frontend/src/components/ui/responsive-list.tsx:21`

**Interfaces:**
- Consumes: —
- Produces: `MOBILE_VIEWPORT_QUERY: string` và `DESKTOP_VIEWPORT_QUERY: string` export từ `@/lib/constants/layout`. Task 4, 5 dùng `MOBILE_VIEWPORT_QUERY`.

- [ ] **Step 1: Thêm constants vào layout.ts**

Thêm vào cuối `frontend/src/lib/constants/layout.ts`:

```ts
/**
 * Ngưỡng desktop/mobile của app (khớp breakpoint `md` 768px của Tailwind).
 * ResponsiveList hiện bảng ở >= 768px; dưới ngưỡng coi là điện thoại
 * (các dialog quét kho hiện nút quét bằng camera thay vì nối QR).
 */
export const DESKTOP_MIN_WIDTH = 768;

/** Media query "là desktop" — dùng cho useMediaQuery / matchMedia. */
export const DESKTOP_VIEWPORT_QUERY = `(min-width: ${DESKTOP_MIN_WIDTH}px)`;

/** Media query "là điện thoại" — phần bù của DESKTOP_VIEWPORT_QUERY. */
export const MOBILE_VIEWPORT_QUERY = `(max-width: ${DESKTOP_MIN_WIDTH - 0.02}px)`;
```

- [ ] **Step 2: ResponsiveList dùng constant (không đổi hành vi)**

Trong `frontend/src/components/ui/responsive-list.tsx`:

```tsx
// Thêm import:
import { DESKTOP_VIEWPORT_QUERY } from "@/lib/constants/layout";
// Dòng 21, CŨ:
const isDesktop = useMediaQuery("(min-width: 768px)");
// MỚI:
const isDesktop = useMediaQuery(DESKTOP_VIEWPORT_QUERY);
```

LƯU Ý: giữ nguyên chiều query `min-width` cho ResponsiveList — KHÔNG đổi thành phủ định của mobile query, vì khi môi trường không có `matchMedia` (jsdom setup mặc định trả `matches: false`) hành vi mặc định phải giữ là "mobile" như hiện tại.

- [ ] **Step 3: Verify**

Run: `npm run typecheck && npm test` — Expected: như baseline, không fail mới.

- [ ] **Step 4: Commit**

```bash
git add src/lib/constants/layout.ts src/components/ui/responsive-list.tsx
git commit -m "refactor(ui): extract shared desktop/mobile viewport query constants"
```

---

### Task 3: Component `CameraScanButton` (TDD)

**Files:**
- Create: `frontend/src/features/inventory/scan/CameraScanButton.tsx`
- Test: `frontend/src/features/inventory/__tests__/CameraScanButton.test.tsx`

**Interfaces:**
- Consumes: `CameraScanner` từ Task 1 (`{ onCode: (code: string) => void }`).
- Produces: `CameraScanButton` export từ `@/features/inventory/scan/CameraScanButton`, props `{ onScan: (code: string) => void | Promise<void> }`. Task 4, 5 dùng.

- [ ] **Step 1: Viết test fail**

Tạo `frontend/src/features/inventory/__tests__/CameraScanButton.test.tsx`:

```tsx
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// CameraScanner thật đụng navigator.mediaDevices — thay bằng stub bắn mã giả.
vi.mock("@/features/inventory/scan/CameraScanner", () => ({
  CameraScanner: ({ onCode }: { onCode: (c: string) => void }) => (
    <button type="button" onClick={() => onCode("VD12345")}>
      fake-scan
    </button>
  ),
}));

import { CameraScanButton } from "@/features/inventory/scan/CameraScanButton";

describe("CameraScanButton", () => {
  afterEach(() => cleanup());

  it("mở overlay camera, đếm mã quét được và gọi onScan", async () => {
    const user = userEvent.setup();
    const onScan = vi.fn();
    render(<CameraScanButton onScan={onScan} />);

    // Chưa mở → không có camera
    expect(screen.queryByText("fake-scan")).toBeNull();

    await user.click(screen.getByRole("button", { name: /Quét bằng camera/ }));
    expect(screen.getByText("Đưa mã vạch vào khung để quét")).toBeTruthy();
    expect(screen.getByText("Đã quét 0 mã")).toBeTruthy();

    await user.click(screen.getByText("fake-scan"));
    expect(onScan).toHaveBeenCalledWith("VD12345");
    expect(screen.getByText("Đã quét 1 mã")).toBeTruthy();
  });

  it("bấm Xong đóng overlay và unmount camera; mở lại reset bộ đếm", async () => {
    const user = userEvent.setup();
    render(<CameraScanButton onScan={() => {}} />);

    await user.click(screen.getByRole("button", { name: /Quét bằng camera/ }));
    await user.click(screen.getByText("fake-scan"));
    expect(screen.getByText("Đã quét 1 mã")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Xong" }));
    expect(screen.queryByText("fake-scan")).toBeNull();

    await user.click(screen.getByRole("button", { name: /Quét bằng camera/ }));
    expect(screen.getByText("Đã quét 0 mã")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Chạy test, verify fail**

Run: `npx vitest run src/features/inventory/__tests__/CameraScanButton.test.tsx`
Expected: FAIL — "Failed to resolve import ... CameraScanButton".

- [ ] **Step 3: Implement component**

Tạo `frontend/src/features/inventory/scan/CameraScanButton.tsx`:

```tsx
import { useState } from "react";
import { createPortal } from "react-dom";
import { Camera } from "lucide-react";

import { Button } from "@/components/ui/button";

import { CameraScanner } from "./CameraScanner";

type Props = {
  /** Cùng dạng handler mà ScanShipmentInput/useScannerGun gọi. */
  onScan: (code: string) => void | Promise<void>;
};

/**
 * Nút "Quét bằng camera" cho viewport mobile: mở overlay full-screen chứa
 * {@link CameraScanner}, đổ mã thẳng vào handler quét của dialog (không qua
 * phiên scan-relay). Thành/bại từng mã do beep + toast của handler phản hồi;
 * overlay chỉ đếm số mã camera bắt được (đã dedup trong CameraScanner).
 */
export function CameraScanButton({ onScan }: Props) {
  const [open, setOpen] = useState(false);
  const [count, setCount] = useState(0);

  function handleCode(code: string) {
    setCount((n) => n + 1);
    void onScan(code);
  }

  return (
    <>
      <Button
        type="button"
        variant="outline"
        onClick={() => {
          setCount(0);
          setOpen(true);
        }}
      >
        <Camera className="mr-2 size-4" />
        Quét bằng camera
      </Button>
      {open &&
        createPortal(
          // z-50 = ngang DialogContent nhưng mount sau nên nổi lên trên;
          // toaster của sonner z cao hơn nhiều nên toast vẫn hiện đè overlay.
          <div className="fixed inset-0 z-50 bg-black text-white">
            <style>{`@keyframes camscanbtn-line{0%{top:6%}50%{top:94%}100%{top:6%}}`}</style>

            <div className="absolute inset-0">
              <CameraScanner onCode={handleCode} />
            </div>

            {/* Lớp tối xung quanh để khung quét nổi bật (không chặn camera) */}
            <div className="pointer-events-none absolute inset-0 bg-black/35" />

            {/* Thanh trên: bộ đếm */}
            <div className="absolute inset-x-0 top-0 z-10 bg-gradient-to-b from-black/80 to-transparent px-4 pb-8 pt-[max(0.75rem,env(safe-area-inset-top))] text-center">
              <p className="text-sm text-white/80">{`Đã quét ${count} mã`}</p>
            </div>

            {/* Khung ngắm quét ở giữa */}
            <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center px-6">
              <div className="relative aspect-[3/2] w-full max-w-sm">
                <span className="absolute -left-1 -top-1 size-9 rounded-tl-xl border-l-4 border-t-4 border-primary" />
                <span className="absolute -right-1 -top-1 size-9 rounded-tr-xl border-r-4 border-t-4 border-primary" />
                <span className="absolute -bottom-1 -left-1 size-9 rounded-bl-xl border-b-4 border-l-4 border-primary" />
                <span className="absolute -bottom-1 -right-1 size-9 rounded-br-xl border-b-4 border-r-4 border-primary" />
                <div
                  className="absolute inset-x-3 h-0.5 rounded-full bg-primary shadow-[0_0_12px_2px] shadow-primary/70"
                  style={{ animation: "camscanbtn-line 2.2s ease-in-out infinite" }}
                />
              </div>
            </div>

            {/* Thanh dưới: hướng dẫn + nút Xong */}
            <div className="absolute inset-x-0 bottom-0 z-10 space-y-3 bg-gradient-to-t from-black/85 to-transparent px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-10">
              <p className="text-center text-sm text-white/80">Đưa mã vạch vào khung để quét</p>
              <Button type="button" className="h-11 w-full" onClick={() => setOpen(false)}>
                Xong
              </Button>
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
```

- [ ] **Step 4: Chạy test, verify pass**

Run: `npx vitest run src/features/inventory/__tests__/CameraScanButton.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/features/inventory/scan/CameraScanButton.tsx src/features/inventory/__tests__/CameraScanButton.test.tsx
git commit -m "feat(inventory): add CameraScanButton full-screen camera scan overlay"
```

---

### Task 4: Gắn vào `ScanDispatchDialog` + prop `autoFocusOnMount` (TDD)

**Files:**
- Modify: `frontend/src/features/inventory/scan/ScanShipmentInput.tsx`
- Modify: `frontend/src/features/inventory/scan/ScanDispatchDialog.tsx`
- Test: `frontend/src/features/inventory/__tests__/ScanDispatchDialog.viewport.test.tsx`

**Interfaces:**
- Consumes: `CameraScanButton` (Task 3), `MOBILE_VIEWPORT_QUERY` (Task 2).
- Produces: `ScanShipmentInput` nhận prop mới `autoFocusOnMount?: boolean` (default `true`). Task 5 dùng cùng pattern.

- [ ] **Step 1: Viết test fail**

Tạo `frontend/src/features/inventory/__tests__/ScanDispatchDialog.viewport.test.tsx`:

```tsx
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

vi.mock("@/services/http", () => ({
  fetchJson: vi.fn((url: string) => {
    if (url.startsWith("/api/warehouses")) {
      return Promise.resolve({ items: [{ id: 1, name: "Kho A" }], total: 1 });
    }
    return Promise.resolve({});
  }),
}));

import { ScanDispatchDialog } from "@/features/inventory/scan/ScanDispatchDialog";

// jsdom setup mặc định stub matchMedia luôn trả matches:false → phải override
// theo chiều query để giả lập viewport.
function mockViewport(mobile: boolean) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) =>
      ({
        matches: mobile ? query.includes("max-width") : query.includes("min-width"),
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => true,
      }) as MediaQueryList,
  });
}

function renderDialog() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return render(<ScanDispatchDialog open onOpenChange={() => {}} />, { wrapper });
}

describe("ScanDispatchDialog — chọn nút quét theo viewport", () => {
  afterEach(() => cleanup());

  it("mobile: hiện nút quét bằng camera, ẩn nút nối QR điện thoại", async () => {
    mockViewport(true);
    renderDialog();
    expect(await screen.findByRole("button", { name: /Quét bằng camera/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Dùng điện thoại để quét/ })).toBeNull();
  });

  it("desktop: giữ nút nối QR điện thoại, không render nút camera", async () => {
    mockViewport(false);
    renderDialog();
    expect(await screen.findByRole("button", { name: /Dùng điện thoại để quét/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Quét bằng camera/ })).toBeNull();
  });

  it("mobile: ô nhập quét không tự focus (tránh bàn phím ảo tự bật)", async () => {
    mockViewport(true);
    renderDialog();
    const input = await screen.findByPlaceholderText("Quét mã vận đơn…");
    expect(document.activeElement).not.toBe(input);
  });
});
```

- [ ] **Step 2: Chạy test, verify fail**

Run: `npx vitest run src/features/inventory/__tests__/ScanDispatchDialog.viewport.test.tsx`
Expected: FAIL — test mobile không tìm thấy nút "Quét bằng camera" (dialog hiện luôn render PhoneScanButton).

- [ ] **Step 3: Thêm prop `autoFocusOnMount` cho ScanShipmentInput**

Trong `frontend/src/features/inventory/scan/ScanShipmentInput.tsx`:

```tsx
type Props = {
  onScan: (code: string) => void;
  disabled?: boolean;
  placeholder?: string;
  /** Mặc định true (desktop). Mobile tắt để bàn phím ảo không tự bật che màn hình. */
  autoFocusOnMount?: boolean;
};

export const ScanShipmentInput = forwardRef<ScanShipmentInputHandle, Props>(
  function ScanShipmentInput({ onScan, disabled, placeholder, autoFocusOnMount = true }, ref) {
    // ... giữ nguyên state/handle ...

    useEffect(() => {
      if (autoFocusOnMount) inputRef.current?.focus();
    }, [autoFocusOnMount]);
```

Và trên JSX của `<Input …>` đổi `autoFocus` thành `autoFocus={autoFocusOnMount}`. Phần còn lại (handleKeyDown, refocus sau Enter) giữ nguyên.

- [ ] **Step 4: Wire ScanDispatchDialog**

Trong `frontend/src/features/inventory/scan/ScanDispatchDialog.tsx`:

Thêm imports:

```tsx
import { useMediaQuery } from "@/hooks/use-media-query";
import { MOBILE_VIEWPORT_QUERY } from "@/lib/constants/layout";
import { CameraScanButton } from "./CameraScanButton";
```

Trong thân component (cạnh các hook đầu hàm):

```tsx
const isMobile = useMediaQuery(MOBILE_VIEWPORT_QUERY);
```

Sửa khối quét (hiện ở khoảng dòng 183-186):

```tsx
// CŨ:
<ScanShipmentInput ref={scanRef} onScan={handleScan} />
<div className="mt-2">
  <PhoneScanButton purpose="dispatch" onCode={handleScan} />
</div>
// MỚI:
<ScanShipmentInput ref={scanRef} onScan={handleScan} autoFocusOnMount={!isMobile} />
<div className="mt-2">
  {isMobile ? (
    <CameraScanButton onScan={handleScan} />
  ) : (
    <PhoneScanButton purpose="dispatch" onCode={handleScan} />
  )}
</div>
```

- [ ] **Step 5: Chạy test, verify pass**

Run: `npx vitest run src/features/inventory/__tests__/ScanDispatchDialog.viewport.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/features/inventory/scan/ScanShipmentInput.tsx src/features/inventory/scan/ScanDispatchDialog.tsx src/features/inventory/__tests__/ScanDispatchDialog.viewport.test.tsx
git commit -m "feat(inventory): camera scan in dispatch dialog on mobile viewport"
```

---

### Task 5: Gắn vào `CreateReturnDialog` — camera mobile, gỡ nút QR (TDD)

**Files:**
- Modify: `frontend/src/features/inventory/pages/CreateReturnDialog.tsx`
- Test: `frontend/src/features/inventory/__tests__/CreateReturnDialog.viewport.test.tsx`

**Interfaces:**
- Consumes: `CameraScanButton` (Task 3, `@/features/inventory/scan/CameraScanButton`, props `{ onScan: (code: string) => void | Promise<void> }`), `MOBILE_VIEWPORT_QUERY` (Task 2, `@/lib/constants/layout`), `autoFocusOnMount` của `ScanShipmentInput` (Task 4, default `true`).
- Produces: —

- [ ] **Step 1: Viết test fail**

Tạo `frontend/src/features/inventory/__tests__/CreateReturnDialog.viewport.test.tsx`:

```tsx
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

vi.mock("@/services/http", () => ({
  fetchJson: vi.fn((url: string) => {
    if (url.startsWith("/api/warehouses")) {
      return Promise.resolve({ items: [{ id: 1, name: "Kho A" }], total: 1 });
    }
    if (url.startsWith("/api/orders/products")) {
      return Promise.resolve([]);
    }
    return Promise.resolve({});
  }),
}));

import { CreateReturnDialog } from "@/features/inventory/pages/CreateReturnDialog";

// jsdom setup mặc định stub matchMedia luôn trả matches:false → override theo
// chiều query để giả lập viewport.
function mockViewport(mobile: boolean) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) =>
      ({
        matches: mobile ? query.includes("max-width") : query.includes("min-width"),
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => true,
      }) as MediaQueryList,
  });
}

function renderDialog() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return render(<CreateReturnDialog open onOpenChange={() => {}} />, { wrapper });
}

describe("CreateReturnDialog — nút quét theo viewport (QR relay đã gỡ)", () => {
  afterEach(() => cleanup());

  it("mobile: hiện nút quét bằng camera, không còn nút nối QR", async () => {
    mockViewport(true);
    renderDialog();
    expect(await screen.findByRole("button", { name: /Quét bằng camera/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Dùng điện thoại để quét/ })).toBeNull();
  });

  it("desktop: không render nút camera lẫn nút QR — chỉ còn ô nhập quét", async () => {
    mockViewport(false);
    renderDialog();
    expect(
      await screen.findByPlaceholderText("Quét mã vận đơn để nạp đơn hoàn…"),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Quét bằng camera/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Dùng điện thoại để quét/ })).toBeNull();
  });
});
```

- [ ] **Step 2: Chạy test, verify fail**

Run: `npx vitest run src/features/inventory/__tests__/CreateReturnDialog.viewport.test.tsx`
Expected: FAIL — mobile không thấy "Quét bằng camera"; desktop vẫn thấy "Dùng điện thoại để quét".

- [ ] **Step 3: Wire CreateReturnDialog**

Trong `frontend/src/features/inventory/pages/CreateReturnDialog.tsx`:

XÓA import cũ:

```tsx
import { PhoneScanButton } from "@/features/inventory/scan/phone/PhoneScanButton";
```

Thêm imports:

```tsx
import { useMediaQuery } from "@/hooks/use-media-query";
import { MOBILE_VIEWPORT_QUERY } from "@/lib/constants/layout";
import { CameraScanButton } from "@/features/inventory/scan/CameraScanButton";
```

Trong thân component (cạnh các hook đầu hàm, sau `useScannerGun`):

```tsx
const isMobile = useMediaQuery(MOBILE_VIEWPORT_QUERY);
```

Sửa khối quét (quanh dòng 300-308):

```tsx
// CŨ:
<ScanShipmentInput
  ref={scanRef}
  onScan={handleScanCode}
  placeholder="Quét mã vận đơn để nạp đơn hoàn…"
/>
<div className="mt-2">
  <PhoneScanButton purpose="return" onCode={handleScanCode} />
</div>
// MỚI:
<ScanShipmentInput
  ref={scanRef}
  onScan={handleScanCode}
  placeholder="Quét mã vận đơn để nạp đơn hoàn…"
  autoFocusOnMount={!isMobile}
/>
{isMobile && (
  <div className="mt-2">
    <CameraScanButton onScan={handleScanCode} />
  </div>
)}
```

- [ ] **Step 4: Chạy test, verify pass**

Run: `npx vitest run src/features/inventory/__tests__/CreateReturnDialog.viewport.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/features/inventory/pages/CreateReturnDialog.tsx src/features/inventory/__tests__/CreateReturnDialog.viewport.test.tsx
git commit -m "feat(inventory): camera scan in return dialog on mobile, drop QR relay button"
```

---

### Task 6: Gỡ nút QR khỏi `ScanDispatchDialog` + `StockDocumentsPage` (TDD)

**Files:**
- Modify: `frontend/src/features/inventory/scan/ScanDispatchDialog.tsx`
- Modify: `frontend/src/features/inventory/__tests__/ScanDispatchDialog.viewport.test.tsx`
- Modify: `frontend/src/features/inventory/pages/StockDocumentsPage.tsx`
- Modify: `frontend/src/features/inventory/__tests__/StockDocumentsPage.lines.test.tsx`

**Interfaces:**
- Consumes: `CameraScanButton` (Task 3); wiring `isMobile` + `CameraScanButton` đã có sẵn trong ScanDispatchDialog từ Task 4 (dạng ternary với PhoneScanButton).
- Produces: không còn file nào ngoài `features/inventory/scan/phone/**` và `features/scan-relay/**` import `PhoneScanButton` — điều kiện để Task 7 xóa 2 thư mục đó.

- [ ] **Step 1: Sửa test viewport của ScanDispatchDialog (RED)**

Trong `frontend/src/features/inventory/__tests__/ScanDispatchDialog.viewport.test.tsx`, THAY test desktop hiện tại (đang expect có "Dùng điện thoại để quét") bằng:

```tsx
it("desktop: không render nút camera lẫn nút QR — chỉ còn ô nhập quét", async () => {
  mockViewport(false);
  renderDialog();
  expect(await screen.findByPlaceholderText("Quét mã vận đơn…")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Quét bằng camera/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /Dùng điện thoại để quét/ })).toBeNull();
});
```

Giữ nguyên 2 test còn lại (mobile hiện camera + mobile không autofocus); ở test mobile, assertion `queryByRole(... /Dùng điện thoại để quét/)` → null giữ nguyên (vẫn đúng sau khi gỡ).

Run: `npx vitest run src/features/inventory/__tests__/ScanDispatchDialog.viewport.test.tsx`
Expected: FAIL — test desktop mới thấy nút "Dùng điện thoại để quét" vẫn render.

- [ ] **Step 2: Gỡ PhoneScanButton khỏi ScanDispatchDialog (GREEN)**

Trong `frontend/src/features/inventory/scan/ScanDispatchDialog.tsx`:

XÓA import:

```tsx
import { PhoneScanButton } from "./phone/PhoneScanButton";
```

Sửa khối quét (Task 4 đã tạo dạng ternary):

```tsx
// CŨ:
<div className="mt-2">
  {isMobile ? (
    <CameraScanButton onScan={handleScan} />
  ) : (
    <PhoneScanButton purpose="dispatch" onCode={handleScan} />
  )}
</div>
// MỚI:
{isMobile && (
  <div className="mt-2">
    <CameraScanButton onScan={handleScan} />
  </div>
)}
```

Run: `npx vitest run src/features/inventory/__tests__/ScanDispatchDialog.viewport.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 3: Gỡ PhoneScanButton khỏi StockDocumentsPage**

Trong `frontend/src/features/inventory/pages/StockDocumentsPage.tsx`:

XÓA import (dòng ~54):

```tsx
import { PhoneScanButton } from "@/features/inventory/scan/phone/PhoneScanButton";
```

XÓA khối render (dòng ~455-458, cùng wrapper `<div className="mt-2">` nếu wrapper chỉ chứa nút này):

```tsx
<div className="mt-2">
  <PhoneScanButton purpose="stock_document" onCode={handleScanCode} />
</div>
```

KHÔNG đụng gì khác ở trang này (ô nhập quét, súng quét, ScannerGunBadge giữ nguyên).

Trong `frontend/src/features/inventory/__tests__/StockDocumentsPage.lines.test.tsx`, XÓA mock không còn cần:

```tsx
vi.mock("@/features/inventory/scan/phone/PhoneScanButton", () => ({
  PhoneScanButton: () => null,
}));
```

Run: `npx vitest run src/features/inventory/__tests__/StockDocumentsPage.lines.test.tsx`
Expected: PASS (2 tests, như trước).

- [ ] **Step 4: Full suite + commit**

Run: `npm test` — Expected: không fail mới.

```bash
git add src/features/inventory/scan/ScanDispatchDialog.tsx src/features/inventory/pages/StockDocumentsPage.tsx src/features/inventory/__tests__/ScanDispatchDialog.viewport.test.tsx src/features/inventory/__tests__/StockDocumentsPage.lines.test.tsx
git commit -m "feat(inventory): remove QR relay button from dispatch dialog and stock documents page"
```

---

### Task 7: Xóa code QR relay phía frontend

**Files:**
- Delete: `frontend/src/features/scan-relay/` (ScanRelayApp.tsx, ScanRelayPage.tsx)
- Delete: `frontend/src/features/inventory/scan/phone/` (PhoneScanButton.tsx, use-phone-scan-session.ts, scan-session-queries.ts, `__tests__/use-phone-scan-session.test.ts`)
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/package.json` + `frontend/package-lock.json` (gỡ `qrcode.react`)

**Interfaces:**
- Consumes: Task 5 + 6 đã gỡ hết import PhoneScanButton khỏi code sống.
- Produces: repo không còn tham chiếu nào tới `scan-relay`, `phone/`, `qrcode.react`, `/api/scan-sessions` phía FE (trừ `src/api/schema.d.ts` — Task 8 regen).

- [ ] **Step 1: Xóa 2 thư mục**

```bash
cd frontend
git rm -r src/features/scan-relay src/features/inventory/scan/phone
```

- [ ] **Step 2: Gỡ route /scan-relay trong main.tsx**

Trong `frontend/src/main.tsx` XÓA:

```tsx
const ScanRelayApp = lazy(() => import("./features/scan-relay/ScanRelayApp"));
```

```tsx
const isScanRelayRoute = path.startsWith("/scan-relay");
```

và nhánh render:

```tsx
) : isScanRelayRoute ? (
  <Suspense fallback={null}>
    <ScanRelayApp />
  </Suspense>
```

(khối ternary còn lại: warranty → mobile → App.)

- [ ] **Step 3: Gỡ dependency qrcode.react**

```bash
npm uninstall qrcode.react
```

- [ ] **Step 4: Verify không còn tham chiếu**

Run: `git grep -n "scan-relay\|PhoneScanButton\|usePhoneScanSession\|scan-session-queries\|qrcode.react" -- src package.json`
Expected: không còn kết quả nào trong code sống (cho phép: `src/api/schema.d.ts` còn `/api/scan-sessions` — Task 8 xử lý; comment nhắc "phiên scan-relay" trong `CameraScanButton.tsx` — sửa comment đó thành mô tả không tham chiếu relay nếu grep khớp).

Run: `npm run typecheck` — Expected: chỉ 7 lỗi pre-existing.
Run: `npm test` — Expected: suite pass, KHÔNG còn test `use-phone-scan-session` (đã xóa cùng thư mục).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(frontend): remove QR relay scan flow (scan-relay route, phone session, qrcode.react)"
```

---

### Task 8: Xóa app `scan_relay` phía backend + regen schema

**Files:**
- Delete: `backend/apps/scan_relay/` (toàn bộ)
- Modify: `backend/config/asgi.py` (dòng ~14 + chỗ dùng `scan_ws`)
- Modify: `backend/config/urls.py` (dòng ~20)
- Modify: `backend/config/settings_parts/base.py` (dòng ~61, INSTALLED_APPS)
- Regen: `backend/schema.yaml`, `frontend/src/api/schema.d.ts`

**Interfaces:**
- Consumes: Task 7 đã gỡ mọi call FE tới `/api/scan-sessions*`.
- Produces: backend không còn app scan_relay; schema không còn path `/api/scan-sessions*`.

- [ ] **Step 1: Gỡ wiring config**

`backend/config/urls.py` — XÓA dòng:

```python
re_path(r"^api/", include("apps.scan_relay.urls")),
```

`backend/config/settings_parts/base.py` — XÓA dòng trong INSTALLED_APPS:

```python
"apps.scan_relay",
```

`backend/config/asgi.py` — XÓA import:

```python
from apps.scan_relay.routing import websocket_urlpatterns as scan_ws  # noqa: E402
```

và bỏ `scan_ws` khỏi danh sách websocket urlpatterns (đọc file để thấy chỗ nối — thường là `... + scan_ws` hoặc phần tử trong list; xóa đúng phần tử đó, giữ các websocket khác nguyên).

- [ ] **Step 2: Xóa app**

```bash
git rm -r backend/apps/scan_relay
```

- [ ] **Step 3: Smoke check backend trong docker**

Chạy từ repo root (Git Bash, image test có sẵn theo runbook nội bộ):

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default --env-file .env --env-file backend/.env -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword -v "$PWD/backend:/app" -w /app desi-order-web-test:latest python manage.py check
```

Expected: `System check identified no issues` (hoặc chỉ warnings có sẵn). Nếu image/network không tồn tại, dùng `docker ps`/`docker images` tìm đúng tên rồi chạy lại; nếu môi trường docker không khả dụng, dừng và báo BLOCKED.

- [ ] **Step 4: Regen schema.yaml + schema.d.ts**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default --env-file .env --env-file backend/.env -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword -v "$PWD/backend:/app" -w /app desi-order-web-test:latest python manage.py spectacular --file schema.yaml
cd frontend && npm run gen:api
```

Expected: `schema.yaml` không còn `/api/scan-sessions`; `git diff` của `schema.yaml`/`schema.d.ts` CHỈ gồm phần xóa scan-sessions (nếu diff lộn xộn thêm thứ khác do version drift, dừng lại và báo DONE_WITH_CONCERNS kèm diff stat).

Run: `git grep -n "scan-sessions" -- backend/schema.yaml frontend/src/api/schema.d.ts` — Expected: không còn.

- [ ] **Step 5: FE verify + commit**

Run (frontend/): `npm run typecheck` — chỉ 7 lỗi pre-existing.

```bash
git add -A
git commit -m "refactor(backend): remove scan_relay app and regenerate schema"
```

---

### Task 9: Verification cuối + push + MR

**Files:**
- Không tạo/sửa file code (chỉ chạy verify + push).

**Interfaces:**
- Consumes: toàn bộ Task 1-8.
- Produces: branch đã push + link MR.

- [ ] **Step 1: Full suite + typecheck + lint**

Trong `frontend/`:

Run: `npm run typecheck` — Expected: chỉ 7 lỗi pre-existing (dashboard/recharts/@sentry).
Run: `npm test` — Expected: chỉ fail baseline đã biết; các test mới (CameraScanButton, 2 file viewport, StockDocumentsPage.lines) pass.
Run: `npx eslint <các file đã sửa trong branch> --max-warnings=0` — Expected: exit 0.

- [ ] **Step 2: So khớp spec lần cuối**

Đối chiếu `docs/superpowers/specs/2026-07-04-mobile-camera-scan-in-dialogs-design.md`:
- [ ] CameraScanner nằm ở `features/inventory/scan/`; không còn thư mục `features/scan-relay/`, `features/inventory/scan/phone/`.
- [ ] Overlay camera: nền, khung ngắm, đếm mã, nút Xong, unmount khi đóng.
- [ ] Mobile hiện CameraScanButton ở 2 dialog; desktop không còn nút QR ở cả 3 chỗ (dispatch, return, stock documents).
- [ ] Mobile không autofocus ô nhập; desktop autofocus giữ nguyên.
- [ ] `git grep "scan_relay\|scan-relay\|scan-sessions"` — không còn trong code sống (docs/specs cũ được phép).
- [ ] Backend: không còn `apps/scan_relay`; `qrcode==8.2` VẪN còn trong requirements (invoice dùng).

- [ ] **Step 3: Push + MR link**

```bash
git push origin feat/mobile-camera-scan-in-dialogs
```

Gửi user link tạo MR: `https://gitlab.com/desi3512912/desi-order/-/merge_requests/new?merge_request%5Bsource_branch%5D=feat%2Fmobile-camera-scan-in-dialogs`
