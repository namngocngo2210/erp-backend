# Đợt quét trả hàng — gộp nhóm phiếu kho + ghi sổ cả đợt

> ⚠️ **ĐÃ THAY THẾ (2026-07-11) — doc lịch sử.** Gộp **hiển thị** theo `scan_batch_id` đã bị gỡ; thay
> bằng **chứng từ thật**: `ReturnNote` nay là **phiếu NGÀY** (`PTH-YYYYMMDD-K{wh}`, 1 phiếu / ngày / kho),
> quan hệ 1 ↔ 1 với đơn nằm ở tầng mới **`ReturnEntry`**. `ledger-grouping.ts`, nút "Ghi sổ cả đợt" và
> endpoint `POST /returns/bulk-post` **đã bị xoá**. `scan_batch_id` còn tồn tại trên `ReturnEntry` nhưng
> chỉ để truy vết đợt quét, không dùng gộp hiển thị.
> → Mô hình hiện hành: **`docs/erp-return-daily-note.md`**.

## Vấn đề (bối cảnh khi đó)

Quét trả hàng hàng loạt (dialog "Quét trả hàng (hàng loạt)" trên trang Phiếu kho)
tạo **1 phiếu trả cho mỗi đơn** — thiết kế khi đó (guard chống cộng tồn trùng,
mark/revert trạng thái đơn, khớp đơn pending-scan đều cần mapping 1 chứng từ
↔ 1 đơn; nay mapping đó là `ReturnEntry`). Hệ quả UX: quét 30 đơn → 30 dòng rời
rạc trên trang Phiếu kho, không thấy được "lần quét" nào sinh ra chúng, và phải
bấm Ghi sổ 30 lần.

## Giải pháp (đã bị thay thế): khái niệm "Đợt quét" (scan batch)

Giữ nguyên sổ sách per-đơn. Chỉ thêm 1 nhãn nhóm + thao tác hàng loạt:

1. **`scan_batch_id` (UUID, nullable)** trên `ReturnNote` và `PendingShipmentScan`.
   - FE sinh `crypto.randomUUID()` cho mỗi phiên xác nhận của dialog quét.
   - Mọi phiếu trả tạo từ lần xác nhận đó mang cùng batch id.
   - Mã chưa khớp đơn đi vào bảng chờ (`PendingShipmentScan`) cũng mang batch id;
     khi cron khớp xong tạo phiếu, phiếu **kế thừa batch id** → tự về đúng đợt.
   - NULL = phiếu tạo lẻ (form tay, luồng cũ) — hiển thị như hiện tại.

2. **`POST /api/inventory/returns/bulk-post`** — body `{ids: [int]}`, ghi sổ từng
   phiếu độc lập qua `ReturnNoteService.post_return` (mỗi phiếu 1 transaction);
   trả `{results: [{id, ok, code?, error?}]}` — phiếu lỗi không chặn cả đợt.
   Quyền: `returns` section (như ghi sổ lẻ).

3. **Trang Phiếu kho**: các dòng cùng `scan_batch_id` (≥2 phiếu trong trang hiện
   tại) gộp thành 1 nhóm collapse, mặc định **thu gọn** — 1 lần quét ≈ 1 dòng.
   Header nhóm: số phiếu, số nháp/đã ghi sổ, kho, ngày, nút **"Ghi sổ cả đợt"**
   (chỉ đếm phiếu nháp đủ điều kiện: đã khớp đơn + đã phân loại).

## Thay đổi chi tiết

### Backend
- `models.py`: `ReturnNote.scan_batch_id`, `PendingShipmentScan.scan_batch_id`
  (UUIDField null/blank), index `returnnote_scanbatch_idx`.
- Migration `0018_scan_batch_id`.
- `ReturnNoteService.create_return(..., scan_batch_id=None)`.
- `PendingScanService.create_batch(..., scan_batch_id=None)`;
  `_complete_return` truyền `scan.scan_batch_id` khi tạo phiếu.
- Serializers: `CreateReturnNoteSerializer` + `CreatePendingScansBatchSerializer`
  nhận `scan_batch_id` optional; `ReturnNoteSerializer` expose field.
- `ledger_documents`: thêm cột `scan_batch` vào union (stock = NULL), item trả
  `scan_batch_id`.
- View mới `return_bulk_views.bulk_post_returns` + route `returns/bulk-post`.

### Frontend
- `ScanReturnDialog`: giữ 1 batch id per phiên (ref, reset khi đóng dialog);
  gắn vào payload tạo phiếu + payload pending-scans.
- `ledger-grouping.ts`: `groupLedgerRows(rows)` thuần — nhóm theo
  `scan_batch_id`, nhóm đứng ở vị trí dòng mới nhất của nó.
- `StockDocumentsPage`: render nhóm collapse + nút Ghi sổ cả đợt
  (`useBulkPostReturns`), confirm trước khi ghi sổ hàng loạt.
- Regen `schema.d.ts` (`npm run gen:api`).

### Giới hạn chấp nhận
- Gộp nhóm theo trang hiện tại (limit 100): đợt trải qua 2 trang hiển thị tách —
  hiếm (các phiếu 1 đợt tạo cách nhau vài giây, cùng sort key).
- Không backfill batch id cho phiếu cũ.

## Tests
- BE: `test_return_scan_batch.py` — create_return lưu batch id; API create trả
  batch id; pending scan khớp kế thừa batch id; bulk-post partial success;
  ledger expose `scan_batch_id`.
- FE: `ledger-grouping.test.ts` (nhóm/không nhóm/thứ tự); cập nhật
  `scan-return-rows.test.ts` cho payload mới.

## Sprint & tiến độ
- Sprint 1 (BE schema + service + API): ✅ hoàn thành trong MR này
- Sprint 2 (FE dialog + grouping + bulk post): ✅ hoàn thành trong MR này
- Chưa làm (đề xuất sau): filter "chỉ xem đợt quét", backfill nhãn đợt cho dữ
  liệu cũ, gộp nhóm xuyên trang.
