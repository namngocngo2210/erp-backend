# Audit bộ test backend — 2026-09-23

Phạm vi: `backend/apps/**` (pytest 9.0.3 + pytest-django 4.12; `backend/pytest.ini`: `testpaths=apps`, `python_files = tests.py test_*.py *_tests.py`). Chỉ đọc và đo, không sửa code, không commit.

## 0. Cách chạy & số liệu đo được

**Cách team chạy** (theo `docs/superpowers/plans/*`): `docker run --rm` image backend, mount `backend/` vào `/app`, env trỏ `desi-test-pg`/`desi-test-redis`, bắt buộc `ENCRYPTION_KEY` (thiếu → toàn bộ ERROR ở migration kiotviet 0009), `-p no:cacheprovider`, mỗi worktree một `POSTGRES_DB` + `REDIS_CACHE_DB` riêng, **không chạy 2 lượt song song**. CI (`.gitlab-ci.yml` job `backend:test`, dòng 136-168): `pytest apps -q --junitxml=report.xml` trên postgres:16 + redis:7 service, `cd backend` trong repo đầy đủ. `pytest-cov`/`coverage` **không có** trong `requirements*.txt` lẫn image → không đo được coverage dòng; §6 dùng ánh xạ tĩnh.

**Lệnh tôi chạy** (image `desi-order-web:latest` có sẵn pytest; `desi-test-pg` 172.17.0.2 user `desi_order`, `desi-test-redis` 172.17.0.3, DB `desi_audit`, `REDIS_CACHE_DB=9`, mount `backend/` → `/app`):

```
docker run --rm --user 1000:1000 -e HOME=/tmp -e POSTGRES_HOST=172.17.0.2 -e POSTGRES_DB=desi_audit \
  -e POSTGRES_USER=desi_order -e POSTGRES_PASSWORD=testpass -e REDIS_HOST=172.17.0.3 -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 \
  -e APP_ENV=development -e DJANGO_DEBUG=0 -e DJANGO_SECRET_KEY=... -e JWT_SIGNING_KEY=... -e ENCRYPTION_KEY=AAAA...= \
  -e PYTHONDONTWRITEBYTECODE=1 -v $PWD/backend:/app -w /app desi-order-web:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest -p no:cacheprovider --durations=30 -q -ra --create-db --junitxml=/out/junit.xml'
```

| Chỉ số | Giá trị |
|---|---|
| File test (match pattern) | **412** (+9 file `helpers.py`/`_fakes.py`/`factories.py` trong `tests/` — helper, không chứa test) |
| Hàm test định nghĩa (AST) | 5.311 |
| Test collect | **5.598** (parametrize nở +287; `--collect-only` 9,4 s) |
| Không collect được | **0** — đối chiếu AST ↔ `--collect-only`: mọi `def test_*` đều có trong collection |
| Kết quả | **5.592 passed, 6 failed, 0 skipped, 0 xfail**, 98 subtests passed, 1.861 warnings, EXIT=1 |
| Thời gian | **56 phút 37 s** wall (3.397 s; tổng call 3.379 s). Máy đang tải nặng lúc đo (load avg 6–18 do tiến trình khác trên VPS) — audit 2026-08-19 ghi "suite 19 phút"; **dùng tỉ lệ, không dùng số tuyệt đối** |

**6 fail:**
- 4 do **môi trường mount** (chỉ mount `backend/` nên thiếu thư mục cha): `apps/ai_mart/tests/test_compose_env_whitelist.py` (3 test đọc `/docker-compose.local.yml`, `docker-compose.prod.yml`, staging) và `apps/daily_report/tests/test_openapi.py::test_committed_file_matches_code` (đọc `../docs/openapi/daily-report.yaml`). Trong CI (repo đầy đủ) chúng pass. P2: 4 test này phụ thuộc file **ngoài package** — nên `skipIf(not path.exists)` kèm lý do, hoặc ghi rõ trong docs cách mount cả repo.
- 2 fail **thật trên code đã commit** (`git diff master` ở orders/pancake/users rỗng):
  - **P0** `apps/orders/tests/test_order_field_catalog.py:28` — field `address_level2` có trong `OrderInlineUpdateSerializer.Meta.fields` nhưng **không có trong `ALL_ORDER_FIELDS`** (catalog quyền sửa) → theo docstring `test_crm_field_catalog.py` field ngoài catalog "ai cũng sửa được". Cần thêm `address_level2` vào catalog (hoặc loại khỏi serializer).
  - **P0** `apps/users/tests/test_write_permission_coverage.py:140` — 3 view có method ghi chỉ gate bằng `require_section` (=:view): `orders/transport/views/order_views_impl/order_address_view.py:129 parse_address_level2`, `:166 lookup_raw_address` (POST nhưng thuần đọc → whitelist KÈM LÝ DO), `pancake/transport/views.py:451 retry_order` (ghi thật → cần `:edit`). Đây đúng lớp lỗi audit 08-19 N2 tái diễn lần nữa; guard làm đúng việc, nhưng **đang đỏ trên master** → cần xác nhận pipeline GitLab có Runner chạy thật hay không (`.gitlab-ci.yml` dòng 14-16 cảnh báo).

**Phân bố thời gian (junit):** <50 ms: 2.637 · <200 ms: 614 · <1 s: 1.432 · <5 s: 834 · **≥5 s: 81**.

| App | n | Tổng s | TB ms/test |
|---|---|---|---|
| orders | 1.805 | 1.485,5 | 823 |
| analytics | 79 | 381,4 | **4.828** |
| inventory | 814 | 334,3 | 411 |
| users | 193 | 205,8 | 1.067 |
| ai_mart | 149 | 164,4 | 1.103 (115,9 s là **setup tạo DB/migrate** dính vào test đầu tiên theo alphabet `test_app_registration.py::test_app_installed`, không phải test chậm) |
| callcenter | 209 | 129,6 | 620 |
| customers | 93 | 121,5 | 1.306 |
| contact_flow | 72 | 79,7 | 1.107 |
| channels_core / hr / media / foxia / pancake / exports / warranty / audit | 108/134/155/157/117/57/148/54 | 77/76/43/38/33/33/28/24 | 130–720 |
| facebook_ads 168 · warranty_codes 147 · **kiotviet 336** · daily_report 88 · cnv_loyalty 127 · google_ads 58 · best_express 109 | | 24/19/**18**/15/15/14/9 | 55–250 |
| common 174 · catalog 26 · dispatch 13 | | 1,5/0,5/0,1 | 7–18 |

Top file theo thời gian: `analytics/tests/test_marts.py` 169 s (34 test, **5,0 s/test**), `orders/tests/test_sale_reports.py` 140 s (63, 2,2 s), `orders/tests/test_order_record_scope.py` 93 s (38, 2,5 s), `analytics/tests/test_profile_api.py` 78 s (13, **6,0 s**), `orders/tests/test_order_bulk_action_permissions.py` 76 s (34, 2,2 s), `analytics/tests/test_refresh_service.py` 74 s (15, 4,9 s), `orders/tests/test_contact_record_scope.py` 63 s, `orders/tests/test_order_print.py` 62 s, `callcenter/tests/test_call_quality_api.py` 61 s, `orders/tests/test_migrations_scratch_db.py` 60 s (**3 test, 20 s/test**), `orders/tests/test_overview_report.py` 57 s, `users/tests/test_team_api.py` 57 s (19, 3,0 s).

30 test chậm nhất (`--durations=30`): 115,9 s setup `ai_mart/test_app_registration.py::test_app_installed` (= tạo DB); 19–24 s ×3 `orders/tests/test_migrations_scratch_db.py` (migrate scratch DB, hợp lệ nhưng 1 phút); 9,2 s `users/tests/test_users_list_queries.py::test_query_count_does_not_grow_with_user_count`; 6,4–6,6 s `users/tests/test_command_guards.py` ×2 (chạy `seed_initial_data` thật); 6,0–6,7 s các test race thread `inventory/test_return_concurrency.py` ×3, `test_stock_command_service.py`, `callcenter/test_transcribe_drain.py`, `customers/test_report_query_guards.py` (statement timeout thật); còn lại **19/30 là `teardown` 5,5–6,1 s của `analytics/test_marts.py`, `test_refresh_service.py`, `test_reconcile.py`, `test_profile_api.py`, `test_coverage_api.py`, `foxia/test_foxia_order_isolation.py`** — chính là flush toàn bộ bảng sau mỗi test `transaction=True`.

---

## 1. Test trùng lặp / nên `parametrize`

Phương pháp: hash AST body sau khi chuẩn hoá tên biến/tham số và thay hằng bằng kiểu (script `scratchpad/analyze.py`). Kết quả: **8 nhóm body giống hệt (16 test)**, **134 nhóm giống sau chuẩn hoá (298 test)** — 108 nhóm **cùng file** (230 test, cắt được 122 định nghĩa nếu gộp `parametrize`; các thành viên thứ 2+ của nhóm chạy hết **143 ca = 79 s**) và 26 nhóm xuyên file (68 test). 66 tên test trùng ở ≥2 file.

Phân loại thẳng thắn: đa số nhóm cùng-file là **cùng hành vi, khác 1 tham số** → gộp `parametrize` giảm code, KHÔNG giảm số ca chạy. Nhóm xuyên-app (facebook_ads ↔ google_ads) là **chép code test cho 2 adapter khác nhau** — không thừa về coverage, thừa về bảo trì. Test thừa thật (xoá được) tập trung ở luật domain được test lại qua HTTP ở nhiều báo cáo.

### 1a. Nhóm trùng đáng gộp

| # | Mức | Nhóm | Bằng chứng | Đề xuất | Cắt |
|---|---|---|---|---|---|
| 1 | P1 | `estimate_returns_none` / `check_permission_denies|allows` / `file_name` / `registered` lặp cho từng export definition | `apps/inventory/tests/test_inventory_export_definitions.py:444` ≡ `:682` (**giống hệt**), `:448`≡`:686`, `:453`≡`:691`; cùng mẫu `audit/tests/test_audit_export_definition.py:46,57,64`, `warranty/tests/test_warranty_export_definition.py:55,73`, `warranty_codes/tests/test_batch_export_definition.py:36,60,67`, `orders/tests/test_orders_export_definition.py:118,135,142,151`, `callcenter/tests/test_call_quality_export_definition.py:230,239` | 1 test generic ở `apps/exports/tests/` duyệt `registry`, `parametrize(kind, quyền_thiếu, quyền_đủ, file_name)`; xoá bản chép từng app | ~25 định nghĩa → 1 |
| 2 | P1 | 6 class `*SectionTests` cùng 2 test `test_section_registered` + `test_sync_creates_permissions` | `apps/users/tests/test_rbac_sections.py:42-160` (`:43,:83,:123`, `:51,:94,:111,:132,:149` body giống nhau) | `parametrize("section,actions")` 2 hàm; `sync_permissions()` 1 lần/module | 12 → 2 |
| 3 | P2 | `test_nonexistent_order_is_404_not_403` ×5 chỉ khác URL | `apps/orders/tests/test_contact_record_scope.py:119,278,353`, `test_order_record_scope.py:161`, `apps/customers/tests/test_resale_tracking_record_scope.py:141` | parametrize `(method, url)` | 5 → 1 |
| 4 | P2 | `return_type → state` 4 test giống trừ hằng | `apps/inventory/tests/test_return_api.py:242,246,250,254` | parametrize | 4 → 1 |
| 5 | P2 | Redaction query-param 4 test cùng body | `apps/audit/tests/test_middleware_redaction.py:190,194,198,207` | parametrize `(query, expected)` | 4 → 1 |
| 6 | P2 | HR salaries RBAC 3 POST + 2 GET | `apps/hr/tests/test_hr_rbac_pii.py:30,37,50,61,72,84,90` | 2 hàm parametrize `(perms, status)` | 7 → 2 |
| 7 | P2 | resale contact/appointment/order_note: 3 cặp forbidden/ok | `apps/customers/tests/test_resale_api.py:94-160` | parametrize `(url, method, payload, can_edit, status)` | 6 → 1 |
| 8 | P2 | KOC slip COD theo `order_type` | `apps/orders/tests/test_order_print.py:633,637,641` | parametrize | 3 → 1 |
| 9 | P2 | Bulk print `too_many_ids` / `429` chép cho 2 endpoint | `apps/orders/tests/test_order_print.py:122,133,422,429` | parametrize endpoint | 4 → 2 |
| 10 | P2 | Landing/sheet3/orders channel-type parity (chung `_assert_filters`) | `apps/orders/tests/test_order_list_filters_api.py:728,754,766` | parametrize `(url, order_source)` | 3 → 1 |
| 11 | P2 | `test_port_registered_at_app_ready` ×5 | `apps/orders/tests/test_kiotviet_port_wiring.py:40,104` (+1), `apps/channels_core/tests/test_order_broadcast_port_wiring.py:23`, `apps/inventory/tests/test_kiotviet_delivery_port_wiring.py:25` | parametrize `(port_fn, module, adapter)` trong từng file | 5 → 3 |
| 12 | **P1** | Hệ số chi phí Facebook theo mốc 08/2026–09/2026 test lại ở **5 báo cáo qua HTTP** (0,85–1,2 s/test) | `apps/orders/tests/test_overview_report.py:198,219,239`, `test_ads_reports.py:557,571`, `apps/facebook_ads/tests/test_campaign_report_api.py:101,110`, `test_page_report_api.py:121,134`, `test_campaign_products_api.py:68`, `apps/daily_report/tests/test_ads.py:173`, `apps/ai_mart/tests/test_chi_phi_quang_cao.py:85` — nhưng **không có unit test trực tiếp** cho `facebook_ads_cost_multiplier()` (`apps/orders/reporting/shared.py:435`; grep chỉ thấy tên trong comment) | 1 unit test `SimpleTestCase` parametrize 4 mốc cho hàm gốc; mỗi báo cáo giữ đúng **1** test "báo cáo có áp hệ số" | ~11 ca HTTP → 4, ≈ 7 s |
| 13 | P2 | Bộ auth Hermes token chép 3 lần (5 test/bộ) | `apps/ai_mart/tests/test_hermes_chat_api.py:87-109`, `apps/callcenter/tests/test_hermes_transcript_api.py:51-74`, `apps/daily_report/tests/test_auth.py:17-40` (helper `daily_report/transport/auth.py:3` tự nhận là "bản sao có chủ đích") | 1 bộ test cho helper gốc + mỗi app 1 smoke "đi qua cửa" | 15 → 7, ≈ 3 s |
| 14 | P2 | facebook_ads ↔ google_ads: 12 test cùng tên/body ở `test_ad_account_scope.py`, +6 ở `test_campaign_report_api.py`/`test_campaign_products_api.py` | `apps/facebook_ads/tests/test_ad_account_scope.py:49,60,113,118,140` ≡ `apps/google_ads/tests/test_ad_account_scope.py:45,55,95,100,111` (hash strict); `test_campaign_report_api.py:241` ≡ google `:176`; `test_campaign_products_api.py:76` ≡ google `:59` | Không xoá (2 adapter thật). Base class/mixin nhận `adapter, make_account` | 0 ca, −~150 dòng |

### 1b. Nhóm cùng-file khác (P2, chỉ giảm code)

`apps/orders/tests/test_age_group.py:105-150` (đã có dict `cases` — parametrize thẳng), `test_ingest_api.py:267/279, 891/901, 1037/1044, 1080/1089, 1266/1276`, `test_order_item_discount.py:104/109`, `test_sale_reports.py:768-791` (4 bucket), `test_shift_service.py:127/131, 173/183`, `test_shipping_lookup.py:290/313, 339/348`, `test_backfill_landing_confirmed_amount.py:47/56/90`, `test_floating_pool.py:118/123, 388/395`, `apps/inventory/tests/test_inventory_export_definitions.py:618/819, 629/643, 656/805`, `test_flow_history_views.py:99/222, 110/265`, `test_stock_document_approval.py:185/234`, `apps/common/tests/test_throttling.py:46,52,57`, `apps/warranty_codes/tests/test_portal_track_api.py:120/129`, `apps/kiotviet/tests/test_returns_sync_service.py:244/271`. Đủ 134 nhóm: `scratchpad/analysis.txt` mục `DUP NORMALISED`.

### 1c. Ước lượng cắt

| Loại | Số test | Giây tiết kiệm (theo junit) |
|---|---|---|
| Gộp parametrize cùng file (108 nhóm) | −122 định nghĩa, 0 ca | ≈ 0 (chỉ bớt setUp lặp ở #2) |
| Luật domain test lặp qua HTTP (#12, #13) | −~15 ca | ≈ 10 s |
| Bản chép fb↔google (#14) | 0 ca | 0 |
| **Tổng cắt thật** | **~15 ca + ~140 định nghĩa** | **≈ 10 s / 3.400 s** |

Kết luận: bộ test **ít test thừa theo nghĩa "chạy 2 lần cùng một thứ"**. Thời gian không nằm ở test trùng mà ở §4 (transaction=True, HTTP-heavy setUp).

---

## 2. Test không assert / assert vô nghĩa / test framework

Quét AST: 33 hàm không `assert`/`raises`/`self.assert*`; 5 hàm chỉ `assert x is not None`. Sau khi đọc tay:

| Mức | file:line | Vấn đề | Đề xuất |
|---|---|---|---|
| P2 | `apps/exports/tests/test_metrics.py:15,21,25` | Chỉ gọi `.labels(...).inc()/observe()` — không assert; label **sai tên** không raise (Prometheus chỉ raise khi sai số lượng label) → test không khoá được thứ docstring nói | Assert `export_jobs_total._labelnames == ("kind","status")` hoặc đọc `REGISTRY.get_sample_value` |
| P2 | `apps/audit/tests/test_audit_export_definition.py:64`, `apps/callcenter/tests/test_call_quality_export_definition.py:239`, `apps/inventory/tests/test_inventory_export_definitions.py:136`, `apps/orders/tests/test_contacts_export_definition.py:194`, `test_orders_export_definition.py:135`, `apps/warranty/tests/test_warranty_export_definition.py:73`, `apps/warranty_codes/tests/test_batch_export_definition.py:67` | 7 test "allows" chỉ dựa vào "không raise" | Gộp vào test generic §1a #1 với `expect_raises: bool` |
| P2 | `apps/common/tests/test_settings_guards.py:89,93`, `apps/analytics/tests/test_reconcile.py:15,25,112`, `apps/common/tests/test_events.py:81`, `apps/ai_mart/tests/test_loaders.py:70`, `test_chat_source.py:96`, `apps/channels_core/tests/test_channel_model_api.py:350,368`, `apps/customers/tests/test_phone_unique.py:82`, `apps/facebook_ads/tests/test_campaign_catalog.py:81`, `apps/warranty/tests/test_migrations.py:6`, `apps/inventory/tests/test_db_schema.py:172`, `apps/orders/tests/test_dynamic_assessment.py:48,108`, `apps/media/tests/test_access_policy.py:11` | Kiểu "không raise = pass" (guard, migration check, system check). Hợp lệ, nhưng nếu hàm dưới đổi từ raise sang trả mã lỗi thì test vẫn xanh | Thêm 1 assert giá trị trả về nơi có (`call_command` → assert stdout) |
| — | `apps/orders/tests/test_order_list_filters_api.py:728,754,766`, `apps/catalog/tests/test_combo_lines.py:128`, `apps/inventory/tests/test_combo_expansion.py:75`, `apps/orders/tests/test_order_search_index_usage.py:328` | **Dương tính giả**: assert trong helper `_assert_filters` / `assertNumQueries` | — |
| P2 | `apps/analytics/tests/test_marts.py:285`, `apps/inventory/tests/test_return_entry_core.py:397`, `apps/foxia/tests/test_foxia_uid_and_tasks.py:104`, `apps/contact_flow/tests/test_operation_engine.py:159` | Chỉ `is not None` — có nghĩa nhưng không khoá giá trị | `test_marts.py:285` assert thêm `order_source IS NULL` được giữ |
| — | Test `__str__`, getter/setter, field-list copy | **Không tìm thấy** (grep 0). `apps/analytics/tests/test_profile_serializer_types.py:90,96` so field serializer với **cột mart thật** — contract test hợp lệ | — |

---

## 3. Skip / xfail / test không được collect

| Mức | Bằng chứng | Kết luận |
|---|---|---|
| — | `skip`/`xfail`: **1** — `apps/common/tests/test_settings_guards.py:223` `@unittest.skipIf(_TIMEOUT_MS == "0", ...)` có điều kiện và lý do. `pytest.skip()`/`skipTest`: 0. Test comment-out (`# def test_`): 0. Kết quả chạy: 0 skipped/xfail | Không có skip vĩnh viễn |
| — | File trong `tests/` không match pattern: 9 (`helpers.py`, `chat_helpers.py`, `_fakes.py`, `_factories.py`, `factories.py`) — không chứa `def test_` | Đúng chủ đích |
| P2 | Ngoài `testpaths`: `scripts/check_backend_file_lengths.py` (script lint); `apps/users/test_helpers.py` là helper test (0 test, `make_admin`… 580 lượt dùng) nhưng **nằm trong package prod** — được `COPY . /app` vào image | Dời sang `apps/users/tests/helpers.py` (sửa ~60 import) |
| — | Class có test nhưng tên không bắt đầu `Test`: script báo 69, tất cả kế thừa base `_Base(TestCase)` cùng file (vd `apps/orders/tests/test_contact_list.py:76-1244` 23 class con) → **đều được collect** (0 test rớt khi đối chiếu) | Không có vấn đề |
| P2 | 4 test đọc file **ngoài `backend/`** (`apps/ai_mart/tests/test_compose_env_whitelist.py:44,49`, `test_staging_compose_*`, `apps/daily_report/tests/test_openapi.py:32`) → đỏ khi chỉ mount `backend/` như mọi recipe trong `docs/superpowers/plans/*` | `skipIf(not path.exists(), "cần repo đầy đủ")` hoặc cập nhật recipe mount |

---

## 4. Test chậm không cần thiết

| Mức | Bằng chứng | Vấn đề | Đề xuất |
|---|---|---|---|
| **P1** | `apps/analytics/tests/test_marts.py` (35 `transaction=True`), `test_refresh_service.py` (16), `test_reconcile.py` (7), `test_coverage_api.py` (4), `test_profile_api.py` (3), `apps/foxia/tests/test_foxia_order_isolation.py:151,175`, `apps/ai_mart/tests/test_don_hang.py:141`, `test_triggers.py:18` — 79 test analytics = **381 s (11 % suite)**, teardown 5,5–6,1 s/test (19/30 mục chậm nhất) | Lý do thật (matview `REFRESH CONCURRENTLY` cần dữ liệu đã commit — docstring `test_refresh_service.py:23`, `test_don_hang.py:142`), nhưng mỗi test flush **toàn bộ bảng** | Gộp: 1 refresh → nhiều assert (module-scope fixture dữ liệu + `subtests`), giảm từ 79 xuống ~15 test `transaction=True`; ước tiết kiệm ~250 s |
| P1 | 64 class `TestCase` (**210 test**) không đụng DB/HTTP: `apps/audit/tests/test_middleware_redaction.py:24,69,98,165` (RequestFactory + mock), `apps/orders/tests/test_shift_service.py:44,60,96,114`, `apps/kiotviet/tests/test_import_create_only_targets.py:36,80,147,269,292,336`, `apps/orders/tests/test_ingest_api.py:260,286,1066`, `test_order_recall.py:36,194,399`, `apps/common/tests/test_ops.py:81,104,148`, `test_exception_handler.py:24`, `apps/hr/tests/test_attendance_export.py:26`, `apps/ai_mart/tests/test_chat_extract_contract.py:6`, `apps/customers/tests/test_customer_tasks.py:16,62`, `apps/callcenter/tests/test_call_insights.py:55`… (đủ 64: output "TestCase classes without DB signals") | Mỗi test mở transaction + savepoint | Đổi `SimpleTestCase` — cơ học; nếu thật sự chạm DB sẽ đỏ ngay |
| P1 | 126/412 file qua HTTP client, **1.162** lời gọi `client.*`; các file 2,2–3,0 s/test: `orders/tests/test_order_record_scope.py` (setUp `:69-83` gọi `sync_permissions()` + 3 `make_scoped_user` mỗi test), `test_order_bulk_action_permissions.py`, `test_contact_record_scope.py`, `test_sale_reports.py`, `users/tests/test_team_api.py`, `callcenter/tests/test_call_quality_api.py` | Setup nặng lặp mỗi test | `setUpTestData` cho user/role/channel (dữ liệu bất biến), giữ HTTP chỉ ở test contract |
| P2 | `apps/orders/tests/test_migrations_scratch_db.py` 3 test = 60 s (migrate scratch DB); `apps/users/tests/test_command_guards.py` 2 test 6,4 s (chạy `seed_initial_data` thật); `apps/users/tests/test_users_list_queries.py` 9,2 s | Hợp lệ nhưng đắt | Đánh dấu `@pytest.mark.slow`, chạy trong CI đầy đủ, bỏ qua ở vòng local nhanh |
| P2 | `apps/inventory/tests/test_return_concurrency.py:84,96` `time.sleep(1.0)` + `sleep(0.2)` (test 6,5 s) | Race 2 thread thật | Thay `sleep(1.0)` bằng `threading.Event` chiều ngược (đã có `entry_created`) |
| P2 | `apps/dispatch/tests/test_dispatch.py:152-155` poll `range(200)×sleep(0.01)` | Có trần 2 s, thực đo 7 ms | Giữ |
| P2 | `TransactionTestCase` cho thread/timeout/partition: `apps/customers/tests/test_report_query_guards.py:17`, `apps/callcenter/tests/test_transcribe_drain.py:199`, `apps/inventory/tests/test_db_schema.py:21,35,44,71`, `test_stock_command_service.py:75`, `test_occurred_at.py:376`, `apps/orders/tests/test_order_recall.py:263,323` | Có lý do (docstring) | Giữ; `test_db_schema.py:72` `reset_sequences=True` không cần cho 3 test đọc catalog |
| — | `range(100+)`: `apps/users/tests/test_admin_api.py:240` (bulk_create 120 user 1 lần/class), `apps/kiotviet/tests/test_sync_service_check_purge.py:224,233` (dict thuần), `test_import_kiotviet_invoices_command.py:77,83`, `apps/warranty_codes/tests/test_serial.py:13` | Không phải vấn đề | — |
| — | Gọi mạng thật: **0** | — | — |

---

## 5. Mock quá mức / patch sai đường

- Kiểm tĩnh **472** đích `patch("apps....")`: mọi đích tồn tại. 46 cảnh báo của script đều là dương tính giả (`import *` ở `apps/orders/services/__init__.py:9`, `__getattr__` lazy ở `apps/dispatch/__init__.py:5`, patch sub-module như `apps.kiotviet.infrastructure.services.invoice_mutations_push`). Không có patch lệch module.
- **76** chỗ `assert_called()`/`assert_called_once()` không kiểm tham số (đủ danh sách: `scratchpad/analysis.txt` mục `assert_called_noargs`). Đáng sửa (P2) — nơi tham số là nội dung nghiệp vụ:
  - `apps/kiotviet/tests/test_invoice_webhook.py:146,263,337,371,477,480,650` — `push_event`/`get_invoice_by_code` không kiểm mã hoá đơn/loại event.
  - `apps/kiotviet/tests/test_access_token.py:187,193,317,324,386,389,397` — `create_order`/`find_customer_by_phone` không kiểm payload.
  - `apps/inventory/tests/test_return_order_sync.py:59,133,219,243,262` — `push.assert_called_once()` không kiểm order/return id.
  - `apps/orders/tests/test_dispatch_cancelled_guard.py:208,308,336,358` — `verify.assert_called_once()` không kiểm mã đơn.
  - `apps/inventory/tests/test_awaiting_return.py:783,852,922,939` — alert `send` không kiểm nội dung (`:848` đã có test nội dung riêng → chấp nhận một phần).
  Đề xuất: `assert_called_once_with(...)` hoặc `call_args.kwargs[...]`.
- Mock "chính thứ đang test": không thấy trường hợp rõ. Gần nhất `apps/customers/tests/test_resale_api.py:94-160` patch `OrderService.can_edit_channel` rồi kiểm 403/200 — hợp lệ (test wiring view→scope; `can_edit_channel` có test riêng ở orders).

---

## 6. Lỗ hổng coverage (ánh xạ tĩnh — không có pytest-cov)

Phương pháp: 137 module ≥200 dòng ở tầng `application/services`, `transport/views`, `reporting`, `domain`, `infrastructure/services`, `tasks`, `management/commands`; đối chiếu (a) import/patch trực tiếp trong test, (b) tên hàm/class public xuất hiện trong test, (c) route `urls.py` của view xuất hiện trong test. "Không có test" = cả 3 trống.

**Không có test nào (P1):**

| LOC | Module | Ghi chú |
|---|---|---|
| 620 | `apps/kiotviet/infrastructure/services/revenue_report_service.py` | Parse HTML báo cáo doanh thu KiotViet (map cột theo `left:` px) — logic dễ vỡ nhất; gọi từ `apps/kiotviet/tasks.py:17` và command `sync_kiotviet_daily_revenue` (push Lark). **0 test**, kể cả `parse_page_content` thuần |
| 474 | `apps/orders/management/commands/import_assessment_csv.py` | Ghi đè `OrderAssessment` theo shipping_code/phone, có `--overwrite` — 0 test |
| 330 | `apps/orders/domain/address/address_catalog_impl/catalog_core.py` | Chỉ gián tiếp qua HTTP `test_address_lookup.py`/`test_parse_address_permission.py`; không unit test `AddressCatalog` |
| 301 | `apps/orders/reporting/delivery_quality.py` | Chỉ 3 assert gián tiếp ở `test_dashboard_summary_api.py:478,520,712`; công thức "% bom", hoàn theo SP không có test riêng |
| 254 + 252 | `apps/orders/application/services/sale_reminder_service.py`, `apps/orders/infrastructure/sale_reminder_templates.py` | Lịch nhắc D+2/D+4/D+7 theo `sales_status_code` — chỉ chạm ở `apps/customers/tests/test_resale_api.py:173`, `test_dashboard_summary_api.py:871`; bảng chiến lược trong docstring không được test |
| 215 | `apps/foxia/transport/api_views.py` | Chỉ `link-config` có test; `reports/marketing`, `channel-maps`, `channel-maps/upsert`, `sync-status` (`apps/foxia/urls.py:17-20`) — 0 test |
| 477 + 274 | `apps/kiotviet/infrastructure/services/_import_service_upsert_mixin.py`, `_sync_service_sync_mixin.py` | Gián tiếp qua test `ImportService`/`SyncService` (6 file mỗi loại) — chấp nhận; nhưng `kiotviet` 19,1k LOC / 26 file test / **18 s** chạy = phủ mỏng nhất so với kích thước |

Chỉ **1** file test tham chiếu (P2): `apps/kiotviet/management/commands/_sync_invoices_legacy.py` (**2.835 LOC**), `_sync_service_check_mixin.py` (939), `apps/channels_core/transport/serializers/channel_serializers_impl.py` (775), `apps/foxia/application/services/order_sync_service.py` (584), `apps/pancake/transport/views.py` (470 — và đang có view `retry_order` lọt guard quyền), `apps/cnv_loyalty/application/services/inbound_service.py` (415), `apps/orders/transport/views/shipping_lookup_view.py` (412), `apps/ai_mart/infrastructure/chat_extract_runner.py` (405), `apps/facebook_ads/infrastructure/lark_ads_report_sync.py` (403).

Theo app (LOC tầng service/view không tham chiếu trực tiếp / tổng): callcenter 822/1.041 (78 %), users 591/914 (64 %), customers 292/517 (56 %), hr 425/928 (45 %), orders 8.149/21.304 (38 % — phần lớn phủ qua HTTP). Đây là "không import trực tiếp", không phải coverage dòng.

Đề xuất P1: thêm `pytest-cov` vào `backend/requirements-dev.txt`, `--cov=apps --cov-report=xml` ở job `backend:test` (+~30 % thời gian) — biến bảng trên thành số thật.

---

## 7. Test kiến trúc (AST guard) hiện có

| File | Test | Quét gì | Hiệu lực |
|---|---|---|---|
| `apps/orders/tests/test_record_scope_coverage.py` | 8 | AST mọi view nhận `order_id`/`ids` phải qua record-scope | **Có**, xanh |
| `apps/users/tests/test_write_permission_coverage.py` | 1 | View method ghi chỉ `require_section` → đỏ (audit 08-19 N2) | **Có và đang ĐỎ trên master** (3 offender §0); whitelist cứng dòng 34-42 cần dọn |
| `apps/orders/tests/test_order_field_catalog.py` + `test_crm_field_catalog.py` | 1 + 4 | Catalog quyền phủ đủ field serializer/CRM | **Có và đang ĐỎ** (`address_level2`) |
| `apps/users/tests/test_serializer_orm_write_guard.py` | 1 | `rglob` serializer không được có `transaction.atomic`/`bulk_create` | Có, xanh |
| `apps/common/tests/test_fk_layering_guard.py` | 1 | FK string-ref vượt tầng, đọc `.importlinter` làm nguồn sự thật | Có, xanh |
| `apps/orders/tests/test_rbac_scope_aware_sections.py` | 1 | Khoá `SCOPE_AWARE_SECTIONS` của command `audit_rbac_scope` | Có |
| `apps/orders/tests/test_kiotviet_port_wiring.py`, `apps/channels_core/tests/test_order_broadcast_port_wiring.py`, `apps/inventory/tests/test_kiotviet_delivery_port_wiring.py` | 13+ | Adapter đăng ký đúng port lúc `ready()` | Có (so tên module, tôn trọng importlinter) |
| `apps/ai_mart/tests/test_compose_env_whitelist.py`, `apps/daily_report/tests/test_openapi.py` | 3 + 1 | Contract compose env / OpenAPI committed | Có nhưng phụ thuộc repo đầy đủ (§3) |
| Chưa có guard cho: queue Celery (task→queue), `cache.clear()`/`time.sleep`/`requests` trong test | | | P2: thêm guard `cache.clear()` (§8) |

Vận hành: `scripts/check-backend-arch.sh` chỉ chạy `lint-imports` + `ruff`; AST guard chỉ chạy khi pytest chạy → nếu pipeline GitLab chưa có Runner (`.gitlab-ci.yml:14-16`) thì 2 guard đỏ trên là bằng chứng chúng không được chạy tự động.

---

## 8. Flaky (thời gian thật, thứ tự, random, state dùng chung)

| Mức | Bằng chứng | Vấn đề | Đề xuất |
|---|---|---|---|
| **P1** | `cache.clear()` thật ở **40 chỗ / 15 file**: `apps/orders/tests/test_dashboard_summary_api.py` (7: `:38,:172,:181`…), `test_order_list_contract.py` (6), `test_ads_reports.py` (6), `test_contact_list.py` (5), `test_sale_reports.py` (2), `apps/media/tests/test_read_views.py:8` + `test_write_views.py:11` + `test_excel_views.py:27` (autouse `_clear_cache` chép 3 lần), `apps/kiotviet/tests/test_kship_service_extra_auto_add.py:22-27` (autouse), `apps/daily_report/tests/test_ads.py`, `test_definitions_api.py`, `test_payload_shapes.py`, `apps/channels_core/tests/test_channel_scope_api.py`, `apps/callcenter/tests/test_zenify_client.py`, `test_export_call_transcript.py` | Redis **thật, dùng chung** (theo `backend/conftest.py:7` và docs "không bao giờ gọi `cache.clear()`" — `docs/superpowers/plans/2026-07-24-*.md:34`; comment `apps/kiotviet/tests/test_returns_sync_entrypoints.py:7`, `apps/customers/tests/test_resale_tracking_record_scope.py:57`). `RedisCache.clear()` = FLUSHDB xoá lock/counter/throttle của test khác (và worktree khác) | Fixture `isolated_cache` (LocMem qua `settings.CACHES`) — đã có nhưng **định nghĩa 2 lần** ở `apps/common/tests/test_rate_limiter.py:17` & `test_throttling.py:147` → đưa lên `backend/conftest.py`, thay 40 chỗ; AST guard cấm `cache.clear()` trong `apps/*/tests` |
| P1 | `event_bus.clear()` ở 8 file (`apps/common/tests/test_events.py`, `apps/orders/tests/test_status_journey_subscriber.py`, `test_direct_sale_shipping_type.py`, `apps/kiotviet/tests/test_invoice_webhook.py`, `apps/inventory/tests/test_stock_reissue_on_revive.py`, `test_cancel_return_semantics.py`, `apps/hr/tests/test_employee_service.py`, `apps/best_express/tests/test_webhook_service.py`) xoá handler production → vá bằng **3 autouse fixture chép nhau** `_ensure_hr/cnv/warranty_subscribers` (`apps/hr/tests/conftest.py:5`, `apps/cnv_loyalty/tests/conftest.py:9`, `apps/warranty/tests/conftest.py:7`); app chưa có fixture (orders, inventory, kiotviet…) vẫn **phụ thuộc thứ tự chạy** | Chưa lộ vì chạy theo alphabet | `event_bus.isolated()` (snapshot/restore) hoặc 1 autouse ở `backend/conftest.py` re-register mọi app; xoá 3 bản chép |
| P2 | `apps/inventory/tests/test_db_schema.py:96` dùng `timezone.now()` thật, assert partition tháng hiện tại + tháng sau tồn tại | Đỏ ở giao tháng nếu partition chỉ do task `ensure_stock_movements_partition` (`apps/inventory/tasks.py:35`) tạo | Gọi `PartitionService.ensure()` cho mốc cố định trong test / patch `timezone.now` |
| P2 | 387 chỗ `timezone.now()`/`localdate()`/`today()` trong test (nhiều nhất `apps/orders/tests/test_lead_assignment.py` 33, `test_contact_list.py` 16, `apps/contact_flow/tests/test_operation_engine.py` 15); không có `freezegun`/`time-machine` | Đa số tương đối (`now() - timedelta`) → an toàn; rủi ro ở test cửa sổ "hôm nay/tháng này" (`daily_report` mặc định "hôm qua", `test_shift_service.py` theo ca, 1.861 warning naive datetime ở `CallRecord.start_time`/`AuditLog.timestamp`) | Patch `django.utils.timezone.now` bằng `unittest.mock` ở test cửa sổ ngày; sửa naive datetime trong fixture callcenter/audit |
| P2 | `apps/daily_report/tests/test_auth.py:82` `random.randint` sinh IP né throttle | Chủ đích, không ảnh hưởng kết quả | Giữ + comment |
| P2 | `apps/warranty_codes/tests/test_portal_track_api.py:11` `order_factory` dùng `get_or_create(name="Test")` — dùng chung Channel giữa các lần gọi (khác 3 factory kia) | Có thể che lỗi scope theo kênh | Đồng bộ với `apps/orders/tests/conftest.py:91` |
| — | `backend/conftest.py:33` `_isolate_hardcoded_channel_ids`, `:7` `_disable_default_throttles` | 2 lớp flaky "tích luỹ theo suite" đã vá đúng chỗ, docstring rất tốt | Giữ; nợ gốc hard-code id kênh cần MR riêng như docstring nói |

---

## 9. conftest / fixture / factory

| Mức | Bằng chứng | Vấn đề | Đề xuất |
|---|---|---|---|
| P1 | `order_factory` định nghĩa **4 lần**: `apps/orders/tests/conftest.py:91`, `apps/cnv_loyalty/tests/conftest.py:38`, `apps/warranty/tests/conftest.py:21`, `apps/warranty_codes/tests/test_portal_track_api.py:11` — 3 bản gần giống, bản 4 khác hành vi; 162 lượt dùng | 4 nguồn sự thật cho "Order hợp lệ tối thiểu" | 1 fixture ở `backend/conftest.py` với tham số `prefix` |
| P1 | `apps/inventory/tests/` **không có `conftest.py`** dù 47 file / 814 test; fixture `warehouse` định nghĩa lại **12 lần**, `product` 7, `channel` 5, `setup` 3, `order` 3, `actor` 3, `staff_user` 2 (vd `test_pending_scan_core.py:36`, `test_return_entry_core.py:379`, `test_stock_as_of.py:63`, `test_stock_query.py:51`, `test_pending_scan_match.py:48`) | Lặp, mỗi bản lệch chút | Tạo `apps/inventory/tests/conftest.py` |
| P2 | Fixture trùng tên khác file: `fake_client` (media conftest + 3 file best_express/cnv), `api` (media conftest + 2 file inventory), `client` 5 file, `channel` 8, `product` 8, `customer` 5, `isolated_cache` 2, `_clear_cache` 3 | Shadowing — pytest lấy bản gần nhất, khó đọc | Hợp nhất lên conftest app; đổi tên fixture đặc thù |
| — | Fixture không dùng: **0** (`wc_user` chỉ 1 lượt) | — | — |
| P2 | factory_boy **không có**; "factory" là hàm tay (`apps/hr/tests/factories.py` 17 dòng, `apps/best_express/tests/_factories.py`), helper `make_order/make_customer` ở `apps/analytics|inventory|orders/tests/helpers.py`; đồng thời **31** hàm `make_/create_/build_order|customer|product|channel|user` rải trong 20+ file test và **3.200** lời gọi `.objects.create(` trong test | Không nhất quán helper ↔ tạo tay ↔ fixture | Không cần factory_boy; chuẩn 1 helper/app + conftest; review chặn `def make_order` mới trong file test |
| — | `backend/conftest.py`: 3 fixture, docstring tốt | Ổn | — |

---

## Tóm tắt

| Chỉ số | Giá trị |
|---|---|
| Tổng test | 5.598 collect (5.311 định nghĩa, 412 file), 0 skip/xfail, 0 không collect |
| Kết quả / thời gian | 5.592 pass / 6 fail (4 do mount, **2 guard đỏ thật trên master**) / 56 phút 37 s dưới tải nặng (baseline audit 08-19: 19 phút) |
| Test thừa ước tính | ~15 ca xoá được (≈10 s) + ~140 định nghĩa gộp `parametrize`; 210 test `TestCase` → `SimpleTestCase`; 79 test `transaction=True` (381 s, 11 %) gộp còn ~15 |
| Module quan trọng không test | 6 module / 2.190 LOC không có test nào; 9 module chỉ 1 file tham chiếu; không có coverage số (thiếu pytest-cov) |
| Flaky tiềm ẩn | `cache.clear()` 40 chỗ trên Redis dùng chung; `event_bus.clear()` 8 file phụ thuộc thứ tự; 1 test theo tháng thật; 1.861 warning naive datetime |

**Top 5 việc**

1. **(P0, ngay)** Sửa 2 guard đang đỏ trên master: thêm `address_level2` vào catalog quyền (`test_order_field_catalog.py:28`); gate `pancake/transport/views.py:451 retry_order` bằng `:edit`, whitelist kèm lý do cho `parse_address_level2`/`lookup_raw_address` (`order_address_view.py:129,166`). Xác nhận GitLab Runner có chạy `backend:test` — guard đỏ mà không ai thấy nghĩa là CI đang không chạy.
2. **(P1, 1 ngày)** `isolated_cache` (LocMem) ở `backend/conftest.py` thay 40 `cache.clear()` + AST guard cấm `cache.clear()`; 1 autouse re-register subscriber/`event_bus.isolated()` thay 3 bản chép. Hết 2 lớp flaky "state dùng chung".
3. **(P1, 1 ngày)** Gộp 79 test `transaction=True` ở analytics/foxia/ai_mart thành ~15 (1 refresh → nhiều assert) → −~250 s (−7 %); `setUpTestData` cho user/role ở `test_order_record_scope.py`, `test_order_bulk_action_permissions.py`, `test_sale_reports.py`, `test_team_api.py`; mark `slow` cho `test_migrations_scratch_db.py`, `test_command_guards.py`.
4. **(P1, 0,5 ngày + viết test)** Thêm `pytest-cov` vào `requirements-dev.txt` + `--cov=apps` ở CI; viết test cho `revenue_report_service.parse_page_content` (thuần, rẻ), `import_assessment_csv` (ghi đè dữ liệu prod), `sale_reminder_service` (bảng lịch nhắc), 4 route foxia chưa test.
5. **(P2, cơ học)** 64 class `TestCase` → `SimpleTestCase` (210 test); `parametrize` 108 nhóm cùng file (ưu tiên `test_rbac_sections.py`, export-definition tests → 1 test generic ở `apps/exports`, `test_return_api.py`, `test_middleware_redaction.py`); unit test trực tiếp `facebook_ads_cost_multiplier()` rồi bỏ ~11 test HTTP lặp ở 5 báo cáo; 1 `order_factory` + `apps/inventory/tests/conftest.py`; dời `apps/users/test_helpers.py` vào `tests/`.

Tệp phụ trợ (scratchpad): `collect.txt` (5.598 id), `full.txt` (log chạy + 30 slowest + traceback 6 fail), `junit.xml`, `analysis.txt` (134 nhóm trùng / no-assert / assert_called / realtime), `coverage_map.txt`, `fixtures.txt`, `mock_targets.txt`, `analyze.py`, `junit_stats.py`.
