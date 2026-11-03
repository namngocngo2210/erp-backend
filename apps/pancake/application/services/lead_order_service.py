"""Tạo đơn `orders` cho một lead Pancake — thay cho đẩy thẳng sang Foxia (từ
2026-09-17, xem docstring `apps.pancake.models`).

`create_order_for_lead` đi qua `OrderService.create_order_from_landing` (tầng
dưới, hợp lệ với `.importlinter`): đơn tạo ra tự khớp khách theo sđt, chạy
contact-flow, đẩy Lark/Foxia theo đúng đường đơn thường — pancake không tự làm
lại các việc đó.
"""

from __future__ import annotations

import logging
from datetime import datetime

from celery.exceptions import SoftTimeLimitExceeded
from django.db.models import Q
from django.utils import timezone

from apps.customers.models import Customer
from apps.common.phone_utils import phone_lookup_candidates
from apps.orders.application.services.lead_filter_service import (
    DuplicateOrderError,
    FilterAction,
    LeadFilterService,
)
from apps.orders.application.services.order_ingest_service import resolve_ingest_staff_id
from apps.orders.application.services.order_service_impl import OrderService
from apps.orders.models import Order
from apps.pancake import metrics
from apps.pancake.models import PancakeLead, PancakePushStatus, PancakeStaffMap

logger = logging.getLogger(__name__)

ERROR_LIMIT = 2000


def resolve_staff_id_for(assignee_name: str, now: datetime) -> int | None:
    """Tên nhân viên Pancake (`assignee_name`) → id user hệ thống, hoặc None.

    Rỗng → None, không đụng bảng map. Có tên → get_or_create dòng map (poll tự
    thêm tên mới gặp, người vận hành gán `user` sau qua UI/admin), luôn cập
    nhật `last_seen_at=now` để biết tên còn đang dùng. Chưa gán `user` (map
    mới hoặc chưa ai gán) → None (đơn rơi về bộ chia tự động). Có `user` →
    resolve_ingest_staff_id (cùng luật với ingest landing: active, chưa xoá
    mềm, không role hệ thống) — user không còn hợp lệ cũng trả None.
    """
    name = (assignee_name or "").strip()
    if not name:
        return None
    staff_map, _created = PancakeStaffMap.objects.get_or_create(pancake_name=name)
    staff_map.last_seen_at = now
    staff_map.save(update_fields=["last_seen_at"])
    if staff_map.user_id is None:
        return None
    return resolve_ingest_staff_id(staff_map.user.username)


def _existing_customer_name(phone: str) -> str:
    """Tên thật của khách đã có trong hệ thống theo sđt, hoặc "" nếu chưa có khách,
    khách chưa có tên, hay khách còn mang tên placeholder "Unknown" (`Customer` mới
    do `create_order_from_landing` tự đặt khi tên rỗng — không phải tên thật, không
    được coi là "đã có tên"). Cùng cách tra (`phone_lookup_candidates` +
    `deleted_at__isnull=True`) với `CustomerService.get_or_create_by_phone` để
    khớp đúng khách mà `create_order_from_landing` sẽ tìm/tạo lại bên trong."""
    customer = (
        Customer.objects.filter(phone_normalized__in=phone_lookup_candidates(phone), deleted_at__isnull=True)
        .first()
    )
    if customer is None or customer.full_name == "Unknown":
        return ""
    return customer.full_name


def find_existing_order_id(phone: str) -> int | None:
    """Đơn MỚI NHẤT từng có của số này (mọi kênh, mọi trạng thái, mọi thời điểm —
    kể cả đã giao/hoàn/huỷ; bỏ đơn đã xoá mềm), hoặc None.

    Chặt hơn cửa sổ trùng 24h cùng kênh của `LeadFilterService` (quyết định của
    người vận hành 24/09/2026): số từ Pancake đã từng có đơn thì KHÔNG tạo đơn nữa,
    khách cũ quay lại do sale chăm theo đơn cũ. Tra cả `phone` lẫn
    `phone_normalized` (đều có index) với các biến thể 0xxx/+84xxx."""
    lookup = phone_lookup_candidates(phone)
    if not lookup:
        return None
    return (
        Order.objects.filter(Q(phone__in=lookup) | Q(phone_normalized__in=lookup), deleted_at__isnull=True)
        .order_by("-created_at")
        .values_list("id", flat=True)
        .first()
    )


def _mark_order_exists(lead: PancakeLead, order_id: int, now: datetime) -> PancakeLead:
    lead.order_id = order_id
    lead.push_status = PancakePushStatus.ORDER_EXISTS
    lead.error = ""
    lead.pushed_at = now
    lead.save(update_fields=["order", "push_status", "error", "pushed_at"])
    metrics.pancake_leads_total.labels(push_status=lead.push_status).inc()
    # KHÔNG log tên/sđt: lead=<id> đủ để tra ngược trong DB.
    logger.info("Pancake tạo đơn: lead=%s đã có đơn order=%s", lead.id, lead.order_id)
    return lead


def build_order_payload(lead: PancakeLead, staff_id: int | None) -> dict:
    """Payload cho `create_order_from_landing` — đơn rỗng như landing, không gửi
    `product_id`/`raw_address`/tiền (Global Constraints). `staff_id` chỉ có mặt
    khi hợp lệ — thiếu key thì `create_order_from_landing` tự chia tự động.

    `name`: khách CŨ (đã có Customer theo sđt, tên thật khác "Unknown") giữ TÊN THẬT,
    không cho tên Pancake (thường là nickname Facebook như "Mẹ Bống") đè lên. Khách
    MỚI (chưa có Customer, hoặc Customer còn "Unknown") dùng tên Pancake cho trong
    hội thoại; tên Pancake rỗng thì rơi về SỐ ĐIỆN THOẠI làm tên — fallback này chỉ
    áp dụng cho khách MỚI, không bao giờ đè lên tên thật của khách cũ.

    `order_source`: snapshot `lead.foxia_kind` (nền tảng của page lúc bắt số —
    `messenger` hoặc `zalo`, xem `PancakeLead.foxia_kind`/`PLATFORM_TO_FOXIA_KIND`);
    rơi về `"messenger"` khi lead cũ thiếu snapshot (trước khi cột này có mặt)."""
    existing_name = _existing_customer_name(lead.phone)
    payload = {
        "name": existing_name or lead.customer_name or lead.phone,
        "phone": lead.phone,
        "channel_id": lead.channel_id,
        "order_source": lead.foxia_kind or "messenger",
        "url_page": f"https://pancake.vn/{lead.page.pancake_page_id}?c_id={lead.conversation_id}",
    }
    if staff_id is not None:
        payload["staff_id"] = staff_id
    return payload


def create_order_for_lead(lead: PancakeLead) -> PancakeLead:
    """Tạo đơn cho MỘT lead. Không raise — lỗi tạo đơn ghi vào chính lead.

    Bỏ qua (trả nguyên lead) khi: đã có `order` (không tạo lại), hoặc
    `push_status=ok` (lead cũ đã đẩy thẳng Foxia trước 17/09 — không tạo đơn
    bù cho lead cũ).

    Chặn TRƯỚC khi thử tạo đơn: số bị blocklist/không hợp lệ (`LeadFilterService.
    evaluate`) → `order_failed`, không chạm `create_order_from_landing`. Trùng đơn
    trong ngày (`DuplicateOrderError` từ `claim_or_reject`, chạy BÊN TRONG
    `create_order_from_landing`) là ca khác: không phải lỗi, lead gắn vào đơn đã có
    (`order_exists`) — không tạo lại đơn khi retry.

    Từ 24/09/2026: số đã TỪNG có đơn bất kỳ (`find_existing_order_id`) cũng gắn
    vào đơn mới nhất đó (`order_exists`), không tạo đơn. `DuplicateOrderError` vẫn
    giữ để chặn đua hai lead cùng số đi qua bước kiểm tra này cùng lúc.
    """
    if lead.order_id is not None or lead.push_status == PancakePushStatus.OK:
        return lead

    now = timezone.now()

    decision = LeadFilterService.evaluate(lead.phone, lead.channel)
    if decision.action in (FilterAction.REJECT_BLOCKED, FilterAction.REJECT_INVALID_PHONE):
        lead.push_status = PancakePushStatus.ORDER_FAILED
        lead.error = decision.reason
        lead.pushed_at = now
        lead.save(update_fields=["push_status", "error", "pushed_at"])
        metrics.pancake_leads_total.labels(push_status=lead.push_status).inc()
        logger.warning("Pancake tạo đơn bị chặn: lead=%s action=%s", lead.id, decision.action.value)
        return lead

    existing_order_id = find_existing_order_id(lead.phone)
    if existing_order_id is not None:
        return _mark_order_exists(lead, existing_order_id, now)

    try:
        staff_id = resolve_staff_id_for(lead.assignee_name, now)
        payload = build_order_payload(lead, staff_id)
        order = OrderService.create_order_from_landing(payload, created_via="pancake")
    except DuplicateOrderError as exc:
        return _mark_order_exists(lead, exc.decision.existing_order_id, now)
    except SoftTimeLimitExceeded:
        raise  # timeout của task, không phải lỗi nghiệp vụ của lead — không đánh order_failed
    except Exception as exc:  # noqa: BLE001 — lỗi tạo đơn của một lead không được chặn poll
        lead.push_status = PancakePushStatus.ORDER_FAILED
        lead.error = f"{type(exc).__name__}: {exc}"[:ERROR_LIMIT]
        lead.pushed_at = now
        lead.save(update_fields=["push_status", "error", "pushed_at"])
        metrics.pancake_leads_total.labels(push_status=lead.push_status).inc()
        # KHÔNG log tên/sđt; chỉ log LOẠI lỗi — thông điệp đầy đủ đã nằm trong lead.error.
        logger.warning("Pancake tạo đơn lỗi: lead=%s (%s)", lead.id, type(exc).__name__)
        return lead

    lead.order = order
    lead.push_status = PancakePushStatus.ORDER_CREATED
    lead.error = ""
    lead.pushed_at = now
    lead.save(update_fields=["order", "push_status", "error", "pushed_at"])
    metrics.pancake_leads_total.labels(push_status=lead.push_status).inc()
    logger.info("Pancake tạo đơn: lead=%s order=%s staff_id=%s", lead.id, order.id, staff_id)
    return lead
