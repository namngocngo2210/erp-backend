# Nội dung sản phẩm cho trang /sp/

## Luồng
```
Thông tin sản phẩm Desi Global.xlsx   (file gốc, KHÔNG commit)
   → python scripts/convert_product_excel.py    (một lần)
   → backend/data/product-content.xlsx          (người duyệt/sửa, CÓ commit)
   → python manage.py import_product_profiles   (chạy được nhiều lần)
   → DB
```

## Việc người dùng phải làm trên file phẳng trước khi import
`cong_bo` được script ghi mặc định là `0` cho MỌI SKU: xuất bản trang public là
quyết định thủ công của người duyệt, không phải việc chạy script quyết định.
Không SKU nào lên trang `/sp/` cho tới khi người duyệt tự đặt `cong_bo = 1`
cho từng SKU đã kiểm tra xong các mục dưới đây.

1. Biên tập lại sheet `faq` — nội dung gốc viết theo giọng script bán hàng, chưa
   dùng được cho trang public. Sửa xong thì đặt `hien_thi = 1` và thêm `faq` vào
   cột `khoi_hien_thi` của sheet `san_pham`.
2. Điền tay SKU mà script cảnh báo: MODEL gốc `MSC02-CAOCAP/ DC20 (Hai mã giống
   nhau, chỉ khác ở chỗ DC20 to hơn)` bị ghi nguyên văn vào cột `sku` của mọi
   sheet liên quan — cần tách thành 2 SKU thật (`MSC02-CAOCAP` và `DC20`) và
   nhân bản dữ liệu tương ứng trước khi import.
3. Điền `mo_ta_ngan` cho từng SKU (file gốc không có trường này).
4. Rà giá lọt trong text mô tả — cột giá trong file gốc không bao giờ được
   script đọc, nhưng giá đôi khi được viết thẳng vào nội dung marketing (cột
   `diem_noi_bat`). Ví dụ đã gặp: SKU `DS13`, dòng "Động cơ chổi than" từng có
   câu "...chi phí tối ưu trong phân khúc giá 599K" (đã sửa trong file commit).
   Đây là nội dung gốc, không phải lỗi script — vẫn cần rà lại mỗi lần chuyển
   đổi từ file gốc mới, vì `diem_noi_bat` không có cổng kiểm duyệt như `faq`.
5. Chỉ sau khi xong các bước trên, đặt `cong_bo = 1` cho SKU đó ở sheet
   `san_pham` — đây là bước bật công khai, làm riêng cho từng SKU đã sẵn sàng.

## Không có ảnh
File gốc không chứa ảnh nào. Ảnh upload qua CRM: `/catalog/products/:id/content`.

## Ghi chú về layout file gốc (cho lần chuyển đổi sau, nếu có)
Sheet `Thông tin chi tiết sản phẩm` là 22 khối sản phẩm liên tiếp, không phải
bảng phẳng. Vài điểm không hiển nhiên khi đọc script:
- Khối "điểm nổi bật" chỉ có tiêu đề `ẢNH SẢN PHẨM` ở cột A trong 2/22 khối;
  20 khối còn lại bắt đầu thẳng bằng hàng `MÔ TẢ SẢN PHẨM` ở cột D — script
  dùng cột D làm mốc vì nó có mặt ở mọi khối.
- Link video được nhận diện qua nhãn cột D bắt đầu bằng "Link" hoặc "Video"
  (không phân biệt hoa/thường); các bước hướng dẫn có kèm URL nhưng nhãn dạng
  "Bước n: ..." vẫn được giữ trong `huong_dan`, không tách sang `video`.
- Ngay trước hàng `TÊN SẢN PHẨM` của khối kế tiếp luôn có một hàng thừa dạng
  mục lục cũ (ví dụ `23. Đai Massage Thắt Lưng DELAX DL11`, chỉ có cột A).
  Script tự loại bỏ hàng này để nó không lọt vào cuối sheet `faq`.
- Nguồn ghi tiêu đề một lần rồi liệt kê tiếp ở các hàng sau với cột tiêu đề bỏ
  trống (ví dụ một "Bước n" có 5-6 hàng con mỗi hàng chỉ có cột chi tiết, hoặc
  một câu hỏi FAQ có câu trả lời nằm ở hàng kế tiếp thay vì cùng hàng). Script
  nối các hàng đó vào mục liền trước (title/detail và symptom/cause/fix) thay
  vì tạo mục mới hoặc bỏ sót — xem `_parse_pairs` và `_parse_troubleshooting`.
- Bảng `XỬ LÝ SỰ CỐ` ở 8/22 khối có một hàng ghi chú chính sách CSKH nội bộ
  ("Nếu làm theo hướng dẫn xử lý sự cố như trên mà không được thì tiến hành
  đổi lỗi/ thu hồi bảo hành cho khách theo quy định") làm hàng cuối bảng.
  Script tự loại hàng này khỏi `su_co` (xuất bản mặc định, không như `faq`)
  và in cảnh báo — không cần người dùng xử lý thêm.
