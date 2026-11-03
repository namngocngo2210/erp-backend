# Product Categories Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement task-by-task.

**Goal:** Thêm tính năng **danh mục sản phẩm** (nhiều-nhiều với sản phẩm), có trang quản lý riêng "Danh mục sản phẩm" trong nhóm Kho; trên trang danh mục có thể tạo danh mục và **thêm sản phẩm vào danh mục**. KHÔNG hiển thị trên trang Khách hàng.

**Architecture:** `ProductCategory` model trong `apps/orders/` + M2M `Product.categories`. CRUD API tại `/api/orders/product-categories`. Trang frontend mới `ProductCategoriesPage`, wiring section `product-categories` vào routes/sidebar/permissions. Làm trên branch hiện tại `loyal-customers-20260520`.

**Tech Stack:** Django + DRF + PostgreSQL (manage.py test); React + TS + shadcn-style components (vitest).

---

## Environment (test commands)
- pytest is BROKEN (BOM in pytest.ini) → use `manage.py test`. Temp Postgres (localhost:5432) + Redis (localhost:6379, password `Desi@2026`), DB creds `desi_order`. Interpreter `/Users/desi/Desktop/workspace/desi-order/.venv/bin/python`. ruff not installed.
- Backend test: `cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 <venv>/python manage.py test apps.orders.tests.<module>`
- Frontend: `cd frontend && npx tsc --noEmit && npx vitest run && npm run build`.

---

## Task 1: Backend — ProductCategory model + M2M + CRUD API

**Files:**
- Modify: `backend/apps/orders/models.py` (add `ProductCategory`, add M2M to `Product`)
- New migration in `backend/apps/orders/migrations/`
- Modify: `backend/apps/orders/serializers.py` (re-export) + the serializers impl file `backend/apps/orders/transport/serializers/order_serializers_impl/product_item_crm_serializers.py` (add `ProductCategorySerializer`, add `categories`/`category_ids` to `ProductSerializer`)
- Modify: views `backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py` (add category CRUD views)
- Modify: `backend/apps/orders/urls.py` (add routes)
- New test: `backend/apps/orders/tests/test_product_categories_api.py`

### Steps
- [ ] **1. Model** — in `models.py`, add after `Product`/`ProductPackage`:
```python
class ProductCategory(models.Model):
    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_categories"
        ordering = ["sort_order", "name"]
        indexes = [
            models.Index(fields=["is_active"], name="prodcat_is_active_idx"),
            models.Index(fields=["deleted_at"], name="prodcat_deleted_idx"),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"
```
   And add to `Product`: `categories = models.ManyToManyField(ProductCategory, related_name="products", blank=True)`.

- [ ] **2. makemigrations + migrate**: `manage.py makemigrations orders` then `migrate`. Verify the M2M through table is created. (atomic default OK; no CONCURRENTLY here.)

- [ ] **3. Write failing test** `tests/test_product_categories_api.py`:
```python
from django.test import TestCase
from rest_framework.test import APIClient

from apps.orders.models import Product, ProductCategory
from apps.users.models import User, UserRole


class ProductCategoryApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username="admin-pc", password="x", role=UserRole.ADMIN)
        self.client.force_authenticate(user=self.admin)
        self.p1 = Product.objects.create(name="Ghế massage", sku="GM-001", price="0")
        self.p2 = Product.objects.create(name="Đệm massage", sku="DM-001", price="0")

    def test_requires_auth(self):
        anon = APIClient()
        self.assertEqual(anon.get("/api/orders/product-categories?limit=10&offset=0").status_code, 401)

    def test_create_with_products_and_list(self):
        res = self.client.post(
            "/api/orders/product-categories",
            {"code": "GHE", "name": "Ghế", "product_ids": [self.p1.id]},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        cat_id = res.data["id"]
        self.assertEqual(res.data["product_count"], 1)
        self.assertEqual([p["id"] for p in res.data["products"]], [self.p1.id])

        lst = self.client.get("/api/orders/product-categories?limit=10&offset=0")
        self.assertEqual(lst.status_code, 200)
        self.assertTrue(any(c["code"] == "GHE" for c in lst.data))

        # Update: replace product set with both products
        upd = self.client.patch(
            f"/api/orders/product-categories/{cat_id}",
            {"product_ids": [self.p1.id, self.p2.id]},
            format="json",
        )
        self.assertEqual(upd.status_code, 200)
        self.assertEqual(upd.data["product_count"], 2)

    def test_delete_soft(self):
        cat = ProductCategory.objects.create(code="X", name="X")
        res = self.client.delete(f"/api/orders/product-categories/{cat.id}")
        self.assertEqual(res.status_code, 204)
        cat.refresh_from_db()
        self.assertIsNotNone(cat.deleted_at)
        # excluded from list
        lst = self.client.get("/api/orders/product-categories?limit=10&offset=0")
        self.assertFalse(any(c["code"] == "X" for c in lst.data))
```
   Run → confirm 404/red.

- [ ] **4. Serializer** — in `product_item_crm_serializers.py` add:
```python
from apps.orders.models import Product, ProductCategory  # adjust to existing import line


class ProductCategoryProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ("id", "name", "sku")


class ProductCategorySerializer(serializers.ModelSerializer):
    products = ProductCategoryProductSerializer(many=True, read_only=True)
    product_count = serializers.SerializerMethodField()
    product_ids = serializers.PrimaryKeyRelatedField(
        many=True, write_only=True, required=False,
        queryset=Product.objects.filter(deleted_at__isnull=True),
    )

    class Meta:
        model = ProductCategory
        fields = ("id", "code", "name", "sort_order", "is_active",
                  "products", "product_count", "product_ids", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

    def get_product_count(self, obj):
        # use prefetched/annotated when available; fallback to count()
        return getattr(obj, "product_count", None) if hasattr(obj, "product_count") else obj.products.count()

    def create(self, validated_data):
        product_ids = validated_data.pop("product_ids", None)
        category = super().create(validated_data)
        if product_ids is not None:
            category.products.set(product_ids)
        return category

    def update(self, instance, validated_data):
        product_ids = validated_data.pop("product_ids", None)
        category = super().update(instance, validated_data)
        if product_ids is not None:
            category.products.set(product_ids)
        return category
```
   NOTE: keep `get_product_count` simple and correct — returning `obj.products.count()` is fine (page size is small). To avoid N+1 on list, the view should `prefetch_related("products")` so `products` serialization + `.count()` use the prefetch.
   Also export `ProductCategorySerializer` from `backend/apps/orders/serializers.py` the same way `ProductSerializer` is exported.

- [ ] **5. Views** — in `order_ingest_product_dashboard_view.py` add (mirror `list_create_products`/`update_delete_product`):
```python
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
@never_cache
def list_create_product_categories(request):
    if request.method == "GET":
        pagination, error_response = _parse_required_pagination(request, max_limit=10000)
        if error_response is not None:
            return error_response
        q = request.query_params.get("q", "").strip()
        qs = ProductCategory.objects.filter(deleted_at__isnull=True).prefetch_related("products")
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))
        qs = qs.order_by("sort_order", "name", "id")[pagination["offset"]: pagination["offset"] + pagination["limit"]]
        return Response(ProductCategorySerializer(qs, many=True).data, status=status.HTTP_200_OK)
    serializer = ProductCategorySerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
@never_cache
def update_delete_product_category(request, category_id: int):
    category = ProductCategory.objects.filter(id=category_id, deleted_at__isnull=True).prefetch_related("products").first()
    if not category:
        return Response({"detail": "Không tìm thấy danh mục."}, status=status.HTTP_404_NOT_FOUND)
    if request.method == "DELETE":
        category.deleted_at = timezone.now()
        category.is_active = False
        category.save(update_fields=["deleted_at", "is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)
    serializer = ProductCategorySerializer(instance=category, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=status.HTTP_200_OK)
```
   Add `ProductCategory` + `ProductCategorySerializer` to the existing imports at top of the view file.

- [ ] **6. URLs** — in `backend/apps/orders/urls.py` import the two new views and add (near the products routes):
```python
    path("product-categories", list_create_product_categories, name="orders-product-categories"),
    path("product-categories/<int:category_id>", update_delete_product_category, name="orders-product-category-detail"),
```

- [ ] **7. Run test green**, then commit: `git add backend/apps/orders/ && git commit -m "Add product category model and CRUD API"`.

---

## Task 2: Frontend — Product Categories page + section wiring

**Files:**
- New: `frontend/src/lib/api/product-categories.ts`
- New: `frontend/src/pages/ProductCategoriesPage.tsx`
- Modify: `frontend/src/lib/routes.ts` (AppSection, SECTION_TO_PATH, sectionFromPath)
- Modify: `frontend/src/App.tsx` (lazy import + render `section === "product-categories"`)
- Modify: `frontend/src/components/Sidebar.tsx` (NAV_GROUPS warehouse group)
- Modify: `frontend/src/features/users/DepartmentManager.tsx` (SECTION_GROUPS Kho)
- Modify backend: `backend/apps/users/models.py` (`VALID_SECTIONS` add `"product-categories"`)
- New test: `frontend/src/lib/api/__tests__/product-categories.test.ts`

### Steps
- [ ] **1. Backend valid section** — in `backend/apps/users/models.py`, add `"product-categories"` to `VALID_SECTIONS` (so admins get it and departments can be granted it). No migration needed (it's a Python list used for validation + admin defaults).

- [ ] **2. API client** (`product-categories.ts`) — mirror `products.ts`:
```typescript
import { fetchJson } from "@/services/http";
const API_BASE = "/api/orders/product-categories";

export interface ProductCategoryProduct { id: number; name: string; sku: string; }
export interface ProductCategory {
  id: number; code: string; name: string; sort_order: number; is_active: boolean;
  products: ProductCategoryProduct[]; product_count: number;
  created_at: string; updated_at: string;
}
export interface ProductCategoryUpsert {
  code?: string; name?: string; sort_order?: number; is_active?: boolean; product_ids?: number[];
}

export async function getProductCategories(params: { q?: string; limit?: number; offset?: number } = {}): Promise<ProductCategory[]> {
  const query = new URLSearchParams({ limit: String(params.limit ?? 100), offset: String(params.offset ?? 0) });
  if (params.q?.trim()) query.set("q", params.q.trim());
  return fetchJson(`${API_BASE}?${query.toString()}`);
}
export async function createProductCategory(data: ProductCategoryUpsert): Promise<ProductCategory> {
  return fetchJson(API_BASE, { method: "POST", body: JSON.stringify(data) });
}
export async function updateProductCategory(id: number, data: ProductCategoryUpsert): Promise<ProductCategory> {
  return fetchJson(`${API_BASE}/${id}`, { method: "PATCH", body: JSON.stringify(data) });
}
export async function deleteProductCategory(id: number): Promise<void> {
  return fetchJson(`${API_BASE}/${id}`, { method: "DELETE" });
}
```

- [ ] **3. API client test** (`product-categories.test.ts`) — vitest, mock `@/services/http`, assert URL/method for getProductCategories (with q) and createProductCategory (POST body includes product_ids). Run red → after step 2 file exists → green.

- [ ] **4. Routes wiring** — `routes.ts`: add `"product-categories"` to the `AppSection` union; add `"product-categories": "/product-categories"` to `SECTION_TO_PATH`; ensure `sectionFromPath` maps `/product-categories` → `"product-categories"` (read the function; if it iterates SECTION_TO_PATH it's automatic, else add a branch).

- [ ] **5. Sidebar** — `Sidebar.tsx`: in the warehouse group (`sidebar.group.warehouse`) `items`, add after products: `{ key: "product-categories", labelKey: "topnav.tabs.productCategories", icon: <a lucide icon, e.g. Layers or FolderTree> }`. Import the icon. The label uses i18n key with a Vietnamese fallback — if the codebase's `t()` supports fallback, pass "Danh mục sản phẩm"; otherwise add the key to the messages file used (check how other tabs add labels — `topnav.tabs.products`). Add `topnav.tabs.productCategories: "Danh mục sản phẩm"` to the i18n messages (vi) the same place `products` is defined.

- [ ] **6. DepartmentManager** — `DepartmentManager.tsx`: in the "Kho" SECTION_GROUPS entry, add `{ key: "product-categories", label: "Danh mục sản phẩm" }` so it's grantable.

- [ ] **7. App.tsx** — lazy import `const ProductCategoriesPage = lazy(() => import("@/pages/ProductCategoriesPage"));` and render near the products line: `{section === "product-categories" && <ProductCategoriesPage canManageUsers={user.role === "admin"} />}`. If there's a per-section data loader switch (the `targetSection === ...` chain), no entry is needed unless the page needs preloaded data (it self-fetches), so skip.

- [ ] **8. ProductCategoriesPage** — mirror `ProductsPage.tsx` patterns (list table, create/edit dialog, delete, bulk where relevant, `canManageUsers` gating, search, pagination, toasts). Columns: Mã (code), Tên (name), Số SP (product_count), Trạng thái (is_active). Create/edit dialog fields: code, name, sort_order (optional), is_active toggle, and a **product multi-select** to add products into the category:
   - Use the existing multi-select component the repo has (search for `MultiSelect` in `frontend/src/components`; ProductsPage or OrdersTable may use it). Load product options via `getProducts({ q, limit })` from `@/lib/api/products` (search-as-you-type or load a reasonable page). Pre-select `category.products` when editing.
   - On save, send `product_ids` (array of selected product ids) plus code/name/is_active.

- [ ] **9. Verify** — `cd frontend && npx tsc --noEmit && npx vitest run && npm run build`. tsc clean, build succeeds, new api test passes, NO new vitest failures (4 pre-existing unrelated files may still fail: websocket, OrdersTable.mobile, ChannelsTable.mobile, OrderFormDialog).

- [ ] **10. Commit** — `git add frontend/ backend/apps/users/models.py && git commit -m "Add product categories management page and section wiring"`.

---

## Self-review checklist
- New section `product-categories` added in ALL places `products` exists: backend `VALID_SECTIONS`, FE routes (`AppSection`, `SECTION_TO_PATH`, `sectionFromPath`), Sidebar NAV_GROUPS, DepartmentManager SECTION_GROUPS, App.tsx (lazy import + render). Missing any one → menu/route broken.
- M2M assignment is on the CATEGORY page (not the product form).
- Soft delete consistent with products (`deleted_at` + `is_active=False`, excluded from list).
- No customer-page changes.
