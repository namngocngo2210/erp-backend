import logging
import time
from decimal import Decimal

from django.utils.dateparse import parse_datetime

from apps.audit.decorators import log_kiotviet_call
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order as OrderModel

from .common_auth_customer import _sanitize_text
from .invoice_sync_queries import _resolve_paired_old_channel, get_invoice_by_code
from .gateway_kship_customer_webhook import (
    create_kship_delivery_for_invoice,
    build_kship_create_order_payload,
    create_kship_order,
    resolve_required_service_extra,
)
from .invoice_mutations_push import (
    delete_invoice_by_id,
    push_order_to_kiotviet,
    update_invoice_delivery_status_by_id,
)


logger = logging.getLogger(__name__)

# Thang trạng thái HOÁ ĐƠN KiotViet (khác thang `Order.status`): 1=hoàn thành, 2=huỷ,
# 3=đang xử lý. Chỉ dùng để nhận biết hoá đơn còn sống, không map sang Order.status.
CANCELLED_INVOICE_OWN_STATUS = 2


# Dò lại mã vận đơn sau khi create-order rơi vào ca "không xác định". KShip đẩy mã sang
# KiotViet có độ trễ nên đọc một lần là chưa đủ. Tổng ngân sách ≤55s để nằm gọn trong
# soft_time_limit=180 của process_kiotviet_job (bước 1 ≤30s + create-order 60s).
KSHIP_RECONCILE_ATTEMPTS = 3
KSHIP_RECONCILE_SLEEP_SECONDS = 5.0
KSHIP_RECONCILE_READ_TIMEOUT_SECONDS = 15

KSHIP_INDETERMINATE_MESSAGE = (
    "KHÔNG XÁC ĐỊNH kết quả tạo vận đơn: KShip không phản hồi kịp nên vận đơn CÓ THỂ "
    "đã được tạo thật. ĐỪNG bấm tạo lại (sẽ sinh vận đơn thứ hai, mất phí thật). Hãy "
    "kiểm tra hoá đơn trên KiotViet rồi bấm \"Đồng bộ mã vận đơn\"."
)


def _parse_kv_purchase_date(source: dict | None):
    if not isinstance(source, dict):
        return None
    raw = source.get("purchaseDate") or source.get("PurchaseDate")
    if not raw:
        return None
    return parse_datetime(str(raw).strip())


def _extract_invoice_delivery_code(invoice_result) -> str:
    """Lấy `invoiceDelivery.deliveryCode` — chính là `Order.shipping_code`.

    Cùng một phép bóc dùng cho action `sync_shipping` và cho bước dò lại sau timeout:
    hai đường phải đọc y hệt nhau, lệch nhau là hai đường ghi ra hai mã khác nhau.
    """
    if not isinstance(invoice_result, dict):
        return ""
    invoice_delivery = invoice_result.get("invoiceDelivery")
    invoice_delivery = invoice_delivery if isinstance(invoice_delivery, dict) else {}
    return _sanitize_text(
        invoice_delivery.get("deliveryCode") or invoice_result.get("deliveryCode") or ""
    )


def _reconcile_kship_shipping_code(order: OrderModel, channel: KiotVietChannel, invoice_code: str) -> str:
    """Đọc lại hoá đơn KiotViet để lấy mã vận đơn KShip vừa tạo (nếu có).

    Gọi khi create-order trả `indeterminate`: request đã bay đi, vận đơn có thể đã
    sống bên hãng vận chuyển. Đây là ĐỌC sự thật từ KiotViet, không phải suy đoán —
    hễ hoá đơn có `deliveryCode` thì đó đúng là mã vận đơn của đơn này, bất kể lần gọi
    nào sinh ra nó (`sync_shipping` vẫn ghi theo đúng luật đó).

    Nuốt mọi lỗi đọc: dò không ra chỉ làm mất bước cứu, còn ném ra ngoài thì job rơi
    vào except chung và người dùng lại nhận đúng thông điệp timeout trần trụi cũ.
    """
    if not invoice_code:
        return ""
    for attempt in range(1, KSHIP_RECONCILE_ATTEMPTS + 1):
        try:
            invoice_result = get_invoice_by_code(
                invoice_code,
                order,
                channel,
                timeout=KSHIP_RECONCILE_READ_TIMEOUT_SECONDS,
            )
            shipping_code = _extract_invoice_delivery_code(invoice_result)
        except Exception as exc:  # noqa: BLE001 — xem docstring
            logger.warning(
                "KShip reconcile đọc hoá đơn lỗi (order_id=%s, invoice_code=%s, attempt=%s/%s): %s",
                order.id, invoice_code, attempt, KSHIP_RECONCILE_ATTEMPTS, exc,
            )
            shipping_code = ""
        if shipping_code:
            logger.info(
                "KShip reconcile dò ra mã vận đơn (order_id=%s, invoice_code=%s, attempt=%s, shipping_code=%s)",
                order.id, invoice_code, attempt, shipping_code,
            )
            return shipping_code
        if attempt < KSHIP_RECONCILE_ATTEMPTS:
            time.sleep(KSHIP_RECONCILE_SLEEP_SECONDS)
    logger.warning(
        "KShip reconcile KHÔNG dò ra mã vận đơn (order_id=%s, invoice_code=%s, attempts=%s)",
        order.id, invoice_code, KSHIP_RECONCILE_ATTEMPTS,
    )
    return ""

class KiotVietService:
    ACTION_PUSH = "push"
    ACTION_CREATE_INVOICE = "create_invoice"
    ACTION_SYNC_SHIPPING = "sync_shipping"
    ACTION_SYNC_STATUS = "sync_status"
    ACTION_CREATE_KSHIP_DELIVERY = "create_kship_delivery"

    @classmethod
    @log_kiotviet_call
    def execute_action(
        cls,
        order: OrderModel,
        action: str,
        bucket: str = "success",
        actor_id: int = None,
        **kwargs,
    ) -> dict:
        """
        Execute a KiotViet action on an order.

        Args:
            order: Order model instance
            action: Action to perform (push, sync_shipping, sync_status, create_kship_delivery)
            bucket: Result bucket (success, warning, failed)
            actor_id: ID of user who triggered the action
            **kwargs: Additional parameters (e.g., provider_code, service_code for create_kship_delivery)

        Returns:
            Dictionary with execution result
        """
        update_fields = []
        changed = False
        status = "unknown"
        payload = {}
        current_order_code = ""
        current_invoice_code = ""
        current_invoice_id = None

        if action == KiotVietService.ACTION_PUSH:
            channel = order.kiotviet_channel
            if channel is None:
                raise ValueError("Order chưa được gán kênh KiotViet.")

            # Always push to new channel first.
            result = push_order_to_kiotviet(order, channel, actor_id)
            order_result = result.get("order_result", {}) or {}
            make_invoice = bool(result.get("make_invoice"))
            current_order_code = str(order_result.get("code") or order_result.get("orderCode") or "").strip()
            invoices = order_result.get("invoices")
            first_invoice = invoices[0] if isinstance(invoices, list) and invoices else {}
            first_invoice = first_invoice if isinstance(first_invoice, dict) else {}
            current_invoice_code = str(
                first_invoice.get("invoiceCode") or order_result.get("invoiceCode") or ""
            ).strip()
            invoice_id_raw = first_invoice.get("invoiceId") or order_result.get("invoiceId")
            try:
                current_invoice_id = int(invoice_id_raw) if invoice_id_raw is not None else None
            except (TypeError, ValueError):
                current_invoice_id = None

            previous_order_code = str(order.order_code or "").strip()
            previous_invoice_code = str(order.invoice_code or "").strip()
            previous_invoice_id = int(order.invoice_id) if order.invoice_id is not None else None
            previous_status = int(order.status) if order.status is not None else None
            order.order_code = current_order_code
            order.invoice_code = current_invoice_code
            order.invoice_id = current_invoice_id
            update_fields.append("order_code")
            update_fields.append("invoice_code")
            update_fields.append("invoice_id")
            changed = changed or previous_order_code != current_order_code
            changed = changed or previous_invoice_code != current_invoice_code
            changed = changed or previous_invoice_id != current_invoice_id
            if not previous_invoice_code and current_invoice_code:
                # Mốc "đã tạo hóa đơn" — chỉ lần gán invoice_code ĐẦU TIÊN
                # (spec 2026-08-04-order-milestone-snapshots).
                from apps.orders.application.services.milestone_snapshot_service import (
                    capture_invoice_snapshot,
                )

                capture_invoice_snapshot(order)

            invoice_status = None
            invoice_own_status = None
            invoice_result = None
            if current_invoice_code:
                try:
                    invoice_result = get_invoice_by_code(current_invoice_code, order, channel)
                except Exception as exc:
                    logger.warning(
                        "Skip post-push invoice read; treat push as success "
                        "(order_id=%s, channel_id=%s, invoice_code=%s): %s",
                        order.id,
                        channel.id if channel else None,
                        current_invoice_code,
                        exc,
                    )
                    invoice_result = None
                invoice_delivery = invoice_result.get("invoiceDelivery") if isinstance(invoice_result, dict) else None
                invoice_delivery = invoice_delivery if isinstance(invoice_delivery, dict) else {}
                invoice_status_raw = invoice_delivery.get("status")
                try:
                    invoice_status = int(invoice_status_raw) if invoice_status_raw is not None else None
                except (TypeError, ValueError):
                    invoice_status = None

                invoice_own_raw = invoice_result.get("status") if isinstance(invoice_result, dict) else None
                try:
                    invoice_own_status = int(invoice_own_raw) if invoice_own_raw is not None else None
                except (TypeError, ValueError):
                    invoice_own_status = None

                # POS sale: invoice hoàn thành (status=1) mà không có invoiceDelivery.status
                # → coi như đã giao (status=3) vì không có vận đơn để theo dõi.
                if invoice_status is None and invoice_own_status == 1:
                    invoice_status = 3

                # Đơn đang HUỶ vừa được đẩy lên thành hoá đơn SỐNG → phải rời CANCELLED
                # ngay, không chờ cron 30' (sự cố 2026-07-23, xem
                # docs/erp-revive-stock-safety-net.md). Kẹt ở CANCELLED có hai hậu quả:
                # lookup mã vận đơn trả 409 "Đơn đã hủy" nên kho không xuất được đơn vừa
                # đẩy lại, và cú lật `6 → x` — thứ kích hoạt trừ lại tồn ở subscriber kho —
                # bị dời sang đường khác.
                #
                # Đây KHÔNG phải suy đoán: hoá đơn mới tồn tại và không mang trạng thái huỷ
                # là bằng chứng dương đơn đã sống lại. Nhưng cũng KHÔNG suy tiếp ra trạng
                # thái GIAO: `invoice.status` và `Order.status` là hai thang số khác nhau,
                # map thẳng thì hoá đơn "đang xử lý"(3) thành đơn "Đã giao"(3) — đúng lỗi
                # audit 2026-07-14 vòng 4. Chỉ hạ về "Chờ xử lý"; cron tinh chỉnh tiếp sang
                # 2/3 khi vận đơn xuất hiện.
                if invoice_status is None and invoice_own_status is not None:
                    from apps.orders.models import OrderStatus as _OrderStatus

                    if invoice_own_status != CANCELLED_INVOICE_OWN_STATUS and int(
                        order.status or 0
                    ) == int(_OrderStatus.CANCELLED):
                        invoice_status = int(_OrderStatus.PENDING_PROCESSING)

                if invoice_status is not None:
                    prev = int(order.status) if order.status is not None else 0
                    order.status = invoice_status
                    update_fields.append("status")
                    changed = changed or previous_status != invoice_status
                    if prev != int(invoice_status):
                        from apps.common.events import event_bus
                        from apps.orders.contracts.order_events import OrderStatusChanged, StatusChangeSource

                        event_bus.publish_on_commit(
                            OrderStatusChanged(
                                order_id=order.id,
                                old_status=prev,
                                new_status=int(invoice_status),
                                actor_id=actor_id,
                                source=StatusChangeSource.KIOTVIET_ACTION,
                            )
                        )

                if invoice_own_status is not None and order.invoice_status != invoice_own_status:
                    order.invoice_status = invoice_own_status
                    update_fields.append("invoice_status")
                    changed = True

                purchase_dt = _parse_kv_purchase_date(invoice_result)
                if purchase_dt is not None and order.kiotviet_purchase_date != purchase_dt:
                    order.kiotviet_purchase_date = purchase_dt
                    update_fields.append("kiotviet_purchase_date")
                    changed = True

            status = "created" if not previous_order_code else "updated"

            payload = {
                "mode": "live",
                "action": action,
                "status": status,
                "kiotviet_order_code": current_order_code,
                "order_code": current_order_code,
                "invoice_code": current_invoice_code,
                "invoice_id": current_invoice_id,
                "invoice_status": invoice_status,
                "make_invoice": make_invoice,
                "customer_code": result.get("customer_code"),
                "message": "Đã đẩy đơn lên KiotViet.",
                "bucket": bucket,
                "api_result": result,
                "invoice_result": invoice_result,
            }

        elif action == KiotVietService.ACTION_CREATE_INVOICE:
            from apps.orders.models import OrderStatus

            channel = order.kiotviet_channel
            if channel is None:
                raise ValueError("Order chưa được gán kênh KiotViet.")

            result = push_order_to_kiotviet(order, channel, actor_id, use_invoice_api=True)
            order_result = result.get("order_result", {}) or {}
            current_invoice_code = str(
                order_result.get("invoiceCode") or order_result.get("code") or ""
            ).strip()
            invoice_id_raw = order_result.get("invoiceId") or order_result.get("id")
            try:
                current_invoice_id = int(invoice_id_raw) if invoice_id_raw is not None else None
            except (TypeError, ValueError):
                current_invoice_id = None

            previous_invoice_code = str(order.invoice_code or "").strip()
            previous_invoice_id = int(order.invoice_id) if order.invoice_id is not None else None
            previous_status = int(order.status) if order.status is not None else None
            order.invoice_code = current_invoice_code
            order.invoice_id = current_invoice_id
            update_fields.append("invoice_code")
            update_fields.append("invoice_id")
            changed = changed or previous_invoice_code != current_invoice_code
            changed = changed or previous_invoice_id != current_invoice_id
            if not previous_invoice_code and current_invoice_code:
                # Mốc "đã tạo hóa đơn" — chỉ lần gán invoice_code ĐẦU TIÊN
                # (spec 2026-08-04-order-milestone-snapshots).
                from apps.orders.application.services.milestone_snapshot_service import (
                    capture_invoice_snapshot,
                )

                capture_invoice_snapshot(order)

            purchase_dt = _parse_kv_purchase_date(order_result)
            if purchase_dt is None and current_invoice_code:
                try:
                    fetched_invoice = get_invoice_by_code(current_invoice_code, order, channel)
                except Exception as exc:
                    logger.warning(
                        "Skip post-create invoice read; keep order without purchase_dt "
                        "(order_id=%s, channel_id=%s, invoice_code=%s): %s",
                        order.id,
                        channel.id if channel else None,
                        current_invoice_code,
                        exc,
                    )
                    fetched_invoice = None
                purchase_dt = _parse_kv_purchase_date(fetched_invoice)
            if purchase_dt is not None and order.kiotviet_purchase_date != purchase_dt:
                order.kiotviet_purchase_date = purchase_dt
                update_fields.append("kiotviet_purchase_date")
                changed = True

            # Quick sale: chỉ chuyển trạng thái sang "Giao thành công" KHI đã
            # có mã hóa đơn từ KiotViet. push_order_to_kiotviet đã raise nếu
            # response thiếu mã, nên tới đây current_invoice_code chắc chắn
            # khác rỗng — đảm bảo đơn fail KiotViet không bị nhảy trạng thái.
            delivered_status = int(OrderStatus.DELIVERED_SUCCESS)
            if current_invoice_code and previous_status != delivered_status:
                order.status = delivered_status
                update_fields.append("status")
                changed = True
                from apps.common.events import event_bus
                from apps.orders.contracts.order_events import OrderStatusChanged, StatusChangeSource

                event_bus.publish_on_commit(
                    OrderStatusChanged(
                        order_id=order.id,
                        old_status=previous_status if previous_status is not None else 0,
                        new_status=delivered_status,
                        actor_id=actor_id,
                        source=StatusChangeSource.KIOTVIET_ACTION,
                    )
                )

            status = "created"
            payload = {
                "mode": "live",
                "action": action,
                "status": status,
                "invoice_code": current_invoice_code,
                "invoice_id": current_invoice_id,
                "customer_code": result.get("customer_code"),
                "message": "Đã tạo hóa đơn trên KiotViet.",
                "bucket": bucket,
                "api_result": result,
            }

        elif action == KiotVietService.ACTION_SYNC_SHIPPING:
            channel = order.kiotviet_channel
            if channel is None:
                raise ValueError("Order chưa được gán kênh KiotViet.")

            invoice_code = str(order.invoice_code or "").strip()
            if not invoice_code:
                raise ValueError("[stage=sync_shipping] Order chưa có invoice_code để đồng bộ mã vận đơn.")

            invoice_result = get_invoice_by_code(invoice_code, order, channel)
            shipping_code = _extract_invoice_delivery_code(invoice_result)

            old_shipping_code = str(order.shipping_code or "").strip()
            if shipping_code:
                order.shipping_code = shipping_code
                update_fields.append("shipping_code")
                changed = old_shipping_code != shipping_code
                status = "updated" if changed else "unchanged"
            else:
                status = "no_delivery_code"

            payload = {
                "mode": "live",
                "action": action,
                "status": status,
                "invoice_code": invoice_code,
                "shipping_code": shipping_code,
                "message": "Đã đồng bộ mã vận đơn từ hóa đơn KiotViet." if shipping_code else "Hóa đơn chưa có deliveryCode.",
                "bucket": bucket,
                "api_result": invoice_result,
            }

        elif action == KiotVietService.ACTION_SYNC_STATUS:
            channel = order.kiotviet_channel
            if channel is None:
                raise ValueError("Order chưa được gán kênh KiotViet.")

            invoice_code = str(order.invoice_code or "").strip()
            if not invoice_code:
                raise ValueError("[stage=sync_status] Order chưa có invoice_code để đồng bộ trạng thái.")

            invoice_result = get_invoice_by_code(invoice_code, order, channel)
            invoice_delivery = invoice_result.get("invoiceDelivery") if isinstance(invoice_result, dict) else None
            invoice_delivery = invoice_delivery if isinstance(invoice_delivery, dict) else {}

            delivery_status_raw = invoice_delivery.get("status")

            try:
                delivery_status = int(delivery_status_raw) if delivery_status_raw is not None else None
            except (TypeError, ValueError):
                delivery_status = None

            invoice_own_raw = invoice_result.get("status") if isinstance(invoice_result, dict) else None
            try:
                invoice_own_status = int(invoice_own_raw) if invoice_own_raw is not None else None
            except (TypeError, ValueError):
                invoice_own_status = None

            # POS sale: invoice hoàn thành (status=1) mà không có invoiceDelivery.status
            # → coi như đã giao (status=3) vì không có vận đơn để theo dõi.
            if delivery_status is None and invoice_own_status == 1:
                delivery_status = 3

            if invoice_own_status is not None and order.invoice_status != invoice_own_status:
                order.invoice_status = invoice_own_status
                update_fields.append("invoice_status")

            old_status = int(order.status) if order.status is not None else None
            old_channel_sync = {"enabled": False, "updated": False, "old_id": order.old_id}
            if delivery_status is not None:
                order.status = delivery_status
                update_fields.append("status")
                changed = old_status != delivery_status
                status = "updated" if changed else "unchanged"
                if old_status != int(delivery_status):
                    from apps.common.events import event_bus
                    from apps.orders.contracts.order_events import OrderStatusChanged, StatusChangeSource

                    event_bus.publish_on_commit(
                        OrderStatusChanged(
                            order_id=order.id,
                            old_status=old_status if old_status is not None else 0,
                            new_status=int(delivery_status),
                            actor_id=actor_id,
                            source=StatusChangeSource.KIOTVIET_ACTION,
                        )
                    )

                old_channel = _resolve_paired_old_channel(channel)
                if old_channel is not None and order.old_id:
                    try:
                        if delivery_status in {5, 6}:
                            old_update_result = delete_invoice_by_id(
                                invoice_id=order.old_id,
                                order=order,
                                channel=old_channel,
                            )
                            old_sync_action = "delete_invoice"
                        else:
                            old_update_result = update_invoice_delivery_status_by_id(
                                invoice_id=order.old_id,
                                status=delivery_status,
                                order=order,
                                channel=old_channel,
                            )
                            old_sync_action = "update_delivery_status"
                        old_channel_sync = {
                            "enabled": True,
                            "updated": True,
                            "old_id": order.old_id,
                            "action": old_sync_action,
                            "api_result": old_update_result,
                        }
                    except Exception as exc:
                        # Không fail toàn bộ sync status của kênh mới nếu kênh cũ trả lỗi.
                        logger.warning(
                            "KiotViet old channel status sync failed (order_id=%s, old_channel_id=%s, old_invoice_id=%s, status=%s): %s",
                            order.id,
                            old_channel.id if old_channel else None,
                            order.old_id,
                            delivery_status,
                            str(exc),
                        )
                        old_channel_sync = {
                            "enabled": True,
                            "updated": False,
                            "old_id": order.old_id,
                            "error": str(exc),
                        }
                else:
                    if old_channel is None:
                        old_channel_sync = {
                            "enabled": False,
                            "updated": False,
                            "old_id": order.old_id,
                            "reason": "old_channel_not_configured",
                        }
                    else:
                        old_channel_sync = {
                            "enabled": True,
                            "updated": False,
                            "old_id": order.old_id,
                            "reason": "old_invoice_missing",
                        }
            else:
                status = "no_status"

            payload = {
                "mode": "live",
                "action": action,
                "status": status,
                "invoice_code": invoice_code,
                "shipping_code": str(order.shipping_code or "").strip(),
                "delivery_status": delivery_status,
                "message": "Đã đồng bộ trạng thái đơn từ invoiceDelivery.status." if delivery_status is not None else "Hóa đơn chưa có trạng thái giao hàng.",
                "bucket": bucket,
                "api_result": invoice_result,
                "old_channel_sync": old_channel_sync,
            }

        elif action == KiotVietService.ACTION_CREATE_KSHIP_DELIVERY:
            channel = order.kiotviet_channel
            if channel is None:
                raise ValueError("Order chưa được gán kênh KiotViet.")

            invoice_id = order.invoice_id
            if not invoice_id:
                raise ValueError("[stage=create_kship_delivery] Order chưa có invoice_id để tạo mã vận đơn.")

            # Get provider_code, service_code and note from kwargs
            provider_code = kwargs.get("provider_code", "")
            service_code = kwargs.get("service_code", "")
            kship_note = kwargs.get("kship_note", "")
            shipper_note = kwargs.get("shipper_note", "")

            if not provider_code:
                raise ValueError("[stage=create_kship_delivery] Thiếu provider_code để tạo mã vận đơn.")

            if not service_code:
                raise ValueError("[stage=create_kship_delivery] Thiếu service_code để tạo mã vận đơn.")

            # Step 1: Call KiotViet API to get delivery information
            delivery_result = create_kship_delivery_for_invoice(invoice_id, order, channel)

            if not delivery_result.get("success"):
                status = "failed"
                # Giữ nguyên lỗi KiotViet trả về; chỉ fallback câu chung khi
                # gateway không đưa được mô tả nào.
                delivery_error = str(delivery_result.get("error") or "").strip()
                payload = {
                    "mode": "live",
                    "action": action,
                    "status": status,
                    "invoice_id": invoice_id,
                    "delivery_result": delivery_result,
                    "message": delivery_error or "Không thể lấy thông tin delivery từ KiotViet.",
                    "bucket": bucket,
                }
            else:
                # Step 2: Build payload and call create-order API
                delivery_data = delivery_result.get("delivery_data", {})

                # Sender lấy từ bảng kship_sender_addresses (id được chọn từ dialog,
                # fallback rule kênh/branch + hằng số lịch sử — xem kship_sender_service).
                from apps.orders.application.services.kship_sender_service import (
                    resolve_kship_sender_info,
                )

                sender_info = resolve_kship_sender_info(order, kwargs.get("sender_address_id"))

                # SERVICE_EXTRA bắt buộc riêng của hãng (SPX: ca lấy hàng, giá trị
                # đổi theo ngày) — phải hỏi setting-service tại đây, không hardcode được.
                extra_services = resolve_required_service_extra(
                    channel, provider_code, service_code, kwargs.get("service_extra")
                )

                # Build create-order payload
                create_order_payload = build_kship_create_order_payload(
                    delivery_data,
                    provider_code,
                    service_code,
                    sender_info,
                    note=kship_note,
                    shipper_note=shipper_note,
                    extra_services=extra_services,
                )

                # Call create-order API
                order_result = create_kship_order(
                    provider_code,
                    create_order_payload,
                    order,
                    channel,
                )

                # Timeout/mất kết nối/5xx = KHÔNG BIẾT KShip đã tạo vận đơn hay chưa.
                # Đọc lại hoá đơn KiotViet để dò `deliveryCode` TRƯỚC khi kết luận
                # thất bại — bỏ qua bước này là vận đơn thật sống bên hãng vận chuyển
                # mà DB trắng mã (đơn 554748, 2026-08-18).
                recovered_shipping_code = ""
                if not order_result.get("success") and order_result.get("indeterminate"):
                    recovered_shipping_code = _reconcile_kship_shipping_code(
                        order, channel, str(order.invoice_code or "").strip()
                    )

                if order_result.get("success") or recovered_shipping_code:
                    # Update order with shipping_code
                    order_number = order_result.get("order_number") or recovered_shipping_code
                    if order_number:
                        order.shipping_code = order_number
                        update_fields.append("shipping_code")
                        changed = True

                    # Save shipping provider & service
                    if provider_code:
                        order.shipping_provider = provider_code
                        update_fields.append("shipping_provider")
                    if service_code:
                        order.shipping_service = service_code
                        update_fields.append("shipping_service")

                    # Update shipping_fee if available
                    total_fee = order_result.get("total_fee")
                    if total_fee:
                        order.shipping_fee = Decimal(str(total_fee))
                        update_fields.append("shipping_fee")
                        changed = True

                    status = "created"
                    payload = {
                        "mode": "live",
                        "action": action,
                        "status": status,
                        "invoice_id": invoice_id,
                        "provider_code": provider_code,
                        "service_code": service_code,
                        "delivery_result": delivery_result,
                        "order_result": order_result,
                        "message": (
                            "KShip không phản hồi kịp nhưng vận đơn đã được tạo; "
                            "đã lấy mã vận đơn từ hoá đơn KiotViet."
                            if recovered_shipping_code
                            else "Đã tạo mã vận đơn KShip thành công."
                        ),
                        "bucket": bucket,
                    }
                    if recovered_shipping_code:
                        payload["recovered_by"] = "reconcile_after_indeterminate"
                else:
                    status = "failed"
                    is_indeterminate = bool(order_result.get("indeterminate"))
                    kship_error = order_result.get("error") or "Tạo mã vận đơn KShip thất bại."
                    payload = {
                        "mode": "live",
                        "action": action,
                        "status": status,
                        "invoice_id": invoice_id,
                        "provider_code": provider_code,
                        "service_code": service_code,
                        "delivery_result": delivery_result,
                        "order_result": order_result,
                        # Dò lại vẫn không ra mã: KHÔNG được để user đọc câu timeout
                        # trần rồi bấm tạo lại — đó là đường sinh vận đơn thứ hai.
                        "message": (
                            f"{KSHIP_INDETERMINATE_MESSAGE} (Chi tiết: {kship_error})"
                            if is_indeterminate
                            else kship_error
                        ),
                        "bucket": bucket,
                    }
                    if is_indeterminate:
                        payload["indeterminate"] = True
        elif action == "cancel_invoice":
            from apps.orders.models import OrderStatus

            channel = order.kiotviet_channel
            if channel is None:
                raise ValueError("Order chưa được gán kênh KiotViet.")
            if not order.invoice_code or not order.invoice_id:
                raise ValueError("Order chưa có mã hóa đơn để hủy.")

            delete_result = delete_invoice_by_id(
                invoice_id=order.invoice_id,
                order=order,
                channel=channel,
            )

            previous_invoice_code = order.invoice_code
            previous_invoice_id = order.invoice_id
            # Giữ invoice_code/invoice_id để webhook KiotViet vẫn match được đơn này
            # (tránh _create_order_from_invoice_event tạo bản sao status=6).
            _prev_status_cancel = int(order.status) if order.status is not None else 0
            order.status = int(OrderStatus.CANCELLED)
            order.invoice_status = 2
            update_fields.extend(["status", "invoice_status"])
            changed = True
            status = "cancelled"
            if _prev_status_cancel != int(OrderStatus.CANCELLED):
                from apps.common.events import event_bus
                from apps.orders.contracts.order_events import OrderStatusChanged, StatusChangeSource

                event_bus.publish_on_commit(
                    OrderStatusChanged(
                        order_id=order.id,
                        old_status=_prev_status_cancel,
                        new_status=int(OrderStatus.CANCELLED),
                        actor_id=actor_id,
                        source=StatusChangeSource.KIOTVIET_ACTION,
                    )
                )

            payload = {
                "mode": "live",
                "action": action,
                "status": status,
                "previous_invoice_code": previous_invoice_code,
                "previous_invoice_id": previous_invoice_id,
                "message": "Đã hủy đơn trên KiotViet.",
                "bucket": bucket,
                "delete_result": delete_result,
            }

        else:
            raise ValueError(f"Unsupported KiotViet action: {action}")

        # Save changes if any
        if changed:
            order.save(update_fields=update_fields)

        # Đồng bộ shipping_code sang kiot cũ do cron reconcile_paired_channels
        # (mode update_delivery_detail, 21h hằng ngày) đảm nhiệm — không push realtime ở đây.

        return {
            "status": status,
            "changed": changed,
            "payload": payload,
            "update_fields": update_fields,
        }
