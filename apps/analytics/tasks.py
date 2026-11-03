"""Facade cho Celery autodiscover.

`app.autodiscover_tasks()` chỉ import `apps.<app>.tasks` ở gốc app, không quét
module lồng. Thiếu file này thì task không đăng ký và job kẹt PENDING.
"""

from .infrastructure.tasks import *  # noqa: F401,F403
