# Audit toàn hệ thống — 2026-09-23

> Phạm vi: backend (hiệu năng/N+1, kiến trúc, design pattern), frontend, database prod,
> security, bộ test (test thừa), CI/hạ tầng. Baseline: master `db0899a8` (22/09).
> Phương pháp: 6 luồng audit độc lập, mỗi luồng đọc audit `2026-08-19` trước, xác minh từng
> mục cũ trong code hiện tại rồi mới tìm mới; số liệu đo thật (pytest + `CaptureQueriesContext`
> trong container, `pg_stat_*` trên prod chỉ đọc, `vite build` đối chiếu image prod, ruff/lint-imports
> qua image `desi-lint:local`). Chi tiết từng mảng ở `docs/audit-2026-09-23/*.md`.
> Từ audit 08-19 tới nay có **791 commit**, nên mọi kết luận cũ đã được đo lại thay vì kế thừa.

## Đánh giá tổng quan

Nền tảng runtime tiếp tục lên: hot path list/detail đơn – khách – contact **không còn N+1**
(số query hằng số khi N=20→40), phân quyền action-level (N2 cũ) **đã đóng cả 13 nhóm** và có test
kiến trúc, CI đã khôi phục 15 job, frontend typecheck/lint/test xanh, bundle first-load 277 KB gzip.

Nhưng ba lớp vấn đề mới nổi lên, đều là **vận hành/cổng chất lượng** chứ không phải bug logic:

1. **Cổng chất lượng đỏ trên master mà vẫn merge được** — ruff 5 lỗi (3 MR từ 05/09), test guard
   phân quyền đỏ với 3 view (từ 15–18/09), guard catalog field đỏ (5 field địa chỉ 2 cấp ai cũng sửa được,
   từ 17/09), hook local không chạy. Cùng lớp lỗi N1 của hai đợt trước, lần thứ ba.
2. **Vòng đời token**: prod đặt JWT access **1 năm**, trong khi token đi qua query string WebSocket
   và đang nằm trong log container → Loki → Grafana.
3. **Prod Postgres vừa dựng lại 22/09** không runbook, mất role read-only, backup chỉ 2 bản cùng host,
   offsite = 0, đổi image sang alpine làm đổi collation.

| Mảng | Trạng thái |
|---|---|
| CI/CD & cổng chất lượng | **Có pipeline nhưng đỏ trên master; hook local tắt** |
| Security (authZ action-level) | Tốt — N2 đóng, có guard (guard đang đỏ vì 3 view mới) |
| Security (token/log/hạ tầng) | **JWT 1 năm + token trong log; DB test mở 0.0.0.0** |
| Backend hiệu năng | Tốt ở hot path; còn dashboard không cache, sweep/import ghi từng dòng |
| Backend kiến trúc | Contract thật nhưng nợ đứng yên; god file tiếp tục phình (+20% LOC/5 tuần) |
| Database prod | Sạch, nhàn (0,44h DB time/20h); rủi ro ở vận hành rebuild + ETL ai_mart |
| Frontend | Tốt về nền; SW precache 4,3 MB cho mọi user, ~1.900 dòng code chết, typed client 17% |
| Test backend | 5.592/5.598 pass, **2 guard đỏ thật trên master**; 57 phút dưới tải; 11% thời gian ở 79 test `transaction=True`; ~140 định nghĩa gộp được |
| Test frontend | Xanh 1795/1795; ~10–15 it thừa; 3 test sát ngưỡng timeout |

---

## 🔴 NGHIÊM TRỌNG

### N1. Cổng chất lượng đỏ trên master — lần thứ ba

- **Ruff đỏ 5 lỗi** (đo lại bằng `desi-lint:local`, ruff 0.16.1): `apps/ai_mart/management/commands/ai_mart_chat_fdw.py:27 RET503`,
  `apps/foxia/tests/test_foxia_order_push.py:489`, `apps/users/tests/test_admin_api.py:324,355`,
  `apps/users/tests/test_rbac_sections.py:360` (SIM300). Vào master ở `63165276` (05/09), `14ff1c20` (16/09), `7c979fd2` (17/09).
- **Guard phân quyền đỏ** (`apps/users/tests/test_write_permission_coverage.py`, chạy lại: FAIL):
  `pancake/transport/views.py:451 retry_order` (POST tạo đơn thật, gác `pancake-leads:view`; catalog chỉ có `view`),
  `order_address_view.py:129,166` (POST-đọc, cần whitelist). Job `backend:test` không `allow_failure` → hoặc pipeline
  đỏ mà vẫn merge, hoặc runner không chạy.
- **Guard catalog field đỏ** (`apps/orders/tests/test_order_field_catalog.py:28`, chạy lại: FAIL): commit `2ce253b0` (17/09) thêm
  5 field `address_level2`, `ward_level2`, `province_level2`, `ward_code_level2`, `province_code_level2` vào
  `OrderInlineUpdateSerializer.Meta.fields` (`kiotviet_landing_serializers.py:412`) nhưng không khai vào `ORDER_FIELD_GROUPS`
  (`apps/users/rbac_catalog.py`, grep `level2` = 0). `order_action_view.py:267-272` tính `ungranted` từ `ALL_ORDER_FIELDS`
  → 5 field này **ai có PATCH đơn cũng sửa được**, bỏ qua field-grant. Đây là lỗ hổng thật, không chỉ test đỏ.
- **Hook local không chạy**: `git config core.hooksPath` chưa set; `frontend/.husky/_` không tồn tại → `pre-push` gọi
  `scripts/check-backend-arch.sh` chưa bao giờ chạy. `.gitlab-ci.yml:57` `uvx ruff check .` không ghim phiên bản.
- **Sửa (1 MR + 1 cấu hình GitLab):** fix 5 lỗi ruff (4 `--fix`); khai 5 field `*_level2` vào nhóm địa chỉ trong
  `ORDER_FIELD_GROUPS` (+ migration grandfather cho role đang có nhóm địa chỉ); thêm `pancake-leads:edit` + grandfather cho `retry_order`,
  whitelist 2 view địa chỉ; ghim `uvx ruff@0.16.1`; `core.hooksPath .githooks` + pre-push gọi `check-backend-arch.sh`;
  bật "Pipelines must succeed" trên GitLab; kiểm tra runner có gắn project. Chi tiết: `backend-architecture.md` P0-1, `security.md` M1/M2.

### N2. JWT access 1 năm + token WebSocket trong URL đang được ghi vào log

- `.env:34-35` prod `JWT_ACCESS_MINUTES=525600`, `JWT_REFRESH_DAYS=365` (mặc định code 60'/14d, `.env.example` 60/14,
  commit `6d22bea2` từng rút ngắn — env prod chưa cập nhật). Access token stateless, chỉ thu hồi khi đổi mật khẩu.
- `frontend/src/services/websocket.ts:74` `?token=${token}` → uvicorn access log: `docker logs desi-web --since 24h | grep -c "token="` = **112 dòng/24h**
  → Alloy thu mọi container → Loki 14 ngày → Grafana. Ai đọc được log (Grafana, docker, nginx) có phiên 12 tháng của bất kỳ user kể cả admin.
- **Sửa:** đặt `JWT_ACCESS_MINUTES=60`, `JWT_REFRESH_DAYS=14` + restart web (FE refresh im lặng sẵn, `services/http.ts:15-30`);
  bỏ token khỏi URL WS (frame đầu / `Sec-WebSocket-Protocol` / WS-ticket 30s); tạm thời scrub `token=` ở Alloy + log format;
  xoay mật khẩu admin từng xuất hiện trong log 14 ngày. Chi tiết: `security.md` H1/H2.

### N3. Prod Postgres dựng lại 22/09 không runbook, mất role read-only, backup không offsite

- `docker logs desi-postgres` mở đầu bằng `initdb` 06:21 UTC 22/09; volume mới; image đổi `postgres:16` → `16-alpine`
  (musl, "no usable system locales", `datcollversion` NULL → sort chuỗi tiếng Việt khác cluster cũ); "Data page checksums are disabled".
- Role `claude_ro` (GRANT `pg_read_all_stats` từ audit trước) **không còn**; `/opt/desi-backup` chỉ 2 dump 22/09 cùng host;
  metric `desi_backup_offsite_configured 0`. Không có ghi chú nào trong `docs/` về việc dựng lại.
- Mọi counter `pg_stat_*` chỉ tích luỹ ~20h → kết luận "index 0 scan" đợt này phải chờ ≥30 ngày.
- **Sửa:** viết `docs/runbooks/2026-09-22-postgres-rebuild.md`; bật offsite trong `scripts/desi-backup.sh`; tạo lại role đọc
  (`GRANT pg_read_all_stats, pg_read_all_data`); quyết định image debian/alpine; lần recreate kế thêm `--data-checksums`
  và 6 tham số ở `database.md` P2-4. Chi tiết: `database.md` P0-1, P1-1.

### N4. Postgres/Redis test publish `0.0.0.0` trên host prod, Redis không mật khẩu

- `ss -ltn`: `0.0.0.0:55432` (`desi-test-pg`), `0.0.0.0:56379` (`desi-test-redis`); chain `DOCKER-USER` rỗng, không ufw
  → mở ra Internet. Đây là container test **thường trực của team** (docs/superpowers/plans hướng dẫn `docker run … -p 5432:5432`),
  không nằm trong compose (compose mọi port đều `127.0.0.1`).
- **Sửa:** tạo lại với `-p 127.0.0.1:…` + `--requirepass`; sửa hướng dẫn trong docs/plans; thêm rule `DOCKER-USER`
  (Docker bỏ qua ufw). Audit này không dừng container vì suite pytest đang dùng.

---

## 🟠 CAO

### C1. Cùng lớp lỗi C6 (08-19) còn sót 5 lookup `icontains` → Parallel Seq Scan `orders`/`customers`

EXPLAIN trên prod: `UPPER(phone) LIKE UPPER('%x%')` → Parallel Seq Scan orders; `phone ILIKE` → Bitmap `order_phone_trgm_idx`.
Vị trí: `apps/warranty/claim_repository.py:32-33` (comment :29-30 "không có trgm" là sai — index có),
`apps/orders/application/services/contact_list_service.py:465-467`, `apps/customers/customer_resale_service.py:142`.
**Sửa:** đổi sang `__ilike_contains` (`apps/common/db_lookups.py:20`), 5 dòng, không cần index mới.

### C2. Engine sync KiotViet legacy 2835 dòng vẫn sống và vẫn được vá song song (N4 cũ nửa chừng)

`apps/kiotviet/management/commands/_sync_invoices_legacy.py` (cờ `--legacy`, docstring "xoá sau 1 release" từ 20/08);
commit `68380b98` (19/09) sửa cả engine mới lẫn legacy. `SyncInvoicesService.run()` 868 dòng.
**Sửa:** xoá legacy + cờ + test routing trong release này (−2,2% LOC backend); MR kế tách `run()` 4 bước.

### C3. Nợ kiến trúc đứng yên trong khi code phình +20%/5 tuần, không có gate "không tăng"

| Chỉ số | 08-18 | 09-23 |
|---|---|---|
| LOC non-test | 106k | 127,5k (+20%) |
| File >800 dòng / hàm >150 / class >1000 | 14 / 50 / 6 | 17 / 57 / 7 |
| `orders` LOC | 33,0k | 38,1k (+15%, dù đã tách `catalog`) |
| Nợ `.importlinter` khối B | 104 | 102 (Lô 6 chưa bắt đầu) |
| Import `apps.X.models` chéo app | — | 242 (chuẩn "cấm import ORM chéo" không có gì thực thi) |
| Ruff vi phạm dưới ignore | 405 | 626 (+55%); `B008`/`E401` = 0 vẫn ignore; `E501` chưa bao giờ bật |

Phình mạnh nhất: `order_service_commands.py` 1216→1475, `gateway_kship_customer_webhook.py` 963→1290, `pending_scan_service.py` 1325→1457,
`order_action_view.py` 871→1009. Header `.importlinter` sai 3 số.
**Sửa:** commit baseline (ruff `--statistics` + AST file/hàm) và fail khi tăng; Lô 7 = cycle `customers↔orders` (39 entry);
move `customers/phone_utils` → `common`; tách app `leads` khỏi orders. Chi tiết: `backend-architecture.md` P1-2…P1-5.

### C4. Workload đọc nặng còn lại: dashboard/report không cache, WS fanout còn ở phase 1, FE refetch "always"

- `dashboard_report_views.py:42-95`: 11 aggregate GROUP BY trên `orders` mỗi request, `grep cache` = 0, không single-flight
  (trong khi `sheet3_view.py:213` đã có khuôn).
- `order_event_publisher.py:244-246`: mỗi event `group_send` 2 lần (group kênh + group chung) vì env prod `WS_CHANNEL_FANOUT_PHASE=1`
  — hạ tầng C5 cũ đã xong nhưng chưa bật.
- Frontend `ads/queries.ts:94,297,412`, `overview-report/queries.ts:28`, `sale-report/queries.ts:61,118` `refetchOnWindowFocus: "always"`
  → mỗi alt-tab gọi lại chính các aggregate trên.
**Sửa:** cache + single-flight 60–120s cho `dashboard_summary`/`reports/*`; đặt `WS_CHANNEL_FANOUT_PHASE=2` (0 dòng code);
FE `"always"` → `true` + `staleTime 60s`. Chi tiết: `performance.md` 2.1/2.5, `frontend.md` P2-5.

### C5. ETL `ai_mart` là workload nặng nhất DB

- `khach_hang` incremental mỗi 30 phút aggregate toàn bộ `ai.don_hang` (560k) rồi mới lọc id: 3,7s + 63MB temp/lần, mean-time cao nhất;
  `temp_files` 7,7GB/20h (`ai_mart/infrastructure/sql/khach_hang.py:26-55,88`).
- Rebuild đêm ~15 phút DB time, statement 230s, seq-scan `orders` 309 lần/75M tuples, `buffers_backend` > `buffers_checkpoint`, 18GB WAL/20h;
  `max_wal_size` còn 1GB mặc định, `random_page_cost=4` trên SSD.
**Sửa:** đẩy `WHERE khach_id IN (...)` vào CTE; `SET LOCAL work_mem` trong session ETL; `UNLOGGED` cho bảng `ai.*` derived;
tham số PG ở `database.md` P2-4.

### C6. Security mức Medium cần đóng cùng đợt

- **Che SĐT bị vòng qua bằng in hoá đơn HTML**: `apps/orders/invoice.py:162-165,269` lấy SĐT nguyên vẹn; gate in chỉ record-scope
  → sale scope `all` không có `customer-phone:view` vẫn đọc số thật (`GET /api/orders/<id>/invoice`, `bulk_download_invoices` cap 200).
- **Vhost ERP không security header**: `curl -I https://erp.desiglobal.vn/` chỉ có `server: nginx/1.24.0` — không HSTS/XFO/nosniff/CSP;
  mẫu đúng có ở vhost `tracuu.delax.com.vn:60-63`.
- **Brute-force chỉ theo IP**, không lockout tài khoản/MFA, mật khẩu min 8 (`auth_views.py:37-52`, `base.py:916-921`).
- **Guard N2 có 3 điểm mù** (view ghi chỉ `IsAuthenticated` 40 chỗ gate trong thân; `require_permission("x:view")` trên method ghi 6 chỗ;
  class-based view không quét) — hôm nay đúng, ngày mai lọt.
- Dependencies: DRF 3.17.0 → 3.17.2 (2 CVE parsing), Django 6.0.8, cryptography 46→50, react-router 7.18.1 (2 high, RSC mode không dùng).
- `/admin` vẫn lộ Internet (`config/urls.py:7`, live 302).

### C7. Frontend: service worker precache 4,36 MB cho mọi user desktop; ~1.900 dòng code chết; typed client 17%

- `vite.config.ts:31-64` VitePWA scope `/m` nhưng `registerSW.js` inject mọi route → user mở `/orders` vẫn cài SW và tải nền 146 entry/4,36 MB,
  lặp mỗi deploy. **Sửa:** `injectRegister: null`, register chỉ trong `features/ess/MobileApp.tsx`, thu hẹp `globPatterns`.
- Code chết đã xác minh 0 import: `features/orders/OrderDetailDialog.tsx` 485, `features/hr/components/DepartmentTeamsManager.tsx` 222
  (bản sao của users/, kèm test 93 dòng đang test code chết), `app/AppLauncher.tsx` 135, `i18n/add_en_translations.{mjs,py}` 713,
  `check_keys.cjs` (luôn in "Missing 0 keys"), `Can.tsx`, `useFieldPolicy.ts`, `use-polling.ts`, `managed-dialog-content.tsx`,
  ~33 export lib/api/queries, `public/logo.pdf` 530 KB. **Sửa:** 1 MR xoá + `knip` trong CI.
- `lib/api`: 46 call typed vs 229 `fetchJson` (+101 ngoài lib/api); 4 bản `Promise<any>`. **Sửa:** lint cấm `@/services/http`
  trong `src/lib/api/**` với allowlist tạm, migrate `orders.ts` trước.

### C8. 5 model `unique=True` tuyệt đối + soft-delete → IntegrityError khi tạo lại cùng mã

`catalog/models.py:12,44,67` (Product.sku, ProductGroup.code, ProductCategory.code), `hr/models.py:53` (Employee.employee_code),
`warehouses/models.py:5-8` (Warehouse.code/name). Mẫu đúng ngay cạnh: `PriceBook` cùng file `:187-191`, `customers/models.py:66-69`.
**Sửa:** partial `UniqueConstraint(condition=Q(deleted_at__isnull=True))`.

### C9. Test backend: 5.598 test, 2 guard đỏ trên master, thời gian dồn vào sai chỗ, flaky tiềm ẩn từ cache dùng chung

Chạy thật toàn suite (image `desi-order-web:latest`, DB/Redis test riêng, `--create-db`): **5.592 pass / 6 fail / 0 skip**, 56 phút 37 giây
dưới tải nặng (baseline 08-19: 19 phút). 4 fail do mount thiếu thư mục cha (`test_compose_env_whitelist.py` ×3, `daily_report/tests/test_openapi.py`
đọc file ngoài `backend/`), **2 fail thật** = 2 guard ở N1. 0 test không collect được (đối chiếu AST ↔ `--collect-only`).

- **Thời gian**: 79 test `transaction=True` ở `analytics/tests/test_marts.py` (35), `test_refresh_service.py` (16), `test_reconcile.py`,
  `test_coverage_api.py`, `test_profile_api.py`, foxia/ai_mart = **381 s (11 % suite)**, teardown 5,5–6,1 s/test vì flush toàn bộ bảng
  → gộp 1 refresh nhiều assert, còn ~15 test (−250 s). 64 class `TestCase` (210 test) không chạm DB → `SimpleTestCase`.
  File 2,2–3,0 s/test do `setUp` lặp `sync_permissions()` + tạo user mỗi test (`test_order_record_scope.py:69-83`,
  `test_order_bulk_action_permissions.py`, `test_sale_reports.py`, `users/tests/test_team_api.py`) → `setUpTestData`.
  `test_migrations_scratch_db.py` 3 test = 60 s, `test_command_guards.py` 2×6,4 s → mark `slow`. `test_return_concurrency.py:84,96` `sleep(1.0)`.
- **Test thừa** (hash AST body sau chuẩn hoá): 134 nhóm giống nhau (298 test), 108 nhóm cùng file (230 test → gộp `parametrize` cắt ~140 định nghĩa,
  không giảm ca chạy). Thừa thật xoá được ~15 ca: hệ số chi phí Facebook theo mốc test lại qua HTTP ở **5 báo cáo** (11 test 0,85–1,2 s) mà
  **không có unit test** cho `facebook_ads_cost_multiplier()` (`orders/reporting/shared.py:435`); bộ auth Hermes token chép 3 lần
  (ai_mart/callcenter/daily_report); `estimate/check_permission/file_name/registered` lặp cho từng export definition ở 7 app
  (`inventory/tests/test_inventory_export_definitions.py:444` ≡ `:682` giống hệt) → 1 test generic duyệt registry ở `apps/exports`;
  6 class `*SectionTests` cùng 2 test (`users/tests/test_rbac_sections.py:42-160`); facebook_ads ↔ google_ads 18 test cùng tên/body
  (`test_ad_account_scope.py`) → base class nhận adapter. Danh sách đủ: `backend-tests.md` §1.
- **Assert yếu**: `exports/tests/test_metrics.py:15,21,25` không assert (label sai tên không raise); 7 test "allows" chỉ dựa không-raise;
  76 `assert_called()` không kiểm tham số (kiotviet webhook/access_token, inventory return sync, dispatch guard).
- **Flaky tiềm ẩn**: `cache.clear()` thật trên Redis dùng chung ở **40 chỗ/15 file** (conftest đã cảnh báo 3 lần); `event_bus.clear()` 8 file
  vá bằng 3 autouse chép nhau; `isolated_cache` LocMem định nghĩa 2 lần (`common/tests/test_rate_limiter.py:17`, `test_throttling.py:147`)
  → đưa lên `backend/conftest.py` + AST guard. 1.861 warning naive datetime.
- **Coverage lỗ hổng** (không có `pytest-cov`, ánh xạ tĩnh): 0 test cho `kiotviet/.../revenue_report_service.py` (620 LOC),
  `orders/management/commands/import_assessment_csv.py` (474, có `--overwrite`), `orders/reporting/delivery_quality.py` (301),
  `sale_reminder_service.py` + templates (506), 4 route `foxia/urls.py:17-20`; kiotviet 19,1k LOC / 26 file test / 18 s chạy = phủ mỏng nhất.
- **Fixture**: `order_factory` 4 bản; `apps/inventory/tests/` (47 file/814 test) không có conftest → `warehouse` định nghĩa lại 12 lần;
  `apps/users/test_helpers.py` (580 lượt dùng) nằm trong package prod, được COPY vào image.
**Sửa:** thứ tự — 2 guard đỏ (N1) → `isolated_cache` + event_bus autouse chung → gộp 79 transaction test + `setUpTestData` → `pytest-cov` vào CI
+ test cho 5 module trống → parametrize cơ học. Chi tiết: `backend-tests.md`.

---

## 🟡 TRUNG BÌNH

### T1. Backend hiệu năng (đường phụ, đã đo)
- Admin users list: RBAC `resolve_effective_permissions` ×2/user, cache lạnh = **5 SQL/user** (đo 54→104 query khi N=10→20);
  test `test_users_list_queries.py` patch chính hàm này nên không bắt được (`users/serializers.py:196-215`).
- Contact-flow sweep: 3 query + 1 transaction/khách, trần 2000/tick 5' (đo 33→63; `operation_engine.py:37-50`) → `bulk_create`.
- KiotViet import: `Product.get_or_create` + lookup Customer từng dòng/hoá đơn, ~10–15 query/hoá đơn × page 100 (`_import_service_upsert_mixin.py:47-97,194-208`) → nạp trước theo page.
- `LeadAssignmentService.transfer`: INSERT journey event/đơn trong lock (`lead_assignment_service.py:234-240`) → `bulk_create`.
- `auto_sync_invoice_full.py:76-79,335`: HTTP tuần tự + `OrderItem.filter` mỗi dòng, quét 7 ngày; không rõ đã thay bằng Go chưa (`docs/go-offload-plan.md:68`).
- Ghi từng dòng qua PgBouncer: `pending_shipment_scans` 106k UPDATE/20h, `facebook_ad_account_daily_spends` 111k (`facebook_ads/services.py:397-420` `update_or_create` trong loop) → bulk upsert như `ad_spend_sync.py:180-199`.
- Audit middleware ghi 1 dòng `audit_logs` cho mọi GET/list poll (bảng 2,23M dòng, 2,8GB, 5 index 382MB 0 scan) → bỏ qua GET 2xx hoặc sample.
- 16/37 file task không `autoretry_for`/`max_retries`; `ChannelTypeSerializer` `.count()`/loại (13 query, có view cache 30s).
- Mở từ đợt trước: resale history không LIMIT, export tồn kho 20k dòng RAM, journey-events không phân trang, contact list slice Python.

### T2. Database
- `order_assessments` chưa có override autovacuum (T1 cũ treo).
- Index ứng viên drop sau ≥30 ngày counter: `order_customer_created_idx` 17MB (bị 3 index cùng prefix che), `order_order_code_idx` 20MB,
  `order_shiptype_created_idx` 17MB, `order_item_order_cr_idx` 22MB, `snap_lastbuy_spent_idx` 23MB; `audit_logs` giảm ~230MB bằng partial 90 ngày.
- 37 cặp index trùng tuyệt đối (~26MB), chủ yếu FK-auto ⟷ `Meta.indexes` khai lại cùng cột (`orders/models.py:997`, warranty_codes `_like`, facebook_ads).
- `orders` 49 index, idx/heap 1,70; 4 GIN trgm mã cùng 1 ô search có thể gộp sau EXPLAIN.
- `customers.code` trùng 7.069 mã/14.143 dòng active (unique bỏ ở migration 0011); lookup theo code ở `gateway_kship_customer_webhook.py:384-398` rủi ro.
  `orders (channel_id, external_order_id)` 2.655 cặp trùng active — cần xác nhận nghiệp vụ. `order_code` trùng là chủ ý (tách hoá đơn KV).
- Decimal tiền 4 chuẩn (12,2/14,2/18,2/20,2); SĐT chưa normalize ở `warranty/models.py:182,341`; `customers.phone_*` TextField có btree;
  thiếu CHECK ≥0 tiền (`orders/models.py:249-260,982-989`).
- Migration: `inventory/0022:199` RunPython không reverse; `orders/0119:46` AddIndex atomic trên `orders`; orders 146 migration,
  90 file pre-squash + `_squashed_src` chưa dọn, 3 cặp trùng số. Prod: 405 [X], 0 chưa apply, `makemigrations --check` sạch.
- Connections/PgBouncer tốt (14/100, maxwait 0, 0 deadlock); thêm `lock_timeout=5000` cho web.

### T3. Backend kiến trúc / pattern
- `transaction.atomic` + nghiệp vụ trong view 5 điểm (`lead_assignment_view.py:184,254,485`, `price_book_views.py:104`, `foxia/link_config_views.py:171`, `order_action_view.py:598,624,721`).
- application → transport 3 điểm; `domain/` import Django 4 điểm (`best_express/domain/status_map.py:3`, `hr/domain/workday.py:17`, `daily_report/domain/periods.py:13`, `warranty_codes/domain/serial.py:3`).
- Repository chỉ là tên: `BaseRepository` chỉ hr/warehouses dùng; `OrderRepository` 1190 dòng 26 classmethod; 3 stub.
- Model-signal làm nghiệp vụ chéo app thay event bus: `customers/signals.py:34-37`, `cnv_loyalty/subscribers.py:115-124`.
- Trùng lặp: `_parse_date` ×6 (3 ngữ nghĩa), pagination tự viết ×3 ngoài `common.pagination`, `CANCELLED_INVOICE_STATUS=2` ×4
  (+ `lead_close_service.py:37` = 6 — cần xác nhận), 2 cặp management command gần giống.
- 8/85 management command không ai gọi (`run_pubsub_bridge` chắc chắn chết; 5 backfill một lần đã qua; 2 công cụ không runbook).
- `settings_parts/base.py` 1198 dòng, 8 import E402; id prod hard-code làm default (`KIOTVIET_STATUS_SYNC_TARGETS` :549-551, `KOC_CHANNEL_ID` :1146, `SALE_FB_CHANNEL_IDS` :1166) chỉ warning.
- Không có `pytest-cov`.

### T4. Frontend
- God component chưa đổi: 39 file >500 dòng (`ProductsPage` 1294, `ResaleTrackingPage` 1120, `OrdersPage` 1051 với Props 220 dòng, `RolesPage` 983, `OrderDetailPage` 982 tăng); 21 page ở `src/pages` song song 35 ở `features/*/pages`.
- Trùng helper: formatCurrency ×9 + 95 `toLocaleString("vi")` inline, formatDate ×27, normalizePhone ×3, PAGE_SIZE_OPTIONS ×8/6 bộ giá trị, pagination component ×4.
- `manualChunks` chỉ tách react/lucide → recharts trộn vào chunk `dashboard-constants` 371 KB, sửa 1 hằng làm mất cache immutable.
- Entry 600 KB chứa data-layer orders (AppShellContext 122 field) + i18n 2 ngôn ngữ tĩnh; 6 hook fetch thủ công ~2.700 dòng song song 57 file useQuery.
- Gate quyền bằng `is_superuser` thay permission code 12 chỗ (`app/routed-pages.tsx:204,398,475,…`, `use-auth-bootstrap.ts:77` hard-code DEPARTMENT_ID).
- 34 `catch {}` rỗng, 336 `toast.error` rải; không virtualization (page size 200); `ShipmentsPage.tsx:5-11` inventory import ruột orders.
- Nhỏ: favicon 110 KB khai 32x32, delax.png 189 KB hiển thị h-10, `index.html` có BOM, `@zxing/library@0.22` đòi Node ≥24 vs engines <24.

### T5. Security nhỏ
Import Excel không trần size riêng (`inventory/.../import_views.py:58`, `contact_flow/.../config_views.py:183`); `callcenter/transcribe.py:176` fetch URL ghi âm không allow-list;
~29 GET list không phân trang (bảng nhỏ); `age_text_backup.csv` (id khách + tuổi) và `product_profiles/` 24MB chưa commit ở root, không gitignore;
`scripts/customer_monthly_report.py` trỏ container `desi-postgres` (đúng) nhưng chưa commit.

---

## Những gì đã XANH — xác nhận để không ai "sửa" nhầm

- **N2 (08-19) đóng cả 13 nhóm** với `require_method_permission`, có guard AST + migration grandfather `users/0039`.
- **N3 `.importlinter`**: `|` + `containers` + `exhaustive`, 27/27 app khai tầng, KEPT thật.
- **C3 serializer**: 0 `transaction.atomic` trong serializers; catalog write đi qua service.
- **C6 search khách**: `__ilike_contains` khớp GIN bare-column, 2,3s → 36ms.
- **Perf 08-19**: transfer_landing batch, contact list cache, resale prefetch, dispatch batch song song, RBAC 1 query, sheet3 single-flight, audit log buffer batch — đều đã fix và đo được.
- **Hot path list/detail**: 11 endpoint đo N=20→40, số query hằng số (orders 11→9, customers 1, contacts 7→3).
- **Security**: JWT key ≥32 byte ép prod, refresh cookie httponly/secure, rotation + blacklist, token_version cả WS, webhook HMAC compare_digest, không raw SQL/eval, 0 `dangerouslySetInnerHTML`, upload sniff magic + cap, secrets có guard fail-fast, compose mọi port 127.0.0.1, container non-root, CORS đã hẹp, Sentry PII off, audit log read-only.
- **Frontend**: 52 route lazy + 64 lazy component (mọi lib nặng ngoài entry), sourcemap 3 lớp, nginx immutable/no-store đúng, query key factory tập trung (0 inline), 5 ref-write-in-render đã sửa, CI gate lint:strict/format/typecheck/vitest/drift-check.
- **DB**: FK thiếu index chỉ partition nhỏ, 0 index INVALID, bloat <1% bảng lớn, PgBouncer/connection tốt, 405 migration apply đủ.
- **Kiến trúc**: django-ninja gỡ, star-import kiotviet gỡ, `flow_history_views` 939 → 15 file, `_repositories_reports` → `orders/reporting/`, mypy có job CI, 6 app mới đủ 4 tầng.

---

## Kế hoạch thực hiện

### Sprint 0 — trong tuần này (không cần code lớn)
1. **N2**: `.env` prod `JWT_ACCESS_MINUTES=60`, `JWT_REFRESH_DAYS=14`, restart web; scrub `token=` ở Alloy; xoay mật khẩu admin.
2. **N4**: tạo lại `desi-test-pg`/`desi-test-redis` bind 127.0.0.1 + requirepass; rule `DOCKER-USER`.
3. **N3**: runbook rebuild 22/09, bật offsite backup, tạo lại role đọc.
4. **N1**: MR sửa 5 ruff + `retry_order` gate + whitelist 2 view + ghim ruff + hooksPath; bật "Pipelines must succeed".
5. **C4**: `WS_CHANNEL_FANOUT_PHASE=2`.

### Sprint 1 — cắt nhanh, ROI cao
6. **C1**: 5 dòng `__ilike_contains`. 7. **C6**: che SĐT hoá đơn + 4 header vhost ERP + bump DRF 3.17.2/react-router.
8. **C7**: SW chỉ `/m`; MR xoá code chết + knip. 9. **C4**: cache + single-flight dashboard/report; FE bỏ `"always"`.
10. **C5**: lọc id trong CTE `khach_hang`, work_mem cục bộ, UNLOGGED `ai.*`. 11. **C8**: partial unique 5 model.
12. **T1**: bulk contact-flow sweep + journey event + facebook daily spends. 13. **C9**: `isolated_cache` chung + gộp 79 transaction test + `pytest-cov` CI.

### Sprint 2+ — nợ cấu trúc theo ratchet
14. **C3**: baseline ratchet ruff + AST; xoá B008/E401; Lô 7 `customers↔orders`; move `phone_utils`; tách app `leads`.
15. **C2**: xoá legacy sync; tách `run()`. 16. Guard N2 mở rộng 3 điểm mù (C6). 17. FE: typed client ép lint, manualChunks vendor, format sweep, tách ProductsPage/OrdersPage.
18. DB: recreate PG 1 lần với 6 tham số + quyết định image; autovacuum `order_assessments`; index đợt 3 sau 30 ngày; dọn migration gốc orders.

## Ghi chú phương pháp & hạn chế
- Counter `pg_stat_*` chỉ 20h sau restore → mọi kết luận về index 0-scan đã đối chiếu call site nhưng vẫn phải chờ ≥30 ngày.
- Dashboard test không có kênh sale nên 8/11 summary short-circuit — chi phí thật theo kích thước bảng, không theo N.
- Frontend chạy trong container node:22 trên bản copy; 3 test đỏ do tranh CPU (pass khi cô lập).
- Không đọc được nginx host `access.log` (quyền) → chưa xác nhận token WS có trong log nginx (format mặc định gần chắc có).
- Không sửa code, không commit, không ghi vào DB, không dừng container trong đợt này.

## Trạng thái thực thi (2026-09-23)

Nhánh: `fix/audit-2026-09-23` gộp `audit/db` (DB), `audit/gates` (gates/security), `audit/perf`,
`audit/fe` (frontend), `audit/tests` (test backend); đợt cuối `audit/arch` (backend C2/C3/T3).

| Mục | Trạng thái | Nhánh / ghi chú |
|---|---|---|
| N1 cổng chất lượng đỏ | Xong | `audit/gates` — 5 ruff, guard phân quyền, field `*_level2`, ghim ruff, hooksPath |
| N2 JWT 1 năm + token WS trong log | Xong (code) / Một phần (ops) | `audit/gates` + FE; `.env` prod + scrub Alloy là việc ops |
| N3 Postgres rebuild không runbook | Xong | `audit/db` — runbook, offsite, role đọc |
| N4 DB/Redis test mở 0.0.0.0 | Xong | `audit/gates` (docs) + ops tạo lại container |
| C1 `icontains` seq scan | Xong | `audit/perf` |
| C2 engine sync legacy + `run()` 868 dòng | **Xong** | `audit/arch` — xoá 2 835 dòng + `--legacy`; `run()` → 14 bước có tên, không hàm >200 dòng |
| C3 nợ kiến trúc không gate | **Xong (gate) / Một phần (nợ)** | `audit/arch` — ratchet `scripts/arch_baseline.py` + `arch-baseline.json` (file 16 / hàm 45 / class 5 / ruff 464) chạy ở pre-push + CI; phone_utils → common (khối B 106 → **100**); Lô 7 cycle customers↔orders **hoãn** (96 entry Lô6 còn lại, cần port riêng); tách app `leads` **hoãn có chủ đích** |
| C4 dashboard cache / WS fanout / FE refetch | Xong | `audit/perf` + `audit/fe`; `WS_CHANNEL_FANOUT_PHASE=2` là env ops |
| C5 ETL ai_mart | Xong | `audit/perf` |
| C6 security medium | Xong (trừ MFA) | `audit/gates` — che SĐT hoá đơn, header vhost (repo; vhost host là ops), lockout theo tài khoản 10 lần/15', throttle (username, IP), mật khẩu min 12, `/admin` allowlist, bump deps; **MFA hoãn** |
| C7 frontend SW / code chết / typed client | Xong | `audit/fe` |
| C8 unique + soft-delete | Xong | `audit/db` |
| C9 test backend | Xong | `audit/tests` — 2 guard, isolated_cache, gộp transaction test, parametrize; `pytest-cov` vào CI (`audit/arch` thêm `--cov-fail-under`) |
| T1 hiệu năng đường phụ | Xong | `audit/perf` |
| T2 database | Một phần | `audit/db` — autovacuum, index đợt 3 chờ ≥30 ngày; **Decimal tiền 4 chuẩn hoãn** (đổi kiểu cột 4 bảng lớn, cần cửa bảo trì) |
| T3 atomic trong view (5 điểm) | **Xong** | `audit/arch` — whitelist guard `test_no_atomic_in_transport.py` RỖNG |
| T3 application→transport (3), domain→Django (4) | **Xong** | `audit/arch` — port/binding orders, contract `domain-pure` trong `.importlinter` |
| T3 repository | **Xong** | `audit/arch` — `orders/repositories/` 8 module (<330 dòng/file), users stub xoá, inventory stub gộp, customers giữ (5 caller) |
| T3 signal → event bus | **Xong** | `audit/arch` — `OrderSaved/CustomerSaved/ProductSaved/ProductCategorySaved`; `post_delete` Order giữ signal (QuerySet.delete không gọi `Model.delete`) |
| T3 trùng lặp | **Xong** | `audit/arch` — `common/dates.py`, `common/pagination_core.py`, `common/kiotviet_constants.py`, 2 cặp command gộp; `lead_close_service (6,)` là cố ý (trục `Order.status`), đổi tên + test |
| T3 command chết (6) + 2 công cụ không runbook | **Xong** | `audit/arch` — 80 → 76 command; runbook `tools-management-commands.md` |
| T3 settings id prod hard-code | **Xong** | `audit/arch` — fail-fast prod; `docker-compose.prod.yml` dùng `${VAR:?}` + passthrough `KOC_CHANNEL_ID`; **3 env phải khai trước deploy** (runbook ops B1); **tách `base.py` hoãn có chủ đích** |
| P1-4 ruff sweep | **Xong** | `audit/arch` — 13 rule rời ignore (B008, E401, UP017, UP037, UP006, UP045, F541, C420, UP035, C416, UP012, UP034, UP031); nợ 626 → 464; header `.importlinter` đúng số + test đếm |
| T4 frontend | Một phần | `audit/fe` — helper trùng, manualChunks, gate `is_superuser`; **virtualization, `ShipmentsPage` import ruột orders hoãn** |
| T5 security nhỏ | Xong | `audit/gates` |
| Nợ ngoài audit lộ ra khi làm | Xong | prettier 84 file đỏ sẵn trên master (job `frontend:lint` `format:check`); `scripts/check-encoding.js` false positive ~60 file; hook pre-commit đòi node; bug cắt đôi cặp ở export tồn kho streaming (lộ khi chạy full suite) |

### Hoãn có chủ đích (cần quyết định riêng, không làm trong đợt này)
- Tách app `leads` khỏi `orders` (PhoneBlocklist, PhoneClaim, SaleReminder*, LeadAssignmentShare, ShiftWindow, FloatingPool*) — đổi `app_label` 8 model + migration state.
- Lô 7 cycle `customers↔orders` qua `orders/contracts/customer_port.py` — khối B còn 100 (phone_utils đã trả 6 entry).
- Tách `config/settings_parts/base.py` (1 313 dòng) — E402 đã về 3, giữ 1 file cho tới khi có lý do khác.
- Decimal tiền 4 chuẩn (12,2/14,2/18,2/20,2) — cần ALTER trên `orders`/`order_items`.
- Frontend: virtualization list (page size 200), `ShipmentsPage` tách khỏi ruột orders.
- MFA (TOTP) cho tài khoản quản trị.
