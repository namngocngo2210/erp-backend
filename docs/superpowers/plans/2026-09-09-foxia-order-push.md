# Cấu hình liên kết Foxia + đẩy đơn landing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cho phép khai báo bảng n×m (kênh bán × SKU) → `idnguonkhachhang` của Foxia, và tự đẩy khách của đơn landing sang Foxia sau khi đơn commit.

**Architecture:** Ba bảng (cột / hàng / ô) để "thêm–bỏ hàng cột" là thao tác thật và "bỏ cột thì mất id trong cột" là `ON DELETE CASCADE` của Postgres. Đơn landing commit xong → `transaction.on_commit` → `dispatch` sang queue `foxia_push` → container `worker-foxia` mới `POST` một request JSON tới `{base}/{id}`, không retry, ghi đúng một dòng nhật ký cho MỌI nhánh (kể cả bỏ qua). Trang cấu hình là màn superuser-only trong Cài đặt.

**Tech Stack:** Django 5 + DRF (function-based `@api_view`), Celery + Redis, Postgres 16, `apps.common.http_client.HttpClient`, React + TypeScript + TanStack Query + vitest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-09-foxia-order-push-design.md`

## Global Constraints

- Worktree: `/home/desi/desi-order/.claude/worktrees/feat-foxia-push`, nhánh `feat/foxia-order-push`. **Không** `cd` sang repo gốc.
- Base URL mặc định: `https://desiglobal.foxia.vn/duongdankhachhang` — URL thật là `{base}/{idnguonkhachhang}`.
- Payload đẩy đi **đúng ba trường**: `name`, `phone`, `address`. Không gửi trường rỗng nào khác.
- Chỉ đẩy đơn từ `create_order_from_landing`. **Không** đẩy đơn tạo tay / sửa đơn / import Excel / KiotViet.
- Đơn nhiều SKU → lấy `OrderItem` có `id` **nhỏ nhất**.
- Không retry (`max_retries=0`, `HttpClient(retries=0)`). HTTP 2xx = thành công.
- `FOXIA_PUSH_ENABLED` mặc định **tắt** (`"0"`).
- Trang cấu hình + toàn bộ 6 endpoint: **chỉ superuser**.
- Bình luận code viết bằng tiếng Việt, theo đúng văn phong các file lân cận trong `apps/foxia`.
- Không sửa test đang xanh trên master để cho qua.

**Lệnh chạy test backend** (dùng nguyên khối này ở mọi task backend — biến `$PYTEST`):

```bash
cd /home/desi/desi-order/.claude/worktrees/feat-foxia-push/backend
docker start desi-test-pg desi-test-redis 2>/dev/null
export POSTGRES_DB=desi_test_foxiapush POSTGRES_USER=desi POSTGRES_PASSWORD=ci-postgres-pass \
  POSTGRES_HOST=localhost POSTGRES_PORT=5433 \
  REDIS_HOST=localhost REDIS_PORT=6380 REDIS_PASSWORD= \
  APP_ENV=development DJANGO_DEBUG=0 LOG_FORMAT=text \
  ZENIFY_DOMAIN= ZENIFY_API_KEY= ZENIFY_API_SECRET= \
  ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
PYTEST=/home/desi/desi-order/.venv/bin/pytest
```

Lần chạy đầu sau khi thêm migration phải dùng `--create-db`; các lần sau `--reuse-db` cho nhanh.

**Lệnh chạy test frontend** (máy không có `node` trên PATH):

```bash
docker run --rm -v /home/desi:/home/desi \
  -w /home/desi/desi-order/.claude/worktrees/feat-foxia-push/frontend \
  node:22-alpine npx vitest run <đường dẫn test>
```

Typecheck: thay `npx vitest run <path>` bằng `npm run typecheck`. Chạy **một lệnh docker tại một thời điểm** — chạy song song gây timeout giả ở test khác.

## File Structure

**Tạo mới**
| File | Trách nhiệm |
|---|---|
| `backend/apps/foxia/migrations/0004_foxia_link_and_push.py` | 4 bảng mới |
| `backend/apps/foxia/application/services/order_push_service.py` | Dựng payload, tra mapping, gọi HTTP, ghi nhật ký |
| `backend/apps/foxia/transport/link_config_views.py` | 6 endpoint cấu hình bảng (tách khỏi `api_views.py` vốn phục vụ báo cáo) |
| `backend/apps/foxia/tests/test_foxia_order_push.py` | Test luồng đẩy |
| `backend/apps/foxia/tests/test_foxia_link_config_api.py` | Test API cấu hình |
| `frontend/src/lib/api/foxia-link.ts` | Client HTTP thuần |
| `frontend/src/features/foxia-link/queries.ts` | TanStack Query hooks |
| `frontend/src/features/foxia-link/pages/FoxiaLinkSettingsPage.tsx` | Trang bảng n×m |
| `frontend/src/features/foxia-link/__tests__/FoxiaLinkSettingsPage.test.tsx` | Test trang |
| `frontend/src/i18n/messages/foxiaLink.ts` | Chuỗi vi/en của trang |

**Sửa**
| File | Thay đổi |
|---|---|
| `backend/apps/foxia/models.py` | Thêm mục 5: 4 model |
| `backend/apps/foxia/admin.py` | Đăng ký 4 model |
| `backend/apps/foxia/tasks.py` | Thêm `push_order_to_foxia` |
| `backend/apps/foxia/urls.py` | 6 route mới |
| `backend/config/settings_parts/base.py` | 3 setting `FOXIA_PUSH_*` |
| `backend/config/settings_parts/celery.py` | `CELERY_FOXIA_PUSH_QUEUE` + route |
| `backend/apps/dispatch/registry.py` | `"foxia.push_order"` |
| `backend/apps/orders/application/services/order_service_commands.py` | Móc `schedule()` vào luồng landing |
| `backend/apps/users/models.py` | `VALID_SECTIONS` += `"foxia-link"` |
| `backend/apps/common/tests/test_celery_routes.py` | Chốt route `foxia_push` |
| `docker-compose.prod.yml` / `.staging.yml` / `.local.yml` | `worker-foxia` |
| `.env.example` | 5 biến mới |
| `frontend/src/lib/routes.ts` | `AppSection` + `SECTION_PATHS` |
| `frontend/src/app/modules.ts` | Mục menu |
| `frontend/src/app/routes.tsx` | Route `settings/foxia-link` |
| `frontend/src/app/routed-pages.tsx` | `FoxiaLinkRoute` |
| `frontend/src/i18n/messages.ts` + `messages/topnav.ts` | Nhãn menu + gộp file chuỗi mới |

---

### Task 1: Model + migration + admin

**Files:**
- Modify: `backend/apps/foxia/models.py` (thêm vào cuối file)
- Modify: `backend/apps/foxia/admin.py`
- Create: `backend/apps/foxia/migrations/0004_foxia_link_and_push.py` (sinh bằng `makemigrations`)
- Test: `backend/apps/foxia/tests/test_foxia_link_models.py`

**Interfaces:**
- Produces: `FoxiaLinkChannel(channel, sort_order)`, `FoxiaLinkProduct(product, sort_order)`, `FoxiaLinkCell(link_channel, link_product, foxia_source_id, updated_by)`, `FoxiaOrderPush(order, status, foxia_source_id, url, request_body, http_status, response_body, error, duration_ms, created_at)`, `FoxiaPushStatus` (TextChoices: `OK="ok"`, `FAILED="failed"`, `SKIPPED_NO_PRODUCT="skipped_no_product"`, `SKIPPED_NO_MAPPING="skipped_no_mapping"`).

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/foxia/tests/test_foxia_link_models.py`:

```python
"""Bảng n×m liên kết Foxia — ràng buộc và hành vi xoá theo tầng.

Ba bảng thay vì một: cột/hàng phải tồn tại được TRƯỚC khi có ô nào điền, và
"bỏ một cột thì id trong cột đó biến mất" phải là ràng buộc DB chứ không phải
một vòng dọn dẹp trong service có thể quên.
"""

import pytest
from django.db import IntegrityError

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.foxia.models import FoxiaLinkCell, FoxiaLinkChannel, FoxiaLinkProduct

pytestmark = pytest.mark.django_db


def _cell(sku: str, code: str, foxia_source_id: int = 1234) -> FoxiaLinkCell:
    channel = Channel.objects.create(code=code, name=f"Kênh {code}")
    product = Product.objects.create(sku=sku, name=f"SP {sku}")
    return FoxiaLinkCell.objects.create(
        link_channel=FoxiaLinkChannel.objects.create(channel=channel),
        link_product=FoxiaLinkProduct.objects.create(product=product),
        foxia_source_id=foxia_source_id,
    )


def test_bo_cot_xoa_luon_o_cua_cot_do():
    cell = _cell("SKU-A", "kenh-a")
    other = _cell("SKU-B", "kenh-b")

    cell.link_channel.delete()

    assert not FoxiaLinkCell.objects.filter(id=cell.id).exists()
    assert FoxiaLinkCell.objects.filter(id=other.id).exists()


def test_bo_hang_xoa_luon_o_cua_hang_do():
    cell = _cell("SKU-A", "kenh-a")
    other = _cell("SKU-B", "kenh-b")

    cell.link_product.delete()

    assert not FoxiaLinkCell.objects.filter(id=cell.id).exists()
    assert FoxiaLinkCell.objects.filter(id=other.id).exists()


def test_mot_cap_kenh_san_pham_chi_co_mot_o():
    cell = _cell("SKU-A", "kenh-a")

    with pytest.raises(IntegrityError):
        FoxiaLinkCell.objects.create(
            link_channel=cell.link_channel,
            link_product=cell.link_product,
            foxia_source_id=9999,
        )


def test_mot_kenh_chi_la_mot_cot():
    channel = Channel.objects.create(code="kenh-a", name="Kênh A")
    FoxiaLinkChannel.objects.create(channel=channel)

    with pytest.raises(IntegrityError):
        FoxiaLinkChannel.objects.create(channel=channel)
```

- [ ] **Step 2: Chạy test, xác nhận đỏ**

```bash
$PYTEST apps/foxia/tests/test_foxia_link_models.py -q
```
Kỳ vọng: FAIL — `ImportError: cannot import name 'FoxiaLinkCell'`.

- [ ] **Step 3: Thêm model**

Nối vào cuối `backend/apps/foxia/models.py`:

```python
# ──────────────────────────────────────────────────────────────────────────────
# 5. Đẩy đơn sang Foxia — chiều GHI (spec 2026-09-09)
# ──────────────────────────────────────────────────────────────────────────────


class FoxiaLinkChannel(models.Model):
    """Một CỘT của bảng liên kết: một kênh bán nội bộ.

    Tách thành bảng riêng thay vì suy ra từ các ô đã điền: người vận hành phải
    thêm được cột rồi mới điền dần, và cột rỗng là trạng thái hợp lệ.
    """

    channel = models.OneToOneField(
        "channels_core.Channel", on_delete=models.CASCADE, related_name="foxia_link_column"
    )
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "foxia_link_channels"
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"cột:{self.channel_id}"


class FoxiaLinkProduct(models.Model):
    """Một HÀNG của bảng liên kết: một sản phẩm (theo SKU)."""

    product = models.OneToOneField(
        "catalog.Product", on_delete=models.CASCADE, related_name="foxia_link_row"
    )
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "foxia_link_products"
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"hàng:{self.product_id}"


class FoxiaLinkCell(models.Model):
    """Ô (kênh × sản phẩm) → `idnguonkhachhang` của Foxia, điền tay.

    `foxia_source_id` CỐ Ý không FK sang FoxiaSource: người vận hành phải điền
    được id mà mirror danh mục chưa kịp đồng bộ về, và một FK cứng sẽ chặn đúng
    lúc cần nhất. API chỉ validate `> 0`; tên nguồn (nếu tra được) chỉ để đối
    chiếu bằng mắt, không tham gia logic.

    FK sang cột/hàng chứ không sang thẳng Channel/Product: nhờ vậy "bỏ một cột
    thì id trong cột đó biến mất" là ON DELETE CASCADE của Postgres.
    """

    link_channel = models.ForeignKey(
        FoxiaLinkChannel, on_delete=models.CASCADE, related_name="cells"
    )
    link_product = models.ForeignKey(
        FoxiaLinkProduct, on_delete=models.CASCADE, related_name="cells"
    )
    foxia_source_id = models.BigIntegerField()
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "foxia_link_cells"
        constraints = [
            models.UniqueConstraint(
                fields=["link_channel", "link_product"], name="foxia_link_cell_uniq"
            ),
        ]
        indexes = [
            models.Index(fields=["link_product"], name="foxia_link_cell_prod_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.link_channel_id}×{self.link_product_id} → {self.foxia_source_id}"


class FoxiaPushStatus(models.TextChoices):
    OK = "ok", "Thành công"
    FAILED = "failed", "Thất bại"
    SKIPPED_NO_PRODUCT = "skipped_no_product", "Bỏ qua — đơn không có sản phẩm"
    SKIPPED_NO_MAPPING = "skipped_no_mapping", "Bỏ qua — chưa cấu hình id"


class FoxiaOrderPush(models.Model):
    """Nhật ký MỘT lần đẩy đơn sang Foxia.

    Ghi một dòng cho MỌI nhánh, kể cả bỏ qua: phần bị bỏ phải nhìn thấy được,
    chứ không suy ra từ sự vắng mặt của bản ghi.

    `response_body` cắt 2000 ký tự — Foxia chưa chốt hợp đồng phản hồi và có thể
    trả nguyên một trang HTML lỗi; bảng này ghi mỗi đơn một dòng nên không được
    phép phình theo kích thước phản hồi.
    """

    RESPONSE_LIMIT = 2000

    order = models.ForeignKey(
        "orders.Order", on_delete=models.CASCADE, related_name="foxia_pushes"
    )
    status = models.CharField(max_length=32, choices=FoxiaPushStatus.choices)
    foxia_source_id = models.BigIntegerField(null=True, blank=True)
    url = models.CharField(max_length=500, blank=True, default="")
    request_body = models.JSONField(null=True, blank=True)
    http_status = models.IntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True, default="")
    error = models.TextField(blank=True, default="")
    duration_ms = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "foxia_order_pushes"
        indexes = [
            models.Index(fields=["order", "-created_at"], name="foxia_push_order_idx"),
            models.Index(fields=["status", "-created_at"], name="foxia_push_status_idx"),
        ]

    def __str__(self) -> str:
        return f"order:{self.order_id} {self.status}"
```

- [ ] **Step 4: Sinh migration**

```bash
cd /home/desi/desi-order/.claude/worktrees/feat-foxia-push/backend
POSTGRES_DB=desi_test_foxiapush POSTGRES_USER=desi POSTGRES_PASSWORD=ci-postgres-pass \
POSTGRES_HOST=localhost POSTGRES_PORT=5433 REDIS_HOST=localhost REDIS_PORT=6380 REDIS_PASSWORD= \
APP_ENV=development DJANGO_DEBUG=0 LOG_FORMAT=text \
ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= \
/home/desi/desi-order/.venv/bin/python manage.py makemigrations foxia --name foxia_link_and_push
```
Kỳ vọng: tạo `0004_foxia_link_and_push.py` với 4 `CreateModel`.

- [ ] **Step 5: Chạy test, xác nhận xanh**

```bash
$PYTEST apps/foxia/tests/test_foxia_link_models.py -q --create-db
```
Kỳ vọng: 4 passed.

- [ ] **Step 6: Đăng ký admin**

Thêm import và block vào `backend/apps/foxia/admin.py`:

```python
@admin.register(FoxiaOrderPush)
class FoxiaOrderPushAdmin(admin.ModelAdmin):
    """Chỉ ĐỌC. Đây là nhật ký đối soát — sửa tay là phá bằng chứng."""

    list_display = ("order", "status", "foxia_source_id", "http_status", "duration_ms", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("order__id", "order__phone")
    readonly_fields = tuple(f.name for f in FoxiaOrderPush._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(FoxiaLinkChannel)
class FoxiaLinkChannelAdmin(admin.ModelAdmin):
    list_display = ("id", "channel", "sort_order")


@admin.register(FoxiaLinkProduct)
class FoxiaLinkProductAdmin(admin.ModelAdmin):
    list_display = ("id", "product", "sort_order")


@admin.register(FoxiaLinkCell)
class FoxiaLinkCellAdmin(admin.ModelAdmin):
    list_display = ("id", "link_channel", "link_product", "foxia_source_id", "updated_at")
    list_select_related = ("link_channel__channel", "link_product__product")
```

Bổ sung `FoxiaLinkCell, FoxiaLinkChannel, FoxiaLinkProduct, FoxiaOrderPush` vào khối `from apps.foxia.models import (...)` ở đầu file (giữ thứ tự alphabet sẵn có).

- [ ] **Step 7: Chạy lại test foxia, xác nhận không vỡ gì**

```bash
$PYTEST apps/foxia/tests -q --reuse-db
```
Kỳ vọng: toàn bộ PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/apps/foxia/models.py backend/apps/foxia/admin.py \
        backend/apps/foxia/migrations/0004_foxia_link_and_push.py \
        backend/apps/foxia/tests/test_foxia_link_models.py
git commit -m "feat(foxia): bảng liên kết kênh×SKU → id nguồn Foxia + nhật ký đẩy đơn"
```

---

### Task 2: Cấu hình settings, queue và registry

**Files:**
- Modify: `backend/config/settings_parts/base.py:745-749` (ngay dưới khối Foxia sẵn có)
- Modify: `backend/config/settings_parts/celery.py:39` và `:103`
- Modify: `backend/apps/dispatch/registry.py`
- Modify: `backend/apps/foxia/tasks.py`
- Modify: `.env.example`
- Test: `backend/apps/common/tests/test_celery_routes.py`

**Interfaces:**
- Consumes: —
- Produces: `settings.FOXIA_PUSH_ENABLED: bool`, `settings.FOXIA_PUSH_BASE_URL: str`, `settings.FOXIA_PUSH_TIMEOUT: float`, `settings.CELERY_FOXIA_PUSH_QUEUE: str`, task `apps.foxia.tasks.push_order_to_foxia(order_id: int) -> str`, tên dispatch `"foxia.push_order"`.

- [ ] **Step 1: Viết test thất bại**

Nối vào cuối `backend/apps/common/tests/test_celery_routes.py`:

```python
def test_foxia_push_co_queue_rieng_khong_dung_chung_voi_backfill():
    """Đẩy đơn là việc NGẮN — không được xếp sau một lượt backfill 15 phút.

    `apps.foxia.tasks.sync_*` chạy với time_limit=900 trên queue `foxia`. Nếu
    push dùng chung queue đó thì mỗi đơn landing có thể nằm chờ hết một lượt
    đồng bộ danh mục mới được gửi đi.
    """
    name = "apps.foxia.tasks.push_order_to_foxia"
    assert name in settings.CELERY_TASK_ROUTES, f"task {name} chưa khai queue"
    assert settings.CELERY_TASK_ROUTES[name]["queue"] == settings.CELERY_FOXIA_PUSH_QUEUE
    assert settings.CELERY_FOXIA_PUSH_QUEUE != settings.CELERY_FOXIA_QUEUE
    assert settings.CELERY_FOXIA_PUSH_QUEUE != settings.CELERY_TASK_DEFAULT_QUEUE
```

- [ ] **Step 2: Chạy test, xác nhận đỏ**

```bash
$PYTEST apps/common/tests/test_celery_routes.py -q --reuse-db
```
Kỳ vọng: FAIL — `AttributeError`/`AssertionError` vì `CELERY_FOXIA_PUSH_QUEUE` chưa có.

- [ ] **Step 3: Thêm setting**

`backend/config/settings_parts/base.py`, ngay sau dòng `FOXIA_HTTP_TIMEOUT = ...`:

```python
# ── Foxia — chiều GHI: đẩy đơn landing sang Foxia (spec 2026-09-09) ──
# Tắt mặc định: bật chỉ sau khi bảng liên kết kênh×SKU đã điền id thật, nếu
# không thì mỗi đơn landing chỉ tạo thêm một dòng "bỏ qua" vô nghĩa.
FOXIA_PUSH_ENABLED = os.getenv("FOXIA_PUSH_ENABLED", "0") == "1"
# Phần đứng trước `/{idnguonkhachhang}`. Đây là URL công khai, KHÔNG xác thực —
# nên để env chứ không cất trong DB như bộ thông tin JSON-RPC.
FOXIA_PUSH_BASE_URL = os.getenv(
    "FOXIA_PUSH_BASE_URL", "https://desiglobal.foxia.vn/duongdankhachhang"
)
# Ngắn hơn FOXIA_HTTP_TIMEOUT (30s): đây là một POST form nhỏ, không phải
# search_read 500 bản ghi. Task không retry nên timeout dài chỉ làm giữ slot lâu.
FOXIA_PUSH_TIMEOUT = float(os.getenv("FOXIA_PUSH_TIMEOUT", "10"))
```

`backend/config/settings_parts/celery.py`, ngay dưới `CELERY_FOXIA_QUEUE`:

```python
# Queue RIÊNG cho chiều đẩy đơn. Không dùng lại `foxia`: queue đó ôm
# sync_catalog/sync_orders/sync_delivered với time_limit=900 — một đơn landing
# xếp sau lượt backfill 15 phút thì việc "đẩy ngay khi lưu" mất hết ý nghĩa.
CELERY_FOXIA_PUSH_QUEUE = os.getenv("CELERY_FOXIA_PUSH_QUEUE", "foxia_push")
```

Và trong `CELERY_TASK_ROUTES`, ngay sau dòng `"apps.foxia.tasks.sync_delivered"`:

```python
    "apps.foxia.tasks.push_order_to_foxia": {"queue": CELERY_FOXIA_PUSH_QUEUE},
```

- [ ] **Step 4: Thêm task rỗng-nhưng-thật**

Nối vào cuối `backend/apps/foxia/tasks.py`:

```python
@shared_task(
    name="apps.foxia.tasks.push_order_to_foxia",
    soft_time_limit=25,
    time_limit=30,
    max_retries=0,
)
def push_order_to_foxia(order_id: int) -> str:
    """Đẩy khách của MỘT đơn landing sang Foxia. Thử đúng một lần.

    Không override time limit lên cao như các task sync: đây là một request HTTP
    ngắn, trần mặc định 30s của CELERY_TASK_TIME_LIMIT là đủ.

    `max_retries=0` là quyết định nghiệp vụ, không phải thiếu sót: POST này KHÔNG
    idempotent (Foxia chưa cấp khoá chống trùng), retry tự động = khách thứ hai.
    """
    from apps.foxia.application.services.order_push_service import FoxiaOrderPushService

    return FoxiaOrderPushService.push(order_id)
```

- [ ] **Step 5: Tạo stub service (Task 3 sẽ điền nội dung)**

`push_order_to_foxia` import service bên TRONG thân hàm nên test registry không
đỏ vì thiếu file — nhưng task phải để lại một module import được, không phải một
tên treo. Tạo `backend/apps/foxia/application/services/order_push_service.py`:

```python
"""Đẩy đơn landing sang Foxia. Nội dung thật ở Task 3 của kế hoạch."""

from __future__ import annotations


class FoxiaOrderPushService:
    @staticmethod
    def schedule(order) -> None:
        raise NotImplementedError

    @staticmethod
    def push(order_id: int) -> str:
        raise NotImplementedError
```

- [ ] **Step 6: Đăng ký dispatch**

`backend/apps/dispatch/registry.py`, thêm mục mới (đặt sau khối Lark, giữ nhóm theo hệ thống):

```python
    # ── Foxia ──
    "foxia.push_order": _lazy("apps.foxia.tasks", "push_order_to_foxia"),
```

- [ ] **Step 7: Bổ sung `.env.example`**

Thêm vào cuối khối Foxia sẵn có trong `.env.example` (nếu chưa có khối thì đặt cạnh `FOXIA_SYNC_ENABLED`):

```dotenv
# Đẩy đơn landing sang Foxia (spec 2026-09-09). Bật SAU khi bảng liên kết đã điền id.
FOXIA_PUSH_ENABLED=0
FOXIA_PUSH_BASE_URL=https://desiglobal.foxia.vn/duongdankhachhang
FOXIA_PUSH_TIMEOUT=10
CELERY_FOXIA_PUSH_QUEUE=foxia_push
FOXIA_WORKER_CONCURRENCY=2
```

- [ ] **Step 8: Chạy test, xác nhận xanh**

```bash
$PYTEST apps/common/tests/test_celery_routes.py -q --reuse-db
```
Kỳ vọng: toàn bộ PASS — bao gồm `test_routed_and_beat_tasks_are_actually_registered`, tức `push_order_to_foxia` phải import được thật.

- [ ] **Step 9: Commit**

```bash
git add backend/config/settings_parts/base.py backend/config/settings_parts/celery.py \
        backend/apps/dispatch/registry.py backend/apps/foxia/tasks.py \
        backend/apps/common/tests/test_celery_routes.py .env.example \
        backend/apps/foxia/application/services/order_push_service.py
git commit -m "feat(foxia): queue foxia_push + task đẩy đơn + cấu hình env"
```

---

### Task 3: Service đẩy đơn

**Files:**
- Modify: `backend/apps/foxia/application/services/order_push_service.py` (điền nội dung)
- Test: `backend/apps/foxia/tests/test_foxia_order_push.py`

**Interfaces:**
- Consumes: `FoxiaLinkCell`, `FoxiaOrderPush`, `FoxiaPushStatus` (Task 1); `settings.FOXIA_PUSH_*`, `settings.CELERY_FOXIA_PUSH_QUEUE` (Task 2).
- Produces: `FoxiaOrderPushService.schedule(order) -> None`, `FoxiaOrderPushService.push(order_id: int) -> str`, `build_address(order) -> str`, `build_payload(order) -> dict`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/foxia/tests/test_foxia_order_push.py`:

```python
"""Luồng đẩy đơn landing sang Foxia.

Ba điều được chốt ở đây, vì sai cái nào cũng hỏng im lặng:
1. Chọn id theo dòng hàng ĐẦU TIÊN — quy tắc do chủ hệ thống chốt.
2. Mọi nhánh đều ghi đúng MỘT dòng nhật ký, kể cả nhánh bỏ qua.
3. Lỗi phía Foxia KHÔNG được raise ra khỏi task.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.test import override_settings

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.foxia.application.services.order_push_service import (
    FoxiaOrderPushService,
    build_address,
    build_payload,
)
from apps.foxia.models import (
    FoxiaLinkCell,
    FoxiaLinkChannel,
    FoxiaLinkProduct,
    FoxiaOrderPush,
    FoxiaPushStatus,
)
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db

PUSH_ON = override_settings(
    FOXIA_PUSH_ENABLED=True,
    FOXIA_PUSH_BASE_URL="https://foxia.test/duongdankhachhang",
    FOXIA_PUSH_TIMEOUT=10.0,
)


def _channel(code="kenh-a"):
    return Channel.objects.create(code=code, name=f"Kênh {code}")


def _product(sku):
    return Product.objects.create(sku=sku, name=f"SP {sku}")


def _order(channel, **overrides):
    values = {
        "channel": channel,
        "name": "Nguyễn Văn A",
        "phone": "0337293367",
        "address": "9b Lê Quý Đôn",
        "ward": "Phường Bạch Đằng",
        "district": "Hai Bà Trưng",
        "province": "Hà Nội",
    }
    values.update(overrides)
    return Order.objects.create(**values)


def _item(order, product):
    return OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        sku=product.sku,
        quantity=1,
        unit_price=Decimal("100000"),
        line_total=Decimal("100000"),
    )


def _map(channel, product, foxia_source_id):
    return FoxiaLinkCell.objects.create(
        link_channel=FoxiaLinkChannel.objects.get_or_create(channel=channel)[0],
        link_product=FoxiaLinkProduct.objects.get_or_create(product=product)[0],
        foxia_source_id=foxia_source_id,
    )


def _response(status_code=200, text="OK"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


@PUSH_ON
def test_day_don_mot_sku_goi_dung_url_va_payload():
    channel = _channel()
    product = _product("SKU-A")
    order = _order(channel)
    _item(order, product)
    _map(channel, product, 1234)

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        client_cls.return_value.post.return_value = _response()
        assert FoxiaOrderPushService.push(order.id) == FoxiaPushStatus.OK

    call = client_cls.return_value.post.call_args
    assert call.args[0] == "https://foxia.test/duongdankhachhang/1234"
    assert call.kwargs["json"] == {
        "name": "Nguyễn Văn A",
        "phone": "0337293367",
        "address": "9b Lê Quý Đôn, Phường Bạch Đằng, Hai Bà Trưng, Hà Nội",
    }
    log = FoxiaOrderPush.objects.get(order=order)
    assert (log.status, log.http_status, log.foxia_source_id) == (FoxiaPushStatus.OK, 200, 1234)


@PUSH_ON
def test_don_nhieu_sku_lay_id_cua_dong_dau_tien():
    channel = _channel()
    first, second = _product("SKU-A"), _product("SKU-B")
    order = _order(channel)
    _item(order, first)
    _item(order, second)
    _map(channel, first, 1111)
    _map(channel, second, 2222)

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        client_cls.return_value.post.return_value = _response()
        FoxiaOrderPushService.push(order.id)

    assert client_cls.return_value.post.call_args.args[0].endswith("/1111")


@PUSH_ON
def test_thieu_mapping_thi_khong_goi_va_ghi_skipped():
    channel = _channel()
    order = _order(channel)
    _item(order, _product("SKU-A"))

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        assert FoxiaOrderPushService.push(order.id) == FoxiaPushStatus.SKIPPED_NO_MAPPING
        client_cls.return_value.post.assert_not_called()

    log = FoxiaOrderPush.objects.get(order=order)
    assert log.status == FoxiaPushStatus.SKIPPED_NO_MAPPING
    assert log.foxia_source_id is None


@PUSH_ON
def test_don_khong_co_dong_hang_thi_ghi_skipped_no_product():
    order = _order(_channel())

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        assert FoxiaOrderPushService.push(order.id) == FoxiaPushStatus.SKIPPED_NO_PRODUCT
        client_cls.return_value.post.assert_not_called()

    assert FoxiaOrderPush.objects.get(order=order).status == FoxiaPushStatus.SKIPPED_NO_PRODUCT


@PUSH_ON
def test_foxia_tra_500_thi_ghi_failed_va_khong_raise():
    channel = _channel()
    product = _product("SKU-A")
    order = _order(channel)
    _item(order, product)
    _map(channel, product, 1234)

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        client_cls.return_value.post.return_value = _response(500, "Internal Server Error")
        assert FoxiaOrderPushService.push(order.id) == FoxiaPushStatus.FAILED

    log = FoxiaOrderPush.objects.get(order=order)
    assert (log.status, log.http_status) == (FoxiaPushStatus.FAILED, 500)
    assert "Internal Server Error" in log.response_body


@PUSH_ON
def test_timeout_thi_ghi_failed_kem_error_va_khong_raise():
    channel = _channel()
    product = _product("SKU-A")
    order = _order(channel)
    _item(order, product)
    _map(channel, product, 1234)

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        client_cls.return_value.post.side_effect = requests.ReadTimeout("hết giờ")
        assert FoxiaOrderPushService.push(order.id) == FoxiaPushStatus.FAILED

    log = FoxiaOrderPush.objects.get(order=order)
    assert log.status == FoxiaPushStatus.FAILED
    assert log.http_status is None
    assert "ReadTimeout" in log.error


@override_settings(FOXIA_PUSH_ENABLED=False)
def test_tat_co_thi_khong_goi_va_khong_ghi_dong_nao():
    channel = _channel()
    product = _product("SKU-A")
    order = _order(channel)
    _item(order, product)
    _map(channel, product, 1234)

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        assert FoxiaOrderPushService.push(order.id) == "disabled"
        client_cls.return_value.post.assert_not_called()

    assert not FoxiaOrderPush.objects.filter(order=order).exists()


def test_ghep_dia_chi_bo_phan_rong():
    order = Order(address="9b Lê Quý Đôn", ward="", district="Hai Bà Trưng", province="Hà Nội")
    assert build_address(order) == "9b Lê Quý Đôn, Hai Bà Trưng, Hà Nội"


def test_ghep_dia_chi_rong_het_thi_dung_raw_address():
    order = Order(address="", ward="", district="", province="", raw_address="  ghi chú địa chỉ ")
    assert build_address(order) == "ghi chú địa chỉ"


def test_payload_chi_gom_ba_truong():
    order = Order(name=" A ", phone=" 0337293367 ", address="9b Lê Quý Đôn")
    assert set(build_payload(order)) == {"name", "phone", "address"}
    assert build_payload(order)["name"] == "A"


@override_settings(FOXIA_PUSH_ENABLED=False)
def test_schedule_khong_enqueue_khi_tat_co():
    order = _order(_channel())
    with patch("apps.dispatch.api.dispatch") as dispatch_fn:
        FoxiaOrderPushService.schedule(order)
    dispatch_fn.assert_not_called()
```

- [ ] **Step 2: Chạy test, xác nhận đỏ**

```bash
$PYTEST apps/foxia/tests/test_foxia_order_push.py -q --reuse-db
```
Kỳ vọng: FAIL — `ImportError: cannot import name 'build_address'`.

- [ ] **Step 3: Viết service**

Ghi đè `backend/apps/foxia/application/services/order_push_service.py`:

```python
"""Đẩy khách của đơn landing sang Foxia — chiều GHI duy nhất của tích hợp.

Foxia nhận một POST JSON tại `{base}/{idnguonkhachhang}`, KHÔNG xác thực. `id`
quyết định đơn được ghi vào nguồn khách hàng nào, và nó tra từ bảng liên kết
(kênh × SKU) do người vận hành khai báo — xem spec 2026-09-09.

Ba ràng buộc bao trùm mọi thứ ở đây:
- **Không retry.** POST này không idempotent (Foxia chưa cấp khoá chống trùng),
  gửi lại = khách thứ hai. HttpClient dựng với `retries=0`.
- **Không raise.** Lỗi của đối tác không được làm bẩn hàng đợi hay sinh cảnh báo
  Celery; bằng chứng nằm ở dòng FoxiaOrderPush, không ở stack trace.
- **Luôn ghi một dòng.** Kể cả nhánh bỏ qua — phần bị bỏ phải nhìn thấy được.
"""

from __future__ import annotations

import logging
import time

from django.conf import settings
from django.db import transaction

from apps.common.http_client import HttpClient

logger = logging.getLogger(__name__)

_ADDRESS_PARTS = ("address", "ward", "district", "province")


def build_address(order) -> str:
    """Ghép địa chỉ giao hàng thành một chuỗi cho Foxia.

    Rơi về `raw_address` khi bốn phần đã tách đều rỗng: đơn landing chưa được
    sale chuẩn hoá địa chỉ vẫn phải đẩy được — bên nhận cần biết khách tồn tại.
    """
    parts = [str(getattr(order, name, "") or "").strip() for name in _ADDRESS_PARTS]
    joined = ", ".join(part for part in parts if part)
    return joined or str(getattr(order, "raw_address", "") or "").strip()


def build_payload(order) -> dict:
    """Đúng ba trường Foxia cần. Không gửi trường rỗng nào khác.

    Foxia nhận thêm message/facebook/email/utm_*/link, nhưng ta không có dữ liệu
    thật cho chúng — gửi chuỗi rỗng và không gửi là hai ý nghĩa khác nhau với
    bên nhận.
    """
    return {
        "name": str(order.name or "").strip(),
        "phone": str(order.phone or "").strip(),
        "address": build_address(order),
    }


class FoxiaOrderPushService:
    @staticmethod
    def schedule(order) -> None:
        """Xếp lịch đẩy SAU commit. Gọi từ trong khối atomic của luồng tạo đơn."""
        if not getattr(settings, "FOXIA_PUSH_ENABLED", False):
            return

        order_id = order.id

        def _enqueue() -> None:
            # Enqueue hỏng (Redis chớp) không được làm hỏng đơn đã lưu — đơn là
            # thật, việc đẩy là phụ trợ.
            try:
                from apps.dispatch import dispatch

                dispatch(
                    "foxia.push_order",
                    queue=settings.CELERY_FOXIA_PUSH_QUEUE,
                    order_id=order_id,
                )
            except Exception:  # noqa: BLE001
                logger.exception("Foxia push: enqueue thất bại (order_id=%s)", order_id)

        transaction.on_commit(_enqueue)

    @staticmethod
    def push(order_id: int) -> str:
        """Thân của task. Trả về giá trị `FoxiaPushStatus`, hoặc `disabled`/`missing`."""
        from apps.foxia.models import FoxiaLinkCell, FoxiaOrderPush, FoxiaPushStatus
        from apps.orders.models import Order

        # Task còn kẹt trong hàng đợi lúc tắt cờ KHÔNG được đẻ rác nhật ký.
        if not getattr(settings, "FOXIA_PUSH_ENABLED", False):
            return "disabled"

        order = Order.objects.filter(id=order_id).first()
        if order is None:
            logger.warning("Foxia push: không tìm thấy đơn %s", order_id)
            return "missing"

        # Dòng hàng ĐẦU TIÊN — quy tắc chốt của chủ hệ thống cho đơn nhiều SKU.
        item = order.items.order_by("id").first()
        if item is None:
            FoxiaOrderPush.objects.create(order=order, status=FoxiaPushStatus.SKIPPED_NO_PRODUCT)
            return FoxiaPushStatus.SKIPPED_NO_PRODUCT

        source_id = (
            FoxiaLinkCell.objects.filter(
                link_channel__channel_id=order.channel_id,
                link_product__product_id=item.product_id,
            )
            .values_list("foxia_source_id", flat=True)
            .first()
        )
        if source_id is None:
            FoxiaOrderPush.objects.create(order=order, status=FoxiaPushStatus.SKIPPED_NO_MAPPING)
            return FoxiaPushStatus.SKIPPED_NO_MAPPING

        base = str(getattr(settings, "FOXIA_PUSH_BASE_URL", "") or "").rstrip("/")
        url = f"{base}/{source_id}"
        payload = build_payload(order)
        limit = FoxiaOrderPush.RESPONSE_LIMIT

        started = time.monotonic()
        http_status = None
        response_body = ""
        error = ""
        try:
            client = HttpClient(
                timeout=float(getattr(settings, "FOXIA_PUSH_TIMEOUT", 10.0)),
                retries=0,
                default_headers={"Content-Type": "application/json"},
            )
            response = client.post(url, json=payload)
            http_status = response.status_code
            response_body = str(response.text or "")[:limit]
        except Exception as exc:  # noqa: BLE001 — lỗi đối tác không được thoát ra task
            error = f"{type(exc).__name__}: {exc}"[:limit]

        ok = http_status is not None and 200 <= http_status < 300
        status = FoxiaPushStatus.OK if ok else FoxiaPushStatus.FAILED
        FoxiaOrderPush.objects.create(
            order=order,
            status=status,
            foxia_source_id=source_id,
            url=url,
            request_body=payload,
            http_status=http_status,
            response_body=response_body,
            error=error,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        if not ok:
            logger.warning(
                "Foxia push thất bại: order_id=%s url=%s http=%s error=%s",
                order_id, url, http_status, error,
            )
        return status
```

- [ ] **Step 4: Chạy test, xác nhận xanh**

```bash
$PYTEST apps/foxia/tests/test_foxia_order_push.py -q --reuse-db
```
Kỳ vọng: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/foxia/application/services/order_push_service.py \
        backend/apps/foxia/tests/test_foxia_order_push.py
git commit -m "feat(foxia): service đẩy đơn — tra mapping, POST một lần, ghi nhật ký mọi nhánh"
```

---

### Task 4: Móc vào luồng tạo đơn landing

**Files:**
- Modify: `backend/apps/orders/application/services/order_service_commands.py` (trong `create_order_from_landing`, ngay sau dòng `OrderServiceCommands._schedule_vat_invoice_notification(order)`)
- Test: `backend/apps/foxia/tests/test_foxia_order_push.py` (nối thêm)

**Interfaces:**
- Consumes: `FoxiaOrderPushService.schedule(order)` (Task 3).
- Produces: — (điểm móc, không có API mới)

- [ ] **Step 1: Viết test thất bại**

Nối vào cuối `backend/apps/foxia/tests/test_foxia_order_push.py`:

```python
@PUSH_ON
def test_tao_don_landing_co_xep_lich_day():
    """Đơn landing lưu xong PHẢI xếp lịch đẩy — và chỉ sau khi commit."""
    from apps.orders.application.services.order_service_commands import OrderServiceCommands

    channel = _channel("kenh-landing")
    product = _product("SKU-LAND")

    with patch(
        "apps.foxia.application.services.order_push_service.FoxiaOrderPushService.schedule"
    ) as schedule:
        order = OrderServiceCommands.create_order_from_landing(
            {
                "channel_id": channel.id,
                "name": "Nguyễn Văn A",
                "phone": "0337293367",
                "raw_address": "9b Lê Quý Đôn, Hai Bà Trưng, Hà Nội",
                "product_id": product.sku,
            }
        )

    schedule.assert_called_once()
    assert schedule.call_args.args[0].id == order.id


@PUSH_ON
def test_tao_don_thu_cong_KHONG_xep_lich_day():
    """Chỉ đơn landing được đẩy — chốt phạm vi của chủ hệ thống, không phải sót."""
    import inspect

    from apps.orders.application.services import order_service_commands

    manual = inspect.getsource(order_service_commands.OrderServiceCommands.create_order_manual)
    assert "FoxiaOrderPushService" not in manual
```

- [ ] **Step 2: Chạy test, xác nhận đỏ**

```bash
$PYTEST apps/foxia/tests/test_foxia_order_push.py -q --reuse-db \
  -k "xep_lich_day or KHONG_xep_lich"
```
Kỳ vọng: `test_tao_don_landing_co_xep_lich_day` FAIL (`schedule` không được gọi).

- [ ] **Step 3: Thêm điểm móc**

Trong `create_order_from_landing`, ngay sau `OrderServiceCommands._schedule_vat_invoice_notification(order)` và TRƯỚC khối `try: ... LeadFilterService.register_claim`:

```python
        # Đẩy khách sang Foxia (spec 2026-09-09). Chỉ luồng landing — đơn tạo tay,
        # sửa đơn, import Excel, KiotViet CỐ Ý không đẩy. `schedule` tự no-op khi
        # FOXIA_PUSH_ENABLED tắt và chỉ enqueue sau commit ngoài cùng.
        try:
            from apps.foxia.application.services.order_push_service import FoxiaOrderPushService

            FoxiaOrderPushService.schedule(order)
        except Exception:  # noqa: BLE001 — tích hợp đối tác không được phá ingest
            logger.exception("Foxia push: xếp lịch thất bại (order_id=%s)", order.id)
```

- [ ] **Step 4: Chạy test, xác nhận xanh**

```bash
$PYTEST apps/foxia/tests/test_foxia_order_push.py -q --reuse-db
```
Kỳ vọng: 13 passed.

- [ ] **Step 5: Chạy test luồng tạo đơn để chắc không vỡ**

```bash
$PYTEST apps/orders/tests -q --reuse-db -k "landing"
```
Kỳ vọng: PASS (không phát sinh lỗi mới so với master).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/application/services/order_service_commands.py \
        backend/apps/foxia/tests/test_foxia_order_push.py
git commit -m "feat(orders): xếp lịch đẩy Foxia sau khi đơn landing commit"
```

---

### Task 5: API cấu hình bảng liên kết

**Files:**
- Create: `backend/apps/foxia/transport/link_config_views.py`
- Modify: `backend/apps/foxia/urls.py`
- Test: `backend/apps/foxia/tests/test_foxia_link_config_api.py`

**Interfaces:**
- Consumes: `FoxiaLinkChannel`, `FoxiaLinkProduct`, `FoxiaLinkCell` (Task 1).
- Produces: 6 endpoint dưới tiền tố `/api/foxia/`:
  `GET link-config`, `POST link-config/channels`, `DELETE link-config/channels/<pk>`,
  `POST link-config/products`, `DELETE link-config/products/<pk>`, `PUT link-config/cells`.
  Hình dạng JSON dùng cho FE (Task 7-8) mô tả trong Step 3.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/foxia/tests/test_foxia_link_config_api.py`:

```python
"""API trang Cấu hình liên kết Foxia. Toàn bộ CHỈ superuser.

Bảng này quyết định đơn của ai chảy vào nguồn nào bên đối tác — sai một ô là
gán nhầm doanh số marketing, nên đường ghi không mở cho role thường.
"""

import pytest
from rest_framework.test import APIClient

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.foxia.models import FoxiaLinkCell, FoxiaLinkChannel, FoxiaLinkProduct
from apps.users.test_helpers import make_admin, make_user_with_sections

pytestmark = pytest.mark.django_db

BASE = "/api/foxia/link-config"


# ⚠️ `resolve_effective_permissions().is_superuser` chỉ bật khi user có role
# `is_system` (apps/users/rbac.py) — nó KHÔNG đọc cờ `is_superuser` của Django.
# `User.objects.create_superuser(...)` ở đây sẽ nhận 403. Dùng helper chuẩn của repo.
@pytest.fixture
def su_client():
    client = APIClient()
    client.force_authenticate(user=make_admin("foxia_link_admin"))
    return client


@pytest.fixture
def normal_client():
    client = APIClient()
    client.force_authenticate(user=make_user_with_sections("sale", ["orders"]))
    return client


def _grid():
    channel = Channel.objects.create(code="kenh-a", name="Kênh A")
    product = Product.objects.create(sku="SKU-A", name="SP A")
    col = FoxiaLinkChannel.objects.create(channel=channel)
    row = FoxiaLinkProduct.objects.create(product=product)
    cell = FoxiaLinkCell.objects.create(link_channel=col, link_product=row, foxia_source_id=1234)
    return channel, product, col, row, cell


def test_get_tra_ve_ba_nhanh(su_client):
    channel, product, col, row, _ = _grid()

    data = su_client.get(BASE).json()

    assert data["channels"] == [
        {"id": col.id, "channel_id": channel.id, "code": "kenh-a", "name": "Kênh A", "sort_order": 0}
    ]
    assert data["products"] == [
        {"id": row.id, "product_id": product.id, "sku": "SKU-A", "name": "SP A", "sort_order": 0}
    ]
    assert data["cells"] == [
        {
            "link_channel_id": col.id,
            "link_product_id": row.id,
            "foxia_source_id": 1234,
            "source_name": "",
        }
    ]


def test_them_kenh_trung_la_idempotent(su_client):
    channel = Channel.objects.create(code="kenh-a", name="Kênh A")

    su_client.post(f"{BASE}/channels", {"channel_ids": [channel.id]}, format="json")
    su_client.post(f"{BASE}/channels", {"channel_ids": [channel.id]}, format="json")

    assert FoxiaLinkChannel.objects.filter(channel=channel).count() == 1


def test_xoa_cot_xoa_o_cua_cot_do_va_tra_dung_so_luong(su_client):
    _, _, col, _, cell = _grid()
    other_channel = Channel.objects.create(code="kenh-b", name="Kênh B")
    other_col = FoxiaLinkChannel.objects.create(channel=other_channel)
    other_cell = FoxiaLinkCell.objects.create(
        link_channel=other_col, link_product=cell.link_product, foxia_source_id=5678
    )

    resp = su_client.delete(f"{BASE}/channels/{col.id}")

    assert resp.json() == {"deleted_cells": 1}
    assert not FoxiaLinkCell.objects.filter(id=cell.id).exists()
    assert FoxiaLinkCell.objects.filter(id=other_cell.id).exists()


def test_put_cells_upsert_va_xoa_trong_mot_lo(su_client):
    _, _, col, row, cell = _grid()
    product_b = Product.objects.create(sku="SKU-B", name="SP B")
    row_b = FoxiaLinkProduct.objects.create(product=product_b)

    resp = su_client.put(
        f"{BASE}/cells",
        {
            "cells": [
                {"link_channel_id": col.id, "link_product_id": row.id, "foxia_source_id": None},
                {"link_channel_id": col.id, "link_product_id": row_b.id, "foxia_source_id": 4321},
            ]
        },
        format="json",
    )

    assert resp.status_code == 200
    assert not FoxiaLinkCell.objects.filter(id=cell.id).exists()
    assert FoxiaLinkCell.objects.get(link_product=row_b).foxia_source_id == 4321


def test_put_cells_mot_o_sai_thi_ca_lo_khong_ghi(su_client):
    _, _, col, row, cell = _grid()
    product_b = Product.objects.create(sku="SKU-B", name="SP B")
    row_b = FoxiaLinkProduct.objects.create(product=product_b)

    resp = su_client.put(
        f"{BASE}/cells",
        {
            "cells": [
                {"link_channel_id": col.id, "link_product_id": row_b.id, "foxia_source_id": 4321},
                {"link_channel_id": col.id, "link_product_id": row.id, "foxia_source_id": 0},
            ]
        },
        format="json",
    )

    assert resp.status_code == 400
    assert not FoxiaLinkCell.objects.filter(link_product=row_b).exists()
    assert FoxiaLinkCell.objects.get(id=cell.id).foxia_source_id == 1234


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("get", "", None),
        ("post", "/channels", {"channel_ids": []}),
        ("delete", "/channels/1", None),
        ("post", "/products", {"product_ids": []}),
        ("delete", "/products/1", None),
        ("put", "/cells", {"cells": []}),
    ],
)
def test_nguoi_dung_thuong_bi_chan(normal_client, method, path, payload):
    call = getattr(normal_client, method)
    resp = call(f"{BASE}{path}", payload, format="json") if payload is not None else call(f"{BASE}{path}")
    assert resp.status_code == 403
```

- [ ] **Step 2: Chạy test, xác nhận đỏ**

```bash
$PYTEST apps/foxia/tests/test_foxia_link_config_api.py -q --reuse-db
```
Kỳ vọng: FAIL — 404 ở mọi endpoint.

- [ ] **Step 3: Viết view**

Tạo `backend/apps/foxia/transport/link_config_views.py`:

```python
"""API trang "Cấu hình liên kết Foxia" — bảng n×m (kênh × SKU) → idnguonkhachhang.

Tách khỏi `api_views.py`: file đó phục vụ BÁO CÁO (chiều đọc), còn đây là cấu
hình của chiều GHI. Trộn hai việc vào một file là trộn hai vòng đời thay đổi.

Toàn bộ endnpoint chỉ superuser: bảng này quyết định đơn của ai chảy vào nguồn
nào bên đối tác, sai một ô là gán nhầm doanh số marketing của cả một kênh.
"""

from __future__ import annotations

from functools import wraps

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db import transaction

from apps.foxia.models import FoxiaLinkCell, FoxiaLinkChannel, FoxiaLinkProduct, FoxiaSource
from apps.users.rbac import resolve_effective_permissions


def superuser_only(view):
    """Chặn TRƯỚC khi chạm DB. Không dùng section RBAC — đây là quyền quản trị."""

    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not resolve_effective_permissions(request.user).is_superuser:
            return Response({"detail": "Chỉ quản trị viên."}, status=status.HTTP_403_FORBIDDEN)
        return view(request, *args, **kwargs)

    return _wrapped


def _serialize_grid() -> dict:
    columns = list(FoxiaLinkChannel.objects.select_related("channel"))
    rows = list(FoxiaLinkProduct.objects.select_related("product"))
    cells = list(FoxiaLinkCell.objects.all())

    # Tên nguồn chỉ để người dùng tự kiểm tra mình gõ đúng id — tra một lượt,
    # không tham gia vào logic nào, thiếu thì để rỗng.
    names = dict(
        FoxiaSource.objects.filter(
            foxia_id__in=[c.foxia_source_id for c in cells]
        ).values_list("foxia_id", "name")
    )
    return {
        "channels": [
            {
                "id": col.id,
                "channel_id": col.channel_id,
                "code": col.channel.code,
                "name": col.channel.name,
                "sort_order": col.sort_order,
            }
            for col in columns
        ],
        "products": [
            {
                "id": row.id,
                "product_id": row.product_id,
                "sku": row.product.sku,
                "name": row.product.name,
                "sort_order": row.sort_order,
            }
            for row in rows
        ],
        "cells": [
            {
                "link_channel_id": cell.link_channel_id,
                "link_product_id": cell.link_product_id,
                "foxia_source_id": cell.foxia_source_id,
                "source_name": names.get(cell.foxia_source_id, ""),
            }
            for cell in cells
        ],
    }


def _add_axis(model, fk_name: str, raw_ids) -> Response:
    """Thêm cột/hàng, bỏ qua id đã có mặt (idempotent) và id không phải số."""
    try:
        ids = [int(value) for value in (raw_ids or [])]
    except (TypeError, ValueError):
        return Response({"detail": "Danh sách id không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)

    existing = set(model.objects.filter(**{f"{fk_name}__in": ids}).values_list(fk_name, flat=True))
    next_order = (model.objects.count() or 0)
    created = []
    for value in ids:
        if value in existing:
            continue
        created.append(model(**{fk_name: value}, sort_order=next_order))
        next_order += 1
    model.objects.bulk_create(created)
    return Response(_serialize_grid())


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@superuser_only
def link_config(request):
    return Response(_serialize_grid())


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@superuser_only
def add_channels(request):
    return _add_axis(FoxiaLinkChannel, "channel_id", request.data.get("channel_ids"))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@superuser_only
def add_products(request):
    return _add_axis(FoxiaLinkProduct, "product_id", request.data.get("product_ids"))


def _delete_axis(model, pk: int, cell_field: str) -> Response:
    obj = model.objects.filter(pk=pk).first()
    if obj is None:
        return Response({"detail": "Không tìm thấy."}, status=status.HTTP_404_NOT_FOUND)
    deleted = FoxiaLinkCell.objects.filter(**{cell_field: obj.id}).count()
    obj.delete()  # ON DELETE CASCADE dọn các ô của cột/hàng này
    return Response({"deleted_cells": deleted})


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
@superuser_only
def delete_channel(request, pk: int):
    return _delete_axis(FoxiaLinkChannel, pk, "link_channel_id")


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
@superuser_only
def delete_product(request, pk: int):
    return _delete_axis(FoxiaLinkProduct, pk, "link_product_id")


@api_view(["PUT"])
@permission_classes([IsAuthenticated])
@superuser_only
def save_cells(request):
    """Lưu HÀNG LOẠT các ô đã sửa. `foxia_source_id: null` = xoá ô.

    Validate TOÀN BỘ trước khi ghi bất cứ gì: bảng cấu hình lưu nửa vời nguy
    hiểm hơn là không lưu — người dùng sẽ tin những ô còn lại cũng đã lưu.
    """
    raw = request.data.get("cells")
    if not isinstance(raw, list):
        return Response({"detail": "Thiếu danh sách `cells`."}, status=status.HTTP_400_BAD_REQUEST)

    column_ids = set(FoxiaLinkChannel.objects.values_list("id", flat=True))
    row_ids = set(FoxiaLinkProduct.objects.values_list("id", flat=True))

    upserts: list[tuple[int, int, int]] = []
    deletes: list[tuple[int, int]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            return Response({"detail": f"Ô thứ {index + 1} không hợp lệ."}, status=400)
        col = entry.get("link_channel_id")
        row = entry.get("link_product_id")
        if col not in column_ids or row not in row_ids:
            return Response(
                {"detail": f"Ô thứ {index + 1}: kênh hoặc sản phẩm không còn trong bảng."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        value = entry.get("foxia_source_id")
        if value is None or value == "":
            deletes.append((col, row))
            continue
        try:
            value = int(value)
        except (TypeError, ValueError):
            return Response({"detail": f"Ô thứ {index + 1}: id phải là số."}, status=400)
        if value <= 0:
            return Response({"detail": f"Ô thứ {index + 1}: id phải lớn hơn 0."}, status=400)
        upserts.append((col, row, value))

    with transaction.atomic():
        for col, row in deletes:
            FoxiaLinkCell.objects.filter(link_channel_id=col, link_product_id=row).delete()
        for col, row, value in upserts:
            FoxiaLinkCell.objects.update_or_create(
                link_channel_id=col,
                link_product_id=row,
                defaults={"foxia_source_id": value, "updated_by": request.user},
            )
    return Response(_serialize_grid())
```

- [ ] **Step 4: Đăng ký route**

Thay `backend/apps/foxia/urls.py` bằng:

```python
from django.urls import path

from apps.foxia.transport.api_views import (
    list_channel_maps,
    marketing_report,
    sync_status,
    upsert_channel_map,
)
from apps.foxia.transport.link_config_views import (
    add_channels,
    add_products,
    delete_channel,
    delete_product,
    link_config,
    save_cells,
)

urlpatterns = [
    path("reports/marketing", marketing_report, name="foxia-marketing-report"),
    path("channel-maps", list_channel_maps, name="foxia-channel-maps"),
    path("channel-maps/upsert", upsert_channel_map, name="foxia-channel-map-upsert"),
    path("sync-status", sync_status, name="foxia-sync-status"),
    # Cấu hình liên kết kênh × SKU → idnguonkhachhang (spec 2026-09-09)
    path("link-config", link_config, name="foxia-link-config"),
    path("link-config/channels", add_channels, name="foxia-link-config-channels"),
    path("link-config/channels/<int:pk>", delete_channel, name="foxia-link-config-channel"),
    path("link-config/products", add_products, name="foxia-link-config-products"),
    path("link-config/products/<int:pk>", delete_product, name="foxia-link-config-product"),
    path("link-config/cells", save_cells, name="foxia-link-config-cells"),
]
```

- [ ] **Step 5: Chạy test, xác nhận xanh**

```bash
$PYTEST apps/foxia/tests/test_foxia_link_config_api.py -q --reuse-db
```
Kỳ vọng: 11 passed (6 trong số đó từ `parametrize`).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/foxia/transport/link_config_views.py backend/apps/foxia/urls.py \
        backend/apps/foxia/tests/test_foxia_link_config_api.py
git commit -m "feat(foxia): API cấu hình bảng liên kết kênh×SKU (superuser)"
```

---

### Task 6: Container `worker-foxia`

**Files:**
- Modify: `docker-compose.prod.yml` (service `worker-slow` ~dòng 609-645; thêm service mới sau `worker-export`)
- Modify: `docker-compose.staging.yml` (service `worker-slow` ~dòng 280-295)
- Modify: `docker-compose.local.yml:311`

**Interfaces:**
- Consumes: `settings.CELERY_FOXIA_PUSH_QUEUE` = `foxia_push` (Task 2).
- Produces: container `desi-worker-foxia` tiêu thụ `foxia,foxia_push`.

- [ ] **Step 1: Gỡ `foxia` khỏi `worker-slow` (prod)**

Trong `docker-compose.prod.yml`, đổi dòng lệnh của `worker-slow`:

```
exec celery -A config worker -Q slow,foxia -l ...
```
thành
```
exec celery -A config worker -Q slow -l ...
```

Và cập nhật bình luận phía trên service cho khớp (bỏ ý "worker slow nghe thêm foxia").

- [ ] **Step 2: Thêm service `worker-foxia` (prod)**

Trong `docker-compose.prod.yml`, ngay sau service `worker-export`, thêm — **sao chép nguyên các khoá `build`, `depends_on`, `networks`, `restart`, `env_file`, `volumes` từ `worker-export` liền trên**, chỉ khác các dòng dưới:

```yaml
  # ──────────────────────── Celery worker Foxia ──────────────────────────
  # Container RIÊNG cho Foxia (spec 2026-09-09). Nghe hai queue:
  #   foxia      — backfill/đồng bộ, time_limit 900s, chạy hàng chục phút
  #   foxia_push — đẩy đơn landing, một request HTTP, phải đi ngay
  # Trước đây `foxia` do worker-slow ôm kèm; tách ra vì chiều đẩy đơn không được
  # xếp sau job Gemini của queue `slow`.
  worker-foxia:
    <<: *backend-common          # ⚠️ dùng đúng anchor mà worker-export đang dùng
    container_name: desi-worker-foxia
    environment:
      <<: *backend-env
      DB_APPLICATION_NAME: desi-worker-foxia
    mem_limit: 1024m
    # Giữ nguyên bao bọc bộ nhớ mà queue `foxia` đang có ở worker-slow: container
    # này tiếp quản luôn backfill (time_limit=900). 512m + thừa kế 180000 của
    # anchor là âm thầm thu nhỏ tài nguyên job đang chạy tốt.
    # environment còn thêm:
    #   CELERY_WORKER_MAX_MEMORY_PER_CHILD: ${FOXIA_WORKER_MAX_MEMORY_PER_CHILD:-358000}
    command: >
      sh -c "
        exec celery -A config worker -Q foxia,foxia_push -l ${DJANGO_LOG_LEVEL:-INFO} --concurrency=${FOXIA_WORKER_CONCURRENCY:-2}
      "
```

- [ ] **Step 3: Kiểm tra compose hợp lệ**

```bash
cd /home/desi/desi-order/.claude/worktrees/feat-foxia-push
docker compose -f docker-compose.prod.yml --env-file /home/desi/desi-order/.env config >/dev/null && echo "prod OK"
```
Kỳ vọng: in `prod OK`, không có cảnh báo YAML.

- [ ] **Step 4: Kiểm tra hai worker không cùng nghe `foxia`**

```bash
docker compose -f docker-compose.prod.yml --env-file /home/desi/desi-order/.env config \
  | grep -E "celery -A config worker -Q"
```
Kỳ vọng: đúng một dòng chứa `-Q foxia,foxia_push`, và dòng của `worker-slow` chỉ còn `-Q slow`.

- [ ] **Step 5: Lặp lại cho staging**

Trong `docker-compose.staging.yml`: đổi `-Q slow,foxia` → `-Q slow`; thêm `worker-foxia` theo đúng khuôn `worker-export` của file đó, `container_name: desi-staging-worker-foxia`, `--concurrency=1`, **không** đặt `mem_limit` (staging không đặt cho worker nào).

```bash
docker compose -f docker-compose.staging.yml --env-file /home/desi/desi-order/.env config >/dev/null && echo "staging OK"
```

- [ ] **Step 6: Local — gộp queue vào worker sẵn có**

`docker-compose.local.yml:311`: đổi
`-Q default,orders,kiotviet,lark_noti,audit,slow` thành
`-Q default,orders,kiotviet,lark_noti,audit,slow,foxia,foxia_push`
(local không dựng container thứ hai — một worker gộp là đủ cho máy dev).

```bash
docker compose -f docker-compose.local.yml --env-file /home/desi/desi-order/.env config >/dev/null && echo "local OK"
```

- [ ] **Step 7: Commit**

```bash
git add docker-compose.prod.yml docker-compose.staging.yml docker-compose.local.yml
git commit -m "chore(deploy): container worker-foxia nghe foxia + foxia_push"
```

---

### Task 7: Nối dây frontend (route, menu, i18n, client API)

**Files:**
- Modify: `backend/apps/users/models.py` (`VALID_SECTIONS`)
- Modify: `frontend/src/lib/routes.ts`
- Modify: `frontend/src/app/modules.ts`
- Modify: `frontend/src/app/routes.tsx`
- Modify: `frontend/src/app/routed-pages.tsx`
- Create: `frontend/src/i18n/messages/foxiaLink.ts`
- Modify: `frontend/src/i18n/messages.ts`, `frontend/src/i18n/messages/topnav.ts`
- Create: `frontend/src/lib/api/foxia-link.ts`
- Create: `frontend/src/features/foxia-link/queries.ts`

**Interfaces:**
- Consumes: 6 endpoint từ Task 5.
- Produces: `FoxiaLinkGrid`, `FoxiaLinkColumn`, `FoxiaLinkRow`, `FoxiaLinkCell` types; `getLinkConfig()`, `addLinkChannels(ids)`, `deleteLinkChannel(id)`, `addLinkProducts(ids)`, `deleteLinkProduct(id)`, `saveLinkCells(cells)`; hooks `useFoxiaLinkGrid()`, `foxiaLinkKeys`.

- [ ] **Step 1: Mở section ở backend**

`backend/apps/users/models.py`, thêm vào `VALID_SECTIONS` ngay sau `"foxia-report"`:

```python
    # Trang Cấu hình liên kết Foxia — superuser-only, CỐ Ý không có trong
    # PERMISSION_CATALOG (cùng khuôn media-*). Vẫn phải có mặt ở đây vì
    # `UserSerializer.get_allowed_sections` trả `list(VALID_SECTIONS)` cho
    # superuser — thiếu là chính Administrator bị FE ẩn menu.
    "foxia-link",
```

- [ ] **Step 2: Khai section ở FE**

`frontend/src/lib/routes.ts`: thêm `| "foxia-link"` vào union `AppSection` (đặt cạnh `"kiotviet"`), và `"foxia-link": "/settings/foxia-link",` vào `SECTION_PATHS`.

- [ ] **Step 3: Thêm mục menu**

`frontend/src/app/modules.ts`, trong `sections` của module `settings`, ngay sau dòng `kiotviet`:

```ts
      { key: "foxia-link", labelKey: "topnav.tabs.foxiaLink", icon: Plug },
```

- [ ] **Step 4: Thêm chuỗi i18n**

`frontend/src/i18n/messages/topnav.ts`: thêm `"topnav.tabs.foxiaLink": "Cấu hình liên kết Foxia",` vào khối `vi` (cạnh `topnav.tabs.kiotviet`) và `"topnav.tabs.foxiaLink": "Foxia Link Configuration",` vào khối `en`.

Tạo `frontend/src/i18n/messages/foxiaLink.ts`:

```ts
export const foxiaLink = {
  vi: {
    "foxiaLink.title": "Cấu hình liên kết Foxia",
    "foxiaLink.description":
      "Mỗi ô là id nguồn khách hàng (idnguonkhachhang) của Foxia cho cặp kênh × sản phẩm. Đơn từ landing page sẽ được đẩy sang Foxia theo id này.",
    "foxiaLink.addChannel": "Thêm kênh",
    "foxiaLink.addProduct": "Thêm sản phẩm",
    "foxiaLink.save": "Lưu",
    "foxiaLink.pendingCount": "{count} ô chờ lưu",
    "foxiaLink.saved": "Đã lưu cấu hình liên kết Foxia",
    "foxiaLink.saveFailed": "Lưu thất bại",
    "foxiaLink.removeChannel": "Bỏ kênh",
    "foxiaLink.removeProduct": "Bỏ sản phẩm",
    "foxiaLink.confirmRemoveChannel":
      "Bỏ kênh \"{name}\" khỏi bảng? {count} ô đã điền của kênh này sẽ bị xoá.",
    "foxiaLink.confirmRemoveProduct":
      "Bỏ sản phẩm \"{name}\" khỏi bảng? {count} ô đã điền của sản phẩm này sẽ bị xoá.",
    "foxiaLink.empty": "Chưa có gì trong bảng — thêm kênh và sản phẩm để bắt đầu.",
    "foxiaLink.columnProduct": "Sản phẩm",
    "foxiaLink.pickChannel": "Chọn kênh bán",
    "foxiaLink.pickProduct": "Chọn sản phẩm",
    "foxiaLink.search": "Tìm kiếm",
  },
  en: {
    "foxiaLink.title": "Foxia Link Configuration",
    "foxiaLink.description":
      "Each cell holds the Foxia customer-source id (idnguonkhachhang) for a channel × product pair. Landing-page orders are pushed to Foxia using this id.",
    "foxiaLink.addChannel": "Add channel",
    "foxiaLink.addProduct": "Add product",
    "foxiaLink.save": "Save",
    "foxiaLink.pendingCount": "{count} cells pending",
    "foxiaLink.saved": "Foxia link configuration saved",
    "foxiaLink.saveFailed": "Save failed",
    "foxiaLink.removeChannel": "Remove channel",
    "foxiaLink.removeProduct": "Remove product",
    "foxiaLink.confirmRemoveChannel":
      'Remove channel "{name}" from the grid? {count} filled cells will be deleted.',
    "foxiaLink.confirmRemoveProduct":
      'Remove product "{name}" from the grid? {count} filled cells will be deleted.',
    "foxiaLink.empty": "The grid is empty — add channels and products to start.",
    "foxiaLink.columnProduct": "Product",
    "foxiaLink.pickChannel": "Pick sales channels",
    "foxiaLink.pickProduct": "Pick products",
    "foxiaLink.search": "Search",
  },
};
```

`frontend/src/i18n/messages.ts`: thêm `import { foxiaLink } from "./messages/foxiaLink";` và `...foxiaLink.vi,` / `...foxiaLink.en,` vào hai khối tương ứng (giữ thứ tự alphabet).

- [ ] **Step 5: Client API**

Tạo `frontend/src/lib/api/foxia-link.ts`:

```ts
import { fetchJson } from "@/services/http";

/**
 * Bảng liên kết kênh × sản phẩm → idnguonkhachhang của Foxia.
 * Chỉ gọi mạng; điều phối (invalidate cache, toast, confirm) ở call site.
 */

export type FoxiaLinkColumn = {
  id: number;
  channel_id: number;
  code: string;
  name: string;
  sort_order: number;
};

export type FoxiaLinkRow = {
  id: number;
  product_id: number;
  sku: string;
  name: string;
  sort_order: number;
};

export type FoxiaLinkCell = {
  link_channel_id: number;
  link_product_id: number;
  foxia_source_id: number;
  source_name: string;
};

export type FoxiaLinkGrid = {
  channels: FoxiaLinkColumn[];
  products: FoxiaLinkRow[];
  cells: FoxiaLinkCell[];
};

export type FoxiaLinkCellInput = {
  link_channel_id: number;
  link_product_id: number;
  foxia_source_id: number | null;
};

const BASE = "/api/foxia/link-config";

export async function getLinkConfig(): Promise<FoxiaLinkGrid> {
  return (await fetchJson(BASE)) as FoxiaLinkGrid;
}

export async function addLinkChannels(channelIds: number[]): Promise<FoxiaLinkGrid> {
  return (await fetchJson(`${BASE}/channels`, {
    method: "POST",
    body: JSON.stringify({ channel_ids: channelIds }),
  })) as FoxiaLinkGrid;
}

export async function deleteLinkChannel(id: number): Promise<{ deleted_cells: number }> {
  return (await fetchJson(`${BASE}/channels/${id}`, { method: "DELETE" })) as {
    deleted_cells: number;
  };
}

export async function addLinkProducts(productIds: number[]): Promise<FoxiaLinkGrid> {
  return (await fetchJson(`${BASE}/products`, {
    method: "POST",
    body: JSON.stringify({ product_ids: productIds }),
  })) as FoxiaLinkGrid;
}

export async function deleteLinkProduct(id: number): Promise<{ deleted_cells: number }> {
  return (await fetchJson(`${BASE}/products/${id}`, { method: "DELETE" })) as {
    deleted_cells: number;
  };
}

export async function saveLinkCells(cells: FoxiaLinkCellInput[]): Promise<FoxiaLinkGrid> {
  return (await fetchJson(`${BASE}/cells`, {
    method: "PUT",
    body: JSON.stringify({ cells }),
  })) as FoxiaLinkGrid;
}
```

`fetchJson(url, options?: RequestInit)` tự gắn `Content-Type: application/json` cho mọi method khác GET/HEAD (`frontend/src/services/http.ts:190`), nên không cần set header thủ công.

- [ ] **Step 6: Query hooks**

Tạo `frontend/src/features/foxia-link/queries.ts`:

```ts
import { useQuery } from "@tanstack/react-query";

import { getLinkConfig } from "@/lib/api/foxia-link";

export const foxiaLinkKeys = {
  all: ["foxia-link"] as const,
  grid: ["foxia-link", "grid"] as const,
};

/** GET /api/foxia/link-config — toàn bộ bảng trong một request. */
export function useFoxiaLinkGrid() {
  return useQuery({ queryKey: foxiaLinkKeys.grid, queryFn: getLinkConfig });
}
```

- [ ] **Step 7: Route**

`frontend/src/app/routed-pages.tsx`, cạnh `ContactFlowRoute`:

```tsx
export function FoxiaLinkRoute() {
  return <FoxiaLinkSettingsPage />;
}
```
`FoxiaLinkSettingsPage` là **default export** (đúng khuôn `MediaSettingsPage`/`ContactFlowSettingsPage`), nên import theo đúng cách file này đang import `ContactFlowSettingsPage`.

`frontend/src/app/routes.tsx`, ngay sau khối `settings/kiotviet`:

```tsx
      {
        // Bảng liên kết quyết định đơn chảy vào nguồn nào bên Foxia — cùng mức
        // rủi ro với cấu hình kênh KiotViet, nên cũng gate superuser.
        path: "settings/foxia-link",
        element: (
          <GuardSuperuser>
            <FoxiaLinkRoute />
          </GuardSuperuser>
        ),
      },
```

- [ ] **Step 8: Typecheck**

```bash
docker run --rm -v /home/desi:/home/desi \
  -w /home/desi/desi-order/.claude/worktrees/feat-foxia-push/frontend \
  node:22-alpine npm run typecheck
```
Kỳ vọng: chỉ còn lỗi "Cannot find module .../FoxiaLinkSettingsPage" (Task 8 tạo file đó). Nếu vậy, tạm tạo file với một component rỗng `export function FoxiaLinkSettingsPage() { return null; }` để typecheck xanh, rồi làm tiếp. Lưu ý: **default export** — `export default function FoxiaLinkSettingsPage() { return null; }`.

- [ ] **Step 9: Chạy test nav/menu sẵn có**

```bash
docker run --rm -v /home/desi:/home/desi \
  -w /home/desi/desi-order/.claude/worktrees/feat-foxia-push/frontend \
  node:22-alpine npx vitest run src/app/__tests__ src/lib/__tests__/routes.test.ts src/components/__tests__/Sidebar.test.tsx
```
Kỳ vọng: PASS — các test này đối chiếu `AppSection` với `VALID_SECTIONS`/menu, nên chúng là chốt chặn cho Step 1-3.

- [ ] **Step 10: Commit**

```bash
git add backend/apps/users/models.py frontend/src/lib/routes.ts frontend/src/app/modules.ts \
        frontend/src/app/routes.tsx frontend/src/app/routed-pages.tsx \
        frontend/src/i18n/messages.ts frontend/src/i18n/messages/topnav.ts \
        frontend/src/i18n/messages/foxiaLink.ts frontend/src/lib/api/foxia-link.ts \
        frontend/src/features/foxia-link/queries.ts \
        frontend/src/features/foxia-link/pages/FoxiaLinkSettingsPage.tsx
git commit -m "feat(fe): route + menu + client API cho trang Cấu hình liên kết Foxia"
```

---

### Task 8: Trang bảng n×m

**Files:**
- Create/Modify: `frontend/src/features/foxia-link/pages/FoxiaLinkSettingsPage.tsx`
- Test: `frontend/src/features/foxia-link/__tests__/FoxiaLinkSettingsPage.test.tsx`

**Interfaces:**
- Consumes: `useFoxiaLinkGrid`, `foxiaLinkKeys`, và toàn bộ hàm trong `@/lib/api/foxia-link` (Task 7); `listChannels()` từ `@/lib/api/channels`; `getProductOptions()` từ `@/lib/api/products`.
- Produces: `FoxiaLinkSettingsPage` — **default export**, không nhận props, tự nạp dữ liệu (đúng khuôn `MediaSettingsPage`).

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/features/foxia-link/__tests__/FoxiaLinkSettingsPage.test.tsx`. Trước khi viết, mở `frontend/src/features/media/__tests__/MediaSettingsPage.test.tsx` và dùng đúng bộ khung dựng (`QueryClientProvider`, provider i18n, cách mock `@/lib/api/...`) của repo. Nội dung test:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@/lib/api/foxia-link", () => ({
  getLinkConfig: vi.fn(),
  addLinkChannels: vi.fn(),
  deleteLinkChannel: vi.fn(),
  addLinkProducts: vi.fn(),
  deleteLinkProduct: vi.fn(),
  saveLinkCells: vi.fn(),
}));
vi.mock("@/lib/api/channels", () => ({ listChannels: vi.fn(async () => []) }));
vi.mock("@/lib/api/products", () => ({ getProductOptions: vi.fn(async () => []) }));

import * as api from "@/lib/api/foxia-link";

const GRID = {
  channels: [
    { id: 1, channel_id: 11, code: "kenh-a", name: "Kênh A", sort_order: 0 },
    { id: 2, channel_id: 12, code: "kenh-b", name: "Kênh B", sort_order: 1 },
  ],
  products: [{ id: 5, product_id: 55, sku: "SKU-A", name: "SP A", sort_order: 0 }],
  cells: [
    { link_channel_id: 1, link_product_id: 5, foxia_source_id: 1234, source_name: "Nguồn X" },
  ],
};

describe("FoxiaLinkSettingsPage", () => {
  beforeEach(() => {
    vi.mocked(api.getLinkConfig).mockResolvedValue(structuredClone(GRID));
    vi.mocked(api.saveLinkCells).mockResolvedValue(structuredClone(GRID));
    vi.mocked(api.deleteLinkChannel).mockResolvedValue({ deleted_cells: 1 });
  });

  it("dựng đúng số cột và hàng từ dữ liệu API", async () => {
    renderPage();
    expect(await screen.findByText("Kênh A")).toBeInTheDocument();
    expect(screen.getByText("Kênh B")).toBeInTheDocument();
    expect(screen.getByText("SKU-A")).toBeInTheDocument();
    expect(screen.getByDisplayValue("1234")).toBeInTheDocument();
  });

  it("sửa ô thì bật nút Lưu và gửi đúng ô đã đổi", async () => {
    const user = userEvent.setup();
    renderPage();

    const input = await screen.findByDisplayValue("1234");
    await user.clear(input);
    await user.type(input, "9999");

    const save = screen.getByRole("button", { name: /Lưu/ });
    expect(save).toBeEnabled();
    await user.click(save);

    await waitFor(() =>
      expect(api.saveLinkCells).toHaveBeenCalledWith([
        { link_channel_id: 1, link_product_id: 5, foxia_source_id: 9999 },
      ]),
    );
  });

  it("bỏ cột thì hỏi xác nhận kèm số ô sẽ mất", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();

    await screen.findByText("Kênh A");
    await user.click(screen.getByRole("button", { name: /Bỏ kênh Kênh A/ }));

    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("1 ô"));
    expect(api.deleteLinkChannel).not.toHaveBeenCalled();
  });
});
```

`renderPage()` là helper cục bộ dựng `QueryClientProvider` + provider i18n rồi render `<FoxiaLinkSettingsPage />` — sao chép nguyên từ `MediaSettingsPage.test.tsx`.

- [ ] **Step 2: Chạy test, xác nhận đỏ**

```bash
docker run --rm -v /home/desi:/home/desi \
  -w /home/desi/desi-order/.claude/worktrees/feat-foxia-push/frontend \
  node:22-alpine npx vitest run src/features/foxia-link
```
Kỳ vọng: FAIL — trang rỗng, không tìm thấy "Kênh A".

- [ ] **Step 3: Viết trang**

Ghi `frontend/src/features/foxia-link/pages/FoxiaLinkSettingsPage.tsx`:

```tsx
import { useMemo, useState } from "react";
import { Plus, X } from "lucide-react";
import { toast } from "sonner";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { useI18n } from "@/i18n";
import { listChannels } from "@/lib/api/channels";
import { getProductOptions } from "@/lib/api/products";
import {
  addLinkChannels,
  addLinkProducts,
  deleteLinkChannel,
  deleteLinkProduct,
  saveLinkCells,
  type FoxiaLinkCellInput,
  type FoxiaLinkGrid,
} from "@/lib/api/foxia-link";
import { foxiaLinkKeys, useFoxiaLinkGrid } from "../queries";

/**
 * Trang "Cấu hình liên kết Foxia" (superuser-only).
 *
 * Bảng n×m: cột = kênh bán, hàng = sản phẩm (SKU), ô = `idnguonkhachhang` của
 * Foxia. Id trong ô quyết định đơn landing của cặp (kênh × SKU) đó được đẩy vào
 * URL nào — xem spec 2026-09-09.
 *
 * Ô đang gõ giữ ở `draft` dạng CHUỖI, không ép số ngay: gõ dở "12" rồi ép số sẽ
 * làm con trỏ nhảy và không phân biệt được "đang xoá trắng" với "số 0".
 */
const cellKey = (columnId: number, rowId: number) => `${columnId}:${rowId}`;

export default function FoxiaLinkSettingsPage() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const gridQuery = useFoxiaLinkGrid();
  const grid: FoxiaLinkGrid = gridQuery.data ?? { channels: [], products: [], cells: [] };

  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [picker, setPicker] = useState<"channel" | "product" | null>(null);

  /** Giá trị đã lưu trên server, tra theo cột:hàng. */
  const saved = useMemo(() => {
    const map: Record<string, { id: number; sourceName: string }> = {};
    for (const cell of grid.cells) {
      map[cellKey(cell.link_channel_id, cell.link_product_id)] = {
        id: cell.foxia_source_id,
        sourceName: cell.source_name,
      };
    }
    return map;
  }, [grid.cells]);

  /** Chỉ những ô THỰC SỰ khác server mới được gửi đi. */
  const changed: FoxiaLinkCellInput[] = useMemo(() => {
    const out: FoxiaLinkCellInput[] = [];
    for (const [key, raw] of Object.entries(draft)) {
      const [columnId, rowId] = key.split(":").map(Number);
      const current = saved[key]?.id ?? null;
      const next = raw.trim() === "" ? null : Number(raw.trim());
      if (next !== null && !Number.isFinite(next)) continue;
      if (next === current) continue;
      out.push({ link_channel_id: columnId, link_product_id: rowId, foxia_source_id: next });
    }
    return out;
  }, [draft, saved]);

  const refresh = () => queryClient.invalidateQueries({ queryKey: foxiaLinkKeys.grid });

  const handleSave = async () => {
    setSaving(true);
    try {
      await saveLinkCells(changed);
      setDraft({});
      await refresh();
      toast.success(t("foxiaLink.saved"));
    } catch (error) {
      toast.error((error as Error)?.message || t("foxiaLink.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  /** Số ô ĐANG có id trong một cột/hàng — con số đưa vào hộp xác nhận. */
  const countCells = (axis: "channel" | "product", axisId: number) =>
    grid.cells.filter((cell) =>
      axis === "channel" ? cell.link_channel_id === axisId : cell.link_product_id === axisId,
    ).length;

  const handleRemoveChannel = async (columnId: number, name: string) => {
    const message = t("foxiaLink.confirmRemoveChannel")
      .replace("{name}", name)
      .replace("{count}", String(countCells("channel", columnId)));
    if (!window.confirm(message)) return;
    await deleteLinkChannel(columnId);
    setDraft({});
    await refresh();
  };

  const handleRemoveProduct = async (rowId: number, name: string) => {
    const message = t("foxiaLink.confirmRemoveProduct")
      .replace("{name}", name)
      .replace("{count}", String(countCells("product", rowId)));
    if (!window.confirm(message)) return;
    await deleteLinkProduct(rowId);
    setDraft({});
    await refresh();
  };

  const isEmpty = grid.channels.length === 0 && grid.products.length === 0;

  return (
    <div className="space-y-4 p-4">
      <PageHeader title={t("foxiaLink.title")} description={t("foxiaLink.description")} />

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" onClick={() => setPicker("channel")}>
          <Plus className="mr-1 h-4 w-4" />
          {t("foxiaLink.addChannel")}
        </Button>
        <Button variant="outline" onClick={() => setPicker("product")}>
          <Plus className="mr-1 h-4 w-4" />
          {t("foxiaLink.addProduct")}
        </Button>
        <div className="ml-auto flex items-center gap-3">
          {changed.length > 0 && (
            <span className="text-sm text-muted-foreground">
              {t("foxiaLink.pendingCount").replace("{count}", String(changed.length))}
            </span>
          )}
          <Button onClick={handleSave} disabled={changed.length === 0 || saving}>
            {t("foxiaLink.save")}
          </Button>
        </div>
      </div>

      {gridQuery.isLoading && <div className="p-8 text-center text-sm">…</div>}
      {gridQuery.isError && (
        <div className="p-8 text-center text-sm text-destructive">
          {(gridQuery.error as Error)?.message}
        </div>
      )}

      {!gridQuery.isLoading && isEmpty && (
        <div className="rounded-md border p-8 text-center text-sm text-muted-foreground">
          {t("foxiaLink.empty")}
        </div>
      )}

      {!gridQuery.isLoading && !isEmpty && (
        // Bảng có thể rất rộng — cuộn NGANG bên trong khung, không đẩy cả trang.
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full border-collapse text-sm">
            <thead className="sticky top-0 z-20 bg-background">
              <tr>
                <th className="sticky left-0 z-30 min-w-56 border-b border-r bg-background p-2 text-left">
                  {t("foxiaLink.columnProduct")}
                </th>
                {grid.channels.map((column) => (
                  <th key={column.id} className="min-w-40 border-b border-r p-2 text-left align-top">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="font-medium">{column.name}</div>
                        <div className="text-xs text-muted-foreground">{column.code}</div>
                      </div>
                      <button
                        type="button"
                        aria-label={`${t("foxiaLink.removeChannel")} ${column.name}`}
                        onClick={() => handleRemoveChannel(column.id, column.name)}
                        className="text-muted-foreground hover:text-destructive"
                      >
                        <X className="h-4 w-4" />
                      </button>
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {grid.products.map((row) => (
                <tr key={row.id}>
                  <th className="sticky left-0 z-10 border-b border-r bg-background p-2 text-left align-top font-normal">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="font-medium">{row.name}</div>
                        <div className="text-xs text-muted-foreground">{row.sku}</div>
                      </div>
                      <button
                        type="button"
                        aria-label={`${t("foxiaLink.removeProduct")} ${row.name}`}
                        onClick={() => handleRemoveProduct(row.id, row.name)}
                        className="text-muted-foreground hover:text-destructive"
                      >
                        <X className="h-4 w-4" />
                      </button>
                    </div>
                  </th>
                  {grid.channels.map((column) => {
                    const key = cellKey(column.id, row.id);
                    const current = saved[key];
                    const value = draft[key] ?? (current ? String(current.id) : "");
                    const dirty = key in draft && value !== (current ? String(current.id) : "");
                    return (
                      <td key={column.id} className="border-b border-r p-2 align-top">
                        <Input
                          inputMode="numeric"
                          aria-label={`${row.name} × ${column.name}`}
                          value={value}
                          onChange={(event) =>
                            setDraft((prev) => ({ ...prev, [key]: event.target.value }))
                          }
                          className={dirty ? "bg-amber-50 dark:bg-amber-950/30" : undefined}
                        />
                        {current?.sourceName ? (
                          <div className="mt-1 text-xs text-muted-foreground">
                            {current.sourceName}
                          </div>
                        ) : null}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {picker !== null && (
        <AxisPickerDialog
          kind={picker}
          excludeIds={
            picker === "channel"
              ? grid.channels.map((c) => c.channel_id)
              : grid.products.map((p) => p.product_id)
          }
          onClose={() => setPicker(null)}
          onConfirm={async (ids) => {
            if (ids.length > 0) {
              await (picker === "channel" ? addLinkChannels(ids) : addLinkProducts(ids));
              await refresh();
            }
            setPicker(null);
          }}
        />
      )}
    </div>
  );
}

/**
 * Hộp chọn kênh/sản phẩm để thêm vào bảng. Ẩn mục ĐÃ có trong bảng — thêm lại
 * là no-op ở BE, nhưng để chúng hiện ra chỉ làm người dùng tưởng mình quên.
 */
function AxisPickerDialog({
  kind,
  excludeIds,
  onClose,
  onConfirm,
}: {
  kind: "channel" | "product";
  excludeIds: number[];
  onClose: () => void;
  onConfirm: (ids: number[]) => Promise<void>;
}) {
  const { t } = useI18n();
  const [term, setTerm] = useState("");
  const [selected, setSelected] = useState<number[]>([]);

  const optionsQuery = useQuery({
    queryKey: ["foxia-link", "axis-options", kind],
    queryFn: async () =>
      kind === "channel"
        ? (await listChannels()).map((row) => ({ id: row.id, label: row.name, hint: row.code }))
        : (await getProductOptions({ limit: 500 })).map((row) => ({
            id: row.id,
            label: row.name,
            hint: row.sku,
          })),
  });

  const excluded = new Set(excludeIds);
  const options = (optionsQuery.data ?? [])
    .filter((option) => !excluded.has(option.id))
    .filter((option) =>
      term.trim() === ""
        ? true
        : `${option.label} ${option.hint}`.toLowerCase().includes(term.trim().toLowerCase()),
    );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-md border bg-background p-4 shadow-lg">
        <h2 className="mb-2 font-medium">
          {kind === "channel" ? t("foxiaLink.pickChannel") : t("foxiaLink.pickProduct")}
        </h2>
        <Input
          autoFocus
          placeholder={t("foxiaLink.search")}
          value={term}
          onChange={(event) => setTerm(event.target.value)}
        />
        <div className="my-3 max-h-72 overflow-y-auto">
          {options.map((option) => (
            <label key={option.id} className="flex items-center gap-2 py-1">
              <input
                type="checkbox"
                checked={selected.includes(option.id)}
                onChange={(event) =>
                  setSelected((prev) =>
                    event.target.checked
                      ? [...prev, option.id]
                      : prev.filter((id) => id !== option.id),
                  )
                }
              />
              <span>{option.label}</span>
              <span className="text-xs text-muted-foreground">{option.hint}</span>
            </label>
          ))}
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button onClick={() => onConfirm(selected)} disabled={selected.length === 0}>
            {kind === "channel" ? t("foxiaLink.addChannel") : t("foxiaLink.addProduct")}
          </Button>
        </div>
      </div>
    </div>
  );
}
```

Ba chỗ phải đối chiếu với repo trước khi coi là xong (đừng đoán):
- `PageHeader` có nhận prop `description` không — nếu không, đưa dòng mô tả ra một `<p>` riêng.
- `t("common.cancel")` đã tồn tại chưa — nếu chưa, thêm khoá vào `messages/foxiaLink.ts`.
- `listChannels()` trả `ChannelRow` có `id`/`name`/`code`, và `getProductOptions()` trả `ProductOption` có `id`/`name`/`sku` — xác nhận bằng `frontend/src/lib/api/channels.ts` và `products.ts`.

- [ ] **Step 4: Chạy test, xác nhận xanh**

```bash
docker run --rm -v /home/desi:/home/desi \
  -w /home/desi/desi-order/.claude/worktrees/feat-foxia-push/frontend \
  node:22-alpine npx vitest run src/features/foxia-link
```
Kỳ vọng: 3 passed.

- [ ] **Step 5: Typecheck + lint**

```bash
docker run --rm -v /home/desi:/home/desi \
  -w /home/desi/desi-order/.claude/worktrees/feat-foxia-push/frontend \
  node:22-alpine sh -c "npm run typecheck && npm run lint"
```
Kỳ vọng: không lỗi.

- [ ] **Step 6: Chạy toàn bộ test backend của foxia + orders lần cuối**

```bash
$PYTEST apps/foxia apps/common/tests/test_celery_routes.py -q --reuse-db
```
Kỳ vọng: toàn bộ PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/foxia-link
git commit -m "feat(fe): trang Cấu hình liên kết Foxia — bảng n×m thêm/bỏ hàng cột"
```

---

## Ghi chú triển khai (đưa vào mô tả MR)

1. Chạy migration: `docker exec desi-web python manage.py migrate foxia`.
2. **Bắt buộc** dựng lại cả hai worker để `worker-slow` nhả queue `foxia`:
   `docker compose -f docker-compose.prod.yml up -d worker-slow worker-foxia`.
3. Vào **Cài đặt → Cấu hình liên kết Foxia**, thêm kênh + sản phẩm và điền id.
4. Chỉ sau đó mới đặt `FOXIA_PUSH_ENABLED=1` trong `.env` và
   `docker compose -f docker-compose.prod.yml up -d worker worker-foxia` (`worker`,
   KHÔNG PHẢI `web` — `create_order_from_landing` chạy trong task Celery
   `process_landing_ingest`, route `CELERY_ORDERS_QUEUE`, tiêu thụ bởi service
   `worker` (`-Q default,orders,lark_noti,audit`), không phải `web`; `web` chỉ cần
   recreate thêm nếu muốn env nhất quán cho tiến trình UI, không phải để tính
   năng chạy).
5. Kiểm chứng: tạo một đơn landing thật, xem `/admin/foxia/foxiaorderpush/` có dòng `ok`.
