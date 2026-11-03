# Trạng thái vận đơn Best realtime cho hành trình bảo hành

**Ngày:** 2026-07-22
**Trạng thái:** User đã chốt design
**Liên quan:** `2026-07-18-warranty-journey-best-tracking-status-design.md` (cron poll),
`2026-07-13-best-express-integration-design.md` (webhook + module best_express)

## Bối cảnh

Badge trạng thái VC trên hành trình bảo hành hiện chỉ cập nhật qua cron
`warranty-sync-best-tracking` (10 phút/lần, gọi `Order/Query`). Webhook Best đã có
(`POST /api/best-express/webhook`) nhưng chỉ cập nhật bảng `best_shipments`, không
chạm tới `WarrantyJourney` — hai đường hoàn toàn tách rời.

Quyết định lúc thiết kế cron (18/07) dựa trên tiền đề "mã BH nhập tay, không có
`BestShipment` để khớp". Tiền đề đó không còn đúng: nút "Tạo đơn Best" trên form bảo
hành sinh `BestShipment` thật. Kết quả là cùng một vận đơn có hai nguồn trạng thái cập
nhật bằng hai cơ chế khác nhau, lệch nhau tới 10 phút.

## Mục tiêu

Hành trình bảo hành nhận trạng thái **realtime** khi webhook Best về, giữ cron làm
lưới an toàn.

## Phạm vi

Chỉ hành trình bảo hành. KHÔNG đụng đơn hàng bán (`Order`) — đơn bán vẫn đi KShip/KiotViet.

## Kiến trúc

Dùng **event bus in-process** có sẵn (`apps/common/events.py`): `best_express` publish,
`warranty` subscribe. Không app nào import app kia — giữ nguyên nguyên tắc "best_express
độc lập hoàn toàn" của spec 13/07. Cùng pattern `apps/inventory/subscribers.py` và
`apps/users/subscribers.py`.

### 1. Contract event (`apps/best_express/contracts/tracking_events.py` — file mới)

```python
@dataclass(frozen=True)
class BestTrackingUpdated(DomainEvent):
    """Phát khi nhận được 1 event trạng thái MỚI từ Best (webhook hoặc trace sync)."""

    event_type: ClassVar[str] = "best_express.tracking_updated"
    code_best: str = ""
    merchant_order_code: str = ""
    best_status_code: str = ""
    modified_at_ms: int = 0
```

Chỉ chứa giá trị nguyên thuỷ, không ORM object — theo yêu cầu của `apps/common/events.py`.

### 2. Publish ở `store_event`, KHÔNG phải `apply_event`

`webhook_service.store_event` publish `BestTrackingUpdated` bằng `publish_on_commit`
ngay sau khi tạo được dòng `BestWebhookEvent` mới. Event trùng (bị `IntegrityError`) thì
KHÔNG publish.

Lý do chọn `store_event`:

- `apply_event` chỉ chạy khi khớp được `BestShipment`. Mã BH nhập tay không có dòng nào
  trong bảng đó → sẽ trượt hết.
- `store_event` chạy cho mọi event mới, đã chống trùng bằng unique constraint ở DB
  (migration 0004) nên không cần dedupe thêm ở tầng publish.
- `tracking_service._sync_trace_results` cũng gọi `store_event` → nút trace tay cập nhật
  badge bảo hành mà không phải viết thêm gì.

### 3. Subscriber (`apps/warranty/subscribers.py` — file mới)

Đăng ký trong `apps/warranty/apps.py` `ready()` (hiện `WarrantyConfig` chưa có `ready`).

Handler `on_best_tracking_updated(event)`:

1. Gom mã cần khớp: `code_best` và `merchant_order_code` (bỏ chuỗi rỗng). Không có mã nào
   → no-op.
2. Tìm `WarrantyJourney` có 1 trong 3 ô mã (`new_tracking_code`, `recall_code`,
   `return_tracking_code`) bằng một trong các mã trên. Không khớp → no-op, không log
   warning (đa số event là của đơn bán, không phải bảo hành — log sẽ thành rác).
3. Với mỗi ô mã khớp: áp guard §4 rồi ghi `*_best_status`, `*_best_status_ms`, và
   `tracking_synced_at`.

Handler nuốt lỗi theo cơ chế sẵn có của `EventBus.publish` (một handler hỏng không làm
chết publisher).

### 4. Chống event lạc hậu — 3 cột mới

Best có thể đẩy event không đúng thứ tự; không guard thì badge "GTC" bị kéo ngược về
"Đang trung chuyển". `WarrantyJourney` hiện chỉ có `tracking_synced_at` chung, không biết
từng mã đang ở mốc thời gian nào.

Thêm vào `WarrantyJourney` (migration mới):

| Field | Kiểu | Ghi chú |
| --- | --- | --- |
| `new_tracking_best_status_ms` | `BigIntegerField(default=0)` | mốc `modified_at` (epoch ms) của trạng thái đang lưu |
| `recall_best_status_ms` | `BigIntegerField(default=0)` | |
| `return_tracking_best_status_ms` | `BigIntegerField(default=0)` | |

`BigIntegerField` vì epoch ms vượt phạm vi `IntegerField` 32-bit.

Quy tắc ghi, dùng CHUNG cho cả subscriber lẫn cron: chỉ ghi khi
`modified_at_ms >= <field>_ms` đang lưu. Dấu `>=` (không phải `>`) để event có cùng mốc
vẫn ghi được — an toàn vì nội dung giống nhau, và tránh kẹt khi giá trị cũ là 0.

Tách thành hàm thuần trong `apps/warranty/tracking_status.py`:
`should_apply_best_status(current_ms: int, incoming_ms: int) -> bool`.

`tracking_sync_service` phải sửa để ghi kèm `*_ms` (hiện đọc `traceLogs` mới nhất rồi ghi
đè vô điều kiện). Không sửa thì cron sẽ đè ngược lên dữ liệu webhook mới hơn.

### 5. Cron giữ vai trò lưới an toàn

`warranty-sync-best-tracking` vẫn chạy 10 phút/lần, **không đổi lịch, không đổi phạm vi**.
Thay đổi duy nhất là ghi kèm `*_ms` và tuân theo guard §4 — nếu không, cron sẽ đè ngược
lên dữ liệu webhook mới hơn.

Cron là lưới đỡ cho: BEST chưa cấu hình webhook, event rơi, mã thuộc account khác, đơn
tạo trước khi bật webhook.

### 6. Chấp nhận có chủ ý

Event chỉ có ích cho bảo hành (không có `BestShipment` khớp) vẫn bị `apply_event` đánh
`processed=False, "unmatched"`, retry mỗi 15 phút rồi bỏ cuộc sau 3 ngày. KHÔNG sửa: sửa
đồng nghĩa `best_express` phải biết `warranty` tồn tại, phá đúng ranh giới vừa dựng. Đây
là nhiễu có giới hạn và đã có cơ chế dọn (`purge_old_best_webhook_events`).

## Test

- **Publish:** `store_event` tạo dòng mới → publish đúng 1 event với đủ 4 field; event
  trùng (IntegrityError) → KHÔNG publish; publish sau commit chứ không trong transaction.
- **Subscriber:** khớp theo `code_best`; khớp theo `merchant_order_code` khi `code_best`
  rỗng; khớp đúng ô mã tương ứng trong 3 ô (gửi đi / thu hồi / gửi lại); không khớp
  journey nào → no-op không nổ; journey không có mã nào → no-op.
- **Guard:** `should_apply_best_status` — incoming cũ hơn → False; bằng → True; current
  bằng 0 → True. Subscriber nhận event cũ hơn → không đổi `*_best_status`.
- **Cron:** `tracking_sync_service` ghi kèm `*_ms`; event cũ hơn dữ liệu webhook đã có →
  không đè.
- **End-to-end:** POST webhook với mã khớp hành trình BH → `*_best_status` đổi và
  `vc_groups` trả nhóm mới.

## Không làm (YAGNI)

- Không đụng đơn hàng bán (`Order`), không đụng luồng KShip/KiotViet.
- Không bỏ hay giãn cron.
- Không đổi bảng map `tracking_status.py` (11 nhóm) hay `status_map.py` của best_express.
- Không thêm lịch sử trạng thái VC cho hành trình.
- Không sửa cơ chế `unmatched`/retry/purge của `best_express`.
