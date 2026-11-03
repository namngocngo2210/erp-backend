# Runbook — RED metrics tầng ứng dụng + alert về Lark

> Audit tải 2026-07-24. Không có thay đổi phá vỡ; toàn bộ nằm dưới profile `monitoring`.

## Vấn đề đang giải

Trước thay đổi này, hệ thống đo được **hạ tầng** (CPU/RAM/disk/container/Postgres) nhưng
mù hoàn toàn ở tầng **ứng dụng**:

- `apps/orders/metrics.py` chỉ có **5 metric**, cả 5 đều riêng cho landing-ingest.
- Cả 17 alert đều là hạ tầng — **không alert nào về độ trễ hay tỉ lệ lỗi của endpoint**.

Hệ quả: người dùng kêu "hệ thống chậm" thì không có cách nào biết endpoint nào chậm.
Đúng tình huống đã lặp lại ở `report-precompute-timeout` và `resale-tracking-performance`.

Và một lỗi nghiêm trọng hơn: **đường gửi alert về Lark chưa từng hoạt động được.**

## Lỗi đường gửi Lark (quan trọng)

`alertmanager.yml` trước đây trỏ `webhook_configs` **thẳng** vào bot Lark (qua sed thay
`LARK_ALERT_WEBHOOK_URL` lúc container start). Cách này không thể chạy:

| | Định dạng |
|---|---|
| Alertmanager **gửi** | `{"version":"4","status":"firing","alerts":[…]}` |
| Lark custom bot **nhận** | `{"msg_type":"text","content":{"text":"…"}}` |

Lark từ chối payload, và vì Alertmanager không kiểm tra body trả về nên **alert biến mất
im lặng**. Đây là chế độ hỏng tệ nhất của một hệ thống cảnh báo: tưởng là có, thực ra không.

Alertmanager v0.27 **không** template được body webhook → bắt buộc có lớp chuyển đổi.

**Cách sửa:** thêm service `lark-alert-adapter` (`monitoring/lark_alert_adapter.py`,
chỉ dùng thư viện chuẩn, chạy trên `python:3.13-slim`, không cần build image).
`alertmanager.yml` giờ trỏ cố định vào `http://lark-alert-adapter:9095`; URL bot thật
nằm ở env của adapter.

Adapter cũng kiểm tra `code` trong body Lark trả về — Lark trả **HTTP 200 kể cả khi từ
chối message**, không đọc body thì mọi lỗi cấu hình đều trông như thành công.

## Metric mới

Nguồn: `apps/common/http_metrics.py` (middleware, đặt ngay sau `RequestIdMiddleware`).

| Metric | Ý nghĩa |
|---|---|
| `desi_http_request_duration_seconds` | Histogram thời gian xử lý, label `method`/`route`/`status` |
| `desi_http_requests_total` | Counter số request, cùng bộ label |

**Cardinality**: label `route` dùng **URL pattern** (`^api/orders/?<int:order_id>`), không
dùng path thật. Dùng path thật thì mỗi đơn hàng đẻ một time series — Prometheus sập trước
hệ thống. URL không khớp route nào gộp vào `<unmatched>` để bot quét không sinh series mới.
Có test khoá điều này (`apps/common/tests/test_http_metrics.py`).

## Alert mới

| Alert | Ngưỡng | Mức |
|---|---|---|
| `HttpEndpointSlow` | p95 > 2s trong 10' cho MỘT route | warning |
| `HttpEndpointVerySlow` | p95 > 10s trong 5' | critical |
| `HttpEndpointErrorRate` | 5xx > 5% trong 10' | critical |
| `HttpEndpointDown` | 5xx = 100% trong 5' | critical |

Ngưỡng đặt **theo từng route**, không đặt toàn cục — trung bình toàn hệ thống luôn đẹp vì
endpoint nhẹ áp đảo về số lượng, đúng lý do report chậm 2 tiếng vẫn không kích hoạt alert nào.

## Triển khai

1. Điền `LARK_ALERT_WEBHOOK_URL` vào `.env` prod (**không commit** — `.env` đã trong gitignore).

2. Khởi động adapter + nạp lại alertmanager & prometheus:
   ```bash
   cd /path/to/desi-order && git pull
   docker compose -f docker-compose.prod.yml --profile monitoring up -d \
     --no-deps lark-alert-adapter alertmanager
   docker compose -f docker-compose.prod.yml --profile monitoring \
     exec prometheus kill -HUP 1     # nạp lại alerts.yml
   ```

3. Restart web để middleware metrics có hiệu lực:
   ```bash
   docker compose -f docker-compose.prod.yml up -d --no-deps web
   ```

4. Xác nhận metric đã chảy (chờ ~1 phút có traffic):
   ```bash
   curl -s localhost:8000/api/orders/metrics | grep desi_http_requests_total | head
   ```

5. **Bắn thử một alert thật** — bước này không được bỏ, vì cả đường dẫn trước đây đứt mà
   không ai biết:
   ```bash
   docker exec desi-alertmanager amtool alert add \
     alertname=TestLarkDelivery severity=warning \
     --annotation=summary="Kiểm tra đường gửi Lark" \
     --alertmanager.url=http://localhost:9093
   docker logs --tail 20 desi-lark-alert-adapter
   ```
   Log phải hiện `[OK] đã gửi Lark` và tin nhắn phải xuất hiện trong nhóm Lark.
   Thấy `[ERROR] Lark từ chối message` → kiểm tra lại URL bot.
   Thấy `[NOOP]` → env `LARK_ALERT_WEBHOOK_URL` chưa tới được container.

## Chi phí

Middleware thêm ~vài chục µs mỗi request (2 phép observe/inc trong bộ nhớ). Adapter giới
hạn 128MB RAM. Số time series ước tính: ~215 route × vài status × 10 bucket — nằm thoải mái
trong khả năng của Prometheus hiện tại.

## Rollback

Gỡ `apps.common.http_metrics.HttpMetricsMiddleware` khỏi `MIDDLEWARE` rồi restart web.
Alert mới nằm riêng trong group `http-app` của `alerts.yml`, xoá group đó là xong.
