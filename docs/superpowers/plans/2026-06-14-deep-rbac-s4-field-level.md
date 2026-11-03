# Deep RBAC — Sprint 4: Field-level enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Phụ thuộc:** S1 (model `RoleFieldRule` + resolver `field_policy`), S2 (/me trả `field_policy`, trang Role), S3 (scope).

**Goal:** Áp **field-level** cho field nhạy cảm (SĐT `phone`/`customer_phone`, và các field giá vốn/lợi nhuận khi có): output drop field `hidden`; write strip field `hidden|readonly` (mặc định bỏ qua + log, không 403 cứng). Tính `field_policy` **1 lần/request** (resolver có cache), bơm vào serializer context — **không** dùng `SerializerMethodField` per-field (tránh N+1). Thêm UI cấu hình field rule trong trang Role.

**Architecture:** Helper thuần + mixin DRF trong `apps/users/field_policy.py`: `drop_hidden_fields(data, section_policy)` cho output, `strip_protected_write_fields(data, section_policy)` cho write. `FieldPolicyOutputMixin.to_representation` đọc context `field_policy` + `field_section`. View bơm `field_policy` (đã có từ resolver) vào context cùng chỗ build `OrderSerializerContext`. BE là nguồn enforcement; FE chỉ ẩn cột (đọc `field_policy` từ `/me`).

**Tech Stack:** Django 5 / DRF, React SPA + TS.

**Spec:** mục 5.2 (chốt 3 field-level), 4 (field rule ít hạn chế nhất thắng), 6 (FE field-level).

---

## File Structure

**Backend (`backend/`):**
- `apps/users/field_policy.py` — CREATE: helpers + `FieldPolicyOutputMixin`, `FieldPolicyWriteMixin`.
- `apps/users/tests/test_field_policy.py` — CREATE.
- `apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py` — MODIFY: `OrderSerializer` kế thừa `FieldPolicyOutputMixin`, khai báo `field_section = "orders"`.
- `apps/orders/transport/views/order_views_impl/_view_helpers.py` — MODIFY: `_build_order_list_context` bơm `field_policy` + `field_section` vào context.
- `apps/orders/transport/views/order_views_impl/order_crud_view.py` — MODIFY: nơi serialize đơn lẻ (detail) cũng bơm context field_policy.
- `apps/orders/tests/test_order_field_policy.py` — CREATE.
- `apps/users/serializers.py`, `apps/users/views.py`, `apps/users/services.py` — MODIFY: API set/get `RoleFieldRule`.
- `apps/users/tests/test_role_field_rule_api.py` — CREATE.

**Frontend (`frontend/`):**
- `src/lib/api/roles.ts` — MODIFY: type + API `field_rules`.
- `src/pages/RolesPage.tsx` — MODIFY: khối cấu hình field rule (section + field + hidden/readonly).
- `src/hooks/useFieldPolicy.ts` — CREATE: hook đọc `field_policy` từ user (ẩn cột/disable input).
- `src/hooks/__tests__/useFieldPolicy.test.tsx` — CREATE.

## Cách chạy test
Như S1/S2.

---

## Task 1: Field-policy helpers + mixins

**Files:**
- Create: `backend/apps/users/field_policy.py`
- Test: `backend/apps/users/tests/test_field_policy.py`

- [ ] **Step 1: Viết test thất bại**

Create `backend/apps/users/tests/test_field_policy.py`:

```python
from django.test import SimpleTestCase

from apps.users.field_policy import drop_hidden_fields, strip_protected_write_fields


class DropHiddenFieldsTests(SimpleTestCase):
    def test_drops_hidden_keys(self):
        data = {"id": 1, "phone": "0900", "name": "A"}
        out = drop_hidden_fields(data, {"phone": "hidden"})
        self.assertNotIn("phone", out)
        self.assertEqual(out["name"], "A")

    def test_readonly_is_kept_in_output(self):
        data = {"phone": "0900"}
        out = drop_hidden_fields(data, {"phone": "readonly"})
        self.assertIn("phone", out)

    def test_no_policy_is_passthrough(self):
        data = {"phone": "0900"}
        self.assertEqual(drop_hidden_fields(data, {}), data)


class StripProtectedWriteTests(SimpleTestCase):
    def test_strips_hidden_and_readonly_on_write(self):
        data = {"phone": "0900", "name": "A", "note": "x"}
        out, stripped = strip_protected_write_fields(
            data, {"phone": "hidden", "note": "readonly"}
        )
        self.assertEqual(set(out.keys()), {"name"})
        self.assertEqual(set(stripped), {"phone", "note"})

    def test_no_policy_passthrough(self):
        data = {"phone": "0900"}
        out, stripped = strip_protected_write_fields(data, {})
        self.assertEqual(out, data)
        self.assertEqual(stripped, [])
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest apps/users/tests/test_field_policy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.users.field_policy'`.

- [ ] **Step 3: Implement**

Create `backend/apps/users/field_policy.py`:

```python
"""Field-level enforcement (drop hidden output / strip protected write).

Dùng field_policy (dict[section→dict[field→rule]]) tính 1 lần/request bởi resolver,
bơm vào serializer context. Không tạo SerializerMethodField per-field (tránh N+1).
"""

import logging

logger = logging.getLogger(__name__)

HIDDEN = "hidden"
READONLY = "readonly"


def drop_hidden_fields(data: dict, section_policy: dict) -> dict:
    """Bỏ field rule=hidden khỏi output. readonly vẫn hiển thị (chỉ chặn write)."""
    if not section_policy:
        return data
    hidden = {field for field, rule in section_policy.items() if rule == HIDDEN}
    if not hidden:
        return data
    return {k: v for k, v in data.items() if k not in hidden}


def strip_protected_write_fields(data: dict, section_policy: dict) -> tuple[dict, list]:
    """Loại field hidden|readonly khỏi payload ghi. Trả (data_sạch, danh_sách_bị_loại)."""
    if not section_policy:
        return data, []
    protected = {
        field for field, rule in section_policy.items() if rule in (HIDDEN, READONLY)
    }
    stripped = [k for k in data if k in protected]
    if not stripped:
        return data, []
    cleaned = {k: v for k, v in data.items() if k not in protected}
    return cleaned, stripped


class FieldPolicyOutputMixin:
    """Mixin cho ModelSerializer: drop field hidden ở output.

    Serializer phải khai báo `field_section = "<section>"`. View truyền context
    `field_policy={"<section>": {...}}`.
    """

    field_section: str = ""

    def to_representation(self, instance):
        data = super().to_representation(instance)
        policy = (self.context.get("field_policy") or {}).get(self.field_section, {})
        return drop_hidden_fields(data, policy)


class FieldPolicyWriteMixin:
    """Mixin cho serializer ghi: strip field protected trước khi validate.

    Mặc định strip + log (không 403). Serializer khai báo `field_section`.
    """

    field_section: str = ""

    def to_internal_value(self, data):
        policy = (self.context.get("field_policy") or {}).get(self.field_section, {})
        cleaned, stripped = strip_protected_write_fields(dict(data), policy)
        if stripped:
            logger.info(
                "field_policy stripped write fields section=%s fields=%s",
                self.field_section,
                stripped,
            )
        return super().to_internal_value(cleaned)
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `python -m pytest apps/users/tests/test_field_policy.py -q`
Expected: PASS (5 test).

- [ ] **Step 5: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/users/field_policy.py backend/apps/users/tests/test_field_policy.py
git add backend/apps/users/field_policy.py backend/apps/users/tests/test_field_policy.py
git commit -m "feat(rbac): field-policy helpers + DRF output/write mixins"
```

---

## Task 2: Áp field-policy cho OrderSerializer (pilot: ẩn phone)

**Files:**
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py`
- Modify: `backend/apps/orders/transport/views/order_views_impl/_view_helpers.py`
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_crud_view.py`
- Test: `backend/apps/orders/tests/test_order_field_policy.py`

- [ ] **Step 1: Viết test thất bại**

Create `backend/apps/orders/tests/test_order_field_policy.py`:

```python
from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderSource
from apps.users.models import (
    DataScope, FieldRuleType, Permission, Role, RoleFieldRule, RolePermission,
    User, UserRole, UserRoleAssignment,
)
from apps.users.rbac import bump_rbac_cache_version


class OrderFieldPolicyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(code="c", name="C")
        self.customer = Customer.objects.create(
            full_name="K", phone_raw="0900000022", phone_normalized="0900000022"
        )
        self.order = Order.objects.create(
            channel=self.channel, customer=self.customer, name="N", phone="0900000022",
            product="P", total_amount="1.00", order_source=OrderSource.LANDING, show_orders=True,
        )

    def _staff_hiding_phone(self):
        user = User.objects.create_user(username="hp", password="x", role=UserRole.STAFF)
        role = Role.objects.create(name="hide-phone", scope_default=DataScope.ALL)
        perm = Permission.objects.get_or_create(
            code="orders:view", defaults={"section": "orders", "action": "view"}
        )[0]
        RolePermission.objects.create(role=role, permission=perm)
        RoleFieldRule.objects.create(
            role=role, section="orders", field="phone", rule=FieldRuleType.HIDDEN
        )
        UserRoleAssignment.objects.create(user=user, role=role)
        bump_rbac_cache_version()
        return user

    def test_phone_hidden_for_restricted_role(self):
        user = self._staff_hiding_phone()
        self.client.force_authenticate(user=user)
        resp = self.client.post("/api/orders/", {}, format="json")
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()["results"]
        self.assertTrue(rows)
        self.assertNotIn("phone", rows[0])

    def test_phone_visible_for_admin(self):
        admin = User.objects.create_user(username="adm", password="x", role=UserRole.ADMIN)
        bump_rbac_cache_version()
        self.client.force_authenticate(user=admin)
        resp = self.client.post("/api/orders/", {}, format="json")
        rows = resp.json()["results"]
        self.assertIn("phone", rows[0])
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest apps/orders/tests/test_order_field_policy.py -q`
Expected: FAIL — `phone` vẫn xuất hiện cho role hide-phone.

- [ ] **Step 3: OrderSerializer kế thừa mixin**

Trong `order_core_serializers.py`, sửa khai báo class:

```python
from apps.users.field_policy import FieldPolicyOutputMixin


class OrderSerializer(FieldPolicyOutputMixin, serializers.ModelSerializer):
    field_section = "orders"
    # ... giữ nguyên phần còn lại
```

> `customer_phone` cũng là SĐT — nếu muốn ẩn luôn, thêm rule `customer_phone` ở UI; mixin xử lý theo tên key trong output nên không cần đổi code.

- [ ] **Step 4: Bơm field_policy vào context (list)**

Trong `_view_helpers.py` `_build_order_list_context`, thêm vào đầu hàm:

```python
    from apps.users.rbac import resolve_effective_permissions

    eff = resolve_effective_permissions(user)
    field_policy = {} if eff.is_superuser else dict(eff.field_policy)
```
và thêm vào dict trả về:
```python
        "field_policy": field_policy,
```
Cập nhật `OrderSerializerContext` (TypedDict) thêm key `field_policy: dict`.

- [ ] **Step 5: Bơm field_policy vào context (detail)**

Trong `order_crud_view.py`, tìm nơi khởi tạo `OrderSerializer(order, context={...})` cho detail; thêm `field_policy` vào context giống list:
```python
    from apps.users.rbac import resolve_effective_permissions

    eff = resolve_effective_permissions(request.user)
    context["field_policy"] = {} if eff.is_superuser else dict(eff.field_policy)
```
(Đọc view để chèn đúng chỗ; nếu detail tái dùng `_build_order_list_context`-style helper thì đã có sẵn.)

- [ ] **Step 6: Chạy test để xác nhận PASS**

Run: `python -m pytest apps/orders/tests/test_order_field_policy.py -q`
Expected: PASS (2 test).

- [ ] **Step 7: Regression**

Run: `python -m pytest apps/orders/tests/test_order_field_policy.py apps/orders/tests/test_order_scope_resolution.py apps/orders/tests/test_channel_scope_mutation_authz.py -q`
Expected: PASS.

- [ ] **Step 8: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py backend/apps/orders/transport/views/order_views_impl/_view_helpers.py backend/apps/orders/transport/views/order_views_impl/order_crud_view.py backend/apps/orders/tests/test_order_field_policy.py
git add backend/apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py backend/apps/orders/transport/views/order_views_impl/_view_helpers.py backend/apps/orders/transport/views/order_views_impl/order_crud_view.py backend/apps/orders/tests/test_order_field_policy.py
git commit -m "feat(rbac): enforce field-level policy on OrderSerializer (hide phone etc.)"
```

---

## Task 3: API + UI cấu hình RoleFieldRule

**Files:**
- Modify: `backend/apps/users/serializers.py`, `backend/apps/users/services.py`, `backend/apps/users/views.py`
- Modify: `frontend/src/lib/api/roles.ts`, `frontend/src/pages/RolesPage.tsx`
- Test: `backend/apps/users/tests/test_role_field_rule_api.py`

- [ ] **Step 1: Viết test thất bại (BE)**

Create `backend/apps/users/tests/test_role_field_rule_api.py`:

```python
from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.models import Role, RoleFieldRule
from apps.users.test_helpers import make_admin


class RoleFieldRuleApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=make_admin("adm"))

    def test_set_field_rules(self):
        role = Role.objects.create(name="r")
        resp = self.client.patch(
            f"/api/users/roles/{role.id}",
            {"field_rules": [{"section": "orders", "field": "phone", "rule": "hidden"}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        rule = RoleFieldRule.objects.get(role=role, section="orders", field="phone")
        self.assertEqual(rule.rule, "hidden")

    def test_role_serializer_returns_field_rules(self):
        role = Role.objects.create(name="r2")
        RoleFieldRule.objects.create(role=role, section="orders", field="phone", rule="readonly")
        resp = self.client.get("/api/users/roles")
        row = next(r for r in resp.json() if r["id"] == role.id)
        self.assertEqual(row["field_rules"], [{"section": "orders", "field": "phone", "rule": "readonly"}])
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest apps/users/tests/test_role_field_rule_api.py -q`
Expected: FAIL — `field_rules` chưa hỗ trợ.

- [ ] **Step 3: Serializer + service + view**

Trong `RoleSerializer` (thêm field + method, import `RoleFieldRule`):
```python
    field_rules = serializers.SerializerMethodField()
    # thêm "field_rules" vào Meta.fields
    def get_field_rules(self, obj):
        return [
            {"section": r.section, "field": r.field, "rule": r.rule}
            for r in RoleFieldRule.objects.filter(role=obj).order_by("section", "field")
        ]
```

Trong `RoleWriteSerializer`:
```python
    field_rules = serializers.ListField(child=serializers.DictField(), required=False)

    def validate_field_rules(self, value):
        from .models import FieldRuleType, VALID_SECTIONS

        valid_rules = {c for c, _ in FieldRuleType.choices}
        for item in value:
            if item.get("section") not in VALID_SECTIONS:
                raise serializers.ValidationError(f"section không hợp lệ: {item.get('section')}")
            if not item.get("field"):
                raise serializers.ValidationError("Thiếu field.")
            if item.get("rule") not in valid_rules:
                raise serializers.ValidationError(f"rule không hợp lệ: {item.get('rule')}")
        return value
```

Trong `RoleService`:
```python
    @staticmethod
    @transaction.atomic
    def set_role_field_rules(role, items: list[dict]) -> None:
        from .models import RoleFieldRule

        RoleFieldRule.objects.filter(role=role).delete()
        RoleFieldRule.objects.bulk_create(
            [
                RoleFieldRule(role=role, section=i["section"], field=i["field"], rule=i["rule"])
                for i in items
            ]
        )
        bump_rbac_cache_version()
```

Trong `role_collection` (POST) và `role_detail` (PATCH), thêm:
```python
    if "field_rules" in data:
        RoleService.set_role_field_rules(role, data["field_rules"])
```

- [ ] **Step 4: Chạy test BE để xác nhận PASS**

Run: `python -m pytest apps/users/tests/test_role_field_rule_api.py -q`
Expected: PASS (2 test).

- [ ] **Step 5: FE — type + API + UI field rule**

Trong `frontend/src/lib/api/roles.ts`:
- `Role` thêm: `field_rules: { section: string; field: string; rule: string }[];`
- `RoleWrite` thêm: `field_rules?: { section: string; field: string; rule: string }[];`

Trong `RolesPage.tsx`, thêm khối "Field rules": danh sách hàng (section input + field input + select rule hidden/readonly + nút xóa) + nút "thêm rule"; gửi kèm khi `save()`:
```tsx
await updateRole(selected.id, {
  permission_codes: [...draftCodes],
  section_scopes: Object.entries(draftScopes).map(([section, scope]) => ({ section, scope })),
  field_rules: draftFieldRules,
});
```
State `draftFieldRules` khởi tạo từ `selected.field_rules`.

- [ ] **Step 6: Build + commit**

```bash
cd frontend && npm run build && cd ..
uv run --no-project --with ruff ruff check backend/apps/users/serializers.py backend/apps/users/services.py backend/apps/users/views.py backend/apps/users/tests/test_role_field_rule_api.py
git add backend/apps/users/serializers.py backend/apps/users/services.py backend/apps/users/views.py backend/apps/users/tests/test_role_field_rule_api.py frontend/src/lib/api/roles.ts frontend/src/pages/RolesPage.tsx
git commit -m "feat(rbac): configure RoleFieldRule via API + role admin UI"
```

---

## Task 4: FE — hook useFieldPolicy (ẩn cột/disable input)

**Files:**
- Create: `frontend/src/hooks/useFieldPolicy.ts`
- Test: `frontend/src/hooks/__tests__/useFieldPolicy.test.tsx`

- [ ] **Step 1: Viết test thất bại**

Create `frontend/src/hooks/__tests__/useFieldPolicy.test.tsx`:

```typescript
import { describe, expect, it } from "vitest";
import { fieldRule } from "@/hooks/useFieldPolicy";

const policy = { orders: { phone: "hidden", note: "readonly" } };

describe("fieldRule()", () => {
  it("returns hidden for hidden field", () => {
    expect(fieldRule(policy, "orders", "phone")).toBe("hidden");
  });
  it("returns readonly for readonly field", () => {
    expect(fieldRule(policy, "orders", "note")).toBe("readonly");
  });
  it("returns null for unspecified field", () => {
    expect(fieldRule(policy, "orders", "name")).toBeNull();
  });
  it("returns null when no policy", () => {
    expect(fieldRule(undefined, "orders", "phone")).toBeNull();
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd frontend && npx vitest run src/hooks/__tests__/useFieldPolicy.test.tsx`
Expected: FAIL — module chưa tồn tại.

- [ ] **Step 3: Implement**

Create `frontend/src/hooks/useFieldPolicy.ts`:

```typescript
import { useAppShell } from "@/app/AppShellContext";

export type FieldRule = "hidden" | "readonly" | null;
type FieldPolicy = Record<string, Record<string, string>> | undefined;

/** Quy tắc field thuần — pure, test được. */
export function fieldRule(policy: FieldPolicy, section: string, field: string): FieldRule {
  const rule = policy?.[section]?.[field];
  if (rule === "hidden" || rule === "readonly") return rule;
  return null;
}

/** Hook: trả callback rule(section, field) đọc field_policy của user trong AppShell. */
export function useFieldPolicy(): (section: string, field: string) => FieldRule {
  const { user } = useAppShell();
  return (section, field) => fieldRule(user?.field_policy, section, field);
}
```

> Đảm bảo type `User` (App.tsx, S2) đã có `field_policy?: Record<string, Record<string, string>>`. Dùng `useFieldPolicy()` ở bảng đơn để ẩn cột phone / disable input khi rule === "readonly".

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `cd frontend && npx vitest run src/hooks/__tests__/useFieldPolicy.test.tsx`
Expected: PASS (4 test).

- [ ] **Step 5: Build + commit**

```bash
cd frontend && npm run build && cd ..
git add frontend/src/hooks/useFieldPolicy.ts frontend/src/hooks/__tests__/useFieldPolicy.test.tsx
git commit -m "feat(rbac-fe): useFieldPolicy hook to hide/disable fields per policy"
```

---

## Self-review checklist (S4)

- [ ] Output: field `hidden` bị drop; `readonly` vẫn hiện (chỉ chặn write). Có test admin vẫn thấy field.
- [ ] Write: field `hidden|readonly` bị strip + log, không 403 cứng (đúng spec mục 5.2 "mặc định strip + log").
- [ ] `field_policy` tính 1 lần/request qua resolver (có cache), bơm context — KHÔNG `SerializerMethodField` per-field.
- [ ] Field rule "ít hạn chế nhất thắng" (resolver S1) — role hidden + role readonly ⇒ readonly.
- [ ] FE chỉ ẩn UI; BE là enforcement thật (test snapshot serializer theo role).
- [ ] Tên nhất quán: `FieldPolicyOutputMixin.field_section`, `drop_hidden_fields`, `strip_protected_write_fields`, `set_role_field_rules`.

## Bàn giao S4 → S5

3 chiều (action/scope/field) đã enforce qua hệ mới. S5: bật multi-department thực sự (UserDepartment làm nguồn chính, UI gán nhiều phòng ban) và **dọn nợ kỹ thuật**: gỡ `User.role` + `Department.allowed_sections` + `has_section_access` + enum `UserRole` cũ + các short-circuit `user.role == ADMIN`. Đây là sprint rủi ro cao (xóa field) → làm cuối.
