# Chặn quét xuất kho cho đơn đã hủy — xác minh realtime với KiotViet

Ngày: 2026-07-11
Branch: `feat/scan-cancel-realtime-verify`

## Vấn đề

Khi nhân viên kho quét mã vận đơn, hệ thống kiểm tra trạng thái hủy bằng dữ liệu trong DB.
DB là bản sao trễ của KiotViet (cron sync trạng thái chạy 30 phút/lần), nên một đơn vừa bị hủy
trên KiotViet vài phút trước vẫn hiện là hợp lệ khi quét. Hậu quả: hàng bị xuất đi thật, phải thu hồi.

Khảo sát code cho thấy độ trễ sync chỉ là một trong **ba** lỗ hổng:

1. **Chỉ chặn ở frontend.** `isCancelledOrder()` (`frontend/src/features/inventory/scan/scan-cancelled.ts:7`)
   so `order.status === 6` trên dữ liệu DB do `shipping_lookup_view.py` trả về.
2. **Backend không có guard.** `OrderService.dispatch_orders_batch`
   (`backend/apps/orders/application/services/order_service_commands.py:489`) chỉ ghi warning
   `unexpected_status` với status ngoài `{2, 11}` rồi **vẫn xuất kho**.
3. **Cron khớp mã pending tự xuất kho cho đơn hủy.** `PendingScanService._complete_dispatch`
   (`backend/apps/inventory/application/services/pending_scan_service.py:378`) không kiểm tra hủy:
   mã quét lúc đơn chưa sync về, đơn về sau ở trạng thái CANCELLED, cron 60s vẫn khớp và xuất kho.

Ngoài ra `lookup-by-shipping-code` không trả `invoice_status`, trong khi phần còn lại của repo định
nghĩa đơn hủy là `status = 6 OR invoice_status = 2`
(`backend/apps/orders/repositories.py:171`, `:205-210`, `:548-553`).

## Nguyên tắc thiết kế

DB là bản sao trễ; KiotViet là nguồn sự thật. Không cố làm bản sao nhanh hơn (tăng tần suất cron chỉ
thu hẹp cửa sổ lỗi chứ không đóng được nó), mà **hỏi thẳng nguồn sự thật tại thời điểm quyết định ghi sổ**.

Quyết định đã chốt với người dùng:

- Hậu quả nghiêm trọng (hàng xuất đi thật) → chấp nhận quét chậm thêm 0.5–1s để đúng.
- API KiotViet lỗi/timeout → **fail-closed** (chặn quét).
- Đơn không có nguồn KiotViet (không `invoice_code`/channel) → DB là nguồn sự thật, cho quét.
- Luồng **quét nhập hoàn**: đơn hủy → hàng về kho là đúng nghiệp vụ → **gỡ chặn**.

## Kiến trúc

### Thành phần mới: `InvoiceCancellationVerifier`

Đặt trong `backend/apps/kiotviet/application/services/cancellation_verifier.py`, tái sử dụng
`get_invoice_by_code()` (`backend/apps/kiotviet/infrastructure/services/invoice_sync_queries.py:197`)
— chính hàm mà webhook đã dùng để verify sự kiện hủy (`webhook_impl.py:242`).

Interface (port) khai báo phía `orders`/`inventory` để hai app này không phụ thuộc trực tiếp vào
`kiotviet`; DI nối implementation ở tầng config.

```
verify_not_cancelled(order) -> VerificationResult
```

| Kết quả | Điều kiện | Luồng quét xuất |
|---|---|---|
| `OK` | KiotViet trả hóa đơn còn hiệu lực | Cho quét |
| `CANCELLED` | KiotViet trả `status == 2` hoặc delivery status ∈ {5, 6} | Chặn + ghi ngược trạng thái hủy vào DB |
| `UNVERIFIABLE` | Đơn không có `invoice_code` hoặc không thuộc channel KiotViet | Cho quét (check DB là đủ) |
| raise `VerificationUnavailable` | API lỗi / timeout / rate-limit | Chặn (fail-closed) |

Điều kiện `CANCELLED` dùng đúng bộ điều kiện webhook đang dùng
(`webhook_impl.py:454-471`: `cancelled_delivery_statuses = {5, 6}`, `invoice_status == 2`).

### Self-heal: ghi ngược trạng thái hủy vào DB

Khi verifier trả `CANCELLED` mà DB chưa phản ánh, ghi ngay `Order.status = 6`,
`Order.invoice_status = 2` và publish `OrderStatusChanged` — tái sử dụng đúng đường ghi của webhook
(`webhook_impl.py:473-497`). Sai lệch được sửa tại nơi nó gây hại, không chờ cron.

### Ba chốt chặn

Verify lúc quét là chưa đủ: từ lúc quét tới lúc bấm Xác nhận có thể cách vài phút, và luồng pending
scan ghi sổ hoàn toàn không đi qua lookup.

**Chốt 1 — lúc quét** (`backend/apps/orders/transport/views/shipping_lookup_view.py`)

- Nhận thêm query param `mode=dispatch|return` (mặc định `dispatch` để an toàn).
- `mode=dispatch`: gọi verifier. `CANCELLED` → trả 409 kèm lý do; `VerificationUnavailable` → 503.
- `mode=return`: không verify.
- Bổ sung `invoice_status` vào payload trả về (đang thiếu).

**Chốt 2 — lúc ghi sổ** (`OrderService.dispatch_orders_batch`)

- Guard cứng: `status == 6` hoặc `invoice_status == 2` → **raise**, thay cho warning `unexpected_status` hiện tại.
- Verify realtime lại cả lô, chạy song song (đây là chốt cuối cùng trước khi trừ tồn).

**Chốt 3 — lúc cron khớp mã pending** (`PendingScanService._complete_dispatch`)

- Trước khi gọi `dispatch_orders_batch`, verify đơn.
- `CANCELLED` → chuyển `PendingShipmentScan` sang trạng thái mới `blocked_cancelled`, **không ghi sổ**,
  hiển thị lên bảng pending để người xử lý thủ công.
- `VerificationUnavailable` → giữ nguyên `pending`, thử lại ở vòng cron sau.

### Cache

Redis, key theo `channel_id` + `invoice_code`:

- Kết quả `OK`: TTL 30s (đủ chống gọi lặp khi quét nhiều lần liên tiếp, đủ ngắn để không che sự kiện hủy).
- Kết quả `CANCELLED`: TTL 5 phút.
- **Không cache lỗi.**

### Van an toàn cho fail-closed

Fail-closed nghĩa là KiotViet sập thì kho ngừng xuất hàng. Hai van an toàn:

- **Timeout 3s + retry 1 lần** — lỗi thoáng qua không bắt nhân viên quét lại.
- **Kill-switch** `SCAN_VERIFY_CANCEL_REALTIME` (mặc định `True`). Khi KiotViet sự cố kéo dài, admin tắt cờ
  → tạm quay về check DB. Mọi lần quét trong thời gian tắt cờ đều ghi audit log để đối soát sau.

## Frontend

- `ScanDispatchDialog`: gọi lookup với `mode=dispatch`. Xử lý 3 phản hồi — hợp lệ, 409 (đã hủy, toast đỏ
  như hiện tại), 503 (không xác minh được với KiotViet, toast đỏ, gợi ý thử lại).
- `ScanReturnDialog`: gọi lookup với `mode=return`, **gỡ** `isCancelledOrder` guard.
- `scan-cancelled.ts`: khi còn dùng dữ liệu DB (kill-switch tắt / đơn `UNVERIFIABLE`), check cả
  `status === 6` **và** `invoice_status === 2`.
- Bảng pending scans: hiển thị trạng thái `blocked_cancelled` với nhãn rõ ràng.

## Testing

Backend:

- `InvoiceCancellationVerifier`: 4 nhánh (OK / CANCELLED / UNVERIFIABLE / API lỗi → raise), mock KiotViet client.
- Self-heal: verifier trả CANCELLED → `Order.status`/`invoice_status` được ghi, event được publish.
- `dispatch_orders_batch`: raise với đơn status=6 và đơn invoice_status=2.
- `PendingScanService`: đơn hủy → `blocked_cancelled`, không tạo chứng từ kho; API lỗi → giữ `pending`.
- Cache: hit không gọi lại API; lỗi không được cache.
- Kill-switch tắt → không gọi API, fallback DB check.

Frontend:

- `scan-cancelled.test.ts`: cập nhật cho `invoice_status`.
- `ScanReturnDialog`: đơn hủy vẫn thêm được dòng.
- `ScanDispatchDialog`: 409 và 503 đều chặn, hiện toast đúng.

## Ngoài phạm vi

- Tích hợp Salework (repo hiện **không** có tích hợp nào với Salework).
- Thay đổi tần suất cron sync trạng thái (self-heal đã xử lý đúng chỗ đau).
- Tự động đảo phiếu khi phát hiện hủy sau lúc đã xuất kho.
