# Warranty Ingest API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a token-authenticated external endpoint `POST /api/warranty/ingest` that creates a `WarrantyClaim` for an existing order resolved strictly by `shipping_code`, mirroring the order-ingest API.

**Architecture:** A new service method on `WarrantyClaimService` resolves the order by `shipping_code` and creates the claim, raising typed exceptions for the three failure modes. A new transport view (separate file from the CRUD) handles token auth (shared `ORDERS_INGEST_API_TOKEN`), validates the payload with a dedicated serializer, and maps service exceptions to HTTP status codes. Synchronous; returns the created claim.

**Tech Stack:** Django 5 + DRF, PostgreSQL, pytest, ruff, drf-spectacular.

## Global Constraints

- Backend Python 3.12+, lint with `ruff` (`uv run --no-project --with ruff ruff check <files>`).
- Clean-architecture layering: transport → service → model. The view maps exceptions; the service holds resolution + creation logic.
- Endpoint `POST /api/warranty/ingest`, synchronous, returns HTTP **200** + the created claim on success.
- Auth: `AllowAny` + no auth class; token from header `X-Api-Token` or `Authorization: Bearer`, constant-time compared (`secrets.compare_digest`) to `settings.ORDERS_INGEST_API_TOKEN`. Token unconfigured + `IS_PRODUCTION` → 503; unconfigured + non-prod → lenient (allow); configured but wrong/missing → 401.
- Order resolution is STRICT by `shipping_code`: exactly one non-deleted order required. 0 → 400, >1 → 400.
- One claim per order: existing non-deleted claim → 409.
- `created_by = None` (external source). Keep `WarrantyClaim` model unchanged (OneToOne order required).
- Do NOT import internals of the `orders` app — replicate the ~8-line token helper inside `warranty`.
- Commit messages in English, short. Branch `feat/warranty-ingest-api` (already created).
- Tests run via docker image `desi-order-web-test` on network `desi-order_desi-net`; redis needs `-e REDIS_HOST=603f65f44977_desi-redis -e REDIS_PASSWORD=desi_order`.

---

## File Structure

**Create:**
- `backend/apps/warranty/ingest_views.py` — token helper + `warranty_claim_ingest` view
- `backend/apps/warranty/tests/test_warranty_ingest_service.py` — service-level tests
- `backend/apps/warranty/tests/test_warranty_ingest_api.py` — endpoint tests

**Modify:**
- `backend/apps/warranty/claim_service.py` — add 3 exception classes + `ingest_claim_by_shipping_code`
- `backend/apps/warranty/serializers.py` — add `WarrantyClaimIngestSerializer`
- `backend/apps/warranty/urls.py` — wire `ingest` route

---

### Task 1: Service layer — resolve-by-shipping-code + typed exceptions

**Files:**
- Modify: `backend/apps/warranty/claim_service.py`
- Test: `backend/apps/warranty/tests/test_warranty_ingest_service.py`

**Interfaces:**
- Consumes: `WarrantyClaim` model, `_EDITABLE_FIELDS` (already in this file), `apps.orders.models.Order`.
- Produces:
  - Exceptions `WarrantyOrderNotFound`, `WarrantyOrderAmbiguous`, `WarrantyClaimExists` (all subclass `Exception`).
  - `WarrantyClaimService.ingest_claim_by_shipping_code(shipping_code: str, data: dict) -> WarrantyClaim` — resolves the order strictly by `shipping_code` (non-deleted); raises `WarrantyOrderNotFound` (0 match), `WarrantyOrderAmbiguous` (>1 match), `WarrantyClaimExists` (order already has a non-deleted claim); else creates a `WarrantyClaim` with `created_by=None` and the editable fields present in `data`.

- [ ] **Step 1: Write the failing tests**

Create `backend/apps/warranty/tests/test_warranty_ingest_service.py`:

```python
from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order
from apps.warranty.claim_service import (
    WarrantyClaimExists,
    WarrantyClaimService,
    WarrantyOrderAmbiguous,
    WarrantyOrderNotFound,
)
from apps.warranty.models import WarrantyClaim, WarrantyClaimStatus

_seq = 0


def _make_order(shipping_code="VD123", **kwargs):
    global _seq
    _seq += 1
    channel = Channel.objects.create(code=f"ch-{_seq}", name=f"Channel {_seq}", is_active=True)
    customer = Customer.objects.create(full_name="KH", phone=f"090000{_seq:04d}")
    defaults = dict(
        name="Nguyen Van A",
        phone="0901234567",
        address="123 ABC",
        product="Áo",
        sku="SKU-1",
        quantity=1,
        shipping_code=shipping_code,
        order_code="OD1",
        channel=channel,
        customer=customer,
    )
    defaults.update(kwargs)
    return Order.objects.create(**defaults)


class IngestClaimServiceTests(TestCase):
    def test_creates_claim_for_resolved_order(self):
        order = _make_order(shipping_code="SHIP-OK")
        claim = WarrantyClaimService.ingest_claim_by_shipping_code(
            "SHIP-OK",
            {"status": WarrantyClaimStatus.PROCESSING, "contact_channel": "Shopee"},
        )
        self.assertEqual(claim.order_id, order.id)
        self.assertIsNone(claim.created_by)
        self.assertEqual(claim.status, WarrantyClaimStatus.PROCESSING)
        self.assertEqual(claim.contact_channel, "Shopee")

    def test_no_order_raises_not_found(self):
        with self.assertRaises(WarrantyOrderNotFound):
            WarrantyClaimService.ingest_claim_by_shipping_code("NOPE", {})

    def test_multiple_orders_raise_ambiguous(self):
        _make_order(shipping_code="DUP")
        _make_order(shipping_code="DUP")
        with self.assertRaises(WarrantyOrderAmbiguous):
            WarrantyClaimService.ingest_claim_by_shipping_code("DUP", {})

    def test_existing_claim_raises_exists(self):
        order = _make_order(shipping_code="HAS-CLAIM")
        WarrantyClaim.objects.create(order=order)
        with self.assertRaises(WarrantyClaimExists):
            WarrantyClaimService.ingest_claim_by_shipping_code("HAS-CLAIM", {})

    def test_soft_deleted_order_not_resolved(self):
        from django.utils import timezone

        o = _make_order(shipping_code="DEL")
        o.deleted_at = timezone.now()
        o.save(update_fields=["deleted_at"])
        with self.assertRaises(WarrantyOrderNotFound):
            WarrantyClaimService.ingest_claim_by_shipping_code("DEL", {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from repo root, Git Bash):
```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_desi-net \
  --env-file backend/.env -e POSTGRES_HOST=postgres -e REDIS_HOST=603f65f44977_desi-redis -e REDIS_PASSWORD=desi_order \
  -e DJANGO_SETTINGS_MODULE=config.settings -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w /app desi-order-web-test \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/warranty/tests/test_warranty_ingest_service.py -q --reuse-db'
```
Expected: FAIL — `ImportError: cannot import name 'WarrantyOrderNotFound'`. (If it errors `relation "warranty_claims" does not exist`, re-run with `--create-db` instead of `--reuse-db`.)

- [ ] **Step 3: Add exceptions + method to the service**

Edit `backend/apps/warranty/claim_service.py`. Add the exception classes after the imports (before `_EDITABLE_FIELDS`):

```python
class WarrantyOrderNotFound(Exception):
    """Không tìm thấy đơn theo mã vận đơn."""


class WarrantyOrderAmbiguous(Exception):
    """Nhiều đơn cùng mã vận đơn, không thể xác định."""


class WarrantyClaimExists(Exception):
    """Đơn đã có phiếu bảo hành."""
```

Then add this method to `WarrantyClaimService` (after `create_claim`):

```python
    @staticmethod
    def ingest_claim_by_shipping_code(shipping_code: str, data: dict) -> WarrantyClaim:
        qs = Order.objects.filter(shipping_code=shipping_code, deleted_at__isnull=True)
        count = qs.count()
        if count == 0:
            raise WarrantyOrderNotFound()
        if count > 1:
            raise WarrantyOrderAmbiguous()
        order = qs.first()
        if WarrantyClaim.objects.filter(order=order, deleted_at__isnull=True).exists():
            raise WarrantyClaimExists()
        fields = {k: data[k] for k in _EDITABLE_FIELDS if k in data}
        return WarrantyClaim.objects.create(order=order, created_by=None, **fields)
```

- [ ] **Step 4: Run tests to verify they pass**

Re-run the Step 2 command. Expected: PASS (5 passed).

- [ ] **Step 5: Lint + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/warranty/claim_service.py backend/apps/warranty/tests/test_warranty_ingest_service.py
git add backend/apps/warranty/claim_service.py backend/apps/warranty/tests/test_warranty_ingest_service.py
git commit -m "feat(warranty): add ingest claim service resolving order by shipping code"
```

---

### Task 2: Ingest serializer + token-authenticated view + URL

**Files:**
- Modify: `backend/apps/warranty/serializers.py`
- Create: `backend/apps/warranty/ingest_views.py`
- Modify: `backend/apps/warranty/urls.py`
- Test: `backend/apps/warranty/tests/test_warranty_ingest_api.py`

**Interfaces:**
- Consumes: `WarrantyClaimService.ingest_claim_by_shipping_code` + the 3 exceptions (Task 1); `WarrantyClaimSerializer` (existing); `WarrantyClaimStatus` (existing).
- Produces: `WarrantyClaimIngestSerializer` (validates `shipping_code` required + editable fields); endpoint `POST /api/warranty/ingest` → `warranty_claim_ingest` view; URL name `warranty-ingest`.

- [ ] **Step 1: Write the failing API tests**

Create `backend/apps/warranty/tests/test_warranty_ingest_api.py`:

```python
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order
from apps.warranty.models import WarrantyClaim, WarrantyClaimStatus

_seq = 0


def _make_order(shipping_code="VD123", **kwargs):
    global _seq
    _seq += 1
    channel = Channel.objects.create(code=f"ich-{_seq}", name=f"Channel {_seq}", is_active=True)
    customer = Customer.objects.create(full_name="KH", phone=f"091000{_seq:04d}")
    defaults = dict(
        name="Tran B",
        phone="0905550001",
        address="456 XYZ",
        product="Quần",
        sku="SKU-2",
        quantity=1,
        shipping_code=shipping_code,
        order_code="OD2",
        channel=channel,
        customer=customer,
    )
    defaults.update(kwargs)
    return Order.objects.create(**defaults)


TOKEN = "ingest-secret"


@override_settings(ORDERS_INGEST_API_TOKEN=TOKEN, IS_PRODUCTION=False)
class WarrantyIngestApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _post(self, body, token=TOKEN):
        headers = {"HTTP_X_API_TOKEN": token} if token is not None else {}
        return self.client.post("/api/warranty/ingest", body, format="json", **headers)

    def test_success_creates_claim(self):
        order = _make_order(shipping_code="SHIP-1")
        res = self._post(
            {
                "shipping_code": "SHIP-1",
                "status": int(WarrantyClaimStatus.PROCESSING),
                "contact_channel": "TikTok",
                "send_reason": "Lỗi",
            }
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["order"]["id"], order.id)
        self.assertEqual(res.data["contact_channel"], "TikTok")
        self.assertEqual(WarrantyClaim.objects.count(), 1)

    def test_missing_shipping_code_400(self):
        res = self._post({"contact_channel": "x"})
        self.assertEqual(res.status_code, 400)

    def test_order_not_found_400(self):
        res = self._post({"shipping_code": "NONE"})
        self.assertEqual(res.status_code, 400)

    def test_ambiguous_order_400(self):
        _make_order(shipping_code="AMB")
        _make_order(shipping_code="AMB")
        res = self._post({"shipping_code": "AMB"})
        self.assertEqual(res.status_code, 400)

    def test_duplicate_claim_409(self):
        order = _make_order(shipping_code="DUP1")
        WarrantyClaim.objects.create(order=order)
        res = self._post({"shipping_code": "DUP1"})
        self.assertEqual(res.status_code, 409)

    def test_invalid_token_401(self):
        _make_order(shipping_code="SHIP-2")
        res = self._post({"shipping_code": "SHIP-2"}, token="wrong")
        self.assertEqual(res.status_code, 401)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_desi-net \
  --env-file backend/.env -e POSTGRES_HOST=postgres -e REDIS_HOST=603f65f44977_desi-redis -e REDIS_PASSWORD=desi_order \
  -e DJANGO_SETTINGS_MODULE=config.settings -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w /app desi-order-web-test \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/warranty/tests/test_warranty_ingest_api.py -q --reuse-db'
```
Expected: FAIL — 404 (route not wired) on every test.

- [ ] **Step 3: Add the ingest serializer**

In `backend/apps/warranty/serializers.py`, after `WarrantyClaimWriteSerializer`, add:

```python
class WarrantyClaimIngestSerializer(serializers.ModelSerializer):
    shipping_code = serializers.CharField(write_only=True)

    class Meta:
        model = WarrantyClaim
        fields = (
            "shipping_code",
            "status",
            "contact_channel",
            "require_carrier_call",
            "social_nickname",
            "send_reason",
            "send_method",
            "classification",
            "recall_code",
            "return_status",
            "accounting_check",
            "accounting_note",
        )

    def validate_status(self, value):
        if value not in WarrantyClaimStatus.values:
            raise serializers.ValidationError("Trạng thái không hợp lệ.")
        return value

    def validate_shipping_code(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Thiếu mã vận đơn.")
        return value
```

(`WarrantyClaim` and `WarrantyClaimStatus` are already imported at the top of this file from Task 3 of the previous feature; verify the import line `from apps.warranty.models import WarrantyClaim, WarrantyClaimStatus` exists — it does.)

- [ ] **Step 4: Create the ingest view**

Create `backend/apps/warranty/ingest_views.py`:

```python
import secrets

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.warranty.claim_service import (
    WarrantyClaimExists,
    WarrantyClaimService,
    WarrantyOrderAmbiguous,
    WarrantyOrderNotFound,
)
from apps.warranty.serializers import WarrantyClaimIngestSerializer, WarrantyClaimSerializer


def _extract_ingest_api_token(request) -> str:
    token = (request.headers.get("X-Api-Token") or "").strip()
    if token:
        return token
    auth_header = (request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


# Ingest PUBLIC tạo phiếu bảo hành (token dùng chung ORDERS_INGEST_API_TOKEN, đồng bộ).
@extend_schema(exclude=True)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def warranty_claim_ingest(request):
    expected_token = getattr(settings, "ORDERS_INGEST_API_TOKEN", "")
    if not expected_token:
        if getattr(settings, "IS_PRODUCTION", False):
            return Response(
                {"detail": "Warranty ingest is not configured"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        # Non-production: lenient (cho dev chạy không cần token).
    else:
        provided = _extract_ingest_api_token(request)
        if not provided or not secrets.compare_digest(provided, expected_token):
            return Response(
                {"detail": "Invalid ingest API token."}, status=status.HTTP_401_UNAUTHORIZED
            )

    serializer = WarrantyClaimIngestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = dict(serializer.validated_data)
    shipping_code = data.pop("shipping_code")

    try:
        claim = WarrantyClaimService.ingest_claim_by_shipping_code(shipping_code, data)
    except WarrantyOrderNotFound:
        return Response(
            {"detail": "Không tìm thấy đơn theo mã vận đơn."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except WarrantyOrderAmbiguous:
        return Response(
            {"detail": "Nhiều đơn cùng mã vận đơn, không thể xác định."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except WarrantyClaimExists:
        return Response(
            {"detail": "Đơn hàng này đã có phiếu bảo hành."},
            status=status.HTTP_409_CONFLICT,
        )

    return Response(WarrantyClaimSerializer(claim).data, status=status.HTTP_200_OK)
```

- [ ] **Step 5: Wire the URL**

Edit `backend/apps/warranty/urls.py`. Add the import and the route (keep all existing routes):

```python
from .ingest_views import warranty_claim_ingest
```
and add to `urlpatterns` (after the public `path("", ...)` line, before/after the claims routes — order does not matter since `ingest` is a distinct static path):

```python
    path("ingest", warranty_claim_ingest, name="warranty-ingest"),
```

- [ ] **Step 6: Run tests to verify they pass**

Re-run the Step 2 command. Expected: PASS (6 passed).

- [ ] **Step 7: Lint + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/warranty/serializers.py backend/apps/warranty/ingest_views.py backend/apps/warranty/urls.py backend/apps/warranty/tests/test_warranty_ingest_api.py
git add backend/apps/warranty/serializers.py backend/apps/warranty/ingest_views.py backend/apps/warranty/urls.py backend/apps/warranty/tests/test_warranty_ingest_api.py
git commit -m "feat(warranty): add token-authenticated warranty ingest endpoint"
```

---

## Self-Review notes

- **Spec coverage:** endpoint + auth (Task 2 view) ✓; token shared `ORDERS_INGEST_API_TOKEN` + prod/non-prod behavior ✓; payload + serializer ✓; strict shipping_code resolve with 0→400 / >1→400 (Task 1 service + Task 2 mapping) ✓; duplicate→409 ✓; success→200 + claim ✓; created_by=None ✓; module independence (token helper replicated, not imported) ✓; tests for all status codes (service Task 1 + API Task 2) ✓.
- **No schema change** — model untouched, no migration. ✓
- **Type consistency:** exception names identical across service (Task 1) and view (Task 2): `WarrantyOrderNotFound`, `WarrantyOrderAmbiguous`, `WarrantyClaimExists`; method name `ingest_claim_by_shipping_code` consistent.
