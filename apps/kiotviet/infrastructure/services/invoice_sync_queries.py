import copy
import json
from urllib.parse import quote

from django.conf import settings

from apps.kiotviet.models import KiotVietChannel, channel_vat_tax_id
from apps.orders.models import Order as OrderModel

from .common_auth_customer import (
    KiotVietApiError,
    KiotVietInvoiceNotFound,
    _extract_prefixed_code,
    _sanitize_payload_value,
    _sanitize_text,
    _truncate_for_log,
    logger,
)
from .gateway_kship_customer_webhook import _kiotviet_request


def create_order(order_data: dict, order: OrderModel, channel: KiotVietChannel) -> dict:
    """
    Create an order in KiotViet.

    Args:
        order_data: Order data dictionary
        channel: KiotVietChannel instance

    Returns:
        Order data from KiotViet API response
    """
    try:
        sanitized_order_data = _sanitize_payload_value(order_data)
        if bool(getattr(settings, "KIOTVIET_DEBUG_CREATE_ORDER_PAYLOAD", False)):
            logger.info(
                "KiotViet create order payload debug (order_id=%s, channel=%s, endpoint=/orders):\n%s",
                order.id if order else None,
                channel.id if channel else None,
                _truncate_for_log(json.dumps(sanitized_order_data, ensure_ascii=False, indent=2), 12000),
            )
        response = _kiotviet_request(order=order, channel=channel, method="POST", endpoint="/orders", json=sanitized_order_data)
        response_body = (response.text or "").strip()
        try:
            parsed_response = response.json()
        except ValueError:
            parsed_response = None

        logger.info(
            "KiotViet create order response (order_id=%s, channel=%s): status=%s body=%s parsed=%s",
            order.id if order else None,
            channel.id if channel else None,
            response.status_code,
            _truncate_for_log(response_body),
            _truncate_for_log(parsed_response),
        )

        if response.status_code in (200, 201):
            created_order = parsed_response if isinstance(parsed_response, dict) else {"raw_response": response_body}
            order_ref = (
                created_order.get("code")
                or created_order.get("orderCode")
                or created_order.get("id")
                or "unknown"
            )
            logger.info("Order created successfully: %s", order_ref)
            return created_order
        else:
            logger.error(
                "Failed to create order on KiotViet (order_id=%s, channel=%s): status=%s body=%s payload=%s",
                order.id if order else None,
                channel.id if channel else None,
                response.status_code,
                _truncate_for_log(response_body),
                _truncate_for_log(sanitized_order_data),
            )
            raise Exception(f"[stage=create_order] Failed to create order: {response.status_code} - {response_body}")
    except Exception as e:
        logger.error(f"Error creating order: {e}")
        raise


def _is_location_validation_error(status_code: int, response_body: str) -> bool:
    """Nhận diện lỗi KiotViet reject location/ward (vd khác phiên bản cấu trúc địa giới).

    KiotViet trả 420 với errorCode KvValidateLocationException khi locationName/wardName
    không tồn tại trong danh mục của retailer đích ("Khu vực không tồn tại" /
    "Phường xã không tồn tại").
    """
    if status_code != 420:
        return False
    body = (response_body or "").lower()
    return "kvvalidatelocation" in body or "phường xã" in body or "khu vực" in body


def _strip_location_fields(invoice_data: dict, *, drop_location: bool) -> dict:
    """Trả về bản copy của invoice_data đã bỏ wardName (và locationName nếu drop_location).

    Bỏ ở cả khối customer lẫn deliveryDetail vì KiotViet validate location/ward ở cả hai.
    Đơn vẫn được tạo từ `address` text nên không mất thông tin giao hàng.
    """
    cleaned = copy.deepcopy(invoice_data)
    fields = ["wardName", "WardName"]
    if drop_location:
        fields += ["locationName", "LocationName"]
    for block_key in ("customer", "Customer", "deliveryDetail", "DeliveryDetail"):
        block = cleaned.get(block_key)
        if isinstance(block, dict):
            for field in fields:
                block.pop(field, None)
    return cleaned


def create_invoice(invoice_data: dict, order: OrderModel, channel: KiotVietChannel) -> dict:
    """
    Create an invoice in KiotViet old API.

    Khi KiotViet reject location/ward (vd 2 retailer khác phiên bản cấu trúc địa giới),
    tự động hạ cấp payload: bỏ wardName rồi bỏ cả locationName và retry, để đơn vẫn được
    tạo từ address text thay vì fail toàn bộ.

    Args:
        invoice_data: Invoice data dictionary
        order: Order model instance
        channel: KiotVietChannel instance

    Returns:
        Invoice data from KiotViet API response
    """
    try:
        # Các biến thể payload thử lần lượt khi gặp lỗi location (lazy để happy path
        # không tốn deepcopy thừa): (1) nguyên bản -> (2) bỏ wardName -> (3) bỏ cả
        # wardName + locationName.
        attempts = (
            ("full", lambda: invoice_data),
            ("no-ward", lambda: _strip_location_fields(invoice_data, drop_location=False)),
            ("no-location", lambda: _strip_location_fields(invoice_data, drop_location=True)),
        )
        last_status: int | None = None
        last_body: str = ""
        for attempt_label, build_attempt_data in attempts:
            sanitized_invoice_data = _sanitize_payload_value(build_attempt_data())
            if bool(getattr(settings, "KIOTVIET_DEBUG_CREATE_ORDER_PAYLOAD", False)):
                logger.info(
                    "KiotViet create invoice payload debug (order_id=%s, channel=%s, attempt=%s, endpoint=/invoices):\n%s",
                    order.id if order else None,
                    channel.id if channel else None,
                    attempt_label,
                    _truncate_for_log(json.dumps(sanitized_invoice_data, ensure_ascii=False, indent=2), 12000),
                )
            response = _kiotviet_request(order=order, channel=channel, method="POST", endpoint="/invoices", json=sanitized_invoice_data)
            response_body = (response.text or "").strip()
            try:
                parsed_response = response.json()
            except ValueError:
                parsed_response = None

            logger.info(
                "KiotViet create invoice response (order_id=%s, channel=%s, attempt=%s): status=%s body=%s parsed=%s",
                order.id if order else None,
                channel.id if channel else None,
                attempt_label,
                response.status_code,
                _truncate_for_log(response_body),
                _truncate_for_log(parsed_response),
            )

            if response.status_code in (200, 201):
                created_invoice = parsed_response if isinstance(parsed_response, dict) else {"raw_response": response_body}
                invoice_ref = (
                    created_invoice.get("code")
                    or created_invoice.get("invoiceCode")
                    or created_invoice.get("id")
                    or "unknown"
                )
                if attempt_label != "full":
                    logger.warning(
                        "Invoice created after stripping location fields (attempt=%s): %s",
                        attempt_label, invoice_ref,
                    )
                else:
                    logger.info("Invoice created successfully: %s", invoice_ref)
                return created_invoice

            last_status = response.status_code
            last_body = response_body

            # Chỉ retry khi đúng là lỗi location và vẫn còn biến thể tiếp theo để thử.
            if _is_location_validation_error(response.status_code, response_body) and attempt_label != "no-location":
                logger.warning(
                    "KiotViet location validation failed (attempt=%s), retrying with fewer location fields. body=%s",
                    attempt_label, _truncate_for_log(response_body),
                )
                continue
            break

        logger.error(
            "Failed to create invoice on KiotViet (order_id=%s, channel=%s): status=%s body=%s",
            order.id if order else None,
            channel.id if channel else None,
            last_status,
            _truncate_for_log(last_body),
        )
        raise Exception(f"[stage=create_invoice] Failed to create invoice: {last_status} - {last_body}")
    except Exception as e:
        logger.error(f"Error creating invoice: {e}")
        raise


def get_invoice_by_code(
    invoice_code: str,
    order: OrderModel,
    channel: KiotVietChannel,
    timeout: float | None = None,
) -> dict:
    """Fetch invoice detail by invoice code from KiotViet.

    `timeout` (giây) là tùy chọn; bỏ trống → giữ nguyên timeout mặc định của gateway.
    """
    code = _sanitize_text(invoice_code)
    if not code:
        raise ValueError("[stage=get_invoice] invoice_code is empty.")

    endpoint = f"/invoices/code/{quote(code, safe='')}"
    params = {
        "includePayment": "true",
        "includeInvoiceDelivery": "true",
        # Keep PascalCase variants for upstream compatibility with old/new KiotViet gateways.
        "IncludePayment": "true",
        "IncludeInvoiceDelivery": "true",
    }
    extra_kwargs = {} if timeout is None else {"timeout": timeout}
    response = _kiotviet_request(
        order=order,
        channel=channel,
        method="GET",
        endpoint=endpoint,
        params=params,
        **extra_kwargs,
    )
    response_body = (response.text or "").strip()
    try:
        parsed_response = response.json()
    except ValueError:
        parsed_response = None

    logger.info(
        "KiotViet get invoice response (order_id=%s, channel=%s, invoice_code=%s): status=%s body=%s parsed=%s",
        order.id if order else None,
        channel.id if channel else None,
        code,
        response.status_code,
        _truncate_for_log(response_body),
        _truncate_for_log(parsed_response),
    )

    if response.status_code == 200:
        if isinstance(parsed_response, dict):
            return parsed_response
        return {"raw_response": response_body}

    message = f"[stage=get_invoice] Failed to get invoice: {response.status_code} - {response_body}"
    # 404 = hóa đơn không tồn tại trên channel này: câu trả lời DỨT KHOÁT, retry vô nghĩa.
    # Tách khỏi lỗi hạ tầng (timeout/5xx/rate-limit) để caller quyết định fail-open/fail-closed.
    if response.status_code == 404:
        raise KiotVietInvoiceNotFound(message)
    raise KiotVietApiError(message, status_code=response.status_code)


def _extract_invoice_items(payload: dict | list | None) -> list[dict]:
    """Extract invoice list from multiple KiotViet response shapes."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    data_obj = payload.get("data")
    if isinstance(data_obj, dict):
        for key in ["items", "results", "invoices", "data"]:
            nested = data_obj.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]

    for key in ["data", "items", "results", "invoices"]:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    if payload.get("id") and (payload.get("code") or payload.get("invoiceCode")):
        return [payload]
    return []


def get_invoice_by_order_code(order_code: str, order: OrderModel, channel: KiotVietChannel) -> dict | None:
    """
    Fetch first invoice by order code from KiotViet.
    KiotViet responses are inconsistent, so we try multiple query shapes.
    """
    code = _sanitize_text(order_code)
    if not code:
        return None

    request_attempts = (
        {"params": {"orderCode": code, "pageSize": 5}},
        {"params": {"code": code, "pageSize": 5}},
        {"params": {"keyword": code, "pageSize": 5}},
    )
    for kwargs in request_attempts:
        response = _kiotviet_request(order=order, channel=channel, method="GET", endpoint="/invoices", **kwargs)
        response_body = (response.text or "").strip()
        try:
            parsed_response = response.json()
        except ValueError:
            parsed_response = None

        logger.info(
            "KiotViet get invoice by order_code (order_id=%s, channel=%s, order_code=%s): status=%s body=%s parsed=%s",
            order.id if order else None,
            channel.id if channel else None,
            code,
            response.status_code,
            _truncate_for_log(response_body),
            _truncate_for_log(parsed_response),
        )

        if response.status_code != 200:
            continue

        items = _extract_invoice_items(parsed_response)
        if not items:
            continue

        for item in items:
            item_order_code = _sanitize_text(item.get("orderCode") or item.get("order_code") or "")
            if item_order_code and item_order_code == code:
                return item

        # Fallback: first item when upstream already filtered.
        return items[0]
    return None


def _is_invoice_not_found_response(status_code: int, parsed_response: dict | None, response_body: str) -> bool:
    """
    Detect "invoice not found" responses from KiotViet across inconsistent shapes/status codes.
    """
    parsed = parsed_response if isinstance(parsed_response, dict) else {}
    response_status = parsed.get("responseStatus") if isinstance(parsed, dict) else None
    response_status = response_status if isinstance(response_status, dict) else {}
    error_code = str(response_status.get("errorCode") or "").strip()
    message = str(response_status.get("message") or "").strip().lower()
    body_text = str(response_body or "").strip().lower()

    if status_code == 404:
        return True
    if error_code in {"KvValidateInvoiceException", "NotFound", "InvoiceNotFound"}:
        return True
    if "không còn tồn tại" in message or "không tồn tại" in message:
        return True
    if "không còn tồn tại" in body_text or "không tồn tại" in body_text:
        return True
    return False


def _invoice_exists_by_id(invoice_id: int, order: OrderModel, channel: KiotVietChannel) -> bool:
    """
    Validate invoice existence by id on a specific channel.
    Returns:
      - True  => invoice exists
      - False => invoice definitely not found
    Raises:
      - Exception for transient/unexpected upstream errors
    """
    endpoint = f"/invoices/{int(invoice_id)}"
    response = _kiotviet_request(order=order, channel=channel, method="GET", endpoint=endpoint)
    response_body = (response.text or "").strip()
    try:
        parsed_response = response.json()
    except ValueError:
        parsed_response = None

    logger.info(
        "KiotViet check invoice by id (order_id=%s, channel_id=%s, invoice_id=%s): status=%s body=%s parsed=%s",
        order.id if order else None,
        channel.id if channel else None,
        invoice_id,
        response.status_code,
        _truncate_for_log(response_body),
        _truncate_for_log(parsed_response),
    )

    if response.status_code == 200:
        return True
    if _is_invoice_not_found_response(response.status_code, parsed_response if isinstance(parsed_response, dict) else None, response_body):
        return False

    raise Exception(
        f"[stage=check_old_invoice] Failed to verify old invoice existence: "
        f"{response.status_code} - {response_body}"
    )


def _resolve_paired_old_channel(channel: KiotVietChannel | None) -> KiotVietChannel | None:
    """Resolve mapped old KiotViet channel from a new channel."""
    if channel is None:
        return None
    if bool(getattr(channel, "old", False)):
        return None

    paired = getattr(channel, "paired_old_channel", None)
    if paired is None:
        return None
    if paired.deleted_at is not None:
        raise ValueError("Kênh KiotViet cũ đã ghép đang bị xóa mềm.")
    if not bool(getattr(paired, "old", False)):
        raise ValueError("Kênh đã ghép không phải kênh KiotViet cũ.")
    return paired


def _extract_invoice_sync_data(order_result: dict | None) -> tuple[str, int | None]:
    payload = order_result or {}
    # Ưu tiên field invoice tường minh trước, chỉ fallback recursive scan khi cần.
    old_invoice_code = str(
        payload.get("invoiceCode")
        or payload.get("invoiceNo")
        or payload.get("invoiceNumber")
        or ""
    ).strip()
    if not old_invoice_code:
        code_candidate = str(payload.get("code") or "").strip()
        if code_candidate.upper().startswith("HD"):
            old_invoice_code = code_candidate
    if not old_invoice_code:
        old_invoice_code = str(_extract_prefixed_code(payload, "HD") or "").strip()
    # NOTE:
    # - Create order response trả về id của ORDER, không phải id của INVOICE.
    # - old_id phải lấy từ API invoice detail (/invoices/code/{code}).
    # Vì vậy helper này chỉ extract old_invoice_code từ order response.
    old_invoice_id = None
    return old_invoice_code, old_invoice_id


def _extract_invoice_id_from_invoice_payload(invoice_payload: dict | None) -> int | None:
    payload = invoice_payload if isinstance(invoice_payload, dict) else {}
    invoice_id_raw = (
        payload.get("id")
        or payload.get("invoiceId")
        or payload.get("invoice_id")
    )
    try:
        return int(invoice_id_raw) if invoice_id_raw is not None else None
    except (TypeError, ValueError):
        return None


def _extract_invoice_code_from_invoice_payload(invoice_payload: dict | None) -> str:
    payload = invoice_payload if isinstance(invoice_payload, dict) else {}
    invoice_code = str(
        payload.get("code")
        or payload.get("invoiceCode")
        or payload.get("invoiceNo")
        or payload.get("invoiceNumber")
        or ""
    ).strip()
    return invoice_code


def _resolve_branch_id_for_push(order: OrderModel, branch_override: str | int | None = None) -> int:
    """
    Resolve branch id for KiotViet push.
    Always uses order.branch_id. branch_override only for old Kiot sync.
    Raises if branch_id is missing or invalid.
    """
    candidate = branch_override if branch_override not in (None, "") else order.branch_id
    if candidate in (None, ""):
        raise ValueError(
            f"[stage=resolve_branch] Thiếu mã kho (order_id={order.id if order else None})."
        )
    try:
        return int(str(candidate).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"[stage=resolve_branch] Mã kho không hợp lệ "
            f"(order_id={order.id if order else None}, override={branch_override}, order_branch={order.branch_id})."
        ) from exc


def _ensure_old_channel_invoice(
    order: OrderModel,
    new_channel: KiotVietChannel,
    actor_id: int | None = None,
    add_old_auto_delivery_fields: bool = False,
) -> dict:
    """
    Ensure the mirrored invoice exists on paired old channel.
    IMPORTANT: this helper must be called only after push to new KiotViet succeeds.
    """
    paired_old_channel = _resolve_paired_old_channel(new_channel)
    result = {
        "enabled": bool(paired_old_channel),
        "pushed": False,
        "skipped": False,
        "old_invoice_code": str(order.old_invoice_code or "").strip(),
        "old_id": order.old_id,
    }

    if paired_old_channel is None:
        result["skipped"] = True
        result["reason"] = "old_channel_not_configured"
        logger.warning(
            "KiotViet old channel sync skipped (order_id=%s, new_channel_id=%s, reason=%s)",
            order.id if order else None,
            new_channel.id if new_channel else None,
            result["reason"],
        )
        return result

    if order.old_id:
        try:
            old_invoice_exists = _invoice_exists_by_id(order.old_id, order, paired_old_channel)
        except Exception as exc:
            result["skipped"] = True
            result["reason"] = "old_invoice_check_failed"
            result["error"] = str(exc)
            logger.warning(
                "KiotViet old invoice check failed, skip old sync (order_id=%s, new_channel_id=%s, old_channel_id=%s, old_id=%s, error=%s)",
                order.id if order else None,
                new_channel.id if new_channel else None,
                paired_old_channel.id if paired_old_channel else None,
                order.old_id,
                str(exc),
            )
            return result
        if old_invoice_exists:
            result["skipped"] = True
            result["reason"] = "old_invoice_already_exists"
            logger.info(
                "KiotViet old channel sync skipped (order_id=%s, new_channel_id=%s, old_channel_id=%s, old_id=%s, reason=%s)",
                order.id if order else None,
                new_channel.id if new_channel else None,
                paired_old_channel.id if paired_old_channel else None,
                order.old_id,
                result["reason"],
            )
            return result

        logger.warning(
            "KiotViet old invoice id is stale, will recreate on old channel (order_id=%s, new_channel_id=%s, old_channel_id=%s, stale_old_id=%s)",
            order.id if order else None,
            new_channel.id if new_channel else None,
            paired_old_channel.id if paired_old_channel else None,
            order.old_id,
        )
        result["stale_old_id"] = order.old_id

    from .branch_sync_map import resolve_old_sync_target_branch_id

    target_branch_id = resolve_old_sync_target_branch_id(
        new_channel, paired_old_channel, order.branch_id
    )
    if target_branch_id in (None, ""):
        raise ValueError(
            "[stage=push_old_channel] Missing target branch id "
            "(old_sync_branch_map/old_sync_branch_id on new channel or branch_id on paired old channel)."
        )
    try:
        target_branch_id_int = int(str(target_branch_id).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"[stage=push_old_channel] Invalid target branch id: {target_branch_id}"
        ) from exc

    logger.info(
        "KiotViet old channel sync started (order_id=%s, new_channel_id=%s, old_channel_id=%s, target_branch_id=%s)",
        order.id if order else None,
        new_channel.id if new_channel else None,
        paired_old_channel.id if paired_old_channel else None,
        target_branch_id_int,
    )

    sync_result = sync_invoice_new_to_old(
        order=order,
        new_channel=new_channel,
        paired_old_channel=paired_old_channel,
        target_branch_id=target_branch_id_int,
    )

    result.update(
        {
            "pushed": True,
            "old_invoice_code": sync_result["old_invoice_code"],
            "old_id": sync_result["old_id"],
            "api_result": sync_result.get("api_result"),
            "invoice_detail": sync_result.get("invoice_detail"),
        }
    )
    old_invoice_code = sync_result["old_invoice_code"]
    old_invoice_id = sync_result["old_id"]
    logger.info(
        "KiotViet old channel sync completed (order_id=%s, old_channel_id=%s, old_id=%s, old_invoice_code=%s)",
        order.id if order else None,
        paired_old_channel.id if paired_old_channel else None,
        old_invoice_id,
        old_invoice_code,
    )
    return result


def sync_invoice_new_to_old(
    *,
    order: OrderModel,
    new_channel: KiotVietChannel,
    paired_old_channel: KiotVietChannel,
    target_branch_id: int,
) -> dict:
    """
    Đồng bộ 1 invoice từ kiot mới sang kiot cũ theo cùng luồng sync command:
      1) Fetch invoice detail từ kiot mới (theo order.invoice_code).
      2) Resolve customer trên kiot cũ (find by phone, fallback create).
      3) Build payload qua shared builder (thuế theo vat_tax_id của kênh cũ).
      4) POST /invoices trên kiot cũ.
      5) Patch invoice + delivery status về giá trị gốc.
      6) Fetch lại invoice trên kiot cũ để lấy old_id chính xác.

    Returns dict: {old_invoice_code, old_id, invoice_detail, api_result}.
    Raise nếu thiếu invoice_code, không tạo được, hoặc không lấy được old_id.
    """
    from .invoice_payload_builder import (
        CustomerCodeMissingError,
        build_target_invoice_payload,
        patch_invoice_status_after_create,
        resolve_target_customer,
    )

    invoice_code = str(getattr(order, "invoice_code", "") or "").strip()
    if not invoice_code:
        raise ValueError("[stage=sync_new_to_old] Order missing invoice_code")

    source_invoice = get_invoice_by_code(invoice_code, order, new_channel) or {}
    if not source_invoice:
        raise ValueError(
            f"[stage=sync_new_to_old] Cannot fetch source invoice (code={invoice_code}) on new channel"
        )

    delivery = source_invoice.get("invoiceDelivery") if isinstance(source_invoice.get("invoiceDelivery"), dict) else {}
    if not delivery:
        delivery = source_invoice.get("InvoiceDelivery") if isinstance(source_invoice.get("InvoiceDelivery"), dict) else {}
    delivery = delivery if isinstance(delivery, dict) else {}

    try:
        customer_block, customer_code, customer_name = resolve_target_customer(
            source_invoice=source_invoice,
            delivery=delivery,
            target_channel=paired_old_channel,
            target_branch_id=int(target_branch_id),
            order=order,
        )
    except CustomerCodeMissingError as exc:
        raise ValueError(f"[stage=sync_new_to_old] {exc}") from exc

    built = build_target_invoice_payload(
        source_invoice=source_invoice,
        target_branch_id=int(target_branch_id),
        vat_tax_id=channel_vat_tax_id(paired_old_channel),
        customer_block=customer_block,
        customer_code=customer_code,
        customer_name=customer_name,
        source_channel=new_channel,
        target_channel=paired_old_channel,
    )
    payload = built["payload"]
    source_invoice_status = built["source_invoice_status"]
    source_delivery_status = built["source_delivery_status"]
    source_delivery_code = built.get("source_delivery_code") or ""
    source_delivery_price = built.get("source_delivery_price")

    logger.info(
        "sync_invoice_new_to_old: posting payload (order_id=%s, source_code=%s, target_branch=%s, old_channel_id=%s)",
        order.id if order else None,
        invoice_code,
        target_branch_id,
        paired_old_channel.id if paired_old_channel else None,
    )
    created = create_invoice(payload, order=order, channel=paired_old_channel)
    created_invoice_id = created.get("id") or created.get("Id")
    if created_invoice_id:
        try:
            patch_invoice_status_after_create(
                target_channel=paired_old_channel,
                created_invoice_id=int(created_invoice_id),
                source_invoice_status=source_invoice_status,
                source_delivery_status=source_delivery_status,
                source_delivery_code=source_delivery_code,
                source_delivery_price=source_delivery_price,
                order=order,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "sync_invoice_new_to_old: patch status failed (order_id=%s, created_id=%s): %s",
                order.id if order else None,
                created_invoice_id,
                exc,
            )

    created_code = str(created.get("code") or created.get("invoiceCode") or "").strip()
    if not created_code:
        raise ValueError("[stage=sync_new_to_old] create_invoice response missing code")

    invoice_detail = get_invoice_by_code(created_code, order, paired_old_channel)
    old_invoice_id = _extract_invoice_id_from_invoice_payload(invoice_detail)
    code_from_detail = _extract_invoice_code_from_invoice_payload(invoice_detail)
    if code_from_detail:
        created_code = code_from_detail
    if old_invoice_id is None:
        raise ValueError(
            "[stage=sync_new_to_old] Failed to resolve old invoice id from /invoices/code response"
        )

    return {
        "old_invoice_code": created_code,
        "old_id": old_invoice_id,
        "invoice_detail": invoice_detail,
        "api_result": created,
    }

