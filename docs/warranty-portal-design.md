# Thiết kế: Portal tra cứu bảo hành cho khách (tra-cuu.delax.asia)

**Ngày:** 2026-08-06
**Trạng thái:** Chờ duyệt spec
**Nhánh:** `feat/warranty-portal`
**Tiền đề:** `docs/warranty-codes-design.md` (giai đoạn 1 — phát hành mã, đã merge)

## 1. Bối cảnh & mục tiêu

Giai đoạn 1 đã phát hành mã bảo hành: mỗi tem có `serial` (`DX-000001`) và `code`
10 ký tự, gắn với một sản phẩm, xuất Excel gửi iCheck in tem.

Giai đoạn 2 (tài liệu này) dựng **portal public** tại domain riêng
`tra-cuu.delax.asia`, ngoài CRM, cho khách hàng cuối:

1. **Kích hoạt bảo hành** bằng `IMEI + số điện thoại` (IMEI = mã 10 ký tự in trên tem).
2. Sau khi kích hoạt → xem **thông tin bảo hành** của sản phẩm.
3. Xem **toàn bộ đơn hàng** theo số điện thoại + **tổng tiền hàng đã mua**.
4. Xem **hành trình vận chuyển** của từng đơn.

Kênh kích hoạt thứ hai là **Zalo OA** (khách nhắn IMEI + SĐT). Đợt này chỉ làm
**API dùng chung**; phần bot Zalo nối vào sau, không thuộc phạm vi.

Trang quản trị "Mã bảo hành" trong CRM được bổ sung **tab Nhật ký** để xem lịch
sử mọi lần nhập/quét mã.

### Quyết định đã chốt với chủ dự án

| Câu hỏi | Quyết định |
| --- | --- |
| Liên kết IMEI ↔ đơn hàng | **Không** gắn IMEI vào đơn. Đơn tra theo số điện thoại kích hoạt. |
| Thời hạn bảo hành | **Cố định 24 tháng** kể từ ngày kích hoạt, cho mọi sản phẩm |
| Khách có nhiều đơn | Hiển thị **toàn bộ đơn** của SĐT + **tổng tiền hàng đã mua** |
| IMEI đã kích hoạt, SĐT khác nhập lại | **Chặn + cảnh báo + ghi nhật ký** (dấu hiệu tem bị sao chép) |
| Zalo OA | Đợt này chỉ làm API dùng chung (có tham số kênh + token); bot Zalo làm sau |
| Domain | `tra-cuu.delax.asia` |

### Ngoài phạm vi (YAGNI)

- Bot/webhook Zalo OA, xác thực OA.
- OTP xác thực số điện thoại.
- Gắn serial vào đơn hàng lúc xuất kho.
- Khách tự tạo phiếu bảo hành/yêu cầu đổi trả trên portal.
- Tài khoản/đăng nhập cho khách.

> **BẢN SỬA ĐỔI 07/08/2026 — portal 2 tab.** Chủ dự án cho biết **mã 10 ký tự
> (kèm QR chứa mã) nằm DƯỚI LỚP CÀO trên tem; tem chỉ in công khai `serial`**.
> Vì vậy "có mã = đã sở hữu sản phẩm", số điện thoại không còn cần cho bước
> kích hoạt. Portal tách thành 2 tab, bỏ danh sách đơn đã mua. Các mục §2, §4.1,
> §4.2, §4.3, §5 dưới đây đã được viết lại theo bản sửa đổi; phần mô tả cũ
> (token phiên, `/orders`, `/orders/<id>/journey`) không còn hiệu lực.

## 2. Nguyên tắc bảo mật của portal

Mỗi tab có một "chìa khoá" riêng, đều là thứ chỉ người mua thật mới có:

- **Tab bảo hành — mã 10 ký tự dưới lớp cào.** Mã sinh ngẫu nhiên bằng `secrets`,
  bị che vật lý (cả QR chứa mã cũng nằm dưới lớp cào). Cào được = đang cầm sản
  phẩm ⇒ đủ để coi là chủ sở hữu, KHÔNG cần hỏi thêm số điện thoại.
  `serial` in công khai trên tem nên **tuyệt đối không dùng để kích hoạt** (xem
  §4.1) — đây là lý do gốc của quyết định 06/08.
- **Tab hành trình — cặp `mã vận đơn + SĐT`.** Phải khớp cả hai; mã vận đơn
  không tuần tự nên không dò được, SĐT chặn người lạ tra đơn của khách khác.
  Sai bất kỳ vế nào đều trả cùng một thông báo "Không tìm thấy đơn hàng" để
  không xác nhận đơn nào có thật.
- ~~**Không có** ô tra cứu chỉ bằng số điện thoại, và không còn API nào trả lịch
  sử mua hàng theo số điện thoại.~~ **Hết hiệu lực từ 24/08** — `track` nhận một
  trong hai ô, tra bằng SĐT trả tối đa 10 đơn gần nhất (không có tổng tiền đã
  mua). Xem hộp "BẢN SỬA ĐỔI 24/08" ở §4.2 cho phần đánh đổi.
- Mỗi tab trả kết quả trong **một lần gọi API**, nên portal **không dùng token
  phiên** (bản trước có JWT 30 phút — đã bỏ cùng với `/orders`).
- Dữ liệu trả về được **giới hạn**: không địa chỉ đầy đủ (chỉ tỉnh/thành),
  không ghi chú nội bộ, không transcript cuộc gọi, không thông tin nhân viên,
  không giá trị đơn.
- Rate limit riêng, chặt hơn mức chung (chi tiết §6).
- Phát hiện tem giả chuyển hẳn sang **tab Nhật ký trong CRM**: nhiều
  `not_found` từ một IP, hoặc một mã bị quét lại dồn dập từ nhiều IP lạ.

## 3. Data model

### 3.1 `warranty_activations` (mới)

Một mã kích hoạt **đúng một lần**.

| Cột | Kiểu | Ghi chú |
| --- | --- | --- |
| `id` | PK | |
| `code` | OneToOne → `warranty_codes.WarrantyCode`, PROTECT | Unique: 1 mã = 1 lần kích hoạt |
| `phone` | varchar(20), blank, index | **Từ 07/08 để rỗng** — kích hoạt không hỏi SĐT nữa. Giữ cột (không xoá) để đọc được các bản ghi đã kích hoạt trước đó và để kênh Zalo OA vẫn ghi được SĐT nếu muốn |
| `channel` | varchar(16) | `web` \| `zalo_oa` |
| `activated_at` | datetime | Mốc tính hạn bảo hành |
| `expires_at` | date | `activated_at + 24 tháng`, lưu sẵn để query/hiển thị nhanh |
| `ip_address` | varchar(45), null | IP lúc kích hoạt |
| `user_agent` | varchar(255), blank | Cắt 255 ký tự |

Index: unique(`code`), (`phone`, `activated_at`), (`activated_at`).

Số tháng bảo hành đặt trong settings `WARRANTY_PORTAL_MONTHS = 24` để đổi được
mà không sửa code; `expires_at` tính lúc kích hoạt nên đổi settings **không**
hồi tố các mã đã kích hoạt (đúng ý: cam kết với khách không thay đổi ngược).

### 3.2 `warranty_code_scan_logs` (mới)

Ghi **mọi** lần nhập/quét, kể cả mã sai — nguồn dữ liệu cho tab Nhật ký.

| Cột | Kiểu | Ghi chú |
| --- | --- | --- |
| `id` | PK | |
| `keyword` | varchar(64), index | Chuỗi khách nhập nguyên văn (đã cắt/trim) |
| `code` | FK `WarrantyCode`, null, SET_NULL | Mã khớp được (null nếu không tìm thấy) |
| `product_sku` | varchar(100), blank | Snapshot SKU lúc quét |
| `phone` | varchar(20), blank | SĐT khách nhập (nếu có) |
| `result` | varchar(24), index | `activated` \| `already_activated` \| `not_found` \| `viewed` |
| `channel` | varchar(16) | `web` \| `zalo_oa` |
| `ip_address` | varchar(45), null | |
| `user_agent` | varchar(255), blank | |
| `created_at` | datetime, index | |

Index: (`created_at`), (`result`, `created_at`), (`ip_address`, `created_at`),
(`keyword`) dùng `varchar_pattern_ops` cho tìm kiếm prefix.

**Retention**: task Celery xóa log cũ hơn **180 ngày**, chạy hằng ngày (mirror
pattern retention của `OrderJourneyEvent`/`BestWebhookEvent`).

### 3.3 Không đổi

`WarrantyCode`, `WarrantyCodeBatch` giữ nguyên. Trạng thái "đã kích hoạt" suy ra
từ việc tồn tại bản ghi `warranty_activations` (join theo `code_id`, unique
index nên rẻ).

## 4. Nghiệp vụ

### 4.1 Kích hoạt (`POST /api/warranty-portal/activate`)

Input: `{ "keyword": "<mã 10 ký tự>", "phone": "<SĐT>", "channel": "web" }`.
**Bản sửa đổi 08/08: `phone` quay lại làm trường BẮT BUỘC**, nhưng với mục đích
KHÁC bản trước 07/08 — mã dưới lớp cào vẫn là thứ tự nó xác thực quyền sở hữu
(không cần SĐT để phân biệt "chủ" hay "người khác"); `phone` chỉ là **thông
tin đăng ký bảo hành điện tử theo số điện thoại** phục vụ CSKH, chuẩn hóa qua
`apps.customers.phone_utils.normalize_phone`, sai định dạng → 400.

1. Chuẩn hóa `keyword`: trim + upper, cắt 64 ký tự. Chuẩn hóa `phone` ở
   serializer (giống `TrackSerializer`).
2. Tìm mã: **chỉ khớp `code` (10 ký tự)**. `serial` (`DX-xxxxxx`) in công khai
   trên tem nên KHÔNG bao giờ dùng để kích hoạt hay xem lại — nhập serial rơi
   vào nhánh 3.
3. **Không tìm thấy** → ghi log `not_found` (kèm `phone` vừa nhập) → 404
   `{"detail": "Mã không hợp lệ. Sản phẩm có thể không phải hàng chính hãng.", "code": "not_found"}`.
4. **Mã đã kích hoạt** → không tạo bản ghi mới, **không ghi đè `phone`** của
   activation hiện có (SĐT đăng ký lần đầu là chuẩn), ghi log `viewed` (kèm
   `phone` vừa nhập lần này, có thể khác SĐT đã đăng ký), trả 200 kèm thông tin
   bảo hành. Không còn nhánh 409 `already_activated`: ai có mã dưới lớp cào đều
   là người đang cầm sản phẩm, không có khái niệm "người khác kích hoạt".
5. **Hợp lệ** → tạo `warranty_activations` với `phone` khách vừa nhập, trong
   transaction; unique `code` là chốt chặn khi hai request cùng lúc,
   `IntegrityError` → xử lý như nhánh 4 (giữ `phone` của bản ghi thắng cuộc
   đua). Ghi log `activated`, trả 201.

Giá trị `already_activated` vẫn giữ trong enum `ScanResult` để đọc được dữ liệu
lịch sử, nhưng không sinh thêm bản ghi mới.

Response (200/201) — **không còn `token`**:

```json
{
  "warranty": {
    "serial": "DX-000001", "code": "A8K2M4P9QX",
    "product_sku": "BNC02", "product_name": "Bồn ngâm chân ...",
    "activated_at": "2026-08-06T10:00:00+07:00",
    "expires_at": "2028-08-06", "days_left": 730, "months": 24
  }
}
```

### 4.2 Tra cứu hành trình (`POST /api/warranty-portal/track`)

> **BẢN SỬA ĐỔI 24/08/2026 — chỉ cần MỘT trong hai ô.** Chủ dự án chốt: khách
> thường chỉ nhớ mã vận đơn **hoặc** số điện thoại, bắt nhập cả hai làm rơi
> nhiều lượt tra. Từ bản này `shipping_code` và `phone` đều **tuỳ chọn**, chỉ cần
> có ít nhất một; nhập cả hai thì lọc AND như cũ.
>
> - Nhập **mã vận đơn** (một mình hoặc kèm SĐT) → thường ra đúng một đơn → trả
>   `{order, journey}` như trước.
> - Nhập **SĐT** một mình mà khớp nhiều đơn → trả
>   `{"orders": [<order_summary>...], "truncated": bool}`, tối đa
>   `TRACK_LIST_LIMIT = 10` đơn **mới nhất trước**, **không kèm hành trình**
>   (dựng hành trình cho cả 10 đơn là N+1 trong khi khách chỉ mở một đơn).
>   Khách chọn một đơn → frontend gọi lại `track` kèm `order_code` (+
>   `shipping_code`/`phone` của chính đơn đó) để lấy `{order, journey}`.
> - `order_code` là **bộ lọc phụ**, không phải lối vào: gửi một mình nó → 400.
>   Thiếu cả `shipping_code` lẫn `phone` → 400
>   `"Vui lòng nhập mã vận đơn hoặc số điện thoại."`
> - Câu 404 đổi thành `"Không tìm thấy đơn hàng. Kiểm tra lại mã vận đơn hoặc số
>   điện thoại."` (cũ là "và"), vẫn dùng chung cho mọi trường hợp sai.
>
> **Đánh đổi bảo mật đã được cân nhắc và chủ dự án chấp nhận:** mã vận đơn nội bộ
> `SC00001` là tuần tự nên dò được, và từ nay chỉ cần mã đó là xem được tên khách
> + SĐT + tỉnh của đơn (§2 trước đây coi cặp mã + SĐT là "chìa khoá", và cấm hẳn
> ô tra chỉ bằng số điện thoại). Phương án che một phần (`Nguyễn V. A`,
> `090****567`) đã được đề xuất và bị **từ chối** để ưu tiên trải nghiệm khách.
> Lớp phòng vệ còn lại là throttle theo IP thật (`warranty_portal_read`, §6) —
> nên **firewall chỉ nhận traffic từ dải IP Cloudflare** (mục 3 phần §9) từ nay
> là điều kiện bắt buộc, không còn là "nên làm".

Thay cho `GET /orders` và `GET /orders/<id>/journey` của bản trước — **cả hai
endpoint đó bị gỡ bỏ**, cùng toàn bộ phần token phiên
(`domain/portal_token.py`, `get_activation_or_none`) và
`portal_orders_service.list_orders`.

Input: `{ "shipping_code": "<mã vận đơn>", "phone": "<SĐT>" }`. Dùng POST (không
phải GET) để mã vận đơn và SĐT không nằm trên URL/log truy cập.

1. Chuẩn hóa `shipping_code` (trim, upper) và `phone` qua
   `apps.customers.phone_utils.normalize_phone`.
2. Tìm `Order` theo **`shipping_code` khớp chính xác VÀ `phone_normalized` khớp**,
   `deleted_at__isnull=True`. Sai bất kỳ vế nào → 404
   `{"detail": "Không tìm thấy đơn hàng. Kiểm tra lại mã vận đơn và số điện thoại.", "code": "not_found"}`
   — cùng một thông báo cho mọi trường hợp sai, không tiết lộ mã vận đơn nào có thật.
3. Tìm thấy → trả tóm tắt đơn + timeline trong một response.

Response 200:

```json
{
  "order": {
    "order_code": "DH001", "invoice_code": "HD0001", "shipping_code": "SC00001",
    "created_at": "2026-08-01T03:00:00Z", "status": 2,
    "status_label": "Đang giao hàng",
    "customer_name": "Nguyễn Văn A", "phone": "0901234567",
    "province": "Hà Nội",
    "items": [{"sku": "BNC02", "name": "Bồn ngâm chân", "quantity": 1}]
  },
  "journey": {
    "results": [{"at": "...", "label": "Đang giao hàng", "group": "delivering"}],
    "source": "best"
  }
}
```

Cố ý **không trả**: `id`, `total_amount`, địa chỉ đầy đủ, thông tin nhân viên,
ghi chú vận hành.

**Bản sửa đổi 10/08 — mở thêm `customer_name`, `phone`, `invoice_code.`** Ba
trường này trước đây bị chặn vì là PII. Lý do mở: muốn nhận được response, người
gọi đã phải nhập ĐÚNG cặp mã vận đơn + SĐT của đơn, tức là đã biết sẵn SĐT và
gần như chắc chắn là chính chủ; đổi lại khách xác nhận được "đúng đơn của mình"
trước khi đọc hành trình. Danh sách trường được assert **khớp tuyệt đối** trong
`test_track_valid_pair_returns_order_and_journey` — thêm trường mới phải sửa test
đó, coi như một chốt chặn bắt buộc cân nhắc lộ lọt.

Nếu sau này muốn siết lại (ví dụ portal mở cho tra cứu hàng loạt), phương án
nhẹ nhất là che một phần ngay ở `order_summary` (`Nguyễn Văn A` → `Nguyễn V. A`,
`0901234567` → `090****567`) chứ không bỏ trường, để frontend không phải đổi.

Nguồn dữ liệu timeline, theo thứ tự ưu tiên:

1. `BestWebhookEvent` lọc theo `code_best`/`merchant_order_code` của
   `BestShipment` gắn với đơn → mỗi event một mốc, map sang nhãn tiếng Việt qua
   `apps/warranty/tracking_status.py` (`BEST_VC_GROUP_LABELS`).
2. Nếu đơn không đi Best Express (hoặc chưa có event) → fallback: các mốc
   `order_status_changed` trong `OrderJourneyEvent`, **chỉ lấy `event_type`,
   thời điểm và nhãn trạng thái** — tuyệt đối không trả `note`/`payload_json`
   (chứa PII, transcript).

Response: `{"results": [{"at": "...", "label": "Đang giao hàng", "group": "delivering"}], "source": "best|internal"}`.

### 4.4 Nhật ký quét (CRM, `GET /api/warranty-codes/scan-logs`)

Endpoint nội bộ, quyền `warranty-codes:view`. Filter: `keyword` (prefix),
`ip_address`, `date_from`, `date_to`, `result`, `channel`; phân trang chuẩn.

## 5. Frontend

### 5.1 Portal public (bundle chung, đường dẫn `/bao-hanh`)

Rẽ nhánh trong `frontend/src/main.tsx` như `/warranty` hiện có:
`path.startsWith("/bao-hanh")` → `WarrantyPortalPage`, **trước** `<App/>` nên
không dính AuthProvider/RequireAuth. Domain `tra-cuu.delax.asia` cấu hình nginx
chuyển `/` → `/bao-hanh` (§6), khách không phải gõ đường dẫn.

Trang có **2 tab** (nhãn tab cập nhật bản sửa đổi 08/08 — "Vận đơn" thay cho
"Hành trình", ngắn hơn cho vừa màn hình mobile):

| Tab | Nhập gì | Hiện gì |
| --- | --- | --- |
| **Bảo hành** | Mã 10 ký tự + nút quét camera + **SĐT (bắt buộc, 08/08)** | Thẻ bảo hành: sản phẩm, serial, ngày kích hoạt, hạn, số ngày còn lại |
| **Vận đơn** (mặc định) | Mã vận đơn **hoặc** SĐT — chỉ cần một (24/08) | Một đơn: tóm tắt (mã, ngày, trạng thái, sản phẩm, tỉnh) + timeline. Nhiều đơn: danh sách thẻ bấm chọn (`OrderPickList`), chọn xong có nút "← Chọn đơn khác" |

#### Tab mở sẵn theo lối vào (`?tab=`, bản sửa đổi 10/08)

Portal có hai lối vào với ý định khác hẳn nhau, nên tab mở sẵn đọc từ query
`?tab=` (`frontend/src/features/warranty-portal/entry-tab.ts`) thay vì hardcode:

| Lối vào | Link | Tab mở sẵn |
| --- | --- | --- |
| Khách tự vào web / redirect `/` → `/bao-hanh` của nginx | `https://tra-cuu.delax.asia/bao-hanh` | **Vận đơn** |
| Nút "Kích hoạt bảo hành" nhúng ở Zalo OA | `https://tra-cuu.delax.asia/bao-hanh?tab=kich-hoat` | **Bảo hành** |

Giá trị nhận: `kich-hoat`/`activate` → tab Bảo hành, `van-don`/`track` → tab Vận
đơn; thiếu hoặc sai giá trị thì rơi về mặc định **Vận đơn** (không báo lỗi).
Query khác trên link (`utm_*`) được giữ nguyên. Khách bấm đổi tab thì URL được
`history.replaceState` cập nhật `?tab=` tương ứng — F5 hay chia sẻ link vẫn ra
đúng tab đang xem, và không làm rác nút Back.

⚠️ Đây là **giao kèo URL công khai**: đổi tên tham số hay giá trị sẽ làm hỏng
link đã cấu hình trong Zalo OA. Muốn đổi thì thêm alias mới ở `PARAM_ALIASES`
chứ đừng thay giá trị cũ.

Ở tab **Bảo hành**, SĐT (bản sửa đổi 08/08) **không** dùng để xác thực quyền sở
hữu — mã dưới lớp cào đã đủ xác thực điều đó — mà là thông tin **đăng ký bảo
hành điện tử theo số điện thoại** cho CSKH. Vì vậy khi mã đã kích hoạt (nhánh
"xem lại"), nhập SĐT khác **không ghi đè** SĐT đã đăng ký lần đầu. Dưới ô SĐT
có dòng hướng dẫn: "Vui lòng nhập đúng số điện thoại để được bảo hành, Delax sẽ
bảo hành điện tử theo số điện thoại đăng ký của quý khách." Ở tab **Vận đơn**,
SĐT vẫn giữ vai trò xác thực chủ đơn (khớp `phone_normalized` mới tra được),
kèm dòng hướng dẫn: "Vui lòng nhập đúng số điện thoại để tra cứu, Delax cũng sẽ
hỗ trợ chăm sóc khách hàng trọn đời theo số điện thoại đăng ký của quý khách."

Chuyển tab thì xoá kết quả của tab kia và mọi thông báo lỗi, để khách không
nhầm dữ liệu giữa hai luồng.

**Bố cục (bản sửa đổi 10/08).** Từ breakpoint `lg` (≥1024px) trang là **lưới 2
cột**: thanh tab ở hàng 1 cột trái (`27rem`), form ở hàng 2 cột trái, kết quả ở
hàng 2 cột phải. Đặt kết quả ở hàng 2 (chứ không phải hàng 1) là chủ ý: mép trên
thẻ kết quả thẳng hàng với mép trên thẻ form. Bản đầu dùng `flex-row`, cột phải
bắt đầu từ đỉnh cột trái — tức ngang thanh tab — nên hai thẻ trắng lệch nhau đúng
bằng chiều cao thanh tab. Chưa có kết quả thì bỏ hẳn cột 2, `justify-center` kéo
cột form về giữa trang như bố cục một cột cũ.

Dưới `lg` thì xếp dọc (tabs → form → kết quả). Vì kết quả phải đặt được sang cột
phải nên state kết quả của **cả hai** tab do `WarrantyPortalPage` giữ;
`ActivateForm`/`TrackForm` chỉ báo lên qua `onSuccess`/`onResult`. Form không còn
bị thay thế sau khi tra xong — khách tra mã tiếp ngay mà không phải bấm quay lại.

Ở mobile kết quả rơi xuống dưới màn hình nên trang tự `scrollIntoView` tới khối
kết quả sau mỗi lần tra thành công (bỏ qua ở desktop vì kết quả đã nằm cạnh
form; tôn trọng `prefers-reduced-motion`).

Cấu trúc `frontend/src/features/warranty-portal/`:

```
pages/WarrantyPortalPage.tsx       # khung 2 tab + header logo
components/ActivateForm.tsx        # tab 1: ô mã + nút quét + ô SĐT đăng ký CSKH
components/PortalScanButton.tsx    # overlay camera, tự đóng sau khi quét được
components/WarrantyCard.tsx        # thẻ bảo hành (signature element)
components/TrackForm.tsx           # tab 2 "Vận đơn": ô mã vận đơn + SĐT
components/OrderJourney.tsx        # timeline mốc vận chuyển
extract-code.ts                    # tách mã 10 ký tự từ URL QR qcheck.vn
phone.ts                           # chuẩn hoá SĐT dùng chung cho cả 2 tab
portal-api.ts                      # gọi API (fetch riêng, KHÔNG dùng fetchJson)
portal-theme.ts                    # palette Delax teal→green
queries.ts                         # react-query hooks
```

Đã gỡ: `components/OrderList.tsx` (danh sách đơn đã mua), phần lưu token trong
`sessionStorage`, hook `usePortalOrders`/`usePortalJourney` theo `order_id`.

Giao diện: mobile-first (khách quét QR bằng điện thoại), palette lấy từ logo
Delax (teal `#12615C` → xanh lá `#3FA95B`, nền mint) — portal là mặt tiền
thương hiệu nên theo logo, không dùng tông xanh da trời của CRM nội bộ. Trạng
thái bảo hành hiển thị nổi bật: còn hạn (gradient thương hiệu) / hết hạn
(xám-đỏ trầm).

### 5.2 Tab "Nhật ký" trong trang Mã bảo hành (CRM)

Thêm tab thứ 3 vào `WarrantyCodesPage` (sau Lô hàng, IMEI). Bảng: IMEI CODE,
TỪ KHÓA, MÃ SP, KẾT QUẢ (badge), KÊNH, ĐỊA CHỈ IP, USER AGENT (cắt ngắn, hover
xem đầy đủ), NGÀY QUÉT. Bộ lọc: IMEI code/từ khóa, địa chỉ IP, từ ngày, đến
ngày; phân trang 50/trang. Cột KẾT QUẢ và KÊNH là bổ sung so với ảnh mẫu —
đây mới là thứ giúp phát hiện tem bị sao chép (nhiều `already_activated`/
`not_found` từ cùng một IP).

## 6. Hạ tầng

### 6.1 Nginx

Thêm `nginx/tra-cuu.delax.asia.conf.prod-template`, copy từ
`customer.delax.vn.conf.prod-template`, khác ở:

- `server_name tra-cuu.delax.asia;`
- **Chứng chỉ**: zone `.asia` khác `.vn` → cần cert riêng cho `delax.asia`
  (Cloudflare Origin Certificate cho `*.delax.asia`, đặt tại
  `/etc/ssl/certs/delax.asia.pem` + `/etc/ssl/private/delax.asia.key`).
  **Việc cần chuẩn bị trước khi bật domain.**
- **Chỉ mở**: `location = /` (rewrite sang `/bao-hanh`), `location /bao-hanh`,
  `location /assets/`, `location /api/warranty-portal/` (zone rate limit riêng),
  `location = /healthz`. **Không** mở `/api/` chung, `/ws/`, `/nginx_status`.
- Zone mới trong `nginx/rate-limit-zones.conf.example` (và bản đồng bộ ở
  `nginx/default.conf`, `nginx/nginx.conf`):
  `limit_req_zone $desi_rl_key zone=desi_portal:10m rate=10r/m;` dùng
  `burst=10 nodelay` cho `/api/warranty-portal/`.

### 6.2 Django

- `DJANGO_ALLOWED_HOSTS` thêm `tra-cuu.delax.asia`;
  `DJANGO_CSRF_TRUSTED_ORIGINS` thêm `https://tra-cuu.delax.asia`.
- Không cần CORS (cùng origin, frontend served từ chính domain đó).
- Throttle scope riêng trong `DEFAULT_THROTTLE_RATES`, theo IP thật
  (`ClientIpMixin` + `ThrottleGuardMixin` như `WarrantyRegisterThrottle`):
  `warranty_portal_activate = "10/hour"` cho `/activate`, và
  `warranty_portal_read = "120/hour"` nay dùng cho `/track` (bản trước dùng cho
  `/orders` + `/orders/<id>/journey` đã gỡ). Giữ nguyên tên scope để không phải
  đổi biến env đã cấu hình trên prod.
- Settings: `WARRANTY_PORTAL_MONTHS = 24`, `WARRANTY_PORTAL_ZALO_TOKEN` (token
  cho kênh `zalo_oa`; thiếu token ở prod → từ chối request có `channel=zalo_oa`).
  `WARRANTY_PORTAL_TOKEN_TTL_MINUTES` **bỏ** cùng với token phiên (gỡ khỏi
  `base.py`, `.env.example` và cả hai file compose).

## 7. Kiểm thử

**Backend**
- Kích hoạt: mã hợp lệ (`code`) → 201 + token + `expires_at` = ngày kích hoạt +
  24 tháng; SĐT dạng `+84...` chuẩn hóa đúng.
- `serial` của mã CHƯA kích hoạt → 404 `not_found` (không kích hoạt được, không
  tạo bản ghi); `serial` của mã ĐÃ kích hoạt + đúng SĐT → 200 "viewed"; `serial`
  của mã đã kích hoạt + SĐT khác → 404 `not_found` (KHÔNG `already_activated`).
- Mã sai → 404 + có dòng log `not_found`; mã (`code`) đã kích hoạt SĐT khác →
  409 + log `already_activated`; chính chủ nhập lại bằng `code` → 200, **không**
  tạo bản ghi kích hoạt thứ hai.
- Hai request kích hoạt song song cùng một mã → đúng một bản ghi được tạo.
- Token: hết hạn → 401; token của SĐT A không xem được đơn/hành trình của SĐT B.
- `orders`: chỉ trả đơn đúng SĐT, `total_spent` chỉ cộng đơn giao thành công,
  không rò trường nhạy cảm (test assert danh sách key trả về).
- `journey`: có Best event → nhãn tiếng Việt đúng; không có → fallback nội bộ và
  **không** chứa `note`/`payload_json`.
- Throttle: quá 10 lần kích hoạt/giờ → 429.
- Scan logs API: filter + quyền `warranty-codes:view`.

**Frontend**: test form (validate rỗng/sai định dạng), render 3 nhánh kết quả,
danh sách đơn + tổng tiền, timeline; tab Nhật ký (bảng + filter) theo pattern
vitest hiện có.

**Bổ sung cho bản sửa đổi 24/08** (`test_portal_track_api.py`,
`track-form.test.tsx`, `warranty-portal-page.test.tsx`):
- Backend: chỉ `shipping_code` → 200; chỉ `phone` + 1 đơn → 200 kèm hành trình;
  chỉ `phone` + nhiều đơn → danh sách **mới nhất trước**, không có `journey`,
  bỏ đơn xoá mềm; quá `TRACK_LIST_LIMIT` → cắt còn 10 + `truncated=true`; chọn
  đơn bằng `order_code` → 200; thiếu cả hai khoá hoặc chỉ có `order_code` → 400;
  `phone` để rỗng kèm mã vận đơn → **không** còn bị coi là sai định dạng.
- Frontend: bỏ trống cả hai ô → báo lỗi và không gọi API; điền một ô → gọi API
  với ô còn lại rỗng; danh sách nhiều đơn hiện thẻ chọn và **chưa** có timeline;
  bấm chọn → gọi lại `track` kèm `order_code`; nút "Chọn đơn khác" quay lại danh
  sách mà không gọi API lần ba; lỗi khi tải hành trình vẫn giữ danh sách.

## 8. Sprint & tiến độ

| Sprint | Nội dung | Trạng thái |
| --- | --- | --- |
| S1 | Backend: models `warranty_activations` + `warranty_code_scan_logs`, migration, settings, service kích hoạt + token phiên, unit test | Đã xong |
| S2 | Backend: API portal (activate/orders/journey) + throttle + test; API scan-logs cho CRM | Đã xong |
| S3 | Frontend portal `/bao-hanh`: form kích hoạt + trang kết quả (bảo hành, đơn, hành trình), test | Đã xong |
| S4 | Tab Nhật ký trong CRM + nginx vhost + env + retention task; rà soát bảo mật/hiệu năng, MR | Đã xong |
| S5 | Fix wave sau final review: chặn kích hoạt bằng serial tuần tự (C1), passthrough env compose (I2), chuẩn hoá SĐT +84 phía FE (I3), xử lý lỗi/401 ở OrderList/OrderJourney (I4), "Xem thêm" nối trang (I5), test throttle 429 + race thật (I6), M1-M3 | Đã xong |
| S6 | 08/08 — tách 2 tab "Bảo hành"/"Vận đơn" (đổi tên tab), thêm lại ô SĐT bắt buộc ở tab Bảo hành làm đăng ký bảo hành điện tử CSKH (không xác thực quyền sở hữu, không ghi đè khi xem lại), dòng hướng dẫn nguyên văn dưới mỗi ô SĐT | Đã xong |
| S7 | 24/08 — tab Vận đơn chỉ cần MỘT trong hai ô (mã vận đơn hoặc SĐT); tra bằng SĐT ra nhiều đơn thì trả danh sách 10 đơn gần nhất để khách chọn (`OrderPickList`), chọn xong mới tải hành trình | Đã xong |

Ghi chú theo sprint:
- **S1**: `ip_address` dùng `CharField(45)` thay `GenericIPAddressField` như app `audit` — theo đúng brief, chưa đồng bộ lại (deferred, tác động thấp).
- **S2**: thiếu test case channel sai (chỉ có thiếu token/đúng token cho `zalo_oa`); `get_activation_or_none` chưa có test riêng ở sprint tạo ra nó (dùng gián tiếp qua test S2/S3).
- **S3**: lỗi mạng (fetch throw trước khi có `Response`) hiện message trình duyệt mặc định thay vì copy tiếng Việt của portal — deferred, hiếm gặp (chỉ khi mất mạng giữa chừng).
- **S4**: retention task `apps.warranty_codes.tasks.purge_old_scan_logs` chạy 03:55 hằng ngày (batch 10k, single-flight lock); nginx vhost `tra-cuu.delax.asia` cần cert Cloudflare Origin riêng cho `*.delax.asia` — CHƯA có trên máy chủ, xem §9 việc cần chuẩn bị.
- **S5**: xem chi tiết quyết định bảo mật ở mục Deviation "06/08 — `serial` không còn kích hoạt được lần đầu" ngay dưới đây. FE thêm `error-message.ts` dùng chung cho ActivateForm/OrderList/OrderJourney (trước đó mỗi component tự viết bảng thông báo lỗi). Việc firewall Cloudflare + bucket throttle riêng cho Zalo OA vẫn là việc CHƯA làm, chuyển thành mục 5-6 ở §9.

- **S7**: chủ dự án chốt **không** che tên/SĐT khi tra bằng một trường — xem đánh
  đổi ở §4.2. Danh sách nhiều đơn cố ý **không** kèm hành trình (tránh N+1) nên
  frontend gọi `track` lần hai khi khách chọn đơn. Chưa làm: đo `EXPLAIN` trên
  prod cho nhánh lọc thuần `phone_normalized` (index `order_phone_norm_idx` đã
  có, nhưng còn phải sort `-created_at`), và siết firewall Cloudflare ở §9 mục 3
  — từ bản này là **bắt buộc** vì throttle theo IP là lớp phòng vệ duy nhất còn
  lại của tab Vận đơn.

Sau mỗi sprint cập nhật cột trạng thái + ghi chú đã/chưa làm tại đây.

### Deviation khi triển khai

So với thiết kế gốc ở các mục trên, thực tế cài đặt có vài điểm lệch (không đổi
hành vi nghiệp vụ, chỉ đổi cách hiện thực):

- **Token phiên chỉ chứa `activation_id`** (`apps/warranty_codes/domain/portal_token.py`),
  KHÔNG kèm `phone_hash` như hướng có thể suy ra từ §4.1: mọi dữ liệu khác (SĐT,
  hạn bảo hành) tra ngược từ DB theo `activation_id` mỗi request — token gọn hơn
  và không cần đồng bộ lại khi SĐT/expires_at đổi.
- **Portal FE dùng hàm fetch riêng** (`frontend/src/features/warranty-portal/portal-api.ts`),
  KHÔNG dùng `fetchJson` dùng chung của CRM: portal là bundle public không đăng
  nhập, không cần interceptor gắn access token/refresh của `fetchJson`.
- **Nhãn trạng thái đơn tiếng Việt** đặt tại `apps/warranty_codes/domain/order_labels.py`
  (module domain riêng của app `warranty_codes`), không tái dùng bảng nhãn của
  app `orders`/`warranty` — tránh phụ thuộc ngược giữa app portal public và app
  nghiệp vụ nội bộ (đúng nguyên tắc module độc lập).
- **06/08 (fix wave sau final review) — `serial` không còn kích hoạt được lần
  đầu.** Thiết kế gốc ở §4.1 cho phép kích hoạt bằng `code` **hoặc** `serial`
  cùng một ô nhập. Final review chỉ ra: `serial` sinh tuần tự (`DX-000123`) nên
  đoán được, trong khi `code` sinh ngẫu nhiên bằng `secrets` — cho phép kích
  hoạt bằng serial nghĩa là kẻ xấu có thể quét tuần tự `DX-000001, DX-000002,
  ...` để chiếm tem hàng loạt (kích hoạt trước khi khách thật mở hộp) và đọc
  được `code` bí mật của người khác qua response kích hoạt. Chốt với chủ dự án:
  kích hoạt lần đầu **chỉ** nhận `code`; `serial` **chỉ** hợp lệ ở nhánh xem lại
  (mã đã kích hoạt + đúng SĐT đã kích hoạt), và mọi nhánh serial khác trả
  `not_found` thay vì `already_activated` để không xác nhận serial có thật.
  Không đổi schema/model — chỉ đổi thứ tự tra cứu và điều kiện chấp nhận trong
  `activation_service.activate()`.
- **08/08 — SĐT quay lại tab Bảo hành, nhưng đổi mục đích.** Bản 07/08 đã bỏ
  hẳn `phone` khỏi `/activate` vì mã dưới lớp cào tự xác thực quyền sở hữu.
  Chủ dự án nay yêu cầu thêm lại SĐT — không phải để xác thực chủ sở hữu (mã
  vẫn đủ), mà để **đăng ký bảo hành điện tử theo số điện thoại** phục vụ CSKH.
  Khác biệt quan trọng so với thiết kế gốc trước 07/08: SĐT của lần **kích hoạt
  đầu tiên** mới là chuẩn — nhánh "xem lại" (mã đã kích hoạt) nhận SĐT mới nào
  cũng **không ghi đè** `activation.phone`, chỉ ghi vào scan log để có dấu vết.
  Không có nhánh chặn/409 nào phát sinh thêm. Cột `warranty_activations.phone`
  và `warranty_code_scan_logs.phone` không đổi schema (đã tồn tại từ S1, giữ
  `blank=True` để đọc được bản ghi rỗng giai đoạn 07/08). Nhãn tab thứ 2 đổi
  "Hành trình" → "Vận đơn" (ngắn hơn, vừa màn hình mobile); mọi chuỗi "Tra cứu
  hành trình" trong UI đổi thành "Tra cứu vận đơn" — tiêu đề timeline "Hành
  trình đơn hàng" (`OrderJourney.tsx`) giữ nguyên, không phải phạm vi đổi tên.

## 9. Việc cần phía vận hành

1. Trỏ DNS `tra-cuu.delax.asia` về IP máy chủ hiện tại (qua Cloudflare).
2. Tạo Cloudflare Origin Certificate cho `*.delax.asia`, cài lên máy chủ theo
   đường dẫn ở §6.1.
3. Cập nhật `.env` prod: `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`.
4. Khi nối Zalo OA: cấp `WARRANTY_PORTAL_ZALO_TOKEN` cho phía bot.
5. **Firewall origin chỉ nhận 80/443 từ dải IP Cloudflare.** Throttle của portal
   (`PortalActivateThrottle`/`PortalReadThrottle`, §6.2) tin header
   `CF-Connecting-IP` do Cloudflare set — nếu client gọi thẳng vào IP gốc của
   máy chủ (bỏ qua Cloudflare), header đó tự set được, né mọi trần request. Phải
   chặn ở tầng firewall (không chỉ nginx) để bắt buộc traffic đi qua Cloudflare;
   mirror đúng nguyên tắc "origin chỉ nhận từ CF" đã áp cho `customer.delax.vn`.
6. **Khi nối Zalo OA (giai đoạn 2): cần bucket throttle riêng theo token/kênh,
   không dùng chung bucket theo IP.** `PortalActivateThrottle` hiện key theo IP
   client thật — bot Zalo OA gọi từ hạ tầng Zalo (một hoặc vài IP cố định) thay
   mặt hàng nghìn người dùng cuối, nên throttle theo IP sẽ chặn nhầm cả kênh
   Zalo chỉ sau vài request thật. Cần scope/khoá throttle riêng theo
   `WARRANTY_PORTAL_ZALO_TOKEN` (hoặc theo số điện thoại người dùng cuối trong
   payload) trước khi bật kênh này ở quy mô lớn.
