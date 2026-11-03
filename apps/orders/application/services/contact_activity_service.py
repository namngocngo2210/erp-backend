"""ContactActivityService — timeline liên hệ + ghi chú/log gọi (trang Danh sách contact).

Timeline = UNION 2 nguồn, không có bảng materialize riêng:
- `OrderJourneyEvent` (event_type in assessment_created/sales_status_changed) —
  đã ghi sẵn tại choke point `_upsert_order_assessment`, KHÔNG ghi lại ở đây.
- `ContactActivity` (kind call/note) — append-only, ghi qua `create_activity`.

Xem docs/sale-contacts-page-design.md §3.2/§3.3. Layering: application service
thuần — không có mối quan tâm HTTP ở đây (exception phẳng, view tự map status).
"""
from __future__ import annotations

import json

from apps.customers.models import ContactActivity, ContactActivityKind, Customer
from apps.orders.crm_catalog import SALES_STATUS_LABELS
from apps.orders.models import MARKETPLACE_ORDER_SOURCES, Order, OrderJourneyEvent

# Cùng danh sách loại nguồn với ContactListService (đơn sàn TMĐT + koc không thuộc
# luồng sale landing) — duplicate có chủ đích, không cross-import private constant.
_EXCLUDED_ORDER_SOURCES = [*MARKETPLACE_ORDER_SOURCES, "koc"]

_JOURNEY_ACTIVITY_EVENT_TYPES = ["assessment_created", "sales_status_changed"]
_ALLOWED_CREATE_KINDS = {ContactActivityKind.NOTE, ContactActivityKind.CALL}
_NOTE_MAX_LEN = 4000
_CALL_META_MAX_LEN = 4000


class ContactActivityValidationError(Exception):
    """Lỗi validate payload POST activity — view map 400."""


class ContactActivityService:
    @staticmethod
    def user_owns_customer(customer_id: int, user) -> bool:
        """True nếu user phụ trách (staff_id) ít nhất 1 đơn landing của khách này.

        Dùng cho scope OWN — mirror cách ContactListService loại đơn sàn/koc.
        """
        return (
            Order.objects.filter(
                customer_id=customer_id, staff_id=getattr(user, "id", None), deleted_at__isnull=True
            )
            .exclude(order_source__in=_EXCLUDED_ORDER_SOURCES)
            .exists()
        )

    @staticmethod
    def customer_in_channels(customer_id: int, channel_codes) -> bool:
        """True nếu khách có ít nhất 1 đơn landing thuộc các kênh được cấp.

        Dùng cho scope CHANNEL/DEPARTMENT — mirror `_apply_contacts_scope` của danh
        sách (audit 2026-07-15b: trước đây chỉ OWN được kiểm, user scope CHANNEL
        đọc/ghi được timeline của MỌI khách).
        """
        if not channel_codes:
            return False
        return (
            Order.objects.filter(
                customer_id=customer_id,
                deleted_at__isnull=True,
                channel__code__in=channel_codes,
            )
            .exclude(order_source__in=_EXCLUDED_ORDER_SOURCES)
            .exists()
        )

    @staticmethod
    def list_activities(customer: Customer, *, limit: int, offset: int) -> dict:
        """→ {"items": [dict item], "total": int}. Sort `at` DESC, merge Python.

        Mỗi nguồn được fetch thô limit `offset + limit` phần tử (đã sort DESC ở DB),
        đủ để merge-sort đúng thứ tự trang hiện tại mà không cần load toàn bộ lịch sử.
        """
        fetch_n = offset + limit

        # Lần chốt Kết quả ĐẦU TIÊN của một đơn ghi ĐỒNG THỜI 2 sự kiện cùng
        # (order, to_code, thời điểm): `assessment_created` (tạo assessment) +
        # `sales_status_changed` (đổi trạng thái). Cả 2 render y hệt "— → Đã chốt"
        # → trùng lặp gây khó chịu. Dedupe: bỏ `assessment_created` khi đã có
        # `sales_status_changed` cùng (order_id, to_code). Lấy toàn bộ key 1 query
        # để dedupe NHẤT QUÁN cả trang lẫn `total` (không lệ thuộc trang fetch).
        all_journey_keys = list(
            OrderJourneyEvent.objects.filter(
                order__customer_id=customer.id, event_type__in=_JOURNEY_ACTIVITY_EVENT_TYPES
            )
            .exclude(order__order_source__in=_EXCLUDED_ORDER_SOURCES)
            .values_list("order_id", "event_type", "to_code")
        )
        sales_changed_keys = {
            (oid, code) for oid, etype, code in all_journey_keys if etype == "sales_status_changed"
        }

        def _is_redundant_assessment(order_id, event_type, to_code) -> bool:
            return event_type == "assessment_created" and (order_id, to_code) in sales_changed_keys

        redundant_count = sum(1 for key in all_journey_keys if _is_redundant_assessment(*key))
        journey_total = len(all_journey_keys) - redundant_count

        journey_events = [
            e
            for e in OrderJourneyEvent.objects.filter(
                order__customer_id=customer.id, event_type__in=_JOURNEY_ACTIVITY_EVENT_TYPES
            )
            .exclude(order__order_source__in=_EXCLUDED_ORDER_SOURCES)
            .select_related("actor")
            .order_by("-created_at", "-id")[:fetch_n]
            if not _is_redundant_assessment(e.order_id, e.event_type, e.to_code)
        ]
        activities = list(
            ContactActivity.objects.filter(customer_id=customer.id)
            .select_related("staff")
            .order_by("-created_at", "-id")[:fetch_n]
        )

        merged = [_journey_event_to_item(e) for e in journey_events] + [
            _activity_to_item(a) for a in activities
        ]
        # Tiebreaker (type, order_id) cho các item trùng `at` tuyệt đối — tránh
        # thứ tự không xác định (skip/dupe tại biên trang) khi 2 nguồn merge.
        merged.sort(key=lambda item: (item["at"], item["type"], item["order_id"] or 0), reverse=True)
        page = merged[offset : offset + limit]

        total = journey_total + ContactActivity.objects.filter(customer_id=customer.id).count()
        return {"items": page, "total": total}

    @staticmethod
    def create_activity(customer: Customer, actor, payload: dict) -> dict:
        """Validate + tạo 1 `ContactActivity` (kind note/call). Raises
        `ContactActivityValidationError` với message tiếng Việt khi payload sai."""
        kind = payload.get("kind")
        if kind not in _ALLOWED_CREATE_KINDS:
            raise ContactActivityValidationError("kind phải là 'note' hoặc 'call'.")

        note = str(payload.get("note") or "").strip()
        if len(note) > _NOTE_MAX_LEN:
            raise ContactActivityValidationError(f"note tối đa {_NOTE_MAX_LEN} ký tự.")
        if kind == ContactActivityKind.NOTE and not note:
            raise ContactActivityValidationError("note bắt buộc khi kind=note.")

        order = None
        order_id = payload.get("order_id")
        if order_id is not None:
            order = Order.objects.filter(pk=order_id, customer_id=customer.id).first()
            if order is None:
                raise ContactActivityValidationError("order_id không thuộc khách hàng này.")

        call_meta = payload.get("call_meta") if kind == ContactActivityKind.CALL else None
        if call_meta is not None:
            # Log append-only không có đường sửa/xoá — không cap thì client bơm blob
            # JSON tuỳ ý làm phình bảng (audit 2026-07-15b).
            if not isinstance(call_meta, dict):
                raise ContactActivityValidationError("call_meta phải là object JSON.")
            if len(json.dumps(call_meta, ensure_ascii=False)) > _CALL_META_MAX_LEN:
                raise ContactActivityValidationError(
                    f"call_meta tối đa {_CALL_META_MAX_LEN} ký tự sau serialize."
                )

        activity = ContactActivity.objects.create(
            customer=customer,
            order=order,
            staff=actor,
            kind=kind,
            note=note,
            call_meta=call_meta,
        )
        return _activity_to_item(activity)


def _sales_status_label(code: str | None) -> str | None:
    if not code:
        return None
    return SALES_STATUS_LABELS.get(code, code)


def _actor_display_name(actor) -> str:
    if not actor:
        return ""
    return actor.display_name or actor.username


def _journey_event_to_item(event: OrderJourneyEvent) -> dict:
    return {
        "type": "status_change",
        "at": event.created_at,
        "actor_name": _actor_display_name(event.actor),
        "note": event.note or "",
        "from_code": event.from_code,
        "to_code": event.to_code,
        "from_label": _sales_status_label(event.from_code),
        "to_label": _sales_status_label(event.to_code),
        "order_id": event.order_id,
        "call_meta": None,
    }


def _activity_to_item(activity: ContactActivity) -> dict:
    return {
        "type": activity.kind,
        "at": activity.created_at,
        "actor_name": _actor_display_name(activity.staff),
        "note": activity.note or "",
        "from_code": None,
        "to_code": None,
        "from_label": None,
        "to_label": None,
        "order_id": activity.order_id,
        "call_meta": activity.call_meta,
    }
