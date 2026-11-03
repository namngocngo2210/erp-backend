# KShip create-order timeout: vận đơn tạo thật nhưng hệ thống không ghi được mã

Sự cố 2026-08-18, đơn 554748 (GHN, channel 4): `POST shipping.kiotapi.com/api/v3/create-order/GHN`
`ReadTimeout (read timeout=30)`. Vận đơn ĐÃ được tạo và hiện trên KiotViet, nhưng
`Order.shipping_code` trong DB vẫn rỗng và job `create_kship_delivery` báo `failed`.

## Nguyên nhân gốc

`ReadTimeout` nghĩa là request đã bay đi và server đã xử lý, chỉ có phản hồi về không
kịp. Code cũ (`gateway_kship_customer_webhook.create_kship_order`) nuốt mọi exception
thành `{"success": False, "error": str(exc)}` — **giống hệt** lỗi nghiệp vụ (KShip từ
chối). Không có bước đọc lại (reconcile) nào, nên mã vận đơn thật nằm bên hãng vận
chuyển mà hệ thống không hay biết.

Nặng hơn: KShip không có khoá chống trùng (`payload["UUID"]` luôn rỗng) và cả API lẫn
FE đều **không** kiểm `order.shipping_code` trước khi tạo. User thấy `failed` → bấm tạo
lại → **vận đơn thật thứ hai**, mất phí thật, mã cũ mồ côi vì `Order` chỉ giữ được một
`shipping_code`.

Cùng lớp lỗi với sự cố `push` 2026-07-14 ("hoá đơn ĐÃ tạo bên KiotViet nhưng CHƯA ghi
invoice_code" → hoá đơn trùng) — lần đó chỉ nới `soft_time_limit`, không xử lý gốc.

## Cách xử lý

| Lớp | Thay đổi |
|---|---|
| `create_kship_order` | Timeout / ConnectionError / HTTP 502-503-504 → trả thêm `indeterminate: True`. Lỗi nghiệp vụ (4xx có message) giữ nguyên, KHÔNG đánh dấu. |
| `create_kship_order` | Timeout 30s → 60s (`KSHIP_CREATE_ORDER_TIMEOUT_SECONDS`, override được qua settings `KIOTVIET_KSHIP_CREATE_ORDER_TIMEOUT_SECONDS`). |
| `service_actions._reconcile_kship_shipping_code` | Gặp `indeterminate` thì đọc lại hoá đơn KiotViet lấy `invoiceDelivery.deliveryCode` — 3 lần, cách nhau 5s, mỗi lần timeout 15s. Dò ra → ghi `shipping_code`, job **SUCCESS** (`payload.recovered_by = "reconcile_after_indeterminate"`). |
| `service_actions` | Dò không ra → job FAILED nhưng `payload.indeterminate = True` và thông điệp cảnh báo **đừng bấm tạo lại**, hướng dẫn dùng "Đồng bộ mã vận đơn". |
| `POST /api/orders/kship-delivery` | Trả 400 + `orders_with_shipping_code` nếu bất kỳ đơn nào trong lô đã có `shipping_code`. Chặn **cả lô**, không lọc bớt. |
| FE `use-kship-delivery` | Không mở dialog khi đơn đã có mã; toast nêu rõ id. |
| `process_kiotviet_job` | `soft_time_limit` 120→180, `time_limit` 150→210. Ngân sách xấu nhất: bước 1 ≤30s + create-order 60s + reconcile ≤55s = 145s. |

Reconcile là **đọc sự thật từ KiotViet**, không phải suy đoán: hễ hoá đơn có
`deliveryCode` thì đó đúng là mã vận đơn của đơn đó — cùng đúng một luật mà action
`sync_shipping` vẫn dùng (giờ hai đường dùng chung `_extract_invoice_delivery_code`).

## Còn nợ

- **Backfill đơn cũ.** Các đơn đã dính lỗi trước khi deploy vẫn trắng `shipping_code`.
  Lọc ra rồi chạy action "Đồng bộ mã vận đơn":

  ```sql
  SELECT o.id, o.invoice_code, j.finished_at, j.error_message
  FROM orders_order o
  JOIN orders_orderkiotvietjob j ON j.order_id = o.id
  WHERE j.action = 'create_kship_delivery'
    AND j.status = 'failed'
    AND j.error_message ILIKE '%timed out%'
    AND COALESCE(o.shipping_code, '') = ''
    AND COALESCE(o.invoice_code, '') <> ''
    AND o.deleted_at IS NULL
  ORDER BY j.finished_at DESC;
  ```

- **`shipping_fee` không được ghi khi cứu bằng reconcile** — hoá đơn KiotViet không trả
  `TOTAL_FEE` của KShip. Cron `reconcile_paired_channels` / đồng bộ hoá đơn sẽ phủ sau.
- **Chặn tạo trùng là chặn cứng.** Nếu nghiệp vụ phát sinh ca cần tạo lại vận đơn (vận
  đơn cũ bị huỷ bên hãng), phải mở thêm đường xoá `shipping_code` có kiểm soát.
- **`payload["UUID"]` vẫn rỗng.** Nếu KShip thực sự coi đây là idempotency key thì gửi
  UUID ổn định theo đơn sẽ diệt tận gốc khả năng trùng — cần hỏi KiotViet để xác minh,
  chưa kiểm chứng được.
