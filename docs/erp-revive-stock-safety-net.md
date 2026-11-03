---
title: Lưới an toàn cho tồn kho khi đơn huỷ được hồi sinh
date: 2026-07-23
status: đang làm
---

## 1. Bối cảnh — sự cố thật

Ngày 23/07/2026, khi truy vấn đơn "đã xuất kho mà sổ tồn trống", tìm ra 2 đơn sai lệch tồn:

| id | order_code | shipping_code | status | invoice_status | net |
|---|---|---|---|---|---|
| 547605 | DH012771 | GYA68XXP | 2 (đang giao) | 3 (hoá đơn đang xử lý) | 0 |
| 547574 | DH012770 | GYA68XKB | 2 (đang giao) | 3 | 0 |

Cả hai: xuất kho (`SALE -1`) → bị huỷ, hoàn tồn (`SALE_REVERSAL +1`, note `Hoàn tồn do huỷ đơn`)
→ **được đẩy lại sang KiotViet thành hoá đơn mới** → sống lại, hàng vẫn đang trên đường giao,
nhưng **không có bút toán trừ lại**. Tồn kho phồng 2 đơn vị.

Đã ghi bù thủ công bằng `StockSalesService.reissue_after_cancel_release` (idempotent) lúc
2026-07-23 09:34 UTC. Đây là 2 đơn duy nhất trong toàn bộ lịch sử dính lỗi này.

### Vì sao không truy được nguyên nhân chính xác

Nhánh hồi sinh ([`inventory/subscribers.py`](../backend/apps/inventory/subscribers.py)) chỉ chạy
khi nhận `OrderStatusChanged` có `old_status = 6`. Dấu vết duy nhất chứng minh nó có chạy hay
không là **một dòng log**, mà log worker đã mất khi container bị recreate lúc deploy cùng ngày.
Đọc lại 8 điểm publish `OrderStatusChanged` thì tất cả đều bắt `previous_status` đúng cách.

Kết luận: **không pin được điểm hụt, và không còn cách nào pin được.** Thay vì đoán tiếp, làm
cho lần sau tự lộ ra và tự được bắt.

### Một lỗ CÓ chứng minh được

Audit log cho thấy cả 6 bản ghi quanh thời điểm hồi sinh đều là `KIOTVIET_REQUEST / POST` —
đường **đẩy đơn**, không phải cron đọc trạng thái. Trong
[`service_actions.execute_action`](../backend/apps/kiotviet/infrastructure/services/service_actions.py)
nhánh `ACTION_PUSH`, khối cập nhật `Order.status` nằm sau guard `if invoice_status is not None`,
mà `invoice_status` lấy từ `invoiceDelivery.status`. Hoá đơn vừa tạo chưa có vận đơn → `None`
→ **`Order.status` không đổi**, trong khi `order.invoice_status` vẫn được ghi (= 3).

Hệ quả: đơn vừa được đẩy lại **vẫn mang `status = 6` (Đã huỷ)** trong DB cho tới khi cron 30'
bắt kịp. Trong cửa sổ đó lookup mã vận đơn trả 409 "Đơn đã hủy" → kho không xuất được đơn vừa
đẩy lại. Và cú lật `6 → x` bị dời sang đường khác, làm mắt xích kích hoạt trừ lại tồn không
chạy đúng chỗ nó nên chạy.

**Không sửa bằng cách suy `Order.status` từ `invoice.status`.** `_resolve_invoice_status` trả
`None` cho hoá đơn "đang xử lý" là **cố ý** (audit 2026-07-14 vòng 4): suy bừa thì hoá đơn
trạng thái 3 ("đang xử lý") thành đơn trạng thái 3 ("Đã giao") — hai thang số khác nhau.

## 2. Phạm vi

Ba việc, độc lập nhau, xếp theo giá trị giảm dần:

| # | Việc | Phụ thuộc root cause? |
|---|---|---|
| 1 | Cron đối soát hàng ngày → cảnh báo Lark | Không — bắt cả lỗ chưa biết |
| 2 | Ghi `OrderJourneyEvent` mỗi lần `Order.status` đổi | Không — xoá điểm mù cho lần sau |
| 3 | Push: đơn `CANCELLED` + hoá đơn sống → rời `CANCELLED` | Có — sửa lỗ đã chứng minh |

### 2.1 Cron đối soát (mục 1)

Điều kiện một đơn bị coi là lệch:

- Có `SALE_REVERSAL` note `Hoàn tồn do huỷ đơn` (đã hoàn tồn vì huỷ), **và**
- Hiện `status <> 6` và chưa xoá mềm (đã hồi sinh), **và**
- Không có `SALE` note `Trừ lại tồn do đơn huỷ được hồi sinh` (chưa ai bù).

Chạy 03:50 hàng ngày (sau các job retention 03:15–03:45, tránh chồng). Có kết quả → bắn Lark
kèm danh sách id + mã đơn. **Chỉ cảnh báo, không tự ghi sổ**: tự động trừ tồn dựa trên suy
đoán là đúng loại rủi ro đã tạo ra sự cố này; để người xác nhận rồi chạy
`reissue_after_cancel_release` (idempotent, an toàn gọi lại).

### 2.2 Journey cho `Order.status` (mục 2)

Hiện `OrderJourneyEvent` chỉ ghi các mã CRM (`sales_status_changed`,
`delivery_business_status_changed`, …) — **không ghi `Order.status`**. Đó chính là lý do buổi
điều tra này mù: biết đơn từng bị huỷ (qua sổ tồn) nhưng không biết lúc nào, đường nào lật nó
về.

Cách làm: subscriber trong app `orders` nghe `OrderStatusChanged` → ghi 1 journey event
`order_status_changed` với `from_code`/`to_code` là số trạng thái. **Một chỗ duy nhất**, phủ
mọi publisher, thay vì sửa 8 điểm publish.

Giới hạn đã biết: đường nào **không** phát sự kiện thì journey cũng không ghi. Mục 1 là lưới
hứng cho đúng ca đó.

Dung lượng: chỉ ghi khi trạng thái THẬT SỰ đổi (publisher đã tự lọc, subscriber lọc lần nữa
cho chắc), nên số dòng ≈ số lần lật trạng thái thật — không phải mỗi tick cron 30'.
`purge_old_journey_events` (03:45) đã sẵn sàng nhưng mặc định retention = 0 nên **chưa xoá
gì**; nếu bảng phình thì bật env retention là đủ, không cần sửa code.

### 2.3 Push rời `CANCELLED` (mục 3)

Trong `ACTION_PUSH`, sau khi đọc hoá đơn: nếu `order.status == CANCELLED` **và** hoá đơn mới
không phải hoá đơn huỷ (`invoice_own_status != 2`) → đặt `status = PENDING_PROCESSING (1)` và
phát `OrderStatusChanged(old=6, new=1)`.

Cơ sở: có bằng chứng dương đơn đang sống (vừa tạo hoá đơn mới, không huỷ) → không phải đoán.
Nghiệp vụ xác nhận: đẩy hoá đơn xong đơn ở trạng thái "chờ xử lý". Cron 30' vẫn tinh chỉnh
tiếp sang 2/3 khi có vận đơn.

Chỉ đụng đơn **đang ở `CANCELLED`** — đơn bình thường không đổi hành vi.

## 3. Sprint

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| S1 | Mục 2 — journey cho `Order.status` | ✅ Xong |
| S2 | Mục 3 — push rời `CANCELLED` | ✅ Xong |
| S3 | Mục 1 — cron đối soát + Lark | ✅ Xong |

### Ghi chú sau sprint

- **S1**: subscriber `apps/orders/subscribers.py` (mới) + đăng ký ở `OrdersConfig.ready()`.
  Ghi `event_type="order_status_changed"`, `from_code`/`to_code` = số trạng thái dạng chuỗi,
  `payload_json` mang nhãn tiếng Việt để đọc không cần tra bảng. Handler nuốt lỗi ghi journey
  (bus đã cô lập handler, nhưng journey hỏng tuyệt đối không được chặn luồng nghiệp vụ).
- **S2**: chỉ thêm một nhánh trong `ACTION_PUSH`; giữ nguyên toàn bộ đường suy
  `invoice_status` sẵn có. Không đụng `_resolve_invoice_status`.
- **S3**: `RevivedStockAuditService.find_unreissued()` + task
  `apps.inventory.tasks.audit_revived_orders_stock`, beat 03:50. Trả danh sách rỗng → không
  bắn Lark (không tạo nhiễu).

### Chưa làm / cố ý bỏ

- **Không** tự động ghi sổ bù trong cron — chỉ cảnh báo (lý do ở §2.1).
- **Không** đụng `_resolve_invoice_status`: hành vi trả `None` cho hoá đơn "đang xử lý" là cố ý.
- Không truy được điểm publish làm hụt sự kiện hồi sinh của 2 đơn gốc — log đã mất. Mục 1+2
  bảo đảm lần sau có đủ dữ kiện.
