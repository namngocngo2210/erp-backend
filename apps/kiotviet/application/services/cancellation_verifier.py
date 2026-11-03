from __future__ import annotations

import logging
from enum import Enum

from django.conf import settings
from django.core.cache import cache

from apps.common.kiotviet_constants import CANCELLED_INVOICE_STATUS
from apps.kiotviet.infrastructure.services.common_auth_customer import KiotVietInvoiceNotFound
from apps.kiotviet.infrastructure.services.invoice_sync_queries import get_invoice_by_code

logger = logging.getLogger(__name__)

# KiotViet: invoice status 2 = hóa đơn đã hủy (CANCELLED_INVOICE_STATUS, nguồn ở
# apps/common/kiotviet_constants.py); invoiceDelivery.status 5 = trả hàng, 6 = hủy giao.
# Nhất quán với webhook (apps/kiotviet/webhook_impl.py): delivery 5 (hóa đơn sống) heal thành
# RETURNED; delivery 6 mà hóa đơn CÒN SỐNG nghĩa là mới hủy VẬN ĐƠN (thường để giao lại bằng
# vận đơn khác) — chặn quét nhưng tuyệt đối không lật đơn thành CANCELLED trong DB (webhook
# cũng skip ghi DB ở ca này vì verify thấy hóa đơn còn sống).
_RETURNED_DELIVERY_STATUS = 5
_DELIVERY_CANCELLED_STATUS = 6


class VerificationUnavailable(Exception):
    """Không hỏi được KiotViet (lỗi/timeout/rate-limit) → caller phải fail-closed."""


class CancelCheck(str, Enum):
    OK = "ok"
    CANCELLED = "cancelled"
    UNVERIFIABLE = "unverifiable"


def _cache_key(channel_id: int, invoice_code: str) -> str:
    return f"scan:cancelcheck:{channel_id}:{invoice_code}"


def _pick(payload: dict, keys: list[str]):
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _as_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _cancellation_target(payload: dict) -> tuple[int | None, bool] | None:
    """Trả về ``(heal_status, mark_invoice_cancelled)`` nếu KiotViet nói đơn không được xuất.

    - invoice status == 2 → đơn HỦY: heal Order.status = 6 (CANCELLED) + invoice_status = 2.
    - delivery status 5 (invoice vẫn sống) → heal Order.status = 5 (RETURNED),
      KHÔNG đụng invoice_status.
    - delivery status 6 (invoice vẫn sống) → chỉ hủy VẬN ĐƠN: chặn quét nhưng
      ``heal_status = None`` — KHÔNG ghi gì vào DB.
    - ngược lại → ``None`` (đơn bình thường).

    Nếu payload 200 nhưng KHÔNG đọc được trạng thái nào (invoice lẫn delivery) — ví dụ body
    không phải JSON dict nên client bọc thành ``{"raw_response": ...}`` — thì ta CHƯA xác minh
    được gì: raise :class:`VerificationUnavailable` để caller fail-closed, thay vì coi là OK.
    """
    from apps.orders.models import OrderStatus

    invoice_status = _as_int(_pick(payload, ["status", "Status", "invoiceStatus", "InvoiceStatus"]))

    delivery = _pick(payload, ["invoiceDelivery", "InvoiceDelivery"]) or {}
    if isinstance(delivery, list):
        delivery = delivery[0] if delivery else {}
    delivery_status = _as_int(_pick(delivery, ["status", "Status"])) if isinstance(delivery, dict) else None

    if invoice_status is None and delivery_status is None:
        raise VerificationUnavailable(
            "KiotViet trả về payload không có trạng thái hóa đơn/giao hàng để xác minh."
        )

    if invoice_status == CANCELLED_INVOICE_STATUS:
        return int(OrderStatus.CANCELLED), True
    if delivery_status == _RETURNED_DELIVERY_STATUS:
        return int(OrderStatus.RETURNED), False
    if delivery_status == _DELIVERY_CANCELLED_STATUS:
        return None, False
    return None


class InvoiceCancellationVerifier:
    """Hỏi KiotViet (nguồn sự thật) xem đơn có bị hủy chưa, thay vì tin DB (sync trễ tới 30')."""

    @staticmethod
    def verify_not_cancelled(order) -> CancelCheck:
        if not getattr(settings, "SCAN_VERIFY_CANCEL_REALTIME", True):
            logger.warning("scan verify skipped: kill-switch off (order_id=%s)", order.id)
            return CancelCheck.UNVERIFIABLE

        channel = order.kiotviet_channel
        invoice_code = str(order.invoice_code or "").strip()
        if channel is None or not invoice_code:
            # Đơn không có hóa đơn KiotViet (đơn gửi ngoài / tự tạo) → DB là nguồn sự thật.
            return CancelCheck.UNVERIFIABLE

        key = _cache_key(channel.id, invoice_code)
        cached = cache.get(key)
        if cached is not None:
            try:
                return CancelCheck(cached)
            except ValueError:
                logger.warning("scan verify cache có giá trị lạ (key=%s, value=%r) → coi như miss", key, cached)

        try:
            payload = get_invoice_by_code(
                invoice_code,
                order,
                channel,
                timeout=float(getattr(settings, "SCAN_VERIFY_TIMEOUT_SECONDS", 3)),
            )
        except KiotVietInvoiceNotFound as exc:
            # 404 là câu trả lời dứt khoát: hóa đơn không thuộc channel này. Fail-closed ở đây
            # sẽ chặn VĨNH VIỄN mọi đơn có invoice_code rác → để caller fallback về DB.
            logger.warning(
                "scan verify unverifiable: KiotViet 404 (order_id=%s, invoice_code=%s, channel_id=%s): %s",
                order.id,
                invoice_code,
                channel.id,
                exc,
            )
            return CancelCheck.UNVERIFIABLE
        except Exception as exc:  # noqa: BLE001 — lỗi hạ tầng (timeout/5xx/mạng) → fail-closed
            logger.warning(
                "scan verify unavailable (order_id=%s, invoice_code=%s): %s",
                order.id,
                invoice_code,
                exc,
            )
            raise VerificationUnavailable(str(exc)) from exc

        if not isinstance(payload, dict):
            raise VerificationUnavailable("KiotViet trả về payload không hợp lệ.")

        target = _cancellation_target(payload)
        if target is not None:
            target_status, mark_invoice_cancelled = target
            if target_status is not None:
                InvoiceCancellationVerifier._heal_cancelled_order(
                    order,
                    target_status=target_status,
                    mark_invoice_cancelled=mark_invoice_cancelled,
                )
            cache.set(key, CancelCheck.CANCELLED.value, settings.SCAN_VERIFY_CACHE_TTL_CANCELLED)
            return CancelCheck.CANCELLED

        cache.set(key, CancelCheck.OK.value, settings.SCAN_VERIFY_CACHE_TTL_OK)
        return CancelCheck.OK

    @staticmethod
    def _heal_cancelled_order(order, *, target_status: int, mark_invoice_cancelled: bool) -> None:
        """DB đang trễ so với KiotViet → ghi lại đúng trạng thái nguồn, publish event cho FE."""
        from apps.common.events import event_bus
        from apps.orders.contracts import OrderEventContract
        from apps.orders.contracts.order_events import OrderStatusChanged, StatusChangeSource
        from apps.orders.models import Order
        from apps.orders.application.services.order_service_impl import OrderService

        previous_status = int(order.status)
        status_changed = previous_status != target_status
        invoice_changed = mark_invoice_cancelled and order.invoice_status != CANCELLED_INVOICE_STATUS
        if not status_changed and not invoice_changed:
            return

        update_values: dict = {}
        if status_changed:
            update_values["status"] = target_status
        if invoice_changed:
            update_values["invoice_status"] = CANCELLED_INVOICE_STATUS

        Order.objects.filter(id=order.id).update(**update_values)
        if status_changed:
            order.status = target_status
        if invoice_changed:
            order.invoice_status = CANCELLED_INVOICE_STATUS

        logger.info(
            "scan verify healed order (order_id=%s, from_status=%s, to_status=%s, invoice_status=%s)",
            order.id,
            previous_status,
            target_status,
            order.invoice_status,
        )

        # Domain event: verifier có thể "cướp" transition của webhook (heal trước, webhook đến sau
        # thấy status trùng → không publish nữa). Nếu ta không publish ở đây, subscriber inventory
        # (release_for_order khi status=6) sẽ KHÔNG bao giờ chạy → tồn giữ của đơn hủy treo vĩnh viễn.
        if status_changed:
            event_bus.publish_on_commit(
                OrderStatusChanged(
                    order_id=order.id,
                    old_status=previous_status,
                    new_status=int(target_status),
                    source=StatusChangeSource.KIOTVIET_SYNC,
                )
            )

        event_kwargs = {
            "order_id": order.id,
            "channel_code": order.channel.code if order.channel_id else None,
            "status": target_status,
            "invoice_status": order.invoice_status,
            "source": "scan.verify.cancellation",
        }
        # Contract cấm from_status == to_status → chỉ gửi khi trạng thái thật sự đổi.
        if status_changed:
            event_kwargs["from_status"] = previous_status
            event_kwargs["to_status"] = target_status

        # Best-effort: WS/Redis chết KHÔNG được nuốt mất verdict CANCELLED (đã heal DB xong).
        try:
            OrderService.push_event(OrderEventContract.build_order_updated(**event_kwargs))
        except Exception:
            logger.exception("scan verify push_event failed (order_id=%s)", order.id)
