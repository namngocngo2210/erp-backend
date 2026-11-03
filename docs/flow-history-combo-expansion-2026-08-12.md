# Lịch sử nhập xuất — khai combo và "nổ" số lượng trong file Excel

Ngày: 2026-08-12
Đối tượng đọc: nhân viên kho, kế toán (người dùng trang **Sản phẩm** và trang
**Lịch sử nhập xuất**)

## 1. Có gì mới

Từ bản này, hệ thống biết một SKU là **combo** gồm những sản phẩm rời nào bên
trong. Khi xuất file Excel ở trang **Lịch sử nhập xuất** (cả tab **Xuất** lẫn
**Nhập hoàn**), những dòng combo sẽ được "nổ" ra thành các sản phẩm con để anh
chị đối chiếu số lượng thật đã ra/vào kho theo từng sản phẩm, thay vì chỉ thấy
một mã combo chung chung.

**Tồn kho trên hệ thống KHÔNG đổi** — kho vẫn ghi sổ và trừ/cộng tồn theo đúng
SKU combo như trước giờ. Việc "nổ" chỉ xảy ra khi xuất file Excel, để phục vụ
đối chiếu/báo cáo.

## 2. Khai báo thành phần combo ở đâu

Vào trang **Sản phẩm**, tìm đúng SKU combo, bấm nút **"Thành phần combo"**
(icon hộp) ở dòng sản phẩm đó. Một hộp thoại hiện ra, cho phép:

- Thêm từng sản phẩm con (tìm theo SKU/tên) và số lượng của sản phẩm con đó
  trong 1 combo.
- Xoá dòng sản phẩm con không còn thuộc combo.
- Bấm **Lưu** để ghi lại — lưu ý: **Lưu sẽ ghi đè toàn bộ danh sách thành
  phần**, không phải chỉ cộng thêm dòng vừa sửa.

Một sản phẩm không khai thành phần nào thì hệ thống coi là sản phẩm đơn lẻ
bình thường, không có gì đổi.

## 3. File Excel "Theo đơn" — thêm 2 cột

Áp dụng cho cả tab **Xuất** (`lich-su-xuat.xlsx`) và tab **Nhập hoàn**
(`lich-su-nhap-hoan.xlsx`) khi xuất ở chế độ mặc định (không tick "Theo SKU").

Mỗi dòng trong file vẫn là **một mã hàng** trong đơn/phiếu (không đổi so với
bản trước). Có thêm 2 cột mới, nằm ngay sau cột SKU:

- **Tên sản phẩm**: tên của đúng sản phẩm ở dòng đó. Nếu dòng này là sản phẩm
  con được nổ ra từ một combo, đây là tên sản phẩm con, không phải tên combo.
- **Thuộc combo**: để trống nếu dòng đó là sản phẩm bán/hoàn lẻ. Nếu dòng đó
  là sản phẩm con được nổ ra từ combo, cột này ghi **mã SKU của combo gốc**
  để anh chị biết sản phẩm con này "sinh ra" từ đơn hàng mua combo nào.

Ví dụ: đơn hàng mua 1 combo `CB01` gồm 2 sản phẩm con `A01` (x1) và `A02` (x2).
Trước đây file chỉ có 1 dòng `CB01`. Nay file có 2 dòng:

| SKU | Tên sản phẩm | Thuộc combo | SL xuất |
|---|---|---|---|
| A01 | (tên A01) | CB01 | 1 |
| A02 | (tên A02) | CB01 | 2 |

## 4. File Excel "Theo SKU" — dòng combo biến mất, cộng dồn vào sản phẩm con

Áp dụng khi xuất ở chế độ **"Theo SKU"** (`lich-su-xuat-sku.xlsx`,
`lich-su-nhap-hoan-sku.xlsx`) — file này gộp mỗi SKU thành 1 dòng duy nhất
cho cả kỳ lọc.

- SKU combo **không còn xuất hiện như một dòng riêng**. Số lượng của nó được
  cộng thẳng vào dòng của từng sản phẩm con (nhân theo số lượng khai trong
  combo).
- Nếu sản phẩm con đó **cũng** được bán/hoàn lẻ trong kỳ, số từ combo và số
  bán lẻ được **cộng chung vào một dòng**.
- Thêm cột **"Trong đó từ combo"** ở cuối mỗi dòng: cho biết trong tổng số
  lượng của dòng đó, bao nhiêu là do nổ ra từ combo (phần còn lại là bán/hoàn
  lẻ trực tiếp SKU đó).

Ví dụ: kỳ lọc có 3 đơn bán lẻ sản phẩm `A01` (tổng 3 cái) và 1 đơn mua combo
`CB01` (nổ ra thêm 1 cái `A01`). File "Theo SKU" sẽ ra **1 dòng `A01`** với
SL xuất = 4, cột "Trong đó từ combo" = 1.

## 5. Đọc các dòng "Tổng" ở cuối file thế nào

Các dòng Tổng cũ (ví dụ "Tổng theo đơn", "Tổng phiếu xuất kho", "Tổng phiếu
chuyển kho", "Tổng cộng") **giữ nguyên con số như trước** — không đổi cách
tính. Chỉ đổi tên nhãn thành **"... (SKU ghi sổ)"**, ví dụ "Tổng cộng (SKU ghi
sổ)", để nói rõ: đây là tổng tính theo đúng SKU đã ghi vào kho (một combo tính
là 1 lần, không nổ). **Đây vẫn là con số đúng để đối chiếu với tồn kho** —
tồn kho ghi sổ theo SKU combo nên phải so với dòng Tổng này, không phải dòng
mới bên dưới.

Cả **hai chế độ** ("Theo đơn" lẫn "Theo SKU", cả tab Xuất lẫn Nhập hoàn) đều có
thêm **1 dòng Tổng mới ở cuối cùng**: **"Tổng SL các dòng trong file"**. Đây là
tổng cộng đúng cột SL của tất cả các dòng chi tiết THẬT SỰ có trong file (sau
khi combo đã nổ thành sản phẩm con). Dòng này dùng để biết tổng số lượng sản
phẩm vật lý thật sự đã xuất/nhập ra khỏi kho trong kỳ — **không dùng để đối
chiếu tồn kho** (đối chiếu tồn kho phải dùng dòng "... (SKU ghi sổ)" phía trên).

Con số này lệch với dòng "... (SKU ghi sổ)" vì **hai lý do**, không chỉ một:

1. **Nở combo**: 1 combo bán ra nổ thành nhiều sản phẩm con, nên tổng SL sau
   khi nở luôn ≥ tổng SL trước khi nở nếu kỳ lọc có bán/hoàn combo.
2. **Cách tính SL theo từng dòng khi đơn đã đảo sổ một phần**: cột SL của mỗi
   dòng chi tiết dùng công thức "hiển thị" riêng cho dòng đó (ưu tiên số thực
   ghi sổ ròng, nếu bằng 0 thì lấy số đã từng ra khỏi kho trước khi đảo sổ),
   trong khi dòng "... (SKU ghi sổ)" cộng dồn theo TOÀN ĐƠN rồi mới áp công
   thức đó một lần. Hai cách tính này có thể ra số khác nhau **ngay cả khi kỳ
   lọc không hề có combo nào**. Ví dụ thực tế: đơn `O1` bán `SP-A` (xuất 2 cái
   rồi bị đảo sổ/huỷ hết — số ròng còn lại là 0) và `SP-B` (xuất 3 cái, không
   đảo sổ). Dòng "... (SKU ghi sổ)" của đơn này tính ra 3 (chỉ còn phần thật sự
   ở ngoài kho), nhưng cộng riêng từng dòng chi tiết lại ra 2 + 3 = 5 (dòng
   `SP-A` vẫn hiện SL = 2 vì nó dùng số đã từng xuất trước khi đảo sổ). Đây là
   hành vi **đúng chủ đích** của cột SL từng dòng (không phải lỗi), nhưng anh
   chị cần biết để không thắc mắc vì sao 2 dòng Tổng không khớp nhau khi kỳ lọc
   không có combo.

Vì vậy: **đừng cộng tay** cột SL để tự đối chiếu — dòng Tổng mới đã cộng sẵn
đúng những gì đang có trong file, kể cả ở chế độ "Theo SKU".

## 6. Những điều cần lưu ý khi dùng

1. **Khai báo thành phần combo không có lịch sử/phiên bản.** Hệ thống chỉ nhớ
   thành phần **hiện hành** của mỗi combo. Nếu hôm nay sửa lại thành phần của
   một combo (đổi sản phẩm con, đổi số lượng), thì khi xuất lại file Excel
   của một kỳ **trong quá khứ**, các dòng đã nổ combo của kỳ đó cũng sẽ đổi
   theo thành phần MỚI, dù thời điểm đó combo đã bán theo thành phần CŨ. Nếu
   cần đối chiếu chính xác một kỳ đã qua, nên xuất và lưu lại file ngay, đừng
   đợi xuất lại sau khi đã sửa thành phần combo.
2. **Màn hình web và file Excel đang cho hai con số khác nhau.** Trang Lịch
   sử nhập xuất trên web hiện vẫn hiển thị gộp theo SKU combo (chưa nổ), còn
   file Excel tải về đã nổ combo ra sản phẩm con. Đây là chủ đích của bản
   này, không phải lỗi — nhưng anh chị cần biết để không nhầm khi so số trên
   màn hình với số trong file.
3. **File Excel sẽ nặng hơn trước**, khoảng 2-3 lần tuỳ tỷ trọng đơn hàng
   combo trong kỳ lọc, vì mỗi combo giờ chiếm nhiều dòng thay vì 1 dòng.
4. **Tồn kho không đổi** — việc trừ/cộng tồn kho khi xuất/nhập hàng vẫn tính
   theo đúng SKU combo như trước, không bị ảnh hưởng bởi tính năng này.
5. **Lọc theo mã hàng có tương tác "lạ" với combo — đọc kỹ trước khi kết luận
   thiếu dữ liệu.**
   - Nếu anh chị lọc theo **mã một sản phẩm con** (ví dụ lọc `A01`): bộ lọc áp
     dụng ở tầng **đơn hàng**, mà đơn mua combo `CB01` không tự thân chứa mã
     `A01` (nó chứa mã `CB01`) — nên đơn đó bị loại khỏi kết quả TRƯỚC khi kịp
     nổ combo. Hậu quả: phần `A01` sinh ra từ việc bán combo sẽ **không xuất
     hiện** trong file, dù thực tế combo đó có bán `A01`. Nếu `A01` chỉ được
     bán qua combo (không bao giờ bán lẻ), lọc theo `A01` sẽ ra file **rỗng**.
   - Nếu anh chị lọc theo **mã một SKU combo** (ví dụ lọc `CB01`): đơn mua combo
     đó vẫn được giữ lại đúng (lọc theo đơn hoạt động bình thường), nhưng vì
     mỗi đơn đã tách theo mã hàng và combo luôn nổ ra sản phẩm con, nên trong
     file **sẽ không có dòng nào mà cột SKU mang giá trị `CB01`** — mã combo
     chỉ xuất hiện ở cột **"Thuộc combo"**, còn cột SKU luôn là sản phẩm con.
     Đừng tưởng lọc sai hoặc file thiếu dòng.
   - Muốn xem đầy đủ mọi dòng liên quan đến một sản phẩm (cả bán lẻ lẫn từ
     combo), cách an toàn nhất là **không lọc theo mã hàng**, xuất cả kỳ rồi
     lọc lại trong Excel theo cột SKU/"Thuộc combo".
6. **Vị trí cột mới thay đổi khác nhau tuỳ chế độ xuất — không lệch cột như
   nhau ở cả hai chế độ.**
   - Chế độ **"Theo đơn"** (mặc định): hai cột mới ("Tên sản phẩm", "Thuộc
     combo") chèn vào GIỮA file, ngay sau cột SKU — không phải chèn ở cuối.
     Nếu anh chị đã có sẵn công thức Excel hoặc bảng pivot tham chiếu theo
     **vị trí cột** (ví dụ VLOOKUP/công thức trỏ cột G, H... của file cũ), các
     công thức đó sẽ **trỏ lệch cột** sau bản này vì mọi cột từ sau SKU đều bị
     đẩy lùi thêm 2 vị trí. Cần mở lại và chỉnh tay file mẫu đang dùng (đổi
     tham chiếu cột) trước khi dùng tiếp, không tự động cập nhật được.
   - Chế độ **"Theo SKU"**: KHÔNG có chuyện đẩy lùi cột. Chế độ này chỉ thêm
     đúng một cột **"Trong đó từ combo"** ở CUỐI mỗi dòng (xem mục 4), nên mọi
     cột hiện có giữ nguyên vị trí — công thức/pivot tham chiếu cột cũ vẫn
     đúng, không cần chỉnh.

## 7. Kiểm tra trước khi phát hành

Đã chạy toàn bộ test tự động của 2 module liên quan (`apps/inventory`,
`apps/orders`) và toàn bộ đều pass, không có test đỏ.
