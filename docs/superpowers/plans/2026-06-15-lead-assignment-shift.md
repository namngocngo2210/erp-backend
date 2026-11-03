# Lead Assignment by Work Shift — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mở rộng chia đơn tự động từ `(ngày, sheet)` sang `(ngày, ca, sheet)` với 3 ca (Sáng/Chiều/Tối), khung giờ cấu hình được trong UI; ca của một đơn xác định theo giờ hệ thống lúc auto-assign.

**Architecture:** Thêm bảng global `shift_windows` (3 dòng, khung giờ chỉnh được) + `ShiftService.current_shift()` dò ca theo `timezone.localtime()`. Thêm cột `shift` vào `LeadAssignmentShare` (unique theo `date,shift,sheet,user`, bộ đếm cân bằng độc lập từng ca). `pick_staff_for`/`assign_if_unassigned` thêm chiều shift; thuật toán largest-remainder giữ nguyên. `workload`/`transfer` KHÔNG đổi (giữ ở mức sheet — Order không lưu ca). API `/shares` thêm tham số `shift`; thêm `/shift-windows` GET/PUT. FE thêm selector ca + khối cấu hình khung giờ.

**Tech Stack:** Django 5 + DRF, PostgreSQL, React (Vite) + TypeScript, shadcn/ui.

**Spec:** `docs/superpowers/specs/2026-06-15-lead-assignment-shift-design.md`

---

## Cách chạy test (dùng lại ở mọi task BE)

Backend test (ephemeral container off prod image — prod container KHÔNG có thư mục tests/ruff):

```bash
cd /home/desi/desi-order
docker inspect desi-web -f '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E '^[A-Z_]+=' | grep -vE '^PATH=|^LANG=|^GPG|^PYTHON' > /tmp/web.env
run_tests() {  # $1 = pytest paths
  docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
    -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
    -v "$(pwd)/backend:/app" -w /app desi-order-web \
    sh -c "mkdir -p /tmp/prom_multiproc && rm -f /tmp/prom_multiproc/* && python -m pytest $1 --reuse-db -q"
}
```

Ruff (host): `uv run --no-project --with ruff ruff check backend/apps/orders/<file>`

Migration tạo bằng ephemeral container: thay `python -m pytest ...` bằng `python manage.py makemigrations orders`.

---

## File Structure

**Backend — tạo mới:**
- `backend/apps/orders/shift_rules.py` — hằng số ca: `SHIFT_CHOICES`, `SHIFT_LABELS`, `DEFAULT_SHIFT_WINDOWS`.
- `backend/apps/orders/application/services/shift_service.py` — `ShiftService.current_shift()` / `shift_for_time()` + cache.
- `backend/apps/orders/tests/test_shift_service.py`
- `backend/apps/orders/tests/test_shift_windows_api.py`
- 2 file migration (model `ShiftWindow` + alter `LeadAssignmentShare`).

**Backend — sửa:**
- `backend/apps/orders/models.py` — model `ShiftWindow` mới; thêm cột `shift` + Meta của `LeadAssignmentShare`.
- `backend/apps/orders/application/services/lead_assignment_service.py` — `pick_staff_for`/`assign_if_unassigned` thêm shift.
- `backend/apps/orders/transport/views/order_views_impl/lead_assignment_view.py` — `/shares` shift-aware + view `/shift-windows`.
- `backend/apps/orders/urls.py` — route `/shift-windows`.
- `backend/apps/orders/tests/test_lead_assignment_service.py` — cập nhật chữ ký gọi `pick_staff_for`.

**Frontend — sửa:**
- `frontend/src/lib/api/lead-assignment.ts` — type + hàm shift; client `/shift-windows`.
- `frontend/src/pages/LeadAssignmentPage.tsx` — selector ca + badge ca hiện tại + khối cấu hình khung giờ.

---

## Task 1: Hằng số ca + model `ShiftWindow` + migration + seed

**Files:**
- Create: `backend/apps/orders/shift_rules.py`
- Modify: `backend/apps/orders/models.py` (thêm class `ShiftWindow` cuối file, sau `LeadAssignmentShare`)
- Create: migration `backend/apps/orders/migrations/00XX_shift_window.py` (auto + thêm seed)
- Test: `backend/apps/orders/tests/test_shift_service.py` (chỉ phần model ở task này; service ở Task 2)

- [ ] **Step 1: Tạo `shift_rules.py`**

```python
"""Hằng số ca làm việc dùng cho chia đơn tự động."""
from __future__ import annotations

from datetime import time

SHIFT_CHOICES = ("morning", "afternoon", "evening")

SHIFT_LABELS = {
    "morning": "Sáng",
    "afternoon": "Chiều",
    "evening": "Tối",
}

# (shift, start_time, end_time) — end_time=00:00 của ca cuối nghĩa là nửa đêm.
DEFAULT_SHIFT_WINDOWS = (
    ("morning", time(0, 0), time(12, 0)),
    ("afternoon", time(12, 0), time(18, 0)),
    ("evening", time(18, 0), time(0, 0)),
)
```

- [ ] **Step 2: Thêm model `ShiftWindow` vào `models.py`** (cuối file)

```python
class ShiftWindow(models.Model):
    """Khung giờ 3 ca làm việc (global, không theo ngày). Đúng 3 dòng cố định."""

    from apps.orders.shift_rules import SHIFT_CHOICES as _SHIFT_KEYS  # noqa: PLC0415

    SHIFT_CHOICES = [
        ("morning", "Sáng"),
        ("afternoon", "Chiều"),
        ("evening", "Tối"),
    ]

    shift = models.CharField(max_length=16, choices=SHIFT_CHOICES, unique=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "shift_windows"
        verbose_name = "Shift Window"

    def __str__(self):
        return f"{self.shift} {self.start_time}-{self.end_time}"
```

> Bỏ dòng `from ... import _SHIFT_KEYS` nếu không dùng — chỉ giữ `SHIFT_CHOICES` literal ở trên cho gọn (model tự định nghĩa choices, tránh import vòng). Giữ literal.

- [ ] **Step 3: Tạo migration model + seed**

Chạy (ephemeral container, lệnh makemigrations như mục đầu plan):
```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c "python manage.py makemigrations orders"
```
Expected: tạo file `00XX_shiftwindow.py` (tên số tăng theo migration mới nhất).

- [ ] **Step 4: Thêm data migration seed vào CÙNG file migration vừa tạo**

Thêm `migrations.RunPython` vào list `operations` (sau `CreateModel`):
```python
from datetime import time

def seed_shift_windows(apps, schema_editor):
    ShiftWindow = apps.get_model("orders", "ShiftWindow")
    defaults = [
        ("morning", time(0, 0), time(12, 0)),
        ("afternoon", time(12, 0), time(18, 0)),
        ("evening", time(18, 0), time(0, 0)),
    ]
    for shift, start, end in defaults:
        ShiftWindow.objects.get_or_create(
            shift=shift, defaults={"start_time": start, "end_time": end}
        )

def unseed(apps, schema_editor):
    apps.get_model("orders", "ShiftWindow").objects.all().delete()
```
Và thêm `migrations.RunPython(seed_shift_windows, unseed)` vào cuối `operations`.

- [ ] **Step 5: Viết test seed + ràng buộc unique**

`backend/apps/orders/tests/test_shift_service.py`:
```python
from datetime import time

from django.test import TestCase

from apps.orders.models import ShiftWindow


class ShiftWindowModelTests(TestCase):
    def test_seed_creates_three_shifts(self):
        # Migration đã seed; test_db áp dụng migration nên 3 dòng tồn tại.
        self.assertEqual(ShiftWindow.objects.count(), 3)
        ev = ShiftWindow.objects.get(shift="evening")
        self.assertEqual(ev.start_time, time(18, 0))
        self.assertEqual(ev.end_time, time(0, 0))
```

- [ ] **Step 6: Chạy migration + test**

```bash
run_tests "apps/orders/tests/test_shift_service.py::ShiftWindowModelTests -v"
```
Expected: PASS (3 dòng seed sẵn). Nếu fail vì `--reuse-db` giữ DB cũ chưa có migration mới → chạy lại không `--reuse-db` một lần: sửa tạm `run_tests` bỏ `--reuse-db`, hoặc `... -q --create-db`.

- [ ] **Step 7: Ruff + Commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/shift_rules.py backend/apps/orders/models.py
git add backend/apps/orders/shift_rules.py backend/apps/orders/models.py backend/apps/orders/migrations/ backend/apps/orders/tests/test_shift_service.py
git commit -m "feat(orders): add ShiftWindow model + shift constants + seed"
```

---

## Task 2: `ShiftService.current_shift()` / `shift_for_time()`

**Files:**
- Create: `backend/apps/orders/application/services/shift_service.py`
- Test: `backend/apps/orders/tests/test_shift_service.py` (thêm class mới)

- [ ] **Step 1: Viết test trước**

Thêm vào `test_shift_service.py`:
```python
from datetime import time

from apps.orders.application.services.shift_service import ShiftService


class ShiftForTimeTests(TestCase):
    def test_boundaries(self):
        # default windows: morning[00:00,12:00) afternoon[12:00,18:00) evening[18:00,24:00)
        self.assertEqual(ShiftService.shift_for_time(time(0, 0)), "morning")
        self.assertEqual(ShiftService.shift_for_time(time(11, 59)), "morning")
        self.assertEqual(ShiftService.shift_for_time(time(12, 0)), "afternoon")
        self.assertEqual(ShiftService.shift_for_time(time(17, 59)), "afternoon")
        self.assertEqual(ShiftService.shift_for_time(time(18, 0)), "evening")
        self.assertEqual(ShiftService.shift_for_time(time(23, 59)), "evening")

    def test_custom_windows(self):
        ShiftWindow.objects.filter(shift="morning").update(start_time=time(6, 0))
        ShiftWindow.objects.filter(shift="evening").update(start_time=time(20, 0))
        ShiftService.invalidate_cache()
        # 05:00 nằm trước ca sáng (06:00) → rơi vào ca có start lớn nhất ≤ now = evening (wrap)
        self.assertEqual(ShiftService.shift_for_time(time(5, 0)), "evening")
        self.assertEqual(ShiftService.shift_for_time(time(21, 0)), "evening")

    def test_fallback_when_empty(self):
        ShiftWindow.objects.all().delete()
        ShiftService.invalidate_cache()
        self.assertEqual(ShiftService.shift_for_time(time(13, 0)), "morning")
```

- [ ] **Step 2: Chạy test → fail**

```bash
run_tests "apps/orders/tests/test_shift_service.py::ShiftForTimeTests -v"
```
Expected: FAIL (`No module named ... shift_service`).

- [ ] **Step 3: Viết `shift_service.py`**

```python
"""Xác định ca làm việc hiện tại theo khung giờ cấu hình (ShiftWindow)."""
from __future__ import annotations

import logging
from datetime import time

from django.utils import timezone

from apps.orders.models import ShiftWindow

logger = logging.getLogger(__name__)

_FALLBACK_SHIFT = "morning"


class ShiftService:
    # cache (start_time, shift) sắp tăng dần theo start_time; None = chưa nạp.
    _windows_cache: list[tuple[time, str]] | None = None

    @classmethod
    def invalidate_cache(cls) -> None:
        cls._windows_cache = None

    @classmethod
    def _windows(cls) -> list[tuple[time, str]]:
        if cls._windows_cache is None:
            rows = list(ShiftWindow.objects.values_list("start_time", "shift"))
            cls._windows_cache = sorted(rows, key=lambda r: r[0])
        return cls._windows_cache

    @classmethod
    def shift_for_time(cls, t: time) -> str:
        """Ca có start_time lớn nhất mà ≤ t. Nếu t nhỏ hơn mọi start → ca cuối (wrap).

        Trả _FALLBACK_SHIFT nếu chưa cấu hình ca nào.
        """
        windows = cls._windows()
        if not windows:
            logger.warning("ShiftWindow chưa cấu hình; fallback %s", _FALLBACK_SHIFT)
            return _FALLBACK_SHIFT
        chosen = windows[-1][1]  # mặc định: ca cuối (xử lý wrap khi t < start ca đầu)
        for start, shift in windows:
            if start <= t:
                chosen = shift
        return chosen

    @classmethod
    def current_shift(cls) -> str:
        return cls.shift_for_time(timezone.localtime().time())
```

- [ ] **Step 4: Chạy test → pass**

```bash
run_tests "apps/orders/tests/test_shift_service.py -v"
```
Expected: PASS tất cả (gồm ShiftWindowModelTests + ShiftForTimeTests).

> Lưu ý cache: `_windows_cache` là class attr in-process. Trong test phải gọi `ShiftService.invalidate_cache()` sau khi sửa ShiftWindow (đã có trong test). Trong runtime, PUT `/shift-windows` (Task 6) phải gọi invalidate.

- [ ] **Step 5: Ruff + Commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/application/services/shift_service.py
git add backend/apps/orders/application/services/shift_service.py backend/apps/orders/tests/test_shift_service.py
git commit -m "feat(orders): add ShiftService.current_shift based on shift windows"
```

---

## Task 3: Thêm cột `shift` vào `LeadAssignmentShare` + migration backfill

**Files:**
- Modify: `backend/apps/orders/models.py` (`LeadAssignmentShare`)
- Create: migration alter + data backfill

- [ ] **Step 1: Sửa model `LeadAssignmentShare`**

Thêm import literal + cột + Meta. Sửa class hiện tại:
```python
    SHEET_CHOICES = [
        ("sheet1", "Landing xác nhận đơn"),
        ("sheet2", "Messenger xác nhận đơn"),
        ("sheet3", "Tư vấn"),
    ]
    SHIFT_CHOICES = [
        ("morning", "Sáng"),
        ("afternoon", "Chiều"),
        ("evening", "Tối"),
    ]

    date = models.DateField()
    shift = models.CharField(max_length=16, choices=SHIFT_CHOICES, default="morning")
    sheet = models.CharField(max_length=16, choices=SHEET_CHOICES)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="lead_assignment_shares",
    )
    weight = models.PositiveSmallIntegerField()
    assigned_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "lead_assignment_shares"
        unique_together = ("date", "shift", "sheet", "user")
        indexes = [
            models.Index(fields=["date", "shift", "sheet"], name="lead_assign_d_sh_sheet_idx")
        ]
```
> `default="morning"` giữ trên field để dòng mới (và backfill bước schema) hợp lệ; reconcile ở API luôn set shift tường minh nên default không gây nhầm.

- [ ] **Step 2: Tạo migration tự động**

```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c "python manage.py makemigrations orders"
```
Expected: migration với `AddField shift`, `AlterUniqueTogether`, `RemoveIndex lead_assign_date_sheet_idx`, `AddIndex lead_assign_d_sh_sheet_idx`.

- [ ] **Step 3: Chèn data migration backfill vào CÙNG file, ĐÚNG THỨ TỰ**

Sửa `operations` thủ công để thứ tự là: (1) `AddField shift` (nullable tạm) → (2) `RunPython` backfill nhân 3 ca → (3) `AlterField` shift thành NOT NULL → (4) đổi unique_together + index.

Cách an toàn: để Django tạo AddField với default="morning" (NOT NULL ngay). Sau đó RunPython nhân bản 2 ca còn lại. Thêm trước `AlterUniqueTogether`:
```python
def fanout_shifts(apps, schema_editor):
    LeadAssignmentShare = apps.get_model("orders", "LeadAssignmentShare")
    # mọi dòng cũ đã = "morning" nhờ default. Nhân bản sang afternoon/evening cùng weight, count=0.
    existing = list(LeadAssignmentShare.objects.all())
    for row in existing:
        for shift in ("afternoon", "evening"):
            LeadAssignmentShare.objects.get_or_create(
                date=row.date, shift=shift, sheet=row.sheet, user_id=row.user_id,
                defaults={"weight": row.weight, "assigned_count": 0},
            )

def noop_reverse(apps, schema_editor):
    apps.get_model("orders", "LeadAssignmentShare").objects.exclude(shift="morning").delete()
```
Đặt `migrations.RunPython(fanout_shifts, noop_reverse)` **sau** `AddField`, **trước** `AlterUniqueTogether`/`AddIndex`. (AlterUniqueTogether mới gồm shift nên không xung đột khi đã có 3 ca.)

- [ ] **Step 4: Viết test migration backfill** (trong `test_lead_assignment_service.py`, class mới)

```python
class ShiftColumnTests(TestCase):
    def test_default_shift_is_morning(self):
        u = User.objects.create(username="sale_x")
        s = LeadAssignmentShare.objects.create(
            date=timezone.localdate(), sheet="sheet1", user=u, weight=10
        )
        self.assertEqual(s.shift, "morning")

    def test_unique_per_shift(self):
        u = User.objects.create(username="sale_y")
        d = timezone.localdate()
        LeadAssignmentShare.objects.create(date=d, shift="morning", sheet="sheet1", user=u, weight=10)
        # cùng (date, sheet, user) nhưng ca khác → hợp lệ
        LeadAssignmentShare.objects.create(date=d, shift="evening", sheet="sheet1", user=u, weight=5)
        self.assertEqual(LeadAssignmentShare.objects.filter(user=u).count(), 2)
```

- [ ] **Step 5: Chạy migration + test** (cần DB mới để áp migration; chạy `--create-db` lần này)

```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c "mkdir -p /tmp/prom_multiproc && rm -f /tmp/prom_multiproc/* && python -m pytest apps/orders/tests/test_lead_assignment_service.py::ShiftColumnTests -q --create-db"
```
Expected: PASS.

- [ ] **Step 6: Ruff + Commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/models.py
git add backend/apps/orders/models.py backend/apps/orders/migrations/ backend/apps/orders/tests/test_lead_assignment_service.py
git commit -m "feat(orders): add shift dimension to LeadAssignmentShare (+ backfill migration)"
```

---

## Task 4: `LeadAssignmentService` shift-aware

**Files:**
- Modify: `backend/apps/orders/application/services/lead_assignment_service.py`
- Modify: `backend/apps/orders/tests/test_lead_assignment_service.py` (cập nhật lời gọi cũ)

- [ ] **Step 1: Cập nhật test hiện có + thêm test cô lập theo ca**

Trong `PickStaffTests`, mọi lời gọi `pick_staff_for(self.today, "sheetX")` → `pick_staff_for(self.today, "morning", "sheetX")`, và mọi `LeadAssignmentShare.objects.create(date=..., sheet=..., ...)` thêm `shift="morning"`. Sửa cả `test_empty_returns_none`, `test_distribution_follows_weights_per_sheet`, `test_remainder_stress`.

Thêm test mới:
```python
def test_shift_isolation(self):
    d = self.today
    LeadAssignmentShare.objects.create(date=d, shift="morning", sheet="sheet1", user=self.a, weight=10)
    LeadAssignmentShare.objects.create(date=d, shift="evening", sheet="sheet1", user=self.b, weight=10)
    # ca evening chỉ chọn b, không đụng a
    for _ in range(5):
        self.assertEqual(LeadAssignmentService.pick_staff_for(d, "evening", "sheet1"), self.b)
    self.assertIsNone(LeadAssignmentService.pick_staff_for(d, "afternoon", "sheet1"))
```

Thêm test `assign_if_unassigned` dùng ca hiện tại (mock):
```python
from unittest import mock

class AssignByShiftTests(TestCase):
    def setUp(self):
        self.a = User.objects.create(username="sale_a")
        self.today = timezone.localdate()

    @mock.patch(
        "apps.orders.application.services.lead_assignment_service.ShiftService.current_shift",
        return_value="evening",
    )
    def test_assign_uses_current_shift(self, _m):
        from apps.orders.models import Order
        LeadAssignmentShare.objects.create(
            date=self.today, shift="evening", sheet="sheet1", user=self.a, weight=10
        )
        order = Order.objects.create(
            order_source="landing", name="A", phone="0900000000", raw_address="HN"
        )
        LeadAssignmentService.assign_if_unassigned(order)
        order.refresh_from_db()
        self.assertEqual(order.staff_id, self.a.id)
```
> Kiểm tra trường bắt buộc của `Order.objects.create` — nếu model cần thêm field NOT NULL, bổ sung tối thiểu cho hợp lệ (đọc `models.py` Order). Giữ đơn thuộc sheet1 (landing + đủ name/phone/address).

- [ ] **Step 2: Chạy test → fail**

```bash
run_tests "apps/orders/tests/test_lead_assignment_service.py -q"
```
Expected: FAIL (signature `pick_staff_for` cũ chỉ nhận 2 tham số / chưa import ShiftService).

- [ ] **Step 3: Sửa service**

Trong `lead_assignment_service.py`:
- Thêm import: `from apps.orders.application.services.shift_service import ShiftService`
- Sửa `pick_staff_for`:
```python
    @staticmethod
    def pick_staff_for(date, shift, sheet):
        with transaction.atomic():
            shares = list(
                LeadAssignmentShare.objects.select_for_update()
                .filter(date=date, shift=shift, sheet=sheet, weight__gt=0)
                .order_by("id")
            )
            if not shares:
                return None
            total_weight = sum(s.weight for s in shares)
            total_assigned = sum(s.assigned_count for s in shares) + 1

            def deficit(s):
                target = s.weight / total_weight * total_assigned
                return (target - s.assigned_count, s.weight, -s.id)

            best = max(shares, key=deficit)
            best.assigned_count += 1
            best.save(update_fields=["assigned_count", "updated_at"])
            return best.user
```
- Sửa `assign_if_unassigned`: thêm `shift = ShiftService.current_shift()` trước khi gọi, và truyền vào:
```python
        with transaction.atomic():
            shift = ShiftService.current_shift()
            user = LeadAssignmentService.pick_staff_for(timezone.localdate(), shift, sheet)
            if user is None:
                return
            order.staff = user
            order.save(update_fields=["staff", "updated_at"])
            logger.info(
                "Lead auto-assigned: order_id=%s shift=%s sheet=%s staff_id=%s",
                order.id, shift, sheet, user.id,
            )
```
- `workload` và `transfer`: KHÔNG đổi.

- [ ] **Step 4: Chạy test → pass**

```bash
run_tests "apps/orders/tests/test_lead_assignment_service.py -q"
```
Expected: PASS.

- [ ] **Step 5: Ruff + Commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/application/services/lead_assignment_service.py
git add backend/apps/orders/application/services/lead_assignment_service.py backend/apps/orders/tests/test_lead_assignment_service.py
git commit -m "feat(orders): pick_staff_for honors current shift"
```

---

## Task 5: API `/shares` shift-aware

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/lead_assignment_view.py`
- Test: `backend/apps/orders/tests/test_shift_windows_api.py` (dùng chung file cho API ca; phần shares ở đây)

- [ ] **Step 1: Viết test GET/PUT theo shift**

`test_shift_windows_api.py` (tạo client đăng nhập theo pattern test API hiện có — xem một test API khác trong `apps/orders/tests/` để lấy cách auth/login chuẩn; dùng `APIClient` + force_authenticate user có section `lead-assignment`):
```python
from django.test import TestCase
from rest_framework.test import APIClient

from apps.orders.models import LeadAssignmentShare
from apps.users.constants import DepartmentID
from apps.users.models import User


class SharesByShiftTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create(username="admin1", is_staff=True, is_superuser=True)
        self.client.force_authenticate(self.admin)
        self.sale = User.objects.create(
            username="sale1", department_id=DepartmentID.SALES, is_active=True
        )

    def test_put_get_roundtrip_per_shift(self):
        payload = {
            "date": "2026-06-15",
            "shift": "evening",
            "rows": [{"user_id": self.sale.id, "weights": {"sheet1": 100}}],
        }
        r = self.client.put("/api/orders/lead-assignment/shares", payload, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        # đúng 1 dòng cho ca evening, không tạo ở ca khác
        self.assertEqual(
            LeadAssignmentShare.objects.filter(shift="evening", sheet="sheet1").count(), 1
        )
        self.assertEqual(LeadAssignmentShare.objects.filter(shift="morning").count(), 0)
        g = self.client.get("/api/orders/lead-assignment/shares?date=2026-06-15&shift=evening")
        self.assertEqual(g.json()["shift"], "evening")
        self.assertEqual(g.json()["rows"][0]["cells"]["sheet1"]["weight"], 100)
```
> Nếu superuser không tự thoả `require_section("lead-assignment")`, đọc `apps/users/permissions.py require_section` + một test API hiện có để gán section đúng cách. Điều chỉnh setUp cho khớp.

- [ ] **Step 2: Chạy test → fail**

```bash
run_tests "apps/orders/tests/test_shift_windows_api.py::SharesByShiftTests -q --create-db"
```
Expected: FAIL (PUT chưa nhận `shift`; GET response chưa có `shift`).

- [ ] **Step 3: Sửa view `lead_assignment_view.py`**

- `_serialize_day(date)` → `_serialize_day(date, shift)`: filter thêm `shift=shift`; thêm `"shift": shift` và `"current_shift": ShiftService.current_shift()` vào dict trả về.
- `_UpsertSerializer`: thêm field `shift = serializers.ChoiceField(choices=[s[0] for s in LeadAssignmentShare.SHIFT_CHOICES])`.
- View `lead_assignment_shares`:
  - GET: đọc `shift = request.query_params.get("shift") or ShiftService.current_shift()`; validate thuộc SHIFT_CHOICES (else 400); `return Response(_serialize_day(date, shift))`.
  - PUT: lấy `shift = serializer.validated_data["shift"]`; reconcile chỉ trong `(date, shift)`:
    ```python
    LeadAssignmentShare.objects.filter(date=date, shift=shift).exclude(
        user_id__in=keep_user_ids
    ).delete()
    for r in rows:
        for sheet in SHEET_CHOICES:
            weight = int(r["weights"].get(sheet, 0) or 0)
            if weight > 0:
                LeadAssignmentShare.objects.update_or_create(
                    date=date, shift=shift, sheet=sheet, user_id=uid,
                    defaults={"weight": weight},
                )
            else:
                LeadAssignmentShare.objects.filter(
                    date=date, shift=shift, sheet=sheet, user_id=uid
                ).delete()
    return Response(_serialize_day(date, shift), status=status.HTTP_200_OK)
    ```
- Thêm import `from apps.orders.application.services.shift_service import ShiftService`.

- [ ] **Step 4: Chạy test → pass**

```bash
run_tests "apps/orders/tests/test_shift_windows_api.py::SharesByShiftTests -q"
```
Expected: PASS.

- [ ] **Step 5: Ruff + Commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/transport/views/order_views_impl/lead_assignment_view.py
git add backend/apps/orders/transport/views/order_views_impl/lead_assignment_view.py backend/apps/orders/tests/test_shift_windows_api.py
git commit -m "feat(orders): lead-assignment shares API is shift-aware"
```

---

## Task 6: API `/shift-windows` GET/PUT + validate phủ-kín-24h

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/lead_assignment_view.py` (thêm view + serializer)
- Modify: `backend/apps/orders/urls.py`
- Test: `backend/apps/orders/tests/test_shift_windows_api.py` (class mới)

- [ ] **Step 1: Viết test**

```python
class ShiftWindowsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create(username="admin2", is_staff=True, is_superuser=True)
        self.client.force_authenticate(self.admin)

    def test_get_returns_three(self):
        r = self.client.get("/api/orders/lead-assignment/shift-windows")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(r.json()["windows"]), 3)

    def test_put_valid_contiguous(self):
        payload = {"windows": [
            {"shift": "morning", "start_time": "06:00", "end_time": "12:00"},
            {"shift": "afternoon", "start_time": "12:00", "end_time": "18:00"},
            {"shift": "evening", "start_time": "18:00", "end_time": "06:00"},
        ]}
        r = self.client.put("/api/orders/lead-assignment/shift-windows", payload, format="json")
        self.assertEqual(r.status_code, 200, r.content)

    def test_put_rejects_gap(self):
        payload = {"windows": [
            {"shift": "morning", "start_time": "00:00", "end_time": "11:00"},
            {"shift": "afternoon", "start_time": "12:00", "end_time": "18:00"},
            {"shift": "evening", "start_time": "18:00", "end_time": "00:00"},
        ]}
        r = self.client.put("/api/orders/lead-assignment/shift-windows", payload, format="json")
        self.assertEqual(r.status_code, 400)
```
> Quy ước phủ-kín: ca đầu (sắp theo start) phải bắt đầu `00:00`; mỗi ca kế tiếp `start == end ca trước`; ca cuối `end == 00:00`. `test_put_valid_contiguous` ở trên có ca đầu 06:00 → KHÔNG bắt đầu 00:00. Sửa test này: ca đầu start `00:00`. Dùng:
> ```python
> {"shift": "morning", "start_time": "00:00", "end_time": "12:00"},
> {"shift": "afternoon", "start_time": "12:00", "end_time": "18:00"},
> {"shift": "evening", "start_time": "18:00", "end_time": "00:00"},
> ```

- [ ] **Step 2: Chạy test → fail**

```bash
run_tests "apps/orders/tests/test_shift_windows_api.py::ShiftWindowsApiTests -q --create-db"
```
Expected: FAIL (404 route chưa có).

- [ ] **Step 3: Thêm serializer + view**

Trong `lead_assignment_view.py`:
```python
from datetime import time as _time

from apps.orders.models import ShiftWindow

SHIFT_ORDER = ["morning", "afternoon", "evening"]


class _ShiftWindowRow(serializers.Serializer):
    shift = serializers.ChoiceField(choices=SHIFT_ORDER)
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()


class _ShiftWindowsSerializer(serializers.Serializer):
    windows = _ShiftWindowRow(many=True)

    def validate_windows(self, windows):
        if {w["shift"] for w in windows} != set(SHIFT_ORDER):
            raise serializers.ValidationError("Phải đủ đúng 3 ca: morning/afternoon/evening.")
        ordered = sorted(windows, key=lambda w: w["start_time"])
        midnight = _time(0, 0)
        if ordered[0]["start_time"] != midnight:
            raise serializers.ValidationError("Ca đầu tiên phải bắt đầu lúc 00:00.")
        for i, w in enumerate(ordered):
            nxt = ordered[i + 1]["start_time"] if i + 1 < len(ordered) else midnight
            if w["end_time"] != nxt:
                raise serializers.ValidationError(
                    "Các ca phải liền mạch, phủ kín 24h (end ca này = start ca kế; ca cuối end=00:00)."
                )
        return windows


def _serialize_windows():
    rows = {w.shift: w for w in ShiftWindow.objects.all()}
    return {
        "windows": [
            {
                "shift": s,
                "label": dict(ShiftWindow.SHIFT_CHOICES)[s],
                "start_time": rows[s].start_time.strftime("%H:%M") if s in rows else None,
                "end_time": rows[s].end_time.strftime("%H:%M") if s in rows else None,
            }
            for s in SHIFT_ORDER
        ],
        "current_shift": ShiftService.current_shift(),
    }


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated, require_section("lead-assignment")])
def lead_assignment_shift_windows(request):
    if request.method == "GET":
        return Response(_serialize_windows())
    serializer = _ShiftWindowsSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    with transaction.atomic():
        for w in serializer.validated_data["windows"]:
            ShiftWindow.objects.update_or_create(
                shift=w["shift"],
                defaults={"start_time": w["start_time"], "end_time": w["end_time"]},
            )
    ShiftService.invalidate_cache()
    return Response(_serialize_windows(), status=status.HTTP_200_OK)
```

- [ ] **Step 4: Thêm route** `backend/apps/orders/urls.py`

Import `lead_assignment_shift_windows` cùng nhóm import view lead-assignment, thêm dòng (cạnh các route lead-assignment khác):
```python
    path(
        "lead-assignment/shift-windows",
        lead_assignment_shift_windows,
        name="orders-lead-assignment-shift-windows",
    ),
```

- [ ] **Step 5: Chạy test → pass**

```bash
run_tests "apps/orders/tests/test_shift_windows_api.py -q"
```
Expected: PASS toàn bộ file.

- [ ] **Step 6: Ruff + Commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/transport/views/order_views_impl/lead_assignment_view.py backend/apps/orders/urls.py
git add backend/apps/orders/transport/views/order_views_impl/lead_assignment_view.py backend/apps/orders/urls.py backend/apps/orders/tests/test_shift_windows_api.py
git commit -m "feat(orders): add shift-windows config API with 24h coverage validation"
```

---

## Task 7: Frontend API client

**Files:**
- Modify: `frontend/src/lib/api/lead-assignment.ts`

- [ ] **Step 1: Thêm types + sửa hàm shares + thêm shift-windows client**

```ts
export type ShiftKey = "morning" | "afternoon" | "evening";

export interface ShiftWindow {
  shift: ShiftKey;
  label: string;
  start_time: string | null; // "HH:MM"
  end_time: string | null;
}
export interface ShiftWindowsResponse {
  windows: ShiftWindow[];
  current_shift: ShiftKey;
}
```

Sửa `LeadAssignmentDay` thêm `shift: ShiftKey; current_shift: ShiftKey;`.

Sửa `getLeadAssignment` nhận shift:
```ts
export async function getLeadAssignment(date: string, shift: ShiftKey): Promise<LeadAssignmentDay> {
  return fetchJson(`${BASE}/shares?date=${encodeURIComponent(date)}&shift=${shift}`);
}
export async function saveLeadAssignment(payload: {
  date: string;
  shift: ShiftKey;
  rows: SaveRow[];
}): Promise<LeadAssignmentDay> {
  return fetchJson(`${BASE}/shares`, { method: "PUT", body: JSON.stringify(payload) });
}
export async function getShiftWindows(): Promise<ShiftWindowsResponse> {
  return fetchJson(`${BASE}/shift-windows`);
}
export async function putShiftWindows(payload: {
  windows: { shift: ShiftKey; start_time: string; end_time: string }[];
}): Promise<ShiftWindowsResponse> {
  return fetchJson(`${BASE}/shift-windows`, { method: "PUT", body: JSON.stringify(payload) });
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend && npx tsc --noEmit -p tsconfig.json 2>&1 | head -30
```
Expected: chỉ còn lỗi ở `LeadAssignmentPage.tsx` (chưa truyền shift) — sẽ sửa ở Task 8. Không có lỗi khác trong file api.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api/lead-assignment.ts
git commit -m "feat(fe): lead-assignment api client supports shift + shift-windows"
```

---

## Task 8: Frontend `LeadAssignmentPage` — selector ca + cấu hình khung giờ

**Files:**
- Modify: `frontend/src/pages/LeadAssignmentPage.tsx`

> Đọc toàn bộ file trước khi sửa để giữ đúng style/state pattern (useState/useEffect, component shadcn đang dùng).

- [ ] **Step 1: Thêm state ca + badge ca hiện tại**

- Thêm `const [shift, setShift] = useState<ShiftKey>("morning")` (khởi tạo theo `current_shift` sau lần fetch đầu).
- `getLeadAssignment(date, shift)` trong effect; thêm `shift` vào deps → đổi ca/đổi ngày refetch.
- Khi nhận response lần đầu, set `shift` = `data.current_shift` nếu người dùng chưa chọn.
- `saveLeadAssignment` truyền thêm `shift`.

- [ ] **Step 2: UI selector 3 ca**

Thêm hàng nút/tab phía trên ma trận (dùng component shadcn sẵn có trong repo — Tabs hoặc nhóm Button):
```tsx
const SHIFTS: { key: ShiftKey; label: string }[] = [
  { key: "morning", label: "Sáng" },
  { key: "afternoon", label: "Chiều" },
  { key: "evening", label: "Tối" },
];
// ...
<div className="flex items-center gap-2">
  {SHIFTS.map((s) => (
    <Button
      key={s.key}
      variant={shift === s.key ? "default" : "outline"}
      onClick={() => setShift(s.key)}
    >
      {s.label}
    </Button>
  ))}
  {currentShift && (
    <span className="text-sm text-muted-foreground">
      Ca hiện tại: {SHIFTS.find((s) => s.key === currentShift)?.label}
    </span>
  )}
</div>
```

- [ ] **Step 3: Khối cấu hình khung giờ 3 ca**

Thêm 1 card riêng (dưới hoặc trên ma trận): load `getShiftWindows()`, render 3 hàng với 2 `<input type="time">` (start/end), nút "Lưu khung giờ" gọi `putShiftWindows`. Hiển thị lỗi 400 từ BE (phủ-kín-24h) qua toast/inline đang dùng trong trang.
```tsx
// state: const [windows, setWindows] = useState<ShiftWindow[]>([]);
// load trong useEffect khởi tạo cùng staff.
// onSave: putShiftWindows({ windows: windows.map(w => ({shift:w.shift, start_time:w.start_time!, end_time:w.end_time!})) })
//   .then(res => { setWindows(res.windows); setCurrentShift(res.current_shift); toast success })
//   .catch(err => toast error với message từ err)
```

- [ ] **Step 4: Type-check + build**

```bash
cd frontend && npx tsc --noEmit -p tsconfig.json 2>&1 | head -30
```
Expected: PASS (0 lỗi).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/LeadAssignmentPage.tsx
git commit -m "feat(fe): shift selector + shift-windows config on lead-assignment page"
```

---

## Task 9: Kiểm thử toàn bộ + đồng bộ docs

- [ ] **Step 1: Chạy toàn bộ test orders liên quan**

```bash
run_tests "apps/orders/tests/test_shift_service.py apps/orders/tests/test_lead_assignment_service.py apps/orders/tests/test_shift_windows_api.py -q --create-db"
```
Expected: PASS toàn bộ. (Baseline có vài test fail không liên quan — xem memory `known-failing-baseline-tests`; chỉ quan tâm 3 file trên xanh.)

- [ ] **Step 2: Đánh dấu spec hoàn tất**

Sửa header spec `docs/superpowers/specs/2026-06-15-lead-assignment-shift-design.md`: `Trạng thái: Đã triển khai`.

- [ ] **Step 3: Commit + push + mở MR**

```bash
git add docs/superpowers/specs/2026-06-15-lead-assignment-shift-design.md
git commit -m "docs: mark lead-assignment-shift spec implemented"
git push -u origin feat/lead-assignment-shift
```
Sau push, lấy link MR (GitLab) trả cho người dùng.

---

## Self-Review notes (đã rà)

- **Spec coverage:** D1→Task2/4; D2→Task1/6; D3→Task3/4; D4 (per-date)→giữ nguyên `date` trong shares; D5 (workload/transfer không đổi)→Task4 ghi rõ. Migration backfill→Task3. Edge cases (ca chưa cấu hình→None; thiếu window→fallback)→Task2/4. UI→Task8.
- **Type consistency:** `pick_staff_for(date, shift, sheet)` thống nhất Task3/4/5; `ShiftKey` thống nhất FE Task7/8; `current_shift` field tên thống nhất giữa BE response và FE.
- **Lưu ý người thực thi:** Kiểm tra cách auth trong test API (`require_section`) bằng cách đọc một test API hiện có trước khi viết setUp Task5/6; kiểm tra trường NOT NULL của `Order` khi tạo trong test Task4.
