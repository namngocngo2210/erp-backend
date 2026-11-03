# Audit Follow-ups 2026-07-06 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Xử lý toàn bộ tồn đọng của đợt re-audit 2026-07-06 (data-integrity, security hardening, DB optimize, FE cleanup, robustness, tests) + nâng Django lên bản patch mới nhất.

**Architecture:** Django monolith-with-modules (`backend/apps/*`, clean architecture per app) + React 19 SPA (`frontend/`). Mọi thay đổi là vá điểm (surgical), không refactor rộng. Constraint mới đi kèm migration có dedup/backfill khi cần.

**Tech Stack:** Django 6.0.x, DRF, PostgreSQL 16, Celery, React 19 + TS + Vite 7 + Tailwind 4, React Query, openapi-typescript.

## Global Constraints

- Branch: `fix/audit-followups-2026-07-06` (worktree `.claude/worktrees/audit-followups-2026-07-06`). **Subagent PHẢI làm trên branch này, KHÔNG tự tạo branch.**
- Django 6.1 **CHƯA release** (PyPI mới nhất 6.0.6) → sprint 2 nâng 6.0.6; 6.1 ghi backlog chờ release (~08/2026).
- Commit message tiếng Anh, ngắn, **không** Co-Authored-By.
- Chạy test BE bằng docker (image `desi-order-web-test`, xác định network/redis bằng `docker ps` — xem lệnh mẫu cuối file). Sau khi thêm migration mới phải chạy với `--create-db`.
- FE: `npm run lint` + `npm run typecheck` + `npm run test:ci` phải xanh (baseline fail đã biết: OrderFormDialog + 2 mobile-viewport — không phải regression).
- KHÔNG re-flag/đụng: JWT lifetime, seed data, RBAC scope đã đúng, `PositiveIntegerField` (PG đã tự có CHECK >= 0 — audit N4 là false positive, **bỏ**, không làm).

## Tiến độ

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| S1 | Data integrity + security hardening (T1–T6) | ✅ 2026-07-06 |
| S2 | Django 6.0.6 (T7) | ✅ 2026-07-06 |
| S3 | DB optimize (T8–T11) | ✅ 2026-07-06 |
| S4 | FE cleanup (T12–T17; T17 = no-op, đã có sẵn trên master) | ✅ 2026-07-06 |
| S5 | Robustness + tests (T18–T21) | ✅ 2026-07-06 |
| S6 | Docs/runbook operator (T22) | ✅ 2026-07-06 |

---

## Sprint 1 — Data integrity + security hardening

### Task 1: Partial unique cho `ReturnNote.shipping_code` (chặn race 2 scan cùng vận đơn)

**Files:**
- Modify: `backend/apps/inventory/models.py` (Meta của `ReturnNote`, ~dòng 288-298)
- Create: `backend/apps/inventory/migrations/0013_returnnote_shipcode_draft_uniq.py`
- Modify: `backend/apps/inventory/application/services/return_note_service.py:87-118`
- Test: `backend/apps/inventory/tests/test_return_note_shipping_code_unique.py`

**Interfaces:**
- Produces: constraint `returnnote_shipcode_draft_uniq`; `create_return` raise `ValueError` (message như pre-check hiện tại) khi trúng constraint thay vì crash 500.

- [ ] **Step 1: Viết test fail** — `test_return_note_shipping_code_unique.py`:

```python
import threading

from django.db import IntegrityError, close_old_connections
from django.test import TestCase, TransactionTestCase

from apps.inventory.application.services.return_note_service import ReturnNoteService
from apps.inventory.models import ReturnNote, ReturnStatus
from apps.warehouses.models import Warehouse
from apps.orders.models import Product


def _make_wh_and_product():
    wh = Warehouse.objects.create(name="WH T1", code="WHT1")
    p = Product.objects.create(name="SP T1", sku="SKU-T1")
    return wh, p


class ShippingCodeUniqueConstraintTests(TestCase):
    def test_duplicate_draft_unmatched_shipping_code_rejected_at_db(self):
        wh, p = _make_wh_and_product()
        ReturnNote.objects.create(code="TH-A1", warehouse=wh, shipping_code="VN123",
                                  status=ReturnStatus.DRAFT)
        with self.assertRaises(IntegrityError):
            ReturnNote.objects.create(code="TH-A2", warehouse=wh, shipping_code="VN123",
                                      status=ReturnStatus.DRAFT)

    def test_posted_or_matched_duplicates_still_allowed(self):
        wh, p = _make_wh_and_product()
        ReturnNote.objects.create(code="TH-B1", warehouse=wh, shipping_code="VN456",
                                  status=ReturnStatus.POSTED)
        # cùng mã nhưng phiếu cũ đã POSTED → cho phép draft mới (re-scan hợp lệ)
        ReturnNote.objects.create(code="TH-B2", warehouse=wh, shipping_code="VN456",
                                  status=ReturnStatus.DRAFT)
        self.assertEqual(ReturnNote.objects.filter(shipping_code="VN456").count(), 2)

    def test_create_return_service_maps_race_to_value_error(self):
        wh, p = _make_wh_and_product()
        ReturnNoteService.create_return(warehouse_id=wh.id, lines=[], shipping_code="VN789")
        with self.assertRaises(ValueError):
            ReturnNoteService.create_return(warehouse_id=wh.id, lines=[], shipping_code="VN789")
```

(Nếu `Product`/`Warehouse` cần field bắt buộc khác, xem factory ở test có sẵn `backend/apps/inventory/tests/` và tái dùng.)

- [ ] **Step 2: Chạy test, xác nhận FAIL** (constraint chưa có → test 1 fail).
- [ ] **Step 3: Thêm constraint vào model** — trong `ReturnNote.Meta` thêm:

```python
constraints = [
    models.UniqueConstraint(
        fields=["shipping_code"],
        condition=Q(shipping_code__gt="") & Q(source_order__isnull=True)
        & Q(status="draft"),
        name="returnnote_shipcode_draft_uniq",
    ),
]
```

(`from django.db.models import Q` — kiểm tra import đầu file). Sinh migration: `makemigrations inventory` trong container. Migration thêm bước dedup TRƯỚC AddConstraint (RunPython: giữ phiếu draft cũ nhất mỗi `shipping_code`, các phiếu draft trùng còn lại set `status=CANCELLED` + note append `"[dedup audit 2026-07-06]"`; reverse = noop).

- [ ] **Step 4: Map IntegrityError trong service** — trong `create_return`, bọc `ReturnNote.objects.create(...)` hiện có: `except IntegrityError` hiện chỉ dành cho trùng `code`; phân nhánh theo tên constraint:

```python
            except IntegrityError as exc:
                if "returnnote_shipcode_draft_uniq" in str(exc):
                    raise ValueError(
                        f"Mã {shipping_code} đã được ghi nhận ở phiếu trả khác."
                    ) from exc
                # Trùng code do 2 phiếu tạo đồng thời cùng ngày → thử mã kế tiếp.
                continue
```

- [ ] **Step 5: Chạy test pass (`--create-db`), commit** `fix(inventory): enforce unique draft shipping_code on return notes`

### Task 2: Warranty — 409 thay vì 500 khi tạo claim trùng + validate form public

**Files:**
- Modify: `backend/apps/warranty/claim_service.py:38-63`
- Modify: `backend/apps/warranty/claim_views.py:46-53`
- Modify: `backend/apps/warranty/serializers.py:34-38` (WarrantyRegistrationSerializer)
- Modify: `backend/apps/warranty/views.py` (create_warranty_registration — dedupe idempotent)
- Test: `backend/apps/warranty/tests/test_warranty_claim_api.py` (sửa test đang assert raw IntegrityError), `backend/apps/warranty/tests/test_warranty_registration_validation.py` (mới)

**Interfaces:**
- Produces: `WarrantyClaimService.create_claim` raise `WarrantyClaimExists` (không còn ValueError cho case trùng); view map → 409. Registration: phone regex VN `^0\d{9,10}$` sau khi strip; POST trùng `(source, invoice_code, phone)` trả record hiện có (idempotent, không insert).

- [ ] **Step 1: Test fail** — thêm test: 2 request tạo claim cùng order → request 2 nhận 409 (hiện tại raise IntegrityError). Registration: phone `"abc"` → 400; POST trùng 2 lần → chỉ 1 row trong DB.
- [ ] **Step 2: Sửa `create_claim` + `ingest_claim_by_shipping_code`** — cả hai: bỏ pattern exists-then-create trần, bọc:

```python
        from django.db import IntegrityError, transaction

        fields = {k: data[k] for k in _EDITABLE_FIELDS if k in data}
        if WarrantyClaim.objects.filter(order=order, deleted_at__isnull=True).exists():
            raise WarrantyClaimExists()
        try:
            with transaction.atomic():
                return WarrantyClaim.objects.create(order=order, created_by=created_by, **fields)
        except IntegrityError as exc:
            raise WarrantyClaimExists() from exc
```

Lưu ý: OneToOne `order` unique cả với claim soft-deleted — nếu constraint bắn do claim đã soft-delete, vẫn coi là Exists (business chấp nhận; đơn từng có phiếu).

- [ ] **Step 3: `claim_views.py`** — thêm `except WarrantyClaimExists: return Response({"detail": "Đơn hàng này đã có phiếu bảo hành."}, status=409)` trước nhánh ValueError (import từ claim_service).
- [ ] **Step 4: Registration validators** — `validate_phone`: strip rồi `re.fullmatch(r"0\d{9,10}", value)` else ValidationError "Số điện thoại không hợp lệ."; `validate_note`/`validate_full_name`: cắt `value[:1000]` / max_length model đã có. Trong view `create_warranty_registration`: sau `is_valid`, `existing = WarrantyRegistration.objects.filter(source=..., invoice_code=..., phone=...).first()`; nếu có → trả serializer(existing) 201, không tạo mới.
- [ ] **Step 5: Test pass, commit** `fix(warranty): return 409 on duplicate claim, validate public registration`

### Task 3: Grafana prod — bắt buộc GRAFANA_ADMIN_PASSWORD

**Files:** Modify: `docker-compose.prod.yml:348`

- [ ] **Step 1:** Đổi `GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:-admin}` → `GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:?GRAFANA_ADMIN_PASSWORD is required for the monitoring profile}`.
- [ ] **Step 2:** `docker compose -f docker-compose.prod.yml config --profile monitoring` (không set env) phải lỗi đúng thông báo; set env dummy thì pass. Commit `fix(monitoring): require Grafana admin password in prod compose`

### Task 4: Global statement_timeout mặc định 30s

**Files:**
- Modify: `backend/config/settings_parts/base.py:99-111`
- Test: `backend/apps/common/tests/test_db_statement_timeout.py` (mới)

**Interfaces:**
- Produces: env `DB_STATEMENT_TIMEOUT_MS` (default `30000`, `0` = tắt); áp vào `OPTIONS["options"]`. Lệnh quản trị migrate/makemigrations/squashmigrations KHÔNG bị áp (index build dài).

- [ ] **Step 1: Test fail:**

```python
import subprocess, sys
from django.db import connection
from django.test import TestCase


class StatementTimeoutTests(TestCase):
    def test_default_statement_timeout_applied(self):
        with connection.cursor() as cur:
            cur.execute("SHOW statement_timeout")
            self.assertEqual(cur.fetchone()[0], "30s")
```

- [ ] **Step 2: Implement trong base.py** (thay dòng `"OPTIONS": {"connect_timeout": 10},`):

```python
# Runaway-query ceiling toàn cục (audit 2026-07-06 N2). Report dài hạn tự nâng
# cục bộ qua apps.common.db_guards.statement_timeout. 0 = tắt. Các lệnh quản trị
# chạy migration cần build index dài → không áp.
_STMT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "30000"))
_MIGRATION_COMMANDS = {"migrate", "makemigrations", "squashmigrations", "sqlmigrate"}
_IS_MIGRATION_CMD = len(sys.argv) > 1 and sys.argv[1] in _MIGRATION_COMMANDS

_db_options = {"connect_timeout": 10}
if _STMT_TIMEOUT_MS > 0 and not _IS_MIGRATION_CMD:
    _db_options["options"] = f"-c statement_timeout={_STMT_TIMEOUT_MS}"
```

và `"OPTIONS": _db_options,` (thêm `import sys` đầu file nếu chưa có).

- [ ] **Step 3:** Kiểm tra `apps/common/db_guards.py` — guard per-report SET LOCAL vẫn override được trong transaction (SET LOCAL thắng session default — đọc file xác nhận nó dùng `SET LOCAL statement_timeout`). Nếu guard đặt qua `SET LOCAL` thì không cần sửa.
- [ ] **Step 4:** Test pass; chạy thêm 1 app test bất kỳ (`apps/warranty`) xác nhận không vỡ. Commit `feat(db): add global statement_timeout default (30s, env-tunable)`

### Task 5: Fail-fast khi DEBUG=1 ở production

**Files:** Modify: `backend/config/settings_parts/base.py:148-149`; Test: thêm case vào `backend/apps/common/tests/` (tìm test hiện có của `find_insecure_production_secrets` — grep `assert_production_secrets`; nếu chưa có file test, tạo `test_prod_settings_guard.py`).

- [ ] **Step 1: Test fail:** `assert_production_debug_off(getenv=lambda k, d="": "1" if k == "DJANGO_DEBUG" else d)` raise `ImproperlyConfigured`.
- [ ] **Step 2:** Thêm vào base.py cạnh `assert_production_secrets`:

```python
def assert_production_debug_off(getenv=os.getenv):
    if getenv("DJANGO_DEBUG", "0").strip().lower() in {"1", "true", "yes"}:
        raise ImproperlyConfigured("DJANGO_DEBUG must be 0 when APP_ENV=production.")


if IS_PRODUCTION:
    assert_production_secrets()
    assert_production_debug_off()
```

- [ ] **Step 3:** Test pass, commit `feat(config): fail fast when DEBUG enabled in production`

### Task 6: Sniff magic bytes cho stock-document attachment

**Files:**
- Modify: `backend/apps/inventory/application/services/stock_document_attachment_service.py:34-40`
- Test: `backend/apps/inventory/tests/test_stock_document_attachment_sniff.py` (mới; xem test attachment hiện có để tái dùng fixture/mocks của `document_storage`)

**Interfaces:** signature `add_attachment` KHÔNG đổi; content_type client chỉ còn là hint — type lưu DB là type sniff được.

- [ ] **Step 1: Test fail:** upload `data=b"\x00\x01garbage"` + `content_type="image/png"` → raise `AttachmentValidationError`; upload PNG thật (`b"\x89PNG\r\n\x1a\n" + b"0"*10`) với content_type sai `application/pdf` → vẫn nhận, lưu `content_type="image/png"`.
- [ ] **Step 2: Implement** — thêm vào service:

```python
_MAGIC_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"RIFF", "image/webp"),  # kèm check data[8:12] == b"WEBP"
    (b"%PDF-", "application/pdf"),
)


def _sniff_content_type(data: bytes) -> str | None:
    for magic, ctype in _MAGIC_SIGNATURES:
        if data.startswith(magic):
            if ctype == "image/webp" and data[8:12] != b"WEBP":
                continue
            return ctype
    return None
```

Trong `add_attachment`: `sniffed = _sniff_content_type(data)`; nếu `sniffed is None` hoặc `sniffed not in ALLOWED_CONTENT_TYPES` → raise AttachmentValidationError như cũ; dùng `sniffed` thay `content_type` cho ext/put_object/DB row.

- [ ] **Step 3:** Test pass + chạy lại test attachment cũ. Commit `fix(inventory): sniff magic bytes on stock document uploads`

---

## Sprint 2 — Django 6.0.6

### Task 7: Bump Django 6.0.3 → 6.0.6

**Files:** Modify: `backend/requirements.txt:25`

- [ ] **Step 1:** Đổi pin `Django==6.0.3` → `Django==6.0.6`. Đọc release notes 6.0.4–6.0.6 (bugfix/security-only, không breaking) — nếu offline, chấp nhận patch-level theo policy Django.
- [ ] **Step 2:** Rebuild image test: `docker build -t desi-order-web-test -f <Dockerfile test đã dùng> backend/` (xem memory: image = base + full requirements). Nếu image cũ vẫn chạy được với pip install đè: `docker run ... sh -c 'pip install Django==6.0.6 && python -m pytest apps/ -q --create-db'`.
- [ ] **Step 3:** Chạy FULL suite BE. Baseline flake đã biết: `test_dispatch_stock_cycle::test_cancel_without_return_still_releases` (fail full-suite, pass chạy riêng). Commit `chore(deps): bump Django to 6.0.6`
- [ ] **Step 4:** Ghi backlog trong doc này (mục cuối): "Nâng Django 6.1 khi release (~08/2026); đích dừng 6.2 LTS 04/2027."

---

## Sprint 3 — DB optimize

### Task 8: Index cho dispatch-history trên stock_movements

**Files:** Modify: `backend/apps/inventory/models.py:70-78`; Create migration `backend/apps/inventory/migrations/0014_stockmove_reftype_movetype_created_idx.py`

- [ ] **Step 1:** Thêm vào `StockMovement.Meta.indexes`:

```python
models.Index(
    fields=["reference_type", "movement_type", "created_at"],
    name="stockmove_ref_type_created_idx",
),
```

- [ ] **Step 2:** `makemigrations inventory`. LƯU Ý bảng partitioned: AddIndex thường trên parent là OK (PG tự cascade xuống partition); KHÔNG dùng CONCURRENTLY (PG không hỗ trợ trên partitioned parent).
- [ ] **Step 3:** Chạy test inventory (`--create-db`), xác nhận `dispatch_history_service` tests pass. Commit `perf(inventory): index stock_movements for dispatch history queries`

### Task 9: CheckConstraint cho `return_type`

**Files:** Modify: `backend/apps/inventory/models.py` (ReturnNote.Meta.constraints); migration mới.

- [ ] **Step 1: Test fail:** tạo `ReturnNote(code=..., warehouse=..., return_type="bogus")` → IntegrityError.
- [ ] **Step 2:** Thêm (cùng list constraints Task 1 đã tạo):

```python
models.CheckConstraint(
    condition=Q(return_type__in=ReturnType.values) | Q(return_type=""),
    name="returnnote_return_type_valid",
),
```

- [ ] **Step 3:** makemigrations; test pass; commit `feat(inventory): DB check constraint for return_type values`

### Task 10: Bỏ 3 db_index đơn lẻ low-selectivity trên order_assessments

**Files:** Modify: `backend/apps/orders/models.py:425,432,434`; migration mới (orders).

- [ ] **Step 1:** Bỏ `db_index=True` ở `not_closed_reason_code`, `occupation_code`, `pain_level_code`. **GIỮ** `post_purchase_rating_code` (dùng trong filter `__in` ở 3 service customers). Cập nhật comment dòng 420-421 nêu lý do.
- [ ] **Step 2:** makemigrations orders (RemoveIndex/AlterField); full test orders + customers pass; commit `perf(orders): drop unused single-column indexes on order_assessments`

### Task 11: Không re-query scope kênh nhiều lần trong một response ads-report

**Files:** Modify: `backend/apps/orders/_repositories_reports.py` (các method `_ads_landing_orders_queryset:~520-532`, `summary_ads_daily_breakdown:~660-670`, `_accessible_ads_channel_ids:476-499`); caller view/service compose (grep `summary_ads_` trong `apps/orders` transport/application để tìm chỗ gọi nhiều method liên tiếp).

- [ ] **Step 1:** Thêm tham số mặc định `sale_channel_ids: list[int] | None = None, excluded_ids: set[int] | None = None` vào các method đang gọi `sale_report_channel_ids()`/`_ads_excluded_channel_ids()`; trong thân: `sale_channel_ids = sale_report_channel_ids() if sale_channel_ids is None else sale_channel_ids` (tương tự excluded). Hành vi mặc định KHÔNG đổi.
- [ ] **Step 2:** Ở caller compose (view `ads_report_summary` hoặc service tương ứng): tính một lần và truyền xuống các lời gọi.
- [ ] **Step 3:** Chạy `test_ads_report_scope.py`, `test_ads_report_channel_scope.py`, dashboard summary tests → pass. Commit `perf(orders): compute ads channel scope once per report request`

---

## Sprint 4 — FE cleanup

### Task 12: Gỡ dependency không dùng

**Files:** Modify: `frontend/package.json`

- [ ] **Step 1:** Xác minh 0 import: `grep -r "next-themes\|fontsource" frontend/src frontend/index.html` → trống.
- [ ] **Step 2:** `npm uninstall next-themes @fontsource-variable/bricolage-grotesque @fontsource-variable/geist` trong `frontend/`.
- [ ] **Step 3:** `npm run build` + `npm run typecheck` xanh. Commit `chore(frontend): remove unused dependencies`

### Task 13: Xóa CSS ag-grid chết

**Files:** Modify: `frontend/src/styles.css` (khối `.ag-theme-alpine` bắt đầu ~dòng 295; xác định biên chính xác bằng cách đọc file — xóa TOÀN BỘ selector `.ag-*` và comment liên quan, không đụng rule khác).

- [ ] **Step 1:** Xóa khối; `grep -c "ag-theme\|\.ag-" src/styles.css` → 0.
- [ ] **Step 2:** `npm run build` xanh; mở nhanh 1 page bảng (nếu chạy dev được) xác nhận style bảng không đổi (khối này không match element nào nên vô hại). Commit `chore(frontend): drop dead ag-grid theme CSS`

### Task 14: Tách react-select khỏi chunk eager vendor-ui

**Files:** Modify: `frontend/vite.config.ts:78`

- [ ] **Step 1:** Đổi dòng manualChunks: `if (id.includes("lucide-react") || id.includes("sonner")) return "vendor-ui";` (bỏ `react-select` — để nó tự split vào chunk của các page lazy dùng nó).
- [ ] **Step 2:** `npm run build`; kiểm tra `dist/assets` — không còn react-select trong vendor-ui (build với `mode=analyze` nếu cần). Commit `perf(frontend): stop bundling react-select into eager vendor-ui chunk`

### Task 15: AdsReportPage bỏ react-select

**Files:** Modify: `frontend/src/pages/AdsReportPage.tsx`; Modify: `frontend/eslint.config.js:76` (xóa dòng allowlist)

- [ ] **Step 1:** Đọc AdsReportPage, tìm các `<Select>` từ react-select; thay bằng `components/ui` tương đương: single → `Select`/`SearchableSelect`, multi → `MultiSelect` (xem các page đã migrate làm mẫu — grep `searchable-select` trong src/pages).
- [ ] **Step 2:** Xóa `"src/pages/AdsReportPage.tsx",` khỏi allowlist eslint.
- [ ] **Step 3:** `npm run lint` (0 warning mới) + `npm run typecheck` + test liên quan (`ChannelDailyTable` tests) xanh. QA thị giác nhanh nếu dev server chạy được. Commit `refactor(ads-report): replace react-select with ui select components`

### Task 16: Type 4 module API cuối từ OpenAPI schema

**Files:** Modify: `frontend/src/lib/api/hr.ts`, `hr-attendance.ts`, `warrantyClaims.ts`, `zalo.ts`; Modify: `frontend/src/api/typed.ts` (thêm helper PATCH/DELETE/PUT)

**Interfaces:**
- Produces trong `typed.ts` (theo đúng pattern `apiPost` hiện có):

```typescript
export async function apiPatch<P extends Extract<keyof paths, string>>(
  path: P, body: JsonBody<P, "patch">,
): Promise<JsonResponse<P, "patch">> {
  return fetchJson(path, { method: "PATCH", body: JSON.stringify(body) }) as Promise<JsonResponse<P, "patch">>;
}

export async function apiDelete<P extends Extract<keyof paths, string>>(
  path: P,
): Promise<JsonResponse<P, "delete">> {
  return fetchJson(path, { method: "DELETE" }) as Promise<JsonResponse<P, "delete">>;
}
```

- [ ] **Step 1:** Thêm `apiPatch`/`apiDelete` vào typed.ts (code trên). Lưu ý path có param động (`/api/hr/attendance/locations/{id}`): schema paths dùng template literal — theo mẫu module đã migrate (xem `src/lib/api/orders.ts` xử lý path param thế nào; nếu orders.ts nội suy chuỗi rồi cast, làm giống hệt).
- [ ] **Step 2:** Migrate từng file: thay `fetchJson` + type tay bằng `apiGet`/`apiPost`/`apiPatch`/`apiDelete` với path literal từ `schema.d.ts`; row types lấy `components["schemas"][...]`. Nếu endpoint nào THIẾU trong schema.d.ts (chưa annotate BE) → chạy `npm run gen:api` với BE local; nếu vẫn thiếu thì để nguyên hàm đó + comment `// TODO(D9): missing in OpenAPI schema` và ghi vào mục backlog doc này.
- [ ] **Step 3:** `npm run typecheck` + `npm run test:ci` xanh. Commit `refactor(frontend): type hr/warranty/zalo api modules from openapi schema`

### Task 17: CronJobsPage → React Query polling

**Files:** Modify: `frontend/src/pages/CronJobsPage.tsx`; Create: `frontend/src/features/cron/queries.ts` (theo mẫu `src/features/orders/queries.ts`)

- [ ] **Step 1:** Đọc CronJobsPage (fetch + setInterval ~dòng 75-81). Tạo `queries.ts` với `useQuery({ queryKey: ["cron-jobs"], queryFn: ..., refetchInterval: <interval hiện tại> })`.
- [ ] **Step 2:** Thay useEffect+setInterval bằng hook; giữ nguyên UI. Mutation (trigger/run job nếu có) → `useMutation` + invalidate.
- [ ] **Step 3:** lint + typecheck + test:ci xanh. Commit `refactor(cron): use react-query polling on CronJobsPage`

---

## Sprint 5 — Robustness + tests

### Task 18: Autoretry cho task event-driven `process_landing_ingest`

**Files:** Modify: `backend/apps/orders/infrastructure/tasks/order_tasks_impl/ingest_job_tasks.py:134-139`; Test: thêm vào test ingest hiện có (grep `process_landing_ingest` trong tests).

- [ ] **Step 1:** Đọc toàn bộ task để xác nhận điểm ghi DB đầu tiên; idempotency guard: nếu `trace_id` có và trace đã ở trạng thái thành công (đọc field status của `LandingIngestTrace`) → return sớm (chống double-create khi retry sau khi đã ghi).
- [ ] **Step 2:** Đổi decorator:

```python
@shared_task(
    bind=True,
    ignore_result=True,
    name="apps.orders.tasks.process_landing_ingest",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=3,
)
```

(`from django.db import OperationalError`). CHỈ retry lỗi hạ tầng DB — không retry ValueError/validation (đơn hỏng thì retry vô ích).

- [ ] **Step 3:** Test: giả lập OperationalError lần 1 (mock service raise) → task retry và thành công lần 2, chỉ 1 order được tạo. Commit `feat(orders): retry landing ingest task on transient db errors`

### Task 19: IMPORT_CREATE_ONLY_TARGETS ra settings

**Files:** Modify: `backend/apps/kiotviet/tasks.py:252-259`; Modify: `backend/config/settings_parts/base.py` (khu settings kiotviet — grep `KIOTVIET_` trong base.py để đặt cạnh); Test: `backend/apps/kiotviet/tests/test_import_create_only_targets.py` (đã có — mở rộng).

- [ ] **Step 1:** settings: `KIOTVIET_IMPORT_CREATE_ONLY_TARGETS = os.getenv("KIOTVIET_IMPORT_CREATE_ONLY_TARGETS", "4:231174,6:89084,5:30965")` + parser trong tasks.py:

```python
def _parse_targets(raw: str) -> tuple[tuple[int, str], ...]:
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        channel_id, _, branch_id = part.partition(":")
        out.append((int(channel_id), branch_id))
    return tuple(out)
```

`iter_import_channels` đọc `_parse_targets(settings.KIOTVIET_IMPORT_CREATE_ONLY_TARGETS)` thay hằng số (giữ hằng số cũ làm default fallback không cần thiết — xóa hẳn, default nằm ở settings).

- [ ] **Step 2:** Test: override_settings với chuỗi khác → iter đổi theo; chuỗi rỗng → không yield gì; test cũ pass giữ nguyên hành vi default. Commit `refactor(kiotviet): make create-only import targets env-configurable`

### Task 20: Test cho `apps/dispatch`

**Files:** Create: `backend/apps/dispatch/tests/__init__.py`, `backend/apps/dispatch/tests/test_dispatch.py`

- [ ] **Step 1:** Đọc `backend/apps/dispatch/` (toàn bộ app — nhỏ) để nắm public API (`dispatch()`, registry).
- [ ] **Step 2:** Viết unit tests: đăng ký handler → dispatch gọi đúng handler với payload; dispatch key chưa đăng ký → hành vi hiện tại (raise/log — assert đúng hành vi thực tế, characterization); đăng ký trùng key → hành vi thực tế. Tối thiểu 4 test.
- [ ] **Step 3:** Test pass, commit `test(dispatch): cover task dispatch registry`

### Task 21: Smoke tests `warehouses` + `cron_jobs`

**Files:** Create: `backend/apps/warehouses/tests/test_warehouse_api.py`, `backend/apps/cron_jobs/tests/test_cron_jobs_api.py` (+ `__init__.py` nếu thiếu)

- [ ] **Step 1:** Đọc urls/views của 2 app; viết mỗi app 3-5 test: list endpoint trả 200 với user có quyền, 403 với user thiếu section (RBAC gate), tạo/sửa hợp lệ (nếu có endpoint write), model `__str__`/constraint cơ bản.
- [ ] **Step 2:** Test pass, commit `test(warehouses,cron-jobs): add baseline api tests`

---

## Sprint 6 — Docs/runbook operator

### Task 22: Runbook env vars + backlog

**Files:** Modify: `docs/audit-followups-2026-07-06.md` (file này — mục Backlog & Runbook dưới); Modify: `.env.example` / `backend/.env.example` (nếu tồn tại — grep; nếu không, chỉ docs)

- [ ] **Step 1:** Điền mục Runbook dưới với hướng dẫn set: `GRAFANA_ADMIN_PASSWORD` (bắt buộc sau Task 3), `LARK_ALERT_WEBHOOK_URL` (alertmanager đang noop), `DESI_BACKUP_REQUIRE_OFFSITE=1` + `DESI_BACKUP_S3_DEST`, `SENTRY_DSN`, `DB_STATEMENT_TIMEOUT_MS` (mới, default 30000), `KIOTVIET_IMPORT_CREATE_ONLY_TARGETS` (mới, default giữ 3 kênh hiện tại).
- [ ] **Step 2:** Cập nhật bảng Tiến độ + mục "Đã làm / Chưa làm" cuối file. Commit `docs: audit follow-ups runbook and progress`

---

## Backlog (không làm đợt này — có chủ đích)

- Nâng Django 6.1 khi release (~08/2026); đích dừng: 6.2 LTS (04/2027).
- Tách `use-orders-data.ts` (2392 dòng) và `sync_invoices_service.py` (1114 dòng) — refactor riêng khi đụng vào, rủi ro cao nếu batch.
- Test coverage thật cho `zalo_bridge`/`google_ads`/`facebook_ads` — cần domain fixtures, làm incremental.
- Audit N4 (CHECK quantity>=0): FALSE POSITIVE — PostgreSQL đã có check tự sinh từ PositiveIntegerField.
- `audit_logs` partition: hạ ưu tiên (retention purge 90d đã chạy).
- vitest 4 / eslint-config-prettier 10 / globals 16: nâng tooling gom đợt sau.

### Phát sinh trong đợt thực hiện (cần quyết định/theo dõi)

- ~~`cron_jobs._require_admin` là code chết~~ → **ĐÃ GIẢI QUYẾT 2026-07-06: user quyết định BỎ trang Cron Jobs.** Gỡ toàn bộ: app `apps/cron_jobs` (view gate hỏng + migration 0001 nguy hiểm DROP bảng django_celery_beat — hiểm họa fresh-install với DatabaseScheduler), route/nav/section FE, section `cron-jobs` khỏi RBAC catalog + data migration `users.0024` dọn Permission/SectionScope/FieldRule, regen schema.yaml + schema.d.ts. Quản lý lịch cron giờ qua CELERY_BEAT_SCHEDULE (code) + django_celery_beat DB.
- **Baseline test fail mới trên master** (không phải regression của branch này, đã xác minh A/B trên Django 6.0.3): 2 date-rollover `test_report_trends.py` (dùng days_ago=8/10, vỡ khi chạy đầu tháng); 2 collision role "Administrator" (`test_role_admin_api`/`test_rbac_resolver`, seed isolation); `test_issue_beyond_stock_triggers_alert` (full-suite-only). FE: `modules.test.ts` fail section "dispatch-history" chưa đăng ký module. Cần đợt fix test-isolation riêng.
- **Ingest trace residual gap** (đã ghi comment trong code): lỗi rơi đúng giữa commit đơn và ghi trace → retry dựa vào LeadFilter same-day dedup làm backstop. Đóng hoàn toàn = gộp ghi trace vào transaction tạo đơn (đánh đổi isolation), để sau.
- **WarrantyRegistration dedupe** là check-then-create app-level (không có DB constraint — tránh migration dedup trên data prod); đủ cho double-submit, không tuyệt đối dưới concurrency. Nếu spam thật xuất hiện: thêm partial unique sau khi dọn data.
- **`apiPost`/`apiPatch` typed helper chỉ suy type cho response 200** — endpoint 201-only/bodyless giữ `fetchJson` + type schema thủ công (đã comment tại chỗ). Nếu muốn phủ nốt: mở rộng JsonResponse union 200|201.

## Runbook operator (Task 22)

Các env cần set trên server prod (deploy kế tiếp):

| Env | Bắt buộc? | Ghi chú |
|---|---|---|
| `GRAFANA_ADMIN_PASSWORD` | **BẮT BUỘC** khi bật profile monitoring | Compose giờ fail nếu thiếu (hết fallback `admin/admin`) |
| `LARK_ALERT_WEBHOOK_URL` | Nên set ngay | Alertmanager đang route về receiver noop — alert bắn ra nhưng không tới ai |
| `DESI_BACKUP_REQUIRE_OFFSITE=1` + `DESI_BACKUP_S3_DEST`/`RCLONE_DEST` | Nên set ngay | Backup hiện local-only trên VPS |
| `SENTRY_DSN` | Nên set | Code BE+FE đã wire sẵn, rỗng = tắt |
| `DB_STATEMENT_TIMEOUT_MS` | Không (default 30000) | Trần chống runaway query toàn cục; `0` = tắt. **Lưu ý:** migration chạy qua `manage.py migrate` được miễn trần; nhưng pytest build test-DB mới thì KHÔNG — nếu thêm migration backfill nặng >30s, set `DB_STATEMENT_TIMEOUT_MS=0` cho lần chạy `--create-db` đầu. Celery workers cũng chịu trần 30s (argv của worker không match lệnh `migrate`) — task backfill nặng chạy một lần phải tự bọc `apps.common.db_guards.statement_timeout` để nâng trần cục bộ thay vì đổi env toàn cục |
| `KIOTVIET_IMPORT_CREATE_ONLY_TARGETS` | Không (default `4:231174,6:89084,5:30965`) | Đổi kênh/branch import create-only 15' không cần deploy; format `channel:branch,...`; phần tử hỏng bị skip + warning |

Sau khi set env: `docker compose -f docker-compose.prod.yml up -d` lại web/celery (và grafana nếu dùng profile monitoring).

## Lệnh test tham chiếu

```bash
# BE (Git Bash, từ repo root của worktree; xác minh stack bằng `docker ps` trước)
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
# ⚠️ KHÔNG BAO GIỜ cache.clear() trong test (FLUSHDB đè Redis thật).

# FE
cd frontend && npm run lint && npm run typecheck && npm run test:ci
```

## Nhật ký sprint (2026-07-06, thực hiện bằng subagent-driven development, review 2 lớp mỗi task)

**S1 (T1–T6, đã làm hết):** partial unique `returnnote_shipcode_draft_uniq` + dedup migration + map IntegrityError (T1); warranty 409 + validate phone VN + dedupe idempotent (T2, kèm fix test hermetic bypass throttle — test cũ đốt quota 20/h trên Redis thật); Grafana password `:?` (T3); statement_timeout global 30s env-tunable, miễn cho lệnh migrate (T4 — bonus: sửa bug `db_guards` reset timeout về '0' làm mất default mới trên pooled connection); guard DEBUG prod (T5); sniff magic bytes stock-doc upload, mirror HR (T6).

**S2 (T7):** Django 6.0.3→6.0.6; full suite 715 pass / 6 fail — cả 6 xác minh pre-existing bằng chạy đối chứng trên 6.0.3 (byte-for-byte). Gotcha mới: `pip install` trong container test ghi vào `backend/.local` (bind mount) — phải xóa, không commit.

**S3 (T8–T11):** index `(reference_type, movement_type, created_at)` cho dispatch-history (bảng partitioned, AddIndex thường); CheckConstraint `return_type`; bỏ 3 db_index low-selectivity trên order_assessments (GIỮ `post_purchase_rating_code` — có filter `__in` thật); thread scope kênh ads qua param, view compose tính 1 lần (mở rộng có chủ đích sang dashboard_summary vì `repositories.py:169` cùng chuỗi gọi).

**S4 (T12–T17):** gỡ next-themes + 2 fontsource; xóa ~360 dòng CSS ag-grid chết (CSS bundle 115→105kB); react-select ra khỏi vendor-ui eager (chunk 134→62kB); AdsReportPage sang MultiSelect components/ui + rút khỏi eslint allowlist; 4 module API cuối typed từ schema (bonus phát hiện: BE share @extend_schema giữa GET/POST làm response code POST khai sai — đã type tay đúng tại chỗ); T17 CronJobsPage hóa ra ĐÃ dùng React Query sẵn trên master (finding audit stale) — no-op.

**S5 (T18–T21):** autoretry OperationalError cho `process_landing_ingest` + idempotency guard evidence-based theo `trace.order_id` (reviewer bắt lỗi guard v1 dead-code trên đường retry — đã sửa, except không downgrade trace CREATED); `KIOTVIET_IMPORT_CREATE_ONLY_TARGETS` ra env (malformed skip+warn); 11 test `apps/dispatch` + 7 `warehouses` + 7 `cron_jobs` (characterization; lộ bug `_require_admin` chết — xem Phát sinh).

**Chưa làm (chuyển backlog):** như mục Backlog; thêm các mục "Phát sinh trong đợt thực hiện".

**Bổ sung cùng branch (sau audit):** bỏ trang Cron Jobs (commit 024bf8ea — gỡ FE page/route/section + app `cron_jobs` + RBAC data-migration users.0024; khử luôn migration cũ DROP bảng django_celery_beat). Merge master 2026-07-06 chiều (commit 1a391ddb): đánh lại số migration inventory 0014-0016 nối sau `0013_returnnoteline_return_type` của master (tránh multiple-leaf), hòa fan-out import create-only (master) với targets đọc env (branch), regen schema; verify BE 480 pass (2 baseline đã biết), FE 211/211.
