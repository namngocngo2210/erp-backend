# LUỒNG XỬ LÝ KHO & ĐƠN HÀNG: PANCAKE ↔ CRM

_Tài liệu vận hành, phiên bản 30/09/2026_

## 1. Mục đích và phạm vi

Tài liệu mô tả cách công ty vận hành kho và đơn hàng khi dùng song song Pancake POS và CRM: ai làm gì, làm ở đâu, và hai hệ thống tự cập nhật cho nhau ra sao. Áp dụng cho nhân viên kho, sale, CSKH/bảo hành, quản lý kho và kế toán.

> **Nguyên tắc số 1: NHÂN VIÊN KHO CHỈ THAO TÁC TRÊN PANCAKE. Mỗi mã vận đơn chỉ được quét ở MỘT nơi. CRM tự ghi lại mọi thứ và là nơi quản lý quyết định hàng hoàn đi đâu.**

## 2. Hai hệ thống, hai vai trò

|  | PANCAKE POS | CRM |
|---|---|---|
| Ai dùng | Nhân viên kho (mọi thao tác quét), sale page/sàn | Quản lý kho, kế toán, CSKH/bảo hành, sale landing/hotline |
| Kho | 3 kho: Đại Mỗ 1, Đại Mỗ 2, Cửa Hàng | Cũng 3 kho, mỗi kho chia 6 nhánh (xem mục 3) |
| Xem tồn | Mỗi kho 2 con số: BÁN ĐƯỢC và HÀNG LỖI | Chi tiết từng nhánh |
| Quyết định | Xuất, nhập, chuyển kho, kiểm kê, nhận hoàn | Hàng hoàn đi nhánh nào; chuyển tính chất hàng; bảo hành |
| Đơn hàng | Nơi TẤT CẢ đơn được xử lý kho | Nơi sale tạo đơn landing/mess/zalo/hotline; nhận đơn Foxia |

## 3. Cấu trúc kho

### 3.1 Trên CRM: 3 kho × 6 nhánh

| Nhánh | Ý nghĩa | Bán được? |
|---|---|---|
| Hàng hoá | Hàng mới, bán bình thường | Có |
| Hoàn chưa xử lý | Hàng hoàn vừa về, chưa kiểm tra. MỌI hàng hoàn đều vào đây trước | Không |
| Đã qua sử dụng | Còn dùng được nhưng không bán như hàng mới | Không |
| Lỗi | Hỏng, chờ gửi nhà cung cấp hoặc huỷ | Không |
| Bảo hành | Máy của khách gửi về sửa (không phải tài sản công ty) | Không |
| Linh kiện | Vỏ, củ nguồn, HDSD, đệm… (là loại sản phẩm riêng) | Không bán lẻ |

### 3.2 Trên Pancake: 3 kho, mỗi kho 2 con số

| Pancake | = Tổng các nhánh CRM |
|---|---|
| Bán được (sản phẩm thường) | Hàng hoá |
| Bán được (danh mục "Linh kiện", ẩn, không nối sàn) | Linh kiện |
| Hàng lỗi | Hoàn chưa xử lý + Đã qua sử dụng + Lỗi (phân biệt bằng THẺ trên phiếu hàng lỗi) |
| Không tính tồn | Bảo hành (máy của khách, dùng mã dịch vụ BH-MAYKHACH) |

### 3.3 Ghép kho

| Kho CRM | Kho Pancake | Nối sàn/page |
|---|---|---|
| Đại Mỗ 1 | Đại Mỗ 1 | Có |
| Đại Mỗ 2 | Đại Mỗ 2 | Có |
| Cửa hàng | Cửa Hàng (282 Nguyễn Huy Tưởng) | Chờ xác nhận |

> **Chỉ các kho bán được mới nối với Shopee/TikTok/page. Không sửa tồn tay trên Pancake. Mọi thay đổi tồn đi qua phiếu (nhập/xuất/chuyển/kiểm kê) để CRM ghi lại được.**

## 4. Đơn hàng đi vào Pancake

| Nguồn đơn | Vào Pancake bằng cách | Ghi chú |
|---|---|---|
| Shopee (6 shop), TikTok (2 shop) | Có sẵn trên Pancake (sàn đã kết nối) | CRM nhận lại để báo cáo, khớp theo mã đơn sàn |
| Page Facebook (chốt trên Pancake) | Có sẵn trên Pancake | CRM nhận lại để báo cáo |
| Landing, Messenger, Zalo, Hotline | Sale tạo trên CRM → CRM TỰ ĐẨY sang Pancake khi đơn được chốt | Sale giữ nguyên thao tác trên CRM; KiotViet vẫn xuất hoá đơn song song (có mã hoá đơn KiotViet vẫn đẩy), chỉ đẩy đơn chốt sau mốc `PANCAKE_POS_PUSH_ORDERS_SINCE` |
| Foxia (telesale chốt) | CRM đẩy cơ hội sang Foxia → kéo đơn Foxia về theo mã cơ hội → CRM TỰ ĐẨY sang Pancake | Luồng kéo đơn Foxia đã có sẵn |
| Bảo hành, linh kiện | CSKH tạo phiếu trên CRM (có mã vận đơn) → CRM tự tạo "đơn bảo hành" 0đ trên Pancake | Gắn thẻ BẢO HÀNH, loại khỏi báo cáo doanh số |

**Quy tắc chống giao trùng**

- Khách landing đã được chuyển thành cơ hội Foxia: chỉ đẩy ĐƠN FOXIA, không đẩy đơn landing gốc.
- Đơn landing đã đẩy sang Pancake rồi mới xuất hiện đơn Foxia tương ứng: không đẩy đơn Foxia, gửi cảnh báo Lark để người xử lý.
- Mỗi đơn CRM chỉ đẩy một lần; đẩy lại (do lỗi mạng) không tạo đơn thứ hai.

**Chọn kho xuất và tách kiện**

- Mỗi đơn trên Pancake chỉ xuất từ MỘT kho.
- Có kho đủ tất cả mã hàng: CRM tự chọn kho theo thứ tự ưu tiên đã cấu hình.
- Không kho nào đủ: đơn vào danh sách "Cần chọn cách xuất". Người điều phối chọn (a) chuyển kho gom hàng về 1 kho rồi gửi 1 kiện, hoặc (b) tách thành nhiều đơn Pancake (nhiều kiện). Một đơn CRM có thể ứng với nhiều đơn Pancake.
- Đơn sàn: Pancake tự gán kho; thiếu hàng thì kho làm phiếu chuyển kho trên Pancake.

**Sửa / huỷ đơn**

- Sửa hoặc huỷ trên CRM TRƯỚC khi Pancake xuất kho: CRM tự cập nhật/huỷ đơn trên Pancake.
- Sau khi Pancake đã xuất: không sửa được nữa. Huỷ lúc này = đơn hoàn, xử lý theo F5/F6 khi hàng về.

## 5. Chi tiết từng luồng

### F1. Nhập hàng mua

| Mục | Nội dung |
|---|---|
| Người làm | Nhân viên kho |
| Làm ở | Pancake: tạo phiếu nhập |
| Khi nào | Hàng từ nhà cung cấp về kho |

**Các bước**

1. Tạo phiếu nhập trên Pancake, chọn kho, quét/nhập SKU và số lượng.
2. Hoàn tất phiếu.

**Hệ thống tự làm**

|  | CRM (kho × nhánh) | Pancake (kho: bán được / hàng lỗi) |
|---|---|---|
| Tồn | + Hàng hoá (kho tương ứng) | + Bán được |
| Chứng từ | Tự sinh phiếu nhập, nguồn = Pancake | Phiếu nhập gốc |

**Trường hợp đặc biệt**

- Nhập linh kiện: chọn SKU linh kiện, CRM ghi vào nhánh Linh kiện.

### F2. Xuất đơn bán (mọi kênh)

| Mục | Nội dung |
|---|---|
| Người làm | Nhân viên kho |
| Làm ở | Pancake: quét mã vận đơn / đóng gói |
| Khi nào | Đơn đã có trên Pancake |

**Các bước**

1. Quét mã vận đơn hoặc mở đơn trên Pancake.
2. Đóng gói, chuyển trạng thái Đã gửi hàng.

**Hệ thống tự làm**

|  | CRM (kho × nhánh) | Pancake (kho: bán được / hàng lỗi) |
|---|---|---|
| Tồn | − Hàng hoá theo từng SKU × kho | − Bán được |
| Đơn | Trạng thái đơn CRM (`status`) **giữ theo KiotViet**; "Trạng thái vận chuyển thực" = Đã xuất kho (khi bật cờ xuất kho sau ngày chuyển); dòng phụ "Đã gửi hàng · <trạng thái ĐVVC>"; lưu mã vận đơn/ĐVVC nếu CRM còn trống | Đã gửi hàng |

**Trường hợp đặc biệt**

- CRM trừ tồn theo SKU ngay cả khi chưa khớp được đơn; khớp đơn chỉ để báo cáo (mục 6).

### F3. Chuyển kho

| Mục | Nội dung |
|---|---|
| Người làm | Nhân viên kho |
| Làm ở | Pancake: phiếu chuyển kho |
| Khi nào | Điều hàng giữa ĐM1, ĐM2, Cửa hàng; gom hàng cho đơn thiếu |

**Các bước**

1. Tạo phiếu chuyển: kho đi → kho đến, SKU, số lượng.
2. Xác nhận đi và nhận tại kho đến.

**Hệ thống tự làm**

|  | CRM (kho × nhánh) | Pancake (kho: bán được / hàng lỗi) |
|---|---|---|
| Tồn | Hàng hoá: − kho đi, + kho đến | Bán được: − kho đi, + kho đến |

### F4. Kiểm kê

| Mục | Nội dung |
|---|---|
| Người làm | Nhân viên kho / quản lý |
| Làm ở | Pancake: phiếu kiểm kho |
| Khi nào | Định kỳ hoặc khi nghi lệch |

**Các bước**

1. Đếm thực tế, nhập số đếm vào phiếu kiểm kho.
2. Hoàn tất phiếu.

**Hệ thống tự làm**

|  | CRM (kho × nhánh) | Pancake (kho: bán được / hàng lỗi) |
|---|---|---|
| Tồn | Hàng hoá = số đếm (tự sinh phiếu điều chỉnh) | Bán được = số đếm |

**Trường hợp đặc biệt**

- Kiểm kê hàng lỗi/hoàn/đã qua sử dụng: làm trên CRM (chỉ CRM có chi tiết nhánh).

### F5. Nhận hàng hoàn toàn bộ

| Mục | Nội dung |
|---|---|
| Người làm | Nhân viên kho |
| Làm ở | Pancake: chuyển đơn sang Đã hoàn |
| Khi nào | ĐVVC trả hàng về kho |

**Các bước**

1. Quét mã vận đơn hoàn, mở đơn.
2. Chuyển trạng thái Đã hoàn (Pancake tự cộng hàng về kho gốc).
3. Để hàng vào khu hàng hoàn chờ kiểm.

**Hệ thống tự làm**

|  | CRM (kho × nhánh) | Pancake (kho: bán được / hàng lỗi) |
|---|---|---|
| Tồn | + Hoàn chưa xử lý (gắn với đơn gốc) | Pancake cộng vào Bán được, sau vài giây CRM tự tạo phiếu xuất hàng lỗi thẻ HOÀN CHƯA XỬ LÝ → chuyển sang Hàng lỗi |
| Đơn | `status` theo KiotViet; "Trạng thái vận chuyển thực" = Chưa nhận; dòng phụ "Đã hoàn" | Đã hoàn |

**Trường hợp đặc biệt**

- Có vài giây hàng hoàn được tính là bán được trước khi CRM chuyển sang hàng lỗi; đối soát sẽ bắt các ca sót.

### F6. Nhận hàng hoàn một phần

| Mục | Nội dung |
|---|---|
| Người làm | Nhân viên kho |
| Làm ở | Pancake: chuyển đơn sang Hoàn một phần |
| Khi nào | Khách nhận một phần, trả lại phần còn lại |

**Các bước**

1. Mở đơn gốc, chọn Hoàn một phần.
2. Nhập số lượng trả về cho TỪNG mã hàng.
3. Xác nhận.

**Hệ thống tự làm**

|  | CRM (kho × nhánh) | Pancake (kho: bán được / hàng lỗi) |
|---|---|---|
| Tồn | + Hoàn chưa xử lý đúng số lượng từng SKU | Hàng lỗi đúng số lượng (qua phiếu hàng lỗi) |
| Đơn | `status` theo KiotViet; "Trạng thái vận chuyển thực" giữ nguyên (khách đã nhận một phần); dòng phụ "Hoàn một phần"; doanh thu tính theo phần khách giữ | Hoàn một phần, tiền thu tính lại |

### F7. Xử lý hàng hoàn từng đơn

| Mục | Nội dung |
|---|---|
| Người làm | Quản lý / nhân viên kiểm hàng |
| Làm ở | CRM: tab Hoàn chưa xử lý |
| Khi nào | Sau khi kiểm tra hàng hoàn thực tế |

**Các bước**

1. Mở tab Hoàn chưa xử lý (đơn chờ > 48 giờ được tô đỏ, Lark nhắc 9h sáng).
2. Bấm Xử lý trên từng đơn, chia số lượng từng dòng vào: Hàng hoá / Đã qua sử dụng / Bảo hành / Lỗi / Gửi lại khách / Huỷ.
3. Xác nhận (phải chia đủ tổng số lượng).

**Hệ thống tự làm**

|  | CRM (kho × nhánh) | Pancake (kho: bán được / hàng lỗi) |
|---|---|---|
| Còn tốt | Hoàn chưa xử lý → Hàng hoá | Giảm/huỷ phiếu hàng lỗi → hàng về Bán được |
| Lỗi / Đã qua SD | → Lỗi / Đã qua sử dụng | Vẫn Hàng lỗi, đổi thẻ phiếu thành LỖI / ĐÃ QUA SỬ DỤNG |
| Bảo hành | → Bảo hành | Xuất bỏ khỏi Hàng lỗi |
| Gửi lại / Huỷ | − Hoàn chưa xử lý | Phiếu hàng lỗi chuyển Xuất bỏ |

**Trường hợp đặc biệt**

- Xử lý nhầm: bấm Hoàn tác xử lý (cần quyền duyệt). Không hoàn tác được nếu hàng đã bán mất.
- Đơn đã xử lý thì không huỷ/điều chỉnh phiếu hoàn được nữa, phải hoàn tác trước.

### F8. Chuyển tính chất hàng

| Mục | Nội dung |
|---|---|
| Người làm | Quản lý kho |
| Làm ở | CRM: Tồn kho → Chuyển tính chất |
| Khi nào | Ví dụ hàng Lỗi sửa xong thành Đã qua sử dụng; Hàng hoá phát hiện lỗi |

**Các bước**

1. Chọn kho, SKU, từ nhánh → sang nhánh, số lượng.
2. Xác nhận.

**Hệ thống tự làm**

|  | CRM (kho × nhánh) | Pancake (kho: bán được / hàng lỗi) |
|---|---|---|
| Tồn | Chuyển giữa các nhánh (không được vào/ra Hoàn chưa xử lý) | Nếu liên quan Hàng hoá: Bán được ↔ Hàng lỗi |

### F9. Đổi lỗi, sản phẩm nguyên

| Mục | Nội dung |
|---|---|
| Người làm | CSKH + nhân viên kho |
| Làm ở | Pancake: đơn đổi trả |
| Khi nào | Khách đổi máy lỗi lấy máy mới |

**Các bước**

1. Tạo đơn đổi trả trên Pancake từ đơn gốc: phần trả về (máy lỗi) + phần gửi đi (máy mới).
2. Pancake tạo vận đơn với ĐVVC (không tạo tay trên web ĐVVC).
3. Kho quét xuất máy mới; khi máy lỗi về thì nhận hoàn.

**Hệ thống tự làm**

|  | CRM (kho × nhánh) | Pancake (kho: bán được / hàng lỗi) |
|---|---|---|
| Máy mới | − Hàng hoá | − Bán được |
| Máy cũ về | + Hoàn chưa xử lý → xử lý theo F7 | Hàng lỗi |

### F10. Gửi linh kiện cho khách

| Mục | Nội dung |
|---|---|
| Người làm | CSKH (khai báo) + nhân viên kho (quét) |
| Làm ở | CRM: phiếu bảo hành; Pancake: quét |
| Khi nào | Khách cần thay linh kiện |

**Các bước**

1. CSKH tạo vận đơn trên web ĐVVC, nhập mã vận đơn + linh kiện vào phiếu bảo hành trên CRM.
2. CRM tự tạo đơn bảo hành 0đ trên Pancake chứa SKU linh kiện.
3. Kho quét xuất trên Pancake như đơn thường.

**Hệ thống tự làm**

|  | CRM (kho × nhánh) | Pancake (kho: bán được / hàng lỗi) |
|---|---|---|
| Tồn | − Linh kiện | − Bán được (SKU linh kiện, ẩn) |

**Trường hợp đặc biệt**

- CSKH quên nhập mã vận đơn: kho không quét được; CRM cảnh báo phiếu bảo hành quá hạn chưa có mã.

### F11. Bảo hành: nhận máy khách gửi về

| Mục | Nội dung |
|---|---|
| Người làm | CSKH (khai báo) + nhân viên kho |
| Làm ở | CRM: phiếu bảo hành; Pancake: nhận hoàn |
| Khi nào | Khách gửi máy về bảo hành |

**Các bước**

1. CSKH nhập mã vận đơn chiều về vào phiếu bảo hành trên CRM.
2. CRM tạo đơn bảo hành (mã dịch vụ BH-MAYKHACH) ở trạng thái đang hoàn trên Pancake.
3. Kho quét, bấm nhận hoàn.

**Hệ thống tự làm**

|  | CRM (kho × nhánh) | Pancake (kho: bán được / hàng lỗi) |
|---|---|---|
| Tồn | + Bảo hành (chi tiết model/lỗi trong phiếu bảo hành) | Không đổi (CRM tự làm sạch mã dịch vụ) |

### F12. Bảo hành: gửi trả máy đã sửa

| Mục | Nội dung |
|---|---|
| Người làm | CSKH (khai báo) + nhân viên kho |
| Làm ở | CRM: phiếu bảo hành; Pancake: quét |
| Khi nào | Máy sửa xong |

**Các bước**

1. CSKH tạo vận đơn, nhập mã vào phiếu bảo hành.
2. CRM tạo đơn bảo hành 0đ trên Pancake.
3. Kho quét xuất.

**Hệ thống tự làm**

|  | CRM (kho × nhánh) | Pancake (kho: bán được / hàng lỗi) |
|---|---|---|
| Tồn | − Bảo hành | Không đổi |

### F13. Bảo hành: đổi máy mới

| Mục | Nội dung |
|---|---|
| Người làm | CSKH + nhân viên kho |
| Làm ở | CRM: phiếu bảo hành; Pancake: quét |
| Khi nào | Máy không sửa được, đổi máy mới |

**Các bước**

1. CSKH chọn kết quả Đổi máy mới + mã vận đơn trên phiếu bảo hành.
2. CRM tạo đơn bảo hành 0đ chứa SKU máy mới.
3. Kho quét xuất.

**Hệ thống tự làm**

|  | CRM (kho × nhánh) | Pancake (kho: bán được / hàng lỗi) |
|---|---|---|
| Tồn | − Hàng hoá (máy mới); máy cũ ở Bảo hành → Lỗi | − Bán được |

### F14. Sản phẩm mới / sửa sản phẩm

| Mục | Nội dung |
|---|---|
| Người làm | Quản lý sản phẩm |
| Làm ở | CRM: Sản phẩm |
| Khi nào | Thêm SKU, đổi giá, đổi tên ngắn, ngừng kinh doanh |

**Các bước**

1. Tạo/sửa trên CRM (SKU là khoá, không đổi SKU tuỳ tiện).
2. Khoảng 2 phút sau Pancake tự cập nhật.

**Hệ thống tự làm**

|  | CRM (kho × nhánh) | Pancake (kho: bán được / hàng lỗi) |
|---|---|---|
| Sản phẩm | Nguồn gốc dữ liệu | Tạo/cập nhật sản phẩm, mẫu mã (tên ngắn, giá); ngừng kinh doanh = ẩn, không xoá |

**Trường hợp đặc biệt**

- Chỉ đẩy SKU có tồn hoặc bán trong 180 ngày (hoặc đặt "Luôn đồng bộ"). Bỏ mã {DEL}, mã lô cũ đuôi -N, combo.
- SKU trên sàn phải trùng SKU CRM để Pancake ghép sàn tự động.

## 6. Tự khớp và tự trừ tồn

- Trừ/cộng tồn: Pancake báo về từng thay đổi theo SKU × kho, CRM ghi ngay vào nhánh tương ứng. Không phụ thuộc việc khớp đơn.
- Khớp đơn (để trạng thái và báo cáo), theo thứ tự: (1) liên kết khi CRM đẩy đơn; (2) mã đơn sàn; (3) mã vận đơn; (4) không khớp thì CRM tự tạo đơn từ dữ liệu Pancake.
- Một đơn CRM có thể ứng nhiều đơn Pancake (tách kiện); CRM chỉ báo Đã gửi khi mọi kiện đã gửi.

### 6.1 Trạng thái đơn trên CRM (chốt 01/10/2026)

- **Trạng thái đơn (`status`) vẫn do KiotViet quyết định.** Pancake không bao giờ đổi trạng thái đơn.
- Pancake ghi vào CRM, xem ở cột/ô **"Trạng thái vận chuyển thực"**:
  - Mã chính: Mới…Chờ chuyển hàng → *Kho đang xử lý*; Đã gửi hàng → *Đã xuất kho*; Đã nhận/Đã thu
    tiền → *Đã nhận*; Đang hoàn/Đã hoàn → *Chưa nhận*. Huỷ, Hoàn một phần → không đổi. Bảng ghép sửa
    được (`pancake_pos_status_map`). Mã do người nhập (*Đơn dừng hành trình, đang khiếu nại*,
    *Bưu cục làm mất*, *Đã nhận/Chưa nhận* đặt tay) không bị ghi đè; chỉ tiến, không lùi.
  - Dòng phụ: trạng thái Pancake nguyên văn tiếng Việt · trạng thái ĐVVC mới nhất (vd "Đang giao
    hàng", "Giao không thành", "Đang chuyển hoàn"). Đơn tách kiện hiện kiện chậm nhất.
  - Chi tiết đơn có mục "Vận chuyển (Pancake)": từng kiện, mã đơn Pancake, ĐVVC, mã vận đơn, kho,
    mốc tạo/gửi/giao/hoàn.
- **Cờ `PANCAKE_POS_DISPATCH_ENABLED` tắt** (kho chạy trên Pancake, CRM không trừ tồn): Pancake
  không xuất kho trên CRM — đơn Pancake đã gửi mà CRM chưa xuất kho hiện ở báo cáo lệch. Đã nhận /
  Đã thu tiền → *Đã nhận*, Đang hoàn / Đã hoàn → *Chưa nhận* vẫn tự ghi (không trừ tồn).
- **Sau ngày chuyển** (bật cờ): Pancake Đã gửi hàng → CRM tự chạy đúng luồng xuất kho như khi quét
  (trừ tồn, chặn đơn huỷ). Bật cờ này thì phần trừ tồn từ sự kiện tồn Pancake phải bỏ qua xuất
  bán theo đơn (tránh trừ hai lần).
- Trang Pancake POS: thẻ **Đơn treo** (chưa gửi quá 24 giờ, đã gửi quá 5 ngày chưa giao — theo
  kho; Lark 9h sáng mỗi ngày) và **Lệch KiotViet ↔ Pancake** (vd CRM huỷ nhưng Pancake đã gửi) —
  chỉ báo, người vận hành tự xử lý.
- Lấy lại trạng thái các đơn cũ: `manage.py pancake_pos_backfill_delivery_status --days N`
  (`--dry-run` để xem trước) — chỉ đọc Pancake, không bao giờ trừ tồn.
- Ca bất thường (mã khớp nhiều đơn, không khớp quá vài giờ): vào màn Chờ khớp trên CRM + cảnh báo Lark.
- Thay đổi do chính CRM tạo ra trên Pancake (ví dụ phiếu hàng lỗi) khi báo ngược về được nhận diện, không ghi hai lần.

## 7. Đối soát và cảnh báo

| Việc | Tần suất | Làm gì khi lệch |
|---|---|---|
| So Hàng hoá CRM với Bán được Pancake theo kho × SKU | Mỗi đêm | Báo Lark danh sách lệch; tuỳ chọn tự điều chỉnh CRM theo Pancake (Pancake là nơi đếm thực tế) |
| Bù sự kiện bị mất (đơn, tồn) | Mỗi đêm | Kéo lại đơn và lịch sử tồn 48h gần nhất |
| Đơn hoàn chờ xử lý > 48h | 9h sáng hằng ngày | Lark nhắc theo kho |
| Đơn treo vận chuyển (chưa gửi > 24 giờ, đã gửi > 5 ngày chưa giao) | 9h sáng hằng ngày (cấu hình được) | Lark theo kho Pancake; xem thẻ Đơn treo |
| Lệch trạng thái KiotViet ↔ Pancake | Xem bất cứ lúc nào (thẻ trên trang Pancake POS) | Người vận hành kiểm tra, sửa tay nếu cần |
| Shop sàn không có đơn mới bất thường | Theo dõi | Kiểm tra kết nối sàn trên Pancake |

## 8. Quy tắc bắt buộc

1. Nhân viên kho chỉ thao tác trên Pancake. Không quét lại trên CRM.
2. Mỗi mã vận đơn chỉ quét ở một nơi, một lần.
3. Không sửa tồn tay trên Pancake; mọi thay đổi qua phiếu.
4. Chỉ kho bán được nối với sàn/page.
5. SKU là khoá chung của CRM, Pancake và sàn: không tạo SKU trùng, không sửa SKU tuỳ tiện.
6. Hàng hoàn luôn đi qua Hoàn chưa xử lý và được xử lý trên CRM trước khi bán lại.
7. CSKH phải nhập mã vận đơn vào phiếu bảo hành trước khi kho quét.

## 9. Tình trạng triển khai

| Hạng mục | Trạng thái |
|---|---|
| Kiểm kê tồn CRM theo file (28/09) | Xong |
| Kết nối CRM ↔ Pancake, đẩy 284 sản phẩm / 518 mẫu mã | Xong (30/09) |
| Đẩy tồn Hàng hoá lên 3 kho Pancake (407 dòng) | Xong (30/09), cần đẩy lại vào ngày chuyển |
| Tên ngắn sản phẩm cho Pancake | Đang làm |
| Ghép kho trên màn hình, đẩy tồn bằng nút, CRM đẩy đơn sang Pancake | Đang làm (MR !1509) |
| Kho 2 level và xử lý hoàn từng đơn trên CRM | Xong code, chờ merge (MR !1504) |
| Pancake báo về CRM: trạng thái vận chuyển thực, đơn treo, lệch KiotViet ↔ Pancake | Xong code (MR D) |
| Pancake báo về CRM: sổ 2 cấp, hàng hoàn qua hàng lỗi, đối soát | Kế tiếp |
| Bảo hành, linh kiện quét trên Pancake | Sau cùng |

### Checklist ngày chuyển

1. Chốt giờ chuyển (cuối ngày), thông báo kho và sale.
2. Kho ngừng quét trên CRM.
3. Đẩy lại tồn CRM → Pancake cho khớp.
3b. Cấu hình `PANCAKE_POS_ORDER_SOURCE_MAP` (nguồn đơn Pancake), rồi bật `PANCAKE_POS_PUSH_ORDERS_ENABLED`
    lúc chuyển kho; đặt `PANCAKE_POS_PUSH_ORDERS_SINCE` = giờ chuyển (trống = lúc bật cờ lần đầu) —
    đơn chốt trước mốc do kho cũ xử lý. KiotViet vẫn xuất hoá đơn song song.
4. Bật nhận dữ liệu từ Pancake về CRM; chạy đối soát ngay.
4b. Bật `PANCAKE_POS_DISPATCH_ENABLED` (Pancake Đã gửi → CRM xuất kho) CÙNG lúc kho ngừng quét trên CRM,
    và chỉ khi phần sự kiện tồn đã bỏ qua xuất bán theo đơn. Chạy `pancake_pos_backfill_delivery_status
    --days 7` để điền trạng thái các đơn đang giao.
5. Ghép sản phẩm sàn theo SKU, bật đồng bộ tồn lên sàn.
6. Theo dõi sát 3 ngày đầu: đối soát, danh sách chờ khớp, hàng hoàn.

## 10. Việc còn chờ quyết định / kiểm chứng

- Đơn Shopee/TikTok còn về CRM qua KiotViet không, hay chỉ lấy từ Pancake?
- Thứ tự ưu tiên kho khi nhiều kho đủ hàng; thiếu hàng thì gom 1 kiện hay tách kiện?
- Kho Cửa hàng có bán online (nối sàn/page) không?
- ĐVVC dùng cho bảo hành; hàng đã qua sử dụng có bán thanh lý không?
- Có đẩy thêm ~215 SKU đang niêm yết trên sàn nhưng hết hàng/không bán gần đây không?
- Kiểm chứng trên Pancake: huỷ/giảm phiếu hàng lỗi có trả về bán được; hộp thoại Hoàn một phần; đơn chứa mã dịch vụ BH-MAYKHACH; thẻ trên phiếu hàng lỗi.
- Kéo đơn Foxia về CRM chưa chạy trên prod (`foxia_order_links` = 0 dòng, 30/09): luật chống trùng
  landing ↔ Foxia mới kiểm bằng test, chưa có ca thật; bật kéo đơn Foxia trước khi bật đẩy đơn Foxia.
- Shop Gia Dụng Domi: không có đơn về Pancake từ 27/09, cần kiểm tra kết nối.
