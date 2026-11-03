# Phân quyền page + scope danh sách theo phòng ban ở Backend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đẩy việc lọc theo phòng ban từ frontend về backend — chặn page theo `allowed_sections` và scope danh sách kênh/nhân viên theo `department` của user đăng nhập.

**Architecture:** Hai lớp cắt ngang dựa trên `request.user`: (1) DRF permission factory `require_section(*sections)` gắn lên view để chặn page; (2) mở rộng repository hiện có để scope danh sách kênh (`Channel.departments` M2M, ADS giữ per-user) và nhân viên (`User.department`). Frontend bỏ phần lọc, render thẳng dữ liệu BE trả.

**Tech Stack:** Django 5 / DRF (Python 3.12), pytest + DRF APIClient; React + Vite + TypeScript + Vitest.

**Spec:** `docs/superpowers/specs/2026-06-04-department-scoping-backend-design.md`

---

## File Structure

**Backend — tạo mới:**
- `backend/apps/users/permissions.py` — `require_section(*sections)` + hằng `ORDER_SECTIONS`.
- `backend/apps/users/tests/test_permissions.py` — unit test permission.
- `backend/apps/channels_core/tests/test_channel_list_scope.py` — test scope kênh.
- `backend/apps/channels_core/tests/test_staff_scope.py` — test scope nhân viên.
- `backend/apps/orders/tests/test_section_enforcement_api.py` — test chặn page (orders/landing/koc).

**Backend — sửa:**
- `backend/apps/channels_core/infrastructure/repositories/channel_repositories_impl.py` — scope `list_for_user`, thêm tham số `department` cho `list_staff_users`.
- `backend/apps/channels_core/transport/views/channel_views_impl.py` — `staff_collection` scope + cache key.
- `backend/apps/orders/transport/views/order_views_impl/*.py` — gắn `require_section(...)`.
- `backend/apps/customers/views.py`, `apps/kiotviet/views.py`, `apps/facebook_ads/views.py`, `apps/audit/views.py`, `apps/cron_jobs/views.py`, `apps/users/views.py`, `apps/zalo_bridge/views.py` — gắn `require_section(...)`.

**Frontend — sửa:**
- `frontend/src/features/orders/OrderFormDialog.tsx` — bỏ lọc kênh theo dept, giữ kênh đang chọn từ `initialOrder`.
- `frontend/src/features/orders/channel-staff.ts` — filter staff theo trang chỉ áp cho admin.
- `frontend/src/pages/OrdersPage.tsx`, `frontend/src/pages/OrderDetailPage.tsx` — truyền `isAdmin` vào `buildChannelStaffByCode`; bỏ prop lọc kênh không còn dùng.
- `frontend/src/features/orders/channel-visibility.ts` + `channel-visibility.test.ts` — xoá (không còn lọc ở FE).
- `frontend/src/features/orders/channel-staff.test.ts` — cập nhật theo `isAdmin`.

---

# SPRINT 1 — Scope danh sách (Backend)

> Làm trước vì rủi ro thấp, không chặn ai; chỉ thu hẹp danh sách trả về.

## Task 1: Scope danh sách kênh theo phòng ban

**Files:**
- Modify: `backend/apps/channels_core/infrastructure/repositories/channel_repositories_impl.py` (hàm `list_for_user`, dòng ~37-66)
- Test: `backend/apps/channels_core/tests/test_channel_list_scope.py` (tạo mới)

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/channels_core/tests/test_channel_list_scope.py`:

```python
"""Scope danh sách kênh theo phòng ban (Lớp B - design 5.1)."""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, UserChannel
from apps.users.constants import DepartmentID
from apps.users.models import Department, User, UserRole


class ChannelListScopeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sales = Department.objects.create(
            id=DepartmentID.SALES, name="Sales", allowed_sections=["orders"]
        )
        self.ads = Department.objects.create(
            id=DepartmentID.ADS, name="Ads", allowed_sections=["ads-report"]
        )
        self.ch_sales = Channel.objects.create(code="ch-sales", name="Sales Channel")
        self.ch_other = Channel.objects.create(code="ch-other", name="Other Channel")
        self.ch_shared = Channel.objects.create(code="ch-shared", name="Shared Channel")
        self.ch_sales.departments.add(self.sales)
        self.ch_other.departments.add(self.ads)
        # ch_shared: không gán phòng ban nào ⇒ dùng chung.

    def _codes(self, user):
        self.client.force_authenticate(user=user)
        resp = self.client.get("/api/channels?limit=100&offset=0")
        self.assertEqual(resp.status_code, 200, resp.content)
        return {row["code"] for row in resp.json()}

    def test_admin_sees_all_channels(self):
        admin = User.objects.create_user(username="adm", password="x", role=UserRole.ADMIN)
        self.assertEqual(self._codes(admin), {"ch-sales", "ch-other", "ch-shared"})

    def test_sales_staff_sees_own_dept_and_shared(self):
        sales_staff = User.objects.create_user(
            username="sale", password="x", role=UserRole.STAFF, department=self.sales
        )
        self.assertEqual(self._codes(sales_staff), {"ch-sales", "ch-shared"})

    def test_ads_staff_sees_only_assigned(self):
        ads_staff = User.objects.create_user(
            username="ads", password="x", role=UserRole.STAFF, department=self.ads
        )
        UserChannel.objects.create(user=ads_staff, channel=self.ch_other, is_active=True)
        self.assertEqual(self._codes(ads_staff), {"ch-other"})
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd backend && python manage.py test apps.channels_core.tests.test_channel_list_scope -v 2`
Expected: FAIL ở `test_sales_staff_sees_own_dept_and_shared` (hiện trả về cả 3 kênh vì non-ADS chưa scope).

- [ ] **Step 3: Sửa `list_for_user`**

Trong `channel_repositories_impl.py`, đảm bảo có import `Q` (cùng dòng với `Prefetch`):

```python
from django.db.models import Prefetch, Q
```

Thay nhánh non-admin của `list_for_user` (đoạn từ `if user.role == UserRole.ADMIN:` đến hết hàm):

```python
        if user.role == UserRole.ADMIN:
            return queryset[offset : offset + limit]
        department_id = getattr(user, "department_id", None)
        # ADS giữ per-user qua UserChannel (không đổi).
        if department_id == DepartmentID.ADS:
            return queryset.filter(
                user_links__user=user, user_links__is_active=True
            ).distinct()[offset : offset + limit]
        # User chưa gán phòng ban: parity với hành vi cũ (thấy tất cả).
        if department_id is None:
            return queryset[offset : offset + limit]
        # Các phòng còn lại: kênh thuộc phòng ban mình + kênh dùng chung (M2M rỗng).
        return queryset.filter(
            Q(departments=user.department) | Q(departments__isnull=True)
        ).distinct()[offset : offset + limit]
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `cd backend && python manage.py test apps.channels_core.tests.test_channel_list_scope -v 2`
Expected: PASS (3 test).

- [ ] **Step 5: Chạy lại test scope đơn cũ để chắc không hồi quy**

Run: `cd backend && python manage.py test apps.orders.tests.test_order_list_role_scope_api apps.users.tests.test_department_channels -v 2`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/channels_core/infrastructure/repositories/channel_repositories_impl.py backend/apps/channels_core/tests/test_channel_list_scope.py
git commit -m "feat: scope channel list by department in backend"
```

## Task 2: Scope danh sách nhân viên theo phòng ban

**Files:**
- Modify: `backend/apps/channels_core/infrastructure/repositories/channel_repositories_impl.py` (hàm `list_staff_users`, dòng ~186-187)
- Modify: `backend/apps/channels_core/transport/views/channel_views_impl.py` (hàm `staff_collection`, dòng ~196-207)
- Test: `backend/apps/channels_core/tests/test_staff_scope.py` (tạo mới)

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/channels_core/tests/test_staff_scope.py`:

```python
"""Scope danh sách nhân viên theo phòng ban (Lớp B - design 5.2)."""
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.constants import DepartmentID
from apps.users.models import Department, User, UserRole


class StaffScopeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.sales = Department.objects.create(
            id=DepartmentID.SALES, name="Sales", allowed_sections=["orders"]
        )
        self.booking = Department.objects.create(
            id=DepartmentID.BOOKING, name="Booking", allowed_sections=["koc-orders"]
        )
        self.sale_user = User.objects.create_user(
            username="sale-1", password="x", role=UserRole.STAFF, department=self.sales
        )
        self.booking_user = User.objects.create_user(
            username="booking-1", password="x", role=UserRole.STAFF, department=self.booking
        )

    def _usernames(self, user):
        self.client.force_authenticate(user=user)
        resp = self.client.get("/api/channels/staff")
        self.assertEqual(resp.status_code, 200, resp.content)
        return {row["username"] for row in resp.json()}

    def test_admin_sees_all_staff(self):
        admin = User.objects.create_user(username="adm", password="x", role=UserRole.ADMIN)
        self.assertEqual(self._usernames(admin), {"sale-1", "booking-1"})

    def test_sales_staff_sees_only_sales(self):
        self.assertEqual(self._usernames(self.sale_user), {"sale-1"})

    def test_booking_staff_sees_only_booking(self):
        self.assertEqual(self._usernames(self.booking_user), {"booking-1"})
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd backend && python manage.py test apps.channels_core.tests.test_staff_scope -v 2`
Expected: FAIL ở `test_sales_staff_sees_only_sales` (hiện trả về tất cả staff).

- [ ] **Step 3: Thêm tham số `department` cho `list_staff_users`**

Trong `channel_repositories_impl.py`, thay hàm `list_staff_users`:

```python
    @staticmethod
    def list_staff_users(*, department=None):
        queryset = User.objects.filter(
            role=UserRole.STAFF, is_active=True, deleted_at__isnull=True
        )
        if department is not None:
            queryset = queryset.filter(department=department)
        return queryset.order_by("username")
```

- [ ] **Step 4: Scope `staff_collection` theo phòng ban + cache key**

Trong `channel_views_impl.py`, thay hàm `staff_collection`:

```python
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def staff_collection(request):
    """Danh sách nhân viên cho dropdown form đơn.

    Non-admin chỉ thấy nhân viên cùng phòng ban; admin thấy tất cả (form đơn của
    admin vẫn lọc theo trang ở FE).
    """
    user = request.user
    department = None if user.role == UserRole.ADMIN else user.department
    scope_key = "all" if department is None else f"d{department.id}"
    version = get_channels_cache_version()
    cache_key = f"channels:staff:v{version}:{scope_key}"
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return Response(cached_data)

    queryset = ChannelRepository.list_staff_users(department=department)
    data = StaffUserSerializer(queryset, many=True).data
    cache.set(cache_key, data, timeout=30)
    return Response(data)
```

- [ ] **Step 5: Chạy test để xác nhận PASS**

Run: `cd backend && python manage.py test apps.channels_core.tests.test_staff_scope -v 2`
Expected: PASS (3 test).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/channels_core/infrastructure/repositories/channel_repositories_impl.py backend/apps/channels_core/transport/views/channel_views_impl.py backend/apps/channels_core/tests/test_staff_scope.py
git commit -m "feat: scope staff list by department in backend"
```

---

# SPRINT 2 — Chặn page (Backend)

> Rủi ro cao nhất (có thể 403 oan). Tiền đề: user đã tự audit `allowed_sections` của các
> phòng ban trên prod. Mỗi task chạy lại toàn bộ test app liên quan để bắt hồi quy.

## Task 3: Permission factory `require_section`

**Files:**
- Create: `backend/apps/users/permissions.py`
- Test: `backend/apps/users/tests/test_permissions.py` (tạo mới)

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/users/tests/test_permissions.py`:

```python
from types import SimpleNamespace

from django.test import TestCase

from apps.users.models import Department, User, UserRole
from apps.users.permissions import ORDER_SECTIONS, require_section


class RequireSectionTests(TestCase):
    def _check(self, user, *sections):
        perm = require_section(*sections)()
        request = SimpleNamespace(user=user)
        return perm.has_permission(request, view=None)

    def test_admin_passes_any_section(self):
        admin = User.objects.create_user(username="adm", password="x", role=UserRole.ADMIN)
        self.assertTrue(self._check(admin, "orders"))
        self.assertTrue(self._check(admin, "audit"))

    def test_staff_passes_only_allowed_sections(self):
        dept = Department.objects.create(name="Booking", allowed_sections=["koc-orders"])
        staff = User.objects.create_user(
            username="booking", password="x", role=UserRole.STAFF, department=dept
        )
        self.assertTrue(self._check(staff, "koc-orders"))
        self.assertFalse(self._check(staff, "orders"))

    def test_union_passes_if_any_section_allowed(self):
        dept = Department.objects.create(name="Booking2", allowed_sections=["koc-orders"])
        staff = User.objects.create_user(
            username="booking2", password="x", role=UserRole.STAFF, department=dept
        )
        # Endpoint dùng chung order: chỉ cần có 1 trong các section đơn.
        self.assertTrue(self._check(staff, *ORDER_SECTIONS))

    def test_anonymous_rejected(self):
        self.assertFalse(self._check(SimpleNamespace(is_authenticated=False), "orders"))
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd backend && python manage.py test apps.users.tests.test_permissions -v 2`
Expected: FAIL với `ModuleNotFoundError: apps.users.permissions`.

- [ ] **Step 3: Tạo `backend/apps/users/permissions.py`**

```python
from rest_framework.permissions import BasePermission

# Nhóm section cho endpoint dùng chung giữa 3 trang đơn (orders / landing / koc).
ORDER_SECTIONS = ("orders", "landing-orders", "koc-orders")


def require_section(*sections: str):
    """Permission pass nếu user đăng nhập có quyền với BẤT KỲ section nào trong danh sách.

    Variadic để xử lý endpoint dùng chung. Nguồn sự thật: User.has_section_access().
    """

    class _HasSectionAccess(BasePermission):
        message = "Bạn không có quyền truy cập chức năng này."

        def has_permission(self, request, view):
            user = getattr(request, "user", None)
            if not user or not getattr(user, "is_authenticated", False):
                return False
            return any(user.has_section_access(section) for section in sections)

    return _HasSectionAccess
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `cd backend && python manage.py test apps.users.tests.test_permissions -v 2`
Expected: PASS (4 test).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/users/permissions.py backend/apps/users/tests/test_permissions.py
git commit -m "feat: add require_section permission factory"
```

## Task 4: Chặn page cho 3 trang đơn + viết test enforcement

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_list_view.py`
- Test: `backend/apps/orders/tests/test_section_enforcement_api.py` (tạo mới)

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/orders/tests/test_section_enforcement_api.py`:

```python
"""Chặn page theo allowed_sections cho 3 trang đơn (Lớp A - design 4)."""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.constants import DepartmentID
from apps.users.models import Department, User, UserRole


class OrderSectionEnforcementTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sales = Department.objects.create(
            id=DepartmentID.SALES, name="Sales",
            allowed_sections=["orders", "landing-orders"],
        )
        self.booking = Department.objects.create(
            id=DepartmentID.BOOKING, name="Booking", allowed_sections=["koc-orders"]
        )
        self.admin = User.objects.create_user(username="adm", password="x", role=UserRole.ADMIN)
        self.sale_user = User.objects.create_user(
            username="sale", password="x", role=UserRole.STAFF, department=self.sales
        )
        self.booking_user = User.objects.create_user(
            username="booking", password="x", role=UserRole.STAFF, department=self.booking
        )

    def _post(self, user, path):
        self.client.force_authenticate(user=user)
        return self.client.post(path, {"limit": "10", "offset": "0"}, format="json")

    def test_booking_blocked_from_orders_and_landing(self):
        self.assertEqual(self._post(self.booking_user, "/api/orders").status_code, 403)
        self.assertEqual(self._post(self.booking_user, "/api/orders/landing").status_code, 403)

    def test_booking_allowed_koc(self):
        self.assertEqual(self._post(self.booking_user, "/api/orders/koc").status_code, 200)

    def test_sales_blocked_from_koc(self):
        self.assertEqual(self._post(self.sale_user, "/api/orders/koc").status_code, 403)

    def test_sales_allowed_orders_and_landing(self):
        self.assertEqual(self._post(self.sale_user, "/api/orders").status_code, 200)
        self.assertEqual(self._post(self.sale_user, "/api/orders/landing").status_code, 200)

    def test_admin_allowed_everywhere(self):
        for path in ("/api/orders", "/api/orders/landing", "/api/orders/koc"):
            self.assertEqual(self._post(self.admin, path).status_code, 200, path)
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd backend && python manage.py test apps.orders.tests.test_section_enforcement_api -v 2`
Expected: FAIL — Booking nhận 200 (chưa chặn) thay vì 403.

- [ ] **Step 3: Gắn `require_section` cho 3 list view**

Trong `order_list_view.py`: thêm import và sửa decorator của 3 hàm.

Thêm vào phần import:
```python
from apps.users.permissions import require_section
```

`list_orders` (dòng ~28-30):
```python
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("orders")])
@never_cache
def list_orders(request):
```

`list_koc_orders` (dòng ~87-89):
```python
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("koc-orders")])
@never_cache
def list_koc_orders(request):
```

`list_landing_orders` (dòng ~144-146):
```python
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("landing-orders")])
@never_cache
def list_landing_orders(request):
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `cd backend && python manage.py test apps.orders.tests.test_section_enforcement_api -v 2`
Expected: PASS (5 test).

- [ ] **Step 5: Chạy toàn bộ test app orders để bắt hồi quy**

Run: `cd backend && python manage.py test apps.orders -v 1`
Expected: PASS. Nếu một test cũ authenticate bằng staff không có `allowed_sections` phù hợp → 403 ⇒ sửa fixture của test đó để gán `allowed_sections` đúng (thêm section tương ứng), KHÔNG nới lỏng permission.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/transport/views/order_views_impl/order_list_view.py backend/apps/orders/tests/test_section_enforcement_api.py
git commit -m "feat: enforce section access on order list pages"
```

## Task 5: Chặn page cho các order-flow view dùng chung + import/export theo trang

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_crud_view.py`, `order_action_view.py`, `order_export_view.py`, `order_import_view.py`, `contact_log_view.py`, `close_case_view.py`, `order_address_view.py`, `kship_note_preset_view.py`, `phone_blocklist_view.py`, `sheet3_view.py`, `order_ingest_product_dashboard_view.py`

**Quy tắc gán (theo design 4.2 + 4.3):**
- **Endpoint dùng chung** (thao tác trên một đơn / lookup khi lên đơn, bất kể trang nào): `require_section(*ORDER_SECTIONS)`.
- **Import/transfer theo trang**: dùng section riêng của trang đó.
- **products / product-categories**: `require_section("products", *ORDER_SECTIONS)` / `require_section("product-categories", *ORDER_SECTIONS)` — giữ dropdown sản phẩm trong form đơn hoạt động cho mọi phòng đơn, đồng thời vẫn chặn user không có quyền nào liên quan.

- [ ] **Step 1: Thêm import vào mỗi file sẽ sửa**

Trong mỗi file ở danh sách dưới, thêm (nếu chưa có):
```python
from apps.users.permissions import ORDER_SECTIONS, require_section
```

- [ ] **Step 2: Gắn decorator — endpoint dùng chung → `require_section(*ORDER_SECTIONS)`**

Sửa decorator `@permission_classes([IsAuthenticated])` → `@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS)])` cho các view sau (theo tên hàm):

- `order_crud_view.py`: `list_order_history`, `create_order`, `list_kiotviet_options`, `get_kship_options`, `list_kiotviet_jobs`, `get_crm_catalogs`, `create_kship_delivery`
- `order_action_view.py`: `update_order_inline`, `duplicate_order`, `bulk_delete_orders`, `restore_cancelled_orders`, `cancel_kiotviet_orders`, `run_kiotviet_action`
- `order_export_view.py`: `download_order_invoice`, `download_order_delivery_slip`, `bulk_download_invoices`, `bulk_download_delivery_slips`, `bulk_export_excel`
- `order_import_view.py`: `download_orders_import_template`, `import_orders_excel`, `import_orders_text`
- `contact_log_view.py`: `create_contact_log`
- `close_case_view.py`: `close_case`
- `order_address_view.py`: `get_kiotviet_location_data`, `parse_raw_address`
- `kship_note_preset_view.py`: `list_create_kship_note_presets`, `update_delete_kship_note_preset`
- `phone_blocklist_view.py`: `list_create_phone_blocklist`, `update_delete_phone_blocklist`
- `sheet3_view.py`: `list_sheet3_orders`

Ví dụ cho `create_order`:
```python
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS)])
def create_order(request):
```

- [ ] **Step 3: Gắn decorator — import/transfer theo trang (section riêng)**

- `order_import_view.py`:
  - `download_landing_orders_import_template`, `import_landing_orders_excel`, `import_landing_orders_text` → `require_section("landing-orders")`
  - `import_koc_orders_text` → `require_section("koc-orders")`
- `order_action_view.py`:
  - `transfer_landing_orders` → `require_section("landing-orders")`

Ví dụ:
```python
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("landing-orders")])
def transfer_landing_orders(request):
```

- [ ] **Step 4: Gắn decorator — products / product-categories**

Trong `order_ingest_product_dashboard_view.py`:
- `list_create_products`, `update_delete_product` → `require_section("products", *ORDER_SECTIONS)`
- `list_create_product_categories`, `update_delete_product_category` → `require_section("product-categories", *ORDER_SECTIONS)`

Ví dụ:
```python
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_section("products", *ORDER_SECTIONS)])
def list_create_products(request):
```

> **KHÔNG đụng** `ingest_landing_order`, `lark_ingest`, `prometheus_metrics` (AllowAny / token).

- [ ] **Step 5: Chạy toàn bộ test app orders**

Run: `cd backend && python manage.py test apps.orders -v 1`
Expected: PASS. Sửa fixture test cũ nếu bị 403 do thiếu `allowed_sections` (gán section đúng).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/transport/views/order_views_impl/
git commit -m "feat: enforce section access on shared order-flow endpoints"
```

## Task 6: Chặn page cho dashboard/ads-report + customers + các app quản trị

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py` (chuyển 2 check inline)
- Modify: `backend/apps/customers/views.py`
- Modify: `backend/apps/kiotviet/views.py`, `apps/facebook_ads/views.py`, `apps/audit/views.py`, `apps/cron_jobs/views.py`, `apps/users/views.py`, `apps/zalo_bridge/views.py`

- [ ] **Step 1: Chuyển 2 check `has_section_access` inline sang permission class**

Trong `order_ingest_product_dashboard_view.py`:
- `dashboard_summary` → `@permission_classes([IsAuthenticated, require_section("dashboard")])`, xoá đoạn `if not request.user.has_section_access("dashboard"): return 403` (dòng ~573).
- `ads_report_summary` → `@permission_classes([IsAuthenticated, require_section("ads-report")])`, xoá đoạn check inline (dòng ~623).

Trong `customers/views.py`:
- `customer_report_summary` → `@permission_classes([IsAuthenticated, require_section("dashboard")])`, xoá đoạn check inline (dòng ~358). Thêm import `from apps.users.permissions import require_section`.

- [ ] **Step 2: Gắn section cho customers (phân biệt order-flow vs quản lý)**

Trong `customers/views.py`, thêm import `from apps.users.permissions import ORDER_SECTIONS, require_section`. Gán:
- Đọc/tra cứu có thể dùng trong luồng đơn → `require_section("customers", *ORDER_SECTIONS)`: `list_create_customers`, `list_customer_profiles`, `customer_orders`, `customer_order_aggregate`
- Quản lý thuần → `require_section("customers")`: `update_delete_customer`, `bulk_delete_customers`
- Loyal → `require_section("loyal-customers")`: `list_loyal_customers`, `list_segment_categories`, `update_loyal_customer_note`
- Resale → `require_section("resale-tracking")`: `list_resale_tracking`, `update_resale_contact`, `update_resale_appointment`, `create_resale_order_note`, `update_customer_resale_note`

> **Xác minh order-flow trước khi giảm phạm vi:** chạy lệnh sau để biết FE gọi customer endpoint nào trong form đơn (tra SĐT). Nếu endpoint tra cứu khác `list_create_customers` thì thêm `*ORDER_SECTIONS` vào đúng endpoint đó:
> Run: `cd frontend && grep -rn "/api/customers" src/features/orders src/lib/api/customers.ts`

- [ ] **Step 3: Gắn section cho các app quản trị**

Thêm `from apps.users.permissions import require_section` vào mỗi file và gán:
- `kiotviet/views.py`: `kiotviet_channels_for_sync`, `sync_invoices_run` → `require_section("kiotviet")`. **KHÔNG đụng** `invoice_webhook` (AllowAny).
- `facebook_ads/views.py`: `list_create_apps`, `update_delete_app`, `sync_ad_accounts`, `ad_account_insights`, `list_app_ad_accounts`, `list_all_ad_accounts`, `sync_daily_spend`, `daily_spend_summary`, `insights_batch` → `require_section("facebook-ads")`.
- `audit/views.py`: `AuditLogViewSet` → đặt `permission_classes = [IsAuthenticated, require_section("audit")]`.
- `cron_jobs/views.py`: `cron_jobs_list`, `cron_job_update` → `require_section("cron-jobs")`.
- `users/views.py`: `admin_users_collection`, `admin_user_detail`, `department_collection`, `department_detail` → `require_section("users")`; `profile_view`, `change_password_view` → giữ `IsAuthenticated` (user tự sửa hồ sơ mình). **KHÔNG đụng** `login_view`, `token_refresh_view`, `logout_view`, `me`.
- `zalo_bridge/views.py`: `add_friend`, `account_map` → `require_section(*ORDER_SECTIONS)` (thêm import `ORDER_SECTIONS`; dùng khi xử lý đơn). **KHÔNG đụng** `bridge_webhook`, `orders_by_phone` (token/HMAC).

> **channels:** KHÔNG thêm `require_section` cho `channels_collection` GET / `staff_collection` / `my_channel_assignments` / `channel_list_for_kiotviet` (dropdown dùng chung, đã tự scope ở Sprint 1). Các mutation kênh đã được `ChannelService.can_manage` chặn — giữ nguyên.

- [ ] **Step 4: Chạy test toàn bộ backend để bắt hồi quy**

Run: `cd backend && python manage.py test -v 1`
Expected: PASS. Mỗi test cũ bị 403 do thiếu section ⇒ sửa fixture của test đó gán `allowed_sections` đúng (hoặc dùng admin nếu test vốn là hành vi admin).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/
git commit -m "feat: enforce section access across all admin and report sections"
```

---

# SPRINT 3 — Dọn dẹp Frontend

> Sau khi BE đã scope + chặn, gỡ phần lọc trùng ở FE; giữ UX "kênh đang chọn không mất".

## Task 7: Bỏ lọc kênh theo phòng ban ở form đơn, giữ kênh đang chọn

**Files:**
- Modify: `frontend/src/features/orders/OrderFormDialog.tsx`
- Delete: `frontend/src/features/orders/channel-visibility.ts`, `frontend/src/features/orders/channel-visibility.test.ts`
- Modify: `frontend/src/pages/OrdersPage.tsx`, `frontend/src/pages/OrderDetailPage.tsx` (gỡ prop `currentDepartmentId`/`isAdmin` truyền cho lọc kênh nếu không còn dùng)

- [ ] **Step 1: Sửa OrderFormDialog — dùng `channels` BE trả, đảm bảo kênh đang chọn có mặt**

Trong `OrderFormDialog.tsx`:

Xoá dòng import (dòng ~31):
```typescript
import { filterChannelsForDepartment } from "@/features/orders/channel-visibility";
```

Thay khối `visibleChannels` + `channelSelectOptions` (dòng ~656-668) bằng:
```typescript
  // BE đã scope danh sách kênh theo phòng ban (xem channel_repositories_impl.list_for_user).
  // FE chỉ đảm bảo kênh đang chọn (đơn đang sửa) luôn xuất hiện trong dropdown, kể cả khi
  // kênh đó nằm ngoài phạm vi phòng ban — lấy nhãn từ initialOrder.
  const channelSelectOptions = useMemo<OrderSelectOption[]>(() => {
    const options = channels.map((item) => ({ value: item.code, label: item.name }));
    const selected = String(form.channel_code || "");
    if (selected && !options.some((opt) => opt.value === selected)) {
      options.unshift({
        value: selected,
        label: initialOrder?.channel_name || selected,
      });
    }
    return options;
  }, [channels, form.channel_code, initialOrder]);
```

- [ ] **Step 2: Gỡ prop lọc kênh không còn dùng khỏi OrderFormDialog**

Trong `OrderFormDialog.tsx`, xoá 2 prop khỏi type props (dòng ~139-142) và khỏi tham số destructure (dòng ~419 `currentDepartmentId`, và `isAdmin`):
```typescript
  /** Phòng ban của user đang đăng nhập — lọc kênh bán hàng hiển thị trong form. */
  currentDepartmentId?: number | null;
  /** Admin thấy toàn bộ kênh, không bị lọc theo phòng ban. */
  isAdmin?: boolean;
```

> Lưu ý: `isAdmin` vẫn cần cho dropdown nhân viên (Task 8). Nếu component dùng `isAdmin`
> cho `buildChannelStaffByCode`, GIỮ `isAdmin`, chỉ xoá `currentDepartmentId`. Kiểm tra
> bằng: `grep -n "isAdmin" frontend/src/features/orders/OrderFormDialog.tsx` trước khi xoá.

- [ ] **Step 3: Xoá file channel-visibility**

```bash
git rm frontend/src/features/orders/channel-visibility.ts frontend/src/features/orders/channel-visibility.test.ts
```

- [ ] **Step 4: Cập nhật call-site OrdersPage/OrderDetailPage**

Gỡ `currentDepartmentId={...}` truyền vào `OrderFormDialog` (và `isAdmin` nếu không còn dùng cho staff) trong `OrdersPage.tsx` và `OrderDetailPage.tsx`. Tìm bằng:
```bash
cd frontend && grep -rn "currentDepartmentId" src/pages src/features/orders
```
Xoá các dòng truyền prop `currentDepartmentId` cho `OrderFormDialog`. Giữ `isAdmin` nếu Task 8 cần.

- [ ] **Step 5: Type-check + test FE**

Run: `cd frontend && npx tsc --noEmit && npx vitest run src/features/orders`
Expected: PASS, không còn tham chiếu `channel-visibility`.

> Lưu ý môi trường: máy local không có node/npm. Nếu không chạy được, để CI chạy; xác nhận
> không còn import `channel-visibility` bằng: `grep -rn "channel-visibility" frontend/src` (rỗng).

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "refactor: drop frontend channel department filter, rely on backend scope"
```

## Task 8: Dropdown nhân viên — chỉ admin mới lọc theo trang

**Files:**
- Modify: `frontend/src/features/orders/channel-staff.ts`
- Modify: `frontend/src/features/orders/channel-staff.test.ts`
- Modify: `frontend/src/pages/OrdersPage.tsx`, `frontend/src/pages/OrderDetailPage.tsx`

- [ ] **Step 1: Cập nhật test theo `isAdmin`**

Trong `channel-staff.test.ts`, thêm các case: non-admin dùng thẳng list BE trả (không lọc theo trang); admin vẫn lọc theo trang (KOC→Booking, còn lại→Sales). Ví dụ bổ sung:

```typescript
it("non-admin dùng thẳng danh sách BE trả (không lọc theo trang)", () => {
  // BE đã scope theo phòng ban; FE không lọc lại.
  const staff = [{ id: 1, username: "sale-a", department_id: 1 }];
  const map = buildChannelStaffByCode([{ code: "c1" }], staff, { isKoc: true, isAdmin: false });
  expect(map["c1"].map((s) => s.name)).toEqual(["sale-a"]);
});

it("admin vẫn lọc theo trang", () => {
  const staff = [
    { id: 1, username: "sale-a", department_id: 1 },
    { id: 2, username: "book-a", department_id: 3 },
  ];
  const map = buildChannelStaffByCode([{ code: "c1" }], staff, { isKoc: true, isAdmin: true });
  expect(map["c1"].map((s) => s.name)).toEqual(["book-a"]);
});
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd frontend && npx vitest run src/features/orders/channel-staff.test.ts`
Expected: FAIL (`isAdmin` chưa được xử lý).

- [ ] **Step 3: Cập nhật `buildChannelStaffByCode`**

Trong `channel-staff.ts`, thêm `isAdmin` vào `BuildChannelStaffOptions` và chỉ lọc theo trang khi admin:

```typescript
export type BuildChannelStaffOptions = {
  isKoc?: boolean;
  /** Admin thấy toàn bộ staff nên phải tự lọc theo trang; non-admin đã được BE scope. */
  isAdmin?: boolean;
};

export function buildChannelStaffByCode(
  channels: ChannelStaffChannel[],
  staffUsers: ChannelStaffUser[],
  { isKoc = false, isAdmin = false }: BuildChannelStaffOptions = {},
): Record<string, ChannelStaffOption[]> {
  const departmentId = staffDepartmentForPage({ isKoc });
  const scoped = isAdmin
    ? staffUsers.filter((user) => user.department_id === departmentId)
    : staffUsers;
  const staff = scoped.map((user) => ({ id: user.id, name: user.username }));

  const result: Record<string, ChannelStaffOption[]> = { "": staff };
  for (const channel of channels) {
    result[channel.code] = staff;
  }
  return result;
}
```

- [ ] **Step 4: Cập nhật call-site truyền `isAdmin`**

Trong `OrdersPage.tsx` (dòng ~1111) và `OrderDetailPage.tsx` (dòng ~438), truyền `isAdmin`:
```typescript
() => buildChannelStaffByCode(channels, staffUsers, { isKoc, isAdmin }),
```
Bảo đảm `isAdmin` có sẵn trong scope (từ `shell.user.role === "admin"`); nếu chưa, lấy từ context user. Tìm bằng:
```bash
cd frontend && grep -rn "buildChannelStaffByCode" src
```

- [ ] **Step 5: Chạy test để xác nhận PASS + type-check**

Run: `cd frontend && npx tsc --noEmit && npx vitest run src/features/orders/channel-staff.test.ts`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "refactor: staff dropdown page-filter applies to admin only"
```

---

## Hoàn tất & kiểm thử tổng

- [ ] **Backend:** `cd backend && python manage.py test -v 1` → toàn bộ PASS.
- [ ] **Frontend (CI):** `tsc --noEmit`, `vitest run`, lint → PASS (chạy ở CI vì local không có node).
- [ ] **Smoke thủ công sau deploy staging:** đăng nhập lần lượt 1 user Sales / Booking / ADS:
  - Sales: vào được Đơn hàng + Landing; dropdown kênh chỉ kênh Sales + dùng chung; dropdown nhân viên chỉ Sales; gọi thẳng `/api/orders/koc` → 403.
  - Booking: chỉ vào KOC; dropdown nhân viên chỉ Booking; gọi `/api/orders` → 403.
  - ADS: chỉ thấy đơn/kênh của mình (không đổi).
- [ ] Push branch + tạo MR.

---

## Self-review (đã chạy khi viết plan)

- **Spec coverage:** Lớp A (chặn page) → Task 3-6; Lớp B1 (kênh) → Task 1; Lớp B2 (nhân viên)
  → Task 2; FE cleanup → Task 7-8. Rủi ro `allowed_sections` → ghi chú đầu Sprint 2 + bước
  sửa fixture trong Task 4/5/6. Đủ.
- **Lookup exception:** endpoint dropdown (channels GET, staff, customer/product list dùng
  trong luồng đơn) đã xử lý bằng union `*ORDER_SECTIONS` hoặc giữ `IsAuthenticated` — Task 5/6.
- **Type consistency:** `require_section`, `ORDER_SECTIONS` dùng nhất quán; `buildChannelStaffByCode`
  thêm `isAdmin` đồng bộ ở định nghĩa + call-site (Task 8).
