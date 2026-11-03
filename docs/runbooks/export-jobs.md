# Runbook — Vận hành xuất file chạy nền (`apps.exports`)

**Nguồn:** `docs/superpowers/specs/2026-08-06-async-export-jobs-design.md` (Sprint 1–3,
Task 1–22). **Ảnh hưởng:** 7 luồng xuất Excel/CSV (đơn hàng, tồn kho, lịch sử xuất/nhập
kho, phiếu bảo hành, mã bảo hành, nhật ký hệ thống) chạy qua bảng `export_jobs`, worker
riêng `desi-worker-export`, lưu trữ MinIO bucket `desi-exports`.

---

## 1. Xem job đang chạy

Bảng `export_jobs` (Postgres, container `desi-postgres`, db/user mặc định `desi`):

```bash
docker exec desi-postgres psql -U desi -d desi -c \
  "SELECT id, user_id, kind, status, progress_rows, total_rows, created_at, started_at
   FROM export_jobs
   WHERE status IN ('pending','running')
   ORDER BY created_at;"
```

Toàn bộ trạng thái hợp lệ (`apps/exports/models.py` — `ExportJobStatus`): `pending`,
`running`, `success`, `failed`, `cancelled`, `expired`. Muốn xem job của một user cụ
thể trong N giờ gần nhất:

```bash
docker exec desi-postgres psql -U desi -d desi -c \
  "SELECT id, kind, status, file_name, file_size, error_message, created_at, finished_at
   FROM export_jobs
   WHERE user_id = <user_id> AND created_at > now() - interval '6 hours'
   ORDER BY created_at DESC;"
```

Panel FE gọi `GET /api/exports?active=1` — cùng dữ liệu, lọc theo `status IN
(pending, running)` và `user = request.user` (`ACTIVE_STATUSES` trong models.py).

---

## 2. Job kẹt RUNNING hoặc PENDING

**Dấu hiệu:** job ở trạng thái `running` nhưng `started_at` đã quá lâu (hoặc `pending`
mà `created_at` đã quá lâu — worker-export không bao giờ nhận task, ví dụ do quên
thêm service vào `scripts/deploy-prod.sh`), panel FE quay spinner vô tận (poll 3s
không thấy tiến độ đổi). Nguyên nhân thường gặp: worker `desi-worker-export` bị
OOM-kill/restart giữa chừng job (celery không kịp cập nhật `status=failed` trước
khi process chết), hoặc worker-export không tồn tại/không tiêu thụ đúng queue.

**Ngưỡng tự phục hồi:** task `purge_expired_exports`
(`apps/exports/infrastructure/tasks.py`) coi một job `running` là kẹt khi
`started_at` cũ hơn `EXPORT_SOFT_TIME_LIMIT × 2` giây — mặc định
`600 × 2 = 1200s = 20 phút`; job `pending` áp cùng ngưỡng nhưng so với `created_at`
(job pending không có `started_at`). Cả hai bị chuyển sang `failed` với
`error_message` giải thích tương ứng. **Cùng task** này còn dọn object MinIO của job
`success` đã hết hạn TTL (mục 4).

Task này CỐ Ý route vào queue `default` (`CELERY_TASK_DEFAULT_QUEUE`), KHÔNG phải
`exports` — nó là cơ chế tự cứu cho worker-export chết, nên không thể phụ thuộc vào
chính worker đó còn sống. `desi-worker` (worker `default`, luôn có mặt) tiêu thụ.
Task cũng override `soft_time_limit=540/time_limit=600` (mặc định toàn cục 25s/30s
không đủ cho vòng lặp xoá object MinIO) và bắt riêng `SoftTimeLimitExceeded` để
thoát vòng lặp dọn file MÀ VẪN chạy tới khối cứu job kẹt bên dưới.

Task chạy theo lịch beat **mỗi giờ, phút :15** (`crontab(minute=15)`,
`backend/config/settings_parts/celery.py`). Nếu cần dọn ngay thay vì đợi tới :15 kế
tiếp (gọi từ container nào cũng được — lệnh dưới chỉ enqueue lên broker theo route
đã cấu hình, `desi-worker` sẽ là consumer thật):

```bash
docker exec desi-worker celery -A config call \
  apps.exports.infrastructure.tasks.purge_expired_exports
```

Hoặc qua Django shell (chạy trực tiếp trong process, không qua broker — container
nào cũng được, để xem giá trị trả về / log ngay trong shell):

```bash
docker exec -it desi-worker python manage.py shell -c \
  "from apps.exports.infrastructure.tasks import purge_expired_exports; purge_expired_exports()"
```

> Task này **giữ lại bản ghi** — chỉ đổi `status`, không xoá row — vì `export_jobs` là
> vết audit "ai đã xuất cái gì" (đọc thêm mục audit ở `apps/exports/transport/views.py:
> record_export_audit`). Không cần khôi phục thủ công job đã bị đánh `failed`; người
> dùng bấm **Thử lại** trên panel để tạo job mới.

---

## 3. Queue `exports` tồn đọng

**Xem độ dài queue** (Celery + Redis broker db 3, theo `REDIS_CELERY_BROKER_DB` —
`backend/config/settings_parts/celery.py`). Container Redis prod là
`desi-redis-managed` (profile `managed-redis`, xem `docker-compose.prod.yml`):

```bash
docker exec desi-redis-managed redis-cli -a "$REDIS_PASSWORD" -n 3 llen exports
```

Ở dev/local (không dùng `managed-redis`, container tên `desi-redis`), cú pháp giống
hệt, chỉ đổi tên container:

```bash
docker exec desi-redis redis-cli -a "$REDIS_PASSWORD" -n 3 llen exports
```

Prometheus có cùng số liệu qua `celery-exporter` — metric thật (không phải suy đoán
tên) là `celery_queue_length{queue_name="exports"}`, dùng bởi alert `ExportQueueBacklog`
(mục 7):

```bash
curl -s localhost:9092/api/v1/query \
  --data-urlencode 'query=celery_queue_length{queue_name="exports"}'
```

**Tăng concurrency** — sửa `EXPORT_WORKER_CONCURRENCY` trong `.env` prod rồi recreate
riêng `worker-export`:

```bash
docker compose -f docker-compose.prod.yml up -d --no-deps worker-export
```

> ⚠️ **BẮT BUỘC đổi kèm `EXPORT_WORKER_MAX_MEMORY_PER_CHILD` theo công thức**
> (giống `worker-slow`, xem `docs/runbooks/2026-07-26-add-slow-worker.md`):
>
> ```
> EXPORT_WORKER_MAX_MEMORY_PER_CHILD (KB) = (mem_limit_MB − parent ~180MB) / concurrency × 0.85
> ```
>
> `worker-export` có `mem_limit: 768m` (`docker-compose.prod.yml`). Với concurrency
> mặc định 2: `(768 − 180) / 2 × 0.85 ≈ 250000` KB — đúng giá trị default hiện tại.
> Đổi concurrency lên 3 mà **quên** đổi ngưỡng này theo công thức (còn để 250000) thì
> mỗi child ôm nhiều RAM hơn dự kiến; cgroup OOM killer SIGKILL child **trước khi**
> Celery kịp tự recycle theo ngưỡng RSS → connection Postgres của child đó thành mồ
> côi, chiếm slot mà không ai đóng lại (cùng cơ chế sự cố 2026-07-21 đã ghi trong
> runbook `worker-slow`).

---

## 4. MinIO đầy

**Bucket:** `desi-exports` (`MINIO_BUCKET_EXPORTS`, tự tạo ở lần upload đầu tiên —
`apps/exports/infrastructure/export_storage.py:upload_file`, không có init job riêng).
Container MinIO: `desi-minio`.

Kiểm dung lượng (dùng `mc` có sẵn trong image MinIO, alias tới chính container):

```bash
docker exec desi-minio sh -c \
  'mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" && \
   mc du local/desi-exports'
```

Liệt kê object cũ nhất (đối soát nhanh với TTL `EXPORT_FILE_TTL_HOURS`, mặc định 24h):

```bash
docker exec desi-minio sh -c 'mc ls --recursive local/desi-exports | sort | head -20'
```

**Xác nhận job dọn đang chạy:** cùng task `purge_expired_exports` ở mục 2 (beat mỗi
giờ, phút :15) xoá object của job `success` đã qua `expires_at`. Xác nhận qua log beat
hoặc so khớp số dòng `export_jobs` đang `expired` với dung lượng bucket đang giảm:

```bash
docker exec desi-postgres psql -U desi -d desi -c \
  "SELECT status, count(*) FROM export_jobs GROUP BY status;"
docker logs desi-beat --since 1h | grep -i purge_expired_exports
```

Nếu bucket vẫn đầy dù `purge_expired_exports` chạy đều: khả năng object MinIO không
khớp với `file_key` nào trong DB (job bị xoá thủ công, hoặc race huỷ-ngay-sau-khi-upload
mà `run_export` không dọn kịp — xem comment `_StorageFailed`/"Bị huỷ ngay sau khi upload
xong" trong `apps/exports/infrastructure/tasks.py`). Đối chiếu tay bằng cách so danh
sách `mc ls --recursive` với tập `file_key` còn `status='success'` trong DB.

---

## 5. Người dùng báo "không tải được file"

`GET /api/exports/{id}/download` (`apps/exports/transport/views.py:download_export`)
trả một trong bốn mã sau:

| Mã | Điều kiện trong code | Ý nghĩa | Việc cần làm |
|---|---|---|---|
| **404** | `ExportJob.objects.filter(id=job_id, user=request.user).first()` rỗng | Job không tồn tại, **hoặc** người tải không phải chủ job (cố tình trả 404 thay vì 403 — không xác nhận job có tồn tại với người không sở hữu, xem comment trong view) | Không phải lỗi hệ thống. Nếu người dùng chắc chắn job là của họ, kiểm `user_id` của job đó trong bảng `export_jobs` (mục 1) — id sai hoặc job đã bị dọn (mục 2/4) |
| **410** | `job.is_downloadable()` là `False` — tức `status != success`, hoặc `file_key` rỗng, hoặc `expires_at <= now()` | File chưa xong (còn `pending`/`running`), hoặc đã hết hạn TTL và bị `purge_expired_exports` dọn (mục 2/4) | Job chưa xong: đợi. Job đã hết hạn: hướng dẫn bấm **Xuất lại** trên panel (không có đường khôi phục file cũ) |
| **502** | `export_storage.open_stream(job.file_key)` ném exception | MinIO không mở được object dù DB nói `status=success` — object bị xoá thủ công/mất, hoặc MinIO đang lỗi/hết dung lượng | Xem log `desi-web` (`logger.exception("Không mở được file export %s", job.id)`) để lấy traceback; kiểm MinIO còn sống (`docker exec desi-minio mc ready local` hoặc healthcheck compose) và bucket còn object đó (mục 4) |
| **200** | Mở stream thành công | File trả về bình thường, `Content-Disposition: attachment` | — |

Nếu người dùng báo lỗi khác 404/410/502 (ví dụ 401/403), đó là tầng auth/RBAC chung
của hệ thống, không riêng export — kiểm session/permission `{section}:export` như mọi
endpoint khác.

---

## 6. Rollback

**Không thể chỉ revert frontend.** Trước sprint 2 (Task 18, commit `25dfc1b5` —
*"refactor: remove synchronous export endpoints"*), 7 endpoint xuất file đồng bộ cũ
(bulk-export-excel đơn hàng, stock/dispatch-history/return-history export tồn kho,
warranty claims export, warranty_codes batch export, audit log export) vẫn còn tồn
tại song song với `/api/exports`. Từ commit đó, **cả 7 endpoint đồng bộ đã bị xoá
khỏi backend** (route, view, và toàn bộ test tương ứng), cùng với helper FE
`lib/api/xlsx.ts`. Revert riêng FE về nút "tải trực tiếp" cũ sẽ gọi vào endpoint không
còn tồn tại — 404 cho toàn bộ 7 luồng export.

**Đường lùi duy nhất:** revert cả hai phía (backend + frontend) về trước commit ranh
giới `25dfc1b5`. Không có cách rollback một nửa.

```bash
git log --oneline 25dfc1b5^..25dfc1b5   # xác nhận đúng commit ranh giới trước khi revert
git revert 25dfc1b5           # backend: khôi phục 7 endpoint đồng bộ + view/route/test
# revert kèm các commit FE tương ứng đã chuyển nút xuất sang startExport() (Task 14/17)
```

Cân nhắc trước khi revert: mọi `export_jobs` đã tạo (kể cả đang `pending`/`running`)
sẽ không còn view nào phục vụ tải nếu worker-export cũng bị tắt — dừng service
`worker-export` sau khi revert để tránh job mới bị tạo mà không có đường lùi tương xứng
phía UI.

---

## 7. Alert

Định nghĩa ở `monitoring/alerts.yml`, nhóm `celery`:

| Alert | Biểu thức | Ý nghĩa | Xử lý |
|---|---|---|---|
| `ExportQueueBacklog` | `celery_queue_length{queue_name="exports"} > 20` trong 10 phút | Queue `exports` tồn đọng — job tạo nhanh hơn worker tiêu thụ | Kiểm `desi-worker-export` còn sống (`docker exec desi-worker-export celery -A config inspect ping`); nếu queue tăng đều mà worker khỏe, cân nhắc tăng `EXPORT_WORKER_CONCURRENCY` (mục 3, **nhớ đổi kèm** `EXPORT_WORKER_MAX_MEMORY_PER_CHILD`) |
| `ExportWorkerMissing` | `absent(celery_queue_length{queue_name="exports"})` trong 10 phút | Worker-export chết hẳn hoặc chưa từng lên: series `celery_queue_length{queue_name="exports"}` do celery-exporter phát ra chỉ tồn tại khi có ít nhất một worker đang nghe queue đó — worker chết làm series BIẾN MẤT (không phải tăng), nên `ExportQueueBacklog` mù trong đúng kịch bản này | Kiểm `docker compose -f docker-compose.prod.yml ps worker-export`; nếu container không chạy, `up -d worker-export`; nếu chạy mà vẫn `absent`, kiểm `celery -A config inspect active_queues` xem có đúng nghe queue `exports` không |

`ExportQueueBacklog` severity `warning`; `ExportWorkerMissing` severity `critical`
(worker chết hẳn nghiêm trọng hơn queue tồn đọng đơn thuần). Cả hai đều không tự
động page — theo dõi qua kênh Lark adapter chung của hệ Prometheus/Alertmanager
hiện có.

**KHÔNG có alert cho job export thất bại** (đã từng có `ExportJobsFailing` dựa trên
`export_jobs_total{status="failed"}`, nhưng bị xoá ở đợt sửa trước-merge cuối cùng):
metric đó chỉ `.inc()` trong tiến trình `desi-worker-export`
(`apps/exports/infrastructure/tasks.py:run_export`), còn `PROMETHEUS_MULTIPROC_DIR`
là thư mục **cục bộ từng container**, không phải volume chung — Prometheus chỉ scrape
đúng một target ứng dụng (`web:8000/api/orders/metrics`, xem
`monitoring/prometheus.yml`) nên số đếm trong worker không bao giờ tới được
Prometheus. Đường lùi "dựa celery-exporter theo trạng thái task" cũng không dùng
được: `run_export` nuốt hết exception nên Celery luôn thấy task `succeeded`. Mount
volume chung hay đổi kiến trúc export metric không nằm trong phạm vi đợt sửa này —
ghi lại làm nợ ở mục 10 của
`docs/superpowers/specs/2026-08-06-async-export-jobs-design.md`.

**Cách duy nhất hiện tại để biết job export đang thất bại nhiều:** query trực tiếp
bảng `export_jobs` (mục 1 có cùng dạng câu lệnh):

```bash
docker exec desi-postgres psql -U desi -d desi -c \
  "SELECT kind, count(*), max(created_at) AS gan_nhat
   FROM export_jobs
   WHERE status = 'failed' AND created_at > now() - interval '15 minutes'
   GROUP BY kind
   ORDER BY count(*) DESC;"
```
