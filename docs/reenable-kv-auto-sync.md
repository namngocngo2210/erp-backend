# Re-enable `kv.auto_sync_full` (và `kv.auto_update_code`) an toàn

> Trạng thái: **Sprint 1 đang triển khai** · Branch: `feature-reenable-kv-auto-sync`
> Ngày tạo: 2026-05-25 · Owner: CTO

## 1. Bối cảnh

Cron `kv.auto_sync_full` (port Go của `process_auto_sync_invoice_shipping_fee_and_status`)
đã bị **disable từ 2026-05-15** trong [`config/task-cron.yaml`](../go-services/config/task-cron.yaml).
`kv.auto_update_code` cũng disable cùng lý do.

Hệ quả khi tắt: order đã push KV nếu bị đổi `shipping_fee` / `status` / `invoice_status`
bên KiotViet sẽ **không tự sync ngược về** hệ thống.

## 2. Nguyên nhân gốc (root cause)

Không phải thiếu tài nguyên (queue `kiotviet` đã có concurrency 20 / batch 10).
Vấn đề nằm ở **thiết kế job**:

| # | Vấn đề | Chi tiết |
|---|--------|----------|
| 1 | **Fat job** | Cron enqueue đúng 1 job; handler lặp toàn bộ order 30 ngày trong 1 invoke → chạy nhiều phút, giữ chặt 1 slot. |
| 2 | **Bypass rate limiter** | Rate limiter 12/min ở consumer key theo `channel_id` trong payload. Job cron không có `channel_id` → vòng lặp nội bộ gọi KV API **không bị throttle** → hammer gateway. |
| 3 | **Cùng queue + cùng priority với push/kship** | Mọi job vào `kiotviet` đều `priority=0`. `ClaimBatch` đã `ORDER BY priority DESC, id ASC` nhưng cơ chế ưu tiên **chưa ai dùng** → job sweep cạnh tranh ngang hàng với job user-trigger. |
| 4 | **Có thể kéo dài > chu kỳ cron** | Cron 60s; 1 run > 60s làm trễ sync (RecordFire UNIQUE/phút chặn double-fire nhưng không trị gốc). |

→ Re-enable nguyên trạng = lặp lại sự cố. Phải sửa thiết kế.

## 3. Chiến lược: Phased C → A

- **Sprint 1 — Vá nhanh (Phương án C):** thêm guard để re-enable an toàn + đo tải thực tế.
- **Sprint 2 — Refactor (Phương án A):** fan-out + priority, trị tận gốc #1/#2/#3, áp dụng chung cho cả `kv.auto_update_code`.

---

## 4. Sprint 1 — Vá nhanh (re-enable an toàn)

**Mục tiêu:** bật lại `kv.auto_sync_full` mà không hammer KV và không giữ slot quá lâu.
Thay đổi tối thiểu, dễ revert.

### 4.1 Thay đổi
1. **Throttle nội bộ handler** — thêm `rate.Limiter` global (mặc định ~8 req/s, burst 1)
   trong `KVAutoSyncFullNative`; `Wait()` trước mỗi `GetInvoiceByCode`. Trị #2 (smooth KV calls).
2. **Thu hẹp scope** — `kvAutoSyncRecentDays` 30 → 7 (đa số order resolve trong vài ngày;
   dùng đúng partial index `order_kv_auto_sync_idx`). Trị #1.
3. **Time budget per run** — dừng vòng lặp sau `kvAutoSyncMaxRunDuration` (mặc định 45s, < tick 60s),
   trả kết quả `truncated=true`. Job **không bao giờ** giữ slot quá ~45s. Trị #1/#4.
4. **Re-enable cron** — bật lại trong `task-cron.yaml`, queue `kiotviet`. Để an toàn ban đầu
   đặt `@every 5m` (sau khi đo tải có thể hạ về 60s).

### 4.2 Đánh đổi đã biết
- Throttle + budget → 1 run có thể **không quét hết** set trong 1 lần (eventual consistency).
  Chấp nhận được: lần tick sau quét tiếp. Đây là background sync, không phải real-time.
- Vẫn dùng chung queue/priority với push/kship (chưa trị #3) — nhưng vì job ngắn (≤45s) +
  throttle nên áp lực slot thấp. #3 để Sprint 2.

### 4.3 Tiêu chí nghiệm thu
- [ ] `go build ./...` + `go vet` + test handler pass.
- [ ] Sau khi deploy, metrics `:2112`: backlog queue `kiotviet` không tăng bất thường;
      latency job push/kship không tăng.
- [ ] KV gateway không xuất hiện 429 / rate-limit error.
- [ ] Order đổi `shipping_fee`/`status` bên KV được sync về trong ≤ 1 chu kỳ.

### 4.4 Tiến độ Sprint 1
- [x] (1) Throttle nội bộ — `rate.Limiter` 8 req/s trong `KVAutoSyncFullNative`
- [x] (2) recentDays 30 → 7
- [x] (3) Time budget per run — `kvAutoSyncMaxRunDuration=45s` + `errStopSweep`, kết quả `truncated`
- [x] (4) Re-enable cron `@every 5m` (queue `kiotviet`)
- [x] Build + vet + test handler pass (qua container `golang:1.25`)
- [ ] Đo tải production (sau deploy)

---

## 5. Sprint 2 — Refactor fan-out + priority (Phương án A)

**Mục tiêu:** trị tận gốc, scale tự nhiên, dùng chung cho `kv.auto_update_code`.

### 5.1 Thiết kế
1. **Tách dispatcher / worker:**
   - `kv.auto_sync_full` (cron) → handler **mỏng**: chỉ SELECT order trong scope rồi
     enqueue N job con `kv.auto_sync_one`, payload `{ order_id, channel_id }`.
   - `kv.auto_sync_one` → handler xử lý **1 order**: GET invoice + update + sync old channel.
2. **Đi qua rate limiter đúng nghĩa:** job con có `channel_id` trong payload → consumer
   throttle 12/min/channel tự động (hết bypass #2).
3. **Priority:** thêm tham số `priority` cho `EnqueueJob`/cron enqueuer; job con sweep
   dùng `priority=-10`, push/kship giữ `0` → user-trigger luôn được claim trước (#3).
4. **Idempotent + de-dup:** tránh enqueue trùng order đang có job pending (check theo
   `task_type + order_id` hoặc unique partial index).
5. Áp dụng cùng pattern cho `kv.auto_update_code` (`kv.auto_update_code_one`).

### 5.2 Cần thay đổi
- `EnqueueJobParams` / cron `JobEnqueuer.Enqueue` nhận `priority`.
- Handler mới `KVAutoSyncOneNative`, đăng ký trong `module.go`.
- (Tùy chọn) index de-dup trên `jobs(task_type, (payload->>'order_id'))` where state='pending'.

### 5.3 Tiến độ Sprint 2
- [x] Thêm priority vào enqueue path — `enqueueJobPriority` helper; job con dùng `kvSweepChildPriority=-10`
- [x] Handler `kv.auto_sync_one` + dispatcher mỏng (`KVAutoSyncFullNative` → `fanOutSweep`)
- [x] De-dup pending jobs — `pendingChildOrderIDs` (skip order đã có job con `pending`/`running`)
- [x] Áp dụng cho `kv.auto_update_code` (`kv.auto_update_code_one`) — re-enable luôn trong `task-cron.yaml`
- [x] Gỡ guard tạm Sprint 1 (time-budget + throttle global) — không còn cần vì job con ngắn + qua rate limiter consumer
- [x] Cron auto_sync trả về `@every 60s` (dispatcher nhẹ)
- [ ] Đo tải production (sau deploy)

### 5.4 Ghi chú triển khai
- Job con đi vào **chính queue `kiotviet`** với `priority=-10`. `ClaimBatch` (`ORDER BY priority DESC`)
  đảm bảo push/kship (priority 0) luôn được claim trước → không cần tách queue riêng.
- Job con **mang `channel_id`** trong payload → consumer rate limiter `12/min` per `channel_id`
  áp dụng tự động (hết bypass — root cause #2).
- De-dup theo `payload->>'order_id'` + `state IN ('pending','running')` → cron tick lại
  không enqueue trùng order đang chờ/đang chạy.

---

## 6. Rollback
- Sprint 1: comment lại block cron trong `task-cron.yaml` → về trạng thái disable hiện tại.
- Sprint 2: comment lại cả `kv.auto_sync_full` và `kv.auto_update_code` trong `task-cron.yaml`
  → dispatcher không chạy → không enqueue job con. Handler con (`*_one`) vẫn đăng ký nhưng
  không được gọi. An toàn, không cần revert code.
