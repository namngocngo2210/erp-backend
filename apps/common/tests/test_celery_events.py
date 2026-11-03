"""Worker phải phát task events, nếu không toàn bộ nhóm alert Celery là giấy.

Sự cố 2026-09-22: ``AiMartSyncSilent`` / ``AiMartRebuildSilent`` /
``AiChatExtractSilent`` nổ liên tục dù beat + worker-slow chạy đúng nhịp. Nguyên
nhân: celery-exporter (danihodovic) chỉ đếm ``celery_task_*_total`` từ các event
``task-succeeded`` / ``task-failed`` mà worker phát ra — và ``worker_send_task_events``
mặc định ``False``, chưa từng được bật. Exporter chỉ thấy heartbeat nên
``absent(celery_task_succeeded_total{...})`` luôn đúng; ``CeleryTaskFailing``
thì ngược lại KHÔNG BAO GIỜ nổ.

Test này chốt cài đặt đó ở tầng app Celery (sau ``config_from_object``), đúng
chỗ worker đọc, để nó không âm thầm rơi khi tách/đổi file settings.
"""

from config.celery import app


def test_worker_send_task_events_enabled():
    assert app.conf.worker_send_task_events is True
