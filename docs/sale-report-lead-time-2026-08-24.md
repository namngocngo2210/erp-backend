# Lead time xử lý data — Báo cáo Sale (2026-08-24)

> **Sửa mốc gốc cùng ngày** sau khi đo trên prod — xem §7. Bản đầu đo từ
> `orders.created_at`; bản đang chạy đo từ lúc data **được chia cho sale**.

## 1. Chỉ số

**Lead time xử lý data** = khoảng thời gian từ lúc data **đến tay sale** tới lần
**ĐẦU TIÊN** có kết quả tác nghiệp, tính **trung bình** trên từng ô của bảng.

Mốc "có kết quả tác nghiệp" = `OrderJourneyEvent` đầu tiên với
`event_type = 'sales_status_changed'`. Chọn event thay vì đọc
`order_assessments.updated_at` vì assessment chỉ giữ trạng thái HIỆN TẠI: đơn đổi
trạng thái nhiều lần sẽ mất dấu lần đầu, còn journey event là bản ghi bất biến.

Lần **lưu đánh giá đầu tiên** cũng bắn event này (xem
`OrderServiceHelpers.save_assessment`), nên không sót đơn nào.

## 2. Ba quyết định nghiệp vụ (chốt 2026-08-24)

| Câu hỏi | Chốt | Vì sao |
|---|---|---|
| Đơn chưa từng đổi trạng thái sale | **Loại khỏi cả tử số lẫn mẫu số** | Đơn còn treo chưa có kết quả để đo. Không hiển thị cột "chưa xử lý" riêng. |
| Trung bình hay trung vị | **Trung bình, cắt trần từng đơn ở 7 ngày** | Trung vị chống nhiễu tốt hơn nhưng KHÔNG cộng được: mỗi ô của bảng sẽ phải là một query riêng. Cắt trần đạt phần lớn lợi ích với chi phí bằng 0. |
| Đơn KiotViet tự lật sang "đã lên đơn" | **Có tính** | Bán trực tiếp / tạo vận đơn KShip thành công lật trạng thái mà sale không bấm tay. Loại ra thì cả nhóm đơn này biến mất khỏi chỉ số. |

Trần nằm ở `LEAD_TIME_CAP` (`apps/orders/reporting/shared.py`). Đổi hằng số này
là đổi số của **cả hai tab** — nhớ sửa kèm ghi chú dưới bảng ở `SaleMetricTable.tsx`
(chuỗi "7 ngày" đang viết tay).

## 3. Vì sao backend trả TỔNG chứ không trả TRUNG BÌNH

`summary_sale_daily_by_staff()` trả hàng phẳng (ngày, nhân viên) và FE gộp lại
theo hai chiều khác nhau:

- Tab **Báo cáo theo sale**: gộp mọi ngày về một hàng cho mỗi nhân viên.
- Tab **Báo cáo chi tiết theo sale**: lọc một nhân viên rồi trải theo ngày.

Nếu backend trả sẵn trung bình thì FE không cộng lại được — mỗi hàng có mẫu số
khác nhau, cộng trung bình sẽ ra "trung bình của trung bình". Nên payload mang
hai số rời:

- `lead_time_seconds` — tổng giây (đã cắt trần từng đơn)
- `lead_time_orders` — số đơn đã có mốc tác nghiệp

FE cộng cả hai rồi mới chia (`toRow` trong `sale-report-selectors.ts`), giống hệt
cách `closeRatePct` dựng từ `orders`/`dataCount`.

## 4. Bẫy: `LEAST` trong Postgres BỎ QUA đối số NULL

`LEAST(NULL, interval '7 days')` trả về `'7 days'`, **không** trả `NULL` — khác
hẳn phép toán số học thông thường. Nghĩa là nếu để đơn chưa có mốc tác nghiệp lọt
vào biểu thức cắt trần, mỗi đơn còn treo sẽ được cộng nguyên **7 ngày** vào tổng.

Vì vậy `Sum(...)` **bắt buộc** mang `filter=Q(first_touch_at__isnull=False)`, không
dựa vào NULL tự lan truyền. Test `test_untouched_order_excluded_instead_of_counted_as_cap`
khoá đúng bẫy này.

`Greatest(0, ...)` bọc ngoài chặn ca dữ liệu backfill có event ghi lùi trước
`created_at` kéo tổng xuống âm.

## 5. Chi phí query

Hai `Subquery` tương quan (mốc tác nghiệp + mốc bàn giao), cả hai phủ trọn bởi
index có sẵn `ord_journey_ord_type_cr_idx (order, event_type, created_at)` —
không cần migration, không thêm index. Toàn bộ báo cáo vẫn là **một câu
group-by duy nhất**, khoá bằng `test_report_stays_a_single_query`.

Lưu ý chi phí: sau khi đổi mốc gốc (§7), subquery mốc tác nghiệp xuất hiện **4
lần** trong SQL (biểu thức `Sum`, `FILTER` của `Sum`, `FILTER` của `Count`, và
điều kiện `created_at <=` của subquery bàn giao). Postgres đánh giá lại từng lần
→ ~4 lần index lookup mỗi đơn thay vì 1. Với một tháng data thì không đáng kể,
nhưng đây là thứ cần nhìn đầu tiên nếu endpoint chậm đi.

Sau deploy nên `EXPLAIN (ANALYZE, BUFFERS)` endpoint
`/api/orders/reports/sale-by-staff` với bộ lọc 1 tháng trên prod để xác nhận
planner chọn index scan chứ không seq scan bảng `order_journey_events`.

## 6. Đọc số thế nào

Cột hiển thị `<1p` / `42p` / `5h 12p` / `2n 3h`; ô `—` nghĩa là **chưa đơn nào
trong hàng đó được xử lý**, không phải lead time bằng 0.

Số này **không** phản ánh riêng tốc độ của sale: nó gồm cả độ trễ chia data. Nếu
data đổ về ban đêm và chia sáng hôm sau, lead time đã cộng sẵn phần chờ đó — đúng
theo định nghĩa "từ lúc nhận data", nhưng cần nhớ khi so sánh giữa các sale làm
khung giờ khác nhau.

## 7. Sửa mốc gốc: từ lúc CHIA DATA, không phải lúc đơn đổ về

Bản đầu tiên (MR !1351) đo từ `orders.created_at`. Số đầu tiên trên prod lộ ra
ngay vấn đề — lead time **tăng đơn điệu theo lượng data**:

| Sale | Data nhận | Lead time (bản cũ) |
|---|---|---|
| Vân Hà | 1 | 27p |
| Hiển | 15 | 58p |
| Hiển Landing | 27 | 1h37p |

Đó không phải chênh lệch năng lực mà là **độ trễ hàng đợi**.

### Nguyên nhân

`LeadAssignmentService.assign_if_unassigned()` **không chia đơn khi data về ngoài
giờ làm việc**:

```python
if shift is None:
    logger.info("Lead skip auto-assign (ngoài giờ làm việc): order_id=%s", order.id)
    return
```

Đơn đó nằm chờ tới khi `sweep_unassigned()` chạy trong giờ hành chính. Data về
23h, chia 8h sáng, sale gọi 8h10 → đo từ `created_at` ra **9h10p** trong khi sale
mất 10 phút. Ai nhận nhiều data thì càng dính nhiều ca như vậy — đúng cái dốc ở
bảng trên. Đơn vào qua import/KiotViet cũng thế: không đi webhook nên không chạy
chia lúc tạo, phải đợi sweep.

### Mốc gốc mới

Lần bàn giao **gần nhất còn hiệu lực tại thời điểm tác nghiệp**
(`created_at <= first_touch_at`), lấy từ `STAFF_HANDOVER_EVENT_TYPES`:
`staff_assigned` / `staff_ingest_designated` / `staff_changed`.

Vì sao là "gần nhất **trước** mốc tác nghiệp" chứ không phải lần chia đầu tiên,
cũng không phải lần gần nhất tuyệt đối:

| Ca | Lần đầu | Gần nhất tuyệt đối | **Gần nhất trước tác nghiệp** |
|---|---|---|---|
| A ôm 4h không đụng → chuyển B → B xử lý sau 15p | 4h15p (bắt B gánh phần của A) | 15p ✓ | 15p ✓ |
| Chia lúc +1h → xử lý lúc +2h → chuyển tay lúc +5h | 1h ✓ | −3h → kẹp về 0 ✗ | 1h ✓ |

Cột giữa "làm đẹp số một cách giả tạo" ở ca thứ hai, nên bị loại.

Không có event bàn giao nào → rơi về `orders.created_at`: đơn tạo tay đã có sẵn
sale, và đơn cũ có trước khi hệ thống ghi event chia, đều không có mốc nào tốt hơn.

### Hệ quả sau deploy

**Cột "Lead time xử lý data" sẽ TỤT mạnh** so với bản !1351 — phải báo trước kẻo
bị hiểu là mất số. Phần chênh không biến mất mà chuyển sang cột mới ở §8.

## 8. Cột "Thời gian chờ chia data"

Phần thời gian bị tách ra khỏi lead time ở §7 là một vấn đề vận hành có thật
(data về đêm nằm chờ tới ca sau), nên nó được hiển thị thành cột riêng chứ không
bị giấu đi: từ `orders.created_at` tới mốc bàn giao.

**Hai cột dùng CHUNG mẫu số `lead_time_orders`** — đây là quyết định có chủ đích,
không phải tiết kiệm payload. Chúng đo hai chặng LIÊN TIẾP của cùng một tập đơn:

```
chờ chia data  +  lead time xử lý  =  từ lúc đơn đổ về  →  lúc tác nghiệp
```

Tách mẫu số là hai cột hết cộng được, và bảng mất đi bất biến dễ kiểm tra nhất.
Khoá bằng `test_two_columns_add_up_to_elapsed_time_since_order_arrived`.

Ngoại lệ duy nhất: khi CẢ HAI chặng đều đụng trần `LEAD_TIME_CAP` — trần áp cho
từng chặng một cách độc lập.

Đơn không có mốc bàn giao → `start_at = created_at` → chờ chia bằng 0, đúng nghĩa
"đơn tạo tay đã có sẵn sale". Đơn chưa xử lý không vào cả hai cột.

Thứ tự cột trên bảng theo đúng vòng đời: … → AOV → **Chờ chia data** → **Lead time
xử lý data**.

### Đọc cặp số này thế nào

- Chờ chia cao, lead time thấp → nghẽn ở **bộ chia**, không phải ở sale. Nhìn
  `LeadAssignmentService` và lịch chạy `sweep_unassigned()`.
- Chờ chia thấp, lead time cao → nghẽn ở **sale**.
- Cả hai cao → xem lại cả hai, và kiểm tra xem có phải data về ngoài giờ rồi sale
  cũng vào ca muộn không.
