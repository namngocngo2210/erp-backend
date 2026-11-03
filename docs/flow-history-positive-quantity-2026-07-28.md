# Lịch sử nhập xuất: SL hiển thị luôn là số dương

Ngày: 2026-07-28
Nhánh: `fix/flow-history-positive-qty`

## 1. Vấn đề

Màn **Lịch sử nhập xuất** → tab "Xuất hàng ra" hiển thị thẳng `net`
(= đã xuất − đã hoàn/chuyển sổ). Với đơn bị huỷ đã chuyển sổ sang đơn xuất lại
`<mã>.NN`, `net = 0` — người nhìn tưởng đơn không xuất gì, trong khi hàng đã rời
kho thật (ca prod 28/07: `HD013349` net 0 dù đã xuất 2, `HD013530` net 0 dù đã
xuất 1).

Yêu cầu vận hành: **ô số lượng không được hiện số âm hoặc 0**. Logic bên trong
vẫn dùng net như cũ.

## 2. Quyết định

Một luật hiển thị duy nhất, dùng chung cho web và file Excel:

```
SL hiển thị = |net|  hoặc, nếu bằng 0, |gross|
```

- `net` giữ nguyên ý nghĩa và vẫn là con số dùng để đối chiếu tồn — **không đổi
  metric**. Đơn hoàn một phần (xuất 3, hoàn 1) vẫn hiện 2, không phải 3.
- `gross` = SL đã TỪNG rời kho, chưa trừ phần đảo. Chỉ dùng làm giá trị thay thế
  khi net = 0.
- `Math.abs` là chốt chặn cuối cho dữ liệu lệch (đảo nhiều hơn xuất — không nên
  xảy ra nhưng prod từng có): sai số vẫn hiện, chỉ không hiện dấu âm.

Đã cân nhắc và loại: đổi hẳn cả bảng sang `gross`. Nó làm sản lượng xuất phồng
lên ở mọi đơn hoàn một phần — sửa cách hiển thị mà đổi luôn số liệu kinh doanh.

## 2b. Gốc của số âm: huỷ đơn đang bị đếm như một lần xuất

Ảnh prod (mã vận đơn `999800020077015`, lọc đúng 27/07) cho thấy một dòng
"xuất" mang **ngày huỷ 22:35 27/7** và tile **−2**. Đơn này xuất ngày 23/07, bị
huỷ ngày 27/07 để hạ COD rồi xuất lại `.01`.

Trong khoảng lọc 27/07 chỉ có bút toán ĐẢO (`Hoàn tồn do huỷ đơn`):

- `_dispatch_last_at()` không thấy SALE thật nào → rơi vào fallback
  `Max(occurred_at)` → gắn ngày huỷ cho dòng.
- `net = dispatched(0) − reversed(2) = −2` → tile và footer ra số âm.

Đây mới là gốc, và nó sinh ra CẢ hai triệu chứng. Chỉ bọc `Math.abs` ở tầng
hiển thị sẽ biến −2 thành 2 — che lỗi và còn đọc sai thành "ngày 27 xuất 2 cái".

**Sửa:** nhóm chỉ được coi là một lần xuất khi trong khoảng lọc có bút toán SALE
thật (`_HAS_REAL_SALE`, áp cho `order_groups` / `sku_groups` / `sku_orders` /
`total_issued` / số đếm phân trang). Bút toán đảo VẪN trừ vào SL của đơn khi đơn
đó có lần xuất trong CÙNG khoảng lọc (xuất rồi thu hồi trong ngày) — nó chỉ
không tự dựng một dòng khi đứng một mình.

Hệ quả cần biết: sản lượng xuất của ngày D không còn bị trừ bởi các lần huỷ
trong ngày D của đơn đã xuất ở ngày khác. Con số "xuất ngày D" từ nay là lượng
hàng thật sự rời kho ngày D — đúng thứ tab này để trả lời.

## 2c. Xuất rồi huỷ vẫn là một lần xuất

Ca prod `HDTTS_585205879163881436`: xuất 09:22 27/7 rồi huỷ ngay trong ngày.
Dòng hiện `SL XUẤT = 1` (đúng) nhưng footer "Tổng SL xuất (1 đơn)" ra **0** —
tổng vẫn cộng theo net. Tồn về 0 là đúng, nhưng hàng đã rời kho thật nên sản
lượng phải ghi nhận 1.

Không thể lấy luật "net = 0 thì tính gộp": test `test_summary_excludes_fully_
reversed_orders` đã chốt một luật ngược lại và vẫn đúng — **bỏ đánh dấu đã xuất
kho** (bấm nhầm, reversal note rỗng) thì hàng CHƯA từng rời kho, không được
tính. Phải phân biệt theo loại bút toán đảo:

| Bút toán đảo | Ý nghĩa | Có trừ khỏi sản lượng xuất? |
|---|---|---|
| `Hoàn tồn do huỷ đơn` | hàng đã đi rồi mới huỷ | **Không** |
| `Chuyển sổ xuất sang đơn xuất lại` | hàng đã đi, chỉ đổi đơn đứng tên | **Không** |
| note rỗng (bỏ xuất kho) | hàng chưa từng rời kho | Có |
| `Đồng bộ tồn do sửa items sau xuất kho` | phần hàng đó không đi | Có |

Đồng thời SALE **hành chính** (`ADMIN_SALE_NOTES`) bị loại khỏi tử số: đơn
`<mã>.NN` chỉ NHẬN SỔ chứ không có lần rời kho riêng — tính nữa là đếm hai lần
cùng một kiện hàng.

```
SL xuất của kỳ = Σ( SALE thật − đảo "chưa từng rời kho" )   -- _issued_qty_expr
```

Kiểm lại trên đúng dữ liệu prod: `HDTTS…` = 1 ✅ · bỏ-xuất-kho = 0 ✅ · hoàn một
phần (xuất 3 hoàn 1) = 2 ✅ · cặp `HD013349` + `HD013349.01` = 2 (không phải 4) ✅

**Điểm cần biết khi đọc bảng:** với cặp huỷ-xuất lại, hai dòng cùng hiện 2 (mỗi
đơn đang gánh 2 kiện hàng đó trên sổ của nó) trong khi footer cộng 2 — vì chỉ có
2 kiện thật sự rời kho. Cột dòng trả lời "đơn này gánh bao nhiêu", footer trả
lời "kỳ này bao nhiêu hàng rời kho". Muốn hai con số luôn cộng khớp thì phải cho
dòng `.NN` hiện 0 — đánh đổi ngược lại, và tôi cho là tệ hơn.

## 3. Thay đổi

**Backend** — bổ sung số gộp vào payload (net vẫn giữ):

| Nơi | Field mới |
|---|---|
| `DispatchHistoryService.enrich_orders` → `lines[]` | `gross` |
| `DispatchHistoryService.sku_orders` (drill-down) | `dispatched` |
| `IssueDocumentHistoryService.sku_orders`, `TransferDocumentHistoryService.sku_orders` | `dispatched` (= net, phiếu không có phần đảo) |
| `PendingScanService` lines | `gross` |

`gross` khai báo `required=False` trong `FlowLineBrief`: component này dùng chung
cho cả tab Nhập hoàn và khối mã chờ khớp, nơi `qty` vốn đã là số dương nên không
cần. FE chịu được payload thiếu field (rolling deploy).

**Frontend** — `features/inventory/flow-quantity.ts` giữ luật, áp ở: dòng đơn,
dòng SKU, drill-down theo SKU, và `SkuLines`.

**Excel** — `flow_history_excel._display_qty` mirror đúng luật đó. Màn hình và
file tải về lệch nhau là một đơn hiện hai con số ở hai chỗ.

## 4. Kiểm thử

- `test_orders_page_exposes_gross_quantity_for_fully_reversed_order`,
  `test_sku_orders_drilldown_exposes_gross_quantity` — payload có số gộp.
- `test_export_issue_orders_shows_gross_when_net_is_zero`,
  `test_export_issue_skus_shows_gross_when_net_is_zero` — Excel khớp màn hình.
- `frontend/src/features/inventory/__tests__/flow-quantity.test.ts` — 5 ca của
  luật hiển thị (bình thường, hoàn một phần, net 0, số âm, payload cũ).
- `test_cancel_day_has_no_issue_row`, `test_cancel_day_has_no_sku_row` — lọc
  đúng ngày huỷ: không dòng nào, tổng = 0 (không còn −2).
- `test_dispatch_day_still_lists_the_order` — ngày xuất thật vẫn liệt kê đủ.

## 5. Ngoài phạm vi

Tile tổng ở đầu trang vẫn là tổng net trên toàn bộ bộ lọc: tổng bằng 0 ở đó
nghĩa là "kỳ này không xuất gì" — đúng nghĩa, không phải dữ liệu khuyết. Thẻ kho
(`StockPage`, `StockMovementHistory`) vẫn hiện `+N / −N` vì đó là sổ bút toán,
dấu chính là chiều nhập/xuất.
