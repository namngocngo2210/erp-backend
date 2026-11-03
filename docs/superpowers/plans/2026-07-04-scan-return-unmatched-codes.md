# Scan Return — Unmatched Shipping Codes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Khi quét mã vận đơn tạo phiếu trả hàng mà không tìm thấy đơn, vẫn ghi nhận mã đó thành phiếu trả nháp (đơn trống) để kho khớp đơn thủ công sau; quét theo dạng hàng loạt.

**Architecture:** Thêm `shipping_code` vào `ReturnNote`; nới validation cho phiếu chờ khớp (0 dòng + có mã); endpoint PATCH mới để gắn đơn vào phiếu nháp (BE tự nạp dòng hàng từ đơn); FE thêm dialog quét hàng loạt `ScanReturnDialog` (mirror `ScanDispatchDialog`) và dialog chi tiết `ReturnNoteDetailDialog` có khối "Khớp đơn".

**Tech Stack:** Django + DRF (backend), React + TypeScript + React Query + shadcn-style ui (frontend), pytest trong Docker, vitest.

**Spec:** `docs/superpowers/specs/2026-07-04-scan-return-unmatched-codes-design.md`

## Global Constraints

- Làm việc trong worktree `C:\Users\MYPC\Desktop\workspace\desi-order\.claude\worktrees\feat-scan-return-unmatched-codes`, nhánh `feat/scan-return-unmatched-codes`. KHÔNG tạo nhánh mới, KHÔNG cd sang repo gốc.
- Views mỏng — logic nghiệp vụ nằm trong `ReturnNoteService`; DB access không nằm trong view.
- Commit message tiếng Anh, ngắn gọn, KHÔNG kèm footer "Co-Authored-By".
- Backend tests chạy qua Docker (host Windows, Git Bash). Xác định stack đang chạy bằng `docker ps` trước; nếu stack là `docker-compose.yml` (containers `desi-order-postgres-1`/`desi-order-redis-1`) dùng lệnh:
  ```bash
  MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
    --env-file .env --env-file backend/.env \
    -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
    -v "$PWD/backend:/app" -w /app desi-order-web-test:latest pytest <path> -q
  ```
  Chạy từ root của worktree. Thêm `--create-db` ở lần chạy đầu sau khi thêm migration. Nếu test DB bị khoá ("being accessed by other users") thêm `-e POSTGRES_DB=desi_scanret`.
- KHÔNG BAO GIỜ dùng `cache.clear()` trong tests (Redis dùng chung — FLUSHDB thật).
- Ruff chạy trên host: `uv run --no-project --with ruff ruff check <files>` (uv tại `/c/Users/MYPC/.local/bin/uv`).
- Baseline FAIL đã biết (KHÔNG phải regression, đừng sửa): BE return-rate; FE OrderFormDialog + 2 test mobile-viewport.
- Frontend: React Query cho mutation/query mới; KHÔNG fetch-in-useEffect; KHÔNG `react-select`.

---

### Task 1: Model — `ReturnNote.shipping_code` + migration

**Files:**
- Modify: `backend/apps/inventory/models.py` (class `ReturnNote`, ~dòng 254-292)
- Create: `backend/apps/inventory/migrations/0011_returnnote_shipping_code.py`
- Modify: `backend/apps/inventory/transport/serializers/return_serializers.py` (class `ReturnNoteSerializer`)

**Interfaces:**
- Produces: field `ReturnNote.shipping_code: CharField(max_length=64, blank, default="")`, xuất hiện trong output của `ReturnNoteSerializer` với key `"shipping_code"`.

- [ ] **Step 1: Thêm field vào model**

Trong `backend/apps/inventory/models.py`, class `ReturnNote`, sau dòng `source_note = models.TextField(blank=True, default="")` thêm:

```python
    # Mã vận đơn đã quét khi tạo phiếu — dùng để khớp đơn thủ công về sau
    # với phiếu chưa có source_order (quét không tìm thấy đơn).
    shipping_code = models.CharField(max_length=64, blank=True, default="")
```

Trong `Meta.indexes` của `ReturnNote` thêm phần tử cuối:

```python
            models.Index(fields=["shipping_code"], name="returnnote_shipcode_idx"),
```

- [ ] **Step 2: Viết migration**

Tạo `backend/apps/inventory/migrations/0011_returnnote_shipping_code.py`:

```python
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0010_merge_20260703_1340"),
    ]

    operations = [
        migrations.AddField(
            model_name="returnnote",
            name="shipping_code",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddIndex(
            model_name="returnnote",
            index=models.Index(
                fields=["shipping_code"], name="returnnote_shipcode_idx"
            ),
        ),
    ]
```

- [ ] **Step 3: Verify không lệch state**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  python manage.py makemigrations --check --dry-run
```

Expected: `No changes detected` (exit 0).

- [ ] **Step 4: Thêm `shipping_code` vào output serializer**

Trong `return_serializers.py`, class `ReturnNoteSerializer.Meta.fields`, thêm `"shipping_code"` sau `"source_order"`:

```python
        fields = [
            "id", "code", "return_type", "status",
            "source_order", "shipping_code", "warehouse", "warehouse_name",
            "customer_name", "customer_phone", "source_note", "note",
            "created_at", "posted_at", "lines",
        ]
```

- [ ] **Step 5: Chạy test suite return hiện có (migration mới → `--create-db`)**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  pytest apps/inventory/tests/test_return_api.py -q --create-db
```

Expected: PASS toàn bộ.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/inventory/models.py backend/apps/inventory/migrations/0011_returnnote_shipping_code.py backend/apps/inventory/transport/serializers/return_serializers.py
git commit -m "feat(inventory): add shipping_code field to ReturnNote"
```

---

### Task 2: Tạo phiếu chờ khớp (0 dòng + mã) + guard trùng mã

**Files:**
- Modify: `backend/apps/inventory/transport/serializers/return_serializers.py` (class `CreateReturnNoteSerializer`)
- Modify: `backend/apps/inventory/application/services/return_note_service.py` (method `create_return`)
- Modify: `backend/apps/inventory/transport/views/return_views.py` (POST branch của `list_create_returns`)
- Create: `backend/apps/inventory/tests/test_return_unmatched.py`

**Interfaces:**
- Consumes: `ReturnNote.shipping_code` (Task 1).
- Produces: `ReturnNoteService.create_return(..., shipping_code: str = "")` — raise `ValueError` khi trùng mã với phiếu nháp chưa khớp khác; POST `/api/inventory/returns` chấp nhận `{"shipping_code": str, "lines": []}` khi không có `source_order_id`, và trả 400 kèm `{"detail": ...}` cho `ValueError`.

- [ ] **Step 1: Viết failing tests**

Tạo `backend/apps/inventory/tests/test_return_unmatched.py`:

```python
from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient

from apps.orders.models import Product
from apps.users.test_helpers import make_admin
from apps.warehouses.models import Warehouse


class UnmatchedReturnCreateTests(TestCase):
    def setUp(self):
        self.user = make_admin("admin_unmatched")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(code="WU1", name="Kho U")
        self.product = Product.objects.create(name="SP U", sku="UNM-1")

    def _create_unmatched(self, code="VD-LOST-001"):
        return self.client.post(
            "/api/inventory/returns",
            data={
                "return_type": "delivery_returned",
                "warehouse_id": self.wh.id,
                "shipping_code": code,
                "lines": [],
            },
            format="json",
        )

    def test_create_unmatched_with_code_and_no_lines_returns_201(self):
        resp = self._create_unmatched()
        assert resp.status_code == 201, resp.json()
        body = resp.json()
        assert body["status"] == "draft"
        assert body["shipping_code"] == "VD-LOST-001"
        assert body["source_order"] is None
        assert body["lines"] == []

    def test_empty_lines_without_code_still_400(self):
        resp = self.client.post(
            "/api/inventory/returns",
            data={
                "return_type": "delivery_returned",
                "warehouse_id": self.wh.id,
                "lines": [],
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_empty_lines_with_source_order_400(self):
        from apps.orders.models import Order

        order = Order.objects.create(order_code="ORD-U1", status=1)
        resp = self.client.post(
            "/api/inventory/returns",
            data={
                "return_type": "delivery_returned",
                "warehouse_id": self.wh.id,
                "shipping_code": "VD-HAS-ORDER",
                "source_order_id": order.id,
                "lines": [],
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_duplicate_unmatched_code_returns_400(self):
        resp1 = self._create_unmatched(code="VD-DUP-01")
        assert resp1.status_code == 201
        resp2 = self._create_unmatched(code="VD-DUP-01")
        assert resp2.status_code == 400
        assert resp1.json()["code"] in resp2.json()["detail"]

    def test_matched_note_with_lines_and_code_ok(self):
        resp = self.client.post(
            "/api/inventory/returns",
            data={
                "return_type": "delivery_returned",
                "warehouse_id": self.wh.id,
                "shipping_code": "VD-OK-01",
                "lines": [{"product_id": self.product.id, "quantity": 2}],
            },
            format="json",
        )
        assert resp.status_code == 201, resp.json()
        assert resp.json()["shipping_code"] == "VD-OK-01"
```

Lưu ý: nếu `Order.objects.create(order_code=..., status=1)` thiếu field bắt buộc, xem cách các test khác trong `backend/apps/orders/tests/` hoặc `backend/apps/inventory/tests/test_stock_sales_cycle.py` tạo Order và bắt chước.

- [ ] **Step 2: Chạy để thấy fail**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  pytest apps/inventory/tests/test_return_unmatched.py -q
```

Expected: FAIL (400 do serializer đang bắt buộc lines, thiếu shipping_code).

- [ ] **Step 3: Sửa `CreateReturnNoteSerializer`**

Thay class trong `return_serializers.py`:

```python
class CreateReturnNoteSerializer(serializers.Serializer):
    return_type = serializers.ChoiceField(
        choices=["delivery_returned", "external_return", "warranty", "defect"]
    )
    warehouse_id = serializers.IntegerField()
    source_order_id = serializers.IntegerField(required=False, allow_null=True)
    shipping_code = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=64
    )
    customer_name = serializers.CharField(required=False, allow_blank=True, default="")
    customer_phone = serializers.CharField(required=False, allow_blank=True, default="")
    source_note = serializers.CharField(required=False, allow_blank=True, default="")
    note = serializers.CharField(required=False, allow_blank=True, default="")
    lines = _ReturnLineInput(many=True, required=False, default=list)

    def validate(self, attrs):
        lines = attrs.get("lines") or []
        # Phiếu chờ khớp: có mã vận đơn + chưa gắn đơn → cho phép 0 dòng.
        unmatched = bool(attrs.get("shipping_code")) and not attrs.get("source_order_id")
        if not lines and not unmatched:
            raise serializers.ValidationError("Phiếu trả hàng phải có ít nhất 1 dòng.")
        product_ids = [line["product_id"] for line in lines]
        if len(product_ids) != len(set(product_ids)):
            raise serializers.ValidationError(
                "Mỗi sản phẩm chỉ được xuất hiện 1 dòng trên phiếu."
            )
        return attrs
```

- [ ] **Step 4: Sửa `ReturnNoteService.create_return`**

Trong `return_note_service.py`, thêm param + guard + lưu field (giữ nguyên retry code):

```python
    @staticmethod
    @transaction.atomic
    def create_return(
        *,
        return_type: str,
        warehouse_id: int,
        lines: list[dict],
        source_order_id: int | None = None,
        shipping_code: str = "",
        customer_name: str = "",
        customer_phone: str = "",
        source_note: str = "",
        note: str = "",
        user_id: int | None = None,
    ) -> ReturnNote:
        if shipping_code and source_order_id is None:
            dup = ReturnNote.objects.filter(
                shipping_code=shipping_code,
                source_order__isnull=True,
                status=ReturnStatus.DRAFT,
            ).first()
            if dup is not None:
                raise ValueError(
                    f"Mã {shipping_code} đã được ghi nhận ở phiếu {dup.code}."
                )
```

và trong `ReturnNote.objects.create(...)` thêm `shipping_code=shipping_code,` (sau `source_order_id=source_order_id,`).

- [ ] **Step 5: View POST truyền field mới + bắt ValueError**

Trong `return_views.py`, POST branch của `list_create_returns`, bọc lời gọi service:

```python
    data = serializer.validated_data
    try:
        rn = ReturnNoteService.create_return(
            return_type=data["return_type"],
            warehouse_id=data["warehouse_id"],
            lines=list(data["lines"]),
            source_order_id=data.get("source_order_id"),
            shipping_code=data.get("shipping_code", ""),
            customer_name=data.get("customer_name", ""),
            customer_phone=data.get("customer_phone", ""),
            source_note=data.get("source_note", ""),
            note=data.get("note", ""),
            user_id=request.user.id,
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
```

- [ ] **Step 6: Chạy tests pass**

Lệnh như Step 2. Expected: PASS cả 5 test + `pytest apps/inventory/tests/test_return_api.py -q` vẫn PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/inventory/transport/serializers/return_serializers.py backend/apps/inventory/application/services/return_note_service.py backend/apps/inventory/transport/views/return_views.py backend/apps/inventory/tests/test_return_unmatched.py
git commit -m "feat(inventory): allow unmatched return notes with scanned shipping code"
```

---

### Task 3: Chặn ghi sổ phiếu 0 dòng

**Files:**
- Modify: `backend/apps/inventory/application/services/return_note_service.py` (method `post_return`)
- Modify: `backend/apps/inventory/tests/test_return_unmatched.py` (thêm test)

**Interfaces:**
- Produces: `post_return` raise `ValueError("Phiếu chưa có dòng hàng — hãy khớp đơn trước khi ghi sổ.")` khi phiếu 0 dòng (view hiện có đã map ValueError → 400).

- [ ] **Step 1: Viết failing test** — thêm class vào `test_return_unmatched.py`:

```python
class UnmatchedReturnPostTests(TestCase):
    def setUp(self):
        self.user = make_admin("admin_unmatched_post")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(code="WU2", name="Kho U2")

    def test_post_zero_line_note_returns_400(self):
        resp = self.client.post(
            "/api/inventory/returns",
            data={
                "return_type": "delivery_returned",
                "warehouse_id": self.wh.id,
                "shipping_code": "VD-NOLINE-01",
                "lines": [],
            },
            format="json",
        )
        assert resp.status_code == 201
        rid = resp.json()["id"]
        post_resp = self.client.post(f"/api/inventory/returns/{rid}/post", format="json")
        assert post_resp.status_code == 400
        assert "dòng hàng" in post_resp.json()["detail"]
```

- [ ] **Step 2: Chạy để thấy fail** (lệnh pytest như Task 2). Expected: FAIL — hiện post trả 200 (ghi sổ rỗng).

- [ ] **Step 3: Thêm guard vào `post_return`** — trong `return_note_service.py`, sau check status DRAFT:

```python
        if not rn.lines.exists():
            raise ValueError("Phiếu chưa có dòng hàng — hãy khớp đơn trước khi ghi sổ.")
```

- [ ] **Step 4: Chạy tests pass** — cả file `test_return_unmatched.py` và `test_return_api.py`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory/application/services/return_note_service.py backend/apps/inventory/tests/test_return_unmatched.py
git commit -m "feat(inventory): block posting return notes with no lines"
```

---

### Task 4: PATCH phiếu nháp — khớp đơn thủ công (BE tự nạp dòng hàng)

**Files:**
- Modify: `backend/apps/inventory/transport/serializers/return_serializers.py` (thêm `UpdateReturnNoteSerializer`)
- Modify: `backend/apps/inventory/application/services/return_note_service.py` (thêm `update_draft`)
- Modify: `backend/apps/inventory/transport/views/return_views.py` (`return_detail` nhận thêm PATCH)
- Modify: `backend/apps/inventory/tests/test_return_unmatched.py` (thêm test)

**Interfaces:**
- Produces: `PATCH /api/inventory/returns/<id>` body `{source_order_id?, customer_name?, customer_phone?, source_note?, note?, lines?}` → 200 `ReturnNoteSerializer`; 400 khi không phải DRAFT / đơn không tồn tại; 404 khi không có phiếu. Khi PATCH gắn `source_order_id` mà payload KHÔNG gửi `lines` và phiếu đang 0 dòng → service tự nạp lines từ items của đơn (merge trùng product, bỏ item không có product_id) và điền tên/SĐT khách nếu đang trống.
- Produces: `ReturnNoteService.update_draft(*, return_note_id: int, data: dict, ) -> ReturnNote`.

- [ ] **Step 1: Viết failing tests** — thêm class vào `test_return_unmatched.py`:

```python
class UpdateDraftReturnTests(TestCase):
    def setUp(self):
        self.user = make_admin("admin_unmatched_patch")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(code="WU3", name="Kho U3")
        self.product = Product.objects.create(name="SP U3", sku="UNM-3")

    def _create_unmatched(self, code="VD-MATCH-01"):
        resp = self.client.post(
            "/api/inventory/returns",
            data={
                "return_type": "delivery_returned",
                "warehouse_id": self.wh.id,
                "shipping_code": code,
                "lines": [],
            },
            format="json",
        )
        assert resp.status_code == 201
        return resp.json()["id"]

    def _make_order_with_items(self):
        # Bắt chước cách test khác trong repo tạo Order + items
        # (xem backend/apps/inventory/tests/test_stock_sales_cycle.py).
        from apps.orders.models import Order, OrderItem

        order = Order.objects.create(
            order_code="ORD-M1", status=1, name="Khách M", phone="0900000001"
        )
        OrderItem.objects.create(
            order=order, product=self.product, product_name="SP U3",
            sku="UNM-3", quantity=2,
        )
        OrderItem.objects.create(
            order=order, product=self.product, product_name="SP U3",
            sku="UNM-3", quantity=3,
        )
        return order

    def test_patch_attach_order_autofills_lines_then_post_ok(self):
        rid = self._create_unmatched()
        order = self._make_order_with_items()

        resp = self.client.patch(
            f"/api/inventory/returns/{rid}",
            data={"source_order_id": order.id},
            format="json",
        )
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert body["source_order"] == order.id
        assert body["customer_name"] == "Khách M"
        # 2 items cùng product được merge thành 1 dòng qty 5
        assert len(body["lines"]) == 1
        assert body["lines"][0]["quantity"] == 5

        post_resp = self.client.post(f"/api/inventory/returns/{rid}/post", format="json")
        assert post_resp.status_code == 200, post_resp.json()

    def test_patch_with_explicit_lines_replaces(self):
        rid = self._create_unmatched(code="VD-MATCH-02")
        order = self._make_order_with_items()
        resp = self.client.patch(
            f"/api/inventory/returns/{rid}",
            data={
                "source_order_id": order.id,
                "lines": [{"product_id": self.product.id, "quantity": 1}],
            },
            format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["lines"][0]["quantity"] == 1

    def test_patch_nonexistent_order_400(self):
        rid = self._create_unmatched(code="VD-MATCH-03")
        resp = self.client.patch(
            f"/api/inventory/returns/{rid}",
            data={"source_order_id": 999999},
            format="json",
        )
        assert resp.status_code == 400

    def test_patch_posted_note_400(self):
        resp = self.client.post(
            "/api/inventory/returns",
            data={
                "return_type": "warranty",
                "warehouse_id": self.wh.id,
                "lines": [{"product_id": self.product.id, "quantity": 1}],
            },
            format="json",
        )
        rid = resp.json()["id"]
        assert self.client.post(f"/api/inventory/returns/{rid}/post").status_code == 200
        patch_resp = self.client.patch(
            f"/api/inventory/returns/{rid}", data={"note": "x"}, format="json"
        )
        assert patch_resp.status_code == 400

    def test_patch_not_found_404(self):
        resp = self.client.patch(
            "/api/inventory/returns/999999", data={"note": "x"}, format="json"
        )
        assert resp.status_code == 404

    def test_patch_without_returns_permission_403(self):
        # User thường không có section "returns" → 403. Xem cách các test RBAC
        # khác tạo user không quyền (grep make_user trong apps/users/test_helpers.py
        # hoặc bắt chước test 403 hiện có trong apps/inventory/tests/).
        from apps.users.test_helpers import make_user

        plain = make_user("plain_patch_user")
        client = APIClient()
        client.force_authenticate(plain)
        rid = self._create_unmatched(code="VD-RBAC-01")
        resp = client.patch(
            f"/api/inventory/returns/{rid}", data={"note": "x"}, format="json"
        )
        assert resp.status_code == 403
```

Nếu `make_user` không tồn tại trong `apps/users/test_helpers.py`, dùng đúng helper tạo-user-không-quyền mà các test 403 hiện có trong repo dùng (grep `403` trong `backend/apps/inventory/tests/`) — KHÔNG đổi assertion.

Nếu `Order`/`OrderItem` có field bắt buộc khác, chỉnh `_make_order_with_items` theo pattern test hiện có — KHÔNG đổi assertions.

- [ ] **Step 2: Chạy để thấy fail** — Expected: FAIL 405 (PATCH chưa được hỗ trợ).

- [ ] **Step 3: Thêm `UpdateReturnNoteSerializer`** vào `return_serializers.py`:

```python
class UpdateReturnNoteSerializer(serializers.Serializer):
    source_order_id = serializers.IntegerField(required=False, allow_null=True)
    customer_name = serializers.CharField(required=False, allow_blank=True)
    customer_phone = serializers.CharField(required=False, allow_blank=True)
    source_note = serializers.CharField(required=False, allow_blank=True)
    note = serializers.CharField(required=False, allow_blank=True)
    lines = _ReturnLineInput(many=True, required=False)

    def validate_lines(self, value):
        product_ids = [line["product_id"] for line in value]
        if len(product_ids) != len(set(product_ids)):
            raise serializers.ValidationError(
                "Mỗi sản phẩm chỉ được xuất hiện 1 dòng trên phiếu."
            )
        return value
```

- [ ] **Step 4: Thêm `ReturnNoteService.update_draft`** vào `return_note_service.py`:

```python
    @staticmethod
    @transaction.atomic
    def update_draft(*, return_note_id: int, data: dict) -> ReturnNote:
        """Sửa phiếu nháp; dùng để khớp đơn thủ công vào phiếu quét chưa khớp.

        Khi gắn ``source_order_id`` mà không gửi ``lines`` và phiếu đang 0
        dòng → tự nạp dòng hàng từ items của đơn (merge item trùng product,
        bỏ item không có product) và điền tên/SĐT khách nếu đang trống.
        """
        from apps.orders.models import Order  # local import: giữ ranh giới module

        rn = ReturnNote.objects.select_for_update().get(id=return_note_id)
        if rn.status != ReturnStatus.DRAFT:
            raise ValueError(f"Chỉ phiếu nháp mới sửa được (đang {rn.status}).")

        order = None
        if data.get("source_order_id"):
            order = (
                Order.objects.filter(
                    pk=data["source_order_id"], deleted_at__isnull=True
                )
                .prefetch_related("items")
                .first()
            )
            if order is None:
                raise ValueError("Đơn hàng không tồn tại.")

        for field in (
            "source_order_id", "customer_name", "customer_phone",
            "source_note", "note",
        ):
            if field in data:
                setattr(rn, field, data[field])

        if "lines" in data:
            rn.lines.all().delete()
            ReturnNoteLine.objects.bulk_create([
                ReturnNoteLine(
                    return_note=rn,
                    product_id=line["product_id"],
                    quantity=line["quantity"],
                    condition_note=line.get("condition_note", ""),
                )
                for line in data["lines"]
            ])
        elif order is not None and not rn.lines.exists():
            merged: dict[int, int] = defaultdict(int)
            for item in order.items.all():
                if item.product_id:
                    merged[item.product_id] += item.quantity
            ReturnNoteLine.objects.bulk_create([
                ReturnNoteLine(return_note=rn, product_id=pid, quantity=qty)
                for pid, qty in merged.items()
            ])
            if not rn.customer_name:
                rn.customer_name = order.name or ""
            if not rn.customer_phone:
                rn.customer_phone = order.phone or ""

        rn.save()
        return rn
```

(`defaultdict` đã được import sẵn ở đầu file.)

Lưu ý field khách của Order: view lookup dùng `o.name` / `o.phone` (`shipping_lookup_view.py:_serialize`) — giữ đúng như vậy.

- [ ] **Step 5: View — thêm PATCH vào `return_detail`** trong `return_views.py`:

Đổi decorator + thêm branch:

```python
@extend_schema(
    methods=["GET"],
    responses={
        200: ReturnNoteSerializer,
        404: OpenApiResponse(description="Không tìm thấy phiếu trả hàng"),
    },
)
@extend_schema(
    methods=["PATCH"],
    request=UpdateReturnNoteSerializer,
    responses={
        200: ReturnNoteSerializer,
        400: OpenApiResponse(description="Không sửa được ở trạng thái hiện tại"),
        404: OpenApiResponse(description="Không tìm thấy phiếu trả hàng"),
    },
)
@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated, require_section("returns")])
@never_cache
def return_detail(request, return_id):
    if request.method == "PATCH":
        serializer = UpdateReturnNoteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            rn = ReturnNoteService.update_draft(
                return_note_id=return_id, data=dict(serializer.validated_data)
            )
        except ReturnNote.DoesNotExist:
            return Response(
                {"detail": "Không tìm thấy phiếu trả hàng."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        rn = (
            ReturnNote.objects.prefetch_related("lines__product")
            .select_related("warehouse", "source_order")
            .get(pk=rn.pk)
        )
        return Response(ReturnNoteSerializer(rn).data, status=status.HTTP_200_OK)

    try:
        rn = (
            ReturnNote.objects.prefetch_related("lines__product")
            .select_related("warehouse", "source_order")
            .get(pk=return_id)
        )
    except ReturnNote.DoesNotExist:
        return Response(
            {"detail": "Không tìm thấy phiếu trả hàng."},
            status=status.HTTP_404_NOT_FOUND,
        )
    return Response(ReturnNoteSerializer(rn).data, status=status.HTTP_200_OK)
```

Nhớ import `UpdateReturnNoteSerializer` ở đầu file.

- [ ] **Step 6: Chạy tests pass** — cả `test_return_unmatched.py` + `test_return_api.py`. Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/inventory/transport/serializers/return_serializers.py backend/apps/inventory/application/services/return_note_service.py backend/apps/inventory/transport/views/return_views.py backend/apps/inventory/tests/test_return_unmatched.py
git commit -m "feat(inventory): PATCH draft return note to attach source order"
```

---

### Task 5: Ledger union — expose `shipping_code` + `source_order` cho dòng trả hàng

**Files:**
- Modify: `backend/apps/inventory/transport/views/ledger_views.py`
- Modify: `backend/apps/inventory/tests/test_return_unmatched.py` (thêm test)

**Interfaces:**
- Produces: mỗi item của `GET /api/inventory/ledger-documents` có thêm `"shipping_code": str` (`""` cho phiếu kho) và `"source_order": int | null` (null cho phiếu kho). FE dùng `kind === "return" && shipping_code && source_order == null` để hiện badge.

- [ ] **Step 1: Viết failing test** — thêm vào `test_return_unmatched.py`:

```python
class LedgerUnmatchedFieldsTests(TestCase):
    def setUp(self):
        self.user = make_admin("admin_unmatched_ledger")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(code="WU4", name="Kho U4")

    def test_ledger_return_row_has_shipping_code_and_source_order(self):
        resp = self.client.post(
            "/api/inventory/returns",
            data={
                "return_type": "delivery_returned",
                "warehouse_id": self.wh.id,
                "shipping_code": "VD-LEDGER-01",
                "lines": [],
            },
            format="json",
        )
        assert resp.status_code == 201

        ledger = self.client.get(
            "/api/inventory/ledger-documents", {"limit": 50, "offset": 0}
        )
        assert ledger.status_code == 200
        rows = [r for r in ledger.json()["items"] if r["kind"] == "return"]
        assert rows, ledger.json()
        row = rows[0]
        assert row["shipping_code"] == "VD-LEDGER-01"
        assert row["source_order"] is None
```

- [ ] **Step 2: Chạy để thấy fail** — Expected: FAIL KeyError `shipping_code`.

- [ ] **Step 3: Sửa `ledger_views.py`**

Mở rộng query resolve theo trang (đoạn `source_map`, ~dòng 146-153):

```python
    return_ids = [row["id"] for row in page if row["kind"] == "return"]
    source_map = {}
    return_extra: dict[int, dict] = {}
    if return_ids:
        for rid, ocode, cname, ship, oid in ReturnNote.objects.filter(
            id__in=return_ids
        ).values_list(
            "id", "source_order__order_code", "customer_name",
            "shipping_code", "source_order_id",
        ):
            label = " · ".join([x for x in [ocode, cname] if x])
            source_map[rid] = label
            return_extra[rid] = {"shipping_code": ship, "source_order": oid}
```

Trong dict `items.append({...})` thêm 2 key (sau `"source_label"`):

```python
            "shipping_code": (
                return_extra.get(row["id"], {}).get("shipping_code", "")
                if row["kind"] == "return" else ""
            ),
            "source_order": (
                return_extra.get(row["id"], {}).get("source_order")
                if row["kind"] == "return" else None
            ),
```

Trong `inline_serializer` "LedgerDocumentItem" thêm:

```python
                        "shipping_code": drf_serializers.CharField(allow_blank=True),
                        "source_order": drf_serializers.IntegerField(
                            allow_null=True, required=False
                        ),
```

- [ ] **Step 4: Chạy tests pass** — file unmatched + test ledger hiện có nếu tồn tại (`grep -rl ledger backend/apps/inventory/tests/`). Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory/transport/views/ledger_views.py backend/apps/inventory/tests/test_return_unmatched.py
git commit -m "feat(inventory): expose shipping_code and source_order in ledger documents"
```

---

### Task 6: OpenAPI regen + FE types/queries

**Files:**
- Modify: `backend/schema.yaml` (generated)
- Modify: `frontend/src/api/schema.d.ts` (generated)
- Modify: `frontend/src/features/inventory/returns-types.ts`
- Modify: `frontend/src/features/inventory/ledger-types.ts`
- Modify: `frontend/src/features/inventory/returns-queries.ts`

**Interfaces:**
- Consumes: API Tasks 2/4/5.
- Produces: `ReturnNoteRow.shipping_code: string`; `CreateReturnNotePayload.shipping_code?: string`; `UpdateReturnNotePayload`; `LedgerRow.shipping_code: string` + `LedgerRow.source_order: number | null`; hook `useUpdateReturn()` (mutationFn nhận `{id, payload}`).

- [ ] **Step 1: Regen schema**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  python manage.py spectacular --file schema.yaml
cd frontend && npm run gen:api && cd ..
```

Expected: `schema.yaml` + `src/api/schema.d.ts` đổi, chứa `UpdateReturnNote` và `shipping_code`.

- [ ] **Step 2: Cập nhật `returns-types.ts`**

Thêm vào `ReturnNoteRow` (sau `source_order`):

```ts
  shipping_code: string;
```

Thêm vào `CreateReturnNotePayload` (sau `source_order_id`):

```ts
  shipping_code?: string;
```

Thêm type mới cuối file:

```ts
export type UpdateReturnNotePayload = {
  source_order_id?: number | null;
  customer_name?: string;
  customer_phone?: string;
  source_note?: string;
  note?: string;
  lines?: { product_id: number; quantity: number; condition_note?: string }[];
};
```

- [ ] **Step 3: Cập nhật `ledger-types.ts`** — thêm vào `LedgerRow` (sau `source_label`):

```ts
  shipping_code: string;
  source_order: number | null;
```

- [ ] **Step 4: Thêm `useUpdateReturn` vào `returns-queries.ts`**

```ts
export function useUpdateReturn() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: UpdateReturnNotePayload }) =>
      fetchJson(`${RETURNS_BASE}/${id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }) as Promise<ReturnNoteRow>,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["inventory", "returns"] });
      void qc.invalidateQueries({ queryKey: ["inventory", "ledger"] });
    },
  });
}
```

Import `UpdateReturnNotePayload` từ `returns-types`.

- [ ] **Step 5: Verify**

```bash
cd frontend && npm run typecheck
```

Expected: PASS (0 lỗi).

- [ ] **Step 6: Commit**

```bash
git add backend/schema.yaml frontend/src/api/schema.d.ts frontend/src/features/inventory/returns-types.ts frontend/src/features/inventory/ledger-types.ts frontend/src/features/inventory/returns-queries.ts
git commit -m "feat(frontend): return note types and update mutation for unmatched codes"
```

---

### Task 7: FE helper `scan-return-rows.ts` (logic thuần + tests)

**Files:**
- Create: `frontend/src/features/inventory/scan/scan-return-rows.ts`
- Create: `frontend/src/features/inventory/scan/__tests__/scan-return-rows.test.ts`

**Interfaces:**
- Consumes: `LookupOrder`, `LookupItem` từ `./scan-types`; `CreateReturnNotePayload`, `ReturnType` từ `../returns-types`.
- Produces: `ScanReturnRow`, `addScanRow(rows, code, order) => {rows, duplicate}`, `buildReturnPayloads(rows, returnType, warehouseId) => CreateReturnNotePayload[]` — Task 8 dùng đúng các tên này.

- [ ] **Step 1: Viết failing tests** — `scan-return-rows.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import type { LookupOrder } from "../scan-types";
import { addScanRow, buildReturnPayloads } from "../scan-return-rows";
import type { ScanReturnRow } from "../scan-return-rows";

const order = (over: Partial<LookupOrder> = {}): LookupOrder => ({
  order_id: 1,
  order_code: "ORD-1",
  shipping_code: "VD-1",
  status: 2,
  status_label: "Đang giao",
  customer_name: "Khách A",
  customer_phone: "0900000000",
  fulfillment_warehouse_id: null,
  items: [
    { product_id: 10, sku: "SKU-10", name: "SP 10", quantity: 2 },
    { product_id: 10, sku: "SKU-10", name: "SP 10", quantity: 3 },
    { product_id: null as unknown as number, sku: "X", name: "No product", quantity: 1 },
  ],
  ...over,
});

describe("addScanRow", () => {
  it("thêm dòng khớp lên đầu danh sách", () => {
    const { rows, duplicate } = addScanRow([], "VD-1", order());
    expect(duplicate).toBe(false);
    expect(rows).toHaveLength(1);
    expect(rows[0].kind).toBe("matched");
  });

  it("thêm dòng không khớp (order null)", () => {
    const { rows } = addScanRow([], "VD-LOST", null);
    expect(rows[0]).toEqual({ kind: "unmatched", code: "VD-LOST" });
  });

  it("chặn trùng mã đã quét", () => {
    const first = addScanRow([], "VD-LOST", null).rows;
    const { rows, duplicate } = addScanRow(first, "VD-LOST", null);
    expect(duplicate).toBe(true);
    expect(rows).toHaveLength(1);
  });

  it("chặn trùng đơn đã quét bằng mã khác", () => {
    const first = addScanRow([], "VD-1", order()).rows;
    const { duplicate } = addScanRow(first, "VD-1-ALT", order());
    expect(duplicate).toBe(true);
  });
});

describe("buildReturnPayloads", () => {
  it("dòng khớp: có đơn + khách + lines merge trùng product, bỏ item thiếu product", () => {
    const rows: ScanReturnRow[] = [{ kind: "matched", code: "VD-1", order: order() }];
    const [p] = buildReturnPayloads(rows, "delivery_returned", 7);
    expect(p).toEqual({
      return_type: "delivery_returned",
      warehouse_id: 7,
      shipping_code: "VD-1",
      source_order_id: 1,
      customer_name: "Khách A",
      customer_phone: "0900000000",
      lines: [{ product_id: 10, quantity: 5 }],
    });
  });

  it("dòng không khớp: chỉ mã + lines rỗng", () => {
    const rows: ScanReturnRow[] = [{ kind: "unmatched", code: "VD-LOST" }];
    const [p] = buildReturnPayloads(rows, "delivery_returned", 7);
    expect(p).toEqual({
      return_type: "delivery_returned",
      warehouse_id: 7,
      shipping_code: "VD-LOST",
      lines: [],
    });
  });
});
```

- [ ] **Step 2: Chạy để thấy fail**

```bash
cd frontend && npx vitest run src/features/inventory/scan/__tests__/scan-return-rows.test.ts
```

Expected: FAIL (module chưa tồn tại).

- [ ] **Step 3: Implement `scan-return-rows.ts`**

```ts
import type { CreateReturnNotePayload, ReturnType } from "../returns-types";
import type { LookupOrder } from "./scan-types";

export type ScanReturnRow =
  | { kind: "matched"; code: string; order: LookupOrder }
  | { kind: "unmatched"; code: string };

/** Thêm mã vừa quét vào đầu danh sách; chặn trùng mã hoặc trùng đơn. */
export function addScanRow(
  rows: ScanReturnRow[],
  code: string,
  order: LookupOrder | null,
): { rows: ScanReturnRow[]; duplicate: boolean } {
  const duplicate = rows.some(
    (r) =>
      r.code === code ||
      (order !== null && r.kind === "matched" && r.order.order_id === order.order_id),
  );
  if (duplicate) return { rows, duplicate: true };
  const row: ScanReturnRow = order
    ? { kind: "matched", code, order }
    : { kind: "unmatched", code };
  return { rows: [row, ...rows], duplicate: false };
}

function mergedLines(order: LookupOrder): { product_id: number; quantity: number }[] {
  const byProduct = new Map<number, number>();
  for (const it of order.items) {
    if (!it.product_id) continue;
    byProduct.set(it.product_id, (byProduct.get(it.product_id) ?? 0) + it.quantity);
  }
  return [...byProduct.entries()].map(([product_id, quantity]) => ({
    product_id,
    quantity,
  }));
}

export function buildReturnPayloads(
  rows: ScanReturnRow[],
  returnType: ReturnType,
  warehouseId: number,
): CreateReturnNotePayload[] {
  return rows.map((r) =>
    r.kind === "matched"
      ? {
          return_type: returnType,
          warehouse_id: warehouseId,
          shipping_code: r.code,
          source_order_id: r.order.order_id,
          customer_name: r.order.customer_name,
          customer_phone: r.order.customer_phone,
          lines: mergedLines(r.order),
        }
      : {
          return_type: returnType,
          warehouse_id: warehouseId,
          shipping_code: r.code,
          lines: [],
        },
  );
}
```

- [ ] **Step 4: Chạy tests pass** — lệnh như Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/inventory/scan/scan-return-rows.ts frontend/src/features/inventory/scan/__tests__/scan-return-rows.test.ts
git commit -m "feat(frontend): scan return row helpers"
```

---

### Task 8: FE `ScanReturnDialog` + nút mở trên trang Phiếu kho

**Files:**
- Create: `frontend/src/features/inventory/scan/ScanReturnDialog.tsx`
- Modify: `frontend/src/features/inventory/pages/StockDocumentsPage.tsx`

**Interfaces:**
- Consumes: `addScanRow`, `buildReturnPayloads`, `ScanReturnRow` (Task 7); `useCreateReturn` (hiện có); `lookupByShippingCode`, `isMultiple` (`./scan-queries`); `ScanShipmentInput`, `PhoneScanButton purpose="return"`, `useScannerGun`, `ScannerGunBadge`, `beepSuccess`/`beepError` — cùng import path như `ScanDispatchDialog.tsx`.
- Produces: `<ScanReturnDialog open onOpenChange onDone? />`.

- [ ] **Step 1: Tạo `ScanReturnDialog.tsx`** (mirror `ScanDispatchDialog.tsx` — đọc file đó trước khi viết):

```tsx
import { useRef, useState } from "react";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormDialogShell } from "@/components/ui/form-dialog-content";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { fetchJson } from "@/services/http";
import type { WarehouseListResponse } from "@/lib/api/warehouses";
import { useCreateReturn } from "@/features/inventory/returns-queries";
import type { ReturnType } from "@/features/inventory/returns-types";

import { ScanShipmentInput } from "./ScanShipmentInput";
import type { ScanShipmentInputHandle } from "./ScanShipmentInput";
import { beepError, beepSuccess } from "./scan-sound";
import { isMultiple, lookupByShippingCode } from "./scan-queries";
import { addScanRow, buildReturnPayloads } from "./scan-return-rows";
import type { ScanReturnRow } from "./scan-return-rows";
import { PhoneScanButton } from "./phone/PhoneScanButton";
import { useScannerGun } from "./useScannerGun";
import { ScannerGunBadge } from "./ScannerGunBadge";

type RowResult = { ok: boolean; noteCode?: string; error?: string };

type Props = {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onDone?: () => void;
};

export function ScanReturnDialog({ open, onOpenChange, onDone }: Props) {
  const [returnType, setReturnType] = useState<ReturnType>("delivery_returned");
  const [warehouseId, setWarehouseId] = useState("");
  const [rows, setRows] = useState<ScanReturnRow[]>([]);
  const [results, setResults] = useState<Record<string, RowResult>>({});
  const [saving, setSaving] = useState(false);

  const scanRef = useRef<ScanShipmentInputHandle>(null);
  const inFlight = useRef<Set<string>>(new Set());
  const { active: gunActive } = useScannerGun({ enabled: open, onScan: handleScan });

  const { data: whData } = useQuery({
    queryKey: ["warehouses", "list"],
    queryFn: () =>
      fetchJson("/api/warehouses?limit=200&offset=0") as Promise<WarehouseListResponse>,
    staleTime: 60_000,
  });
  const warehouses = whData?.items ?? [];
  const createReturn = useCreateReturn();

  function pushRow(code: string, order: Parameters<typeof addScanRow>[2]) {
    setResults({});
    setRows((prev) => {
      const { rows: next, duplicate } = addScanRow(prev, code, order);
      if (duplicate) {
        beepError();
        toast.warning(`Mã ${code} (hoặc đơn tương ứng) đã quét rồi.`);
        return prev;
      }
      if (order === null) {
        beepError();
        toast.warning(`Không tìm thấy đơn cho mã ${code} — đã ghi nhận, khớp đơn sau.`);
      } else {
        beepSuccess();
      }
      return next;
    });
  }

  async function handleScan(code: string) {
    if (inFlight.current.has(code)) return;
    inFlight.current.add(code);
    try {
      const res = await lookupByShippingCode(code);
      if (isMultiple(res)) {
        toast.message(`Mã ${code} khớp ${res.orders.length} đơn — lấy đơn đầu:`, {
          description: res.orders.map((o) => o.order_code).join(", "),
        });
        pushRow(code, res.orders[0]);
        return;
      }
      pushRow(code, res);
    } catch {
      // 404 — đơn chưa có trong hệ thống: vẫn ghi nhận mã (dòng đỏ).
      pushRow(code, null);
    } finally {
      inFlight.current.delete(code);
      scanRef.current?.focus();
    }
  }

  function removeRow(code: string) {
    setRows((prev) => prev.filter((r) => r.code !== code));
  }

  async function handleConfirm() {
    if (!warehouseId) {
      toast.error("Vui lòng chọn kho nhận.");
      return;
    }
    if (rows.length === 0) {
      toast.error("Chưa quét mã nào.");
      return;
    }
    setSaving(true);
    const payloads = buildReturnPayloads(rows, returnType, Number(warehouseId));
    const next: Record<string, RowResult> = {};
    for (let i = 0; i < rows.length; i++) {
      try {
        const note = await createReturn.mutateAsync(payloads[i]);
        next[rows[i].code] = { ok: true, noteCode: note.code };
      } catch (err) {
        next[rows[i].code] = {
          ok: false,
          error: err instanceof Error ? err.message : "lỗi",
        };
      }
    }
    setSaving(false);
    setResults(next);
    const okCount = Object.values(next).filter((r) => r.ok).length;
    toast.success(`Đã tạo ${okCount}/${rows.length} phiếu trả nháp.`);
    onDone?.();
  }

  function reset() {
    setReturnType("delivery_returned");
    setWarehouseId("");
    setRows([]);
    setResults({});
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) reset();
        onOpenChange(o);
      }}
    >
      <FormDialogShell
        title="Quét trả hàng (hàng loạt)"
        footer={
          <>
            <Button
              variant="outline"
              onClick={() => {
                reset();
                onOpenChange(false);
              }}
            >
              Đóng
            </Button>
            <Button onClick={handleConfirm} disabled={saving}>
              {saving ? "Đang tạo..." : `Xác nhận (${rows.length})`}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-sm text-muted-foreground">Loại trả về</label>
              <Select
                value={returnType}
                onValueChange={(v) => {
                  setReturnType(v as ReturnType);
                  scanRef.current?.focus();
                }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="delivery_returned">Hàng hoàn</SelectItem>
                  <SelectItem value="external_return">Trả hàng ngoài</SelectItem>
                  <SelectItem value="warranty">Bảo hành</SelectItem>
                  <SelectItem value="defect">Đổi lỗi</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="mb-1 block text-sm text-muted-foreground">Kho nhận</label>
              <Select
                value={warehouseId}
                onValueChange={(v) => {
                  setWarehouseId(v);
                  scanRef.current?.focus();
                }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="Chọn kho nhận" />
                </SelectTrigger>
                <SelectContent>
                  {warehouses.map((w) => (
                    <SelectItem key={w.id} value={String(w.id)}>
                      {w.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div>
            <div className="mb-1 flex items-center justify-between gap-2">
              <label className="text-sm text-muted-foreground">Quét mã vận đơn</label>
              <ScannerGunBadge active={gunActive} />
            </div>
            <ScanShipmentInput ref={scanRef} onScan={handleScan} />
            <div className="mt-2">
              <PhoneScanButton purpose="return" onCode={handleScan} />
            </div>
          </div>

          <div className="rounded-lg border border-border">
            <table data-slot="table" className="w-full text-sm">
              <thead className="text-left text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">Mã vận đơn</th>
                  <th className="px-3 py-2">Mã đơn</th>
                  <th className="px-3 py-2">Khách</th>
                  <th className="px-3 py-2">Kết quả</th>
                  <th className="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && (
                  <tr>
                    <td className="px-3 py-3 text-muted-foreground" colSpan={5}>
                      Chưa quét mã nào.
                    </td>
                  </tr>
                )}
                {rows.map((r) => {
                  const res = results[r.code];
                  return (
                    <tr
                      key={r.code}
                      className={`border-t border-border transition-colors ${
                        r.kind === "unmatched"
                          ? "bg-red-50 hover:bg-red-100/60"
                          : "hover:bg-muted/50"
                      }`}
                    >
                      <td className="px-3 py-2 font-medium">
                        {r.code}
                        {r.kind === "unmatched" && (
                          <span className="ml-2 rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700">
                            Chưa khớp đơn
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        {r.kind === "matched" ? r.order.order_code : "—"}
                      </td>
                      <td className="px-3 py-2">
                        {r.kind === "matched" ? r.order.customer_name : "—"}
                      </td>
                      <td className="px-3 py-2">
                        {res && (
                          <span className={res.ok ? "text-green-600" : "text-red-600"}>
                            {res.ok ? `✓ ${res.noteCode}` : `✗ ${res.error}`}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 px-2"
                          onClick={() => removeRow(r.code)}
                          aria-label={`Xoá ${r.code}`}
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </FormDialogShell>
    </Dialog>
  );
}
```

Lưu ý: sau khi tạo xong (results set), user có thể bấm Đóng — reset() xoá list. Không tự đóng dialog để user còn đọc kết quả từng dòng.

- [ ] **Step 2: Wire vào `StockDocumentsPage.tsx`**

- Import: `import { ScanReturnDialog } from "@/features/inventory/scan/ScanReturnDialog";`
- State: `const [scanReturnOpen, setScanReturnOpen] = useState(false);`
- Trong `DropdownMenuContent` (menu "Tạo"), thêm sau item "Phiếu trả hàng":

```tsx
                <DropdownMenuItem onClick={() => setScanReturnOpen(true)}>
                  Quét trả hàng (hàng loạt)
                </DropdownMenuItem>
```

- Render cạnh `<CreateReturnDialog ...>`:

```tsx
      <ScanReturnDialog
        open={scanReturnOpen}
        onOpenChange={setScanReturnOpen}
        onDone={refreshLedger}
      />
```

- [ ] **Step 3: Verify**

```bash
cd frontend && npm run typecheck && npx eslint src/features/inventory/scan/ScanReturnDialog.tsx src/features/inventory/pages/StockDocumentsPage.tsx
```

Expected: PASS, 0 lỗi lint.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/inventory/scan/ScanReturnDialog.tsx frontend/src/features/inventory/pages/StockDocumentsPage.tsx
git commit -m "feat(frontend): batch scan return dialog with unmatched code capture"
```

---

### Task 9: FE `ReturnNoteDetailDialog` — xem phiếu + khớp đơn thủ công + badge

**Files:**
- Create: `frontend/src/features/inventory/components/ReturnNoteDetailDialog.tsx`
- Modify: `frontend/src/features/inventory/returns-queries.ts` (thêm `useReturnNote`)
- Modify: `frontend/src/features/inventory/pages/StockDocumentsPage.tsx`

**Interfaces:**
- Consumes: `useUpdateReturn` (Task 6), `lookupByShippingCode`/`isMultiple`, `ReturnNoteRow`, `LedgerRow.shipping_code`/`.source_order` (Task 5/6).
- Produces: `<ReturnNoteDetailDialog returnId={number|null} onOpenChange />`; hook `useReturnNote(id: number | null)`.

- [ ] **Step 1: Thêm `useReturnNote` vào `returns-queries.ts`**

```ts
export function useReturnNote(id: number | null) {
  return useQuery({
    queryKey: ["inventory", "returns", "detail", id],
    queryFn: () => fetchJson(`${RETURNS_BASE}/${id}`) as Promise<ReturnNoteRow>,
    enabled: id != null,
  });
}
```

(Import `useQuery` từ `@tanstack/react-query` nếu file chưa có.)

- [ ] **Step 2: Tạo `ReturnNoteDetailDialog.tsx`**

```tsx
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormDialogShell } from "@/components/ui/form-dialog-content";
import { Input } from "@/components/ui/input";
import { isMultiple, lookupByShippingCode } from "@/features/inventory/scan/scan-queries";
import { useReturnNote, useUpdateReturn } from "@/features/inventory/returns-queries";

const RETURN_TYPE_LABELS: Record<string, string> = {
  delivery_returned: "Hàng hoàn",
  external_return: "Trả hàng ngoài",
  warranty: "Bảo hành",
  defect: "Đổi lỗi",
};

type Props = {
  returnId: number | null;
  onOpenChange: (open: boolean) => void;
};

export function ReturnNoteDetailDialog({ returnId, onOpenChange }: Props) {
  const { data: note } = useReturnNote(returnId);
  const updateMutation = useUpdateReturn();
  const [manualOrderId, setManualOrderId] = useState("");
  const [relooking, setRelooking] = useState(false);

  const unmatched = !!note && !!note.shipping_code && note.source_order == null;
  const canMatch = unmatched && note.status === "draft";

  async function attachOrder(orderId: number) {
    if (returnId == null) return;
    try {
      const updated = await updateMutation.mutateAsync({
        id: returnId,
        payload: { source_order_id: orderId },
      });
      toast.success(`Đã khớp đơn vào phiếu ${updated.code}.`);
      setManualOrderId("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Khớp đơn thất bại.");
    }
  }

  async function handleRelookup() {
    if (!note?.shipping_code) return;
    setRelooking(true);
    try {
      const res = await lookupByShippingCode(note.shipping_code);
      const order = isMultiple(res) ? res.orders[0] : res;
      if (isMultiple(res)) {
        toast.message(`Mã khớp ${res.orders.length} đơn — lấy đơn đầu.`);
      }
      await attachOrder(order.order_id);
    } catch {
      toast.error(`Vẫn chưa tìm thấy đơn cho mã ${note.shipping_code}.`);
    } finally {
      setRelooking(false);
    }
  }

  function handleManualAttach() {
    const id = Number(manualOrderId);
    if (!Number.isInteger(id) || id <= 0) {
      toast.error("ID đơn không hợp lệ.");
      return;
    }
    void attachOrder(id);
  }

  return (
    <Dialog open={returnId != null} onOpenChange={onOpenChange}>
      <FormDialogShell
        title={note ? `Phiếu trả ${note.code}` : "Phiếu trả hàng"}
        footer={
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Đóng
          </Button>
        }
      >
        {!note ? (
          <p className="text-sm text-muted-foreground">Đang tải…</p>
        ) : (
          <div className="space-y-4 text-sm">
            <div className="grid grid-cols-2 gap-2">
              <p>
                <span className="text-muted-foreground">Loại: </span>
                {RETURN_TYPE_LABELS[note.return_type] ?? note.return_type}
              </p>
              <p>
                <span className="text-muted-foreground">Kho nhận: </span>
                {note.warehouse_name}
              </p>
              <p>
                <span className="text-muted-foreground">Mã vận đơn: </span>
                {note.shipping_code || "—"}
                {unmatched && (
                  <span className="ml-2 rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700">
                    Chưa khớp đơn
                  </span>
                )}
              </p>
              <p>
                <span className="text-muted-foreground">Đơn gốc: </span>
                {note.source_order ?? "—"}
              </p>
              <p>
                <span className="text-muted-foreground">Khách: </span>
                {note.customer_name || "—"}
              </p>
              <p>
                <span className="text-muted-foreground">SĐT: </span>
                {note.customer_phone || "—"}
              </p>
            </div>

            {canMatch && (
              <div className="space-y-2 rounded-lg border border-red-200 bg-red-50/50 p-3">
                <p className="font-medium text-red-700">Khớp đơn cho phiếu này</p>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={relooking || updateMutation.isPending}
                    onClick={handleRelookup}
                  >
                    {relooking ? "Đang tra..." : "Tra lại mã"}
                  </Button>
                  <span className="text-muted-foreground">hoặc</span>
                  <Input
                    className="w-32"
                    value={manualOrderId}
                    onChange={(e) => setManualOrderId(e.target.value)}
                    placeholder="ID đơn"
                    type="number"
                    min={1}
                  />
                  <Button
                    size="sm"
                    disabled={updateMutation.isPending || !manualOrderId.trim()}
                    onClick={handleManualAttach}
                  >
                    Gắn đơn
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Gắn đơn sẽ tự nạp dòng hàng từ đơn; sau đó ghi sổ như bình thường.
                </p>
              </div>
            )}

            <div>
              <p className="mb-1 text-muted-foreground">Dòng hàng ({note.lines.length})</p>
              {note.lines.length === 0 ? (
                <p className="rounded border border-dashed border-border px-3 py-3 text-center text-muted-foreground">
                  Chưa có dòng hàng — khớp đơn để nạp sản phẩm.
                </p>
              ) : (
                <table data-slot="table" className="w-full text-sm">
                  <thead className="text-left text-muted-foreground">
                    <tr>
                      <th className="px-2 py-1">SKU</th>
                      <th className="px-2 py-1">Tên</th>
                      <th className="px-2 py-1">SL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {note.lines.map((l) => (
                      <tr key={l.id} className="border-t border-border">
                        <td className="px-2 py-1 font-medium">{l.product_sku}</td>
                        <td className="px-2 py-1">{l.product_name}</td>
                        <td className="px-2 py-1">{l.quantity}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        )}
      </FormDialogShell>
    </Dialog>
  );
}
```

- [ ] **Step 3: Wire vào `StockDocumentsPage.tsx`**

- Import `ReturnNoteDetailDialog`.
- State: `const [returnDetailId, setReturnDetailId] = useState<number | null>(null);`
- Trong nhánh `r.kind === "return"` của cột Thao tác, thêm nút Xem TRƯỚC các nút hiện có (dùng icon `Eye` đã import):

```tsx
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 px-2 text-xs"
                            onClick={() => setReturnDetailId(r.id)}
                          >
                            <Eye className="mr-1 size-3.5" />
                            Xem
                          </Button>
```

- Disable nút "Ghi sổ" cho phiếu trả chưa khớp đơn (0 dòng chắc chắn — đồng bộ guard BE). Trong nhánh `r.kind === "return" && r.status === "draft"`, sửa nút Ghi sổ:

```tsx
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-7 px-2 text-xs text-green-700 border-green-300 hover:bg-green-50"
                              disabled={
                                postReturnMutation.isPending ||
                                (!!r.shipping_code && r.source_order == null)
                              }
                              title={
                                r.shipping_code && r.source_order == null
                                  ? "Phiếu chưa khớp đơn — mở Xem để gắn đơn trước"
                                  : undefined
                              }
                              onClick={() => handlePostReturn(r.id)}
                            >
                              Ghi sổ
                            </Button>
```

- Badge ở cột Mã (trong `<td className="px-3 py-2 font-medium">{r.code}</td>`):

```tsx
                  <td className="px-3 py-2 font-medium">
                    {r.code}
                    {r.kind === "return" && r.shipping_code && r.source_order == null && (
                      <span className="ml-2 rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700">
                        Chưa khớp đơn
                      </span>
                    )}
                  </td>
```

- Render dialog cuối trang:

```tsx
      <ReturnNoteDetailDialog
        returnId={returnDetailId}
        onOpenChange={(open) => {
          if (!open) {
            setReturnDetailId(null);
            refreshLedger();
          }
        }}
      />
```

- [ ] **Step 4: Verify**

```bash
cd frontend && npm run typecheck && npx eslint src/features/inventory/components/ReturnNoteDetailDialog.tsx src/features/inventory/pages/StockDocumentsPage.tsx src/features/inventory/returns-queries.ts && npx vitest run src/features/inventory
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/inventory/components/ReturnNoteDetailDialog.tsx frontend/src/features/inventory/returns-queries.ts frontend/src/features/inventory/pages/StockDocumentsPage.tsx
git commit -m "feat(frontend): return note detail dialog with manual order matching"
```

---

### Task 10: Verify toàn bộ + push + MR

**Files:** không sửa code mới (chỉ fix nếu verify fail).

- [ ] **Step 1: Backend full inventory suite + ruff**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  pytest apps/inventory -q
uv run --no-project --with ruff ruff check backend/apps/inventory
```

Expected: PASS / All checks passed. (Nhắc: baseline fail đã biết chỉ nằm ngoài `apps/inventory`.)

- [ ] **Step 2: Frontend full verify**

```bash
cd frontend && npm run typecheck && npm run lint && npx vitest run && npm run build
```

Expected: PASS (trừ 3 test fail baseline đã biết: OrderFormDialog + 2 mobile-viewport).

- [ ] **Step 3: Push + MR link**

```bash
git push -u origin feat/scan-return-unmatched-codes
```

Gửi link MR (GitLab in ra remote URL tạo MR sau khi push) cho user.
