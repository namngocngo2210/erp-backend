# CSKH: bộ đếm chờ theo mốc GTC + cột SKU/giá đơn + lọc sản phẩm — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trang Danh sách contact đếm thời gian chờ từ mốc đơn chuyển sang Giao thành công (GTC) cho nhóm Chăm sóc sau bán, hiển thị thêm SKU + giá đơn thực thu, và lọc được danh sách theo sản phẩm.

**Architecture:** Toàn bộ thay đổi backend nằm trong `ContactListService` — hai query mới (`OrderJourneyEvent` để lấy mốc GTC, `OrderItem` để lấy SKU) chạy trong `_build_items`, tức chỉ trên ≤100 dòng của trang hiện tại, không đụng đường tính `action_counts`/`operation_counts` vốn quét toàn tập kết quả. Bộ lọc sản phẩm áp bằng `Exists(OrderItem…)` trước `DISTINCT ON`. Frontend thêm 2 cột vào `ContactsTable`, 1 `MultiSelect` vào `ContactsFilterBar`, và một helper thuần cho tooltip.

**Tech Stack:** Django 5 + DRF + drf-spectacular (backend), PostgreSQL 16, React 19 + TypeScript + shadcn/ui + TanStack Query (frontend), pytest + vitest.

**Spec:** `docs/superpowers/specs/2026-08-12-cskh-gtc-waiting-sku-design.md`

## Global Constraints

- Worktree: `c:\Users\MYPC\Desktop\workspace\desi-order-cskh-gtc-waiting`, branch `feat/cskh-gtc-waiting-sku` (đã tạo từ master `ec1e4be5`). Mọi lệnh chạy trong worktree này.
- Commit message: tiếng Anh, ngắn, **không** kèm dòng `Co-Authored-By`.
- Python 3.12, ruff bắt buộc xanh (CI chạy `ruff check .` đầy đủ theo `backend/pyproject.toml`).
- **Không regen `backend/schema.yaml` bằng lệnh** — file này đang lệch sẵn ~444 dòng trên master; regen sẽ kéo nhiễu của feature khác vào MR. Sửa tay đúng phần contacts, kiểm chứng bằng cách regen ra file tạm rồi `diff` (đúng tiền lệ `docs/invoice-code-edit-lock.md:150`).
- Tiền tệ qua API trả **chuỗi** (`str(Decimal)`) — DRF mặc định `COERCE_DECIMAL_TO_STRING=True`, `schema.yaml` khai `type: string, format: decimal`. Không trả float.
- Hằng `STATUS_CHANGED_EVENT = "order_status_changed"` chỉ có MỘT nguồn: `apps.orders.subscribers`. Không khai lại chuỗi này ở nơi khác.

### Lệnh chạy test

Backend (dựng lại hạ tầng test nếu Docker Desktop vừa restart — container không có restart policy):

```bash
docker network create desi-test-net 2>/dev/null
docker run -d --name desi-test-pg --network desi-test-net \
  -e POSTGRES_USER=desi_order -e POSTGRES_PASSWORD=desi_order -e POSTGRES_DB=desi_order \
  public.ecr.aws/docker/library/postgres:16-alpine 2>/dev/null
docker run -d --name desi-test-redis --network desi-test-net \
  public.ecr.aws/docker/library/redis:7-alpine 2>/dev/null
```

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-cskh-gtc-waiting && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_cskh_gtc \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders/tests/test_contact_list.py -q --create-db'
```

Ruff (image test không có ruff):

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-cskh-gtc-waiting && docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

Frontend — worktree này là worktree **anh em** (nằm cạnh repo chính) nên junction `node_modules` dùng được:

```powershell
New-Item -ItemType Junction -Path "C:\Users\MYPC\Desktop\workspace\desi-order-cskh-gtc-waiting\frontend\node_modules" `
         -Target "C:\Users\MYPC\Desktop\workspace\desi-order\frontend\node_modules"
```

Sau đó trong `frontend/`: `npx vitest run <file>`, `npx tsc --noEmit`.

---

## File Structure

**Backend**

| File | Trách nhiệm | Thay đổi |
|---|---|---|
| `backend/apps/orders/application/services/contact_list_service.py` | Aggregate 1 dòng/khách | Thêm `_gtc_at_map`, `_order_items_map`, param `product_ids`, mở rộng `_build_items` |
| `backend/apps/orders/transport/serializers/contact_serializers.py` | Khai OpenAPI | Thêm `waiting_basis`, `items`, `total_after_discount` |
| `backend/apps/orders/transport/views/order_views_impl/contact_list_view.py` | Parse query param | Thêm param `product` |
| `backend/apps/orders/tests/test_contact_list.py` | Test service + API | Thêm 3 test class |
| `backend/schema.yaml` | Hợp đồng OpenAPI | Sửa tay phần contacts |

**Frontend**

| File | Trách nhiệm | Thay đổi |
|---|---|---|
| `frontend/src/api/schema.d.ts` | Type sinh từ OpenAPI | Sửa tay phần contacts |
| `frontend/src/lib/api/contacts.ts` | API client | Thêm `product` vào params |
| `frontend/src/features/contacts/contact-actions.ts` | Logic thuần của trang | Thêm `waitingTooltip` |
| `frontend/src/features/contacts/components/ContactSkuCell.tsx` | **MỚI** — render ô SKU | Tạo mới |
| `frontend/src/features/contacts/components/ContactsTable.tsx` | Bảng | Thêm 2 cột + tooltip |
| `frontend/src/features/contacts/components/ContactsFilterBar.tsx` | Thanh lọc | Thêm MultiSelect sản phẩm |
| `frontend/src/features/contacts/pages/ContactsPage.tsx` | State + query | State `productIds`, nạp danh mục SP |
| `frontend/src/features/contacts/__tests__/contact-actions.test.ts` | Test helper thuần | Thêm test `waitingTooltip` |
| `frontend/src/features/contacts/__tests__/contact-cells.test.tsx` | Test component | Thêm test `ContactSkuCell` |

---

### Task 1: Mốc GTC cho `waiting_since` + `waiting_basis`

**Files:**
- Modify: `backend/apps/orders/application/services/contact_list_service.py`
- Modify: `backend/apps/orders/transport/serializers/contact_serializers.py:37-49`
- Test: `backend/apps/orders/tests/test_contact_list.py`

**Interfaces:**
- Produces: `ContactListService._gtc_at_map(order_ids: list[int]) -> dict[int, datetime]`
- Produces: mỗi item có `waiting_basis: str` — `"gtc"` hoặc `"activity"`
- Consumes: `apps.orders.subscribers.STATUS_CHANGED_EVENT` (= `"order_status_changed"`), `OrderStatus.DELIVERED_SUCCESS` (= 3)

Bối cảnh cho người làm: `Order.status` là int (`OrderStatus`), còn `OrderJourneyEvent.from_code`/`to_code` là `CharField` — subscriber ghi `str(event.new_status)` nên đơn GTC có `to_code == "3"`. Subscriber này chỉ tồn tại từ 23/07/2026, đơn GTC cũ hơn không có event → fallback `Order.updated_at`.

- [x] **Step 1: Viết test thất bại**

Thêm vào cuối `backend/apps/orders/tests/test_contact_list.py`:

```python
class GtcWaitingSinceTests(ContactListServiceTestsBase):
    """Nhóm Chăm sóc sau bán đếm chờ từ mốc chuyển GTC, không phải từ ngày data về."""

    def setUp(self):
        self.admin = make_admin("admin-contacts-gtc")
        self.now = timezone.now()

    def _gtc_event(self, order, *, at, from_code="2"):
        event = OrderJourneyEvent.objects.create(
            order=order,
            event_type="order_status_changed",
            from_code=from_code,
            to_code=str(OrderStatus.DELIVERED_SUCCESS.value),
        )
        OrderJourneyEvent.objects.filter(pk=event.pk).update(created_at=at)
        return event

    def _item_for(self, customer):
        result = ContactListService.list_contacts(user=self.admin)
        items = [i for i in result["items"] if i["customer_id"] == customer.id]
        self.assertEqual(len(items), 1)
        return items[0]

    def test_delivered_order_waits_from_journey_event(self):
        customer = _customer(full_name="GTC Event", phone_raw="0921111111", phone_normalized="0921111111")
        order = self._order(
            customer,
            created_at=self.now - timedelta(days=11),
            status=OrderStatus.DELIVERED_SUCCESS,
        )
        gtc_at = self.now - timedelta(days=3)
        self._gtc_event(order, at=gtc_at)

        item = self._item_for(customer)

        self.assertEqual(item["waiting_basis"], "gtc")
        self.assertEqual(item["waiting_since"], gtc_at)

    def test_delivered_order_without_event_falls_back_to_updated_at(self):
        customer = _customer(full_name="GTC Fallback", phone_raw="0921111112", phone_normalized="0921111112")
        order = self._order(
            customer,
            created_at=self.now - timedelta(days=11),
            status=OrderStatus.DELIVERED_SUCCESS,
        )
        updated_at = self.now - timedelta(days=5)
        Order.objects.filter(pk=order.pk).update(updated_at=updated_at)

        item = self._item_for(customer)

        self.assertEqual(item["waiting_basis"], "gtc")
        self.assertEqual(item["waiting_since"], updated_at)

    def test_redelivered_order_uses_latest_gtc_event(self):
        """GTC → hoàn → GTC lại: đếm từ lần GTC đang hiệu lực, không phải lần đầu."""
        customer = _customer(full_name="GTC Lai", phone_raw="0921111113", phone_normalized="0921111113")
        order = self._order(
            customer,
            created_at=self.now - timedelta(days=20),
            status=OrderStatus.DELIVERED_SUCCESS,
        )
        self._gtc_event(order, at=self.now - timedelta(days=15))
        latest_gtc = self.now - timedelta(days=2)
        self._gtc_event(order, at=latest_gtc, from_code="4")

        item = self._item_for(customer)

        self.assertEqual(item["waiting_since"], latest_gtc)

    def test_non_delivered_order_keeps_activity_basis(self):
        customer = _customer(full_name="Chua GTC", phone_raw="0921111114", phone_normalized="0921111114")
        created_at = self.now - timedelta(days=4)
        self._order(
            customer,
            created_at=created_at,
            status=OrderStatus.IN_DELIVERY,
            sales_status_code="closed",
        )

        item = self._item_for(customer)

        self.assertEqual(item["waiting_basis"], "activity")
        self.assertEqual(item["waiting_since"], created_at)
```

- [x] **Step 2: Chạy test để xác nhận đỏ**

Chạy lệnh backend ở mục "Lệnh chạy test" với `apps/orders/tests/test_contact_list.py::GtcWaitingSinceTests`.
Kỳ vọng: FAIL với `KeyError: 'waiting_basis'`.

- [x] **Step 3: Thêm `_gtc_at_map` vào service**

Trong `contact_list_service.py`, sửa khối import ở đầu file:

```python
from django.db.models import Count, Max, Q

from apps.customers.models import ContactActivity
from apps.orders.domain.contact_rules import derive_action_needed
from apps.orders.models import (
    MARKETPLACE_ORDER_SOURCES,
    Order,
    OrderJourneyEvent,
    OrderStatus,
)
from apps.orders.subscribers import STATUS_CHANGED_EVENT
from apps.users.scoping import OWN_SCOPE_SENTINEL, resolved_channel_codes
```

Thêm hằng ngay dưới `_DEFAULT_WINDOW_DAYS`:

```python
# to_code của journey event khi đơn chuyển sang Giao thành công. Subscriber ghi
# str(int) nên phải so sánh bằng chuỗi, không phải bằng OrderStatus.
_GTC_TO_CODE = str(OrderStatus.DELIVERED_SUCCESS.value)
```

Thêm static method ngay sau `_last_journey_event_map`:

```python
    @staticmethod
    def _gtc_at_map(order_ids: list[int]) -> dict[int, object]:
        """order_id → mốc chuyển sang GTC GẦN NHẤT.

        Lấy Max chứ không Min: đơn có thể GTC → hoàn → GTC lại, bộ đếm chăm sóc
        sau bán phải tính từ lần GTC đang hiệu lực. Query chỉ chạy trên order của
        TRANG hiện tại (≤100), phủ bởi index ord_journey_ord_type_cr_idx.
        """
        if not order_ids:
            return {}
        rows = (
            OrderJourneyEvent.objects.filter(
                order_id__in=order_ids,
                event_type=STATUS_CHANGED_EVENT,
                to_code=_GTC_TO_CODE,
            )
            .values("order_id")
            .annotate(last=Max("created_at"))
        )
        return {row["order_id"]: row["last"] for row in rows}
```

- [x] **Step 4: Dùng mốc GTC trong `_build_items`**

Trong `_build_items`, ngay sau dòng `order_count_map = ...`, thêm:

```python
        gtc_at_map = ContactListService._gtc_at_map(page_ids)
```

Thay khối tính `waiting_since` (đang là 3 dòng `waiting_since = row["created_at"]` …) bằng:

```python
            # Nhóm chăm sóc sau bán đếm từ lúc khách NHẬN được hàng, không phải từ
            # ngày data về — mốc GTC là thứ CSKH thực sự trễ so với nó. Đơn GTC
            # trước 23/07/2026 chưa có journey event → updated_at là mốc gần đúng
            # nhất (cùng quy ước trang Theo dõi mua lại).
            if order.status == OrderStatus.DELIVERED_SUCCESS:
                waiting_since = gtc_at_map.get(order.id) or order.updated_at
                waiting_basis = "gtc"
            else:
                waiting_since = row["created_at"]
                if row["last_activity_at"] is not None and row["last_activity_at"] > waiting_since:
                    waiting_since = row["last_activity_at"]
                waiting_basis = "activity"
```

Trong dict `items.append({...})`, thêm ngay sau dòng `"waiting_since": ...`:

```python
                    "waiting_basis": waiting_basis,
```

- [x] **Step 5: Khai field mới trong serializer**

`contact_serializers.py`, trong `ContactItemSerializer` thêm ngay sau `waiting_since`:

```python
    # "gtc" = đếm từ mốc chuyển Giao thành công (nhóm chăm sóc sau bán);
    # "activity" = đếm từ ngày data về / lần tương tác cuối. FE cần biết để
    # render tooltip đúng ngữ nghĩa — cùng một cột nhưng hai cách đọc.
    waiting_basis = serializers.ChoiceField(choices=["gtc", "activity"])
```

- [x] **Step 6: Chạy test để xác nhận xanh**

Chạy `apps/orders/tests/test_contact_list.py -q`.
Kỳ vọng: PASS toàn bộ file (test cũ không được đỏ).

- [x] **Step 7: Ruff**

Chạy lệnh ruff ở mục "Lệnh chạy test". Kỳ vọng: `All checks passed!`.

- [x] **Step 8: Commit**

```bash
git add backend/apps/orders/application/services/contact_list_service.py \
        backend/apps/orders/transport/serializers/contact_serializers.py \
        backend/apps/orders/tests/test_contact_list.py
git commit -m "feat(contacts): count waiting time from delivery-success milestone"
```

---

### Task 2: SKU + giá đơn thực thu trong `latest_order`

**Files:**
- Modify: `backend/apps/orders/application/services/contact_list_service.py`
- Modify: `backend/apps/orders/transport/serializers/contact_serializers.py:12-22`
- Test: `backend/apps/orders/tests/test_contact_list.py`

**Interfaces:**
- Consumes: `ContactListService._build_items` từ Task 1
- Produces: `latest_order.items: list[dict]` với key `sku: str`, `product_name: str`, `quantity: int`
- Produces: `latest_order.total_after_discount: str` (chuỗi thập phân, vd `"1250000.00"`)

Bối cảnh: `OrderItem` là nguồn chuẩn cho dòng hàng. Đơn sàn / import cũ có thể không có `OrderItem` mà chỉ có chuỗi gộp `Order.sku` (join bằng `", "` ở `order_service_helpers.py:440`, nhưng `import_excel.py:381` join bằng `","` — phải tách được cả hai).

- [x] **Step 1: Viết test thất bại**

Thêm vào cuối `test_contact_list.py`. Chú ý import bổ sung ở đầu file — thêm `OrderItem`, `Product` vào khối import sẵn có:

```python
from apps.orders.models import (
    Order,
    OrderAssessment,
    OrderItem,
    OrderJourneyEvent,
    OrderSource,
    OrderStatus,
    Product,
)
```

```python
class LatestOrderItemsAndAmountTests(ContactListServiceTestsBase):
    """latest_order mang SKU + tiền thực thu để CSKH biết khách đã mua gì, giá bao nhiêu."""

    def setUp(self):
        self.admin = make_admin("admin-contacts-items")
        self.product_a = Product.objects.create(sku="SKU-A", name="San pham A")
        self.product_b = Product.objects.create(sku="SKU-B", name="San pham B")

    def _item_for(self, customer):
        result = ContactListService.list_contacts(user=self.admin)
        items = [i for i in result["items"] if i["customer_id"] == customer.id]
        self.assertEqual(len(items), 1)
        return items[0]

    def test_items_come_from_order_items(self):
        customer = _customer(full_name="Co OrderItem", phone_raw="0922222221", phone_normalized="0922222221")
        order = self._order(customer)
        Order.objects.filter(pk=order.pk).update(total_after_discount="1250000.00")
        OrderItem.objects.create(
            order=order, product=self.product_a, product_name="San pham A", sku="SKU-A", quantity=2
        )
        OrderItem.objects.create(
            order=order, product=self.product_b, product_name="San pham B", sku="SKU-B", quantity=1
        )

        latest = self._item_for(customer)["latest_order"]

        self.assertEqual(
            latest["items"],
            [
                {"sku": "SKU-A", "product_name": "San pham A", "quantity": 2},
                {"sku": "SKU-B", "product_name": "San pham B", "quantity": 1},
            ],
        )
        self.assertEqual(latest["total_after_discount"], "1250000.00")

    def test_order_without_items_falls_back_to_joined_sku_string(self):
        customer = _customer(full_name="Khong OrderItem", phone_raw="0922222222", phone_normalized="0922222222")
        order = self._order(customer)
        Order.objects.filter(pk=order.pk).update(sku="SKU-X, SKU-Y")

        latest = self._item_for(customer)["latest_order"]

        self.assertEqual(
            latest["items"],
            [
                {"sku": "SKU-X", "product_name": "", "quantity": 0},
                {"sku": "SKU-Y", "product_name": "", "quantity": 0},
            ],
        )

    def test_order_without_items_and_without_sku_returns_empty_list(self):
        customer = _customer(full_name="Trong Tron", phone_raw="0922222223", phone_normalized="0922222223")
        self._order(customer)

        self.assertEqual(self._item_for(customer)["latest_order"]["items"], [])
```

- [x] **Step 2: Chạy test để xác nhận đỏ**

Chạy `apps/orders/tests/test_contact_list.py::LatestOrderItemsAndAmountTests -v`.
Kỳ vọng: FAIL với `KeyError: 'items'`.

- [x] **Step 3: Thêm `_order_items_map`**

Trong `contact_list_service.py`, thêm `OrderItem` vào khối import từ `apps.orders.models`. Thêm static method ngay sau `_gtc_at_map`:

```python
    @staticmethod
    def _order_items_map(order_ids: list[int]) -> dict[int, list[dict]]:
        """order_id → danh sách dòng hàng. 1 query trên index order_item_order_cr_idx."""
        if not order_ids:
            return {}
        result: dict[int, list[dict]] = {}
        rows = (
            OrderItem.objects.filter(order_id__in=order_ids)
            .order_by("order_id", "id")
            .values("order_id", "sku", "product_name", "quantity")
        )
        for row in rows:
            result.setdefault(row["order_id"], []).append(
                {
                    "sku": row["sku"],
                    "product_name": row["product_name"],
                    "quantity": row["quantity"],
                }
            )
        return result
```

Thêm hàm module-level ngay dưới `_apply_contacts_scope`:

```python
def _items_from_sku_string(raw: str) -> list[dict]:
    """Tách chuỗi SKU gộp của Order thành dòng hàng tối thiểu.

    Đơn sàn / import cũ không có OrderItem, chỉ có Order.sku là chuỗi join. Hai
    điểm ghi dùng dấu phân cách khác nhau (", " ở order_service_helpers, "," ở
    import_excel) nên tách theo dấu phẩy rồi strip.
    """
    return [
        {"sku": piece, "product_name": "", "quantity": 0}
        for piece in (part.strip() for part in (raw or "").split(","))
        if piece
    ]
```

- [x] **Step 4: Nối vào `_build_items`**

Ngay sau dòng `gtc_at_map = ...` thêm:

```python
        items_by_order = ContactListService._order_items_map(page_ids)
```

Trong dict `"latest_order": {...}`, thêm hai key ngay sau `"status": order.status,`:

```python
                        "items": items_by_order.get(order.id) or _items_from_sku_string(order.sku),
                        "total_after_discount": str(order.total_after_discount),
```

- [x] **Step 5: Khai field mới trong serializer**

`contact_serializers.py`, thêm class mới ngay TRƯỚC `ContactLatestOrderSerializer`:

```python
class ContactOrderItemSerializer(serializers.Serializer):
    """1 dòng hàng của đơn. Đơn không có OrderItem (đơn sàn/import cũ) được suy từ
    chuỗi gộp Order.sku nên product_name rỗng và quantity = 0."""

    sku = serializers.CharField(allow_blank=True)
    product_name = serializers.CharField(allow_blank=True)
    quantity = serializers.IntegerField()
```

Trong `ContactLatestOrderSerializer`, thêm ngay sau `status`:

```python
    items = ContactOrderItemSerializer(many=True)
    total_after_discount = serializers.DecimalField(max_digits=12, decimal_places=2)
```

- [x] **Step 6: Chạy test để xác nhận xanh**

Chạy `apps/orders/tests/test_contact_list.py -q`. Kỳ vọng: PASS toàn bộ file.

- [x] **Step 7: Ruff + commit**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
git add backend/apps/orders/application/services/contact_list_service.py \
        backend/apps/orders/transport/serializers/contact_serializers.py \
        backend/apps/orders/tests/test_contact_list.py
git commit -m "feat(contacts): expose order SKUs and net amount on latest order"
```

---

### Task 3: Bộ lọc theo sản phẩm

**Files:**
- Modify: `backend/apps/orders/application/services/contact_list_service.py:63-96`
- Modify: `backend/apps/orders/transport/views/order_views_impl/contact_list_view.py:73-116`
- Test: `backend/apps/orders/tests/test_contact_list.py`

**Interfaces:**
- Consumes: `_order_items_map` từ Task 2 (không bắt buộc, chỉ cùng file)
- Produces: `ContactListService.list_contacts(..., product_ids: list[int] | None = None)`
- Produces: query param `?product=<id>` (multi-value, chấp cả `?product=1&product=2` lẫn `?product=1,2`)

Bối cảnh quan trọng: danh sách là 1 dòng/khách theo đơn mới nhất (`DISTINCT ON (customer_id)`). Bộ lọc đặt **trước** `DISTINCT ON` nên "đơn mới nhất" thành "đơn mới nhất **có chứa** sản phẩm đã chọn" — đây là hành vi mong muốn, có test khoá lại.

- [x] **Step 1: Viết test thất bại**

Thêm vào cuối `test_contact_list.py`:

```python
class ProductFilterTests(ContactListServiceTestsBase):
    """Lọc theo sản phẩm — match bằng product_id trên OrderItem (không icontains)."""

    def setUp(self):
        self.admin = make_admin("admin-contacts-product")
        self.now = timezone.now()
        self.product_a = Product.objects.create(sku="PF-A", name="Loc A")
        self.product_b = Product.objects.create(sku="PF-B", name="Loc B")

    def _order_with(self, customer, product, *, created_at):
        order = self._order(customer, created_at=created_at)
        OrderItem.objects.create(
            order=order, product=product, product_name=product.name, sku=product.sku, quantity=1
        )
        return order

    def test_filter_keeps_only_customers_owning_the_product(self):
        buyer = _customer(full_name="Mua A", phone_raw="0923333331", phone_normalized="0923333331")
        other = _customer(full_name="Mua B", phone_raw="0923333332", phone_normalized="0923333332")
        self._order_with(buyer, self.product_a, created_at=self.now - timedelta(days=1))
        self._order_with(other, self.product_b, created_at=self.now - timedelta(days=1))

        result = ContactListService.list_contacts(user=self.admin, product_ids=[self.product_a.id])

        self.assertEqual([i["customer_id"] for i in result["items"]], [buyer.id])

    def test_row_shows_latest_order_containing_the_product(self):
        """Khách mua A rồi mua B: lọc A phải trả đơn A, không phải đơn mới nhất (B)."""
        customer = _customer(full_name="Mua Ca Hai", phone_raw="0923333333", phone_normalized="0923333333")
        order_a = self._order_with(customer, self.product_a, created_at=self.now - timedelta(days=5))
        self._order_with(customer, self.product_b, created_at=self.now - timedelta(days=1))

        result = ContactListService.list_contacts(user=self.admin, product_ids=[self.product_a.id])

        self.assertEqual([i["latest_order"]["id"] for i in result["items"]], [order_a.id])

    def test_no_filter_returns_latest_order_regardless_of_product(self):
        customer = _customer(full_name="Khong Loc", phone_raw="0923333334", phone_normalized="0923333334")
        self._order_with(customer, self.product_a, created_at=self.now - timedelta(days=5))
        order_b = self._order_with(customer, self.product_b, created_at=self.now - timedelta(days=1))

        result = ContactListService.list_contacts(user=self.admin)
        items = [i for i in result["items"] if i["customer_id"] == customer.id]

        self.assertEqual(items[0]["latest_order"]["id"], order_b.id)
```

- [x] **Step 2: Chạy test để xác nhận đỏ**

Chạy `apps/orders/tests/test_contact_list.py::ProductFilterTests -v`.
Kỳ vọng: FAIL với `TypeError: list_contacts() got an unexpected keyword argument 'product_ids'`.

- [x] **Step 3: Thêm param vào service**

Trong `contact_list_service.py`, thêm `Exists, OuterRef` vào import django:

```python
from django.db.models import Count, Exists, Max, OuterRef, Q
```

Trong chữ ký `list_contacts`, thêm ngay sau `staff_ids`:

```python
        product_ids: list[int] | None = None,
```

Ngay sau khối `if staff_ids: ...`, thêm:

```python
        if product_ids:
            # Match bằng product_id trên index order_item_product_idx. KHÔNG dùng
            # icontains trên OrderItem.sku: đó là seq-scan ~855k dòng mỗi lần lọc
            # (bài học repositories.py:614-641). Lọc đặt TRƯỚC DISTINCT ON nên dòng
            # trả về là đơn mới nhất CÓ CHỨA sản phẩm, không phải đơn mới nhất nói chung.
            base = base.filter(
                Exists(
                    OrderItem.objects.filter(
                        order_id=OuterRef("pk"), product_id__in=product_ids
                    )
                )
            )
```

- [x] **Step 4: Chạy test service để xác nhận xanh**

Chạy `apps/orders/tests/test_contact_list.py::ProductFilterTests -v`. Kỳ vọng: PASS.

- [x] **Step 5: Nối query param ở view**

`contact_list_view.py` — thêm vào list `parameters` của `@extend_schema`, ngay sau dòng `OpenApiParameter("staff", ...)`:

```python
        OpenApiParameter(
            "product", int, many=True, description="Lọc theo product_id (nhiều giá trị)"
        ),
```

Trong thân `sale_contacts_list`, thêm ngay sau `staff_ids=...`:

```python
        product_ids=_parse_int_list(params.getlist("product")) or None,
```

- [x] **Step 6: Viết test API cho query param**

Thêm vào cuối `test_contact_list.py`:

```python
class ProductFilterApiTests(ContactListServiceTestsBase):
    """Query param ?product= đi tới service (parse multi-value như channel/staff)."""

    def setUp(self):
        self.admin = make_admin("admin-contacts-product-api")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.product = Product.objects.create(sku="API-A", name="Api A")
        self.other = Product.objects.create(sku="API-B", name="Api B")
        self.buyer = _customer(full_name="Api Mua A", phone_raw="0924444441", phone_normalized="0924444441")
        self.non_buyer = _customer(full_name="Api Mua B", phone_raw="0924444442", phone_normalized="0924444442")
        for customer, product in ((self.buyer, self.product), (self.non_buyer, self.other)):
            order = self._order(customer)
            OrderItem.objects.create(
                order=order, product=product, product_name=product.name, sku=product.sku, quantity=1
            )

    def test_product_query_param_filters_rows(self):
        response = self.client.get("/api/sale/contacts", {"product": str(self.product.id)})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [i["customer_id"] for i in response.json()["items"]], [self.buyer.id]
        )
```

- [x] **Step 7: Chạy toàn bộ file test**

Chạy `apps/orders/tests/test_contact_list.py -q`. Kỳ vọng: PASS toàn bộ.

- [x] **Step 8: Ruff + commit**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
git add backend/apps/orders/application/services/contact_list_service.py \
        backend/apps/orders/transport/views/order_views_impl/contact_list_view.py \
        backend/apps/orders/tests/test_contact_list.py
git commit -m "feat(contacts): filter contact list by product"
```

---

### Task 4: Cập nhật hợp đồng OpenAPI (sửa tay)

**Files:**
- Modify: `backend/schema.yaml` (khối `ContactItem` ~dòng 9200, `ContactLatestOrder` ~dòng 9241, params `sale_contacts_list` ~dòng 6810)
- Modify: `frontend/src/api/schema.d.ts`

**Interfaces:**
- Consumes: field mới từ Task 1-3
- Produces: type `ContactItem["waiting_basis"]`, `ContactItem["latest_order"]["items"]`, `ContactItem["latest_order"]["total_after_discount"]` dùng được ở Task 5-7

**KHÔNG chạy `python manage.py spectacular` ghi đè `backend/schema.yaml`** — file đang lệch sẵn ~444 dòng trên master, regen sẽ kéo nhiễu vào MR. Sửa tay rồi kiểm chứng bằng diff với bản regen tạm.

- [x] **Step 1: Thêm schema `ContactOrderItem`**

drf-spectacular xuất `components.schemas` theo thứ tự alphabet, nên vị trí chèn phải đúng thì bước diff ở Step 4 mới sạch. `ContactOrderItem` đứng **sau** `ContactOperation` (so sánh `Ope` < `Ord`) và **trước** `Contract` (so sánh `Contac` < `Contra`).

Trong `backend/schema.yaml`, chèn khối mới giữa khối `ContactOperation:` và dòng `    Contract:` (hiện ở ~dòng 9349):

```yaml
    ContactOrderItem:
      type: object
      description: 1 dòng hàng của đơn. Đơn không có OrderItem (đơn sàn/import cũ)
        được suy từ chuỗi gộp Order.sku nên product_name rỗng và quantity = 0.
      properties:
        sku:
          type: string
        product_name:
          type: string
        quantity:
          type: integer
      required:
      - product_name
      - quantity
      - sku
```

- [x] **Step 2: Thêm field vào `ContactItem` và `ContactLatestOrder`**

Trong `ContactItem.properties`, ngay sau khối `waiting_since`, thêm:

```yaml
        waiting_basis:
          enum:
          - gtc
          - activity
          type: string
```

Trong `ContactItem.required` (danh sách alphabet), thêm `- waiting_basis` ngay sau `- waiting_since`.

Trong `ContactLatestOrder.properties`, ngay sau khối `status`, thêm:

```yaml
        items:
          type: array
          items:
            $ref: '#/components/schemas/ContactOrderItem'
        total_after_discount:
          type: string
          format: decimal
          pattern: ^-?\d{0,10}(?:\.\d{0,2})?$
```

Trong `ContactLatestOrder.required`, thêm `- items` (sau `- id`) và `- total_after_discount` (sau `- status`) giữ đúng thứ tự alphabet.

- [x] **Step 3: Thêm query param `product`**

Trong path `/api/sale/contacts` (operationId `sale_contacts_list`), params sắp theo alphabet — chèn giữa `order_source` và `q`:

```yaml
      - in: query
        name: product
        schema:
          type: array
          items:
            type: integer
        description: Lọc theo product_id (nhiều giá trị)
        explode: true
        style: form
```

Đối chiếu format với param `channel` ngay phía trên để copy đúng khuôn (`explode`/`style` phải khớp).

- [x] **Step 4: Kiểm chứng bằng regen ra file tạm**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-cskh-gtc-waiting && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_cskh_gtc \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'python manage.py spectacular --file /tmp/schema-new.yaml 2>/dev/null && sed -n "/ContactItem:/,/ContactOperation:/p" /tmp/schema-new.yaml'
```

So khối in ra với phần vừa sửa tay trong `backend/schema.yaml`. Kỳ vọng: giống nhau về tên field, `type`, `enum`, và danh sách `required`. Nếu lệch, sửa `schema.yaml` theo bản regen (bản regen là chân lý cho phần contacts).

- [x] **Step 5: Sinh lại type FE và cắt về đúng phần contacts**

```bash
cd frontend && npx openapi-typescript ../backend/schema.yaml -o /tmp/schema-new.d.ts && npx prettier --write /tmp/schema-new.d.ts
git diff --no-index src/api/schema.d.ts /tmp/schema-new.d.ts
```

Áp vào `src/api/schema.d.ts` **chỉ** các hunk thuộc: `ContactItem`, `ContactLatestOrder`, `ContactOrderItem`, và `operations["sale_contacts_list"]`. Bỏ qua mọi hunk khác (đó là nợ drift cũ của repo, để MR chore riêng).

- [x] **Step 6: Xác nhận type biên dịch được**

```bash
cd frontend && npx tsc --noEmit
```

Kỳ vọng: không có lỗi. (Nếu có lỗi ở file ngoài `features/contacts`, tức là đã lỡ áp hunk drift không liên quan ở Step 5 — quay lại revert hunk đó.)

- [x] **Step 7: Commit**

```bash
git add backend/schema.yaml frontend/src/api/schema.d.ts
git commit -m "chore(api): sync contacts schema with waiting_basis, items, product filter"
```

---

### Task 5: Tooltip cột Thời gian chờ

**Files:**
- Modify: `frontend/src/features/contacts/contact-actions.ts` (thêm vào cuối file, sau `waitingUrgencyClass`)
- Modify: `frontend/src/features/contacts/components/ContactsTable.tsx:273-282`
- Test: `frontend/src/features/contacts/__tests__/contact-actions.test.ts`

**Interfaces:**
- Consumes: `ContactItem["waiting_basis"]` từ Task 4
- Produces: `waitingTooltip(iso: string | null, basis: string): string`

Lý do tồn tại: sau Task 1, cùng một cột mang hai ngữ nghĩa. Không có tooltip thì người dùng không có cách nào biết dòng nào đếm từ đâu.

- [x] **Step 1: Viết test thất bại**

Thêm vào cuối `frontend/src/features/contacts/__tests__/contact-actions.test.ts` (giữ nguyên khối import sẵn có, chỉ thêm `waitingTooltip` vào danh sách import từ `../contact-actions`):

```ts
describe("waitingTooltip", () => {
  it("mô tả mốc giao thành công khi basis là gtc", () => {
    expect(waitingTooltip("2026-08-01T09:15:00+07:00", "gtc")).toBe(
      "Chờ từ lúc giao thành công 01/08 09:15",
    );
  });

  it("mô tả mốc chờ thường khi basis là activity", () => {
    expect(waitingTooltip("2026-08-01T09:15:00+07:00", "activity")).toBe("Chờ từ 01/08 09:15");
  });

  it("trả chuỗi rỗng khi không có mốc", () => {
    expect(waitingTooltip(null, "gtc")).toBe("");
  });

  it("trả chuỗi rỗng khi ISO không hợp lệ", () => {
    expect(waitingTooltip("khong-phai-ngay", "gtc")).toBe("");
  });
});
```

Lưu ý cho người làm: test chạy ở timezone máy (`Asia/Ho_Chi_Minh`). ISO trong test đã ghi rõ offset `+07:00` nên kết quả không phụ thuộc TZ của runner.

- [x] **Step 2: Chạy test để xác nhận đỏ**

```bash
cd frontend && npx vitest run src/features/contacts/__tests__/contact-actions.test.ts
```

Kỳ vọng: FAIL — `waitingTooltip is not a function`.

- [x] **Step 3: Viết helper**

Thêm vào cuối `frontend/src/features/contacts/contact-actions.ts`:

```ts
/**
 * Nội dung tooltip cho ô "Thời gian chờ". Cột này mang hai ngữ nghĩa tuỳ dòng:
 * đơn đã giao thành công đếm từ mốc GTC, còn lại đếm từ ngày data về / lần
 * tương tác cuối — không nói ra thì người dùng không có cách nào phân biệt.
 */
export function waitingTooltip(iso: string | null, basis: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  const stamp = `${pad(date.getDate())}/${pad(date.getMonth() + 1)} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  return basis === "gtc" ? `Chờ từ lúc giao thành công ${stamp}` : `Chờ từ ${stamp}`;
}
```

- [x] **Step 4: Chạy test để xác nhận xanh**

```bash
cd frontend && npx vitest run src/features/contacts/__tests__/contact-actions.test.ts
```

Kỳ vọng: PASS.

- [x] **Step 5: Nối vào bảng**

Trong `ContactsTable.tsx`, sửa import ở dòng 20:

```tsx
import {
  CONTACT_ACTIONS,
  humanizeWaiting,
  waitingTooltip,
  waitingUrgencyClass,
} from "../contact-actions";
```

Thay ô Thời gian chờ (khối `<TableCell>` chứa `humanizeWaiting`) bằng:

```tsx
                    <TableCell>
                      <span
                        className={cn("text-xs font-medium", waitingUrgencyClass(row.waiting_since))}
                        title={waitingTooltip(row.waiting_since, row.waiting_basis)}
                      >
                        {humanizeWaiting(row.waiting_since)}
                      </span>
                    </TableCell>
```

- [x] **Step 6: Kiểm tra type + commit**

```bash
cd frontend && npx tsc --noEmit
```

```bash
git add frontend/src/features/contacts/contact-actions.ts \
        frontend/src/features/contacts/components/ContactsTable.tsx \
        frontend/src/features/contacts/__tests__/contact-actions.test.ts
git commit -m "feat(contacts): explain waiting-time basis via cell tooltip"
```

---

### Task 6: Cột SKU + Giá đơn

**Files:**
- Create: `frontend/src/features/contacts/components/ContactSkuCell.tsx`
- Modify: `frontend/src/features/contacts/components/ContactsTable.tsx`
- Test: `frontend/src/features/contacts/__tests__/contact-cells.test.tsx`

**Interfaces:**
- Consumes: `ContactItem["latest_order"]["items"]` từ Task 4
- Produces: component `ContactSkuCell({ items }: { items: ContactOrderItem[] })`
- Produces: type `ContactOrderItem` export từ `@/lib/api/contacts`

- [x] **Step 1: Export type dòng hàng**

Thêm vào `frontend/src/lib/api/contacts.ts`, ngay sau dòng export `ContactOperation`:

```ts
/** 1 dòng hàng của đơn mới nhất — {sku, product_name, quantity}. */
export type ContactOrderItem = ContactItem["latest_order"]["items"][number];
```

- [x] **Step 2: Viết test thất bại**

Thêm vào cuối `frontend/src/features/contacts/__tests__/contact-cells.test.tsx` (thêm import ở đầu file: `import { ContactSkuCell } from "@/features/contacts/components/ContactSkuCell";`):

```tsx
describe("ContactSkuCell", () => {
  it("hiển thị SKU duy nhất, không có badge đếm thêm", () => {
    render(<ContactSkuCell items={[{ sku: "SKU-A", product_name: "San pham A", quantity: 1 }]} />);
    expect(screen.getByText("SKU-A")).toBeInTheDocument();
    expect(screen.queryByText(/^\+/)).not.toBeInTheDocument();
  });

  it("hiển thị SKU đầu + badge +N khi đơn nhiều dòng hàng", () => {
    render(
      <ContactSkuCell
        items={[
          { sku: "SKU-A", product_name: "San pham A", quantity: 2 },
          { sku: "SKU-B", product_name: "San pham B", quantity: 1 },
          { sku: "SKU-C", product_name: "San pham C", quantity: 3 },
        ]}
      />,
    );
    expect(screen.getByText("SKU-A")).toBeInTheDocument();
    expect(screen.getByText("+2")).toBeInTheDocument();
  });

  it("tooltip liệt kê đầy đủ SKU kèm số lượng", () => {
    const { container } = render(
      <ContactSkuCell
        items={[
          { sku: "SKU-A", product_name: "San pham A", quantity: 2 },
          { sku: "SKU-B", product_name: "San pham B", quantity: 1 },
        ]}
      />,
    );
    expect(container.firstChild).toHaveAttribute("title", "SKU-A ×2\nSKU-B ×1");
  });

  it("hiển thị gạch ngang khi đơn không có dòng hàng", () => {
    render(<ContactSkuCell items={[]} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("bỏ qua số lượng 0 trong tooltip (đơn suy từ chuỗi SKU gộp)", () => {
    const { container } = render(
      <ContactSkuCell items={[{ sku: "SKU-X", product_name: "", quantity: 0 }]} />,
    );
    expect(container.firstChild).toHaveAttribute("title", "SKU-X");
  });
});
```

- [x] **Step 3: Chạy test để xác nhận đỏ**

```bash
cd frontend && npx vitest run src/features/contacts/__tests__/contact-cells.test.tsx
```

Kỳ vọng: FAIL — không resolve được module `ContactSkuCell`.

- [x] **Step 4: Viết component**

Tạo `frontend/src/features/contacts/components/ContactSkuCell.tsx`:

```tsx
import { Badge } from "@/components/ui/badge";
import type { ContactOrderItem } from "@/lib/api/contacts";

/**
 * Ô "SKU" của bảng contact: hiện SKU đầu tiên + badge đếm phần còn lại, tooltip
 * ra danh sách đầy đủ. Bảng đã 14 cột nên không đổ hết SKU ra ô — đơn sàn có thể
 * có cả chục dòng hàng, mỗi dòng bảng sẽ cao khác nhau.
 *
 * `quantity = 0` là đơn không có OrderItem, SKU suy từ chuỗi gộp Order.sku —
 * không có số lượng thật để hiện.
 */
export function ContactSkuCell({ items }: { items: ContactOrderItem[] }) {
  if (items.length === 0) return <span className="text-muted-foreground">—</span>;

  const [first, ...rest] = items;
  const title = items
    .map((item) => (item.quantity > 0 ? `${item.sku} ×${item.quantity}` : item.sku))
    .join("\n");

  return (
    <div className="flex items-center gap-1" title={title}>
      <span className="truncate text-xs text-foreground">{first.sku || "—"}</span>
      {rest.length > 0 ? (
        <Badge className="shrink-0 border-0 bg-slate-100 px-1.5 text-[10px] text-slate-600">
          +{rest.length}
        </Badge>
      ) : null}
    </div>
  );
}
```

- [x] **Step 5: Chạy test để xác nhận xanh**

```bash
cd frontend && npx vitest run src/features/contacts/__tests__/contact-cells.test.tsx
```

Kỳ vọng: PASS.

- [x] **Step 6: Thêm 2 cột vào bảng**

Trong `ContactsTable.tsx`:

Sửa import — thêm hai dòng vào khối import sẵn có:

```tsx
import { formatCurrency } from "@/lib/format";
import { ContactSkuCell } from "./ContactSkuCell";
```

Đổi `const COLUMN_COUNT = 12;` → `const COLUMN_COUNT = 14;`

Đổi `className` của `<Table>`: `min-w-[1320px]` → `min-w-[1560px]`.

Trong `<colgroup>`, chèn hai `<col>` ngay sau `<col className="w-[130px]" />` (cột "Ngày data về"):

```tsx
            <col className="w-[130px]" />
            <col className="w-[110px]" />
```

Trong mảng nhãn header, chèn hai nhãn giữa `"Ngày data về"` và `"Loại khách"`:

```tsx
                "Ngày data về",
                "SKU",
                "Giá đơn",
                "Loại khách",
```

Trong thân dòng, chèn hai `<TableCell>` ngay sau ô "Ngày data về" (ô chứa `formatDateTime(row.latest_order.created_at)`):

```tsx
                    <TableCell>
                      <ContactSkuCell items={row.latest_order.items} />
                    </TableCell>
                    <TableCell className="text-xs text-foreground whitespace-nowrap">
                      {formatCurrency(row.latest_order.total_after_discount, {
                        style: "suffix",
                        maximumFractionDigits: 0,
                        fallback: "—",
                      })}
                    </TableCell>
```

- [x] **Step 7: Kiểm tra type + toàn bộ test contacts**

```bash
cd frontend && npx tsc --noEmit && npx vitest run src/features/contacts
```

Kỳ vọng: tsc sạch, mọi test contacts PASS.

- [x] **Step 8: Commit**

```bash
git add frontend/src/lib/api/contacts.ts \
        frontend/src/features/contacts/components/ContactSkuCell.tsx \
        frontend/src/features/contacts/components/ContactsTable.tsx \
        frontend/src/features/contacts/__tests__/contact-cells.test.tsx
git commit -m "feat(contacts): add SKU and order amount columns"
```

---

### Task 7: Bộ lọc chọn sản phẩm trên thanh lọc

**Files:**
- Modify: `frontend/src/lib/api/contacts.ts`
- Modify: `frontend/src/features/contacts/components/ContactsFilterBar.tsx`
- Modify: `frontend/src/features/contacts/pages/ContactsPage.tsx`

**Interfaces:**
- Consumes: query param `product` từ Task 3, type `ContactListParams` từ Task 4
- Produces: `ContactsFilterBar` nhận thêm props `productIds: string[]`, `onProductIdsChange: (values: string[]) => void`, `productOptions: MultiSelectOption[]`

Bối cảnh: `MultiSelect` (`@/components/ui/multi-select`) nhận `options: {value, label, code?, keywords?}[]`. Quy ước của repo cho picker sản phẩm: `label` = SKU, `keywords` = tên sản phẩm (tìm được theo tên nhưng chỉ hiện SKU) — xem `DispatchHistoryPage.tsx:94-108`.

- [x] **Step 1: Thêm `product` vào API client**

Trong `frontend/src/lib/api/contacts.ts`, thêm vào type `ContactListParams` ngay sau `operation_bucket`:

```ts
  /** Lọc theo product_id — khách có đơn chứa sản phẩm này. */
  product?: number[];
```

Trong `getContacts`, thêm ngay sau dòng append `action_needed`:

```ts
  params.product?.forEach((id) => query.append("product", String(id)));
```

- [x] **Step 2: Thêm MultiSelect vào thanh lọc**

Trong `ContactsFilterBar.tsx`, thêm import:

```tsx
import { MultiSelect } from "@/components/ui/multi-select";
```

Thêm vào `ContactsFilterBarProps`:

```tsx
  productOptions: { value: string; label: string; keywords?: string }[];
  productIds: string[];
  onProductIdsChange: (values: string[]) => void;
```

Thêm ba tên đó vào danh sách destructure của hàm, rồi chèn `MultiSelect` ngay sau `<DateFilter ... />`:

```tsx
      <MultiSelect
        className="w-[190px] shrink-0"
        options={productOptions}
        selected={productIds}
        onChange={onProductIdsChange}
        placeholder="Tất cả sản phẩm"
        menuPortal
      />
```

`menuPortal` là bắt buộc ở đây: thanh lọc nằm trong container cuộn ngang, dropdown absolute sẽ bị cắt.

- [x] **Step 3: Nạp danh mục + nối state ở trang**

Trong `ContactsPage.tsx`:

Thêm import:

```tsx
import { useQuery } from "@tanstack/react-query";
import { fetchJson } from "@/services/http";
import type { Product } from "@/lib/api/products";
```

Thêm state ngay sau `const [sourceValue, setSourceValue] = useState(SOURCE_ALL);`:

```tsx
  const [productIds, setProductIds] = useState<string[]>([]);
```

Thêm `productIds` vào mảng `filterKey` (ngay sau `sourceValue`) — thiếu bước này thì đổi lọc sản phẩm mà trang vẫn đứng ở trang cũ:

```tsx
  const filterKey = JSON.stringify([
    debouncedQ,
    dateFrom,
    dateTo,
    sourceValue,
    productIds,
    interaction,
    customerType,
    actionFilter,
    operationBucket,
    pageSize,
  ]);
```

Thêm nạp danh mục sản phẩm, đặt cạnh `channelsQuery`:

```tsx
  // Danh mục sản phẩm cho bộ lọc (label = SKU, tìm được theo tên qua `keywords`)
  // — cùng endpoint/quy ước với các trang lọc theo SP khác.
  const productsQuery = useQuery({
    queryKey: ["products", "contacts-filter-options"],
    queryFn: () => fetchJson("/api/orders/products?limit=1000&offset=0") as Promise<Product[]>,
    staleTime: 60_000,
  });
  const productOptions = useMemo(
    () =>
      (Array.isArray(productsQuery.data) ? productsQuery.data : []).map((item) => ({
        value: String(item.id),
        label: item.sku || String(item.id),
        keywords: item.name || undefined,
      })),
    [productsQuery.data],
  );
```

Trong `listParams`, thêm ngay sau khối `if (operationBucket) ...`:

```tsx
    if (productIds.length > 0) params.product = productIds.map(Number);
```

và thêm `productIds` vào mảng dependency của `useMemo` đó.

Trong `resetFilters`, thêm:

```tsx
    setProductIds([]);
```

Truyền props xuống `<ContactsFilterBar ... />`:

```tsx
          productOptions={productOptions}
          productIds={productIds}
          onProductIdsChange={setProductIds}
```

- [x] **Step 4: Kiểm tra type**

```bash
cd frontend && npx tsc --noEmit
```

Kỳ vọng: sạch. (Nếu `@/lib/api/products` không export type `Product`, kiểm tra lại tên export thật trong file đó và dùng đúng tên — `DispatchHistoryPage.tsx:18` đang import chính type này.)

- [x] **Step 5: Chạy toàn bộ test contacts**

```bash
cd frontend && npx vitest run src/features/contacts
```

Kỳ vọng: PASS.

- [x] **Step 6: Commit**

```bash
git add frontend/src/lib/api/contacts.ts \
        frontend/src/features/contacts/components/ContactsFilterBar.tsx \
        frontend/src/features/contacts/pages/ContactsPage.tsx
git commit -m "feat(contacts): add product picker filter to contact list"
```

---

### Task 8: Kiểm chứng toàn bộ + MR

**Files:** không sửa code, trừ khi bước kiểm chứng lộ ra hồi quy.

- [x] **Step 1: Đo baseline test FE trên master TRƯỚC khi kết luận**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order/frontend && npx vitest run 2>&1 | tail -20
```

Ghi lại số file/test pass-fail. Đây là baseline — không được chép baseline cũ từ ghi chú, phải đo tại thời điểm này.

- [x] **Step 2: Chạy full suite FE trong worktree**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-cskh-gtc-waiting/frontend && npx vitest run 2>&1 | tail -20
```

Kỳ vọng: không đỏ thêm so với baseline Step 1.

- [x] **Step 3: Chạy nhóm test backend liên quan**

Dùng lệnh backend ở mục "Lệnh chạy test" với paths:
`apps/orders/tests/test_contact_list.py apps/orders/tests/test_contact_api_scope.py apps/orders/tests/test_contact_record_scope.py apps/orders/tests/test_status_journey_subscriber.py`

Kỳ vọng: PASS toàn bộ.

- [x] **Step 4: Ruff toàn backend**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-cskh-gtc-waiting && docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

Kỳ vọng: `All checks passed!`.

- [x] **Step 5: Kiểm tra diff không lẫn nhiễu**

```bash
git diff master --stat
```

Kỳ vọng: chỉ các file liệt kê ở mục File Structure + file spec/plan. Nếu `frontend/src/api/schema.d.ts` có hunk ngoài phạm vi contacts → quay lại Task 4 Step 5 gỡ ra.

- [x] **Step 6: Cập nhật tiến độ vào plan**

Tick toàn bộ checkbox đã làm trong file này, thêm mục "Ghi chú sau khi làm" ở cuối ghi rõ: đã làm gì, chưa làm gì (backfill mốc GTC cho đơn cũ; đơn không có OrderItem không lọc được theo sản phẩm), và kết quả test thực tế.

- [ ] **Step 7: Push + tạo MR**

Repo không có `glab`/token — dùng push options:

```bash
git push -o merge_request.create \
         -o merge_request.title="CSKH: waiting time from delivery-success, SKU/amount columns, product filter" \
         -o merge_request.remove_source_branch \
         -u origin feat/cskh-gtc-waiting-sku
```

Lấy link MR trong output của lệnh push và gửi cho user.

---

## Ghi chú sau khi làm

**Đã làm (Task 1-7, Task 8 Step 1-6):** toàn bộ 7 task triển khai đã commit trên `feat/cskh-gtc-waiting-sku` (base `ec1e4be5`): mốc GTC cho `waiting_since`/`waiting_basis` (0050d032), SKU + `total_after_discount` trong `latest_order` (a55ee65c), bộ lọc `?product=<id>` (eab969cd), sync `schema.yaml`/`schema.d.ts` (7f920b54), tooltip cột Thời gian chờ + fix test bom múi giờ (6545d7db, cc18afab), 2 cột SKU/Giá đơn trên bảng (2d407afd), MultiSelect chọn sản phẩm trên thanh lọc (840b61f8). Task 8 đã chạy Step 1-6 (kiểm chứng); **Step 7 (push + tạo MR) CHƯA làm** — chờ vòng review toàn nhánh tiếp theo.

**Kết quả test thực tế đo ngày 2026-08-12:**

- FE baseline (repo chính `desi-order/frontend`, branch `master`): Test Files 2 failed | 141 passed (143) — Tests 3 failed | 1072 passed (1075). 3 failure đều do `foxia-report` không nằm trong sidebar zone/pinned (`src/app/__tests__/modules.test.ts` + `src/app/__tests__/nav-layout.test.ts`), không liên quan tính năng này.
- FE worktree (`desi-order-cskh-gtc-waiting/frontend`): Test Files 2 failed | 141 passed (143) — Tests 3 failed | 1081 passed (1084). Cùng 3 test đỏ, cùng 2 file, y hệt baseline — **không có hồi quy**. 9 test pass thêm là test mới của feature (`contact-actions.test.ts`, `contact-cells.test.tsx`).
- Backend (`test_contact_list.py test_contact_api_scope.py test_contact_record_scope.py test_status_journey_subscriber.py`, DB `desi_cskh_gtc_t8`, Redis DB 10): **132 passed**, 0 failed, 146.96s.
- Ruff toàn backend: `All checks passed!`.
- `git diff` — lưu ý `master` cục bộ (`ba82fb59`) đã vượt qua merge-base thật của nhánh (`ec1e4be5`) một commit không liên quan (`fix/flow-sku-row-pair-gate`, đụng `dispatch_history_service.py`/`flow_history_views.py`/1 doc flow-history). Vì vậy `git diff master --stat` thô lẫn 4 file nhiễu từ commit đó — không phải nhiễu của nhánh này. `git diff ec1e4be5 --stat` (đúng điểm rẽ nhánh) cho ra đúng 16 file: 5 file backend + 2 file doc (spec + plan) + 9 file frontend, khớp 100% bảng File Structure. `frontend/src/api/schema.d.ts` chỉ có 4 hunk, tất cả thuộc phạm vi contacts: `ContactItem.waiting_basis`, `ContactLatestOrder.items`/`total_after_discount`, schema mới `ContactOrderItem`, schema mới `WaitingBasisEnum`, và `operations["sale_contacts_list"].parameters.query.product`. Không có hunk lạ.

**Chưa làm:**

1. Chưa backfill mốc GTC cho đơn cũ — đơn GTC trước 23/07/2026 (thời điểm bắt đầu ghi `OrderJourneyEvent` đầy đủ) vẫn dùng `updated_at` làm mốc gần đúng thay vì mốc GTC thật.
2. Đơn không có `OrderItem` (đơn sàn/import cũ, chỉ có chuỗi `Order.sku` gộp) KHÔNG lọc được bằng bộ lọc sản phẩm vì không có `product_id` để join — bộ lọc `?product=` chỉ áp dụng cho đơn có `OrderItem` chuẩn.
3. Chưa có test cấp trang (page-level) cho `ContactsPage` — test hiện có chỉ ở cấp helper thuần (`contact-actions.test.ts`) và cấp component đơn lẻ (`contact-cells.test.tsx`).

**Sai sót trong plan đã phát hiện khi thực thi:**

1. Task 5 Step 1: plan khẳng định chuỗi ISO có offset `+07:00` làm test độc lập múi giờ — **SAI**. `waitingTooltip` format bằng getter local (`getHours`/`getMinutes`...) nên test chỉ xanh khi máy chạy ở UTC+7; đã sửa lại test để dựng mốc thời gian bằng `new Date(2026, 7, 1, 9, 15)` (local-time constructor) thay vì chuỗi ISO có offset.
2. Task 4: `waiting_basis` thực tế được drf-spectacular sinh thành `$ref: '#/components/schemas/WaitingBasisEnum'` (schema enum riêng, đặt tên theo field), không phải enum khai inline trong `ContactItem` như plan mô tả.
3. Task 4: trong `ContactItem.required`, `waiting_basis` đứng **trước** `waiting_since` theo thứ tự alphabet (drf-spectacular sort required list theo tên field) — plan viết thứ tự ngược lại.
4. Task 4: query param `product` trong `schema.yaml` **không có** khoá `explode`/`style` — rà toàn bộ `schema.yaml` hiện tại không có param nào (kể cả param mảng khác) khai 2 khoá này; đoạn YAML mẫu trong plan có `explode: false` là thừa, không khớp convention thật của file.

## Vòng review toàn nhánh + đợt sửa cuối (sau Task 8)

Review toàn nhánh (`ec1e4be5..82822b83`) không tìm thấy lỗi Critical, nhưng ra **2 finding Important nằm ở chính spec** chứ không ở implementation. Đã sửa trong 2 commit: `90e057be` (backend) + `df983aac` (frontend).

**F1 — tooltip khẳng định sai khi rơi vào fallback.** `waiting_basis` trước đây gán `"gtc"` cho MỌI đơn GTC, kể cả đơn không có journey event nên giá trị thật chỉ là `Order.updated_at` — một mốc bị đẩy bởi bất kỳ thao tác ghi nào lên đơn. Vì subscriber chỉ có từ 23/07/2026 còn cửa sổ mặc định là 92 ngày, **đa số dòng chăm sóc sau bán hiện tại chạy nhánh fallback**, tức tooltip đang khẳng định một mốc chính xác mà nó không biết. Đã tách thành giá trị thứ ba `"gtc_estimated"`; `waitingTooltip` trả "Chờ từ lúc giao thành công (ước tính) …" cho nhánh này. `WaitingBasisEnum` giờ là `"gtc" | "gtc_estimated" | "activity"`.

**F2 — bộ đếm GTC làm cột đỏ vĩnh viễn.** `waitingUrgencyClass` dùng ngưỡng thiết kế cho bộ đếm theo GIỜ (≥3h vàng, ≥24h đỏ, ≥72h đỏ đậm). Bộ đếm GTC tính theo NGÀY và không reset khi CSKH đã chăm sóc, nên mọi dòng chăm sóc sau bán đều vượt 72h → đỏ đậm vĩnh viễn, kể cả dòng vừa gọi hôm qua. Màu mất tác dụng phân loại và mâu thuẫn với chip "Chưa tương tác/Mới tương tác" trên cùng dòng.

**Quyết định của user:** giữ nguyên con số (vẫn đếm từ mốc GTC, KHÔNG reset theo tương tác — đúng yêu cầu ban đầu), chỉ nới ngưỡng màu riêng cho nhóm GTC. Thang mới cho basis `gtc`/`gtc_estimated`: `<3d` bình thường, `≥3d` vàng, `≥7d` đỏ, `≥14d` đỏ đậm. Basis `activity` giữ nguyên thang giờ cũ. Chữ ký đổi thành `waitingUrgencyClass(iso, basis, now)`.

**Minor đã sửa cùng đợt:** xoá dòng trống thừa trong `ContactListService`; sửa comment nói quá về độ phủ của index `ord_journey_ord_type_cr_idx` (`to_code` là residual filter, không nằm trong index); thêm test tách chuỗi SKU kiểu `","` không khoảng trắng; `min-w-0` cho ô SKU (thiếu nó thì SKU dài đẩy badge `+N` tràn sang cột bên); `sku` rỗng fallback sang `product_name` ở cả hiển thị lẫn tooltip; `items ?? []` phòng cửa sổ deploy lệch FE/BE.

**Sai sót thứ 5 trong plan/spec:** lý do "đơn sàn" cho nhánh fallback tách chuỗi `Order.sku` là **SAI** — `_EXCLUDED_ORDER_SOURCES` loại hẳn đơn sàn khỏi endpoint này nên chúng không bao giờ tới nhánh đó. Dân số thật là đơn import cũ / đơn tạo trước khi có `OrderItem`. Đã sửa ở 4 chỗ, gồm cả description CÔNG KHAI của `ContactOrderItem` trong `schema.yaml`.

**Kết quả sau đợt sửa:** backend `test_contact_list.py` 54 passed; ruff `All checks passed!`; `vitest run src/features/contacts` 68 passed (5 file); `tsc --noEmit` sạch; bản sửa tay `schema.yaml` khớp byte-for-byte với bản regen ở phần contacts.

**Còn nợ (park lại, cố ý không sửa):**

4. `frontend/src/api/schema.d.ts` — khối JSDoc phía trên `WaitingBasisEnum` vẫn chỉ liệt kê `gtc`/`activity`; chỉ dòng union type được cập nhật. Thuần comment, không ảnh hưởng `tsc` hay runtime, nhưng lần regen sau sẽ sinh thêm ~3 dòng drift.
5. Hằng `STATUS_CHANGED_EVENT` vẫn được `application/services` import từ `apps.orders.subscribers` — chiều phụ thuộc ngược với clean architecture. Không có bug (module không side-effect lúc import) và `.importlinter` chỉ ràng buộc giữa các app nên lint không bắt. Nhà đúng là `contracts/` hoặc `domain/`; để MR riêng.
6. `productsQuery`/`productOptions` bị lặp ~15 dòng giữa `ContactsPage.tsx` và `DispatchHistoryPage.tsx` — đã là lần thứ hai, đủ để tách `useProductOptions()` ở lần chạm tiếp theo.

**Khuyến nghị trước khi mở cho toàn bộ user:** chạy `EXPLAIN (ANALYZE, BUFFERS)` trên prod cho bộ lọc sản phẩm với một SKU bán chạy. Pattern giống `repositories.py:604-612` nên rủi ro thấp, nhưng chưa có số đo thật, mà repo này có tiền sử sự cố hiệu năng đúng ở `order_items` (~855k dòng).
