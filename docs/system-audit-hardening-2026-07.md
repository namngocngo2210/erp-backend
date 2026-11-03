# System Audit Hardening 2026-07 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) tracking. Mọi subagent PHẢI làm việc trên branch `fix/system-audit-hardening-2026-07` tại worktree hiện tại — KHÔNG tự tạo branch mới.

**Goal:** Xử lý toàn bộ phát hiện của đợt audit toàn hệ thống 2026-07-03 (5 mảng: security, ops/monitoring, database, backend architecture, frontend, infra/tooling).

**Architecture:** Không rewrite, không đổi hướng kiến trúc (modular monolith giữ nguyên). Sửa theo work-package (WP) độc lập theo subsystem; mỗi WP là một chuỗi commit riêng biệt, test kèm theo. Các thay đổi có rủi ro vận hành (đổi hành vi prod) đều env-gated hoặc kèm runbook.

**Tech Stack:** Django 6 / DRF / Celery / Postgres 16 / React 19 + Vite / docker compose / Prometheus + Alertmanager.

## Global Constraints

- Branch: `fix/system-audit-hardening-2026-07` (từ master `fb50db7d`). Commit message tiếng Anh, ngắn, KHÔNG footer Co-Authored-By.
- Không đổi API contract FE↔BE trừ khi có mục ghi rõ. Không đổi format lỗi DRF hiện có (chỉ bổ sung, không thay).
- Migration phải reversible khi khả thi; migration đụng bảng lớn (orders, customers, audit_logs) phải non-blocking (CREATE INDEX CONCURRENTLY qua `AddIndexConcurrently` / `atomic = False`).
- Test BE: chạy qua docker image `desi-order-web-test` trên network `desi-order_desi-net` (xem memory running-backend-tests); tối thiểu `ruff check` + pytest app bị sửa. Test FE: `npm run typecheck && npm run test` trong `frontend/`.
- Baseline fail đã biết (KHÔNG phải regression): return-rate BE; OrderFormDialog + 2 mobile-viewport FE; `modules.test.ts` section-module.
- Mỗi hạng mục hoàn thành: tick checkbox ở đây + ghi chú nếu lệch kế hoạch.

## Trạng thái tổng quan

| WP | Nội dung | Trạng thái |
|---|---|---|
| WP1 | Security critical & hardening | ✅ |
| WP2 | Ops: alerting, backup, error tracking, logging | ✅ |
| WP3 | Database | ✅ |
| WP4 | Backend architecture | ✅ |
| WP5 | Frontend quick wins + repo hygiene | ✅ |
| WP6 | Frontend structural | ✅ |
| WP7 | Infra/tooling | ✅ |
| WP8 | Refactor lớn (kiotviet, tests, Vite 7) | ✅ |
| RB | Runbook prod (việc cần người vận hành) | ⬜ CHỜ VẬN HÀNH |

### Verify tổng cuối (2026-07-03)
- Backend (image desi-order-web-test, full suite `pytest apps`): **535 passed / 18 failed** — cả 18 là baseline có sẵn trên master, đã đối chứng trên HEAD sạch: 2 users RBAC (`rbac_roles_name_key`), 5 `test_report_trends` (flake đầu tháng), 11 kiotviet (`test_access_token` 6 + `test_invoice_webhook` 4 + `test_import` 1 — hỏng từ commit "remove celery" cũ). **0 regression.**
- Frontend: `typecheck` ✅ · `lint:strict` 0 warning ✅ · `build` (Vite 7) ✅ · `vitest run` **130/131** (fail duy nhất `modules.test.ts` — baseline). 
- Lưu ý chạy test BE local: cần `ENCRYPTION_KEY` là Fernet key hợp lệ (migration kiotviet 0009) và drop test DB cũ nếu từng chạy fail (`DROP DATABASE test_desi_order`).

### Backlog phát sinh trong quá trình thực hiện (ngoài 3.9)
1. **Baseline tests kiotviet hỏng sẵn trên master** (11 test — patch `apps.dispatch.dispatch` trùng, `bump_cache_version` undefined F821, kỳ vọng total lệch behavior) — cần sprint sửa test riêng.
2. **`modules.test.ts`** fail baseline: `APP_MODULES` thiếu sections `roles/shipments/warranty` so với `ALL_SECTIONS` — cần bổ sung vào module map.
3. **WP6.5 phát hiện hành vi cần quyết định nghiệp vụ** (đã đặc tả trong test, chưa sửa): (a) `normalizeDecimalInput("2.500.000")` → `"2.500000"` — dấu chấm kiểu VN bị hiểu là thập phân; (b) `order_source: "kol"` bị drop âm thầm ở create/patch; (c) `customer_gender: null` bất đối xứng create vs patch.
4. **OpenAPI spec gaps** (WP6.2): thiếu endpoints `/api/customers/report/{reactivation,trends,channel-quality}`, `/api/audit-logs/export/`; annotation generic ở ~8 schema; nghi lệch: `Product.price` (decimal-string vs number FE), `KiotVietChannelForSync.branch_id` (number vs string), Department serializer thiếu `allowed_sections`.
5. **Hai stack sync KiotViet fork-drift** (WP8.1): `SyncInvoicesService` (reconcile) có fix mới hơn CLI service (pagination public-API, skip cancelled-mirror, delivery cap); CLI có `--compare-invoice` mà service thiếu — cần hợp nhất có chủ đích ở sprint riêng (luồng tiền, cần test kỹ).
6. `celery-exporter` image còn `:latest` (ngoài danh sách pin 7.2); recharts 2→3 + vite 8/vitest 4 đợt sau; `hr.ts`/`hr-attendance.ts` typed hoá khi được phép sửa features/hr types; `warrantyClaims.ts` typed hoá được sau regen schema (`cb200307`).
7. `uv sync --frozen` trong backend-ci rơi về fallback `uv pip install -r requirements.txt` sau khi xoá root pyproject — theo dõi run CI đầu sau merge.

---

## WP1 — Security critical & hardening

### 1.1 Gỡ seed admin123 khỏi prod ✅ NGHIÊM TRỌNG
- Files: `docker-compose.prod.yml:186`, `backend/apps/users/management/commands/impl/seed_initial_data_impl.py`
- [x] Xoá dòng `python manage.py seed_initial_data || true` khỏi command web trong `docker-compose.prod.yml`.
- [x] Sửa `seed_initial_data_impl.py`: (a) đầu `handle()` — nếu `settings.IS_PRODUCTION` thì raise `CommandError("seed_initial_data is dev-only")`; (b) bỏ toàn bộ logic reset mật khẩu `if not check_password(...)` — chỉ set password trong `defaults` lúc `get_or_create` tạo mới (dùng `User.objects.create_user`/`set_password` khi `created=True`); (c) password đọc từ env `SEED_ADMIN_PASSWORD`/`SEED_STAFF_PASSWORD`, fallback giá trị cũ chỉ cho dev.
- [x] Test: `backend/apps/users/tests/test_seed_initial_data.py` — 3 case: chạy 2 lần không reset password đã đổi; production raise CommandError; tạo mới dùng env password.

### 1.2 Base compose bind Postgres 127.0.0.1
- File: `docker-compose.yml:9`
- [x] `"5432:5432"` → `"127.0.0.1:5432:5432"`.

### 1.3 Mở rộng prod secret guard
- File: `backend/config/settings_parts/base.py:110-120`
- [x] Thêm vào guard `IS_PRODUCTION`: fail-fast nếu `REDIS_PASSWORD` rỗng, `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` == `minioadmin`, `INTERNAL_AUTH_TOKEN` rỗng, `ENCRYPTION_KEY` rỗng/default.
- [x] Test: unit test set env production + default secrets → `ImproperlyConfigured`.

### 1.4 Throttle keyed đúng client IP sau Cloudflare+nginx
- Files: `backend/apps/users/views.py:67-81`, `backend/apps/warranty/views.py:16-28`, settings
- [x] Tạo `backend/apps/common/throttling.py` — `ClientIpMixin.get_ident()`: ưu tiên header `CF-Connecting-IP`, fallback XFF hop đầu tin cậy theo `THROTTLE_TRUSTED_PROXIES` (env, default 1), fallback `REMOTE_ADDR`.
- [x] LoginRateThrottle + warranty throttle dùng mixin. Test: request có/không CF header → key khác nhau.

### 1.5 nginx client_max_body_size
- File: `nginx/customer.delax.vn.conf.prod-template`
- [x] Thêm `client_max_body_size 12m;` vào server block (khớp `DATA_UPLOAD_MAX_MEMORY_SIZE` 10MB + headroom).

## WP2 — Ops: alerting, backup, error tracking, logging

### 2.1 Sửa alerting prod đứt gãy
- Files: `docker-compose.prod.yml` (profile monitoring), `monitoring/prometheus.full.yml`, `monitoring/alertmanager.yml`
- [x] Prod compose: mount `./monitoring/alerts.yml:/etc/prometheus/alerts.yml:ro` vào service prometheus.
- [x] `prometheus.full.yml`: thêm section `alerting:` trỏ `alertmanager:9093`.
- [x] Prod compose: thêm service `alertmanager` (image pin `prom/alertmanager:v0.27.0`, mount `alertmanager.yml`, mem_limit, restart, profile monitoring) + service `celery-exporter` (như `monitoring/docker-compose.yml:105`) để rule CeleryQueueBacklog có data.
- [x] `alertmanager.yml`: receiver webhook Lark đọc URL từ env qua `am_env_secret` pattern — dùng `--config.expand-env` hoặc entrypoint sed template; giữ receiver noop làm fallback khi env trống, kèm comment hướng dẫn. (URL thật do vận hành điền — xem RB.2)

### 2.2 Backup offsite + bằng chứng sống
- Files: `scripts/desi-backup.sh`, `monitoring/alerts.yml`
- [x] Script: sau khi backup thành công ghi textfile metric `desi_backup_last_success_timestamp` vào `/var/lib/node_exporter/textfile/desi_backup.prom` (đường dẫn env-configurable, skip nếu không có thư mục).
- [x] `alerts.yml`: rule `BackupStale` — `time() - desi_backup_last_success_timestamp > 93600` (26h) → critical.
- [x] Prod compose: node-exporter thêm mount textfile dir + flag `--collector.textfile.directory`.
- [x] Offsite: script đã có `DESI_BACKUP_S3_DEST` — bổ sung comment hướng dẫn MinIO/B2 + exit non-zero nếu `DESI_BACKUP_REQUIRE_OFFSITE=1` mà không có dest. (Bật thật = RB.3)

### 2.3 Sentry (env-gated)
- Files: `backend/requirements.txt`, `backend/config/settings_parts/base.py` (hoặc file mới `settings_parts/observability.py`)
- [x] Thêm `sentry-sdk[django,celery]` vào requirements (pin version mới nhất).
- [x] Init chỉ khi `SENTRY_DSN` env không rỗng: DjangoIntegration + CeleryIntegration, `traces_sample_rate` env (default 0), `send_default_pii=False`, `environment=APP_ENV`.
- [x] Frontend: `@sentry/react` env-gated qua `VITE_SENTRY_DSN` trong `frontend/src/main.tsx` (chỉ init khi có DSN).

### 2.4 JSON logging + root logger
- File: `backend/config/settings_parts/base.py:474-490`
- [x] Thêm `python-json-logger` vào requirements; formatter JSON khi `LOG_FORMAT=json` (env, default giữ plain-text để không phá dev), root logger thay danh sách app thủ công.
- [x] Middleware request-id: dùng `X-Request-ID` inbound hoặc uuid4, đưa vào log record qua filter + trả response header. File mới `backend/apps/common/request_id.py`, đăng ký MIDDLEWARE. Test đơn giản.

## WP3 — Database

### 3.1 Retention audit_logs
- Files: `backend/apps/audit/tasks.py` (mới), `backend/config/settings_parts/base.py` (beat), `backend/apps/audit/middleware_impl.py`
- [x] Task `purge_old_audit_logs`: xoá theo batch 10k `timestamp < now - AUDIT_LOG_RETENTION_DAYS` (env, default 90), lock Redis chống double-run, time_limit 600.
- [x] Beat: chạy 03:30 hàng ngày.
- [x] Middleware: không lưu `response_data` cho request GET thành công (2xx) — chỉ giữ status; env `AUDIT_LOG_GET_RESPONSE=0` default tắt.
- [x] (audit 2026-09-23 T1) Middleware: KHÔNG ghi dòng audit cho request đọc thành công — GET 2xx và POST tới endpoint list (`/api/orders`, `/landing`, `/sheet3`, `/shipments`) — trừ đường đọc nhạy cảm (`customer-phone`, `/export`, `/invoice`, `/print`, `/api/audit-logs`, `/admin`, `/api/users`). Env `AUDIT_LOG_SKIP_READS=0` trả về hành vi cũ; danh sách tuỳ chỉnh qua `AUDIT_LOG_SENSITIVE_READ_PATHS` / `AUDIT_LOG_POST_LIST_PATHS`.
- [x] Test: tạo log cũ/mới → purge đúng; GET không lưu response_data.

### 3.2 Unique phone_normalized cho customers
- Files: `backend/apps/customers/models.py`, `services.py:39-58`, migration mới
- [x] Migration 2 bước, `atomic = False`: (a) RunPython dedup — gộp customer trùng `phone_normalized` (giữ bản ghi có order nhiều nhất/cũ nhất, repoint FK orders → survivor, soft-delete bản thừa); (b) `AddConstraintNotValid`/partial unique index CONCURRENTLY: `UNIQUE (phone_normalized) WHERE phone_normalized <> '' AND deleted_at IS NULL`.
- [x] `get_or_create_by_phone`: bọc create trong try/except `IntegrityError` → re-fetch survivor.
- [x] Test: race giả lập (tạo trước rồi gọi service), dedup migration test với 2 customer trùng.

### 3.3 Drop index trùng/thừa
- Files: `backend/apps/orders/models.py:409-415,511`, `backend/apps/customers/models.py:23`, migrations
- [x] Bỏ `db_index=True` trên 5 field order_assessments (giữ Meta.indexes), `OrderJourneyEvent.event_type`, `Customer.code` → makemigrations (Django sinh drop index). Đã verify trên DB scratch migrate từ đầu: chỉ index auto-name bị drop, index Meta còn nguyên.

### 3.4 Retention landing_ingest_traces + order_journey_events
- [x] Task `purge_old_ingest_traces` (default 120 ngày) + `purge_old_journey_events` (default 365 ngày, env-gated `JOURNEY_EVENT_RETENTION_DAYS=0` = giữ vĩnh viễn, default 0 để an toàn — bật qua env). Beat 03:45.

### 3.5 N+1 list users
- Files: `backend/apps/users/views.py:362-363`, `serializers.py:118-139`
- [x] Queryset: `.prefetch_related("role_assignments", "department_links", Prefetch("kiotviet_mappings", queryset=...select_related("kiotviet_channel")))` — đặt trong `UserService.admin_list_users()` (service layer), view gọi qua service.
- [x] Serializer methods đọc từ `obj.role_assignments.all()` (prefetch cache) thay vì query mới; fallback an toàn khi không prefetch (test riêng).
- [x] Test: `assertNumQueries` chặn trần (≤6 query cho 10 users).

### 3.6 Channel.code partial unique
- [x] `backend/apps/channels_core/models.py:9` — UniqueConstraint `code` WHERE `deleted_at IS NULL` (kiểm tra dup trước trong migration, fail rõ ràng nếu có).

### 3.7 TextChoices cho field trạng thái tự do
- [x] `StockMovement.reference_type` (order/document/return/reclass — khớp 4 hằng `_REFERENCE_TYPE` trong services) + `AttendanceRecord.source` (manual/self/lark) → TextChoices, migration AlterField no-op DB.
- **LỆCH KẾ HOẠCH — `WarrantyClaim.classification/return_status/accounting_check` KHÔNG thêm choices:** 3 field này là ô nhập TỰ DO trên FE (`WarrantyClaimFormDialog.tsx` dùng `<Input>` thường, không phải select) và đi qua `WarrantyClaimWriteSerializer`/`IngestSerializer` (ModelSerializer) — thêm model choices sẽ khiến DRF sinh ChoiceField và reject mọi giá trị người dùng gõ → vỡ API contract (vi phạm Global Constraints). Muốn siết thì phải chuẩn hoá catalog giá trị + đổi FE sang select trước (đề xuất đưa vào backlog cùng 3.9).

### 3.8 HR FK trần → FK thật
- [x] `SalaryRecord.created_by_id`, `LeaveRequest.approver_id` (`hr/models.py:227,267`) → `ForeignKey(User, on_delete=SET_NULL, null=True, db_constraint=True)` giữ nguyên db_column để không đổi tên cột. Migration kiểm orphan trước khi add constraint. (Field đổi tên Python thành `created_by`/`approver`; attname `created_by_id`/`approver_id` giữ nguyên nên code cũ không đổi; `LeaveRequestSerializer` khai báo tường minh `approver_id` read-only để giữ contract.)

### 3.9 Backlog DB (KHÔNG làm đợt này — lý do ghi rõ)
- ChannelAdAccount M2M thay ArrayField: đổi contract API channels + FE — sprint riêng.
- Squash 92 migrations orders: làm khi không có MR nào mở để tránh conflict.
- Drop `order_phone_idx`: cần số liệu `pg_stat_user_indexes` prod trước (RB.5).

## WP4 — Backend architecture

### 4.1 EXCLUDED_BRANCH_IDS → apps/common
- [x] Tạo `backend/apps/common/business_constants.py`; `orders/repositories.py` re-export (giữ tương thích); import ở customers đổi sang common. *(commit f1f1f5c8 — thực tế có 10 chỗ import ở customers chứ không phải 5; đổi cả 10 để giảm ignore ở 4.3.)*

### 4.2 Đảo chiều channels_core → kiotviet
- Files: `channels_core/transport/views/channel_views_impl.py:17`, `channel_serializers_impl.py:6`, `channel_repositories_impl.py:8`
- [x] Thay import module-level `from apps.kiotviet.models import KiotVietChannel` bằng `django.apps.apps.get_model("kiotviet", "KiotVietChannel")` trong hàm (lazy) — cắt phụ thuộc import-time; ghi chú TODO registry pattern cho sprint kiến trúc sau. *(commits 01a3a4d8 + fix schema — chi tiết: type hint chuyển sang `if TYPE_CHECKING` + `from __future__ import annotations`; `Meta.model` của KiotVietChannelSerializer dùng `get_model` tại class body (module chỉ nạp sau app-registry qua urls); 2 field `paired_old_channel_id` chuyển sang `PairedOldKiotVietChannelField` override `get_queryset` + `pk_field=IntegerField` để schema OpenAPI giữ nguyên `integer` — verify schema sinh ra IDENTICAL với base.)*

### 4.3 import-linter
- [x] Thêm `import-linter==2.12` vào `backend/requirements-dev.txt`; `backend/.importlinter` contract layers: `apps.common` ← `apps.users, apps.channels_core` ← `apps.customers, apps.orders, apps.inventory, apps.hr` ← `apps.kiotviet, apps.facebook_ads, apps.google_ads, apps.zalo_bridge`. **25 vi phạm hiện hữu** trong `ignore_imports` tường minh — contract chặn NỢ MỚI, nợ cũ trả dần. Contract PASS ("1 kept, 0 broken", 744 files / 1188 dependencies). **Ghi chú semantics:** import-linter 2.x dùng `:` = sibling KHÔNG bắt độc lập (orders↔customers cùng tầng được phép import nhau — không cần ignore); muốn siết độc lập cùng tầng thì đổi `:` thành `|` ở sprint sau (sẽ lộ thêm nợ orders↔customers).
- [x] CI: job `lint-imports` trong backend-ci (static analysis, không cần deps app/DB).

### 4.4 users/views.py CRUD → services
- [x] Dồn CRUD Department + Role vào `apps/users/services.py` (`DepartmentService.create/update/delete_department` + `_sync_channels`, `RoleService.create/update/delete_role`); check trùng tên chuyển vào service (raise `ValueError`, view map về 400 giữ nguyên message); serializer-validate + permission giữ ở view. Test service 11 case (happy + duplicate-name + channels sync + permissions).

### 4.5 Tách order_ingest_product_dashboard_view.py (1119 dòng)
- [x] Tách thành `order_ingest_views.py`, `product_catalog_views.py`, `dashboard_report_views.py`; file cũ thành re-export shim. Logic `ingest_landing_order` chuyển vào `application/services/order_ingest_service.py` (`OrderIngestService.ingest_landing_order` trả `LandingIngestResult(body, status)`; view chỉ check token + parse + map Response). Lệch nhỏ: test patch target `...order_ingest_product_dashboard_view.dispatch` đổi sang `...order_ingest_service.dispatch` (move refactor — dispatch giờ gọi từ service). Orders suite 134 passed + 13 subtests, không đổi logic.

### 4.6 orders/services/product_group.py → application/services
- [x] Move → `application/services/product_group_service.py`; cập nhật importer duy nhất (`product_item_crm_serializers.py`). Package `apps/orders/services/` KHÔNG xoá được vì `__init__.py` là facade `from apps.orders.services import OrderService` đang được dùng rộng — giữ lại facade, sửa docstring.

### 4.7 Settings dọn
- [x] Tách `settings_parts/celery.py` (toàn bộ CELERY_* + 19 entry beat schedule) — `base.py` `from .celery import *`; thêm `settings_parts/_redis.py` (REDIS_HOST/PORT/PASSWORD + build_redis_url dùng chung, tránh trùng lặp). Verify: `manage.py check` sạch + shell in `CELERY_BEAT_SCHEDULE` (19), `CELERY_TASK_ROUTES` (21), broker URL, `REDIS_HOST` — mọi tên cũ truy cập được từ `config.settings`.
- [x] `KIOTVIET_STATUS_SYNC_TARGETS` + `KOC_CHANNEL_ID`: khi IS_PRODUCTION mà env chưa set → log warning lúc boot (KHÔNG fail) — verify bằng container APP_ENV=production: cả 2 warning in ra stderr.
- [x] Ghi chú RB.6 vào comment `TASK_BACKEND`/`AUDIT_BACKEND` — chưa xoá code path.

### 4.8 DRF exception handler thống nhất (an toàn tương thích)
- [x] `backend/apps/common/exceptions.py` — `api_exception_handler` bọc drf handler: body dict → bổ sung `error: {code: snake_case(exception), detail: str đầu tiên}` (không ghi đè nếu đã có key `error`); body list giữ nguyên; 500 (DRF trả None) → log kèm request_id rồi trả None (giữ hành vi server-error cũ). Đăng ký `EXCEPTION_HANDLER`. Tests: unit snapshot 400 dict/list + 403 + 404 + 500-log (apps/common) + endpoint snapshot 400/403/404 (đặt ở apps/users/tests để không tạo nợ import common→users cho 4.3).

## WP5 — Frontend quick wins + repo hygiene

### 5.1 Dọn dead code orders grid
- [x] Xoá `frontend/src/features/orders/order-grid-columns.tsx` (1566 dòng, không ai import); gỡ `ag-grid-community` + `ag-grid-react` khỏi package.json; xoá manualChunks `vendor-grid`, `OrdersGridDesktop`, `order-grid-columns` trong `vite.config.ts:78-84`; sửa 2 comment nhắc tới file này (`order-status-badge.ts:3`, `datetime-local.ts:4`). `npm run build` xanh. *(commit 5aad0fc8)*

### 5.2 shadcn CLI → devDependencies
- [x] `package.json:52` move sang devDependencies. *(commit 80209570)*

### 5.3 lint:strict vào CI
- [x] Sửa các warning còn lại để `npm run lint:strict` pass; frontend-ci đổi step lint dùng `lint:strict`. *(commit b00413c5 — 128 warning → 0. Lệch kế hoạch: (a) 8 file legacy có `eslint-disable exhaustive-deps` chủ đích (effect keyed/mount-only) → override tắt `react-compiler/react-compiler` per-file trong eslint.config.js, gỡ dần khi WP6; (b) `i18n.tsx` inline-disable react-refresh vì I18nProvider import ở main.tsx (file khoá bởi 2.3/Sentry) và useI18n có ~76 importer; (c) file mới tách ra: `app/route-guards.tsx`, `features/orders/landing-tabs.ts`, `features/orders/order-detail-format.ts`, `features/customers/components/ChipList.tsx`.)*

### 5.4 Repo hygiene (root)
- [x] `git rm` các file rác được track: `main.py`, `_map_t4.py`, `backend/uv` (0 byte), root `pyproject.toml` + `uv.lock`, root `package.json` + `package-lock.json`. Xoá thư mục rỗng `backend;C`, `err.png` (untracked — xoá disk). KIỂM TRA trước từng file: grep tham chiếu trong CI/scripts/docs; nếu có tham chiếu thì giữ + ghi chú. *(Deletions bị cuốn vào commit 8f747fe8 (WP3) do commit đồng thời trên branch chung — nội dung đúng. `backend;C` đã không còn trên disk. Ghi chú: backend-ci `uv sync --frozen` trước đây resolve LÊN root pyproject/uv.lock; sau khi xoá sẽ dùng nhánh fallback `uv pip install -r requirements.txt` — đúng bộ deps hơn.)*
- [x] Viết lại `.gitignore` gọn bằng pattern (bỏ entry log theo ngày lặp), giữ nguyên các rule chặn `.env`, `*.dump`, `*.rdb`, xlsx PII. *(commit 597ad19a — so sánh `git status --ignored` trước/sau: tập file ignore GIỐNG HỆT; bỏ các entry vô hiệu `frontend/package-lock.json`, `.gitignore` x3.)*

### 5.5 ESLint chặn nợ mới
- [x] `no-restricted-imports` cho `react-select` (message: dùng components/ui select) — mức `warn` + allowlist 12 file hiện có qua overrides để lint:strict vẫn xanh. *(commit 627f84e2)*

### 5.6 Convention pages
- [x] Ghi vào `.claude/CLAUDE.md` (project): page mới đặt trong `features/<domain>/pages/`, GET mới bắt buộc React Query, API module mới bắt buộc typed từ `api/schema.d.ts` + cấm react-select cho code mới. *(File nằm ngoài git — `.claude/` bị ignore — sửa trực tiếp tại checkout chính.)*

## WP6 — Frontend structural

### 6.1 Migrate 15 page sang React Query (theo đợt, mỗi page 1 commit)
Đợt A (rủi ro cao/giá trị cao): OrderDetailPage, ProductsPage, WarehousesPage, RolesPage.
Đợt B: LeadAssignmentPage, ResaleTrackingPage, LoyalCustomersPage, CustomerReportPage, AdsReportPage.
Đợt C: FacebookAdsPage, GoogleAdsPage, BlocklistPage, CronJobsPage, ProductCategoriesPage, SyncPage.
- [ ] Mỗi page: chuyển GET sang `useQuery` (queries.ts per-feature theo mẫu `src/features/orders/queries.ts`), mutation sang `useMutation` + invalidate; giữ nguyên UI/UX. Typecheck + test + smoke build sau mỗi đợt.
- [ ] Xoá `src/lib/expiring-map-cache.ts` khi không còn ai dùng.

### 6.2 Typed client cho 20 module lib/api
- [ ] Từng module `src/lib/api/*.ts` đổi type tay sang `paths` từ `@/api/schema.d.ts` theo mẫu `orders.ts`. Ưu tiên: channels, customers, hr, products, roles, inventory. Mỗi module 1 commit, typecheck xanh.

### 6.3 OrdersTable + App.tsx
- [ ] Move `components/OrdersTable.tsx` → `features/orders/OrdersTable/`; tách phần gọi API/auth (`getValidAccessToken`, `getCrmCatalogs`) lên container hook `useOrdersTableData`; presentational nhận props. KHÔNG đổi hành vi — dựa test `order-realtime-merge` + smoke.
- [ ] `App.tsx`: move type `DashboardSummary` → `features/dashboard/types.ts`.

### 6.4 ResponsiveList cho 23 bảng thô (ưu tiên mobile-first)
Đợt A (HR/ESS — nhân viên dùng điện thoại): EmployeeContractsTab, AttendanceHistoryPage, LeaveRequestsPage + các bảng HR còn lại.
Đợt B: LoyalCustomersPage, ProductsPage, ProductCategoriesPage, ResaleTrackingPage, WarehousesPage, CronJobsPage.
Đợt C: UsersTable, KiotVietTable, ChannelsTable.
- [ ] Mỗi bảng chuyển sang `ResponsiveList` theo mẫu EmployeesPage. Kiểm tra viewport mobile không vỡ.

### 6.5 Tests logic orders thuần
- [ ] Unit tests cho `order-payload.ts`, `order-normalizers.ts`, `use-order-mutations.ts` (pure logic trước, UI sau).

## WP7 — Infra/tooling

### 7.1 CI Node 20 → 22
- [x] `frontend-ci.yml:18` → `node-version: "22"`.

### 7.2 Pin image tags prod
- [x] `minio/minio`, `prom/prometheus`, `grafana/grafana`, `node-exporter`, `postgres-exporter`, `redis-exporter` → pin tag cụ thể. Tags (pin 2026-07-03, verify tồn tại trên registry): minio `RELEASE.2025-09-07T16-13-09Z` (bản community CUỐI — MinIO archived repo 04/2026, :latest không bao giờ nhích nữa; pin cả compose base/local), prometheus `v3.13.0` (LTS line mới, 3.5 LTS EOL 07/2026), grafana `13.0.3` (supported tới 01/2027), node-exporter `v1.11.1`, postgres-exporter `v0.19.1`, redis_exporter `v1.83.0`. Giữ alertmanager v0.27.0 + cadvisor v0.49.1 (pin sẵn); `celery-exporter:latest` ngoài scope — backlog.

### 7.3 Dockerfile
- [x] `backend/Dockerfile`: multi-stage (builder pip install --prefix → runtime copy), giữ non-root; CMD chỉ còn gunicorn default không flags trùng compose (compose là nguồn chân lý). Xoá luôn `backend/entrypoint.sh` chết (không được tham chiếu ở đâu, còn chứa seed_initial_data đã gỡ ở 1.1); dev/prod compose đều override command nên không flow nào dựa vào CMD cũ.
- [x] `frontend/Dockerfile:7`: `npm ci || npm install` → `npm ci`. Verify: `npm ci --ignore-scripts` sạch trên node:22-alpine + full `docker build frontend/` OK.

### 7.4 psycopg2-binary gỡ
- [x] Xoá `psycopg2-binary==2.9.11` khỏi requirements; grep toàn backend 0 import psycopg2 trực tiếp. **Phát hiện:** `psycopg==3.3.3` bare KHÔNG import được trên python-slim (thiếu libpq) → Django lâu nay âm thầm fallback psycopg2. Fix: `psycopg[binary]==3.3.3` (khôi phục intent gốc `psycopg[binary]>=3.2` của commit đầu repo). Verify image build mới: `psycopg.pq.__impl__=='binary'`, `import psycopg2`→ImportError, Django driver = psycopg 3; migrate from-zero + pytest apps/audit 8/8 pass hoàn toàn trên psycopg 3.

### 7.5 Celery beat bền vững
- [x] Thêm `django-celery-beat==2.9.0` (bản đầu hỗ trợ Django 6.0, PyPI 02/2026; kèm pins cron-descriptor 1.4.5 / django-timezone-field 7.2.2 / python-crontab 3.3.0) vào requirements + INSTALLED_APPS; beat command (prod + local) → `--scheduler django_celery_beat.schedulers:DatabaseScheduler --pidfile=/tmp/celerybeat.pid` (bỏ `--schedule=/tmp/...`; healthcheck prod đổi sang test pidfile; beat `depends_on web: service_healthy` để bảng django_celery_beat được migrate trước khi beat start). **Chọn phương án A (DatabaseScheduler), không cần fallback volume** — bằng chứng: source 2.9.0 trong image, `setup_schedule()` gọi `update_from_dict(self.app.conf.beat_schedule)`; test end-to-end scratch Postgres+Redis: beat start thật → 19/19 entry CELERY_BEAT_SCHEDULE sync vào PeriodicTask, pidfile được tạo.
- [x] `makemigrations --check` trong backend-ci: bỏ `continue-on-error`. Drift có sẵn zalo_bridge đã dọn: sinh `0007_alter_zalobridgecontact_*` (2 AlterField khớp model master), sau đó check = "No changes detected".

### 7.6 CI build Docker image
- [x] Job `docker-image` (build-only, không push — cần credentials RB.7) trong backend-ci.yml + frontend-ci.yml: `docker/build-push-action@v6`, cache GHA scope riêng mỗi image, trigger theo paths hiện có (push master + PR).

## WP8 — Refactor lớn

### 8.1 KiotViet: business logic ra khỏi management command
- [x] Kéo `_handle_import` + các mixin (`_import_invoices_handlers_mixin`, `_sync_invoices_{check,purge,sync}_mixin`) từ `sync_invoices_between_channels.py` (1274 dòng) vào services trong `apps/kiotviet/infrastructure/services/` (theo mẫu `SyncInvoicesService`); command chỉ parse args + gọi service; beat task gọi service công khai thay vì `_handle_import` private (sửa chú thích `base.py:240-242`). Move-only refactor — không đổi logic; test hiện có phải xanh + thêm test smoke cho service entrypoint. *(Chi tiết + lệch kế hoạch: xem nhật ký 2026-07-03 WP8.1/8.2. **Backlog mới**: hợp nhất `SyncInvoicesCliService` (stack CLI move nguyên văn, web-API list) với `SyncInvoicesService` (stack reconcile được bảo trì, public-API) — 2 stack trùng lặp CÓ CHỦ ĐÍCH từ trước WP8, KHÔNG hợp nhất trong đợt này để giữ move thuần trên luồng tiền.)*

### 8.2 Tests cho app 0-test
- [x] `zalo_bridge`: test 3 task gửi tin (mock API) + idempotency. *(16 test: template/variables/tags, claim-revert idempotency, 429 throttle retry đúng backoff Hub, revert cờ khi lỗi, guard thiếu nick/invoice_code/shipping_code/inactive status, tracking URL provider.)*
- [x] `kiotviet`: test check/purge mixin logic chính (fixture invoice, xác nhận không purge nhầm). *(11 test trên SyncInvoicesService — stack reconcile prod: chỉ xóa mirror match SYNC desc; đơn tay/mirror source khác/ngoài window/đã hủy/delivery-cancelled KHÔNG xóa; yes_flag guard; _select_duplicates giữ đơn cũ nhất; guard cross-channel cancelled mirror không lật đơn kênh mới.)*
- [x] `audit`: test middleware (skip paths, GET không lưu response — cùng 3.1). *(8 test WP3 đã cover GET/retention; bổ sung 3 test SKIP_PATHS + OPTIONS + entity mapping — audit giờ 11 test.)*

### 8.3 Vite 5 → 7 (+ vitest 3, react-router-dom 7 nếu thuận)
- [ ] Nâng Vite 7 + `@vitejs/plugin-react` tương thích; sửa breaking config; `npm run build` + `test` + `typecheck` xanh. react-router 7 chỉ nâng nếu codemod sạch — nếu không, ghi backlog.

## RB — Runbook prod (cần người vận hành, không làm được từ repo)

- [ ] RB.1 **NGAY SAU MERGE**: đổi mật khẩu `admin` trên prod; kiểm tra `staff1/2/3` — đổi pass hoặc `is_active=False`.
- [ ] RB.2 Điền `LARK_ALERT_WEBHOOK_URL` vào env prod cho Alertmanager; gửi alert test.
- [ ] RB.3 Bật offsite backup: set `DESI_BACKUP_S3_DEST` (MinIO/B2 KHÁC VPS) + `DESI_BACKUP_REQUIRE_OFFSITE=1`; xác nhận cron chạy (kiểm tra metric BackupStale).
- [ ] RB.4 Restore-drill quý: restore dump mới nhất vào máy khác, ghi thời gian RTO.
- [ ] RB.5 Chạy `SELECT indexrelname, idx_scan FROM pg_stat_user_indexes WHERE relname='orders' ORDER BY idx_scan` — drop index 0-scan (ứng viên: `order_phone_idx`).
- [ ] RB.6 Xác nhận env prod `TASK_BACKEND`/`AUDIT_BACKEND` không còn dùng "go" → sprint sau gỡ code path.
- [ ] RB.7 Tạo registry credentials (GitLab Container Registry) để CI push image + deploy bằng tag.
- [ ] RB.8 Set `SENTRY_DSN` (self-host GlitchTip hoặc Sentry SaaS) cho web/worker/beat + `VITE_SENTRY_DSN`.
- [ ] RB.9 Cân nhắc cadence `sync_all_daily_spend` 60s → 300s (quyết định nghiệp vụ: độ tươi số liệu ads vs tải FB API).

## Nhật ký tiến độ

- 2026-07-03: Plan tạo. Branch `fix/system-audit-hardening-2026-07` từ master fb50db7d.
- 2026-07-03: WP1 (1.1→1.5) + WP2 (2.1→2.4) hoàn thành (commits 2312491a→41e9d0ad). Ghi chú lệch nhỏ: (a) 1.1 phát hiện seed cũ vốn đã hỏng do `defaults={"role": ...}` — field User.role không còn tồn tại, đã bỏ; (b) 1.3 guard chuyển sang `ImproperlyConfigured` (thay RuntimeError cũ) + prod compose thêm `INTERNAL_AUTH_TOKEN` required để deploy fail sớm ở compose thay vì trong app; (c) 2.1 thêm scrape job `celery-exporter` vào prometheus.full.yml (rule backlog cần data) — cơ chế Lark webhook = entrypoint sed thay URL noop, đã verify bằng amtool trong image v0.27.0; (d) 2.3 FE đụng thêm `frontend/package-lock.json` (bắt buộc để `npm ci` CI không vỡ); (e) 2.4 formatter json chỉ khai báo khi LOG_FORMAT=json để môi trường chưa cài python-json-logger không vỡ dictConfig. Test: seed 3/3, guard 11/11, throttle 7/7, request-id 6/6 pass trong image desi-order-web-test; 2 fail có sẵn ở apps/users (rbac system-role, duplicate rbac_roles_name_key) tồn tại trên HEAD sạch — không phải regression.
- 2026-07-03: WP3 (3.1→3.8) hoàn thành (commits 9a2384a7→f5292f09; 3.9 backlog giữ nguyên). Lệch kế hoạch: (a) 3.7 — WarrantyClaim 3 field KHÔNG thêm choices vì FE nhập tự do, thêm choices sẽ vỡ API contract (chi tiết ghi tại mục 3.7); (b) 3.5 — queryset prefetch đặt ở `UserService.admin_list_users()` thay vì inline trong view (đúng layering); (c) 3.8 — field FK đổi tên Python (`created_by`/`approver`, giữ db_column + attname) thay vì giữ tên `*_id` (Django không cho FK tên trùng attname). Kiểm chứng: audit 8/8, customers test_phone_unique 5/5, users test_users_list_queries 3/3, channels_core test_channel_code_unique 4/4, hr 41/41, orders full suite 134/134 (+13 subtests); verify index drop 3.3 bằng scratch DB migrate-from-zero (chỉ index auto-name biến mất, index Meta + partial unique còn nguyên). Suite gộp 7 app: 267 passed / 7 failed — 2 fail users là baseline đã biết; 5 fail `test_report_trends` là flake theo lịch (test backdate order 3–10 ngày, chạy ngày 03/07 rơi hết về tháng 6 → "current month not found"), không liên quan WP3. `makemigrations --check`: sạch cho các app WP3; còn drift CÓ SẴN ở zalo_bridge (0007 chưa được commit từ d7b626e7 trên master) — ngoài phạm vi WP3, cần xử lý ở WP4/WP8. 2 file test customers cần matplotlib bị skip vì image test thiếu lib (đã biết từ memory monthly-report).
- 2026-07-03: WP4 (4.1→4.8) hoàn thành (commits f1f1f5c8, 01a3a4d8, + users/orders/settings/exceptions/import-linter — xem tick từng mục). Lệch kế hoạch: (a) 4.1 — customers có 10 chỗ import EXCLUDED_BRANCH_IDS (plan ghi 5), đổi cả 10; (b) 4.3 — import-linter 2.x: `:` giữa sibling = KHÔNG bắt độc lập cùng tầng → orders↔customers 2 chiều KHÔNG phải vi phạm với contract hiện tại (không cần ignore); 25 ignore_imports đều là vi phạm xuyên tầng thật (nặng nhất: orders→kiotviet 10 entry, users→kiotviet/orders/customers/hr 6 entry); siết độc lập cùng tầng (`|`) để backlog; (c) 4.4 — check trùng tên Department/Role chuyển vào service (ValueError → view map 400, message giữ nguyên) thay vì để ở view như plan gợi ý — view mỏng hơn, contract không đổi; (d) 4.5 — test patch target dispatch đổi theo module mới (service); (e) 4.6 — package `apps/orders/services/` giữ lại vì `__init__.py` là facade import công khai; (f) phát hiện `schema.yaml` drift CÓ SẴN từ trước WP4 (staff_collection docstring + endpoint channel-quality chưa regen) → regen + commit riêng; verify schema sinh từ code TRƯỚC vs SAU WP4 IDENTICAL (sau khi thêm `pk_field=IntegerField` cho PairedOldKiotVietChannelField); (g) image test desi-order-web-test được rebuild thêm deps mới (django-celery-beat từ WP7). Kiểm chứng: manage.py check sạch; lint-imports "1 kept, 0 broken"; ruff sạch trên mọi file sửa; pytest users+orders+customers+channels_core+common gộp: **329 passed + 13 subtests / 7 failed** — cả 7 đều là baseline đã biết (2 users rbac_roles_name_key duplicate; 5 customers test_report_trends flake đầu tháng), không có regression.
- 2026-07-03: WP8.1 + WP8.2 hoàn thành (commits aed2a8c3, b6f8a3e5, 6087a921 + 3 commit test). **WP8.1** — 2 command KiotViet hết chứa nghiệp vụ: (a) `import_kiotviet_invoices` → `ImportInvoicesService` (`infrastructure/services/import_invoices_service.py`; mixin move nguyên văn thành `_import_service_{handlers,upsert}_mixin.py`, git rename 99%); public methods `import_invoices`/`update_status_phone`/`upsert_invoice`; tasks.py + webhook_impl gọi service công khai thay vì `Command()._handle_*`; (b) `sync_invoices_between_channels` → `SyncInvoicesCliService` (`sync_invoices_cli_service.py`; 3 mixin move nguyên văn thành `_sync_cli_{check,purge,sync}_mixin.py`, rename 99%; `_sync_invoices_shared.py` xóa — dùng chung `_SkipInvoice` từ `_sync_service_shared`). Output plumbing qua `_service_output.py` (StreamOutput/PlainStyle); CLI truyền thẳng stdout/stderr/style của BaseCommand nên output không đổi; giữ CommandError trong code move để hành vi CLI/task y nguyên. Bằng chứng move thuần: diff normalize old-command vs new-service = 0 dòng nghiệp vụ đổi (chỉ header/class/`handle`→`run`). **PHÁT HIỆN quan trọng**: `SyncInvoicesService` (reconcile) và command CLI vốn là 2 bản fork đã DRIFT từ trước (service có fix mới hơn: public-API pagination ccff3397/cf936a89, skip cancelled-mirror 228e6af9, delivery cap ccaf29c0; command có mode `--compare-invoice` + update-status theo codes mà service không có) → KHÔNG hợp nhất trong WP8.1 (giữ move thuần trên luồng tiền), ghi backlog hợp nhất ở mục 8.1. **WP8.2** — 39 test mới: kiotviet wiring 9 (command→service delegation, task→public method, upsert dry-run) + kiotviet purge/check 11 + zalo_bridge 16 + audit 3 (tổng audit 11). Kiểm chứng: manage.py check sạch; `--help` 2 command OK; lint-imports "1 kept, 0 broken" (746 files); ruff CI-scope (E9,F63,F7,F82) sạch trên file sửa; suite gộp kiotviet+zalo_bridge+audit: **81 passed + 4 subtests / 11 failed** — cả 11 fail là BASELINE CÓ SẴN (chạy đối chứng trên HEAD sạch qua git archive: y hệt 11 fail này): 6 test_access_token + 4 test_invoice_webhook + 1 test_import idempotent-rerun (total_amount) — hỏng từ trước trên branch/stack này (dấu vết: commit cũ "remove celery" đổi patch target sang `apps.dispatch.dispatch` x2 + `bump_cache_version` undefined (F821 có sẵn, nằm ngoài scope ruff CI hiện tại chỉ chạy khi test đến dòng đó); test idempotent kỳ vọng total từ line items nhưng behavior hiện tại lấy từ `invoice.total`) — KHÔNG phải regression của WP8, cần sprint riêng sửa test kiotviet baseline.
- 2026-07-03: WP7 (7.1→7.6) hoàn thành (commits 93e6829a, e56b582a, bb586fb3, 427b3c8b, d07020d8, bb4cdff0, b62bee41). Lệch/bổ sung so với plan: (a) 7.4 — psycopg bare không load được libpq trên python-slim, Django thực chất vẫn chạy psycopg2; đổi sang `psycopg[binary]==3.3.3` nghĩa là **driver prod đổi psycopg2 → psycopg 3 thật sự** sau deploy (đã verify migrate + tests trên psycopg 3, nhưng đáng lưu ý khi theo dõi release); (b) 7.3 — xoá `backend/entrypoint.sh` chết; (c) 7.5 — chọn DatabaseScheduler (không fallback volume) với bằng chứng sync 19/19 entry vào DB khi beat start trên scratch stack; beat depends_on web nâng lên service_healthy (cả prod lẫn local) để tránh beat start trước khi migrate; (d) 7.5 — sinh migration zalo_bridge 0007 còn thiếu (drift từ master d7b626e7) trước khi bật makemigrations --check blocking; (e) 7.2 — pin thêm minio ở compose base/local (image upstream đã ngừng phát hành). Verify: compose config -q sạch cả 3 file (kèm profile monitoring + managed-redis); docker build backend (multi-stage, USER=app, CMD gunicorn tối thiểu) + frontend (npm ci strict) đều OK; scratch-DB migrate from-zero OK (cần ENCRYPTION_KEY — hành vi có sẵn của kiotviet 0009); pytest apps/audit 8/8; ruff base.py sạch; YAML 2 workflow parse OK.

---

## ĐỢT 2 — Backlog follow-ups (branch `fix/audit-backlog-followups`, từ master c9660c9b)

| Task | Nội dung | Trạng thái |
|---|---|---|
| T1 | Quyết định nghiệp vụ FE: normalizeDecimalInput kiểu VN; order_source "kol" round-trip; customer_gender null đối xứng create/patch | ✅ 2f3a7e2a + 098e9f98 + 79a228f7 |
| T2 | modules.test.ts: APP_MODULES bổ sung roles/shipments/warranty | ✅ ae25156b — modules.ts đã có sẵn 4 section (roles/shipments/warranty/lead-assignment); phần lỗi thời là ALL_SECTIONS trong test ("returns" gộp vào stock-documents qua requiresAny, không có tab riêng) |
| T3 | Sửa 11 test kiotviet hỏng baseline (patch dispatch trùng, bump_cache_version F821, total expectation) | ⬜ |
| T4 | Hợp nhất 2 stack sync KiotViet (CLI service ⊕ reconcile service) | ✅ 2b69b9d9 + 15030e1c — nguyên tắc "chân lý = fix mới nhất từng mảnh": engine dùng chung = `SyncInvoicesService`; `SyncInvoicesCliService` chỉ còn adapter output + dịch SyncError→CommandError (1138→48 dòng); XÓA 3 mixin `_sync_cli_*` (1874 dòng fork-drift, net −2765 dòng). CLI hưởng fix reconcile: pagination orderBy=id (ccff3397/cf936a89), skip mirror đã hủy purge_cancelled (228e6af9), cap delivery 3→2 (ccaf29c0), tolerance ±1đ update_price (cf936a89), PUT ship 1 lần (980e8602); và bỏ web-API `POST /api/invoices/list` (public≡web đã verify từ trước, 949≡949/545≡545). Chiều ngược lại service nhận từ CLI: `--compare-invoice`, update-status theo `--invoice-codes`/`--target-invoice-codes`, mutual-exclusion update-only modes, và **check_price chỉ so `total`** (fix a782572d Jun-02 mà fork service Jun-08 bỏ sót — B gộp giảm giá vào header nên so discountAfterTax là lệch giả; nhất quán comment update_price của chính service). Hành vi beat reconcile KHÔNG đổi (6 mode không gồm check_price; params mới đều default None/False). Guard root-cause (ch5 login, customer POST, completed-invoice, cross-channel cancel) không đụng — file chứa guard ngoài diff, test guard vẫn xanh. Verify: pytest kiotviet 77 passed (66 baseline + 11 test mới `test_sync_unified_cli.py`), orders 141 passed; manage.py check sạch; ruff sạch; lint-imports "1 kept, 0 broken"; `--help` 2 command OK. |
| T5 | OpenAPI gaps: 4 endpoint thiếu, ~8 schema generic, 3 lệch spec; typed hr.ts + warrantyClaims.ts | ✅ 4d06efb1→8c4eca4c — 3 report endpoint vốn ĐÃ có path nhưng body "Unspecified" → serializer tường minh (`report_serializers.py`); `/api/audit-logs/export/` KHÔNG tồn tại ở BE dù FE có nút Export (tải về 404) → bổ sung action CSV + 4 test. 3 nghi lệch xử theo response thật: (a) `Product.price` = decimal-STRING (verify runtime: COERCE_DECIMAL_TO_STRING=True, render `"2500000.00"`) → spec đúng, sửa FE `products.ts` bỏ override number + parse khi format; (b) `branch_id` model CharField → spec sai, đổi string (+FE sync.ts gỡ override branch_ids); (c) `Department.allowed_sections` KHÔNG có thật (model drop ở users/0017, BE bỏ qua khi FE gửi) → KHÔNG annotate spec, ghi chú legacy ở users.ts. Bonus: trám 3 Error spectacular scan_relay (CI fail-on-error đang đỏ vì thiếu @extend_schema) → spectacular 0 Error; User/Role method-fields hết default-string; hr `approver_id` nullable. FE: warranty-claim-types + hr-types + attendance-types alias từ schema (giữ tên export); decimal hr (amount/base_salary/days) theo spec là string — đúng response thật, formatCurrency nhận cả string. |
| T6 | ChannelAdAccount M2M thay ArrayField (giữ nguyên JSON contract) | ⬜ |
| T7 | Squash 92 migrations orders | ✅ 7361843d — squash 0001→0089 thành `0001_squashed_0089_order_is_export_vat_vat_link` (93 file → 5 node graph: squashed + 0090..0093). Xem ghi chú T7 |
| T8 | CI: makemigrations --check trên push master (bắt xung đột leaf giữa 2 MR song song); pin celery-exporter | ⬜ |
| T9 | recharts 2→3 (gate QA thị giác trước merge); thăm dò vite 8/vitest 4 | 🟡 code-done 1e5a0737 — **CHỜ QA THỊ GIÁC trước merge** (xem ghi chú T9); vite 8/vitest 4 KHÔNG nâng đợt này (không zero-code-change) |

**Ghi chú T7 (2026-07-03) — squash migrations orders (commit 7361843d):**
- Hiện trạng: orders có **93** migration (0001..0093, không phải 92). **Điểm cắt = 0089**, KHÔNG thể squash tới 0093: `0090` phụ thuộc `customers.0019` + `channels_core.0019`, mà `customers.0016` phụ thuộc `orders.0059` (nằm trong dải squash) → from-zero graph bị **CircularDependencyError** (squashed → customers.0019 → … → customers.0016 → squashed). Đã kiểm tra mọi back-edge khác: inventory→orders.0080/0082, warranty→orders.0088 chỉ là chiều VÀO (an toàn, remap về squashed); kiotviet/warehouses/channels_core/users không phụ thuộc ngược orders/customers.
- Kết quả: graph orders from-zero 93 node → **5 node** (squashed + 0090..0093). `replaces` đủ 89 entry — prod (đã áp đủ chuỗi cũ) nhận squashed là applied, migrate NO-OP.
- 2 lỗi kỹ thuật của `squashmigrations` phải sửa tay:
  1. **RunPython refs không hợp lệ**: Django sinh `code=apps.orders.migrations.0019_xxx.func` — module bắt đầu bằng số → SyntaxError. Fix: package `migrations/_squashed_src/` (loader bỏ qua tên `_`) chứa bản copy NGUYÊN VĂN 12 migration có RunPython (n0019, n0025, n0026, n0027, n0038, n0043, n0045, n0049, n0052, n0066, n0079, n0085) — tự chứa, sống sót cả khi xoá file gốc sau này. Không migration nào import code app (chỉ `apps.get_model` + django/stdlib) → không có rủi ro import-drift. Với DB from-zero mọi backfill chạy trên bảng RỖNG → no-op an toàn (0066 fix created_at, 0070 phone_normalized backfill là RunSQL, 0079 product_groups: quét bảng rỗng; 0026/0043/0052/0085: seed catalog/shift — chạy thật nhưng idempotent get_or_create/update_or_create).
  2. **CreateModel + AlterUniqueTogether cùng 1 migration vỡ**: indexes/unique_together khai trong options của CreateModel đi vào `deferred_sql`, chỉ flush ở CUỐI migration → `AlterUniqueTogether` (0086, introspect DB) thấy 0 constraint → `ValueError: Found wrong number (0) of constraints for lead_assignment_shares`. Fix tay: collapse 0083+0086 — CreateModel `LeadAssignmentShare` thẳng ở dạng cuối (thêm `shift`, unique cuối, index `lead_assign_d_sh_sheet_idx`), elide RunPython `fanout_shifts` (chỉ nhân bản row có sẵn — bảng rỗng from-zero). Các RemoveIndex/RemoveConstraint khác đều nhắm object tạo bằng AddIndex/AddConstraint (execute ngay) → không dính deferred.
- Squashed migration đặt `atomic = False` vì giữ nguyên các RunSQL `CREATE INDEX CONCURRENTLY` (0058/0069/0071/0072/0074/0077/0088) — giống chính các migration gốc; prod không bao giờ chạy file này nên rủi ro partial-apply chỉ ở DB scratch.
- **Verify 2 chiều** trên Postgres 16 scratch (image `desi-order-web-test`): (a) FROM-ZERO: migrate DB trống OK; `pg_dump --schema-only` diff với schema từ chuỗi 93 migration gốc → **cấu trúc giống hệt 100%**, chỉ khác TÊN auto-gen của 30 index + 19 FK constraint (vd `orders_kshipnotepreset_created_by_id_9dee9360` → `kship_note_presets_created_by_id_d3ae4127`) — vì squash tạo model thẳng với `db_table` cuối trong khi chuỗi cũ tạo dưới tên bảng cũ rồi `AlterModelTable` (không rename index/constraint). Django không thao tác theo tên auto-gen (introspect theo cột) → vô hại. (b) PROD-SIM: DB áp đủ chuỗi cũ bằng code TRƯỚC squash → chuyển code có squashed → `migrate` = "No migrations to apply", `showmigrations orders` = `[X] 0001_squashed_... (89 squashed migrations)`.
- **Thời gian migrate-from-zero (full, mọi app): 15.9s trước = 15.9s sau** (đo 2 lần mỗi chiều, cùng máy/scratch PG). KHÔNG nhanh hơn vì optimizer chỉ giảm 247→233 op (23 RunPython/RunSQL là barrier chặn merge) — thời gian nằm ở DDL, không ở số lượng file migration. Giá trị thực của squash: graph đơn giản (5 node), ít row django_migrations, dễ đọc lịch sử; tiền đề T8 makemigrations --check rẻ hơn không đáng kể.
- Test: `makemigrations --check` sạch; pytest apps/orders + apps/customers trên test-DB MỚI (đi qua đường squashed): **248 passed + 13 subtests**; 5 FAIL trong `customers/tests/test_report_trends.py` là **baseline** (fail y hệt khi chạy với chuỗi migration gốc — đã đối chứng, không liên quan squash).
- ⚠️ **KHÔNG xoá 89 file gốc trong MR này** (Django cho phép song song). Bước dọn sau — khi MỌI môi trường (prod, staging, máy dev) đã chạy code chứa squashed ít nhất 1 lần deploy: xoá 0001..0089 cũ, xoá `replaces` trong file squashed, và chuyển dependency nội bộ nếu Django yêu cầu. Lúc đó `_squashed_src/` GIỮ NGUYÊN (squashed vẫn cần các hàm RunPython).

**Ghi chú T9 (2026-07-03) — recharts 2.15.4 → 3.9.1 (commit 1e5a0737):**
- Bề mặt dùng recharts chỉ 5 file nguồn / **2 trang**: Dashboard (`features/dashboard/components/DashboardCharts.tsx` + `DashboardPrimitives.tsx`, mount ở `DashboardPage` qua `DashboardSections`) và Báo cáo khách hàng (`pages/CustomerReportPage.tsx`, `components/report/StackedBarChart.tsx`, `components/report/TrendsPanel.tsx`). `BombReasonsChart` là SVG thuần — không đụng. Ads Report/Resale Tracking KHÔNG dùng recharts.
- Breaking đã xử (đa số type-level, không đổi render): (a) `TooltipProps` → `Partial<TooltipContentProps>` cho custom `ChartTooltip` (DashboardPrimitives); (b) Pie `label` callback giờ nhận `PieLabelRenderProps` — datum gốc lấy qua `props.payload` (CustomerReportPage donut); (c) param của `Tooltip formatter`/`LabelList formatter` có thể `undefined`/`RenderableText` → coerce `Number(v ?? 0)` (TrendsPanel, DashboardCharts ×2); (d) **fix hành vi duy nhất**: v3 yêu cầu `CartesianGrid` khớp `yAxisId` có thật — TrendsChart có 2 YAxis đặt tên (`revenue`/`buyers`), không có axis id 0 mặc định → thêm `yAxisId="revenue"` cho grid, nếu không lưới nền biến mất.
- Defaults v3 đổi nhưng không cần prop bù: migration guide không ghi đổi margin/animation mặc định; `accessibilityLayer` giờ mặc định BẬT (thêm điều hướng bàn phím + aria, không đổi pixel tĩnh — giữ vì là cải thiện a11y); multi-YAxis giờ render theo alphabet của yAxisId (2 trục đã tách trái/phải bằng `orientation` nên không ảnh hưởng); trục hiện axis line kể cả khi 0 tick (các chart dashboard đều đã `axisLine={false}` tường minh).
- ⚠️ **GATE: CẦN QA THỊ GIÁC trước merge** (jsdom không render SVG recharts, không QA headless được). So sánh cạnh master: (1) **Dashboard** — các chart cột (tỉ lệ chốt, năng lực sale, doanh thu, chất lượng giao hàng, chân dung KH), donut phân bổ, hover tooltip custom + label trên đỉnh cột; (2) **Báo cáo khách hàng** — tab Tổng quan/Trước mua/Sau mua (DistributionBar dọc-ngang, donut + label `%` quanh viền, 3 chart Phân tích chéo stacked) và tab **Xu hướng** (line 2 trục: kiểm tra lưới nền còn hiển thị — điểm fix yAxisId — và tooltip format tiền). Soi margins/màu/animation/legend/tooltip.
- **Vite 8 / vitest 4: KHÔNG nâng.** Trạng thái ecosystem (npm, 2026-07-03): vite latest **8.1.3** — bundler đổi hẳn sang **rolldown** (~1.1.3); `@vitejs/plugin-react` **6.0.3** (peer `vite ^8.0.0`, chuyển sang `@rolldown/plugin-babel`, peer `babel-plugin-react-compiler ^1.0.0` — repo có sẵn); vitest **4.1.9** (peer vite ^6||^7||^8); `vite-plugin-pwa` **1.3.0** đã khai báo peer `^8.0.0`; `rollup-plugin-visualizer` **7.0.1** đã hỗ trợ `rolldown 1.x`. Mọi plugin ĐÃ khai báo hỗ trợ, NHƯNG vi phạm gate "zero code change": `vite.config.ts` dùng `esbuild: { pure: [...] }` (vite 8 bỏ esbuild transform → oxc, option phải đổi) và `manualChunks` function (rolldown chỉ chạy qua compat, khuyến nghị `advancedChunks`); đổi bundler còn làm khác chunk graph → PWA precache manifest (92 entries) đổi theo. Đề xuất MR riêng sprint sau: chuyển `esbuild.pure` sang tương đương oxc, `manualChunks` → `advancedChunks`, nâng vitest 4 cùng lúc, diff bundle size + verify `sw.js` precache trước/sau.

**Quyết định nghiệp vụ T1 (CTO call — có thể veto khi review):**
1. `normalizeDecimalInput`: dấu chấm theo sau bởi ĐÚNG nhóm 3 chữ số (≥1 nhóm, không có phần lẻ khác) = phân cách nghìn kiểu VN → strip; "2.5" vẫn là thập phân; hỗ trợ "2.500.000,5" (chấm nghìn + phẩy thập phân). Đặc tả đầy đủ bằng test.
2. `order_source "kol"`: thêm vào ORDER_SOURCES để round-trip — đối chiếu choices backend trước, nếu backend thiếu thì bổ sung cả hai đầu.
3. `customer_gender: null` khi create: pass-through như patch (nghĩa thống nhất: null = xoá/không xác định), sau khi xác nhận serializer backend chấp nhận.

### Verify tổng Đợt 2 (2026-07-03)
- Backend full suite: **615 passed / 8 failed** — 7 baseline cũ (5 report_trends flake đầu tháng + 2 users RBAC) + 1 flake MỚI PHÁT HIỆN: `orders/tests/test_dispatch_stock_cycle.py::test_cancel_without_return_still_releases` — pass khi chạy riêng, fail trong full-suite (test-order pollution); ĐÃ ĐỐI CHỨNG fail y hệt trên master sạch (19 fail trên master) → có sẵn từ merge inventory-flow-hardening, KHÔNG phải regression. Backlog: sửa isolation cho test này.
- So với master: branch này GIẢM fail 19 → 8 (sửa 11 kiotviet baseline).
- Frontend: typecheck ✅ · lint:strict 0 warning ✅ · build ✅ · **137/137 test** (0 baseline fail còn lại).
- ⚠️ Gate trước merge: QA THỊ GIÁC 2 trang dùng recharts 3 — Dashboard + Báo cáo khách hàng (nhất là tab Xu hướng, chart 2 trục).
