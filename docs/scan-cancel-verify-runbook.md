# Runbook: Xác minh realtime "đơn đã hủy" khi quét XUẤT KHO

Đối tượng đọc: Ops / quản lý kho / trực hệ thống.
Liên quan: `docs/erp-inventory-scan-shipment.md`, `docs/erp-inventory-pending-scan.md`.

## 1. Vấn đề và cách xử lý

DB của hệ thống lấy đơn từ KiotViet qua cron, độ trễ có thể tới **30 phút**. Trong cửa sổ
đó, một đơn đã bị hủy trên KiotViet vẫn hiện "bình thường" trong DB → kho quét mã và xuất
hàng đi mất.

Từ nay, **mọi đường xuất kho** đều hỏi thẳng KiotViet (nguồn sự thật) xem hóa đơn còn sống
không, trước khi trừ tồn:

| Đường xuất kho | Nơi chặn |
| --- | --- |
| 1. Quét mã trong dialog "Quét xuất kho" (lúc tra mã) | `GET /api/orders/lookup-by-shipping-code?mode=dispatch` |
| 2. Bấm nút **Xác nhận xuất kho** (xuất hàng loạt các mã đã quét) | `dispatch_orders_batch` |
| 3. Đổi dropdown **"Đã xuất kho"** trên trang Vận đơn / Đơn hàng (PATCH inline) | `verify_dispatch_intent` + guard trong `update_order_inline` |

Đơn bị hủy (KiotViet `status = 2`, hoặc `invoiceDelivery.status` = 5 trả hàng / 6 hủy giao)
sẽ **bị chặn ở cả 3 đường**, và hệ thống **tự ghi ngược trạng thái hủy vào DB** (self-heal:
`Order.status`, `invoice_status`) rồi bắn realtime cho FE — nên đơn đó sẽ hiện "Đã hủy" ngay,
không cần chờ cron.

**Nhập hoàn KHÔNG bị chặn ở khâu quét**: tab *Nhập hoàn* không hỏi KiotViet, đơn đã hủy vẫn
tra được mã và vẫn quét được — đây là chủ đích (kho phải đối chiếu được kiện hàng thật).

⚠️ **Nhưng đơn đã HỦY thì không cần phiếu nhập hoàn**: khi đơn chuyển sang trạng thái hủy,
hệ thống **đã tự cộng lại tồn** (`release_for_order`). Nếu ghi sổ thêm một phiếu trả nữa thì
**tồn bị cộng hai lần**. Vì vậy phiếu trả cho đơn hủy bị chặn khi ghi sổ, với thông báo:

> `Đơn <mã đơn> đã hủy nên tồn kho đã được tự động hoàn lại — không cần nhập hoàn phiếu này.`

Kho thấy câu này thì **không phải lỗi hệ thống**: hàng cứ nhập về vị trí, tồn đã đúng rồi.
Áp dụng cho cả đơn hủy **chưa từng xuất kho** (hàng chưa bao giờ rời kho → cộng tồn ở đây sẽ
là tồn ẢO).

## 2. Fail-closed: khi KiotViet lỗi thì kho thấy gì

Nguyên tắc: **không xác minh được ⇒ không cho xuất** (thà chặn nhầm còn hơn xuất hàng đơn đã hủy).

Triệu chứng khi KiotViet timeout / 5xx / mạng đứt (toast đỏ + tiếng bíp lỗi):

- Lúc **quét mã** (mã không được thêm vào danh sách):
  > `Chưa xác minh được đơn của mã <mã vận đơn> với KiotViet — chưa cho xuất kho. Thử quét lại.`
- Lúc bấm **Xác nhận xuất kho** (toast `N đơn không xuất kho được`, mỗi đơn một dòng):
  > `<mã đơn>: Chưa xác minh được với KiotViet — chưa cho xuất kho, thử lại`
- Lúc đổi dropdown **"Đã xuất kho"** (trang Vận đơn / Đơn hàng):
  > `Chưa xác minh được trạng thái đơn với KiotViet nên tạm thời không thể đánh dấu Đã xuất kho. Vui lòng thử lại sau ít phút.`

Khi đơn **thật sự đã hủy** (không phải lỗi hạ tầng), câu hiện ra là:

- Quét: `Đơn <mã đơn> đã hủy — không thể xuất kho.`
- Xác nhận xuất kho: `<mã đơn>: Đơn đã hủy — không thể xuất kho`
- Dropdown: `Đơn đã hủy nên không thể đánh dấu Đã xuất kho.`

Hai loại thông báo này khác nhau về bản chất: **"đã hủy" = đừng xuất nữa**; **"chưa xác minh
được" = lỗi tạm, quét lại sau ít phút** (hoặc gọi ops nếu lặp lại liên tục).

Ngoại lệ hợp lệ (không chặn): đơn **không có hóa đơn KiotViet** (đơn gửi ngoài / đơn tự tạo)
hoặc KiotViet trả **404** cho `invoice_code` → hệ thống không thể xác minh, quay về tin DB
(`status = 6` hoặc `invoice_status = 2`).

## 3. Kill-switch khi KiotViet sự cố kéo dài

Nếu KiotViet chết lâu và kho đứng hình (mọi mã đều báo "Chưa xác minh được"):

```bash
# backend/.env
SCAN_VERIFY_CANCEL_REALTIME=0
```

rồi **restart cả `web` và `worker`** (worker chạy cron khớp mã pending):

```bash
docker compose -f docker-compose.prod.yml up -d --force-recreate web worker
```

**Hệ quả chính xác khi tắt cờ:**

- Verifier trả về `UNVERIFIABLE` ngay lập tức, **không gọi KiotViet**.
- Việc chặn đơn hủy quay về **check DB**: chỉ chặn khi `Order.status = 6` (CANCELLED) hoặc
  `invoice_status = 2`. Check DB này **luôn chạy ở cả 3 đường** (kể cả khi cờ bật) và
  không bao giờ bị tắt.
- ⚠️ Rủi ro: đơn vừa hủy trên KiotViet nhưng DB chưa sync → **lọt qua, xuất kho được**
  (đúng cửa sổ trễ tới 30' như trước khi có tính năng này). Đây là đánh đổi có ý thức:
  chấp nhận lọt vài đơn hủy để kho không dừng hoạt động.
- Không còn self-heal: đơn hủy sẽ chỉ được cập nhật khi cron/webhook KiotViet về.
- **Mỗi lần quét/xuất khi tắt cờ đều ghi 1 dòng log WARNING** — dùng để biết đã chạy bao lâu
  ở chế độ này và bao nhiêu lượt xuất kho không được xác minh:

  ```
  scan verify skipped: kill-switch off (order_id=123)
  ```

Nhớ **bật lại `=1` + restart** ngay khi KiotViet ổn định.

Các env liên quan (`backend/.env.example`):

| Env | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `SCAN_VERIFY_CANCEL_REALTIME` | `1` | Bật/tắt xác minh realtime (kill-switch). |
| `SCAN_VERIFY_TIMEOUT_SECONDS` | `3` | Timeout HTTP mỗi lần hỏi hóa đơn KiotViet (áp cho **cả** lần xin token mới). |
| `SCAN_VERIFY_CACHE_TTL_OK` | `300` | Cache kết quả "đơn OK" (giây) — quét lại trong 5' không gọi lại KiotViet. |
| `SCAN_VERIFY_CACHE_TTL_CANCELLED` | `300` | Cache kết quả "đơn đã hủy" (giây). |

Lỗi (timeout/5xx) **không bao giờ được cache** → hết lỗi là quét lại ăn ngay.

### 3.1. Vì sao `SCAN_VERIFY_CACHE_TTL_OK` = 300s (đánh đổi cần biết)

Kho quét từng kiện rồi mới bấm **Xác nhận xuất kho** cho cả lô — khoảng cách giữa hai việc
này có thể vài phút. TTL 30s (giá trị đầu tiên) khiến toàn bộ mã quét từ đầu phiên đã hết
cache lúc bấm Xác nhận → lô 50 kiện phải gọi KiotViet **50 lần tuần tự** ngay trên request
web (KiotViet chậm → request kéo dài hàng phút, ăn hết slot của `web`).

- **Đánh đổi**: một đơn bị hủy **ngay sau** lần verify OK gần nhất có thể lọt tối đa **5
  phút**. Vẫn tốt hơn cửa sổ 30' của cron sync, và **chốt cuối `dispatch_orders_batch` luôn
  đọc DB tươi** nên đơn đã biết hủy (webhook/cron/self-heal về kịp) vẫn bị chặn.
- Muốn siết lại: giảm `SCAN_VERIFY_CACHE_TTL_OK` (đổi bằng env, restart `web` + `worker`) —
  đổi lại là mỗi lần bấm Xác nhận sẽ gọi KiotViet nhiều hơn và chậm hơn.
- Đơn **đã xuất kho rồi** (quét/bấm lại lần hai) **không gọi KiotViet nữa** — tồn đã trừ,
  round-trip không bảo vệ được gì; guard vẫn check DB nên đơn hủy vẫn bị chặn.
- Timeout: `SCAN_VERIFY_TIMEOUT_SECONDS` áp cho **cả lần refresh token**, nên worst case một
  lần xác minh ≈ **2 × 3s = 6s** (token + invoice), không còn ca treo 30s vì token hết hạn.

## 4. Log để tra (grep gì)

Toàn bộ prefix dưới đây là **nguyên văn** trong code, grep được trực tiếp trên log `web` / `worker`.

Xác minh (`apps.kiotviet.application.services.cancellation_verifier`):

| Mức | Nguyên văn (prefix) | Ý nghĩa |
| --- | --- | --- |
| WARNING | `scan verify skipped: kill-switch off (order_id=...)` | Đang tắt kill-switch → không hỏi KiotViet. |
| WARNING | `scan verify unavailable (order_id=..., invoice_code=...): <lỗi>` | Gọi KiotViet lỗi/timeout → fail-closed, chặn xuất. |
| WARNING | `scan verify unverifiable: KiotViet 404 (order_id=..., invoice_code=..., channel_id=...)` | Hóa đơn không thuộc channel → fallback tin DB (không chặn). |
| INFO | `scan verify healed order (order_id=..., from_status=..., to_status=..., invoice_status=...)` | Phát hiện đơn hủy trên KiotViet và **đã ghi ngược vào DB** (self-heal). |
| ERROR | `scan verify push_event failed (order_id=...)` | Heal DB xong nhưng bắn realtime WS lỗi (không ảnh hưởng chặn). |

Chặn xuất kho (`apps.orders.application.services.order_service_commands`) — grep `dispatch blocked`:

```
dispatch blocked (order_id=..., reason=order_cancelled, source=db, status=..., invoice_status=...)
dispatch blocked (order_id=..., reason=order_cancelled, source=kiotviet)
dispatch blocked (order_id=..., reason=verify_unavailable): <lỗi>
```

`source=db` = DB đã biết đơn hủy; `source=kiotviet` = DB chưa biết, KiotViet mới nói (đây
chính là ca mà trước đây hàng đã bị xuất nhầm).

Cron khớp mã pending (`apps.inventory.application.services.pending_scan_service`):

```
pending scan <mã> verify unavailable: <lỗi>            # lỗi tạm → giữ PENDING, thử lại vòng sau
pending scan <mã>: order <id> cancelled — no dispatch  # đơn hủy → chuyển sang trạng thái cancelled
pending scan <mã> failed matching order <id>: <lỗi>    # lỗi nghiệp vụ khác → failed
```

## 5. Mã quét chờ có trạng thái "Đơn đã hủy" (`cancelled`)

Bảng "Mã chờ khớp" (dialog Quét xuất kho) có thêm trạng thái **`cancelled` — "Đơn đã hủy"**.

- **Nghĩa**: mã vận đơn được quét khi đơn chưa về DB; cron sau đó khớp được đơn, nhưng đơn ở
  trạng thái **HỦY** → cron **không xuất kho**, đánh dấu mã là `cancelled` và dừng lại.
  Đây là kết quả nghiệp vụ hợp lệ, **không phải lỗi hệ thống** (khác với `failed`).
- **Terminal**: cron chỉ quét lại mã `pending` → mã `cancelled` **sẽ không bao giờ tự khớp lại**.
- **Ai xử lý**: quản lý kho.
- **Xử lý thế nào**:
  1. Dòng đó hiện trên bảng với nhãn *"Đơn đã hủy — không xuất kho"*. Đối chiếu mã vận đơn với
     kiện hàng: kiện đó **giữ lại, không giao**.
  2. Nếu xác định đơn thật sự đã hủy → **xoá mã** khỏi bảng (nút xoá trên dòng) sau khi đã tách
     kiện hàng ra.
  3. Nếu đơn bị hủy nhầm và sau đó được **khôi phục** trên KiotViet: **không có nút "thử lại"** —
     phải **xoá mã và quét lại từ đầu** (hoặc xuất kho đơn đó bằng đường thông thường).

## 6. Vì sao mã chờ có thể mất vài phút mới khớp

Cron `inventory-match-pending-scans` chạy **mỗi 60 giây**. Từ khi có xác minh realtime, mỗi mã
xuất kho phải gọi HTTP tới KiotViet, nên mỗi vòng bị chốt ngân sách
(`apps/inventory/application/services/pending_scan_service.py`):

- `_CYCLE_TIME_BUDGET_SECONDS = 55.0` — hết 55 giây là dừng vòng, phần còn lại để vòng sau
  (task có `soft_time_limit = 100s`, phải chừa chỗ cho mã cuối đang chạy).
- `_DISPATCH_PER_CYCLE = 40` — tối đa 40 mã xuất kho mỗi vòng.
- `_VERIFY_RETRY_BACKOFF_SECONDS = 300` — mã đã **thất bại xác minh** bị hoãn **5 phút** trước
  khi thử lại, để không chiếm chỗ của mã mới quét (chống head-of-line blocking).

Hệ quả ops cần biết:

- Quét một lô lớn (> 40 mã chưa có đơn) → khớp dần qua nhiều vòng, mỗi phút tối đa 40 mã.
- KiotViet chậm → số mã xử lý được mỗi vòng giảm, và mã lỗi phải **chờ 5 phút** mới thử lại.
  Mã vẫn ở trạng thái **"Chờ khớp"** kèm ghi chú lỗi — **đây là bình thường, đừng khớp tay vội**,
  cứ để cron tự thử lại. Chỉ can thiệp nếu mã kẹt `pending` quá lâu (> 30') và log có
  `pending scan ... verify unavailable` lặp lại liên tục.

## 7. Checklist khi kho báo "không xuất kho được"

1. Toast nói **"đã hủy"** → đúng nghiệp vụ, đơn hủy thật. Giữ hàng, không giao. Kết thúc.
2. Toast nói **"Chưa xác minh được ... với KiotViet"** → lỗi hạ tầng:
   - `grep "scan verify unavailable" ` trên log `web` để xem lỗi thật (timeout? 401? 5xx?).
   - Nếu chỉ lác đác → bảo kho quét lại.
   - Nếu đồng loạt và kéo dài (KiotViet down) → cân nhắc **tắt kill-switch** (mục 3), báo
     quản lý kho là trong thời gian tắt, đơn hủy mới có thể lọt.
3. Sau khi KiotViet ổn định → `SCAN_VERIFY_CANCEL_REALTIME=1` + restart `web`, `worker`.
