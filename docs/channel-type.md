# Loại kênh bán (`channel_types`) — thiết kế & tiến độ

**Ngày:** 2026-08-21 · **Branch:** `feat/channel-type-taxonomy`

## 1. Vấn đề

Bảng `channels` chỉ có `code`/`name` tự do, không có trường nào nói kênh đó bán
trên **nền tảng** nào. Hệ quả đang thấy trong codebase:

- Báo cáo tổng phải **đoán** nền tảng từ mã kênh — `channel__code__istartswith
  ='facebook'` và `channel__code__iexact='google-ads-web'` trong
  `apps/orders/reporting/shared.py`. Mở kênh đặt tên lệch quy ước là rơi khỏi
  nhóm, **không có gì báo lỗi**.
- Không lọc/nhóm được kênh theo nền tảng ở bất kỳ màn hình nào.

## 2. Thiết kế

### 2.1 Bảng `channel_types`

| Cột | Kiểu | Ghi chú |
| --- | --- | --- |
| `id` | bigserial | PK |
| `code` | slug, **unique** | `shopee` · `tiktok` · `facebook` · `web` |
| `name` | varchar(100) | Tên hiển thị |
| `sort_order` | int | Thứ tự dropdown (10/20/30/40) |
| `is_active` | bool | Tắt để ẩn khỏi dropdown, **không xoá** dữ liệu đã gán |
| `created_at` | timestamptz | |

Cố ý là **bảng** chứ không phải `TextChoices`: thêm nền tảng mới (Lazada,
Sendo…) chỉ là thêm một dòng dữ liệu, không phải deploy code.

`code` **trùng đúng** giá trị `SALE_CHANNEL_*` trong
`apps/orders/reporting/shared.py` để bước sau báo cáo đọc thẳng được loại kênh.

### 2.2 `channels.channel_type_id`

FK **`PROTECT`**, `null=True`. Khác `price_book` (dùng `SET_NULL`) là có chủ ý:
xoá một loại kênh đang được dùng phải **nổ**, chứ không được âm thầm bỏ phân
loại hàng loạt kênh. Muốn "nghỉ dùng" một loại thì tắt `is_active`.

`null` = **chưa phân loại**, là trạng thái hợp lệ (xem §2.3).

### 2.3 Backfill trong migration `0024`

Migration seed 4 loại rồi gán best-effort cho kênh đang có, theo đúng thứ tự:

| Luật | → loại |
| --- | --- |
| `code` chứa `shopee` | shopee |
| `code` chứa `tiktok` | tiktok |
| `code` = `google-ads-web` | web |
| `code` bắt đầu bằng `facebook` | facebook |

Hai luật cuối lấy nguyên từ `reporting/shared.py` — đã được kiểm chứng trên
prod. Kênh không khớp luật nào để **NULL**, admin tự gán trên giao diện.

**CỐ Ý không đoán theo prefix hoá đơn** (`HDSPE_`/`HDTTS_`): prefix đó nằm rải
trên 6 kênh khác nhau (đo prod 08/2026) nên nó là thuộc tính của **đơn**, không
phải của kênh — xem ghi chú "sàn xét TRƯỚC kênh" trong `reporting/shared.py`.

Migration **reversible**: `unseed` gỡ gán rồi xoá 4 dòng seed.

### 2.4 API

| Endpoint | Thay đổi |
| --- | --- |
| `GET /api/channels/types` | **Mới**. Danh mục loại kênh đang bật, gate `channels:view`, cache 30s theo version chung của app channels. Không phân trang. |
| `GET /api/channels` | Thêm `channel_type: {id, code, name} \| null` (read-only) |
| `POST /api/channels` | Nhận `channel_type_id` (optional, nullable) |
| `PATCH /api/channels/{id}` | Nhận `channel_type_id`; **sentinel `_UNSET`** ở repository — không gửi field thì giữ nguyên, gửi `null` mới là bỏ phân loại |

`ChannelTypeField.get_queryset()` lọc `is_active=True` **lúc request**: tắt một
loại giữa chừng thì form ngừng nhận ngay, không phải restart process.

`list_for_user` thêm `select_related("channel_type")` — thiếu là N+1 đúng nghĩa
vì serializer đọc `obj.channel_type` trên mọi dòng.

### 2.5 Frontend

- Cột **"Loại kênh"** trên bảng kênh (cả bảng desktop lẫn thẻ mobile).
- Ô chọn loại kênh trong dialog **Tạo kênh** và **Quản lý kênh**.
- Ô tìm kiếm của trang kênh nay khớp cả tên loại kênh.
- Loại kênh **đã tắt mà kênh đang gán** vẫn hiện trong trigger kèm nhãn
  `(đã ẩn)` — cùng cái bẫy đã xử lý cho bảng giá: trigger trắng trơn khiến người
  quản lý tưởng "chưa gán" rồi bấm chọn lại và vô tình gỡ phân loại thật.

## 2bis. Đợt 2 (2026-08-22, branch `feat/channel-type-followups`)

Trả nốt hai khoản đã ghi ở §3 của đợt 1.

### 2bis.1 CRUD danh mục loại kênh

| Endpoint | Quyền | Ghi chú |
| --- | --- | --- |
| `GET /api/channels/types` | `channels:view` **hoặc** `{orders,landing-orders,koc-orders}:view` | Mở rộng so với đợt 1: ô lọc "Loại kênh" nằm trên trang đơn mà sale không có `channels:view`. Danh mục chỉ là tên nền tảng, không nhạy cảm. |
| `GET …?include_inactive=true` | `channels:view` | Cờ này bị **bỏ qua** (không 403) với người chỉ có quyền trang đơn — họ vẫn nhận danh sách loại đang bật. |
| `POST /api/channels/types` | `channels:create` | |
| `PATCH /api/channels/types/{id}` | `channels:edit` | **Không nhận `code`** — xem dưới. |
| `DELETE /api/channels/types/{id}` | `channels:delete` | 400 kèm số kênh đang dùng nếu còn ràng buộc. |

Ba quyết định:

- **`code` bất biến sau khi tạo.** Nó là khoá nghiệp vụ: báo cáo sẽ dựa vào nó
  và hàm reverse của migration `0024` xoá seed theo đúng mã này. Đổi mã sau khi
  tạo là hỏng cả hai một cách âm thầm, nên `ChannelTypeUpdateSerializer` cố ý
  chỉ có `name` / `sort_order` / `is_active`.
- **Xoá bị chặn khi còn kênh dùng** (FK `PROTECT`). API đếm trước để nêu đúng
  con số, và vẫn bắt `ProtectedError` phòng ca kênh được gán xen vào giữa lúc
  đếm và lúc xoá. UI khoá luôn nút xoá dựa trên cột `channel_count`.
- **`channel_count` annotate ở repository**, không đếm trong serializer — vài
  dòng thì N+1 chưa đau, nhưng đây là mẫu chung của repo.

Giao diện: nút **"Loại kênh"** trên trang Quản lý kênh mở dialog danh mục
(thêm dòng mới, sửa tên/thứ tự, bật/tắt, xoá, cột "Số kênh").

### 2bis.2 Bộ lọc "Loại kênh" trên trang đơn

Lọc theo **CODE** (`channel__channel_type__code__in`), token `none` = kênh chưa
phân loại — cùng quy ước với `shipping_type`, và nhóm này thật vì backfill của
`0024` cố ý để một phần kênh NULL.

Khai báo ở **bốn** dict filter, đúng bài học đã cắn ba lần
(`has_recall_code` → `shipping_type` → `koc_order_type`):

1. `_view_helpers.build_orders_filters` — `/api/orders` **và** job xuất Excel
   (qua `_FiltersRequestShim`)
2. `order_list_view.list_koc_orders` — `/api/orders/koc`
3. `order_list_view.list_landing_orders` — trang Landing
4. `sheet3_view` — tab Sheet 3

`ChannelTypeFilterEndpointParityTests` bắn thẳng vào cả bốn URL, không chỉ
`/api/orders`.

`/api/orders/shipments` (trang Vận đơn) **cố ý ngoài phạm vi**: trang đó không
có bộ lọc kênh nào cả.

Phía client còn hai chỗ nữa phải khớp, nếu không bộ lọc "đúng một nửa":

- `OrderListSerializer` thêm `channel_type` (code) + `channel_type_name`, kèm
  `select_related("channel__channel_type")` — thiếu join là N+1 trên mọi dòng.
- `use-filter-mirror.ts` đối chiếu lại phía client cho hàng realtime (WS
  `order.created`), nếu không đơn mới vẫn "nhảy" vào bảng dù bộ lọc đang giấu nó.

## 3. Phạm vi — cái KHÔNG làm ở đợt này

- **Báo cáo tổng KHÔNG đổi.** 5 nhóm kênh bán vẫn khớp theo prefix hoá đơn +
  mã kênh như cũ. Chuyển báo cáo sang đọc `channel_type` là một thay đổi làm
  **lệch số** nên phải đi MR riêng, có đối chiếu trước/sau trên prod. **Vẫn còn
  nợ sau đợt 2.**
- ~~Chưa có màn CRUD cho `channel_types`~~ — xong ở đợt 2 (§2bis.1).
- ~~Chưa có bộ lọc "Loại kênh" trên trang đơn hàng~~ — xong ở đợt 2 (§2bis.2).
- Chưa có **cột** "Loại kênh" trên bảng đơn (mới chỉ có bộ lọc); dữ liệu đã có
  sẵn trong payload (`channel_type_name`) nên thêm cột là việc nhỏ khi cần.

## 4. Tiến độ

| Hạng mục | Trạng thái |
| --- | --- |
| Model `ChannelType` + FK `Channel.channel_type` | ✅ |
| Migration `0024` (create + seed + backfill, reversible) | ✅ |
| Serializer đọc/ghi + endpoint `GET /api/channels/types` | ✅ |
| Repository (`_UNSET` sentinel, `select_related`) | ✅ |
| `schema.yaml` + `schema.d.ts` (sửa tay, đã đối chiếu regen lệch 0 dòng) | ✅ |
| Frontend: cột bảng, 2 dialog, tìm kiếm, i18n vi/en | ✅ |
| Test backend (12 case, `apps/channels_core/tests`) | ✅ 96 passed |
| Test frontend (8 case) | ✅ 25 passed |
| Đăng ký Django admin | ✅ |
| **Đợt 2** — CRUD danh mục (`POST`/`PATCH`/`DELETE` + dialog quản lý) | ✅ |
| **Đợt 2** — bộ lọc "Loại kênh" trên 4 endpoint đơn + xuất Excel | ✅ |
| **Đợt 2** — `channel_type` trên payload danh sách đơn + mirror realtime | ✅ |
| **Đợt 2** — test backend 20 case, frontend 15 case | ✅ |

## 5. Việc cần làm sau khi deploy

1. **Chạy migrate rồi rà lại phân loại**: backfill chỉ phủ kênh khớp 4 luật ở
   §2.3. Đếm kênh còn `NULL`:
   ```sql
   SELECT COUNT(*) FROM channels WHERE channel_type_id IS NULL AND deleted_at IS NULL;
   ```
   rồi gán tay phần còn lại trên trang Quản lý kênh.
2. Cân nhắc MR tiếp theo: cho Báo cáo tổng đọc `channel_type` thay vì đoán theo
   mã kênh — **phải đo trước/sau**, số từng nhóm sẽ đổi.
