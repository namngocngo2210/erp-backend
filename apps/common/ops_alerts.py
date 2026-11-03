"""Kênh cảnh báo vận hành: đẩy sự cố hạ tầng/tích hợp tới người trực.

Vì sao cần: sự cố token Facebook 25–27/07/2026 kéo dài 2 ngày mà không ai biết —
cảnh báo duy nhất là ``logger.warning`` lặp 1.440 lần/ngày trong log worker. Log
không phải kênh gọi người.

Thiết kế:

- **Env-gated no-op** giống Sentry: chưa cấu hình ``LARK_OPS_ALERT_WEBHOOK_URL``
  thì không gọi mạng, nhưng VẪN ``logger.error`` để Sentry/Grafana bắt được.
- **Dedupe theo key** qua Redis: cron 5 phút/lần không được biến thành 288 tin
  nhắn/ngày. Hết cửa sổ dedupe thì nhắc lại — sự cố chưa xử lý phải còn kêu.
- **Không bao giờ ném exception**: cảnh báo hỏng không được kéo theo task nghiệp vụ.

Webhook dùng Lark custom bot (``/bot/v2/hook/...``). Bot bật xác thực chữ ký thì
đặt thêm ``LARK_OPS_ALERT_SECRET``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from collections.abc import Sequence

from django.core.cache import cache

from apps.common import runtime_config
from apps.common.http_client import HttpClient

logger = logging.getLogger(__name__)

_client = HttpClient()

DEFAULT_DEDUPE_SECONDS = 3600
_DEDUPE_KEY_PREFIX = "ops_alert:sent"


def _dedupe_key(key: str) -> str:
    return f"{_DEDUPE_KEY_PREFIX}:{key}"


def _webhook_url() -> str:
    return str(runtime_config.get("LARK_OPS_ALERT_WEBHOOK_URL"))


def _secret() -> str:
    return str(runtime_config.get("LARK_OPS_ALERT_SECRET"))


def _sign(timestamp: str, secret: str) -> str:
    """Chữ ký custom bot của Lark: HMAC-SHA256 với khoá ``"{timestamp}\\n{secret}"``."""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_ops_alert(
    *,
    key: str,
    title: str,
    lines: Sequence[str] = (),
    dedupe_seconds: int = DEFAULT_DEDUPE_SECONDS,
) -> bool:
    """Gửi một cảnh báo vận hành. Trả ``True`` nếu webhook đã nhận.

    ``key`` là danh tính của sự cố (vd ``"fb:token_invalid:2"``) — cùng key trong
    cùng cửa sổ ``dedupe_seconds`` chỉ kêu một lần.
    """
    if dedupe_seconds > 0 and not cache.add(_dedupe_key(key), "1", dedupe_seconds):
        return False

    body = "\n".join([title, *lines])
    # Log trước khi gửi: kể cả webhook chưa cấu hình hoặc chết, sự cố vẫn có dấu vết.
    logger.error("[ops-alert] %s", body.replace("\n", " | "))

    url = _webhook_url()
    if not url:
        return False

    payload: dict = {"msg_type": "text", "content": {"text": body}}
    secret = _secret()
    if secret:
        timestamp = str(int(time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = _sign(timestamp, secret)

    try:
        response = _client.post(url, json=payload, timeout=10)
    except Exception:  # noqa: BLE001 — cảnh báo hỏng không được giết task nghiệp vụ
        logger.exception("[ops-alert] gửi webhook thất bại (key=%s)", key)
        return False

    if response.status_code != 200:
        logger.error(
            "[ops-alert] webhook trả HTTP %s (key=%s): %s",
            response.status_code, key, (response.text or "")[:200],
        )
        return False
    return True
