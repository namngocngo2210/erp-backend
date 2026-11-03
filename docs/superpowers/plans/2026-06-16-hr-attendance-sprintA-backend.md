# HR Attendance Sprint A — Backend + MinIO — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Backend cho self-service chấm công: model + geofence check + lưu ảnh MinIO + API self check-in (NV) và CRUD vị trí (admin).

**Architecture:** Module HR (clean arch). `AttendanceEvent` append-only (ảnh + GPS), `CompanyLocation` cấu hình điểm; vẫn cập nhật `AttendanceRecord` daily sẵn có. Ảnh lưu MinIO qua wrapper boto3 (lazy import → test mock không cần boto3 thật).

**Tech Stack:** Django 5 + DRF, PostgreSQL, MinIO (boto3), pytest. Spec: `docs/erp-hr-attendance.md`.

**Test harness (chạy container NON-root để tránh file root):**
```bash
docker inspect desi-web -f '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^[A-Z_]+=' | grep -vE '^PATH=|^LANG=|^GPG|^PYTHON' > /tmp/web.env
docker run --rm --network desi-order_default --user 1000:1000 --env-file /tmp/web.env \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" -e HOME=/tmp \
  -v "/home/desi/desi-order/backend:/app" -w /app desi-order-web:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && rm -f /tmp/prom_multiproc/* && python -m pytest <PATHS> --reuse-db -q'
```
Migration mới + constraint cần `--create-db` (xoá idle session trước: `docker exec desi-postgres psql -U desi -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='test_desi' AND pid<>pg_backend_pid();"`).
Ruff (host): `uv run --no-project --with ruff ruff check backend/apps/hr`. Commit: tiếng Anh, không trailer Co-Authored-By, không gửi MR link.

---

## File structure
- `backend/apps/hr/domain/geo.py` — haversine + nearest_location
- `backend/apps/hr/models.py` — thêm `CompanyLocation`, `AttendanceEvent`, enum `AttendanceEventKind`
- `backend/apps/hr/infrastructure/__init__.py`, `backend/apps/hr/infrastructure/photo_storage.py` — wrapper MinIO (lazy boto3)
- `backend/apps/hr/repositories.py` — thêm `CompanyLocationRepository`, `AttendanceEventRepository`
- `backend/apps/hr/application/services/attendance_checkin_service.py` — service self check-in
- `backend/apps/hr/transport/serializers/attendance_checkin_serializers.py`
- `backend/apps/hr/transport/views/attendance_checkin_views.py`, `.../attendance_location_views.py`
- `backend/apps/hr/urls.py` — thêm routes
- `backend/config/settings_parts/base.py` — MINIO_* + DATA_UPLOAD_MAX_MEMORY_SIZE
- `backend/requirements.txt` — boto3
- `docker-compose.yml` — service `minio` + volume

---

## Task 1: Hạ tầng MinIO (compose + settings + dep)

**Files:** Modify `docker-compose.yml`, `backend/requirements.txt`, `backend/config/settings_parts/base.py`

- [ ] **Step 1: Thêm service minio vào `docker-compose.yml`** (mirror postgres/redis). Thêm vào `services:`:
```yaml
  minio:
    image: minio/minio:latest
    container_name: desi-minio
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER:?MINIO_ROOT_USER is required}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD is required}
    command: server /data --console-address ":9001"
    volumes:
      - minio_data:/data
    ports:
      - "127.0.0.1:9000:9000"
      - "127.0.0.1:9001:9001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 10s
      timeout: 5s
      retries: 10
    restart: unless-stopped
```
Và thêm `minio_data:` vào mục `volumes:` top-level.

- [ ] **Step 2: Thêm boto3** vào cuối `backend/requirements.txt`:
```
boto3==1.35.0
```

- [ ] **Step 3: Thêm settings** vào `backend/config/settings_parts/base.py` (gần block KIOTVIET/LARK):
```python
# MinIO (S3-compatible) — lưu ảnh chấm công
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000").strip()
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin").strip()
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin").strip()
MINIO_BUCKET_ATTENDANCE = os.getenv("MINIO_BUCKET_ATTENDANCE", "attendance").strip()
MINIO_USE_SSL = os.getenv("MINIO_USE_SSL", "0") == "1"
# Cho phép payload ảnh base64 (~vài trăm KB)
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DATA_UPLOAD_MAX_MEMORY_SIZE", str(10 * 1024 * 1024)))
```

- [ ] **Step 4: Verify** `manage.py check` chạy (harness, `python manage.py check`) → "no issues". (boto3 chưa cần cho check vì import lazy ở Task 4.)

- [ ] **Step 5: Commit**
```bash
git add docker-compose.yml backend/requirements.txt backend/config/settings_parts/base.py
git commit -m "chore(hr): add minio service + settings for attendance photos"
```
> Lưu ý deploy: cần rebuild image backend (cài boto3) + đặt env `MINIO_ROOT_USER`/`PASSWORD`/`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` và tạo bucket `attendance`.

---

## Task 2: Domain — geo (haversine + nearest_location)

**Files:** Create `backend/apps/hr/domain/geo.py`; Test `backend/apps/hr/tests/test_geo.py`

- [ ] **Step 1: Test thất bại** — `backend/apps/hr/tests/test_geo.py`:
```python
from apps.hr.domain.geo import haversine_m, nearest_location


def test_haversine_zero():
    assert haversine_m(10.0, 106.0, 10.0, 106.0) == 0


def test_haversine_known_distance():
    # ~111.2m mỗi 0.001 độ vĩ; cho phép sai số
    d = haversine_m(10.000, 106.0, 10.001, 106.0)
    assert 100 < d < 125


def test_nearest_picks_closest():
    locs = [
        {"id": 1, "latitude": 10.0, "longitude": 106.0, "radius_m": 100},
        {"id": 2, "latitude": 20.0, "longitude": 106.0, "radius_m": 100},
    ]
    loc, dist = nearest_location(10.0005, 106.0, locs)
    assert loc["id"] == 1
    assert dist < 100


def test_nearest_empty():
    assert nearest_location(10.0, 106.0, []) == (None, None)
```

- [ ] **Step 2: Chạy → FAIL.** Run pytest `apps/hr/tests/test_geo.py`.

- [ ] **Step 3: Implement** — `backend/apps/hr/domain/geo.py`:
```python
from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Khoảng cách great-circle giữa 2 điểm (mét)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def nearest_location(lat: float, lng: float, locations):
    """``locations``: iterable dict có latitude/longitude. Trả (location, distance_m) gần nhất hoặc (None, None)."""
    best = None
    best_d = None
    for loc in locations:
        d = haversine_m(lat, lng, loc["latitude"], loc["longitude"])
        if best_d is None or d < best_d:
            best_d, best = d, loc
    if best is None:
        return None, None
    return best, best_d
```

- [ ] **Step 4: Chạy → PASS.**
- [ ] **Step 5: Commit** `feat(hr): geo haversine + nearest location`.

---

## Task 3: Models `CompanyLocation` + `AttendanceEvent`

**Files:** Modify `backend/apps/hr/models.py`; Test `backend/apps/hr/tests/test_attendance_models.py`

- [ ] **Step 1: Test thất bại**:
```python
from datetime import date
from django.test import TestCase
from django.utils import timezone
from apps.hr.models import (
    AttendanceEvent, AttendanceEventKind, CompanyLocation, Employee,
)
from apps.users.models import User


class AttendanceModelsTests(TestCase):
    def test_company_location_defaults_active(self):
        loc = CompanyLocation.objects.create(name="HQ", latitude=10.0, longitude=106.0)
        self.assertTrue(loc.is_active)
        self.assertEqual(loc.radius_m, 150)

    def test_event_created(self):
        u = User.objects.create_user(username="nv", password="x")
        emp = Employee.objects.create(user=u, employee_code="NV0001", full_name="A")
        ev = AttendanceEvent.objects.create(
            employee=emp, kind=AttendanceEventKind.CHECK_IN, occurred_at=timezone.now(),
            work_date=date(2026, 6, 16), photo_key="attendance/1/x.jpg",
            latitude=10.0, longitude=106.0, distance_m=12,
        )
        self.assertEqual(ev.kind, "check_in")
        self.assertEqual(list(emp.attendance_events.all()), [ev])
```

- [ ] **Step 2: Chạy → FAIL.**

- [ ] **Step 3: Implement** — thêm vào `backend/apps/hr/models.py`:
```python
class CompanyLocation(models.Model):
    name = models.CharField(max_length=150)
    latitude = models.FloatField()
    longitude = models.FloatField()
    radius_m = models.PositiveIntegerField(default=150)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "hr_company_locations"
        ordering = ["name"]


class AttendanceEventKind(models.TextChoices):
    CHECK_IN = "check_in", "Vào"
    CHECK_OUT = "check_out", "Ra"


class AttendanceEvent(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="attendance_events")
    kind = models.CharField(max_length=20, choices=AttendanceEventKind.choices)
    occurred_at = models.DateTimeField()
    work_date = models.DateField()
    photo_key = models.CharField(max_length=300)
    latitude = models.FloatField()
    longitude = models.FloatField()
    distance_m = models.IntegerField()
    location = models.ForeignKey(
        CompanyLocation, null=True, blank=True, on_delete=models.SET_NULL, related_name="events"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "hr_attendance_events"
        indexes = [models.Index(fields=["employee", "work_date"], name="hr_att_evt_emp_date_idx")]
```

- [ ] **Step 4: makemigrations hr** (tạo `0007_...`) + chạy test → PASS. `makemigrations --check` clean.
- [ ] **Step 5: Commit** `feat(hr): CompanyLocation + AttendanceEvent models`.

---

## Task 4: Infrastructure — photo_storage (MinIO, lazy boto3)

**Files:** Create `backend/apps/hr/infrastructure/__init__.py`, `backend/apps/hr/infrastructure/photo_storage.py`; Test `backend/apps/hr/tests/test_photo_storage.py`

- [ ] **Step 1: Test thất bại** (mock boto3 client; verify put gọi đúng + presigned trả url):
```python
from unittest.mock import MagicMock, patch
from django.test import TestCase
from apps.hr.infrastructure import photo_storage


class PhotoStorageTests(TestCase):
    @patch("apps.hr.infrastructure.photo_storage._client")
    def test_put_photo_calls_put_object(self, mock_client_fn):
        client = MagicMock()
        mock_client_fn.return_value = client
        key = photo_storage.put_photo("attendance/1/x.jpg", b"abc", "image/jpeg")
        self.assertEqual(key, "attendance/1/x.jpg")
        client.put_object.assert_called_once()
        _, kwargs = client.put_object.call_args
        self.assertEqual(kwargs["Key"], "attendance/1/x.jpg")
        self.assertEqual(kwargs["ContentType"], "image/jpeg")

    @patch("apps.hr.infrastructure.photo_storage._client")
    def test_presigned_url(self, mock_client_fn):
        client = MagicMock()
        client.generate_presigned_url.return_value = "http://minio/presigned"
        mock_client_fn.return_value = client
        url = photo_storage.presigned_get_url("attendance/1/x.jpg")
        self.assertEqual(url, "http://minio/presigned")
```

- [ ] **Step 2: Chạy → FAIL.**

- [ ] **Step 3: Implement** — `backend/apps/hr/infrastructure/photo_storage.py` (boto3 import LAZY, chỉ khi gọi `_client`):
```python
from __future__ import annotations

from django.conf import settings

_BUCKET = settings.MINIO_BUCKET_ATTENDANCE


def _client():
    import boto3  # lazy: tests mock _client nên không cần boto3 lúc import module

    scheme = "https" if settings.MINIO_USE_SSL else "http"
    return boto3.client(
        "s3",
        endpoint_url=f"{scheme}://{settings.MINIO_ENDPOINT}",
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY,
        region_name="us-east-1",
    )


def put_photo(key: str, data: bytes, content_type: str) -> str:
    client = _client()
    client.put_object(Bucket=_BUCKET, Key=key, Body=data, ContentType=content_type)
    return key


def presigned_get_url(key: str, expires: int = 300) -> str:
    client = _client()
    return client.generate_presigned_url(
        "get_object", Params={"Bucket": _BUCKET, "Key": key}, ExpiresIn=expires
    )
```
`infrastructure/__init__.py`: rỗng.

- [ ] **Step 4: Chạy → PASS.**
- [ ] **Step 5: Commit** `feat(hr): minio photo storage wrapper`.

---

## Task 5: Repositories (locations + events)

**Files:** Modify `backend/apps/hr/repositories.py`; Test `backend/apps/hr/tests/test_attendance_repo.py`

- [ ] **Step 1: Test thất bại**:
```python
from datetime import date
from django.test import TestCase
from django.utils import timezone
from apps.hr.models import AttendanceEvent, AttendanceEventKind, CompanyLocation, Employee
from apps.hr.repositories import AttendanceEventRepository, CompanyLocationRepository
from apps.users.models import User


class AttendanceRepoTests(TestCase):
    def _emp(self):
        u = User.objects.create_user(username="nv", password="x")
        return Employee.objects.create(user=u, employee_code="NV0001", full_name="A")

    def test_active_locations(self):
        CompanyLocation.objects.create(name="A", latitude=1, longitude=1, is_active=True)
        CompanyLocation.objects.create(name="B", latitude=2, longitude=2, is_active=False)
        self.assertEqual([l.name for l in CompanyLocationRepository.active()], ["A"])

    def test_events_for_day(self):
        emp = self._emp()
        AttendanceEvent.objects.create(employee=emp, kind=AttendanceEventKind.CHECK_IN,
            occurred_at=timezone.now(), work_date=date(2026, 6, 16), photo_key="k",
            latitude=1, longitude=1, distance_m=5)
        rows = AttendanceEventRepository.for_day(emp.id, date(2026, 6, 16))
        self.assertEqual(len(list(rows)), 1)
```

- [ ] **Step 2: Chạy → FAIL.**

- [ ] **Step 3: Implement** — thêm vào `backend/apps/hr/repositories.py` (mở rộng import models):
```python
class CompanyLocationRepository(BaseRepository[CompanyLocation]):
    model = CompanyLocation

    @classmethod
    def active(cls):
        return cls.model._default_manager.filter(is_active=True).order_by("name")


class AttendanceEventRepository(BaseRepository[AttendanceEvent]):
    model = AttendanceEvent

    @classmethod
    def for_day(cls, employee_id: int, work_date):
        return cls.model._default_manager.filter(
            employee_id=employee_id, work_date=work_date
        ).order_by("occurred_at")
```
> `CompanyLocation`/`AttendanceEvent` không có `deleted_at`; override `active()` của CompanyLocation theo `is_active` (khác BaseRepository.active mặc định). Import 2 model mới ở đầu file.

- [ ] **Step 4: Chạy → PASS.**
- [ ] **Step 5: Commit** `feat(hr): attendance repositories`.

---

## Task 6: Service — AttendanceCheckinService

**Files:** Create `backend/apps/hr/application/services/attendance_checkin_service.py`; Test `backend/apps/hr/tests/test_attendance_checkin_service.py`

- [ ] **Step 1: Test thất bại**:
```python
from datetime import date
from unittest.mock import patch
from django.test import TestCase
from apps.hr.application.services.attendance_checkin_service import (
    AlreadyCompleteError, AttendanceCheckinService, OutsideGeofenceError,
)
from apps.hr.models import AttendanceEvent, AttendanceRecord, CompanyLocation, Employee
from apps.users.models import User


class CheckinServiceTests(TestCase):
    def setUp(self):
        u = User.objects.create_user(username="nv", password="x")
        self.emp = Employee.objects.create(user=u, employee_code="NV0001", full_name="A")
        self.loc = CompanyLocation.objects.create(name="HQ", latitude=10.0, longitude=106.0, radius_m=150)

    @patch("apps.hr.application.services.attendance_checkin_service.put_photo", return_value="k.jpg")
    def test_first_is_checkin_second_is_checkout(self, _put):
        ev1 = AttendanceCheckinService.check_in(employee_id=self.emp.id, latitude=10.0, longitude=106.0,
                                                photo_bytes=b"x", content_type="image/jpeg")
        self.assertEqual(ev1.kind, "check_in")
        ev2 = AttendanceCheckinService.check_in(employee_id=self.emp.id, latitude=10.0, longitude=106.0,
                                                photo_bytes=b"x", content_type="image/jpeg")
        self.assertEqual(ev2.kind, "check_out")
        # AttendanceRecord daily cập nhật cả 2 giờ
        rec = AttendanceRecord.objects.get(employee=self.emp, work_date=ev1.work_date)
        self.assertIsNotNone(rec.check_in)
        self.assertIsNotNone(rec.check_out)

    @patch("apps.hr.application.services.attendance_checkin_service.put_photo", return_value="k.jpg")
    def test_third_checkin_rejected(self, _put):
        for _ in range(2):
            AttendanceCheckinService.check_in(employee_id=self.emp.id, latitude=10.0, longitude=106.0,
                                              photo_bytes=b"x", content_type="image/jpeg")
        with self.assertRaises(AlreadyCompleteError):
            AttendanceCheckinService.check_in(employee_id=self.emp.id, latitude=10.0, longitude=106.0,
                                              photo_bytes=b"x", content_type="image/jpeg")

    @patch("apps.hr.application.services.attendance_checkin_service.put_photo", return_value="k.jpg")
    def test_outside_radius_rejected(self, _put):
        with self.assertRaises(OutsideGeofenceError):
            AttendanceCheckinService.check_in(employee_id=self.emp.id, latitude=20.0, longitude=106.0,
                                              photo_bytes=b"x", content_type="image/jpeg")
        self.assertEqual(AttendanceEvent.objects.count(), 0)  # không tạo event khi ngoài vùng
```

- [ ] **Step 2: Chạy → FAIL.**

- [ ] **Step 3: Implement** — `attendance_checkin_service.py`:
```python
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.hr.application.services.attendance_service import AttendanceService
from apps.hr.domain.geo import nearest_location
from apps.hr.infrastructure.photo_storage import put_photo
from apps.hr.models import AttendanceEvent, AttendanceEventKind
from apps.hr.repositories import AttendanceEventRepository, CompanyLocationRepository


class OutsideGeofenceError(Exception):
    def __init__(self, distance_m: int, radius_m: int):
        self.distance_m = distance_m
        self.radius_m = radius_m
        super().__init__("Ngoài bán kính cho phép.")


class AlreadyCompleteError(Exception):
    pass


class AttendanceCheckinService:
    @staticmethod
    @transaction.atomic
    def check_in(*, employee_id: int, latitude: float, longitude: float,
                 photo_bytes: bytes, content_type: str = "image/jpeg") -> AttendanceEvent:
        now = timezone.now()
        work_date = timezone.localdate(now)
        events = list(AttendanceEventRepository.for_day(employee_id, work_date))
        has_in = any(e.kind == AttendanceEventKind.CHECK_IN for e in events)
        has_out = any(e.kind == AttendanceEventKind.CHECK_OUT for e in events)
        if has_in and has_out:
            raise AlreadyCompleteError("Bạn đã chấm công đủ hôm nay.")
        kind = AttendanceEventKind.CHECK_OUT if has_in else AttendanceEventKind.CHECK_IN

        locs = [
            {"id": l.id, "latitude": l.latitude, "longitude": l.longitude, "radius_m": l.radius_m}
            for l in CompanyLocationRepository.active()
        ]
        loc, dist = nearest_location(latitude, longitude, locs)
        if loc is None or dist > loc["radius_m"]:
            raise OutsideGeofenceError(
                distance_m=int(dist) if dist is not None else -1,
                radius_m=loc["radius_m"] if loc else 0,
            )

        key = f"attendance/{employee_id}/{work_date.isoformat()}/{kind}-{int(now.timestamp())}.jpg"
        put_photo(key, photo_bytes, content_type)

        event = AttendanceEvent.objects.create(
            employee_id=employee_id, kind=kind, occurred_at=now, work_date=work_date,
            photo_key=key, latitude=latitude, longitude=longitude,
            distance_m=int(dist), location_id=loc["id"],
        )
        local_time = timezone.localtime(now).time()
        if kind == AttendanceEventKind.CHECK_IN:
            AttendanceService.record(employee_id, work_date=work_date, check_in=local_time, source="self")
        else:
            AttendanceService.record(employee_id, work_date=work_date, check_out=local_time, source="self")
        return event
```

- [ ] **Step 4: Chạy → PASS.**
- [ ] **Step 5: Commit** `feat(hr): attendance check-in service (geofence + photo + daily)`.

---

## Task 7: Serializers + self check-in API + config endpoint

**Files:** Create `backend/apps/hr/transport/serializers/attendance_checkin_serializers.py`, `backend/apps/hr/transport/views/attendance_checkin_views.py`; Modify `backend/apps/hr/urls.py`; Test `backend/apps/hr/tests/test_attendance_checkin_api.py`

- [ ] **Step 1: Test thất bại**:
```python
import base64
from unittest.mock import patch
from django.test import TestCase
from rest_framework.test import APIClient
from apps.hr.models import CompanyLocation, Employee
from apps.users.models import User
from apps.users.test_helpers import make_user_with_sections

PHOTO = "data:image/jpeg;base64," + base64.b64encode(b"fakejpeg").decode()


class CheckinApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        CompanyLocation.objects.create(name="HQ", latitude=10.0, longitude=106.0, radius_m=150)
        # NV thường: KHÔNG có quyền employees:* (chỉ orders)
        self.user = make_user_with_sections("nv-checkin", ["orders"])
        self.emp = Employee.objects.create(user=self.user, employee_code="NV0001", full_name="A")

    @patch("apps.hr.application.services.attendance_checkin_service.put_photo", return_value="k.jpg")
    def test_checkin_inside_radius(self, _put):
        self.client.force_authenticate(user=self.user)
        r = self.client.post("/api/hr/attendance/check-in",
            {"latitude": 10.0, "longitude": 106.0, "photo": PHOTO}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["kind"], "check_in")

    @patch("apps.hr.application.services.attendance_checkin_service.put_photo", return_value="k.jpg")
    def test_checkin_outside_radius_422(self, _put):
        self.client.force_authenticate(user=self.user)
        r = self.client.post("/api/hr/attendance/check-in",
            {"latitude": 20.0, "longitude": 106.0, "photo": PHOTO}, format="json")
        self.assertEqual(r.status_code, 422)
        self.assertIn("distance_m", r.data)

    def test_no_employee_404(self):
        other = make_user_with_sections("no-emp", ["orders"])
        self.client.force_authenticate(user=other)
        r = self.client.post("/api/hr/attendance/check-in",
            {"latitude": 10.0, "longitude": 106.0, "photo": PHOTO}, format="json")
        self.assertEqual(r.status_code, 404)

    def test_config_returns_locations(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.get("/api/hr/attendance/config")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["locations"]), 1)
```

- [ ] **Step 2: Chạy → FAIL.**

- [ ] **Step 3: Implement serializers** — `attendance_checkin_serializers.py`:
```python
from __future__ import annotations

import base64
import binascii

from rest_framework import serializers

_MAX_PHOTO_BYTES = 800 * 1024


class CheckinSerializer(serializers.Serializer):
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    photo = serializers.CharField()  # data URL base64

    def validate_photo(self, value: str) -> bytes:
        raw = value.split(",", 1)[1] if value.startswith("data:") else value
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            raise serializers.ValidationError("Ảnh không hợp lệ.")
        if not data:
            raise serializers.ValidationError("Ảnh rỗng.")
        if len(data) > _MAX_PHOTO_BYTES:
            raise serializers.ValidationError("Ảnh quá lớn (tối đa 800KB).")
        return data
```

- [ ] **Step 4: Implement views** — `attendance_checkin_views.py`:
```python
from __future__ import annotations

from django.utils import timezone
from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.hr.application.services.attendance_checkin_service import (
    AlreadyCompleteError, AttendanceCheckinService, OutsideGeofenceError,
)
from apps.hr.models import AttendanceEventKind
from apps.hr.repositories import AttendanceEventRepository, CompanyLocationRepository
from apps.hr.transport.serializers.attendance_checkin_serializers import CheckinSerializer


def _employee(request):
    return getattr(request.user, "employee", None)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@never_cache
def attendance_config(request):
    locations = [
        {"id": l.id, "name": l.name, "latitude": l.latitude, "longitude": l.longitude,
         "radius_m": l.radius_m}
        for l in CompanyLocationRepository.active()
    ]
    today = {"check_in": None, "check_out": None}
    emp = _employee(request)
    if emp:
        work_date = timezone.localdate()
        for e in AttendanceEventRepository.for_day(emp.id, work_date):
            t = timezone.localtime(e.occurred_at).strftime("%H:%M")
            if e.kind == AttendanceEventKind.CHECK_IN:
                today["check_in"] = t
            else:
                today["check_out"] = t
    return Response({"locations": locations, "today": today})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@never_cache
def attendance_check_in(request):
    emp = _employee(request)
    if not emp:
        return Response({"detail": "Tài khoản chưa gắn hồ sơ nhân viên."}, status=404)
    ser = CheckinSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    try:
        ev = AttendanceCheckinService.check_in(
            employee_id=emp.id, latitude=ser.validated_data["latitude"],
            longitude=ser.validated_data["longitude"],
            photo_bytes=ser.validated_data["photo"], content_type="image/jpeg",
        )
    except OutsideGeofenceError as exc:
        return Response(
            {"detail": "Bạn đang ở ngoài khu vực công ty.", "distance_m": exc.distance_m,
             "radius_m": exc.radius_m},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except AlreadyCompleteError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    return Response(
        {"kind": ev.kind, "occurred_at": ev.occurred_at, "distance_m": ev.distance_m,
         "location_name": ev.location.name if ev.location else ""},
        status=status.HTTP_201_CREATED,
    )
```

- [ ] **Step 5: URLs** — thêm vào `backend/apps/hr/urls.py`:
```python
    path("attendance/config", attendance_config, name="hr-attendance-config"),
    path("attendance/check-in", attendance_check_in, name="hr-attendance-checkin"),
```
(import 2 view).

- [ ] **Step 6: Chạy test → PASS.** Full `apps/hr` + ruff.
- [ ] **Step 7: Commit** `feat(hr): self check-in + config API`.

---

## Task 8: Locations CRUD API (admin)

**Files:** Create `backend/apps/hr/transport/serializers/location_serializers.py`, `backend/apps/hr/transport/views/attendance_location_views.py`; Modify `backend/apps/hr/urls.py`; Test `backend/apps/hr/tests/test_attendance_location_api.py`

- [ ] **Step 1: Test thất bại** — list (`employees:view`), create/patch/delete (`employees:edit`); NV thường → 403.
```python
from django.test import TestCase
from rest_framework.test import APIClient
from apps.hr.models import CompanyLocation
from apps.users.test_helpers import make_admin, make_user_with_sections


class LocationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("hr-loc-admin")

    def test_admin_create_and_list(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.post("/api/hr/attendance/locations",
            {"name": "HQ", "latitude": 10.0, "longitude": 106.0, "radius_m": 200}, format="json")
        self.assertEqual(r.status_code, 201)
        lst = self.client.get("/api/hr/attendance/locations")
        self.assertEqual(len(lst.data["items"]), 1)

    def test_non_admin_cannot_create(self):
        nobody = make_user_with_sections("loc-nobody", ["orders"])
        self.client.force_authenticate(user=nobody)
        r = self.client.post("/api/hr/attendance/locations",
            {"name": "X", "latitude": 1, "longitude": 1}, format="json")
        self.assertEqual(r.status_code, 403)
```

- [ ] **Step 2: Chạy → FAIL.**

- [ ] **Step 3: Implement** serializer (`LocationSerializer` ModelSerializer over CompanyLocation: id, name, latitude, longitude, radius_m, is_active) + views (function-based, dùng `_helpers.can/forbidden`: GET=`employees:view`, POST/PATCH/DELETE=`employees:edit`; DELETE = soft via `is_active=False` hoặc xoá cứng — chọn xoá cứng `delete()` vì không có FK ràng buộc cứng, event giữ `location` SET_NULL). Routes:
```python
    path("attendance/locations", attendance_locations, name="hr-attendance-locations"),
    path("attendance/locations/<int:location_id>", attendance_location_detail, name="hr-attendance-location-detail"),
```

- [ ] **Step 4: Chạy → PASS.** Full `apps/hr` + ruff.
- [ ] **Step 5: Commit** `feat(hr): attendance location CRUD API`.

---

## Task 9: Tổng kiểm tra + ghi chú

- [ ] **Step 1:** Full suite trên DB sạch:
```bash
docker exec desi-postgres psql -U desi -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='test_desi' AND pid<>pg_backend_pid();"
# ... harness ... python -m pytest apps/hr --create-db -q
```
Expected: all PASS. Ruff sạch. Xác nhận `ls -la backend/apps/hr/migrations/` các file thuộc uid desi.
- [ ] **Step 2:** Ghi chú "Attendance Sprint A — đã làm" vào `docs/erp-hr-attendance.md`.
- [ ] **Step 3:** Commit + push + gửi MR link.

---

## Self-review
- **Spec coverage:** model (T3), geo (T2), storage MinIO (T1,T4), service auto-kind+geofence+daily (T6), self API + config (T7), locations CRUD (T8), infra (T1). Phủ đủ §2-4,6-8 spec.
- **Type consistency:** `AttendanceCheckinService.check_in(employee_id, latitude, longitude, photo_bytes, content_type)`, `put_photo(key,data,content_type)`, `nearest_location→(loc,dist)`, errors `OutsideGeofenceError(distance_m,radius_m)`/`AlreadyCompleteError` dùng nhất quán giữa service↔views↔tests.
- **Test không cần boto3 thật:** `put_photo` mock; boto3 import lazy trong `_client`. Self check-in dùng IsAuthenticated + `request.user.employee` (không cần quyền `employees`).
- **Điểm cần xác minh khi code:** `AttendanceService.record` chữ ký (work_date/check_in/check_out/source) — đã có từ Sprint 3; `_helpers.can/forbidden` path; số migration hr mới nhất (đặt 0007 sau khi check).
