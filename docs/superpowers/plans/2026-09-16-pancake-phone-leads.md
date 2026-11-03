# Kéo số điện thoại từ Pancake → đẩy cơ hội Foxia — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nhập token Pancake, đồng bộ page về, gán page ↔ kênh bán; poll định kỳ hội thoại có số điện thoại, lưu `pancake_leads` và đẩy sang Foxia đúng nguồn khách hàng của kênh; có trang cấu hình và trang xem lead.

**Architecture:** App mới `apps/pancake` (models + client + poll service + task + API), phụ thuộc xuống `apps.foxia` qua hàm thuần `push_customer(source_id, payload)` được tách ra từ `order_push_service.py` (hợp đồng Foxia chỉ có một chỗ). Celery beat 3 phút vào queue `foxia` (worker-foxia). Lead unique theo `(channel, phone)` ở DB. FE: hai trang mới trong feature-slice `features/pancake`.

**Tech Stack:** Django 5 + DRF (function-based `@api_view`), Celery + Redis, Postgres 16, `apps.common.http_client.HttpClient`, React + TypeScript + TanStack Query + vitest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-16-pancake-phone-leads-design.md`

## Global Constraints

- Worktree: `/home/desi/desi-order-pancake`, nhánh `feat/pancake-phone-leads` (tách từ `master` tại commit chứa spec `43274e33` hoặc mới hơn). **Không** làm việc trong `/home/desi/desi-order` (cây làm việc chung, session khác có thể checkout nhánh khác).
- Pancake: token tài khoản → `GET https://pages.fm/api/v1/pages?access_token=`; token page → `POST https://pages.fm/api/v1/pages/{id}/generate_page_access_token?access_token=` với header `Content-Type: application/json` + body `{}`; hội thoại → `GET https://pages.fm/api/public_api/v1/pages/{id}/conversations?page_access_token=&since=&until=&page_number=&sort_by=updated_at`; chi tiết → `GET .../conversations/{cid}/messages?page_access_token=&current_count=0`. Timestamp Pancake không có múi giờ → coi là **UTC**.
- Ánh xạ platform → cột Foxia: `facebook → messenger`, `zalo → zalo`, `personal_zalo → zalo`, khác → `None` (không gán được).
- Chống trùng: `UNIQUE (channel, phone)` trên `pancake_leads`. Không backfill: bật page → `polled_until = now`.
- Payload Foxia: `{"name", "phone", "address": "", "message": "Pancake: <tên page>"}`. Không retry POST Foxia.
- `PANCAKE_POLL_ENABLED` mặc định tắt; thao tác tay (lưu token, đồng bộ page, bật page, đẩy lại) không bị gate.
- Cài đặt Pancake + API cấu hình: **chỉ superuser**. Trang lead: quyền `pancake-leads:view`.
- Bình luận code tiếng Việt, theo văn phong `apps/foxia`. Không sửa test đang xanh trên master để cho qua. Không `git add -A` (symlink `node_modules` trong worktree).

**Lệnh chạy test backend** (dùng nguyên khối này ở mọi task backend — biến `$PYTEST`):

```bash
cd /home/desi/desi-order-pancake/backend
docker start desi-test-pg desi-test-redis 2>/dev/null
export POSTGRES_DB=desi_test_pancake POSTGRES_USER=desi POSTGRES_PASSWORD=ci-postgres-pass \
  POSTGRES_HOST=localhost POSTGRES_PORT=5433 \
  REDIS_HOST=localhost REDIS_PORT=6380 REDIS_PASSWORD= \
  APP_ENV=development DJANGO_DEBUG=0 LOG_FORMAT=text \
  ZENIFY_DOMAIN= ZENIFY_API_KEY= ZENIFY_API_SECRET= \
  ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
PYTEST=/home/desi/desi-order/.venv/bin/pytest
PY=/home/desi/desi-order/.venv/bin/python
```

Lần chạy đầu và mỗi lần thêm migration: `--create-db`; các lần khác `--reuse-db`. Máy chật RAM: **không** chạy hai lượt pytest song song.

**Lệnh chạy test frontend** (máy không có `node` trên PATH):

```bash
docker run --rm -v /home/desi:/home/desi -w /home/desi/desi-order-pancake/frontend \
  node:22-alpine npx vitest run <đường dẫn test>
```

Typecheck: thay `npx vitest run <path>` bằng `npm run typecheck`. Chạy **một lệnh docker tại một thời điểm**.

## File Structure

**Tạo mới**
| File | Trách nhiệm |
|---|---|
| `backend/apps/pancake/__init__.py`, `apps.py` | Đăng ký app |
| `backend/apps/pancake/models.py` | `PancakeConnection`, `PancakePage`, `PancakeLead` + choices |
| `backend/apps/pancake/migrations/0001_initial.py` | Sinh bằng `makemigrations` |
| `backend/apps/pancake/admin.py` | Soi nhật ký thô |
| `backend/apps/pancake/infrastructure/pancake_client.py` | Gọi Pancake, DTO, parser số điện thoại 2 dạng |
| `backend/apps/pancake/application/services/poll_service.py` | Poll một page: cursor, dedup, đẩy Foxia |
| `backend/apps/pancake/application/services/page_service.py` | Đồng bộ page, bật/tắt page (sinh token) |
| `backend/apps/pancake/tasks.py` | `poll_phone_leads`, `repush_lead` |
| `backend/apps/pancake/metrics.py` | Prometheus |
| `backend/apps/pancake/transport/views.py`, `urls.py` | API |
| `backend/apps/pancake/tests/test_pancake_models.py` … | Test theo từng task |
| `backend/apps/users/migrations/0049_seed_pancake_leads_permission.py` | Quyền `pancake-leads:view` |
| `frontend/src/lib/api/pancake.ts` | Client HTTP thuần |
| `frontend/src/features/pancake/queries.ts` | TanStack Query hooks |
| `frontend/src/features/pancake/pages/PancakeSettingsPage.tsx` | Cài đặt → Pancake |
| `frontend/src/features/pancake/pages/PancakeLeadsPage.tsx` | Danh sách lead |
| `frontend/src/features/pancake/__tests__/*.test.tsx` | Test 2 trang |
| `frontend/src/i18n/messages/pancake.ts` | Chuỗi vi/en |

**Sửa**
| File | Thay đổi |
|---|---|
| `backend/config/settings_parts/base.py` | `INSTALLED_APPS` + `PANCAKE_POLL_ENABLED`, `PANCAKE_HTTP_TIMEOUT` |
| `backend/.importlinter` | Thêm tầng `pancake` |
| `backend/apps/foxia/application/services/order_push_service.py` | Tách `push_customer` + `PushResult` |
| `backend/config/settings_parts/celery.py` | Route + beat |
| `backend/config/urls.py` | `api/pancake/` |
| `backend/apps/users/models.py`, `rbac_catalog.py` | Section `pancake`, `pancake-leads` |
| `backend/schema.yaml`, `frontend/src/api/schema.d.ts` | Sinh lại |
| `docker-compose.prod.yml`, `.staging.yml`, `.local.yml`, `.env.example` | Env mới |
| `frontend/src/lib/routes.ts`, `app/modules.ts`, `app/routes.tsx`, `app/routed-pages.tsx`, `app/__tests__/modules.test.ts`, `i18n/messages.ts`, `i18n/messages/topnav.ts` | Đăng ký 2 route |

---

### Task 0: Worktree

- [ ] **Step 1: Tạo worktree + nhánh**

```bash
cd /home/desi/desi-order
git worktree add /home/desi/desi-order-pancake -b feat/pancake-phone-leads master
ln -s /home/desi/desi-order/frontend/node_modules /home/desi/desi-order-pancake/frontend/node_modules
cd /home/desi/desi-order-pancake && git log --oneline -1
```
Expected: HEAD là commit spec (`docs(pancake): spec …`) hoặc mới hơn.

---

### Task 1: App `apps.pancake` — model, migration, admin, đăng ký

**Files:**
- Create: `backend/apps/pancake/__init__.py`, `apps.py`, `models.py`, `admin.py`, `migrations/__init__.py`, `tests/__init__.py`, `tests/test_pancake_models.py`
- Modify: `backend/config/settings_parts/base.py` (INSTALLED_APPS sau `"apps.media"`), `backend/.importlinter` (layers)

**Interfaces (Produces):**
- `PancakeConnection.get_active() -> PancakeConnection | None`
- `PancakePage.foxia_kind -> str | None` (property), hằng `PLATFORM_TO_FOXIA_KIND`
- `PancakePushStatus` (`pending|ok|failed|skipped_no_mapping`), `PancakeAuthStatus` (`unknown|ok|error`)
- `PancakeLead.RESPONSE_LIMIT = 2000`

- [ ] **Step 1: Viết test model (fail vì chưa có app)**

`backend/apps/pancake/tests/test_pancake_models.py`:
```python
"""Ràng buộc schema của apps.pancake — khoá chống trùng (channel, phone) và ánh
xạ platform → cột Foxia. Sai cái nào cũng hỏng im lặng: trùng thì khách bị đẩy
hai lần sang Foxia, sai cột thì cơ hội rơi vào nguồn khác."""

import pytest
from django.db import IntegrityError, transaction

from apps.channels_core.models import Channel
from apps.pancake.models import PancakeLead, PancakePage, PancakePushStatus

pytestmark = pytest.mark.django_db


def _page(**overrides):
    values = {"pancake_page_id": "945997011940587", "name": "DELAX", "platform": "facebook"}
    values.update(overrides)
    return PancakePage.objects.create(**values)


def _lead(page, channel, phone="0901234567", **overrides):
    values = {
        "page": page,
        "channel": channel,
        "conversation_id": f"{page.pancake_page_id}_1",
        "phone_raw": phone,
        "phone": phone,
        "foxia_kind": "messenger",
    }
    values.update(overrides)
    return PancakeLead.objects.create(**values)


def test_foxia_kind_theo_platform():
    assert _page().foxia_kind == "messenger"
    assert _page(pancake_page_id="zl_1", platform="zalo").foxia_kind == "zalo"
    assert _page(pancake_page_id="pzl_1", platform="personal_zalo").foxia_kind == "zalo"
    assert _page(pancake_page_id="tts_1", platform="tiktok_shop").foxia_kind is None


def test_cung_kenh_cung_sdt_khong_tao_dong_thu_hai():
    channel = Channel.objects.create(code="kenh-a", name="Kênh A")
    page = _page()
    _lead(page, channel)
    with pytest.raises(IntegrityError), transaction.atomic():
        _lead(page, channel, conversation_id="hoi-thoai-khac")


def test_cung_sdt_khac_kenh_thi_duoc():
    a = Channel.objects.create(code="kenh-a", name="Kênh A")
    b = Channel.objects.create(code="kenh-b", name="Kênh B")
    page = _page()
    _lead(page, a)
    _lead(page, b)
    assert PancakeLead.objects.count() == 2


def test_lead_mac_dinh_pending_va_tags_rong():
    channel = Channel.objects.create(code="kenh-a", name="Kênh A")
    lead = _lead(_page(), channel)
    assert lead.push_status == PancakePushStatus.PENDING
    assert lead.tags == []
    assert lead.foxia_source_id is None
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `$PYTEST apps/pancake/tests/test_pancake_models.py -q --reuse-db`
Expected: FAIL `ModuleNotFoundError: No module named 'apps.pancake'`

- [ ] **Step 3: Tạo app**

`backend/apps/pancake/__init__.py`: rỗng. `migrations/__init__.py`, `tests/__init__.py`: rỗng.

`backend/apps/pancake/apps.py`:
```python
from django.apps import AppConfig


class PancakeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.pancake"
    verbose_name = "Pancake"
```

`backend/apps/pancake/models.py`:
```python
"""Model của tích hợp Pancake — xem docs/superpowers/specs/2026-09-16-pancake-phone-leads-design.md.

Ba bảng:
  1. PancakeConnection — token tài khoản (mã hoá), thường 1 row.
  2. PancakePage — mirror danh sách page + gán kênh bán + cursor poll theo page.
  3. PancakeLead — mỗi số điện thoại khách cho trong hội thoại là một dòng, kèm
     kết quả đẩy sang Foxia. KHÔNG tạo đơn trong `orders` (quyết định D2).
"""

from django.db import models

from apps.common.fields import EncryptedCharField


class PancakeAuthStatus(models.TextChoices):
    UNKNOWN = "unknown", "Chưa kiểm tra"
    OK = "ok", "Hợp lệ"
    ERROR = "error", "Lỗi"


class PancakeConnection(models.Model):
    """Token của MỘT tài khoản Pancake có quyền Admin trên các page.

    Token nằm trong DB mã hoá (cùng cách FoxiaConnection.api_key), không ở env:
    đây là secret của đối tác và người vận hành xoay nó qua UI, không qua deploy.
    """

    name = models.CharField(max_length=255, default="Pancake")
    access_token = EncryptedCharField(max_length=1024)
    is_active = models.BooleanField(default=True)
    auth_status = models.CharField(
        max_length=16, choices=PancakeAuthStatus.choices, default=PancakeAuthStatus.UNKNOWN
    )
    auth_error = models.TextField(blank=True, default="")
    last_pages_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pancake_connections"

    def __str__(self) -> str:
        return self.name

    @classmethod
    def get_active(cls) -> "PancakeConnection | None":
        return cls.objects.filter(is_active=True).order_by("-updated_at").first()


# Platform Pancake → cột của bảng liên kết Foxia (`FoxiaLinkKind`). Page nền tảng
# khác (tiktok_shop, shopee_official…) không có cột → không gán kênh được (D4).
PLATFORM_TO_FOXIA_KIND: dict[str, str] = {
    "facebook": "messenger",
    "zalo": "zalo",
    "personal_zalo": "zalo",
}


class PancakePage(models.Model):
    """Mirror một page Pancake + cấu hình vận hành của ta trên page đó.

    `page_access_token` sinh khi BẬT page (cần quyền Admin bên Pancake), xoá khi
    Pancake từ chối. `polled_until` là cursor theo `updated_at` hội thoại — chỉ
    tiến khi lượt poll page đó không lỗi.
    """

    pancake_page_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255, blank=True, default="")
    platform = models.CharField(max_length=32, blank=True, default="")
    is_activated = models.BooleanField(default=True)
    has_active_subscription = models.BooleanField(default=False)
    role_in_page = models.CharField(max_length=32, blank=True, default="")
    synced_at = models.DateTimeField(auto_now=True)

    page_access_token = EncryptedCharField(max_length=2048, blank=True, default="")
    token_error = models.TextField(blank=True, default="")

    channel = models.ForeignKey(
        "channels_core.Channel", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="pancake_pages",
    )
    is_enabled = models.BooleanField(default=False)
    polled_until = models.DateTimeField(null=True, blank=True)
    last_polled_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "pancake_pages"
        ordering = ["name", "id"]

    def __str__(self) -> str:
        return f"{self.name or self.pancake_page_id} ({self.platform})"

    @property
    def foxia_kind(self) -> str | None:
        return PLATFORM_TO_FOXIA_KIND.get(self.platform)


class PancakePushStatus(models.TextChoices):
    PENDING = "pending", "Chờ đẩy"
    OK = "ok", "Thành công"
    FAILED = "failed", "Thất bại"
    SKIPPED_NO_MAPPING = "skipped_no_mapping", "Bỏ qua — chưa cấu hình id"


class PancakeLead(models.Model):
    """Một số điện thoại khách cho trong một hội thoại Pancake.

    `channel`/`foxia_kind` là SNAPSHOT lúc bắt số: đổi kênh của page sau này
    không đổi nghĩa lead cũ. `UNIQUE (channel, phone)` là khoá chống trùng D3 —
    ở DB, không ở code.
    """

    RESPONSE_LIMIT = 2000

    page = models.ForeignKey(PancakePage, on_delete=models.PROTECT, related_name="leads")
    channel = models.ForeignKey(
        "channels_core.Channel", on_delete=models.PROTECT, related_name="pancake_leads"
    )
    conversation_id = models.CharField(max_length=128, db_index=True)
    conversation_inserted_at = models.DateTimeField(null=True, blank=True)
    pancake_customer_id = models.CharField(max_length=64, blank=True, default="")
    customer_name = models.CharField(max_length=255, blank=True, default="")
    phone_raw = models.CharField(max_length=32)
    phone = models.CharField(max_length=16)
    tags = models.JSONField(default=list, blank=True)
    phone_given_at = models.DateTimeField(null=True, blank=True)
    assignee_name = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    foxia_kind = models.CharField(max_length=16)
    foxia_source_id = models.BigIntegerField(null=True, blank=True)
    push_status = models.CharField(
        max_length=32, choices=PancakePushStatus.choices, default=PancakePushStatus.PENDING
    )
    http_status = models.IntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True, default="")
    error = models.TextField(blank=True, default="")
    pushed_at = models.DateTimeField(null=True, blank=True)
    cohoi_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    khachhang_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        db_table = "pancake_leads"
        constraints = [
            models.UniqueConstraint(fields=["channel", "phone"], name="pancake_lead_channel_phone_uniq"),
        ]
        indexes = [
            models.Index(fields=["page", "-created_at"], name="pancake_lead_page_created_idx"),
            models.Index(fields=["push_status", "-created_at"], name="pancake_lead_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.phone} @ {self.page_id} ({self.push_status})"
```

`backend/apps/pancake/admin.py`:
```python
from django.contrib import admin

from apps.pancake.models import PancakeConnection, PancakeLead, PancakePage


@admin.register(PancakeConnection)
class PancakeConnectionAdmin(admin.ModelAdmin):
    list_display = ("name", "auth_status", "is_active", "last_pages_synced_at", "updated_at")
    readonly_fields = ("auth_status", "auth_error", "last_pages_synced_at")


@admin.register(PancakePage)
class PancakePageAdmin(admin.ModelAdmin):
    list_display = ("name", "pancake_page_id", "platform", "channel", "is_enabled", "polled_until", "last_polled_at")
    list_filter = ("platform", "is_enabled", "is_activated")
    search_fields = ("name", "pancake_page_id")
    # Token/cursor do hệ thống ghi; sửa tay chỉ tạo lệch với Pancake.
    readonly_fields = ("page_access_token", "token_error", "polled_until", "last_polled_at", "last_error", "synced_at")


@admin.register(PancakeLead)
class PancakeLeadAdmin(admin.ModelAdmin):
    list_display = ("phone", "customer_name", "page", "channel", "push_status", "cohoi_id", "created_at")
    list_filter = ("push_status", "page")
    search_fields = ("phone", "customer_name", "conversation_id")
    readonly_fields = ("response_body", "error", "http_status", "pushed_at", "created_at")
```

- [ ] **Step 4: Đăng ký app + tầng import**

`backend/config/settings_parts/base.py`: trong `INSTALLED_APPS`, thêm `"apps.pancake",` ngay sau `"apps.media",`.

`backend/.importlinter`, khối `layers =`: thêm dòng `pancake` NGAY TRÊN dòng `kiotviet | facebook_ads | google_ads | best_express | cnv_loyalty | foxia | media` (pancake import foxia + customers + channels_core + users — đều tầng dưới; `exhaustive = true` nên thiếu dòng này contract FAIL):
```
    warranty
    pancake
    kiotviet | facebook_ads | google_ads | best_express | cnv_loyalty | foxia | media
```

- [ ] **Step 5: Sinh migration**

Run: `$PY manage.py makemigrations pancake --name initial`
Expected: tạo `apps/pancake/migrations/0001_initial.py` với 3 model, constraint `pancake_lead_channel_phone_uniq`, 2 index.

- [ ] **Step 6: Chạy test, xác nhận pass**

Run: `$PYTEST apps/pancake/tests/test_pancake_models.py -q --create-db`
Expected: 4 passed

- [ ] **Step 7: Lint tầng import**

Run: `cd /home/desi/desi-order-pancake/backend && /home/desi/desi-order/.venv/bin/lint-imports`
Expected: `Contracts: 1 kept, 0 broken.`

- [ ] **Step 8: Commit**

```bash
git add backend/apps/pancake backend/config/settings_parts/base.py backend/.importlinter
git commit -m "feat(pancake): app mới — model kết nối, page, lead + migration 0001"
```

---

### Task 2: Tách `push_customer` khỏi `FoxiaOrderPushService`

**Files:**
- Modify: `backend/apps/foxia/application/services/order_push_service.py`
- Test: `backend/apps/foxia/tests/test_foxia_push_customer.py` (mới); test cũ `test_foxia_order_push.py`, `test_foxia_lead_link.py` phải xanh nguyên.

**Interfaces (Produces):**
```python
@dataclass(frozen=True)
class PushResult:
    url: str
    http_status: int | None
    response_body: str      # đã cắt RESPONSE_LIMIT
    error: str
    duration_ms: int
    cohoi_id: int | None
    khachhang_id: int | None
    @property
    def ok(self) -> bool

def push_customer(source_id: int, payload: dict) -> PushResult
```
`HttpClient` vẫn được tham chiếu qua `apps.foxia.application.services.order_push_service.HttpClient` (test cũ patch đường này).

- [ ] **Step 1: Viết test mới**

`backend/apps/foxia/tests/test_foxia_push_customer.py`:
```python
"""`push_customer` — hàm thuần POST một khách vào `{base}/{source_id}`, dùng chung
cho đẩy đơn landing và đẩy lead Pancake. Không raise, không retry, đọc được
`cohoi_id` khi Foxia trả JSON đúng dạng, và cắt phản hồi về RESPONSE_LIMIT."""

from unittest.mock import MagicMock, patch

import pytest
import requests
from django.test import override_settings

from apps.foxia.application.services.order_push_service import PushResult, push_customer

PUSH_ON = override_settings(
    FOXIA_PUSH_BASE_URL="https://foxia.test/duongdankhachhang", FOXIA_PUSH_TIMEOUT=10.0
)
MODULE = "apps.foxia.application.services.order_push_service.HttpClient"


def _response(status_code=200, text="OK"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


@PUSH_ON
def test_thanh_cong_doc_duoc_cohoi_id():
    with patch(MODULE) as client_cls:
        post = client_cls.return_value.__enter__.return_value.post
        post.return_value = _response(text='{"success": true, "data": {"khachhang_id": 7, "cohoi_id": 99}}')
        result = push_customer(1234, {"name": "A", "phone": "0901234567", "address": ""})

    assert isinstance(result, PushResult)
    assert result.ok is True
    assert (result.url, result.http_status) == ("https://foxia.test/duongdankhachhang/1234", 200)
    assert (result.cohoi_id, result.khachhang_id) == (99, 7)
    assert post.call_args.kwargs["json"] == {"name": "A", "phone": "0901234567", "address": ""}
    # Không retry: POST không idempotent.
    assert client_cls.call_args.kwargs["retries"] == 0


@PUSH_ON
def test_phan_hoi_khong_phai_json_van_ok_nhung_khong_co_cohoi():
    with patch(MODULE) as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = _response(text="<html>ok</html>")
        result = push_customer(1, {})
    assert result.ok is True
    assert result.cohoi_id is None


@PUSH_ON
def test_http_500_la_failed_va_cat_body():
    with patch(MODULE) as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = _response(500, "x" * 5000)
        result = push_customer(1, {})
    assert result.ok is False
    assert result.http_status == 500
    assert len(result.response_body) == 2000


@PUSH_ON
def test_loi_mang_khong_raise():
    with patch(MODULE) as client_cls:
        client_cls.return_value.__enter__.return_value.post.side_effect = requests.ConnectionError("boom")
        result = push_customer(1, {})
    assert result.ok is False
    assert result.http_status is None
    assert result.error.startswith("ConnectionError")
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `$PYTEST apps/foxia/tests/test_foxia_push_customer.py -q --reuse-db`
Expected: FAIL `ImportError: cannot import name 'PushResult'`

- [ ] **Step 3: Refactor `order_push_service.py`**

Thêm sau `build_payload` (giữ nguyên docstring module, `build_address`, `build_skus`, `build_payload`):
```python
@dataclass(frozen=True)
class PushResult:
    """Kết quả MỘT lần POST khách sang Foxia — thuần dữ liệu, không biết đơn hay lead."""

    url: str
    http_status: int | None
    response_body: str
    error: str
    duration_ms: int
    cohoi_id: int | None
    khachhang_id: int | None

    @property
    def ok(self) -> bool:
        return self.http_status is not None and 200 <= self.http_status < 300


def _parse_lead_ids(raw_body: str) -> tuple[int | None, int | None]:
    """`data.cohoi_id` / `data.khachhang_id` từ phản hồi Foxia; sai dạng → (None, None).

    Hợp đồng phản hồi chưa được chốt bằng văn bản, nên MỌI sai lệch (không phải
    JSON, thiếu khoá) chỉ là "không có id để ghi" — không phải lỗi.
    """
    try:
        data = json.loads(raw_body).get("data") or {}
        cohoi_id = int(data["cohoi_id"])
        khachhang_id = data.get("khachhang_id")
        return cohoi_id, (int(khachhang_id) if khachhang_id is not None else None)
    except Exception:  # noqa: BLE001 — xem docstring
        return None, None


def push_customer(source_id: int, payload: dict) -> PushResult:
    """POST `payload` tới `{FOXIA_PUSH_BASE_URL}/{source_id}`. Không retry, không raise.

    Dùng chung cho đẩy đơn landing (`FoxiaOrderPushService`) và đẩy lead Pancake
    (`apps.pancake`). Bên gọi tự ghi nhật ký vào bảng của mình.
    """
    base = str(getattr(settings, "FOXIA_PUSH_BASE_URL", "") or "").rstrip("/")
    url = f"{base}/{source_id}"
    limit = FOXIA_RESPONSE_LIMIT

    started = time.monotonic()
    http_status = None
    raw_body = ""
    error = ""
    try:
        with HttpClient(
            timeout=float(getattr(settings, "FOXIA_PUSH_TIMEOUT", 10.0)),
            retries=0,
            default_headers={"Content-Type": "application/json"},
        ) as client:
            response = client.post(url, json=payload)
            http_status = response.status_code
            raw_body = str(response.text or "")
    except Exception as exc:  # noqa: BLE001 — lỗi đối tác không được thoát ra task
        error = f"{type(exc).__name__}: {exc}"[:limit]

    duration_ms = int((time.monotonic() - started) * 1000)
    ok = http_status is not None and 200 <= http_status < 300
    cohoi_id, khachhang_id = _parse_lead_ids(raw_body) if ok else (None, None)
    return PushResult(
        url=url,
        http_status=http_status,
        response_body=raw_body[:limit],
        error=error,
        duration_ms=duration_ms,
        cohoi_id=cohoi_id,
        khachhang_id=khachhang_id,
    )
```
Thêm `from dataclasses import dataclass` vào import; thêm hằng module `FOXIA_RESPONSE_LIMIT = 2000` (trùng `FoxiaOrderPush.RESPONSE_LIMIT`; không import model ở mức module để giữ lazy import như hiện tại).

Sửa `_record_lead(order, raw_body)` → `_record_lead(order, result: PushResult)`:
```python
def _record_lead(order, result: PushResult) -> None:
    """Ghi cơ hội Foxia vừa tạo. Không có `cohoi_id` → không có gì để ghi, không phải lỗi."""
    from apps.foxia.models import FoxiaLeadLink

    if result.cohoi_id is None:
        logger.warning("Foxia push: order=%s khong doc duoc cohoi_id tu phan hoi", order.id)
        return
    FoxiaLeadLink.objects.get_or_create(
        cohoi_id=result.cohoi_id,
        defaults={"order": order, "khachhang_id": result.khachhang_id},
    )
```

Trong `FoxiaOrderPushService.push`, thay toàn bộ đoạn từ `base = str(getattr(settings, "FOXIA_PUSH_BASE_URL"...` tới trước `FoxiaOrderPush.objects.create(` bằng:
```python
        payload = build_payload(order)
        result = push_customer(source_id, payload)
        status = FoxiaPushStatus.OK if result.ok else FoxiaPushStatus.FAILED
```
và sửa lời gọi tạo log + phần sau:
```python
        FoxiaOrderPush.objects.create(
            order=order,
            status=status,
            foxia_source_id=source_id,
            url=result.url,
            request_body=payload,
            http_status=result.http_status,
            response_body=result.response_body,
            error=result.error,
            duration_ms=result.duration_ms,
        )
        if result.ok:
            _record_lead(order, result)
        (logger.info if result.ok else logger.warning)(
            "Foxia push %s: order=%s kenh=%s nguon_don=%s sku=%s nguon=%s http=%s %sms url=%s%s",
            status, order_id, order.channel.code, kind, payload.get("message") or "-", source_id,
            result.http_status, result.duration_ms, result.url,
            f" error={result.error}" if result.error else "",
        )
        return status
```
Xoá các biến cục bộ không còn dùng (`started`, `raw_body`, `response_body`, `error`, `http_status`, `limit`, `url`, `base`, `duration_ms`, `ok`) và `import json`/`time` chỉ giữ nếu còn dùng (cả hai vẫn dùng trong `push_customer`/`_parse_lead_ids`).

- [ ] **Step 4: Chạy test mới + toàn bộ test foxia**

Run: `$PYTEST apps/foxia/tests/test_foxia_push_customer.py apps/foxia/tests/test_foxia_order_push.py apps/foxia/tests/test_foxia_lead_link.py -q --reuse-db`
Expected: tất cả passed, không sửa test cũ.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/foxia
git commit -m "refactor(foxia): tách push_customer + PushResult khỏi FoxiaOrderPushService để dùng chung"
```

---

### Task 3: `PancakeClient` — gọi Pancake + parser

**Files:**
- Create: `backend/apps/pancake/infrastructure/__init__.py`, `backend/apps/pancake/infrastructure/pancake_client.py`
- Test: `backend/apps/pancake/tests/test_pancake_client.py`

**Interfaces (Produces):**
```python
class PancakeError(Exception)
class PancakeTokenError(PancakeError)          # sinh token page thất bại (message của Pancake)
class PancakePageTokenRejected(PancakeError)   # token page bị từ chối khi gọi API page

@dataclass(frozen=True) class PageDTO: id, name, platform, is_activated, has_active_subscription, role_in_page
@dataclass(frozen=True) class ConversationDTO: id, type, inserted_at, updated_at, has_phone, phones: list[str], customer_name, customer_id, tags: list[str], assignee_name
@dataclass(frozen=True) class ConversationPage: total: int; items: list[ConversationDTO]
@dataclass(frozen=True) class ConversationDetail: phones: list[str], customer_name, customer_id, phone_message_at: datetime | None

parse_pancake_time(value) -> datetime | None       # naive ISO → UTC-aware
extract_phones(value) -> list[str]                  # chuỗi | object | list hỗn hợp → list[str] thô (chưa chuẩn hoá)

class PancakeClient:
    ACCOUNT_BASE = "https://pages.fm/api/v1"
    PAGE_BASE = "https://pages.fm/api/public_api/v1"
    def __init__(self, access_token: str, timeout: float | None = None)
    def list_pages(self) -> list[PageDTO]
    def generate_page_token(self, page_id: str) -> str
    def list_conversations(self, page_id, page_token, since: datetime, until: datetime, page_number: int) -> ConversationPage
    def get_conversation_detail(self, page_id, page_token, conversation_id) -> ConversationDetail
```

- [ ] **Step 1: Viết test**

`backend/apps/pancake/tests/test_pancake_client.py`:
```python
"""Client Pancake — hợp đồng đã kiểm chứng ở repo desi-crawl-conversations
(docs/pancake-api-verified.md, 2026-06-26). Ba bẫy được chốt ở đây:
1. Sinh token page phải POST kèm Content-Type JSON + body {} (thiếu → 500).
2. `recent_phone_numbers` có 2 dạng phần tử: chuỗi hoặc object {phone_number}.
3. Timestamp Pancake không có múi giờ → UTC.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from apps.pancake.infrastructure.pancake_client import (
    PancakeClient,
    PancakePageTokenRejected,
    PancakeTokenError,
    extract_phones,
    parse_pancake_time,
)

MODULE = "apps.pancake.infrastructure.pancake_client.HttpClient"


def _resp(payload, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.text = str(payload)
    return resp


def test_parse_time_naive_la_utc():
    assert parse_pancake_time("2026-06-14T02:00:56.667139") == datetime(
        2026, 6, 14, 2, 0, 56, 667139, tzinfo=timezone.utc
    )
    assert parse_pancake_time("2026-06-14T02:00:56") == datetime(2026, 6, 14, 2, 0, 56, tzinfo=timezone.utc)
    assert parse_pancake_time("") is None
    assert parse_pancake_time(None) is None
    assert parse_pancake_time("rác") is None


def test_extract_phones_hai_dang():
    assert extract_phones(["0901234567", {"phone_number": "0912345678"}, {"number": "0923456789"}, None, 5]) == [
        "0901234567", "0912345678", "0923456789"
    ]
    assert extract_phones(None) == []
    assert extract_phones("0901234567") == ["0901234567"]


def test_list_pages_chi_lay_activated():
    payload = {
        "success": True,
        "categorized": {
            "activated": [
                {"id": "945997011940587", "name": "DELAX", "platform": "facebook", "is_activated": True,
                 "has_active_subscription": True, "role_in_page": "ADMINISTER"},
            ],
            "hidden": [{"id": "x", "name": "Ẩn", "platform": "facebook"}],
        },
    }
    with patch(MODULE) as client_cls:
        get = client_cls.return_value.__enter__.return_value.get
        get.return_value = _resp(payload)
        pages = PancakeClient("tok").list_pages()

    assert [p.id for p in pages] == ["945997011940587"]
    assert pages[0].role_in_page == "ADMINISTER"
    assert get.call_args.args[0] == "https://pages.fm/api/v1/pages"
    assert get.call_args.kwargs["params"] == {"access_token": "tok"}


def test_list_pages_success_false_raise():
    with patch(MODULE) as client_cls:
        client_cls.return_value.__enter__.return_value.get.return_value = _resp({"success": False, "message": "Invalid token"})
        with pytest.raises(PancakeTokenError, match="Invalid token"):
            PancakeClient("tok").list_pages()


def test_generate_page_token_post_json_body_rong():
    with patch(MODULE) as client_cls:
        post = client_cls.return_value.__enter__.return_value.post
        post.return_value = _resp({"success": True, "page_access_token": "ptok", "message": "Tạo mới token thành công"})
        assert PancakeClient("tok").generate_page_token("945997011940587") == "ptok"

    assert post.call_args.args[0] == "https://pages.fm/api/v1/pages/945997011940587/generate_page_access_token"
    assert post.call_args.kwargs["params"] == {"access_token": "tok"}
    assert post.call_args.kwargs["json"] == {}


def test_generate_page_token_thieu_quyen_raise_message_pancake():
    with patch(MODULE) as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = _resp(
            {"success": False, "message": "Thiếu quyền Admin", "error_code": None}
        )
        with pytest.raises(PancakeTokenError, match="Thiếu quyền Admin"):
            PancakeClient("tok").generate_page_token("1")


def test_list_conversations_map_dto():
    payload = {
        "success": True,
        "total": 1,
        "conversations": [
            {
                "id": "945997011940587_27427851286867310",
                "type": "INBOX",
                "inserted_at": "2026-06-14T02:00:56.667139",
                "updated_at": "2026-06-14T02:03:04.381000",
                "has_phone": True,
                "recent_phone_numbers": [{"phone_number": "0901234567"}],
                "tags": [{"id": 46, "text": "BOT-BNC02"}],
                "customers": [{"id": "uuid-1", "name": "Tuan Anh Nguyen"}],
                "customer_id": "uuid-1",
                "current_assign_users": [{"id": "u1", "name": "Nhung HN"}],
            }
        ],
    }
    since = datetime(2026, 6, 1, tzinfo=timezone.utc)
    until = datetime(2026, 6, 15, tzinfo=timezone.utc)
    with patch(MODULE) as client_cls:
        get = client_cls.return_value.__enter__.return_value.get
        get.return_value = _resp(payload)
        page = PancakeClient("tok").list_conversations("945997011940587", "ptok", since, until, 2)

    assert page.total == 1
    conv = page.items[0]
    assert conv.id == "945997011940587_27427851286867310"
    assert conv.has_phone is True
    assert conv.phones == ["0901234567"]
    assert conv.tags == ["BOT-BNC02"]
    assert conv.customer_name == "Tuan Anh Nguyen"
    assert conv.assignee_name == "Nhung HN"
    assert conv.updated_at == datetime(2026, 6, 14, 2, 3, 4, 381000, tzinfo=timezone.utc)
    assert get.call_args.args[0] == "https://pages.fm/api/public_api/v1/pages/945997011940587/conversations"
    assert get.call_args.kwargs["params"] == {
        "page_access_token": "ptok",
        "since": int(since.timestamp()),
        "until": int(until.timestamp()),
        "page_number": 2,
        "sort_by": "updated_at",
    }


def test_list_conversations_token_bi_tu_choi():
    with patch(MODULE) as client_cls:
        client_cls.return_value.__enter__.return_value.get.return_value = _resp({"success": False, "message": "Invalid page_access_token"}, 401)
        with pytest.raises(PancakePageTokenRejected):
            PancakeClient("tok").list_conversations("1", "bad", datetime.now(timezone.utc), datetime.now(timezone.utc), 1)


def test_get_conversation_detail_lay_phone_va_tin_nhan_co_sdt_moi_nhat():
    payload = {
        "success": True,
        "customers": [{"id": "uuid-1", "name": "Tuan Anh", "recent_phone_numbers": ["0901234567", "0912345678"]}],
        "messages": [
            {"id": "m1", "inserted_at": "2026-06-14T02:00:49.000000", "has_phone": False},
            {"id": "m2", "inserted_at": "2026-06-14T02:01:10.000000", "has_phone": True},
            {"id": "m3", "inserted_at": "2026-06-14T02:02:00.000000", "has_phone": True},
            {"id": "m4", "inserted_at": "2026-06-14T02:03:00.000000", "has_phone": False},
        ],
    }
    with patch(MODULE) as client_cls:
        get = client_cls.return_value.__enter__.return_value.get
        get.return_value = _resp(payload)
        detail = PancakeClient("tok").get_conversation_detail("1", "ptok", "1_2")

    assert detail.phones == ["0901234567", "0912345678"]
    assert detail.customer_name == "Tuan Anh"
    assert detail.phone_message_at == datetime(2026, 6, 14, 2, 2, 0, tzinfo=timezone.utc)
    assert get.call_args.args[0] == "https://pages.fm/api/public_api/v1/pages/1/conversations/1_2/messages"
    assert get.call_args.kwargs["params"] == {"page_access_token": "ptok", "current_count": 0}


def test_get_conversation_detail_khong_tin_nao_co_sdt():
    payload = {"success": True, "customers": [], "messages": [{"id": "m1", "inserted_at": "2026-06-14T02:00:49", "has_phone": False}]}
    with patch(MODULE) as client_cls:
        client_cls.return_value.__enter__.return_value.get.return_value = _resp(payload)
        detail = PancakeClient("tok").get_conversation_detail("1", "ptok", "1_2")
    assert detail.phones == []
    assert detail.phone_message_at is None
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `$PYTEST apps/pancake/tests/test_pancake_client.py -q --reuse-db`
Expected: FAIL `ModuleNotFoundError: apps.pancake.infrastructure`

- [ ] **Step 3: Viết client**

`backend/apps/pancake/infrastructure/__init__.py`: rỗng.

`backend/apps/pancake/infrastructure/pancake_client.py`:
```python
"""Client Pancake (pages.fm) — hợp đồng kiểm chứng 2026-06-26 ở repo
desi-crawl-conversations (`docs/pancake-api-verified.md`).

Hai namespace, xác thực qua QUERY STRING (không header):
- Account API `https://pages.fm/api/v1`: list page, sinh token page (`access_token`).
- Page API `https://pages.fm/api/public_api/v1`: hội thoại, tin nhắn (`page_access_token`).

Bẫy đã biết:
- Sinh token page PHẢI là POST với `Content-Type: application/json` + body `{}`;
  thiếu header thì Pancake trả HTTP 500.
- `since/until` lọc theo `inserted_at` (ngày TẠO hội thoại), không theo
  `updated_at`; kết quả trả DESC theo `updated_at` khi `sort_by=updated_at`.
- `recent_phone_numbers` phần tử là chuỗi HOẶC object `{phone_number}` tuỳ page.
- Timestamp không có múi giờ → UTC (đo bằng token thật ở crawler).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from django.conf import settings

from apps.common.http_client import HttpClient


class PancakeError(Exception):
    """Lỗi chung khi gọi Pancake."""


class PancakeTokenError(PancakeError):
    """Token tài khoản không dùng được / không sinh được token page (mang message của Pancake)."""


class PancakePageTokenRejected(PancakeError):
    """Token page bị từ chối — bên gọi sinh lại token một lần rồi mới tắt page."""


@dataclass(frozen=True)
class PageDTO:
    id: str
    name: str
    platform: str
    is_activated: bool
    has_active_subscription: bool
    role_in_page: str


@dataclass(frozen=True)
class ConversationDTO:
    id: str
    type: str
    inserted_at: datetime | None
    updated_at: datetime | None
    has_phone: bool
    phones: list[str] = field(default_factory=list)
    customer_name: str = ""
    customer_id: str = ""
    tags: list[str] = field(default_factory=list)
    assignee_name: str = ""


@dataclass(frozen=True)
class ConversationPage:
    total: int
    items: list[ConversationDTO]


@dataclass(frozen=True)
class ConversationDetail:
    phones: list[str]
    customer_name: str
    customer_id: str
    # `inserted_at` của tin nhắn MỚI NHẤT có `has_phone` trong 30 tin trả về — là
    # "Ngày cho số điện thoại". Không có tin nào → None, bên gọi rơi về updated_at.
    phone_message_at: datetime | None


_TIME_FORMATS = ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S")


def parse_pancake_time(value) -> datetime | None:
    """Chuỗi ISO không múi giờ của Pancake → datetime UTC-aware; rác → None."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def extract_phones(value) -> list[str]:
    """`recent_phone_numbers` → list chuỗi THÔ (chưa chuẩn hoá), giữ thứ tự, bỏ rác."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    phones: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("phone_number") or item.get("number") or "").strip()
        else:
            continue
        if text:
            phones.append(text)
    return phones


def _first_customer(raw: dict) -> dict:
    customers = raw.get("customers") or []
    return customers[0] if customers and isinstance(customers[0], dict) else {}


def _assignee_name(raw: dict) -> str:
    """Tên nhân viên đang được gán hội thoại. Hình dạng `current_assign_users` chưa
    kiểm chứng bằng token thật — đọc `name` nếu có, rỗng nếu không (chỉ hiển thị)."""
    users = raw.get("current_assign_users") or []
    for user in users:
        if isinstance(user, dict) and user.get("name"):
            return str(user["name"]).strip()
    return ""


def _conversation_dto(raw: dict) -> ConversationDTO:
    customer = _first_customer(raw)
    return ConversationDTO(
        id=str(raw.get("id") or ""),
        type=str(raw.get("type") or ""),
        inserted_at=parse_pancake_time(raw.get("inserted_at")),
        updated_at=parse_pancake_time(raw.get("updated_at")),
        has_phone=bool(raw.get("has_phone")),
        phones=extract_phones(raw.get("recent_phone_numbers")),
        customer_name=str(customer.get("name") or "").strip(),
        customer_id=str(raw.get("customer_id") or customer.get("id") or ""),
        tags=[str(t.get("text")).strip() for t in (raw.get("tags") or []) if isinstance(t, dict) and t.get("text")],
        assignee_name=_assignee_name(raw),
    )


class PancakeClient:
    ACCOUNT_BASE = "https://pages.fm/api/v1"
    PAGE_BASE = "https://pages.fm/api/public_api/v1"

    def __init__(self, access_token: str, timeout: float | None = None):
        self._access_token = access_token
        self._timeout = float(timeout or getattr(settings, "PANCAKE_HTTP_TIMEOUT", 15.0))

    def _client(self) -> HttpClient:
        # GET idempotent, POST sinh token cũng idempotent (chỉ tạo token mới) → retry được.
        return HttpClient(timeout=self._timeout, retries=2)

    @staticmethod
    def _json(response) -> dict:
        try:
            data = response.json()
        except ValueError as exc:
            raise PancakeError(f"Pancake trả không phải JSON (HTTP {response.status_code})") from exc
        return data if isinstance(data, dict) else {}

    # ── Account API ──────────────────────────────────────────────────────────

    def list_pages(self) -> list[PageDTO]:
        with self._client() as client:
            response = client.get(f"{self.ACCOUNT_BASE}/pages", params={"access_token": self._access_token})
        data = self._json(response)
        if not data.get("success"):
            raise PancakeTokenError(str(data.get("message") or f"HTTP {response.status_code}"))
        pages = []
        for raw in (data.get("categorized") or {}).get("activated") or []:
            pages.append(
                PageDTO(
                    id=str(raw.get("id") or ""),
                    name=str(raw.get("name") or ""),
                    platform=str(raw.get("platform") or ""),
                    is_activated=bool(raw.get("is_activated", True)),
                    has_active_subscription=bool(raw.get("has_active_subscription")),
                    role_in_page=str(raw.get("role_in_page") or ""),
                )
            )
        return pages

    def generate_page_token(self, page_id: str) -> str:
        with self._client() as client:
            response = client.post(
                f"{self.ACCOUNT_BASE}/pages/{page_id}/generate_page_access_token",
                params={"access_token": self._access_token},
                json={},
                headers={"Content-Type": "application/json"},
            )
        data = self._json(response)
        token = str(data.get("page_access_token") or "")
        if not data.get("success") or not token:
            raise PancakeTokenError(str(data.get("message") or f"HTTP {response.status_code}"))
        return token

    # ── Page API ─────────────────────────────────────────────────────────────

    def _page_get(self, url: str, params: dict) -> dict:
        with self._client() as client:
            response = client.get(url, params=params)
        if response.status_code in (401, 403):
            raise PancakePageTokenRejected(f"HTTP {response.status_code}")
        data = self._json(response)
        if not data.get("success"):
            message = str(data.get("message") or f"HTTP {response.status_code}")
            if "token" in message.lower():
                raise PancakePageTokenRejected(message)
            raise PancakeError(message)
        return data

    def list_conversations(
        self, page_id: str, page_token: str, since: datetime, until: datetime, page_number: int
    ) -> ConversationPage:
        data = self._page_get(
            f"{self.PAGE_BASE}/pages/{page_id}/conversations",
            {
                "page_access_token": page_token,
                "since": int(since.timestamp()),
                "until": int(until.timestamp()),
                "page_number": page_number,
                "sort_by": "updated_at",
            },
        )
        items = [_conversation_dto(raw) for raw in data.get("conversations") or [] if isinstance(raw, dict)]
        return ConversationPage(total=int(data.get("total") or 0), items=items)

    def get_conversation_detail(self, page_id: str, page_token: str, conversation_id: str) -> ConversationDetail:
        data = self._page_get(
            f"{self.PAGE_BASE}/pages/{page_id}/conversations/{conversation_id}/messages",
            {"page_access_token": page_token, "current_count": 0},
        )
        customer = _first_customer(data)
        phone_message_at = None
        for message in data.get("messages") or []:
            if isinstance(message, dict) and message.get("has_phone"):
                when = parse_pancake_time(message.get("inserted_at"))
                if when and (phone_message_at is None or when > phone_message_at):
                    phone_message_at = when
        return ConversationDetail(
            phones=extract_phones(customer.get("recent_phone_numbers")),
            customer_name=str(customer.get("name") or "").strip(),
            customer_id=str(customer.get("customer_id") or customer.get("id") or ""),
            phone_message_at=phone_message_at,
        )
```

- [ ] **Step 4: Chạy test, xác nhận pass**

Run: `$PYTEST apps/pancake/tests/test_pancake_client.py -q --reuse-db`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add backend/apps/pancake
git commit -m "feat(pancake): client Pancake — list page, sinh token page, hội thoại, chi tiết + parser sđt 2 dạng"
```

---

### Task 4: `PancakePageService` — đồng bộ page, bật/tắt page

**Files:**
- Create: `backend/apps/pancake/application/__init__.py`, `application/services/__init__.py`, `application/services/page_service.py`
- Test: `backend/apps/pancake/tests/test_pancake_page_service.py`

**Interfaces:**
- Consumes: `PancakeClient` (Task 3), model (Task 1).
- Produces:
```python
def get_client(connection: PancakeConnection | None = None) -> PancakeClient | None   # None khi chưa có connection active
class PageNotEligible(Exception)   # message tiếng Việt cho UI
@dataclass(frozen=True) class SyncPagesResult: fetched: int; created: int; updated: int; deactivated: int
class PancakePageService:
    def __init__(self, client: PancakeClient)
    def sync_pages(self, connection: PancakeConnection) -> SyncPagesResult
    def enable_page(self, page: PancakePage) -> PancakePage     # raise PageNotEligible / PancakeTokenError
```
Tắt page chỉ là `is_enabled=False` (giữ token + cursor) — làm thẳng ở view, không cần service.

- [ ] **Step 1: Viết test**

`backend/apps/pancake/tests/test_pancake_page_service.py`:
```python
"""Đồng bộ page (upsert theo pancake_page_id, page biến mất → is_activated=False,
KHÔNG xoá) và bật page (cần kênh + platform hỗ trợ + sinh được token; cursor =
now vì không backfill)."""

from unittest.mock import MagicMock

import pytest
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.pancake.application.services.page_service import (
    PageNotEligible,
    PancakePageService,
    get_client,
)
from apps.pancake.infrastructure.pancake_client import PageDTO, PancakeTokenError
from apps.pancake.models import PancakeConnection, PancakePage

pytestmark = pytest.mark.django_db


def _dto(id="945997011940587", name="DELAX", platform="facebook", **kw):
    values = {"is_activated": True, "has_active_subscription": True, "role_in_page": "ADMINISTER"}
    values.update(kw)
    return PageDTO(id=id, name=name, platform=platform, **values)


def test_get_client_none_khi_chua_co_connection():
    assert get_client() is None
    PancakeConnection.objects.create(access_token="tok")
    assert get_client() is not None


def test_sync_pages_upsert_va_danh_dau_page_bien_mat():
    conn = PancakeConnection.objects.create(access_token="tok")
    PancakePage.objects.create(pancake_page_id="old", name="Cũ", platform="facebook")
    client = MagicMock()
    client.list_pages.return_value = [_dto(), _dto(id="zl_1", name="Zalo OA", platform="zalo")]

    result = PancakePageService(client).sync_pages(conn)

    assert (result.fetched, result.created, result.updated, result.deactivated) == (2, 2, 0, 1)
    assert PancakePage.objects.get(pancake_page_id="old").is_activated is False
    page = PancakePage.objects.get(pancake_page_id="945997011940587")
    assert (page.name, page.platform, page.role_in_page, page.is_activated) == ("DELAX", "facebook", "ADMINISTER", True)
    conn.refresh_from_db()
    assert conn.auth_status == "ok"
    assert conn.last_pages_synced_at is not None

    # Lượt sau: đổi tên → updated, không sinh dòng mới.
    client.list_pages.return_value = [_dto(name="DELAX mới"), _dto(id="zl_1", name="Zalo OA", platform="zalo")]
    result = PancakePageService(client).sync_pages(conn)
    # `updated` đếm mọi page đã có (upsert), không riêng page đổi dữ liệu.
    assert (result.created, result.updated) == (0, 2)
    assert PancakePage.objects.count() == 3


def test_sync_pages_token_hong_ghi_auth_error_va_raise():
    conn = PancakeConnection.objects.create(access_token="tok")
    client = MagicMock()
    client.list_pages.side_effect = PancakeTokenError("Invalid token")
    with pytest.raises(PancakeTokenError):
        PancakePageService(client).sync_pages(conn)
    conn.refresh_from_db()
    assert (conn.auth_status, conn.auth_error) == ("error", "Invalid token")


def test_enable_page_can_kenh():
    page = PancakePage.objects.create(pancake_page_id="1", platform="facebook")
    with pytest.raises(PageNotEligible, match="kênh"):
        PancakePageService(MagicMock()).enable_page(page)


def test_enable_page_platform_khong_ho_tro():
    channel = Channel.objects.create(code="a", name="A")
    page = PancakePage.objects.create(pancake_page_id="tts_1", platform="tiktok_shop", channel=channel)
    with pytest.raises(PageNotEligible, match="tiktok_shop"):
        PancakePageService(MagicMock()).enable_page(page)


def test_enable_page_sinh_token_va_dat_cursor_now():
    channel = Channel.objects.create(code="a", name="A")
    page = PancakePage.objects.create(pancake_page_id="1", platform="facebook", channel=channel, token_error="cũ")
    client = MagicMock()
    client.generate_page_token.return_value = "ptok"
    before = timezone.now()

    page = PancakePageService(client).enable_page(page)

    page.refresh_from_db()
    assert (page.is_enabled, page.page_access_token, page.token_error) == (True, "ptok", "")
    assert page.polled_until is not None and page.polled_until >= before
    client.generate_page_token.assert_called_once_with("1")


def test_enable_page_sinh_token_loi_thi_khong_bat():
    channel = Channel.objects.create(code="a", name="A")
    page = PancakePage.objects.create(pancake_page_id="1", platform="facebook", channel=channel)
    client = MagicMock()
    client.generate_page_token.side_effect = PancakeTokenError("Thiếu quyền Admin")
    with pytest.raises(PancakeTokenError, match="Thiếu quyền Admin"):
        PancakePageService(client).enable_page(page)
    page.refresh_from_db()
    assert (page.is_enabled, page.page_access_token, page.token_error) == (False, "", "Thiếu quyền Admin")

```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `$PYTEST apps/pancake/tests/test_pancake_page_service.py -q --reuse-db`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Viết service**

`application/__init__.py`, `application/services/__init__.py`: rỗng.

`backend/apps/pancake/application/services/page_service.py`:
```python
"""Đồng bộ danh sách page từ Pancake + bật/tắt poll trên một page.

Bật page = sinh token page (đòi quyền Admin bên Pancake) + đặt cursor bằng
"bây giờ" (không backfill — quyết định D5). Tắt page (ở view) chỉ hạ cờ, giữ
nguyên token và cursor; bật lại thì cursor đặt lại bằng now.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils import timezone

from apps.pancake.infrastructure.pancake_client import PancakeClient, PancakeTokenError
from apps.pancake.models import PancakeAuthStatus, PancakeConnection, PancakePage


class PageNotEligible(Exception):
    """Page chưa đủ điều kiện bật — message hiển thị thẳng lên UI."""


@dataclass(frozen=True)
class SyncPagesResult:
    fetched: int
    created: int
    updated: int
    deactivated: int


def get_client(connection: PancakeConnection | None = None) -> PancakeClient | None:
    connection = connection or PancakeConnection.get_active()
    if connection is None or not connection.access_token:
        return None
    return PancakeClient(connection.access_token)


class PancakePageService:
    def __init__(self, client: PancakeClient):
        self.client = client

    def sync_pages(self, connection: PancakeConnection) -> SyncPagesResult:
        try:
            pages = self.client.list_pages()
        except PancakeTokenError as exc:
            connection.auth_status = PancakeAuthStatus.ERROR
            connection.auth_error = str(exc)
            connection.save(update_fields=["auth_status", "auth_error", "updated_at"])
            raise

        created = updated = 0
        seen: list[str] = []
        for dto in pages:
            if not dto.id:
                continue
            seen.append(dto.id)
            _, is_new = PancakePage.objects.update_or_create(
                pancake_page_id=dto.id,
                defaults={
                    "name": dto.name,
                    "platform": dto.platform,
                    "is_activated": True,
                    "has_active_subscription": dto.has_active_subscription,
                    "role_in_page": dto.role_in_page,
                },
            )
            if is_new:
                created += 1
            else:
                updated += 1
        # Page biến mất khỏi danh sách: chỉ đánh dấu, KHÔNG xoá — lead đang PROTECT
        # theo page, và page có thể quay lại (mất quyền tạm thời).
        deactivated = (
            PancakePage.objects.filter(is_activated=True).exclude(pancake_page_id__in=seen).update(is_activated=False)
        )
        connection.auth_status = PancakeAuthStatus.OK
        connection.auth_error = ""
        connection.last_pages_synced_at = timezone.now()
        connection.save(update_fields=["auth_status", "auth_error", "last_pages_synced_at", "updated_at"])
        return SyncPagesResult(fetched=len(pages), created=created, updated=updated, deactivated=deactivated)

    def enable_page(self, page: PancakePage) -> PancakePage:
        if page.channel_id is None:
            raise PageNotEligible("Chọn kênh bán trước khi bật page.")
        if page.foxia_kind is None:
            raise PageNotEligible(f"Nền tảng {page.platform!r} chưa có cột nguồn Foxia tương ứng.")
        try:
            token = self.client.generate_page_token(page.pancake_page_id)
        except PancakeTokenError as exc:
            page.is_enabled = False
            page.page_access_token = ""
            page.token_error = str(exc)
            page.save(update_fields=["is_enabled", "page_access_token", "token_error"])
            raise
        page.page_access_token = token
        page.token_error = ""
        page.is_enabled = True
        page.polled_until = timezone.now()
        page.last_error = ""
        page.save(update_fields=["page_access_token", "token_error", "is_enabled", "polled_until", "last_error"])
        return page
```

- [ ] **Step 4: Chạy test, xác nhận pass**

Run: `$PYTEST apps/pancake/tests/test_pancake_page_service.py -q --reuse-db`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add backend/apps/pancake
git commit -m "feat(pancake): PancakePageService — đồng bộ page, bật/tắt page với token page"
```

---

### Task 5: `PancakePollService` — poll hội thoại, tạo lead, đẩy Foxia

**Files:**
- Create: `backend/apps/pancake/application/services/poll_service.py`, `backend/apps/pancake/metrics.py`
- Test: `backend/apps/pancake/tests/test_pancake_poll_service.py`

**Interfaces:**
- Consumes: `PancakeClient` DTO + exceptions (Task 3), `push_customer`/`PushResult` (Task 2), `normalize_phone` từ `apps.customers.phone_utils` (trả `""` khi không hợp lệ), `FoxiaLinkCell`.
- Produces:
```python
@dataclass class PollResult: page_id: int; fetched: int; leads_created: int; pushed: int; skipped: int; error: str = ""
class PancakePollService:
    LOOKBACK = timedelta(days=30); CURSOR_SLACK = timedelta(minutes=5); PAGE_SIZE = 200
    def __init__(self, client: PancakeClient)
    def poll_page(self, page: PancakePage) -> PollResult
def push_lead(lead: PancakeLead) -> PancakeLead     # hàm module, task repush cũng gọi
def build_lead_payload(lead: PancakeLead) -> dict
```

- [ ] **Step 1: Viết test**

`backend/apps/pancake/tests/test_pancake_poll_service.py`:
```python
"""Luồng poll một page. Chốt: dừng theo cursor, bỏ hội thoại không có sđt, tạo
lead + đẩy Foxia, chống trùng (channel, phone), skipped_no_mapping, cursor chỉ tiến
khi không lỗi, token bị từ chối → sinh lại 1 lần rồi tắt page."""

from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.foxia.application.services.order_push_service import PushResult
from apps.foxia.models import FoxiaLinkCell, FoxiaLinkChannel
from apps.pancake.application.services.poll_service import PancakePollService, build_lead_payload, push_lead
from apps.pancake.infrastructure.pancake_client import (
    ConversationDTO,
    ConversationDetail,
    ConversationPage,
    PancakeError,
    PancakePageTokenRejected,
    PancakeTokenError,
)
from apps.pancake.models import PancakeLead, PancakePage, PancakePushStatus

pytestmark = pytest.mark.django_db

PUSH_MODULE = "apps.pancake.application.services.poll_service.push_customer"
T0 = datetime(2026, 9, 16, 9, 0, tzinfo=dt_timezone.utc)


def _ok_result(cohoi_id=99):
    return PushResult(url="u", http_status=200, response_body="{}", error="", duration_ms=5,
                      cohoi_id=cohoi_id, khachhang_id=7)


def _fail_result():
    return PushResult(url="u", http_status=500, response_body="err", error="", duration_ms=5,
                      cohoi_id=None, khachhang_id=None)


def _conv(id, updated_at, has_phone=True, phones=(), name="Khách", tags=("BOT",), inserted_at=None):
    return ConversationDTO(
        id=id, type="INBOX", inserted_at=inserted_at or updated_at - timedelta(minutes=10),
        updated_at=updated_at, has_phone=has_phone, phones=list(phones), customer_name=name,
        customer_id=f"c-{id}", tags=list(tags), assignee_name="Nhung HN",
    )


class FakeClient:
    """Trang hội thoại cho sẵn; ghi lại lời gọi để test đếm request."""

    def __init__(self, pages=None, details=None, total=None):
        self.pages = pages or {}          # page_number -> list[ConversationDTO]
        self.details = details or {}      # conversation_id -> ConversationDetail
        self.total = total
        self.calls = []
        self.token_calls = 0
        self.reject_tokens = set()
        self.token_error = None

    def list_conversations(self, page_id, page_token, since, until, page_number):
        self.calls.append(("list", page_number, page_token))
        if page_token in self.reject_tokens:
            raise PancakePageTokenRejected("bad token")
        items = self.pages.get(page_number, [])
        total = self.total if self.total is not None else sum(len(v) for v in self.pages.values())
        return ConversationPage(total=total, items=items)

    def get_conversation_detail(self, page_id, page_token, conversation_id):
        self.calls.append(("detail", conversation_id))
        return self.details.get(conversation_id, ConversationDetail(phones=[], customer_name="", customer_id="", phone_message_at=None))

    def generate_page_token(self, page_id):
        self.token_calls += 1
        if self.token_error:
            raise self.token_error
        return "new-token"


@pytest.fixture
def page():
    channel = Channel.objects.create(code="kenh-a", name="Kênh A")
    row = FoxiaLinkChannel.objects.create(channel=channel)
    FoxiaLinkCell.objects.create(link_channel=row, kind="messenger", foxia_source_id=1234)
    return PancakePage.objects.create(
        pancake_page_id="945997011940587", name="DELAX", platform="facebook", channel=channel,
        is_enabled=True, page_access_token="ptok", polled_until=T0 - timedelta(minutes=30),
    )


def _service(client, now=T0):
    service = PancakePollService(client)
    service.now = lambda: now
    return service


def test_tao_lead_va_day_foxia(page):
    client = FakeClient(
        pages={1: [_conv("c1", T0 - timedelta(minutes=1), phones=["0901234567"])]},
        details={"c1": ConversationDetail(phones=["+84 901 234 567"], customer_name="Tuan Anh", customer_id="cust-1",
                                          phone_message_at=T0 - timedelta(minutes=2))},
    )
    with patch(PUSH_MODULE, return_value=_ok_result()) as push:
        result = _service(client).poll_page(page)

    assert (result.fetched, result.leads_created, result.pushed, result.skipped, result.error) == (1, 1, 1, 0, "")
    lead = PancakeLead.objects.get()
    assert (lead.phone, lead.phone_raw, lead.customer_name, lead.pancake_customer_id) == ("0901234567", "+84 901 234 567", "Tuan Anh", "cust-1")
    assert (lead.channel_id, lead.foxia_kind, lead.foxia_source_id) == (page.channel_id, "messenger", 1234)
    assert (lead.push_status, lead.cohoi_id, lead.khachhang_id, lead.http_status) == (PancakePushStatus.OK, 99, 7, 200)
    assert lead.phone_given_at == T0 - timedelta(minutes=2)
    assert lead.tags == ["BOT"] and lead.assignee_name == "Nhung HN"
    assert lead.conversation_inserted_at == T0 - timedelta(minutes=11)
    push.assert_called_once_with(1234, {"name": "Tuan Anh", "phone": "0901234567", "address": "", "message": "Pancake: DELAX"})
    page.refresh_from_db()
    assert page.polled_until == T0 - timedelta(minutes=1)
    assert page.last_polled_at == T0 and page.last_error == ""


def test_khong_co_tin_nhan_co_sdt_thi_phone_given_at_la_updated_at(page):
    client = FakeClient(pages={1: [_conv("c1", T0, phones=["0901234567"])]})
    with patch(PUSH_MODULE, return_value=_ok_result()):
        _service(client).poll_page(page)
    assert PancakeLead.objects.get().phone_given_at == T0


def test_bo_hoi_thoai_khong_co_sdt_khong_goi_detail(page):
    client = FakeClient(pages={1: [_conv("c1", T0, has_phone=False)]})
    with patch(PUSH_MODULE) as push:
        result = _service(client).poll_page(page)
    assert result.leads_created == 0
    assert ("detail", "c1") not in client.calls
    push.assert_not_called()


def test_dung_khi_gap_hoi_thoai_cu_hon_cursor(page):
    old = page.polled_until - timedelta(minutes=10)
    client = FakeClient(
        pages={1: [_conv("new", T0, phones=["0901234567"]), _conv("old", old, phones=["0912345678"])],
               2: [_conv("older", old - timedelta(hours=1), phones=["0923456789"])]},
        total=400,
    )
    with patch(PUSH_MODULE, return_value=_ok_result()):
        result = _service(client).poll_page(page)
    assert result.leads_created == 1
    assert [c for c in client.calls if c[0] == "list"] == [("list", 1, "ptok")]


def test_lat_trang_khi_chua_gap_cursor(page):
    client = FakeClient(
        pages={1: [_conv(f"a{i}", T0 - timedelta(seconds=i), phones=[f"09000000{i:02d}"]) for i in range(2)],
               2: [_conv("b", T0 - timedelta(minutes=2), phones=["0911111111"])]},
        total=202,
    )
    # Trang 1 chỉ có 2 phần tử nhưng total=202 → phải sang trang 2 (Pancake không có has_next).
    service = _service(client)
    service.PAGE_SIZE = 2
    with patch(PUSH_MODULE, return_value=_ok_result()):
        result = service.poll_page(page)
    assert result.leads_created == 3
    # Trang 3 rỗng là tín hiệu hết (Pancake không có has_next): 3 request list.
    assert [c[1] for c in client.calls if c[0] == "list"] == [1, 2, 3]


def test_trung_kenh_va_sdt_khong_day_lai(page):
    PancakeLead.objects.create(page=page, channel=page.channel, conversation_id="x", phone_raw="0901234567",
                               phone="0901234567", foxia_kind="messenger", push_status=PancakePushStatus.OK)
    client = FakeClient(pages={1: [_conv("c1", T0, phones=["0901234567"])]})
    with patch(PUSH_MODULE) as push:
        result = _service(client).poll_page(page)
    assert (result.leads_created, result.pushed) == (0, 0)
    assert PancakeLead.objects.count() == 1
    push.assert_not_called()


def test_sdt_khong_hop_le_dem_skipped(page):
    client = FakeClient(pages={1: [_conv("c1", T0, phones=["123", "0901234567"])]})
    with patch(PUSH_MODULE, return_value=_ok_result()):
        result = _service(client).poll_page(page)
    assert (result.leads_created, result.skipped) == (1, 1)


def test_chua_cau_hinh_o_thi_skipped_no_mapping(page):
    FoxiaLinkCell.objects.all().delete()
    client = FakeClient(pages={1: [_conv("c1", T0, phones=["0901234567"])]})
    with patch(PUSH_MODULE) as push:
        result = _service(client).poll_page(page)
    lead = PancakeLead.objects.get()
    assert lead.push_status == PancakePushStatus.SKIPPED_NO_MAPPING
    assert lead.foxia_source_id is None and lead.pushed_at is not None
    assert (result.leads_created, result.pushed) == (1, 0)
    push.assert_not_called()


def test_foxia_loi_ghi_failed_cursor_van_tien(page):
    client = FakeClient(pages={1: [_conv("c1", T0, phones=["0901234567"])]})
    with patch(PUSH_MODULE, return_value=_fail_result()):
        result = _service(client).poll_page(page)
    lead = PancakeLead.objects.get()
    assert (lead.push_status, lead.http_status, lead.response_body) == (PancakePushStatus.FAILED, 500, "err")
    assert result.pushed == 0
    page.refresh_from_db()
    assert page.polled_until == T0


def test_pancake_loi_giu_cursor_ghi_last_error(page):
    cursor = page.polled_until

    class Boom(FakeClient):
        def list_conversations(self, *a, **k):
            raise PancakeError("HTTP 502")

    result = _service(Boom()).poll_page(page)
    assert result.error == "PancakeError: HTTP 502"
    page.refresh_from_db()
    assert page.polled_until == cursor
    assert page.last_error == "PancakeError: HTTP 502"
    assert page.last_polled_at == T0


def test_token_bi_tu_choi_sinh_lai_mot_lan_roi_chay_tiep(page):
    client = FakeClient(pages={1: [_conv("c1", T0, phones=["0901234567"])]})
    client.reject_tokens = {"ptok"}
    with patch(PUSH_MODULE, return_value=_ok_result()):
        result = _service(client).poll_page(page)
    assert result.leads_created == 1
    assert client.token_calls == 1
    page.refresh_from_db()
    assert page.page_access_token == "new-token" and page.is_enabled is True


def test_token_bi_tu_choi_sinh_lai_that_bai_thi_tat_page(page):
    client = FakeClient()
    client.reject_tokens = {"ptok"}
    client.token_error = PancakeTokenError("Thiếu quyền Admin")
    result = _service(client).poll_page(page)
    assert "Thiếu quyền Admin" in result.error
    page.refresh_from_db()
    assert (page.is_enabled, page.page_access_token, page.token_error) == (False, "", "Thiếu quyền Admin")


def test_token_moi_van_bi_tu_choi_thi_tat_page(page):
    client = FakeClient()
    client.reject_tokens = {"ptok", "new-token"}
    result = _service(client).poll_page(page)
    assert result.error
    page.refresh_from_db()
    assert page.is_enabled is False and page.page_access_token == ""


def test_page_chua_san_sang_khong_goi_mang(page):
    page.is_enabled = False
    page.save()
    client = FakeClient()
    result = _service(client).poll_page(page)
    assert result.error and client.calls == []


def test_hoi_thoai_da_co_lead_ma_khong_moi_hon_khong_goi_detail(page):
    PancakeLead.objects.create(page=page, channel=page.channel, conversation_id="c1", phone_raw="0901234567",
                               phone="0901234567", foxia_kind="messenger", phone_given_at=T0)
    client = FakeClient(pages={1: [_conv("c1", T0 - timedelta(minutes=1), phones=["0901234567"])]})
    _service(client).poll_page(page)
    assert ("detail", "c1") not in client.calls


def test_push_lead_dung_rieng_cho_repush(page):
    lead = PancakeLead.objects.create(page=page, channel=page.channel, conversation_id="c1", phone_raw="0901234567",
                                      phone="0901234567", customer_name="A", foxia_kind="messenger",
                                      push_status=PancakePushStatus.FAILED)
    with patch(PUSH_MODULE, return_value=_ok_result(cohoi_id=5)) as push:
        push_lead(lead)
    lead.refresh_from_db()
    assert (lead.push_status, lead.cohoi_id, lead.foxia_source_id) == (PancakePushStatus.OK, 5, 1234)
    push.assert_called_once_with(1234, build_lead_payload(lead))
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `$PYTEST apps/pancake/tests/test_pancake_poll_service.py -q --reuse-db`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Viết metrics**

`backend/apps/pancake/metrics.py`:
```python
"""Metrics Prometheus của tích hợp Pancake — cùng khuôn fallback với foxia/metrics.py."""

from __future__ import annotations

try:
    from prometheus_client import Counter, Histogram

    _ENABLED = True
except Exception:  # pragma: no cover - prometheus là optional
    _ENABLED = False

    class _Noop:
        def labels(self, *_args, **_kwargs):
            return self

        def inc(self, *_args, **_kwargs):
            return None

        def observe(self, *_args, **_kwargs):
            return None

    def Counter(*_args, **_kwargs):  # type: ignore[no-redef]
        return _Noop()

    def Histogram(*_args, **_kwargs):  # type: ignore[no-redef]
        return _Noop()


pancake_poll_pages_total = Counter(
    "desi_pancake_poll_pages_total",
    "Số lượt poll một page Pancake theo kết quả",
    ["status"],  # ok | error | token_rejected
)

pancake_leads_total = Counter(
    "desi_pancake_leads_total",
    "Số lead Pancake tạo mới theo trạng thái đẩy Foxia",
    ["push_status"],
)

pancake_poll_duration_seconds = Histogram(
    "desi_pancake_poll_duration_seconds",
    "Thời lượng poll một page",
    buckets=(0.5, 1.0, 2.0, 5.0, 15.0, 30.0, 60.0, 120.0),
)
```

- [ ] **Step 4: Viết poll service**

`backend/apps/pancake/application/services/poll_service.py`:
```python
"""Poll hội thoại có số điện thoại của một page Pancake → `pancake_leads` → Foxia.

Cách đi qua danh sách: `since = now − 30 ngày` (lọc theo ngày TẠO hội thoại),
kết quả DESC theo `updated_at`, đọc trang 1, 2, … và DỪNG khi gặp hội thoại có
`updated_at` cũ hơn cursor (trừ 5 phút đệm). Bình thường mỗi lượt một request
cho mỗi page. Hội thoại tạo hơn 30 ngày mới cho số sẽ lọt — chấp nhận (spec §7).

Cursor `polled_until` = max(updated_at) của lượt, CHỈ tiến khi lượt page đó không
lỗi phía Pancake. Lỗi Foxia khi đẩy một lead KHÔNG chặn cursor: lead đã ghi
`failed`, người vận hành đẩy lại bằng tay.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.customers.phone_utils import normalize_phone
from apps.foxia.application.services.order_push_service import push_customer
from apps.pancake import metrics
from apps.pancake.infrastructure.pancake_client import (
    ConversationDTO,
    PancakeClient,
    PancakePageTokenRejected,
    PancakeTokenError,
)
from apps.pancake.models import PancakeLead, PancakePage, PancakePushStatus

logger = logging.getLogger(__name__)


@dataclass
class PollResult:
    page_id: int
    fetched: int = 0
    leads_created: int = 0
    pushed: int = 0
    skipped: int = 0
    error: str = ""


def build_lead_payload(lead: PancakeLead) -> dict:
    """Ba trường bắt buộc của Foxia + `message` = tên page. `address` rỗng vì Pancake
    không có địa chỉ (quyết định D7)."""
    return {
        "name": lead.customer_name,
        "phone": lead.phone,
        "address": "",
        "message": f"Pancake: {lead.page.name}",
    }


def _lookup_source_id(lead: PancakeLead) -> int | None:
    from apps.foxia.models import FoxiaLinkCell

    return (
        FoxiaLinkCell.objects.filter(link_channel__channel_id=lead.channel_id, kind=lead.foxia_kind)
        .values_list("foxia_source_id", flat=True)
        .first()
    )


def push_lead(lead: PancakeLead) -> PancakeLead:
    """Tra ô (kênh, cột) rồi POST sang Foxia; ghi kết quả vào chính lead. Không raise."""
    now = timezone.now()
    source_id = _lookup_source_id(lead)
    if source_id is None:
        lead.push_status = PancakePushStatus.SKIPPED_NO_MAPPING
        lead.foxia_source_id = None
        lead.pushed_at = now
        lead.save(update_fields=["push_status", "foxia_source_id", "pushed_at"])
        metrics.pancake_leads_total.labels(push_status=lead.push_status).inc()
        logger.info("Pancake push skipped_no_mapping: lead=%s kenh=%s cot=%s", lead.id, lead.channel_id, lead.foxia_kind)
        return lead

    result = push_customer(source_id, build_lead_payload(lead))
    lead.foxia_source_id = source_id
    lead.push_status = PancakePushStatus.OK if result.ok else PancakePushStatus.FAILED
    lead.http_status = result.http_status
    lead.response_body = result.response_body[: PancakeLead.RESPONSE_LIMIT]
    lead.error = result.error
    lead.pushed_at = now
    lead.cohoi_id = result.cohoi_id
    lead.khachhang_id = result.khachhang_id
    lead.save(
        update_fields=[
            "foxia_source_id", "push_status", "http_status", "response_body", "error",
            "pushed_at", "cohoi_id", "khachhang_id",
        ]
    )
    metrics.pancake_leads_total.labels(push_status=lead.push_status).inc()
    # KHÔNG log tên/sđt: lead=<id> đủ để tra ngược trong DB.
    (logger.info if result.ok else logger.warning)(
        "Pancake push %s: lead=%s page=%s nguon=%s http=%s %sms%s",
        lead.push_status, lead.id, lead.page_id, source_id, result.http_status, result.duration_ms,
        f" error={result.error}" if result.error else "",
    )
    return lead


class PancakePollService:
    LOOKBACK = timedelta(days=30)
    CURSOR_SLACK = timedelta(minutes=5)
    PAGE_SIZE = 200

    def __init__(self, client: PancakeClient):
        self.client = client

    def now(self) -> datetime:  # tách ra để test ghim thời gian
        return timezone.now()

    def poll_page(self, page: PancakePage) -> PollResult:
        result = PollResult(page_id=page.id)
        if not page.is_enabled or page.channel_id is None or not page.page_access_token or page.foxia_kind is None:
            result.error = "page chưa sẵn sàng (chưa bật / chưa gán kênh / chưa có token)"
            return result

        started = time.monotonic()
        now = self.now()
        status = "ok"
        try:
            try:
                self._scan(page, page.page_access_token, now, result)
            except PancakePageTokenRejected:
                # Sinh lại token đúng MỘT lần trong lượt; vẫn hỏng thì tắt page để
                # người vận hành thấy trên bảng, không âm thầm lỗi mỗi 3 phút.
                status = "token_rejected"
                token = self.client.generate_page_token(page.pancake_page_id)
                page.page_access_token = token
                page.token_error = ""
                page.save(update_fields=["page_access_token", "token_error"])
                result = PollResult(page_id=page.id)
                self._scan(page, token, now, result)
                status = "ok"
        except (PancakeTokenError, PancakePageTokenRejected) as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            page.is_enabled = False
            page.page_access_token = ""
            page.token_error = str(exc)
            page.last_error = result.error
            page.last_polled_at = now
            page.save(update_fields=["is_enabled", "page_access_token", "token_error", "last_error", "last_polled_at"])
            metrics.pancake_poll_pages_total.labels(status="token_rejected").inc()
            logger.warning("Pancake poll: page=%s token bị từ chối, đã tắt page (%s)", page.id, exc)
            return result
        except Exception as exc:  # noqa: BLE001 — lỗi đối tác không được thoát ra task
            result.error = f"{type(exc).__name__}: {exc}"
            page.last_error = result.error
            page.last_polled_at = now
            page.save(update_fields=["last_error", "last_polled_at"])
            metrics.pancake_poll_pages_total.labels(status="error").inc()
            logger.warning("Pancake poll: page=%s lỗi, giữ cursor (%s)", page.id, result.error)
            return result
        finally:
            metrics.pancake_poll_duration_seconds.observe(time.monotonic() - started)

        metrics.pancake_poll_pages_total.labels(status=status).inc()
        logger.info(
            "Pancake poll ok: page=%s fetched=%s leads=%s pushed=%s skipped=%s",
            page.id, result.fetched, result.leads_created, result.pushed, result.skipped,
        )
        return result

    def _scan(self, page: PancakePage, token: str, now: datetime, result: PollResult) -> None:
        cursor = page.polled_until or now
        threshold = cursor - self.CURSOR_SLACK
        max_updated = cursor
        since, until = now - self.LOOKBACK, now
        page_number = 1
        while True:
            conv_page = self.client.list_conversations(page.pancake_page_id, token, since, until, page_number)
            stop = False
            for conv in conv_page.items:
                result.fetched += 1
                if conv.updated_at is not None and conv.updated_at < threshold:
                    stop = True
                    break
                if conv.updated_at is not None and conv.updated_at > max_updated:
                    max_updated = conv.updated_at
                if not conv.has_phone:
                    continue
                self._handle_conversation(page, token, conv, result)
            if stop or not conv_page.items or page_number * self.PAGE_SIZE >= conv_page.total:
                break
            page_number += 1

        page.polled_until = max_updated
        page.last_polled_at = now
        page.last_error = ""
        page.save(update_fields=["polled_until", "last_polled_at", "last_error"])

    def _handle_conversation(self, page: PancakePage, token: str, conv: ConversationDTO, result: PollResult) -> None:
        # Đã có lead của hội thoại này mà hội thoại không mới hơn lúc cho số → khỏi
        # tốn request chi tiết. Hội thoại có tin mới thì gọi lại: có thể là số thứ hai.
        latest = (
            PancakeLead.objects.filter(page=page, conversation_id=conv.id)
            .order_by("-phone_given_at")
            .values_list("phone_given_at", flat=True)
            .first()
        )
        if latest is not None and conv.updated_at is not None and conv.updated_at <= latest:
            return

        detail = self.client.get_conversation_detail(page.pancake_page_id, token, conv.id)
        phones = detail.phones or conv.phones
        name = detail.customer_name or conv.customer_name
        customer_id = detail.customer_id or conv.customer_id
        given_at = detail.phone_message_at or conv.updated_at

        for raw in phones:
            phone = normalize_phone(raw)
            if not phone:
                result.skipped += 1
                continue
            defaults = {
                "page": page,
                "conversation_id": conv.id,
                "conversation_inserted_at": conv.inserted_at,
                "pancake_customer_id": customer_id[:64],
                "customer_name": name[:255],
                "phone_raw": raw[:32],
                "tags": conv.tags,
                "phone_given_at": given_at,
                "assignee_name": conv.assignee_name[:255],
                "foxia_kind": page.foxia_kind,
            }
            try:
                with transaction.atomic():
                    lead, created = PancakeLead.objects.get_or_create(
                        channel_id=page.channel_id, phone=phone, defaults=defaults
                    )
            except IntegrityError:
                created = False
            if not created:
                continue
            result.leads_created += 1
            lead = push_lead(lead)
            if lead.push_status == PancakePushStatus.OK:
                result.pushed += 1
```

- [ ] **Step 5: Chạy test, xác nhận pass**

Run: `$PYTEST apps/pancake/tests/test_pancake_poll_service.py -q --reuse-db`
Expected: 16 passed

- [ ] **Step 6: Commit**

```bash
git add backend/apps/pancake
git commit -m "feat(pancake): PancakePollService — poll hội thoại có sđt, tạo lead, đẩy Foxia; metrics"
```

---

### Task 6: Task Celery, route/beat, cờ env

**Files:**
- Create: `backend/apps/pancake/tasks.py`, `backend/apps/pancake/tests/test_pancake_tasks.py`
- Modify: `backend/config/settings_parts/base.py` (sau khối `FOXIA_PUSH_*`), `backend/config/settings_parts/celery.py` (routes + beat), `docker-compose.prod.yml`, `docker-compose.staging.yml`, `docker-compose.local.yml` (anchor env, cạnh `FOXIA_PUSH_TIMEOUT`), `.env.example` (cạnh dòng 394 `FOXIA_PUSH_TIMEOUT=10`)

**Interfaces (Produces):**
- Task `apps.pancake.tasks.poll_phone_leads() -> str` (`disabled` / `skipped` / `no_client` / `ok:<pages>:<leads>`), queue `foxia`.
- Task `apps.pancake.tasks.repush_lead(lead_id: int) -> str`, queue `foxia_push`.
- Settings: `PANCAKE_POLL_ENABLED: bool`, `PANCAKE_HTTP_TIMEOUT: float`.

- [ ] **Step 1: Viết test**

`backend/apps/pancake/tests/test_pancake_tasks.py`:
```python
"""Task poll: gate bằng cờ, single-flight, chỉ page đã bật; task repush chỉ đẩy lead
failed/skipped. Route + beat được test_celery_routes bảo vệ, ở đây chỉ chốt queue."""

from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import override_settings

from apps.channels_core.models import Channel
from apps.pancake.models import PancakeConnection, PancakeLead, PancakePage, PancakePushStatus
from apps.pancake.tasks import poll_phone_leads, repush_lead

pytestmark = pytest.mark.django_db

POLL_SERVICE = "apps.pancake.tasks.PancakePollService"


def test_route_va_beat():
    assert settings.CELERY_TASK_ROUTES["apps.pancake.tasks.poll_phone_leads"]["queue"] == settings.CELERY_FOXIA_QUEUE
    assert settings.CELERY_TASK_ROUTES["apps.pancake.tasks.repush_lead"]["queue"] == settings.CELERY_FOXIA_PUSH_QUEUE
    assert settings.CELERY_BEAT_SCHEDULE["pancake-poll-phone-leads"]["task"] == "apps.pancake.tasks.poll_phone_leads"
    assert settings.CELERY_BEAT_SCHEDULE["pancake-poll-phone-leads"]["schedule"] == 180.0


@override_settings(PANCAKE_POLL_ENABLED=False)
def test_poll_tat_co_thi_disabled():
    assert poll_phone_leads() == "disabled"


@override_settings(PANCAKE_POLL_ENABLED=True)
def test_poll_chua_co_connection():
    assert poll_phone_leads() == "no_client"


@override_settings(PANCAKE_POLL_ENABLED=True)
def test_poll_chi_page_da_bat_va_gom_ket_qua():
    PancakeConnection.objects.create(access_token="tok")
    channel = Channel.objects.create(code="a", name="A")
    on = PancakePage.objects.create(pancake_page_id="1", platform="facebook", channel=channel, is_enabled=True, page_access_token="t")
    PancakePage.objects.create(pancake_page_id="2", platform="facebook", channel=channel, is_enabled=False, page_access_token="t")
    with patch(POLL_SERVICE) as service_cls:
        service_cls.return_value.poll_page.side_effect = lambda page: type("R", (), {"leads_created": 2, "error": ""})()
        assert poll_phone_leads() == "ok:1:2"
    assert [call.args[0].id for call in service_cls.return_value.poll_page.call_args_list] == [on.id]


@override_settings(PANCAKE_POLL_ENABLED=True)
def test_poll_single_flight():
    from django.core.cache import cache

    from apps.pancake.tasks import _POLL_LOCK_KEY

    cache.add(_POLL_LOCK_KEY, "khac", 60)
    try:
        assert poll_phone_leads() == "skipped"
    finally:
        cache.delete(_POLL_LOCK_KEY)


def _lead(status):
    channel = Channel.objects.create(code="a", name="A")
    page = PancakePage.objects.create(pancake_page_id="1", name="P", platform="facebook", channel=channel)
    return PancakeLead.objects.create(page=page, channel=channel, conversation_id="c", phone_raw="0901234567",
                                      phone="0901234567", foxia_kind="messenger", push_status=status)


def test_repush_chi_lead_failed_hoac_skipped():
    lead = _lead(PancakePushStatus.OK)
    with patch("apps.pancake.tasks.push_lead") as push:
        assert repush_lead(lead.id) == "not_eligible"
    push.assert_not_called()

    lead.push_status = PancakePushStatus.FAILED
    lead.save()
    with patch("apps.pancake.tasks.push_lead", side_effect=lambda l: l) as push:
        assert repush_lead(lead.id) == "pushed"
    assert push.call_args.args[0].id == lead.id
    assert repush_lead(lead.id + 999) == "missing"
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `$PYTEST apps/pancake/tests/test_pancake_tasks.py -q --reuse-db`
Expected: FAIL `ModuleNotFoundError: apps.pancake.tasks`

- [ ] **Step 3: Settings**

`backend/config/settings_parts/base.py`, ngay sau khối `FOXIA_PUSH_TIMEOUT`:
```python
# ── Pancake — kéo số điện thoại từ hội thoại → đẩy cơ hội Foxia (spec 2026-09-16) ──
# Tắt mặc định: cron no-op tới khi nhập token ở UI, gán kênh + bật page, và bật cờ.
# Lưu token / đồng bộ page / bật page / đẩy lại chạy tay KHÔNG bị gate.
PANCAKE_POLL_ENABLED = os.getenv("PANCAKE_POLL_ENABLED", "0") == "1"
PANCAKE_HTTP_TIMEOUT = float(os.getenv("PANCAKE_HTTP_TIMEOUT", "15"))
```

`backend/config/settings_parts/celery.py`:
- Trong `CELERY_TASK_ROUTES`, sau dòng `"apps.foxia.tasks.push_order_to_foxia"`:
```python
    # Pancake (spec 2026-09-16): poll là I/O đối tác → chung container worker-foxia;
    # đẩy lại một lead là một POST Foxia → chung queue với đẩy đơn.
    "apps.pancake.tasks.poll_phone_leads": {"queue": CELERY_FOXIA_QUEUE},
    "apps.pancake.tasks.repush_lead": {"queue": CELERY_FOXIA_PUSH_QUEUE},
```
- Trong `CELERY_BEAT_SCHEDULE`, sau entry `foxia-match-lead-orders`:
```python
    # Poll hội thoại Pancake có sđt: 3 phút — độ trễ khách cho số → cơ hội Foxia.
    # Task tự no-op khi PANCAKE_POLL_ENABLED=0; single-flight nên chồng lượt vô hại.
    "pancake-poll-phone-leads": {
        "task": "apps.pancake.tasks.poll_phone_leads",
        "schedule": 180.0,
    },
```

Compose (3 file, trong anchor env `*backend-env`, ngay sau `CELERY_FOXIA_PUSH_QUEUE`):
```yaml
  # Pancake → Foxia (spec 2026-09-16). Token ở DB (mã hoá), nhập qua UI Cài đặt.
  PANCAKE_POLL_ENABLED: ${PANCAKE_POLL_ENABLED:-0}
  PANCAKE_HTTP_TIMEOUT: ${PANCAKE_HTTP_TIMEOUT:-15}
```
`.env.example`, sau `FOXIA_PUSH_TIMEOUT=10`:
```
# Pancake: poll hội thoại có sđt → đẩy cơ hội Foxia. Bật sau khi nhập token + gán page.
PANCAKE_POLL_ENABLED=0
PANCAKE_HTTP_TIMEOUT=15
```

- [ ] **Step 4: Viết tasks**

`backend/apps/pancake/tasks.py`:
```python
"""Celery task của Pancake — poll vào queue `foxia` (worker-foxia, I/O đối tác),
đẩy lại vào `foxia_push` (một POST Foxia, cùng bản chất đẩy đơn).

`CELERY_TASK_TIME_LIMIT` toàn cục là 30 GIÂY — task poll bắt buộc override.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings

from apps.common.db_guards import single_flight
from apps.pancake.application.services.page_service import get_client
from apps.pancake.application.services.poll_service import PancakePollService, push_lead
from apps.pancake.models import PancakeLead, PancakePage, PancakePushStatus

logger = logging.getLogger(__name__)

_POLL_LOCK_KEY = "lock:pancake_poll_phone_leads"
_POLL_LOCK_TTL = 600  # > time_limit


@shared_task(
    name="apps.pancake.tasks.poll_phone_leads",
    soft_time_limit=150,
    time_limit=170,
)
def poll_phone_leads() -> str:
    """Poll mọi page đã bật. Một page lỗi không chặn page khác (service tự nuốt lỗi)."""
    if not getattr(settings, "PANCAKE_POLL_ENABLED", False):
        return "disabled"
    with single_flight(_POLL_LOCK_KEY, _POLL_LOCK_TTL) as acquired:
        if not acquired:
            logger.info("Pancake poll: lượt trước chưa xong — bỏ qua lượt này")
            return "skipped"
        client = get_client()
        if client is None:
            logger.warning("Pancake poll: chưa có PancakeConnection active — bỏ qua")
            return "no_client"
        service = PancakePollService(client)
        # KHÔNG lọc theo page_access_token ở DB: cột mã hoá (EncryptedCharField) không
        # so sánh được; poll_page tự bỏ qua page thiếu token.
        pages = list(PancakePage.objects.filter(is_enabled=True, channel__isnull=False).select_related("channel"))
        leads = 0
        for page in pages:
            result = service.poll_page(page)
            leads += result.leads_created
        return f"ok:{len(pages)}:{leads}"


@shared_task(name="apps.pancake.tasks.repush_lead", soft_time_limit=25, time_limit=30, max_retries=0)
def repush_lead(lead_id: int) -> str:
    """Đẩy lại MỘT lead theo yêu cầu người vận hành. Chỉ failed/skipped_no_mapping —
    lead đã `ok` mà đẩy lại là cơ hội thứ hai bên Foxia."""
    lead = PancakeLead.objects.filter(id=lead_id).select_related("page").first()
    if lead is None:
        return "missing"
    if lead.push_status not in (PancakePushStatus.FAILED, PancakePushStatus.SKIPPED_NO_MAPPING):
        return "not_eligible"
    push_lead(lead)
    return "pushed"
```

- [ ] **Step 5: Chạy test**

Run: `$PYTEST apps/pancake/tests/test_pancake_tasks.py apps/common/tests/test_celery_routes.py -q --reuse-db`
Expected: tất cả passed (đặc biệt `test_routed_and_beat_tasks_are_actually_registered`).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/pancake backend/config .env.example docker-compose.prod.yml docker-compose.staging.yml docker-compose.local.yml
git commit -m "feat(pancake): task poll 3 phút (queue foxia) + repush_lead; cờ PANCAKE_POLL_ENABLED"
```

---

### Task 7: RBAC + API `/api/pancake/…`

**Files:**
- Create: `backend/apps/pancake/transport/__init__.py`, `transport/views.py`, `backend/apps/pancake/urls.py`, `backend/apps/users/migrations/0049_seed_pancake_leads_permission.py`, `backend/apps/pancake/tests/test_pancake_api.py`
- Modify: `backend/apps/users/models.py` (VALID_SECTIONS), `backend/apps/users/rbac_catalog.py` (PERMISSION_CATALOG), `backend/config/urls.py` (sau dòng `api/foxia/`)

**Interfaces:**
- Consumes: `PancakePageService`, `get_client`, `PageNotEligible` (Task 4); `repush_lead` task (Task 6); `superuser_only` mẫu từ `foxia/transport/link_config_views.py`; `require_section` từ `apps.users.permissions`.
- Produces (hợp đồng JSON cho FE):
```
GET  /api/pancake/connection      → {has_token, auth_status, auth_error, last_pages_synced_at, name}
PUT  /api/pancake/connection      body {access_token} → như GET (200) | 400 {detail}
POST /api/pancake/pages/sync      → {fetched, created, updated, deactivated, pages: [PageOut]}
GET  /api/pancake/pages           → {pages: [PageOut]}
PATCH /api/pancake/pages/<id>     body {channel_id?: int|null, is_enabled?: bool} → PageOut | 400 {detail}
GET  /api/pancake/leads?page&channel&push_status&q&date_from&date_to&limit&offset → {items:[LeadOut], total, limit, offset}
POST /api/pancake/leads/<id>/repush → {status: "queued"} | 409 {detail}
PageOut = {id, pancake_page_id, name, platform, is_activated, has_active_subscription, role_in_page,
           channel_id, channel_name, foxia_kind, is_enabled, has_token, token_error, polled_until, last_polled_at, last_error}
LeadOut = {id, page_id, pancake_page_id, page_name, channel_id, channel_name, conversation_id,
           conversation_inserted_at, customer_name, phone, tags, phone_given_at, assignee_name, created_at,
           foxia_kind, foxia_source_id, push_status, http_status, error, pushed_at, cohoi_id}
```

- [ ] **Step 1: RBAC**

`backend/apps/users/models.py`, trong `VALID_SECTIONS` ngay sau `"foxia-link",`:
```python
    # Cài đặt → Pancake — superuser-only, CỐ Ý không có trong PERMISSION_CATALOG
    # (cùng khuôn foxia-link). Trang lead Pancake thì có quyền riêng bên dưới.
    "pancake",
    "pancake-leads",
```
`backend/apps/users/rbac_catalog.py`, trong `PERMISSION_CATALOG` sau `"address-lookup": ("view",),`:
```python
    # Danh sách sđt kéo từ Pancake + nút đẩy lại Foxia — chỉ xem (đẩy lại là thao tác
    # vận hành trên dữ liệu đã có, không phải sửa dữ liệu).
    "pancake-leads": ("view",),
```
`backend/apps/users/migrations/0049_seed_pancake_leads_permission.py` (copy nguyên khuôn `0047`, đổi `SECTION = "pancake-leads"`, dependency `("users", "0048_user_foxia_id")`).

- [ ] **Step 2: Viết test API**

`backend/apps/pancake/tests/test_pancake_api.py`:
```python
"""API Pancake: cấu hình chỉ superuser; danh sách lead theo quyền `pancake-leads:view`."""

from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.pancake.infrastructure.pancake_client import PageDTO, PancakeTokenError
from apps.pancake.models import PancakeConnection, PancakeLead, PancakePage, PancakePushStatus
from apps.users.test_helpers import make_admin, make_user_with_sections

pytestmark = pytest.mark.django_db

BASE = "/api/pancake"
VIEWS = "apps.pancake.transport.views"


@pytest.fixture
def su():
    client = APIClient()
    client.force_authenticate(user=make_admin("pancake_admin"))
    return client


@pytest.fixture
def viewer():
    client = APIClient()
    client.force_authenticate(user=make_user_with_sections("sale", ["pancake-leads"]))
    return client


@pytest.fixture
def stranger():
    client = APIClient()
    client.force_authenticate(user=make_user_with_sections("kho", ["orders"]))
    return client


def _page(**kw):
    values = {"pancake_page_id": "945997011940587", "name": "DELAX", "platform": "facebook"}
    values.update(kw)
    return PancakePage.objects.create(**values)


# ── connection ────────────────────────────────────────────────────────────────

def test_connection_get_khong_lo_token(su):
    PancakeConnection.objects.create(access_token="secret")
    data = su.get(f"{BASE}/connection").json()
    assert data["has_token"] is True
    assert "secret" not in str(data) and "access_token" not in data


def test_connection_get_chua_co(su):
    assert su.get(f"{BASE}/connection").json()["has_token"] is False


def test_connection_put_kiem_tra_ngay(su):
    with patch(f"{VIEWS}.PancakeClient") as client_cls:
        client_cls.return_value.list_pages.return_value = [
            PageDTO(id="1", name="P", platform="facebook", is_activated=True, has_active_subscription=True, role_in_page="ADMINISTER")
        ]
        resp = su.put(f"{BASE}/connection", {"access_token": "tok"}, format="json")
    assert resp.status_code == 200
    assert resp.json()["auth_status"] == "ok"
    conn = PancakeConnection.get_active()
    assert conn.access_token == "tok"
    assert PancakePage.objects.filter(pancake_page_id="1").exists()


def test_connection_put_token_hong_400(su):
    with patch(f"{VIEWS}.PancakeClient") as client_cls:
        client_cls.return_value.list_pages.side_effect = PancakeTokenError("Invalid token")
        resp = su.put(f"{BASE}/connection", {"access_token": "bad"}, format="json")
    assert resp.status_code == 400
    assert "Invalid token" in resp.json()["detail"]
    assert PancakeConnection.get_active().auth_status == "error"


def test_connection_put_thieu_token_400(su):
    assert su.put(f"{BASE}/connection", {"access_token": ""}, format="json").status_code == 400


def test_cau_hinh_chi_superuser(viewer):
    assert viewer.get(f"{BASE}/connection").status_code == 403
    assert viewer.get(f"{BASE}/pages").status_code == 403
    assert viewer.post(f"{BASE}/pages/sync").status_code == 403


# ── pages ─────────────────────────────────────────────────────────────────────

def test_pages_sync(su):
    PancakeConnection.objects.create(access_token="tok")
    with patch(f"{VIEWS}.PancakeClient") as client_cls:
        client_cls.return_value.list_pages.return_value = [
            PageDTO(id="zl_1", name="Zalo", platform="zalo", is_activated=True, has_active_subscription=True, role_in_page="ADMINISTER")
        ]
        resp = su.post(f"{BASE}/pages/sync")
    assert resp.status_code == 200
    assert resp.json()["created"] == 1
    assert resp.json()["pages"][0]["foxia_kind"] == "zalo"


def test_pages_sync_chua_co_token_400(su):
    assert su.post(f"{BASE}/pages/sync").status_code == 400


def test_pages_list_shape(su):
    channel = Channel.objects.create(code="a", name="Kênh A")
    _page(channel=channel, page_access_token="t", token_error="", is_enabled=True)
    _page(pancake_page_id="tts_1", name="Tiktok", platform="tiktok_shop")
    data = su.get(f"{BASE}/pages").json()
    by_id = {p["pancake_page_id"]: p for p in data["pages"]}
    p = by_id["945997011940587"]
    assert (p["channel_id"], p["channel_name"], p["foxia_kind"], p["is_enabled"], p["has_token"]) == (channel.id, "Kênh A", "messenger", True, True)
    assert "page_access_token" not in p
    assert by_id["tts_1"]["foxia_kind"] is None


def test_pages_patch_gan_kenh(su):
    channel = Channel.objects.create(code="a", name="A")
    page = _page()
    resp = su.patch(f"{BASE}/pages/{page.id}", {"channel_id": channel.id}, format="json")
    assert resp.status_code == 200 and resp.json()["channel_id"] == channel.id
    resp = su.patch(f"{BASE}/pages/{page.id}", {"channel_id": None}, format="json")
    assert resp.status_code == 200 and resp.json()["channel_id"] is None


def test_pages_patch_kenh_khong_ton_tai_400(su):
    page = _page()
    assert su.patch(f"{BASE}/pages/{page.id}", {"channel_id": 999999}, format="json").status_code == 400


def test_pages_patch_bat_thieu_kenh_400(su):
    page = _page()
    resp = su.patch(f"{BASE}/pages/{page.id}", {"is_enabled": True}, format="json")
    assert resp.status_code == 400 and "kênh" in resp.json()["detail"]


def test_pages_patch_bat_sinh_token(su):
    PancakeConnection.objects.create(access_token="tok")
    channel = Channel.objects.create(code="a", name="A")
    page = _page()
    with patch(f"{VIEWS}.PancakeClient") as client_cls:
        client_cls.return_value.generate_page_token.return_value = "ptok"
        resp = su.patch(f"{BASE}/pages/{page.id}", {"channel_id": channel.id, "is_enabled": True}, format="json")
    assert resp.status_code == 200
    assert (resp.json()["is_enabled"], resp.json()["has_token"]) == (True, True)
    page.refresh_from_db()
    assert page.page_access_token == "ptok" and page.polled_until is not None


def test_pages_patch_bat_sinh_token_loi_400_khong_bat(su):
    PancakeConnection.objects.create(access_token="tok")
    channel = Channel.objects.create(code="a", name="A")
    page = _page(channel=channel)
    with patch(f"{VIEWS}.PancakeClient") as client_cls:
        client_cls.return_value.generate_page_token.side_effect = PancakeTokenError("Thiếu quyền Admin")
        resp = su.patch(f"{BASE}/pages/{page.id}", {"is_enabled": True}, format="json")
    assert resp.status_code == 400 and "Thiếu quyền Admin" in resp.json()["detail"]
    page.refresh_from_db()
    assert page.is_enabled is False and page.token_error == "Thiếu quyền Admin"


def test_pages_patch_tat(su):
    channel = Channel.objects.create(code="a", name="A")
    page = _page(channel=channel, is_enabled=True, page_access_token="t")
    resp = su.patch(f"{BASE}/pages/{page.id}", {"is_enabled": False}, format="json")
    assert resp.status_code == 200 and resp.json()["is_enabled"] is False


# ── leads ─────────────────────────────────────────────────────────────────────

def _lead(page, channel, phone, **kw):
    values = {"page": page, "channel": channel, "conversation_id": f"c-{phone}", "phone_raw": phone,
              "phone": phone, "foxia_kind": "messenger", "customer_name": "Khách"}
    values.update(kw)
    return PancakeLead.objects.create(**values)


def test_leads_list_theo_quyen(viewer, stranger):
    assert viewer.get(f"{BASE}/leads").status_code == 200
    assert stranger.get(f"{BASE}/leads").status_code == 403


def test_leads_list_loc_va_phan_trang(viewer):
    a = Channel.objects.create(code="a", name="A")
    b = Channel.objects.create(code="b", name="B")
    page = _page(channel=a)
    _lead(page, a, "0901234567", push_status=PancakePushStatus.OK, cohoi_id=5, tags=["BOT"])
    _lead(page, b, "0912345678", push_status=PancakePushStatus.FAILED, customer_name="Tuấn")

    data = viewer.get(f"{BASE}/leads").json()
    assert data["total"] == 2 and [i["phone"] for i in data["items"]] == ["0912345678", "0901234567"]
    assert data["items"][1]["tags"] == ["BOT"] and data["items"][1]["cohoi_id"] == 5
    assert data["items"][1]["page_name"] == "DELAX" and data["items"][1]["channel_name"] == "A"

    assert viewer.get(f"{BASE}/leads", {"push_status": "failed"}).json()["total"] == 1
    assert viewer.get(f"{BASE}/leads", {"channel": b.id}).json()["total"] == 1
    assert viewer.get(f"{BASE}/leads", {"page": page.id}).json()["total"] == 2
    assert viewer.get(f"{BASE}/leads", {"q": "0912"}).json()["total"] == 1
    assert viewer.get(f"{BASE}/leads", {"q": "tuấn"}).json()["total"] == 1
    assert viewer.get(f"{BASE}/leads", {"date_from": "2099-01-01"}).json()["total"] == 0
    paged = viewer.get(f"{BASE}/leads", {"limit": 1, "offset": 1}).json()
    assert (len(paged["items"]), paged["limit"], paged["offset"]) == (1, 1, 1)


def test_leads_repush(viewer, stranger):
    a = Channel.objects.create(code="a", name="A")
    page = _page(channel=a)
    ok = _lead(page, a, "0901234567", push_status=PancakePushStatus.OK)
    failed = _lead(page, a, "0912345678", push_status=PancakePushStatus.FAILED)

    assert stranger.post(f"{BASE}/leads/{failed.id}/repush").status_code == 403
    assert viewer.post(f"{BASE}/leads/{ok.id}/repush").status_code == 409
    with patch(f"{VIEWS}.repush_lead") as task:
        resp = viewer.post(f"{BASE}/leads/{failed.id}/repush")
    assert resp.status_code == 202 and resp.json() == {"status": "queued"}
    task.delay.assert_called_once_with(failed.id)
    assert viewer.post(f"{BASE}/leads/999999/repush").status_code == 404
```

- [ ] **Step 3: Chạy test, xác nhận fail**

Run: `$PYTEST apps/pancake/tests/test_pancake_api.py -q --create-db`
Expected: FAIL 404 / import error (chưa có urls).

- [ ] **Step 4: Viết views + urls**

`backend/apps/pancake/transport/__init__.py`: rỗng.

`backend/apps/pancake/transport/views.py`:
```python
"""API Pancake. Cấu hình (token, page, gán kênh) CHỈ superuser — gán sai kênh là
cơ hội đổ vào nguồn Foxia của kênh khác. Danh sách lead theo quyền `pancake-leads:view`.

Không có endpoint nào trả token (tài khoản hay page) ra ngoài.
"""

from __future__ import annotations

from datetime import datetime, time
from functools import wraps

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.channels_core.models import Channel
from apps.pancake.application.services.page_service import PageNotEligible, PancakePageService
from apps.pancake.infrastructure.pancake_client import PancakeClient, PancakeError, PancakeTokenError
from apps.pancake.models import PancakeConnection, PancakeLead, PancakePage, PancakePushStatus
from apps.pancake.tasks import repush_lead
from apps.users.permissions import require_section
from apps.users.rbac import resolve_effective_permissions

MAX_LIMIT = 200


def superuser_only(view):
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not resolve_effective_permissions(request.user).is_superuser:
            return Response({"detail": "Chỉ quản trị viên."}, status=status.HTTP_403_FORBIDDEN)
        return view(request, *args, **kwargs)

    return _wrapped


def _bad(detail: str) -> Response:
    return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)


def _client() -> tuple[PancakeConnection | None, PancakeClient | None]:
    """Dựng client NGAY TẠI ĐÂY (không qua page_service.get_client) để test patch
    được `views.PancakeClient` — mọi lời gọi mạng từ API đều đi qua một cửa."""
    conn = PancakeConnection.get_active()
    if conn is None or not conn.access_token:
        return conn, None
    return conn, PancakeClient(conn.access_token)


def _int_param(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _connection_out(conn: PancakeConnection | None) -> dict:
    if conn is None:
        return {"has_token": False, "auth_status": "unknown", "auth_error": "", "last_pages_synced_at": None, "name": ""}
    return {
        "has_token": bool(conn.access_token),
        "auth_status": conn.auth_status,
        "auth_error": conn.auth_error,
        "last_pages_synced_at": conn.last_pages_synced_at,
        "name": conn.name,
    }


def _page_out(page: PancakePage) -> dict:
    return {
        "id": page.id,
        "pancake_page_id": page.pancake_page_id,
        "name": page.name,
        "platform": page.platform,
        "is_activated": page.is_activated,
        "has_active_subscription": page.has_active_subscription,
        "role_in_page": page.role_in_page,
        "channel_id": page.channel_id,
        "channel_name": page.channel.name if page.channel_id else "",
        "foxia_kind": page.foxia_kind,
        "is_enabled": page.is_enabled,
        "has_token": bool(page.page_access_token),
        "token_error": page.token_error,
        "polled_until": page.polled_until,
        "last_polled_at": page.last_polled_at,
        "last_error": page.last_error,
    }


def _pages_out() -> list[dict]:
    return [_page_out(p) for p in PancakePage.objects.select_related("channel")]


def _lead_out(lead: PancakeLead) -> dict:
    return {
        "id": lead.id,
        "page_id": lead.page_id,
        "pancake_page_id": lead.page.pancake_page_id,
        "page_name": lead.page.name,
        "channel_id": lead.channel_id,
        "channel_name": lead.channel.name,
        "conversation_id": lead.conversation_id,
        "conversation_inserted_at": lead.conversation_inserted_at,
        "customer_name": lead.customer_name,
        "phone": lead.phone,
        "tags": lead.tags or [],
        "phone_given_at": lead.phone_given_at,
        "assignee_name": lead.assignee_name,
        "created_at": lead.created_at,
        "foxia_kind": lead.foxia_kind,
        "foxia_source_id": lead.foxia_source_id,
        "push_status": lead.push_status,
        "http_status": lead.http_status,
        "error": lead.error,
        "pushed_at": lead.pushed_at,
        "cohoi_id": lead.cohoi_id,
    }


# ── connection ────────────────────────────────────────────────────────────────

@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
@superuser_only
def connection(request):
    if request.method == "GET":
        return Response(_connection_out(PancakeConnection.get_active()))

    token = str(request.data.get("access_token") or "").strip()
    if not token:
        return _bad("Thiếu access token.")
    conn = PancakeConnection.get_active() or PancakeConnection(name="Pancake")
    conn.access_token = token
    conn.is_active = True
    conn.save()
    # Kiểm tra ngay bằng cách đồng bộ page: token sai phải lộ lúc nhập, không phải
    # 3 phút sau trong log worker.
    try:
        PancakePageService(PancakeClient(token)).sync_pages(conn)
    except PancakeTokenError as exc:
        return _bad(f"Pancake từ chối token: {exc}")
    except PancakeError as exc:
        return _bad(f"Không gọi được Pancake: {exc}")
    return Response(_connection_out(conn))


# ── pages ─────────────────────────────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([IsAuthenticated])
@superuser_only
def sync_pages(request):
    conn, client = _client()
    if client is None:
        return _bad("Chưa nhập access token Pancake.")
    try:
        result = PancakePageService(client).sync_pages(conn)
    except PancakeTokenError as exc:
        return _bad(f"Pancake từ chối token: {exc}")
    except PancakeError as exc:
        return _bad(f"Không gọi được Pancake: {exc}")
    return Response(
        {
            "fetched": result.fetched,
            "created": result.created,
            "updated": result.updated,
            "deactivated": result.deactivated,
            "pages": _pages_out(),
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@superuser_only
def list_pages(request):
    return Response({"pages": _pages_out()})


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
@superuser_only
def update_page(request, pk: int):
    page = PancakePage.objects.select_related("channel").filter(pk=pk).first()
    if page is None:
        return Response({"detail": "Không tìm thấy page."}, status=status.HTTP_404_NOT_FOUND)

    if "channel_id" in request.data:
        channel_id = request.data.get("channel_id")
        if channel_id in (None, ""):
            page.channel = None
        else:
            channel = Channel.objects.filter(pk=channel_id, deleted_at__isnull=True).first()
            if channel is None:
                return _bad("Kênh bán không tồn tại.")
            page.channel = channel
        page.save(update_fields=["channel"])

    if "is_enabled" in request.data:
        enable = bool(request.data.get("is_enabled"))
        if enable:
            _, client = _client()
            if client is None:
                return _bad("Chưa nhập access token Pancake.")
            try:
                PancakePageService(client).enable_page(page)
            except PageNotEligible as exc:
                return _bad(str(exc))
            except PancakeTokenError as exc:
                return _bad(f"Không sinh được token page: {exc}")
            except PancakeError as exc:
                return _bad(f"Không gọi được Pancake: {exc}")
        else:
            # Tắt chỉ hạ cờ: giữ token + cursor, bật lại thì enable_page đặt lại cursor.
            page.is_enabled = False
            page.save(update_fields=["is_enabled"])

    page.refresh_from_db()
    return Response(_page_out(page))


# ── leads ─────────────────────────────────────────────────────────────────────

def _parse_day(value: str, end: bool) -> datetime | None:
    day = parse_date(value or "")
    if day is None:
        return None
    return timezone.make_aware(datetime.combine(day, time.max if end else time.min))


@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("pancake-leads")])
def list_leads(request):
    qs = PancakeLead.objects.select_related("page", "channel").order_by("-created_at", "-id")
    params = request.query_params
    if (page_id := _int_param(params.get("page"))) is not None:
        qs = qs.filter(page_id=page_id)
    if (channel_id := _int_param(params.get("channel"))) is not None:
        qs = qs.filter(channel_id=channel_id)
    if params.get("push_status") in PancakePushStatus.values:
        qs = qs.filter(push_status=params["push_status"])
    q = str(params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(phone__contains=q) | Q(customer_name__icontains=q))
    if (start := _parse_day(params.get("date_from"), end=False)) is not None:
        qs = qs.filter(created_at__gte=start)
    if (stop := _parse_day(params.get("date_to"), end=True)) is not None:
        qs = qs.filter(created_at__lte=stop)

    try:
        limit = max(1, min(int(params.get("limit", 50)), MAX_LIMIT))
        offset = max(0, int(params.get("offset", 0)))
    except (TypeError, ValueError):
        return _bad("limit/offset không hợp lệ.")
    total = qs.count()
    items = [_lead_out(lead) for lead in qs[offset : offset + limit]]
    return Response({"items": items, "total": total, "limit": limit, "offset": offset})


@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("pancake-leads")])
def repush(request, pk: int):
    lead = PancakeLead.objects.filter(pk=pk).first()
    if lead is None:
        return Response({"detail": "Không tìm thấy lead."}, status=status.HTTP_404_NOT_FOUND)
    if lead.push_status not in (PancakePushStatus.FAILED, PancakePushStatus.SKIPPED_NO_MAPPING):
        return Response({"detail": "Chỉ đẩy lại lead thất bại hoặc chưa cấu hình id."}, status=status.HTTP_409_CONFLICT)
    repush_lead.delay(lead.id)
    return Response({"status": "queued"}, status=status.HTTP_202_ACCEPTED)
```

`backend/apps/pancake/urls.py`:
```python
from django.urls import path

from apps.pancake.transport.views import connection, list_leads, list_pages, repush, sync_pages, update_page

urlpatterns = [
    path("connection", connection, name="pancake-connection"),
    path("pages", list_pages, name="pancake-pages"),
    path("pages/sync", sync_pages, name="pancake-pages-sync"),
    path("pages/<int:pk>", update_page, name="pancake-page"),
    path("leads", list_leads, name="pancake-leads"),
    path("leads/<int:pk>/repush", repush, name="pancake-lead-repush"),
]
```

`backend/config/urls.py`, sau dòng `re_path(r"^api/foxia/", include("apps.foxia.urls")),`:
```python
    re_path(r"^api/pancake/", include("apps.pancake.urls")),
```

- [ ] **Step 5: Chạy test**

Run: `$PYTEST apps/pancake apps/users/tests/test_rbac_sections.py -q --reuse-db`
Expected: tất cả passed (`test_rbac_sections` chốt section mới có mặt ở cả VALID_SECTIONS lẫn catalog khi cần).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/pancake backend/apps/users backend/config/urls.py
git commit -m "feat(pancake): API kết nối, đồng bộ/gán page, danh sách lead + đẩy lại; quyền pancake-leads:view"
```

---

### Task 8: Sinh lại hợp đồng OpenAPI

**Files:**
- Modify: `backend/schema.yaml`, `frontend/src/api/schema.d.ts`

- [ ] **Step 1: Sinh schema BE**

Run (cùng env block backend):
```bash
$PY manage.py spectacular --file schema.yaml 2>/tmp/spectacular.log; grep -c 'Error \[' /tmp/spectacular.log || true
git diff --stat schema.yaml
```
Expected: không có dòng `Error [`; diff chỉ thêm path `/api/pancake/…` (nếu diff có thay đổi ngoài pancake thì đó là drift có sẵn — giữ nguyên, vẫn commit vì CI so `schema.yaml` với code).

- [ ] **Step 2: Sinh types FE**

Run: `docker run --rm -v /home/desi:/home/desi -w /home/desi/desi-order-pancake/frontend node:22-alpine npm run gen:api`
Expected: `src/api/schema.d.ts` đổi, đã qua prettier.

- [ ] **Step 3: Commit**

```bash
git add backend/schema.yaml frontend/src/api/schema.d.ts
git commit -m "chore(api): sinh lại schema OpenAPI cho /api/pancake"
```

---

### Task 9: FE — client API, query hooks, i18n, đăng ký route/module

**Files:**
- Create: `frontend/src/lib/api/pancake.ts`, `frontend/src/features/pancake/queries.ts`, `frontend/src/i18n/messages/pancake.ts`
- Modify: `frontend/src/lib/routes.ts`, `frontend/src/app/modules.ts`, `frontend/src/app/routes.tsx`, `frontend/src/app/routed-pages.tsx`, `frontend/src/app/__tests__/modules.test.ts`, `frontend/src/i18n/messages.ts`, `frontend/src/i18n/messages/topnav.ts`

**Interfaces (Produces):** types `PancakeConnection`, `PancakePage`, `PancakeLead`, `PancakeLeadsResponse`, `PancakeLeadsParams`; hàm `getConnection`, `saveConnection`, `syncPages`, `listPages`, `updatePage`, `listLeads`, `repushLead`; hooks `usePancakeConnection`, `usePancakePages`, `usePancakeLeads(params)`; keys `pancakeKeys`.

- [ ] **Step 1: Client API**

`frontend/src/lib/api/pancake.ts`:
```ts
import { fetchJson } from "@/services/http";

/**
 * Tích hợp Pancake: token tài khoản, page ↔ kênh bán, lead (sđt khách cho trong
 * hội thoại) + trạng thái đẩy Foxia. Chỉ gọi mạng; điều phối ở call site.
 */

export type PancakeConnection = {
  has_token: boolean;
  auth_status: "unknown" | "ok" | "error";
  auth_error: string;
  last_pages_synced_at: string | null;
  name: string;
};

export type PancakePage = {
  id: number;
  pancake_page_id: string;
  name: string;
  platform: string;
  is_activated: boolean;
  has_active_subscription: boolean;
  role_in_page: string;
  channel_id: number | null;
  channel_name: string;
  /** Cột nguồn Foxia suy từ platform; null = nền tảng không hỗ trợ, không gán được. */
  foxia_kind: "messenger" | "zalo" | null;
  is_enabled: boolean;
  has_token: boolean;
  token_error: string;
  polled_until: string | null;
  last_polled_at: string | null;
  last_error: string;
};

export type PancakePushStatus = "pending" | "ok" | "failed" | "skipped_no_mapping";

export type PancakeLead = {
  id: number;
  page_id: number;
  pancake_page_id: string;
  page_name: string;
  channel_id: number;
  channel_name: string;
  conversation_id: string;
  conversation_inserted_at: string | null;
  customer_name: string;
  phone: string;
  tags: string[];
  phone_given_at: string | null;
  assignee_name: string;
  created_at: string;
  foxia_kind: string;
  foxia_source_id: number | null;
  push_status: PancakePushStatus;
  http_status: number | null;
  error: string;
  pushed_at: string | null;
  cohoi_id: number | null;
};

/** Shape phân trang chuẩn repo: {items,total,limit,offset}. */
export type PancakeLeadsResponse = {
  items: PancakeLead[];
  total: number;
  limit: number;
  offset: number;
};

export type PancakeLeadsParams = {
  page?: number | "";
  channel?: number | "";
  push_status?: PancakePushStatus | "";
  q?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
};

const BASE = "/api/pancake";

export async function getConnection(): Promise<PancakeConnection> {
  return (await fetchJson(`${BASE}/connection`)) as PancakeConnection;
}

export async function saveConnection(accessToken: string): Promise<PancakeConnection> {
  return (await fetchJson(`${BASE}/connection`, {
    method: "PUT",
    body: JSON.stringify({ access_token: accessToken }),
  })) as PancakeConnection;
}

export type SyncPagesResult = {
  fetched: number;
  created: number;
  updated: number;
  deactivated: number;
  pages: PancakePage[];
};

export async function syncPages(): Promise<SyncPagesResult> {
  return (await fetchJson(`${BASE}/pages/sync`, { method: "POST" })) as SyncPagesResult;
}

export async function listPages(): Promise<PancakePage[]> {
  const data = (await fetchJson(`${BASE}/pages`)) as { pages: PancakePage[] };
  return data.pages ?? [];
}

export async function updatePage(
  id: number,
  patch: { channel_id?: number | null; is_enabled?: boolean },
): Promise<PancakePage> {
  return (await fetchJson(`${BASE}/pages/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  })) as PancakePage;
}

export async function listLeads(params: PancakeLeadsParams = {}): Promise<PancakeLeadsResponse> {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "" || value === null) continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return (await fetchJson(`${BASE}/leads${qs ? `?${qs}` : ""}`)) as PancakeLeadsResponse;
}

export async function repushLead(id: number): Promise<{ status: string }> {
  return (await fetchJson(`${BASE}/leads/${id}/repush`, { method: "POST" })) as { status: string };
}
```

- [ ] **Step 2: Query hooks**

`frontend/src/features/pancake/queries.ts`:
```ts
import { useQuery } from "@tanstack/react-query";

import { getConnection, listLeads, listPages, type PancakeLeadsParams } from "@/lib/api/pancake";

export const pancakeKeys = {
  all: ["pancake"] as const,
  connection: ["pancake", "connection"] as const,
  pages: ["pancake", "pages"] as const,
  leads: (params: PancakeLeadsParams) => ["pancake", "leads", params] as const,
};

export function usePancakeConnection() {
  return useQuery({ queryKey: pancakeKeys.connection, queryFn: getConnection });
}

export function usePancakePages() {
  return useQuery({ queryKey: pancakeKeys.pages, queryFn: listPages });
}

export function usePancakeLeads(params: PancakeLeadsParams) {
  return useQuery({ queryKey: pancakeKeys.leads(params), queryFn: () => listLeads(params) });
}
```

- [ ] **Step 3: i18n**

`frontend/src/i18n/messages/pancake.ts`:
```ts
export const pancake = {
  vi: {
    "pancake.settings.title": "Pancake",
    "pancake.settings.description":
      "Nhập token của tài khoản Pancake có quyền Admin trên các page, đồng bộ page về rồi gán từng page với kênh bán. Số điện thoại khách cho trong hội thoại của page đã bật sẽ được kéo về mỗi 3 phút và đẩy sang Foxia theo id ở cột Messenger (page Facebook) hoặc Zalo (page Zalo) của Cấu hình liên kết Foxia.",
    "pancake.connection.title": "Kết nối",
    "pancake.connection.tokenLabel": "Access token",
    "pancake.connection.tokenPlaceholder": "Dán access token Pancake",
    "pancake.connection.hasToken": "Đã có token — để trống nếu giữ nguyên",
    "pancake.connection.save": "Lưu & kiểm tra",
    "pancake.connection.saved": "Đã lưu token và đồng bộ page",
    "pancake.connection.saveFailed": "Lưu token thất bại",
    "pancake.connection.status.unknown": "Chưa kiểm tra",
    "pancake.connection.status.ok": "Hợp lệ",
    "pancake.connection.status.error": "Lỗi",
    "pancake.connection.lastSynced": "Đồng bộ page lần cuối: {time}",
    "pancake.pages.title": "Page",
    "pancake.pages.sync": "Đồng bộ page",
    "pancake.pages.synced": "Đã đồng bộ {fetched} page (mới {created}, ẩn {deactivated})",
    "pancake.pages.syncFailed": "Đồng bộ page thất bại",
    "pancake.pages.empty": "Chưa có page nào — nhập token rồi bấm Đồng bộ page.",
    "pancake.pages.col.name": "Tên page",
    "pancake.pages.col.platform": "Nền tảng",
    "pancake.pages.col.channel": "Kênh bán",
    "pancake.pages.col.enabled": "Bật",
    "pancake.pages.col.lastPolled": "Poll lần cuối",
    "pancake.pages.col.error": "Lỗi",
    "pancake.pages.noChannel": "— Chưa gán —",
    "pancake.pages.unsupported": "Nền tảng chưa có cột nguồn Foxia",
    "pancake.pages.inactive": "Không còn trong danh sách Pancake",
    "pancake.pages.updated": "Đã cập nhật page",
    "pancake.pages.updateFailed": "Cập nhật page thất bại",
    "pancake.leads.title": "Lead Pancake",
    "pancake.leads.description": "Số điện thoại khách cho trong hội thoại Pancake và kết quả đẩy sang Foxia.",
    "pancake.leads.filter.page": "Page",
    "pancake.leads.filter.channel": "Kênh bán",
    "pancake.leads.filter.status": "Trạng thái đẩy",
    "pancake.leads.filter.q": "Tìm sđt / tên",
    "pancake.leads.filter.from": "Từ ngày",
    "pancake.leads.filter.to": "Đến ngày",
    "pancake.leads.filter.all": "Tất cả",
    "pancake.leads.col.page": "Page Pancake",
    "pancake.leads.col.customer": "Tên khách hàng",
    "pancake.leads.col.convCreated": "Ngày tạo hội thoại",
    "pancake.leads.col.phone": "Số điện thoại",
    "pancake.leads.col.tags": "Thẻ Pancake",
    "pancake.leads.col.created": "Ngày tạo",
    "pancake.leads.col.phoneGiven": "Ngày cho số điện thoại",
    "pancake.leads.col.assignee": "Nhân viên Pancake",
    "pancake.leads.col.push": "Đẩy Foxia",
    "pancake.leads.status.pending": "Chờ đẩy",
    "pancake.leads.status.ok": "Thành công",
    "pancake.leads.status.failed": "Thất bại",
    "pancake.leads.status.skipped_no_mapping": "Chưa cấu hình id",
    "pancake.leads.repush": "Đẩy lại",
    "pancake.leads.repushQueued": "Đã xếp lịch đẩy lại",
    "pancake.leads.repushFailed": "Đẩy lại thất bại",
    "pancake.leads.empty": "Chưa có lead nào.",
    "pancake.leads.total": "{total} lead",
  },
  en: {
    "pancake.settings.title": "Pancake",
    "pancake.settings.description":
      "Enter the access token of a Pancake account with Admin rights on the pages, sync pages, then map each page to a sales channel. Phone numbers customers give in conversations of enabled pages are pulled every 3 minutes and pushed to Foxia using the Messenger (Facebook page) or Zalo (Zalo page) column of the Foxia link configuration.",
    "pancake.connection.title": "Connection",
    "pancake.connection.tokenLabel": "Access token",
    "pancake.connection.tokenPlaceholder": "Paste the Pancake access token",
    "pancake.connection.hasToken": "Token saved — leave blank to keep it",
    "pancake.connection.save": "Save & verify",
    "pancake.connection.saved": "Token saved and pages synced",
    "pancake.connection.saveFailed": "Failed to save token",
    "pancake.connection.status.unknown": "Not verified",
    "pancake.connection.status.ok": "Valid",
    "pancake.connection.status.error": "Error",
    "pancake.connection.lastSynced": "Pages last synced: {time}",
    "pancake.pages.title": "Pages",
    "pancake.pages.sync": "Sync pages",
    "pancake.pages.synced": "Synced {fetched} pages (new {created}, hidden {deactivated})",
    "pancake.pages.syncFailed": "Failed to sync pages",
    "pancake.pages.empty": "No pages yet — enter a token and click Sync pages.",
    "pancake.pages.col.name": "Page name",
    "pancake.pages.col.platform": "Platform",
    "pancake.pages.col.channel": "Sales channel",
    "pancake.pages.col.enabled": "Enabled",
    "pancake.pages.col.lastPolled": "Last polled",
    "pancake.pages.col.error": "Error",
    "pancake.pages.noChannel": "— Not mapped —",
    "pancake.pages.unsupported": "Platform has no Foxia source column",
    "pancake.pages.inactive": "No longer listed in Pancake",
    "pancake.pages.updated": "Page updated",
    "pancake.pages.updateFailed": "Failed to update page",
    "pancake.leads.title": "Pancake leads",
    "pancake.leads.description": "Phone numbers customers gave in Pancake conversations and the Foxia push result.",
    "pancake.leads.filter.page": "Page",
    "pancake.leads.filter.channel": "Sales channel",
    "pancake.leads.filter.status": "Push status",
    "pancake.leads.filter.q": "Search phone / name",
    "pancake.leads.filter.from": "From",
    "pancake.leads.filter.to": "To",
    "pancake.leads.filter.all": "All",
    "pancake.leads.col.page": "Pancake page",
    "pancake.leads.col.customer": "Customer",
    "pancake.leads.col.convCreated": "Conversation created",
    "pancake.leads.col.phone": "Phone",
    "pancake.leads.col.tags": "Pancake tags",
    "pancake.leads.col.created": "Created",
    "pancake.leads.col.phoneGiven": "Phone given at",
    "pancake.leads.col.assignee": "Pancake staff",
    "pancake.leads.col.push": "Foxia push",
    "pancake.leads.status.pending": "Pending",
    "pancake.leads.status.ok": "OK",
    "pancake.leads.status.failed": "Failed",
    "pancake.leads.status.skipped_no_mapping": "No source id",
    "pancake.leads.repush": "Push again",
    "pancake.leads.repushQueued": "Push queued",
    "pancake.leads.repushFailed": "Failed to queue push",
    "pancake.leads.empty": "No leads yet.",
    "pancake.leads.total": "{total} leads",
  },
};
```
`frontend/src/i18n/messages.ts`: import `{ pancake } from "./messages/pancake"` và spread `...pancake.vi` / `...pancake.en` (theo thứ tự alphabet cạnh `page`).
`frontend/src/i18n/messages/topnav.ts`: thêm `"topnav.tabs.pancake": "Pancake"` (vi + en) và `"topnav.tabs.pancakeLeads": "Lead Pancake"` (vi) / `"Pancake leads"` (en).

- [ ] **Step 4: Đăng ký route/module**

`frontend/src/lib/routes.ts`: thêm `| "pancake"` và `| "pancake-leads"` vào union `AppSection` (cạnh `"foxia-link"`), và trong `SECTION_TO_PATH`: `pancake: "/settings/pancake"`, `"pancake-leads": "/sales/pancake-leads"`.

`frontend/src/app/modules.ts`:
- Module `cskh` (basePath `/sales`), sau `resale-tracking`: `{ key: "pancake-leads", labelKey: "topnav.tabs.pancakeLeads", icon: MessageSquare }` (import `MessageSquare` từ lucide-react).
- Module `settings`, sau `foxia-link`: `{ key: "pancake", labelKey: "topnav.tabs.pancake", icon: Plug }`.

`frontend/src/app/routed-pages.tsx`: thêm lazy import + 2 hàm route:
```tsx
const PancakeSettingsPage = lazy(() => import("@/features/pancake/pages/PancakeSettingsPage"));
const PancakeLeadsPage = lazy(() => import("@/features/pancake/pages/PancakeLeadsPage"));
export function PancakeSettingsRoute() { return <PancakeSettingsPage />; }
export function PancakeLeadsRoute() { return <PancakeLeadsPage />; }
```
`frontend/src/app/routes.tsx`: import 2 route; sau block `settings/foxia-link`:
```tsx
      {
        // Gán page ↔ kênh quyết định cơ hội chảy vào nguồn Foxia nào — gate superuser như foxia-link.
        path: "settings/pancake",
        element: (
          <GuardSuperuser>
            <PancakeSettingsRoute />
          </GuardSuperuser>
        ),
      },
```
và cạnh route `sales/resale-tracking`:
```tsx
      {
        path: "sales/pancake-leads",
        element: (
          <Guard section="pancake-leads">
            <PancakeLeadsRoute />
          </Guard>
        ),
      },
```
`frontend/src/app/__tests__/modules.test.ts`: thêm `"pancake"`, `"pancake-leads"` vào `ALL_SECTIONS`; thêm 2 assert `SECTION_TO_MODULE["pancake"].key === "settings"`, `SECTION_TO_MODULE["pancake-leads"].key === "cskh"`.

- [ ] **Step 5: Tạo 2 trang tạm (placeholder) để typecheck qua**

`frontend/src/features/pancake/pages/PancakeSettingsPage.tsx` và `PancakeLeadsPage.tsx` tạm: `export default function PancakeSettingsPage() { return null; }` (Task 10/11 thay thế).

- [ ] **Step 6: Typecheck + test modules**

Run: `docker run --rm -v /home/desi:/home/desi -w /home/desi/desi-order-pancake/frontend node:22-alpine npm run typecheck`
Expected: 0 lỗi.
Run: `docker run --rm -v /home/desi:/home/desi -w /home/desi/desi-order-pancake/frontend node:22-alpine npx vitest run src/app/__tests__/modules.test.ts src/i18n/messages.test.ts`
Expected: passed.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/api/pancake.ts frontend/src/features/pancake frontend/src/i18n frontend/src/lib/routes.ts frontend/src/app
git commit -m "feat(pancake-ui): client API, query hooks, i18n, đăng ký route /settings/pancake và /sales/pancake-leads"
```

---

### Task 10: FE — trang Cài đặt → Pancake

**Files:**
- Modify (thay placeholder): `frontend/src/features/pancake/pages/PancakeSettingsPage.tsx`
- Test: `frontend/src/features/pancake/__tests__/PancakeSettingsPage.test.tsx`

**Interfaces:** Consumes Task 9 (`@/lib/api/pancake`, `@/features/pancake/queries`), `listChannels` từ `@/lib/api/channels` (trả `ChannelRow[]` có `id`, `name`, `is_active`).

- [ ] **Step 1: Viết test**

`frontend/src/features/pancake/__tests__/PancakeSettingsPage.test.tsx`:
```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/pancake", () => ({
  getConnection: vi.fn(),
  saveConnection: vi.fn(),
  syncPages: vi.fn(),
  listPages: vi.fn(),
  updatePage: vi.fn(),
}));
vi.mock("@/lib/api/channels", () => ({ listChannels: vi.fn(async () => []) }));
vi.mock("sonner", async () => (await import("@/test/utils")).makeToastMock());

import { toast } from "sonner";

import * as api from "@/lib/api/pancake";
import * as channelsApi from "@/lib/api/channels";
import PancakeSettingsPage from "@/features/pancake/pages/PancakeSettingsPage";
import { I18nProvider } from "@/i18n";
import { renderWithClient } from "@/test/utils";

const CONNECTION = { has_token: true, auth_status: "ok" as const, auth_error: "", last_pages_synced_at: "2026-09-16T02:00:00Z", name: "Pancake" };
const PAGE = {
  id: 1, pancake_page_id: "945997011940587", name: "DELAX", platform: "facebook", is_activated: true,
  has_active_subscription: true, role_in_page: "ADMINISTER", channel_id: null, channel_name: "",
  foxia_kind: "messenger" as const, is_enabled: false, has_token: false, token_error: "",
  polled_until: null, last_polled_at: null, last_error: "",
};
const TIKTOK = { ...PAGE, id: 2, pancake_page_id: "tts_1", name: "Tiktok Shop", platform: "tiktok_shop", foxia_kind: null };
const CHANNELS = [
  { id: 11, name: "Kênh A", code: "a", is_active: true },
  { id: 12, name: "Kênh ẩn", code: "b", is_active: false },
];

function renderPage() {
  return renderWithClient(
    <I18nProvider>
      <PancakeSettingsPage />
    </I18nProvider>,
  );
}

describe("PancakeSettingsPage", () => {
  beforeEach(() => {
    vi.mocked(api.getConnection).mockResolvedValue(CONNECTION);
    vi.mocked(api.listPages).mockResolvedValue([PAGE, TIKTOK]);
    vi.mocked(channelsApi.listChannels).mockResolvedValue(CHANNELS as never);
  });

  it("hiện trạng thái kết nối và bảng page; page nền tảng lạ bị khoá select", async () => {
    renderPage();
    expect(await screen.findByText("Hợp lệ")).toBeInTheDocument();
    expect(await screen.findByText("DELAX")).toBeInTheDocument();
    const select = screen.getByLabelText("Kênh bán: DELAX") as HTMLSelectElement;
    const options = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["— Chưa gán —", "Kênh A"]); // kênh is_active=false không hiện
    expect(screen.getByLabelText("Kênh bán: Tiktok Shop")).toBeDisabled();
    expect(screen.getByLabelText("Bật: Tiktok Shop")).toBeDisabled();
    // Chưa gán kênh → không bật được.
    expect(screen.getByLabelText("Bật: DELAX")).toBeDisabled();
  });

  it("lưu token gọi saveConnection và báo thành công", async () => {
    const user = userEvent.setup();
    vi.mocked(api.saveConnection).mockResolvedValue({ ...CONNECTION, auth_status: "ok" });
    renderPage();
    await screen.findByText("DELAX");
    await user.type(screen.getByLabelText("Access token"), "tok-moi");
    await user.click(screen.getByRole("button", { name: "Lưu & kiểm tra" }));
    await waitFor(() => expect(api.saveConnection).toHaveBeenCalledWith("tok-moi"));
    expect(toast.success).toHaveBeenCalledWith("Đã lưu token và đồng bộ page");
  });

  it("token sai hiện thông báo lỗi của BE", async () => {
    const user = userEvent.setup();
    vi.mocked(api.saveConnection).mockRejectedValue(new Error("Pancake từ chối token: Invalid token"));
    renderPage();
    await screen.findByText("DELAX");
    await user.type(screen.getByLabelText("Access token"), "sai");
    await user.click(screen.getByRole("button", { name: "Lưu & kiểm tra" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Pancake từ chối token: Invalid token"));
  });

  it("gán kênh rồi bật page gọi updatePage; lỗi 400 hiện detail và giữ nguyên switch", async () => {
    const user = userEvent.setup();
    vi.mocked(api.updatePage).mockResolvedValueOnce({ ...PAGE, channel_id: 11, channel_name: "Kênh A" });
    renderPage();
    await screen.findByText("DELAX");
    // Sau PATCH trang invalidate + refetch: mock list phải trả page đã gán kênh.
    vi.mocked(api.listPages).mockResolvedValue([{ ...PAGE, channel_id: 11, channel_name: "Kênh A" }, TIKTOK]);
    await user.selectOptions(screen.getByLabelText("Kênh bán: DELAX"), "11");
    await waitFor(() => expect(api.updatePage).toHaveBeenCalledWith(1, { channel_id: 11 }));

    vi.mocked(api.updatePage).mockRejectedValueOnce(new Error("Không sinh được token page: Thiếu quyền Admin"));
    await waitFor(() => expect(screen.getByLabelText("Bật: DELAX")).toBeEnabled());
    await user.click(screen.getByLabelText("Bật: DELAX"));
    await waitFor(() => expect(api.updatePage).toHaveBeenCalledWith(1, { is_enabled: true }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Không sinh được token page: Thiếu quyền Admin"));
    expect(screen.getByLabelText("Bật: DELAX")).not.toBeChecked();
  });

  it("đồng bộ page báo số lượng", async () => {
    const user = userEvent.setup();
    vi.mocked(api.syncPages).mockResolvedValue({ fetched: 3, created: 1, updated: 2, deactivated: 0, pages: [PAGE] });
    renderPage();
    await screen.findByText("DELAX");
    await user.click(screen.getByRole("button", { name: "Đồng bộ page" }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Đã đồng bộ 3 page (mới 1, ẩn 0)"));
  });
});
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `docker run --rm -v /home/desi:/home/desi -w /home/desi/desi-order-pancake/frontend node:22-alpine npx vitest run src/features/pancake/__tests__/PancakeSettingsPage.test.tsx`
Expected: FAIL (placeholder render null).

- [ ] **Step 3: Viết trang**

`frontend/src/features/pancake/pages/PancakeSettingsPage.tsx`:
```tsx
import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { listChannels } from "@/lib/api/channels";
import { saveConnection, syncPages, updatePage, type PancakePage } from "@/lib/api/pancake";
import { formatDateTime } from "@/lib/utils";
import { pancakeKeys, usePancakeConnection, usePancakePages } from "@/features/pancake/queries";

/**
 * Cài đặt → Pancake (superuser-only): token tài khoản, đồng bộ page, gán page ↔
 * kênh bán, bật/tắt poll. Bật page cần kênh + nền tảng có cột Foxia (BE validate
 * lại; FE chỉ khoá control để người dùng không bấm vào chỗ chắc chắn 400).
 */
export default function PancakeSettingsPage() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const connectionQuery = usePancakeConnection();
  const pagesQuery = usePancakePages();
  const channelsQuery = useQuery({ queryKey: ["channels", "for-pancake"], queryFn: listChannels });

  const [token, setToken] = useState("");
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [busyPage, setBusyPage] = useState<number | null>(null);

  const connection = connectionQuery.data;
  const pages = pagesQuery.data ?? [];
  const channels = (channelsQuery.data ?? []).filter((c) => c.is_active);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: pancakeKeys.all });

  const handleSaveToken = async () => {
    const value = token.trim();
    if (!value) return;
    setSaving(true);
    try {
      await saveConnection(value);
      setToken("");
      toast.success(t("pancake.connection.saved"));
      await invalidate();
    } catch (error) {
      toast.error((error as Error)?.message || t("pancake.connection.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      const result = await syncPages();
      toast.success(
        t("pancake.pages.synced")
          .replace("{fetched}", String(result.fetched))
          .replace("{created}", String(result.created))
          .replace("{deactivated}", String(result.deactivated)),
      );
      await invalidate();
    } catch (error) {
      toast.error((error as Error)?.message || t("pancake.pages.syncFailed"));
    } finally {
      setSyncing(false);
    }
  };

  const patchPage = async (page: PancakePage, patch: { channel_id?: number | null; is_enabled?: boolean }) => {
    setBusyPage(page.id);
    try {
      await updatePage(page.id, patch);
      toast.success(t("pancake.pages.updated"));
    } catch (error) {
      toast.error((error as Error)?.message || t("pancake.pages.updateFailed"));
    } finally {
      setBusyPage(null);
      await invalidate();
    }
  };

  const statusLabel = (status: string) => t(`pancake.connection.status.${status}`, status);

  return (
    <div className="space-y-6">
      <PageHeader title={t("pancake.settings.title")} description={t("pancake.settings.description")} />

      <Card>
        <CardHeader>
          <CardTitle>{t("pancake.connection.title")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Badge variant={connection?.auth_status === "ok" ? "default" : "secondary"}>
              {statusLabel(connection?.auth_status ?? "unknown")}
            </Badge>
            {connection?.auth_error && <span className="text-destructive">{connection.auth_error}</span>}
            {connection?.last_pages_synced_at && (
              <span className="text-muted-foreground">
                {t("pancake.connection.lastSynced").replace("{time}", formatDateTime(connection.last_pages_synced_at))}
              </span>
            )}
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <div className="flex-1 space-y-1">
              <label htmlFor="pancake-token" className="text-sm font-medium">
                {t("pancake.connection.tokenLabel")}
              </label>
              <Input
                id="pancake-token"
                type="password"
                autoComplete="off"
                value={token}
                placeholder={connection?.has_token ? t("pancake.connection.hasToken") : t("pancake.connection.tokenPlaceholder")}
                onChange={(event) => setToken(event.target.value)}
              />
            </div>
            <Button onClick={handleSaveToken} disabled={saving || !token.trim()}>
              {t("pancake.connection.save")}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>{t("pancake.pages.title")}</CardTitle>
          <Button variant="outline" onClick={handleSync} disabled={syncing || !connection?.has_token}>
            <RefreshCw className="mr-2 h-4 w-4" />
            {t("pancake.pages.sync")}
          </Button>
        </CardHeader>
        <CardContent>
          {pages.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("pancake.pages.empty")}</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("pancake.pages.col.name")}</TableHead>
                  <TableHead>{t("pancake.pages.col.platform")}</TableHead>
                  <TableHead>{t("pancake.pages.col.channel")}</TableHead>
                  <TableHead>{t("pancake.pages.col.enabled")}</TableHead>
                  <TableHead>{t("pancake.pages.col.lastPolled")}</TableHead>
                  <TableHead>{t("pancake.pages.col.error")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pages.map((page) => {
                  const unsupported = page.foxia_kind === null;
                  const busy = busyPage === page.id;
                  const error = page.token_error || page.last_error;
                  return (
                    <TableRow key={page.id} className={page.is_activated ? undefined : "opacity-50"}>
                      <TableCell>
                        <div className="font-medium">{page.name}</div>
                        <div className="text-xs text-muted-foreground">
                          {page.pancake_page_id}
                          {!page.is_activated && ` · ${t("pancake.pages.inactive")}`}
                        </div>
                      </TableCell>
                      <TableCell>{page.platform}</TableCell>
                      <TableCell>
                        <select
                          aria-label={`${t("pancake.pages.col.channel")}: ${page.name}`}
                          title={unsupported ? t("pancake.pages.unsupported") : undefined}
                          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                          value={page.channel_id ?? ""}
                          disabled={unsupported || busy}
                          onChange={(event) =>
                            patchPage(page, { channel_id: event.target.value ? Number(event.target.value) : null })
                          }
                        >
                          <option value="">{t("pancake.pages.noChannel")}</option>
                          {channels.map((channel) => (
                            <option key={channel.id} value={channel.id}>
                              {channel.name}
                            </option>
                          ))}
                        </select>
                      </TableCell>
                      <TableCell>
                        <Switch
                          aria-label={`${t("pancake.pages.col.enabled")}: ${page.name}`}
                          checked={page.is_enabled}
                          disabled={unsupported || busy || page.channel_id === null}
                          onCheckedChange={(checked) => patchPage(page, { is_enabled: checked })}
                        />
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {page.last_polled_at ? formatDateTime(page.last_polled_at) : "—"}
                      </TableCell>
                      <TableCell className="max-w-[16rem] truncate text-sm text-destructive" title={error}>
                        {error}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
```

- [ ] **Step 4: Chạy test + typecheck**

Run: `docker run --rm -v /home/desi:/home/desi -w /home/desi/desi-order-pancake/frontend node:22-alpine npx vitest run src/features/pancake/__tests__/PancakeSettingsPage.test.tsx`
Expected: 5 passed. (`Badge` có variant `default|secondary|destructive|outline|success|warning|info`; `Switch` truyền `...props` xuống `<input>` nên `aria-label` hoạt động — đã đối chiếu `src/components/ui/badge.tsx`, `switch.tsx`.)
Run typecheck: `... npm run typecheck` → 0 lỗi.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/pancake
git commit -m "feat(pancake-ui): trang Cài đặt → Pancake — token, đồng bộ page, gán kênh, bật/tắt poll"
```

---

### Task 11: FE — trang Lead Pancake

**Files:**
- Modify (thay placeholder): `frontend/src/features/pancake/pages/PancakeLeadsPage.tsx`
- Test: `frontend/src/features/pancake/__tests__/PancakeLeadsPage.test.tsx`

**Interfaces:** Consumes Task 9. `Badge` có variant `success | warning | destructive | secondary` (`src/components/ui/badge.tsx`). `PaginationPages({page, totalPages, onPageChange, disabled})` từ `@/components/ui/pagination`.

- [ ] **Step 1: Viết test**

`frontend/src/features/pancake/__tests__/PancakeLeadsPage.test.tsx`:
```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/pancake", () => ({
  listLeads: vi.fn(),
  listPages: vi.fn(),
  repushLead: vi.fn(),
}));
vi.mock("@/lib/api/channels", () => ({ listChannels: vi.fn(async () => []) }));
vi.mock("sonner", async () => (await import("@/test/utils")).makeToastMock());

import { toast } from "sonner";

import * as api from "@/lib/api/pancake";
import PancakeLeadsPage from "@/features/pancake/pages/PancakeLeadsPage";
import { I18nProvider } from "@/i18n";
import { renderWithClient } from "@/test/utils";

const LEAD = {
  id: 1, page_id: 1, pancake_page_id: "945997011940587", page_name: "DELAX", channel_id: 11, channel_name: "Kênh A",
  conversation_id: "945997011940587_1", conversation_inserted_at: "2026-09-16T09:54:26Z", customer_name: "Tuan Anh Nguyen",
  phone: "0888185886", tags: ["BOT-BNC02"], phone_given_at: "2026-09-16T09:54:27Z", assignee_name: "Nhung HN",
  created_at: "2026-09-16T09:54:21Z", foxia_kind: "messenger", foxia_source_id: 1234, push_status: "ok" as const,
  http_status: 200, error: "", pushed_at: "2026-09-16T09:54:30Z", cohoi_id: 216983,
};
const FAILED = { ...LEAD, id: 2, phone: "0912345678", customer_name: "Khách B", push_status: "failed" as const, cohoi_id: null, http_status: 500, error: "" };

function renderPage() {
  return renderWithClient(
    <I18nProvider>
      <PancakeLeadsPage />
    </I18nProvider>,
  );
}

describe("PancakeLeadsPage", () => {
  beforeEach(() => {
    vi.mocked(api.listLeads).mockResolvedValue({ items: [LEAD, FAILED], total: 2, limit: 50, offset: 0 });
    vi.mocked(api.listPages).mockResolvedValue([]);
  });

  it("dựng đúng 8 cột chủ dự án chỉ định + cột Đẩy Foxia", async () => {
    renderPage();
    expect(await screen.findByText("Tuan Anh Nguyen")).toBeInTheDocument();
    const headerRow = screen.getAllByRole("row")[0];
    expect(within(headerRow).getAllByRole("columnheader").map((th) => th.textContent)).toEqual([
      "Page Pancake",
      "Tên khách hàng",
      "Ngày tạo hội thoại",
      "Số điện thoại",
      "Thẻ Pancake",
      "Ngày tạo",
      "Ngày cho số điện thoại",
      "Nhân viên Pancake",
      "Đẩy Foxia",
    ]);
    expect(screen.getByText("[945997011940587] DELAX")).toBeInTheDocument();
    expect(screen.getByText("BOT-BNC02")).toBeInTheDocument();
    expect(screen.getByText("Nhung HN")).toBeInTheDocument();
    expect(screen.getByText("216983")).toBeInTheDocument();
    expect(screen.getByText("2 lead")).toBeInTheDocument();
  });

  it("chỉ lead thất bại có nút Đẩy lại; bấm gọi repushLead", async () => {
    const user = userEvent.setup();
    vi.mocked(api.repushLead).mockResolvedValue({ status: "queued" });
    renderPage();
    await screen.findByText("Khách B");
    const buttons = screen.getAllByRole("button", { name: "Đẩy lại" });
    expect(buttons).toHaveLength(1);
    await user.click(buttons[0]);
    await waitFor(() => expect(api.repushLead).toHaveBeenCalledWith(2));
    expect(toast.success).toHaveBeenCalledWith("Đã xếp lịch đẩy lại");
  });

  it("đổi bộ lọc trạng thái gọi listLeads với push_status", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Khách B");
    await user.selectOptions(screen.getByLabelText("Trạng thái đẩy"), "failed");
    await waitFor(() =>
      expect(api.listLeads).toHaveBeenLastCalledWith(expect.objectContaining({ push_status: "failed", offset: 0 })),
    );
  });
});
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `docker run --rm -v /home/desi:/home/desi -w /home/desi/desi-order-pancake/frontend node:22-alpine npx vitest run src/features/pancake/__tests__/PancakeLeadsPage.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Viết trang**

`frontend/src/features/pancake/pages/PancakeLeadsPage.tsx`:
```tsx
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { PaginationPages } from "@/components/ui/pagination";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { listChannels } from "@/lib/api/channels";
import { repushLead, type PancakeLead, type PancakeLeadsParams, type PancakePushStatus } from "@/lib/api/pancake";
import { formatDateTime } from "@/lib/utils";
import { pancakeKeys, usePancakeLeads, usePancakePages } from "@/features/pancake/queries";

const PAGE_SIZE = 50;
const STATUSES: PancakePushStatus[] = ["pending", "ok", "failed", "skipped_no_mapping"];
const STATUS_VARIANT: Record<PancakePushStatus, "secondary" | "success" | "destructive" | "warning"> = {
  pending: "secondary",
  ok: "success",
  failed: "destructive",
  skipped_no_mapping: "warning",
};

const fmt = (value: string | null) => (value ? formatDateTime(value) : "—");

/**
 * Sales → Lead Pancake (quyền `pancake-leads:view`). 8 cột theo chỉ định của chủ dự
 * án (2026-09-16) + cột Đẩy Foxia của ta. Phân trang server, lọc đổi là về trang 1.
 */
export default function PancakeLeadsPage() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState({ page: "", channel: "", push_status: "", q: "", date_from: "", date_to: "" });
  const [currentPage, setCurrentPage] = useState(1);
  const [busyLead, setBusyLead] = useState<number | null>(null);

  const params = useMemo<PancakeLeadsParams>(
    () => ({
      page: filters.page ? Number(filters.page) : "",
      channel: filters.channel ? Number(filters.channel) : "",
      push_status: filters.push_status as PancakePushStatus | "",
      q: filters.q.trim(),
      date_from: filters.date_from,
      date_to: filters.date_to,
      limit: PAGE_SIZE,
      offset: (currentPage - 1) * PAGE_SIZE,
    }),
    [filters, currentPage],
  );
  const leadsQuery = usePancakeLeads(params);
  const pagesQuery = usePancakePages();
  const channelsQuery = useQuery({ queryKey: ["channels", "for-pancake-leads"], queryFn: listChannels });

  const data = leadsQuery.data;
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const setFilter = (key: keyof typeof filters, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setCurrentPage(1);
  };

  const handleRepush = async (lead: PancakeLead) => {
    setBusyLead(lead.id);
    try {
      await repushLead(lead.id);
      toast.success(t("pancake.leads.repushQueued"));
      await queryClient.invalidateQueries({ queryKey: pancakeKeys.all });
    } catch (error) {
      toast.error((error as Error)?.message || t("pancake.leads.repushFailed"));
    } finally {
      setBusyLead(null);
    }
  };

  const selectClass = "h-9 rounded-md border border-input bg-background px-3 text-sm";

  return (
    <div className="space-y-4">
      <PageHeader title={t("pancake.leads.title")} description={t("pancake.leads.description")} />

      <Card>
        <CardContent className="flex flex-wrap items-end gap-2 pt-4">
          <select aria-label={t("pancake.leads.filter.page")} className={selectClass} value={filters.page} onChange={(e) => setFilter("page", e.target.value)}>
            <option value="">{`${t("pancake.leads.filter.page")}: ${t("pancake.leads.filter.all")}`}</option>
            {(pagesQuery.data ?? []).map((page) => (
              <option key={page.id} value={page.id}>{page.name}</option>
            ))}
          </select>
          <select aria-label={t("pancake.leads.filter.channel")} className={selectClass} value={filters.channel} onChange={(e) => setFilter("channel", e.target.value)}>
            <option value="">{`${t("pancake.leads.filter.channel")}: ${t("pancake.leads.filter.all")}`}</option>
            {(channelsQuery.data ?? []).map((channel) => (
              <option key={channel.id} value={channel.id}>{channel.name}</option>
            ))}
          </select>
          <select aria-label={t("pancake.leads.filter.status")} className={selectClass} value={filters.push_status} onChange={(e) => setFilter("push_status", e.target.value)}>
            <option value="">{`${t("pancake.leads.filter.status")}: ${t("pancake.leads.filter.all")}`}</option>
            {STATUSES.map((status) => (
              <option key={status} value={status}>{t(`pancake.leads.status.${status}`)}</option>
            ))}
          </select>
          <Input aria-label={t("pancake.leads.filter.q")} placeholder={t("pancake.leads.filter.q")} className="w-48" value={filters.q} onChange={(e) => setFilter("q", e.target.value)} />
          <Input aria-label={t("pancake.leads.filter.from")} type="date" className="w-40" value={filters.date_from} onChange={(e) => setFilter("date_from", e.target.value)} />
          <Input aria-label={t("pancake.leads.filter.to")} type="date" className="w-40" value={filters.date_to} onChange={(e) => setFilter("date_to", e.target.value)} />
          <span className="ml-auto text-sm text-muted-foreground">{t("pancake.leads.total").replace("{total}", String(total))}</span>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-4">
          {data && data.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("pancake.leads.empty")}</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("pancake.leads.col.page")}</TableHead>
                  <TableHead>{t("pancake.leads.col.customer")}</TableHead>
                  <TableHead>{t("pancake.leads.col.convCreated")}</TableHead>
                  <TableHead>{t("pancake.leads.col.phone")}</TableHead>
                  <TableHead>{t("pancake.leads.col.tags")}</TableHead>
                  <TableHead>{t("pancake.leads.col.created")}</TableHead>
                  <TableHead>{t("pancake.leads.col.phoneGiven")}</TableHead>
                  <TableHead>{t("pancake.leads.col.assignee")}</TableHead>
                  <TableHead>{t("pancake.leads.col.push")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(data?.items ?? []).map((lead) => {
                  const canRepush = lead.push_status === "failed" || lead.push_status === "skipped_no_mapping";
                  return (
                    <TableRow key={lead.id}>
                      <TableCell className="max-w-[14rem]">
                        <span className="text-sm">{`[${lead.pancake_page_id}] ${lead.page_name}`}</span>
                        <div className="text-xs text-muted-foreground">{lead.channel_name}</div>
                      </TableCell>
                      <TableCell>{lead.customer_name || "—"}</TableCell>
                      <TableCell className="whitespace-nowrap">{fmt(lead.conversation_inserted_at)}</TableCell>
                      <TableCell className="font-mono">{lead.phone}</TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          {lead.tags.map((tag) => (
                            <Badge key={tag} variant="outline">{tag}</Badge>
                          ))}
                        </div>
                      </TableCell>
                      <TableCell className="whitespace-nowrap">{fmt(lead.created_at)}</TableCell>
                      <TableCell className="whitespace-nowrap">{fmt(lead.phone_given_at)}</TableCell>
                      <TableCell>{lead.assignee_name || "—"}</TableCell>
                      <TableCell>
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge variant={STATUS_VARIANT[lead.push_status]} title={lead.error || undefined}>
                            {t(`pancake.leads.status.${lead.push_status}`)}
                          </Badge>
                          {lead.cohoi_id !== null && <span className="text-xs text-muted-foreground">{String(lead.cohoi_id)}</span>}
                          {canRepush && (
                            <Button size="sm" variant="outline" disabled={busyLead === lead.id} onClick={() => handleRepush(lead)}>
                              {t("pancake.leads.repush")}
                            </Button>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
          {totalPages > 1 && (
            <div className="mt-4 flex justify-end">
              <PaginationPages page={currentPage} totalPages={totalPages} onPageChange={setCurrentPage} disabled={leadsQuery.isFetching} />
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
```

- [ ] **Step 4: Chạy test + typecheck + lint**

Run: `docker run --rm -v /home/desi:/home/desi -w /home/desi/desi-order-pancake/frontend node:22-alpine npx vitest run src/features/pancake`
Expected: 8 passed (5 settings + 3 leads).
Run: `... npm run typecheck` → 0 lỗi. Run: `... npm run lint` → không lỗi mới ở `src/features/pancake`, `src/lib/api/pancake.ts`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/pancake
git commit -m "feat(pancake-ui): trang Lead Pancake — 8 cột chỉ định + trạng thái đẩy Foxia, lọc, phân trang, đẩy lại"
```

---

### Task 12: Tài liệu, kiểm chứng toàn bộ, MR

**Files:**
- Create: `docs/runbooks/2026-09-16-pancake-phone-leads-deploy.md`
- Modify: `docs/superpowers/specs/2026-09-16-pancake-phone-leads-design.md` (ghi chú sai lệch nếu có)

- [ ] **Step 1: Runbook deploy**

`docs/runbooks/2026-09-16-pancake-phone-leads-deploy.md`:
```markdown
# Runbook — Kéo số điện thoại Pancake → Foxia (spec 2026-09-16)

## 1. Deploy code
- Migration: `pancake.0001_initial`, `users.0049_seed_pancake_leads_permission`.
- Env (`.env` prod): `PANCAKE_POLL_ENABLED=0` (giữ tắt ở bước này), `PANCAKE_HTTP_TIMEOUT=15`.
- `docker compose -f docker-compose.prod.yml up -d backend worker-foxia beat` (env nướng vào
  container lúc tạo — `docker restart` KHÔNG đọc lại `.env`).

## 2. Cấu hình (UI, tài khoản superuser)
1. Vai trò: cấp `pancake-leads:view` cho role cần xem trang Sales → Lead Pancake.
2. Cài đặt → Cấu hình liên kết Foxia: điền ô (kênh, Messenger) và (kênh, Zalo) cho
   mọi kênh sẽ gán page. Thiếu ô → lead ghi `skipped_no_mapping`, đẩy lại được sau.
3. Cài đặt → Pancake: dán access token (tài khoản Admin trên các page) → *Lưu & kiểm tra*
   (đồng bộ page ngay). Gán kênh cho từng page, gạt *Bật* — sinh token page lúc này;
   lỗi "Thiếu quyền Admin" / "không có gói cước" hiện ngay ở cột Lỗi.

## 3. Bật cron
- `.env`: `PANCAKE_POLL_ENABLED=1` → `up -d worker-foxia`.
- Kiểm tra: `docker compose logs -f worker-foxia | grep "Pancake poll"` — mỗi 3 phút một
  dòng `Pancake poll ok: page=…`. Metric `desi_pancake_poll_pages_total{status}`.

## 4. Vận hành
- Lead thất bại: trang Lead Pancake → *Đẩy lại* (chỉ failed / chưa cấu hình id).
- Token page bị Pancake từ chối: hệ thống sinh lại 1 lần; vẫn hỏng → page tự TẮT,
  cột Lỗi ghi lý do; sửa quyền bên Pancake rồi gạt Bật lại (cursor đặt lại = now).
- Xem nhật ký thô (response Foxia): Django admin → Pancake → Pancake leads.
- Sau lượt poll đầu có lead: kiểm tra cột "Nhân viên Pancake" có tên hay rỗng. Hình dạng
  `current_assign_users` chưa kiểm chứng bằng token thật (spec §3) — nếu rỗng, lấy một
  hội thoại thật, gọi `GET .../conversations?...` bằng token page (xem
  `apps/pancake/infrastructure/pancake_client.py`) và sửa `_assignee_name` theo đúng khoá.
- Tắt khẩn: `PANCAKE_POLL_ENABLED=0` + `up -d worker-foxia`, hoặc gạt tắt từng page.
```

- [ ] **Step 2: Kiểm chứng backend toàn bộ phần liên quan**

Run: `$PYTEST apps/pancake apps/foxia apps/common/tests/test_celery_routes.py apps/users/tests/test_rbac_sections.py -q --reuse-db`
Expected: pancake + foxia + celery routes passed; lỗi users nếu có phải trùng danh sách lỗi có sẵn trên master (memory `running-backend-tests`).
Run: `/home/desi/desi-order/.venv/bin/lint-imports` (trong `backend/`) → `1 kept, 0 broken`.
Run: `$PY manage.py makemigrations --check --dry-run` → `No changes detected`.
Run: `$PY manage.py spectacular --file /tmp/schema.check.yaml 2>/dev/null && diff -q schema.yaml /tmp/schema.check.yaml` → không lệch.

- [ ] **Step 3: Kiểm chứng frontend**

Run (tuần tự, từng lệnh docker một): `npm run typecheck`; `npx vitest run src/features/pancake src/app/__tests__/modules.test.ts src/i18n/messages.test.ts`; `npm run lint`.
Expected: 0 lỗi typecheck; test passed; lint không lỗi mới.

- [ ] **Step 4: Commit tài liệu + đẩy nhánh, tạo MR**

```bash
git add docs/runbooks/2026-09-16-pancake-phone-leads-deploy.md docs/superpowers
git commit -m "docs(pancake): runbook deploy kéo số Pancake → Foxia"
git push -u origin feat/pancake-phone-leads \
  -o merge_request.create -o merge_request.target=master \
  -o merge_request.title="feat(pancake): kéo số điện thoại từ Pancake, đẩy cơ hội sang Foxia" \
  -o merge_request.description="Spec docs/superpowers/specs/2026-09-16-pancake-phone-leads-design.md. Deploy: migration pancake.0001 + users.0049, env PANCAKE_POLL_ENABLED (mặc định 0), runbook docs/runbooks/2026-09-16-pancake-phone-leads-deploy.md. 🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```
(Tạo MR bằng push option — repo không có `glab`/token, xem memory `gitlab-mr-via-push-options`.)
