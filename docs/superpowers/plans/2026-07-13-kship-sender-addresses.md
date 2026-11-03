# KShip Sender Address Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tách địa chỉ người gửi KShip (đang hardcode trong `service_actions.py`) ra bảng `kship_sender_addresses`, cho chọn địa chỉ trong dialog tạo vận đơn, quản lý CRUD gated `kiotviet:edit`.

**Architecture:** Model + CRUD endpoint trong `apps/orders` (theo precedent `KshipNotePreset`); resolver rule mặc định cũng ở `apps/orders` để `apps/kiotviet` import xuôi chiều layering (kiotviet → orders được phép, orders → kiotviet là nợ importlinter — KHÔNG thêm). `sender_address_id` đi qua serializer → `OrderKiotVietJob` → `ingest_job_tasks` → `service_actions`. FE thêm select trong `KshipDeliveryDialog` + dialog quản lý.

**Tech Stack:** Django/DRF + pytest; React + TypeScript + shadcn/ui + React Query; openapi-typescript.

**Spec:** `docs/superpowers/specs/2026-07-13-kship-sender-addresses-design.md` (đọc trước khi làm).

## Global Constraints

- Worktree: `C:/Users/MYPC/Desktop/workspace/desi-order-wt-kship-sender`, branch `feat/kship-sender-addresses`. LÀM VIỆC TRONG WORKTREE NÀY, không tạo branch mới, không đụng checkout chính.
- Commit message tiếng Anh, ngắn, KHÔNG kèm dòng `Co-Authored-By`.
- Backend test (Git Bash, chạy từ bất kỳ đâu — path tuyệt đối):
  ```bash
  MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
    --env-file c:/Users/MYPC/Desktop/workspace/desi-order-wt-kship-sender/.env \
    --env-file c:/Users/MYPC/Desktop/workspace/desi-order-wt-kship-sender/backend/.env \
    -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
    -e POSTGRES_DB=desi_kshipsender \
    -v "c:/Users/MYPC/Desktop/workspace/desi-order-wt-kship-sender/backend:/app" -w /app \
    desi-order-web-test:latest sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <PATHS> -q --create-db'
  ```
  Dùng `--create-db` ở run đầu sau khi thêm migration. KHÔNG bao giờ dùng `manage.py test`. KHÔNG bao giờ `cache.clear()` trong test.
- Ruff (host): `uv run --no-project --with ruff ruff check <files>` (`uv` ở `/c/Users/MYPC/.local/bin/uv`).
- FE: chạy trong `frontend/` của worktree; `npm install` trước nếu node_modules thiếu. Sau khi đổi BE schema: regen `schema.yaml` rồi `npm run gen:api` rồi **bắt buộc** `npx prettier --write src/api/schema.d.ts` (không prettier sẽ ra diff giả ~28k dòng).
- Import layering (backend/.importlinter): `apps.kiotviet` được import `apps.orders`; chiều ngược lại CẤM thêm mới.
- Không sửa file không liên quan; diff tối thiểu.

---

### Task 1: Model `KshipSenderAddress` + cột job + migration seed

**Files:**
- Modify: `backend/apps/orders/models.py` (thêm model sau class `KshipNotePreset` ~line 851; thêm field vào `OrderKiotVietJob` sau `kship_note` ~line 653)
- Create: `backend/apps/orders/migrations/0102_kship_sender_address.py` (số thứ tự: kiểm tra `ls backend/apps/orders/migrations/ | tail -3` — file mới = số lớn nhất + 1; nếu không phải 0102 thì đổi tên tương ứng)
- Test: `backend/apps/orders/tests/test_kship_sender_address_model.py`

**Interfaces:**
- Produces: model `apps.orders.models.KshipSenderAddress` (fields: `name`, `code`, `location_id`, `ward_id`, `address`, `mobile`, `sort_order`, `created_by`, `created_at`, `updated_at`); field `OrderKiotVietJob.kship_sender_address_id: int | None`; 2 row seed `code="van-quan"` (238/260) và `code="dai-mo"` (255/209).

- [ ] **Step 1: Viết test fail**

```python
# backend/apps/orders/tests/test_kship_sender_address_model.py
"""Bảng địa chỉ người gửi KShip: seed migration phải tạo đúng 2 bản ghi hardcode cũ."""
from __future__ import annotations

import pytest

from apps.orders.models import KshipSenderAddress, OrderKiotVietJob

pytestmark = pytest.mark.django_db


def test_seed_rows_match_legacy_hardcode():
    van_quan = KshipSenderAddress.objects.get(code="van-quan")
    assert (van_quan.location_id, van_quan.ward_id) == (238, 260)
    assert van_quan.address == "số 60 Đại Mỗ"
    assert van_quan.mobile == "+84 359 529 280"

    dai_mo = KshipSenderAddress.objects.get(code="dai-mo")
    assert (dai_mo.location_id, dai_mo.ward_id) == (255, 209)


def test_job_has_sender_address_id_field():
    field = OrderKiotVietJob._meta.get_field("kship_sender_address_id")
    assert field.null is True
```

- [ ] **Step 2: Chạy test xác nhận fail**

Run (thay `<PATHS>` trong lệnh docker ở Global Constraints): `apps/orders/tests/test_kship_sender_address_model.py`
Expected: FAIL `ImportError: cannot import name 'KshipSenderAddress'`

- [ ] **Step 3: Thêm model + field**

Trong `backend/apps/orders/models.py`, ngay SAU class `KshipNotePreset`:

```python
class KshipSenderAddress(models.Model):
    """Địa chỉ điểm gửi hàng cho luồng tạo vận đơn KShip.

    location_id/ward_id là ID nội bộ KiotViet (KShip) — nhập tay khi thêm mới.
    `code` chỉ dành cho 2 bản ghi seed để rule mặc định theo kênh/branch trỏ vào;
    bản ghi thường để NULL (không lưu chuỗi rỗng để không vướng unique).
    """

    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50, unique=True, null=True, blank=True)
    location_id = models.IntegerField()
    ward_id = models.IntegerField()
    address = models.CharField(max_length=255)
    mobile = models.CharField(max_length=20)
    sort_order = models.IntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_kship_sender_addresses",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "kship_sender_addresses"
        ordering = ["sort_order", "id"]
        indexes = [
            models.Index(fields=["sort_order", "id"], name="kship_sender_sort_idx"),
        ]
```

Trong `OrderKiotVietJob`, ngay sau `kship_note = models.TextField(...)`:

```python
    # ID bản ghi KshipSenderAddress được chọn (không FK cứng: bản ghi bị xóa
    # không được chặn queue — resolver tự fallback rule mặc định).
    kship_sender_address_id = models.IntegerField(null=True, blank=True)
```

- [ ] **Step 4: Tạo migration + thêm seed**

Run makemigrations trong docker (bind mount Windows ghi được, không cần --user):

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file c:/Users/MYPC/Desktop/workspace/desi-order-wt-kship-sender/.env \
  --env-file c:/Users/MYPC/Desktop/workspace/desi-order-wt-kship-sender/backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "c:/Users/MYPC/Desktop/workspace/desi-order-wt-kship-sender/backend:/app" -w /app \
  desi-order-web-test:latest python manage.py makemigrations orders
```

Mở file migration vừa sinh, đổi tên file thành `<số>_kship_sender_address.py` nếu tên auto xấu, và thêm seed vào cuối `operations` (giữ nguyên các operation auto-sinh CreateModel/AddField):

```python
def seed_sender_addresses(apps, schema_editor):
    KshipSenderAddress = apps.get_model("orders", "KshipSenderAddress")
    rows = [
        {"code": "van-quan", "name": "Kho Văn Quán", "location_id": 238, "ward_id": 260, "sort_order": 0},
        {"code": "dai-mo", "name": "Kho Đại Mỗ", "location_id": 255, "ward_id": 209, "sort_order": 1},
    ]
    for row in rows:
        KshipSenderAddress.objects.get_or_create(
            code=row["code"],
            defaults={**row, "address": "số 60 Đại Mỗ", "mobile": "+84 359 529 280"},
        )


def unseed_sender_addresses(apps, schema_editor):
    KshipSenderAddress = apps.get_model("orders", "KshipSenderAddress")
    KshipSenderAddress.objects.filter(code__in=["van-quan", "dai-mo"]).delete()
```

và trong `operations`: `migrations.RunPython(seed_sender_addresses, unseed_sender_addresses),`

- [ ] **Step 5: Chạy lại test → PASS** (cùng lệnh Step 2, có `--create-db`)

- [ ] **Step 6: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/models.py backend/apps/orders/migrations/ backend/apps/orders/tests/test_kship_sender_address_model.py
cd C:/Users/MYPC/Desktop/workspace/desi-order-wt-kship-sender
git add backend/apps/orders/models.py backend/apps/orders/migrations/ backend/apps/orders/tests/test_kship_sender_address_model.py
git commit -m "feat(orders): kship sender address table + job column, seed legacy rows"
```

---

### Task 2: Resolver sender_info + wire vào service_actions

**Files:**
- Create: `backend/apps/orders/application/services/kship_sender_service.py`
- Modify: `backend/apps/kiotviet/infrastructure/services/service_actions.py:470-489` (thay block sender hardcode)
- Test: `backend/apps/orders/tests/test_kship_sender_resolver.py`

**Interfaces:**
- Consumes: model `KshipSenderAddress` (Task 1).
- Produces: `resolve_kship_sender_info(order, sender_address_id: int | None = None) -> dict` (keys: `location_id`, `ward_id`, `address`, `mobile`) và `resolve_default_sender_address(order) -> KshipSenderAddress | None` — Task 3 dùng hàm sau cho `default_id`.
- LƯU Ý layering: file resolver nằm ở `apps/orders`; `apps/kiotviet` import nó là chiều ĐƯỢC PHÉP. Không import gì từ `apps.kiotviet` trong file này.

- [ ] **Step 1: Viết test fail**

```python
# backend/apps/orders/tests/test_kship_sender_resolver.py
"""Resolver sender KShip: id được chọn → seed theo rule kênh/branch → hằng số fallback."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.orders.application.services.kship_sender_service import (
    default_sender_code_for_order,
    resolve_default_sender_address,
    resolve_kship_sender_info,
)
from apps.orders.models import KshipSenderAddress

pytestmark = pytest.mark.django_db


def _order(channel_old: bool, branch_id: str):
    return SimpleNamespace(
        id=1,
        branch_id=branch_id,
        kiotviet_channel=SimpleNamespace(old=channel_old),
    )


def test_rule_new_channel_is_van_quan():
    assert default_sender_code_for_order(_order(channel_old=False, branch_id="123")) == "van-quan"


def test_rule_legacy_dai_mo_branch_is_van_quan():
    assert default_sender_code_for_order(_order(channel_old=True, branch_id="1000000477")) == "van-quan"


def test_rule_old_channel_branch_89084_is_dai_mo():
    assert default_sender_code_for_order(_order(channel_old=True, branch_id="89084")) == "dai-mo"


def test_explicit_id_wins():
    row = KshipSenderAddress.objects.create(
        name="Kho test", location_id=999, ward_id=888, address="1 Test", mobile="0900000000"
    )
    info = resolve_kship_sender_info(_order(channel_old=False, branch_id="1"), row.id)
    assert info == {"location_id": 999, "ward_id": 888, "address": "1 Test", "mobile": "0900000000"}


def test_deleted_id_falls_back_to_seed_rule():
    info = resolve_kship_sender_info(_order(channel_old=False, branch_id="1"), 999999)
    assert (info["location_id"], info["ward_id"]) == (238, 260)


def test_no_id_uses_seed_by_rule():
    info = resolve_kship_sender_info(_order(channel_old=True, branch_id="89084"))
    assert (info["location_id"], info["ward_id"]) == (255, 209)


def test_seed_deleted_falls_back_to_constants():
    KshipSenderAddress.objects.filter(code="van-quan").delete()
    info = resolve_kship_sender_info(_order(channel_old=False, branch_id="1"))
    assert (info["location_id"], info["ward_id"]) == (238, 260)
    assert info["address"] == "số 60 Đại Mỗ"


def test_resolve_default_row():
    row = resolve_default_sender_address(_order(channel_old=False, branch_id="1"))
    assert row is not None and row.code == "van-quan"
```

- [ ] **Step 2: Chạy test → FAIL** (`ModuleNotFoundError: kship_sender_service`)

Run: `apps/orders/tests/test_kship_sender_resolver.py`

- [ ] **Step 3: Viết resolver**

```python
# backend/apps/orders/application/services/kship_sender_service.py
"""Resolve thông tin người gửi cho luồng tạo vận đơn KShip.

Ưu tiên: bản ghi được chọn từ dialog → bản ghi seed theo rule kênh/branch
lịch sử → hằng số hardcode cũ (safety net, log warning). Không bao giờ raise
vì thiếu config — thiếu địa chỉ gửi không được chặn việc tạo vận đơn.

Đặt ở apps/orders (không phải apps/kiotviet) để giữ chiều import layering:
kiotviet → orders được phép, orders → kiotviet là nợ importlinter.
"""
from __future__ import annotations

import logging

from apps.orders.models import KshipSenderAddress

logger = logging.getLogger(__name__)

# Rule lịch sử (nguyên văn từ service_actions.py trước refactor):
# kênh mới hoặc chi nhánh Đại Mỗ legacy ship từ Văn Quán; kênh cũ từ Đại Mỗ.
LEGACY_DAI_MO_BRANCH_ID = "1000000477"
LEGACY_DAI_MO_BRANCH_ID_2 = "89084"

_FALLBACK_BY_CODE = {
    "van-quan": {"location_id": 238, "ward_id": 260, "address": "số 60 Đại Mỗ", "mobile": "+84 359 529 280"},
    "dai-mo": {"location_id": 255, "ward_id": 209, "address": "số 60 Đại Mỗ", "mobile": "+84 359 529 280"},
}


def default_sender_code_for_order(order) -> str:
    """Trả code bản ghi seed mặc định ("van-quan" | "dai-mo") theo rule kênh/branch."""
    channel = getattr(order, "kiotviet_channel", None)
    is_new_channel = (
        channel is not None
        and not channel.old
        and str(order.branch_id) != LEGACY_DAI_MO_BRANCH_ID_2
    )
    is_legacy_dai_mo = str(order.branch_id) == LEGACY_DAI_MO_BRANCH_ID
    return "van-quan" if (is_new_channel or is_legacy_dai_mo) else "dai-mo"


def resolve_default_sender_address(order) -> KshipSenderAddress | None:
    """Bản ghi mặc định theo rule, hoặc None nếu seed đã bị xóa."""
    return KshipSenderAddress.objects.filter(code=default_sender_code_for_order(order)).first()


def _to_sender_info(row: KshipSenderAddress) -> dict:
    return {
        "location_id": row.location_id,
        "ward_id": row.ward_id,
        "address": row.address,
        "mobile": row.mobile,
    }


def resolve_kship_sender_info(order, sender_address_id: int | None = None) -> dict:
    """Resolve sender_info dict cho payload KShip (SENDER_LOCATION_ID/WARD_ID/ADDRESS/MOBILE)."""
    if sender_address_id:
        row = KshipSenderAddress.objects.filter(id=sender_address_id).first()
        if row is not None:
            return _to_sender_info(row)
        logger.warning(
            "kship_sender.selected_missing id=%s order=%s — dùng rule mặc định",
            sender_address_id,
            order.id,
        )

    code = default_sender_code_for_order(order)
    row = KshipSenderAddress.objects.filter(code=code).first()
    if row is not None:
        return _to_sender_info(row)

    logger.warning("kship_sender.seed_missing code=%s order=%s — dùng hằng số fallback", code, order.id)
    return dict(_FALLBACK_BY_CODE[code])
```

- [ ] **Step 4: Chạy test → PASS**

- [ ] **Step 5: Wire vào service_actions**

Trong `backend/apps/kiotviet/infrastructure/services/service_actions.py`, tìm block (hiện ở ~470-489):

```python
                # Get sender information from channel configuration.
                # Legacy "Đại Mỗ" branch + all new (non-old) channels ship from
                # location 238 / ward 260; older channels default to 255 / 209.
                LEGACY_DAI_MO_BRANCH_ID = "1000000477"
                LEGACY_DAI_MO_BRANCH_ID_2 = "89084"
                is_new_channel = not order.kiotviet_channel.old and str(order.branch_id) != LEGACY_DAI_MO_BRANCH_ID_2
                is_legacy_dai_mo = str(order.branch_id) == LEGACY_DAI_MO_BRANCH_ID
                if is_new_channel or is_legacy_dai_mo:
                    # Phường Văn Quán
                    default_location_id, default_ward_id = 238, 260
                else:
                    # Phường Đại Mỗ
                    default_location_id, default_ward_id = 255, 209

                sender_info = {
                    "location_id": getattr(channel, "sender_location_id", 0) or default_location_id,
                    "ward_id": getattr(channel, "sender_ward_id", 0) or default_ward_id,
                    "address": getattr(channel, "sender_address", "") or "số 60 Đại Mỗ",
                    "mobile": getattr(channel, "sender_mobile", "") or "+84 359 529 280",
                }
```

thay bằng:

```python
                # Sender lấy từ bảng kship_sender_addresses (id được chọn từ dialog,
                # fallback rule kênh/branch + hằng số lịch sử — xem kship_sender_service).
                from apps.orders.application.services.kship_sender_service import (
                    resolve_kship_sender_info,
                )

                sender_info = resolve_kship_sender_info(order, kwargs.get("sender_address_id"))
```

(Import cục bộ trong nhánh action — nhất quán với các import cục bộ khác trong file, tránh import vòng lúc load module.)

- [ ] **Step 6: Chạy test kiotviet hiện có để bảo đảm không vỡ**

Run: `apps/kiotviet/tests -q` — Expected: pass như baseline (nếu có fail, so với master trước khi kết luận).

- [ ] **Step 7: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/application/services/kship_sender_service.py backend/apps/kiotviet/infrastructure/services/service_actions.py backend/apps/orders/tests/test_kship_sender_resolver.py
git add -A backend/apps/orders/application backend/apps/kiotviet/infrastructure/services/service_actions.py backend/apps/orders/tests/test_kship_sender_resolver.py
git commit -m "feat(kship): resolve sender info from kship_sender_addresses table"
```

---

### Task 3: CRUD endpoint `/api/orders/kship-sender-addresses` + default_id

**Files:**
- Create: `backend/apps/orders/transport/views/order_views_impl/kship_sender_address_view.py`
- Modify: `backend/apps/orders/transport/views/order_views_impl/__init__.py`, `backend/apps/orders/views.py`, `backend/apps/orders/urls.py` (thêm export/route theo ĐÚNG chuỗi đăng ký của `list_create_kship_note_presets` — grep tên đó trong 3 file để thấy chỗ thêm)
- Test: `backend/apps/orders/tests/test_kship_sender_address_api.py`

**Interfaces:**
- Consumes: `resolve_default_sender_address` (Task 2), model Task 1.
- Produces: `GET /api/orders/kship-sender-addresses[?ids=1,2]` → `{items: [...], total, default_id}`; `POST` (201) body `{name, location_id, ward_id, address, mobile, sort_order?}`; `PATCH/DELETE /api/orders/kship-sender-addresses/<id>`. FE (Task 6) tiêu thụ đúng shape này qua schema.d.ts.

- [ ] **Step 1: Viết test fail**

```python
# backend/apps/orders/tests/test_kship_sender_address_api.py
"""CRUD địa chỉ gửi KShip: đọc = quyền orders, sửa = kiotviet:edit, GET trả default_id."""
from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import KshipSenderAddress, Order
from apps.users.test_helpers import make_user_with_permissions

pytestmark = pytest.mark.django_db

LIST_URL = "/api/orders/kship-sender-addresses"


@pytest.fixture
def viewer():
    return make_user_with_permissions("kship-addr-viewer", ["orders:view"])


@pytest.fixture
def editor():
    return make_user_with_permissions("kship-addr-editor", ["orders:view", "kiotviet:edit"])


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_viewer_can_list_but_not_mutate(viewer):
    client = _client(viewer)
    response = client.get(LIST_URL)
    assert response.status_code == 200
    codes = {item["code"] for item in response.json()["items"]}
    assert {"van-quan", "dai-mo"} <= codes

    create = client.post(
        LIST_URL,
        {"name": "Kho A", "location_id": 1, "ward_id": 2, "address": "1 A", "mobile": "0900"},
        format="json",
    )
    assert create.status_code == 403


def test_editor_can_crud(editor):
    client = _client(editor)
    created = client.post(
        LIST_URL,
        {"name": "Kho A", "location_id": 10, "ward_id": 20, "address": "1 A", "mobile": "0900000000"},
        format="json",
    )
    assert created.status_code == 201
    entry_id = created.json()["id"]
    assert created.json()["code"] is None  # code read-only, không set được qua API

    patched = client.patch(f"{LIST_URL}/{entry_id}", {"name": "Kho B"}, format="json")
    assert patched.status_code == 200
    assert patched.json()["name"] == "Kho B"

    deleted = client.delete(f"{LIST_URL}/{entry_id}")
    assert deleted.status_code == 204


def test_default_id_follows_channel_rule(viewer):
    channel = Channel.objects.create(code="kship-addr-ch", name="KShip Addr")
    kv_new = KiotVietChannel.objects.create(name="kv-new", retailer="r-new", old=False)
    order = Order.objects.create(
        channel=channel,
        order_code="ORD-KSHIP-ADDR-1",
        name="KH",
        phone="0911111111",
        total_amount="0",
        kiotviet_channel=kv_new,
        branch_id="123",
    )
    client = _client(viewer)
    response = client.get(f"{LIST_URL}?ids={order.id}")
    assert response.status_code == 200
    van_quan_id = KshipSenderAddress.objects.get(code="van-quan").id
    assert response.json()["default_id"] == van_quan_id


def test_default_id_null_without_ids(viewer):
    response = _client(viewer).get(LIST_URL)
    assert response.json()["default_id"] is None
```

Lưu ý: nếu `KiotVietChannel`/`Order` create thiếu field bắt buộc (NOT NULL không default), xem cách test hiện có tạo chúng (`grep -rn "KiotVietChannel.objects.create" backend/apps/` và `test_dispatch_cancelled_guard.py::_make_order`) rồi bổ sung field tối thiểu — KHÔNG đổi assert.

- [ ] **Step 2: Chạy test → FAIL** (404 vì route chưa tồn tại)

Run: `apps/orders/tests/test_kship_sender_address_api.py`

- [ ] **Step 3: Viết view**

```python
# backend/apps/orders/transport/views/order_views_impl/kship_sender_address_view.py
"""CRUD địa chỉ người gửi KShip.

Đọc: ai có quyền orders (cần chọn địa chỉ khi tạo vận đơn). Sửa: kiotviet:edit —
nhập sai location_id/ward_id là vận đơn tạo SAI ĐIỂM GỬI, nhạy hơn note preset.
GET nhận ?ids=<order_ids> để trả kèm default_id theo rule kênh/branch (đơn đầu lô).
"""
from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import ORDER_SECTIONS, require_permission, require_section

from apps.orders.application.services.kship_sender_service import resolve_default_sender_address
from apps.orders.models import KshipSenderAddress, Order


class KshipSenderAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = KshipSenderAddress
        fields = [
            "id",
            "name",
            "code",
            "location_id",
            "ward_id",
            "address",
            "mobile",
            "sort_order",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "code", "created_at", "updated_at"]

    def validate_name(self, value: str) -> str:
        name = (value or "").strip()
        if not name:
            raise serializers.ValidationError("Tên địa chỉ gửi không được để trống.")
        return name

    def validate_address(self, value: str) -> str:
        address = (value or "").strip()
        if not address:
            raise serializers.ValidationError("Địa chỉ không được để trống.")
        return address

    def validate_mobile(self, value: str) -> str:
        mobile = (value or "").strip()
        if not mobile:
            raise serializers.ValidationError("SĐT người gửi không được để trống.")
        return mobile

    def validate_location_id(self, value: int) -> int:
        if value is None or int(value) <= 0:
            raise serializers.ValidationError("location_id phải là số nguyên dương.")
        return int(value)

    def validate_ward_id(self, value: int) -> int:
        if value is None or int(value) <= 0:
            raise serializers.ValidationError("ward_id phải là số nguyên dương.")
        return int(value)


def _default_sender_address_id(request) -> int | None:
    raw = str(request.query_params.get("ids") or "").strip()
    if not raw:
        return None
    try:
        first_id = int(raw.split(",")[0])
    except (TypeError, ValueError):
        return None
    order = (
        Order.objects.select_related("kiotviet_channel")
        .filter(id=first_id, deleted_at__isnull=True)
        .first()
    )
    if order is None:
        return None
    row = resolve_default_sender_address(order)
    return row.id if row else None


@extend_schema(
    request=KshipSenderAddressSerializer,
    responses={
        200: inline_serializer(
            "KshipSenderAddressListResponse",
            fields={
                "items": KshipSenderAddressSerializer(many=True),
                "total": drf_serializers.IntegerField(),
                "default_id": drf_serializers.IntegerField(allow_null=True),
            },
        ),
        201: KshipSenderAddressSerializer,
    },
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS)])
def list_create_kship_sender_addresses(request):
    if request.method == "GET":
        items = list(KshipSenderAddress.objects.order_by("sort_order", "id"))
        data = KshipSenderAddressSerializer(items, many=True).data
        return Response({"items": data, "total": len(data), "default_id": _default_sender_address_id(request)})

    # Mutation gate chặt hơn GET: sai ID điểm gửi = vận đơn sai kho.
    if not require_permission("kiotviet:edit")().has_permission(request, None):
        return Response({"detail": "Bạn không có quyền quản lý địa chỉ gửi."}, status=status.HTTP_403_FORBIDDEN)

    serializer = KshipSenderAddressSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    entry = serializer.save(created_by=request.user if request.user.is_authenticated else None)
    return Response(KshipSenderAddressSerializer(entry).data, status=status.HTTP_201_CREATED)


@extend_schema(
    request=KshipSenderAddressSerializer,
    responses={
        200: KshipSenderAddressSerializer,
        204: OpenApiResponse(description="Đã xóa bản ghi"),
        404: OpenApiResponse(description="Không tìm thấy bản ghi"),
    },
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_permission("kiotviet:edit")])
def update_delete_kship_sender_address(request, entry_id: int):
    entry = KshipSenderAddress.objects.filter(id=entry_id).first()
    if entry is None:
        return Response({"detail": "Không tìm thấy bản ghi."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        entry.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = KshipSenderAddressSerializer(entry, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(KshipSenderAddressSerializer(entry).data, status=status.HTTP_200_OK)
```

- [ ] **Step 4: Đăng ký route**

Grep `list_create_kship_note_presets` trong `backend/apps/orders/` — nó xuất hiện ở 3 nơi: `transport/views/order_views_impl/__init__.py` (import + `__all__`), `views.py` (import + `__all__`), `urls.py` (import + `urlpatterns`). Thêm 2 hàm mới NGAY CẠNH từng chỗ tương ứng:

```python
# urls.py — cạnh 2 route kship-note-presets:
path("kship-sender-addresses", list_create_kship_sender_addresses, name="orders-kship-sender-addresses"),
path("kship-sender-addresses/<int:entry_id>", update_delete_kship_sender_address, name="orders-kship-sender-address-detail"),
```

- [ ] **Step 5: Chạy test → PASS**; chạy thêm `apps/orders/tests/test_kship_sender_address_model.py apps/orders/tests/test_kship_sender_resolver.py` cùng lượt.

- [ ] **Step 6: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/transport/views/order_views_impl/kship_sender_address_view.py backend/apps/orders/tests/test_kship_sender_address_api.py
git add -A backend/apps/orders
git commit -m "feat(orders): kship sender address CRUD endpoint with default_id"
```

---

### Task 4: `sender_address_id` xuyên suốt serializer → job → task

**Files:**
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/order_action_serializers.py:206-214` (class `OrderCreateKshipDeliverySerializer`)
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_crud_view.py` (view `create_kship_delivery`: validate + gắn vào job, ~line 250-345)
- Modify: `backend/apps/orders/infrastructure/tasks/order_tasks_impl/ingest_job_tasks.py:390-397` (truyền vào `execute_action`)
- Test: `backend/apps/orders/tests/test_kship_delivery_sender_address.py`

**Interfaces:**
- Consumes: field `OrderKiotVietJob.kship_sender_address_id` (Task 1); `service_actions` đọc `kwargs.get("sender_address_id")` (Task 2).
- Produces: API `POST /api/orders/kship-delivery` nhận `sender_address_id` optional; FE Task 7 gửi field này.

- [ ] **Step 1: Viết test fail**

```python
# backend/apps/orders/tests/test_kship_delivery_sender_address.py
"""sender_address_id đi từ API vào OrderKiotVietJob và tới execute_action."""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import KshipSenderAddress, Order, OrderKiotVietJob
from apps.users.test_helpers import make_admin

pytestmark = pytest.mark.django_db

URL = "/api/orders/kship-delivery"


def _make_order() -> Order:
    suffix = uuid.uuid4().hex[:8]
    channel = Channel.objects.create(code=f"kship-{suffix}", name=f"KShip {suffix}")
    kv = KiotVietChannel.objects.create(
        name=f"kv-{suffix}", retailer=f"r-{suffix}", old=False,
        username="u", password="p",
    )
    return Order.objects.create(
        channel=channel,
        order_code=f"ORD-{suffix}",
        name="KH",
        phone="0911111111",
        total_amount="0",
        kiotviet_channel=kv,
        invoice_code=f"HD-{suffix}",
    )


def _client() -> APIClient:
    client = APIClient()
    client.force_authenticate(user=make_admin(f"kship-sender-{uuid.uuid4().hex[:6]}"))
    return client


@patch("apps.orders.transport.views.order_views_impl.order_crud_view.dispatch")
def test_sender_address_id_saved_on_job(mock_dispatch):
    order = _make_order()
    row = KshipSenderAddress.objects.get(code="dai-mo")
    response = _client().post(
        URL,
        {"ids": [order.id], "provider_code": "BEST", "service_code": "STD", "sender_address_id": row.id},
        format="json",
    )
    assert response.status_code == 200, response.content
    job = OrderKiotVietJob.objects.get(order=order, action=OrderKiotVietJob.ACTION_CREATE_KSHIP_DELIVERY)
    assert job.kship_sender_address_id == row.id


@patch("apps.orders.transport.views.order_views_impl.order_crud_view.dispatch")
def test_unknown_sender_address_id_is_400(mock_dispatch):
    order = _make_order()
    response = _client().post(
        URL,
        {"ids": [order.id], "provider_code": "BEST", "service_code": "STD", "sender_address_id": 999999},
        format="json",
    )
    assert response.status_code == 400


@patch("apps.orders.transport.views.order_views_impl.order_crud_view.dispatch")
def test_omitted_sender_address_id_saves_null(mock_dispatch):
    order = _make_order()
    response = _client().post(
        URL,
        {"ids": [order.id], "provider_code": "BEST", "service_code": "STD"},
        format="json",
    )
    assert response.status_code == 200, response.content
    job = OrderKiotVietJob.objects.get(order=order, action=OrderKiotVietJob.ACTION_CREATE_KSHIP_DELIVERY)
    assert job.kship_sender_address_id is None
```

(Nếu view yêu cầu thêm điều kiện khác trả 4xx trước khi tạo job — vd thiếu username/password kênh — bổ sung field cho `KiotVietChannel` trong `_make_order` theo thông báo lỗi thực tế; giữ nguyên assert.)

Kiểm tra thêm pass-through trong `ingest_job_tasks`: thêm test sau vào cùng file:

```python
@patch("apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks.KiotVietService.execute_action")
def test_task_passes_sender_address_id(mock_execute):
    from apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks import process_kiotviet_job

    order = _make_order()
    row = KshipSenderAddress.objects.get(code="van-quan")
    job = OrderKiotVietJob.objects.create(
        order=order,
        action=OrderKiotVietJob.ACTION_CREATE_KSHIP_DELIVERY,
        kship_provider_code="BEST",
        kship_service_code="STD",
        kship_sender_address_id=row.id,
    )
    mock_execute.return_value = {"status": "success", "payload": {}}
    process_kiotviet_job(job_id=job.id)
    assert mock_execute.call_args.kwargs.get("sender_address_id") == row.id
```

(Tên hàm task/entry `process_kiotviet_job` — xác nhận bằng `grep -n "def process_kiotviet_job\|def .*kv.process_job" backend/apps/orders/infrastructure/tasks/order_tasks_impl/ingest_job_tasks.py`; nếu tên/chữ ký khác (vd nhận `task_id`), sửa lời gọi trong test theo chữ ký thật.)

- [ ] **Step 2: Chạy test → FAIL**

Run: `apps/orders/tests/test_kship_delivery_sender_address.py`

- [ ] **Step 3: Implement**

Serializer (`order_action_serializers.py`, trong `OrderCreateKshipDeliverySerializer` sau dòng `notes = ...`):

```python
    sender_address_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
```

View (`order_crud_view.py`): sau dòng `notes = serializer.validated_data.get("notes", {})`:

```python
    sender_address_id = serializer.validated_data.get("sender_address_id")
    if sender_address_id is not None and not KshipSenderAddress.objects.filter(id=sender_address_id).exists():
        return Response({"detail": "Địa chỉ gửi không tồn tại."}, status=status.HTTP_400_BAD_REQUEST)
```

(import `KshipSenderAddress` thêm vào dòng import `from apps.orders.models import ...` sẵn có của file). Trong list-comprehension `jobs_to_create` thêm:

```python
            kship_sender_address_id=sender_address_id,
```

Task (`ingest_job_tasks.py`, call `KiotVietService.execute_action`): thêm sau `kship_note=job.kship_note or "",`:

```python
            sender_address_id=job.kship_sender_address_id,
```

- [ ] **Step 4: Chạy test → PASS**

- [ ] **Step 5: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/transport/serializers/order_serializers_impl/order_action_serializers.py backend/apps/orders/transport/views/order_views_impl/order_crud_view.py backend/apps/orders/infrastructure/tasks/order_tasks_impl/ingest_job_tasks.py backend/apps/orders/tests/test_kship_delivery_sender_address.py
git add -A backend/apps/orders
git commit -m "feat(orders): pass sender_address_id through kship delivery job pipeline"
```

---

### Task 5: Regenerate OpenAPI schema + FE types

**Files:**
- Modify (generated): `backend/schema.yaml`, `frontend/src/api/schema.d.ts`

- [ ] **Step 1: Regen schema.yaml**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file c:/Users/MYPC/Desktop/workspace/desi-order-wt-kship-sender/.env \
  --env-file c:/Users/MYPC/Desktop/workspace/desi-order-wt-kship-sender/backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "c:/Users/MYPC/Desktop/workspace/desi-order-wt-kship-sender/backend:/app" -w /app \
  desi-order-web-test:latest python manage.py spectacular --file schema.yaml
```

Kiểm tra diff CHỈ chứa phần kship-sender-addresses + sender_address_id (git diff backend/schema.yaml). Diff lạ ngoài phạm vi → dừng, báo lại.

- [ ] **Step 2: Regen FE types + prettier (BẮT BUỘC)**

```bash
cd C:/Users/MYPC/Desktop/workspace/desi-order-wt-kship-sender/frontend
npm install
npm run gen:api
npx prettier --write src/api/schema.d.ts
git diff --stat src/api/schema.d.ts
```

Expected: diff nhỏ (vài chục–vài trăm dòng quanh kship), KHÔNG phải ~28k dòng (nếu 28k = quên prettier).

- [ ] **Step 3: Commit**

```bash
git add backend/schema.yaml frontend/src/api/schema.d.ts
git commit -m "chore(api): regenerate schema for kship sender addresses"
```

---

### Task 6: FE API module + React Query hook

**Files:**
- Modify: `frontend/src/lib/api/orders.ts` (thêm cuối file)
- Modify: `frontend/src/features/orders/queries.ts`
- Create: `frontend/src/features/orders/use-kship-sender-addresses.ts` (mutations)

**Interfaces:**
- Consumes: schema types từ Task 5 (`components["schemas"]["KshipSenderAddress"]`, `components["schemas"]["KshipSenderAddressListResponse"]` — xác nhận tên chính xác trong `src/api/schema.d.ts` sau regen; nếu drf-spectacular đặt tên khác thì dùng đúng tên sinh ra).
- Produces: `listKshipSenderAddresses(ids)`, `createKshipSenderAddress(body)`, `updateKshipSenderAddress(id, patch)`, `deleteKshipSenderAddress(id)` (lib/api); hook `useKshipSenderAddresses(ids, enabled)` (query key `orderKeys.kshipSenderAddresses(ids)`); hook `useKshipSenderAddressMutations()` trả `{create, update, remove}` đã invalidate query. Task 7 tiêu thụ đúng các tên này.

- [ ] **Step 1: lib/api/orders.ts — thêm cuối file**

```typescript
/** ---- KShip sender addresses (địa chỉ điểm gửi khi tạo vận đơn) ---- */

export type KshipSenderAddress = components["schemas"]["KshipSenderAddress"];
export type KshipSenderAddressListResponse =
  components["schemas"]["KshipSenderAddressListResponse"];

export type KshipSenderAddressInput = {
  name: string;
  location_id: number;
  ward_id: number;
  address: string;
  mobile: string;
  sort_order?: number;
};

/** GET /api/orders/kship-sender-addresses — kèm default_id theo lô đơn đang chọn. */
export async function listKshipSenderAddresses(
  ids?: number[],
): Promise<KshipSenderAddressListResponse> {
  const query = ids && ids.length > 0 ? `?ids=${ids.join(",")}` : "";
  return fetchJson(`/api/orders/kship-sender-addresses${query}`);
}

export async function createKshipSenderAddress(
  body: KshipSenderAddressInput,
): Promise<KshipSenderAddress> {
  return fetchJson("/api/orders/kship-sender-addresses", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function updateKshipSenderAddress(
  id: number,
  patch: Partial<KshipSenderAddressInput>,
): Promise<KshipSenderAddress> {
  return fetchJson(`/api/orders/kship-sender-addresses/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function deleteKshipSenderAddress(id: number): Promise<void> {
  await fetchJson(`/api/orders/kship-sender-addresses/${id}`, { method: "DELETE" });
}
```

(File đã import `fetchJson` và `components` sẵn ở đầu — kiểm tra, nếu `components` chưa import thì nó đang có từ `@/api/typed`.)

- [ ] **Step 2: queries.ts — thêm key + hook**

```typescript
// thêm vào orderKeys:
  kshipSenderAddresses: (ids: number[]) => ["orders", "kship-sender-addresses", ...ids] as const,

// thêm hook (import listKshipSenderAddresses từ "@/lib/api/orders"):
/** Danh sách địa chỉ gửi KShip + default_id theo lô đơn. Chỉ fetch khi dialog mở. */
export function useKshipSenderAddresses(ids: number[], enabled: boolean) {
  return useQuery({
    queryKey: orderKeys.kshipSenderAddresses(ids),
    queryFn: () => listKshipSenderAddresses(ids),
    enabled: enabled && ids.length > 0,
    staleTime: 60_000,
  });
}
```

- [ ] **Step 3: Hook mutations**

```typescript
// frontend/src/features/orders/use-kship-sender-addresses.ts
import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  createKshipSenderAddress,
  deleteKshipSenderAddress,
  updateKshipSenderAddress,
  type KshipSenderAddressInput,
} from "@/lib/api/orders";

/** Mutations CRUD địa chỉ gửi KShip; invalidate mọi query kship-sender-addresses. */
export function useKshipSenderAddressMutations() {
  const queryClient = useQueryClient();
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["orders", "kship-sender-addresses"] });

  const create = useMutation({
    mutationFn: (body: KshipSenderAddressInput) => createKshipSenderAddress(body),
    onSuccess: invalidate,
  });
  const update = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: Partial<KshipSenderAddressInput> }) =>
      updateKshipSenderAddress(id, patch),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: (id: number) => deleteKshipSenderAddress(id),
    onSuccess: invalidate,
  });

  return { create, update, remove };
}
```

- [ ] **Step 4: Test hook mutations** (theo pattern `__tests__/use-order-mutations.test.tsx` — renderHook + QueryClientProvider + mock lib/api)

```tsx
// frontend/src/features/orders/__tests__/use-kship-sender-addresses.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

vi.mock("@/lib/api/orders", () => ({
  createKshipSenderAddress: vi.fn(),
  updateKshipSenderAddress: vi.fn(),
  deleteKshipSenderAddress: vi.fn(),
}));
import {
  createKshipSenderAddress,
  deleteKshipSenderAddress,
  updateKshipSenderAddress,
} from "@/lib/api/orders";
import { useKshipSenderAddressMutations } from "@/features/orders/use-kship-sender-addresses";

const mockCreate = createKshipSenderAddress as ReturnType<typeof vi.fn>;
const mockUpdate = updateKshipSenderAddress as ReturnType<typeof vi.fn>;
const mockDelete = deleteKshipSenderAddress as ReturnType<typeof vi.fn>;

function setup() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  const { result } = renderHook(() => useKshipSenderAddressMutations(), { wrapper });
  return { result, invalidateSpy };
}

describe("useKshipSenderAddressMutations", () => {
  beforeEach(() => vi.clearAllMocks());

  it("create gọi API với body và invalidate danh sách địa chỉ khi thành công", async () => {
    mockCreate.mockResolvedValue({ id: 9 });
    const { result, invalidateSpy } = setup();

    const body = { name: "Kho A", location_id: 1, ward_id: 2, address: "1 A", mobile: "0900" };
    await result.current.create.mutateAsync(body);

    expect(mockCreate).toHaveBeenCalledWith(body);
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["orders", "kship-sender-addresses"],
      }),
    );
  });

  it("update gọi API (id, patch) và invalidate", async () => {
    mockUpdate.mockResolvedValue({ id: 9, name: "Kho B" });
    const { result, invalidateSpy } = setup();

    await result.current.update.mutateAsync({ id: 9, patch: { name: "Kho B" } });

    expect(mockUpdate).toHaveBeenCalledWith(9, { name: "Kho B" });
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalled());
  });

  it("remove gọi API (id) và invalidate", async () => {
    mockDelete.mockResolvedValue(undefined);
    const { result, invalidateSpy } = setup();

    await result.current.remove.mutateAsync(9);

    expect(mockDelete).toHaveBeenCalledWith(9);
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalled());
  });
});
```

Run: `cd frontend && npx vitest run src/features/orders/__tests__/use-kship-sender-addresses.test.tsx`
Expected: 3 pass.

- [ ] **Step 5: Typecheck + commit**

```bash
cd C:/Users/MYPC/Desktop/workspace/desi-order-wt-kship-sender/frontend
npm run typecheck
git add src/lib/api/orders.ts src/features/orders/queries.ts src/features/orders/use-kship-sender-addresses.ts src/features/orders/__tests__/use-kship-sender-addresses.test.tsx
git commit -m "feat(fe): kship sender address api module + query hooks"
```

---

### Task 7: Dialog select + manage dialog + wiring OrdersPage + i18n

**Files:**
- Modify: `frontend/src/features/orders/KshipDeliveryDialog.tsx` (thêm select "Địa chỉ gửi" vào toolbar "Áp dụng tất cả")
- Create: `frontend/src/features/orders/KshipSenderAddressManageDialog.tsx`
- Modify: `frontend/src/features/orders/use-kship-delivery.ts` (state senderAddressId + submit)
- Modify: `frontend/src/features/orders/use-orders-data.ts` (`createKshipDelivery` thêm param, ~line 1690)
- Modify: `frontend/src/pages/OrdersPage.tsx` (query + wiring + render manage dialog)
- Modify: `frontend/src/i18n/messages/orders.ts` (thêm key cạnh các key `kshipNote*` hiện có, cùng format file)

**Interfaces:**
- Consumes: `useKshipSenderAddresses`, `useKshipSenderAddressMutations`, types từ Task 6.
- Produces: body request `POST /api/orders/kship-delivery` có thêm `sender_address_id` (Task 4 đã nhận).

- [ ] **Step 1: `use-orders-data.ts` — thêm param cho `createKshipDelivery`**

Đổi chữ ký (giữ vị trí param cũ, thêm cuối):

```typescript
  const createKshipDelivery = useCallback(
    async (
      ids: number[],
      providerCode: string,
      serviceCode: string,
      notes?: Record<number, string>,
      senderAddressId?: number | null,
    ): Promise<{ created_count: number; detail: string }> => {
```

và trong body request:

```typescript
        body: JSON.stringify({
          ids: normalizedIds,
          provider_code: providerCode,
          service_code: serviceCode,
          notes: notesPayload,
          ...(typeof senderAddressId === "number" ? { sender_address_id: senderAddressId } : {}),
        }),
```

- [ ] **Step 2: `use-kship-delivery.ts` — state + submit**

1. Type `UseKshipDeliveryParams.onCreateKshipDelivery` thêm param thứ 5 `senderAddressId?: number | null` (khớp Step 1).
2. Thêm state + expose (thêm vào `UseKshipDeliveryReturn` và object return):

```typescript
  const [kshipSenderAddressId, setKshipSenderAddressId] = useState<number | null>(null);
```

3. Reset khi đóng dialog — thêm `setKshipSenderAddressId(null);` vào `useEffect` reset-on-close hiện có (cái set hàng loạt state về rỗng khi `createKshipCodeModalOpen` false).
4. `handleSubmitKshipDelivery`: truyền `kshipSenderAddressId` làm arg thứ 5 của `onCreateKshipDelivery(...)` và thêm vào dependency array của `useCallback`.

- [ ] **Step 3: `KshipDeliveryDialog.tsx` — select địa chỉ gửi**

Thêm props:

```typescript
  senderAddresses: { id: number; name: string; address: string }[];
  senderAddressId: number | null;
  onChangeSenderAddress: (id: number | null) => void;
  canManageSenderAddresses: boolean;
  onOpenManageSenderAddresses: () => void;
```

Trong toolbar "Áp dụng tất cả" (div `flex flex-wrap items-end gap-2 rounded-md border ...`), thêm block ĐẦU TIÊN (trước select Nhà vận chuyển). Import thêm `Settings2` từ `lucide-react`:

```tsx
            <div className="min-w-[220px] flex-1">
              <label className="mb-1 block text-xs font-medium text-muted-foreground">
                {t("orders.kiotviet.kshipSenderAddress", "Địa chỉ gửi")}
              </label>
              <div className="flex gap-1">
                <Select
                  value={senderAddressId !== null ? String(senderAddressId) : ""}
                  onValueChange={(value) => onChangeSenderAddress(value ? Number(value) : null)}
                >
                  <SelectTrigger className="w-full">
                    {(() => {
                      const selected = senderAddresses.find((a) => a.id === senderAddressId);
                      return selected ? (
                        <span className="truncate">{selected.name}</span>
                      ) : (
                        <SelectValue
                          placeholder={t(
                            "orders.kiotviet.kshipSenderAddressPlaceholder",
                            "Chọn địa chỉ gửi",
                          )}
                        />
                      );
                    })()}
                  </SelectTrigger>
                  <SelectContent>
                    {senderAddresses.map((addr) => (
                      <SelectItem key={addr.id} value={String(addr.id)}>
                        <div className="flex min-w-0 flex-col">
                          <span className="truncate">{addr.name}</span>
                          <span className="truncate text-xs text-muted-foreground">
                            {addr.address}
                          </span>
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {canManageSenderAddresses ? (
                  <button
                    type="button"
                    className="shrink-0 rounded-md border border-input bg-background px-2 py-1.5 text-muted-foreground hover:text-foreground"
                    title={t("orders.kiotviet.kshipSenderManage", "Quản lý địa chỉ gửi")}
                    onClick={onOpenManageSenderAddresses}
                  >
                    <Settings2 className="h-3.5 w-3.5" />
                  </button>
                ) : null}
              </div>
            </div>
```

- [ ] **Step 4: `KshipSenderAddressManageDialog.tsx` — dialog CRUD**

Model theo `KshipNotePresetManageDialog` (nằm trong `KshipNotePresetPicker.tsx` — đọc trước để khớp giao diện). Nội dung đầy đủ:

```tsx
import { useState } from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useI18n } from "@/i18n";
import type { KshipSenderAddress, KshipSenderAddressInput } from "@/lib/api/orders";
import { useKshipSenderAddressMutations } from "@/features/orders/use-kship-sender-addresses";

type Props = {
  open: boolean;
  onOpenChange: (nextOpen: boolean) => void;
  addresses: KshipSenderAddress[];
};

const EMPTY_FORM: KshipSenderAddressInput = {
  name: "",
  location_id: 0,
  ward_id: 0,
  address: "",
  mobile: "",
};

/** Quản lý địa chỉ điểm gửi KShip. Chỉ render khi user có kiotviet:edit (gate ở caller). */
export function KshipSenderAddressManageDialog({ open, onOpenChange, addresses }: Props) {
  const { t } = useI18n();
  const { create, update, remove } = useKshipSenderAddressMutations();
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<KshipSenderAddressInput>(EMPTY_FORM);
  const [formOpen, setFormOpen] = useState(false);

  const startCreate = () => {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormOpen(true);
  };
  const startEdit = (addr: KshipSenderAddress) => {
    setEditingId(addr.id);
    setForm({
      name: addr.name,
      location_id: addr.location_id,
      ward_id: addr.ward_id,
      address: addr.address,
      mobile: addr.mobile,
    });
    setFormOpen(true);
  };

  const handleSave = async () => {
    if (!form.name.trim() || !form.address.trim() || !form.mobile.trim()) {
      toast.error(t("orders.kiotviet.kshipSenderFormIncomplete", "Điền đủ tên, địa chỉ, SĐT."));
      return;
    }
    if (form.location_id <= 0 || form.ward_id <= 0) {
      toast.error(
        t("orders.kiotviet.kshipSenderIdInvalid", "location_id/ward_id phải là số nguyên dương."),
      );
      return;
    }
    try {
      if (editingId !== null) {
        await update.mutateAsync({ id: editingId, patch: form });
      } else {
        await create.mutateAsync(form);
      }
      setFormOpen(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Lưu địa chỉ gửi thất bại.");
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await remove.mutateAsync(id);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Xóa địa chỉ gửi thất bại.");
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {t("orders.kiotviet.kshipSenderManage", "Quản lý địa chỉ gửi")}
          </DialogTitle>
        </DialogHeader>

        <div className="rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-800">
          {t(
            "orders.kiotviet.kshipSenderIdWarning",
            "location_id / ward_id là ID nội bộ KiotViet — nhập sai sẽ tạo vận đơn SAI điểm gửi.",
          )}
        </div>

        <div className="max-h-[320px] space-y-1 overflow-y-auto">
          {addresses.map((addr) => (
            <div
              key={addr.id}
              className="flex items-center gap-2 rounded-md border border-border p-2 text-sm"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{addr.name}</div>
                <div className="truncate text-xs text-muted-foreground">
                  {addr.address} · {addr.mobile} · loc {addr.location_id} / ward {addr.ward_id}
                </div>
              </div>
              <Button variant="ghost" size="sm" onClick={() => startEdit(addr)}>
                <Pencil className="h-3.5 w-3.5" />
              </Button>
              <Button variant="ghost" size="sm" onClick={() => void handleDelete(addr.id)}>
                <Trash2 className="h-3.5 w-3.5 text-destructive" />
              </Button>
            </div>
          ))}
        </div>

        {formOpen ? (
          <div className="space-y-2 rounded-md border border-border p-3">
            <input
              type="text"
              className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
              placeholder={t("orders.kiotviet.kshipSenderName", "Tên điểm gửi (vd: Kho Văn Quán)")}
              value={form.name}
              onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
            />
            <div className="flex gap-2">
              <input
                type="number"
                className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
                placeholder="location_id"
                value={form.location_id || ""}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, location_id: Number(e.target.value) || 0 }))
                }
              />
              <input
                type="number"
                className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
                placeholder="ward_id"
                value={form.ward_id || ""}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, ward_id: Number(e.target.value) || 0 }))
                }
              />
            </div>
            <input
              type="text"
              className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
              placeholder={t("orders.kiotviet.kshipSenderAddressField", "Địa chỉ")}
              value={form.address}
              onChange={(e) => setForm((prev) => ({ ...prev, address: e.target.value }))}
            />
            <input
              type="text"
              className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
              placeholder={t("orders.kiotviet.kshipSenderMobile", "SĐT người gửi")}
              value={form.mobile}
              onChange={(e) => setForm((prev) => ({ ...prev, mobile: e.target.value }))}
            />
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setFormOpen(false)}>
                {t("common.cancel", "Hủy")}
              </Button>
              <Button size="sm" disabled={create.isPending || update.isPending} onClick={() => void handleSave()}>
                {t("common.save", "Lưu")}
              </Button>
            </div>
          </div>
        ) : null}

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={startCreate}>
            <Plus className="mr-1 h-3.5 w-3.5" />
            {t("orders.kiotviet.kshipSenderAdd", "Thêm địa chỉ")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 5: Wiring `OrdersPage.tsx`**

1. Imports:

```typescript
import { KshipSenderAddressManageDialog } from "@/features/orders/KshipSenderAddressManageDialog";
import { useKshipSenderAddresses } from "@/features/orders/queries";
import { usePermission } from "@/hooks/usePermission";
```

(`usePermission` có thể đã import — kiểm tra trước.)

2. State + query (đặt cạnh `kshipNotePresetManageOpen` ~line 624). Destructure thêm `createKshipCodeModalOpen`, `kshipSenderAddressId`, `setKshipSenderAddressId` từ `useKshipDelivery` (Step 2 đã expose):

```typescript
  const [kshipSenderManageOpen, setKshipSenderManageOpen] = useState(false);
  const can = usePermission();
  const kshipSenderAddressesQuery = useKshipSenderAddresses(
    selectedOrderIdsForKship,
    createKshipCodeModalOpen,
  );
  const kshipSenderAddresses = kshipSenderAddressesQuery.data?.items ?? [];
  // Pre-select default theo rule kênh/branch khi dialog mở và user chưa tự chọn.
  useEffect(() => {
    if (!createKshipCodeModalOpen) return;
    const defaultId = kshipSenderAddressesQuery.data?.default_id;
    if (kshipSenderAddressId === null && typeof defaultId === "number") {
      setKshipSenderAddressId(defaultId);
    }
  }, [
    createKshipCodeModalOpen,
    kshipSenderAddressesQuery.data?.default_id,
    kshipSenderAddressId,
    setKshipSenderAddressId,
  ]);
```

3. Props cho `<KshipDeliveryDialog ...>` (cạnh `notePresets=...`):

```tsx
        senderAddresses={kshipSenderAddresses}
        senderAddressId={kshipSenderAddressId}
        onChangeSenderAddress={setKshipSenderAddressId}
        canManageSenderAddresses={can("kiotviet:edit")}
        onOpenManageSenderAddresses={() => setKshipSenderManageOpen(true)}
```

4. Render manage dialog cạnh `<KshipNotePresetManageDialog ...>`:

```tsx
      <KshipSenderAddressManageDialog
        open={kshipSenderManageOpen}
        onOpenChange={setKshipSenderManageOpen}
        addresses={kshipSenderAddresses}
      />
```

5. Kiểm tra chữ ký `usePermission` thực tế (`frontend/src/hooks/usePermission.ts`) — `Can.tsx` dùng `const can = usePermission(); can(permission)`; giữ đúng cách gọi đó.

- [ ] **Step 6: i18n**

Trong `frontend/src/i18n/messages/orders.ts`, cạnh các key `kshipNote*`, thêm (đúng format key/value của file):

```
"orders.kiotviet.kshipSenderAddress": "Địa chỉ gửi"
"orders.kiotviet.kshipSenderAddressPlaceholder": "Chọn địa chỉ gửi"
"orders.kiotviet.kshipSenderManage": "Quản lý địa chỉ gửi"
"orders.kiotviet.kshipSenderAdd": "Thêm địa chỉ"
"orders.kiotviet.kshipSenderName": "Tên điểm gửi (vd: Kho Văn Quán)"
"orders.kiotviet.kshipSenderAddressField": "Địa chỉ"
"orders.kiotviet.kshipSenderMobile": "SĐT người gửi"
"orders.kiotviet.kshipSenderIdWarning": "location_id / ward_id là ID nội bộ KiotViet — nhập sai sẽ tạo vận đơn SAI điểm gửi."
"orders.kiotviet.kshipSenderFormIncomplete": "Điền đủ tên, địa chỉ, SĐT."
"orders.kiotviet.kshipSenderIdInvalid": "location_id/ward_id phải là số nguyên dương."
```

Nếu file có bản EN/VI song song thì thêm cả hai theo cấu trúc sẵn có.

- [ ] **Step 7: Typecheck + lint + build**

```bash
cd C:/Users/MYPC/Desktop/workspace/desi-order-wt-kship-sender/frontend
npm run typecheck && npx eslint src/features/orders/KshipDeliveryDialog.tsx src/features/orders/KshipSenderAddressManageDialog.tsx src/features/orders/use-kship-delivery.ts src/pages/OrdersPage.tsx && npm run build
```

Expected: 0 lỗi.

- [ ] **Step 8: Commit**

```bash
git add src/features/orders src/pages/OrdersPage.tsx src/i18n/messages/orders.ts src/lib/api/orders.ts
git commit -m "feat(fe): sender address select + manage dialog in kship delivery flow"
```

---

### Task 8: Verification tổng

- [ ] **Step 1: Backend full-ish suite** (orders + kiotviet):

Run: `apps/orders/tests apps/kiotviet/tests -q --create-db` (lệnh docker ở Global Constraints, POSTGRES_DB=desi_kshipsender).
Expected: pass trừ baseline fail đã biết (xem memory known-failing-baseline: return-rate BE có thể fail trên DB bẩn — với DB mới phải pass; test_report_trends ×2, stock_negative_alert, dispatch_stock_cycle cancel-release, rbac system-role là baseline fail của full-suite ngoài phạm vi).

- [ ] **Step 2: FE test suite**: `cd frontend && npm test` (vitest; baseline fail đã biết: OrderFormDialog + 2 mobile-viewport — không phải regression).

- [ ] **Step 3: Ruff toàn bộ file backend đã sửa** (danh sách từ `git diff master --name-only -- backend`).

- [ ] **Step 4: Push + tổng kết**

```bash
git push -u origin feat/kship-sender-addresses
```

Ghi lại: link MR (nếu remote trả về), tests đã chạy + kết quả, những gì làm/không làm.
