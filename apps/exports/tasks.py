"""Facade module to preserve public imports while delegating to layered implementation.

Vì sao file này tồn tại (đừng xoá): ``config/celery.py`` gọi
``app.autodiscover_tasks()`` — ở chế độ Django, hàm này chỉ import
``<app_config.name>.tasks`` cho MỖI app trong INSTALLED_APPS, tức đúng một
module ``apps.exports.tasks`` ở GỐC app. Nó không quét xuống các module lồng
sâu như ``apps/exports/infrastructure/tasks.py``.

Sự cố 2026-08-08: task thật (``run_export``, ``purge_expired_exports``) chỉ
nằm ở ``apps/exports/infrastructure/tasks.py`` nên chưa từng được import khi
worker khởi động → không đăng ký vào Celery registry. Message vẫn tới đúng
queue ``exports``, worker vẫn nhận, nhưng bị vứt với lỗi "Received
unregistered task of type 'apps.exports.infrastructure.tasks.run_export'" —
job Xuất Excel của người dùng kẹt PENDING vĩnh viễn. Tiền lệ xử lý giống hệt
đã có sẵn ở ``apps/orders/tasks.py`` (facade cho ``infrastructure/tasks/``).

``run_export`` không khai ``name=`` trong ``@shared_task`` nên tên đăng ký
suy ra từ ``__module__`` thực tế của hàm — vẫn là
``apps.exports.infrastructure.tasks.run_export`` dù import lại qua đây, nên
khớp nguyên với ``CELERY_TASK_ROUTES``. Import kiểu ``*`` để không phải liệt
kê tay và tự động theo khi thêm task mới trong module gốc.
"""

from .infrastructure.tasks import *  # noqa: F401,F403
