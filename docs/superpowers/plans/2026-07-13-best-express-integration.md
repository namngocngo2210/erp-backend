# Best Express Direct Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Xây app Django `apps/best_express` — tầng đấu nối trực tiếp Best Express API V3 (client 8 endpoint, webhook nhận trạng thái, models account/shipment/event) — chưa gắn vào luồng đơn/KShip hiện có.

**Architecture:** App độc lập theo layering của repo (domain → infrastructure → application → transport). Client HTTP tự quản token (30 ngày, re-login khi 401, rate-limit login 5/phút qua Redis cache). Webhook idempotent (unique constraint trên event) + guard out-of-order theo `modified_at`. TraceQuery sync ngược dùng chung đường áp event với webhook.

**Tech Stack:** Django 5 + DRF (view webhook), `requests`, PostgreSQL, Redis (django `core.cache`), pytest-django.

**Spec:** `docs/superpowers/specs/2026-07-13-best-express-integration-design.md`

## Global Constraints

- **Branch/worktree:** mọi việc làm trên branch `feat/best-express-integration` trong worktree `c:/Users/MYPC/Desktop/workspace/desi-order-wt-best-express`. KHÔNG commit vào checkout chính, KHÔNG tự tạo branch mới.
- Commit message tiếng Anh, ngắn, KHÔNG kèm dòng `Co-Authored-By`.
- KHÔNG sửa bất cứ file nào của luồng KShip/KiotViet (trừ 2 file cấu hình: `config/settings_parts/base.py`, `config/urls.py`, và `backend/.importlinter`).
- `EncryptedCharField` import từ `apps.kiotviet.models` — đây là pattern chung sẵn có (facebook_ads, google_ads đều làm vậy); KHÔNG copy field, KHÔNG import gì khác từ kiotviet.
- Lỗi Best passthrough nguyên văn `Message` — không dịch, không nuốt.
- Tests: KHÔNG BAO GIỜ gọi `cache.clear()` (RedisCache.clear = FLUSHDB xóa cache thật). Dùng username uuid4 cho account trong test để key cache không đụng nhau giữa các run.
- **Chạy test** (Git Bash, từ thư mục worktree gốc; lần đầu sau khi thêm migration phải có `--create-db`):

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-wt-best-express
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -e POSTGRES_DB=desi_bestexp \
  -v "c:/Users/MYPC/Desktop/workspace/desi-order-wt-best-express/backend:/app" -w /app \
  desi-order-web-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/best_express -q --create-db'
```

  Các lần sau trong cùng task có thể bỏ `--create-db` (giữ `-e POSTGRES_DB=desi_bestexp`). Nếu image `desi-order-web-test` không tồn tại, báo lại — không tự build.
- **Ruff** (host): `uv run --no-project --with ruff ruff check backend/apps/best_express` (uv ở `/c/Users/MYPC/.local/bin/uv`).

---

### Task 1: App scaffold + domain status map

**Files:**
- Create: `backend/apps/best_express/__init__.py` (rỗng)
- Create: `backend/apps/best_express/apps.py`
- Create: `backend/apps/best_express/domain/__init__.py` (rỗng)
- Create: `backend/apps/best_express/domain/status_map.py`
- Create: `backend/apps/best_express/exceptions.py`
- Create: `backend/apps/best_express/tests/__init__.py` (rỗng)
- Test: `backend/apps/best_express/tests/test_status_map.py`

**Interfaces:**
- Produces: `BestShipmentStatus` (TextChoices: CREATED/PICKING_UP/PICKUP_FAILED/PICKED_UP/IN_TRANSIT/OUT_FOR_DELIVERY/DELIVERY_FAILED/DELIVERED/RETURNING/RETURN_FAILED/RETURNED/LOST/DAMAGED/CANCELLED), `map_best_status(code) -> BestShipmentStatus | None`, `class BestExpressError(Exception)` với `.message: str`, `.payload: dict`.

- [ ] **Step 1: Tạo scaffold app**

`backend/apps/best_express/apps.py`:

```python
from django.apps import AppConfig


class BestExpressConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.best_express"
    verbose_name = "Best Express"
```

`backend/apps/best_express/exceptions.py`:

```python
class BestExpressError(Exception):
    """Lỗi từ Best Express API — message giữ nguyên văn từ Best (passthrough)."""

    def __init__(self, message: str, payload: dict | None = None):
        super().__init__(message)
        self.message = message
        self.payload = payload or {}
```

- [ ] **Step 2: Viết test status map (failing)**

`backend/apps/best_express/tests/test_status_map.py`:

```python
import pytest

from apps.best_express.domain.status_map import BestShipmentStatus, map_best_status


@pytest.mark.parametrize(
    "code,expected",
    [
        ("211", BestShipmentStatus.PICKING_UP),
        ("203", BestShipmentStatus.PICKUP_FAILED),
        ("202", BestShipmentStatus.PICKED_UP),
        ("301", BestShipmentStatus.IN_TRANSIT),
        ("302", BestShipmentStatus.IN_TRANSIT),
        ("303", BestShipmentStatus.IN_TRANSIT),
        ("304", BestShipmentStatus.IN_TRANSIT),
        ("309", BestShipmentStatus.IN_TRANSIT),
        ("601", BestShipmentStatus.OUT_FOR_DELIVERY),
        ("604", BestShipmentStatus.DELIVERY_FAILED),
        ("666", BestShipmentStatus.DELIVERED),
        ("605", BestShipmentStatus.RETURNING),
        ("701", BestShipmentStatus.RETURNING),
        ("702", BestShipmentStatus.RETURNING),
        ("703", BestShipmentStatus.RETURNING),
        ("704", BestShipmentStatus.RETURNING),
        ("705", BestShipmentStatus.RETURNING),
        ("707", BestShipmentStatus.RETURN_FAILED),
        ("708", BestShipmentStatus.RETURNED),
        ("777", BestShipmentStatus.LOST),
        ("1000", BestShipmentStatus.DAMAGED),
        ("2", BestShipmentStatus.CANCELLED),
        ("customer Cancel", BestShipmentStatus.CANCELLED),
        ("CUSTOMER CANCEL", BestShipmentStatus.CANCELLED),
    ],
)
def test_map_known_codes(code, expected):
    assert map_best_status(code) == expected


def test_unknown_code_returns_none():
    assert map_best_status("999") is None
    assert map_best_status("") is None
    assert map_best_status(None) is None


def test_int_code_accepted():
    assert map_best_status(666) == BestShipmentStatus.DELIVERED
```

- [ ] **Step 3: Chạy test, xác nhận FAIL**

Chạy lệnh test ở Global Constraints (path `apps/best_express/tests/test_status_map.py`). Expected: FAIL `ModuleNotFoundError: No module named 'apps.best_express.domain.status_map'`.

- [ ] **Step 4: Implement status map**

`backend/apps/best_express/domain/status_map.py`:

```python
"""Bảng map mã trạng thái Best Express → enum nội bộ. Pure — không I/O."""

from django.db import models


class BestShipmentStatus(models.TextChoices):
    CREATED = "created", "Đã tạo"
    PICKING_UP = "picking_up", "Đang đi lấy hàng"
    PICKUP_FAILED = "pickup_failed", "Lấy hàng thất bại"
    PICKED_UP = "picked_up", "Đã lấy hàng"
    IN_TRANSIT = "in_transit", "Đang trung chuyển"
    OUT_FOR_DELIVERY = "out_for_delivery", "Đang giao"
    DELIVERY_FAILED = "delivery_failed", "Giao thất bại"
    DELIVERED = "delivered", "Giao thành công"
    RETURNING = "returning", "Đang chuyển hoàn"
    RETURN_FAILED = "return_failed", "Trả hàng thất bại"
    RETURNED = "returned", "Đã chuyển hoàn"
    LOST = "lost", "Thất lạc"
    DAMAGED = "damaged", "Hư hỏng"
    CANCELLED = "cancelled", "Đã hủy"


_STATUS_BY_CODE: dict[str, BestShipmentStatus] = {
    "211": BestShipmentStatus.PICKING_UP,
    "203": BestShipmentStatus.PICKUP_FAILED,
    "202": BestShipmentStatus.PICKED_UP,
    "301": BestShipmentStatus.IN_TRANSIT,
    "302": BestShipmentStatus.IN_TRANSIT,
    "303": BestShipmentStatus.IN_TRANSIT,
    "304": BestShipmentStatus.IN_TRANSIT,
    "309": BestShipmentStatus.IN_TRANSIT,
    "601": BestShipmentStatus.OUT_FOR_DELIVERY,
    "604": BestShipmentStatus.DELIVERY_FAILED,
    "666": BestShipmentStatus.DELIVERED,
    "605": BestShipmentStatus.RETURNING,
    "701": BestShipmentStatus.RETURNING,
    "702": BestShipmentStatus.RETURNING,
    "703": BestShipmentStatus.RETURNING,
    "704": BestShipmentStatus.RETURNING,
    "705": BestShipmentStatus.RETURNING,
    "707": BestShipmentStatus.RETURN_FAILED,
    "708": BestShipmentStatus.RETURNED,
    "777": BestShipmentStatus.LOST,
    "1000": BestShipmentStatus.DAMAGED,
    "2": BestShipmentStatus.CANCELLED,
}


def map_best_status(code) -> BestShipmentStatus | None:
    """Mã Best (str/int) → enum nội bộ; mã lạ trả None (caller giữ status cũ)."""
    raw = str(code or "").strip()
    if not raw:
        return None
    if raw.lower() == "customer cancel":
        return BestShipmentStatus.CANCELLED
    return _STATUS_BY_CODE.get(raw)
```

- [ ] **Step 5: Chạy test, xác nhận PASS** (cùng lệnh Step 3)

- [ ] **Step 6: Commit**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-wt-best-express
git add backend/apps/best_express
git commit -m "feat(best-express): scaffold app with status map domain"
```

---

### Task 2: Models + migration + admin + đăng ký app

**Files:**
- Create: `backend/apps/best_express/models.py`
- Create: `backend/apps/best_express/admin.py`
- Create: `backend/apps/best_express/migrations/__init__.py` (rỗng)
- Create: `backend/apps/best_express/migrations/0001_initial.py` (bằng makemigrations)
- Modify: `backend/config/settings_parts/base.py` (INSTALLED_APPS + 2 settings)
- Modify: `backend/.importlinter` (thêm app vào layer trên cùng)
- Test: `backend/apps/best_express/tests/test_models.py`

**Interfaces:**
- Consumes: `BestShipmentStatus` (Task 1).
- Produces:
  - `BestExpressAccount` — fields: `name`, `environment` (`"test"|"production"`), `base_url`, `username`, `password`, `token`, `token_expires_at`, `ems_key`, `webhook_token`, `is_active`; method `resolve_base_url() -> str`.
  - `BestShipment` — fields: `account` (FK), `order` (FK `orders.Order` null), `merchant_order_code` (unique), `code_best` (unique null), `route_code`, `status`, `best_status_code`, `status_content`, các Decimal `cod, product_price, freight_fee, cod_fee, insurance_fee, vat_fee, return_fee, discount, total_fee`, các Int `weight, length, width, height, journey_type, service_id, pickup_type`, `request_payload` (JSON), `last_event_at`.
  - `BestWebhookEvent` — fields: `account` (FK null), `code_best`, `merchant_order_code`, `best_status_code`, `payload` (JSON), `modified_at_ms`, `processed`, `processing_error`, `received_at`; unique (`code_best`, `best_status_code`, `modified_at_ms`).
  - Settings: `BEST_EXPRESS_BASE_URL_TEST`, `BEST_EXPRESS_BASE_URL_PROD`.

- [ ] **Step 1: Viết test models (failing)**

`backend/apps/best_express/tests/test_models.py`:

```python
import uuid

import pytest
from django.db import IntegrityError
from django.test import override_settings

from apps.best_express.domain.status_map import BestShipmentStatus
from apps.best_express.models import BestExpressAccount, BestShipment, BestWebhookEvent


def make_account(**kwargs) -> BestExpressAccount:
    defaults = {
        "name": "Test",
        "environment": "test",
        "username": f"user-{uuid.uuid4()}",
        "password": "secret",
        "webhook_token": "hook-token",
    }
    defaults.update(kwargs)
    return BestExpressAccount.objects.create(**defaults)


@pytest.mark.django_db
def test_password_encrypted_at_rest_and_decrypted_on_read():
    account = make_account(password="s3cret")
    raw = BestExpressAccount.objects.values_list("password", flat=True).get(pk=account.pk)
    # values_list bỏ qua from_db_value? Không — vẫn decrypt. So sánh qua raw SQL:
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("SELECT password FROM best_express_accounts WHERE id = %s", [account.pk])
        stored = cur.fetchone()[0]
    assert stored != "s3cret"
    account.refresh_from_db()
    assert account.password == "s3cret"


@pytest.mark.django_db
@override_settings(
    BEST_EXPRESS_BASE_URL_TEST="http://test.example",
    BEST_EXPRESS_BASE_URL_PROD="http://prod.example",
)
def test_resolve_base_url():
    assert make_account(environment="test").resolve_base_url() == "http://test.example"
    assert make_account(environment="production").resolve_base_url() == "http://prod.example"
    assert make_account(base_url="http://override.example").resolve_base_url() == "http://override.example"


@pytest.mark.django_db
def test_shipment_defaults():
    account = make_account()
    shipment = BestShipment.objects.create(
        account=account, merchant_order_code=f"DH-{uuid.uuid4()}"
    )
    assert shipment.status == BestShipmentStatus.CREATED
    assert shipment.cod == 0
    assert shipment.code_best is None


@pytest.mark.django_db
def test_webhook_event_unique_constraint():
    account = make_account()
    kwargs = {
        "account": account,
        "code_best": "84855000000001",
        "merchant_order_code": "DH-1",
        "best_status_code": "666",
        "payload": {"status": "666"},
        "modified_at_ms": 1700000000000,
    }
    BestWebhookEvent.objects.create(**kwargs)
    with pytest.raises(IntegrityError):
        BestWebhookEvent.objects.create(**kwargs)
```

- [ ] **Step 2: Chạy test, xác nhận FAIL** (`apps/best_express/tests/test_models.py`, lỗi import models)

- [ ] **Step 3: Implement models**

`backend/apps/best_express/models.py`:

```python
from django.conf import settings
from django.db import models

# EncryptedCharField là field dùng chung của repo, sống ở apps.kiotviet.models
# (facebook_ads/google_ads cùng import từ đây).
from apps.kiotviet.models import EncryptedCharField

from apps.best_express.domain.status_map import BestShipmentStatus


class BestExpressEnvironment(models.TextChoices):
    TEST = "test", "Test"
    PRODUCTION = "production", "Production"


class BestExpressAccount(models.Model):
    name = models.CharField(max_length=255, verbose_name="Tên tài khoản")
    environment = models.CharField(
        max_length=20,
        choices=BestExpressEnvironment.choices,
        default=BestExpressEnvironment.TEST,
    )
    base_url = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Override base URL; trống = default theo environment",
    )
    username = models.CharField(max_length=255)
    password = EncryptedCharField(max_length=512)
    token = EncryptedCharField(max_length=4096, blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)
    ems_key = models.CharField(max_length=255, blank=True, default="")
    webhook_token = EncryptedCharField(
        max_length=512,
        blank=True,
        default="",
        help_text="Bearer token tĩnh mình sinh, gửi sale Best cấu hình webhook",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "best_express_accounts"

    def __str__(self) -> str:
        return f"{self.name} ({self.environment})"

    def resolve_base_url(self) -> str:
        if self.base_url:
            return self.base_url
        if self.environment == BestExpressEnvironment.PRODUCTION:
            return settings.BEST_EXPRESS_BASE_URL_PROD
        return settings.BEST_EXPRESS_BASE_URL_TEST


_DECIMAL_KW = {"max_digits": 14, "decimal_places": 2, "default": 0}


class BestShipment(models.Model):
    account = models.ForeignKey(
        BestExpressAccount, on_delete=models.PROTECT, related_name="shipments"
    )
    order = models.ForeignKey(
        "orders.Order",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="best_shipments",
    )
    merchant_order_code = models.CharField(max_length=100, unique=True)
    code_best = models.CharField(max_length=100, unique=True, null=True, blank=True)
    route_code = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(
        max_length=32,
        choices=BestShipmentStatus.choices,
        default=BestShipmentStatus.CREATED,
    )
    best_status_code = models.CharField(max_length=32, blank=True, default="")
    status_content = models.TextField(blank=True, default="")
    cod = models.DecimalField(**_DECIMAL_KW)
    product_price = models.DecimalField(**_DECIMAL_KW)
    freight_fee = models.DecimalField(**_DECIMAL_KW)
    cod_fee = models.DecimalField(**_DECIMAL_KW)
    insurance_fee = models.DecimalField(**_DECIMAL_KW)
    vat_fee = models.DecimalField(**_DECIMAL_KW)
    return_fee = models.DecimalField(**_DECIMAL_KW)
    discount = models.DecimalField(**_DECIMAL_KW)
    total_fee = models.DecimalField(**_DECIMAL_KW)
    weight = models.IntegerField(default=0, help_text="gram")
    length = models.IntegerField(default=0, help_text="mm")
    width = models.IntegerField(default=0, help_text="mm")
    height = models.IntegerField(default=0, help_text="mm")
    journey_type = models.IntegerField(default=1)
    service_id = models.IntegerField(default=12491)
    pickup_type = models.IntegerField(default=2)
    request_payload = models.JSONField(default=dict, blank=True)
    last_event_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "best_shipments"
        indexes = [
            models.Index(fields=["status"], name="best_shipment_status_idx"),
            models.Index(fields=["order"], name="best_shipment_order_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.merchant_order_code} ({self.code_best or '-'})"


class BestWebhookEvent(models.Model):
    account = models.ForeignKey(
        BestExpressAccount,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="webhook_events",
    )
    code_best = models.CharField(max_length=100, blank=True, default="", db_index=True)
    merchant_order_code = models.CharField(max_length=100, blank=True, default="", db_index=True)
    best_status_code = models.CharField(max_length=32, blank=True, default="")
    payload = models.JSONField(default=dict)
    modified_at_ms = models.BigIntegerField(default=0)
    processed = models.BooleanField(default=False)
    processing_error = models.TextField(blank=True, default="")
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "best_webhook_events"
        constraints = [
            models.UniqueConstraint(
                fields=["code_best", "best_status_code", "modified_at_ms"],
                name="best_webhook_event_uniq",
            )
        ]

    def __str__(self) -> str:
        return f"{self.code_best or self.merchant_order_code} [{self.best_status_code}]"
```

`backend/apps/best_express/admin.py`:

```python
from django.contrib import admin

from apps.best_express.models import BestExpressAccount, BestShipment, BestWebhookEvent


@admin.register(BestExpressAccount)
class BestExpressAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "environment", "username", "is_active", "token_expires_at")
    list_filter = ("environment", "is_active")


@admin.register(BestShipment)
class BestShipmentAdmin(admin.ModelAdmin):
    list_display = ("merchant_order_code", "code_best", "status", "best_status_code", "total_fee", "updated_at")
    list_filter = ("status",)
    search_fields = ("merchant_order_code", "code_best")
    raw_id_fields = ("order",)


@admin.register(BestWebhookEvent)
class BestWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("code_best", "merchant_order_code", "best_status_code", "processed", "received_at")
    list_filter = ("processed",)
    search_fields = ("code_best", "merchant_order_code")
```

- [ ] **Step 4: Đăng ký app + settings + importlinter**

Trong `backend/config/settings_parts/base.py`:
- Thêm `"apps.best_express",` vào `INSTALLED_APPS` ngay sau dòng `"apps.hr",`.
- Thêm block settings (đặt sau block `LARK_INVENTORY_BOT_*`, ~dòng 313):

```python
# Best Express direct integration (apps.best_express). Credentials nằm trong DB
# (EncryptedCharField trên BestExpressAccount); đây chỉ là base URL default theo env.
BEST_EXPRESS_BASE_URL_TEST = os.getenv(
    "BEST_EXPRESS_BASE_URL_TEST", "http://sgp-seaedi-test.800best.com"
).rstrip("/")
BEST_EXPRESS_BASE_URL_PROD = os.getenv(
    "BEST_EXPRESS_BASE_URL_PROD", "http://sgp-seaedi.800best.com"
).rstrip("/")
```

Trong `backend/.importlinter`, sửa dòng layer trên cùng:

```
    apps.kiotviet : apps.facebook_ads : apps.google_ads : apps.best_express
```

(cùng layer với kiotviet nên được import `apps.kiotviet.models` cho EncryptedCharField — siblings phân tách bằng `:` không bị check độc lập, giống facebook_ads hiện tại; và được import `apps.orders` ở layer dưới cho FK.)

- [ ] **Step 5: Tạo migration**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "c:/Users/MYPC/Desktop/workspace/desi-order-wt-best-express/backend:/app" -w /app \
  desi-order-web-test:latest python manage.py makemigrations best_express
```

Expected: tạo `apps/best_express/migrations/0001_initial.py` với 3 models.

- [ ] **Step 6: Chạy test models, xác nhận PASS** (lệnh Global Constraints, path `apps/best_express/tests/test_models.py`, PHẢI có `--create-db`)

- [ ] **Step 7: Chạy lint import + ruff**

```bash
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "c:/Users/MYPC/Desktop/workspace/desi-order-wt-best-express/backend:/app" -w /app \
  desi-order-web-test:latest sh -c 'pip show import-linter >/dev/null 2>&1 && lint-imports || echo "import-linter not in image, skip"'
uv run --no-project --with ruff ruff check backend/apps/best_express
```

Expected: không lỗi (hoặc skip nếu image thiếu import-linter — không chặn).

- [ ] **Step 8: Commit**

```bash
git add backend/apps/best_express backend/config/settings_parts/base.py backend/.importlinter
git commit -m "feat(best-express): add account, shipment and webhook event models"
```

---

### Task 3: HTTP client + quản lý token

**Files:**
- Create: `backend/apps/best_express/infrastructure/__init__.py` (rỗng)
- Create: `backend/apps/best_express/infrastructure/best_client.py`
- Test: `backend/apps/best_express/tests/test_best_client.py`

**Interfaces:**
- Consumes: `BestExpressAccount` (Task 2), `BestExpressError` (Task 1).
- Produces: `BestExpressClient(account)` với methods:
  - `create_order(payload: dict) -> dict`
  - `update_order(payload: dict) -> dict`
  - `update_address(payload: dict) -> dict`
  - `cancel_order(code: str) -> dict`
  - `trace_query(codes: list[str], lang: str = "en-US") -> dict` (raise nếu >10 mã)
  - `estimate_fee(payload: dict) -> dict`
  - `print_waybill(codes: list[str]) -> dict` (raise nếu >50 mã)
  - Module constants: `TRACE_MAX_CODES = 10`, `PRINT_MAX_CODES = 50`, `API_PREFIX`.
  - Hàm module-level `ensure_token(account, force_refresh: bool = False) -> str`.

- [ ] **Step 1: Viết test client (failing)**

`backend/apps/best_express/tests/test_best_client.py`:

```python
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.best_express.exceptions import BestExpressError
from apps.best_express.infrastructure import best_client
from apps.best_express.models import BestExpressAccount


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code
        self.text = str(json_data)

    def json(self):
        return self._json


def make_account(**kwargs) -> BestExpressAccount:
    defaults = {
        "name": "T",
        "environment": "test",
        "base_url": "http://best.example",
        "username": f"user-{uuid.uuid4()}",
        "password": "pw",
    }
    defaults.update(kwargs)
    return BestExpressAccount.objects.create(**defaults)


@pytest.mark.django_db
def test_login_stores_token(monkeypatch):
    account = make_account()
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse({"token": "tok-123"})

    monkeypatch.setattr(best_client.requests, "post", fake_post)
    token = best_client.ensure_token(account)
    assert token == "tok-123"
    account.refresh_from_db()
    assert account.token == "tok-123"
    assert account.token_expires_at > timezone.now() + timedelta(days=28)
    assert calls[0][0] == "http://best.example/VietNamV3/v3/api/process/sears/User/Login"
    assert calls[0][1]["json"] == {"Username": account.username, "Password": "pw"}


@pytest.mark.django_db
def test_token_reused_when_valid(monkeypatch):
    account = make_account(
        token="cached", token_expires_at=timezone.now() + timedelta(days=10)
    )

    def fail_post(url, **kwargs):  # pragma: no cover - guard
        raise AssertionError("không được gọi login khi token còn hạn")

    monkeypatch.setattr(best_client.requests, "post", fail_post)
    assert best_client.ensure_token(account) == "cached"


@pytest.mark.django_db
def test_relogin_once_on_401(monkeypatch):
    account = make_account(
        token="stale", token_expires_at=timezone.now() + timedelta(days=10)
    )
    responses = [
        FakeResponse({}, status_code=401),          # create_order với token cũ
        FakeResponse({"token": "fresh"}),            # login lại
        FakeResponse({"Result": 1, "Code": "848", "RouteCode": "R1"}),  # retry OK
    ]
    seen = []

    def fake_post(url, **kwargs):
        seen.append((url, kwargs.get("headers", {})))
        return responses.pop(0)

    monkeypatch.setattr(best_client.requests, "post", fake_post)
    data = best_client.BestExpressClient(account).create_order({"Code": "DH-1"})
    assert data["Code"] == "848"
    assert seen[2][1]["Authorization"] == "Bearer fresh"


@pytest.mark.django_db
def test_result_2_raises_with_original_message(monkeypatch):
    account = make_account(
        token="tok", token_expires_at=timezone.now() + timedelta(days=10)
    )
    monkeypatch.setattr(
        best_client.requests,
        "post",
        lambda url, **kw: FakeResponse(
            {"Result": 2, "Message": "[ex0034] The order have cancelled"}
        ),
    )
    with pytest.raises(BestExpressError) as exc:
        best_client.BestExpressClient(account).cancel_order("848")
    assert exc.value.message == "[ex0034] The order have cancelled"


@pytest.mark.django_db
def test_login_rate_limited(monkeypatch):
    account = make_account()
    monkeypatch.setattr(
        best_client.requests, "post", lambda url, **kw: FakeResponse({"token": "t"})
    )
    for _ in range(best_client.LOGIN_RATE_LIMIT):
        best_client.ensure_token(account, force_refresh=True)
    with pytest.raises(BestExpressError):
        best_client.ensure_token(account, force_refresh=True)


@pytest.mark.django_db
def test_trace_and_print_limits(monkeypatch):
    account = make_account(
        token="tok", token_expires_at=timezone.now() + timedelta(days=10)
    )
    client = best_client.BestExpressClient(account)
    with pytest.raises(BestExpressError):
        client.trace_query([str(i) for i in range(11)])
    with pytest.raises(BestExpressError):
        client.print_waybill([str(i) for i in range(51)])


@pytest.mark.django_db
def test_ems_key_header_sent(monkeypatch):
    account = make_account(
        token="tok",
        token_expires_at=timezone.now() + timedelta(days=10),
        ems_key="EMS-1",
    )
    seen = {}

    def fake_post(url, **kwargs):
        seen["headers"] = kwargs["headers"]
        return FakeResponse({"Result": 1})

    monkeypatch.setattr(best_client.requests, "post", fake_post)
    best_client.BestExpressClient(account).estimate_fee({"Weight": 100})
    assert seen["headers"]["ems-key"] == "EMS-1"
```

- [ ] **Step 2: Chạy test, xác nhận FAIL** (`apps/best_express/tests/test_best_client.py`, lỗi import best_client)

- [ ] **Step 3: Implement client**

`backend/apps/best_express/infrastructure/best_client.py`:

```python
"""HTTP client cho Best Express API V3.

Token: hạn 30 ngày (lưu 29 ngày cho an toàn), re-login tối đa 1 lần khi 401.
Best giới hạn 5 lần login/phút — chặn bằng counter trên Redis cache.
Lỗi Best (Result=2) raise BestExpressError với Message NGUYÊN VĂN (passthrough).
"""

import logging
from datetime import timedelta

import requests
from django.core.cache import cache
from django.utils import timezone

from apps.best_express.exceptions import BestExpressError
from apps.best_express.models import BestExpressAccount

logger = logging.getLogger(__name__)

API_PREFIX = "/VietNamV3/v3/api/process/sears"
TRACE_MAX_CODES = 10
PRINT_MAX_CODES = 50
LOGIN_RATE_LIMIT = 5  # theo tài liệu Best: 1 phút không gọi token quá 5 lần
TOKEN_LIFETIME_DAYS = 29
_TIMEOUT = 30


def _login_url(account: BestExpressAccount) -> str:
    return f"{account.resolve_base_url().rstrip('/')}{API_PREFIX}/User/Login"


def ensure_token(account: BestExpressAccount, force_refresh: bool = False) -> str:
    if (
        not force_refresh
        and account.token
        and account.token_expires_at
        and account.token_expires_at > timezone.now()
    ):
        return account.token
    return _login(account)


def _login(account: BestExpressAccount) -> str:
    rate_key = f"best_express:login_count:{account.pk}:{account.username}"
    cache.add(rate_key, 0, timeout=60)
    if cache.incr(rate_key) > LOGIN_RATE_LIMIT:
        raise BestExpressError(
            "Best Express: vượt giới hạn 5 lần login/phút — thử lại sau."
        )
    response = requests.post(
        _login_url(account),
        json={"Username": account.username, "Password": account.password},
        headers={"Content-Type": "application/json"},
        timeout=_TIMEOUT,
    )
    if response.status_code >= 400:
        raise BestExpressError(
            f"Best Express login HTTP {response.status_code}: {response.text[:500]}"
        )
    data = response.json()
    token = str(data.get("token") or "").strip()
    if not token:
        raise BestExpressError(f"Best Express login không trả token: {data}")
    account.token = token
    account.token_expires_at = timezone.now() + timedelta(days=TOKEN_LIFETIME_DAYS)
    account.save(update_fields=["token", "token_expires_at", "updated_at"])
    return token


class BestExpressClient:
    def __init__(self, account: BestExpressAccount):
        self.account = account

    # -- core ---------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.account.resolve_base_url().rstrip('/')}{API_PREFIX}/{path}"

    def _headers(self, token: str) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        if self.account.ems_key:
            headers["ems-key"] = self.account.ems_key
        return headers

    def _post(self, path: str, payload: dict) -> dict:
        token = ensure_token(self.account)
        response = requests.post(
            self._url(path), json=payload, headers=self._headers(token), timeout=_TIMEOUT
        )
        if response.status_code == 401:
            token = ensure_token(self.account, force_refresh=True)
            response = requests.post(
                self._url(path),
                json=payload,
                headers=self._headers(token),
                timeout=_TIMEOUT,
            )
        if response.status_code >= 400:
            raise BestExpressError(
                f"Best Express HTTP {response.status_code}: {response.text[:500]}"
            )
        data = response.json()
        if isinstance(data, dict) and data.get("Result") == 2:
            raise BestExpressError(
                str(data.get("Message") or "Best Express error"), payload=data
            )
        return data

    # -- endpoints ------------------------------------------------------------

    def create_order(self, payload: dict) -> dict:
        return self._post("Order/Add", payload)

    def update_order(self, payload: dict) -> dict:
        return self._post("Order/Update", payload)

    def update_address(self, payload: dict) -> dict:
        return self._post("Order/UpdateAddress", payload)

    def cancel_order(self, code: str) -> dict:
        return self._post("Order/Cancel", {"Code": code})

    def trace_query(self, codes: list[str], lang: str = "en-US") -> dict:
        if len(codes) > TRACE_MAX_CODES:
            raise BestExpressError(
                f"TraceQuery tối đa {TRACE_MAX_CODES} mã/lần (nhận {len(codes)})."
            )
        return self._post("Order/Query", {"LangType": lang, "Codes": codes})

    def estimate_fee(self, payload: dict) -> dict:
        return self._post("Service/EstimateFee", payload)

    def print_waybill(self, codes: list[str]) -> dict:
        if len(codes) > PRINT_MAX_CODES:
            raise BestExpressError(
                f"PrintWayBill tối đa {PRINT_MAX_CODES} đơn/lần (nhận {len(codes)})."
            )
        return self._post("Print/PrintWayBill", {"codes": codes})
```

- [ ] **Step 4: Chạy test, xác nhận PASS** (`apps/best_express/tests/test_best_client.py`)

- [ ] **Step 5: Commit**

```bash
git add backend/apps/best_express
git commit -m "feat(best-express): add http client with token management"
```

---

### Task 4: Webhook service (store + apply event, idempotent)

**Files:**
- Create: `backend/apps/best_express/application/__init__.py` (rỗng)
- Create: `backend/apps/best_express/application/services/__init__.py` (rỗng)
- Create: `backend/apps/best_express/application/services/webhook_service.py`
- Test: `backend/apps/best_express/tests/test_webhook_service.py`

**Interfaces:**
- Consumes: models (Task 2), `map_best_status` (Task 1).
- Produces:
  - `store_event(account, payload: dict) -> BestWebhookEvent | None` — None nếu trùng (đã có event cùng code/status/modified_at).
  - `apply_event(event: BestWebhookEvent) -> bool` — True nếu shipment được cập nhật; False nếu unmatched/stale. Idempotent, transaction ngắn + `select_for_update`.

- [ ] **Step 1: Viết test (failing)**

`backend/apps/best_express/tests/test_webhook_service.py`:

```python
import uuid
from decimal import Decimal

import pytest

from apps.best_express.application.services import webhook_service
from apps.best_express.domain.status_map import BestShipmentStatus
from apps.best_express.models import BestExpressAccount, BestShipment, BestWebhookEvent


def make_account() -> BestExpressAccount:
    return BestExpressAccount.objects.create(
        name="T", username=f"u-{uuid.uuid4()}", password="pw"
    )


def make_shipment(account, **kwargs) -> BestShipment:
    defaults = {
        "merchant_order_code": f"DH-{uuid.uuid4()}",
        "code_best": f"848{uuid.uuid4().hex[:10]}",
    }
    defaults.update(kwargs)
    return BestShipment.objects.create(account=account, **defaults)


def webhook_payload(shipment, status="666", modified_at=1700000000000, **extra):
    payload = {
        "code_best": shipment.code_best,
        "merchant_order_code": shipment.merchant_order_code,
        "status": status,
        "status_content": "ok",
        "cod": 180000.0,
        "product_price": 180000.0,
        "freight_fee": 33880.0,
        "cod_fee": 0.0,
        "insurance_fee": 0.0,
        "vat_fee": 0.0,
        "return_fee": 0.0,
        "discount": 0.0,
        "total_fee": 33880.0,
        "weight": 600,
        "length": 140,
        "width": 200,
        "height": 150,
        "modified_at": modified_at,
    }
    payload.update(extra)
    return payload


@pytest.mark.django_db
def test_store_event_then_duplicate_returns_none():
    account = make_account()
    shipment = make_shipment(account)
    payload = webhook_payload(shipment)
    event = webhook_service.store_event(account, payload)
    assert event is not None
    assert webhook_service.store_event(account, payload) is None


@pytest.mark.django_db
def test_apply_event_updates_shipment():
    account = make_account()
    shipment = make_shipment(account)
    event = webhook_service.store_event(account, webhook_payload(shipment))
    assert webhook_service.apply_event(event) is True
    shipment.refresh_from_db()
    assert shipment.status == BestShipmentStatus.DELIVERED
    assert shipment.best_status_code == "666"
    assert shipment.freight_fee == Decimal("33880")
    assert shipment.total_fee == Decimal("33880")
    assert shipment.weight == 600
    assert shipment.last_event_at is not None
    event.refresh_from_db()
    assert event.processed is True


@pytest.mark.django_db
def test_apply_out_of_order_event_does_not_downgrade():
    account = make_account()
    shipment = make_shipment(account)
    newer = webhook_service.store_event(
        account, webhook_payload(shipment, status="666", modified_at=1700000002000)
    )
    webhook_service.apply_event(newer)
    older = webhook_service.store_event(
        account, webhook_payload(shipment, status="601", modified_at=1700000001000)
    )
    assert webhook_service.apply_event(older) is False
    shipment.refresh_from_db()
    assert shipment.status == BestShipmentStatus.DELIVERED
    older.refresh_from_db()
    assert older.processed is True
    assert "stale" in older.processing_error


@pytest.mark.django_db
def test_apply_unmatched_event_kept_for_reconciliation():
    account = make_account()
    event = webhook_service.store_event(
        account,
        {"code_best": "848unknown", "status": "666", "modified_at": 1700000000000},
    )
    assert webhook_service.apply_event(event) is False
    event.refresh_from_db()
    assert event.processed is False
    assert "unmatched" in event.processing_error


@pytest.mark.django_db
def test_apply_matches_by_merchant_code_and_backfills_code_best():
    account = make_account()
    shipment = make_shipment(account, code_best=None)
    payload = webhook_payload(shipment)
    payload["code_best"] = "84855000000009"
    event = webhook_service.store_event(account, payload)
    assert webhook_service.apply_event(event) is True
    shipment.refresh_from_db()
    assert shipment.code_best == "84855000000009"


@pytest.mark.django_db
def test_unknown_status_code_keeps_internal_status():
    account = make_account()
    shipment = make_shipment(account)
    event = webhook_service.store_event(
        account, webhook_payload(shipment, status="999")
    )
    assert webhook_service.apply_event(event) is True
    shipment.refresh_from_db()
    assert shipment.status == BestShipmentStatus.CREATED
    assert shipment.best_status_code == "999"
```

- [ ] **Step 2: Chạy test, xác nhận FAIL** (`apps/best_express/tests/test_webhook_service.py`)

- [ ] **Step 3: Implement webhook service**

`backend/apps/best_express/application/services/webhook_service.py`:

```python
"""Ghi + áp webhook event Best Express vào BestShipment (idempotent).

Đường áp event dùng chung cho webhook realtime và TraceQuery sync
(tracking_service) — một chỗ duy nhất quyết định trạng thái/phí.
"""

import logging
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction

from apps.best_express.domain.status_map import map_best_status
from apps.best_express.models import BestExpressAccount, BestShipment, BestWebhookEvent

logger = logging.getLogger(__name__)

_DECIMAL_FIELDS = (
    "cod",
    "product_price",
    "freight_fee",
    "cod_fee",
    "insurance_fee",
    "vat_fee",
    "return_fee",
    "discount",
    "total_fee",
)
_INT_FIELDS = ("weight", "length", "width", "height")


def store_event(
    account: BestExpressAccount | None, payload: dict
) -> BestWebhookEvent | None:
    """Lưu event thô; trả None nếu đã có event trùng (code, status, modified_at)."""
    code_best = str(payload.get("code_best") or "").strip()
    merchant_order_code = str(payload.get("merchant_order_code") or "").strip()
    best_status_code = str(payload.get("status") or "").strip()
    try:
        modified_at_ms = int(payload.get("modified_at") or 0)
    except (TypeError, ValueError):
        modified_at_ms = 0
    try:
        with transaction.atomic():
            return BestWebhookEvent.objects.create(
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


def _event_datetime(event: BestWebhookEvent) -> datetime | None:
    if not event.modified_at_ms:
        return None
    return datetime.fromtimestamp(event.modified_at_ms / 1000, tz=dt_timezone.utc)


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def apply_event(event: BestWebhookEvent) -> bool:
    """Áp event vào shipment khớp mã. True = shipment được cập nhật."""
    with transaction.atomic():
        shipment = None
        if event.code_best:
            shipment = (
                BestShipment.objects.select_for_update()
                .filter(code_best=event.code_best)
                .first()
            )
        if shipment is None and event.merchant_order_code:
            shipment = (
                BestShipment.objects.select_for_update()
                .filter(merchant_order_code=event.merchant_order_code)
                .first()
            )
        if shipment is None:
            event.processed = False
            event.processing_error = "unmatched: không tìm thấy BestShipment"
            event.save(update_fields=["processed", "processing_error"])
            return False

        event_dt = _event_datetime(event)
        if event_dt and shipment.last_event_at and event_dt < shipment.last_event_at:
            event.processed = True
            event.processing_error = "stale: event cũ hơn last_event_at (out-of-order)"
            event.save(update_fields=["processed", "processing_error"])
            return False

        payload = event.payload or {}
        mapped = map_best_status(event.best_status_code)
        if mapped is not None:
            shipment.status = mapped
        else:
            logger.warning(
                "Best webhook: mã trạng thái lạ %s (code_best=%s) — giữ status nội bộ",
                event.best_status_code,
                event.code_best,
            )
        shipment.best_status_code = event.best_status_code
        content = str(payload.get("status_content") or "").strip()
        if content:
            shipment.status_content = content
        for field in _DECIMAL_FIELDS:
            value = _to_decimal(payload.get(field))
            if value is not None:
                setattr(shipment, field, value)
        for field in _INT_FIELDS:
            value = payload.get(field)
            if isinstance(value, (int, float)) and value:
                setattr(shipment, field, int(value))
        if event.code_best and not shipment.code_best:
            shipment.code_best = event.code_best
        if event_dt:
            shipment.last_event_at = event_dt
        shipment.save()

        event.processed = True
        event.processing_error = ""
        event.save(update_fields=["processed", "processing_error"])
        return True
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/apps/best_express
git commit -m "feat(best-express): add idempotent webhook event service"
```

---

### Task 5: Webhook endpoint (transport) + URLs

**Files:**
- Create: `backend/apps/best_express/transport/__init__.py` (rỗng)
- Create: `backend/apps/best_express/transport/webhook_views.py`
- Create: `backend/apps/best_express/urls.py`
- Modify: `backend/config/urls.py` (thêm 1 dòng include)
- Test: `backend/apps/best_express/tests/test_webhook_view.py`

**Interfaces:**
- Consumes: `store_event`, `apply_event` (Task 4), `BestExpressAccount` (Task 2).
- Produces: endpoint `POST /api/best-express/webhook` (public, auth = Bearer webhook_token), luôn trả `{"Result": 1, "Message": ""}` khi event đã ghi; 401 khi token sai; 400 khi body không phải JSON object.

- [ ] **Step 1: Viết test (failing)**

`backend/apps/best_express/tests/test_webhook_view.py`:

```python
import uuid

import pytest
from rest_framework.test import APIClient

from apps.best_express.domain.status_map import BestShipmentStatus
from apps.best_express.models import BestExpressAccount, BestShipment, BestWebhookEvent

WEBHOOK_URL = "/api/best-express/webhook"


def make_account(webhook_token="hook-secret") -> BestExpressAccount:
    return BestExpressAccount.objects.create(
        name="T",
        username=f"u-{uuid.uuid4()}",
        password="pw",
        webhook_token=webhook_token,
    )


def auth_client(token="hook-secret") -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


@pytest.mark.django_db
def test_missing_or_wrong_token_401():
    make_account()
    assert APIClient().post(WEBHOOK_URL, {}, format="json").status_code == 401
    assert auth_client("wrong").post(WEBHOOK_URL, {}, format="json").status_code == 401


@pytest.mark.django_db
def test_valid_webhook_updates_shipment_and_returns_result_1():
    account = make_account()
    shipment = BestShipment.objects.create(
        account=account,
        merchant_order_code=f"DH-{uuid.uuid4()}",
        code_best=f"848{uuid.uuid4().hex[:10]}",
    )
    payload = {
        "code_best": shipment.code_best,
        "merchant_order_code": shipment.merchant_order_code,
        "status": "666",
        "status_content": "ok",
        "total_fee": 33880.0,
        "modified_at": 1700000000000,
    }
    response = auth_client().post(WEBHOOK_URL, payload, format="json")
    assert response.status_code == 200
    assert response.json() == {"Result": 1, "Message": ""}
    shipment.refresh_from_db()
    assert shipment.status == BestShipmentStatus.DELIVERED


@pytest.mark.django_db
def test_duplicate_webhook_still_returns_result_1():
    account = make_account()
    shipment = BestShipment.objects.create(
        account=account,
        merchant_order_code=f"DH-{uuid.uuid4()}",
        code_best=f"848{uuid.uuid4().hex[:10]}",
    )
    payload = {
        "code_best": shipment.code_best,
        "status": "202",
        "modified_at": 1700000000000,
    }
    assert auth_client().post(WEBHOOK_URL, payload, format="json").status_code == 200
    response = auth_client().post(WEBHOOK_URL, payload, format="json")
    assert response.status_code == 200
    assert response.json()["Result"] == 1
    assert BestWebhookEvent.objects.filter(code_best=shipment.code_best).count() == 1


@pytest.mark.django_db
def test_unmatched_code_stored_and_returns_result_1():
    make_account()
    payload = {"code_best": "848nope", "status": "666", "modified_at": 1700000000000}
    response = auth_client().post(WEBHOOK_URL, payload, format="json")
    assert response.status_code == 200
    assert response.json()["Result"] == 1
    event = BestWebhookEvent.objects.get(code_best="848nope")
    assert event.processed is False


@pytest.mark.django_db
def test_non_dict_body_400():
    make_account()
    response = auth_client().post(WEBHOOK_URL, ["not-a-dict"], format="json")
    assert response.status_code == 400
    assert response.json()["Result"] == 2
```

- [ ] **Step 2: Chạy test, xác nhận FAIL** (404 vì chưa có route)

- [ ] **Step 3: Implement view + urls**

`backend/apps/best_express/transport/webhook_views.py`:

```python
"""Webhook nhận trạng thái từ Best Express.

Auth: Bearer token tĩnh (BestExpressAccount.webhook_token — mình sinh, gửi sale
Best cấu hình trên trang V9). Best yêu cầu đối tác luôn phản hồi Result 1/2.
"""

import logging

from django.utils.crypto import constant_time_compare
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.best_express.application.services.webhook_service import apply_event, store_event
from apps.best_express.models import BestExpressAccount

logger = logging.getLogger(__name__)


def _resolve_account(request) -> BestExpressAccount | None:
    header = str(request.headers.get("Authorization") or "")
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    if not token:
        return None
    # webhook_token mã hóa Fernet (không deterministic) nên không filter được
    # trong SQL — so sánh trong Python; số account rất nhỏ (1-2 dòng).
    for account in BestExpressAccount.objects.filter(is_active=True):
        if account.webhook_token and constant_time_compare(account.webhook_token, token):
            return account
    return None


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def best_express_webhook(request):
    account = _resolve_account(request)
    if account is None:
        return Response(
            {"Result": 2, "Message": "Unauthorized"},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    payload = request.data
    if not isinstance(payload, dict):
        return Response(
            {"Result": 2, "Message": "Payload phải là JSON object"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    event = store_event(account, payload)
    if event is not None:
        try:
            apply_event(event)
        except Exception:
            # Event đã lưu — lỗi áp dụng xử lý lại sau qua đối soát, vẫn trả 1
            # để Best không retry vô hạn.
            logger.exception(
                "Best webhook: apply_event lỗi (event_id=%s)", event.pk
            )
    return Response({"Result": 1, "Message": ""})
```

`backend/apps/best_express/urls.py`:

```python
from django.urls import path

from apps.best_express.transport.webhook_views import best_express_webhook

urlpatterns = [
    path("webhook", best_express_webhook, name="best-express-webhook"),
]
```

Trong `backend/config/urls.py`, thêm sau dòng `re_path(r"^api/webhook/?", ...)`:

```python
    re_path(r"^api/best-express/?", include("apps.best_express.urls")),
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/apps/best_express backend/config/urls.py
git commit -m "feat(best-express): add webhook endpoint with bearer auth"
```

---

### Task 6: Shipment service (create / cancel / update / update-address)

**Files:**
- Create: `backend/apps/best_express/application/services/shipment_service.py`
- Test: `backend/apps/best_express/tests/test_shipment_service.py`

**Interfaces:**
- Consumes: `BestExpressClient` (Task 3), models (Task 2), `BestExpressError` (Task 1), `BestShipmentStatus` (Task 1).
- Produces:
  - `create_shipment(account, payload: dict, *, order=None) -> BestShipment` — payload là dict field Best (Code, ProductName, …).
  - `cancel_shipment(shipment) -> BestShipment`
  - `update_shipment(shipment, changes: dict) -> BestShipment`
  - `update_shipment_address(shipment, address: dict) -> dict` (address = dict các field UpdateAddress trừ Code)

- [ ] **Step 1: Viết test (failing)**

`backend/apps/best_express/tests/test_shipment_service.py`:

```python
import uuid
from decimal import Decimal

import pytest

from apps.best_express.application.services import shipment_service
from apps.best_express.domain.status_map import BestShipmentStatus
from apps.best_express.exceptions import BestExpressError
from apps.best_express.models import BestExpressAccount, BestShipment


def make_account() -> BestExpressAccount:
    return BestExpressAccount.objects.create(
        name="T", username=f"u-{uuid.uuid4()}", password="pw"
    )


def valid_payload(code=None) -> dict:
    return {
        "Code": code or f"DH-{uuid.uuid4()}",
        "ProductName": "Ốp lưng",
        "CollectAmount": 180000,
        "ProductPrice": 180000,
        "JourneyType": 1,
        "ServiceId": 12491,
        "PickupType": 2,
        "Weight": 600,
        "SourceCity": "Hà Nội",
        "SourceWard": "Phường Thượng Đình",
        "SourceAddress": "Nguyễn Trãi",
        "SourceName": "A",
        "SourcePhoneNumber": "0900000001",
        "DestCity": "Hồ Chí Minh",
        "DestWard": "Phường 13",
        "DestAddress": "Cộng Hòa",
        "DestName": "B",
        "DestPhoneNumber": "0900000002",
        "ReturnCity": "Hà Nội",
        "ReturnWard": "Phường Thượng Đình",
        "ReturnAddress": "Nguyễn Trãi",
        "ReturnName": "A",
        "ReturnPhoneNumber": "0900000001",
    }


class FakeClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def create_order(self, payload):
        self.calls.append(("create_order", payload))
        return self.responses.get(
            "create_order", {"Result": 1, "Code": "84855000000001", "RouteCode": "SG9"}
        )

    def cancel_order(self, code):
        self.calls.append(("cancel_order", code))
        return {"Result": 1, "Message": None}

    def update_order(self, payload):
        self.calls.append(("update_order", payload))
        return {"Result": 1, "Message": ""}

    def update_address(self, payload):
        self.calls.append(("update_address", payload))
        return {"Result": 1, "Message": "", "AddressTypeId": payload["AddressTypeId"]}


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(
        shipment_service, "BestExpressClient", lambda account: client
    )
    return client


@pytest.mark.django_db
def test_create_shipment_success(fake_client):
    account = make_account()
    payload = valid_payload()
    shipment = shipment_service.create_shipment(account, payload)
    assert shipment.code_best == "84855000000001"
    assert shipment.route_code == "SG9"
    assert shipment.status == BestShipmentStatus.CREATED
    assert shipment.cod == Decimal("180000")
    assert shipment.weight == 600
    # District trống được tự thêm (địa chỉ 2 cấp mới)
    sent = fake_client.calls[0][1]
    assert sent["SourceDistrict"] == ""
    assert sent["DestDistrict"] == ""
    assert sent["ReturnDistrict"] == ""


@pytest.mark.django_db
def test_create_shipment_missing_required_field(fake_client):
    account = make_account()
    payload = valid_payload()
    del payload["DestPhoneNumber"]
    with pytest.raises(BestExpressError) as exc:
        shipment_service.create_shipment(account, payload)
    assert "DestPhoneNumber" in exc.value.message
    assert fake_client.calls == []


@pytest.mark.django_db
def test_create_shipment_duplicate_merchant_code(fake_client):
    account = make_account()
    payload = valid_payload()
    shipment_service.create_shipment(account, payload)
    fake_client.responses["create_order"] = {"Result": 1, "Code": "848other"}
    with pytest.raises(BestExpressError):
        shipment_service.create_shipment(account, valid_payload(code=payload["Code"]))


@pytest.mark.django_db
def test_cancel_shipment(fake_client):
    account = make_account()
    shipment = shipment_service.create_shipment(account, valid_payload())
    shipment = shipment_service.cancel_shipment(shipment)
    assert shipment.status == BestShipmentStatus.CANCELLED
    assert fake_client.calls[-1] == ("cancel_order", "84855000000001")


@pytest.mark.django_db
def test_update_shipment_merges_changes(fake_client):
    account = make_account()
    shipment = shipment_service.create_shipment(account, valid_payload())
    shipment = shipment_service.update_shipment(
        shipment, {"CollectAmount": 200000, "Weight": 700}
    )
    assert shipment.cod == Decimal("200000")
    assert shipment.weight == 700
    action, sent = fake_client.calls[-1]
    assert action == "update_order"
    assert sent["Code"] == "84855000000001"
    assert sent["CollectAmount"] == 200000


@pytest.mark.django_db
def test_update_shipment_address(fake_client):
    account = make_account()
    shipment = shipment_service.create_shipment(account, valid_payload())
    result = shipment_service.update_shipment_address(
        shipment,
        {
            "AddressTypeId": 2,
            "Address": "Thảo Điền",
            "CityName": "Hồ Chí Minh",
            "DistrictName": "",
            "WardName": "Thảo Điền",
            "ContactName": "B",
            "PhoneNumber1": "0900000002",
        },
    )
    assert result["Result"] == 1
    action, sent = fake_client.calls[-1]
    assert action == "update_address"
    assert sent["Code"] == "84855000000001"
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

- [ ] **Step 3: Implement shipment service**

`backend/apps/best_express/application/services/shipment_service.py`:

```python
"""Tạo/hủy/cập nhật vận đơn Best Express + ghi BestShipment.

Giới hạn nghiệp vụ phía Best (hủy trước 202, sửa địa chỉ trước 303, số lần
sửa, hạ COD…) KHÔNG re-implement ở đây — gọi thẳng và passthrough lỗi Best.
"""

from decimal import Decimal

from apps.best_express.domain.status_map import BestShipmentStatus
from apps.best_express.exceptions import BestExpressError
from apps.best_express.infrastructure.best_client import BestExpressClient
from apps.best_express.models import BestExpressAccount, BestShipment

# *District không bắt buộc (địa chỉ 2 cấp mới từ 01/07/2025 để trống District).
REQUIRED_FIELDS = (
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
    "DestCity",
    "DestWard",
    "DestAddress",
    "DestName",
    "DestPhoneNumber",
    "ReturnCity",
    "ReturnWard",
    "ReturnAddress",
    "ReturnName",
    "ReturnPhoneNumber",
)
_DISTRICT_FIELDS = ("SourceDistrict", "DestDistrict", "ReturnDistrict")


def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(0)


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def create_shipment(
    account: BestExpressAccount, payload: dict, *, order=None
) -> BestShipment:
    missing = [
        field for field in REQUIRED_FIELDS if payload.get(field) in (None, "")
    ]
    if missing:
        raise BestExpressError(f"Thiếu trường bắt buộc: {', '.join(missing)}")
    payload = dict(payload)
    for field in _DISTRICT_FIELDS:
        payload.setdefault(field, "")

    merchant_order_code = str(payload["Code"]).strip()
    if BestShipment.objects.filter(merchant_order_code=merchant_order_code).exists():
        raise BestExpressError(
            f"Mã đơn {merchant_order_code} đã có vận đơn Best — không tạo trùng."
        )

    data = BestExpressClient(account).create_order(payload)
    return BestShipment.objects.create(
        account=account,
        order=order,
        merchant_order_code=merchant_order_code,
        code_best=str(data.get("Code") or "").strip() or None,
        route_code=str(data.get("RouteCode") or "").strip(),
        status=BestShipmentStatus.CREATED,
        cod=_to_decimal(payload.get("CollectAmount")),
        product_price=_to_decimal(payload.get("ProductPrice")),
        weight=_to_int(payload.get("Weight")),
        length=_to_int(payload.get("Length")),
        width=_to_int(payload.get("Width")),
        height=_to_int(payload.get("Height")),
        journey_type=_to_int(payload.get("JourneyType")) or 1,
        service_id=_to_int(payload.get("ServiceId")) or 12491,
        pickup_type=_to_int(payload.get("PickupType")) or 2,
        request_payload=payload,
    )


def _best_code(shipment: BestShipment) -> str:
    return shipment.code_best or shipment.merchant_order_code


def cancel_shipment(shipment: BestShipment) -> BestShipment:
    BestExpressClient(shipment.account).cancel_order(_best_code(shipment))
    shipment.status = BestShipmentStatus.CANCELLED
    shipment.save(update_fields=["status", "updated_at"])
    return shipment


def update_shipment(shipment: BestShipment, changes: dict) -> BestShipment:
    payload = {"Code": _best_code(shipment), **changes}
    BestExpressClient(shipment.account).update_order(payload)
    if "CollectAmount" in changes:
        shipment.cod = _to_decimal(changes["CollectAmount"])
    if "ProductPrice" in changes:
        shipment.product_price = _to_decimal(changes["ProductPrice"])
    for best_field, model_field in (
        ("Weight", "weight"),
        ("Length", "length"),
        ("Width", "width"),
        ("Height", "height"),
        ("ServiceId", "service_id"),
    ):
        if best_field in changes:
            setattr(shipment, model_field, _to_int(changes[best_field]))
    shipment.request_payload = {**(shipment.request_payload or {}), **changes}
    shipment.save()
    return shipment


def update_shipment_address(shipment: BestShipment, address: dict) -> dict:
    payload = {"Code": _best_code(shipment), **address}
    data = BestExpressClient(shipment.account).update_address(payload)
    shipment.request_payload = {**(shipment.request_payload or {}), "UpdateAddress": address}
    shipment.save(update_fields=["request_payload", "updated_at"])
    return data
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/apps/best_express
git commit -m "feat(best-express): add shipment create/cancel/update service"
```

---

### Task 7: Tracking service (trace + estimate fee + print)

**Files:**
- Create: `backend/apps/best_express/application/services/tracking_service.py`
- Test: `backend/apps/best_express/tests/test_tracking_service.py`

**Interfaces:**
- Consumes: `BestExpressClient` + constants (Task 3), `store_event`/`apply_event` (Task 4).
- Produces:
  - `trace(account, codes: list[str], lang: str = "en-US") -> list[dict]` — tự chunk 10 mã/call, sync trạng thái ngược vào BestShipment qua đường event.
  - `estimate_fee(account, params: dict) -> dict` — trả nguyên `data` object của Best.
  - `print_waybills(account, codes: list[str]) -> list[str]` — list URL PDF, tự chunk 50.

- [ ] **Step 1: Viết test (failing)**

`backend/apps/best_express/tests/test_tracking_service.py`:

```python
import uuid

import pytest

from apps.best_express.application.services import tracking_service
from apps.best_express.domain.status_map import BestShipmentStatus
from apps.best_express.models import BestExpressAccount, BestShipment


def make_account() -> BestExpressAccount:
    return BestExpressAccount.objects.create(
        name="T", username=f"u-{uuid.uuid4()}", password="pw"
    )


class FakeClient:
    def __init__(self):
        self.trace_calls = []
        self.print_calls = []

    def trace_query(self, codes, lang="en-US"):
        self.trace_calls.append(codes)
        return {
            "Result": 1,
            "data": [
                {
                    "merchant_order_code": f"DH-for-{code}",
                    "code_best": code,
                    "traceLogs": [
                        {"status": "202", "modified_at": 1700000001000},
                        {"status": "666", "modified_at": 1700000002000},
                    ],
                }
                for code in codes
            ],
        }

    def estimate_fee(self, payload):
        return {"Result": 1, "Message": "", "data": {"TotalFee": 19800.0}}

    def print_waybill(self, codes):
        self.print_calls.append(codes)
        return {"Result": 1, "Url": f"http://pdf/{len(codes)}.pdf", "Guid": "g"}


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(
        tracking_service, "BestExpressClient", lambda account: client
    )
    return client


@pytest.mark.django_db
def test_trace_chunks_by_10(fake_client):
    account = make_account()
    codes = [f"848{i:05d}" for i in range(23)]
    results = tracking_service.trace(account, codes)
    assert [len(c) for c in fake_client.trace_calls] == [10, 10, 3]
    assert len(results) == 23


@pytest.mark.django_db
def test_trace_syncs_shipment_status(fake_client):
    account = make_account()
    code = f"848{uuid.uuid4().hex[:10]}"
    shipment = BestShipment.objects.create(
        account=account, merchant_order_code=f"DH-{uuid.uuid4()}", code_best=code
    )
    tracking_service.trace(account, [code])
    shipment.refresh_from_db()
    assert shipment.status == BestShipmentStatus.DELIVERED
    assert shipment.best_status_code == "666"


@pytest.mark.django_db
def test_trace_sync_idempotent(fake_client):
    account = make_account()
    code = f"848{uuid.uuid4().hex[:10]}"
    BestShipment.objects.create(
        account=account, merchant_order_code=f"DH-{uuid.uuid4()}", code_best=code
    )
    tracking_service.trace(account, [code])
    tracking_service.trace(account, [code])  # chạy lại — không lỗi, không đổi
    from apps.best_express.models import BestWebhookEvent

    assert BestWebhookEvent.objects.filter(code_best=code).count() == 2


@pytest.mark.django_db
def test_estimate_fee_returns_data(fake_client):
    account = make_account()
    data = tracking_service.estimate_fee(account, {"Weight": 100})
    assert data["TotalFee"] == 19800.0


@pytest.mark.django_db
def test_print_waybills_chunks_by_50(fake_client):
    account = make_account()
    codes = [f"848{i:05d}" for i in range(120)]
    urls = tracking_service.print_waybills(account, codes)
    assert [len(c) for c in fake_client.print_calls] == [50, 50, 20]
    assert len(urls) == 3
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

- [ ] **Step 3: Implement tracking service**

`backend/apps/best_express/application/services/tracking_service.py`:

```python
"""Tra cứu hành trình / phí tạm tính / in vận đơn Best Express.

TraceQuery sync ngược vào BestShipment dùng CHUNG đường event với webhook
(store_event + apply_event) — idempotent nhờ unique constraint trên event.
"""

import logging

from apps.best_express.application.services.webhook_service import apply_event, store_event
from apps.best_express.infrastructure.best_client import (
    PRINT_MAX_CODES,
    TRACE_MAX_CODES,
    BestExpressClient,
)
from apps.best_express.models import BestExpressAccount

logger = logging.getLogger(__name__)

_TRACE_EVENT_FIELDS = (
    "status",
    "status_content",
    "modified_at",
    "cod",
    "product_price",
    "freight_fee",
    "cod_fee",
    "insurance_fee",
    "vat_fee",
    "return_fee",
    "discount",
    "total_fee",
    "weight",
    "length",
    "width",
    "height",
)


def _chunks(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def trace(
    account: BestExpressAccount, codes: list[str], lang: str = "en-US"
) -> list[dict]:
    client = BestExpressClient(account)
    cleaned = [str(code).strip() for code in codes if str(code or "").strip()]
    results: list[dict] = []
    for chunk in _chunks(cleaned, TRACE_MAX_CODES):
        data = client.trace_query(chunk, lang=lang)
        results.extend(data.get("data") or [])
    _sync_trace_results(account, results)
    return results


def _sync_trace_results(account: BestExpressAccount, results: list[dict]) -> None:
    for item in results:
        code_best = str(item.get("code_best") or "").strip()
        merchant_order_code = str(item.get("merchant_order_code") or "").strip()
        trace_logs = item.get("traceLogs") or []
        ordered = sorted(
            (log for log in trace_logs if isinstance(log, dict)),
            key=lambda log: log.get("modified_at") or 0,
        )
        for log in ordered:
            payload = {
                key: log[key] for key in _TRACE_EVENT_FIELDS if key in log
            }
            payload["code_best"] = code_best
            payload["merchant_order_code"] = merchant_order_code
            event = store_event(account, payload)
            if event is not None:
                apply_event(event)


def estimate_fee(account: BestExpressAccount, params: dict) -> dict:
    data = BestExpressClient(account).estimate_fee(params)
    return data.get("data") or {}


def print_waybills(account: BestExpressAccount, codes: list[str]) -> list[str]:
    client = BestExpressClient(account)
    cleaned = [str(code).strip() for code in codes if str(code or "").strip()]
    urls: list[str] = []
    for chunk in _chunks(cleaned, PRINT_MAX_CODES):
        data = client.print_waybill(chunk)
        url = str(data.get("Url") or "").strip()
        if url:
            urls.append(url)
    return urls
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/apps/best_express
git commit -m "feat(best-express): add tracking, estimate fee and print services"
```

---

### Task 8: Smoke test command + full suite + lint + push

**Files:**
- Create: `backend/apps/best_express/management/__init__.py` (rỗng)
- Create: `backend/apps/best_express/management/commands/__init__.py` (rỗng)
- Create: `backend/apps/best_express/management/commands/best_smoke_test.py`

**Interfaces:**
- Consumes: toàn bộ services (Task 3, 6, 7).
- Produces: `manage.py best_smoke_test --account-id N [--create]` — smoke test thủ công trên env test Best.

- [ ] **Step 1: Implement command** (không cần unit test — công cụ thủ công, side effect là API thật)

`backend/apps/best_express/management/commands/best_smoke_test.py`:

```python
"""Smoke test đấu nối Best Express (chạy TAY trên tài khoản env test).

    python manage.py best_smoke_test --account-id 1            # login + estimate
    python manage.py best_smoke_test --account-id 1 --create   # + tạo/trace/hủy đơn test

KHÔNG chạy trên account production.
"""

import uuid

from django.core.management.base import BaseCommand, CommandError

from apps.best_express.application.services import shipment_service, tracking_service
from apps.best_express.infrastructure.best_client import ensure_token
from apps.best_express.models import BestExpressAccount


class Command(BaseCommand):
    help = "Smoke test Best Express API (login → estimate → [create → trace → cancel])"

    def add_arguments(self, parser):
        parser.add_argument("--account-id", type=int, required=True)
        parser.add_argument(
            "--create",
            action="store_true",
            help="Tạo đơn test thật trên env test rồi hủy ngay",
        )

    def handle(self, *args, **options):
        account = BestExpressAccount.objects.filter(pk=options["account_id"]).first()
        if account is None:
            raise CommandError(f"Không tìm thấy account id={options['account_id']}")
        if account.environment != "test" and options["create"]:
            raise CommandError("--create chỉ cho phép trên account environment=test")

        token = ensure_token(account, force_refresh=True)
        self.stdout.write(self.style.SUCCESS(f"[1/4] Login OK (token {token[:16]}…)"))

        fee = tracking_service.estimate_fee(
            account,
            {
                "ServiceId": 12491,
                "SourceCity": "Hà Nội",
                "SourceDistrict": "Quận Thanh Xuân",
                "SourceWard": "Phường Thượng Đình",
                "DestCity": "Hồ Chí Minh",
                "DestDistrict": "Tân Bình",
                "DestWard": "Phường 13",
                "Cod": 100000,
                "ProductPrice": 100000,
                "Weight": 500,
            },
        )
        self.stdout.write(
            self.style.SUCCESS(f"[2/4] EstimateFee OK (TotalFee={fee.get('TotalFee')})")
        )

        if not options["create"]:
            self.stdout.write("[3/4][4/4] Bỏ qua create/cancel (thêm --create để chạy)")
            return

        code = f"DESITEST{uuid.uuid4().hex[:12].upper()}"
        shipment = shipment_service.create_shipment(
            account,
            {
                "Code": code,
                "ProductName": "Đơn test DESI - không xử lý",
                "CollectAmount": 0,
                "ProductPrice": 0,
                "JourneyType": 1,
                "ServiceId": 12491,
                "PickupType": 2,
                "Weight": 500,
                "SourceCity": "Hà Nội",
                "SourceDistrict": "Quận Thanh Xuân",
                "SourceWard": "Phường Thượng Đình",
                "SourceAddress": "Đường Nguyễn Trãi",
                "SourceName": "DESI Test",
                "SourcePhoneNumber": "0900000000",
                "DestCity": "Hồ Chí Minh",
                "DestDistrict": "Tân Bình",
                "DestWard": "Phường 13",
                "DestAddress": "Cộng Hòa",
                "DestName": "DESI Test",
                "DestPhoneNumber": "0900000000",
                "ReturnCity": "Hà Nội",
                "ReturnDistrict": "Quận Thanh Xuân",
                "ReturnWard": "Phường Thượng Đình",
                "ReturnAddress": "Đường Nguyễn Trãi",
                "ReturnName": "DESI Test",
                "ReturnPhoneNumber": "0900000000",
                "Note": "Đơn test API. Không cần thực hiện hành động nào.",
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"[3/4] CreateOrder OK (code_best={shipment.code_best}, route={shipment.route_code})"
            )
        )

        tracking_service.trace(account, [shipment.code_best])
        shipment_service.cancel_shipment(shipment)
        self.stdout.write(self.style.SUCCESS("[4/4] Trace + Cancel OK"))
```

- [ ] **Step 2: Chạy TOÀN BỘ suite app + ruff**

```bash
# full app suite (lệnh Global Constraints, path apps/best_express)
uv run --no-project --with ruff ruff check backend/apps/best_express
```

Expected: tất cả tests PASS, ruff sạch.

- [ ] **Step 3: Chạy thêm suite liên quan để chắc không vỡ gì**

Chạy `apps/kiotviet/tests apps/orders/tests` bằng lệnh Global Constraints (giữ `-e POSTGRES_DB=desi_bestexp`). Expected: pass như baseline (6 fail pre-existing toàn repo KHÔNG nằm trong 2 path này — nếu có fail mới, dừng và báo).

- [ ] **Step 4: Commit + push + MR**

```bash
git add backend/apps/best_express
git commit -m "feat(best-express): add smoke test management command"
git push -u origin feat/best-express-integration
```

Sau khi push, lấy link tạo MR từ output của GitLab và báo lại user. KHÔNG merge.

---

## Ghi chú deviation so với spec

- `token_store.py` gộp vào `best_client.py` (tránh import vòng qua `BestExpressError`; tách `exceptions.py` riêng). Cấu trúc còn lại giữ nguyên spec.
- `transport/serializers.py` bỏ — webhook không cần serializer riêng (payload lưu thô JSONField, validate tối thiểu là "phải là dict").
- Redis login lock của spec §4 thay bằng counter + refresh-from-db trước khi login (chống stale-worker).
- Spec §3.1 ghi EncryptedTextField cho token — dùng EncryptedCharField(max_length=4096) vì repo chỉ có field Char.
