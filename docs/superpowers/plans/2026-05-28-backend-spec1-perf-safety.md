# Backend Spec 1 — Perf & Safety Quick Wins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Áp dụng các sửa đổi hiệu năng + an toàn rủi ro thấp đã thống nhất ở Spec 1: connection pooling, vá 2 chỗ N+1, thu hẹp except quá rộng, fail-fast secret production, security headers production, fix BOM pytest.

**Architecture:** Tất cả thay đổi cục bộ, không refactor cấu trúc, không migration DB. Mỗi task TDD (test trước → impl → xanh → commit). Sau task cuối chạy bộ test mở rộng + verify pytest collect.

**Tech Stack:** Django 5, DRF, PostgreSQL, pytest (sẽ được sửa BOM), `manage.py test` cho test thực sự, `assertNumQueries` để chống hồi quy N+1.

**Spec gốc:** [docs/superpowers/specs/2026-05-28-backend-spec1-perf-safety-design.md](../specs/2026-05-28-backend-spec1-perf-safety-design.md)

---

## Quy ước chung

- Mọi command chạy từ thư mục repo root (`/Users/desi/Desktop/workspace/desi-order`) trừ khi nói khác.
- Backend test chạy bằng `python manage.py test <app>.<module> -v 2` từ thư mục `backend/` với Postgres/Redis tạm + biến môi trường đúng (memory dự án).
- Trước mỗi commit: `git branch --show-current` phải in `backend-perf-safety-improvements-20260528` (memory: shared workdir hazard).
- Commit message tiếng Anh, ngắn gọn, không kèm Co-Authored-By.

---

## Task 1: Fix BOM `pytest.ini`

**Files:**
- Modify: `backend/pytest.ini`

Lý do làm trước: các task sau cần `pytest --collect-only` để verify; hiện file đang BOM nên pytest hỏng.

- [ ] **Step 1: Xác minh trạng thái hiện tại**

```bash
file backend/pytest.ini
```
Expected: `backend/pytest.ini: Unicode text, UTF-8 (with BOM) text`

- [ ] **Step 2: Ghi lại file không BOM + thêm testpaths**

Dùng tool `Write` ghi đè `backend/pytest.ini` với nội dung sau (đảm bảo không BOM):

```ini
[pytest]
DJANGO_SETTINGS_MODULE = config.settings
python_files = tests.py test_*.py *_tests.py
testpaths = apps
```

- [ ] **Step 3: Verify hết BOM**

```bash
file backend/pytest.ini
```
Expected: `backend/pytest.ini: ASCII text` (không còn "BOM").

- [ ] **Step 4: Verify pytest collect-only chạy được**

```bash
cd backend && python -m pytest --collect-only -q 2>&1 | tail -5
```
Expected: in ra số test collect (không cần xanh hết, chỉ cần KHÔNG còn lỗi BOM `INI file: invalid character ...`).

- [ ] **Step 5: Commit**

```bash
git add backend/pytest.ini
git commit -m "chore: fix UTF-8 BOM in pytest.ini and add testpaths"
```

---

## Task 2: DB connection pooling

**Files:**
- Modify: `backend/config/settings_parts/base.py` (block `DATABASES`, dòng 91–100)

- [ ] **Step 1: Viết test ngắn xác nhận settings load đúng**

Create: `backend/apps/users/tests/test_db_settings.py` (hoặc append vào file test settings nếu có sẵn — nếu chưa, tạo mới ở app `users` để gọn). Nội dung:

```python
from django.conf import settings
from django.test import SimpleTestCase


class DatabaseSettingsTests(SimpleTestCase):
    def test_default_db_has_pooling(self):
        default = settings.DATABASES["default"]
        self.assertGreaterEqual(default.get("CONN_MAX_AGE", 0), 60)
        self.assertTrue(default.get("CONN_HEALTH_CHECKS", False))
        self.assertIn("connect_timeout", default.get("OPTIONS", {}))
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd backend && python manage.py test apps.users.tests.test_db_settings -v 2
```
Expected: FAIL (CONN_MAX_AGE = 0).

- [ ] **Step 3: Sửa block DATABASES trong `base.py`**

Thay thế:
```python
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "desi_order"),
        "USER": os.getenv("POSTGRES_USER", "desi_order"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "desi_order"),
        "HOST": os.getenv("POSTGRES_HOST", "postgres"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }
}
```
Bằng:
```python
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "desi_order"),
        "USER": os.getenv("POSTGRES_USER", "desi_order"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "desi_order"),
        "HOST": os.getenv("POSTGRES_HOST", "postgres"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(os.getenv("DJANGO_CONN_MAX_AGE", "600")),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"connect_timeout": 10},
    }
}
```

- [ ] **Step 4: Chạy lại test, xác nhận PASS**

```bash
cd backend && python manage.py test apps.users.tests.test_db_settings -v 2
```
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add backend/config/settings_parts/base.py backend/apps/users/tests/test_db_settings.py
git commit -m "perf(db): enable persistent connections (CONN_MAX_AGE, health checks)"
```

---

## Task 3: Vá N+1 export Excel (prefetch `need_type`)

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_export_view.py` (dòng 1–18 imports, dòng 140–145 queryset)
- Test: `backend/apps/orders/tests/test_export_orders_n_plus_one.py` (tạo mới)

- [ ] **Step 1: Viết test `assertNumQueries` chống hồi quy N+1**

Create: `backend/apps/orders/tests/test_export_orders_n_plus_one.py`

```python
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import (
    NeedGroup,
    NeedType,
    Order,
    OrderAssessment,
    OrderNeed,
)


class ExportOrdersNPlusOneTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="exporter", password="x", is_staff=True)
        self.channel = Channel.objects.create(code="CH1", name="Channel 1")
        self.customer = Customer.objects.create(name="C", phone_normalized="0900000001")
        self.group = NeedGroup.objects.create(code="G1", name_vi="G1", is_active=True)
        self.types = [
            NeedType.objects.create(group=self.group, code=f"N{i}", name_vi=f"Need {i}", is_active=True)
            for i in range(5)
        ]

    def _make_order_with_needs(self, n_needs: int) -> Order:
        order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="X",
            phone="0900000001",
        )
        assessment = OrderAssessment.objects.create(order=order)
        for i in range(n_needs):
            OrderNeed.objects.create(order_assessment=assessment, need_type=self.types[i])
        return order

    def test_export_with_needs_column_is_constant_queries(self):
        # 3 orders x 3 needs = 9 needs
        ids = [self._make_order_with_needs(3).id for _ in range(3)]
        client = APIClient()
        client.force_authenticate(self.user)
        url = reverse("order-export")  # adjust if route name differs
        payload = {"ids": ids, "columns": [{"key": "needs", "header": "Needs"}]}
        with self.assertNumQueries(_max_queries=15) if False else self.assertNumQueries(__import__("unittest").mock.ANY):
            # Use a soft assertion: capture count and assert <= threshold.
            # (assertNumQueries strict number is brittle across Django versions; use a manual capture below.)
            pass

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            resp = client.post(url, payload, format="json")
        self.assertEqual(resp.status_code, 200)
        # Must be <= 15 regardless of need count. If N+1 returns, will be ~30+.
        self.assertLessEqual(len(ctx.captured_queries), 15, f"too many queries: {len(ctx.captured_queries)}")
```

> Ghi chú: nếu `reverse("order-export")` không khớp tên url thực tế, dò qua `backend/apps/orders/urls.py` và sửa lại. Đây là điểm cần xác minh khi chạy.

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd backend && python manage.py test apps.orders.tests.test_export_orders_n_plus_one -v 2
```
Expected: FAIL (số query > 15 do N+1 trên `need_type`).

- [ ] **Step 3: Sửa view export — thêm Prefetch có select_related need_type**

Trong `order_export_view.py`:

Imports (thêm vào đầu file cùng các import models):
```python
from django.db.models import Prefetch
from apps.orders.models import Order, OrderNeed
```

Thay queryset (dòng 140–145):
```python
    orders = (
        Order.objects.select_related("channel", "kiotviet_channel", "customer", "staff")
        .prefetch_related(
            "assessment",
            Prefetch(
                "assessment__needs",
                queryset=OrderNeed.objects.select_related("need_type"),
            ),
        )
        .filter(id__in=ids, deleted_at__isnull=True)
        .order_by("-created_at", "-id")
    )
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

```bash
cd backend && python manage.py test apps.orders.tests.test_export_orders_n_plus_one -v 2
```
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/transport/views/order_views_impl/order_export_view.py backend/apps/orders/tests/test_export_orders_n_plus_one.py
git commit -m "perf(orders): prefetch OrderNeed.need_type in export to avoid N+1"
```

---

## Task 4: Vá N+1 `get_product_count` (annotate)

**Files:**
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/product_item_crm_serializers.py` (vùng 155–181)
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py` (dòng 417–425)
- Test: `backend/apps/orders/tests/test_product_categories_n_plus_one.py` (tạo mới)

- [ ] **Step 1: Viết test chống hồi quy**

Create: `backend/apps/orders/tests/test_product_categories_n_plus_one.py`

```python
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from apps.orders.models import Product, ProductCategory


class ProductCategoryListQueryCountTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="u", password="x", is_staff=True)
        self.cats = [
            ProductCategory.objects.create(code=f"C{i}", name=f"Cat {i}") for i in range(5)
        ]
        for c in self.cats:
            for j in range(3):
                p = Product.objects.create(sku=f"S-{c.code}-{j}", name=f"P{j}")
                p.categories.add(c)

    def test_list_query_count_constant(self):
        client = APIClient()
        client.force_authenticate(self.user)
        url = reverse("product-categories-list")  # adjust if differs
        with CaptureQueriesContext(connection) as ctx:
            resp = client.get(url + "?offset=0&limit=50")
        self.assertEqual(resp.status_code, 200)
        # Trước fix: 1 query danh sách + N query count -> ~1 + 5 = 6+. Sau fix: ~3.
        self.assertLessEqual(len(ctx.captured_queries), 6)
        # product_count đúng:
        items = resp.json() if isinstance(resp.json(), list) else resp.json().get("results", resp.json())
        by_code = {item["code"]: item for item in items}
        for c in self.cats:
            self.assertEqual(by_code[c.code]["product_count"], 3)
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd backend && python manage.py test apps.orders.tests.test_product_categories_n_plus_one -v 2
```
Expected: FAIL (số query nhiều hơn ngưỡng do `.count()` trong serializer mỗi category).

- [ ] **Step 3: Sửa serializer**

Trong `product_item_crm_serializers.py`:

- Thay dòng 156:
  ```python
      product_count = serializers.SerializerMethodField()
  ```
  Bằng:
  ```python
      product_count = serializers.IntegerField(read_only=True)
  ```

- Xóa method dòng 180–181:
  ```python
      def get_product_count(self, obj) -> int:
          return obj.products.count()
  ```

- [ ] **Step 4: Sửa view — annotate `product_count`**

Trong `order_ingest_product_dashboard_view.py`, sửa block 418–425:

Thay:
```python
        queryset = ProductCategory.objects.filter(deleted_at__isnull=True).prefetch_related("products")
        if search_query:
            queryset = queryset.filter(Q(name__icontains=search_query) | Q(code__icontains=search_query))

        queryset = queryset.order_by("sort_order", "name", "id")[
            pagination["offset"] : pagination["offset"] + pagination["limit"]
        ]
```
Bằng:
```python
        from django.db.models import Count
        queryset = (
            ProductCategory.objects.filter(deleted_at__isnull=True)
            .prefetch_related("products")
            .annotate(product_count=Count("products", distinct=True))
        )
        if search_query:
            queryset = queryset.filter(Q(name__icontains=search_query) | Q(code__icontains=search_query))

        queryset = queryset.order_by("sort_order", "name", "id")[
            pagination["offset"] : pagination["offset"] + pagination["limit"]
        ]
```

(Nếu `Count` đã import ở đầu file thì bỏ dòng `from django.db.models import Count` tại chỗ — kiểm tra imports hiện hành trước.)

- [ ] **Step 5: Chạy test, xác nhận PASS**

```bash
cd backend && python manage.py test apps.orders.tests.test_product_categories_n_plus_one -v 2
```
Expected: OK.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/transport/serializers/order_serializers_impl/product_item_crm_serializers.py \
        backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py \
        backend/apps/orders/tests/test_product_categories_n_plus_one.py
git commit -m "perf(orders): annotate product_count to drop per-category COUNT query"
```

---

## Task 5: Thu hẹp `except` rộng — audit middleware

**Files:**
- Modify: `backend/apps/audit/middleware_impl.py` (dòng 169–178)

- [ ] **Step 1: Đọc lại đoạn cần sửa để xác nhận indent**

Đoạn hiện tại (dòng 170–178):
```python
        if request.method in ['POST', 'PUT', 'PATCH']:
            try:
                if request.content_type == 'application/json' and request.body:
                    body = json.loads(request.body.decode('utf-8'))
                    # Giới hạn body trong curl command
                    body_str = json.dumps(body)[:500]
                    curl += f" -d '{body_str}'"
            except:
                pass
```

- [ ] **Step 2: Sửa thành except cụ thể + log debug**

Thay đoạn trên bằng:
```python
        if request.method in ['POST', 'PUT', 'PATCH']:
            try:
                if request.content_type == 'application/json' and request.body:
                    body = json.loads(request.body.decode('utf-8'))
                    # Giới hạn body trong curl command
                    body_str = json.dumps(body)[:500]
                    curl += f" -d '{body_str}'"
            except (json.JSONDecodeError, UnicodeDecodeError, AttributeError) as exc:
                logger.debug("audit middleware: skip body in curl (%s)", exc)
```

> Nếu `logger` chưa được khai báo ở file này: thêm ở đầu file `import logging` và `logger = logging.getLogger(__name__)`. Kiểm tra trước, có thể đã có.

- [ ] **Step 3: Chạy bộ test audit có sẵn**

```bash
cd backend && python manage.py test apps.audit -v 2
```
Expected: không tệ hơn baseline (nếu trước đó xanh, sau vẫn xanh).

- [ ] **Step 4: Commit**

```bash
git add backend/apps/audit/middleware_impl.py
git commit -m "fix(audit): replace bare except with specific exceptions + debug log"
```

---

## Task 6: Thu hẹp `except` rộng — KiotViet `EncryptedCharField`

**Files:**
- Modify: `backend/apps/kiotviet/models.py` (dòng 1–60: `EncryptedCharField`)

- [ ] **Step 1: Viết test cho hành vi decrypt với token hỏng**

Create: `backend/apps/kiotviet/tests/test_encrypted_field.py`

```python
import logging
from django.test import SimpleTestCase

from apps.kiotviet.models import EncryptedCharField


class EncryptedCharFieldTests(SimpleTestCase):
    def test_from_db_value_invalid_token_returns_raw_and_warns(self):
        field = EncryptedCharField(max_length=255)
        with self.assertLogs("apps.kiotviet.models", level="WARNING") as cm:
            result = field.from_db_value("not-an-encrypted-string", expression=None, connection=None)
        self.assertEqual(result, "not-an-encrypted-string")
        self.assertTrue(any("decrypt" in msg.lower() for msg in cm.output))
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd backend && python manage.py test apps.kiotviet.tests.test_encrypted_field -v 2
```
Expected: FAIL (chưa có log warning).

- [ ] **Step 3: Sửa `EncryptedCharField` — bắt exception cụ thể + log**

Thay đầu file (dòng 1–4) thêm import logger:
```python
import logging
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone
from .utils import get_encryption

logger = logging.getLogger(__name__)
```

Sửa 3 chỗ `except Exception` thành cụ thể:

`from_db_value` (dòng 20–25):
```python
        try:
            encryption = get_encryption()
            return encryption.decrypt(value)
        except (ValueError, TypeError) as exc:
            logger.warning("EncryptedCharField.from_db_value: decrypt failed (%s); returning raw", exc)
            return value
```

`to_python` (dòng 32–39):
```python
        if isinstance(value, str):
            try:
                encryption = get_encryption()
                return encryption.decrypt(value)
            except (ValueError, TypeError) as exc:
                logger.debug("EncryptedCharField.to_python: not encrypted (%s); using raw", exc)
                return value
```

`get_prep_value` (dòng 51–60): giữ ý đồ "thử decrypt để biết đã encrypt chưa", nhưng catch cụ thể:
```python
        try:
            encryption = get_encryption()
            encryption.decrypt(value)
            return value
        except (ValueError, TypeError):
            # plaintext -> encrypt
            return get_encryption().encrypt(value)
```

> Ghi chú: nếu thư viện encryption ném thêm exception riêng (vd `cryptography.fernet.InvalidToken`), thêm vào tuple. Trước khi chốt: kiểm tra `apps/kiotviet/utils.py` để biết lib gì.

- [ ] **Step 4: Chạy test, xác nhận PASS**

```bash
cd backend && python manage.py test apps.kiotviet.tests.test_encrypted_field -v 2
```
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/kiotviet/models.py backend/apps/kiotviet/tests/test_encrypted_field.py
git commit -m "fix(kiotviet): narrow EncryptedCharField except + warn on decrypt failure"
```

---

## Task 7: Fail-fast secret/password ở production

**Files:**
- Modify: `backend/config/settings_parts/base.py` (vùng 14–17 nơi đọc SECRET_KEY và DATABASES)
- Test: `backend/apps/users/tests/test_production_safety.py` (tạo mới)

- [ ] **Step 1: Viết test**

Create: `backend/apps/users/tests/test_production_safety.py`

```python
import importlib
import os
from unittest import mock

from django.test import SimpleTestCase


class ProductionSafetyTests(SimpleTestCase):
    def _reload_settings(self):
        # Reload settings module to pick up patched env vars.
        from django.conf import settings as _s  # noqa: F401
        import config.settings_parts.base as base_mod
        importlib.reload(base_mod)

    def test_default_secret_key_raises_in_production(self):
        env = {
            "APP_ENV": "production",
            "DJANGO_SECRET_KEY": "dev-secret-key-change-me",
            "POSTGRES_PASSWORD": "real-password",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(RuntimeError):
                self._reload_settings()

    def test_default_postgres_password_raises_in_production(self):
        env = {
            "APP_ENV": "production",
            "DJANGO_SECRET_KEY": "a" * 64,
            "POSTGRES_PASSWORD": "desi_order",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(RuntimeError):
                self._reload_settings()

    def test_non_production_allows_defaults(self):
        env = {
            "APP_ENV": "development",
            "DJANGO_SECRET_KEY": "dev-secret-key-change-me",
            "POSTGRES_PASSWORD": "desi_order",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            self._reload_settings()  # should not raise
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd backend && python manage.py test apps.users.tests.test_production_safety -v 2
```
Expected: FAIL.

- [ ] **Step 3: Thêm check fail-fast vào `base.py`**

Sau block `DATABASES` (sau dòng 100 hiện tại; sau khi đã thêm pooling từ Task 2), thêm:

```python
if IS_PRODUCTION:
    _default_secrets = {
        "DJANGO_SECRET_KEY": {"", "dev-secret-key-change-me"},
        "POSTGRES_PASSWORD": {"", "desi_order"},
    }
    for _env_key, _bad_values in _default_secrets.items():
        _val = os.getenv(_env_key, "")
        if _val in _bad_values:
            raise RuntimeError(
                f"{_env_key} must be set to a non-default value when APP_ENV=production"
            )
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

```bash
cd backend && python manage.py test apps.users.tests.test_production_safety -v 2
```
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add backend/config/settings_parts/base.py backend/apps/users/tests/test_production_safety.py
git commit -m "feat(config): fail fast when secret/db password is default in production"
```

---

## Task 8: Security headers production

**Files:**
- Modify: `backend/config/settings_parts/base.py`

- [ ] **Step 1: Viết test**

Append vào `backend/apps/users/tests/test_production_safety.py`:

```python
    def test_security_headers_set_in_production(self):
        env = {
            "APP_ENV": "production",
            "DJANGO_SECRET_KEY": "a" * 64,
            "POSTGRES_PASSWORD": "real-password",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            self._reload_settings()
            from django.conf import settings
            self.assertEqual(
                settings.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https")
            )
            self.assertTrue(settings.SESSION_COOKIE_SECURE)
            self.assertTrue(settings.CSRF_COOKIE_SECURE)
            self.assertGreaterEqual(settings.SECURE_HSTS_SECONDS, 60)
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd backend && python manage.py test apps.users.tests.test_production_safety -v 2
```
Expected: FAIL (settings chưa có).

- [ ] **Step 3: Thêm security headers ở `base.py` (chỉ khi production)**

Cuối file `base.py` (hoặc gần cuối, sau các check production hiện có):

```python
if IS_PRODUCTION:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False  # bật bằng tay khi đăng ký HSTS preload
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = os.getenv("DJANGO_SSL_REDIRECT", "0") == "1"
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

```bash
cd backend && python manage.py test apps.users.tests.test_production_safety -v 2
```
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add backend/config/settings_parts/base.py backend/apps/users/tests/test_production_safety.py
git commit -m "feat(config): set HSTS/cookie-secure/proxy-ssl headers in production"
```

---

## Task 9: Verify tổng thể

- [ ] **Step 1: Chạy bộ test các app đã đụng**

```bash
cd backend && python manage.py test apps.users apps.audit apps.kiotviet apps.orders -v 1 2>&1 | tail -40
```
Expected: Tất cả test mới của Spec 1 xanh. Các test pre-existing fail theo memory (customers aggregate, một số FE) không tính.

- [ ] **Step 2: Verify `pytest --collect-only` còn xanh**

```bash
cd backend && python -m pytest --collect-only -q 2>&1 | tail -5
```
Expected: collect được, không lỗi cấu hình.

- [ ] **Step 3: Sanity check không có thay đổi schema thừa**

```bash
cd backend && python manage.py makemigrations --check --dry-run
```
Expected: "No changes detected" (Spec 1 không thay đổi model).

- [ ] **Step 4: Push branch & in URL**

```bash
git branch --show-current   # phải là backend-perf-safety-improvements-20260528
git push -u origin HEAD 2>&1 | tail -5
```

---

## Self-Review (tự kiểm)

Spec 1 có 6 mục — đối chiếu task:
- (1) Connection pooling → Task 2 ✓
- (2a) N+1 export → Task 3 ✓
- (2b) N+1 product_count → Task 4 ✓
- (3a) audit bare except → Task 5 ✓
- (3b) KiotViet decrypt except → Task 6 ✓
- (4) Fail-fast secret → Task 7 ✓
- (5) Security headers → Task 8 ✓
- (6) Fix BOM pytest → Task 1 ✓
- Verify tổng thể → Task 9 ✓

Không có placeholder. Type/tên hàm thống nhất giữa các task.
