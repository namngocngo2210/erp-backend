# Chặn quét xuất kho cho đơn đã hủy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Khi quét mã vận đơn để XUẤT giao, xác minh realtime với KiotViet rằng đơn chưa bị hủy, thay vì tin vào trạng thái DB (trễ tới 30 phút).

**Architecture:** Thêm service `InvoiceCancellationVerifier` trong `apps/kiotviet`, tái sử dụng `get_invoice_by_code()` (chính hàm webhook đang dùng để verify sự kiện hủy). Cắm verifier vào **ba chốt**: lúc quét (`lookup_by_shipping_code`), lúc ghi sổ (`OrderService.dispatch_orders_batch`), và lúc cron khớp mã pending (`PendingScanService._complete_dispatch`). Khi phát hiện KiotViet đã hủy mà DB chưa biết → ghi ngược vào DB ngay (self-heal). Luồng nhập hoàn KHÔNG verify và KHÔNG chặn đơn hủy.

**Tech Stack:** Django 5 + DRF, pytest, Redis (django.core.cache), React + TypeScript + vitest.

## Global Constraints

- Spec gốc: `docs/superpowers/specs/2026-07-11-scan-cancelled-realtime-verify-design.md`. Đọc trước khi bắt đầu.
- Branch: `feat/scan-cancel-realtime-verify`. **KHÔNG tạo branch mới.** Worktree: `C:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify`.
- Python 3.12, ruff. Lint: `uv run --no-project --with ruff ruff check <files>` chạy trên host.
- **Lệnh chạy test backend** (Git Bash, cwd = worktree root):

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/.env \
  --env-file c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -e POSTGRES_DB=desi_scanverify \
  -v "c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/backend:/app" -w /app \
  desi-order-web-test:latest python -m pytest <paths> -q
```

  Thêm `--create-db` ở lần chạy đầu tiên SAU khi thêm migration (Task 4).
- **KHÔNG BAO GIỜ gọi `cache.clear()` trong test** — backend cache là RedisCache dùng chung với dev/prod, `.clear()` = FLUSHDB. Dùng key unique per test (uuid) hoặc `cache.delete(key)`.
- Frontend: `cd frontend && npm run typecheck && npx vitest run <paths>`.
- Response API đổi shape → sau Task 2 phải chạy lại `npm run gen:api` trong `frontend/` để cập nhật `src/api/schema.d.ts`.
- Commit message tiếng Anh, ngắn gọn, **không** kèm dòng `Co-Authored-By`.
- Đơn hủy = `Order.status == 6` **hoặc** `Order.invoice_status == 2`. Dùng đúng định nghĩa này ở mọi chỗ mới.

---

## File Structure

**Tạo mới:**
- `backend/apps/kiotviet/application/services/cancellation_verifier.py` — service verify + self-heal + cache.
- `backend/apps/kiotviet/tests/test_cancellation_verifier.py`
- `backend/apps/orders/tests/test_dispatch_cancelled_guard.py`
- `backend/apps/inventory/tests/test_pending_scan_cancelled.py`
- `backend/apps/inventory/migrations/00XX_pendingscan_cancelled_status.py`

**Sửa:**
- `backend/config/settings_parts/base.py` — flag `SCAN_VERIFY_CANCEL_REALTIME`, timeout, TTL cache.
- `backend/apps/orders/transport/views/shipping_lookup_view.py` — param `mode`, field `invoice_status`, verify, 409/503.
- `backend/apps/orders/application/services/order_service_commands.py:489` — guard cứng trong `dispatch_orders_batch`.
- `backend/apps/inventory/models.py:393` — thêm `PendingScanStatus.CANCELLED`.
- `backend/apps/inventory/application/services/pending_scan_service.py:378` — guard trong `_complete_dispatch`, xử lý `_process_one`.
- `frontend/src/features/inventory/scan/scan-cancelled.ts` — thêm check `invoice_status`.
- `frontend/src/features/inventory/scan/scan-types.ts` — thêm `invoice_status`.
- `frontend/src/features/inventory/scan/scan-queries.ts` — param `mode`.
- `frontend/src/features/inventory/scan/ScanDispatchDialog.tsx` — xử lý 409/503.
- `frontend/src/features/inventory/scan/ScanReturnDialog.tsx` — gỡ chặn đơn hủy.
- `frontend/src/features/inventory/scan/PendingScansDialog.tsx` — badge trạng thái `cancelled`.

---

## Task 1: `InvoiceCancellationVerifier` + settings

**Files:**
- Create: `backend/apps/kiotviet/application/services/cancellation_verifier.py`
- Create: `backend/apps/kiotviet/tests/test_cancellation_verifier.py`
- Modify: `backend/config/settings_parts/base.py` (sau dòng 208, cạnh các setting `KIOTVIET_*`)

**Interfaces:**
- Consumes: `get_invoice_by_code(invoice_code, order, channel)` từ `apps.kiotviet.infrastructure.services.invoice_sync_queries` (raise Exception khi API lỗi/404).
- Produces:
  - `class CancelCheck(str, Enum)` với 3 giá trị: `OK`, `CANCELLED`, `UNVERIFIABLE`.
  - `class VerificationUnavailable(Exception)` — API KiotViet không trả lời được.
  - `InvoiceCancellationVerifier.verify_not_cancelled(order) -> CancelCheck` — raise `VerificationUnavailable` khi API lỗi.

- [ ] **Step 1: Thêm settings**

Thêm vào `backend/config/settings_parts/base.py` ngay sau dòng `KIOTVIET_TASK_RATE_LIMIT = ...` (dòng 208):

```python
# Quét xuất kho: xác minh realtime với KiotViet rằng đơn chưa bị hủy (DB sync trễ tới 30').
# Fail-closed: API lỗi → chặn quét. Tắt cờ này khi KiotViet sự cố kéo dài để kho không đứng hình.
SCAN_VERIFY_CANCEL_REALTIME = os.getenv("SCAN_VERIFY_CANCEL_REALTIME", "1") == "1"
SCAN_VERIFY_TIMEOUT_SECONDS = float(os.getenv("SCAN_VERIFY_TIMEOUT_SECONDS", "3"))
SCAN_VERIFY_CACHE_TTL_OK = int(os.getenv("SCAN_VERIFY_CACHE_TTL_OK", "30"))
SCAN_VERIFY_CACHE_TTL_CANCELLED = int(os.getenv("SCAN_VERIFY_CACHE_TTL_CANCELLED", "300"))
```

- [ ] **Step 2: Viết test thất bại**

Tạo `backend/apps/kiotviet/tests/test_cancellation_verifier.py`:

```python
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.test import override_settings

from apps.kiotviet.application.services.cancellation_verifier import (
    CancelCheck,
    InvoiceCancellationVerifier,
    VerificationUnavailable,
)
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order, OrderStatus

pytestmark = pytest.mark.django_db

_PATCH_TARGET = (
    "apps.kiotviet.application.services.cancellation_verifier.get_invoice_by_code"
)


def _channel() -> KiotVietChannel:
    return KiotVietChannel.objects.create(
        name=f"ch-{uuid.uuid4().hex[:8]}",
        client_id="cid",
        client_secret="secret",
        retailer="retailer",
    )


def _order(**kwargs) -> Order:
    defaults = {
        "order_code": f"ORD-{uuid.uuid4().hex[:10]}",
        "invoice_code": f"HD-{uuid.uuid4().hex[:10]}",
        "status": OrderStatus.IN_DELIVERY,
        "name": "KH",
        "phone": "0900000001",
    }
    defaults.update(kwargs)
    return Order.objects.create(**defaults)


def test_returns_ok_when_kiotviet_says_invoice_is_live():
    order = _order(kiotviet_channel=_channel())
    with patch(_PATCH_TARGET, return_value={"status": 1}) as api:
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.OK
    assert api.call_count == 1


def test_returns_cancelled_and_heals_db_when_kiotviet_says_status_2():
    order = _order(kiotviet_channel=_channel())
    with patch(_PATCH_TARGET, return_value={"status": 2}):
        assert (
            InvoiceCancellationVerifier.verify_not_cancelled(order)
            is CancelCheck.CANCELLED
        )
    order.refresh_from_db()
    assert int(order.status) == int(OrderStatus.CANCELLED)
    assert order.invoice_status == 2


def test_returns_unverifiable_when_order_has_no_kiotviet_channel():
    order = _order(kiotviet_channel=None)
    with patch(_PATCH_TARGET) as api:
        assert (
            InvoiceCancellationVerifier.verify_not_cancelled(order)
            is CancelCheck.UNVERIFIABLE
        )
    api.assert_not_called()


def test_returns_unverifiable_when_order_has_no_invoice_code():
    order = _order(kiotviet_channel=_channel(), invoice_code="")
    with patch(_PATCH_TARGET) as api:
        assert (
            InvoiceCancellationVerifier.verify_not_cancelled(order)
            is CancelCheck.UNVERIFIABLE
        )
    api.assert_not_called()


def test_raises_when_kiotviet_api_fails():
    order = _order(kiotviet_channel=_channel())
    with patch(_PATCH_TARGET, side_effect=Exception("boom")):
        with pytest.raises(VerificationUnavailable):
            InvoiceCancellationVerifier.verify_not_cancelled(order)


def test_api_error_is_not_cached_but_ok_result_is():
    order = _order(kiotviet_channel=_channel())
    with patch(_PATCH_TARGET, side_effect=Exception("boom")):
        with pytest.raises(VerificationUnavailable):
            InvoiceCancellationVerifier.verify_not_cancelled(order)
    # Lỗi không được cache → lần sau vẫn gọi API thật và trả OK.
    with patch(_PATCH_TARGET, return_value={"status": 1}) as api:
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.OK
        assert InvoiceCancellationVerifier.verify_not_cancelled(order) is CancelCheck.OK
    assert api.call_count == 1  # lần 2 lấy từ cache


@override_settings(SCAN_VERIFY_CANCEL_REALTIME=False)
def test_kill_switch_skips_api_entirely():
    order = _order(kiotviet_channel=_channel())
    with patch(_PATCH_TARGET) as api:
        assert (
            InvoiceCancellationVerifier.verify_not_cancelled(order)
            is CancelCheck.UNVERIFIABLE
        )
    api.assert_not_called()
```

**Lưu ý:** trước khi chạy, mở `backend/apps/kiotviet/models.py` xác nhận tên field bắt buộc của `KiotVietChannel` và `backend/apps/orders/models.py` cho `Order` — chỉnh `_channel()` / `_order()` cho khớp field thật (đây là helper của test, không phải hợp đồng API).

- [ ] **Step 3: Chạy test, xác nhận FAIL**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/.env \
  --env-file c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -e POSTGRES_DB=desi_scanverify \
  -v "c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/backend:/app" -w /app \
  desi-order-web-test:latest python -m pytest apps/kiotviet/tests/test_cancellation_verifier.py -q
```

Expected: FAIL — `ModuleNotFoundError: apps.kiotviet.application.services.cancellation_verifier`.

- [ ] **Step 4: Implement verifier**

Tạo `backend/apps/kiotviet/application/services/cancellation_verifier.py`:

```python
from __future__ import annotations

import logging
from enum import Enum

from django.conf import settings
from django.core.cache import cache

from apps.kiotviet.infrastructure.services.invoice_sync_queries import get_invoice_by_code

logger = logging.getLogger(__name__)

# KiotViet: invoice status 2 = đã hủy; delivery status 5/6 = trả hàng/hủy giao.
# Cùng bộ điều kiện webhook đang dùng (apps/kiotviet/webhook_impl.py:454).
_CANCELLED_INVOICE_STATUS = 2
_CANCELLED_DELIVERY_STATUSES = {5, 6}


class VerificationUnavailable(Exception):
    """Không hỏi được KiotViet (lỗi/timeout/rate-limit) → caller phải fail-closed."""


class CancelCheck(str, Enum):
    OK = "ok"
    CANCELLED = "cancelled"
    UNVERIFIABLE = "unverifiable"


def _cache_key(channel_id: int, invoice_code: str) -> str:
    return f"scan:cancelcheck:{channel_id}:{invoice_code}"


def _pick(payload: dict, keys: list[str]):
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _as_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _is_cancelled_payload(payload: dict) -> bool:
    invoice_status = _as_int(
        _pick(payload, ["status", "Status", "invoiceStatus", "InvoiceStatus"])
    )
    if invoice_status == _CANCELLED_INVOICE_STATUS:
        return True
    delivery = _pick(payload, ["invoiceDelivery", "InvoiceDelivery"]) or {}
    if isinstance(delivery, list):
        delivery = delivery[0] if delivery else {}
    if isinstance(delivery, dict):
        delivery_status = _as_int(_pick(delivery, ["status", "Status"]))
        if delivery_status in _CANCELLED_DELIVERY_STATUSES:
            return True
    return False


class InvoiceCancellationVerifier:
    """Hỏi KiotViet (nguồn sự thật) xem đơn có bị hủy chưa, thay vì tin DB (sync trễ)."""

    @staticmethod
    def verify_not_cancelled(order) -> CancelCheck:
        if not getattr(settings, "SCAN_VERIFY_CANCEL_REALTIME", True):
            logger.warning(
                "scan verify skipped: kill-switch off (order_id=%s)", order.id
            )
            return CancelCheck.UNVERIFIABLE

        channel = order.kiotviet_channel
        invoice_code = str(order.invoice_code or "").strip()
        if channel is None or not invoice_code:
            # Đơn không có hóa đơn KiotViet (đơn gửi ngoài / tự tạo) → DB là nguồn sự thật.
            return CancelCheck.UNVERIFIABLE

        key = _cache_key(channel.id, invoice_code)
        cached = cache.get(key)
        if cached is not None:
            return CancelCheck(cached)

        try:
            payload = get_invoice_by_code(invoice_code, order, channel)
        except Exception as exc:  # noqa: BLE001 — mọi lỗi API đều là fail-closed
            logger.warning(
                "scan verify unavailable (order_id=%s, invoice_code=%s): %s",
                order.id,
                invoice_code,
                exc,
            )
            raise VerificationUnavailable(str(exc)) from exc

        if not isinstance(payload, dict):
            raise VerificationUnavailable("KiotViet trả về payload không hợp lệ.")

        if _is_cancelled_payload(payload):
            InvoiceCancellationVerifier._heal_cancelled_order(order)
            cache.set(key, CancelCheck.CANCELLED.value, settings.SCAN_VERIFY_CACHE_TTL_CANCELLED)
            return CancelCheck.CANCELLED

        cache.set(key, CancelCheck.OK.value, settings.SCAN_VERIFY_CACHE_TTL_OK)
        return CancelCheck.OK

    @staticmethod
    def _heal_cancelled_order(order) -> None:
        """DB đang trễ so với KiotViet → ghi trạng thái hủy ngay, publish event cho FE."""
        from apps.orders.contracts import OrderEventContract
        from apps.orders.models import Order, OrderStatus
        from apps.orders.services import OrderService

        previous_status = int(order.status)
        if previous_status == int(OrderStatus.CANCELLED) and order.invoice_status == 2:
            return

        Order.objects.filter(id=order.id).update(
            status=OrderStatus.CANCELLED, invoice_status=2
        )
        order.status = OrderStatus.CANCELLED
        order.invoice_status = 2
        logger.info(
            "scan verify healed cancelled order (order_id=%s, from_status=%s)",
            order.id,
            previous_status,
        )
        OrderService.push_event(
            OrderEventContract.build_order_updated(
                order_id=order.id,
                channel_code=order.channel.code if order.channel_id else "",
                status=int(OrderStatus.CANCELLED),
                invoice_status=2,
                from_status=previous_status,
                to_status=int(OrderStatus.CANCELLED),
                source="scan.verify.cancellation",
            )
        )
```

**Lưu ý:** mở `backend/apps/kiotviet/webhook_impl.py:275-304` (`_publish_webhook_order_updated_event`) để đối chiếu đúng kwargs mà `OrderEventContract.build_order_updated` chấp nhận; nếu khác, chỉnh cho khớp — đừng bịa tham số.

- [ ] **Step 5: Chạy test, xác nhận PASS**

Cùng lệnh Step 3. Expected: 7 passed.

- [ ] **Step 6: Lint + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/kiotviet/application/services/cancellation_verifier.py backend/apps/kiotviet/tests/test_cancellation_verifier.py backend/config/settings_parts/base.py
git add backend/apps/kiotviet/application/services/cancellation_verifier.py backend/apps/kiotviet/tests/test_cancellation_verifier.py backend/config/settings_parts/base.py
git commit -m "feat(kiotviet): add realtime invoice cancellation verifier"
```

---

## Task 2: Chốt 1 — verify lúc quét (lookup view)

**Files:**
- Modify: `backend/apps/orders/transport/views/shipping_lookup_view.py`
- Create: `backend/apps/orders/tests/test_shipping_lookup_cancel_verify.py`

**Interfaces:**
- Consumes: `CancelCheck`, `InvoiceCancellationVerifier`, `VerificationUnavailable` (Task 1).
- Produces: `GET /api/orders/lookup-by-shipping-code?code=X&mode=dispatch|return`
  - `mode` mặc định `dispatch`.
  - Payload đơn có thêm field `invoice_status: int|null`.
  - `409 {"detail": "order_cancelled", "order_code": "..."}` khi KiotViet xác nhận hủy (chỉ `mode=dispatch`).
  - `503 {"detail": "verify_unavailable"}` khi không hỏi được KiotViet (chỉ `mode=dispatch`).

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/orders/tests/test_shipping_lookup_cancel_verify.py`:

```python
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.kiotviet.application.services.cancellation_verifier import (
    CancelCheck,
    VerificationUnavailable,
)
from apps.orders.models import Order, OrderStatus

pytestmark = pytest.mark.django_db

_URL = "/api/orders/lookup-by-shipping-code"
_VERIFY = (
    "apps.orders.transport.views.shipping_lookup_view."
    "InvoiceCancellationVerifier.verify_not_cancelled"
)


@pytest.fixture
def client(warehouse_staff_user):
    # Tái dùng fixture user có quyền section "shipments" trong apps/orders/tests/conftest.py.
    # Nếu chưa có, tạo user + gán quyền theo đúng mẫu các test lookup hiện có.
    api = APIClient()
    api.force_authenticate(user=warehouse_staff_user)
    return api


def _order() -> Order:
    return Order.objects.create(
        order_code=f"ORD-{uuid.uuid4().hex[:10]}",
        shipping_code=f"SC{uuid.uuid4().hex[:10]}",
        status=OrderStatus.IN_DELIVERY,
        name="KH",
        phone="0900000002",
    )


def test_dispatch_mode_blocks_when_kiotviet_says_cancelled(client):
    order = _order()
    with patch(_VERIFY, return_value=CancelCheck.CANCELLED):
        res = client.get(_URL, {"code": order.shipping_code, "mode": "dispatch"})
    assert res.status_code == 409
    assert res.json()["detail"] == "order_cancelled"


def test_dispatch_mode_blocks_when_kiotviet_unreachable(client):
    order = _order()
    with patch(_VERIFY, side_effect=VerificationUnavailable("timeout")):
        res = client.get(_URL, {"code": order.shipping_code, "mode": "dispatch"})
    assert res.status_code == 503
    assert res.json()["detail"] == "verify_unavailable"


def test_dispatch_mode_allows_live_order(client):
    order = _order()
    with patch(_VERIFY, return_value=CancelCheck.OK):
        res = client.get(_URL, {"code": order.shipping_code, "mode": "dispatch"})
    assert res.status_code == 200
    assert res.json()["order_id"] == order.id


def test_return_mode_never_verifies_and_allows_cancelled_order(client):
    order = _order()
    order.status = OrderStatus.CANCELLED
    order.save(update_fields=["status"])
    with patch(_VERIFY) as verify:
        res = client.get(_URL, {"code": order.shipping_code, "mode": "return"})
    verify.assert_not_called()
    assert res.status_code == 200
    assert res.json()["order_id"] == order.id


def test_payload_exposes_invoice_status(client):
    order = _order()
    order.invoice_status = 1
    order.save(update_fields=["invoice_status"])
    with patch(_VERIFY, return_value=CancelCheck.OK):
        res = client.get(_URL, {"code": order.shipping_code, "mode": "dispatch"})
    assert res.json()["invoice_status"] == 1
```

**Lưu ý:** mở `backend/apps/orders/tests/` xem các test lookup hiện có dùng fixture user/quyền nào (`require_section("shipments")`) và dùng đúng fixture đó thay cho `warehouse_staff_user` nếu tên khác.

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/.env \
  --env-file c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -e POSTGRES_DB=desi_scanverify \
  -v "c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/backend:/app" -w /app \
  desi-order-web-test:latest python -m pytest apps/orders/tests/test_shipping_lookup_cancel_verify.py -q
```

Expected: FAIL — ImportError (`InvoiceCancellationVerifier` chưa được import trong view) và 409/503 chưa tồn tại.

- [ ] **Step 3: Implement view**

Trong `backend/apps/orders/transport/views/shipping_lookup_view.py`:

Thêm import ở đầu file (sau `from apps.orders.models import Order, OrderStatus`):

```python
from apps.kiotviet.application.services.cancellation_verifier import (
    CancelCheck,
    InvoiceCancellationVerifier,
    VerificationUnavailable,
)
```

Thêm `invoice_status` vào `_LookupOrderSerializer` fields (sau `"status_label"`):

```python
        "invoice_status": drf_serializers.IntegerField(allow_null=True),
```

Thêm vào dict trả về của `_serialize` (sau `"status_label"`):

```python
        "invoice_status": o.invoice_status,
```

Thêm `mode` vào `@extend_schema` parameters (sau param `code`):

```python
        OpenApiParameter(
            name="mode",
            type=str,
            required=False,
            enum=["dispatch", "return"],
            description=(
                "dispatch (mặc định): xác minh realtime với KiotViet, chặn đơn đã hủy. "
                "return: không xác minh (đơn hủy vẫn được nhập hoàn)."
            ),
        ),
```

Thêm responses 409/503 vào `@extend_schema` responses:

```python
        409: OpenApiResponse(description="Đơn đã hủy trên KiotViet — không cho xuất kho."),
        503: OpenApiResponse(description="Không xác minh được với KiotViet — chặn quét (fail-closed)."),
```

Thay phần cuối hàm `lookup_by_shipping_code` (từ dòng `dispatched_ids = ...` tới hết) bằng:

```python
    dispatched_ids = _dispatched_order_ids([o.id for o in orders])

    # Quét XUẤT giao: DB có thể trễ tới 30' so với KiotViet → hỏi thẳng nguồn sự thật.
    # Quét NHẬP hoàn không xác minh: đơn hủy thì hàng về kho là đúng nghiệp vụ.
    if (request.query_params.get("mode") or "dispatch").strip() != "return":
        blocked = _verify_not_cancelled(orders)
        if blocked is not None:
            return blocked

    if len(orders) > 1:
        return Response(
            {
                "multiple": True,
                "orders": [_serialize(o, o.id in dispatched_ids) for o in orders],
            }
        )
    return Response(_serialize(orders[0], orders[0].id in dispatched_ids))


def _verify_not_cancelled(orders: list[Order]) -> Response | None:
    """Trả Response chặn (409/503) nếu bất kỳ đơn nào bị hủy / không xác minh được."""
    for order in orders:
        try:
            check = InvoiceCancellationVerifier.verify_not_cancelled(order)
        except VerificationUnavailable:
            return Response(
                {"detail": "verify_unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if check is CancelCheck.CANCELLED:
            return Response(
                {"detail": "order_cancelled", "order_code": order.order_code},
                status=status.HTTP_409_CONFLICT,
            )
        # UNVERIFIABLE (đơn ngoài KiotViet / kill-switch tắt) → DB là nguồn sự thật.
        if check is CancelCheck.UNVERIFIABLE and (
            int(order.status) == int(OrderStatus.CANCELLED) or order.invoice_status == 2
        ):
            return Response(
                {"detail": "order_cancelled", "order_code": order.order_code},
                status=status.HTTP_409_CONFLICT,
            )
    return None
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

Cùng lệnh Step 2. Expected: 5 passed.

- [ ] **Step 5: Regen OpenAPI schema cho FE**

```bash
cd frontend && npm run gen:api && cd ..
git diff --stat frontend/src/api/schema.d.ts backend/schema.yaml
```

Expected: hai file có thay đổi (thêm `invoice_status`, param `mode`, response 409/503).

- [ ] **Step 6: Lint + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/transport/views/shipping_lookup_view.py backend/apps/orders/tests/test_shipping_lookup_cancel_verify.py
git add backend/apps/orders/transport/views/shipping_lookup_view.py backend/apps/orders/tests/test_shipping_lookup_cancel_verify.py backend/schema.yaml frontend/src/api/schema.d.ts
git commit -m "feat(orders): verify cancellation with KiotViet on dispatch scan lookup"
```

---

## Task 3: Chốt 2 — guard cứng trong `dispatch_orders_batch`

**Files:**
- Modify: `backend/apps/orders/application/services/order_service_commands.py:489-534`
- Create: `backend/apps/orders/tests/test_dispatch_cancelled_guard.py`

**Interfaces:**
- Consumes: `CancelCheck`, `InvoiceCancellationVerifier`, `VerificationUnavailable` (Task 1).
- Produces: `dispatch_orders_batch` trả `{"order_id": N, "ok": False, "error": "order_cancelled"}` cho đơn đã hủy và `"error": "verify_unavailable"` khi không hỏi được KiotViet. Các đơn khác trong lô vẫn xử lý bình thường (không raise cả lô).

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/orders/tests/test_dispatch_cancelled_guard.py`:

```python
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from apps.inventory.models import MovementType, StockMovement
from apps.kiotviet.application.services.cancellation_verifier import (
    CancelCheck,
    VerificationUnavailable,
)
from apps.orders.models import Order, OrderStatus
from apps.orders.services import OrderService

pytestmark = pytest.mark.django_db

_VERIFY = (
    "apps.orders.application.services.order_service_commands."
    "InvoiceCancellationVerifier.verify_not_cancelled"
)


def _order(**kwargs) -> Order:
    defaults = {
        "order_code": f"ORD-{uuid.uuid4().hex[:10]}",
        "status": OrderStatus.IN_DELIVERY,
        "name": "KH",
        "phone": "0900000003",
    }
    defaults.update(kwargs)
    return Order.objects.create(**defaults)


def _no_stock_movement(order: Order) -> bool:
    return not StockMovement.objects.filter(
        reference_type="order", reference_id=order.id, movement_type=MovementType.SALE
    ).exists()


def test_db_cancelled_order_is_rejected_without_touching_stock(warehouse, staff_user):
    order = _order(status=OrderStatus.CANCELLED)
    with patch(_VERIFY, return_value=CancelCheck.UNVERIFIABLE):
        results = OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=staff_user
        )
    assert results[0]["ok"] is False
    assert results[0]["error"] == "order_cancelled"
    assert _no_stock_movement(order)


def test_invoice_status_2_is_rejected(warehouse, staff_user):
    order = _order(invoice_status=2)
    with patch(_VERIFY, return_value=CancelCheck.UNVERIFIABLE):
        results = OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=staff_user
        )
    assert results[0]["error"] == "order_cancelled"
    assert _no_stock_movement(order)


def test_kiotviet_cancelled_is_rejected_even_if_db_looks_fine(warehouse, staff_user):
    order = _order()
    with patch(_VERIFY, return_value=CancelCheck.CANCELLED):
        results = OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=staff_user
        )
    assert results[0]["error"] == "order_cancelled"
    assert _no_stock_movement(order)


def test_verify_unavailable_is_fail_closed(warehouse, staff_user):
    order = _order()
    with patch(_VERIFY, side_effect=VerificationUnavailable("timeout")):
        results = OrderService.dispatch_orders_batch(
            order_ids=[order.id], warehouse_id=warehouse.id, actor=staff_user
        )
    assert results[0]["error"] == "verify_unavailable"
    assert _no_stock_movement(order)
```

**Lưu ý:** `warehouse` và `staff_user` là fixture — mở `backend/apps/orders/tests/conftest.py` và test xuất kho hiện có (`test_dispatch_stock_cycle*`) để lấy đúng tên fixture đang dùng; sửa cho khớp.

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/.env \
  --env-file c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -e POSTGRES_DB=desi_scanverify \
  -v "c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/backend:/app" -w /app \
  desi-order-web-test:latest python -m pytest apps/orders/tests/test_dispatch_cancelled_guard.py -q
```

Expected: FAIL — đơn hủy hiện vẫn `ok: True` (chỉ có warning `unexpected_status`).

- [ ] **Step 3: Implement guard**

Trong `backend/apps/orders/application/services/order_service_commands.py`, hàm `dispatch_orders_batch` (dòng 489). Thêm import bên trong hàm, cạnh các import cục bộ có sẵn:

```python
        from apps.kiotviet.application.services.cancellation_verifier import (
            CancelCheck,
            InvoiceCancellationVerifier,
            VerificationUnavailable,
        )
```

Sau khối `if order is None: ... continue` (dòng 505-507), chèn:

```python
            # Chốt cuối trước khi trừ tồn: đơn hủy TUYỆT ĐỐI không được xuất kho.
            # DB có thể trễ so với KiotViet → hỏi lại nguồn sự thật (fail-closed).
            if int(order.status) == int(OrderStatus.CANCELLED) or order.invoice_status == 2:
                results.append({"order_id": oid, "ok": False, "error": "order_cancelled"})
                continue
            try:
                check = InvoiceCancellationVerifier.verify_not_cancelled(order)
            except VerificationUnavailable:
                results.append({"order_id": oid, "ok": False, "error": "verify_unavailable"})
                continue
            if check is CancelCheck.CANCELLED:
                results.append({"order_id": oid, "ok": False, "error": "order_cancelled"})
                continue
```

Đảm bảo `OrderStatus` đã được import ở đầu file (`from apps.orders.models import Order, OrderStatus` — nếu chưa có `OrderStatus`, thêm vào).

- [ ] **Step 4: Chạy test, xác nhận PASS**

Cùng lệnh Step 2. Expected: 4 passed.

- [ ] **Step 5: Chạy hồi quy suite orders**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/.env \
  --env-file c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -e POSTGRES_DB=desi_scanverify \
  -v "c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/backend:/app" -w /app \
  desi-order-web-test:latest python -m pytest apps/orders/tests -q
```

Expected: PASS. Test xuất kho cũ nào đang dùng đơn `status=6` mà kỳ vọng `ok: True` thì đó là hành vi sai cũ — sửa test cho đúng hành vi mới và ghi rõ trong commit.

- [ ] **Step 6: Lint + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/application/services/order_service_commands.py backend/apps/orders/tests/test_dispatch_cancelled_guard.py
git add backend/apps/orders/application/services/order_service_commands.py backend/apps/orders/tests/test_dispatch_cancelled_guard.py
git commit -m "feat(orders): reject cancelled orders in dispatch batch"
```

---

## Task 4: Chốt 3 — cron khớp mã pending không tự xuất đơn hủy

**Files:**
- Modify: `backend/apps/inventory/models.py:393` (`PendingScanStatus`)
- Create: `backend/apps/inventory/migrations/00XX_pendingscan_cancelled_status.py` (dùng `makemigrations`, tên tự sinh)
- Modify: `backend/apps/inventory/application/services/pending_scan_service.py` (`_process_one` + `_complete_dispatch`)
- Create: `backend/apps/inventory/tests/test_pending_scan_cancelled.py`

**Interfaces:**
- Consumes: `dispatch_orders_batch` trả `error: "order_cancelled" | "verify_unavailable"` (Task 3).
- Produces: `PendingScanStatus.CANCELLED = "cancelled"` (label "Đơn đã hủy"). Cron gặp đơn hủy → scan chuyển `cancelled`, **không tạo StockMovement**; gặp `verify_unavailable` → giữ `pending` để thử lại vòng sau.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_pending_scan_cancelled.py`:

```python
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from apps.inventory.application.services.pending_scan_service import PendingScanService
from apps.inventory.models import (
    MovementType,
    PendingScanKind,
    PendingScanStatus,
    PendingShipmentScan,
    StockMovement,
)
from apps.kiotviet.application.services.cancellation_verifier import (
    CancelCheck,
    VerificationUnavailable,
)
from apps.orders.models import Order, OrderStatus

pytestmark = pytest.mark.django_db

_VERIFY = (
    "apps.orders.application.services.order_service_commands."
    "InvoiceCancellationVerifier.verify_not_cancelled"
)


def _order(code: str, **kwargs) -> Order:
    defaults = {
        "order_code": f"ORD-{uuid.uuid4().hex[:10]}",
        "shipping_code": code,
        "status": OrderStatus.IN_DELIVERY,
        "name": "KH",
        "phone": "0900000004",
    }
    defaults.update(kwargs)
    return Order.objects.create(**defaults)


def _scan(code: str, warehouse, user) -> PendingShipmentScan:
    return PendingShipmentScan.objects.create(
        code=code,
        kind=PendingScanKind.DISPATCH,
        status=PendingScanStatus.PENDING,
        warehouse=warehouse,
        scanned_by=user,
    )


def test_cancelled_order_marks_scan_cancelled_and_skips_stock(warehouse, staff_user):
    code = f"SC{uuid.uuid4().hex[:10]}"
    order = _order(code)
    scan = _scan(code, warehouse, staff_user)
    with patch(_VERIFY, return_value=CancelCheck.CANCELLED):
        PendingScanService.match_pending_scans()
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.CANCELLED
    assert not StockMovement.objects.filter(
        reference_type="order", reference_id=order.id, movement_type=MovementType.SALE
    ).exists()


def test_verify_unavailable_keeps_scan_pending_for_retry(warehouse, staff_user):
    code = f"SC{uuid.uuid4().hex[:10]}"
    _order(code)
    scan = _scan(code, warehouse, staff_user)
    with patch(_VERIFY, side_effect=VerificationUnavailable("timeout")):
        PendingScanService.match_pending_scans()
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.PENDING
```

**Lưu ý:** kiểm tra `backend/apps/inventory/tests/conftest.py` cho tên fixture `warehouse` / `staff_user`; và `PendingShipmentScan` có thể yêu cầu thêm field bắt buộc — mở `backend/apps/inventory/models.py:400-430` để khớp.

- [ ] **Step 2: Thêm status + migration**

Trong `backend/apps/inventory/models.py`, class `PendingScanStatus` (dòng 393):

```python
class PendingScanStatus(models.TextChoices):
    PENDING = "pending", "Chờ khớp"
    MATCHED = "matched", "Đã khớp"
    FAILED = "failed", "Lỗi"
    INVALID = "invalid", "Mã không đúng"
    CANCELLED = "cancelled", "Đơn đã hủy"
```

`"cancelled"` = 9 ký tự, vừa `max_length=10` hiện tại → **không cần đổi max_length**. Sinh migration:

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/.env \
  --env-file c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/backend:/app" -w /app \
  desi-order-web-test:latest python manage.py makemigrations inventory
```

- [ ] **Step 3: Chạy test, xác nhận FAIL**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/.env \
  --env-file c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -e POSTGRES_DB=desi_scanverify \
  -v "c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/backend:/app" -w /app \
  desi-order-web-test:latest python -m pytest apps/inventory/tests/test_pending_scan_cancelled.py -q --create-db
```

Expected: FAIL — scan hiện thành `failed` (do Task 3 raise `order_cancelled`) chứ chưa phải `cancelled`; test thứ 2 cũng `failed` thay vì giữ `pending`.

- [ ] **Step 4: Implement**

Trong `backend/apps/inventory/application/services/pending_scan_service.py`:

Thêm exception nghiệp vụ ở đầu file (sau các import):

```python
class ScanOrderCancelled(Exception):
    """Đơn đã hủy → không xuất kho, đánh dấu scan là cancelled."""


class ScanVerifyUnavailable(Exception):
    """Không xác minh được với KiotViet → giữ pending, thử lại vòng sau."""
```

Trong `_complete_dispatch` (dòng 378), sau khi lấy `result = results[0]`:

```python
        result = results[0]
        if not result.get("ok"):
            error = str(result.get("error") or "")
            if error == "order_cancelled":
                raise ScanOrderCancelled("Đơn đã hủy — không xuất kho.")
            if error == "verify_unavailable":
                raise ScanVerifyUnavailable("Chưa xác minh được với KiotViet.")
            raise ValueError(error or "Xuất kho thất bại.")
        return "", ", ".join(result.get("warnings") or [])
```

Trong `_process_one` (dòng 246-259), thêm hai nhánh except **trước** `except Exception`:

```python
            try:
                if scan.kind == PendingScanKind.DISPATCH:
                    result_ref, note = PendingScanService._complete_dispatch(scan, order)
                else:
                    result_ref, note = PendingScanService._complete_return(scan, order)
            except SoftTimeLimitExceeded:
                raise  # timeout của task, không phải lỗi nghiệp vụ của scan này
            except ScanOrderCancelled as exc:
                # Đơn về ở trạng thái hủy → KHÔNG tự xuất kho, chờ người xử lý.
                scan.status = PendingScanStatus.CANCELLED
                scan.matched_order = order
                scan.error = (multi_note + str(exc)).strip()
                scan.save()
                return "failed"
            except ScanVerifyUnavailable as exc:
                # KiotViet không trả lời → giữ pending, cron thử lại vòng sau.
                logger.warning("pending scan %s verify unavailable: %s", scan.code, exc)
                scan.save(update_fields=["attempts", "last_attempt_at", "updated_at"])
                return "unmatched"
            except Exception as exc:  # noqa: BLE001 — lỗi nghiệp vụ → failed, chờ xử lý tay
                logger.warning("pending scan %s failed matching order %s: %s", scan.code, order.id, exc)
                scan.status = PendingScanStatus.FAILED
                scan.matched_order = order
                scan.error = (multi_note + str(exc)).strip()
                scan.save()
                return "failed"
```

Áp dụng đúng hai nhánh except tương tự vào `manual_match` (dòng 345-364): `ScanOrderCancelled` → `scan.status = PendingScanStatus.CANCELLED` + `return {"ok": False, "error": "Đơn đã hủy — không xuất kho."}`; `ScanVerifyUnavailable` → giữ `PENDING` + `return {"ok": False, "error": "Chưa xác minh được với KiotViet — thử lại sau."}`.

- [ ] **Step 5: Chạy test, xác nhận PASS**

Cùng lệnh Step 3 (bỏ `--create-db` nếu đã chạy). Expected: 2 passed.

- [ ] **Step 6: Hồi quy suite inventory**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/.env \
  --env-file c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -e POSTGRES_DB=desi_scanverify \
  -v "c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/backend:/app" -w /app \
  desi-order-web-test:latest python -m pytest apps/inventory/tests -q
```

Expected: PASS (trừ baseline fail đã biết: `stock_negative_alert`, `dispatch_stock_cycle` cancel-release — nếu gặp, xác nhận chúng cũng fail trên master trước khi bỏ qua).

- [ ] **Step 7: Lint + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/inventory/models.py backend/apps/inventory/application/services/pending_scan_service.py backend/apps/inventory/tests/test_pending_scan_cancelled.py
git add backend/apps/inventory/models.py backend/apps/inventory/migrations/ backend/apps/inventory/application/services/pending_scan_service.py backend/apps/inventory/tests/test_pending_scan_cancelled.py
git commit -m "feat(inventory): stop auto-dispatching cancelled orders on pending scan match"
```

---

## Task 5: Frontend

**Files:**
- Modify: `frontend/src/features/inventory/scan/scan-types.ts`
- Modify: `frontend/src/features/inventory/scan/scan-queries.ts`
- Modify: `frontend/src/features/inventory/scan/scan-cancelled.ts`
- Modify: `frontend/src/features/inventory/scan/ScanDispatchDialog.tsx`
- Modify: `frontend/src/features/inventory/scan/ScanReturnDialog.tsx`
- Modify: `frontend/src/features/inventory/scan/PendingScansDialog.tsx`
- Modify: `frontend/src/features/inventory/scan/__tests__/scan-cancelled.test.ts`

**Interfaces:**
- Consumes: `GET /api/orders/lookup-by-shipping-code?code=X&mode=dispatch|return` → 200 / 409 `order_cancelled` / 503 `verify_unavailable` (Task 2). `fetchJson` gắn `.status` và `.body` vào Error (`src/services/http.ts`).

- [ ] **Step 1: Cập nhật test `scan-cancelled`**

Sửa `frontend/src/features/inventory/scan/__tests__/scan-cancelled.test.ts`, thêm case cho `invoice_status`:

```ts
it("coi đơn có invoice_status=2 là đã hủy dù status chưa đổi", () => {
  expect(isCancelledOrder({ status: 2, invoice_status: 2 })).toBe(true);
});

it("đơn bình thường không bị coi là hủy", () => {
  expect(isCancelledOrder({ status: 2, invoice_status: 1 })).toBe(false);
});
```

Giữ nguyên các case cũ (`status === 6` → true), bổ sung `invoice_status: 1` vào object của chúng cho khớp type mới.

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd frontend && npx vitest run src/features/inventory/scan/__tests__/scan-cancelled.test.ts
```

Expected: FAIL — `isCancelledOrder` chưa xét `invoice_status`.

- [ ] **Step 3: Implement types + helper + query**

`scan-types.ts` — thêm field vào `LookupOrder` (sau `status_label`):

```ts
  invoice_status: number | null;
```

`scan-cancelled.ts` — thay toàn bộ nội dung:

```ts
import type { LookupOrder } from "./scan-types";

/** OrderStatus.CANCELLED (backend apps/orders/models.py) — đơn đã hủy. */
export const ORDER_STATUS_CANCELLED = 6;
/** KiotViet invoice status = 2 → hóa đơn đã hủy. */
export const INVOICE_STATUS_CANCELLED = 2;

/**
 * Đơn đã hủy theo DB. Backend còn xác minh realtime với KiotViet (409) cho luồng
 * xuất giao; hàm này là lớp chặn phụ, dùng cho đơn ngoài KiotViet / khi kill-switch tắt.
 */
export function isCancelledOrder(
  order: Pick<LookupOrder, "status" | "invoice_status">,
): boolean {
  return (
    order.status === ORDER_STATUS_CANCELLED ||
    order.invoice_status === INVOICE_STATUS_CANCELLED
  );
}

/** Toast khi chặn mã của đơn đã hủy. */
export function cancelledScanMessage(order: Pick<LookupOrder, "order_code">): string {
  return `Đơn ${order.order_code} đã hủy — không thể quét.`;
}
```

`scan-queries.ts` — `lookupByShippingCode` nhận thêm `mode`:

```ts
export async function lookupByShippingCode(
  code: string,
  mode: "dispatch" | "return" = "dispatch",
): Promise<LookupResponse> {
  return fetchJson(
    `/api/orders/lookup-by-shipping-code?code=${encodeURIComponent(code)}&mode=${mode}`,
  ) as Promise<LookupResponse>;
}
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

```bash
cd frontend && npx vitest run src/features/inventory/scan/__tests__/scan-cancelled.test.ts
```

Expected: PASS.

- [ ] **Step 5: `ScanDispatchDialog` — xử lý 409/503**

Trong `handleScan`, đổi lời gọi lookup thành `await lookupByShippingCode(code, "dispatch")`. Trong khối `catch (err)`, thêm **trước** nhánh `if (status === 404)`:

```tsx
      if (status === 409) {
        const body = (err as Error & { body?: { order_code?: string } }).body;
        beepError();
        toast.error(`Đơn ${body?.order_code ?? code} đã hủy — không thể xuất kho.`);
        return;
      }
      if (status === 503) {
        beepError();
        toast.error(
          `Chưa xác minh được đơn ${code} với KiotViet — chưa cho xuất kho. Thử quét lại.`,
        );
        return;
      }
```

Guard `isCancelledOrder(scanned)` hiện có ở dòng 114 **giữ nguyên** (bắt đơn ngoài KiotViet / kill-switch tắt).

- [ ] **Step 6: `ScanReturnDialog` — gỡ chặn đơn hủy**

Xóa khối chặn (`if (isCancelledOrder(scanned)) { ... return; }`, quanh dòng 110-114) và các import `isCancelledOrder` / `cancelledScanMessage` nếu không còn dùng. Đổi lời gọi lookup thành `await lookupByShippingCode(code, "return")`.

Lý do (ghi vào commit body, không cần comment trong code): đơn hủy → hàng quay về kho là đúng nghiệp vụ, chặn nhập hoàn sẽ làm hỏng luồng đang đúng.

- [ ] **Step 7: `PendingScansDialog` — badge trạng thái `cancelled`**

Trong `StatusBadge` (dòng 23), thêm nhánh **trước** nhánh `invalid`:

```tsx
  if (row.status === "cancelled") {
    return (
      <span className="rounded bg-amber-50 px-2 py-0.5 text-xs text-amber-700">
        Đơn đã hủy — không xuất kho
      </span>
    );
  }
```

Nếu `PendingScanRow["status"]` là union type sinh từ `src/api/schema.d.ts` (`@/lib/api/pending-scans`), chạy lại `npm run gen:api` để `"cancelled"` xuất hiện trong union. Nếu type khai tay, bổ sung `"cancelled"`.

- [ ] **Step 8: Typecheck + test FE**

```bash
cd frontend && npm run typecheck && npx vitest run src/features/inventory
```

Expected: typecheck sạch, test inventory pass.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/features/inventory/scan frontend/src/api/schema.d.ts frontend/src/lib/api/pending-scans.ts
git commit -m "feat(scan): block cancelled orders on dispatch scan, allow them on return scan"
```

---

## Task 6: Tài liệu vận hành + MR

**Files:**
- Modify: `backend/.env.example`
- Create/Modify: `docs/` — ghi chú vận hành kill-switch (thêm vào doc vận hành hiện có nếu có, nếu không tạo `docs/scan-cancel-verify-runbook.md`)

- [ ] **Step 1: Thêm env vào `.env.example`**

```
# Quét xuất kho: xác minh realtime với KiotViet rằng đơn chưa hủy (fail-closed).
# Đặt 0 khi KiotViet sự cố kéo dài để kho không đứng hình (tạm quay về check DB).
SCAN_VERIFY_CANCEL_REALTIME=1
SCAN_VERIFY_TIMEOUT_SECONDS=3
SCAN_VERIFY_CACHE_TTL_OK=30
SCAN_VERIFY_CACHE_TTL_CANCELLED=300
```

- [ ] **Step 2: Viết runbook `docs/scan-cancel-verify-runbook.md`**

Nội dung bắt buộc: (1) hành vi fail-closed và triệu chứng khi KiotViet lỗi (nhân viên kho thấy toast "Chưa xác minh được... Thử quét lại"); (2) cách tắt kill-switch (`SCAN_VERIFY_CANCEL_REALTIME=0` + restart web/worker) và hệ quả (quay về check DB, có thể lọt đơn hủy trong cửa sổ sync 30'); (3) cách đọc log (`scan verify unavailable`, `scan verify healed cancelled order`); (4) trạng thái pending scan `cancelled` nghĩa là gì và ai xử lý.

- [ ] **Step 3: Full suite backend (orders + inventory + kiotviet)**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/.env \
  --env-file c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -e POSTGRES_DB=desi_scanverify_full \
  -v "c:/Users/MYPC/Desktop/workspace/wt-scan-cancel-verify/backend:/app" -w /app \
  desi-order-web-test:latest python -m pytest apps/orders/tests apps/inventory/tests apps/kiotviet/tests -q --create-db
```

Expected: PASS. Ghi lại số liệu thật (passed/failed) — không được tuyên bố pass khi chưa chạy.

- [ ] **Step 4: Pull master, kiểm tra conflict, push, tạo MR**

```bash
git fetch origin && git rebase origin/master
git push -u origin feat/scan-cancel-realtime-verify
```

Tạo MR (title tiếng Anh, mô tả nêu: 3 chốt chặn, self-heal, fail-closed + kill-switch, thay đổi hành vi nhập hoàn) và gửi link cho user.

---

## Self-Review (đã chạy)

**Spec coverage:** verifier + 3 kết quả + exception (Task 1) ✓; self-heal (Task 1 Step 4) ✓; chốt 1 lookup + `mode` + `invoice_status` (Task 2) ✓; chốt 2 dispatch guard (Task 3) ✓; chốt 3 pending scan (Task 4) ✓; cache TTL OK/CANCELLED, không cache lỗi (Task 1) ✓; kill-switch + timeout (Task 1 settings, Task 6 runbook) ✓; FE gỡ chặn nhập hoàn + 409/503 (Task 5) ✓; test list của spec ✓.

**Ghi chú cho implementer:** `SCAN_VERIFY_TIMEOUT_SECONDS` chỉ có tác dụng nếu `_kiotviet_request` nhận `timeout`. Ở Task 1, mở `backend/apps/kiotviet/infrastructure/services/common_auth_customer.py:92` xem `_kiotviet_request` có tham số `timeout`/`**kwargs` chuyển xuống `requests` không. Nếu **có** → truyền `timeout=settings.SCAN_VERIFY_TIMEOUT_SECONDS` xuống `get_invoice_by_code` (thêm kwarg optional, mặc định giữ nguyên hành vi cũ). Nếu **không** → giữ nguyên timeout mặc định của client và ghi rõ điều đó vào runbook (Task 6) thay vì bịa ra một setting không có tác dụng.
