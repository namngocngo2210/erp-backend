# Fix chia đơn tự động — UX + invalidate khung giờ tức thì

## Bối cảnh
Chức năng "Chia đơn tự động" (`LeadAssignmentService`) gán đơn cho NV phòng Sale
ngay lúc ingest, theo cấu hình `LeadAssignmentShare(date, shift, sheet, user, weight)`.
Người vận hành phản ánh 4 vấn đề. Sau điều tra (xem phần Chẩn đoán) xác định:

| Triệu chứng | Nguyên nhân gốc |
|---|---|
| Ấn lưu nhưng "không lưu" | `save()` FE không có toast thành công; dòng weight=0 bị BE drop → tưởng mất |
| Không biết bao lâu mới áp dụng | Ma trận áp dụng NGAY; khung giờ ca trễ tới 60s (cache TTL/process), UI không nói |
| Có đơn được gán có đơn không | Đơn bị skip nếu: đã có staff / `classify_sheet`=None / ngoài giờ / sheet chưa cấu hình. Đúng thiết kế nhưng không minh bạch |
| Xoá cấu hình vẫn gán | Xoá chỉ tác động đúng cặp (ngày, ca) đang xem; ca/ngày khác vẫn còn |

## Quyết định
- **Giữ mô hình per-date** (không bỏ field `date`). Chỉ xử lý UX + bug.
- **Invalidate khung giờ tức thì qua Redis** thay cho chờ TTL 60s.

## Phạm vi thay đổi

### Backend
1. `shift_service.py`: thêm **generation-counter trên Redis** (`cache`). `invalidate_cache()`
   tăng counter (atomic `incr`); `_windows()` đọc counter (1 GET rẻ) — counter đổi ⇒ nạp lại
   ngay ở mọi process. Vẫn giữ TTL 60s làm fallback khi Redis lỗi. Bọc try/except để
   Redis chết không làm vỡ luồng.
2. `lead_assignment_view.py`: thêm method **DELETE** cho `lead_assignment_shares` —
   xoá toàn bộ cấu hình của 1 NGÀY (cả 3 ca) → nút "Xoá cấu hình ngày này" rõ ràng.

### Frontend (`LeadAssignmentPage.tsx`)
3. `save()`: thêm `toast.success`, cảnh báo (non-blocking) khi cột ≠ 100%.
4. Banner cảnh báo khi **ca đang sửa ≠ ca hệ thống đang chạy** (đơn mới chia theo ca hệ thống).
5. Nút **"Xoá cấu hình ngày này"** (gọi DELETE, có confirm).
6. Ghi chú minh bạch: điều kiện 1 đơn được chia tự động + thời điểm áp dụng.

## Tiến độ
- [x] Điều tra & chẩn đoán
- [x] BE: Redis gen invalidate + test
- [x] BE: DELETE day endpoint + test
- [x] FE: toast, banner ca, nút xoá ngày, ghi chú
- [x] Chạy test BE
