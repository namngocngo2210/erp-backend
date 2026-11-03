# Bảng giá theo nền tảng (Price Books) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cho phép khai giá sản phẩm khác nhau theo nền tảng (facebook, web, …) và để đơn của kênh KOC tính theo giá web thay vì giá facebook.

**Architecture:** Thêm entity `PriceBook` (bảng giá) và bảng con `ProductPackagePrice` treo vào `ProductPackage` đang có — gói và cân nặng/kích thước vẫn khai một lần, chỉ giá tách theo bảng giá. `Channel.price_book` quyết định bảng giá cho mọi đơn của kênh đó; thiếu giá thì fallback về bảng mặc định. Rollout 3 pha (expand → dual-write → contract) để không downtime.

**Tech Stack:** Django 5 + DRF + PostgreSQL (backend), React + TypeScript + shadcn/ui + react-query (frontend), pytest (backend test), vitest (frontend test).

**Spec:** `docs/superpowers/specs/2026-07-31-platform-price-books-design.md`

## Global Constraints

- Entity mới tên **`PriceBook`**, KHÔNG dùng tên `PriceList` — `price_list` đã bị chiếm trong import Excel/KOC với nghĩa "danh sách giá từng dòng SKU" (`backend/apps/orders/_import_excel_constants.py:72,99,144-151`).
- Code bảng giá seed: **`facebook`** (`is_default=True`, `sort_order=0`), **`web`** (`sort_order=1`).
- Tham số API là **`price_book=<code>`**, KHÔNG nhận id. Code sai/thiếu → dùng book mặc định, không raise (cùng triết lý `OrderSource.normalize` ở `backend/apps/orders/models.py:75-88`).
- RBAC: dùng lại section `products`, KHÔNG thêm section mới vào `backend/apps/users/rbac_catalog.py`.
- Gói (`quantity`) và `weight/length/width/height` **dùng chung mọi bảng giá**. Chỉ `price` khác nhau.
- KOC **không được hardcode** ở logic giá. Chỉ gán dữ liệu `Channel(code="koc").price_book = web`.
- Comment/docstring tiếng Việt, commit message tiếng Anh ngắn gọn, không kèm trailer `Co-Authored-By`.
- Backend phải sạch `ruff check .` theo `backend/pyproject.toml` (CI bắt buộc từ MR !1069).
- Task 13 (drop cột) CHỈ deploy sau khi Task 1-12 chạy production ổn định ≥ 1 tuần.

## Lệnh chạy test

**Backend** (`<db>` và `<redis-db>` phải khác nhau giữa các lần chạy song song):

```bash
cd <worktree> && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=test_pricebooks \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
```

Lint backend: `docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .`

**Frontend** — worktree không có `node_modules`, tạo junction một lần:

```powershell
New-Item -ItemType Junction -Path "<worktree>\frontend\node_modules" `
         -Target "C:\Users\MYPC\Desktop\workspace\desi-order\frontend\node_modules"
```

Rồi `npx vitest run <file>`, `npx tsc --noEmit`, `npm run lint:strict`.

**Bẫy đã biết:** `frontend/src/app/__tests__/RequireAuth.test.tsx` fail 2 test ngay trên master sạch. Thấy đỏ ở file đó thì bỏ qua, không phải do thay đổi của mình.

## File Structure

**Backend — tạo mới**

| File | Trách nhiệm |
|---|---|
| `backend/apps/orders/application/services/price_book_service.py` | Điểm chân lý duy nhất cho resolve bảng giá + tra giá đã fallback |
| `backend/apps/orders/transport/views/order_views_impl/price_book_views.py` | CRUD bảng giá |
| `backend/apps/orders/migrations/0112_price_books.py` | Pha 1 — expand + seed + backfill |
| `backend/apps/channels_core/migrations/0022_channel_price_book.py` | Cột `channels.price_book_id` |
| `backend/apps/orders/migrations/0113_drop_product_package_price.py` | Pha 3 — contract (Task 13) |
| `backend/apps/orders/management/commands/assign_koc_price_book.py` | Cutover: gán kênh KOC → book `web` |
| `backend/apps/orders/tests/test_price_book_service.py` | Test service resolve/fallback |
| `backend/apps/orders/tests/test_price_book_api.py` | Test CRUD + catalog + ETag |
| `backend/apps/orders/tests/test_price_book_order_pricing.py` | Test ingest/order dùng đúng book |

**Backend — sửa**

| File | Sửa gì |
|---|---|
| `backend/apps/orders/models.py:198-217` | Thêm `PriceBook`, `ProductPackagePrice` |
| `backend/apps/channels_core/models.py:6-50` | Thêm `Channel.price_book` |
| `.../serializers/order_serializers_impl/product_item_crm_serializers.py:42-190` | `prices` dict, dual-write |
| `.../views/order_views_impl/product_catalog_views.py:71-231,402-506` | `?price_book=`, ETag |
| `backend/apps/orders/urls.py:91-106` | Route `price-books` |
| `backend/apps/orders/application/services/order_service_helpers.py:352` | Fallback giá theo book |
| `backend/apps/orders/application/services/order_service_commands.py:57-60,228` | Ingest theo book |
| `backend/apps/channels_core/transport/serializers/channel_serializers_impl.py:246-283` | Field `price_book` |

**Frontend — sửa**

| File | Sửa gì |
|---|---|
| `frontend/src/lib/api/price-books.ts` *(tạo)* | Client CRUD bảng giá |
| `frontend/src/lib/api/products.ts:69-152` | Param `price_book` |
| `frontend/src/pages/ProductsPage.tsx` | Tab bảng giá trong form sản phẩm |
| `frontend/src/features/channels/ChannelManageDialog.tsx` | Select bảng giá |
| `frontend/src/features/orders/use-orders-catalogs.ts:128,346-385` | Cache theo `price_book` |
| `frontend/src/features/orders/order-form/use-order-form-products.ts` | Reset catalog khi đổi book |
| `frontend/src/features/orders/OrderFormDialog.tsx` | Reprice khi đổi kênh |
| `frontend/src/i18n/messages/products.ts` | Chuỗi tiếng Việt/Anh mới |

---

## SPRINT 1 — Nền tảng dữ liệu + service (pha 1 expand)

### Task 1: Model `PriceBook`, `ProductPackagePrice`, `Channel.price_book` + migration expand

**Files:**
- Modify: `backend/apps/orders/models.py` (sau `ProductPackage`, dòng 217)
- Modify: `backend/apps/channels_core/models.py` (trong `class Channel`, sau dòng 21)
- Create: `backend/apps/orders/migrations/0112_price_books.py`
- Create: `backend/apps/channels_core/migrations/0022_channel_price_book.py`
- Test: `backend/apps/orders/tests/test_price_book_service.py`

**Interfaces:**
- Produces: `PriceBook` (fields `code, name, is_default, sort_order, is_active, deleted_at`), `ProductPackagePrice` (`package, price_book, price`), `Channel.price_book` (nullable FK). Task 2-7 dùng các model này.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/orders/tests/test_price_book_service.py`:

```python
"""Test model PriceBook / ProductPackagePrice và service resolve bảng giá."""
from __future__ import annotations

from decimal import Decimal

from django.db.utils import IntegrityError
from django.test import TestCase

from apps.channels_core.models import Channel
from apps.orders.models import PriceBook, Product, ProductPackage, ProductPackagePrice


class PriceBookModelTests(TestCase):
    def setUp(self):
        # Migration đã seed facebook/web — lấy ra dùng, không tạo trùng code.
        self.facebook = PriceBook.objects.get(code="facebook")
        self.web = PriceBook.objects.get(code="web")
        self.product = Product.objects.create(name="SP giá", sku="SKU-PB-01", price=Decimal("100"))
        self.package = ProductPackage.objects.create(product=self.product, quantity=1)

    def test_seed_creates_facebook_as_default(self):
        self.assertTrue(self.facebook.is_default)
        self.assertFalse(self.web.is_default)

    def test_price_unique_per_package_and_book(self):
        ProductPackagePrice.objects.create(
            package=self.package, price_book=self.facebook, price=Decimal("100")
        )
        with self.assertRaises(IntegrityError):
            ProductPackagePrice.objects.create(
                package=self.package, price_book=self.facebook, price=Decimal("200")
            )

    def test_only_one_default_book_allowed(self):
        with self.assertRaises(IntegrityError):
            PriceBook.objects.create(code="tiktok", name="TikTok", is_default=True)

    def test_channel_price_book_is_nullable(self):
        channel = Channel.objects.create(code="pb-chan", name="Kênh test")
        self.assertIsNone(channel.price_book)
        channel.price_book = self.web
        channel.save(update_fields=["price_book"])
        channel.refresh_from_db()
        self.assertEqual(channel.price_book.code, "web")
```

- [ ] **Step 2: Chạy test để xác nhận đỏ**

Run: `... python -m pytest apps/orders/tests/test_price_book_service.py -q --create-db`
Expected: FAIL — `ImportError: cannot import name 'PriceBook'`

- [ ] **Step 3: Thêm model vào `backend/apps/orders/models.py`** (chèn ngay sau `class ProductPackage`, dòng 217)

```python
class PriceBook(models.Model):
    """Bảng giá theo nền tảng bán (facebook, web, ...).

    Đúng MỘT bảng giá được đánh ``is_default`` trong tập chưa xoá — bảo đảm bằng
    partial unique constraint bên dưới (cùng pattern ``ProductCategory.is_loyal_default``).
    Bảng mặc định là đích fallback khi sản phẩm chưa khai giá ở nền tảng đang chọn.
    """

    code = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    is_default = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "price_books"
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(deleted_at__isnull=True),
                name="pricebook_code_active_uniq",
            ),
            models.UniqueConstraint(
                fields=["is_default"],
                condition=models.Q(is_default=True, deleted_at__isnull=True),
                name="pricebook_one_default",
            ),
        ]
        indexes = [
            models.Index(fields=["deleted_at"], name="pricebook_deleted_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class ProductPackagePrice(models.Model):
    """Giá của một gói ở một bảng giá.

    Vắng dòng = nền tảng đó chưa khai giá → resolve fallback về bảng mặc định
    (xem ``price_book_service.package_prices``). Cân nặng/kích thước KHÔNG nằm ở
    đây: chúng dùng chung mọi nền tảng và vẫn thuộc ``ProductPackage``.
    """

    package = models.ForeignKey(ProductPackage, on_delete=models.CASCADE, related_name="book_prices")
    price_book = models.ForeignKey(PriceBook, on_delete=models.CASCADE, related_name="package_prices")
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_package_prices"
        constraints = [
            models.UniqueConstraint(fields=["package", "price_book"], name="uniq_package_price_book"),
        ]
        indexes = [
            models.Index(fields=["price_book", "package"], name="pkgprice_book_pkg_idx"),
        ]
```

- [ ] **Step 4: Thêm FK vào `backend/apps/channels_core/models.py`**

Chèn vào `class Channel` ngay trước `is_active = models.BooleanField(default=True)` (dòng 21):

```python
    price_book = models.ForeignKey(
        "orders.PriceBook",
        on_delete=models.SET_NULL,
        related_name="channels",
        null=True,
        blank=True,
        help_text="Bảng giá áp dụng cho đơn của kênh này. Rỗng = dùng bảng giá mặc định.",
    )
```

**Lưu ý layering:** `backend/.importlinter` quy định `channels_core` nằm DƯỚI `orders`. FK khai bằng **string reference** `"orders.PriceBook"` nên không sinh import Python — không vi phạm contract. Chạy `lint-imports` ở Step 8 để xác nhận.

- [ ] **Step 5: Viết migration `backend/apps/orders/migrations/0112_price_books.py`**

```python
"""Pha 1 (expand): tạo price_books + product_package_prices, seed facebook/web,
backfill giá hiện có vào bảng mặc định. Chưa đụng cột product_packages.price."""
from django.db import migrations, models
import django.db.models.deletion


def seed_and_backfill(apps, schema_editor):
    PriceBook = apps.get_model("orders", "PriceBook")
    ProductPackage = apps.get_model("orders", "ProductPackage")
    ProductPackagePrice = apps.get_model("orders", "ProductPackagePrice")

    facebook, _ = PriceBook.objects.get_or_create(
        code="facebook", defaults={"name": "Facebook", "is_default": True, "sort_order": 0}
    )
    PriceBook.objects.get_or_create(
        code="web", defaults={"name": "Web", "is_default": False, "sort_order": 1}
    )

    batch = []
    for pkg_id, price in ProductPackage.objects.values_list("id", "price").iterator(chunk_size=2000):
        batch.append(
            ProductPackagePrice(package_id=pkg_id, price_book_id=facebook.id, price=price)
        )
        if len(batch) >= 2000:
            ProductPackagePrice.objects.bulk_create(batch, ignore_conflicts=True)
            batch = []
    if batch:
        ProductPackagePrice.objects.bulk_create(batch, ignore_conflicts=True)


def unseed(apps, schema_editor):
    apps.get_model("orders", "ProductPackagePrice").objects.all().delete()
    apps.get_model("orders", "PriceBook").objects.filter(code__in=["facebook", "web"]).delete()


class Migration(migrations.Migration):
    dependencies = [("orders", "0111_order_restored_from")]

    operations = [
        migrations.CreateModel(
            name="PriceBook",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=50)),
                ("name", models.CharField(max_length=255)),
                ("is_default", models.BooleanField(default=False)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "price_books", "ordering": ["sort_order", "id"]},
        ),
        migrations.CreateModel(
            name="ProductPackagePrice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("price", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("package", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="book_prices", to="orders.productpackage")),
                ("price_book", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="package_prices", to="orders.pricebook")),
            ],
            options={"db_table": "product_package_prices"},
        ),
        migrations.AddIndex(
            model_name="pricebook",
            index=models.Index(fields=["deleted_at"], name="pricebook_deleted_idx"),
        ),
        migrations.AddConstraint(
            model_name="pricebook",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("code",),
                name="pricebook_code_active_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="pricebook",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_default", True), ("deleted_at__isnull", True)),
                fields=("is_default",),
                name="pricebook_one_default",
            ),
        ),
        migrations.AddIndex(
            model_name="productpackageprice",
            index=models.Index(fields=["price_book", "package"], name="pkgprice_book_pkg_idx"),
        ),
        migrations.AddConstraint(
            model_name="productpackageprice",
            constraint=models.UniqueConstraint(fields=("package", "price_book"), name="uniq_package_price_book"),
        ),
        migrations.RunPython(seed_and_backfill, unseed),
    ]
```

- [ ] **Step 6: Viết migration `backend/apps/channels_core/migrations/0022_channel_price_book.py`**

```python
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("channels_core", "0021_channel_ad_accounts"),
        ("orders", "0112_price_books"),
    ]

    operations = [
        migrations.AddField(
            model_name="channel",
            name="price_book",
            field=models.ForeignKey(
                blank=True,
                help_text="Bảng giá áp dụng cho đơn của kênh này. Rỗng = dùng bảng giá mặc định.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="channels",
                to="orders.pricebook",
            ),
        ),
    ]
```

- [ ] **Step 7: Chạy test để xác nhận xanh**

Run: `... python -m pytest apps/orders/tests/test_price_book_service.py -q --create-db`
Expected: PASS — 4 test.

- [ ] **Step 8: Kiểm tra không có migration lệch + không vi phạm layering + lint**

```bash
# trong container backend
python manage.py makemigrations --check --dry-run   # Expected: "No changes detected"
lint-imports                                        # Expected: contracts KEPT
```
Rồi lint: `docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .`

- [ ] **Step 9: Commit**

```bash
git add backend/apps/orders/models.py backend/apps/channels_core/models.py \
  backend/apps/orders/migrations/0112_price_books.py \
  backend/apps/channels_core/migrations/0022_channel_price_book.py \
  backend/apps/orders/tests/test_price_book_service.py
git commit -m "feat(orders): add price book models and expand migration"
```

---

### Task 2: `price_book_service` — resolve + tra giá đã fallback

**Files:**
- Create: `backend/apps/orders/application/services/price_book_service.py`
- Test: `backend/apps/orders/tests/test_price_book_service.py` (thêm class mới)

**Interfaces:**
- Consumes: `PriceBook`, `ProductPackagePrice`, `Channel.price_book` (Task 1).
- Produces:
  - `default_price_book() -> PriceBook | None`
  - `resolve_price_book(channel) -> PriceBook | None`
  - `resolve_price_book_by_code(code: str | None) -> PriceBook | None`
  - `package_prices(product_ids: Iterable[int], book: PriceBook | None) -> dict[tuple[int, int], Decimal]` — key `(product_id, quantity)`
  - `unit_price_for(product, book: PriceBook | None) -> Decimal`

  Task 4, 5, 6, 7 gọi các hàm này.

- [ ] **Step 1: Viết test thất bại** — thêm vào cuối `test_price_book_service.py`:

```python
class PriceBookServiceTests(TestCase):
    def setUp(self):
        from apps.orders.models import PriceBook

        self.facebook = PriceBook.objects.get(code="facebook")
        self.web = PriceBook.objects.get(code="web")
        self.product = Product.objects.create(name="SP", sku="SKU-SVC-01", price=Decimal("100"))
        self.pkg1 = ProductPackage.objects.create(product=self.product, quantity=1)
        self.pkg2 = ProductPackage.objects.create(product=self.product, quantity=2)
        ProductPackagePrice.objects.create(package=self.pkg1, price_book=self.facebook, price=Decimal("100"))
        ProductPackagePrice.objects.create(package=self.pkg2, price_book=self.facebook, price=Decimal("180"))
        # web CHỈ khai giá gói 1 → gói 2 phải fallback về facebook.
        ProductPackagePrice.objects.create(package=self.pkg1, price_book=self.web, price=Decimal("130"))

    def test_resolve_uses_channel_price_book(self):
        from apps.orders.application.services.price_book_service import resolve_price_book

        channel = Channel.objects.create(code="svc-web", name="Web", price_book=self.web)
        self.assertEqual(resolve_price_book(channel).code, "web")

    def test_resolve_falls_back_to_default_when_channel_has_none(self):
        from apps.orders.application.services.price_book_service import resolve_price_book

        channel = Channel.objects.create(code="svc-none", name="Không gán")
        self.assertEqual(resolve_price_book(channel).code, "facebook")

    def test_resolve_falls_back_when_book_soft_deleted(self):
        from django.utils import timezone

        from apps.orders.application.services.price_book_service import resolve_price_book

        channel = Channel.objects.create(code="svc-del", name="Book đã xoá", price_book=self.web)
        self.web.deleted_at = timezone.now()
        self.web.save(update_fields=["deleted_at"])
        self.assertEqual(resolve_price_book(channel).code, "facebook")

    def test_resolve_falls_back_when_book_inactive(self):
        from apps.orders.application.services.price_book_service import resolve_price_book

        channel = Channel.objects.create(code="svc-off", name="Book tắt", price_book=self.web)
        self.web.is_active = False
        self.web.save(update_fields=["is_active"])
        self.assertEqual(resolve_price_book(channel).code, "facebook")

    def test_resolve_by_code_is_lenient(self):
        from apps.orders.application.services.price_book_service import resolve_price_book_by_code

        self.assertEqual(resolve_price_book_by_code("web").code, "web")
        self.assertEqual(resolve_price_book_by_code("khong-ton-tai").code, "facebook")
        self.assertEqual(resolve_price_book_by_code(None).code, "facebook")
        self.assertEqual(resolve_price_book_by_code("  WEB  ").code, "web")

    def test_package_prices_fallback_per_tier(self):
        from apps.orders.application.services.price_book_service import package_prices

        prices = package_prices([self.product.id], self.web)
        self.assertEqual(prices[(self.product.id, 1)], Decimal("130"))  # giá web
        self.assertEqual(prices[(self.product.id, 2)], Decimal("180"))  # fallback facebook

    def test_unit_price_for_uses_qty1_of_book(self):
        from apps.orders.application.services.price_book_service import unit_price_for

        self.assertEqual(unit_price_for(self.product, self.web), Decimal("130"))
        self.assertEqual(unit_price_for(self.product, self.facebook), Decimal("100"))
```

- [ ] **Step 2: Chạy test để xác nhận đỏ**

Run: `... python -m pytest apps/orders/tests/test_price_book_service.py -q --create-db`
Expected: FAIL — `ModuleNotFoundError: apps.orders.application.services.price_book_service`

- [ ] **Step 3: Viết `price_book_service.py`**

```python
"""Điểm chân lý duy nhất cho việc chọn bảng giá và tra giá đã fallback.

Mọi nơi cần biết "đơn này tính theo giá nào" phải đi qua module này. Riêng KOC:
KHÔNG có nhánh if nào cho KOC ở đây — kênh KOC trỏ sang bảng giá `web` bằng dữ
liệu (xem management command ``assign_koc_price_book``).
"""
from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from apps.orders.models import PriceBook, ProductPackagePrice


def _active_books():
    return PriceBook.objects.filter(deleted_at__isnull=True, is_active=True)


def default_price_book() -> PriceBook | None:
    """Bảng giá mặc định (đích fallback). None khi DB chưa seed."""
    return _active_books().filter(is_default=True).first()


def resolve_price_book(channel) -> PriceBook | None:
    """Bảng giá áp dụng cho một kênh bán.

    Kênh chưa gán, hoặc gán vào bảng đã xoá/đã tắt → về bảng mặc định.
    """
    book_id = getattr(channel, "price_book_id", None)
    if book_id:
        book = _active_books().filter(id=book_id).first()
        if book is not None:
            return book
    return default_price_book()


def resolve_price_book_by_code(code: str | None) -> PriceBook | None:
    """Tra bảng giá theo code (lenient — code lạ/rỗng thì về mặc định, không raise)."""
    text = str(code or "").strip().lower()
    if text:
        book = _active_books().filter(code=text).first()
        if book is not None:
            return book
    return default_price_book()


def package_prices(
    product_ids: Iterable[int], book: PriceBook | None
) -> dict[tuple[int, int], Decimal]:
    """Giá từng gói của các sản phẩm, đã fallback về bảng mặc định.

    Trả về map ``(product_id, quantity) -> price``. Một query duy nhất cho cả hai
    bảng giá (đang chọn + mặc định), gộp bằng Python — giữ đúng đặc tính O(1) query
    của ``_serialize_products_fast``.
    """
    ids = [int(pid) for pid in product_ids]
    if not ids:
        return {}
    fallback = default_price_book()
    book_ids = {b.id for b in (book, fallback) if b is not None}
    if not book_ids:
        return {}

    rows = ProductPackagePrice.objects.filter(
        package__product_id__in=ids, price_book_id__in=book_ids
    ).values_list("package__product_id", "package__quantity", "price_book_id", "price")

    fallback_id = fallback.id if fallback is not None else None
    selected_id = book.id if book is not None else fallback_id
    result: dict[tuple[int, int], Decimal] = {}
    for product_id, quantity, price_book_id, price in rows:
        key = (product_id, quantity)
        # Giá của bảng đang chọn luôn thắng; giá bảng mặc định chỉ điền chỗ trống.
        if price_book_id == selected_id:
            result[key] = price
        elif key not in result and price_book_id == fallback_id:
            result[key] = price
    return result


def unit_price_for(product, book: PriceBook | None) -> Decimal:
    """Đơn giá 1 sản phẩm theo bảng giá — giá gói ``quantity=1`` đã fallback.

    Dùng cho các luồng ingest (landing/Lark/Excel) vốn chỉ nhân đơn giá với số
    lượng, không áp bậc gói.
    """
    prices = package_prices([product.id], book)
    price = prices.get((product.id, 1))
    if price is not None:
        return Decimal(price)
    return Decimal(product.price or 0)
```

- [ ] **Step 4: Chạy test để xác nhận xanh**

Run: `... python -m pytest apps/orders/tests/test_price_book_service.py -q --create-db`
Expected: PASS — 11 test.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/application/services/price_book_service.py \
  backend/apps/orders/tests/test_price_book_service.py
git commit -m "feat(orders): add price book resolution service"
```

---

## SPRINT 2 — API backend (pha 2 dual-write)

### Task 3: CRUD bảng giá

**Files:**
- Create: `backend/apps/orders/transport/views/order_views_impl/price_book_views.py`
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/product_item_crm_serializers.py` (thêm `PriceBookSerializer` trước `ProductPackageSerializer`, dòng 42)
- Modify: `backend/apps/orders/transport/views/order_views_impl/__init__.py`, `backend/apps/orders/views.py`, `backend/apps/orders/urls.py`
- Test: `backend/apps/orders/tests/test_price_book_api.py`

**Interfaces:**
- Consumes: `PriceBook` (Task 1).
- Produces: `PriceBookSerializer`; endpoints `list_create_price_books`, `update_delete_price_book` tại `/api/orders/price-books`. Task 8 (FE client) dùng contract này.

- [ ] **Step 1: Viết test thất bại** — tạo `backend/apps/orders/tests/test_price_book_api.py`:

```python
"""Test API CRUD bảng giá."""
from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient

from apps.orders.models import PriceBook
from apps.users.test_helpers import make_admin


class PriceBookApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(make_admin("pricebook-admin"))

    def test_list_returns_seeded_books_sorted(self):
        response = self.client.get("/api/orders/price-books")
        self.assertEqual(response.status_code, 200)
        codes = [row["code"] for row in response.json()]
        self.assertEqual(codes[:2], ["facebook", "web"])

    def test_create_price_book(self):
        response = self.client.post(
            "/api/orders/price-books", {"code": "tiktok", "name": "TikTok", "sort_order": 2}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.json()["is_default"])

    def test_setting_default_demotes_previous_default(self):
        web = PriceBook.objects.get(code="web")
        response = self.client.patch(
            f"/api/orders/price-books/{web.id}", {"is_default": True}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(PriceBook.objects.get(code="web").is_default)
        self.assertFalse(PriceBook.objects.get(code="facebook").is_default)

    def test_cannot_delete_default_book(self):
        facebook = PriceBook.objects.get(code="facebook")
        response = self.client.delete(f"/api/orders/price-books/{facebook.id}")
        self.assertEqual(response.status_code, 400)
        facebook.refresh_from_db()
        self.assertIsNone(facebook.deleted_at)

    def test_delete_non_default_soft_deletes(self):
        web = PriceBook.objects.get(code="web")
        response = self.client.delete(f"/api/orders/price-books/{web.id}")
        self.assertEqual(response.status_code, 204)
        web.refresh_from_db()
        self.assertIsNotNone(web.deleted_at)
```

- [ ] **Step 2: Chạy test để xác nhận đỏ**

Run: `... python -m pytest apps/orders/tests/test_price_book_api.py -q --create-db`
Expected: FAIL — 404 vì route chưa tồn tại.

- [ ] **Step 3: Thêm `PriceBookSerializer`** vào `product_item_crm_serializers.py` (ngay trước `class ProductPackageSerializer`, dòng 42); nhớ thêm `PriceBook` vào khối import `from apps.orders.models import (...)` dòng 22-39:

```python
class PriceBookSerializer(serializers.ModelSerializer):
    # Khai tường minh để DRF không tự gắn UniqueValidator từ partial unique constraint
    # (validator sẽ chặn trước khi logic hạ cờ ở update kịp chạy) — cùng lý do với
    # ProductCategorySerializer.is_loyal_default.
    is_default = serializers.BooleanField(required=False)

    class Meta:
        model = PriceBook
        fields = ("id", "code", "name", "is_default", "sort_order", "is_active", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_code(self, value: str) -> str:
        text = str(value or "").strip().lower()
        if not text:
            raise serializers.ValidationError("Mã bảng giá không được rỗng.")
        qs = PriceBook.objects.filter(code=text, deleted_at__isnull=True)
        if self.instance is not None:
            qs = qs.exclude(id=self.instance.id)
        if qs.exists():
            raise serializers.ValidationError(f"Mã bảng giá '{text}' đã tồn tại.")
        return text

    @staticmethod
    def _demote_other_defaults(exclude_id: int | None) -> None:
        qs = PriceBook.objects.filter(is_default=True, deleted_at__isnull=True)
        if exclude_id is not None:
            qs = qs.exclude(id=exclude_id)
        qs.update(is_default=False)

    @transaction.atomic
    def create(self, validated_data):
        if validated_data.get("is_default"):
            self._demote_other_defaults(exclude_id=None)
        return super().create(validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        if validated_data.get("is_default"):
            self._demote_other_defaults(exclude_id=instance.id)
        return super().update(instance, validated_data)
```

- [ ] **Step 4: Viết `price_book_views.py`**

```python
"""CRUD bảng giá theo nền tảng."""

from django.utils import timezone
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orders.models import PriceBook
from apps.orders.serializers import PriceBookSerializer
from apps.users.permissions import ORDER_SECTIONS, require_section


@extend_schema(
    request=PriceBookSerializer,
    responses={200: PriceBookSerializer(many=True), 201: PriceBookSerializer},
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_section("products", *ORDER_SECTIONS)])
@never_cache
def list_create_price_books(request):
    if request.method == "GET":
        books = PriceBook.objects.filter(deleted_at__isnull=True).order_by("sort_order", "id")
        return Response(PriceBookSerializer(books, many=True).data, status=status.HTTP_200_OK)

    serializer = PriceBookSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema(
    request=PriceBookSerializer,
    responses={
        200: PriceBookSerializer,
        204: OpenApiResponse(description="Đã xóa (soft-delete) bảng giá"),
        400: OpenApiResponse(description="Không thể xoá bảng giá mặc định"),
        404: OpenApiResponse(description="Không tìm thấy bảng giá"),
    },
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_section("products", *ORDER_SECTIONS)])
@never_cache
def update_delete_price_book(request, price_book_id: int):
    book = PriceBook.objects.filter(id=price_book_id, deleted_at__isnull=True).first()
    if not book:
        return Response({"detail": "Không tìm thấy bảng giá."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        if book.is_default:
            # Bảng mặc định là đích fallback của mọi bảng khác — xoá đi thì sản phẩm
            # chưa khai giá ở nền tảng nào cũng mất giá.
            return Response(
                {"detail": "Không thể xoá bảng giá mặc định. Hãy đặt bảng giá mặc định khác trước."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        book.deleted_at = timezone.now()
        book.is_active = False
        book.save(update_fields=["deleted_at", "is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = PriceBookSerializer(instance=book, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=status.HTTP_200_OK)
```

- [ ] **Step 5: Nối route** — thêm export vào `backend/apps/orders/transport/views/order_views_impl/__init__.py` (theo đúng pattern các view khác trong file), thêm tên vào `backend/apps/orders/views.py` (import + `__all__`), rồi thêm vào `backend/apps/orders/urls.py` ngay sau dòng 92:

```python
    path("price-books", list_create_price_books, name="orders-price-books"),
    path("price-books/<int:price_book_id>", update_delete_price_book, name="orders-price-book-detail"),
```

- [ ] **Step 6: Chạy test để xác nhận xanh**

Run: `... python -m pytest apps/orders/tests/test_price_book_api.py -q --create-db`
Expected: PASS — 5 test.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/orders/transport backend/apps/orders/views.py \
  backend/apps/orders/urls.py backend/apps/orders/tests/test_price_book_api.py
git commit -m "feat(orders): add price book CRUD endpoints"
```

---

### Task 4: `ProductSerializer` — đọc `prices` thô, ghi dual-write

**Files:**
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/product_item_crm_serializers.py:42-190`
- Test: `backend/apps/orders/tests/test_price_book_api.py` (thêm class)

**Interfaces:**
- Consumes: `PriceBook`, `ProductPackagePrice` (Task 1).
- Produces: `packages[].prices` — dict `{code: "decimal-string"}`, KHÔNG fallback. Ghi vào cả `ProductPackagePrice` lẫn `ProductPackage.price` (= giá bảng mặc định). Task 9 (ProductsPage) dùng contract này.

- [ ] **Step 1: Viết test thất bại** — thêm vào `test_price_book_api.py`:

```python
class ProductPricesSerializerTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(make_admin("product-prices-admin"))

    def _create(self, payload):
        return self.client.post("/api/orders/products", payload, format="json")

    def test_create_product_writes_prices_for_both_books(self):
        response = self._create(
            {
                "name": "SP đa nền tảng",
                "sku": "SKU-MP-01",
                "packages": [
                    {"quantity": 1, "prices": {"facebook": "100000", "web": "130000"}, "weight": 0.25},
                    {"quantity": 2, "prices": {"facebook": "180000"}},
                ],
            }
        )
        self.assertEqual(response.status_code, 201, response.content)
        packages = {pkg["quantity"]: pkg for pkg in response.json()["packages"]}
        self.assertEqual(packages[1]["prices"], {"facebook": "100000.00", "web": "130000.00"})
        # Gói 2 chưa khai giá web → KHÔNG fallback ở contract quản trị: vắng key.
        self.assertEqual(packages[2]["prices"], {"facebook": "180000.00"})

    def test_default_book_price_mirrors_into_legacy_columns(self):
        from apps.orders.models import Product, ProductPackage

        self._create(
            {
                "name": "SP mirror",
                "sku": "SKU-MP-02",
                "packages": [{"quantity": 1, "prices": {"facebook": "90000", "web": "120000"}}],
            }
        )
        product = Product.objects.get(sku="SKU-MP-02")
        # Product.price và ProductPackage.price là mirror của BẢNG MẶC ĐỊNH, không phải web.
        self.assertEqual(str(product.price), "90000.00")
        self.assertEqual(str(ProductPackage.objects.get(product=product, quantity=1).price), "90000.00")

    def test_price_required_only_for_default_book(self):
        response = self._create(
            {"name": "Thiếu giá mặc định", "sku": "SKU-MP-03",
             "packages": [{"quantity": 1, "prices": {"web": "120000"}}]}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("packages", response.json())

    def test_update_replaces_prices(self):
        created = self._create(
            {"name": "SP sửa", "sku": "SKU-MP-04",
             "packages": [{"quantity": 1, "prices": {"facebook": "100000", "web": "130000"}}]}
        ).json()
        response = self.client.patch(
            f"/api/orders/products/{created['id']}",
            {"packages": [{"quantity": 1, "prices": {"facebook": "110000"}}]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        # Bỏ giá web đi thì row giá web phải biến mất, không được sót lại.
        self.assertEqual(response.json()["packages"][0]["prices"], {"facebook": "110000.00"})
```

- [ ] **Step 2: Chạy test để xác nhận đỏ**

Run: `... python -m pytest apps/orders/tests/test_price_book_api.py::ProductPricesSerializerTests -q --create-db`
Expected: FAIL — `prices` chưa có trong payload.

- [ ] **Step 3: Sửa `ProductPackageSerializer` và `ProductSerializer`**

Thay `class ProductPackageSerializer` (dòng 42-54) bằng:

```python
class ProductPackageSerializer(serializers.ModelSerializer):
    # Map code bảng giá -> giá, dữ liệu THÔ (không fallback). Vắng key = nền tảng đó
    # chưa khai giá; FE hiển thị "kế thừa từ bảng mặc định". Nếu fallback ở đây thì
    # trang quản trị không phân biệt được "khai trùng giá" với "chưa khai".
    prices = serializers.DictField(child=serializers.CharField(), required=False)

    class Meta:
        model = ProductPackage
        fields = ("id", "quantity", "price", "prices", "weight", "length", "width", "height")
        read_only_fields = ("id", "price")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["prices"] = {
            row.price_book.code: str(row.price)
            for row in instance.book_prices.all()
            if row.price_book.deleted_at is None
        }
        return data
```

Trong `ProductSerializer`, thay ba method `_validate_packages`, `_sync_base_from_qty1`, `_replace_packages` (dòng 96-137) bằng:

```python
    @staticmethod
    def _default_book_code() -> str:
        from apps.orders.application.services.price_book_service import default_price_book

        book = default_price_book()
        return book.code if book else "facebook"

    def _validate_packages(self, packages):
        if not packages:
            return packages
        default_code = self._default_book_code()
        seen_qty: set[int] = set()
        for pkg in packages:
            qty = int(pkg.get("quantity") or 0)
            if qty <= 0:
                raise serializers.ValidationError({"packages": "Số lượng package phải lớn hơn 0."})
            if qty in seen_qty:
                raise serializers.ValidationError({"packages": f"Trùng số lượng {qty} trong danh sách package."})
            seen_qty.add(qty)
            # Chỉ bảng giá MẶC ĐỊNH bắt buộc có giá; nền tảng khác để trống = kế thừa.
            prices = pkg.get("prices") or {}
            if Decimal(str(prices.get(default_code) or 0)) <= 0:
                raise serializers.ValidationError(
                    {"packages": f"Gói số lượng {qty} thiếu giá ở bảng giá mặc định ({default_code})."}
                )
        if 1 not in seen_qty:
            raise serializers.ValidationError({"packages": "Bắt buộc có package quantity=1."})
        return packages

    def _sync_base_from_qty1(self, product: Product, packages: list[dict]) -> None:
        """Mirror gói qty=1 của BẢNG MẶC ĐỊNH về Product (cột đọc nhiều nơi: report,
        price range ở products_grouped, KiotViet)."""
        qty1 = next((p for p in packages if int(p.get("quantity") or 0) == 1), None)
        if not qty1:
            return
        default_code = self._default_book_code()
        prices = qty1.get("prices") or {}
        product.price = Decimal(str(prices.get(default_code) or product.price))
        product.weight = qty1.get("weight", product.weight)
        product.length = qty1.get("length", product.length)
        product.width = qty1.get("width", product.width)
        product.height = qty1.get("height", product.height)
        product.save(update_fields=["price", "weight", "length", "width", "height", "updated_at"])

    def _replace_packages(self, product: Product, packages: list[dict]) -> None:
        from apps.orders.models import PriceBook, ProductPackagePrice

        default_code = self._default_book_code()
        books_by_code = {
            book.code: book
            for book in PriceBook.objects.filter(deleted_at__isnull=True, is_active=True)
        }
        product.packages.all().delete()  # CASCADE xoá luôn book_prices cũ
        created = ProductPackage.objects.bulk_create(
            [
                ProductPackage(
                    product=product,
                    quantity=int(pkg["quantity"]),
                    # Cột legacy: mirror giá bảng mặc định. Bị drop ở migration pha 3.
                    price=Decimal(str((pkg.get("prices") or {}).get(default_code) or 0)),
                    weight=pkg.get("weight", 0),
                    length=pkg.get("length", 0),
                    width=pkg.get("width", 0),
                    height=pkg.get("height", 0),
                )
                for pkg in packages
            ]
        )
        price_rows = []
        for package, pkg in zip(created, packages, strict=True):
            for code, raw in (pkg.get("prices") or {}).items():
                book = books_by_code.get(str(code).strip().lower())
                if book is None:
                    continue  # code lạ → bỏ qua, không chặn lưu sản phẩm
                value = Decimal(str(raw or 0))
                if value <= 0 and book.code != default_code:
                    continue  # để trống ở nền tảng phụ = kế thừa, không lưu row 0
                price_rows.append(
                    ProductPackagePrice(package=package, price_book=book, price=value)
                )
        if price_rows:
            ProductPackagePrice.objects.bulk_create(price_rows)
```

Thêm `from decimal import Decimal` vào đầu file. Trong `create()` (dòng 151-172), thay nhánh seed gói mặc định khi `packages is None`:

```python
        else:
            # Seed gói qty=1 từ field base của Product để sản phẩm nào cũng có gói 1.
            self._replace_packages(
                product,
                [
                    {
                        "quantity": 1,
                        "prices": {self._default_book_code(): str(product.price)},
                        "weight": product.weight,
                        "length": product.length,
                        "width": product.width,
                        "height": product.height,
                    }
                ],
            )
```

- [ ] **Step 4: Sửa queryset prefetch để không N+1**

Trong `product_catalog_views.py:246-250` (`update_delete_product`), đổi prefetch:

```python
        .prefetch_related("packages__book_prices__price_book")
```

- [ ] **Step 5: Chạy test để xác nhận xanh**

Run: `... python -m pytest apps/orders/tests/test_price_book_api.py -q --create-db`
Expected: PASS — 9 test.

- [ ] **Step 6: Chạy hồi quy nhóm test sản phẩm/đơn**

Run: `... python -m pytest apps/orders/tests -q --create-db`
Expected: PASS toàn bộ. Test nào còn gửi `packages: [{"quantity": 1, "price": ...}]` sẽ đỏ — sửa sang `prices: {"facebook": ...}` cho khớp contract mới.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/orders/transport backend/apps/orders/tests/test_price_book_api.py
git commit -m "feat(orders): expose per-price-book prices on product serializer"
```

---

### Task 5: Catalog nhận `?price_book=` + ETag

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/product_catalog_views.py:71-231,402-506`
- Test: `backend/apps/orders/tests/test_price_book_api.py` (thêm class)

**Interfaces:**
- Consumes: `package_prices`, `resolve_price_book_by_code` (Task 2).
- Produces: `GET /api/orders/products?price_book=<code>` và `GET /api/orders/catalog/products?price_book=<code>` trả `packages[].price` là **scalar đã fallback**. Task 11 (order form) dùng contract này.

- [ ] **Step 1: Viết test thất bại** — thêm vào `test_price_book_api.py`:

```python
class ProductCatalogPriceBookTests(TestCase):
    def setUp(self):
        from decimal import Decimal

        from apps.orders.models import PriceBook, Product, ProductPackage, ProductPackagePrice

        self.client = APIClient()
        self.client.force_authenticate(make_admin("catalog-pb-admin"))
        facebook = PriceBook.objects.get(code="facebook")
        web = PriceBook.objects.get(code="web")
        self.product = Product.objects.create(name="SP catalog", sku="SKU-CAT-01", price=Decimal("100"))
        pkg1 = ProductPackage.objects.create(product=self.product, quantity=1)
        pkg2 = ProductPackage.objects.create(product=self.product, quantity=2)
        ProductPackagePrice.objects.create(package=pkg1, price_book=facebook, price=Decimal("100"))
        ProductPackagePrice.objects.create(package=pkg2, price_book=facebook, price=Decimal("180"))
        ProductPackagePrice.objects.create(package=pkg1, price_book=web, price=Decimal("130"))
        self.web_price_row = ProductPackagePrice.objects.get(package=pkg1, price_book=web)

    def _packages(self, price_book: str | None):
        url = "/api/orders/products?limit=100&offset=0"
        if price_book:
            url += f"&price_book={price_book}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.json() if item["sku"] == "SKU-CAT-01")
        return {pkg["quantity"]: pkg["price"] for pkg in row["packages"]}

    def test_default_book_used_when_param_missing(self):
        self.assertEqual(self._packages(None), {1: "100.00", 2: "180.00"})

    def test_web_book_prices_with_per_tier_fallback(self):
        self.assertEqual(self._packages("web"), {1: "130.00", 2: "180.00"})

    def test_unknown_code_falls_back_to_default(self):
        self.assertEqual(self._packages("khong-ton-tai"), {1: "100.00", 2: "180.00"})

    def test_etag_differs_between_books(self):
        fb = self.client.get("/api/orders/products?limit=100&offset=0&price_book=facebook")
        web = self.client.get("/api/orders/products?limit=100&offset=0&price_book=web")
        self.assertNotEqual(fb["ETag"], web["ETag"])

    def test_etag_changes_when_web_price_edited(self):
        from decimal import Decimal

        before = self.client.get("/api/orders/products?limit=100&offset=0&price_book=web")["ETag"]
        self.web_price_row.price = Decimal("140")
        self.web_price_row.save(update_fields=["price", "updated_at"])
        after = self.client.get("/api/orders/products?limit=100&offset=0&price_book=web")["ETag"]
        # Bỏ sót nhánh này = sale bán giá cũ sau khi đổi giá, không có log nào.
        self.assertNotEqual(before, after)

    def test_catalog_grouped_uses_price_book(self):
        response = self.client.get("/api/orders/catalog/products?limit=100&offset=0&price_book=web")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        entries = payload["ungrouped"] + [v for g in payload["groups"] for v in g["variants"]]
        entry = next(item for item in entries if item["sku"] == "SKU-CAT-01")
        self.assertEqual(entry["price"], "130.00")
```

- [ ] **Step 2: Chạy test để xác nhận đỏ**

Run: `... python -m pytest apps/orders/tests/test_price_book_api.py::ProductCatalogPriceBookTests -q --create-db`
Expected: FAIL — giá web vẫn trả về giá facebook.

- [ ] **Step 3: Sửa `_serialize_products_fast`** (dòng 71-130) — thêm tham số `book` và tra giá qua service:

```python
def _serialize_products_fast(product_queryset, book=None):
    """Serialize nhanh cho catalog (limit tới 10k).

    `price` của mỗi gói là giá ĐÃ resolve theo `book` (fallback bảng mặc định) —
    contract cho form đơn, khác với contract quản trị (`prices` dict thô).
    """
    from apps.orders.application.services.price_book_service import package_prices

    products = list(
        product_queryset.values(
            "id", "name", "sku", "price", "weight", "length", "width", "height",
            "is_active", "group_id", "group__code", "group__name", "group__deleted_at",
            "created_at", "updated_at",
        )
    )
    product_ids = [p["id"] for p in products]
    resolved = package_prices(product_ids, book)
    packages_by_product: dict[int, list[dict]] = {}
    if product_ids:
        package_rows = (
            ProductPackage.objects.filter(product_id__in=product_ids)
            .order_by("product_id", "quantity")
            .values("product_id", "id", "quantity", "weight", "length", "width", "height")
        )
        for pkg in package_rows:
            price = resolved.get((pkg["product_id"], pkg["quantity"]), Decimal("0"))
            packages_by_product.setdefault(pkg["product_id"], []).append(
                {
                    "id": pkg["id"],
                    "quantity": pkg["quantity"],
                    "price": str(price),
                    "weight": str(pkg["weight"]),
                    "length": str(pkg["length"]),
                    "width": str(pkg["width"]),
                    "height": str(pkg["height"]),
                }
            )
    base_prices = {pid: resolved.get((pid, 1)) for pid in product_ids}
    return [
        {
            "id": p["id"],
            "name": p["name"],
            "sku": p["sku"],
            # Giá base cũng theo bảng giá đang chọn — FE dùng nó khi sản phẩm không có
            # gói khớp số lượng (OrderFormDialog dòng 1013).
            "price": str(base_prices.get(p["id"]) if base_prices.get(p["id"]) is not None else p["price"]),
            "weight": str(p["weight"]),
            "length": str(p["length"]),
            "width": str(p["width"]),
            "height": str(p["height"]),
            "is_active": p["is_active"],
            "group": (
                {"id": p["group_id"], "code": p["group__code"], "name": p["group__name"]}
                if p["group_id"] and p["group__deleted_at"] is None
                else None
            ),
            "packages": packages_by_product.get(p["id"], []),
            "created_at": p["created_at"].isoformat() if p["created_at"] else None,
            "updated_at": p["updated_at"].isoformat() if p["updated_at"] else None,
        }
        for p in products
    ]
```

Thêm `from decimal import Decimal` vào đầu file.

- [ ] **Step 4: Sửa `_products_catalog_etag`** (dòng 138-169) — thêm tham số `price_book_code` và hai nguồn version mới:

```python
def _products_catalog_etag(
    search_query: str, offset: int, limit: int, group_id: int | None, price_book_code: str
) -> str:
    """Version tag rẻ cho catalog product đang active.

    PHẢI phản ánh cả bảng giá đang chọn lẫn thời điểm sửa giá của mọi bảng giá:
    thiếu hai phần đó, sửa giá web xong client vẫn nhận 304 và tiếp tục bán giá cũ.
    """
    product_agg = Product.objects.filter(is_active=True, deleted_at__isnull=True).aggregate(
        n=Count("id"), m=Max("updated_at")
    )
    package_m = ProductPackage.objects.filter(
        product__is_active=True, product__deleted_at__isnull=True
    ).aggregate(m=Max("updated_at"))["m"]
    price_agg = ProductPackagePrice.objects.filter(
        package__product__is_active=True, package__product__deleted_at__isnull=True
    ).aggregate(n=Count("id"), m=Max("updated_at"))
    book_agg = PriceBook.objects.aggregate(n=Count("id"), m=Max("updated_at"))
    group_agg = ProductGroup.objects.aggregate(n=Count("id"), m=Max("updated_at"))
    parts = [
        str(product_agg["n"] or 0),
        product_agg["m"].isoformat() if product_agg["m"] else "0",
        package_m.isoformat() if package_m else "0",
        str(price_agg["n"] or 0),
        price_agg["m"].isoformat() if price_agg["m"] else "0",
        str(book_agg["n"] or 0),
        book_agg["m"].isoformat() if book_agg["m"] else "0",
        str(group_agg["n"] or 0),
        group_agg["m"].isoformat() if group_agg["m"] else "0",
        search_query,
        str(offset),
        str(limit),
        str(group_id) if group_id is not None else "",
        price_book_code,
    ]
    digest = hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()
    return f'"{digest}"'
```

Thêm `PriceBook`, `ProductPackagePrice` vào khối import model dòng 25-30.

- [ ] **Step 5: Nối tham số vào `list_create_products`** — trong nhánh GET (dòng 184-224), sau khi parse `group_filter_id`:

```python
        from apps.orders.application.services.price_book_service import resolve_price_book_by_code

        book = resolve_price_book_by_code(request.query_params.get("price_book"))
        book_code = book.code if book else ""

        etag = _products_catalog_etag(search_query, offset, limit, group_filter_id, book_code)
```

và đổi dòng `data = _serialize_products_fast(queryset)` thành `data = _serialize_products_fast(queryset, book)`.

- [ ] **Step 6: Nối tham số vào `products_catalog_grouped`** (dòng 462-506) — thay khối build `active_products`/`entry`:

```python
    from apps.orders.application.services.price_book_service import resolve_price_book_by_code

    book = resolve_price_book_by_code(request.query_params.get("price_book"))
    active_products = list(
        Product.objects.filter(is_active=True, deleted_at__isnull=True)
        .order_by("name", "id")
        .values("id", "name", "sku", "price", "group_id")
    )
    from apps.orders.application.services.price_book_service import package_prices

    resolved = package_prices([p["id"] for p in active_products], book)
```

và trong vòng lặp, đổi dòng dựng `entry`:

```python
        base_price = resolved.get((p["id"], 1))
        entry = {
            "id": p["id"],
            "name": p["name"],
            "sku": p["sku"],
            "price": str(base_price if base_price is not None else p["price"]),
        }
```

- [ ] **Step 6b: Thêm `?include_prices=1` cho màn hình quản trị**

Trang Sản phẩm lấy dữ liệu qua `products_grouped` và `list_create_products?group=` — cả hai hydrate bằng `_serialize_products_fast`. Không có `prices` dict ở đó thì dialog sửa sản phẩm (Task 9) mở ra với mọi tab giá trống. Nhưng catalog form đơn tải tới 10k sản phẩm, không nên gánh thêm dict giá thô.

Thêm tham số `include_prices` (mặc định tắt) vào `_serialize_products_fast`:

```python
def _serialize_products_fast(product_queryset, book=None, include_prices=False):
```

Khi bật, thêm một query lấy giá THÔ (không fallback) và gắn vào từng gói:

```python
    raw_prices: dict[int, dict[str, str]] = {}
    if include_prices and product_ids:
        raw_rows = ProductPackagePrice.objects.filter(
            package__product_id__in=product_ids, price_book__deleted_at__isnull=True
        ).values_list("package_id", "price_book__code", "price")
        for package_id, code, price in raw_rows:
            raw_prices.setdefault(package_id, {})[code] = str(price)
```

và trong vòng dựng package dict:

```python
                    **({"prices": raw_prices.get(pkg["id"], {})} if include_prices else {}),
```

Nối vào view: `list_create_products` đọc `include_prices = request.query_params.get("include_prices") == "1"`, cộng cờ này vào `parts` của `_products_catalog_etag`, và truyền xuống `_serialize_products_fast`. `products_grouped` luôn gọi với `include_prices=True` (màn hình quản trị, vài trăm dòng mỗi trang).

Thêm test vào `ProductCatalogPriceBookTests`:

```python
    def test_include_prices_returns_raw_dict_without_fallback(self):
        response = self.client.get(
            "/api/orders/products?limit=100&offset=0&price_book=web&include_prices=1"
        )
        row = next(item for item in response.json() if item["sku"] == "SKU-CAT-01")
        packages = {pkg["quantity"]: pkg for pkg in row["packages"]}
        self.assertEqual(packages[1]["prices"], {"facebook": "100.00", "web": "130.00"})
        # Gói 2 chưa khai giá web → prices thô KHÔNG có key web (dù price đã fallback).
        self.assertEqual(packages[2]["prices"], {"facebook": "180.00"})
        self.assertEqual(packages[2]["price"], "180.00")

    def test_prices_absent_without_include_flag(self):
        response = self.client.get("/api/orders/products?limit=100&offset=0")
        row = next(item for item in response.json() if item["sku"] == "SKU-CAT-01")
        self.assertNotIn("prices", row["packages"][0])
```

- [ ] **Step 7: `products_grouped` giữ nguyên phần price range**

Trang quản trị dùng `Min/Max(Product.price)` cho price range của header nhóm — `Product.price` là mirror bảng mặc định, đúng ý nghĩa "khoảng giá gốc". Không truyền `book` vào đây. Ghi comment một dòng ngay trên `group_rows` (dòng 582) để người sau không tưởng là bỏ sót:

```python
    # Price range của header nhóm luôn theo BẢNG GIÁ MẶC ĐỊNH (Product.price là mirror
    # của nó) — đây là màn hình quản trị, không phải màn hình bán hàng.
```

- [ ] **Step 8: Chạy test để xác nhận xanh**

Run: `... python -m pytest apps/orders/tests/test_price_book_api.py -q --create-db`
Expected: PASS — 15 test.

- [ ] **Step 9: Chạy test đếm query để chắc không N+1**

Run: `... python -m pytest apps/orders/tests/test_orders_list_query_count.py -q --create-db`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add backend/apps/orders/transport backend/apps/orders/tests/test_price_book_api.py
git commit -m "feat(orders): resolve catalog prices by price book"
```

---

### Task 6: `ChannelSerializer` phơi `price_book`

**Files:**
- Modify: `backend/apps/channels_core/transport/serializers/channel_serializers_impl.py:246-283`
- Test: `backend/apps/channels_core/tests/test_channel_price_book_api.py` (tạo mới)

**Interfaces:**
- Consumes: `Channel.price_book` (Task 1).
- Produces: `ChannelSerializer` có `price_book` (read, object `{id, code, name}`) và `price_book_id` (write, nullable). Task 10 (ChannelManageDialog) và Task 11 (order form) dùng.

- [ ] **Step 1: Viết test thất bại**

```python
"""Test field price_book trên API kênh."""
from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.orders.models import PriceBook
from apps.users.test_helpers import make_admin


class ChannelPriceBookApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(make_admin("channel-pb-admin"))
        self.channel = Channel.objects.create(code="pb-api", name="Kênh PB")
        self.web = PriceBook.objects.get(code="web")

    def test_channel_list_exposes_null_price_book(self):
        response = self.client.get("/api/channels")
        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.json() if item["code"] == "pb-api")
        self.assertIsNone(row["price_book"])

    def test_assign_price_book(self):
        response = self.client.patch(
            f"/api/channels/{self.channel.id}", {"price_book_id": self.web.id}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["price_book"]["code"], "web")

    def test_clear_price_book(self):
        self.channel.price_book = self.web
        self.channel.save(update_fields=["price_book"])
        response = self.client.patch(
            f"/api/channels/{self.channel.id}", {"price_book_id": None}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["price_book"])
```

**Lưu ý:** kiểm tra đường dẫn thật của endpoint kênh trong `backend/apps/channels_core/urls.py` trước khi chạy; nếu khác `/api/channels` thì sửa lại trong test.

- [ ] **Step 2: Chạy test để xác nhận đỏ**

Run: `... python -m pytest apps/channels_core/tests/test_channel_price_book_api.py -q --create-db`
Expected: FAIL — `KeyError: 'price_book'`

- [ ] **Step 3: Sửa `ChannelSerializer`**

Thêm ngay sau khai báo `google_ads_accounts` (dòng 264):

```python
    price_book = serializers.SerializerMethodField()
    price_book_id = serializers.PrimaryKeyRelatedField(
        queryset=_price_book_model().objects.filter(deleted_at__isnull=True),
        source="price_book",
        write_only=True,
        required=False,
        allow_null=True,
    )
```

Thêm `"price_book"`, `"price_book_id"` vào `Meta.fields` (dòng 268-283), và method:

```python
    @extend_schema_field(
        inline_serializer(
            "ChannelPriceBookRef",
            fields={
                "id": serializers.IntegerField(),
                "code": serializers.CharField(),
                "name": serializers.CharField(),
            },
        )
    )
    def get_price_book(self, obj: Channel) -> dict | None:
        book = getattr(obj, "price_book", None)
        if book is None or book.deleted_at is not None:
            return None
        return {"id": book.id, "code": book.code, "name": book.name}
```

`get_price_book` dùng `extend_schema_field` và `inline_serializer` — kiểm tra đầu file đã import chưa, chưa thì thêm:

```python
from drf_spectacular.utils import extend_schema_field, inline_serializer
```

Thêm helper ở đầu module (cùng pattern `_kiotviet_channel_model()` đã có trong file) để không import `apps.orders` ở top-level — `channels_core` nằm dưới `orders` trong `backend/.importlinter`:

```python
def _price_book_model():
    from apps.orders.models import PriceBook

    return PriceBook
```

- [ ] **Step 4: Thêm `select_related` để không N+1**

Trong repository/view liệt kê kênh (tìm bằng `grep -rn "ChannelSerializer(" backend/apps/channels_core`), thêm `.select_related("price_book")` vào queryset danh sách.

- [ ] **Step 5: Chạy test để xác nhận xanh**

Run: `... python -m pytest apps/channels_core -q --create-db`
Expected: PASS.

- [ ] **Step 6: Kiểm tra layering + lint**

```bash
lint-imports    # Expected: contracts KEPT
```
Rồi ruff như Task 1 Step 8.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/channels_core
git commit -m "feat(channels): expose price book on channel serializer"
```

---

### Task 7: Luồng tạo đơn / ingest dùng đúng bảng giá

**Files:**
- Modify: `backend/apps/orders/application/services/order_service_helpers.py:352`
- Modify: `backend/apps/orders/application/services/order_service_commands.py:57-60,228`
- Test: `backend/apps/orders/tests/test_price_book_order_pricing.py` (tạo mới)

**Interfaces:**
- Consumes: `resolve_price_book`, `unit_price_for` (Task 2).
- Produces: không có API mới. `OrderItem.unit_price` khi payload không gửi giá = đơn giá theo bảng giá của `order.channel`.

- [ ] **Step 1: Viết test thất bại**

```python
"""Đơn của kênh gán bảng giá web phải lấy giá web, không phải giá facebook."""
from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import (
    Order,
    OrderItem,
    PriceBook,
    Product,
    ProductPackage,
    ProductPackagePrice,
)
from apps.orders.application.services.order_service_helpers import OrderServiceHelpers


class OrderPricingByPriceBookTests(TestCase):
    def setUp(self):
        facebook = PriceBook.objects.get(code="facebook")
        self.web = PriceBook.objects.get(code="web")
        self.product = Product.objects.create(name="SP đơn", sku="SKU-ORD-01", price=Decimal("100"))
        pkg1 = ProductPackage.objects.create(product=self.product, quantity=1)
        ProductPackagePrice.objects.create(package=pkg1, price_book=facebook, price=Decimal("100"))
        ProductPackagePrice.objects.create(package=pkg1, price_book=self.web, price=Decimal("130"))
        self.customer = Customer.objects.create(
            full_name="KH", phone_raw="0900000001", phone_normalized="0900000001", code="KH-PB-01"
        )

    def _order_for(self, channel_code: str, price_book):
        channel = Channel.objects.create(code=channel_code, name=channel_code, price_book=price_book)
        return Order.objects.create(channel=channel, customer=self.customer)

    def test_koc_channel_uses_web_price(self):
        order = self._order_for("koc", self.web)
        OrderServiceHelpers._replace_order_items(order, [{"sku": "SKU-ORD-01", "quantity": 2}])
        item = OrderItem.objects.get(order=order)
        self.assertEqual(item.unit_price, Decimal("130.00"))
        self.assertEqual(item.line_total, Decimal("260.00"))

    def test_facebook_channel_uses_default_price(self):
        order = self._order_for("sale-fb", None)
        OrderServiceHelpers._replace_order_items(order, [{"sku": "SKU-ORD-01", "quantity": 1}])
        self.assertEqual(OrderItem.objects.get(order=order).unit_price, Decimal("100.00"))

    def test_explicit_unit_price_still_wins(self):
        order = self._order_for("koc-manual", self.web)
        OrderServiceHelpers._replace_order_items(
            order, [{"sku": "SKU-ORD-01", "quantity": 1, "unit_price": "99000"}]
        )
        self.assertEqual(OrderItem.objects.get(order=order).unit_price, Decimal("99000"))
```

- [ ] **Step 2: Chạy test để xác nhận đỏ**

Run: `... python -m pytest apps/orders/tests/test_price_book_order_pricing.py -q --create-db`
Expected: FAIL ở `test_koc_channel_uses_web_price` — nhận `100.00`.

- [ ] **Step 3: Sửa `_replace_order_items`** (`order_service_helpers.py:334-372`)

Thêm ngay sau `grand_total = Decimal("0")` (dòng 341):

```python
        from apps.orders.application.services.price_book_service import resolve_price_book

        # Bảng giá của kênh quyết định đơn giá mặc định. Payload gửi unit_price
        # tường minh thì vẫn thắng (sale sửa tay / import có cột giá).
        book = resolve_price_book(order.channel)
```

và thay dòng 352:

```python
            unit_price = OrderServiceHelpers._decimal(
                item_payload.get("unit_price"),
                fallback=unit_price_for(product, book),
            )
```

với `unit_price_for` import cùng dòng `resolve_price_book`.

- [ ] **Step 4: Sửa ingest landing** (`order_service_commands.py`)

Ở dòng 228, thay `unit_price = OrderServiceCommands._decimal(product.price)` bằng lời gọi `unit_price_for(product, book)` — trong đó `book` lấy từ channel của đơn đang dựng ngay trên đó (đọc biến channel trong scope, thường tên `channel`):

```python
                from apps.orders.application.services.price_book_service import (
                    resolve_price_book,
                    unit_price_for,
                )

                unit_price = unit_price_for(product, resolve_price_book(channel))
```

Ở dòng 57-60, thay công thức `items_total`:

```python
            book = resolve_price_book(channel)
            items_total = sum(
                (unit_price_for(p, book) * qty for p, qty in matched_items),
                Decimal("0"),
            )
```

**Nếu biến channel chưa có trong scope tại dòng 57**, di chuyển khối tính `items_total` xuống sau khi channel được resolve, hoặc truyền channel vào hàm. Đọc kỹ hàm bao quanh trước khi sửa — không đoán tên biến.

- [ ] **Step 5: Chạy test để xác nhận xanh**

Run: `... python -m pytest apps/orders/tests/test_price_book_order_pricing.py -q --create-db`
Expected: PASS — 3 test.

- [ ] **Step 6: Chạy hồi quy toàn bộ backend**

Run: `... python -m pytest -q --create-db 2>&1 | tee /tmp/pytest-full.log`
Suite đầy đủ mất ~19 phút. Chạy nền và redirect ra file — shell nền bị kill nhưng container docker vẫn chạy tiếp.
Expected: PASS toàn bộ.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/orders/application backend/apps/orders/tests/test_price_book_order_pricing.py
git commit -m "feat(orders): price order items by channel price book"
```

---

## SPRINT 3 — Frontend

### Task 8: API client + types

**Files:**
- Create: `frontend/src/lib/api/price-books.ts`
- Modify: `frontend/src/lib/api/products.ts:69-137`
- Modify: `frontend/src/lib/api/channels.ts`
- Modify: `frontend/src/features/orders/order-types.ts`

**Interfaces:**
- Consumes: endpoints Task 3, 4, 5, 6.
- Produces: `PriceBook` type, `getPriceBooks/createPriceBook/updatePriceBook/deletePriceBook`, `GetProductsParams.price_book`, `ProductPackage.prices`. Task 9-11 dùng.

- [ ] **Step 1: Cập nhật OpenAPI types**

`frontend/src/api/schema.d.ts` được sinh từ `backend/schema.yaml`. Chạy lại generator theo cách repo đang dùng (tìm script trong `frontend/package.json`, thường `npm run gen:api`). Nếu không có script, sửa tay `schema.d.ts` cho khớp contract mới.

- [ ] **Step 2: Viết `frontend/src/lib/api/price-books.ts`**

```typescript
import { fetchJson } from "./client";

export type PriceBook = {
  id: number;
  code: string;
  name: string;
  is_default: boolean;
  sort_order: number;
  is_active: boolean;
};

export type PriceBookInput = {
  code: string;
  name: string;
  sort_order?: number;
  is_default?: boolean;
  is_active?: boolean;
};

export async function getPriceBooks(): Promise<PriceBook[]> {
  return fetchJson<PriceBook[]>("/api/orders/price-books");
}

export async function createPriceBook(data: PriceBookInput): Promise<PriceBook> {
  return fetchJson<PriceBook>("/api/orders/price-books", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updatePriceBook(id: number, data: Partial<PriceBookInput>): Promise<PriceBook> {
  return fetchJson<PriceBook>(`/api/orders/price-books/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function deletePriceBook(id: number): Promise<void> {
  await fetchJson<void>(`/api/orders/price-books/${id}`, { method: "DELETE" });
}
```

**Quan trọng:** đọc `frontend/src/lib/api/products.ts:1-40` trước để dùng ĐÚNG helper fetch và cách import mà repo đang dùng (tên hàm, đường dẫn, cách set header). Không copy nguyên mẫu trên nếu repo dùng helper khác.

- [ ] **Step 3: Thêm `price_book` và `include_prices` vào `getProducts`**

Trong `frontend/src/lib/api/products.ts`, thêm `price_book?: string` và `include_prices?: boolean` vào `GetProductsParams`, append vào query string cùng chỗ với `q`/`group` (`include_prices` gửi dạng `"1"` khi true).

Trang Sản phẩm (Task 9) nạp biến thể theo nhóm bằng `getProducts({ group })` — chỗ đó PHẢI truyền `include_prices: true`, nếu không dialog sửa sản phẩm mở ra với mọi tab giá trống.

- [ ] **Step 4: Thêm `prices` vào type package + `price_book` vào channel**

Trong type `ProductPackage` (products.ts) thêm `prices?: Record<string, string>`.
Trong type channel (`frontend/src/lib/api/channels.ts`) thêm:

```typescript
  price_book?: { id: number; code: string; name: string } | null;
```

Trong `frontend/src/features/orders/order-types.ts`, thêm `price_book` vào type kênh dùng ở form đơn (tìm bằng `grep -n "ChannelOption\|channels:" frontend/src/features/orders/order-types.ts frontend/src/features/orders/order-form/order-form-helpers.ts`).

- [ ] **Step 5: Kiểm tra type**

Run: `cd frontend && npx tsc --noEmit`
Expected: không lỗi.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/api frontend/src/features/orders/order-types.ts frontend/src/api/schema.d.ts
git commit -m "feat(frontend): add price book api client and types"
```

---

### Task 9: Tab bảng giá trong form sản phẩm

**Files:**
- Modify: `frontend/src/pages/ProductsPage.tsx:60-113,143-155,276-364,367-410,482-495,1151-1306`
- Modify: `frontend/src/i18n/messages/products.ts`
- Test: `frontend/src/pages/__tests__/ProductsPage.prices.test.tsx` (tạo mới)

**Interfaces:**
- Consumes: `getPriceBooks`, `ProductPackage.prices` (Task 8).
- Produces: payload `packages[].prices` gửi lên API (Task 4).

- [ ] **Step 1: Viết test thất bại**

```typescript
import { describe, expect, it } from "vitest";

import { packageFormToPayload, sortedPackageForms } from "@/pages/products-package-form";

describe("packageFormToPayload", () => {
  it("chỉ gửi giá của bảng giá đã nhập, bỏ ô trống", () => {
    const payload = packageFormToPayload({
      quantity: 1,
      prices: { facebook: 100000, web: "" },
      weight: 0.25,
      length: "",
      width: "",
      height: "",
    });
    expect(payload.prices).toEqual({ facebook: 100000 });
  });

  it("giữ nguyên nhiều bảng giá khi đều có số", () => {
    const payload = packageFormToPayload({
      quantity: 2,
      prices: { facebook: 180000, web: 220000 },
      weight: "",
      length: "",
      width: "",
      height: "",
    });
    expect(payload.prices).toEqual({ facebook: 180000, web: 220000 });
  });
});

describe("sortedPackageForms", () => {
  it("đọc prices từ API và sắp theo số lượng tăng dần", () => {
    const forms = sortedPackageForms([
      { id: 2, quantity: 2, prices: { facebook: "180000" }, weight: "0", length: "0", width: "0", height: "0" },
      { id: 1, quantity: 1, prices: { facebook: "100000", web: "130000" }, weight: "0.25", length: "0", width: "0", height: "0" },
    ]);
    expect(forms.map((f) => f.quantity)).toEqual([1, 2]);
    expect(forms[0].prices).toEqual({ facebook: 100000, web: 130000 });
    expect(forms[1].prices).toEqual({ facebook: 180000 });
  });

  it("tự chèn gói số lượng 1 khi API không trả", () => {
    const forms = sortedPackageForms([
      { id: 5, quantity: 3, prices: { facebook: "250000" }, weight: "0", length: "0", width: "0", height: "0" },
    ]);
    expect(forms[0].quantity).toBe(1);
    expect(forms[0].prices).toEqual({});
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận đỏ**

Run: `cd frontend && npx vitest run src/pages/__tests__/ProductsPage.prices.test.tsx`
Expected: FAIL — module `@/pages/products-package-form` không tồn tại.

- [ ] **Step 3: Tách logic package form ra module riêng**

`ProductsPage.tsx` đang 1308 dòng và sắp phình thêm. Tách phần thuần logic (không JSX) ra `frontend/src/pages/products-package-form.ts`, chuyển từ `ProductsPage.tsx:60-113` sang, sửa `price` thành `prices`:

```typescript
import type { ProductPackage } from "@/lib/api/products";

export type PackageFormNumField = "quantity" | "weight" | "length" | "width" | "height";

export type PackageForm = {
  id?: number;
  quantity: number | "";
  /** Giá theo code bảng giá. Ô để trống = kế thừa bảng giá mặc định. */
  prices: Record<string, number | "">;
  weight: number | "";
  length: number | "";
  width: number | "";
  height: number | "";
};

export const EMPTY_PACKAGE = (quantity: number | "" = ""): PackageForm => ({
  quantity,
  prices: {},
  weight: "",
  length: "",
  width: "",
  height: "",
});

export const numOrEmptyFromUnknown = (v: number | string | undefined | null): number | "" => {
  if (v === undefined || v === null || v === "") return "";
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n) || n === 0) return "";
  return n;
};

export const packageFormToPayload = (p: PackageForm): ProductPackage => {
  const prices: Record<string, number> = {};
  for (const [code, value] of Object.entries(p.prices)) {
    if (value === "" || value === null || value === undefined) continue;
    const n = Number(value);
    if (Number.isFinite(n) && n > 0) prices[code] = n;
  }
  return {
    ...(p.id ? { id: p.id } : {}),
    quantity: p.quantity === "" ? 0 : Number(p.quantity),
    prices,
    weight: p.weight === "" ? 0 : Number(p.weight),
    length: p.length === "" ? 0 : Number(p.length),
    width: p.width === "" ? 0 : Number(p.width),
    height: p.height === "" ? 0 : Number(p.height),
  } as ProductPackage;
};

export const sortedPackageForms = (packages: ProductPackage[] | undefined): PackageForm[] => {
  const list = Array.isArray(packages) ? packages : [];
  const mapped = list.map<PackageForm>((pkg) => {
    const prices: Record<string, number | ""> = {};
    for (const [code, value] of Object.entries(pkg.prices ?? {})) {
      prices[code] = numOrEmptyFromUnknown(value as string);
    }
    return {
      id: pkg.id,
      quantity: numOrEmptyFromUnknown(pkg.quantity) || (pkg.quantity ? Number(pkg.quantity) : ""),
      prices,
      weight: numOrEmptyFromUnknown(pkg.weight),
      length: numOrEmptyFromUnknown(pkg.length),
      width: numOrEmptyFromUnknown(pkg.width),
      height: numOrEmptyFromUnknown(pkg.height),
    };
  });
  if (!mapped.some((p) => Number(p.quantity) === 1)) {
    mapped.unshift(EMPTY_PACKAGE(1));
  }
  return mapped.sort((a, b) => Number(a.quantity || 0) - Number(b.quantity || 0));
};
```

Trong `ProductsPage.tsx`, xoá các định nghĩa cũ (dòng 60-113) và import từ module mới.

- [ ] **Step 4: Chạy test để xác nhận xanh**

Run: `cd frontend && npx vitest run src/pages/__tests__/ProductsPage.prices.test.tsx`
Expected: PASS — 4 test.

- [ ] **Step 5: Nạp danh sách bảng giá + state tab**

Trong `ProductsPage`, thêm:

```typescript
  const priceBooksQuery = useQuery({ queryKey: ["price-books"], queryFn: getPriceBooks });
  const priceBooks = useMemo(
    () => (priceBooksQuery.data ?? []).filter((book) => book.is_active),
    [priceBooksQuery.data],
  );
  const defaultBookCode = useMemo(
    () => priceBooks.find((book) => book.is_default)?.code ?? "facebook",
    [priceBooks],
  );
  const [activeBookCode, setActiveBookCode] = useState<string>("");
  const currentBookCode = activeBookCode || defaultBookCode;
```

Reset `setActiveBookCode("")` ở chỗ đang `resetForm()` (dòng ~482) và khi mở dialog sửa (dòng ~495) — mở form luôn bắt đầu ở tab mặc định.

- [ ] **Step 6: Sửa `updatePackage` để ghi theo tab**

```typescript
  const updatePackagePrice = (index: number, bookCode: string, raw: string) => {
    const nextVal: number | "" = raw === "" ? "" : Number(raw);
    setFormData((prev) => ({
      ...prev,
      packages: prev.packages.map((pkg, idx) =>
        idx === index ? { ...pkg, prices: { ...pkg.prices, [bookCode]: nextVal } } : pkg,
      ),
    }));
    const errKey = `packages.${index}.price`;
    if (formErrors[errKey]) setFormErrors((prev) => ({ ...prev, [errKey]: "" }));
  };
```

`updatePackage` cũ giữ nguyên cho `quantity/weight/length/width/height` (bỏ `"price"` khỏi union `PackageFormNumField`).

- [ ] **Step 7: Sửa validate — chỉ bắt buộc giá ở tab mặc định**

Trong `validateForm` (dòng 324-364), thay khối kiểm tra giá:

```typescript
      const price = Number(pkg.prices[defaultBookCode] || 0);
      if (!price || price <= 0) {
        errors[`packages.${idx}.price`] = t(
          "products.form.priceRequiredDefaultBook",
          "Bắt buộc nhập giá ở bảng giá mặc định.",
        );
      }
```

Nếu lỗi rơi vào tab đang ẩn, tự chuyển về tab mặc định trước khi `setFormErrors` để user thấy được ô đỏ:

```typescript
    if (Object.keys(errors).some((key) => key.endsWith(".price"))) {
      setActiveBookCode(defaultBookCode);
    }
```

- [ ] **Step 8: Sửa `PackagesEditor` — thêm tab + ô giá theo tab + placeholder kế thừa**

Signature mới:

```typescript
type PackagesEditorProps = {
  packages: PackageForm[];
  priceBooks: PriceBook[];
  activeBookCode: string;
  defaultBookCode: string;
  onSelectBook: (code: string) => void;
  errors: Record<string, string>;
  onUpdate: (index: number, field: PackageFormNumField, raw: string) => void;
  onUpdatePrice: (index: number, bookCode: string, raw: string) => void;
  onFocusClearZero: (index: number, field: PackageFormNumField) => void;
  onAdd: () => void;
  onRemove: (index: number) => void;
  t: (key: string, fallback?: string) => string;
};
```

Thêm dải tab ngay dưới header (trên `{errors["packages._base"] && ...}`):

```tsx
      {priceBooks.length > 1 && (
        <div className="mb-3 inline-flex rounded-md border border-border bg-muted/30 p-0.5">
          {priceBooks.map((book) => (
            <button
              key={book.code}
              type="button"
              onClick={() => onSelectBook(book.code)}
              className={
                book.code === activeBookCode
                  ? "rounded px-3 py-1 text-sm font-medium bg-background text-foreground shadow-sm"
                  : "rounded px-3 py-1 text-sm text-muted-foreground hover:text-foreground"
              }
            >
              {book.name}
            </button>
          ))}
        </div>
      )}
```

Trong vòng lặp gói, thay ô giá (dòng 1219-1232):

```tsx
                {(() => {
                  const isDefaultTab = activeBookCode === defaultBookCode;
                  const value = pkg.prices[activeBookCode] ?? "";
                  const inherited = pkg.prices[defaultBookCode];
                  return (
                    <div className="col-span-3">
                      <label className="mb-1 block text-xs text-muted-foreground">
                        {t("products.form.packagePrice", "Giá (cả gói)")}
                      </label>
                      <Input
                        type="number"
                        min="0"
                        value={value}
                        placeholder={
                          !isDefaultTab && inherited
                            ? t("products.form.inheritedPrice", "Kế thừa {price}").replace(
                                "{price}",
                                String(inherited),
                              )
                            : undefined
                        }
                        onChange={(e) => onUpdatePrice(index, activeBookCode, e.target.value)}
                        className={priceErr && isDefaultTab ? "border-red-500" : ""}
                      />
                      {priceErr && isDefaultTab && (
                        <p className="mt-1 text-xs text-red-500">{priceErr}</p>
                      )}
                    </div>
                  );
                })()}
```

Khoá số lượng và cân nặng/kích thước ở tab không mặc định — thêm `disabled={activeBookCode !== defaultBookCode}` vào các `Input` `quantity`, `weight`, `length`, `width`, `height`, và vào nút "Thêm gói" / nút xoá gói. Thêm dòng gợi ý dưới tab khi ở tab phụ:

```tsx
      {activeBookCode !== defaultBookCode && (
        <p className="mb-2 text-xs text-muted-foreground">
          {t(
            "products.form.secondaryBookHint",
            "Số lượng gói và cân nặng/kích thước dùng chung mọi nền tảng — sửa ở tab mặc định.",
          )}
        </p>
      )}
```

- [ ] **Step 9: Thêm chuỗi i18n**

Trong `frontend/src/i18n/messages/products.ts`, thêm vào cả `vi` và `en`:
`products.form.priceRequiredDefaultBook`, `products.form.inheritedPrice`, `products.form.secondaryBookHint`, `products.form.priceBookTab`.

- [ ] **Step 10: Kiểm tra type + lint + test**

```bash
cd frontend && npx tsc --noEmit && npm run lint:strict \
  && npx vitest run src/pages/__tests__/ProductsPage.prices.test.tsx
```
Expected: sạch, 4 test PASS.

- [ ] **Step 11: Commit**

```bash
git add frontend/src/pages frontend/src/i18n/messages/products.ts
git commit -m "feat(frontend): edit product prices per price book"
```

---

### Task 10: Select bảng giá ở dialog quản lý kênh

**Files:**
- Modify: `frontend/src/features/channels/ChannelManageDialog.tsx`
- Modify: `frontend/src/i18n/messages/channels.ts`

**Interfaces:**
- Consumes: `getPriceBooks` (Task 8), field `price_book_id` (Task 6).

- [ ] **Step 1: Đọc dialog hiện tại**

Run: `grep -n "owner\|is_active\|Select\|onSubmit\|payload" frontend/src/features/channels/ChannelManageDialog.tsx | head -40`
Mục đích: bám đúng pattern field/submit đang có, không dựng pattern mới.

- [ ] **Step 2: Thêm state + query**

```typescript
  const priceBooksQuery = useQuery({ queryKey: ["price-books"], queryFn: getPriceBooks });
  const [priceBookId, setPriceBookId] = useState<number | null>(channel?.price_book?.id ?? null);
```

Reset `priceBookId` mỗi khi `channel` đổi (trong effect đang reset các field khác).

- [ ] **Step 3: Thêm select vào form** — dùng component `Select` của shadcn/ui đang dùng trong file này:

```tsx
        <div className="space-y-1">
          <label className="text-sm font-medium">{t("channels.form.priceBook", "Bảng giá")}</label>
          <select
            className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
            value={priceBookId === null ? "" : String(priceBookId)}
            onChange={(e) => setPriceBookId(e.target.value === "" ? null : Number(e.target.value))}
          >
            <option value="">
              {t("channels.form.priceBookDefault", "(Theo mặc định)")}
            </option>
            {(priceBooksQuery.data ?? [])
              .filter((book) => book.is_active)
              .map((book) => (
                <option key={book.id} value={book.id}>
                  {book.name}
                </option>
              ))}
          </select>
        </div>
```

Nếu file đang dùng `<Select>` của shadcn thay vì `<select>` thuần, dùng đúng component đó — giữ nhất quán với các field còn lại trong dialog.

- [ ] **Step 4: Gửi `price_book_id` trong payload submit**

Thêm `price_book_id: priceBookId` vào object payload của mutation cập nhật kênh.

- [ ] **Step 5: Thêm chuỗi i18n**

`channels.form.priceBook`, `channels.form.priceBookDefault` vào cả `vi` và `en` trong `frontend/src/i18n/messages/channels.ts`.

- [ ] **Step 6: Kiểm tra type + lint + test hồi quy dialog kênh**

```bash
cd frontend && npx tsc --noEmit && npm run lint:strict \
  && npx vitest run src/features/channels
```
Expected: sạch, test hiện có vẫn PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/channels frontend/src/i18n/messages/channels.ts
git commit -m "feat(frontend): assign price book to channel"
```

---

### Task 11: Form đơn nạp catalog theo bảng giá của kênh

**Files:**
- Modify: `frontend/src/features/orders/use-orders-catalogs.ts:60-104,128-129,168-171,346-385`
- Modify: `frontend/src/features/orders/order-form/use-order-form-products.ts:33-59,74-95`
- Modify: `frontend/src/features/orders/OrderFormDialog.tsx`
- Test: `frontend/src/features/orders/__tests__/use-orders-catalogs.price-book.test.ts` (tạo mới)

**Interfaces:**
- Consumes: `?price_book=` (Task 5), `channel.price_book` (Task 6, 8).
- Produces: `loadProducts({ priceBook })`, `searchProducts(query, { priceBook })`.

- [ ] **Step 1: Viết test thất bại**

```typescript
import { describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

import { useOrdersCatalogs } from "@/features/orders/use-orders-catalogs";

const productPayload = (price: string) => [
  { id: 1, name: "SP", sku: "SKU-1", price, packages: [{ id: 1, quantity: 1, price }] },
];

describe("useOrdersCatalogs — cache theo bảng giá", () => {
  it("gửi price_book lên server và KHÔNG dùng chung cache giữa hai bảng giá", async () => {
    const fetchJson = vi
      .fn()
      .mockResolvedValueOnce(productPayload("100"))
      .mockResolvedValueOnce(productPayload("130"));
    const { result } = renderHook(() => useOrdersCatalogs(fetchJson));

    await act(async () => {
      await result.current.loadProducts({ priceBook: "facebook" });
    });
    expect(result.current.products[0].price).toBe("100");

    await act(async () => {
      await result.current.loadProducts({ priceBook: "web" });
    });
    expect(result.current.products[0].price).toBe("130");
    expect(fetchJson).toHaveBeenCalledTimes(2);
    expect(String(fetchJson.mock.calls[1][0])).toContain("price_book=web");
  });

  it("nạp lại cùng bảng giá thì dùng cache, không gọi server lần nữa", async () => {
    const fetchJson = vi.fn().mockResolvedValue(productPayload("100"));
    const { result } = renderHook(() => useOrdersCatalogs(fetchJson));

    await act(async () => {
      await result.current.loadProducts({ priceBook: "facebook" });
      await result.current.loadProducts({ priceBook: "facebook" });
    });
    expect(fetchJson).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận đỏ**

Run: `cd frontend && npx vitest run src/features/orders/__tests__/use-orders-catalogs.price-book.test.ts`
Expected: FAIL — lần gọi thứ hai lấy cache và trả giá `100`.

- [ ] **Step 3: Sửa `fetchAllProductsCatalog` + `searchProductsCatalog` nhận `priceBook`**

```typescript
async function fetchAllProductsCatalog(
  fetchJson: FetchJson,
  priceBook?: string,
): Promise<ProductRow[]> {
  const allProducts: ProductRow[] = [];
  let offset = 0;

  for (let page = 0; page < PRODUCTS_FETCH_MAX_PAGES; page += 1) {
    const query = new URLSearchParams({
      limit: String(PRODUCTS_FETCH_LIMIT),
      offset: String(offset),
    });
    if (priceBook) query.set("price_book", priceBook);
    const data = await fetchJson(`/api/orders/products?${query.toString()}`);
    const parsed = extractListPayload<ProductRow>(data);
    if (parsed.length === 0) break;
    allProducts.push(...parsed);
    if (parsed.length < PRODUCTS_FETCH_LIMIT) break;
    offset += PRODUCTS_FETCH_LIMIT;
  }

  return allProducts;
}
```

Tương tự thêm `if (priceBook) params.set("price_book", priceBook);` vào `searchProductsCatalog`, với `options?: { limit?; signal?; priceBook? }`.

- [ ] **Step 4: Đổi cache products sang Map theo bảng giá**

```typescript
  // Cache/in-flight tách theo bảng giá: đổi kênh sang nền tảng khác mà dùng chung
  // một ref thì form sẽ bán giá của kênh cũ.
  const productsCacheRef = useRef<Map<string, { value: ProductRow[]; expiresAt: number }>>(new Map());
  const productsInFlightRef = useRef<Map<string, Promise<ProductRow[]>>>(new Map());
```

`clearProductsCache` đổi thành `productsCacheRef.current.clear(); productsInFlightRef.current.clear();`.

- [ ] **Step 5: Sửa `loadProducts`**

```typescript
  const loadProducts = useCallback(
    async (overrides?: LoadProductsOverrides) => {
      const bookKey = overrides?.priceBook ?? "";
      const cached = productsCacheRef.current.get(bookKey);
      if (!overrides?.force && cached && isCacheFresh(cached.expiresAt)) {
        setProducts(cached.value);
        return;
      }
      const inFlight = productsInFlightRef.current.get(bookKey);
      if (!overrides?.force && inFlight) {
        setProducts(await inFlight);
        return;
      }

      const request = (async () => {
        const parsed = await fetchAllProductsCatalog(fetchJson, overrides?.priceBook);
        productsCacheRef.current.set(bookKey, {
          value: parsed,
          expiresAt: Date.now() + STATIC_CATALOG_CACHE_TTL_MS,
        });
        return parsed;
      })();
      productsInFlightRef.current.set(bookKey, request);
      try {
        setProducts(await request);
      } finally {
        productsInFlightRef.current.delete(bookKey);
      }
    },
    [fetchJson],
  );
```

và mở rộng `LoadProductsOverrides`:

```typescript
type LoadProductsOverrides = {
  force?: boolean;
  /** Code bảng giá của kênh đang chọn. Rỗng = bảng mặc định. */
  priceBook?: string;
};
```

`searchProducts` truyền tiếp `options.priceBook` xuống `searchProductsCatalog`.

- [ ] **Step 6: Chạy test để xác nhận xanh**

Run: `cd frontend && npx vitest run src/features/orders/__tests__/use-orders-catalogs.price-book.test.ts`
Expected: PASS — 2 test.

- [ ] **Step 7: Reset catalog "đã biết" khi đổi bảng giá**

Trong `use-order-form-products.ts`, thêm `priceBookCode: string` vào `UseOrderFormProductsArgs` và effect:

```typescript
  // knownProducts tích luỹ theo id và KHÔNG bao giờ bị loại bỏ — đổi bảng giá mà
  // không dọn thì sản phẩm chỉ có ở lần nạp trước vẫn giữ giá của nền tảng cũ.
  const previousBookRef = useRef(priceBookCode);
  useEffect(() => {
    if (previousBookRef.current === priceBookCode) return;
    previousBookRef.current = priceBookCode;
    setKnownProducts([]);
    setDefaultProductOptions([]);
  }, [priceBookCode]);
```

Đặt effect này TRƯỚC effect `mergeProducts(products)` để lần render kế tiếp nạp lại từ prop `products` (đã là dữ liệu bảng giá mới).

- [ ] **Step 8: Nối bảng giá của kênh vào form đơn**

Trong `OrderFormDialog.tsx`, sau khối dựng `channelOptions` (dòng ~322):

```typescript
  // Bảng giá suy từ kênh đang chọn. Sale không thấy khái niệm này trên form.
  const priceBookCode = useMemo(() => {
    const channel = channels.find((item) => item.code === form.channel_code);
    return channel?.price_book?.code ?? "";
  }, [channels, form.channel_code]);
```

Truyền `priceBookCode` vào `useOrderFormProducts({ ..., priceBookCode })`, và gọi `loadProducts({ priceBook: priceBookCode })` ở nơi form đang nạp catalog (tìm bằng `grep -n "loadProducts\|onSearchProducts" frontend/src/features/orders/OrderFormDialog.tsx frontend/src/features/orders/orders-page/*.tsx`).

- [ ] **Step 9: Tính lại giá các dòng chưa sửa tay khi đổi kênh**

Trong `OrderFormDialog.tsx`, thêm ref đánh dấu dòng đã sửa giá tay. Ở handler ô giá (`focusedPriceIndex` / `onChange` của input giá), thêm `manualPriceIndexesRef.current.add(index)`. Rồi:

```typescript
  const manualPriceIndexesRef = useRef<Set<number>>(new Set());
  const previousPriceBookRef = useRef(priceBookCode);

  useEffect(() => {
    if (previousPriceBookRef.current === priceBookCode) return;
    previousPriceBookRef.current = priceBookCode;
    // Đổi kênh → tính lại giá theo bảng giá mới, TRỪ dòng sale đã tự gõ giá.
    updateItems((items) =>
      items.map((item, index) => {
        if (manualPriceIndexesRef.current.has(index)) return item;
        const product = item.product_id ? productById.get(item.product_id) : undefined;
        if (!product) return item;
        const pkg = findPackageForQuantity(product, Math.max(1, Number(item.quantity) || 1));
        return { ...item, unit_price: pkg ? packageUnitPrice(pkg) : product.price || item.unit_price };
      }),
    );
  }, [priceBookCode, productById, updateItems]);
```

**Lưu ý thứ tự:** effect này chỉ đúng khi `productById` đã là dữ liệu của bảng giá mới. Vì `useOrderFormProducts` xoá `knownProducts` khi đổi book (Step 7), `productById` rỗng ở render đầu → `!product` → giữ nguyên; render sau khi catalog mới về mới reprice. Đó là hành vi mong muốn, nhưng phải kiểm chứng bằng test ở Step 10.

- [ ] **Step 10: Viết test reprice**

Tạo `frontend/src/features/orders/__tests__/OrderFormDialog.reprice.test.tsx`. Bám theo cách các test hiện có trong `frontend/src/features/orders/__tests__/` dựng props cho `OrderFormDialog` (đọc một file test có sẵn trước). Ba case:

1. Đổi kênh từ kênh không gán bảng giá sang kênh gán `web` → dòng sản phẩm chưa sửa tay đổi `unit_price` từ `100` sang `130`.
2. Dòng đã sửa tay (`99000`) giữ nguyên sau khi đổi kênh.
3. Mở dialog ở mode `edit` với đơn cũ → `unit_price` KHÔNG đổi khi chỉ mở form (không có sự kiện đổi kênh).

- [ ] **Step 11: Chạy toàn bộ test frontend liên quan**

```bash
cd frontend && npx tsc --noEmit && npm run lint:strict \
  && npx vitest run src/features/orders src/pages
```
Expected: PASS (trừ `RequireAuth.test.tsx` vốn đỏ sẵn trên master).

- [ ] **Step 12: Commit**

```bash
git add frontend/src/features/orders
git commit -m "feat(frontend): load order form catalog by channel price book"
```

---

## SPRINT 4 — Cutover và contract

### Task 12: Gán kênh KOC sang bảng giá web + smoke checklist

**Files:**
- Create: `backend/apps/orders/management/commands/assign_koc_price_book.py`
- Test: `backend/apps/orders/tests/test_price_book_order_pricing.py` (thêm class)

**Interfaces:**
- Consumes: `Channel.is_koc` (có sẵn), `PriceBook` (Task 1).

- [ ] **Step 1: Viết test thất bại**

```python
class AssignKocPriceBookCommandTests(TestCase):
    def test_command_assigns_web_book_to_koc_channels(self):
        from django.core.management import call_command

        koc = Channel.objects.create(code="koc", name="Hàng mẫu KOC")
        other = Channel.objects.create(code="sale-fb", name="Sale FB")
        call_command("assign_koc_price_book")
        koc.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(koc.price_book.code, "web")
        self.assertIsNone(other.price_book)

    def test_command_is_idempotent(self):
        from django.core.management import call_command

        koc = Channel.objects.create(code="koc", name="Hàng mẫu KOC")
        call_command("assign_koc_price_book")
        call_command("assign_koc_price_book")
        koc.refresh_from_db()
        self.assertEqual(koc.price_book.code, "web")
```

- [ ] **Step 2: Chạy test để xác nhận đỏ**

Run: `... python -m pytest apps/orders/tests/test_price_book_order_pricing.py::AssignKocPriceBookCommandTests -q --create-db`
Expected: FAIL — `CommandError: Unknown command`

- [ ] **Step 3: Viết command**

```python
"""Gán kênh KOC sang bảng giá web (cutover pha 2).

Tách thành command thay vì nhét vào migration: đây là quyết định NGHIỆP VỤ, cần
chạy có kiểm soát và chạy lại được, không phải một phần lược đồ DB.
"""

from django.core.management.base import BaseCommand

from apps.channels_core.models import Channel
from apps.orders.models import PriceBook


class Command(BaseCommand):
    help = "Gán mọi kênh KOC sang bảng giá web."

    def add_arguments(self, parser):
        parser.add_argument("--book", default="web", help="Code bảng giá đích (mặc định: web).")
        parser.add_argument("--dry-run", action="store_true", help="Chỉ in ra, không ghi DB.")

    def handle(self, *args, **options):
        code = str(options["book"]).strip().lower()
        book = PriceBook.objects.filter(code=code, deleted_at__isnull=True, is_active=True).first()
        if book is None:
            self.stderr.write(f"Không tìm thấy bảng giá active với code '{code}'.")
            return

        channels = [
            channel
            for channel in Channel.objects.filter(deleted_at__isnull=True)
            if channel.is_koc
        ]
        for channel in channels:
            self.stdout.write(f"{channel.code} ({channel.id}) -> {book.code}")
        if options["dry_run"]:
            self.stdout.write(f"[dry-run] {len(channels)} kênh sẽ được gán.")
            return

        updated = Channel.objects.filter(id__in=[c.id for c in channels]).update(price_book=book)
        self.stdout.write(f"Đã gán {updated} kênh KOC sang bảng giá '{book.code}'.")
```

- [ ] **Step 4: Chạy test để xác nhận xanh**

Run: `... python -m pytest apps/orders/tests/test_price_book_order_pricing.py -q --create-db`
Expected: PASS — 5 test.

- [ ] **Step 5: Viết checklist cutover vào plan**

Tạo `docs/superpowers/plans/2026-07-31-platform-price-books-cutover.md` với nội dung: thứ tự deploy (migrate → deploy backend → deploy frontend → chạy `assign_koc_price_book --dry-run` → chạy thật), cách rollback từng bước, và 2 smoke test thủ công trên staging:

1. Tạo đơn kênh Sale FB, chọn sản phẩm có giá 2 nền tảng → `unit_price` phải bằng giá facebook.
2. Tạo đơn kênh KOC cùng sản phẩm đó → `unit_price` phải bằng giá web.

Kiểm chứng bằng SQL: `SELECT o.id, c.code, oi.unit_price FROM order_items oi JOIN orders o ON o.id = oi.order_id JOIN channels c ON c.id = o.channel_id ORDER BY o.id DESC LIMIT 5;`

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/management docs/superpowers/plans/2026-07-31-platform-price-books-cutover.md \
  backend/apps/orders/tests/test_price_book_order_pricing.py
git commit -m "feat(orders): add command to assign koc channels to web price book"
```

---

### Task 13: Pha 3 — drop `product_packages.price`

> **DỪNG:** Task này CHỈ chạy sau khi Task 1-12 đã lên production và chạy ổn định ≥ 1 tuần. Không gộp vào cùng MR với các task trên.

**Files:**
- Create: `backend/apps/orders/migrations/0113_drop_product_package_price.py`
- Modify: `backend/apps/orders/models.py` (bỏ `ProductPackage.price`)
- Modify: `.../product_item_crm_serializers.py` (bỏ dual-write vào `ProductPackage.price`)

**Interfaces:**
- Consumes: mọi thứ từ Task 1-12.
- Produces: `ProductPackage` không còn cột `price`. `Product.price` GIỮ NGUYÊN (mirror giá gói 1 của bảng mặc định, đang được `products_grouped`, KiotViet, report đọc).

- [ ] **Step 1: Xác nhận không còn chỗ nào đọc `ProductPackage.price`**

```bash
grep -rn "packages\.price\|pkg\[.price.\]\|package.price" backend/apps --include=*.py | grep -v migrations
```
Expected: không có kết quả ngoài serializer sắp sửa. Có kết quả khác → sửa chỗ đó trước, không drop.

- [ ] **Step 2: Viết test thất bại**

Thêm vào `backend/apps/orders/tests/test_price_book_api.py`:

```python
    def test_product_package_has_no_price_column(self):
        from apps.orders.models import ProductPackage

        field_names = {f.name for f in ProductPackage._meta.get_fields()}
        self.assertNotIn("price", field_names)
```

- [ ] **Step 3: Chạy test để xác nhận đỏ**

Run: `... python -m pytest apps/orders/tests/test_price_book_api.py -k no_price_column -q --create-db`
Expected: FAIL — `price` vẫn còn.

- [ ] **Step 4: Bỏ dual-write trong `_replace_packages`**

Xoá dòng `price=Decimal(...)` khỏi phần dựng `ProductPackage(...)` (Task 4 Step 3) và xoá comment "Cột legacy".

- [ ] **Step 5: Bỏ field khỏi model + viết migration**

Xoá `price = models.DecimalField(...)` khỏi `class ProductPackage`, rồi:

```python
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("orders", "0112_price_books")]

    operations = [
        migrations.RemoveField(model_name="productpackage", name="price"),
    ]
```

- [ ] **Step 6: Chạy test để xác nhận xanh**

Run: `... python -m pytest apps/orders -q --create-db`
Expected: PASS toàn bộ.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/orders
git commit -m "refactor(orders): drop legacy product package price column"
```

---

## Tự kiểm tra plan so với spec

| Yêu cầu trong spec | Task |
|---|---|
| Entity `PriceBook`, không dùng tên `PriceList` | 1 (+ Global Constraints) |
| `ProductPackagePrice` bảng con, unique + index | 1 |
| `Channel.price_book` nullable | 1 |
| Partial unique cho `is_default` | 1, 3 |
| Seed `facebook` (default) + `web` | 1 |
| Backfill giá hiện có vào book mặc định | 1 |
| `resolve_price_book` / `package_prices` / `unit_price_for` | 2 |
| Fallback khi book xoá/tắt/không gán | 2 |
| `price_book=<code>`, lenient | 2, 5 |
| CRUD bảng giá, RBAC section `products` | 3 |
| Chặn xoá book mặc định | 3 |
| Contract quản trị: `prices` dict thô, không fallback | 4 |
| Validate giá chỉ bắt buộc ở book mặc định | 4, 9 |
| `Product.price` mirror book mặc định | 4 |
| Contract catalog: `price` scalar đã fallback | 5 |
| ETag phản ánh book + giá + bảng giá | 5 |
| `products_grouped` giữ nguyên (giá mặc định) | 5 |
| `ChannelSerializer.price_book` | 6 |
| Ingest landing/Lark theo book của kênh | 7 |
| `unit_price` tường minh vẫn thắng (import có cột giá) | 7 |
| Tab nền tảng trong form sản phẩm | 9 |
| Khoá số lượng/cân nặng/kích thước ở tab phụ | 9 |
| Placeholder "kế thừa" ở tab phụ | 9 |
| Select bảng giá ở dialog kênh | 10 |
| Cache catalog tách theo book | 11 |
| Reprice dòng chưa sửa tay khi đổi kênh | 11 |
| Đơn cũ không reprice | 11 (test case 3) |
| KOC → web bằng dữ liệu, không hardcode | 12 |
| Smoke test staging trước khi mở pha 2 | 12 |
| Pha 3 drop `product_packages.price` | 13 |

**Ngoài phạm vi** (khớp spec, không có task): giá theo thời gian, giá theo khách hàng, gói khác nhau giữa nền tảng, cân nặng/kích thước theo nền tảng, đồng bộ bảng giá sang KiotViet, backfill giá đơn KOC cũ.
