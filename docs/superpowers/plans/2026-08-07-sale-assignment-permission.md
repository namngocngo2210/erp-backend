# Sale Assignment Permission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chỉ người được cấp quyền `sale-assignment:edit` mới đổi được sale phụ trách trên đơn hàng và trên khách thân thiết.

**Architecture:** Thêm một section RBAC mới `sale-assignment` với action `edit` (dùng lại action có sẵn nên không phải sửa enum `PermissionAction`). Một helper `can_assign_sale(user)` được gọi ở cả bốn đường ghi trường `staff` (PATCH đơn, POST tạo đơn, import Excel, PATCH note khách thân thiết). Frontend nhận cờ quyền qua prop và khoá các ô select tương ứng.

**Tech Stack:** Django 5 + DRF, Postgres, pytest/Django TestCase; React 19 + TypeScript + vitest + react-select.

## Global Constraints

- Backend Python 3.12, lint bắt buộc bằng `ruff check .` theo `backend/pyproject.toml` — vi phạm mới là CI đỏ.
- Message lỗi trả về client bằng tiếng Việt, khớp giọng các message 403 sẵn có trong `order_action_view.py`.
- Không dùng `cache.clear()` trong test (FLUSHDB đè Redis thật).
- Không thêm test lẻ rải rác: backend gom vào `apps/orders/tests/test_sale_assignment_permission.py` và `apps/customers/tests/test_loyal_note_permission.py`; section RBAC bổ sung vào `apps/users/tests/test_rbac_sections.py` đã có.
- Commit message tiếng Anh, ngắn, KHÔNG kèm dòng `Co-Authored-By`.
- Làm việc trong worktree `c:/Users/MYPC/Desktop/workspace/desi-order-wt-sale-assign` (branch `feat/sale-assignment-permission`).
- Contract layering (`backend/.importlinter`) xếp `apps.users` ở tầng DƯỚI `apps.orders`/`apps.customers`, nên các import mới `apps.orders → apps.users.permissions` và `apps.customers → apps.users.permissions` đều hợp lệ, KHÔNG cần thêm entry `ignore_imports`. `unmatched_ignore_imports_alerting = error` nên tuyệt đối không thêm entry thừa — entry không khớp vi phạm nào sẽ làm lint FAIL.

## Môi trường test

**Backend** — container test đã bị xoá 2026-08-05, dựng lại một lần trước Task 1:

```bash
docker network create desi-test-net 2>/dev/null || true
docker run -d --name desi-test-pg --network desi-test-net \
  -e POSTGRES_USER=desi_order -e POSTGRES_PASSWORD=desi_order -e POSTGRES_DB=desi_order \
  public.ecr.aws/docker/library/postgres:16-alpine
docker run -d --name desi-test-redis --network desi-test-net \
  public.ecr.aws/docker/library/redis:7-alpine
```

Lệnh chạy pytest dùng xuyên suốt plan (thay `<paths>`; `POSTGRES_DB` phải riêng cho mỗi lần chạy đồng thời):

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-sale-assign && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_saleassign \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
```

Lint backend:

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

**Frontend** — worktree anh em nên dùng junction, chạy một lần trước Task 6:

```powershell
New-Item -ItemType Junction -Path "c:\Users\MYPC\Desktop\workspace\desi-order-wt-sale-assign\frontend\node_modules" -Target "C:\Users\MYPC\Desktop\workspace\desi-order\frontend\node_modules"
```

Baseline đỏ sẵn trên master (KHÔNG phải do thay đổi này): `src/app/__tests__/RequireAuth.test.tsx` 2 test, `src/features/orders/__tests__/use-orders-data.realtime-upsert-filters.test.tsx` 5 test.

---

### Task 1: Đăng ký section RBAC và helper quyền

**Files:**
- Modify: `backend/apps/users/models.py:6-45` (`VALID_SECTIONS`)
- Modify: `backend/apps/users/rbac_catalog.py:15-67` (`PERMISSION_CATALOG`)
- Modify: `backend/apps/users/permissions.py`
- Create: `backend/apps/users/migrations/0033_seed_sale_assignment_permissions.py`
- Test: `backend/apps/users/tests/test_rbac_sections.py`

**Interfaces:**
- Produces: `apps.users.permissions.SALE_ASSIGNMENT_PERMISSION` (str = `"sale-assignment:edit"`) và `apps.users.permissions.can_assign_sale(user) -> bool`. Task 2-5 import đúng hai tên này.
- Produces: `PERMISSION_CATALOG["sale-assignment"] == ("edit",)` và `PERMISSION_CATALOG["loyal-customers"] == ("view", "edit", "export")`.

- [ ] **Step 1: Viết test đỏ**

Thêm vào cuối `backend/apps/users/tests/test_rbac_sections.py`:

```python
class SaleAssignmentSectionTests(TestCase):
    def test_section_registered(self):
        # Thiếu VALID_SECTIONS thì superuser bị FE ẩn (allowed_sections =
        # list(VALID_SECTIONS)) và role không gán được rule — cùng vết salaries.
        self.assertIn("sale-assignment", VALID_SECTIONS)
        self.assertIn("sale-assignment", PERMISSION_CATALOG)
        # Cố ý KHÔNG có "view": đây là quyền hành vi, không phải trang. Khai "view"
        # chỉ mời gọi người sau gate nhầm bằng require_section.
        self.assertEqual(PERMISSION_CATALOG["sale-assignment"], ("edit",))

    def test_sync_creates_permissions(self):
        sync_permissions()

        codes = set(
            Permission.objects.filter(code__startswith="sale-assignment:").values_list(
                "code", flat=True
            )
        )
        self.assertEqual(codes, {"sale-assignment:edit"})


class LoyalCustomersSectionTests(TestCase):
    def test_edit_action_registered(self):
        # Endpoint ghi note trước đây gate bằng loyal-customers:view — ai xem được là
        # ghi được. Action edit là điều kiện để siết lại (xem Task 5).
        self.assertEqual(PERMISSION_CATALOG["loyal-customers"], ("view", "edit", "export"))

    def test_sync_creates_edit_permission(self):
        sync_permissions()

        codes = set(
            Permission.objects.filter(code__startswith="loyal-customers:").values_list(
                "code", flat=True
            )
        )
        self.assertEqual(
            codes,
            {"loyal-customers:view", "loyal-customers:edit", "loyal-customers:export"},
        )
```

- [ ] **Step 2: Chạy test, xác nhận đỏ**

Chạy lệnh pytest với `<paths>` = `apps/users/tests/test_rbac_sections.py`
Kỳ vọng: FAIL — `AssertionError: 'sale-assignment' not found in [...]`

- [ ] **Step 3: Đăng ký section**

Trong `backend/apps/users/models.py`, thêm vào `VALID_SECTIONS` ngay dưới `"lead-assignment",`:

```python
    # Quyền đổi sale phụ trách (đơn hàng + khách thân thiết). Section RBAC riêng,
    # KHÔNG có route riêng — cố ý, để một chính sách công ty nằm ở một chỗ thay vì
    # rải thành 5 action trên 5 section đơn.
    "sale-assignment",
```

Trong `backend/apps/users/rbac_catalog.py`, sửa dòng `"loyal-customers"` và thêm section mới trong nhóm CRM:

```python
    "loyal-customers": ("view", "edit", "export"),
```

Thêm vào nhóm "Quản trị" (cạnh `"lead-assignment"`):

```python
    # Đổi sale phụ trách đơn / khách thân thiết. CHỈ có "edit": không phải một trang
    # nên "view" vô nghĩa và chỉ mời gọi gate nhầm bằng require_section.
    "sale-assignment": ("edit",),
```

- [ ] **Step 4: Thêm helper quyền**

Thêm vào cuối `backend/apps/users/permissions.py`:

```python
# Quyền đổi sale phụ trách. Mặc định KHÔNG role nào có (ngoài role is_system) —
# fail-closed cố ý: đây là trường quyết định ai được tính doanh số.
SALE_ASSIGNMENT_PERMISSION = "sale-assignment:edit"


def can_assign_sale(user) -> bool:
    """True nếu user được đổi sale phụ trách (đơn hàng hoặc khách thân thiết)."""
    from .auth_core import user_has_any_permission

    return user_has_any_permission(user, SALE_ASSIGNMENT_PERMISSION)
```

- [ ] **Step 5: Tạo migration seed permission**

Tạo `backend/apps/users/migrations/0033_seed_sale_assignment_permissions.py`:

```python
from django.db import migrations

# Section mới + action edit bổ sung cho loyal-customers. Seed thẳng thay vì gọi
# sync_permissions() để migration không phụ thuộc code catalog ở thời điểm chạy.
NEW_PERMISSIONS = (
    ("sale-assignment", "edit"),
    ("loyal-customers", "edit"),
)


def seed(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    for section, action in NEW_PERMISSIONS:
        code = f"{section}:{action}"
        if not Permission.objects.filter(code=code).exists():
            Permission.objects.create(
                code=code, section=section, action=action, label=f"{section} · {action}"
            )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("users", "0032_seed_warranty_codes_permissions")]
    operations = [migrations.RunPython(seed, noop)]
```

- [ ] **Step 6: Chạy test, xác nhận xanh**

Chạy lệnh pytest với `<paths>` = `apps/users/tests/test_rbac_sections.py`
Kỳ vọng: PASS toàn bộ file.

- [ ] **Step 7: Lint**

Chạy lệnh ruff. Kỳ vọng: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add backend/apps/users/models.py backend/apps/users/rbac_catalog.py backend/apps/users/permissions.py backend/apps/users/migrations/0033_seed_sale_assignment_permissions.py backend/apps/users/tests/test_rbac_sections.py
git commit -m "feat(rbac): add sale-assignment section and loyal-customers edit action"
```

---

### Task 2: Chặn đổi sale qua PATCH đơn

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_action_view.py:96-105`
- Test: `backend/apps/orders/tests/test_sale_assignment_permission.py` (tạo mới)

**Interfaces:**
- Consumes: `apps.users.permissions.can_assign_sale` (Task 1).
- Produces: file test `test_sale_assignment_permission.py` với class `SaleAssignmentOrderPatchTests`; Task 3 và 4 thêm class mới vào cùng file này.

- [ ] **Step 1: Viết test đỏ**

Tạo `backend/apps/orders/tests/test_sale_assignment_permission.py`:

```python
"""Quyền `sale-assignment:edit` gác mọi đường ghi trường `staff` của đơn.

Bốn đường: PATCH đơn, POST tạo đơn, import Excel workbook, import Excel text.
Thiếu một đường là hở toàn bộ — sale chỉ cần đổi cách thao tác.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, UserChannel
from apps.orders.models import Order, OrderStatus
from apps.users.models import User
from apps.users.test_helpers import make_user_with_permissions, make_user_with_sections


class SaleAssignmentOrderPatchTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(code="sa-patch", name="Sale Assign Patch")
        self.sale_a = make_user_with_sections("sa-patch-a", ["orders"])
        self.sale_b = User.objects.create_user(username="sa-patch-b", password="x")
        UserChannel.objects.create(user=self.sale_a, channel=self.channel, is_active=True)
        self.order = Order.objects.create(
            channel=self.channel,
            staff=self.sale_a,
            name="Khach Patch",
            phone="0909000001",
            status=OrderStatus.NEW,
        )

    def test_patch_staff_forbidden_without_permission(self):
        self.client.force_authenticate(user=self.sale_a)

        response = self.client.patch(
            f"/api/orders/{self.order.id}", {"staff_id": self.sale_b.id}, format="json"
        )

        self.assertEqual(response.status_code, 403)
        self.order.refresh_from_db()
        self.assertEqual(self.order.staff_id, self.sale_a.id)

    def test_patch_same_staff_allowed_without_permission(self):
        # Form đơn gửi lại full object; gửi đúng giá trị cũ KHÔNG được coi là đổi sale.
        self.client.force_authenticate(user=self.sale_a)

        response = self.client.patch(
            f"/api/orders/{self.order.id}",
            {"staff_id": self.sale_a.id, "name": "Khach Patch 2"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.name, "Khach Patch 2")

    def test_patch_other_fields_allowed_without_permission(self):
        self.client.force_authenticate(user=self.sale_a)

        response = self.client.patch(
            f"/api/orders/{self.order.id}", {"name": "Khach Doi Ten"}, format="json"
        )

        self.assertEqual(response.status_code, 200)

    def test_patch_staff_allowed_with_permission(self):
        manager = make_user_with_permissions(
            "sa-patch-manager",
            [
                "orders:view",
                "orders:edit",
                "orders:create",
                "orders:delete",
                "sale-assignment:edit",
            ],
        )
        UserChannel.objects.create(user=manager, channel=self.channel, is_active=True)
        self.client.force_authenticate(user=manager)

        response = self.client.patch(
            f"/api/orders/{self.order.id}", {"staff_id": self.sale_b.id}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.staff_id, self.sale_b.id)

    def test_clearing_staff_forbidden_without_permission(self):
        self.client.force_authenticate(user=self.sale_a)

        response = self.client.patch(
            f"/api/orders/{self.order.id}", {"staff_id": None}, format="json"
        )

        self.assertEqual(response.status_code, 403)
        self.order.refresh_from_db()
        self.assertEqual(self.order.staff_id, self.sale_a.id)

    def test_patch_staff_on_unassigned_order_forbidden_without_permission(self):
        # Đơn chưa có sale + chuỗi số có khoảng trắng: `" 7".isdigit()` là False
        # nhưng `int(" 7")` = 7. Gate parse lỏng hơn serializer là tự nhận đơn trống.
        self.order.staff = None
        self.order.save(update_fields=["staff"])
        self.client.force_authenticate(user=self.sale_a)

        response = self.client.patch(
            f"/api/orders/{self.order.id}", {"staff_id": f" {self.sale_a.id}"}, format="json"
        )

        self.assertEqual(response.status_code, 403)
```

`Order.customer` là FK bắt buộc (`on_delete=PROTECT`, không `null=True`), nên `setUp` phải tạo `Customer` và truyền `customer=` vào `Order.objects.create(...)` — theo đúng khuôn các test khác trong `apps/orders/tests/`.

- [ ] **Step 2: Chạy test, xác nhận đỏ**

Chạy lệnh pytest với `<paths>` = `apps/orders/tests/test_sale_assignment_permission.py`
Kỳ vọng: FAIL — 3 test đòi 403 nhưng nhận 200.

- [ ] **Step 3: Thêm gate vào view**

Trong `backend/apps/orders/transport/views/order_views_impl/order_action_view.py`, thêm import cạnh các import `apps.users` sẵn có:

```python
from apps.users.permissions import can_assign_sale
```

Chèn ngay TRƯỚC dòng `serializer = OrderInlineUpdateSerializer(` (sau khối kiểm tra đơn đã huỷ ở dòng 96-97):

```python
    # Đổi sale = đổi người ăn doanh số → quyền riêng, không đi chung orders:edit.
    # So với giá trị hiện tại vì form đơn gửi lại full object: gửi đúng sale cũ
    # không phải là "đổi" và không được chặn oan.
    #
    # Parse PHẢI khớp cách serializer ép kiểu, đừng "đơn giản hoá" về .isdigit():
    # `staff_id` là PrimaryKeyRelatedField → `queryset.get(pk=...)` → `int()`, mà
    # `int(" 7")` = 7 trong khi `" 7".isdigit()` là False. Lệch nhau thì " 7" bị
    # coi là None, bằng `order.staff_id` của đơn CHƯA có sale → gate bị bỏ qua và
    # người không có quyền tự nhận đơn trống. Parse hỏng phải fail-closed.
    if "staff_id" in request.data:
        raw_staff = request.data.get("staff_id")
        try:
            next_staff_id = int(raw_staff) if raw_staff is not None else None
        except (TypeError, ValueError):
            # Sentinel: không bao giờ bằng một staff_id thật → luôn bắt kiểm tra quyền.
            next_staff_id = object()
        if next_staff_id != order.staff_id and not can_assign_sale(request.user):
            return Response(
                {"detail": "Bạn không có quyền đổi sale phụ trách đơn."},
                status=status.HTTP_403_FORBIDDEN,
            )
```

Trả 403 chứ KHÔNG strip im lặng như `FieldPolicyWriteMixin`: người dùng vừa chủ động bấm dropdown, nuốt lặng sẽ khiến họ tưởng đã đổi xong và chỉ phát hiện khi đối soát doanh số.

- [ ] **Step 4: Chạy test, xác nhận xanh**

Chạy lệnh pytest với `<paths>` = `apps/orders/tests/test_sale_assignment_permission.py`
Kỳ vọng: PASS 5/5.

- [ ] **Step 5: Chạy test hồi quy journey (đang PATCH staff_id bằng admin)**

Chạy lệnh pytest với `<paths>` = `apps/orders/tests/test_order_journey_events.py`
Kỳ vọng: PASS — các test này dùng `make_admin` (role `is_system`, short-circuit full quyền) nên không bị chặn. Nếu đỏ, kiểm tra lại điều kiện so sánh `next_staff_id != order.staff_id` trước khi sửa test.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/transport/views/order_views_impl/order_action_view.py backend/apps/orders/tests/test_sale_assignment_permission.py
git commit -m "feat(orders): require sale-assignment permission to change order staff on patch"
```

---

### Task 3: Ép sale về chính mình khi tạo đơn

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_crud_view.py:105-114`
- Test: `backend/apps/orders/tests/test_sale_assignment_permission.py`

**Interfaces:**
- Consumes: `can_assign_sale` (Task 1); file test từ Task 2.
- Endpoint tạo đơn là `POST /api/orders/create` (KHÔNG phải `/api/orders`).

- [ ] **Step 1: Viết test đỏ**

Thêm class vào cuối `backend/apps/orders/tests/test_sale_assignment_permission.py`:

```python
class SaleAssignmentOrderCreateTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(code="sa-create", name="Sale Assign Create")
        self.sale_a = make_user_with_sections("sa-create-a", ["orders"])
        self.sale_b = User.objects.create_user(username="sa-create-b", password="x")
        UserChannel.objects.create(user=self.sale_a, channel=self.channel, is_active=True)

    def _payload(self, **overrides):
        payload = {
            "channel_code": self.channel.code,
            "name": "Khach Create",
            "phone": "0909000002",
            "raw_address": "1 Nguyen Trai, Quan 1, HCM",
            "status": OrderStatus.NEW,
            "total_amount": "100000",
        }
        payload.update(overrides)
        return payload

    def test_create_forced_to_self_when_staff_omitted(self):
        self.client.force_authenticate(user=self.sale_a)

        response = self.client.post("/api/orders/create", self._payload(), format="json")

        self.assertEqual(response.status_code, 201)
        order = Order.objects.get(id=response.data["id"])
        self.assertEqual(order.staff_id, self.sale_a.id)

    def test_create_with_self_as_staff_allowed(self):
        self.client.force_authenticate(user=self.sale_a)

        response = self.client.post(
            "/api/orders/create", self._payload(staff_id=self.sale_a.id), format="json"
        )

        self.assertEqual(response.status_code, 201)

    def test_create_for_other_staff_forbidden(self):
        self.client.force_authenticate(user=self.sale_a)

        response = self.client.post(
            "/api/orders/create", self._payload(staff_id=self.sale_b.id), format="json"
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Order.objects.filter(phone="0909000002").exists())

    def test_create_for_other_staff_allowed_with_permission(self):
        manager = make_user_with_permissions(
            "sa-create-manager",
            ["orders:view", "orders:create", "orders:edit", "sale-assignment:edit"],
        )
        UserChannel.objects.create(user=manager, channel=self.channel, is_active=True)
        self.client.force_authenticate(user=manager)

        response = self.client.post(
            "/api/orders/create", self._payload(staff_id=self.sale_b.id), format="json"
        )

        self.assertEqual(response.status_code, 201)
        order = Order.objects.get(id=response.data["id"])
        self.assertEqual(order.staff_id, self.sale_b.id)
```

- [ ] **Step 2: Chạy test, xác nhận đỏ**

Chạy lệnh pytest với `<paths>` = `apps/orders/tests/test_sale_assignment_permission.py::SaleAssignmentOrderCreateTests`
Kỳ vọng: FAIL — `test_create_forced_to_self_when_staff_omitted` (staff là None) và `test_create_for_other_staff_forbidden` (nhận 201).

- [ ] **Step 3: Thêm gate vào view**

Trong `backend/apps/orders/transport/views/order_views_impl/order_crud_view.py`, thêm import:

```python
from apps.users.permissions import can_assign_sale
```

Chèn ngay SAU khối `can_edit_channel` (sau dòng `return Response({"detail": "Bạn không có quyền tạo đơn cho kênh này."}, ...)`), trước `t1 = time.monotonic()`:

```python
    # Không có quyền gán sale → đơn luôn về tên chính mình. Chặn cứng sẽ làm sale
    # không tạo nổi đơn cho bản thân; ép về mình vẫn đóng đường chuyển đơn trá hình
    # qua bước tạo.
    if not can_assign_sale(request.user):
        requested_staff = payload.get("staff")
        if requested_staff is not None and requested_staff.id != request.user.id:
            return Response(
                {"detail": "Bạn chỉ được tạo đơn cho chính mình."},
                status=status.HTTP_403_FORBIDDEN,
            )
        payload["staff"] = request.user
```

- [ ] **Step 4: Chạy test, xác nhận xanh**

Chạy lệnh pytest với `<paths>` = `apps/orders/tests/test_sale_assignment_permission.py`
Kỳ vọng: PASS 9/9.

- [ ] **Step 5: Chạy test hồi quy tạo đơn**

Chạy lệnh pytest với `<paths>` = `apps/orders/tests/test_order_create_api.py`
Kỳ vọng: PASS — các test dùng `make_admin` nên không bị ép.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/transport/views/order_views_impl/order_crud_view.py backend/apps/orders/tests/test_sale_assignment_permission.py
git commit -m "feat(orders): force order staff to self on create without sale-assignment permission"
```

---

### Task 4: Bỏ qua cột Nhân viên khi import không có quyền

**Files:**
- Modify: `backend/apps/orders/import_excel.py:174-176` và `:789`
- Modify: `backend/apps/orders/import_landing_excel.py:306-307` và `:396-397`
- Test: `backend/apps/orders/tests/test_sale_assignment_permission.py`

**Interfaces:**
- Consumes: `can_assign_sale` (Task 1).
- Produces: key mới `"warnings": list[str]` trong dict trả về của cả bốn hàm import. FE hiện chỉ đọc `total_rows`/`success_count`/`failed_count`/`failures` nên thêm key là tương thích ngược.

- [ ] **Step 1: Viết test đỏ**

Thêm class vào cuối `backend/apps/orders/tests/test_sale_assignment_permission.py` và bổ sung import ở đầu file:

```python
from apps.orders.import_excel import import_orders_from_text
```

```python
class SaleAssignmentImportTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="sa-import", name="Sale Assign Import")
        self.sale_a = make_user_with_sections("sa-import-a", ["orders"])
        self.sale_b = User.objects.create_user(username="sa-import-b", password="x")
        UserChannel.objects.create(user=self.sale_a, channel=self.channel, is_active=True)

    def _row(self, staff_username: str) -> str:
        # Thứ tự cột theo docstring import_orders_from_text:
        # kiotviet_channel_id|staff_username|channel_code|name|customer_gender|phone|
        # raw_address|sku_list|quantity_list|price_list|discount_amount|
        # booking_voucher_discount|weight|dimensions|branch_id|order_type|customer_prepaid
        return (
            f"|{staff_username}|{self.channel.code}|Khach Import||0909000003|"
            "1 Nguyen Trai, Quan 1, HCM|||||||||"
        )

    def test_import_ignores_staff_column_without_permission(self):
        result = import_orders_from_text(
            text_data=self._row(self.sale_b.username), actor=self.sale_a
        )

        self.assertEqual(result["success_count"], 1)
        order = Order.objects.get(phone="0909000003")
        self.assertEqual(order.staff_id, self.sale_a.id)

    def test_import_reports_warning_when_staff_column_ignored(self):
        result = import_orders_from_text(
            text_data=self._row(self.sale_b.username), actor=self.sale_a
        )

        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("Nhân viên", result["warnings"][0])

    def test_import_no_warning_when_column_empty(self):
        result = import_orders_from_text(text_data=self._row(""), actor=self.sale_a)

        self.assertEqual(result["warnings"], [])

    def test_import_honours_staff_column_with_permission(self):
        manager = make_user_with_permissions(
            "sa-import-manager",
            ["orders:view", "orders:create", "orders:edit", "sale-assignment:edit"],
        )
        UserChannel.objects.create(user=manager, channel=self.channel, is_active=True)

        result = import_orders_from_text(
            text_data=self._row(self.sale_b.username), actor=manager
        )

        self.assertEqual(result["success_count"], 1)
        order = Order.objects.get(phone="0909000003")
        self.assertEqual(order.staff_id, self.sale_b.id)
        self.assertEqual(result["warnings"], [])
```

- [ ] **Step 2: Chạy test, xác nhận đỏ**

Chạy lệnh pytest với `<paths>` = `apps/orders/tests/test_sale_assignment_permission.py::SaleAssignmentImportTests`
Kỳ vọng: FAIL — `KeyError: 'warnings'` và staff bằng `sale_b` thay vì `sale_a`.

- [ ] **Step 3: Thêm helper dùng chung trong import_excel.py**

Thêm import và helper vào `backend/apps/orders/import_excel.py` (đặt ngay sau khối `logger = logging.getLogger(__name__)`):

```python
from apps.users.permissions import can_assign_sale

STAFF_COLUMN_IGNORED_WARNING = (
    "Cột Nhân viên bị bỏ qua: bạn không có quyền gán sale — "
    "toàn bộ đơn trong file đã gán cho bạn."
)


def resolve_import_staff(validated: dict, actor) -> bool:
    """Chốt `validated["staff"]` theo quyền của người import.

    Trả True nếu cột Nhân viên trong file bị bỏ qua (để caller gom cảnh báo).
    Không có quyền mà im lặng gán sai chủ đơn thì import 500 dòng xong mới phát
    hiện, mất cả buổi đối soát.
    """
    requested = validated.get("staff")
    if can_assign_sale(actor):
        if requested is None:
            validated["staff"] = actor
        return False
    ignored = requested is not None and requested.id != getattr(actor, "id", None)
    validated["staff"] = actor
    return ignored
```

- [ ] **Step 4: Dùng helper ở cả bốn call-site**

Trong `backend/apps/orders/import_excel.py`, thay khối 3 dòng ở `import_orders_from_workbook` (hiện là dòng 174-176):

```python
        # Use staff from import data if provided, otherwise fall back to the operator.
        if "staff" not in validated or validated["staff"] is None:
            validated["staff"] = actor
```

bằng:

```python
        if resolve_import_staff(validated, actor):
            staff_column_ignored = True
```

Khai báo `staff_column_ignored = False` cạnh `failures: list[ImportedOrderFailure] = []`, và đổi dict trả về (dòng 205-210) thành:

```python
    return {
        "total_rows": total_rows,
        "success_count": success_count,
        "failed_count": len(failures),
        "failures": [failure.to_payload() for failure in failures],
        "warnings": [STAFF_COLUMN_IGNORED_WARNING] if staff_column_ignored else [],
    }
```

Làm y hệt cho `import_orders_from_text` trong cùng file (khối tương ứng ở dòng 789 và dict trả về của hàm đó), kể cả nhánh trả về sớm khi file rỗng — nhánh đó trả `"warnings": []`.

- [ ] **Step 5: Áp cùng thay đổi cho import landing**

Trong `backend/apps/orders/import_landing_excel.py`, thêm import:

```python
from .import_excel import STAFF_COLUMN_IGNORED_WARNING, resolve_import_staff
```

Thay hai khối `if "staff" not in validated or validated["staff"] is None:` (dòng 306-307 và 396-397) bằng `if resolve_import_staff(validated, actor): staff_column_ignored = True`, khai báo cờ và thêm `"warnings"` vào mọi dict trả về của `import_landing_orders_from_workbook` và `import_landing_orders_from_text` đúng như Step 4.

- [ ] **Step 6: Chạy test, xác nhận xanh**

Chạy lệnh pytest với `<paths>` = `apps/orders/tests/test_sale_assignment_permission.py`
Kỳ vọng: PASS 13/13.

- [ ] **Step 7: Chạy test hồi quy import**

Chạy lệnh pytest với `<paths>` = `apps/orders/tests/ -k "import"`
Kỳ vọng: PASS.

- [ ] **Step 8: Lint và commit**

```bash
git add backend/apps/orders/import_excel.py backend/apps/orders/import_landing_excel.py backend/apps/orders/tests/test_sale_assignment_permission.py
git commit -m "feat(orders): ignore staff column on excel import without sale-assignment permission"
```

---

### Task 5: Siết ghi note khách thân thiết

**Files:**
- Modify: `backend/apps/customers/transport/views/customers_views_impl/loyalty_view.py:95-107`
- Test: `backend/apps/customers/tests/test_loyal_note_permission.py` (tạo mới)

**Interfaces:**
- Consumes: `can_assign_sale` và permission code `loyal-customers:edit` (Task 1).
- Endpoint: `PATCH /api/customers/loyal/<customer_id>/note`.

- [ ] **Step 1: Viết test đỏ**

Tạo `backend/apps/customers/tests/test_loyal_note_permission.py`:

```python
"""Quyền trên endpoint ghi note khách thân thiết.

Trước đây endpoint gate bằng `loyal-customers:view` — ai xem được là ghi được toàn
bộ ghi chú, kể cả gán sale phụ trách. Nay: ghi cần `loyal-customers:edit`, riêng
trường `sale_staff_id` cần thêm `sale-assignment:edit`.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.customers.models import Customer, LoyalCustomerNote
from apps.users.models import User
from apps.users.test_helpers import make_user_with_permissions


class LoyalNotePermissionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.customer = Customer.objects.create(full_name="Khach Than Thiet", phone="0909000004")
        self.sale = User.objects.create_user(username="loyal-sale", password="x")

    def _url(self) -> str:
        return f"/api/customers/loyal/{self.customer.id}/note"

    def test_viewer_cannot_write_note(self):
        viewer = make_user_with_permissions("loyal-viewer", ["loyal-customers:view"])
        self.client.force_authenticate(user=viewer)

        response = self.client.patch(self._url(), {"special_note": "abc"}, format="json")

        self.assertEqual(response.status_code, 403)

    def test_editor_can_write_note_without_sale_field(self):
        editor = make_user_with_permissions(
            "loyal-editor", ["loyal-customers:view", "loyal-customers:edit"]
        )
        self.client.force_authenticate(user=editor)

        response = self.client.patch(self._url(), {"special_note": "abc"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["special_note"], "abc")

    def test_editor_cannot_assign_sale(self):
        editor = make_user_with_permissions(
            "loyal-editor-2", ["loyal-customers:view", "loyal-customers:edit"]
        )
        self.client.force_authenticate(user=editor)

        response = self.client.patch(
            self._url(), {"sale_staff_id": self.sale.id}, format="json"
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(LoyalCustomerNote.objects.filter(customer=self.customer).exists())

    def test_manager_can_assign_sale(self):
        manager = make_user_with_permissions(
            "loyal-manager",
            ["loyal-customers:view", "loyal-customers:edit", "sale-assignment:edit"],
        )
        self.client.force_authenticate(user=manager)

        response = self.client.patch(
            self._url(), {"sale_staff_id": self.sale.id}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["sale_staff_id"], self.sale.id)
```

- [ ] **Step 2: Chạy test, xác nhận đỏ**

Chạy lệnh pytest với `<paths>` = `apps/customers/tests/test_loyal_note_permission.py`
Kỳ vọng: FAIL — `test_viewer_cannot_write_note` và `test_editor_cannot_assign_sale` nhận 200.

- [ ] **Step 3: Đổi gate của endpoint**

Trong `backend/apps/customers/transport/views/customers_views_impl/loyalty_view.py`, sửa import quyền để có `require_permission` và `can_assign_sale`:

```python
from apps.users.permissions import can_assign_sale, require_permission, require_section
```

Đổi decorator của `update_loyal_customer_note` từ:

```python
@permission_classes([IsAuthenticated, require_section("loyal-customers")])
```

thành:

```python
# Ghi note là hành vi SỬA, không phải xem. Gate cũ dùng require_section (= :view)
# nên ai xem được là ghi được toàn bộ ghi chú khách (audit 2026-08-07).
@permission_classes([IsAuthenticated, require_permission("loyal-customers:edit")])
```

- [ ] **Step 4: Chặn riêng trường sale**

Chèn ngay SAU `serializer.is_valid(raise_exception=True)` và TRƯỚC dòng `has_sale_staff = ...`:

```python
    if "sale_staff_id" in serializer.validated_data and not can_assign_sale(request.user):
        return Response(
            {"detail": "Bạn không có quyền gán sale phụ trách khách hàng."},
            status=status.HTTP_403_FORBIDDEN,
        )
```

- [ ] **Step 5: Chạy test, xác nhận xanh**

Chạy lệnh pytest với `<paths>` = `apps/customers/tests/test_loyal_note_permission.py`
Kỳ vọng: PASS 4/4.

- [ ] **Step 6: Chạy test hồi quy customers**

Chạy lệnh pytest với `<paths>` = `apps/customers/tests/`
Kỳ vọng: PASS. Test nào dựng user bằng `make_user_with_sections(..., ["loyal-customers"])` vẫn xanh vì helper gán MỌI action của section (nay gồm cả `edit`).

- [ ] **Step 7: Lint và commit**

```bash
git add backend/apps/customers/transport/views/customers_views_impl/loyalty_view.py backend/apps/customers/tests/test_loyal_note_permission.py
git commit -m "feat(customers): require edit and sale-assignment permissions on loyal note write"
```

---

### Task 6: Khoá ô sale trên trang đơn (frontend)

**Files:**
- Modify: `frontend/src/features/orders/OrdersTable/OrderCell.tsx:44-66` (props) và `:281-288` (case `staff`)
- Modify: `frontend/src/features/orders/OrdersTable/OrderTableRow.tsx:145-156`
- Modify: `frontend/src/features/orders/OrdersTable/OrdersTable.tsx:651`
- Modify: `frontend/src/features/orders/OrderFormDialog.tsx:626-648`
- Modify: `frontend/src/pages/OrdersPage.tsx:998`
- Test: `frontend/src/features/orders/__tests__/sale-assignment-gate.test.tsx` (tạo mới)

**Interfaces:**
- Consumes: permission code `"sale-assignment:edit"` (Task 1), có sẵn trong `user.permissions` do API `/api/auth/me` trả về.
- Produces: prop `canAssignSale?: boolean` (default `false`) trên `OrderCell`, `OrderTableRow`, `OrdersTable`, `OrderFormDialog`.

**Lưu ý bắt buộc:** KHÔNG gọi `usePermission()` bên trong `OrderCell` hay `OrderFormDialog`. `useAppShell` throw khi không có Provider, mà `OrderFormDialog.reprice.test.tsx` render dialog không kèm Provider — gọi hook trong đó sẽ làm đỏ test hiện có. Truyền qua prop đúng như prop `canCall` đã làm.

- [ ] **Step 1: Tạo junction node_modules (một lần)**

```powershell
New-Item -ItemType Junction -Path "c:\Users\MYPC\Desktop\workspace\desi-order-wt-sale-assign\frontend\node_modules" -Target "C:\Users\MYPC\Desktop\workspace\desi-order\frontend\node_modules"
```

- [ ] **Step 2: Viết test đỏ**

Tạo `frontend/src/features/orders/__tests__/sale-assignment-gate.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { OrderCell } from "@/features/orders/OrdersTable/OrderCell";
import type { OrderRow } from "@/features/orders/order-types";
import { I18nProvider } from "@/i18n";

afterEach(cleanup);

const ROW = {
  id: 1,
  can_edit: true,
  channel_code: "fb",
  staff_id: 7,
  staff_name: "Sale A",
} as unknown as OrderRow;

const BASE_PROPS = {
  row: ROW,
  columnKey: "staff" as const,
  kiotvietOptions: [],
  addressCatalog: { provinces: [], districts_by_province: {}, wards_by_district_key: {} },
  channelStaffByCode: { fb: [{ id: 7, name: "Sale A" }, { id: 8, name: "Sale B" }] },
  onPatch: vi.fn().mockResolvedValue(undefined),
};

describe("OrderCell cột sale phụ trách", () => {
  it("hiện dropdown khi có quyền gán sale", () => {
    render(
      <I18nProvider>
        <OrderCell {...BASE_PROPS} canAssignSale />
      </I18nProvider>,
    );

    expect(screen.getByRole("combobox")).toBeTruthy();
  });

  it("chỉ hiện text khi không có quyền gán sale", () => {
    render(
      <I18nProvider>
        <OrderCell {...BASE_PROPS} canAssignSale={false} />
      </I18nProvider>,
    );

    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getByText("Sale A")).toBeTruthy();
  });
});
```

- [ ] **Step 3: Chạy test, xác nhận đỏ**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-sale-assign/frontend && npx vitest run src/features/orders/__tests__/sale-assignment-gate.test.tsx
```

Kỳ vọng: FAIL ở test thứ hai — dropdown vẫn render vì prop chưa được dùng.

- [ ] **Step 4: Thêm prop vào OrderCell**

Trong `frontend/src/features/orders/OrdersTable/OrderCell.tsx`, thêm vào destructuring (sau `canCall = false,`):

```tsx
  canAssignSale = false,
```

và vào type props (sau dòng khai báo `canCall`):

```tsx
  /** Quyền `sale-assignment:edit` — thiếu thì cột sale chỉ đọc. */
  canAssignSale?: boolean;
```

Đổi dòng 282 trong `case "staff"`:

```tsx
      const canEdit = !!row.can_edit && !!onPatch && canAssignSale;
```

- [ ] **Step 5: Chạy test, xác nhận xanh**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-sale-assign/frontend && npx vitest run src/features/orders/__tests__/sale-assignment-gate.test.tsx
```

Kỳ vọng: PASS 2/2.

- [ ] **Step 6: Nối prop qua OrderTableRow và OrdersTable**

Trong `OrderTableRow.tsx`, thêm `canAssignSale` vào props (cùng chỗ với `canCall`) và truyền xuống `<OrderCell ... canAssignSale={canAssignSale} />`.

Trong `OrdersTable.tsx`, thêm ở đầu component:

```tsx
const can = usePermission();
const canAssignSale = can("sale-assignment:edit");
```

kèm `import { usePermission } from "@/hooks/usePermission";`, rồi truyền `canAssignSale={canAssignSale}` vào `<OrderTableRow>` cạnh `canCall={isLandingPage}` (dòng 651).

- [ ] **Step 7: Khoá ô sale trong form đơn**

Trong `OrderFormDialog.tsx`, thêm `canAssignSale = false` vào props và type props (mô tả giống Step 4). Đổi `isDisabled` của ReactSelect chọn nhân viên (dòng 632):

```tsx
                    isDisabled={!form.channel_code || !canAssignSale}
```

Thêm effect preset chính mình cho đơn TẠO MỚI, đặt cạnh các effect khởi tạo form sẵn có:

```tsx
  // Không có quyền gán sale → đơn mới luôn đứng tên chính mình, khớp với BE
  // (order_crud_view ép staff = request.user). Chỉ áp cho đơn tạo mới: đơn đang
  // sửa phải giữ nguyên sale hiện tại, không được âm thầm kéo về người đang mở form.
  useEffect(() => {
    if (!open || canAssignSale || initialOrder) return;
    setForm((prev) => (prev.staff_id === currentUserId ? prev : { ...prev, staff_id: currentUserId }));
  }, [open, canAssignSale, initialOrder, currentUserId]);
```

Thêm prop `currentUserId?: number | null` (default `null`) cùng lúc — dialog không được tự đọc AppShell (xem Lưu ý bắt buộc ở đầu task).

Trong `OrdersPage.tsx` dòng 998, truyền cả hai prop:

```tsx
        canAssignSale={can("sale-assignment:edit")}
        currentUserId={user?.id ?? null}
```

`OrdersPage` đã có `useAppShell`; nếu chưa có `can`, thêm `const can = usePermission();`.

- [ ] **Step 8: Kiểm tra type và test hồi quy orders**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-sale-assign/frontend && npx tsc --noEmit && npx vitest run src/features/orders
```

Kỳ vọng: tsc sạch; vitest chỉ còn 5 test đỏ baseline trong `use-orders-data.realtime-upsert-filters.test.tsx`.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/features/orders frontend/src/pages/OrdersPage.tsx
git commit -m "feat(orders): gate order staff select behind sale-assignment permission"
```

---

### Task 7: Khoá ô sale khách thân thiết và hiện quyền mới trên trang Phân quyền

**Files:**
- Modify: `frontend/src/pages/LoyalCustomersPage.tsx:758-766`
- Modify: `frontend/src/features/orders/use-orders-import.ts:96-137`
- Modify: `frontend/src/pages/RolesPage.tsx:67-99` (`SECTION_LABELS`) và `:102-124` (`MODULE_GROUPS`)

**Interfaces:**
- Consumes: permission code `"sale-assignment:edit"` (Task 1) và `usePermission` hook.

- [ ] **Step 1: Khoá ô chọn sale trên trang khách thân thiết**

Trong `LoyalCustomersPage.tsx`, thêm ở đầu component:

```tsx
const can = usePermission();
const canAssignSale = can("sale-assignment:edit");
```

kèm `import { usePermission } from "@/hooks/usePermission";`. Thêm `disabled={!canAssignSale}` vào `<Select>` chọn sale phụ trách (dòng 758) — shadcn Select nhận prop `disabled` và tự khoá trigger.

- [ ] **Step 1b: Hiện cảnh báo import lên UI**

Task 4 tính ra `result.warnings` và trả trong body HTTP, nhưng chưa có ai đọc — người bị chặn vẫn thấy toast "Import thành công" và tưởng cột Nhân viên đã có tác dụng, đúng cái mà thiết kế muốn tránh. Bịt nốt ở `frontend/src/features/orders/use-orders-import.ts`:

Thêm `warnings?: string[];` vào type inline của `result` trong `handleSubmitImport` (cạnh `failures: ImportFailure[];`), rồi ngay sau `setImportFailures(...)` thêm:

```ts
      // Cảnh báo cấp file (vd cột Nhân viên bị bỏ vì thiếu quyền gán sale). Toast
      // riêng, KHÔNG gộp vào toast tổng kết bên dưới: gộp vào thì nó biến mất sau
      // "Import thành công" và người dùng vẫn tưởng cột đã có tác dụng.
      for (const warning of Array.isArray(result.warnings) ? result.warnings : []) {
        toast.warning(warning);
      }
```

`use-orders-import.ts` là nơi DUY NHẤT ở FE gọi các endpoint import (`import-text`, `import-koc-text`, `import-landing-text`) — hai endpoint `import-excel`/`import-landing-excel` có trong schema nhưng không có call-site FE, nên không cần đụng.

- [ ] **Step 2: Thêm nhãn tiếng Việt cho section mới**

Trong `RolesPage.tsx`, thêm vào `SECTION_LABELS`:

```tsx
  "sale-assignment": "Gán sale phụ trách",
```

và thêm `"sale-assignment"` vào `sections` của nhóm `{ label: "Quản trị", ... }` (dòng 123), sau `"lead-assignment"`.

- [ ] **Step 3: Kiểm tra type và lint**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-sale-assign/frontend && npx tsc --noEmit && npm run lint:strict
```

Kỳ vọng: sạch cả hai.

- [ ] **Step 4: Chạy toàn bộ test frontend**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-sale-assign/frontend && npm run test:ci
```

Kỳ vọng: đúng baseline đỏ đã biết (2 file / 7 test: `RequireAuth.test.tsx` + `use-orders-data.realtime-upsert-filters.test.tsx`), không có file đỏ mới.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/LoyalCustomersPage.tsx frontend/src/pages/RolesPage.tsx
git commit -m "feat(rbac): show sale assignment permission and lock loyal customer sale select"
```

---

### Task 8: Chạy suite backend liên quan và cập nhật OpenAPI schema

**Files:**
- Modify: `backend/schema.yaml` (sinh lại, không sửa tay)
- Modify: `frontend/src/api/schema.d.ts` (sinh lại, không sửa tay)

**Interfaces:**
- Consumes: toàn bộ thay đổi từ Task 1-7.

- [ ] **Step 1: Chạy các app đã chạm**

Chạy lệnh pytest với `<paths>` = `apps/users/ apps/orders/ apps/customers/` và `POSTGRES_DB=desi_saleassign_full`.
Kỳ vọng: PASS. Nếu có test đỏ, kiểm chứng nó có đỏ sẵn trên master trước khi sửa code.

- [ ] **Step 2: Kiểm tra drift schema OpenAPI**

Thay đổi trong plan này KHÔNG sửa serializer nào, nên nhiều khả năng schema không đổi. Vẫn phải kiểm chứng thay vì đoán:

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-sale-assign && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_saleassign_full \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  python manage.py spectacular --file schema.yaml
git diff --stat backend/schema.yaml
```

Nếu `git diff` rỗng: bỏ qua Step 4, không commit gì. Nếu có diff, sinh lại type FE rồi commit cả hai file:

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-sale-assign/frontend && npm run gen:api
```

- [ ] **Step 3: Lint backend lần cuối**

Chạy lệnh ruff. Kỳ vọng: `All checks passed!`

- [ ] **Step 4: Commit (nếu có thay đổi schema)**

```bash
git add backend/schema.yaml frontend/src/api/schema.d.ts
git commit -m "chore: regenerate openapi schema for sale assignment permission"
```

- [ ] **Step 5: Push và tạo MR**

```bash
git push -o merge_request.create -o merge_request.target=master -o merge_request.title="Phân quyền gán sale phụ trách" -u origin feat/sale-assignment-permission
```

Lấy link MR từ output và báo lại cho người dùng.

---

## Sau khi merge — việc vận hành BẮT BUỘC

Deploy xong thì không role nào có `sale-assignment:edit` (ngoài role `is_system`), và role đang ghi note khách thân thiết sẽ mất quyền cho tới khi được cấp `loyal-customers:edit`. Vào trang Phân quyền cấp hai quyền này cho các role quản lý **ngay trong cùng cửa sổ deploy**. Mọi đường ghi role đã gọi `bump_rbac_cache_version()` nên quyền có hiệu lực ngay, không phải chờ hết TTL 30 phút.
