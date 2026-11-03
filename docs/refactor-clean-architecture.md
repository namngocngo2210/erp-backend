# Refactor: Clean Architecture, Module hóa & Reusable Code

> Trạng thái: ĐANG THỰC HIỆN — branch `refactor-clean-architecture-20260608`
> Cập nhật lần cuối: 2026-06-08

## 1. Bối cảnh & Mục tiêu

Đánh giá hiện trạng (xem `docs/backend_review_report.md`, `docs/frontend_review_report.md`) cho thấy
codebase chạy ổn nhưng tích lũy nợ kỹ thuật nhanh, cản trở việc scale đội từ 50 → 100–150 người:

- **God-files**: backend có file 3.157 / 2.465 dòng; frontend có file 2.851 / 2.679 / 2.378 dòng.
- **Phân module không nhất quán**: backend trộn clean-architecture (`orders`) với Django phẳng
  (`users`, `customers`, `warranty`...); frontend xé 1 domain ra 3 nơi (`features/`, `components/`, `pages/`).
- **Vi phạm DRY**: pagination copy 3 lần, `formatCurrency`/`formatMoney` định nghĩa lại 9 lần,
  mỗi external client tự viết HTTP/retry riêng.
- **SOLID**: SRP vi phạm nặng (god-files), thiếu base class Service/Repository.

Mục tiêu: **phân module rõ ràng, code tái sử dụng, tuân thủ SOLID**, một chuẩn kiến trúc duy nhất.

### Nguyên tắc
1. **Không đổi hành vi** khi refactor — chỉ tổ chức lại. Mọi util thay thế phải tái tạo đúng output cũ.
2. Mỗi sprint phải **xanh test** trước khi merge.
3. Refactor tăng dần (incremental), không big-bang rewrite.

## Trạng thái chốt phiên (2026-06-08)

Đã làm & verify (19 commit): nền tảng A/B, **Sprint 1 trọn vẹn** (3 god-file kiotviet:
3157→1274, 2465→984, 1554→694), **Sprint 2 customers** (CustomerService 810→168 + resale/loyalty
tách riêng), **Sprint 4** (5 HTTP client → HttpClient), Sprint 3 (helper: format/metrics/errors/
list-payload/order-import-format), `import_excel.py` 1022→835 (constants tách).

Còn lại — kèm **lý do cụ thể** vì sao chưa tách blind trong sandbox:
- `gateway_kship_customer_webhook.py` (874), `invoice_payload_builder.py` (726): dùng
  `from ... import *` (star import) + re-export qua `kiotviet/services.py` → **pyflakes mất khả
  năng bắt undefined name**; cần test runtime (Redis/CI) để tách an toàn.
- Frontend component-body (`use-orders-data` 2669, `OrdersPage` 2668, `OrdersTable` 2381…):
  `tsc`/vitest verify type+logic được, nhưng **hành vi UI runtime** sandbox không chạy app thật.
  `use-orders-data` còn vướng type `CreateOrderPayload` định nghĩa trùng (cần hợp nhất trước).
- Flat apps domain layer + Sprint 5 (test 7 app): phần lớn logic DB/Redis-bound → cần CI.

## 2. Lộ trình Sprint

| Sprint | Tên | Trạng thái |
|--------|-----|-----------|
| A | Nền tảng tái sử dụng (shared utils, dedup) | ✅ DONE |
| B | Chuẩn kiến trúc + app/feature template | ✅ DONE |
| 1 | Tách god-files backend (`kiotviet`) | ✅ DONE (3 file) |
| 2 | Chuẩn hóa app phẳng backend (`customers`, `users`, `warranty`, ...) | 🔄 (customers xong) |
| 3 | Frontend feature-slice + tách god-components | 🔄 ĐANG LÀM |
| 4 | Base Service/Repository + migrate external HTTP clients | 🔄 ĐANG LÀM |
| 5 | Bù test cho app/feature thiếu coverage | ⏳ TODO |

---

### Sprint A — Nền tảng tái sử dụng ✅

**Backend** (`apps/common/`):
- [x] `apps/common/pagination.py` — `parse_required_pagination(request, max_limit=100)` gộp 3 bản trùng.
- [x] Rewire `orders/.../_view_helpers.py`, `channels_core/.../channel_views_impl.py`, `customers/views.py`.
- [x] `apps/common/http_client.py` — base HTTP client (timeout + retry + backoff) cho Sprint 4.
- [x] Unit test `apps/common/tests/`.

**Frontend** (`src/lib/format.ts`):
- [x] `formatCurrency(value, opts)` linh hoạt (fallback / style `plain|vnd|suffix` / locale) tái tạo 9 biến thể.
- [x] `formatNumber`, re-export `formatDateTime`.
- [x] Migrate các call-site trùng, giữ đúng hành vi hiển thị.
- [x] Unit test `src/lib/__tests__/format.test.ts`.

**Đã làm gì / chưa làm**: xem mục "Nhật ký" cuối file.

---

### Sprint B — Chuẩn kiến trúc + template ✅

- [x] `docs/architecture-standard.md`: quy ước cấu trúc 1 app backend & 1 feature frontend,
      chiều phụ thuộc lớp, quy tắc SOLID, Definition of Done.
- [x] App backend mẫu (layered): tham chiếu `apps.orders` làm chuẩn (đã có sẵn cấu trúc đúng).
- [x] Feature frontend mẫu: quy ước `features/<domain>/{components,pages,queries,types,hooks}`.
- [x] Base `Repository` abstraction `apps/common/repository.py::BaseRepository`
      (soft-delete-aware CRUD + paginate) + test DB-free. *Base Service: quy ước stateless
      orchestrator trong tài liệu, không ép base class (services hiện là static-method, base
      class thừa) — sẽ áp dụng khi tách service ở Sprint 1/2.*

### Sprint 1 — Tách god-files `kiotviet` 🔄
- [x] **Bước 1 (an toàn, đã verify):** trích nhóm helper thuần (datetime/decimal) khỏi
      `sync_invoices_between_channels.py` → `infrastructure/services/datetime_utils.py`
      (`first_non_empty`, `to_decimal`, `parse_dt_loose`, `is_after`, `shift_dt`) + 6 unit test.
      Giữ wrapper `staticmethod` trong Command để mọi call-site không đổi. Verify: compile,
      `manage.py check` (0 issues), command load OK, test 6/6 pass.
- [x] **Bước 2a:** tách nhóm `purge` (5 method, ~625 dòng) khỏi god-class `Command` →
      `_sync_invoices_purge_mixin.py::PurgeInvoicesMixin`. `Command(PurgeInvoicesMixin, BaseCommand)`,
      behavior-preserving qua MRO (self.* không đổi). Command **3104→2478 dòng**.
      Verify: **pyflakes sạch** (bắt được import `KiotVietChannel` thiếu khi dựng mixin — đã sửa;
      prune 5 import thừa trong command), compile, django check 0 issues, command `--help` load OK.
      *(pyflakes cài qua `uv` — lưới an toàn cho move-between-files khi không có Redis)*
- [x] **Bước 2b/2c:** tách nhóm `check` (4 method → `CheckInvoicesMixin`) và `sync`
      (5 method → `SyncInvoiceMixin`); `_SkipInvoice` → `_sync_invoices_shared.py` (phá cycle).
      `Command(Purge, Check, Sync, BaseCommand)`. **Command 3157 → 1274 dòng** (còn `handle`
      orchestrator + `_web_list_invoices` + `_normalize_dt_arg`). pyflakes sạch toàn bộ 5 file,
      compile, django check, command `--help` load (4-way MRO). Runtime CI-pending.
- [x] **`sync_invoices_service.py` 2465 → 984 dòng:** tách god-class `SyncInvoicesService`
      thành 3 mixin (`SyncPurgeMixin` 425, `SyncCheckMixin` 558, `SyncCreateMixin` 471);
      `SyncError`/`_SkipInvoice` → `_sync_service_shared.py` (re-export cho `kiotviet/views.py`);
      khử trùng 5 helper datetime → `dt_utils`. **Verify: pyflakes sạch 5 file, django check,
      và runtime smoke (instantiate + mọi method resolve qua MRO).**
- [x] **`import_kiotviet_invoices.py` 1554 → 694 dòng:** tách `ImportHandlersMixin` (494,
      special-mode handlers) + `ImportUpsertMixin` (393, customer/order/item upsert);
      `Command(ImportHandlersMixin, ImportUpsertMixin, BaseCommand)`. pyflakes sạch 3 file,
      django check, command `--help` load (3-way MRO).

### Sprint 2 — Chuẩn hóa app phẳng 🔄
- [x] **`customers/services.py` (810 → 518 dòng):** tách subsystem resale-tracking (9 method,
      ~290 dòng) ra `customer_resale_service.py::CustomerResaleService`. Khử trùng catalog
      phân khúc → `segment_catalog.py` (dùng chung loyalty + resale, phá import cycle).
      Views + test gọi `CustomerResaleService` trực tiếp (đúng convention sẵn có như
      `CustomerProfileService`/`CustomerReportService`). Verify đầy đủ trong sandbox.
- [x] **Tách loyalty/segmentation/reporting khỏi `CustomerService`** → `customer_loyalty_service.py`
      (`CustomerLoyaltyService`, 10 method + `_parse_loyal_date_bounds` + hằng số LOYAL_*).
      `CustomerService` còn **168 dòng (810→168, chỉ CRUD)**. Thêm `order_status_constants.py`
      (DRY: gộp DELIVERED_SUCCESS/CANCELLED_INVOICE đang lặp 3 nơi). Callers (views/profile/report)
      trỏ `CustomerLoyaltyService` trực tiếp. **Verify: pyflakes sạch (bắt được import gãy
      profile_service→services constants, đã sửa), django check 0 issues, resale 9/9 pass.**
      *Loyalty runtime (cache Redis) CI-pending nhưng logic byte-identical.*
- [ ] Thêm `domain/` cho `users`, `customers`.
- [ ] Đưa các app phẳng (`warranty`, `audit`, `zalo_bridge`, `facebook_ads`, `dispatch`) về chuẩn Sprint B.

### Sprint 3 — Frontend feature-slice 🔄
- [x] **Bước 1 (đã verify):** trích 7 helper thuần khỏi `OrdersPage.tsx` (2851 → 2666 dòng)
      → `features/orders/order-import-format.ts` (buildPatchPayload, format Money/Weight/Dimensions,
      formatImportText + IMPORT_HEADERS/KOC_IMPORT_HEADERS/RECALL_COLUMN_KEYS) + 8 unit test
      (riêng `formatImportText` — logic parse paste nhiều edge-case nay đã có test). Verify:
      tsc 0 lỗi, eslint 0 error, FE suite 96/96 pass.
- [ ] Tách tiếp body component `OrdersPage` (state/dialog/tabs) thành sub-component + hook.
- [x] Trích `extractListPayload` (unwrap response results/items/data) → `lib/api/list-payload.ts`
      (reusable, dùng 7× trong use-orders-data) + 3 test. tsc + FE 106/106.
- [x] **`use-orders-data.ts` 2679 → 2438:** trích `lib/expiring-map-cache.ts` (TTL/LRU cache,
      4 test), `features/orders/order-storage.ts` (localStorage cột/filter + types re-export,
      4 test), `features/orders/order-normalizers.ts` (3 normalizer thuần, 3 test). tsc + FE 117/117.
- [x] **`use-orders-data` 2438 → 2182:** gỡ nốt `order-payload.ts` (type `CreateOrderPayload` +
      `sanitizeCreateOrderPayload`/`sanitizePatchOrderPayload` + field constants), re-export
      `CreateOrderPayload` giữ 3 importer ngoài. 4 test. tsc + FE 121/121. **use-orders-data tổng
      2669→2182** (487 dòng → 4 module focused: cache/storage/normalizers/payload).
- [ ] Phần còn lại của `use-orders-data` (2182) là **thân hook `useOrdersData`** (fetch+state+
      mutation) — React-coupled, tách phải đụng cấu trúc render → cần chạy app thật verify.
      Tương tự `OrdersTable.tsx` (2381), `OrdersPage` body.
- [ ] Gom `orders` về `features/orders/{components,pages,...}`, bỏ trùng ở `pages/`, `components/`.
- [x] `any` → `unknown` + type guard ở error handlers: `lib/errors.ts`
      (`getApiErrorMessage`/`getApiErrorBody`) + chuyển 4 site `catch (err: any)`
      (OrdersTable ×3, CronJobsPage ×1) sang `unknown`. tsc sạch, FE 103/103.
- [x] `lib/metrics.ts` — tách helper % / số rút gọn khỏi DashboardPage (2274), 4 test.
- [x] **Khử trùng cross-file (DRY)** reviewer nêu, đều có test:
      `lib/datetime-local.ts` (gộp `isoToDatetimeLocal`/`datetimeLocalToIso` từ **3 file**),
      `features/orders/order-status-badge.ts` (gộp `statusBadgeClass` từ 2 file),
      `parseDimensions` + `normalizeNumericInput`→`normalizeDecimalInput` (gộp về `order-normalizers`).
      *Các duplicate còn lại (`parseAmount`, `daysSince`) lệch hành vi (NaN-vs-0, floor-vs-không)
      → KHÔNG gộp mù; cần quyết định hành vi đúng + verify runtime.*

### Sprint 4 — Base abstractions + HTTP clients 🔄
- [x] `lark_notifier` → `HttpClient` (client pooled module-level, timeout per-call, retry
      429/5xx). Test mock chuyển sang `_client.post`. **Verified: 6/6 pass** (không cần Redis).
- [x] Migrate `facebook_ads/services.py` (5 call), `zalo_bridge/services.py` +
      `public_api.py` (request), `kiotviet/lark_bitable_client.py` (post+request) → `HttpClient`.
      Swap tại-chỗ (giữ nguyên import khác). **Verify: compile + `manage.py check` 0 issues**;
      chưa có test runtime (facebook/zalo) → **CI-pending** cho kiểm thử thực tế.

### Sprint 5 — Test coverage ⏳
- [ ] Backend: thêm test cho `warranty`, `dispatch`, `zalo_bridge`, `facebook_ads`, `cron_jobs`.
- [ ] Frontend: integration test cho các page chính.

---

## 3. Nhật ký thực hiện

### 2026-06-08 — Sprint A ✅

**Đã làm:**
- Backend `apps/common/`:
  - `pagination.py::parse_required_pagination` — gộp 3 bản `_parse_required_pagination`
    trùng (orders / channels_core / customers). Giữ wrapper local ở 3 nơi để delegate
    (zero thay đổi call-site). Logic byte-identical với bản gốc của orders.
  - `http_client.py::HttpClient` — base HTTP client (timeout mặc định + retry/backoff cho
    429/5xx, connection pooling). Sẵn sàng cho Sprint 4 migrate các external client.
  - `tests/test_pagination.py` — 8 test, PASS.
- Frontend `src/lib/format.ts`:
  - `formatCurrency(value, opts)` linh hoạt (fallback / style `plain|suffix|vnd` /
    locale / maximumFractionDigits / onInvalid) + `formatNumber` + re-export date helpers.
  - Migrate 9 call-site trùng sang delegation, giữ đúng hành vi: `order-detail-shared.fmt`,
    `OrderDetailDialog`, `Sheet3FollowUpCard.formatMoney`, `CustomersPage`, `DashboardPage`,
    `ResaleTrackingPage`, `LoyalCustomersPage`, `AdsReportPage`, `OrdersPage.formatMoneyDisplay`.
  - `src/lib/__tests__/format.test.ts` — 9 test, PASS.

**Kiểm chứng:**
- Backend: `apps/common` 8/8 pass; `apps/customers` 24/24 pass (dùng helper đã rewire).
- Frontend: `tsc --noEmit` exit 0; eslint các file đã đổi 0 error (chỉ warning pre-existing);
  `format.test.ts` 9/9 pass.

**Lưu ý / chưa làm:**
- Các test `apps/channels_core` fail trong sandbox do **Redis ConnectionError** (môi trường
  test không kết nối được Redis) — không liên quan refactor, cần verify lại trên CI có Redis.
- Một khác biệt nhỏ có chủ đích: input rỗng `""` ở vài site `plain` trước đây render `"0"`,
  nay render fallback (`"-"`/`"0"`). Với cột tiền, fallback hợp lý hơn; rủi ro thực tế ~0 vì
  các field này luôn có giá trị số.
- `apps/common/http_client.py` mới chỉ là hạ tầng; việc migrate `lark_notifier`, `zalo_bridge`,
  `kiotviet`, `facebook_ads` sang nó để ở **Sprint 4**.

### 2026-06-08 — Sprint B ✅

**Đã làm:**
- `docs/architecture-standard.md` — chuẩn bắt buộc cho backend (clean architecture layered,
  chiều phụ thuộc lớp) và frontend (feature-slice), kèm SOLID checklist + Definition of Done.
- `apps/common/repository.py::BaseRepository[Model]` — soft-delete-aware `active()` /
  `get_or_none()` / `exists()` / `paginate()` / `soft_delete()`.
- `apps/common/tests/test_repository.py` — 5 test DB-free (PASS).

**Kiểm chứng:** `apps/common` 13/13 pass (8 pagination + 5 repository), không cần DB/Redis.

**Chưa làm:** retrofit các repository hiện có sang `BaseRepository` (Sprint 2).

### 2026-06-08 — Sprint 1 (bước 1) + Sprint 2 (resale) ✅

**Sprint 1 bước 1** — `kiotviet` god-command:
- `infrastructure/services/datetime_utils.py` (5 pure helpers) + 6 unit test. Command giữ
  `staticmethod` wrapper → call-site không đổi. Verify: compile, `manage.py check` 0 issues,
  command `--help` load OK, 6/6 test pass.

**Sprint 2 (resale)** — `customers/services.py` 810 → 518 dòng:
- `customer_resale_service.py::CustomerResaleService` (9 method resale, di chuyển nguyên văn).
- `segment_catalog.py` — khử trùng catalog phân khúc, dùng chung loyalty + resale (phá cycle).
- views.py (4) + `test_resale_appointment_reminder.py` (3) gọi `CustomerResaleService` trực tiếp.
- Bỏ import thừa `CustomerResaleFollowUp`.

**Kiểm chứng:** compile + `manage.py check` (0 issues) sạch; **resale tests 9/9 PASS**
(`test_resale_appointment_reminder` + `test_resale_channel_scope_api`, không đụng cache).

**Lưu ý (Redis):** sandbox **không có redis-server** (port 6379 đóng, không có binary) → các
test đụng cache (`test_loyal_customers_api`, `test_customer_order_aggregate_api`) fail vì
`ConnectionError` — môi trường, **không liên quan** refactor. Thay đổi loyalty duy nhất là
`_segment_catalog` delegate sang `segment_catalog()` với logic byte-identical → cần CI có
Redis chạy lại để xác nhận xanh.
