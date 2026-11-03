from apps.audit.decorators import log_kiotviet_call
from apps.common import rate_limiter
from apps.common.http_client import HttpClient
from apps.kiotviet.models import KiotVietAccessToken, KiotVietChannel
from apps.orders.models import Order as OrderModel
from apps.orders.models import OrderItem
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
import requests
import logging
import json
import re
import time
from urllib.parse import quote

logger = logging.getLogger(__name__)

KIOTVIET_TOKEN_URL = "https://id.kiotviet.vn/connect/token"
KIOTVIET_WEB_LOGIN_URL = "https://api-man1.kiotviet.vn/api/account/login?quan-ly=true"
KIOTVIET_WEB_API_BASE_URL = "https://api-man1.kiotviet.vn"
KIOTVIET_WEB_FINGERPRINT_KEY = "180e1cfc9aa92e89b5f81feb8590d10c_Chrome_Desktop_Máy tính Mac OS"
KIOTVIET_TOKEN_SCOPE_PUBLIC = "PublicApi.Access"
KIOTVIET_TOKEN_SCOPE_WEB = "web"

# ---------------------------------------------------------------------------
# HTTP client dùng chung (audit backend-architecture 4.4): session pooling +
# timeout mặc định thay cho requests trần. Giữ 30s lịch sử của module này,
# KHÔNG lấy DEFAULT_TIMEOUT=10 của HttpClient kẻo đổi hành vi call site cũ.
#
# Retry (429/5xx + backoff) CHỈ an toàn cho request ĐỌC. POST mutation lên
# KiotViet/KShip KHÔNG idempotent — KShip create-order timeout nhưng vận đơn
# VẪN tạo thật (đơn 554748, MR !1249): retry tự động = vận đơn thứ hai.
# Vì vậy tách 2 client:
# - http_read:   GET/HEAD/OPTIONS — retry 429/5xx với backoff.
# - http_mutate: mọi method còn lại — retries=0, chỉ hưởng pooling + timeout,
#   và giữ nguyên ngữ nghĩa exception của requests (ReadTimeout vẫn là
#   ReadTimeout để create_kship_order phân loại "không xác định").
KIOTVIET_HTTP_TIMEOUT = 30

http_read = HttpClient(timeout=KIOTVIET_HTTP_TIMEOUT)
http_mutate = HttpClient(timeout=KIOTVIET_HTTP_TIMEOUT, retries=0)

_RETRY_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _client_for_method(method: str) -> HttpClient:
    """Route theo method: chỉ request đọc mới được phép retry tự động.

    POST "thuần đọc" (nếu có) vẫn đi http_mutate — bỏ lỡ retry thì vô hại,
    còn retry nhầm một mutation thì nhân đôi hoá đơn/vận đơn thật.
    """
    if str(method or "").upper() in _RETRY_SAFE_METHODS:
        return http_read
    return http_mutate


class KiotVietApiError(Exception):
    """Lỗi gọi API KiotViet, kèm HTTP status code khi biết được.

    Kế thừa ``Exception`` để giữ tương thích ngược với các caller cũ chỉ ``except Exception``.
    """

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class KiotVietInvoiceNotFound(KiotVietApiError):
    """KiotViet trả 404: hóa đơn không tồn tại trên channel này — câu trả lời DỨT KHOÁT."""

    def __init__(self, message: str, status_code: int | None = 404):
        super().__init__(message, status_code=status_code)


def _sanitize_text(value: str | None) -> str:
    """
    Remove hidden control chars (BS/DEL/...) that break KiotViet display.
    Keep printable unicode (including Vietnamese accents).
    """
    raw = str(value or "")
    filtered = "".join(ch for ch in raw if ord(ch) >= 32 and ord(ch) != 127)
    return re.sub(r"\s+", " ", filtered).strip()


def _sanitize_payload_value(value):
    """Recursively sanitize strings inside payload objects."""
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, dict):
        return {k: _sanitize_payload_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_payload_value(item) for item in value]
    return value


def _truncate_for_log(value, limit: int = 4000) -> str:
    """Keep log lines readable while still exposing useful API debug details."""
    text = str(value or "")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated {len(text) - limit} chars>"


def _parse_dimensions(dimensions: str | None) -> tuple[int, int, int] | None:
    """
    Parse dimensions string in AxBxC format into (length, width, height).
    Example inputs: "30x20x15", "30 x 20 x 15", "30X20X15".
    Values are interpreted as cm.
    """
    raw = str(dimensions or "").strip()
    if not raw:
        return None

    parts = [part.strip() for part in re.split(r"[xX×*]", raw) if part.strip()]
    if len(parts) < 3:
        return None

    try:
        length = int(float(parts[0].replace(",", ".")))
        width = int(float(parts[1].replace(",", ".")))
        height = int(float(parts[2].replace(",", ".")))
    except (ValueError, TypeError):
        return None

    if length <= 0 or width <= 0 or height <= 0:
        return None

    return length, width, height


def _resolve_channel(order: OrderModel | None, channel: KiotVietChannel | None = None) -> KiotVietChannel:
    """Resolve KiotViet channel, prioritizing explicit channel and then order mapping."""
    if channel is not None:
        return channel

    order_channel = getattr(order, "kiotviet_channel", None)
    if order_channel is not None:
        return order_channel

    raise ValueError("Order chưa gán kênh KiotViet.")


def _kiotviet_request(
    order: OrderModel | None,
    channel: KiotVietChannel,
    method: str,
    endpoint: str,
    **kwargs,
) -> requests.Response:
    """Send authenticated request to KiotViet Public API."""
    # Chốt chặn THẬT cho staging (audit 2026-07-18): staging chạy snapshot data prod,
    # mọi lớp "mock mode"/xoá secret đều có thể bị cấu hình sai hoặc chạy sai thứ tự —
    # đây là choke point cuối cùng trước khi bắn HTTP lên KiotViet thật.
    if getattr(settings, "IS_STAGING", False):
        raise RuntimeError(
            "KiotViet outbound bị chặn trên staging (APP_ENV=staging) — "
            "không được ghi/đọc KiotViet thật từ môi trường staging."
        )
    resolved_channel = _resolve_channel(order, channel)
    # Caller có thể siết timeout (vd: quét kho cần fail nhanh); mặc định giữ nguyên 30s.
    # Áp CẢ cho lần refresh token: token hết hạn đúng lúc quét mà vẫn để 30s thì hot path
    # xuất kho treo 30s dù đã xin timeout 3s.
    timeout = kwargs.pop("timeout", None)
    token = get_access_token(order=order, channel=resolved_channel, timeout=timeout)
    if timeout is None:
        timeout = 30
    base_url = str(getattr(settings, "KIOTVIET_API_BASE_URL", "https://public.kiotapi.com")).rstrip("/")
    url = f"{base_url}/{endpoint.lstrip('/')}"

    headers = kwargs.pop("headers", {}) or {}
    headers["Authorization"] = f"Bearer {token}"
    retailer = str(resolved_channel.retailer or "").strip()
    headers["Retailer"] = retailer
    headers.setdefault("Content-Type", "application/json")

    wait_for_kiotviet_slot(retailer)

    return _client_for_method(method).request(method, url, headers=headers, timeout=timeout, **kwargs)


def wait_for_kiotviet_slot(retailer: str) -> None:
    """Giữ nhịp gọi ra KiotViet dưới trần tự áp, dùng chung giữa mọi worker.

    Key theo **retailer** vì hạn mức của KiotViet tính theo gian hàng — chia trần
    theo tiến trình thì n worker sẽ nhân lên n lần đúng thứ ta muốn giới hạn.

    CHỜ chứ không từ chối: bên gọi là job đồng bộ / thao tác in, không phải người
    dùng đang ngồi nhìn spinner một request. Chờ quá hạn thì vẫn đi tiếp
    (fail-open, có metric + log) — xem apps.common.rate_limiter.acquire.
    """
    limit = int(getattr(settings, "KIOTVIET_RATE_LIMIT_PER_SECOND", 0) or 0)
    if limit <= 0:
        return
    rate_limiter.acquire(
        key=f"kiotviet:{retailer or 'unknown'}",
        limit=limit,
        window=1,
        max_wait=float(getattr(settings, "KIOTVIET_RATE_LIMIT_MAX_WAIT", 10)),
        service="kiotviet",
    )


def _kiotviet_request_with_retry(
    order: OrderModel | None,
    channel: KiotVietChannel,
    method: str,
    endpoint: str,
    *,
    max_retries: int = 3,
    **kwargs,
) -> requests.Response:
    """Gọi ``_kiotviet_request`` với retry 5xx — điểm hợp nhất các vòng retry tay (audit BE-5).

    Trước đây engine sync hóa đơn có 6+ bản copy vòng ``for attempt in range(1, 4)``
    (command monolith + service + purge mixin). Helper này giữ NGUYÊN semantics của
    các vòng đó:

    - tối đa ``max_retries`` attempt; CHỈ retry khi status >= 500 (lỗi hạ tầng);
    - 4xx/429 trả response NGAY cho caller tự quyết — không retry mù, tránh lặp
      thao tác ghi lên hóa đơn thật;
    - backoff tuyến tính 2s, 4s giữa các attempt (2 * attempt).

    Cố tình KHÔNG chuyển sang HTTPAdapter Retry của ``apps.common.http_client``:
    retry ở tầng adapter gửi lại nguyên request cũ nên bỏ qua
    ``wait_for_kiotviet_slot`` (rate-limit theo retailer phải được acquire lại MỖI
    attempt) và bỏ qua cơ hội refresh token giữa các attempt. Caller vẫn chịu trách
    nhiệm check ``status_code`` cuối cùng (hàm này không raise).
    """
    response = None
    for attempt in range(1, max_retries + 1):
        response = _kiotviet_request(
            order=order, channel=channel, method=method, endpoint=endpoint, **kwargs
        )
        if response.status_code < 500 or attempt >= max_retries:
            return response
        time.sleep(2 * attempt)
    return response


def _phone_candidates(raw_phone: str | None) -> list[str]:
    """Generate possible phone formats for KiotViet lookup."""
    phone = str(raw_phone or "").strip()
    if not phone:
        return []

    digits = re.sub(r"\D+", "", phone)
    candidates: list[str] = []

    for value in [phone, digits]:
        if value and value not in candidates:
            candidates.append(value)

    if digits.startswith("84"):
        local = f"0{digits[2:]}"
        if local not in candidates:
            candidates.append(local)
    elif digits.startswith("0"):
        intl = f"84{digits[1:]}"
        if intl not in candidates:
            candidates.append(intl)

    return candidates


def _phone_for_kiotviet(*values: str | None) -> str:
    """
    Pick the first non-empty phone and normalize for KiotViet:
    - replace +84xxxxxxxxx -> 0xxxxxxxxx
    - replace 84xxxxxxxxx  -> 0xxxxxxxxx (when no leading 0)
    """
    for raw in values:
        phone = str(raw or "").strip()
        if not phone:
            continue

        compact = re.sub(r"\s+", "", phone)
        if compact.startswith("+84"):
            return f"0{compact[3:]}"

        digits = re.sub(r"\D+", "", compact)
        if digits.startswith("84") and not digits.startswith("840"):
            return f"0{digits[2:]}"

        return compact
    return ""


def _to_kiotviet_gender(value, default: bool | None = None) -> bool | None:
    """
    Map local gender representation to KiotViet boolean:
    - 1 / "1" / "male" / "nam" -> True
    - 0 / "0" / "female" / "nu" / "nữ" -> False
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))

    text = str(value).strip().lower()
    if text in {"1", "true", "male", "nam"}:
        return True
    if text in {"0", "false", "female", "nu", "nữ"}:
        return False
    return default


def _extract_customer_items(payload: dict | list | None) -> list[dict]:
    """Extract customer list from multiple possible KiotViet response shapes."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    data_obj = payload.get("data")
    if isinstance(data_obj, dict):
        for key in ["items", "results", "customers", "data"]:
            nested = data_obj.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]

    for key in ["data", "items", "results", "customers"]:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    if (payload.get("id") or payload.get("Id")) and (
        payload.get("code")
        or payload.get("Code")
        or payload.get("customerCode")
        or payload.get("CustomerCode")
        or payload.get("contactNumber")
        or payload.get("ContactNumber")
    ):
        return [payload]
    return []


def _normalize_customer_payload(customer: dict | None, fallback: dict | None = None) -> dict:
    """
    Normalize customer fields so downstream order payload always has a stable shape.
    """
    source = customer or {}
    fb = fallback or {}

    code = _sanitize_text(
        source.get("code")
        or source.get("Code")
        or source.get("customerCode")
        or source.get("CustomerCode")
        or fb.get("code")
        or fb.get("Code")
        or fb.get("customerCode")
        or fb.get("CustomerCode")
        or ""
    )
    payload = {
        "id": source.get("id") or source.get("Id") or fb.get("id") or fb.get("Id"),
        "code": code,
        "customerCode": code,
        "name": _sanitize_text(source.get("name") or source.get("Name") or fb.get("name") or fb.get("Name") or ""),
        "gender": _to_kiotviet_gender(
            source.get("gender", source.get("Gender")),
            _to_kiotviet_gender(fb.get("gender", fb.get("Gender"))),
        ),
        "birthDate": source.get("birthDate") or source.get("BirthDate") or fb.get("birthDate") or fb.get("BirthDate"),
        "contactNumber": _sanitize_text(
            source.get("contactNumber")
            or source.get("ContactNumber")
            or source.get("phoneNumber")
            or source.get("PhoneNumber")
            or fb.get("contactNumber")
            or fb.get("ContactNumber")
            or fb.get("phoneNumber")
            or fb.get("PhoneNumber")
            or ""
        ),
        "address": _sanitize_text(source.get("address") or source.get("Address") or fb.get("address") or fb.get("Address") or ""),
        "email": _sanitize_text(source.get("email") or source.get("Email") or fb.get("email") or fb.get("Email") or ""),
        "comment": _sanitize_text(
            source.get("comment")
            or source.get("Comment")
            or source.get("comments")
            or source.get("Comments")
            or fb.get("comment")
            or fb.get("Comment")
            or fb.get("comments")
            or fb.get("Comments")
            or ""
        ),
        "comments": _sanitize_text(
            source.get("comments")
            or source.get("Comments")
            or source.get("comment")
            or source.get("Comment")
            or fb.get("comments")
            or fb.get("Comments")
            or fb.get("comment")
            or fb.get("Comment")
            or ""
        ),
    }
    return payload


def _extract_prefixed_code(payload: dict | list | None, prefix: str) -> str:
    """
    Recursively find first string code that starts with prefix (e.g. DH, HD).
    """
    target = str(prefix or "").strip().upper()
    if not target:
        return ""

    queue = [payload]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            for value in current.values():
                queue.append(value)
            continue
        if isinstance(current, list):
            queue.extend(current)
            continue
        if isinstance(current, str):
            candidate = current.strip()
            if candidate.upper().startswith(target):
                return candidate
    return ""


def find_customer_by_phone(phone: str, order: OrderModel, channel: KiotVietChannel) -> dict | None:
    """
    Find existing customer by phone using POST /customers.
    Request body follows KiotViet search format:
    {
      "contactNumber": "...",
      "pageSize": 5
    }
    """
    for candidate in _phone_candidates(phone):
        # CHỈ dùng GET để search. POST /customers là endpoint TẠO khách (xem
        # gateway_kship_customer_webhook.create_customer) nên gọi POST với body search
        # luôn bị KiotViet từ chối 420 "Bạn chưa nhập chi nhánh" → vô ích, còn nuôi
        # rate-limit 429. Bỏ 2 lần POST sai, giữ 2 biến thể GET.
        request_attempts = (
            ("GET", {"params": {"contactNumber": candidate, "pageSize": 5}}),
            ("GET", {"params": {"keyword": candidate, "pageSize": 5}}),
        )
        for method, kwargs in request_attempts:
            try:
                response = _kiotviet_request(
                    order=order,
                    channel=channel,
                    method=method,
                    endpoint="/customers",
                    **kwargs,
                )
                if response.status_code != 200:
                    logger.info(
                        "Find customer by phone miss (method=%s, candidate=%s, status=%s, body=%s)",
                        method,
                        candidate,
                        response.status_code,
                        (response.text or "").strip(),
                    )
                    continue

                payload = response.json()
                items = _extract_customer_items(payload)
                if not items:
                    continue

                for item in items:
                    contact = str(
                        item.get("contactNumber")
                        or item.get("ContactNumber")
                        or item.get("phoneNumber")
                        or item.get("PhoneNumber")
                        or ""
                    ).strip()
                    if contact and (contact == candidate or re.sub(r"\D+", "", contact) == re.sub(r"\D+", "", candidate)):
                        return item

                # Fallback: if API already filtered and returned one record, use first.
                if len(items) == 1:
                    return items[0]
            except Exception as exc:
                logger.info(
                    "Find customer by phone error (method=%s, candidate=%s): %s",
                    method,
                    candidate,
                    exc,
                )
                continue
    return None


def find_customer_by_code(customer_code: str, order: OrderModel, channel: KiotVietChannel) -> dict | None:
    code = _sanitize_text(customer_code)
    if not code:
        return None
    try:
        response = _kiotviet_request(
            order=order,
            channel=channel,
            method="GET",
            endpoint=f"/customers/code/{code}",
        )
        if response.status_code == 200:
            payload = response.json() or {}
            if isinstance(payload, dict):
                normalized_code = str(
                    payload.get("code")
                    or payload.get("Code")
                    or payload.get("customerCode")
                    or payload.get("CustomerCode")
                    or ""
                ).strip()
                if normalized_code and not payload.get("code"):
                    payload["code"] = normalized_code
                return payload
            return None
        logger.info(
            "Find customer by code miss (code=%s, status=%s, body=%s)",
            code,
            response.status_code,
            (response.text or "").strip(),
        )
        return None
    except Exception as exc:
        logger.info("Find customer by code error (code=%s): %s", code, exc)
        return None


def _extract_list_payload(payload: dict | list | None) -> list[dict]:
    """Extract list of objects from common response envelopes."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ["data", "items", "results", "result", "list"]:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    data_value = payload.get("data")
    if isinstance(data_value, dict):
        for key in ["items", "results", "data", "list"]:
            nested = data_value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
    return []


def _get_latest_token_by_scope(channel: KiotVietChannel, scope: str) -> KiotVietAccessToken | None:
    return (
        KiotVietAccessToken.objects.filter(channel=channel, scope=scope)
        .order_by("-created_at")
        .first()
    )


def _jwt_expires_in_seconds(jwt_token: str, fallback_seconds: int = 3600) -> int:
    """Decode a JWT (no signature verification) and return ``exp - iat`` seconds.

    Falls back to ``fallback_seconds`` when the token can't be decoded or claims
    are missing. Used to size cache TTL so we don't keep server-invalidated tokens.
    """
    import base64

    try:
        parts = str(jwt_token or "").split(".")
        if len(parts) < 2:
            return fallback_seconds
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        exp = int(payload.get("exp") or 0)
        iat = int(payload.get("iat") or 0)
        if exp <= 0:
            return fallback_seconds
        if iat > 0 and exp > iat:
            return exp - iat
        # No iat claim — use exp - now.
        import time
        delta = exp - int(time.time())
        return delta if delta > 0 else fallback_seconds
    except (ValueError, TypeError, json.JSONDecodeError, base64.binascii.Error):
        return fallback_seconds


def invalidate_web_access_token(channel: KiotVietChannel) -> int:
    """Delete cached web tokens for ``channel``. Returns the number of rows removed.

    Call this when the upstream returns 422 "Token không hợp lệ" so the next
    ``get_web_access_token`` does a fresh login instead of returning the bad cache.
    """
    deleted, _ = KiotVietAccessToken.objects.filter(
        channel=channel, scope=KIOTVIET_TOKEN_SCOPE_WEB
    ).delete()
    return deleted


def get_access_token(
    order: OrderModel | None,
    channel: KiotVietChannel | None = None,
    timeout: float | None = None,
) -> str:
    """
    Get KiotViet access token by order channel credentials.

    OAuth token request:
    - URL: https://id.kiotviet.vn/connect/token
    - Content-Type: application/x-www-form-urlencoded
    - grant_type: client_credentials
    - scopes: PublicApi.Access
    - client_id/client_secret: from order.kiotviet_channel (or explicit channel)

    `timeout` (giây): siết thời gian chờ khi PHẢI xin token mới (token cache còn hạn thì
    không có HTTP nào cả). Hot path quét kho truyền `SCAN_VERIFY_TIMEOUT_SECONDS` xuống đây
    để không treo 30s chỉ vì token vừa hết hạn. Bỏ trống → giữ mặc định 30s như trước.
    """
    resolved_channel = _resolve_channel(order, channel)
    client_id = str(resolved_channel.client_id or "").strip()
    client_secret = str(resolved_channel.client_secret or "").strip()

    # EncryptedCharField automatically decrypts when loading from database
    # No need for explicit decryption here
    logger.debug(
        "Using client credentials (channel_id=%s, client_id=%s, secret_length=%s)",
        resolved_channel.id,
        client_id,
        len(client_secret) if client_secret else 0,
    )

    if not client_id or not client_secret:
        raise ValueError("Thiếu client_id hoặc client_secret của kênh KiotViet.")

    cached_token = _get_latest_token_by_scope(resolved_channel, KIOTVIET_TOKEN_SCOPE_PUBLIC)
    if cached_token and not cached_token.is_expired():
        return cached_token.access_token

    payload = {
        "scopes": KIOTVIET_TOKEN_SCOPE_PUBLIC,
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
    }

    # POST nhưng "thuần đọc": cấp token client_credentials không có side effect
    # nghiệp vụ (token thừa vô hại) → cho phép retry 429/5xx qua http_read.
    response = http_read.post(
        KIOTVIET_TOKEN_URL,
        data=payload,
        headers=headers,
        timeout=KIOTVIET_HTTP_TIMEOUT if timeout is None else timeout,
    )
    if response.status_code != 200:
        logger.error(
            "Failed to get KiotViet token for channel=%s status=%s body=%s",
            resolved_channel.id,
            response.status_code,
            response.text,
        )
        raise Exception(f"Failed to get KiotViet access token: {response.status_code}")

    token_data = response.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise ValueError("KiotViet token response missing access_token.")

    token = KiotVietAccessToken.objects.create(
        channel=resolved_channel,
        access_token=access_token,
        refresh_token=token_data.get("refresh_token"),
        expires_in=int(token_data.get("expires_in") or getattr(settings, "KIOTVIET_ACCESS_TOKEN_TTL", 86400)),
        token_type=token_data.get("token_type", "Bearer"),
        scope=KIOTVIET_TOKEN_SCOPE_PUBLIC,
    )
    return token.access_token


def get_web_access_token(
    order: OrderModel | None = None,
    channel: KiotVietChannel | None = None,
    force_refresh: bool = False,
) -> str:
    """Get KiotViet web token using username/password login flow.

    Set ``force_refresh=True`` to drop the cached token and re-login (use this
    after an upstream 422 "Token không hợp lệ" to recover from server-side
    session invalidation).
    """
    resolved_channel = _resolve_channel(order, channel)
    username = str(resolved_channel.username or "").strip()
    password = str(resolved_channel.password or "").strip()
    retailer = str(resolved_channel.retailer or "").strip()
    branch_id = str(resolved_channel.branch_id or "").strip()

    # EncryptedCharField automatically decrypts when loading from database
    # No need for explicit decryption here
    logger.debug(
        "Using web credentials (channel_id=%s, username=%s, password_length=%s)",
        resolved_channel.id,
        username,
        len(password) if password else 0,
    )

    if not username or not password:
        raise ValueError("Thiếu username hoặc password của kênh KiotViet.")
    if not retailer:
        raise ValueError("Thiếu retailer của kênh KiotViet.")

    if force_refresh:
        invalidate_web_access_token(resolved_channel)
    else:
        cached_token = _get_latest_token_by_scope(resolved_channel, KIOTVIET_TOKEN_SCOPE_WEB)
        if cached_token and not cached_token.is_expired():
            return cached_token.access_token

    if resolved_channel.old:
        # KiotViet cũ
        payload = {
            "model": {
                "RememberMe": True,
                "ShowCaptcha": False,
                "UserName": username,
                "Password": password,
                "Language": "vi-VN",
                "LatestBranchId": int(branch_id),
            },
            "IsManageSide": True,
            "FingerPrintKey": KIOTVIET_WEB_FINGERPRINT_KEY,
        }
    else:
        # KiotViet mới
        payload = {
            "model": {
                "UserName": username,
                "Password": password,
                "RememberMe": True,
                "ShowCaptcha": False,
                "LatestBranchId": int(branch_id),
                "FingerPrintKey": KIOTVIET_WEB_FINGERPRINT_KEY,
                "IsManageSide": True,
            }
        }
    headers = {
        "Content-Type": "application/json",
        "retailer": retailer,
    }

    # DEBUG: log request body
    import json as _json
    logger.info(
        "KiotViet web login request (channel=%s): url=%s headers=%s payload=%s",
        resolved_channel.id,
        KIOTVIET_WEB_LOGIN_URL,
        {k: v for k, v in headers.items() if k != "Content-Type"},
        _json.dumps(payload, ensure_ascii=False),
    )

    # KHÔNG retry: mỗi lần login web đều bị KiotViet đếm lượt đăng nhập (có thể
    # kích hoạt captcha/khoá tài khoản) và tạo session phía server → http_mutate.
    response = http_mutate.post(
        KIOTVIET_WEB_LOGIN_URL, json=payload, headers=headers, timeout=KIOTVIET_HTTP_TIMEOUT
    )
    logger.info(
        "KiotViet web login response (channel=%s): status=%s body=%s",
        resolved_channel.id,
        response.status_code,
        response.text[:500],
    )
    if response.status_code != 200:
        logger.error(
            "Failed to get KiotViet web token for channel=%s status=%s body=%s",
            resolved_channel.id,
            response.status_code,
            response.text,
        )
        raise Exception(f"Failed to get KiotViet web access token: {response.status_code}")

    token_data = response.json() or {}

    # KiotViet returns 200 even on login errors (wrong password, account locked)
    login_error = token_data.get("error")
    if login_error:
        logger.error(
            "KiotViet web login error (channel=%s): %s",
            resolved_channel.id,
            login_error,
        )
        raise ValueError(f"KiotViet web login failed: {login_error}")

    access_token = token_data.get("access_token") or token_data.get("token") or token_data.get("accessToken")
    if not access_token and isinstance(token_data.get("data"), dict):
        data_obj = token_data.get("data") or {}
        access_token = data_obj.get("access_token") or data_obj.get("token") or data_obj.get("accessToken")
    if not access_token:
        raise ValueError("KiotViet web token response missing access token.")

    token_type = token_data.get("token_type") or token_data.get("tokenType") or "Bearer"
    expires_in = _jwt_expires_in_seconds(access_token, fallback_seconds=3600)
    KiotVietAccessToken.objects.create(
        channel=resolved_channel,
        access_token=access_token,
        refresh_token=token_data.get("refresh_token") or token_data.get("refreshToken"),
        expires_in=expires_in,
        token_type=token_type,
        scope=KIOTVIET_TOKEN_SCOPE_WEB,
    )
    return access_token
