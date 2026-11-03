# Đồng bộ mã phiếu trả hàng từ KiotViet

## Luồng

- Celery beat `kv-sync-returns` chạy mỗi 15 phút → task `apps.kiotviet.tasks.sync_kiotviet_returns`
  → service `sync_returns_all_channels(last_modified_from=now-2h)`.
- Gọi `GET /returns` (pageSize=100, phân trang `currentItem`) cho mọi `KiotVietChannel` active
  **có `old=False`**. Một kênh lỗi không chặn kênh còn lại (gom vào `summary["errors"]`, log warning).
- **Kênh cũ (mirror, `old=True`) bị loại chủ đích:** hoá đơn của nó nằm ở `Order.old_id` /
  `old_invoice_code`, không phải `Order.invoice_id` (trường này là hoá đơn kênh mới, và
  `Order.kiotviet_channel` cũng trỏ kênh mới). Sync kênh cũ theo `invoice_id` trượt 100% và
  từng làm KiotViet trả `420` (rate-limit) hàng nghìn lần.
- Chạy theo 3 pha: **(1) gom** toàn bộ phiếu trả (phân trang hết, lọc `returnDate` theo
  `--from/--to` ngay trong bộ nhớ, chưa đụng DB) → **(2) map cả lô** → **(3) ghi** từng đơn.
- Map cả lô bằng **1 query** `invoice_id__in` (không N+1) và **không gọi thêm API**. Trượt →
  đếm `skipped_no_order`, bỏ qua (**không** tạo đơn mới). Cố ý bỏ fallback `GET /invoices/{id}`:
  nó bắn 1 request/id, KiotViet trả `420` rate-limit và vẫn không khớp thêm đơn nào.
- Ghi vào `OrderRecall.return_slip_code` theo policy **UNION**: giữ nguyên mọi mã đang có
  (nhập tay hoặc đã sync trước đó, đúng thứ tự), chỉ **nối thêm** mã KiotViet chưa có trong
  mảng. Không bao giờ xoá hay sắp xếp lại mã đã có — nếu KiotViet không mang mã mới nào thì
  không ghi, đếm vào `skipped_no_new_codes`. Đọc-sửa-ghi từng đơn nằm trong
  `transaction.atomic()` + `select_for_update()` (qua `OrderRecall.objects.get_or_create`) để
  tránh: (1) PATCH tay của nhân viên chạy song song bị ghi đè, (2) hai tiến trình cùng tạo
  `OrderRecall` lần đầu ném `IntegrityError` làm hỏng các đơn còn lại trong cùng lượt chạy.
- Task giữ lock `kv:sync_returns:lock` (`single_flight`, TTL 360s > time_limit 300s) nên tick sau
  không chạy chồng khi lần trước chưa xong.
- Chiều ngược lại (PATCH tay inline → `OrderServiceHelpers._upsert_order_recall`) cũng khoá
  `OrderRecall` bằng `select_for_update()` trước khi ghi, cùng lock class hàng với sync — 2 request
  ghi đồng thời (nhân viên sửa tay + tick sync chạy đúng lúc) được tuần tự hoá thay vì đọc-ghi
  chồng nhau; request sau luôn thấy dữ liệu request trước vừa commit. Field không có mặt tường
  minh trong payload PATCH tay giữ nguyên, không bị suy diễn/ghi đè.
- **Hệ quả vận hành của UNION:** nếu nhân viên XOÁ một mã do sync điền (vd phiếu trả đã huỷ) mà
  phiếu đó vẫn nằm trong cửa sổ `lastModifiedFrom` 2h, tick 15' kế tiếp sẽ điền lại. Muốn gỡ hẳn
  thì xoá sau khi phiếu đã ra khỏi cửa sổ, hoặc sửa ở KiotViet.
- `iter_returns` có trần cứng `MAX_PAGES = 1000`: nếu KiotViet lờ `currentItem` (đổi hợp đồng
  API/bug) thì phân trang raise `RuntimeError` thay vì lặp vô hạn.
- Nếu channel gặp `SoftTimeLimitExceeded` (celery soft-timeout, sắp bị SIGKILL) thì lỗi này
  được raise thẳng ra ngoài, **không** gom vào `summary["errors"]` — tránh việc worker chạy
  tiếp sang kênh khác cho tới lúc hard time-limit giết giữa chừng.

## Backfill lịch sử

```bash
docker compose exec web python manage.py sync_kiotviet_returns --from 2026-01-01 --to 2026-07-11 --dry-run
docker compose exec web python manage.py sync_kiotviet_returns --from 2026-01-01 --to 2026-07-11
```

Chạy một kênh: thêm `--channel-id <id>` (id không tồn tại hoặc đã xoá mềm → `CommandError`).
`--from/--to` lọc theo `returnDate` (client-side), định dạng `YYYY-MM-DD`.

Summary in ra JSON: `read`, `matched`, `updated`, `skipped_no_order`, `skipped_no_code`,
`skipped_out_of_window`, `skipped_no_new_codes`, `skipped_invalid_invoice_id`, `pages`,
(+ `channels`, `errors` khi chạy all).

`skipped_invalid_invoice_id` = `invoiceId` không parse được thành số nguyên (dữ liệu rác từ
KiotViet) — chỉ bỏ qua item đó, không abort cả kênh.

`updated` = số đơn có mảng **thay đổi thật sự** (có mã mới được nối thêm).
`skipped_no_new_codes` = KiotViet không mang mã nào mới cho đơn đó (mọi mã KiotViet trả về
đã có sẵn trong mảng, kể cả khi mảng đó có mã nhân viên nhập tay).

## Ghi chú

- `return_slip_code` là `varchar(64)[]` ở DB (migration `orders/0101`), nhưng API vẫn trả/nhận
  chuỗi nối phẩy (`"TH001, TH002"`) → frontend không phải sửa. Migration tách chuỗi phẩy cũ
  thành nhiều phần tử.
- Trước khi deploy prod: kiểm tra `SELECT count(*) FROM order_recalls;` — migration đổi kiểu cột
  sẽ lock bảng trong lúc rewrite (đã thêm `SET lock_timeout = '5s'` ở đầu `FORWARD_SQL` để migration
  fail nhanh thay vì xếp hàng vô thời hạn phía sau một reader chạy lâu).
- **Reverse SQL bị mất dữ liệu (lossy):** nếu rollback migration `0101` SAU KHI đã sync (mảng có
  ≥2 phần tử), `array_to_string(return_slip_code, ', ')` rồi cắt về 64 ký tự sẽ làm mất mọi mã
  vượt quá 64 ký tự gộp — rollback không phục hồi được dữ liệu gốc. Không rollback migration này
  trên môi trường đã chạy sync thật.
- Sau deploy: chạy `--dry-run` cho một khoảng ngày nhỏ và đối chiếu `matched` / `skipped_no_order`
  trước khi để cron chạy tự do.
