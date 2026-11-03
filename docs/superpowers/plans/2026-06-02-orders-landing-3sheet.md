# Orders Landing 3-Sheet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tổ chức lại màn đơn hàng Landing thành 3 sheet (Sheet 1: Landing/Hotline/Zalo đủ TT · Sheet 2: Messenger đủ TT · Sheet 3: mọi nguồn chưa đủ TT — follow-up card), giữ các tab Giao thành công / Đã hủy / Blocklist.

**Architecture:** Phân sheet ở backend qua filter `info_complete` + nguồn (tái dùng `_apply_list_filters`). Số lần gọi / lần gọi gần nhất / nhiệt độ suy ra **real-time** từ `OrderJourneyEvent(event_type="sale_contact")` bằng annotate (phương án A — không lưu cứng). Lý do chưa chốt = append vào `NOT_CLOSED_REASON_OPTIONS`. Auto-close 14 ngày bằng management command (chạy qua cron). Frontend tái dùng `OrdersTable` cho Sheet 1/2, thêm component card mới cho Sheet 3.

**Tech Stack:** Django 5 + DRF, PostgreSQL, React + TypeScript (Vite), hook `useOrdersData`.

**Quy ước test (theo memory project):** pytest hỏng (BOM trong pytest.ini) → chạy bằng `python manage.py test <path>` với Postgres/Redis tạm. Lệnh mẫu mỗi task ghi rõ bên dưới.

**Hằng số quyết định:**
- SLA "đơn mới chưa gọi quá hạn" = **30 phút** (`SHEET_SLA_MINUTES = 30`).
- Auto-close = **14 ngày** không có `sale_contact` mới (`FOLLOWUP_AUTOCLOSE_DAYS = 14`).
- Cảnh báo Sheet 3 = **7 ngày** (`FOLLOWUP_STALE_DAYS = 7`).
- "Đủ thông tin" = `name` non-empty AND `phone` non-empty AND (`raw_address` OR `address`) non-empty sau trim.

---

## Phase A — Backend: catalog, model, filter

### Task A1: Bổ sung lý do chưa chốt + sales_status "archived"

**Files:**
- Modify: `backend/apps/orders/crm_catalog.py:44-54` (NOT_CLOSED_REASON_OPTIONS), `:23-42` (SALES_STATUS_OPTIONS)
- Test: `backend/apps/orders/tests/test_crm_catalog_sheet3.py` (Create)

- [ ] **Step 1: Viết test fail**

```python
# backend/apps/orders/tests/test_crm_catalog_sheet3.py
from django.test import SimpleTestCase

from apps.orders.crm_catalog import (
    NOT_CLOSED_REASON_LABELS,
    SALES_STATUS_LABELS,
    build_crm_catalog_payload,
)


class Sheet3CatalogTests(SimpleTestCase):
    def test_new_not_closed_reasons_present(self):
        for code in (
            "watching_demo",
            "waiting_for_money",
            "spouse_not_approved",
            "no_need_anymore",
            "other",
        ):
            self.assertIn(code, NOT_CLOSED_REASON_LABELS)

    def test_legacy_not_closed_reasons_kept(self):
        self.assertIn("too_expensive", NOT_CLOSED_REASON_LABELS)
        self.assertIn("competitor_cheaper", NOT_CLOSED_REASON_LABELS)

    def test_archived_sales_status_present(self):
        self.assertIn("archived", SALES_STATUS_LABELS)
        payload = build_crm_catalog_payload()
        codes = {opt["code"] for opt in payload["sales_status_options"]}
        self.assertIn("archived", codes)
```

- [ ] **Step 2: Chạy test xác nhận fail**

Run: `cd backend && python manage.py test apps.orders.tests.test_crm_catalog_sheet3 -v 2`
Expected: FAIL — `archived`/`watching_demo` not found.

- [ ] **Step 3: Thêm giá trị mới vào catalog**

Trong `crm_catalog.py`, append vào `NOT_CLOSED_REASON_OPTIONS` (sau dòng `competitor_closed_first`):

```python
    CatalogOption("watching_demo", "Đang xem demo"),
    CatalogOption("waiting_for_money", "Chờ tiền"),
    CatalogOption("spouse_not_approved", "Vợ/chồng chưa duyệt"),
    CatalogOption("no_need_anymore", "Không có nhu cầu nữa"),
    CatalogOption("other", "Khác"),
```

Append vào `SALES_STATUS_OPTIONS` (sau `warranty`):

```python
    CatalogOption("archived", "Đóng case (win-back)"),
```

- [ ] **Step 4: Chạy test xác nhận pass**

Run: `cd backend && python manage.py test apps.orders.tests.test_crm_catalog_sheet3 -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/crm_catalog.py backend/apps/orders/tests/test_crm_catalog_sheet3.py
git commit -m "Add Sheet 3 not-closed reasons and archived sales status"
```

---

### Task A2: Hằng số + helper phân loại "đủ thông tin"

**Files:**
- Create: `backend/apps/orders/sheet_rules.py`
- Test: `backend/apps/orders/tests/test_sheet_rules.py`

- [ ] **Step 1: Viết test fail**

```python
# backend/apps/orders/tests/test_sheet_rules.py
from django.test import SimpleTestCase

from apps.orders.sheet_rules import (
    FOLLOWUP_AUTOCLOSE_DAYS,
    FOLLOWUP_STALE_DAYS,
    SHEET_SLA_MINUTES,
    is_info_complete,
)


class InfoCompleteTests(SimpleTestCase):
    def test_complete_when_name_phone_address(self):
        self.assertTrue(is_info_complete(name="A", phone="09", raw_address="Hà Nội", address=""))

    def test_uses_address_fallback(self):
        self.assertTrue(is_info_complete(name="A", phone="09", raw_address="", address="HN"))

    def test_incomplete_when_address_blank(self):
        self.assertFalse(is_info_complete(name="A", phone="09", raw_address="  ", address=""))

    def test_incomplete_when_phone_blank(self):
        self.assertFalse(is_info_complete(name="A", phone="", raw_address="HN", address=""))

    def test_constants(self):
        self.assertEqual(SHEET_SLA_MINUTES, 30)
        self.assertEqual(FOLLOWUP_AUTOCLOSE_DAYS, 14)
        self.assertEqual(FOLLOWUP_STALE_DAYS, 7)
```

- [ ] **Step 2: Chạy test xác nhận fail**

Run: `cd backend && python manage.py test apps.orders.tests.test_sheet_rules -v 2`
Expected: FAIL — module không tồn tại.

- [ ] **Step 3: Tạo module**

```python
# backend/apps/orders/sheet_rules.py
"""Quy tắc phân Sheet 1/2/3 và follow-up cho trang Landing."""
from __future__ import annotations

SHEET_SLA_MINUTES = 30          # đơn mới chưa gọi quá hạn → highlight đỏ
FOLLOWUP_STALE_DAYS = 7         # cảnh báo lâu không liên hệ
FOLLOWUP_AUTOCLOSE_DAYS = 14    # tự đóng case sau ngần này ngày không liên hệ

SALE_CONTACT_EVENT = "sale_contact"   # event_type log mỗi lần "Ghi liên hệ"
CASE_CLOSED_EVENT = "case_closed"     # event_type khi đóng case

SHEET1_SOURCES = ("landing", "hotline", "zalo")
SHEET2_SOURCES = ("messenger",)
SHEET3_SOURCES = ("landing", "hotline", "zalo", "messenger")

# Trạng thái sale "tích cực" / "dead" để suy nhiệt độ.
POSITIVE_SALES_STATUSES = frozenset(
    {"scheduled", "call_back_later", "need_more_consulting", "closed_scheduled_date"}
)
DEAD_SALES_STATUSES = frozenset(
    {"unreachable", "wrong_number", "spam", "duplicate", "not_buying", "no_answer_3"}
)
# Trạng thái coi như đã rời khỏi follow-up (không hiện ở Sheet 3).
CLOSED_SALES_STATUSES = frozenset(
    {"closed", "closed_express", "closed_scheduled_date", "created_order", "archived"}
)


def is_info_complete(*, name: str, phone: str, raw_address: str = "", address: str = "") -> bool:
    """Đủ thông tin = có Tên + SĐT + Địa chỉ (text bất kỳ, raw_address hoặc address)."""
    has_name = bool((name or "").strip())
    has_phone = bool((phone or "").strip())
    has_address = bool((raw_address or "").strip()) or bool((address or "").strip())
    return has_name and has_phone and has_address
```

- [ ] **Step 4: Chạy test xác nhận pass**

Run: `cd backend && python manage.py test apps.orders.tests.test_sheet_rules -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/sheet_rules.py backend/apps/orders/tests/test_sheet_rules.py
git commit -m "Add sheet routing rules and info-complete helper"
```

---

### Task A3: Filter `info_complete` trong repository

**Files:**
- Modify: `backend/apps/orders/repositories.py` (trong `_apply_list_filters`, sau khối `order_source_exclude` ~dòng 311)
- Test: `backend/apps/orders/tests/test_sheet_info_complete_filter.py`

- [ ] **Step 1: Viết test fail**

```python
# backend/apps/orders/tests/test_sheet_info_complete_filter.py
from django.test import TestCase

from apps.orders.repositories import OrderRepository
from apps.orders.tests.factories import make_order  # dùng helper sẵn có; nếu chưa có, tạo order trực tiếp


class InfoCompleteFilterTests(TestCase):
    def setUp(self):
        # order đủ TT (có địa chỉ)
        self.full = make_order(name="A", phone="0900000001", raw_address="Hà Nội", order_source="landing")
        # order thiếu địa chỉ
        self.partial = make_order(name="B", phone="0900000002", raw_address="", address="", order_source="landing")

    def test_info_complete_true_returns_only_full(self):
        qs = OrderRepository._apply_list_filters(
            OrderRepository._base_orders_queryset(include_cancelled=False),
            {"info_complete": "true"},
        )
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(self.full.id, ids)
        self.assertNotIn(self.partial.id, ids)

    def test_info_complete_false_returns_only_partial(self):
        qs = OrderRepository._apply_list_filters(
            OrderRepository._base_orders_queryset(include_cancelled=False),
            {"info_complete": "false"},
        )
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(self.partial.id, ids)
        self.assertNotIn(self.full.id, ids)
```

> **Lưu ý cho người thực hiện:** kiểm tra `apps/orders/tests/` xem đã có factory/helper tạo Order chưa (Order yêu cầu `channel` + `customer` non-null). Nếu chưa có `make_order`, tạo helper nhỏ trong file test tạo `Channel`, `Customer`, `Order` tối thiểu, hoặc tái dùng pattern từ một test order hiện có (vd `test_order_list*`).

- [ ] **Step 2: Chạy test xác nhận fail**

Run: `cd backend && python manage.py test apps.orders.tests.test_sheet_info_complete_filter -v 2`
Expected: FAIL — filter chưa lọc.

- [ ] **Step 3: Thêm filter trong `_apply_list_filters`**

Sau khối xử lý `order_source_exclude` (ngay trước `show_orders = filters.get("show_orders")`), thêm:

```python
        # info_complete: "true" = có Tên+SĐT+Địa chỉ; "false" = thiếu địa chỉ (Sheet 3).
        from django.db.models import Q as _Q
        info_complete = str(filters.get("info_complete") or "").strip().lower()
        if info_complete in ("true", "false"):
            address_blank = (
                (_Q(raw_address__isnull=True) | _Q(raw_address="")) &
                (_Q(address__isnull=True) | _Q(address=""))
            )
            name_blank = _Q(name__isnull=True) | _Q(name="")
            phone_blank = _Q(phone__isnull=True) | _Q(phone="")
            incomplete = address_blank | name_blank | phone_blank
            if info_complete == "true":
                queryset = queryset.exclude(incomplete)
            else:
                queryset = queryset.filter(incomplete)
```

> Dùng import cục bộ `_Q` để tránh đụng import phía trên file nếu `Q` chưa import trong scope; nếu `Q` đã import ở đầu `repositories.py` thì bỏ dòng import và dùng `Q` trực tiếp.

- [ ] **Step 4: Chạy test xác nhận pass**

Run: `cd backend && python manage.py test apps.orders.tests.test_sheet_info_complete_filter -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/repositories.py backend/apps/orders/tests/test_sheet_info_complete_filter.py
git commit -m "Add info_complete filter for sheet routing"
```

---

### Task A4: Index composite cho đếm sale_contact

**Files:**
- Modify: `backend/apps/orders/models.py:360-365` (Meta.indexes của OrderJourneyEvent)
- Migration: `backend/apps/orders/migrations/XXXX_journey_order_type_created_idx.py` (tạo bằng makemigrations)

- [ ] **Step 1: Thêm index**

Trong `OrderJourneyEvent.Meta.indexes`, thêm:

```python
            models.Index(
                fields=["order", "event_type", "created_at"],
                name="ord_journey_ord_type_cr_idx",
            ),
```

- [ ] **Step 2: Tạo migration**

Run: `cd backend && python manage.py makemigrations orders`
Expected: tạo file migration thêm index `ord_journey_ord_type_cr_idx`.

- [ ] **Step 3: Áp migration (DB tạm)**

Run: `cd backend && python manage.py migrate orders`
Expected: OK, no errors.

- [ ] **Step 4: Commit**

```bash
git add backend/apps/orders/models.py backend/apps/orders/migrations/
git commit -m "Add composite index for sale_contact counting"
```

---

## Phase B — Backend: nhiệt độ, API Sheet 3, ghi liên hệ, đóng case

### Task B1: Annotate contact_count / last_contact_at + suy nhiệt độ

**Files:**
- Create: `backend/apps/orders/followup.py` (hàm thuần tính nhiệt độ + annotate helper)
- Test: `backend/apps/orders/tests/test_followup_temperature.py`

- [ ] **Step 1: Viết test fail (hàm thuần)**

```python
# backend/apps/orders/tests/test_followup_temperature.py
from datetime import timedelta

from django.test import SimpleTestCase

from apps.orders.followup import compute_temperature


class TemperatureTests(SimpleTestCase):
    def test_hot(self):
        # ≤2 lần, gần đây ≤3 ngày, trạng thái tích cực
        self.assertEqual(
            compute_temperature(contact_count=1, days_since=1, sales_status="scheduled"),
            "hot",
        )

    def test_cold_by_count(self):
        self.assertEqual(
            compute_temperature(contact_count=6, days_since=1, sales_status="scheduled"),
            "cold",
        )

    def test_cold_by_days(self):
        self.assertEqual(
            compute_temperature(contact_count=1, days_since=8, sales_status="scheduled"),
            "cold",
        )

    def test_warm_when_not_positive(self):
        self.assertEqual(
            compute_temperature(contact_count=1, days_since=1, sales_status=None),
            "warm",
        )

    def test_warm_mid_range(self):
        self.assertEqual(
            compute_temperature(contact_count=4, days_since=2, sales_status="need_more_consulting"),
            "warm",
        )
```

- [ ] **Step 2: Chạy test xác nhận fail**

Run: `cd backend && python manage.py test apps.orders.tests.test_followup_temperature -v 2`
Expected: FAIL — module không tồn tại.

- [ ] **Step 3: Tạo `followup.py`**

```python
# backend/apps/orders/followup.py
"""Suy nhiệt độ follow-up (real-time) từ số lần gọi + thời gian + trạng thái sale."""
from __future__ import annotations

from django.db.models import Count, Max, Q, QuerySet

from apps.orders.sheet_rules import (
    FOLLOWUP_STALE_DAYS,
    POSITIVE_SALES_STATUSES,
    SALE_CONTACT_EVENT,
)

HOT = "hot"
WARM = "warm"
COLD = "cold"


def compute_temperature(*, contact_count: int, days_since: int, sales_status: str | None) -> str:
    """Quy tắc:
    - Lạnh nếu gọi > 5 lần HOẶC > 7 ngày không liên hệ.
    - Nóng nếu gọi ≤ 2 VÀ ≤ 3 ngày VÀ trạng thái tích cực.
    - Còn lại Ấm.
    Thứ tự kiểm tra: Lạnh trước, rồi Nóng, còn lại Ấm.
    """
    if contact_count > 5 or days_since > FOLLOWUP_STALE_DAYS:
        return COLD
    if contact_count <= 2 and days_since <= 3 and (sales_status in POSITIVE_SALES_STATUSES):
        return HOT
    return WARM


def annotate_contact_aggregates(queryset: QuerySet) -> QuerySet:
    """Annotate `contact_count` và `last_contact_at` từ OrderJourneyEvent(sale_contact)."""
    contact_q = Q(journey_events__event_type=SALE_CONTACT_EVENT)
    return queryset.annotate(
        contact_count=Count("journey_events", filter=contact_q, distinct=True),
        last_contact_at=Max("journey_events__created_at", filter=contact_q),
    )
```

- [ ] **Step 4: Chạy test xác nhận pass**

Run: `cd backend && python manage.py test apps.orders.tests.test_followup_temperature -v 2`
Expected: PASS.

- [ ] **Step 5: Test annotate trên DB**

Thêm vào cùng file test (class mới `AnnotateTests(TestCase)`): tạo 1 order, tạo 3 `OrderJourneyEvent(event_type="sale_contact")` + 1 event khác loại, gọi `annotate_contact_aggregates(Order.objects.filter(id=order.id))` và assert `contact_count == 3` và `last_contact_at is not None`.

```python
from django.test import TestCase
from django.utils import timezone
from apps.orders.models import Order, OrderJourneyEvent
from apps.orders.followup import annotate_contact_aggregates
from apps.orders.sheet_rules import SALE_CONTACT_EVENT
# ... tạo order qua helper make_order như Task A3 ...

class AnnotateTests(TestCase):
    def test_counts_only_sale_contact(self):
        order = make_order(name="A", phone="09", order_source="landing")
        for _ in range(3):
            OrderJourneyEvent.objects.create(order=order, event_type=SALE_CONTACT_EVENT)
        OrderJourneyEvent.objects.create(order=order, event_type="sales_status_changed")
        row = annotate_contact_aggregates(Order.objects.filter(id=order.id)).get()
        self.assertEqual(row.contact_count, 3)
        self.assertIsNotNone(row.last_contact_at)
```

Run: `cd backend && python manage.py test apps.orders.tests.test_followup_temperature -v 2`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/followup.py backend/apps/orders/tests/test_followup_temperature.py
git commit -m "Add follow-up temperature computation and contact aggregates"
```

---

### Task B2: Endpoint list Sheet 3 (card data)

**Files:**
- Create: `backend/apps/orders/transport/views/order_views_impl/sheet3_view.py`
- Modify: `backend/apps/orders/urls.py` (đăng ký route `orders/sheet3`)
- Modify: `backend/apps/orders/transport/views/order_views_impl/__init__.py` (export view)
- Test: `backend/apps/orders/tests/test_sheet3_list_api.py`

- [ ] **Step 1: Viết test fail**

```python
# backend/apps/orders/tests/test_sheet3_list_api.py
from django.test import TestCase
from rest_framework.test import APIClient
# ... import make_user/make_order helpers ...

class Sheet3ListApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = make_authenticated_user(self.client)  # helper: tạo user + force_authenticate

    def test_returns_only_incomplete_non_koc_with_temperature(self):
        partial = make_order(name="A", phone="09", raw_address="", order_source="landing", staff=self.user)
        full = make_order(name="B", phone="09", raw_address="HN", order_source="landing", staff=self.user)
        koc = make_order(name="C", phone="09", raw_address="", order_source="koc", staff=self.user)
        resp = self.client.post("/api/orders/sheet3", {"limit": 50, "offset": 0}, format="json")
        self.assertEqual(resp.status_code, 200)
        ids = {r["id"] for r in resp.data["results"]}
        self.assertIn(partial.id, ids)
        self.assertNotIn(full.id, ids)
        self.assertNotIn(koc.id, ids)
        row = next(r for r in resp.data["results"] if r["id"] == partial.id)
        self.assertIn(row["temperature"], ("hot", "warm", "cold"))
        self.assertIn("contact_count", row)
        self.assertIn("last_contact_at", row)
```

> Kiểm tra prefix URL thực tế (`/api/orders/...` hay `/orders/...`) trong `config/urls.py` / `apps/orders/urls.py` và chỉnh path test cho khớp.

- [ ] **Step 2: Chạy test xác nhận fail**

Run: `cd backend && python manage.py test apps.orders.tests.test_sheet3_list_api -v 2`
Expected: FAIL — route 404.

- [ ] **Step 3: Viết view**

```python
# backend/apps/orders/transport/views/order_views_impl/sheet3_view.py
import logging

from django.utils import timezone
from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orders.followup import annotate_contact_aggregates, compute_temperature
from apps.orders.repositories import OrderRepository
from apps.orders.serializers import OrderSerializer
from apps.orders.sheet_rules import CLOSED_SALES_STATUSES, SHEET3_SOURCES

from ._view_helpers import (
    _apply_user_channel_scope,
    _build_order_list_context,
    _empty_orders_response,
    _parse_required_pagination,
)

logger = logging.getLogger(__name__)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@never_cache
def list_sheet3_orders(request):
    """Sheet 3 — follow-up: mọi nguồn (trừ koc) CHƯA đủ thông tin, chưa đóng case."""
    pagination, error_response = _parse_required_pagination(request)
    if error_response is not None:
        return error_response

    source = request.data
    filters = {
        "q": source.get("q", ""),
        "staff": _parse_multi_query_values_safe(request, "staff"),
        "order_source_present": "true",
        "order_source_exclude": ["koc"],
        "info_complete": "false",
        "date_from": source.get("date_from", ""),
        "date_to": source.get("date_to", ""),
        "limit": pagination["limit"],
        "offset": pagination["offset"],
    }
    filters, scope_empty = _apply_user_channel_scope(filters, request.user)
    if scope_empty:
        return _empty_orders_response({"temperature_counts": {}})

    try:
        base = OrderRepository._apply_list_filters(
            OrderRepository._base_orders_queryset(include_cancelled=False), filters
        )
        # Loại đơn đã đóng/đã chốt khỏi follow-up.
        base = base.exclude(assessment__sales_status_code__in=CLOSED_SALES_STATUSES)
        base = annotate_contact_aggregates(base)
        total_count = base.count()
        queryset = OrderRepository._wrap_with_related_and_annotations(
            base, with_journey_events=True
        )
        offset, limit = filters["offset"], filters["limit"]
        page = list(queryset.order_by("last_contact_at", "created_at")[offset : offset + limit])
        context = _build_order_list_context(request.user, page)
        serializer = OrderSerializer(page, many=True, context=context)
        results = serializer.data
        now = timezone.now()
        temperature_counts = {"hot": 0, "warm": 0, "cold": 0}
        for obj, row in zip(page, results):
            cc = getattr(obj, "contact_count", 0) or 0
            last = getattr(obj, "last_contact_at", None) or obj.created_at
            days = (now - last).days
            ss = getattr(getattr(obj, "assessment", None), "sales_status_code", None)
            temp = compute_temperature(contact_count=cc, days_since=days, sales_status=ss)
            row["contact_count"] = cc
            row["last_contact_at"] = last.isoformat() if last else None
            row["temperature"] = temp
            temperature_counts[temp] += 1
        data = {"results": results, "count": total_count, "temperature_counts": temperature_counts}
    except Exception:
        logger.exception("Failed to query/serialize sheet3 orders, filters=%s", filters)
        return Response(
            {"detail": "Không thể tải danh sách follow-up. Vui lòng thử lại."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return Response(data)


def _parse_multi_query_values_safe(request, key):
    from ._view_helpers import _parse_multi_query_values
    return _parse_multi_query_values(request, key)
```

> **Lưu ý:** `temperature_counts` ở trên chỉ đếm theo trang hiện tại. Nếu Sale Manager cần tổng toàn bộ (không phân trang), đổi sang tính trên `base` đầy đủ bằng cách lặp `base.values(...)` với annotate — nhưng để tránh tải nặng, bản đầu đếm theo trang + ghi chú; xác nhận lại với PO khi review. (Quyết định mặc định: đếm trên TOÀN BỘ `base` để stats đúng — thay `for obj in page` bằng vòng lặp riêng trên `base` đã annotate, chỉ lấy `contact_count/last_contact_at/assessment__sales_status_code` qua `.values()`.) Người thực hiện chọn cách tính trên toàn bộ `base` cho stats, và per-row temperature cho `page`.

- [ ] **Step 4: Đăng ký route**

Trong `apps/orders/urls.py`: thêm `path("orders/sheet3", list_sheet3_orders)` (khớp pattern các route `orders/landing`, `orders/koc` hiện có) và import view. Export trong `order_views_impl/__init__.py`.

- [ ] **Step 5: Chạy test xác nhận pass**

Run: `cd backend && python manage.py test apps.orders.tests.test_sheet3_list_api -v 2`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/transport/views/order_views_impl/sheet3_view.py backend/apps/orders/urls.py backend/apps/orders/transport/views/order_views_impl/__init__.py backend/apps/orders/tests/test_sheet3_list_api.py
git commit -m "Add Sheet 3 follow-up list endpoint with temperature"
```

---

### Task B3: Endpoint ghi liên hệ (sale_contact log)

**Files:**
- Create: `backend/apps/orders/transport/views/order_views_impl/contact_log_view.py`
- Modify: `backend/apps/orders/urls.py`, `order_views_impl/__init__.py`
- Test: `backend/apps/orders/tests/test_contact_log_api.py`

- [ ] **Step 1: Viết test fail**

```python
# backend/apps/orders/tests/test_contact_log_api.py
from django.test import TestCase
from rest_framework.test import APIClient
from apps.orders.models import OrderJourneyEvent
from apps.orders.sheet_rules import SALE_CONTACT_EVENT

class ContactLogApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = make_authenticated_user(self.client)
        self.order = make_order(name="A", phone="09", order_source="landing", staff=self.user)

    def test_creates_new_event_each_time_no_overwrite(self):
        for i in range(2):
            resp = self.client.post(
                f"/api/orders/{self.order.id}/contact-log",
                {"outcome": "answered", "transcript": f"call {i}", "not_closed_reason": "watching_demo"},
                format="json",
            )
            self.assertEqual(resp.status_code, 200)
        events = OrderJourneyEvent.objects.filter(order=self.order, event_type=SALE_CONTACT_EVENT)
        self.assertEqual(events.count(), 2)
        # cập nhật lý do chưa chốt lên assessment
        self.order.refresh_from_db()
        self.assertEqual(self.order.assessment.not_closed_reason_code, "watching_demo")

    def test_rejects_invalid_reason(self):
        resp = self.client.post(
            f"/api/orders/{self.order.id}/contact-log",
            {"outcome": "answered", "not_closed_reason": "bogus"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
```

- [ ] **Step 2: Chạy test xác nhận fail**

Run: `cd backend && python manage.py test apps.orders.tests.test_contact_log_api -v 2`
Expected: FAIL — route 404.

- [ ] **Step 3: Viết view**

```python
# backend/apps/orders/transport/views/order_views_impl/contact_log_view.py
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orders.assessment_fields import AssessmentValidationError, coerce_and_validate
from apps.orders.models import Order, OrderAssessment, OrderJourneyEvent
from apps.orders.sheet_rules import SALE_CONTACT_EVENT


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_contact_log(request, order_id: int):
    """Ghi 1 lần liên hệ → tạo OrderJourneyEvent mới (KHÔNG ghi đè).

    Body: { outcome: str, transcript?: str, sales_status?: str, not_closed_reason?: str }
    """
    order = get_object_or_404(Order.objects.filter(deleted_at__isnull=True), pk=order_id)
    outcome = str(request.data.get("outcome") or "").strip()
    transcript = str(request.data.get("transcript") or "").strip()
    sales_status = request.data.get("sales_status")
    not_closed_reason = request.data.get("not_closed_reason")

    # Validate code values dựa trên whitelist sẵn có.
    try:
        if sales_status is not None:
            sales_status = coerce_and_validate("sales_status_code", sales_status)
        if not_closed_reason is not None:
            not_closed_reason = coerce_and_validate("not_closed_reason_code", not_closed_reason)
    except AssessmentValidationError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    OrderJourneyEvent.objects.create(
        order=order,
        event_type=SALE_CONTACT_EVENT,
        actor=request.user,
        note=transcript,
        payload_json={"outcome": outcome, "transcript": transcript},
    )

    # Cập nhật assessment (sales_status / not_closed_reason) nếu có gửi.
    if sales_status is not None or not_closed_reason is not None:
        assessment, _ = OrderAssessment.objects.get_or_create(order=order)
        if sales_status is not None:
            assessment.sales_status_code = sales_status
        if not_closed_reason is not None:
            assessment.not_closed_reason_code = not_closed_reason
        assessment.updated_by = request.user
        assessment.save()

    return Response({"ok": True})
```

- [ ] **Step 4: Đăng ký route** `path("orders/<int:order_id>/contact-log", create_contact_log)` trong `urls.py` + export.

- [ ] **Step 5: Chạy test xác nhận pass**

Run: `cd backend && python manage.py test apps.orders.tests.test_contact_log_api -v 2`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/transport/views/order_views_impl/contact_log_view.py backend/apps/orders/urls.py backend/apps/orders/transport/views/order_views_impl/__init__.py backend/apps/orders/tests/test_contact_log_api.py
git commit -m "Add contact-log endpoint (append-only sale_contact events)"
```

---

### Task B4: Endpoint đóng case + management command auto-close

**Files:**
- Create: `backend/apps/orders/transport/views/order_views_impl/close_case_view.py`
- Create: `backend/apps/orders/management/commands/autoclose_followups.py`
- Modify: `urls.py`, `order_views_impl/__init__.py`
- Test: `backend/apps/orders/tests/test_close_case.py`

- [ ] **Step 1: Viết test fail**

```python
# backend/apps/orders/tests/test_close_case.py
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from io import StringIO
from django.core.management import call_command
from apps.orders.models import Order, OrderAssessment, OrderJourneyEvent
from apps.orders.sheet_rules import SALE_CONTACT_EVENT

class CloseCaseTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = make_authenticated_user(self.client)

    def test_manual_close_sets_archived(self):
        order = make_order(name="A", phone="09", order_source="landing", staff=self.user)
        resp = self.client.post(f"/api/orders/{order.id}/close-case", {"reason": "no_need_anymore"}, format="json")
        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.assessment.sales_status_code, "archived")

    def test_autoclose_after_14_days(self):
        order = make_order(name="A", phone="09", raw_address="", order_source="landing")
        ev = OrderJourneyEvent.objects.create(order=order, event_type=SALE_CONTACT_EVENT)
        OrderJourneyEvent.objects.filter(id=ev.id).update(created_at=timezone.now() - timedelta(days=20))
        call_command("autoclose_followups", stdout=StringIO())
        order.refresh_from_db()
        self.assertEqual(order.assessment.sales_status_code, "archived")

    def test_autoclose_skips_recent(self):
        order = make_order(name="A", phone="09", raw_address="", order_source="landing")
        OrderJourneyEvent.objects.create(order=order, event_type=SALE_CONTACT_EVENT)
        call_command("autoclose_followups", stdout=StringIO())
        order.refresh_from_db()
        self.assertNotEqual(getattr(getattr(order, "assessment", None), "sales_status_code", None), "archived")
```

- [ ] **Step 2: Chạy test xác nhận fail**

Run: `cd backend && python manage.py test apps.orders.tests.test_close_case -v 2`
Expected: FAIL.

- [ ] **Step 3: Viết view đóng case**

```python
# backend/apps/orders/transport/views/order_views_impl/close_case_view.py
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orders.assessment_fields import AssessmentValidationError, coerce_and_validate
from apps.orders.models import Order, OrderAssessment, OrderJourneyEvent
from apps.orders.sheet_rules import CASE_CLOSED_EVENT


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def close_case(request, order_id: int):
    order = get_object_or_404(Order.objects.filter(deleted_at__isnull=True), pk=order_id)
    reason = request.data.get("reason")
    try:
        if reason is not None:
            reason = coerce_and_validate("not_closed_reason_code", reason)
    except AssessmentValidationError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    assessment, _ = OrderAssessment.objects.get_or_create(order=order)
    prev = assessment.sales_status_code
    assessment.sales_status_code = "archived"
    if reason is not None:
        assessment.not_closed_reason_code = reason
    assessment.updated_by = request.user
    assessment.save()
    OrderJourneyEvent.objects.create(
        order=order, event_type=CASE_CLOSED_EVENT, actor=request.user,
        from_code=prev, to_code="archived",
        payload_json={"reason": reason, "auto": False},
    )
    return Response({"ok": True})
```

- [ ] **Step 4: Viết management command auto-close**

```python
# backend/apps/orders/management/commands/autoclose_followups.py
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Max, Q
from django.utils import timezone

from apps.orders.models import Order, OrderAssessment, OrderJourneyEvent
from apps.orders.sheet_rules import (
    CASE_CLOSED_EVENT,
    CLOSED_SALES_STATUSES,
    FOLLOWUP_AUTOCLOSE_DAYS,
    SALE_CONTACT_EVENT,
    SHEET3_SOURCES,
)


class Command(BaseCommand):
    help = "Tự đóng (archive) các đơn follow-up Sheet 3 quá 14 ngày không liên hệ."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=FOLLOWUP_AUTOCLOSE_DAYS)
        contact_q = Q(journey_events__event_type=SALE_CONTACT_EVENT)
        # Đơn Sheet 3: có nguồn (trừ koc), thiếu địa chỉ, chưa đóng/chốt.
        qs = (
            Order.objects.filter(deleted_at__isnull=True, order_source__in=SHEET3_SOURCES)
            .filter(Q(raw_address="") | Q(raw_address__isnull=True), Q(address="") | Q(address__isnull=True))
            .exclude(assessment__sales_status_code__in=CLOSED_SALES_STATUSES)
            .annotate(_last=Max("journey_events__created_at", filter=contact_q))
        )
        # last_contact <= cutoff, hoặc chưa từng liên hệ và tạo > 14 ngày.
        stale = qs.filter(Q(_last__lte=cutoff) | (Q(_last__isnull=True) & Q(created_at__lte=cutoff)))
        count = 0
        for order in stale.iterator():
            assessment, _ = OrderAssessment.objects.get_or_create(order=order)
            prev = assessment.sales_status_code
            assessment.sales_status_code = "archived"
            assessment.save(update_fields=["sales_status_code", "updated_at"])
            OrderJourneyEvent.objects.create(
                order=order, event_type=CASE_CLOSED_EVENT, from_code=prev, to_code="archived",
                payload_json={"reason": None, "auto": True},
            )
            count += 1
        self.stdout.write(f"autoclosed={count}")
```

- [ ] **Step 5: Đăng ký route** `path("orders/<int:order_id>/close-case", close_case)` + export. Đảm bảo có `apps/orders/management/commands/__init__.py` và `apps/orders/management/__init__.py` (tạo nếu thiếu).

- [ ] **Step 6: Chạy test xác nhận pass**

Run: `cd backend && python manage.py test apps.orders.tests.test_close_case -v 2`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/orders/transport/views/order_views_impl/close_case_view.py backend/apps/orders/management/ backend/apps/orders/urls.py backend/apps/orders/transport/views/order_views_impl/__init__.py backend/apps/orders/tests/test_close_case.py
git commit -m "Add close-case endpoint and 14-day auto-close command"
```

> **Lịch chạy cron:** đăng ký command `autoclose_followups` chạy 1 lần/ngày theo pattern cron sẵn có của project (xem các management command + scheduler hiện tại). Nếu KV cron là Go-native (theo memory), thêm entry gọi command này; ghi rõ trong PR để DevOps cấu hình.

---

## Phase C — Frontend: cấu trúc tab + Sheet 1/2

### Task C1: Đổi LandingTabKey sang 6 tab

**Files:**
- Modify: `frontend/src/pages/OrdersPage.tsx:90-97` (LandingTabKey, LANDING_TABS)
- Modify: `frontend/src/features/orders/use-orders-data.ts` (route fetch theo tab)
- Test: `frontend/src/pages/__tests__/OrdersPage.tabs.test.tsx` (nếu có hạ tầng test; nếu không, kiểm thử thủ công + ghi chú)

- [ ] **Step 1: Đổi định nghĩa tab**

```typescript
// OrdersPage.tsx
export type LandingTabKey =
  | "sheet1" | "sheet2" | "sheet3"
  | "delivered" | "cancelled" | "blocklist";

export const LANDING_TABS: Array<{ key: LandingTabKey; label: string }> = [
  { key: "sheet1", label: "Sheet 1: Landing + Hotline + Zalo" },
  { key: "sheet2", label: "Sheet 2: Messenger" },
  { key: "sheet3", label: "Sheet 3: Follow-up khách" },
  { key: "delivered", label: "Giao thành công" },
  { key: "cancelled", label: "Đã hủy" },
  { key: "blocklist", label: "Danh sách blocklist" },
];
```

- [ ] **Step 2: Map tab → filter ở `use-orders-data.ts`**

Khi `landingTab` ∈ {sheet1, sheet2, sheet3}, gọi endpoint phù hợp với filter:
- `sheet1`: `list_landing_orders` + `info_complete=true` + `order_source=["landing","hotline","zalo"]` (dùng filter nguồn sẵn có; landing endpoint đã loại koc).
- `sheet2`: `list_landing_orders` + `info_complete=true` + `order_source=["messenger"]`.
- `sheet3`: endpoint mới `orders/sheet3`.

> Người thực hiện: tìm chỗ `use-orders-data.ts` chọn endpoint landing theo tab (`delivered/cancelled/blocklist`) và thêm nhánh cho `sheet1/2/3`. Truyền `info_complete` + `order_source` vào payload POST. `list_landing_orders` cần đọc thêm `info_complete` (đã hỗ trợ ở repo qua Task A3) — bổ sung dòng `"info_complete": source.get("info_complete", "")` vào `filters` của `list_landing_orders` (Modify `order_list_view.py:152-173`).

- [ ] **Step 3: Build kiểm tra**

Run: `cd frontend && npm run build` (hoặc `npx tsc --noEmit`)
Expected: không lỗi type.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/OrdersPage.tsx frontend/src/features/orders/use-orders-data.ts backend/apps/orders/transport/views/order_views_impl/order_list_view.py
git commit -m "Restructure landing tabs into 3 sheets + delivered/cancelled/blocklist"
```

---

### Task C2: Helper thời gian tương đối + nhiệt độ (frontend)

**Files:**
- Create: `frontend/src/features/orders/format-relative-time.ts`
- Create: `frontend/src/features/orders/__tests__/format-relative-time.test.ts`

- [ ] **Step 1: Viết test fail**

```typescript
// format-relative-time.test.ts
import { describe, it, expect } from "vitest";
import { formatRelativeTime } from "../format-relative-time";

describe("formatRelativeTime", () => {
  const now = new Date("2026-06-02T10:00:00Z");
  it("minutes", () => {
    expect(formatRelativeTime("2026-06-02T09:52:00Z", now)).toBe("8p trước");
  });
  it("hours", () => {
    expect(formatRelativeTime("2026-06-02T09:00:00Z", now)).toBe("1h trước");
  });
  it("yesterday", () => {
    expect(formatRelativeTime("2026-06-01T09:00:00Z", now)).toBe("Hôm qua");
  });
});
```

- [ ] **Step 2: Chạy test fail**

Run: `cd frontend && npx vitest run src/features/orders/__tests__/format-relative-time.test.ts`
Expected: FAIL — module không tồn tại.

- [ ] **Step 3: Implement**

```typescript
// frontend/src/features/orders/format-relative-time.ts
export function formatRelativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "—";
  const then = new Date(iso);
  const diffMs = now.getTime() - then.getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "Vừa xong";
  if (mins < 60) return `${mins}p trước`;
  const hours = Math.floor(mins / 60);
  if (hours < 24 && now.getDate() === then.getDate()) return `${hours}h trước`;
  const days = Math.floor(diffMs / 86400000);
  if (days <= 1) return "Hôm qua";
  if (days < 7) return `${days} ngày trước`;
  return then.toLocaleDateString("vi-VN");
}
```

- [ ] **Step 4: Chạy test pass**

Run: `cd frontend && npx vitest run src/features/orders/__tests__/format-relative-time.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/orders/format-relative-time.ts frontend/src/features/orders/__tests__/format-relative-time.test.ts
git commit -m "Add relative time formatter for orders"
```

---

### Task C3: Stats row + cột gộp + highlight SLA cho Sheet 1/2

**Files:**
- Create: `frontend/src/components/orders/SheetStatsRow.tsx`
- Modify: `frontend/src/components/OrdersTable.tsx` (OrderCell: gộp cell Khách hàng/Địa chỉ/SKU&Giá; cột "Tạo lúc" dùng formatRelativeTime; row class highlight SLA), `frontend/src/features/orders/order-types.ts` (DEFAULT_ORDER_COLUMNS cho sheet)

- [ ] **Step 1: Tạo SheetStatsRow (5 ô click-to-filter)**

```tsx
// frontend/src/components/orders/SheetStatsRow.tsx
type StatItem = { key: string; label: string; value: number; tone?: "danger" | "warn" | "ok" | "info" };

export function SheetStatsRow({ items, onPick }: { items: StatItem[]; onPick: (key: string) => void }) {
  const tone: Record<string, string> = {
    danger: "border-red-400 text-red-600",
    warn: "text-amber-600",
    ok: "text-emerald-600",
    info: "text-blue-600",
  };
  return (
    <div className="flex gap-2.5 mb-4">
      {items.map((it) => (
        <button
          key={it.key}
          onClick={() => onPick(it.key)}
          className={`flex-1 rounded-lg border bg-white px-3.5 py-2.5 text-left hover:shadow ${it.tone === "danger" ? "border-red-300" : "border-slate-200"}`}
        >
          <div className="text-[11px] text-slate-500">{it.label}</div>
          <div className={`text-xl font-bold ${it.tone ? tone[it.tone] : "text-slate-800"}`}>{it.value}</div>
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Tích hợp stats vào OrdersPage cho sheet1/2**

Trong OrdersPage, khi `landingTab` ∈ {sheet1, sheet2}, render `<SheetStatsRow>` với items từ `salesStatusCounts` (Đơn 7 ngày = tổng; Mới chưa gọi; Đã chốt = `closed*`; Hẹn hôm nay; Spam/sai số = `spam`+`wrong_number`). `onPick` set filter tương ứng (vd "new" → `sales_status` rỗng + flag chưa gọi; "closed" → sales_status closed...). Tái dùng `onApplyFilters`.

> "Mới chưa gọi" cần count 0-contact: bản đầu lấy xấp xỉ từ `sales_status_counts["__empty__"]` (chưa đặt trạng thái). Ghi chú để tinh chỉnh sau nếu PO muốn đếm chính xác theo event.

- [ ] **Step 3: Cột gộp + thời gian tương đối + SLA đỏ trong OrdersTable**

- Cell "Khách hàng": badge nguồn + tên đậm + SĐT (đã có name/phone/order_source trong row).
- Cell "Địa chỉ": 2 dòng `province` / `district · ward`.
- Cell "SKU & Giá": `sku` đậm + `total_amount` định dạng tiền.
- Cột "Tạo lúc": `formatRelativeTime(row.created_at)`.
- Row highlight: thêm class nền đỏ khi `isSlaBreached(row)` = nguồn thuộc sheet + chưa gọi (không có contact / sales_status rỗng) + `created_at` > 30 phút. Đưa helper `isSlaBreached` vào `format-relative-time.ts` hoặc file riêng kèm test.

> Người thực hiện: định cấu hình cột mặc định sheet qua một preset mới (vd `SHEET_DEFAULT_COLUMNS`) thay vì sửa `DEFAULT_ORDER_COLUMNS` toàn cục, để không phá các trang khác. Áp preset khi `landingTab` ∈ {sheet1, sheet2}.

- [ ] **Step 4: Test helper SLA**

```typescript
// thêm vào format-relative-time.test.ts hoặc file mới
import { isSlaBreached } from "../sla";
it("sla breach when uncalled and older than 30m", () => {
  const now = new Date("2026-06-02T10:00:00Z");
  expect(isSlaBreached({ created_at: "2026-06-02T09:20:00Z", contact_count: 0 }, now)).toBe(true);
  expect(isSlaBreached({ created_at: "2026-06-02T09:50:00Z", contact_count: 0 }, now)).toBe(false);
});
```

Run: `cd frontend && npx vitest run src/features/orders/__tests__/`
Expected: PASS.

- [ ] **Step 5: Build + commit**

```bash
cd frontend && npx tsc --noEmit
git add frontend/src/components/orders/SheetStatsRow.tsx frontend/src/components/OrdersTable.tsx frontend/src/features/orders/
git commit -m "Add stats row, merged cells, relative time and SLA highlight for Sheet 1/2"
```

---

## Phase D — Frontend: Sheet 3 card

### Task D1: Component card follow-up

**Files:**
- Create: `frontend/src/components/orders/Sheet3FollowUpCard.tsx`
- Create: `frontend/src/components/orders/Sheet3Board.tsx` (stats nhiệt độ + filter nhanh + danh sách card)
- Test: `frontend/src/components/orders/__tests__/Sheet3FollowUpCard.test.tsx`

- [ ] **Step 1: Viết test render**

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { Sheet3FollowUpCard } from "../Sheet3FollowUpCard";

const row = {
  id: 1, name: "Lê Thị Hoa", phone: "0934567890", order_source: "zalo",
  temperature: "hot", contact_count: 1, last_contact_at: "2026-06-01T12:30:00Z",
  sku: "GH-Pro", total_amount: "6900000", staff_name: "Hương",
  crm: { sales_status_code: "scheduled", not_closed_reason_code: "watching_demo",
         events: [{ event_type: "sale_contact", created_at: "2026-06-01T12:30:00Z", note: "tư vấn ghế", actor_name: "Hương" }] },
};

describe("Sheet3FollowUpCard", () => {
  it("shows name, temperature badge, contact count, action buttons", () => {
    render(<Sheet3FollowUpCard row={row as any} onLogContact={vi.fn()} onSchedule={vi.fn()} onClose={vi.fn()} onConvert={vi.fn()} />);
    expect(screen.getByText("Lê Thị Hoa")).toBeInTheDocument();
    expect(screen.getByText(/Nóng/)).toBeInTheDocument();
    expect(screen.getByText(/Ghi liên hệ/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Chạy test fail**

Run: `cd frontend && npx vitest run src/components/orders/__tests__/Sheet3FollowUpCard.test.tsx`
Expected: FAIL — component không tồn tại.

- [ ] **Step 3: Implement card** (3 cột: thông tin + nhiệt độ/cảnh báo; lý do dropdown + lịch sử liên hệ; nút hành động). Lạnh → nút "Đóng case"; Nóng/Ấm → "Chốt đơn". Badge nhiệt độ map: hot→"🔥 Nóng" (đỏ), warm→"🌤 Ấm" (vàng), cold→"❄️ Lạnh" (xanh). Cảnh báo ngày khi `daysSince(last_contact_at) > 7`. Lịch sử = `crm.events` lọc `event_type==="sale_contact"`, mới nhất trên cùng, mỗi dòng `formatRelativeTime(created_at) — note`.

Dropdown "Lý do chưa chốt": load options từ CRM catalog (`not_closed_reason_options`) đã có trong `useOrdersData`; onChange gọi `onLogContact` hoặc patch riêng (quyết định: dropdown gọi `PATCH not_closed_reason` qua endpoint inline update sẵn có hoặc qua contact-log; mặc định dùng contact-log endpoint với chỉ `not_closed_reason`).

- [ ] **Step 4: Chạy test pass**

Run: `cd frontend && npx vitest run src/components/orders/__tests__/Sheet3FollowUpCard.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/orders/Sheet3FollowUpCard.tsx frontend/src/components/orders/__tests__/Sheet3FollowUpCard.test.tsx
git commit -m "Add Sheet 3 follow-up card component"
```

---

### Task D2: Board Sheet 3 (stats + filter nhanh) + wiring vào OrdersPage

**Files:**
- Create: `frontend/src/components/orders/Sheet3Board.tsx`
- Modify: `frontend/src/pages/OrdersPage.tsx` (render Sheet3Board khi `landingTab === "sheet3"` thay cho OrdersTable)
- Modify: `frontend/src/features/orders/use-orders-data.ts` (state cho sheet3 results + `temperature_counts`; action `logContact`, `scheduleFollowup`, `closeCase`)

- [ ] **Step 1: Sheet3Board** — render `SheetStatsRow`(nhiệt độ) + hàng filter nhanh (Tất cả / 📅 Có hẹn hôm nay (n) nổi bật / Cần gọi lại <24h / ⚠ Cảnh báo >7 ngày) + danh sách `Sheet3FollowUpCard`. Filter client-side trên mảng results theo `temperature` / `next_followup_at` hôm nay / `last_contact_at`.

- [ ] **Step 2: Actions trong use-orders-data**

```typescript
// thêm vào useOrdersData
const logContact = async (orderId: number, payload: { outcome: string; transcript?: string; sales_status?: string; not_closed_reason?: string }) => {
  await fetchJson(`orders/${orderId}/contact-log`, { method: "POST", body: JSON.stringify(payload) });
  await refetchSheet3();
};
const closeCase = async (orderId: number, reason?: string) => {
  await fetchJson(`orders/${orderId}/close-case`, { method: "POST", body: JSON.stringify({ reason }) });
  await refetchSheet3();
};
```

> Khớp chữ ký `fetchJson` thực tế (xem `@/services/http`). `scheduleFollowup` = patch `next_followup_at` qua endpoint inline update đơn sẵn có.

- [ ] **Step 3: Wiring OrdersPage** — khi `landingTab === "sheet3"`, render `<Sheet3Board rows={sheet3Rows} temperatureCounts={...} onLogContact={logContact} onCloseCase={closeCase} ... />` thay cho bảng. Ẩn toolbar cột (không áp dụng cho card).

- [ ] **Step 4: Build + test**

Run: `cd frontend && npx tsc --noEmit && npx vitest run src/components/orders/__tests__/`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/orders/Sheet3Board.tsx frontend/src/pages/OrdersPage.tsx frontend/src/features/orders/use-orders-data.ts
git commit -m "Wire Sheet 3 board with stats, quick filters and follow-up actions"
```

---

## Phase E — Hoàn thiện

### Task E1: Chạy toàn bộ test + dọn dẹp

- [ ] **Step 1: Backend test suite (các module mới)**

Run: `cd backend && python manage.py test apps.orders.tests.test_crm_catalog_sheet3 apps.orders.tests.test_sheet_rules apps.orders.tests.test_sheet_info_complete_filter apps.orders.tests.test_followup_temperature apps.orders.tests.test_sheet3_list_api apps.orders.tests.test_contact_log_api apps.orders.tests.test_close_case -v 2`
Expected: tất cả PASS.

- [ ] **Step 2: Frontend test**

Run: `cd frontend && npx vitest run src/features/orders src/components/orders`
Expected: PASS. (Lưu ý: một số test frontend cũ fail sẵn trên master — xem memory, không phải do thay đổi này.)

- [ ] **Step 3: Lint backend** `cd backend && ruff check apps/orders` (nếu cấu hình có ruff).

- [ ] **Step 4: Cập nhật spec** ghi tiến độ đã làm/chưa làm vào `docs/superpowers/specs/2026-06-02-orders-landing-3sheet-design.md`.

- [ ] **Step 5: Commit + push**

```bash
git add -A
git commit -m "Finalize orders landing 3-sheet redesign"
git push
```

---

## Self-review checklist (đã rà)
- **Spec coverage:** routing 3 sheet (A2/A3/C1) · stats row (C3) · thời gian tương đối (C2) · SLA đỏ (C3) · cột gộp (C3) · nhiệt độ tự động (B1/B2) · lý do chuẩn hoá (A1/D1) · ghi liên hệ append-only (B3) · đóng case + auto-close 14 ngày (B4) · filter "Có hẹn hôm nay" (D2). ✅
- **Quyết định mở đã chốt:** archived sales_status (A1) · "Mới chưa gọi" = 0 sale_contact (C3 note). ✅
- **Type consistency:** `event_type="sale_contact"` (SALE_CONTACT_EVENT) dùng nhất quán B1/B3/B4; `temperature` ∈ {hot,warm,cold} B1/B2/D1; `info_complete` "true"/"false" A3/B2/C1. ✅
- **Phụ thuộc người thực hiện cần verify khi làm:** URL prefix (`/api/orders` vs `/orders`); tồn tại factory test order; chữ ký `fetchJson`; chỗ chọn endpoint theo tab trong `use-orders-data.ts`; scheduler cho command auto-close.
