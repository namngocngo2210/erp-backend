# Đơn hàng gửi ngoài — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trang "Đơn hàng gửi ngoài" (section RBAC riêng) tạo đơn `outside=true` với hình thức gửi + mã thu hồi; mở rộng `order_type` (7 giá trị); bộ lọc hình thức gửi/loại đơn trên mọi trang đơn; quét trả hàng khớp thêm `recall_code`; bỏ block lịch sử mua hàng khi TẠO đơn.

**Architecture:** Đơn outside vẫn là `Order` thường — thêm 2 cột (`outside`, `shipping_form`), trang mới là chế độ thứ 4 của `OrdersPage` (pattern Landing/KOC), endpoint list riêng `/api/orders/outside` khóa `outside=true` + section `outside-orders`. Mã thu hồi tái dùng bảng `order_recalls` (OneToOne, đã có index).

**Tech Stack:** Django/DRF + drf-spectacular; React + TypeScript + shadcn/ui, vitest; pytest chạy qua Docker image `desi-order-web-test`.

**Spec:** `docs/superpowers/specs/2026-07-06-outside-orders-design.md`

## Global Constraints

- Worktree: `.claude/worktrees/outside-orders`, branch `feat/outside-orders-page`. MỌI lệnh chạy từ root worktree này. KHÔNG tự tạo branch khác.
- Commit message tiếng Anh, ngắn, KHÔNG kèm Co-Authored-By.
- Enum values (copy đúng nguyên văn):
  - `ShippingForm`: `one_way` "Đơn 1 chiều", `two_way` "Đơn 2 chiều", `warranty_recall` "Đơn thu hồi bảo hành", `customer_return` "Đơn trả hàng", `recall` "Đơn thu hồi".
  - `OrderType` thêm: `same_product` "Cùng sản phẩm", `different_product` "Khác sản phẩm", `purchase` "Mua hàng", `warranty_return` "Trả bảo hành" (GIỮ 3 giá trị cũ).
- Section RBAC mới: `outside-orders`; route FE `/sales/outside-orders`; label sidebar "Đơn gửi ngoài" (key i18n `topnav.tabs.outsideOrders`).
- Lệnh test BE (Git Bash, từ root worktree; sau khi thêm migration PHẢI có `--create-db`):

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
```

- ⚠️ KHÔNG BAO GIỜ `cache.clear()` trong test (FLUSHDB đè Redis thật).
- Test FE: `cd frontend && npm run lint && npm run typecheck && npm run test:ci`. Baseline đã hỏng sẵn trên master (KHÔNG phải regression): test OrderFormDialog + 2 test mobile-viewport.
- Field `outside` chỉ set lúc TẠO (không cho sửa qua PATCH).

---

### Task 1: Model + migration 0097 (outside, shipping_form, OrderType mở rộng)

**Files:**
- Modify: `backend/apps/orders/models.py` (OrderType lines 33-36; Order fields ~line 245; Meta.indexes ~line 277; docstring OrderRecall line 496)
- Create: `backend/apps/orders/migrations/0097_order_outside_shipping_form.py` (sinh bằng makemigrations)
- Test: `backend/apps/orders/tests/test_outside_order_fields.py`

**Interfaces:**
- Produces: `ShippingForm(models.TextChoices)` importable từ `apps.orders.models`; `Order.outside: bool`, `Order.shipping_form: str`; `OrderType` có 7 values. Các task sau import `ShippingForm` từ `apps.orders.models`.

- [ ] **Step 1: Viết test fail**

Tạo `backend/apps/orders/tests/test_outside_order_fields.py`. Cách tạo `Channel`/`Customer`: copy đúng các dòng `Channel.objects.create(...)` / `Customer.objects.create(...)` trong `setUp` của `backend/apps/orders/tests/test_shipping_lookup_dispatched_flag.py` (lines 16-58) để khớp required fields.

```python
from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderType, ShippingForm


class OutsideOrderFieldsTests(TestCase):
    def test_order_defaults(self):
        channel = Channel.objects.create(code="out-ch", name="Out Channel")
        customer = Customer.objects.create(name="KH Out", phone="0900000001")
        order = Order.objects.create(channel=channel, customer=customer)
        order.refresh_from_db()
        self.assertFalse(order.outside)
        self.assertEqual(order.shipping_form, "")

    def test_shipping_form_choices(self):
        self.assertEqual(
            set(ShippingForm.values),
            {"one_way", "two_way", "warranty_recall", "customer_return", "recall"},
        )

    def test_order_type_has_new_values(self):
        for value in ("same_product", "different_product", "purchase", "warranty_return"):
            self.assertIn(value, OrderType.values)
        for value in ("normal", "no_return", "has_return"):
            self.assertIn(value, OrderType.values)
```

- [ ] **Step 2: Chạy test xác nhận fail**

Chạy lệnh test BE (Global Constraints) với path `apps/orders/tests/test_outside_order_fields.py`.
Expected: FAIL — `ImportError: cannot import name 'ShippingForm'`.

- [ ] **Step 3: Sửa model**

Trong `backend/apps/orders/models.py`:

(a) Mở rộng `OrderType` (lines 33-36):

```python
class OrderType(models.TextChoices):
    NORMAL = "normal", "Normal"
    NO_RETURN = "no_return", "No Return"
    HAS_RETURN = "has_return", "Has Return"
    SAME_PRODUCT = "same_product", "Cùng sản phẩm"
    DIFFERENT_PRODUCT = "different_product", "Khác sản phẩm"
    PURCHASE = "purchase", "Mua hàng"
    WARRANTY_RETURN = "warranty_return", "Trả bảo hành"
```

(b) Thêm enum mới ngay sau `OrderType`:

```python
class ShippingForm(models.TextChoices):
    """Hình thức gửi — chỉ dùng cho đơn gửi ngoài (outside=True)."""

    ONE_WAY = "one_way", "Đơn 1 chiều"
    TWO_WAY = "two_way", "Đơn 2 chiều"
    WARRANTY_RECALL = "warranty_recall", "Đơn thu hồi bảo hành"
    CUSTOMER_RETURN = "customer_return", "Đơn trả hàng"
    RECALL = "recall", "Đơn thu hồi"
```

(c) Thêm 2 field vào `Order`, ngay sau dòng `order_source` (line 245):

```python
    # Đơn tạo từ trang "Đơn hàng gửi ngoài" (tạo ở hệ thống ngoài, nhập vào CRM).
    # Chỉ set lúc tạo, không sửa qua PATCH.
    outside = models.BooleanField(default=False, db_default=False)
    shipping_form = models.CharField(
        max_length=30, choices=ShippingForm.choices, blank=True, default="", db_default=""
    )
```

(d) Thêm partial index vào `Order.Meta.indexes` (cạnh `ord_act_cr_desc_idx`):

```python
            # Trang Đơn gửi ngoài: tập outside nhỏ so với toàn bảng.
            models.Index(
                fields=["-created_at"],
                condition=Q(outside=True, deleted_at__isnull=True),
                name="ord_outside_cr_idx",
            ),
```

(e) Sửa docstring `OrderRecall` (line 496): thay câu `"""Thông tin thu hồi cho đơn KOC (order_source="koc").` thành `"""Thông tin thu hồi cho đơn (KOC và đơn gửi ngoài).` — giữ phần còn lại.

- [ ] **Step 4: Sinh migration**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  python manage.py makemigrations orders
```

Expected: tạo `backend/apps/orders/migrations/0097_*.py` với AddField `outside`, `shipping_form`, AlterField `order_type` (choices), AddIndex `ord_outside_cr_idx`. Rename file thành `0097_order_outside_shipping_form.py` nếu tên tự sinh khác (giữ nguyên nội dung; nếu rename phải sửa cả `dependencies` của migration nào trỏ tới — với migration mới nhất thì không có).

- [ ] **Step 5: Chạy test xác nhận pass**

Lệnh test BE với path `apps/orders/tests/test_outside_order_fields.py` (nhớ `--create-db`).
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/models.py backend/apps/orders/migrations/
git commit -m "feat(orders): add outside + shipping_form fields, extend order_type choices"
```

---

### Task 2: BE tạo đơn — nhận outside / shipping_form / recall_code

**Files:**
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/order_action_serializers.py` (OrderCreateSerializer, ~line 21)
- Modify: `backend/apps/orders/application/services/order_service_commands.py` (`create_order_manual`, `Order.objects.create` ~line 677 và sau block `_upsert_order_assessment` ~line 744)
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py` (`OrderSerializer.Meta.fields` ~line 231)
- Test: `backend/apps/orders/tests/test_order_create_api.py`

**Interfaces:**
- Consumes: `ShippingForm` từ Task 1; helper sẵn có `OrderServiceCommands._upsert_order_recall(order, payload_dict, actor)` (`order_service_helpers.py:612`).
- Produces: POST `/api/orders/create` nhận `outside` (bool), `shipping_form` (choice, optional), `recall_code` (str ≤64, optional); response `OrderSerializer` chứa `outside`, `shipping_form` (FE Task 9 dùng).

- [ ] **Step 1: Viết test fail**

Thêm vào cuối class `OrderCreateApiTests` trong `test_order_create_api.py`. Payload cơ sở: COPY nguyên payload của test create đầu tiên trong file (giữ đúng key channel/customer đang dùng), rồi thêm các key mới:

```python
    def test_create_outside_order_with_new_fields(self):
        # payload = {... copy từ test create thành công đầu tiên trong file ...}
        payload.update(
            {
                "outside": True,
                "shipping_form": "two_way",
                "order_type": "same_product",
                "recall_code": "RC-OUT-001",
            }
        )
        self.client.force_authenticate(self.admin)
        resp = self.client.post("/api/orders/create", payload, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        order = Order.objects.get(id=resp.data["id"])
        self.assertTrue(order.outside)
        self.assertEqual(order.shipping_form, "two_way")
        self.assertEqual(order.order_type, "same_product")
        self.assertEqual(order.recall.recall_code, "RC-OUT-001")
        self.assertTrue(resp.data["outside"])
        self.assertEqual(resp.data["shipping_form"], "two_way")

    def test_create_order_without_new_fields_keeps_defaults(self):
        # payload = {... copy như trên, KHÔNG thêm key mới ...}
        self.client.force_authenticate(self.admin)
        resp = self.client.post("/api/orders/create", payload, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        order = Order.objects.get(id=resp.data["id"])
        self.assertFalse(order.outside)
        self.assertEqual(order.shipping_form, "")
        self.assertFalse(hasattr(order, "recall") and order.recall is not None)
```

Import bổ sung nếu thiếu: `from apps.orders.models import Order`.

- [ ] **Step 2: Chạy test xác nhận fail**

Path: `apps/orders/tests/test_order_create_api.py -k outside`.
Expected: FAIL (KeyError `outside` trong response hoặc `order.outside` False).

- [ ] **Step 3: Sửa serializer create**

Trong `order_action_serializers.py`, thêm import `ShippingForm` vào dòng import từ `apps.orders.models`, và thêm 3 field vào `OrderCreateSerializer` ngay sau `vat` (line 23):

```python
    outside = serializers.BooleanField(required=False, default=False)
    shipping_form = serializers.ChoiceField(
        choices=ShippingForm.choices, required=False, allow_blank=True, default=""
    )
    recall_code = serializers.CharField(
        max_length=64, required=False, allow_blank=True, default=""
    )
```

- [ ] **Step 4: Sửa service create**

Trong `create_order_manual` (`order_service_commands.py`):

(a) Trong `Order.objects.create(...)`, ngay sau dòng `order_type=payload.get("order_type", "normal"),` (line 677):

```python
            outside=bool(payload.get("outside", False)),
            shipping_form=payload.get("shipping_form", "") or "",
```

(b) Sau block gọi `_upsert_order_assessment(order, ..., <actor>)` (~line 744-748), thêm — dùng ĐÚNG biến actor mà block assessment đang dùng (đọc code tại chỗ, thường là `user` hoặc `actor`):

```python
        recall_code = (payload.get("recall_code") or "").strip()
        if recall_code:
            OrderServiceCommands._upsert_order_recall(order, {"recall_code": recall_code}, <actor>)
```

Nếu `_upsert_order_recall` chưa được import/khả dụng trong scope của `create_order_manual`, import theo đúng cách block assessment đang tham chiếu helper (cùng module pattern).

- [ ] **Step 5: Expose field ở serializer đọc**

Trong `order_core_serializers.py`, thêm `"outside", "shipping_form",` vào tuple `OrderSerializer.Meta.fields` (cạnh `"order_type"`).

- [ ] **Step 6: Chạy test xác nhận pass**

Path: `apps/orders/tests/test_order_create_api.py`.
Expected: toàn bộ file pass (test cũ + 2 test mới).

- [ ] **Step 7: Commit**

```bash
git add backend/apps/orders/transport/serializers/ backend/apps/orders/application/services/order_service_commands.py backend/apps/orders/tests/test_order_create_api.py
git commit -m "feat(orders): accept outside, shipping_form, recall_code on order create"
```

---

### Task 3: BE PATCH — cho sửa shipping_form

**Files:**
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py` (`OrderInlineUpdateSerializer.Meta.fields`, lines 276-325)
- Test: `backend/apps/orders/tests/test_outside_order_fields.py` (thêm class mới)

**Interfaces:**
- Consumes: PATCH `/api/orders/<id>` (view `update_order_inline`, generic setattr loop tự áp field mới — không sửa service).
- Produces: PATCH nhận `shipping_form`; `recall.recall_code` PATCH được từ trước (nested `recall`) — không đổi. `outside` KHÔNG nằm trong Meta.fields (create-only).

- [ ] **Step 1: Viết test fail**

Thêm vào `test_outside_order_fields.py`:

```python
from rest_framework.test import APIClient

from apps.users.test_helpers import make_admin


class OutsideOrderPatchTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-outside-patch")
        self.channel = Channel.objects.create(code="out-patch", name="Out Patch")
        self.customer = Customer.objects.create(name="KH Patch", phone="0900000002")
        self.order = Order.objects.create(
            channel=self.channel, customer=self.customer, outside=True
        )

    def test_patch_shipping_form(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.patch(
            f"/api/orders/{self.order.id}", {"shipping_form": "recall"}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_form, "recall")

    def test_patch_shipping_form_invalid_value(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.patch(
            f"/api/orders/{self.order.id}", {"shipping_form": "bogus"}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_patch_cannot_change_outside(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.patch(
            f"/api/orders/{self.order.id}", {"outside": False}, format="json"
        )
        # outside không nằm trong serializer → bị bỏ qua, không đổi giá trị
        self.assertEqual(resp.status_code, 200, resp.data)
        self.order.refresh_from_db()
        self.assertTrue(self.order.outside)
```

Lưu ý: nếu PATCH bị chặn bởi field_policy (403/400 vì permission theo field), xem cách các test PATCH hiện có trong repo cấp quyền (grep `update_order_inline` trong `backend/apps/orders/tests/`) và mirror.

- [ ] **Step 2: Chạy test xác nhận fail**

Path: `apps/orders/tests/test_outside_order_fields.py`.
Expected: `test_patch_shipping_form` FAIL (400 — field không được khai báo).

- [ ] **Step 3: Sửa serializer**

Trong `kiotviet_landing_serializers.py`, thêm `"shipping_form",` vào tuple `OrderInlineUpdateSerializer.Meta.fields` (cạnh `"order_type"`). KHÔNG thêm `"outside"`. ModelSerializer tự validate theo choices của model.

- [ ] **Step 4: Chạy test xác nhận pass**

Path: `apps/orders/tests/test_outside_order_fields.py`. Expected: tất cả pass.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py backend/apps/orders/tests/test_outside_order_fields.py
git commit -m "feat(orders): allow patching shipping_form"
```

---

### Task 4: BE bộ lọc list — shipping_form / order_type / outside trên CẢ 4 endpoint

**Files:**
- Modify: `backend/apps/orders/repositories.py` (`_apply_list_filters`, thêm block sau block `vat` lines 558-565)
- Modify: `backend/apps/orders/transport/views/order_views_impl/_view_helpers.py` (`build_orders_filters` lines 94-129)
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_list_view.py` (3 inline dict: KOC lines 127-159, Landing lines 226-260, Shipment lines 389-401)
- Test: `backend/apps/orders/tests/test_orders_list_new_filters.py` (mới)

**Interfaces:**
- Produces: các list endpoint (`/api/orders`, `/api/orders/landing`, `/api/orders/koc`, `/api/orders/shipments`) nhận `shipping_form` (multi), `order_type` (multi); repository hiểu key `outside` ("true"/"false") — Task 6 dùng cho endpoint outside. FE gửi key `shipping_form` / `order_type` trong body list (Task 10).

- [ ] **Step 1: Viết test fail**

Trước khi viết: mở một test list-filter sẵn có để lấy ĐÚNG request shape (method/body/URL) — grep `has_return_slip` hoặc `"vat"` trong `backend/apps/orders/tests/` và mirror cách test đó gọi endpoint list. Tạo `test_orders_list_new_filters.py`:

```python
from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order
from apps.users.test_helpers import make_admin


class OrdersListNewFiltersTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-list-newfilters")
        self.channel = Channel.objects.create(code="lst-ch", name="List Ch")
        self.customer = Customer.objects.create(name="KH List", phone="0900000003")
        self.o_two_way = Order.objects.create(
            channel=self.channel, customer=self.customer,
            outside=True, shipping_form="two_way", order_type="same_product",
        )
        self.o_plain = Order.objects.create(
            channel=self.channel, customer=self.customer, order_type="normal",
        )

    def _ids(self, resp):
        rows = resp.data["results"] if isinstance(resp.data, dict) and "results" in resp.data else resp.data
        return {r["id"] for r in rows}

    def test_filter_shipping_form_on_orders_list(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post("/api/orders", {"shipping_form": ["two_way"]}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self._ids(resp), {self.o_two_way.id})

    def test_filter_order_type_on_orders_list(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post("/api/orders", {"order_type": ["same_product"]}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self._ids(resp), {self.o_two_way.id})

    def test_filter_order_type_on_landing_list_parity(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post("/api/orders/landing", {"order_type": ["same_product"]}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        # landing list có thể lọc order_source — chỉ cần KHÔNG trả row order_type khác
        self.assertNotIn(self.o_plain.id, self._ids(resp))
```

CHỈNH lại URL/body/response-shape theo test mẫu đã mirror (đây là phần dễ lệch nhất — bám test sẵn có, không đoán).

- [ ] **Step 2: Chạy test xác nhận fail**

Path: `apps/orders/tests/test_orders_list_new_filters.py`.
Expected: FAIL — filter bị bỏ qua nên trả cả 2 đơn.

- [ ] **Step 3: Thêm block filter vào repository**

Trong `_apply_list_filters` (`repositories.py`), ngay sau block `vat` (line 565), copy đúng idiom của block vat:

```python
        shipping_form_filter = filters.get("shipping_form")
        if shipping_form_filter:
            sf_values = shipping_form_filter if isinstance(shipping_form_filter, list) else [shipping_form_filter]
            sf_values = [str(v).strip() for v in sf_values if str(v).strip()]
            if sf_values:
                queryset = queryset.filter(shipping_form__in=sf_values)
            else:
                queryset = queryset.none()

        order_type_filter = filters.get("order_type")
        if order_type_filter:
            ot_values = order_type_filter if isinstance(order_type_filter, list) else [order_type_filter]
            ot_values = [str(v).strip() for v in ot_values if str(v).strip()]
            if ot_values:
                queryset = queryset.filter(order_type__in=ot_values)
            else:
                queryset = queryset.none()

        outside_filter = filters.get("outside")
        if outside_filter in (True, "true"):
            queryset = queryset.filter(outside=True)
        elif outside_filter in (False, "false"):
            queryset = queryset.filter(outside=False)
```

- [ ] **Step 4: Thêm key vào 4 chỗ build filter**

(a) `build_orders_filters` (`_view_helpers.py`): thêm 2 entry theo ĐÚNG cách entry `vat` đang làm (dùng `_parse_multi_query_values(request, "shipping_form")` và `_parse_multi_query_values(request, "order_type")`).

(b) 3 inline dict trong `order_list_view.py` (`list_koc_orders` ~line 127, `list_landing_orders` ~line 226, `list_shipment_orders` ~line 389): thêm cùng 2 key với cùng parser, đặt cạnh key `vat` sẵn có của từng dict.

- [ ] **Step 5: Chạy test xác nhận pass**

Path: `apps/orders/tests/test_orders_list_new_filters.py` + chạy lại `apps/orders/tests/test_outside_order_fields.py` (không vỡ gì).
Expected: pass hết.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/repositories.py backend/apps/orders/transport/views/order_views_impl/ backend/apps/orders/tests/test_orders_list_new_filters.py
git commit -m "feat(orders): add shipping_form, order_type, outside list filters to all list endpoints"
```

---

### Task 5: RBAC — section outside-orders

**Files:**
- Modify: `backend/apps/users/models.py` (`VALID_SECTIONS`, lines 6-32)
- Modify: `backend/apps/users/rbac_catalog.py` (`PERMISSION_CATALOG`, lines 15-49)
- Modify: `backend/apps/users/permissions.py` (`ORDER_SECTIONS`, line 4)
- Create: `backend/apps/users/migrations/0025_seed_outside_orders_permissions.py`
- Test: `backend/apps/users/tests/` — thêm test vào file test catalog/permissions sẵn có (grep `sync_permissions` trong `backend/apps/users/tests/` chọn file phù hợp) hoặc tạo `test_outside_orders_section.py`

**Interfaces:**
- Produces: section `outside-orders` với actions `("view", "create", "edit", "delete", "export")` trong catalog; `ORDER_SECTIONS = ("orders", "landing-orders", "koc-orders", "outside-orders")` → user chỉ có `outside-orders` gọi được các endpoint dùng chung (create/patch/detail). Task 6 + FE dựa vào section này.

- [ ] **Step 1: Viết test fail**

```python
from django.test import TestCase

from apps.users.rbac_catalog import PERMISSION_CATALOG, sync_permissions
from apps.users.models import VALID_SECTIONS
from apps.users.permissions import ORDER_SECTIONS


class OutsideOrdersSectionTests(TestCase):
    def test_section_registered(self):
        self.assertIn("outside-orders", VALID_SECTIONS)
        self.assertIn("outside-orders", PERMISSION_CATALOG)
        self.assertEqual(
            PERMISSION_CATALOG["outside-orders"],
            ("view", "create", "edit", "delete", "export"),
        )
        self.assertIn("outside-orders", ORDER_SECTIONS)

    def test_sync_creates_permissions(self):
        sync_permissions()
        from apps.users.models import Permission  # điều chỉnh import theo model thật

        codes = set(
            Permission.objects.filter(code__startswith="outside-orders:").values_list("code", flat=True)
        )
        self.assertEqual(
            codes,
            {f"outside-orders:{a}" for a in ("view", "create", "edit", "delete", "export")},
        )
```

Điều chỉnh import/field `Permission` theo model thật trong `apps/users/models.py` (xem `sync_permissions` ở `rbac_catalog.py:59-83` dùng model/field nào thì assert theo đó).

- [ ] **Step 2: Chạy test xác nhận fail**

Path: `apps/users/tests/test_outside_orders_section.py`. Expected: FAIL ở assert đầu.

- [ ] **Step 3: Đăng ký section**

(a) `users/models.py`: thêm `"outside-orders",` vào `VALID_SECTIONS` ngay sau `"koc-orders"`.
(b) `rbac_catalog.py`: thêm vào `PERMISSION_CATALOG` cạnh entry `"orders"`:

```python
    "outside-orders": ("view", "create", "edit", "delete", "export"),
```

(c) `users/permissions.py` line 4:

```python
ORDER_SECTIONS = ("orders", "landing-orders", "koc-orders", "outside-orders")
```

- [ ] **Step 4: Migration seed**

Tạo `backend/apps/users/migrations/0025_seed_outside_orders_permissions.py` — MIRROR cấu trúc `backend/apps/users/migrations/0014_seed_rbac_catalog.py` (mở file đó, copy đúng cách nó tạo Permission qua `apps.get_model`, thu hẹp về một section duy nhất `outside-orders` với 5 action trên; `dependencies = [("users", "0024_remove_cron_jobs_permissions")]`; có reverse xoá `outside-orders:*`). Nếu 0014 có bước bump RBAC cache — copy cả bước đó.

- [ ] **Step 5: Chạy test xác nhận pass**

Path: `apps/users/tests/test_outside_orders_section.py` (nhớ `--create-db` vì có migration mới). Expected: pass. Chạy thêm toàn bộ `apps/users -q` — lưu ý baseline có thể sẵn fail `rbac_roles_name_key` (2 test users RBAC, đã ghi nhận trên master — không phải regression).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/users/
git commit -m "feat(users): add outside-orders RBAC section"
```

---

### Task 6: BE endpoint list đơn gửi ngoài `/api/orders/outside`

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_list_view.py` (thêm `list_outside_orders` — clone `list_orders` lines 57-91)
- Modify: `backend/apps/orders/urls.py` (thêm path)
- Test: `backend/apps/orders/tests/test_outside_orders_list.py` (mới)

**Interfaces:**
- Consumes: `build_orders_filters` + key `outside` (Task 4), section `outside-orders` (Task 5).
- Produces: `POST /api/orders/outside` — cùng request/response shape với `POST /api/orders`, luôn khóa `outside=true`, guard `require_section("outside-orders")`, scope `_apply_user_channel_scope(filters, user, "outside-orders")`. FE Task 10 gọi endpoint này.

- [ ] **Step 1: Viết test fail**

```python
from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order
from apps.users.test_helpers import make_admin, make_user_with_sections


class OutsideOrdersListTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-outside-list")
        self.channel = Channel.objects.create(code="out-lst", name="Out List")
        self.customer = Customer.objects.create(name="KH OL", phone="0900000004")
        self.outside_order = Order.objects.create(
            channel=self.channel, customer=self.customer, outside=True, shipping_form="one_way"
        )
        self.normal_order = Order.objects.create(channel=self.channel, customer=self.customer)

    def _ids(self, resp):
        rows = resp.data["results"] if isinstance(resp.data, dict) and "results" in resp.data else resp.data
        return {r["id"] for r in rows}

    def test_returns_only_outside_orders(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post("/api/orders/outside", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self._ids(resp), {self.outside_order.id})

    def test_requires_outside_orders_section(self):
        user = make_user_with_sections("user-no-outside", ["orders"])
        self.client.force_authenticate(user)
        resp = self.client.post("/api/orders/outside", {}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_outside_section_user_allowed(self):
        user = make_user_with_sections("user-outside-only", ["outside-orders"])
        self.client.force_authenticate(user)
        resp = self.client.post("/api/orders/outside", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
```

(Chỉnh request shape theo đúng cách `list_orders` được test — như Task 4 Step 1. Nếu scope OWN mặc định làm user thường không thấy đơn, tạo user với `scope=DataScope.ALL` — xem chữ ký `make_user_with_sections` tại `apps/users/test_helpers.py:46`.)

- [ ] **Step 2: Chạy test xác nhận fail**

Path: `apps/orders/tests/test_outside_orders_list.py`. Expected: FAIL 404 (URL chưa có).

- [ ] **Step 3: Thêm view + URL (helper dùng chung — KHÔNG clone)**

Quyết định đã chốt với user: KHÔNG copy nguyên `list_orders`. Thay vào đó, trong `order_list_view.py`:

(a) Tách ruột `list_orders` (lines 57-91, phần SAU khi build filters) thành helper module-level:

```python
def _run_orders_list(request, *, section: str, forced_filters: dict | None = None):
    """Ruột chung của list_orders / list_outside_orders — build filters, scope theo section, query, serialize."""
    filters = build_orders_filters(request)
    if forced_filters:
        filters.update(forced_filters)
    _apply_user_channel_scope(filters, request.user, section)
    # ... phần còn lại giữ NGUYÊN từ list_orders (query repo, serialize, response) ...
```

(Di chuyển code hiện có, không viết lại; giữ nguyên mọi behavior — `list_orders` hiện truyền section mặc định "orders" cho scope, giờ truyền tường minh.)

(b) `list_orders` trở thành wrapper: `return _run_orders_list(request, section="orders")` (giữ nguyên decorators).

(c) Thêm view mới:

```python
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("outside-orders")])
def list_outside_orders(request):
    return _run_orders_list(request, section="outside-orders", forced_filters={"outside": "true"})
```

(copy decorator `@extend_schema` của `list_orders` nếu có, đổi operationId/description phù hợp).

KHÔNG đụng `list_koc_orders` / `list_landing_orders` / `list_shipment_orders` (ngoài phạm vi task).

Trong `urls.py`, cạnh path list landing/koc:

```python
    path("outside", list_outside_orders, name="orders-outside-list"),
```

(+ import view; đặt TRƯỚC pattern `<int:order_id>` nếu thứ tự khai báo có thể nuốt path.)

- [ ] **Step 4: Chạy test xác nhận pass**

Path: `apps/orders/tests/test_outside_orders_list.py`. Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/transport/views/order_views_impl/order_list_view.py backend/apps/orders/urls.py backend/apps/orders/tests/test_outside_orders_list.py
git commit -m "feat(orders): add outside orders list endpoint with own RBAC section"
```

---

### Task 7: BE lookup quét trả hàng khớp thêm recall_code

**Files:**
- Modify: `backend/apps/orders/transport/views/shipping_lookup_view.py` (lines 88-91)
- Test: `backend/apps/orders/tests/test_shipping_lookup_dispatched_flag.py`

**Interfaces:**
- Produces: `GET /api/orders/lookup-by-shipping-code?code=X` khớp `shipping_code=X` HOẶC `recall__recall_code=X` (index `order_recall_code_idx` sẵn có). FE quét không đổi.

- [ ] **Step 1: Viết test fail**

Thêm vào class test sẵn có trong `test_shipping_lookup_dispatched_flag.py` (dùng helper `_make_order` của file):

```python
    def test_lookup_matches_recall_code(self):
        order = self._make_order("VD-HAS-RECALL")
        OrderRecall.objects.create(order=order, recall_code="RC-SCAN-1")
        resp = self.client.get("/api/orders/lookup-by-shipping-code?code=RC-SCAN-1")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["order_id"], order.id)

    def test_lookup_shipping_code_still_works(self):
        order = self._make_order("VD-PLAIN-1")
        resp = self.client.get("/api/orders/lookup-by-shipping-code?code=VD-PLAIN-1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["order_id"], order.id)

    def test_lookup_code_matching_both_fields_returns_multiple(self):
        # Một đơn có shipping_code=X, đơn khác có recall_code=X → nhánh multiple
        order_a = self._make_order("CODE-DUP-1")
        order_b = self._make_order("VD-OTHER-9")
        OrderRecall.objects.create(order=order_b, recall_code="CODE-DUP-1")
        resp = self.client.get("/api/orders/lookup-by-shipping-code?code=CODE-DUP-1")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data.get("multiple"))
        self.assertEqual(
            {o["order_id"] for o in resp.data["orders"]}, {order_a.id, order_b.id}
        )
```

Import: `from apps.orders.models import OrderRecall`.

- [ ] **Step 2: Chạy test xác nhận fail**

Path: `apps/orders/tests/test_shipping_lookup_dispatched_flag.py`. Expected: test recall FAIL 404.

- [ ] **Step 3: Sửa view**

Trong `shipping_lookup_view.py`, thêm `from django.db.models import Q` và đổi query (lines 88-91) thành:

```python
    orders = list(
        Order.objects.filter(
            Q(shipping_code=code) | Q(recall__recall_code=code),
            deleted_at__isnull=True,
        ).prefetch_related("items")[:20]
    )
```

Cập nhật docstring/`description` của `OpenApiParameter` code thành "Mã vận đơn hoặc mã thu hồi".

- [ ] **Step 4: Chạy test xác nhận pass**

Path: `apps/orders/tests/test_shipping_lookup_dispatched_flag.py`. Expected: cả file pass.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/transport/views/shipping_lookup_view.py backend/apps/orders/tests/test_shipping_lookup_dispatched_flag.py
git commit -m "feat(orders): match recall_code in shipping code lookup"
```

---

### Task 8: Regenerate OpenAPI schema + FE types

**Files:**
- Modify: `backend/schema.yaml` (sinh tự động)
- Modify: `frontend/src/api/schema.d.ts` (sinh tự động)

**Interfaces:**
- Produces: schema chứa `outside`/`shipping_form` trong Order schemas + endpoint `/api/orders/outside` — FE typed dùng ở Task 9-12.

- [ ] **Step 1: Sinh schema BE**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  python manage.py spectacular --file schema.yaml
```

Expected: `backend/schema.yaml` diff có `outside`, `shipping_form`, path `/api/orders/outside`. Không có Error mới trong output spectacular.

- [ ] **Step 2: Sinh types FE**

```bash
cd frontend && npm run gen:api
```

Expected: `frontend/src/api/schema.d.ts` cập nhật.

- [ ] **Step 3: Commit**

```bash
git add backend/schema.yaml frontend/src/api/schema.d.ts
git commit -m "chore: regenerate openapi schema and fe types"
```

---

### Task 9: FE nền tảng — types, i18n, payload builders

**Files:**
- Modify: `frontend/src/features/orders/order-types.ts` (OrderRow lines 120-196; CreateOrderPayload; OrderColumnKey lines 198-268; ORDER_COLUMN_OPTIONS lines 270-330; ORDER_COLUMN_FIELD_BY_KEY lines 352-423)
- Modify: `frontend/src/i18n/messages/orderType.ts`
- Create: `frontend/src/i18n/messages/shippingForm.ts` (+ đăng ký ở index messages — xem cách `orderType.ts` được import)
- Modify: `frontend/src/i18n/messages/orders.ts` (keys mới)
- Modify: file chứa `topnav.tabs.kocOrders` + `page.title.*` (grep `topnav.tabs.kocOrders` trong `frontend/src/i18n/messages/`) — thêm keys outside
- Modify: `frontend/src/features/orders/order-import-format.ts` (`buildPatchPayload` lines 11-50)
- Modify: `frontend/src/features/orders/OrderFormDialog.tsx` (`DEFAULT_FORM_VALUES`, `mapOrderToForm` — grep tên trong file/nơi định nghĩa)
- Test: `frontend/src/features/orders/__tests__/order-payload.test.ts`

**Interfaces:**
- Produces:
  - `OrderRow` thêm `outside?: boolean; shipping_form?: string;`
  - `CreateOrderPayload` thêm `shipping_form?: string; recall_code?: string; outside?: boolean;`
  - `export const SHIPPING_FORM_VALUES = ["one_way", "two_way", "warranty_recall", "customer_return", "recall"] as const;` trong `order-types.ts`
  - `export const OUTSIDE_COLUMN_KEYS: OrderColumnKey[] = ["shipping_form"];` trong `order-import-format.ts` (cạnh `RECALL_COLUMN_KEYS` lines 81-86)
  - Column key mới `"shipping_form"`; i18n keys: `orderType.same_product|different_product|purchase|warranty_return`, `shippingForm.one_way|two_way|warranty_recall|customer_return|recall`, `orders.filters.shippingForm`, `orders.filters.orderType`, `orders.columns.shippingForm`, `orders.fields.shippingForm`, `orders.fields.recallCode`, `topnav.tabs.outsideOrders`, `page.title.outsideOrders`.
  - `buildPatchPayload` map `shipping_form` + `recall: { recall_code }`.

- [ ] **Step 1: Viết test fail cho buildPatchPayload**

Thêm vào `order-payload.test.ts` (mirror test sẵn có trong file):

```ts
it("maps shipping_form and recall_code into patch payload", () => {
  const values = {
    ...baseValues, // dùng fixture/base values sẵn có của file test
    shipping_form: "two_way",
    recall_code: "RC-9",
  };
  const payload = buildPatchPayload(values);
  expect(payload.shipping_form).toBe("two_way");
  expect(payload.recall).toEqual({ recall_code: "RC-9" });
});

it("omits recall when recall_code empty", () => {
  const payload = buildPatchPayload({ ...baseValues, shipping_form: "", recall_code: "" });
  expect(payload.shipping_form).toBe("");
  expect(payload.recall).toBeUndefined();
});
```

- [ ] **Step 2: Chạy test xác nhận fail**

```bash
cd frontend && npx vitest run src/features/orders/__tests__/order-payload.test.ts
```

Expected: FAIL (type error / undefined).

- [ ] **Step 3: Sửa types + builders**

(a) `order-types.ts`:
- `OrderRow` thêm (cạnh `order_type?`): `outside?: boolean;` và `shipping_form?: string;`
- `CreateOrderPayload` thêm: `shipping_form?: string; recall_code?: string; outside?: boolean;`
- Thêm export:

```ts
export const SHIPPING_FORM_VALUES = [
  "one_way",
  "two_way",
  "warranty_recall",
  "customer_return",
  "recall",
] as const;
export type ShippingFormValue = (typeof SHIPPING_FORM_VALUES)[number];
```

- `OrderColumnKey` union thêm `| "shipping_form"`; `ORDER_COLUMN_OPTIONS` thêm `{ key: "shipping_form", label: "Hình thức gửi" }` (đặt cạnh cột order_type); `ORDER_COLUMN_FIELD_BY_KEY` thêm `shipping_form: "shipping_form",`.

(b) `order-import-format.ts`:
- `buildPatchPayload` thêm:

```ts
    shipping_form: values.shipping_form ?? "",
    ...(values.recall_code ? { recall: { recall_code: values.recall_code } } : {}),
```

- Thêm cạnh `RECALL_COLUMN_KEYS`:

```ts
export const OUTSIDE_COLUMN_KEYS: OrderColumnKey[] = ["shipping_form"];
```

(c) `OrderFormDialog.tsx` (hoặc nơi định nghĩa): `DEFAULT_FORM_VALUES` thêm `shipping_form: "", recall_code: "",`; `mapOrderToForm` thêm `shipping_form: order.shipping_form ?? "", recall_code: order.recall?.recall_code ?? "",`.

(d) i18n:
- `orderType.ts` thêm cả 2 locale:

```ts
  "orderType.same_product": "Cùng sản phẩm",       // en: "Same product"
  "orderType.different_product": "Khác sản phẩm",  // en: "Different product"
  "orderType.purchase": "Mua hàng",                // en: "Purchase"
  "orderType.warranty_return": "Trả bảo hành",     // en: "Warranty return"
```

- Tạo `shippingForm.ts` mirror cấu trúc `orderType.ts`:

```ts
export const shippingForm = {
  vi: {
    "shippingForm.one_way": "Đơn 1 chiều",
    "shippingForm.two_way": "Đơn 2 chiều",
    "shippingForm.warranty_recall": "Đơn thu hồi bảo hành",
    "shippingForm.customer_return": "Đơn trả hàng",
    "shippingForm.recall": "Đơn thu hồi",
  },
  en: {
    "shippingForm.one_way": "One-way order",
    "shippingForm.two_way": "Two-way order",
    "shippingForm.warranty_recall": "Warranty recall order",
    "shippingForm.customer_return": "Customer return order",
    "shippingForm.recall": "Recall order",
  },
} as const;
```

và đăng ký vào messages index đúng cách `orderType` được đăng ký.
- `orders.ts` thêm (cả vi + en): `"orders.filters.shippingForm": "Hình thức gửi"`, `"orders.filters.orderType": "Loại đơn hàng"`, `"orders.columns.shippingForm": "Hình thức gửi"`, `"orders.fields.shippingForm": "Hình thức gửi"`, `"orders.fields.recallCode": "Mã thu hồi"`.
- File chứa `topnav.tabs.kocOrders`: thêm `"topnav.tabs.outsideOrders": "Đơn gửi ngoài"` + key `page.title.outsideOrders: "Đơn hàng gửi ngoài"` (cả 2 locale, mirror keys koc).

- [ ] **Step 4: Chạy test xác nhận pass**

```bash
cd frontend && npx vitest run src/features/orders/__tests__/order-payload.test.ts && npm run typecheck
```

Expected: PASS + typecheck sạch.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/orders/ frontend/src/i18n/
git commit -m "feat(fe): order types, i18n, payload for outside orders"
```

---

### Task 10: FE filter state + data hook (outside mode + 2 bộ lọc mới)

**Files:**
- Modify: `frontend/src/features/orders/use-orders-filters.ts` (mirror `vat`: return type ~line 12, useState ~82, reset ~109, returns ~133/154)
- Modify: `frontend/src/features/orders/order-storage.ts` (StoredOrderFilters ~line 101; defaults ~168; parse ~198/224; `FilterScope` union lines 111-124)
- Modify: `frontend/src/app/orders-tab.ts` (`getOrderTabScope` lines 22-43)
- Modify: `frontend/src/features/orders/use-orders-data.ts` (LoadOrdersOverrides ~line 74; destructure/snapshot ~343/421/441; body ~899-943; endpoint ~981-989; mode refs ~294-307; return object)
- Test: `frontend/src/features/orders/__tests__/use-orders-filters.test.tsx`

**Interfaces:**
- Consumes: endpoint `/api/orders/outside` (Task 6), body keys `shipping_form`/`order_type` (Task 4).
- Produces:
  - `useOrdersFilters` trả thêm `shippingForm: string[]`, `setShippingForm`, `orderType: string[]`, `setOrderType` (mirror `vat`/`setVat`).
  - `FilterScope` thêm `"outside" | "outside-delivered" | "outside-cancelled"`.
  - `getOrderTabScope("outside-orders", tab)` trả các scope trên (mirror nhánh koc lines 35-39); widen param type thêm `"outside-orders"`.
  - `useOrdersData` trả thêm `setOutsideMode(value: boolean)`, `shippingForm`, `setShippingForm`, `orderType`, `setOrderType`; `LoadOrdersOverrides` thêm `shippingForm?: string[]; orderType?: string[]`; body gửi `shipping_form`/`order_type` khi không rỗng; endpoint `/api/orders/outside` khi outside mode.

- [ ] **Step 1: Viết test fail**

Thêm vào `use-orders-filters.test.tsx` (mirror cách file test `vat`):

```tsx
it("exposes shippingForm and orderType filter state", () => {
  const { result } = renderHook(() => useOrdersFilters());
  expect(result.current.shippingForm).toEqual([]);
  expect(result.current.orderType).toEqual([]);
  act(() => {
    result.current.setShippingForm(["two_way"]);
    result.current.setOrderType(["same_product", "purchase"]);
  });
  expect(result.current.shippingForm).toEqual(["two_way"]);
  expect(result.current.orderType).toEqual(["same_product", "purchase"]);
});
```

(Chỉnh cách renderHook/props theo test sẵn có trong file.)

- [ ] **Step 2: Chạy test xác nhận fail**

```bash
cd frontend && npx vitest run src/features/orders/__tests__/use-orders-filters.test.tsx
```

- [ ] **Step 3: Thêm state filter**

(a) `use-orders-filters.ts`: clone đúng 4 chỗ của `vat` cho `shippingForm` và `orderType` (state mảng string, reset về `[]`).
(b) `order-storage.ts`: `StoredOrderFilters` thêm `shipping_form?: string[]; order_type?: string[];`; default `shipping_form: [], order_type: []`; parse bằng `normalizeStoredFilterArray(parsed?.shipping_form)` / `...order_type` (2 chỗ như `vat`); `FilterScope` union thêm `| "outside" | "outside-delivered" | "outside-cancelled"`.
(c) `orders-tab.ts`: widen type param `section` thêm `"outside-orders"`, thêm nhánh:

```ts
  if (section === "outside-orders") {
    if (tab === "delivered") return "outside-delivered";
    if (tab === "cancelled") return "outside-cancelled";
    return "outside";
  }
```

- [ ] **Step 4: Nối vào use-orders-data**

Trong `use-orders-data.ts`, mirror `vat` từng chỗ một:
- `LoadOrdersOverrides` thêm `shippingForm?: string[]; orderType?: string[];`
- Destructure `shippingForm`/`orderType` + setters từ filters hook; thêm vào `filterSnapshotRef`; normalize + gắn body:

```ts
const shippingFormValue = Array.from(new Set((overrides?.shippingForm ?? shippingForm).map((v) => v.trim()).filter(Boolean))).sort();
const orderTypeValue = Array.from(new Set((overrides?.orderType ?? orderType).map((v) => v.trim()).filter(Boolean))).sort();
...
if (shippingFormValue.length > 0) body.shipping_form = shippingFormValue;
if (orderTypeValue.length > 0) body.order_type = orderTypeValue;
```

- Mode: clone cặp `kocModeRef`/`setKocMode` (lines 304-307) thành `outsideModeRef`/`setOutsideMode`; endpoint selection (lines 981-989) thêm nhánh:

```ts
    : outsideModeRef.current ? `/api/orders/outside`
```

(đặt sau nhánh koc). Export `setOutsideMode`, `shippingForm`, `setShippingForm`, `orderType`, `setOrderType` trong return object (đúng chỗ `vat`/`setVat`/`setKocMode` đang được trả).

- [ ] **Step 5: Chạy test xác nhận pass**

```bash
cd frontend && npx vitest run src/features/orders/__tests__/use-orders-filters.test.tsx && npm run typecheck
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/orders/ frontend/src/app/orders-tab.ts
git commit -m "feat(fe): shipping_form/order_type filters and outside mode in orders data hook"
```

---

### Task 11: FE routing + trang Đơn gửi ngoài + bộ lọc UI + cột bảng

**Files:**
- Modify: `frontend/src/lib/routes.ts` (AppSection lines 3-35; SECTION_TO_PATH 40-73; parseOrdersView 84-93; sectionFromPath 110-119; detail path helper ~105)
- Modify: `frontend/src/app/modules.ts` (sales sections lines 61-67 + lucide import)
- Modify: `frontend/src/App.tsx` (isOrderSection 92-93; loadSectionData nhánh mới clone koc 368-411; title effect 465-502; các nhánh orders/landing/koc thêm `orders.setOutsideMode(false)` cạnh `setKocMode(...)`)
- Modify: `frontend/src/app/routes.tsx` (clone block koc lines 97-114)
- Modify: `frontend/src/app/routed-pages.tsx` (clone `KocOrdersRoute` lines 254-285 → `OutsideOrdersRoute`)
- Modify: `frontend/src/pages/OrdersPage.tsx` (prop `isOutside`; columns 377-396; filter options/handlers ~605-871; truyền panel 1305-1355; truyền dialog 1679-1691; submit 1006-1032)
- Modify: `frontend/src/features/orders/OrdersFilterPanel.tsx` (2 MultiSelect mirror vat: props ~105-107, render ~352-367)
- Modify: `frontend/src/features/orders/OrdersTable/OrderCell.tsx` (case `shipping_form` + order_type select 7 options, lines 554-584)
- Modify: `frontend/src/pages/RolesPage.tsx` (SECTION_LABELS lines 66-91; MODULE_GROUPS "Bán hàng" ~97)

**Interfaces:**
- Consumes: `setOutsideMode`, filter state (Task 10); i18n keys + `OUTSIDE_COLUMN_KEYS`/`SHIPPING_FORM_VALUES` (Task 9); section `outside-orders` (Task 5).
- Produces: route `/sales/outside-orders` hoạt động end-to-end; `OrdersPage` nhận prop `isOutside?: boolean` và truyền `outsideMode={isOutside}` xuống `OrderFormDialog` (Task 12 dùng); 2 dropdown lọc xuất hiện trên MỌI trang đơn.

- [ ] **Step 1: Wiring routes/section**

(a) `lib/routes.ts`:
- `AppSection` thêm `| "outside-orders"` (cạnh koc-orders).
- `SECTION_TO_PATH` thêm `"outside-orders": "/sales/outside-orders",`.
- `parseOrdersView` regex: `/^\/sales\/(?:orders|landing-orders|koc-orders|outside-orders)\/(\d+)\/?$/i`.
- `sectionFromPath`: thêm `if (/^\/sales\/outside-orders(\/|$)/.test(normalized)) return "outside-orders";` TRƯỚC check `/sales/orders`.
- Thêm helper mirror `kocOrderDetailPath`:

```ts
/** Path URL chi tiết đơn gửi ngoài (giữ section outside để back về /sales/outside-orders) */
export function outsideOrderDetailPath(orderId: number): string {
  return `/sales/outside-orders/${orderId}`;
}
```

(b) `app/modules.ts`: import icon `Send` từ lucide-react, thêm vào sales sections sau koc-orders:

```ts
      { key: "outside-orders", labelKey: "topnav.tabs.outsideOrders", icon: Send },
```

(c) `RolesPage.tsx`: `SECTION_LABELS` thêm `"outside-orders": "Đơn gửi ngoài",`; thêm `"outside-orders"` vào mảng sections của group "Bán hàng" (~line 97).

- [ ] **Step 2: App.tsx + routes + route component**

(a) `App.tsx`:
- `isOrderSection` (92-93) thêm `|| section === "outside-orders"`.
- Trong `loadSectionData`, clone nguyên nhánh `koc-orders` (368-411) thành nhánh `outside-orders`, đổi: `orders.setKocMode(false); orders.setOutsideMode(true);` và scope `getOrderTabScope("outside-orders", initialTab)`.
- Các nhánh `orders`/`landing-orders`/`koc-orders` sẵn có: thêm `orders.setOutsideMode(false);` cạnh dòng `setKocMode(...)` (3 chỗ) — nếu quên, chuyển trang sẽ dính endpoint outside cũ.
- Title effect (~465): thêm nhánh `outside-orders` → `t("page.title.outsideOrders", "Đơn hàng gửi ngoài")`.

(b) `app/routes.tsx`: clone block koc (97-114) thành:

```tsx
      {
        path: "sales/outside-orders",
        element: (<Guard section="outside-orders"><OutsideOrdersRoute /></Guard>),
      },
      {
        path: "sales/outside-orders/:orderId",
        element: (<Guard section="outside-orders"><OrderDetailRoute /></Guard>),
      },
```

(+ import `OutsideOrdersRoute`).

(c) `routed-pages.tsx`: clone `KocOrdersRoute` (254-285) thành `OutsideOrdersRoute`, đổi props truyền cho `OrdersPage`: bỏ `isKoc`, thêm `isOutside`, giữ `showOrderTabs`, giữ `hideChannelFilter` hay không tùy koc — GIỮ NGUYÊN như koc để đồng nhất; bỏ nhánh blocklist nếu nó chỉ dành cho koc (kiểm tra `shell.landingTab === "blocklist"` — với outside không có blocklist, xoá nhánh đó).

- [ ] **Step 3: OrdersPage — prop isOutside, cột, bộ lọc**

(a) Props (85-269) + destructure (356-366): thêm `isOutside = false,` (khai báo `isOutside?: boolean` với doc ngắn).

(b) Columns (377-396): mở rộng logic lọc cột —

```ts
    let baseOptions = ORDER_COLUMN_OPTIONS;
    if (!isKoc && !isOutside) {
      baseOptions = baseOptions.filter((o) => !RECALL_COLUMN_KEYS.includes(o.key));
    }
    if (!isOutside) {
      baseOptions = baseOptions.filter((o) => !OUTSIDE_COLUMN_KEYS.includes(o.key));
    }
```

(import `OUTSIDE_COLUMN_KEYS` từ order-import-format). Áp cùng logic cho `effectiveVisibleColumns` (390-396).

(c) Filter options + handlers — clone đúng pattern `vat` (options 625-628, handler 862-871):

```ts
const shippingFormOptions: FilterSelectOption[] = useMemo(
  () => SHIPPING_FORM_VALUES.map((v) => ({ value: v, label: t(`shippingForm.${v}`, v) })),
  [t],
);
const orderTypeFilterOptions: FilterSelectOption[] = useMemo(
  () =>
    ["normal", "no_return", "has_return", "same_product", "different_product", "purchase", "warranty_return"].map(
      (v) => ({ value: v, label: t(`orderType.${v}`, v) }),
    ),
  [t],
);
```

+ `handleShippingFormChange` / `handleOrderTypeChange` clone `handleVatChange` (gọi `onShippingFormChange(values)` + `onApplyFilters({ shippingForm: values })`; tương tự `orderType`). Props `shippingForm`, `onShippingFormChange`, `orderType`, `onOrderTypeChange` thêm vào Props type của OrdersPage và được `routed-pages.tsx`/`useOrdersPageProps()` truyền từ `shell.orders` (mirror đúng cách `vat`/`onVatChange` đang chảy — xem `useOrdersPageProps` lines 63-158).

(d) Truyền xuống `OrdersFilterPanel` (1305-1355): `shippingFormOptions={shippingFormOptions} shippingForm={shippingForm} onShippingFormChange={handleShippingFormChange}` + bộ orderType tương tự.

(e) Truyền dialog (1679-1691): thêm `outsideMode={isOutside}`.

(f) Submit create (1006-1032): mirror `withLandingSource` — thêm helper:

```ts
const withOutsideFlag = (values: CreateOrderPayload): CreateOrderPayload =>
  isOutside ? { ...values, outside: true } : values;
```

và bọc: `onCreateOrder(withOutsideFlag(withLandingSource(values)))`.

- [ ] **Step 4: OrdersFilterPanel + OrderCell**

(a) `OrdersFilterPanel.tsx`: thêm 6 props (`shippingFormOptions`, `shippingForm`, `onShippingFormChange`, `orderTypeOptions`, `orderType`, `onOrderTypeChange`) vào Props type (cạnh vat ~105-107); render 2 block MultiSelect mirror block vat (352-367), label dùng `t("orders.filters.shippingForm", "Hình thức gửi")` / `t("orders.filters.orderType", "Loại đơn hàng")`, id `orders-filter-shipping-form` / `orders-filter-order-type`.

(b) `OrderCell.tsx`:
- Select inline `order_type` (554-584): mở rộng options từ 3 → 7 giá trị (thêm 4 option mới, label qua cùng cơ chế label hiện dùng trong cell).
- Thêm case cột `shipping_form`: render text label `t(\`shippingForm.${value}\`)` khi có giá trị, "—" khi rỗng (đọc pattern case text đơn giản sẵn có trong file, ví dụ cột `branch_id`, và mirror; không cần inline-edit cho cột này).

- [ ] **Step 5: Verify**

```bash
cd frontend && npm run lint && npm run typecheck && npm run test:ci
```

Expected: sạch (trừ baseline fail đã ghi ở Global Constraints).

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(fe): outside orders page, new filters, table column"
```

---

### Task 12: OrderFormDialog — 3 field outside + bỏ lịch sử khi tạo

**Files:**
- Modify: `frontend/src/features/orders/OrderFormDialog.tsx` (props ~157-177; order_type select 1223-1253; block lịch sử 1471-1550+; fetch effects 836-879 + 899-933)
- Test: FE suite (typecheck + vitest; không có component test OrderFormDialog baseline)

**Interfaces:**
- Consumes: prop `outsideMode?: boolean` (OrdersPage truyền `outsideMode={isOutside}` — Task 11); form values `shipping_form`/`recall_code` (Task 9).
- Produces: dialog create ở trang outside submit values chứa `shipping_form`, `recall_code` (OrdersPage inject `outside: true`).

- [ ] **Step 1: Thêm prop + fields**

(a) Props: thêm `outsideMode?: boolean;` (cạnh `kocMode` line 171), destructure `outsideMode = false`.

(b) Select `order_type` (1223-1253): danh sách `SelectItem` — khi `outsideMode` render đủ 7 giá trị, ngược lại giữ 3 giá trị cũ:

```tsx
{(outsideMode
  ? ["normal", "no_return", "has_return", "same_product", "different_product", "purchase", "warranty_return"]
  : ["normal", "no_return", "has_return"]
).map((v) => (
  <SelectItem key={v} value={v}>{orderTypeLabel(v)}</SelectItem>
))}
```

(c) Ngay sau block field order_type, thêm block chỉ render khi `outsideMode`:

```tsx
{outsideMode && (
  <>
    <div>
      <label className="mb-1 block text-sm text-muted-foreground">
        {t("orders.fields.shippingForm", "Hình thức gửi")}
      </label>
      <Select
        value={form.shipping_form || undefined}
        onValueChange={(value) => setForm((prev) => ({ ...prev, shipping_form: value }))}
      >
        <SelectTrigger className="w-full">
          <SelectValue placeholder={t("common.select", "Chọn...")} />
        </SelectTrigger>
        <SelectContent>
          {SHIPPING_FORM_VALUES.map((v) => (
            <SelectItem key={v} value={v}>
              {t(`shippingForm.${v}`, v)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
    <div>
      <label className="mb-1 block text-sm text-muted-foreground">
        {t("orders.fields.recallCode", "Mã thu hồi")}
      </label>
      <Input
        value={form.recall_code}
        onChange={(e) => setForm((prev) => ({ ...prev, recall_code: e.target.value }))}
        placeholder="RC-..."
      />
    </div>
  </>
)}
```

(Mirror đúng markup label/spacing của field order_type liền kề; import `SHIPPING_FORM_VALUES`; `Input` dùng component đang được import sẵn trong file.)

- [ ] **Step 2: Bỏ lịch sử khi tạo**

- Bọc block lịch sử (div `className="-mt-6 rounded-lg border..."` line 1471 đến hết block ~1550+) trong `{mode === "edit" && ( ... )}` — mirror pattern line 1001.
- 2 effect fetch lịch sử (836-879 và 899-933): thêm early-return `if (mode !== "edit") return;` ở đầu mỗi effect (thêm `mode` vào dependency array nếu lint yêu cầu).

- [ ] **Step 3: Verify**

```bash
cd frontend && npm run lint && npm run typecheck && npm run test:ci
```

Expected: sạch (trừ baseline). Mở nhanh bằng mắt nếu dev server sẵn: form tạo đơn ở trang Orders KHÔNG còn block lịch sử; form sửa VẪN có.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/orders/OrderFormDialog.tsx
git commit -m "feat(fe): outside fields in order form, hide history on create"
```

---

### Task 13: Verify tổng + push + MR

**Files:** không sửa code mới (chỉ fix nếu verify lòi lỗi).

- [ ] **Step 1: BE full check**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && ruff check apps && python -m pytest apps/orders apps/users apps/inventory -q --create-db'
```

Expected: ruff sạch; pytest chỉ fail các baseline đã biết (users RBAC `rbac_roles_name_key` ×2 nếu có). Bất kỳ fail mới nào = regression, phải fix trước khi tiếp.

- [ ] **Step 2: FE full check**

```bash
cd frontend && npm run lint && npm run typecheck && npm run test:ci
```

Expected: sạch trừ baseline (OrderFormDialog + 2 mobile-viewport).

- [ ] **Step 3: Push + MR**

```bash
git push -u origin feat/outside-orders-page
```

Gửi link MR cho user: `https://gitlab.com/desi3512912/desi-order/-/merge_requests/new?merge_request%5Bsource_branch%5D=feat%2Foutside-orders-page`

- [ ] **Step 4: Cập nhật spec status**

Sửa dòng `- Trạng thái:` trong `docs/superpowers/specs/2026-07-06-outside-orders-design.md` thành `Đã triển khai (chờ QA)` + commit `docs: mark outside orders spec implemented`.
