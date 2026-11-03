# Customer Profile Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nâng cấp trang "Khách hàng" thành bảng tổng hợp 1 dòng/khách (38 cột theo `database.xlsx`): định danh + nhân khẩu học + phân khúc + hành vi mua + sản phẩm + chân dung (assessment đơn mới nhất) + resale.

**Architecture:** Hướng A — làm giàu dữ liệu theo từng trang. Endpoint mới `GET /api/customers/profiles` phân trang trên bảng `customers`, sau đó làm giàu **chỉ cho các customer_id đang hiển thị** bằng vài query gộp `customer_id IN (...)` (metrics, đơn mới nhất, notes). Phân khúc tái dùng `segmentation.py`. Resale: ngày suy ra read-only + 2 trường text nhập tay lưu ở `loyal_customer_notes`. Không đổi shape `GET /api/customers` (order autocomplete đang phụ thuộc).

**Tech Stack:** Backend Django + DRF + PostgreSQL (pytest/`TestCase`). Frontend React + TypeScript + shadcn/ui + vitest.

**Spec:** `docs/superpowers/specs/2026-05-21-customer-profile-page-design.md`

---

## File Structure

**Backend (`backend/apps/customers/`)**
- `models.py` — MODIFY: thêm 2 cột vào `LoyalCustomerNote`.
- `migrations/000X_*.py` — CREATE: migration cho 2 cột + partial index `orders(customer_id, -created_at)`.
- `profile_service.py` — CREATE: `CustomerProfileService` (aggregate metrics + latest order + segment + resale date → list profile dict). Tách riêng để `services.py` không phình.
- `serializers.py` — MODIFY: thêm `CustomerProfileSerializer`, `CustomerResaleNoteUpdateSerializer`.
- `views.py` — MODIFY: thêm `list_customer_profiles`, `update_customer_resale_note`.
- `urls.py` — MODIFY: thêm 2 route.
- `services.py` — MODIFY: `upsert_loyal_note` nhận thêm `resale_status`/`resale_suggestion`.
- `tests/test_customer_profiles_api.py` — CREATE.
- `tests/test_customer_resale_note_api.py` — CREATE.

**Frontend (`frontend/src/`)**
- `lib/api/customers.ts` — MODIFY: thêm types `CustomerProfile*`, `getCustomerProfiles`, `updateCustomerResaleNote`.
- `pages/CustomersPage.tsx` — REWRITE: bảng tổng hợp nhóm cột + toggle + expandable row + inline edit + filter bar.
- `pages/customers/profile-columns.ts` — CREATE: cấu hình 8 nhóm cột + nhãn hiển thị.
- `lib/api/__tests__/customers.profiles.test.ts` — CREATE: test API client.

---

## Task 1: Migration — 2 cột resale + partial index

**Files:**
- Modify: `backend/apps/customers/models.py:68-96` (model `LoyalCustomerNote`)
- Create: `backend/apps/customers/migrations/` (migration mới, số thứ tự kế tiếp)

- [ ] **Step 1: Thêm 2 field vào model**

Trong `backend/apps/customers/models.py`, thêm sau `special_note` (dòng 81):

```python
    resale_status = models.TextField(blank=True, default="", help_text="Trạng thái resale (sale nhập tay)")
    resale_suggestion = models.TextField(blank=True, default="", help_text="Gợi ý SP resale tiếp theo (sale nhập tay)")
```

- [ ] **Step 2: Tạo migration**

Run: `cd backend && python manage.py makemigrations customers`
Expected: tạo file migration mới thêm 2 cột `resale_status`, `resale_suggestion`.

- [ ] **Step 3: Thêm partial index vào migration**

Mở file migration vừa tạo, thêm operation sau khi `AddField` (đảm bảo truy vấn "đơn mới nhất theo khách" + aggregate nhanh; dùng `RunSQL` với `IF NOT EXISTS` cho idempotent, `CONCURRENTLY` cần `atomic=False`):

```python
from django.db import migrations, models


class Migration(migrations.Migration):

    atomic = False  # cần cho CREATE INDEX CONCURRENTLY

    dependencies = [
        # giữ nguyên dependency mà makemigrations sinh ra
    ]

    operations = [
        migrations.AddField(
            model_name="loyalcustomernote",
            name="resale_status",
            field=models.TextField(blank=True, default="", help_text="Trạng thái resale (sale nhập tay)"),
        ),
        migrations.AddField(
            model_name="loyalcustomernote",
            name="resale_suggestion",
            field=models.TextField(blank=True, default="", help_text="Gợi ý SP resale tiếp theo (sale nhập tay)"),
        ),
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS order_cust_recent_idx "
                "ON orders (customer_id, created_at DESC) WHERE deleted_at IS NULL;"
            ),
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS order_cust_recent_idx;",
        ),
    ]
```

- [ ] **Step 4: Chạy migration**

Run: `cd backend && python manage.py migrate customers`
Expected: migration áp dụng thành công, không lỗi.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/customers/models.py backend/apps/customers/migrations/
git commit -m "Add resale note fields and recent-order index for customer profile"
```

---

## Task 2: Profile aggregation service

**Files:**
- Create: `backend/apps/customers/profile_service.py`
- Create: `backend/apps/customers/tests/test_customer_profiles_api.py` (dùng chung cho Task 2–5)

Hằng số tham chiếu (đã có trong codebase): `DELIVERED_SUCCESS_ORDER_STATUS = 3`, `CANCELLED_INVOICE_STATUS = 2` (`apps/customers/services.py`), `EXCLUDED_BRANCH_IDS` (`apps/orders/repositories.py`). Order status hoàn/hủy: 4 (IN_RETURN), 5 (RETURNED), 6 (CANCELLED).

- [ ] **Step 1: Viết test cho metrics aggregate (đỏ trước)**

Tạo `backend/apps/customers/tests/test_customer_profiles_api.py`:

```python
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.customers.profile_service import CustomerProfileService
from apps.orders.models import Order, OrderAssessment, OrderItem, OrderStatus, Product


class CustomerProfileServiceTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="lp-prof", name="Landing Profile")
        self.product = Product.objects.create(name="Ghế massage", sku="GM-001", price="0")

    def _customer(self, code, name, phone):
        return Customer.objects.create(code=code, full_name=name, phone_raw=phone, phone_normalized=phone)

    def _order(self, customer, amount, *, status=OrderStatus.DELIVERED_SUCCESS, days_ago=0, sku="GM-001",
               invoice_status=0):
        order = Order.objects.create(
            channel=self.channel, customer=customer, name=customer.full_name, phone=customer.phone_raw,
            status=status, total_after_discount=amount, invoice_status=invoice_status, sku=sku,
            product="Ghế massage", address="123 Lê Lợi", province="TP.HCM",
        )
        # created_at là auto_now_add → set lại để kiểm soát thứ tự "đơn mới nhất".
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
        order.refresh_from_db()
        OrderItem.objects.create(order=order, product=self.product, product_name="Ghế massage",
                                 sku=sku, quantity=1, unit_price=amount, line_total=amount)
        return order

    def test_metrics_exclude_cancelled_and_count_orders(self):
        cus = self._customer("KH1", "Nguyen Van A", "0900000001")
        self._order(cus, "1000000", days_ago=30)
        self._order(cus, "2000000", days_ago=5)
        # Đơn hủy hóa đơn: không tính vào metrics.
        self._order(cus, "9000000", days_ago=1, invoice_status=2)
        # Đơn hoàn: không tính vào metrics.
        self._order(cus, "5000000", status=OrderStatus.RETURNED, days_ago=2)

        profiles = CustomerProfileService.list_profiles(q="", limit=10, offset=0)
        row = next(r for r in profiles["results"] if r["identity"]["id"] == cus.id)
        self.assertEqual(row["purchase"]["total_orders"], 2)
        self.assertEqual(row["purchase"]["total_spent"], "3000000.00")
        self.assertEqual(profiles["total"], 1)
```

- [ ] **Step 2: Chạy test để xác nhận đỏ**

Run: `cd backend && python -m pytest apps/customers/tests/test_customer_profiles_api.py -v`
Expected: FAIL — `ModuleNotFoundError: apps.customers.profile_service`.

- [ ] **Step 3: Viết `profile_service.py` (phần metrics + pagination)**

Tạo `backend/apps/customers/profile_service.py`:

```python
from decimal import Decimal

from django.db.models import Count, Max, Min, Sum
from django.utils import timezone

from . import segmentation
from .models import Customer, LoyalCustomerNote
from .repositories import CustomerRepository
from .services import CANCELLED_INVOICE_STATUS, DELIVERED_SUCCESS_ORDER_STATUS
from apps.orders.models import Order
from apps.orders.repositories import EXCLUDED_BRANCH_IDS

# Trạng thái đơn hoàn/hủy — loại khỏi metrics mua hàng.
RETURNED_OR_CANCELLED_STATUSES = (4, 5, 6)


class CustomerProfileService:
    @staticmethod
    def list_profiles(*, q: str, limit: int, offset: int,
                      channel: str | None = None, sales_status: str | None = None,
                      segment: str | None = None) -> dict:
        """Danh sách hồ sơ khách hàng tổng hợp, phân trang trên bảng customers,
        làm giàu chỉ cho các customer_id của trang hiện tại."""
        base = CustomerRepository.list_customers_queryset(q)
        total = base.count()
        page = list(base[offset : offset + limit])
        customer_ids = [c.id for c in page]
        if not customer_ids:
            return {"results": [], "total": total}

        metrics = CustomerProfileService._metrics_map(customer_ids)
        results = []
        now = timezone.now()
        for customer in page:
            m = metrics.get(customer.id, {})
            total_orders = m.get("total_orders", 0) or 0
            last_purchase_at = m.get("last_purchase_at")
            days_since = (now - last_purchase_at).days if last_purchase_at else None
            segment_code = segmentation.classify_segment(
                delivered_count=total_orders,
                total_spent=m.get("total_spent") or Decimal("0"),
                days_since_last_purchase=days_since,
                has_problem=False,
            )
            results.append({
                "identity": {"id": customer.id, "code": customer.code,
                             "full_name": customer.full_name,
                             "phone": customer.phone_raw or customer.phone_normalized},
                "purchase": {
                    "first_purchase_at": m.get("first_purchase_at"),
                    "last_purchase_at": last_purchase_at,
                    "total_orders": total_orders,
                    "total_spent": str(m.get("total_spent") or Decimal("0")),
                },
                "segment": {"segment_code": segment_code},
                "resale": {
                    "last_resale_at": last_purchase_at if total_orders >= 2 else None,
                },
            })
        return {"results": results, "total": total}

    @staticmethod
    def _metrics_map(customer_ids: list[int]) -> dict[int, dict]:
        rows = (
            Order.objects.filter(customer_id__in=customer_ids, deleted_at__isnull=True)
            .exclude(status__in=RETURNED_OR_CANCELLED_STATUSES)
            .exclude(invoice_status=CANCELLED_INVOICE_STATUS)
            .exclude(branch_id__in=EXCLUDED_BRANCH_IDS)
            .values("customer_id")
            .annotate(
                total_orders=Count("id"),
                total_spent=Sum("total_after_discount"),
                first_purchase_at=Min("created_at"),
                last_purchase_at=Max("created_at"),
            )
        )
        return {r["customer_id"]: r for r in rows}
```

- [ ] **Step 4: Thêm `list_customers_queryset` vào repository**

Trong `backend/apps/customers/repositories.py`, refactor để tái dùng filter `q` (giữ `list_customers` cũ gọi qua queryset mới):

```python
from django.db.models import Q

from .models import Customer


class CustomerRepository:
    @staticmethod
    def list_customers_queryset(q: str):
        queryset = Customer.objects.filter(deleted_at__isnull=True).order_by("-created_at")
        if q:
            normalized_query = "".join(ch for ch in q if ch.isdigit())
            filters = (
                Q(full_name__icontains=q)
                | Q(phone_raw__icontains=q)
                | Q(code__icontains=q)
            )
            if normalized_query:
                filters = filters | Q(phone_normalized__icontains=normalized_query)
            queryset = queryset.filter(filters)
        return queryset

    @staticmethod
    def list_customers(q: str, *, limit: int, offset: int):
        return CustomerRepository.list_customers_queryset(q)[offset : offset + limit]

    @staticmethod
    def get_customer_by_id(customer_id: int) -> Customer | None:
        return Customer.objects.filter(id=customer_id, deleted_at__isnull=True).first()
```

- [ ] **Step 5: Chạy test để xác nhận xanh**

Run: `cd backend && python -m pytest apps/customers/tests/test_customer_profiles_api.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/customers/profile_service.py backend/apps/customers/repositories.py backend/apps/customers/tests/test_customer_profiles_api.py
git commit -m "Add customer profile metrics aggregation service"
```

---

## Task 3: Latest-order enrichment (chân dung + đơn gần nhất)

**Files:**
- Modify: `backend/apps/customers/profile_service.py`
- Modify: `backend/apps/customers/tests/test_customer_profiles_api.py`

- [ ] **Step 1: Viết test latest-order + persona (đỏ trước)**

Thêm vào `test_customer_profiles_api.py`:

```python
    def test_latest_order_persona_and_demographics(self):
        cus = self._customer("KH2", "Tran Thi B", "0900000002")
        self._order(cus, "1000000", days_ago=20)
        latest = self._order(cus, "2000000", days_ago=1)
        OrderAssessment.objects.create(
            order=latest,
            sales_status_code="closed",
            customer_classification_code="easy_close",
            pain_level_code="moderate",
            occupation_code="teacher",
            age_text="45-60",
            purchase_barrier_codes=["price"],
            delax_source_codes=["facebook"],
        )

        profiles = CustomerProfileService.list_profiles(q="0900000002", limit=10, offset=0)
        row = profiles["results"][0]
        self.assertEqual(row["latest_order"]["sales_status_code"], "closed")
        self.assertEqual(row["latest_order"]["last_order_amount"], "2000000.00")
        self.assertEqual(row["latest_order"]["last_order_status"], OrderStatus.DELIVERED_SUCCESS.value)
        self.assertEqual(row["demographics"]["province"], "TP.HCM")
        self.assertEqual(row["demographics"]["address"], "123 Lê Lợi")
        self.assertEqual(row["demographics"]["occupation_code"], "teacher")
        self.assertEqual(row["demographics"]["age_text"], "45-60")
        self.assertEqual(row["channel"]["channel_name"], "Landing Profile")
        self.assertEqual(row["persona"]["customer_classification_code"], "easy_close")
        self.assertEqual(row["persona"]["pain_level_code"], "moderate")
        self.assertEqual(row["persona"]["purchase_barrier_codes"], ["price"])

    def test_customer_without_orders_has_empty_enrichment(self):
        cus = self._customer("KH3", "Le Van C", "0900000003")
        profiles = CustomerProfileService.list_profiles(q="0900000003", limit=10, offset=0)
        row = profiles["results"][0]
        self.assertEqual(row["purchase"]["total_orders"], 0)
        self.assertIsNone(row["latest_order"]["sales_status_code"])
        self.assertIsNone(row["resale"]["last_resale_at"])
        self.assertEqual(row["demographics"]["province"], "")
```

- [ ] **Step 2: Chạy test để xác nhận đỏ**

Run: `cd backend && python -m pytest apps/customers/tests/test_customer_profiles_api.py::CustomerProfileServiceTests::test_latest_order_persona_and_demographics -v`
Expected: FAIL — `KeyError: 'latest_order'`.

- [ ] **Step 3: Thêm `_latest_order_map` và ráp vào `list_profiles`**

Trong `profile_service.py`, thêm import và method, rồi bổ sung 4 nhóm (`demographics`, `channel`, `latest_order`, `persona`) vào dict kết quả.

Thêm import đầu file:

```python
from apps.orders.models import Order, OrderAssessment
```

Thêm method:

```python
    # Các trường assessment đưa nguyên vào nhóm persona.
    _PERSONA_FIELDS = (
        "customer_classification_code", "pain_level_code", "pain_duration_code",
        "lifestyle_habit_codes", "used_massage_before", "relax_time_codes",
        "purchase_barrier_codes", "delax_source_codes", "comparing_brand_text",
        "call_duration_code", "post_purchase_feedback_text", "post_purchase_rating_code",
    )

    @staticmethod
    def _latest_order_map(customer_ids: list[int]) -> dict[int, Order]:
        """1 đơn mới nhất/khách (kể cả đơn hoàn/hủy — phản ánh trạng thái hiện tại),
        kèm channel + assessment. DISTINCT ON (customer_id) ORDER BY created_at DESC."""
        orders = (
            Order.objects.filter(customer_id__in=customer_ids, deleted_at__isnull=True)
            .select_related("channel", "assessment")
            .order_by("customer_id", "-created_at")
            .distinct("customer_id")
        )
        return {o.customer_id: o for o in orders}
```

Trong `list_profiles`, sau khi tính `metrics`, thêm:

```python
        latest_map = CustomerProfileService._latest_order_map(customer_ids)
```

Và trong vòng lặp, thay phần build dict — thêm các nhóm sau (đặt cùng cấp với `identity`/`purchase`):

```python
            order = latest_map.get(customer.id)
            assessment = getattr(order, "assessment", None) if order else None
            persona = {
                field: getattr(assessment, field, None) if assessment else None
                for field in CustomerProfileService._PERSONA_FIELDS
            }
            # ... thêm vào dict results.append({...}) các khóa:
            #   "demographics": {...}, "channel": {...}, "latest_order": {...}, "persona": persona
```

Dict đầy đủ của một phần tử `results` (thay cho dict ở Task 2 Step 3):

```python
            results.append({
                "identity": {"id": customer.id, "code": customer.code,
                             "full_name": customer.full_name,
                             "phone": customer.phone_raw or customer.phone_normalized},
                "demographics": {
                    "gender": customer.gender,
                    "province": order.province if order else "",
                    "address": order.address if order else "",
                    "age_text": getattr(assessment, "age_text", "") if assessment else "",
                    "occupation_code": getattr(assessment, "occupation_code", None) if assessment else None,
                },
                "channel": {
                    "channel_id": order.channel_id if order else None,
                    "channel_name": order.channel.name if order and order.channel_id else "",
                },
                "segment": {"segment_code": segment_code},
                "purchase": {
                    "first_purchase_at": m.get("first_purchase_at"),
                    "last_purchase_at": last_purchase_at,
                    "total_orders": total_orders,
                    "total_spent": str(m.get("total_spent") or Decimal("0")),
                },
                "latest_order": {
                    "sku": order.sku if order else None,
                    "product": order.product if order else "",
                    "last_order_amount": str(order.total_after_discount) if order else None,
                    "last_order_status": order.status if order else None,
                    "sales_status_code": getattr(assessment, "sales_status_code", None) if assessment else None,
                    "not_closed_reason_code": getattr(assessment, "not_closed_reason_code", None) if assessment else None,
                },
                "persona": persona,
                "resale": {"last_resale_at": last_purchase_at if total_orders >= 2 else None},
            })
```

- [ ] **Step 4: Chạy toàn bộ test của file**

Run: `cd backend && python -m pytest apps/customers/tests/test_customer_profiles_api.py -v`
Expected: PASS tất cả.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/customers/profile_service.py backend/apps/customers/tests/test_customer_profiles_api.py
git commit -m "Enrich customer profile with latest order, demographics and persona"
```

---

## Task 4: Serializer + endpoint `GET /api/customers/profiles`

**Files:**
- Modify: `backend/apps/customers/serializers.py`
- Modify: `backend/apps/customers/views.py`
- Modify: `backend/apps/customers/urls.py`
- Modify: `backend/apps/customers/tests/test_customer_profiles_api.py`

- [ ] **Step 1: Viết test API (đỏ trước)**

Thêm class test API vào `test_customer_profiles_api.py`:

```python
from rest_framework.test import APIClient

from apps.users.models import User, UserRole


class CustomerProfilesApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username="admin-prof", password="x", role=UserRole.ADMIN)
        self.channel = Channel.objects.create(code="lp-api", name="Landing API")

    def test_requires_auth(self):
        response = self.client.get("/api/customers/profiles?limit=10&offset=0")
        self.assertEqual(response.status_code, 401)

    def test_returns_results_and_total_with_labels(self):
        cus = Customer.objects.create(code="KH9", full_name="Pham D", phone_raw="0900000009",
                                      phone_normalized="0900000009")
        order = Order.objects.create(channel=self.channel, customer=cus, name="Pham D",
                                     phone="0900000009", status=3, total_after_discount="1500000")
        OrderAssessment.objects.create(order=order, sales_status_code="closed", occupation_code="teacher")

        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/customers/profiles?limit=10&offset=0")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 1)
        row = response.data["results"][0]
        self.assertEqual(row["identity"]["code"], "KH9")
        self.assertEqual(row["latest_order"]["sales_status_code"], "closed")
        self.assertEqual(row["latest_order"]["sales_status_label"], "Đã chốt")
        self.assertEqual(row["demographics"]["occupation_label"], "Giáo viên")
        self.assertEqual(row["demographics"]["gender_label"], None)
```

Lưu ý nhãn: kiểm tra giá trị nhãn thực tế trong `apps/orders/crm_catalog.py` (`SALES_STATUS_OPTIONS`, `OCCUPATION_OPTIONS`) và sửa chuỗi kỳ vọng cho khớp nếu khác ("Đã chốt"/"Giáo viên" là ví dụ — đối chiếu trước khi chạy).

- [ ] **Step 2: Chạy test để xác nhận đỏ**

Run: `cd backend && python -m pytest apps/customers/tests/test_customer_profiles_api.py::CustomerProfilesApiTests -v`
Expected: FAIL — 404 (route chưa có).

- [ ] **Step 3: Thêm `CustomerProfileSerializer`**

Trong `backend/apps/customers/serializers.py`, thêm import label maps và serializer. Dùng `SerializerMethodField` cho các `*_label` (tái dùng map trong `crm_catalog`):

```python
from apps.orders.crm_catalog import (
    OCCUPATION_LABELS, SALES_STATUS_LABELS, NOT_CLOSED_REASON_LABELS,
    CUSTOMER_CLASSIFICATION_LABELS, PAIN_LEVEL_LABELS, PAIN_DURATION_LABELS,
    CALL_DURATION_LABELS, POST_PURCHASE_RATING_LABELS,
)

GENDER_LABELS = {0: "Nữ", 1: "Nam"}


class CustomerProfileSerializer(serializers.Serializer):
    """1 dòng/khách. Nhận dict do CustomerProfileService.list_profiles tạo ra.

    Thêm các *_label phẳng vào trong nhóm con tương ứng để frontend hiển thị trực tiếp.
    """
    def to_representation(self, instance):
        data = dict(instance)  # shallow copy các nhóm con
        demo = dict(data["demographics"])
        demo["gender_label"] = GENDER_LABELS.get(demo.get("gender"))
        demo["occupation_label"] = OCCUPATION_LABELS.get(demo.get("occupation_code"))
        data["demographics"] = demo

        latest = dict(data["latest_order"])
        latest["sales_status_label"] = SALES_STATUS_LABELS.get(latest.get("sales_status_code"))
        latest["not_closed_reason_label"] = NOT_CLOSED_REASON_LABELS.get(latest.get("not_closed_reason_code"))
        data["latest_order"] = latest

        persona = dict(data["persona"])
        persona["customer_classification_label"] = CUSTOMER_CLASSIFICATION_LABELS.get(persona.get("customer_classification_code"))
        persona["pain_level_label"] = PAIN_LEVEL_LABELS.get(persona.get("pain_level_code"))
        persona["pain_duration_label"] = PAIN_DURATION_LABELS.get(persona.get("pain_duration_code"))
        persona["call_duration_label"] = CALL_DURATION_LABELS.get(persona.get("call_duration_code"))
        persona["post_purchase_rating_label"] = POST_PURCHASE_RATING_LABELS.get(persona.get("post_purchase_rating_code"))
        data["persona"] = persona
        return data
```

- [ ] **Step 4: Thêm view + nối resale note/segment_name vào service**

Trong `profile_service.py`, làm giàu `segment.segment_name` + `resale.resale_status/resale_suggestion` từ `LoyalCustomerNote` và danh mục segment.

Sửa import đầu file thành: `from .models import Customer, CustomerSegmentCategory, LoyalCustomerNote`.

Thêm vào `list_profiles` sau `latest_map`:

```python
        notes = {n.customer_id: n for n in LoyalCustomerNote.objects.filter(customer_id__in=customer_ids)}
        segment_names = dict(CustomerSegmentCategory.objects.values_list("code", "name"))
```

Trong vòng lặp, đổi nhóm `segment` và `resale`:

```python
                "segment": {"segment_code": segment_code,
                            "segment_name": segment_names.get(segment_code, "")},
                ...
                "resale": {
                    "last_resale_at": last_purchase_at if total_orders >= 2 else None,
                    "resale_status": notes[customer.id].resale_status if customer.id in notes else "",
                    "resale_suggestion": notes[customer.id].resale_suggestion if customer.id in notes else "",
                },
```

Trong `views.py`, thêm view (đặt sau `update_loyal_customer_note`):

```python
from .profile_service import CustomerProfileService
from .serializers import CustomerProfileSerializer  # thêm vào import sẵn có


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_customer_profiles(request):
    q = request.query_params.get("q", "")
    pagination, error_response = _parse_required_pagination(request)
    if error_response is not None:
        return error_response
    channel = (request.query_params.get("channel") or "").strip() or None
    sales_status = (request.query_params.get("sales_status") or "").strip() or None
    segment = (request.query_params.get("segment") or "").strip() or None
    payload = CustomerProfileService.list_profiles(
        q=q, limit=pagination["limit"], offset=pagination["offset"],
        channel=channel, sales_status=sales_status, segment=segment,
    )
    serializer = CustomerProfileSerializer(payload["results"], many=True)
    return Response({"results": serializer.data, "total": payload["total"]})
```

- [ ] **Step 5: Thêm route**

Trong `backend/apps/customers/urls.py`, import `list_customer_profiles` và thêm route **trước** route `<int:customer_id>` (để không bị nuốt):

```python
    path("profiles", list_customer_profiles, name="customers-profiles"),
```

- [ ] **Step 6: Chạy test API**

Run: `cd backend && python -m pytest apps/customers/tests/test_customer_profiles_api.py -v`
Expected: PASS (sau khi đối chiếu chuỗi nhãn ở Step 1).

- [ ] **Step 7: Commit**

```bash
git add backend/apps/customers/serializers.py backend/apps/customers/views.py backend/apps/customers/urls.py backend/apps/customers/profile_service.py backend/apps/customers/tests/test_customer_profiles_api.py
git commit -m "Add GET /api/customers/profiles endpoint with labels"
```

---

## Task 5: Filters (channel, sales_status, segment) + PATCH resale note

### 5A. Filter channel + sales_status (latest order)

**Files:** `backend/apps/customers/profile_service.py`, `tests/test_customer_profiles_api.py`

- [ ] **Step 1: Test filter (đỏ trước)** — thêm vào `CustomerProfilesApiTests`:

```python
    def test_filter_by_channel_and_sales_status(self):
        other = Channel.objects.create(code="lp-other", name="Other")
        c1 = Customer.objects.create(code="KHA", full_name="A", phone_raw="0911", phone_normalized="0911")
        c2 = Customer.objects.create(code="KHB", full_name="B", phone_raw="0922", phone_normalized="0922")
        o1 = Order.objects.create(channel=self.channel, customer=c1, name="A", phone="0911", status=3, total_after_discount="100")
        OrderAssessment.objects.create(order=o1, sales_status_code="closed")
        o2 = Order.objects.create(channel=other, customer=c2, name="B", phone="0922", status=3, total_after_discount="100")
        OrderAssessment.objects.create(order=o2, sales_status_code="not_buying")

        self.client.force_authenticate(user=self.admin)
        by_channel = self.client.get(f"/api/customers/profiles?limit=10&offset=0&channel={self.channel.id}")
        codes = [r["identity"]["code"] for r in by_channel.data["results"]]
        self.assertEqual(codes, ["KHA"])
        by_status = self.client.get("/api/customers/profiles?limit=10&offset=0&sales_status=not_buying")
        codes = [r["identity"]["code"] for r in by_status.data["results"]]
        self.assertEqual(codes, ["KHB"])
```

- [ ] **Step 2: Chạy → đỏ.** Run: `cd backend && python -m pytest apps/customers/tests/test_customer_profiles_api.py::CustomerProfilesApiTests::test_filter_by_channel_and_sales_status -v` → FAIL (filter chưa áp dụng).

- [ ] **Step 3: Áp filter qua subquery "đơn mới nhất"** — trong `list_profiles`, sau khi tạo `base = CustomerRepository.list_customers_queryset(q)`, thêm:

```python
        from django.db.models import OuterRef, Subquery
        latest_order_qs = (
            Order.objects.filter(customer_id=OuterRef("pk"), deleted_at__isnull=True)
            .order_by("-created_at")
        )
        if channel:
            base = base.annotate(
                _latest_channel=Subquery(latest_order_qs.values("channel_id")[:1])
            ).filter(_latest_channel=channel)
        if sales_status:
            base = base.annotate(
                _latest_sales=Subquery(latest_order_qs.values("assessment__sales_status_code")[:1])
            ).filter(_latest_sales=sales_status)
```

(Đặt khối này trước `total = base.count()`.)

- [ ] **Step 4: Chạy → xanh.** Run: `cd backend && python -m pytest apps/customers/tests/test_customer_profiles_api.py -v` → PASS.

### 5B. Filter segment (đường nặng, chỉ khi có param)

Phân khúc là giá trị tính toán, không lọc được bằng SQL thuần. Khi có `segment`, tính phân khúc cho **toàn bộ customer_id khớp `q`+filter** rồi mới phân trang.

- [ ] **Step 5: Test segment filter (đỏ trước)** — thêm:

```python
    def test_filter_by_segment(self):
        loyal = Customer.objects.create(code="KHL", full_name="Loyal", phone_raw="0933", phone_normalized="0933")
        once = Customer.objects.create(code="KHO", full_name="Once", phone_raw="0944", phone_normalized="0944")
        for amount in ("1000000", "1000000"):
            Order.objects.create(channel=self.channel, customer=loyal, name="Loyal", phone="0933", status=3, total_after_discount=amount)
        Order.objects.create(channel=self.channel, customer=once, name="Once", phone="0944", status=3, total_after_discount="100000")

        self.client.force_authenticate(user=self.admin)
        resp = self.client.get("/api/customers/profiles?limit=10&offset=0&segment=S3")
        codes = [r["identity"]["code"] for r in resp.data["results"]]
        self.assertEqual(codes, ["KHL"])  # 2 đơn GTC → S3 trung thành
```

- [ ] **Step 6: Chạy → đỏ.**

- [ ] **Step 7: Implement segment filter** — trong `list_profiles`, thay phần phân trang bằng nhánh có/không có `segment`:

```python
        if segment:
            all_ids = list(base.values_list("id", flat=True))
            metrics_all = CustomerProfileService._metrics_map(all_ids)
            now = timezone.now()
            matched = []
            for cid in all_ids:
                m = metrics_all.get(cid, {})
                total_orders = m.get("total_orders", 0) or 0
                last = m.get("last_purchase_at")
                days = (now - last).days if last else None
                code = segmentation.classify_segment(
                    delivered_count=total_orders, total_spent=m.get("total_spent") or Decimal("0"),
                    days_since_last_purchase=days, has_problem=False,
                )
                if code == segment:
                    matched.append(cid)
            total = len(matched)
            page_ids = matched[offset : offset + limit]
            page = list(Customer.objects.filter(id__in=page_ids))
            page.sort(key=lambda c: page_ids.index(c.id))
        else:
            total = base.count()
            page = list(base[offset : offset + limit])
        customer_ids = [c.id for c in page]
```

(Xóa 2 dòng `total = base.count()` / `page = ...` cũ ở đầu; phần enrichment phía dưới giữ nguyên dùng `customer_ids`.)

- [ ] **Step 8: Chạy toàn bộ → xanh.** Run: `cd backend && python -m pytest apps/customers/tests/test_customer_profiles_api.py -v` → PASS.

### 5C. PATCH resale note

**Files:** `backend/apps/customers/serializers.py`, `services.py`, `views.py`, `urls.py`, `tests/test_customer_resale_note_api.py`

- [ ] **Step 9: Test PATCH (đỏ trước)** — tạo `backend/apps/customers/tests/test_customer_resale_note_api.py`:

```python
from django.test import TestCase
from rest_framework.test import APIClient

from apps.customers.models import Customer, LoyalCustomerNote
from apps.users.models import User, UserRole


class CustomerResaleNoteApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username="admin-resale", password="x", role=UserRole.ADMIN)
        self.customer = Customer.objects.create(code="KH1", full_name="A", phone_raw="0900", phone_normalized="0900")

    def test_update_resale_fields(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            f"/api/customers/{self.customer.id}/resale-note",
            {"resale_status": "Đã mua lại 3 lần", "resale_suggestion": "BNC02"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["resale_status"], "Đã mua lại 3 lần")
        note = LoyalCustomerNote.objects.get(customer=self.customer)
        self.assertEqual(note.resale_suggestion, "BNC02")
        self.assertEqual(note.updated_by_id, self.admin.id)

    def test_requires_auth(self):
        response = self.client.patch(f"/api/customers/{self.customer.id}/resale-note", {}, format="json")
        self.assertEqual(response.status_code, 401)
```

- [ ] **Step 10: Chạy → đỏ** (404).

- [ ] **Step 11: Serializer** — thêm vào `serializers.py`:

```python
class CustomerResaleNoteUpdateSerializer(serializers.Serializer):
    resale_status = serializers.CharField(required=False, allow_blank=True)
    resale_suggestion = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if "resale_status" not in attrs and "resale_suggestion" not in attrs:
            raise serializers.ValidationError("Cần ít nhất một trường để cập nhật.")
        return attrs
```

- [ ] **Step 12: Service** — mở rộng `upsert_loyal_note` trong `services.py` để nhận thêm 2 trường:

```python
    @staticmethod
    def upsert_loyal_note(customer, *, sale_action=None, special_note=None,
                          resale_status=None, resale_suggestion=None, updated_by=None):
        note, _ = LoyalCustomerNote.objects.get_or_create(customer=customer)
        update_fields = []
        if sale_action is not None:
            note.sale_action = sale_action; update_fields.append("sale_action")
        if special_note is not None:
            note.special_note = special_note; update_fields.append("special_note")
        if resale_status is not None:
            note.resale_status = resale_status; update_fields.append("resale_status")
        if resale_suggestion is not None:
            note.resale_suggestion = resale_suggestion; update_fields.append("resale_suggestion")
        if update_fields:
            note.updated_by = updated_by
            update_fields.extend(["updated_by", "updated_at"])
            note.save(update_fields=list(dict.fromkeys(update_fields)))
        return note
```

- [ ] **Step 13: View + route** — trong `views.py`:

```python
from .serializers import CustomerResaleNoteUpdateSerializer  # thêm vào import


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def update_customer_resale_note(request, customer_id: int):
    customer = CustomerRepository.get_customer_by_id(customer_id)
    if customer is None:
        return Response({"detail": "Không tìm thấy khách hàng."}, status=status.HTTP_404_NOT_FOUND)
    serializer = CustomerResaleNoteUpdateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    note = CustomerService.upsert_loyal_note(
        customer,
        resale_status=serializer.validated_data.get("resale_status"),
        resale_suggestion=serializer.validated_data.get("resale_suggestion"),
        updated_by=request.user if request.user.is_authenticated else None,
    )
    return Response({"resale_status": note.resale_status, "resale_suggestion": note.resale_suggestion},
                    status=status.HTTP_200_OK)
```

Trong `urls.py` thêm (sau route `/orders/aggregate`):

```python
    path("<int:customer_id>/resale-note", update_customer_resale_note, name="customers-resale-note"),
```

(nhớ import `update_customer_resale_note`).

- [ ] **Step 14: Chạy → xanh.** Run: `cd backend && python -m pytest apps/customers/tests/test_customer_resale_note_api.py -v` → PASS.

- [ ] **Step 15: Chạy toàn bộ test app customers + lint**

Run: `cd backend && python -m pytest apps/customers/ -v && ruff check apps/customers/`
Expected: PASS (trừ test fail có sẵn trên master — xem `preexisting-failing-aggregate-test`, không liên quan). Ruff sạch.

- [ ] **Step 16: Commit**

```bash
git add backend/apps/customers/
git commit -m "Add profile filters and resale note update endpoint"
```

---

## Task 6: Frontend API client

**Files:**
- Modify: `frontend/src/lib/api/customers.ts`
- Create: `frontend/src/lib/api/__tests__/customers.profiles.test.ts`

- [ ] **Step 1: Test API client (đỏ trước)** — tạo `customers.profiles.test.ts`:

```typescript
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@/services/http", () => ({ fetchJson: vi.fn() }));
import { fetchJson } from "@/services/http";
import { getCustomerProfiles, updateCustomerResaleNote } from "@/lib/api/customers";

describe("customer profiles api", () => {
  beforeEach(() => vi.mocked(fetchJson).mockReset());

  it("builds query with q, segment, channel, sales_status", async () => {
    vi.mocked(fetchJson).mockResolvedValue({ results: [], total: 0 });
    await getCustomerProfiles({ query: " an ", limit: 50, offset: 0, segment: "S3", channel: "5", salesStatus: "closed" });
    const url = vi.mocked(fetchJson).mock.calls[0][0] as string;
    expect(url).toContain("/api/customers/profiles?");
    expect(url).toContain("q=an");
    expect(url).toContain("segment=S3");
    expect(url).toContain("channel=5");
    expect(url).toContain("sales_status=closed");
  });

  it("patches resale note", async () => {
    vi.mocked(fetchJson).mockResolvedValue({ resale_status: "x", resale_suggestion: "y" });
    await updateCustomerResaleNote(7, { resale_status: "x" });
    expect(fetchJson).toHaveBeenCalledWith("/api/customers/7/resale-note", expect.objectContaining({ method: "PATCH" }));
  });
});
```

- [ ] **Step 2: Chạy → đỏ.** Run: `cd frontend && npx vitest run src/lib/api/__tests__/customers.profiles.test.ts` → FAIL (export chưa có).

- [ ] **Step 3: Thêm types + functions vào `customers.ts`**

```typescript
export interface CustomerProfile {
  identity: { id: number; code: string; full_name: string; phone: string };
  demographics: {
    gender: number | null; gender_label: string | null;
    province: string; address: string;
    age_text: string; occupation_code: string | null; occupation_label: string | null;
  };
  channel: { channel_id: number | null; channel_name: string };
  segment: { segment_code: string; segment_name: string };
  purchase: { first_purchase_at: string | null; last_purchase_at: string | null; total_orders: number; total_spent: string };
  latest_order: {
    sku: string | null; product: string; last_order_amount: string | null; last_order_status: number | null;
    sales_status_code: string | null; sales_status_label: string | null;
    not_closed_reason_code: string | null; not_closed_reason_label: string | null;
  };
  persona: {
    customer_classification_code: string | null; customer_classification_label: string | null;
    pain_level_code: string | null; pain_level_label: string | null;
    pain_duration_code: string | null; pain_duration_label: string | null;
    lifestyle_habit_codes: string[]; used_massage_before: boolean | null;
    relax_time_codes: string[]; purchase_barrier_codes: string[]; delax_source_codes: string[];
    comparing_brand_text: string; call_duration_code: string | null; call_duration_label: string | null;
    post_purchase_feedback_text: string; post_purchase_rating_code: string | null; post_purchase_rating_label: string | null;
  };
  resale: { last_resale_at: string | null; resale_status: string; resale_suggestion: string };
}

export interface CustomerProfileListResult { results: CustomerProfile[]; total: number; }

export interface CustomerProfileListParams {
  query?: string; limit: number; offset: number;
  segment?: string; channel?: string; salesStatus?: string;
}

export async function getCustomerProfiles(params: CustomerProfileListParams): Promise<CustomerProfileListResult> {
  const safeLimit = Math.max(1, Math.min(CUSTOMERS_MAX_LIMIT, Math.trunc(Number(params.limit) || CUSTOMERS_MAX_LIMIT)));
  const safeOffset = Math.max(0, Math.trunc(Number(params.offset) || 0));
  const query = new URLSearchParams({ limit: String(safeLimit), offset: String(safeOffset) });
  const q = params.query?.trim() || "";
  if (q) query.set("q", q);
  if (params.segment) query.set("segment", params.segment);
  if (params.channel) query.set("channel", params.channel);
  if (params.salesStatus) query.set("sales_status", params.salesStatus);
  return fetchJson(`${API_BASE}/profiles?${query.toString()}`);
}

export interface CustomerResaleNotePayload { resale_status?: string; resale_suggestion?: string; }

export async function updateCustomerResaleNote(
  customerId: number, payload: CustomerResaleNotePayload,
): Promise<{ resale_status: string; resale_suggestion: string }> {
  return fetchJson(`${API_BASE}/${customerId}/resale-note`, { method: "PATCH", body: JSON.stringify(payload) });
}
```

- [ ] **Step 4: Chạy → xanh.** Run: `cd frontend && npx vitest run src/lib/api/__tests__/customers.profiles.test.ts` → PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api/customers.ts frontend/src/lib/api/__tests__/customers.profiles.test.ts
git commit -m "Add customer profiles API client"
```

---

## Task 7: Frontend — bảng tổng hợp (CustomersPage)

**Files:**
- Create: `frontend/src/pages/customers/profile-columns.ts`
- Rewrite: `frontend/src/pages/CustomersPage.tsx`

> Đây là task lớn (UI ~900 dòng). Bám pattern sẵn có: inline-edit từ `LoyalCustomersPage.tsx` (state `editing`+`editForm`, gọi PATCH, cập nhật `rows` cục bộ), và modal lịch sử đơn hiện có trong `CustomersPage.tsx` (đổi thành expandable row). Giữ nguyên các chức năng tạo/sửa/xóa/bulk-delete khách hàng đang có.

- [ ] **Step 1: Tạo cấu hình nhóm cột**

Tạo `frontend/src/pages/customers/profile-columns.ts`:

```typescript
export type ColumnGroupKey =
  | "identity" | "sale_status" | "demographics" | "segment"
  | "purchase" | "products" | "persona" | "resale";

export interface ColumnGroup { key: ColumnGroupKey; label: string; defaultVisible: boolean; }

// 8 nhóm theo database.xlsx. Mặc định hiện nhóm cốt lõi, còn lại bật qua menu.
export const COLUMN_GROUPS: ColumnGroup[] = [
  { key: "identity", label: "📋 Định danh", defaultVisible: true },
  { key: "sale_status", label: "Trạng thái xử lý", defaultVisible: true },
  { key: "segment", label: "🏷️ Phân khúc & nhu cầu", defaultVisible: true },
  { key: "purchase", label: "🛒 Hành vi mua", defaultVisible: true },
  { key: "demographics", label: "👤 Nhân khẩu học", defaultVisible: false },
  { key: "products", label: "📦 Sản phẩm", defaultVisible: false },
  { key: "persona", label: "💬 Chân dung", defaultVisible: false },
  { key: "resale", label: "🔄 Resale", defaultVisible: false },
];
```

- [ ] **Step 2: Đổi data layer của CustomersPage sang `getCustomerProfiles`**

Trong `CustomersPage.tsx`: thay state list từ `Customer[]` sang `CustomerProfile[]`, đổi `getCustomers(...)` (dòng ~106) → `getCustomerProfiles({ query, limit, offset, segment, channel, salesStatus })`, lưu `total` từ kết quả. Giữ debounce search 300ms và phân trang hiện có. Form tạo/sửa khách vẫn dùng `createCustomer/updateCustomer` (shape `Customer` cũ) — các API đó không đổi.

- [ ] **Step 3: Render bảng nhóm cột + header 2 tầng + toggle**

- State `visibleGroups: Record<ColumnGroupKey, boolean>` khởi tạo từ `COLUMN_GROUPS[].defaultVisible`.
- `DropdownMenu` (shadcn) với `DropdownMenuCheckboxItem` cho từng nhóm để bật/tắt.
- `Table` với `<thead>` 2 hàng: hàng 1 = nhóm đang hiện (dùng `colSpan` = số cột con của nhóm), hàng 2 = tên cột. Sticky 2 cột đầu (Mã KH, Họ tên) bằng `className="sticky left-0 bg-background z-10"`.
- Mỗi nhóm map sang các cột (đối chiếu spec mục 2). Trường code → hiển thị `*_label` (fallback code); mảng (`purchase_barrier_codes`, `lifestyle_habit_codes`, `delax_source_codes`, `relax_time_codes`) → render chip bằng `Badge`. `gender` → `gender_label`. `last_order_status` → map sang nhãn trạng thái đơn (tái dùng map nhãn order nếu có; nếu không, map tối thiểu {3:"GTC",4:"Hoàn",5:"Hoàn",6:"Hủy"}). `segment_code` → `Badge` màu.

- [ ] **Step 4: Filter bar**

Thêm hàng filter trên bảng: ô search (đang có) + 3 `Select`:
- Phân khúc: nạp từ `getSegmentCategories()` (đã có), set `segment`.
- Kênh bán: nạp danh sách channel (dùng API channel sẵn có; nếu chưa có client, tạm để các giá trị xuất hiện trong trang hoặc bỏ qua nếu không có nguồn — xác nhận khi thực thi), set `channel`.
- Trạng thái sale: dùng options `SALES_STATUS` (lấy từ catalog CRM frontend nếu có; nếu không, gọi `/api/orders/crm-catalog` đang dùng ở order form), set `salesStatus`.
- Đổi filter → reset offset về 0, refetch.

- [ ] **Step 5: Expandable row (lịch sử đơn)**

- State `expandedId: number | null`. Click dòng (hoặc nút chevron) → toggle.
- Khi mở lần đầu: gọi `getCustomerOrders(customerId)` (đã có), cache theo id. Render bảng đơn con với các cột CRM/đơn (tái dùng cách modal hiện tại đang render).
- Hàng mở rộng là 1 `<tr>` với `<td colSpan={tổng số cột}>` chứa bảng con.

- [ ] **Step 6: Inline edit 2 trường resale**

- 2 cột `resale_status`, `resale_suggestion` (nhóm Resale): hiển thị text, click → `Input`/`Textarea`, lưu khi blur/Enter.
- Handler gọi `updateCustomerResaleNote(customerId, { resale_status })` (hoặc `resale_suggestion`), cập nhật `rows` cục bộ như `LoyalCustomersPage` làm với note. Toast lỗi nếu thất bại.

- [ ] **Step 7: Typecheck + build + test**

Run: `cd frontend && npx tsc --noEmit && npx vitest run && npm run build`
Expected: tsc sạch, test PASS, build thành công.

- [ ] **Step 8: Verify thủ công**

Chạy app (theo skill `run` / docker-compose.local.yml), mở trang Khách hàng:
- Bảng hiện nhóm cốt lõi; bật/tắt nhóm hoạt động; sticky cột đầu khi cuộn ngang.
- Filter phân khúc/kênh/trạng thái sale lọc đúng + phân trang.
- Click dòng xổ lịch sử đơn.
- Sửa inline 2 trường resale → reload vẫn giữ.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/pages/CustomersPage.tsx frontend/src/pages/customers/profile-columns.ts
git commit -m "Rebuild customers page as aggregated profile table"
```

---

## Task 8: Hoàn tất

- [ ] **Step 1: Chạy full test backend + frontend**

Run: `cd backend && python -m pytest apps/customers/ -v` và `cd frontend && npx vitest run`
Expected: PASS (trừ test fail có sẵn trên master).

- [ ] **Step 2: Push + tạo MR**

```bash
git push -u origin loyal-customers-20260520
```
Tạo MR trên GitLab, gửi link cho người dùng (theo CLAUDE.md).

---

## Self-Review Notes (đã kiểm)

- **Spec coverage**: 38 cột → Task 2 (metrics/segment/resale date), Task 3 (latest order + demographics + persona), Task 4 (labels + endpoint). Resale nhập tay → Task 1 (cột) + 5C (PATCH) + 7 (inline edit). Filter → Task 5. Frontend nhóm cột/expandable/inline → Task 6–7.
- **Endpoint riêng `/profiles`**: giữ `/api/customers` cũ cho order autocomplete (đã xác minh `use-orders-data.ts`).
- **Tên nhất quán**: service `CustomerProfileService.list_profiles`; serializer `CustomerProfileSerializer`; FE `getCustomerProfiles` / `updateCustomerResaleNote`; route `profiles` + `<id>/resale-note`.
- **Quyết định mở khi thực thi**: chuỗi nhãn kỳ vọng trong test (Task 4 Step 1) cần đối chiếu `crm_catalog.py`; nguồn danh sách channel/sales-status cho filter bar (Task 7 Step 4) — dùng catalog/endpoint sẵn có, xác nhận khi thực thi.
