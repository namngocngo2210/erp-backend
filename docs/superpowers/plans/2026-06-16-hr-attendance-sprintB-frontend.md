# HR Attendance Sprint B — Frontend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.
> **Tiền đề:** Sprint A (backend) đã merge — các endpoint `/api/hr/attendance/config`, `/api/hr/attendance/check-in`, `/api/hr/attendance/locations[/{id}]` đã hoạt động.

**Goal:** Trang self check-in `/hr/check-in` (camera + GPS, mobile-first, chỉ cần đăng nhập) + trang cấu hình vị trí (admin, section `employees`).

**Architecture:** React + Vite + TanStack Query + shadcn/ui. Feature-slice `src/features/hr`. Route check-in chỉ chặn theo đăng nhập (RequireAuth không có `section`). Ảnh chụp từ camera → canvas nén JPEG → base64 data URL gửi JSON.

**Tech Stack:** TypeScript, react-router, @tanstack/react-query, vitest+jsdom.

**Chạy FE (host):** `cd /home/desi/desi-order/frontend && npx vitest run src/features/hr && npx tsc --noEmit && npm run build`. Commit: tiếng Anh, không trailer, không MR link.

---

## File structure
- `frontend/src/features/hr/attendance-types.ts` — types
- `frontend/src/lib/api/hr-attendance.ts` — api fns
- `frontend/src/features/hr/queries.ts` — thêm hooks (hoặc file `attendance-queries.ts`)
- `frontend/src/features/hr/hooks/useGeolocation.ts`, `useCamera.ts`; `frontend/src/features/hr/lib/compress-image.ts`
- `frontend/src/features/hr/pages/AttendanceCheckInPage.tsx`
- `frontend/src/features/hr/pages/AttendanceLocationsPage.tsx` + `components/LocationFormDialog.tsx`
- Modify: `frontend/src/app/routes.tsx`, `routed-pages.tsx`, `frontend/src/lib/routes.ts` (section `attendance-locations`), `frontend/src/app/modules.ts`, `frontend/src/i18n/messages/hr.ts`
- `frontend/src/test/setup.ts` — (nếu cần) helpers mock ge/camera trong từng test

---

## Task 1: Types + API + queries

**Files:** Create `attendance-types.ts`, `frontend/src/lib/api/hr-attendance.ts`; Modify `queries.ts`; Test `frontend/src/features/hr/__tests__/attendance-queries.test.ts`

- [ ] **Step 1: Test thất bại**:
```typescript
import { describe, expect, it } from "vitest";
import { attendanceKeys } from "@/features/hr/queries";

describe("attendanceKeys", () => {
  it("config key", () => {
    expect(attendanceKeys.config).toEqual(["hr", "attendance", "config"]);
  });
});
```

- [ ] **Step 2: Chạy → FAIL** (`npx vitest run src/features/hr`).

- [ ] **Step 3: Implement** types — `attendance-types.ts`:
```typescript
export interface AttendanceLocation {
  id: number;
  name: string;
  latitude: number;
  longitude: number;
  radius_m: number;
  is_active?: boolean;
}
export interface AttendanceConfig {
  locations: AttendanceLocation[];
  today: { check_in: string | null; check_out: string | null };
}
export interface CheckInPayload { latitude: number; longitude: number; photo: string; }
export interface CheckInResult {
  kind: "check_in" | "check_out";
  occurred_at: string;
  distance_m: number;
  location_name: string;
}
export interface LocationPayload {
  name: string; latitude: number; longitude: number; radius_m: number; is_active?: boolean;
}
```
api — `frontend/src/lib/api/hr-attendance.ts`:
```typescript
import { fetchJson } from "@/services/http";
import type {
  AttendanceConfig, AttendanceLocation, CheckInPayload, CheckInResult, LocationPayload,
} from "@/features/hr/attendance-types";

const BASE = "/api/hr/attendance";

export async function getAttendanceConfig(): Promise<AttendanceConfig> {
  return fetchJson(`${BASE}/config`);
}
export async function checkIn(payload: CheckInPayload): Promise<CheckInResult> {
  return fetchJson(`${BASE}/check-in`, { method: "POST", body: JSON.stringify(payload) });
}
export async function getLocations(): Promise<AttendanceLocation[]> {
  const res: { items: AttendanceLocation[] } = await fetchJson(`${BASE}/locations`);
  return res.items;
}
export async function createLocation(p: LocationPayload): Promise<AttendanceLocation> {
  return fetchJson(`${BASE}/locations`, { method: "POST", body: JSON.stringify(p) });
}
export async function updateLocation(id: number, p: Partial<LocationPayload>): Promise<AttendanceLocation> {
  return fetchJson(`${BASE}/locations/${id}`, { method: "PATCH", body: JSON.stringify(p) });
}
export async function deleteLocation(id: number): Promise<void> {
  await fetchJson(`${BASE}/locations/${id}`, { method: "DELETE" });
}
```
queries — thêm vào `frontend/src/features/hr/queries.ts`:
```typescript
import {
  checkIn, createLocation, deleteLocation, getAttendanceConfig, getLocations, updateLocation,
} from "@/lib/api/hr-attendance";
import type { CheckInPayload, LocationPayload } from "@/features/hr/attendance-types";

export const attendanceKeys = {
  config: ["hr", "attendance", "config"] as const,
  locations: ["hr", "attendance", "locations"] as const,
};

export function useAttendanceConfig(enabled = true) {
  return useQuery({ queryKey: attendanceKeys.config, queryFn: getAttendanceConfig, enabled });
}
export function useCheckIn() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: CheckInPayload) => checkIn(p),
    onSuccess: () => qc.invalidateQueries({ queryKey: attendanceKeys.config }),
  });
}
export function useLocations(enabled = true) {
  return useQuery({ queryKey: attendanceKeys.locations, queryFn: getLocations, enabled });
}
export function useCreateLocation() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (p: LocationPayload) => createLocation(p),
    onSuccess: () => qc.invalidateQueries({ queryKey: attendanceKeys.locations }) });
}
export function useUpdateLocation() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: ({ id, p }: { id: number; p: Partial<LocationPayload> }) => updateLocation(id, p),
    onSuccess: () => qc.invalidateQueries({ queryKey: attendanceKeys.locations }) });
}
export function useDeleteLocation() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: number) => deleteLocation(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: attendanceKeys.locations }) });
}
```

- [ ] **Step 4: Chạy → PASS.** tsc clean.
- [ ] **Step 5: Commit** `feat(hr): attendance frontend types, api, queries`.

---

## Task 2: Hooks useGeolocation + useCamera + compress-image

**Files:** Create `frontend/src/features/hr/hooks/useGeolocation.ts`, `useCamera.ts`, `frontend/src/features/hr/lib/compress-image.ts`; Test `frontend/src/features/hr/__tests__/useGeolocation.test.ts`

- [ ] **Step 1: Test thất bại** (mock navigator.geolocation):
```typescript
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useGeolocation } from "@/features/hr/hooks/useGeolocation";

beforeEach(() => {
  Object.defineProperty(global.navigator, "geolocation", {
    configurable: true,
    value: {
      watchPosition: vi.fn((success) => {
        success({ coords: { latitude: 10.5, longitude: 106.5, accuracy: 8 } });
        return 1;
      }),
      clearWatch: vi.fn(),
    },
  });
});

describe("useGeolocation", () => {
  it("returns coords from watchPosition", async () => {
    const { result } = renderHook(() => useGeolocation());
    await waitFor(() => expect(result.current.coords?.latitude).toBe(10.5));
    expect(result.current.coords?.longitude).toBe(106.5);
  });
});
```

- [ ] **Step 2: Chạy → FAIL.**

- [ ] **Step 3: Implement**:
`useGeolocation.ts`:
```typescript
import { useEffect, useState } from "react";

export type Coords = { latitude: number; longitude: number; accuracy: number };

export function useGeolocation() {
  const [coords, setCoords] = useState<Coords | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!navigator.geolocation) {
      setError("Trình duyệt không hỗ trợ định vị.");
      return;
    }
    const id = navigator.geolocation.watchPosition(
      (pos) => {
        setCoords({ latitude: pos.coords.latitude, longitude: pos.coords.longitude, accuracy: pos.coords.accuracy });
        setError(null);
      },
      (err) => setError(err.message || "Không lấy được vị trí. Hãy bật định vị."),
      { enableHighAccuracy: true, maximumAge: 10000, timeout: 15000 },
    );
    return () => navigator.geolocation.clearWatch(id);
  }, []);

  return { coords, error };
}
```
`useCamera.ts` — quản lý `getUserMedia` stream, gắn vào video ref, `capture()` vẽ frame ra canvas:
```typescript
import { useCallback, useEffect, useRef, useState } from "react";

export function useCamera() {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function start() {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "user" }, audio: false,
        });
        if (cancelled) { stream.getTracks().forEach((t) => t.stop()); return; }
        streamRef.current = stream;
        if (videoRef.current) { videoRef.current.srcObject = stream; await videoRef.current.play(); }
        setReady(true);
      } catch {
        setError("Không truy cập được camera. Hãy cấp quyền camera.");
      }
    }
    start();
    return () => { cancelled = true; streamRef.current?.getTracks().forEach((t) => t.stop()); };
  }, []);

  const capture = useCallback((): string | null => {
    const video = videoRef.current;
    if (!video) return null;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.6);
  }, []);

  return { videoRef, ready, error, capture };
}
```
`compress-image.ts` — thu nhỏ data URL về cạnh dài ≤ maxEdge:
```typescript
export async function compressDataUrl(dataUrl: string, maxEdge = 640, quality = 0.6): Promise<string> {
  const img = new Image();
  await new Promise<void>((res, rej) => { img.onload = () => res(); img.onerror = () => rej(); img.src = dataUrl; });
  const scale = Math.min(1, maxEdge / Math.max(img.width, img.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(img.width * scale);
  canvas.height = Math.round(img.height * scale);
  const ctx = canvas.getContext("2d");
  if (!ctx) return dataUrl;
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", quality);
}
```
> `useCamera`/`compressDataUrl` khó test trong jsdom (canvas/video) — KHÔNG bắt buộc test ở task này; chỉ test `useGeolocation`. Giữ logic camera tối giản, test ở mức page (Task 3) bằng cách mock `useCamera`.

- [ ] **Step 4: Chạy `useGeolocation` test → PASS.** tsc clean.
- [ ] **Step 5: Commit** `feat(hr): geolocation/camera hooks + image compress`.

---

## Task 3: Trang `/hr/check-in` + route login-only + nav link

**Files:** Create `frontend/src/features/hr/pages/AttendanceCheckInPage.tsx`; Modify `frontend/src/app/routes.tsx`, `routed-pages.tsx`, top-nav/sidebar (link), `frontend/src/i18n/messages/hr.ts`; Test `frontend/src/features/hr/__tests__/AttendanceCheckInPage.test.tsx`

- [ ] **Step 1: Test thất bại** (mock api + hooks):
```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "@/i18n";

vi.mock("@/lib/api/hr-attendance", async () => {
  const a = await vi.importActual<typeof import("@/lib/api/hr-attendance")>("@/lib/api/hr-attendance");
  return { ...a, getAttendanceConfig: vi.fn(), checkIn: vi.fn() };
});
vi.mock("@/features/hr/hooks/useGeolocation", () => ({
  useGeolocation: () => ({ coords: { latitude: 10.0, longitude: 106.0, accuracy: 5 }, error: null }),
}));
vi.mock("@/features/hr/hooks/useCamera", () => ({
  useCamera: () => ({ videoRef: { current: null }, ready: true, error: null, capture: () => "data:image/jpeg;base64,x" }),
}));
import { getAttendanceConfig } from "@/lib/api/hr-attendance";
import AttendanceCheckInPage from "@/features/hr/pages/AttendanceCheckInPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><I18nProvider><MemoryRouter><AttendanceCheckInPage /></MemoryRouter></I18nProvider></QueryClientProvider>
  );
}
import { render } from "@testing-library/react";

beforeEach(() => {
  vi.mocked(getAttendanceConfig).mockResolvedValue({
    locations: [{ id: 1, name: "HQ", latitude: 10.0, longitude: 106.0, radius_m: 150 }],
    today: { check_in: null, check_out: null },
  });
});
afterEach(cleanup);

describe("AttendanceCheckInPage", () => {
  it("shows the check-in button when inside radius", async () => {
    renderPage();
    await waitFor(() => expect(getAttendanceConfig).toHaveBeenCalled());
    expect(await screen.findByRole("button", { name: /chấm công/i })).toBeEnabled();
  });
});
```

- [ ] **Step 2: Chạy → FAIL.**

- [ ] **Step 3: Implement** `AttendanceCheckInPage.tsx` (mobile-first):
  - Dùng `useAttendanceConfig()`, `useGeolocation()`, `useCamera()`, `useCheckIn()`.
  - Tính khoảng cách client tới điểm gần nhất (dùng haversine TS nội bộ trong page hoặc `compress-image` lib — viết hàm `distanceM` nhỏ trong page) để hiển thị + quyết định nút có enabled không (so với `radius_m` của điểm gần nhất). Server vẫn là chốt chặn cuối.
  - Hiển thị: trạng thái hôm nay (today.check_in/out), `<video ref>` preview, khoảng cách + "Trong/Ngoài khu vực".
  - Nút "Chấm công": disabled khi `!coords || !cameraReady || ngoài bán kính || mutation.isPending`. onClick: `capture()` → `compressDataUrl` → `checkIn({latitude, longitude, photo})`; hiện kết quả (Đã chấm Vào/Ra lúc ...) hoặc lỗi (422 → message khoảng cách từ `error`).
  - i18n keys `hr.checkin.*` (vi/en) + fallback.
  - Giữ file < 400 dòng; tách presentational nếu cần.

- [ ] **Step 4: Route login-only** — trong `frontend/src/app/routes.tsx` thêm route KHÔNG dùng `<Guard section>` mà bọc trực tiếp `<RequireAuth user={user}>` (không truyền `section`):
```typescript
{
  path: "hr/check-in",
  element: (
    <RequireAuth user={user}>
      <AttendanceCheckInRoute />
    </RequireAuth>
  ),
},
```
> Đọc routes.tsx: cách lấy `user` (qua `useAppShell()` trong `Guard`). Có thể tạo 1 wrapper `LoginOnly` tương tự `Guard` nhưng không truyền section: `function LoginOnly({children}){ const {user}=useAppShell(); return <RequireAuth user={user}>{children}</RequireAuth>; }`. Dùng nó cho route check-in.
- `routed-pages.tsx`: `const AttendanceCheckInPage = lazy(() => import("@/features/hr/pages/AttendanceCheckInPage")); export function AttendanceCheckInRoute() { return <AttendanceCheckInPage />; }`

- [ ] **Step 5: Link "Chấm công" cho mọi NV** — thêm link tới `/hr/check-in` ở nơi mọi user đăng nhập thấy (top-nav/sidebar trong `RootLayout`/app-shell). Đọc cấu trúc top-nav; thêm 1 mục/icon (vd `Fingerprint`/`Clock`) trỏ `/hr/check-in`, hiển thị cho mọi user (không gate theo section). Nếu không có chỗ tự nhiên, thêm vào user menu/avatar dropdown.

- [ ] **Step 6: Chạy test → PASS.** tsc + build.
- [ ] **Step 7: Commit** `feat(hr): self check-in page + login-only route`.

---

## Task 4: Trang cấu hình vị trí (admin)

**Files:** Create `frontend/src/features/hr/pages/AttendanceLocationsPage.tsx`, `frontend/src/features/hr/components/LocationFormDialog.tsx`; Modify `frontend/src/lib/routes.ts` (section + path), `frontend/src/app/modules.ts` (section), `routes.tsx`, `routed-pages.tsx`, `__tests__/modules.test.ts`, i18n; Test `AttendanceLocationsPage.test.tsx`

- [ ] **Step 1: Test thất bại** — mock `getLocations` → 1 điểm; render trang; assert tên "HQ" hiển thị + có nút "Thêm vị trí".

- [ ] **Step 2: Chạy → FAIL.**

- [ ] **Step 3: Implement**:
  - `AttendanceLocationsPage` (default export): `useLocations()` → bảng (Tên, Lat, Lng, Bán kính, Trạng thái, hành động Sửa/Xoá). Nút "Thêm vị trí" mở `LocationFormDialog`.
  - `LocationFormDialog` (FormDialogShell): fields name, latitude, longitude, radius_m, is_active; nút "Lấy vị trí hiện tại" gọi `navigator.geolocation.getCurrentPosition` điền lat/lng; gọi `useCreateLocation`/`useUpdateLocation`.
  - Đăng ký section `attendance-locations`:
    - `frontend/src/lib/routes.ts`: thêm `"attendance-locations"` vào `AppSection`; `SECTION_TO_PATH` thêm `"attendance-locations": "/hr/attendance-locations"`; `SECTION_PERMISSION` thêm `"attendance-locations": "employees"` (guard theo quyền employees).
    - `frontend/src/app/modules.ts`: thêm section `{ key: "attendance-locations", labelKey: "topnav.tabs.attendanceLocations", icon: MapPin, requires: "employees" }` vào module `hr`.
    - `frontend/src/app/__tests__/modules.test.ts`: thêm `"attendance-locations"` vào `ALL_SECTIONS` fixture (giữ test "mỗi section thuộc đúng 1 module" cân bằng; KHÔNG đụng lỗi pre-existing `lead-assignment`/`roles`).
    - `routes.tsx` + `routed-pages.tsx`: route `hr/attendance-locations` guard section `attendance-locations`.
  - i18n `hr.location.*` + `topnav.tabs.attendanceLocations` + `sidebar` nếu cần.

- [ ] **Step 4: Chạy test → PASS.** tsc + build + `npx vitest run src/app` (chỉ còn lỗi pre-existing `lead-assignment`/`roles`).
- [ ] **Step 5: Commit** `feat(hr): attendance locations admin page`.

---

## Task 5: Tổng kiểm tra + ghi chú

- [ ] **Step 1:** `npx vitest run src/features/hr src/app && npx tsc --noEmit && npm run build` → xanh (trừ lỗi modules.test pre-existing).
- [ ] **Step 2:** Ghi chú "Attendance Sprint B — đã làm" vào `docs/erp-hr-attendance.md`.
- [ ] **Step 3:** Commit + push + gửi MR link.

---

## Self-review
- **Spec coverage (§5):** trang check-in mobile (T3) với camera+GPS (T2) + route login-only + nav link (T3); trang cấu hình vị trí admin (T4); types/api/queries (T1). Phủ đủ.
- **Type consistency:** `AttendanceConfig{locations,today}`, `CheckInPayload{latitude,longitude,photo}`, `CheckInResult{kind,occurred_at,distance_m,location_name}`, `attendanceKeys.config/locations`, hooks `useAttendanceConfig/useCheckIn/useLocations/...` nhất quán api↔queries↔pages.
- **Login-only route:** dùng `RequireAuth` không `section` (đã xác nhận `section?` optional). Camera/canvas khó test trong jsdom → page test mock `useCamera`/`useGeolocation`; chỉ test logic hiển thị/nút.
- **Điểm cần xác minh khi code:** cấu trúc top-nav để chèn link "Chấm công"; `AppSection`/`SECTION_PERMISSION` ở `lib/routes.ts`; `FormDialogShell` props; `render` import trong test.
