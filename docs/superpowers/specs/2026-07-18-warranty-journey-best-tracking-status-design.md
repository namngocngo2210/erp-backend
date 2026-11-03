# Trạng thái vận chuyển tự động theo Best cho hành trình bảo hành

**Ngày:** 2026-07-18
**Trạng thái:** Đã duyệt thiết kế, chờ triển khai

## Bối cảnh

Hành trình bảo hành (`WarrantyJourney`) có 3 mã vận đơn: `new_tracking_code` (gửi đi),
`recall_code` (thu hồi), `return_tracking_code` (gửi lại khách). Trạng thái vận chuyển
hiện do nhân viên chọn tay (`JourneyStatus` 8 giá trị + `return_status`). Bảo hành CHỈ
đi Best Express (đã tích hợp module `apps/best_express`) → trạng thái VC hiển thị được
tự động theo mã trạng thái chi tiết của Best.

## Quyết định đã chốt

| Câu hỏi | Quyết định |
|---|---|
| Hiển thị ở đâu | Hành trình phiếu bảo hành |
| Áp cho mã nào | Cả 3 mã, mỗi mã 1 badge riêng |
| Thay hay bổ sung trạng thái tay | Thay: gỡ select tay `status` + `return_status` trên UI; trạng thái VC hiển thị = tự động theo Best |
| Carrier khác (GHN/VTP) | Không xử lý — bảo hành chỉ đi Best |
| Quét xuất kho | GIỮ hook set "Đã đóng hàng" (trạng thái nội bộ), mở rộng khớp cả 3 mã |
| Quét hàng hoàn khớp mã BH | Set `warehouse_received_at` = hôm nay + trạng thái nội bộ "Đã nhận hàng hoàn"; KHÔNG ghi sổ tồn; không rơi vào phiếu nháp mã lạ |
| Ưu tiên khớp khi quét | Đơn hàng (shipping_code/recall) TRƯỚC, không khớp mới thử hành trình BH |

## Bảng nhóm trạng thái VC (11 nhóm — chỉ dùng cho hiển thị BH)

| Nhóm (key) | Nhãn | Mã Best chi tiết |
|---|---|---|
| `created` | Tạo mới | Chưa có mã VC hoặc chưa có dữ liệu trạng thái |
| `picking_up` | Đang lấy hàng | 211 |
| `pickup_failed` | Lấy hàng không thành công | 203 |
| `in_transit` | Đang trung chuyển | 202, 301, 302, 303, 304, 309 |
| `delivering` | Đang giao hàng | 601 |
| `redelivery` | Chờ giao lại | 604 |
| `delivered` | GTC | 666 |
| `returning` | Đang chuyển hoàn | 605, 701, 702, 703, 704, 705, 707 |
| `returned` | Đã trả hàng | 708 |
| `lost` | Thất lạc | 777 |
| `damaged` | Hư hỏng | 1000 |

Mã lạ (không trong bảng, kể cả "2" hủy của Best) → giữ nhóm theo dữ liệu gần nhất đã
biết; chưa từng có dữ liệu → `created`. KHÔNG sửa enum/map nội bộ của module
`best_express` (bảng đó phục vụ trang Best shipments, map 202/707 khác).

## Backend

### Model (`apps/warranty/models.py`)

`WarrantyJourney` thêm 4 field:
- `new_tracking_best_status` (CharField 32, blank) — mã Best thô mới nhất của mã gửi đi
- `recall_best_status` (CharField 32, blank) — của mã thu hồi
- `return_tracking_best_status` (CharField 32, blank) — của mã gửi lại
- `tracking_synced_at` (DateTimeField null) — lần trace thành công gần nhất

Migration 0015. `status`/`return_status` cũ GIỮ NGUYÊN cột (trạng thái nội bộ cho
luồng quét kho), nhưng bị gỡ khỏi `JOURNEY_EDITABLE_FIELDS` — API không nhận ghi tay
nữa; auto new↔created + hook quét vẫn set trực tiếp như cũ.

### Mapping thuần (`apps/warranty/tracking_status.py`)

- `map_best_vc_group(code: str) -> str | None`: mã chi tiết → key nhóm; mã lạ/rỗng → None.
- `journey_vc_groups(journey) -> dict`: trả `{new, recall, return, main}` — mỗi mã:
  không có mã → None (FE ẩn badge); có mã → nhóm từ mã Best thô đã lưu, chưa có dữ liệu
  → `created`. `main` = nhóm của mã ưu tiên: `return_tracking_code` > `recall_code` >
  `new_tracking_code`; không có mã nào → `created`.

### Cron trace (`apps/warranty/tracking_sync_service.py` + celery beat 10 phút)

- Thu thập mã (cả 3 field, khác rỗng, dedup) của journey thuộc claim mở
  (`deleted_at` null, `status` ∉ {Hoàn thành 9, Hủy 11}).
- Trace qua `BestExpressClient(account).trace_query` (chunk `TRACE_MAX_CODES`),
  account = BestExpressAccount `is_active=True` ưu tiên `environment=production`
  (lấy bản ghi đầu; không có account → skip + log warning, không lỗi).
- KHÔNG đi qua `store_event/apply_event` (tránh rác event unmatched) — đọc thẳng
  `traceLogs` mới nhất theo `modified_at`, map `code_best` → mã trạng thái, update
  field tương ứng của journey khi đổi + `tracking_synced_at`.
- Lỗi API (BestExpressError/network) → log, bỏ qua lần đó (cron 10' sau chạy lại).

### Quét kho (`apps/inventory` gọi sang warranty qua service — lazy import như hiện tại)

- **Quét xuất:** `mark_packed_by_tracking(code)` mở rộng khớp
  `new_tracking_code | recall_code | return_tracking_code` (hiện chỉ khớp
  `new_tracking_code`); hành vi giữ nguyên: nâng lên `packed` nếu rank thấp hơn.
- **Quét hoàn:** thêm `mark_returned_by_tracking(code)` — khớp 1 trong 3 mã →
  set `status = returned` (nội bộ), `warehouse_received_at = hôm nay` nếu đang trống;
  trả journey để caller hiển thị "khớp hành trình BH #x". Wire vào luồng quét hoàn ở
  điểm mã không khớp Đơn hàng, TRƯỚC khi rơi vào phiếu nháp mã lạ; đồng thời wire vào
  cron khớp mã pending cho kind hoàn.
- KHÔNG ghi sổ tồn khi khớp BH.

### Serializer / API

- `WarrantyJourneySerializer` thêm read-only: 3 field mã Best thô + `vc_groups`
  (dict `{new, recall, return, main}` — key nhóm). Regen schema.yaml + gen:api
  (+ prettier).
- `status`/`return_status` vẫn serialize (đọc) nhưng không nhận ghi.

## Frontend (trang chi tiết phiếu BH)

- Map key nhóm → nhãn + màu badge (11 nhóm): created xám, picking_up xanh dương nhạt,
  pickup_failed đỏ nhạt, in_transit chàm, delivering xanh dương, redelivery amber,
  delivered xanh lá, returning cam, returned teal, lost đỏ, damaged đỏ đậm.
- Badge nhóm hiển thị cạnh từng ô mã vận đơn (chỉ khi có mã); badge `main` ở header
  hành trình thay vị trí select trạng thái cũ.
- GỠ select `status` + select `return_status` khỏi form hành trình (không gửi 2 key
  này trong payload nữa).

## Test

- BE: map đủ 11 nhóm + mã lạ → None; `journey_vc_groups` ưu tiên return > recall > new
  + fallback `created`; sync service mock client (update field, không account → skip,
  lỗi API không nổ); `mark_packed_by_tracking` khớp 3 field; `mark_returned_by_tracking`
  set returned + ngày kho nhận (không đè ngày đã có); quét hoàn khớp BH không tạo phiếu
  nháp; API từ chối ghi status tay (bị bỏ qua).
- FE: badge theo mã, ưu tiên main, form không còn select trạng thái.

## Không làm (YAGNI)

- Không sửa module best_express (enum/map/webhook).
- Không áp GHN/VTP, không backfill lịch sử, không log lịch sử trạng thái VC.
- Không đổi luồng ghi sổ tồn kho.

## Triển khai

Branch `feat/warranty-claim-status-flow` (worktree `.claude/worktrees/warranty-claim-statuses`),
chung MR với bộ 11 trạng thái phiếu (cùng session).
