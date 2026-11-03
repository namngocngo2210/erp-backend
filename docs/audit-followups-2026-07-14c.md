# Audit followups 2026-07-14 (vòng 3) — Plan & nhật ký

Scope `2ebc504c..bebe5807`: adversarial review 16 fix của MR `fix/audit-followups-2026-07-14b`
(đã merge) + MR `refactor/remove-return-note-ui`. Branch: `fix/audit-followups-2026-07-14c`.

**Bối cảnh đáng ghi nhớ**: 4/6 lỗi CAO của vòng này do chính các MR vừa merge sinh ra, 3 trong
số đó là hồi quy từ fix vòng 2. Cơ chế "fix xong audit lại chính fix đó" đang trả cổ tức.

## CAO

### 1. Cron 30' lật đơn HUỶ thành "đã giao" → trừ tồn của đơn đã huỷ
`_handle_update_status_phone` chỉ đọc `invoiceDelivery.status` rồi **mặc định 3** khi thiếu.
Hoá đơn huỷ (bán quầy POS / KV bỏ delivery khi huỷ) không có `invoiceDelivery` → đơn CANCELLED bị
kéo về IN_DELIVERY. Đường import làm ĐÚNG (`_resolve_invoice_status`: own_status=2 + không delivery
→ CANCELLED); cron thì không. Trước vòng 2 đây chỉ là lệch hiển thị âm thầm (FE lấy tab "Đã huỷ"
theo `invoice_status`), nhưng vòng 2 thêm `publish OrderStatusChanged` vào chính handler này → cú
lật `6 → 3` gọi `reissue_after_cancel_release` → **trừ tồn hàng vẫn nằm trong kho**. Cron quét cả
tháng ⇒ lần chạy đầu sau deploy sai hàng loạt.
**Fix**: handler dùng `_resolve_invoice_status(invoice, delivery)` — cùng resolver với import.
Thêm publish cho `_handle_resync_codes` (bất biến: mọi đường đổi `Order.status` đều phát sự kiện).

### 2. Trừ lại tồn theo sổ dùng SKU CŨ khi hoá đơn bị sửa dòng hàng
Mà sửa dòng hàng chính là *lý do* KiotViet huỷ-xuất lại `X → X.01`. Đơn xuất 2×SKU-A → huỷ (+2 A)
→ hồi sinh với 3×SKU-B: sổ trừ lại 2×SKU-A. SKU-A bị trừ vĩnh viễn dù không còn trong đơn, SKU-B
không bao giờ bị trừ; khi hàng hoàn về thì `_guard_double_credit` chặn (SKU-B chưa từng bị trừ) →
mã quét chết cứng. Bản cũ (`_reissue_after_revive`, trừ theo `order.items`) không có ca này —
**hồi quy do chuyển sang ledger-based**.
**Fix**: subscriber gọi `reissue_after_cancel_release` rồi `resync_for_order` theo items hiện tại
(`_resync_to_current_items`). `resync` tự no-op khi outstanding = 0 nên đơn chưa rời kho không bị
đụng. Thêm `logger.info` cho mọi bút toán trừ lại (chạy trong on_commit → lỗi bị bus nuốt).

### 3. Phiếu trả NHÁP bị khoá chết (MR remove-return-note-ui)
MR xoá list/PATCH/post của phiếu trả và `cancel_entry` chỉ nhận POSTED — nhưng **vẫn để nguyên
đường tạo** nháp 0 dòng. Mã vận đơn kẹt trong nháp legacy bị guard chống-trùng chặn: quét lại báo
"đã có trong phiếu PTH-…" và **không ghi movement nào** → hàng hoàn không bao giờ cộng tồn, không
còn nút nào gỡ.
**Fix**: (a) serializer bắt buộc `lines` (mã chưa khớp đi đường `PendingShipmentScan`); (b)
`create_entry` tự huỷ nháp RỖNG đang giữ mã rồi tạo tiếp; (c) `cancel_entry` nhận cả DRAFT; (d)
`pending_scan_service` không coi nháp rỗng là "đã ghi nhận".

### 4. Cron retry Best "bỏ cuộc theo tuổi" làm MẤT trạng thái đơn
Bước abandon chạy **trước** vòng apply và quét mọi `processed=False` theo tuổi. Hệ thống chết 3
ngày (worker treo / DB cạn connection) ⇒ vòng cron đầu tiên khi sống lại đánh dấu `processed=True`
cho cả event **chưa từng được thử**, gồm Giao thành công / COD / Chuyển hoàn — Best đã nhận
`Result 1` nên không đẩy lại. Mất vĩnh viễn, `processing_error` còn ghi sai lý do.
**Fix**: abandon chạy SAU vòng apply, và chỉ với event đã thử & trượt vì `unmatched`
(`processing_error__startswith="unmatched"`); event lỗi do exception giữ nguyên. Thêm
`logger.warning` khi có event bị bỏ cuộc.

### 5. Reattach lần 3 vẫn sinh dòng đơn trùng + lost update mảng mã chết
`dead_codes` chỉ gồm mã gốc + mã đơn ĐANG giữ. `X → X.01 → X.02` mà event của `X.01` không kịp xử
lý (đến sau `X.02`, hoặc fetch timeout) thì lúc xử lý `X.02` đơn vẫn mang `X` → `X.01` không bao
giờ vào mảng → sự kiện huỷ của nó tới muộn lại tạo dòng trùng. Kèm lost-update: giữa lúc đọc
`candidate` và lúc ghi mảng có **2 lần gọi API KiotViet**, không lock.
**Fix**: suy mã chết từ **CHUỖI MÃ** (`X.02` tồn tại ⇒ mọi `X.kk` với kk < 02 đã chết), không suy
từ trạng thái đơn; bọc repoint trong `transaction.atomic` + `select_for_update`, đọc lại mảng
trong lock; ưu tiên candidate là đơn kế thừa (mảng chứa base) hơn đơn mang `invoice_code = base`
(prod còn dòng rác của bug cũ, `updated_at` rất mới nên dễ bị chọn nhầm); `_upsert_invoice` đọc
lại mảng ngay trước `save()` để không ghi đè bằng bản đọc cũ.

### 6. Migration `AddConstraint` không dedupe → kẹt deploy
`0004` dựng lại 2 unique constraint bỏ điều kiện `modified_at_ms > 0` — mà dòng trùng ts=0 chính là
thứ DB đang được phép chứa. `0003` thêm unique `(username, environment)` cho bảng chưa từng có
constraint đó. Có dữ liệu trùng ⇒ `CREATE UNIQUE INDEX` fail giữa deploy.
**Fix**: `RunSQL` dedupe trước mỗi `AddConstraint` (giữ dòng active / id nhỏ nhất).

## TRUNG
7. **PII nhân sự** — `/api/hr/employees` trả CCCD, ngày sinh, địa chỉ thường trú, STK ngân hàng,
   MST, sổ BHXH cho **mọi** ai có `employees:view` (quyền được cấp rộng: sơ đồ tổ chức, danh bạ),
   không scope theo người ⇒ gate `salaries` của vòng 2 chỉ đúng một nửa. **Fix**:
   `SENSITIVE_EMPLOYEE_FIELDS` chỉ lộ cho `employees:edit` hoặc **chính chủ**.
8. **`create_shipment` Best quên scope order** (write-IDOR: NV scope OWN/CHANNEL gắn vận đơn vào
   đơn kênh khác) → `_scoped_orders`. Kèm: vận đơn `order=None` giờ neo `created_by` nên scope hẹp
   vẫn thấy lại vận đơn của chính mình (trước đây tạo xong là mất dấu).
9. **`best_sync_account` deactivate mọi account cùng env** → phá setup nhiều merchant, webhook của
   account bị tắt trả 401 và Best ngừng retry. Đổi thành opt-in `--deactivate-others`.
10. **Migration 0003 RunPython** dùng model THẬT (replay trên DB sạch sẽ nổ khi có cột mới) và
    `.exclude(ems_key="")` là **no-op** (get_prep_value mã hoá cả chuỗi rỗng → so với ciphertext
    ngẫu nhiên). Viết lại bằng SQL thô + `get_encryption()`.
11. **Route "Phiếu kho" lệch RBAC** — FE `GuardAny ["stock-documents","returns"]` nhưng endpoint đã
    siết còn `stock-documents` ⇒ user kho-chỉ-làm-hàng-hoàn vào được trang rồi ăn 403, trang trống.
    Guard đổi thành `stock-documents`; nút "Quét trả hàng" gate riêng theo `returns:edit`.

## Deploy notes
- **best_express `0003`/`0004`**: nay có bước dedupe tự động trước khi dựng constraint — không cần
  dọn tay nữa. `0005` thêm `BestShipment.created_by` (nullable, không backfill: vận đơn cũ không có
  người tạo → chỉ scope ALL thấy, đúng như hiện trạng).
- **RBAC**: ai đang vào trang Phiếu kho bằng section `returns` sẽ cần thêm `stock-documents:view`.
  Ngược lại, muốn dùng nút "Quét trả hàng" thì cần `returns:edit`.
- **`best_sync_account`**: script deploy nào đang dựa vào hành vi tự-tắt-account-cũ phải thêm cờ
  `--deactivate-others`.
- **PII**: người phụ trách nhân sự cần `employees:edit` để còn thấy CCCD/STK; `employees:view`
  thuần giờ chỉ thấy thông tin phi nhạy cảm.

## Còn treo (chờ quyết định)
- **Hoàn MỘT PHẦN**: luồng quét luôn nạp full số lượng của đơn và `return_type` luôn
  `delivery_returned`; UI chọn dòng/SL/loại hoàn (`warranty`/`defect`/`external_return`) đã bị MR
  remove-return-note-ui gỡ. Khách trả 2/5 SP → quét cộng tồn 5, dư 3, `_guard_double_credit` không
  chặn. Chưa sửa vì đây có thể là cắt tính năng CÓ CHỦ ĐÍCH — cần chốt với người dùng.

## Vòng 4 (adversarial review chính commit vòng 3 + sweep route↔RBAC)

Kết quả: **commit vòng 3 không có lỗi CAO** (3 agent review độc lập xác nhận). Sweep ngang
route tìm ra lớp lỗi lệch guard tiếp theo:

- **Trang Đồng bộ (`/settings/sync`) — GỠ HẲN** (user xác nhận trang đã bỏ về nghiệp vụ).
  Trước đó nó là ca vỡ-trang: route gate section `sync` nhưng 2 endpoint của trang gate
  `kiotviet` + admin ⇒ `sync:view/edit` là quyền chết. Gỡ: FE page/route/tab/api/i18n,
  BE `sync/channels` + `sync/run` (caller duy nhất là trang này; đồng bộ định kỳ vẫn chạy
  qua celery, thao tác tay qua CLI), section `sync` khỏi catalog + `VALID_SECTIONS`,
  migration `users/0028` dọn dòng permission đã seed.
- **Trang KiotViet (`/settings/kiotviet`)**: BE vốn superuser-only (`can_manage`), FE shell
  cũng chỉ fire query khi superuser, nhưng route lại mở theo `kiotviet:view` ⇒ người được
  cấp quyền đó vào bằng URL trực tiếp gặp trang trống + 403. Thêm `GuardSuperuser` cho
  route. Quyền `kiotviet:edit` GIỮ NGUYÊN trong catalog — dialog quản lý địa chỉ gửi KShip
  đang dùng thật.
- **Cron retry Best — lớp event bất tử thứ 2**: event khớp shipment nhưng `apply_event`
  ném exception bền vững (vd `IntegrityError` unique `code_best`) → transaction rollback
  giữ `processing_error` rỗng → không bị abandon (không phải unmatched), không bị purge
  (processed=False) → bảng phình + chiếm slot retry. Fix: nhánh exception ghi
  `processing_error="error: ..."` (ngoài transaction đã rollback); abandon giờ áp cho mọi
  event ĐÃ THỬ (error không rỗng) trừ event vừa lỗi lần đầu trong chính vòng đó.
- **Gộp reissue + resync vào một `transaction.atomic`** (subscriber hồi sinh đơn): tránh
  tồn kẹt nửa chừng nếu resync lỗi sau khi reissue đã commit.
- **`_resolve_invoice_status` hết trộn thang**: bỏ nhánh trả `invoice_own_status` thô vào
  `Order.status` (hoá đơn "đang xử lý"(3) từng có thể thành đơn "Đã giao"(3)) → trả None.
- Fix cờ log "defaulted to 3" từng nói dối khi resolver tự suy ra 6 (huỷ).

Ghi nhận (không sửa): quyền `channels:create/edit/delete` cũng là quyền chết (BE chặn bằng
superuser) — cùng bản chất với trang KiotViet, để đợt dọn catalog sau; TOCTOU nhỏ còn lại ở
re-read mảng trước `order.save()` (cửa sổ hẹp, đã có guard invoice_id chặn phần lớn); giả
định KiotViet pad 2 chữ số (`.01`) trong suy chuỗi mã chết — đúng với dữ liệu prod hiện tại,
cần xác nhận lại nếu thấy format khác.

### Deploy notes vòng 4
- `users/0028`: xoá permission `sync:view`/`sync:edit` (cascade role links). Role nào đang
  hiển thị quyền này sẽ mất nó — vốn là quyền không có tác dụng.
- Ai bookmark `/settings/sync` hoặc `/sync` sẽ về trang mặc định (route đã gỡ).

## Nhật ký
- CAO 1–6 + TRUNG 7–11: XONG. Test mới: `test_cron_status_cancelled_invoice.py` (3),
  `test_employee_pii_visibility.py` (5), reissue-on-revive +1 (items đổi sau hồi sinh),
  `test_return_unmatched.py` viết lại theo hợp đồng mới (10), best webhook +2 (không abandon event
  chưa thử; apply trước abandon), invoice webhook +1 (mã trung gian bị lỡ vẫn được ghi là chết).
- Verify: kiotviet 192 pass, best_express + return 116 pass, hr 119 pass, inventory reissue 11 pass;
  FE 406 pass, `tsc` sạch.
- Vòng 4: users+kiotviet+best_express+inventory 718 pass (2 fail baseline rbac_roles_name_key);
  FE 406 pass, `tsc` sạch; schema regen (2 endpoint sync gỡ khỏi schema).
