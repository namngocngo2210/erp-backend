# Thiết kế: Thông báo Lark đơn cần xuất VAT sau khi kship thành công

## 1. Mục tiêu
Khi một đơn có `vat ∈ {individual, company}` được **giao kship thành công và đã có
mã vận đơn**, tự động gửi thông báo vào **nhóm Lark riêng của team kế toán/VAT** để
họ chủ động xuất hoá đơn.

## 2. Điểm kích hoạt (trigger)
Tái dùng hook sẵn có trong luồng xử lý job KiotViet:
`apps/orders/infrastructure/tasks/order_tasks_impl/ingest_job_tasks.py` — ngay sau khi
`job.status == SUCCESS` và `job.action == "create_kship_delivery"` và `order.shipping_code`
đã có (cùng chỗ đang bắn event Zalo `shipping_started`).

Điều kiện bổ sung để dispatch: `order.vat ∈ {individual, company}`.

## 3. Luồng kỹ thuật
1. Hook → `dispatch("lark.notify_vat_invoice_required", queue="lark_noti", order_id=...)`
   (best-effort, bọc try/except, không chặn luồng chính).
2. Registry `apps/dispatch/registry.py` map tên → Celery task
   `notify_lark_vat_invoice_required`.
3. Task (`lark_notification_tasks.py`):
   - Phòng vệ: chỉ xử lý khi `vat ∈ {individual, company}`, có `shipping_code`, và
     `vat_invoice_notified_at IS NULL`.
   - Soạn nội dung (xem §4) → `send_vat_invoice_notification(text)` (webhook VAT riêng).
   - **Idempotent**: gửi xong đánh dấu `vat_invoice_notified_at` bằng `UPDATE ... WHERE
     vat_invoice_notified_at IS NULL` (atomic, chống gửi trùng khi job kship chạy lại).
     Gửi lỗi → không đánh dấu, để lần sau còn cơ hội gửi lại.
4. `lark_notifier.send_vat_invoice_notification(text)`: POST text tới
   `LARK_VAT_BOT_WEBHOOK_URL` (ký HMAC nếu có secret). Webhook rỗng → tắt âm thầm.

## 4. Nội dung tin nhắn
```
🧾 Đơn cần xuất hoá đơn VAT (Công ty)
• Mã đơn/HĐ: HD001            (invoice_code → order_code → #id)
• Mã vận đơn: KS123456 (ghn)  (shipping_code + provider/service)
• Khách: Nguyễn A - 0909xxxxxx
```

## 5. Cấu hình (env mới)
| Env | Ý nghĩa |
|-----|---------|
| `LARK_VAT_BOT_WEBHOOK_URL` | Webhook nhóm Lark kế toán/VAT (rỗng = tắt) |
| `LARK_VAT_BOT_SECRET` | Secret ký HMAC (nếu bot bật) |
| `LARK_VAT_BOT_TIMEOUT_SECONDS` | Timeout HTTP (mặc định 5s) |

Task route: `notify_lark_vat_invoice_required` → queue `lark_noti` (worker đã chạy queue này).

## 6. Schema
Migration `0082_order_vat_invoice_notified_at`: thêm `Order.vat_invoice_notified_at`
(DateTimeField, null) — cờ chống gửi trùng. Additive, an toàn.

## 7. Sprint & Tiến độ — Sprint 1 (DONE)
- [x] Model field + migration 0082
- [x] `lark_notifier.send_vat_invoice_notification`
- [x] Task `notify_lark_vat_invoice_required` (idempotent) + registry + task route
- [x] Hook dispatch tại kship-success
- [x] Settings env `LARK_VAT_BOT_*`
- [x] Unit test 6/6 pass · `manage.py check` OK · migration khớp model
- [ ] **Cấu hình `LARK_VAT_BOT_WEBHOOK_URL` ở môi trường thật** + apply migration khi deploy:
  `docker exec desi-web python manage.py migrate orders`

## 8. Ghi chú vận hành
- Hiện kship là provider giao vận duy nhất → trigger phủ đủ. Nếu sau này thêm provider
  khác cũng set `shipping_code`, cần mở rộng điều kiện hook tương ứng.
- Đơn được tạo trước khi bật tính năng vẫn có `vat_invoice_notified_at = NULL`; nếu kship
  chạy lại cho các đơn đó sẽ gửi (1 lần). Không bắn hồi tố cho đơn đã kship xong từ trước.
