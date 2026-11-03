# Xem tồn kho cuối ngày tại một mốc thời gian

Ngày: 2026-08-12
Nhánh: `feat/stock-as-of-date`

## 1. Vấn đề

Trang Tồn kho (`frontend/src/features/inventory/pages/StockPage.tsx`) chỉ hiển thị
tồn **hiện tại**, đọc từ bảng `stock_items` — một projection không giữ lịch sử.
Khi cần đối chiếu kiểm kê, giải trình chênh lệch hay tra lại số liệu một ngày đã
qua, không có cách nào biết tồn cuối ngày hôm đó là bao nhiêu.

Sổ cái `stock_movements` là append-only và có cột `occurred_at` (ngày nghiệp vụ —
lúc hàng thực sự rời/về kho), nên dựng lại tồn tại một mốc bất kỳ là khả thi mà
không cần thêm dữ liệu mới.

## 2. Phạm vi

**Trong phạm vi**: chọn **một** ngày trên trang Tồn kho hiện tại → bảng hiển thị
tồn cuối ngày đó (23:59 giờ VN), kèm xuất Excel theo cùng mốc.

**Ngoài phạm vi** (cố ý bỏ, không phải quên):
- Báo cáo Nhập – Xuất – Tồn theo kỳ (tồn đầu kỳ / nhập / xuất / tồn cuối kỳ).
- Chọn mốc chi tiết đến giờ:phút. Bút toán quét khớp trễ được gán về *ngày*
  nghiệp vụ chứ không phải giờ thực ghi sổ, nên số theo giờ sẽ lệch với mọi báo
  cáo khác trong hệ thống.
- Trang báo cáo riêng. Tính năng nằm ngay trên trang Tồn kho để không nhân đôi
  bảng và không thêm mục menu.

## 3. Phương án đã chọn

**Trừ ngược từ tồn hiện tại.**

```
end = local_midnight(as_of + 1 ngày)          # cận trên nửa mở, giờ VN
tồn(as_of) = stock_items.quantity − Σ qty_delta của movement có occurred_at ≥ end
```

Đúng tuyệt đối vì `stock_items` chính là projection của toàn bộ `stock_movements`
(xem `management/commands/rebuild_stock_items.py`): trừ đi phần biến động sau mốc
thì còn lại đúng số dư tại mốc.

### 3.1 Vì sao không cộng xuôi từ đầu sổ

`Σ qty_delta WHERE occurred_at < end` cũng đúng, nhưng `stock_movements` được
RANGE-partition theo tháng trên `created_at`, nên cộng xuôi luôn quét toàn bộ
partition lịch sử — kể cả khi người dùng chỉ xem hôm qua, tức là ca dùng phổ biến
nhất. Trừ ngược thì càng gần hiện tại càng nhanh.

### 3.2 Vì sao không dựng bảng snapshot cuối ngày + cron

Tra nhanh hơn, nhưng phải backfill toàn bộ lịch sử, thêm một bảng, một job, và
một nguồn số có thể lệch sổ. Bảng `stock_items` hiện dưới 20.000 dòng (trần
`_STOCK_MAX_ROWS` của export tồn kho đã ngầm khẳng định). Không tương xứng chi
phí. Nếu sau này thật sự chậm, phương án này vẫn cắm thêm được phía sau cùng API,
không phải sửa hợp đồng.

### 3.3 Cắt partition

Thêm điều kiện `created_at ≥ end` bên cạnh `occurred_at ≥ end`. Nhờ bất biến
`occurred_at ≤ created_at` (ép ở `StockCommandService`), điều kiện này **không đổi
kết quả**: mọi dòng bị nó loại thêm đều có `created_at < end`, kéo theo
`occurred_at < end`, tức đằng nào cũng bị loại. Nhưng nó cắt sạch mọi partition cũ
hơn mốc.

Luật này đã được ghi và giải thích ở `apps/inventory/application/services/movement_window.py`.
**Dùng lại helper ở đó**, tuyệt đối không chép luật ra file mới — module đó tự
tuyên bố là nơi duy nhất giữ luật, hai bản sao sẽ lệch nhau lúc nào không hay.

So sánh phải trên **cột trần** (`__gte`/`__lt` trên datetime), không được cast
`__date` — cast làm Postgres mất khả năng cắt partition.

## 4. Backend

### 4.1 Service mới

`apps/inventory/application/services/stock_as_of_service.py` — một nhiệm vụ duy
nhất: nhận danh sách `StockItem` đã lọc + mốc ngày, trả về số tồn tại mốc đó.

```python
deltas = (
    StockMovement.objects
    .filter(occurred_at__gte=end, created_at__gte=end)
    .values("warehouse_id", "product_id", "state")
    .annotate(delta=Sum("qty_delta"))
)
as_of_qty = item.quantity - deltas.get((wh, prod, state), 0)
```

Truy vấn delta được thu hẹp theo đúng bộ lọc của màn hình (`warehouse_id`, và tập
`product_id` khi có tìm kiếm) để không gom thừa.

`StockQueryService` giữ nguyên trách nhiệm hiện tại (đọc tồn hiện tại); logic mốc
nằm ở service riêng, không trộn vào.

### 4.2 API

Giữ nguyên endpoint `GET /api/inventory/stock`, thêm query param `as_of=YYYY-MM-DD`.

| Đầu vào | Hành vi |
|---|---|
| Không truyền `as_of` | Hành vi hiện tại, không đổi một dòng nào |
| `as_of` = hôm nay | Trả tồn hiện tại (đọc thẳng `stock_items`, bỏ qua phép trừ) |
| `as_of` trong quá khứ | Tính theo §3 |
| `as_of` ở tương lai | 400 |
| `as_of` sai định dạng | 400 |

Parse ngày bằng `strptime("%Y-%m-%d")` (không dùng `date.fromisoformat`) để đồng
bộ với `_valid_date` ở `flow_history_views.py`.

Response thêm trường `"as_of"` echo lại giá trị đã áp dụng, để frontend hiển thị
đúng cái nó nhận được thay vì đúng cái nó gửi đi.

### 4.3 Phân trang

Ở chế độ `as_of`, số tồn tại mốc không nằm trong bảng nên không thể `LIMIT/OFFSET`
ở DB. Luồng: lấy toàn bộ tập `StockItem` đã lọc → tính tồn tại mốc → lọc/sắp xếp
→ cắt trang trong Python. `total` là số dòng sau khi lọc.

Guard an toàn: nếu tập đã lọc vượt `_STOCK_MAX_ROWS` (20.000, dùng lại hằng số của
export tồn kho) thì trả 400 kèm thông báo yêu cầu lọc thêm kho/mã, thay vì để một
request kéo sập worker.

Thứ tự sắp xếp giữ nguyên như hiện tại: `product__sku`, `warehouse__name`, `id`.

### 4.4 Ba quyết định nghiệp vụ

1. **Ẩn cặp khi TẤT CẢ trạng thái đều bằng 0**, không phải khi tổng bằng 0. Cặp
   (kho, mã) chỉ bị ẩn tại mốc khi cả ba dòng trạng thái (sellable/warranty/
   defect) đều = 0 — sản phẩm chưa nhập về thời điểm đó không nên chiếm chỗ.
   Lọc theo TỔNG sẽ sai: `StockItem.quantity` được phép âm (bán vượt hợp lệ),
   nên ca SELLABLE=+5 / DEFECT=−5 có tổng 0 nhưng vẫn còn 5 chiếc bán được thật
   — ẩn theo tổng sẽ giấu mất số đó. Việc lọc làm theo **cặp (kho, mã)**, không
   theo từng dòng trạng thái riêng lẻ: loại thì loại cả ba dòng cùng lúc. Chế
   độ tồn hiện tại giữ nguyên hành vi cũ (không lọc).

2. **RR3/RR7 tính theo 3/7 ngày trước mốc**, không phải trước hôm nay. Xem lại
   ngày 05/08 thì phải thấy tốc độ bán của ngày 05/08.
   `StockQueryService.sales_run_rates` nhận thêm tham số mốc kết thúc (mặc định
   `timezone.now()` — không đổi hành vi hiện tại) và thêm cận trên
   `occurred_at < end`. Cửa sổ dưới vẫn kèm `created_at ≥ since_7d` như hiện tại
   để cắt partition.

3. **Xuất Excel dùng chung mốc.** Export kind `stock` nhận thêm param `as_of`,
   gọi cùng service. Tên file kèm ngày (`ton-kho-2026-08-05.xlsx`) để không lẫn
   với file tồn hiện tại.

### 4.5 Không đụng tới

Không thêm bảng, không migration, không index mới. Việc thêm index trên
`occurred_at` chỉ xét **sau khi** có số `EXPLAIN` đo trên prod — thêm index vào
bảng partition là thao tác tốn, không quyết định bằng phỏng đoán.

## 5. Frontend

### 5.1 Bộ chọn ngày

Component `DateFilter` sẵn có là bộ chọn **khoảng** ngày, không hợp ngữ nghĩa một
mốc đơn. Dùng `Input type="date"` với `max` = hôm nay, kèm nút "Hiện tại" để xoá
về mặc định. Không tạo component mới.

Nhãn: *"Xem tồn đến ngày"*. Bỏ trống = tồn hiện tại.

Trạng thái `as_of` giữ trong React state cùng chỗ với bộ lọc kho hiện có; không
đẩy lên URL, giữ đúng khuôn màn hình này đang dùng.

### 5.2 Khi đang xem quá khứ

1. **Dải cảnh báo** màu hổ phách ngay trên bảng:
   *"Đang xem tồn cuối ngày 05/08/2026 — số liệu tra cứu, không phản ánh tồn hiện tại."*

2. **Ẩn nút "Xử lý tồn"** và mọi hành động ghi sổ. Đây là điểm an toàn quan trọng
   nhất: cho phép bấm phân loại lại tồn khi màn hình đang hiển thị số của tuần
   trước là công thức tạo sai lệch kho. Backend không cần đổi — `reclassify` vốn
   luôn làm việc trên tồn hiện tại.

3. **Lịch sử biến động cắt tại mốc.** Bung dòng chỉ hiện bút toán đến hết ngày đã
   chọn. Nếu không, người dùng thấy tồn ngày 05/08 nhưng bên dưới liệt kê bút toán
   ngày 10/08 — mâu thuẫn ngay trên một màn hình. Cần thêm param `as_of` cho
   endpoint danh sách movements, lọc `occurred_at < end`.

   Lưu ý: mẹo cắt partition ở §3.3 **chỉ áp dụng cho cận dưới**. Ở đây là cận
   trên, mà `created_at` được phép lớn hơn `occurred_at` (chính là ca quét khớp
   trễ), nên thêm `created_at < end` sẽ âm thầm loại mất đúng những dòng cần
   hiện. Chấp nhận quét các partition mới hơn mốc — truy vấn đã hẹp sẵn theo
   (kho, mã).

4. **Xuất Excel** truyền cùng mốc.

## 6. Kiểm thử

Bám cấu trúc suite sẵn có của app `inventory`, một file cho tính năng.

Backend:
- Tồn tại mốc khớp với tổng cộng xuôi từ sổ — **đối chiếu chéo hai chiều**: cách
  trừ ngược (§3) và cách cộng xuôi phải ra cùng một số. Đây là test bắt được ca
  `stock_items` lệch sổ.
- Bút toán ghi sổ trễ (`occurred_at` ngày D, `created_at` ngày D+3) rơi đúng vào
  ngày D, không rơi vào D+3.
- Mốc = hôm nay trả đúng tồn hiện tại.
- Mốc ở tương lai / sai định dạng trả 400.
- Cặp (kho, mã) tổng 0 tại mốc bị ẩn; cặp có 1 trạng thái khác 0 thì giữ đủ ba dòng.
- Ranh giới nửa đêm giờ VN: bút toán lúc 23:30 ngày D thuộc về ngày D; lúc 00:30
  ngày D+1 không thuộc ngày D.
- RR3/RR7 ở chế độ mốc tính theo cửa sổ trước mốc.
- Guard vượt trần số dòng trả 400.

Frontend:
- Ẩn nút xử lý tồn khi có mốc; hiện lại khi xoá mốc.
- Xoá mốc thì bảng quay về tồn hiện tại.
- Dải cảnh báo hiện đúng ngày server echo về.

**Bom hẹn giờ**: fixture tuyệt đối không cắm ngày cứng. Dữ liệu dựng theo mốc
tương đối so với "hôm nay", nếu không suite sẽ tự đỏ vào ngày 1 hàng tháng.

## 7. Rủi ro

| Rủi ro | Đánh giá |
|---|---|
| `stock_items` lệch sổ → snapshot lệch theo | Lệch y hệt màn hình tồn hiện tại đang lệch, không sinh mâu thuẫn mới. Đã có `rebuild_stock_items` để nắn. Test đối chiếu chéo ở §6 chính là cái bắt được ca này. |
| Chọn ngày rất xa → quét nhiều partition | Chấp nhận. Tần suất thấp, và vẫn ít hơn phương án cộng xuôi. Đo `EXPLAIN` trên prod sau khi deploy trước khi quyết định thêm index. |
| Tính toán trong Python thay vì DB | Trần 20.000 dòng có guard chặn. |

## 8. Nợ kỹ thuật ghi nhận

- Đo `EXPLAIN` trên prod với mốc 30/90/180 ngày trước, quyết định có thêm index
  `(occurred_at, warehouse, product)` hay không.
- Phân trang hiện tại đếm theo dòng `StockItem` (mỗi trạng thái một dòng) trong
  khi bảng web pivot ba trạng thái thành một dòng — số dòng hiển thị luôn ít hơn
  `limit`. Đây là hành vi **có sẵn**, không phát sinh từ tính năng này; ghi nhận
  để xử lý riêng, không mở rộng phạm vi lần này.
