# Kho số thả nổi (Floating Pool) — Thiết kế

**Ngày:** 2026-07-16 · **Trạng thái:** Chờ duyệt spec · **Branch:** `feat/sale-floating-pool`

## 1. Bài toán

Lead (Order) được chia tự động cho sale qua `LeadAssignmentService`, nhưng nếu sale bỏ bê
không chăm sóc thì lead chết trong tay người giữ. Cần cơ chế **thu hồi & tái phân phối**
(tham khảo "Kho số thả nổi" của Pushsale): đơn không được chăm sóc trong X ngày sẽ bị
"thả nổi" vào một kho chung, sale khác tự vào **nhặt** về chăm tiếp.

Khác biệt so với Pushsale: bên họ đơn vị dữ liệu là contact (số điện thoại), bên mình là
**Order** (mỗi lần khách để lại số trên landing → 1 Order). Đã chốt: **thả nổi Order**.

## 2. Quyết định phạm vi (đã chốt với CTO)

| Quyết định | Lựa chọn |
|---|---|
| Đối tượng thả nổi | **Order** (không phải contact) |
| Điều kiện "đã chăm" | **Chỉ một số trạng thái được cứu** (cấu hình) + **lịch hẹn còn hiệu lực** |
| Chu kỳ | **Vòng lặp**: X ngày không tích trạng thái & không có lịch hẹn → thả nổi (kể cả đơn từng được cứu rồi lại bỏ bê) |
| Phạm vi hiển thị | **1 kho chung toàn phòng Sale** (không chia team, không leo thang) |
| Giới hạn | Tối đa **N lần nổi/đơn**, tối đa **M số nhặt/ngày/sale**, **ẩn SĐT** trong kho |
| Ngoài phạm vi | Leo thang team→đơn vị, cấu hình kho theo nhóm team, đếm theo cuộc gọi Zenify, chia thẳng data mới vào kho, thông báo Lark, tin nhắn nội bộ, cooldown giữa 2 lần nhặt |

## 3. Điều kiện thả nổi (sweep)

Cron **15 phút/lần** quét đơn thỏa **tất cả**:

1. `staff` không trống, `deleted_at` trống, không phải đơn hủy (`status=6` / `invoice_status=2`).
2. Trạng thái sale hiện tại (`assessment.sales_status_code`) **không nằm trong danh sách
   "được cứu"** của config. Đơn đã lên hóa đơn KiotViet (đã chốt thật) mặc nhiên được cứu.
3. Mốc tác nghiệp gần nhất — `assessment.updated_at`, fallback `order.created_at` khi chưa
   có assessment — cách hiện tại **≥ X ngày**.
4. Không có lịch hẹn còn hiệu lực: `assessment.next_followup_at` trống **hoặc đã quá hạn**.
   Lịch hẹn tương lai = được cứu đến hết hẹn (quá hẹn mà không tác nghiệp thì các điều kiện
   khác quyết định).
5. Số lần đã nổi của đơn **< N**.
6. Tuổi đơn (`created_at`) **≤ Z ngày** nếu Z > 0 (Z = 0 → không giới hạn) — data quá cũ
   không thả, tránh dồn rác vào kho.

Đơn thỏa hết → tạo `FloatingPoolEntry(status=floating)`, ghi `previous_staff` + `entered_at`,
`float_count` = số entry đã có của đơn + 1. **Đơn vẫn thuộc sale cũ** cho tới khi có người nhặt.

**Tự gỡ (cũng trong sweep):** entry đang nổi mà đơn được cứu lại (tích trạng thái cứu, đặt
lịch hẹn tương lai, có tác nghiệp mới sau `entered_at`, đơn hủy/xóa) → `status=removed`,
`removed_reason` tương ứng. Vòng bị gỡ vì "sale cũ chăm lại" **không** tính vào N (điều kiện 5
đếm theo entry có kết cục `picked` hoặc entry đang nổi).

## 4. Nhặt số (claim)

`POST /api/orders/floating-pool/entries/{id}/claim`:

- `select_for_update` trên entry; entry phải đang `floating` — trượt (người khác nhặt trước /
  đã gỡ) → **409** kèm thông báo "Số đã có người nhặt".
- Chặn **403** nếu `user == previous_staff` (không cho sale nhặt lại đơn mình vừa bị thu hồi —
  muốn giữ số thì tích trạng thái, đừng để bị thả).
- Kiểm tra quota: số entry `picked_by=user` có `picked_at` trong ngày (giờ +07) **< M**
  (M = 0 → không giới hạn). Vượt → **400** kèm detail "Đã dùng hết lượt nhặt hôm nay".
- Đạt: `Order.staff = user` (+ `updated_at`), entry → `status=picked, picked_by, picked_at`.
  Tác nghiệp mốc mới = thời điểm nhặt (đồng hồ X ngày đếm lại — nhặt về mà lại bỏ bê X ngày
  thì vòng lặp thả tiếp, trừ khi đã chạm N).

Toàn bộ trong 1 transaction, pattern như `LeadAssignmentService.pick_staff_for` /
`transfer`.

## 5. Ẩn số điện thoại

- List API trả số **đã che từ backend** (`0935***234`) khi config `hide_phone=True` và user
  không có quyền `floating-pool:edit`; số đầy đủ không xuống trình duyệt lúc đơn đang nổi.
- Nhặt xong xem số ở trang Đơn hàng như bình thường (đơn đã là của mình).
- Tìm kiếm: tên KH tìm thường (icontains); tìm theo SĐT **chỉ dành cho user thấy số đầy đủ**
  (quyền edit hoặc `hide_phone=False`) và phải nhập **đủ số chính xác** (exact match sau
  normalize) — user bị che số không dò được membership qua search (chống oracle dò 3 số cuối).

## 6. Cấu hình — `FloatingPoolConfig` (bảng 1 dòng)

| Trường | Kiểu | Mặc định | Ý nghĩa |
|---|---|---|---|
| `enabled` | bool | False | Tắt = sweep không chạy, trang ẩn khỏi sidebar |
| `stale_days` (X) | int | 3 | Số ngày không tác nghiệp thì thả nổi |
| `saved_status_codes` | ArrayField | `[]` (khuyến nghị set = mã "Chốt đơn") | Trạng thái được cứu |
| `max_float_count` (N) | int | 10 | Số lần nổi tối đa/đơn |
| `daily_claim_limit` (M) | int | 10 | Số lượt nhặt tối đa/ngày/sale; 0 = không giới hạn |
| `max_order_age_days` (Z) | int | 0 | Tuổi đơn tối đa được thả; 0 = không giới hạn |
| `hide_phone` | bool | True | Che SĐT trong kho |
| `updated_by`, `updated_at` | — | — | Audit |

API: `GET/PUT /api/orders/floating-pool/config` (PUT gated `floating-pool:edit`).

## 7. Dữ liệu — `FloatingPoolEntry`

```
order            FK Order (PROTECT xóa mềm theo order)
previous_staff   FK User SET_NULL     # sale bị thu hồi
status           char: floating | picked | removed
float_count      int                  # vòng thứ mấy của đơn này
entered_at       datetime
picked_by        FK User SET_NULL, null
picked_at        datetime null
removed_reason   char blank: saved | cancelled   # saved = được chăm lại, cancelled = đơn hủy/xóa
created_at / updated_at
```

- **Partial unique index**: `order_id` where `status='floating'` — 1 đơn chỉ 1 entry đang nổi.
- Index: `(status, entered_at)` cho list; `(picked_by, picked_at)` cho quota ngày.
- Entry giữ vĩnh viễn làm lịch sử + nguồn đếm `float_count`.

Đặt tại `apps/orders` (models + service `FloatingPoolService` trong
`application/services/`, view trong `transport/views/order_views_impl/`), cùng nhà với
lead-assignment vì chung vòng đời Order.

## 8. Sweep — vận hành

- Management command `sweep_floating_pool` (chạy tay/backfill được) + đăng ký cron
  **15 phút** theo pattern cron hiện có (như pending-shipment-scan).
- Idempotent: chạy lại không tạo entry trùng (partial unique + `get_or_create`).
- Query đánh index sẵn có (`ord_assessment_updated_idx`, `ord_assessment_sales_idx`);
  batch update, không N+1.

## 9. API

| Method & path | Quyền | Mô tả |
|---|---|---|
| `GET /api/orders/floating-pool/entries` | `floating-pool:view` | List entry đang nổi: kênh/nguồn, tên KH, SĐT (che), khách cũ/mới (`is_new_customer`), trạng thái sale gần nhất, sale trước + team, `float_count`, `entered_at`, quota còn lại hôm nay của user. Filter: khoảng ngày vào kho, kênh, từ khóa (tên icontains / SĐT exact). Envelope `{items, total}`, phân trang số. |
| `POST /api/orders/floating-pool/entries/{id}/claim` | `floating-pool:view` | Nhặt số (mục 4) |
| `GET /api/orders/floating-pool/config` | `floating-pool:view` | Đọc config |
| `PUT /api/orders/floating-pool/config` | `floating-pool:edit` | Sửa config |

RBAC: section mới `floating-pool` (view/edit) theo scheme section:action hiện hành.

## 10. Frontend — `frontend/src/features/floating-pool/`

- `pages/FloatingPoolPage.tsx` — bảng: Nguồn (kênh), Khách hàng, SĐT (che), Khách cũ/mới
  (badge), Trạng thái gần nhất, Sale trước đó + team, Số vòng, Ngày vào kho, nút **Nhặt số**.
  Header hiện quota "đã nhặt 3/10 hôm nay". Filter: khoảng ngày, kênh, ô tìm kiếm.
  Phân trang `PaginationPages`. Loading `LogoSpinner`.
- Nhặt: confirm dialog → mutation; 409 → toast "Số đã có người nhặt" + refetch; hết quota →
  nút disabled kèm tooltip.
- `pages/FloatingPoolConfigPage.tsx` (hoặc tab trong trang, gated `floating-pool:edit`) —
  form config, multi-select trạng thái được cứu từ danh mục trạng thái sale.
- Convention bắt buộc: React Query (`queries.ts` per-feature), API module
  `src/lib/api/floating-pool.ts` typed từ `schema.d.ts` (chạy `gen:api` + prettier),
  components từ `components/ui`, **không** `react-select`.
- Sidebar: mục "Kho số thả nổi" nhóm Sale, ẩn khi `enabled=False` hoặc thiếu quyền.

## 11. Edge cases

- **2 sale nhặt cùng lúc** → lock, người sau 409.
- **Sale cũ chăm lại khi đơn đang nổi** → sweep gỡ entry (`saved`), không tính vòng.
- **Đơn hủy/xóa/chốt khi đang nổi** → sweep gỡ (`cancelled`/`saved`); claim re-check trạng
  thái đơn trong transaction trước khi gán.
- **Đơn lên hóa đơn KiotViet khi đang nổi** → sweep/claim gỡ (`saved`) — đã chốt thật, không
  phải bỏ bê.
- **Đơn đổi sale (transfer) khi đang nổi** → `order.staff_id != entry.previous_staff_id` →
  sweep/claim gỡ (`saved`) — sale mới nhận đơn không có lỗi bỏ bê, không phải chờ nổi lại.
- **Đơn chạm N vòng** → không thả nữa, nằm im ở sale cuối (lead chết — báo cáo sau nếu cần).
- **Config đổi X/N/Z** → chỉ ảnh hưởng lần sweep sau, không hồi tố entry cũ.
- **Sale nghỉ việc (`deleted_at`)** → đơn của họ vẫn quét thả nổi bình thường (staff còn giá
  trị); claim chỉ cho user active.
- **Múi giờ**: mốc "trong ngày" của quota tính theo `timezone.localdate()` (+07), tránh lỗi
  UTC như bài học monthly-report.

## 12. Kiểm thử

Unit test (pytest, container `desi-order-web-test`, DB unique):

1. Sweep: từng nhánh điều kiện 1–6 (có/không trạng thái cứu, lịch hẹn tương lai/quá hạn,
   quá N vòng, quá Z tuổi, đơn hủy) + idempotent chạy 2 lần.
2. Tự gỡ: tích trạng thái cứu / đặt hẹn / hủy đơn khi đang nổi.
3. Claim: thành công đổi staff; race 2 user (409); previous_staff bị chặn (403); quota ngày
   (M và M=0); re-check đơn hủy.
4. Che số: hide_phone on/off, quyền edit thấy full, search exact phone.
5. FE: test bảng + nút nhặt disabled khi hết quota (vitest, theo mẫu feature orders).

## 13. Sprint

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| 1 — Backend core | Models (`FloatingPoolEntry`, `FloatingPoolConfig`) + migration, `FloatingPoolService` (sweep + claim + tự gỡ), management command + cron 15 phút, config/entries/claim API, RBAC section `floating-pool`, 40 BE unit test | Đã làm |
| 2 — API list + FE | List API (mask số + search exact + filter + quota), `gen:api` typed client, `queries.ts`, trang kho số (`FloatingPoolPage`), `ClaimButton`, config dialog, route/sidebar/i18n, 2 FE test | Đã làm |
| 3 — Hoàn thiện | Smoke test trên prod với config tắt → bật dần (X=3, N=10, M=10), theo dõi 1 tuần, tinh chỉnh default | Chưa làm (chờ deploy prod) |

**Ghi chú tiến độ:** cập nhật bảng này sau mỗi sprint (đã làm / chưa làm / phát sinh).

### Ghi chú triển khai (2026-07-16)

Sai khác so với thiết kế gốc (đã cân nhắc, không phải thiếu sót):

- **(a) Config = dialog trong trang, không phải trang riêng.** §10 có nêu phương án
  "hoặc tab trong trang"; đã chọn dialog mở từ `FloatingPoolPage` (gated
  `floating-pool:edit`) thay vì route `FloatingPoolConfigPage` riêng — giảm 1 route/1 menu
  item cho một form ít khi sửa.
- **(b) Sidebar không ẩn runtime theo `enabled`.** Mục "Kho số thả nổi" luôn hiện với ai có
  quyền `floating-pool:view` (không đọc config để ẩn/hiện động); khi `enabled=False`, trang
  tự hiện empty-state thay vì thả nổi số nào. Tránh gọi thêm API config chỉ để quyết định
  render sidebar.
- **(c) `Checkbox` → `Switch`.** Các cờ bool trong config dialog (`enabled`, `hide_phone`)
  dùng `Switch` (component `ui`) thay vì `Checkbox` cho nhất quán với các form cấu hình
  on/off khác trong hệ thống.
- **(d) `exclude()` same-row fix bằng subquery.** Điều kiện "đơn chưa có entry đang nổi"
  trong sweep ban đầu dùng `.exclude(entries__status=floating)` — lỗi self-join loại nhầm
  đơn có entry `picked/removed` khác dòng cùng đơn. Fix bằng subquery
  (`~Exists`/`Subquery` trên `FloatingPoolEntry.objects.filter(status=floating)`) để chỉ so
  đúng trạng thái entry hiện tại, không bị nhiễu bởi các entry lịch sử khác status.
- **(e) Claim dùng deferred-raise thay vì raise ngay trong transaction.** `claim()` gom lỗi
  409/403/400 vào biến rồi raise **sau khi** thoát khối `transaction.atomic()` (thay vì raise
  ngay bên trong) — để `select_for_update` lock được nhả đúng chỗ và tránh trường hợp
  Django rollback luôn state `REMOVED` vừa ghi (tự gỡ phát hiện trong lúc claim) khi request
  claim đó sau đó bị từ chối vì lý do khác.

**Ghi chú deploy:**

1. Chạy migration: `orders` migration `0106` (FloatingPoolEntry/FloatingPoolConfig) +
   `users` migration `0029` (seed permission `floating-pool:view/edit`).
2. Restart celery beat để nhận schedule mới `orders-floating-pool-sweep` (`*/15`, chạy trên
   queue mặc định `CELERY_TASK_DEFAULT_QUEUE` — task nhẹ, không cần queue `orders` riêng).
3. Trước khi bật `enabled=True` lần đầu, **BẮT BUỘC** set `saved_status_codes` (tối thiểu các
   mã chốt đơn — thiếu là đơn đã chốt vẫn bị thả) và **nên** set `max_order_age_days`
   (khuyến nghị Z=30) để tránh flood kho với data lịch sử ngay lần sweep đầu.
4. Sau khi smoke test xong, bật `enabled=True` qua UI (dialog config) với
   `stale_days=3, max_float_count=10, daily_claim_limit=10` (X=3/N=10/M=10) theo kế hoạch
   Sprint 3. Có thể chạy tay lần sweep đầu ngoài giờ bằng
   `python manage.py sweep_floating_pool` để kiểm soát thời điểm flood đầu tiên trước khi
   bật cron tự động.

## 14. Nâng cấp tương lai (không làm bây giờ)

- Leo thang hiển thị theo team → toàn phòng (thêm `team_visible_until` vào entry).
- Đếm theo cuộc gọi Zenify: cuộc gọi ≥ T giây tới đúng SĐT = tác nghiệp hợp lệ (reset đồng hồ).
- Chia thẳng data mới vào kho theo nguồn (mode "pull").
- Thông báo Lark khi có số vào kho; báo cáo lead chết (chạm N vòng).
- Khi trang Sale Contacts xong: thêm cột lịch sử khách/contact vào kho số.
