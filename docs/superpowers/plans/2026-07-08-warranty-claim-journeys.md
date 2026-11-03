# Warranty Claim Multi-Journey Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chuyển phiếu bảo hành từ bảng phẳng 1-1 Order sang mô hình 1 phiếu (1-1 Order) chứa N hành trình, mỗi hành trình có đủ section 2–7 + trạng thái, phiếu có nút Hoàn thành.

**Architecture:** 2 bảng — `warranty_claims` (section 1 cấp phiếu + trạng thái phiếu) và `warranty_journeys` (section 2–7 dạng cột + seq + trạng thái hành trình). Backend giữ clean layering hiện có (repository/service/serializer/view tách file). Frontend React + React Query + typed OpenAPI, trang chi tiết accordion các hành trình.

**Tech Stack:** Django 6 + DRF + drf-spectacular (OpenAPI), PostgreSQL; React + TypeScript + shadcn/ui + TanStack Query + openapi-typescript.

## Global Constraints

- Backend clean architecture: transport (views) → business (service) → storage (repository) → model. Business KHÔNG gọi ORM trực tiếp qua view; view chỉ gọi service/repository.
- RBAC mọi endpoint claim/journey: `IsAuthenticated + require_section("warranty")`. Ingest public giữ token gate `ORDERS_INGEST_API_TOKEN`.
- Tiền dùng `DecimalField(max_digits=12, decimal_places=2, default=0)` (khớp Order).
- Enum lưu `CharField(choices=...)`; text default `""` blank=True; date `null=True, blank=True` (cho lưu nháp từng phần).
- KHÔNG lưu trùng dữ liệu Order (mã HĐ, ngày gửi, kênh KV) — đọc live qua FK `order`.
- Frontend code MỚI: GET dùng React Query + `queries.ts`; api module typed từ `src/api/schema.d.ts`; CẤM `react-select` (dùng `components/ui/select`). Không tự khai type response bằng tay.
- Commit message tiếng Anh, ngắn gọn, KHÔNG kèm dòng Co-Authored-By.
- Nhánh làm việc: `feat/warranty-claim-journeys` (worktree `.claude/worktrees/feat-warranty-claim-journeys`).

## Cách chạy test (tham chiếu chung)

**Backend** (image `desi-order-web-test`, net `desi-order_desi-net`, DB unique — theo memory running-backend-tests). Sau khi thêm migration phải có `--create-db`:

```bash
docker run --rm --network desi-order_desi-net -e POSTGRES_DB=desi_warranty_wj \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/warranty -q --create-db'
```

Lint: `docker run --rm -v "$PWD/backend:/app" -w /app desi-order-web-test:latest ruff check apps/warranty`

**Frontend** (trong `frontend/`): `npm run typecheck && npm run test`. Regen types sau khi đổi OpenAPI: `npm run gen:api` (đọc từ backend schema).

---

## SPRINT 1 — Model & Migration

### Task 1: Enums + mở rộng model `WarrantyClaim`

**Files:**
- Modify: `backend/apps/warranty/models.py`
- Test: `backend/apps/warranty/tests/test_models.py` (create)

**Interfaces:**
- Produces: `WarrantyClaimStatus` (IntegerChoices: PROCESSING=1, COMPLETED=2, CANCELLED=3), `WarrantyClaim` với thêm field `customer_report_date: date|None`, `completed_at: datetime|None`. Giữ `order`, `created_by`, `created_at`, `updated_at`, `deleted_at`.

- [ ] **Step 1: Viết test model claim mới**

Thêm vào `backend/apps/warranty/tests/test_models.py`:

```python
import pytest
from django.utils import timezone

from apps.warranty.models import WarrantyClaim, WarrantyClaimStatus


@pytest.mark.django_db
def test_claim_defaults(order_factory):
    order = order_factory()
    claim = WarrantyClaim.objects.create(order=order)
    assert claim.status == WarrantyClaimStatus.PROCESSING
    assert claim.customer_report_date is None
    assert claim.completed_at is None
    assert claim.deleted_at is None


@pytest.mark.django_db
def test_claim_status_choices():
    assert set(WarrantyClaimStatus.values) == {1, 2, 3}
```

> `order_factory` — nếu chưa có fixture, tạo `backend/apps/warranty/tests/conftest.py` trả về hàm tạo Order tối thiểu (xem Step 1b). Kiểm tra field bắt buộc của Order bằng `codegraph_explore "Order model required fields"` trước khi viết factory.

- [ ] **Step 1b: Tạo fixture order_factory (nếu chưa có)**

`backend/apps/warranty/tests/conftest.py`:

```python
import pytest


@pytest.fixture
def order_factory(db):
    from apps.orders.models import Order

    def _make(**kwargs):
        defaults = {"name": "Khách A", "phone": "0900000001"}
        defaults.update(kwargs)
        return Order.objects.create(**defaults)

    return _make
```

(Điều chỉnh `defaults` cho khớp field NOT NULL thực tế của Order.)

- [ ] **Step 2: Chạy test — kỳ vọng FAIL**

Run: `... python -m pytest apps/warranty/tests/test_models.py -q --create-db`
Expected: FAIL (`customer_report_date`/`completed_at` chưa tồn tại, hoặc CANCELLED=3 chưa đúng).

- [ ] **Step 3: Sửa `models.py` — enum + THÊM field claim (GIỮ field cũ tới Task 3)**

Trong `backend/apps/warranty/models.py`, thay `WarrantyClaimStatus`:

```python
class WarrantyClaimStatus(models.IntegerChoices):
    PROCESSING = 1, "Đang xử lý"
    COMPLETED = 2, "Hoàn thành"
    CANCELLED = 3, "Hủy"
```

Trong class `WarrantyClaim`: THÊM 2 field mới (`customer_report_date`, `completed_at`) và giữ nguyên `order`/`created_by`/`created_at`/`updated_at`/`deleted_at`. **QUAN TRỌNG: KHÔNG xóa các field nghiệp vụ cũ** (`contact_channel`, `require_carrier_call`, `social_nickname`, `send_reason`, `send_method`, `classification`, `recall_code`, `return_status`, `accounting_check`, `accounting_note`) ở task này — data migration ở Task 3 cần đọc chúng; chúng sẽ bị drop ở Task 3 sau khi backfill xong.

```python
class WarrantyClaim(models.Model):
    """Phiếu bảo hành nội bộ 1-1 với một đơn. Section 1 đọc live qua FK order."""

    order = models.OneToOneField(
        "orders.Order", on_delete=models.PROTECT, related_name="warranty_claim"
    )
    customer_report_date = models.DateField(null=True, blank=True)   # MỚI
    status = models.PositiveSmallIntegerField(
        choices=WarrantyClaimStatus.choices, default=WarrantyClaimStatus.PROCESSING
    )
    completed_at = models.DateTimeField(null=True, blank=True)       # MỚI
    # --- field cũ GIỮ tạm (drop ở Task 3 sau data migration) ---
    contact_channel = models.CharField(max_length=100, blank=True, default="")
    require_carrier_call = models.BooleanField(default=False)
    social_nickname = models.CharField(max_length=255, blank=True, default="")
    send_reason = models.TextField(blank=True, default="")
    send_method = models.CharField(max_length=100, blank=True, default="")
    classification = models.CharField(max_length=100, blank=True, default="")
    recall_code = models.CharField(max_length=100, blank=True, default="")
    return_status = models.CharField(max_length=100, blank=True, default="")
    accounting_check = models.CharField(max_length=100, blank=True, default="")
    accounting_note = models.TextField(blank=True, default="")
    # -----------------------------------------------------------
    created_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "warranty_claims"
        indexes = [
            models.Index(fields=["status"], name="warranty_claim_status_idx"),
            models.Index(fields=["created_at"], name="warranty_claim_created_idx"),
            models.Index(fields=["deleted_at"], name="warranty_claim_deleted_idx"),
        ]

    def __str__(self):
        return f"WarrantyClaim #{self.pk} (order={self.order_id})"
```

- [ ] **Step 4: Sinh migration cho field mới**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w /app desi-order-web-test:latest \
  python manage.py makemigrations warranty --name add_claim_section1_fields
```
(Dùng `MSYS_NO_PATHCONV=1` prefix nếu chạy Git Bash.) Kỳ vọng: file `0004_add_claim_section1_fields.py` chỉ gồm `AddField(customer_report_date)`, `AddField(completed_at)`, `AlterField(status)`. KHÔNG có RemoveField nào (vì field cũ còn giữ). Nếu có RemoveField → bạn đã lỡ xóa field cũ, quay lại Step 3.

- [ ] **Step 5: Chạy test — kỳ vọng PASS**

Run: `bash .superpowers/sdd/run-be-tests.sh apps/warranty/tests/test_models.py -q`
Expected: PASS (cột mới đã tồn tại nhờ migration 0004).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/warranty/models.py backend/apps/warranty/migrations/ backend/apps/warranty/tests/
git commit -m "feat: add warranty claim section-1 fields and status choices"
```

---

### Task 2: Model `WarrantyJourney`

**Files:**
- Modify: `backend/apps/warranty/models.py`
- Test: `backend/apps/warranty/tests/test_models.py`

**Interfaces:**
- Produces: `WarrantyJourney` (FK `claim` related_name="journeys", `seq`, section 2–7 fields), enums `JourneyStatus`, `JourneyClassification`, `JourneyErrorCategory`, `JourneyShippingForm`, `JourneyCarrier`, `JourneyInspectionResult`, `JourneyResolution`. Ordering `("claim", "seq")`.

- [ ] **Step 1: Viết test journey**

Thêm vào `test_models.py`:

```python
from apps.warranty.models import WarrantyJourney, JourneyStatus


@pytest.mark.django_db
def test_journey_defaults(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourney.objects.create(claim=claim, seq=1)
    assert j.status == JourneyStatus.NEW
    assert j.declared_value == 0
    assert j.receiver_name == ""
    assert j.claim.journeys.count() == 1


@pytest.mark.django_db
def test_journey_seq_unique_per_claim(order_factory):
    from django.db import IntegrityError

    claim = WarrantyClaim.objects.create(order=order_factory())
    WarrantyJourney.objects.create(claim=claim, seq=1)
    with pytest.raises(IntegrityError):
        WarrantyJourney.objects.create(claim=claim, seq=1)
```

- [ ] **Step 2: Chạy test — FAIL** (`WarrantyJourney` chưa tồn tại)

Run: `... python -m pytest apps/warranty/tests/test_models.py -q --create-db`
Expected: FAIL ImportError.

- [ ] **Step 3: Thêm enums + `WarrantyJourney` vào `models.py`**

```python
class JourneyStatus(models.TextChoices):
    NEW = "new", "Mới"
    CREATED = "created", "Đã lên đơn"
    PACKED = "packed", "Đã đóng hàng"
    SHIPPING = "shipping", "Đang giao"
    LOST = "lost", "Thất lạc"
    DELIVERED = "delivered", "GTC"


class JourneyClassification(models.TextChoices):
    SAME_PRODUCT = "same_product", "Cùng SP"
    DIFFERENT_PRODUCT = "different_product", "Khác SP"
    ACCESSORY = "accessory", "Phụ kiện"


class JourneyErrorCategory(models.TextChoices):
    PRODUCT = "product", "Sản phẩm"
    CUSTOMER = "customer", "Khách"
    SHIPPING = "shipping", "Vận chuyển"
    WAREHOUSE = "warehouse", "Kho"


class JourneyShippingForm(models.TextChoices):
    ONE_WAY = "one_way", "Đơn 1 chiều"
    EXCHANGE = "exchange", "Đơn đổi hàng"
    WARRANTY_RECALL = "warranty_recall", "Đơn thu hồi BH"
    RETURN_RECALL = "return_recall", "Đơn thu hồi trả hàng"
    WARRANTY_RETURN = "warranty_return", "Đơn trả BH"


class JourneyCarrier(models.TextChoices):
    GHN = "ghn", "GHN"
    BEST = "best", "BEST"
    VTP = "vtp", "VTP"


class JourneyInspectionResult(models.TextChoices):
    BROKEN_UNREPAIRABLE = "broken_unrepairable", "Hỏng - Ko sửa được"
    BROKEN_REPAIRABLE = "broken_repairable", "Hỏng - sửa được"
    NORMAL = "normal", "Dùng BT"
    WRONG_ITEM = "wrong_item", "Sai hàng"


class JourneyResolution(models.TextChoices):
    NEW_MACHINE = "new_machine", "Gửi máy mới"
    REWARRANTY = "rewarranty", "Gửi lại máy BH"
    EXCHANGE = "exchange", "Đổi SP khác"
    REFUND = "refund", "Hoàn tiền"


class WarrantyJourney(models.Model):
    """Một hành trình của phiếu bảo hành (section 2–7). N hành trình / phiếu."""

    _MONEY = dict(max_digits=12, decimal_places=2, default=0)

    claim = models.ForeignKey(
        WarrantyClaim, on_delete=models.CASCADE, related_name="journeys"
    )
    seq = models.PositiveSmallIntegerField()

    # §2 Người nhận
    receiver_name = models.CharField(max_length=255, blank=True, default="")
    receiver_phone = models.CharField(max_length=30, blank=True, default="")
    receiver_raw_address = models.TextField(blank=True, default="")
    province_code = models.CharField(max_length=20, blank=True, default="")
    district_code = models.CharField(max_length=20, blank=True, default="")
    ward_code = models.CharField(max_length=20, blank=True, default="")

    # §3 SP & lý do
    product = models.ForeignKey(
        "products.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    send_reason = models.TextField(blank=True, default="")
    classification = models.CharField(
        max_length=20, choices=JourneyClassification.choices, blank=True, default=""
    )
    error_category = models.CharField(
        max_length=20, choices=JourneyErrorCategory.choices, blank=True, default=""
    )

    # §4 Vận chuyển & chi phí
    shipping_form = models.CharField(
        max_length=20, choices=JourneyShippingForm.choices, blank=True, default=""
    )
    declared_value = models.DecimalField(**_MONEY)
    cod_amount = models.DecimalField(**_MONEY)
    carrier = models.CharField(
        max_length=10, choices=JourneyCarrier.choices, blank=True, default=""
    )
    shipping_fee = models.DecimalField(**_MONEY)
    order_date = models.DateField(null=True, blank=True)
    new_tracking_code = models.CharField(max_length=100, blank=True, default="")
    recall_code = models.CharField(max_length=100, blank=True, default="")
    note = models.TextField(blank=True, default="")

    # Trạng thái
    status = models.CharField(
        max_length=12, choices=JourneyStatus.choices, default=JourneyStatus.NEW
    )

    # §5 Kho nhận hàng BH
    warehouse_received_at = models.DateField(null=True, blank=True)
    inspection_result = models.CharField(
        max_length=20, choices=JourneyInspectionResult.choices, blank=True, default=""
    )

    # §6 CSKH chốt phương án
    resolution = models.CharField(
        max_length=20, choices=JourneyResolution.choices, blank=True, default=""
    )
    resolution_declared_value = models.DecimalField(**_MONEY)
    resolution_cod = models.DecimalField(**_MONEY)
    resolution_note = models.TextField(blank=True, default="")

    # §7 Gửi đơn mới về khách
    return_carrier = models.CharField(
        max_length=10, choices=JourneyCarrier.choices, blank=True, default=""
    )
    return_shipping_fee = models.DecimalField(**_MONEY)
    return_order_date = models.DateField(null=True, blank=True)
    return_tracking_code = models.CharField(max_length=100, blank=True, default="")

    created_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "warranty_journeys"
        ordering = ["claim", "seq"]
        constraints = [
            models.UniqueConstraint(fields=["claim", "seq"], name="warranty_journey_claim_seq_uniq"),
        ]
        indexes = [
            models.Index(fields=["status"], name="warranty_journey_status_idx"),
            models.Index(fields=["new_tracking_code"], name="warranty_journey_newtc_idx"),
            models.Index(fields=["return_tracking_code"], name="warranty_journey_rettc_idx"),
        ]

    def __str__(self):
        return f"WarrantyJourney #{self.pk} (claim={self.claim_id} seq={self.seq})"
```

- [ ] **Step 4: Sinh migration cho model journey**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w /app desi-order-web-test:latest \
  python manage.py makemigrations warranty --name warranty_journey
```
Kỳ vọng: `0005_warranty_journey.py` chỉ gồm `CreateModel(WarrantyJourney)` (+ index/constraint). KHÔNG có thao tác nào trên WarrantyClaim.

- [ ] **Step 5: Chạy test — PASS**

Run: `bash .superpowers/sdd/run-be-tests.sh apps/warranty/tests/test_models.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/warranty/models.py backend/apps/warranty/migrations/ backend/apps/warranty/tests/test_models.py
git commit -m "feat: add WarrantyJourney model with section 2-7 fields"
```

---

### Task 3: Data migration (backfill journeys) + drop cột cũ

**Tiền đề:** Task 1 đã tạo `0004_add_claim_section1_fields`, Task 2 đã tạo `0005_warranty_journey`. Model `WarrantyClaim` HIỆN VẪN còn field cũ (chưa drop).

**Files:**
- Create: `backend/apps/warranty/migrations/0006_backfill_journeys.py` (data, hand-written)
- Modify: `backend/apps/warranty/models.py` (xóa field cũ khỏi WarrantyClaim)
- Create: `backend/apps/warranty/migrations/0007_drop_legacy_claim_columns.py` (auto)
- Test: `backend/apps/warranty/tests/test_migrations.py` (create)

**Interfaces:**
- Consumes: models từ Task 1–2 (WarrantyClaim còn field cũ, WarrantyJourney).
- Produces: mỗi phiếu cũ có journey seq=1; cột nghiệp vụ cũ đã drop khỏi DB; `makemigrations --check` sạch.

- [ ] **Step 1: Viết data migration `0006_backfill_journeys.py`** (đọc field cũ — chúng vẫn còn trên model)

```python
from django.db import migrations

_LEGACY_TO_NOTE = ("contact_channel", "social_nickname", "return_status")


def forwards(apps, schema_editor):
    WarrantyClaim = apps.get_model("warranty", "WarrantyClaim")
    WarrantyJourney = apps.get_model("warranty", "WarrantyJourney")

    # status cũ 1 Mới/2 Đang xử lý → 1 Đang xử lý; 3 Hoàn tất → 2; 4 Hủy → 3.
    status_map = {1: 1, 2: 1, 3: 2, 4: 3}

    for claim in WarrantyClaim.objects.all().iterator():
        if claim.journeys.exists():
            continue
        note_parts = []
        for f in _LEGACY_TO_NOTE:
            v = getattr(claim, f, "") or ""
            if v:
                note_parts.append(f"{f}: {v}")
        if getattr(claim, "require_carrier_call", False):
            note_parts.append("YC gọi xác nhận: có")
        # classification cũ là text tự do → không map enum, ghi vào note.
        legacy_classification = getattr(claim, "classification", "") or ""
        if legacy_classification:
            note_parts.append(f"phân loại cũ: {legacy_classification}")

        resolution_note_parts = []
        for f in ("accounting_check", "accounting_note"):
            v = getattr(claim, f, "") or ""
            if v:
                resolution_note_parts.append(f"{f}: {v}")

        WarrantyJourney.objects.create(
            claim=claim,
            seq=1,
            send_reason=getattr(claim, "send_reason", "") or "",
            recall_code=getattr(claim, "recall_code", "") or "",
            note="\n".join(note_parts),
            resolution_note="\n".join(resolution_note_parts),
            status="new",
            created_by=claim.created_by,
        )
        new_status = status_map.get(claim.status, 1)
        if claim.status != new_status:
            claim.status = new_status
            claim.save(update_fields=["status"])


def backwards(apps, schema_editor):
    # Không reverse hoàn hảo dữ liệu; chỉ xóa journey seq=1 sinh bởi migration.
    WarrantyJourney = apps.get_model("warranty", "WarrantyJourney")
    WarrantyJourney.objects.filter(seq=1).delete()


class Migration(migrations.Migration):
    dependencies = [("warranty", "0005_warranty_journey")]
    operations = [migrations.RunPython(forwards, backwards)]
```

> QUAN TRỌNG: data migration đọc field cũ (`send_reason`...) nên PHẢI chạy TRƯỚC khi drop (Step 3). Thứ tự: 0004 (add claim fields) → 0005 (journey table) → 0006 (backfill) → 0007 (drop legacy).

- [ ] **Step 2: Áp migration + xác nhận backfill (chưa drop)**

Chạy test suite hiện có để migrate sạch qua 0006:
```bash
bash .superpowers/sdd/run-be-tests.sh apps/warranty -q
```
Expected: PASS (0006 chạy không lỗi; DB rỗng nên backfill no-op nhưng migration hợp lệ).

- [ ] **Step 3: Xóa field cũ khỏi model + sinh migration drop**

Trong `models.py`, xóa 10 field cũ khỏi `WarrantyClaim` (`contact_channel`, `require_carrier_call`, `social_nickname`, `send_reason`, `send_method`, `classification`, `recall_code`, `return_status`, `accounting_check`, `accounting_note`). Giữ `order`, `customer_report_date`, `status`, `completed_at`, `created_by`, `created_at`, `updated_at`, `deleted_at`.

Sinh migration:
```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w /app desi-order-web-test:latest \
  python manage.py makemigrations warranty --name drop_legacy_claim_columns
```
Kỳ vọng: `0007_drop_legacy_claim_columns.py` gồm 10 `RemoveField` trên `warrantyclaim`. File phải phụ thuộc `0006_backfill_journeys` (Django tự set vì tuần tự).

- [ ] **Step 4: Viết test migration mapping**

`backend/apps/warranty/tests/test_migrations.py` — dùng `django_test_migrations` nếu có, hoặc test đơn giản qua `migrator`. Nếu package không có, kiểm chứng gián tiếp: test service ở Sprint 2 đã cover. Tối thiểu smoke test rằng migrate chạy sạch:

```python
import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_migrations_apply_clean():
    # Nếu tới đây DB đã migrate (fixture db), chỉ cần makemigrations --check không sinh mới.
    call_command("makemigrations", "warranty", check=True, dry_run=True)
```

- [ ] **Step 5: Chạy toàn bộ test warranty**

Run: `bash .superpowers/sdd/run-be-tests.sh apps/warranty -q`
Expected: PASS, migrate sạch qua 0004→0007, `makemigrations --check` không sinh mới.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/warranty/models.py backend/apps/warranty/migrations/ backend/apps/warranty/tests/test_migrations.py
git commit -m "feat: warranty journeys backfill + drop legacy claim columns"
```

---

## SPRINT 2 — API & Service

### Task 4: Repository journey + cập nhật claim repository

**Files:**
- Modify: `backend/apps/warranty/claim_repository.py`
- Create: `backend/apps/warranty/journey_repository.py`
- Test: `backend/apps/warranty/tests/test_repositories.py` (create)

**Interfaces:**
- Produces:
  - `WarrantyClaimRepository.base_qs()` — thêm `.prefetch_related("journeys", "journeys__product")`.
  - `WarrantyJourneyRepository.for_claim(claim_id) -> list[WarrantyJourney]`
  - `WarrantyJourneyRepository.get(journey_id) -> WarrantyJourney | None`
  - `WarrantyJourneyRepository.next_seq(claim_id) -> int`

- [ ] **Step 1: Test repository**

```python
import pytest
from apps.warranty.models import WarrantyClaim, WarrantyJourney
from apps.warranty.journey_repository import WarrantyJourneyRepository


@pytest.mark.django_db
def test_next_seq(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    assert WarrantyJourneyRepository.next_seq(claim.id) == 1
    WarrantyJourney.objects.create(claim=claim, seq=1)
    WarrantyJourney.objects.create(claim=claim, seq=2)
    assert WarrantyJourneyRepository.next_seq(claim.id) == 3
```

- [ ] **Step 2: Chạy — FAIL** (module chưa có)

- [ ] **Step 3: Viết `journey_repository.py`**

```python
from __future__ import annotations

from django.db.models import Max

from apps.warranty.models import WarrantyJourney


class WarrantyJourneyRepository:
    @staticmethod
    def base_qs():
        return WarrantyJourney.objects.select_related("product", "created_by")

    @classmethod
    def for_claim(cls, claim_id: int) -> list[WarrantyJourney]:
        return list(cls.base_qs().filter(claim_id=claim_id).order_by("seq"))

    @classmethod
    def get(cls, journey_id: int) -> WarrantyJourney | None:
        return cls.base_qs().filter(id=journey_id).first()

    @staticmethod
    def next_seq(claim_id: int) -> int:
        agg = WarrantyJourney.objects.filter(claim_id=claim_id).aggregate(m=Max("seq"))
        return (agg["m"] or 0) + 1
```

Cập nhật `claim_repository.py` `base_qs()`:

```python
    @staticmethod
    def base_qs():
        return (
            WarrantyClaim.objects.filter(deleted_at__isnull=True)
            .select_related("order", "created_by")
            .prefetch_related("journeys", "journeys__product")
        )
```

- [ ] **Step 4: Chạy — PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/apps/warranty/journey_repository.py backend/apps/warranty/claim_repository.py backend/apps/warranty/tests/test_repositories.py
git commit -m "feat: warranty journey repository + prefetch journeys"
```

---

### Task 5: Service — claim (create+journey, complete) & journey (add/update/auto-status/delete)

**Files:**
- Modify: `backend/apps/warranty/claim_service.py`
- Create: `backend/apps/warranty/journey_service.py`
- Test: `backend/apps/warranty/tests/test_services.py` (create; giữ test cũ nếu còn hợp lệ)

**Interfaces:**
- Consumes: repositories (Task 4), models.
- Produces:
  - `WarrantyClaimService.create_claim(order_id, data, created_by) -> WarrantyClaim` — tạo claim + journey seq=1 rỗng (atomic). `data` gồm `customer_report_date` optional.
  - `WarrantyClaimService.complete(claim) -> WarrantyClaim` — set status=COMPLETED + completed_at=now.
  - `WarrantyJourneyService.add_journey(claim) -> WarrantyJourney`
  - `WarrantyJourneyService.update_journey(journey, data: dict) -> WarrantyJourney` — set field whitelist + `apply_auto_status`.
  - `WarrantyJourneyService.delete_journey(journey) -> None`
  - `WarrantyJourneyService.apply_auto_status(journey) -> None` — new↔created theo new_tracking_code, không hạ cấp.
  - `WarrantyJourneyService.mark_packed_by_tracking(code) -> WarrantyJourney | None` (dùng ở Sprint 4).
  - Hằng `JOURNEY_EDITABLE_FIELDS: tuple[str, ...]`.

- [ ] **Step 1: Test service (auto-status, create-with-journey, complete)**

```python
import pytest
from django.utils import timezone

from apps.warranty.models import WarrantyClaim, WarrantyClaimStatus, JourneyStatus
from apps.warranty.claim_service import WarrantyClaimService
from apps.warranty.journey_service import WarrantyJourneyService


@pytest.mark.django_db
def test_create_claim_makes_first_journey(order_factory, django_user_model):
    order = order_factory()
    user = django_user_model.objects.create(username="u1")
    claim = WarrantyClaimService.create_claim(order.id, {"customer_report_date": None}, user)
    assert claim.journeys.count() == 1
    assert claim.journeys.first().seq == 1


@pytest.mark.django_db
def test_add_journey_increments_seq(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    WarrantyJourneyService.add_journey(claim)
    j2 = WarrantyJourneyService.add_journey(claim)
    assert j2.seq == 2


@pytest.mark.django_db
def test_auto_status_new_to_created(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    assert j.status == JourneyStatus.NEW
    WarrantyJourneyService.update_journey(j, {"new_tracking_code": "GHN123"})
    j.refresh_from_db()
    assert j.status == JourneyStatus.CREATED


@pytest.mark.django_db
def test_auto_status_no_downgrade(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    WarrantyJourneyService.update_journey(j, {"new_tracking_code": "X", "status": "shipping"})
    j.refresh_from_db()
    assert j.status == JourneyStatus.SHIPPING  # không bị auto kéo về created


@pytest.mark.django_db
def test_complete_sets_timestamp(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    WarrantyClaimService.complete(claim)
    claim.refresh_from_db()
    assert claim.status == WarrantyClaimStatus.COMPLETED
    assert claim.completed_at is not None
```

- [ ] **Step 2: Chạy — FAIL**

- [ ] **Step 3: Viết `journey_service.py`**

```python
from __future__ import annotations

from apps.warranty.journey_repository import WarrantyJourneyRepository
from apps.warranty.models import JourneyStatus, WarrantyClaim, WarrantyJourney

JOURNEY_EDITABLE_FIELDS = (
    "receiver_name", "receiver_phone", "receiver_raw_address",
    "province_code", "district_code", "ward_code",
    "product", "send_reason", "classification", "error_category",
    "shipping_form", "declared_value", "cod_amount", "carrier", "shipping_fee",
    "order_date", "new_tracking_code", "recall_code", "note", "status",
    "warehouse_received_at", "inspection_result",
    "resolution", "resolution_declared_value", "resolution_cod", "resolution_note",
    "return_carrier", "return_shipping_fee", "return_order_date", "return_tracking_code",
)

# Cấp bậc trạng thái để không auto hạ cấp.
_STATUS_RANK = {
    JourneyStatus.NEW: 0,
    JourneyStatus.CREATED: 1,
    JourneyStatus.PACKED: 2,
    JourneyStatus.SHIPPING: 3,
    JourneyStatus.LOST: 4,
    JourneyStatus.DELIVERED: 4,
}


class WarrantyJourneyService:
    @staticmethod
    def add_journey(claim: WarrantyClaim, created_by=None) -> WarrantyJourney:
        seq = WarrantyJourneyRepository.next_seq(claim.id)
        return WarrantyJourney.objects.create(claim=claim, seq=seq, created_by=created_by)

    @staticmethod
    def apply_auto_status(journey: WarrantyJourney) -> None:
        """new↔created theo new_tracking_code; không đụng nếu đã ở mức > created."""
        rank = _STATUS_RANK.get(journey.status, 0)
        if rank > _STATUS_RANK[JourneyStatus.CREATED]:
            return
        if journey.new_tracking_code.strip():
            journey.status = JourneyStatus.CREATED
        else:
            journey.status = JourneyStatus.NEW

    @classmethod
    def update_journey(cls, journey: WarrantyJourney, data: dict) -> WarrantyJourney:
        changed = []
        for key in JOURNEY_EDITABLE_FIELDS:
            if key in data:
                setattr(journey, key, data[key])
                changed.append(key)
        # Nếu client KHÔNG chủ động set status thì auto suy ra.
        if "status" not in data:
            before = journey.status
            cls.apply_auto_status(journey)
            if journey.status != before and "status" not in changed:
                changed.append("status")
        if changed:
            journey.save(update_fields=[*changed, "updated_at"])
        return journey

    @staticmethod
    def delete_journey(journey: WarrantyJourney) -> None:
        journey.delete()

    @classmethod
    def mark_packed_by_tracking(cls, code: str) -> WarrantyJourney | None:
        code = (code or "").strip()
        if not code:
            return None
        journey = (
            WarrantyJourney.objects.filter(new_tracking_code=code)
            .exclude(status__in=[JourneyStatus.PACKED, JourneyStatus.SHIPPING,
                                 JourneyStatus.LOST, JourneyStatus.DELIVERED])
            .order_by("-id")
            .first()
        )
        if journey is None:
            return None
        journey.status = JourneyStatus.PACKED
        journey.save(update_fields=["status", "updated_at"])
        return journey
```

Cập nhật `claim_service.py`: rút gọn `_EDITABLE_FIELDS` claim còn `("customer_report_date", "status")`; `create_claim` tạo journey seq=1; thêm `complete`; giữ `soft_delete`; sửa `ingest_claim_by_shipping_code` để tạo journey seq=1 và map field ingest vào journey (xem Task 8). Ví dụ:

```python
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.orders.models import Order
from apps.warranty.models import WarrantyClaim, WarrantyClaimStatus
from apps.warranty.journey_service import WarrantyJourneyService

_CLAIM_EDITABLE_FIELDS = ("customer_report_date", "status")


class WarrantyClaimService:
    @staticmethod
    def create_claim(order_id: int, data: dict, created_by) -> WarrantyClaim:
        order = Order.objects.filter(id=order_id, deleted_at__isnull=True).first()
        if order is None:
            raise ValueError("Không tìm thấy đơn hàng.")
        if WarrantyClaim.objects.filter(order=order, deleted_at__isnull=True).exists():
            raise WarrantyClaimExists()
        fields = {k: data[k] for k in _CLAIM_EDITABLE_FIELDS if k in data}
        try:
            with transaction.atomic():
                claim = WarrantyClaim.objects.create(order=order, created_by=created_by, **fields)
                WarrantyJourneyService.add_journey(claim, created_by=created_by)
                return claim
        except IntegrityError as exc:
            raise WarrantyClaimExists() from exc

    @staticmethod
    def update_claim(claim: WarrantyClaim, data: dict) -> WarrantyClaim:
        changed = []
        for key in _CLAIM_EDITABLE_FIELDS:
            if key in data:
                setattr(claim, key, data[key])
                changed.append(key)
        if changed:
            claim.save(update_fields=[*changed, "updated_at"])
        return claim

    @staticmethod
    def complete(claim: WarrantyClaim) -> WarrantyClaim:
        claim.status = WarrantyClaimStatus.COMPLETED
        claim.completed_at = timezone.now()
        claim.save(update_fields=["status", "completed_at", "updated_at"])
        return claim

    @staticmethod
    def soft_delete(claim: WarrantyClaim) -> None:
        claim.deleted_at = timezone.now()
        claim.save(update_fields=["deleted_at", "updated_at"])
```

(Giữ các exception `WarrantyClaimExists`, `WarrantyOrderNotFound`, `WarrantyOrderAmbiguous`.)

- [ ] **Step 4: Chạy — PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/apps/warranty/journey_service.py backend/apps/warranty/claim_service.py backend/apps/warranty/tests/test_services.py
git commit -m "feat: warranty claim/journey services with auto-status and complete"
```

---

### Task 6: Serializers

**Files:**
- Modify: `backend/apps/warranty/serializers.py`
- Test: `backend/apps/warranty/tests/test_serializers.py` (create)

**Interfaces:**
- Produces:
  - `WarrantyJourneySerializer` (read; nhúng `product_name`, `status_label`).
  - `WarrantyJourneyWriteSerializer` (write; mọi field optional/partial; validate enum).
  - `WarrantyClaimSerializer` — thêm `customer_report_date`, `completed_at`, `journeys` (nested read), `status_label`; giữ `order` nhúng.
  - `WarrantyClaimWriteSerializer` — `order_id` (write-only optional), `customer_report_date`, `status`.

- [ ] **Step 1: Test serializer round-trip**

```python
import pytest
from apps.warranty.models import WarrantyClaim, WarrantyJourney
from apps.warranty.serializers import WarrantyClaimSerializer, WarrantyJourneyWriteSerializer


@pytest.mark.django_db
def test_claim_serializer_embeds_journeys(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    WarrantyJourney.objects.create(claim=claim, seq=1, receiver_name="Nam")
    data = WarrantyClaimSerializer(claim).data
    assert data["journeys"][0]["receiver_name"] == "Nam"
    assert "status_label" in data


def test_journey_write_validates_enum():
    s = WarrantyJourneyWriteSerializer(data={"classification": "bogus"})
    assert not s.is_valid()
    assert "classification" in s.errors
```

- [ ] **Step 2: Chạy — FAIL**

- [ ] **Step 3: Viết serializers**

Trong `serializers.py` (giữ `WarrantyRegistrationSerializer`, `WarrantyClaimOrderSerializer`, `WarrantyClaimIngestSerializer` — cập nhật ingest ở Task 8). Thêm:

```python
from apps.warranty.models import (
    WarrantyClaim, WarrantyJourney, WarrantyClaimStatus,
)


class WarrantyJourneySerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True, default="")
    product_sku = serializers.CharField(source="product.sku", read_only=True, default="")
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = WarrantyJourney
        fields = (
            "id", "seq", "status", "status_label",
            "receiver_name", "receiver_phone", "receiver_raw_address",
            "province_code", "district_code", "ward_code",
            "product", "product_name", "product_sku",
            "send_reason", "classification", "error_category",
            "shipping_form", "declared_value", "cod_amount", "carrier", "shipping_fee",
            "order_date", "new_tracking_code", "recall_code", "note",
            "warehouse_received_at", "inspection_result",
            "resolution", "resolution_declared_value", "resolution_cod", "resolution_note",
            "return_carrier", "return_shipping_fee", "return_order_date", "return_tracking_code",
            "created_at", "updated_at",
        )
        read_only_fields = fields


class WarrantyJourneyWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = WarrantyJourney
        fields = (
            "status",
            "receiver_name", "receiver_phone", "receiver_raw_address",
            "province_code", "district_code", "ward_code",
            "product", "send_reason", "classification", "error_category",
            "shipping_form", "declared_value", "cod_amount", "carrier", "shipping_fee",
            "order_date", "new_tracking_code", "recall_code", "note",
            "warehouse_received_at", "inspection_result",
            "resolution", "resolution_declared_value", "resolution_cod", "resolution_note",
            "return_carrier", "return_shipping_fee", "return_order_date", "return_tracking_code",
        )
        extra_kwargs = {f: {"required": False} for f in fields}
```

Sửa `WarrantyClaimSerializer`:

```python
class WarrantyClaimSerializer(serializers.ModelSerializer):
    order = WarrantyClaimOrderSerializer(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    created_by_name = serializers.SerializerMethodField()
    journeys = WarrantyJourneySerializer(many=True, read_only=True)

    class Meta:
        model = WarrantyClaim
        fields = (
            "id", "order", "customer_report_date", "status", "status_label",
            "completed_at", "journeys", "created_by_name", "created_at", "updated_at",
        )
        read_only_fields = fields

    def get_created_by_name(self, obj) -> str:
        user = obj.created_by
        return (user.display_name or user.username) if user else ""


class WarrantyClaimWriteSerializer(serializers.ModelSerializer):
    order_id = serializers.IntegerField(write_only=True, required=False)

    class Meta:
        model = WarrantyClaim
        fields = ("order_id", "customer_report_date", "status")
        extra_kwargs = {"customer_report_date": {"required": False}, "status": {"required": False}}

    def validate_status(self, value):
        if value not in WarrantyClaimStatus.values:
            raise serializers.ValidationError("Trạng thái không hợp lệ.")
        return value
```

Bổ sung `WarrantyClaimOrderSerializer` thêm field `invoice_code`, `created_at` (cho §1b/§1c) và kênh KV nếu chưa có — kiểm tra field kênh KiotViet của Order (`kiotviet_channel_id` hoặc tên kênh) bằng `codegraph_explore "Order kiotviet channel field name"` rồi thêm vào `fields`.

- [ ] **Step 4: Chạy — PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/apps/warranty/serializers.py backend/apps/warranty/tests/test_serializers.py
git commit -m "feat: warranty claim/journey serializers with nested journeys"
```

---

### Task 7: Views + URLs (claim create/list/detail/complete, journey CRUD)

**Files:**
- Modify: `backend/apps/warranty/claim_views.py`
- Create: `backend/apps/warranty/journey_views.py`
- Modify: `backend/apps/warranty/urls.py`
- Test: `backend/apps/warranty/tests/test_warranty_claim_api.py` (mở rộng)

**Interfaces:**
- Consumes: services + serializers.
- Produces endpoints (bảng §4.1 spec). `apiClient` authenticated + section `warranty`.

- [ ] **Step 1: Test endpoint (create→journey, complete, journey CRUD, RBAC)**

```python
import pytest
from rest_framework.test import APIClient


@pytest.fixture
def auth_client(warranty_user):  # fixture cấp user có section warranty (xem test cũ)
    c = APIClient()
    c.force_authenticate(warranty_user)
    return c


@pytest.mark.django_db
def test_create_claim_returns_journey(auth_client, order_factory):
    order = order_factory()
    r = auth_client.post("/api/warranty/claims", {"order_id": order.id}, format="json")
    assert r.status_code == 201
    assert len(r.data["journeys"]) == 1


@pytest.mark.django_db
def test_complete_endpoint(auth_client, order_factory):
    order = order_factory()
    cid = auth_client.post("/api/warranty/claims", {"order_id": order.id}, format="json").data["id"]
    r = auth_client.post(f"/api/warranty/claims/{cid}/complete")
    assert r.status_code == 200
    assert r.data["status"] == 2
    assert r.data["completed_at"]


@pytest.mark.django_db
def test_add_and_patch_journey(auth_client, order_factory):
    order = order_factory()
    claim = auth_client.post("/api/warranty/claims", {"order_id": order.id}, format="json").data
    r = auth_client.post(f"/api/warranty/claims/{claim['id']}/journeys")
    assert r.status_code == 201
    jid = r.data["id"]
    p = auth_client.patch(f"/api/warranty/journeys/{jid}",
                          {"new_tracking_code": "GHN9"}, format="json")
    assert p.status_code == 200
    assert p.data["status"] == "created"
```

> Fixture `warranty_user`: theo mẫu `test_warranty_claim_api.py` cũ (cấp role/section). Tái dùng, đừng tạo mới nếu đã có.

- [ ] **Step 2: Chạy — FAIL**

- [ ] **Step 3: Cập nhật `claim_views.py`** — POST create dùng service (đã tạo journey), thêm view `complete_warranty_claim`. GET list/detail dùng `WarrantyClaimSerializer` (đã nhúng journeys). Thêm:

```python
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("warranty")])
def complete_warranty_claim(request, claim_id: int):
    claim = WarrantyClaimRepository.get_claim(claim_id)
    if claim is None:
        return Response({"detail": "Không tìm thấy phiếu bảo hành."}, status=status.HTTP_404_NOT_FOUND)
    claim = WarrantyClaimService.complete(claim)
    return Response(WarrantyClaimSerializer(claim).data)
```

(Trong `list_create_warranty_claims` POST: bỏ nhánh `_EDITABLE_FIELDS` cũ; truyền `write.validated_data` cho `create_claim`; đổi PATCH detail dùng `_CLAIM_EDITABLE_FIELDS` — chỉ `customer_report_date`/`status`.)

- [ ] **Step 4: Viết `journey_views.py`**

```python
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import require_section

from .claim_repository import WarrantyClaimRepository
from .journey_repository import WarrantyJourneyRepository
from .journey_service import WarrantyJourneyService
from .serializers import WarrantyJourneySerializer, WarrantyJourneyWriteSerializer


@extend_schema(
    responses={201: WarrantyJourneySerializer, 404: OpenApiResponse(description="Không tìm thấy phiếu")}
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("warranty")])
def add_journey(request, claim_id: int):
    claim = WarrantyClaimRepository.get_claim(claim_id)
    if claim is None:
        return Response({"detail": "Không tìm thấy phiếu."}, status=status.HTTP_404_NOT_FOUND)
    journey = WarrantyJourneyService.add_journey(
        claim, created_by=request.user if request.user.is_authenticated else None
    )
    return Response(WarrantyJourneySerializer(journey).data, status=status.HTTP_201_CREATED)


@extend_schema(
    request=WarrantyJourneyWriteSerializer,
    responses={200: WarrantyJourneySerializer, 204: OpenApiResponse(description="Đã xóa"),
               404: OpenApiResponse(description="Không tìm thấy hành trình")},
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_section("warranty")])
def update_delete_journey(request, journey_id: int):
    journey = WarrantyJourneyRepository.get(journey_id)
    if journey is None:
        return Response({"detail": "Không tìm thấy hành trình."}, status=status.HTTP_404_NOT_FOUND)
    if request.method == "DELETE":
        WarrantyJourneyService.delete_journey(journey)
        return Response(status=status.HTTP_204_NO_CONTENT)
    write = WarrantyJourneyWriteSerializer(data=request.data, partial=True)
    write.is_valid(raise_exception=True)
    journey = WarrantyJourneyService.update_journey(journey, dict(write.validated_data))
    return Response(WarrantyJourneySerializer(journey).data)
```

- [ ] **Step 5: Cập nhật `urls.py`**

```python
from .claim_views import (
    complete_warranty_claim,
    detail_update_delete_warranty_claim,
    list_create_warranty_claims,
)
from .journey_views import add_journey, update_delete_journey
# ...
urlpatterns = [
    path("", create_warranty_registration, name="warranty-create"),
    path("ingest", warranty_claim_ingest, name="warranty-ingest"),
    path("claims", list_create_warranty_claims, name="warranty-claims"),
    path("claims/<int:claim_id>", detail_update_delete_warranty_claim, name="warranty-claim-detail"),
    path("claims/<int:claim_id>/complete", complete_warranty_claim, name="warranty-claim-complete"),
    path("claims/<int:claim_id>/journeys", add_journey, name="warranty-claim-journeys"),
    path("journeys/<int:journey_id>", update_delete_journey, name="warranty-journey-detail"),
]
```

- [ ] **Step 6: Chạy toàn bộ test warranty — PASS**

Run: `... python -m pytest apps/warranty -q --create-db`

- [ ] **Step 7: Commit**

```bash
git add backend/apps/warranty/claim_views.py backend/apps/warranty/journey_views.py backend/apps/warranty/urls.py backend/apps/warranty/tests/test_warranty_claim_api.py
git commit -m "feat: warranty journey endpoints + claim complete"
```

---

### Task 8: Ingest adapt + OpenAPI schema regen

**Files:**
- Modify: `backend/apps/warranty/ingest_views.py`, `backend/apps/warranty/serializers.py` (ingest serializer)
- Test: mở rộng test ingest hiện có

**Interfaces:**
- Produces: ingest tạo claim + journey seq=1 mang field ingest (map các field journey như `send_reason`, `recall_code`, `receiver_*`...).

- [ ] **Step 1: Sửa `ingest_claim_by_shipping_code` trong `claim_service.py`** để tạo journey seq=1 và đẩy `data` (đã pop shipping_code) vào journey qua `WarrantyJourneyService.update_journey`. Chỉ nhận các key thuộc `JOURNEY_EDITABLE_FIELDS`.

```python
    @staticmethod
    def ingest_claim_by_shipping_code(shipping_code: str, data: dict) -> WarrantyClaim:
        qs = Order.objects.filter(shipping_code=shipping_code, deleted_at__isnull=True)
        count = qs.count()
        if count == 0:
            raise WarrantyOrderNotFound()
        if count > 1:
            raise WarrantyOrderAmbiguous()
        order = qs.first()
        if WarrantyClaim.objects.filter(order=order, deleted_at__isnull=True).exists():
            raise WarrantyClaimExists()
        try:
            with transaction.atomic():
                claim = WarrantyClaim.objects.create(order=order, created_by=None)
                journey = WarrantyJourneyService.add_journey(claim)
                WarrantyJourneyService.update_journey(journey, data)
                return claim
        except IntegrityError as exc:
            raise WarrantyClaimExists() from exc
```

Cập nhật `WarrantyClaimIngestSerializer.Meta.fields` cho khớp field journey mới (bỏ field cũ đã drop). Giữ `shipping_code` write-only + validate.

- [ ] **Step 2: Chạy test ingest — PASS** (giữ 6 case cũ: 200/400×3/409/401)

- [ ] **Step 3: Regen OpenAPI schema**

Chạy lệnh generate schema của dự án (kiểm tra `package.json` script `gen:api` hoặc `manage.py spectacular`). Ví dụ backend:

```bash
docker run --rm -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  python manage.py spectacular --file /app/schema.yml
```

Sau đó ở `frontend/`: `npm run gen:api` để cập nhật `src/api/schema.d.ts`.

- [ ] **Step 4: Commit**

```bash
git add backend/apps/warranty/ frontend/src/api/schema.d.ts
git commit -m "feat: adapt warranty ingest to journeys + regen openapi schema"
```

---

## SPRINT 3 — Frontend

### Task 9: Typed API modules + enum option constants

**Files:**
- Modify: `frontend/src/lib/api/warrantyClaims.ts`
- Create: `frontend/src/lib/api/warrantyJourneys.ts`
- Create: `frontend/src/features/warranty-claims/journey-enums.ts`
- Modify: `frontend/src/features/warranty-claims/warranty-claim-types.ts`

**Interfaces:**
- Produces:
  - `completeWarrantyClaim(id) -> Promise<WarrantyClaim>`
  - `addJourney(claimId) -> Promise<WarrantyJourney>`, `updateJourney(id, patch) -> Promise<WarrantyJourney>`, `deleteJourney(id) -> Promise<void>`
  - Types `WarrantyJourney`, `WarrantyJourneyPayload` (derive từ `components["schemas"]`), `WarrantyClaim` (đã có `journeys`).
  - Option arrays: `CLASSIFICATION_OPTIONS`, `ERROR_CATEGORY_OPTIONS`, `SHIPPING_FORM_OPTIONS`, `CARRIER_OPTIONS`, `INSPECTION_OPTIONS`, `RESOLUTION_OPTIONS`, `JOURNEY_STATUS_OPTIONS`, `CLAIM_STATUS_OPTIONS`.

- [ ] **Step 1: Cập nhật types** trong `warranty-claim-types.ts`:

```typescript
export type WarrantyJourney = components["schemas"]["WarrantyJourney"];
export type WarrantyJourneyPayload = Partial<
  Omit<WarrantyJourney, "id" | "seq" | "status_label" | "product_name" | "product_sku" | "created_at" | "updated_at">
> & { status?: WarrantyJourney["status"] };
// WarrantyClaim đã tự có `journeys`, `customer_report_date`, `completed_at` sau regen.
```

- [ ] **Step 2: Tạo `journey-enums.ts`** — mảng `{value,label}` khớp enum backend (dán nhãn từ spec §2.2). Ví dụ:

```typescript
export const CLASSIFICATION_OPTIONS = [
  { value: "same_product", label: "Cùng SP" },
  { value: "different_product", label: "Khác SP" },
  { value: "accessory", label: "Phụ kiện" },
] as const;

export const CARRIER_OPTIONS = [
  { value: "ghn", label: "GHN" },
  { value: "best", label: "BEST" },
  { value: "vtp", label: "VTP" },
] as const;
// ... ERROR_CATEGORY_OPTIONS, SHIPPING_FORM_OPTIONS, INSPECTION_OPTIONS,
//     RESOLUTION_OPTIONS, JOURNEY_STATUS_OPTIONS, CLAIM_STATUS_OPTIONS tương tự.
```

- [ ] **Step 3: Tạo `warrantyJourneys.ts`**

```typescript
import { apiDelete, apiPatch, apiPost } from "@/api/typed";
import type { WarrantyJourney, WarrantyJourneyPayload } from "@/features/warranty-claims/warranty-claim-types";

export async function addJourney(claimId: number): Promise<WarrantyJourney> {
  return apiPost(`/api/warranty/claims/${claimId}/journeys` as "/api/warranty/claims/{claim_id}/journeys", {});
}
export async function updateJourney(id: number, patch: WarrantyJourneyPayload): Promise<WarrantyJourney> {
  return apiPatch(`/api/warranty/journeys/${id}` as "/api/warranty/journeys/{journey_id}", patch);
}
export async function deleteJourney(id: number): Promise<void> {
  await apiDelete(`/api/warranty/journeys/${id}` as "/api/warranty/journeys/{journey_id}");
}
```

Thêm `completeWarrantyClaim` vào `warrantyClaims.ts`:

```typescript
export async function completeWarrantyClaim(id: number): Promise<WarrantyClaim> {
  return apiPost(`${BASE}/${id}/complete` as "/api/warranty/claims/{claim_id}/complete", {});
}
```

> Nếu type path literal không khớp schema sinh ra, mở `src/api/schema.d.ts` tra path key chính xác.
> LƯU Ý: `@/api/typed` hiện chỉ export `apiGet/apiPatch/apiDelete` (module cũ dùng `fetchJson` cho POST vì view GET+POST share schema). Kiểm tra `apiPost` có được export không — nếu KHÔNG, thêm `apiPost` vào `@/api/typed` theo mẫu `apiPatch`, hoặc dùng `fetchJson(path, { method: "POST", body: "{}" })` cho `addJourney`/`completeWarrantyClaim` (2 endpoint này có schema riêng, không share nên type trả về đúng).

- [ ] **Step 4: `npm run typecheck`** — PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api/warrantyClaims.ts frontend/src/lib/api/warrantyJourneys.ts frontend/src/features/warranty-claims/journey-enums.ts frontend/src/features/warranty-claims/warranty-claim-types.ts
git commit -m "feat: typed warranty journey api + enum options"
```

---

### Task 10: React Query hooks cho journey + complete

**Files:**
- Modify: `frontend/src/features/warranty-claims/queries.ts`

**Interfaces:**
- Produces: `useCompleteClaim()`, `useAddJourney()`, `useUpdateJourney()`, `useDeleteJourney()` — đều invalidate `warrantyClaimKeys.all` (và detail).

- [ ] **Step 1: Thêm hooks**

```typescript
import { addJourney, deleteJourney, updateJourney } from "@/lib/api/warrantyJourneys";
import { completeWarrantyClaim } from "@/lib/api/warrantyClaims";
import type { WarrantyJourneyPayload } from "@/features/warranty-claims/warranty-claim-types";

export function useCompleteClaim() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => completeWarrantyClaim(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: warrantyClaimKeys.all }),
  });
}
export function useAddJourney() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (claimId: number) => addJourney(claimId),
    onSuccess: () => qc.invalidateQueries({ queryKey: warrantyClaimKeys.all }),
  });
}
export function useUpdateJourney() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: WarrantyJourneyPayload }) => updateJourney(id, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: warrantyClaimKeys.all }),
  });
}
export function useDeleteJourney() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => deleteJourney(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: warrantyClaimKeys.all }),
  });
}
```

- [ ] **Step 2: `npm run typecheck`** — PASS. **Commit**

```bash
git add frontend/src/features/warranty-claims/queries.ts
git commit -m "feat: warranty journey query hooks"
```

---

### Task 11: Component địa chỉ hành trình (cascade tỉnh/huyện/xã)

**Files:**
- Create: `frontend/src/features/warranty-claims/components/JourneyAddressFields.tsx`

**Interfaces:**
- Consumes: address catalog hooks/API của orders (theo mẫu `OrderFormDialog`). Tra chính xác bằng `codegraph_explore "order address catalog hook useAddressCatalog province district ward select"` trước khi viết.
- Produces: `<JourneyAddressFields value={{province_code,district_code,ward_code,receiver_raw_address}} onChange={...} />` dùng `components/ui/select` (KHÔNG react-select).

- [ ] **Step 1: Viết component** theo mẫu cascade của Order (3 select phụ thuộc + textarea raw). Reuse loader tỉnh/huyện/xã hiện có; không tự fetch trong useEffect nếu đã có hook/React Query.

- [ ] **Step 2: `npm run typecheck`** — PASS. **Commit**

```bash
git add frontend/src/features/warranty-claims/components/JourneyAddressFields.tsx
git commit -m "feat: reusable journey address fields (province/district/ward cascade)"
```

---

### Task 12: `JourneyCard` — form 1 hành trình (§2–§7) auto-PATCH

**Files:**
- Create: `frontend/src/features/warranty-claims/components/JourneyCard.tsx`
- Test: `frontend/src/features/warranty-claims/components/JourneyCard.test.tsx`

**Interfaces:**
- Consumes: `useUpdateJourney`, `useDeleteJourney`, enum options (Task 9), `JourneyAddressFields`, product-select async.
- Produces: `<JourneyCard journey={WarrantyJourney} onChanged={() => void} />` — accordion item hiển thị 6 nhóm; mỗi field PATCH khi blur/change (debounce nhẹ hoặc onBlur như pattern draft hiện có).

- [ ] **Step 1: Viết test render + patch**

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
// ... mock useUpdateJourney trả mutate spy
test("nhập mã vận đơn mới gọi patch", () => {
  // render JourneyCard với journey seq=1 status new
  // fireEvent.change ô "Mã vận đơn mới" + blur → expect mutate called with {new_tracking_code}
});
```

(Viết cụ thể theo helper test hiện có của repo — xem `WarrantyClaimsTable` không có test; dùng mẫu test FE khác trong `src/features/**/*.test.tsx`.)

- [ ] **Step 2: Viết `JourneyCard.tsx`** — dùng `Accordion`/`Collapsible` từ `components/ui`. Bố cục 6 nhóm với heading emoji theo spec. Pattern field:

```tsx
// helper PATCH 1 field
const patch = (field: keyof WarrantyJourneyPayload, value: unknown) =>
  update.mutate({ id: journey.id, patch: { [field]: value } as WarrantyJourneyPayload },
    { onError: (e) => toast.error(e instanceof Error ? e.message : "Lưu thất bại") });

// §2 người nhận
<Input defaultValue={journey.receiver_name} onBlur={(e) => patch("receiver_name", e.target.value)} />
// §3 phân loại (select enum)
<Select value={journey.classification ?? ""} onValueChange={(v) => patch("classification", v)}>
  <SelectTrigger>...</SelectTrigger>
  <SelectContent>{CLASSIFICATION_OPTIONS.map(o => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
</Select>
// §4 mã vận đơn mới → auto-status; tiền dùng input numeric format như OrderFormDialog
```

**Danh sách field bắt buộc render (đủ, không bỏ sót):**
- §2: receiver_name, receiver_phone, `JourneyAddressFields` (province/district/ward + receiver_raw_address).
- §3: product (async product-select), send_reason (textarea), classification (select), error_category (select).
- §4: shipping_form (select), declared_value, cod_amount, carrier (select), shipping_fee, order_date (date), new_tracking_code, recall_code, note (textarea). Badge trạng thái (status) — select cho shipping/lost/delivered; new/created hiển thị readonly (auto).
- §5: warehouse_received_at (date), inspection_result (select).
- §6: resolution (select), resolution_declared_value, resolution_cod, resolution_note (textarea).
- §7: return_carrier (select), return_shipping_fee, return_order_date (date), return_tracking_code.
- Nút xóa hành trình (confirm) → `useDeleteJourney`.

- [ ] **Step 3: `npm run typecheck && npm run test`** — PASS. **Commit**

```bash
git add frontend/src/features/warranty-claims/components/JourneyCard.tsx frontend/src/features/warranty-claims/components/JourneyCard.test.tsx
git commit -m "feat: JourneyCard form for a single warranty journey"
```

---

### Task 13: Rewrite trang chi tiết + form tạo phiếu + danh sách

**Files:**
- Modify: `frontend/src/pages/WarrantyClaimDetailPage.tsx`
- Modify: `frontend/src/features/warranty-claims/components/WarrantyClaimFormDialog.tsx` (create form: chỉ order + customer_report_date)
- Modify: `frontend/src/features/warranty-claims/warranty-claim-columns.ts` + `WarrantyClaimsTable.tsx` (cột trạng thái = trạng thái phiếu; bỏ inline-edit field cũ đã drop)

**Interfaces:**
- Consumes: `useWarrantyClaim`, `useAddJourney`, `useCompleteClaim`, `JourneyCard`.

- [ ] **Step 1: Rewrite `WarrantyClaimDetailPage.tsx`** — header §1:
  - Ngày khách báo lỗi: `<Input type="date">` PATCH claim (`useUpdateWarrantyClaim`).
  - §1b Mã đơn gốc = `claim.order.invoice_code` (readonly), §1c Ngày gửi = format `claim.order.created_at`, §1d Kênh KV = field kênh của order (readonly).
  - Trạng thái phiếu badge + nút **Hoàn thành** (`useCompleteClaim`, confirm; disable nếu đã completed).
  - Danh sách journeys: map `claim.journeys` → `<JourneyCard>` trong Accordion; nút **Thêm hành trình** (`useAddJourney`).
  - Bỏ `WarrantyClaimFormDialog` edit-all cũ khỏi trang chi tiết (giữ nút Xóa phiếu).

- [ ] **Step 2: Sửa `WarrantyClaimFormDialog`** — form tạo chỉ gồm `OrderPicker` (order_id) + `customer_report_date`. Bỏ toàn bộ field nghiệp vụ cũ.

- [ ] **Step 3: Sửa `warranty-claim-columns.ts` + `WarrantyClaimsTable.tsx`** — cột hiển thị trạng thái phiếu (Đang xử lý/Hoàn thành/Hủy), số hành trình (`journeys.length`), khách/SĐT/mã VĐ từ order. Bỏ các cột/inline-edit field đã drop. `STATUS_BADGE_CLASS` cập nhật cho 3 trạng thái.

- [ ] **Step 4: `npm run typecheck && npm run test && npm run lint`** — PASS (không thêm file mới vào allowlist react-select).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/WarrantyClaimDetailPage.tsx frontend/src/features/warranty-claims/
git commit -m "feat: warranty detail page with journeys accordion + complete"
```

---

### Task 14: Verify end-to-end (chạy app, kiểm flow thật)

**Files:** none (thao tác kiểm thử)

- [ ] **Step 1:** Dùng skill `verify`/`run` để chạy app; tạo 1 phiếu từ 1 order → thấy journey seq=1; sửa §2–§7; nhập mã vận đơn mới → trạng thái tự chuyển "Đã lên đơn"; thêm hành trình #2; bấm Hoàn thành → phiếu chuyển Hoàn thành. Ghi lại quan sát.
- [ ] **Step 2:** Commit ghi chú tiến độ (nếu cập nhật doc).

---

## SPRINT 4 — Tích hợp quét kho ("Đã đóng hàng")

### Task 15: Nối matcher quét kho → set journey `packed`

**Files:**
- Modify: điểm matcher trong `backend/apps/inventory/` (tra bằng `codegraph_explore "pending shipment scan matcher cron match order by shipping_code set dispatched"`).
- Test: `backend/apps/warranty/tests/test_scan_integration.py` (create)

**Interfaces:**
- Consumes: `WarrantyJourneyService.mark_packed_by_tracking(code)`.
- Produces: khi kho quét mã trùng `new_tracking_code` của một journey chưa `packed`+ → set `packed`, ghi audit.

- [ ] **Step 1: Test tích hợp**

```python
import pytest
from apps.warranty.models import WarrantyClaim, WarrantyJourney, JourneyStatus
from apps.warranty.journey_service import WarrantyJourneyService


@pytest.mark.django_db
def test_scan_marks_journey_packed(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourney.objects.create(claim=claim, seq=1, new_tracking_code="GHN777", status="created")
    result = WarrantyJourneyService.mark_packed_by_tracking("GHN777")
    assert result is not None
    j.refresh_from_db()
    assert j.status == JourneyStatus.PACKED


@pytest.mark.django_db
def test_scan_unknown_code_noop():
    assert WarrantyJourneyService.mark_packed_by_tracking("NOPE") is None
```

- [ ] **Step 2: Chạy — PASS** (service `mark_packed_by_tracking` đã viết ở Task 5).

- [ ] **Step 3: Wire vào matcher** — tại điểm xử lý một mã quét kho không khớp Order (hoặc song song), gọi `WarrantyJourneyService.mark_packed_by_tracking(code)`; nếu trả về journey → coi là khớp warranty (ghi audit, không tạo pending "unmatched"). Giữ thay đổi tối thiểu, không phá luồng Order hiện có.

- [ ] **Step 4: Chạy test inventory + warranty liên quan — PASS**

Run: `... python -m pytest apps/warranty apps/inventory -q --create-db`

- [ ] **Step 5: Commit**

```bash
git add backend/apps/warranty/tests/test_scan_integration.py backend/apps/inventory/
git commit -m "feat: warehouse scan marks warranty journey as packed"
```

---

## Ghi chú tiến độ (cập nhật sau mỗi sprint)

- Sprint 1: _(đã làm / chưa làm)_
- Sprint 2: _(…)_
- Sprint 3: _(…)_
- Sprint 4: _(…)_

## Ngoài phạm vi (v1)
- Webhook hãng vận chuyển tự set `shipping`/`lost` (v1 chọn tay).
- Báo cáo/analytics bảo hành theo hành trình.
- Notification tự động cho khách.
