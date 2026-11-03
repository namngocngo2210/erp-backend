# Warranty Activation / Expiry / Days-Left Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm 3 cột **Ngày kích hoạt** (chọn được), **Ngày hết hạn** (= kích hoạt + số tháng bảo hành, mặc định 24) và **Số ngày bảo hành** (số ngày còn lại tới hạn) vào phiếu bảo hành — bảng danh sách, trang chi tiết và file Excel.

**Architecture:** Backend là nguồn sự thật duy nhất: `WarrantyClaim` lưu 2 field mới (`activation_date`, `warranty_months`), hai giá trị hết hạn / còn lại là **property dẫn xuất** (không lưu DB) và được serializer trả về. Frontend chỉ render + PATCH.

**Tech Stack:** Django 5 + DRF + drf-spectacular (OpenAPI), pytest; React + TypeScript + React Query + shadcn/ui, openapi-typescript (`npm run gen:api`).

## Global Constraints

- Branch: `feat/warranty-activation-expiry`, worktree `../desi-order-warranty-exp`. **Không tự tạo branch mới.**
- Spec nguồn: `docs/warranty-activation-expiry.md`. Không thêm scope ngoài spec (mục "Không làm").
- Thời hạn mặc định: `warranty_months = 24`, hợp lệ 1–120.
- `warranty_days_left` là số nguyên **có dấu** (âm = đã quá hạn), `None` khi chưa có `activation_date`.
- Mốc "hôm nay" dùng `django.utils.timezone.localdate()` (giờ VN), không dùng UTC `date.today()`.
- Commit message tiếng Anh, ngắn gọn, **không** kèm dòng `Co-Authored-By`.
- Chạy test backend trên Windows: image `desi-order-web-test`, luôn truyền DB riêng, ví dụ:
  `docker run --rm --network desi-order_default -e POSTGRES_DB=desi_wexp -e REDIS_PASSWORD=testpassword desi-order-web-test pytest apps/warranty -q`
  (nếu image chưa có: `docker build -f backend/Dockerfile.test -t desi-order-web-test backend`; kiểm tra tên Dockerfile test thực tế trong repo trước.)

---

### Task 1: Model + migration

**Files:**
- Modify: `backend/apps/warranty/models.py` (class `WarrantyClaim`, dòng ~10-37)
- Create: `backend/apps/warranty/migrations/0012_claim_activation_warranty_months.py`
- Test: `backend/apps/warranty/tests/test_models.py`

**Interfaces:**
- Produces: `WarrantyClaim.activation_date: date | None`, `WarrantyClaim.warranty_months: int` (default 24),
  `WarrantyClaim.expiry_date -> date | None` (property), `WarrantyClaim.warranty_days_left -> int | None` (property).

- [ ] **Step 1: Viết test thất bại** — thêm vào cuối `backend/apps/warranty/tests/test_models.py`:

```python
from datetime import date, timedelta
from unittest import mock


@pytest.mark.django_db
def test_claim_warranty_defaults(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    assert claim.activation_date is None
    assert claim.warranty_months == 24
    assert claim.expiry_date is None
    assert claim.warranty_days_left is None


@pytest.mark.django_db
def test_expiry_date_is_activation_plus_months(order_factory):
    claim = WarrantyClaim.objects.create(
        order=order_factory(), activation_date=date(2026, 1, 15)
    )
    assert claim.expiry_date == date(2028, 1, 15)

    claim.warranty_months = 12
    assert claim.expiry_date == date(2027, 1, 15)


@pytest.mark.django_db
def test_expiry_date_handles_leap_day(order_factory):
    claim = WarrantyClaim.objects.create(
        order=order_factory(), activation_date=date(2024, 2, 29), warranty_months=12
    )
    assert claim.expiry_date == date(2025, 2, 28)


@pytest.mark.django_db
def test_warranty_days_left_counts_from_today(order_factory):
    today = date(2026, 7, 11)
    claim = WarrantyClaim.objects.create(
        order=order_factory(), activation_date=date(2026, 7, 1), warranty_months=24
    )
    with mock.patch("apps.warranty.models.timezone.localdate", return_value=today):
        assert claim.warranty_days_left == (date(2028, 7, 1) - today).days


@pytest.mark.django_db
def test_warranty_days_left_negative_when_expired(order_factory):
    today = date(2026, 7, 11)
    claim = WarrantyClaim.objects.create(
        order=order_factory(), activation_date=date(2020, 1, 1), warranty_months=24
    )
    with mock.patch("apps.warranty.models.timezone.localdate", return_value=today):
        assert claim.warranty_days_left < 0
```

- [ ] **Step 2: Chạy test cho chắc là FAIL**

Run: `docker run --rm --network desi-order_default -e POSTGRES_DB=desi_wexp -e REDIS_PASSWORD=testpassword desi-order-web-test pytest apps/warranty/tests/test_models.py -q`
Expected: FAIL — `TypeError: WarrantyClaim() got unexpected keyword arguments: 'activation_date'`

- [ ] **Step 3: Thêm field + property vào `WarrantyClaim`**

Đầu file `models.py` thêm import:

```python
from dateutil.relativedelta import relativedelta
from django.utils import timezone
```

Trong class `WarrantyClaim`, ngay sau `customer_report_date`:

```python
    activation_date = models.DateField(null=True, blank=True)
    warranty_months = models.PositiveSmallIntegerField(default=24)
```

Sau `def __str__`:

```python
    @property
    def expiry_date(self):
        """Ngày hết hạn bảo hành = ngày kích hoạt + số tháng bảo hành (None nếu chưa kích hoạt)."""
        if not self.activation_date:
            return None
        return self.activation_date + relativedelta(months=self.warranty_months)

    @property
    def warranty_days_left(self):
        """Số ngày bảo hành còn lại tính tới hôm nay (âm = đã quá hạn, None nếu chưa kích hoạt)."""
        expiry = self.expiry_date
        if expiry is None:
            return None
        return (expiry - timezone.localdate()).days
```

- [ ] **Step 4: Sinh migration**

Run: `docker compose -f docker-compose.local.yml run --rm web python manage.py makemigrations warranty`
Đổi tên file sinh ra thành `0012_claim_activation_warranty_months.py` nếu tên khác. Nội dung phải là 2 `AddField`, không có gì khác.

- [ ] **Step 5: Chạy test — PASS**

Run: `docker run --rm --network desi-order_default -e POSTGRES_DB=desi_wexp -e REDIS_PASSWORD=testpassword desi-order-web-test pytest apps/warranty -q`
Expected: PASS toàn bộ `apps/warranty` (không được vỡ test cũ).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/warranty/models.py backend/apps/warranty/migrations/0012_claim_activation_warranty_months.py backend/apps/warranty/tests/test_models.py
git commit -m "feat(warranty): add activation_date and warranty_months to claim"
```

---

### Task 2: API — serializer + service

**Files:**
- Modify: `backend/apps/warranty/serializers.py` (`WarrantyClaimSerializer` ~123-150, `WarrantyClaimWriteSerializer` ~152-170)
- Modify: `backend/apps/warranty/claim_service.py:22` (`_CLAIM_EDITABLE_FIELDS`)
- Test: `backend/apps/warranty/tests/test_warranty_claim_api.py`

**Interfaces:**
- Consumes: `WarrantyClaim.expiry_date`, `WarrantyClaim.warranty_days_left` (Task 1).
- Produces: response phiếu có thêm `activation_date` (str|null), `warranty_months` (int), `expiry_date` (str|null),
  `warranty_days_left` (int|null); PATCH/POST nhận `activation_date`, `warranty_months`.

- [ ] **Step 1: Viết test thất bại** — thêm vào cuối `test_warranty_claim_api.py` (dùng `auth_client`, `order_factory` như các test có sẵn):

```python
@pytest.mark.django_db
def test_patch_activation_date_returns_expiry_and_days_left(auth_client, order_factory):
    order = order_factory()
    created = auth_client.post("/api/warranty/claims", {"order_id": order.id}, format="json")
    claim_id = created.json()["id"]

    r = auth_client.patch(
        f"/api/warranty/claims/{claim_id}",
        {"activation_date": "2026-01-15"},
        format="json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["activation_date"] == "2026-01-15"
    assert body["warranty_months"] == 24
    assert body["expiry_date"] == "2028-01-15"
    assert isinstance(body["warranty_days_left"], int)


@pytest.mark.django_db
def test_patch_warranty_months_changes_expiry(auth_client, order_factory):
    order = order_factory()
    claim_id = auth_client.post(
        "/api/warranty/claims", {"order_id": order.id}, format="json"
    ).json()["id"]

    r = auth_client.patch(
        f"/api/warranty/claims/{claim_id}",
        {"activation_date": "2026-01-15", "warranty_months": 12},
        format="json",
    )
    assert r.status_code == 200
    assert r.json()["expiry_date"] == "2027-01-15"


@pytest.mark.django_db
def test_patch_invalid_warranty_months_rejected(auth_client, order_factory):
    order = order_factory()
    claim_id = auth_client.post(
        "/api/warranty/claims", {"order_id": order.id}, format="json"
    ).json()["id"]

    r = auth_client.patch(
        f"/api/warranty/claims/{claim_id}", {"warranty_months": 0}, format="json"
    )
    assert r.status_code == 400


@pytest.mark.django_db
def test_clear_activation_date(auth_client, order_factory):
    order = order_factory()
    claim_id = auth_client.post(
        "/api/warranty/claims", {"order_id": order.id}, format="json"
    ).json()["id"]
    auth_client.patch(
        f"/api/warranty/claims/{claim_id}", {"activation_date": "2026-01-15"}, format="json"
    )

    r = auth_client.patch(
        f"/api/warranty/claims/{claim_id}", {"activation_date": None}, format="json"
    )
    assert r.status_code == 200
    assert r.json()["activation_date"] is None
    assert r.json()["expiry_date"] is None
    assert r.json()["warranty_days_left"] is None
```

- [ ] **Step 2: Chạy test cho chắc là FAIL**

Run: `docker run --rm --network desi-order_default -e POSTGRES_DB=desi_wexp -e REDIS_PASSWORD=testpassword desi-order-web-test pytest apps/warranty/tests/test_warranty_claim_api.py -q`
Expected: FAIL — `KeyError: 'expiry_date'`

- [ ] **Step 3: Sửa serializer**

Trong `WarrantyClaimSerializer`, thêm 2 field dẫn xuất (khai báo tường minh để OpenAPI sinh đúng kiểu) và mở rộng `fields`:

```python
class WarrantyClaimSerializer(serializers.ModelSerializer):
    order = WarrantyClaimOrderSerializer(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    created_by_name = serializers.SerializerMethodField()
    journeys = WarrantyJourneySerializer(many=True, read_only=True)
    expiry_date = serializers.DateField(read_only=True, allow_null=True)
    warranty_days_left = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = WarrantyClaim
        fields = (
            "id",
            "order",
            "customer_report_date",
            "activation_date",
            "warranty_months",
            "expiry_date",
            "warranty_days_left",
            "status",
            "status_label",
            "completed_at",
            "journeys",
            "created_by_name",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields
```

Trong `WarrantyClaimWriteSerializer`:

```python
    class Meta:
        model = WarrantyClaim
        fields = ("order_id", "customer_report_date", "activation_date", "warranty_months", "status")
        extra_kwargs = {
            "customer_report_date": {"required": False},
            "activation_date": {"required": False, "allow_null": True},
            "warranty_months": {"required": False},
            "status": {"required": False},
        }

    def validate_warranty_months(self, value):
        if value < 1 or value > 120:
            raise serializers.ValidationError("Số tháng bảo hành phải từ 1 đến 120.")
        return value
```

(giữ nguyên `validate_status` đang có)

- [ ] **Step 4: Sửa service** — `backend/apps/warranty/claim_service.py:22`:

```python
_CLAIM_EDITABLE_FIELDS = ("customer_report_date", "activation_date", "warranty_months", "status")
```

- [ ] **Step 5: Chạy test — PASS**

Run: `docker run --rm --network desi-order_default -e POSTGRES_DB=desi_wexp -e REDIS_PASSWORD=testpassword desi-order-web-test pytest apps/warranty -q`
Expected: PASS toàn bộ.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/warranty/serializers.py backend/apps/warranty/claim_service.py backend/apps/warranty/tests/test_warranty_claim_api.py
git commit -m "feat(warranty): expose activation/expiry/days-left in claim API"
```

---

### Task 3: Xuất Excel 3 cột mới

**Files:**
- Modify: `backend/apps/warranty/claim_excel.py` (`_HEADERS` dòng 14-24, `_claim_row` dòng 49-61)
- Test: `backend/apps/warranty/tests/test_claim_bulk_export.py` (sửa assert header có sẵn ~dòng 45)

**Interfaces:**
- Consumes: `WarrantyClaim.expiry_date`, `WarrantyClaim.warranty_days_left` (Task 1).

- [ ] **Step 1: Sửa test header có sẵn cho FAIL** — trong `test_export_returns_xlsx_with_expected_columns`, đổi assert header thành:

```python
    assert header == [
        "Mã đơn gốc",
        "Khách",
        "SĐT",
        "Địa chỉ",
        "Mã vận đơn",
        "Trạng thái phiếu",
        "Số hành trình",
        "Ngày khách báo lỗi",
        "Ngày kích hoạt",
        "Ngày hết hạn",
        "Số ngày bảo hành",
        "Ngày tạo",
    ]
```

và thêm test mới ở cuối file:

```python
@pytest.mark.django_db
def test_export_includes_activation_columns(auth_client, order_factory):
    order = order_factory(shipping_code="VD-ACT-1", invoice_code="HD-ACT-1")
    claim_id = auth_client.post(
        "/api/warranty/claims", {"order_id": order.id}, format="json"
    ).json()["id"]
    auth_client.patch(
        f"/api/warranty/claims/{claim_id}", {"activation_date": "2026-01-15"}, format="json"
    )

    r = auth_client.get("/api/warranty/claims/export")
    ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
    row = [cell.value for cell in ws[2]]
    assert row[8] == "15/01/2026"   # Ngày kích hoạt
    assert row[9] == "15/01/2028"   # Ngày hết hạn
    assert isinstance(row[10], int)  # Số ngày bảo hành còn lại
```

- [ ] **Step 2: Chạy test cho chắc là FAIL**

Run: `docker run --rm --network desi-order_default -e POSTGRES_DB=desi_wexp -e REDIS_PASSWORD=testpassword desi-order-web-test pytest apps/warranty/tests/test_claim_bulk_export.py -q`
Expected: FAIL — header thiếu 3 cột.

- [ ] **Step 3: Sửa `claim_excel.py`**

`_HEADERS` chèn 3 mục sau `"Ngày khách báo lỗi"`:

```python
_HEADERS = [
    "Mã đơn gốc",
    "Khách",
    "SĐT",
    "Địa chỉ",
    "Mã vận đơn",
    "Trạng thái phiếu",
    "Số hành trình",
    "Ngày khách báo lỗi",
    "Ngày kích hoạt",
    "Ngày hết hạn",
    "Số ngày bảo hành",
    "Ngày tạo",
]
```

`_claim_row` chèn 3 giá trị tương ứng, ngay sau `_fmt_date(claim.customer_report_date)`:

```python
        _fmt_date(claim.activation_date),
        _fmt_date(claim.expiry_date),
        claim.warranty_days_left if claim.warranty_days_left is not None else "",
```

- [ ] **Step 4: Chạy test — PASS**

Run: `docker run --rm --network desi-order_default -e POSTGRES_DB=desi_wexp -e REDIS_PASSWORD=testpassword desi-order-web-test pytest apps/warranty -q`
Expected: PASS toàn bộ.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/warranty/claim_excel.py backend/apps/warranty/tests/test_claim_bulk_export.py
git commit -m "feat(warranty): add activation columns to claim excel export"
```

---

### Task 4: Frontend — 3 cột mới trên bảng + trang chi tiết

**Files:**
- Regenerate: `frontend/src/api/schema.d.ts` (qua `npm run gen:api`)
- Modify: `frontend/src/features/warranty-claims/warranty-claim-columns.ts`
- Modify: `frontend/src/features/warranty-claims/warranty-claims-storage.ts` (3 hằng số key, dòng 7-9)
- Modify: `frontend/src/features/warranty-claims/warranty-claim-types.ts` (`WarrantyClaimPayload`, dòng 40-44)
- Modify: `frontend/src/features/warranty-claims/components/WarrantyClaimsTable.tsx` (`renderCell`, dòng 63-101)
- Modify: `frontend/src/pages/WarrantyClaimDetailPage.tsx` (khối thông tin, dòng 106-151)
- Test: `frontend/src/features/warranty-claims/warranty-claim-columns.test.ts` (tạo mới)

**Interfaces:**
- Consumes: các field API từ Task 2 (`activation_date`, `warranty_months`, `expiry_date`, `warranty_days_left`).
- Produces: `WarrantyColumnKey` thêm `"activation_date" | "expiry_date" | "warranty_days_left"`; `WarrantyColumnKind` thêm `"date"`.

- [ ] **Step 1: Regen typed API contract**

Backend phải đang chạy (`docker compose -f docker-compose.local.yml up -d web`), sau đó:
Run: `cd frontend && npm run gen:api`
Kiểm tra `git diff --stat src/api/schema.d.ts` có thay đổi ở `WarrantyClaim`.

- [ ] **Step 2: Viết test thất bại** — tạo `frontend/src/features/warranty-claims/warranty-claim-columns.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { columnDisplayValue } from "./warranty-claim-columns";
import type { WarrantyClaim } from "./warranty-claim-types";

const base = {
  id: 1,
  status: 1,
  status_label: "Đang xử lý",
  journeys: [],
  order: {},
} as unknown as WarrantyClaim;

describe("columnDisplayValue — cột bảo hành", () => {
  it("chưa kích hoạt → gạch ngang", () => {
    const row = { ...base, activation_date: null, expiry_date: null, warranty_days_left: null };
    expect(columnDisplayValue(row, "activation_date")).toBe("—");
    expect(columnDisplayValue(row, "expiry_date")).toBe("—");
    expect(columnDisplayValue(row, "warranty_days_left")).toBe("—");
  });

  it("còn hạn → số ngày còn lại", () => {
    const row = { ...base, expiry_date: "2028-01-15", warranty_days_left: 540 };
    expect(columnDisplayValue(row, "warranty_days_left")).toBe("540 ngày");
    expect(columnDisplayValue(row, "expiry_date")).toBe("15/1/2028");
  });

  it("hết hạn → Hết hạn", () => {
    const row = { ...base, expiry_date: "2020-01-15", warranty_days_left: -12 };
    expect(columnDisplayValue(row, "warranty_days_left")).toBe("Hết hạn");
  });
});
```

- [ ] **Step 3: Chạy test cho chắc là FAIL**

Run: `cd frontend && npx vitest run src/features/warranty-claims/warranty-claim-columns.test.ts`
Expected: FAIL — TS lỗi key `"activation_date"` không thuộc `WarrantyColumnKey`.

- [ ] **Step 4: Sửa `warranty-claim-columns.ts`**

```ts
export type WarrantyColumnKind = "readonly" | "status" | "date";

export type WarrantyColumnKey =
  | "customer"
  | "phone"
  | "address"
  | "product"
  | "sku"
  | "amount"
  | "shipping_code"
  | "invoice_code"
  | "status"
  | "journeys_count"
  | "customer_report_date"
  | "activation_date"
  | "expiry_date"
  | "warranty_days_left"
  | "created_by_name"
  | "created_at";
```

`WARRANTY_COLUMN_OPTIONS` thêm sau mục `customer_report_date`:

```ts
  { key: "activation_date", label: "Ngày kích hoạt", kind: "date" },
  { key: "expiry_date", label: "Ngày hết hạn", kind: "readonly" },
  { key: "warranty_days_left", label: "Số ngày bảo hành", kind: "readonly" },
```

`DEFAULT_WARRANTY_COLUMNS`:

```ts
export const DEFAULT_WARRANTY_COLUMNS: WarrantyColumnKey[] = [
  "customer",
  "phone",
  "shipping_code",
  "status",
  "journeys_count",
  "activation_date",
  "expiry_date",
  "warranty_days_left",
  "created_at",
];
```

Trong `columnDisplayValue`, thêm trước nhánh `created_at` (và export helper để chỗ khác dùng lại):

```ts
/** Hiển thị số ngày bảo hành còn lại: null → "—", <= 0 → "Hết hạn", còn lại → "N ngày". */
export function formatDaysLeft(days: number | null | undefined): string {
  if (days == null) return "—";
  return days <= 0 ? "Hết hạn" : `${days} ngày`;
}
```

và trong thân hàm:

```ts
  if (key === "activation_date") return fmtDateOnly(row.activation_date);
  if (key === "expiry_date") return fmtDateOnly(row.expiry_date);
  if (key === "warranty_days_left") return formatDaysLeft(row.warranty_days_left);
```

- [ ] **Step 5: Reset cấu hình cột** — `warranty-claims-storage.ts` dòng 7-9:

```ts
const VISIBLE_KEY = "desi_warranty_visible_columns_v2";
const LOCKED_KEY = "desi_warranty_locked_columns_v2";
const ORDER_KEY = "desi_warranty_column_order_v2";
```

- [ ] **Step 6: Payload type** — `warranty-claim-types.ts`:

```ts
export interface WarrantyClaimPayload {
  order_id?: number;
  customer_report_date?: string | null;
  activation_date?: string | null;
  warranty_months?: number;
  status?: WarrantyClaimStatus;
}
```

- [ ] **Step 7: Ô ngày sửa inline trên bảng** — `WarrantyClaimsTable.tsx`

Thêm import `Input` từ `@/components/ui/input`, rồi trong `renderCell`, trước nhánh readonly:

```tsx
    if (kind === "date") {
      return (
        <Input
          type="date"
          key={row.activation_date ?? ""}
          defaultValue={row.activation_date ?? ""}
          className="h-7 w-[140px]"
          onClick={stop}
          onBlur={(e) => {
            const value = e.target.value || null;
            if ((row.activation_date ?? null) === value) return;
            onPatch(row.id, { activation_date: value });
          }}
        />
      );
    }
```

Ô "Số ngày bảo hành" tô đỏ khi hết hạn — trong nhánh readonly:

```tsx
    const text = String(columnDisplayValue(row, key) ?? "");
    const expired = key === "warranty_days_left" && text === "Hết hạn";
    return (
      <span
        className={`block max-w-[240px] truncate ${expired ? "font-medium text-destructive" : "text-muted-foreground"}`}
        title={text}
      >
        {text}
      </span>
    );
```

- [ ] **Step 8: Trang chi tiết** — `WarrantyClaimDetailPage.tsx`

Thêm handler cạnh `handleReportDateChange`:

```tsx
  const handleActivationDateChange = (value: string) => {
    if ((claim.activation_date ?? "") === value) return;
    updateMutation.mutate(
      { id, payload: { activation_date: value || null } },
      { onError: (err) => toast.error(err instanceof Error ? err.message : "Thao tác thất bại.") },
    );
  };

  const handleWarrantyMonthsChange = (value: string) => {
    const months = Number(value);
    if (!Number.isInteger(months) || months < 1 || months > 120) {
      toast.error("Số tháng bảo hành phải từ 1 đến 120.");
      return;
    }
    if (months === claim.warranty_months) return;
    updateMutation.mutate(
      { id, payload: { warranty_months: months } },
      { onError: (err) => toast.error(err instanceof Error ? err.message : "Thao tác thất bại.") },
    );
  };
```

Trong khối thông tin (grid), thêm 4 mục sau ô "Ngày báo lỗi":

```tsx
          <div className="flex items-center gap-2">
            <span className="shrink-0 text-muted-foreground">Ngày kích hoạt:</span>
            <Input
              type="date"
              key={claim.activation_date ?? ""}
              defaultValue={claim.activation_date ?? ""}
              onBlur={(e) => handleActivationDateChange(e.target.value)}
              className="h-7 w-[150px]"
            />
          </div>
          <div className="flex items-center gap-2">
            <span className="shrink-0 text-muted-foreground">Số tháng bảo hành:</span>
            <Input
              type="number"
              min={1}
              max={120}
              key={claim.warranty_months}
              defaultValue={claim.warranty_months}
              onBlur={(e) => handleWarrantyMonthsChange(e.target.value)}
              className="h-7 w-[80px]"
            />
          </div>
          <div className="truncate">
            <span className="text-muted-foreground">Ngày hết hạn: </span>
            {claim.expiry_date ? new Date(claim.expiry_date).toLocaleDateString("vi-VN") : "—"}
          </div>
          <div className="truncate">
            <span className="text-muted-foreground">Số ngày bảo hành: </span>
            <span
              className={
                claim.warranty_days_left != null && claim.warranty_days_left <= 0
                  ? "font-medium text-destructive"
                  : ""
              }
            >
              {formatDaysLeft(claim.warranty_days_left)}
            </span>
          </div>
```

Import `formatDaysLeft` từ `@/features/warranty-claims/warranty-claim-columns`.

- [ ] **Step 9: Chạy test + lint + typecheck**

Run: `cd frontend && npx vitest run src/features/warranty-claims && npx tsc --noEmit && npx eslint src/features/warranty-claims src/pages/WarrantyClaimDetailPage.tsx`
Expected: test PASS, không lỗi type, không lỗi lint.

- [ ] **Step 10: Commit**

```bash
git add frontend/src
git commit -m "feat(warranty): show activation, expiry and remaining warranty days columns"
```

---

### Task 5: Verify end-to-end + MR

- [ ] **Step 1: Chạy toàn bộ test backend app warranty + FE suite warranty**

Run: `docker run --rm --network desi-order_default -e POSTGRES_DB=desi_wexp2 -e REDIS_PASSWORD=testpassword desi-order-web-test pytest apps/warranty -q`
Run: `cd frontend && npx vitest run src/features/warranty-claims`
Expected: PASS cả hai.

- [ ] **Step 2: Kiểm tra thủ công trên app local**

Mở `/sales/warranty`: 3 cột mới hiện sẵn; chọn ngày kích hoạt trên 1 dòng → cột Ngày hết hạn = +2 năm, Số ngày bảo hành ra số ngày còn lại. Mở trang chi tiết phiếu đó: 4 mục mới hiển thị đúng. Bấm Xuất Excel → file có 3 cột mới.

- [ ] **Step 3: Rebase master mới nhất, xử lý conflict nếu có**

```bash
git fetch origin && git rebase origin/master
```

- [ ] **Step 4: Push + tạo MR**

```bash
git push -u origin feat/warranty-activation-expiry
```
Gửi link MR cho user.

## Self-Review

- Spec §4.1 → Task 1; §4.2/4.3 → Task 2; §4.4 → Task 3; §5.1-5.5 → Task 4; §6 test → rải trong Task 1-4; §7 (không làm) → không task nào chạm.
- Không có placeholder; mọi step có code/lệnh cụ thể.
- Tên field nhất quán giữa các task: `activation_date`, `warranty_months`, `expiry_date`, `warranty_days_left`; helper FE `formatDaysLeft` định nghĩa ở Task 4 Step 4, dùng lại ở Step 8.
