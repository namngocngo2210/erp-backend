from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, ClassVar

from django.utils import timezone

from apps.common.events import DomainEvent
from apps.orders.models import OrderStatus


@dataclass(frozen=True)
class OrderStatusChanged(DomainEvent):
    """Phát khi Order.status đổi (inline edit hoặc đồng bộ KiotViet).

    ``actor_id``: user gây ra thay đổi (sửa tay, job KiotViet do user bấm, nhập hoàn);
    ``None`` = hệ thống. ``source``: đường phát (``StatusChangeSource``) để journey hiện
    nguồn thay cho chữ "Hệ thống" chung chung khi không có actor.
    """

    event_type: ClassVar[str] = "orders.status_changed"
    order_id: int = 0
    old_status: int = 0
    new_status: int = 0
    actor_id: int | None = None
    source: str = ""


class StatusChangeSource:
    """Giá trị ``OrderStatusChanged.source`` — ghi vào ``payload_json.source`` của journey."""

    USER = "user"
    KIOTVIET_ACTION = "kiotviet_action"
    KIOTVIET_WEBHOOK = "kiotviet_webhook"
    KIOTVIET_SYNC = "kiotviet_sync"
    STOCK_RETURN = "stock_return"


@dataclass(frozen=True)
class OrderSaved(DomainEvent):
    """Phát sau MỖI ``Order.save()`` (tạo lẫn sửa), sau commit.

    Thay cho ``post_save`` mà các app khác từng móc thẳng vào model (audit
    2026-09-23 T3/P2-4): consumer chỉ nhận id/giá trị, không nhận ORM object, và
    thấy state ĐÃ commit. Cùng tập trường hợp với ``post_save`` (mọi ``.save()``,
    kể cả ``create``/``update_or_create``); ``bulk_*``/``queryset.update()`` không
    phát — y như signal cũ.

    ``update_fields``: ``None`` = full save (không biết field nào đổi); tuple = tập
    field caller truyền — consumer tự lọc field mình quan tâm.
    """

    event_type: ClassVar[str] = "orders.saved"
    order_id: int = 0
    customer_id: int | None = None
    order_source: str = ""
    created: bool = False
    is_deleted: bool = False
    update_fields: tuple[str, ...] | None = None


class OrderEventContractError(ValueError):
    pass


@dataclass(frozen=True)
class OrderEvent:
    type: str
    order_id: int
    channel_code: str | None = None
    # Chủ đơn (Order.staff_id). Cùng với channel_code, đây là hai field khiến payload
    # TỰ CHỨA đủ dữ liệu phân quyền — consumer không phải tra DB cho từng client.
    # Thiếu chúng thì CRMConsumer._is_authorized phải query, và số query nhân theo số
    # client đang kết nối (sự cố 2026-07-29 — xem docs/ws-broadcast-db-fanout-2026-07-29.md).
    staff_id: int | None = None
    status: int | None = None
    invoice_status: int | None = None
    shipping_fee: str | None = None
    invoice_code: str | None = None
    shipping_code: str | None = None
    is_kiotviet_processing: bool | None = None
    latest_kiotviet_job_status: str | None = None
    latest_kiotviet_job_action: str | None = None
    latest_kiotviet_job_error: str | None = None
    requested_by_id: int | None = None
    from_status: int | None = None
    to_status: int | None = None
    sales_status_code: str | None = None
    # Row bản list (OrderListSerializer, đã pop can_edit) — serialize MỘT lần tại
    # điểm phát để tab upsert thẳng, thay cho N tab × GET /api/orders/{id} (bão
    # gate 2026-08-04, xem docs/order-list-fanout-hardening-2026-08-04.md).
    # Consumer strip field ẩn theo field_policy của từng user trước khi gửi.
    row: dict[str, Any] | None = None
    source: str = "orders"
    occurred_at: str = ""

    def to_payload(self) -> dict[str, Any]:
        # KHÔNG dùng dataclasses.asdict(): nó deep-copy và dựng lại MỌI mapping con
        # bằng chính type của mapping đó. `row` mang các giá trị đến thẳng từ DRF
        # (`row["crm"]` là ReturnDict, ctor bắt buộc kwarg `serializer`) nên asdict
        # nổ KeyError: 'serializer' — 500 ở transfer-landing, prod 2026-08-11.
        # Event này không có field dataclass lồng nhau, đệ quy chẳng mang lại gì.
        return {
            field.name: value
            for field in fields(self)
            if (value := getattr(self, field.name)) is not None and value != ""
        }


class OrderEventContract:
    EVENT_ORDER_CREATED = "order.created"
    EVENT_ORDER_UPDATED = "order.updated"
    EVENT_TYPES = {EVENT_ORDER_CREATED, EVENT_ORDER_UPDATED}

    STATUSES = {choice for choice, _label in OrderStatus.choices}

    @classmethod
    def validate_payload(cls, payload: dict[str, Any]) -> OrderEvent:
        if not isinstance(payload, dict):
            raise OrderEventContractError("Event payload must be an object.")

        event_type = str(payload.get("type") or "").strip()
        if event_type not in cls.EVENT_TYPES:
            raise OrderEventContractError(f"Unsupported event type: {event_type or 'empty'}.")

        order_id = payload.get("order_id")
        if not isinstance(order_id, int) or order_id <= 0:
            raise OrderEventContractError("order_id must be a positive integer.")

        channel_code = cls._normalize_optional_text(payload.get("channel_code"))
        staff_id = cls._normalize_optional_int(payload.get("staff_id"))
        raw_status = payload.get("status")
        status = cls._normalize_optional_int(raw_status)
        raw_invoice_status = payload.get("invoice_status")
        invoice_status = cls._normalize_optional_int(raw_invoice_status)
        shipping_fee = cls._normalize_optional_decimal_text(payload.get("shipping_fee"))
        invoice_code = cls._normalize_optional_text(payload.get("invoice_code"))
        shipping_code = cls._normalize_optional_text(payload.get("shipping_code"))
        is_kiotviet_processing = cls._normalize_optional_bool(payload.get("is_kiotviet_processing"))
        latest_kiotviet_job_status = cls._normalize_optional_text(payload.get("latest_kiotviet_job_status"))
        latest_kiotviet_job_action = cls._normalize_optional_text(payload.get("latest_kiotviet_job_action"))
        latest_kiotviet_job_error = cls._normalize_optional_text(payload.get("latest_kiotviet_job_error"))
        requested_by_id = cls._normalize_optional_int(payload.get("requested_by_id"))
        source = cls._normalize_optional_text(payload.get("source")) or "orders"
        occurred_at = cls._normalize_optional_text(payload.get("occurred_at")) or timezone.now().isoformat()

        raw_from_status = payload.get("from_status")
        raw_to_status = payload.get("to_status")
        from_status = cls._normalize_optional_int(raw_from_status)
        to_status = cls._normalize_optional_int(raw_to_status)
        sales_status_code = cls._normalize_optional_text(payload.get("sales_status_code"))

        # row lenient: hỏng (không phải dict / id không khớp order_id) thì BỎ ROW,
        # không giết event — client mất row thì rơi về đường fetch cũ, còn hơn
        # mất cả cập nhật realtime.
        row = payload.get("row")
        if row is not None and (not isinstance(row, dict) or row.get("id") != order_id):
            row = None

        if raw_status not in (None, "") and status is None:
            raise OrderEventContractError(f"Invalid status: {raw_status}.")
        if raw_invoice_status not in (None, "") and invoice_status is None:
            raise OrderEventContractError(f"Invalid invoice_status: {raw_invoice_status}.")
        if raw_from_status not in (None, "") and from_status is None:
            raise OrderEventContractError(f"Invalid from_status: {raw_from_status}.")
        if raw_to_status not in (None, "") and to_status is None:
            raise OrderEventContractError(f"Invalid to_status: {raw_to_status}.")
        cls._validate_status_transition(
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
        )

        if status is not None and status not in cls.STATUSES:
            raise OrderEventContractError(f"Invalid status: {status}.")

        if event_type == cls.EVENT_ORDER_CREATED and not channel_code:
            raise OrderEventContractError("channel_code is required for order.created events.")

        return OrderEvent(
            type=event_type,
            order_id=order_id,
            channel_code=channel_code,
            staff_id=staff_id,
            status=status,
            invoice_status=invoice_status,
            shipping_fee=shipping_fee,
            invoice_code=invoice_code,
            shipping_code=shipping_code,
            is_kiotviet_processing=is_kiotviet_processing,
            latest_kiotviet_job_status=latest_kiotviet_job_status,
            latest_kiotviet_job_action=latest_kiotviet_job_action,
            latest_kiotviet_job_error=latest_kiotviet_job_error,
            requested_by_id=requested_by_id,
            from_status=from_status,
            to_status=to_status,
            sales_status_code=sales_status_code,
            row=row,
            source=source,
            occurred_at=occurred_at,
        )

    @classmethod
    def build_order_created(
        cls,
        *,
        order_id: int,
        channel_code: str,
        staff_id: int | None = None,
        status: int | None = None,
        source: str = "orders.service",
        row: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = cls.validate_payload(
            {
                "type": cls.EVENT_ORDER_CREATED,
                "order_id": order_id,
                "channel_code": channel_code,
                "staff_id": staff_id,
                "status": status,
                "source": source,
                "row": row,
            }
        )
        return event.to_payload()

    @classmethod
    def build_order_updated(
        cls,
        *,
        order_id: int,
        channel_code: str | None = None,
        staff_id: int | None = None,
        status: int | None = None,
        invoice_status: int | None = None,
        shipping_fee: str | int | float | None = None,
        invoice_code: str | None = None,
        shipping_code: str | None = None,
        is_kiotviet_processing: bool | None = None,
        latest_kiotviet_job_status: str | None = None,
        latest_kiotviet_job_action: str | None = None,
        latest_kiotviet_job_error: str | None = None,
        requested_by_id: int | None = None,
        from_status: int | None = None,
        to_status: int | None = None,
        sales_status_code: str | None = None,
        source: str = "orders.service",
        row: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """``row`` chỉ dành cho các nguồn mà tab đọc CHƯA CÓ dòng đó trong bảng.

        Phần lớn ``order.updated`` không cần row: FE vá tại chỗ bằng chính các
        field trong payload (`patchOrderFromRealtime`), không tốn request nào.
        Ngoại lệ là chuyển đơn Landing → Đơn hàng: đơn vừa xuất hiện trong section
        `orders` nên không có gì để vá, tab buộc phải đi lấy. Xem
        `build_order_created_row` và §6.5 của
        docs/cache-latency-optimization-2026-08-11.md.
        """
        event = cls.validate_payload(
            {
                "type": cls.EVENT_ORDER_UPDATED,
                "order_id": order_id,
                "channel_code": channel_code,
                "staff_id": staff_id,
                "status": status,
                "invoice_status": invoice_status,
                "shipping_fee": shipping_fee,
                "invoice_code": invoice_code,
                "shipping_code": shipping_code,
                "is_kiotviet_processing": is_kiotviet_processing,
                "latest_kiotviet_job_status": latest_kiotviet_job_status,
                "latest_kiotviet_job_action": latest_kiotviet_job_action,
                "latest_kiotviet_job_error": latest_kiotviet_job_error,
                "requested_by_id": requested_by_id,
                "from_status": from_status,
                "to_status": to_status,
                "sales_status_code": sales_status_code,
                "source": source,
                "row": row,
            }
        )
        return event.to_payload()

    @classmethod
    def _validate_status_transition(
        cls,
        *,
        event_type: str,
        from_status: int | None,
        to_status: int | None,
    ) -> None:
        has_from = from_status is not None
        has_to = to_status is not None
        if has_from != has_to:
            raise OrderEventContractError("from_status and to_status must be provided together.")

        if not has_from:
            return

        if event_type != cls.EVENT_ORDER_UPDATED:
            raise OrderEventContractError("Status transitions are only allowed on order.updated events.")

        if from_status not in cls.STATUSES or to_status not in cls.STATUSES:
            raise OrderEventContractError("Invalid status transition values.")

        if from_status == to_status:
            raise OrderEventContractError("from_status and to_status must be different.")

        return

    @staticmethod
    def _normalize_optional_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _normalize_optional_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_optional_decimal_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return text

    @staticmethod
    def _normalize_optional_bool(value: Any) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"true", "1", "yes"}:
            return True
        if text in {"false", "0", "no"}:
            return False
        return None
