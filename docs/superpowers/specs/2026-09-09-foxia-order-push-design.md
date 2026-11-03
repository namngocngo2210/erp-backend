# Cấu hình liên kết Foxia + đẩy đơn landing sang Foxia

Ngày: 2026-09-09 · Nhánh: `feat/foxia-order-push` · Worktree: `.claude/worktrees/feat-foxia-push`

> **Cập nhật 2026-09-16 (nhánh `feat/foxia-link-by-source`):** bảng liên kết
> đổi trục. Hàng = kênh bán, cột = 5 loại nguồn cố định do chủ hệ thống tạo bên
> Foxia (`landing / messenger / hotline / zalo / resale`, `FoxiaLinkKind`, giá
> trị trùng `OrderSource`). Bỏ trục SKU (`FoxiaLinkProduct`) và ô "Mặc định"
> (`default_source_id`); toàn bộ id cũ bị xoá ở migration `foxia.0007`, danh
> sách kênh giữ lại. Đơn (vẫn chỉ qua landing ingest) tra ô
> `(kênh, order_source)`; nguồn không có cột (koc, shopee…) → bỏ qua. Payload
> thêm `message` = SKU các dòng hàng (bỏ trường khi đơn không có dòng hàng).
> Các mục §4.2–4.3, §5 bên dưới mô tả bản cũ.
>
> **Cập nhật 2026-09-17 (nhánh `feat/foxia-push-manual-landing`):** mở rộng phạm vi
> đẩy cho **đơn tạo tay ở trang landing** (nút tạo đơn gửi `show_orders=False`) khi
> `order_source ∈ {landing, messenger, zalo, hotline}` — `create_order_manual` gọi
> `_schedule_foxia_push` cùng cơ chế on_commit/queue/cờ như ingest. Đơn tạo ở trang
> Đơn hàng chính, KOC, sửa đơn, import Excel vẫn không đẩy. Danh sách nguồn khai ở
> `orders.FOXIA_MANUAL_PUSH_SOURCES` (test chốt khớp `FoxiaLinkKind` trừ `resale`).

## 1. Bối cảnh

`apps/foxia` hiện tại là tích hợp **một chiều đọc**: kéo danh mục + đơn từ Foxia
về để dựng báo cáo hiệu quả marketing (`docs/foxia-integration-design.md`). Docstring
`apps/foxia/transport/api_views.py` ghi rõ "Không có endpoint nào ghi sang Foxia".

Spec này thêm **chiều ghi đầu tiên**: khi một đơn từ landing page được lưu thành
công, đẩy thông tin khách sang Foxia để Foxia ghi nhận khách hàng theo đúng
**nguồn khách hàng** (`idnguonkhachhang`) mà chủ hệ thống chỉ định.

Nguồn nào được chọn phụ thuộc **cặp (kênh bán × sản phẩm)** của đơn — quan hệ này
không suy ra được từ dữ liệu sẵn có, phải do người vận hành khai báo bằng tay.
Vì vậy cần một trang cấu hình dạng bảng n×m.

## 2. Phạm vi

**Trong phạm vi**
- Trang **Cài đặt → Cấu hình liên kết Foxia**: bảng n×m (cột = kênh bán, hàng =
  sản phẩm theo SKU, ô = `idnguonkhachhang` của Foxia), thêm/bỏ được cả hàng lẫn cột.
- Đẩy đơn **từ landing page** sang Foxia qua Celery sau khi đơn commit.
- Container worker mới `worker-foxia`.
- Nhật ký từng lần đẩy, xem qua Django admin.

**Ngoài phạm vi (cố ý)**
- Đơn tạo tay, đơn sửa, import Excel, webhook KiotViet — chủ hệ thống chốt chỉ
  đẩy đơn landing.
- Retry khi Foxia lỗi — chốt "thử một lần, ghi log".
- Trang FE xem nhật ký đẩy — dùng Django admin.
- Đọc lại id đơn/lead Foxia trả về — Foxia hiện chưa trả, sẽ bổ sung sau.

## 3. Hợp đồng phía Foxia (do chủ hệ thống cung cấp)

- `POST {base}/{idnguonkhachhang}` — mặc định base là
  `https://desiglobal.foxia.vn/duongdankhachhang`
- Content-Type: `application/json`. **Không xác thực** — gọi thẳng.
- Body đầy đủ mà Foxia nhận:
  ```json
  {"name": "...", "phone": "...", "address": "...", "message": "...",
   "facebook": "...", "email": "...", "utm_id": "...", "utm_content": "...", "link": "..."}
  ```
  **Ta chỉ gửi ba trường**: `name`, `phone`, `address`. Không gửi trường rỗng cho
  phần còn lại — gửi chuỗi rỗng và không gửi là hai ý nghĩa khác nhau với bên nhận,
  và ta không có dữ liệu thật cho chúng.
- Phản hồi: chưa có hợp đồng. Ta coi **HTTP 2xx = thành công**, mọi mã khác =
  thất bại, và lưu nguyên văn phần thân phản hồi (cắt bớt) để đối chiếu sau.

## 4. Model

Tất cả thêm vào `backend/apps/foxia/models.py`, mục 5 mới ("Đẩy đơn sang Foxia").

Bảng n×m tách làm **ba** bảng thay vì một bảng ô đơn thuần. Lý do: "thêm một cột
rồi mới điền dần" phải làm được, nghĩa là cột tồn tại độc lập với việc có ô nào
đã điền hay chưa. Tách ra còn khiến "bỏ một cột thì xoá các id trong cột đó" trở
thành `ON DELETE CASCADE` của Postgres, không phải một vòng dọn dẹp trong service
có thể quên.

### 4.1 `FoxiaLinkChannel` — cột của bảng

```
db_table = "foxia_link_channels"
channel     FK channels_core.Channel, on_delete=CASCADE, unique
sort_order  PositiveIntegerField(default=0)
created_at  auto_now_add
Meta.ordering = ["sort_order", "id"]
```

`unique` trên `channel`: một kênh chỉ là một cột. Kênh bị xoá khỏi hệ thống thì
cột biến mất theo (CASCADE) — cùng với các ô của nó qua CASCADE tầng hai.

### 4.2 `FoxiaLinkProduct` — hàng của bảng

```
db_table = "foxia_link_products"
product     FK catalog.Product, on_delete=CASCADE, unique
sort_order  PositiveIntegerField(default=0)
created_at  auto_now_add
Meta.ordering = ["sort_order", "id"]
```

### 4.3 `FoxiaLinkCell` — ô (kênh × sản phẩm) → id nguồn khách hàng

```
db_table = "foxia_link_cells"
link_channel     FK FoxiaLinkChannel, on_delete=CASCADE, related_name="cells"
link_product     FK FoxiaLinkProduct, on_delete=CASCADE, related_name="cells"
foxia_source_id  BigIntegerField          # id điền tay, đi thẳng vào URL
updated_by       FK settings.AUTH_USER_MODEL, on_delete=SET_NULL, null=True
created_at       auto_now_add
updated_at       auto_now
constraints: UniqueConstraint(link_channel, link_product) name="foxia_link_cell_uniq"
indexes: Index(link_product) name="foxia_link_cell_prod_idx"
```

`foxia_source_id` **không** FK sang `FoxiaSource`: người vận hành phải điền được
id mà mirror danh mục chưa kịp đồng bộ về, và một FK cứng sẽ chặn đúng lúc cần
nhất. Đổi lại API validate `> 0` và FE hiển thị tên nguồn khi tra được trong
`foxia_sources` (chỉ để tham khảo, không chặn lưu).

Tra cứu lúc đẩy đơn đi qua `link_channel__channel_id` + `link_product__product_id`;
`foxia_link_cell_uniq` phục vụ chiều `link_channel` trước, `foxia_link_cell_prod_idx`
phục vụ chiều còn lại.

### 4.4 `FoxiaOrderPush` — nhật ký từng lần đẩy

```
db_table = "foxia_order_pushes"
order            FK orders.Order, on_delete=CASCADE, related_name="foxia_pushes"
status           CharField(16, choices=FoxiaPushStatus)
foxia_source_id  BigIntegerField(null=True, blank=True)
url              CharField(500, blank=True, default="")
request_body     JSONField(null=True, blank=True)
http_status      IntegerField(null=True, blank=True)
response_body    TextField(blank=True, default="")   # cắt 2000 ký tự
error            TextField(blank=True, default="")
duration_ms      IntegerField(null=True, blank=True)
created_at       auto_now_add, db_index
indexes: Index(order, -created_at) name="foxia_push_order_idx"
         Index(status, -created_at) name="foxia_push_status_idx"
```

`FoxiaPushStatus` (TextChoices):
- `ok` — HTTP 2xx
- `failed` — HTTP không phải 2xx, timeout, hoặc lỗi mạng
- `skipped_no_product` — đơn không có dòng hàng nào **và** kênh không có id mặc định
- `skipped_no_mapping` — cặp (kênh × SKU) chưa được cấu hình id **và** kênh không có id mặc định

Mỗi lần task chạy ghi **đúng một** dòng, kể cả nhánh bỏ qua — đó là cách người
vận hành thấy được phần bị bỏ thay vì phải suy ra từ sự vắng mặt.

`response_body` cắt còn 2000 ký tự: Foxia chưa chốt hợp đồng phản hồi, có thể trả
nguyên một trang HTML lỗi; bảng này ghi mỗi đơn một dòng nên không được phép phình.

## 5. Luồng đẩy đơn

### 5.1 Điểm móc

`OrderServiceCommands.create_order_from_landing`
(`backend/apps/orders/application/services/order_service_commands.py`), ngay sau
`OrderServiceCommands._schedule_vat_invoice_notification(order)` và trước khối
`LeadFilterService.register_claim`:

```python
OrderServiceCommands._schedule_foxia_push(order)
```

Hàm nằm trong `@transaction.atomic`, và helper chỉ gọi
`transaction.on_commit(...)` → đơn rollback thì không có gì được đẩy. Đây cũng là
đúng vết của `_push_event_on_commit` / `_schedule_lark_notification` đã có ở đó.

⚠️ Helper nằm trong `apps.orders`, **không** trong `apps.foxia`: `.importlinter`
xếp `foxia` ở tầng TRÊN `orders` với `exhaustive = true`, nên `orders` import
`apps.foxia` là CI đỏ. Việc enqueue đi qua `apps.dispatch` (tầng dưới `orders`),
với `"foxia.push_order"` đã đăng ký trong `apps/dispatch/registry.py` — cùng cơ
chế `_schedule_lark_notification` đang dùng. Bản spec đầu kê một lời gọi trực
tiếp sang `apps.foxia`; đó là lỗi của spec, phát hiện ở review toàn nhánh.

Chỉ móc vào `create_order_from_landing`. **Không** móc vào `create_order_manual`,
`update_order_inline`, import Excel hay webhook KiotViet.

### 5.2 `OrderServiceCommands._schedule_foxia_push(order)`

`backend/apps/orders/application/services/order_service_commands.py`

```python
@staticmethod
def _schedule_foxia_push(order) -> None:
    if not getattr(settings, "FOXIA_PUSH_ENABLED", False):
        return
    order_id = order.id

    def _enqueue() -> None:
        try:
            from apps.dispatch import dispatch

            dispatch("foxia.push_order",
                     queue=settings.CELERY_FOXIA_PUSH_QUEUE, order_id=order_id)
        except Exception:  # noqa: BLE001 — enqueue hỏng không được phá đơn đã lưu
            logger.exception("Foxia push: enqueue thất bại (order_id=%s)", order_id)

    transaction.on_commit(_enqueue)
```

Chỉ truyền `order_id`, không truyền payload dựng sẵn: task đọc lại từ DB sau khi
commit nên không có nguy cơ đọc state bị mutate, và tra mapping ở worker giữ
đường tạo đơn nhẹ. Bọc `try/except Exception` + `logger.exception` ở call site
để lỗi enqueue không phá luồng tạo đơn (cùng vết `register_claim` ngay bên dưới).

### 5.3 Task `apps.foxia.tasks.push_order_to_foxia(order_id: int)`

```python
@shared_task(name="apps.foxia.tasks.push_order_to_foxia",
             soft_time_limit=25, time_limit=30, max_retries=0)
```

Không override time limit lên cao: một request HTTP 10s không cần hơn trần
mặc định 30s của `CELERY_TASK_TIME_LIMIT`. `max_retries=0` — chốt "thử một lần".

Các bước:

1. `FOXIA_PUSH_ENABLED` tắt → return `"disabled"`, **không** ghi log DB
   (task còn kẹt trong hàng đợi lúc tắt cờ không được đẻ rác).
2. Lấy `Order` theo `order_id`, `select_related("channel")`. Không có → return.
3. Lấy dòng hàng **đầu tiên**: `order.items.order_by("id").first()`.
   Không có → ghi `skipped_no_product`, return.
   *Quy tắc "dòng đầu tiên" là quyết định của chủ hệ thống. Trên prod 30 ngày,
   97,8% đơn chỉ có 1 SKU; ~194 đơn/tháng có nhiều SKU và sẽ lấy id của dòng
   nhập trước.*
4. Tra ô:
   ```python
   FoxiaLinkCell.objects.filter(
       link_channel__channel_id=order.channel_id,
       link_product__product_id=item.product_id,
   ).values_list("foxia_source_id", flat=True).first()
   ```
   Không có (hoặc đơn không có dòng hàng) → tra `FoxiaLinkChannel.default_source_id`
   của kênh — ô hàng "Mặc định" trên trang cấu hình (bổ sung 2026-09-11). Có → đẩy
   như bình thường, log thêm `mac_dinh=1`. Không có nốt → ghi `skipped_no_mapping`
   (hoặc `skipped_no_product` nếu không có dòng hàng), return. Kênh chưa có cột
   trong bảng thì không có gì để rơi về.
5. Dựng payload (§5.4), `POST` với `timeout=(5, 10)` (connect, read).
6. Ghi một dòng `FoxiaOrderPush` với kết quả. HTTP 2xx → `ok`, còn lại → `failed`.
7. **Không raise** trong mọi trường hợp lỗi từ phía Foxia: lỗi đối tác không
   được làm bẩn hàng đợi hay sinh cảnh báo Celery. Lỗi ghi ở mức `logger.warning`
   kèm `order_id`, và đã có dòng DB làm bằng chứng.

### 5.4 Payload

```python
{
  "name":    (order.name or "").strip(),
  "phone":   (order.phone or "").strip(),
  "address": full_address,
}
```

`full_address` = ghép `", "` các phần **không rỗng** theo thứ tự
`address`, `ward`, `district`, `province`. Nếu cả bốn đều rỗng thì dùng
`order.raw_address.strip()`. Nếu vẫn rỗng thì gửi chuỗi rỗng — vẫn đẩy, vì
đơn landing chưa có địa chỉ là chuyện bình thường (`Order.has_address`) và Foxia
cần biết khách này tồn tại.

`name`/`phone` rỗng **không** chặn đẩy: đơn landing luôn có SĐT trên thực tế, và
việc suy đoán thay bên nhận không phải việc của lớp này.

## 6. Queue và container `worker-foxia`

### 6.1 Queue

Thêm vào `backend/config/settings_parts/celery.py`:

```python
CELERY_FOXIA_PUSH_QUEUE = os.getenv("CELERY_FOXIA_PUSH_QUEUE", "foxia_push")
...
CELERY_TASK_ROUTES["apps.foxia.tasks.push_order_to_foxia"] = {"queue": CELERY_FOXIA_PUSH_QUEUE}
```

Queue **riêng**, không dùng chung `foxia`: queue đó ôm `sync_catalog` /
`sync_orders` / `sync_delivered` với `time_limit=900`. Đẩy đơn là việc ngắn đi
sau một lượt backfill 15 phút thì vô nghĩa.

### 6.2 Registry `dispatch`

`backend/apps/dispatch/registry.py`, mục Foxia mới:

```python
"foxia.push_order": _lazy("apps.foxia.tasks", "push_order_to_foxia"),
```

### 6.3 Container

`docker-compose.prod.yml`:
- `worker-slow`: đổi `-Q slow,foxia` → `-Q slow`.
- Thêm service `worker-foxia`, dựng theo khuôn `worker-export`:
  - `container_name: desi-worker-foxia`
  - `DB_APPLICATION_NAME: desi-worker-foxia`
  - `exec celery -A config worker -Q foxia,foxia_push -l ${DJANGO_LOG_LEVEL:-INFO} --concurrency=${FOXIA_WORKER_CONCURRENCY:-1}`
    — **ruling controller (review 2026-09-09, I6)**: mặc định hạ từ 2 xuống 1.
    Host chỉ còn ~2.2GB khả dụng + 2.5GB swap đã dùng trên 31 container; trước đây
    backfill `foxia` CHIA hai slot với job của `slow`, chưa từng SỞ HỮU nguyên hai
    slot, và `foxia_push` chỉ là một request HTTP ngắn — không có lý do giữ 2.
  - `mem_limit: 1024m` + `CELERY_WORKER_MAX_MEMORY_PER_CHILD:
    ${FOXIA_WORKER_MAX_MEMORY_PER_CHILD:-358000}` — **giữ nguyên bao bọc bộ nhớ
    mà queue `foxia` đang có ở `worker-slow`**: container này tiếp quản luôn
    backfill (`time_limit=900`), hạ xuống 512m + thừa kế 180000 của anchor là âm
    thầm thu nhỏ tài nguyên của job đang chạy tốt. Ngưỡng recycle 358000 tính cho
    concurrency 2 vẫn giữ NGUYÊN dù mặc định concurrency hạ còn 1 — ruling I6 cố
    ý không tính lại công thức, xem comment ở service `worker-foxia`.
  - cùng `depends_on` / `networks` / `restart` / healthcheck như các worker khác.

`docker-compose.staging.yml`: cùng thay đổi, `--concurrency=1`, không đặt
`mem_limit` (staging không đặt cho worker nào). Staging cố ý giữ đúng topology
của prod để lỗi định tuyến queue lộ ra ở đó trước.

`docker-compose.local.yml`: worker local (dòng 311) nghe gộp `default,orders,kiotviet,lark_noti,audit,slow` — thêm
`foxia,foxia_push` vào danh sách của nó thay vì dựng container thứ hai.

Ghi chú triển khai (đưa vào phần mô tả MR): sau khi deploy phải
`docker compose -f docker-compose.prod.yml up -d worker-slow worker-foxia`
để `worker-slow` nhả queue `foxia` và container mới nhận nó.

### 6.4 Cấu hình `.env`

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `FOXIA_PUSH_ENABLED` | `0` | Công tắc tổng. Mặc định **tắt** — bật sau khi bảng mapping đã điền. |
| `FOXIA_PUSH_BASE_URL` | `https://desiglobal.foxia.vn/duongdankhachhang` | Phần trước `/{id}`. Bỏ dấu `/` thừa ở cuối khi dựng URL. |
| `FOXIA_PUSH_TIMEOUT` | `10` | Giây, dùng cho read timeout. |
| `CELERY_FOXIA_PUSH_QUEUE` | `foxia_push` | |
| `FOXIA_WORKER_CONCURRENCY` | `1` | Hạ từ 2 xuống 1 (ruling controller, review 2026-09-09 mục I6) — xem 6.3. |

Đặt trong `config/settings_parts/` cùng chỗ các cờ Foxia hiện có, và thêm vào
`.env.example`.

## 7. API

`backend/apps/foxia/transport/link_config_views.py` (file mới — `api_views.py`
đã 210 dòng và phục vụ báo cáo, trộn vào là hai trách nhiệm một chỗ), đăng ký
trong `apps/foxia/urls.py` dưới tiền tố sẵn có `^api/foxia/`.

Toàn bộ endpoint dưới đây: `@api_view` + `IsAuthenticated` + chặn
`resolve_effective_permissions(request.user).is_superuser` (`apps/users/rbac.py`) — trả `403` nếu không
phải superuser. Không thêm section RBAC mới cho quyền ghi.

| Method | Path | Việc |
|---|---|---|
| `GET` | `/api/foxia/link-config` | Trả toàn bộ bảng trong một request |
| `POST` | `/api/foxia/link-config/channels` | `{"channel_ids": [int]}` → thêm cột |
| `DELETE` | `/api/foxia/link-config/channels/<int:pk>` | Bỏ cột (CASCADE xoá ô) |
| `POST` | `/api/foxia/link-config/products` | `{"product_ids": [int]}` → thêm hàng |
| `DELETE` | `/api/foxia/link-config/products/<int:pk>` | Bỏ hàng (CASCADE xoá ô) |
| `PUT` | `/api/foxia/link-config/cells` | Lưu hàng loạt ô đã sửa |

`GET /api/foxia/link-config` trả:

```json
{
  "channels": [{"id": 1, "channel_id": 12, "code": "fb-a", "name": "Facebook A", "sort_order": 0}],
  "products": [{"id": 1, "product_id": 34, "sku": "SKU-01", "name": "Máy massage", "sort_order": 0}],
  "cells": [{"link_channel_id": 1, "link_product_id": 1, "foxia_source_id": 1234,
             "source_name": "FB - Máy massage"}]
}
```

`source_name` tra từ `foxia_sources` theo `foxia_id`, rỗng nếu không có — chỉ để
người dùng tự kiểm tra mình gõ đúng id, không tham gia vào logic nào.

`POST channels/products`: bỏ qua id đã có mặt (idempotent), `sort_order` =
`max + 1` theo thứ tự truyền vào. Trả về danh sách mục vừa thêm.

`DELETE`: trả `{"deleted_cells": <n>}`. FE gọi trước một lần `GET` để biết số ô
sẽ mất và hỏi xác nhận; con số trong phản hồi là con số thật đã xoá.

`PUT cells` nhận:
```json
{"cells": [{"link_channel_id": 1, "link_product_id": 1, "foxia_source_id": 1234},
           {"link_channel_id": 1, "link_product_id": 2, "foxia_source_id": null}]}
```
`foxia_source_id` là số `> 0` → upsert (ghi `updated_by`); `null` → xoá ô nếu có.
Toàn bộ trong một `transaction.atomic`. Validate: mọi `link_channel_id` /
`link_product_id` phải tồn tại, `foxia_source_id` phải là số nguyên dương —
sai một ô thì **cả lô** trả `400` và không ghi gì (bảng cấu hình lưu nửa vời
nguy hiểm hơn là không lưu).

## 8. Django admin

`backend/apps/foxia/admin.py`:
- `FoxiaOrderPushAdmin` — `list_display = (order, status, foxia_source_id, http_status, duration_ms, created_at)`,
  `list_filter = (status, created_at)`, `search_fields = (order__id, order__phone)`,
  toàn bộ readonly, `has_add_permission = has_change_permission = False`.
- `FoxiaLinkChannelAdmin` / `FoxiaLinkProductAdmin` / `FoxiaLinkCellAdmin` — đăng
  ký đơn giản để đối soát, đường sửa chính là trang FE.

## 9. Frontend

### 9.1 Định tuyến & menu

- `backend/apps/users/models.py`: thêm `"foxia-link"` vào `VALID_SECTIONS`.
  **Không** thêm vào `PERMISSION_CATALOG` — cùng khuôn với `media-*`: superuser
  nhận `list(VALID_SECTIONS)` nên thấy menu, role thường không gán được nên không thấy.
- `frontend/src/lib/routes.ts`: thêm `"foxia-link"` vào `AppSection` và
  `SECTION_PATHS["foxia-link"] = "/settings/foxia-link"`.
- `frontend/src/app/modules.ts`: trong module `settings`, thêm
  `{ key: "foxia-link", labelKey: "topnav.tabs.foxiaLink", icon: Plug }` — đặt
  ngay sau `kiotviet`.
- `frontend/src/app/routes.tsx`: route `settings/foxia-link` bọc `GuardSuperuser`
  (đúng khuôn `settings/kiotviet`), trang lazy-load qua `routed-pages.tsx`.
- i18n: `topnav.tabs.foxiaLink` = "Cấu hình liên kết Foxia" (vi) / "Foxia link
  configuration" (en), cùng các key của trang.

### 9.2 Trang `FoxiaLinkSettingsPage`

`frontend/src/features/foxia-link/` — thư mục mới, cùng khuôn `features/kiotviet`
(`pages/`, `queries.ts`, `__tests__/`); client API ở `frontend/src/lib/api/foxia-link.ts`.

Bố cục:
- Tiêu đề + một dòng giải thích: id điền vào ô là `idnguonkhachhang` của Foxia,
  dùng để dựng URL đẩy đơn.
- Thanh công cụ: **＋ Thêm kênh**, **＋ Thêm sản phẩm**, **Lưu** (chỉ bật khi có ô
  đã đổi, hiện số ô đang chờ lưu).
- Bảng cuộn ngang, **cột đầu dính** (`sticky left-0`) chứa tên sản phẩm + SKU;
  hàng header dính trên chứa tên kênh + mã kênh. Mỗi header có nút ✕.
- Ô: `<input inputMode="numeric">`. Ô đã đổi so với lúc tải được tô nền nhạt.
  Ô có id tra được tên nguồn thì tên hiện dạng chữ nhỏ bên dưới.
- Dialog thêm kênh/sản phẩm: danh sách có ô tìm kiếm, chọn nhiều, ẩn mục đã có
  trong bảng. Nguồn dữ liệu là API kênh/sản phẩm sẵn có.
- Bỏ cột/hàng: `confirm` nêu rõ **số ô sẽ bị xoá** trước khi gọi `DELETE`.
- Bảng rỗng: trạng thái trống hướng dẫn thêm kênh và sản phẩm trước.

## 10. Kiểm thử

### Backend (`backend/apps/foxia/tests/`)

`test_foxia_order_push.py`:
1. Đơn landing 1 SKU, có mapping → gọi đúng URL `{base}/{id}`, body đúng 3 trường,
   ghi 1 dòng `ok`.
2. Đơn nhiều SKU → lấy id của **dòng có `id` nhỏ nhất**.
3. Không có mapping → không gọi HTTP, ghi `skipped_no_mapping`.
4. Đơn không có dòng hàng → ghi `skipped_no_product`.
5. Foxia trả 500 / timeout → ghi `failed` kèm `http_status`/`error`, task **không raise**.
6. `FOXIA_PUSH_ENABLED=False` → task return sớm, không ghi dòng nào.
7. Ghép địa chỉ: đủ 4 phần; thiếu ward/district; cả 4 rỗng thì rơi về `raw_address`.
8. Enqueue đúng MỘT lần với task `"foxia.push_order"` và `queue="foxia_push"`, chỉ
   sau commit; transaction rollback thì không enqueue gì.
9. `create_order_from_landing` có đẩy — còn `create_order_manual` thì **không**
   (kiểm qua endpoint thật `POST /api/orders/create`, không soi source text).

`test_foxia_link_config_api.py`:
10. `GET` trả đúng hình dạng 3 nhánh.
11. Thêm kênh trùng → idempotent, không tạo bản ghi thứ hai.
12. `DELETE` một cột → các ô của cột đó biến mất, ô cột khác còn nguyên, trả đúng
    `deleted_cells`.
13. `PUT cells` upsert + xoá (`null`) trong một lô.
14. `PUT cells` có một ô `foxia_source_id <= 0` → `400` và **không ô nào** được ghi.
15. Người dùng thường (không superuser) → `403` ở cả 6 endpoint.

`backend/apps/common/tests/test_celery_routes.py` (file sẵn có): `push_order_to_foxia` route vào `foxia_push`.

### Frontend (`frontend/src/features/foxia-link/__tests__/`)

16. Dựng bảng đúng số hàng/cột từ dữ liệu API.
17. Sửa ô → nút Lưu bật và đếm đúng số ô chờ lưu; Lưu gửi đúng các ô đã đổi.
18. Bỏ cột → hộp xác nhận nêu đúng số ô sẽ mất.

Chạy test backend bằng container test (pg:5433, redis:6380), **không** dùng `.env`
prod của repo. Test frontend chạy qua docker (`node:22-alpine`) vì máy không có
`node` trên PATH.

## 11. Rủi ro và cách chặn

| Rủi ro | Cách chặn |
|---|---|
| Bật nhầm lúc bảng mapping còn trống → đẩy rác sang Foxia | `FOXIA_PUSH_ENABLED` mặc định tắt; thiếu mapping thì bỏ qua chứ không dùng id mặc định |
| Foxia chậm/treo làm nghẽn worker | Timeout `(5, 10)`, `max_retries=0`, queue riêng, container riêng |
| Đẩy trùng khi đơn landing bị gửi lại | Ngoài phạm vi lần này — chống trùng nằm ở lớp ingest sẵn có (blocklist/duplicate) trước khi tới `create_order_from_landing` |
| Bỏ cột làm mất id đã điền công sức | Xác nhận có nêu số ô; và đây là lựa chọn có chủ đích của chủ hệ thống |
| `response_body` phình bảng | Cắt 2000 ký tự |
| `worker-slow` và `worker-foxia` cùng nghe `foxia` sau deploy nửa vời | Ghi rõ trong mô tả MR: phải `up -d` cả hai service |
