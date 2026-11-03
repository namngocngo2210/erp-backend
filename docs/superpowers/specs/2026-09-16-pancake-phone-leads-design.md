# Spec — Kéo số điện thoại từ Pancake → đẩy cơ hội sang Foxia

> Ngày tạo: 2026-09-16. Branch dự kiến: `feat/pancake-phone-leads`.
> Chuẩn áp dụng: `docs/architecture-standard.md`, `docs/erp-app-shell.md`.
> App tham chiếu: `apps/foxia` (tích hợp đối tác, chiều đẩy `order_push_service.py`),
> hợp đồng Pancake API đã kiểm chứng ở repo `desi-crawl-conversations`
> (`docs/pancake-api-verified.md`, 2026-06-26).

---

## 1. Mục tiêu & phạm vi

**Mục tiêu**
- Quản trị viên nhập **access token của một tài khoản Pancake có quyền Admin** trên các
  page; hệ thống đồng bộ danh sách page về và cho **gán từng page với một kênh bán**.
- Khi khách hàng **cho số điện thoại trong hội thoại** trên một page đã bật, số đó chảy
  về bảng `pancake_leads` và được **đẩy sang Foxia để tạo cơ hội** vào đúng nguồn khách
  hàng của kênh (`idnguonkhachhang` tra từ bảng liên kết Foxia hiện có).
- Có trang xem danh sách lead đã kéo, kèm trạng thái đẩy Foxia và nút đẩy lại.

**Ngoài phạm vi (cố ý)**
- Webhook Pancake — dùng poll định kỳ (quyết định D1).
- Backfill lịch sử hội thoại khi bật page (D5). Có thể thêm lệnh quản trị chạy tay sau.
- Tạo đơn trong `orders` từ lead Pancake (D2).
- Tự retry khi Foxia lỗi — giữ nguyên ràng buộc "POST không idempotent, không retry"
  của spec 2026-09-09; chỉ có nút Đẩy lại thủ công.
- Đồng bộ nội dung hội thoại/tin nhắn — đã có crawler riêng + mart chat.

---

## 2. Quyết định đã chốt (2026-09-16)

| # | Quyết định |
|---|---|
| D1 | **Poll API Pancake định kỳ** (Celery beat 3 phút), không webhook. |
| D2 | Số về lưu thành bản ghi riêng `pancake_leads`; **không** tạo đơn. |
| D3 | Chống trùng theo cặp **(kênh bán, sđt chuẩn hoá)** — mỗi cặp đẩy Foxia đúng 1 lần. Cùng sđt sang kênh khác thì đẩy lại (nguồn Foxia khác). |
| D4 | Cột nguồn Foxia chọn theo **platform của page**: `facebook` → `messenger`; `zalo`, `personal_zalo` → `zalo`. Platform khác (tiktok_shop, shopee_official…) không cho gán kênh. |
| D5 | Không backfill: cursor của page đặt bằng thời điểm bật. |
| D6 | UI: trang Cài đặt → Pancake (token, đồng bộ page, gán kênh, bật/tắt) **và** trang danh sách lead với 8 cột chủ dự án chỉ định (§6.2) + cột trạng thái đẩy. |
| D7 | Payload Foxia gửi `address: ""` (Pancake không có địa chỉ; ba trường `name/phone/address` là bắt buộc theo hợp đồng hiện có). |
| D8 | Mỗi **số điện thoại** một dòng lead. Hội thoại có nhiều số → nhiều dòng (hiếm). |

---

## 3. Hợp đồng Pancake (đã kiểm chứng ở crawler, 2026-06-26)

Hai namespace, xác thực đều qua query string, không header:

| Việc | Endpoint | Auth |
|---|---|---|
| Danh sách page | `GET https://pages.fm/api/v1/pages?access_token=` | token tài khoản |
| Sinh token page | `POST https://pages.fm/api/v1/pages/{page_id}/generate_page_access_token?access_token=` — **bắt buộc** `Content-Type: application/json` + body `{}` (thiếu → HTTP 500) | token tài khoản, cần quyền Admin trên page |
| Hội thoại | `GET https://pages.fm/api/public_api/v1/pages/{page_id}/conversations?page_access_token=&since=&until=&page_number=&order_by=updated_at` — cả 4 tham số đầu bắt buộc; 200/trang; với `order_by=updated_at` trả **DESC theo `updated_at`** và `since/until` (unix giây) lọc theo `updated_at`. **Sửa 21/09/2026:** bản đầu ghi `sort_by=updated_at` — Pancake bỏ qua tham số này, trả DESC theo `inserted_at` và lọc theo `inserted_at`, khiến poll bỏ sót hội thoại cũ vừa cho số | token page |
| Tin nhắn + khách | `GET .../pages/{page_id}/conversations/{conv_id}/messages?page_access_token=&current_count=0` — 30 tin mới nhất + `customers[]` | token page |

Trường dùng đến:
- Page: `id` (chuỗi; Zalo có tiền tố `zl_`/`pzl_`), `name`, `platform`, `is_activated`,
  `has_active_subscription`, `role_in_page`. Chỉ lấy `categorized.activated`.
- Hội thoại: `id`, `type` (INBOX/COMMENT), `inserted_at`, `updated_at`, `has_phone`,
  `recent_phone_numbers[]`, `tags[].text`, `customers[0].name`, `customer_id`,
  `current_assign_users` / `assignee_ids`.
- Messages response: `customers[].recent_phone_numbers` — phần tử có thể là **chuỗi**
  `"09…"` hoặc **object** `{"phone_number": …}` tuỳ page (crawler gặp cả hai, `flexContact`);
  `messages[].has_phone`, `messages[].inserted_at`.

Lỗi sinh token đã biết: `error_code 121` (page không có gói cước), `"Thiếu quyền Admin"`.

**Cần xác minh khi làm (probe 1 lần bằng token thật, ghi vào docstring client):**
tên nhân viên trong `current_assign_users` (hình dạng phần tử); định dạng deep-link
hội thoại trên pancake.vn (chỉ để hiển thị, không tham gia logic).

---

## 4. Model — `backend/apps/pancake/models.py`

### 4.1 `PancakeConnection` — `pancake_connections` (thường 1 row)
```
name             CharField(255) default "Pancake"
access_token     EncryptedCharField(1024)         # apps.common.fields
is_active        Boolean default True
auth_status      unknown | ok | error
auth_error       Text
last_pages_synced_at  DateTime null
created_at / updated_at
get_active()     classmethod như FoxiaConnection
```

### 4.2 `PancakePage` — `pancake_pages` (mirror)
```
pancake_page_id  CharField(64) unique
name             CharField(255)
platform         CharField(32)      # facebook / zalo / personal_zalo / tiktok_shop / …
is_activated     Boolean            # False khi page biến mất khỏi danh sách Pancake
has_active_subscription  Boolean
role_in_page     CharField(32)
synced_at        DateTime auto_now

page_access_token  EncryptedCharField(2048) blank   # sinh khi bật, xoá khi Pancake từ chối
token_error        Text blank

channel          FK channels_core.Channel SET_NULL null   # gán kênh bán
is_enabled       Boolean default False                    # chỉ page bật mới poll
polled_until     DateTime null       # cursor theo updated_at hội thoại
last_polled_at   DateTime null
last_error       Text blank

property foxia_kind -> "messenger" | "zalo" | None   (theo D4)
```

### 4.3 `PancakeLead` — `pancake_leads`
```
page             FK PancakePage PROTECT
channel          FK Channel PROTECT      # SNAPSHOT lúc bắt số — đổi kênh của page
                                         # sau này không đổi nghĩa lead cũ
conversation_id          CharField(128) db_index
conversation_inserted_at DateTime null   # "Ngày tạo hội thoại"
pancake_customer_id      CharField(64) blank
customer_name            CharField(255) blank
phone_raw                CharField(32)
phone                    CharField(16)   # chuẩn hoá 0xxxxxxxxx
tags                     JSONField list[str] default list   # "Thẻ Pancake"
phone_given_at           DateTime null   # "Ngày cho số điện thoại" — xem §5.3
assignee_name            CharField(255) blank             # "Nhân viên Pancake"
created_at               DateTime auto_now_add db_index    # "Ngày tạo"

foxia_kind       CharField(16)           # messenger / zalo (snapshot)
foxia_source_id  BigInteger null         # ô đã tra; null = chưa cấu hình
push_status      pending | ok | failed | skipped_no_mapping
http_status      Int null
response_body    Text (cắt 2000, như FoxiaOrderPush.RESPONSE_LIMIT)
error            Text
pushed_at        DateTime null
cohoi_id         BigInteger null db_index
khachhang_id     BigInteger null

UNIQUE (channel, phone)                      # khoá chống trùng D3 — ở DB, không ở code
INDEX (page, -created_at), (push_status, -created_at)
```

Ghi chú: `phone` unique theo kênh nên một khách cho lại số ở hội thoại khác cùng kênh
→ `get_or_create` trả về dòng cũ, không đẩy lại, không ghi thêm dòng (cố ý — D3).

---

## 5. Backend

### 5.1 Client — `apps/pancake/infrastructure/pancake_client.py`
Dựng trên `apps.common.http_client.HttpClient`, timeout 15s, `retries=2` (GET idempotent;
POST sinh token cũng idempotent — sinh lại token mới, không sinh dữ liệu).
- `list_pages() -> list[PageDTO]`
- `generate_page_token(page_id) -> str` — raise `PancakeTokenError(message)` khi
  `success=false` (mang nguyên `message` của Pancake để hiện lên UI).
- `list_conversations(page_id, token, since, until, page_number) -> ConversationPage`
  (`total`, `items[]`).
- `get_conversation_detail(page_id, token, conv_id) -> ConversationDetail`
  (`customers[].recent_phone_numbers` đã ép về `list[str]`, `messages[]` với
  `has_phone`, `inserted_at`).
- Parser `recent_phone_numbers` chấp nhận cả chuỗi lẫn object (`phone_number` / `number`).
- Token page bị từ chối (HTTP 401/403 hoặc `success=false` kèm thông báo token) →
  raise `PancakePageTokenRejected`.

### 5.2 Chuẩn hoá số điện thoại — `apps/pancake/domain/phone.py`
Hàm thuần `normalize_phone(raw) -> str | None`: bỏ mọi ký tự không phải số, `+84`/`84`
đầu → `0`, hợp lệ khi đúng 10 chữ số bắt đầu bằng `0`; sai → `None` (bỏ qua, đếm
`skipped`). Tái dùng hàm chuẩn hoá sẵn có của repo nếu đã có (kiểm tra `apps.common`
trước khi viết mới).

### 5.3 Service poll — `apps/pancake/application/services/poll_service.py`
`PancakePollService(client).poll_page(page) -> PollResult(fetched, leads_created, pushed, skipped, error)`

Với mỗi page `is_enabled=True` có `page_access_token`:
1. `since = now − 30 ngày`, `until = now`, `page_number = 1…`; dừng khi gặp hội thoại
   có `updated_at < polled_until − 5 phút` hoặc hết trang. Vì Pancake trả DESC theo
   `updated_at`, bình thường mỗi lượt chỉ 1 request/page. `since` 30 ngày là mức cắt
   chấp nhận được: hội thoại tạo >30 ngày mới cho số sẽ lọt (ghi rõ trong docstring).
2. Bỏ hội thoại `has_phone=False`.
3. Với hội thoại `has_phone=True` chưa có lead nào (`conversation_id` chưa có trong
   `pancake_leads` với cùng `page`) **hoặc** đã có nhưng `updated_at` mới hơn
   `phone_given_at` của lead cũ: gọi `get_conversation_detail` (1 request) để lấy
   `customers[].recent_phone_numbers`, tên khách, và `phone_given_at` = `inserted_at`
   của **tin nhắn mới nhất có `has_phone=True`** trong 30 tin trả về; không tìm thấy →
   `updated_at` hội thoại.
   **Bổ sung 22/09/2026 — chỉ nhận số cho trong 24h gần nhất** (`PHONE_WINDOW`):
   `phone_given_at` (theo cách tính trên) cũ hơn `now − 24h` → đếm `skipped`, không
   tạo lead/đơn, cursor vẫn tiến. Lý do: với `order_by=updated_at` hội thoại cũ vừa có
   tin mới cũng về, mà `recent_phone_numbers` giữ cả số cho từ nhiều ngày trước → sale
   nhận lead cũ. Số gắn qua đơn POS (không có tin nhắn chứa số) rơi về `updated_at`
   nên vẫn nhận nếu hội thoại mới trong 24h (chốt phương án A). Giới hạn: endpoint chỉ
   trả 30 tin mới nhất — số cho từ lâu rồi có >30 tin sau đó sẽ không thấy tin có số,
   rơi về `updated_at` và lọt; hiếm, chấp nhận.
4. Mỗi số hợp lệ → `PancakeLead.objects.get_or_create(channel=page.channel, phone=…,
   defaults=…)`. Có sẵn → bỏ qua (D3). Tạo mới → tra
   `FoxiaLinkCell(link_channel__channel=page.channel, kind=page.foxia_kind)`:
   không có → `skipped_no_mapping`; có → gọi `push_customer` (§5.4) ngay, ghi kết quả.
5. Cursor: `polled_until = max(updated_at)` của lượt (chỉ khi lượt page đó không lỗi);
   `last_polled_at = now`. Lỗi → giữ cursor, ghi `last_error`, sang page kế.
6. `PancakePageTokenRejected` → sinh lại token **1 lần** trong lượt; vẫn lỗi →
   `is_enabled=False`, `token_error` = thông báo, `page_access_token = ""`.

Payload Foxia: `{"name": customer_name, "phone": phone, "address": "",
"message": f"Pancake: {page.name}"}`.

### 5.4 Tách hàm đẩy dùng chung — `apps/foxia/application/services/order_push_service.py`
Tách phần HTTP + đọc phản hồi thành hàm thuần:
```python
@dataclass
class PushResult:
    url: str; http_status: int | None; response_body: str  # đã cắt 2000
    error: str; duration_ms: int; ok: bool
    cohoi_id: int | None; khachhang_id: int | None

def push_customer(source_id: int, payload: dict) -> PushResult
```
`FoxiaOrderPushService.push` gọi hàm này rồi ghi `FoxiaOrderPush` + `FoxiaLeadLink` y
như cũ (hành vi không đổi; test hiện có phải xanh nguyên). `apps/pancake` import hàm
này — chiều phụ thuộc `pancake → foxia` là hợp lệ (foxia không biết pancake).

### 5.5 Task — `apps/pancake/tasks.py`
- `apps.pancake.tasks.poll_phone_leads`: beat mỗi **3 phút**, route vào
  `CELERY_FOXIA_QUEUE` (worker-foxia — I/O đối tác, không chiếm slot tạo đơn),
  `single_flight("lock:pancake_poll", 600)`, `soft_time_limit=150, time_limit=170`
  (bắt buộc override vì `CELERY_TASK_TIME_LIMIT` toàn cục = 30s).
- Gate bằng `PANCAKE_POLL_ENABLED` (env, mặc định tắt). Các thao tác chạy tay (lưu
  token, đồng bộ page, bật page, đẩy lại) **không** bị gate — thử được trước khi bật cron.
- `apps.pancake.tasks.repush_lead(lead_id)`: thân của nút Đẩy lại; queue
  `CELERY_FOXIA_PUSH_QUEUE`.

### 5.6 Metrics — `apps/pancake/metrics.py` (theo mẫu `foxia/metrics.py`)
`pancake_poll_pages_total{status=ok|error|token_rejected}`,
`pancake_leads_total{push_status}`, `pancake_poll_duration_seconds`.

### 5.7 API — `apps/pancake/transport/views.py`, prefix `/api/pancake/`
Tất cả **chỉ superuser** (tái dùng mẫu `superuser_only` của `foxia/transport/link_config_views.py`)
trừ nhóm leads dùng RBAC `pancake-leads:view` (superuser có mặc định).

| Method | Path | Việc |
|---|---|---|
| GET | `connection` | `{has_token, auth_status, auth_error, last_pages_synced_at}` — không bao giờ trả token |
| PUT | `connection` | body `{access_token}` → lưu, gọi `list_pages` ngay để xác thực; lỗi → `auth_status=error` + 400 kèm thông báo |
| POST | `pages/sync` | upsert `PancakePage` theo `pancake_page_id`; page không còn trong danh sách → `is_activated=False` (không xoá) |
| GET | `pages` | danh sách page + kênh + trạng thái poll/token |
| PATCH | `pages/<id>` | `{channel_id?, is_enabled?}`. Bật cần có kênh và `foxia_kind` khác None (400 nếu không); bật → sinh token page (lỗi → 400 nguyên thông báo Pancake, không bật), `polled_until = now`. Tắt → giữ token + cursor; bật lại → `polled_until = now` |
| GET | `leads` | phân trang `limit/offset` → `{items,total,limit,offset}` (chuẩn repo), lọc `page`, `channel`, `push_status`, `q` (sđt/tên), `date_from`/`date_to` theo `created_at`; sort `-created_at` |
| POST | `leads/<id>/repush` | chỉ khi `failed`/`skipped_no_mapping`; tra lại ô rồi xếp `repush_lead`; 409 nếu trạng thái khác |

### 5.8 Django admin
Đăng ký 3 model; `PancakeLead` list_display có `response_body`/`error` để soi nhật ký thô.

### 5.9 RBAC
Section mới `pancake-leads` với action `view` — thêm vào `VALID_SECTIONS`
(`users/models.py`) và `PERMISSION_CATALOG` (`users/rbac_catalog.py`); migration
`users.0049` seed quyền. Trang Cài đặt → Pancake gate bằng superuser như `foxia-link`.

---

## 6. Frontend (feature-slice `frontend/src/features/pancake/`)

Đăng ký route/module như `foxia-link` (`lib/routes.ts`, `app/modules.ts`, `app/routes.tsx`,
`routed-pages.tsx`, i18n `topnav.tabs.*`).

### 6.1 `/settings/pancake` — `PancakeSettingsPage` (nhóm Cài đặt, icon Plug)
- Khối **Kết nối**: ô token (type password, để trống = giữ nguyên), nút *Lưu & kiểm tra*,
  dòng trạng thái (`auth_status`, lỗi, lần đồng bộ page cuối).
- Nút **Đồng bộ page**.
- Bảng page: Tên · Platform · Kênh bán (select kênh active; khoá nếu platform không hỗ
  trợ, tooltip lý do) · Bật (switch; disabled khi chưa chọn kênh) · Poll lần cuối ·
  Lỗi (token_error / last_error, rút gọn + tooltip). Page `is_activated=False` hiện mờ.

### 6.2 `/pancake-leads` — `PancakeLeadsPage` (nhóm Bán hàng, quyền `pancake-leads:view`)
Cột theo thứ tự chủ dự án chỉ định (2026-09-16):

| Cột | Nguồn |
|---|---|
| Page Pancake | `[pancake_page_id] name` |
| Tên khách hàng | `customer_name` |
| Ngày tạo hội thoại | `conversation_inserted_at` |
| Số điện thoại | `phone` |
| Thẻ Pancake | `tags` (chip) |
| Ngày tạo | `created_at` |
| Ngày cho số điện thoại | `phone_given_at` |
| Nhân viên Pancake | `assignee_name` |
| Đẩy Foxia *(cột thêm của ta)* | badge `push_status` + `cohoi_id`; nút **Đẩy lại** khi failed/skipped_no_mapping |

Bộ lọc: page, kênh, trạng thái đẩy, tìm sđt/tên, khoảng ngày (`created_at`). Phân trang
server. Giờ hiển thị theo `Asia/Ho_Chi_Minh` như các trang khác.

---

## 7. Vận hành & triển khai
- Env mới: `PANCAKE_POLL_ENABLED` (mặc định `0`), `PANCAKE_HTTP_TIMEOUT` (15).
- Beat entry `pancake-poll-phone-leads` trong `config/settings_parts/celery.py`
  (code là nguồn chân lý; `django_celery_beat` cập nhật từ dict).
- Deploy: migration `pancake.0001`, `users.0049`; cấp `pancake-leads:view` cho role cần;
  nhập token ở UI → Đồng bộ page → gán kênh + bật; kiểm tra ô `(kênh, messenger/zalo)`
  đã điền ở Cấu hình liên kết Foxia; cuối cùng bật `PANCAKE_POLL_ENABLED=1` và
  `up -d worker-foxia beat` (env nướng vào container lúc tạo).
- Rủi ro đã biết: hội thoại tạo >30 ngày mới cho số → lọt (chấp nhận); Pancake không
  cấp expiry cho token page — coi là dài hạn, sinh lại khi bị từ chối.

---

## 8. Kiểm thử
Backend (pytest, chạy bằng container test theo memory `running-backend-tests`):
- `normalize_phone`: `+84…`, `84…`, có khoảng trắng/dấu chấm, sai độ dài → None.
- Parser `recent_phone_numbers` 2 dạng (chuỗi / object).
- `poll_page`: dừng đúng theo cursor; bỏ `has_phone=False`; tạo lead + đẩy; trùng
  (channel, phone) → không đẩy lại; `skipped_no_mapping`; lỗi Foxia → `failed`, cursor
  vẫn tiến; lỗi Pancake → cursor giữ nguyên; token bị từ chối → sinh lại 1 lần rồi tắt page.
- `phone_given_at` lấy từ tin nhắn `has_phone` mới nhất, fallback `updated_at`.
- API: PUT connection xác thực token; PATCH bật page thiếu kênh/platform sai → 400; sinh
  token lỗi → 400 + không bật; repush sai trạng thái → 409; leads list lọc + phân quyền.
- `push_customer` tách ra: toàn bộ test `foxia` hiện có xanh nguyên.

Frontend (vitest, chạy một mình theo memory `dev-workspace-gotchas`): render 2 trang, lưu
token, gán kênh + bật page (kể cả nhánh lỗi 400 hiện thông báo), bảng lead + nút Đẩy lại.

---

## 9. Sai lệch khi triển khai (2026-09-17)

- Lỗi `PancakeError` ở chi tiết MỘT hội thoại không giữ cursor nữa — rơi về dữ liệu cấp
  hội thoại, log `conversation_id`.
- Client bọc `requests.RequestException` thành `PancakeError` không kèm URL/token.
- View PATCH page kiểm tra điều kiện (kênh/platform) trước token, dùng chung `check_eligible`.
- FE luôn refetch sau mọi thao tác kể cả lỗi.
- `GET leads` phân trang `limit/offset`.
- (f) Trang Lead Pancake đặt trong nhóm sidebar CSKH (`cskh`, cùng basePath `/sales`) thay
  vì nhóm Bán hàng.
- (g) `GET /api/pancake/pages` mở cho quyền `pancake-leads:view` để trang lead lọc theo
  page; lead `pending` đẩy lại được.
- (h) 2026-09-17 — Lead KHÔNG còn đẩy thẳng sang Foxia. Poll tạo `PancakeLead` xong thì
  gọi `apps.pancake.application.services.lead_order_service.create_order_for_lead`, đi
  qua `OrderService.create_order_from_landing` (đường đơn thường, `order_source` lấy
  snapshot `lead.foxia_kind` — `messenger` hoặc `zalo` tuỳ nền tảng page lúc bắt số,
  rơi về `messenger` khi lead cũ thiếu snapshot) — đơn tự khớp/tạo khách theo sđt, chạy
  contact-flow, và tự đẩy Foxia theo đúng ô (kênh, messenger) — hoặc (kênh, zalo) với
  page Zalo — như mọi đơn khác; nhật ký đẩy Foxia của lead xem ở `foxia_order_pushes`
  của chính đơn đó, không còn nằm trên `PancakeLead`.
  - Gán sale: bảng `PancakeStaffMap` (`pancake_staff_maps`) ánh xạ tên nhân viên Pancake
    (`assignee_name` của hội thoại) → user hệ thống. Poll gặp tên mới tự tạo dòng map
    (`user=None`, cập nhật `last_seen_at`) — người vận hành gán `user` sau qua Cài đặt →
    Pancake → Nhân viên Pancake. Tên rỗng, chưa gán, hoặc user không còn hợp lệ (nghỉ
    việc, admin, đã xoá mềm — cùng luật `resolve_ingest_staff_id` của ingest landing) →
    không set `staff_id` trong payload, đơn rơi về bộ chia tự động như landing thường.
  - Tên khách: khách CŨ (đã có `Customer` khớp theo sđt, tên thật khác placeholder
    "Unknown") giữ nguyên TÊN THẬT đã có, không cho tên Pancake (thường là nickname
    Facebook, ví dụ "Mẹ Bống") ghi đè. Khách MỚI (chưa có `Customer`, hoặc `Customer`
    còn "Unknown") dùng tên Pancake trong hội thoại; KHÔNG có tên (Pancake không cho
    tên) → lấy SỐ ĐIỆN THOẠI làm tên — fallback này chỉ áp dụng cho khách MỚI không
    tên, không bao giờ đè lên tên thật của khách cũ ở lượt sau.
  - Trùng đơn trong ngày cùng kênh (`DuplicateOrderError` từ `claim_or_reject`, chạy BÊN
    TRONG `create_order_from_landing`) không phải lỗi: lead gắn `order` vào đơn có sẵn,
    `push_status=order_exists`. Số bị blocklist/không hợp lệ (`LeadFilterService.
    evaluate`) bị chặn TRƯỚC khi gọi `create_order_from_landing` →
    `push_status=order_failed` kèm lý do trong `error`, không thử tạo đơn.
  - Trạng thái lead (`PancakePushStatus`) thêm `order_created` / `order_failed` /
    `order_exists`; các trạng thái cũ (`pending/ok/failed/skipped_no_mapping`) giữ lại
    chỉ để đọc lịch sử lead trước 17/09 (đẩy thẳng Foxia, không qua `orders`) — lead cũ
    có `push_status=ok` KHÔNG được tạo đơn bù khi retry.
  - Task Celery `repush_lead` đổi tên thành `retry_lead_order`; endpoint
    `POST leads/<id>/repush` đổi thành `POST leads/<id>/retry-order`. FE: nút "Đẩy lại"
    đổi thành "Tạo đơn" — hiện cho lead `pending`/`failed`/`skipped_no_mapping`/
    `order_failed` (chưa có `order_id`, kể cả lead cũ trước 17/09 ở trạng thái
    `pending/failed/skipped_no_mapping`), ẩn khi lead đã có `order_id`
    (`order_created`/`order_exists`) hoặc `push_status=ok`.
  - Migration: `pancake.0002_staff_map_and_lead_order` (bảng `PancakeStaffMap`, cột
    `PancakeLead.order`), `pancake.0003_alter_pancakelead_push_status` (thêm 3 choice
    mới cho `push_status`).

- **(i) 2026-09-17 — không lọc `has_phone`:** poll gọi chi tiết cho MỌI hội thoại vừa cập nhật; số lấy gộp từ hồ sơ khách (kể cả số nhân viên gắn qua đơn Pancake POS, dạng `{order_id, phone_number}`, không bật `has_phone`), `recent_phone_numbers` và `conv_phone_numbers` cấp trên cùng của endpoint tin nhắn. Đo 17/09: 9/27 số nhập tay chỉ thấy được bằng cách này; ~500 request chi tiết/ngày. Số không có ở Pancake (page chưa bật/tài khoản khác) vẫn không kéo được.
