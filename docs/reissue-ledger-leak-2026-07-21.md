# Sự cố tồn phồng: đơn xuất-lại `<mã>.NN` bị bỏ rơi sổ xuất (2026-07-21)

## 1. Tóm tắt

Ngày 21/07/2026, khi tra cứu vòng đời tồn kho của mã vận đơn `999800010074545`,
phát hiện một lớp lỗi rộng hơn: **10 cặp đơn, 12 sản phẩm** đang được hệ thống
đếm là tồn trong kho trong khi hàng đã giao cho khách.

| Kho | SKU | SL phồng |
|---|---|---|
| Đại Mỗ 1 | MSC06 | 6 |
| Đại Mỗ 1 | BNC02 | 2 |
| Đại Mỗ 1 | TMS01-PROMAX | 2 |
| Đại Mỗ 1 | DC30 | 1 |
| Đại Mỗ 2 | DC12 | 1 |
| | **Tổng** | **12** |

Khoảng thời gian ảnh hưởng: 08/07 → 20/07/2026.

## 2. Cơ chế lỗi

KiotViet xử lý "huỷ rồi xuất lại hoá đơn" bằng cách sinh mã mới `<gốc>.NN`
(vd `HD012791` → `HD012791.01`). Khi bản `.NN` **không reattach được** vào đơn cũ
(về trước webhook huỷ, hoặc về qua cron import đêm), nó thành một DÒNG ĐƠN MỚI.

Chuỗi sự kiện dẫn tới tồn phồng:

1. Đơn gốc xuất kho → `stock_movements` ghi `sale` (−N).
2. KiotViet huỷ đơn gốc → subscriber `_on_order_status_changed` **hoàn tồn** (+N,
   note `Hoàn tồn do huỷ đơn`).
3. Đơn `.NN` mang hàng thật đó đi giao, `status` lên `3 = giao thành công`, nhưng
   **không có bất kỳ bút toán nào** — vì nó là bản ghi khác, và luồng "trừ lại khi
   hồi sinh" chỉ áp cho chính đơn bị huỷ.
4. Kết quả: hàng ở chỗ khách, sổ ghi hàng trong kho.

Bản vá `356bc946` (18/07) đã thêm `transfer_issue_to_sibling`: lúc huỷ, nếu tìm
thấy đơn anh em đang sống chưa có sổ thì **chuyển sổ** thay vì thả tồn. Nhưng nó
chỉ bắt tại **đúng khoảnh khắc** webhook huỷ về. Hai ca ngày 20/07 vẫn lọt dù đơn
anh em đã tồn tại trước đó 3–6 phút — nhiều khả năng `invoice_code`/`shipping_code`
của đơn `.NN` được điền sau thời điểm huỷ, khiến `_find_live_reissued_sibling`
không khớp.

**Bài học kiến trúc:** không thể đảm bảo đúng thứ tự đến của hai luồng KiotViet
độc lập. Bắt-tại-chỗ là tối ưu, không phải bảo đảm; cần một lưới hứng chạy sau.

## 3. Thiết kế bản vá

### 3.1 `ReissueLedgerRepairService` (lưới an toàn)

Quét định kỳ tìm cặp lệch rồi ghi sổ bù:

- **Phát hiện**: đơn `status=6` từng có `sale`, đã hoàn tồn hết (`outstanding = 0`,
  có reversal note `Hoàn tồn do huỷ đơn`), tồn tại đơn anh em cùng mã vận đơn +
  cùng gốc hoá đơn, đang sống (`status ≠ 6`), **chưa có bút toán nào**.
- **Sửa**: ghi `sale` cho đơn anh em, mirror đúng (kho, sản phẩm, số lượng) mà đơn
  huỷ đã thả ra, note `Nhận sổ xuất từ đơn bị huỷ` (`SIBLING_TRANSFER_REISSUE_NOTE`).
  Note này nằm trong `_ADMIN_SALE_NOTES` của `dispatch_history_service` nên **không
  làm sai cột "ngày xuất"** của báo cáo lịch sử xuất kho.
- **Bỏ qua + báo cáo** (không tự sửa) khi đơn huỷ có `ReturnEntry` đã ghi sổ: hàng
  đã vật lý quay về kho thì ghi thêm `sale` sẽ trừ tồn hai lần. Ca này cần người xem.

Tái dùng nguyên vẹn quy tắc nhận diện anh em của bản vá cũ — tách
`_find_live_reissued_sibling` từ `subscribers.py` sang module dùng chung để hai
đường (bắt-tại-chỗ và quét-bù) không bao giờ lệch luật.

### 3.2 Điểm chạy

| Đường | Cách chạy | Vai trò |
|---|---|---|
| `transfer_issue_to_sibling` | Ngay lúc huỷ (đã có) | Bắt sớm, giữ tồn liên tục đúng |
| `repair_reissue_ledger` | Management command, có `--dry-run` | Vá dữ liệu lịch sử, chạy tay |
| `sweep_reissue_ledger` | Celery beat 30' | Lưới an toàn, cảnh báo Lark khi phát hiện |

## 4. Việc còn lại (chưa làm trong lần này)

- **Đơn `.NN` sinh từ KiotViet luôn có `fulfillment_warehouse` rỗng.** Đây là lý do
  không ai xuất kho tay được cho chúng, và cũng làm báo cáo doanh số theo kho bỏ
  sót. Cần cho luồng sync kế thừa kho từ đơn gốc — tách thành việc riêng.
- **Không có guard chặn gỡ `fulfillment_warehouse` khỏi đơn đã trừ tồn**
  (serializer đang để `allow_null: True`). Đơn 546875 là ví dụ: có bút toán xuất
  tại Đại Mỗ 1 nhưng trường kho hiện rỗng.
- Xác minh dứt điểm liệu bản vá `356bc946` đã được deploy trước 20/07 hay chưa
  (log Loki của cửa sổ đó không truy được vì profile monitoring chưa bật).
