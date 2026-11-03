# Phiếu trả hàng theo ngày (`ReturnNote` ngày + `ReturnEntry`)

> **Đây là mô tả HIỆN HÀNH của mô hình trả hàng.** Mọi doc cũ nói "mỗi đơn hoàn = 1 phiếu" /
> "1 phiếu ↔ 1 đơn" đều đã **lỗi thời** — quan hệ 1-1 đó nay nằm ở tầng **`ReturnEntry`**, còn
> `ReturnNote` là **phiếu ngày**.
>
> - Thiết kế chi tiết: `docs/superpowers/specs/2026-07-11-return-note-daily-batch-design.md`
> - Nền: `docs/erp-inventory-returns.md` (Sprint B gốc), `docs/erp-inventory-merge-returns.md` (trang Phiếu kho gộp)
> - Module: `apps/inventory`, FE `features/inventory`

## 1. Mô hình 3 tầng

```
ReturnNote (phiếu NGÀY)  1 ──< ReturnEntry (nhóm ĐƠN)  1 ──< ReturnNoteLine (dòng SP)
     mã PTH-...                  giữ mọi ngữ nghĩa kho              return_type bắt buộc
     open / closed               draft / posted / cancelled
```

### `ReturnNote` — phiếu ngày (dòng hiển thị trên trang Phiếu kho)

| Trường | Ghi chú |
|---|---|
| `code` | `PTH-YYYYMMDD-K{warehouse_id}` (+ `-02`, `-03` nếu ngày đó đã có phiếu chốt trước), unique |
| `warehouse` | FK, bắt buộc |
| `doc_date` | `DateField`, ngày theo **giờ VN** (`Asia/Ho_Chi_Minh`) — không dùng `created_at::date` UTC |
| `status` | `open` (Đang mở) / `closed` (Đã chốt) |
| `closed_at`, `closed_by`, `created_by`, `created_at` | |

Ràng buộc: partial unique `(doc_date, warehouse) WHERE status='open'` → **tối đa 1 phiếu đang mở** mỗi
ngày/kho. Phiếu ngày **không** có `return_type`, `source_order`, `shipping_code`, `posted_at`,
`scan_batch_id` — tất cả đã xuống `ReturnEntry`.

### `ReturnEntry` — nhóm đơn trong phiếu (không có mã riêng)

`return_note` (FK), `source_order` (FK Order, nullable khi quét chưa khớp đơn), `shipping_code`,
`customer_name/phone`, `source_note`, `note`, `status` (`draft`/`posted`/`cancelled`), `posted_at`,
`order_status_before`, `scan_batch_id` (chỉ để truy vết đợt quét, **không** còn dùng gộp hiển thị).

Guard chống khớp hoàn 2 lần giữ nguyên, chỉ chuyển tầng: 1 `shipping_code` chỉ có **1 entry chưa huỷ**;
partial unique `(shipping_code) WHERE shipping_code != '' AND source_order IS NULL AND status='draft'`.

### `ReturnNoteLine`

FK `entry` (thay `return_note`); `return_type` **bắt buộc** ở từng dòng (phiếu ngày không còn loại);
unique `(entry, product)`.

### `StockMovement`

`reference_type = "return_entry"`, `reference_id = ReturnEntry.id` (trước là `("return", note_id)`).
Bắt buộc để huỷ lẻ 1 đơn chỉ đảo movement của đơn đó, không phải cả ngày.

## 2. Nghiệp vụ

- **Tạo (quét hoặc tay)**: `resolve_open_note(today_vn, warehouse)` (advisory lock class **62001** theo
  `(ngày, kho)` → 2 người quét đồng thời không đẻ 2 phiếu) → tạo entry + lines → `post_entry` (cộng tồn,
  đơn → `RETURNED`). Tất cả trong 1 transaction.
- **Ghi sổ ở mức ENTRY** (tức thì khi quét khớp mã) — phiếu ngày chỉ là chứng từ gom.
- **Huỷ 1 entry**: đảo movement của entry, trả đơn về `order_status_before`; cho phép **kể cả khi phiếu
  đã chốt** (huỷ là sửa sổ, không phải sửa phiếu). Quyền `returns:approve`.
- ~~**Chốt phiếu**~~ — **ĐÃ GỠ 2026-07-14** cùng toàn bộ UI phiếu trả (xem
  `docs/superpowers/specs/2026-07-14-remove-return-note-ui-design.md`): phiếu ngày là sổ gom ngầm,
  không chốt/huỷ tay nữa. Phiếu `closed` chỉ còn ở dữ liệu legacy; gặp phiếu closed thì quét tiếp
  vẫn tạo phiếu mới `...-02` như cũ.
- **Pending scan khớp**: entry rơi vào phiếu ngày **của ngày quét gốc** (`scan.created_at` giờ VN); phiếu
  ngày đó đã chốt (legacy) → tạo phiếu mới cùng ngày.

## 3. API (sau khi gỡ UI phiếu trả 2026-07-14)

| Endpoint | Ghi chú |
|---|---|
| `POST /api/inventory/returns` | tạo **entry** (luồng quét, `auto_post=true` ghi sổ ngay); trả `ReturnEntrySerializer`, phiếu ngày lồng trong khoá `return_note` |
| `POST /api/inventory/returns/{entry_id}/cancel` | huỷ 1 nhóm đơn (đảo tồn, `returns:approve`) |
| ~~`GET /api/inventory/returns`, `GET/PATCH /returns/{id}`, `POST /returns/{id}/post`~~ | **ĐÃ GỠ** — khớp tay đi qua bảng chờ mã quét (`flow-pending/{scan_id}/match`), ghi sổ nháp legacy qua lệnh `post_matched_return_drafts` |
| ~~`/api/inventory/return-notes/{id}` + `/close` + `/cancel`~~ | **ĐÃ GỠ** — không còn màn chi tiết/chốt/huỷ phiếu ngày |
| `GET /api/inventory/ledger-documents` | chỉ còn **phiếu kho** — dòng phiếu trả đã rời sổ; theo dõi hoàn ở `return-history/*` |
| ~~`POST /api/inventory/returns/bulk-post`~~ | **ĐÃ GỠ** — phiếu ngày thay thế "Ghi sổ cả đợt" |

RBAC không đổi: `returns:view` để tạo qua quét, `returns:approve` để huỷ.

## 4. Frontend (sau khi gỡ UI phiếu trả 2026-07-14)

Trang Phiếu kho (`StockDocumentsPage`) chỉ còn phiếu kho; giữ "Quét trả hàng (hàng loạt)" +
"Chờ khớp đơn". Đơn hoàn xem/sửa sai ở **Lịch sử nhập xuất → tab Nhập hàng hoàn về**: dòng lịch sử
(đơn hoàn đã ghi sổ) có nút **Huỷ** (`returns:approve`), mã chưa khớp có sẵn **Khớp** (manual match).
Đã gỡ: `CreateReturnDialog`, `ReturnNoteDetailDialog`, `ReturnEntryMatchPanel`,
`ledger-grouping.ts`, `useBulkPostReturns`.

---

# 5. RUNBOOK DEPLOY PROD

Hai bước độc lập: **(A) migration 0022** — bắt buộc, sau bước này hệ thống **đã chạy đúng** (phiếu cũ
tạm thời là phiếu-ngày-1-entry, giữ mã cũ `THyyyymmdd-...`); **(B) lệnh gộp hồi tố
`merge_return_notes_by_day`** — chỉ để làm đẹp lịch sử, có thể chạy sau, cắt lát tuỳ ý.

## A. Deploy code + migrate

### A0 — NGƯỜI phải kiểm TRƯỚC khi migrate: phiếu cũ thiếu loại hoàn

```sql
SELECT count(*) FROM stock_return_notes WHERE COALESCE(return_type,'') = '';
```

Mỗi phiếu ở đây sẽ được 0022 điền `return_type = 'delivery_returned'` cho các dòng của nó (mặc định
"hàng hoàn"). Nếu thực tế nó là **bảo hành / hàng lỗi**, migration sẽ cộng vào **tồn BÁN ĐƯỢC** thay vì
bucket warranty/defect → tồn bán được phồng, hàng lỗi biến mất khỏi sổ. Không có cách nào đoán đúng tự
động → **soi tay từng phiếu** (ghi chú, đơn nguồn) và `UPDATE stock_return_notes SET return_type = …`
cho đúng **TRƯỚC** khi chạy migrate. Chỉ đi tiếp khi count = 0 hoặc đã xác nhận toàn bộ là hàng hoàn.

### A1 — migrate

```bash
docker compose -f docker-compose.prod.yml exec -T web python manage.py migrate inventory
```

`0022_return_entry` làm: tạo `stock_return_entries`; mỗi `ReturnNote` cũ → đúng 1 entry (copy
`source_order`, `shipping_code`, khách, `status`, `posted_at`, `order_status_before`, `scan_batch_id`);
lines trỏ sang entry, `return_type` rỗng → điền từ `note.return_type`; `StockMovement("return", note_id)`
→ `("return_entry", entry_id)`; phiếu cũ giữ mã cũ, `doc_date` = ngày tạo (giờ VN), `status=closed`.
**Số phiếu chưa đổi. Dừng ở đây là an toàn.**

### A2 — assertion NGAY SAU migrate (bắt buộc)

```sql
SELECT count(*) FROM stock_movements WHERE reference_type='return';   -- phải = 0 sau khi chạy 0022
```

0022 chỉ trỏ lại movement nào có **phiếu nguồn còn tồn tại**. Kết quả **> 0** = số movement trỏ vào các
phiếu đã bị xoá trước migration → chúng ở lại `reference_type='return'` mãi mãi, và
`stock_query_service.resolve_reference_labels` (đã **bỏ nhánh `"return"`**) sẽ render chúng **không có
nhãn chứng từ** trong danh sách biến động kho. Số tồn vẫn đúng (movement bất biến), chỉ mất truy vết →
xử lý: hoặc chấp nhận + ghi nhận, hoặc dựng bảng ánh xạ tay. Đừng chạy bước B khi chưa biết vì sao > 0.

## B. `merge_return_notes_by_day` — gộp phiếu cũ theo (ngày, kho)

### Bước 0 — pre-image 2 bảng (UNDO phẫu thuật, KHÔNG restore cả DB)

Restore cả DB sẽ ném đi mọi nghiệp vụ ghi từ lúc dump. Lệnh chỉ đụng `code/status/closed_at` của
`stock_return_notes` và `return_note_id` của `stock_return_entries` → 2 pre-image là đủ để hoàn nguyên:

```sql
CREATE TABLE stock_return_notes_bak_20260711   AS SELECT * FROM stock_return_notes;
CREATE TABLE stock_return_entries_bak_20260711 AS SELECT id, return_note_id FROM stock_return_entries;
```

### Bước 1 — BASELINE (chạy TRƯỚC dry-run, để biết cái gì vốn đã lệch từ trước)

```sql
SELECT warehouse_id, product_id, state, SUM(quantity) FROM stock_items GROUP BY 1,2,3 ORDER BY 1,2,3;
SELECT count(*), COALESCE(SUM(qty_delta),0) FROM stock_movements;
SELECT count(*) FROM stock_return_notes;
SELECT count(*) FROM stock_return_notes WHERE code NOT LIKE 'PTH-%';   -- số phiếu cũ cần gộp
SELECT count(*) FROM stock_return_entries;
SELECT status, count(*) FROM stock_return_entries GROUP BY 1;
-- movement MỒ CÔI (nếu >0 thì 0022 để sót, KHÔNG phải do lệnh gộp gây ra):
SELECT count(*) FROM stock_movements m
 WHERE m.reference_type='return_entry'
   AND NOT EXISTS (SELECT 1 FROM stock_return_entries e WHERE e.id=m.reference_id);
```

### Bước 1b — đếm (ngày, kho) + kiểm tra ngân sách advisory lock (BẮT BUỘC)

Lệnh giữ **1 advisory lock SESSION cho mỗi (ngày, kho) có phiếu cũ** suốt lần chạy. Bảng khoá là tài
nguyên CHUNG toàn server (~`max_locks_per_transaction × max_connections`, mặc định 64 × 100 ≈ 6400 slot).
Lịch sử prod ~1 phiếu cũ / đơn hoàn / ngày → dễ 1000–2000 khoá.

```sql
SELECT count(*) FROM (SELECT DISTINCT doc_date, warehouse_id FROM stock_return_notes
                      WHERE code NOT LIKE 'PTH-%') t;
SHOW max_locks_per_transaction;
SHOW max_connections;
```

Quy tắc: số (ngày, kho) phải ≤ **20%** ngân sách. Lệnh **tự kiểm tra**; vượt → `CommandError` + in sẵn các
mốc `--date-from`, **không lấy khoá, không ghi gì**. Khi đó **cắt lát theo tháng, chạy MỚI NHẤT TRƯỚC rồi
lùi dần** (chỉ có `--date-from`, nhưng lát trước đã biến phiếu cũ tháng đó thành `PTH-` nên lát sau không
khoá lại chúng):

```bash
python manage.py merge_return_notes_by_day --date-from 2026-07-01   # lát 1
python manage.py merge_return_notes_by_day --date-from 2026-06-01   # lát 2 ...
```

`--force` chỉ dùng khi đã nâng `max_locks_per_transaction`.

### Bước 2 — `--dry-run`, BẤM GIỜ, chạy giờ thấp điểm

Transaction quét toàn bộ `stock_items` + 2 aggregate trên `stock_movements` (bảng **partition**, rất lớn)
→ dry-run cho biết lần chạy thật tốn bao lâu và giữ khoá ngày bao lâu. Dry-run > vài phút → cắt nhỏ.

```bash
time docker compose -f docker-compose.prod.yml exec -T web \
  python manage.py merge_return_notes_by_day --dry-run
```

Đọc output:
- dòng đầu `Mức cô lập: repeatable read` — **bắt buộc**; không phải → lệnh tự `CommandError` và dừng.
- mỗi nhóm: `PTH-YYYYMMDD-K{wh}: N phiếu cũ → 1 (M nhóm đơn chuyển, status)`. Hôm nay → `open`, quá khứ →
  `closed` (chỉ khi phiếu giữ lại là phiếu CŨ được đổi tên; nhóm đã có phiếu `PTH-` thật thì phiếu đó giữ
  NGUYÊN mã + NGUYÊN status).
- dòng cuối `[dry-run] Gộp X phiếu ngày, chuyển Y nhóm đơn, xoá Z phiếu rỗng, bỏ qua S nhóm không có phiếu
  cũ. Tồn không đổi.`
- Bất kỳ `CommandError` nào (LỆCH TỒN / LỆCH SỔ MOVEMENT / MỒ CÔI / CASCADE guard) → **DỪNG**, không chạy thật.

`--dry-run` chạy full logic rồi rollback. Lệnh **idempotent**: chạy lại trên dữ liệu đã gộp = no-op.

### Bước 3 — chạy thật

```bash
docker compose -f docker-compose.prod.yml exec -T web python manage.py merge_return_notes_by_day
```

Luồng quét hoàn sẽ **xếp hàng** sau advisory lock ngày của lệnh (không lỗi, chỉ chờ) → chạy giờ thấp điểm.

### Bước 4 — assertions sau khi chạy

```sql
-- (a) KHÔNG còn phiếu cũ (chỉ đúng khi ĐÃ chạy hết mọi lát --date-from; đang chạy dở thì phiếu cũ của
--     các tháng chưa tới lượt là HỢP LỆ — kiểm theo lát: thêm AND doc_date >= '<mốc vừa chạy>')
SELECT count(*) FROM stock_return_notes WHERE code NOT LIKE 'PTH-%';        -- = 0

-- (b) Ngày/kho còn >1 phiếu: chỉ được phép là ngày có phiếu tách -02/-03 hợp lệ
SELECT doc_date, warehouse_id, count(*), array_agg(code ORDER BY id)
  FROM stock_return_notes GROUP BY 1,2 HAVING count(*) > 1;

-- (c) Đúng 1 phiếu MỞ mỗi (ngày, kho)
SELECT doc_date, warehouse_id, count(*) FROM stock_return_notes
 WHERE status='open' GROUP BY 1,2 HAVING count(*) > 1;                      -- rỗng

-- (d) closed_at khớp status
SELECT count(*) FROM stock_return_notes WHERE status='open'   AND closed_at IS NOT NULL;  -- 0
SELECT count(*) FROM stock_return_notes WHERE status='closed' AND closed_at IS NULL;      -- 0

-- (e) Movement mồ côi KHÔNG tăng so với baseline bước 1
SELECT count(*) FROM stock_movements m
 WHERE m.reference_type='return_entry'
   AND NOT EXISTS (SELECT 1 FROM stock_return_entries e WHERE e.id=m.reference_id);
```

So với baseline bước 1: tổng tồn theo `(warehouse, product, state)` **giống hệt**; `count`/`sum` của
`stock_movements` **không đổi**; `count(stock_return_entries)` + phân bố `status` **không đổi**;
`count(stock_return_notes)` giảm đúng `Z`.

### Bước 5 — smoke test nghiệp vụ

Trang Phiếu kho: mỗi ngày/kho chỉ còn 1 dòng phiếu trả (trừ ngày có `-02` hợp lệ). Quét thử 1 mã vận đơn
hoàn → entry mới rơi vào phiếu `PTH-<hôm nay>-K{wh}` đang mở. Đối chiếu 1 phiếu ngày với kho thực tế.

### Bước 6 — UNDO (nếu cần): phẫu thuật, không restore DB

Thứ tự BẮT BUỘC: **INSERT lại phiếu đã xoá TRƯỚC**, rồi mới trỏ entry về (nếu UPDATE trước thì
`return_note_id` tạm trỏ vào phiếu chưa tồn tại — chỉ "chạy được" nhờ FK của Django là DEFERRABLE
INITIALLY DEFERRED; đừng dựa vào đó).

```sql
BEGIN;
INSERT INTO stock_return_notes SELECT * FROM stock_return_notes_bak_20260711 b
 WHERE NOT EXISTS (SELECT 1 FROM stock_return_notes n WHERE n.id = b.id);
UPDATE stock_return_entries e SET return_note_id = b.return_note_id
  FROM stock_return_entries_bak_20260711 b
 WHERE b.id = e.id AND b.return_note_id <> e.return_note_id;
UPDATE stock_return_notes n
   SET code = b.code, status = b.status, closed_at = b.closed_at, closed_by_id = b.closed_by_id
  FROM stock_return_notes_bak_20260711 b
 WHERE b.id = n.id;
COMMIT;   -- phiếu do người quét TẠO MỚI sau lúc backup không nằm trong bản sao → giữ nguyên
```

### Escape hatch — advisory lock kẹt

Lệnh chết bất thường / connection pooled nửa sống → khoá ngày còn treo, kho quét hoàn sẽ **đứng chờ**:

```sql
SELECT l.pid, l.classid, l.objid, l.granted, a.state, a.query, a.state_change
  FROM pg_locks l JOIN pg_stat_activity a USING (pid)
 WHERE l.locktype = 'advisory' ORDER BY a.state_change;   -- classid 62001 = lock class phiếu-ngày

SELECT pg_terminate_backend(<pid>);   -- CHỈ khi đã xác nhận pid là của lệnh này
```

## 6. Cảnh báo vận hành

1. **Transaction dài**: aggregate chạy trên `stock_movements` (partitioned) là phần tốn thời gian nhất →
   bấm giờ ở dry-run, chạy giờ thấp điểm, hoặc cắt theo `--date-from`.
2. Ngân sách khoá tính xấp xỉ (không trừ phần backend khác đang dùng) → hệ số bảo thủ 20%. Là **guard**,
   không phải bảo đảm.
3. Movement mồ côi **có sẵn từ trước** không bị lệnh báo đỏ (lệnh chỉ so trước/sau) → đã đưa vào baseline.
