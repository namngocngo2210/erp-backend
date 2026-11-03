"""Điểm phát duy nhất cho order event ra WebSocket (route theo kênh, xem
``publish_order_event`` và ``apps/channels_core/groups.py``).

Sự cố 2026-07-29 (``FATAL: sorry, too many clients already``): ``CRMConsumer``
phân quyền từng event bằng cách tra DB — ``_order_belongs_to_user`` cho scope
``own`` và ``_resolve_order_channel_code`` cho scope ``channels``. Consumer chạy
trên MỌI client trong group, nên một event sinh ra N query đồng thời với N là số
tab đang mở. Với ``CONN_MAX_AGE=0`` ở prod, mỗi query là một connection mới:

    1 event  →  N query đồng thời  →  N connection

Bulk tạo vận đơn KShip phát tới 50 event một lượt, nhân với vài chục tab là cạn
``max_connections=100`` trong tích tắc.

Cách chữa: payload phải TỰ CHỨA đủ dữ liệu phân quyền (``staff_id`` +
``channel_code``) để consumer quyết định mà không chạm DB. Việc bổ sung đặt ở đây —
điểm hội tụ của mọi luồng phát — thay vì rải ra 18 call site, để call site mới
trong tương lai tự động được bảo vệ.

Chi phí của enrichment là ĐÚNG MỘT query, và nó chạy trên connection sẵn có của
thread gọi (code sync Django tái dùng connection hiện tại) nên KHÔNG mở connection
mới. Đây là khác biệt cốt lõi so với query phía consumer: mỗi
``database_sync_to_async`` ở đó chạy trong thread riêng của Channels và mở
connection riêng.

Xem docs/ws-broadcast-db-fanout-2026-07-29.md.
"""

from __future__ import annotations

import logging

from uuid import uuid4

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from apps.orders.application.ports import present_order_row

# channels_core nằm DƯỚI orders theo .importlinter — import xuống là đúng chiều
# phân tầng (khác consumers.py phải ghép literal tên group export bằng tay vì
# apps.exports nằm TRÊN channels_core).
from apps.channels_core.groups import CRM_GROUP, order_channel_group
from apps.orders.contracts import OrderEventContract
from apps.users.phone_visibility import PhoneVisibility

logger = logging.getLogger(__name__)

# Bản sao "diện rộng" của event CÓ kênh — cho consumer scope all/own và consumer
# CŨ chưa join group kênh (deploy web/worker lệch nhịp, xem lộ trình trong
# apps/channels_core/groups.py):
# - GIAI ĐOẠN 1 (hiện tại) = CRM_GROUP: consumer cũ chỉ ở group chung nên bản sao
#   phải vào đó, consumer mới khử trùng lặp bằng dedup_id.
# - GIAI ĐOẠN 2: bật env WS_CHANNEL_FANOUT_PHASE=2 + restart worker SAU KHI mọi web đã chạy
#   consumer join CRM_BROAD_GROUP — từ đó sale scope "channels" hết nhận traffic
#   kênh ngoài phạm vi. Đổi sớm khi còn consumer cũ là client đó MẤT event.
# Env-gated (WS_CHANNEL_FANOUT_PHASE, mặc định 1) để giai đoạn 2 là việc BẬT ENV
# + restart worker sau khi xác nhận mọi web đã chạy consumer mới — không phải một
# lần deploy code nữa (merge an toàn bất kể trạng thái rollout).
def _broad_copy_group() -> str:
    from django.conf import settings

    if int(getattr(settings, "WS_CHANNEL_FANOUT_PHASE", 1)) >= 2:
        from apps.channels_core.groups import CRM_BROAD_GROUP

        return CRM_BROAD_GROUP
    return CRM_GROUP


def enrich_order_event(payload: dict) -> dict:
    """Bổ sung ``staff_id``/``channel_code`` còn thiếu bằng đúng một query.

    Payload đã đủ hai field thì trả về nguyên trạng, không chạm DB. Không tra được
    đơn (đã xoá, hoặc order_id sai) thì trả nguyên trạng — consumer còn lớp fallback
    riêng, không được để việc làm giàu payload làm hỏng luồng phát event.
    """
    if payload.get("staff_id") and payload.get("channel_code"):
        return payload

    order_id = payload.get("order_id")
    if not isinstance(order_id, int):
        return payload

    from apps.orders.models import Order

    try:
        row = (
            Order.objects.filter(pk=order_id)
            .values_list("staff_id", "channel__code")
            .first()
        )
    except Exception:
        # Best-effort: hỏng ở đây thì client vẫn nhận event (consumer tự fallback),
        # còn hơn nuốt mất cập nhật realtime.
        logger.exception("Không làm giàu được order event order_id=%s", order_id)
        return payload

    if not row:
        return payload

    staff_id, channel_code = row
    enriched = dict(payload)
    if not enriched.get("staff_id"):
        # 0 = "đã tra DB, đơn này chưa giao cho ai" — KHÁC với vắng mặt field, vốn
        # nghĩa là "chưa biết" và khiến consumer phải fallback query. Không có sentinel
        # thì mọi đơn chưa có sale phụ trách sẽ fallback mỗi lần phát event, đúng cái
        # bản vá này muốn xoá. Consumer so `staff_id == user_id` nên 0 không khớp ai.
        enriched["staff_id"] = staff_id or 0
    if not enriched.get("channel_code") and channel_code:
        enriched["channel_code"] = channel_code
    return enriched


def load_event_meta(order_ids) -> dict[int, dict]:
    """Nạp ``staff_id`` + ``channel_code`` cho MỘT LÔ đơn bằng đúng một query.

    Dành cho các luồng bulk (queue KiotViet, tạo vận đơn KShip...) chỉ cầm trong tay
    danh sách ``order_id``. Không có hàm này thì mỗi event phải tự tra DB ở
    ``enrich_order_event`` — 50 đơn thành 50 query tuần tự.
    """
    ids = list(order_ids)
    if not ids:
        return {}

    from apps.orders.models import Order

    return {
        row["id"]: row
        for row in Order.objects.filter(id__in=ids).values(
            "id", "staff_id", "channel__code"
        )
    }


# Row realtime là payload NỘI BỘ giữa publisher và consumer, không phải response gửi
# thẳng cho một user — nên serialize với SĐT thật rồi để consumer che theo từng
# connection. `OrderSerializer` mặc định fail-closed (thiếu context = che hết), nên
# phải nói rõ ý định ở đây thay vì để im lặng rơi vào nhánh mặc định.
_UNMASKED_PHONE = PhoneVisibility(user_id=None, can_view_full=True)


def build_order_rows_context(orders) -> dict:
    """Context batch cho ``build_order_created_row`` khi phát row cho CẢ MỘT LÔ đơn.

    Không có context này thì mỗi row tự dựng phần dùng chung: ``build_duplicate_map``
    1-phần-tử (thiết kế cho cả trang) + 1 query reminder per-row trong
    ``OrderCRMSerializer.get_next_reminder_at`` — lô transfer 50 đơn thành ~400 query
    (audit performance 2026-08-19, P0.1). Ở đây mỗi map dựng đúng MỘT lần cho cả lô;
    giá trị từng đơn giống hệt bản 1-phần-tử (cửa sổ trùng tính TƯƠNG ĐỐI giữa hai
    đơn, không phụ thuộc lô — xem ``order_duplicate_warning_service``).

    Caller phải nạp orders bằng queryset đã select_related/prefetch_related đủ các
    quan hệ serializer đọc (customer, staff, channel, kiotviet_channel, assessment
    (+updated_by, needs__need_type__group), recall, recall_entries, items) — context
    này chỉ gánh phần map dùng chung, không cứu được lazy-load quan hệ per-row.
    """
    from apps.orders.application.services.order_duplicate_warning_service import (
        build_duplicate_map,
    )
    from apps.orders.application.services.sale_reminder_service import (
        get_next_reminder_at_map,
    )

    orders = list(orders)
    return {
        "skip_journey_events": True,
        "duplicate_map": build_duplicate_map(orders),
        "next_reminder_map": get_next_reminder_at_map(order.id for order in orders),
        # Row realtime serialize MỘT lần cho MỌI client nên không thể che theo người
        # xem ở đây — giữ SĐT thật, consumer WS che per-connection trước khi gửi
        # (cùng khuôn field_policy, xem apps/channels_core/consumers.py).
        "phone_visibility": _UNMASKED_PHONE,
    }


def _reload_for_row(order):
    """Order + quan hệ OrderListSerializer đọc, cùng loader với trang list
    (`OrderRepository._with_related_crm`) — 1 SELECT + 3 prefetch."""
    from apps.orders.models import Order
    from apps.orders.repositories import OrderRepository

    try:
        loaded = OrderRepository._with_related_crm(
            Order.objects.filter(pk=order.pk),
            with_journey_events=False,
            # KHÔNG lean: `get_crm` đường 1 đơn dùng OrderCRMSerializer đầy đủ (không
            # có crm_map) → đụng cột bị defer là Django query lại per-field.
            lean_list_columns=False,
        ).first()
    except Exception:
        logger.exception("Không nạp lại được order %s cho row realtime", getattr(order, "id", None))
        return order
    return loaded if loaded is not None else order


def build_order_created_row(order, context: dict | None = None) -> dict | None:
    """Serialize row bản LIST cho payload ``order.created`` — MỘT lần tại điểm phát.

    Thay cho N tab × GET /api/orders/{id} sau mỗi đơn ingest (bão gate 2026-08-04,
    xem docs/order-list-fanout-hardening-2026-08-04.md). ``can_edit`` bị pop vì
    per-user — FE tự suy từ row cùng kênh đang có; field ẩn theo field_policy do
    consumer strip per-connection trước khi gửi. Best-effort: lỗi serialize trả
    None (FE rơi về đường fetch cũ), không được giết luồng tạo đơn.

    ``context``: truyền kết quả ``build_order_rows_context`` khi phát cho cả lô
    (transfer Landing → Đơn hàng) để phần map dùng chung chỉ dựng một lần. Bỏ trống
    (call site 1 đơn: ingest landing, duplicate, gán sale) thì tự dựng như cũ.
    """
    try:
        if context is None:
            # Đường 1 đơn (ingest landing, tạo tay, duplicate, gán sale): nạp lại
            # đơn với đúng bộ select_related/prefetch của trang list rồi dựng
            # context lô-1 — cảnh báo trùng + reminder map + phone_visibility y
            # như đường lô. Trước đây serializer lazy-load channel/customer/
            # assessment/needs/recall/recall_entries/items + 1 query reminder →
            # 9 query/event (audit 2026-09-23 perf §2.9); giờ 1 SELECT + 3 prefetch
            # + 2 map. Nạp lại thất bại (đơn vừa bị xoá?) thì dùng instance đã có.
            order = _reload_for_row(order)
            context = build_order_rows_context([order])
        # Qua port (transport cắm OrderListSerializer lúc ready()) — application
        # không import serializer transport (audit 2026-09-23 T3/P2-2).
        row = present_order_row(order, context)
        row.pop("can_edit", None)
        return row
    except Exception:
        logger.exception(
            "Không build được row cho order.created order_id=%s", getattr(order, "id", None)
        )
        return None


def publish_order_event(payload: dict) -> None:
    """Validate → làm giàu → route theo kênh.

    Event CÓ ``channel_code`` (sau enrich — tuyệt đại đa số) đi HAI group:
    ``crm_updates.ch.<code>`` (đích cuối cùng — chỉ client thấy kênh đó join) và
    ``_broad_copy_group()`` (scope all/own + consumer cũ). Hai bản mang cùng
    ``dedup_id`` ở TẦNG MESSAGE (không đụng payload — consumer cũ chỉ đọc
    ``event["payload"]`` nên bỏ qua key lạ), consumer mới chỉ xử lý bản tới trước.

    Event KHÔNG có channel_code (enrich thất bại / đơn đã xoá) → một bản duy nhất
    vào group chung như cũ, consumer phân quyền qua đường fallback.

    ``_is_authorized`` phía consumer GIỮ NGUYÊN (defense in depth): group kênh chỉ
    thu hẹp fanout, quyền vẫn quyết theo channel_code gốc trong payload.
    """
    payload = OrderEventContract.validate_payload(payload).to_payload()
    payload = enrich_order_event(payload)

    channel_layer = get_channel_layer()
    if not channel_layer:
        return

    channel_code = payload.get("channel_code")
    if not channel_code:
        async_to_sync(channel_layer.group_send)(
            CRM_GROUP, {"type": "crm.event", "payload": payload}
        )
        return

    message = {"type": "crm.event", "payload": payload, "dedup_id": uuid4().hex}
    async_to_sync(channel_layer.group_send)(order_channel_group(channel_code), message)
    async_to_sync(channel_layer.group_send)(_broad_copy_group(), message)
