---
title: Khôi phục Celery (gỡ bỏ Go task services), parity đầy đủ + cutover 1 lần
version: 1.0
date: 2026-05-29
status: draft
owner: CTO
---

# Quay lại Celery 100% — gỡ 3 Go service (task-worker / task-cron / audit-writer)

> **Quyết định nghiệp vụ (2026-05-29):** Dừng dùng Golang cho tầng async/cron. Quay lại Celery hoàn toàn.
> - **Phạm vi:** *Parity đầy đủ* — giữ nguyên hành vi production hiện tại, port **toàn bộ** logic đã viết trong Go về Python.
> - **Cách triển khai:** *Cutover 1 lần* — khôi phục Celery đầy đủ, test kỹ, rồi tắt hẳn 3 Go service + `pubsub-bridge` (xem §7) và xoá `go-services/`.
> - Mốc parity = **HEAD hiện tại** (gồm cả fix `sync delivery price` ở commit `b369199`).

---

## 1. Bối cảnh — vì sao không phải "git revert"

Celery đã bị gỡ sạch ở commit `fdfce35 remove celery` (18/05). Từ đó tới nay logic nghiệp vụ **phân kỳ mạnh về Go**: 16 commit sửa logic vào Go handlers vs 3 commit vào Python. Một số tính năng **sinh ra thuần trong Go, chưa từng có bản Python**. Vì vậy không thể revert sạch — phải:

- **Phần A (dễ):** khôi phục hạ tầng Celery. Có sẵn nguyên vẹn trong git tại `97f71dd` (cha của `fdfce35`) — ở đó `dispatch()` còn hỗ trợ cả 2 backend qua cờ `TASK_BACKEND`.
- **Phần B (khó):** port ngược logic Go về Python (xem ma trận §4).

**Tin tốt đã xác minh:** mọi bảng DB (kể cả `ads_daily_*_lark_records`, `kiotviet_revenue_lark_records`, `kiotviet_daily_revenues`) **do Django migration sở hữu** → model Python đã có. Việc port chỉ là **hành vi**, không đụng schema.

---

## 2. Hiện trạng runtime

| Thành phần | Hiện tại (Go) | Sau khi khôi phục (Celery) |
|---|---|---|
| Hàng đợi task | Bảng `jobs` (Postgres) + `task-worker` poll | Redis broker DB3/4 + `celery worker` |
| Lịch (cron/beat) | `task-cron` (robfig/cron) | `celery beat` + `django_celery_beat` (DB scheduler) |
| Ghi audit log | `audit-writer` (HTTP batch) | `create_audit_log_task.delay()` |
| `dispatch()` | Chỉ INSERT bảng `jobs` | `.apply_async()` Celery (giữ wrapper) |
| Bridge WS Go→Django | `pubsub-bridge` | Không cần (task chạy in-process Django, publish trực tiếp) |

3 Go service trên prod: `audit-writer`, `task-worker`, `task-cron` (+ `pubsub-bridge`). Đều bị xoá ở Sprint 4.

---

## 3. Bản đồ task (task_type Go → Celery task)

5 queue giữ nguyên như cũ: `default`, `orders`, `kiotviet`, `zalo_bridge`, `lark_noti`, `audit`.

| task_type (Go) | Celery task (Python) | Queue | Pattern* | Bản Python đã có? |
|---|---|---|---|---|
| `audit.create` | `apps.audit.tasks.create_audit_log_task` | `audit` | A | ✅ (func sync, cần bọc `@shared_task`) |
| `lark.notify_order_created` | `…lark_notification_tasks.notify_lark_order_created` | `lark_noti` | A | ✅ |
| `zalo.apply_initial_tags` | `apps.zalo_bridge.tasks.zalo_apply_initial_tags` | `zalo_bridge` | A | ✅ |
| `zalo.send_order_created` | `…zalo_send_order_created` | `zalo_bridge` | A | ✅ |
| `zalo.send_shipping_started` | `…zalo_send_shipping_started` | `zalo_bridge` | A | ✅ |
| `zalo.send_delivery_completed` | `…zalo_send_delivery_completed` | `zalo_bridge` | A | ✅ |
| `kv.process_job` | `…ingest_job_tasks.process_kiotviet_job` | `kiotviet` | A→djclient | ✅ (logic nặng vốn ở Django service layer) |
| `kv.webhook_push_old` | `…webhook_sync_tasks.process_kiotviet_webhook_push_old` | `kiotviet` | A→djclient | ✅ |
| `kv.webhook_sync_old_status` | `…webhook_sync_tasks.process_kiotviet_webhook_sync_old_status` | `kiotviet` | A | ✅ — **cần kiểm tra parity** với fix `b369199` |
| `fb.daily_spend` | `apps.facebook_ads.tasks.sync_all_daily_spend` | `orders` | A | ✅ — kiểm tra parity (date_preset last_7d/365d, upsert) |
| `kv.revenue_sync` | `apps.kiotviet.tasks.sync_daily_revenue_all_channels` | `kiotviet` | A | ⚠️ **thiếu Lark push + backfill** |
| `kv.hourly_audit` | `…cron_hourly_audit_tasks.process_cron_hourly_audit_channels` | `kiotviet` | A→djclient | ✅ |
| `kv.auto_sync_full` | `…auto_sync_tasks.process_auto_sync_invoice_shipping_fee_and_status` | `kiotviet` | A | ✅ (bản inline cũ) — **cần merge fix per-order** |
| `kv.auto_sync_one` | *(không cần — Python xử lý inline)* | — | A | ❌ Go-only (fan-out) |
| `kv.auto_update_code` | `…auto_sync_tasks.process_auto_update_invoice_code_and_push_old` | `kiotviet` | A | ✅ (bản inline cũ) — **cần merge fix per-order** |
| `kv.auto_update_code_one` | *(không cần — Python xử lý inline)* | — | A | ❌ Go-only (fan-out) |
| `ads.report_lark_sync` | **task mới** `apps.facebook_ads.tasks.sync_ads_report_lark` (đề xuất) | `orders` | A | ❌ **Go-only — viết mới hoàn toàn** |
| `orders.sale_reminders` | `…sale_reminder_tasks.process_sale_reminders` | `lark_noti` | B | ✅ |
| `orders.no_answer_reminders` | `…sale_reminder_tasks.process_no_answer_reminders` | `lark_noti` | B | ✅ |
| `orders.restore_orders` | `…restore_tasks.process_restore_orders` | `orders` | B | ✅ |
| `orders.landing_ingest` | `…ingest_job_tasks.process_landing_ingest` | `orders` | B | ✅ |

\* **Pattern A** = Go xử lý native; **B** = Go proxy HTTP về Django. Sau khi về Celery cả 2 đều là task Python chạy in-process worker → Pattern phân biệt không còn ý nghĩa runtime, nhưng cột "A→djclient" nhắc rằng logic nặng vốn đã nằm ở Django service layer (rủi ro thấp).

**Lịch beat (khôi phục từ task-cron.yaml hiện tại):**

| Lịch | Celery task | Queue | kwargs |
|---|---|---|---|
| mỗi 60s | `fb.daily_spend` | `orders` | — |
| mỗi 30m | `ads.report_lark_sync` (mới) | `orders` | — |
| mỗi 1h | `kv.revenue_sync` | `kiotviet` | `time_range=thismonth` |
| mỗi 1h | `kv.hourly_audit` | `kiotviet` | — |
| mỗi 60s | `orders.sale_reminders` | `lark_noti` | — |
| 08:00 / 11:00 / 15:00 / 16:30 | `orders.no_answer_reminders` | `lark_noti` | `slot=…` |
| *(disabled)* | `kv.auto_sync_full`, `kv.auto_update_code` | `kiotviet` | giữ disabled |

Housekeeping Go (`requeue_stuck_jobs`, `cleanup_old_jobs`) → **bỏ** (Celery/Redis tự quản hàng đợi, không cần bảng `jobs`).

---

## 4. Ma trận công việc PORT (phần khó — Phần B)

| # | Hạng mục | Độ khó | Ghi chú |
|---|---|---|---|
| P1 | **Python Lark Bitable/OpenAPI client** | Cao | Port `go-services/internal/shared/gateway/lark/openapi.go`: auth (tenant_access_token), `batch_create`, `batch_update`. Dùng chung cho P2 + P3. |
| P2 | **`ads.report_lark_sync` → task Python mới** | Cao (~250 dòng Go) | SQL: channel-grain + product-grain 7 ngày; phân bổ cost FB (spend ÷ số kênh × 1.1); semantics create/update/**zero-out**; idempotency qua `record_key`. Models `AdsDailyChannelLarkRecord`/`AdsDailyProductLarkRecord` đã có. |
| P3 | **Lark push + backfill cho `kv.revenue_sync`** | Trung | Bản Python chỉ UPSERT DB, chưa đẩy Lark. Thêm: gom theo ngày, SUM đa kênh, batch create/update Lark, lưu `KiotVietRevenueLarkRecord`, self-heal ngày thiếu record. |
| P4 | **Merge fix per-order vào auto_sync Python** | Trung | Các fix gần đây (`b369199` delivery price+code, `9445334` invoice_status mapping 1/2/3/5, `e59650d`) nằm ở Go handler per-order. Bản Python inline cần được cập nhật cho khớp. So sánh `kiotviet_native.go` ↔ `auto_sync_tasks.py`. |
| P5 | **Parity `kv.webhook_sync_old_status`** | Thấp-TB | Xác nhận bản Python có logic preserve price + shipping_code + nhánh delete khi RETURNED/CANCELLED như Go (`b369199`). |
| P6 | **Parity `fb.daily_spend`** | Thấp | Xác nhận chọn `last_7d` vs `last_365d` theo có/không dữ liệu, UPSERT đúng cột. |

> **Quyết định fan-out:** Go tách `auto_sync_full → auto_sync_one` (per-order job, priority −10) để tận dụng job queue + rate limit. Celery bản cũ xử lý **inline trong 1 task** (vòng lặp + rate limit nội bộ). Giữ mô hình inline cũ cho đơn giản; nếu cần parallel có thể fan-out bằng `task.apply_async()` per-order sau. Ghi nhận: bỏ fan-out = chấp nhận 1 task chạy lâu hơn, nhưng đây đúng là hành vi Celery nguyên bản.

---

## 5. Các điểm phải đụng (Phần A — khôi phục hạ tầng, lấy từ `97f71dd`)

1. `backend/requirements.txt`: thêm lại `celery==5.5.3`, `django-celery-beat==2.9.0`, `kombu==5.5.4`, `billiard==4.2.3`, `vine==5.1.0`, `amqp==5.3.1`.
2. `backend/config/celery.py` + `backend/config/__init__.py` (Celery app + autodiscover).
3. `backend/config/settings_parts/base.py`: khối `CELERY_*` (broker/result Redis DB3/4, routes, queues, timezone Asia/Bangkok, time limits, beat scheduler DB).
4. `apps/dispatch/api.py`: khôi phục nhánh fallback Celery + `apps/dispatch/registry.py` (`CELERY_REGISTRY`). Mặc định `TASK_BACKEND=celery`, `AUDIT_BACKEND=celery`.
5. `@shared_task`: bọc lại ~18 task function (đa số chỉ cần thêm decorator + `name=`/`queue` đúng route). `INTERNAL_TASK_REGISTRY` giữ nguyên hoặc trỏ thẳng task object.
6. `apps/cron_jobs/management/commands/seed_cron_jobs.py`: khôi phục seed beat schedule + thêm entry `ads.report_lark_sync` (mỗi 30m). Lưu ý có migration `0001_drop_celery_beat_tables` đã chạy → cần migration **mới** tái tạo bảng `django_celery_beat_*` (chạy lại `django_celery_beat` migrations).
7. `docker-compose.{prod,local}.yml`: thêm lại service `worker` + `beat` (bỏ profile `celery-fallback`, để chạy mặc định); thêm env `REDIS_CELERY_BROKER_DB=3`, `REDIS_CELERY_RESULT_DB=4`.

---

## 6. Rủi ro & lưu ý

| Rủi ro | Mức | Giảm thiểu |
|---|---|---|
| `0001_drop_celery_beat_tables` đã drop bảng beat | Cao | Viết migration mới gọi lại `django_celery_beat` schema, hoặc revert migration đó. Test trên staging trước. |
| Lark push (ads + revenue) sai field/record_id → tạo bản ghi trùng | Cao | Bám sát `record_key` idempotency của Go. Test bằng table Lark staging trước khi trỏ prod. |
| RAM tăng ~1GB do Celery prefork (lý do ban đầu migrate sang Go!) | TB | Chấp nhận theo quyết định nghiệp vụ. Có thể giảm `--concurrency` hoặc đổi pool `gevent`/`solo` nếu VPS chật. |
| Mất rate-limit per-channel KiotViet (Go dùng token bucket) | TB | Khôi phục cơ chế rate-limit nội bộ task Python như bản cũ (đã có ở `97f71dd`). |
| Fix per-order chưa được merge → regression delivery price | Cao | P4/P5 bắt buộc diff kỹ Go↔Python trước cutover. |
| WS event: Go publish qua Redis Pub/Sub `crm_updates`, Python publish trực tiếp qua Channels | Thấp | Task Python chạy in-process → gọi thẳng `channel_layer.group_send` như trước. Bỏ `pubsub-bridge`. |

---

## 7. Sprint

### Sprint 1 — Khôi phục hạ tầng Celery (Phần A)
Mục 1–7 của §5. Tiêu chí pass: `celery worker` + `beat` khởi động, `dispatch()` chạy task qua Celery (test 1 task đơn giản như `lark.notify_order_created`), beat seed thành công. **Chưa tắt Go.**

### Sprint 2 — Parity Pattern A đơn giản (P5, P6 + verify zalo/kv.process_job/webhook/audit/hourly_audit)
Diff từng task Go↔Python, vá chênh lệch. Tiêu chí: mỗi task chạy qua Celery cho kết quả khớp Go trên staging.

### Sprint 3 — Port tính năng Go-only (P1, P2, P3, P4)
Lark client Python → revenue Lark push → ads report task → merge fix auto_sync. Nặng nhất. Tiêu chí: bảng Lark staging nhận đúng số bản ghi create/update/zero như Go.

### Sprint 4 — Cutover + dọn dẹp
1. Set `TASK_BACKEND=celery`, `AUDIT_BACKEND=celery` toàn cục.
2. Bật `worker` + `beat`, tắt `task-worker`/`task-cron`/`audit-writer`/`pubsub-bridge`.
3. Smoke test toàn bộ flow critical (order create, ingest, webhook, restore, revenue, ads, reminders).
4. Monitor 48h → xoá `go-services/`, gỡ service Go khỏi compose, gỡ bảng `jobs`/`go_cron_runs`/`go_audit_batch_marks` (migration), dọn `apps/dispatch/queue.py` + `apps/internal` proxy nếu không còn dùng.

---

## 8. Tiến độ

- [x] **Sprint 1 — hạ tầng Celery.** deps (celery/kombu/billiard/vine/amqp), `config/celery.py`, settings `CELERY_*`, **beat tĩnh `CELERY_BEAT_SCHEDULE`** (không dùng django_celery_beat — tránh rắc rối bảng đã bị drop), `dispatch/api.py` + `registry.py`, `@shared_task` cho 18 task, `worker`+`beat` trong compose.
- [x] **Sprint 2 — parity Pattern A.** Audit Go↔Python: process_job / webhook_push_old / hourly_audit / zalo / fb / audit đều **parity OK**. Vá: webhook_sync giữ price+deliveryCode + WS event (b369199); zalo order_created `total` format 2 chữ số.
- [x] **Sprint 3 — port Go-only.**
  - 3a Lark Bitable client: **đã có sẵn** (`lark_bitable_client.py`).
  - 3b revenue Lark push + backfill: **đã có sẵn** (`lark_revenue_sync.push_daily_aggregates_to_lark`).
  - 3c ads.report_lark_sync: **viết mới** `apps/facebook_ads/infrastructure/lark_ads_report_sync.py` + task `sync_ads_report_lark` + settings 2 ads table ID. Port trung thành SQL + create/update/zero + record_key idempotency.
  - 3d auto_sync flow 2: vá status PUT hóa đơn cũ kèm price+deliveryCode (parity per-order Go).
- [x] **Sprint 4 — cutover + dọn dẹp.** Xoá `worker`/`beat` chạy mặc định; **gỡ 3 Go service + pubsub-bridge + go-env** khỏi `docker-compose.{prod,local}.yml`; **xoá `go-services/`**; dispatch → celery-only (xoá `queue.py`/`audit_http.py`, làm rỗng `models.py`); **migration `0003_drop_go_task_queue`** drop bảng jobs/go_cron_runs/go_audit_batch_marks; **xoá `apps/internal`** (proxy Go); dọn prometheus/alerts scrape Go.

### Lưu ý vận hành khi deploy
1. Cần Python runtime/Docker để chạy `python manage.py check` + `migrate` + `pytest` — môi trường hiện tại (Windows host) không có, **bắt buộc verify ở staging/CI trước khi lên prod**.
2. Set env prod: `TASK_BACKEND=celery`, `AUDIT_BACKEND=celery`, `REDIS_CELERY_BROKER_DB=3`, `REDIS_CELERY_RESULT_DB=4`, và `LARK_BITABLE_ADS_CHANNEL_TABLE_ID` / `LARK_BITABLE_ADS_PRODUCT_TABLE_ID`.
3. `migrate` sẽ chạy `dispatch.0003` drop 3 bảng Go — đảm bảo không còn job pending quan trọng trong bảng `jobs` trước khi cutover.
4. Beat dùng PersistentScheduler ghi file `/tmp/celerybeat-schedule` (đã set trong command).
</content>
</invoke>
