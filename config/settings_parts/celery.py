"""Celery config + beat schedule — tách khỏi base.py (audit 2026-07 mục 4.7).

``base.py`` làm ``from .celery import *`` nên mọi tên CELERY_* vẫn truy cập được
từ ``config.settings`` như trước.
"""

import os

from celery.schedules import crontab

from ._redis import build_redis_url as _build_redis_url

# ── Celery broker & result backend (Redis DB 3/4) ──
REDIS_CELERY_BROKER_DB = os.getenv("REDIS_CELERY_BROKER_DB", "3")
REDIS_CELERY_RESULT_DB = os.getenv("REDIS_CELERY_RESULT_DB", "4")

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", _build_redis_url(REDIS_CELERY_BROKER_DB))
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", _build_redis_url(REDIS_CELERY_RESULT_DB))
CELERY_TASK_TRACK_STARTED = True
# Worker PHẢI phát task events (task-succeeded/failed/...) — celery-exporter đếm
# celery_task_*_total từ đúng những event này, và toàn bộ nhóm alert
# CeleryTaskFailing / AiMart*Silent / AiChatExtractSilent đọc metric đó. Mặc định
# Celery là False → trước 2026-09-22 exporter chỉ thấy heartbeat, ba alert AiMart
# nổ vĩnh viễn qua nhánh absent() còn CeleryTaskFailing thì câm. Không bật
# task_send_sent_event: event task-sent do web/beat phát, exporter cảnh báo nổ
# cardinality theo hostname mà không thêm giá trị cho các rule hiện có.
CELERY_WORKER_SEND_TASK_EVENTS = True
# Global guard rail; long-running tasks override via @shared_task(time_limit=..., soft_time_limit=...).
CELERY_TASK_TIME_LIMIT = int(os.getenv("CELERY_TASK_TIME_LIMIT", "30"))
CELERY_TASK_SOFT_TIME_LIMIT = int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", "25"))
CELERY_TASK_DEFAULT_QUEUE = os.getenv("CELERY_TASK_DEFAULT_QUEUE", "default")
# Nhịp kéo chi tiêu Facebook. Để env chỉnh được vì đây là đánh đổi rate limit ↔ độ
# tươi số liệu, không phải hằng số kỹ thuật.
FB_SYNC_INTERVAL_SECONDS = float(os.getenv("FB_SYNC_INTERVAL_SECONDS", "300"))
CELERY_KIOTVIET_QUEUE = os.getenv("CELERY_KIOTVIET_QUEUE", "kiotviet")
CELERY_ORDERS_QUEUE = os.getenv("CELERY_ORDERS_QUEUE", "orders")
CELERY_LARK_NOTI_QUEUE = os.getenv("CELERY_LARK_NOTI_QUEUE", "lark_noti")
CELERY_SLOW_QUEUE = os.getenv("CELERY_SLOW_QUEUE", "slow")
# Queue RIÊNG cho export, không dùng chung `slow`: queue đó đang ôm job Gemini
# (soft_time_limit 300–480s) và precompute báo cáo (time_limit 900s) với concurrency 2
# — dùng chung là đổi timeout lấy độ trễ xếp hàng sau một job LLM 8 phút.
CELERY_EXPORTS_QUEUE = os.getenv("CELERY_EXPORTS_QUEUE", "exports")
# Queue riêng cho Foxia, KHÔNG khai chung `slow`: một lượt backfill có thể chạy
# hàng chục phút và sẽ đứng chắn trước job Gemini/precompute vốn cũng ở đó.
# Từ 2026-09-09: có container RIÊNG `worker-foxia` nghe cả `foxia` lẫn
# `foxia_push` (xem docker-compose.prod.yml) — không còn ghép vào `slow` nữa.
CELERY_FOXIA_QUEUE = os.getenv("CELERY_FOXIA_QUEUE", "foxia")
# Queue RIÊNG cho chiều đẩy đơn. Không dùng lại `foxia`: queue đó ôm
# sync_catalog/sync_orders/sync_delivered với time_limit=900 — một đơn landing
# xếp sau lượt backfill 15 phút thì việc "đẩy ngay khi lưu" mất hết ý nghĩa.
CELERY_FOXIA_PUSH_QUEUE = os.getenv("CELERY_FOXIA_PUSH_QUEUE", "foxia_push")
CELERY_TASK_ROUTES = {
    "apps.orders.tasks.process_landing_ingest": {"queue": CELERY_ORDERS_QUEUE},
    "apps.orders.tasks.process_restore_orders": {"queue": CELERY_ORDERS_QUEUE},
    "apps.orders.tasks.process_kiotviet_job": {"queue": CELERY_KIOTVIET_QUEUE},
    "apps.kiotviet.tasks.sync_daily_revenue_all_channels": {"queue": CELERY_KIOTVIET_QUEUE},
    "apps.kiotviet.tasks.reconcile_paired_channels": {"queue": CELERY_KIOTVIET_QUEUE},
    "apps.kiotviet.tasks.reconcile_one_pair": {"queue": CELERY_KIOTVIET_QUEUE},
    "apps.kiotviet.tasks.update_invoice_status_targets": {"queue": CELERY_KIOTVIET_QUEUE},
    "apps.kiotviet.tasks.update_invoice_status_deep": {"queue": CELERY_KIOTVIET_QUEUE},
    "apps.kiotviet.tasks.import_invoices_create_only": {"queue": CELERY_KIOTVIET_QUEUE},
    "apps.kiotviet.tasks.import_create_only_one_channel": {"queue": CELERY_KIOTVIET_QUEUE},
    "apps.kiotviet.tasks.sync_kiotviet_returns": {"queue": CELERY_KIOTVIET_QUEUE},
    "apps.orders.tasks.notify_lark_order_created": {"queue": CELERY_LARK_NOTI_QUEUE},
    "apps.orders.tasks.notify_lark_vat_invoice_required": {"queue": CELERY_LARK_NOTI_QUEUE},
    "apps.orders.tasks.notify_lark_cancelled_after_dispatch": {"queue": CELERY_LARK_NOTI_QUEUE},
    "apps.orders.tasks.process_sale_reminders": {"queue": CELERY_LARK_NOTI_QUEUE},
    "apps.orders.tasks.process_no_answer_reminders": {"queue": CELERY_LARK_NOTI_QUEUE},
    "apps.hr.tasks.send_attendance_reminder": {"queue": CELERY_LARK_NOTI_QUEUE},
    # Ads sync là nền chạy dày (60s/300s) — KHÔNG để chung queue `orders` với
    # process_landing_ingest (tạo đơn, user chờ): API ads chậm là chiếm slot của
    # đường tạo đơn (audit 2026-07-14 vòng 5).
    "apps.facebook_ads.tasks.sync_all_daily_spend": {"queue": CELERY_TASK_DEFAULT_QUEUE},
    "apps.facebook_ads.tasks.sync_all_ad_spend": {"queue": CELERY_TASK_DEFAULT_QUEUE},
    "apps.facebook_ads.tasks.sync_all_ad_catalog": {"queue": CELERY_TASK_DEFAULT_QUEUE},
    "apps.facebook_ads.tasks.assign_campaign_products": {"queue": CELERY_TASK_DEFAULT_QUEUE},
    "apps.facebook_ads.tasks.check_facebook_tokens": {"queue": CELERY_TASK_DEFAULT_QUEUE},
    "apps.facebook_ads.tasks.sync_ads_report_lark": {"queue": CELERY_TASK_DEFAULT_QUEUE},
    "apps.google_ads.tasks.sync_all_daily_spend_google": {"queue": CELERY_TASK_DEFAULT_QUEUE},
    "apps.google_ads.tasks.sync_all_campaign_spend_google": {"queue": CELERY_TASK_DEFAULT_QUEUE},
    "apps.google_ads.tasks.sync_all_accounts_google": {"queue": CELERY_TASK_DEFAULT_QUEUE},
    "apps.google_ads.tasks.assign_campaign_products_google": {"queue": CELERY_TASK_DEFAULT_QUEUE},
    "apps.audit.tasks.create_audit_log_task": {"queue": "audit"},
    "apps.audit.tasks.create_audit_log_batch_task": {"queue": "audit"},
    # ── Queue `slow`: task dài/nặng, tách khỏi `default` (audit 2026-07-26 mục T5) ──
    # 4 task dưới đây trước nay rơi vào `default` cùng với các sweep điều phối lead.
    # Worker `default` chỉ có concurrency=4, trong khi:
    #   - 3 task callcenter gọi Gemini (transcribe/extract) + tải ghi âm Zenify:
    #     I/O-bound, soft_time_limit 300–480s, một tick có thể giữ slot hàng phút.
    #   - 1 task customers precompute báo cáo: nặng CPU/DB, time_limit tới 900s
    #     (đúng nhóm đã gây sự cố docs/report-precompute-timeout-2026-07-22.md).
    # 3 job LLM chạy cùng lúc + 1 rebuild_segment_snapshot là đã chiếm trọn 4 slot →
    # các sweep NGẮN nhưng cần đúng giờ (sweep_floating_pool, sweep_lead_assignment,
    # sweep_operation_entry) phải xếp hàng, và đó là thứ sale cảm nhận ngay.
    # Tách sang worker riêng để độ trễ điều phối lead không phụ thuộc vào Gemini.
    "apps.callcenter.tasks.sync_zenify_call_histories": {"queue": CELERY_SLOW_QUEUE},
    "apps.callcenter.tasks.transcribe_pending_call_records": {"queue": CELERY_SLOW_QUEUE},
    "apps.callcenter.tasks.extract_pending_call_insights": {"queue": CELERY_SLOW_QUEUE},
    "apps.callcenter.tasks.score_pending_call_quality": {"queue": CELERY_SLOW_QUEUE},
    "apps.customers.tasks.rebuild_segment_snapshot": {"queue": CELERY_SLOW_QUEUE},
    # Sinh mã bảo hành cho lô lớn (không giới hạn số lượng, ~3.500 mã/s) — resumable.
    "apps.warranty_codes.tasks.generate_batch_codes": {"queue": CELERY_SLOW_QUEUE},
    # ── CNV Loyalty sync: gọi API ngoài (apis.cnvloyalty.com), I/O-bound — queue
    # `slow`, không tranh slot với đường tạo đơn (spec 2026-08-05).
    "apps.cnv_loyalty.tasks.sync_customer": {"queue": CELERY_SLOW_QUEUE},
    "apps.cnv_loyalty.tasks.sync_product": {"queue": CELERY_SLOW_QUEUE},
    "apps.cnv_loyalty.tasks.sync_collection": {"queue": CELERY_SLOW_QUEUE},
    "apps.cnv_loyalty.tasks.sync_order": {"queue": CELERY_SLOW_QUEUE},
    "apps.cnv_loyalty.tasks.retry_failed_records": {"queue": CELERY_SLOW_QUEUE},
    "apps.cnv_loyalty.tasks.reconcile_full": {"queue": CELERY_SLOW_QUEUE},
    "apps.cnv_loyalty.tasks.run_backfill": {"queue": CELERY_SLOW_QUEUE},
    # Chiều ngược (spec 2026-08-19): CHỈ poll đi ra ngoài (apis.cnvloyalty.com).
    # retry/purge webhook chỉ chạm DB, để ở queue default đúng như cặp tương ứng
    # bên best_express — xem KNOWN_DEFAULT_QUEUE_TASKS ở test_celery_routes.
    "apps.cnv_loyalty.tasks.poll_inbound_orders": {"queue": CELERY_SLOW_QUEUE},
    "apps.foxia.tasks.sync_catalog": {"queue": CELERY_FOXIA_QUEUE},
    "apps.foxia.tasks.sync_orders": {"queue": CELERY_FOXIA_QUEUE},
    "apps.foxia.tasks.sync_delivered": {"queue": CELERY_FOXIA_QUEUE},
    "apps.foxia.tasks.match_lead_orders": {"queue": CELERY_FOXIA_QUEUE},
    # Lô POST (tối đa 150/lượt) → queue `foxia`, không chen vào `foxia_push` của đơn landing.
    "apps.foxia.tasks.push_resale_delivered": {"queue": CELERY_FOXIA_QUEUE},
    "apps.foxia.tasks.push_order_to_foxia": {"queue": CELERY_FOXIA_PUSH_QUEUE},
    # Pancake (spec 2026-09-16, tạo đơn từ 17/09): poll là I/O đối tác → chung
    # container worker-foxia; tạo lại đơn cho một lead cùng bản chất "đẩy đơn".
    "apps.pancake.tasks.poll_phone_leads": {"queue": CELERY_FOXIA_QUEUE},
    "apps.pancake.tasks.retry_lead_order": {"queue": CELERY_FOXIA_PUSH_QUEUE},
    # Pancake POS (docs/pancake-pos-product-sync.md): gọi Open API đối tác, lượt full
    # có thể vài chục phút → cùng queue `foxia` với poll Pancake, không chắn `slow`.
    "apps.pancake_pos.tasks.run_sync": {"queue": CELERY_FOXIA_QUEUE},
    "apps.pancake_pos.tasks.sync_dirty_products": {"queue": CELERY_FOXIA_QUEUE},
    "apps.pancake_pos.tasks.run_initial_stock": {"queue": CELERY_FOXIA_QUEUE},
    "apps.pancake_pos.tasks.push_orders": {"queue": CELERY_FOXIA_QUEUE},
    "apps.pancake_pos.tasks.push_orders_for": {"queue": CELERY_FOXIA_QUEUE},
    "apps.exports.infrastructure.tasks.run_export": {"queue": CELERY_EXPORTS_QUEUE},
    # KHÔNG route vào chính queue `exports`: đây là cơ chế TỰ CỨU cho job kẹt
    # RUNNING/PENDING khi worker-export chết — người gác cổng không thể sống
    # trong đúng căn nhà nó phải dọn (worker chết thì cơ chế cứu chết theo).
    # `worker` chính (queue default, luôn có mặt — không có nó thì mọi task
    # default khác cũng đã chết) tiêu thụ thay.
    "apps.exports.infrastructure.tasks.purge_expired_exports": {"queue": CELERY_TASK_DEFAULT_QUEUE},
    "apps.analytics.tasks.refresh_dwh_marts": {"queue": CELERY_SLOW_QUEUE},
    "apps.ai_mart.tasks.sync_hot_marts": {"queue": CELERY_SLOW_QUEUE},
    "apps.ai_mart.tasks.rebuild_all_marts": {"queue": CELERY_SLOW_QUEUE},
    "apps.ai_mart.tasks.extract_chat_labels": {"queue": CELERY_SLOW_QUEUE},
}
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = os.getenv("CELERY_TIMEZONE", "Asia/Bangkok")
CELERY_WORKER_POOL = os.getenv("CELERY_WORKER_POOL", "solo" if os.name == "nt" else "prefork")
CELERY_WORKER_CONCURRENCY = int(os.getenv("CELERY_WORKER_CONCURRENCY", "1" if os.name == "nt" else "4"))
# ── Recycle prefork child processes để chống memory leak tích tụ ──
# Worker prefork sống mãi sẽ tích RAM dần (ORM cache, allocator fragmentation) cho tới khi OOM.
# Đây là tương đương của gunicorn `--max-requests` ở tầng web (đã có) áp cho Celery:
# child bị thay mới sau N task → RAM được trả về OS định kỳ, giữ mức ổn định.
CELERY_WORKER_MAX_TASKS_PER_CHILD = int(os.getenv("CELERY_WORKER_MAX_TASKS_PER_CHILD", "300"))
# Lưới an toàn theo RSS (KB): child vượt ngưỡng sẽ được recycle sau task hiện tại.
# Mặc định 0 = tắt (dựa vào max-tasks ở trên). Khi bật ở prod phải giữ
# (giới hạn container MB * 1024 / concurrency * ~0.85) để tránh OOM trước khi recycle.
# Ví dụ prod 1GiB / concurrency=8 → ~100000 (≈100MB) mỗi child.
CELERY_WORKER_MAX_MEMORY_PER_CHILD = int(os.getenv("CELERY_WORKER_MAX_MEMORY_PER_CHILD", "0"))

# ── Static beat schedule (queues resolved via CELERY_TASK_ROUTES) ──
# Beat chạy django_celery_beat DatabaseScheduler (compose --scheduler): các entry
# dưới đây được sync vào DB mỗi lần beat start (DatabaseScheduler.setup_schedule
# → update_from_dict(app.conf.beat_schedule)) — code vẫn là nguồn chân lý.
# Cadence mirrors the former Go task-cron (go-services/config/task-cron.yaml).
# Đồng bộ status/giá/phí ship sang kênh cũ do webhook (realtime) + cron
# kv-reconcile-paired-channels (quét sâu hằng ngày) đảm nhiệm.
#
# ── Lệch pha, KHÔNG dùng `*/N` (audit cache/latency 2026-08-11 §2.6) ──
# `crontab(minute="*/15")` và `crontab(minute="*/30")` đều nổ tại phút 0, rồi lại
# chồng nhau tại 15/30/45 — mọi job định kỳ khởi động cùng một khoảnh khắc, tạo
# gai CPU + connection DB đều đặn ngay trong giờ người dùng thao tác. Chu kỳ giữ
# nguyên, chỉ dời điểm bắt đầu; các phút dưới đây đã chọn để KHÔNG job nào trùng
# phút với job nào:
#     */5  → 3,8,13,…  và  4,9,14,…
#     */15 → 2,17,32,47 · 6,21,36,51 · 11,26,41,56
#     */30 → 7,37 · 22,52
# Task chạy đúng giờ nghiệp vụ (`crontab(hour=…, minute=…)`) giữ nguyên tuyệt đối.
CELERY_BEAT_SCHEDULE = {
    # 300s (bằng nhịp Google Ads), hạ từ 60s: Facebook không cập nhật insights theo
    # phút nên tick 60s chỉ đốt rate limit, và khi token hỏng thì nhân log lỗi lên 5
    # lần (sự cố 25–27/07/2026 — docs/facebook-token-lifecycle-2026-07-27.md).
    "facebook-ads-sync-daily-spend": {
        "task": "apps.facebook_ads.tasks.sync_all_daily_spend",
        "schedule": FB_SYNC_INTERVAL_SECONDS,
    },
    # Cùng nhịp với grain account để hai bảng luôn phản ánh cùng một thời điểm —
    # chốt so khớp 0.98 mới có nghĩa.
    "facebook-ads-sync-ad-spend": {
        "task": "apps.facebook_ads.tasks.sync_all_ad_spend",
        "schedule": FB_SYNC_INTERVAL_SECONDS,
    },
    # Danh mục ad/page đổi chậm hơn chi tiêu nhiều — 1h là đủ, và giữ cho lượt
    # đồng bộ chi tiêu (nhịp 300s) không phải gánh thêm việc kéo catalog.
    "facebook-ads-sync-ad-catalog": {
        "task": "apps.facebook_ads.tasks.sync_all_ad_catalog",
        "schedule": crontab(minute=5),
    },
    # Soi hạn token trước khi Facebook từ chối: tự đổi sang long-lived nếu còn kịp,
    # không thì cảnh báo để kịp cấp System User token.
    "facebook-ads-check-tokens": {
        "task": "apps.facebook_ads.tasks.check_facebook_tokens",
        "schedule": crontab(hour=8, minute=0),
    },
    "ads-report-lark-sync": {
        "task": "apps.facebook_ads.tasks.sync_ads_report_lark",
        "schedule": 30 * 60.0,
    },
    "google-ads-sync-daily-spend": {
        "task": "apps.google_ads.tasks.sync_all_daily_spend_google",
        "schedule": 300.0,
    },
    # Grain chiến dịch: cùng nhịp 300s với grain account để hai bảng luôn phản ánh
    # cùng một thời điểm — so hai con số mới có nghĩa.
    "google-ads-sync-campaign-spend": {
        "task": "apps.google_ads.tasks.sync_all_campaign_spend_google",
        "schedule": 300.0,
    },
    # Phát hiện/cập nhật tài khoản con dưới MCC. Account list đổi hiếm → chạy mỗi 6h.
    "google-ads-sync-accounts": {
        "task": "apps.google_ads.tasks.sync_all_accounts_google",
        "schedule": 6 * 3600.0,
    },
    # Gán sản phẩm cho chiến dịch quảng cáo (đọc SKU từ tên chiến dịch). Chạy sau nhịp
    # sync catalog (phút 5) để chiến dịch mới có ngay sản phẩm; phút lẻ để không dồn
    # vào đầu giờ cùng loạt cron khác.
    "facebook-ads-assign-campaign-products": {
        "task": "apps.facebook_ads.tasks.assign_campaign_products",
        "schedule": crontab(minute=12),
    },
    "google-ads-assign-campaign-products": {
        "task": "apps.google_ads.tasks.assign_campaign_products_google",
        "schedule": crontab(minute=17),
    },
    # Danh mục Foxia (chi nhánh, nhân viên, nguồn, kênh, trạng thái) đổi rất chậm
    # → 1 lần/giờ là thừa đủ. Chạy ở phút 15 để không trùng nhịp với loạt cron
    # đầu giờ. Task tự no-op khi FOXIA_SYNC_ENABLED=0.
    "foxia-sync-catalog": {
        "task": "apps.foxia.tasks.sync_catalog",
        "schedule": crontab(minute=15),
    },
    # Đơn mới/vừa chốt: 20 phút — giữa dải 15–30' tài liệu Foxia khuyến nghị.
    "foxia-sync-orders": {
        "task": "apps.foxia.tasks.sync_orders",
        "schedule": 20 * 60.0,
    },
    # Đơn giao thành công đi lượt RIÊNG theo `ngayhoanthanh`: đơn chốt tháng
    # trước có thể giao xong tháng này, lọc theo ngày chốt sẽ không thấy nó đổi
    # trạng thái. Cửa sổ dài hơn nên nhịp thưa hơn.
    "foxia-sync-delivered": {
        "task": "apps.foxia.tasks.sync_delivered",
        "schedule": 3600.0,
    },
    # Đối chiếu cơ hội đã đẩy ↔ đơn Foxia đã kéo về. Thuần join local nên rẻ;
    # nhịp 15 phút để `foxia_order_id` không trễ hơn `sync_orders` (20 phút) bao
    # nhiêu. Lệch phút 7 để không chạy chồng lượt sync.
    "foxia-match-lead-orders": {
        "task": "apps.foxia.tasks.match_lead_orders",
        "schedule": crontab(minute="7,22,37,52"),
    },
    # Đơn giao thành công → cơ hội resale Foxia. MỖI PHÚT (user chốt 2026-10-06):
    # sale resale nhận gần như ngay. Lượt rỗng chỉ 2-3 câu SQL có index (~0,05s) nên
    # không chiếm `worker-foxia`; single_flight chặn chồng lượt khi một lượt đầy
    # 150 cơ hội (~30s, ca xấu vài phút) chưa xong.
    "foxia-push-resale-delivered": {
        "task": "apps.foxia.tasks.push_resale_delivered",
        "schedule": crontab(minute="*"),
        # worker-foxia concurrency 1: khi nó bận backfill (tới 900s) task mỗi phút
        # không được dồn thành hàng chục bản — quá 50s chưa chạy thì bỏ, lượt sau làm.
        "options": {"expires": 50},
    },
    # Poll hội thoại Pancake có sđt: 3 phút — độ trễ khách cho số → cơ hội Foxia.
    # Task tự no-op khi PANCAKE_POLL_ENABLED=0; single-flight nên chồng lượt vô hại.
    "pancake-poll-phone-leads": {
        "task": "apps.pancake.tasks.poll_phone_leads",
        "schedule": 180.0,
    },
    # Đồng bộ tăng dần sản phẩm → Pancake POS: xử lý hàng đợi dirty. No-op khi
    # PANCAKE_POS_ENABLED=0 hoặc hàng đợi rỗng (1 câu SQL); single-flight chung với lượt full.
    "pancake-pos-sync-dirty-products": {
        "task": "apps.pancake_pos.tasks.sync_dirty_products",
        "schedule": 120.0,
    },
    # Đẩy đơn CRM → Pancake (two-way-sync A2): quét bù đơn chốt + hàng đợi + sửa/huỷ.
    # No-op khi PANCAKE_POS_ENABLED hoặc PANCAKE_POS_PUSH_ORDERS_ENABLED tắt (mặc định tắt).
    "pancake-pos-push-orders": {
        "task": "apps.pancake_pos.tasks.push_orders",
        "schedule": 120.0,
    },
    # Webhook Pancake → CRM (two-way-sync B1): vớt sự kiện đơn pending/lỗi, khớp lại đơn
    # chờ (quá hạn → Chờ khớp + Lark), dọn sự kiện cũ. Không gọi Pancake (chỉ DB).
    "pancake-pos-webhook-cycle": {
        "task": "apps.pancake_pos.tasks.process_webhook_events",
        "schedule": 60.0,
    },
    # MR D: báo cáo đơn treo vận chuyển Pancake — chạy mỗi giờ (phút 1, chưa job nào dùng),
    # task tự gửi Lark đúng giờ PANCAKE_POS_STUCK_ALERT_HOUR, một lần/ngày.
    "pancake-pos-stuck-deliveries": {
        "task": "apps.pancake_pos.tasks.alert_stuck_deliveries",
        "schedule": crontab(minute=1),
    },
    "kiotviet-sync-revenue": {
        "task": "apps.kiotviet.tasks.sync_daily_revenue_all_channels",
        "schedule": 3600.0,
        "kwargs": {"time_range": "thismonth"},
    },
    # Đối soát new↔old channel TRONG THÁNG hiện tại (tạo thiếu / xóa hủy+trùng / sync lại
    # khi lệch giá, phí ship, trạng thái). Chạy 00:00 mỗi ngày, quét cả tháng để bắt mọi lệch
    # tích lũy; gọi thẳng SyncInvoicesService (không qua command). Lịch sử cũ dùng command CLI
    # `reconcile_channels` cho ngày tuỳ chỉnh.
    "kv-reconcile-paired-channels": {
        "task": "apps.kiotviet.tasks.reconcile_paired_channels",
        "schedule": crontab(hour=0, minute=0),
    },
    # Cập nhật invoice_status + status (giao hàng) cho đơn của các (kênh, chi nhánh)
    # cấu hình ở KIOTVIET_STATUS_SYNC_TARGETS. Thay 2 lệnh chạy tay
    # `import_kiotviet_invoices --update-status`; gọi ImportInvoicesService.update_status_phone
    # (service công khai, không qua command), window = trong tháng.
    "kv-update-invoice-status": {
        "task": "apps.kiotviet.tasks.update_invoice_status_targets",
        "schedule": 30 * 60.0,
    },
    # Quét sâu 1 lần/ngày 04:30: cùng task logic nhưng window lùi 62 ngày. Tick 30' lọc
    # theo purchaseDate từ ngày 1 tháng này nên hoá đơn mua THÁNG TRƯỚC giao xong / hoàn /
    # huỷ / thu tiền sang tháng này không bao giờ được cập nhật — vận hành phải chạy tay
    # `--update-status --from <tháng trước>` (sự cố 2026-09-16). Chạy sau import deep
    # 01:30 để đơn backdate vừa tạo cũng được cập nhật trạng thái.
    "kv-update-invoice-status-deep": {
        "task": "apps.kiotviet.tasks.update_invoice_status_deep",
        "schedule": crontab(hour=4, minute=30),
        "kwargs": {"lookback_days": 62},
    },
    # Kéo invoice mới của các kênh cấu hình ở KIOTVIET_IMPORT_CREATE_ONLY_TARGETS
    # và CHỈ tạo Order mới (create-only), mỗi 5'. Fan-out: mỗi kênh dispatch thành
    # một import_create_only_one_channel task riêng chạy song song trên queue kiotviet;
    # lock per-(channel, branch) trong task con đảm bảo cặp chưa chạy xong thì tick
    # sau SKIP (không import chồng).
    # Gọi ImportInvoicesService.import_invoices (service công khai, không qua command);
    # window = lùi KIOTVIET_IMPORT_CREATE_ONLY_LOOKBACK_DAYS ngày → hiện tại (filter
    # theo purchaseDate nên phải lùi để bắt hóa đơn backdate — xem _import_window).
    # Có thể gọi tay qua import_invoices_create_only_task.delay().
    "kv-import-create-only": {
        "task": "apps.kiotviet.tasks.import_invoices_create_only",
        "schedule": 5 * 60.0,
    },
    # Quét sâu 1 lần/ngày lúc 01:30: cùng task create-only nhưng window lùi 31 ngày —
    # vớt hóa đơn purchaseDate backdate sâu (đơn sàn sync trễ nhiều ngày, hủy-xuất lại
    # giữ ngày cũ) mà window ngắn của tick 5' không bao giờ thấy. Create-only nên đơn
    # đã có chỉ tốn "skipped", không ghi đè gì.
    "kv-import-create-only-deep": {
        "task": "apps.kiotviet.tasks.import_invoices_create_only",
        "schedule": crontab(hour=1, minute=30),
        "kwargs": {"lookback_days": 31},
    },
    # Kéo phiếu trả hàng KiotViet (/returns) của MỌI kênh → điền
    # OrderRecall.return_slip_code (mảng mã). Cửa sổ lastModifiedFrom = now-2h,
    # chồng lấn để không mất bản ghi; policy ghi = UNION — nối thêm mã KiotViet
    # chưa có vào cuối mảng, giữ nguyên thứ tự + nội dung mã đã có (nhập tay hoặc
    # sync trước đó), không đè hay xoá. Xem docs/erp-kiotviet-returns-sync.md.
    "kv-sync-returns": {
        "task": "apps.kiotviet.tasks.sync_kiotviet_returns",
        "schedule": 15 * 60.0,
    },
    "process-sale-reminders": {
        "task": "apps.orders.tasks.process_sale_reminders",
        "schedule": 60.0,
    },
    "knm-reminder-shift-start": {
        "task": "apps.orders.tasks.process_no_answer_reminders",
        "schedule": crontab(hour=8, minute=0),
        "kwargs": {"slot": "start_of_shift"},
    },
    "knm-reminder-morning-mid": {
        "task": "apps.orders.tasks.process_no_answer_reminders",
        "schedule": crontab(hour=11, minute=0),
        "kwargs": {"slot": "morning_mid"},
    },
    "knm-reminder-afternoon-mid": {
        "task": "apps.orders.tasks.process_no_answer_reminders",
        "schedule": crontab(hour=15, minute=0),
        "kwargs": {"slot": "afternoon_mid"},
    },
    "knm-reminder-end-of-shift": {
        "task": "apps.orders.tasks.process_no_answer_reminders",
        "schedule": crontab(hour=16, minute=30),
        "kwargs": {"slot": "end_of_shift"},
    },
    # Nhắc chấm công qua Lark DM (thay automation Base assistant): bot đọc thành
    # viên nhóm LARK_CHECKIN_REMINDER_CHAT_ID rồi DM từng người. chat_id rỗng = tắt.
    "hr-attendance-reminder-arrive": {
        "task": "apps.hr.tasks.send_attendance_reminder",
        "schedule": crontab(hour=7, minute=50),
        "args": ["arrive"],
    },
    "hr-attendance-reminder-leave": {
        "task": "apps.hr.tasks.send_attendance_reminder",
        "schedule": crontab(hour=17, minute=30),
        "args": ["leave"],
    },
    # Retention ảnh chấm công (audit followups 2026-07 mục 3.1): xoá object MinIO +
    # clear photo_key cho ảnh cũ hơn ATTENDANCE_PHOTO_RETENTION_DAYS; event giữ nguyên.
    "hr-purge-old-attendance-photos": {
        "task": "apps.hr.tasks.purge_old_attendance_photos",
        "schedule": crontab(hour=2, minute=30),
    },
    # Nhịp 2h: chỉ tính lại snapshot của khách CÓ THAY ĐỔI (xem docstring task).
    # Audit 2026-08-19: đổi giờ CHẴN → giờ LẺ. Giữ nguyên độ tươi 2h nhưng lệch pha
    # khỏi cụm task `*/2` khác (nguyên tắc lệch-pha ở đầu file) và tránh nổ đúng
    # 08:00/10:00/14:00/16:00 — đây là workload đọc nặng thứ hai của DB.
    "customers-rebuild-segment-snapshot": {
        "task": "apps.customers.tasks.rebuild_segment_snapshot",
        "schedule": crontab(minute=0, hour="1-23/2"),
    },
    # Lưới an toàn 1 lần/ngày lúc thấp điểm: quét lại TOÀN BỘ để nhặt mọi thay đổi
    # không chạm `updated_at` (sửa tay trên DB, xoá cứng, lỗi một chu kỳ tăng dần).
    # 03:20 — sau `hr-purge-old-attendance-photos` (02:30) và lệch khỏi mọi mốc
    # phút chẵn, không chồng với nhịp 2h ở phút 0.
    "customers-rebuild-segment-snapshot-full": {
        "task": "apps.customers.tasks.rebuild_segment_snapshot",
        "schedule": crontab(hour=3, minute=20),
        "kwargs": {"full": True},
    },
    # Tạo trước partition ledger cho tháng kế (chạy ngày 25 hàng tháng, dư thời gian trước khi lăn tháng).
    "inventory-ensure-stock-partition": {
        "task": "apps.inventory.tasks.ensure_stock_movements_partition",
        "schedule": crontab(day_of_month=25, hour=0, minute=30),
    },
    # Khớp mã vận đơn quét chưa có đơn (bảng pending_shipment_scans) với Order
    # đã được cron KiotViet 5' kéo về. Xem docs/erp-inventory-pending-scan.md.
    "inventory-match-pending-scans": {
        "task": "apps.inventory.tasks.match_pending_shipment_scans",
        "schedule": 60.0,
    },
    # Lưới an toàn sổ xuất cho đơn XUẤT LẠI `<mã>.NN` (docs/reissue-ledger-leak-2026-07-21.md).
    # Nhánh chuyển sổ lúc huỷ đơn chỉ bắt đúng khoảnh khắc webhook về; trượt thì tồn
    # phồng vĩnh viễn và không có đường tự khớp lại — task này quét bù + cảnh báo Lark.
    "inventory-sweep-reissue-ledger": {
        "task": "apps.inventory.tasks.sweep_reissue_ledger",
        "schedule": crontab(minute="7,37"),
    },
    # Đối soát tồn đơn HUỶ được hồi sinh (docs/erp-revive-stock-safety-net.md). Khác
    # sweep-reissue-ledger ở trên: kia lo cặp "đơn huỷ ↔ đơn xuất lại `<mã>.NN`" (hai
    # BẢN GHI đơn khác nhau), còn task này lo CHÍNH đơn đó sống lại mà không được trừ
    # lại tồn. 03:50 — sau các job retention 03:15–03:45, tránh chồng I/O.
    "inventory-audit-revived-orders-stock": {
        "task": "apps.inventory.tasks.audit_revived_orders_stock",
        "schedule": crontab(hour=3, minute=50),
    },
    # Nhắc kho các đơn huỷ đã xuất mà hàng chưa quay về sau 14 ngày. 08:30 giờ VN
    # (01:30 UTC) — đầu ca làm việc, đúng lúc có người xử lý được.
    "inventory-alert-awaiting-return": {
        "task": "apps.inventory.tasks.alert_awaiting_return_overdue",
        "schedule": crontab(hour=1, minute=30),
    },
    # Retention mã quét đã khớp (audit followups 2026-07 mục 3.2): xoá dòng MATCHED
    # cũ hơn PENDING_SHIPMENT_SCAN_RETENTION_DAYS; pending/failed/invalid/cancelled
    # KHÔNG BAO GIỜ bị xoá bởi task này.
    "inventory-purge-matched-pending-scans": {
        "task": "apps.inventory.tasks.purge_matched_pending_scans",
        "schedule": crontab(hour=3, minute=15),
    },
    # Áp lại event webhook BEST chưa xử lý được (tới trước khi shipment tồn tại, hoặc
    # apply_event lỗi). Không có cron này thì event nằm chết vĩnh viễn — Best đã nhận
    # Result 1 nên không retry (audit 2026-07-14).
    "best-express-retry-webhook-events": {
        "task": "apps.best_express.tasks.retry_unprocessed_best_webhook_events",
        "schedule": crontab(minute="2,17,32,47"),
    },
    # Retention event webhook BEST (audit 2026-07-14): xoá event ĐÃ xử lý cũ hơn
    # BEST_WEBHOOK_EVENT_RETENTION_DAYS lúc 03:20. Event chưa áp được (processed=False)
    # giữ nguyên để còn đối soát.
    "best-express-purge-webhook-events": {
        "task": "apps.best_express.tasks.purge_old_best_webhook_events",
        "schedule": crontab(hour=3, minute=20),
    },
    # Retention audit_logs (mục 3.1 audit hardening): purge batch 10k lúc 03:30.
    "audit-purge-old-logs": {
        "task": "apps.audit.tasks.purge_old_audit_logs",
        "schedule": crontab(hour=3, minute=30),
    },
    # Retention landing_ingest_traces + order_journey_events (mục 3.4), 03:45.
    # Journey events default retention=0 → task no-op tới khi bật env.
    "orders-purge-ingest-traces": {
        "task": "apps.orders.tasks.purge_old_ingest_traces",
        "schedule": crontab(hour=3, minute=45),
    },
    "orders-purge-journey-events": {
        "task": "apps.orders.tasks.purge_old_journey_events",
        "schedule": crontab(hour=3, minute=45),
    },
    # Kho số thả nổi: đưa đơn bỏ bê vào kho + gỡ entry đã được chăm lại, mỗi 15'.
    # Xem docs/sale-floating-pool.md §8.
    "orders-floating-pool-sweep": {
        "task": "apps.orders.tasks.sweep_floating_pool",
        "schedule": crontab(minute="6,21,36,51"),
    },
    # Lưới đỡ chia đơn tự động: chia lại đơn thuộc sheet còn trống staff trong
    # cửa sổ LEAD_ASSIGNMENT_SWEEP_LOOKBACK_HOURS, mỗi 5'. Vớt mọi đường skip
    # im lặng lúc ingest (config nhập muộn, đơn import/KiotViet không qua
    # webhook, đơn về ngoài giờ). Điều tra 2026-07-18.
    "orders-lead-assignment-sweep": {
        "task": "apps.orders.tasks.sweep_lead_assignment",
        "schedule": 5 * 60.0,
    },
    # Đóng lead đã chuyển đổi thành hóa đơn KiotViet (giao dịch ghi đôi —
    # điều tra phồng số Báo cáo tổng 2026-07-20), 02:10 hằng ngày.
    # TẮT từ 2026-09-08: task tự no-op khi LEAD_AUTO_CLOSE_ENABLED=0 (mặc định).
    # Luật khớp cũ chỉ dùng SĐT + thời gian nên hủy oan lead của khách mua nhiều
    # món (đơn 560919). Chỉ bật lại khi đã soát bằng --dry-run.
    "orders-close-converted-leads": {
        "task": "apps.orders.tasks.close_converted_leads",
        "schedule": crontab(hour=2, minute=10),
    },
    # Entry tự động vào flow tác nghiệp (spec §4.4): vớt khách có đơn hợp lệ
    # trong 24h chưa vào flow (hook at-ingest bị lỗi/skip). Xem entry_service.py.
    "contact-flow-entry-sweep": {
        "task": "apps.contact_flow.tasks.sweep_operation_entry",
        "schedule": 5 * 60.0,
    },
    # Kéo lịch sử cuộc gọi Zenify → callcenter_call_records + khớp đơn theo SĐT.
    # Gate bằng ZENIFY_CALL_SYNC_ENABLED (default tắt). Queue `slow` (xem
    # CELERY_TASK_ROUTES) — không chung queue orders/kiotviet, cũng không còn ở
    # default sau audit 2026-07-26. Xem docs/zenify-call-transcripts.md.
    "callcenter-sync-call-histories": {
        "task": "apps.callcenter.tasks.sync_zenify_call_histories",
        "schedule": crontab(minute="11,26,41,56"),
    },
    # Transcribe ghi âm bằng Gemini free tier (quota guard RPM/RPD trong service).
    "callcenter-transcribe-pending": {
        "task": "apps.callcenter.tasks.transcribe_pending_call_records",
        "schedule": crontab(minute="3,8,13,18,23,28,33,38,43,48,53,58"),
    },
    # Trích xuất chân dung khách từ transcript → điền OrderAssessment.
    "callcenter-extract-pending-insights": {
        "task": "apps.callcenter.tasks.extract_pending_call_insights",
        "schedule": crontab(minute="4,9,14,19,24,29,34,39,44,49,54,59"),
    },
    # Chấm chất lượng tư vấn theo khung kịch bản mẫu → trang Báo cáo QA.
    # 1 lần/ngày lúc 02:00 (Asia/Bangkok) chứ không phải mỗi 5' như transcribe:
    # báo cáo QA đọc theo tuần/tháng nên chấm sớm vài giờ không đổi kết luận gì,
    # mà chạy đêm thì không tranh worker `slow` với các sweep ban ngày. 02:00 cũng
    # đủ trễ để transcribe của ca tối đã xong.
    "callcenter-score-call-quality": {
        "task": "apps.callcenter.tasks.score_pending_call_quality",
        "schedule": crontab(hour=2, minute=0),
    },
    # Heartbeat healthchecks.io (docs/ops-hardening-2026-07.md): chứng minh cụm
    # Celery (beat + broker + worker) còn sống với giám sát NGOÀI VPS.
    # OPS_HEARTBEAT_URL trống = task no-op.
    "ops-heartbeat": {
        "task": "apps.common.tasks.ops_heartbeat",
        "schedule": 300.0,
    },
    # Trace Best Express cập nhật trạng thái VC 3 mã hành trình bảo hành (phiếu
    # chưa Hoàn thành/Hủy). Không có BestExpressAccount active → task no-op.
    # Xem docs/superpowers/specs/2026-07-18-warranty-journey-best-tracking-status-design.md.
    "warranty-sync-best-tracking": {
        "task": "apps.warranty.tasks.sync_best_tracking_statuses",
        "schedule": 600.0,
    },
    # ── CNV Loyalty (spec 2026-08-05): CNV_SYNC_ENABLED tắt → cả hai no-op ──
    # Retry record chưa synced với backoff 2^attempts phút (trần 6h, 10 lần).
    "cnv-retry-failed": {
        "task": "apps.cnv_loyalty.tasks.retry_failed_records",
        "schedule": crontab(minute="22,52"),
    },
    # Lưới cuối: vớt entity chưa có sync record. 02:40 — trước dải retention
    # 03:15-03:50, sau các job 02:00-02:30, tránh chồng I/O trên worker slow.
    "cnv-reconcile": {
        "task": "apps.cnv_loyalty.tasks.reconcile_full",
        "schedule": crontab(hour=2, minute=40),
    },
    # ── CNV chiều NGƯỢC (spec 2026-08-19): CNV_INBOUND_ENABLED tắt → cả ba no-op ──
    # Poll là LƯỚI VÁ cho webhook (webhook mới là đường chính) nên 15' là đủ dày;
    # phút lẻ 7,22,37,52 để không đụng nhịp :00/:30 của các job nặng khác.
    "cnv-poll-inbound-orders": {
        "task": "apps.cnv_loyalty.tasks.poll_inbound_orders",
        "schedule": crontab(minute="7,22,37,52"),
    },
    # Áp lại event webhook lỗi. Dày hơn poll vì đơn đã nằm sẵn trong DB, chỉ còn
    # thiếu bước ghi — không tốn request ra ngoài.
    "cnv-retry-webhook-events": {
        "task": "apps.cnv_loyalty.tasks.retry_unprocessed_webhook_events",
        "schedule": crontab(minute="*/10"),
    },
    # Retention payload webhook đã xử lý. 03:35 — trong dải retention 03:15-03:50.
    "cnv-purge-webhook-events": {
        "task": "apps.cnv_loyalty.tasks.purge_old_webhook_events",
        "schedule": crontab(hour=3, minute=35),
    },
    # Retention warranty_code_scan_logs (portal tra-cuu.delax.asia — spec 2026-08-06):
    # xoá log quét cũ hơn WARRANTY_PORTAL_SCAN_LOG_RETENTION_DAYS. 03:55 — cuối dải
    # retention 03:15-03:50, tránh chồng I/O với các job purge khác.
    "warranty-codes-purge-scan-logs": {
        "task": "apps.warranty_codes.tasks.purge_old_scan_logs",
        "schedule": crontab(hour=3, minute=55),
    },
    # Dọn file export hết hạn (TTL EXPORT_FILE_TTL_HOURS) + cứu job kẹt RUNNING do
    # worker chết giữa chừng. Mỗi giờ, phút 15.
    "purge-expired-exports": {
        "task": "apps.exports.infrastructure.tasks.purge_expired_exports",
        "schedule": crontab(minute=15),
    },
    # 04:25 — giờ chẵn, phút lẻ. Sau `customers-rebuild-segment-snapshot-full` (03:20)
    # 65 phút (lần đo 22/07 nó mất 426s), và lệch khỏi nhịp giờ lẻ phút 00 của
    # cụm rebuild-segment-snapshot. Nguyên tắc lệch pha ở đầu file.
    "analytics-refresh-dwh-marts": {
        "task": "apps.analytics.tasks.refresh_dwh_marts",
        "schedule": crontab(hour=4, minute=25),
    },
    # Nhịp 30' lệch pha (phút 7/37 — nguyên tắc lệch pha ở đầu file) nạp tăng dần
    # schema ai cho agent Hermes. Rebuild đêm 04:50: SAU refresh dwh 04:25 để
    # không giẫm I/O, và là cơ chế tự lành cho mọi thứ watermark bỏ sót.
    "ai-mart-sync-hot": {
        "task": "apps.ai_mart.tasks.sync_hot_marts",
        "schedule": crontab(minute="7,37"),
    },
    "ai-mart-rebuild-all": {
        "task": "apps.ai_mart.tasks.rebuild_all_marts",
        "schedule": crontab(hour=4, minute=50),
    },
    # Gán nhãn chat: nhịp 10' lệch pha khỏi sync (phút 7/37) VÀ khỏi cnv-retry-failed
    # (22/52) + cnv-poll-inbound-orders (7/22/37/52) — cả hai cũng chạy trên
    # queue slow, phút 2/12/32/42 từng đụng 22/52. Lô nhỏ, chạy đều còn hơn dồn cục.
    "ai-mart-chat-extract": {
        "task": "apps.ai_mart.tasks.extract_chat_labels",
        "schedule": crontab(minute="3,13,23,33,43,53"),
    },
}
