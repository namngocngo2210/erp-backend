# Checklist cutover — Bảng giá theo nền tảng (platform price books)

Tài liệu vận hành cho lần deploy đưa feature "bảng giá theo nền tảng" (`facebook` /
`web`) lên production và gán kênh KOC sang bảng giá `web`. Đọc kèm:

- Thiết kế: `docs/superpowers/specs/2026-07-31-platform-price-books-design.md`
- Kế hoạch sprint: `docs/superpowers/plans/2026-07-31-platform-price-books.md`
- Ledger thực thi: `.superpowers/sdd/2026-07-31-platform-price-books/progress.md`

Feature này KHÔNG hardcode KOC ở bất kỳ đâu trong logic tính giá — bảng giá được
gắn vào `Channel.price_book` như dữ liệu thuần. Command
`assign_koc_price_book` (Task 12) là nơi DUY NHẤT trong toàn bộ codebase biết
"KOC" nghĩa là gì (qua `Channel.is_koc`), và nó chỉ chạy MỘT LẦN cho mục đích
gán dữ liệu ban đầu — không phải một phần của luồng nghiệp vụ thường trực.

## 0. Điều kiện tiên quyết

- [ ] Task 1–12 đã merge vào `master`, pipeline CI xanh (test + `ruff check .`).
- [ ] Đã thông báo trước cho đội sale/CSKH: đơn KOC tạo SAU thời điểm cutover sẽ
      lấy giá web, KHÔNG còn lấy giá facebook.
- [ ] Đã backup DB production gần nhất (theo quy trình backup định kỳ hiện có),
      ghi lại thời điểm backup để đối chiếu nếu cần restore.
- [ ] Migration `0112_price_books` và `0022_channel_price_book` đã được review —
      đây là migration EXPAND (tạo bảng + cột nullable), an toàn chạy trước khi
      deploy code mới, không khoá bảng lớn (seed `facebook`/`web` chỉ 2 dòng).
      Backfill `product_package_prices` đọc `ProductPackage` theo `iterator(chunk_size=2000)`
      và `bulk_create` theo lô 2000 dòng/lần — đây CHỈ là cách chia INSERT
      thành nhiều câu lệnh cho nhẹ bộ nhớ, KHÔNG chia transaction: toàn bộ
      `RunPython` (`seed_and_backfill`) chạy trong ĐÚNG MỘT transaction của
      migration Django (`Migration.atomic` mặc định `True`, không bị override
      trong file này). Dưới ~100k dòng `product_packages` thì transaction này
      chạy nhanh, yên tâm chạy trong giờ hành chính; trên mốc đó cần cân nhắc
      `atomic = False` + tự chia migration/commit theo lô trước khi chạy trên
      production.
- [ ] Chạy `SELECT count(*) FROM product_packages;` trên production TRƯỚC khi
      migrate, ghi con số vào đây: `______`. Dùng con số này để quyết định có
      cần tách transaction backfill (xem gạch đầu dòng trên) hay chạy thẳng.

## 1. Thứ tự deploy

1. **Migrate DB trước** (`python manage.py migrate`).
   - Tạo bảng `price_books`, `product_package_prices`, cột `channels.price_book_id`.
   - Seed 2 bảng giá `facebook` (mặc định, `is_default=True`) và `web`.
   - Backfill giá hiện có của mọi `ProductPackage` vào bảng `facebook`.
   - Tại bước này CHƯA có kênh nào được gán `price_book` — mọi đơn (kể cả KOC)
     vẫn resolve về bảng mặc định `facebook` như trước (hành vi KHÔNG đổi).
2. **Deploy backend** (image mới chứa toàn bộ API + service tính giá theo
   `price_book_service.resolve_price_book`).
   - Sau bước này API bảng giá (`/api/orders/price-books`, PATCH kênh gán
     `price_book_id`, catalog theo bảng giá) đã sẵn sàng, nhưng hành vi tính
     giá đơn hàng THỰC TẾ vẫn chưa đổi vì chưa kênh nào được gán bảng `web`.
3. **Deploy frontend** (màn quản lý bảng giá, dropdown gán bảng giá cho kênh,
   form tạo đơn tải catalog theo bảng giá của kênh).
   - Sau bước này người dùng có thể VÀO TAY gán bảng giá cho từng kênh qua UI
     nếu muốn — nhưng bước gán hàng loạt cho KOC vẫn nên đi qua command ở
     bước 4 để đảm bảo nhất quán và có log audit rõ ràng.
4. **Chạy dry-run trên production**:
   ```bash
   python manage.py assign_koc_price_book --dry-run
   ```
   - Đọc kỹ danh sách kênh được in ra (`code (id) -> web`). Đối chiếu thủ công
     với danh sách kênh KOC đã biết trong Lark/CRM. `Channel.is_koc`
     (`backend/apps/channels_core/models.py`) là SO KHỚP TUYỆT ĐỐI — `code`
     bằng đúng `"koc"` (không phải chứa chuỗi "koc") HOẶC `id` trùng
     `settings.KOC_CHANNEL_ID`. Nếu thấy kênh LẠ xuất hiện trong danh sách (ví
     dụ do `settings.KOC_CHANNEL_ID` trỏ nhầm sang kênh khác) — DỪNG LẠI,
     không chạy bước 5, báo cáo trước.
5. **Chạy thật**:
   ```bash
   python manage.py assign_koc_price_book
   ```
   - Lệnh idempotent: chạy lại nhiều lần chỉ ghi đè cùng một kết quả
     (`Channel.price_book = web` cho các kênh KOC), không nhân bản dữ liệu, và
     KHÔNG đụng tới bất kỳ kênh nào không phải KOC.
   - Nếu không tìm thấy bảng giá `web` đang active (ví dụ bị xoá mềm hoặc tắt
     `is_active` trước đó), lệnh in lỗi ra `stderr` và KHÔNG ghi gì vào DB —
     kiểm tra lại bảng `price_books` trước khi chạy lại.

## 2. Rollback từng bước

Rollback đi NGƯỢC thứ tự deploy, dừng lại ở bước tương ứng với sự cố:

- **Sự cố sau bước 5 (gán KOC sai)**: không cần rollback code, có 2 cách sửa
  DỮ LIỆU, chọn đúng theo phạm vi sự cố — KHÔNG trộn lẫn:
  - (a) **Đảo NGƯỢC TOÀN BỘ kênh KOC về facebook**: chạy
    `python manage.py assign_koc_price_book --book facebook`. Lệnh này gán lại
    HẾT các kênh có `is_koc=True` (đúng như bước 5 đã làm, chỉ đổi bảng đích),
    không có tham số nào để giới hạn theo từng kênh. Chỉ dùng khi TOÀN BỘ việc
    gán KOC → web là sai (ví dụ gán nhầm bảng, cần lùi hết về trạng thái trước
    cutover).
    **LƯU Ý QUAN TRỌNG:** lệnh này gán `Channel.price_book` TƯỜNG MINH sang
    bảng `facebook` (một FK có giá trị), KHÔNG trả về `NULL` như trạng thái
    trước cutover (kênh KOC trước cutover chưa từng được gán `price_book`, cột
    là `NULL` và luôn resolve qua `default_price_book()`). Hai trạng thái NHÌN
    THÌ giống nhau (đều ra giá facebook) nhưng KHÁC nhau về sau: nếu sau này
    đổi bảng giá mặc định sang một bảng khác `facebook`, kênh có `NULL` sẽ tự
    động đi theo bảng mặc định mới, còn kênh đã bị gán tường minh `facebook`
    ở bước rollback này thì KHÔNG — nó dính cứng vào `facebook` cho tới khi có
    người sửa tay. Muốn trả đúng NGUYÊN TRẠNG (`NULL`) phải làm qua UI quản lý
    kênh (Task 10) hoặc câu lệnh SQL/shell riêng; lệnh `assign_koc_price_book`
    hiện KHÔNG có tuỳ chọn `--book none` để làm việc này (nếu bổ sung sau này,
    bắt buộc phải có test đi kèm).
  - (b) **Sửa một vài kênh cụ thể**: dùng UI quản lý kênh (Task 10) để gán lại
    `price_book` cho TỪNG kênh bị gán nhầm (ví dụ kênh không phải KOC thực sự
    nhưng vô tình khớp điều kiện `is_koc`, hoặc một kênh KOC cần giữ giá
    facebook vì lý do nghiệp vụ riêng). Chỉ dùng khi sự cố KHOANH VÙNG được ở
    một số kênh nhất định, không phải lỗi hệ thống trên toàn bộ tập KOC.
- **Sự cố sau bước 3 (frontend lỗi)**: rollback riêng frontend về bản trước,
  backend + DB giữ nguyên (backend tương thích ngược, API bảng giá là API mới
  thêm, không phá API cũ).
- **Sự cố sau bước 2 (backend lỗi)**: rollback backend về image trước. Migration
  đã chạy vẫn AN TOÀN giữ nguyên — cột `price_book_id` nullable, code cũ không
  biết tới cột này vẫn chạy bình thường (đơn vẫn tính giá qua `product.price` /
  đường cũ tuỳ theo state code trước Task 7).
- **Sự cố sau bước 1 (migration lỗi giữa chừng)**: migration có `RunPython`
  đảo ngược (`unseed`) và các `CreateModel`/`AddField` đều tự đảo ngược được:
  ```bash
  python manage.py migrate channels_core 0021_channel_ad_accounts
  python manage.py migrate orders 0111_order_restored_from
  ```
  Lưu ý: rollback migration sẽ XOÁ toàn bộ `product_package_prices` đã backfill
  và 2 dòng `price_books` — chỉ làm khi chắc chắn chưa có ai gán giá riêng theo
  bảng `web` qua UI (nếu đã gán, dữ liệu đó cũng mất theo).

Trong mọi trường hợp: `OrderItem.unit_price` đã ghi (snapshot tại thời điểm tạo
đơn) KHÔNG bị ảnh hưởng bởi rollback — đơn cũ giữ nguyên giá đã chốt.

## 3. Smoke test thủ công trên staging (chạy TRƯỚC khi lặp lại trên production)

Chuẩn bị: 1 sản phẩm có khai giá ở CẢ HAI bảng `facebook` và `web` (giá khác
nhau, ví dụ facebook 100.000đ / web 130.000đ), 1 kênh code `sale-fb` (không
phải KOC) và 1 kênh code `koc` đã được gán `price_book = web` (qua bước 5 ở
trên hoặc gán tay qua UI).

1. **Tạo đơn kênh Sale FB, chọn sản phẩm nói trên** → kỳ vọng
   `OrderItem.unit_price` = giá facebook (100.000đ).
2. **Tạo đơn kênh KOC, cùng sản phẩm đó** → kỳ vọng `OrderItem.unit_price` =
   giá web (130.000đ).

Đối chiếu bằng SQL (chạy trên DB staging, sửa `LIMIT`/điều kiện nếu cần lọc
đúng 2 đơn vừa tạo):

```sql
SELECT o.id, c.code, oi.unit_price
FROM order_items oi
JOIN orders o ON o.id = oi.order_id
JOIN channels c ON c.id = o.channel_id
ORDER BY o.id DESC
LIMIT 5;
```

Kỳ vọng: dòng ứng với kênh `koc` có `unit_price = 130.00`, dòng ứng với kênh
`sale-fb` có `unit_price = 100.00`.

Nếu cả 2 test đều đúng trên staging, lặp lại đúng quy trình (bước 1 → 5) trên
production.

## 4. CẢNH BÁO — vết gãy báo cáo doanh thu KOC tại ngày cutover

`OrderItem.unit_price` là SNAPSHOT tại thời điểm đơn được tạo, không phải giá
tính động. Sau khi chạy `assign_koc_price_book`:

- Đơn KOC tạo **MỚI** (sau cutover) → ăn giá theo bảng `web`.
- Đơn KOC đã tồn tại **TRƯỚC** cutover → vẫn giữ nguyên `unit_price` đã chốt
  theo bảng `facebook` lúc tạo đơn. **Spec đã chốt KHÔNG backfill đơn cũ.**

Hệ quả: nếu nhìn báo cáo doanh thu/đơn giá theo kênh KOC trải dài qua ngày
cutover, sẽ thấy một bước nhảy giá (thường là tăng, vì giá web > giá facebook
đối với hầu hết sản phẩm hiện có) đúng tại ngày chạy command. **Đây là hành vi
ĐÚNG theo thiết kế, không phải lỗi dữ liệu** — người đọc báo cáo (đặc biệt là
đội phân tích doanh thu/CSKH) cần được thông báo trước để không hoang mang
hoặc report nhầm thành bug. Khi so sánh doanh thu KOC theo thời gian, nên tách
biệt rõ mốc cutover (ghi lại timestamp thực tế chạy bước 5) làm điểm gãy trong
mọi biểu đồ/báo cáo liên quan.

## 4b. CẢNH BÁO — mở-lưu đơn CŨ không có `order_items` sẽ vật chất hoá giá HIỆN TẠI

Đơn hàng cũ (tạo trước khi `OrderItem` tồn tại) chỉ có `order.sku`/`order.quantity`
phẳng trên bản ghi `Order`, không có dòng `OrderItem` nào. Khi form sửa đơn mở
loại đơn này, `toEditableItems` (FE, `order-form-helpers.ts`) tự tổng hợp MỘT
dòng item giả `{sku: order.sku, quantity: order.quantity}` — KHÔNG kèm
`unit_price` — chỉ để có gì đó hiển thị trên form. `buildPatchPayload` luôn gửi
kèm `items` này trong MỌI lần PATCH (kể cả khi người dùng chỉ sửa một field
không liên quan như trạng thái/feedback, không đụng gì tới sản phẩm/số lượng —
UI cũng không cho xoá dòng cuối cùng nên dòng giả luôn có mặt).

Phía backend, `OrderItemInputSerializer.unit_price` là `required=False` —
`OrderServiceHelpers._replace_order_items` khi thấy item không kèm `unit_price`
sẽ tự tính lại bằng `price_book_service.resolve_price_book(order.channel)` +
`package_prices(...)` (giá theo bảng giá HIỆN TẠI của kênh, không phải giá tại
thời điểm đơn được tạo), rồi XOÁ và TẠO LẠI toàn bộ `OrderItem` của đơn, ghi đè
`total_amount`/`total_after_discount`.

**Hệ quả sau cutover:** một đơn KOC CŨ dạng "không có `order_items`" nếu được
mở ra và lưu lại (dù chỉ sửa field không liên quan tới giá) sẽ bị vật chất hoá
giá theo bảng `web` — nhảy giá âm thầm, không phải do ai chủ ý sửa giá. Đơn có
`order_items` thật (đại đa số, kể cả đơn KOC) thì AN TOÀN vì FE luôn gửi kèm
`unit_price` đã có sẵn từ item cũ, không rơi vào nhánh tự tính lại này — nhưng
đây là quy ước ở tầng FE, backend không có ràng buộc cấu trúc nào chặn việc bỏ
`unit_price`, nên đừng dựa vào nó khi review các thay đổi FE sau này liên quan
tới payload sửa đơn.

Hành động khi cutover: nếu biết trước có nhiều đơn KOC cũ dạng item-less đang
được sale thao tác lại thường xuyên (ví dụ đơn đang xử lý CSKH/bomb), cân nhắc
thông báo thêm cho đội đó, hoặc backfill `OrderItem` cho các đơn này TRƯỚC
cutover để đường "tự tính lại giá" không còn cửa xảy ra.

## 5. Task 13 (chưa thực hiện, out of scope)

Việc xoá cột giá cũ (`ProductPackage.price` legacy) là Task 13, CHỦ ĐỘNG hoãn
lại ≥ 1 tuần sau khi Task 1–12 đã chạy ổn định trên production (xem
`.superpowers/sdd/2026-07-31-platform-price-books/progress.md` dòng đầu). Không
thực hiện trong lần deploy này.

## 6. Việc còn nợ (ghi lại để không rơi im lặng, chưa xử lý ở lần fix này)

1. **Import Excel/KOC, ô giá trống chưa resolve theo bảng giá kênh.** Spec yêu
   cầu "giữ ưu tiên file; ô giá trống mới resolve theo kênh", nhưng kế hoạch
   sprint không có task nào cho việc này và cũng không được liệt kê vào phần
   ngoài phạm vi — bị rơi. Hành vi hiện tại: ô giá trống trong file import →
   `unit_price = 0`, và giá tường minh `0` (dù là 0 do rỗng) THẮNG fallback
   bảng giá ở luồng ingest — nên chính luồng import KOC lại KHÔNG đi theo giá
   web như mục tiêu feature. Không phải hồi quy (hành vi giống hệt trên
   `master` trước feature này) nhưng nghịch với mục tiêu "đơn kênh KOC ăn giá
   web". Cần một task riêng: khi ô giá trong file trống, gọi
   `price_book_service.unit_price_for` theo bảng giá của kênh thay vì mặc
   định về 0.
2. **CRUD bảng giá phía FE chưa dựng UI, chỉ có API client.**
   `createPriceBook` / `updatePriceBook` / `deletePriceBook` trong
   `frontend/src/lib/api/price-books.ts` là dead code — chưa có màn hình nào
   gọi tới (chỉ `getPriceBooks()` được dùng, để tra `code` cho dropdown gán
   kênh). Model `PriceBook` cũng chưa được đăng ký ở Django admin
   (`backend/apps/orders/admin.py`). Kết quả: muốn thêm nền tảng bán thứ ba
   (ngoài `facebook`/`web`) trên production hiện phải gọi thẳng API bằng tay
   (Postman/curl) hoặc vào Django shell — chưa có đường UI. Cần một task dựng
   màn hình quản lý bảng giá (list + create + update + toggle active/default)
   và đăng ký `PriceBookAdmin`.
