import json

import requests
from django.conf import settings
from django.core.cache import cache

from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order as OrderModel

from .common_auth_customer import (
    KIOTVIET_WEB_API_BASE_URL,
    _client_for_method,
    _extract_list_payload,
    _kiotviet_request,  # noqa: F401 — re-exported: mọi caller cũ import từ module này
    _kiotviet_request_with_retry,  # noqa: F401 — re-exported (BE-5, retry 5xx dùng chung)
    _normalize_customer_payload,
    _resolve_channel,
    _sanitize_payload_value,
    _sanitize_text,
    _truncate_for_log,
    find_customer_by_code,
    find_customer_by_phone,
    get_web_access_token,
    http_mutate,
    http_read,
    logger,
)


def _kiotviet_web_request(
    *,
    order: OrderModel | None = None,
    channel: KiotVietChannel,
    method: str,
    endpoint: str,
    **kwargs,
) -> requests.Response:
    """Send authenticated request to KiotViet web API."""
    # Cùng chốt chặn staging với _kiotviet_request (audit 2026-07-18).
    if getattr(settings, "IS_STAGING", False):
        raise RuntimeError(
            "KiotViet web outbound bị chặn trên staging (APP_ENV=staging)."
        )
    resolved_channel = _resolve_channel(order, channel)
    token = get_web_access_token(order=order, channel=resolved_channel)
    endpoint_value = str(endpoint or "").strip()
    if endpoint_value.lower().startswith(("http://", "https://")):
        url = endpoint_value
    else:
        base_url = str(getattr(settings, "KIOTVIET_WEB_API_BASE_URL", KIOTVIET_WEB_API_BASE_URL)).rstrip("/")
        url = f"{base_url}/{endpoint_value.lstrip('/')}"

    headers = kwargs.pop("headers", {}) or {}
    headers["Authorization"] = f"Bearer {token}"
    headers["Token"] = token
    headers["retailer"] = str(resolved_channel.retailer or "").strip()
    headers.setdefault("Content-Type", "application/json")

    logger.info(
        "KiotViet web request: method=%s url=%s headers=%s kwargs_keys=%s",
        method,
        url,
        {k: (v[:20] + "..." if k == "Token" and len(str(v)) > 20 else v) for k, v in headers.items()},
        list(kwargs.keys()),
    )

    # Retry chỉ cho method đọc (xem _client_for_method): mutation KShip/KiotViet
    # không idempotent, retry tự động là nhân đôi thao tác thật.
    return _client_for_method(method).request(method, url, headers=headers, timeout=30, **kwargs)


def _normalize_kship_provider(item: dict) -> dict | None:
    provider_id = item.get("id") or item.get("partnerId") or item.get("providerId")
    code = _sanitize_text(item.get("code") or item.get("partnerCode") or item.get("providerCode") or "")
    name = _sanitize_text(item.get("name") or item.get("partnerName") or item.get("providerName") or "")
    image_for_mobile = _sanitize_text(item.get("imageForMobile") or item.get("image_for_mobile") or "")
    image = _sanitize_text(item.get("image") or "")
    if not code and not name and provider_id is None:
        return None
    return {
        "id": provider_id,
        "code": code,
        "name": name or code,
        "imageForMobile": image_for_mobile,
        "image": image,
    }


def _normalize_kship_service(item: dict, provider_code: str) -> dict | None:
    service_id = item.get("id") or item.get("serviceId")
    code = _sanitize_text(item.get("code") or item.get("serviceCode") or "")
    name = _sanitize_text(item.get("name") or item.get("serviceName") or "")
    if not code and not name and service_id is None:
        return None
    return {
        "id": service_id,
        "code": code,
        "name": name or code,
        "provider_code": provider_code,
    }


def _parse_kship_setting_service_payload(payload: dict | None) -> tuple[list[dict], list[dict]]:
    """
    Parse KShip setting-service payload:
    data.clients => { "<provider_code>": { ..., services: [...] } }
    """
    if not isinstance(payload, dict):
        return [], []

    data_obj = payload.get("data")
    if not isinstance(data_obj, dict):
        return [], []

    clients = data_obj.get("clients")
    if not isinstance(clients, dict):
        return [], []

    providers: list[dict] = []
    services: list[dict] = []
    provider_seen: set[str] = set()
    service_seen: set[str] = set()

    for key, client_obj in clients.items():
        if not isinstance(client_obj, dict):
            continue
        try:
            client_status = int(str(client_obj.get("status")).strip())
        except (TypeError, ValueError):
            client_status = None
        if client_status != 1:
            continue

        raw_code = _sanitize_text(client_obj.get("code") or key or "")
        normalized_provider = _normalize_kship_provider(client_obj)
        if not normalized_provider:
            continue
        if not normalized_provider.get("code"):
            normalized_provider["code"] = raw_code
        provider_code = str(normalized_provider.get("code") or "").strip()
        if not provider_code:
            continue

        provider_key = provider_code.upper()
        if provider_key not in provider_seen:
            provider_seen.add(provider_key)
            providers.append(normalized_provider)

        raw_services = client_obj.get("services")
        if not isinstance(raw_services, list):
            continue
        for service_obj in raw_services:
            if not isinstance(service_obj, dict):
                continue
            normalized_service = _normalize_kship_service(service_obj, provider_code)
            if not normalized_service:
                continue
            service_key = (
                str(normalized_service.get("provider_code") or "").upper(),
                str(normalized_service.get("code") or "").upper(),
                str(normalized_service.get("id") or ""),
            )
            service_key_str = "|".join(service_key)
            if service_key_str in service_seen:
                continue
            service_seen.add(service_key_str)
            services.append(normalized_service)

    return providers, services


def get_kship_providers(channel: KiotVietChannel) -> list[dict]:
    providers_endpoint = str(
        getattr(settings, "KIOTVIET_KSHIP_PROVIDERS_ENDPOINT", "/api/deliverypartner/getall")
    ).strip()
    response = _kiotviet_web_request(channel=channel, method="GET", endpoint=providers_endpoint)
    if response.status_code != 200:
        raise Exception(
            f"KiotViet providers request failed: status={response.status_code}, body={_truncate_for_log(response.text)}"
        )
    payload = response.json() if response.content else []
    providers = []
    for item in _extract_list_payload(payload):
        normalized = _normalize_kship_provider(item)
        if normalized:
            providers.append(normalized)
    return providers


def get_kship_services(channel: KiotVietChannel, provider_code: str) -> list[dict]:
    services_endpoint = str(
        getattr(settings, "KIOTVIET_KSHIP_SERVICES_ENDPOINT", "/api/deliverypartner/getservices")
    ).strip()
    response = _kiotviet_web_request(
        channel=channel,
        method="GET",
        endpoint=services_endpoint,
        params={"providerCode": provider_code},
    )
    if response.status_code != 200:
        raise Exception(
            f"KiotViet services request failed: status={response.status_code}, body={_truncate_for_log(response.text)}"
        )
    payload = response.json() if response.content else []
    services = []
    for item in _extract_list_payload(payload):
        normalized = _normalize_kship_service(item, provider_code)
        if normalized:
            services.append(normalized)
    return services


def _is_invalid_token_response(response) -> bool:
    """KShip returns 401/422 with a 'Token không hợp lệ' message when the cached
    web token has been invalidated server-side. The body may use ASCII-escape
    JSON (``\\u00f4``) so we parse the JSON to compare the decoded message."""
    if response.status_code not in (401, 422):
        return False
    message = ""
    try:
        data = response.json() if response.content else {}
        if isinstance(data, dict):
            message = str(data.get("message") or data.get("msg") or "").lower()
    except (ValueError, TypeError, AttributeError):
        pass
    raw = (response.text or "").lower()
    return "token" in (message + " " + raw)


def _request_kship_setting_service(channel: KiotVietChannel, *, force_refresh: bool):
    setting_service_endpoint = str(
        getattr(settings, "KIOTVIET_KSHIP_SETTING_SERVICE_ENDPOINT", "/api/v3/setting-service")
    ).strip()
    if setting_service_endpoint.lower().startswith(("http://", "https://")):
        url = setting_service_endpoint
    else:
        base_url = str(
            getattr(settings, "KIOTVIET_SHIPPING_API_BASE_URL", "https://shipping.kiotapi.com")
        ).rstrip("/")
        url = f"{base_url}/{setting_service_endpoint.lstrip('/')}"

    token = get_web_access_token(channel=channel, force_refresh=force_refresh)
    retailer = str(channel.retailer or "").strip()
    headers = {
        "Token": token,
        "retailer": retailer,
        "Content-Type": "application/json",
    }
    # GET thuần đọc → http_read (retry 429/5xx an toàn).
    return http_read.get(url, headers=headers, params={"active_Kship": "true"}, timeout=30)


def get_kship_providers_and_services(channel: KiotVietChannel) -> dict:
    response = _request_kship_setting_service(channel, force_refresh=False)
    if _is_invalid_token_response(response):
        logger.warning(
            "KShip setting-service rejected cached token; retrying with fresh login. channel=%s status=%s",
            channel.id,
            response.status_code,
        )
        response = _request_kship_setting_service(channel, force_refresh=True)

    if response.status_code != 200:
        logger.error(
            "KShip setting-service request failed. channel=%s status=%s body=%s",
            channel.id,
            response.status_code,
            _truncate_for_log(response.text),
        )
        raise Exception(
            f"KShip setting-service request failed: status={response.status_code}, "
            f"body={_truncate_for_log(response.text)}"
        )

    payload = response.json() if response.content else {}
    providers, services = _parse_kship_setting_service_payload(payload)
    if not providers:
        logger.error(
            "KShip setting-service payload parsed empty providers. channel=%s body=%s",
            channel.id,
            _truncate_for_log(response.text),
        )
        raise Exception(
            f"KShip setting-service returned no active providers: body={_truncate_for_log(response.text)}"
        )

    return {
        "providers": providers,
        "services": services,
        "shipper_notes": list_shipper_note_options(),
        "service_extras": _collect_service_extra_choices(payload),
    }


def register_webhook(
    *,
    channel: KiotVietChannel,
    webhook_type: str,
    webhook_url: str,
    is_active: bool = True,
    description: str = "",
    secret: str | None = None,
) -> dict:
    """
    Register KiotViet webhook for a specific channel.
    """
    type_value = str(webhook_type or "").strip()
    url_value = str(webhook_url or "").strip()
    description_value = str(description or "").strip()

    if not type_value:
        raise ValueError("Webhook type không được để trống.")
    if not url_value:
        raise ValueError("Webhook url không được để trống.")

    payload = {
        "Webhook": {
            "Type": type_value,
            "Url": url_value,
            "IsActive": bool(is_active),
            "Description": description_value,
        }
    }
    if secret:
        payload["Webhook"]["Secret"] = str(secret).strip()

    response = _kiotviet_request(
        order=None,
        channel=channel,
        method="POST",
        endpoint="/webhooks",
        json=payload,
    )
    if response.status_code not in (200, 201):
        logger.error(
            "Register webhook failed (channel_id=%s, status=%s, body=%s)",
            channel.id,
            response.status_code,
            (response.text or "").strip(),
        )
        raise Exception(
            f"Đăng ký webhook KiotViet thất bại: status={response.status_code}, body={_truncate_for_log(response.text)}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ValueError("Response webhook KiotViet không phải JSON hợp lệ.") from exc


# Thông tin xuất hoá đơn điện tử của khách trên KiotViet là bộ trường RIÊNG, không dùng
# lại contactNumber/name/address. Không gửi thì HĐĐT xuất ra trống thông tin người mua,
# kế toán phải sửa tay từng hoá đơn. Mặc định soi gương đúng 3 trường gốc.
_EINVOICE_FIELD_SOURCES = (
    ("ContactNumberEInvoice", ("contactNumber", "ContactNumber")),
    ("NameEInvoice", ("name", "Name")),
    ("AddressEInvoice", ("address", "Address")),
)


def _apply_einvoice_fields(payload: dict) -> dict:
    """Điền các trường hoá đơn điện tử theo SĐT / tên / địa chỉ đang gửi lên.

    Sửa payload tại chỗ rồi trả về chính nó. Caller đã tự truyền giá trị thì tôn trọng,
    chỉ điền khi trường HĐĐT còn trống.
    """
    for target, sources in _EINVOICE_FIELD_SOURCES:
        if _sanitize_text(payload.get(target)):
            continue
        for source in sources:
            value = _sanitize_text(payload.get(source))
            if value:
                payload[target] = value
                break
    return payload


def get_or_create_customer(customer_data: dict, order: OrderModel, channel: KiotVietChannel) -> dict:
    """
    Phone-first lookup: find customer on KiotViet by phone. Reuse if found, otherwise create new.
    """
    customer_data = _sanitize_payload_value(customer_data)
    phone = _sanitize_text(customer_data.get("contactNumber") or "")
    requested_code = _sanitize_text(customer_data.get("code") or customer_data.get("customerCode") or "")
    logger.info("Checking customer by phone (code=%s, phone=%s)", requested_code or "N/A", phone)
    try:
        if phone:
            existing = find_customer_by_phone(phone=phone, order=order, channel=channel)
            if isinstance(existing, dict) and existing:
                existing_code = str(existing.get("code") or existing.get("customerCode") or "").strip()
                if existing_code:
                    logger.info(
                        "Customer found by phone=%s, reuse KiotViet code=%s (local_code=%s)",
                        phone,
                        existing_code,
                        requested_code or "N/A",
                    )
                    enriched = find_customer_by_code(
                        customer_code=existing_code, order=order, channel=channel
                    )
                    return _normalize_customer_payload(enriched, fallback=existing)

        logger.info("Customer not found by phone=%s, creating new (code=%s)", phone, requested_code or "N/A")
        return create_customer(customer_data, order, channel)
    except Exception as e:
        logger.error(f"Error checking/creating customer: {e}")
        raise


def create_customer(customer_data: dict, order: OrderModel, channel: KiotVietChannel) -> dict:
    """
    Create a new customer in KiotViet.

    Args:
        customer_data: Customer data dictionary
        channel: KiotVietChannel instance

    Returns:
        Customer data from KiotViet API response
    """
    try:
        payload = _sanitize_payload_value(dict(customer_data or {}))
        requested_code = _sanitize_text(payload.get("code") or payload.get("customerCode") or "")
        if requested_code:
            payload["code"] = requested_code
            payload.setdefault("customerCode", requested_code)
        _apply_einvoice_fields(payload)

        response = _kiotviet_request(order=order, channel=channel, method="POST", endpoint="/customers", json=payload)
        if response.status_code in {200, 201}:
            customer = response.json()
            customer_code = _sanitize_text(
                customer.get("code")
                or customer.get("Code")
                or customer.get("customerCode")
                or customer.get("CustomerCode")
                or ""
            )
            if customer_code and not customer.get("code"):
                customer["code"] = customer_code
            if requested_code and customer_code and customer_code != requested_code:
                logger.warning(
                    "KiotViet returned different customer code (requested=%s, actual=%s, phone=%s)",
                    requested_code,
                    customer_code,
                    payload.get("contactNumber"),
                )
            logger.info(
                "Customer created successfully (code=%s, id=%s)",
                customer_code or "N/A",
                customer.get("id"),
            )
            return _normalize_customer_payload(customer)
        else:
            response_body = (response.text or "").strip()
            response_json = {}
            try:
                response_json = response.json() or {}
            except ValueError:
                response_json = {}
            response_status = response_json.get("responseStatus") or {}
            error_code = str(response_status.get("errorCode") or "").strip()
            message = str(response_status.get("message") or "").strip().lower()
            is_duplicate_phone = error_code == "KvValidateCustomerException" and "số điện thoại" in message and "đã tồn tại" in message
            is_duplicate_code = error_code == "KvValidateCustomerException" and "mã khách hàng" in message and "đã tồn tại" in message

            if response.status_code == 420 and is_duplicate_code and requested_code:
                existing_by_code = None
                try:
                    existing_by_code = find_customer_by_code(
                        customer_code=requested_code,
                        order=order,
                        channel=channel,
                    )
                except Exception as read_exc:  # noqa: BLE001
                    logger.info(
                        "Cannot read existing customer by code (channel=%s, code=%s): %s — "
                        "accepting duplicate as success.",
                        channel.id if channel else None,
                        requested_code,
                        read_exc,
                    )
                existing_by_code_value = ""
                if existing_by_code:
                    existing_by_code_value = (
                        existing_by_code.get("code")
                        or existing_by_code.get("Code")
                        or existing_by_code.get("customerCode")
                        or existing_by_code.get("CustomerCode")
                        or ""
                    )
                if existing_by_code_value:
                    logger.info(
                        "Customer already exists by code, fallback to existing customer code=%s (channel=%s)",
                        existing_by_code_value,
                        channel.id if channel else None,
                    )
                    return _normalize_customer_payload(existing_by_code)
                # KiotViet xác nhận code đã tồn tại nhưng mình không có quyền đọc lại
                # (403 / network) — coi như success, dùng code đã gửi cho bước order.
                logger.info(
                    "Customer code %s already exists on KiotViet; cannot re-fetch but treat as success (channel=%s)",
                    requested_code,
                    channel.id if channel else None,
                )
                return _normalize_customer_payload({"code": requested_code}, fallback=customer_data)

            if response.status_code == 420 and is_duplicate_phone:
                existing = None
                try:
                    existing = find_customer_by_phone(
                        phone=customer_data.get("contactNumber", ""),
                        order=order,
                        channel=channel,
                    )
                except Exception as read_exc:  # noqa: BLE001
                    logger.info(
                        "Cannot read existing customer by phone (channel=%s, phone=%s): %s — "
                        "accepting duplicate as success.",
                        channel.id if channel else None,
                        customer_data.get("contactNumber"),
                        read_exc,
                    )
                existing_code = str(
                    existing.get("code")
                    or existing.get("Code")
                    or existing.get("customerCode")
                    or existing.get("CustomerCode")
                    or ""
                ).strip() if existing else ""
                if existing_code:
                    logger.info(
                        "Customer already exists by phone, reuse KiotViet code=%s (channel=%s)",
                        existing_code,
                        channel.id if channel else None,
                    )
                    enriched = None
                    if existing_code:
                        try:
                            enriched = find_customer_by_code(customer_code=existing_code, order=order, channel=channel)
                        except Exception:  # noqa: BLE001
                            enriched = None
                    return _normalize_customer_payload(enriched, fallback=existing)
                # KiotViet báo phone đã tồn tại nhưng không đọc được — fallback dùng
                # code mình đã gửi (nếu có) để tiếp tục flow.
                if requested_code:
                    logger.info(
                        "Customer phone already exists on KiotViet; cannot re-fetch, "
                        "reuse requested code=%s (channel=%s)",
                        requested_code,
                        channel.id if channel else None,
                    )
                    return _normalize_customer_payload({"code": requested_code}, fallback=customer_data)

            logger.error(
                "Failed to create customer on KiotViet (channel=%s): status=%s body=%s payload=%s",
                channel.id if channel else None,
                response.status_code,
                response_body,
                customer_data,
            )
            raise Exception(f"[stage=create_customer] Failed to create customer: {response.status_code} - {response_body}")
    except Exception as e:
        logger.error(f"Error creating customer: {e}")
        raise


def create_kship_delivery_for_invoice(invoice_id: int, order: OrderModel, channel: KiotVietChannel) -> dict:
    """
    Create KShip delivery for an invoice.

    Args:
        invoice_id: Invoice ID from KiotViet
        order: Order model instance
        channel: KiotVietChannel instance

    Returns:
        Dictionary with delivery creation result
    """
    try:
        # Call KiotViet API to get delivery information
        endpoint = str(
            getattr(settings, "KIOTVIET_KSHIP_DELIVERY_CREATE_ENDPOINT", "/api/clientdelivery/invoicesForMultiOrderDeliveryCreate")
        ).strip()

        payload = {
            "InvoiceIds": f"[{invoice_id}]",
            "IsMultiPointDelivery": True,
        }

        logger.info(
            "KiotViet KShip delivery create request (order_id=%s, invoice_id=%s, channel=%s): endpoint=%s payload=%s",
            order.id,
            invoice_id,
            channel.id if channel else None,
            endpoint,
            payload,
        )

        response = _kiotviet_web_request(
            order=order,
            channel=channel,
            method="POST",
            endpoint=endpoint,
            json=payload,
        )

        if response.status_code != 200:
            logger.error(
                "KiotViet KShip delivery create failed (order_id=%s, invoice_id=%s, channel=%s): status=%s body=%s",
                order.id,
                invoice_id,
                channel.id if channel else None,
                response.status_code,
                response.text,
            )
            # Giữ nguyên lỗi KiotViet trả về (responseStatus.message / message /
            # raw body) thay vì chỉ báo status code — FE cần hiển thị đúng lỗi kiot.
            error_message = ""
            try:
                err_json = response.json() if response.content else {}
                response_status = (
                    err_json.get("responseStatus") or err_json.get("ResponseStatus") or {}
                )
                if not isinstance(response_status, dict):
                    response_status = {}
                error_message = str(
                    response_status.get("message")
                    or response_status.get("Message")
                    or err_json.get("message")
                    or err_json.get("Message")
                    or ""
                ).strip()
            except (ValueError, AttributeError):
                pass
            if not error_message:
                raw_body = (response.text or "").strip()
                error_message = (
                    f"KiotViet trả lỗi {response.status_code}: {raw_body[:500]}"
                    if raw_body
                    else f"KiotViet trả lỗi {response.status_code} (không kèm nội dung)."
                )
            return {
                "success": False,
                "error": error_message,
                "response_body": response.text,
            }

        response_data = response.json() if response.content else {}
        logger.info(
            "KiotViet KShip delivery create response (order_id=%s, invoice_id=%s, channel=%s): %s",
            order.id,
            invoice_id,
            channel.id if channel else None,
            response_data,
        )

        # Extract delivery information from response
        data_list = response_data.get("Data", [])
        if not data_list or not isinstance(data_list, list):
            return {
                "success": False,
                "error": f"KiotViet không trả dữ liệu delivery (response={json.dumps(response_data, ensure_ascii=False)[:500]})",
                "response_data": response_data,
            }

        delivery_data = data_list[0] if data_list else {}
        delivery_id = delivery_data.get("DeliveryId")
        delivery_code = delivery_data.get("DeliveryCode")

        if not delivery_id:
            return {
                "success": False,
                "error": f"KiotViet không trả DeliveryId (response={json.dumps(response_data, ensure_ascii=False)[:500]})",
                "response_data": response_data,
            }

        return {
            "success": True,
            "delivery_id": delivery_id,
            "delivery_code": delivery_code,
            "delivery_data": delivery_data,
            "response_data": response_data,
        }
    except Exception as exc:
        logger.exception(
            "KiotViet KShip delivery create exception (order_id=%s, invoice_id=%s, channel=%s): %s",
            order.id,
            invoice_id,
            channel.id if channel else None,
            exc,
        )
        return {
            "success": False,
            "error": str(exc),
        }


# Quy định xem hàng gửi kèm vận đơn (SERVICE_EXTRA.ShipperNote của KShip).
# Bảng này là nguồn sự thật DUY NHẤT: frontend đọc qua /api/orders/kship-options nên
# đổi hoặc bổ sung mã chỉ cần deploy backend, không phải build lại FE.
# Xem docs/superpowers/specs/2026-08-10-kship-shipper-note-design.md.
KSHIP_SHIPPER_NOTE_DEFAULT = "CHOXEMHANGKHONGTHU"
KSHIP_SHIPPER_NOTE_OPTIONS = (
    {"code": "CHOXEMHANGKHONGTHU", "name": "Cho xem hàng không thử"},
    {"code": "CHOTHUHANG", "name": "Cho thử hàng"},
    {"code": "KHONGCHOXEMHANG", "name": "Không cho xem hàng"},
)


def list_shipper_note_options() -> list[dict]:
    """Danh sách quy định xem hàng cho FE render dropdown (kèm cờ mặc định)."""
    return [
        {**item, "is_default": item["code"] == KSHIP_SHIPPER_NOTE_DEFAULT}
        for item in KSHIP_SHIPPER_NOTE_OPTIONS
    ]


def resolve_shipper_note(code: str = "") -> dict:
    """Đổi mã quy định xem hàng thành entry ``SERVICE_EXTRA``.

    Mã rỗng hoặc mã lạ thì rơi về mặc định kèm warning. Cố tình KHÔNG ném exception:
    một trường phụ không được quyền làm hỏng cả lượt tạo vận đơn.
    """
    normalized = str(code or "").strip().upper()
    by_code = {item["code"]: item for item in KSHIP_SHIPPER_NOTE_OPTIONS}
    if normalized and normalized not in by_code:
        logger.warning(
            "Unknown KShip ShipperNote code %r, falling back to %s",
            code,
            KSHIP_SHIPPER_NOTE_DEFAULT,
        )
    return by_code.get(normalized) or by_code[KSHIP_SHIPPER_NOTE_DEFAULT]


# 3 mục SERVICE_EXTRA builder luôn tự dựng (xem build_kship_create_order_payload).
# Hãng nào khai lại đúng các code này thì bỏ qua — giá trị của builder mới đúng ngữ
# cảnh đơn (vd GBH phải bằng giá hàng, không phải giá trị mặc định của KShip).
KSHIP_DEFAULT_SERVICE_EXTRA_CODES = frozenset({"PaymentBy", "GBH", "ShipperNote"})

# TTL cache services_extra bắt buộc (giây). Lỗi mạng KHÔNG được cache.
KSHIP_REQUIRED_SERVICE_EXTRA_CACHE_TTL = 60


def _parse_service_extra_options(raw) -> list[dict]:
    """``options`` của services_extra là CHUỖI JSON (hoặc rỗng) trong payload KShip."""
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw.strip() else []
    except ValueError:
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _auto_service_extra_for_client(
    client_obj: dict, service_code: str, *, with_options: bool = False
) -> list[dict]:
    """Các mục services_extra hãng tự bật (``auto_add=1``) cho đúng dịch vụ đang chọn.

    Chỉ nhận ``DropdownList``: đó là dạng KShip liệt kê sẵn danh sách giá trị hợp lệ
    (SPX ``PickShift``), nên chọn phần tử đầu là an toàn. Các view_type khác cần giá
    trị do người dùng nhập nên không tự đoán được — bỏ qua và để KShip báo lỗi rõ ràng.
    """
    service_id = ""
    services = client_obj.get("services")
    if isinstance(services, list):
        wanted = str(service_code or "").strip().upper()
        for service_obj in services:
            if not isinstance(service_obj, dict):
                continue
            if str(service_obj.get("code") or "").strip().upper() == wanted:
                service_id = str(service_obj.get("id") or "")
                break

    extras = client_obj.get("services_extra")
    if not isinstance(extras, list):
        return []

    resolved: list[dict] = []
    for extra in extras:
        if not isinstance(extra, dict):
            continue
        code = _sanitize_text(extra.get("code") or "")
        if not code or code in KSHIP_DEFAULT_SERVICE_EXTRA_CODES:
            continue
        try:
            auto_add = int(str(extra.get("auto_add")).strip())
        except (TypeError, ValueError):
            auto_add = 0
        if auto_add != 1:
            continue
        # services_possible liệt kê ID dịch vụ áp dụng được; rỗng = áp cho mọi dịch vụ.
        possible = extra.get("services_possible")
        if isinstance(possible, list) and possible:
            if not service_id or service_id not in [str(item) for item in possible]:
                continue
        view_type = _sanitize_text(extra.get("view_type") or "")
        if view_type != "DropdownList":
            logger.warning(
                "KShip auto_add extra %r có view_type %r chưa hỗ trợ tự điền — bỏ qua.",
                code,
                view_type,
            )
            continue
        options = _parse_service_extra_options(extra.get("options"))
        value = _sanitize_text(options[0].get("Value") or "") if options else ""
        if not value:
            logger.warning("KShip auto_add extra %r không có giá trị khả dụng — bỏ qua.", code)
            continue
        entry = {
            "Code": code,
            "Name": _sanitize_text(extra.get("name") or code),
            "ViewType": view_type,
            "Value": value,
        }
        if with_options:
            entry["Options"] = [
                {
                    "value": _sanitize_text(option.get("Value") or ""),
                    "name": _sanitize_text(option.get("Name") or ""),
                }
                for option in options
                if _sanitize_text(option.get("Value") or "")
            ]
        resolved.append(entry)
    return resolved


def _apply_selected_service_extra(resolved: list[dict], selected: dict | None) -> list[dict]:
    """Ghép lựa chọn người dùng vào các mục đã resolve và bỏ ``Options`` (chỉ dùng cho UI).

    Giá trị lạ/hết hạn bị bỏ qua có chủ đích: gửi một ca lấy hàng không còn trong danh
    sách sang KShip là hỏng cả vận đơn, còn ca sớm nhất thì luôn hợp lệ.
    """
    picked = selected if isinstance(selected, dict) else {}
    final: list[dict] = []
    for entry in resolved:
        options = entry.get("Options") or []
        value = entry.get("Value")
        wanted = str(picked.get(entry.get("Code")) or "").strip()
        if wanted:
            if any(wanted == option.get("value") for option in options):
                value = wanted
            else:
                logger.warning(
                    "Giá trị %r cho SERVICE_EXTRA %r không còn khả dụng — dùng %r.",
                    wanted,
                    entry.get("Code"),
                    value,
                )
        final.append(
            {
                "Code": entry.get("Code"),
                "Name": entry.get("Name"),
                "ViewType": entry.get("ViewType"),
                "Value": value,
            }
        )
    return final


def _collect_service_extra_choices(payload: dict | None) -> list[dict]:
    """Các lựa chọn SERVICE_EXTRA bắt buộc, phẳng theo (hãng, dịch vụ), cho dialog FE.

    Chỉ liệt kê mục người dùng thực sự chọn được (``auto_add=1`` + ``DropdownList``).
    Hiện chỉ SPX ``PickShift`` rơi vào đây; hãng khác trả rỗng nên dialog không đổi.
    """
    clients = ((payload or {}).get("data") or {}).get("clients")
    if not isinstance(clients, dict):
        return []

    choices: list[dict] = []
    for key, client_obj in clients.items():
        if not isinstance(client_obj, dict):
            continue
        try:
            client_status = int(str(client_obj.get("status")).strip())
        except (TypeError, ValueError):
            client_status = None
        if client_status != 1:
            continue
        provider_code = _sanitize_text(client_obj.get("code") or key or "")
        if not provider_code:
            continue
        services = client_obj.get("services")
        if not isinstance(services, list):
            continue
        for service_obj in services:
            if not isinstance(service_obj, dict):
                continue
            service_code = _sanitize_text(service_obj.get("code") or "")
            if not service_code:
                continue
            for extra in _auto_service_extra_for_client(
                client_obj, service_code, with_options=True
            ):
                if not extra.get("Options"):
                    continue
                choices.append(
                    {
                        "provider_code": provider_code,
                        "service_code": service_code,
                        "code": extra["Code"],
                        "name": extra["Name"],
                        "view_type": extra["ViewType"],
                        "options": extra["Options"],
                    }
                )
    return choices


def resolve_required_service_extra(
    channel: KiotVietChannel,
    provider_code: str,
    service_code: str,
    selected: dict | None = None,
) -> list[dict]:
    """SERVICE_EXTRA hãng BẮT BUỘC ngoài 3 mục mặc định, đọc tại thời điểm tạo đơn.

    SPX yêu cầu ``PickShift`` (ca lấy hàng) và giá trị hợp lệ đổi theo NGÀY
    (``"<epoch ngày>-<số ca>"``), nên không hardcode được — phải hỏi setting-service
    ngay lúc tạo vận đơn. ``selected`` là lựa chọn người dùng bấm trong dialog
    (``{"PickShift": "<giá trị>"}``); giá trị đó có thể đã hết hạn giữa lúc mở dialog
    và lúc worker chạy job nên chỉ dùng khi vẫn còn trong danh sách hiện hành, còn lại
    rơi về ca sớm nhất — đúng như KiotViet tự điền khi ``auto_add=1``. Các hãng đang
    dùng (BEST/GHN/J&T) không có mục nào → trả rỗng.

    Lỗi mạng/parse KHÔNG được cắt ngang lượt tạo vận đơn: trả rỗng và để KShip từ
    chối kèm lỗi của hãng (cùng nguyên tắc với ``resolve_shipper_note``).
    """
    wanted = str(provider_code or "").strip().upper()
    if not wanted:
        return []

    # setting-service là payload ~170KB và bước này chạy MỘT LẦN MỖI ĐƠN; tạo vận đơn
    # hàng loạt sẽ nã hàng trăm request thừa vào KiotViet. Cache ngắn: đủ để một lượt
    # bulk dùng chung, vẫn ngắn hơn khoảng cách giữa hai ca lấy hàng của SPX.
    cache_key = f"kship:required-service-extra:{getattr(channel, 'id', '')}:{wanted}:{service_code}"
    cached = cache.get(cache_key)
    if cached is not None:
        return _apply_selected_service_extra(cached, selected)

    try:
        response = _request_kship_setting_service(channel, force_refresh=False)
        if response.status_code != 200:
            logger.warning(
                "KShip setting-service trả %s khi lấy services_extra. channel=%s provider=%s",
                response.status_code,
                getattr(channel, "id", None),
                provider_code,
            )
            return []
        payload = response.json() if response.content else {}
        clients = ((payload or {}).get("data") or {}).get("clients")
        if not isinstance(clients, dict):
            return []
        resolved: list[dict] = []
        for key, client_obj in clients.items():
            if not isinstance(client_obj, dict):
                continue
            code = _sanitize_text(client_obj.get("code") or key or "")
            if code.strip().upper() != wanted:
                continue
            resolved = _auto_service_extra_for_client(
                client_obj, service_code, with_options=True
            )
            break
        cache.set(cache_key, resolved, KSHIP_REQUIRED_SERVICE_EXTRA_CACHE_TTL)
        return _apply_selected_service_extra(resolved, selected)
    except Exception:
        logger.exception(
            "Không lấy được services_extra bắt buộc của KShip. channel=%s provider=%s",
            getattr(channel, "id", None),
            provider_code,
        )
        return []


def build_kship_create_order_payload(
    delivery_data: dict,
    provider_code: str,
    service_code: str,
    sender_info: dict,
    note: str = "",
    shipper_note: str = "",
    extra_services: list[dict] | None = None,
) -> dict:
    """
    Build payload for KShip create-order API.

    Args:
        delivery_data: Delivery data from invoicesForMultiOrderDeliveryCreate API
        provider_code: Provider code from KShip options
        service_code: Service code from KShip options
        sender_info: Sender information from channel configuration
        note: Ghi chú tự do gửi shipper
        shipper_note: Mã quy định xem hàng; rỗng hoặc lạ thì dùng mặc định
        extra_services: SERVICE_EXTRA bắt buộc riêng của hãng (xem
            ``resolve_required_service_extra``); code trùng 3 mục mặc định bị bỏ qua

    Returns:
        Dictionary with create-order API payload
    """
    try:
        # Extract receiver information
        receiver_name = _sanitize_text(delivery_data.get("ReceiverName") or "")
        receiver_phone = _sanitize_text(delivery_data.get("ReceiverPhone") or "")
        receiver_address = _sanitize_text(delivery_data.get("ReceiverAddress") or "")

        # Extract location information
        receiver_location = delivery_data.get("ReceiverLocation") or {}
        receiver_location_id = receiver_location.get("Id") or 0
        receiver_location_name = _sanitize_text(receiver_location.get("Name") or "")

        receiver_ward = delivery_data.get("ReceiverWard") or {}
        receiver_ward_id = receiver_ward.get("Id") or 0
        receiver_ward_name = _sanitize_text(receiver_ward.get("Name") or "")

        # Extract product information
        weight = int(float(delivery_data.get("Weight") or 0))
        length = int(float(delivery_data.get("Length") or 0))
        width = int(float(delivery_data.get("Width") or 0))
        height = int(float(delivery_data.get("Height") or 0))
        product_quantity = int(float(delivery_data.get("ProductQuantity") or 1))
        product_price = int(float(delivery_data.get("ProductPrice") or 0))
        cod_need_payment = int(float(delivery_data.get("CodNeedPayment") or 0))

        # Extract products list
        products_list = delivery_data.get("Products") or []
        first_product = products_list[0] if products_list else {}
        product_name = _sanitize_text(first_product.get("Name") or "")

        # Extract invoice information
        shop_invoice = _sanitize_text(delivery_data.get("Code") or "")
        shop_invoice_id = delivery_data.get("Id") or 0

        # Extract sender information
        sender_location_id = sender_info.get("location_id") or 0
        sender_ward_id = sender_info.get("ward_id") or 0
        sender_address = _sanitize_text(sender_info.get("address") or "")
        sender_mobile = _sanitize_text(sender_info.get("mobile") or "")

        # Build SERVICE_EXTRA with default values
        shipper_note_option = resolve_shipper_note(shipper_note)
        service_extra = [
            {
                "Code": "PaymentBy",
                "ViewType": "Radio",
                "Value": "NGUOIGUI",
                "Name": "Người gửi trả phí",
            },
            {
                "Value": product_price,
                "Name": "Khai giá",
                "ViewType": "CheckList",
                "Code": "GBH",
            },
            {
                "Value": shipper_note_option["code"],
                "Name": shipper_note_option["name"],
                "ViewType": "DropdownList",
                "Code": "ShipperNote",
            },
        ]
        for item in extra_services or []:
            code = _sanitize_text((item or {}).get("Code") or "")
            if not code or code in KSHIP_DEFAULT_SERVICE_EXTRA_CODES:
                continue
            service_extra.append(
                {
                    "Code": code,
                    "Name": _sanitize_text(item.get("Name") or code),
                    "ViewType": _sanitize_text(item.get("ViewType") or ""),
                    "Value": item.get("Value"),
                }
            )

        # Build payload
        payload = {
            "ACTIVE_KSHIP": True,
            "UUID": "",
            "client_code": provider_code,
            "group_merchant": "retail",
            "SENDER_LOCATION_ID": sender_location_id,
            "SENDER_WARD_ID": sender_ward_id,
            "SENDER_ADDRESS": sender_address,
            "SENDER_MOBILE": sender_mobile,
            "RECEIVER_LOCATION_ID": receiver_location_id,
            "PRODUCTS": [
                {
                    "NAME": product_name,
                    "QUANTITY": product_quantity,
                }
            ],
            "RECEIVER_MOBILE": receiver_phone,
            "RECEIVER_FULLNAME": receiver_name,
            "RECEIVER_LOCATION_NAME": receiver_location_name,
            "RECEIVER_WARD_NAME": receiver_ward_name,
            "SHOP_INVOICE": shop_invoice,
            "SHOP_INVOICE_ID": shop_invoice_id,
            "RECEIVER_WARD_ID": receiver_ward_id,
            "RECEIVER_ADDRESS": receiver_address,
            "PRODUCT_WIDTH": width,
            "PRODUCT_HEIGHT": height,
            "PRODUCT_LENGTH": length,
            "PRODUCT_WEIGHT": weight,
            "PRODUCT_QUANTITY": product_quantity,
            "MONEY_COLLECTION": cod_need_payment,
            "SERVICES": [
                {
                    "CODE": service_code,
                }
            ],
            "ORDER_SERVICE": service_code,
            "SERVICE_EXTRA": service_extra,
            "NOTE": _sanitize_text(note),
            "SOURCE": "KSHIP",
            "PRODUCT_PRICE": product_price,
        }

        return payload
    except Exception as exc:
        logger.exception("Failed to build KShip create-order payload: %s", exc)
        raise Exception(f"Failed to build KShip create-order payload: {str(exc)}") from exc


# Timeout POST create-order. KShip gọi tiếp sang hãng vận chuyển (GHN/BEST/GHTK) rồi
# mới trả lời nên độ trễ phụ thuộc hãng; 30s từng cắt ngang đúng lúc vận đơn đã tạo
# xong bên kia (đơn 554748, 2026-08-18). Để env chỉnh được vì đây là đánh đổi thời
# gian giữ slot worker ↔ tỉ lệ rơi vào ca "không xác định", không phải hằng số kỹ thuật.
# Ngân sách của process_kiotviet_job (soft_time_limit): bước 1 ≤30s + create-order 60s
# + reconcile ≤55s.
KSHIP_CREATE_ORDER_TIMEOUT_SECONDS = 60

# HTTP status ở tầng gateway: request có thể đã tới KShip và vận đơn đã được tạo, chỉ
# là phản hồi không về được. Xử như timeout, KHÔNG xử như KShip từ chối.
_KSHIP_INDETERMINATE_STATUS_CODES = frozenset({502, 503, 504})


def create_kship_order(
    provider_code: str,
    payload: dict,
    order: OrderModel,
    channel: KiotVietChannel,
) -> dict:
    """
    Create KShip order using create-order API.

    Args:
        provider_code: Provider code (e.g., "BEST", "GHN", "GHTK")
        payload: Create-order API payload
        order: Order model instance
        channel: KiotVietChannel instance

    Returns:
        Dictionary with order creation result. ``indeterminate=True`` nghĩa là KHÔNG
        biết KShip đã tạo vận đơn hay chưa — caller phải đọc lại hoá đơn KiotViet để
        dò `deliveryCode` trước khi kết luận thất bại (xem
        ``service_actions._reconcile_kship_shipping_code``). Thiếu bước đó thì vận đơn
        thật nằm bên hãng vận chuyển mà hệ thống không hề hay biết.
    """
    try:
        # Build endpoint URL
        base_url = str(getattr(settings, "KIOTVIET_SHIPPING_API_BASE_URL", "https://shipping.kiotapi.com")).rstrip("/")
        endpoint = f"{base_url}/api/v3/create-order/{provider_code}"

        # Get web access token
        token = get_web_access_token(order=order, channel=channel)
        retailer = str(channel.retailer or "").strip()

        # Build headers
        headers = {
            "Token": token,
            "retailer": retailer,
            "Content-Type": "application/json",
        }

        # DEBUG: log payload
        import json as _json
        logger.info(
            "KiotViet KShip create-order request (order_id=%s, provider=%s, channel=%s): endpoint=%s payload=%s",
            order.id,
            provider_code,
            channel.id if channel else None,
            endpoint,
            _json.dumps(payload, ensure_ascii=False),
        )

        # Make API call.
        # http_mutate = KHÔNG retry (MR !1249): timeout ở đây nghĩa là vận đơn có
        # thể ĐÃ tạo thật phía KShip — re-send tự động sẽ sinh vận đơn thứ hai.
        # http_mutate giữ nguyên ReadTimeout/ConnectionError để nhánh except dưới
        # phân loại "không xác định" rồi reconcile.
        response = http_mutate.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=getattr(
                settings,
                "KIOTVIET_KSHIP_CREATE_ORDER_TIMEOUT_SECONDS",
                KSHIP_CREATE_ORDER_TIMEOUT_SECONDS,
            ),
        )

        if response.status_code != 200:
            logger.error(
                "KiotViet KShip create-order failed (order_id=%s, provider=%s, channel=%s): status=%s body=%s",
                order.id,
                provider_code,
                channel.id if channel else None,
                response.status_code,
                response.text,
            )
            # Extract human-readable error message from KShip response.
            error_message = f"API call failed with status {response.status_code}"
            try:
                err_json = response.json() if response.content else {}
                error_message = (
                    (err_json.get("data") or {}).get("message")
                    or (err_json.get("data") or {}).get("msg")
                    or err_json.get("message")
                    or err_json.get("msg")
                    or error_message
                )
            except (ValueError, AttributeError):
                pass
            return {
                "success": False,
                "indeterminate": response.status_code in _KSHIP_INDETERMINATE_STATUS_CODES,
                "error": error_message,
                "response_body": response.text,
            }

        response_data = response.json() if response.content else {}
        logger.info(
            "KiotViet KShip create-order response (order_id=%s, provider=%s, channel=%s): %s",
            order.id,
            provider_code,
            channel.id if channel else None,
            response_data,
        )

        # Extract order information from response
        data = response_data.get("data", {})
        order_number = data.get("ORDER_NUMBER")
        total_fee = data.get("TOTAL_FEE")
        sort_code = data.get("SORT_CODE")

        if not order_number:
            return {
                "success": False,
                "error": f"KShip không trả ORDER_NUMBER (response={_json.dumps(response_data, ensure_ascii=False)[:500]})",
                "response_data": response_data,
            }

        return {
            "success": True,
            "order_number": order_number,
            "total_fee": total_fee,
            "sort_code": sort_code,
            "response_data": response_data,
        }
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
        # Lỗi TRUYỀN TẢI, không phải lỗi nghiệp vụ: request đã (hoặc có thể đã) tới
        # KShip và vận đơn có thể đã tạo xong, chỉ phản hồi không về kịp. Trả về
        # success=False trơn ở đây chính là chỗ đơn 554748 mất mã vận đơn.
        logger.exception(
            "KiotViet KShip create-order KHÔNG XÁC ĐỊNH (order_id=%s, provider=%s, channel=%s): %s",
            order.id,
            provider_code,
            channel.id if channel else None,
            exc,
        )
        return {
            "success": False,
            "indeterminate": True,
            "error": str(exc),
        }
    except Exception as exc:
        logger.exception(
            "KiotViet KShip create-order exception (order_id=%s, provider=%s, channel=%s): %s",
            order.id,
            provider_code,
            channel.id if channel else None,
            exc,
        )
        return {
            "success": False,
            "error": str(exc),
        }
