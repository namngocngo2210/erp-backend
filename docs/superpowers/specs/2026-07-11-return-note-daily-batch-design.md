# Gộp phiếu trả hàng theo ngày (1 phiếu / ngày / kho)

Ngày: 2026-07-11
Branch: `feat/return-note-daily-batch`
Trạng thái: ✅ **ĐÃ TRIỂN KHAI** (migration `0022_return_entry` + lệnh `merge_return_notes_by_day`).
Mô hình hiện hành + **runbook deploy prod**: **`docs/erp-return-daily-note.md`**.

## Vấn đề

Trang **Phiếu kho** rối vì phiếu trả hàng sinh quá nhiều: mỗi đơn hoàn = 1 `ReturnNote` riêng
(quét 20 mã trong 1 lần → 20 phiếu). Hiện chỉ có **gộp hiển thị** theo `scan_batch_id` (đợt quét),
không phải chứng từ thật. Trang cũng chưa có bộ lọc khoảng ngày.

## Mục tiêu

1. Mỗi **ngày / kho** đúng **1 phiếu trả hàng** có mã phiếu thật (chứng từ, không phải gộp hiển thị).
2. Trang Phiếu kho có **bộ lọc khoảng ngày** như các trang khác.
3. Giữ nguyên hành vi kho đang chạy đúng: quét khớp mã → cộng tồn + đơn `RETURNED` **ngay lập tức**;
   chống khớp hoàn 2 lần; huỷ lẻ được 1 đơn quét nhầm.

## Quyết định thiết kế (đã chốt với CTO)

| # | Quyết định | Lý do |
|---|---|---|
| 1 | Gộp **chứng từ thật** (không phải gộp hiển thị) | Yêu cầu "mỗi ngày 1 phiếu và có mã phiếu" |
| 2 | **Ghi sổ ngay ở mức đơn** (phiếu ngày chỉ là chứng từ gom) | Giữ tính tức thời của tồn kho + huỷ lẻ được |
| 3 | Khoá gộp = **(ngày, kho)**; loại hoàn xuống cấp dòng | Ít phiếu nhất |
| 4 | Phiếu tạo tay **cũng gộp** vào phiếu ngày | Không còn phiếu lẻ xen kẽ |
| 5 | **Chốt phiếu thủ công** (không auto-close) | Kho kiểm soát; chốt rồi → phiếu mới `-02` |
| 6 | **Gộp hồi tố** toàn bộ lịch sử | Nhìn đồng nhất; chấp nhận rewrite `reference_id` có đối soát |

## 1. Mô hình dữ liệu

Ba tầng thay cho hai tầng hiện tại:

### `ReturnNote` — phiếu ngày (thứ hiển thị trên trang Phiếu kho)

| Trường | Ghi chú |
|---|---|
| `code` | `PTH-YYYYMMDD-K{warehouse_id}` (+ `-02`, `-03` nếu ngày đó có phiếu đã chốt trước đó), unique |
| `warehouse` | FK, bắt buộc |
| `doc_date` | `DateField` — ngày theo **giờ VN** (`Asia/Ho_Chi_Minh`), không dùng `created_at::date` UTC |
| `status` | `open` (Đang mở) / `closed` (Đã chốt) |
| `closed_at`, `closed_by` | khi chốt |
| `created_by`, `created_at` | |

Bỏ khỏi cấp phiếu: `return_type`, `source_order`, `shipping_code`, `order_status_before`,
`posted_at`, `scan_batch_id`.

Ràng buộc: `UniqueConstraint(doc_date, warehouse, status=open)` — mỗi ngày/kho tối đa **1 phiếu đang mở**.

### `ReturnEntry` — nhóm đơn trong phiếu (mới, **không có mã riêng**)

| Trường | Ghi chú |
|---|---|
| `return_note` | FK → phiếu ngày |
| `source_order` | FK Order, nullable (quét chưa khớp đơn) |
| `shipping_code` | mã vận đơn đã quét |
| `customer_name`, `customer_phone`, `source_note`, `note` | chuyển từ `ReturnNote` cũ |
| `status` | `draft` / `posted` / `cancelled` |
| `posted_at`, `order_status_before` | chuyển từ `ReturnNote` cũ |
| `scan_batch_id` | giữ để truy vết đợt quét (không còn dùng để gộp hiển thị) |

Ràng buộc chuyển nguyên từ `ReturnNote`:
- `UniqueConstraint(shipping_code) WHERE shipping_code != '' AND source_order IS NULL AND status='draft'`
- Guard trong service: 1 `shipping_code` chỉ có **1 entry chưa huỷ** (chống khớp hoàn 2 lần).

### `ReturnNoteLine`

- FK đổi từ `return_note` → `entry`.
- `return_type` **bắt buộc** (bỏ cơ chế kế thừa từ phiếu — phiếu ngày không còn loại).
- `UniqueConstraint(entry, product)` thay cho `(return_note, product)`.

### `StockMovement`

`reference_type` đổi `"return"` → `"return_entry"`, `reference_id` = `ReturnEntry.id`.
Bắt buộc: huỷ lẻ 1 đơn phải đảo đúng movement của đơn đó, không phải cả ngày.

## 2. Nghiệp vụ

### Tạo (quét hoặc tạo tay)

```
resolve_open_note(doc_date=today_vn, warehouse) # advisory lock theo (ngày, kho)
  → phiếu đang mở, hoặc tạo mới (mã PTH-YYYYMMDD-K{wh}[-NN])
create entry + lines
post_entry()  # cộng tồn, đơn → RETURNED (nguyên logic create_and_post_return hiện tại)
```

Advisory lock key theo `(doc_date, warehouse_id)` để 2 người quét đồng thời không đẻ 2 phiếu.
Toàn bộ trong 1 transaction: lỗi ở bất kỳ đâu → không phiếu, không entry, không movement,
không đổi status đơn (giữ nguyên bất biến của `create_and_post_return`).

### Ghi sổ / Guard chống cộng tồn kép

Logic `_guard_double_credit` giữ nguyên, chỉ đổi vế: tính `returned_by_product(order,
exclude_entry_id=...)` theo `ReturnEntry` đã ghi sổ của đơn thay vì `ReturnNote`.

### Huỷ

- **Huỷ 1 entry**: đảo movement của entry (reference `return_entry`/`entry.id`), trả đơn về
  `order_status_before`. Cho phép **kể cả khi phiếu đã chốt** — huỷ là sửa sai sổ sách,
  không phải sửa phiếu. Quyền `returns:approve`.
- **Huỷ cả phiếu ngày**: huỷ tuần tự mọi entry `posted`, FE confirm có đếm số đơn.

### Chốt phiếu

`POST /return-notes/{id}/close` → `status=closed`. Quét tiếp trong ngày → không thấy phiếu mở
→ tạo phiếu mới cùng ngày, mã `...-02`. Không có auto-close, không cron.

### Khớp mã treo + cron auto-post

`PendingShipmentScan` khớp → tạo entry trong phiếu ngày **của ngày quét gốc** (`scan.created_at`
giờ VN) nếu phiếu ngày đó còn mở; nếu đã chốt → phiếu mới của ngày đó. Hành vi với đơn/tồn không đổi.

## 3. API (`apps/inventory`)

| Endpoint | Thay đổi |
|---|---|
| `POST /api/inventory/returns` | Trả `{entry, note}` thay vì note đơn lẻ. Payload giữ nguyên (+ `return_type` bắt buộc ở mỗi line) |
| `PATCH /api/inventory/returns/{entry_id}` | Sửa entry nháp (khớp đơn thủ công) — nguyên logic `update_draft` |
| `POST /api/inventory/returns/{entry_id}/cancel` | Huỷ 1 nhóm đơn (`returns:approve`) |
| `POST /api/inventory/return-notes/{id}/close` | Chốt phiếu ngày (`returns:approve`) |
| `POST /api/inventory/return-notes/{id}/cancel` | Huỷ cả phiếu (`returns:approve`) |
| `GET /api/inventory/return-notes/{id}` | Chi tiết phiếu ngày: entries + lines |
| `GET /api/inventory/ledger-documents` | Dòng `kind=return` = phiếu ngày: `code`, kho, `doc_date`, `entry_count`, `qty_total`, `status`, `cancelled_count`. Filter `date_from`/`date_to` **đã có sẵn** |
| `POST /api/inventory/returns/bulk-post` | **Gỡ bỏ** — phiếu ngày thay thế |

RBAC giữ nguyên: `returns:view` để xem, `returns:approve` để huỷ/chốt.

## 4. Frontend

- **`StockDocumentsPage`**: thêm `DateFilter` (variant `inline`, như trang Vận đơn) — preset
  Hôm nay / 7 ngày / Tháng này + tuỳ chọn; mặc định **Tháng này**. Thêm **phân trang thật**
  (hiện `offset` cứng = 0, limit 100 — backend đã trả `total` chính xác).
- Dòng phiếu trả: `PTH-20260711-K1 · Kho HN · 14 đơn · 23 SP · Đang mở`.
- **`ReturnNoteDetailDialog`** viết lại: bảng entry (đơn / mã VĐ / khách / SL / trạng thái),
  mỗi dòng có nút **Huỷ**; header có **Chốt phiếu** / **Huỷ phiếu**.
- **Gỡ** `ledger-grouping.ts` (gộp theo `scan_batch_id`) + nút "Ghi sổ cả đợt" + `useBulkPostReturns`.
- Toast quét đổi hiển thị: mã phiếu ngày + mã vận đơn (không còn mã phiếu cấp đơn).
- `ReturnCard`, `ReturnTab` (lịch sử nhập xuất), `flow-shared`: đọc entry thay vì note.

## 5. Migration (prod)

**Bước 1 — schema + data migration (an toàn, dừng ở đây vẫn chạy đúng)**
1. Tạo bảng `stock_return_entries`.
2. Mỗi `ReturnNote` cũ → đúng 1 entry (copy `source_order`, `shipping_code`, khách, `status`,
   `posted_at`, `order_status_before`, `scan_batch_id`); lines trỏ sang entry;
   `return_type` rỗng ở line → điền từ `note.return_type`.
3. `StockMovement` `("return", note_id)` → `("return_entry", entry_id)`.
4. Phiếu cũ giữ mã cũ (`THyyyymmdd-0001`), `doc_date` = ngày tạo (giờ VN), `status=closed`.
   → Số phiếu **chưa đổi**, hệ thống chạy đúng ngay lập tức.

**Bước 2 — lệnh gộp hồi tố `merge_return_notes_by_day --dry-run`**
- Gom entry theo `(doc_date, warehouse)` vào 1 phiếu ngày mới mã `PTH-...`, xoá phiếu rỗng.
- **Đối soát bắt buộc**: in tổng tồn theo `(kho, sản phẩm, trạng thái)` + đếm movement **trước và
  sau**; lệch 1 đơn vị → abort + rollback. Không đụng `qty_delta`, chỉ đổi `return_note_id` của entry.
- Backup DB trước khi chạy.

## 6. Test

Backend (pytest):
- Quét 2 mã cùng ngày/kho → 1 phiếu, 2 entry.
- Quét khi phiếu đã chốt → phiếu mới `-02`.
- 2 request tạo đồng thời → đúng 1 phiếu (advisory lock).
- Huỷ 1 entry: tồn đảo đúng phần của entry đó, entry khác không đổi, đơn về trạng thái cũ.
- Huỷ entry sau khi phiếu đã chốt → cho phép.
- Guard: 1 `shipping_code` không tạo được 2 entry chưa huỷ.
- Guard cộng tồn kép theo entry.
- Migration hồi tố: dựng dữ liệu cũ (posted + cancelled + draft lẫn lộn) → gộp → **tồn không đổi**.

Frontend (vitest):
- Filter khoảng ngày đẩy đúng `date_from`/`date_to` vào query.
- Dialog phiếu ngày render entry, nút Huỷ/Chốt gate theo `returns:approve`.

## Rủi ro & giới hạn

- **Mất mã phiếu cấp đơn** (`THyyyymmdd-0007`): đã chấp nhận. Đối chiếu chuyển sang mã phiếu ngày
  + mã vận đơn/mã đơn.
- Gộp hồi tố rewrite `reference_id` của movement đã chốt — giảm rủi ro bằng dry-run + đối soát tồn
  + backup. Không đổi số lượng nên tồn về mặt toán học không thể đổi.
- Loại hoàn bắt buộc ở line: entry cũ có line `return_type` rỗng sẽ được backfill từ note trong
  migration; sau đó UI bắt buộc chọn.
