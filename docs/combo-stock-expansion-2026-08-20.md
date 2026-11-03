# Nở combo khi ghi sổ tồn kho (đồng bộ cách KiotViet trừ tồn)

Ngày: 2026-08-20 · Nhánh: `feat/combo-stock-expansion`

## 1. Vấn đề

Trên KiotViet, bán một SKU combo sẽ trừ tồn vào **các mã lẻ** theo công thức combo.
Trên CRM, cùng đơn đó trừ tồn vào **chính SKU combo** — một mã không bao giờ được
nhập kho. Hệ quả:

- Tồn mã lẻ trên CRM **cao hơn thực tế** (BNC02 kho 1 = 615, NC01 kho 1 = 816 trong
  khi KiotViet đã trừ rồi).
- Tồn SKU combo **âm vĩnh viễn** ngay từ bút toán đầu tiên (`BNC02+NC01` = −1,
  `DC30+NC01` = −1, `MCK01-COMBOXL` = −1, `GMC01+MSC02` = −12).
- Màn Lịch sử nhập xuất chế độ *Theo SKU* hiện dòng `BNC02+NC01` thay vì tan ra
  `BNC02` + `NC01` (ảnh chụp 19/08).

### Gốc rễ

Đây **không phải bug ngẫu nhiên mà là thiết kế cố ý** khi làm tính năng khai báo
thành phần combo. Nguyên văn trong code:

- `apps/orders/models.py` — `ProductComponent`: *"CHỈ phục vụ báo cáo, KHÔNG tham gia
  ghi sổ tồn kho."*
- `apps/orders/application/services/product_component_service.py` — *"Không có code nào
  trong luồng ghi StockMovement đọc bảng này."*

Grep xác nhận `ProductComponent` chỉ được đọc ở `apps/inventory/exports_definitions.py`
(file Excel) và service quản lý khai báo. Luồng xuất kho trừ thẳng vào `product_id` của
`OrderItem`, tức chính SKU combo.

### Quy mô (đo trên prod 2026-08-20)

| Chỉ số | Giá trị |
|---|---|
| SKU dạng combo trong `products` | 121 |
| Đã khai thành phần | 3 (`DG20-COMBO2`, `GMC01+MSC02`, `MSC02 + GMC01`) |
| Bút toán sai — tháng 7/2026 | 12 đơn · 13 sp |
| Bút toán sai — tháng 8/2026 | 10 đơn · 10 sp |
| Trước tháng 7/2026 | không có |

Nợ dữ liệu nhỏ và mới phát sinh → backfill nhẹ.

Lưu ý: tồn âm là **bệnh chung** của hệ thống (SQA02 −178, DG11 −77) do CRM không chặn
xuất khi chưa nhập. Combo không tạo ra loại lỗi mới mà **sai địa chỉ trừ tồn**.

## 2. Quyết định

1. **Nở combo ngay khi ghi sổ**, giống KiotViet. SKU combo **chưa khai thành phần** vẫn
   trừ vào chính nó (fallback an toàn — không im lặng đánh mất bút toán).
2. **Backfill dữ liệu cũ bằng migration có kiểm toán**, chạy giờ thấp điểm.
3. **Khai thành phần bằng tay trên CRM**, không sync từ KiotViet ở giai đoạn này.

### Vì sao nở BÊN TRONG service ghi sổ, không nở ở từng caller

Hiện có đúng 3 nơi dựng `SaleLine` từ `OrderItem`:

- `apps/inventory/subscribers.py:210` (resync khi sửa items)
- `apps/orders/application/services/order_service_commands.py:551` (resync)
- `apps/orders/application/services/order_service_commands.py:606` (issue)

Nở ở từng caller là lặp lại đúng cái bẫy đã cắn nhiều lần trong codebase này (thêm một
cửa mới, quên một chỗ, lệch số im lặng). Nở tại `issue_for_order` / `resync_for_order`
khiến **mọi caller hiện tại và tương lai đều đúng theo mặc định**.

### Vì sao các luồng hoàn/huỷ/resync không phải sửa

`release_for_order`, `reclaim_auto_release`, `transfer_issue_to_sibling`,
`reissue_after_cancel_release` đều làm việc trên `outstanding_by_key()` — **đọc ngược từ
chính `StockMovement`**. Bút toán gốc ghi theo mã lẻ thì các luồng này tự khớp mã lẻ,
không cần biết combo là gì. Đây là lý do fix gọn được ở một điểm.

## 3. Rủi ro đã nhận diện

| Rủi ro | Xử lý |
|---|---|
| **Phiếu hoàn bị chặn.** `_guard_double_credit` đối chiếu hạn mức theo `outstanding_by_key` (mã lẻ), trong khi UI gợi ý dòng hoàn lấy từ `OrderItem` (mã combo) tại `flow_pending_views.py:96` → nhân viên kho chọn dòng combo sẽ bị báo "đơn nguồn chỉ còn 0 đơn vị" | Nở luôn danh sách `items` gợi ý trong order-lookup (Sprint 2) |
| **Tồn mã lẻ TỤT sau deploy.** BNC02/NC01/DC30… giảm đúng bằng lượng combo đã bán | Báo trước cho vận hành — số tụt là số ĐÚNG, không phải mất hàng |
| Sửa công thức combo sau khi đã bán | Bảng `product_components` không có phiên bản; bút toán cũ giữ nguyên theo sổ, không hồi tố |
| Khai combo lồng nhau | Đã chặn sẵn 2 chiều ở `product_component_service._validate` (chỉ một cấp) |

## 4. Sprint

### Sprint 1 — Lõi ghi sổ ✅
- [x] `expand_desired_quantities()` trong `combo_expansion.py`, fallback giữ nguyên
- [x] Áp vào `issue_for_order` và `resync_for_order`
- [x] `apps/inventory/tests/test_combo_stock_expansion.py` — 6 test

### Sprint 2 — Luồng hàng hoàn ✅
- [x] Nở `items` trong `flow_order_lookup`, gộp theo `product_id`
- [x] 2 test trong `test_flow_history_views.py`

### Sprint 3 — Backfill dữ liệu cũ ✅
- [x] `combo_stock_backfill.py` + migration `0038_backfill_combo_stock`
- [x] `apps/inventory/tests/test_combo_stock_backfill.py` — 7 test

#### Phạm vi backfill thực tế trên prod

Chỉ combo **đã khai thành phần** mới được chuyển. Đo ngày 20/08, duy nhất
`GMC01+MSC02` có hàng đang ngoài kho:

| Kho | Sẽ chuyển | Mã lẻ bị trừ |
|---|---|---|
| 1 | 12 | GMC01 ×1 + MSC02-CAOCAP ×1 |
| 2 | 1 → **bỏ qua** (đã hoàn về kho) | — |

Sau backfill: `GMC01+MSC02` kho 1 từ **−12 về 0**; `GMC01` kho 1 từ −7 xuống **−19**;
`MSC02-CAOCAP` kho 1 từ 250 xuống **238**.

#### Ca hàng đã hoàn về — cố ý bỏ qua

Đơn 548336 bán combo ở kho 1, hàng hoàn về nhập kho 2 dưới **chính SKU combo**. Phiếu
hoàn ghi `reference_type='return_entry'` nên không làm giảm outstanding của đơn (xem
`order_goods_still_out`). Chuyển sổ phần này sẽ trừ mã lẻ trong khi một bộ combo vẫn
nằm trong kho — tức tạo tồn ảo.

Backfill trừ credit `returned_by_product` đúng như `release_for_order`, bỏ qua phần đã
về và ghi `logger.warning`. **Cố ý không tự động quy đổi** 1 combo trong kho thành các
mã lẻ: đó là hàng vật lý, phải đi qua phiếu chuyển đổi/kiểm kê của kho chứ không phải
một migration âm thầm. `combo_outstanding_report` dùng cùng công thức để log kiểm toán
không báo động giả sau khi chạy.

## 5. Ghi chú sau mỗi sprint

### Đã làm

- Nở combo đặt **bên trong** `issue_for_order` / `resync_for_order`. Ba caller hiện
  có không phải sửa dòng nào, caller tương lai cũng đúng theo mặc định.
- `resync_for_order` nở **trước** khi so với `deducted_by_product`; so thẳng SKU combo
  sẽ thấy "combo thừa, mã lẻ thiếu" rồi vừa hoàn vừa trừ một lượt.
- `COMBO_BACKFILL_ISSUE_NOTE` đã đăng ký vào `ADMIN_SALE_NOTES`. Thiếu bước này thì
  `real_dispatch_occurred_at` coi ngày chạy migration là ngày hàng rời kho của đơn —
  ngày xuất của mọi đơn được backfill sẽ nhảy sang hôm nay.
- Backfill giữ `occurred_at` của bút toán gốc, không dùng `now()`, để sản lượng xuất
  không nhảy kỳ báo cáo.
- Hai note chuyển sổ đặt ở `stock_sales_service` cùng các note khác, và
  `all_combo_components()` đặt trong `combo_expansion` — giữ một cửa duy nhất đọc
  `ProductComponent` từ phía inventory, không phát sinh nợ `.importlinter` mới.

### Chưa làm / nợ lại

- **Khai thành phần cho 118 SKU combo còn lại.** Cho tới khi khai, các SKU đó vẫn trừ
  vào chính nó như cũ. Khai xong chạy lại backfill là chuyển được (idempotent).
- **Chưa sync công thức combo từ KiotViet.** Đã chốt khai tay giai đoạn này.
- **Kiểm tra lại tồn sau deploy.** Tồn mã lẻ sẽ TỤT đúng bằng lượng combo đã bán —
  cần báo trước cho vận hành, đây là số đúng chứ không phải mất hàng.
- Frontend chưa đụng gì: màn Lịch sử nhập xuất vốn đã nở combo ở tầng report.

### Đề xuất tách riêng (ngoài phạm vi MR này)

`stock_warnings` chỉ trả `{product_id, balance_after}` và FE in ra `#<id>: <số>`
(`use-orders-data.ts`). Sau thay đổi này id trong cảnh báo là **mã lẻ** chứ không còn
là SKU khách đặt, nên câu cảnh báo càng khó đọc. Vốn đã khó đọc từ trước (id thô,
không tên) nên không sửa kèm ở đây — nên bổ sung `sku`/`name` vào payload cảnh báo
trong một MR riêng.


## 6. Sprint 4 — Sửa hiển thị sau backfill (20/08, sau khi chạy prod)

Chạy backfill trên prod xong thì màn Lịch sử nhập xuất vẫn hiện dòng `BNC02+NC01`
SL 1, còn `NC01` hiện "2 đơn nhưng SL 1". Sổ ghi ĐÚNG (4 bút toán, đều mang ngày
19/08) — sai ở tầng ĐỌC, do chính hai quyết định ở Sprint 1/3.

### Nguyên nhân 1 — xếp nhầm note vào `ADMIN_SALE_NOTES`

Danh sách đó không chỉ dùng cho ngày xuất (`real_dispatch_occurred_at`) mà còn để
tính SẢN LƯỢNG xuất (`_issued_qty_expr`) và ngày hiển thị (`_dispatch_last_at`).
Xếp `COMBO_BACKFILL_ISSUE_NOTE` vào đó làm hai bút toán trên mã lẻ biến mất khỏi
cột SL.

Việc xếp vào vốn đã THỪA: mọi note khác trong danh sách mang mốc LÚC GHI, còn bút
toán nhận sổ từ combo giữ `occurred_at` GỐC nên không thể gây lệch ngày. Đã bỏ ra,
kèm comment tại chỗ giải thích vì sao nó cố ý khác ba note còn lại.

### Nguyên nhân 2 — `display_qty` fallback sang `gross`

`display_qty(net, gross) = abs(net) or abs(gross)` là quy tắc CÓ CHỦ Ý cho ca "đơn
xuất rồi huỷ/chuyển sổ hết trong kỳ" — hàng đã rời kho nên vẫn ghi nhận một lần
xuất. Nhưng với combo chuyển sổ sang mã lẻ thì phần đó ĐÃ được đếm ở mã lẻ, giữ
thêm ở combo là đếm hai lần cùng một kiện hàng.

Đã thêm `_combo_moved_expr()`, trừ khỏi `dispatched` và ẩn dòng combo khi chuyển sổ
hết. Combo chuyển sổ MỘT PHẦN (khai thành phần muộn) vẫn hiện phần còn lại.

### Hệ quả số liệu

Tổng SL xuất ngày 19/08 **tăng 168 → 170**: một combo rời kho thực chất là HAI món,
trước đây đếm là một. Excel dùng chung `MergedIssueHistory.skus_page` nên tự khớp.

### Bài học

Một note ghi sổ không chỉ là nhãn — nó tham gia vào các biểu thức đọc ở tầng báo
cáo. Trước khi thêm note mới vào một danh sách có sẵn, phải grep xem danh sách đó
được dùng ở NHỮNG ĐÂU, không chỉ ở chỗ mình đang quan tâm.

## 7. Sprint 5 — Dòng combo trùng ở chế độ Theo đơn (23/08)

Người dùng báo: file Excel "Lịch sử nhập xuất → tab Xuất" hiện mã lẻ HAI DÒNG cho
cùng một mã vận đơn (ca prod `SPXVN066980553678`, combo `DC30+NC01`).

### Nguyên nhân — Sprint 4 mới vá nửa tầng đọc

`_combo_moved_expr()` chỉ được áp ở `sku_groups` (chế độ **Theo SKU**). Chế độ
**Theo đơn** dựng dòng SKU của từng đơn ở `DispatchHistoryService.enrich_orders`
— một khối `values(...).annotate(...)` RIÊNG, không đi qua `sku_groups` — nên vẫn
cộng thô:

```
combo DC30+NC01 : SALE −1, SALE_REVERSAL +1 ("Chuyển sổ combo sang mã lẻ")
                  → qty = 0, gross = 1  → display_qty rơi về gross = 1
mã lẻ DC30      : SALE −1 ("Nhận sổ từ SKU combo") → 1
mã lẻ NC01      : SALE −1 → 1
```

Dòng combo còn sót được file Excel **nở tiếp** ra chính DC30 + NC01 → mỗi mã lẻ
xuất hiện hai lần. Màn hình dùng chung `lines` nên cũng sai y hệt. Cột "Số SKU"
đếm cả SKU combo → nói 3 trong khi đơn chỉ còn 2 mã lẻ.

Tổng theo đơn (`_issued_qty_expr`) và footer "SKU ghi sổ" vẫn ĐÚNG — chỉ phần bung
dòng sai, nên lỗi im lặng: các ô tổng không tố cáo gì.

### Sửa

`enrich_orders` annotate thêm `combo_moved`, `gross = −sale_sum − combo_moved`, bỏ
hẳn dòng khi combo đã chuyển sổ hết, và `sku_count = len(lines)` (giống
`issue_document_history` / `transfer_document_history` vốn đã đếm theo dòng).
Điều kiện bỏ dòng gác bằng `combo_moved` để không đụng các bút toán đảo khác.

### Hệ quả số liệu (đo prod 23/08)

13 đơn có bút toán chuyển sổ (07/07 → 20/08). Sau khi deploy, file Excel và màn
hình **bớt 28 dòng và 28 đơn vị** ở "Tổng SL các dòng trong file"; các ô tổng theo
SKU ghi sổ KHÔNG đổi. Ngày ảnh hưởng nhiều nhất: 15/08 (3 đơn, 6 đơn vị).

### Bài học

Cùng một luật nghiệp vụ đang sống ở HAI query song song (`sku_groups` cho Theo SKU
và `enrich_orders` cho Theo đơn). Vá một bên là lệch — grep cả hai trước khi chốt.

## 8. Sprint 6 — Quét trả hàng bị chặn vì dòng combo (07/09)

Kho báo: quét trả hàng loạt mã `VNGH80923248708` (đơn `DHTTS_585690038434628865`,
combo `DC30+NC01`) bị chặn

```
✗ Sản phẩm #3862: đơn nguồn chỉ còn 0 đơn vị chưa được cộng lại tồn (phiếu nhập 1).
```

Đây **đúng là rủi ro đã ghi ở mục 3** ("Phiếu hoàn bị chặn"), và Sprint 2 đã xử —
nhưng chỉ vá MỘT trong HAI cửa.

### Nguyên nhân

Đối chiếu trên prod:

```
order_items  đơn 556614 : product_id 3862 (combo DC30+NC01) × 1
stock_movements đơn 556614: sale −1 sp 31 (DC30), sale −1 sp 3857 (NC01)
```

Sổ trừ theo mã lẻ, còn màn quét lấy dòng hàng từ `_serialize` trong
`apps/orders/transport/views/shipping_lookup_view.py`, nơi trả thẳng `OrderItem`
chưa nở. `_guard_double_credit` đọc `outstanding_by_key` (mã lẻ) → hạn mức của
3862 bằng 0 → chặn.

Sprint 2 nở `items` ở `flow_order_lookup` (panel khớp tay). Nhưng ba màn dùng
`lookup-by-shipping-code` chứ không dùng endpoint đó:

| Màn | mode | `items` dùng làm gì |
|---|---|---|
| Quét trả hàng (hàng loạt) | `return_scan` | dòng `ReturnEntry` → **chặn** |
| Phiếu kho thủ công | `return` | dòng `StockDocument` → **chặn** |
| Quét xuất giao | `dispatch` | chỉ hiển thị / gộp "tổng hàng cần lấy" |

### Sửa

Luật nở chuyển thành MỘT bản dùng chung: `apps/catalog/combo_lines.py`
(`expand_order_lines` / `expand_order_lines_bulk`). Cả hai cửa gọi nó —
`shipping_lookup_view._expanded_items` (bật qua `_RETURN_LINE_MODES`) và
`flow_order_lookup`, nơi vòng lặp nở+gộp viết tay ở Sprint 2 bị xoá.

**Vì sao đặt ở `catalog` chứ không ở `inventory`:** hai màn cần luật này nằm ở hai
tầng khác nhau. Để helper ở `inventory` thì `orders` phải thêm một dòng nợ vào
KHỐI B của `.importlinter` — khối **CHỈ ĐƯỢC GIẢM** (`lint-imports` đã báo
`apps.orders is not allowed to import apps.inventory` khi thử cách đó). `catalog`
nằm dưới cả `orders` lẫn `inventory` nên cả hai import hợp lệ; module cũng chỉ đụng
`ProductComponent`, đúng chỗ của nó.

Hai luật giữ nguyên từ đường ghi sổ: combo chưa khai thành phần giữ nguyên, và
combo chứa mã A gộp với dòng A bán lẻ thành MỘT dòng (bút toán đã gộp, mà
`build_lines` còn chặn thẳng hai dòng trùng `(product_id, return_type)`).

**Cố ý không nở `mode=dispatch`**: ở đó lines xuất kho do `issue_for_order` tự dựng
từ `OrderItem` rồi nở bên trong, `items` chỉ để hiển thị — nở thêm chỉ đổi thứ
nhân viên nhặt hàng nhìn thấy mà không sửa lỗi nào. Có test khoá ranh giới này.

Frontend không phải sửa: `defaultReturnLines` và `mergeOrderItemsIntoLines` vốn đã
gộp theo `product_id`.

### Bài học

Lặp lại đúng bài học Sprint 5 ở một tầng khác: cùng một luật ("dòng hàng gợi ý cho
phiếu hoàn phải theo mã lẻ") sống ở HAI endpoint song song — `flow_order_lookup`
và `lookup-by-shipping-code`. Mục 3 đã nhận diện đúng rủi ro nhưng chỉ trỏ tới một
cửa (`flow_pending_views.py:96`), nên bản vá dừng ở đó. Khi rủi ro là "UI gợi ý
dòng hoàn", phải grep MỌI đường UI lấy dòng hoàn, không chỉ đường đầu tiên tìm ra.
