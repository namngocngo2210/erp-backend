# §7 Inline Best Order Form Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Khối §7 "Gửi đơn mới về khách" bung form tạo đơn Best ngay tại chỗ thay vì mở dialog, và tự điền ĐVVC + ngày lên đơn sau khi tạo.

**Architecture:** Tách `BestCreateOrderDialog` thành hook (`useBestOrderForm`) + thân form (`BestOrderFormBody`) + vỏ dialog. §7 dùng lại đúng hook và thân form đó, chỉ khác cách bày. Bộ test sẵn có của dialog là lưới an toàn cho bước tách.

**Tech Stack:** React 19 + TypeScript + vitest + @testing-library/react.

## Global Constraints

- Spec nguồn: `docs/superpowers/specs/2026-07-22-warranty-return-inline-best-order-design.md`.
- Nhánh `feat/warranty-return-inline-best-order`, cắt từ `feat/warranty-journey-conditional-flow`.
- KHÔNG đụng `best-order-form.ts` và KHÔNG đổi payload gửi Best.
- KHÔNG chuyển chặng gửi đi (`new`) / thu hồi (`recall`) sang inline — hai chặng đó giữ nguyên `BestCreateOrderButton` + dialog.
- KHÔNG tự điền phí vận chuyển (`return_shipping_fee`) — `total_fee` của Best bằng 0 lúc mới tạo.
- Tự điền `return_carrier: "best"` và `return_order_date` CHỈ cho chặng `return`; chặng `new`/`recall` dùng chung `carrier`/`order_date` ở §4, không được tự đè.
- ĐVVC + ngày lên đơn phải nằm trong CÙNG một PATCH với mã vận đơn, không chỉ ghi vào bản nháp.
- Hành vi dialog cho chặng `new`/`recall` phải KHÔNG đổi — bộ test sẵn có của dialog không được sửa kỳ vọng.
- Comment code tiếng Việt, khớp phong cách file lân cận. Commit message tiếng Anh, ngắn, KHÔNG kèm `Co-Authored-By`.

## Cách chạy test

Frontend chạy native. Từ gốc repo trong PowerShell, nạp PATH trước:

```
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
cd frontend
npx vitest run src/features/warranty-claims
npx tsc --noEmit
```

**Baseline:** `src/features/warranty-claims` = **79 passed, 0 failed**; `npx tsc --noEmit` sạch. Sau mỗi task phải là 79 + số test mới, không đỏ thêm gì.

**Git:** husky pre-commit cần `npx` trong PATH — chạy git qua PowerShell sau khi nạp PATH như trên. KHÔNG dùng `--no-verify`.

---

### Task 1: Hàm thuần tự điền theo chặng

**Files:**
- Create: `frontend/src/features/warranty-claims/components/best/best-order-autofill.ts`
- Test: `frontend/src/features/warranty-claims/components/best/best-order-autofill.test.ts`

**Interfaces:**
- Produces:
  - `todayIso(now?: Date): string` — ngày ĐỊA PHƯƠNG dạng `YYYY-MM-DD`
  - `autoFilledFields(leg: BestLeg, now?: Date): Record<string, string>`
- Task 2 dùng `autoFilledFields` khi build patch; Task 3 dùng `todayIso` khi đổ vào bản nháp.

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/features/warranty-claims/components/best/best-order-autofill.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { autoFilledFields, todayIso } from "./best-order-autofill";

describe("todayIso", () => {
  it("trả ngày ĐỊA PHƯƠNG, không phải UTC", () => {
    // 00:30 ngày 23/07 giờ VN (UTC+7) = 17:30 ngày 22/07 UTC.
    // Dùng toISOString() sẽ ra 2026-07-22 — sai một ngày với người dùng VN.
    const localMidnightish = new Date(2026, 6, 23, 0, 30, 0);
    expect(todayIso(localMidnightish)).toBe("2026-07-23");
  });

  it("đệm 0 cho tháng và ngày một chữ số", () => {
    expect(todayIso(new Date(2026, 0, 5))).toBe("2026-01-05");
  });
});

describe("autoFilledFields", () => {
  it("chặng gửi lại: điền ĐVVC và ngày lên đơn của §7", () => {
    expect(autoFilledFields("return", new Date(2026, 6, 22))).toEqual({
      return_carrier: "best",
      return_order_date: "2026-07-22",
    });
  });

  it("chặng gửi đi và thu hồi: KHÔNG điền gì", () => {
    // `carrier`/`order_date` ở §4 dùng chung cho cả hành trình — tự đè sẽ xoá
    // giá trị người dùng đã chọn cho chặng khác.
    expect(autoFilledFields("new", new Date(2026, 6, 22))).toEqual({});
    expect(autoFilledFields("recall", new Date(2026, 6, 22))).toEqual({});
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `npx vitest run src/features/warranty-claims/components/best/best-order-autofill.test.ts`
Expected: FAIL — không resolve được module `./best-order-autofill`.

- [ ] **Step 3: Cài đặt**

Tạo `frontend/src/features/warranty-claims/components/best/best-order-autofill.ts`:

```ts
import type { BestLeg } from "./best-order-form";

/**
 * Ngày hôm nay dạng `YYYY-MM-DD` theo múi giờ ĐỊA PHƯƠNG.
 *
 * KHÔNG dùng `toISOString().slice(0, 10)`: nó trả ngày UTC, nên 00:30 giờ VN
 * (UTC+7) sẽ ra ngày HÔM QUA. Nhận `now` để test được mà không phải giả lập
 * đồng hồ hệ thống.
 */
export function todayIso(now: Date = new Date()): string {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/**
 * Field tự điền kèm theo mã vận đơn khi tạo đơn Best thành công.
 *
 * CHỈ chặng gửi lại: §7 có `return_carrier`/`return_order_date` riêng. Chặng
 * gửi đi và thu hồi dùng chung `carrier`/`order_date` ở §4 cho cả hành trình —
 * tự đè sẽ xoá lựa chọn người dùng đã đặt cho chặng khác.
 */
export function autoFilledFields(leg: BestLeg, now: Date = new Date()): Record<string, string> {
  if (leg !== "return") return {};
  return { return_carrier: "best", return_order_date: todayIso(now) };
}
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `npx vitest run src/features/warranty-claims/components/best/best-order-autofill.test.ts`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/warranty-claims/components/best/best-order-autofill.ts frontend/src/features/warranty-claims/components/best/best-order-autofill.test.ts
git commit -m "feat(warranty): add per-leg autofill helpers for Best order creation"
```

---

### Task 2: Tách hook + thân form ra khỏi dialog

Đây là refactor: hành vi dialog cho chặng `new`/`recall` phải y nguyên. Bộ test sẵn có `BestCreateOrderDialog.test.tsx` là lưới an toàn — **không sửa kỳ vọng của nó**, chỉ thêm test mới cho phần tự điền.

**Files:**
- Create: `frontend/src/features/warranty-claims/components/best/useBestOrderForm.ts`
- Create: `frontend/src/features/warranty-claims/components/best/BestOrderFormBody.tsx`
- Modify: `frontend/src/features/warranty-claims/components/best/BestCreateOrderDialog.tsx`
- Test: `frontend/src/features/warranty-claims/components/best/BestCreateOrderDialog.test.tsx`

**Interfaces:**
- Consumes: `autoFilledFields` (Task 1).
- Produces:

```ts
// useBestOrderForm.ts
export type UseBestOrderFormArgs = {
  journey: WarrantyJourney;
  leg: BestLeg;
  /** Form đang sống (dialog mở / khối inline đang bung) — false thì không khởi tạo. */
  active: boolean;
  /** Gọi SAU khi PATCH mã thành công. Nơi gọi tự quyết đóng dialog hay thu khối. */
  onCreated: (code: string) => void;
};

export type UseBestOrderForm = {
  form: BestOrderFormState | null;
  setForm: React.Dispatch<React.SetStateAction<BestOrderFormState | null>>;
  /** Đã lọc `is_active` — nơi gọi chỉ việc render. */
  accounts: BestAccount[];
  /** Đã suy ra mặc định (account active đầu tiên) khi người dùng chưa chọn. */
  accountId: string;
  setAccountId: (id: string) => void;
  customReturn: boolean;
  setCustomReturn: (value: boolean) => void;
  submit: () => void;
  isPending: boolean;
};

export function useBestOrderForm(args: UseBestOrderFormArgs): UseBestOrderForm;
```

```tsx
// BestOrderFormBody.tsx — thuần trình bày, không gọi API, không giữ state riêng
export function BestOrderFormBody(props: {
  form: BestOrderFormState;
  setForm: React.Dispatch<React.SetStateAction<BestOrderFormState | null>>;
  accounts: BestAccount[];
  accountId: string;
  setAccountId: (id: string) => void;
  customReturn: boolean;
  setCustomReturn: (value: boolean) => void;
}): JSX.Element;
```

Task 3 dùng cả hai.

- [ ] **Step 1: Viết test thất bại cho phần tự điền**

Thêm vào `BestCreateOrderDialog.test.tsx`, dùng đúng helper/mock sẵn có ở đầu file:

```tsx
  it("chặng gửi lại: PATCH mang cả mã, ĐVVC và ngày lên đơn", () => {
    render(<BestCreateOrderButton journey={journey} leg="return" currentCode="" />);
    fireEvent.click(screen.getByRole("button", { name: /tạo đơn best \(gửi lại khách\)/i }));
    fireEvent.click(screen.getByRole("button", { name: "Tạo đơn" }));

    const [, createOpts] = mockCreateMutate.mock.calls[0];
    createOpts.onSuccess({ code_best: "84859706027573", merchant_order_code: "BHJ21S-AAAA" });

    const [vars] = mockUpdateJourneyMutate.mock.calls[0];
    expect(vars.patch.return_tracking_code).toBe("84859706027573");
    expect(vars.patch.return_carrier).toBe("best");
    expect(vars.patch.return_order_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("chặng thu hồi: PATCH CHỈ mang mã, không đè ĐVVC/ngày của §4", () => {
    render(<BestCreateOrderButton journey={journey} leg="recall" currentCode="" />);
    fireEvent.click(screen.getByRole("button", { name: /tạo đơn best \(thu hồi\)/i }));
    fireEvent.click(screen.getByRole("button", { name: "Tạo đơn" }));

    const [, createOpts] = mockCreateMutate.mock.calls[0];
    createOpts.onSuccess({ code_best: "BEST-RC", merchant_order_code: "BHJ21R-AAAA" });

    const [vars] = mockUpdateJourneyMutate.mock.calls[0];
    expect(vars.patch).toEqual({ recall_code: "BEST-RC" });
  });
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `npx vitest run src/features/warranty-claims/components/best/BestCreateOrderDialog.test.tsx`
Expected: test "chặng gửi lại" FAIL — `vars.patch.return_carrier` là `undefined` (hiện chỉ PATCH mã). Test "chặng thu hồi" PASS sẵn.

- [ ] **Step 3: Tạo hook**

Tạo `frontend/src/features/warranty-claims/components/best/useBestOrderForm.ts`:

```ts
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
  useBestAccounts,
  useCreateBestShipment,
  useUpdateJourney,
} from "@/features/warranty-claims/queries";
import type { WarrantyJourney } from "@/features/warranty-claims/warranty-claim-types";
import type { BestAccount } from "@/lib/api/bestExpress";
import { parseAddress } from "@/lib/api/orders";
import { autoFilledFields } from "./best-order-autofill";
import {
  LEG_FIELD,
  applyParsedAddress,
  buildOrderAddPayload,
  customerSideOf,
  defaultFormForLeg,
  loadSenderAddress,
  saveSenderAddress,
  shopSideOf,
  type AddressBlock,
  type BestLeg,
  type BestOrderFormState,
} from "./best-order-form";

export type UseBestOrderFormArgs = {
  journey: WarrantyJourney;
  leg: BestLeg;
  /** Form đang sống (dialog mở / khối inline đang bung) — false thì không khởi tạo. */
  active: boolean;
  /** Gọi SAU khi PATCH mã thành công. Nơi gọi tự quyết đóng dialog hay thu khối. */
  onCreated: (code: string) => void;
};

export type UseBestOrderForm = {
  form: BestOrderFormState | null;
  setForm: React.Dispatch<React.SetStateAction<BestOrderFormState | null>>;
  accounts: BestAccount[];
  accountId: string;
  setAccountId: (id: string) => void;
  customReturn: boolean;
  setCustomReturn: (value: boolean) => void;
  submit: () => void;
  isPending: boolean;
};

/** Khối kho theo loại đơn: đơn thu hồi thì kho là bên NHẬN, còn lại là bên GIAO. */
function senderBlockOf(form: BestOrderFormState): AddressBlock {
  return shopSideOf(form.journeyType) === "to" ? form.to : form.from;
}

/**
 * State + nghiệp vụ tạo đơn Best cho 1 chặng, dùng chung cho dialog (chặng gửi
 * đi/thu hồi) và khối §7 bung inline (chặng gửi lại).
 *
 * KHÔNG biết gì về dialog: nơi gọi tự đóng/thu qua `onCreated`. Nhờ vậy §7 tái
 * dùng được mà không phải nhân bản toàn bộ logic.
 */
export function useBestOrderForm({
  journey,
  leg,
  active,
  onCreated,
}: UseBestOrderFormArgs): UseBestOrderForm {
  const accountsQuery = useBestAccounts();
  const create = useCreateBestShipment();
  const updateJourney = useUpdateJourney();
  const [form, setForm] = useState<BestOrderFormState | null>(null);
  const [accountId, setAccountId] = useState<string>("");
  // Return mặc định = địa chỉ kho; mở khối riêng khi cần hoàn về nơi khác.
  const [customReturn, setCustomReturn] = useState(false);

  // Khởi tạo form mỗi lần form BẮT ĐẦU sống (prefill theo chặng + địa chỉ kho
  // đã nhớ), rồi parse địa chỉ thô của khách ở nền — không chặn thao tác.
  useEffect(() => {
    if (!active) return;
    const initial = defaultFormForLeg(journey, leg, loadSenderAddress());
    setForm(initial);
    setCustomReturn(false);

    const raw = (journey.receiver_raw_address || "").trim();
    if (!raw) return;
    let cancelled = false;
    parseAddress(raw)
      .then((parsed) => {
        if (cancelled) return;
        // Tính "side" từ state HIỆN TẠI, không phải initial lúc mount — người
        // dùng có thể đã đổi Loại đơn hàng (swap 2 khối) trong lúc request bay.
        setForm((f) => {
          if (!f) return f;
          const side = customerSideOf(f.journeyType);
          return { ...f, [side]: applyParsedAddress(f[side], parsed) };
        });
      })
      .catch(() => {
        // Không parse được thì giữ nguyên prefill thô, người dùng tự điền.
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, journey.id, leg]);

  const accounts = (accountsQuery.data ?? []).filter((a) => a.is_active);
  const effectiveAccountId = accountId || (accounts[0] ? String(accounts[0].id) : "");

  const submit = () => {
    if (!form) return;
    const sender = senderBlockOf(form);
    const payload = buildOrderAddPayload(
      customReturn ? form : { ...form, returnBlock: sender },
    );
    create.mutate(
      {
        account_id: effectiveAccountId ? Number(effectiveAccountId) : null,
        payload,
      },
      {
        onSuccess: (shipment) => {
          saveSenderAddress(sender);
          const code = (shipment.code_best || shipment.merchant_order_code || "").trim();
          if (!code) {
            // Không có mã thì không PATCH (patch rỗng = 200 nhưng chẳng ghi gì).
            toast.error("Best không trả về mã vận đơn — kiểm tra lại trên trang Best.");
            return;
          }
          // ĐVVC + ngày lên đơn đi CÙNG patch với mã: chỉ ghi vào bản nháp thì
          // đóng trang là mất, và form hiển thị dữ liệu server không hề có.
          updateJourney.mutate(
            {
              id: journey.id,
              patch: { [LEG_FIELD[form.leg]]: code, ...autoFilledFields(form.leg) },
            },
            {
              onSuccess: () => {
                onCreated(code);
                toast.success(`Đã tạo vận đơn Best: ${code}`);
              },
              // Vận đơn ĐÃ tạo thật phía Best — hiện mã để người dùng chép tay,
              // tuyệt đối không nuốt lỗi này.
              onError: () =>
                toast.error(
                  `Đã tạo vận đơn Best ${code} nhưng LƯU MÃ VÀO HÀNH TRÌNH THẤT BẠI — ` +
                    "hãy chép mã này và điền tay vào ô mã vận đơn.",
                  { duration: 30_000 },
                ),
            },
          );
        },
        onError: (err) =>
          toast.error(err instanceof Error ? err.message : "Tạo vận đơn Best thất bại"),
      },
    );
  };

  return {
    form,
    setForm,
    accounts,
    accountId: effectiveAccountId,
    setAccountId,
    customReturn,
    setCustomReturn,
    submit,
    isPending: create.isPending || updateJourney.isPending,
  };
}
```

- [ ] **Step 4: Tạo thân form**

Tạo `frontend/src/features/warranty-claims/components/best/BestOrderFormBody.tsx`.

Chuyển **nguyên văn** từ `BestCreateOrderDialog.tsx` sang file mới:
- component `AddressFields` (toàn bộ, kể cả `handleRawBlur`)
- toàn bộ JSX bên trong `<div className="space-y-6">` của dialog (từ `<FormGrid>` đầu tiên tới hết khối checkbox hoàn hàng)

Vỏ component mới:

```tsx
/**
 * Thân form tạo đơn Best — thuần trình bày, không gọi API, không giữ state.
 * Dùng chung cho dialog (chặng gửi đi/thu hồi) và khối §7 bung inline.
 */
export function BestOrderFormBody({
  form,
  setForm,
  accounts,
  accountId,
  setAccountId,
  customReturn,
  setCustomReturn,
}: {
  form: BestOrderFormState;
  setForm: React.Dispatch<React.SetStateAction<BestOrderFormState | null>>;
  accounts: BestAccount[];
  accountId: string;
  setAccountId: (id: string) => void;
  customReturn: boolean;
  setCustomReturn: (value: boolean) => void;
}) {
  const setField = <K extends keyof BestOrderFormState>(key: K, value: BestOrderFormState[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f));

  return <div className="space-y-6">{/* JSX chuyển sang, không sửa nội dung */}</div>;
}
```

Trong JSX vừa chuyển, đổi đúng hai chỗ tham chiếu:
- `activeAccounts` → `accounts`
- `effectiveAccountId` → `accountId`

Mọi `id` của input giữ NGUYÊN (`best-code`, `best-from-name`, …) — test sẵn có tìm theo các id đó.

- [ ] **Step 5: Rút gọn dialog**

`BestCreateOrderDialog.tsx` giờ chỉ còn vỏ:

```tsx
export function BestCreateOrderDialog({ journey, leg, open, onOpenChange, onCreated }: Props) {
  const best = useBestOrderForm({
    journey,
    leg,
    active: open,
    onCreated: (code) => {
      onCreated?.(code);
      // Chỉ đóng SAU khi PATCH xong: nút cha unmount dialog theo `open`, đóng
      // sớm thì mutation lưu mã chết theo component và mã Best mất trắng.
      onOpenChange(false);
    },
  });

  if (!best.form) return <Dialog open={open} onOpenChange={onOpenChange} />;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <FormDialogShell
        title={`Tạo đơn Best — ${LEG_LABEL[leg]}`}
        description={`Hành trình #${journey.seq}. Tạo vận đơn thật phía Best; lỗi từ Best hiển thị nguyên văn.`}
        onSubmit={(e) => {
          e.preventDefault();
          best.submit();
        }}
        footer={
          <>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Hủy
            </Button>
            <Button type="submit" disabled={best.isPending}>
              {best.isPending ? "Đang tạo..." : "Tạo đơn"}
            </Button>
          </>
        }
      >
        <BestOrderFormBody
          form={best.form}
          setForm={best.setForm}
          accounts={best.accounts}
          accountId={best.accountId}
          setAccountId={best.setAccountId}
          customReturn={best.customReturn}
          setCustomReturn={best.setCustomReturn}
        />
      </FormDialogShell>
    </Dialog>
  );
}
```

Xoá khỏi dialog mọi import không còn dùng (`useEffect`, `useState`, `parseAddress`, `buildOrderAddPayload`, các helper form…). ESLint sẽ chỉ ra.

Lưu ý `title` giờ dùng `LEG_LABEL[leg]` chứ không `LEG_LABEL[form.leg]` — cùng giá trị, nhưng đọc từ prop nên không phụ thuộc form đã khởi tạo xong hay chưa.

- [ ] **Step 6: Chạy test để xác nhận PASS**

Run: `npx vitest run src/features/warranty-claims` rồi `npx tsc --noEmit`
Expected: toàn bộ PASS (79 cũ + 2 mới = 81), typecheck sạch.

Nếu test cũ đỏ, bạn đã làm đổi hành vi trong lúc tách — sửa lại cho khớp, ĐỪNG sửa kỳ vọng của test cũ.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/warranty-claims/components/best/
git commit -m "refactor(warranty): split Best order form into hook and body"
```

---

### Task 3: §7 bung form inline

**Files:**
- Modify: `frontend/src/features/warranty-claims/components/journey/journey-sections-return.tsx` (`ReturnSection`)
- Test: `frontend/src/features/warranty-claims/components/JourneyCard.test.tsx`

**Interfaces:**
- Consumes: `useBestOrderForm`, `BestOrderFormBody` (Task 2); `todayIso` (Task 1).

- [ ] **Step 1: Viết test thất bại**

File `JourneyCard.test.tsx` đã có sẵn: `mockJourney` (dòng 30, `id: 1`), `renderCard(journey)`, `expandCard()`, và helper `cardWith(overrides: JourneyOverrides)` (dòng 114). Dùng đúng chúng — `JourneyOverrides` là kiểu đã nới `shipping_form`/`inspection_result` thành `string` để test dùng được giá trị `""`.

§7 chỉ hiện khi `shipping_form: "warranty_recall"` + mã thu hồi `delivered` — mọi test dưới đây phải đặt đúng điều kiện đó.

Thêm vào cuối file:

```tsx
  const returnSectionCard = (overrides: JourneyOverrides = {}) =>
    cardWith({
      shipping_form: "warranty_recall",
      inspection_result: "",
      return_tracking_code: "",
      vc_groups: { new: null, recall: "delivered", return: null, main: "delivered" },
      ...overrides,
    });

  it("§7 mã trống → hiện nút Tạo đơn Best, chưa bung form", () => {
    returnSectionCard();

    expect(screen.getByRole("button", { name: "Tạo đơn Best" })).toBeInTheDocument();
    expect(document.querySelector("#best-code")).not.toBeInTheDocument();
  });

  it("§7 bấm Tạo đơn Best → form bung tại chỗ, 4 ô tạm ẩn", () => {
    returnSectionCard();
    fireEvent.click(screen.getByRole("button", { name: "Tạo đơn Best" }));

    expect(document.querySelector("#best-code")).toBeInTheDocument();
    expect(document.querySelector("#journey-1-return_carrier")).not.toBeInTheDocument();
  });

  it("§7 bấm Hủy → thu lại, 4 ô trở lại", () => {
    returnSectionCard();
    fireEvent.click(screen.getByRole("button", { name: "Tạo đơn Best" }));
    fireEvent.click(screen.getByRole("button", { name: "Hủy" }));

    expect(document.querySelector("#best-code")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tạo đơn Best" })).toBeInTheDocument();
  });

  it("§7 đã có mã → KHÔNG hiện nút tạo", () => {
    returnSectionCard({ return_tracking_code: "84859706027573" });

    expect(screen.queryByRole("button", { name: "Tạo đơn Best" })).not.toBeInTheDocument();
  });
```

`journey-1-return_carrier` là id do `journeyFieldId(journey.id, "return_carrier")` sinh; `mockJourney.id` bằng `1` nên chuỗi trên đã đúng, không phải đổi.

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `npx vitest run src/features/warranty-claims/components/JourneyCard.test.tsx`
Expected: FAIL — không tìm thấy nút tên đúng "Tạo đơn Best" (hiện nút là icon có `aria-label` dạng `Tạo đơn Best (Gửi lại khách)`).

- [ ] **Step 3: Viết lại `ReturnSection`**

Trong `journey-sections-return.tsx`, thay nguyên `ReturnSection`:

```tsx
/** §7 Gửi đơn mới về khách — bung form tạo đơn Best ngay tại chỗ. */
export function ReturnSection({ journey, form, set, setSynced }: JourneySectionProps) {
  const fieldId = (name: string) => journeyFieldId(journey.id, name);
  const [expanded, setExpanded] = useState(false);

  const best = useBestOrderForm({
    journey,
    leg: "return",
    active: expanded,
    onCreated: (code) => {
      // Ba field này VỪA được PATCH xuống server trong cùng một lệnh (xem
      // useBestOrderForm) — đổ vào bản nháp bằng setSynced để không bật cờ
      // "chưa lưu". `todayIso()` tính lại ở đây, cùng ngày với lúc PATCH.
      setSynced("return_tracking_code", code);
      setSynced("return_carrier", "best");
      setSynced("return_order_date", todayIso());
      setExpanded(false);
    },
  });

  return (
    <section>
      <h4 className={sectionTitleClass}>🛡️ Gửi đơn mới về khách</h4>
      {expanded && best.form ? (
        <div className="space-y-4">
          <BestOrderFormBody
            form={best.form}
            setForm={best.setForm}
            accounts={best.accounts}
            accountId={best.accountId}
            setAccountId={best.setAccountId}
            customReturn={best.customReturn}
            setCustomReturn={best.setCustomReturn}
          />
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setExpanded(false)}>
              Hủy
            </Button>
            <Button type="button" onClick={best.submit} disabled={best.isPending}>
              {best.isPending ? "Đang tạo..." : "Tạo đơn"}
            </Button>
          </div>
        </div>
      ) : (
        <FormGrid>
          <Field label="Đơn vị vận chuyển (hàng về)" htmlFor={fieldId("return_carrier")}>
            <EnumSelect
              id={fieldId("return_carrier")}
              value={form.return_carrier}
              onValueChange={(v) => set("return_carrier", v)}
              options={CARRIER_OPTIONS}
            />
          </Field>
          <Field label="Phí vận chuyển (hàng về)" htmlFor={fieldId("return_shipping_fee")}>
            <Input
              id={fieldId("return_shipping_fee")}
              type="number"
              step="0.01"
              value={form.return_shipping_fee}
              onChange={(e) => set("return_shipping_fee", e.target.value)}
            />
          </Field>
          <Field label="Ngày lên đơn (hàng về)" htmlFor={fieldId("return_order_date")}>
            <Input
              id={fieldId("return_order_date")}
              type="date"
              value={form.return_order_date}
              onChange={(e) => set("return_order_date", e.target.value)}
            />
          </Field>
          <Field label="Mã vận đơn (hàng về)" htmlFor={fieldId("return_tracking_code")}>
            {/* Trạng thái giao chặng gửi lại đọc tự động từ mã Best (badge). */}
            <div className="flex items-center gap-2">
              <Input
                id={fieldId("return_tracking_code")}
                value={form.return_tracking_code}
                onChange={(e) => set("return_tracking_code", e.target.value)}
              />
              {form.return_tracking_code.trim() === "" && (
                <Button type="button" variant="outline" size="sm" onClick={() => setExpanded(true)}>
                  Tạo đơn Best
                </Button>
              )}
              <VcGroupBadge group={journey.vc_groups?.return} />
            </div>
          </Field>
        </FormGrid>
      )}
    </section>
  );
}
```

Bổ sung import ở đầu file:

```ts
import { useState } from "react";

import { Button } from "@/components/ui/button";

import { todayIso } from "../best/best-order-autofill";
import { BestOrderFormBody } from "../best/BestOrderFormBody";
import { useBestOrderForm } from "../best/useBestOrderForm";
```

Bỏ import `BestCreateOrderButton` khỏi file này nếu không còn chỗ nào dùng (chặng gửi đi/thu hồi ở `journey-sections-send.tsx` vẫn dùng — file đó không đụng).

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `npx vitest run src/features/warranty-claims` rồi `npx tsc --noEmit`
Expected: toàn bộ PASS (81 + 4 mới = 85), typecheck sạch.

- [ ] **Step 5: Chạy toàn bộ test frontend + lint**

Run: `npx vitest run` rồi `npm run lint`
Expected: chỉ còn các lỗi sẵn có ngoài `warranty-claims` (nếu có). Lint sạch.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/warranty-claims/components/journey/journey-sections-return.tsx frontend/src/features/warranty-claims/components/JourneyCard.test.tsx
git commit -m "feat(warranty): expand Best order form inline in return section"
```

---

## Ghi chú cho người thực hiện

- Thứ tự bắt buộc: 1 → 2 → 3.
- Task 2 là refactor thuần cộng đúng một thay đổi hành vi (PATCH thêm 2 field cho chặng gửi lại). Test cũ của dialog là lưới an toàn — đỏ nghĩa là bạn làm lệch, đừng sửa kỳ vọng.
- `BestCreateOrderButton` vẫn tồn tại và vẫn dùng cho chặng gửi đi/thu hồi ở `journey-sections-send.tsx`. Đừng xoá nó.
- Sau khi merge, kiểm tra bằng mắt: mở một phiếu thu hồi BH đã GTC chặng thu hồi → §7 hiện 4 ô + nút "Tạo đơn Best" → bấm → form bung tại chỗ → tạo đơn → form thu lại, ô mã có mã, ĐVVC = BEST, ngày lên đơn = hôm nay.
