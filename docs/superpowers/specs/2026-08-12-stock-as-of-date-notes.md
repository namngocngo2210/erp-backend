# Tồn kho theo mốc ngày — ghi chú sau triển khai

Ngày: 2026-08-13 · Nhánh: `feat/stock-as-of-date`

## Đã làm

- `StockAsOfService` dựng tồn tại mốc bằng cách trừ ngược từ `stock_items`, đối
  chiếu chéo với cách cộng xuôi từ sổ trong test.
- `GET /api/inventory/stock?as_of=YYYY-MM-DD` và
  `GET /api/inventory/stock/movements?as_of=` — mốc = hôm nay quy về tồn hiện
  tại; mốc tương lai / sai định dạng trả 400.
- Cặp (kho, mã) chỉ bị ẩn khi CẢ BA trạng thái đều = 0 tại mốc (không phải khi
  tổng = 0 — `StockItem.quantity` được phép âm nên tổng 0 có thể che một dòng
  vẫn còn tồn thật, ví dụ SELLABLE=+5/DEFECT=−5).
- RR3/RR7 tính theo cửa sổ 3/7 ngày TRƯỚC mốc, không phải trước hôm nay.
- Export kind `stock` nhận `as_of`; tên file lấy theo mốc, không theo ngày
  chạy job.
- Trang Tồn kho: ô "Xem tồn đến ngày" (`max` = hôm nay), dải cảnh báo hổ
  phách, ẩn nút "Xử lý" và mọi hành động ghi sổ ở chế độ quá khứ, cắt panel
  Lịch sử biến động tại mốc, nút "Hiện tại" để xoá mốc.
- Guard trần `_AS_OF_MAX_ROWS = 20000` dòng cho chế độ mốc (Python phải
  materialize cả tập đã lọc để trừ ngược).
- Guard chặn request as-of thiếu cả `warehouse_id` lẫn `search` (400) — tránh
  vật chất hoá toàn bộ `StockItem` kèm `select_related` cho một request có thể
  chỉ xin `limit=1`; giao diện luôn gửi `warehouse_id` nên guard vô hình với
  người dùng thật.
- `todayIso` ở `StockPage` là `useState` tự làm mới bằng `setInterval` (60s)
  để không đóng băng "hôm nay" qua đêm khi màn hình mở suốt. KHÔNG tính thẳng
  trong thân render dù có bỏ `useMemo`: đã biên dịch thử và xác nhận React
  Compiler của repo vẫn memo hoá lời gọi `toIsoDate(new Date())`, chỉ khoá
  scope theo `asOf` chứ không phải khoá vĩnh viễn — nên tab mở qua đêm mà
  không đổi mốc vẫn kẹt "hôm nay" cũ nếu không dùng state + interval.

## Còn nợ

1. **Đo `EXPLAIN` trên prod** với mốc 30/90/180 ngày trước, rồi mới quyết định
   có thêm index `(occurred_at, warehouse, product)` cho `stock_movements` hay
   không. Thêm index vào bảng partition là thao tác tốn — không quyết bằng
   phỏng đoán. **Đo cả truy vấn RR ở chế độ mốc**: cận trên của cửa sổ 7 ngày
   không được kèm `created_at` (chỉ cận dưới mới cắt được partition an toàn,
   xem §3.3/§5.2 điểm 3 trong design), nên với mốc 6 tháng trước, truy vấn vẫn
   chạm mọi partition từ `mốc−7d` đến hiện tại chỉ để tính một cửa sổ 7 ngày.
   Kiểm tra thêm partition `stock_movements_default` (DEFAULT, không prune
   được theo range) có rỗng trên prod không — nếu không rỗng, nó luôn bị quét
   thêm bất kể mốc.
2. **`backend/schema.yaml` chưa khai `as_of`.** Cố ý: hai endpoint này vốn
   không khai query param nào (view không dùng
   `@extend_schema(parameters=...)`), thêm mỗi param mới vào sẽ tạo file
   schema nửa vời, mâu thuẫn với chính nó. Trả nợ bằng một MR chore khai đủ
   tham số cho cả hai endpoint.
3. **Phân trang đếm theo dòng trạng thái** (`StockItem`, mỗi trạng thái một
   dòng) trong khi bảng web pivot ba trạng thái thành một dòng sản phẩm, nên
   trang hiển thị luôn ngắn hơn `limit`. Hành vi CÓ SẴN từ trước tính năng
   mốc, không phát sinh từ nhánh này.
4. **Trường `as_of` trong response `GET /api/inventory/stock` là echo thô** —
   server chỉ lặp lại nguyên chuỗi client vừa gửi (đã qua validate), không nói
   cho client điều gì nó chưa biết, nên vô dụng với phía dùng thật. Hợp đồng
   hữu ích hơn sẽ là một cờ `viewing_past` do SERVER quyết dựa trên
   `timezone.localdate()` của server — khi đó khoản 5 dưới về đồng bộ giờ qua
   nửa đêm ở FE cũng tự tan vì FE không cần tự tính "hôm nay" nữa. Đã bỏ khai
   báo `as_of` phía FE (`StockListResponse`) vì không có chỗ nào đọc; giữ
   nguyên phía backend (đã có test riêng, chưa muốn đổi hợp đồng ở nhánh này).
5. **Cột "Tồn" (`balance_after`) trong panel Lịch sử biến động không cùng hệ
   quy chiếu với mốc.** Panel liệt kê đúng các bút toán tính đến mốc (lọc theo
   `occurred_at < end`), nhưng `balance_after` là số dư tại thời điểm GHI SỔ
   (`created_at`) — với bút toán ghi sổ trễ (quét khớp trễ, `created_at` sau
   `occurred_at`), con số này đã bao gồm cả biến động xảy ra SAU mốc. Cân nhắc
   ẩn cột này khi đang xem theo mốc thay vì hiện một số dư không khớp ngữ
   cảnh.
6. **`StockExport.estimate()` không biết `as_of`** nên mẫu số thanh tiến trình
   vẫn đếm theo tồn hiện tại — thuần hiển thị, tiến trình có thể báo về đích
   sớm hơn thực tế khi tồn tại mốc ít dòng hơn tồn hiện tại.
7. **Kiểm chứng số liệu cuối cùng nên làm trên prod.** Test đối chiếu chéo
   (trừ ngược so cộng xuôi) chỉ chứng minh hai cách TÍNH khớp nhau, không
   chứng minh `stock_items` khớp sổ cái thật — nếu projection đã lệch từ
   trước, cả hai cách tính lệch giống nhau và test vẫn xanh.
