# Warranty Journey Best Tracking Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trạng thái VC hành trình BH tự động theo mã Best (11 nhóm hiển thị, cron trace 10'), gỡ chọn tay, mở rộng quét kho khớp cả 3 mã.

**Architecture:** Mapping thuần mã Best → nhóm (riêng cho BH); 4 field mới trên `WarrantyJourney` lưu mã trạng thái thô + synced_at; cron celery 10' trace qua `BestExpressClient.trace_query`; serializer expose `vc_groups`; quét xuất khớp 3 mã (giữ hook packed), quét hoàn khớp 3 mã → returned + ngày kho nhận; FE badge thay select.

**Tech Stack:** Django/DRF + celery (BE), React+TS (FE), pytest + vitest.

**Spec:** `docs/superpowers/specs/2026-07-18-warranty-journey-best-tracking-status-design.md`

## Global Constraints

- Worktree `c:\Users\MYPC\Desktop\workspace\desi-order\.claude\worktrees\warranty-claim-statuses`, branch `feat/warranty-claim-status-flow` (chung MR với bộ trạng thái phiếu). Path dưới đây tương đối worktree root.
- KHÔNG sửa `apps/best_express` (enum/map/webhook/client) — chỉ GỌI client.
- `JourneyStatus`/`return_status` cũ: GIỮ cột + luồng quét, chỉ gỡ khỏi editable fields.
- Bảng nhóm (key → nhãn → mã): created=Tạo mới (không mã/không data), picking_up=Đang lấy hàng (211), pickup_failed=Lấy hàng không thành công (203), in_transit=Đang trung chuyển (202,301,302,303,304,309), delivering=Đang giao hàng (601), redelivery=Chờ giao lại (604), delivered=GTC (666), returning=Đang chuyển hoàn (605,701,702,703,704,705,707), returned=Đã trả hàng (708), lost=Thất lạc (777), damaged=Hư hỏng (1000).
- Lệnh test BE (PowerShell — image `desi-order-web:wp7`, DB riêng, `--create-db` sau migration mới):
  ```powershell
  docker run --rm --network desi-order_default `
    -e POSTGRES_DB=desi_wjtrack -e POSTGRES_HOST=desi-order-postgres-1 `
    -e REDIS_HOST=desi-order-redis-1 -e REDIS_PASSWORD=testpassword `
    -v "C:\Users\MYPC\Desktop\workspace\desi-order\.claude\worktrees\warranty-claim-statuses\backend:/app" `
    -w /app desi-order-web:wp7 pytest <paths> -q -p no:cacheprovider
  ```
- FE chạy trong `frontend/` của worktree (`node_modules` đã cài từ đợt trước).
- Commit tiếng Anh, ngắn, không Co-Authored-By.

---

### Task 1: BE — mapping thuần + 4 field model + migration 0015

**Files:**
- Create: `backend/apps/warranty/tracking_status.py`
- Modify: `backend/apps/warranty/models.py` (thêm field sau `return_status`, ~dòng 205)
- Create: `backend/apps/warranty/migrations/0015_journey_best_tracking_status.py`
- Test: `backend/apps/warranty/tests/test_tracking_status.py` (mới)

**Interfaces:**
- Produces: `map_best_vc_group(code) -> str | None`; `journey_vc_groups(journey) -> dict {new, recall, return, main}` (key nhóm hoặc None khi không có mã; main luôn có giá trị); `BEST_VC_GROUP_LABELS` dict key→nhãn. Model fields: `new_tracking_best_status`, `recall_best_status`, `return_tracking_best_status` (CharField 32 blank), `tracking_synced_at` (DateTimeField null).

- [ ] **Step 1: Viết test fail**

Create `backend/apps/warranty/tests/test_tracking_status.py`:

```python
import pytest

from apps.warranty.models import WarrantyClaim
from apps.warranty.tracking_status import (
    BEST_VC_GROUP_LABELS,
    journey_vc_groups,
    map_best_vc_group,
)


def test_map_best_vc_group_all_codes():
    expected = {
        "211": "picking_up",
        "203": "pickup_failed",
        "202": "in_transit", "301": "in_transit", "302": "in_transit",
        "303": "in_transit", "304": "in_transit", "309": "in_transit",
        "601": "delivering",
        "604": "redelivery",
        "666": "delivered",
        "605": "returning", "701": "returning", "702": "returning",
        "703": "returning", "704": "returning", "705": "returning",
        "707": "returning",
        "708": "returned",
        "777": "lost",
        "1000": "damaged",
    }
    for code, group in expected.items():
        assert map_best_vc_group(code) == group
    assert map_best_vc_group("999") is None  # mã lạ
    assert map_best_vc_group("") is None
    assert map_best_vc_group(None) is None
    assert map_best_vc_group(666) == "delivered"  # int cũng nhận


def test_group_labels_cover_11_groups():
    assert len(BEST_VC_GROUP_LABELS) == 11
    assert BEST_VC_GROUP_LABELS["created"] == "Tạo mới"
    assert BEST_VC_GROUP_LABELS["delivered"] == "GTC"


@pytest.mark.django_db
def test_journey_vc_groups_priority_and_fallback(order_factory):
    from apps.warranty.journey_service import WarrantyJourneyService

    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)

    # Không có mã nào → per-code None, main = created.
    groups = journey_vc_groups(j)
    assert groups == {"new": None, "recall": None, "return": None, "main": "created"}

    # Có mã gửi đi nhưng chưa có data Best → created.
    j.new_tracking_code = "B1"
    assert journey_vc_groups(j)["new"] == "created"
    assert journey_vc_groups(j)["main"] == "created"

    # Có data Best cho mã gửi đi.
    j.new_tracking_best_status = "601"
    assert journey_vc_groups(j)["new"] == "delivering"
    assert journey_vc_groups(j)["main"] == "delivering"

    # Ưu tiên: có mã thu hồi → main theo thu hồi.
    j.recall_code = "B2"
    j.recall_best_status = "301"
    assert journey_vc_groups(j)["main"] == "in_transit"

    # Ưu tiên cao nhất: mã gửi lại.
    j.return_tracking_code = "B3"
    j.return_tracking_best_status = "666"
    groups = journey_vc_groups(j)
    assert groups["return"] == "delivered"
    assert groups["main"] == "delivered"

    # Mã lạ đã lưu → coi như chưa có data (created), không nổ.
    j.return_tracking_best_status = "999"
    assert journey_vc_groups(j)["return"] == "created"
```

- [ ] **Step 2: Chạy fail** — `pytest apps/warranty/tests/test_tracking_status.py` → FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

Create `backend/apps/warranty/tracking_status.py`:

```python
"""Nhóm trạng thái VC hiển thị cho hành trình BH theo mã chi tiết Best Express.

Bảng RIÊNG cho bảo hành (11 nhóm hiển thị) — KHÔNG dùng chung với
`apps.best_express.domain.status_map` (bảng đó phục vụ trang Best shipments,
map 202/707 khác mục đích). Pure — không I/O.
"""

from __future__ import annotations

BEST_VC_GROUP_LABELS: dict[str, str] = {
    "created": "Tạo mới",
    "picking_up": "Đang lấy hàng",
    "pickup_failed": "Lấy hàng không thành công",
    "in_transit": "Đang trung chuyển",
    "delivering": "Đang giao hàng",
    "redelivery": "Chờ giao lại",
    "delivered": "GTC",
    "returning": "Đang chuyển hoàn",
    "returned": "Đã trả hàng",
    "lost": "Thất lạc",
    "damaged": "Hư hỏng",
}

_GROUP_BY_CODE: dict[str, str] = {
    "211": "picking_up",
    "203": "pickup_failed",
    "202": "in_transit",
    "301": "in_transit",
    "302": "in_transit",
    "303": "in_transit",
    "304": "in_transit",
    "309": "in_transit",
    "601": "delivering",
    "604": "redelivery",
    "666": "delivered",
    "605": "returning",
    "701": "returning",
    "702": "returning",
    "703": "returning",
    "704": "returning",
    "705": "returning",
    "707": "returning",
    "708": "returned",
    "777": "lost",
    "1000": "damaged",
}


def map_best_vc_group(code) -> str | None:
    """Mã Best (str/int) → key nhóm hiển thị; mã lạ/rỗng → None."""
    raw = str(code or "").strip()
    if not raw:
        return None
    return _GROUP_BY_CODE.get(raw)


def _group_for(tracking_code: str, best_status: str) -> str | None:
    """Nhóm của 1 mã: không có mã → None (ẩn badge); có mã nhưng chưa có/không
    hiểu data Best → "created"."""
    if not (tracking_code or "").strip():
        return None
    return map_best_vc_group(best_status) or "created"


def journey_vc_groups(journey) -> dict:
    """Nhóm VC per-code + nhóm chính của hành trình.

    `main` theo mã ưu tiên (chặng muộn nhất có mã): gửi lại > thu hồi > gửi đi;
    không có mã nào → "created".
    """
    new = _group_for(journey.new_tracking_code, journey.new_tracking_best_status)
    recall = _group_for(journey.recall_code, journey.recall_best_status)
    ret = _group_for(
        journey.return_tracking_code, journey.return_tracking_best_status
    )
    return {"new": new, "recall": recall, "return": ret, "main": ret or recall or new or "created"}
```

Model — thêm sau block `return_status` ([models.py:203-205](backend/apps/warranty/models.py#L203-L205)):

```python
    # Trạng thái Best thô mới nhất theo TỪNG mã (cron trace 10' cập nhật) — nhóm
    # hiển thị suy từ đây qua apps.warranty.tracking_status (không lưu nhóm).
    new_tracking_best_status = models.CharField(max_length=32, blank=True, default="")
    recall_best_status = models.CharField(max_length=32, blank=True, default="")
    return_tracking_best_status = models.CharField(max_length=32, blank=True, default="")
    tracking_synced_at = models.DateTimeField(null=True, blank=True)
```

Migration `0014_...` đợt trước là của claim; tạo `0015_journey_best_tracking_status.py` bằng makemigrations trong docker (mount worktree backend):

```powershell
docker run --rm --network desi-order_default -e POSTGRES_HOST=desi-order-postgres-1 `
  -e REDIS_HOST=desi-order-redis-1 -e REDIS_PASSWORD=testpassword `
  -v "<worktree>\backend:/app" -w /app desi-order-web:wp7 `
  python manage.py makemigrations warranty -n journey_best_tracking_status
```

- [ ] **Step 4: Chạy pass** — `pytest apps/warranty/tests/test_tracking_status.py -q --create-db` → PASS.

- [ ] **Step 5: Commit** — `feat(warranty): best vc group mapping + journey tracking status fields`

---

### Task 2: BE — quét khớp 3 mã + mark_returned + gỡ status khỏi editable

**Files:**
- Modify: `backend/apps/warranty/journey_service.py` (JOURNEY_EDITABLE_FIELDS, mark_packed_by_tracking, thêm mark_returned_by_tracking)
- Modify: `backend/apps/warranty/serializers.py` (gỡ validate_status của Ingest — dead sau khi status rời editable; 2 fields-tuple của write serializers tự theo JOURNEY_EDITABLE_FIELDS? — KIỂM: nếu fields liệt kê tay thì gỡ "status"/"return_status")
- Test: `backend/apps/warranty/tests/test_services.py`, `test_scan_integration.py`, `test_serializers.py`, `test_warranty_claim_api.py` (chỗ nào gửi status trong payload journey thì sửa)

**Interfaces:**
- Consumes: model fields Task 1 (không bắt buộc).
- Produces: `mark_packed_by_tracking(code)` khớp `new_tracking_code | recall_code | return_tracking_code`; `mark_returned_by_tracking(code) -> WarrantyJourney | None` — khớp 1 trong 3 mã (mới nhất trước), set `status=returned` + `warehouse_received_at=localdate()` nếu trống; API journey BỎ QUA key `status`/`return_status` trong payload.

- [ ] **Step 1: Test fail**

Thêm vào `test_services.py`:

```python
@pytest.mark.django_db
@pytest.mark.parametrize("field", ["new_tracking_code", "recall_code", "return_tracking_code"])
def test_mark_packed_matches_all_three_codes(order_factory, field):
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    setattr(j, field, "BEST-X1")
    j.save()
    got = WarrantyJourneyService.mark_packed_by_tracking("BEST-X1")
    assert got is not None and got.id == j.id
    got.refresh_from_db()
    assert got.status == JourneyStatus.PACKED


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["new_tracking_code", "recall_code", "return_tracking_code"])
def test_mark_returned_by_tracking_sets_status_and_date(order_factory, field):
    from django.utils import timezone

    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    setattr(j, field, "BEST-R1")
    j.save()
    got = WarrantyJourneyService.mark_returned_by_tracking("BEST-R1")
    assert got is not None and got.id == j.id
    got.refresh_from_db()
    assert got.status == JourneyStatus.RETURNED
    assert got.warehouse_received_at == timezone.localdate()


@pytest.mark.django_db
def test_mark_returned_keeps_existing_received_date(order_factory):
    from datetime import date

    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.recall_code = "BEST-R2"
    j.warehouse_received_at = date(2026, 7, 1)
    j.save()
    WarrantyJourneyService.mark_returned_by_tracking("BEST-R2")
    j.refresh_from_db()
    assert j.warehouse_received_at == date(2026, 7, 1)  # không đè ngày đã có
    assert j.status == JourneyStatus.RETURNED


@pytest.mark.django_db
def test_mark_returned_unknown_code_returns_none(order_factory):
    assert WarrantyJourneyService.mark_returned_by_tracking("KHONG-CO") is None
    assert WarrantyJourneyService.mark_returned_by_tracking("") is None


@pytest.mark.django_db
def test_update_journey_ignores_status_keys(order_factory):
    """status/return_status không còn editable — payload có cũng bị bỏ qua."""
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    WarrantyJourneyService.update_journey(
        j, {"status": "delivered", "return_status": "shipping", "note": "x"}
    )
    j.refresh_from_db()
    assert j.status == JourneyStatus.NEW  # không bị set tay
    assert j.return_status == ""
    assert j.note == "x"
```

SỬA test cũ trong `test_services.py` (giờ sai hành vi):
- `test_explicit_status_wins`: xóa (thay bằng `test_update_journey_ignores_status_keys`). Payload `{"new_tracking_code": "X", "status": "shipping"}` giờ ra `created` (auto).
- `test_auto_status_no_downgrade_from_shipping` + `test_auto_status_no_downgrade_from_packed`: setup đang dùng `update_journey({"status": ...})` — đổi setup thành gán trực tiếp `j.status = JourneyStatus.SHIPPING; j.save()` (tương tự packed), phần assert giữ nguyên.

- [ ] **Step 2: Chạy fail** — `pytest apps/warranty/tests/test_services.py -q` → FAIL (mark_returned chưa có, editable còn status).

- [ ] **Step 3: Implement journey_service.py**

- `JOURNEY_EDITABLE_FIELDS`: xóa `"status"` (dòng 11) và `"return_status"` (dòng 15).
- `update_journey`: đổi điều kiện `if "status" not in data:` thành luôn chạy auto (status không còn vào từ data):

```python
        # status không còn editable qua API — luôn auto suy new/created theo mã.
        before = journey.status
        cls.apply_auto_status(journey)
        if journey.status != before:
            changed.append("status")
```

- `mark_packed_by_tracking`: đổi filter khớp 3 mã (thêm import `Q` từ django.db.models):

```python
        journey = (
            WarrantyJourney.objects.filter(
                Q(new_tracking_code=code) | Q(recall_code=code) | Q(return_tracking_code=code),
                status__in=promotable_statuses,
            )
            .order_by("-id")
            .first()
        )
```

Cập nhật docstring: khớp cả 3 mã (quét xuất kho đóng hàng bất kỳ chặng nào).

- Thêm method mới:

```python
    @staticmethod
    def mark_returned_by_tracking(code: str) -> WarrantyJourney | None:
        """Quét HÀNG HOÀN khớp 1 trong 3 mã của hành trình → kho đã nhận hàng BH.

        Set trạng thái nội bộ `returned` + ngày kho nhận (nếu trống). KHÔNG ghi
        sổ tồn — hàng BH nhập kho qua phiếu kho như hiện tại.
        """
        from django.utils import timezone

        code = (code or "").strip()
        if not code:
            return None
        journey = (
            WarrantyJourney.objects.filter(
                Q(new_tracking_code=code) | Q(recall_code=code) | Q(return_tracking_code=code)
            )
            .order_by("-id")
            .first()
        )
        if journey is None:
            return None
        journey.status = JourneyStatus.RETURNED
        update_fields = ["status", "updated_at"]
        if journey.warehouse_received_at is None:
            journey.warehouse_received_at = timezone.localdate()
            update_fields.append("warehouse_received_at")
        journey.save(update_fields=update_fields)
        return journey
```

- [ ] **Step 4: serializers.py** — kiểm 2 fields-tuple của journey write/patched serializer (dòng ~88-121): gỡ `"status"`, `"return_status"` khỏi fields liệt kê tay của WRITE serializer (READ serializer GIỮ để hiển thị). `WarrantyClaimIngestSerializer.validate_status` (dòng ~211): xóa method (status không còn trong `JOURNEY_EDITABLE_FIELDS`). Grep test ingest/api nào gửi `status` trong payload journey → sửa bỏ key hoặc đổi expected.

- [ ] **Step 5: Chạy pass** — `pytest apps/warranty -q` → PASS toàn bộ.

- [ ] **Step 6: Commit** — `feat(warranty): scan matches all 3 tracking codes, mark returned on return scan, drop manual journey status`

---

### Task 3: BE — serializer expose vc_groups + raw fields

**Files:**
- Modify: `backend/apps/warranty/serializers.py` (`WarrantyJourneySerializer`)
- Test: `backend/apps/warranty/tests/test_serializers.py`

**Interfaces:**
- Produces: response journey có `new_tracking_best_status`, `recall_best_status`, `return_tracking_best_status`, `tracking_synced_at`, `vc_groups: {new, recall, return, main}` (string|null, main luôn string).

- [ ] **Step 1: Test fail** (thêm vào test_serializers.py):

```python
@pytest.mark.django_db
def test_journey_serializer_exposes_vc_groups(order_factory):
    from apps.warranty.journey_service import WarrantyJourneyService
    from apps.warranty.serializers import WarrantyJourneySerializer

    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.new_tracking_code = "B1"
    j.new_tracking_best_status = "601"
    j.save()
    data = WarrantyJourneySerializer(j).data
    assert data["new_tracking_best_status"] == "601"
    assert data["vc_groups"] == {
        "new": "delivering", "recall": None, "return": None, "main": "delivering",
    }
```

- [ ] **Step 2: Chạy fail**, rồi implement: thêm vào `WarrantyJourneySerializer`:

```python
    vc_groups = serializers.SerializerMethodField()

    @extend_schema_field(
        inline_serializer(
            "WarrantyJourneyVcGroups",
            fields={
                "new": drf_serializers.CharField(allow_null=True),
                "recall": drf_serializers.CharField(allow_null=True),
                "return": drf_serializers.CharField(allow_null=True),
                "main": drf_serializers.CharField(),
            },
        )
    )
    def get_vc_groups(self, obj) -> dict:
        return journey_vc_groups(obj)
```

(import `journey_vc_groups` từ tracking_status; import `extend_schema_field`, `inline_serializer` từ drf_spectacular.utils — theo pattern sẵn trong repo; nếu file đang import kiểu khác thì theo file). Thêm vào `fields` tuple đọc: 4 field mới + `"vc_groups"`.

- [ ] **Step 3: Pass + Commit** — `feat(warranty): expose per-code best status + vc groups on journey serializer`

---

### Task 4: BE — cron trace Best 10 phút

**Files:**
- Create: `backend/apps/warranty/tracking_sync_service.py`
- Create: `backend/apps/warranty/tasks.py`
- Modify: `backend/config/settings_parts/celery.py` (thêm beat entry cuối dict, trước `}` dòng ~276)
- Test: `backend/apps/warranty/tests/test_tracking_sync.py` (mới)

**Interfaces:**
- Consumes: `BestExpressClient(account).trace_query(codes)` (max `TRACE_MAX_CODES=10`/lần), model fields Task 1.
- Produces: `WarrantyTrackingSyncService.sync() -> dict` summary `{codes, updated, skipped}`; task `apps.warranty.tasks.sync_best_tracking_statuses` beat 600s.

- [ ] **Step 1: Test fail** — create `test_tracking_sync.py`:

```python
import pytest

from apps.best_express.models import BestExpressAccount
from apps.warranty.models import WarrantyClaim, WarrantyClaimStatus
from apps.warranty.journey_service import WarrantyJourneyService
from apps.warranty.tracking_sync_service import WarrantyTrackingSyncService


def _account():
    return BestExpressAccount.objects.create(
        name="acc", environment="production", username="u", password="p", is_active=True
    )


def _trace_result(code, status):
    return {
        "code_best": code,
        "merchant_order_code": "",
        "traceLogs": [
            {"status": "211", "modified_at": 1},
            {"status": status, "modified_at": 2},  # mới nhất theo modified_at
        ],
    }


@pytest.mark.django_db
def test_sync_updates_matching_fields(order_factory, monkeypatch):
    _account()
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.new_tracking_code = "B-NEW"
    j.recall_code = "B-RC"
    j.save()

    def fake_trace(self, codes, lang="en-US"):
        data = []
        if "B-NEW" in codes:
            data.append(_trace_result("B-NEW", "601"))
        if "B-RC" in codes:
            data.append(_trace_result("B-RC", "708"))
        return {"data": data}

    monkeypatch.setattr(
        "apps.best_express.infrastructure.best_client.BestExpressClient.trace_query",
        fake_trace,
    )
    summary = WarrantyTrackingSyncService.sync()
    j.refresh_from_db()
    assert j.new_tracking_best_status == "601"
    assert j.recall_best_status == "708"
    assert j.tracking_synced_at is not None
    assert summary["updated"] == 1


@pytest.mark.django_db
def test_sync_skips_closed_claims(order_factory, monkeypatch):
    _account()
    claim = WarrantyClaim.objects.create(
        order=order_factory(), status=WarrantyClaimStatus.COMPLETED
    )
    j = WarrantyJourneyService.add_journey(claim)
    j.new_tracking_code = "B-DONE"
    j.save()
    called = {"n": 0}

    def fake_trace(self, codes, lang="en-US"):
        called["n"] += 1
        return {"data": []}

    monkeypatch.setattr(
        "apps.best_express.infrastructure.best_client.BestExpressClient.trace_query",
        fake_trace,
    )
    WarrantyTrackingSyncService.sync()
    assert called["n"] == 0  # không có mã nào cần trace


@pytest.mark.django_db
def test_sync_without_account_noop(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.new_tracking_code = "B-X"
    j.save()
    summary = WarrantyTrackingSyncService.sync()  # không có account — không nổ
    assert summary == {"codes": 0, "updated": 0, "skipped": "no_account"}


@pytest.mark.django_db
def test_sync_api_error_does_not_raise(order_factory, monkeypatch):
    from apps.best_express.exceptions import BestExpressError

    _account()
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.new_tracking_code = "B-ERR"
    j.save()

    def boom(self, codes, lang="en-US"):
        raise BestExpressError("Best chết")

    monkeypatch.setattr(
        "apps.best_express.infrastructure.best_client.BestExpressClient.trace_query", boom
    )
    summary = WarrantyTrackingSyncService.sync()  # log + bỏ qua, không raise
    assert summary["updated"] == 0
```

- [ ] **Step 2: Chạy fail**, rồi implement `tracking_sync_service.py`:

```python
"""Cron trace Best → cập nhật trạng thái VC thô của hành trình BH (10 phút/lần).

KHÔNG đi qua store_event/apply_event của best_express (mã BH nhập tay không có
BestShipment → sinh rác event unmatched). Đọc thẳng traceLogs mới nhất.
"""

from __future__ import annotations

import logging

from django.db.models import Q
from django.utils import timezone

from apps.warranty.models import WarrantyClaimStatus, WarrantyJourney

logger = logging.getLogger(__name__)

# (field mã, field trạng thái thô) — 3 chặng của hành trình.
_CODE_FIELDS = (
    ("new_tracking_code", "new_tracking_best_status"),
    ("recall_code", "recall_best_status"),
    ("return_tracking_code", "return_tracking_best_status"),
)


def _latest_status_by_code(results: list[dict]) -> dict[str, str]:
    """TraceQuery results → {mã: mã trạng thái Best mới nhất theo modified_at}."""
    latest: dict[str, str] = {}
    for item in results:
        code = str(item.get("code_best") or item.get("merchant_order_code") or "").strip()
        if not code:
            continue
        logs = [log for log in (item.get("traceLogs") or []) if isinstance(log, dict)]
        if not logs:
            continue
        newest = max(logs, key=lambda log: log.get("modified_at") or 0)
        status = str(newest.get("status") or "").strip()
        if status:
            latest[code] = status
    return latest


class WarrantyTrackingSyncService:
    @staticmethod
    def sync() -> dict:
        # Lazy import: warranty không phụ thuộc module-level vào best_express.
        from apps.best_express.exceptions import BestExpressError
        from apps.best_express.infrastructure.best_client import (
            TRACE_MAX_CODES,
            BestExpressClient,
        )
        from apps.best_express.models import BestExpressAccount

        journeys = list(
            WarrantyJourney.objects.filter(
                claim__deleted_at__isnull=True,
            )
            .exclude(
                claim__status__in=[
                    WarrantyClaimStatus.COMPLETED,
                    WarrantyClaimStatus.CANCELLED,
                ]
            )
            .exclude(
                Q(new_tracking_code="") & Q(recall_code="") & Q(return_tracking_code="")
            )
        )
        codes: set[str] = set()
        for j in journeys:
            for code_field, _ in _CODE_FIELDS:
                code = (getattr(j, code_field) or "").strip()
                if code:
                    codes.add(code)
        if not codes:
            return {"codes": 0, "updated": 0, "skipped": "no_codes"}

        account = (
            BestExpressAccount.objects.filter(is_active=True)
            .order_by("environment")  # "production" < "test" alphabet → production trước
            .first()
        )
        if account is None:
            logger.warning("warranty tracking sync: không có BestExpressAccount active — bỏ qua")
            return {"codes": 0, "updated": 0, "skipped": "no_account"}

        client = BestExpressClient(account)
        ordered = sorted(codes)
        results: list[dict] = []
        for start in range(0, len(ordered), TRACE_MAX_CODES):
            chunk = ordered[start : start + TRACE_MAX_CODES]
            try:
                data = client.trace_query(chunk)
            except (BestExpressError, Exception) as exc:  # noqa: BLE001 — cron: log rồi bỏ qua vòng này
                logger.warning("warranty tracking sync: trace lỗi (%s mã): %s", len(chunk), exc)
                continue
            results.extend(data.get("data") or [])

        latest = _latest_status_by_code(results)
        now = timezone.now()
        updated = 0
        for j in journeys:
            changed: list[str] = []
            for code_field, status_field in _CODE_FIELDS:
                code = (getattr(j, code_field) or "").strip()
                new_status = latest.get(code)
                if code and new_status and getattr(j, status_field) != new_status:
                    setattr(j, status_field, new_status)
                    changed.append(status_field)
            if changed:
                j.tracking_synced_at = now
                j.save(update_fields=[*changed, "tracking_synced_at", "updated_at"])
                updated += 1
        return {"codes": len(codes), "updated": updated, "skipped": ""}
```

Create `backend/apps/warranty/tasks.py`:

```python
from celery import shared_task

from apps.warranty.tracking_sync_service import WarrantyTrackingSyncService


@shared_task
def sync_best_tracking_statuses() -> dict:
    """Cron 10': trace Best cập nhật trạng thái VC 3 mã của hành trình BH mở."""
    return WarrantyTrackingSyncService.sync()
```

`config/settings_parts/celery.py` — thêm trước `}` đóng dict beat (~dòng 276):

```python
    # Trace Best Express cập nhật trạng thái VC 3 mã hành trình bảo hành (phiếu
    # chưa Hoàn thành/Hủy). Xem docs/superpowers/specs/2026-07-18-warranty-journey-best-tracking-status-design.md.
    "warranty-sync-best-tracking": {
        "task": "apps.warranty.tasks.sync_best_tracking_statuses",
        "schedule": 600.0,
    },
```

Lưu ý test: `test_sync_updates_matching_fields` mong `updated == 1` (1 journey, 2 field đổi trong 1 save).

- [ ] **Step 3: Pass** — `pytest apps/warranty/tests/test_tracking_sync.py -q` → PASS.
- [ ] **Step 4: Commit** — `feat(warranty): best tracking status sync cron (10m trace)`

---

### Task 5: BE — pending scan hoàn khớp hành trình BH

**Files:**
- Modify: `backend/apps/inventory/application/services/pending_scan_service.py` (`_process_one` nhánh `if not orders:` ~dòng 493-515; `_match_warranty_journey` ~dòng 634)
- Test: `backend/apps/warranty/tests/test_scan_integration.py`

**Interfaces:**
- Consumes: `mark_returned_by_tracking` (Task 2).
- Produces: pending scan kind=RETURN không khớp Order nhưng khớp mã BH → `status=MATCHED`, `result_ref="warranty-journey:<id>"`, message "— đã nhận hàng hoàn."; KHÔNG tạo ReturnNote.

- [ ] **Step 1: Test fail** — thêm vào `test_scan_integration.py` (theo pattern test hiện có trong file — dùng cách tạo PendingShipmentScan kind return sẵn có; nếu file chưa có pattern return thì tạo scan qua model trực tiếp):

```python
@pytest.mark.django_db
def test_return_scan_matches_warranty_journey(order_factory):
    from apps.inventory.application.services.pending_scan_service import PendingScanService
    from apps.inventory.models import PendingScanKind, PendingScanStatus, PendingShipmentScan
    from apps.warranty.models import JourneyStatus, WarrantyClaim

    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.recall_code = "BH-RET-1"
    j.save()
    scan = PendingShipmentScan.objects.create(
        code="BH-RET-1", kind=PendingScanKind.RETURN
    )
    outcome, used_remote = PendingScanService._process_one(scan.id)
    assert outcome == "matched"
    assert used_remote is False
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED
    assert scan.result_ref == f"warranty-journey:{j.id}"
    j.refresh_from_db()
    assert j.status == JourneyStatus.RETURNED
    assert j.warehouse_received_at is not None
```

(Điều chỉnh field bắt buộc của PendingShipmentScan theo model thật — xem cách các test khác trong repo tạo; ví dụ cần `warehouse_id`/`scanned_by` thì thêm.)

- [ ] **Step 2: Chạy fail**, rồi implement trong `_process_one` — thay block dispatch-only (dòng 498-513) thành xử lý cả 2 kind:

```python
                if scan.kind == PendingScanKind.DISPATCH:
                    journey = PendingScanService._match_warranty_journey(scan.code)
                    note = "— đã đóng hàng."
                else:
                    # Quét HOÀN khớp mã BH: kho đã nhận hàng bảo hành về — ghi nhận
                    # ngày nhận + trạng thái nội bộ, KHÔNG ghi sổ tồn (hàng BH nhập
                    # qua phiếu kho riêng).
                    journey = PendingScanService._match_warranty_journey_return(scan.code)
                    note = "— đã nhận hàng hoàn."
                if journey is not None:
                    scan.status = PendingScanStatus.MATCHED
                    scan.result_ref = f"warranty-journey:{journey.id}"
                    scan.error = (
                        f"Khớp hành trình bảo hành #{journey.id} (claim #{journey.claim_id}) "
                        + note
                    )
                    scan.save(
                        update_fields=[
                            "attempts", "last_attempt_at", "status",
                            "result_ref", "error", "updated_at",
                        ]
                    )
                    return "matched", False
```

Và thêm helper cạnh `_match_warranty_journey`:

```python
    @staticmethod
    def _match_warranty_journey_return(code: str):
        """Quét hoàn khớp 1 trong 3 mã hành trình BH (lazy import như trên)."""
        from apps.warranty.journey_service import WarrantyJourneyService

        return WarrantyJourneyService.mark_returned_by_tracking(code)
```

Cập nhật comment cũ dòng 494-497 (không còn dispatch-only).

- [ ] **Step 3: Pass** — `pytest apps/warranty/tests/test_scan_integration.py -q` + chạy thêm `pytest apps/inventory -q` (suite inventory không hồi quy).
- [ ] **Step 4: Commit** — `feat(scan): return scan matches warranty journey codes`

---

### Task 6: Schema regen + FE badge/gỡ select

**Files:**
- Modify: `backend/schema.yaml`, `frontend/src/api/schema.d.ts` (generated)
- Modify: `frontend/src/features/warranty-claims/journey-enums.ts` (thêm VC_GROUP_LABELS + VC_GROUP_BADGE_CLASS)
- Modify: `frontend/src/features/warranty-claims/components/journey/journey-form.ts` (gỡ status/return_status khỏi form)
- Modify: `frontend/src/features/warranty-claims/components/journey/journey-sections-send.tsx` (§4: gỡ select trạng thái → badge nội bộ + badge VC per-code cạnh 2 ô mã)
- Modify: `frontend/src/features/warranty-claims/components/journey/journey-sections-return.tsx` (§7: gỡ select return_status → badge VC cạnh ô mã hàng về)
- Modify: `frontend/src/features/warranty-claims/components/JourneyCard.tsx` (badge main ở header — xem cấu trúc thật khi sửa)
- Test: `frontend/src/features/warranty-claims/components/JourneyCard.test.tsx`

**Interfaces:**
- Consumes: `vc_groups` từ schema Task 3.
- Produces: `VC_GROUP_LABELS: Record<string,string>` (11 nhóm), `VC_GROUP_BADGE_CLASS: Record<string,string>`, component `VcGroupBadge({group}: {group: string | null})` (export từ journey-fields.tsx) — null → không render.

- [ ] **Step 1: Regen schema**

```powershell
docker run --rm --network desi-order_default -e POSTGRES_HOST=desi-order-postgres-1 `
  -e REDIS_HOST=desi-order-redis-1 -e REDIS_PASSWORD=testpassword `
  -v "<worktree>\backend:/app" -w /app desi-order-web:wp7 python manage.py spectacular --file schema.yaml
cd <worktree>\frontend; npm run gen:api; npx prettier --write src/api/schema.d.ts
```

Kiểm `git diff --stat` 2 file generated chỉ quanh warranty journey (status/return_status rời write schema + 4 field + vc_groups mới).

- [ ] **Step 2: journey-enums.ts** — thêm:

```ts
/** Nhóm trạng thái VC tự động theo Best (11 nhóm) — key khớp BE tracking_status. */
export const VC_GROUP_LABELS: Record<string, string> = {
  created: "Tạo mới",
  picking_up: "Đang lấy hàng",
  pickup_failed: "Lấy hàng không thành công",
  in_transit: "Đang trung chuyển",
  delivering: "Đang giao hàng",
  redelivery: "Chờ giao lại",
  delivered: "GTC",
  returning: "Đang chuyển hoàn",
  returned: "Đã trả hàng",
  lost: "Thất lạc",
  damaged: "Hư hỏng",
};

export const VC_GROUP_BADGE_CLASS: Record<string, string> = {
  created: "bg-gray-200 text-gray-600",
  picking_up: "bg-sky-100 text-sky-700",
  pickup_failed: "bg-rose-100 text-rose-700",
  in_transit: "bg-indigo-100 text-indigo-700",
  delivering: "bg-blue-100 text-blue-700",
  redelivery: "bg-amber-100 text-amber-700",
  delivered: "bg-green-100 text-green-700",
  returning: "bg-orange-100 text-orange-700",
  returned: "bg-teal-100 text-teal-700",
  lost: "bg-red-100 text-red-700",
  damaged: "bg-red-200 text-red-800",
};
```

- [ ] **Step 3: journey-fields.tsx** — thêm component:

```tsx
/** Badge nhóm trạng thái VC tự động theo Best — null (chưa có mã) → ẩn. */
export function VcGroupBadge({ group }: { group: string | null | undefined }) {
  if (!group) return null;
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium ${VC_GROUP_BADGE_CLASS[group] ?? "bg-gray-200 text-gray-600"}`}
    >
      {VC_GROUP_LABELS[group] ?? group}
    </span>
  );
}
```

- [ ] **Step 4: journey-form.ts** — gỡ `status` + `return_status` khỏi `JourneyFormState`, `initForm`, `buildJourneyPatch` (cả dòng `if (form.status !== journey.status) patch.status = form.status;`). Chỗ §4 đang dùng `form.status === "returned"` cho "Kho check hàng" → đổi sang `journey.status === "returned"`.

- [ ] **Step 5: journey-sections-send.tsx** —
  - Xóa `MANUAL_STATUS_VALUES`/`MANUAL_STATUS_OPTIONS` + import Select/JOURNEY_STATUS_OPTIONS không dùng.
  - Field "Trạng thái hành trình" → hiển thị read-only: badge VC main + badge nội bộ nhỏ:

```tsx
        <Field label="Trạng thái vận chuyển (tự động theo Best)">
          <div className="flex flex-wrap items-center gap-2">
            <VcGroupBadge group={journey.vc_groups?.main ?? "created"} />
            {/* Trạng thái nội bộ kho (quét xuất/hoàn đặt) — tham khảo. */}
            <Badge variant="outline" className="shrink-0">
              {journey.status_label}
            </Badge>
          </div>
        </Field>
```

  - 2 ô mã: bọc input + badge per-code:

```tsx
        <Field label="Mã vận đơn mới" htmlFor={fieldId("new_tracking_code")}>
          <div className="flex items-center gap-2">
            <Input ... (giữ nguyên) />
            <VcGroupBadge group={journey.vc_groups?.new} />
          </div>
        </Field>
```

  (tương tự cho `recall_code` với `journey.vc_groups?.recall`).

- [ ] **Step 6: journey-sections-return.tsx** — xóa Field "Trạng thái giao" + import RETURN_STATUS_OPTIONS; ô "Mã vận đơn (hàng về)" thêm `<VcGroupBadge group={journey.vc_groups?.return} />` cạnh Input như trên.

- [ ] **Step 7: JourneyCard.tsx** — đọc file, thêm `<VcGroupBadge group={journey.vc_groups?.main ?? "created"} />` vào header card (cạnh tên hành trình). Cập nhật `JourneyCard.test.tsx` fixture journey thêm `vc_groups` + assert badge label xuất hiện (ví dụ vc_groups.main="delivering" → "Đang giao hàng").

- [ ] **Step 8: Chạy pass**

```powershell
npx vitest run src/features/warranty-claims; npm run typecheck; npm run build
```

Expected: pass + build OK (test cũ nào reference status form thì cập nhật theo).

- [ ] **Step 9: Commit** — `feat(warranty): auto best vc status badges, drop manual journey status selects`

---

### Task 7: Verify tổng + push

- [ ] **Step 1:** `pytest apps/warranty apps/inventory -q --create-db` (DB mới `desi_wjtrack2`) → 0 fail; FE vitest warranty + typecheck + build → pass.
- [ ] **Step 2:** `git fetch origin master && git merge origin/master --no-edit` → resolve nếu conflict, re-test.
- [ ] **Step 3:** Push `feat/warranty-claim-status-flow`, gửi link MR (MR chung với bộ trạng thái phiếu). Note deploy: migration 0015 + beat mới `warranty-sync-best-tracking` (cần restart beat), yêu cầu có `BestExpressAccount` active để cron chạy.
