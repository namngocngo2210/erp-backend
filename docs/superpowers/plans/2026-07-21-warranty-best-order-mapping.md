# Warranty Best Order Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Form "Tạo đơn Best" trong hành trình bảo hành gửi payload `Order/Add` đúng tên trường (COD/khai giá không còn bị mất), cho chọn loại đơn hàng, và tự parse địa chỉ 3 cấp.

**Architecture:** Toàn bộ logic build payload nằm ở helper thuần `best-order-form.ts` (không React, test bằng vitest); `BestCreateOrderDialog.tsx` chỉ là lớp UI gọi helper. Backend gần như không đổi — chỉ nới permission của endpoint parse địa chỉ đã có sẵn để trang bảo hành gọi được.

**Tech Stack:** React 19 + TypeScript + vitest + @testing-library/react (FE); Django REST Framework + pytest (BE).

## Global Constraints

- Spec nguồn: `docs/superpowers/specs/2026-07-21-warranty-best-order-mapping-design.md`.
- Enum `JourneyType` theo tài liệu Best V3: **1 = Giao hàng, 2 = Thu hồi, 3 = Đơn đổi hàng**.
- `ServiceId` giữ hằng `12491`, `PickupType` giữ hằng `2` — KHÔNG phơi ra form (chưa biết đủ enum).
- `Weight` đơn vị **gram** (không đổi sang kg).
- `*District` được phép rỗng (địa chỉ 2 cấp mới) — không thêm validate bắt buộc.
- Comment trong code viết tiếng Việt, khớp phong cách các file lân cận.
- Commit message tiếng Anh, ngắn, KHÔNG kèm dòng `Co-Authored-By`.
- Lệnh test FE chạy trong thư mục `frontend/`; lệnh test BE chạy trong `backend/`.

---

### Task 1: Nới quyền endpoint parse địa chỉ cho section `warranty`

Endpoint `POST /api/orders/parse-address` đang gate bằng `require_section(*ORDER_SECTIONS)`. CSKH bảo hành chỉ có `warranty:view` sẽ nhận 403, khiến tính năng auto-parse ở Task 6 chết câm.

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_address_view.py:58`
- Test: `backend/apps/orders/tests/test_parse_address_permission.py` (tạo mới)

**Interfaces:**
- Consumes: `apps.users.test_helpers.make_user_with_sections(username, sections)` — helper có sẵn, trả về `User` đã gắn role chứa mọi permission của các section truyền vào.
- Produces: không có symbol mới; các task FE dựa vào việc endpoint trả 200 cho user `warranty`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/orders/tests/test_parse_address_permission.py`:

```python
import pytest
from rest_framework.test import APIClient

from apps.users.test_helpers import make_user_with_sections


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.mark.django_db
def test_warranty_user_can_parse_address():
    """CSKH bảo hành dùng form tạo đơn Best → phải parse được địa chỉ."""
    client = _client(make_user_with_sections("warranty-parse", ["warranty"]))

    response = client.post(
        "/api/orders/parse-address",
        {"raw_address": "12 Lê Lợi, Phường Đại Mỗ, Quận Nam Từ Liêm, Hà Nội"},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["province"]


@pytest.mark.django_db
def test_orders_user_still_can_parse_address():
    """Không phá quyền cũ của các trang đơn."""
    client = _client(make_user_with_sections("orders-parse", ["orders"]))

    response = client.post(
        "/api/orders/parse-address", {"raw_address": "12 Lê Lợi, Hà Nội"}, format="json"
    )

    assert response.status_code == 200


@pytest.mark.django_db
def test_user_without_any_section_is_denied():
    """Không có quyền liên quan → vẫn 403, không mở toang endpoint."""
    client = _client(make_user_with_permissions("nobody-parse", []))

    response = client.post(
        "/api/orders/parse-address", {"raw_address": "12 Lê Lợi, Hà Nội"}, format="json"
    )

    assert response.status_code == 403
```

Import ở đầu file lấy cả hai helper:

```python
from apps.users.test_helpers import make_user_with_permissions, make_user_with_sections
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd backend && pytest apps/orders/tests/test_parse_address_permission.py -v`
Expected: `test_warranty_user_can_parse_address` FAIL với `assert 403 == 200`. Hai test còn lại PASS.

Nếu `make_user_with_sections("hr-parse", ["hr"])` báo lỗi vì section `hr` không tồn tại, đổi sang một section chắc chắn có trong DB seed (kiểm bằng `pytest --collect-only` không giúp — chạy `python manage.py shell -c "from apps.users.models import Permission; print(sorted(set(Permission.objects.values_list('section', flat=True))))"`) và dùng section nào không nằm trong `ORDER_SECTIONS` cũng không phải `warranty`.

- [ ] **Step 3: Sửa permission**

Trong `backend/apps/orders/transport/views/order_views_impl/order_address_view.py`, đổi decorator của `parse_raw_address`:

```python
# Trang bảo hành cũng dùng endpoint này để parse địa chỉ trước khi tạo đơn Best.
# Endpoint chỉ chuẩn hoá chuỗi người dùng tự nhập, không đọc dữ liệu đơn nào.
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS, "warranty")])
def parse_raw_address(request):
```

Giữ nguyên phần thân hàm và decorator `@extend_schema` phía trên.

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `cd backend && pytest apps/orders/tests/test_parse_address_permission.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/transport/views/order_views_impl/order_address_view.py backend/apps/orders/tests/test_parse_address_permission.py
git commit -m "fix(orders): allow warranty section to parse raw address"
```

---

### Task 2: Khai báo đủ trường trả về của `parseAddress` ở FE

Backend đã trả `ward` / `district` / `province` (xem `order_address_view.py:88-90`) nhưng interface TS chưa khai, nên Task 5 không đọc được nếu không sửa.

**Files:**
- Modify: `frontend/src/lib/api/orders.ts:36-44`

**Interfaces:**
- Produces: `ParsedAddress` có thêm `ward?: string`, `district?: string`, `province?: string`.

- [ ] **Step 1: Mở rộng interface**

Trong `frontend/src/lib/api/orders.ts`, thay khối `export interface ParsedAddress` bằng:

```ts
/** Result of parsing a raw, free-text address into structured KiotViet fields. */
export interface ParsedAddress {
  address?: string;
  kiotviet_location?: string;
  kiotviet_ward?: string;
  /** Tên hành chính chuẩn — Best cần 3 trường này cho Source/Dest/Return. */
  ward?: string;
  district?: string;
  province?: string;
  ward_code?: string;
  district_code?: string;
  province_code?: string;
}
```

- [ ] **Step 2: Xác nhận typecheck sạch**

Run: `cd frontend && npx tsc --noEmit`
Expected: không lỗi (chỉ thêm field optional nên không phá call-site nào).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api/orders.ts
git commit -m "feat(orders): expose parsed ward/district/province in ParsedAddress type"
```

---

### Task 3: Sửa mapping payload `Order/Add` + mở rộng form state

Đây là task sửa bug chính: FE đang gửi `DeclareValue`/`CollectionValue` trong khi Best và backend dùng `ProductPrice`/`CollectAmount`.

**Files:**
- Modify: `frontend/src/features/warranty-claims/components/best/best-order-form.ts`
- Test: `frontend/src/features/warranty-claims/components/best/best-order-form.test.ts`

**Interfaces:**
- Produces:
  - `AddressBlock` thêm `district: string` và `raw: string`.
  - `BestOrderFormState` thêm `length: string`, `width: string`, `height: string`, `note: string`.
  - `buildOrderAddPayload(form: BestOrderFormState): Record<string, unknown>` — không đổi chữ ký, đổi nội dung.
  - `NOTE_PRESETS: string[]` — dùng ở Task 7.

- [ ] **Step 1: Sửa test cho hành vi mới (test sẽ fail)**

Trong `best-order-form.test.ts`, thay fixture `sender` và test `buildOrderAddPayload`:

```ts
const sender: AddressBlock = {
  name: "Kho Đại Mỗ",
  phone: "0911111111",
  city: "Hà Nội",
  district: "Quận Nam Từ Liêm",
  ward: "Đại Mỗ",
  address: "Số 1 Đại Mỗ",
  raw: "Số 1 Đại Mỗ, Đại Mỗ, Nam Từ Liêm, Hà Nội",
};
```

Thay nguyên test `"buildOrderAddPayload đủ trường bắt buộc + District rỗng + số ép Number"` bằng:

```ts
it("buildOrderAddPayload dùng đúng tên trường tiền của Best", () => {
  const payload = buildOrderAddPayload(defaultFormForLeg(journey, "new", sender));
  const required = [
    "Code",
    "ProductName",
    "JourneyType",
    "ServiceId",
    "Weight",
    "SourceCity",
    "SourceWard",
    "SourceAddress",
    "SourceName",
    "SourcePhoneNumber",
    "DestName",
    "DestPhoneNumber",
    "DestAddress",
    "ReturnCity",
    "ReturnWard",
    "ReturnAddress",
    "ReturnName",
    "ReturnPhoneNumber",
  ];
  for (const key of required) {
    expect(payload[key], key).not.toBeUndefined();
  }
  expect(payload.ProductPrice).toBe(150000);
  expect(payload.CollectAmount).toBe(50000);
  expect(payload.DeclareValue).toBeUndefined();
  expect(payload.CollectionValue).toBeUndefined();
  expect(payload.PickupType).toBe(2);
  expect(payload.ServiceId).toBe(12491);
  expect(payload.Weight).toBe(500);
});

it("buildOrderAddPayload gửi District từ form, rỗng khi địa chỉ 2 cấp", () => {
  const form = defaultFormForLeg(journey, "new", sender);
  expect(buildOrderAddPayload(form).SourceDistrict).toBe("Quận Nam Từ Liêm");

  const twoLevel = { ...form, from: { ...form.from, district: "" } };
  expect(buildOrderAddPayload(twoLevel).SourceDistrict).toBe("");
});

it("buildOrderAddPayload bỏ kích thước rỗng/0, giữ kích thước dương", () => {
  const form = defaultFormForLeg(journey, "new", sender);
  const empty = buildOrderAddPayload(form);
  expect(empty.Length).toBeUndefined();
  expect(empty.Width).toBeUndefined();
  expect(empty.Height).toBeUndefined();

  const sized = buildOrderAddPayload({ ...form, length: "30", width: "0", height: "" });
  expect(sized.Length).toBe(30);
  expect(sized.Width).toBeUndefined();
  expect(sized.Height).toBeUndefined();
});

it("buildOrderAddPayload gửi Note đã trim", () => {
  const form = defaultFormForLeg(journey, "new", sender);
  expect(buildOrderAddPayload(form).Note).toBe("");
  expect(buildOrderAddPayload({ ...form, note: "  gọi trước khi giao  " }).Note).toBe(
    "gọi trước khi giao",
  );
});
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd frontend && npx vitest run src/features/warranty-claims/components/best/best-order-form.test.ts`
Expected: FAIL — lỗi TS `Object literal may only specify known properties` ở `district`/`raw`, và `expect(payload.ProductPrice).toBe(150000)` nhận `undefined`.

- [ ] **Step 3: Mở rộng type + sửa payload builder**

Trong `best-order-form.ts`, thay khối `AddressBlock` / `EMPTY_ADDRESS` / `BestOrderFormState`:

```ts
/** 1 khối địa chỉ trên form. `district` rỗng = địa chỉ 2 cấp mới. */
export type AddressBlock = {
  name: string;
  phone: string;
  city: string;
  district: string;
  ward: string;
  address: string;
  /** Chuỗi địa chỉ đầy đủ người dùng dán vào để parse — không gửi lên Best. */
  raw: string;
};

export const EMPTY_ADDRESS: AddressBlock = {
  name: "",
  phone: "",
  city: "",
  district: "",
  ward: "",
  address: "",
  raw: "",
};

export type BestOrderFormState = {
  leg: BestLeg;
  code: string;
  productName: string;
  weight: string; // gram
  declaredValue: string;
  cod: string;
  /** Kích thước (cm) — rỗng thì không gửi lên Best. */
  length: string;
  width: string;
  height: string;
  /** Ghi chú cho bưu tá. */
  note: string;
  /** Bên GIAO hàng cho Best (Source). */
  from: AddressBlock;
  /** Bên NHẬN hàng (Dest). */
  to: AddressBlock;
  /** Hàng không phát được trả về đâu (Return) — mặc định = địa chỉ kho. */
  returnBlock: AddressBlock;
};

/** Câu ghi chú bấm nhanh — lấy nguyên các chip của form web Best. */
export const NOTE_PRESETS: string[] = [
  "cho khách xem hàng, không cho thử",
  "không cho xem hàng",
  "cho khách xem và thử hàng",
  "nếu giao không thành công liên hệ shop",
  "Hàng dễ vỡ, vui lòng nhẹ tay",
  "Gọi điện cho khách trước khi giao",
  "Giao hàng vào giờ hành chính",
  "Cho xem hàng, không được bóc hộp",
];
```

Trong `customerBlock`, bổ sung field mới:

```ts
function customerBlock(journey: WarrantyJourney): AddressBlock {
  return {
    ...EMPTY_ADDRESS,
    name: journey.receiver_name,
    phone: journey.receiver_phone,
    address: journey.receiver_raw_address,
    raw: journey.receiver_raw_address,
  };
}
```

Trong `defaultFormForLeg`, thêm giá trị mặc định cho field mới — chèn ngay sau `cod:`:

```ts
    length: "",
    width: "",
    height: "",
    note: "",
```

Thay nguyên `buildOrderAddPayload`:

```ts
const num = (v: string): number => {
  const parsed = Number(v);
  return Number.isFinite(parsed) ? parsed : 0;
};

/** Kích thước là tùy chọn — Best hiểu "thiếu key" là không khai, gửi 0 thì không. */
function optionalNum(value: string, key: string): Record<string, number> {
  const parsed = num(value);
  return parsed > 0 ? { [key]: parsed } : {};
}

/**
 * Payload Order/Add nguyên bản Best V3.
 *
 * Tên trường tiền là `ProductPrice` (khai giá) và `CollectAmount` (thu hộ) —
 * KHÔNG phải `DeclareValue`/`CollectionValue`; gửi sai tên thì Best bỏ qua và
 * đơn ra COD = 0.
 */
export function buildOrderAddPayload(form: BestOrderFormState): Record<string, unknown> {
  return {
    Code: form.code.trim(),
    ProductName: form.productName.trim(),
    JourneyType: 1,
    ServiceId: 12491,
    PickupType: 2,
    Weight: num(form.weight),
    ProductPrice: num(form.declaredValue),
    CollectAmount: num(form.cod),
    ...optionalNum(form.length, "Length"),
    ...optionalNum(form.width, "Width"),
    ...optionalNum(form.height, "Height"),
    Note: form.note.trim(),
    SourceName: form.from.name.trim(),
    SourcePhoneNumber: form.from.phone.trim(),
    SourceCity: form.from.city.trim(),
    SourceDistrict: form.from.district.trim(),
    SourceWard: form.from.ward.trim(),
    SourceAddress: form.from.address.trim(),
    DestName: form.to.name.trim(),
    DestPhoneNumber: form.to.phone.trim(),
    DestCity: form.to.city.trim(),
    DestDistrict: form.to.district.trim(),
    DestWard: form.to.ward.trim(),
    DestAddress: form.to.address.trim(),
    ReturnName: form.returnBlock.name.trim(),
    ReturnPhoneNumber: form.returnBlock.phone.trim(),
    ReturnCity: form.returnBlock.city.trim(),
    ReturnDistrict: form.returnBlock.district.trim(),
    ReturnWard: form.returnBlock.ward.trim(),
    ReturnAddress: form.returnBlock.address.trim(),
  };
}
```

Trong `loadSenderAddress`, bổ sung 2 field mới vào object trả về:

```ts
    return {
      name: String(parsed.name ?? ""),
      phone: String(parsed.phone ?? ""),
      city: String(parsed.city ?? ""),
      district: String(parsed.district ?? ""),
      ward: String(parsed.ward ?? ""),
      address: String(parsed.address ?? ""),
      raw: String(parsed.raw ?? ""),
    };
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `cd frontend && npx vitest run src/features/warranty-claims/components/best/best-order-form.test.ts`
Expected: tất cả PASS.

Test `BestCreateOrderDialog.test.tsx` sẽ còn fail (thiếu ô mới) — Task 6/7/8 xử lý.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/warranty-claims/components/best/best-order-form.ts frontend/src/features/warranty-claims/components/best/best-order-form.test.ts
git commit -m "fix(warranty): send Best order payload with correct price and COD keys"
```

---

### Task 4: Loại đơn hàng (`JourneyType`) trong form state

**Files:**
- Modify: `frontend/src/features/warranty-claims/components/best/best-order-form.ts`
- Test: `frontend/src/features/warranty-claims/components/best/best-order-form.test.ts`

**Interfaces:**
- Consumes: `BestOrderFormState`, `AddressBlock` từ Task 3.
- Produces:
  - `type BestJourneyType = 1 | 2 | 3`
  - `JOURNEY_TYPE_OPTIONS: { value: BestJourneyType; label: string }[]`
  - `JOURNEY_TYPE_BLOCK_LABELS: Record<BestJourneyType, { from: string; to: string }>`
  - `shopSideOf(journeyType: BestJourneyType): "from" | "to"`
  - `customerSideOf(journeyType: BestJourneyType): "from" | "to"`
  - `applyJourneyType(form: BestOrderFormState, nextType: BestJourneyType): BestOrderFormState`
  - `BestOrderFormState` thêm `journeyType: BestJourneyType`

- [ ] **Step 1: Viết test thất bại**

Thêm vào cuối `describe("best-order-form", ...)` trong `best-order-form.test.ts`:

```ts
  it("JourneyType mặc định theo chặng: gửi đi 1, thu hồi 2, gửi lại 1", () => {
    expect(defaultFormForLeg(journey, "new", sender).journeyType).toBe(1);
    expect(defaultFormForLeg(journey, "recall", sender).journeyType).toBe(2);
    expect(defaultFormForLeg(journey, "return", sender).journeyType).toBe(1);
  });

  it("shopSideOf: chỉ đơn thu hồi mới có shop ở phía nhận", () => {
    expect(shopSideOf(1)).toBe("from");
    expect(shopSideOf(2)).toBe("to");
    expect(shopSideOf(3)).toBe("from");
    expect(customerSideOf(2)).toBe("from");
    expect(customerSideOf(3)).toBe("to");
  });

  it("applyJourneyType 1 → 3: shop vẫn ở phía gửi, KHÔNG swap", () => {
    const form = defaultFormForLeg(journey, "new", sender);
    const next = applyJourneyType(form, 3);
    expect(next.journeyType).toBe(3);
    expect(next.from).toEqual(form.from);
    expect(next.to).toEqual(form.to);
  });

  it("applyJourneyType 1 → 2 và 2 → 1: shop đổi phía nên swap nội dung 2 khối", () => {
    const form = defaultFormForLeg(journey, "new", sender);
    const recall = applyJourneyType(form, 2);
    expect(recall.journeyType).toBe(2);
    expect(recall.from).toEqual(form.to);
    expect(recall.to).toEqual(form.from);

    const back = applyJourneyType(recall, 1);
    expect(back.from).toEqual(form.from);
    expect(back.to).toEqual(form.to);
  });

  it("applyJourneyType không đụng khối hoàn hàng", () => {
    const form = defaultFormForLeg(journey, "new", sender);
    expect(applyJourneyType(form, 2).returnBlock).toEqual(form.returnBlock);
  });

  it("buildOrderAddPayload gửi JourneyType từ form", () => {
    expect(buildOrderAddPayload(defaultFormForLeg(journey, "recall", sender)).JourneyType).toBe(2);
  });
```

Bổ sung import ở đầu file test:

```ts
import {
  applyJourneyType,
  buildOrderAddPayload,
  customerSideOf,
  defaultFormForLeg,
  loadSenderAddress,
  saveSenderAddress,
  shopSideOf,
  suggestMerchantCode,
  type AddressBlock,
} from "./best-order-form";
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd frontend && npx vitest run src/features/warranty-claims/components/best/best-order-form.test.ts`
Expected: FAIL — `shopSideOf is not a function` / lỗi import không tồn tại.

- [ ] **Step 3: Cài đặt**

Trong `best-order-form.ts`, thêm ngay sau khối `LEG_LABEL`:

```ts
/** Loại đơn hàng theo tài liệu Best V3. */
export type BestJourneyType = 1 | 2 | 3;

export const JOURNEY_TYPE_OPTIONS: { value: BestJourneyType; label: string }[] = [
  { value: 1, label: "Giao hàng" },
  { value: 2, label: "Thu hồi" },
  { value: 3, label: "Đơn đổi hàng" },
];

/**
 * Nhãn 2 khối địa chỉ đổi theo loại đơn — giống form web của Best. Vị trí trong
 * payload KHÔNG đổi: Source luôn là bên đưa hàng cho bưu tá.
 */
export const JOURNEY_TYPE_BLOCK_LABELS: Record<BestJourneyType, { from: string; to: string }> = {
  1: { from: "Người gửi", to: "Người nhận" },
  2: { from: "Người mua", to: "Shop" },
  3: { from: "Shop", to: "Người mua" },
};

/** Phía nào của payload là SHOP — đơn thu hồi thì shop là bên nhận. */
export function shopSideOf(journeyType: BestJourneyType): "from" | "to" {
  return journeyType === 2 ? "to" : "from";
}

/** Phía còn lại là khách. */
export function customerSideOf(journeyType: BestJourneyType): "from" | "to" {
  return shopSideOf(journeyType) === "from" ? "to" : "from";
}

/**
 * Đổi loại đơn. Nếu shop chuyển phía (vd. 1 → 2) thì swap nội dung 2 khối, nếu
 * không dữ liệu sẽ đứng nguyên dưới nhãn mới và bị gửi sai bên.
 */
export function applyJourneyType(
  form: BestOrderFormState,
  nextType: BestJourneyType,
): BestOrderFormState {
  if (shopSideOf(form.journeyType) === shopSideOf(nextType)) {
    return { ...form, journeyType: nextType };
  }
  return { ...form, journeyType: nextType, from: form.to, to: form.from };
}

const LEG_JOURNEY_TYPE: Record<BestLeg, BestJourneyType> = {
  new: 1,
  recall: 2,
  return: 1,
};
```

Thêm field vào `BestOrderFormState` (ngay dưới `leg`):

```ts
  journeyType: BestJourneyType;
```

Trong `defaultFormForLeg`, thêm vào object trả về (ngay sau `leg,`):

```ts
    journeyType: LEG_JOURNEY_TYPE[leg],
```

Trong `buildOrderAddPayload`, đổi `JourneyType: 1,` thành:

```ts
    JourneyType: form.journeyType,
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `cd frontend && npx vitest run src/features/warranty-claims/components/best/best-order-form.test.ts`
Expected: tất cả PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/warranty-claims/components/best/best-order-form.ts frontend/src/features/warranty-claims/components/best/best-order-form.test.ts
git commit -m "feat(warranty): support Best JourneyType with shop-side aware swap"
```

---

### Task 5: Hàm đổ kết quả parse vào khối địa chỉ

**Files:**
- Modify: `frontend/src/features/warranty-claims/components/best/best-order-form.ts`
- Test: `frontend/src/features/warranty-claims/components/best/best-order-form.test.ts`

**Interfaces:**
- Consumes: `AddressBlock` (Task 3).
- Produces:
  - `type ParsedAddressLike = { address?: string; ward?: string; district?: string; province?: string }`
  - `applyParsedAddress(block: AddressBlock, parsed: ParsedAddressLike): AddressBlock`

Không import trực tiếp `ParsedAddress` từ `lib/api/orders` để helper thuần không kéo theo tầng HTTP; `ParsedAddress` là structural-compatible nên truyền thẳng vào được.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `best-order-form.test.ts`:

```ts
  it("applyParsedAddress map province/district/ward/address vào đúng ô", () => {
    const next = applyParsedAddress(
      { ...sender, city: "", district: "", ward: "", address: "" },
      {
        province: "Thành phố Huế",
        district: "Thuận Hóa",
        ward: "Phước Vĩnh",
        address: "Nhà 17h, ngõ 51, Kiệt 131 Trần Phú",
      },
    );
    expect(next.city).toBe("Thành phố Huế");
    expect(next.district).toBe("Thuận Hóa");
    expect(next.ward).toBe("Phước Vĩnh");
    expect(next.address).toBe("Nhà 17h, ngõ 51, Kiệt 131 Trần Phú");
  });

  it("applyParsedAddress giữ giá trị cũ khi parser trả rỗng, không đụng tên/SĐT", () => {
    const next = applyParsedAddress(sender, { province: "", district: "  ", ward: "Đại Mỗ" });
    expect(next.city).toBe("Hà Nội");
    expect(next.district).toBe("Quận Nam Từ Liêm");
    expect(next.ward).toBe("Đại Mỗ");
    expect(next.name).toBe("Kho Đại Mỗ");
    expect(next.phone).toBe("0911111111");
  });
```

Thêm `applyParsedAddress` vào danh sách import của file test.

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd frontend && npx vitest run src/features/warranty-claims/components/best/best-order-form.test.ts`
Expected: FAIL — `applyParsedAddress is not a function`.

- [ ] **Step 3: Cài đặt**

Thêm vào `best-order-form.ts`, ngay dưới `EMPTY_ADDRESS`:

```ts
/** Phần kết quả của `POST /api/orders/parse-address` mà form Best dùng tới. */
export type ParsedAddressLike = {
  address?: string;
  ward?: string;
  district?: string;
  province?: string;
};

/** Đổ kết quả parse vào khối địa chỉ — parser trả rỗng thì giữ giá trị đang có. */
export function applyParsedAddress(
  block: AddressBlock,
  parsed: ParsedAddressLike,
): AddressBlock {
  const keep = (value: string | undefined, current: string) => (value || "").trim() || current;
  return {
    ...block,
    city: keep(parsed.province, block.city),
    district: keep(parsed.district, block.district),
    ward: keep(parsed.ward, block.ward),
    address: keep(parsed.address, block.address),
  };
}
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `cd frontend && npx vitest run src/features/warranty-claims/components/best/best-order-form.test.ts`
Expected: tất cả PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/warranty-claims/components/best/best-order-form.ts frontend/src/features/warranty-claims/components/best/best-order-form.test.ts
git commit -m "feat(warranty): add applyParsedAddress helper for Best order form"
```

---

### Task 6: Select loại đơn hàng + nhãn khối động trên dialog

**Files:**
- Modify: `frontend/src/features/warranty-claims/components/best/BestCreateOrderDialog.tsx`
- Test: `frontend/src/features/warranty-claims/components/best/BestCreateOrderDialog.test.tsx`

**Interfaces:**
- Consumes: `applyJourneyType`, `JOURNEY_TYPE_OPTIONS`, `JOURNEY_TYPE_BLOCK_LABELS`, `BestJourneyType` (Task 4).
- Produces: dialog render select `#best-journey-type`; hai `AddressFields` dùng nhãn `📤 <label>` / `📥 <label>`.

- [ ] **Step 1: Sửa test hiện có cho hành vi mới (test sẽ fail)**

Trong `BestCreateOrderDialog.test.tsx`, đổi assert cuối của test `"ô trống → mở dialog..."`:

```ts
    expect(body.payload.JourneyType).toBe(2); // chặng thu hồi → đơn thu hồi
```

Thêm test mới vào cuối `describe`:

```ts
  it("nhãn 2 khối địa chỉ đổi theo loại đơn", () => {
    render(<BestCreateOrderButton journey={journey} leg="recall" currentCode="" />);
    fireEvent.click(screen.getByRole("button", { name: /tạo đơn best \(thu hồi\)/i }));

    // Mặc định chặng thu hồi = JourneyType 2 → Người mua giao cho Shop.
    expect(screen.getByText("📤 Người mua")).toBeInTheDocument();
    expect(screen.getByText("📥 Shop")).toBeInTheDocument();
  });
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd frontend && npx vitest run src/features/warranty-claims/components/best/BestCreateOrderDialog.test.tsx`
Expected: FAIL — `Unable to find an element with the text: 📤 Người mua` (đang render "📤 Bên gửi").

- [ ] **Step 3: Cài đặt**

Trong `BestCreateOrderDialog.tsx`, bổ sung import từ `./best-order-form`:

```ts
import {
  JOURNEY_TYPE_BLOCK_LABELS,
  JOURNEY_TYPE_OPTIONS,
  LEG_FIELD,
  LEG_LABEL,
  applyJourneyType,
  buildOrderAddPayload,
  defaultFormForLeg,
  loadSenderAddress,
  saveSenderAddress,
  shopSideOf,
  type AddressBlock,
  type BestJourneyType,
  type BestLeg,
  type BestOrderFormState,
} from "./best-order-form";
```

Thêm select vào đầu `<FormGrid>` (ngay trước `{activeAccounts.length > 1 && ...}`):

```tsx
            <Field label="Loại đơn hàng" htmlFor="best-journey-type">
              <Select
                value={String(form.journeyType)}
                onValueChange={(value) =>
                  setForm((f) =>
                    f ? applyJourneyType(f, Number(value) as BestJourneyType) : f,
                  )
                }
              >
                <SelectTrigger id="best-journey-type" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {JOURNEY_TYPE_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={String(option.value)}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
```

Đổi nhãn 2 khối địa chỉ:

```tsx
          <AddressFields
            title={`📤 ${JOURNEY_TYPE_BLOCK_LABELS[form.journeyType].from}`}
            block={form.from}
            onChange={(next) => setField("from", next)}
            idPrefix="best-from"
          />
          <AddressFields
            title={`📥 ${JOURNEY_TYPE_BLOCK_LABELS[form.journeyType].to}`}
            block={form.to}
            onChange={(next) => setField("to", next)}
            idPrefix="best-to"
          />
```

`senderBlock()` đang suy phía kho từ `form.leg` — đổi sang suy từ loại đơn để khớp select:

```tsx
  /** Khối kho theo loại đơn: đơn thu hồi thì kho là bên NHẬN, còn lại là bên GIAO. */
  function senderBlock(): AddressBlock {
    return shopSideOf(form!.journeyType) === "to" ? form!.to : form!.from;
  }
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `cd frontend && npx vitest run src/features/warranty-claims/components/best/BestCreateOrderDialog.test.tsx`
Expected: tất cả PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/warranty-claims/components/best/BestCreateOrderDialog.tsx frontend/src/features/warranty-claims/components/best/BestCreateOrderDialog.test.tsx
git commit -m "feat(warranty): pick Best order type with role-aware address labels"
```

---

### Task 7: Ô Quận/Huyện + địa chỉ đầy đủ tự parse

**Files:**
- Modify: `frontend/src/features/warranty-claims/components/best/BestCreateOrderDialog.tsx`
- Test: `frontend/src/features/warranty-claims/components/best/BestCreateOrderDialog.test.tsx`

**Interfaces:**
- Consumes: `applyParsedAddress`, `customerSideOf` (Task 4/5); `parseAddress` từ `@/lib/api/orders` (Task 2).
- Produces: mỗi khối địa chỉ có input `#<prefix>-raw` và `#<prefix>-district`.

- [ ] **Step 1: Viết test thất bại**

Trong `BestCreateOrderDialog.test.tsx`, thêm mock cho `parseAddress` ngay dưới mock `queries` (trước dòng `import { BestCreateOrderButton }`):

```ts
const mockParseAddress = vi.fn();

vi.mock("@/lib/api/orders", () => ({
  parseAddress: (raw: string) => mockParseAddress(raw),
}));
```

Thêm vào `beforeEach`:

```ts
    mockParseAddress.mockReset();
    mockParseAddress.mockResolvedValue({
      province: "Hà Nội",
      district: "Quận Hoàn Kiếm",
      ward: "Phường Tràng Tiền",
      address: "12 Lê Lợi, Phường Tràng Tiền, Quận Hoàn Kiếm, Hà Nội",
    });
```

Thêm test mới (dùng `waitFor` — bổ sung vào import từ `@testing-library/react`):

```ts
  it("mở dialog → tự parse địa chỉ khách vào đúng khối", async () => {
    render(<BestCreateOrderButton journey={journey} leg="new" currentCode="" />);
    fireEvent.click(screen.getByRole("button", { name: /tạo đơn best \(gửi đi\)/i }));

    expect(mockParseAddress).toHaveBeenCalledWith("12 Lê Lợi, Hà Nội");

    // Chặng gửi đi: khách là bên NHẬN.
    await waitFor(() => {
      expect(document.querySelector("#best-to-city")).toHaveValue("Hà Nội");
    });
    expect(document.querySelector("#best-to-district")).toHaveValue("Quận Hoàn Kiếm");
    expect(document.querySelector("#best-to-ward")).toHaveValue("Phường Tràng Tiền");
  });

  it("dán địa chỉ đầy đủ vào khối kho → blur là parse và điền 4 ô", async () => {
    render(<BestCreateOrderButton journey={journey} leg="new" currentCode="" />);
    fireEvent.click(screen.getByRole("button", { name: /tạo đơn best \(gửi đi\)/i }));

    const raw = document.querySelector("#best-from-raw") as HTMLInputElement;
    fireEvent.change(raw, { target: { value: "12 Lê Lợi, Hà Nội" } });
    fireEvent.blur(raw);

    await waitFor(() => {
      expect(document.querySelector("#best-from-city")).toHaveValue("Hà Nội");
    });
    expect(document.querySelector("#best-from-district")).toHaveValue("Quận Hoàn Kiếm");
  });

  it("parse lỗi → form vẫn dùng được, không văng", async () => {
    mockParseAddress.mockRejectedValue(new Error("403"));
    render(<BestCreateOrderButton journey={journey} leg="new" currentCode="" />);
    fireEvent.click(screen.getByRole("button", { name: /tạo đơn best \(gửi đi\)/i }));

    await waitFor(() => expect(mockParseAddress).toHaveBeenCalled());
    expect(document.querySelector("#best-to-name")).toHaveValue("Khách A");
  });
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd frontend && npx vitest run src/features/warranty-claims/components/best/BestCreateOrderDialog.test.tsx`
Expected: FAIL — `mockParseAddress` chưa được gọi và `#best-to-district` là `null`.

- [ ] **Step 3: Cài đặt**

Trong `BestCreateOrderDialog.tsx`, thêm import:

```ts
import { parseAddress } from "@/lib/api/orders";
```

và bổ sung `applyParsedAddress`, `customerSideOf` vào import từ `./best-order-form`.

Thay nguyên component `AddressFields`:

```tsx
/** 1 khối địa chỉ. Ô "Địa chỉ đầy đủ" blur là parse ra Tỉnh/Quận/Phường/Địa chỉ. */
function AddressFields({
  title,
  block,
  onChange,
  idPrefix,
}: {
  title: string;
  block: AddressBlock;
  onChange: (next: AddressBlock) => void;
  idPrefix: string;
}) {
  const set = (key: keyof AddressBlock) => (e: React.ChangeEvent<HTMLInputElement>) =>
    onChange({ ...block, [key]: e.target.value });

  const handleRawBlur = async (e: React.FocusEvent<HTMLInputElement>) => {
    const raw = e.target.value.trim();
    if (!raw) return;
    try {
      onChange(applyParsedAddress({ ...block, raw }, await parseAddress(raw)));
    } catch {
      // Parse hỏng/không có quyền — người dùng tự điền 4 ô bên dưới.
    }
  };

  return (
    <section>
      <h4 className={sectionTitleClass}>{title}</h4>
      <FormGrid>
        <Field label="Tên" htmlFor={`${idPrefix}-name`}>
          <Input id={`${idPrefix}-name`} value={block.name} onChange={set("name")} />
        </Field>
        <Field label="SĐT" htmlFor={`${idPrefix}-phone`}>
          <Input id={`${idPrefix}-phone`} value={block.phone} onChange={set("phone")} />
        </Field>
        <Field label="Địa chỉ đầy đủ (dán vào để tự tách)" htmlFor={`${idPrefix}-raw`}>
          <Input
            id={`${idPrefix}-raw`}
            value={block.raw}
            onChange={set("raw")}
            onBlur={handleRawBlur}
            placeholder="VD: 12 Lê Lợi, Tràng Tiền, Hoàn Kiếm, Hà Nội"
          />
        </Field>
        <Field label="Tỉnh/TP" htmlFor={`${idPrefix}-city`}>
          <Input id={`${idPrefix}-city`} value={block.city} onChange={set("city")} />
        </Field>
        <Field label="Quận/Huyện (để trống nếu 2 cấp)" htmlFor={`${idPrefix}-district`}>
          <Input id={`${idPrefix}-district`} value={block.district} onChange={set("district")} />
        </Field>
        <Field label="Phường/Xã" htmlFor={`${idPrefix}-ward`}>
          <Input id={`${idPrefix}-ward`} value={block.ward} onChange={set("ward")} />
        </Field>
        <Field label="Địa chỉ" htmlFor={`${idPrefix}-address`}>
          <Input id={`${idPrefix}-address`} value={block.address} onChange={set("address")} />
        </Field>
      </FormGrid>
    </section>
  );
}
```

Thay `useEffect` khởi tạo form để parse sẵn địa chỉ khách:

```tsx
  // Khởi tạo form mỗi lần MỞ dialog (prefill theo chặng + địa chỉ kho đã nhớ),
  // rồi parse địa chỉ thô của khách ở nền — không chặn thao tác trên form.
  useEffect(() => {
    if (!open) return;
    const initial = defaultFormForLeg(journey, leg, loadSenderAddress());
    setForm(initial);
    setCustomReturn(false);

    const raw = (journey.receiver_raw_address || "").trim();
    if (!raw) return;
    let cancelled = false;
    parseAddress(raw)
      .then((parsed) => {
        if (cancelled) return;
        const side = customerSideOf(initial.journeyType);
        setForm((f) => (f ? { ...f, [side]: applyParsedAddress(f[side], parsed) } : f));
      })
      .catch(() => {
        // Không parse được thì giữ nguyên prefill thô, người dùng tự điền.
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, journey.id, leg]);
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `cd frontend && npx vitest run src/features/warranty-claims/components/best/BestCreateOrderDialog.test.tsx`
Expected: tất cả PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/warranty-claims/components/best/BestCreateOrderDialog.tsx frontend/src/features/warranty-claims/components/best/BestCreateOrderDialog.test.tsx
git commit -m "feat(warranty): auto-parse addresses into 3-level fields on Best form"
```

---

### Task 8: Kích thước D×R×C + ghi chú kèm chip bấm nhanh

**Files:**
- Modify: `frontend/src/features/warranty-claims/components/best/BestCreateOrderDialog.tsx`
- Test: `frontend/src/features/warranty-claims/components/best/BestCreateOrderDialog.test.tsx`

**Interfaces:**
- Consumes: `NOTE_PRESETS` (Task 3).
- Produces: input `#best-length`, `#best-width`, `#best-height`, `#best-note`; nút chip đặt nội dung vào ô ghi chú.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `BestCreateOrderDialog.test.tsx`:

```ts
  it("nhập kích thước + bấm chip ghi chú → payload có Length/Height và Note", () => {
    render(<BestCreateOrderButton journey={journey} leg="new" currentCode="" />);
    fireEvent.click(screen.getByRole("button", { name: /tạo đơn best \(gửi đi\)/i }));

    fireEvent.change(document.querySelector("#best-length") as HTMLInputElement, {
      target: { value: "30" },
    });
    fireEvent.change(document.querySelector("#best-height") as HTMLInputElement, {
      target: { value: "10" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Gọi điện cho khách trước khi giao" }));
    fireEvent.click(screen.getByRole("button", { name: "Tạo đơn" }));

    const [body] = mockCreateMutate.mock.calls[0];
    expect(body.payload.Length).toBe(30);
    expect(body.payload.Height).toBe(10);
    expect(body.payload.Width).toBeUndefined();
    expect(body.payload.Note).toBe("Gọi điện cho khách trước khi giao");
  });
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd frontend && npx vitest run src/features/warranty-claims/components/best/BestCreateOrderDialog.test.tsx`
Expected: FAIL — `#best-length` là `null`.

- [ ] **Step 3: Cài đặt**

Trong `BestCreateOrderDialog.tsx`, bổ sung `NOTE_PRESETS` vào import từ `./best-order-form`.

Thêm 3 ô kích thước vào `<FormGrid>` đầu, ngay sau `Field` "Thu hộ (COD)":

```tsx
            <Field label="Dài (cm)" htmlFor="best-length">
              <Input
                id="best-length"
                type="number"
                value={form.length}
                onChange={(e) => setField("length", e.target.value)}
              />
            </Field>
            <Field label="Rộng (cm)" htmlFor="best-width">
              <Input
                id="best-width"
                type="number"
                value={form.width}
                onChange={(e) => setField("width", e.target.value)}
              />
            </Field>
            <Field label="Cao (cm)" htmlFor="best-height">
              <Input
                id="best-height"
                type="number"
                value={form.height}
                onChange={(e) => setField("height", e.target.value)}
              />
            </Field>
```

Thêm khối ghi chú ngay trước `<label className="flex items-center gap-2 text-sm">` (checkbox hoàn hàng):

```tsx
          <section>
            <h4 className={sectionTitleClass}>📝 Ghi chú cho bưu tá</h4>
            <Input
              id="best-note"
              value={form.note}
              onChange={(e) => setField("note", e.target.value)}
              placeholder="Để lại lời nhắn cho nhân viên giao hàng"
            />
            <div className="mt-2 flex flex-wrap gap-2">
              {NOTE_PRESETS.map((preset) => (
                <button
                  key={preset}
                  type="button"
                  className="rounded-full border px-2 py-0.5 text-xs text-muted-foreground hover:bg-accent"
                  onClick={() => setField("note", preset)}
                >
                  {preset}
                </button>
              ))}
            </div>
          </section>
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `cd frontend && npx vitest run src/features/warranty-claims/components/best/BestCreateOrderDialog.test.tsx`
Expected: tất cả PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/warranty-claims/components/best/BestCreateOrderDialog.tsx frontend/src/features/warranty-claims/components/best/BestCreateOrderDialog.test.tsx
git commit -m "feat(warranty): add parcel dimensions and courier note to Best form"
```

---

### Task 9: Chạy full test + lint, đẩy nhánh

**Files:** không sửa file nào mới; sửa những gì lint/test báo lỗi.

- [ ] **Step 1: Typecheck + lint FE**

Run: `cd frontend && npx tsc --noEmit && npm run lint`
Expected: không lỗi. Nếu ESLint báo `react-hooks/exhaustive-deps` ở `useEffect` của Task 7, giữ nguyên dòng `eslint-disable-next-line` đã có — không thêm dep vì `journey` là object đổi identity mỗi render.

- [ ] **Step 2: Chạy toàn bộ test FE**

Run: `cd frontend && npm run test`
Expected: toàn bộ suite PASS.

- [ ] **Step 3: Chạy test BE liên quan**

Run: `cd backend && pytest apps/orders apps/best_express apps/warranty -q`
Expected: toàn bộ PASS.

- [ ] **Step 4: Commit phần sửa lint (nếu có) và đẩy nhánh**

```bash
git add -A
git commit -m "chore(warranty): fix lint after Best form rework" || echo "không có gì để commit"
git push -u origin fix/warranty-best-order-mapping
```

- [ ] **Step 5: Xác minh thủ công trên môi trường test (BẮT BUỘC trước khi merge)**

Repo không lưu tài liệu Best V3 đầy đủ; tên trường `ProductPrice`/`CollectAmount` suy ra từ `best_smoke_test` đã chạy thật. Phải xác nhận bằng đơn thật:

```bash
cd backend && python manage.py best_smoke_test --account-id <id account environment=test> --create
```

Mở đơn vừa tạo trên trang V9 của Best và kiểm tra: **COD và khai giá phải khác 0**. Nếu vẫn 0 thì tên trường sai — dừng lại, báo lại trước khi merge.

---

## Ghi chú cho người thực hiện

- Ba task đầu (1, 2, 3) độc lập nhau, có thể làm song song. Task 4 và 5 nối tiếp Task 3 (cùng file helper). Task 6, 7, 8 nối tiếp nhau (cùng file dialog).
- Sau Task 3, `BestCreateOrderDialog.test.tsx` sẽ tạm fail — đó là dự kiến, Task 6 trở đi mới xanh lại. Đừng "sửa cho xanh" bằng cách quay lại tên trường cũ.
- Không đổi `ServiceId`/`PickupType` thành select dù thấy form web Best có — chưa biết đủ enum, đoán sai thì sai cước hoặc sai tuyến.
