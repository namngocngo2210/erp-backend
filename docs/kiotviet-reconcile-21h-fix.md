# Sửa luồng đối soát KiotViet 21h: lặp đơn / đơn huỷ còn tồn / giá sai

> Branch: `fix/kiotviet-reconcile-21h-dup-cancel-price`
> Ngày: 2026-06-27 · Trạng thái: **ĐIỀU TRA XONG — chờ duyệt plan**

## 1. Bối cảnh

Task cron `kv-reconcile-paired-channels` chạy **21h mỗi ngày**, quét **cả tháng hiện tại**, fan-out mỗi
cặp `new(old=False) → paired_old_channel` thành `reconcile_one_pair` chạy 6 mode tuần tự:
`purge_cancelled → purge_duplicates → sync_missing → update_price → update_delivery_detail → update_status`.
Mỗi mode là một lần `SyncInvoicesService.run(...)` riêng (`infrastructure/services/reconcile.py:21-28`).

Mô hình: mirror hoá đơn từ kênh A (mới) sang kênh B (cũ). Mirror ở B mang `description`
`"SYNC từ kênh {branchId} - {Acode}"`; **toàn bộ việc map A↔B dựa vào regex trên description này**.

## 2. Bằng chứng từ lần chạy thật (worker log, 2026-06-26 21:00)

| Tín hiệu | Số lượng | Nguồn |
|---|---|---|
| `parity mismatch` (giá dựng lại ≠ total A) | **178** | `invoice_payload_builder.py:466` |
| `purge duplicates done: groups=55, deleted=59` | **59 mirror trùng/lần** | `_sync_service_purge_mixin.py:426` |
| `delete failed … 420 "Không thể hủy hóa đơn ở trạng thái đã hủy"` | **53** (17+36) | `purge cancelled` |
| `purge cancelled done: deleted=0` | xoá được **0** | |
| `SKIP [B-completed]` / `read-back mismatch` | 15 / 27 | `_sync_service_sync_mixin.py:145` |

## 3. Root cause (đã xác nhận, không phải suy đoán)

### 3.1 Lặp đơn — PUBLIC LIST phân trang KHÔNG ổn định (đã xác minh)
- **Phát hiện "đã mirror"** (pre-scan B, quyết định có tạo lại không) dùng **PUBLIC API** `GET /invoices`
  phân trang `currentItem` (`sync_invoices_service.py:391-447`, `:714`).
- Verify trên B=kv5 (15.485 đơn/tháng): public LIST **mặc định sort theo `modifiedDate`** (không bất biến).
  Quét 155 trang trong lúc reconcile đang sửa đơn → hàng xê dịch giữa trang → **chỉ 15.036 distinct,
  449 dòng LẶP, ~449 đơn bị SÓT**; mỗi lần gọi trả tập KHÁC nhau (HD095511 active vẫn vắng mặt).
- pre-scan sót mirror active → `sync_missing` tạo lại → **lặp đơn** (purge_duplicates dọn 59/đêm rồi
  hôm sau lại sinh). WEB LIST (`$skip/$top` OData) ổn định, đầy đủ (0 trùng) — KHÔNG phải thủ phạm.
- **Cách lấy đủ (đã test):** thêm `orderBy=id&orderDirection=Asc` (sort id bất biến) → **15.485 distinct,
  0 lặp, ổn định 100% giữa các lần gọi**, đủ cả các đơn trước đó bị sót. → "gộp về public" KHẢ THI.
- Phụ: lỗi giá (§3.3) làm `update_price` resync churn; **không có lock** chống chạy chồng (`tasks.py:48,83`).

### 3.2 Đơn huỷ còn tồn — KiotViet DELETE = huỷ mềm, purge không bỏ qua đơn đã huỷ
- `delete_invoice_by_id` gọi `DELETE /invoices` — KiotViet **không xoá cứng mà chuyển trạng thái huỷ**
  (`invoice_mutations_push.py:234-261`).
- `purge_cancelled` quét B map theo description nhưng **không lọc đơn B đã huỷ sẵn**
  (`_sync_service_purge_mixin.py:244-252`) → cố huỷ lại đơn đã huỷ → `420` mỗi ngày, `deleted=0`.
- Hệ quả: đơn A huỷ → mirror B thành "đã huỷ" (không biến mất) và bị đụng lại mỗi lần chạy.
- Ca phụ: A huỷ nhưng mirror B đã "Hoàn thành" → KiotViet chặn mọi thay đổi (15 ca `SKIP [B-completed]`).

### 3.3 Giá sai — giảm giá hoá đơn dùng giá TRƯỚC thuế (đã xác minh trên data thật)
- Mirror gửi `invoiceDetails[].price = priceAfterTax` (đúng), nhưng **giảm giá hoá đơn** lấy field
  `discount` (TRƯỚC thuế) áp lên giá dòng SAU thuế → B.total thừa đúng **phần thuế của khoản giảm**.
- Lý do: **LIST API** (luồng reconcile dùng) KHÔNG trả `discountAfterTax` ở header — chỉ có `discount`
  (trước thuế). Builder ưu tiên `discountAfterTax` (`:451`) nhưng nhận data thiếu → rơi xuống `discount`.
- Verify (Sprint 0, kv4): diff **luôn = `discountAfterTax − discount`** (HD010719: 198.000−183.333=14.667 ✓).
  KHÔNG phải làm tròn, KHÔNG phải double-tax.
- **Field SAU thuế thật vẫn có trong LIST**: line tax row `allocationDiscountAfterTax` (giảm giá HĐ phân bổ
  xuống dòng, đã gồm thuế); Σ lại = giảm giá HĐ sau thuế (HD010719 = 198.000 ✓). → đọc field này, **không
  suy ra từ total**.
- `codAmount`/`receiveMoney` = `total` thực ra **ĐÚNG**: ở KiotViet này `total` = khách cần trả (đã trừ giảm
  giá), `totalPayment` = 0 (đã thu). Giả thuyết "phải dùng totalPayment" SAI — đã loại.
- Spec đúng (chốt với CTO + verify): `tổng tiền hàng = Σ subTotalAfterTax · giảm giá hoá đơn =
  Σ allocationDiscountAfterTax · khách cần trả = total = Σ(dòng sau thuế) − giảm giá HĐ sau thuế`.

## 4. Định hướng (CTO chốt): **hợp nhất về PUBLIC API**

Bỏ `_web_list_invoices` (web API) ở mọi mode; mọi list/scan A và B dùng chung **một** nguồn public API
→ pre-scan, purge, check, update nhìn cùng một tập dữ liệu → hết lệch → hết tái tạo trùng.
Public API đã chứng minh trả về `status` (kể cả =2 huỷ), `total`, `description`, delivery
(`sync_invoices_service.py:413-441`) nên thay thế được web API ở các mode này.

## 5. Plan theo sprint

### Sprint 0 — Verify trên dữ liệu thật (read-only, BẮT BUỘC trước khi sửa write)
- [ ] Script Django-shell read-only: fetch 3 đơn parity-mismatch (HD010719, HDSPE_2606264J1MCCY3, HD010702)
      + 1 nhóm duplicate, qua **public API**, in đủ field giá/line/total/discount của A và mirror B.
- [ ] Chốt mô hình giá KiotViet recompute để biết cách map "khớp tuyệt đối" (không round per-unit):
      gửi giá decimal chính xác / dùng `subTotal` A / hay bù tròn ở dòng cuối.
- [ ] Xác nhận public API: lọc theo purchaseDate, phân trang đủ, trả cancelled & discount fields.

### Sprint 1 — Public LIST ổn định (lỗi lặp đơn) ✅ XONG (phần lõi)
- [x] Thêm `orderBy=id&orderDirection=Asc` vào 3 chỗ gọi public LIST `/invoices`: pre-scan B, fetch
      source A (`sync_invoices_service.py`), `_purge_target_branch` (`_sync_service_purge_mixin.py`).
      → pre-scan đầy đủ → hết bỏ sót mirror → hết tạo lại. Verify: 449 sót → 0.
- [ ] (follow-up, tuỳ chọn) Gộp NỐT purge_cancelled/purge_duplicates/check/update từ WEB LIST → PUBLIC
      LIST (đã chứng minh public + sort id = đầy đủ), để bỏ phụ thuộc web-login. Refactor lớn, làm riêng.
- [ ] (follow-up) Lock chống chạy chồng cho `reconcile_one_pair` (Redis lock theo cặp) — defense-in-depth.

### Sprint 2 — Giá khớp tuyệt đối (lỗi giá) ✅ XONG
- [x] `build_target_invoice_payload`: giảm giá HĐ = Σ `allocationDiscountAfterTax` (field thật, sau thuế),
      thêm helper `_header_discount_after_tax` (fallback: header `discountAfterTax` → `discount`).
- [x] `codAmount` = `total` giữ nguyên (đã xác minh đúng = khách cần trả).
- [x] Unit test `test_invoice_payload_builder.py` (3 case) + verify 2.858 đơn thật: **387 lệch → 2**
      (2 ca còn lại = phụ thu/đơn-0-dòng, parity warning bắt được, xử lý riêng).
- [ ] (sau) `update_price` so sánh có tolerance để hết churn delete+resync.

### Sprint 3 — Đơn huỷ (lỗi đơn huỷ)
- [ ] purge_cancelled bỏ qua mirror B **đã huỷ sẵn** (status=2) → hết 420 noise.
- [ ] Ca B đã "Hoàn thành" mà A huỷ: log cảnh báo rõ, không retry mù.

### Sprint 4 — Test + quan sát
- [ ] Unit test cho builder giá (fixture A→payload B khớp total).
- [ ] Test purge bỏ qua cancelled; integration dry-run.
- [ ] Đếm parity/duplicate/420 sau 1 đêm để xác nhận về 0.

## 5b. Trạng thái vận chuyển + hoá đơn (kiểm tra thêm)

Đo A vs mirror B (tháng 6): khớp cả 2 status chỉ 1081/2770 (kv4), 459/2026 (kv6). 3 nhóm lệch:
- **(inv 3→1) ~1.693**: B "Hoàn thành" trong khi A "Đang xử lý". Do **KiotViet auto-complete hoá đơn khi
  delivery=Giao thành công(3)**. **Đã xác minh là TỒN ĐỌNG**: toàn bộ tạo 01–19/06, **0 đơn sau 23/06**
  (ngày cap-guard `cb9a9902`/`ccaf29c0`). Cap-guard đã chặn phát sinh mới → **code đúng, không sửa thêm**.
- **(del 3→2) ~1.194**: cap có CHỦ ĐÍCH (giữ B.invoice đúng, hi sinh delivery) — theo lựa chọn CTO ưu tiên
  đúng invoice status.
- **(inv 1→3) ~226**: A xong nhưng B chưa — phần lớn là timing (A hoàn thành sau lần chạy 21h gần nhất),
  tự khớp lần chạy tới; theo dõi.

**Dọn tồn đọng:** command `cleanup_legacy_completed_mirrors` (dry-run mặc định, `--apply --yes --limit
--pair` để chạy lô). Verified dry-run live: kv4→kv5 found=599, 0 mutation. Chạy SAU deploy, lô nhỏ trước.

## 6. Tiến độ
- [x] Điều tra root cause trên log thật + query live (Phase 1-2 systematic-debugging)
- [x] Sprint 0 (verify) · Sprint 1 lõi (sort ổn định) · Sprint 2 (giá) · Sprint 3 (đơn huỷ)
- [x] Follow-up #1: gộp purge/check/update sang public LIST (`_web_list_invoices` → public GET /invoices
      + orderBy=id; bỏ web API). **Cảnh báo:** bản gộp ban đầu (tồn từ trước, lọt vào commit ccff3397)
      THIẾU sort id — đã bổ sung ở cf936a89, nếu không sẽ tái lập lỗi thiếu sót trên window lớn.
- [x] Follow-up #2: lock per-cặp (`cache.add`) chống chạy chồng `reconcile_one_pair` (95cc8742).
- [x] Follow-up #3: `update_price` tolerance ±1đ chống churn delete+resync (cf936a89).
- [ ] **CHƯA**: dry-run end-to-end sau deploy (`reconcile_channels --dry-run`) để xác nhận purge/check/
      update với public LIST trên dữ liệu thật (ephemeral container không reach được KiotViet để test ở đây).

## 7. Hồi quy 2026-08-26 — trần pageSize=100 làm purge/check chỉ quét trang đầu

**Triệu chứng:** kv4 có 55 hoá đơn đã huỷ trong tháng, `purge_cancelled` chỉ bắt được **3**.

**Root cause:** commit `ccff3397` (27/06) đổi `_web_list_invoices` từ web API `POST /api/invoices/list`
(OData `$top`, nhận 500) sang public API `GET /invoices` (`pageSize`). Signature giữ nguyên
(`top` → `pageSize`) nhưng **ngữ nghĩa đổi**: public API có **trần cứng pageSize = 100** và cắt
trong im lặng, không báo lỗi. Trong khi đó 8 vòng quét của purge/check vẫn giữ di sản thời web API:

```python
check_top = max(page_size, 500)          # xin 500
...
if len(web_data) < check_top or ...:     # nhận 100 → 100 < 500 → break
```

→ mọi mode purge/check **thoát ngay sau trang 1**, chỉ nhìn thấy **100 hoá đơn cũ nhất** trong window
(vì `orderBy=id&orderDirection=Asc`). Đúng 3/55 đơn huỷ bắt được đều là đơn cũ nhất.

Phạm vi: `purge_cancelled`, `purge_duplicates`, `purge_wrong_date` (`_sync_service_purge_mixin.py`),
`check_missing`, `check_price`, `check_status`, `update_status`, `update_delivery_detail`,
`update_sale_channel` (`_sync_service_check_mixin.py`). Kéo dài 27/06 → 26/08.
**`reconcile_sync` KHÔNG dính** — nó đi qua `_prescan_target` / `_iter_source_invoices`, hai vòng này
so `len(page)` với `page_size=100` nên vẫn đúng.

**Fix:** hằng số `KIOTVIET_LIST_MAX_PAGE_SIZE = 100` ở `_sync_service_shared.py`, kẹp ở 3 tầng:
`run()` (chặn `--page-size 500` từ CLI) → `_web_list_invoices` (request thật không bao giờ vượt trần)
→ `check_top` của 8 vòng quét (điều kiện dừng lại đúng nghĩa "hết dữ liệu").
Thêm cảnh báo khi list bỏ dòng `id<=0` — trường hợp đó `currentItem` cộng thiếu, phân trang vẫn lệch.

**Test:** `ListPaginationCapTests` trong `test_sync_service_check_purge.py` — stub mô phỏng server cắt
trang về 100, 250 đơn/kênh, đơn huỷ rải ở trang 1-3. Đã xác nhận 3 test này ĐỎ trên code cũ.

**Sau deploy — cảnh báo vận hành:** lần reconcile đầu tiên sẽ dọn một lượng lớn tồn đọng 2 tháng
(đơn huỷ chưa xoá bên B, mirror trùng, giá/trạng thái lệch). Số liệu kênh cũ sẽ **tụt**. Chạy dry-run
theo lô trước khi chạy thật:

```bash
python manage.py reconcile_channels --from-dt "2026-07-01 00:00:00" --to-dt "2026-07-31 23:59:59" --dry-run
python manage.py reconcile_channels --from-dt "2026-08-01 00:00:00" --to-dt "2026-08-26 23:59:59" --dry-run
```
