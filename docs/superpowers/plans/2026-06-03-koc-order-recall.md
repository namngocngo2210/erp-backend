# KOC Order Recall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quản lý thông tin thu hồi (mã đơn thu hồi, ngày tạo, ngày nhận, phiếu trả hàng) cho đơn KOC qua một bảng `OrderRecall` riêng, hiển thị + edit inline trên trang danh sách KOC, và thay panel "Hành vi mua hàng" bằng panel thu hồi ở trang chi tiết KOC.

**Architecture:** Bảng `OrderRecall` quan hệ 1-1 với `Order` (`related_name="recall"`), mirror đúng pattern `OrderAssessment` (CRM). Đọc qua `OrderSerializer.get_recall` (field `recall`), ghi qua object con lồng nhau `recall` trong `OrderInlineUpdateSerializer` → service upsert. Dùng chung endpoint `PATCH /api/orders/{id}/`. Frontend thêm 4 cột inline (chỉ hiện khi `isKoc`) và component `BlockRecall`.

**Tech Stack:** Django + DRF (backend), Next.js + React + TypeScript (frontend). Tests: `manage.py test` (backend), Vitest (frontend).

> **Lưu ý môi trường (theo memory):**
> - Backend test chạy bằng `python manage.py test ...` với Postgres/Redis tạm (KHÔNG dùng `pytest` — pytest.ini hỏng do BOM).
> - Trước MỖI commit phải xác nhận đang ở đúng branch `koc-order-recall-20260603` (workdir chia sẻ HEAD giữa các session).
> - Migration mới phải là `0073_*` (master vừa thêm `0072`).

---

## File Structure

**Backend (tạo mới):**
- `backend/apps/orders/migrations/0073_order_recall.py` — migration tạo bảng.

**Backend (sửa):**
- `backend/apps/orders/models.py` — thêm class `OrderRecall`.
- `backend/apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py` — thêm field `recall` + `get_recall` vào `OrderSerializer`.
- `backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py` — thêm `OrderRecallInputSerializer` + field `recall` vào `OrderInlineUpdateSerializer`.
- `backend/apps/orders/application/services/order_service_helpers.py` — thêm helper `_upsert_order_recall`.
- `backend/apps/orders/application/services/order_service_commands.py` — pop `recall` payload + gọi helper trong `update_order_inline`.
- `backend/apps/orders/repositories.py` — thêm `"recall"` vào `_with_related_crm` select_related.
- `backend/apps/orders/transport/views/order_views_impl/order_action_view.py` — thêm `"recall"` vào select_related của query detail.

**Backend (test):**
- `backend/apps/orders/tests/test_order_recall_api.py` — tạo mới.

**Frontend (tạo mới):**
- `frontend/src/features/orders/BlockRecall.tsx` — panel thu hồi cho trang chi tiết.

**Frontend (sửa):**
- `frontend/src/features/orders/order-types.ts` — type `OrderRecallRow`, `OrderRow.recall`, 4 `OrderColumnKey`, options, field-by-key.
- `frontend/src/features/orders/order-detail-shared.tsx` — thêm `"date"` vào `EditType` + branch render trong `EditableCell`.
- `frontend/src/components/OrdersTable.tsx` — helper `renderRecallText`/`renderRecallDate` + 4 case trong `OrderCell`.
- `frontend/src/pages/OrdersPage.tsx` — gating cột thu hồi theo `isKoc`.
- `frontend/src/pages/OrderDetailPage.tsx` — render `BlockRecall` khi `isKoc`, ngược lại `BlockCrmRating`.
- `frontend/src/i18n/messages/orders.ts` — labels (vi + en).

---

## Task 1: Model `OrderRecall` + migration

**Files:**
- Modify: `backend/apps/orders/models.py` (thêm class mới, đặt ngay sau `OrderNeed`, khoảng dòng 360+)
- Create: `backend/apps/orders/migrations/0073_order_recall.py`
- Test: `backend/apps/orders/tests/test_order_recall_api.py`

- [ ] **Step 1: Viết test thất bại — tạo được bản ghi OrderRecall 1-1 với Order**

Tạo file `backend/apps/orders/tests/test_order_recall_api.py`:

```python
import datetime

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, UserChannel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderRecall
from apps.orders.models import OrderSource
from apps.users.models import User, UserRole


class OrderRecallModelTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="koc-recall", name="KOC Recall")
        self.customer = Customer.objects.create(
            full_name="KOC Cust",
            phone_raw="0909111222",
            phone_normalized="0909111222",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="KOC Cust",
            phone="0909111222",
            sku="KOC01",
            quantity=1,
            total_amount="500000.00",
            order_source=OrderSource.KOC,
        )

    def test_create_recall_one_to_one(self):
        recall = OrderRecall.objects.create(
            order=self.order,
            recall_code="TH001",
            recall_created_date=datetime.date(2026, 6, 1),
            recall_received_date=datetime.date(2026, 6, 3),
            return_slip_code="PTH001",
        )
        self.assertEqual(self.order.recall, recall)
        self.assertEqual(recall.recall_code, "TH001")
        self.assertEqual(recall.return_slip_code, "PTH001")
        self.assertEqual(recall.recall_created_date, datetime.date(2026, 6, 1))
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python manage.py test apps.orders.tests.test_order_recall_api.OrderRecallModelTests -v 2`
Expected: FAIL — `ImportError: cannot import name 'OrderRecall'`.

- [ ] **Step 3: Thêm class `OrderRecall` vào `models.py`**

Thêm ngay sau class `OrderNeed` (sau dòng ~371). `User` reference dùng `settings.AUTH_USER_MODEL` (đã import `from django.conf import settings` ở đầu file):

```python
class OrderRecall(models.Model):
    """Thông tin thu hồi cho đơn KOC (order_source="koc"). Quan hệ 1-1 với Order,
    mirror pattern OrderAssessment. Dữ liệu do nhân viên nhập tay (inline edit)."""

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="recall")
    recall_code = models.CharField(max_length=64, blank=True, default="")          # Mã đơn thu hồi
    recall_created_date = models.DateField(null=True, blank=True)                  # Ngày tạo đơn thu hồi
    recall_received_date = models.DateField(null=True, blank=True)                 # Ngày nhận hàng thu hồi
    return_slip_code = models.CharField(max_length=64, blank=True, default="")     # Phiếu trả hàng (mã/text)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_order_recalls",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "order_recalls"
        indexes = [
            models.Index(fields=["recall_code"], name="order_recall_code_idx"),
            models.Index(fields=["created_at"], name="order_recall_created_idx"),
        ]
```

- [ ] **Step 4: Tạo migration**

Run: `python manage.py makemigrations orders`
Expected: tạo file `0073_order_recall.py` (tên auto). Nếu tên khác `0073_order_recall`, đổi tên file cho khớp convention rồi sửa `name=` trong `dependencies` nếu cần. Kiểm tra dependency trỏ tới `0072_order_phone_norm_trgm_index`.

- [ ] **Step 5: Chạy test để xác nhận PASS**

Run: `python manage.py test apps.orders.tests.test_order_recall_api.OrderRecallModelTests -v 2`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git branch --show-current   # phải là koc-order-recall-20260603
git add backend/apps/orders/models.py backend/apps/orders/migrations/0073_order_recall.py backend/apps/orders/tests/test_order_recall_api.py
git commit -m "Add OrderRecall model and migration for KOC recall"
```

---

## Task 2: Đọc `recall` qua OrderSerializer

**Files:**
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py` (class `OrderSerializer`, dòng 127-214 + method area ~274)
- Modify: `backend/apps/orders/repositories.py:79-89` (`_with_related_crm`)
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_action_view.py:28-33`
- Test: `backend/apps/orders/tests/test_order_recall_api.py`

- [ ] **Step 1: Viết test thất bại — GET đơn trả về object `recall`**

Thêm class test vào `test_order_recall_api.py`:

```python
class OrderRecallReadApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username="admin-recall-read", password="x", role=UserRole.ADMIN)
        self.channel = Channel.objects.create(code="koc-recall-read", name="KOC Recall Read")
        self.customer = Customer.objects.create(
            full_name="KOC Read",
            phone_raw="0909111333",
            phone_normalized="0909111333",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="KOC Read",
            phone="0909111333",
            sku="KOC02",
            quantity=1,
            total_amount="500000.00",
            order_source=OrderSource.KOC,
        )

    def test_get_order_without_recall_returns_null(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/orders/{self.order.id}")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["recall"])

    def test_get_order_with_recall_returns_fields(self):
        OrderRecall.objects.create(
            order=self.order,
            recall_code="TH010",
            recall_created_date=datetime.date(2026, 6, 1),
            recall_received_date=datetime.date(2026, 6, 2),
            return_slip_code="PTH010",
        )
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/orders/{self.order.id}")
        self.assertEqual(response.status_code, 200)
        recall = response.data["recall"]
        self.assertEqual(recall["recall_code"], "TH010")
        self.assertEqual(recall["recall_created_date"], "2026-06-01")
        self.assertEqual(recall["recall_received_date"], "2026-06-02")
        self.assertEqual(recall["return_slip_code"], "PTH010")
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python manage.py test apps.orders.tests.test_order_recall_api.OrderRecallReadApiTests -v 2`
Expected: FAIL — `KeyError: 'recall'` (field chưa tồn tại).

- [ ] **Step 3: Thêm field `recall` + `get_recall` vào `OrderSerializer`**

Trong `order_core_serializers.py`, sau dòng `crm = serializers.SerializerMethodField()` (dòng 144) thêm:

```python
    recall = serializers.SerializerMethodField()
```

Trong `Meta.fields` (dòng 149-214), thêm `"recall",` ngay sau `"crm",` (dòng 205):

```python
            "crm",
            "recall",
```

Thêm method (đặt ngay sau `get_crm`, sau dòng ~290 — cuối block get_crm):

```python
    def get_recall(self, obj) -> dict | None:
        recall = getattr(obj, "recall", None)
        if recall is None:
            return None
        return {
            "recall_code": recall.recall_code,
            "recall_created_date": (
                recall.recall_created_date.isoformat() if recall.recall_created_date else None
            ),
            "recall_received_date": (
                recall.recall_received_date.isoformat() if recall.recall_received_date else None
            ),
            "return_slip_code": recall.return_slip_code,
        }
```

- [ ] **Step 4: Thêm `select_related("recall")` để tránh N+1**

Trong `repositories.py`, sửa `_with_related_crm` (dòng 79-85) thêm `"recall"` và `"recall__updated_by"` vào `select_related`:

```python
        qs = queryset.select_related(
            "customer",
            "channel",
            "kiotviet_channel",
            "staff",
            "assessment",
            "assessment__updated_by",
            "recall",
            "recall__updated_by",
        ).prefetch_related(
```

Trong `order_action_view.py` (dòng 28-33), thêm `"recall"`:

```python
    order = (
        Order.objects.select_related("channel", "kiotviet_channel", "staff", "recall")
        .prefetch_related("items")
        .filter(id=order_id, deleted_at__isnull=True)
        .first()
    )
```

- [ ] **Step 5: Chạy test để xác nhận PASS**

Run: `python manage.py test apps.orders.tests.test_order_recall_api.OrderRecallReadApiTests -v 2`
Expected: PASS (cả 2 test).

- [ ] **Step 6: Commit**

```bash
git branch --show-current   # phải là koc-order-recall-20260603
git add backend/apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py backend/apps/orders/repositories.py backend/apps/orders/transport/views/order_views_impl/order_action_view.py backend/apps/orders/tests/test_order_recall_api.py
git commit -m "Expose recall field on order serializer"
```

---

## Task 3: Ghi `recall` qua inline update (upsert + validation)

**Files:**
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py:225-301`
- Modify: `backend/apps/orders/application/services/order_service_helpers.py` (thêm helper)
- Modify: `backend/apps/orders/application/services/order_service_commands.py:253-368`
- Test: `backend/apps/orders/tests/test_order_recall_api.py`

- [ ] **Step 1: Viết test thất bại — PATCH tạo/cập nhật recall + validation**

Thêm class test vào `test_order_recall_api.py`:

```python
class OrderRecallWriteApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username="admin-recall-write", password="x", role=UserRole.ADMIN)
        self.channel = Channel.objects.create(code="koc-recall-write", name="KOC Recall Write")
        self.customer = Customer.objects.create(
            full_name="KOC Write",
            phone_raw="0909111444",
            phone_normalized="0909111444",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="KOC Write",
            phone="0909111444",
            sku="KOC03",
            quantity=1,
            total_amount="500000.00",
            order_source=OrderSource.KOC,
        )

    def test_patch_creates_recall(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            f"/api/orders/{self.order.id}",
            {
                "recall": {
                    "recall_code": "TH100",
                    "recall_created_date": "2026-06-01",
                    "recall_received_date": "2026-06-03",
                    "return_slip_code": "PTH100",
                }
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        recall = OrderRecall.objects.get(order=self.order)
        self.assertEqual(recall.recall_code, "TH100")
        self.assertEqual(recall.recall_created_date, datetime.date(2026, 6, 1))
        self.assertEqual(recall.return_slip_code, "PTH100")
        self.assertEqual(recall.updated_by_id, self.admin.id)

    def test_patch_updates_existing_recall_no_duplicate(self):
        OrderRecall.objects.create(order=self.order, recall_code="OLD")
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            f"/api/orders/{self.order.id}",
            {"recall": {"recall_code": "NEW"}},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(OrderRecall.objects.filter(order=self.order).count(), 1)
        self.order.recall.refresh_from_db()
        self.assertEqual(self.order.recall.recall_code, "NEW")

    def test_patch_clears_field_with_null(self):
        OrderRecall.objects.create(
            order=self.order,
            recall_code="TH200",
            recall_received_date=datetime.date(2026, 6, 5),
        )
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            f"/api/orders/{self.order.id}",
            {"recall": {"recall_code": "", "recall_received_date": None}},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        recall = OrderRecall.objects.get(order=self.order)
        self.assertEqual(recall.recall_code, "")
        self.assertIsNone(recall.recall_received_date)

    def test_patch_rejects_bad_date(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            f"/api/orders/{self.order.id}",
            {"recall": {"recall_created_date": "not-a-date"}},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python manage.py test apps.orders.tests.test_order_recall_api.OrderRecallWriteApiTests -v 2`
Expected: FAIL — recall không được ghi (serializer bỏ qua field `recall` chưa khai báo) nên `OrderRecall.objects.get` raise `DoesNotExist`.

- [ ] **Step 3: Thêm `OrderRecallInputSerializer` + field `recall` vào `OrderInlineUpdateSerializer`**

Trong `kiotviet_landing_serializers.py`, thêm class này ngay TRƯỚC `class OrderInlineUpdateSerializer` (trước dòng 225):

```python
class OrderRecallInputSerializer(serializers.Serializer):
    recall_code = serializers.CharField(max_length=64, required=False, allow_blank=True, allow_null=True)
    recall_created_date = serializers.DateField(required=False, allow_null=True)
    recall_received_date = serializers.DateField(required=False, allow_null=True)
    return_slip_code = serializers.CharField(max_length=64, required=False, allow_blank=True, allow_null=True)
```

Trong `OrderInlineUpdateSerializer`, sau dòng `crm = OrderCRMInputSerializer(required=False, allow_null=True)` (dòng 250) thêm:

```python
    recall = OrderRecallInputSerializer(required=False, allow_null=True)
```

Trong `Meta.fields`, sau `"crm",` (dòng 299) thêm `"recall",`:

```python
            "crm",
            "recall",
            "created_at",
```

- [ ] **Step 4: Thêm helper `_upsert_order_recall`**

Trong `order_service_helpers.py`, thêm import ở phần import model `OrderRecall` (cùng nơi import `OrderAssessment`). Tìm dòng import hiện có (vd `from apps.orders.models import ... OrderAssessment ...`) và thêm `OrderRecall`. Thêm static method vào class `OrderServiceHelpers` (đặt ngay sau `_upsert_order_assessment`):

```python
    @staticmethod
    def _upsert_order_recall(order: "Order", recall_payload: dict | None, actor):
        """Upsert OrderRecall (1-1) từ payload inline. None = không đụng tới recall."""
        if recall_payload is None:
            return getattr(order, "recall", None)

        from apps.orders.models import OrderRecall

        recall, _created = OrderRecall.objects.get_or_create(
            order=order,
            defaults={"updated_by": actor},
        )
        char_fields = {"recall_code", "return_slip_code"}
        allowed = ("recall_code", "recall_created_date", "recall_received_date", "return_slip_code")
        changed: list[str] = []
        for field_name in allowed:
            if field_name not in recall_payload:
                continue
            value = recall_payload[field_name]
            if field_name in char_fields and value is None:
                value = ""
            setattr(recall, field_name, value)
            changed.append(field_name)
        if changed:
            recall.updated_by = actor
            recall.save(update_fields=[*changed, "updated_by", "updated_at"])
        return recall
```

> Nếu file đã import `OrderRecall` ở top thì bỏ dòng `from apps.orders.models import OrderRecall` cục bộ và dùng import top-level. Chọn 1 cách, không import 2 lần.

- [ ] **Step 5: Pop `recall` payload + gọi helper trong `update_order_inline`**

Trong `order_service_commands.py`, sau dòng `crm_payload = data.pop("crm", None)` (dòng 256) thêm:

```python
        recall_payload = data.pop("recall", None)
```

Sau block upsert assessment (sau dòng 368 `order.assessment = assessment`) thêm:

```python
        recall = OrderServiceHelpers._upsert_order_recall(order, recall_payload, actor)
        if recall is not None:
            order.recall = recall
```

> Xác nhận `OrderServiceHelpers` đã được import trong file này (assessment upsert đã gọi qua `OrderServiceCommands._upsert_order_assessment` — kiểm tra cách gọi thực tế; nếu helper được expose qua `OrderServiceCommands`, thì thêm một wrapper `_upsert_order_recall` tương ứng hoặc gọi trực tiếp `OrderServiceHelpers._upsert_order_recall`). Dùng cùng cơ chế gọi như `_upsert_order_assessment` đang dùng ở dòng 364.

- [ ] **Step 6: Chạy test để xác nhận PASS**

Run: `python manage.py test apps.orders.tests.test_order_recall_api.OrderRecallWriteApiTests -v 2`
Expected: PASS (cả 4 test).

- [ ] **Step 7: Chạy toàn bộ test recall + smoke test inline edit cũ**

Run: `python manage.py test apps.orders.tests.test_order_recall_api apps.orders.tests.test_order_inline_edit_api -v 2`
Expected: PASS (không vỡ luồng inline edit hiện có).

- [ ] **Step 8: Commit**

```bash
git branch --show-current   # phải là koc-order-recall-20260603
git add backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py backend/apps/orders/application/services/order_service_helpers.py backend/apps/orders/application/services/order_service_commands.py backend/apps/orders/tests/test_order_recall_api.py
git commit -m "Upsert order recall on inline update"
```

---

## Task 4: Frontend types + i18n labels

**Files:**
- Modify: `frontend/src/features/orders/order-types.ts`
- Modify: `frontend/src/i18n/messages/orders.ts`

- [ ] **Step 1: Thêm type `OrderRecallRow` + `OrderRow.recall`**

Trong `order-types.ts`, thêm type mới ngay trước `export type OrderRow` (trước dòng 103):

```ts
export type OrderRecallRow = {
  recall_code?: string | null;
  recall_created_date?: string | null; // YYYY-MM-DD
  recall_received_date?: string | null; // YYYY-MM-DD
  return_slip_code?: string | null;
};
```

Trong `OrderRow`, thêm sau `crm?: OrderCRMRow | null;` (dòng 161):

```ts
  recall?: OrderRecallRow | null;
```

- [ ] **Step 2: Thêm 4 `OrderColumnKey`**

Trong union `OrderColumnKey`, thêm 4 key ở cuối (sau `"purchase_barriers"`, dòng 229 — đổi dấu `;` cuối thành tiếp tục union):

```ts
  | "purchase_barriers"
  | "recall_code"
  | "recall_created_date"
  | "recall_received_date"
  | "return_slip_code";
```

- [ ] **Step 3: Thêm vào `ORDER_COLUMN_OPTIONS`**

Trong mảng `ORDER_COLUMN_OPTIONS`, thêm 4 entry trước dấu `]` đóng mảng (sau dòng 278):

```ts
  { key: "recall_code", label: "orders.columns.recallCode" },
  { key: "recall_created_date", label: "orders.columns.recallCreatedDate" },
  { key: "recall_received_date", label: "orders.columns.recallReceivedDate" },
  { key: "return_slip_code", label: "orders.columns.returnSlipCode" },
```

- [ ] **Step 4: Thêm vào `ORDER_COLUMN_FIELD_BY_KEY`**

Trong object `ORDER_COLUMN_FIELD_BY_KEY`, thêm trước dấu `}` đóng (sau dòng 357):

```ts
  recall_code: "recall_code",
  recall_created_date: "recall_created_date",
  recall_received_date: "recall_received_date",
  return_slip_code: "return_slip_code",
```

- [ ] **Step 5: Thêm i18n labels (vi + en)**

Trong `i18n/messages/orders.ts`, ở block `vi:` thêm cạnh các key `orders.columns.*` (vd sau dòng 50 `"orders.columns.invoiceCode": "Mã hóa đơn",`):

```ts
    "orders.columns.recallCode": "Mã đơn thu hồi",
    "orders.columns.recallCreatedDate": "Ngày tạo đơn thu hồi",
    "orders.columns.recallReceivedDate": "Ngày nhận hàng thu hồi",
    "orders.columns.returnSlipCode": "Phiếu trả hàng",
    "orders.recall.title": "Thu hồi",
```

Ở block `en:` (cạnh dòng 234 `"orders.columns.invoiceCode": "Invoice Code",`):

```ts
    "orders.columns.recallCode": "Recall Code",
    "orders.columns.recallCreatedDate": "Recall Created Date",
    "orders.columns.recallReceivedDate": "Recall Received Date",
    "orders.columns.returnSlipCode": "Return Slip",
    "orders.recall.title": "Recall",
```

- [ ] **Step 6: Kiểm tra typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: KHÔNG có lỗi mới. (Lưu ý: nếu `OrderCell` switch dùng exhaustive checking, sẽ báo lỗi thiếu case cho 4 key mới — đó là kỳ vọng, sẽ xử lý ở Task 6. Nếu lỗi chỉ ở `OrderCell`/`OrdersTable`, bỏ qua tạm và tiếp tục; nếu lỗi ở chỗ khác phải sửa.)

- [ ] **Step 7: Commit**

```bash
git branch --show-current   # phải là koc-order-recall-20260603
git add frontend/src/features/orders/order-types.ts frontend/src/i18n/messages/orders.ts
git commit -m "Add recall types and i18n labels for KOC orders"
```

---

## Task 5: `EditableCell` date type + `BlockRecall` + swap ở trang chi tiết

**Files:**
- Modify: `frontend/src/features/orders/order-detail-shared.tsx:247` (EditType) + EditableCell branch
- Create: `frontend/src/features/orders/BlockRecall.tsx`
- Modify: `frontend/src/pages/OrderDetailPage.tsx:372` (prop) + `:721-727` (render)

- [ ] **Step 1: Thêm `"date"` vào `EditType` + branch render trong `EditableCell`**

Trong `order-detail-shared.tsx`, sửa dòng 247:

```ts
export type EditType = "text" | "number" | "select" | "toggle" | "datetime" | "date";
```

Thêm branch xử lý `date` NGAY TRƯỚC comment `// Text / Number` (trước dòng 462). Date lưu thẳng dạng `YYYY-MM-DD`, không round-trip ISO:

```tsx
  // Date — native date input, value dạng YYYY-MM-DD (không kèm giờ/TZ).
  if (type === "date") {
    const dateStr = currentValue ? String(currentValue).slice(0, 10) : "";
    if (editing) {
      const commit = () => {
        if (saveRef.current) return;
        saveRef.current = true;
        setEditing(false);
        const next = draft.trim();
        if (next === dateStr) return;
        if (immediate) onSave(next);
        else {
          setLocalValue(next);
          onChange?.(next);
        }
      };
      return (
        <input
          autoFocus
          type="date"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit();
            }
            if (e.key === "Escape") setEditing(false);
          }}
          className="w-full rounded-md border border-primary bg-background px-2 py-0.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
        />
      );
    }
    const beginDateEdit = () => {
      setDraft(dateStr);
      setEditing(true);
      saveRef.current = false;
    };
    const shownDate =
      display ??
      (dateStr ? (
        <span className="text-sm font-medium text-foreground">{dateStr}</span>
      ) : (
        <span className="text-muted-foreground/30">{placeholder}</span>
      ));
    return (
      <span
        role="button"
        tabIndex={0}
        onClick={beginDateEdit}
        onKeyDown={(e) => e.key === "Enter" && beginDateEdit()}
        title="Nhấn để chỉnh sửa"
        className={[
          "cursor-text rounded px-1 -mx-1 hover:bg-accent/50 focus:outline-none focus:ring-1 focus:ring-primary/40 transition-colors",
          hasPendingChange ? "ring-2 ring-amber-400 ring-offset-1" : "",
        ].join(" ")}
      >
        {isValidElement(shownDate) ? (
          shownDate
        ) : (
          <span className="text-sm font-medium text-foreground">{shownDate}</span>
        )}
      </span>
    );
  }
```

- [ ] **Step 2: Tạo component `BlockRecall.tsx`**

Create `frontend/src/features/orders/BlockRecall.tsx`:

```tsx
import { memo } from "react";
import { Undo2 } from "lucide-react";
import { type OrderRecallRow, type OrderRow } from "@/features/orders/order-types";
import { ERow, SectionCard } from "@/features/orders/order-detail-shared";
import { useI18n } from "@/i18n";

function BlockRecallImpl({
  order,
  patch,
}: {
  order: OrderRow;
  patch: (p: Partial<OrderRow>) => void;
}) {
  const { t } = useI18n();
  const recall = order.recall;

  // Chỉ gửi đúng field user vừa sửa; OrderDetailPage deep-merge `recall` vào pendingRows.
  const patchRecall = (fields: Partial<OrderRecallRow>) => {
    patch({ recall: fields as any });
  };

  return (
    <SectionCard icon={<Undo2 className="h-4 w-4" />} title={t("orders.recall.title")}>
      <ERow
        label={t("orders.columns.recallCode")}
        rawValue={recall?.recall_code ?? ""}
        type="text"
        onSave={(v) => patchRecall({ recall_code: String(v) })}
      />
      <ERow
        label={t("orders.columns.recallCreatedDate")}
        rawValue={recall?.recall_created_date ?? ""}
        type="date"
        onSave={(v) => patchRecall({ recall_created_date: v ? String(v) : null })}
      />
      <ERow
        label={t("orders.columns.recallReceivedDate")}
        rawValue={recall?.recall_received_date ?? ""}
        type="date"
        onSave={(v) => patchRecall({ recall_received_date: v ? String(v) : null })}
      />
      <ERow
        label={t("orders.columns.returnSlipCode")}
        rawValue={recall?.return_slip_code ?? ""}
        type="text"
        onSave={(v) => patchRecall({ return_slip_code: String(v) })}
      />
    </SectionCard>
  );
}

export const BlockRecall = memo(BlockRecallImpl);
```

> Kiểm tra path import `useI18n` cho khớp codebase: BlockCrmRating import hook ở đâu? Nếu khác `@/i18n`, dùng đúng path đó. (Tìm: `grep -rn "useI18n" frontend/src/features/orders/BlockCrmRating.tsx` hoặc file lân cận.)

- [ ] **Step 3: Thread `isKoc` + swap render trong `OrderDetailPage.tsx`**

`isKoc?: boolean;` đã khai báo (dòng 372). Destructure nó trong function body của `OrderDetailPage` (thêm vào danh sách props được destructure cùng `order`, `patch`, ...). Thêm import ở đầu file cạnh import `BlockCrmRating`:

```tsx
import { BlockRecall } from "@/features/orders/BlockRecall";
```

Sửa block render (dòng 721-727) thành:

```tsx
                {/* KOC: panel thu hồi thay cho hành vi mua hàng */}
                <div className="w-full">
                  {isKoc ? (
                    <BlockRecall order={effectiveOrder || order} patch={patch} />
                  ) : (
                    <BlockCrmRating
                      order={effectiveOrder || order}
                      patch={patch}
                      crmCatalog={crmCatalog}
                    />
                  )}
                </div>
```

> Xác nhận cách `isKoc` được nhận: nếu `OrderDetailPage` nhận props qua một object `props` (không destructure), dùng `props.isKoc`. Kiểm tra signature ở dòng ~380.

- [ ] **Step 4: Verify build/typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: KHÔNG lỗi mới ở `order-detail-shared.tsx`, `BlockRecall.tsx`, `OrderDetailPage.tsx`. (Lỗi exhaustive ở `OrderCell` nếu có vẫn còn — xử lý Task 6.)

- [ ] **Step 5: Commit**

```bash
git branch --show-current   # phải là koc-order-recall-20260603
git add frontend/src/features/orders/order-detail-shared.tsx frontend/src/features/orders/BlockRecall.tsx frontend/src/pages/OrderDetailPage.tsx
git commit -m "Show recall panel on KOC order detail page"
```

---

## Task 6: Cột inline trên trang danh sách KOC + gating theo `isKoc`

**Files:**
- Modify: `frontend/src/components/OrdersTable.tsx` (helper + `OrderCell` cases)
- Modify: `frontend/src/pages/OrdersPage.tsx` (gating cột theo `isKoc`)

- [ ] **Step 1: Thêm helper `renderRecallText` và `renderRecallDate`**

Trong `OrdersTable.tsx`, ngay sau hàm `renderCrmText` (sau dòng 1348) thêm:

```tsx
function renderRecallText(
  row: OrderRow,
  onPatch:
    | ((id: number, payload: Partial<OrderRow>, silent?: boolean) => Promise<void>)
    | undefined,
  field: keyof OrderRecallRow,
  current: string | null | undefined,
  minWidthClass = "min-w-[130px]",
) {
  const canEdit = !!row.can_edit && !!onPatch;
  const value = current ?? "";
  return (
    <input
      type="text"
      className={`h-7 w-full ${minWidthClass} rounded border border-border bg-transparent px-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-ring disabled:cursor-default disabled:opacity-60`}
      defaultValue={value}
      disabled={!canEdit}
      onClick={(e) => e.stopPropagation()}
      onBlur={(e) => {
        const next = e.target.value.trim();
        if (next === (value || "").trim()) return;
        onPatch?.(row.id, { recall: { [field]: next || null } } as Partial<OrderRow>);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
      }}
    />
  );
}

function renderRecallDate(
  row: OrderRow,
  onPatch:
    | ((id: number, payload: Partial<OrderRow>, silent?: boolean) => Promise<void>)
    | undefined,
  field: keyof OrderRecallRow,
  current: string | null | undefined,
) {
  const canEdit = !!row.can_edit && !!onPatch;
  const value = current ? String(current).slice(0, 10) : "";
  return (
    <input
      type="date"
      className="h-7 w-full min-w-[140px] rounded border border-border bg-transparent px-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-ring disabled:cursor-default disabled:opacity-60"
      defaultValue={value}
      disabled={!canEdit}
      onClick={(e) => e.stopPropagation()}
      onBlur={(e) => {
        const next = e.target.value;
        if (next === value) return;
        onPatch?.(row.id, { recall: { [field]: next || null } } as Partial<OrderRow>);
      }}
    />
  );
}
```

- [ ] **Step 2: Import `OrderRecallRow` trong OrdersTable**

Đảm bảo `OrderRecallRow` được import. Tìm dòng import từ `order-types` (vd `import { ..., type OrderCRMRow, ... } from ...` hoặc qua barrel) và thêm `OrderRecallRow`. Nếu `OrderCRMRow` đang được import ở đầu file thì thêm `OrderRecallRow` cạnh nó.

- [ ] **Step 3: Thêm 4 case vào `OrderCell` switch**

Trong `OrderCell` (switch trên `columnKey`), thêm 4 case ngay TRƯỚC `default:` (trước dòng 1265):

```tsx
    case "recall_code":
      return renderRecallText(row, onPatch, "recall_code", row.recall?.recall_code);
    case "return_slip_code":
      return renderRecallText(row, onPatch, "return_slip_code", row.recall?.return_slip_code);
    case "recall_created_date":
      return renderRecallDate(row, onPatch, "recall_created_date", row.recall?.recall_created_date);
    case "recall_received_date":
      return renderRecallDate(row, onPatch, "recall_received_date", row.recall?.recall_received_date);
```

- [ ] **Step 4: Gating cột thu hồi chỉ hiện khi `isKoc` trong `OrdersPage.tsx`**

Thêm hằng số ở module scope (đầu file, sau các import) hoặc ngay trên component:

```ts
const RECALL_COLUMN_KEYS: OrderColumnKey[] = [
  "recall_code",
  "recall_created_date",
  "recall_received_date",
  "return_slip_code",
];
```

Sửa `sortedColumnOptions` (dòng 635-643) để loại 4 cột recall khi `!isKoc`:

```ts
  const sortedColumnOptions = useMemo(() => {
    const baseOptions = isKoc
      ? ORDER_COLUMN_OPTIONS
      : ORDER_COLUMN_OPTIONS.filter((option) => !RECALL_COLUMN_KEYS.includes(option.key));
    const optionByKey = new Map(baseOptions.map((option) => [option.key, option]));
    const visibleSet = new Set(visibleOrderColumns);
    const visibleOptions = visibleOrderColumns
      .map((key) => optionByKey.get(key))
      .filter((option): option is (typeof ORDER_COLUMN_OPTIONS)[number] => Boolean(option));
    const hiddenOptions = baseOptions.filter((option) => !visibleSet.has(option.key));
    return [...visibleOptions, ...hiddenOptions];
  }, [visibleOrderColumns, isKoc]);
```

Tạo danh sách cột thực sự render xuống bảng, loại recall khi `!isKoc` (phòng trường hợp key recall bị persist từ trang KOC nhưng đang xem trang thường). Thêm ngay trên chỗ `<OrdersTable ... visibleColumns={visibleOrderColumns} ...>` (dòng ~1817):

```ts
  const effectiveVisibleColumns = useMemo(
    () =>
      isKoc
        ? visibleOrderColumns
        : visibleOrderColumns.filter((key) => !RECALL_COLUMN_KEYS.includes(key)),
    [visibleOrderColumns, isKoc],
  );
```

Đổi prop truyền vào `<OrdersTable>` (dòng 1817) từ `visibleColumns={visibleOrderColumns}` thành:

```tsx
              visibleColumns={effectiveVisibleColumns}
```

> Nếu trang KOC có tập cột mặc định riêng và muốn 4 cột recall xuất hiện sẵn: tìm nơi khởi tạo `visibleOrderColumns` mặc định (hook quản lý cột, prop truyền vào OrdersPage). Việc thêm vào default là tùy chọn — yêu cầu bắt buộc chỉ là "có thể bật được 4 cột này trên trang KOC", đã thỏa bằng gating ở trên. Nếu cần default, thêm `RECALL_COLUMN_KEYS` vào danh sách mặc định CHỈ cho nhánh KOC ở nơi khởi tạo đó.

- [ ] **Step 5: Verify typecheck + test frontend**

Run: `cd frontend && npx tsc --noEmit`
Expected: KHÔNG lỗi (4 case mới đã làm switch đầy đủ).

Run: `cd frontend && npx vitest run src/lib/routes.koc.test.ts`
Expected: PASS (không hồi quy route KOC).

- [ ] **Step 6: Commit**

```bash
git branch --show-current   # phải là koc-order-recall-20260603
git add frontend/src/components/OrdersTable.tsx frontend/src/pages/OrdersPage.tsx
git commit -m "Add inline recall columns to KOC orders list"
```

---

## Task 7: Kiểm thử tích hợp end-to-end + push

**Files:** (không sửa code; chạy kiểm thử)

- [ ] **Step 1: Chạy full test backend orders**

Run: `python manage.py test apps.orders -v 1`
Expected: Các test recall PASS; các fail còn lại trùng với danh sách pre-existing đã biết (xem memory về test fail sẵn — dashboard/kiotviet, crm-api, excel-import...). KHÔNG có fail MỚI liên quan tới recall/inline edit/serializer order.

- [ ] **Step 2: Build frontend**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: build thành công.

- [ ] **Step 3: Smoke test thủ công (mô tả để người review chạy)**

Trên trang `/koc-orders`:
1. Mở bộ chọn cột → thấy 4 cột "Mã đơn thu hồi", "Ngày tạo đơn thu hồi", "Ngày nhận hàng thu hồi", "Phiếu trả hàng". Bật chúng.
2. Sửa inline mã thu hồi / ngày → reload → giá trị được lưu.
3. Mở chi tiết 1 đơn KOC (`/koc-orders/:id`) → thấy panel "Thu hồi" thay cho "Hành vi mua hàng"; sửa 4 trường → lưu OK.
4. Mở `/orders` (đơn thường) → KHÔNG thấy 4 cột thu hồi trong bộ chọn cột; trang chi tiết đơn thường vẫn hiển thị "Hành vi mua hàng".

- [ ] **Step 4: Push + tạo MR**

```bash
git branch --show-current   # phải là koc-order-recall-20260603
git push -u origin koc-order-recall-20260603
```
Gửi link MR cho người dùng (theo quy ước CLAUDE.md).

---

## Self-Review

**Spec coverage:**
- Bảng thu hồi 4 trường → Task 1 (model + migration). ✓
- Đọc/ghi qua API dùng chung endpoint → Task 2 (read) + Task 3 (write upsert + validation). ✓
- Hiển thị + edit inline trên trang KOC → Task 6 (cột + gating isKoc). ✓
- Trang chi tiết KOC: ẩn hành vi mua hàng, hiện panel thu hồi → Task 5 (BlockRecall + swap). ✓
- Chỉ KOC mới thấy cột thu hồi → Task 6 Step 4 (gating). ✓
- Edge cases (chưa có recall→null, xoá field, validation date/length, đơn huỷ không sửa) → Task 2/3 tests + tận dụng rule can_edit/cancelled hiện có. ✓
- N+1 → Task 2 (`select_related("recall")`). ✓

**Type consistency:** `OrderRecallRow` (4 field optional nullable) dùng nhất quán ở `order-types.ts`, `BlockRecall.tsx`, `OrdersTable.tsx`. Field code khớp model: `recall_code`, `recall_created_date`, `recall_received_date`, `return_slip_code`. Payload ghi luôn dạng `{ recall: {...} }` ở cả inline (Task 6) và panel (Task 5), khớp `OrderRecallInputSerializer` (Task 3) và pop `recall` trong service. i18n key khớp giữa options (Task 4) và BlockRecall (Task 5): `orders.columns.recall*` + `orders.recall.title`.

**Placeholder scan:** Không có TBD/TODO; mọi step code đều có code cụ thể. Một số step có "verify import path" — đây là chỉ dẫn xác nhận pattern hiện có, kèm lệnh grep cụ thể, không phải placeholder logic.
