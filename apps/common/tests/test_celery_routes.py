"""Bảo vệ định tuyến queue Celery (audit 2026-07-26 mục T5).

Định tuyến queue là quyết định VẬN HÀNH, không phải chi tiết cấu hình: task nào
nằm chung queue nào quyết định task nào phải chờ task nào. Trước audit 2026-07-26,
19 task không có route nên rơi hết vào queue ``default`` — nơi worker prod chỉ có
4 slot. Ba job callcenter gọi Gemini (soft_time_limit 300–480s) cộng một job
precompute báo cáo (time_limit 900s) là đủ chiếm trọn 4 slot, đẩy các sweep điều
phối lead — vốn chỉ chạy vài giây nhưng phải đúng giờ — ra sau hàng chờ.

Các test dưới đây chốt lại quyết định đó bằng code để nó không âm thầm trôi ngược.
"""

from django.conf import settings

# 5 task dài/nặng đã tách sang queue riêng, do worker-slow tiêu thụ.
SLOW_TASKS = [
    "apps.callcenter.tasks.sync_zenify_call_histories",
    "apps.callcenter.tasks.transcribe_pending_call_records",
    "apps.callcenter.tasks.extract_pending_call_insights",
    "apps.customers.tasks.rebuild_segment_snapshot",
]

# Danh sách task beat CỐ Ý để ở queue ``default``: chạy ngắn (giây), không gọi
# LLM, không precompute báo cáo — nên chúng chia slot với nhau vô hại.
#
# ĐÂY LÀ TÀI LIỆU SỐNG, KHÔNG PHẢI DANH SÁCH CHO QUA. Thêm một task beat mới mà
# không khai route sẽ làm test bên dưới ĐỎ, buộc người thêm phải trả lời câu hỏi
# "task này chạy bao lâu, có được phép chiếm slot của sweep điều phối lead
# không?" rồi mới chọn: route sang queue phù hợp, hoặc thêm tên vào đây.
# Xoá test để CI xanh nhanh = vứt bỏ đúng cái chốt chặn đã ngăn sự cố 2026-07-26.
KNOWN_DEFAULT_QUEUE_TASKS = frozenset(
    {
        # Retention / purge — batch nhỏ, chạy đêm, không đụng giờ cao điểm.
        "apps.hr.tasks.purge_old_attendance_photos",
        "apps.inventory.tasks.purge_matched_pending_scans",
        "apps.best_express.tasks.purge_old_best_webhook_events",
        "apps.audit.tasks.purge_old_audit_logs",
        "apps.orders.tasks.purge_old_ingest_traces",
        "apps.orders.tasks.purge_old_journey_events",
        # Sweep điều phối / đối soát — ngắn nhưng cần đúng giờ, chính là nhóm
        # được hưởng lợi khi 5 task ở SLOW_TASKS rời khỏi queue default.
        "apps.orders.tasks.sweep_floating_pool",
        "apps.orders.tasks.sweep_lead_assignment",
        "apps.orders.tasks.close_converted_leads",
        "apps.contact_flow.tasks.sweep_operation_entry",
        "apps.inventory.tasks.match_pending_shipment_scans",
        "apps.inventory.tasks.sweep_reissue_ledger",
        "apps.inventory.tasks.audit_revived_orders_stock",
        "apps.inventory.tasks.ensure_stock_movements_partition",
        # Một query đếm + một tin Lark, chạy 1 lần/ngày lúc 08:30 — vài giây.
        "apps.inventory.tasks.alert_awaiting_return_overdue",
        "apps.best_express.tasks.retry_unprocessed_best_webhook_events",
        # Cặp tương ứng bên CNV (spec 2026-08-19): chỉ đọc/ghi DB, không gọi ra
        # ngoài — cùng nhóm với best_express ở trên.
        "apps.cnv_loyalty.tasks.retry_unprocessed_webhook_events",
        "apps.cnv_loyalty.tasks.purge_old_webhook_events",
        # Webhook Pancake POS (two-way-sync B1): vớt sự kiện đơn + khớp lại + dọn — chỉ DB.
        "apps.pancake_pos.tasks.process_webhook_events",
        # Báo đơn treo vận chuyển Pancake (MR D): chỉ DB + Lark, mỗi giờ.
        "apps.pancake_pos.tasks.alert_stuck_deliveries",
        # Retention log quét portal bảo hành. Thiếu dòng này từ lúc task ra đời
        # (MR portal bảo hành) nên test đỏ sẵn trên master — cùng nhóm retention
        # với `purge_old_best_webhook_events`, giữ nguyên queue default.
        "apps.warranty_codes.tasks.purge_old_scan_logs",
        # Hạ tầng / tích hợp nhẹ.
        "apps.common.tasks.ops_heartbeat",
        "apps.warranty.tasks.sync_best_tracking_statuses",
    }
)


def test_slow_tasks_routed_off_default_queue():
    """5 task dài/nặng phải nằm ở queue ``slow``, không phải ``default``."""
    for name in SLOW_TASKS:
        assert name in settings.CELERY_TASK_ROUTES, f"task {name} mất route sang queue slow"
        assert settings.CELERY_TASK_ROUTES[name]["queue"] == settings.CELERY_SLOW_QUEUE
    assert settings.CELERY_SLOW_QUEUE != settings.CELERY_TASK_DEFAULT_QUEUE


def test_slow_queue_is_separate_from_other_queues():
    """Queue ``slow`` phải là queue riêng — trùng tên queue khác là mất tác dụng cách ly."""
    others = {
        settings.CELERY_TASK_DEFAULT_QUEUE,
        settings.CELERY_KIOTVIET_QUEUE,
        settings.CELERY_ORDERS_QUEUE,
        settings.CELERY_LARK_NOTI_QUEUE,
    }
    assert settings.CELERY_SLOW_QUEUE not in others


def test_every_beat_task_has_explicit_route_or_is_known_default():
    """Mọi task beat phải hoặc có route rõ ràng, hoặc nằm trong danh sách cố ý ở default.

    Chốt chặn chống hồi quy dài hạn: task beat mới không khai queue sẽ lặng lẽ rơi
    vào ``default`` và cạnh tranh slot với các sweep điều phối lead — đúng cơ chế
    đã gây chậm ở audit 2026-07-26. Test này biến "quên quyết định queue" thành
    một CI đỏ có tên task cụ thể trong thông báo lỗi.
    """
    routed = set(settings.CELERY_TASK_ROUTES)
    beat_tasks = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
    unrouted = beat_tasks - routed
    assert unrouted <= KNOWN_DEFAULT_QUEUE_TASKS, (
        f"task beat mới chưa quyết định queue: {unrouted - KNOWN_DEFAULT_QUEUE_TASKS}"
    )


def test_known_default_list_has_no_stale_entries():
    """Danh sách 'cố ý để ở default' không được chứa tên đã chết hoặc đã có route.

    Không có test này, KNOWN_DEFAULT_QUEUE_TASKS sẽ phình ra thành bãi rác và mất
    hết giá trị tài liệu.
    """
    beat_tasks = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
    routed = set(settings.CELERY_TASK_ROUTES)
    stale = {name for name in KNOWN_DEFAULT_QUEUE_TASKS if name not in beat_tasks or name in routed}
    assert not stale, f"KNOWN_DEFAULT_QUEUE_TASKS còn tên thừa (đã có route hoặc không còn trong beat): {stale}"


def test_routed_and_beat_tasks_are_actually_registered():
    """Mọi task có route hoặc có mặt trong beat schedule phải ĐĂNG KÝ THẬT trong Celery.

    Sự cố 2026-08-08: ``CELERY_TASK_ROUTES`` trỏ
    ``apps.exports.infrastructure.tasks.run_export`` sang queue ``exports``, nhưng
    module đó nằm lồng sâu trong app ``exports`` nên ``app.autodiscover_tasks()``
    (chỉ import ``<app>.tasks`` ở GỐC mỗi app) không bao giờ import nó — task
    không đăng ký. Route/beat schedule vẫn "đúng cú pháp", nên
    ``test_every_beat_task_has_explicit_route_or_is_known_default`` ở trên vẫn
    xanh trong khi worker prod vứt message với "Received unregistered task".

    Test này import Celery app THẬT và đối chiếu tên task trong route + beat
    schedule với registry thật (``celery_app.tasks``) — bắt đúng lớp lỗi "route
    trỏ tới tên task không tồn tại".

    ``app.autodiscover_tasks()`` (gọi trong ``config/celery.py``) KHÔNG import gì
    ngay lập tức — nó chỉ nối một signal handler (``on_after_finalize`` /
    ``import_modules``) sẽ chạy khi có worker thật khởi động. Vì vậy chỉ
    ``django.setup()`` + đọc ``celery_app.tasks`` là không đủ: registry vẫn trống
    (đã kiểm chứng thủ công — kể cả gọi ``app.finalize()`` cũng KHÔNG đủ, registry
    vẫn trống). Phải gọi ``app.loader.import_default_modules()`` — đúng bước
    ``WorkController`` gọi lúc worker thật boot — để các module ``tasks.py`` được
    import và ``@shared_task`` đăng ký thật.
    """
    from config.celery import app as celery_app

    celery_app.loader.import_default_modules()
    registered = set(celery_app.tasks)
    routed_tasks = set(settings.CELERY_TASK_ROUTES)
    beat_tasks = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}

    unregistered = (routed_tasks | beat_tasks) - registered
    assert not unregistered, (
        f"task có route/beat schedule nhưng KHÔNG đăng ký trong Celery registry "
        f"(kiểm tra facade tasks.py ở gốc app — autodiscover_tasks không quét "
        f"module lồng sâu): {unregistered}"
    )


def test_foxia_push_co_queue_rieng_khong_dung_chung_voi_backfill():
    """Đẩy đơn là việc NGẮN — không được xếp sau một lượt backfill 15 phút.

    `apps.foxia.tasks.sync_*` chạy với time_limit=900 trên queue `foxia`. Nếu
    push dùng chung queue đó thì mỗi đơn landing có thể nằm chờ hết một lượt
    đồng bộ danh mục mới được gửi đi.
    """
    name = "apps.foxia.tasks.push_order_to_foxia"
    assert name in settings.CELERY_TASK_ROUTES, f"task {name} chưa khai queue"
    assert settings.CELERY_TASK_ROUTES[name]["queue"] == settings.CELERY_FOXIA_PUSH_QUEUE
    assert settings.CELERY_FOXIA_PUSH_QUEUE != settings.CELERY_FOXIA_QUEUE
    assert settings.CELERY_FOXIA_PUSH_QUEUE != settings.CELERY_TASK_DEFAULT_QUEUE
