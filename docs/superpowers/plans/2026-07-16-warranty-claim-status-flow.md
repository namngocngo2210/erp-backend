# Warranty Claim Status Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thay bộ 3 trạng thái của `WarrantyClaim.status` bằng 11 trạng thái theo flow CSKH (Đợi khách XN → ... → Hoàn thành / Thất lạc / Hủy), nhân viên chọn tay tự do.

**Architecture:** Đổi `IntegerChoices` ở model + 1 migration (remap data cũ 2→9, 3→11 + AlterField); `update_claim` stamp `completed_at` khi status sang 9; gỡ guard chặn PATCH status=COMPLETED; regen OpenAPI schema; FE mở rộng options/badge, cho chọn đủ 11 trạng thái inline.

**Tech Stack:** Django + DRF + drf-spectacular (BE), React + TypeScript + openapi-typescript (FE), pytest + vitest.

**Spec:** `docs/superpowers/specs/2026-07-16-warranty-claim-status-flow-design.md`

## Global Constraints

- Worktree: `c:\Users\MYPC\Desktop\workspace\desi-order\.claude\worktrees\warranty-claim-statuses`, branch `feat/warranty-claim-status-flow`. Mọi path dưới đây tương đối từ worktree root.
- KHÔNG đụng `WarrantyJourney.status` / `return_status` / `JourneyStatus` — chỉ tầng phiếu.
- Không ràng buộc thứ tự chuyển trạng thái; không auto-suggest; không log lịch sử (YAGNI).
- Bộ mã chính xác (mã → code → nhãn):
  | 1 | `WAITING_CUSTOMER_CONFIRM` | Đợi khách XN |
  | 2 | `WAITING_ORDER_CREATE` | Chờ tạo đơn |
  | 3 | `WAITING_SHIPMENT` | Chờ vận chuyển |
  | 4 | `REPAIRING` | Đang sửa chữa |
  | 5 | `WAITING_RESOLUTION` | Chờ chốt phương án |
  | 6 | `WAITING_RESEND_ORDER` | Chờ tạo đơn gửi lại |
  | 7 | `RESENDING` | Đang gửi lại khách |
  | 8 | `WAITING_FEEDBACK` | Xin feedback |
  | 9 | `COMPLETED` | Hoàn thành |
  | 10 | `LOST` | Thất lạc |
  | 11 | `CANCELLED` | Hủy |
- Chạy test BE (PowerShell, từ bất kỳ đâu — image test cũ stale, dùng `desi-order-web:wp7`):
  ```powershell
  docker run --rm --network desi-order_default `
    -e POSTGRES_DB=desi_wcsflow -e POSTGRES_HOST=desi-order-postgres-1 `
    -e REDIS_HOST=desi-order-redis-1 -e REDIS_PASSWORD=testpassword `
    -v "C:\Users\MYPC\Desktop\workspace\desi-order\.claude\worktrees\warranty-claim-statuses\backend:/app" `
    -w /app desi-order-web:wp7 pytest apps/warranty -q -p no:cacheprovider --create-db
  ```
  Lần đầu sau khi thêm migration PHẢI `--create-db`. Nếu lỗi NOAUTH redis → dựng redis throwaway theo memory `running-backend-tests`.
- FE test/typecheck chạy trong `frontend/` của worktree; worktree mới chưa có `node_modules` → `npm ci` trước.
- Commit message tiếng Anh, ngắn, KHÔNG kèm Co-Authored-By.

---

### Task 1: Backend — enum 11 trạng thái + migration remap

**Files:**
- Modify: `backend/apps/warranty/models.py:6-9` (class `WarrantyClaimStatus`)
- Create: `backend/apps/warranty/migrations/0014_claim_status_flow.py`
- Test: `backend/apps/warranty/tests/test_models.py`

**Interfaces:**
- Produces: `WarrantyClaimStatus` với 11 member đúng bảng Global Constraints; default field `status` vẫn là member đầu (`WAITING_CUSTOMER_CONFIRM = 1`). Task 2 dùng `WarrantyClaimStatus.COMPLETED == 9`.

- [ ] **Step 1: Viết test fail cho enum mới**

Thêm vào cuối `backend/apps/warranty/tests/test_models.py`:

```python
def test_claim_status_flow_choices():
    """Bộ 11 trạng thái phiếu theo flow CSKH — mã đánh theo thứ tự flow."""
    expected = {
        1: "Đợi khách XN",
        2: "Chờ tạo đơn",
        3: "Chờ vận chuyển",
        4: "Đang sửa chữa",
        5: "Chờ chốt phương án",
        6: "Chờ tạo đơn gửi lại",
        7: "Đang gửi lại khách",
        8: "Xin feedback",
        9: "Hoàn thành",
        10: "Thất lạc",
        11: "Hủy",
    }
    assert dict(WarrantyClaimStatus.choices) == expected
    assert WarrantyClaimStatus.COMPLETED == 9
    assert WarrantyClaimStatus.CANCELLED == 11


@pytest.mark.django_db
def test_claim_default_status_is_waiting_customer_confirm(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    assert claim.status == WarrantyClaimStatus.WAITING_CUSTOMER_CONFIRM == 1
```

Bổ sung import ở đầu file nếu chưa có: `import pytest`, `from apps.warranty.models import WarrantyClaim, WarrantyClaimStatus` (xem import sẵn có của file, đừng trùng lặp).

- [ ] **Step 2: Chạy test xác nhận fail**

Run (lệnh docker ở Global Constraints, thay path test): `pytest apps/warranty/tests/test_models.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: WAITING_CUSTOMER_CONFIRM` / choices mismatch.

- [ ] **Step 3: Sửa enum trong models.py**

Thay toàn bộ class `WarrantyClaimStatus` (models.py dòng 6-9):

```python
class WarrantyClaimStatus(models.IntegerChoices):
    """Trạng thái phiếu BH theo flow CSKH — mã đánh theo thứ tự flow, NV chọn tay tự do."""

    WAITING_CUSTOMER_CONFIRM = 1, "Đợi khách XN"
    WAITING_ORDER_CREATE = 2, "Chờ tạo đơn"
    WAITING_SHIPMENT = 3, "Chờ vận chuyển"
    REPAIRING = 4, "Đang sửa chữa"
    WAITING_RESOLUTION = 5, "Chờ chốt phương án"
    WAITING_RESEND_ORDER = 6, "Chờ tạo đơn gửi lại"
    RESENDING = 7, "Đang gửi lại khách"
    WAITING_FEEDBACK = 8, "Xin feedback"
    COMPLETED = 9, "Hoàn thành"
    LOST = 10, "Thất lạc"
    CANCELLED = 11, "Hủy"
```

Field `status` của `WarrantyClaim` giữ nguyên `default=WarrantyClaimStatus.PROCESSING`? — KHÔNG: `PROCESSING` không còn tồn tại. Sửa dòng 21-23 thành:

```python
    status = models.PositiveSmallIntegerField(
        choices=WarrantyClaimStatus.choices,
        default=WarrantyClaimStatus.WAITING_CUSTOMER_CONFIRM,
    )
```

Grep toàn backend tìm reference cũ còn sót: `PROCESSING` trong `backend/apps/warranty/` (test cũ nào dùng `WarrantyClaimStatus.PROCESSING` phải đổi sang `WAITING_CUSTOMER_CONFIRM`).

- [ ] **Step 4: Tạo migration**

Create `backend/apps/warranty/migrations/0014_claim_status_flow.py`:

```python
from django.db import migrations, models


def _remap_forward(apps, schema_editor):
    """Remap mã cũ → mã mới theo flow: 2 (Hoàn thành)→9, 3 (Hủy)→11.

    Chạy 3→11 TRƯỚC 2→9 (tường minh, dù 9/11 không phải mã nguồn nên không va).
    1 (Đang xử lý) giữ số 1, chỉ đổi nghĩa nhãn thành "Đợi khách XN".
    """
    WarrantyClaim = apps.get_model("warranty", "WarrantyClaim")
    WarrantyClaim.objects.filter(status=3).update(status=11)
    WarrantyClaim.objects.filter(status=2).update(status=9)


def _remap_backward(apps, schema_editor):
    WarrantyClaim = apps.get_model("warranty", "WarrantyClaim")
    WarrantyClaim.objects.filter(status=9).update(status=2)
    WarrantyClaim.objects.filter(status=11).update(status=3)
    # Các trạng thái mới (4-8, 10) không có ánh xạ cũ → về "Đang xử lý".
    WarrantyClaim.objects.filter(status__gt=3).update(status=1)


class Migration(migrations.Migration):
    dependencies = [
        ("warranty", "0013_merge_20260711"),
    ]

    operations = [
        migrations.RunPython(_remap_forward, _remap_backward),
        migrations.AlterField(
            model_name="warrantyclaim",
            name="status",
            field=models.PositiveSmallIntegerField(
                choices=[
                    (1, "Đợi khách XN"),
                    (2, "Chờ tạo đơn"),
                    (3, "Chờ vận chuyển"),
                    (4, "Đang sửa chữa"),
                    (5, "Chờ chốt phương án"),
                    (6, "Chờ tạo đơn gửi lại"),
                    (7, "Đang gửi lại khách"),
                    (8, "Xin feedback"),
                    (9, "Hoàn thành"),
                    (10, "Thất lạc"),
                    (11, "Hủy"),
                ],
                default=1,
            ),
        ),
    ]
```

Sau đó verify không còn migration nào thiếu: chạy `python manage.py makemigrations --check --dry-run` trong container (expected: "No changes detected"). Nếu Django đòi migration khác tên/format thì dùng `makemigrations warranty` sinh AlterField rồi thêm RunPython vào cùng file, giữ tên `0014_claim_status_flow`.

- [ ] **Step 5: Chạy test xác nhận pass**

Run: `pytest apps/warranty/tests/test_models.py -q -p no:cacheprovider --create-db`
Expected: PASS toàn bộ.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/warranty/models.py backend/apps/warranty/migrations/0014_claim_status_flow.py backend/apps/warranty/tests/test_models.py
git commit -m "feat(warranty): 11-step claim status flow enum + remap migration"
```

---

### Task 2: Backend — stamp `completed_at` khi PATCH sang Hoàn thành + gỡ guard serializer

**Files:**
- Modify: `backend/apps/warranty/claim_service.py:76-92` (`update_claim`)
- Modify: `backend/apps/warranty/serializers.py:184-191` (`WarrantyClaimWriteSerializer.validate_status`)
- Test: `backend/apps/warranty/tests/test_services.py`, `backend/apps/warranty/tests/test_warranty_claim_api.py`

**Interfaces:**
- Consumes: `WarrantyClaimStatus.COMPLETED == 9` (Task 1).
- Produces: `update_claim(claim, data)` — khi `data["status"] == 9` và `completed_at` đang None → stamp now; rời khỏi 9 KHÔNG xóa `completed_at`. PATCH `/api/warranty/claims/<id>` với `{"status": 9}` giờ trả 200 (hết 400). Endpoint `/complete` giữ nguyên.

- [ ] **Step 1: Viết test fail cho service**

Thêm vào cuối `backend/apps/warranty/tests/test_services.py`:

```python
@pytest.mark.django_db
def test_update_claim_to_completed_stamps_completed_at(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    WarrantyClaimService.update_claim(claim, {"status": int(WarrantyClaimStatus.COMPLETED)})
    claim.refresh_from_db()
    assert claim.status == WarrantyClaimStatus.COMPLETED
    assert claim.completed_at is not None


@pytest.mark.django_db
def test_update_claim_leaving_completed_keeps_completed_at(order_factory):
    """Rời khỏi Hoàn thành giữ nguyên completed_at (lưu vết lần hoàn thành)."""
    claim = WarrantyClaim.objects.create(order=order_factory())
    WarrantyClaimService.complete(claim)
    stamped = claim.completed_at
    WarrantyClaimService.update_claim(
        claim, {"status": int(WarrantyClaimStatus.WAITING_FEEDBACK)}
    )
    claim.refresh_from_db()
    assert claim.status == WarrantyClaimStatus.WAITING_FEEDBACK
    assert claim.completed_at == stamped


@pytest.mark.django_db
def test_update_claim_completed_again_does_not_restamp(order_factory):
    """Đã có completed_at → set 9 lần nữa không ghi đè timestamp cũ."""
    claim = WarrantyClaim.objects.create(order=order_factory())
    WarrantyClaimService.complete(claim)
    stamped = claim.completed_at
    WarrantyClaimService.update_claim(
        claim, {"status": int(WarrantyClaimStatus.REPAIRING)}
    )
    WarrantyClaimService.update_claim(claim, {"status": int(WarrantyClaimStatus.COMPLETED)})
    claim.refresh_from_db()
    assert claim.completed_at == stamped


@pytest.mark.django_db
def test_update_claim_other_status_does_not_stamp(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    WarrantyClaimService.update_claim(claim, {"status": int(WarrantyClaimStatus.REPAIRING)})
    claim.refresh_from_db()
    assert claim.completed_at is None
```

- [ ] **Step 2: Chạy test xác nhận fail**

Run: `pytest apps/warranty/tests/test_services.py -q -p no:cacheprovider`
Expected: FAIL — `test_update_claim_to_completed_stamps_completed_at` (completed_at is None).

- [ ] **Step 3: Sửa `update_claim` trong claim_service.py**

Thay method `update_claim` (dòng 76-85):

```python
    @staticmethod
    def update_claim(claim: WarrantyClaim, data: dict) -> WarrantyClaim:
        changed = []
        for key in _CLAIM_EDITABLE_FIELDS:
            if key in data:
                setattr(claim, key, data[key])
                changed.append(key)
        # NV chọn tay sang Hoàn thành → stamp completed_at như endpoint /complete.
        # Không stamp lại nếu đã có (lưu vết lần hoàn thành đầu tiên).
        if (
            "status" in changed
            and claim.status == WarrantyClaimStatus.COMPLETED
            and claim.completed_at is None
        ):
            claim.completed_at = timezone.now()
            changed.append("completed_at")
        if changed:
            claim.save(update_fields=[*changed, "updated_at"])
        return claim
```

(`timezone` đã import sẵn ở đầu file.)

- [ ] **Step 4: Gỡ guard COMPLETED trong serializers.py**

Thay `validate_status` của `WarrantyClaimWriteSerializer` (dòng 184-191):

```python
    def validate_status(self, value):
        if value not in WarrantyClaimStatus.values:
            raise serializers.ValidationError("Trạng thái không hợp lệ.")
        return value
```

- [ ] **Step 5: Sửa test API cũ đang assert PATCH status=COMPLETED bị 400**

Trong `backend/apps/warranty/tests/test_warranty_claim_api.py` (~dòng 102): test docstring "Hoàn thành phiếu chỉ được set qua /complete, PATCH status=2 trực tiếp phải 400" — viết lại thành hành vi mới:

```python
@pytest.mark.django_db
def test_patch_status_completed_directly_stamps_completed_at(auth_client, order_factory):
    """NV chọn tay sang Hoàn thành qua PATCH → 200 + stamp completed_at (flow mới)."""
    claim = WarrantyClaim.objects.create(order=order_factory())
    r = auth_client.patch(
        f"/api/warranty/claims/{claim.id}",
        {"status": int(WarrantyClaimStatus.COMPLETED)},
        format="json",
    )
    assert r.status_code == 200, r.data
    claim.refresh_from_db()
    assert claim.status == WarrantyClaimStatus.COMPLETED
    assert claim.completed_at is not None
```

Giữ nguyên tên hàm test cũ nếu tiện (đổi body + docstring), hoặc đổi tên như trên — miễn không còn assert 400. Các test khác dùng `int(WarrantyClaimStatus.COMPLETED)` (lọc, complete endpoint) tự đúng theo mã 9, không sửa.

- [ ] **Step 6: Chạy toàn bộ test warranty xác nhận pass**

Run: `pytest apps/warranty -q -p no:cacheprovider`
Expected: PASS toàn bộ (0 fail).

- [ ] **Step 7: Commit**

```bash
git add backend/apps/warranty/claim_service.py backend/apps/warranty/serializers.py backend/apps/warranty/tests/test_services.py backend/apps/warranty/tests/test_warranty_claim_api.py
git commit -m "feat(warranty): stamp completed_at on manual status change, drop COMPLETED patch guard"
```

---

### Task 3: Regen OpenAPI schema (BE schema.yaml + FE schema.d.ts)

**Files:**
- Modify: `backend/schema.yaml` (generated)
- Modify: `frontend/src/api/schema.d.ts` (generated)

**Interfaces:**
- Produces: `components["schemas"]["WarrantyClaim"]["status"]` type = union `1|2|...|11` cho Task 4.

- [ ] **Step 1: Regen schema.yaml trong docker**

```powershell
docker run --rm --network desi-order_default `
  -e POSTGRES_HOST=desi-order-postgres-1 -e REDIS_HOST=desi-order-redis-1 -e REDIS_PASSWORD=testpassword `
  -v "C:\Users\MYPC\Desktop\workspace\desi-order\.claude\worktrees\warranty-claim-statuses\backend:/app" `
  -w /app desi-order-web:wp7 python manage.py spectacular --file schema.yaml
```

Expected: file `backend/schema.yaml` đổi — diff chỉ quanh enum status của WarrantyClaim (kiểm bằng `git diff --stat backend/schema.yaml`; nếu diff lớn bất thường thì xem có phải version spectacular khác → dừng, báo lại).

- [ ] **Step 2: Regen FE types + prettier (gotcha bắt buộc)**

```powershell
cd C:\Users\MYPC\Desktop\workspace\desi-order\.claude\worktrees\warranty-claim-statuses\frontend
npm ci
npm run gen:api
npx prettier --write src/api/schema.d.ts
```

Expected: `git diff --stat frontend/src/api/schema.d.ts` chỉ vài chục dòng quanh warranty status enum — KHÔNG phải ~28k dòng (nếu 28k = quên prettier, chạy lại prettier).

- [ ] **Step 3: Commit**

```bash
git add backend/schema.yaml frontend/src/api/schema.d.ts
git commit -m "chore(api): regen schema for warranty claim status enum"
```

---

### Task 4: Frontend — options, badge, inline select đủ 11 trạng thái

**Files:**
- Modify: `frontend/src/features/warranty-claims/warranty-claim-types.ts:9-16`
- Modify: `frontend/src/features/warranty-claims/warranty-claim-columns.ts:58-62`
- Modify: `frontend/src/features/warranty-claims/components/WarrantyClaimsTable.tsx:24-96`
- Modify: `frontend/src/pages/WarrantyClaimsPage.tsx:231`
- Modify: `frontend/src/pages/WarrantyClaimDetailPage.tsx:48,121`
- Test: `frontend/src/features/warranty-claims/components/WarrantyClaimsTable.test.tsx`

**Interfaces:**
- Consumes: type `WarrantyClaimStatus` = `1|...|11` từ schema.d.ts (Task 3).
- Produces: `WARRANTY_STATUS_OPTIONS` 11 mục; `STATUS_BADGE_CLASS: Record<WarrantyClaimStatus, string>` đủ 11 key (hết cast `as 1 | 2 | 3`).

- [ ] **Step 1: Viết test fail cho inline select trạng thái mới**

Thêm vào `WarrantyClaimsTable.test.tsx` (trong describe hiện có, thêm `"status"` vào orderedColumns của test mới — KHÔNG đổi các test cũ):

```tsx
  it("inline select đủ 11 trạng thái, chọn Hoàn thành PATCH status 9", () => {
    const onPatch = vi.fn().mockResolvedValue(true);
    render(
      <WarrantyClaimsTable
        rows={[row]}
        orderedColumns={["status"]}
        lockedColumns={[]}
        isLoading={false}
        onOpenDetail={vi.fn()}
        onPatch={onPatch}
        selectedIds={new Set()}
        onToggleRow={vi.fn()}
        onToggleAll={vi.fn()}
      />,
    );
    const select = document.querySelector("select") as HTMLSelectElement;
    expect(select.options.length).toBe(11);
    fireEvent.change(select, { target: { value: "9" } });
    expect(onPatch).toHaveBeenCalledWith(7, { status: 9 });
  });
```

- [ ] **Step 2: Chạy test xác nhận fail**

Run: `npx vitest run src/features/warranty-claims/components/WarrantyClaimsTable.test.tsx`
Expected: FAIL — select chỉ có 2 options (INLINE_STATUS_OPTIONS cũ lọc mất Hoàn thành).

- [ ] **Step 3: Cập nhật warranty-claim-types.ts**

Thay dòng 9-16:

```ts
/** 1..11 — derive qua field `status` để không phụ thuộc tên enum sinh tự động. */
export type WarrantyClaimStatus = components["schemas"]["WarrantyClaim"]["status"];

/** Bộ trạng thái phiếu theo flow CSKH — mã đánh theo thứ tự flow. */
export const WARRANTY_STATUS_OPTIONS: { value: WarrantyClaimStatus; label: string }[] = [
  { value: 1, label: "Đợi khách XN" },
  { value: 2, label: "Chờ tạo đơn" },
  { value: 3, label: "Chờ vận chuyển" },
  { value: 4, label: "Đang sửa chữa" },
  { value: 5, label: "Chờ chốt phương án" },
  { value: 6, label: "Chờ tạo đơn gửi lại" },
  { value: 7, label: "Đang gửi lại khách" },
  { value: 8, label: "Xin feedback" },
  { value: 9, label: "Hoàn thành" },
  { value: 10, label: "Thất lạc" },
  { value: 11, label: "Hủy" },
];
```

- [ ] **Step 4: Cập nhật STATUS_BADGE_CLASS trong warranty-claim-columns.ts**

Thay dòng 58-62 (import type `WarrantyClaimStatus` từ warranty-claim-types — file đã import type từ đó):

```ts
/** Badge màu theo nhóm: 1-3 chặng thu hồi (xanh dương), 4-5 xưởng (amber/cam),
 * 6-8 chặng gửi lại (cyan/teal/tím), 9 xanh lá, 10 đỏ, 11 xám. */
export const STATUS_BADGE_CLASS: Record<WarrantyClaimStatus, string> = {
  1: "bg-sky-100 text-sky-700",
  2: "bg-blue-100 text-blue-700",
  3: "bg-indigo-100 text-indigo-700",
  4: "bg-amber-100 text-amber-700",
  5: "bg-orange-100 text-orange-700",
  6: "bg-cyan-100 text-cyan-700",
  7: "bg-teal-100 text-teal-700",
  8: "bg-purple-100 text-purple-700",
  9: "bg-green-100 text-green-700",
  10: "bg-red-100 text-red-700",
  11: "bg-gray-200 text-gray-600",
};
```

Import bổ sung ở dòng 1: `import type { WarrantyClaim, WarrantyClaimStatus } from "@/features/warranty-claims/warranty-claim-types";`

- [ ] **Step 5: WarrantyClaimsTable.tsx — cho chọn đủ 11 trạng thái inline**

- Xóa comment khối dòng 24-29 + `INLINE_STATUS_OPTIONS` (dòng 30).
- Trong `renderCell` kind === "status" (dòng 69-96): xóa nhánh read-only `row.status === 2`, select map thẳng `WARRANTY_STATUS_OPTIONS`, thay comment cũ:

```tsx
    if (kind === "status") {
      // NV chọn tay tự do đủ 11 trạng thái; PATCH sang Hoàn thành (9) được BE
      // stamp completed_at nên không cần chặn như bộ 3 trạng thái cũ.
      return (
        <select
          className="h-7 rounded border border-input bg-background px-1 text-sm"
          value={row.status}
          onClick={stop}
          onChange={(e) =>
            void onPatch(row.id, { status: Number(e.target.value) as WarrantyClaimStatus })
          }
        >
          {WARRANTY_STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      );
    }
```

- Import `STATUS_BADGE_CLASS` không còn dùng trong file này sau khi xóa nhánh read-only → gỡ khỏi import (dòng 12) nếu ESLint báo unused.

- [ ] **Step 6: Gỡ cast cứng ở 2 page**

- `WarrantyClaimsPage.tsx:231`: `STATUS_BADGE_CLASS[row.status as 1 | 2 | 3]` → `STATUS_BADGE_CLASS[row.status]` (nếu TS báo `row.status` là `number` thì cast `as WarrantyClaimStatus` — type đã import sẵn dòng 41).
- `WarrantyClaimDetailPage.tsx:121`: tương tự gỡ `as 1 | 2 | 3`.
- `WarrantyClaimDetailPage.tsx:48`: `const isCompleted = claim.status === 2;` → `claim.status === 9;` (giữ nút Hoàn thành hoạt động như cũ, chỉ đổi mã).

- [ ] **Step 7: Chạy test + typecheck xác nhận pass**

```powershell
npx vitest run src/features/warranty-claims
npm run typecheck
```

Expected: test PASS (cả file columns.test.ts cũ), typecheck 0 lỗi. Lưu ý baseline fail sẵn trên master: `OrderFormDialog` + 2 test mobile-viewport (memory `known-failing-baseline-tests`) — KHÔNG thuộc scope này.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/warranty-claims frontend/src/pages/WarrantyClaimsPage.tsx frontend/src/pages/WarrantyClaimDetailPage.tsx
git commit -m "feat(warranty): 11-step status options, badges, free inline status select"
```

---

### Task 5: Verify tổng + push + MR

**Files:** không sửa code mới (chỉ chạy verify).

- [ ] **Step 1: Full test warranty BE + FE build**

```powershell
# BE (lệnh docker ở Global Constraints)
pytest apps/warranty -q -p no:cacheprovider --create-db
# FE
cd C:\Users\MYPC\Desktop\workspace\desi-order\.claude\worktrees\warranty-claim-statuses\frontend
npx vitest run src/features/warranty-claims
npm run build
```

Expected: BE 0 fail; FE test pass; build OK.

- [ ] **Step 2: Kiểm tra conflict với master mới nhất**

```bash
git -C <worktree> fetch origin master
git -C <worktree> merge origin/master --no-edit
```

Expected: no conflict (resolve nếu có, chạy lại test khi resolve).

- [ ] **Step 3: Push + gửi link MR**

```bash
git push -u origin feat/warranty-claim-status-flow
```

Tạo MR qua link GitLab trả về, title: `feat(warranty): 11-step claim status flow`. Ghi chú deploy trong MR description: migration 0014 remap data (bảng nhỏ, chạy tự động khi deploy).
