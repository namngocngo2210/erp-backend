# Landing ingest — chỉ định sale bằng `staff_username`

**Ngày:** 2026-08-12
**Nhánh:** `feat/ingest-staff-username`
**Endpoint:** `POST /api/orders/ingest`

## Bài toán

Bên gửi đơn qua `/api/orders/ingest` hiện không có cách chỉ định sale phụ trách:
mọi đơn về đều đi qua bộ chia tự động (`LeadAssignmentService`). Với đơn không
phải từ landing page (hotline / messenger / zalo / kol / koc...), bên gửi **đã
biết** ai là sale đang giữ khách, nên chia tự động là sai — đơn của sale này rơi
vào tay sale khác.

## Hợp đồng mới

Thêm một field optional vào payload:

| Field | Kiểu | Bắt buộc | Ý nghĩa |
|---|---|---|---|
| `staff_username` | string (≤150) | không | Username tài khoản CRM của sale phụ trách |

Luật quyết định (tính trên `order_source` **sau normalize**):

| `order_source` gửi lên | `staff_username` | Kết quả |
|---|---|---|
| `zalo` / `hotline` / `messenger` / `kol` / ... | khớp user hợp lệ | Đơn gán sẵn cho sale đó, **không** chia tự động |
| khác `landing` | không gửi / rỗng / toàn khoảng trắng | Chia tự động (như hiện tại) |
| khác `landing` | không tra ra user hợp lệ | Chia tự động + `logger.warning` + metric |
| `landing`, hoặc thiếu field, hoặc giá trị lạ | bất kỳ | **Bỏ qua** `staff_username`, luôn chia tự động |

`OrderSource.normalize(value, default=LANDING)` đã gộp cả ba ca *thiếu field* /
*rỗng* / *giá trị không thuộc enum* về `landing`, nên luật "rỗng hoặc landing thì
bỏ qua" thoả bằng đúng một phép so sánh sau normalize — không đọc giá trị thô.

Tra user: `username__iexact` + `is_active=True` + `deleted_at IS NULL` +
**loại role hệ thống** (`.exclude(role_assignments__role__is_system=True)`).
Tài khoản sale đã nghỉ (inactive) hoặc đã xoá coi như không khớp — đơn rơi về
vòng chia tự động thay vì nằm chết ở tài khoản không ai dùng. Điều kiện loại
role hệ thống là bắt buộc: endpoint `AllowAny` + token dùng chung khiến
`{"staff_username":"admin"}` là payload giả rất rẻ để dựng, và "admin" là
username dễ đoán nhất; nếu tra ra được Administrator thì đơn có `staff_id` nên
thoát khỏi cả `assign_if_unassigned` lẫn cron `sweep_unassigned` — nằm ngoài
mọi lưới đỡ. Cùng luật với `import_excel.py` (`staff_username` → `staff_id`)
và `staff_id` field của `OrderInlineUpdateSerializer`.

Ca "không tra ra user" **cố ý lenient** (tạo đơn + chia tự động), đồng bộ với
`/api/orders/lark-ingest` đang làm y vậy: sai một cái tên không đáng để mất lead.
Bù lại phải có metric, xem mục Quan sát.

## Thiết kế

### Vì sao không cần cờ DB "đừng chia đơn này"

Chỉ cần gán `staff` là đủ, vì cả hai đường chia đều tự tránh đơn đã có sale:

- `LeadAssignmentService.assign_if_unassigned` return sớm khi `order.staff_id is not None`
  (`lead_assignment_service.py:75`);
- cron `LeadAssignmentService.sweep_unassigned` chỉ quét `staff_id__isnull=True`.

Ngược lại, phương án "để trống staff + đánh dấu đừng chia" **không** bền: sweep là
lưới đỡ quét mọi đơn thuộc sheet1/2/3 còn trống staff trong 48h, nên sẽ cần thêm
một field DB + migration chỉ để tự vô hiệu hoá lưới đỡ đó. Bỏ.

### Vì sao resolve user ở tầng request, không ở task

Payload đi qua queue (`dispatch("orders.landing_ingest")`) nên phải là JSON thuần.
Resolve ngay trong request rồi đẩy `staff_id` (int) sang task:

- lỗi/typo hiện trong log request kèm `trace_id`, nối được với `LandingIngestTrace`;
- task không cần biết gì về `staff_username`;
- không thêm truy vấn DB vào transaction tạo đơn.

Đánh đổi đã biết: giữa lúc resolve và lúc task tạo đơn (vài giây) tài khoản có thể
bị vô hiệu hoá — đơn vẫn gán cho tài khoản đó. Không đáng để đổi thiết kế.

### Các điểm sửa

**1. Transport — `LandingOrderSerializer`**
(`backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py:189`)

- thêm `staff_username = CharField(required=False, allow_blank=True, allow_null=True)`
  — **không** khai `max_length` ở field: đây là field best-effort, tên quá dài
  không được phép làm `is_valid()` = False và làm mất cả đơn;
- trong `validate()`, **sau** bước normalize `order_source`: trim giá trị rồi
  **cắt còn 150 ký tự** (`[:150]`, khớp `AbstractUser.username`); nếu rỗng
  hoặc `order_source == OrderSource.LANDING` thì bỏ hẳn key khỏi `validated_data`.

Tầng này không chạm DB — chỉ chuẩn hoá hợp đồng, giống cách `channel_id` giữ nguyên
kiểu thô để service resolve.

**2. Application — `OrderIngestService`**
(`backend/apps/orders/application/services/order_ingest_service.py`)

- hàm mới `resolve_ingest_staff_id(username) -> int | None` đặt cạnh
  `resolve_channel_ref` (`:70`), dùng `.only("id")`; query loại thêm
  `.exclude(role_assignments__role__is_system=True)` (xem lý do ở mục "Tra
  user" phía trên) — cùng pattern với `import_excel.py` /
  `kiotviet_landing_serializers.py` (`staff_id` field);
- trong `ingest_landing_order`, khối resolve `staff_username` nằm **SAU**
  `LeadFilterService.evaluate(...)` (không phải ngay sau resolve kênh như bản
  đầu): nếu payload có `staff_username` thì resolve → set `payload["staff_id"]`,
  và pop `staff_username` khỏi payload (không đẩy tên qua queue). Không tra ra →
  `logger.warning` kèm `trace_id` + username, không set `staff_id`.

Lý do đặt SAU bộ lọc lead: resolve trước lọc sẽ tính cả những payload sau đó
bị 409 (trùng/blocklist) / 400 (sđt sai) hoặc dry-run vào
`landing_ingest_staff_hint_total` — mà metric này là đường duy nhất ops nhìn
thấy tính năng có chạy hay không, số liệu lẫn tạp sẽ đánh lừa việc phát hiện
bên gửi gõ sai tên hàng loạt. Đặt sau còn tránh một query User vô ích cho mỗi
lead bị dedup.

**3. Domain — `create_order_from_landing`**
(`backend/apps/orders/application/services/order_service_commands.py:212`)

- thêm `staff_id=payload.get("staff_id")` vào `Order.objects.create(...)`.

Gán ở đúng lúc `create` (không gán sau) để `build_order_created_row(order)` ở cuối
hàm mang luôn `staff_id`/tên sale — FE nhận realtime thấy đúng người phụ trách ngay
ở dòng vừa hiện, không phải chờ refetch.

**Có** ghi journey event khi `payload.get("staff_id")` có giá trị, loại **riêng**
`staff_ingest_designated` (`STAFF_INGEST_DESIGNATED_EVENT`, khai ở
`sheet_rules.py` + nhãn ở `crm_catalog.EVENT_TYPE_OPTIONS`) — KHÔNG dùng
`staff_assigned`: loại đó mang nghĩa "bộ chia tự động đã gán", dùng lại sẽ làm
nhiễu (không phân biệt được đơn do bộ chia chọn hay do payload chỉ định sẵn).
Gọi `OrderServiceHelpers.log_staff_change(..., actor=None, event_type=STAFF_INGEST_DESIGNATED_EVENT)`
ngay cạnh `LeadAssignmentService.assign_if_unassigned(order)`. `actor=None` vì
đường ingest dùng token dùng chung, không có người thao tác — khác luồng tạo
đơn tay (`create_order_manual`) nơi có actor thật nên không cần ghi thêm.
Quyết định này khác bản thiết kế ban đầu (xem mục "Không làm"): đơn ingest ẩn
danh hoàn toàn nên khi tranh chấp chủ đơn, timeline phải tra ngược được sale
này đến từ đâu.

### Quan sát (observability)

Counter mới trong `backend/apps/orders/metrics.py`:

```
desi_landing_ingest_staff_hint_total{result="assigned|unknown_username"}
```

Vì ca không-tra-ra-user im lặng (đơn vẫn 200), metric là đường duy nhất để ops
phát hiện bên gửi đang gõ sai tên sale hàng loạt. Không thêm panel Grafana ở lô
này — query được là đủ cho lần đầu.

## Không làm (non-goals)

- Không sửa `/api/orders/lark-ingest` (đã có `staff_username` từ trước).
- Không nhận `staff_id` thô trong payload — chỉ `staff_username`.
- Không nhận email/display_name làm khoá tra.
- Không migration, không đổi model (`OrderJourneyEvent` đã có sẵn, chỉ thêm
  giá trị `event_type` mới).
- Không đưa endpoint này vào OpenAPI (vẫn `@extend_schema(exclude=True)`).

**Đính chính so với bản đầu:** mục này từng ghi "không ghi journey event
`staff_assigned`" và dừng ở đó. Sau review đã đổi thành **CÓ** ghi — nhưng bằng
loại event riêng (`staff_ingest_designated`), không phải `staff_assigned`. Lý
do: đường ingest là ẩn danh hoàn toàn (token dùng chung, không actor), khác
luồng tạo đơn tay có actor thật để tra cứu — nếu không ghi gì thì khi tranh
chấp chủ đơn, không ai lần ra được sale này đến từ payload ingest nào. Xem chi
tiết ở mục "Domain — `create_order_from_landing`" phía trên.

## Test

Thêm class `LandingIngestStaffUsernameTests` vào
`backend/apps/orders/tests/test_ingest_api.py` (dùng lại pattern patch `dispatch`
để chạy task đồng bộ đã có trong file), phủ đúng ma trận quyết định:

| `order_source` | `staff_username` | Kỳ vọng |
|---|---|---|
| `zalo` | user active, khớp | `order.staff_id` = user đó; `pick_staff_for` không được gọi |
| `zalo` | username không tồn tại | không gán theo payload; đi vào bộ chia |
| `zalo` | user `is_active=False` | bỏ qua; đi vào bộ chia |
| `zalo` | user `deleted_at` != NULL | bỏ qua; đi vào bộ chia |
| `landing` | user active, khớp | bỏ qua; đi vào bộ chia |
| không gửi | user active, khớp | bỏ qua (default `landing`); đi vào bộ chia |
| `zalo` | `"   "` | bỏ qua; đi vào bộ chia |

Thêm test cấp serializer (không cần DB) cho việc drop key: `landing` → không có
`staff_username` trong `validated_data`; `zalo` → có, đã trim; kèm test tên quá
150 ký tự → `is_valid()` vẫn `True`, giá trị bị cắt còn 150 (không 400).

Sau review cuối, bổ sung thêm:

- `ResolveIngestStaffIdTests.test_ignores_system_role_user` — user role hệ
  thống (dựng bằng `make_admin`) không được resolve dù username khớp.
- `LandingIngestStaffUsernameTests.test_designated_sale_logs_ingest_journey_event`
  — đơn được chỉ định sale từ payload có đúng 1 `OrderJourneyEvent` loại
  `staff_ingest_designated`, `actor=None`, `to_code` là id sale; không có
  event `staff_assigned` đi kèm.
- `LandingIngestStaffUsernameTests.test_auto_assigned_order_has_no_ingest_journey_event`
  — đơn đi vào bộ chia tự động (username không khớp) không có event
  `staff_ingest_designated`.
