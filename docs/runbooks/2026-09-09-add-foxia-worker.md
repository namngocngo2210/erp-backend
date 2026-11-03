# Runbook — Thêm worker `worker-foxia` (queue `foxia` + `foxia_push`)

**Ngày:** 2026-09-09
**Nguồn:** spec `docs/superpowers/specs/2026-09-09-foxia-order-push-design.md`; review toàn nhánh mục C3/I6.
**Ảnh hưởng:** thêm 1 container Celery worker trên prod, RÚT queue `foxia` khỏi `worker-slow`. KHÔNG có migration, KHÔNG downtime nếu làm đúng thứ tự §3.

---

## 1. Vì sao

Trước thay đổi này, queue `foxia` (backfill CRM đối tác: `sync_catalog`, `sync_orders`,
`sync_delivered`, `time_limit=900`) sống chung container với queue `slow` (job Gemini
callcenter + precompute báo cáo) — `worker-slow` nghe `-Q slow,foxia`. Tính năng mới
"đẩy đơn landing sang Foxia" (spec 2026-09-09) thêm một chiều GHI: mỗi đơn landing tạo
xong phải `POST` một request HTTP ngắn sang Foxia, route qua queue mới `foxia_push`.

Nhét `foxia_push` vào `slow` là sai theo đúng lý do đã ghi trong runbook
`2026-07-26-add-slow-worker.md`: một đơn landing xếp hàng sau lượt backfill `foxia` 900s
HOẶC sau job Gemini 8 phút của `slow` thì việc "đẩy ngay khi lưu" mất hết ý nghĩa.

**Cách xử lý:** dựng container RIÊNG `worker-foxia`, nghe CẢ HAI queue `foxia,foxia_push`,
và rút `foxia` khỏi `-Q` của `worker-slow` (`slow,foxia` → `slow`).

---

## 2. Thay đổi trong code

| File | Thay đổi |
|---|---|
| `backend/config/settings_parts/celery.py` | thêm `CELERY_FOXIA_PUSH_QUEUE` + route `apps.foxia.tasks.push_order_to_foxia` |
| `backend/apps/dispatch/registry.py` | thêm entry `"foxia.push_order"` |
| `backend/apps/orders/application/services/order_service_commands.py` | `_schedule_foxia_push` — enqueue qua `apps.dispatch` (KHÔNG import thẳng `apps.foxia`, xem layering `.importlinter`) sau `transaction.on_commit`, chỉ ở luồng landing |
| `backend/apps/foxia/application/services/order_push_service.py` | `FoxiaOrderPushService.push` — thân task thật (chọn `foxia_source_id`, gọi HTTP, ghi `FoxiaOrderPush`) |
| `docker-compose.prod.yml` | `worker-slow`: `-Q slow,foxia` → `-Q slow`. Thêm service `worker-foxia` (`desi-worker-foxia`, `-Q foxia,foxia_push`). Thêm 4 biến push vào anchor `x-backend-env` (**bắt buộc** — anchor này là WHITELIST, biến không khai ở đây không bao giờ vào container dù có trong `.env`) |
| `docker-compose.staging.yml` | tương tự: `worker-slow` bỏ `foxia`; thêm `worker-foxia` (`desi-staging-worker-foxia`, `--concurrency=1`, không `mem_limit`); thêm 4 biến push vào anchor (mặc định tắt — staging không gọi CRM thật) |
| `docker-compose.local.yml` | worker duy nhất thêm `foxia,foxia_push` vào `-Q`; thêm 4 biến push vào anchor |
| `.env.example` | mục `===== Foxia =====`: 4 biến push + `FOXIA_WORKER_CONCURRENCY`/`FOXIA_WORKER_MAX_MEMORY_PER_CHILD` |
| `scripts/deploy-prod.sh` | thêm `worker-foxia` vào danh sách `up -d --build` |
| `scripts/staging-refresh.sh` | thêm `worker-foxia` vào CẢ BỐN chỗ stop/start (bẫy giống `worker-export`: bỏ sót thì nó sống sót qua drop/restore/neutralize và có thể đẩy khách của dump prod sang Foxia THẬT) |
| `backend/apps/foxia/tests/test_foxia_order_push.py` | test enqueue đúng task/queue sau commit + rollback không enqueue gì |
| `backend/apps/common/tests/test_celery_routes.py` | test `foxia_push` tách queue riêng, không dùng chung `foxia` |

> ⚠️ **Đây chính là danh sách mà bản gốc của thay đổi này đã BỎ SÓT hai dòng** (review
> 2026-09-09 mục C3): `scripts/deploy-prod.sh` và bốn chỗ trong `scripts/staging-refresh.sh`.
> Hệ quả nếu bỏ sót: một lần `deploy-prod.sh` bình thường recreate `worker-slow` (mất queue
> `foxia`) mà KHÔNG tạo `worker-foxia` (service mới hoàn toàn, không service nào tự tạo nó) —
> cả `foxia` LẪN `foxia_push` mất consumer, task chất đống trong Redis, không ai báo lỗi.
> Đây là loại lỗi runbook này (đọc TRƯỚC khi deploy) tồn tại để chặn.

Biến env mới (đều có default trong compose):

| Biến | Default | Ý nghĩa |
|---|---|---|
| `CELERY_FOXIA_PUSH_QUEUE` | `foxia_push` | tên queue cho việc đẩy đơn |
| `FOXIA_PUSH_ENABLED` | `0` | công tắc tổng chiều đẩy đơn — tắt thì `_schedule_foxia_push` no-op ngay, không enqueue |
| `FOXIA_PUSH_BASE_URL` | `https://desiglobal.foxia.vn/duongdankhachhang` | phần trước `/{id}` |
| `FOXIA_PUSH_TIMEOUT` | `10` | giây, read timeout |
| `FOXIA_WORKER_CONCURRENCY` | `1` | số slot của `desi-worker-foxia` (hạ từ 2 xuống 1, ruling controller review 2026-09-09 mục I6 — host chỉ còn ~2.2GB khả dụng, backfill trước đây chưa từng SỞ HỮU nguyên 2 slot riêng, `foxia_push` chỉ là một request HTTP ngắn) |
| `FOXIA_WORKER_MAX_MEMORY_PER_CHILD` | `358000` | ngưỡng RSS (KB) recycle prefork child — **cố ý** vẫn tính theo công thức concurrency 2 (kế thừa nguyên từ `worker-slow`) dù default concurrency đã hạ còn 1; an toàn hơn mức cần, không sai |

> ⚠️ `FOXIA_WORKER_MAX_MEMORY_PER_CHILD` phụ thuộc `FOXIA_WORKER_CONCURRENCY`. Công thức
> (giống comment ở anchor `x-backend-env` và runbook `worker-slow`):
> `(mem_limit_MB − parent ~180MB) / concurrency × 0,85`
> → `(1024 − 180) / 2 × 0,85 ≈ 358MB ≈ 358000KB`.
> Đổi `FOXIA_WORKER_CONCURRENCY` LÊN (vượt 2) mà quên đổi ngưỡng này thì cgroup OOM killer
> sẽ SIGKILL child TRƯỚC khi Celery kịp recycle → connection Postgres mồ côi chiếm slot.
> `mem_limit: 1024m` giữ NGUYÊN của `worker-slow` — container này kế thừa nguyên backfill
> `foxia` (900s), không phải service mới nhẹ như `foxia_push` một mình.

---

## 3. Deploy lên prod

> ⚠️ **KHÔNG chạy `docker compose pull` hay `up -d` cho cả stack.** Trên VPS này host
> dùng chung với dự án khác: `pull` kéo luôn image core và `up -d` không tham số sẽ
> recreate nhiều service cùng lúc, đã từng gây lỗi trùng tên container. Chỉ đụng đúng
> service cần đụng.

```bash
cd /path/to/desi-order
git pull --ff-only

# 1) Kiểm tra compose parse được TRƯỚC khi động vào container nào
docker compose -f docker-compose.prod.yml config >/dev/null && echo OK

# 2) Dựng RIÊNG worker mới (build image backend nếu chưa có layer mới)
docker compose -f docker-compose.prod.yml up -d --build worker-foxia

# 3) Recreate worker-slow để nó nhả queue `foxia` (đổi -Q slow,foxia -> slow)
docker compose -f docker-compose.prod.yml up -d --build worker-slow

# 4) Beat phải nạp lại route để đẩy task push vào queue mới.
#    CELERY_TASK_ROUTES áp ở phía PRODUCER, nên beat cần restart.
docker compose -f docker-compose.prod.yml restart beat
```

Từ lần deploy sau, `scripts/deploy-prod.sh` đã có `worker-foxia` trong danh sách
`up -d --build` nên không cần thao tác tay nữa — chạy script như bình thường.

**Bật chiều đẩy đơn (`FOXIA_PUSH_ENABLED=1`) là bước RIÊNG, làm SAU khi bảng liên kết
kênh×SKU → `foxia_source_id` (Cài đặt → Cấu hình liên kết Foxia, superuser-only) đã điền:**

```bash
# Producer thật của "tạo đơn landing" là service `worker` (task Celery
# process_landing_ingest, route CELERY_ORDERS_QUEUE, -Q default,orders,lark_noti,audit)
# — KHÔNG PHẢI `web`. Recreate cả hai để cả producer lẫn consumer đọc env mới.
docker compose -f docker-compose.prod.yml up -d worker worker-foxia
```

### Thứ tự an toàn

Dựng `worker-foxia` **trước**, recreate `worker-slow` **sau**, restart `beat` **cuối
cùng**. Làm ngược lại (rút `foxia` khỏi `worker-slow` trước khi `worker-foxia` sống) thì
trong vài giây backfill CRM đang chạy dở mất consumer — task nằm chờ trong Redis (không
mất) nhưng chờ vô ích, và nếu đúng lúc đó `worker-slow` cũ đang xử lý dở một job `foxia`
thì job đó bị cắt ngang bởi chính thao tác recreate.

---

## 4. Kiểm tra sau deploy

```bash
# a) Cả hai worker đã lên và healthy
docker compose -f docker-compose.prod.yml ps worker-foxia worker-slow

# b) worker-foxia ĐÃ NHẬN ĐÚNG QUEUE — bước quan trọng nhất
docker exec desi-worker-foxia celery -A config inspect active_queues
#    Kỳ vọng: mục "celery@<hostname>" liệt kê CẢ "foxia" LẪN "foxia_push".

# c) worker-slow KHÔNG còn nghe foxia
docker exec desi-worker-slow celery -A config inspect active_queues
#    Kỳ vọng: chỉ còn "slow", KHÔNG có "foxia".

# d) Ping qua broker
docker exec desi-worker-foxia celery -A config inspect ping --timeout=10
#    Kỳ vọng: "pong".

# e) Hai queue không tồn đọng
docker exec desi-redis-managed redis-cli -a "$REDIS_PASSWORD" -n 3 llen foxia
docker exec desi-redis-managed redis-cli -a "$REDIS_PASSWORD" -n 3 llen foxia_push
#    Kỳ vọng: về 0 sau mỗi chu kỳ/mỗi đơn landing. Tăng đều = không ai tiêu thụ.

# f) Log worker mới nhận task
docker logs --tail 50 desi-worker-foxia

# g) (chỉ sau khi bật FOXIA_PUSH_ENABLED=1) tạo một đơn landing thật rồi soi
#    /admin/foxia/foxiaorderpush/ — phải có dòng mới, status "ok".
```

**Chỉ số nghiệm thu:** `docs/runbooks/foxia-integration.md` §3 mục "Không có lượt sync
thành công nào trong 2 giờ" — đổi lệnh soi log từ `worker-slow` sang `worker-foxia` (đã
sửa trong runbook đó cùng đợt review này).

---

## 5. Rollback

Thay đổi này thuần cấu hình (queue routing), không có state mới ngoài bảng
`foxia_link_*`/`foxia_order_pushes` → rollback sạch, không mất task đang backfill.

```bash
# 1) Tắt ngay chiều đẩy đơn nếu đang bật (không cần recreate gì — flag đọc ở runtime
#    của producer, nhưng an toàn nhất vẫn là recreate worker để chắc chắn):
#    đặt FOXIA_PUSH_ENABLED=0 trong .env rồi
docker compose -f docker-compose.prod.yml up -d worker worker-foxia

# 2) Trả queue foxia về worker-slow: revert -Q slow -> slow,foxia trong
#    docker-compose.prod.yml, revert route CELERY_FOXIA_PUSH_QUEUE nếu cần, rồi:
docker compose -f docker-compose.prod.yml up -d --build worker-slow
docker compose -f docker-compose.prod.yml restart beat

# 3) Đợi cả hai queue rỗng rồi mới dừng worker-foxia (tránh cắt ngang task đang chạy):
docker exec desi-redis-managed redis-cli -a "$REDIS_PASSWORD" -n 3 llen foxia         # chờ về 0
docker exec desi-redis-managed redis-cli -a "$REDIS_PASSWORD" -n 3 llen foxia_push    # chờ về 0

# 4) Dừng và xoá worker-foxia
docker compose -f docker-compose.prod.yml stop worker-foxia
docker compose -f docker-compose.prod.yml rm -f worker-foxia
```

> Lưu ý: `foxia_push` không idempotent (`max_retries=0`, xem docstring
> `order_push_service.py`) — nếu phải rollback GẤP trong khi `foxia_push` còn task chưa
> xử lý, KHÔNG đẩy chúng sang queue khác để "cứu": xoá thẳng
> (`redis-cli ... del foxia_push`) rồi chấp nhận các đơn đó không được đẩy tự động, xử lý
> tay qua trang Cấu hình liên kết Foxia + `manage.py foxia_backfill` nếu cần. `foxia`
> (backfill) thì an toàn re-route vì `sync_*` upsert idempotent theo `foxia_id`.
>
> Test `apps/common/tests/test_celery_routes.py` sẽ ĐỎ ngay khi bỏ route mà không cập
> nhật kỳ vọng — đó là chủ đích, cùng nguyên tắc với runbook `worker-slow`.
