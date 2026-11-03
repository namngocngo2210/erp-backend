# Warranty Best Tracking Realtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hành trình bảo hành nhận trạng thái vận đơn Best ngay khi webhook về, thay vì chờ cron poll 10 phút.

**Architecture:** Dùng event bus in-process có sẵn (`apps/common/events.py`). `best_express` publish `BestTrackingUpdated` khi lưu được event trạng thái mới; `warranty` subscribe và ghi vào `WarrantyJourney`. Không app nào import app kia (chỉ consumer import contract của publisher — cùng chiều với `apps/users/subscribers.py` ↔ `apps/hr/contracts`). Cron cũ giữ nguyên làm lưới an toàn.

**Tech Stack:** Django 5 + DRF, pytest + pytest-django, Celery beat.

## Global Constraints

- Spec nguồn: `docs/superpowers/specs/2026-07-22-warranty-best-tracking-realtime-design.md`.
- `apps/best_express` KHÔNG được import bất cứ thứ gì từ `apps.warranty` — chiều phụ thuộc chỉ đi warranty → best_express.contracts.
- Event chỉ chứa giá trị nguyên thuỷ (str/int), KHÔNG truyền ORM object qua bus.
- 3 cột mốc thời gian dùng `BigIntegerField` — epoch millisecond (~1.78×10¹²) tràn `IntegerField` 32-bit.
- Guard so sánh dùng `>=`, KHÔNG phải `>`.
- Cron `warranty-sync-best-tracking` giữ nguyên lịch 600s và giữ nguyên phạm vi query; chỉ thêm việc ghi `*_ms` và tuân guard.
- KHÔNG sửa cơ chế `unmatched`/retry/purge của `best_express`; KHÔNG đụng `Order` hay luồng KShip/KiotViet.
- Comment trong code viết tiếng Việt, khớp phong cách file lân cận.
- Commit message tiếng Anh, ngắn, KHÔNG kèm dòng `Co-Authored-By`.
- Lệnh pytest chạy trong thư mục `backend/`.

---

### Task 1: Contract event + publish từ `store_event`

**Files:**
- Create: `backend/apps/best_express/contracts/__init__.py`
- Create: `backend/apps/best_express/contracts/tracking_events.py`
- Modify: `backend/apps/best_express/application/services/webhook_service.py:32-65`
- Test: `backend/apps/best_express/tests/test_tracking_event_publish.py`

**Interfaces:**
- Consumes: `apps.common.events.DomainEvent`, `event_bus.publish_on_commit`.
- Produces: `BestTrackingUpdated` — frozen dataclass, `event_type = "best_express.tracking_updated"`, fields `code_best: str`, `merchant_order_code: str`, `best_status_code: str`, `modified_at_ms: int` (đều default rỗng/0). Task 4 subscribe event này.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/best_express/tests/test_tracking_event_publish.py`:

```python
import pytest

from apps.best_express.application.services.webhook_service import store_event
from apps.best_express.contracts.tracking_events import BestTrackingUpdated
from apps.best_express.models import BestExpressAccount
from apps.common.events import event_bus


@pytest.fixture
def captured():
    """Bắt event trên bus.

    KHÔNG gọi `event_bus.clear()` khi teardown: bus là singleton toàn process,
    clear sẽ xoá luôn subscriber production của các app khác và làm test khác
    fail theo thứ tự chạy. Handler thừa chỉ append vào list không ai đọc.
    """
    received: list[BestTrackingUpdated] = []
    event_bus.subscribe(BestTrackingUpdated.event_type, received.append)
    return received


def _account():
    return BestExpressAccount.objects.create(
        name="acc", environment="production", username="u", password="p", is_active=True
    )


def _payload(status="601", modified_at=1700000000000):
    return {
        "code_best": "84859706027573",
        "merchant_order_code": "BHJ6N-5DTZ",
        "status": status,
        "modified_at": modified_at,
    }


@pytest.mark.django_db
def test_store_event_publishes_tracking_event(captured):
    account = _account()

    assert store_event(account, _payload()) is not None

    assert len(captured) == 1
    event = captured[0]
    assert event.code_best == "84859706027573"
    assert event.merchant_order_code == "BHJ6N-5DTZ"
    assert event.best_status_code == "601"
    assert event.modified_at_ms == 1700000000000


@pytest.mark.django_db
def test_duplicate_event_does_not_publish(captured):
    account = _account()

    assert store_event(account, _payload()) is not None
    # Cùng (code_best, status, modified_at) → unique constraint chặn, không publish lại.
    assert store_event(account, _payload()) is None

    assert len(captured) == 1
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd backend && pytest apps/best_express/tests/test_tracking_event_publish.py -v`
Expected: FAIL ngay ở import — `ModuleNotFoundError: No module named 'apps.best_express.contracts'`.

- [ ] **Step 3: Tạo contract event**

Tạo `backend/apps/best_express/contracts/__init__.py` (file rỗng).

Tạo `backend/apps/best_express/contracts/tracking_events.py`:

```python
"""Domain event Best Express phát cho module khác qua event bus in-process.

`best_express` KHÔNG biết ai tiêu thụ — consumer tự subscribe ở `ready()` của
app mình. Xem `apps/common/events.py` và `docs/architecture-standard.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from apps.common.events import DomainEvent


@dataclass(frozen=True)
class BestTrackingUpdated(DomainEvent):
    """Phát khi lưu được 1 event trạng thái MỚI từ Best (webhook hoặc trace sync)."""

    event_type: ClassVar[str] = "best_express.tracking_updated"
    code_best: str = ""
    merchant_order_code: str = ""
    best_status_code: str = ""
    modified_at_ms: int = 0
```

- [ ] **Step 4: Publish trong `store_event`**

Trong `backend/apps/best_express/application/services/webhook_service.py`, thêm import ở đầu file (sau `from apps.best_express.domain.status_map import map_best_status`):

```python
from apps.best_express.contracts.tracking_events import BestTrackingUpdated
from apps.common.events import event_bus
```

Thay khối `try/except IntegrityError` ở cuối `store_event` bằng:

```python
    try:
        with transaction.atomic():
            event = BestWebhookEvent.objects.create(
                account=account,
                code_best=code_best,
                merchant_order_code=merchant_order_code,
                best_status_code=best_status_code,
                payload=payload,
                modified_at_ms=modified_at_ms,
            )
    except IntegrityError:
        logger.info(
            "Best webhook: bỏ qua event trùng code=%s status=%s ts=%s",
            code_best,
            best_status_code,
            modified_at_ms,
        )
        return None
    # Event MỚI → báo module khác (hành trình bảo hành). Publish ở ĐÂY chứ không
    # ở apply_event: apply_event chỉ chạy khi khớp BestShipment, mà mã bảo hành
    # nhập tay không có dòng nào trong bảng đó. publish_on_commit để consumer
    # đọc được dữ liệu đã commit.
    event_bus.publish_on_commit(
        BestTrackingUpdated(
            code_best=code_best,
            merchant_order_code=merchant_order_code,
            best_status_code=best_status_code,
            modified_at_ms=modified_at_ms,
        )
    )
    return event
```

- [ ] **Step 5: Chạy test để xác nhận PASS**

Run: `cd backend && pytest apps/best_express/tests/test_tracking_event_publish.py -v`
Expected: 2 passed.

- [ ] **Step 6: Chạy test cũ của best_express để chắc không phá gì**

Run: `cd backend && pytest apps/best_express -q`
Expected: toàn bộ PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/best_express/contracts backend/apps/best_express/application/services/webhook_service.py backend/apps/best_express/tests/test_tracking_event_publish.py
git commit -m "feat(best-express): publish tracking event when a new status arrives"
```

---

### Task 2: Guard chống event lạc hậu + bảng field dùng chung

Task 4 (subscriber) và Task 5 (cron) đều cần cùng một guard và cùng bộ tên field. Đặt một chỗ để hai bên không lệch nhau.

**Files:**
- Modify: `backend/apps/warranty/tracking_status.py`
- Test: `backend/apps/warranty/tests/test_tracking_status.py`

**Interfaces:**
- Produces:
  - `should_apply_best_status(current_ms: int, incoming_ms: int) -> bool`
  - `JOURNEY_CODE_FIELDS: tuple[tuple[str, str, str], ...]` — mỗi phần tử là `(field mã, field trạng thái thô, field mốc ms)` cho 3 chặng, theo thứ tự gửi đi → thu hồi → gửi lại.

- [ ] **Step 1: Viết test thất bại**

Thêm vào cuối `backend/apps/warranty/tests/test_tracking_status.py`:

```python
def test_should_apply_best_status_blocks_older_events():
    from apps.warranty.tracking_status import should_apply_best_status

    # Event mới hơn → ghi.
    assert should_apply_best_status(100, 200) is True
    # Cùng mốc → vẫn ghi (nội dung giống nhau, tránh kẹt).
    assert should_apply_best_status(200, 200) is True
    # Cũ hơn → chặn, nếu không "GTC" bị kéo ngược về "Đang trung chuyển".
    assert should_apply_best_status(200, 100) is False
    # Chưa có mốc nào (mặc định 0) → luôn ghi.
    assert should_apply_best_status(0, 0) is True
    assert should_apply_best_status(0, 50) is True


def test_journey_code_fields_cover_three_legs():
    from apps.warranty.tracking_status import JOURNEY_CODE_FIELDS

    assert JOURNEY_CODE_FIELDS == (
        ("new_tracking_code", "new_tracking_best_status", "new_tracking_best_status_ms"),
        ("recall_code", "recall_best_status", "recall_best_status_ms"),
        (
            "return_tracking_code",
            "return_tracking_best_status",
            "return_tracking_best_status_ms",
        ),
    )
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd backend && pytest apps/warranty/tests/test_tracking_status.py -v`
Expected: FAIL — `ImportError: cannot import name 'should_apply_best_status'`.

- [ ] **Step 3: Cài đặt**

Thêm vào `backend/apps/warranty/tracking_status.py`, ngay sau `_GROUP_BY_CODE`:

```python
# (field mã, field trạng thái thô, field mốc thời gian) — 3 chặng của hành trình.
# Dùng chung cho subscriber realtime và cron trace để hai đường không lệch nhau.
JOURNEY_CODE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("new_tracking_code", "new_tracking_best_status", "new_tracking_best_status_ms"),
    ("recall_code", "recall_best_status", "recall_best_status_ms"),
    (
        "return_tracking_code",
        "return_tracking_best_status",
        "return_tracking_best_status_ms",
    ),
)


def should_apply_best_status(current_ms, incoming_ms) -> bool:
    """Có nên ghi đè trạng thái đang lưu bằng event vừa tới?

    Best không đảm bảo thứ tự event; không chặn thì "GTC" bị kéo ngược về
    "Đang trung chuyển". Dùng `>=` (không phải `>`) để event cùng mốc vẫn ghi
    được — nội dung giống nhau nên vô hại, và tránh kẹt khi mốc đang lưu là 0.
    """
    try:
        incoming = int(incoming_ms or 0)
    except (TypeError, ValueError):
        incoming = 0
    try:
        current = int(current_ms or 0)
    except (TypeError, ValueError):
        current = 0
    return incoming >= current
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `cd backend && pytest apps/warranty/tests/test_tracking_status.py -v`
Expected: toàn bộ PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/warranty/tracking_status.py backend/apps/warranty/tests/test_tracking_status.py
git commit -m "feat(warranty): add stale-event guard for Best tracking status"
```

---

### Task 3: Ba cột mốc thời gian trên `WarrantyJourney`

**Files:**
- Modify: `backend/apps/warranty/models.py:220-223`
- Create: `backend/apps/warranty/migrations/0016_journey_best_status_ms.py`
- Test: `backend/apps/warranty/tests/test_models.py`

**Interfaces:**
- Produces: `WarrantyJourney.new_tracking_best_status_ms`, `.recall_best_status_ms`, `.return_tracking_best_status_ms` — `BigIntegerField(default=0)`. Task 4 và 5 ghi vào các field này.

- [ ] **Step 1: Viết test thất bại**

Thêm vào cuối `backend/apps/warranty/tests/test_models.py`:

```python
@pytest.mark.django_db
def test_journey_best_status_ms_defaults_to_zero(order_factory):
    from apps.warranty.journey_service import WarrantyJourneyService
    from apps.warranty.models import WarrantyClaim

    claim = WarrantyClaim.objects.create(order=order_factory())
    journey = WarrantyJourneyService.add_journey(claim)

    assert journey.new_tracking_best_status_ms == 0
    assert journey.recall_best_status_ms == 0
    assert journey.return_tracking_best_status_ms == 0

    # Epoch millisecond vượt trần IntegerField 32-bit (2_147_483_647) — phải lưu được.
    journey.new_tracking_best_status_ms = 1_780_000_000_000
    journey.save(update_fields=["new_tracking_best_status_ms"])
    journey.refresh_from_db()
    assert journey.new_tracking_best_status_ms == 1_780_000_000_000
```

Nếu đầu file `test_models.py` chưa có `import pytest` thì thêm vào.

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd backend && pytest apps/warranty/tests/test_models.py -v -k best_status_ms`
Expected: FAIL — `AttributeError: 'WarrantyJourney' object has no attribute 'new_tracking_best_status_ms'`.

- [ ] **Step 3: Thêm field vào model**

Trong `backend/apps/warranty/models.py`, ngay sau dòng `return_tracking_best_status = ...` và trước `tracking_synced_at = ...`:

```python
    # Mốc `modified_at` (epoch ms) của trạng thái đang lưu, theo từng chặng —
    # chặn event Best tới muộn ghi đè trạng thái mới hơn. BigInteger vì epoch ms
    # tràn IntegerField 32-bit.
    new_tracking_best_status_ms = models.BigIntegerField(default=0)
    recall_best_status_ms = models.BigIntegerField(default=0)
    return_tracking_best_status_ms = models.BigIntegerField(default=0)
```

- [ ] **Step 4: Sinh migration**

Run: `cd backend && python manage.py makemigrations warranty --name journey_best_status_ms`
Expected: tạo `apps/warranty/migrations/0016_journey_best_status_ms.py` với 3 `AddField`.

Mở file vừa sinh kiểm tra: cả 3 field phải là `models.BigIntegerField(default=0)`, dependency trỏ `("warranty", "0015_journey_best_tracking_status")`.

- [ ] **Step 5: Chạy test để xác nhận PASS**

Run: `cd backend && pytest apps/warranty/tests/test_models.py -v -k best_status_ms`
Expected: PASS.

- [ ] **Step 6: Xác nhận không còn migration thiếu**

Run: `cd backend && python manage.py makemigrations --check --dry-run`
Expected: `No changes detected`.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/warranty/models.py backend/apps/warranty/migrations/0016_journey_best_status_ms.py backend/apps/warranty/tests/test_models.py
git commit -m "feat(warranty): track per-leg Best status timestamp on journeys"
```

---

### Task 4: Subscriber ghi trạng thái realtime vào hành trình

**Files:**
- Create: `backend/apps/warranty/subscribers.py`
- Modify: `backend/apps/warranty/apps.py`
- Modify: `backend/apps/warranty/tests/conftest.py`
- Test: `backend/apps/warranty/tests/test_tracking_subscriber.py`

**Interfaces:**
- Consumes: `BestTrackingUpdated` (Task 1); `should_apply_best_status`, `JOURNEY_CODE_FIELDS` (Task 2); 3 field `*_ms` (Task 3).
- Produces: `apps.warranty.subscribers.register()` — idempotent, gọi ở `WarrantyConfig.ready()`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/warranty/tests/test_tracking_subscriber.py`:

```python
import pytest

from apps.best_express.contracts.tracking_events import BestTrackingUpdated
from apps.warranty.journey_service import WarrantyJourneyService
from apps.warranty.models import WarrantyClaim
from apps.warranty.subscribers import _on_best_tracking_updated


@pytest.fixture
def journey(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.new_tracking_code = "B-NEW"
    j.recall_code = "B-RC"
    j.return_tracking_code = "B-RT"
    j.save()
    return j


@pytest.mark.django_db
def test_updates_leg_matched_by_code_best(journey):
    _on_best_tracking_updated(
        BestTrackingUpdated(code_best="B-RC", best_status_code="708", modified_at_ms=500)
    )

    journey.refresh_from_db()
    assert journey.recall_best_status == "708"
    assert journey.recall_best_status_ms == 500
    assert journey.tracking_synced_at is not None
    # Chặng khác không bị đụng.
    assert journey.new_tracking_best_status == ""
    assert journey.return_tracking_best_status == ""


@pytest.mark.django_db
def test_matches_by_merchant_order_code_when_code_best_empty(journey):
    _on_best_tracking_updated(
        BestTrackingUpdated(
            code_best="", merchant_order_code="B-NEW", best_status_code="601", modified_at_ms=10
        )
    )

    journey.refresh_from_db()
    assert journey.new_tracking_best_status == "601"


@pytest.mark.django_db
def test_older_event_does_not_overwrite(journey):
    _on_best_tracking_updated(
        BestTrackingUpdated(code_best="B-RT", best_status_code="666", modified_at_ms=900)
    )
    # Event tới muộn nhưng mốc CŨ hơn — không được kéo "GTC" về "đang trung chuyển".
    _on_best_tracking_updated(
        BestTrackingUpdated(code_best="B-RT", best_status_code="302", modified_at_ms=100)
    )

    journey.refresh_from_db()
    assert journey.return_tracking_best_status == "666"
    assert journey.return_tracking_best_status_ms == 900


@pytest.mark.django_db
def test_unknown_code_is_noop(journey):
    _on_best_tracking_updated(
        BestTrackingUpdated(code_best="KHONG-KHOP", best_status_code="601", modified_at_ms=1)
    )

    journey.refresh_from_db()
    assert journey.new_tracking_best_status == ""
    assert journey.tracking_synced_at is None


@pytest.mark.django_db
def test_event_without_code_or_status_is_noop(journey):
    _on_best_tracking_updated(BestTrackingUpdated(best_status_code="601", modified_at_ms=1))
    _on_best_tracking_updated(BestTrackingUpdated(code_best="B-NEW", best_status_code=""))

    journey.refresh_from_db()
    assert journey.new_tracking_best_status == ""


@pytest.mark.django_db
def test_register_is_idempotent_and_wired_to_bus(journey):
    from apps.common.events import event_bus
    from apps.warranty import subscribers

    subscribers.register()
    subscribers.register()

    event_bus.publish(
        BestTrackingUpdated(code_best="B-NEW", best_status_code="601", modified_at_ms=7)
    )

    journey.refresh_from_db()
    assert journey.new_tracking_best_status == "601"
    # Đăng ký 2 lần không được nhân đôi handler → mốc chỉ ghi 1 lần, giá trị đúng.
    assert journey.new_tracking_best_status_ms == 7
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd backend && pytest apps/warranty/tests/test_tracking_subscriber.py -v`
Expected: FAIL ở import — `ModuleNotFoundError: No module named 'apps.warranty.subscribers'`.

- [ ] **Step 3: Viết subscriber**

Tạo `backend/apps/warranty/subscribers.py`:

```python
"""Nhận trạng thái vận đơn Best realtime cho hành trình bảo hành.

`best_express` publish `BestTrackingUpdated` (từ webhook và từ trace sync);
warranty subscribe ở đây. Chiều phụ thuộc chỉ đi warranty → best_express.contracts,
không có chiều ngược lại. Cron `warranty-sync-best-tracking` vẫn chạy 10 phút/lần
làm lưới an toàn cho event rơi hoặc mã không thuộc account đã cấu hình webhook.
"""

from __future__ import annotations

import logging

from apps.best_express.contracts.tracking_events import BestTrackingUpdated
from apps.common.events import event_bus

logger = logging.getLogger(__name__)


def _on_best_tracking_updated(event: BestTrackingUpdated) -> None:
    from django.db.models import Q
    from django.utils import timezone

    from apps.warranty.models import WarrantyJourney
    from apps.warranty.tracking_status import (
        JOURNEY_CODE_FIELDS,
        should_apply_best_status,
    )

    status = (event.best_status_code or "").strip()
    codes = [
        code
        for code in ((event.code_best or "").strip(), (event.merchant_order_code or "").strip())
        if code
    ]
    if not codes or not status:
        return

    match = Q()
    for code_field, _, _ in JOURNEY_CODE_FIELDS:
        match |= Q(**{f"{code_field}__in": codes})

    # Không log khi không khớp: đa số event Best là của đơn bán, không phải bảo
    # hành — log ở đây sẽ thành rác.
    for journey in WarrantyJourney.objects.filter(match):
        changed: list[str] = []
        for code_field, status_field, ms_field in JOURNEY_CODE_FIELDS:
            if (getattr(journey, code_field) or "").strip() not in codes:
                continue
            if not should_apply_best_status(getattr(journey, ms_field), event.modified_at_ms):
                continue
            setattr(journey, status_field, status)
            setattr(journey, ms_field, int(event.modified_at_ms or 0))
            changed += [status_field, ms_field]
        if changed:
            journey.tracking_synced_at = timezone.now()
            journey.save(update_fields=[*changed, "tracking_synced_at", "updated_at"])


def register() -> None:
    if not event_bus.is_subscribed(
        BestTrackingUpdated.event_type, _on_best_tracking_updated
    ):
        event_bus.subscribe(BestTrackingUpdated.event_type, _on_best_tracking_updated)
```

- [ ] **Step 4: Đăng ký ở `ready()`**

Thay nguyên `backend/apps/warranty/apps.py`:

```python
from django.apps import AppConfig


class WarrantyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.warranty"

    def ready(self):
        from apps.warranty import subscribers

        subscribers.register()
```

- [ ] **Step 5: Chống bị app khác wipe subscriber trong test**

`apps/hr/tests/test_employee_service.py` gọi `event_bus.clear()` khi teardown — bus là singleton toàn process nên subscriber production của warranty bị xoá theo, và test warranty chạy sau đó sẽ fail tuỳ thứ tự. `apps/hr/tests/conftest.py` đã xử lý đúng vấn đề này cho `users`; làm tương tự cho warranty.

Thêm vào đầu `backend/apps/warranty/tests/conftest.py` (sau `import pytest`):

```python
@pytest.fixture(autouse=True)
def _ensure_warranty_subscribers():
    """Đăng ký lại subscriber production trước mỗi test warranty.

    Test của app khác gọi `event_bus.clear()` sẽ xoá luôn handler
    `_on_best_tracking_updated` đăng ký lúc `ready()`. `register()` idempotent
    nên gọi lại ở đây là an toàn, và test warranty hết phụ thuộc thứ tự chạy.
    """
    from apps.warranty import subscribers

    subscribers.register()
    yield
```

- [ ] **Step 6: Chạy test để xác nhận PASS**

Run: `cd backend && pytest apps/warranty/tests/test_tracking_subscriber.py -v`
Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/warranty/subscribers.py backend/apps/warranty/apps.py backend/apps/warranty/tests/conftest.py backend/apps/warranty/tests/test_tracking_subscriber.py
git commit -m "feat(warranty): apply Best tracking status from event bus in realtime"
```

---

### Task 5: Cron ghi kèm mốc thời gian và tuân guard

Không sửa thì cron sẽ đè ngược lên dữ liệu webhook mới hơn.

**Files:**
- Modify: `backend/apps/warranty/tracking_sync_service.py:18-40` và `:98-113`
- Test: `backend/apps/warranty/tests/test_tracking_sync.py`

**Interfaces:**
- Consumes: `JOURNEY_CODE_FIELDS`, `should_apply_best_status` (Task 2); 3 field `*_ms` (Task 3).
- Produces: `_latest_status_by_code(results) -> dict[str, tuple[str, int]]` (đổi kiểu trả về, trước là `dict[str, str]`).

- [ ] **Step 1: Viết test thất bại**

Thêm vào cuối `backend/apps/warranty/tests/test_tracking_sync.py`:

```python
@pytest.mark.django_db
def test_sync_records_modified_at_ms(order_factory, monkeypatch):
    _account()
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.new_tracking_code = "B-NEW"
    j.save()

    def fake_trace(self, codes, lang="en-US"):
        return {"data": [_trace_result("B-NEW", "601")]}

    monkeypatch.setattr(
        "apps.best_express.infrastructure.best_client.BestExpressClient.trace_query",
        fake_trace,
    )
    WarrantyTrackingSyncService.sync()

    j.refresh_from_db()
    assert j.new_tracking_best_status == "601"
    # `_trace_result` đặt log mới nhất ở modified_at=2.
    assert j.new_tracking_best_status_ms == 2


@pytest.mark.django_db
def test_sync_does_not_overwrite_newer_webhook_status(order_factory, monkeypatch):
    _account()
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.new_tracking_code = "B-NEW"
    # Webhook đã ghi trạng thái mới hơn hẳn (mốc 999) — cron trace trả mốc 2.
    j.new_tracking_best_status = "666"
    j.new_tracking_best_status_ms = 999
    j.save()

    def fake_trace(self, codes, lang="en-US"):
        return {"data": [_trace_result("B-NEW", "302")]}

    monkeypatch.setattr(
        "apps.best_express.infrastructure.best_client.BestExpressClient.trace_query",
        fake_trace,
    )
    WarrantyTrackingSyncService.sync()

    j.refresh_from_db()
    assert j.new_tracking_best_status == "666"
    assert j.new_tracking_best_status_ms == 999
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd backend && pytest apps/warranty/tests/test_tracking_sync.py -v`
Expected: `test_sync_records_modified_at_ms` FAIL với `assert 0 == 2` (cron chưa ghi `*_ms`); `test_sync_does_not_overwrite_newer_webhook_status` FAIL vì cron đè `"302"` lên `"666"`.

- [ ] **Step 3: Đổi `_CODE_FIELDS` và `_latest_status_by_code`**

Trong `backend/apps/warranty/tracking_sync_service.py`, thay import và khối `_CODE_FIELDS`:

```python
from apps.warranty.models import WarrantyClaimStatus, WarrantyJourney
from apps.warranty.tracking_status import JOURNEY_CODE_FIELDS, should_apply_best_status
```

Xoá hẳn khối `_CODE_FIELDS = (...)` cục bộ (giờ dùng `JOURNEY_CODE_FIELDS` chung với subscriber).

Thay `_latest_status_by_code`:

```python
def _latest_status_by_code(results: list[dict]) -> dict[str, tuple[str, int]]:
    """TraceQuery results → {mã: (mã trạng thái mới nhất, mốc modified_at ms)}."""
    latest: dict[str, tuple[str, int]] = {}
    for item in results:
        code = str(item.get("code_best") or item.get("merchant_order_code") or "").strip()
        if not code:
            continue
        logs = [log for log in (item.get("traceLogs") or []) if isinstance(log, dict)]
        if not logs:
            continue
        newest = max(logs, key=lambda log: log.get("modified_at") or 0)
        status = str(newest.get("status") or "").strip()
        if not status:
            continue
        try:
            modified_ms = int(newest.get("modified_at") or 0)
        except (TypeError, ValueError):
            modified_ms = 0
        latest[code] = (status, modified_ms)
    return latest
```

- [ ] **Step 4: Sửa vòng lặp áp trạng thái**

Trong `WarrantyTrackingSyncService.sync`, thay đoạn gom mã (dùng `_CODE_FIELDS`):

```python
        codes: set[str] = set()
        for j in journeys:
            for code_field, _, _ in JOURNEY_CODE_FIELDS:
                code = (getattr(j, code_field) or "").strip()
                if code:
                    codes.add(code)
```

và thay vòng lặp cập nhật ở cuối:

```python
        for j in journeys:
            changed: list[str] = []
            for code_field, status_field, ms_field in JOURNEY_CODE_FIELDS:
                code = (getattr(j, code_field) or "").strip()
                found = latest.get(code) if code else None
                if not found:
                    continue
                new_status, new_ms = found
                if getattr(j, status_field) == new_status:
                    continue
                # Webhook có thể đã ghi trạng thái mới hơn — cron không được đè ngược.
                if not should_apply_best_status(getattr(j, ms_field), new_ms):
                    continue
                setattr(j, status_field, new_status)
                setattr(j, ms_field, new_ms)
                changed += [status_field, ms_field]
            if changed:
                j.tracking_synced_at = now
                j.save(update_fields=[*changed, "tracking_synced_at", "updated_at"])
                updated += 1
```

- [ ] **Step 5: Chạy test để xác nhận PASS**

Run: `cd backend && pytest apps/warranty/tests/test_tracking_sync.py -v`
Expected: toàn bộ PASS, gồm cả các test cũ.

- [ ] **Step 6: Chạy toàn bộ test warranty + best_express**

Run: `cd backend && pytest apps/warranty apps/best_express -q`
Expected: toàn bộ PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/warranty/tracking_sync_service.py backend/apps/warranty/tests/test_tracking_sync.py
git commit -m "fix(warranty): stop cron from overwriting newer webhook status"
```

---

### Task 6: Kiểm chứng end-to-end qua webhook thật

**Files:**
- Test: `backend/apps/best_express/tests/test_webhook_view.py`

**Interfaces:**
- Consumes: toàn bộ Task 1-5.

- [ ] **Step 1: Viết test thất bại**

Thêm vào cuối `backend/apps/best_express/tests/test_webhook_view.py`. File này đã có sẵn `WEBHOOK_URL`, `make_account(webhook_token=...)` và `auth_client(token=...)` ở đầu file — dùng lại, đừng dựng mới.

File nằm ngoài `apps/warranty/tests` nên KHÔNG thấy fixture `order_factory` của conftest warranty; test tự dựng `Order` tối thiểu (`Order.channel` và `Order.customer` là FK PROTECT, bắt buộc có).

```python
def _warranty_order():
    """Order tối thiểu để gắn WarrantyClaim — Channel/Customer là FK PROTECT."""
    from apps.channels_core.models import Channel
    from apps.customers.models import Customer
    from apps.orders.models import Order

    suffix = uuid.uuid4().hex[:8]
    channel = Channel.objects.create(code=f"best-e2e-{suffix}", name=f"Best E2E {suffix}")
    customer = Customer.objects.create(
        code=f"KH-BEST-{suffix}",
        full_name="Khách E2E",
        phone_raw=f"09{suffix[:8]}",
        phone_normalized=f"09{suffix[:8]}",
    )
    return Order.objects.create(channel=channel, customer=customer)


@pytest.mark.django_db
def test_webhook_updates_warranty_journey(django_capture_on_commit_callbacks):
    """Webhook Best về → badge hành trình bảo hành đổi ngay, không đợi cron."""
    from apps.warranty import subscribers
    from apps.warranty.journey_service import WarrantyJourneyService
    from apps.warranty.models import WarrantyClaim
    from apps.warranty.tracking_status import journey_vc_groups

    # Test app khác có thể đã gọi event_bus.clear() — đăng ký lại cho chắc.
    subscribers.register()

    make_account()
    code = f"848{uuid.uuid4().hex[:10]}"
    claim = WarrantyClaim.objects.create(order=_warranty_order())
    journey = WarrantyJourneyService.add_journey(claim)
    journey.new_tracking_code = code
    journey.save()

    # publish_on_commit chỉ chạy khi transaction commit; trong test bọc transaction
    # thì phải bắt callback và chạy tay.
    with django_capture_on_commit_callbacks(execute=True):
        response = auth_client().post(
            WEBHOOK_URL,
            {
                "code_best": code,
                "merchant_order_code": "",
                "status": "666",
                "modified_at": 1700000000000,
            },
            format="json",
        )

    assert response.status_code == 200
    assert response.json()["Result"] == 1

    journey.refresh_from_db()
    assert journey.new_tracking_best_status == "666"
    assert journey.new_tracking_best_status_ms == 1700000000000
    assert journey_vc_groups(journey)["new"] == "delivered"
```

`Order` có thể còn field bắt buộc khác ngoài `channel`/`customer` — nếu `_warranty_order()` nổ vì thiếu field, mở `apps/warranty/tests/conftest.py::order_factory` xem nó truyền thêm gì và bổ sung y hệt.

- [ ] **Step 2: Chạy test để xác nhận FAIL nếu chưa nối đủ**

Run: `cd backend && pytest apps/best_express/tests/test_webhook_view.py -v -k warranty_journey`
Expected: PASS nếu Task 1-5 đã đúng. Nếu FAIL, đọc lỗi để biết mắt xích nào đứt — đây là test canh toàn tuyến.

- [ ] **Step 3: Chạy toàn bộ suite backend**

Run: `cd backend && pytest -q`
Expected: toàn bộ PASS.

- [ ] **Step 4: Commit và đẩy nhánh**

```bash
git add backend/apps/best_express/tests/test_webhook_view.py
git commit -m "test(best-express): cover webhook to warranty journey end to end"
git push -u origin feat/warranty-best-tracking-realtime
```

---

## Ghi chú cho người thực hiện

- Thứ tự bắt buộc: Task 1 → 2 → 3 → 4 → 5 → 6. Task 4 cần cả 1, 2, 3; Task 5 cần 2, 3.
- `publish_on_commit` là cố ý — consumer phải đọc được dữ liệu đã commit. Đừng đổi sang `publish` cho test dễ pass; dùng `django_capture_on_commit_callbacks` thay vì đổi code production.
- Đừng "tiện tay" sửa cơ chế `unmatched` của `best_express` để bớt event `processed=False`. Spec cố ý giữ nguyên: sửa nó nghĩa là `best_express` phải biết `warranty` tồn tại.
- Sau khi merge, tính năng vẫn im lặng cho tới khi `BEST_EXPRESS_WEBHOOK_TOKEN` được đặt trên `.env` production, chạy `best_sync_account`, và sale BEST cấu hình URL `https://customer.delax.vn/api/best-express/webhook` phía họ. Kiểm chứng: Django admin → `Best webhook events` có dòng mới.
