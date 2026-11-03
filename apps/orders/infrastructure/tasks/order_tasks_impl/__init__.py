"""Facade package: re-export tường minh các Celery task.

Import package này (qua ``apps/orders/tasks.py``) là cách autodiscovery của
Celery nạp và đăng ký toàn bộ task — các dòng import dưới đây vì thế còn có
side effect đăng ký task, không chỉ re-export tên.
"""

from .ingest_job_tasks import (  # noqa: F401
    process_kiotviet_job,
    process_landing_ingest,
)
from .auto_sync_invoice_full import process_auto_sync_invoice_full  # noqa: F401
from .lark_notification_tasks import (  # noqa: F401
    notify_lark_cancelled_after_dispatch,
    notify_lark_order_created,
    notify_lark_vat_invoice_required,
)
from .restore_tasks import process_restore_orders  # noqa: F401
from .sale_reminder_tasks import (  # noqa: F401
    NO_ANSWER_CODES,
    process_no_answer_reminders,
    process_sale_reminders,
)
from .retention_tasks import (  # noqa: F401
    purge_old_ingest_traces,
    purge_old_journey_events,
)
from .floating_pool_tasks import sweep_floating_pool  # noqa: F401
from .lead_assignment_tasks import sweep_lead_assignment  # noqa: F401
from .lead_close_tasks import close_converted_leads, logger  # noqa: F401
