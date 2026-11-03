# Tái cấu trúc test suite toàn project — 2026-08-05

## Bối cảnh & vấn đề

Test suite hiện tại phình to theo kiểu "mỗi fix nhỏ một file test":

| Khu vực | File test | Dòng | Ghi chú |
|---|---|---|---|
| backend/apps/orders | 100 | ~18.9k | nhiều file 30–100 dòng |
| backend/apps/inventory | 84 | ~15.0k | tương tự |
| backend còn lại (16 app) | 178 | ~25.3k | kiotviet 27, customers 25, users 18… |
| **Backend tổng** | **362** | **~59.2k** | 3068 test (baseline collect 2026-08-05) |
| frontend/src | 139 | ~14.8k | vitest + RTL |

Hệ quả: khó tìm test theo domain, helper bị copy-paste hàng chục lần (`_make_user`
12 bản y hệt trong orders), import chéo giữa các file test (đổi tên 1 file là vỡ
file khác), test migration một-lần đã chạy xong trên prod vẫn chạy mãi trong CI.

## Nguyên tắc (tiêu chí quyết định)

1. **GỘP theo domain, không xóa hành vi.** Phần lớn file nhỏ là regression guard
   có chủ đích (docstring tiếng Việt giải thích lý do tồn tại) — khi gộp phải
   **giữ nguyên docstring** (chuyển thành docstring của class/section).
2. **XÓA chỉ khi** thuộc 1 trong 3 loại:
   a. Test gọi trực tiếp hàm RunPython của migration một-lần đã áp dụng trên prod
      (không chạy lại được, mục tiêu đã đạt) **và** hành vi còn sống đã được cover
      ở test khác;
   b. Trùng lặp coverage thật sự (cùng hành vi, assert ở 2 nơi — dẫn chứng cả 2);
   c. Test không có giá trị hồi quy (assert hằng số với chính nó).
3. **Helper dùng chung** đưa về `tests/helpers.py` của từng app (chỉ extract khi
   các bản copy y hệt nhau; helper khác nhau theo domain thì giữ tại chỗ).
   helpers.py **không được import `apps.kiotviet`** (ràng buộc `.importlinter`)
   — mock bằng string path.
4. **Không đổi hành vi test khi gộp**: giữ nguyên class, tôn trọng các bẫy đã
   biết (xem "Ràng buộc an toàn").
5. **File đích ≤ ~800 dòng**; file lớn sẵn có, mạch lạc thì giữ nguyên làm merge
   target.
6. Số test sau tái cấu trúc = baseline − số test xóa có chủ đích (đối chiếu bằng
   `pytest --collect-only`).

## Ràng buộc an toàn khi gộp (bắt buộc đọc trước khi sửa)

- **Redis thật dùng chung**: KHÔNG BAO GIỜ gộp file có `cache.clear()` trong
  `setUp` với file có comment cấm `cache.clear()` (FLUSHDB đè Redis chung).
  Giữ nguyên convention của từng cụm.
- **event_bus là singleton toàn process**: `test_status_journey_subscriber.py`
  và các test phụ thuộc subscriber phải tự re-register (fixture riêng), không
  gộp vào module TestCase khác, không hoisting lên conftest.
- **`override_settings(CACHES=...)` mức method** phải giữ nguyên mức method khi
  gộp (không hoisting lên class/module).
- **Import chéo giữa file test** (3 chỗ trong orders) được giải quyết bằng chính
  việc gộp — không để lại import chéo mới.
- **Migration test dùng scratch DB** (`django_db_setup` + `MigrationExecutor`)
  chỉ gộp với nhau, không gộp với test thường.
- Test có `transaction=True` + thread thật giữ nguyên style pytest, không ép vào
  TestCase.

## Cấu trúc đích (convention mới)

```
backend/apps/<app>/tests/
  helpers.py            # helper dùng chung trong app (factory user/order, mock)
  test_<domain>.py      # 1 file / domain nghiệp vụ, nhiều class theo chủ đề
  test_migrations_scratch_db.py  # (nếu có) mọi test migration chạy scratch DB
frontend/src/features/<feature>/__tests__/
  <feature-area>.test.tsx  # 1 file / component-feature, gộp các file tí hon
```

Quy tắc từ nay về sau (ghi vào README test / review checklist): **fix nhỏ bổ sung
test vào file domain sẵn có, không tạo file mới** trừ khi mở domain mới.

## Sprint plan & tiến độ

| Sprint | Phạm vi | Trạng thái |
|---|---|---|
| 1 | orders: 100 → ~56 file, helpers.py, xóa D1/D2/D3 | 🔄 đang làm |
| 2 | inventory: 84 file → gộp theo bản đồ agent | ⏳ chờ báo cáo phân tích |
| 3 | 16 app backend còn lại (178 file) | ⏳ chờ báo cáo phân tích |
| 4 | frontend (139 file) | ⏳ chờ báo cáo phân tích |
| 5 | Verify toàn suite backend + frontend, đối chiếu số test, MR | ⏳ |

## Sprint 1 — orders (bản đồ tóm tắt)

Nguồn: báo cáo phân tích 100 file, 2026-08-05.

**Xóa (3 mục):**
- `test_age_group_migration.py` — gọi RunPython của `0110_backfill_age_group`
  (đã áp dụng; head hiện 0115); cả 3 assert đã được cover trong
  `test_age_group_backfill.py` (đối chiếu từng case trong báo cáo).
- `test_landing_orders_scope_migration.py` — data-fix RBAC một-lần
  (`users.0022`), test trên model hiện tại chứ không phải state lịch sử; hành vi
  scope còn sống cover bởi `test_order_record_scope.py` + `test_record_scope_coverage.py`.
- `test_return_slip_array.py::ReturnSlipArrayMigrationSqlTests` (chỉ class) —
  bị thay thế bởi `test_return_slip_migration.py` chạy migration thật trên
  scratch DB (docstring file sau nêu đích danh điều này).

**Gộp (các cụm chính):** ads_reports (5→1), sale_reports (3→1, gỡ import chéo
`_ReportFixtureMixin`), overview_report (3→1), age_group (4→1), contact_list
(3→1) + contact_api_scope (3→1, gỡ import chéo), record_scope contact/read
(4→2), dispatch guard (2→1, đổi tên 4 test trùng tên với suffix `_batch`/`_inline`),
dispatch_stock (2→1 + gộp `test_dispatch_batch_record_scope`), shipping_lookup
(3→1), kship (4→1), price_book (5→3), return/recall/exists-filters (6→3),
migrations scratch-db (2→1, dedup 4 helper y hệt), order_list filters/contract/
outside (8→3), journey_events (3→1), dynamic_assessment (2→1) + assessment_ai
(2→1), ingest (2→1), lead_assignment (2→1), floating_pool (2→1, gỡ import chéo),
print/delivery-slip (4→1), export (2→1), pure-unit không DB (5→1
`test_orders_units.py`).

**helpers.py (orders):** `make_scoped_user` (12 bản copy y hệt), `mock_kiotviet`
(3 bản, patch string-path), `ingest_headers` (3 bản), `no_sale_movement` (2 bản).

**Giữ nguyên:** 20 file lớn mạch lạc (dashboard_summary 760, ingest_api 728,
order_record_scope 523, bulk_action 507, search_index_usage 499, record_scope_coverage 370 —
cấm gộp vì cơ chế AST đặc thù, v.v.)

## Sprint 2 — inventory (bản đồ tóm tắt)

84 file → ~32 file (−62%). **Không có cặp file trùng lặp thật sự** — mọi overlap
là khác tầng (service vs API vs DB constraint); dư thừa nằm ở helper copy-paste.

**Xóa (2, tier 1):** `test_return_line_quantity_migration.py` (replay RunPython
`inventory.0023` đã áp dụng; constraint quantity>=1 đã được guard live ở 2 chỗ),
`test_return_entry_migration.py` (replay `inventory.0022`, irreversible, đã áp
dụng; invariant sống cover ở return_entry_model/shipping_code_unique).
`test_occurred_at_backfill.py` (461L) GIỮ — service của nó vẫn được warranty
tham chiếu làm reference pattern; chỉ xóa khi retire service.

**Gộp chính:** pending_scan 13→3 (core/match/manual — PHẢI extract `_make_order`
ra helpers.py TRƯỚC vì 2 file import chéo), return entry/api/lines/constraints/
concurrency/cancel-semantics 21→7, occurred_at 4→1, db schema+partition 4→1,
stock document 5→2, reissue sweeps 2→1, sibling transfer 2→1, stock query 3→1,
commands 2→1, issue history 3→1, flow excel 2→1 (giữ per-test django_db mark,
KHÔNG module-level — 11 test đang không chạm DB), robustness_hardening 1 file
grab-bag → chia về 4 cụm đúng chỗ. Thêm `tests/helpers.py` + `tests/conftest.py`
(fixture warehouse ×23, channel ×9...).

**Bẫy riêng:** event_bus 6 file (chỉ 1 file kỷ luật clear/re-register — khi gộp
C7/C9 phải thêm pattern clear+addCleanup); TransactionTestCase 7 file (giữ
`reset_sequences=True` khi gộp partition); partition test tạo bảng 2030/2035
không drop — giữ co-located; 2 file migration scratch DB mutate settings —
không gộp với test thường; 3 file threading + advisory lock giữ nguyên style.

## Sprint 3 — 16 app backend còn lại (bản đồ tóm tắt)

178 → 125 file (−30%), 37 cụm gộp, **0 file xóa vô điều kiện** (2 mục có điều
kiện: `warranty/test_migrations.py` 8L chỉ xóa nếu CI đã chạy makemigrations
--check toàn repo — nếu chưa thì nâng cấp thành test repo-level;
`customers/test_phone_unique.py::DedupMigrationTests` khuyến nghị GIỮ vì là
coverage duy nhất của rule chọn survivor).

**Cụm chính:** users 4 file section → 1 `test_rbac_sections.py` (table-driven;
CHÚ Ý 2 cặp test trùng tên sẽ shadow im lặng nếu gộp nguyên trạng — phải đổi
tên); kiotviet 5 file import edge-case → 1; customers report_doc 5→1,
behavior 4→1, resale 3→1; common settings guards 4→1, ops 2→1, http layer 3→1;
warranty tracking 3→1, intake 2→1, journey 2→1; best_express 4 cụm absorb;
facebook_ads 3 cụm; hr 2 cụm; còn lại 1-2 cụm/app; warehouses/google_ads/
dispatch không đổi.

**Bẫy toàn cục:** (1) `event_bus.clear()` ở kiotviet/test_invoice_webhook,
hr/test_employee_service, common/test_events — 3 conftest guard (warranty, hr,
cnv_loyalty) là bất khả xâm phạm; (2) LocMemCache trùng LOCATION là share
chung 1 dict — 2 file pin LOCATION phải khác nhau khi về cùng module;
(3) channels_core CH2 là merge nguy hiểm nhất: `@override_settings(CACHES=LocMem)`
phải giữ mức class, rớt decorator là `cache.clear()` FLUSHDB Redis thật;
(4) các cặp tách 2 app theo import-linter (common↔users exception handler,
warranty/best_webhook_realtime) KHÔNG được di dời; (5) 7 bản `make_account`
(best_express + warranty), 3 bản `_FakeResponse` (facebook_ads), 2 bản
`_reset_quota_counters` đè quota chung (callcenter) → extract `_helpers.py`/
`_factories.py` per-app TRƯỚC khi gộp.

**Thứ tự thực hiện:** users U1 → contact_flow/audit/callcenter → customers/
kiotviet → common/facebook_ads/best_express (dedup helper trước) →
channels_core CH2 cuối cùng.

## Sprint 4 — frontend (bản đồ tóm tắt)

140 file → ~106 file (−24%) + 4 module test-utils dùng chung. **Không có test
xóa được** (0 snapshot test, 0 smoke test, 0 skip, 0 test của feature đã gỡ);
chỉ `reportLinks.test.ts` (8L) được hấp thụ vào file gộp sau khi port assertion.

**Thứ tự bắt buộc:** (1) đưa `afterEach(cleanup)` + stub ResizeObserver vào
`src/test/setup.ts` (tiền đề số 1 — ~60 file đang tự cleanup, gộp 2 file mà 1
file thiếu cleanup là DOM chồng nhau); (2) mở rộng test-utils (`renderWithI18n`
đang chỉ 3/140 file dùng, `renderWithClient` thay 33 bản QueryClient chép tay,
`mockToast` thay 17 bản vi.mock sonner); (3) gộp cụm không hazard: report
primitives/panels 9→2, ads charts/tables 9→4, inventory display helpers 4→1,
scan logic 4→2, contacts 7→5, callcenter 4→2, dashboard 3→2, overview 4→3,
warranty 9→7, di dời 6 file lạc chỗ (§N); (4) gộp cụm nhạy mock từng cái một:
flow-tabs 3→1, scan dialogs 6→2, orders hooks 2→1 (factory vi.mock phải export
đủ 6 hàm); (5) BỎ QUA cụm StockDocumentsPage (mock ledger-queries toàn file sẽ
stub nhầm hook thật — coverage loss im lặng).

**Bẫy:** vi.mock hoisting (2 flavour mock http không được trộn); state mock
mức module (Map outcomes/lookupUrls) phải beforeEach clear khi gộp;
`RequireAuth.test.tsx` 2 test đỏ GIẢ trên Node 24 (undici siết AbortSignal,
package.json đã ghi engines <24, CI chạy Node 22) — không sửa, không lấy làm
tín hiệu regression khi chạy local.

## Nhật ký sprint

### Sprint 1 — orders (2026-08-05) ✅ XONG
- **100 file → 49 file + helpers.py** (giảm 51%).
- Xóa: `test_age_group_migration.py` (3 test), `test_landing_orders_scope_migration.py`
  (4 test), class `ReturnSlipArrayMigrationSqlTests` trong `test_return_slip_array.py`
  (5 test) — tổng 12 test migration một-lần đã áp dụng, có dẫn chứng coverage thay thế.
- `helpers.py` mới: `make_scoped_user` (thay 12 bản copy), `make_scoped_user_by_codes`,
  `mock_kiotviet` (thay 3 bản), `ingest_headers` (thay 3 bản), `no_sale_movement`.
- `.importlinter`: cập nhật 6 dòng tham chiếu module test bị đổi tên.
- **Verify chính thức: toàn app orders 1018 test + 16 subtest PASS (12m56s), ruff sạch.**
  (Baseline trước tái cấu trúc: 1030 test = 1018 + 12 test xóa có chủ đích.)
- Ghi chú: 4 test trùng tên giữa 2 file dispatch guard được đổi tên suffix
  `_batch`/`_inline` (không phải trùng lặp — 2 entry point khác nhau).

### Sprint 1 bổ sung
- 3 file giữ-nguyên (bulk_action, order_record_scope, search_index_usage) cũng đã
  swap `_make_user` → `helpers.make_scoped_user` (xác nhận body y hệt).

### Sprint 2 — inventory (2026-08-05) ✅ XONG
- **84 file → 37 file + helpers.py** (−56%).
- Prep: helpers.py (make_order, sellable, pending_scan, return_line, movements_for),
  gỡ 2 import chéo giữa file test; collect 590 = 590.
- Gộp: pending_scan 13→3 (87 pass), return core/api/lines/constraints 14→4
  (85 pass; 3 test 400-case gộp thành 1 parametrize 2 case — đổi −1 test có chủ đích),
  concurrency/cancel-semantics/sync/commands 9→4 (30 pass), awaiting/excel/
  history/occurred_at/db-schema 17→5 (77 pass), stock doc/reissue/sibling/query/
  command 14→6 (104 pass).
- Xóa: `test_return_entry_migration.py` + `test_return_line_quantity_migration.py`
  (2 test replay migration 0022/0023 đã áp dụng; constraint sống có guard riêng).
- Quyết định lệch so với phân tích: GIỮ `test_robustness_hardening.py` (không chia
  7 test đi 4 nơi), GIỮ `test_occurred_at_backfill.py` (service còn được warranty
  tham chiếu), KHÔNG thêm kỷ luật event_bus.clear() mới khi gộp (giữ nguyên hành
  vi; follow-up riêng nếu muốn chuẩn hóa).

### Sprint 3 — 16 app backend còn lại (2026-08-05) ✅ XONG
- **178 → 125 file** đúng bản đồ (users 17→10, kiotviet 27→19, customers 25→16,
  common 17→10, warranty 17→12, hr 13→11, best_express 12→8 (+_factories.py),
  facebook_ads 9→6 (+_fakes.py), cnv_loyalty 9→7, callcenter 9→8,
  channels_core 8→6, contact_flow 6→5, audit 5→4; 3 app 1-file giữ nguyên).
- Kết quả chạy theo cụm: 70+74+52+76+63+109+51+42 pass, collect khớp tuyệt đối
  từng cụm, 0 test xóa. Cụm nguy hiểm CH2 (LocMemCache class-scoped) đã kiểm.
- `.importlinter`: vá staging_neutralize→command_guards (4 dòng),
  channel_price_book_api→channel_model_api, return_order_status→return_order_sync
  (gỡ trùng), pending_scan_cancelled→pending_scan_match.
- Follow-up ghi nhận: import chéo CÓ SẴN `test_webhook_hardening.py` →
  `test_webhook_service.py` (best_express) vẫn hoạt động, để nguyên;
  `warranty/test_migrations.py` giữ nguyên (chưa xác nhận CI có makemigrations
  --check toàn repo; cân nhắc nâng repo-level sau).

### Sprint 4 — frontend (2026-08-05) ✅ XONG
- **140 → 107 file** + 4 module dùng chung (src/test/utils.tsx,
  ads fixtures.ts, inventory flow-test-utils.tsx, scan-test-utils.tsx).
- GĐ1: setup.ts thêm afterEach(cleanup) toàn cục + stub ResizeObserver; 6 file
  di dời đúng chỗ (§N); 11 cụm an toàn gộp — 230/230 test pass, tsc sạch;
  reportLinks.test.ts được port assertion rồi xóa (test duy nhất bị xóa của FE).
- GĐ2: scan dialogs 6→2, flow-tabs 3→1, orders hooks 2→1 (factory vi.mock union
  đủ 6 hàm) — 56/56 pass; 5 file lẻ chuyển sang dùng rig chung.
- Cụm StockDocumentsPage BỎ QUA có chủ đích (mock ledger-queries toàn file sẽ
  stub nhầm hook thật). RequireAuth.test.tsx không đụng (2 test đỏ giả Node 24).

### Tổng kết
| Khu vực | Trước | Sau | Test xóa có chủ đích |
|---|---|---|---|
| Backend (18 app) | 362 file / 3068 test | 212 file (−41%) | 15 test (12 orders + 2 inventory + 1 parametrize hợp nhất) |
| Frontend | 140 file | 107 file (−24%) | 0 (1 test được port vào file gộp) |

Verify cuối:
- Frontend vitest full: **848/855 pass**. 7 fail = 2 RequireAuth (đỏ giả Node 24,
  package.json engines đã ghi) + 5 test trong
  `use-orders-data.realtime-upsert-filters.test.tsx` — **đỏ SẴN trên master**
  (đã chạy đối chứng trên checkout master: y hệt 5 failed | 4 passed; không phải
  regression của tái cấu trúc; cần fix riêng ngoài MR này).
- Backend toàn suite Docker (23m07s): **3047 pass / 6 fail**; collect 3053 =
  baseline 3068 − 15 test xóa có chủ đích ✓. Cả 6 fail nằm trong
  `customers/test_report_trends.py` (file KHÔNG bị sửa) và **đỏ SẴN trên master**
  (đã chạy đối chứng cùng lệnh trên checkout master: y hệt 6 failed | 12 passed)
  — cần fix riêng ngoài MR này. **Tái cấu trúc không gây regression nào.**
