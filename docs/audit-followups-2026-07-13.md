# Audit followups 2026-07-13 — Plan & nhật ký

Xử lý toàn bộ findings của đợt audit 2026-07-13 (4 mảng, scope = code merge sau 06/07).
Branch: `fix/audit-followups-2026-07-13`. Mỗi task 1 nhóm commit, test kèm theo.

Quy ước chung (Global Constraints):
- Không đổi contract API trừ khi task ghi rõ; nếu đổi serializer → regen `schema.yaml` + FE `npm run gen:api` + `npx prettier --write src/api/schema.d.ts`.
- BE test chạy qua image `desi-order-web-test` với `-e POSTGRES_DB=desi_<unique>` + `-e REDIS_PASSWORD=testpassword`; FE test = `npx vitest run <file>`.
- Commit message tiếng Anh, ngắn, không Co-Authored-By.
- Code mới bám convention repo: FE dùng React Query + typed schema, không react-select; BE logic ở service/repository, view mỏng.
- KHÔNG tạo branch mới, KHÔNG push — làm việc ngay trên worktree/branch hiện tại.

## Task 1 — BE orders/kiotviet correctness

1.1 (CAO) Guard "đã xuất kho" đếm SALE bỏ SALE_REVERSAL — `backend/apps/orders/application/services/order_service_commands.py:693-699`: `already` phải tính theo NET (tổng qty SALE + SALE_REVERSAL còn outstanding > 0 → mới coi là đã xuất). Khi net = 0 phải đi tiếp round-trip verify KiotViet thay vì UNVERIFIABLE. Áp cùng logic cho `_dispatched_order_ids` ở `backend/apps/orders/transport/views/shipping_lookup_view.py:206-216` (hiển thị `already_dispatched`). Test: đơn có SALE + SALE_REVERSAL (net 0) → không bị coi là already_dispatched, verify vẫn chạy.

1.2 (TRUNG) Lost-update `return_slip_code` — `backend/apps/orders/application/services/order_service_helpers.py:626-652` `_upsert_order_recall`: (a) bọc `select_for_update()` khi update recall hiện có; (b) chỉ ghi `return_slip_code` khi key có mặt tường minh trong payload (tránh full-form stale đè); giữ nguyên hành vi các field khác. Ghi chú semantics: xoá tay sẽ thắng cho tới khi KV return có lastModified trong cửa sổ sync (union nối lại) — chấp nhận, ghi vào docs/erp-kiotviet-returns-sync.md.

1.3 (THẤP) `int(invoice_id)` không guard per-item — `backend/apps/kiotviet/infrastructure/services/returns_sync_service.py:187`: bắt ValueError/TypeError per-item, đếm vào counter skip, không abort cả kênh. Test: item có invoiceId rác → các item sau vẫn xử lý.

1.4 (THẤP) Hợp nhất 2 bản `_kiotviet_request` — `backend/apps/kiotviet/.../common_auth_customer.py:110` và `gateway_kship_customer_webhook.py:4` hiện identical: đưa về 1 module chung, cả 2 nơi import.

1.5 (THẤP) Thứ tự khoá ngược post-return vs dispatch — thống nhất: lấy `Order.select_for_update()` TRƯỚC advisory 61001 ở `backend/apps/inventory/application/services/return_entry_service.py` (khớp thứ tự bên dispatch `order_service_commands.py:515` → `issue_for_order`). Kiểm tra lại mọi caller để không tạo chu trình mới.

1.6 (THẤP) `single_flight` owner-token — `backend/apps/common/db_guards.py:47-61`: set giá trị token ngẫu nhiên khi `cache.add`, chỉ delete khi token khớp.

1.7 (THẤP) Comment celery lỗi thời — `backend/config/settings_parts/celery.py:123-125`: sửa mô tả theo policy union.

1.8 (THẤP) Comment guard chống race partial-unique — `return_entry_service.py` quanh `_guard_double_credit`: thêm comment ngắn "constraint chỉ phủ draft; guard này là tầng chặn thật khi 2 tx cùng create_and_post" để không ai gỡ nhầm.

## Task 2 — BE security/RBAC

2.1 (CAO) Permission `salaries:view` — thêm section `salaries` (view) vào registry permission (`backend/apps/users/permissions.py` + data migration seed: KHÔNG tự gán cho role nào ngoài admin); gate GET lương + hợp đồng ở `backend/apps/hr/transport/views/contract_salary_views.py:48,92` bằng `salaries:view` (POST giữ `employees:edit`). FE: nếu trang admin permission render từ danh sách BE thì tự có; kiểm tra và thêm label i18n nếu cần.

2.2 (TRUNG) Leave tự duyệt — `backend/apps/hr/application/services/leave_service.py:24-33` `approve/reject`: resolve employee của approver qua EmployeeAccount, nếu trùng `lr.employee_id` → raise LeaveRuleError("Không thể tự duyệt đơn của chính mình"). Test cả approve lẫn reject.

2.3 (TRUNG) Redact query_params trong audit — `backend/apps/audit/middleware_impl.py:179-201`: chạy từng key qua `_is_sensitive_key`, thêm `phone` vào danh sách nhạy cảm với dạng mask giữ 3 số cuối (đừng che trắng — vẫn cần đối soát). Test: `?phone=0912345678` → lưu `*******678`.

2.4 (TRUNG) `flow_manual_match` gate theo kind — `backend/apps/inventory/transport/views/flow_history_views.py:624-642`: kind dispatch → yêu cầu thêm section `shipments`; kind return → thêm `returns` (giữ `stock` cho phần đọc). Test 403 cho user chỉ có `stock`.

2.5 (TRUNG) `raw_address` theo field-policy của `address` — `backend/apps/users/field_policy.py:15-22`: thêm cơ chế alias-group (ẩn `address` ⇒ ẩn `raw_address`); giữ tổng quát để thêm alias sau (vd phone). Test policy ẩn address → response không còn raw_address.

2.6 (THẤP) Mask phone ở `flow_order_lookup` — `flow_history_views.py:579-605`: trả phone mask giữ 3 số cuối (khớp đơn không cần full số). Đồng thời fix layering L6: expose method public trên OrderRepository (vd `search_for_lookup(q, limit)`) thay vì gọi `_base_orders_queryset`/`_apply_list_filters` private. FE nếu hiển thị phone chỗ khớp đơn thì không cần đổi (nhận string đã mask).

2.7 (TRUNG) FB batch sub-error — `backend/apps/facebook_ads/services.py:278-282`: ghi `results["_errors"][acc.id] = err_msg`, log WARNING kèm account id; nơi tổng hợp daily spend đọc `_errors` để log/đếm account lỗi (tối thiểu: log tổng số account fail mỗi lần sync để lộ ra ở log/Sentry). Xoá dead code. Test: sub_code != 200 → không nuốt.

## Task 3 — BE retention/bounds

3.1 (TRUNG) Purge selfie chấm công — thêm delete vào `backend/apps/hr/infrastructure/photo_storage.py`; celery task daily purge ảnh cũ hơn `ATTENDANCE_PHOTO_RETENTION_DAYS` (env, default 180): xoá object MinIO + clear `photo_key` trên AttendanceEvent (giữ event). Đăng ký beat. Test service với storage fake.

3.2 (TRUNG) Bounds cho pending scans — `backend/apps/inventory/application/services/pending_scan_service.py:69-157` `list_unmatched`: nếu thiếu date range → default 30 ngày gần nhất (BE-side); thêm LIMIT cứng 1000 (log khi chạm). Purge: task daily xoá PendingShipmentScan status MATCHED cũ hơn 90 ngày (env override), theo pattern purge audit_logs. Test default range + cap.

## Task 4 — DB/perf migrations + status_counts

4.1 (TRUNG) Index cho returns sync — migration orders: partial index `(kiotviet_channel, invoice_id) WHERE invoice_id IS NOT NULL AND deleted_at IS NULL` (đặt tên rõ, có lock_timeout pattern như 0101). PHẢI land trước khi chạy backfill returns.

4.2 (TRUNG) `status_counts_for` — `backend/apps/orders/repositories.py:805-826`: gộp lifecycle + cancelled vào 1 query `aggregate(Count(filter=...))` trên base queryset (giảm 2 vật hoá → 1). Thêm partial index `invoice_status WHERE invoice_status = 2` (bảng orders). Đồng thời sửa semantics đếm để khớp Task 7 (chips): counts phải áp DÙNG các filter panel trừ đúng 2 trục status/invoice_status như hiện tại — giữ contract response `status_counts` không đổi shape.

4.3 (THẤP) CheckConstraint `quantity >= 1` trên ReturnNoteLine + filter `quantity > 0` ở auto-fill `backend/apps/inventory/application/services/return_entry_edit_service.py:56-72`. Migration kèm data-fix xoá line qty=0 nếu có (safe: chỉ draft).

4.4 (THẤP) Reverse SQL migration 0101 — `backend/apps/orders/migrations/0101_...py:39-45`: thêm `SET LOCAL lock_timeout` vào REVERSE_SQL cho đồng bộ pattern.

### Deploy notes (Task 4 — chạy tay trên prod, không tự động)

a. **TRƯỚC khi migrate inventory `0023_returnnoteline_quantity_positive`** — kiểm tra không có dòng qty=0 ngoài draft (migration chỉ data-fix dòng thuộc entry draft; nếu còn dòng qty=0 ở entry posted/cancelled thì `ADD CONSTRAINT` fail loudly theo thiết kế, cần review tay các dòng đó trước):

   ```sql
   SELECT count(*) FROM stock_return_note_lines l
   JOIN stock_return_entries e ON e.id = l.entry_id
   WHERE l.quantity = 0 AND e.status != 'draft';
   -- Phải = 0. Nếu > 0: xem từng dòng, quyết định xoá/sửa tay rồi mới migrate.
   ```

b. **SAU khi migrate orders `0103`/`0104` (CREATE INDEX CONCURRENTLY; renumber từ 0102/0103 sau khi merge master có `0102_kship_sender_address`)** — verify index hợp lệ. CONCURRENTLY bị ngắt giữa chừng để lại index INVALID, và `IF NOT EXISTS` ở lần chạy lại sẽ **âm thầm bỏ qua** index hỏng đó:

   ```sql
   SELECT c.relname, i.indisvalid
   FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
   WHERE c.relname IN ('ord_kv_channel_invoice_idx', 'ord_inv_status_cancel_idx');
   -- Cả 2 dòng phải indisvalid = true. Nếu false:
   -- DROP INDEX CONCURRENTLY <tên>; rồi chạy lại CREATE INDEX CONCURRENTLY (SQL trong migration).
   ```

c. **Thứ tự bắt buộc**: migration `0103` (index `(kiotviet_channel_id, invoice_id)`) phải land + valid TRƯỚC khi chạy backfill returns KiotViet — backfill lookup theo cặp cột này trên toàn bảng orders, thiếu index sẽ seq-scan per-invoice.

## Task 5 — BE warranty + Excel

5.1 (TRUNG) `mark_packed_by_tracking` — `backend/apps/warranty/journey_service.py:78-88`: dùng `_STATUS_RANK` — chỉ nâng status khi rank(PACKED) > rank(hiện tại); bỏ exclude-list tay. Test: journey RETURNED/PENDING_RETURN không bị hạ về PACKED.

5.2 (THẤP) Excel formula escape — hàm `_clean` chung: nếu string bắt đầu `=`, `+`, `-`, `@` → prefix `'`. Áp cho `backend/apps/inventory/infrastructure/flow_history_excel.py`, `backend/apps/warranty/claim_excel.py`, `backend/apps/orders/export_excel.py` (kiểm tra tên file thật). Cẩn thận số âm hợp lệ: chỉ escape khi giá trị là str, không đụng số.

5.3 (THẤP) Lệch web/Excel "Hết hạn" — `claim_excel.py:65`: khi days_left <= 0 xuất chuỗi "Hết hạn" thay vì số, khớp web (`warranty-claim-columns.ts:93-96`).

## Task 6 — FE inventory

6.1 (CAO) Autosave ghi chú nuốt lỗi — `frontend/src/features/inventory/components/flow-shared.tsx:149-154`: chỉ set `savedRef` trong `onSuccess`; thêm `onError` (toast + giữ savedRef cũ để blur lại retry được) cho `useUpdateReturnNote` (`return-history-queries.ts:92-107`) và `useFlowUpdatePendingNote` (`flow-match-queries.ts:66-76`).

6.2 (TRUNG) ScanDispatchDialog passthrough lỗi — `scan/ScanDispatchDialog.tsx:228-230,244-246`: thay `catch {}` bằng `getApiErrorMessage(err, ...)` + toast nguyên văn (policy passthrough 100% lỗi KiotViet).

6.3 (TRUNG) Nút Xuất Excel silent fail — `components/flow-shared.tsx:24-31`: thêm catch → toast lỗi.

6.4 (TRUNG) Xóa/Mã sai không confirm + không onError — `flow-shared.tsx:105-111` + `flow-match-queries.ts:56-63,79-86`: confirm như PendingScansDialog + onError toast.

6.5 (TRUNG) CreateReturnDialog race 2 lượt quét — `pages/CreateReturnDialog.tsx:190-241`: sequence token/inFlight — chỉ nhận response của mã đang hiển thị (theo mẫu 2 dialog batch).

6.6 (TRUNG) Debounce tìm kiếm Lịch sử nhập xuất — `pages/DispatchHistoryPage.tsx`: dùng `useDebouncedValue` cho product/order (mẫu StockDocumentsPage:83); bỏ remount `key={filtersKey}` nếu được — thay bằng reset offset có chủ đích khi filter đổi (giữ hành vi reset trang, tránh mất state bung dòng).

6.7 (THẤP) PendingScansDialog thiếu nhánh loading — `scan/PendingScansDialog.tsx:114-119`: LogoSpinner khi isLoading.

6.8 (THẤP) Toast tổng kết quét luôn xanh — `scan/ScanReturnDialog.tsx:252` + `scan-return-result.ts:21-30`: failedCount > 0 → `toast.warning`/`error`.

6.9 (THẤP) Query keys phân mảnh — chuẩn hoá invalidate chéo: `useDispatchBatch` invalidate pending keys; `useCreateReturn` invalidate `return-history`; hợp key pending-scans nếu rẻ.

6.10 (THẤP) `returns-types.ts` tự khai tay — re-export từ `components["schemas"]` theo mẫu `lib/api/pending-scans.ts:9-13`; bỏ cast `as Promise<T>` trong `returns-queries.ts`.

6.11 (THẤP) A11y row bung/thu — `IssueTab.tsx:86-90`, `ReturnTab.tsx:86-90`, `ReturnNoteDetailDialog.tsx:177-179`: tabIndex=0 + onKeyDown Enter/Space + aria-expanded.

6.12 (THẤP) pushRow snapshot cũ — `ScanDispatchDialog.tsx:82-104`: check trùng bên trong functional updater.

## Task 7 — FE orders/ads

7.1 (TRUNG) Chips lệch số (3 bug) — ràng buộc: số trên chip = số dòng sau khi click chip với panel filter hiện tại.
  a. Counts đứng yên sau mutation/realtime: `use-orders-data.ts:1056-1069` — sau patchOrder/realtime upsert thành công → revalidate counts (rẻ nhất: gọi lại loadOrders phần counts hoặc điều chỉnh delta local).
  b. Chip "Tất cả" cộng cả key "6": `pages/OrdersPage.tsx:1352-1355` — loại key 6 khỏi tổng (khớp hành vi click).
  c. Counts vs invoice_status panel: phối hợp semantics Task 4.2 — nếu BE giữ strip invoice_status thì FE khi click chip lifecycle phải clear invoice_status ≠ "2"; chọn 1 phương án và ghi vào doc này.

7.2 (THẤP) i18n — `OrdersPage.tsx:1378` key `orders.filters.salesStatusAll` không tồn tại → thêm key hoặc dùng key đúng; count format `toLocaleString("vi-VN")`.

7.3 (THẤP) A11y chip — thêm `aria-pressed`.

7.4 (THẤP) Ads first-load KPI=0 — `features/ads/pages/AdsReportPage.tsx:49-51,126`: nhánh isLoading → LogoSpinner; giữ keep-previous khi refetch.

### Task 7 chip semantics (7.1c — quyết định)

`status_counts_for` (backend) strip TOÀN BỘ 2 trục `status` và `invoice_status` khi đếm
lifecycle key (0..5) — tức count không quan tâm panel đang lọc invoice_status gì. Nếu FE
giữ nguyên invoice_status của panel (trừ "2") khi bấm chip lifecycle, count trên chip sẽ
nhiều hơn số dòng thực trả về (đếm bỏ qua invoice_status, danh sách thì áp).

Phương án chọn: khi bấm 1 chip lifecycle (bao gồm "Tất cả") → **xoá sạch** filter
`invoice_status` (không chỉ bỏ "2") để danh sách khớp cách BE đếm. Khi bấm chip
"Đã hủy" → **giữ nguyên hành vi cũ** (set `invoice_status=["2"]`, gộp thêm các giá trị
invoice_status khác đang có trong panel) — không đổi, vì đây là trục mà chip đang chủ
động điều khiển.

Đánh đổi: nếu user đã tự chọn invoice_status khác qua panel (hiếm, không phải "2") rồi
bấm chip lifecycle, filter đó bị xoá theo — chấp nhận vì ưu tiên "số trên chip = số dòng
sau khi bấm" (ràng buộc chính của 7.1).

Bổ sung (review follow-up): realtime event trỏ tới đơn KHÔNG nằm trên trang hiện tại
không suy được delta cục bộ (không biết trạng thái cũ; đơn có thuộc tập đếm hay không
là tri thức server-side). Không có đường revalidate sẵn nào cho case này (`order.updated`
chỉ patch in-memory; duy nhất `restore.completed` mới reload list) → thêm revalidation
counts-only DEBOUNCED 2.5s trailing: `loadOrders(undefined, { statusCountsOnly: true })`
gửi lại đúng body filter hiện hành với `limit=1`, chỉ đọc `status_counts` từ response —
không đụng rows/pagination/loading (giữ keep-row-on-user-edit, không nháy bảng), burst
webhook = 1 request, seq-guard bỏ kết quả nếu một full-load mới hơn đã bắt đầu.

## Task 8 — FE warranty

8.1 (CAO) JourneyCard wipe form dirty — `features/warranty-claims/components/JourneyCard.tsx:204-210`: không reset khi `dirty` (giữ badge "Chưa lưu"); `commitName` chặn khi dirty hoặc gộp name vào buffered patch. Test các case: lưu tên khi đang dirty, refetch realtime.
8.2 (TRUNG) Tách file 718 dòng → sub-component per-section + hook `useJourneyForm` (mục tiêu < 300 dòng/file).
8.3 (TRUNG) Checkbox sticky đè cột locked — `WarrantyClaimsTable.tsx:134,149,176-179,192`: cột locked offset `left-10` khi có checkbox column.
8.4 (THẤP) Delete journey guard `disabled={del.isPending}` — `JourneyCard.tsx:377-385`.
8.5 (THẤP) searchable-select async nuốt lỗi — `components/ui/searchable-select.tsx:74-76`: state error riêng, hiển thị "Không tải được dữ liệu" thay vì "Không có dữ liệu".
8.6 (THẤP) Product search qua React Query — `JourneyCard.tsx:214-217`: dùng useQuery theo mẫu useOrderSearch.
8.7 (THẤP) Bỏ cast `as WarrantyJourneyPayload` — `JourneyCard.tsx:275`: khai báo typed object.
8.8 (THẤP) Detail page phân biệt isError — `pages/WarrantyClaimDetailPage.tsx:33-34` + LogoSpinner cho loading (cả `WarrantyClaimsTable.tsx:157-161`).
8.9 (THẤP) Input ngày kích hoạt rollback khi PATCH lỗi — `WarrantyClaimsTable.tsx:96-108`: onError → remount/reset về giá trị server.
8.10 (THẤP) Xoá dead re-export `CLAIM_STATUS_OPTIONS` — `journey-enums.ts:83`.

## Hoãn có chủ đích (không làm đợt này)
- ReturnEntry.source_order SET_NULL vs partial unique khi hard-delete Order (edge chỉ xảy ra với script tay; prod soft-delete).
- Trigram cho search phiếu trả ledger (bảng còn nhỏ).
- `outside-orders` trong ORDER_SECTIONS thừa hưởng cụm endpoint đọc (thiết kế nhất quán với landing/koc — cần quyết định sản phẩm riêng).
- Bump boto3/websockets (không CVE; làm riêng để test kỹ S3/WS path).
- Django 6.2 LTS (đã có backlog riêng).

## Nhật ký tiến độ
(cập nhật sau mỗi task)
- Task 1 (BE orders/kiotviet): XONG — 7 commit 6a069a2b..4ee2ab7b, review sạch. Ghi chú: 1.2 field-presence guard đã đúng sẵn trên master (chỉ thiếu lock); 1.5 mở rộng Order-row lock cho cả entry warranty/defect (chủ đích, rẻ).
- Task 2 (BE security/RBAC): XONG — 8 commit fc4cea89..233d422d (gồm 2 commit fix schema drift + fail-closed gate), review sạch. salaries:view đã seed (chỉ admin), leave chặn tự duyệt, query_params redact (phone giữ 3 số cuối), manual_match gate theo kind fail-closed, raw_address alias theo address, FB failed_accounts lộ ra log + response (schema regen 2 phía).
- Task 3 (BE retention/bounds): XONG — 3 commit c9a6ab4d..70457121. 3.1: `photo_storage.delete_photo` + task `apps.hr.tasks.purge_old_attendance_photos` (clear object MinIO + `photo_key`, event giữ nguyên; delete lỗi → giữ `photo_key` để thử lại; `ATTENDANCE_PHOTO_RETENTION_DAYS` default 180; beat 02:30). 3.2: `list_unmatched` default 30 ngày khi thiếu CẢ HAI date_from/date_to (chỉ thiếu 1 thì giữ hành vi cũ), LIMIT cứng 1000 dòng (log khi chạm); task `apps.inventory.tasks.purge_matched_pending_scans` xoá status=matched cũ hơn `PENDING_SHIPMENT_SCAN_RETENTION_DAYS` (default 90, beat 03:15), pending/failed/invalid/cancelled không bao giờ bị đụng. Cả 2 task theo đúng pattern `purge_old_audit_logs` (single_flight + batch). Test: 13 test mới (hr photo purge 5, pending scan bounds 4, pending scan purge 4) + regression `apps/hr apps/inventory apps/audit apps/orders` 733 passed, 0 fail.
- Task 3 (BE retention/bounds): XONG — purge selfie 180d (beat 02:30) + bounds list_unmatched (default 30d, LIMIT 1000) + purge MATCHED scans 90d (beat 03:15); fix follow-up bắt BotoCoreError. Ghi chú: default 30d chỉ áp khi cả 2 đầu ngày trống (range hở 1 đầu giữ hành vi cũ, có LIMIT chặn).
- Task 4 (DB/perf): XONG — index 0102 (kv_channel,invoice_id) + 0103 partial invoice_status=2 (CONCURRENTLY), status_counts_for gộp 1 aggregate + CANCELLED_ORDER_Q dùng chung, CheckConstraint quantity>=1 (0023) + auto-fill bỏ qty=0, reverse SQL 0101 có lock_timeout. Deploy notes trong doc (pre-check 0023, indisvalid, 0102 trước backfill).
- Task 5 (BE warranty/Excel): XONG — journey rank guard (không hạ RETURNED->PACKED), append_clean_row neutralize formula qua data_type+quotePrefix (giá trị giữ nguyên 100%, chỉ '=' là vector với XLSX openpyxl), Excel 'Hết hạn' khớp web.
- Task 6 (FE inventory): XONG — 10 commit, 12 item + fix ScanReturnDialog cùng bug-class. Autosave note có onError+revert savedRef, passthrough lỗi batch, sequence token CreateReturnDialog, debounce flow history (bỏ remount, reset offset tường minh), invalidate chéo, types từ schema, a11y rows. 391 test FE pass.
- Task 7 (FE orders/ads): XONG — 5 commit b3dcba52..85fe840a. 7.1a: statusCounts revalidate qua delta cục bộ (order-status-counts.ts: statusChipKeyFor/applyStatusCountsDelta) sau patchOrder/patch+upsert realtime; bắt được 1 bug thật lúc viết test — tính oldKey/newKey NGAY SAU khi gọi setRows(...) sai vì updater không đảm bảo chạy đồng bộ, phải chuyển sang đọc từ `rowsRef` snapshot trước khi gọi setRows. 7.1b: statusChipTotal loại key "6" (computeStatusChipTotal). 7.1c: bấm chip lifecycle nay xoá sạch invoice_status (không chỉ bỏ "2") để khớp cách status_counts_for đếm (strip cả 2 trục); chip "Đã hủy" giữ nguyên hành vi cũ — xem note "Task 7 chip semantics" phía trên. 7.2: `orders.filters.salesStatusAll` thêm bản dịch thật (vi "Tất cả trạng thái"/en "All statuses"); 2 chip "Tất cả" đổi sang tái dùng `status.all` có sẵn; count format đổi "en-US"→"vi-VN". 7.3: aria-pressed cho mọi chip toggle. 7.4: AdsReportPage — LogoSpinner khi `reportQuery.isLoading` (chỉ true ở lần tải đầu vì đã có `placeholderData: keepPreviousData`), giữ KPI cũ khi refetch. Test: 39 test mới (order-status-counts 20, use-orders-data status-counts 3, i18n 3, AdsReportPage 3) + toàn bộ 421 test FE pass, tsc/eslint sạch.
- Task 7 (FE orders/ads): XONG — chips delta + counts-only revalidation debounce 2.5s cho update off-page (statusCountsOnly, seq-guard, không đụng rows), Tất cả loại key 6, click chip lifecycle clear invoice_status (đã ghi semantics), i18n + aria-pressed + vi-VN, ads LogoSpinner first-load. 423 test FE pass.
- Task 8 (FE warranty): XONG — 3 commit 0ec3bc9f..dab345a5, đủ 10 item. 8.1: root cause wipe = effect sync form theo updated_at chạy cả khi đang dirty (lưu tên → invalidate → refetch → reset); fix = effect bỏ qua khi `dirty` (giữ badge Chưa lưu), nameDraft cũng guard theo `editingName` — nhờ đó commitName khi dirty an toàn, KHÔNG cần chặn hay gộp name vào buffered patch. 8.2: JourneyCard 718 dòng → `journey/` module 5 file đều <300 (journey-form.ts: state+initForm+buildJourneyPatch+useJourneyForm; journey-fields.tsx: Field+EnumSelect generic; journey-sections-send/-return.tsx: §2-§4/§5-§7). 8.7 đi kèm: JourneyFormState type các field enum từ payload OpenAPI, EnumSelect generic trả đúng union (option "" = BlankEnum), status chọn qua `MANUAL_STATUS_VALUES.find` — hết cast `as WarrantyJourneyPayload`. 8.6: `useProductSearch` (products/queries.ts) dùng `qc.fetchQuery` + key `products.search(q)` staleTime 30s — SearchableSelect tự quản search state nên dạng callback-fetchQuery thay vì hook useQuery (ghi chú lệch mẫu useOrderSearch có chủ đích). 8.9: contract `onPatch` của WarrantyClaimsTable đổi trả `Promise<boolean>` (page mutateAsync, toast tại page, không reject) → ô ngày kích hoạt rollback `input.value` về server khi false; select status là controlled nên tự hồi. 8.3: cột locked đổi left-0 → left-10 (checkbox w-10 chiếm left-0). 8.5: SearchableSelect thêm `loadError` — phân biệt "Không tải được dữ liệu" (destructive, retry bằng gõ lại) với "Không có dữ liệu". 8.4/8.8/8.10: delete guard isPending, detail page isError + LogoSpinner (cả loading row của bảng), xoá dead re-export CLAIM_STATUS_OPTIONS. Test: +4 (JourneyCard dirty-preserve/sync-khi-sạch/lưu-tên-khi-dirty, table rollback); toàn suite 427 pass, tsc + eslint sạch (2 warning react-compiler do eslint-disable exhaustive-deps — pattern có sẵn từ trước).
