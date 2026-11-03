# Audit followups 2026-07-14 (vòng 5) — Plan & nhật ký

Scope vòng 5: (1) adversarial review commit vòng 4 (`29390387`, đã merge); (2) ba sweep
chưa từng làm có hệ thống — endpoint thiếu gate (209 endpoint), cron/celery (toàn bộ beat),
webhook/verifier (xác minh 2 lỗ hổng treo từ điều tra "đơn tự hủy"). Branch:
`fix/audit-followups-2026-07-14e`.

**Kết quả tổng**: commit vòng 4 không có CAO (2 TRUNG là hồi quy tinh vi của chính nó, fix
ở vòng này); 209 endpoint không có lỗ hổng CAO; 2 lỗ hổng webhook cũ ĐÃ VÁ từ trước (có
test chứng minh — memory đã cập nhật). 2 CAO mới đều ở tầng cron/hạ tầng.

## CAO

### 1. `process_kiotviet_job` dính time limit toàn cục 25s/30s → hoá đơn TRÙNG trên KiotViet
Task đẩy đơn lên KiotViet gọi nhiều HTTP + retry-sleep; bị giết ở 25s là chết đúng khoảng
"hoá đơn ĐÃ tạo bên KV nhưng CHƯA ghi invoice_code về Order" → job FAILED → user bấm đẩy
lại → hoá đơn trùng. Rất có thể là nguồn của nghi vấn "ai đó PUT update hoá đơn từ DESI"
trong điều tra đơn-tự-hủy. **Fix**: `soft_time_limit=120, time_limit=150` riêng cho task.

### 2. Prod 1 worker ăn cả 5 queue → job tương tác xếp hàng sau job sync dài
Compose ghi `--concurrency=4` (prod thực chạy 8 qua env); reconcile 21h (fan-out per-cặp
tới 65') + cron trạng thái 30' + ads sync 60s có thể bão hoà slot → `process_landing_ingest`
(tạo đơn) và `process_kiotviet_job` (user chờ) trễ hàng chục phút. **Fix**: tách service
`worker-kiotviet` riêng ôm queue `kiotviet` (env `KIOTVIET_WORKER_CONCURRENCY`, default 4);
worker chính giữ `default,orders,lark_noti,audit`; route 4 task ads sync từ queue `orders`
sang `default` (ads chậm không được chiếm slot của đường tạo đơn).

## TRUNG

3. **Best retry — event lỗi deterministic vẫn bất tử** (hồi quy vòng 4 của chính tôi):
   miễn-abandon theo "lỗi trong vòng này" thì event raise ổn định được miễn MỖI vòng.
   **Fix**: chỉ miễn khi lỗi LẦN ĐẦU THẬT (processing_error cũ còn rỗng); lỗi tái diễn quá
   tuổi thì abandon. `.update()` ghi vết bọc try/except riêng (DB chết không giết cả batch).
   Test 2 vòng: vòng 1 ghi "error:" chưa bỏ cuộc, vòng 2 bỏ cuộc.
4. **Revive handler fail = tồn lệch im lặng**: reissue+resync atomic rollback mà bus nuốt
   exception → không ai biết. **Fix**: bắn Lark alert (kênh stock-negative) kèm lệnh chạy
   lại `reissue_after_cancel_release(order_id=...)` rồi re-raise.
5. **`GET /api/channels/staff` mở cho mọi user đăng nhập** → full danh bạ nhân viên (kể cả
   `?department=` phòng khác). **Fix**: `require_section(*ORDER_SECTIONS, "lead-assignment",
   "customers", "loyal-customers", "warranty", "users")` — đúng tập trang đang dùng dropdown.
   Tương tự `GET /api/channels/list-for-kiotviet` → `(*ORDER_SECTIONS, "lead-assignment")`.
6. **`process_sale_reminders`**: (a) time_limit 90s > chu kỳ 60s, không lock → 2 tick chồng
   gửi TRÙNG tin Lark → single_flight; (b) row `lark_post_failed` giữ nguyên due_at chiếm
   đầu hàng `order_by(due_at)[:100]` vĩnh viễn → đủ 100 row hỏng là reminder mới không bao
   giờ gửi → retry với backoff (due_at += 10').
7. **`update_invoice_status_targets`**: không single-flight (window cả tháng, cuối tháng
   chạy dài dần → tick 30' chồng, 2 run cùng ghi Order.status) + `except Exception` per-kênh
   nuốt cả `SoftTimeLimitExceeded` (chạy tiếp tới hard-kill). **Fix**: single_flight TTL
   1800 + re-raise SoftTimeLimitExceeded.
8. **`facebook_ads.sync_all_daily_spend`**: time_limit 360s = 6× chu kỳ 60s, không lock →
   tới ~6 instance chồng cùng upsert. **Fix**: single_flight, tick sau skip.

## THẤP
9. Webhook KiotViet hardening: dedupe theo `payload["Id"]` (cache SETNX, TTL
   `KIOTVIET_WEBHOOK_DEDUPE_TTL`=600s, chống replay request đã-ký) + trần
   `KIOTVIET_WEBHOOK_MAX_ITEMS`=200 item/request (body 10MB → N call KiotViet đồng bộ).
   Ghi vẫn idempotent — đây là chống khuếch đại, không phải chốt đúng-sai. Test payload
   phải dùng Id ngẫu nhiên (cache Redis test shared giữa các lần chạy).
10. `create_audit_log_task`: `max_retries=3` chết (không bao giờ retry) → `autoretry_for=
    (OperationalError,), retry_backoff=True`.
11. `_resolve_invoice_status` fallback bỏ `statusValue`/`StatusValue` (thang hoá đơn —
    chính `_resolve_invoice_own_status` đọc field này), chỉ giữ `deliveryStatus`.
12. `process_restore_orders`: `soft_time_limit=300, time_limit=360` (batch không giới hạn
    từng bị SIGKILL 30s giữa vòng lặp — nửa batch khôi phục, event completed không phát).

## Ghi nhận KHÔNG fix vòng này (chủ đích / chờ quyết)
- Nhắc chấm công DM cả Chủ nhật — chờ xác nhận lịch làm việc (bán lẻ có thể làm cuối tuần).
- Quyền `channels:create/edit/delete` chết (BE superuser-only) — chờ đợt dọn catalog.
- Unmatched Best già chỉ được thử 1 lần sau outage rồi abandon: chấp nhận (shipment luôn
  được tạo TRƯỚC khi Best push; mã >3 ngày không khớp ≈ không thuộc hệ thống).
- Cron own-status lạ (không 1/2) không delivery bị ép 3: bề mặt ~0 (hoá đơn fail-delivery
  luôn có invoiceDelivery); đường import đã đúng (create → NEW, update → giữ nguyên).
- `ensure_stock_movements_partition` lỡ nhịp chỉ có log — gắn với alerting đứt đã ghi nhận.

## Deploy notes
- **Compose prod đổi topology worker**: service mới `worker-kiotviet` (queue `kiotviet`),
  worker chính bỏ queue `kiotviet`. Prod đang chạy concurrency 8 qua env — giữ
  `CELERY_WORKER_CONCURRENCY=8` cho worker chính nếu muốn, worker-kiotviet mặc định 4
  (`KIOTVIET_WORKER_CONCURRENCY`). `docker compose -f ... up -d worker worker-kiotviet`.
- Env mới (tuỳ chọn): `KIOTVIET_WEBHOOK_MAX_ITEMS` (200), `KIOTVIET_WEBHOOK_DEDUPE_TTL`
  (600, đặt 0 để tắt dedupe).
- RBAC: user cần dropdown nhân viên/kênh giờ phải có ít nhất một trong các section đã liệt
  kê ở mục 5 — mọi role thực tế đang dùng các trang đó đều có sẵn.

## Nhật ký
- Toàn bộ CAO/TRUNG/THẤP trên: XONG trong 1 commit. Test mới: best retry
  persistent-exception 2 vòng; webhook replay/dedupe/cap (3 test); test cũ `Id` cố định đổi
  sang uuid (chống flaky cache Redis shared).
