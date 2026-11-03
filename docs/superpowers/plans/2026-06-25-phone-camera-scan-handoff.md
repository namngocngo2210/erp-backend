# Phone-Camera Scan Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cho phép desktop mở dialog quét, dùng camera điện thoại (đã đăng nhập) làm đầu quét, mã hiện realtime về desktop qua WebSocket.

**Architecture:** App backend mới `apps/scan_relay` lưu phiên ghép cặp trong Redis cache (TTL 15'), nhận mã từ điện thoại qua REST rồi `group_send` về desktop qua consumer Channels mới `ws/scan/<token>/`. Frontend thêm component dùng lại `<PhoneScanButton>` (render QR + nối WS) cắm vào 3 dialog quét, và một trang camera điện thoại `/scan-relay/:token`.

**Tech Stack:** Django + DRF + Channels (channels-redis, daphne) + Redis cache; React + TypeScript + TanStack Query; `@zxing/browser`, `qrcode.react`.

## Global Constraints

- Python ≥ 3.12, clean architecture, module độc lập (transport/service/dto tách lớp). Ruff sạch.
- Backend test: `TestCase` + `rest_framework.test.APIClient`; helper `apps.users.test_helpers` (`make_admin`, `make_user_with_permissions`, `make_user_with_sections`). Channels test: `channels.testing.WebsocketCommunicator`.
- Điện thoại **bắt buộc cùng user** đã tạo phiên: `session.user_id == request.user.id`; khác user → REST `403`, WS `close(4403)`.
- Quyền theo purpose: `dispatch`→`shipments:view`, `stock_document`→`stock-documents:create`, `return`→`returns:create`.
- Token = `uuid4().hex`; key Redis `scan:session:{token}`; TTL 900s (refresh khi hoạt động).
- WS auth qua `?token=<jwt>` (middleware `apps.channels_core.jwt_middleware` đã có); FE dùng lại `WebSocketManager` (`frontend/src/services/websocket.ts`).
- Commit message tiếng Anh, ngắn gọn, KHÔNG kèm dòng Co-Authored-By.
- Camera cần HTTPS (prod đã có; dev dùng localhost/https).
- Branch hiện tại: `feat/phone-camera-scan-handoff` (đã tạo từ master).

---

## File Structure

**Backend (mới trừ khi ghi rõ Modify):**
- `apps/scan_relay/__init__.py`, `apps.py` — app config
- `apps/scan_relay/dto.py` — `ScanSession` + hằng số purpose/permission/label
- `apps/scan_relay/services.py` — `ScanSessionService` (Redis cache + group_send)
- `apps/scan_relay/transport/serializers.py`, `transport/views.py` — REST
- `apps/scan_relay/urls.py` — routes REST
- `apps/scan_relay/consumers.py`, `routing.py` — WebSocket
- `apps/scan_relay/tests/test_session_service.py`, `test_session_views.py`, `test_consumer.py`
- Modify `config/settings_parts/base.py:58` — thêm app vào `INSTALLED_APPS`
- Modify `config/urls.py:20` — include REST urls
- Modify `config/asgi.py` — gộp websocket routing

**Frontend (mới trừ khi ghi rõ Modify):**
- `src/features/inventory/scan/phone/scan-session-queries.ts`
- `src/features/inventory/scan/phone/use-phone-scan-session.ts`
- `src/features/inventory/scan/phone/PhoneScanButton.tsx`
- `src/features/inventory/scan/phone/CameraScanner.tsx`
- `src/features/scan-relay/ScanRelayApp.tsx`, `ScanRelayPage.tsx`
- Modify `src/main.tsx` — route `/scan-relay`
- Modify `src/features/inventory/scan/ScanDispatchDialog.tsx`
- Modify `src/features/inventory/pages/StockDocumentsPage.tsx`
- Modify `src/features/inventory/pages/CreateReturnDialog.tsx`
- `package.json` — thêm `@zxing/browser`, `qrcode.react`

---

## Task 1: Backend — ScanSessionService + app scaffold

**Files:**
- Create: `backend/apps/scan_relay/__init__.py` (rỗng)
- Create: `backend/apps/scan_relay/apps.py`
- Create: `backend/apps/scan_relay/dto.py`
- Create: `backend/apps/scan_relay/services.py`
- Create: `backend/apps/scan_relay/tests/__init__.py` (rỗng)
- Create: `backend/apps/scan_relay/tests/test_session_service.py`
- Modify: `backend/config/settings_parts/base.py` (thêm `"apps.scan_relay",` sau dòng 58 `"apps.hr",`)

**Interfaces:**
- Produces:
  - `dto.PURPOSES: frozenset[str]` = {"dispatch","stock_document","return"}
  - `dto.PERMISSION_BY_PURPOSE: dict[str,str]`, `dto.PURPOSE_LABELS: dict[str,str]`
  - `dto.ScanSession(token: str, user_id: int, purpose: str)` (frozen dataclass)
  - `ScanSessionService.create(user_id: int, purpose: str) -> ScanSession`
  - `ScanSessionService.get(token: str) -> ScanSession | None`
  - `ScanSessionService.touch(token: str) -> None`
  - `ScanSessionService.push_code(token: str, code: str) -> None`  (group_send + touch)
  - `ScanSessionService.allow_code(token: str) -> bool`  (rate-limit, ≤10/giây)

- [ ] **Step 1: Register app trong INSTALLED_APPS**

Modify `backend/config/settings_parts/base.py`, thêm dòng ngay sau `"apps.hr",` (dòng 58):

```python
    "apps.hr",
    "apps.scan_relay",
]
```

- [ ] **Step 2: Tạo app config + dto**

Create `backend/apps/scan_relay/apps.py`:

```python
from django.apps import AppConfig


class ScanRelayConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.scan_relay"
```

Create `backend/apps/scan_relay/dto.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

PURPOSES = frozenset({"dispatch", "stock_document", "return"})

PERMISSION_BY_PURPOSE = {
    "dispatch": "shipments:view",
    "stock_document": "stock-documents:create",
    "return": "returns:create",
}

PURPOSE_LABELS = {
    "dispatch": "Quét xuất kho",
    "stock_document": "Quét thêm dòng vào phiếu kho",
    "return": "Quét tạo phiếu trả hàng",
}


@dataclass(frozen=True)
class ScanSession:
    token: str
    user_id: int
    purpose: str
```

Create empty `backend/apps/scan_relay/__init__.py` and `backend/apps/scan_relay/tests/__init__.py`.

- [ ] **Step 3: Write the failing test**

Create `backend/apps/scan_relay/tests/test_session_service.py`:

```python
from __future__ import annotations

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.scan_relay.dto import PERMISSION_BY_PURPOSE
from apps.scan_relay.services import ScanSessionService

IN_MEMORY = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


class ScanSessionServiceTests(TestCase):
    def tearDown(self):
        cache.clear()

    def test_create_returns_token_and_persists(self):
        s = ScanSessionService.create(user_id=7, purpose="dispatch")
        assert s.token
        assert s.user_id == 7
        got = ScanSessionService.get(s.token)
        assert got is not None and got.user_id == 7 and got.purpose == "dispatch"

    def test_create_rejects_unknown_purpose(self):
        with self.assertRaises(ValueError):
            ScanSessionService.create(user_id=1, purpose="bogus")

    def test_get_missing_returns_none(self):
        assert ScanSessionService.get("nope") is None

    def test_permission_map_covers_all_purposes(self):
        assert set(PERMISSION_BY_PURPOSE) == {"dispatch", "stock_document", "return"}

    @override_settings(CHANNEL_LAYERS=IN_MEMORY)
    def test_push_code_sends_to_group(self):
        s = ScanSessionService.create(user_id=3, purpose="dispatch")
        layer = get_channel_layer()
        async_to_sync(layer.group_add)(f"scan_relay_{s.token}", "test-chan")
        ScanSessionService.push_code(s.token, "VD123")
        msg = async_to_sync(layer.receive)("test-chan")
        assert msg["type"] == "scan.code"
        assert msg["code"] == "VD123"

    def test_rate_limit_blocks_after_threshold(self):
        s = ScanSessionService.create(user_id=3, purpose="dispatch")
        allowed = [ScanSessionService.allow_code(s.token) for _ in range(12)]
        assert allowed[0] is True
        assert allowed[-1] is False
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest apps/scan_relay/tests/test_session_service.py -v`
Expected: FAIL — `ModuleNotFoundError: apps.scan_relay.services`.

- [ ] **Step 5: Implement the service**

Create `backend/apps/scan_relay/services.py`:

```python
from __future__ import annotations

import uuid

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache

from .dto import PURPOSES, ScanSession

SESSION_TTL = 900  # 15 phút
RATE_WINDOW = 1  # giây
RATE_MAX = 10  # mã/giây mỗi phiên


def _session_key(token: str) -> str:
    return f"scan:session:{token}"


def _group(token: str) -> str:
    return f"scan_relay_{token}"


class ScanSessionService:
    @staticmethod
    def create(user_id: int, purpose: str) -> ScanSession:
        if purpose not in PURPOSES:
            raise ValueError(f"unknown purpose: {purpose}")
        token = uuid.uuid4().hex
        cache.set(_session_key(token), {"user_id": user_id, "purpose": purpose}, SESSION_TTL)
        return ScanSession(token=token, user_id=user_id, purpose=purpose)

    @staticmethod
    def get(token: str) -> ScanSession | None:
        data = cache.get(_session_key(token))
        if not data:
            return None
        return ScanSession(token=token, user_id=data["user_id"], purpose=data["purpose"])

    @staticmethod
    def touch(token: str) -> None:
        data = cache.get(_session_key(token))
        if data:
            cache.set(_session_key(token), data, SESSION_TTL)

    @staticmethod
    def push_code(token: str, code: str) -> None:
        layer = get_channel_layer()
        async_to_sync(layer.group_send)(_group(token), {"type": "scan.code", "code": code})
        ScanSessionService.touch(token)

    @staticmethod
    def allow_code(token: str) -> bool:
        # Rate-limit best-effort (get/set, không atomic) — đủ chống spam camera.
        key = f"scan:rl:{token}"
        count = cache.get(key, 0)
        if count >= RATE_MAX:
            return False
        cache.set(key, count + 1, RATE_WINDOW)
        return True

    @staticmethod
    def delete(token: str) -> None:
        cache.delete(_session_key(token))
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest apps/scan_relay/tests/test_session_service.py -v`
Expected: PASS (6 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/apps/scan_relay/__init__.py backend/apps/scan_relay/apps.py \
  backend/apps/scan_relay/dto.py backend/apps/scan_relay/services.py \
  backend/apps/scan_relay/tests/ backend/config/settings_parts/base.py
git commit -m "feat(scan-relay): add scan session service backed by redis"
```

---

## Task 2: Backend — REST endpoints (create / get / push code)

**Files:**
- Create: `backend/apps/scan_relay/transport/__init__.py` (rỗng)
- Create: `backend/apps/scan_relay/transport/serializers.py`
- Create: `backend/apps/scan_relay/transport/views.py`
- Create: `backend/apps/scan_relay/urls.py`
- Create: `backend/apps/scan_relay/tests/test_session_views.py`
- Modify: `backend/config/urls.py` (thêm include sau dòng 20 `apps.audit.urls`)

**Interfaces:**
- Consumes: `ScanSessionService` (Task 1), `apps.users.rbac.resolve_effective_permissions`.
- Produces (HTTP):
  - `POST /api/scan-sessions` body `{purpose}` → `200 {token, relay_url}` | `400` | `403`
  - `GET /api/scan-sessions/<token>` → `200 {purpose, purpose_label}` | `403` | `404`
  - `POST /api/scan-sessions/<token>/codes` body `{code}` → `200 {ok:true}` | `403` | `404` | `429`

- [ ] **Step 1: Write the failing test**

Create `backend/apps/scan_relay/tests/test_session_views.py`:

```python
from __future__ import annotations

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.scan_relay.services import ScanSessionService
from apps.users.test_helpers import make_user_with_permissions

IN_MEMORY = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


class ScanSessionViewTests(TestCase):
    def setUp(self):
        self.owner = make_user_with_permissions("relay-owner", ["shipments:view"])
        self.other = make_user_with_permissions("relay-other", ["shipments:view"])
        self.noperm = make_user_with_permissions("relay-noperm", ["customers:view"])

    def tearDown(self):
        cache.clear()

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def test_create_session_ok(self):
        resp = self._client(self.owner).post(
            "/api/scan-sessions", {"purpose": "dispatch"}, format="json"
        )
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert body["token"]
        assert body["relay_url"].endswith(f"/scan-relay/{body['token']}")

    def test_create_session_bad_purpose_400(self):
        resp = self._client(self.owner).post(
            "/api/scan-sessions", {"purpose": "bogus"}, format="json"
        )
        assert resp.status_code == 400

    def test_create_session_without_permission_403(self):
        resp = self._client(self.noperm).post(
            "/api/scan-sessions", {"purpose": "dispatch"}, format="json"
        )
        assert resp.status_code == 403

    def test_get_session_owner_ok(self):
        s = ScanSessionService.create(self.owner.id, "dispatch")
        resp = self._client(self.owner).get(f"/api/scan-sessions/{s.token}")
        assert resp.status_code == 200
        assert resp.json()["purpose"] == "dispatch"
        assert resp.json()["purpose_label"] == "Quét xuất kho"

    def test_get_session_other_user_403(self):
        s = ScanSessionService.create(self.owner.id, "dispatch")
        resp = self._client(self.other).get(f"/api/scan-sessions/{s.token}")
        assert resp.status_code == 403

    def test_get_session_missing_404(self):
        resp = self._client(self.owner).get("/api/scan-sessions/deadbeef")
        assert resp.status_code == 404

    @override_settings(CHANNEL_LAYERS=IN_MEMORY)
    def test_push_code_owner_ok_emits_group(self):
        s = ScanSessionService.create(self.owner.id, "dispatch")
        layer = get_channel_layer()
        async_to_sync(layer.group_add)(f"scan_relay_{s.token}", "t1")
        resp = self._client(self.owner).post(
            f"/api/scan-sessions/{s.token}/codes", {"code": "VD9"}, format="json"
        )
        assert resp.status_code == 200, resp.json()
        msg = async_to_sync(layer.receive)("t1")
        assert msg["code"] == "VD9"

    def test_push_code_other_user_403(self):
        s = ScanSessionService.create(self.owner.id, "dispatch")
        resp = self._client(self.other).post(
            f"/api/scan-sessions/{s.token}/codes", {"code": "VD9"}, format="json"
        )
        assert resp.status_code == 403

    def test_push_code_missing_session_404(self):
        resp = self._client(self.owner).post(
            "/api/scan-sessions/deadbeef/codes", {"code": "VD9"}, format="json"
        )
        assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/scan_relay/tests/test_session_views.py -v`
Expected: FAIL — 404 cho mọi route (chưa khai báo url).

- [ ] **Step 3: Implement serializers**

Create `backend/apps/scan_relay/transport/__init__.py` (rỗng) và `backend/apps/scan_relay/transport/serializers.py`:

```python
from rest_framework import serializers


class CreateSessionSerializer(serializers.Serializer):
    purpose = serializers.CharField()


class PushCodeSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=128)
```

- [ ] **Step 4: Implement views**

Create `backend/apps/scan_relay/transport/views.py`:

```python
from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.rbac import resolve_effective_permissions

from ..dto import PERMISSION_BY_PURPOSE, PURPOSE_LABELS, PURPOSES
from ..services import ScanSessionService
from .serializers import CreateSessionSerializer, PushCodeSerializer


def _can(user, purpose: str) -> bool:
    code = PERMISSION_BY_PURPOSE.get(purpose)
    if not code:
        return False
    return resolve_effective_permissions(user).can(code)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_session(request):
    ser = CreateSessionSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    purpose = ser.validated_data["purpose"]
    if purpose not in PURPOSES:
        return Response({"detail": "Mục đích không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)
    if not _can(request.user, purpose):
        return Response({"detail": "Không có quyền."}, status=status.HTTP_403_FORBIDDEN)
    session = ScanSessionService.create(user_id=request.user.id, purpose=purpose)
    relay_url = request.build_absolute_uri(f"/scan-relay/{session.token}")
    return Response({"token": session.token, "relay_url": relay_url})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_session(request, token: str):
    session = ScanSessionService.get(token)
    if session is None:
        return Response({"detail": "Phiên không tồn tại hoặc đã hết hạn."}, status=status.HTTP_404_NOT_FOUND)
    if session.user_id != request.user.id:
        return Response({"detail": "Không có quyền."}, status=status.HTTP_403_FORBIDDEN)
    return Response({"purpose": session.purpose, "purpose_label": PURPOSE_LABELS[session.purpose]})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def push_code(request, token: str):
    session = ScanSessionService.get(token)
    if session is None:
        return Response({"detail": "Phiên không tồn tại hoặc đã hết hạn."}, status=status.HTTP_404_NOT_FOUND)
    if session.user_id != request.user.id or not _can(request.user, session.purpose):
        return Response({"detail": "Không có quyền."}, status=status.HTTP_403_FORBIDDEN)
    if not ScanSessionService.allow_code(token):
        return Response({"detail": "Quá nhanh, thử lại."}, status=status.HTTP_429_TOO_MANY_REQUESTS)
    ser = PushCodeSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    ScanSessionService.push_code(token, ser.validated_data["code"].strip())
    return Response({"ok": True})
```

- [ ] **Step 5: Wire urls**

Create `backend/apps/scan_relay/urls.py`:

```python
from django.urls import path

from .transport import views

urlpatterns = [
    path("scan-sessions", views.create_session),
    path("scan-sessions/<str:token>", views.get_session),
    path("scan-sessions/<str:token>/codes", views.push_code),
]
```

Modify `backend/config/urls.py` — thêm dòng ngay trước `re_path(r"^api/", include("apps.audit.urls")),` (dòng 20):

```python
    re_path(r"^api/", include("apps.scan_relay.urls")),
    re_path(r"^api/", include("apps.audit.urls")),
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest apps/scan_relay/tests/test_session_views.py -v`
Expected: PASS (9 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/apps/scan_relay/transport/ backend/apps/scan_relay/urls.py \
  backend/apps/scan_relay/tests/test_session_views.py backend/config/urls.py
git commit -m "feat(scan-relay): add REST endpoints for scan sessions"
```

---

## Task 3: Backend — WebSocket consumer + routing

**Files:**
- Create: `backend/apps/scan_relay/consumers.py`
- Create: `backend/apps/scan_relay/routing.py`
- Create: `backend/apps/scan_relay/tests/test_consumer.py`
- Modify: `backend/config/asgi.py`

**Interfaces:**
- Consumes: `ScanSessionService.get` (Task 1), `dto` permission map.
- Produces: WS route `ws/scan/<token>/`. Consumer joins group `scan_relay_<token>`, handler `scan_code` → gửi `{"type":"scan.code","code":...}` xuống client.

- [ ] **Step 1: Write the failing test**

Create `backend/apps/scan_relay/tests/test_consumer.py`:

```python
from __future__ import annotations

from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.scan_relay.consumers import ScanRelayConsumer
from apps.scan_relay.services import ScanSessionService
from apps.users.test_helpers import make_user_with_permissions

IN_MEMORY = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


def _communicator(token, user=None):
    comm = WebsocketCommunicator(ScanRelayConsumer.as_asgi(), f"/ws/scan/{token}/")
    comm.scope["url_route"] = {"kwargs": {"token": token}}
    comm.scope["user"] = user
    return comm


@override_settings(CHANNEL_LAYERS=IN_MEMORY)
class ScanRelayConsumerTests(TestCase):
    def setUp(self):
        self.owner = make_user_with_permissions("ws-owner", ["shipments:view"])
        self.other = make_user_with_permissions("ws-other", ["shipments:view"])

    def tearDown(self):
        cache.clear()

    def test_owner_connects_and_receives_code(self):
        session = ScanSessionService.create(self.owner.id, "dispatch")

        async def scenario():
            comm = _communicator(session.token, self.owner)
            connected, _ = await comm.connect()
            assert connected
            layer = get_channel_layer()
            await layer.group_send(
                f"scan_relay_{session.token}", {"type": "scan.code", "code": "VD7"}
            )
            msg = await comm.receive_json_from()
            assert msg == {"type": "scan.code", "code": "VD7"}
            await comm.disconnect()

        async_to_sync(scenario)()

    def test_unauthenticated_rejected(self):
        session = ScanSessionService.create(self.owner.id, "dispatch")

        async def scenario():
            comm = _communicator(session.token, None)
            connected, _ = await comm.connect()
            assert connected is False

        async_to_sync(scenario)()

    def test_other_user_rejected(self):
        session = ScanSessionService.create(self.owner.id, "dispatch")

        async def scenario():
            comm = _communicator(session.token, self.other)
            connected, _ = await comm.connect()
            assert connected is False

        async_to_sync(scenario)()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/scan_relay/tests/test_consumer.py -v`
Expected: FAIL — `ModuleNotFoundError: apps.scan_relay.consumers`.

- [ ] **Step 3: Implement consumer**

Create `backend/apps/scan_relay/consumers.py`:

```python
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .services import ScanSessionService

logger = logging.getLogger(__name__)


class ScanRelayConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if not user or not getattr(user, "is_authenticated", False):
            await self.close(code=4401)
            return
        self.token = self.scope["url_route"]["kwargs"]["token"]
        session = await self._get_session(self.token)
        if session is None or session.user_id != user.id:
            await self.close(code=4403)
            return
        self.group = f"scan_relay_{self.token}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        group = getattr(self, "group", None)
        if group:
            await self.channel_layer.group_discard(group, self.channel_name)

    async def scan_code(self, event):
        await self.send_json({"type": "scan.code", "code": event["code"]})

    @staticmethod
    @database_sync_to_async
    def _get_session(token):
        return ScanSessionService.get(token)
```

Create `backend/apps/scan_relay/routing.py`:

```python
from django.urls import path

from .consumers import ScanRelayConsumer

websocket_urlpatterns = [
    path("ws/scan/<str:token>/", ScanRelayConsumer.as_asgi()),
]
```

- [ ] **Step 4: Merge routing vào asgi**

Modify `backend/config/asgi.py` — đổi 2 dòng import + dòng `URLRouter`:

```python
from apps.channels_core.jwt_middleware import JwtAuthMiddlewareStack
from apps.channels_core.routing import websocket_urlpatterns as crm_ws
from apps.scan_relay.routing import websocket_urlpatterns as scan_ws

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": JwtAuthMiddlewareStack(URLRouter(crm_ws + scan_ws)),
    }
)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest apps/scan_relay/tests/test_consumer.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run full app test suite + ruff**

Run: `pytest apps/scan_relay -v && ruff check apps/scan_relay`
Expected: PASS, no lint errors.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/scan_relay/consumers.py backend/apps/scan_relay/routing.py \
  backend/apps/scan_relay/tests/test_consumer.py backend/config/asgi.py
git commit -m "feat(scan-relay): add websocket consumer for desktop relay"
```

---

## Task 4: Frontend — deps + scan-session-queries

**Files:**
- Modify: `frontend/package.json` (deps)
- Create: `frontend/src/features/inventory/scan/phone/scan-session-queries.ts`
- Create: `frontend/src/features/inventory/scan/phone/__tests__/scan-session-queries.test.ts`

**Interfaces:**
- Produces:
  - `createScanSession(purpose: ScanPurpose): Promise<{token: string; relay_url: string}>`
  - `getScanSession(token: string): Promise<{purpose: ScanPurpose; purpose_label: string}>`
  - `pushScanCode(token: string, code: string): Promise<void>`
  - `type ScanPurpose = "dispatch" | "stock_document" | "return"`

- [ ] **Step 1: Install deps**

Run: `cd frontend && npm install @zxing/browser qrcode.react`
Expected: `package.json` thêm 2 dependency.

- [ ] **Step 2: Write the failing test**

Create `frontend/src/features/inventory/scan/phone/__tests__/scan-session-queries.test.ts`:

```ts
import { describe, expect, it, vi, beforeEach } from "vitest";

import * as http from "@/services/http";
import { createScanSession, getScanSession, pushScanCode } from "../scan-session-queries";

describe("scan-session-queries", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("createScanSession posts purpose", async () => {
    const spy = vi
      .spyOn(http, "fetchJson")
      .mockResolvedValue({ token: "t1", relay_url: "https://x/scan-relay/t1" });
    const res = await createScanSession("dispatch");
    expect(spy).toHaveBeenCalledWith("/api/scan-sessions", {
      method: "POST",
      body: JSON.stringify({ purpose: "dispatch" }),
    });
    expect(res.token).toBe("t1");
  });

  it("getScanSession GETs by token", async () => {
    const spy = vi
      .spyOn(http, "fetchJson")
      .mockResolvedValue({ purpose: "dispatch", purpose_label: "Quét xuất kho" });
    await getScanSession("t1");
    expect(spy).toHaveBeenCalledWith("/api/scan-sessions/t1");
  });

  it("pushScanCode posts code", async () => {
    const spy = vi.spyOn(http, "fetchJson").mockResolvedValue({ ok: true });
    await pushScanCode("t1", "VD9");
    expect(spy).toHaveBeenCalledWith("/api/scan-sessions/t1/codes", {
      method: "POST",
      body: JSON.stringify({ code: "VD9" }),
    });
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/inventory/scan/phone/__tests__/scan-session-queries.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement queries**

Create `frontend/src/features/inventory/scan/phone/scan-session-queries.ts`:

```ts
import { fetchJson } from "@/services/http";

export type ScanPurpose = "dispatch" | "stock_document" | "return";

export async function createScanSession(
  purpose: ScanPurpose,
): Promise<{ token: string; relay_url: string }> {
  return fetchJson("/api/scan-sessions", {
    method: "POST",
    body: JSON.stringify({ purpose }),
  }) as Promise<{ token: string; relay_url: string }>;
}

export async function getScanSession(
  token: string,
): Promise<{ purpose: ScanPurpose; purpose_label: string }> {
  return fetchJson(`/api/scan-sessions/${token}`) as Promise<{
    purpose: ScanPurpose;
    purpose_label: string;
  }>;
}

export async function pushScanCode(token: string, code: string): Promise<void> {
  await fetchJson(`/api/scan-sessions/${token}/codes`, {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/inventory/scan/phone/__tests__/scan-session-queries.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json \
  frontend/src/features/inventory/scan/phone/scan-session-queries.ts \
  frontend/src/features/inventory/scan/phone/__tests__/scan-session-queries.test.ts
git commit -m "feat(scan-relay): add phone scan session queries + deps"
```

---

## Task 5: Frontend — use-phone-scan-session hook

**Files:**
- Create: `frontend/src/features/inventory/scan/phone/use-phone-scan-session.ts`
- Create: `frontend/src/features/inventory/scan/phone/__tests__/use-phone-scan-session.test.ts`

**Interfaces:**
- Consumes: `createScanSession` (Task 4), `WebSocketManager` (`@/services/websocket`).
- Produces hook:
  `usePhoneScanSession({ purpose, onCode }: { purpose: ScanPurpose; onCode: (code: string) => void }) => { status: "idle"|"waiting"|"connected"|"error"; relayUrl: string | null; scannedCount: number; start: () => Promise<void>; stop: () => void }`
  - `start()` tạo phiên, mở WS `/ws/scan/<token>/`, mỗi message `scan.code` gọi `onCode` + tăng `scannedCount`, đặt `status="connected"`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/features/inventory/scan/phone/__tests__/use-phone-scan-session.test.ts`:

```ts
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import * as queries from "../scan-session-queries";

const listeners: Record<string, (e: MessageEvent) => void> = {};
const wsMock = {
  connect: vi.fn(),
  disconnect: vi.fn(),
  addEventListener: vi.fn((ev: string, cb: (e: MessageEvent) => void) => {
    listeners[ev] = cb;
  }),
  removeEventListener: vi.fn(),
};

vi.mock("@/services/websocket", () => ({
  WebSocketManager: vi.fn(() => wsMock),
}));

import { usePhoneScanSession } from "../use-phone-scan-session";

describe("usePhoneScanSession", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    for (const k of Object.keys(listeners)) delete listeners[k];
  });

  it("start creates session and feeds codes via onCode", async () => {
    vi.spyOn(queries, "createScanSession").mockResolvedValue({
      token: "t1",
      relay_url: "https://x/scan-relay/t1",
    });
    const onCode = vi.fn();
    const { result } = renderHook(() => usePhoneScanSession({ purpose: "dispatch", onCode }));

    await act(async () => {
      await result.current.start();
    });
    expect(result.current.relayUrl).toBe("https://x/scan-relay/t1");

    // Simulate WS open + message
    act(() => listeners["open"]?.(new MessageEvent("open")));
    await waitFor(() => expect(result.current.status).toBe("connected"));
    act(() =>
      listeners["message"]?.(
        new MessageEvent("message", { data: JSON.stringify({ type: "scan.code", code: "VD9" }) }),
      ),
    );
    expect(onCode).toHaveBeenCalledWith("VD9");
    expect(result.current.scannedCount).toBe(1);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/inventory/scan/phone/__tests__/use-phone-scan-session.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement hook**

Create `frontend/src/features/inventory/scan/phone/use-phone-scan-session.ts`:

```ts
import { useCallback, useEffect, useRef, useState } from "react";

import { WebSocketManager } from "@/services/websocket";

import { createScanSession, type ScanPurpose } from "./scan-session-queries";

type Status = "idle" | "waiting" | "connected" | "error";

export function usePhoneScanSession({
  purpose,
  onCode,
}: {
  purpose: ScanPurpose;
  onCode: (code: string) => void;
}) {
  const [status, setStatus] = useState<Status>("idle");
  const [relayUrl, setRelayUrl] = useState<string | null>(null);
  const [scannedCount, setScannedCount] = useState(0);
  const wsRef = useRef<WebSocketManager | null>(null);
  const onCodeRef = useRef(onCode);
  onCodeRef.current = onCode;

  const stop = useCallback(() => {
    wsRef.current?.disconnect();
    wsRef.current = null;
    setStatus("idle");
    setRelayUrl(null);
    setScannedCount(0);
  }, []);

  const start = useCallback(async () => {
    setStatus("waiting");
    try {
      const { token, relay_url } = await createScanSession(purpose);
      setRelayUrl(relay_url);
      const ws = new WebSocketManager(`/ws/scan/${token}/`);
      ws.addEventListener("open", () => setStatus("connected"));
      ws.addEventListener("message", (e) => {
        try {
          const data = JSON.parse((e as MessageEvent).data as string);
          if (data?.type === "scan.code" && typeof data.code === "string") {
            onCodeRef.current(data.code);
            setScannedCount((n) => n + 1);
          }
        } catch {
          /* ignore malformed frame */
        }
      });
      ws.addEventListener("error", () => setStatus("error"));
      wsRef.current = ws;
      ws.connect();
    } catch {
      setStatus("error");
    }
  }, [purpose]);

  useEffect(() => () => stop(), [stop]);

  return { status, relayUrl, scannedCount, start, stop };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/inventory/scan/phone/__tests__/use-phone-scan-session.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/inventory/scan/phone/use-phone-scan-session.ts \
  frontend/src/features/inventory/scan/phone/__tests__/use-phone-scan-session.test.ts
git commit -m "feat(scan-relay): add use-phone-scan-session hook"
```

---

## Task 6: Frontend — PhoneScanButton (QR dialog)

**Files:**
- Create: `frontend/src/features/inventory/scan/phone/PhoneScanButton.tsx`
- Create: `frontend/src/features/inventory/scan/phone/__tests__/PhoneScanButton.test.tsx`

**Interfaces:**
- Consumes: `usePhoneScanSession` (Task 5), `qrcode.react`, shadcn `Button`/`Dialog`.
- Produces: `<PhoneScanButton purpose={ScanPurpose} onCode={(code: string) => void} />` — nút mở dialog, render QR `relayUrl`, hiện trạng thái + số mã đã quét.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/features/inventory/scan/phone/__tests__/PhoneScanButton.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const startMock = vi.fn();
vi.mock("../use-phone-scan-session", () => ({
  usePhoneScanSession: () => ({
    status: "connected",
    relayUrl: "https://x/scan-relay/t1",
    scannedCount: 3,
    start: startMock,
    stop: vi.fn(),
  }),
}));
vi.mock("qrcode.react", () => ({
  QRCodeSVG: ({ value }: { value: string }) => <div data-testid="qr">{value}</div>,
}));

import { PhoneScanButton } from "../PhoneScanButton";

describe("PhoneScanButton", () => {
  it("opens dialog, starts session, renders QR + count", async () => {
    render(<PhoneScanButton purpose="dispatch" onCode={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /điện thoại/i }));
    await waitFor(() => expect(startMock).toHaveBeenCalled());
    expect(screen.getByTestId("qr")).toHaveTextContent("https://x/scan-relay/t1");
    expect(screen.getByText(/đã quét 3/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/inventory/scan/phone/__tests__/PhoneScanButton.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement component**

Create `frontend/src/features/inventory/scan/phone/PhoneScanButton.tsx`:

```tsx
import { useState } from "react";
import { Smartphone } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import { usePhoneScanSession } from "./use-phone-scan-session";
import type { ScanPurpose } from "./scan-session-queries";

export function PhoneScanButton({
  purpose,
  onCode,
}: {
  purpose: ScanPurpose;
  onCode: (code: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const { status, relayUrl, scannedCount, start, stop } = usePhoneScanSession({ purpose, onCode });

  async function handleOpen() {
    setOpen(true);
    await start();
  }

  function handleClose(next: boolean) {
    if (!next) stop();
    setOpen(next);
  }

  return (
    <>
      <Button type="button" variant="outline" onClick={handleOpen}>
        <Smartphone className="mr-2 size-4" />
        Dùng điện thoại để quét
      </Button>
      <Dialog open={open} onOpenChange={handleClose}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Quét bằng điện thoại</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col items-center gap-3 py-2">
            {relayUrl ? (
              <QRCodeSVG value={relayUrl} size={220} />
            ) : (
              <div className="h-[220px] w-[220px] animate-pulse rounded bg-muted" />
            )}
            <p className="text-center text-sm text-muted-foreground">
              Mở camera điện thoại quét mã QR này, rồi quét mã vận đơn.
            </p>
            <p className="text-sm font-medium">
              {status === "connected"
                ? `Đã kết nối · đã quét ${scannedCount} mã`
                : status === "error"
                  ? "Lỗi kết nối, vui lòng đóng và thử lại."
                  : "Đang chờ điện thoại kết nối…"}
            </p>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/inventory/scan/phone/__tests__/PhoneScanButton.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/inventory/scan/phone/PhoneScanButton.tsx \
  frontend/src/features/inventory/scan/phone/__tests__/PhoneScanButton.test.tsx
git commit -m "feat(scan-relay): add PhoneScanButton with QR pairing dialog"
```

---

## Task 7: Frontend — CameraScanner (điện thoại)

**Files:**
- Create: `frontend/src/features/scan-relay/CameraScanner.tsx`
- Create: `frontend/src/features/scan-relay/__tests__/CameraScanner.test.tsx`

**Interfaces:**
- Produces: `<CameraScanner onCode={(code: string) => void} />` — mở camera sau (`facingMode:"environment"`), đọc mã (native `BarcodeDetector` nếu có, else `@zxing/browser`), chống trùng ~1.5s, gọi `onCode`. Khi không có camera/quyền → hiện thông báo.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/features/scan-relay/__tests__/CameraScanner.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

import { CameraScanner } from "../CameraScanner";

describe("CameraScanner", () => {
  beforeEach(() => {
    const track = { stop: vi.fn(), getCapabilities: () => ({}) };
    // @ts-expect-error test stub
    navigator.mediaDevices = {
      getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [track], getVideoTracks: () => [track] }),
    };
  });
  afterEach(() => vi.restoreAllMocks());

  it("uses native BarcodeDetector when available", async () => {
    const detect = vi.fn().mockResolvedValue([{ rawValue: "VD-NATIVE" }]);
    // @ts-expect-error test stub
    window.BarcodeDetector = vi.fn(() => ({ detect }));
    const onCode = vi.fn();
    render(<CameraScanner onCode={onCode} />);
    await waitFor(() => expect(onCode).toHaveBeenCalledWith("VD-NATIVE"), { timeout: 2000 });
    // @ts-expect-error cleanup
    delete window.BarcodeDetector;
  });

  it("shows error message when camera denied", async () => {
    // @ts-expect-error test stub
    navigator.mediaDevices.getUserMedia = vi.fn().mockRejectedValue(new Error("denied"));
    render(<CameraScanner onCode={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/không truy cập được camera/i)).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/scan-relay/__tests__/CameraScanner.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement component**

Create `frontend/src/features/scan-relay/CameraScanner.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";

type Props = { onCode: (code: string) => void };

// Chống quét trùng: bỏ qua cùng mã trong khoảng thời gian ngắn.
const DEDUP_MS = 1500;

export function CameraScanner({ onCode }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState<string | null>(null);
  const lastRef = useRef<{ code: string; at: number }>({ code: "", at: 0 });
  const onCodeRef = useRef(onCode);
  onCodeRef.current = onCode;

  useEffect(() => {
    let stopped = false;
    let stream: MediaStream | null = null;
    let raf = 0;
    let zxingControls: { stop: () => void } | null = null;

    function emit(code: string) {
      const now = Date.now();
      if (code === lastRef.current.code && now - lastRef.current.at < DEDUP_MS) return;
      lastRef.current = { code, at: now };
      onCodeRef.current(code);
    }

    async function start() {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "environment" },
        });
        if (stopped) return;
        const video = videoRef.current;
        if (video) {
          video.srcObject = stream;
          await video.play().catch(() => {});
        }
        if ("BarcodeDetector" in window) {
          // @ts-expect-error BarcodeDetector chưa có trong lib.dom mặc định
          const detector = new window.BarcodeDetector({
            formats: ["code_128", "qr_code", "ean_13", "code_39"],
          });
          const loop = async () => {
            if (stopped || !videoRef.current) return;
            try {
              const found = await detector.detect(videoRef.current);
              if (found?.[0]?.rawValue) emit(found[0].rawValue);
            } catch {
              /* frame chưa sẵn sàng */
            }
            raf = requestAnimationFrame(loop);
          };
          raf = requestAnimationFrame(loop);
        } else {
          const { BrowserMultiFormatReader } = await import("@zxing/browser");
          const reader = new BrowserMultiFormatReader();
          zxingControls = await reader.decodeFromVideoElement(
            videoRef.current!,
            (result) => {
              if (result) emit(result.getText());
            },
          );
        }
      } catch {
        if (!stopped) setError("Không truy cập được camera. Hãy cấp quyền hoặc dùng ô nhập tay.");
      }
    }

    void start();
    return () => {
      stopped = true;
      cancelAnimationFrame(raf);
      zxingControls?.stop();
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  if (error) {
    return <p className="p-4 text-center text-sm text-destructive">{error}</p>;
  }
  return (
    <video
      ref={videoRef}
      className="h-full w-full rounded-lg bg-black object-cover"
      muted
      playsInline
    />
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/scan-relay/__tests__/CameraScanner.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/scan-relay/CameraScanner.tsx \
  frontend/src/features/scan-relay/__tests__/CameraScanner.test.tsx
git commit -m "feat(scan-relay): add CameraScanner with native + zxing fallback"
```

---

## Task 8: Frontend — ScanRelayPage + app entry + route

**Files:**
- Create: `frontend/src/features/scan-relay/ScanRelayPage.tsx`
- Create: `frontend/src/features/scan-relay/ScanRelayApp.tsx`
- Create: `frontend/src/features/scan-relay/__tests__/ScanRelayPage.test.tsx`
- Modify: `frontend/src/main.tsx`

**Interfaces:**
- Consumes: `getScanSession`, `pushScanCode` (Task 4), `CameraScanner` (Task 7), `beepSuccess`/`beepError` (`@/features/inventory/scan/scan-sound`), `useAuthBootstrap` + `MobileLoginView` (`@/features/ess/*`).
- Produces:
  - `ScanRelayPage` (đọc token từ `window.location.pathname`): validate phiên → hiện nhãn; camera + ô nhập tay → mỗi mã `pushScanCode` + `beepSuccess`; đếm "Đã quét N".
  - `ScanRelayApp` (default export): auth-gate giống `MobileApp`, render `ScanRelayPage` khi đã đăng nhập.
  - `main.tsx`: path bắt đầu `/scan-relay` → render `<ScanRelayApp/>`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/features/scan-relay/__tests__/ScanRelayPage.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import * as queries from "@/features/inventory/scan/phone/scan-session-queries";

vi.mock("../CameraScanner", () => ({ CameraScanner: () => <div data-testid="cam" /> }));
vi.mock("@/features/inventory/scan/scan-sound", () => ({
  beepSuccess: vi.fn(),
  beepError: vi.fn(),
}));

import { ScanRelayPage } from "../ScanRelayPage";

describe("ScanRelayPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.history.pushState({}, "", "/scan-relay/tok1");
  });

  it("validates session and shows purpose label", async () => {
    vi.spyOn(queries, "getScanSession").mockResolvedValue({
      purpose: "dispatch",
      purpose_label: "Quét xuất kho",
    });
    render(<ScanRelayPage />);
    await waitFor(() => expect(screen.getByText(/quét xuất kho/i)).toBeInTheDocument());
    expect(screen.getByTestId("cam")).toBeInTheDocument();
  });

  it("shows error when session invalid", async () => {
    vi.spyOn(queries, "getScanSession").mockRejectedValue(new Error("404"));
    render(<ScanRelayPage />);
    await waitFor(() => expect(screen.getByText(/hết hạn|không hợp lệ/i)).toBeInTheDocument());
  });

  it("manual input submits code via pushScanCode", async () => {
    vi.spyOn(queries, "getScanSession").mockResolvedValue({
      purpose: "dispatch",
      purpose_label: "Quét xuất kho",
    });
    const push = vi.spyOn(queries, "pushScanCode").mockResolvedValue();
    render(<ScanRelayPage />);
    await waitFor(() => screen.getByText(/quét xuất kho/i));
    fireEvent.change(screen.getByPlaceholderText(/nhập mã/i), { target: { value: "VD9" } });
    fireEvent.click(screen.getByRole("button", { name: /gửi mã/i }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("tok1", "VD9"));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/scan-relay/__tests__/ScanRelayPage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement ScanRelayPage**

Create `frontend/src/features/scan-relay/ScanRelayPage.tsx`:

```tsx
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { beepError, beepSuccess } from "@/features/inventory/scan/scan-sound";
import {
  getScanSession,
  pushScanCode,
} from "@/features/inventory/scan/phone/scan-session-queries";

function tokenFromPath(): string {
  return window.location.pathname.replace(/^\/scan-relay\//, "").replace(/\/+$/, "");
}

export function ScanRelayPage() {
  const token = tokenFromPath();
  const [label, setLabel] = useState<string | null>(null);
  const [invalid, setInvalid] = useState(false);
  const [count, setCount] = useState(0);
  const [manual, setManual] = useState("");

  useEffect(() => {
    let active = true;
    getScanSession(token)
      .then((s) => active && setLabel(s.purpose_label))
      .catch(() => active && setInvalid(true));
    return () => {
      active = false;
    };
  }, [token]);

  async function send(code: string) {
    const trimmed = code.trim();
    if (!trimmed) return;
    try {
      await pushScanCode(token, trimmed);
      beepSuccess();
      setCount((n) => n + 1);
    } catch {
      beepError();
    }
  }

  if (invalid) {
    return (
      <div className="grid min-h-dvh place-items-center p-6 text-center">
        <p className="text-destructive">Phiên quét không hợp lệ hoặc đã hết hạn. Vui lòng quét lại mã QR trên máy tính.</p>
      </div>
    );
  }

  return (
    <div className="flex min-h-dvh flex-col gap-3 p-3">
      <header className="text-center">
        <h1 className="text-base font-semibold">{label ?? "Đang kết nối…"}</h1>
        <p className="text-sm text-muted-foreground">Đã quét {count} mã</p>
      </header>
      <div className="relative aspect-square w-full overflow-hidden rounded-lg">
        <LazyCamera onCode={send} />
      </div>
      <form
        className="mt-auto flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void send(manual);
          setManual("");
        }}
      >
        <Input
          value={manual}
          onChange={(e) => setManual(e.target.value)}
          placeholder="Nhập mã thủ công…"
          inputMode="text"
        />
        <Button type="submit">Gửi mã</Button>
      </form>
    </div>
  );
}

// CameraScanner mở camera ngay khi mount; tách ra để test mock dễ dàng.
import { CameraScanner } from "./CameraScanner";
function LazyCamera({ onCode }: { onCode: (code: string) => void }) {
  return <CameraScanner onCode={onCode} />;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/scan-relay/__tests__/ScanRelayPage.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Implement ScanRelayApp (auth gate)**

Create `frontend/src/features/scan-relay/ScanRelayApp.tsx`:

```tsx
import { MobileLoginView } from "@/features/ess/MobileLoginView";
import { useAuthBootstrap } from "@/hooks/use-auth-bootstrap";

import { ScanRelayPage } from "./ScanRelayPage";

export default function ScanRelayApp() {
  const { user, setUser, authChecking } = useAuthBootstrap();
  if (authChecking)
    return <div className="grid min-h-dvh place-items-center text-muted-foreground">Đang tải…</div>;
  if (!user) return <MobileLoginView onLoggedIn={(u) => setUser(u)} />;
  return <ScanRelayPage />;
}
```

- [ ] **Step 6: Wire route trong main.tsx**

Modify `frontend/src/main.tsx`:

Thêm lazy import (sau dòng `const MobileApp = ...`, ~L11):

```tsx
const ScanRelayApp = lazy(() => import("./features/scan-relay/ScanRelayApp"));
```

Thêm điều kiện path (sau `const isMobileRoute = ...`, ~L15):

```tsx
const isScanRelayRoute = path.startsWith("/scan-relay");
```

Thêm nhánh render (trước nhánh `isMobileRoute ?`):

```tsx
        {isWarrantyRoute ? (
          <Suspense fallback={null}>
            <WarrantyPage />
          </Suspense>
        ) : isScanRelayRoute ? (
          <Suspense fallback={null}>
            <ScanRelayApp />
          </Suspense>
        ) : isMobileRoute ? (
```

- [ ] **Step 7: Verify build + run page test again**

Run: `cd frontend && npx vitest run src/features/scan-relay && npx tsc --noEmit`
Expected: tests PASS, no type errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/scan-relay/ScanRelayPage.tsx \
  frontend/src/features/scan-relay/ScanRelayApp.tsx \
  frontend/src/features/scan-relay/__tests__/ScanRelayPage.test.tsx \
  frontend/src/main.tsx
git commit -m "feat(scan-relay): add phone relay page + auth-gated route"
```

---

## Task 9: Frontend — tích hợp PhoneScanButton vào 3 dialog

**Files:**
- Modify: `frontend/src/features/inventory/scan/ScanDispatchDialog.tsx` (cạnh `<ScanShipmentInput>` ~L175)
- Modify: `frontend/src/features/inventory/pages/StockDocumentsPage.tsx` (cạnh `<ScanShipmentInput>` ~L414)
- Modify: `frontend/src/features/inventory/pages/CreateReturnDialog.tsx` (cạnh `<ScanShipmentInput>` ~L274)

**Interfaces:**
- Consumes: `PhoneScanButton` (Task 6); các handler quét đã có: `handleScan` (dispatch), `handleScanCode` (stock doc + return).

- [ ] **Step 1: Tích hợp vào ScanDispatchDialog**

Modify `frontend/src/features/inventory/scan/ScanDispatchDialog.tsx`:

Thêm import:

```tsx
import { PhoneScanButton } from "./phone/PhoneScanButton";
```

Đổi khối ô quét (quanh L173-176) thành:

```tsx
          <div>
            <label className="mb-1 block text-sm text-muted-foreground">Quét mã vận đơn</label>
            <ScanShipmentInput ref={scanRef} onScan={handleScan} />
            <div className="mt-2">
              <PhoneScanButton purpose="dispatch" onCode={handleScan} />
            </div>
          </div>
```

- [ ] **Step 2: Tích hợp vào StockDocumentsPage (dialog tạo phiếu)**

Modify `frontend/src/features/inventory/pages/StockDocumentsPage.tsx`:

Thêm import:

```tsx
import { PhoneScanButton } from "@/features/inventory/scan/phone/PhoneScanButton";
```

Ngay sau `<ScanShipmentInput ... onScan={handleScanCode} ... />` (quanh L414-417), thêm:

```tsx
            <div className="mt-2">
              <PhoneScanButton purpose="stock_document" onCode={handleScanCode} />
            </div>
```

- [ ] **Step 3: Tích hợp vào CreateReturnDialog**

Modify `frontend/src/features/inventory/pages/CreateReturnDialog.tsx`:

Thêm import:

```tsx
import { PhoneScanButton } from "@/features/inventory/scan/phone/PhoneScanButton";
```

Ngay sau `<ScanShipmentInput ... onScan={handleScanCode} />` (quanh L274-277), thêm:

```tsx
            <div className="mt-2">
              <PhoneScanButton purpose="return" onCode={handleScanCode} />
            </div>
```

- [ ] **Step 4: Verify type + existing tests still pass**

Run: `cd frontend && npx tsc --noEmit && npx vitest run src/features/inventory/scan`
Expected: no type errors; scan tests PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/inventory/scan/ScanDispatchDialog.tsx \
  frontend/src/features/inventory/pages/StockDocumentsPage.tsx \
  frontend/src/features/inventory/pages/CreateReturnDialog.tsx
git commit -m "feat(scan-relay): wire phone scan button into 3 scan dialogs"
```

---

## Task 10: Verification end-to-end + tài liệu

**Files:**
- Modify: `docs/superpowers/specs/2026-06-25-phone-camera-scan-handoff-design.md` (đánh dấu trạng thái Done) — tuỳ chọn.

- [ ] **Step 1: Backend full suite + ruff**

Run: `cd backend && pytest apps/scan_relay -v && ruff check apps/scan_relay config`
Expected: tất cả PASS, không lỗi lint.

- [ ] **Step 2: Frontend lint + type + test toàn phần liên quan**

Run: `cd frontend && npx tsc --noEmit && npx vitest run src/features/scan-relay src/features/inventory/scan`
Expected: PASS.

- [ ] **Step 3: Smoke test thủ công (ghi lại kết quả)**

Trên môi trường HTTPS (hoặc localhost): mở Vận đơn → Quét xuất kho → "Dùng điện thoại để quét" → quét QR bằng điện thoại đã đăng nhập cùng user → camera bật → quét/nhập 1 mã → xác nhận mã hiện trên desktop. Lặp với phiếu kho + phiếu trả. Ghi lại pass/fail.

- [ ] **Step 4: Commit (nếu có cập nhật doc)**

```bash
git add docs/superpowers/
git commit -m "docs(scan-relay): mark implementation done"
```

---

## Self-Review (đã thực hiện khi viết plan)

- **Spec coverage:** §4 quyết định → Task 1–3 (token cùng-user, quyền theo purpose, WS); §6 backend → Task 1–3; §7 frontend → Task 4–9; §8 bảo mật → kiểm trong Task 2 (403/404/429) + Task 3 (4401/4403); §9 edge cases → reconnect (WebSocketManager có sẵn), token hết hạn (Task 8), trùng mã (Task 7); §10 test → mỗi task có test; cả 3 luồng → Task 9.
- **Placeholder scan:** không còn TBD/`handle edge cases` chung chung; mọi step có code/command thật.
- **Type consistency:** `ScanPurpose` dùng nhất quán Task 4→9; `createScanSession/getScanSession/pushScanCode` cùng chữ ký; group `scan_relay_<token>` + event `scan.code` khớp giữa service (Task 1), consumer (Task 3), hook (Task 5).
