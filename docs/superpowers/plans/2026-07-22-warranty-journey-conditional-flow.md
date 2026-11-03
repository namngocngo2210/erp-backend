# Warranty Journey Conditional Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Form hành trình bảo hành chỉ hiện khối đúng lúc theo hình thức gửi + trạng thái vận chuyển, và có thêm hai khối mới: cảm nhận của khách, khiếu nại & đền bù.

**Architecture:** 8 field mới trên `WarrantyJourney` (2 nhóm: feedback, khiếu nại). Quy tắc ẩn/hiện là logic UI thuần, đặt trong một hàm không-React (`journey-visibility.ts`) để test bằng vitest; `JourneyCard` gọi một lần rồi truyền cờ xuống các section.

**Tech Stack:** Django 6 + DRF + pytest (backend); React 19 + TypeScript + vitest + @testing-library/react (frontend).

## Global Constraints

- Spec nguồn: `docs/superpowers/specs/2026-07-22-warranty-journey-conditional-flow-design.md`.
- Nhánh `feat/warranty-journey-conditional-flow`, cắt từ `feat/warranty-best-tracking-realtime`. Migration mới nhất hiện có là `0017_recall_code_index` → migration của plan này là **0018**.
- KHÔNG thêm field cho "Tình trạng nhận thực" — suy từ `status === "returned"`, hiển thị badge chỉ đọc.
- Cờ hiển thị tính ở FRONTEND, không tính ở backend, không thêm field vào serializer cho việc này.
- Khối bị ẩn thì KHÔNG render (không dùng `display:none`); `buildJourneyPatch` vẫn gửi đủ mọi field như hiện tại để dữ liệu đã nhập không bị mất.
- §2 Người nhận, §3 SP & lý do, §4 Vận chuyển & chi phí: LUÔN hiện, không đụng.
- KHÔNG đụng nội dung bên trong §7 (thuộc Phần 2, spec riêng).
- KHÔNG định nghĩa luồng cho `return_recall` / `warranty_return` — hai loại này chỉ bật cờ `claim`.
- Comment code tiếng Việt, khớp phong cách file lân cận. Commit message tiếng Anh, ngắn, KHÔNG kèm `Co-Authored-By`.

## Cách chạy test

**Backend** (máy không có Python native đúng bản, chạy qua Docker), gọi từ gốc repo trong PowerShell:

```
.\.superpowers\run-backend-tests.ps1 apps/warranty
```

Script đã lo `ENCRYPTION_KEY`, `REDIS_HOST`, `--reuse-db`. Đừng tự dựng lệnh docker khác — thiếu `REDIS_HOST` thì test TREO im lặng.

`makemigrations` cũng phải chạy trong container:

```
docker run --rm --network desi-test-net -v "C:\Users\MYPC\Desktop\workspace\desi-order\backend:/app" -w /app -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_order -e POSTGRES_USER=desi_order -e POSTGRES_PASSWORD=desi_order -e DJANGO_SECRET_KEY=test-secret -e "ENCRYPTION_KEY=_o0HqX1zVD_tDJUDcrKnImGYYJrBrHh62VtEsV_h-ao=" -e REDIS_HOST=desi-test-redis desi-backend-test python manage.py makemigrations warranty --name journey_feedback_claim_fields
```

**Frontend** (node native), từ gốc repo trong PowerShell:

```
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
cd frontend
npx vitest run src/features/warranty-claims
npx tsc --noEmit
```

**Git:** husky pre-commit cần `npx` trong PATH — chạy git qua PowerShell sau khi nạp PATH như trên. KHÔNG dùng `--no-verify`.

**Baseline:** `apps/warranty` backend và `src/features/warranty-claims` frontend đều đang xanh trước khi bắt đầu.

---

### Task 1: 8 field mới + 3 enum + migration

**Files:**
- Modify: `backend/apps/warranty/models.py`
- Modify: `backend/apps/warranty/journey_service.py:10-19` (`JOURNEY_EDITABLE_FIELDS`)
- Modify: `backend/apps/warranty/serializers.py` (`WarrantyJourneySerializer.Meta.fields`, `WarrantyJourneyWriteSerializer.Meta.fields`)
- Create: `backend/apps/warranty/migrations/0018_journey_feedback_claim_fields.py`
- Test: `backend/apps/warranty/tests/test_journey_feedback_claim_fields.py`

**Interfaces:**
- Produces: 8 field trên `WarrantyJourney` — `feedback_agree_review`, `feedback_rating`, `feedback_note`, `claim_date`, `claim_note`, `carrier_response`, `compensation_status`, `compensation_amount`. Task 3 khai cùng tên ở `JourneyFormState`.
- Produces: enum `JourneyFeedbackAgree`, `JourneyFeedbackRating`, `JourneyCarrierResponse`, `JourneyCompensationStatus`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/warranty/tests/test_journey_feedback_claim_fields.py`:

```python
import pytest
from rest_framework.test import APIClient

from apps.users.test_helpers import make_user_with_sections
from apps.warranty.journey_service import JOURNEY_EDITABLE_FIELDS, WarrantyJourneyService
from apps.warranty.models import WarrantyClaim

_NEW_FIELDS = (
    "feedback_agree_review",
    "feedback_rating",
    "feedback_note",
    "claim_date",
    "claim_note",
    "carrier_response",
    "compensation_status",
    "compensation_amount",
)


def test_new_fields_are_editable():
    for field in _NEW_FIELDS:
        assert field in JOURNEY_EDITABLE_FIELDS, field


@pytest.mark.django_db
def test_new_fields_default_to_empty(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    journey = WarrantyJourneyService.add_journey(claim)

    assert journey.feedback_agree_review == ""
    assert journey.feedback_rating == ""
    assert journey.feedback_note == ""
    assert journey.claim_date is None
    assert journey.claim_note == ""
    assert journey.carrier_response == ""
    assert journey.compensation_status == ""
    assert str(journey.compensation_amount) == "0.00"


@pytest.mark.django_db
def test_patch_saves_feedback_and_claim(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    journey = WarrantyJourneyService.add_journey(claim)
    client = APIClient()
    client.force_authenticate(make_user_with_sections("wty-feedback", ["warranty"]))

    response = client.patch(
        f"/api/warranty/journeys/{journey.id}",
        {
            "feedback_agree_review": "yes",
            "feedback_rating": "lost_trust",
            "feedback_note": "Khách phàn nàn giao chậm",
            "claim_date": "2026-07-20",
            "claim_note": "Best báo mất kiện",
            "carrier_response": "agreed",
            "compensation_status": "paid",
            "compensation_amount": "350000.00",
        },
        format="json",
    )

    assert response.status_code == 200
    journey.refresh_from_db()
    assert journey.feedback_agree_review == "yes"
    assert journey.feedback_rating == "lost_trust"
    assert journey.feedback_note == "Khách phàn nàn giao chậm"
    assert str(journey.claim_date) == "2026-07-20"
    assert journey.claim_note == "Best báo mất kiện"
    assert journey.carrier_response == "agreed"
    assert journey.compensation_status == "paid"
    assert str(journey.compensation_amount) == "350000.00"
    # Serializer trả lại đủ 8 field để FE đọc.
    for field in _NEW_FIELDS:
        assert field in response.data, field


@pytest.mark.django_db
def test_invalid_enum_value_rejected(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    journey = WarrantyJourneyService.add_journey(claim)
    client = APIClient()
    client.force_authenticate(make_user_with_sections("wty-feedback-2", ["warranty"]))

    response = client.patch(
        f"/api/warranty/journeys/{journey.id}",
        {"feedback_rating": "khong_ton_tai"},
        format="json",
    )

    assert response.status_code == 400
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `.\.superpowers\run-backend-tests.ps1 apps/warranty/tests/test_journey_feedback_claim_fields.py`
Expected: FAIL — `test_new_fields_are_editable` báo `AssertionError: feedback_agree_review`.

- [ ] **Step 3: Thêm 4 enum vào `models.py`**

Chèn ngay sau `class JourneyReturnStatus` (khoảng dòng 129-134):

```python
class JourneyFeedbackAgree(models.TextChoices):
    """Khách có đồng ý để lại review công khai không."""

    YES = "yes", "Có"
    NO = "no", "Không"


class JourneyFeedbackRating(models.TextChoices):
    """Đánh giá của khách về trải nghiệm bảo hành."""

    GOOD = "good", "Tốt"
    AVERAGE = "average", "Trung bình"
    UNSATISFIED = "unsatisfied", "Không hài lòng"
    LOST_TRUST = "lost_trust", "Mất niềm tin"


class JourneyCarrierResponse(models.TextChoices):
    """Phản hồi của đơn vị vận chuyển cho khiếu nại thất lạc."""

    PENDING = "pending", "Chưa phản hồi"
    WORKING_WITH_POST = "working_with_post", "Chờ lv với bưu cục"
    AGREED = "agreed", "Chốt đền bù"
    REJECTED = "rejected", "Không được đền bù"


class JourneyCompensationStatus(models.TextChoices):
    UNPAID = "unpaid", "Chưa đền bù"
    PAID = "paid", "Đã đền bù"
```

- [ ] **Step 4: Thêm 8 field vào `WarrantyJourney`**

Chèn ngay sau khối `return_status = ...` (§7) và TRƯỚC khối `new_tracking_best_status`:

```python
    # Khách nhận hàng — cảm nhận. Hiện khi chặng giao tới khách đã GTC
    # (quy tắc hiển thị nằm ở FE, xem journey-visibility.ts).
    feedback_agree_review = models.CharField(
        max_length=10, choices=JourneyFeedbackAgree.choices, blank=True, default=""
    )
    feedback_rating = models.CharField(
        max_length=20, choices=JourneyFeedbackRating.choices, blank=True, default=""
    )
    feedback_note = models.TextField(blank=True, default="")

    # Khiếu nại & đền bù — hiện khi có mã vận đơn rơi vào nhóm thất lạc.
    claim_date = models.DateField(null=True, blank=True)
    claim_note = models.TextField(blank=True, default="")
    carrier_response = models.CharField(
        max_length=20, choices=JourneyCarrierResponse.choices, blank=True, default=""
    )
    compensation_status = models.CharField(
        max_length=15, choices=JourneyCompensationStatus.choices, blank=True, default=""
    )
    compensation_amount = models.DecimalField(**_MONEY)
```

- [ ] **Step 5: Cho phép sửa 8 field qua API**

Trong `backend/apps/warranty/journey_service.py`, thêm vào cuối tuple `JOURNEY_EDITABLE_FIELDS` (ngay sau dòng `"return_carrier", "return_shipping_fee", "return_order_date", "return_tracking_code",`):

```python
    "feedback_agree_review", "feedback_rating", "feedback_note",
    "claim_date", "claim_note", "carrier_response",
    "compensation_status", "compensation_amount",
```

`WarrantyJourneyWriteSerializer.Meta.fields` trong `serializers.py` dùng danh sách viết tay — thêm đúng 8 tên đó vào cuối tuple `fields` của nó, ngay sau `"return_tracking_code",`:

```python
            "feedback_agree_review", "feedback_rating", "feedback_note",
            "claim_date", "claim_note", "carrier_response",
            "compensation_status", "compensation_amount",
```

Và thêm y hệt 8 tên vào tuple `fields` của `WarrantyJourneySerializer.Meta` (ngay sau `"return_status",`) để FE đọc được.

- [ ] **Step 6: Sinh migration**

Run lệnh `makemigrations` ở mục "Cách chạy test" phía trên.
Expected: tạo `apps/warranty/migrations/0018_journey_feedback_claim_fields.py` với 8 `AddField`, dependency trỏ `("warranty", "0017_recall_code_index")`.

Mở file kiểm tra: `claim_date` là `DateField(null=True, blank=True)`; `compensation_amount` là `DecimalField` khớp `_MONEY`; 5 field còn lại `CharField`/`TextField` `blank=True, default=""`.

- [ ] **Step 7: Chạy test để xác nhận PASS**

Run: `.\.superpowers\run-backend-tests.ps1 apps/warranty`
Expected: toàn bộ PASS, gồm 4 test mới.

- [ ] **Step 8: Xác nhận không còn migration thiếu**

Run lệnh `makemigrations` nhưng đổi đuôi thành `--check --dry-run` (bỏ `--name`):
`... desi-backend-test python manage.py makemigrations --check --dry-run`
Expected: `No changes detected`.

- [ ] **Step 9: Commit**

```bash
git add backend/apps/warranty/models.py backend/apps/warranty/journey_service.py backend/apps/warranty/serializers.py backend/apps/warranty/migrations/0018_journey_feedback_claim_fields.py backend/apps/warranty/tests/test_journey_feedback_claim_fields.py
git commit -m "feat(warranty): add customer feedback and lost-parcel claim fields"
```

---

### Task 2: Hàm thuần tính cờ hiển thị

**Files:**
- Create: `frontend/src/features/warranty-claims/components/journey/journey-visibility.ts`
- Test: `frontend/src/features/warranty-claims/components/journey/journey-visibility.test.ts`

**Interfaces:**
- Produces:
  - `type JourneyVcGroups = { new: string | null; recall: string | null; return: string | null }`
  - `type JourneySectionFlags = { warehouse: boolean; resolution: boolean; returnBlock: boolean; feedback: boolean; claim: boolean }`
  - `journeySectionFlags(shippingForm: string, vcGroups: JourneyVcGroups | null | undefined, inspectionResult: string): JourneySectionFlags`
- Task 5 gọi hàm này trong `JourneyCard`.

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/features/warranty-claims/components/journey/journey-visibility.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { journeySectionFlags, type JourneyVcGroups } from "./journey-visibility";

const groups = (over: Partial<JourneyVcGroups> = {}): JourneyVcGroups => ({
  new: null,
  recall: null,
  return: null,
  ...over,
});

describe("journeySectionFlags", () => {
  it("phiếu mới tạo: chưa mã, chưa trạng thái → không hiện khối nào", () => {
    expect(journeySectionFlags("", null, "")).toEqual({
      warehouse: false,
      resolution: false,
      returnBlock: false,
      feedback: false,
      claim: false,
    });
    expect(journeySectionFlags("one_way", groups(), "")).toEqual({
      warehouse: false,
      resolution: false,
      returnBlock: false,
      feedback: false,
      claim: false,
    });
  });

  it("đơn 1 chiều + mã gửi đi GTC → hiện feedback", () => {
    const flags = journeySectionFlags("one_way", groups({ new: "delivered" }), "");
    expect(flags.feedback).toBe(true);
    expect(flags.warehouse).toBe(false);
    expect(flags.returnBlock).toBe(false);
  });

  it("đơn đổi hàng + mã gửi đi GTC → hiện feedback", () => {
    expect(journeySectionFlags("exchange", groups({ new: "delivered" }), "").feedback).toBe(true);
  });

  it("đơn đổi hàng + mã thu hồi Đã trả hàng → hiện kho nhận, chưa hiện §7", () => {
    const flags = journeySectionFlags("exchange", groups({ recall: "returned" }), "");
    expect(flags.warehouse).toBe(true);
    expect(flags.returnBlock).toBe(false);
    expect(flags.resolution).toBe(false);
  });

  it("đơn thu hồi BH + mã thu hồi GTC → hiện kho nhận VÀ §7", () => {
    const flags = journeySectionFlags("warranty_recall", groups({ recall: "delivered" }), "");
    expect(flags.warehouse).toBe(true);
    expect(flags.returnBlock).toBe(true);
    expect(flags.feedback).toBe(false);
  });

  it("đơn thu hồi BH + mã gửi lại GTC → hiện feedback", () => {
    const flags = journeySectionFlags("warranty_recall", groups({ return: "delivered" }), "");
    expect(flags.feedback).toBe(true);
  });

  it("kết quả kiểm tra đã điền → hiện CSKH chốt phương án", () => {
    const flags = journeySectionFlags("exchange", groups({ recall: "returned" }), "normal");
    expect(flags.warehouse).toBe(true);
    expect(flags.resolution).toBe(true);
  });

  it("resolution KHÔNG bao giờ hiện khi warehouse chưa hiện", () => {
    const flags = journeySectionFlags("one_way", groups({ new: "delivered" }), "normal");
    expect(flags.warehouse).toBe(false);
    expect(flags.resolution).toBe(false);
  });

  it("bất kỳ mã nào thất lạc → hiện khiếu nại & đền bù", () => {
    expect(journeySectionFlags("one_way", groups({ new: "lost" }), "").claim).toBe(true);
    expect(journeySectionFlags("exchange", groups({ recall: "lost" }), "").claim).toBe(true);
    expect(journeySectionFlags("warranty_recall", groups({ return: "lost" }), "").claim).toBe(true);
  });

  it("2 loại đơn chưa định nghĩa luồng: chỉ bật claim khi thất lạc", () => {
    for (const form of ["return_recall", "warranty_return"]) {
      expect(journeySectionFlags(form, groups({ new: "delivered", recall: "returned" }), "normal")).toEqual({
        warehouse: false,
        resolution: false,
        returnBlock: false,
        feedback: false,
        claim: false,
      });
      expect(journeySectionFlags(form, groups({ new: "lost" }), "").claim).toBe(true);
    }
  });

  it("hình thức gửi rỗng: chỉ claim theo mã thất lạc", () => {
    expect(journeySectionFlags("", groups({ new: "delivered" }), "").feedback).toBe(false);
    expect(journeySectionFlags("", groups({ new: "lost" }), "").claim).toBe(true);
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `npx vitest run src/features/warranty-claims/components/journey/journey-visibility.test.ts`
Expected: FAIL — không resolve được module `./journey-visibility`.

- [ ] **Step 3: Cài đặt**

Tạo `frontend/src/features/warranty-claims/components/journey/journey-visibility.ts`:

```ts
/**
 * Quy tắc ẩn/hiện các khối hậu-kỳ của form hành trình BH.
 *
 * Logic TRÌNH BÀY thuần — cố ý không tính ở backend để đổi quy tắc UI không
 * phải sửa serializer + regen schema. Không import React nên test trực tiếp
 * được. Spec: docs/superpowers/specs/2026-07-22-warranty-journey-conditional-flow-design.md
 */

/**
 * Nhóm trạng thái VC của 3 chặng (đọc từ `journey.vc_groups` của serializer).
 * Khai optional để nhận thẳng object của serializer — nó còn có khoá `main`
 * và nullability do OpenAPI sinh, không nên ép caller phải map lại.
 */
export type JourneyVcGroups = {
  new?: string | null;
  recall?: string | null;
  return?: string | null;
};

export type JourneySectionFlags = {
  /** §5 Kho nhận hàng BH */
  warehouse: boolean;
  /** §6 CSKH chốt phương án */
  resolution: boolean;
  /** §7 Gửi đơn mới về khách */
  returnBlock: boolean;
  /** Khách nhận hàng — Cảm nhận */
  feedback: boolean;
  /** Khiếu nại & đền bù */
  claim: boolean;
};

const NONE: JourneySectionFlags = {
  warehouse: false,
  resolution: false,
  returnBlock: false,
  feedback: false,
  claim: false,
};

export function journeySectionFlags(
  shippingForm: string,
  vcGroups: JourneyVcGroups | null | undefined,
  inspectionResult: string,
): JourneySectionFlags {
  if (!vcGroups) return { ...NONE };

  const isNewDelivered = vcGroups.new === "delivered";
  const isRecallDelivered = vcGroups.recall === "delivered";
  const isRecallReturned = vcGroups.recall === "returned";
  const isReturnDelivered = vcGroups.return === "delivered";

  // Thất lạc ở BẤT KỲ chặng nào cũng mở khối khiếu nại, kể cả loại đơn chưa
  // định nghĩa luồng.
  const claim =
    vcGroups.new === "lost" || vcGroups.recall === "lost" || vcGroups.return === "lost";

  const feedback =
    ((shippingForm === "one_way" || shippingForm === "exchange") && isNewDelivered) ||
    (shippingForm === "warranty_recall" && isReturnDelivered);

  const warehouse =
    (shippingForm === "exchange" && isRecallReturned) ||
    (shippingForm === "warranty_recall" && isRecallDelivered);

  const returnBlock = shippingForm === "warranty_recall" && isRecallDelivered;

  // Không chốt được phương án khi chưa biết hàng hỏng thế nào → phụ thuộc
  // warehouse, không bao giờ hiện một mình.
  const resolution = warehouse && inspectionResult.trim() !== "";

  return { warehouse, resolution, returnBlock, feedback, claim };
}
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `npx vitest run src/features/warranty-claims/components/journey/journey-visibility.test.ts`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/warranty-claims/components/journey/journey-visibility.ts frontend/src/features/warranty-claims/components/journey/journey-visibility.test.ts
git commit -m "feat(warranty): add pure visibility rules for journey sections"
```

---

### Task 3: 8 field mới vào form state và enum options

**Files:**
- Modify: `frontend/src/features/warranty-claims/journey-enums.ts`
- Modify: `frontend/src/features/warranty-claims/components/journey/journey-form.ts`
- Test: `frontend/src/features/warranty-claims/components/journey/journey-form.test.ts` (tạo mới)

**Interfaces:**
- Consumes: 8 field backend từ Task 1 (cùng tên).
- Produces: `JourneyFormState` có thêm 8 key; `FEEDBACK_AGREE_OPTIONS`, `FEEDBACK_RATING_OPTIONS`, `CARRIER_RESPONSE_OPTIONS`, `COMPENSATION_STATUS_OPTIONS` trong `journey-enums.ts`. Task 4 dùng các option này.

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/features/warranty-claims/components/journey/journey-form.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import type { WarrantyJourney } from "@/features/warranty-claims/warranty-claim-types";
import { buildJourneyPatch, initForm } from "./journey-form";

const journey = {
  id: 1,
  seq: 1,
  feedback_agree_review: "yes",
  feedback_rating: "good",
  feedback_note: "Khách khen nhanh",
  claim_date: "2026-07-20",
  claim_note: "Best báo mất kiện",
  carrier_response: "agreed",
  compensation_status: "paid",
  compensation_amount: "350000.00",
} as unknown as WarrantyJourney;

describe("journey-form: field feedback + khiếu nại", () => {
  it("initForm đọc đủ 8 field từ bản ghi server", () => {
    const form = initForm(journey);
    expect(form.feedback_agree_review).toBe("yes");
    expect(form.feedback_rating).toBe("good");
    expect(form.feedback_note).toBe("Khách khen nhanh");
    expect(form.claim_date).toBe("2026-07-20");
    expect(form.claim_note).toBe("Best báo mất kiện");
    expect(form.carrier_response).toBe("agreed");
    expect(form.compensation_status).toBe("paid");
    expect(form.compensation_amount).toBe("350000.00");
  });

  it("claim_date null từ server → chuỗi rỗng trong form", () => {
    const form = initForm({ ...journey, claim_date: null } as unknown as WarrantyJourney);
    expect(form.claim_date).toBe("");
  });

  it("buildJourneyPatch gửi đủ 8 field; ngày rỗng → null, tiền rỗng → 0", () => {
    const form = initForm(journey);
    const patch = buildJourneyPatch(form, journey);
    expect(patch.feedback_rating).toBe("good");
    expect(patch.claim_date).toBe("2026-07-20");
    expect(patch.compensation_amount).toBe("350000.00");

    const empty = buildJourneyPatch(
      { ...form, claim_date: "", compensation_amount: "" },
      journey,
    );
    expect(empty.claim_date).toBeNull();
    expect(empty.compensation_amount).toBe("0");
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `npx vitest run src/features/warranty-claims/components/journey/journey-form.test.ts`
Expected: FAIL — `expect(form.feedback_agree_review).toBe("yes")` nhận `undefined`.

- [ ] **Step 3: Thêm option arrays vào `journey-enums.ts`**

Thêm vào cuối file, TRƯỚC khối `VC_GROUP_LABELS`:

```ts
/** Khách nhận hàng — cảm nhận: `feedback_agree_review`. */
export const FEEDBACK_AGREE_OPTIONS = [
  { value: "yes", label: "Có" },
  { value: "no", label: "Không" },
] as const;

/** Khách nhận hàng — cảm nhận: `feedback_rating`. */
export const FEEDBACK_RATING_OPTIONS = [
  { value: "good", label: "Tốt" },
  { value: "average", label: "Trung bình" },
  { value: "unsatisfied", label: "Không hài lòng" },
  { value: "lost_trust", label: "Mất niềm tin" },
] as const;

/** Khiếu nại & đền bù: `carrier_response`. */
export const CARRIER_RESPONSE_OPTIONS = [
  { value: "pending", label: "Chưa phản hồi" },
  { value: "working_with_post", label: "Chờ lv với bưu cục" },
  { value: "agreed", label: "Chốt đền bù" },
  { value: "rejected", label: "Không được đền bù" },
] as const;

/** Khiếu nại & đền bù: `compensation_status`. */
export const COMPENSATION_STATUS_OPTIONS = [
  { value: "unpaid", label: "Chưa đền bù" },
  { value: "paid", label: "Đã đền bù" },
] as const;
```

- [ ] **Step 4: Thêm 8 field vào `journey-form.ts`**

Trong `JourneyFormState`, thêm sau `return_tracking_code: string;`:

```ts
  feedback_agree_review: NonNullable<WarrantyJourneyPayload["feedback_agree_review"]>;
  feedback_rating: NonNullable<WarrantyJourneyPayload["feedback_rating"]>;
  feedback_note: string;
  claim_date: string;
  claim_note: string;
  carrier_response: NonNullable<WarrantyJourneyPayload["carrier_response"]>;
  compensation_status: NonNullable<WarrantyJourneyPayload["compensation_status"]>;
  compensation_amount: string;
```

Trong `initForm`, thêm sau `return_tracking_code: j.return_tracking_code,`:

```ts
    feedback_agree_review: j.feedback_agree_review,
    feedback_rating: j.feedback_rating,
    feedback_note: j.feedback_note,
    claim_date: j.claim_date ?? "",
    claim_note: j.claim_note,
    carrier_response: j.carrier_response,
    compensation_status: j.compensation_status,
    compensation_amount: j.compensation_amount,
```

Trong `buildJourneyPatch`, thêm sau `return_tracking_code: form.return_tracking_code,`:

```ts
    feedback_agree_review: form.feedback_agree_review,
    feedback_rating: form.feedback_rating,
    feedback_note: form.feedback_note,
    claim_date: form.claim_date || null,
    claim_note: form.claim_note,
    carrier_response: form.carrier_response,
    compensation_status: form.compensation_status,
    compensation_amount: money(form.compensation_amount),
```

- [ ] **Step 5: Regen type OpenAPI**

`WarrantyJourneyPayload` sinh từ `schema.d.ts`. Sinh lại schema rồi regen client:

```
docker run --rm --network desi-test-net -v "C:\Users\MYPC\Desktop\workspace\desi-order\backend:/app" -w /app -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_order -e POSTGRES_USER=desi_order -e POSTGRES_PASSWORD=desi_order -e DJANGO_SECRET_KEY=test-secret -e "ENCRYPTION_KEY=_o0HqX1zVD_tDJUDcrKnImGYYJrBrHh62VtEsV_h-ao=" -e REDIS_HOST=desi-test-redis desi-backend-test python manage.py spectacular --file schema.yaml
```

rồi trong `frontend/`: `npm run gen:api`

Nếu `npm run gen:api` không tồn tại, mở `frontend/package.json` xem tên script sinh type thật rồi chạy đúng tên đó. Chạy `npx prettier --write src/api/schema.d.ts` sau khi sinh.

- [ ] **Step 6: Chạy test để xác nhận PASS**

Run: `npx vitest run src/features/warranty-claims` rồi `npx tsc --noEmit`
Expected: toàn bộ PASS, typecheck sạch.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/warranty-claims/journey-enums.ts frontend/src/features/warranty-claims/components/journey/journey-form.ts frontend/src/features/warranty-claims/components/journey/journey-form.test.ts frontend/src/api/schema.d.ts backend/schema.yaml
git commit -m "feat(warranty): wire feedback and claim fields into journey form state"
```

---

### Task 4: Hai khối UI mới + badge tình trạng nhận thực

**Files:**
- Modify: `frontend/src/features/warranty-claims/components/journey/journey-sections-return.tsx`
- Test: (phủ ở Task 5 qua `JourneyCard.test.tsx`)

**Interfaces:**
- Consumes: option arrays + `JourneyFormState` từ Task 3.
- Produces: `FeedbackSection`, `ClaimSection` — cùng chữ ký `JourneySectionProps` như các section sẵn có. Task 5 render chúng.

- [ ] **Step 1: Thêm badge "Tình trạng nhận thực" vào `WarehouseSection`**

Trong `journey-sections-return.tsx`, thay nguyên `WarehouseSection`:

```tsx
/** §5 Kho nhận hàng BH. */
export function WarehouseSection({ journey, form, set }: JourneySectionProps) {
  const fieldId = (name: string) => journeyFieldId(journey.id, name);
  // Tình trạng nhận thực KHÔNG có field riêng: suy từ trạng thái nội bộ do quét
  // kho đặt, tránh 2 nguồn sự thật lệch nhau (xem spec §Model).
  const received = journey.status === "returned";
  return (
    <section>
      <h4 className={sectionTitleClass}>🏭 Kho nhận hàng BH</h4>
      <FormGrid>
        <Field label="Tình trạng nhận thực">
          <span
            className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
              received ? "bg-teal-100 text-teal-700" : "bg-gray-200 text-gray-600"
            }`}
          >
            {received ? "Đã nhận" : "Chưa nhận"}
          </span>
        </Field>
        <Field label="Ngày nhận hàng hoàn" htmlFor={fieldId("warehouse_received_at")}>
          <Input
            id={fieldId("warehouse_received_at")}
            type="date"
            value={form.warehouse_received_at}
            onChange={(e) => set("warehouse_received_at", e.target.value)}
          />
        </Field>
        <Field label="Kết quả kiểm tra" htmlFor={fieldId("inspection_result")}>
          <EnumSelect
            id={fieldId("inspection_result")}
            value={form.inspection_result}
            onValueChange={(v) => set("inspection_result", v)}
            options={INSPECTION_OPTIONS}
          />
        </Field>
      </FormGrid>
    </section>
  );
}
```

- [ ] **Step 2: Thêm 2 section mới**

Thêm vào cuối `journey-sections-return.tsx`:

```tsx
/** Khách nhận hàng — cảm nhận. Hiện khi chặng giao tới khách đã GTC. */
export function FeedbackSection({ journey, form, set }: JourneySectionProps) {
  const fieldId = (name: string) => journeyFieldId(journey.id, name);
  return (
    <section>
      <h4 className={sectionTitleClass}>✅ Khách nhận hàng — Cảm nhận</h4>
      <FormGrid>
        <Field label="Đồng ý để lại review?" htmlFor={fieldId("feedback_agree_review")}>
          <EnumSelect
            id={fieldId("feedback_agree_review")}
            value={form.feedback_agree_review}
            onValueChange={(v) => set("feedback_agree_review", v)}
            options={FEEDBACK_AGREE_OPTIONS}
          />
        </Field>
        <Field label="Đánh giá về trải nghiệm BH" htmlFor={fieldId("feedback_rating")}>
          <EnumSelect
            id={fieldId("feedback_rating")}
            value={form.feedback_rating}
            onValueChange={(v) => set("feedback_rating", v)}
            options={FEEDBACK_RATING_OPTIONS}
          />
        </Field>
        <Field label="Note cảm nhận của khách" htmlFor={fieldId("feedback_note")}>
          <Textarea
            id={fieldId("feedback_note")}
            value={form.feedback_note}
            onChange={(e) => set("feedback_note", e.target.value)}
            rows={2}
          />
        </Field>
      </FormGrid>
    </section>
  );
}

/** Khiếu nại & đền bù — hiện khi có mã vận đơn rơi vào nhóm thất lạc. */
export function ClaimSection({ journey, form, set }: JourneySectionProps) {
  const fieldId = (name: string) => journeyFieldId(journey.id, name);
  return (
    <section>
      <h4 className={sectionTitleClass}>📮 Khiếu nại & đền bù</h4>
      <FormGrid>
        <Field label="Ngày khiếu nại" htmlFor={fieldId("claim_date")}>
          <Input
            id={fieldId("claim_date")}
            type="date"
            value={form.claim_date}
            onChange={(e) => set("claim_date", e.target.value)}
          />
        </Field>
        <Field label="Phản hồi của ĐVVC" htmlFor={fieldId("carrier_response")}>
          <EnumSelect
            id={fieldId("carrier_response")}
            value={form.carrier_response}
            onValueChange={(v) => set("carrier_response", v)}
            options={CARRIER_RESPONSE_OPTIONS}
          />
        </Field>
        <Field label="Tình trạng đền bù" htmlFor={fieldId("compensation_status")}>
          <EnumSelect
            id={fieldId("compensation_status")}
            value={form.compensation_status}
            onValueChange={(v) => set("compensation_status", v)}
            options={COMPENSATION_STATUS_OPTIONS}
          />
        </Field>
        <Field label="Số tiền đền bù" htmlFor={fieldId("compensation_amount")}>
          <Input
            id={fieldId("compensation_amount")}
            type="number"
            step="0.01"
            value={form.compensation_amount}
            onChange={(e) => set("compensation_amount", e.target.value)}
          />
        </Field>
        <Field label="Ghi chú" htmlFor={fieldId("claim_note")}>
          <Textarea
            id={fieldId("claim_note")}
            value={form.claim_note}
            onChange={(e) => set("claim_note", e.target.value)}
            rows={2}
          />
        </Field>
      </FormGrid>
    </section>
  );
}
```

Bổ sung import ở đầu file:

```ts
import {
  CARRIER_OPTIONS,
  CARRIER_RESPONSE_OPTIONS,
  COMPENSATION_STATUS_OPTIONS,
  FEEDBACK_AGREE_OPTIONS,
  FEEDBACK_RATING_OPTIONS,
  INSPECTION_OPTIONS,
  RESOLUTION_OPTIONS,
} from "@/features/warranty-claims/journey-enums";
```

- [ ] **Step 3: Xác nhận typecheck sạch**

Run: `npx tsc --noEmit`
Expected: không lỗi. (Hai section mới chưa được render ở đâu — đó là việc của Task 5.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/warranty-claims/components/journey/journey-sections-return.tsx
git commit -m "feat(warranty): add feedback and claim sections plus receipt badge"
```

---

### Task 5: `JourneyCard` render theo cờ

**Files:**
- Modify: `frontend/src/features/warranty-claims/components/JourneyCard.tsx:72,162-170`
- Test: `frontend/src/features/warranty-claims/components/JourneyCard.test.tsx`

**Interfaces:**
- Consumes: `journeySectionFlags` (Task 2), `FeedbackSection` / `ClaimSection` (Task 4).

- [ ] **Step 1: Viết test thất bại**

File đã có sẵn `mockJourney` (fixture), `renderCard(journey)` (nhận journey ĐẦY ĐỦ, không phải overrides) và `expandCard()`. Dùng đúng ba thứ đó — đừng tự chế helper mới.

Thêm helper gọn ở đầu `describe`:

```tsx
  const cardWith = (overrides: Partial<WarrantyJourney>) => {
    renderCard({ ...mockJourney, ...overrides } as WarrantyJourney);
    expandCard();
  };
```

Rồi thêm các test sau vào cuối file:

```tsx
  it("phiếu mới tạo → KHÔNG hiện kho nhận, CSKH, gửi đơn mới, feedback, khiếu nại", () => {
    cardWith({
      shipping_form: "",
      inspection_result: "",
      vc_groups: { new: null, recall: null, return: null, main: "created" },
    });

    expect(screen.queryByText("🏭 Kho nhận hàng BH")).not.toBeInTheDocument();
    expect(screen.queryByText("📤 CSKH chốt phương án")).not.toBeInTheDocument();
    expect(screen.queryByText("🛡️ Gửi đơn mới về khách")).not.toBeInTheDocument();
    expect(screen.queryByText("✅ Khách nhận hàng — Cảm nhận")).not.toBeInTheDocument();
    expect(screen.queryByText("📮 Khiếu nại & đền bù")).not.toBeInTheDocument();
  });

  it("đơn 1 chiều + mã gửi đi GTC → hiện đúng khối feedback", () => {
    cardWith({
      shipping_form: "one_way",
      vc_groups: { new: "delivered", recall: null, return: null, main: "delivered" },
    });

    expect(screen.getByText("✅ Khách nhận hàng — Cảm nhận")).toBeInTheDocument();
    expect(screen.queryByText("🏭 Kho nhận hàng BH")).not.toBeInTheDocument();
  });

  it("đơn thu hồi BH + mã thu hồi GTC → hiện kho nhận + gửi đơn mới, chưa hiện CSKH", () => {
    cardWith({
      shipping_form: "warranty_recall",
      inspection_result: "",
      vc_groups: { new: null, recall: "delivered", return: null, main: "delivered" },
    });

    expect(screen.getByText("🏭 Kho nhận hàng BH")).toBeInTheDocument();
    expect(screen.getByText("🛡️ Gửi đơn mới về khách")).toBeInTheDocument();
    expect(screen.queryByText("📤 CSKH chốt phương án")).not.toBeInTheDocument();
  });

  it("đã có kết quả kiểm tra → hiện CSKH chốt phương án", () => {
    cardWith({
      shipping_form: "warranty_recall",
      inspection_result: "normal",
      vc_groups: { new: null, recall: "delivered", return: null, main: "delivered" },
    });

    expect(screen.getByText("📤 CSKH chốt phương án")).toBeInTheDocument();
  });

  it("mã thất lạc → hiện khối khiếu nại & đền bù", () => {
    cardWith({
      shipping_form: "one_way",
      vc_groups: { new: "lost", recall: null, return: null, main: "lost" },
    });

    expect(screen.getByText("📮 Khiếu nại & đền bù")).toBeInTheDocument();
  });

  it("tình trạng nhận thực bám theo trạng thái quét kho", () => {
    cardWith({
      shipping_form: "warranty_recall",
      status: "returned",
      vc_groups: { new: null, recall: "delivered", return: null, main: "delivered" },
    });

    expect(screen.getByText("Đã nhận")).toBeInTheDocument();
  });
```

Lưu ý: `mockJourney` mặc định có `inspection_result: "normal"` — test nào muốn kiểm "chưa hiện CSKH" PHẢI ghi đè về `""`, nếu không cờ `resolution` bật và test sai ý.

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `npx vitest run src/features/warranty-claims/components/JourneyCard.test.tsx`
Expected: FAIL — các khối vẫn luôn render nên `queryByText("🏭 Kho nhận hàng BH")` tìm thấy phần tử.

- [ ] **Step 3: Cài đặt**

Trong `JourneyCard.tsx`, bổ sung import:

```tsx
import { journeySectionFlags } from "./journey/journey-visibility";
```

và thêm `ClaimSection`, `FeedbackSection` vào import sẵn có từ `./journey/journey-sections-return`.

Ngay dưới dòng `const sectionProps = { journey, form, set, setSynced };` (dòng 72), thêm:

```tsx
  // Cờ ẩn/hiện tính MỘT lần ở đây rồi truyền xuống — không để mỗi section tự suy.
  // Dùng `form.inspection_result` (bản nháp) chứ không phải `journey`: chọn kết
  // quả kiểm tra xong là khối CSKH hiện ngay, không phải bấm Lưu trước.
  const flags = journeySectionFlags(form.shipping_form, journey.vc_groups, form.inspection_result);
```

Thay khối render các section (dòng 164-169) bằng:

```tsx
          <ReceiverSection {...sectionProps} />
          <ProductSection {...sectionProps} />
          <ShippingSection {...sectionProps} />
          {flags.warehouse && <WarehouseSection {...sectionProps} />}
          {flags.resolution && <ResolutionSection {...sectionProps} />}
          {flags.returnBlock && <ReturnSection {...sectionProps} />}
          {flags.feedback && <FeedbackSection {...sectionProps} />}
          {flags.claim && <ClaimSection {...sectionProps} />}
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `npx vitest run src/features/warranty-claims` rồi `npx tsc --noEmit`
Expected: toàn bộ PASS, typecheck sạch.

- [ ] **Step 5: Chạy toàn bộ test frontend + lint**

Run: `npx vitest run` rồi `npm run lint`
Expected: chỉ còn 2 test fail sẵn có ở `src/app/__tests__/RequireAuth.test.tsx` (lệch phiên bản Node, KHÔNG phải do nhánh này). Lint sạch.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/warranty-claims/components/JourneyCard.tsx frontend/src/features/warranty-claims/components/JourneyCard.test.tsx
git commit -m "feat(warranty): render journey sections by visibility flags"
```

---

## Ghi chú cho người thực hiện

- Thứ tự bắt buộc: 1 → 2 → 3 → 4 → 5. Task 3 cần Task 1 (type OpenAPI sinh từ serializer backend); Task 4 cần Task 3; Task 5 cần Task 2 và 4.
- Sau Task 4, hai section mới chưa render ở đâu cả — đúng dự kiến, Task 5 mới nối vào. Đừng "tiện tay" nối sớm.
- `flags` dùng `form.shipping_form` và `form.inspection_result` (bản nháp) nhưng `journey.vc_groups` (server). Cố ý: hai cái đầu người dùng đang gõ nên phải phản hồi ngay; `vc_groups` là dẫn xuất từ trạng thái Best, chỉ đổi khi refetch.
- Sau khi merge, kiểm tra bằng mắt trên một phiếu thật: phiếu mới tạo chỉ có §2-§4; tạo đơn Best chặng thu hồi rồi đợi badge GTC → §5 và §7 hiện ra.
