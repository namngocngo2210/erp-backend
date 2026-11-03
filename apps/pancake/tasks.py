"""Celery task của Pancake — poll vào queue `foxia` (worker-foxia, I/O đối tác),
tạo lại đơn vào `foxia_push` (một lượt gọi `create_order_from_landing`, cùng
bản chất "đẩy" như trước — giữ tên queue).

`CELERY_TASK_TIME_LIMIT` toàn cục là 30 GIÂY — task poll bắt buộc override.
"""

from __future__ import annotations

import logging

from celery import shared_task

from apps.common.db_guards import single_flight
from apps.pancake.application.services.lead_order_service import create_order_for_lead
from apps.pancake.application.services.page_service import get_client
from apps.pancake.application.services.poll_service import PancakePollService
from apps.pancake.models import PancakeLead, PancakePage, PancakePushStatus
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)

POLL_LOCK_KEY = "lock:pancake_poll_phone_leads"
POLL_LOCK_TTL = 600  # > time_limit


@shared_task(
    name="apps.pancake.tasks.poll_phone_leads",
    soft_time_limit=150,
    time_limit=170,
)
def poll_phone_leads() -> str:
    """Poll mọi page đã bật. Một page lỗi không chặn page khác (service tự nuốt lỗi)."""
    if not sysconfig.get("PANCAKE_POLL_ENABLED"):
        return "disabled"
    with single_flight(POLL_LOCK_KEY, POLL_LOCK_TTL) as acquired:
        if not acquired:
            logger.info("Pancake poll: lượt trước chưa xong — bỏ qua lượt này")
            return "skipped"
        client = get_client()
        if client is None:
            logger.warning("Pancake poll: chưa có PancakeConnection active — bỏ qua")
            return "no_client"
        service = PancakePollService(client)
        # KHÔNG lọc theo page_access_token ở DB: cột mã hoá (EncryptedCharField) không
        # so sánh được; poll_page tự bỏ qua page thiếu token.
        pages = list(PancakePage.objects.filter(is_enabled=True, channel__isnull=False).select_related("channel"))
        leads = 0
        for page in pages:
            result = service.poll_page(page)
            leads += result.leads_created
        return f"ok:{len(pages)}:{leads}"


@shared_task(name="apps.pancake.tasks.retry_lead_order", soft_time_limit=25, time_limit=30, max_retries=0)
def retry_lead_order(lead_id: int) -> str:
    """Tạo lại đơn cho MỘT lead theo yêu cầu người vận hành. Chỉ nhận lead CHƯA có
    `order` và ở trạng thái pending/failed/skipped_no_mapping (lịch sử lead cũ,
    trước 17/09) hoặc order_failed (lần tạo đơn trước lỗi). `pending` xảy ra khi
    worker bị giết giữa lúc tạo lead và `create_order_for_lead` (spec §9) — không
    có nó thì lead đó kẹt vĩnh viễn, không ai tạo lại đơn được."""
    lead = PancakeLead.objects.filter(id=lead_id).select_related("page").first()
    if lead is None:
        return "missing"
    eligible = (
        PancakePushStatus.PENDING,
        PancakePushStatus.FAILED,
        PancakePushStatus.SKIPPED_NO_MAPPING,
        PancakePushStatus.ORDER_FAILED,
    )
    if lead.order_id is not None or lead.push_status not in eligible:
        return "not_eligible"
    lead = create_order_for_lead(lead)
    if lead.push_status == PancakePushStatus.ORDER_CREATED:
        return "created"
    if lead.push_status == PancakePushStatus.ORDER_EXISTS:
        return "exists"
    return "failed"
