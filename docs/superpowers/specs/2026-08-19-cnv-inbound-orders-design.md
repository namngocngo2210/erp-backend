# Thiết kế: kéo đơn từ CNV Loyalty về desi-order (chiều ngược)

*Ngày: 2026-08-19 · Trạng thái: đang thực hiện · Bổ sung cho `2026-08-05-cnv-loyalty-sync-design.md`*

## 1. Bối cảnh

Spec 05/08 làm đồng bộ **một chiều** desi-order → CNV. §Ngoài phạm vi ghi rõ:
*"Chiều ngược CNV → desi-order (webhook/poll từ CNV): chưa có nhu cầu"*. Nay có nhu cầu:
khách đặt hàng trực tiếp trên app loyalty CNV, đơn đó phải về DB của mình.

Đọc lại OpenAPI thật (`https://apis.cnvloyalty.com/swagger/v1/swagger.json`, OpenAPI 3.0.1)
phát hiện CNV có sẵn hạ tầng cho chiều ngược mà spec cũ không biết:

| Khả năng | Endpoint | Ghi chú |
|---|---|---|
| Poll đơn tăng dần | `GET /orders.json?updated_at_from=&updated_at_to=&limit=&page=` | Có phân trang theo `page` |
| Đếm tổng | `GET /orders/count.json` | Biết tồn đọng trước khi quét |
| Tra theo external id | `GET /orders/external/{id}.json` | CNV **index theo `unique_id`** mình gửi lên |
| Webhook đẩy | `GET/POST/DELETE /webhook/register`, `POST/DELETE /webhook/subscribe` | body: `{callback_url, verify_token, topics[]}` |

## 2. Quyết định nghiệp vụ đã chốt

1. **Đơn CNV KHÔNG tính vào doanh thu công ty** (chốt 19/08, có thể đổi sau).
   → xử lý như đơn Foxia: `order_source="cnv"` + thêm `"cnv"` vào `MIRROR_ORDER_SOURCES`.
   Một dòng đó phủ cả hai tầng hàng rào (manager `Order.objects` + `sql_exclude_mirror_orders`).
2. **Không đấu nối nhóm `booking*`** của CNV (đặt lịch/đặt bàn) — không thuộc nghiệp vụ công ty.
3. **Không tạo `OrderItem`** cho đơn CNV — giữ dòng thô ở `CnvOrderLine` (lý do ở §5.3).

## 3. Bẫy lớn nhất: vòng lặp echo

Đơn kéo từ CNV về mà `.save()` bình thường sẽ kích `post_save` ở
`apps/cnv_loyalty/subscribers.py` → **đẩy ngược chính nó lên CNV** → CNV sinh đơn thứ hai.
Dữ liệu đã bay ra hệ thống ngoài thì không rút lại được.

Ba lớp chặn, độc lập nhau:

1. `MIRROR_ORDER_SOURCES` chứa `"cnv"` → guard sẵn có ở `_on_order_saved` return sớm.
2. Đường ingest ghi bằng `bulk_create`/`bulk_update` — Django **không phát `post_save`**.
3. Test `test_ingest_khong_day_nguoc_don_len_cnv` + `test_guard_subscriber_chan_don_cnv_ke_ca_khi_save`
   khẳng định cả hai lớp trên: sau ingest không task `sync_order` nào được enqueue, và
   guard vẫn chặn kể cả khi một đường code tương lai gọi `.save()`.

Cùng lý do đó, ingest **không tạo `Customer` mới** bằng `.save()` (sẽ kích `_on_customer_saved`).

## 4. Chống trùng với đơn CRM đã đẩy lên CNV — 4 tầng

Đơn của mình đẩy lên CNV mang `unique_id = "desi-<order_id>"`
(`domain/mappers.py::build_unique_id`). Khi kéo về phải nhận ra và bỏ:

| Tầng | Cơ chế | Chặn được |
|---|---|---|
| 1 | `unique_id` khớp `^desi-\d+$` → bỏ | Đơn mình đẩy lên, nhận diện tức thì, không tốn query |
| 2 | `cnv_id` đã có trong `CnvSyncRecord(entity_type="order")` → bỏ | Đơn mình đẩy lên mà CNV không echo `unique_id` |
| 3 | `CnvOrderLink.cnv_order_id` UNIQUE, tra trước rồi tạo/cập nhật | Chạy lại/replay webhook bao nhiêu lần cũng không sinh đơn thứ hai |
| 4 | `CnvWebhookEvent` UNIQUE `(topic, cnv_object_id, payload_hash)` | CNV retry cùng một event |

Tầng 1 và 2 là **chống trùng chéo** (đơn CRM ↔ đơn CNV) — yêu cầu gốc.
Tầng 3 và 4 là **idempotency** của chính đường ingest.

> Rủi ro còn lại: swagger khai báo response của mọi endpoint chỉ là `200 Success`, **không có
> schema body**. Việc `GET /orders.json` có echo `unique_id` trong từng phần tử hay không phải
> probe bằng token thật (`manage.py cnv_probe --orders`). Nếu KHÔNG echo, tầng 1 sập và tầng 2
> gánh — nên tầng 2 là bắt buộc, không phải tuỳ chọn.

## 5. Mô hình dữ liệu

### 5.1 `CnvOrderLink` (1-1 với `orders`)
`cnv_order_id` **UNIQUE** — khoá idempotent của toàn bộ đồng bộ, y hệt vai trò `foxia_id`
ở `FoxiaOrderLink`. Giữ các trường CNV không có chỗ trong `Order`: `unique_id`,
`financial_status`, `fulfillment_status`, `cancelled_status`, `location_id`,
`cnv_customer_id`, `cnv_created_at`, `cnv_updated_at`.

### 5.2 `CnvInboundCursor`
Mốc `synced_until` cho poll tăng dần (mô phỏng `FoxiaSyncCursor`). Poll là **lưới vá**
cho webhook, không phải đường chính.

### 5.3 `CnvOrderLine`
Dòng sản phẩm giữ nguyên trạng, **không** tạo `OrderItem`: `OrderItem.product` là FK
`PROTECT NOT NULL`, mà đơn CNV có thể chứa sản phẩm không nằm trong danh mục nội bộ.
Khác Foxia ở chỗ CNV **có trả `sku`** → lưu lại, sau này muốn promote sang `OrderItem` thì
đủ dữ kiện, không phải kéo lại từ CNV. Tên sản phẩm gộp vào `Order.product` để tra cứu
không cần join (giống `_write_product_summary` của Foxia).

### 5.4 `CnvWebhookEvent`
Lưu payload thô + `processed`, đúng pattern `BestWebhookEvent`: trả `200` ngay cho CNV,
xử lý sau; lỗi áp event thì cron vớt lại chứ không để CNV retry vô hạn.

### 5.5 Khách hàng — quyết định khác Foxia
Foxia dùng sentinel vì API không trả PII. CNV **có** trả `customer.phone` + tên. Nên:

- Khớp `phone_normalized` với `Customer` có sẵn → link vào khách thật (**chỉ đọc**, không ghi).
- Không khớp → dùng **một** sentinel dùng chung `CNV-UNKNOWN`, còn tên/SĐT thô ghi thẳng vào
  `Order.name` / `Order.phone`.

Vì sao không tạo `Customer` mới: (a) kích signal đẩy ngược lên CNV, (b) phình bảng
`customers` và bẩn phép đếm "khách mới". Dữ liệu thô vẫn còn nguyên trên `Order` + link →
ngày nào chốt tính doanh thu CNV thì promote được, không mất gì.

### 5.6 Trạng thái
Ghi nguyên văn vào `Order.partner_delivery_status` — cột này cố ý đặt tên `partner_*` chứ
không phải `foxia_*`, "chỗ chứa chung cho mọi hệ thống đối tác đổ đơn vào". `Order.status`
để `NEW`, **không map** (chưa có bản chốt mã trạng thái CNV; đoán sai thì đơn hiện sai
trạng thái mà không ai biết vì dữ liệu gốc đã bị nuốt).

## 6. Luồng

```
CNV (đơn mới/đổi)
   │ webhook POST /api/cnv/webhook   (Authorization: Token <verify_token>)
   ▼
webhook_views  ──► store_event (dedupe tầng 4) ──► 200 OK ngay
                        │
                        └─► apply_event ──► CnvOrderIngestService
                                                 │ dedup tầng 1,2
                                                 │ upsert tầng 3 (bulk_*, không post_save)
                                                 ▼
                                          orders(order_source="cnv") + cnv_order_links

cron poll_inbound_orders (lưới vá, 15')
   └─► GET /orders.json?updated_at_from=cursor ──► cùng CnvOrderIngestService
```

Một điểm ingest duy nhất cho cả hai đường — cùng lý do `webhook_service` của Best Express
dùng chung `apply_event` cho webhook lẫn TraceQuery sync.

## 7. Webhook: đăng ký thế nào

`POST /webhook/register` body `{callback_url, verify_token, topics[]}`.
**Swagger không liệt kê giá trị `topics` hợp lệ** (chỉ khai `array<string>`), tài liệu công
khai của CNV cũng không có. Do đó:

- Không hard-code danh sách topic trong code. `topics` truyền từ CLI/env
  (`CNV_WEBHOOK_TOPICS`, mặc định `orders/create,orders/updated`).
- Lệnh `manage.py cnv_webhook list` gọi `GET /webhook/register` để xem CNV đang nhận gì —
  đây là cách duy nhất biết chắc tên topic. Chạy trước khi register.
- Receiver **topic-agnostic**: nhận diện theo hình dạng payload (`order`/`orders` key,
  hoặc object có `line_items`), không dựa vào tên topic. Topic lạ vẫn lưu event, đánh
  `processed=False` và log — không nuốt im lặng.

`verify_token` do mình sinh, lưu ở `CNV_WEBHOOK_VERIFY_TOKEN`; receiver so sánh bằng
`constant_time_compare`.

> **Chưa register được ở thời điểm viết**: `callback_url` phải là URL public HTTPS mà CNV
> gọi tới được — chỉ tồn tại sau khi deploy prod. Trình tự bắt buộc: deploy → `cnv_webhook list`
> → `cnv_webhook register` → bật `CNV_INBOUND_ENABLED=1`.

## 8. Đấu nối các API còn thiếu

Gap analysis: swagger có 63 endpoint chưa có trong `cnv_client.py`. **Không đấu nối hết** —
đấu nối cái không dùng là nợ kỹ thuật trả bằng công review và test. Chọn theo giá trị:

**Đấu nối (Sprint 4)**
- Dedup/inbound: `GET /orders/external/{id}.json`, `GET /orders/count.json`, `PUT /orders/order-status/{id}.json`
- Lấp nợ: `GET /customers/get-by-phone.json`, `PUT /customers/{id}.json`, `GET /customers/external/{id}.json`, `GET /products/external/{id}.json`, `GET /custom_collections/external/{id}.json`
- Đếm: `GET /customers/count.json`, `GET /products/count.json`, `GET /collects/count.json`, `GET /custom_collections/count.json`, `GET /smart_collections/count.json`
- Vận hành: `GET /health-check`, `GET /locations.json`, `GET /sale-channels.json`
- Webhook: `GET/POST/DELETE /webhook/register`, `POST/DELETE /webhook/subscribe`
- Loyalty (giá trị CSKH cao, dùng ngay được ở màn chi tiết khách):
  `GET /loyalty/customers/{id}/membership.json`, `.../membership/histories.json`,
  `GET /loyalty/membership-levels.json`, `POST /loyalty/customers/{id}/points.json`,
  `POST /loyalty/adjust-customer-membership-external.json`
- Bổ sung CRUD còn thiếu của nhóm đã dùng: `PUT/DELETE /custom_collections/{id}.json`,
  `GET /collects.json`, `DELETE /collects/{id}.json`, `POST /customer/{id}/tags.json`

**KHÔNG đấu nối** — nêu rõ để lần sau không phải hỏi lại
- Toàn bộ `booking*` (13 endpoint): CNV bán kèm module đặt lịch, công ty không dùng.
- `POST /collection-topping.json`, `ToppingOrder*`: nghiệp vụ F&B (topping), không áp dụng.
- `PUT /customers/update-physical-card/{id}.json`, `update-signature`: chưa phát hành thẻ cứng.
- `GET /topup/customers/{id}/balance.json`, `POST /use-discount-code`: ví/mã giảm giá do CNV
  quản lý, mình chưa có luồng nghiệp vụ tiêu thụ. Đấu nối khi có yêu cầu thật.
- `GET/POST /locations` phần ghi (`POST/PUT/DELETE`): chi nhánh khai trên CNV, mình chỉ đọc.

## 9. Hai chỗ code hiện tại ghi chú SAI so với swagger (trả nợ trong Sprint 4)

1. `infrastructure/cnv_client.py::update_order` ghi *"Tài liệu CNV không mô tả PUT /orders"* —
   swagger **có** `PUT /orders/{id}.json`, và có thêm `PUT /orders/order-status/{id}.json`.
2. `application/services/sync_service.py::sync_customer` ghi *"CNV KHÔNG cho lọc
   /customers.json theo SĐT (probe prod 2026-08-06)"* → khách đã tồn tại bên CNV bị đánh dấu
   `synced` với `cnv_id = None`. Swagger có endpoint riêng **`GET /customers/get-by-phone.json`**.
   Probe hồi 06/08 thử filter trên `/customers.json` nên trượt. Vá chỗ này lấp được lỗ hổng
   khách thiếu `cnv_id` — mà `cnv_id` chính là tầng dedup số 2 ở §4.

## 10. Cấu hình mới

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `CNV_INBOUND_ENABLED` | `0` | Bật ingest (webhook + poll). Tách khỏi `CNV_SYNC_ENABLED` để bật/tắt độc lập hai chiều |
| `CNV_WEBHOOK_VERIFY_TOKEN` | `""` | Token CNV gửi kèm; rỗng = từ chối mọi webhook |
| `CNV_WEBHOOK_CALLBACK_URL` | `""` | URL public để đăng ký với CNV |
| `CNV_WEBHOOK_TOPICS` | `orders/create,orders/updated` | Danh sách topic đăng ký, sửa được sau khi biết tên thật |
| `CNV_INBOUND_LOOKBACK_MINUTES` | `120` | Poll lùi lại bấy nhiêu phút để bù đơn về muộn |

Cập nhật kèm ở `docker-compose.prod.yml`, `docker-compose.local.yml`, `.env.example`.

## 11. Sprint

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| 1 | `OrderSource.CNV` + `MIRROR_ORDER_SOURCES`, 4 model mới, migration, sentinel | ✅ 19/08 |
| 2 | Webhook: client methods, receiver, service, command `cnv_webhook` | ✅ 19/08 |
| 3 | `CnvOrderIngestService` + poll cron + beat | ✅ 19/08 |
| 4 | Đấu nối API còn thiếu + trả 2 nợ §9 | ✅ 19/08 |
| 5 | Test suite + runbook triển khai | ✅ 19/08 |

### Đã làm

- 4 model + migration `cnv_loyalty/0003`, `orders/0130` (`makemigrations --check` sạch).
- Ingest dùng chung cho webhook + poll, 4 tầng chống trùng, ghi bằng `bulk_*` để
  không phát `post_save`.
- Endpoint `POST /api/cnv/webhook` (token 3 chỗ đặt), `CnvWebhookEvent` + cron
  retry/purge, lệnh `manage.py cnv_webhook list|register|delete`.
- 30 method client mới; sửa 2 ghi chú sai ở §9; khách trùng SĐT nay lấy lại được
  `cnv_id` qua `/customers/get-by-phone.json`.
- Runbook: `docs/cnv-inbound-orders-runbook.md`.
- Kiểm chứng: `apps/cnv_loyalty` 124 passed · `apps/foxia` + `test_celery_routes`
  100 passed · ruff sạch · import-linter `App layering KEPT`.

### CHƯA làm (không làm được ở máy dev)

- **Chưa register webhook với CNV.** Cần URL public HTTPS + token thật, chỉ tồn tại
  sau khi deploy prod. Trình tự ở runbook §3-5.
- **Chưa xác minh tên `topics` thật** — mặc định `orders/create,orders/updated` là
  giá trị CHƯA kiểm chứng, sửa bằng env sau khi chạy `cnv_webhook list`.
- **Chưa probe response `GET /orders.json`** bằng token thật → chưa biết CNV có echo
  `unique_id` không. Tầng dedup 2 đã thiết kế để gánh một mình nếu không.
- `schema.yaml` KHÔNG regen: `OrderSourceEnum` trong file đó thiếu sẵn cả `foxia`
  (stale từ trước) — regen ở MR này sẽ kéo theo nhiễu của feature khác, trả nợ
  bằng MR chore riêng.

## 12. Rủi ro

- **Chưa biết tên topic thật** → phải `cnv_webhook list` trên prod trước khi register. Đã thiết
  kế để không chặn: poll cron chạy được độc lập với webhook.
- **Không biết schema response** → parser phải chịu được cả `{"orders": [...]}`, `{"data": [...]}`
  và mảng trần; thiếu field thì bỏ qua field đó chứ không văng cả lô.
- **Migration đổi `OrderSource.choices`** chỉ là `AlterField` (choices không đụng schema DB),
  nhưng `MIRROR_ORDER_SOURCES` đổi hành vi `Order.objects` **ngay khi roll code** — đơn CNV
  chưa tồn tại nên tác động thực tế bằng không. Vẫn phải migrate trước, roll code sau.
