# Runbook — Thêm worker `worker-slow` (queue `slow`)

**Ngày:** 2026-07-26
**Nguồn:** audit 2026-07-26 mục T5
**Ảnh hưởng:** thêm 1 container Celery worker trên prod. KHÔNG có migration, KHÔNG downtime.

---

## 1. Vì sao

Trước thay đổi này, 19 task không có route trong `CELERY_TASK_ROUTES` nên rơi hết vào
queue `default`, do container `desi-worker` tiêu thụ với `--concurrency=4`. Trong nhóm đó
có hai loại task lệch nhau rất xa về thời lượng:

| Nhóm | Task | Thời lượng |
|---|---|---|
| Dài, I/O-bound (Gemini / Zenify) | `callcenter.tasks.sync_zenify_call_histories`, `transcribe_pending_call_records`, `extract_pending_call_insights` | `soft_time_limit` 300–480s |
| Nặng CPU/DB (precompute báo cáo) | `customers.tasks.rebuild_segment_snapshot` (khi viết runbook này còn có `warm_report_cache`, đã gỡ cùng report khách hàng 2026-08-21 — xem `docs/dwh-customer-analytics.md`) | `time_limit` tới 900s (nhóm đã gây sự cố `docs/report-precompute-timeout-2026-07-22.md`) |
| Ngắn nhưng cần ĐÚNG GIỜ | `orders.tasks.sweep_floating_pool`, `orders.tasks.sweep_lead_assignment`, `contact_flow.tasks.sweep_operation_entry`, các job `purge_*` | vài giây |

3 job LLM chạy đồng thời + 1 task customers precompute (`rebuild_segment_snapshot`, khi
đó còn cả `warm_report_cache`) là chiếm trọn 4 slot. Sweep điều phối
lead — thứ sale cảm nhận ngay khi đơn về mà không được chia — phải xếp hàng sau chúng.

**Cách xử lý:** route 5 task nhóm dài/nặng sang queue `slow` và cho một worker riêng
(`desi-worker-slow`) tiêu thụ. Hai nhóm không còn tranh slot của nhau.

---

## 2. Thay đổi trong code

| File | Thay đổi |
|---|---|
| `backend/config/settings_parts/celery.py` | thêm `CELERY_SLOW_QUEUE` + 5 entry route |
| `docker-compose.prod.yml` | thêm service `worker-slow` (`desi-worker-slow`) |
| `docker-compose.staging.yml` | thêm service `worker-slow` (`desi-staging-worker-slow`, concurrency=1) |
| `docker-compose.local.yml` | worker duy nhất thêm `slow` vào `-Q` |
| `backend/apps/common/tests/test_celery_routes.py` | test chốt định tuyến + chống hồi quy |
| `scripts/deploy-prod.sh` | thêm `worker-slow` vào danh sách `up -d --build` |
| `scripts/staging-refresh.sh` | thêm `worker-slow` vào các bước stop/start |

> Hai script trên liệt kê service THỦ CÔNG. Quên thêm `worker-slow` ở đó nghĩa là sau
> mỗi lần deploy container này vẫn chạy IMAGE CŨ vô thời hạn (đúng cái bẫy đã ghi trong
> comment về `worker-kiotviet`), và `staging-refresh.sh` sẽ drop DB trong khi worker vẫn
> còn kết nối.

Biến env mới (tùy chọn, đều có default trong compose):

| Biến | Default | Ý nghĩa |
|---|---|---|
| `CELERY_SLOW_QUEUE` | `slow` | tên queue cho nhóm task dài/nặng |
| `SLOW_WORKER_CONCURRENCY` | `2` | số slot của `desi-worker-slow` |
| `SLOW_WORKER_MAX_MEMORY_PER_CHILD` | `358000` | ngưỡng RSS (KB) recycle prefork child |

> ⚠️ `SLOW_WORKER_MAX_MEMORY_PER_CHILD` phụ thuộc `SLOW_WORKER_CONCURRENCY`. Công thức
> (giống comment ở anchor `x-backend-env`):
> `(mem_limit_MB − parent ~180MB) / concurrency × 0,85`
> → `(1024 − 180) / 2 × 0,85 ≈ 358MB ≈ 358000KB`.
> Đổi concurrency mà quên đổi ngưỡng này thì cgroup OOM killer sẽ SIGKILL child TRƯỚC
> khi Celery kịp recycle → connection Postgres mồ côi chiếm slot (sự cố 2026-07-21).

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
docker compose -f docker-compose.prod.yml up -d --build worker-slow

# 3) Beat phải nạp lại route để đẩy 5 task vào queue mới.
#    CELERY_TASK_ROUTES áp ở phía PRODUCER, nên beat cần restart.
docker compose -f docker-compose.prod.yml restart beat
```

Từ lần deploy sau, `scripts/deploy-prod.sh` đã có `worker-slow` trong danh sách
`up -d --build` nên không cần thao tác tay nữa — chạy script như bình thường.

**Không cần restart `worker`** (worker `default`): nó vẫn nghe `default,orders,lark_noti,audit`
như cũ, chỉ là 5 task kia không còn được gửi vào `default` nữa.

### Thứ tự an toàn

Dựng `worker-slow` **trước**, restart `beat` **sau**. Làm ngược lại thì trong vài giây
task sẽ được đẩy vào queue `slow` chưa có ai tiêu thụ — chúng nằm chờ trong Redis
(không mất), nhưng chờ vô ích.

---

## 4. Kiểm tra sau deploy

```bash
# a) Worker đã lên và healthy
docker compose -f docker-compose.prod.yml ps worker-slow

# b) Worker ĐÃ NHẬN ĐÚNG QUEUE — bước quan trọng nhất
docker exec desi-worker-slow celery -A config inspect active_queues
#    Kỳ vọng: mục "celery@<hostname>" liệt kê queue tên "slow" (và CHỈ "slow").

# c) Ping qua broker
docker exec desi-worker-slow celery -A config inspect ping --timeout=10
#    Kỳ vọng: "pong".

# d) Queue slow có được tiêu thụ không (không tồn đọng)
docker exec desi-redis-managed redis-cli -a "$REDIS_PASSWORD" -n 3 llen slow
#    Kỳ vọng: về 0 sau mỗi chu kỳ. Số chỉ tăng đều = worker không nghe đúng queue.

# e) Queue default đã nhẹ đi
docker exec desi-redis-managed redis-cli -a "$REDIS_PASSWORD" -n 3 llen default

# f) Log worker mới nhận task
docker logs --tail 50 desi-worker-slow
#    Kỳ vọng thấy: transcribe_pending_call_records / rebuild_segment_snapshot ... succeeded
#    (task `warm_report_cache` đã bị gỡ 2026-08-21 cùng report khách hàng — nếu thấy
#    NotRegistered cho task này thì đó là dấu hiệu beat chưa được restart, không phải lỗi mới)
```

**Chỉ số nghiệm thu (24h sau):** thời gian chờ của `sweep_lead_assignment` (beat 5') và
`sweep_floating_pool` (beat 15') không còn spike trùng với chu kỳ 2h của
`rebuild_segment_snapshot` (task `warm_report_cache` đã gỡ 2026-08-21, không còn chạy).

---

## 5. Rollback

Thay đổi này thuần cấu hình, không có state mới → rollback sạch, không mất task.

```bash
# 1) Trả 5 task về queue default: revert 5 dòng route trong
#    backend/config/settings_parts/celery.py (giữ hay bỏ CELERY_SLOW_QUEUE đều được),
#    rồi restart beat để producer nạp lại route:
docker compose -f docker-compose.prod.yml restart beat

# 2) Đợi queue slow rỗng rồi mới dừng worker (tránh cắt ngang task đang chạy):
docker exec desi-redis-managed redis-cli -a "$REDIS_PASSWORD" -n 3 llen slow   # chờ về 0

# 3) Dừng và xoá worker-slow
docker compose -f docker-compose.prod.yml stop worker-slow
docker compose -f docker-compose.prod.yml rm -f worker-slow
```

Nếu phải rollback GẤP trong khi queue `slow` còn task: cứ dừng `worker-slow`, task nằm
lại trong Redis DB 3. Sau khi revert route + restart beat, các tick beat kế tiếp sẽ đẩy
lại chúng vào `default` — 5 task này đều idempotent (có lock/single-flight riêng), nên
task tồn đọng ở `slow` có thể bỏ luôn: `docker exec desi-redis-managed redis-cli -a "$REDIS_PASSWORD" -n 3 del slow`.

> Lưu ý: test `apps/common/tests/test_celery_routes.py` sẽ ĐỎ ngay khi bỏ route mà
> không cập nhật `SLOW_TASKS`/`KNOWN_DEFAULT_QUEUE_TASKS`. Đó là chủ đích — rollback
> vĩnh viễn thì phải sửa test kèm lý do; rollback tạm thì chấp nhận CI đỏ có chủ ý.
