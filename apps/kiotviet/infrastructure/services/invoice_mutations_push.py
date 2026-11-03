from decimal import Decimal
from urllib.parse import quote

from django.utils import timezone

from apps.kiotviet.models import KiotVietChannel, channel_vat_tax_id
from apps.orders.models import Order as OrderModel

from .common_auth_customer import (
    _parse_dimensions,
    _phone_for_kiotviet,
    _resolve_channel,
    _sanitize_text,
    _to_kiotviet_gender,
    _truncate_for_log,
    find_customer_by_code,
    find_customer_by_phone,
    logger,
)
from .gateway_kship_customer_webhook import _kiotviet_request, get_or_create_customer
from .invoice_sync_queries import (
    _extract_invoice_id_from_invoice_payload,
    _resolve_branch_id_for_push,
    create_invoice,
    create_order,
    get_invoice_by_code,
)


# Loại đơn KOC không bao giờ thu tiền người nhận: KOC giữ hàng ("không trả lại")
# hoặc sẽ trả hàng về ("có trả lại"). Dùng chung cho push KiotViet và in phiếu giao.
KOC_NO_COD_ORDER_TYPES = frozenset({"no_return", "has_return"})


def is_koc_non_cod_order(order) -> bool:
    """Đơn kênh KOC thuộc loại không được thu tiền người nhận."""
    channel = getattr(order, "channel", None)
    if not (channel and channel.is_koc):
        return False
    return str(getattr(order, "order_type", "") or "") in KOC_NO_COD_ORDER_TYPES


def _koc_payment_override(is_koc: bool, order_type: str, total_after_discount: int) -> dict | None:
    """Ghi đè COD/thanh toán khi đẩy KiotViet cho đơn kênh KOC.

    - ``no_return`` ("không trả lại"): KOC giữ hàng → coi như đã thanh toán đủ:
      không thu COD, ``totalPayment`` = tổng tiền. Không dựa vào ``customer_prepaid``
      đã lưu vì đơn có thể được đổi loại đơn hoặc sửa tiền sau khi tạo, khi đó
      prepaid cũ không còn khớp tổng tiền và bill in ra vẫn hiện COD.
    - ``has_return`` ("có trả lại"): đơn vẫn lên nhưng không thu COD, để công nợ.

    Trả về ``None`` khi ngoài phạm vi (không phải KOC, hoặc đơn thường) → giữ
    nguyên hành vi cũ.
    """
    if not is_koc:
        return None
    normalized_type = str(order_type or "")
    if normalized_type == "no_return":
        return {"is_cod": False, "cod_amount": 0, "total_payment": total_after_discount}
    if normalized_type == "has_return":
        return {"is_cod": False, "cod_amount": 0, "total_payment": 0}
    return None


def _parse_sale_channel_id(raw_value) -> int | None:
    if raw_value in (None, ""):
        return None
    try:
        return int(str(raw_value).strip())
    except (TypeError, ValueError):
        return None


def _resolve_sale_channel_id_for_push(order: OrderModel, resolved_channel: KiotVietChannel | None) -> int | None:
    """
    Resolve saleChannelId for KiotViet payload.

    Tra cứu qua bảng ChannelKiotvietMapping: (order.channel, resolved_channel) ->
    external_sale_channel_id của cặp đó.
    """
    order_channel = getattr(order, "channel", None)
    if order_channel is None or resolved_channel is None:
        return None

    from apps.channels_core.models import ChannelKiotvietMapping
    mapping = (
        ChannelKiotvietMapping.objects
        .filter(channel=order_channel, kiotviet_channel=resolved_channel)
        .only("external_sale_channel_id")
        .first()
    )
    raw_value = mapping.external_sale_channel_id if mapping else None
    sale_channel_id = _parse_sale_channel_id(raw_value)
    if sale_channel_id is None and raw_value not in (None, ""):
        logger.warning(
            "Skip invalid saleChannelId from ChannelKiotvietMapping (order_id=%s, channel_id=%s, kv_channel_id=%s, value=%s)",
            order.id if order else None,
            order_channel.id,
            resolved_channel.id,
            raw_value,
        )
    return sale_channel_id


def _parse_kiot_user_id(raw_value) -> int | None:
    if raw_value in (None, ""):
        return None
    try:
        return int(str(raw_value).strip())
    except (TypeError, ValueError):
        return None


def _resolve_sold_by_id_for_push(
    *,
    order: OrderModel,
    requested_by_id: int | None,
    resolved_channel: KiotVietChannel | None,
) -> int | None:
    """
    Resolve soldById từ Order.staff qua bảng UserKiotvietMapping cho
    (staff, resolved_channel). requested_by_id intentionally ignored.
    """
    user_id_candidate = getattr(order, "staff_id", None)
    if user_id_candidate in (None, "") or resolved_channel is None:
        return None

    from apps.users.models import UserKiotvietMapping
    mapping = (
        UserKiotvietMapping.objects
        .filter(
            user_id=user_id_candidate,
            kiotviet_channel=resolved_channel,
            user__deleted_at__isnull=True,
        )
        .only("external_kiot_user_id")
        .first()
    )
    if mapping is None:
        logger.warning(
            "Cannot resolve soldById: no UserKiotvietMapping (order_id=%s, staff_id=%s, kv_id=%s, requested_by_id_ignored=%s)",
            order.id if order else None,
            user_id_candidate,
            resolved_channel.id,
            requested_by_id,
        )
        return None

    parsed_kiot_user_id = _parse_kiot_user_id(mapping.external_kiot_user_id)
    if parsed_kiot_user_id is None and mapping.external_kiot_user_id not in (None, ""):
        logger.warning(
            "Invalid soldById source on UserKiotvietMapping (order_id=%s, staff_id=%s, kv_id=%s, raw_value=%s)",
            order.id if order else None,
            user_id_candidate,
            resolved_channel.id,
            mapping.external_kiot_user_id,
        )
    return parsed_kiot_user_id


def update_invoice_delivery_status_by_id(
    *,
    invoice_id: int,
    status: int,
    order: OrderModel,
    channel: KiotVietChannel,
    price: Decimal | float | int | None = None,
    delivery_code: str | None = None,
) -> dict:
    """Update delivery status of invoice by id on KiotViet.

    KiotViet's ``PUT deliveryDetail`` is replace-style: any field omitted from the
    payload is reset on the invoice. When ``price`` / ``delivery_code`` are supplied
    (webhook status sync to the paired old channel) we send them alongside ``status``
    so KiotViet does not wipe them. Callers that omit them keep the old behaviour.
    """
    if invoice_id in (None, ""):
        raise ValueError("[stage=update_old_invoice_status] invoice_id is empty.")
    if status is None:
        raise ValueError("[stage=update_old_invoice_status] status is empty.")

    endpoint = f"/invoices/{int(invoice_id)}"
    delivery_detail = {
        "status": int(status),
        "partnerDelivery": {"name": "DVVC cho đơn DESI"},
    }
    if price is not None:
        delivery_detail["price"] = float(price or 0)
    code = _sanitize_text(delivery_code) if delivery_code is not None else ""
    if code:
        delivery_detail["deliveryCode"] = code
    payload = {"deliveryDetail": delivery_detail}
    response = _kiotviet_request(order=order, channel=channel, method="PUT", endpoint=endpoint, json=payload)
    response_body = (response.text or "").strip()
    try:
        parsed_response = response.json()
    except ValueError:
        parsed_response = None

    logger.info(
        "KiotViet update old invoice delivery status (order_id=%s, channel=%s, invoice_id=%s, status=%s): http_status=%s body=%s parsed=%s",
        order.id if order else None,
        channel.id if channel else None,
        invoice_id,
        status,
        response.status_code,
        _truncate_for_log(response_body),
        _truncate_for_log(parsed_response),
    )

    if response.status_code in {200, 201, 204}:
        return parsed_response if isinstance(parsed_response, dict) else {"raw_response": response_body}

    raise Exception(f"[stage=update_old_invoice_status] Failed to update old invoice status: {response.status_code} - {response_body}")


def update_invoice_delivery_price_by_code(*, invoice_code: str, price: Decimal | float | int, order: OrderModel, channel: KiotVietChannel) -> dict:
    """Update delivery price of invoice by invoice_code on KiotViet old channel."""
    code = _sanitize_text(invoice_code)
    if not code:
        raise ValueError("[stage=update_old_invoice_price] invoice_code is empty.")
    normalized_price = float(price or 0)

    payload = {"deliveryDetail": {"price": normalized_price}}
    endpoint = f"/invoices/code/{quote(code, safe='')}"
    response = _kiotviet_request(order=order, channel=channel, method="PUT", endpoint=endpoint, json=payload)
    response_body = (response.text or "").strip()
    try:
        parsed_response = response.json()
    except ValueError:
        parsed_response = None

    logger.info(
        "KiotViet update old invoice delivery price by code (order_id=%s, channel=%s, invoice_code=%s, price=%s): http_status=%s body=%s parsed=%s",
        order.id if order else None,
        channel.id if channel else None,
        code,
        normalized_price,
        response.status_code,
        _truncate_for_log(response_body),
        _truncate_for_log(parsed_response),
    )

    if response.status_code in {200, 201, 204}:
        return parsed_response if isinstance(parsed_response, dict) else {"raw_response": response_body}

    # Fallback: resolve id then update by id.
    invoice_payload = get_invoice_by_code(code, order, channel)
    invoice_id = _extract_invoice_id_from_invoice_payload(invoice_payload)
    if invoice_id is None:
        raise Exception(
            f"[stage=update_old_invoice_price] Failed to resolve invoice id for invoice_code={code}: "
            f"{response.status_code} - {response_body}"
        )

    fallback_endpoint = f"/invoices/{int(invoice_id)}"
    fallback_response = _kiotviet_request(
        order=order,
        channel=channel,
        method="PUT",
        endpoint=fallback_endpoint,
        json=payload,
    )
    fallback_body = (fallback_response.text or "").strip()
    try:
        fallback_parsed = fallback_response.json()
    except ValueError:
        fallback_parsed = None

    logger.info(
        "KiotViet update old invoice delivery price fallback by id (order_id=%s, channel=%s, invoice_id=%s, price=%s): http_status=%s body=%s parsed=%s",
        order.id if order else None,
        channel.id if channel else None,
        invoice_id,
        normalized_price,
        fallback_response.status_code,
        _truncate_for_log(fallback_body),
        _truncate_for_log(fallback_parsed),
    )

    if fallback_response.status_code in {200, 201, 204}:
        return fallback_parsed if isinstance(fallback_parsed, dict) else {"raw_response": fallback_body}

    raise Exception(
        f"[stage=update_old_invoice_price] Failed to update old invoice price: "
        f"{fallback_response.status_code} - {fallback_body}"
    )


def delete_invoice_by_id(*, invoice_id: int, order: OrderModel, channel: KiotVietChannel) -> dict:
    """Delete invoice by id on KiotViet."""
    if invoice_id in (None, ""):
        raise ValueError("[stage=delete_invoice] invoice_id is empty.")

    endpoint = "/invoices"
    payload = {"id": int(invoice_id)}
    response = _kiotviet_request(order=order, channel=channel, method="DELETE", endpoint=endpoint, json=payload)
    response_body = (response.text or "").strip()
    try:
        parsed_response = response.json()
    except ValueError:
        parsed_response = None

    logger.info(
        "KiotViet delete invoice (order_id=%s, channel=%s, invoice_id=%s): http_status=%s body=%s parsed=%s",
        order.id if order else None,
        channel.id if channel else None,
        invoice_id,
        response.status_code,
        _truncate_for_log(response_body),
        _truncate_for_log(parsed_response),
    )

    if response.status_code in {200, 201, 204}:
        return parsed_response if isinstance(parsed_response, dict) else {"raw_response": response_body}

    raise Exception(f"[stage=delete_invoice] Failed to delete invoice: {response.status_code} - {response_body}")


def push_order_to_kiotviet(
    order: OrderModel,
    channel: KiotVietChannel,
    requested_by_id: int = None,
    branch_override: str | int | None = None,
    add_old_auto_delivery_fields: bool = False,
    use_invoice_api: bool = False,
) -> dict:
    """
    Push order to KiotViet with customer check/create logic.

    Process:
    1. Check if customer exists by code, if not create new customer
    2. Build order data with proper address formatting
    3. Create order/invoice in KiotViet

    Args:
        order: Order model instance
        channel: KiotVietChannel to use
        requested_by_id: ID of user who requested the push (optional)
        use_invoice_api: If True, use POST /invoices instead of POST /orders

    Returns:
        Dictionary with push result information
    """
    from apps.orders.models import OrderItem

    # Use stable customer code to avoid creating duplicated customers on KiotViet.
    if order.customer_id and getattr(order.customer, "code", ""):
        customer_code = _sanitize_text(order.customer.code)
    else:
        customer_code = _sanitize_text(f"CUST-ORDER-{order.id:010d}")

    contact_number = _phone_for_kiotviet(
        getattr(order.customer, "phone_normalized", None),
        getattr(order.customer, "phone_raw", None),
        order.phone,
    )

    resolved_channel = _resolve_channel(order, channel)
    is_old_channel_push = bool(getattr(resolved_channel, "old", False)) if resolved_channel else False
    old_sync_branch_override = (
        branch_override
        if branch_override not in (None, "")
        else (getattr(resolved_channel, "old_sync_branch_id", None) if is_old_channel_push else None)
    )
    branch_id_for_push = _resolve_branch_id_for_push(order, old_sync_branch_override)
    sold_by_id = _resolve_sold_by_id_for_push(order=order, requested_by_id=requested_by_id, resolved_channel=resolved_channel)
    logger.info("KiotViet push started (order_id=%s, channel_id=%s)", order.id, resolved_channel.id if resolved_channel else None)

    old_channel_customer_prefetched: dict | None = None
    if is_old_channel_push and customer_code:
        try:
            existing_by_phone = None
            if contact_number:
                existing_by_phone = find_customer_by_phone(
                    phone=contact_number,
                    order=order,
                    channel=resolved_channel,
                )
            if isinstance(existing_by_phone, dict) and existing_by_phone:
                existing_code = _sanitize_text(
                    existing_by_phone.get("code")
                    or existing_by_phone.get("customerCode")
                    or ""
                )
                if existing_code:
                    customer_code = existing_code
                    old_channel_customer_prefetched = existing_by_phone
                    logger.info(
                        "Old KiotViet customer found by phone, reuse code (order_id=%s, channel_id=%s, customer_code=%s)",
                        order.id if order else None,
                        resolved_channel.id if resolved_channel else None,
                        customer_code,
                    )
            if old_channel_customer_prefetched is None:
                customer_code = _sanitize_text(f"{customer_code}.1")
                logger.info(
                    "Old KiotViet customer not found by phone, will create with suffix .1 (order_id=%s, channel_id=%s, customer_code=%s)",
                    order.id if order else None,
                    resolved_channel.id if resolved_channel else None,
                    customer_code,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Cannot pre-check old KiotViet customer by phone, fallback to default create flow (order_id=%s, channel_id=%s, base_customer_code=%s): %s",
                order.id if order else None,
                resolved_channel.id if resolved_channel else None,
                customer_code,
                exc,
            )

    # Build customer data
    customer_data = {
        "code": customer_code,
        "name": _sanitize_text(
            (order.customer.full_name if order.customer else "")
            or str(getattr(order, "name", "") or "").strip()
            or order.channel_owner
            or "Khách lẻ"
        ),
        "gender": _to_kiotviet_gender(order.customer.gender if order.customer else None, True),
        "contactNumber": contact_number,
        "address": _sanitize_text(order.address or order.raw_address or ""),
        "locationName": getattr(order, "kiotviet_location", "") or "",
        "wardName": getattr(order, "kiotviet_ward", "") or "",
        "branchId": branch_id_for_push,
    }

    # Get or create customer
    try:
        customer_result = get_or_create_customer(customer_data, order, resolved_channel)
    except Exception as exc:
        raise Exception(f"[stage=get_or_create_customer] {exc}") from exc
    customer_code_used = str(
        customer_result.get("code")
        or customer_result.get("customerCode")
        or customer_code
    ).strip()
    customer_code_used = _sanitize_text(customer_code_used)
    if not customer_code_used:
        raise Exception("[stage=push_order] Missing customer code after get_or_create_customer")

    # Old-channel push must prefer customer data from old KiotViet channel.
    # This avoids reusing stale customer fields from new-channel request context.
    if bool(getattr(resolved_channel, "old", False)) and customer_code_used:
        try:
            old_channel_customer = None
            if (
                isinstance(old_channel_customer_prefetched, dict)
                and old_channel_customer_prefetched
                and _sanitize_text(
                    old_channel_customer_prefetched.get("code")
                    or old_channel_customer_prefetched.get("customerCode")
                    or ""
                )
                == customer_code_used
            ):
                old_channel_customer = old_channel_customer_prefetched
            else:
                old_channel_customer = find_customer_by_code(
                    customer_code=customer_code_used,
                    order=order,
                    channel=resolved_channel,
                )
            if isinstance(old_channel_customer, dict) and old_channel_customer:
                customer_result = old_channel_customer
                logger.info(
                    "Resolved customer from old KiotViet channel before create order (order_id=%s, channel_id=%s, customer_code=%s, customer_id=%s)",
                    order.id if order else None,
                    resolved_channel.id if resolved_channel else None,
                    customer_code_used,
                    customer_result.get("id"),
                )
            else:
                logger.warning(
                    "Cannot re-fetch customer by code on old KiotViet channel, fallback to get_or_create_customer result (order_id=%s, channel_id=%s, customer_code=%s)",
                    order.id if order else None,
                    resolved_channel.id if resolved_channel else None,
                    customer_code_used,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to re-fetch customer on old KiotViet channel, fallback to get_or_create_customer result (order_id=%s, channel_id=%s, customer_code=%s): %s",
                order.id if order else None,
                resolved_channel.id if resolved_channel else None,
                customer_code_used,
                exc,
            )
    customer_name_used = _sanitize_text(customer_data.get("name") or "Khách lẻ") or "Khách lẻ"

    vat_tax_id = channel_vat_tax_id(resolved_channel)

    # Build order details from order items
    order_items = OrderItem.objects.filter(order=order).select_related("product")
    order_details = []
    total_amount = Decimal("0")

    for item in order_items:
        order_detail = {
            "productCode": item.sku or item.product.sku if item.product else f"SKU-{item.id}",
            "productName": item.product_name or (item.product.name if item.product else ""),
            "quantity": item.quantity,
            "price": int(float(item.unit_price or 0)),
            # KV hiểu `discount` là giảm cho MỖI sản phẩm — cùng nghĩa với
            # OrderItem.discount, không chia/nhân theo quantity.
            "discount": int(float(item.discount or 0)),
        }
        # Thuế dòng hàng đi theo cấu hình kênh, KHÔNG theo cờ `old`: kênh không kê khai
        # VAT (HKD) để vat_tax_id trống → không gửi *DetailTaxs.
        if vat_tax_id:
            if use_invoice_api:
                order_detail["InvoiceDetailTaxs"] = [{"TaxId": vat_tax_id}]
            else:
                order_detail["OrderDetailTaxs"] = [{"TaxId": vat_tax_id}]
        order_details.append(order_detail)
        total_amount += item.line_total

    total_after_discount_value = Decimal(order.total_after_discount or 0)
    total_after_discount_amount = int(float(total_after_discount_value or 0))

    # Build delivery information
    customer_prepaid_amount = int(float(order.customer_prepaid or 0))
    is_cod = customer_prepaid_amount < total_after_discount_amount  # COD if not fully prepaid
    cod_amount = max(0, total_after_discount_amount - customer_prepaid_amount)

    # Đơn KOC: "không trả lại" coi như đã trả đủ, "có trả lại" để công nợ — cả hai
    # đều không thu COD.
    order_channel = getattr(order, "channel", None)
    koc_payment = _koc_payment_override(
        bool(order_channel and order_channel.is_koc),
        getattr(order, "order_type", "") or "",
        total_after_discount_amount,
    )
    if koc_payment is not None:
        is_cod = koc_payment["is_cod"]
        cod_amount = koc_payment["cod_amount"]

    order_delivery = {
        "receiver": customer_name_used,
        "contactNumber": contact_number,
        "address": order.address or order.raw_address or "",
        "price": 0,
        "codAmount": cod_amount,
        "isCod": is_cod,
        "usingCod": is_cod,
        "usingPriceCod": is_cod,
        "receiveMoney": cod_amount,
    }

    if add_old_auto_delivery_fields:
        shipping_code_for_old_delivery = _sanitize_text(getattr(order, "shipping_code", ""))
        if shipping_code_for_old_delivery:
            order_delivery["deliveryCode"] = shipping_code_for_old_delivery
        order_delivery["partnerDelivery"] = {
            "name": "DVVC cho đơn DESI",
        }

    # Add optional delivery fields if available
    if hasattr(order, 'weight') and order.weight and order.weight > 0:
        order_delivery["weight"] = int(float(order.weight or 0))

    # Add optional dimensions in cm if available (AxBxC -> length/width/height)
    parsed_dimensions = _parse_dimensions(getattr(order, "dimensions", None))
    if parsed_dimensions:
        length, width, height = parsed_dimensions
        order_delivery["length"] = length
        order_delivery["width"] = width
        order_delivery["height"] = height

    # Add location info to delivery from pre-resolved KiotViet fields
    kv_location = getattr(order, "kiotviet_location", "") or ""
    kv_ward = getattr(order, "kiotviet_ward", "") or ""
    if kv_location:
        order_delivery["locationName"] = kv_location
    if kv_ward:
        order_delivery["wardName"] = kv_ward

    customer_id_used = customer_result.get("id")
    try:
        customer_id_used = int(customer_id_used) if customer_id_used is not None else None
    except (TypeError, ValueError):
        customer_id_used = None

    customer_block = {
        "code": customer_code_used,  # from create/get customer response
        "name": customer_name_used,
        "gender": _to_kiotviet_gender(customer_data.get("gender"), True),
        "birthDate": "",
        "contactNumber": _sanitize_text(customer_data.get("contactNumber") or contact_number or ""),
        "address": _sanitize_text(customer_data.get("address") or ""),
        "email": "",
        "comment": "",
    }
    if customer_id_used is not None:
        customer_block["id"] = customer_id_used

    sale_channel_id = _resolve_sale_channel_id_for_push(order, resolved_channel)

    purchase_date = timezone.localtime(order.created_at).strftime("%Y-%m-%d %H:%M:%S") if order.created_at else timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
    if is_old_channel_push:
        source_branch_id = getattr(getattr(order, "kiotviet_channel", None), "branch_id", "") or ""
        source_invoice_code = str(order.invoice_code or "").strip()
        order_description = _sanitize_text(
            f"SYNC từ kênh {source_branch_id} - {source_invoice_code}"
        )
    else:
        order_description = _sanitize_text(
            f"Đơn hàng từ {order.channel.name}" if order.channel else "Đơn hàng online"
        )
    discount_amount = int(float(order.discount_amount or 0)) + int(float(order.booking_voucher_discount or 0))
    payment_method = str(getattr(order, "payment_method", "") or "").strip() or "Cash"

    # Build order data (cả new và old channel đều dùng create_order)
    order_data = {
        "isApplyVoucher": False,
        "purchaseDate": purchase_date,
        "branchId": branch_id_for_push,
        "customerCode": customer_code_used,  # keep for backward compatibility
        "customer": customer_block,
        "description": order_description,
        "makeInvoice": True,
        "saleChannelId": sale_channel_id,
        "orderDetails": order_details,
        "orderDelivery": order_delivery,
        # pricingMode 1 = giá chưa thuế (KV cộng VAT lên), chỉ đúng khi kênh có thuế dòng.
        "pricingMode": 1 if vat_tax_id else 0,
        "discountAfterTax": discount_amount,
        "discount": discount_amount,
    }
    if koc_payment is not None:
        # KOC: "có trả lại" → totalPayment=0 (công nợ); "không trả lại" → trả đủ.
        order_data["totalPayment"] = koc_payment["total_payment"]
        order_data["method"] = payment_method
    elif str(getattr(order, "order_type", "") or "") == "has_return":
        # has_return ngoài KOC: giữ hành vi cũ — đánh đã trả đủ.
        order_data["totalPayment"] = total_after_discount_amount
        order_data["method"] = payment_method
    elif customer_prepaid_amount > 0:
        order_data["totalPayment"] = customer_prepaid_amount
        order_data["method"] = payment_method
    if sale_channel_id is None:
        order_data.pop("saleChannelId", None)
    if sold_by_id is not None:
        order_data["soldById"] = sold_by_id

    logger.info(
        "KiotViet create %s payload summary (order_id=%s, customer_code=%s, customer_id=%s, has_customer_obj=%s, item_count=%s, total_estimate=%s, total_after_discount=%s, shipping_fee=%s, discount=%s, sold_by_id=%s, is_old_channel=%s, prepaid=%s)",
        "invoice" if use_invoice_api else "order",
        order.id if order else None,
        customer_code_used,
        customer_id_used,
        bool(order_data.get("customer")),
        len(order_details),
        int(float(total_amount or 0)),
        int(float(total_after_discount_value)),
        int(float(order.shipping_fee or 0)),
        int(float(order.discount_amount or 0)),
        sold_by_id,
        is_old_channel_push,
        customer_prepaid_amount,
    )

    if use_invoice_api:
        # Use POST /invoices (KiotViet invoice API)
        # - deliveryDetail (not invoiceDelivery)
        # - totalPayment at top level for cash payment
        # - method for payment method
        invoice_data = {
            "branchId": branch_id_for_push,
            "purchaseDate": purchase_date,
            "customerCode": customer_code_used,
            "customer": customer_block,
            "description": order_description,
            "saleChannelId": sale_channel_id,
            "invoiceDetails": order_details,
            "discount": discount_amount,
            "totalPayment": (
                koc_payment["total_payment"] if koc_payment is not None else customer_prepaid_amount
            ),
            "method": payment_method,
        }
        if sale_channel_id is None:
            invoice_data.pop("saleChannelId", None)
        if sold_by_id is not None:
            invoice_data["soldById"] = sold_by_id

        try:
            invoice_result = create_invoice(invoice_data, order, resolved_channel)
        except Exception as exc:
            raise Exception(f"[stage=create_invoice] {exc}") from exc

        # Normalize result to match order_result structure for service_actions.
        # KiotViet trả response có thể là camelCase hoặc PascalCase tuỳ kênh,
        # nên check tất cả biến thể trước khi rơi về rỗng.
        invoice_code = str(
            invoice_result.get("code")
            or invoice_result.get("Code")
            or invoice_result.get("invoiceCode")
            or invoice_result.get("InvoiceCode")
            or ""
        ).strip()
        invoice_id = (
            invoice_result.get("id")
            or invoice_result.get("Id")
            or invoice_result.get("invoiceId")
            or invoice_result.get("InvoiceId")
        )
        if not invoice_code:
            # Nếu KiotViet trả 200 nhưng không kèm mã hóa đơn thì coi như thất bại
            # để job ở trạng thái FAILED thay vì ghi đè order.invoice_code rỗng.
            raise Exception(
                f"[stage=create_invoice] Phản hồi KiotViet thiếu mã hóa đơn (response={invoice_result!r})"
            )
        # Loại các key đã được normalize khỏi spread để raw response (nếu có
        # cùng key với giá trị khác/null) không đè giá trị chuẩn hoá ở trên.
        _normalized_keys = {
            "code",
            "Code",
            "id",
            "Id",
            "invoiceCode",
            "InvoiceCode",
            "invoiceId",
            "InvoiceId",
            "invoices",
        }
        return {
            "success": True,
            "customer_code": customer_code_used,
            "make_invoice": True,
            "order_result": {
                **{k: v for k, v in invoice_result.items() if k not in _normalized_keys},
                "code": invoice_code,
                "invoiceCode": invoice_code,
                "invoiceId": invoice_id,
                "invoices": [{"invoiceCode": invoice_code, "invoiceId": invoice_id}],
            },
            "message": "Invoice created on KiotViet successfully",
        }

    try:
        order_result = create_order(order_data, order, resolved_channel)
    except Exception as exc:
        raise Exception(f"[stage=push_order] {exc}") from exc

    return {
        "success": True,
        "customer_code": customer_code_used,
        "make_invoice": True,
        "order_result": order_result,
        "message": "Order pushed to KiotViet successfully",
    }
