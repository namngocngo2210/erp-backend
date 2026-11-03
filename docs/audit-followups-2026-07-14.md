# Audit followups 2026-07-14 — Plan & nhật ký

Xử lý findings audit 2026-07-14 (scope = 10 MR merge sau audit 13/07). Branch: `fix/audit-followups-2026-07-14`.

Quy ước: commit tiếng Anh ngắn; BE test qua image `desi-order-web-test` (`-e POSTGRES_DB=desi_<unique>` + `-e REDIS_PASSWORD=testpassword`); FE test `npx vitest run`; đổi serializer → regen `schema.yaml` + `npm run gen:api` + `npx prettier --write src/api/schema.d.ts`.

## Task 1 (CAO) — Reattach `.NN` hồi sinh đơn nhưng không trừ lại tồn

Root cause: `ImportInvoicesService.upsert_invoice` hồi sinh đơn (status 6 → sống) bằng `order.save()` trần, KHÔNG publish `OrderStatusChanged` → subscriber kho không biết để trừ lại tồn đã hoàn lúc hủy. Hệ quả: đơn sống + hàng đang giao nhưng `order_has_outstanding_stock=False` → cho quét xuất lại + tồn phồng vĩnh viễn khi giao thành công.

Fix:
1. `_import_service_upsert_mixin._upsert_invoice`: nhánh đơn đã tồn tại — chụp `previous_status` trước khi gán, sau `save()` publish `OrderStatusChanged` nếu status đổi (cùng chuẩn webhook). Cũng đóng luôn chiều ngược: import lật đơn sang hủy giờ mới release tồn.
2. `inventory/subscribers.py`: thêm nhánh "hồi sinh" — `old_status=6` và `new_status` không phải trạng thái release → trừ lại tồn theo items hiện tại + kho xuất, CHỈ khi đơn đang `dispatched` và từng có SALE. Có phiếu trả đã ghi sổ (hàng về kho thật) → KHÔNG trừ lại, log WARNING để đối soát tay.

## Task 2 (TRUNG) — Hóa đơn gốc mồ côi sau reattach → tạo lại đơn hủy trùng

Sau reattach, `invoice_code` cũ (X) không còn thuộc đơn nào → webhook hủy X đến muộn / cron import tạo dòng đơn hủy MỚI (đúng symptom "đơn trùng" MR muốn diệt). Phantom giữ `invoice_id` cũ → returns-sync có thể gắn phiếu trả nhầm.

Fix: lưu vết mã gốc khi reattach (`Order.reattached_invoice_codes` — mảng, migration mới) và cho `_find_order_for_invoice_event` + `_find_existing_order` (import) tra cứu mã gốc về đúng đơn đã reattach → webhook hủy muộn/cron rơi vào nhánh "đã có đơn", không tạo mới.

## Task 3 (TRUNG) — Payroll/compensation bypass gate `salaries`

`compensation_views` gate `employees:edit`/`employees:export` → role có section `employees` tải được workbook lương toàn công ty + sửa lương. Fix: thêm action `salaries:export` vào registry (không tự gán role nào ngoài admin), gate `compensation_list` = `salaries:view`, `compensation_update` = `salaries:edit`, `payroll_export` = `salaries:export`. FE: gate route/nav theo `salaries`.

## Task 4 (TRUNG) — best_express: retention + dedupe webhook events

- `tasks.py` mới: purge `best_webhook_events` cũ hơn `BEST_WEBHOOK_EVENT_RETENTION_DAYS` (default 90), theo pattern `purge_old_audit_logs` + beat; index `received_at`.
- Partial unique thứ hai cho event thiếu `code_best` (theo `merchant_order_code`); event `modified_at_ms=0` → từ chối store + log thay vì insert mù.
- `apply_event` scope theo `event.account` (không cho token account A ghi shipment account B).

## Task 5 (TRUNG, FE) — Trang Cơ chế lương + KShip sender

- 5.1 `CompensationPage`: seed lại draft từ response sau save (hết dirty vĩnh viễn); so sánh dirty theo số, không theo chuỗi.
- 5.2 `CompensationPage`: thêm nhánh `isError` (403 vs lỗi khác) thay vì hiện "Chưa có nhân sự."; route/nav gate theo quyền thật (Task 3).
- 5.3 `OrdersPage` kship sender: khi list về mà id đang chọn không còn → reset về `default_id ?? null`.
- 5.4 `KshipSenderAddressManageDialog`: reset form state khi đóng dialog (chống stale-edit lần mở sau).

## Task 6 (THẤP) — dọn dẹp

- 6.1 `compensation_views`: `date.today()` → `timezone.localdate()` (2 chỗ).
- 6.2 `attendance_export_excel.py` + `payroll_export_excel.py`: dùng `append_clean_row` (formula injection qua tên NV / tên ngân hàng).
- 6.3 `payroll_export_service`: lương cơ bản chọn `SalaryRecord` phủ tháng export (`effective_from/effective_to`) thay vì `is_current` — ghi rõ semantics vào doc.
- 6.4 migration mới đụng bảng nóng: quy ước `SET lock_timeout` (áp cho migration Task 2).
- 6.5 `use-orders-data`: cập nhật `rowsRef.current` ngay trong updater để delta counts không lệch khi 2 patch cùng đơn trong 1 batch.

## Deploy notes (chạy tay trên prod)

a. **orders `0105_order_reattached_invoice_codes`** — ADD COLUMN có `SET LOCAL lock_timeout='5s'` (fail nhanh thay vì chặn bảng `orders`); index GIN tạo CONCURRENTLY. Sau khi migrate verify index hợp lệ (CONCURRENTLY bị ngắt để lại index INVALID và `IF NOT EXISTS` lần sau sẽ âm thầm bỏ qua):

   ```sql
   SELECT c.relname, i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
   WHERE c.relname = 'ord_reattached_codes_gin';
   -- indisvalid phải = true. Nếu false: DROP INDEX CONCURRENTLY rồi tạo lại.
   ```

b. **best_express `0002`** — bảng `best_webhook_events` còn rỗng (app chưa wire vào luồng đơn) nên AddIndex/AddConstraint thường là đủ. Nếu tới lúc deploy bảng đã có dữ liệu thật thì phải đổi sang CONCURRENTLY trước khi chạy.

c. **users `0027`** — seed `salaries:edit` + `salaries:export`. KHÔNG gán role nào (chỉ Administrator có sẵn full quyền). Sau deploy: cấp `salaries:*` cho đúng người phụ trách lương (kế toán/HCNS trưởng), nếu không trang Cơ chế lương + nút Xuất bảng lương sẽ biến mất khỏi nav của họ — đây là hành vi ĐÚNG theo thiết kế mới.

d. **Env mới (tuỳ chọn)**: `BEST_WEBHOOK_EVENT_RETENTION_DAYS` (default 90).

## Nhật ký tiến độ
- Task 1 (CAO — tồn phồng khi reattach): XONG — commit 56e0a983. Root cause đúng như audit: `upsert_invoice` hồi sinh đơn bằng `order.save()` trần nên không phát `OrderStatusChanged` → subscriber kho không biết trừ lại tồn đã cộng lúc huỷ. Fix 2 tầng: (1) upsert phát event khi status đơn ĐÃ TỒN TẠI đổi (đóng luôn chiều ngược: import lật đơn sang huỷ giờ mới release tồn); (2) subscriber thêm nhánh hồi sinh — chỉ trừ lại khi đơn đang `dispatched` và từng có SALE; có phiếu trả đã ghi sổ (hàng về kho thật) thì KHÔNG trừ, chỉ log WARNING để đối soát tay. Test: 5 test mới (`test_stock_reissue_on_revive.py`) + 1 test webhook phát event. Regression: 864 test kiotviet/orders/inventory pass.
- Task 3 (gate lương) + Task 5 (FE lương/kship): XONG — commit 5ec08d6e. `salaries` giờ có đủ view/edit/export (migration users 0027); compensation list/update + payroll export + POST lương/hợp đồng đều chuyển sang gate `salaries:*` (trước đây `employees:edit`/`employees:export` → role HCNS xem/sửa được lương + số tài khoản toàn công ty). FE: route + nav item `compensation` gate theo `salaries` (SECTION_PERMISSION giờ nhận key quyền không-phải-AppSection). CompensationPage: so dirty theo GIÁ TRỊ SỐ (BE trả canonical "24.0"/"0.20000" khác chuỗi user gõ → nút Lưu sáng vĩnh viễn), seed lại draft từ response sau khi lưu, thêm nhánh isError + LogoSpinner, toast lỗi nguyên văn qua `getApiErrorMessage`. KShip: id địa chỉ gửi đã xoá được reset về default (không còn 400 "Địa chỉ gửi không tồn tại"), manage dialog reset form khi đóng. Test: 4 test FE mới + 4 test BE mới; test cũ `test_post_salaries_200_with_employees_edit` được viết lại theo contract mới (employees:edit KHÔNG còn đặt được lương).
- Task 2 (đơn hủy phantom): XONG — commit 6d21b736. Thêm `Order.reattached_invoice_codes` (ArrayField + GIN index, migration orders 0105). Webhook reattach ghi mã gốc vào đó; `_find_order_for_invoice_event` (webhook) và `_has_reattached_successor` (import) tra ngược mã gốc → webhook huỷ đến muộn / cron import 5' không còn tạo dòng đơn huỷ trùng. Guard "chỉ áp huỷ khi `order.invoice_code` khớp" (có sẵn) giữ đơn kế thừa không bị lật huỷ. Test: 1 test webhook + 2 test import.
- Task 4 (best_express): XONG — commit c8950fc4. `apply_event` scope theo `event.account` (token account A không ghi được shipment account B); partial unique thứ hai cho event chỉ có `merchant_order_code` (chặn nhân bản khi Best retry / `trace()` re-store); `tasks.py` mới: purge event ĐÃ xử lý cũ hơn `BEST_WEBHOOK_EVENT_RETENTION_DAYS` (default 90, beat 03:20) + **retry event chưa xử lý được** mỗi 15' (trước đây comment hứa "xử lý lại sau" nhưng không có task nào → event unmatched nằm chết vĩnh viễn vì Best đã nhận Result 1). Index `received_at` + partial `processed=False`. Test: 5 test mới, toàn app 73 pass.
- Task 6 (nhóm THẤP): XONG — `date.today()` → `timezone.localdate()` (gộp vào commit Task 3); 2 export HR mới dùng `append_clean_row`/`set_text_cell` (formula injection qua tên NV / tên ngân hàng — công thức chủ đích của bảng lương vẫn giữ); bảng lương lấy `SalaryRecord` có hiệu lực TRONG THÁNG XUẤT thay vì `is_current` (chốt lương T7 đầu T8 sau đợt tăng lương 01/08 không còn đổ lương mới vào tháng cũ) + helper public `month_bounds`; `rowsRef` cập nhật đồng bộ trong applyOrderPatch/Upsert (2 event realtime cùng đơn trong 1 batch không trừ delta 2 lần). Test: 2 test payroll + 1 test formula + 1 test FE race.
