# Đơn xuất lại `<mã>.NN` giữ nguyên ngày xuất của đơn gốc

Ngày: 2026-07-28
Nhánh: `fix/sibling-transfer-dispatch-date`
Ca prod: `HD013469` bị huỷ → `HD013469.01` hiện ngày xuất = ngày tạo đơn mới.

## 1. Vấn đề

KiotViet huỷ-xuất lại hoá đơn. Bản `.01` về trước webhook huỷ (hoặc về qua cron
import) nên thành DÒNG ĐƠN MỚI thay vì reattach. Lúc huỷ, hệ thống **chuyển sổ
xuất** sang đơn `.01` (`transfer_issue_to_sibling`) — hàng vẫn ngoài kho, chỉ
đổi đơn đứng tên. Lưới hứng phía sau (`ReissueLedgerRepairService.repair`) làm
việc tương tự khi nhánh bắt-tại-chỗ trượt.

Cả hai đều gọi `record_movement` **không truyền `occurred_at`** ⇒ mặc định
`now()`, tức thời điểm huỷ/tạo đơn mới. Tab Xuất lọc và sắp xếp theo
`occurred_at` nên đơn `.01` nhảy sang ngày đó.

Bản vá 2026-07-18 (`d339e84c`) chữa ở TẦNG ĐỌC: `_dispatch_last_at()` loại các
note hành chính khỏi `Max(occurred_at)`. Nó đủ cho ca **hồi sinh** vì đơn hồi
sinh vẫn giữ bút toán SALE thật của chính nó. Với đơn `.01` thì không: đó là
**bản ghi đơn khác**, bút toán hành chính là thứ DUY NHẤT nó có → filter trả
NULL → rơi vào fallback `Coalesce(..., Max(occurred_at))` → đúng bút toán sai
ngày.

Nặng hơn cột hiển thị: `base_queryset` cũng **lọc theo `occurred_at`**, nên đơn
`.01` nằm sai ngày trong tab Xuất — lọc đúng ngày giao thật sẽ không thấy nó.
Đây là lỗi tầng đọc không vá được; phải sửa ở đường ghi.

## 2. Quyết định

Bút toán SALE hành chính ghi cho **đơn khác** phải mang sẵn ngày nghiệp vụ của
lần hàng thật rời kho:

```
StockSalesService.transfer_issue_to_sibling      → occurred_at = ngày xuất đơn gốc
ReissueLedgerRepairService.repair                → occurred_at = ngày xuất đơn huỷ
```

Nguồn ngày: `real_dispatch_occurred_at(order_id)` — `Max(occurred_at)` trên SALE
của đơn, **loại note hành chính**; nếu đơn chỉ có SALE hành chính (chuỗi `X` →
`X.01` → `X.02`) thì lấy luôn ngày của chúng, nếu không mỗi vòng huỷ-xuất lại
đẩy ngày đi một nấc.

**Reversal giữ nguyên `now()`.** Đảo sổ là nghiệp vụ xảy ra tại thời điểm huỷ —
cùng luật với §7 của `docs/superpowers/specs/2026-07-22-scan-match-business-date-design.md`.

`created_at` (partition key, dấu vết audit "ghi sổ lúc nào") không đụng tới. Bất
biến `occurred_at <= created_at` được giữ: ngày mới luôn ở quá khứ.

Danh sách note hành chính dời về `stock_sales_service.ADMIN_SALE_NOTES` —
đường ghi và đường đọc (`DispatchHistoryService`) nay đọc chung một hằng số. Hai
bên lệch nhau là ngày xuất lệch theo.

## 3. Dữ liệu cũ

Sửa đường ghi chỉ đúng cho dòng mới. Dòng đã ghi nắn một lần bằng command:

```bash
python manage.py backfill_sibling_dispatch_date --dry-run   # duyệt trước
python manage.py backfill_sibling_dispatch_date
```

Luật: với mỗi bút toán note `Nhận sổ xuất từ đơn bị huỷ`, tìm đơn huỷ nguồn theo
ĐÚNG luật của đường ghi (`find_cancelled_source_order`: cùng mã vận đơn + cùng
gốc hoá đơn sau khi bỏ hậu tố `.NN`), rồi lấy ngày xuất thật của đơn đó.

- Chỉ nắn **lùi** (`ngày mới < occurred_at hiện tại`) ⇒ giữ bất biến và chạy lại
  là no-op.
- Duyệt theo **id tăng dần** ⇒ chuỗi `.01` → `.02` nắn từ mắt xích sớm nhất, mắt
  xích sau đọc lại giá trị vừa nắn.
- Không suy ra được đơn nguồn (mã vận đơn trùng nhưng hoá đơn khác gốc) ⇒ bỏ
  qua, đếm vào `skipped`.
- Là command chứ không phải data migration: tập dòng nhỏ, cần `--dry-run` cho
  vận hành duyệt, và cùng kiểu thao tác với `repair_reissue_ledger`.

Hệ quả cần báo trước cho vận hành: sản lượng xuất của các ngày đã qua sẽ đổi số
so với ảnh chụp trước đó — hàng chạy về đúng ngày rời kho. Đó là mục tiêu.

## 4. Kiểm thử

| Ca | Test |
|---|---|
| Chuyển sổ giữ ngày xuất gốc, `created_at` vẫn là lúc chuyển | `test_transferred_sale_carries_original_dispatch_date` |
| Chuỗi `.01` → `.02` giữ ngày rời kho ĐẦU TIÊN | `test_transfer_chain_keeps_first_dispatch_date` |
| Sweep ghi bù mang ngày đơn huỷ, không phải ngày sweep | `test_repaired_sale_carries_original_dispatch_date` |
| Tab Xuất: đơn `.01` hiện ở đúng ngày, ngày xuất đúng | `test_sibling_order_keeps_real_dispatch_date_in_orders_page` |
| Tab Xuất: đơn `.01` KHÔNG hiện ở ngày chuyển sổ | `test_sibling_order_absent_from_the_day_the_ledger_moved` |
| Backfill: nắn, dry-run, idempotent, không đẩy ngày ra sau, bỏ qua khi không có đơn nguồn, chuỗi nhiều nấc | `test_sibling_dispatch_date_backfill.py` |

## 5. Còn lại, chưa làm

Bút toán hành chính ghi cho CHÍNH đơn đó (`REVIVE_REISSUE_NOTE` khi hồi sinh,
`RESYNC_NOTE` khi sửa items sau xuất) vẫn mang `now()`. Cột ngày xuất đã đúng
nhờ `_dispatch_last_at()` loại note, nhưng nếu lọc đúng ngày hồi sinh/sửa items
thì đơn vẫn hiện ra ở ngày đó (net 0 ở ca hồi sinh, net dương ở ca resync). Cùng
lớp lỗi, khác mức độ ảnh hưởng — xử lý riêng nếu vận hành thấy nhiễu.
