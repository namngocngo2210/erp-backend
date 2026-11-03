# Warranty Best Create Order Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nút "Tạo đơn Best" cạnh 3 ô mã hành trình BH → dialog tạo vận đơn qua `POST /api/best-express/shipments`, thành công tự PATCH `code_best` vào field mã tương ứng.

**Architecture:** FE-only (BE đã đủ). API module typed + React Query mutation; helper thuần build payload/suggest code/localStorage sender (test được); dialog FormDialogShell; nút gắn ở 2 section form hành trình.

**Tech Stack:** React + TS + shadcn/ui + React Query + vitest.

**Spec:** `docs/superpowers/specs/2026-07-18-warranty-best-create-order-design.md`

## Global Constraints

- Worktree/branch như 2 đợt trước (`feat/warranty-claim-status-flow`). FE-only, KHÔNG sửa backend.
- Payload Order/Add: bắt buộc Code, ProductName, JourneyType(1), ServiceId(12491), Weight, {Source,Dest,Return}×{City,Ward,Address,Name,PhoneNumber}; District gửi "" (BE tự setdefault). DeclaredValue/CollectAmount (COD) thêm theo tài liệu Best.
- Chiều: leg `new`/`return` → Source=kho, Dest=khách; leg `recall` → đảo. Return block luôn = kho.
- Response 201 chỉ khai 201 → dùng `fetchJson` typed (không `apiPost`), theo ghi chú ở `warrantyJourneys.ts`.

### Task 1: API module + queries

**Files:**
- Create: `frontend/src/lib/api/bestExpress.ts`
- Modify: `frontend/src/features/warranty-claims/queries.ts`

**Interfaces:**
- Produces: `listBestAccounts(): Promise<BestAccount[]>`; `createBestShipment(body: CreateShipmentRequest): Promise<BestShipment>` (types từ `schema.d.ts`); hooks `useBestAccounts()` (staleTime 5'), `useCreateBestShipment()` (mutation, KHÔNG invalidate warranty keys — caller tự PATCH journey).

### Task 2: Helper thuần + unit test

**Files:**
- Create: `frontend/src/features/warranty-claims/components/best/best-order-form.ts`
- Test: `frontend/src/features/warranty-claims/components/best/best-order-form.test.ts`

**Interfaces:**
- Produces:
  - `type BestLeg = "new" | "recall" | "return"`; `LEG_FIELD: Record<BestLeg, "new_tracking_code" | "recall_code" | "return_tracking_code">`; `LEG_LABEL`.
  - `type AddressBlock = { name, phone, city, ward, address }` (string cả 5).
  - `suggestMerchantCode(claimId, seq, leg)` → `BH<claimId>J<seq><N|R|S>-<4 ký tự A-Z0-9>`.
  - `defaultFormForLeg(journey, claimId, leg, sender: AddressBlock)` → state form (code, productName từ product_sku||product_name, weight "500", declaredValue/cod theo leg: `return` dùng resolution_*, còn lại declared_value/cod_amount; sender/receiver đặt đúng chiều; receiver prefill từ journey receiver_*).
  - `buildOrderAddPayload(form)` → dict Order/Add (đảo Source/Dest theo leg nằm sẵn trong form: form giữ `from`/`to`/`returnBlock`); JourneyType 1, ServiceId 12491; số ép Number.
  - `loadSenderAddress()` / `saveSenderAddress(block)` — localStorage key `warranty-best-sender-address`, JSON, lỗi parse → block rỗng.
- Test: suggest code đúng format + khác nhau giữa 2 lần gọi; defaultFormForLeg đảo chiều cho recall (from=khách, to=kho) và thuận cho new/return; return leg lấy resolution_*; buildOrderAddPayload đủ REQUIRED_FIELDS + District ""; load/save localStorage round-trip.

### Task 3: Dialog + nút ở 3 ô mã

**Files:**
- Create: `frontend/src/features/warranty-claims/components/best/BestCreateOrderDialog.tsx`
- Modify: `frontend/src/features/warranty-claims/components/journey/journey-sections-send.tsx` (nút cạnh 2 ô, chỉ khi ô trống)
- Modify: `frontend/src/features/warranty-claims/components/journey/journey-sections-return.tsx` (nút cạnh ô mã hàng về)
- Test: `frontend/src/features/warranty-claims/components/best/BestCreateOrderDialog.test.tsx`

**Interfaces:**
- Consumes: Task 1 hooks, Task 2 helper, `useUpdateJourney` (queries), `FormDialogShell`, `Field` (journey-fields).
- Produces: `<BestCreateOrderDialog journey leg open onOpenChange />` — submit: mutation create → onSuccess: `saveSenderAddress`, toast mã, `updateJourney.mutate({id, patch: {[LEG_FIELD[leg]]: code_best}})`, đóng. Lỗi hiện message nguyên văn (toast). Nút: icon `PackagePlus` `aria-label="Tạo đơn Best (<nhãn chặng>)"`, chỉ render khi form field mã trống.
- Component test: nút chỉ hiện khi ô trống; mở dialog, submit mock API → gọi createBestShipment đúng shape + updateJourney đúng field.

### Task 4: Verify + push

- `npx vitest run src/features/warranty-claims`, `npm run typecheck`, `npm run build` → pass; commit từng task; merge master check conflict; push, gửi link MR.
