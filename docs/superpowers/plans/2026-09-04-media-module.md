# Module Media (ads-crm proxy) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm module **Media** vào sidebar desi-order: 4 trang nghiệp vụ + trang Cài đặt kết nối, mọi dữ liệu đọc/ghi thời gian thực sang hệ thống ads-crm qua backend proxy `/api/media/*`, phân quyền theo từng người (key riêng → ads-crm enforce; key chung + vai trò Media → desi-order enforce).

**Architecture:** Django app mới `apps/media` theo clean architecture (domain thuần → application → infrastructure/transport), một client HTTP bọc `apps.common.http_client.HttpClient`, allowlist route tường minh trong `urls.py`. Frontend thêm module vào registry `app/modules.ts`, feature-slice `features/media/`, type API viết tay theo tài liệu ads-crm trong `lib/api/media.ts`.

**Tech Stack:** Django 6 + DRF + pytest (backend), React 19 + TypeScript + react-query + vitest (frontend), Redis cache (`django.core.cache`), `EncryptedCharField` (`apps.common.fields`).

**Spec:** `docs/superpowers/specs/2026-09-04-media-module-design.md` — đọc trước khi làm bất kỳ task nào. Số mục (§) trong plan trỏ về spec.

## Global Constraints

- Làm trong worktree `/home/desi/desi-order/.claude/worktrees/feat-media`, branch `feat/media-module`. Không `git add -A` (symlink `frontend/node_modules`).
- Backend: Python ≥ 3.12, `from __future__ import annotations`, type hint đầy đủ, file ≤ 500 dòng. Domain KHÔNG import Django. Chỉ `infrastructure/` được gọi mạng, qua `HttpClient`.
- Frontend: file ≤ 400 dòng, không `any`, i18n đủ **vi + en** cho mọi key mới, dùng component trong `components/ui`, không thêm thư viện.
- Tên module hiển thị: **"Media"** (vi và en). Section keys: `media-contents`, `media-proposals`, `media-reports`, `media-taxonomy`, `media-settings`.
- Không proxy: `/contents/export`, `save-to-gdrive`, `gdrive/*`, `users`, `api-keys`, `audit-logs`, `video-cache/stats`, `notifications`, `auth/login|logout|change-password`, `ws`, `contents/:id/video`.
- Chặn toàn bộ outbound khi `settings.IS_STAGING` (D10).
- Chạy test backend (từ `backend/` của worktree; bật container trước: `docker start desi-test-pg desi-test-redis`):
  ```bash
  cd /home/desi/desi-order/.claude/worktrees/feat-media/backend
  POSTGRES_DB=desi_test_media POSTGRES_USER=desi POSTGRES_PASSWORD=ci-postgres-pass \
  POSTGRES_HOST=localhost POSTGRES_PORT=5433 REDIS_HOST=localhost REDIS_PORT=6380 REDIS_PASSWORD= \
  APP_ENV=development DJANGO_DEBUG=0 LOG_FORMAT=text \
  ZENIFY_DOMAIN= ZENIFY_API_KEY= ZENIFY_API_SECRET= \
  ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= \
  /home/desi/desi-order/.venv/bin/pytest <path> -q --create-db
  ```
  (`--create-db` lần đầu và sau mỗi migration mới; các lần sau `--reuse-db`.) Gọi tắt bên dưới là **`PYTEST <path>`**.
- Chạy frontend (node không có trên PATH): trước hết `ln -s /home/desi/desi-order/frontend/node_modules /home/desi/desi-order/.claude/worktrees/feat-media/frontend/node_modules` (một lần). Rồi:
  ```bash
  docker run --rm -v /home/desi:/home/desi -w /home/desi/desi-order/.claude/worktrees/feat-media/frontend node:22-alpine npx vitest run <path>
  docker run --rm -v /home/desi:/home/desi -w /home/desi/desi-order/.claude/worktrees/feat-media/frontend node:22-alpine npm run typecheck
  ```
  Gọi tắt là **`VITEST <path>`** và **`TYPECHECK`**. Chạy lần lượt, không song song.
- Commit message tiếng Việt, tiền tố `feat(media):` / `test(media):` / `docs(media):`, kết bằng hai dòng:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01K1sE8SukdiHpVFd48vyC4E
  ```

---

## File Structure

**Backend — tạo mới (`backend/apps/media/`)**

| File | Trách nhiệm |
|---|---|
| `__init__.py`, `apps.py` | Khai báo app `apps.media` |
| `models.py` | `MediaConnection`, `MediaContentOwner`, `MediaProposalOwner` |
| `migrations/0001_initial.py` | Schema 3 bảng |
| `exceptions.py` | Cây exception `MediaError` (status + code + detail) |
| `domain/roles.py` | Hằng role, `Action`, `ROLE_MATRIX`, `check()` — thuần Python |
| `domain/errors.py` | Bảng dịch mã lỗi ads-crm → tiếng Việt |
| `contracts/dtos.py` | `Credential` dataclass |
| `infrastructure/ads_crm_client.py` | `AdsCrmClient` (HTTP, header, parse lỗi, staging guard) |
| `application/services/credential_resolver.py` | `resolve(user) -> Credential` |
| `application/services/access_policy.py` | `authorize(cred, action, owner_check)` |
| `application/services/proxy_service.py` | `MediaProxy`: forward + cache taxonomy + ghi owner |
| `application/services/connection_service.py` | Test kết nối, lưu settings |
| `transport/errors.py` | decorator `media_errors` → Response `{detail, code}` |
| `transport/params.py` | Allowlist query param từng route |
| `transport/api_views.py` | View proxy nghiệp vụ |
| `transport/settings_views.py` | `/api/media/settings*` |
| `transport/serializers.py` | Serializer settings + body tối thiểu |
| `urls.py` | Allowlist route |
| `tests/` | pytest |

**Backend — sửa**

| File | Thay đổi |
|---|---|
| `apps/users/models.py` | `MediaRole` TextChoices; `User.media_role`, `User.media_api_key`; 5 key vào `VALID_SECTIONS` |
| `apps/users/migrations/0046_user_media_fields.py` | AddField ×2 |
| `apps/users/serializers.py` | `UserSerializer` (+3 field), Create/Update serializer (+2 field), `get_allowed_sections` union Media |
| `apps/users/transport/views/users_views_impl/admin_user_views.py` | PATCH ghi `media_role`, `media_api_key` |
| `apps/users/services.py` | `create_admin_user` ghi `media_api_key` |
| `config/settings_parts/base.py` | `INSTALLED_APPS` + 3 env `MEDIA_*` |
| `config/urls.py` | `^api/media/` |
| `.importlinter` | `media` vào tầng cùng `foxia` |

**Frontend — tạo mới**

| File | Trách nhiệm |
|---|---|
| `lib/api/media.ts` | Type theo tài liệu ads-crm + hàm gọi `/api/media/*` |
| `features/media/access.ts` | `MediaRole`, `MediaAction`, `mediaCan` |
| `features/media/filters.ts` | `ContentFilters` ↔ `URLSearchParams` |
| `features/media/queries.ts` | react-query hooks + `mediaKeys` |
| `features/media/components/MediaAccessGate.tsx` | Gate provisioned / connection banner |
| `features/media/components/ContentFilterBar.tsx`, `ContentTable.tsx`, `ContentDetailSheet.tsx`, `ContentFormDialog.tsx`, `ContentTagsDialog.tsx`, `AdEvaluationList.tsx` | Trang nội dung |
| `features/media/components/ProposalTable.tsx`, `ProposalFormDialog.tsx` | Trang đề xuất |
| `features/media/components/TaxonomyTabs.tsx`, `TaxonomyFormDialog.tsx` | Trang danh mục |
| `features/media/components/ReportKpiRow.tsx`, `ReportTables.tsx` | Trang báo cáo |
| `features/media/pages/MediaContentsPage.tsx`, `MediaProposalsPage.tsx`, `MediaReportsPage.tsx`, `MediaTaxonomyPage.tsx`, `MediaSettingsPage.tsx` | Trang |
| `features/media/__tests__/*.test.ts(x)` | vitest |
| `i18n/messages/media.ts` | Toàn bộ chuỗi `media.*` vi/en |

**Frontend — sửa**

| File | Thay đổi |
|---|---|
| `lib/routes.ts` | `AppSection` +5, `SECTION_TO_PATH` +5, `sectionFromPath` nhận `/media/contents/:id` |
| `app/modules.ts` | Module `media` + section `media-settings` trong `settings` |
| `app/routed-pages.tsx`, `app/routes.tsx` | Lazy import + 6 route |
| `i18n/messages.ts`, `messages/sidebar.ts`, `messages/topnav.ts`, `messages/app.ts`, `messages/users.ts` | Khoá mới |
| `features/users/types.ts`, `UserCreateDialog.tsx`, `UserEditDialog.tsx`, `components/UserManagement.tsx`, `features/hr/components/EmployeeAccountCreateDialog.tsx` | Ô Vai trò Media + API key Media |
| `app/__tests__/nav-layout.test.ts` | Kỳ vọng groupKeys có `media` |

---

## Giai đoạn 1 — Nền + đọc (Task 1–14)

### Task 1: Skeleton app `apps/media` + model + migration + đăng ký

**Files:**
- Create: `backend/apps/media/__init__.py`, `apps.py`, `models.py`, `migrations/__init__.py`, `migrations/0001_initial.py`, `tests/__init__.py`, `tests/test_models.py`
- Modify: `backend/apps/users/models.py` (sau `VALID_SECTIONS`, và trong `class User`), `backend/apps/users/migrations/0046_user_media_fields.py` (create), `backend/config/settings_parts/base.py:84` (INSTALLED_APPS, sau `"apps.daily_report"`), `backend/config/urls.py:22` (sau dòng foxia), `backend/.importlinter:41`

**Interfaces:**
- Produces: `apps.users.models.MediaRole` (TextChoices: `""`, `viewer`, `marketing`, `uploader`, `admin`), `User.media_role: str`, `User.media_api_key: str`; `apps.media.models.MediaConnection.get_active()`, `MediaContentOwner(remote_id, user)`, `MediaProposalOwner(remote_id, user)`.

- [ ] **Step 1: Viết test model**

`backend/apps/media/tests/test_models.py`:
```python
"""Model Media: connection singleton-ish + bảng owner mô phỏng ownership khi dùng key chung."""

import pytest
from django.db import IntegrityError

from apps.media.models import MediaConnection, MediaContentOwner
from apps.users.models import MediaRole, User

pytestmark = pytest.mark.django_db


def test_get_active_returns_latest_active_connection():
    MediaConnection.objects.create(base_url="https://old.example", shared_api_key="dak_old", is_active=False)
    live = MediaConnection.objects.create(base_url="https://media.example/", shared_api_key="dak_live")
    assert MediaConnection.get_active() == live
    # base_url được chuẩn hoá bỏ dấu "/" cuối khi đọc.
    assert live.resolve_base_url() == "https://media.example"


def test_get_active_none_when_nothing_configured():
    assert MediaConnection.get_active() is None


def test_content_owner_unique_per_remote_id():
    u = User.objects.create_user(username="up1", password="x")
    MediaContentOwner.objects.create(remote_id=12, user=u)
    with pytest.raises(IntegrityError):
        MediaContentOwner.objects.create(remote_id=12, user=u)


def test_user_media_fields_default_blank():
    u = User.objects.create_user(username="u1", password="x")
    assert u.media_role == MediaRole.NONE == ""
    assert u.media_api_key == ""
    assert set(MediaRole.values) == {"", "viewer", "marketing", "uploader", "admin"}
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `PYTEST apps/media/tests/test_models.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.media'`.

- [ ] **Step 3: Tạo app + model**

`backend/apps/media/__init__.py`: rỗng.

`backend/apps/media/apps.py`:
```python
from django.apps import AppConfig


class MediaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.media"
    verbose_name = "Media (ads-crm proxy)"
```

`backend/apps/media/models.py`:
```python
"""Model của module Media — xem docs/superpowers/specs/2026-09-04-media-module-design.md §4.2.

Không có bảng nghiệp vụ nào của ads-crm được mirror về đây. Ba bảng chỉ phục vụ
việc NÓI CHUYỆN với ads-crm: kết nối (key chung) và hai bảng owner để mô phỏng
kiểm tra chủ sở hữu khi mọi request đi bằng một tài khoản service.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.fields import EncryptedCharField


class MediaConnection(models.Model):
    """Kết nối tới ads-crm (thường chỉ 1 dòng active). Key chung mã hoá at-rest."""

    base_url = models.CharField(max_length=255, help_text="VD: https://media.example.vn (không kèm /api/v1)")
    shared_api_key = EncryptedCharField(max_length=1024, blank=True, default="")
    is_active = models.BooleanField(default=True)

    last_check_at = models.DateTimeField(null=True, blank=True)
    last_check_ok = models.BooleanField(null=True, blank=True)
    last_check_error = models.TextField(blank=True, default="")
    shared_key_role = models.CharField(max_length=32, blank=True, default="")
    shared_key_username = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "media_connections"

    def __str__(self) -> str:
        return f"MediaConnection({self.base_url})"

    @classmethod
    def get_active(cls) -> MediaConnection | None:
        return cls.objects.filter(is_active=True).order_by("-updated_at").first()

    def resolve_base_url(self) -> str:
        return (self.base_url or "").strip().rstrip("/")


class MediaContentOwner(models.Model):
    """Nội dung ads-crm `remote_id` do user desi-order nào tạo QUA KEY CHUNG.

    Chỉ ghi ở chế độ key chung; key riêng thì ads-crm đã biết chủ sở hữu thật.
    """

    remote_id = models.BigIntegerField(unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "media_content_owners"


class MediaProposalOwner(models.Model):
    remote_id = models.BigIntegerField(unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "media_proposal_owners"
```

- [ ] **Step 4: Thêm field vào `User` + `VALID_SECTIONS`**

Trong `backend/apps/users/models.py`, ngay trước `class Department`:
```python
class MediaRole(models.TextChoices):
    """Vai trò Media khi user dùng KEY CHUNG của ads-crm (spec Media §5.2).

    Giá trị phải khớp `apps.media.domain.roles.ROLES` — domain thuần không import
    Django nên enum nằm ở đây (users là tầng đáy, media import được users).
    """

    NONE = "", "Không có"
    VIEWER = "viewer", "Viewer"
    MARKETING = "marketing", "Marketing"
    UPLOADER = "uploader", "Uploader"
    ADMIN = "admin", "Admin"
```
Trong `class User`, ngay sau `zenify_email`:
```python
    # Module Media (spec 2026-09-04 §4.2). Có key riêng → bỏ qua media_role.
    media_role = models.CharField(max_length=16, choices=MediaRole.choices, blank=True, default="")
    media_api_key = EncryptedCharField(max_length=1024, blank=True, default="")
```
Thêm import `from apps.common.fields import EncryptedCharField` ở đầu file (kiểm tra chưa có).
Trong `VALID_SECTIONS`, sau `"audit",`:
```python
    # Module Media: menu hiện theo provisioning từng user (spec Media D5), KHÔNG có
    # trong PERMISSION_CATALOG. Vẫn phải có ở đây vì superuser nhận list(VALID_SECTIONS)
    # — thiếu là Administrator bị FE ẩn menu (cùng vết salaries/contact_flow).
    "media-contents",
    "media-proposals",
    "media-reports",
    "media-taxonomy",
    "media-settings",
```

- [ ] **Step 5: Migration**

`backend/apps/users/migrations/0046_user_media_fields.py`:
```python
import apps.common.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("users", "0045_user_zenify_email")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="media_role",
            field=models.CharField(
                blank=True,
                choices=[("", "Không có"), ("viewer", "Viewer"), ("marketing", "Marketing"), ("uploader", "Uploader"), ("admin", "Admin")],
                default="",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="media_api_key",
            field=apps.common.fields.EncryptedCharField(blank=True, default="", max_length=1024),
        ),
    ]
```

`backend/apps/media/migrations/__init__.py`: rỗng. `backend/apps/media/migrations/0001_initial.py`:
```python
import apps.common.fields
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ("users", "0046_user_media_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MediaConnection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("base_url", models.CharField(help_text="VD: https://media.example.vn (không kèm /api/v1)", max_length=255)),
                ("shared_api_key", apps.common.fields.EncryptedCharField(blank=True, default="", max_length=1024)),
                ("is_active", models.BooleanField(default=True)),
                ("last_check_at", models.DateTimeField(blank=True, null=True)),
                ("last_check_ok", models.BooleanField(blank=True, null=True)),
                ("last_check_error", models.TextField(blank=True, default="")),
                ("shared_key_role", models.CharField(blank=True, default="", max_length=32)),
                ("shared_key_username", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "media_connections"},
        ),
        migrations.CreateModel(
            name="MediaContentOwner",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("remote_id", models.BigIntegerField(unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "media_content_owners"},
        ),
        migrations.CreateModel(
            name="MediaProposalOwner",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("remote_id", models.BigIntegerField(unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "media_proposal_owners"},
        ),
    ]
```

- [ ] **Step 6: Đăng ký app, URL, importlinter, env**

`config/settings_parts/base.py`: thêm `"apps.media",` sau `"apps.daily_report",` trong `INSTALLED_APPS`. Cuối khối Foxia (gần dòng 720) thêm:
```python
# Module Media (ads-crm proxy) — spec 2026-09-04 §7. Host/key nằm trong DB.
MEDIA_HTTP_TIMEOUT = float(os.getenv("MEDIA_HTTP_TIMEOUT", "30"))
MEDIA_DOWNLOAD_TIMEOUT = float(os.getenv("MEDIA_DOWNLOAD_TIMEOUT", "120"))
MEDIA_TAXONOMY_CACHE_TTL = int(os.getenv("MEDIA_TAXONOMY_CACHE_TTL", "300"))
```
`config/urls.py`: sau dòng foxia thêm `re_path(r"^api/media/", include("apps.media.urls")),`. Tạo tạm `backend/apps/media/urls.py` với `urlpatterns: list = []` (Task 6 điền).
`.importlinter` dòng 41: `kiotviet | facebook_ads | google_ads | best_express | cnv_loyalty | foxia | media`.

- [ ] **Step 7: Kiểm tra migration khớp model rồi chạy test**

Run (cùng env block): `.../python manage.py makemigrations --check --dry-run media users` → Expected: `No changes detected`.
Run: `PYTEST apps/media/tests/test_models.py` → Expected: 4 passed.

- [ ] **Step 8: Commit**

```bash
git add backend/apps/media backend/apps/users/models.py backend/apps/users/migrations/0046_user_media_fields.py backend/config/settings_parts/base.py backend/config/urls.py backend/.importlinter
git commit -m "feat(media): app apps.media, MediaConnection + bảng owner, field media_role/media_api_key trên User"
```

---

### Task 2: Domain thuần — ma trận role + bảng lỗi

**Files:**
- Create: `backend/apps/media/domain/__init__.py`, `domain/roles.py`, `domain/errors.py`, `tests/test_domain_roles.py`, `tests/test_domain_errors.py`

**Interfaces:**
- Produces: `roles.ROLES`, `roles.Action` (Enum), `roles.Verdict` (Enum `ALLOW|DENY_ROLE|DENY_OWNER`), `roles.check(role: str, action: Action, *, is_owner: bool = False) -> Verdict`, `roles.is_owner_scoped(role, action) -> bool`; `errors.upstream_detail(code: str) -> str`.

- [ ] **Step 1: Test ma trận (tham số hoá toàn bộ §5.2)**

`backend/apps/media/tests/test_domain_roles.py`:
```python
"""Ma trận role Media phải khớp §3.3 tài liệu ads-crm (spec §5.2)."""

import pytest

from apps.media.domain.roles import ROLES, Action, Verdict, check
from apps.users.models import MediaRole

FULL = {a for a in Action}
VIEWER = {Action.READ, Action.PROPOSAL_CREATE, Action.PROPOSAL_EDIT}
MARKETING = VIEWER | {Action.CONTENT_EDIT_MARKETING, Action.EVALUATION_WRITE}
UPLOADER = VIEWER | {
    Action.CONTENT_CREATE, Action.CONTENT_EDIT_UPLOAD, Action.CONTENT_EDIT_MARKETING,
    Action.TAXONOMY_WRITE_SOURCES,
}
EXPECTED = {"viewer": VIEWER, "marketing": MARKETING, "uploader": UPLOADER, "admin": FULL}


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("action", list(Action))
def test_matrix_allows_exactly_expected_actions(role, action):
    verdict = check(role, action, is_owner=True)
    assert (verdict is Verdict.ALLOW) == (action in EXPECTED[role])


@pytest.mark.parametrize(
    "role,action",
    [
        ("uploader", Action.CONTENT_EDIT_UPLOAD),
        ("uploader", Action.CONTENT_EDIT_MARKETING),
        ("viewer", Action.PROPOSAL_EDIT),
        ("marketing", Action.PROPOSAL_EDIT),
        ("uploader", Action.PROPOSAL_EDIT),
    ],
)
def test_owner_scoped_actions_deny_non_owner(role, action):
    assert check(role, action, is_owner=False) is Verdict.DENY_OWNER
    assert check(role, action, is_owner=True) is Verdict.ALLOW


def test_admin_never_owner_scoped():
    assert check("admin", Action.PROPOSAL_EDIT, is_owner=False) is Verdict.ALLOW


def test_unknown_or_blank_role_denied():
    assert check("", Action.READ) is Verdict.DENY_ROLE
    assert check("boss", Action.READ) is Verdict.DENY_ROLE


def test_roles_mirror_user_enum():
    assert set(ROLES) == set(MediaRole.values) - {""}
```

- [ ] **Step 2: Test bảng lỗi**

`backend/apps/media/tests/test_domain_errors.py`:
```python
from apps.media.domain.errors import upstream_detail


def test_known_codes_translated():
    assert upstream_detail("invalid_url") == "URL không hợp lệ (cần scheme + host)."
    assert upstream_detail("busy") == "Nguồn tải đang bận, thử lại sau vài giây."
    assert upstream_detail("photo_post") == "Bài dạng ảnh, không có video để tải."


def test_unknown_code_falls_back_to_code_itself():
    assert upstream_detail("weird_thing") == "weird_thing"
```

- [ ] **Step 3: Chạy, xác nhận fail**

Run: `PYTEST apps/media/tests/test_domain_roles.py apps/media/tests/test_domain_errors.py` → FAIL `ModuleNotFoundError`.

- [ ] **Step 4: Implement `domain/roles.py`**

```python
"""Ma trận quyền Media — mirror §3.3 tài liệu ads-crm. Thuần Python, không Django.

Dùng khi request đi bằng KEY CHUNG: ads-crm chỉ thấy một tài khoản service nên
desi-order phải tự chặn. Với key riêng, ads-crm enforce — module này chỉ còn dùng
để frontend ẩn nút (qua /api/media/me).
"""

from __future__ import annotations

from enum import Enum

VIEWER = "viewer"
MARKETING = "marketing"
UPLOADER = "uploader"
ADMIN = "admin"
ROLES: tuple[str, ...] = (VIEWER, MARKETING, UPLOADER, ADMIN)


class Action(str, Enum):
    READ = "read"
    CONTENT_CREATE = "content.create"
    CONTENT_EDIT_UPLOAD = "content.edit_upload"
    CONTENT_EDIT_MARKETING = "content.edit_marketing"
    CONTENT_DELETE = "content.delete"
    VIDEO_CACHE_FORCE = "video.cache_force"
    EVALUATION_WRITE = "evaluation.write"
    TAXONOMY_WRITE = "taxonomy.write"
    TAXONOMY_WRITE_SOURCES = "taxonomy.write_sources"
    TAXONOMY_INCLUDE_INACTIVE = "taxonomy.include_inactive"
    PROPOSAL_CREATE = "proposal.create"
    PROPOSAL_EDIT = "proposal.edit"
    PROPOSAL_DELETE = "proposal.delete"


class Verdict(Enum):
    ALLOW = "allow"
    DENY_ROLE = "deny_role"
    DENY_OWNER = "deny_owner"


_VIEWER = frozenset({Action.READ, Action.PROPOSAL_CREATE, Action.PROPOSAL_EDIT})
ROLE_MATRIX: dict[str, frozenset[Action]] = {
    VIEWER: _VIEWER,
    MARKETING: _VIEWER | {Action.CONTENT_EDIT_MARKETING, Action.EVALUATION_WRITE},
    UPLOADER: _VIEWER
    | {
        Action.CONTENT_CREATE,
        Action.CONTENT_EDIT_UPLOAD,
        Action.CONTENT_EDIT_MARKETING,
        Action.TAXONOMY_WRITE_SOURCES,
    },
    ADMIN: frozenset(Action),
}

# (role, action) chỉ được phép trên bản ghi do chính user tạo. Admin không bao giờ bị giới hạn.
OWNER_SCOPED: frozenset[tuple[str, Action]] = frozenset(
    {
        (UPLOADER, Action.CONTENT_EDIT_UPLOAD),
        (UPLOADER, Action.CONTENT_EDIT_MARKETING),
        (VIEWER, Action.PROPOSAL_EDIT),
        (MARKETING, Action.PROPOSAL_EDIT),
        (UPLOADER, Action.PROPOSAL_EDIT),
    }
)


def is_owner_scoped(role: str, action: Action) -> bool:
    return (role, action) in OWNER_SCOPED


def check(role: str, action: Action, *, is_owner: bool = False) -> Verdict:
    allowed = ROLE_MATRIX.get(role)
    if allowed is None or action not in allowed:
        return Verdict.DENY_ROLE
    if is_owner_scoped(role, action) and not is_owner:
        return Verdict.DENY_OWNER
    return Verdict.ALLOW
```

- [ ] **Step 5: Implement `domain/errors.py`**

```python
"""Dịch mã lỗi ads-crm (§5 tài liệu) sang thông báo tiếng Việt cho toast frontend."""

from __future__ import annotations

UPSTREAM_MESSAGES: dict[str, str] = {
    "invalid_json": "Dữ liệu gửi lên không đúng định dạng JSON.",
    "missing_required_fields": "Thiếu trường bắt buộc.",
    "invalid_uploaded_at": "Ngày upload không hợp lệ.",
    "invalid_due_date": "Hạn hoàn thành không hợp lệ.",
    "invalid_expires_at": "Ngày hết hạn không hợp lệ.",
    "invalid_url": "URL không hợp lệ (cần scheme + host).",
    "invalid_rating": "Điểm đánh giá phải từ 0 đến 5, bước 0.5.",
    "invalid_evaluation": "Đánh giá trống: cần điểm hoặc ghi chú.",
    "invalid_status": "Trạng thái không hợp lệ.",
    "invalid_role": "Vai trò không hợp lệ.",
    "invalid_content_type": "Loại nội dung không hợp lệ.",
    "invalid_product": "Sản phẩm không hợp lệ hoặc đã tắt.",
    "invalid_category": "Danh mục không hợp lệ.",
    "invalid_channel": "Kênh ads không hợp lệ.",
    "invalid_source": "Nguồn không hợp lệ.",
    "product_not_found": "Sản phẩm không tồn tại.",
    "category_not_found": "Danh mục không tồn tại.",
    "duplicate_record": "Bản ghi đã tồn tại (trùng tên/mã).",
    "forbidden": "Tài khoản ads-crm không đủ quyền cho thao tác này.",
    "not_content_owner": "Bạn không phải người tạo nội dung này.",
    "not_proposal_owner": "Bạn không phải người tạo đề xuất này.",
    "content_not_found": "Nội dung không tồn tại hoặc đã xoá.",
    "evaluation_not_found": "Đánh giá không tồn tại.",
    "proposal_not_found": "Đề xuất không tồn tại.",
    "taxonomy_not_found": "Bản ghi danh mục không tồn tại.",
    "photo_post": "Bài dạng ảnh, không có video để tải.",
    "rate_limited": "Hệ thống Media đang giới hạn tốc độ, thử lại sau.",
    "download_failed": "Không tải được video từ nguồn.",
    "busy": "Nguồn tải đang bận, thử lại sau vài giây.",
    "video_cache_not_configured": "ads-crm chưa cấu hình kho cache video.",
    "video_queue_full": "Hàng đợi cache video đang đầy, thử lại sau.",
    "query_failed": "Lỗi phía ads-crm (query_failed).",
    "internal_error": "Lỗi phía ads-crm (internal_error).",
}


def upstream_detail(code: str) -> str:
    return UPSTREAM_MESSAGES.get(code, code)
```

`domain/__init__.py`: rỗng.

- [ ] **Step 6: Chạy test → pass, commit**

Run: `PYTEST apps/media/tests/test_domain_roles.py apps/media/tests/test_domain_errors.py` → all passed.
```bash
git add backend/apps/media/domain backend/apps/media/tests/test_domain_*.py
git commit -m "feat(media): domain ma trận role + bảng dịch lỗi ads-crm"
```

---

### Task 3: Exceptions + `AdsCrmClient`

**Files:**
- Create: `backend/apps/media/exceptions.py`, `infrastructure/__init__.py`, `infrastructure/ads_crm_client.py`, `tests/test_ads_crm_client.py`

**Interfaces:**
- Produces: exceptions `MediaError(status:int, code:str, detail:str)` và các lớp con `MediaDisabled`, `MediaStagingBlocked`, `MediaNotProvisioned`, `MediaForbidden`, `MediaNotOwner`, `AdsCrmUnavailable`, `AdsCrmApiError(code, status)`; `AdsCrmClient(base_url, api_key, *, timeout=None, reader=None, writer=None)` với `json_request(method, path, *, params=None, json=None) -> tuple[int, Any]`, `stream_get(path, *, timeout) -> requests.Response`, `me() -> dict`.

- [ ] **Step 1: Test client**

`backend/apps/media/tests/test_ads_crm_client.py`:
```python
"""AdsCrmClient: header, params lặp, parse {"error"}, 401 → key_rejected, staging guard."""

from unittest.mock import MagicMock

import pytest
import requests

from apps.media.exceptions import AdsCrmApiError, AdsCrmUnavailable, MediaStagingBlocked
from apps.media.infrastructure.ads_crm_client import AdsCrmClient


def _resp(status=200, body=None, text=""):
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.text = text
    r.headers = {"Content-Type": "application/json"}
    r.json.return_value = body if body is not None else {}
    return r


def _client(reader=None, writer=None):
    return AdsCrmClient("https://media.example/", "dak_abc", reader=reader or MagicMock(), writer=writer or MagicMock())


def test_get_sends_api_key_request_id_and_repeated_params():
    reader = MagicMock()
    reader.request.return_value = _resp(200, {"items": [], "total": 0})
    status, data = _client(reader=reader).json_request(
        "GET", "/contents", params=[("product_id", "1"), ("product_id", "2")]
    )
    assert (status, data["total"]) == (200, 0)
    kwargs = reader.request.call_args.kwargs
    assert reader.request.call_args.args[:2] == ("GET", "https://media.example/api/v1/contents")
    assert kwargs["headers"]["X-API-Key"] == "dak_abc"
    assert kwargs["headers"]["X-Request-ID"]
    assert kwargs["params"] == [("product_id", "1"), ("product_id", "2")]


def test_mutations_go_through_writer_not_reader():
    reader, writer = MagicMock(), MagicMock()
    writer.request.return_value = _resp(201, {"id": 5})
    status, _ = _client(reader, writer).json_request("POST", "/contents", json={"title": "x"})
    assert status == 201
    reader.request.assert_not_called()
    assert writer.request.call_args.kwargs["json"] == {"title": "x"}


def test_error_body_raises_api_error_with_code_and_status():
    reader = MagicMock()
    reader.request.return_value = _resp(400, {"error": "invalid_url"})
    with pytest.raises(AdsCrmApiError) as exc:
        _client(reader).json_request("GET", "/contents/1")
    assert (exc.value.code, exc.value.status) == ("invalid_url", 400)
    assert exc.value.detail == "URL không hợp lệ (cần scheme + host)."


def test_401_invalid_token_becomes_502_key_rejected():
    """Không được trả 401 về frontend — interceptor sẽ logout phiên desi-order."""
    reader = MagicMock()
    reader.request.return_value = _resp(401, {"error": "invalid_token"})
    with pytest.raises(AdsCrmApiError) as exc:
        _client(reader).json_request("GET", "/auth/me")
    assert (exc.value.status, exc.value.code) == (502, "media_key_rejected")


def test_network_error_becomes_unavailable():
    reader = MagicMock()
    reader.request.side_effect = requests.ConnectionError("boom")
    with pytest.raises(AdsCrmUnavailable):
        _client(reader).json_request("GET", "/contents")


def test_non_json_error_body_becomes_unavailable():
    reader = MagicMock()
    r = _resp(502, text="<html>bad gateway</html>")
    r.json.side_effect = ValueError("no json")
    reader.request.return_value = r
    with pytest.raises(AdsCrmUnavailable):
        _client(reader).json_request("GET", "/contents")


def test_staging_blocks_before_network(settings):
    settings.IS_STAGING = True
    reader = MagicMock()
    with pytest.raises(MediaStagingBlocked):
        _client(reader).json_request("GET", "/contents")
    reader.request.assert_not_called()


def test_stream_get_passes_stream_and_timeout():
    reader = MagicMock()
    reader.request.return_value = _resp(200)
    _client(reader).stream_get("/contents/1/download", timeout=120)
    kwargs = reader.request.call_args.kwargs
    assert kwargs["stream"] is True and kwargs["timeout"] == 120
```

- [ ] **Step 2: Chạy → fail** (`PYTEST apps/media/tests/test_ads_crm_client.py`, ModuleNotFoundError).

- [ ] **Step 3: `exceptions.py`**

```python
"""Cây lỗi module Media. Mỗi lỗi mang sẵn (status, code, detail) để transport trả
`{"detail", "code"}` mà không phải map lại (spec §4.6)."""

from __future__ import annotations

from apps.media.domain.errors import upstream_detail


class MediaError(Exception):
    status: int = 500
    code: str = "media_error"

    def __init__(self, detail: str = "", *, status: int | None = None, code: str | None = None):
        self.detail = detail or self.default_detail()
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code
        super().__init__(self.detail)

    def default_detail(self) -> str:
        return "Lỗi module Media."


class MediaDisabled(MediaError):
    status, code = 503, "media_disabled"

    def default_detail(self) -> str:
        return "Chưa cấu hình kết nối Media hoặc kết nối đang tắt."


class MediaStagingBlocked(MediaError):
    status, code = 503, "media_staging_blocked"

    def default_detail(self) -> str:
        return "Media outbound bị chặn trên staging (APP_ENV=staging)."


class MediaNotProvisioned(MediaError):
    status, code = 403, "media_not_provisioned"

    def default_detail(self) -> str:
        return "Bạn chưa được cấp quyền Media. Nhờ admin gán vai trò Media hoặc nhập API key riêng."


class MediaForbidden(MediaError):
    status, code = 403, "media_forbidden"

    def default_detail(self) -> str:
        return "Vai trò Media của bạn không đủ quyền cho thao tác này."


class MediaNotOwner(MediaError):
    status, code = 403, "media_not_owner"

    def default_detail(self) -> str:
        return "Chỉ người tạo (hoặc admin Media) mới sửa được bản ghi này."


class AdsCrmUnavailable(MediaError):
    status, code = 502, "media_upstream_unavailable"

    def default_detail(self) -> str:
        return "Hệ thống Media (ads-crm) không phản hồi."


class AdsCrmApiError(MediaError):
    """Lỗi nghiệp vụ ads-crm trả về dạng {"error": code}. Giữ nguyên HTTP status,
    TRỪ 401: đổi thành 502 media_key_rejected để frontend không logout phiên desi-order."""

    def __init__(self, code: str, status: int):
        if status == 401:
            super().__init__(
                "API key Media bị ads-crm từ chối (sai/hết hạn/tài khoản khoá). Nhờ admin cập nhật key.",
                status=502,
                code="media_key_rejected",
            )
        else:
            super().__init__(upstream_detail(code), status=status, code=code)
```

- [ ] **Step 4: `infrastructure/ads_crm_client.py`**

```python
"""HTTP client sang ads-crm `/api/v1` (tài liệu "Tích hợp API đối ngoại").

Hai HttpClient: `reader` có retry (429/5xx là transient — tài liệu khuyên backoff),
`writer` retries=0 vì POST/PATCH/DELETE không idempotent (xem apps.common.http_client).
"""

from __future__ import annotations

import uuid
from typing import Any

import requests
from django.conf import settings

from apps.common.http_client import HttpClient
from apps.media.exceptions import AdsCrmApiError, AdsCrmUnavailable, MediaStagingBlocked

API_PREFIX = "/api/v1"
_READ_METHODS = frozenset({"GET", "HEAD"})


class AdsCrmClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float | None = None,
        reader: HttpClient | None = None,
        writer: HttpClient | None = None,
    ):
        self._base = base_url.rstrip("/") + API_PREFIX
        self._api_key = api_key
        timeout = timeout or float(getattr(settings, "MEDIA_HTTP_TIMEOUT", 30.0))
        self._reader = reader or HttpClient(timeout=timeout, retries=3)
        self._writer = writer or HttpClient(timeout=timeout, retries=0)

    # ── vận chuyển ──────────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self._api_key,
            "Accept": "application/json",
            "X-Request-ID": uuid.uuid4().hex,
        }

    def _send(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        if getattr(settings, "IS_STAGING", False):
            raise MediaStagingBlocked()
        http = self._reader if method in _READ_METHODS else self._writer
        url = f"{self._base}/{path.lstrip('/')}"
        try:
            return http.request(method, url, headers=self._headers(), **kwargs)
        except requests.RequestException as exc:
            raise AdsCrmUnavailable(f"ads-crm không phản hồi: {exc}") from exc

    def _raise_for_error(self, response: requests.Response) -> None:
        if response.status_code < 400:
            return
        try:
            body = response.json()
        except ValueError as exc:
            raise AdsCrmUnavailable(
                f"ads-crm trả HTTP {response.status_code} không phải JSON: {response.text[:200]}"
            ) from exc
        code = body.get("error") if isinstance(body, dict) else None
        raise AdsCrmApiError(str(code or "upstream_error"), response.status_code)

    # ── API ─────────────────────────────────────────────────────────────────
    def json_request(
        self,
        method: str,
        path: str,
        *,
        params: list[tuple[str, str]] | dict[str, str] | None = None,
        json: Any = None,
    ) -> tuple[int, Any]:
        response = self._send(method, path, params=params, json=json)
        self._raise_for_error(response)
        if response.status_code == 204 or not response.text:
            return response.status_code, None
        try:
            return response.status_code, response.json()
        except ValueError as exc:
            raise AdsCrmUnavailable("ads-crm trả body không phải JSON.") from exc

    def stream_get(self, path: str, *, timeout: float) -> requests.Response:
        response = self._send("GET", path, stream=True, timeout=timeout)
        self._raise_for_error(response)
        return response

    def me(self) -> dict[str, Any]:
        _, data = self.json_request("GET", "/auth/me")
        return data if isinstance(data, dict) else {}
```

`infrastructure/__init__.py`: rỗng.

- [ ] **Step 5: Chạy → pass, commit**

Run: `PYTEST apps/media/tests/test_ads_crm_client.py` → 8 passed.
```bash
git add backend/apps/media/exceptions.py backend/apps/media/infrastructure backend/apps/media/tests/test_ads_crm_client.py
git commit -m "feat(media): AdsCrmClient + cây exception (401 upstream → 502 key_rejected)"
```

---

### Task 4: Credential resolver + access policy

**Files:**
- Create: `backend/apps/media/contracts/__init__.py`, `contracts/dtos.py`, `application/__init__.py`, `application/services/__init__.py`, `application/services/credential_resolver.py`, `application/services/access_policy.py`, `tests/test_credential_resolver.py`, `tests/test_access_policy.py`

**Interfaces:**
- Consumes: `MediaConnection`, `User.media_api_key/media_role`, `roles.check`.
- Produces: `Credential(kind: "personal"|"shared", api_key: str, role: str|None, base_url: str)`; `credential_resolver.resolve(user) -> Credential`; `access_policy.authorize(cred, action, *, owner_check: Callable[[], bool] | None = None) -> None` (raise `MediaForbidden`/`MediaNotOwner`; personal → no-op).

- [ ] **Step 1: Test resolver**

`backend/apps/media/tests/test_credential_resolver.py`:
```python
import pytest

from apps.media.application.services.credential_resolver import resolve
from apps.media.exceptions import MediaDisabled, MediaNotProvisioned
from apps.media.models import MediaConnection
from apps.users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def conn():
    return MediaConnection.objects.create(base_url="https://media.example/", shared_api_key="dak_shared")


def test_personal_key_wins_over_role(conn):
    u = User.objects.create_user(username="a", password="x", media_api_key="dak_me", media_role="viewer")
    cred = resolve(u)
    assert (cred.kind, cred.api_key, cred.role, cred.base_url) == ("personal", "dak_me", None, "https://media.example")


def test_shared_key_with_role(conn):
    u = User.objects.create_user(username="b", password="x", media_role="uploader")
    cred = resolve(u)
    assert (cred.kind, cred.api_key, cred.role) == ("shared", "dak_shared", "uploader")


def test_not_provisioned(conn):
    u = User.objects.create_user(username="c", password="x")
    with pytest.raises(MediaNotProvisioned):
        resolve(u)


def test_disabled_when_no_active_connection():
    u = User.objects.create_user(username="d", password="x", media_api_key="dak_me")
    with pytest.raises(MediaDisabled):
        resolve(u)


def test_shared_mode_requires_shared_key_configured():
    MediaConnection.objects.create(base_url="https://media.example", shared_api_key="")
    u = User.objects.create_user(username="e", password="x", media_role="admin")
    with pytest.raises(MediaDisabled):
        resolve(u)
```

- [ ] **Step 2: Test policy**

`backend/apps/media/tests/test_access_policy.py`:
```python
import pytest

from apps.media.application.services.access_policy import authorize
from apps.media.contracts.dtos import Credential
from apps.media.domain.roles import Action
from apps.media.exceptions import MediaForbidden, MediaNotOwner

BASE = "https://media.example"


def test_personal_credential_is_never_checked_locally():
    cred = Credential(kind="personal", api_key="dak_me", role=None, base_url=BASE)
    authorize(cred, Action.CONTENT_DELETE)  # không raise — ads-crm enforce


def test_shared_role_denied_action_raises_forbidden():
    cred = Credential(kind="shared", api_key="dak_s", role="viewer", base_url=BASE)
    with pytest.raises(MediaForbidden):
        authorize(cred, Action.EVALUATION_WRITE)


def test_shared_owner_scoped_uses_owner_check_lazily():
    cred = Credential(kind="shared", api_key="dak_s", role="uploader", base_url=BASE)
    calls = []

    def owner_check():
        calls.append(1)
        return False

    with pytest.raises(MediaNotOwner):
        authorize(cred, Action.CONTENT_EDIT_UPLOAD, owner_check=owner_check)
    assert calls == [1]
    # Admin: không gọi owner_check.
    admin = Credential(kind="shared", api_key="dak_s", role="admin", base_url=BASE)
    authorize(admin, Action.CONTENT_EDIT_UPLOAD, owner_check=lambda: (_ for _ in ()).throw(AssertionError()))


def test_owner_scoped_without_check_is_denied():
    cred = Credential(kind="shared", api_key="dak_s", role="uploader", base_url=BASE)
    with pytest.raises(MediaNotOwner):
        authorize(cred, Action.CONTENT_EDIT_UPLOAD)
```

- [ ] **Step 3: Chạy → fail** (ModuleNotFoundError).

- [ ] **Step 4: Implement**

`contracts/dtos.py`:
```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CredentialKind = Literal["personal", "shared"]


@dataclass(frozen=True)
class Credential:
    """Danh tính dùng để nói chuyện với ads-crm cho MỘT request (spec §4.4)."""

    kind: CredentialKind
    api_key: str
    role: str | None  # chỉ có ở kind="shared"
    base_url: str
```

`application/services/credential_resolver.py`:
```python
"""user → Credential theo thứ tự D3: key riêng → key chung + vai trò → từ chối."""

from __future__ import annotations

from apps.media.contracts.dtos import Credential
from apps.media.exceptions import MediaDisabled, MediaNotProvisioned
from apps.media.models import MediaConnection


def resolve(user) -> Credential:
    conn = MediaConnection.get_active()
    if conn is None:
        raise MediaDisabled()
    base_url = conn.resolve_base_url()
    personal = (getattr(user, "media_api_key", "") or "").strip()
    if personal:
        return Credential(kind="personal", api_key=personal, role=None, base_url=base_url)
    role = (getattr(user, "media_role", "") or "").strip()
    if role:
        shared = (conn.shared_api_key or "").strip()
        if not shared:
            raise MediaDisabled("Chưa nhập API key chung cho kết nối Media.")
        return Credential(kind="shared", api_key=shared, role=role, base_url=base_url)
    raise MediaNotProvisioned()


def is_provisioned(user) -> bool:
    """Dùng cho allowed_sections: không cần connection, chỉ cần user có key/role."""
    return bool((getattr(user, "media_api_key", "") or "").strip() or (getattr(user, "media_role", "") or "").strip())
```

`application/services/access_policy.py`:
```python
"""Enforce ma trận role Media khi dùng key chung (spec §5.1 lớp 2)."""

from __future__ import annotations

from collections.abc import Callable

from apps.media.contracts.dtos import Credential
from apps.media.domain.roles import Action, Verdict, check, is_owner_scoped
from apps.media.exceptions import MediaForbidden, MediaNotOwner


def authorize(
    cred: Credential,
    action: Action,
    *,
    owner_check: Callable[[], bool] | None = None,
) -> None:
    if cred.kind == "personal":
        return  # ads-crm tự phân quyền theo role của key riêng
    role = cred.role or ""
    needs_owner = is_owner_scoped(role, action)
    is_owner = bool(owner_check()) if (needs_owner and owner_check is not None) else False
    verdict = check(role, action, is_owner=is_owner)
    if verdict is Verdict.DENY_ROLE:
        raise MediaForbidden()
    if verdict is Verdict.DENY_OWNER:
        raise MediaNotOwner()
```

Các `__init__.py` còn lại: rỗng.

- [ ] **Step 5: Chạy → pass, commit**

Run: `PYTEST apps/media/tests/test_credential_resolver.py apps/media/tests/test_access_policy.py` → 9 passed.
```bash
git add backend/apps/media/contracts backend/apps/media/application backend/apps/media/tests/test_credential_resolver.py backend/apps/media/tests/test_access_policy.py
git commit -m "feat(media): credential resolver (key riêng → key chung) + access policy"
```

---

### Task 5: `users` — serializer/admin view cho `media_role`, `media_api_key`, `allowed_sections`

**Files:**
- Modify: `backend/apps/users/serializers.py` (UserSerializer.Meta.fields, `get_allowed_sections`, AdminUserCreateSerializer, AdminUserUpdateSerializer), `backend/apps/users/transport/views/users_views_impl/admin_user_views.py:119-126`, `backend/apps/users/services.py:59-76`
- Test: `backend/apps/users/tests/test_admin_api.py` (thêm class mới)

**Interfaces:**
- Consumes: `credential_resolver.is_provisioned(user)`.
- Produces: JSON user có `media_role`, `media_api_key_set: bool`, `media_api_key_prefix: str`; PATCH/POST nhận `media_role`, `media_api_key` (write-only: `""` = giữ nguyên ở PATCH, `null` = xoá); `allowed_sections` chứa 4 section Media khi provisioned.

- [ ] **Step 1: Test**

Thêm vào cuối `backend/apps/users/tests/test_admin_api.py`:
```python
class AdminUsersMediaFieldsTests(TestCase):
    """Vai trò Media + API key Media riêng (spec Media §4.7)."""

    MEDIA_SECTIONS = {"media-contents", "media-proposals", "media-reports", "media-taxonomy"}

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("adm")
        self.client.force_authenticate(user=self.admin)

    def test_create_with_role_and_key_never_echoes_key(self):
        resp = self.client.post(
            "/api/users/admin",
            {"username": "m1", "password": "secret-pass-123", "media_role": "uploader",
             "media_api_key": "dak_abcdefghijklmnop"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertEqual(body["media_role"], "uploader")
        self.assertTrue(body["media_api_key_set"])
        self.assertEqual(body["media_api_key_prefix"], "dak_abcdefgh")
        self.assertNotIn("media_api_key", body)
        self.assertEqual(User.objects.get(username="m1").media_api_key, "dak_abcdefghijklmnop")
        self.assertTrue(self.MEDIA_SECTIONS <= set(body["allowed_sections"]))

    def test_create_rejects_key_without_dak_prefix_and_bad_role(self):
        resp = self.client.post(
            "/api/users/admin",
            {"username": "m2", "password": "secret-pass-123", "media_api_key": "abc"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("media_api_key", resp.json())
        resp = self.client.post(
            "/api/users/admin",
            {"username": "m3", "password": "secret-pass-123", "media_role": "boss"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("media_role", resp.json())

    def test_patch_blank_keeps_key_null_clears_key(self):
        user = User.objects.create(username="m4", media_api_key="dak_keepme12345", media_role="viewer")
        resp = self.client.patch(f"/api/users/admin/{user.id}", {"media_api_key": ""}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        user.refresh_from_db()
        self.assertEqual(user.media_api_key, "dak_keepme12345")

        resp = self.client.patch(f"/api/users/admin/{user.id}", {"media_api_key": None}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        user.refresh_from_db()
        self.assertEqual(user.media_api_key, "")
        self.assertFalse(resp.json()["media_api_key_set"])
        # Vẫn provisioned nhờ media_role.
        self.assertTrue(self.MEDIA_SECTIONS <= set(resp.json()["allowed_sections"]))

        resp = self.client.patch(f"/api/users/admin/{user.id}", {"media_role": ""}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.MEDIA_SECTIONS & set(resp.json()["allowed_sections"]))

    def test_unprovisioned_user_has_no_media_sections(self):
        staff = make_user_with_permissions("s1", ["orders:view"])
        self.assertFalse(self.MEDIA_SECTIONS & set(UserSerializer(staff).data["allowed_sections"]))
```
Thêm import `from apps.users.serializers import UserSerializer` ở đầu file test.

- [ ] **Step 2: Chạy → fail**

Run: `PYTEST apps/users/tests/test_admin_api.py -k Media` → FAIL (KeyError `media_role`).

- [ ] **Step 3: Serializer**

Trong `UserSerializer`:
- Thêm khai báo field: `media_api_key_set = serializers.SerializerMethodField()`, `media_api_key_prefix = serializers.SerializerMethodField()`.
- `Meta.fields`: sau `"zenify_email",` thêm `"media_role", "media_api_key_set", "media_api_key_prefix",`.
- Method:
```python
    def get_media_api_key_set(self, obj) -> bool:
        return bool((obj.media_api_key or "").strip())

    def get_media_api_key_prefix(self, obj) -> str:
        key = (obj.media_api_key or "").strip()
        return key[:12] if key else ""

    def get_allowed_sections(self, obj) -> list[str]:
        from apps.media.application.services.credential_resolver import is_provisioned

        eff = resolve_effective_permissions(obj)
        sections = set(VALID_SECTIONS) if eff.is_superuser else {
            code.split(":", 1)[0] for code in eff.permission_codes
        }
        # Module Media hiện theo provisioning từng user, không qua role (spec Media D5).
        # Superuser cũng cần provisioning mới gọi được ads-crm, nhưng menu đã có sẵn
        # qua VALID_SECTIONS — trang sẽ hiện "chưa được cấp quyền".
        if is_provisioned(obj):
            sections |= MEDIA_CONTENT_SECTIONS
        return sorted(sections)
```
Hằng ở đầu module: `MEDIA_CONTENT_SECTIONS = frozenset({"media-contents", "media-proposals", "media-reports", "media-taxonomy"})`.

> Lưu ý import: `users` (tầng đáy) không được import `apps.media` ở mức module — import cục bộ trong hàm để import-linter không tính (nếu lint vẫn báo, chuyển `is_provisioned` thành hàm nội bộ trong `serializers.py` với cùng logic; ưu tiên cách này nếu CI lint đỏ).

Hàm validate chung, đặt trên hai serializer admin:
```python
def _validate_media_api_key(value):
    if value is None or value == "":
        return value
    value = value.strip()
    if not value.startswith("dak_") or len(value) < 12:
        raise serializers.ValidationError("API key Media phải bắt đầu bằng 'dak_'.")
    return value
```
`AdminUserCreateSerializer` thêm:
```python
    media_role = serializers.ChoiceField(choices=MediaRole.choices, required=False, default="")
    media_api_key = serializers.CharField(max_length=1024, allow_blank=True, allow_null=True, required=False, default="")

    def validate_media_api_key(self, value):
        return _validate_media_api_key(value) or ""
```
`AdminUserUpdateSerializer` thêm:
```python
    media_role = serializers.ChoiceField(choices=MediaRole.choices, required=False)
    media_api_key = serializers.CharField(max_length=1024, allow_blank=True, allow_null=True, required=False)

    def validate_media_api_key(self, value):
        return _validate_media_api_key(value)
```
Import `MediaRole` từ `apps.users.models`.

- [ ] **Step 4: View PATCH + service create**

`admin_user_views.py`, sau khối `zenify_email`:
```python
    if "media_role" in serializer.validated_data:
        user.media_role = serializer.validated_data["media_role"]
    if "media_api_key" in serializer.validated_data:
        # "" = giữ nguyên (ô password để trống), None = xoá key (spec §4.7).
        new_key = serializer.validated_data["media_api_key"]
        if new_key is None:
            user.media_api_key = ""
        elif new_key:
            user.media_api_key = new_key
```
`update_fields` thêm `"media_role", "media_api_key"`.

`services.py` `create_admin_user`: `User(**data)` đã nhận `media_role`/`media_api_key` vì chúng nằm trong `validated` — chỉ cần đảm bảo `media_api_key` None → "" : thêm trước `User(**data)`:
```python
        if data.get("media_api_key") is None:
            data["media_api_key"] = ""
```

- [ ] **Step 5: Chạy → pass; chạy cả file cũ**

Run: `PYTEST apps/users/tests/test_admin_api.py` → all passed (kể cả test zenify cũ).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/users/serializers.py backend/apps/users/services.py backend/apps/users/transport/views/users_views_impl/admin_user_views.py backend/apps/users/tests/test_admin_api.py
git commit -m "feat(users): media_role + media_api_key (write-only) trên admin user API; allowed_sections thêm Media khi provisioned"
```

---

### Task 6: Proxy service + view đọc + URL allowlist

**Files:**
- Create: `backend/apps/media/application/services/proxy_service.py`, `transport/__init__.py`, `transport/errors.py`, `transport/params.py`, `transport/api_views.py`, `tests/conftest.py`, `tests/test_read_views.py`
- Modify: `backend/apps/media/urls.py`

**Interfaces:**
- Consumes: `resolve`, `authorize`, `AdsCrmClient`, `MediaContentOwner`, `MediaProposalOwner`.
- Produces: `MediaProxy(user)` với `.cred`, `.client`, `.forward(method, path, *, action, params=None, json=None, owner_check=None) -> tuple[int, Any]`, `.taxonomy(group, include_inactive) -> Any`, `.invalidate_taxonomy(group)`, `.me() -> dict`, `.owns_content(remote_id) -> bool`, `.owns_proposal(remote_id) -> bool`, `.record_content_owner(remote_id)`, `.record_proposal_owner(remote_id)`; decorator `media_errors`; `params.pick(query_params, allowed) -> list[tuple[str,str]]`; hằng `TAXONOMY_GROUPS`.
- Route đọc: `GET me`, `GET contents`, `GET contents/<id>`, `GET contents/<id>/ad-evaluations`, `GET <group>`, `GET proposals`, `GET proposals/<id>`, `GET reports/overview|ratings-by-channel|content-quality`.

- [ ] **Step 1: Fixture chung**

`backend/apps/media/tests/conftest.py`:
```python
"""Fixture Media: connection sẵn, user 3 kiểu, client ads-crm giả (patch tại proxy_service)."""

from unittest.mock import MagicMock

import pytest
from rest_framework.test import APIClient

from apps.media.exceptions import AdsCrmApiError
from apps.media.models import MediaConnection
from apps.users.models import User


@pytest.fixture
def connection(db):
    return MediaConnection.objects.create(base_url="https://media.example", shared_api_key="dak_shared_key_123")


@pytest.fixture
def fake_client(monkeypatch):
    """MagicMock thay AdsCrmClient. `fake_client.json_request.return_value = (200, {...})`."""
    client = MagicMock()
    client.json_request.return_value = (200, {"items": [], "total": 0, "page": 1, "page_size": 20})
    client.me.return_value = {"id": 7, "username": "svc", "name": "Service", "role": "admin"}
    monkeypatch.setattr(
        "apps.media.application.services.proxy_service.AdsCrmClient",
        lambda base_url, api_key, **kw: client,
    )
    return client


def _user(username, **fields):
    return User.objects.create_user(username=username, password="x", **fields)


@pytest.fixture
def personal_user(db):
    return _user("personal", media_api_key="dak_personal_key_1")


@pytest.fixture
def shared_viewer(db):
    return _user("viewer", media_role="viewer")


@pytest.fixture
def shared_uploader(db):
    return _user("uploader", media_role="uploader")


@pytest.fixture
def shared_marketing(db):
    return _user("marketing", media_role="marketing")


@pytest.fixture
def shared_admin(db):
    return _user("madmin", media_role="admin")


@pytest.fixture
def unprovisioned(db):
    return _user("nobody")


@pytest.fixture
def api():
    def _as(user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    return _as


@pytest.fixture
def upstream_error():
    return lambda code, status: AdsCrmApiError(code, status)
```

- [ ] **Step 2: Test view đọc**

`backend/apps/media/tests/test_read_views.py`:
```python
import pytest
from django.core.cache import cache

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def test_me_unprovisioned_returns_200_provisioned_false(api, unprovisioned, connection):
    r = api(unprovisioned).get("/api/media/me")
    assert r.status_code == 200
    assert r.data["provisioned"] is False
    assert r.data["public_base_url"] == "https://media.example"


def test_me_shared_reports_role_without_calling_upstream(api, shared_uploader, connection, fake_client):
    r = api(shared_uploader).get("/api/media/me")
    assert r.status_code == 200
    assert r.data == {
        "provisioned": True, "mode": "shared", "role": "uploader", "remote_user": None,
        "public_base_url": "https://media.example", "connection_ok": True,
    }
    fake_client.me.assert_not_called()


def test_me_personal_reads_role_from_upstream_and_caches(api, personal_user, connection, fake_client):
    fake_client.me.return_value = {"id": 9, "username": "hung", "name": "Hùng", "role": "marketing"}
    r1 = api(personal_user).get("/api/media/me")
    r2 = api(personal_user).get("/api/media/me")
    assert r1.data["mode"] == "personal" and r1.data["role"] == "marketing"
    assert r1.data["remote_user"] == {"id": 9, "username": "hung", "name": "Hùng"}
    assert r2.status_code == 200
    assert fake_client.me.call_count == 1


def test_me_personal_key_rejected_gives_connection_false_not_401(api, personal_user, connection, fake_client, upstream_error):
    fake_client.me.side_effect = upstream_error("invalid_token", 401)
    r = api(personal_user).get("/api/media/me")
    assert r.status_code == 200
    assert r.data["connection_ok"] is False and r.data["role"] is None


def test_contents_list_passes_only_allowlisted_repeated_params(api, shared_viewer, connection, fake_client):
    r = api(shared_viewer).get("/api/media/contents?product_id=1&product_id=2&page=2&evil=1&sort=title")
    assert r.status_code == 200
    args, kwargs = fake_client.json_request.call_args
    assert args == ("GET", "/contents")
    assert sorted(kwargs["params"]) == [("page", "2"), ("product_id", "1"), ("product_id", "2"), ("sort", "title")]


def test_unprovisioned_gets_403_media_not_provisioned(api, unprovisioned, connection):
    r = api(unprovisioned).get("/api/media/contents")
    assert r.status_code == 403
    assert r.data == {"detail": "Bạn chưa được cấp quyền Media. Nhờ admin gán vai trò Media hoặc nhập API key riêng.", "code": "media_not_provisioned"}


def test_no_connection_gives_503(api, shared_viewer):
    r = api(shared_viewer).get("/api/media/contents")
    assert (r.status_code, r.data["code"]) == (503, "media_disabled")


def test_upstream_error_status_and_code_passthrough(api, shared_viewer, connection, fake_client, upstream_error):
    fake_client.json_request.side_effect = upstream_error("content_not_found", 404)
    r = api(shared_viewer).get("/api/media/contents/99")
    assert r.status_code == 404
    assert r.data == {"detail": "Nội dung không tồn tại hoặc đã xoá.", "code": "content_not_found"}


def test_taxonomy_cached_per_group_and_flag(api, shared_viewer, shared_admin, connection, fake_client):
    fake_client.json_request.return_value = (200, {"items": [{"id": 1, "name": "TikTok"}]})
    api(shared_viewer).get("/api/media/content-sources")
    api(shared_viewer).get("/api/media/content-sources")
    assert fake_client.json_request.call_count == 1
    # include_inactive: chỉ admin mới được truyền; viewer bị lược bỏ → dùng chung cache.
    api(shared_viewer).get("/api/media/content-sources?include_inactive=true")
    assert fake_client.json_request.call_count == 1
    api(shared_admin).get("/api/media/content-sources?include_inactive=true")
    assert fake_client.json_request.call_count == 2
    assert fake_client.json_request.call_args.kwargs["params"] == [("include_inactive", "true")]


def test_unknown_taxonomy_group_is_404(api, shared_viewer, connection, fake_client):
    assert api(shared_viewer).get("/api/media/users").status_code == 404
    assert api(shared_viewer).get("/api/media/api-keys").status_code == 404


def test_reports_and_proposals_routes(api, shared_viewer, connection, fake_client):
    for path in ("/api/media/reports/overview?uploaded_from=2026-09-01", "/api/media/reports/ratings-by-channel",
                 "/api/media/reports/content-quality", "/api/media/proposals?status=pending&q=x", "/api/media/proposals/3",
                 "/api/media/contents/3/ad-evaluations"):
        assert api(shared_viewer).get(path).status_code == 200, path


def test_staging_blocks_with_503(api, shared_viewer, connection, settings):
    settings.IS_STAGING = True
    r = api(shared_viewer).get("/api/media/contents")
    assert (r.status_code, r.data["code"]) == (503, "media_staging_blocked")
```

- [ ] **Step 3: Chạy → fail** (`PYTEST apps/media/tests/test_read_views.py`, 404 mọi route).

- [ ] **Step 4: `transport/errors.py` + `transport/params.py`**

`transport/errors.py`:
```python
"""Đổi MediaError → Response {detail, code} (spec §4.6). Mọi view Media bọc decorator này."""

from __future__ import annotations

import functools
import logging

from rest_framework.response import Response

from apps.media.exceptions import MediaError

logger = logging.getLogger(__name__)


def media_errors(view):
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except MediaError as exc:
            if exc.status >= 500:
                logger.warning("media proxy %s %s → %s %s", request.method, request.path, exc.status, exc.code)
            return Response({"detail": exc.detail, "code": exc.code}, status=exc.status)

    return wrapper
```

`transport/params.py`:
```python
"""Allowlist query param từng route ads-crm (tài liệu §3). Param lạ bị lược im lặng."""

from __future__ import annotations

CONTENTS_LIST = frozenset({
    "source_id", "content_type_id", "overall_rating", "product_id", "category_id", "channel_id",
    "ad_rating", "uploaded_from", "uploaded_to", "sort", "order", "page", "page_size",
})
PROPOSALS_LIST = frozenset({"status", "product_id", "q", "page", "page_size"})
REPORTS = frozenset({"uploaded_from", "uploaded_to"})
TAXONOMY = frozenset({"include_inactive"})
TAXONOMY_GROUPS = ("content-types", "products", "product-categories", "ad-channels", "content-sources")


def pick(query_params, allowed: frozenset[str]) -> list[tuple[str, str]]:
    """Giữ thứ tự và giá trị lặp (`?a=1&a=2`) — ads-crm nhận cả hai kiểu."""
    out: list[tuple[str, str]] = []
    for key in query_params:
        if key in allowed:
            out.extend((key, v) for v in query_params.getlist(key) if v != "")
    return out
```

- [ ] **Step 5: `proxy_service.py`**

```python
"""MediaProxy: một đối tượng cho một request — giữ credential + client, gói forward,
cache taxonomy và bảng owner (spec §4.5)."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.core.cache import cache

from apps.media.application.services.access_policy import authorize
from apps.media.application.services.credential_resolver import is_provisioned, resolve
from apps.media.contracts.dtos import Credential
from apps.media.domain.roles import Action
from apps.media.exceptions import MediaError
from apps.media.infrastructure.ads_crm_client import AdsCrmClient
from apps.media.models import MediaConnection, MediaContentOwner, MediaProposalOwner

ME_CACHE_TTL = 600


def _tax_key(group: str, include_inactive: bool) -> str:
    return f"media:tax:{group}:{int(include_inactive)}"


class MediaProxy:
    def __init__(self, user):
        self.user = user
        self.cred: Credential = resolve(user)
        self.client = AdsCrmClient(self.cred.base_url, self.cred.api_key)

    # ── forward ─────────────────────────────────────────────────────────────
    def forward(
        self,
        method: str,
        path: str,
        *,
        action: Action,
        params: list[tuple[str, str]] | None = None,
        json: Any = None,
        owner_check: Callable[[], bool] | None = None,
    ) -> tuple[int, Any]:
        authorize(self.cred, action, owner_check=owner_check)
        return self.client.json_request(method, path, params=params or None, json=json)

    def allows(self, action: Action) -> bool:
        try:
            authorize(self.cred, action, owner_check=lambda: True)
        except MediaError:
            return False
        return True

    # ── taxonomy cache ──────────────────────────────────────────────────────
    def taxonomy(self, group: str, include_inactive: bool) -> Any:
        include_inactive = include_inactive and self.allows(Action.TAXONOMY_INCLUDE_INACTIVE)
        key = _tax_key(group, include_inactive)
        data = cache.get(key)
        if data is None:
            params = [("include_inactive", "true")] if include_inactive else None
            _, data = self.forward("GET", f"/{group}", action=Action.READ, params=params)
            cache.set(key, data, int(getattr(settings, "MEDIA_TAXONOMY_CACHE_TTL", 300)))
        return data

    @staticmethod
    def invalidate_taxonomy(group: str) -> None:
        cache.delete_many([_tax_key(group, False), _tax_key(group, True)])

    # ── me ──────────────────────────────────────────────────────────────────
    def remote_me(self) -> dict[str, Any] | None:
        """Role của key riêng, cache 10 phút theo sha256(key). None nếu upstream từ chối."""
        key = "media:me:" + hashlib.sha256(self.cred.api_key.encode()).hexdigest()
        data = cache.get(key)
        if data is None:
            try:
                data = self.client.me()
            except MediaError:
                return None
            cache.set(key, data, ME_CACHE_TTL)
        return data

    # ── ownership (chỉ key chung) ───────────────────────────────────────────
    def owns_content(self, remote_id: int) -> bool:
        return MediaContentOwner.objects.filter(remote_id=remote_id, user=self.user).exists()

    def owns_proposal(self, remote_id: int) -> bool:
        return MediaProposalOwner.objects.filter(remote_id=remote_id, user=self.user).exists()

    def record_content_owner(self, remote_id: int) -> None:
        if self.cred.kind == "shared":
            MediaContentOwner.objects.get_or_create(remote_id=remote_id, defaults={"user": self.user})

    def record_proposal_owner(self, remote_id: int) -> None:
        if self.cred.kind == "shared":
            MediaProposalOwner.objects.get_or_create(remote_id=remote_id, defaults={"user": self.user})


def describe_me(user) -> dict[str, Any]:
    """Payload GET /api/media/me — không raise khi chưa provisioned (spec §4.5)."""
    conn = MediaConnection.get_active()
    base = conn.resolve_base_url() if conn else ""
    empty = {"provisioned": False, "mode": None, "role": None, "remote_user": None,
             "public_base_url": base, "connection_ok": conn is not None}
    if conn is None or not is_provisioned(user):
        return empty
    proxy = MediaProxy(user)
    if proxy.cred.kind == "shared":
        return {**empty, "provisioned": True, "mode": "shared", "role": proxy.cred.role}
    remote = proxy.remote_me()
    if remote is None:
        return {**empty, "provisioned": True, "mode": "personal", "connection_ok": False}
    return {
        **empty, "provisioned": True, "mode": "personal", "role": remote.get("role"),
        "remote_user": {"id": remote.get("id"), "username": remote.get("username"), "name": remote.get("name")},
    }
```

- [ ] **Step 6: `transport/api_views.py` (phần đọc) + `urls.py`**

```python
"""View proxy Media — mỏng: pick params → MediaProxy.forward → Response."""

from __future__ import annotations

from django.http import Http404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.media.application.services.proxy_service import MediaProxy, describe_me
from apps.media.domain.roles import Action
from apps.media.transport import params as P
from apps.media.transport.errors import media_errors


def _auth(methods):
    """@api_view + IsAuthenticated + media_errors, đúng thứ tự (media_errors trong cùng)."""

    def deco(fn):
        return api_view(methods)(permission_classes([IsAuthenticated])(media_errors(fn)))

    return deco


def _passthrough(status: int, data):
    return Response(data, status=status)


@_auth(["GET"])
def me(request):
    return Response(describe_me(request.user))


@_auth(["GET"])
def contents_collection(request):
    proxy = MediaProxy(request.user)
    return _passthrough(*proxy.forward("GET", "/contents", action=Action.READ,
                                       params=P.pick(request.query_params, P.CONTENTS_LIST)))


@_auth(["GET"])
def content_detail(request, content_id: int):
    proxy = MediaProxy(request.user)
    return _passthrough(*proxy.forward("GET", f"/contents/{content_id}", action=Action.READ))


@_auth(["GET"])
def content_evaluations(request, content_id: int):
    proxy = MediaProxy(request.user)
    return _passthrough(*proxy.forward("GET", f"/contents/{content_id}/ad-evaluations", action=Action.READ))


@_auth(["GET"])
def taxonomy_collection(request, group: str):
    if group not in P.TAXONOMY_GROUPS:
        raise Http404
    proxy = MediaProxy(request.user)
    include_inactive = request.query_params.get("include_inactive") == "true"
    return Response(proxy.taxonomy(group, include_inactive))


@_auth(["GET"])
def proposals_collection(request):
    proxy = MediaProxy(request.user)
    return _passthrough(*proxy.forward("GET", "/proposals", action=Action.READ,
                                       params=P.pick(request.query_params, P.PROPOSALS_LIST)))


@_auth(["GET"])
def proposal_detail(request, proposal_id: int):
    proxy = MediaProxy(request.user)
    return _passthrough(*proxy.forward("GET", f"/proposals/{proposal_id}", action=Action.READ))


@_auth(["GET"])
def report(request, name: str):
    if name not in ("overview", "ratings-by-channel", "content-quality"):
        raise Http404
    proxy = MediaProxy(request.user)
    return _passthrough(*proxy.forward("GET", f"/reports/{name}", action=Action.READ,
                                       params=P.pick(request.query_params, P.REPORTS)))
```

`urls.py`:
```python
"""Allowlist route Media. Không có catch-all: path không khai ở đây → 404 (spec §4.5)."""

from django.urls import path, re_path

from apps.media.transport import api_views as v

TAX = "content-types|products|product-categories|ad-channels|content-sources"

urlpatterns = [
    path("me", v.me, name="media-me"),
    path("contents", v.contents_collection, name="media-contents"),
    path("contents/<int:content_id>", v.content_detail, name="media-content-detail"),
    path("contents/<int:content_id>/ad-evaluations", v.content_evaluations, name="media-content-evaluations"),
    path("proposals", v.proposals_collection, name="media-proposals"),
    path("proposals/<int:proposal_id>", v.proposal_detail, name="media-proposal-detail"),
    re_path(r"^reports/(?P<name>[a-z-]+)$", v.report, name="media-report"),
    re_path(rf"^(?P<group>{TAX})$", v.taxonomy_collection, name="media-taxonomy"),
]
```
`transport/__init__.py`: rỗng.

- [ ] **Step 7: Chạy → pass, commit**

Run: `PYTEST apps/media/tests/test_read_views.py` → 12 passed. Chạy thêm `PYTEST apps/media` toàn bộ.
```bash
git add backend/apps/media
git commit -m "feat(media): proxy đọc /api/media (me, contents, taxonomy cache, proposals, reports) + allowlist route"
```

---

### Task 7: Settings API (`/api/media/settings`, `/settings/test`)

**Files:**
- Create: `backend/apps/media/application/services/connection_service.py`, `transport/serializers.py`, `transport/settings_views.py`, `tests/test_settings_views.py`
- Modify: `backend/apps/media/urls.py`

**Interfaces:**
- Produces: `GET settings → {base_url, shared_api_key_set, shared_api_key_prefix, is_active, last_check_at, last_check_ok, last_check_error, shared_key_role, shared_key_username}`; `PATCH settings {base_url?, shared_api_key?("" giữ, null xoá), is_active?}`; `POST settings/test → 200 {ok: true, role, username}` hoặc `200 {ok: false, error}`. Quyền: `require_permission("users:edit")` (GET dùng `users:view`).

- [ ] **Step 1: Test**

`backend/apps/media/tests/test_settings_views.py`:
```python
import pytest

from apps.media.models import MediaConnection
from apps.users.test_helpers import make_admin, make_user_with_permissions

pytestmark = pytest.mark.django_db


def test_non_admin_forbidden(api):
    staff = make_user_with_permissions("s", ["orders:view"])
    assert api(staff).get("/api/media/settings").status_code == 403
    assert api(staff).patch("/api/media/settings", {"base_url": "x"}, format="json").status_code == 403


def test_get_empty_when_not_configured(api):
    r = api(make_admin()).get("/api/media/settings")
    assert r.status_code == 200
    assert r.data["base_url"] == "" and r.data["shared_api_key_set"] is False and r.data["is_active"] is False


def test_patch_creates_then_updates_without_echoing_key(api):
    admin = make_admin()
    r = api(admin).patch("/api/media/settings", {"base_url": "https://media.example/", "shared_api_key": "dak_sharedkey_abc"}, format="json")
    assert r.status_code == 200, r.data
    assert r.data["base_url"] == "https://media.example"
    assert r.data["shared_api_key_set"] is True and r.data["shared_api_key_prefix"] == "dak_sharedke"
    assert "shared_api_key" not in r.data
    conn = MediaConnection.objects.get()
    assert conn.shared_api_key == "dak_sharedkey_abc"

    # "" giữ nguyên key; null xoá key; is_active đổi được.
    api(admin).patch("/api/media/settings", {"shared_api_key": "", "is_active": False}, format="json")
    conn.refresh_from_db()
    assert conn.shared_api_key == "dak_sharedkey_abc" and conn.is_active is False
    api(admin).patch("/api/media/settings", {"shared_api_key": None}, format="json")
    conn.refresh_from_db()
    assert conn.shared_api_key == ""


def test_patch_rejects_bad_url_and_bad_key(api):
    admin = make_admin()
    assert api(admin).patch("/api/media/settings", {"base_url": "media.example"}, format="json").status_code == 400
    assert api(admin).patch("/api/media/settings", {"base_url": "https://ok.example", "shared_api_key": "nope"}, format="json").status_code == 400


def test_test_connection_records_result(api, connection, fake_client):
    admin = make_admin()
    fake_client.me.return_value = {"id": 1, "username": "svc", "role": "uploader"}
    r = api(admin).post("/api/media/settings/test")
    assert r.status_code == 200
    assert r.data == {"ok": True, "role": "uploader", "username": "svc",
                      "warning": "Key chung không phải role admin: người dùng key chung sẽ bị 403 khi sửa nội dung không do key tạo."}
    connection.refresh_from_db()
    assert connection.last_check_ok is True and connection.shared_key_role == "uploader"


def test_test_connection_failure_is_200_ok_false(api, connection, fake_client, upstream_error):
    fake_client.me.side_effect = upstream_error("invalid_token", 401)
    r = api(make_admin()).post("/api/media/settings/test")
    assert r.status_code == 200 and r.data["ok"] is False
    assert "từ chối" in r.data["error"]
    connection.refresh_from_db()
    assert connection.last_check_ok is False and connection.last_check_error
```

- [ ] **Step 2: Chạy → fail** (404).

- [ ] **Step 3: Implement**

`application/services/connection_service.py`:
```python
"""Đọc/ghi MediaConnection và kiểm tra key chung bằng GET /auth/me (spec §4.5)."""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from apps.media.exceptions import MediaError
from apps.media.infrastructure.ads_crm_client import AdsCrmClient
from apps.media.models import MediaConnection

NON_ADMIN_WARNING = (
    "Key chung không phải role admin: người dùng key chung sẽ bị 403 khi sửa nội dung không do key tạo."
)


def current() -> MediaConnection | None:
    return MediaConnection.objects.order_by("-updated_at").first()


def to_payload(conn: MediaConnection | None) -> dict[str, Any]:
    if conn is None:
        return {"base_url": "", "shared_api_key_set": False, "shared_api_key_prefix": "", "is_active": False,
                "last_check_at": None, "last_check_ok": None, "last_check_error": "",
                "shared_key_role": "", "shared_key_username": ""}
    key = (conn.shared_api_key or "").strip()
    return {
        "base_url": conn.resolve_base_url(), "shared_api_key_set": bool(key), "shared_api_key_prefix": key[:12],
        "is_active": conn.is_active, "last_check_at": conn.last_check_at, "last_check_ok": conn.last_check_ok,
        "last_check_error": conn.last_check_error, "shared_key_role": conn.shared_key_role,
        "shared_key_username": conn.shared_key_username,
    }


def update(validated: dict[str, Any]) -> MediaConnection:
    conn = current() or MediaConnection(base_url="", shared_api_key="")
    if "base_url" in validated:
        conn.base_url = validated["base_url"].strip().rstrip("/")
    if "shared_api_key" in validated:
        key = validated["shared_api_key"]
        if key is None:
            conn.shared_api_key = ""
        elif key:
            conn.shared_api_key = key
    if "is_active" in validated:
        conn.is_active = validated["is_active"]
    conn.save()
    return conn


def test_shared_key(conn: MediaConnection) -> dict[str, Any]:
    client = AdsCrmClient(conn.resolve_base_url(), conn.shared_api_key)
    conn.last_check_at = timezone.now()
    try:
        me = client.me()
    except MediaError as exc:
        conn.last_check_ok, conn.last_check_error = False, exc.detail
        conn.save(update_fields=["last_check_at", "last_check_ok", "last_check_error", "updated_at"])
        return {"ok": False, "error": exc.detail}
    role, username = str(me.get("role") or ""), str(me.get("username") or "")
    conn.last_check_ok, conn.last_check_error = True, ""
    conn.shared_key_role, conn.shared_key_username = role, username
    conn.save()
    payload: dict[str, Any] = {"ok": True, "role": role, "username": username}
    if role != "admin":
        payload["warning"] = NON_ADMIN_WARNING
    return payload
```

`transport/serializers.py`:
```python
from __future__ import annotations

from rest_framework import serializers


def validate_dak_key(value):
    if value is None or value == "":
        return value
    value = value.strip()
    if not value.startswith("dak_") or len(value) < 12:
        raise serializers.ValidationError("API key phải bắt đầu bằng 'dak_'.")
    return value


class MediaSettingsUpdateSerializer(serializers.Serializer):
    base_url = serializers.URLField(required=False)
    shared_api_key = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=1024)
    is_active = serializers.BooleanField(required=False)

    def validate_shared_api_key(self, value):
        return validate_dak_key(value)
```

`transport/settings_views.py`:
```python
"""Cấu hình kết nối Media — admin (users:view/edit), không đi qua MediaProxy."""

from __future__ import annotations

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.media.application.services import connection_service as svc
from apps.media.exceptions import MediaDisabled
from apps.media.transport.errors import media_errors
from apps.media.transport.serializers import MediaSettingsUpdateSerializer
from apps.users.permissions import require_method_permission, require_permission


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated, require_method_permission(GET="users:view", PATCH="users:edit")])
@media_errors
def settings_view(request):
    if request.method == "GET":
        return Response(svc.to_payload(svc.current()))
    serializer = MediaSettingsUpdateSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    return Response(svc.to_payload(svc.update(serializer.validated_data)))


@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("users:edit")])
@media_errors
def settings_test(request):
    conn = svc.current()
    if conn is None or not conn.shared_api_key:
        raise MediaDisabled("Chưa nhập host hoặc API key chung.")
    return Response(svc.test_shared_key(conn))
```

`urls.py` thêm trước `re_path` reports:
```python
    path("settings", s.settings_view, name="media-settings"),
    path("settings/test", s.settings_test, name="media-settings-test"),
```
với `from apps.media.transport import settings_views as s`.

> `fake_client` fixture patch `AdsCrmClient` ở `proxy_service`; cho connection_service cần patch thêm — sửa fixture `fake_client` trong `tests/conftest.py`: thêm dòng `monkeypatch.setattr("apps.media.application.services.connection_service.AdsCrmClient", lambda base_url, api_key, **kw: client)`.

- [ ] **Step 4: Chạy → pass, commit**

Run: `PYTEST apps/media` → all passed.
```bash
git add backend/apps/media
git commit -m "feat(media): API cấu hình kết nối + kiểm tra key chung (/api/media/settings)"
```

---

### Task 8: Frontend — type API, ma trận `mediaCan`, filter ↔ URL

**Files:**
- Create: `frontend/src/lib/api/media.ts`, `frontend/src/features/media/access.ts`, `frontend/src/features/media/filters.ts`, `frontend/src/features/media/__tests__/access.test.ts`, `frontend/src/features/media/__tests__/filters.test.ts`

**Interfaces:**
- Produces (dùng ở mọi task FE sau): type `MediaRole = "viewer"|"marketing"|"uploader"|"admin"`, `MediaAction` (cùng chuỗi với backend `Action`), `mediaCan(role: MediaRole | null | undefined, action: MediaAction): boolean`; `ContentFilters`, `DEFAULT_FILTERS`, `filtersToSearchParams(f): URLSearchParams`, `filtersFromSearchParams(sp): ContentFilters`; các hàm fetch trong `lib/api/media.ts` (liệt kê ở Step 4).

- [ ] **Step 1: Test `access.ts`**

`frontend/src/features/media/__tests__/access.test.ts`:
```ts
import { describe, expect, it } from "vitest";

import { MEDIA_ACTIONS, mediaCan, type MediaAction, type MediaRole } from "@/features/media/access";

const VIEWER: MediaAction[] = ["read", "proposal.create", "proposal.edit"];
const MARKETING: MediaAction[] = [...VIEWER, "content.edit_marketing", "evaluation.write"];
const UPLOADER: MediaAction[] = [
  ...VIEWER, "content.create", "content.edit_upload", "content.edit_marketing", "taxonomy.write_sources",
];
const EXPECTED: Record<MediaRole, readonly MediaAction[]> = {
  viewer: VIEWER, marketing: MARKETING, uploader: UPLOADER, admin: MEDIA_ACTIONS,
};

describe("mediaCan", () => {
  it("khớp ma trận §5.2 cho 4 role × mọi action", () => {
    for (const role of Object.keys(EXPECTED) as MediaRole[]) {
      for (const action of MEDIA_ACTIONS) {
        expect(mediaCan(role, action), `${role}/${action}`).toBe(EXPECTED[role].includes(action));
      }
    }
  });
  it("role rỗng/null → không có quyền gì", () => {
    expect(mediaCan(null, "read")).toBe(false);
    expect(mediaCan(undefined, "read")).toBe(false);
  });
});
```

- [ ] **Step 2: Test `filters.ts`**

`frontend/src/features/media/__tests__/filters.test.ts`:
```ts
import { describe, expect, it } from "vitest";

import { DEFAULT_FILTERS, filtersFromSearchParams, filtersToSearchParams } from "@/features/media/filters";

describe("content filters ↔ URLSearchParams", () => {
  it("round-trip giữ danh sách và bỏ giá trị mặc định", () => {
    const f = {
      ...DEFAULT_FILTERS,
      productIds: ["1", "5"], channelIds: ["2"], adRatings: ["4", "4.5"],
      uploadedFrom: "2026-09-01", uploadedTo: "2026-09-04", sort: "rating" as const, order: "desc" as const,
      page: 3, pageSize: 50,
    };
    const sp = filtersToSearchParams(f);
    expect(sp.getAll("product_id")).toEqual(["1", "5"]);
    expect(sp.get("uploaded_from")).toBe("2026-09-01");
    expect(sp.get("page")).toBe("3");
    expect(filtersFromSearchParams(sp)).toEqual(f);
  });
  it("mặc định → query rỗng; ad_rating bị bỏ khi không có channel_id", () => {
    expect(filtersToSearchParams(DEFAULT_FILTERS).toString()).toBe("");
    const sp = filtersToSearchParams({ ...DEFAULT_FILTERS, adRatings: ["4"] });
    expect(sp.has("ad_rating")).toBe(false);
  });
  it("page_size ngoài 1..100 rơi về 20", () => {
    expect(filtersFromSearchParams(new URLSearchParams("page_size=500")).pageSize).toBe(20);
  });
});
```

- [ ] **Step 3: Chạy → fail** (`VITEST src/features/media` — module not found).

- [ ] **Step 4: `lib/api/media.ts`**

```ts
/**
 * Type theo tài liệu ads-crm "Tích hợp API đối ngoại" §4 + hàm gọi proxy /api/media/*.
 * Path mirror 1:1 với ads-crm (bỏ tiền tố /api/v1). Khi có docs/openapi.yaml của ads-crm,
 * thay phần type bằng openapi-typescript.
 */
import { fetchJson, getValidAccessToken } from "@/services/http";

export type MediaRole = "viewer" | "marketing" | "uploader" | "admin";
export type OverallRating = "good" | "average" | "bad" | "unknown";
export type JobStatus = "idle" | "pending" | "success" | "failed";
export type ProposalStatus = "pending" | "in_progress" | "done" | "cancelled";
export type TaxonomyGroup = "content-types" | "products" | "product-categories" | "ad-channels" | "content-sources";
export const TAXONOMY_GROUPS: readonly TaxonomyGroup[] = [
  "content-types", "products", "product-categories", "ad-channels", "content-sources",
];

export type Paginated<T> = { items: T[]; total: number; page: number; page_size: number };
export type Listed<T> = { items: T[] };

export type MediaRemoteUser = { id: number; name: string; username: string; email?: string; role?: MediaRole; status?: string };
export type MediaTaxonomyItem = {
  id: number; name: string; is_active: boolean;
  group?: "video" | "image"; code?: string; created_at?: string; updated_at?: string;
};
export type MediaAdEvaluation = {
  id: number; content_id: number; channel_id: number | null; channel: MediaTaxonomyItem | null;
  evaluator_id: number; evaluator_name: string; rating: number; note: string; evaluated_at: string;
  created_at: string; updated_at: string;
};
export type MediaContent = {
  id: number; content_code: string; uploaded_at: string; title: string; url: string;
  thumbnail_url: string; thumbnail_ext: string | null;
  video_status: JobStatus; video_ext: string | null; video_size: number | null; video_cached_at: string | null;
  source_id: number; source: MediaTaxonomyItem | null;
  content_type_id: number | null; content_type: MediaTaxonomyItem | null;
  overall_rating: OverallRating; overall_score: number | null; overall_note: string;
  products: MediaTaxonomyItem[]; categories: MediaTaxonomyItem[]; ad_evaluations: MediaAdEvaluation[];
  created_by: number; creator: MediaRemoteUser | null; updated_by: number; created_at: string; updated_at: string;
};
export type MediaProposal = {
  id: number; main_key: string; idea: string; due_date: string | null; status: ProposalStatus;
  product_id: number | null; product: MediaTaxonomyItem | null;
  content_id: number | null; content: MediaContent | null;
  created_by: number; creator: MediaRemoteUser | null; created_at: string; updated_at: string;
};
export type MediaMe = {
  provisioned: boolean; mode: "personal" | "shared" | null; role: MediaRole | null;
  remote_user: { id: number; username: string; name: string } | null;
  public_base_url: string; connection_ok: boolean;
};
export type MediaReportOverview = {
  total_contents: number; missing_tags: number; missing_evaluations: number;
  content_by_type: { name: string; total: number }[];
};
export type MediaRatingsByChannel = { items: { channel: string; avg_rating: number; total: number }[] };
export type MediaQualityRow = { id: number; content_code: string; title: string; issue: string };
export type MediaContentQuality = { missing_tags: MediaQualityRow[]; missing_evaluations: MediaQualityRow[] };
export type MediaSettings = {
  base_url: string; shared_api_key_set: boolean; shared_api_key_prefix: string; is_active: boolean;
  last_check_at: string | null; last_check_ok: boolean | null; last_check_error: string;
  shared_key_role: string; shared_key_username: string;
};
export type MediaSettingsTest = { ok: boolean; role?: string; username?: string; warning?: string; error?: string };

export type ContentUploadPayload = {
  uploaded_at: string; title: string; url: string; source_id: number;
  content_type_id?: number | null; product_ids?: number[]; category_ids?: number[];
};
export type ContentTagsPayload = { content_type_id: number | null; product_ids: number[]; category_ids: number[] };
export type EvaluationPayload = { rating: number; note: string };
export type ProposalPayload = {
  main_key: string; idea: string; due_date: string | null; status?: ProposalStatus;
  product_id: number | null; content_id: number | null;
};
export type TaxonomyPayload = { name?: string; code?: string; group?: "video" | "image"; is_active?: boolean };

const BASE = "/api/media";
const q = (sp?: URLSearchParams) => (sp && sp.toString() ? `?${sp}` : "");
const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export const getMediaMe = (signal?: AbortSignal) => fetchJson(`${BASE}/me`, { signal }) as Promise<MediaMe>;
export const listContents = (sp: URLSearchParams, signal?: AbortSignal) =>
  fetchJson(`${BASE}/contents${q(sp)}`, { signal }) as Promise<Paginated<MediaContent>>;
export const getContent = (id: number, signal?: AbortSignal) =>
  fetchJson(`${BASE}/contents/${id}`, { signal }) as Promise<MediaContent>;
export const createContent = (p: ContentUploadPayload) => fetchJson(`${BASE}/contents`, json("POST", p)) as Promise<MediaContent>;
export const duplicateContent = (id: number) => fetchJson(`${BASE}/contents/${id}/duplicate`, { method: "POST" }) as Promise<MediaContent>;
export const updateUploadInfo = (id: number, p: ContentUploadPayload) =>
  fetchJson(`${BASE}/contents/${id}/upload-info`, json("PATCH", p)) as Promise<MediaContent>;
export const updateMarketingInfo = (id: number, p: ContentTagsPayload) =>
  fetchJson(`${BASE}/contents/${id}/marketing-info`, json("PATCH", p)) as Promise<MediaContent>;
export const deleteContent = (id: number) => fetchJson(`${BASE}/contents/${id}`, { method: "DELETE" }) as Promise<{ ok: true }>;
export const requestVideoCache = (id: number, force: boolean) =>
  fetchJson(`${BASE}/contents/${id}/video/cache${force ? "?force=1" : ""}`, { method: "POST" }) as Promise<{ video_status: JobStatus }>;

export const listEvaluations = (contentId: number, signal?: AbortSignal) =>
  fetchJson(`${BASE}/contents/${contentId}/ad-evaluations`, { signal }) as Promise<Listed<MediaAdEvaluation>>;
export const createEvaluation = (contentId: number, p: EvaluationPayload) =>
  fetchJson(`${BASE}/contents/${contentId}/ad-evaluations`, json("POST", p)) as Promise<MediaAdEvaluation>;
export const updateEvaluation = (id: number, p: EvaluationPayload) =>
  fetchJson(`${BASE}/ad-evaluations/${id}`, json("PATCH", p)) as Promise<MediaAdEvaluation>;
export const deleteEvaluation = (id: number) => fetchJson(`${BASE}/ad-evaluations/${id}`, { method: "DELETE" }) as Promise<{ ok: true }>;

export const listTaxonomy = (group: TaxonomyGroup, includeInactive: boolean, signal?: AbortSignal) =>
  fetchJson(`${BASE}/${group}${includeInactive ? "?include_inactive=true" : ""}`, { signal }) as Promise<Listed<MediaTaxonomyItem>>;
export const createTaxonomy = (group: TaxonomyGroup, p: TaxonomyPayload) =>
  fetchJson(`${BASE}/${group}`, json("POST", p)) as Promise<MediaTaxonomyItem>;
export const updateTaxonomy = (group: TaxonomyGroup, id: number, p: TaxonomyPayload) =>
  fetchJson(`${BASE}/${group}/${id}`, json("PATCH", p)) as Promise<MediaTaxonomyItem>;
export const deleteTaxonomy = (group: TaxonomyGroup, id: number) =>
  fetchJson(`${BASE}/${group}/${id}`, { method: "DELETE" }) as Promise<{ ok: true }>;

export const listProposals = (sp: URLSearchParams, signal?: AbortSignal) =>
  fetchJson(`${BASE}/proposals${q(sp)}`, { signal }) as Promise<Paginated<MediaProposal>>;
export const createProposal = (p: ProposalPayload) => fetchJson(`${BASE}/proposals`, json("POST", p)) as Promise<MediaProposal>;
export const updateProposal = (id: number, p: ProposalPayload) => fetchJson(`${BASE}/proposals/${id}`, json("PATCH", p)) as Promise<MediaProposal>;
export const deleteProposal = (id: number) => fetchJson(`${BASE}/proposals/${id}`, { method: "DELETE" }) as Promise<{ ok: true }>;

export const getReportOverview = (sp: URLSearchParams, signal?: AbortSignal) =>
  fetchJson(`${BASE}/reports/overview${q(sp)}`, { signal }) as Promise<MediaReportOverview>;
export const getRatingsByChannel = (sp: URLSearchParams, signal?: AbortSignal) =>
  fetchJson(`${BASE}/reports/ratings-by-channel${q(sp)}`, { signal }) as Promise<MediaRatingsByChannel>;
export const getContentQuality = (sp: URLSearchParams, signal?: AbortSignal) =>
  fetchJson(`${BASE}/reports/content-quality${q(sp)}`, { signal }) as Promise<MediaContentQuality>;

export const getMediaSettings = (signal?: AbortSignal) => fetchJson(`${BASE}/settings`, { signal }) as Promise<MediaSettings>;
export const updateMediaSettings = (p: { base_url?: string; shared_api_key?: string | null; is_active?: boolean }) =>
  fetchJson(`${BASE}/settings`, json("PATCH", p)) as Promise<MediaSettings>;
export const testMediaSettings = () => fetchJson(`${BASE}/settings/test`, { method: "POST" }) as Promise<MediaSettingsTest>;

/** Thumbnail công khai trên host ads-crm (spec D8) — hotlink, không qua proxy. */
export function thumbnailUrl(publicBaseUrl: string, content: Pick<MediaContent, "id" | "thumbnail_ext">): string | null {
  if (!publicBaseUrl || !content.thumbnail_ext) return null;
  return `${publicBaseUrl.replace(/\/$/, "")}/api/v1/contents/${content.id}/thumbnail`;
}

/** Tải video qua proxy: fetch có Bearer → Blob (spec D9). Ném Error(detail) khi lỗi. */
export async function downloadContentVideo(id: number): Promise<{ blob: Blob; filename: string }> {
  const token = await getValidAccessToken();
  const res = await fetch(`${BASE}/contents/${id}/download`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) {
    let detail = `Tải video thất bại (${res.status}).`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* body không phải JSON */
    }
    throw new Error(detail);
  }
  const cd = res.headers.get("Content-Disposition") ?? "";
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)/i.exec(cd);
  const filename = m ? decodeURIComponent(m[1]) : `media-${id}.mp4`;
  return { blob: await res.blob(), filename };
}
```

- [ ] **Step 5: `features/media/access.ts`**

```ts
import type { MediaRole } from "@/lib/api/media";

export type { MediaRole };

/** Cùng chuỗi với backend `apps.media.domain.roles.Action`. */
export const MEDIA_ACTIONS = [
  "read", "content.create", "content.edit_upload", "content.edit_marketing", "content.delete",
  "video.cache_force", "evaluation.write", "taxonomy.write", "taxonomy.write_sources",
  "taxonomy.include_inactive", "proposal.create", "proposal.edit", "proposal.delete",
] as const;
export type MediaAction = (typeof MEDIA_ACTIONS)[number];

const VIEWER: readonly MediaAction[] = ["read", "proposal.create", "proposal.edit"];
const MATRIX: Record<MediaRole, ReadonlySet<MediaAction>> = {
  viewer: new Set(VIEWER),
  marketing: new Set([...VIEWER, "content.edit_marketing", "evaluation.write"]),
  uploader: new Set([...VIEWER, "content.create", "content.edit_upload", "content.edit_marketing", "taxonomy.write_sources"]),
  admin: new Set(MEDIA_ACTIONS),
};

/**
 * Ẩn/hiện nút theo role Media. Chỉ là UX: các action giới hạn "chủ sở hữu" (uploader
 * sửa nội dung, viewer/marketing sửa đề xuất) vẫn hiện nút — backend/ads-crm quyết
 * định và frontend hiện toast lỗi.
 */
export function mediaCan(role: MediaRole | null | undefined, action: MediaAction): boolean {
  if (!role) return false;
  return MATRIX[role]?.has(action) ?? false;
}
```

- [ ] **Step 6: `features/media/filters.ts`**

```ts
export type ContentSort = "uploaded_at" | "title" | "rating";
export type ContentFilters = {
  sourceIds: string[]; contentTypeIds: string[]; overallRatings: string[];
  productIds: string[]; categoryIds: string[]; channelIds: string[]; adRatings: string[];
  uploadedFrom: string; uploadedTo: string;
  sort: ContentSort; order: "asc" | "desc"; page: number; pageSize: number;
};
export const PAGE_SIZE_OPTIONS = [20, 50, 100] as const;
export const DEFAULT_FILTERS: ContentFilters = {
  sourceIds: [], contentTypeIds: [], overallRatings: [], productIds: [], categoryIds: [], channelIds: [], adRatings: [],
  uploadedFrom: "", uploadedTo: "", sort: "uploaded_at", order: "desc", page: 1, pageSize: 20,
};

const LISTS: Array<[keyof ContentFilters & string, string]> = [
  ["sourceIds", "source_id"], ["contentTypeIds", "content_type_id"], ["overallRatings", "overall_rating"],
  ["productIds", "product_id"], ["categoryIds", "category_id"], ["channelIds", "channel_id"], ["adRatings", "ad_rating"],
];

export function filtersToSearchParams(f: ContentFilters): URLSearchParams {
  const sp = new URLSearchParams();
  for (const [key, param] of LISTS) {
    // ad_rating chỉ có tác dụng khi gửi kèm channel_id (tài liệu §3.4) — không gửi lẻ.
    if (key === "adRatings" && f.channelIds.length === 0) continue;
    for (const v of f[key] as string[]) sp.append(param, v);
  }
  if (f.uploadedFrom) sp.set("uploaded_from", f.uploadedFrom);
  if (f.uploadedTo) sp.set("uploaded_to", f.uploadedTo);
  if (f.sort !== DEFAULT_FILTERS.sort || f.order !== DEFAULT_FILTERS.order) {
    sp.set("sort", f.sort);
    sp.set("order", f.order);
  }
  if (f.page !== 1) sp.set("page", String(f.page));
  if (f.pageSize !== DEFAULT_FILTERS.pageSize) sp.set("page_size", String(f.pageSize));
  return sp;
}

export function filtersFromSearchParams(sp: URLSearchParams): ContentFilters {
  const f: ContentFilters = { ...DEFAULT_FILTERS };
  for (const [key, param] of LISTS) (f[key] as string[]) = sp.getAll(param);
  f.uploadedFrom = sp.get("uploaded_from") ?? "";
  f.uploadedTo = sp.get("uploaded_to") ?? "";
  const sort = sp.get("sort");
  if (sort === "uploaded_at" || sort === "title" || sort === "rating") f.sort = sort;
  f.order = sp.get("order") === "asc" ? "asc" : "desc";
  const page = Number(sp.get("page") ?? "1");
  f.page = Number.isInteger(page) && page > 0 ? page : 1;
  const size = Number(sp.get("page_size") ?? "20");
  f.pageSize = Number.isInteger(size) && size >= 1 && size <= 100 ? size : 20;
  return f;
}
```

- [ ] **Step 7: Chạy → pass; typecheck; commit**

Run: `VITEST src/features/media` → 5 passed. `TYPECHECK` → 0 lỗi.
```bash
git add frontend/src/lib/api/media.ts frontend/src/features/media
git commit -m "feat(media): type API ads-crm, ma trận mediaCan, filter ↔ URL"
```

---

### Task 9: Frontend — registry module, route, i18n, queries, `MediaAccessGate`, khung 5 trang

**Files:**
- Create: `frontend/src/features/media/queries.ts`, `features/media/components/MediaAccessGate.tsx`, `features/media/pages/MediaContentsPage.tsx`, `MediaProposalsPage.tsx`, `MediaReportsPage.tsx`, `MediaTaxonomyPage.tsx`, `MediaSettingsPage.tsx` (khung, nội dung ở task sau), `i18n/messages/media.ts`, `features/media/__tests__/MediaAccessGate.test.tsx`
- Modify: `frontend/src/lib/routes.ts`, `app/modules.ts`, `app/routed-pages.tsx`, `app/routes.tsx`, `i18n/messages.ts`, `i18n/messages/sidebar.ts`, `i18n/messages/topnav.ts`, `i18n/messages/app.ts`, `app/__tests__/nav-layout.test.ts`

**Interfaces:**
- Produces: `mediaKeys`, hooks `useMediaMe()`, `useContents(sp)`, `useContent(id)`, `useEvaluations(contentId)`, `useTaxonomy(group, includeInactive)`, `useAllTaxonomy()` (5 nhóm song song), `useProposals(sp)`, `useReportOverview(sp)`, `useRatingsByChannel(sp)`, `useContentQuality(sp)`, `useMediaSettings()`; `<MediaAccessGate>{(me: MediaMe) => ReactNode}</MediaAccessGate>`; hook `useMediaRole(): MediaRole | null`.

- [ ] **Step 1: Test gate + nav**

`frontend/src/features/media/__tests__/MediaAccessGate.test.tsx`:
```tsx
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

const fetchJson = vi.fn();
vi.mock("@/services/http", () => ({
  fetchJson: (...args: unknown[]) => fetchJson(...args),
  getValidAccessToken: vi.fn().mockResolvedValue(null),
}));

import { MediaAccessGate } from "@/features/media/components/MediaAccessGate";
import { renderWithClient } from "@/test/utils";

describe("MediaAccessGate", () => {
  it("chưa provisioned → hướng dẫn, không render children", async () => {
    fetchJson.mockResolvedValueOnce({ provisioned: false, mode: null, role: null, remote_user: null, public_base_url: "", connection_ok: true });
    renderWithClient(<MediaAccessGate>{() => <p>NỘI DUNG</p>}</MediaAccessGate>);
    expect(await screen.findByText(/chưa được cấp quyền Media/i)).toBeInTheDocument();
    expect(screen.queryByText("NỘI DUNG")).toBeNull();
  });
  it("provisioned → render children với me; connection lỗi → banner", async () => {
    fetchJson.mockResolvedValueOnce({ provisioned: true, mode: "personal", role: "uploader", remote_user: null, public_base_url: "https://m", connection_ok: false });
    renderWithClient(<MediaAccessGate>{(me) => <p>ROLE:{me.role}</p>}</MediaAccessGate>);
    expect(await screen.findByText("ROLE:uploader")).toBeInTheDocument();
    expect(screen.getByText(/Kết nối Media đang lỗi/i)).toBeInTheDocument();
  });
});
```
Sửa `app/__tests__/nav-layout.test.ts` test "full quyền": `groupKeys` kỳ vọng thành `["sales","cskh","crm","inventory","catalog","marketing","media","aftersales","hr","settings"]`.

- [ ] **Step 2: Chạy → fail**.

- [ ] **Step 3: `lib/routes.ts`**

- `AppSection` thêm `| "media-contents" | "media-proposals" | "media-reports" | "media-taxonomy" | "media-settings"`.
- `SECTION_TO_PATH` thêm: `"media-contents": "/media/contents"`, `"media-proposals": "/media/proposals"`, `"media-reports": "/media/reports"`, `"media-taxonomy": "/media/taxonomy"`, `"media-settings": "/settings/media"`.
- Trong `sectionFromPath`, ngay sau dòng `/^\/catalog\/products…` thêm:
```ts
  // Chi tiết nội dung Media `/media/contents/:id` là route con của Thư viện nội dung.
  if (/^\/media\/contents(\/|$)/.test(normalized)) return "media-contents";
```

- [ ] **Step 4: `app/modules.ts`**

Import thêm `Clapperboard, Film, Lightbulb, Plug, Tags` từ lucide-react. Sau module `marketing` thêm:
```ts
  {
    key: "media",
    labelKey: "sidebar.group.media",
    shortLabelKey: "module.media",
    icon: Clapperboard,
    accent: { chip: "bg-indigo-400", tile: "bg-indigo-500/15 text-indigo-600 dark:text-indigo-400" },
    basePath: "/media",
    // Menu hiện theo provisioning từng user (backend thêm 4 section này vào
    // allowed_sections khi user có key riêng hoặc vai trò Media) — không qua role RBAC.
    sections: [
      { key: "media-contents", labelKey: "topnav.tabs.mediaContents", icon: Film },
      { key: "media-proposals", labelKey: "topnav.tabs.mediaProposals", icon: Lightbulb },
      { key: "media-reports", labelKey: "topnav.tabs.mediaReports", icon: BarChart3 },
      { key: "media-taxonomy", labelKey: "topnav.tabs.mediaTaxonomy", icon: Tags },
    ],
  },
```
Trong module `settings`, sau `kiotviet`: `{ key: "media-settings", labelKey: "topnav.tabs.mediaSettings", icon: Plug, requires: "users" },`.
`nav-layout.ts` `SIDEBAR_ZONES[0].moduleKeys`: chèn `"media"` sau `"marketing"`.

- [ ] **Step 5: i18n**

`i18n/messages/sidebar.ts`: vi+en `"sidebar.group.media": "Media"`. `app.ts`: vi+en `"module.media": "Media"`. `topnav.ts` vi:
```ts
    "topnav.tabs.mediaContents": "Thư viện nội dung",
    "topnav.tabs.mediaProposals": "Đề xuất nội dung",
    "topnav.tabs.mediaReports": "Báo cáo Media",
    "topnav.tabs.mediaTaxonomy": "Danh mục Media",
    "topnav.tabs.mediaSettings": "Kết nối Media",
```
en: `Content library`, `Content proposals`, `Media reports`, `Media taxonomy`, `Media connection`.

`i18n/messages/media.ts` (tạo, đăng ký vào `messages.ts` cả vi/en):
```ts
export const media = {
  vi: {
    "media.gate.notProvisioned.title": "Bạn chưa được cấp quyền Media",
    "media.gate.notProvisioned.desc": "Nhờ admin gán vai trò Media hoặc nhập API key Media riêng cho tài khoản của bạn.",
    "media.gate.connectionError": "Kết nối Media đang lỗi. API key có thể đã bị thu hồi hoặc ads-crm không phản hồi.",
    "media.common.loading": "Đang tải...",
    "media.common.empty": "Không có dữ liệu",
    "media.common.save": "Lưu",
    "media.common.cancel": "Huỷ",
    "media.common.delete": "Xoá",
    "media.common.edit": "Sửa",
    "media.common.add": "Thêm",
  },
  en: {
    "media.gate.notProvisioned.title": "You do not have Media access",
    "media.gate.notProvisioned.desc": "Ask an admin to assign you a Media role or enter your personal Media API key.",
    "media.gate.connectionError": "Media connection is failing. The API key may be revoked or ads-crm is not responding.",
    "media.common.loading": "Loading...",
    "media.common.empty": "No data",
    "media.common.save": "Save",
    "media.common.cancel": "Cancel",
    "media.common.delete": "Delete",
    "media.common.edit": "Edit",
    "media.common.add": "Add",
  },
} as const;
```
(Các task sau bổ khoá `media.*` vào file này; luôn thêm cả vi và en.)

- [ ] **Step 6: `queries.ts`**

```ts
import { keepPreviousData, useQueries, useQuery } from "@tanstack/react-query";

import {
  getContent, getContentQuality, getMediaMe, getMediaSettings, getRatingsByChannel, getReportOverview,
  listContents, listEvaluations, listProposals, listTaxonomy, TAXONOMY_GROUPS,
  type MediaMe, type MediaTaxonomyItem, type TaxonomyGroup,
} from "@/lib/api/media";

export const mediaKeys = {
  all: ["media"] as const,
  me: ["media", "me"] as const,
  contents: (qs: string) => ["media", "contents", qs] as const,
  content: (id: number) => ["media", "content", id] as const,
  evaluations: (id: number) => ["media", "evaluations", id] as const,
  taxonomy: (group: TaxonomyGroup, inactive: boolean) => ["media", "taxonomy", group, inactive] as const,
  proposals: (qs: string) => ["media", "proposals", qs] as const,
  report: (name: string, qs: string) => ["media", "report", name, qs] as const,
  settings: ["media", "settings"] as const,
};

export function useMediaMe() {
  return useQuery<MediaMe>({ queryKey: mediaKeys.me, queryFn: ({ signal }) => getMediaMe(signal), staleTime: 60_000 });
}
/** Role hiệu lực để ẩn/hiện nút; null khi chưa tải hoặc chưa provisioned. */
export function useMediaRole() {
  return useMediaMe().data?.role ?? null;
}
export function useContents(sp: URLSearchParams) {
  const qs = sp.toString();
  return useQuery({ queryKey: mediaKeys.contents(qs), queryFn: ({ signal }) => listContents(sp, signal), placeholderData: keepPreviousData });
}
export function useContent(id: number | null) {
  return useQuery({ queryKey: mediaKeys.content(id ?? 0), queryFn: ({ signal }) => getContent(id as number, signal), enabled: id != null });
}
export function useEvaluations(contentId: number | null) {
  return useQuery({ queryKey: mediaKeys.evaluations(contentId ?? 0), queryFn: ({ signal }) => listEvaluations(contentId as number, signal), enabled: contentId != null });
}
export function useTaxonomy(group: TaxonomyGroup, includeInactive = false) {
  return useQuery({ queryKey: mediaKeys.taxonomy(group, includeInactive), queryFn: ({ signal }) => listTaxonomy(group, includeInactive, signal), staleTime: 5 * 60_000 });
}
/** 5 danh mục cho ô lọc/tag; map theo group. */
export function useAllTaxonomy(): Record<TaxonomyGroup, MediaTaxonomyItem[]> {
  const results = useQueries({
    queries: TAXONOMY_GROUPS.map((group) => ({
      queryKey: mediaKeys.taxonomy(group, false),
      queryFn: ({ signal }: { signal?: AbortSignal }) => listTaxonomy(group, false, signal),
      staleTime: 5 * 60_000,
    })),
  });
  return Object.fromEntries(TAXONOMY_GROUPS.map((g, i) => [g, results[i].data?.items ?? []])) as Record<TaxonomyGroup, MediaTaxonomyItem[]>;
}
export function useProposals(sp: URLSearchParams) {
  const qs = sp.toString();
  return useQuery({ queryKey: mediaKeys.proposals(qs), queryFn: ({ signal }) => listProposals(sp, signal), placeholderData: keepPreviousData });
}
export function useReportOverview(sp: URLSearchParams) {
  return useQuery({ queryKey: mediaKeys.report("overview", sp.toString()), queryFn: ({ signal }) => getReportOverview(sp, signal) });
}
export function useRatingsByChannel(sp: URLSearchParams) {
  return useQuery({ queryKey: mediaKeys.report("ratings", sp.toString()), queryFn: ({ signal }) => getRatingsByChannel(sp, signal) });
}
export function useContentQuality(sp: URLSearchParams) {
  return useQuery({ queryKey: mediaKeys.report("quality", sp.toString()), queryFn: ({ signal }) => getContentQuality(sp, signal) });
}
export function useMediaSettings() {
  return useQuery({ queryKey: mediaKeys.settings, queryFn: ({ signal }) => getMediaSettings(signal) });
}
```

- [ ] **Step 7: `MediaAccessGate.tsx`**

```tsx
import type { ReactNode } from "react";
import { ShieldOff } from "lucide-react";

import { EmptyState } from "@/components/ui/empty-state";
import { LogoSpinner } from "@/components/ui/logo-spinner";
import { useI18n } from "@/i18n";
import type { MediaMe } from "@/lib/api/media";

import { useMediaMe } from "../queries";

/** Bọc mọi trang Media: chờ /api/media/me, chặn khi chưa provisioned, banner khi kết nối lỗi. */
export function MediaAccessGate({ children }: { children: (me: MediaMe) => ReactNode }) {
  const { t } = useI18n();
  const { data: me, isLoading, error } = useMediaMe();
  if (isLoading) return <LogoSpinner />;
  if (error || !me) {
    return <EmptyState icon={ShieldOff} title={t("media.gate.connectionError")} description={error instanceof Error ? error.message : undefined} />;
  }
  if (!me.provisioned) {
    return <EmptyState icon={ShieldOff} title={t("media.gate.notProvisioned.title")} description={t("media.gate.notProvisioned.desc")} />;
  }
  return (
    <>
      {!me.connection_ok ? (
        <div className="mb-3 rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-200">
          {t("media.gate.connectionError")}
        </div>
      ) : null}
      {children(me)}
    </>
  );
}
```

- [ ] **Step 8: Khung 5 trang + route**

Mỗi trang trong `features/media/pages/` tạm là:
```tsx
import { PageHeader } from "@/components/ui/page-header";
import { useI18n } from "@/i18n";

import { MediaAccessGate } from "../components/MediaAccessGate";

export default function MediaContentsPage() {
  const { t } = useI18n();
  return (
    <div className="space-y-4">
      <PageHeader title={t("topnav.tabs.mediaContents")} />
      <MediaAccessGate>{() => null}</MediaAccessGate>
    </div>
  );
}
```
(tương tự cho Proposals/Reports/Taxonomy với labelKey tương ứng; `MediaSettingsPage` không bọc gate, chỉ `PageHeader title={t("topnav.tabs.mediaSettings")}`).

`app/routed-pages.tsx`: thêm 5 `lazy(() => import("@/features/media/pages/…"))` và 5 wrapper `export function MediaContentsRoute() { return <MediaContentsPage />; }` … `MediaSettingsRoute`.

`app/routes.tsx`: thêm sau khối `reports/*`:
```tsx
      { path: "media/contents", element: <Guard section="media-contents"><MediaContentsRoute /></Guard> },
      { path: "media/contents/:contentId", element: <Guard section="media-contents"><MediaContentsRoute /></Guard> },
      { path: "media/proposals", element: <Guard section="media-proposals"><MediaProposalsRoute /></Guard> },
      { path: "media/reports", element: <Guard section="media-reports"><MediaReportsRoute /></Guard> },
      { path: "media/taxonomy", element: <Guard section="media-taxonomy"><MediaTaxonomyRoute /></Guard> },
      { path: "settings/media", element: <Guard section="media-settings"><MediaSettingsRoute /></Guard> },
```
(Import các `*Route` từ `routed-pages`.) `Guard` dùng `requiredPermissionFor(section)` → với `media-settings` có `requires: "users"` nên gate theo `users`.

- [ ] **Step 9: Chạy → pass; commit**

Run: `VITEST src/features/media src/app src/components` → all passed (Sidebar test hiện có vẫn xanh vì FULL_PERMISSIONS sinh từ APP_MODULES). `TYPECHECK` → 0 lỗi.
```bash
git add frontend/src
git commit -m "feat(media): module Media trên sidebar, route /media/*, i18n, queries, MediaAccessGate, khung trang"
```

---

### Task 10: Trang Thư viện nội dung (đọc): filter bar, bảng, drawer chi tiết

**Files:**
- Create: `frontend/src/features/media/components/ContentFilterBar.tsx`, `ContentTable.tsx`, `ContentDetailSheet.tsx`, `RatingBadge.tsx`, `features/media/__tests__/MediaContentsPage.test.tsx`
- Modify: `features/media/pages/MediaContentsPage.tsx`, `i18n/messages/media.ts`

**Interfaces:**
- Consumes: `useContents`, `useContent`, `useAllTaxonomy`, `useMediaRole`, `filters.ts`, `thumbnailUrl`.
- Produces: `<ContentFilterBar value onChange taxonomy />`, `<ContentTable items publicBaseUrl onOpen(id) actions?: (c) => ReactNode />`, `<ContentDetailSheet contentId publicBaseUrl onClose actions?: ReactNode />` (slot `actions`/`evaluationsSlot` để Task 16–17, 21 cắm nút ghi), `<RatingBadge rating />`.

- [ ] **Step 1: Test trang**

`frontend/src/features/media/__tests__/MediaContentsPage.test.tsx`:
```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const fetchJson = vi.fn();
vi.mock("@/services/http", () => ({
  fetchJson: (...args: unknown[]) => fetchJson(...args),
  getValidAccessToken: vi.fn().mockResolvedValue(null),
}));

import MediaContentsPage from "@/features/media/pages/MediaContentsPage";
import { I18nProvider } from "@/i18n";
import { renderWithClient } from "@/test/utils";

const ME = { provisioned: true, mode: "shared", role: "viewer", remote_user: null, public_base_url: "https://m.example", connection_ok: true };
const CONTENT = {
  id: 12, content_code: "C0012", uploaded_at: "2026-09-01T00:00:00Z", title: "Video review A", url: "https://www.tiktok.com/@u/video/1",
  thumbnail_url: "", thumbnail_ext: "jpg", video_status: "success", video_ext: "mp4", video_size: 1, video_cached_at: null,
  source_id: 3, source: { id: 3, name: "TikTok", is_active: true }, content_type_id: 2, content_type: { id: 2, name: "Review", is_active: true },
  overall_rating: "good", overall_score: 4.25, overall_note: "", products: [{ id: 1, code: "SP1", name: "Serum", is_active: true }],
  categories: [], ad_evaluations: [], created_by: 7, creator: null, updated_by: 7, created_at: "", updated_at: "",
};

function route(url: string) {
  if (url.startsWith("/api/media/me")) return ME;
  if (/\/api\/media\/contents\/12$/.test(url)) return CONTENT;
  if (url.startsWith("/api/media/contents")) return { items: [CONTENT], total: 1, page: 1, page_size: 20 };
  return { items: [] }; // taxonomy
}

function renderPage(path = "/media/contents") {
  fetchJson.mockImplementation((url: string) => Promise.resolve(route(url)));
  return renderWithClient(
    <I18nProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/media/contents" element={<MediaContentsPage />} />
          <Route path="/media/contents/:contentId" element={<MediaContentsPage />} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  );
}

describe("MediaContentsPage", () => {
  it("hiện bảng với thumbnail hotlink và badge rating", async () => {
    renderPage();
    expect(await screen.findByText("Video review A")).toBeInTheDocument();
    const img = screen.getByRole("img", { name: "Video review A" }) as HTMLImageElement;
    expect(img.src).toBe("https://m.example/api/v1/contents/12/thumbnail");
    expect(screen.getByText("Tốt")).toBeInTheDocument();
    // viewer: không có nút Thêm nội dung
    expect(screen.queryByRole("button", { name: /Thêm nội dung/ })).toBeNull();
  });
  it("mở chi tiết theo URL /media/contents/:id", async () => {
    renderPage("/media/contents/12");
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(await screen.findByText("C0012")).toBeInTheDocument();
  });
  it("đổi filter rating ghi vào query string", async () => {
    renderPage();
    await screen.findByText("Video review A");
    fireEvent.click(screen.getByRole("button", { name: /Tốt/ }));
    const last = fetchJson.mock.calls.map((c) => String(c[0])).filter((u) => u.includes("/api/media/contents?")).pop();
    expect(last).toContain("overall_rating=good");
  });
});
```

- [ ] **Step 2: Chạy → fail**.

- [ ] **Step 3: i18n bổ sung (`media.ts`, vi/en)**

```ts
    "media.rating.good": "Tốt", "media.rating.average": "Trung bình", "media.rating.bad": "Kém", "media.rating.unknown": "Chưa đánh giá",
    "media.contents.add": "Thêm nội dung",
    "media.contents.filter.source": "Nguồn", "media.contents.filter.type": "Loại nội dung", "media.contents.filter.rating": "Rating tổng",
    "media.contents.filter.product": "Sản phẩm", "media.contents.filter.category": "Danh mục", "media.contents.filter.channel": "Kênh ads",
    "media.contents.filter.adRating": "Điểm ads", "media.contents.filter.from": "Upload từ", "media.contents.filter.to": "đến",
    "media.contents.filter.sort": "Sắp xếp", "media.contents.sort.uploaded_at": "Ngày upload", "media.contents.sort.title": "Tiêu đề", "media.contents.sort.rating": "Rating",
    "media.contents.col.code": "Mã", "media.contents.col.title": "Tiêu đề", "media.contents.col.source": "Nguồn", "media.contents.col.type": "Loại",
    "media.contents.col.tags": "Tag", "media.contents.col.rating": "Rating", "media.contents.col.uploaded": "Upload",
    "media.contents.detail.title": "Chi tiết nội dung", "media.contents.detail.openSource": "Mở link gốc",
    "media.contents.detail.video": "Video", "media.contents.detail.evaluations": "Đánh giá ads", "media.contents.detail.noEvaluations": "Chưa có đánh giá",
    "media.video.idle": "Chưa cache", "media.video.pending": "Đang cache", "media.video.success": "Đã cache", "media.video.failed": "Cache lỗi",
```
en tương ứng (`Good/Average/Poor/Not rated`, `Add content`, `Source/Content type/Overall rating/Product/Category/Ad channel/Ad rating/Uploaded from/to/Sort/Upload date/Title/Rating`, `Code/Title/Source/Type/Tags/Rating/Uploaded`, `Content detail/Open source link/Video/Ad evaluations/No evaluations yet`, `Not cached/Caching/Cached/Cache failed`).

- [ ] **Step 4: `RatingBadge.tsx`**

```tsx
import { Badge } from "@/components/ui/badge";
import { useI18n } from "@/i18n";
import type { OverallRating } from "@/lib/api/media";

const TONE: Record<OverallRating, string> = {
  good: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  average: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  bad: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
  unknown: "bg-muted text-muted-foreground",
};

export function RatingBadge({ rating, score }: { rating: OverallRating; score?: number | null }) {
  const { t } = useI18n();
  return (
    <Badge variant="outline" className={`border-transparent ${TONE[rating]}`}>
      {t(`media.rating.${rating}`)}
      {score != null && rating !== "unknown" ? <span className="ml-1 tabular-nums">{score.toFixed(2)}</span> : null}
    </Badge>
  );
}
```

- [ ] **Step 5: `ContentFilterBar.tsx`**

```tsx
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { MultiSelect } from "@/components/ui/multi-select";
import { useI18n } from "@/i18n";
import type { MediaTaxonomyItem, TaxonomyGroup } from "@/lib/api/media";

import type { ContentFilters, ContentSort } from "../filters";

type Props = {
  value: ContentFilters;
  onChange: (next: ContentFilters) => void;
  taxonomy: Record<TaxonomyGroup, MediaTaxonomyItem[]>;
};

const RATINGS = ["good", "average", "bad", "unknown"] as const;
const AD_RATINGS = ["0", "0.5", "1", "1.5", "2", "2.5", "3", "3.5", "4", "4.5", "5"];
const toOptions = (items: MediaTaxonomyItem[]) => items.map((i) => ({ value: String(i.id), label: i.code ? `${i.code} – ${i.name}` : i.name }));

export function ContentFilterBar({ value, onChange, taxonomy }: Props) {
  const { t } = useI18n();
  // Mọi thay đổi filter đưa về trang 1.
  const set = <K extends keyof ContentFilters>(key: K, v: ContentFilters[K]) => onChange({ ...value, [key]: v, page: 1 });
  const field = (label: string, node: React.ReactNode) => (
    <div className="flex min-w-[10rem] flex-col gap-1">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {node}
    </div>
  );
  return (
    <Card>
      <CardContent className="flex flex-wrap items-end gap-3 pt-6">
        {field(t("media.contents.filter.source"), <MultiSelect options={toOptions(taxonomy["content-sources"])} selected={value.sourceIds} onChange={(v) => set("sourceIds", v)} placeholder={t("media.contents.filter.source")} menuPortal />)}
        {field(t("media.contents.filter.type"), <MultiSelect options={toOptions(taxonomy["content-types"])} selected={value.contentTypeIds} onChange={(v) => set("contentTypeIds", v)} placeholder={t("media.contents.filter.type")} menuPortal />)}
        {field(t("media.contents.filter.rating"), (
          <div className="flex gap-1" role="group" aria-label={t("media.contents.filter.rating")}>
            {RATINGS.map((r) => {
              const on = value.overallRatings.includes(r);
              return (
                <button key={r} type="button" aria-pressed={on}
                  className={`rounded-md border px-2 py-1 text-xs ${on ? "border-primary bg-primary/10 text-primary" : "border-input text-muted-foreground"}`}
                  onClick={() => set("overallRatings", on ? value.overallRatings.filter((x) => x !== r) : [...value.overallRatings, r])}>
                  {t(`media.rating.${r}`)}
                </button>
              );
            })}
          </div>
        ))}
        {field(t("media.contents.filter.product"), <MultiSelect options={toOptions(taxonomy.products)} selected={value.productIds} onChange={(v) => set("productIds", v)} placeholder={t("media.contents.filter.product")} menuPortal />)}
        {field(t("media.contents.filter.category"), <MultiSelect options={toOptions(taxonomy["product-categories"])} selected={value.categoryIds} onChange={(v) => set("categoryIds", v)} placeholder={t("media.contents.filter.category")} menuPortal />)}
        {field(t("media.contents.filter.channel"), <MultiSelect options={toOptions(taxonomy["ad-channels"])} selected={value.channelIds} onChange={(v) => set("channelIds", v)} placeholder={t("media.contents.filter.channel")} menuPortal />)}
        {/* Điểm ads chỉ có tác dụng khi đã chọn kênh (tài liệu ads-crm §3.4). */}
        {value.channelIds.length > 0
          ? field(t("media.contents.filter.adRating"), <MultiSelect options={AD_RATINGS.map((r) => ({ value: r, label: r }))} selected={value.adRatings} onChange={(v) => set("adRatings", v)} placeholder={t("media.contents.filter.adRating")} menuPortal />)
          : null}
        {field(t("media.contents.filter.from"), <Input type="date" value={value.uploadedFrom} onChange={(e) => set("uploadedFrom", e.target.value)} className="h-9" />)}
        {field(t("media.contents.filter.to"), <Input type="date" value={value.uploadedTo} onChange={(e) => set("uploadedTo", e.target.value)} className="h-9" />)}
        {field(t("media.contents.filter.sort"), (
          <select className="h-9 rounded-md border border-input bg-background px-2 text-sm" value={`${value.sort}:${value.order}`}
            onChange={(e) => { const [sort, order] = e.target.value.split(":") as [ContentSort, "asc" | "desc"]; onChange({ ...value, sort, order, page: 1 }); }}>
            {(["uploaded_at", "title", "rating"] as const).flatMap((s) => (["desc", "asc"] as const).map((o) => (
              <option key={`${s}:${o}`} value={`${s}:${o}`}>{t(`media.contents.sort.${s}`)} {o === "desc" ? "↓" : "↑"}</option>
            )))}
          </select>
        ))}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 6: `ContentTable.tsx`**

```tsx
import type { ReactNode } from "react";
import { Film } from "lucide-react";

import { EmptyState } from "@/components/ui/empty-state";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { thumbnailUrl, type MediaContent } from "@/lib/api/media";

import { RatingBadge } from "./RatingBadge";

type Props = {
  items: MediaContent[];
  publicBaseUrl: string;
  onOpen: (id: number) => void;
  /** Cột hành động cuối hàng (Task 16 cắm menu nhân bản/xoá). */
  rowActions?: (content: MediaContent) => ReactNode;
};

export function Thumb({ content, publicBaseUrl, className = "h-12 w-9" }: { content: MediaContent; publicBaseUrl: string; className?: string }) {
  const src = thumbnailUrl(publicBaseUrl, content);
  return (
    <span className={`grid shrink-0 place-items-center overflow-hidden rounded bg-muted ${className}`}>
      {src ? (
        <img src={src} alt={content.title} loading="lazy" className="h-full w-full object-cover"
          onError={(e) => { (e.currentTarget as HTMLImageElement).hidden = true; }} />
      ) : <Film className="h-4 w-4 text-muted-foreground" aria-hidden />}
    </span>
  );
}

export function ContentTable({ items, publicBaseUrl, onOpen, rowActions }: Props) {
  const { t } = useI18n();
  if (items.length === 0) return <EmptyState icon={Film} title={t("media.common.empty")} />;
  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-12" />
            <TableHead>{t("media.contents.col.code")}</TableHead>
            <TableHead>{t("media.contents.col.title")}</TableHead>
            <TableHead>{t("media.contents.col.source")}</TableHead>
            <TableHead>{t("media.contents.col.type")}</TableHead>
            <TableHead>{t("media.contents.col.tags")}</TableHead>
            <TableHead>{t("media.contents.col.rating")}</TableHead>
            <TableHead>{t("media.contents.col.uploaded")}</TableHead>
            {rowActions ? <TableHead className="w-10" /> : null}
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((c) => (
            <TableRow key={c.id} className="cursor-pointer" onClick={() => onOpen(c.id)}>
              <TableCell><Thumb content={c} publicBaseUrl={publicBaseUrl} /></TableCell>
              <TableCell className="font-mono text-xs">{c.content_code}</TableCell>
              <TableCell className="max-w-[24rem] truncate font-medium">{c.title}</TableCell>
              <TableCell>{c.source?.name ?? "—"}</TableCell>
              <TableCell>{c.content_type?.name ?? "—"}</TableCell>
              <TableCell className="max-w-[16rem] truncate text-xs text-muted-foreground">
                {[...c.products.map((p) => p.code || p.name), ...c.categories.map((k) => k.name)].join(", ") || "—"}
              </TableCell>
              <TableCell><RatingBadge rating={c.overall_rating} score={c.overall_score} /></TableCell>
              <TableCell className="whitespace-nowrap text-xs">{c.uploaded_at.slice(0, 10)}</TableCell>
              {rowActions ? <TableCell onClick={(e) => e.stopPropagation()}>{rowActions(c)}</TableCell> : null}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
```

- [ ] **Step 7: `ContentDetailSheet.tsx`**

```tsx
import type { ReactNode } from "react";
import { ExternalLink } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { LogoSpinner } from "@/components/ui/logo-spinner";
import { Sheet, SheetBody, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useI18n } from "@/i18n";
import type { MediaContent } from "@/lib/api/media";

import { useContent } from "../queries";
import { Thumb } from "./ContentTable";
import { RatingBadge } from "./RatingBadge";

type Props = {
  contentId: number | null;
  publicBaseUrl: string;
  onClose: () => void;
  /** Nút hành động ở header (sửa upload-info, sửa tag, tải/cache video…) — task sau cắm. */
  actions?: (content: MediaContent) => ReactNode;
  /** Khối đánh giá ads (Task 17 thay bằng list có form). */
  evaluationsSlot?: (content: MediaContent) => ReactNode;
};

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[8rem_1fr] gap-2 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="min-w-0 break-words">{children}</span>
    </div>
  );
}

export function ContentDetailSheet({ contentId, publicBaseUrl, onClose, actions, evaluationsSlot }: Props) {
  const { t } = useI18n();
  const { data: c, isLoading } = useContent(contentId);
  return (
    <Sheet open={contentId != null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <SheetContent side="right" className="sm:max-w-xl lg:max-w-2xl">
        <SheetHeader>
          <SheetTitle>{c?.title ?? t("media.contents.detail.title")}</SheetTitle>
          <SheetDescription className="font-mono text-xs">{c?.content_code ?? ""}</SheetDescription>
        </SheetHeader>
        <SheetBody>
          {isLoading || !c ? <LogoSpinner /> : (
            <div className="space-y-4">
              <div className="flex gap-4">
                <Thumb content={c} publicBaseUrl={publicBaseUrl} className="h-40 w-28" />
                <div className="flex-1 space-y-2">
                  <Row label={t("media.contents.col.source")}>{c.source?.name ?? "—"}</Row>
                  <Row label={t("media.contents.col.type")}>{c.content_type?.name ?? "—"}</Row>
                  <Row label={t("media.contents.col.uploaded")}>{c.uploaded_at.slice(0, 10)}</Row>
                  <Row label={t("media.contents.col.rating")}><RatingBadge rating={c.overall_rating} score={c.overall_score} /></Row>
                  <Row label={t("media.contents.detail.video")}><Badge variant="outline">{t(`media.video.${c.video_status}`)}</Badge></Row>
                  <a href={c.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sm text-primary underline">
                    <ExternalLink className="h-3.5 w-3.5" /> {t("media.contents.detail.openSource")}
                  </a>
                </div>
              </div>
              {actions ? <div className="flex flex-wrap gap-2">{actions(c)}</div> : null}
              <Row label={t("media.contents.filter.product")}>{c.products.map((p) => <Badge key={p.id} variant="secondary" className="mr-1">{p.code || p.name}</Badge>)}{c.products.length === 0 ? "—" : null}</Row>
              <Row label={t("media.contents.filter.category")}>{c.categories.map((k) => <Badge key={k.id} variant="secondary" className="mr-1">{k.name}</Badge>)}{c.categories.length === 0 ? "—" : null}</Row>
              <section className="space-y-2">
                <h3 className="text-sm font-semibold">{t("media.contents.detail.evaluations")}</h3>
                {evaluationsSlot ? evaluationsSlot(c) : (
                  c.ad_evaluations.length === 0 ? <p className="text-sm text-muted-foreground">{t("media.contents.detail.noEvaluations")}</p> : (
                    <ul className="space-y-1 text-sm">
                      {c.ad_evaluations.map((e) => (
                        <li key={e.id} className="rounded border px-3 py-2">
                          <span className="font-medium tabular-nums">{e.rating.toFixed(1)}</span> · {e.evaluator_name} · {e.evaluated_at.slice(0, 10)}
                          {e.note ? <p className="text-muted-foreground">{e.note}</p> : null}
                        </li>
                      ))}
                    </ul>
                  )
                )}
              </section>
            </div>
          )}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}
```
(Kiểm tra `SheetContent` có prop `side` nhận `"right"` — `OrdersFilterSheet` dùng `"left"`; nếu chỉ hỗ trợ left/right theo `sheet.tsx` thì giữ, nếu không thì bỏ prop.)

- [ ] **Step 8: `MediaContentsPage.tsx`**

```tsx
import { useCallback, useMemo } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { LogoSpinner } from "@/components/ui/logo-spinner";
import { PageHeader } from "@/components/ui/page-header";
import { TablePagination } from "@/components/ui/table-pagination";
import { useI18n } from "@/i18n";

import { ContentDetailSheet } from "../components/ContentDetailSheet";
import { ContentFilterBar } from "../components/ContentFilterBar";
import { ContentTable } from "../components/ContentTable";
import { MediaAccessGate } from "../components/MediaAccessGate";
import { PAGE_SIZE_OPTIONS, filtersFromSearchParams, filtersToSearchParams, type ContentFilters } from "../filters";
import { useAllTaxonomy, useContents } from "../queries";

export default function MediaContentsPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { contentId } = useParams<{ contentId?: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => filtersFromSearchParams(searchParams), [searchParams]);
  const setFilters = useCallback((next: ContentFilters) => setSearchParams(filtersToSearchParams(next), { replace: true }), [setSearchParams]);
  const apiParams = useMemo(() => filtersToSearchParams(filters), [filters]);
  const { data, isLoading } = useContents(apiParams);
  const taxonomy = useAllTaxonomy();
  const selectedId = contentId ? Number(contentId) : null;
  const openDetail = (id: number) => navigate({ pathname: `/media/contents/${id}`, search: searchParams.toString() });
  const closeDetail = () => navigate({ pathname: "/media/contents", search: searchParams.toString() });

  return (
    <div className="space-y-4">
      <MediaAccessGate>
        {(me) => (
          <>
            <PageHeader title={t("topnav.tabs.mediaContents")} />
            <ContentFilterBar value={filters} onChange={setFilters} taxonomy={taxonomy} />
            {isLoading || !data ? <LogoSpinner /> : (
              <>
                <ContentTable items={data.items} publicBaseUrl={me.public_base_url} onOpen={openDetail} />
                <TablePagination total={data.total} page={filters.page} pageSize={filters.pageSize}
                  onPageChange={(page) => setFilters({ ...filters, page })}
                  pageSizeOptions={PAGE_SIZE_OPTIONS} onPageSizeChange={(pageSize) => setFilters({ ...filters, pageSize, page: 1 })} />
              </>
            )}
            <ContentDetailSheet contentId={selectedId} publicBaseUrl={me.public_base_url} onClose={closeDetail} />
          </>
        )}
      </MediaAccessGate>
    </div>
  );
}
```

- [ ] **Step 9: Chạy → pass; typecheck; commit**

Run: `VITEST src/features/media` → pass. `TYPECHECK`.
```bash
git add frontend/src/features/media frontend/src/i18n/messages/media.ts
git commit -m "feat(media): trang Thư viện nội dung (filter trên URL, bảng, drawer chi tiết)"
```

---

### Task 11: Trang Đề xuất nội dung (đọc)

**Files:**
- Create: `features/media/components/ProposalTable.tsx`, `features/media/proposal-filters.ts`, `features/media/__tests__/MediaProposalsPage.test.tsx`
- Modify: `features/media/pages/MediaProposalsPage.tsx`, `i18n/messages/media.ts`

**Interfaces:**
- Produces: `ProposalFilters {statuses: string[]; productIds: string[]; q: string; page: number; pageSize: number}`, `proposalFiltersToParams/FromParams`; `<ProposalTable items onOpenContent(id) rowActions? />`. Slot `headerActions`/`rowActions` để Task 18 cắm nút.

- [ ] **Step 1: Test**

```tsx
// frontend/src/features/media/__tests__/MediaProposalsPage.test.tsx — cùng bộ mock như MediaContentsPage.test
// fetchJson: /api/media/me → ME(viewer); /api/media/proposals → {items:[PROPOSAL], total:1, page:1, page_size:20}; còn lại → {items:[]}
// PROPOSAL = { id: 4, main_key: "Từ khoá chính", idea: "Ý tưởng", due_date: "2026-10-01T00:00:00Z", status: "pending",
//   product_id: 5, product: { id: 5, code: "SP5", name: "Serum", is_active: true }, content_id: null, content: null,
//   created_by: 7, creator: { id: 7, name: "Hùng", username: "hung" }, created_at: "", updated_at: "" }
it("hiện bảng đề xuất với trạng thái dịch", async () => {
  renderPage();
  expect(await screen.findByText("Từ khoá chính")).toBeInTheDocument();
  expect(screen.getByText("Chờ xử lý")).toBeInTheDocument();
  expect(screen.getByText("SP5")).toBeInTheDocument();
});
it("ô tìm kiếm gửi q lên API", async () => {
  renderPage();
  await screen.findByText("Từ khoá chính");
  fireEvent.change(screen.getByPlaceholderText(/Tìm theo từ khoá/), { target: { value: "abc" } });
  await waitFor(() => expect(fetchJson.mock.calls.some((c) => String(c[0]).includes("q=abc"))).toBe(true));
});
```
(Viết đầy đủ theo khuôn file test Task 10; import `waitFor`.)

- [ ] **Step 2: Chạy → fail.**

- [ ] **Step 3: i18n**

vi: `"media.proposals.status.pending": "Chờ xử lý"`, `in_progress: "Đang làm"`, `done: "Hoàn thành"`, `cancelled: "Đã huỷ"`, `"media.proposals.search": "Tìm theo từ khoá / ý tưởng"`, `"media.proposals.add": "Thêm đề xuất"`, cột: `"media.proposals.col.mainKey": "Từ khoá chính"`, `idea: "Ý tưởng"`, `product: "Sản phẩm"`, `due: "Hạn"`, `status: "Trạng thái"`, `creator: "Người tạo"`, `content: "Nội dung"`, `"media.proposals.filter.status": "Trạng thái"`, `"media.proposals.filter.product": "Sản phẩm"`. en tương ứng (`Pending/In progress/Done/Cancelled`, `Search key / idea`, `Add proposal`, `Main key/Idea/Product/Due/Status/Creator/Content`).

- [ ] **Step 4: `proposal-filters.ts`**

```ts
export type ProposalFilters = { statuses: string[]; productIds: string[]; q: string; page: number; pageSize: number };
export const DEFAULT_PROPOSAL_FILTERS: ProposalFilters = { statuses: [], productIds: [], q: "", page: 1, pageSize: 20 };

export function proposalFiltersToParams(f: ProposalFilters): URLSearchParams {
  const sp = new URLSearchParams();
  f.statuses.forEach((s) => sp.append("status", s));
  f.productIds.forEach((p) => sp.append("product_id", p));
  if (f.q.trim()) sp.set("q", f.q.trim());
  if (f.page !== 1) sp.set("page", String(f.page));
  if (f.pageSize !== 20) sp.set("page_size", String(f.pageSize));
  return sp;
}
export function proposalFiltersFromParams(sp: URLSearchParams): ProposalFilters {
  const page = Number(sp.get("page") ?? "1");
  const size = Number(sp.get("page_size") ?? "20");
  return {
    statuses: sp.getAll("status"), productIds: sp.getAll("product_id"), q: sp.get("q") ?? "",
    page: Number.isInteger(page) && page > 0 ? page : 1,
    pageSize: Number.isInteger(size) && size >= 1 && size <= 100 ? size : 20,
  };
}
```

- [ ] **Step 5: `ProposalTable.tsx` + trang**

`ProposalTable`: bảng `Table` với cột main_key (đậm), idea (truncate), product (`code` dạng Badge), due_date (`slice(0,10)`), status (`Badge` với `t("media.proposals.status."+status)`), creator name, content (nút link "C0012" gọi `onOpenContent(content.id)` khi có), cột `rowActions?` cuối. Rỗng → `EmptyState`.

`MediaProposalsPage`: giống `MediaContentsPage` — filter trên URL (`useSearchParams`), thanh lọc gồm `MultiSelect` trạng thái (4 giá trị), `MultiSelect` sản phẩm (từ `useAllTaxonomy().products`), `Input` tìm kiếm (placeholder `t("media.proposals.search")`, debounce 300ms bằng `useDebounce` từ `@/hooks/useDebounce`), `useProposals(params)`, `TablePagination`, `onOpenContent = (id) => navigate(`/media/contents/${id}`)`. Header `PageHeader actions={headerActions}` với `headerActions` để trống (Task 18 cắm).

- [ ] **Step 6: Chạy → pass; typecheck; commit** — `feat(media): trang Đề xuất nội dung (đọc)`.

---

### Task 12: Trang Báo cáo Media

**Files:**
- Create: `features/media/components/ReportKpiRow.tsx`, `ReportTables.tsx`, `features/media/__tests__/MediaReportsPage.test.tsx`
- Modify: `features/media/pages/MediaReportsPage.tsx`, `i18n/messages/media.ts`

- [ ] **Step 1: Test**

Mock: `/api/media/reports/overview` → `{ total_contents: 320, missing_tags: 12, missing_evaluations: 45, content_by_type: [{ name: "Review", total: 120 }] }`; `ratings-by-channel` → `{ items: [{ channel: "Facebook Ads", avg_rating: 3.8, total: 42 }] }`; `content-quality` → `{ missing_tags: [{ id: 9, content_code: "C009", title: "Thiếu tag", issue: "missing_tags" }], missing_evaluations: [] }`.
```tsx
it("hiện KPI, bảng theo loại, rating theo kênh và danh sách chất lượng", async () => {
  renderPage();
  expect(await screen.findByText("320")).toBeInTheDocument();
  expect(screen.getByText("Review")).toBeInTheDocument();
  expect(screen.getByText("Facebook Ads")).toBeInTheDocument();
  expect(screen.getByText("Thiếu tag")).toBeInTheDocument();
  expect(screen.getByText(/channel_id/)).toBeInTheDocument(); // ghi chú giới hạn §5.3
});
it("đổi khoảng ngày gửi uploaded_from/uploaded_to", async () => {
  renderPage();
  await screen.findByText("320");
  // DateFilter: chọn preset "last_30_days" đã là mặc định → mọi request đều có uploaded_from
  expect(fetchJson.mock.calls.some((c) => /reports\/overview\?uploaded_from=\d{4}-\d{2}-\d{2}&uploaded_to=/.test(String(c[0])))).toBe(true);
});
```

- [ ] **Step 2: Chạy → fail.**

- [ ] **Step 3: i18n**

vi: `"media.reports.kpi.total": "Tổng nội dung"`, `"media.reports.kpi.missingTags": "Thiếu tag"`, `"media.reports.kpi.missingEvaluations": "Thiếu đánh giá"`, `"media.reports.byType": "Theo loại nội dung"`, `"media.reports.byChannel": "Rating theo kênh ads"`, `"media.reports.byChannel.note": "API ads-crm hiện chưa cho đặt channel_id khi tạo đánh giá, nên bảng này có thể rỗng."`, `"media.reports.quality.missingTags": "Nội dung thiếu tag"`, `"media.reports.quality.missingEvaluations": "Nội dung thiếu đánh giá"`, `"media.reports.quality.limit": "Tối đa 50 dòng mỗi danh sách."`, `"media.reports.col.name": "Tên"`, `"media.reports.col.total": "Số lượng"`, `"media.reports.col.avg": "Rating TB"`. en tương ứng.

- [ ] **Step 4: Components + trang**

`ReportKpiRow({ overview })`: 3 `Card` (grid 3 cột) hiện số lớn `tabular-nums` với nhãn.
`ReportTables({ overview, ratings, quality, onOpenContent })`: 4 bảng nhỏ (`Table`): theo loại; rating theo kênh (avg `toFixed(2)`, kèm `<p className="text-xs text-muted-foreground">` ghi chú `byChannel.note`); hai bảng chất lượng — mỗi dòng `content_code` là `<button>` gọi `onOpenContent(id)`, dưới bảng ghi `quality.limit`.
`MediaReportsPage`: `DateFilter` (mặc định `last_30_days` như `FoxiaReportPage`), params `uploaded_from`/`uploaded_to`, 3 hook `useReportOverview/useRatingsByChannel/useContentQuality`, `onOpenContent = (id) => navigate(`/media/contents/${id}`)`, bọc `MediaAccessGate`.

- [ ] **Step 5: Chạy → pass; typecheck; commit** — `feat(media): trang Báo cáo Media`.

---

### Task 13: Trang Danh mục Media (đọc, 5 tab)

**Files:**
- Create: `features/media/components/TaxonomyTabs.tsx`, `features/media/__tests__/MediaTaxonomyPage.test.tsx`
- Modify: `features/media/pages/MediaTaxonomyPage.tsx`, `i18n/messages/media.ts`

**Interfaces:**
- Produces: `<TaxonomyTabs group onGroupChange includeInactive onIncludeInactiveChange role headerActions?: ReactNode rowActions?: (item) => ReactNode />` — dùng `useTaxonomy(group, includeInactive)`.

- [ ] **Step 1: Test**

Mock `/api/media/<group>` → `{ items: [{ id: 1, name: "Review", group: "video", is_active: true }] }` (mọi group).
```tsx
it("5 tab, bảng theo tab; viewer không thấy toggle 'hiện cả đã tắt'", async () => {
  renderPage(); // ME role viewer
  expect(await screen.findByText("Review")).toBeInTheDocument();
  for (const label of ["Loại nội dung", "Sản phẩm", "Danh mục SP", "Kênh ads", "Nguồn"]) expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
  expect(screen.queryByLabelText(/Hiện cả đã tắt/)).toBeNull();
});
it("admin thấy toggle và bật gửi include_inactive=true", async () => {
  renderPage({ ...ME, role: "admin" });
  await screen.findByText("Review");
  fireEvent.click(screen.getByLabelText(/Hiện cả đã tắt/));
  await waitFor(() => expect(fetchJson.mock.calls.some((c) => String(c[0]).includes("include_inactive=true"))).toBe(true));
});
```

- [ ] **Step 2: Chạy → fail.**

- [ ] **Step 3: i18n**

vi: `"media.taxonomy.group.content-types": "Loại nội dung"`, `products: "Sản phẩm"`, `"product-categories": "Danh mục SP"`, `"ad-channels": "Kênh ads"`, `"content-sources": "Nguồn"`, `"media.taxonomy.includeInactive": "Hiện cả đã tắt"`, `"media.taxonomy.col.name": "Tên"`, `"media.taxonomy.col.code": "Mã"`, `"media.taxonomy.col.group": "Nhóm"`, `"media.taxonomy.col.active": "Đang dùng"`, `"media.taxonomy.add": "Thêm"`. en tương ứng (`Content types/Products/Product categories/Ad channels/Sources`, `Show inactive`, `Name/Code/Group/Active`, `Add`).

- [ ] **Step 4: Component + trang**

`TaxonomyTabs`: `Tabs` (`components/ui/tabs`) với 5 `TabItem` (label = `t("media.taxonomy.group."+g)`), `content` của tab active là bảng `Table` cột id/name/(code nếu products)/(group nếu content-types)/is_active (`Switch` chỉ hiển thị, disabled) + cột `rowActions?`. Trên bảng: hàng công cụ gồm `headerActions` và, khi `mediaCan(role, "taxonomy.include_inactive")`, một `Checkbox` có `<label>` "Hiện cả đã tắt" nối `includeInactive`. Mỗi tab dùng `useTaxonomy(group, includeInactive)`.
`MediaTaxonomyPage`: state `group` (mặc định `"content-types"`), `includeInactive`, lấy `role` từ `me.role` trong gate.

- [ ] **Step 5: Chạy → pass; typecheck; commit** — `feat(media): trang Danh mục Media (5 tab, đọc)`.

---

### Task 14: Trang Kết nối Media + ô Vai trò/API key Media trong dialog Người dùng

**Files:**
- Create: `features/media/__tests__/MediaSettingsPage.test.tsx`
- Modify: `features/media/pages/MediaSettingsPage.tsx`, `features/media/queries.ts` (mutation), `features/users/types.ts`, `features/users/UserCreateDialog.tsx`, `features/users/UserEditDialog.tsx`, `features/users/components/UserManagement.tsx`, `features/hr/components/EmployeeAccountCreateDialog.tsx`, `i18n/messages/users.ts`, `i18n/messages/media.ts`

- [ ] **Step 1: Test settings page**

Mock `/api/media/settings` GET → `{ base_url: "https://m", shared_api_key_set: true, shared_api_key_prefix: "dak_sharedke", is_active: true, last_check_at: null, last_check_ok: null, last_check_error: "", shared_key_role: "", shared_key_username: "" }`; POST `/settings/test` → `{ ok: true, role: "uploader", username: "svc", warning: "Key chung không phải role admin..." }`.
```tsx
it("hiện prefix key, nút kiểm tra hiện role + cảnh báo", async () => {
  renderPage();
  expect(await screen.findByText(/dak_sharedke/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Kiểm tra kết nối/ }));
  expect(await screen.findByText(/uploader/)).toBeInTheDocument();
  expect(screen.getByText(/không phải role admin/)).toBeInTheDocument();
});
it("lưu với ô key trống không gửi shared_api_key", async () => {
  renderPage();
  await screen.findByText(/dak_sharedke/);
  fireEvent.click(screen.getByRole("button", { name: /^Lưu$/ }));
  await waitFor(() => {
    const patch = fetchJson.mock.calls.find((c) => (c[1] as RequestInit | undefined)?.method === "PATCH");
    expect(patch).toBeTruthy();
    expect(JSON.parse(String((patch![1] as RequestInit).body))).not.toHaveProperty("shared_api_key");
  });
});
```

- [ ] **Step 2: Chạy → fail.**

- [ ] **Step 3: Mutation hooks (thêm vào `queries.ts`)**

```ts
export function useUpdateMediaSettings() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: updateMediaSettings, onSuccess: () => { void qc.invalidateQueries({ queryKey: mediaKeys.settings }); void qc.invalidateQueries({ queryKey: mediaKeys.me }); } });
}
export function useTestMediaSettings() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: testMediaSettings, onSuccess: () => void qc.invalidateQueries({ queryKey: mediaKeys.settings }) });
}
```
(import `useMutation, useQueryClient`, `updateMediaSettings, testMediaSettings`.)

- [ ] **Step 4: `MediaSettingsPage.tsx`**

Form (`Card`): `Input` base_url; `Input type="password"` key với hint "Để trống = giữ key hiện tại" + text `shared_api_key_prefix…` khi `shared_api_key_set` + nút "Xoá key" (gửi `shared_api_key: null`); `Switch` is_active; nút **Lưu** (gửi chỉ field đổi: `base_url` nếu khác, `shared_api_key` chỉ khi ô không rỗng, `is_active` nếu khác) → toast; nút **Kiểm tra kết nối** → hiện kết quả `{ok, role, username, warning|error}` trong khối dưới form, màu theo `ok`. Hiện `last_check_at/ok/error` từ settings. i18n `media.settings.*` (vi/en): `title`, `baseUrl`, `sharedKey`, `sharedKeyHint`, `clearKey`, `active`, `save`, `test`, `testOk`, `testFail`, `lastCheck`.

- [ ] **Step 5: Dialog Người dùng**

`features/users/types.ts` `AdminUserRow` thêm: `media_role?: "" | "viewer" | "marketing" | "uploader" | "admin"; media_api_key_set?: boolean; media_api_key_prefix?: string;`.

`UserEditDialog.tsx` & `UserCreateDialog.tsx`: thêm props `mediaRole: string; mediaApiKey: string; mediaApiKeySet?: boolean; mediaApiKeyPrefix?: string; onMediaRoleChange(v); onMediaApiKeyChange(v); onClearMediaApiKey?(): void` và UI ngay sau ô Zenify:
```tsx
          <div>
            <label htmlFor="user-edit-media-role" className="mb-1 block text-sm text-muted-foreground">{t("users.fields.mediaRole")}</label>
            <Select value={mediaRole || "none"} onValueChange={(v) => onMediaRoleChange(v === "none" ? "" : v)}>
              <SelectTrigger id="user-edit-media-role"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="none">{t("users.fields.mediaRole.none")}</SelectItem>
                <SelectItem value="viewer">Viewer</SelectItem>
                <SelectItem value="marketing">Marketing</SelectItem>
                <SelectItem value="uploader">Uploader</SelectItem>
                <SelectItem value="admin">Admin</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <label htmlFor="user-edit-media-key" className="mb-1 block text-sm text-muted-foreground">{t("users.fields.mediaApiKey")}</label>
            <Input id="user-edit-media-key" type="password" autoComplete="off" placeholder="dak_..." value={mediaApiKey} onChange={(e) => onMediaApiKeyChange(e.target.value)} />
            <p className="mt-1 text-xs text-muted-foreground">
              {mediaApiKeySet ? `${t("users.fields.mediaApiKey.set")} ${mediaApiKeyPrefix}… ` : null}
              {t("users.fields.mediaApiKeyHint")}
              {mediaApiKeySet && onClearMediaApiKey ? (
                <button type="button" className="ml-1 text-rose-600 underline" onClick={onClearMediaApiKey}>{t("users.fields.mediaApiKey.clear")}</button>
              ) : null}
            </p>
          </div>
```
`UserManagement.tsx`: state `createForm`/`editForm` thêm `mediaRole: ""`, `mediaApiKey: ""`, `clearMediaKey: false`; `startEdit` nạp `mediaRole: user.media_role ?? ""`; payload create thêm `media_role`, `media_api_key: createForm.mediaApiKey.trim() || ""`; payload update thêm `media_role`, và `media_api_key: editForm.clearMediaKey ? null : editForm.mediaApiKey.trim() || ""` (rỗng = giữ). Kiểu `onCreate/onUpdate` payload thêm `media_role?: string; media_api_key?: string | null;`. `EmployeeAccountCreateDialog`: `EMPTY_FORM` thêm `mediaRole: ""`, `mediaApiKey: ""`, truyền props tương ứng.

i18n `users.ts` (vi): `"users.fields.mediaRole": "Vai trò Media"`, `"users.fields.mediaRole.none": "Không có"`, `"users.fields.mediaApiKey": "API key Media riêng"`, `"users.fields.mediaApiKey.set": "Đã có key:"`, `"users.fields.mediaApiKey.clear": "Xoá key"`, `"users.fields.mediaApiKeyHint": "Có key riêng thì bỏ qua vai trò Media; ads-crm tự phân quyền. Để trống = giữ nguyên."` — en tương ứng.

- [ ] **Step 6: Chạy → pass; typecheck; chạy test users hiện có; commit**

Run: `VITEST src/features/media src/features/users` → pass. `TYPECHECK`.
```bash
git add frontend/src
git commit -m "feat(media): trang Kết nối Media; ô Vai trò Media + API key Media trong dialog Người dùng"
```

---

## Giai đoạn 2 — Ghi (Task 15–19)

### Task 15: Backend — view ghi (contents, ad-evaluations, taxonomy, proposals) + ownership

**Files:**
- Modify: `backend/apps/media/transport/api_views.py`, `backend/apps/media/urls.py`
- Create: `backend/apps/media/tests/test_write_views.py`

**Interfaces:**
- Route mới: `POST contents`, `POST contents/<id>/duplicate`, `PATCH contents/<id>/upload-info`, `PATCH contents/<id>/marketing-info`, `DELETE contents/<id>`, `POST contents/<id>/ad-evaluations`, `PATCH|DELETE ad-evaluations/<id>`, `POST <group>`, `PATCH|DELETE <group>/<id>`, `POST proposals`, `PATCH|DELETE proposals/<id>`.
- Body JSON pass-through nguyên trạng (`request.data`) — ads-crm validate; proxy chỉ đảm bảo là object.

- [ ] **Step 1: Test**

`backend/apps/media/tests/test_write_views.py`:
```python
import pytest
from django.core.cache import cache

from apps.media.models import MediaContentOwner, MediaProposalOwner

pytestmark = pytest.mark.django_db
BODY = {"uploaded_at": "2026-09-04", "title": "T", "url": "https://t.tk/v/1", "source_id": 3}


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


def test_viewer_cannot_create_content(api, shared_viewer, connection, fake_client):
    r = api(shared_viewer).post("/api/media/contents", BODY, format="json")
    assert (r.status_code, r.data["code"]) == (403, "media_forbidden")
    fake_client.json_request.assert_not_called()


def test_uploader_creates_and_owner_recorded(api, shared_uploader, connection, fake_client):
    fake_client.json_request.return_value = (201, {"id": 12, "title": "T"})
    r = api(shared_uploader).post("/api/media/contents", BODY, format="json")
    assert r.status_code == 201 and r.data["id"] == 12
    assert fake_client.json_request.call_args.args == ("POST", "/contents")
    assert fake_client.json_request.call_args.kwargs["json"] == BODY
    assert MediaContentOwner.objects.get(remote_id=12).user == shared_uploader


def test_personal_key_does_not_record_owner(api, personal_user, connection, fake_client):
    fake_client.json_request.return_value = (201, {"id": 13})
    api(personal_user).post("/api/media/contents", BODY, format="json")
    assert not MediaContentOwner.objects.filter(remote_id=13).exists()


def test_uploader_edits_only_own_content(api, shared_uploader, connection, fake_client):
    fake_client.json_request.return_value = (200, {"id": 12})
    r = api(shared_uploader).patch("/api/media/contents/12/upload-info", BODY, format="json")
    assert (r.status_code, r.data["code"]) == (403, "media_not_owner")
    MediaContentOwner.objects.create(remote_id=12, user=shared_uploader)
    r = api(shared_uploader).patch("/api/media/contents/12/upload-info", BODY, format="json")
    assert r.status_code == 200


def test_marketing_edits_tags_of_any_content_but_not_upload_info(api, shared_marketing, connection, fake_client):
    fake_client.json_request.return_value = (200, {"id": 12})
    tags = {"content_type_id": 2, "product_ids": [1], "category_ids": []}
    assert api(shared_marketing).patch("/api/media/contents/12/marketing-info", tags, format="json").status_code == 200
    assert api(shared_marketing).patch("/api/media/contents/12/upload-info", BODY, format="json").status_code == 403


def test_only_admin_deletes_content(api, shared_uploader, shared_admin, connection, fake_client):
    fake_client.json_request.return_value = (200, {"ok": True})
    assert api(shared_uploader).delete("/api/media/contents/12").status_code == 403
    assert api(shared_admin).delete("/api/media/contents/12").status_code == 200


def test_evaluations_marketing_yes_uploader_no(api, shared_marketing, shared_uploader, connection, fake_client):
    fake_client.json_request.return_value = (201, {"id": 88})
    ev = {"rating": 4.5, "note": "ok"}
    assert api(shared_marketing).post("/api/media/contents/12/ad-evaluations", ev, format="json").status_code == 201
    assert api(shared_uploader).post("/api/media/contents/12/ad-evaluations", ev, format="json").status_code == 403
    fake_client.json_request.return_value = (200, {"ok": True})
    assert api(shared_marketing).delete("/api/media/ad-evaluations/88").status_code == 200


def test_taxonomy_write_invalidates_cache(api, shared_admin, shared_uploader, connection, fake_client):
    fake_client.json_request.return_value = (200, {"items": [{"id": 1}]})
    api(shared_admin).get("/api/media/products")
    fake_client.json_request.return_value = (201, {"id": 2, "code": "SP2"})
    assert api(shared_admin).post("/api/media/products", {"code": "SP2"}, format="json").status_code == 201
    fake_client.json_request.return_value = (200, {"items": [{"id": 1}, {"id": 2}]})
    r = api(shared_admin).get("/api/media/products")
    assert len(r.data["items"]) == 2  # cache đã xoá, gọi lại upstream
    # uploader: chỉ được POST content-sources.
    fake_client.json_request.return_value = (201, {"id": 9})
    assert api(shared_uploader).post("/api/media/content-sources", {"name": "YT"}, format="json").status_code == 201
    assert api(shared_uploader).post("/api/media/products", {"code": "X"}, format="json").status_code == 403
    assert api(shared_uploader).patch("/api/media/content-sources/9", {"name": "Y"}, format="json").status_code == 403


def test_proposals_owner_scoped_edit_and_admin_delete(api, shared_viewer, shared_admin, connection, fake_client):
    fake_client.json_request.return_value = (201, {"id": 4})
    body = {"main_key": "k", "product_id": 5}
    assert api(shared_viewer).post("/api/media/proposals", body, format="json").status_code == 201
    assert MediaProposalOwner.objects.get(remote_id=4).user == shared_viewer
    fake_client.json_request.return_value = (200, {"id": 4})
    assert api(shared_viewer).patch("/api/media/proposals/4", body, format="json").status_code == 200
    assert api(shared_viewer).patch("/api/media/proposals/5", body, format="json").status_code == 403
    assert api(shared_viewer).delete("/api/media/proposals/4").status_code == 403
    fake_client.json_request.return_value = (200, {"ok": True})
    assert api(shared_admin).delete("/api/media/proposals/4").status_code == 200


def test_non_object_body_rejected_400(api, shared_admin, connection, fake_client):
    r = api(shared_admin).post("/api/media/contents", [1, 2], format="json")
    assert r.status_code == 400
    fake_client.json_request.assert_not_called()
```

- [ ] **Step 2: Chạy → fail** (405/404).

- [ ] **Step 3: Implement view ghi**

Thêm vào `api_views.py`:
```python
from rest_framework import status as http

from apps.media.exceptions import MediaError


def _body(request) -> dict:
    if not isinstance(request.data, dict):
        raise MediaError("Body phải là JSON object.", status=http.HTTP_400_BAD_REQUEST, code="invalid_json")
    return request.data


def _remote_id(data) -> int | None:
    rid = data.get("id") if isinstance(data, dict) else None
    return int(rid) if isinstance(rid, int) else None
```
Đổi `contents_collection` thành `@_auth(["GET", "POST"])`:
```python
    if request.method == "POST":
        code, data = proxy.forward("POST", "/contents", action=Action.CONTENT_CREATE, json=_body(request))
        if (rid := _remote_id(data)) is not None:
            proxy.record_content_owner(rid)
        return _passthrough(code, data)
```
Đổi `content_detail` thành `@_auth(["GET", "DELETE"])`, nhánh DELETE: `proxy.forward("DELETE", f"/contents/{content_id}", action=Action.CONTENT_DELETE)`.

View mới:
```python
@_auth(["POST"])
def content_duplicate(request, content_id: int):
    proxy = MediaProxy(request.user)
    code, data = proxy.forward("POST", f"/contents/{content_id}/duplicate", action=Action.CONTENT_CREATE)
    if (rid := _remote_id(data)) is not None:
        proxy.record_content_owner(rid)
    return _passthrough(code, data)


@_auth(["PATCH"])
def content_upload_info(request, content_id: int):
    proxy = MediaProxy(request.user)
    return _passthrough(*proxy.forward("PATCH", f"/contents/{content_id}/upload-info", action=Action.CONTENT_EDIT_UPLOAD,
                                       json=_body(request), owner_check=lambda: proxy.owns_content(content_id)))


@_auth(["PATCH"])
def content_marketing_info(request, content_id: int):
    proxy = MediaProxy(request.user)
    return _passthrough(*proxy.forward("PATCH", f"/contents/{content_id}/marketing-info", action=Action.CONTENT_EDIT_MARKETING,
                                       json=_body(request), owner_check=lambda: proxy.owns_content(content_id)))
```
`content_evaluations` → `@_auth(["GET", "POST"])`, POST: `forward("POST", f"/contents/{content_id}/ad-evaluations", action=Action.EVALUATION_WRITE, json=_body(request))`.
```python
@_auth(["PATCH", "DELETE"])
def evaluation_detail(request, evaluation_id: int):
    proxy = MediaProxy(request.user)
    if request.method == "DELETE":
        return _passthrough(*proxy.forward("DELETE", f"/ad-evaluations/{evaluation_id}", action=Action.EVALUATION_WRITE))
    return _passthrough(*proxy.forward("PATCH", f"/ad-evaluations/{evaluation_id}", action=Action.EVALUATION_WRITE, json=_body(request)))
```
`taxonomy_collection` → `@_auth(["GET", "POST"])`, POST:
```python
        action = Action.TAXONOMY_WRITE_SOURCES if group == "content-sources" else Action.TAXONOMY_WRITE
        code, data = proxy.forward("POST", f"/{group}", action=action, json=_body(request))
        MediaProxy.invalidate_taxonomy(group)
        return _passthrough(code, data)
```
```python
@_auth(["PATCH", "DELETE"])
def taxonomy_detail(request, group: str, item_id: int):
    if group not in P.TAXONOMY_GROUPS:
        raise Http404
    proxy = MediaProxy(request.user)
    if request.method == "DELETE":
        code, data = proxy.forward("DELETE", f"/{group}/{item_id}", action=Action.TAXONOMY_WRITE)
    else:
        code, data = proxy.forward("PATCH", f"/{group}/{item_id}", action=Action.TAXONOMY_WRITE, json=_body(request))
    MediaProxy.invalidate_taxonomy(group)
    return _passthrough(code, data)
```
`proposals_collection` → `["GET", "POST"]`, POST: `forward("POST", "/proposals", action=Action.PROPOSAL_CREATE, json=_body(request))` rồi `record_proposal_owner`. `proposal_detail` → `["GET", "PATCH", "DELETE"]`: PATCH `action=Action.PROPOSAL_EDIT, owner_check=lambda: proxy.owns_proposal(proposal_id)`; DELETE `action=Action.PROPOSAL_DELETE`.

`urls.py` thêm:
```python
    path("contents/<int:content_id>/duplicate", v.content_duplicate),
    path("contents/<int:content_id>/upload-info", v.content_upload_info),
    path("contents/<int:content_id>/marketing-info", v.content_marketing_info),
    path("ad-evaluations/<int:evaluation_id>", v.evaluation_detail),
    re_path(rf"^(?P<group>{TAX})/(?P<item_id>\d+)$", v.taxonomy_detail),
```
(`item_id` từ regex là str → ép `int(item_id)` trong view, hoặc dùng `path(f"<group>/<int:item_id>")` với kiểm tra group trong view — chọn `path("<str:group>/<int:item_id>", ...)` đặt SAU các path cụ thể; view raise 404 khi group lạ.)

Nếu `api_views.py` vượt 500 dòng, tách view ghi sang `transport/write_views.py` và import trong `urls.py`.

- [ ] **Step 4: Chạy → pass; commit**

Run: `PYTEST apps/media` → all passed.
```bash
git add backend/apps/media
git commit -m "feat(media): proxy ghi (contents, đánh giá ads, taxonomy, đề xuất) + ownership cho key chung"
```

---

### Task 16: Frontend — tạo/sửa/nhân bản/xoá nội dung, sửa tag

**Files:**
- Create: `features/media/components/ContentFormDialog.tsx`, `ContentTagsDialog.tsx`, `ContentRowActions.tsx`, `features/media/__tests__/ContentFormDialog.test.tsx`
- Modify: `features/media/queries.ts` (mutations), `features/media/pages/MediaContentsPage.tsx`, `i18n/messages/media.ts`

**Interfaces:**
- Mutations: `useCreateContent()`, `useUpdateUploadInfo()`, `useUpdateMarketingInfo()`, `useDuplicateContent()`, `useDeleteContent()` — mỗi cái `onSuccess` invalidate `["media","contents"]` và `mediaKeys.content(id)`; lỗi → `toast.error(err.message)`.
- `<ContentFormDialog open onOpenChange initial?: MediaContent taxonomy onSubmit(payload: ContentUploadPayload) />`; `<ContentTagsDialog open onOpenChange content taxonomy onSubmit(payload: ContentTagsPayload) />`.

- [ ] **Step 1: Test dialog**

```tsx
it("bắt buộc tiêu đề, URL tuyệt đối, nguồn; submit payload đúng shape", async () => {
  const onSubmit = vi.fn();
  renderWithI18n(<ContentFormDialog open onOpenChange={() => {}} taxonomy={TAX} onSubmit={onSubmit} />);
  fireEvent.click(screen.getByRole("button", { name: /^Lưu$/ }));
  expect(onSubmit).not.toHaveBeenCalled();
  expect(screen.getByText(/Tiêu đề bắt buộc/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(/Tiêu đề/), { target: { value: "V1" } });
  fireEvent.change(screen.getByLabelText(/^URL/), { target: { value: "tiktok.com/x" } });
  fireEvent.click(screen.getByRole("button", { name: /^Lưu$/ }));
  expect(screen.getByText(/URL phải có http/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(/^URL/), { target: { value: "https://tiktok.com/x" } });
  fireEvent.change(screen.getByLabelText(/Nguồn/), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: /^Lưu$/ }));
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ title: "V1", url: "https://tiktok.com/x", source_id: 3, uploaded_at: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/) }));
});
```
`TAX` = record 5 group với `"content-sources": [{id:3,name:"TikTok",is_active:true}]`, `products: [{id:1,code:"SP1",name:"Serum",is_active:true}]`, còn lại `[]`.

- [ ] **Step 2: Chạy → fail.**

- [ ] **Step 3: Mutations trong `queries.ts`**

```ts
function useInvalidateContents() {
  const qc = useQueryClient();
  return (id?: number) => {
    void qc.invalidateQueries({ queryKey: ["media", "contents"] });
    if (id) void qc.invalidateQueries({ queryKey: mediaKeys.content(id) });
  };
}
export function useCreateContent() {
  const inv = useInvalidateContents();
  return useMutation({ mutationFn: createContent, onSuccess: () => inv() });
}
export function useUpdateUploadInfo() {
  const inv = useInvalidateContents();
  return useMutation({ mutationFn: ({ id, payload }: { id: number; payload: ContentUploadPayload }) => updateUploadInfo(id, payload), onSuccess: (_, v) => inv(v.id) });
}
export function useUpdateMarketingInfo() {
  const inv = useInvalidateContents();
  return useMutation({ mutationFn: ({ id, payload }: { id: number; payload: ContentTagsPayload }) => updateMarketingInfo(id, payload), onSuccess: (_, v) => inv(v.id) });
}
export function useDuplicateContent() {
  const inv = useInvalidateContents();
  return useMutation({ mutationFn: duplicateContent, onSuccess: () => inv() });
}
export function useDeleteContent() {
  const inv = useInvalidateContents();
  return useMutation({ mutationFn: deleteContent, onSuccess: (_, id) => inv(id) });
}
```

- [ ] **Step 4: `ContentFormDialog.tsx`**

`Dialog` + `FormDialogShell` (title `media.contents.form.create|edit`, `onSubmit` preventDefault → validate → `onSubmit(payload)`), `FormGrid`: `Input type="date"` uploaded_at (mặc định hôm nay, `<label htmlFor>` "Ngày upload"), `Input` title (label "Tiêu đề"), `Input` url (label "URL"), `<select>` nguồn (label "Nguồn", options từ `taxonomy["content-sources"]`), và **chỉ khi tạo mới** (`!initial`): `<select>` loại nội dung, `MultiSelect` sản phẩm, `MultiSelect` danh mục (upload-info bỏ qua chúng — tài liệu §3.4). Validate: title trống → `media.contents.form.err.title` ("Tiêu đề bắt buộc"), URL không khớp `/^https?:\/\/[^/\s]+/` → `media.contents.form.err.url` ("URL phải có http(s):// và host"), source rỗng → `err.source`. Payload: `{ uploaded_at, title, url, source_id: Number(source), content_type_id: type ? Number(type) : null, product_ids: productIds.map(Number), category_ids: categoryIds.map(Number) }`. Khi `initial` có, nạp giá trị từ `initial` (uploaded_at `slice(0,10)`).

- [ ] **Step 5: `ContentTagsDialog.tsx`**

Dialog nhỏ: `<select>` loại nội dung (cho phép "— Không —"), `MultiSelect` sản phẩm, `MultiSelect` danh mục; nạp từ `content`; submit `{ content_type_id, product_ids, category_ids }`. Ghi chú dưới form: `media.contents.tags.replaceNote` ("Lưu sẽ thay toàn bộ tag hiện có").

- [ ] **Step 6: `ContentRowActions.tsx` + cắm vào trang**

`ContentRowActions({ content, role, onDuplicate, onDelete })`: `DropdownMenu` (`components/ui/dropdown-menu`) với item "Nhân bản" khi `mediaCan(role,"content.create")`, "Xoá" (đỏ) khi `mediaCan(role,"content.delete")` mở `Dialog` xác nhận. Không có quyền nào → render null.

`MediaContentsPage`: state `createOpen`, `editTarget: MediaContent | null`, `tagsTarget`; header `actions={mediaCan(me.role,"content.create") ? <Button onClick={() => setCreateOpen(true)}>{t("media.contents.add")}</Button> : null}`; `ContentTable rowActions={(c) => <ContentRowActions … />}`; `ContentDetailSheet actions={(c) => (<>{mediaCan(role,"content.edit_upload") && <Button variant="outline" size="sm" onClick={() => setEditTarget(c)}>Sửa thông tin</Button>}{mediaCan(role,"content.edit_marketing") && <Button variant="outline" size="sm" onClick={() => setTagsTarget(c)}>Sửa tag</Button>}</>)}`. Mutation `mutateAsync` + `toast.success/error`.

i18n vi: `"media.contents.form.create": "Thêm nội dung"`, `"media.contents.form.edit": "Sửa thông tin upload"`, `"media.contents.form.uploadedAt": "Ngày upload"`, `"media.contents.form.title": "Tiêu đề"`, `"media.contents.form.url": "URL"`, `"media.contents.form.source": "Nguồn"`, `"media.contents.form.type": "Loại nội dung"`, `err.title/url/source` như trên, `"media.contents.tags.title": "Sửa tag"`, `"media.contents.tags.replaceNote": "Lưu sẽ thay toàn bộ tag hiện có."`, `"media.contents.action.editInfo": "Sửa thông tin"`, `"media.contents.action.editTags": "Sửa tag"`, `"media.contents.action.duplicate": "Nhân bản"`, `"media.contents.action.delete": "Xoá"`, `"media.contents.action.deleteConfirm": "Xoá nội dung này? Bản ghi bị ẩn khỏi mọi danh sách."`, `"media.toast.saved": "Đã lưu."`, `"media.toast.deleted": "Đã xoá."`, `"media.toast.duplicated": "Đã nhân bản."`. en tương ứng.

- [ ] **Step 7: Chạy → pass; typecheck; commit** — `feat(media): tạo/sửa/nhân bản/xoá nội dung, sửa tag`.

---

### Task 17: Frontend — đánh giá ads (thêm/sửa/xoá) trong drawer

**Files:**
- Create: `features/media/components/AdEvaluationList.tsx`, `features/media/__tests__/AdEvaluationList.test.tsx`
- Modify: `features/media/queries.ts`, `features/media/pages/MediaContentsPage.tsx`, `i18n/messages/media.ts`

- [ ] **Step 1: Test**

```tsx
it("marketing thấy form; submit gửi rating step 0.5 + note; rating 0 và note rỗng bị chặn", async () => {
  fetchJson.mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === "POST") return Promise.resolve({ id: 99, rating: 4.5, note: "ok" });
    return Promise.resolve({ items: [{ id: 1, content_id: 12, rating: 3, note: "cũ", evaluator_name: "A", evaluated_at: "2026-09-01T00:00:00Z", channel: null, channel_id: null }] });
  });
  renderWithClient(<I18nProvider><AdEvaluationList contentId={12} role="marketing" /></I18nProvider>);
  expect(await screen.findByText("cũ")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Thêm đánh giá/ }));
  expect(screen.getByText(/Cần điểm hoặc ghi chú/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(/Điểm/), { target: { value: "4.5" } });
  fireEvent.change(screen.getByLabelText(/Ghi chú/), { target: { value: "ok" } });
  fireEvent.click(screen.getByRole("button", { name: /Thêm đánh giá/ }));
  await waitFor(() => expect(fetchJson.mock.calls.some((c) => (c[1] as RequestInit)?.method === "POST" && String((c[1] as RequestInit).body) === JSON.stringify({ rating: 4.5, note: "ok" }))).toBe(true));
});
it("viewer không thấy form và nút xoá", async () => { /* role="viewer" → queryByRole button 'Thêm đánh giá' null */ });
```

- [ ] **Step 2: Chạy → fail.**

- [ ] **Step 3: Mutations**

```ts
export function useCreateEvaluation(contentId: number) {
  const qc = useQueryClient();
  const inv = () => { void qc.invalidateQueries({ queryKey: mediaKeys.evaluations(contentId) }); void qc.invalidateQueries({ queryKey: mediaKeys.content(contentId) }); void qc.invalidateQueries({ queryKey: ["media", "contents"] }); };
  return useMutation({ mutationFn: (p: EvaluationPayload) => createEvaluation(contentId, p), onSuccess: inv });
}
export function useUpdateEvaluation(contentId: number) { /* mutationFn: ({id,payload}) => updateEvaluation(id,payload); cùng inv */ }
export function useDeleteEvaluation(contentId: number) { /* mutationFn: deleteEvaluation; cùng inv */ }
```
(Invalidate cả danh sách contents vì `overall_rating` được ads-crm tính lại.)

- [ ] **Step 4: `AdEvaluationList.tsx`**

Props `{ contentId: number; role: MediaRole | null }`. `useEvaluations(contentId)`. Danh sách: mỗi dòng rating (`toFixed(1)`), evaluator_name, ngày, note; khi `mediaCan(role,"evaluation.write")` có nút "Sửa" (chuyển dòng thành form inline) và "Xoá" (confirm `window.confirm` → `Dialog` nếu muốn đồng nhất; dùng `Dialog` nhỏ). Form thêm (chỉ khi write): `<select id="ev-rating">` 0..5 bước 0.5 (label "Điểm"), `Textarea id="ev-note"` (label "Ghi chú"), nút "Thêm đánh giá". Validate: `rating === 0 && !note.trim()` → hiện `media.eval.err.empty` ("Cần điểm hoặc ghi chú"). Submit `mutateAsync` → toast.

i18n vi: `"media.eval.add": "Thêm đánh giá"`, `"media.eval.rating": "Điểm"`, `"media.eval.note": "Ghi chú"`, `"media.eval.err.empty": "Cần điểm hoặc ghi chú."`, `"media.eval.deleteConfirm": "Xoá đánh giá này?"`, `"media.eval.byline": "{name} · {date}"` (dùng `t(...).replace`). en tương ứng.

- [ ] **Step 5: Cắm vào trang**: `ContentDetailSheet evaluationsSlot={(c) => <AdEvaluationList contentId={c.id} role={me.role} />}`.

- [ ] **Step 6: Chạy → pass; typecheck; commit** — `feat(media): đánh giá ads trong drawer chi tiết`.

---

### Task 18: Frontend — CRUD đề xuất

**Files:**
- Create: `features/media/components/ProposalFormDialog.tsx`, `features/media/__tests__/ProposalFormDialog.test.tsx`
- Modify: `features/media/queries.ts`, `features/media/pages/MediaProposalsPage.tsx`, `i18n/messages/media.ts`

- [ ] **Step 1: Test**: `main_key` trống → lỗi "Từ khoá chính bắt buộc"; submit gửi đủ field (`{ main_key, idea, due_date: "2026-10-01" | null, status, product_id: 5, content_id: null }`); khi `initial` có status hiện select trạng thái, khi tạo mới ẩn (mặc định pending).

- [ ] **Step 2: Chạy → fail.**

- [ ] **Step 3: Mutations** `useCreateProposal`, `useUpdateProposal` (`{id,payload}`), `useDeleteProposal` — invalidate `["media","proposals"]`.

- [ ] **Step 4: `ProposalFormDialog.tsx`**

`FormDialogShell`: `Input` main_key (label "Từ khoá chính"), `Textarea` idea, `Input type="date"` due_date, `<select>` product_id (từ `taxonomy.products`, chỉ `is_active`), `Input type="number"` content_id (tuỳ chọn, label "ID nội dung liên kết", hint "Để trống nếu chưa có"), `<select>` status chỉ khi sửa. PATCH ghi đè toàn bộ (tài liệu §2.3) → luôn gửi đủ field từ form.

- [ ] **Step 5: Trang**: header `actions` = nút "Thêm đề xuất" khi `mediaCan(role,"proposal.create")`; `rowActions`: "Sửa" khi `mediaCan(role,"proposal.edit")` (owner do backend quyết), "Xoá" khi `mediaCan(role,"proposal.delete")` với confirm. toast.

i18n vi: `"media.proposals.form.create": "Thêm đề xuất"`, `"media.proposals.form.edit": "Sửa đề xuất"`, `"media.proposals.form.mainKey": "Từ khoá chính"`, `idea: "Ý tưởng"`, `due: "Hạn hoàn thành"`, `product: "Sản phẩm"`, `contentId: "ID nội dung liên kết"`, `contentIdHint: "Để trống nếu chưa có."`, `status: "Trạng thái"`, `"media.proposals.form.err.mainKey": "Từ khoá chính bắt buộc."`, `"media.proposals.deleteConfirm": "Xoá đề xuất này?"`. en tương ứng.

- [ ] **Step 6: Chạy → pass; typecheck; commit** — `feat(media): CRUD đề xuất nội dung`.

---

### Task 19: Frontend — CRUD danh mục

**Files:**
- Create: `features/media/components/TaxonomyFormDialog.tsx`, `features/media/__tests__/TaxonomyFormDialog.test.tsx`
- Modify: `features/media/queries.ts`, `features/media/components/TaxonomyTabs.tsx`, `features/media/pages/MediaTaxonomyPage.tsx`, `i18n/messages/media.ts`

- [ ] **Step 1: Test**: group `products` → ô Mã bắt buộc (lỗi "Mã bắt buộc"); `content-types` → select Nhóm video/image bắt buộc; group khác → chỉ Tên bắt buộc; khi sửa hiện `Switch` is_active và payload có `is_active`; khi tạo không gửi `is_active`.

- [ ] **Step 2: Chạy → fail.**

- [ ] **Step 3: Mutations** `useCreateTaxonomy(group)`, `useUpdateTaxonomy(group)`, `useDeleteTaxonomy(group)` — invalidate `["media","taxonomy",group]`.

- [ ] **Step 4: `TaxonomyFormDialog.tsx`** props `{ open, onOpenChange, group, initial?: MediaTaxonomyItem, onSubmit(payload: TaxonomyPayload) }`. Field theo bảng §3.6 tài liệu: `content-types`: name + group; `products`: code (+ name tuỳ chọn); còn lại: name. Khi sửa thêm `Switch` is_active.

- [ ] **Step 5: Cắm vào `TaxonomyTabs`/trang**: `headerActions` = nút "Thêm" khi `mediaCan(role, group === "content-sources" ? "taxonomy.write_sources" : "taxonomy.write")`; `rowActions`: "Sửa"/"Xoá" khi `mediaCan(role,"taxonomy.write")`; confirm xoá ghi rõ `media.taxonomy.deleteConfirm` ("Xoá (ẩn) bản ghi này? Nội dung đang gắn không đổi."). toast.

i18n vi: `"media.taxonomy.form.create": "Thêm bản ghi"`, `edit: "Sửa bản ghi"`, `name: "Tên"`, `code: "Mã"`, `group: "Nhóm"`, `active: "Đang dùng"`, `err.name: "Tên bắt buộc."`, `err.code: "Mã bắt buộc."`, `err.group: "Chọn nhóm."`, `deleteConfirm` như trên. en tương ứng.

- [ ] **Step 6: Chạy → pass; typecheck; commit** — `feat(media): CRUD danh mục Media`.

---

## Giai đoạn 3 — Tệp (Task 20–21)

### Task 20: Backend — tải video (stream) + cache video

**Files:**
- Create: `backend/apps/media/transport/file_views.py`, `backend/apps/media/tests/test_file_views.py`
- Modify: `backend/apps/media/urls.py`

**Interfaces:**
- `GET contents/<id>/download` → `StreamingHttpResponse` forward `Content-Type`, `Content-Disposition`, `Content-Length`; `POST contents/<id>/video/cache[?force=1]` → pass-through (202/200), `force` chỉ khi `Action.VIDEO_CACHE_FORCE`.
- `AdsCrmClient.stream_get` đã có (Task 3).

- [ ] **Step 1: Test**

```python
import pytest
from unittest.mock import MagicMock

pytestmark = pytest.mark.django_db


def _stream_resp(chunks, headers):
    r = MagicMock()
    r.status_code = 200
    r.headers = headers
    r.iter_content.return_value = iter(chunks)
    return r


def test_download_streams_and_forwards_headers(api, shared_viewer, connection, fake_client, read_streaming_response):
    fake_client.stream_get.return_value = _stream_resp(
        [b"abc", b"def"], {"Content-Type": "video/mp4", "Content-Disposition": 'attachment; filename="v.mp4"', "Content-Length": "6"})
    r = api(shared_viewer).get("/api/media/contents/12/download")
    assert r.status_code == 200
    assert r["Content-Type"] == "video/mp4" and r["Content-Disposition"] == 'attachment; filename="v.mp4"' and r["Content-Length"] == "6"
    assert read_streaming_response(r) == b"abcdef"
    assert fake_client.stream_get.call_args.args == ("/contents/12/download",)
    assert fake_client.stream_get.call_args.kwargs["timeout"] == 120.0


def test_download_upstream_errors_mapped(api, shared_viewer, connection, fake_client, upstream_error):
    fake_client.stream_get.side_effect = upstream_error("photo_post", 422)
    r = api(shared_viewer).get("/api/media/contents/12/download")
    assert (r.status_code, r.data["code"]) == (422, "photo_post")


def test_video_cache_force_only_admin(api, shared_viewer, shared_admin, connection, fake_client):
    fake_client.json_request.return_value = (202, {"video_status": "pending"})
    assert api(shared_viewer).post("/api/media/contents/12/video/cache").status_code == 202
    assert fake_client.json_request.call_args.kwargs["params"] is None
    assert api(shared_viewer).post("/api/media/contents/12/video/cache?force=1").status_code == 403
    assert api(shared_admin).post("/api/media/contents/12/video/cache?force=1").status_code == 202
    assert fake_client.json_request.call_args.kwargs["params"] == [("force", "1")]
```
(`read_streaming_response` là fixture sẵn trong `backend/conftest.py`; nếu fixture trả coroutine/async, dùng đúng cách nó mô tả.)

- [ ] **Step 2: Chạy → fail.**

- [ ] **Step 3: `transport/file_views.py`**

```python
"""Tải video qua proxy: stream từ ads-crm về trình duyệt, không đệm vào RAM (spec D9)."""

from __future__ import annotations

from django.conf import settings
from django.http import StreamingHttpResponse

from apps.media.application.services.access_policy import authorize
from apps.media.application.services.proxy_service import MediaProxy
from apps.media.domain.roles import Action
from apps.media.transport.api_views import _auth, _passthrough

_FORWARD_HEADERS = ("Content-Type", "Content-Disposition", "Content-Length")
_CHUNK = 64 * 1024


@_auth(["GET"])
def content_download(request, content_id: int):
    proxy = MediaProxy(request.user)
    authorize(proxy.cred, Action.READ)
    upstream = proxy.client.stream_get(
        f"/contents/{content_id}/download", timeout=float(getattr(settings, "MEDIA_DOWNLOAD_TIMEOUT", 120.0))
    )
    response = StreamingHttpResponse(
        upstream.iter_content(chunk_size=_CHUNK),
        content_type=upstream.headers.get("Content-Type", "application/octet-stream"),
    )
    for name in _FORWARD_HEADERS[1:]:
        if value := upstream.headers.get(name):
            response[name] = value
    return response


@_auth(["POST"])
def content_video_cache(request, content_id: int):
    proxy = MediaProxy(request.user)
    force = request.query_params.get("force") in ("1", "true")
    params = None
    if force:
        authorize(proxy.cred, Action.VIDEO_CACHE_FORCE)
        params = [("force", "1")]
    return _passthrough(*proxy.forward("POST", f"/contents/{content_id}/video/cache", action=Action.READ, params=params))
```
> Prod chạy ASGI (xem `apps/common/http_streaming.py`): iterator đồng bộ bị Django gom hết vào RAM trước khi gửi. Nếu test `response.is_async` cần đúng, bọc `upstream.iter_content` bằng async generator qua `sync_to_async(next, thread_sensitive=True)` theo mẫu `aiter_filelike` — làm ngay trong task này nếu file video thực tế > vài chục MB (tài liệu nói "vài chục giây" → có thể lớn): dùng `aiter_filelike(upstream.raw)` với `upstream.raw.decode_content = True`.

`urls.py`: `path("contents/<int:content_id>/download", f.content_download)`, `path("contents/<int:content_id>/video/cache", f.content_video_cache)` với `from apps.media.transport import file_views as f`.

- [ ] **Step 4: Chạy → pass; commit** — `feat(media): tải video stream qua proxy + cache video (force chỉ admin)`.

---

### Task 21: Frontend — nút Tải video / Cache video

**Files:**
- Create: `features/media/components/VideoActions.tsx`, `features/media/__tests__/VideoActions.test.tsx`
- Modify: `features/media/queries.ts`, `features/media/pages/MediaContentsPage.tsx`, `i18n/messages/media.ts`

- [ ] **Step 1: Test**: mock `fetch` toàn cục trả `Response(new Blob(["x"]), { headers: { "Content-Disposition": 'attachment; filename="v.mp4"' } })`; bấm "Tải video" → gọi `/api/media/contents/12/download` và tạo `<a download="v.mp4">` (spy `URL.createObjectURL`); lỗi 422 `{detail:"Bài dạng ảnh..."}` → `toast.error` với detail. Nút "Cache video" chỉ hiện khi URL chứa `tiktok.com` và `video_status !== "success"`; ô "force" chỉ admin.

- [ ] **Step 2: Chạy → fail.**

- [ ] **Step 3: Implement**

`queries.ts`: `useRequestVideoCache(contentId)` → `requestVideoCache(contentId, force)`, onSuccess invalidate `mediaKeys.content(contentId)`.
`VideoActions({ content, role })`: nút "Tải video" (`downloadContentVideo` → `URL.createObjectURL(blob)` → `<a download={filename}>` click → `revokeObjectURL`; disable + spinner khi đang tải, toast lỗi); nút "Cache video" khi `/tiktok\.com/i.test(content.url) && content.video_status !== "success"`; khi `mediaCan(role,"video.cache_force")` và `video_status === "success"` hiện nút "Cache lại" (force). Toast theo `video_status` trả về.
Cắm vào `ContentDetailSheet actions` (cạnh nút sửa).

i18n vi: `"media.video.download": "Tải video"`, `"media.video.downloading": "Đang tải…"`, `"media.video.cache": "Cache video"`, `"media.video.recache": "Cache lại"`, `"media.video.queued": "Đã xếp hàng cache video."`, `"media.video.already": "Video đã có sẵn trên cache."`. en tương ứng.

- [ ] **Step 4: Chạy → pass; typecheck; commit** — `feat(media): tải video và cache video từ drawer`.

---

## Hoàn tất

### Task 22: Regen schema, toàn bộ test, lint, docs

**Files:**
- Modify: `backend/schema.yaml`, `frontend/src/api/schema.d.ts`, `docs/superpowers/specs/2026-09-04-media-module-design.md` (mục "Trạng thái" nếu có lệch), `README.md` (một dòng về module Media + env `MEDIA_*`)

- [ ] **Step 1: Regen OpenAPI** (serializer `users` đổi):
```bash
cd backend && <env block> /home/desi/desi-order/.venv/bin/python manage.py spectacular --file schema.yaml
cd ../frontend && docker run --rm -v /home/desi:/home/desi -w $PWD node:22-alpine npm run gen:api
```
- [ ] **Step 2: Backend toàn bộ**: `PYTEST apps/media apps/users` → pass (bỏ qua 2 lỗi đã biết trên master: `test_system_role_short_circuits_to_full`, `test_cannot_edit_system_role`). Chạy `.venv/bin/lint-imports` từ `backend/` → contract pass (nếu `users.serializers -> apps.media` bị bắt, chuyển `is_provisioned` thành hàm nội bộ trong `serializers.py` như ghi chú Task 5).
- [ ] **Step 3: Frontend toàn bộ**: `VITEST` (không tham số) → pass; `TYPECHECK`; `docker run … npm run lint`.
- [ ] **Step 4: Kiểm tra thủ công (staging/local)**: tạo `MediaConnection` qua trang Cài đặt → Kiểm tra kết nối; gán vai trò Media cho một user → menu Media xuất hiện sau reload; mở Thư viện, lọc, mở chi tiết; user không key/role không thấy menu.
- [ ] **Step 5: Docs + commit**: README thêm mục "Module Media" trỏ tới spec; commit `docs(media): schema regen, README`.
- [ ] **Step 6: Kết thúc branch** theo skill `superpowers:finishing-a-development-branch` (merge/PR vào `master`).

---

## Phụ lục A — Mã tham chiếu cho các component mô tả ngắn ở Task 11–13

### A.1 `ProposalTable.tsx` (Task 11)
```tsx
import type { ReactNode } from "react";
import { Lightbulb } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import type { MediaProposal } from "@/lib/api/media";

type Props = { items: MediaProposal[]; onOpenContent: (id: number) => void; rowActions?: (p: MediaProposal) => ReactNode };

export function ProposalTable({ items, onOpenContent, rowActions }: Props) {
  const { t } = useI18n();
  if (items.length === 0) return <EmptyState icon={Lightbulb} title={t("media.common.empty")} />;
  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            {(["mainKey", "idea", "product", "due", "status", "creator", "content"] as const).map((k) => (
              <TableHead key={k}>{t(`media.proposals.col.${k}`)}</TableHead>
            ))}
            {rowActions ? <TableHead className="w-10" /> : null}
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((p) => (
            <TableRow key={p.id}>
              <TableCell className="font-medium">{p.main_key}</TableCell>
              <TableCell className="max-w-[20rem] truncate text-muted-foreground">{p.idea || "—"}</TableCell>
              <TableCell>{p.product ? <Badge variant="secondary">{p.product.code || p.product.name}</Badge> : "—"}</TableCell>
              <TableCell className="whitespace-nowrap text-xs">{p.due_date ? p.due_date.slice(0, 10) : "—"}</TableCell>
              <TableCell><Badge variant="outline">{t(`media.proposals.status.${p.status}`)}</Badge></TableCell>
              <TableCell>{p.creator?.name ?? "—"}</TableCell>
              <TableCell>
                {p.content ? (
                  <button type="button" className="font-mono text-xs text-primary underline" onClick={() => onOpenContent(p.content!.id)}>
                    {p.content.content_code || `#${p.content.id}`}
                  </button>
                ) : "—"}
              </TableCell>
              {rowActions ? <TableCell>{rowActions(p)}</TableCell> : null}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
```

### A.2 `MediaProposalsPage.tsx` (Task 11, phần đọc; Task 18 thêm `headerActions`/`rowActions`)
```tsx
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { LogoSpinner } from "@/components/ui/logo-spinner";
import { MultiSelect } from "@/components/ui/multi-select";
import { PageHeader } from "@/components/ui/page-header";
import { TablePagination } from "@/components/ui/table-pagination";
import { useDebounce } from "@/hooks/useDebounce";
import { useI18n } from "@/i18n";

import { MediaAccessGate } from "../components/MediaAccessGate";
import { ProposalTable } from "../components/ProposalTable";
import { PAGE_SIZE_OPTIONS } from "../filters";
import { proposalFiltersFromParams, proposalFiltersToParams, type ProposalFilters } from "../proposal-filters";
import { useAllTaxonomy, useProposals } from "../queries";

const STATUSES = ["pending", "in_progress", "done", "cancelled"] as const;

export default function MediaProposalsPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [sp, setSp] = useSearchParams();
  const filters = useMemo(() => proposalFiltersFromParams(sp), [sp]);
  const setFilters = useCallback((next: ProposalFilters) => setSp(proposalFiltersToParams(next), { replace: true }), [setSp]);
  const [q, setQ] = useState(filters.q);
  const debouncedQ = useDebounce(q, 300);
  useEffect(() => { if (debouncedQ !== filters.q) setFilters({ ...filters, q: debouncedQ, page: 1 }); }, [debouncedQ]); // eslint-disable-line react-hooks/exhaustive-deps
  const params = useMemo(() => proposalFiltersToParams(filters), [filters]);
  const { data, isLoading } = useProposals(params);
  const taxonomy = useAllTaxonomy();

  return (
    <div className="space-y-4">
      <MediaAccessGate>
        {() => (
          <>
            <PageHeader title={t("topnav.tabs.mediaProposals")} />
            <Card>
              <CardContent className="flex flex-wrap items-end gap-3 pt-6">
                <Input placeholder={t("media.proposals.search")} value={q} onChange={(e) => setQ(e.target.value)} className="h-9 w-64" />
                <MultiSelect options={STATUSES.map((s) => ({ value: s, label: t(`media.proposals.status.${s}`) }))} selected={filters.statuses}
                  onChange={(v) => setFilters({ ...filters, statuses: v, page: 1 })} placeholder={t("media.proposals.filter.status")} menuPortal />
                <MultiSelect options={taxonomy.products.map((p) => ({ value: String(p.id), label: p.code ? `${p.code} – ${p.name}` : p.name }))}
                  selected={filters.productIds} onChange={(v) => setFilters({ ...filters, productIds: v, page: 1 })} placeholder={t("media.proposals.filter.product")} menuPortal />
              </CardContent>
            </Card>
            {isLoading || !data ? <LogoSpinner /> : (
              <>
                <ProposalTable items={data.items} onOpenContent={(id) => navigate(`/media/contents/${id}`)} />
                <TablePagination total={data.total} page={filters.page} pageSize={filters.pageSize}
                  onPageChange={(page) => setFilters({ ...filters, page })}
                  pageSizeOptions={PAGE_SIZE_OPTIONS} onPageSizeChange={(pageSize) => setFilters({ ...filters, pageSize, page: 1 })} />
              </>
            )}
          </>
        )}
      </MediaAccessGate>
    </div>
  );
}
```

### A.3 `TaxonomyTabs.tsx` (Task 13; Task 19 dùng `headerActions`/`rowActions`)
```tsx
import type { ReactNode } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import { LogoSpinner } from "@/components/ui/logo-spinner";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs } from "@/components/ui/tabs";
import { useI18n } from "@/i18n";
import { TAXONOMY_GROUPS, type MediaTaxonomyItem, type TaxonomyGroup } from "@/lib/api/media";

import { mediaCan, type MediaRole } from "../access";
import { useTaxonomy } from "../queries";

type Props = {
  group: TaxonomyGroup; onGroupChange: (g: TaxonomyGroup) => void;
  includeInactive: boolean; onIncludeInactiveChange: (v: boolean) => void;
  role: MediaRole | null;
  headerActions?: ReactNode;
  rowActions?: (item: MediaTaxonomyItem) => ReactNode;
};

function GroupTable({ group, includeInactive, rowActions }: Pick<Props, "group" | "includeInactive" | "rowActions">) {
  const { t } = useI18n();
  const { data, isLoading } = useTaxonomy(group, includeInactive);
  if (isLoading || !data) return <LogoSpinner />;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-16">ID</TableHead>
          {group === "products" ? <TableHead>{t("media.taxonomy.col.code")}</TableHead> : null}
          <TableHead>{t("media.taxonomy.col.name")}</TableHead>
          {group === "content-types" ? <TableHead>{t("media.taxonomy.col.group")}</TableHead> : null}
          <TableHead>{t("media.taxonomy.col.active")}</TableHead>
          {rowActions ? <TableHead className="w-10" /> : null}
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.items.map((item) => (
          <TableRow key={item.id}>
            <TableCell className="font-mono text-xs">{item.id}</TableCell>
            {group === "products" ? <TableCell className="font-mono text-xs">{item.code}</TableCell> : null}
            <TableCell>{item.name}</TableCell>
            {group === "content-types" ? <TableCell>{item.group}</TableCell> : null}
            <TableCell><Switch checked={item.is_active} disabled aria-label={t("media.taxonomy.col.active")} /></TableCell>
            {rowActions ? <TableCell>{rowActions(item)}</TableCell> : null}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function TaxonomyTabs({ group, onGroupChange, includeInactive, onIncludeInactiveChange, role, headerActions, rowActions }: Props) {
  const { t } = useI18n();
  const toolbar = (
    <div className="flex flex-wrap items-center justify-between gap-2 py-3">
      <div className="flex items-center gap-2">
        {mediaCan(role, "taxonomy.include_inactive") ? (
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={includeInactive} onCheckedChange={(v) => onIncludeInactiveChange(v === true)} />
            {t("media.taxonomy.includeInactive")}
          </label>
        ) : null}
      </div>
      <div className="flex gap-2">{headerActions}</div>
    </div>
  );
  return (
    <Tabs
      active={group}
      onChange={(k) => onGroupChange(k as TaxonomyGroup)}
      tabs={TAXONOMY_GROUPS.map((g) => ({
        key: g,
        label: t(`media.taxonomy.group.${g}`),
        content: (
          <div className="rounded-lg border">
            {toolbar}
            <GroupTable group={g} includeInactive={includeInactive} rowActions={rowActions} />
          </div>
        ),
      }))}
    />
  );
}
```
(Kiểm tra API thật của `Checkbox`/`Switch` trong `components/ui` — nếu là input thuần thì dùng `onChange`.)

### A.4 `ReportKpiRow.tsx` (Task 12)
```tsx
import { Card, CardContent } from "@/components/ui/card";
import { useI18n } from "@/i18n";
import type { MediaReportOverview } from "@/lib/api/media";

export function ReportKpiRow({ overview }: { overview: MediaReportOverview }) {
  const { t } = useI18n();
  const tiles = [
    ["media.reports.kpi.total", overview.total_contents],
    ["media.reports.kpi.missingTags", overview.missing_tags],
    ["media.reports.kpi.missingEvaluations", overview.missing_evaluations],
  ] as const;
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {tiles.map(([key, value]) => (
        <Card key={key}>
          <CardContent className="pt-6">
            <p className="text-xs font-medium text-muted-foreground">{t(key)}</p>
            <p className="text-2xl font-semibold tabular-nums">{value}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
```

## Phụ lục B — Metric (spec §7)

Trong Task 6, tạo thêm `backend/apps/media/metrics.py` theo khuôn `apps/foxia/metrics.py` (fallback no-op khi thiếu prometheus_client):
```python
media_proxy_requests_total = Counter(
    "desi_media_proxy_requests_total", "Request proxy Media theo route, chế độ credential và kết quả",
    ["route", "mode", "status"],
)
```
và trong `transport/errors.py::media_errors` sau khi có response: `media_proxy_requests_total.labels(route=request.resolver_match.url_name or "-", mode=getattr(request, "_media_mode", "-"), status=str(response.status_code)).inc()`; `MediaProxy.__init__` gán `request`-independent → để đơn giản, view gọi `request._media_mode = proxy.cred.kind` ngay sau khi tạo proxy (một dòng trong `_auth` wrapper không được vì chưa có proxy — chấp nhận `mode="-"` cho `me`/lỗi trước khi resolve).

## Phụ lục C — Ghi chú nhất quán

- `describe_me` (Task 6) phải bọc `MediaProxy(user)` trong `try/except MediaError` → trả `{**empty, "provisioned": True, "connection_ok": False}` khi key chung trống (resolve raise `MediaDisabled`) — tránh 503 trên `/me`.
- `Guard` cho `media-settings` dựa vào `requiredPermissionFor(section)` trong `app/route-guards.tsx`; kiểm tra hàm này đọc `requires` từ `SECTION_TO_MODULE` — nếu không, dùng `<Guard section="users">`.
- Mọi khoá i18n `media.*` nằm trong `i18n/messages/media.ts`; khoá `users.fields.media*` nằm trong `users.ts`; khoá menu nằm trong `topnav.ts`/`sidebar.ts`/`app.ts`. Test `i18n/messages.test.ts` (nếu kiểm tra vi/en đủ cặp) phải xanh.
