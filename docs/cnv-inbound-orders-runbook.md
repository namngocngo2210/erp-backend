# Runbook: bật chiều kéo đơn CNV → desi-order

*Thiết kế: `docs/superpowers/specs/2026-08-19-cnv-inbound-orders-design.md`*

Thứ tự dưới đây **không đảo được**. Bật cờ trước khi register webhook thì mở một
endpoint công khai không ai gọi; register trước khi deploy thì CNV gọi vào 404 và
(nếu CNV có retry) đơn rơi mất trong lúc mình chưa sẵn sàng.

## 0. Điều kiện trước

- [ ] Đã có `CnvConnection` active (chiều đẩy lên đang chạy).
- [ ] Domain public đã có cert, `https://<domain>/api/cnv/webhook` gọi tới được.
- [ ] Sinh verify token: `python -c "import secrets;print(secrets.token_urlsafe(48))"`

## 1. Migrate TRƯỚC, roll code SAU

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py migrate cnv_loyalty 0003
docker compose -f docker-compose.prod.yml exec web python manage.py migrate orders 0130
```

Toàn bộ là `CREATE TABLE` mới + một `AlterField` chỉ đổi `choices` (không đụng
schema Postgres) → chạy được giờ hành chính, không khoá bảng `orders`.

Roll code kèm `"cnv"` vào `MIRROR_ORDER_SOURCES`: từ lúc đó đơn `order_source="cnv"`
bị loại khỏi mọi báo cáo/danh sách nội bộ. **Tác động thực tế lúc deploy bằng
không** vì chưa có đơn CNV nào trong DB.

## 2. Env (chưa bật cờ)

Thêm vào `.env` prod rồi recreate `web` + `worker` + `beat`:

```
CNV_INBOUND_ENABLED=0
CNV_WEBHOOK_VERIFY_TOKEN=<token vừa sinh>
CNV_WEBHOOK_CALLBACK_URL=https://<domain>/api/cnv/webhook
CNV_WEBHOOK_TOPICS=orders/create,orders/updated
```

> Bẫy đã cắn nhiều lần: `docker compose pull` kéo cả image core và recreate nhiều
> service cùng lúc gây trùng tên container. Recreate từng service một.

## 3. Hỏi CNV đang đăng ký gì — **BẮT BUỘC trước khi register**

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py cnv_webhook list
```

Swagger CNV khai `topics` là `array<string>` **không kèm danh sách giá trị hợp lệ**,
tài liệu công khai cũng không có. Đây là cách duy nhất biết tên topic thật. Nếu
kết quả cho thấy tên khác `orders/create` / `orders/updated`, sửa `CNV_WEBHOOK_TOPICS`
trong `.env` rồi recreate lại — **không sửa code**.

Nếu `list` trả rỗng hoặc lỗi: hỏi CNV support danh sách topic. Đăng ký bằng topic
đoán sai thì CNV trả 200 nhưng **không bao giờ gửi gì về**, và không có tín hiệu
nào báo là sai — chỉ phát hiện được bằng cách thấy `cnv_webhook_events` mãi rỗng.

## 4. Register

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py cnv_webhook register
```

Lệnh in cấu hình rồi hỏi xác nhận. Nó tự chặn: callback không phải HTTPS, thiếu
verify token, thiếu topics.

## 5. Bật cờ

```
CNV_INBOUND_ENABLED=1
```

Recreate `web` (nhận webhook) + `worker`/`beat` (poll + retry + purge).

## 6. Xác minh — đặt một đơn thật trên app CNV

```sql
-- Event webhook có về không?
SELECT id, topic, cnv_object_id, processed, attempts, last_error, received_at
FROM cnv_webhook_events ORDER BY id DESC LIMIT 10;

-- Đơn đã vào chưa?
SELECT o.id, o.order_code, o.order_source, o.partner_delivery_status, l.cnv_order_id
FROM orders o JOIN cnv_order_links l ON l.order_id = o.id
ORDER BY o.id DESC LIMIT 10;
```

Đúng thì: `processed = true`, có dòng trong `cnv_order_links`, `order_source = 'cnv'`.

**Kiểm tra chống trùng — việc quan trọng nhất:** tạo một đơn trên CRM, đợi nó
đẩy lên CNV (`cnv_sync_records` có `cnv_id`), rồi xem đơn đó **không** quay về
thành đơn mirror:

```sql
-- Phải trả 0 dòng. Khác 0 là chống trùng thủng.
SELECT l.cnv_order_id
FROM cnv_order_links l
JOIN cnv_sync_records r ON r.cnv_id = l.cnv_order_id AND r.entity_type = 'order';
```

Nếu ra dòng: **tắt `CNV_INBOUND_ENABLED` ngay**, rồi kiểm `unique_id` trong
`cnv_order_links` — CNV không echo `unique_id` và tầng 2 cũng trượt nghĩa là
`cnv_sync_records.cnv_id` đang rỗng (xem §9 của thiết kế).

## 7. Nếu webhook không về

Poll chạy độc lập, không phụ thuộc webhook — kiểm nó trước khi kết luận:

```bash
docker compose -f docker-compose.prod.yml exec worker \
  python -c "from apps.cnv_loyalty.tasks import poll_inbound_orders; print(poll_inbound_orders())"
```

`{"reason": "disabled"}` → chưa bật cờ. `{"reason": "locked"}` → lần chạy trước
chưa xong. Có số `created` → đường poll ổn, vấn đề nằm ở đăng ký webhook.

Quét lại một khoảng quá khứ: sửa `synced_until` của `cnv_inbound_cursors` (resource
`orders`) lùi về mốc cần, qua Django admin hoặc SQL.

## 8. Tắt khẩn cấp

```
CNV_INBOUND_ENABLED=0    # dừng nhận + dừng poll, KHÔNG cần gỡ đăng ký bên CNV
```

Webhook sẽ trả 503 (không phải 200) để CNV biết mình chưa nhận. Gỡ hẳn đăng ký
chỉ khi bỏ hẳn tính năng: `manage.py cnv_webhook delete`.

## 9. Nợ sau deploy

- [ ] Chạy `cnv_webhook list` và **ghi lại tên topic thật** vào thiết kế §7 — cả
      hệ thống đang chạy trên một giá trị mặc định chưa xác minh.
- [ ] Probe `GET /orders.json` bằng token thật, xác nhận response **có** trả
      `unique_id`. Nếu không, tầng dedup 1 vô hiệu và tầng 2 gánh một mình.
- [ ] Đo `EXPLAIN` cho `_should_skip` khi `cnv_sync_records` lớn — hiện là 1 query
      trên mỗi đơn poll về (index `cnv_sync_cnv_id_idx` đã có).
- [ ] Quyết định về khách CNV chưa khớp SĐT: hiện gom hết vào một sentinel
      `CNV-UNKNOWN`. Nếu sau này tính doanh thu CNV thì phải promote thành
      `Customer` thật — dữ liệu thô đã nằm sẵn ở `orders.name` / `orders.phone`.
