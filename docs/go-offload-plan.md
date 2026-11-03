---
title: Thay thế Celery bằng Go + deploy unified docker compose (customer.delax.vn, 8GB/4CPU)
version: 0.8.1
date: 2026-05-14
branch: feature-go-microservices-grpc
status: approved
---

# Thay thế hoàn toàn Celery bằng Go

> **Bối cảnh & quyết định**
> - **v0.1 (gRPC, hybrid 3 task)**: bị bỏ vì gRPC overkill cho volume này (xem §5 v0.2 cũ — git history).
> - **v0.2 (REST, hybrid offload)**: tốt hơn nhưng vẫn giữ Celery cho phần còn lại. Vẫn tốn RAM Celery prefork.
> - **v0.3 (bản này)**: **diệt Celery 100%**. Lý do chính: tiết kiệm ~900MB-1GB RAM (Celery prefork 4 child × Django interpreter + beat + Redis broker DB).
> - Plan v0.2 "100% migrate Django → Go" (commit [56dc4c0](https://) đã revert tại [c6a1a08](https://)) **không phải hướng đi**. Django web/REST/WS/ORM giữ nguyên.

---

## 1. Nguyên tắc thiết kế

1. **Diệt Celery, không diệt Django.** Django web + REST + WS + ORM giữ nguyên. Chỉ thay tầng async/worker/beat.
2. **Postgres làm task queue.** Bảng `jobs` generic + `SELECT ... FOR UPDATE SKIP LOCKED`. Lý do chọn xem §6.
3. **Redis dùng đúng chỗ nó mạnh.** Pub/Sub cho WS broadcast (đã có) + cache. Bỏ Redis broker/result DB.
4. **2 pattern xử lý task body.** Pattern A: port logic sang Go khi I/O thuần. Pattern B: giữ code Python, Go enqueue → Django web endpoint sync chạy.
5. **Schema Django sở hữu.** Cả Django và Go cùng đọc/ghi DB. Schema migration vẫn ở Django.
6. **Rollback dễ.** Feature flag `TASK_BACKEND` per task type → switch về Celery worker cũ trong 1 phút (giữ ảnh worker 14 ngày sau cutover).

---

## 2. Tiết kiệm RAM thực tế

Setup hiện tại theo [docker-compose.full.yml](../docker-compose.full.yml):

| Process | RAM/instance | Số | Total |
|---|---|---|---|
| Celery `worker` (prefork concurrency=4) | ~200MB × 4 child + 150MB master | 1 | ~950MB |
| Celery `beat` | ~150MB | 1 | ~150MB |
| Redis DB 3 (broker) + DB 4 (result) | ~50MB chia sẻ trong `redis` | – | ~50MB |
| **Tổng** | | | **~1.15GB** |

Sau khi diệt Celery (v0.3):

| Process | RAM |
|---|---|
| `audit-writer` (Go) | 30-50MB |
| `task-worker` (Go) | 50-80MB |
| `task-cron` (Go) | 30-40MB |
| Django web pool tăng `--threads 4` (Pattern B) | +0 (cùng tiến trình) |
| **Tổng Go** | **~110-170MB** |

**Tiết kiệm: ~1GB.** Khả thi trên VPS 4GB / 8GB.

---

## 3. Phân loại đầy đủ 19 task

### 3.1. Pattern A — port logic 100% sang Go (15 task)

Task body chủ yếu HTTP I/O + DB CRUD đơn giản. Go xử lý concurrency tốt hơn rõ rệt.

| # | Task | Trigger | Workload chính | Notes |
|---|------|---------|----------------|-------|
| 1 | `audit.tasks.create_audit_log_task` | per-request middleware + decorators | INSERT 1 row | Batch trong Go (200/200ms). |
| 2 | `kiotviet.tasks.sync_daily_revenue_all_channels` | cron 60s | 5 HTTP step + parse HTML report | Concurrent per channel. |
| 3 | `facebook_ads.tasks.sync_all_daily_spend` | cron 60s | Loop app → paginate Graph API | Fanout per app/per page. |
| 4 | `orders.tasks.process_auto_update_invoice_code_and_push_old` | cron 60s | Quét order, fetch invoice, push old channel | Goroutine fanout. |
| 5 | `orders.tasks.process_auto_sync_invoice_shipping_fee_and_status` | (cron, currently disabled?) | Update shipping/status từ KV | Tương tự #4. |
| 6 | `orders.tasks.process_auto_sync_invoice_full` | cron 60s | Quét 7 ngày, fetch invoice full | Concurrent. |
| 7 | `orders.tasks.process_cron_hourly_audit_channels` | cron 1h (soft_time_limit 1800s!) | So sánh 2 channel/tháng, remediate | Cần concurrent fetch, target < 10 phút. |
| 8 | `orders.tasks.process_kiotviet_job` | `apply_async(countdown=idx*3)` per job | Lock job row, gọi KV API, retry | Token bucket 12/m per channel. |
| 9 | `orders.tasks.process_kiotviet_webhook_push_old` | `.delay()` từ webhook | KV API push old channel | I/O thuần. |
| 10 | `orders.tasks.process_kiotviet_webhook_sync_old_status` | `.delay()` từ webhook | KV API sync status | I/O thuần. |
| 11 | `zalo_bridge.tasks.zalo_apply_initial_tags` | `.delay()` | HTTP Zalo Bridge | I/O thuần. |
| 12 | `zalo_bridge.tasks.zalo_send_order_created` | `.delay()` | HTTP Zalo Bridge | I/O thuần. |
| 13 | `zalo_bridge.tasks.zalo_send_shipping_started` | `.delay()` | HTTP Zalo Bridge | I/O thuần. |
| 14 | `zalo_bridge.tasks.zalo_send_delivery_completed` | `.delay()` | HTTP Zalo Bridge | I/O thuần. |
| 15 | `orders.tasks.notify_lark_order_created` | `transaction.on_commit` | HTTP Lark webhook 1 line | Tiny. |

### 3.2. Pattern B — Giữ code Python, Go enqueue → Django web sync endpoint (4 task)

Task body đụng nặng Django ORM hoặc business logic phức tạp. Port sang Go phải duplicate logic, không đáng.

| # | Task | Lý do giữ Python |
|---|------|------------------|
| 16 | `orders.tasks.process_sale_reminders` | Query `SaleReminder` + `OrderAssessment` join, build template Lark message ([sale_reminder_templates.py](../backend/apps/orders/infrastructure/sale_reminder_templates.py) 238 dòng). |
| 17 | `orders.tasks.process_no_answer_reminders` | Aggregation Count + per-slot template. |
| 18 | `orders.tasks.process_restore_orders` | Bulk UPDATE Order + tạo OrderAssessment + WS broadcast `restore.completed`. Đụng nhiều ORM. |
| 19 | `orders.tasks.process_landing_ingest` | Gọi `OrderService.create_order_from_landing(payload)` — phone normalize + Customer dedupe + Order/Item create + status log + WS event. Là **commit pipeline trung tâm**. Cũng là chỗ DUY NHẤT trong tầng task gọi `vietnamadminunits` (xem §3.4). |

### 3.3. Pattern C — Cron schedule

Tách riêng vì là tầng schedule, không phải task body. Toàn bộ entry trong [seed_cron_jobs.py](../backend/apps/cron_jobs/management/commands/seed_cron_jobs.py) (4 interval task + 4 crontab task) đều chuyển sang Go cron (`robfig/cron/v3`).

Sau cutover: celery-beat container bị **xoá**. Bảng `django_celery_beat_*` giữ lại đọc-only đến khi confirm ổn (14 ngày).

### 3.4. Python-only dependencies — KHÔNG cần port

Một số thư viện Python không có port Go tốt tương đương, hoặc port lại quá tốn công so với benefit. Pattern B đã giải quyết việc này: code Python vẫn chạy nguyên trong Django container, Go chỉ enqueue/trigger.

Bảng các dependency Python ở lại Django, **không bị ảnh hưởng** bởi Go migration:

| Dependency | Dùng ở đâu | Path sau migration |
|------------|-----------|-------------------|
| **`vietnamadminunits`** (parse địa chỉ VN 3 cấp) | [address_parser.py](../backend/apps/orders/address_parser.py) → `parse_legacy_level3()`, `format_standard_address()` | Pattern B `process_landing_ingest` + 2 REST endpoint sync (`GET /api/orders/parse-address`, `POST /api/orders/create`) + management commands `import_kiotviet_invoices`. Tất cả vẫn chạy Django web. |
| `python-barcode` + `qrcode` | Sinh barcode/QR cho phiếu giao | REST endpoint Django sync. |
| `openpyxl` | Excel import/export (`import_excel.py`, `export_excel.py`) | REST endpoint Django sync. |
| `cryptography` (Fernet) | Mã hoá KiotViet credentials | Django decrypt → expose qua `GET /api/internal/kiotviet/.../access-token` cho Go. Go KHÔNG decrypt. |
| `channels` + `daphne` | WebSocket `/ws/crm/` | Django ASGI giữ nguyên. Go publish event qua Redis Pub/Sub `crm_updates` → Channels consume. |
| `psycopg` / `psycopg2-binary` | ORM driver | Django web giữ. Go dùng `pgx/v5` độc lập. |

**Verify trace cho `vietnamadminunits`** (đã grep):
- 19 file `@shared_task` không có file nào `import address_parser` trực tiếp.
- Chỉ 1 chain duy nhất: `process_landing_ingest` → `OrderService.create_order_from_landing()` → `parse_legacy_level3()` → `vietnamadminunits.parse_address()`.
- `process_landing_ingest` đã được classify Pattern B → giữ Python → `vietnamadminunits` chạy nguyên ở Django container.
- Các task khác (zalo, kiotviet job, audit, FB, …) **không** đụng tới address parser.

**Kết luận**: Go migration **zero impact** lên `vietnamadminunits`. Library vẫn ở `backend/requirements.txt`, vẫn cài trong Django Docker image, vẫn được gọi từ landing ingest pipeline.

> **Quy tắc chung khi gặp Python lib không port được**: classify task chứa lib đó vào Pattern B. Nếu lib được gọi từ REST endpoint sync (không qua task), không cần làm gì — Django web đã giữ nguyên.

---

## 4. Kiến trúc đề xuất

### 4.1. Sơ đồ tổng thể

```
                              ┌─────────────────────────────────┐
                              │   Frontend (React) — không đổi  │
                              └───────────────┬─────────────────┘
                                              │ REST + WS /ws/crm/
                                              ▼
            ┌──────────────────────────────────────────────────────────┐
            │ Django web (gunicorn workers=3, threads=4)               │
            │  ├ REST/WS, ORM, RBAC, JWT, Channels                     │
            │  ├ apps/internal/ — REST endpoint nội bộ                 │
            │  │   ├ POST /tasks/{name}/run  (Pattern B exec)          │
            │  │   ├ POST /audit/events     (audit-writer batch)       │
            │  │   └ GET  /kiotviet/.../access-token                   │
            │  └ apps/dispatch.py — Job.objects.create(...)            │
            └─┬──────────────┬─────────────────────────────────────────┘
              │              │
              │ HTTP POST    │ INSERT INTO jobs (...)
              │ (Pattern B)  │ (Pattern A, async via DB)
              │              ▼
              ▼     ┌──────────────────────────────────────────┐
   ┌──────────────┐│        Postgres                          │
   │ Go services  ││  ├ jobs (generic queue)                  │
   │              ││  ├ go_cron_runs (audit/idempotency)      │
   │ audit-writer ││  ├ audit_logs, order_kiotviet_jobs, ...  │
   │ task-worker  ││  └ schema cũ Django sở hữu               │
   │ task-cron    │└──────────────────────────────────────────┘
   └──────┬───────┘
          │ publish event
          ▼
   ┌──────────────┐
   │ Redis        │
   │ DB 1 cache   │  (giữ)
   │ DB 2 channel │  (giữ — Channels pub/sub)
   │ DB 3 ✗       │  (XOÁ — Celery broker)
   │ DB 4 ✗       │  (XOÁ — Celery result)
   └──────────────┘
```

### 4.2. Vai trò 3 binary Go

| Binary | Chức năng | Thay thế |
|--------|-----------|----------|
| `audit-writer` | HTTP server nhận audit batch, INSERT pg | celery `audit_logs` queue |
| `task-worker` | Poll bảng `jobs` per queue, dispatch handler | celery `worker -Q default,orders,kiotviet,zalo_bridge,lark_noti` |
| `task-cron` | `robfig/cron/v3` đẩy job vào `jobs` table | celery `beat` |

`task-worker` có 2 loại handler bên trong:
- **Native Go handler** (Pattern A): xử lý hoàn toàn trong Go.
- **Proxy handler** (Pattern B): POST `http://web:8000/api/internal/tasks/{task_name}/run` body=payload, lấy status code.

Dispatch table cố định trong code Go:
```go
var handlers = map[string]Handler{
    "audit.create":                  goNativeAudit,           // A
    "zalo.apply_initial_tags":       goNativeZaloApplyTags,   // A
    "kv.process_job":                goNativeKVJob,           // A
    ...
    "orders.sale_reminders":         djangoProxy("process_sale_reminders"),     // B
    "orders.no_answer_reminders":    djangoProxy("process_no_answer_reminders"),// B
    "orders.restore_orders":         djangoProxy("process_restore_orders"),     // B
    "orders.landing_ingest":         djangoProxy("process_landing_ingest"),     // B
}
```

---

## 5. Schema mới

Chỉ thêm 2 bảng, **không sửa bảng cũ**:

### 5.1. `jobs` — generic queue

```sql
CREATE TABLE jobs (
    id            BIGSERIAL PRIMARY KEY,
    queue         TEXT NOT NULL,                              -- default | orders | kiotviet | zalo | lark | audit
    task_type     TEXT NOT NULL,                              -- "kv.process_job", "zalo.send_order_created", ...
    payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
    state         TEXT NOT NULL DEFAULT 'pending',            -- pending | running | done | failed | dead
    priority      SMALLINT NOT NULL DEFAULT 0,
    attempts      INT NOT NULL DEFAULT 0,
    max_attempts  INT NOT NULL DEFAULT 5,
    run_after     TIMESTAMPTZ NOT NULL DEFAULT now(),         -- thay countdown=X (Celery apply_async)
    locked_at     TIMESTAMPTZ,
    locked_by     TEXT,                                       -- "task-worker-1"
    last_error    TEXT,
    result        JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);

CREATE INDEX jobs_dispatch_idx ON jobs (queue, state, run_after, priority DESC, id)
    WHERE state IN ('pending','running');
CREATE INDEX jobs_cleanup_idx  ON jobs (finished_at)
    WHERE state IN ('done','failed','dead');
```

Cleanup: cron job phụ xoá `state IN ('done','failed') AND finished_at < now() - interval '7 days'`.

### 5.2. `go_cron_runs` — audit + idempotency cho cron

```sql
CREATE TABLE go_cron_runs (
    id            BIGSERIAL PRIMARY KEY,
    cron_name     TEXT NOT NULL,                              -- "kv.revenue_sync"
    fired_at      TIMESTAMPTZ NOT NULL,
    fired_by      TEXT NOT NULL,                              -- "task-cron-1"
    job_id        BIGINT REFERENCES jobs(id),
    status        TEXT NOT NULL,                              -- "scheduled" | "skipped_dup" | "error"
    UNIQUE (cron_name, fired_at)                              -- chống fire trùng giữa instance
);
```

### 5.3. Bảng cũ giữ nguyên

- `OrderKiotVietJob` (business state machine cho KV job) — không thay đổi. Khi Django enqueue KV job, ghi `OrderKiotVietJob.create(...)` như cũ **và** thêm 1 row vào `jobs` với `payload={kiotviet_job_id: <id>}`.
- `audit_logs` — không đổi schema.
- `django_celery_beat_*` — giữ tạm 14 ngày, mọi `PeriodicTask.enabled=False`.

---

## 6. Vì sao Postgres `jobs` thay vì Redis Streams / RabbitMQ

So sánh thực tế cho desi-order:

| | Postgres SKIP LOCKED | Redis Streams | RabbitMQ |
|---|---|---|---|
| Infra mới phải maintain | **Không** | Không (đã có Redis) | **Có** (~150MB RAM — đi ngược mục tiêu) |
| Durability | Mạnh nhất (WAL fsync) | AOF mỗi giây | Disk khi `durable=true` |
| Throughput cần đạt | Volume desi-order < 1k msg/s — dư sức | 100k+ msg/s | ~50k msg/s |
| Latency dispatch | 5-50ms poll (acceptable) hoặc `LISTEN/NOTIFY` < 1ms | < 1ms | < 1ms |
| Transactional enqueue | **Có** — `Order.create()` + `Job.create()` cùng tx | Không (cần outbox pattern) | Không (cần outbox) |
| Pattern đã có ở repo | **Có** ([OrderKiotVietJob](../backend/apps/orders/models.py#L333) `select_for_update()`) | Không | Không |
| Observability | `SELECT FROM jobs WHERE state='failed'` (DBeaver / Django Admin) | redis-cli xinfo | Mgmt UI |
| Backup | Cùng `pg_dump` định kỳ | Cần backup AOF/RDB riêng | Cần backup riêng |

**Quyết định: Postgres.** RabbitMQ phù hợp khi cần priority queue cao cấp / DLX routing phức tạp / throughput > 5k/s sustained — không có ở đây.

> Pub/Sub vs RabbitMQ là **so sánh sai bài toán**. Pub/Sub là broadcast fire-and-forget (subscriber offline → mất message), không phải task queue. Trong v0.3, Pub/Sub chỉ dùng cho hướng **Go → Django broadcast WS** (đã đúng use case của nó).

---

## 7. Codemod cho 28 call site

Tạo wrapper duy nhất, tất cả call site đi qua đây:

```python
# apps/dispatch/__init__.py (app mới)
from django.conf import settings
from .registry import CELERY_REGISTRY  # task name -> celery task object
from .queue import enqueue_job          # Job.objects.create wrapper


def dispatch(task_name: str, *, queue: str = "default", countdown: int = 0, **payload):
    """
    Thay thế cho:
        xxx.delay(**kwargs)
        xxx.apply_async(args=[...], queue=..., countdown=...)
    """
    if settings.TASK_BACKEND == "go":
        enqueue_job(task_type=task_name, queue=queue, run_after_seconds=countdown, payload=payload)
        return

    # Fallback Celery (giai đoạn migration & rollback)
    celery_task = CELERY_REGISTRY[task_name]
    if countdown:
        celery_task.apply_async(kwargs=payload, queue=queue, countdown=countdown)
    else:
        celery_task.apply_async(kwargs=payload, queue=queue)
```

Sed/codemod cho 28 site (file `scripts/migrate_dispatch.py`):

```python
# Trước:
zalo_send_order_created.delay(order_id=order.id)
# Sau:
dispatch("zalo.send_order_created", queue="zalo_bridge", order_id=order.id)

# Trước:
process_kiotviet_job.apply_async(
    args=[job.id],
    queue="kiotviet",
    countdown=idx * 3,
)
# Sau:
dispatch("kv.process_job", queue="kiotviet", countdown=idx * 3, job_id=job.id)
```

Bảng chuyển tên (lưu trong `apps/dispatch/registry.py`):

| Celery task name | dispatch() name | queue | Pattern |
|------------------|-----------------|-------|---------|
| `apps.audit.tasks.create_audit_log_task` | `audit.create` | `audit` | A (via audit-writer HTTP, không qua jobs table) |
| `apps.zalo_bridge.tasks.zalo_apply_initial_tags` | `zalo.apply_initial_tags` | `zalo_bridge` | A |
| `apps.zalo_bridge.tasks.zalo_send_order_created` | `zalo.send_order_created` | `zalo_bridge` | A |
| `apps.zalo_bridge.tasks.zalo_send_shipping_started` | `zalo.send_shipping_started` | `zalo_bridge` | A |
| `apps.zalo_bridge.tasks.zalo_send_delivery_completed` | `zalo.send_delivery_completed` | `zalo_bridge` | A |
| `apps.orders.tasks.process_kiotviet_job` | `kv.process_job` | `kiotviet` | A |
| `apps.orders.tasks.process_kiotviet_webhook_push_old` | `kv.webhook_push_old` | `kiotviet` | A |
| `apps.orders.tasks.process_kiotviet_webhook_sync_old_status` | `kv.webhook_sync_old_status` | `kiotviet` | A |
| `apps.orders.tasks.notify_lark_order_created` | `lark.notify_order_created` | `lark_noti` | A |
| `apps.orders.tasks.process_restore_orders` | `orders.restore_orders` | `orders` | B |
| `apps.orders.tasks.process_landing_ingest` | `orders.landing_ingest` | `orders` | B |

Note: `audit.create` đặc biệt — Django middleware/decorator gọi **trực tiếp** audit-writer qua HTTP buffer (không đi qua `jobs` table) vì QPS cao và idempotent qua `batch_id`. Wrapper:
```python
# audit dispatch path khác
def write_audit(**payload):
    if settings.AUDIT_BACKEND == "go":
        audit_http_client.enqueue(payload)
    else:
        create_audit_log_task.delay(**payload)
```

---

## 8. Pattern B — Django sync endpoint design

Thay vì Celery worker chạy code Python, Go worker POST tới Django web để chạy. Django web phải chịu được call sync.

### 8.1. Endpoint

```python
# apps/internal/views.py
@require_internal_token
@api_view(["POST"])
def run_task(request, task_name):
    handler = INTERNAL_TASK_REGISTRY[task_name]   # mapping name -> Python function
    payload = request.data.get("payload", {})
    job_id = request.data.get("job_id")           # for tracing
    try:
        result = handler(**payload)
        return Response({"status": "ok", "result": result})
    except Exception as exc:
        logger.exception("internal task %s failed (job_id=%s)", task_name, job_id)
        return Response({"status": "error", "error": str(exc)}, status=500)
```

```python
# apps/internal/registry.py
from apps.orders.infrastructure.tasks.order_tasks_impl.sale_reminder_tasks import (
    process_sale_reminders,
    process_no_answer_reminders,
)
from apps.orders.infrastructure.tasks.order_tasks_impl.restore_tasks import process_restore_orders
from apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks import process_landing_ingest

# Reuse code Python cũ nhưng bỏ @shared_task decorator (hoặc gọi .run() trực tiếp)
INTERNAL_TASK_REGISTRY = {
    "orders.sale_reminders":      lambda **kw: process_sale_reminders.run(**kw),
    "orders.no_answer_reminders": lambda **kw: process_no_answer_reminders.run(**kw),
    "orders.restore_orders":      lambda order_ids, user_id, **_: process_restore_orders.run(order_ids, user_id),
    "orders.landing_ingest":      lambda payload, trace_id=None, **_: process_landing_ingest.run(payload, trace_id),
}
```

> `celery_task.run(*args, **kwargs)` là cách Celery cho gọi sync. Không cần Celery broker — chỉ cần import + gọi.

### 8.2. Vì sao không block gunicorn

- `process_sale_reminders` (cron 60s): chạy < 30s ổn.
- `process_no_answer_reminders` (4 mốc/ngày): < 30s.
- `process_landing_ingest`: 100-500ms / call.
- `process_restore_orders`: chậm nhất, có thể vài phút cho batch 1000 đơn.

Với gunicorn `--workers=3 --threads=4` = 12 concurrent slot. 1 slot bận vài phút cho restore là acceptable. Nếu thấy contention thì:
- Option 1: tăng thread pool gunicorn.
- Option 2: tách process `desi-django-runner` riêng (cùng image, expose port khác, chỉ phục vụ `/api/internal/tasks/*`).

Khởi điểm: Option 1.

### 8.3. Bảo vệ idempotency

Một số Pattern B task cần lock để tránh double-fire:

```python
# process_sale_reminders đã có @transaction.atomic + filter trạng thái pending.
# Nếu cần thêm lớp, dùng pg advisory lock:
with advisory_lock(hash("orders.sale_reminders")):
    process_sale_reminders.run()
```

Hoặc bảo vệ phía Go: `pg_advisory_lock` ở task-cron trước khi enqueue.

---

## 9. Layout repo — Clean Architecture per microservice

### 9.1. Cây tổng thể

```
desi-order/
├── backend/
│   └── apps/
│       ├── dispatch/                  # MỚI: wrapper duy nhất .delay()/.apply_async()
│       │   ├── __init__.py
│       │   ├── queue.py               # Job.objects.create(...) helper
│       │   └── registry.py            # task name <-> celery object map (rollback path)
│       └── internal/                  # MỚI: REST endpoint nội bộ cho Go
│           ├── auth.py                # X-Internal-Token middleware
│           ├── views.py               # run_task, audit batch, kv access-token
│           ├── registry.py            # INTERNAL_TASK_REGISTRY (Pattern B)
│           └── urls.py
├── go-services/                       # MỚI — monorepo 3 microservice
│   ├── go.mod
│   ├── Makefile                       # make tidy, test, sqlc, build, run-*
│   ├── config/                        # YAML config per service (mount vào container)
│   │   ├── audit-writer.yaml          # HTTP listen, batch size, db conn
│   │   ├── task-worker.yaml           # per-queue concurrency, rate limit
│   │   ├── task-cron.yaml             # khai báo lịch (cron expr + task_type + payload)
│   │   └── README.md                  # spec env interpolation ${VAR}
│   ├── db/                            # SQL nguồn cho sqlc
│   │   ├── schema/
│   │   │   └── snapshot.sql           # pg_dump -s từ Django DB (regen khi schema đổi)
│   │   ├── queries/                   # query phức tạp — sqlc input
│   │   │   ├── jobs.sql               # claim/settle/requeue (FOR UPDATE SKIP LOCKED)
│   │   │   ├── cron_runs.sql          # INSERT ON CONFLICT RETURNING (idempotency)
│   │   │   ├── audit_logs.sql         # bulk INSERT đa dòng
│   │   │   └── housekeeping.sql       # cleanup, requeue stuck
│   │   └── sqlc.yaml                  # config: out → internal/shared/platform/pg/sqlcdb/
│   ├── cmd/                           # composition root (entrypoint)
│   │   ├── audit-writer/main.go
│   │   ├── task-worker/main.go
│   │   └── task-cron/main.go
│   ├── internal/
│   │   ├── shared/                    # hạ tầng dùng chung (cross-service)
│   │   │   ├── platform/
│   │   │   │   ├── config/            # YAML loader + ${VAR} env interpolation
│   │   │   │   ├── pg/
│   │   │   │   │   ├── pool.go        # *pgxpool.Pool wrapper + tx helper
│   │   │   │   │   ├── gorm.go        # *gorm.DB factory (share pool qua stdlib.OpenDBFromPool)
│   │   │   │   │   └── sqlcdb/        # OUTPUT của `sqlc generate` (KHÔNG sửa tay)
│   │   │   │   │       ├── querier.go
│   │   │   │   │       ├── jobs.sql.go
│   │   │   │   │       ├── cron_runs.sql.go
│   │   │   │   │       └── audit_logs.sql.go
│   │   │   │   ├── redis/             # *redis.Client wrapper
│   │   │   │   ├── httpserver/        # *gin.Engine + middleware (auth, logger, recover, metrics)
│   │   │   │   ├── httpclient/        # resty.Client với retry + timeout
│   │   │   │   ├── logger/            # slog JSON
│   │   │   │   ├── metrics/           # prometheus collectors
│   │   │   │   ├── di/                # samber/do v2 injector + Module helper
│   │   │   │   └── shutdown/          # signal handler + injector.Shutdown()
│   │   │   ├── concurrency/           # primitives — chan/mutex/goroutine
│   │   │   │   ├── workerpool/        # Producer-Consumer (chan Job + N goroutine)
│   │   │   │   ├── batcher/           # buffer events + flush ticker (audit dùng)
│   │   │   │   ├── ratelimiter/       # token bucket per-key (sync.Map[string]*rate.Limiter)
│   │   │   │   └── advisorylock/      # pg_advisory_lock wrapper
│   │   │   ├── gateway/               # external HTTP client (Adapter pattern)
│   │   │   │   ├── kiotviet/          # KV API + OAuth + HTML report scrape
│   │   │   │   ├── lark/              # Lark webhook
│   │   │   │   ├── zalo/              # Zalo Bridge
│   │   │   │   └── facebook/          # Facebook Graph
│   │   │   └── djclient/              # client gọi Django /api/internal/*
│   │   │
│   │   └── service/                   # mỗi microservice 1 thư mục, Clean Architecture đầy đủ
│   │       ├── audit/                 # ──── audit-writer ────
│   │       │   ├── transport/         # Controller layer: Gin handler
│   │       │   │   ├── handler.go         # POST /audit/events (gin.HandlerFunc)
│   │       │   │   └── router.go          # *gin.Engine wiring
│   │       │   ├── business/          # Use case layer (orchestration + rule)
│   │       │   │   ├── service.go         # AuditService.WriteBatch(...)
│   │       │   │   └── service_test.go
│   │       │   ├── port/              # Interfaces (consumed by business)
│   │       │   │   └── port.go            # AuditRepository, IdempotencyStore
│   │       │   ├── repository/        # Adapter layer
│   │       │   │   ├── gorm_audit.go      # GORM — bulk INSERT audit_logs (CreateInBatches)
│   │       │   │   └── gorm_idempotency.go # GORM — go_audit_batch_marks SELECT/INSERT
│   │       │   ├── entity/            # Domain models (pure structs + method)
│   │       │   │   └── audit_log.go       # GORM tag inline (table mapping)
│   │       │   ├── dto/               # Wire shapes (request/response)
│   │       │   │   ├── request.go
│   │       │   │   └── response.go
│   │       │   └── module.go          # samber/do Register(injector, cfg)
│   │       │
│   │       ├── worker/                # ──── task-worker ────
│   │       │   ├── transport/         # "Controller" cho job: poll loop & dispatcher
│   │       │   │   ├── poller.go          # producer goroutine: SELECT FOR UPDATE SKIP LOCKED
│   │       │   │   └── consumer.go        # consumer goroutine: <-chan Job, gọi business
│   │       │   ├── business/          # Use case: route task_type → handler
│   │       │   │   ├── dispatcher.go      # map[string]TaskHandler (Strategy pattern)
│   │       │   │   ├── handlers/          # 1 file per task_type
│   │       │   │   │   ├── audit_skip.go              # (audit không qua jobs table)
│   │       │   │   │   ├── kv_process_job.go          # Pattern A
│   │       │   │   │   ├── kv_webhook_push_old.go     # Pattern A
│   │       │   │   │   ├── kv_webhook_sync_old.go     # Pattern A
│   │       │   │   │   ├── kv_revenue_sync.go         # Pattern A (cron-triggered)
│   │       │   │   │   ├── kv_auto_sync_full.go       # Pattern A
│   │       │   │   │   ├── kv_auto_update_code.go     # Pattern A
│   │       │   │   │   ├── kv_hourly_audit.go         # Pattern A
│   │       │   │   │   ├── fb_daily_spend.go          # Pattern A
│   │       │   │   │   ├── zalo_apply_initial_tags.go # Pattern A
│   │       │   │   │   ├── zalo_send_order_created.go # Pattern A
│   │       │   │   │   ├── zalo_send_shipping.go      # Pattern A
│   │       │   │   │   ├── zalo_send_delivery.go      # Pattern A
│   │       │   │   │   ├── lark_notify_order.go       # Pattern A
│   │       │   │   │   └── django_proxy.go            # Pattern B (factory cho 4 task)
│   │       │   │   └── handler.go         # interface TaskHandler
│   │       │   ├── port/
│   │       │   │   └── port.go            # JobClaimer, JobSettler, RateLimiter, EventPublisher
│   │       │   ├── repository/
│   │       │   │   ├── sqlc_jobs.go       # SQLC — wrap sqlcdb.Querier (claim FOR UPDATE SKIP LOCKED, settle)
│   │       │   │   └── gorm_misc.go       # GORM — query phụ trợ đơn giản
│   │       │   ├── entity/
│   │       │   │   ├── job.go             # Job entity + state machine
│   │       │   │   └── queue.go           # QueueConfig (concurrency, rate)
│   │       │   ├── dto/
│   │       │   │   ├── payloads.go        # 1 struct per task_type payload
│   │       │   │   └── django_proxy.go
│   │       │   └── module.go              # samber/do Register
│   │       │
│   │       └── cron/                  # ──── task-cron ────
│   │           ├── transport/         # robfig/cron scheduler registry
│   │           │   ├── scheduler.go       # tạo cron, đăng ký tất cả entry
│   │           │   └── entries.go         # khai báo lịch (hard-code phase 1)
│   │           ├── business/
│   │           │   ├── enqueuer.go        # CronEnqueuer.Fire(name, queue, payload)
│   │           │   └── housekeeping.go    # requeue stuck jobs, cleanup old jobs
│   │           ├── port/
│   │           │   └── port.go            # JobEnqueuer, CronRunRecorder
│   │           ├── repository/
│   │           │   ├── sqlc_cron_runs.go  # SQLC — INSERT ON CONFLICT RETURNING (idempotency)
│   │           │   └── gorm_jobs_insert.go # GORM — Job CRUD đơn giản
│   │           ├── entity/
│   │           │   └── cron_entry.go      # CronEntry: name, expr, taskType, queue
│   │           ├── dto/
│   │           │   └── enqueue.go
│   │           └── module.go              # samber/do Register
│   │
│   └── deploy/
│       ├── Dockerfile.audit-writer
│       ├── Dockerfile.task-worker
│       └── Dockerfile.task-cron
├── scripts/
│   └── migrate_dispatch.py            # codemod 28 call site
├── docker-compose.full.yml            # bỏ worker + beat, thêm 3 Go service
└── docs/
    └── go-offload-plan.md             # tài liệu này
```

### 9.2. Layer dependency rule (Clean Architecture)

Phụ thuộc chỉ đi **vào trong**:

```
   transport ──► business ──► port (interface)
                     ▲              ▲
                     │              │
                  entity        repository (impl)
                                gateway (impl)
```

- `entity` không phụ thuộc bất kỳ layer nào (pure struct + method).
- `business` phụ thuộc `entity` + `port`. KHÔNG biết về pgx, http, redis, gin, gorm.
- `port` chỉ là interface — declare ở phía business (consumer), impl ở repository/gateway.
- `repository` & `gateway` implement `port`. Phụ thuộc `shared/platform/*` (pgxpool, gorm.DB, sqlcdb.Querier, redis, resty).
- `transport` orchestrate request → business. Map DTO ↔ entity. Dùng `*gin.Engine` để route, `gin.HandlerFunc` cho handler.
- `dto` ở mỗi service riêng — wire shape, không leak ra ngoài service.
- `module.go` là composition root cho service đó: đăng ký providers vào `do.Injector` (samber/do v2). `main.go` ở `cmd/` chỉ gọi `<service>.Register(injector, cfg)` rồi `do.MustInvoke[*gin.Engine](injector)`.

Rule check chạy local trước khi deploy: `go-arch-lint check` (optional, không bắt buộc).

### 9.3. Stack chính

| Mục đích | Thư viện | Ghi chú |
|---------|----------|---------|
| HTTP framework | **`gin-gonic/gin`** | `*gin.Engine` ở `shared/platform/httpserver/`; middleware return `gin.HandlerFunc`. |
| ORM đơn giản | **`gorm.io/gorm`** + `gorm.io/driver/postgres` | CRUD 1 row, list ≤ 2 điều kiện, bulk nhỏ (`CreateInBatches`). |
| Query phức tạp | **`sqlc-dev/sqlc`** sinh `*pgx.Pool` querier | `FOR UPDATE SKIP LOCKED`, `INSERT ON CONFLICT RETURNING`, CTE, window. |
| DB driver | **`jackc/pgx/v5`** + `pgxpool` | GORM share pool qua `stdlib.OpenDBFromPool`. SQLC sinh code dùng pgx native. |
| DI container | **`samber/do/v2`** | Mỗi service có `module.go` đăng ký providers; `cmd/*` invoke root component. |
| Logger | `log/slog` (stdlib) | JSON handler, structured. |
| Config | `gopkg.in/yaml.v3` + `os.ExpandEnv` | YAML file per service, env interpolation `${VAR}` cho secrets. Xem §18. |
| HTTP client | `go-resty/resty/v2` | Retry, timeout, baseURL — cho gateway/djclient. |
| Validator | `go-playground/validator/v10` | Gin tích hợp sẵn binding tag. |
| Rate limit | `golang.org/x/time/rate` | Token bucket per-key. |
| Cron | `robfig/cron/v3` | `task-cron` schedule. |
| Redis | `redis/go-redis/v9` | Pub/Sub `crm_updates`. |
| Test | `stretchr/testify` + `testcontainers/testcontainers-go` | Unit + integration. |
| Metrics | `prometheus/client_golang` | Expose `:2112/metrics`. |

Quy tắc chọn GORM vs SQLC: xem §17.6.

---

## 10. Compose stack delta

### 10.1. Xoá

```yaml
worker:     # celery worker
  ...

beat:       # celery beat
  ...
```

Không xoá `redis` (vẫn cần DB 1 cache + DB 2 Channels). Chỉ bỏ env `REDIS_CELERY_BROKER_DB`, `REDIS_CELERY_RESULT_DB`.

### 10.2. Thêm

```yaml
  audit-writer:
    build:
      context: ./go-services
      dockerfile: deploy/Dockerfile.audit-writer
    command: ["/app/bin/audit-writer", "-config", "/app/config/audit-writer.yaml"]
    volumes:
      - ./go-services/config/audit-writer.yaml:/app/config/audit-writer.yaml:ro
    environment:
      # Chỉ secrets — config structural ở YAML
      DATABASE_URL: postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}?sslmode=disable
      INTERNAL_AUTH_TOKEN: ${INTERNAL_AUTH_TOKEN:?required}
    depends_on: { postgres: { condition: service_healthy } }
    networks: [desi-net]
    expose: ["8080"]
    restart: unless-stopped

  task-worker:
    build:
      context: ./go-services
      dockerfile: deploy/Dockerfile.task-worker
    command: ["/app/bin/task-worker", "-config", "/app/config/task-worker.yaml"]
    volumes:
      - ./go-services/config/task-worker.yaml:/app/config/task-worker.yaml:ro
    environment:
      DATABASE_URL: postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}?sslmode=disable
      DJANGO_INTERNAL_BASE: "http://web:8000/api/internal"
      INTERNAL_AUTH_TOKEN: ${INTERNAL_AUTH_TOKEN}
      REDIS_URL: "redis://:${REDIS_PASSWORD}@redis:6379/2"
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }
      web:      { condition: service_started }
    networks: [desi-net]
    restart: unless-stopped

  task-cron:
    build:
      context: ./go-services
      dockerfile: deploy/Dockerfile.task-cron
    command: ["/app/bin/task-cron", "-config", "/app/config/task-cron.yaml"]
    volumes:
      - ./go-services/config/task-cron.yaml:/app/config/task-cron.yaml:ro
    environment:
      DATABASE_URL: postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}?sslmode=disable
    depends_on: { postgres: { condition: service_healthy } }
    networks: [desi-net]
    restart: unless-stopped
```

> **Vì sao mount YAML thay vì copy vào image?** Edit lịch cron / tweak concurrency không phải rebuild image — chỉ `docker compose restart task-cron`. Phù hợp với self-deploy không có CI/CD pipeline tự động build.

Django web tăng threads:
```yaml
  web:
    command: >
      sh -c "... exec gunicorn config.asgi:application -k uvicorn.workers.UvicornWorker
             -b 0.0.0.0:8000 --workers ${WEB_WORKERS:-3} --threads ${WEB_THREADS:-4}
             --access-logfile -"
```

---

## 11. Concurrency model — goroutine, channel, mutex

### 11.1. task-worker — Producer-Consumer qua `chan Job`

File: [internal/service/worker/transport/poller.go](go-services/internal/service/worker/transport/poller.go) + [.../consumer.go](go-services/internal/service/worker/transport/consumer.go)

```
                      ┌──────────────────────────────────┐
                      │  Postgres: jobs table            │
                      └──────────────────────────────────┘
                                  ▲
        SELECT FOR UPDATE SKIP    │
        LOCKED LIMIT batchSize    │ UPDATE state='done|failed'
                                  │
       ┌──────────────────────────┴─────────────────────────────┐
       │                                                         │
       ▼ (1 goroutine per queue)                                 │
┌──────────────┐    chan Job (buffered=2N)   ┌──────────────┐    │
│  Producer    │ ─────────────────────────►  │ N Consumers  │ ───┘
│  (poller)    │                              │ (workers)    │
└──────────────┘                              └──────────────┘
       │                                            │
       │ context.Cancel ───────────────────────────┤ context.Cancel
       │ close(chan)                                │
       ▼                                            ▼
   exit → wg.Done()                          drain & exit → wg.Done()
```

**Producer goroutine** (`poller.go`):
```go
func (p *Poller) Run(ctx context.Context) error {
    ticker := time.NewTicker(500 * time.Millisecond) // §15 quyết định #1
    defer ticker.Stop()
    for {
        select {
        case <-ctx.Done():
            close(p.jobs)
            return nil
        case <-ticker.C:
            jobs, err := p.repo.ClaimBatch(ctx, p.queue, batchSize)
            if err != nil {
                p.log.Error("claim", "err", err)
                continue
            }
            for _, j := range jobs {
                select {
                case p.jobs <- j:   // block nếu chan full → backpressure tự nhiên
                case <-ctx.Done():
                    return nil
                }
            }
        }
    }
}
```

**Consumer goroutines** (`consumer.go`):
```go
func (c *Consumer) Run(ctx context.Context) {
    defer c.wg.Done()
    for job := range c.jobs {                           // <-chan Job
        if err := c.limiter.Wait(ctx, job.RateKey); err != nil {  // mutex bên trong
            c.repo.Requeue(ctx, job.ID, time.Second)
            continue
        }
        result, err := c.dispatcher.Dispatch(ctx, job)  // Strategy pattern
        c.repo.Settle(ctx, job, result, err)
    }
}
```

**Khởi tạo** (`wire.go`):
```go
jobsChan := make(chan entity.Job, 2*concurrency) // buffered
var wg sync.WaitGroup
poller := transport.NewPoller(repo, jobsChan, queueName, log)
go poller.Run(ctx)

for i := 0; i < concurrency; i++ {
    wg.Add(1)
    c := transport.NewConsumer(jobsChan, dispatcher, limiter, repo, &wg, log)
    go c.Run(ctx)
}

<-ctx.Done()
wg.Wait()  // chờ tất cả consumer drain xong
```

### 11.2. Rate limiter — `sync.Map` + `*rate.Limiter`

File: [internal/shared/concurrency/ratelimiter/ratelimiter.go](go-services/internal/shared/concurrency/ratelimiter/ratelimiter.go)

```go
type Limiter struct {
    mu       sync.Mutex                       // bảo vệ map khi tạo limiter mới
    limiters map[string]*rate.Limiter         // key = "kiotviet:channel_id=7"
    rps      rate.Limit
    burst    int
}

func (l *Limiter) Wait(ctx context.Context, key string) error {
    l.mu.Lock()
    lim, ok := l.limiters[key]
    if !ok {
        lim = rate.NewLimiter(l.rps, l.burst)
        l.limiters[key] = lim
    }
    l.mu.Unlock()
    return lim.Wait(ctx)   // rate.Limiter tự thread-safe
}
```

> Dùng `sync.Mutex` đơn giản hơn `sync.Map` ở đây vì lookup rất ngắn. Khi `KV_WORKER_REPLICAS > 1` → đổi sang Redis token bucket (`go-redis/redis_rate`).

### 11.3. audit-writer — Batcher pattern (channel + ticker)

File: [internal/shared/concurrency/batcher/batcher.go](go-services/internal/shared/concurrency/batcher/batcher.go)

```
HTTP handler  ──►  in.Add(event)  ──►  chan event (buffered 10k)
                                              │
                                              ▼
                              ┌──────────────────────────┐
                              │ flusher goroutine        │
                              │ - case ev := <-in:       │
                              │     buf = append(buf,ev) │
                              │     if len(buf)>=N: flush│
                              │ - case <-tick:           │
                              │     if len(buf)>0: flush │
                              │ - case <-ctx.Done():     │
                              │     flush rest, exit     │
                              └────────┬─────────────────┘
                                       │ multi-row INSERT
                                       ▼
                                Postgres audit_logs
```

```go
type Batcher[T any] struct {
    in       chan T
    flush    func(ctx context.Context, batch []T) error
    size     int
    interval time.Duration
}

func (b *Batcher[T]) Run(ctx context.Context) {
    buf := make([]T, 0, b.size)
    tick := time.NewTicker(b.interval)
    defer tick.Stop()
    for {
        select {
        case ev := <-b.in:
            buf = append(buf, ev)
            if len(buf) >= b.size {
                _ = b.flush(ctx, buf); buf = buf[:0]
            }
        case <-tick.C:
            if len(buf) > 0 { _ = b.flush(ctx, buf); buf = buf[:0] }
        case <-ctx.Done():
            if len(buf) > 0 { _ = b.flush(context.Background(), buf) }
            return
        }
    }
}
```

Idempotency: handler tra `batch_id` trong `go_audit_batch_marks` trước khi insert (bảo vệ Django retry).

### 11.4. task-cron — robfig/cron + advisory lock

File: [internal/service/cron/transport/scheduler.go](go-services/internal/service/cron/transport/scheduler.go)

```go
c := cron.New(cron.WithLocation(time.FixedZone("Asia/Bangkok", 7*3600)))

c.AddFunc("@every 60s",   wrap("kv.revenue_sync",    "kiotviet", nil))
c.AddFunc("@every 60s",   wrap("fb.daily_spend",     "default",  nil))
c.AddFunc("@every 60s",   wrap("kv.auto_sync_full",  "kiotviet", nil))
c.AddFunc("@every 60s",   wrap("kv.auto_update_code","kiotviet", nil))
c.AddFunc("@every 1h",    wrap("kv.hourly_audit",    "kiotviet", nil))

c.AddFunc("@every 60s",   wrap("orders.sale_reminders", "lark_noti", nil))
c.AddFunc("0 8  * * *",   wrap("orders.no_answer_reminders","lark_noti", payload("start_of_shift")))
c.AddFunc("0 11 * * *",   wrap("orders.no_answer_reminders","lark_noti", payload("morning_mid")))
c.AddFunc("0 15 * * *",   wrap("orders.no_answer_reminders","lark_noti", payload("afternoon_mid")))
c.AddFunc("30 16 * * *",  wrap("orders.no_answer_reminders","lark_noti", payload("end_of_shift")))

c.AddFunc("@every 5m",    requeueStuckJobs)
c.AddFunc("@every 1h",    cleanupOldJobs)
```

`wrap()` = decorator áp idempotency `INSERT ... ON CONFLICT DO NOTHING` vào `go_cron_runs`:
```sql
INSERT INTO go_cron_runs (cron_name, fired_at, fired_by, status)
VALUES ($1, date_trunc('minute', now()), $2, 'scheduled')
ON CONFLICT DO NOTHING
RETURNING id;
-- RETURNING rỗng → instance khác đã fire, skip enqueue.
```

> Phase 1 chạy 1 instance (§15 quyết định #2) — UNIQUE constraint vẫn giữ làm safety net.

### 11.5. Graceful shutdown — `context` + `sync.WaitGroup`

File: [internal/shared/platform/shutdown/shutdown.go](go-services/internal/shared/platform/shutdown/shutdown.go)

```go
func Run(services ...Runnable) error {
    ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
    defer cancel()

    var wg sync.WaitGroup
    for _, s := range services {
        wg.Add(1)
        go func(s Runnable) { defer wg.Done(); _ = s.Run(ctx) }(s)
    }
    <-ctx.Done()
    log.Info("shutdown initiated, waiting drain...")

    drainCtx, drainCancel := context.WithTimeout(context.Background(), 30*time.Second)
    defer drainCancel()
    done := make(chan struct{})
    go func() { wg.Wait(); close(done) }()
    select {
    case <-done:
        return nil
    case <-drainCtx.Done():
        return errors.New("shutdown timed out")
    }
}
```

Job đang chạy mà worker phải bỏ → bảo vệ qua `requeue_stuck_jobs` cron (chạy mỗi 5 phút):
```sql
UPDATE jobs SET state='pending', locked_at=NULL, locked_by=NULL
WHERE state='running' AND locked_at < now() - interval '15 minutes';
```

---

## 12. Lộ trình triển khai

Tổng: **6-8 tuần**, 1 dev biết Go + 1 dev Python.

### Phase 1 — Hạ tầng dispatch + jobs table (1 tuần)
1. Migration: `0001_jobs_table` + `0002_go_cron_runs` + `0003_audit_batch_marks`.
2. Tạo `apps/dispatch/` wrapper với fallback Celery.
3. Tạo `apps/internal/` skeleton với auth middleware.
4. **Codemod 28 call site** → `dispatch(...)`. Mỗi call site đều giữ fallback Celery (env `TASK_BACKEND=celery` cho phase 1-3).
5. Test full suite — đảm bảo không regression với Celery vẫn chạy.

**Tiêu chí pass**: Celery vẫn xử lý 100% task qua `dispatch()` wrapper, 0 regression.

### Phase 2 — audit-writer + task-cron (1.5 tuần)
1. Scaffold `go-services/`, build 3 binary `audit-writer`, `task-worker` (chưa Pattern B), `task-cron`.
2. Implement `audit-writer` HTTP handler + Django HTTP client buffer + flag `AUDIT_BACKEND=go`.
3. Implement `task-cron` đẩy job vào `jobs` table cho 5 cron Pattern A (`kv.revenue_sync`, `fb.daily_spend`, `kv.auto_sync_full`, `kv.auto_update_code`, `kv.hourly_audit`).
4. Trong `task-worker`: implement 5 native Go handler tương ứng.
5. Disable 5 `PeriodicTask` Celery beat tương ứng (set `enabled=False`).

**Tiêu chí pass**:
- Audit throughput ≥ 2× Celery cũ, 0 mất log trong 48h.
- `kv.hourly_audit` từ 30 phút → < 10 phút.
- Cron Go + celery-beat **không trùng** (PeriodicTask đã disable).

### Phase 3 — task-worker Pattern A cho ad-hoc task (1.5 tuần)
1. Implement handler Pattern A còn lại: `kv.process_job`, `kv.webhook_push_old`, `kv.webhook_sync_old_status`, 4 zalo task, `lark.notify_order_created`.
2. Bật flag `TASK_BACKEND=go` cho từng task riêng lẻ (bằng dict env `TASK_BACKEND_OVERRIDE={"kv.process_job":"go", ...}`).
3. Canary mỗi task 24h, so sánh count + latency với Celery (split traffic theo modulo `id`).
4. Cutover từng task khi pass canary.

**Tiêu chí pass**: Mỗi task success rate ≥ Celery, latency p95 ≤ Celery, rate-limit KiotViet không vi phạm.

### Phase 4 — Pattern B proxy (1 tuần)
1. Implement `INTERNAL_TASK_REGISTRY` + endpoint `POST /api/internal/tasks/{name}/run`.
2. Implement `djangoProxy` handler trong task-worker.
3. Bật flag `TASK_BACKEND=go` cho 4 task Pattern B.
4. Test load: `process_restore_orders` với batch 1000 đơn — đo latency Django web.

**Tiêu chí pass**:
- 4 task Pattern B chạy ổn 1 tuần.
- Django web p95 không tăng > 10% (đo qua Prometheus).
- Không có gunicorn worker exhaustion.

### Phase 5 — Cutover toàn bộ (3 ngày)
1. Set `TASK_BACKEND=go` toàn cục.
2. Set `AUDIT_BACKEND=go` toàn cục.
3. Disable **mọi** `PeriodicTask.enabled=False`.
4. Stop `worker` + `beat` container, **giữ image** (rollback).
5. Smoke test toàn bộ flow critical (order create, ingest, webhook, restore).
6. Monitor 48h.

### Phase 6 — Dọn dẹp (sau cutover 14 ngày stable)
1. Xoá `worker` + `beat` block khỏi `docker-compose.full.yml`.
2. Xoá Celery imports khỏi code Python (`from celery import shared_task` → đổi `@shared_task` thành no-op decorator hoặc dọn hẳn).
3. Xoá `redis` env `REDIS_CELERY_BROKER_DB`, `REDIS_CELERY_RESULT_DB`.
4. Drop `requirements.txt`: `celery`, `kombu`, `billiard`, `vine`, `amqp`, `django_celery_beat`.
5. Drop bảng `django_celery_beat_*` qua migration.
6. Xoá `apps/dispatch/registry.py` (fallback Celery path).

**Tiết kiệm RAM thực tế đo được ở phase này** (so với baseline).

### Phase 7 — Deploy unified docker compose (1 tuần)

Chuyển production từ supervisor + jenkins sang docker compose toàn bộ. Chi tiết kế hoạch ở **§19**.

Tóm tắt:
1. Staging test với compose stack đầy đủ — Django web + Frontend + 3 Go service.
2. Backup Postgres + Redis (state hiện tại).
3. Cutover prod: stop supervisor → restore data vào volume docker → `docker compose up -d`.
4. Verify health + smoke test.
5. Decommission supervisor + jenkins sau 1 tuần stable.

**Tiêu chí pass**: 1 lệnh `docker compose up -d` khởi động toàn bộ stack; không còn service nào ngoài docker; jenkins job đã disable.

---

## 13. Rollback

Mỗi pha có cờ riêng để gạt về Celery:

| Cờ env | Default | Effect |
|--------|---------|--------|
| `TASK_BACKEND` | `celery` | `go` → enqueue vào `jobs` table; `celery` → fallback `.apply_async()` |
| `TASK_BACKEND_OVERRIDE` | `{}` | per-task override, vd. `{"kv.process_job":"go"}` |
| `AUDIT_BACKEND` | `celery` | `go` → HTTP audit-writer; `celery` → `create_audit_log_task.delay()` |
| `PeriodicTask.enabled` (DB) | `True` | Set `False` để tắt celery-beat schedule khi Go cron đảm nhiệm |

Trong Phase 6 mới xoá fallback path. Tới Phase 5 vẫn có thể flip về Celery trong 1 phút bằng:
```bash
# 1. Set TASK_BACKEND=celery in env
# 2. UPDATE periodic_task SET enabled=True WHERE name IN (...);
# 3. docker compose up worker beat
# 4. (Optional) Replay job đang pending trong jobs table sang Celery bằng script
```

---

## 14. Rủi ro & mitigation

| Rủi ro | Mức | Mitigation |
|--------|-----|-----------|
| Postgres bị nóng do polling | Trung | Index `jobs_dispatch_idx` partial. Idle worker poll 500ms (config). Cân nhắc `LISTEN/NOTIFY` để đánh thức khi có job mới. |
| Go ghi `audit_logs` schema lệch sau Django migration | Trung | Manual pre-deploy: `manage.py inspectdb audit_logs` → diff với struct GORM trong `entity/audit_log.go`. Hoặc git pre-push hook. |
| Go decrypt Fernet (KV token) sai | Cao | Go **không decrypt** — gọi `GET /api/internal/kiotviet/.../access-token`, Django decrypt. |
| Pattern B làm Django web exhaust gunicorn worker | Trung | Tăng `--threads`. Nếu vẫn không đủ → tách container `desi-django-runner`. |
| Cron Go fire trùng (2 instance) | Cao | `go_cron_runs UNIQUE(cron_name, fired_at)` + `pg_advisory_lock`. |
| Job stuck `state='running'` do worker crash | Trung | Cron `requeue_stuck_jobs` chạy mỗi 5 phút. |
| Rate-limit KiotViet bị vượt | Trung | Token bucket per channel (in-memory); nếu deploy multi-instance task-worker → đổi sang Redis rate-limit. |
| HTTP timeout giữa Go ↔ Django (Pattern B chạy lâu) | Trung | Set timeout Go client = 600s cho proxy handler. Restore task có thể vài phút. |
| Migration Django mới làm `Job.objects.create` lệch | Thấp | `dispatch.queue` dùng raw SQL `INSERT` (không phụ thuộc ORM model trong Go). Trong Django dùng ORM. |
| Build complexity (Python + Go) | Thấp | Makefile root, compose `--profile go-only` cho dev. |
| Maintainer chỉ biết Python | Trung | Mỗi binary Go nhỏ (~500-800 dòng), idiomatic, có code review pattern. Bắt đầu phase 2 (audit-writer) là đơn giản nhất. |

---

## 15. Quyết định đã chốt (2026-05-14)

| # | Câu hỏi | Quyết định | Trigger để xem lại |
|---|---------|-----------|--------------------|
| 1 | Worker đánh thức bằng gì? | **Polling 500ms** phase đầu | Postgres CPU > 30% do `jobs_dispatch_idx` scan → đổi sang `LISTEN/NOTIFY` |
| 2 | Số instance task-worker phase 1? | **1 instance** | Metric `jobs_backlog{queue}` > 100 sustained 10 phút → scale ngang |
| 3 | Pattern B endpoint chạy ở đâu? | **Chung Django web container**, tăng `--threads 4` | Gunicorn worker saturation > 70% hoặc REST p95 tăng > 10% → tách container `desi-django-runner` |
| 4 | Cron schedule khai báo ở đâu? | **YAML config** (`config/task-cron.yaml`) — không UI CRUD phase 1, edit YAML + restart container | Sau cutover (Phase 6) → thêm bảng `cron_schedules` + admin UI React |
| 5 | Khi nào xoá `celery` khỏi `requirements.txt`? | **Phase 6**, sau 14 ngày stable post-cutover | – |
| 6 | HTTP framework Go? | **`gin-gonic/gin`** | Performance issue cụ thể đo được (chưa từng thấy ở scale này) → cân nhắc `fiber` hoặc `chi`. |
| 7 | Query strategy? | **GORM cho query đơn giản, SQLC cho query phức tạp** (xem §17.6) | Nếu phải maintain > 50% SQL trong GORM raw string → port hẳn sang SQLC. |
| 8 | DI container? | **`samber/do/v2`** | Build time chậm hoặc panic khó debug → cân nhắc `wire` (codegen) nhưng phase 1 không cần. |

Các quyết định này được phản ánh trong §9.3 (stack), §11 (concurrency), §17.1 (composition), §17.6 (ORM strategy).

---

## 16. Tổng kết — vì sao v0.3 đáng làm

| Tiêu chí | v0.1 gRPC | v0.2 hybrid REST | **v0.3 full Celery kill** |
|---|---|---|---|
| Thời gian | 3-5 tuần | 3-5 tuần | **6-8 tuần** |
| Tiết kiệm RAM | ~200MB | ~400MB | **~1GB** |
| Phải port Fernet / address parser / PDF | Không | Không | Không |
| Phải port WS + JWT + RBAC | Không | Không | Không |
| Phải dựng proto toolchain | Có | Không | Không |
| Phải port code Python phức tạp (sale_reminder, restore, landing_ingest) | Không | Không | **Không** (Pattern B) |
| Số binary Go | 3 | 3 | 3 |
| Số call site Django đổi | 1 (middleware) | 1 + 2 task path | **28** (codemod) |
| Celery còn lại sau cutover | Có | Có (cho 6 task) | **0** |
| Rollback | Dễ | Dễ | Dễ tới Phase 5 |

**ROI rõ ràng**:
- ~1GB RAM saved → có thể downsize VPS hoặc dùng RAM đó cho Postgres shared_buffers.
- Concurrency Go tốt hơn rõ rệt cho fan-out task (`hourly_audit` 30 → 10 phút).
- Loại bỏ broker Redis → bớt 1 failure mode.
- Codebase đơn giản hơn sau Phase 6 (không còn Celery + beat + worker container).

**Trade-off**: thời gian gấp đôi v0.2 vì phải đụng 28 call site + cài Pattern B proxy. Đổi lại được mục tiêu chính bạn đặt ra.

**Khuyến nghị**: bắt đầu Phase 1 (dispatch wrapper + codemod) — đây là phần ít rủi ro nhất, không đụng infra, dễ rollback (vì vẫn chạy Celery underneath). Sau Phase 1, mới quyết định có đi tiếp Phase 2+ hay không.

---

## 17. Clean Architecture & Design Patterns

### 17.1. 6 layer per microservice

| Layer | Thư mục | Trách nhiệm | Phụ thuộc cho phép |
|-------|---------|-------------|--------------------|
| **entity** | `entity/` | Pure struct + domain method. State machine của `Job`, validation `AuditLog`. | Không gì cả (chỉ stdlib). |
| **dto** | `dto/` | Wire shape — request/response body, payload từng `task_type`. | `entity` (mapping). |
| **port** | `port/` | Interface declare ở phía business — `JobRepository`, `RateLimiter`, `EventPublisher`. | `entity`. |
| **business** | `business/` | Use case: orchestrate, gọi port, áp business rule. | `entity`, `port`. **KHÔNG** biết pgx/http/redis. |
| **repository / gateway** | `repository/`, `shared/gateway/` | Adapter: implement port bằng pgx/resty/redis. | `entity`, `port`, `shared/platform`. |
| **transport** | `transport/` | Controller: nhận HTTP/poll event, parse DTO, gọi business, trả response. | `business`, `dto`. |

**Composition root** ở `module.go` mỗi service — chỗ duy nhất biết tất cả concrete type. Dùng **`samber/do/v2`** làm DI container: providers đăng ký vào `do.Injector`, lấy ra qua `do.MustInvoke[T](inj)`.

Ví dụ `module.go` của audit service:
```go
// internal/service/audit/module.go
package audit

import (
    "github.com/samber/do/v2"
    "github.com/gin-gonic/gin"
    "gorm.io/gorm"

    "go-services/internal/service/audit/business"
    "go-services/internal/service/audit/port"
    "go-services/internal/service/audit/repository"
    "go-services/internal/service/audit/transport"
    "go-services/internal/shared/platform/pg"
)

func Register(inj do.Injector) {
    // Repositories (GORM cho query đơn giản — xem §17.6)
    do.Provide(inj, func(i do.Injector) (port.AuditRepository, error) {
        return repository.NewGormAudit(do.MustInvoke[*gorm.DB](i)), nil
    })
    do.Provide(inj, func(i do.Injector) (port.IdempotencyStore, error) {
        return repository.NewGormIdempotency(do.MustInvoke[*gorm.DB](i)), nil
    })

    // Business
    do.Provide(inj, business.NewAuditService)   // tự resolve port qua injector

    // Transport
    do.Provide(inj, transport.NewHandler)
    do.Provide(inj, transport.NewRouter)        // return *gin.Engine
}
```

Platform providers đăng ký 1 lần ở entrypoint:
```go
// cmd/audit-writer/main.go
package main

import (
    "flag"

    "github.com/samber/do/v2"
    "go-services/internal/service/audit"
    "go-services/internal/shared/platform/config"
    "go-services/internal/shared/platform/di"
    "go-services/internal/shared/platform/shutdown"
)

func main() {
    cfgPath := flag.String("config", "/app/config/audit-writer.yaml", "path to config YAML")
    flag.Parse()

    cfg := config.MustLoad[audit.Config](*cfgPath)   // YAML + ${ENV} interpolation, xem §18
    inj := do.New()
    defer inj.Shutdown()        // gọi tất cả `Shutdownable.Shutdown()` ngược thứ tự provide

    di.ProvidePlatform(inj, cfg)   // *pgxpool.Pool, *gorm.DB, *sqlcdb.Queries, slog.Logger, prometheus.Registry
    audit.Register(inj)

    router := do.MustInvoke[*gin.Engine](inj)
    shutdown.Run(serveHTTP(router, cfg.HTTP.Listen))
}
```

`di.ProvidePlatform()` gom các provider dùng chung:
```go
// internal/shared/platform/di/platform.go
func ProvidePlatform[C Config](inj do.Injector, cfg C) {
    do.Provide(inj, func(_ do.Injector) (*pgxpool.Pool, error) {
        return pg.OpenPool(context.Background(), cfg.DatabaseURL())
    })
    do.Provide(inj, func(i do.Injector) (*gorm.DB, error) {
        return pg.NewGorm(do.MustInvoke[*pgxpool.Pool](i))
    })
    do.Provide(inj, func(i do.Injector) (*sqlcdb.Queries, error) {
        return sqlcdb.New(do.MustInvoke[*pgxpool.Pool](i)), nil
    })
    do.Provide(inj, func(_ do.Injector) (*slog.Logger, error) {
        return logger.NewJSON(cfg.LogLevel()), nil
    })
}
```

> Lưu ý Go idiom: **interface declare ở consumer side**, không ở producer side. `port/port.go` ở trong `business/` namespace logic, đặt cạnh business để rõ "đây là cái business cần". Repository implement interface đó — không cần "implements" annotation, Go duck-types.

> Lưu ý samber/do: dùng **`do.Provide`** (lazy resolve khi MustInvoke) thay vì `do.ProvideValue` (eager). Mỗi provider trả `(T, error)` để báo lỗi khởi tạo. Nếu T implement `do.Shutdownable` (`Shutdown() error`), `inj.Shutdown()` sẽ tự gọi → pool/connection được close đúng thứ tự.

### 17.2. Design patterns dùng ở đâu

| Pattern | Vị trí | Mục đích |
|---------|--------|---------|
| **Repository** | `repository/pg_*.go` ↔ `business/port/*.go` | Tách persistence khỏi business; mock dễ trong test. |
| **Adapter** | `shared/gateway/{kiotviet,lark,zalo,facebook}/` | Dịch model nội bộ ↔ payload external API. Strategy ẩn cho client thật vs mock. |
| **Strategy** | `worker/business/dispatcher.go` (`map[string]TaskHandler`) | Route `task_type` → handler tương ứng (15 Pattern A + 1 djangoProxy cho 4 Pattern B). Thêm task mới = đăng ký 1 entry. |
| **Producer-Consumer** | `worker/transport/{poller,consumer}.go` + `chan Job` | Decouple SELECT FOR UPDATE và xử lý task. Backpressure tự nhiên. |
| **Token Bucket** | `shared/concurrency/ratelimiter/` (mutex + `*rate.Limiter`) | Rate-limit KiotViet 12/m per channel. |
| **Batcher** | `shared/concurrency/batcher/` (channel + ticker) | Gom audit event 200/200ms → 1 multi-row INSERT. |
| **Decorator (Middleware)** | `shared/platform/httpserver/middleware/` | Chain: recover → logger → metrics → auth → handler. |
| **Factory** | `worker/business/handlers/django_proxy.go` | Sinh handler cho 4 task Pattern B dùng chung HTTP client. |
| **Outbox-lite** | `jobs` table (Postgres) | Django transactional enqueue: `Order.create()` + `Job.create()` cùng tx → consistency tuyệt đối. |
| **Idempotency Key** | `go_audit_batch_marks(batch_id)`, `go_cron_runs(cron_name, fired_at)` | Bảo vệ retry và multi-instance fire. |
| **Advisory Lock** | `shared/concurrency/advisorylock/` (`pg_advisory_lock`) | Single-flight cho cron heavy như `kv.hourly_audit`. |
| **State Machine** | `entity/job.go` | Job: `pending → running → done|failed|dead`. Transition rule + invariant ở entity layer. |

### 17.3. Quy tắc interface

1. **Tên interface = role, không phải implementation.** `JobRepository` không phải `PgJobRepository`. Concrete struct mới có prefix tech: `PgJobRepo`, `RedisRateLimiter`.
2. **Interface nhỏ.** 1-3 method là lý tưởng. Tách lớn thành nhỏ: `JobClaimer`, `JobSettler` thay vì 1 interface 10 method.
3. **Declare cạnh consumer.** `business/port/job_port.go` chứ không phải `repository/port.go`.
4. **Không export khi không cần.** Nếu chỉ dùng nội bộ service, interface ở package private (`internal/service/worker/business/`).

Ví dụ:
```go
// internal/service/worker/business/port/job_port.go
package port

type JobClaimer interface {
    ClaimBatch(ctx context.Context, queue string, n int) ([]entity.Job, error)
}

type JobSettler interface {
    Settle(ctx context.Context, job entity.Job, result []byte, err error) error
    Requeue(ctx context.Context, jobID int64, delay time.Duration) error
}

type EventPublisher interface {
    Publish(ctx context.Context, channel string, payload []byte) error
}
```

Repository implement tất cả, business chỉ depend vào interface nhỏ:
```go
// business/service.go
type Service struct {
    claimer  port.JobClaimer
    settler  port.JobSettler
    events   port.EventPublisher
    ...
}
```

Test mock chỉ cần stub 1-3 method, không phải toàn bộ.

### 17.4. Testing pyramid

| Layer | Test type | Tool |
|-------|-----------|------|
| `entity/` | Unit thuần | `testing` stdlib |
| `business/` | Unit + mock port | `testify/mock` hoặc `gomock` |
| `repository/` | Integration với Postgres thật | `testcontainers-go` Postgres |
| `gateway/` | Contract test với HTTP mock | `httptest.Server` |
| `transport/` | Handler test | `httptest.NewRecorder` |
| End-to-end | 1 service chạy thật + Postgres | docker-compose test stack |

Mục tiêu coverage:
- `entity`, `business`: ≥ 80%.
- `repository`, `gateway`: ≥ 60% (integration).
- `transport`: ≥ 70%.

### 17.5. Module boundaries — không leak

Nguyên tắc enforce:
- `cmd/<service>/main.go` chỉ gọi `service/<name>/wire.go::New(cfg)` — không import trực tiếp `business`, `repository`, `transport`.
- `shared/*` không bao giờ import `service/*` (cross-cutting không phụ thuộc business cụ thể).
- `service/<a>` không import `service/<b>` (microservice isolation).
- Mọi communication giữa 2 service Go (audit-writer ↔ task-worker chẳng hạn) phải đi qua HTTP/DB/Redis, không gọi function trực tiếp.

Lint script chạy local (optional):
```yaml
# go-services/.go-arch-lint.yml — chạy `go-arch-lint check`
- forbid: service/* importing other service/*
- forbid: shared/* importing service/*
- forbid: entity/* importing anything outside stdlib
- forbid: business/* importing repository|gateway|transport
```

Chạy thủ công trước khi deploy hoặc thêm vào git pre-push hook nếu muốn.

### 17.6. ORM strategy — GORM cho đơn giản, SQLC cho phức tạp

Cả 2 dùng chung `*pgxpool.Pool` (chia sẻ connection):

```go
// shared/platform/pg/gorm.go
func NewGorm(pool *pgxpool.Pool) (*gorm.DB, error) {
    sqlDB := stdlib.OpenDBFromPool(pool)
    return gorm.Open(postgres.New(postgres.Config{Conn: sqlDB}), &gorm.Config{
        SkipDefaultTransaction: true,   // tự control tx, tránh wrap không cần
        PrepareStmt:            true,
    })
}

// shared/platform/pg/sqlcdb/querier.go (generated)
func New(db DBTX) *Queries { return &Queries{db: db} }
```

#### Quy tắc chọn

| Loại truy vấn | Tool | Lý do | Ví dụ trong codebase |
|---------------|------|-------|----------------------|
| INSERT/UPDATE/DELETE 1 row | **GORM** | API gọn, không cần SQL string | `audit_logs` INSERT, `Job.Save()` |
| Get-by-id, list ≤ 2 điều kiện WHERE | **GORM** | Chained query, type-safe | `Job.First(&j, id)` |
| Bulk INSERT nhỏ (≤ 200 row) | **GORM** `CreateInBatches` | Tự chunk + tx | Audit batch flush |
| Soft delete, scope, hook | **GORM** | Built-in | – |
| `FOR UPDATE SKIP LOCKED` | **SQLC** | GORM raw không type-safe | `jobs.ClaimBatch` |
| `INSERT ... ON CONFLICT ... RETURNING` | **SQLC** | GORM clause builder lằng nhằng | `go_cron_runs` idempotency |
| CTE, window function | **SQLC** | GORM không support cleanly | Aggregation, reporting |
| Filter động ≥ 3 điều kiện | **SQLC** | Generated argument type-safe | Future: jobs admin search |
| `UPDATE ... WHERE state='running' AND locked_at<...` | **SQLC** | Conditional update + RETURNING affected | `requeue_stuck_jobs` |
| Bulk update với điều kiện phức tạp | **SQLC** | – | Cleanup, archive |

#### File SQLC source cho desi-order

```sql
-- go-services/db/queries/jobs.sql

-- name: ClaimBatch :many
SELECT id, queue, task_type, payload, attempts, max_attempts
FROM jobs
WHERE queue = $1 AND state = 'pending' AND run_after <= now()
ORDER BY priority DESC, id ASC
FOR UPDATE SKIP LOCKED
LIMIT $2;

-- name: MarkRunning :exec
UPDATE jobs
SET state = 'running', locked_at = now(), locked_by = $2, attempts = attempts + 1
WHERE id = ANY(@ids::bigint[]);

-- name: SettleDone :exec
UPDATE jobs
SET state = 'done', finished_at = now(), result = $2, locked_by = NULL
WHERE id = $1;

-- name: SettleFailed :exec
UPDATE jobs
SET state = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'pending' END,
    run_after = CASE WHEN attempts >= max_attempts THEN run_after ELSE now() + ($3 * interval '1 second') END,
    last_error = $2,
    locked_by = NULL
WHERE id = $1;

-- name: RequeueStuckJobs :execrows
UPDATE jobs
SET state = 'pending', locked_at = NULL, locked_by = NULL
WHERE state = 'running' AND locked_at < now() - interval '15 minutes';
```

```sql
-- go-services/db/queries/cron_runs.sql

-- name: RecordCronFire :one
INSERT INTO go_cron_runs (cron_name, fired_at, fired_by, status)
VALUES ($1, date_trunc('minute', now()), $2, 'scheduled')
ON CONFLICT (cron_name, fired_at) DO NOTHING
RETURNING id;
-- Nếu RETURNING rỗng → instance khác đã fire phút này, skip enqueue.
```

```sql
-- go-services/db/queries/audit_logs.sql

-- name: InsertAuditLogs :copyfrom
INSERT INTO audit_logs (
    user_id, action_type, entity_type, entity_id, method, ip_address,
    response_data, duration_ms, source, curl_command, metadata, created_at
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
);
-- :copyfrom dùng pgx COPY protocol → nhanh hơn multi-row INSERT 5-10×.
```

#### Workflow generate code

```bash
# go-services/Makefile
sqlc-gen:
	cd db && sqlc generate

sqlc-verify:
	cd db && sqlc vet

snapshot:
	pg_dump -s -h postgres -U $$POSTGRES_USER $$POSTGRES_DB > db/schema/snapshot.sql
```

Khi Django migration thêm bảng/cột mà SQLC cần đọc:
1. Migration Django apply lên dev DB.
2. `make snapshot` → cập nhật `db/schema/snapshot.sql`.
3. Cập nhật `db/queries/*.sql` nếu cần query mới.
4. `make sqlc-gen` → regen `internal/shared/platform/pg/sqlcdb/`.
5. Code review, commit cả snapshot + generated code.

#### Repository wrap cả 2

Mỗi repository có thể **mix** GORM + SQLC trong cùng struct:
```go
// internal/service/worker/repository/sqlc_jobs.go
type JobRepo struct {
    q  *sqlcdb.Queries        // SQLC cho claim/settle phức tạp
    db *gorm.DB                // GORM cho query phụ trợ đơn giản
}

func NewJobRepo(q *sqlcdb.Queries, db *gorm.DB) port.JobClaimer { ... }

func (r *JobRepo) ClaimBatch(ctx context.Context, queue string, n int) ([]entity.Job, error) {
    rows, err := r.q.ClaimBatch(ctx, sqlcdb.ClaimBatchParams{Queue: queue, Limit: int32(n)})
    // map rows → entity.Job
}

func (r *JobRepo) CountByQueue(ctx context.Context, queue string) (int64, error) {
    var n int64
    return n, r.db.WithContext(ctx).Model(&entity.Job{}).Where("queue=?", queue).Count(&n).Error
}
```

Cả 2 chia sẻ pool và tham gia cùng transaction nếu cần (pass `pgx.Tx` cho SQLC, `db.WithContext(ctx).Transaction(...)` cho GORM với cùng underlying conn).

---

## 18. Configuration — YAML + env interpolation

### 18.1. Quy tắc tách

| Loại giá trị | Ở đâu | Lý do |
|--------------|-------|-------|
| **Secrets** (DATABASE_URL, INTERNAL_AUTH_TOKEN, REDIS_URL) | Env var | Không commit vào repo. Mỗi env (dev/staging/prod) tự set. |
| **Structural** (timeout, concurrency, batch size, rate limit) | YAML file | Versioned trong git, dễ review diff, không cần rebuild image khi đổi. |
| **Cron schedule** (cron expr + payload) | YAML file | Edit YAML + restart container, không cần code change. |

YAML reference env qua `${VAR}` — load time interpolation:
```yaml
database:
  url: "${DATABASE_URL}"   # giá trị thật lấy từ env khi service start
```

### 18.2. Loader

```go
// internal/shared/platform/config/load.go
package config

import (
    "fmt"
    "os"

    "gopkg.in/yaml.v3"
)

func MustLoad[T any](path string) T {
    cfg, err := Load[T](path)
    if err != nil {
        panic(fmt.Sprintf("config load %s: %v", path, err))
    }
    return cfg
}

func Load[T any](path string) (T, error) {
    var cfg T
    raw, err := os.ReadFile(path)
    if err != nil {
        return cfg, fmt.Errorf("read: %w", err)
    }
    // Thay ${VAR} bằng giá trị env. Nếu env chưa set → giữ nguyên ""
    // (sẽ fail validate sau, dễ debug hơn là silent default).
    expanded := os.ExpandEnv(string(raw))
    if err := yaml.Unmarshal([]byte(expanded), &cfg); err != nil {
        return cfg, fmt.Errorf("unmarshal: %w", err)
    }
    if v, ok := any(&cfg).(validator); ok {
        if err := v.Validate(); err != nil {
            return cfg, fmt.Errorf("validate: %w", err)
        }
    }
    return cfg, nil
}

type validator interface{ Validate() error }
```

### 18.3. `config/audit-writer.yaml`

```yaml
http:
  listen: ":8080"
  read_timeout: 10s
  write_timeout: 30s
  internal_token: "${INTERNAL_AUTH_TOKEN}"

database:
  url: "${DATABASE_URL}"
  max_conns: 10
  min_conns: 2
  conn_max_lifetime: 30m

batch:
  flush_threshold: 200          # số event/batch
  flush_interval: 200ms         # tối đa giữ event
  queue_buffer: 10000           # in-memory queue size trước khi reject

logger:
  level: "info"                 # debug | info | warn | error
  format: "json"

metrics:
  listen: ":2112"
  path: "/metrics"
```

Struct Go tương ứng:
```go
// internal/service/audit/config.go
package audit

import "time"

type Config struct {
    HTTP     HTTPConfig     `yaml:"http"`
    Database DBConfig       `yaml:"database"`
    Batch    BatchConfig    `yaml:"batch"`
    Logger   LoggerConfig   `yaml:"logger"`
    Metrics  MetricsConfig  `yaml:"metrics"`
}

type HTTPConfig struct {
    Listen        string        `yaml:"listen"`
    ReadTimeout   time.Duration `yaml:"read_timeout"`
    WriteTimeout  time.Duration `yaml:"write_timeout"`
    InternalToken string        `yaml:"internal_token"`
}

type BatchConfig struct {
    FlushThreshold int           `yaml:"flush_threshold"`
    FlushInterval  time.Duration `yaml:"flush_interval"`
    QueueBuffer    int           `yaml:"queue_buffer"`
}

func (c Config) Validate() error {
    if c.HTTP.InternalToken == "" {
        return errors.New("http.internal_token (INTERNAL_AUTH_TOKEN) required")
    }
    if c.Database.URL == "" {
        return errors.New("database.url (DATABASE_URL) required")
    }
    return nil
}
```

### 18.4. `config/task-worker.yaml`

```yaml
database:
  url: "${DATABASE_URL}"
  max_conns: 20
  min_conns: 4

django_internal:
  base_url: "${DJANGO_INTERNAL_BASE}"
  token: "${INTERNAL_AUTH_TOKEN}"
  timeout: 600s                 # Pattern B có thể chạy vài phút (restore_orders)
  retries: 2

redis:
  url: "${REDIS_URL}"
  pubsub_channel: "crm_updates"

worker:
  worker_id_prefix: "task-worker"
  poll_interval: 500ms          # §15 quyết định #1
  shutdown_grace: 30s

queues:
  - name: default
    concurrency: 4
    batch_claim: 5
  - name: orders
    concurrency: 4
    batch_claim: 5
  - name: kiotviet
    concurrency: 6
    batch_claim: 3
    rate_limit:
      per_minute: 12
      per_key: "channel_id"     # key trong payload để tính rate-limit
  - name: zalo_bridge
    concurrency: 4
    batch_claim: 5
  - name: lark_noti
    concurrency: 2
    batch_claim: 3

logger:
  level: "info"
  format: "json"

metrics:
  listen: ":2112"
```

### 18.5. `config/task-cron.yaml`

```yaml
database:
  url: "${DATABASE_URL}"

timezone: "Asia/Bangkok"

# Pattern A — Go xử lý 100%
crons:
  - name: kv.revenue_sync
    schedule: "@every 60s"
    queue: kiotviet
  - name: kv.auto_sync_full
    schedule: "@every 60s"
    queue: kiotviet
  - name: kv.auto_update_code
    schedule: "@every 60s"
    queue: kiotviet
  - name: kv.hourly_audit
    schedule: "@every 1h"
    queue: kiotviet
  - name: fb.daily_spend
    schedule: "@every 60s"
    queue: default

# Pattern B — Go enqueue, Django sync chạy
  - name: orders.sale_reminders
    schedule: "@every 60s"
    queue: lark_noti

  # KNM 4 mốc/ngày
  - name: orders.no_answer_reminders
    schedule: "0 8 * * *"
    queue: lark_noti
    payload:
      slot: start_of_shift
  - name: orders.no_answer_reminders
    schedule: "0 11 * * *"
    queue: lark_noti
    payload:
      slot: morning_mid
  - name: orders.no_answer_reminders
    schedule: "0 15 * * *"
    queue: lark_noti
    payload:
      slot: afternoon_mid
  - name: orders.no_answer_reminders
    schedule: "30 16 * * *"
    queue: lark_noti
    payload:
      slot: end_of_shift

# Housekeeping (chạy trực tiếp, không enqueue)
housekeeping:
  - name: requeue_stuck_jobs
    schedule: "@every 5m"
  - name: cleanup_old_jobs
    schedule: "@every 1h"
    params:
      retention_days: 7

logger:
  level: "info"
  format: "json"

metrics:
  listen: ":2112"
```

### 18.6. Quy trình edit config

1. Edit YAML trong `go-services/config/<service>.yaml` ở repo.
2. Commit + push.
3. Trên máy deploy: `git pull` → `docker compose restart <service>`.
4. Container đọc YAML mới qua volume mount → service start với config mới.

Không cần rebuild image. Không cần CI/CD pipeline.

### 18.7. Validate trước deploy

Thêm subcommand `validate` cho mỗi binary để check config trước khi restart:
```bash
docker compose run --rm task-cron /app/bin/task-cron -config /app/config/task-cron.yaml -validate
```
Exit code 0 = OK, ≠ 0 = lỗi (in chi tiết). Chạy thủ công trước `docker compose restart`.

### 18.8. Multi-environment (sau này)

Phase 1 chỉ có 1 file/service. Nếu sau này cần env riêng (staging/prod), pattern:
```
config/
├── audit-writer.yaml              # base
├── audit-writer.staging.yaml      # override staging
└── audit-writer.prod.yaml         # override prod
```
Loader merge `base` + `<env>` (env quyết bởi env var `APP_ENV`). Không làm phase 1.

---

## 19. Deploy migration — supervisor + jenkins → docker compose

### 19.1. Hiện trạng (đã xác nhận 2026-05-14)

| Component | Cách deploy hiện tại |
|-----------|---------------------|
| **Server** | 1 VPS — **Ubuntu 22.04**, **8GB RAM**, **4 vCPU** |
| **Domain** | `customer.delax.vn` (production), proxy qua Cloudflare |
| Django web (gunicorn ASGI) | Supervisor managed process bind `127.0.0.1:8000` |
| Celery worker + beat | Supervisor managed process |
| Frontend (React build) | Static files build sẵn ở `/home/desi/desi-order/frontend/dist`, **nginx host** serve trực tiếp |
| Postgres 16 | **Container** (chạy ngoài compose này — cần xác nhận tên container và volume) |
| Redis 7 | **Container** (tương tự) |
| Reverse proxy + SSL | **Nginx host** với cert Cloudflare Origin (15 năm): `/etc/ssl/certs/delax.vn.pem` + `/etc/ssl/private/delax.vn.key`. Listen 80 (redirect) + 443. |
| CI/CD | Jenkins — chỉ dùng cho desi-order → sẽ **gỡ hẳn** sau Phase 8 |
| Backup cron | **Chưa có** — cần thêm trước Phase 7 |
| Monitoring | Chưa bật, sẽ bật `--profile monitoring` ở Phase 7 |

> File [docker-compose.full.yml](../docker-compose.full.yml) đã có sẵn (postgres + redis + web + worker + beat + frontend + nginx + monitoring) nhưng **chưa dùng cho production**. Sẽ được cập nhật trong Phase 6 (bỏ worker + beat) và Phase 7 (cấu hình nginx + SSL).

### 19.2. Target state (sau Phase 7)

```
┌──────────────────────────────────────────────────────────────┐
│ Host (1 VPS / bare-metal server)                             │
│                                                               │
│   docker compose -f docker-compose.full.yml up -d            │
│   ┌────────────────────────────────────────────────────┐    │
│   │ desi-net (bridge network nội bộ)                   │    │
│   │                                                     │    │
│   │  nginx ──► web (Django) ◄── frontend (built React) │    │
│   │     │         │                                     │    │
│   │     │         ▼                                     │    │
│   │     │    audit-writer ◄── Django middleware POST   │    │
│   │     │    task-worker  ◄── poll jobs                │    │
│   │     │    task-cron    ──► enqueue jobs             │    │
│   │     │         │                                     │    │
│   │     ▼         ▼                                     │    │
│   │  postgres   redis (cache + Pub/Sub)                 │    │
│   └────────────────────────────────────────────────────┘    │
│                                                               │
│   Volumes (named):                                            │
│     pg_data, redis_data, backend_static                       │
│                                                               │
│   Cũ (sau Phase 8): supervisor + jenkins gỡ                  │
└──────────────────────────────────────────────────────────────┘
```

**Stack cuối:**

| Container | Image / Build | Role | Port expose ra host |
|-----------|---------------|------|---------------------|
| `postgres` | postgres:16-alpine | DB | — (chỉ trong `desi-net`) |
| `redis` | redis:7-alpine | Cache + Pub/Sub | — |
| `web` | build `./backend` | Django ASGI + internal API | — |
| `frontend` | build `./frontend` | Nginx serve static React | — |
| `audit-writer` | build `./go-services` (audit-writer) | Audit HTTP server | — |
| `task-worker` | build `./go-services` (task-worker) | Job consumer | — |
| `task-cron` | build `./go-services` (task-cron) | Cron scheduler | — |
| `nginx` | nginx:1.27-alpine | Reverse proxy public | **`${HTTP_PORT}:80`** |

Chỉ `nginx` bind port 80 ra ngoài. Tất cả còn lại expose nội bộ. Đơn giản, ít attack surface.

### 19.3. Postgres + Redis — đã trong container

Vì hai dịch vụ này đã chạy container (không phải host process), có 2 lựa chọn:

| Lựa chọn | Cách làm | Khuyến nghị |
|----------|---------|------------|
| **A. Merge vào `docker-compose.full.yml`** dùng chung volume | Nhập container hiện tại vào compose mới bằng `external: true` volume hoặc rename volume | ✅ Phase 7 — **bảo toàn data tại chỗ**, không cần dump/restore |
| **B. Tạo volume mới + restore dump** | Tạo container mới với named volume `pg_data`, restore từ `pg_dump` | Backup option nếu A không khả thi |

**Quy trình A (khuyến nghị):**
1. Trước Phase 7, chạy lệnh để biết tên container + volume hiện tại:
   ```bash
   docker ps --filter "ancestor=postgres" --format "table {{.Names}}\t{{.Image}}"
   docker ps --filter "ancestor=redis"    --format "table {{.Names}}\t{{.Image}}"
   docker inspect <postgres-container> --format '{{range .Mounts}}{{.Name}} {{.Source}}{{println}}{{end}}'
   docker exec <postgres-container> psql -U desi -d desi -c "SELECT pg_size_pretty(pg_database_size('desi'));"
   ```
2. Stop compose cũ:
   ```bash
   docker compose -f <old-compose>.yml down    # KHÔNG dùng -v (giữ volume)
   ```
3. Trong `docker-compose.full.yml`, declare volume `external: true` trỏ tới volume hiện có:
   ```yaml
   volumes:
     pg_data:
       external: true
       name: <tên_volume_hiện_tại>            # vd. "desi-pg_data" hoặc "<projectname>_pg_data"
     redis_data:
       external: true
       name: <tên_volume_redis>
   ```
4. Khởi động stack mới — Postgres mount đúng data dir cũ → không mất dữ liệu.

> Backup vẫn bắt buộc (Phase 7 bước 2) trước khi đổi compose, phòng trường hợp tên volume gõ sai.

### 19.4. Cutover plan — supervisor → docker compose (downtime 10-15 phút)

**Tiền điều kiện**: Phase 6 đã xong (Celery đã chết), 3 binary Go đang chạy (có thể tạm thời chạy ngoài compose hoặc đã merge vào compose ở staging).

#### Bước 1 — Chuẩn bị (1-2 ngày trước cutover)
1. Trên **staging** (hoặc 1 LXC/server test) build và chạy đầy đủ compose stack:
   ```bash
   cd /home/desi/desi-order
   cp .env.example .env       # chỉnh secrets thật
   docker compose -f docker-compose.full.yml --profile monitoring build
   docker compose -f docker-compose.full.yml --profile monitoring up -d
   ```
2. Smoke test toàn bộ qua domain test (`staging.delax.vn` hoặc IP+port). Kiểm tra:
   - `GET /api/users/me` (cần 401)
   - WebSocket `wss://staging.../ws/crm/?token=...`
   - Một ingest landing test
   - Webhook KiotViet (gọi qua `curl` mock)
3. Đo RAM/CPU container vs baseline supervisor (xem §19.9 resource budget).
4. Confirm: tên container + volume Postgres/Redis hiện hữu (§19.3 bước 1).

#### Bước 2 — Backup production (T-1 giờ, chưa downtime)
```bash
# Postgres — pg_dump qua docker exec (Postgres đang chạy container)
mkdir -p /opt/desi-backup
docker exec <postgres-container> pg_dump -U desi -F c desi \
    > /opt/desi-backup/desi_$(date +%Y%m%d-%H%M).dump
ls -lh /opt/desi-backup/

# Snapshot supervisor + nginx host config
sudo tar -czf /opt/desi-backup/host-snapshot-$(date +%Y%m%d-%H%M).tar.gz \
    /etc/supervisor/conf.d/ \
    /etc/nginx/sites-enabled/ \
    /etc/nginx/sites-available/ \
    /etc/ssl/certs/delax.vn.pem \
    /etc/ssl/private/delax.vn.key
```

#### Bước 3 — Cutover (T-0, downtime bắt đầu, ban đêm)
```bash
cd /home/desi/desi-order
git pull   # lấy bản code mới nhất (docker-compose.full.yml đã update SSL + Go services)

# 1. Maintenance mode trên Cloudflare — bật "Under Attack" hoặc 1 page rule serve 503
#    HOẶC giữ nginx host trả 503 tạm thời (đặt file maintenance.html + return 503)

# 2. Stop supervisor (Django + frontend nếu frontend cũng supervisor)
sudo supervisorctl stop all          # hoặc: stop desi-django desi-frontend
sudo supervisorctl status            # phải STOPPED

# 3. Đảm bảo không có process nào bind 127.0.0.1:8000 nữa
sudo ss -tlnp | grep ':8000'         # phải rỗng

# 4. Stop nginx host
sudo systemctl stop nginx
sudo systemctl disable nginx
sudo ss -tlnp | grep -E ':80|:443'   # phải rỗng

# 5. Build + start docker stack (postgres + redis vẫn dùng volume cũ qua external: true)
docker compose -f docker-compose.full.yml build
docker compose -f docker-compose.full.yml --profile monitoring up -d

# 6. Đợi healthcheck
sleep 30
docker compose -f docker-compose.full.yml ps
# Expect: postgres healthy, redis healthy, web up, audit-writer up, task-worker up, task-cron up, frontend up, nginx up

# 7. Verify Django migrate đã apply (Phase 1-6 đã apply rồi, nhưng kiểm tra lại)
docker exec desi-web python manage.py showmigrations | grep -v '\[X\]' | head

# 8. Smoke test qua nginx container (đã bind 443 ra host)
curl -k https://customer.delax.vn/api/users/me           # qua DNS thật
curl -k https://customer.delax.vn/                       # frontend HTML
# Test WS với token thật + ingest landing đơn test

# 9. Tail logs 5 phút
docker compose -f docker-compose.full.yml logs -f --tail=100 web task-worker task-cron audit-writer

# 10. Tắt maintenance mode Cloudflare → traffic thật quay về
```

**Tổng thời gian**: 10-15 phút (volume `external: true` không restore → tiết kiệm pg_restore time).

#### Bước 4 — Theo dõi (T+1 giờ → T+24 giờ)
- `docker stats` mỗi 10 phút để theo dõi RAM/CPU
- Tail `docker compose logs`
- Grafana (port 3001 bind 127.0.0.1) — kiểm tra dashboard system-overview
- Vẫn giữ supervisor configs + nginx host config trong 1 tuần (rollback ready)

#### Bước 4 — Theo dõi (T+1 giờ)
- Tail logs từng container: `docker compose logs -f --tail=100`
- Theo dõi metrics nếu đã có Grafana (`--profile monitoring` bật)
- Đo RAM/CPU container vs baseline

### 19.5. Rollback (nếu cutover fail)

Trong 30 phút đầu sau cutover, nếu có vấn đề critical:
```bash
# 1. Stop docker stack
docker compose -f docker-compose.full.yml down

# 2. Restart host services
sudo systemctl start postgresql redis-server nginx
sudo supervisorctl start django-web frontend

# 3. Verify
sudo supervisorctl status
```

**Data**: Vì host Postgres không bị ghi mới trong khoảng downtime → data nguyên vẹn. Restore vào docker không ảnh hưởng host data.

> **Nguyên tắc**: trong 1 tuần đầu sau cutover, **không xoá** Postgres data dir host (`/var/lib/postgresql/*`). Backup folder cũng giữ.

### 19.6. Decommission supervisor + jenkins (Phase 8 — sau 1 tuần stable)

1. Xoá supervisor configs:
   ```bash
   sudo rm /etc/supervisor/conf.d/desi-*.conf
   sudo supervisorctl reread && sudo supervisorctl update
   sudo apt remove supervisor    # tuỳ chọn — có thể giữ cho service khác
   ```

2. Disable jenkins jobs:
   - Vào Jenkins UI → disable mọi job liên quan desi-order
   - Backup `~/.jenkins/jobs/desi-*` rồi xoá nếu chắc chắn

3. Xoá nginx host config:
   ```bash
   sudo rm /etc/nginx/sites-enabled/desi-order
   sudo nginx -t   # OK vì nginx host đã disabled
   ```

4. Xoá Postgres + Redis host:
   ```bash
   sudo apt remove postgresql-16 redis-server
   sudo rm -rf /var/lib/postgresql /var/lib/redis    # CẨN THẬN — đã verify volume docker chạy ổn
   ```

> **Backup folder** `backup/desi_*.dump` lưu 30 ngày trước khi xoá.

### 19.7. Quyết định đã chốt (2026-05-14)

| # | Quyết định |
|---|-----------|
| 1 | Postgres + Redis: **container** (đã chạy). Phase 7 dùng `volumes.external: true` để giữ data. |
| 2 | Postgres size: kiểm tra qua `docker exec ... pg_size_pretty(pg_database_size(...))` trước Phase 7. |
| 3 | Server: **Ubuntu 22.04, 8GB RAM, 4 vCPU** → budget §19.9. |
| 4 | SSL: **Cloudflare Origin cert 15 năm** tại `/etc/ssl/certs/delax.vn.pem` + `/etc/ssl/private/delax.vn.key`. Mount vào nginx container. Domain `customer.delax.vn`. |
| 5 | Jenkins: chỉ cho desi-order → **gỡ hẳn** Phase 8. |
| 6 | Downtime: chấp nhận **10-15 phút** (ban đêm). |
| 7 | Backup cron: **chưa có** → thêm cron host daily trước Phase 7 (template §19.8). |
| 8 | Monitoring: **bật** `--profile monitoring` từ Phase 7 (Grafana port 3001 bind 127.0.0.1, login qua Cloudflare tunnel hoặc SSH port-forward). |

### 19.8. Backup strategy sau khi vào docker

Cron host (không cần CI/CD):
```bash
# /etc/cron.daily/desi-backup
#!/bin/bash
set -e
DATE=$(date +%Y%m%d-%H%M)
BACKUP_DIR=/opt/desi-backup
mkdir -p $BACKUP_DIR

# Postgres
docker exec desi-postgres pg_dump -U desi -F c desi > $BACKUP_DIR/desi_$DATE.dump

# Giữ 14 ngày
find $BACKUP_DIR -name 'desi_*.dump' -mtime +14 -delete

# (Optional) push lên S3/R2
# aws s3 cp $BACKUP_DIR/desi_$DATE.dump s3://my-backup/desi/
```

Test restore định kỳ (1 lần/tháng) trên server staging — đảm bảo backup không corrupted.

### 19.9. Cấu hình cụ thể cho `customer.delax.vn` (8GB RAM / 4 vCPU)

#### 19.9.1. Resource budget

| Container | RAM target | CPU target | Ghi chú |
|-----------|-----------|-----------|---------|
| `postgres` | 2GB (shared_buffers 1GB, effective_cache 1.5GB) | 2 vCPU | Workload chính. |
| `redis` | 256MB | 0.5 vCPU | Cache + Pub/Sub. |
| `web` (Django gunicorn workers=3 threads=4) | 800MB-1GB | 1 vCPU | Pattern B đụng vào — đã tính threads. |
| `audit-writer` (Go) | 50MB | 0.3 vCPU | – |
| `task-worker` (Go) | 80MB | 0.5 vCPU | – |
| `task-cron` (Go) | 40MB | 0.2 vCPU | – |
| `frontend` (nginx-static) | 20MB | 0.1 vCPU | – |
| `nginx` (reverse proxy + SSL) | 30MB | 0.3 vCPU | – |
| Monitoring (Prometheus + Grafana + 4 exporter) | 600MB | 0.5 vCPU | `--profile monitoring`. |
| **Tổng** | **~4.0 GB** | **~5.4 vCPU** | – |
| **Free buffer** | ~4 GB | ~0 vCPU (oversubscribe 35%) | Page cache + spikes |

> CPU oversubscribe OK vì peak khác nhau theo từng container. RAM dư ~4GB → page cache Postgres rất tốt.

Resource limits trong compose (thêm vào v0.8):
```yaml
postgres:
  deploy:
    resources:
      limits:    { memory: 2.5G, cpus: '2.5' }
      reservations: { memory: 1.5G }

web:
  deploy:
    resources:
      limits:    { memory: 1.2G, cpus: '1.5' }
      reservations: { memory: 600M }
# ... tương tự các container khác
```

> **Lưu ý**: `deploy.resources.limits` chỉ active khi dùng `docker compose --compatibility` hoặc Swarm mode. Nếu chạy compose v2 thường, dùng `mem_limit: 2.5g` + `cpus: 2.5` (deprecated nhưng vẫn hoạt động). Tham khảo doc Docker Compose v2.

Postgres tuning ([backend/init-postgres-conf.sh](../backup/) mới hoặc env):
```yaml
postgres:
  command: >
    postgres
    -c shared_buffers=1GB
    -c effective_cache_size=1500MB
    -c maintenance_work_mem=128MB
    -c work_mem=16MB
    -c max_connections=100
    -c wal_buffers=16MB
    -c checkpoint_completion_target=0.9
```

#### 19.9.2. Nginx container config (replace nginx host)

Tạo file [nginx/customer.delax.vn.conf](../nginx/customer.delax.vn.conf) mới (thay vì giữ `default.conf` chung):

```nginx
# Đặt trong container: /etc/nginx/conf.d/customer.delax.vn.conf

server {
    listen 80;
    server_name customer.delax.vn;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    http2 on;
    server_name customer.delax.vn;

    # Cert mount từ host (xem volumes trong compose)
    ssl_certificate     /etc/ssl/certs/delax.vn.pem;
    ssl_certificate_key /etc/ssl/private/delax.vn.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;

    # Trust Cloudflare IPs để X-Forwarded-For đúng
    # (Tự thêm danh sách IP CF nếu cần — Cloudflare publish ở https://www.cloudflare.com/ips/)

    # ── Frontend static ──
    location /assets/ {
        proxy_pass http://frontend/assets/;
        expires 7d;
        add_header Cache-Control "public, max-age=604800, immutable";
    }

    # ── Landing ingest với CORS preflight (giữ nguyên logic cũ) ──
    location ~ ^/api/orders/ingest/?$ {
        if ($request_method = OPTIONS) {
            add_header Access-Control-Allow-Origin  "https://www.delax.click" always;
            add_header Access-Control-Allow-Methods "POST, OPTIONS" always;
            add_header Access-Control-Allow-Headers "Content-Type, X-Api-Token" always;
            add_header Access-Control-Max-Age 86400 always;
            add_header Content-Length 0;
            add_header Content-Type text/plain;
            return 204;
        }
        proxy_pass http://web:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # ── REST API → web container ──
    location /api/ {
        proxy_pass http://web:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # ── WebSocket → web container (Django Channels) ──
    location /ws/ {
        proxy_pass http://web:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_buffering off;
    }

    # ── Frontend SPA fallback ──
    location / {
        proxy_pass http://frontend/;
        proxy_set_header Host $host;
    }
}
```

**Khác biệt so với config host hiện tại:**
- `proxy_pass http://127.0.0.1:8000` → `http://web:8000` (DNS internal qua `desi-net`)
- `root /home/desi/desi-order/frontend/dist` + `try_files` → proxy sang `frontend` container (nó đã serve static qua nginx riêng)
- Mount cert vào container qua volume `:ro`.

Compose `nginx` service update:
```yaml
nginx:
  image: public.ecr.aws/docker/library/nginx:1.27-alpine
  container_name: desi-nginx
  depends_on: [web, frontend]
  ports:
    - "80:80"
    - "443:443"
  volumes:
    - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
    - ./nginx/customer.delax.vn.conf:/etc/nginx/conf.d/customer.delax.vn.conf:ro
    - /etc/ssl/certs/delax.vn.pem:/etc/ssl/certs/delax.vn.pem:ro
    - /etc/ssl/private/delax.vn.key:/etc/ssl/private/delax.vn.key:ro
  restart: unless-stopped
  networks: [desi-net]
  healthcheck:
    test: ["CMD", "wget", "-q", "-O", "-", "http://localhost/health"]   # cần thêm location /health
    interval: 30s
    timeout: 5s
    retries: 5
```

Top-level [nginx/nginx.conf](../nginx/) đơn giản chỉ include `conf.d/*.conf`:
```nginx
events {}
http {
  include /etc/nginx/mime.types;
  default_type application/octet-stream;
  gzip on;
  gzip_types text/plain text/css application/javascript application/json image/svg+xml;
  resolver 127.0.0.11 valid=10s ipv6=off;
  include /etc/nginx/conf.d/*.conf;
}
```

#### 19.9.3. Django settings delta — Cloudflare + proxy aware

Vì traffic qua Cloudflare → nginx container → Django container, Django thấy connection từ IP container nội bộ. Cần thêm vào [backend/config/settings_parts/base.py](../backend/config/settings_parts/base.py):

```python
# Trust X-Forwarded-Proto từ nginx container (đã đặt header trong proxy_set_header)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Trust X-Forwarded-Host
USE_X_FORWARDED_HOST = True

# CSRF cho domain Cloudflare-proxied
CSRF_TRUSTED_ORIGINS = [
    "https://customer.delax.vn",
    *(o.strip() for o in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()),
]

# Allowed hosts: nginx container forward `Host: customer.delax.vn`
# Có thể giữ env-driven hiện tại + đảm bảo .env có:
#   DJANGO_ALLOWED_HOSTS=customer.delax.vn,localhost,web
```

`.env` production update:
```
DJANGO_ALLOWED_HOSTS=customer.delax.vn,localhost,127.0.0.1,web
DJANGO_CSRF_TRUSTED_ORIGINS=https://customer.delax.vn
SECURE_PROXY_SSL_HEADER_ENABLED=1
```

#### 19.9.4. Cloudflare config

- **SSL/TLS mode**: **Full (strict)** — Cloudflare TLS đến cert origin 15 năm.
- **WebSocket**: Cloudflare bật `Network → WebSockets: ON`.
- **Timeout**: Cloudflare free plan có timeout 100s mặc định — WS giữ alive bằng heartbeat ping (Django Channels có sẵn).
- **Cache rule**: bypass cache cho `/api/*`, `/ws/*`. Cache `/assets/*` 1 tuần (đã set qua `Cache-Control` header).

#### 19.9.5. Backup cron host (thêm trước Phase 7)

```bash
# /etc/cron.d/desi-backup
# Backup Postgres hàng ngày 02:00 (giờ server)
0 2 * * * desi /usr/local/bin/desi-backup.sh >> /var/log/desi-backup.log 2>&1
```

```bash
# /usr/local/bin/desi-backup.sh
#!/bin/bash
set -e
DATE=$(date +%Y%m%d-%H%M)
DIR=/opt/desi-backup
mkdir -p $DIR

docker exec desi-postgres pg_dump -U desi -F c desi > $DIR/desi_$DATE.dump
gzip $DIR/desi_$DATE.dump

# Retention 14 ngày
find $DIR -name 'desi_*.dump.gz' -mtime +14 -delete

# (Optional) sync lên S3/R2
# aws s3 cp $DIR/desi_$DATE.dump.gz s3://my-backup/desi/
```

```bash
sudo chmod +x /usr/local/bin/desi-backup.sh
sudo chown desi:desi /usr/local/bin/desi-backup.sh
sudo systemctl enable --now cron
```

#### 19.9.6. Smoke test script (Phase 7 bước 8)

```bash
# /usr/local/bin/desi-smoke.sh
#!/bin/bash
set -e
DOMAIN=customer.delax.vn

echo "1. Health check nginx..."
curl -sf https://$DOMAIN/healthz || { echo "FAIL: nginx healthz"; exit 1; }

echo "2. Django web (expect 401 unauth)..."
code=$(curl -s -o /dev/null -w '%{http_code}' https://$DOMAIN/api/users/me)
[ "$code" = "401" ] || { echo "FAIL: expected 401 got $code"; exit 1; }

echo "3. Frontend HTML..."
curl -sf https://$DOMAIN/ | grep -q '<title' || { echo "FAIL: frontend"; exit 1; }

echo "4. WebSocket handshake (cần JWT thật)..."
# Skip nếu không có token; manual test trong browser.

echo "5. Postgres reachable từ web container..."
docker exec desi-web python -c "import psycopg; psycopg.connect('$DATABASE_URL').close()" || exit 1

echo "6. Go services up..."
docker exec desi-task-worker wget -qO- http://localhost:2112/metrics | head -3 || exit 1

echo "OK — all smoke checks passed."
```

Chạy sau cutover, exit 0 = green light gỡ maintenance mode Cloudflare.
