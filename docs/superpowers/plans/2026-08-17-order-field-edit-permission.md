# Kế hoạch triển khai: Phân quyền sửa từng field trong đơn hàng

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cho phép cấu hình theo vai trò: ai được sửa field nào trong đơn hàng, mặc
định không được sửa gì (allow-list, fail-closed).

**Architecture:** Bảng mới `RoleFieldGrant` (role, section, field) làm allow-list,
tách hẳn khỏi `RoleFieldRule` cũ (lo việc *ẩn khi xem*). Resolver RBAC gộp grant của
mọi role thành `editable_fields` và cache cùng chỗ với quyền hiện có. Một guard duy
nhất ở `update_order_inline` so **giá trị** payload với giá trị đang lưu, khác mà
không có grant thì 403. Frontend loại field chưa được cấp khỏi payload và disable ô
nhập.

**Tech Stack:** Django 5 + DRF, PostgreSQL, Redis (cache RBAC), React 19 + TypeScript
+ shadcn/ui, vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-08-17-order-field-edit-permission-design.md`

## Global Constraints

- Worktree: `C:/Users/MYPC/Desktop/workspace/desi-order-wt-field-perm`, nhánh
  `feat/order-field-edit-permission`, gốc từ master `c8c6a581`.
- `apps.users` **KHÔNG được import** `apps.orders` (`.importlinter`). Chiều ngược lại
  được phép. Catalog field vì vậy nằm ở `apps/users/rbac_catalog.py`.
- Ruff bắt buộc xanh (ruleset `E4,E7,E9,F,B,C4,DTZ,LOG,PIE,RET,SIM,T20,UP` theo
  `backend/pyproject.toml`). `DTZ` cấm `datetime.now()` không tz.
- Không `cache.clear()` trong test (FLUSHDB đè Redis thật).
- Commit message tiếng Anh, ngắn, không kèm dòng `Co-Authored-By`.
- Section dùng cho grant ở v1 luôn là chuỗi `"orders"`.
- `staff_id` **không** thuộc catalog — đã có `sale-assignment:edit` gác riêng.

### Lệnh chạy test

Backend (dựng lại hạ tầng test nếu Docker Desktop vừa restart):

```bash
docker network create desi-test-net 2>/dev/null
docker run -d --name desi-test-pg --network desi-test-net \
  -e POSTGRES_USER=desi_order -e POSTGRES_PASSWORD=desi_order -e POSTGRES_DB=desi_order \
  public.ecr.aws/docker/library/postgres:16-alpine
docker run -d --name desi-test-redis --network desi-test-net \
  public.ecr.aws/docker/library/redis:7-alpine
```

```bash
cd C:/Users/MYPC/Desktop/workspace/desi-order-wt-field-perm && \
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_fieldperm \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
```

Đổi `POSTGRES_DB`/`REDIS_CACHE_DB` nếu chạy song song với suite khác (`REDIS_CACHE_DB`
chỉ nhận 0-15). Lint: `docker run --rm -v "$(pwd -W)/backend:/app" -w //app
ghcr.io/astral-sh/ruff:latest check --no-cache .`

Frontend — worktree mới chưa có `node_modules`, **đừng thử junction**, cứ cài:

```bash
cd C:/Users/MYPC/Desktop/workspace/desi-order-wt-field-perm/frontend && \
  npm ci --prefer-offline --no-audit --no-fund
```

Rồi `npx vitest run <file>`, `npx tsc --noEmit`, `npm run lint:strict`.

**Đo baseline frontend NGAY trước khi bắt đầu** (`npm run test:ci`) và ghi lại con số.
Baseline repo này đổi trong vòng một ngày; chép số cũ vào plan đã suýt làm bỏ sót hồi
quy thật.

---

## Cấu trúc file

| File | Trách nhiệm |
|---|---|
| `backend/apps/users/models.py` | + model `RoleFieldGrant` |
| `backend/apps/users/rbac_catalog.py` | + `ORDER_FIELD_GROUPS`, `ORDER_FIELD_LABELS`, `ALL_ORDER_FIELDS` |
| `backend/apps/users/migrations/0035_role_field_grant.py` | tạo bảng |
| `backend/apps/users/migrations/0036_seed_field_grants.py` | seed role đang có quyền sửa đơn + bump cache version |
| `backend/apps/users/rbac.py` | + `editable_fields` trong `EffectivePermissions` |
| `backend/apps/users/serializers.py` | đọc/ghi `field_grants` của role |
| `backend/apps/users/services.py` | `set_role_field_grants`, prefetch |
| `backend/apps/users/transport/views/users_views_impl/rbac_views.py` | endpoint catalog |
| `backend/apps/orders/field_diff.py` | **mới** — so sánh giá trị, thuần, không I/O |
| `backend/apps/orders/invoice_lock.py` | dùng lại `field_diff`, hành vi không đổi |
| `backend/apps/orders/transport/views/.../order_action_view.py` | guard mới |
| `frontend/src/hooks/useOrderFieldGrants.ts` | **mới** — `canEditField(field)` |
| `frontend/src/features/orders/OrdersTable/OrderCell.tsx` | chặn ô inline |
| `frontend/src/features/orders/OrderFormDialog.tsx` | disable + strip payload |
| `frontend/src/pages/RolesPage.tsx` | tab cấu hình |

---

### Task 1: Model, catalog và bảng grant

**Files:**
- Modify: `backend/apps/users/models.py` (sau `class RoleFieldRule`, ~dòng 211)
- Modify: `backend/apps/users/rbac_catalog.py`
- Create: `backend/apps/users/migrations/0035_role_field_grant.py`
- Test: `backend/apps/orders/tests/test_order_field_catalog.py`

**Interfaces:**
- Produces: `RoleFieldGrant` (fields `role`, `section`, `field`; `related_name="field_grants"`);
  `ORDER_FIELD_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...]`;
  `ORDER_FIELD_LABELS: dict[str, str]`; `ALL_ORDER_FIELDS: frozenset[str]`.

- [ ] **Step 1: Viết test đối chiếu catalog ↔ serializer (test sẽ đỏ)**

Đặt ở `apps/orders/tests/` vì `orders` được phép import `users`, không phải ngược lại.

```python
# backend/apps/orders/tests/test_order_field_catalog.py
"""Catalog field đơn phải khớp đúng serializer ghi đơn.

Thiếu test này thì thêm field vào serializer mà quên khai nhóm sẽ trôi im lặng:
field đó không nằm trong catalog nên guard bỏ qua = ai cũng sửa được, đúng thứ
allow-list sinh ra để chặn.
"""
from apps.orders.transport.serializers.order_serializers_impl.kiotviet_landing_serializers import (
    OrderInlineUpdateSerializer,
)
from apps.users.rbac_catalog import ALL_ORDER_FIELDS, ORDER_FIELD_GROUPS, ORDER_FIELD_LABELS


def test_catalog_covers_every_writable_order_field():
    serializer_fields = set(OrderInlineUpdateSerializer.Meta.fields) - {"staff_id"}
    assert ALL_ORDER_FIELDS == serializer_fields


def test_every_catalog_field_has_a_vietnamese_label():
    assert ALL_ORDER_FIELDS == set(ORDER_FIELD_LABELS)


def test_no_field_belongs_to_two_groups():
    seen: set[str] = set()
    for _code, _label, fields in ORDER_FIELD_GROUPS:
        overlap = seen & set(fields)
        assert not overlap, f"field nằm ở 2 nhóm: {overlap}"
        seen |= set(fields)


def test_group_codes_are_unique():
    codes = [code for code, _label, _fields in ORDER_FIELD_GROUPS]
    assert len(codes) == len(set(codes))
```

- [ ] **Step 2: Chạy test cho chắc là đỏ**

Run: `... python -m pytest apps/orders/tests/test_order_field_catalog.py -q --create-db`
Expected: FAIL — `ImportError: cannot import name 'ALL_ORDER_FIELDS'`

- [ ] **Step 3: Thêm catalog vào `rbac_catalog.py`**

Chèn xuống cuối file, sau `PERMISSION_CATALOG`:

```python
# ── Catalog field đơn hàng (quyền SỬA theo field) ────────────────────────────
# Đặt ở apps.users vì resolver/serializer role cần đọc, mà .importlinter cấm
# apps.users import apps.orders. Đây thuần dữ liệu: tên field + nhãn tiếng Việt.
# `staff_id` CỐ Ý vắng mặt — đã có quyền riêng `sale-assignment:edit`.
ORDER_FIELD_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("customer", "Khách hàng", ("name", "phone", "customer_gender")),
    (
        "address",
        "Địa chỉ",
        (
            "address", "raw_address", "province", "district", "ward",
            "province_code", "district_code", "ward_code",
            "kiotviet_location", "kiotviet_ward",
        ),
    ),
    ("products", "Sản phẩm", ("items", "sku", "quantity")),
    (
        "money",
        "Tiền & chiết khấu",
        (
            "total_amount", "total_after_discount", "discount_amount",
            "booking_voucher_discount", "other_price", "customer_prepaid",
            "shipping_fee",
        ),
    ),
    (
        "shipping",
        "Vận chuyển",
        ("shipping_code", "weight", "dimensions", "fulfillment_warehouse", "branch_id"),
    ),
    (
        "status",
        "Trạng thái & CSKH",
        ("status", "result", "feedback", "customer_return", "return_date", "crm"),
    ),
    (
        "invoice",
        "Hoá đơn & VAT",
        (
            "invoice_code", "invoice_id", "vat", "is_export_vat", "vat_link",
            "payment_method", "order_code",
        ),
    ),
    (
        "classify",
        "Phân loại & KOC",
        (
            "order_type", "koc_order_type", "order_source", "channel_code",
            "kiotviet_channel_id", "recall", "recall_entries", "created_at",
        ),
    ),
)

ORDER_FIELD_LABELS: dict[str, str] = {
    "name": "Tên khách", "phone": "Số điện thoại", "customer_gender": "Giới tính",
    "address": "Địa chỉ", "raw_address": "Địa chỉ thô", "province": "Tỉnh/TP",
    "district": "Quận/Huyện", "ward": "Phường/Xã", "province_code": "Mã tỉnh",
    "district_code": "Mã quận", "ward_code": "Mã phường",
    "kiotviet_location": "Khu vực KiotViet", "kiotviet_ward": "Phường KiotViet",
    "items": "Danh sách sản phẩm", "sku": "SKU", "quantity": "Số lượng",
    "total_amount": "Tổng tiền", "total_after_discount": "Sau giảm giá",
    "discount_amount": "Giảm giá", "booking_voucher_discount": "Voucher booking",
    "other_price": "Phụ phí khác", "customer_prepaid": "Khách ứng trước",
    "shipping_fee": "Phí vận chuyển",
    "shipping_code": "Mã vận đơn", "weight": "Khối lượng", "dimensions": "Kích thước",
    "fulfillment_warehouse": "Kho xuất", "branch_id": "Chi nhánh",
    "status": "Trạng thái", "result": "Kết quả", "feedback": "Phản hồi",
    "customer_return": "Khách hoàn", "return_date": "Ngày hoàn", "crm": "Thông tin CSKH",
    "invoice_code": "Mã hoá đơn", "invoice_id": "ID hoá đơn", "vat": "Hình thức VAT",
    "is_export_vat": "Xuất VAT", "vat_link": "Link VAT",
    "payment_method": "Hình thức thanh toán", "order_code": "Mã đơn",
    "order_type": "Loại đơn", "koc_order_type": "Loại đơn KOC",
    "order_source": "Nguồn đơn", "channel_code": "Kênh",
    "kiotviet_channel_id": "Kênh KiotViet", "recall": "Thu hồi",
    "recall_entries": "Nhóm thu hồi", "created_at": "Ngày tạo đơn",
}

ALL_ORDER_FIELDS: frozenset[str] = frozenset(
    field for _code, _label, fields in ORDER_FIELD_GROUPS for field in fields
)
```

- [ ] **Step 4: Chạy lại test catalog**

Run: `... python -m pytest apps/orders/tests/test_order_field_catalog.py -q --create-db`
Expected: PASS — 4 test.

Nếu `test_catalog_covers_every_writable_order_field` đỏ, in ra hiệu hai tập rồi bổ
sung field thiếu vào nhóm hợp lý; **không** sửa test cho khớp catalog.

- [ ] **Step 5: Thêm model `RoleFieldGrant`**

Trong `backend/apps/users/models.py`, ngay sau `class RoleFieldRule`:

```python
class RoleFieldGrant(models.Model):
    """Allow-list: role được SỬA field nào. Vắng mặt = KHÔNG được sửa (fail-closed).

    Khác `RoleFieldRule` ở ngay chiều ngữ nghĩa: `RoleFieldRule` là deny-list cho
    việc XEM (ẩn field khỏi output), bảng này là allow-list cho việc GHI. Cố ý tách
    bảng thay vì thêm rule type — nhét hai chiều ngược nhau vào cùng một map
    field→rule là mời `drop_hidden_fields` đọc nhầm, và không test nào bắt được.

    Lưu tên field THẬT (đã bung khỏi nhóm nghiệp vụ ở UI). Lưu tên nhóm thì thêm
    field mới vào nhóm là mọi role có nhóm đó tự động sửa được field mới —
    fail-open lẻn vào cửa sau.
    """

    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="field_grants")
    section = models.CharField(max_length=50)
    field = models.CharField(max_length=100)

    class Meta:
        db_table = "rbac_role_field_grants"
        unique_together = ("role", "section", "field")
        indexes = [
            models.Index(fields=["role", "section"], name="rbac_rfg_role_sec_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.role_id}:{self.section}.{self.field}"
```

- [ ] **Step 6: Sinh migration**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_fieldperm \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  python manage.py makemigrations users --name role_field_grant
```

Expected: tạo `0035_role_field_grant.py`. Mở ra kiểm: chỉ có `CreateModel` +
`AddIndex` + `AlterUniqueTogether`, **không** kèm thay đổi lạ của model khác. Có thì
dừng lại tìm hiểu — migration lẫn thay đổi ngoài ý muốn là nợ khó gỡ về sau.

- [ ] **Step 7: Chạy test migration + catalog**

Run: `... python -m pytest apps/orders/tests/test_order_field_catalog.py apps/users/tests -q --create-db`
Expected: PASS toàn bộ (`apps/users/tests` là lưới bắt hồi quy của model/RBAC).

- [ ] **Step 8: Commit**

```bash
git add backend/apps/users/models.py backend/apps/users/rbac_catalog.py \
        backend/apps/users/migrations/0035_role_field_grant.py \
        backend/apps/orders/tests/test_order_field_catalog.py
git commit -m "feat(rbac): add RoleFieldGrant model and order field catalog"
```

---

### Task 2: Resolver trả `editable_fields`

**Files:**
- Modify: `backend/apps/users/rbac.py`
- Test: `backend/apps/users/tests/test_rbac_resolver.py`

**Interfaces:**
- Consumes: `RoleFieldGrant` (Task 1)
- Produces: `EffectivePermissions.editable_fields: dict[str, frozenset[str]]`;
  `EffectivePermissions.can_edit_field(section: str, field: str) -> bool`

- [ ] **Step 1: Viết test (sẽ đỏ)**

Thêm vào `backend/apps/users/tests/test_rbac_resolver.py`, theo đúng khuôn fixture
đang có trong file (đọc các test `field_policy` sẵn có để dùng lại helper tạo role).

```python
def test_editable_fields_unions_grants_across_roles(db, django_user_model):
    user = django_user_model.objects.create(username="u-union", is_active=True)
    role_a = Role.objects.create(name="Chỉ tiền")
    role_b = Role.objects.create(name="Chỉ khách")
    RoleFieldGrant.objects.create(role=role_a, section="orders", field="total_amount")
    RoleFieldGrant.objects.create(role=role_b, section="orders", field="phone")
    UserRoleAssignment.objects.create(user=user, role=role_a)
    UserRoleAssignment.objects.create(user=user, role=role_b)

    eff = resolve_effective_permissions(user)

    assert eff.editable_fields["orders"] == frozenset({"total_amount", "phone"})
    assert eff.can_edit_field("orders", "total_amount")
    assert eff.can_edit_field("orders", "phone")
    assert not eff.can_edit_field("orders", "status")


def test_no_grant_means_no_editable_field(db, django_user_model):
    user = django_user_model.objects.create(username="u-empty", is_active=True)
    role = Role.objects.create(name="Không grant")
    UserRoleAssignment.objects.create(user=user, role=role)

    eff = resolve_effective_permissions(user)

    assert eff.editable_fields == {}
    assert not eff.can_edit_field("orders", "phone")


def test_superuser_can_edit_every_field(db, django_user_model):
    user = django_user_model.objects.create(username="u-super", is_active=True, is_superuser=True)

    eff = resolve_effective_permissions(user)

    assert eff.can_edit_field("orders", "total_amount")
    assert eff.can_edit_field("orders", "bat_ky_field_nao")


def test_from_cache_tolerates_payload_without_editable_fields():
    """Entry cache CŨ (TTL 30') không có key này. Đọc bằng data[...] là KeyError
    hàng loạt ngay giây deploy — case này khoá lại đúng cú đó."""
    legacy = {
        "permission_codes": ["orders:view"],
        "scope_by_section": {},
        "field_policy": {},
        "is_superuser": False,
    }

    eff = EffectivePermissions.from_cache(legacy)

    assert eff.editable_fields == {}
    assert eff.can("orders:view")


def test_to_cache_round_trip_keeps_editable_fields():
    eff = EffectivePermissions(editable_fields={"orders": frozenset({"phone"})})

    restored = EffectivePermissions.from_cache(eff.to_cache())

    assert restored.editable_fields == {"orders": frozenset({"phone"})}
```

Import cần thêm ở đầu file test: `RoleFieldGrant`, `EffectivePermissions` (nếu chưa có).

- [ ] **Step 2: Chạy test cho chắc là đỏ**

Run: `... python -m pytest apps/users/tests/test_rbac_resolver.py -q --create-db`
Expected: FAIL — `AttributeError: 'EffectivePermissions' object has no attribute 'editable_fields'`

- [ ] **Step 3: Sửa `rbac.py`**

Thêm `RoleFieldGrant` vào khối import từ `.models`. Rồi:

```python
@dataclass(frozen=True)
class EffectivePermissions:
    permission_codes: frozenset = field(default_factory=frozenset)
    scope_by_section: dict = field(default_factory=dict)
    field_policy: dict = field(default_factory=dict)
    # Allow-list field được SỬA, theo section. Vắng mặt = không được sửa.
    editable_fields: dict = field(default_factory=dict)
    is_superuser: bool = False

    def can(self, code: str) -> bool:
        return self.is_superuser or code in self.permission_codes

    def scope_for(self, section: str) -> str:
        if self.is_superuser:
            return DataScope.ALL
        return self.scope_by_section.get(section, DataScope.OWN)

    def can_edit_field(self, section: str, field_name: str) -> bool:
        if self.is_superuser:
            return True
        return field_name in self.editable_fields.get(section, frozenset())

    def to_cache(self) -> dict:
        return {
            "permission_codes": sorted(self.permission_codes),
            "scope_by_section": self.scope_by_section,
            "field_policy": self.field_policy,
            "editable_fields": {s: sorted(f) for s, f in self.editable_fields.items()},
            "is_superuser": self.is_superuser,
        }

    @classmethod
    def from_cache(cls, data: dict) -> "EffectivePermissions":
        return cls(
            permission_codes=frozenset(data["permission_codes"]),
            scope_by_section=data["scope_by_section"],
            field_policy=data["field_policy"],
            # .get() chứ KHÔNG data[...]: entry cache cũ (TTL 30') thiếu key này,
            # đọc cứng là KeyError hàng loạt ngay giây deploy.
            editable_fields={
                s: frozenset(f) for s, f in (data.get("editable_fields") or {}).items()
            },
            is_superuser=data["is_superuser"],
        )
```

Trong `_compute`, ngay sau khối tính `field_policy`:

```python
    # 4) editable fields = HỢP các grant (allow-list, nới lỏng nhất thắng)
    editable_fields: dict[str, set] = {}
    for grant in RoleFieldGrant.objects.filter(role_id__in=role_ids):
        editable_fields.setdefault(grant.section, set()).add(grant.field)

    return EffectivePermissions(
        permission_codes=frozenset(codes),
        scope_by_section=scope_by_section,
        field_policy=field_policy,
        editable_fields={s: frozenset(f) for s, f in editable_fields.items()},
        is_superuser=False,
    )
```

Cập nhật docstring đầu file: thêm dòng `- editable field: hợp các grant (allow-list).`

- [ ] **Step 4: Chạy lại test**

Run: `... python -m pytest apps/users/tests/test_rbac_resolver.py -q --create-db`
Expected: PASS.

- [ ] **Step 5: Chạy cả suite users để chắc không vỡ cache/serializer**

Run: `... python -m pytest apps/users/tests -q --create-db`
Expected: PASS toàn bộ.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/users/rbac.py backend/apps/users/tests/test_rbac_resolver.py
git commit -m "feat(rbac): resolve editable order fields from role grants"
```

---

### Task 3: API role đọc/ghi `field_grants`

**Files:**
- Modify: `backend/apps/users/serializers.py` (`RoleSerializer` ~238, `RoleWriteSerializer` ~287)
- Modify: `backend/apps/users/services.py` (`RoleService`)
- Test: `backend/apps/users/tests/test_rbac_sections.py`

**Interfaces:**
- Consumes: `ALL_ORDER_FIELDS` (Task 1)
- Produces: `RoleSerializer.field_grants: list[str]` (chỉ field của section `orders`);
  `RoleWriteSerializer.field_grants: list[str]`;
  `RoleService.set_role_field_grants(role, fields: list[str]) -> None`

- [ ] **Step 1: Viết test (sẽ đỏ)**

Thêm vào `backend/apps/users/tests/test_rbac_sections.py` (nơi chuẩn cho việc đăng ký
section/quyền), dùng client + fixture admin sẵn có trong file:

```python
def test_role_detail_returns_field_grants(admin_client, db):
    role = Role.objects.create(name="Kế toán test")
    RoleFieldGrant.objects.create(role=role, section="orders", field="total_amount")
    RoleFieldGrant.objects.create(role=role, section="orders", field="phone")

    res = admin_client.get(f"/api/rbac/roles/{role.id}")

    assert res.status_code == 200
    assert res.json()["field_grants"] == ["phone", "total_amount"]  # sắp xếp ổn định


def test_update_role_replaces_field_grants(admin_client, db):
    role = Role.objects.create(name="Kho test")
    RoleFieldGrant.objects.create(role=role, section="orders", field="phone")

    res = admin_client.patch(
        f"/api/rbac/roles/{role.id}",
        data={"field_grants": ["shipping_code", "weight"]},
        content_type="application/json",
    )

    assert res.status_code == 200
    assert set(
        RoleFieldGrant.objects.filter(role=role).values_list("field", flat=True)
    ) == {"shipping_code", "weight"}


def test_update_role_rejects_unknown_field_name(admin_client, db):
    """Chống hẳn lớp lỗi của cơ chế cũ: gõ sai tên field thì rule im lặng không chạy."""
    role = Role.objects.create(name="Role gõ sai")

    res = admin_client.patch(
        f"/api/rbac/roles/{role.id}",
        data={"field_grants": ["cost_price"]},
        content_type="application/json",
    )

    assert res.status_code == 400
    assert "cost_price" in str(res.json())
    assert not RoleFieldGrant.objects.filter(role=role).exists()


def test_empty_field_grants_clears_all(admin_client, db):
    role = Role.objects.create(name="Role bị thu quyền")
    RoleFieldGrant.objects.create(role=role, section="orders", field="phone")

    res = admin_client.patch(
        f"/api/rbac/roles/{role.id}",
        data={"field_grants": []},
        content_type="application/json",
    )

    assert res.status_code == 200
    assert not RoleFieldGrant.objects.filter(role=role).exists()
```

- [ ] **Step 2: Chạy test cho chắc là đỏ**

Run: `... python -m pytest apps/users/tests/test_rbac_sections.py -q --create-db`
Expected: FAIL — `KeyError: 'field_grants'`

- [ ] **Step 3: Sửa `serializers.py`**

Trong `RoleSerializer`: thêm `field_grants = serializers.SerializerMethodField()` và
vào `Meta.fields` (sau `"field_rules"`), rồi:

```python
    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_field_grants(self, obj: Role) -> list[str]:
        # Đọc qua .all() để ăn prefetch của list_roles (xem comment 3 method trên);
        # .filter() mới trên related manager là 1 query MỖI role.
        return sorted(
            g.field for g in obj.field_grants.all() if g.section == ORDER_GRANT_SECTION
        )
```

Trong `RoleWriteSerializer`:

```python
    field_grants = serializers.ListField(child=serializers.CharField(), required=False)

    def validate_field_grants(self, value):
        unknown = sorted(set(value) - ALL_ORDER_FIELDS)
        if unknown:
            raise serializers.ValidationError(
                f"Field không hợp lệ: {', '.join(unknown)}."
            )
        return value
```

Import ở đầu file: `from .rbac_catalog import ALL_ORDER_FIELDS, ORDER_GRANT_SECTION`.

Hằng `ORDER_GRANT_SECTION = "orders"` khai trong **`rbac_catalog.py`** (Task 1),
không phải trong `serializers.py`: `services.py` và `UserSerializer` cũng dùng nó, mà
để `services` import từ `serializers` là ngược chiều phụ thuộc. Quay lại Task 1 thêm
dòng này vào cuối `rbac_catalog.py` nếu chưa có:

```python
# Section dùng cho grant field ở v1. Đặt tên thay vì rải chuỗi "orders" khắp nơi.
ORDER_GRANT_SECTION = "orders"
```

- [ ] **Step 4: Sửa `services.py`**

Thêm `RoleFieldGrant` vào import model. Trong `RoleService`:

```python
    @staticmethod
    @transaction.atomic
    def set_role_field_grants(role: Role, fields: list[str]) -> None:
        """Thay toàn bộ grant của section orders. Danh sách rỗng = thu hết quyền sửa."""
        RoleFieldGrant.objects.filter(role=role, section=ORDER_GRANT_SECTION).delete()
        RoleFieldGrant.objects.bulk_create(
            [
                RoleFieldGrant(role=role, section=ORDER_GRANT_SECTION, field=f)
                for f in sorted(set(fields))
            ]
        )
        bump_rbac_cache_version()
```

Trong `_apply_role_relations`, thêm cuối hàm:

```python
        if "field_grants" in validated:
            cls.set_role_field_grants(role, validated["field_grants"])
```

Trong `list_roles`, thêm `"field_grants"` vào `prefetch_related` — thiếu là **thêm 1
query mỗi role** trên trang Phân quyền, đúng lỗi audit 2026-07-26 T1:

```python
        return Role.objects.prefetch_related(
            "role_permissions__permission", "section_scopes", "field_rules", "field_grants"
        ).order_by("id")
```

- [ ] **Step 5: Chạy lại test + test N+1 sẵn có**

Run: `... python -m pytest apps/users/tests/test_rbac_sections.py apps/users/tests/test_users_list_queries.py -q --create-db`
Expected: PASS. Nếu test đếm query của trang Phân quyền đỏ, kiểm lại `prefetch_related`
và việc `get_field_grants` đọc `.all()` chứ không `.filter()`.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/users/serializers.py backend/apps/users/services.py \
        backend/apps/users/tests/test_rbac_sections.py
git commit -m "feat(rbac): read and write role field grants via API"
```

---

### Task 4: Endpoint catalog cho frontend

**Files:**
- Modify: `backend/apps/users/transport/views/users_views_impl/rbac_views.py`
- Modify: `backend/apps/users/urls.py`
- Test: `backend/apps/users/tests/test_rbac_sections.py`

**Interfaces:**
- Produces: `GET /api/rbac/order-field-catalog` →
  `{"groups": [{"code": str, "label": str, "fields": [{"name": str, "label": str}]}]}`

- [ ] **Step 1: Viết test (sẽ đỏ)**

```python
def test_order_field_catalog_endpoint(admin_client, db):
    res = admin_client.get("/api/rbac/order-field-catalog")

    assert res.status_code == 200
    groups = res.json()["groups"]
    assert [g["code"] for g in groups][:3] == ["customer", "address", "products"]
    money = next(g for g in groups if g["code"] == "money")
    assert money["label"] == "Tiền & chiết khấu"
    assert {f["name"] for f in money["fields"]} == {
        "total_amount", "total_after_discount", "discount_amount",
        "booking_voucher_discount", "other_price", "customer_prepaid", "shipping_fee",
    }
    assert all(f["label"] for g in groups for f in g["fields"])
```

- [ ] **Step 2: Chạy test cho chắc là đỏ**

Run: `... python -m pytest apps/users/tests/test_rbac_sections.py -k catalog -q --create-db`
Expected: FAIL — 404.

- [ ] **Step 3: Viết view**

Trong `rbac_views.py`, đặt cạnh `permissions_catalog` và bắt chước đúng decorator của
nó (đọc hàm đó trước để khớp `@extend_schema` + `@permission_classes`):

```python
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def order_field_catalog(request):
    """Nhóm field đơn + nhãn tiếng Việt cho tab cấu hình quyền sửa field.

    Nguồn sự thật ở backend: frontend hard-code danh sách thì hai bên lệch nhau chỉ
    là chuyện thời gian, và lệch ở đây nghĩa là admin tick một field không tồn tại.
    """
    return Response(
        {
            "groups": [
                {
                    "code": code,
                    "label": label,
                    "fields": [
                        {"name": f, "label": ORDER_FIELD_LABELS[f]} for f in fields
                    ],
                }
                for code, label, fields in ORDER_FIELD_GROUPS
            ]
        }
    )
```

- [ ] **Step 4: Đăng ký URL**

Trong `backend/apps/users/urls.py`, cạnh dòng `path("permissions", ...)`:

```python
    path("order-field-catalog", order_field_catalog, name="rbac-order-field-catalog"),
```

kèm thêm `order_field_catalog` vào khối import.

- [ ] **Step 5: Chạy lại test**

Run: `... python -m pytest apps/users/tests/test_rbac_sections.py -q --create-db`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/users/transport/views/users_views_impl/rbac_views.py \
        backend/apps/users/urls.py backend/apps/users/tests/test_rbac_sections.py
git commit -m "feat(rbac): expose order field catalog endpoint"
```

---

### Task 5: Tách bộ so sánh giá trị ra `field_diff.py`

Refactor thuần — **hành vi `invoice_lock` không được đổi một ly**. Test sẵn có của
`invoice_lock` là lưới bảo vệ; không sửa file test đó.

**Files:**
- Create: `backend/apps/orders/field_diff.py`
- Modify: `backend/apps/orders/invoice_lock.py`
- Test: test `invoice_lock` sẵn có (tìm bằng `grep -rl invoice_lock backend/apps/orders/tests`)

**Interfaces:**
- Produces: `UNPARSEABLE`; `norm_str(v)`, `norm_int(v)`, `norm_decimal(v)`;
  `items_changed(order, payload_items) -> bool`;
  `field_changed(order, field: str, new_value) -> bool`;
  `changed_fields(order, validated_data: dict, fields: Iterable[str]) -> list[str]`

- [ ] **Step 1: Chạy test invoice_lock hiện tại, ghi lại kết quả xanh**

```bash
grep -rl "invoice_lock\|locked_field_changes" backend/apps/orders/tests
```

Run: `... python -m pytest <các file vừa tìm được> -q --create-db`
Expected: PASS. Ghi lại số test — đây là mốc so sánh sau refactor.

- [ ] **Step 2: Tạo `field_diff.py` bằng cách bê nguyên phần thuần từ `invoice_lock.py`**

```python
"""So sánh giá trị field của đơn: payload có ĐỔI THẬT so với bản ghi đang lưu không.

Tách khỏi invoice_lock.py để hai guard dùng chung (khoá theo hoá đơn, và quyền sửa
theo field). Module thuần: không import DRF/model, không I/O — chỉ đọc thuộc tính
của order đã nạp.

So GIÁ TRỊ chứ không so sự có mặt trong payload: form đơn gửi lại full object, chặn
theo sự có mặt sẽ 403 oan cho mọi cập nhật khác đi kèm.
"""
from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

# Giá trị payload không parse nổi → sentinel khác MỌI giá trị hợp lệ. Tuyệt đối
# không rơi về None: None trùng với đơn đang để trống → tuột qua guard.
UNPARSEABLE = object()

DECIMAL_FIELDS = frozenset({
    "weight", "total_amount", "discount_amount", "booking_voucher_discount",
    "other_price", "customer_prepaid", "total_after_discount", "shipping_fee",
})

INT_FIELDS = frozenset({"quantity"})


def norm_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def norm_int(value: Any) -> Any:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return UNPARSEABLE


def norm_decimal(value: Any) -> Any:
    if value is None or value == "":
        return Decimal(0)
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return UNPARSEABLE


def items_changed(order: Any, payload_items: list[dict[str, Any]]) -> bool:
    """Đổi dòng hàng = thêm/bớt dòng, đổi sản phẩm, đổi số lượng hoặc đổi đơn giá.

    Dòng payload khớp dòng DB theo `product_id`, thiếu thì theo `sku`. `unit_price`
    không gửi nghĩa là "để hệ thống tự tính theo bảng giá" → không coi là đổi giá.
    """
    current_items = list(order.items.all())
    if len(current_items) != len(payload_items):
        return True

    by_product = {item.product_id: item for item in current_items}
    by_sku: dict[str, Any] = {}
    for item in current_items:
        sku = norm_str(item.sku)
        if sku:
            by_sku.setdefault(sku, item)

    matched_pks: set[Any] = set()
    for payload_item in payload_items:
        product_id = norm_int(payload_item.get("product_id"))
        sku = norm_str(payload_item.get("sku"))
        matched = by_product.get(product_id) if product_id is not None else None
        if matched is None and sku:
            matched = by_sku.get(sku)
        if matched is None or matched.pk in matched_pks:
            return True
        matched_pks.add(matched.pk)

        if norm_int(payload_item.get("quantity")) != norm_int(matched.quantity):
            return True
        unit_price = payload_item.get("unit_price")
        if unit_price is not None and norm_decimal(unit_price) != norm_decimal(matched.unit_price):
            return True
    return False


def field_changed(order: Any, field: str, new_value: Any) -> bool:
    if field == "items":
        return items_changed(order, new_value or [])

    current = getattr(order, field, None)
    if field in DECIMAL_FIELDS:
        return norm_decimal(new_value) != norm_decimal(current)
    if field in INT_FIELDS:
        return norm_int(new_value) != norm_int(current)
    return norm_str(new_value) != norm_str(current)


def changed_fields(order: Any, validated_data: dict[str, Any], fields: Iterable[str]) -> list[str]:
    """Các field trong `fields` mà payload ĐỔI THẬT giá trị, theo thứ tự `fields`."""
    return [
        f for f in fields if f in validated_data and field_changed(order, f, validated_data[f])
    ]
```

`shipping_fee` được thêm vào `DECIMAL_FIELDS` (invoice_lock không khoá nó nên trước
đây không cần) — không đổi hành vi invoice_lock vì `changed_fields` chỉ duyệt đúng
danh sách field được truyền vào.

- [ ] **Step 3: Rút gọn `invoice_lock.py` để dùng lại**

Giữ nguyên `INVOICE_LOCKED_FIELD_LABELS`, `INVOICE_LOCKED_FIELDS`,
`is_invoice_locked`, `describe_locked_fields` và toàn bộ docstring đầu file. Xoá
`_norm_*`, `_items_changed`, `_field_changed`, `_DECIMAL_FIELDS`, `_INT_FIELDS`,
`_UNPARSEABLE`. Thay thân `locked_field_changes`:

```python
from .field_diff import changed_fields


def locked_field_changes(order: Any, validated_data: dict[str, Any]) -> list[str]:
    """Các field bị khoá mà payload ĐỔI THẬT giá trị, theo thứ tự khai báo."""
    return changed_fields(order, validated_data, INVOICE_LOCKED_FIELD_LABELS)
```

`INVOICE_LOCKED_FIELD_LABELS` là dict → duyệt nó cho ra đúng thứ tự khai báo như bản
cũ. Dọn import thừa (`Decimal`, `InvalidOperation`, `Iterable` nếu không còn dùng) —
ruff `F401` sẽ bắt.

- [ ] **Step 4: Chạy lại test invoice_lock — phải xanh y hệt Step 1**

Run: `... python -m pytest <các file ở Step 1> -q --create-db`
Expected: PASS, đúng số test như Step 1. Lệch một test là refactor đã đổi hành vi —
dừng lại, so `git diff`, đừng sửa test.

- [ ] **Step 5: Lint**

Run: `docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache apps/orders`
Expected: không lỗi mới.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/field_diff.py backend/apps/orders/invoice_lock.py
git commit -m "refactor(orders): extract value comparison into field_diff module"
```

---

### Task 6: Mở rộng comparator cho các kiểu field còn lại

**Files:**
- Modify: `backend/apps/orders/field_diff.py`
- Test: `backend/apps/orders/tests/test_order_field_diff.py`

**Interfaces:**
- Produces: `VALIDATED_KEY_BY_FIELD: dict[str, str]`;
  `FK_FIELDS`, `DATETIME_FIELDS`, `DATE_FIELDS`, `BOOL_FIELDS`, `NESTED_FIELDS: frozenset[str]`;
  `changed_fields` xử lý đủ 6 kiểu.

**Bẫy phải nhớ:** `validated_data` dùng khoá theo `source` của serializer, **không**
theo tên field khai báo. `channel_code` → `channel`, `kiotviet_channel_id` →
`kiotviet_channel`. Tra sai khoá = field luôn "không có trong payload" = guard không
bao giờ bắn = quyền vô hiệu hoàn toàn, mà test hời hợt vẫn xanh.

- [ ] **Step 1: Viết test (sẽ đỏ)**

```python
# backend/apps/orders/tests/test_order_field_diff.py
"""Comparator field đơn: gửi lại giá trị CŨ không được tính là đổi.

Đây là điều kiện sống của guard quyền field: form đơn gửi full object mỗi lần lưu,
một false positive ở đây là 403 oan cho người dùng hợp lệ.
"""
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

import pytest

from apps.orders.field_diff import changed_fields, field_changed


@pytest.mark.django_db
def test_resending_current_values_is_not_a_change(order_with_items):
    order = order_with_items
    validated = {
        "phone": order.phone,
        "total_amount": Decimal(str(order.total_amount)),
        "status": order.status,
        "channel": order.channel,
        "created_at": order.created_at,
        "is_export_vat": order.is_export_vat,
    }

    assert changed_fields(order, validated, list(validated)) == []


@pytest.mark.django_db
def test_foreign_key_compares_by_pk(order_with_items, another_channel):
    order = order_with_items

    assert not field_changed(order, "channel", order.channel)
    assert field_changed(order, "channel", another_channel)


@pytest.mark.django_db
def test_datetime_compares_across_timezones(order_with_items):
    """Cùng một thời điểm, khác biểu diễn múi giờ → KHÔNG phải đổi."""
    order = order_with_items
    same_instant_other_tz = order.created_at.astimezone(
        dt_timezone(timedelta(hours=7))
    )

    assert not field_changed(order, "created_at", same_instant_other_tz)
    assert field_changed(order, "created_at", order.created_at + timedelta(minutes=1))


@pytest.mark.django_db
def test_date_field_has_no_timezone_trap(order_with_items):
    order = order_with_items
    order.return_date = date(2026, 8, 17)
    order.save(update_fields=["return_date"])

    assert not field_changed(order, "return_date", date(2026, 8, 17))
    assert field_changed(order, "return_date", date(2026, 8, 18))


@pytest.mark.django_db
def test_branch_id_is_a_string_not_a_foreign_key(order_with_items):
    """branch_id là CharField dù tên kết thúc bằng _id — suy kiểu theo tên là chặn oan."""
    order = order_with_items
    order.branch_id = "CN01"
    order.save(update_fields=["branch_id"])

    assert not field_changed(order, "branch_id", "CN01")
    assert field_changed(order, "branch_id", "CN02")


@pytest.mark.django_db
def test_boolean_field(order_with_items):
    order = order_with_items
    order.customer_return = False
    order.save(update_fields=["customer_return"])

    assert not field_changed(order, "customer_return", False)
    assert field_changed(order, "customer_return", True)


@pytest.mark.django_db
def test_items_unchanged_when_resent_identically(order_with_items):
    order = order_with_items
    payload = [
        {"sku": i.sku, "quantity": i.quantity, "unit_price": i.unit_price}
        for i in order.items.all()
    ]

    assert not field_changed(order, "items", payload)


@pytest.mark.django_db
def test_items_changed_on_quantity_bump(order_with_items):
    order = order_with_items
    payload = [
        {"sku": i.sku, "quantity": i.quantity + 1, "unit_price": i.unit_price}
        for i in order.items.all()
    ]

    assert field_changed(order, "items", payload)
```

Fixture `order_with_items` và `another_channel`: dùng lại factory sẵn có trong
`backend/apps/orders/tests/conftest.py`. Đọc conftest trước; nếu chưa có fixture tên
đó thì thêm vào conftest (không tự dựng object trong từng test).

- [ ] **Step 2: Chạy test cho chắc là đỏ**

Run: `... python -m pytest apps/orders/tests/test_order_field_diff.py -q --create-db`
Expected: FAIL — `test_foreign_key_compares_by_pk` và `test_datetime_compares_across_timezones`
đỏ (nhánh `norm_str` hiện tại so `str(instance)` và so chuỗi có offset).

- [ ] **Step 3: Bổ sung comparator vào `field_diff.py`**

```python
# Khoá trong `validated_data` KHÁC tên field khai báo khi serializer có `source`.
# Tra sai khoá = guard không bao giờ bắn = quyền vô hiệu, mà test hời hợt vẫn xanh.
VALIDATED_KEY_BY_FIELD: dict[str, str] = {
    "channel_code": "channel",
    "kiotviet_channel_id": "kiotviet_channel",
}

# validated_data trả về INSTANCE model → so .pk, không so str(instance).
FK_FIELDS = frozenset({"channel", "kiotviet_channel", "fulfillment_warehouse"})
DATETIME_FIELDS = frozenset({"created_at"})
DATE_FIELDS = frozenset({"return_date"})
BOOL_FIELDS = frozenset({"is_export_vat", "customer_return"})
NESTED_FIELDS = frozenset({"crm", "recall", "recall_entries"})


def _norm_dt(value: Any) -> Any:
    """Quy về UTC rồi so. Cùng thời điểm khác múi giờ phải bằng nhau — so chuỗi ISO
    có offset thì '+07:00' và 'Z' của cùng một mốc lại khác nhau → 403 oan."""
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        return UNPARSEABLE
    return value.astimezone(dt_timezone.utc)


def _datetime_changed(order: Any, field: str, new_value: Any) -> bool:
    normalized_new = _norm_dt(new_value)
    normalized_current = _norm_dt(getattr(order, field, None))
    # Hai vế cùng không parse nổi thì `UNPARSEABLE != UNPARSEABLE` là False → guard
    # coi như "không đổi" và cho ghi. Với hệ fail-closed đó là lỗ: không hiểu nổi
    # giá trị thì phải chặn, không phải cho qua.
    if normalized_new is UNPARSEABLE or normalized_current is UNPARSEABLE:
        return True
    return normalized_new != normalized_current


def _fk_pk(value: Any) -> Any:
    return getattr(value, "pk", value)


def field_changed(order: Any, field: str, new_value: Any) -> bool:
    if field == "items":
        return items_changed(order, new_value or [])
    if field in NESTED_FIELDS:
        return nested_changed(order, field, new_value)

    if field in FK_FIELDS:
        return _fk_pk(new_value) != getattr(order, f"{field}_id", None)

    if field in DATETIME_FIELDS:
        return _datetime_changed(order, field, new_value)

    current = getattr(order, field, None)
    if field in DATE_FIELDS:
        return new_value != current
    if field in BOOL_FIELDS:
        return bool(new_value) != bool(current)
    if field in DECIMAL_FIELDS:
        return norm_decimal(new_value) != norm_decimal(current)
    if field in INT_FIELDS:
        return norm_int(new_value) != norm_int(current)
    return norm_str(new_value) != norm_str(current)
```

Và `changed_fields` phải dịch tên field sang khoá validated_data:

```python
def changed_fields(order: Any, validated_data: dict[str, Any], fields: Iterable[str]) -> list[str]:
    """Các field trong `fields` mà payload ĐỔI THẬT giá trị, theo thứ tự `fields`.

    Trả về tên FIELD (theo catalog/serializer), không phải khoá validated_data —
    người dùng cần thấy "Kênh", không phải "channel".
    """
    result: list[str] = []
    for f in fields:
        key = VALIDATED_KEY_BY_FIELD.get(f, f)
        if key in validated_data and field_changed(order, key, validated_data[key]):
            result.append(f)
    return result
```

- [ ] **Step 4: Viết `nested_changed`**

`crm` ghi sang bản ghi Assessment chứ không phải cột của Order; `recall` và
`recall_entries` là quan hệ riêng. So theo chữ ký chuẩn hoá của từng field con:

```python
def _signature(source: Any, keys: Iterable[str]) -> tuple:
    """Chữ ký chuẩn hoá của một object/dict theo danh sách khoá."""
    def read(k: str) -> Any:
        return source.get(k) if isinstance(source, dict) else getattr(source, k, None)

    return tuple(norm_str(read(k)) for k in keys)


NESTED_KEYS: dict[str, tuple[str, ...]] = {
    "recall": ("recall_code", "recall_created_date", "recall_received_date"),
    "recall_entries": ("recall_code", "recall_created_date", "recall_received_date"),
}


def nested_changed(order: Any, field: str, new_value: Any) -> bool:
    if field == "recall_entries":
        keys = NESTED_KEYS[field]
        current = [_signature(e, keys) for e in order.recall_entries.all()]
        incoming = [_signature(e, keys) for e in (new_value or [])]
        return sorted(current) != sorted(incoming)

    if field == "recall":
        keys = NESTED_KEYS[field]
        current = getattr(order, "recall", None)
        if current is None and not new_value:
            return False
        if current is None or not new_value:
            return True
        return _signature(current, keys) != _signature(new_value, keys)

    # crm: so từng khoá gửi lên với giá trị tương ứng trên Assessment. Chỉ duyệt khoá
    # CÓ trong payload — payload crm là partial, khoá vắng mặt không phải "xoá".
    assessment = getattr(order, "assessment", None)
    for key, value in (new_value or {}).items():
        current = getattr(assessment, key, None) if assessment is not None else None
        if norm_str(value) != norm_str(current):
            return True
    return False
```

Trước khi viết, mở `OrderCRMInputSerializer` và model Assessment để xác nhận tên
thuộc tính (`order.assessment` hay related_name khác) — đoán sai ở đây là guard bắn
oan mọi lần lưu form.

- [ ] **Step 5: Chạy lại test**

Run: `... python -m pytest apps/orders/tests/test_order_field_diff.py -q --create-db`
Expected: PASS.

- [ ] **Step 6: Chạy lại test invoice_lock — không được vỡ**

Run: `... python -m pytest <các file ở Task 5 Step 1> -q --create-db`
Expected: PASS, đúng số test cũ.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/orders/field_diff.py backend/apps/orders/tests/test_order_field_diff.py
git commit -m "feat(orders): compare fk, datetime, boolean and nested order fields"
```

---

### Task 7: Guard quyền field ở endpoint sửa đơn

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_action_view.py` (sau khối `is_invoice_locked`, ~dòng 157)
- Modify: `backend/apps/orders/transport/views/order_views_impl/_view_helpers.py`
- Test: `backend/apps/orders/tests/test_order_field_permission.py`

**Interfaces:**
- Consumes: `EffectivePermissions.editable_fields` (Task 2), `changed_fields` (Task 6),
  `ALL_ORDER_FIELDS` + `ORDER_FIELD_LABELS` (Task 1)
- Produces: `order_editable_fields(user) -> frozenset[str]` trong `_view_helpers.py`

- [ ] **Step 1: Viết test hành vi (sẽ đỏ)**

Một file phủ ma trận, không rải test lẻ:

```python
# backend/apps/orders/tests/test_order_field_permission.py
"""Quyền sửa theo field trên PATCH /api/orders/{id}.

Ma trận: (có grant / không grant) × (đổi thật / gửi lại giá trị cũ) × các kiểu field.
"""
import pytest
from decimal import Decimal

from apps.users.models import Role, RoleFieldGrant, UserRoleAssignment


def grant(role, *fields):
    for f in fields:
        RoleFieldGrant.objects.create(role=role, section="orders", field=f)


@pytest.mark.django_db
def test_change_without_grant_is_rejected(sale_client, sale_role, order_with_items):
    res = sale_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={"total_amount": "999000"},
        content_type="application/json",
    )

    assert res.status_code == 403
    assert res.json()["blocked_fields"] == ["total_amount"]
    assert "Tổng tiền" in res.json()["detail"]
    order_with_items.refresh_from_db()
    assert order_with_items.total_amount != Decimal("999000")


@pytest.mark.django_db
def test_change_with_grant_is_allowed(sale_client, sale_role, order_with_items):
    grant(sale_role, "total_amount")

    res = sale_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={"total_amount": "999000"},
        content_type="application/json",
    )

    assert res.status_code == 200
    order_with_items.refresh_from_db()
    assert order_with_items.total_amount == Decimal("999000")


@pytest.mark.django_db
def test_resending_unchanged_value_without_grant_is_allowed(
    sale_client, sale_role, order_with_items
):
    """Form gửi full object: field không được cấp nhưng giá trị y nguyên → không chặn.
    Đây là case làm hỏng cả tính năng nếu làm sai — mọi lần lưu sẽ 403."""
    grant(sale_role, "feedback")

    res = sale_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={
            "feedback": "ghi chú mới",
            "total_amount": str(order_with_items.total_amount),
            "phone": order_with_items.phone,
        },
        content_type="application/json",
    )

    assert res.status_code == 200
    order_with_items.refresh_from_db()
    assert order_with_items.feedback == "ghi chú mới"


@pytest.mark.django_db
def test_blocked_fields_lists_every_violation(sale_client, sale_role, order_with_items):
    res = sale_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={"total_amount": "1", "phone": "0900000009"},
        content_type="application/json",
    )

    assert res.status_code == 403
    assert set(res.json()["blocked_fields"]) == {"total_amount", "phone"}


@pytest.mark.django_db
def test_channel_grant_uses_declared_field_name(sale_client, sale_role, order_with_items, another_channel):
    """channel_code là tên field; validated_data lại dùng khoá 'channel'. Cấp
    'channel_code' phải có tác dụng thật."""
    grant(sale_role, "channel_code")

    res = sale_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={"channel_code": another_channel.code},
        content_type="application/json",
    )

    assert res.status_code == 200


@pytest.mark.django_db
def test_superuser_bypasses_field_grants(admin_client, order_with_items):
    res = admin_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={"total_amount": "123000"},
        content_type="application/json",
    )

    assert res.status_code == 200


@pytest.mark.django_db
def test_items_change_without_grant_is_rejected(sale_client, sale_role, order_with_items):
    payload = [
        {"sku": i.sku, "quantity": i.quantity + 1} for i in order_with_items.items.all()
    ]

    res = sale_client.patch(
        f"/api/orders/{order_with_items.id}",
        data={"items": payload},
        content_type="application/json",
    )

    assert res.status_code == 403
    assert res.json()["blocked_fields"] == ["items"]
```

Fixture `sale_client` (user có `orders:edit`, gán `sale_role`, không grant field nào)
và `sale_role`: thêm vào `backend/apps/orders/tests/conftest.py` nếu chưa có, theo
khuôn fixture phân quyền sẵn có trong file.

- [ ] **Step 2: Chạy test cho chắc là đỏ**

Run: `... python -m pytest apps/orders/tests/test_order_field_permission.py -q --create-db`
Expected: FAIL — mọi PATCH trả 200, chưa có guard.

- [ ] **Step 3: Thêm helper đọc grant**

Trong `_view_helpers.py`, cạnh `order_field_policy`:

```python
def order_editable_fields(user) -> frozenset:
    """Field user được SỬA trên đơn. Superuser → toàn bộ catalog.

    Trả frozenset rỗng nghĩa là không sửa được gì (fail-closed) — đúng ý đồ với role
    chưa được cấp, KHÔNG phải lỗi cấu hình cần fallback.
    """
    from apps.users.rbac import resolve_effective_permissions
    from apps.users.rbac_catalog import ALL_ORDER_FIELDS

    eff = resolve_effective_permissions(user)
    if eff.is_superuser:
        return ALL_ORDER_FIELDS
    return eff.editable_fields.get("orders", frozenset())
```

- [ ] **Step 4: Cắm guard vào view**

Trong `order_action_view.py`, ngay **sau** khối `if is_invoice_locked(order):` và
**trước** `try:`:

```python
    # Quyền sửa theo field (allow-list, fail-closed). Đặt sau is_valid() để so trên
    # validated_data đã coerce type, và so GIÁ TRỊ chứ không so sự có mặt của field:
    # form đơn gửi lại full object nên chặn theo sự có mặt sẽ 403 oan mọi lần lưu.
    # Cùng khuôn hai guard ngay trên (staff_id, invoice_lock).
    #
    # Guard đặt ở view nên chỉ chặn hành vi NGƯỜI DÙNG: job nền, sync KiotViet, quét
    # kho đi qua service nên không bị chặn — chủ ý, xem spec mục "Ngoài phạm vi".
    editable = order_editable_fields(request.user)
    ungranted = [f for f in ALL_ORDER_FIELDS if f not in editable]
    blocked = changed_fields(order, serializer.validated_data, sorted(ungranted))
    if blocked:
        logger.warning(
            "field_grant blocked user=%s order=%s fields=%s",
            request.user.id, order.id, blocked,
        )
        return Response(
            {
                "detail": (
                    "Bạn không có quyền sửa: "
                    f"{', '.join(ORDER_FIELD_LABELS.get(f, f) for f in blocked)}."
                ),
                "blocked_fields": blocked,
            },
            status=status.HTTP_403_FORBIDDEN,
        )
```

Import thêm ở đầu file: `order_editable_fields` từ `._view_helpers`,
`changed_fields` từ `apps.orders.field_diff`,
`ALL_ORDER_FIELDS, ORDER_FIELD_LABELS` từ `apps.users.rbac_catalog`, và `logger`
(dùng `logger` sẵn có nếu file đã khai, không thì `logger = logging.getLogger(__name__)`).

- [ ] **Step 5: Chạy lại test**

Run: `... python -m pytest apps/orders/tests/test_order_field_permission.py -q --create-db`
Expected: PASS.

- [ ] **Step 6: Chạy rộng để bắt hồi quy — bước quan trọng nhất của task này**

Guard này nằm trên đường ghi mà rất nhiều test đơn đi qua. Fail-closed nghĩa là **mọi
test sửa đơn bằng user không phải superuser sẽ 403** nếu fixture chưa cấp grant.

Run: `... python -m pytest apps/orders -q --create-db`
Expected: PASS.

Nếu có test đỏ vì 403: đó là **tín hiệu đúng của thiết kế**, không phải bug. Sửa
fixture của test đó để cấp grant tương ứng (hoặc dùng user superuser nếu ý định ban
đầu là "admin sửa"). Tuyệt đối không nới guard cho test xanh.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/orders/transport/views/order_views_impl/order_action_view.py \
        backend/apps/orders/transport/views/order_views_impl/_view_helpers.py \
        backend/apps/orders/tests/test_order_field_permission.py \
        backend/apps/orders/tests/conftest.py
git commit -m "feat(orders): enforce per-field edit permission on inline update"
```

---

### Task 8: Frontend — thực thi trên bảng đơn và form đơn

**Files:**
- Modify: `backend/apps/users/serializers.py` (`UserSerializer`)
- Create: `frontend/src/hooks/useOrderFieldGrants.ts`
- Modify: `frontend/src/features/orders/OrdersTable/OrderCell.tsx`
- Modify: `frontend/src/features/orders/OrderFormDialog.tsx`
- Test: `frontend/src/features/orders/__tests__/field-grant-gate.test.tsx`

**Interfaces:**
- Consumes: `editable_order_fields` trên payload user
- Produces: `useOrderFieldGrants(): (field: string) => boolean`;
  `canEditOrderField(grants: string[] | undefined, field: string): boolean` (thuần, test được)

- [ ] **Step 1: Backend trả danh sách field cho user**

Trong `UserSerializer` (`backend/apps/users/serializers.py`), thêm cạnh
`get_allowed_sections`:

```python
    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_editable_order_fields(self, obj) -> list[str]:
        eff = resolve_effective_permissions(obj)
        if eff.is_superuser:
            return sorted(ALL_ORDER_FIELDS)
        return sorted(eff.editable_fields.get(ORDER_GRANT_SECTION, frozenset()))
```

Khai `editable_order_fields = serializers.SerializerMethodField()` và thêm vào
`Meta.fields`.

Test backend nhanh trong `apps/users/tests/test_rbac_sections.py`:

```python
def test_user_payload_exposes_editable_order_fields(admin_client, db, staff_user, staff_role):
    RoleFieldGrant.objects.create(role=staff_role, section="orders", field="phone")

    res = admin_client.get(f"/api/users/{staff_user.id}")

    assert res.json()["editable_order_fields"] == ["phone"]
```

Run: `... python -m pytest apps/users/tests/test_rbac_sections.py -q --create-db`
Expected: PASS.

- [ ] **Step 2: Viết test frontend (sẽ đỏ)**

```tsx
// frontend/src/features/orders/__tests__/field-grant-gate.test.tsx
import { describe, expect, it } from "vitest";
import { canEditOrderField } from "@/hooks/useOrderFieldGrants";

describe("canEditOrderField", () => {
  it("cho sửa field được cấp", () => {
    expect(canEditOrderField(["phone", "total_amount"], "phone")).toBe(true);
  });

  it("chặn field không được cấp", () => {
    expect(canEditOrderField(["phone"], "total_amount")).toBe(false);
  });

  it("chặn hết khi chưa có grant nào", () => {
    expect(canEditOrderField([], "phone")).toBe(false);
  });

  it("field ngoài catalog thì không bị quản lý", () => {
    // backend chỉ gác đúng field trong catalog; FE siết hơn backend sẽ khoá nhầm
    // những ô mà API vẫn cho ghi.
    expect(canEditOrderField([], "shipping_type")).toBe(true);
  });

  it("payload user chưa nạp xong thì không khoá nhầm", () => {
    expect(canEditOrderField(undefined, "phone")).toBe(true);
  });
});
```

Run: `npx vitest run src/features/orders/__tests__/field-grant-gate.test.tsx`
Expected: FAIL — không import được module.

- [ ] **Step 3: Viết hook**

```ts
// frontend/src/hooks/useOrderFieldGrants.ts
import { useAppShell } from "@/app/AppShellContext";

/** Field đơn nằm trong phạm vi quản lý của quyền sửa-theo-field (khớp catalog backend). */
const MANAGED_FIELDS = new Set([
  "name", "phone", "customer_gender",
  "address", "raw_address", "province", "district", "ward",
  "province_code", "district_code", "ward_code", "kiotviet_location", "kiotviet_ward",
  "items", "sku", "quantity",
  "total_amount", "total_after_discount", "discount_amount",
  "booking_voucher_discount", "other_price", "customer_prepaid", "shipping_fee",
  "shipping_code", "weight", "dimensions", "fulfillment_warehouse", "branch_id",
  "status", "result", "feedback", "customer_return", "return_date", "crm",
  "invoice_code", "invoice_id", "vat", "is_export_vat", "vat_link",
  "payment_method", "order_code",
  "order_type", "koc_order_type", "order_source", "channel_code",
  "kiotviet_channel_id", "recall", "recall_entries", "created_at",
]);

/**
 * Quy tắc thuần — pure, test được.
 *
 * `undefined` = payload user chưa nạp xong → KHÔNG khoá. Khoá lúc chưa biết sẽ làm
 * ô nhập nhấp nháy disable→enable mỗi lần tải trang.
 *
 * Field ngoài catalog → không bị quản lý. FE siết hơn backend là khoá nhầm ô mà API
 * vẫn cho ghi.
 */
export function canEditOrderField(grants: string[] | undefined, field: string): boolean {
  if (grants === undefined) return true;
  if (!MANAGED_FIELDS.has(field)) return true;
  return grants.includes(field);
}

/** Hook: trả callback canEditField(field) đọc grant của user trong AppShell. */
export function useOrderFieldGrants(): (field: string) => boolean {
  const { user } = useAppShell();
  return (field) => canEditOrderField(user?.editable_order_fields, field);
}
```

`MANAGED_FIELDS` trùng lặp catalog backend là chủ ý ở v1: nạp catalog qua API chỉ để
dựng UI cấu hình, còn ô nhập cần biết ngay lúc render đầu tiên. Task 10 thêm test
đối chiếu hai danh sách để chúng không trôi khỏi nhau.

- [ ] **Step 4: Chạy lại test**

Run: `npx vitest run src/features/orders/__tests__/field-grant-gate.test.tsx`
Expected: PASS — 5 test.

- [ ] **Step 5: Cắm vào `OrderCell.tsx`**

Trong component, gọi hook một lần ở đầu (cạnh chỗ lấy `canAssignSale`):

```tsx
  const canEditField = useOrderFieldGrants();
```

Với mỗi `case` có `canEdit`, thêm điều kiện. Cột nào tên khác tên field API thì tra
qua map đặt cạnh component:

```tsx
/** Cột hiển thị → field API. Cột không có trong map thì trùng tên. */
const FIELD_BY_COLUMN: Record<string, string> = {
  kiotviet: "kiotviet_channel_id",
  channel_owner: "channel_code",
};
```

rồi trong từng case đổi:

```tsx
      const canEdit = !!row.can_edit && !!onPatch
        && canEditField(FIELD_BY_COLUMN[column] ?? column);
```

Áp cho các case: `branch_id`, `channel_code`, `name`, `phone`, `address`,
`raw_address`, `kiotviet_location`, `kiotviet_ward`, `sku`, `quantity`,
`total_amount`, `total_after_discount`, `customer_prepaid`, `payment_method`,
`invoice_code`, `shipping_code`, `status`, `customer_gender`, `vat`,
`is_export_vat`, `vat_link`, `order_type`, `created_at`, `return_date`,
`fulfillment_warehouse`, `koc_order_type`, `kiotviet`, `channel_owner`.

**Không** áp cho `staff` (đã có `canAssignSale`), `kiotviet_state`, `invoice_status`,
`customer_code`, `customer_type`, `shipping_type` — không thuộc catalog.

Lấy quyền từ hook context, **không đọc ref trong thân render**: React Compiler đóng
băng giá trị (đã cắn ở nút Xuất Excel) và vitest không có compiler nên test không
bắt được.

- [ ] **Step 6: Cắm vào `OrderFormDialog.tsx`**

Hai việc, việc thứ hai quan trọng hơn:

1. `disabled={!canEditField("<field>")}` cho từng ô nhập, kèm `title` = "Bạn không
   có quyền sửa trường này".
2. **Loại field không được cấp khỏi payload lúc submit** — form gửi lại full object,
   để nguyên thì mỗi lần comparator backend so sai một field lồng nhau là user ăn
   403 vô cớ:

```tsx
  const buildPayload = (values: OrderFormValues) =>
    Object.fromEntries(
      Object.entries(values).filter(([key]) => canEditField(key)),
    );
```

Áp cho nhánh **sửa** đơn; nhánh **tạo** đơn giữ nguyên toàn bộ payload (quyền field
không áp cho luồng tạo — xem spec).

- [ ] **Step 7: Chạy test frontend + typecheck**

Run: `npx vitest run src/features/orders && npx tsc --noEmit && npm run lint:strict`
Expected: PASS, không lỗi type, không lỗi lint.

- [ ] **Step 8: Commit**

```bash
git add backend/apps/users/serializers.py backend/apps/users/tests/test_rbac_sections.py \
        frontend/src/hooks/useOrderFieldGrants.ts \
        frontend/src/features/orders/OrdersTable/OrderCell.tsx \
        frontend/src/features/orders/OrderFormDialog.tsx \
        frontend/src/features/orders/__tests__/field-grant-gate.test.tsx
git commit -m "feat(orders): disable ungranted fields in table and form"
```

---

### Task 9: Frontend — tab cấu hình ở trang Phân quyền

**Files:**
- Modify: `frontend/src/lib/api/roles.ts`
- Modify: `frontend/src/pages/RolesPage.tsx`
- Test: `frontend/src/pages/__tests__/roles-field-grants.test.tsx`

**Interfaces:**
- Consumes: `GET /api/rbac/order-field-catalog` (Task 4), `field_grants` trên role (Task 3)
- Produces: `toggleGroup(current: string[], groupFields: string[]): string[]` (thuần, test được)

- [ ] **Step 1: Viết test cho logic tick nhóm (sẽ đỏ)**

```tsx
// frontend/src/pages/__tests__/roles-field-grants.test.tsx
import { describe, expect, it } from "vitest";
import { groupState, toggleGroup } from "@/pages/RolesPage";

const MONEY = ["total_amount", "discount_amount", "shipping_fee"];

describe("toggleGroup", () => {
  it("nhóm trống → tick là cấp cả nhóm", () => {
    expect(toggleGroup([], MONEY).sort()).toEqual([...MONEY].sort());
  });

  it("nhóm đủ → tick là bỏ cả nhóm", () => {
    expect(toggleGroup(MONEY, MONEY)).toEqual([]);
  });

  it("nhóm một phần → tick là cấp nốt phần còn lại", () => {
    expect(toggleGroup(["total_amount"], MONEY).sort()).toEqual([...MONEY].sort());
  });

  it("không đụng field của nhóm khác", () => {
    expect(toggleGroup(["phone"], MONEY).sort()).toEqual([...MONEY, "phone"].sort());
  });
});

describe("groupState", () => {
  it("phân biệt đủ / một phần / trống", () => {
    expect(groupState(MONEY, MONEY)).toBe("all");
    expect(groupState(["total_amount"], MONEY)).toBe("partial");
    expect(groupState([], MONEY)).toBe("none");
  });
});
```

Run: `npx vitest run src/pages/__tests__/roles-field-grants.test.tsx`
Expected: FAIL — không export.

- [ ] **Step 2: Viết hai hàm thuần và export từ `RolesPage.tsx`**

```tsx
export type GroupState = "all" | "partial" | "none";

export function groupState(granted: string[], groupFields: string[]): GroupState {
  const hit = groupFields.filter((f) => granted.includes(f)).length;
  if (hit === 0) return "none";
  return hit === groupFields.length ? "all" : "partial";
}

/** Tick nhóm: đủ → bỏ hết; trống hoặc một phần → cấp hết. Không đụng nhóm khác. */
export function toggleGroup(granted: string[], groupFields: string[]): string[] {
  if (groupState(granted, groupFields) === "all") {
    return granted.filter((f) => !groupFields.includes(f));
  }
  return Array.from(new Set([...granted, ...groupFields]));
}
```

- [ ] **Step 3: Chạy lại test**

Run: `npx vitest run src/pages/__tests__/roles-field-grants.test.tsx`
Expected: PASS — 5 test.

- [ ] **Step 4: Thêm client API**

Trong `frontend/src/lib/api/roles.ts`: thêm `field_grants: string[]` vào type `Role`
và payload ghi; thêm `fetchOrderFieldCatalog()` gọi
`GET /api/rbac/order-field-catalog`, trả
`{ groups: { code: string; label: string; fields: { name: string; label: string }[] }[] }`.

- [ ] **Step 5: Dựng tab**

Thêm state `draftFieldGrants: string[]` theo đúng khuôn `draftFieldRules` sẵn có
(khởi tạo trong `useEffect` từ `selected?.field_grants ?? []`, đưa vào so sánh
`isDirty`, gửi kèm khi lưu ở hàm save ~dòng 328).

Panel: accordion 8 nhóm từ catalog, mỗi nhóm có checkbox 3 trạng thái + badge
`[đã cấp/tổng]`, mở ra là checkbox từng field. Nút *Chọn tất cả* / *Bỏ hết*. Dùng
`Checkbox`, `Accordion`, `Badge` của shadcn/ui đang có trong repo — không tự dựng.

Thêm cảnh báo khi role có `orders:edit` mà `draftFieldGrants.length === 0`:

```tsx
{draftCodes.includes("orders:edit") && draftFieldGrants.length === 0 && (
  <div className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
    <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
    <span>
      Vai trò này sửa được đơn nhưng chưa được cấp field nào — người dùng sẽ không
      sửa được gì.
    </span>
  </div>
)}
```

- [ ] **Step 6: Dọn tab "Quy tắc field" cũ**

Đổi nhãn tab thành "Ẩn field khi xem" và bỏ `readonly` khỏi `RULE_LABELS` (chỉ còn
`hidden`). Allow-list đã thay hoàn toàn vai trò của `readonly`; để hai cần gạt cùng
điều khiển một cửa là nguồn của lỗi cấu hình. Prod đang 0 dòng nên không mất gì.

Backend: giữ nguyên `FieldRuleType.READONLY` trong model (dữ liệu cũ nếu có vẫn đọc
được), chỉ bỏ khỏi UI.

- [ ] **Step 7: Chạy test + typecheck**

Run: `npx vitest run src/pages && npx tsc --noEmit && npm run lint:strict`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/api/roles.ts frontend/src/pages/RolesPage.tsx \
        frontend/src/pages/__tests__/roles-field-grants.test.tsx
git commit -m "feat(roles): add order field grant configuration tab"
```

---

### Task 10: Migration seed, chống trôi catalog, và runbook

**Files:**
- Create: `backend/apps/users/migrations/0036_seed_field_grants.py`
- Create: `backend/apps/users/tests/test_field_grant_seed.py`
- Modify: `frontend/src/hooks/useOrderFieldGrants.ts` (không đổi code, chỉ là đối tượng của test)
- Create: `frontend/src/hooks/__tests__/managed-fields-sync.test.ts`
- Modify: `backend/schema.yaml`
- Create: `docs/order-field-permission-runbook.md`

- [ ] **Step 1: Viết test cho migration seed (sẽ đỏ)**

```python
# backend/apps/users/tests/test_field_grant_seed.py
"""Migration seed: role đang sửa được đơn phải giữ nguyên quyền sau khi lên fail-closed.

Không seed = ngày deploy cả công ty mất quyền sửa đơn.

Gọi thẳng hàm `seed` của module migration với registry model thật: migration đã chạy
lúc dựng test DB nên bảng đã có dữ liệu seed sẵn; test xoá sạch rồi gọi lại để kiểm
đúng logic chọn role, không phụ thuộc trạng thái seed lúc migrate.
"""
import importlib

import pytest
from django.apps import apps as django_apps

from apps.users.models import Permission, Role, RoleFieldGrant, RolePermission
from apps.users.rbac_catalog import ALL_ORDER_FIELDS

seed_migration = importlib.import_module("apps.users.migrations.0036_seed_field_grants")


def _role_with_permission(name: str, code: str) -> Role:
    role = Role.objects.create(name=name)
    section, action = code.split(":", 1)
    permission, _ = Permission.objects.get_or_create(
        code=code, defaults={"section": section, "action": action, "label": code}
    )
    RolePermission.objects.create(role=role, permission=permission)
    return role


@pytest.mark.django_db
def test_seed_grants_every_catalog_field_to_order_editing_roles():
    editor = _role_with_permission("Role sửa đơn", "orders:edit")
    viewer = _role_with_permission("Role chỉ xem", "orders:view")
    RoleFieldGrant.objects.all().delete()

    seed_migration.seed(django_apps, None)

    granted = set(
        RoleFieldGrant.objects.filter(role=editor).values_list("field", flat=True)
    )
    assert granted == set(ALL_ORDER_FIELDS)
    assert not RoleFieldGrant.objects.filter(role=viewer).exists()


@pytest.mark.django_db
def test_seed_covers_shipments_edit_role():
    """Phòng Kho có shipments:edit và PATCH đơn qua cùng endpoint — bỏ sót là kho
    mất quyền sửa mã vận đơn ngay ngày deploy."""
    warehouse = _role_with_permission("Role kho", "shipments:edit")
    RoleFieldGrant.objects.all().delete()

    seed_migration.seed(django_apps, None)

    assert RoleFieldGrant.objects.filter(role=warehouse).count() == len(ALL_ORDER_FIELDS)


@pytest.mark.django_db
def test_seed_is_rerunnable():
    """`ignore_conflicts=True` phải chịu được chạy lại — migration replay không vỡ."""
    _role_with_permission("Role sửa đơn 2", "orders:edit")
    RoleFieldGrant.objects.all().delete()

    seed_migration.seed(django_apps, None)
    before = RoleFieldGrant.objects.count()
    seed_migration.seed(django_apps, None)

    assert RoleFieldGrant.objects.count() == before
```

Tên module migration bắt đầu bằng chữ số nên **phải** import bằng `importlib`, cú
pháp `from ... import` sẽ lỗi cú pháp.

- [ ] **Step 2: Viết migration**

```python
# backend/apps/users/migrations/0036_seed_field_grants.py
"""Cấp đủ field cho role đang có quyền sửa đơn — giữ nguyên hành vi ngày deploy.

Fail-closed mà không seed = 7 role / 36 user mất quyền sửa đơn ngay giây roll code.
Cấp bằng migration thay vì cấp tay (như đợt sale-assignment 2026-08-07) vì ở đây là
~50 field × 7 role = ~350 dòng: cấp tay là cầm chắc sai sót và downtime.

Role tạo MỚI sau migration này: 0 grant → fail-closed đúng thiết kế.
"""
from django.db import migrations

ORDER_EDIT_CODES = (
    "orders:edit", "landing-orders:edit", "koc-orders:edit", "shipments:edit",
)


def seed(apps, schema_editor):
    from apps.users.rbac_catalog import ALL_ORDER_FIELDS

    Role = apps.get_model("users", "Role")
    RoleFieldGrant = apps.get_model("users", "RoleFieldGrant")

    role_ids = set(
        Role.objects.filter(
            role_permissions__permission__code__in=ORDER_EDIT_CODES
        ).values_list("id", flat=True)
    )
    RoleFieldGrant.objects.bulk_create(
        [
            RoleFieldGrant(role_id=rid, section="orders", field=f)
            for rid in sorted(role_ids)
            for f in sorted(ALL_ORDER_FIELDS)
        ],
        ignore_conflicts=True,
    )


def unseed(apps, schema_editor):
    RoleFieldGrant = apps.get_model("users", "RoleFieldGrant")
    RoleFieldGrant.objects.filter(section="orders").delete()


def bump_cache(apps, schema_editor):
    from apps.users.rbac import bump_rbac_cache_version

    bump_rbac_cache_version()


class Migration(migrations.Migration):
    dependencies = [("users", "0035_role_field_grant")]

    operations = [
        migrations.RunPython(seed, unseed),
        migrations.RunPython(bump_cache, migrations.RunPython.noop),
    ]
```

`ignore_conflicts=True` để migration chạy lại không vỡ vì `unique_together`.

Điều chỉnh test ở Step 1 cho khớp: gọi hàm `seed` của module migration (import qua
`importlib` như các test migration sẵn có trong repo — tìm bằng
`grep -rl "migrations import\|importlib" backend/apps/*/tests`).

- [ ] **Step 3: Chạy test seed**

Run: `... python -m pytest apps/users/tests/test_field_grant_seed.py -q --create-db`
Expected: PASS.

- [ ] **Step 4: Test chống trôi giữa catalog backend và `MANAGED_FIELDS` frontend**

Danh sách ở `useOrderFieldGrants.ts` chép từ catalog backend; hai bên trôi khỏi nhau
là FE khoá nhầm hoặc bỏ sót ô.

```ts
// frontend/src/hooks/__tests__/managed-fields-sync.test.ts
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * Rút danh sách field từ catalog backend (nguồn sự thật) và so với MANAGED_FIELDS.
 *
 * Đọc khối ORDER_FIELD_LABELS chứ KHÔNG đọc ORDER_FIELD_GROUPS: khoá của dict labels
 * đúng bằng tập field, còn GROUPS trộn cả mã nhóm vào cùng dấu nháy nên phải lọc tay
 * — mà "address"/"status" vừa là mã nhóm vừa là tên field, lọc kiểu gì cũng sai một
 * trong hai. Task 1 đã có test khoá ALL_ORDER_FIELDS == set(ORDER_FIELD_LABELS), nên
 * đọc labels vẫn là đọc đúng nguồn sự thật.
 */
describe("MANAGED_FIELDS bám catalog backend", () => {
  it("không trôi khỏi ORDER_FIELD_LABELS", () => {
    const py = readFileSync("../backend/apps/users/rbac_catalog.py", "utf8");
    const labelsBlock = py.slice(
      py.indexOf("ORDER_FIELD_LABELS"),
      py.indexOf("ALL_ORDER_FIELDS"),
    );
    // chỉ lấy KHOÁ của dict: chuỗi đứng ngay trước dấu hai chấm
    const backendFields = Array.from(
      labelsBlock.matchAll(/"([a-z_]+)":/g),
      (m) => m[1],
    );

    const ts = readFileSync("src/hooks/useOrderFieldGrants.ts", "utf8");
    const feBlock = ts.slice(ts.indexOf("MANAGED_FIELDS"), ts.indexOf("]);"));
    const feFields = Array.from(feBlock.matchAll(/"([a-z_]+)"/g), (m) => m[1]);

    expect(backendFields.length).toBeGreaterThan(40); // regex hỏng thì phải đỏ, không phải xanh rỗng
    expect([...new Set(feFields)].sort()).toEqual([...new Set(backendFields)].sort());
  });
});
```

Dòng `toBeGreaterThan(40)` là chốt chặn: regex hỏng mà trả mảng rỗng thì hai vế đều
rỗng và test xanh giả — đúng loại test vô dụng mà vẫn báo an toàn.

Run: `npx vitest run src/hooks/__tests__/managed-fields-sync.test.ts`
Expected: PASS.

- [ ] **Step 5: Regen schema OpenAPI**

Chạy lệnh regen của repo (tìm trong `backend/Makefile` hoặc
`docs/` — đã có sự cố "schema drift" nên lệnh chuẩn nằm ở đó). `schema.yaml` đang cũ
sẵn ~444 dòng: **chỉ commit phần diff thuộc MR này** (endpoint catalog, `field_grants`
trên role, `editable_order_fields` trên user, response 403 mới). Phần nhiễu của
feature khác thì `git checkout -p` bỏ ra.

- [ ] **Step 6: Viết runbook**

`docs/order-field-permission-runbook.md`:

```markdown
# Runbook: quyền sửa field đơn hàng

## Thứ tự deploy — KHÔNG được đảo

1. `python manage.py migrate users` (0035 tạo bảng, 0036 seed + bump cache).
2. Roll code (backend + frontend).

Code cũ không biết bảng mới nên migrate trước là vô hại. Roll code trước khi migrate
= guard đọc bảng chưa tồn tại → 500 toàn bộ đường sửa đơn.

## Thứ tự rollback — NGƯỢC LẠI

1. Revert code trước.
2. Mới `migrate users 0034` (drop bảng).

Drop bảng khi code mới còn chạy = guard đọc rỗng = fail-closed = **toàn hệ thống
không sửa được đơn**. Đúng vết migration 0124 đã cắn.

## Kiểm tra sau deploy

- Vào Phân quyền → chọn "Phòng: Sales" → tab "Quyền sửa field đơn": phải thấy đủ 8
  nhóm đã tick.
- Sửa thử một đơn bằng tài khoản sale: phải lưu được như trước.
- `SELECT COUNT(*) FROM rbac_role_field_grants;` — kỳ vọng ≈ 49 × số role có quyền
  sửa đơn.

## Siết quyền (làm sau, không gấp)

Bỏ tick nhóm không thuộc phận sự của từng role. Hiệu lực ngay: mọi đường ghi role
đều gọi `bump_rbac_cache_version()`.

## Điều KHÔNG chặn được (cố ý, xem spec)

- Nút Huỷ đơn / Khôi phục đơn đổi `status` qua endpoint hành động riêng → khoá field
  `status` không chặn được. Muốn chặn phải siết quyền hành động.
- Tạo đơn và import Excel không áp quyền field (gate bằng `orders:create`).
- Job nền, sync KiotViet, quét kho không có `request.user` → không áp.
```

- [ ] **Step 7: Chạy toàn bộ suite liên quan trước khi đóng**

```bash
# backend
... python -m pytest apps/orders apps/users -q --create-db
# frontend
npm run test:ci && npx tsc --noEmit && npm run lint:strict
# lint backend
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

Expected: xanh, trừ các test đã đỏ sẵn trên baseline đo ở đầu phiên. So với con số
baseline đã ghi — **không** so với ghi chú cũ trong memory.

- [ ] **Step 8: Commit và push tạo MR**

```bash
git add backend/apps/users/migrations/0036_seed_field_grants.py \
        backend/apps/users/tests/test_field_grant_seed.py \
        frontend/src/hooks/__tests__/managed-fields-sync.test.ts \
        backend/schema.yaml docs/order-field-permission-runbook.md
git commit -m "chore: seed field grants for existing roles and add rollout runbook"
git push -o merge_request.create -o merge_request.title="Phan quyen sua tung field trong don hang" \
         -u origin feat/order-field-edit-permission
```

Gửi link MR cho user. **Một branch = một MR**: đã gửi link thì đóng băng branch này,
thay đổi mới đi MR mới.

---

## Ghi chú cho người thực thi

**Task 7 Step 6 là chỗ dễ mất thời gian nhất.** Fail-closed làm đỏ những test đơn
không liên quan gì tới tính năng này — chúng sửa đơn bằng user thường mà fixture chưa
có grant. Đó là tín hiệu đúng. Sửa fixture, đừng nới guard.

**Đừng tin `validated_data` có khoá trùng tên field.** `channel_code` → `channel`,
`kiotviet_channel_id` → `kiotviet_channel`. Sai chỗ này thì guard im lặng không bao
giờ bắn, và test chỉ kiểm "có quyền thì sửa được" vẫn xanh.

**Đừng sửa test của `invoice_lock`.** Task 5 là refactor thuần; test đó là lưới bảo
vệ, đỏ nghĩa là refactor đã đổi hành vi.
