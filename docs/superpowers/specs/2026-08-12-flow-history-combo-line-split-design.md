# Lịch sử nhập xuất: tách dòng theo mã hàng + nổ SKU combo trong file Excel

Ngày: 12/08/2026

> **Ghi chú cập nhật (13/08/2026 — sau khi thực thi xong):** Tài liệu này là
> thiết kế gốc, giữ nguyên bên dưới để lưu lịch sử quyết định. Hai điểm đã đổi
> khác đi trong lúc thực thi (khác với những gì spec mô tả ở phần "Các dòng
> Tổng" và "Kiểm thử"):
>
> 1. **Đổi tên dòng tổng cuối** từ `Tổng SL sau nổ combo` thành `Tổng SL các
>    dòng trong file`. Lý do: tên cũ ngụ ý sai rằng chênh lệch với các dòng
>    "... (SKU ghi sổ)" chỉ đến từ việc nổ combo, trong khi thực tế còn một
>    nguồn chênh lệch khác — cách tính SL của từng dòng chi tiết dùng số đã
>    từng xuất/hoàn TRƯỚC khi đảo sổ, còn dòng ghi sổ chỉ tính phần còn thật sự
>    ở ngoài kho sau đảo sổ (xem mục "SP-A vẫn hiện SL... đảo sổ" trong tài
>    liệu người dùng). Tên mới trung lập, không gán nguyên nhân.
> 2. **Cả bốn nhánh (2 tab Xuất/Nhập hoàn × 2 chế độ Theo đơn/Theo SKU) đều có
>    dòng tổng này**, kể cả chế độ Theo SKU — spec/kế hoạch ban đầu chỉ tính
>    thêm dòng này ở chế độ Theo đơn, chế độ Theo SKU dừng ở dòng `Tổng cộng
>    (SKU ghi sổ)`. Khi thực thi nhận ra chế độ Theo SKU mới là nơi CẦN dòng
>    tổng này nhất: mọi dòng ở chế độ này đều đã nở/gộp combo, nên người dùng
>    càng dễ SUM cột và ra số khác dòng ghi sổ nếu thiếu dòng đối chiếu.

## Vấn đề

File Excel xuất từ trang Lịch sử nhập xuất (chế độ "Theo đơn") gộp mọi mã hàng
của một đơn vào **một ô** — cột SKU ra chuỗi `"SP A, SP B"` — còn cột SL chỉ có
**một con số tổng của cả đơn**. Người dùng không lọc, không pivot, không cộng
được theo từng mã hàng.

Đồng thời, khi đơn có SKU combo: khách mua 2 combo thì mỗi sản phẩm con phải
được ghi nhận 2, nhưng file hiện chỉ hiện đúng một dòng `SL = 2` cho SKU combo.
Hệ thống hiện **không có bất kỳ nơi nào khai báo combo gồm những sản phẩm nào**
— `Product` là bảng phẳng, `ProductPackage` chỉ là "gói N cái của cùng một sản
phẩm", `OrderItem` trỏ thẳng một product.

## Phạm vi

| Có làm | Không làm |
| --- | --- |
| Bảng khai báo thành phần combo + API + UI trên màn Sản phẩm | Đổi luồng ghi sổ tồn kho sang sản phẩm con |
| Tách dòng theo mã hàng ở file Excel tab Xuất và tab Nhập hoàn | Đổi bảng hiển thị trên màn hình web |
| Nổ combo ở cả chế độ "Theo đơn" và "Theo SKU" của file Excel | Đồng bộ BOM từ KiotViet |

Tồn kho **vẫn ghi sổ theo SKU combo như hiện nay**. Việc nổ combo chỉ xảy ra ở
tầng xuất báo cáo.

## Phần 1 — Khai báo thành phần combo

### Mô hình dữ liệu

Bảng mới `product_components` (`apps/orders/models.py`, đặt cạnh
`ProductPackage`):

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| `parent_id` | FK → `Product`, `CASCADE` | SKU combo |
| `component_id` | FK → `Product`, `PROTECT` | Sản phẩm con |
| `quantity` | `PositiveIntegerField` | Số lượng sản phẩm con trong **1** combo |
| `created_at` / `updated_at` | datetime | |

Ràng buộc:

- `UniqueConstraint(parent, component)` — một sản phẩm con chỉ khai một lần
  trong một combo.
- `CheckConstraint(quantity >= 1)` — thành phần số lượng 0 là dữ liệu rác.
- `CheckConstraint(parent_id != component_id)` — combo không chứa chính nó.
- Index trên `parent` (đường đọc chính) và trên `component` (phục vụ câu hỏi
  ngược "SKU này nằm trong những combo nào").

`component` để `PROTECT`: không cho xoá cứng một sản phẩm đang là thành phần của
combo — nếu cho xoá thì file Excel cũ mất dòng mà không ai biết.

### Quy tắc nghiệp vụ

1. **Chỉ một cấp.** Sản phẩm con không được tự nó là combo. Chặn khi lưu ở tầng
   service (kiểm tra `ProductComponent.objects.filter(parent_id__in=component_ids)`
   rỗng). Lý do: nổ đệ quy nhiều cấp mở ra khả năng vòng lặp và làm số liệu khó
   truy vết, trong khi nghiệp vụ hiện tại không cần.
2. **Có ≥ 1 dòng thành phần ⇒ là combo.** Không thêm cờ `is_combo` — một nguồn
   sự thật duy nhất, không có nguy cơ cờ lệch với dữ liệu.
3. **BOM không có phiên bản.** Sửa thành phần hôm nay thì file Excel của kỳ
   trước cũng nổ theo công thức mới. Đây là đánh đổi có ý thức: làm versioning
   đòi gắn snapshot BOM vào từng `StockMovement`, tốn gấp nhiều lần cho một nhu
   cầu thuần báo cáo. Giới hạn này phải ghi trong tài liệu người dùng.
4. Bảng này **không tham gia ghi sổ tồn kho**. Không có code nào trong luồng
   `StockMovement` được đọc nó.

### API

Đặt trong `apps/orders`, cùng module với các view danh mục sản phẩm hiện có:

- `GET /api/products/{id}/components/` — trả danh sách thành phần (`sku`, `name`,
  `quantity`).
- `PUT /api/products/{id}/components/` — ghi đè trọn danh sách.

Chọn ghi đè trọn gói thay vì CRUD từng dòng: người dùng sửa cả bảng trong một
dialog rồi bấm Lưu; ghi đè tránh trạng thái nửa vời khi một request lẻ thất bại.

Quyền: dùng lại `require_section("products", *ORDER_SECTIONS)` như các view sản
phẩm hiện hành, không sinh quyền mới.

### Giao diện

`frontend/src/pages/ProductsPage.tsx` đã 1397 dòng — không nhét thêm vào đó.
Thêm nút "Thành phần combo" trên mỗi dòng sản phẩm, mở component mới
`frontend/src/features/products/components/ComboComponentsDialog.tsx`:

- bảng các dòng thành phần: ô tìm sản phẩm theo SKU/tên + ô số lượng + nút xoá
- nút "Thêm thành phần"
- chặn tại chỗ: trùng sản phẩm, số lượng < 1, chọn chính sản phẩm đang mở, chọn
  một sản phẩm vốn đã là combo

## Phần 2 — File Excel

### Nguồn dữ liệu

Mỗi phần tử `lines` đã mang sẵn `product_id`, `sku`, `qty`, `gross`
(`dispatch_history_service.enrich_orders`, `transfer_document_history`,
`return_history_service`, `pending_scan_service`). Tách dòng **không cần query
thêm gì** ngoài một query nạp bảng combo cho các `product_id` có mặt trong trang.

### Helper dùng chung

Module mới `apps/inventory/application/services/combo_expansion.py` — **một chỗ
duy nhất** cho cả tab Xuất và tab Nhập hoàn. Màn này từng có bài học hai nhánh
code song song (màn hình và job export) khiến sửa một bên là lệch; helper chung
là cách chặn việc đó tái diễn.

```
component_map(product_ids) -> dict[int, list[{sku, name, quantity}]]   # 1 query
expand(lines)              -> list[{sku, name, combo_sku, qty, gross}]
```

- SKU không phải combo: giữ nguyên một dòng, `combo_sku` rỗng.
- SKU combo: nở thành N dòng, `qty = qty_combo × quantity`, `combo_sku` = SKU
  combo gốc.
- Dòng không có `lines` (đơn chưa khớp mã): trả lại đúng một dòng rỗng SKU —
  không được nuốt mất dòng.

### Bố cục file — chế độ "Theo đơn"

Tab Xuất, thêm hai cột ngay sau `SKU`:

```
Thời gian | Mã hóa đơn | Mã vận đơn | Kho | Người thực hiện |
SKU | Tên sản phẩm | Thuộc combo | Ghi chú | Số SKU | SL xuất
```

Tab Nhập hoàn, cùng quy tắc:

```
Thời gian | Mã phiếu trả | Mã hóa đơn nguồn | Mã vận đơn | Kho | Người thực hiện |
SKU | Tên sản phẩm | Thuộc combo | Loại hoàn | Ghi chú | SL hoàn
```

- Một đơn có 3 mã hàng → 3 dòng; các cột đầu **lặp lại nguyên giá trị** để lọc
  và pivot được trên Excel.
- Cột SL giờ là SL của **riêng mã hàng đó** (dùng `_display_qty(qty, gross)` của
  chính dòng), không còn là tổng đơn.
- Cột "Số SKU" (chỉ có ở tab Xuất) giữ nguyên, lặp giá trị của đơn — không phá
  công thức người dùng đang có.
- Dòng phiếu xuất kho / phiếu chuyển kho / dòng "chờ khớp" tách y hệt.

### Bố cục file — chế độ "Theo SKU"

Dòng của SKU combo biến mất; SL cộng dồn vào các sản phẩm con. Thêm một cột
"Trong đó từ combo" để phân biệt phần đến từ combo với phần bán lẻ — ở chế độ
này một SKU con có thể vừa bán lẻ vừa nằm trong nhiều combo nên không thể dùng
cột "Thuộc combo" như chế độ Theo đơn.

Nguồn `skus_page` tổng hợp sẵn trên DB toàn kỳ; việc nổ và cộng lại làm trong
Python trên tập dòng đã có.

### Các dòng Tổng

Đây là chỗ dễ sai nhất: tổng hiện tại tính trên DB toàn kỳ theo SKU **ghi sổ**
(combo tính 1), trong khi các dòng chi tiết đã nổ. Để nguyên thì người dùng SUM
cột sẽ ra số lớn hơn footer và tưởng file lỗi.

- **Giữ nguyên** bốn dòng tổng hiện có, đổi nhãn thành `Tổng theo đơn (SKU ghi
  sổ)`, `Tổng phiếu xuất kho (SKU ghi sổ)`, ... — đây là số đối chiếu được với
  tồn kho thật, không được đụng vào.
- **Thêm một dòng cuối** `Tổng SL sau nổ combo` = cộng đúng các dòng có trong
  file.

Khi file bị cắt vì vượt trần, dòng cảnh báo sẵn có (`_truncation_rows`) đã nói
rõ file chỉ chứa N dòng đầu; `Tổng SL sau nổ combo` vì vậy chỉ phản ánh phần có
trong file, đúng như tên gọi.

### Trần số dòng

`_EXPORT_LIMIT = 5000` đang đếm theo **đơn/phiếu**, giữ nguyên. Sau khi nổ, file
có thể ra 10–15 nghìn dòng — Excel chịu tốt, chỉ job chạy lâu hơn. Đổi trần sang
đếm theo dòng sẽ làm ngữ nghĩa cảnh báo cắt dòng lệch với con số `total` mà
service trả về.

## Dọn kèm

Bốn hàm `export_issue_orders`, `export_return_notes`, `export_issue_skus`,
`export_return_skus` trong
`backend/apps/inventory/infrastructure/flow_history_excel.py` là **code chết** —
chỉ còn test gọi, ba view đồng bộ dùng chúng đã xoá từ Task 18 của đợt async
export. Để lại thì mỗi thay đổi bố cục file phải sửa hai nơi và nơi thứ hai
không ai chạy. Xoá cả bốn hàm cùng test của chúng; giữ lại các helper
(`_fmt_dt`, `_display_qty`, `_issue_order_code`, `_pending_mark`,
`_truncation_rows`) vì `exports_definitions.py` còn dùng. `_lines_text` bị thay
bằng helper nổ combo nên xoá theo.

## Kiểm thử

Backend:

- Model: ràng buộc trùng thành phần, `quantity = 0`, combo tự trỏ chính nó.
- Service `combo_expansion`: SKU thường giữ nguyên; combo 2 thành phần × 2 đơn
  vị ra đúng số lượng nhân; combo lồng combo bị chặn khi khai báo; danh sách
  `lines` rỗng vẫn trả một dòng.
- Export (`test_inventory_export_definitions.py`): đơn 3 mã hàng ra 3 dòng; cột
  "Thuộc combo" đúng; dòng `Tổng SL sau nổ combo` khớp tổng cột; chế độ Theo SKU
  cộng dồn đúng.
- Số query không tăng theo số đơn (chặn N+1).

Frontend: dialog khai thành phần — thêm/xoá dòng, chặn trùng, chặn số lượng < 1,
gọi đúng payload `PUT`.

## Rủi ro và điểm cần theo dõi

1. **Số liệu quá khứ đổi theo BOM hiện hành** (mục Quy tắc nghiệp vụ 3) — cần
   nói rõ với người dùng nghiệp vụ.
2. **Bảng hiển thị trên web vẫn gộp** trong khi file Excel đã nổ; hai nơi cho
   hai con số khác nhau. Đúng phạm vi đã chốt, nhưng phải nêu trong ghi chú
   phát hành để tránh báo lỗi nhầm.
3. **File nặng hơn 2–3 lần** — theo dõi thời gian job export sau khi lên prod.
