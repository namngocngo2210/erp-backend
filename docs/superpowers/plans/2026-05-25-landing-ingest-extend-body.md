# Landing Ingest — Extend Body Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mở rộng API landing ingest (`POST /api/orders/ingest`) để nhận thêm `quantity`, `total_amount`, và `channel_id` dạng text (vd `"Facebook Thành"`), giữ tương thích ngược với `channel_id` int.

**Architecture:** Sửa 3 lớp theo luồng hiện có — Serializer (validate/nhận field), View (resolve channel int|text, reject nếu không map được, chuẩn hoá `payload["channel_id"]` về int), Service (`create_order_from_landing` honor `quantity`/`total_amount` tường minh). `total_amount` trong body thắng tổng từ item.

**Tech Stack:** Django + DRF + PostgreSQL. Test chạy qua Docker (`manage.py test`).

---

## Bối cảnh quan trọng (đọc trước khi code)

- Channel model nằm ở `apps.channels_core.models.Channel` (có `code` unique, `name`, `owner`, `deleted_at`).
- Helper map text→code: [`_normalize_channel_code_or_original`](../../../backend/apps/orders/import_excel.py) trong `apps.orders.import_excel` (vd `"Facebook Thành"` → `"facebook-thanh"`).
- `Order.channel` là `ForeignKey(..., on_delete=PROTECT)` **không nullable** → channel vẫn bắt buộc.
- **2 test ingest hiện ĐANG ĐỎ** vì viết trước khi `channel_id` thành bắt buộc (không gửi channel → 400):
  - `apps/orders/tests/test_ingest_api.py::LandingIngestApiTests::test_ingest_accepts_new_payload_fields` (kỳ vọng 200)
  - `apps/orders/tests/test_order_items_and_products.py::OrderItemsAndProductsApiTests::test_ingest_maps_product_by_sku` (kỳ vọng 202 — sai, success thực tế là **200**)
  Plan này sửa luôn 2 test đó cho khớp design.
- Endpoint success trả **HTTP 200** (`order_ingest_product_dashboard_view.ingest_landing_order`).

## Lệnh chạy test (đã verify)

Postgres + Redis cần chạy trước (một lần):
```bash
docker compose -f docker-compose.local.yml up -d postgres redis
```
Chạy test (lặp lại cho mỗi task):
```bash
docker compose -f docker-compose.local.yml run --rm --no-deps web \
  sh -c "mkdir -p /tmp/prom_multiproc && python manage.py test <test_label> -v2"
```

## File Structure

- **Modify** `backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py` — `LandingOrderSerializer`: field `channel_id` nhận int|text; thêm `quantity`, `total_amount`; bỏ `validate_channel_id`.
- **Modify** `backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py` — thêm `_resolve_channel_ref`; dùng trong `ingest_landing_order`; chuẩn hoá `payload["channel_id"] = channel.id`.
- **Modify** `backend/apps/orders/application/services/order_service_commands.py` — `create_order_from_landing`: honor `quantity` & `total_amount` tường minh.
- **Modify (test)** `backend/apps/orders/tests/test_ingest_api.py` — sửa test stale + thêm case mới.
- **Modify (test)** `backend/apps/orders/tests/test_order_items_and_products.py` — sửa test stale `test_ingest_maps_product_by_sku`.

---

## Task 1: Serializer nhận channel text + quantity + total_amount

**Files:**
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py`
- Test: `backend/apps/orders/tests/test_ingest_api.py`

- [ ] **Step 1: Viết test serializer (fail trước)**

Thêm vào cuối `backend/apps/orders/tests/test_ingest_api.py`:

```python
from apps.orders.serializers import LandingOrderSerializer
from apps.channels_core.models import Channel
from decimal import Decimal


class LandingOrderSerializerFieldTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="facebook-thanh", name="Facebook Thành")

    def test_accepts_text_channel_and_new_fields(self):
        serializer = LandingOrderSerializer(data={
            "channel_id": "Facebook Thành",
            "phone": "0909123456",
            "quantity": 3,
            "total_amount": "350000",
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        data = serializer.validated_data
        self.assertEqual(data["channel_id"], "Facebook Thành")
        self.assertEqual(data["quantity"], 3)
        self.assertEqual(data["total_amount"], Decimal("350000"))

    def test_accepts_int_channel_backward_compat(self):
        serializer = LandingOrderSerializer(data={"channel_id": self.channel.id, "phone": "0909123456"})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["channel_id"], self.channel.id)

    def test_rejects_empty_channel(self):
        serializer = LandingOrderSerializer(data={"channel_id": "  ", "phone": "0909123456"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("channel_id", serializer.errors)
```

- [ ] **Step 2: Chạy test → fail**

Run:
```bash
docker compose -f docker-compose.local.yml run --rm --no-deps web \
  sh -c "mkdir -p /tmp/prom_multiproc && python manage.py test apps.orders.tests.test_ingest_api.LandingOrderSerializerFieldTests -v2"
```
Expected: FAIL — `total_amount`/`quantity` không có trong validated_data, hoặc channel text bị reject (IntegerField).

- [ ] **Step 3: Thêm custom field `_ChannelRefField`**

Trong `kiotviet_landing_serializers.py`, ngay sau class `_ProductIdField` (kết thúc ~dòng 143), thêm:

```python
class _ChannelRefField(serializers.Field):
    """Accepts int (Channel.id) hoặc chuỗi text (tên/mã kênh).

    Giữ nguyên kiểu JSON để view phân biệt resolve theo id vs theo code.
    """

    def to_internal_value(self, data):
        if data is None or isinstance(data, bool):
            raise serializers.ValidationError("Kênh bán không hợp lệ.")
        if isinstance(data, int):
            return data
        if isinstance(data, str):
            stripped = data.strip()
            if not stripped:
                raise serializers.ValidationError("Kênh bán không được để trống.")
            return stripped
        raise serializers.ValidationError("Kênh bán phải là số ID hoặc tên kênh.")

    def to_representation(self, value):
        return value
```

- [ ] **Step 4: Đổi field `channel_id` + thêm `quantity`, `total_amount`, bỏ `validate_channel_id`**

Trong `LandingOrderSerializer`, đổi dòng:
```python
    channel_id = serializers.IntegerField()
```
thành:
```python
    channel_id = _ChannelRefField()
    quantity = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    total_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True, min_value=0
    )
```

Xoá nguyên method:
```python
    def validate_channel_id(self, value: int) -> int:
        if not Channel.objects.filter(id=value, deleted_at__isnull=True).exists():
            raise serializers.ValidationError("Kênh bán không tồn tại.")
        return value
```
(Việc kiểm tra tồn tại chuyển sang view.)

- [ ] **Step 5: Chạy test → pass**

Run: lệnh ở Step 2.
Expected: PASS (3 test).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py backend/apps/orders/tests/test_ingest_api.py
git commit -m "feat: landing serializer accepts channel text, quantity, total_amount"
```

---

## Task 2: View resolve channel (int|text), reject nếu không map được

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py`
- Test: `backend/apps/orders/tests/test_ingest_api.py`

- [ ] **Step 1: Viết integration test (fail trước)**

Thêm vào `test_ingest_api.py`. Lưu ý: tái dùng pattern mock `dispatch` chạy task inline như test hiện có.

```python
class LandingIngestChannelResolveTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(
            code="facebook-thanh", name="Facebook Thành", owner="Facebook Thành"
        )

    def _post(self, payload):
        headers = {}
        if settings.ORDERS_INGEST_API_TOKEN:
            headers["HTTP_X_API_TOKEN"] = settings.ORDERS_INGEST_API_TOKEN

        def _run_task(_task_name, **kwargs):
            process_landing_ingest(payload=kwargs["payload"], trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        with patch(
            "apps.orders.transport.views.order_views_impl.order_ingest_product_dashboard_view.dispatch",
            side_effect=_run_task,
        ):
            return self.client.post("/api/orders/ingest", data=payload, format="json", **headers)

    def test_resolves_text_channel(self):
        resp = self._post({"channel_id": "Facebook Thành", "phone": "0909123456", "address": "1 Q1"})
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.latest("id")
        self.assertEqual(order.channel_id, self.channel.id)

    def test_resolves_int_channel_backward_compat(self):
        resp = self._post({"channel_id": self.channel.id, "phone": "0909123457", "address": "1 Q1"})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Order.objects.latest("id").channel_id, self.channel.id)

    def test_rejects_unmappable_channel(self):
        before = Order.objects.count()
        resp = self._post({"channel_id": "Kênh Không Tồn Tại", "phone": "0909123458", "address": "1 Q1"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Order.objects.count(), before)
```

- [ ] **Step 2: Chạy test → fail**

Run:
```bash
docker compose -f docker-compose.local.yml run --rm --no-deps web \
  sh -c "mkdir -p /tmp/prom_multiproc && python manage.py test apps.orders.tests.test_ingest_api.LandingIngestChannelResolveTests -v2"
```
Expected: FAIL — text channel không resolve (view parse int → None → 400) hoặc service raise.

- [ ] **Step 3: Thêm helper `_resolve_channel_ref` + import**

Trong `order_ingest_product_dashboard_view.py`, thêm import (cạnh import `build_landing_payload_from_fields`, ~dòng 18):
```python
from apps.orders.import_excel import _normalize_channel_code_or_original
```

Thêm helper ngay sau `_resolve_landing_product_ids` (~dòng 83):
```python
def _resolve_channel_ref(raw):
    """Resolve tham chiếu kênh (int id hoặc text) → Channel.

    int / chuỗi toàn số → tra theo id; text khác → normalize sang code rồi tra theo code.
    Trả None nếu không tìm thấy.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return Channel.objects.filter(id=raw, deleted_at__isnull=True).first()
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit():
        return Channel.objects.filter(id=int(text), deleted_at__isnull=True).first()
    code = _normalize_channel_code_or_original(text)
    return Channel.objects.filter(code=code, deleted_at__isnull=True).first()
```

- [ ] **Step 4: Thay block resolve channel trong `ingest_landing_order`**

Tìm block hiện tại (sau khi `payload = serializer.validated_data`, ~dòng 151-168):
```python
    channel_id_raw = payload.get("channel_id")
    try:
        channel_pk = int(channel_id_raw) if channel_id_raw is not None else None
    except (TypeError, ValueError):
        channel_pk = None
    channel = (
        Channel.objects.filter(id=channel_pk, deleted_at__isnull=True).first()
        if channel_pk
        else None
    )
    if channel is None:
        trace.status = LandingIngestTraceStatus.VALIDATION_FAILED
        trace.error_message = f"Kênh bán không tồn tại: channel_id={channel_id_raw}"[:4000]
        trace.save(update_fields=["status", "error_message", "updated_at"])
        return Response(
            {"detail": f"Kênh bán không tồn tại: channel_id={channel_id_raw}"},
            status=status.HTTP_400_BAD_REQUEST,
        )
```
Thay bằng:
```python
    channel_id_raw = payload.get("channel_id")
    channel = _resolve_channel_ref(channel_id_raw)
    if channel is None:
        trace.status = LandingIngestTraceStatus.VALIDATION_FAILED
        trace.error_message = f"Kênh bán không tồn tại: channel_id={channel_id_raw}"[:4000]
        trace.save(update_fields=["status", "error_message", "updated_at"])
        return Response(
            {"detail": f"Kênh bán không tồn tại: channel_id={channel_id_raw}"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    # Chuẩn hoá về id để service downstream dùng nguyên logic resolve theo id.
    payload["channel_id"] = channel.id
```

- [ ] **Step 5: Chạy test → pass**

Run: lệnh ở Step 2.
Expected: PASS (3 test).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py backend/apps/orders/tests/test_ingest_api.py
git commit -m "feat: ingest view resolves channel by id or text, rejects unmappable"
```

---

## Task 3: Service honor quantity & total_amount tường minh

**Files:**
- Modify: `backend/apps/orders/application/services/order_service_commands.py`
- Test: `backend/apps/orders/tests/test_ingest_api.py`

- [ ] **Step 1: Viết integration test (fail trước)**

Thêm vào `test_ingest_api.py`:

```python
from apps.orders.models import Product


class LandingIngestQuantityTotalTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(code="facebook-thanh", name="Facebook Thành", owner="FB")
        self.product = Product.objects.create(name="Bồn ngâm chân", sku="BNC02", price="1290000")

    def _post(self, payload):
        headers = {}
        if settings.ORDERS_INGEST_API_TOKEN:
            headers["HTTP_X_API_TOKEN"] = settings.ORDERS_INGEST_API_TOKEN

        def _run_task(_task_name, **kwargs):
            process_landing_ingest(payload=kwargs["payload"], trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        with patch(
            "apps.orders.transport.views.order_views_impl.order_ingest_product_dashboard_view.dispatch",
            side_effect=_run_task,
        ):
            return self.client.post("/api/orders/ingest", data=payload, format="json", **headers)

    def test_explicit_quantity_drives_item_qty(self):
        resp = self._post({
            "channel_id": "Facebook Thành", "phone": "0909123456", "address": "1 Q1",
            "product_id": "BNC02", "quantity": 4,
        })
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.latest("id")
        self.assertEqual(order.quantity, 4)
        item = order.items.first()
        self.assertIsNotNone(item)
        self.assertEqual(item.quantity, 4)

    def test_explicit_total_amount_overrides_item_sum(self):
        # SKU map ra catalog price 1.290.000 x4 = 5.160.000, nhưng body total_amount thắng.
        resp = self._post({
            "channel_id": "Facebook Thành", "phone": "0909123457", "address": "1 Q1",
            "product_id": "BNC02", "quantity": 4, "total_amount": "999000",
        })
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.latest("id")
        self.assertEqual(int(order.total_amount), 999000)
        self.assertEqual(order.items.count(), 1)

    def test_total_amount_without_item_match(self):
        resp = self._post({
            "channel_id": "Facebook Thành", "phone": "0909123458", "address": "1 Q1",
            "product_id": "SKU-KHONG-CO", "total_amount": "777000",
        })
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.latest("id")
        self.assertEqual(int(order.total_amount), 777000)
        self.assertEqual(order.items.count(), 0)
```

- [ ] **Step 2: Chạy test → fail**

Run:
```bash
docker compose -f docker-compose.local.yml run --rm --no-deps web \
  sh -c "mkdir -p /tmp/prom_multiproc && python manage.py test apps.orders.tests.test_ingest_api.LandingIngestQuantityTotalTests -v2"
```
Expected: FAIL — `quantity` bị suy ra từ text (không phải 4); `total_amount` bị `_sync_order_totals_from_items` ghi đè bằng item-sum.

- [ ] **Step 3: Đọc field tường minh ở đầu `create_order_from_landing`**

Trong `order_service_commands.py`, đầu method (sau dòng 10 `derived_total_amount = derive_total_amount(product_raw)`), thêm:
```python
        explicit_quantity = payload.get("quantity")
        explicit_total_amount = payload.get("total_amount")
```

- [ ] **Step 4: quantity tường minh override**

Đổi dòng 14:
```python
        landing_quantity = max(1, int(derived_quantity or 1))
```
thành:
```python
        if explicit_quantity is not None:
            landing_quantity = max(1, int(explicit_quantity))
        else:
            landing_quantity = max(1, int(derived_quantity or 1))
```

- [ ] **Step 5: total_amount tường minh thắng (và quantity khi không có item)**

Sau khối `if matched_items: ... else: ...` (kết thúc ~dòng 50, ngay trước `raw_address = payload.get("raw_address", "")`), thêm:
```python
        if explicit_total_amount is not None:
            derived_total_amount = explicit_total_amount
        if explicit_quantity is not None and not matched_items:
            derived_quantity = max(1, int(explicit_quantity))
```

Đổi khối sync tổng từ item (dòng 196-197):
```python
            OrderServiceCommands._sync_order_totals_from_items(order, grand_total)
            order.save(update_fields=["total_amount", "total_after_discount", "updated_at"])
```
thành:
```python
            sync_total = explicit_total_amount if explicit_total_amount is not None else grand_total
            OrderServiceCommands._sync_order_totals_from_items(order, sync_total)
            order.save(update_fields=["total_amount", "total_after_discount", "updated_at"])
```

- [ ] **Step 6: Chạy test → pass**

Run: lệnh ở Step 2.
Expected: PASS (3 test).

- [ ] **Step 7: Commit**

```bash
git add backend/apps/orders/application/services/order_service_commands.py backend/apps/orders/tests/test_ingest_api.py
git commit -m "feat: landing ingest honors explicit quantity and total_amount"
```

---

## Task 4: Sửa 2 test stale cho khớp design mới

**Files:**
- Modify: `backend/apps/orders/tests/test_ingest_api.py`
- Modify: `backend/apps/orders/tests/test_order_items_and_products.py`

- [ ] **Step 1: Sửa `test_ingest_accepts_new_payload_fields`**

Trong `test_ingest_api.py`, class `LandingIngestApiTests`: thêm tạo channel + gửi `channel_id`. Sửa `setUp` và payload:

```python
class LandingIngestApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(
            code="facebook-ads-team", name="Facebook Ads Team", owner="Facebook Ads Team"
        )

    def test_ingest_accepts_new_payload_fields(self):
        Product.objects.create(name="Bồn ngâm chân", sku="BNC02", price="1290000")
        payload = {
            "channel_id": "Facebook Ads Team",
            "name": "Nguyen Van A",
            "phone": "0909 123 456",
            "address": "1 Nguyen Trai, Q1",
            "product": "Mua 1 Bồn Ngâm Chân: 1.290.000 VNĐ Tặng Kèm 30 Gói Thảo Dược + Ổ Cắm Chống Giật",
            "link": "https://example.com/landing/san-pham-a",
        }
        ...  # giữ nguyên phần mock dispatch + post
        self.assertEqual(response.status_code, 200)
        ...  # giữ nguyên các assert khác
        self.assertEqual(order.channel_owner, payload.get("channel_owner", order.channel.name))
```

Lưu ý: bỏ field `channel_owner` khỏi payload (serializer không nhận); dòng assert cuối `order.channel_owner == order.channel.name` vẫn đúng vì `channel_owner = channel.owner` và channel.owner = "Facebook Ads Team" = channel.name. Sửa dòng 51 cũ `self.assertEqual(order.channel_owner, payload["channel_owner"])` thành `self.assertEqual(order.channel_owner, "Facebook Ads Team")`. Đảm bảo `import` đã có `Channel` và `Product` (đã thêm ở Task 1/3).

- [ ] **Step 2: Sửa `test_ingest_maps_product_by_sku`**

Trong `test_order_items_and_products.py`: `setUp` đã tạo `self.channel = Channel.objects.create(code="lp-item", name="Landing Item")`. Sửa payload gửi `channel_id` và status code 200:

```python
    def test_ingest_maps_product_by_sku(self):
        mapped_product = Product.objects.create(name="Bon ngam chan", sku="BNC02", price="1290000")
        payload = {
            "channel_id": self.channel.id,
            "name": "Nguyen Van A",
            "phone": "0909 123 456",
            "address": "1 Nguyen Trai, Q1",
            "product": "Mua 1 Bồn Ngâm Chân: 1.290.000 VNĐ Tặng Kèm 30 Gói Thảo Dược + Ổ Cắm Chống Giật",
            "link": "https://example.com/landing/san-pham-a",
        }
        headers = {}
        if settings.ORDERS_INGEST_API_TOKEN:
            headers["HTTP_X_API_TOKEN"] = settings.ORDERS_INGEST_API_TOKEN

        def _run_task(_task_name, **kwargs):
            from apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks import process_landing_ingest
            process_landing_ingest(payload=kwargs["payload"], trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        from types import SimpleNamespace
        from unittest.mock import patch
        with patch(
            "apps.orders.transport.views.order_views_impl.order_ingest_product_dashboard_view.dispatch",
            side_effect=_run_task,
        ):
            response = self.client.post("/api/orders/ingest", data=payload, format="json", **headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["accepted"])
        self.assertIn("task_id", response.data)
        order = Order.objects.latest("id")
        self.assertEqual(order.items.count(), 1)
        item = order.items.first()
        self.assertIsNotNone(item)
        self.assertEqual(item.product_id, mapped_product.id)
        self.assertEqual(item.sku, "BNC02")
```
Đưa `from types import SimpleNamespace` và `from unittest.mock import patch` lên đầu file (cùng các import hiện có) thay vì import trong hàm.

- [ ] **Step 3: Chạy 2 module test → pass**

Run:
```bash
docker compose -f docker-compose.local.yml run --rm --no-deps web \
  sh -c "mkdir -p /tmp/prom_multiproc && python manage.py test apps.orders.tests.test_ingest_api apps.orders.tests.test_order_items_and_products -v2"
```
Expected: PASS toàn bộ.

- [ ] **Step 4: Commit**

```bash
git add backend/apps/orders/tests/test_ingest_api.py backend/apps/orders/tests/test_order_items_and_products.py
git commit -m "test: update stale ingest tests to send channel and correct status"
```

---

## Task 5: Regression toàn app orders + lint

**Files:** none (verify)

- [ ] **Step 1: Chạy toàn bộ test app orders**

Run:
```bash
docker compose -f docker-compose.local.yml run --rm --no-deps web \
  sh -c "mkdir -p /tmp/prom_multiproc && python manage.py test apps.orders -v1"
```
Expected: PASS (không regression).

- [ ] **Step 2: Lint (ruff) các file đã sửa**

Run:
```bash
docker compose -f docker-compose.local.yml run --rm --no-deps web \
  sh -c "ruff check apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py apps/orders/application/services/order_service_commands.py"
```
Expected: no errors. Nếu ruff chưa cài trong image, bỏ qua bước này.

- [ ] **Step 3: Cập nhật spec — đánh dấu hoàn thành**

Thêm dòng trạng thái "Đã implement & test pass — <ngày>" vào cuối spec `docs/superpowers/specs/2026-05-25-landing-ingest-extend-body-design.md`, commit:
```bash
git add docs/superpowers/specs/2026-05-25-landing-ingest-extend-body-design.md
git commit -m "docs: mark landing ingest extend body as implemented"
```

---

## Self-Review

- **Spec coverage:** channel text→Task 1+2; channel int compat→Task 1+2; reject unmappable→Task 2; quantity→Task 3; total_amount body thắng→Task 3; total khi không có item→Task 3; trace VALIDATION_FAILED→Task 2 (giữ nguyên block trace). ✔
- **Type consistency:** `_resolve_channel_ref` nhận int|str|None; serializer trả int|str; `explicit_total_amount` là Decimal (DRF DecimalField) hợp với `_sync_order_totals_from_items(order, Decimal)`. ✔
- **Placeholder scan:** không có TBD; mọi step có code/lệnh cụ thể. ✔
