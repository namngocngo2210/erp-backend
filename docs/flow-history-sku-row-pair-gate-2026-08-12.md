# Lịch sử nhập xuất — dòng "Theo SKU" đếm sai đơn và trừ nhầm SL

Ngày: 2026-08-12
Nhánh: `fix/flow-sku-row-pair-gate`

## 1. Vấn đề (ca prod)

Lọc ngày 12/08, kho **Đại Mỗ 1**, view **Theo SKU**: dòng `BNC02` hiện
**11 đơn · 9 chiếc**, nhưng bung dòng ra chỉ có **10 đơn × 1 = 10 chiếc** (6 mã
SPXVN + 4 mã VNGH) — và file Excel theo đơn cũng ra 10. Hai con số nằm trên
đúng một màn hình, do đúng một bộ lọc, mà nói khác nhau.

Sai số có dạng rất đặc trưng: **+1 đơn và −1 chiếc**.

## 2. Root cause

Ba nơi cùng đọc `stock_movements` nhưng chỉ có hai nơi áp luật per-đơn:

| Nơi | Gộp theo | Luật loại đơn |
|---|---|---|
| Tab Theo đơn (`order_groups`) | `reference_id` | có `_HAS_REAL_SALE` (đơn phải có SALE thật trong kỳ) |
| Bung dòng (`sku_orders`) | `reference_id` (đã lọc 1 SKU) | có `_HAS_REAL_SALE` |
| **Dòng SKU (`sku_groups`)** | `product_id` | **không có** — `_HAS_REAL_SALE` chỉ hỏi ở tầng SKU |

Đơn thứ 11 là đơn **xuất từ ngày khác, ngày 12/08 chỉ còn bút toán ĐẢO**
(`sale_reversal` do huỷ đơn / chuyển sổ). Vì `sku_groups` gộp thẳng theo
product, bút toán đảo mồ côi đó:

- lọt vào `Count("reference_id", distinct=True)` → **11 đơn**;
- bị trừ vào `net = dispatched − reversed` → **9 chiếc**.

Còn `sku_orders` gộp theo đơn nên loại nó ngay từ `HAVING sale_sum < 0` → 10 dòng.

Phát hiện kèm theo (cùng gốc — dòng SKU không theo luật chung):

- `net` của dòng SKU cộng thẳng, nên **đơn xuất rồi huỷ trong CÙNG kỳ** bị trừ
  mất, trong khi tile "Tổng SL xuất" (`_issued_qty_expr`) và tab Theo đơn đều
  ghi nhận là một lần xuất (hàng đã rời kho thật).
- `sku_orders` thiếu chốt `issued > 0 OR admin_sale < 0` của `order_groups`, nên
  đơn bị **bỏ đánh dấu xuất kho** (bấm nhầm rồi sửa) vẫn hiện ở phần bung dòng
  dù tab Theo đơn đã ẩn.

## 3. Cách sửa

Một luật duy nhất: **dòng SKU chỉ tính các cặp (đơn, SKU) thật sự có hàng rời
kho trong kỳ, và đo bằng cùng thước đo với tile.**

1. `sku_groups` thêm điều kiện `EXISTS` ở tầng WHERE: cặp `(reference_id,
   product_id)` của dòng phải có bút toán `SALE` (qty < 0) **trong chính kỳ
   lọc**. Bút toán đảo mồ côi bị loại khỏi cả `order_count` lẫn tổng SL.
2. `net` của dòng SKU chuyển sang `_issued_qty_expr()` — cùng biểu thức tile
   "Tổng SL xuất" và tab Theo đơn dùng (đảo sổ SAU khi hàng đã đi không xoá đi
   một lần xuất đã xảy ra; đảo "chưa từng rời kho" thì vẫn trừ).
3. `sku_orders` thêm chốt `issued > 0 OR admin_sale < 0` như `order_groups`.
4. `sku_groups` giải ô tìm mã đơn một lần (`with_resolved_order`) rồi truyền
   xuống cả query ngoài lẫn subquery — không thì mỗi lần gọi dò trigram thêm
   một lượt.

Không đụng FE: `displayQty(net, gross)` giữ nguyên, `dispatched` vẫn là SL đã
từng rời kho để làm giá trị thay thế cho đơn `.NN` (chỉ nhận sổ, `issued = 0`).

## 4. Chi phí query

`EXISTS` tương quan theo `(reference_type, reference_id)` — dùng đúng index
`stockmove_ref_idx` đang có, mỗi đơn chỉ vài dòng movement nên mỗi lần dò là
một index lookup. Không thêm index, không đổi shape của GROUP BY.

Đã cân nhắc và loại: gộp hai tầng (theo cặp rồi mới theo product) trong SQL —
đúng tuyệt đối cả ca "bỏ đánh dấu xuất kho" nhưng biến `EXISTS` thành subplan
có `GROUP BY/HAVING` chạy trên từng dòng, đắt hơn hẳn ở khoảng lọc rộng. Ca đó
hiếm (thao tác sửa sai) và sau bản này chỉ còn lệch ở `order_count`, không lệch
SL.

## 5. Test

`backend/apps/inventory/tests/test_dispatch_history_service.py`:

- `test_cancel_of_older_dispatch_does_not_inflate_sku_row` — dựng đúng ca prod
  BNC02 (một đơn xuất trong kỳ + một đơn chỉ có bút toán huỷ trong kỳ); dòng SKU
  phải khớp phần bung dòng: 1 đơn · 1 chiếc.
- `test_sku_row_counts_dispatch_cancelled_within_window` — xuất rồi huỷ cùng kỳ:
  dòng SKU = tile = 3.
- `test_undone_dispatch_drops_out_of_sku_drilldown` — đơn bị bỏ đánh dấu xuất
  kho biến mất khỏi cả hai tab.

Toàn bộ `apps/inventory`: 625 passed. `ruff check .`: sạch.
