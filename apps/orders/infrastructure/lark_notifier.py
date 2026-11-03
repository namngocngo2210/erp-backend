"""Lark Custom Bot notifier for incoming orders.

Sends a plain text message to a configured Lark group chat via webhook when a
new order is ingested. Kept dependency-free beyond `requests` so it can be
called from background tasks without extra setup.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import time
from typing import NamedTuple

import requests

from apps.common.http_client import HttpClient
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)

# Shared pooled client; retries transient 429/5xx + connection errors with backoff.
# Per-call timeout is passed through to override the client default.
_client = HttpClient()

# Tên sale đứng ĐẦU tin trong ngoặc vuông (chốt 26/08): nhóm Lark chạy rất nhanh,
# sale phải nhận ra đơn của mình ngay ở ký tự đầu dòng thay vì đọc hết câu.
DEFAULT_MESSAGE_TEMPLATE = "[{staff_name}] Có đơn hàng mới ở kênh {channel_name}, check nha"

# Ghép bù khi `LARK_ORDER_BOT_MESSAGE` là mẫu CŨ (đặt trước 2026-08-05, không có
# ô {staff_name}): tên sale phải xuất hiện trong tin, không được rơi im lặng chỉ
# vì một biến env viết từ trước khi có tính năng. Ghép vào ĐẦU tin cho khớp mẫu
# mặc định — cùng một chỗ để mắt nhìn, dù prod đang chạy mẫu nào.
STAFF_NAME_PREFIX = "[{staff_name}] "

# Nhận ô {staff_name} kể cả khi có conversion/format spec ({staff_name!r},
# {staff_name:>10}) — chỉ `in` chuỗi thì bỏ sót và ghép bù thừa.
_STAFF_PLACEHOLDER_RE = re.compile(r"\{staff_name[!:}]")

# Cảnh báo cấu hình chỉ log MỘT lần mỗi process: tin báo đơn mới bắn hàng trăm
# lần/ngày, warn mỗi lần là lấp log chứ không phải tín hiệu.
_warned_missing_staff_placeholder = False


class LarkBotConfig(NamedTuple):
    webhook_url: str
    secret: str
    timeout: float


def order_bot_config() -> LarkBotConfig:
    return LarkBotConfig(
        webhook_url=sysconfig.get("LARK_ORDER_BOT_WEBHOOK_URL"),
        secret=sysconfig.get("LARK_ORDER_BOT_SECRET"),
        timeout=sysconfig.get("LARK_ORDER_BOT_TIMEOUT_SECONDS"),
    )


def _sign(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _render_message(channel_name: str | None, staff_name: str | None = None) -> str:
    global _warned_missing_staff_placeholder

    template = sysconfig.get("LARK_ORDER_BOT_MESSAGE") or DEFAULT_MESSAGE_TEMPLATE
    name = (channel_name or "").strip() or "—"
    # Đơn ngoài giờ làm việc không auto-chia → chưa có NV được gán.
    staff = (staff_name or "").strip() or "chưa gán"
    try:
        text = template.format(channel_name=name, staff_name=staff)
    except (KeyError, IndexError):
        logger.warning("Invalid LARK_ORDER_BOT_MESSAGE template: %s", template)
        return DEFAULT_MESSAGE_TEMPLATE.format(channel_name=name, staff_name=staff)

    if _STAFF_PLACEHOLDER_RE.search(template) is None:
        if not _warned_missing_staff_placeholder:
            logger.warning(
                "LARK_ORDER_BOT_MESSAGE thiếu ô {staff_name}, đang ghép bù tên NV vào "
                "đầu tin — cập nhật lại biến env cho gọn: %s",
                template,
            )
            _warned_missing_staff_placeholder = True
        text = STAFF_NAME_PREFIX.format(staff_name=staff) + text
    return text


def _post_lark_body(*, webhook_url: str, secret: str, body: dict, timeout: float) -> bool:
    """Low-level POST of an already-shaped Lark webhook body. Returns True on success."""
    if not webhook_url:
        return False

    if secret:
        timestamp = int(time.time())
        body = {**body, "timestamp": str(timestamp), "sign": _sign(secret, timestamp)}

    # Timeout giờ là số float người vận hành nhập trên UI (sysconfig); 0 hoặc âm lọt tới
    # requests sẽ raise ValueError (không phải RequestException) → nổ ra ngoài thay vì
    # trả False. Kẹp sàn 0.5s: vẫn "gần như không chờ" nhưng không làm hỏng luồng gọi.
    timeout = max(float(timeout), 0.5)
    try:
        response = _client.post(webhook_url, json=body, timeout=timeout)
    except requests.RequestException:
        logger.exception("Lark notify failed (network)")
        return False

    if response.status_code >= 400:
        logger.warning(
            "Lark notify non-2xx: status=%s body=%s",
            response.status_code,
            response.text[:500],
        )
        return False

    try:
        payload = response.json()
    except ValueError:
        payload = {}
    status_code = payload.get("StatusCode", payload.get("code", 0))
    if status_code not in (0, None):
        logger.warning("Lark notify rejected: payload=%s", payload)
        return False

    return True


def _post_lark_text(*, webhook_url: str, secret: str, text: str, timeout: float) -> bool:
    """Low-level POST of a `text` Lark message. Returns True on success."""
    return _post_lark_body(
        webhook_url=webhook_url,
        secret=secret,
        body={"msg_type": "text", "content": {"text": text}},
        timeout=timeout,
    )


def send_order_created_notification(*, channel_name: str | None = None, staff_name: str | None = None) -> bool:
    cfg = order_bot_config()
    return _post_lark_text(
        webhook_url=cfg.webhook_url,
        secret=cfg.secret,
        text=_render_message(channel_name, staff_name),
        timeout=cfg.timeout,
    )


def send_vat_invoice_notification(text: str) -> bool:
    """Post a free-form text message to the VAT-invoice bot (separate webhook).

    Dùng để báo team kế toán những đơn cần xuất hoá đơn VAT (cá nhân/công ty)
    sau khi giao kship thành công. Empty webhook → tắt tính năng âm thầm.
    """
    return _post_lark_text(
        webhook_url=sysconfig.get("LARK_VAT_BOT_WEBHOOK_URL"),
        secret=sysconfig.get("LARK_VAT_BOT_SECRET"),
        text=text,
        timeout=sysconfig.get("LARK_VAT_BOT_TIMEOUT_SECONDS"),
    )


def send_stock_negative_alert(text: str) -> bool:
    """Post a free-form text alert to the inventory bot (separate webhook).

    Dùng để cảnh báo team kho/kế toán khi tồn kho bị âm sau khi trừ tồn xuất đơn.
    Empty webhook → tắt tính năng âm thầm (chỉ còn log warning ở subscriber).
    """
    return _post_lark_text(
        webhook_url=sysconfig.get("LARK_INVENTORY_BOT_WEBHOOK_URL"),
        secret=sysconfig.get("LARK_INVENTORY_BOT_SECRET"),
        text=text,
        timeout=sysconfig.get("LARK_INVENTORY_BOT_TIMEOUT_SECONDS"),
    )


def send_cancelled_dispatch_alert(text: str) -> bool:
    """Post a free-form text alert to the cancelled-after-dispatch bot (separate webhook).

    Báo kho NGAY khi một đơn đã quét xuất kho bị huỷ — hàng đang ngoài kho, phải đi
    đòi về. Webhook riêng chứ không dùng chung bot tồn âm: hai loại cảnh báo khác
    người xử lý và khác mức khẩn. Empty webhook → tắt tính năng âm thầm.
    """
    return _post_lark_text(
        webhook_url=sysconfig.get("LARK_CANCEL_DISPATCH_BOT_WEBHOOK_URL"),
        secret=sysconfig.get("LARK_CANCEL_DISPATCH_BOT_SECRET"),
        text=text,
        timeout=sysconfig.get("LARK_CANCEL_DISPATCH_BOT_TIMEOUT_SECONDS"),
    )


def send_sale_reminder_text(text: str) -> bool:
    """Post a free-form text message to the sale-reminder bot (separate webhook)."""
    return _post_lark_text(
        webhook_url=sysconfig.get("LARK_SALE_REMINDER_WEBHOOK_URL"),
        secret=sysconfig.get("LARK_SALE_REMINDER_SECRET"),
        text=text,
        timeout=sysconfig.get("LARK_SALE_REMINDER_TIMEOUT_SECONDS"),
    )


def send_sale_reminder_post(body: dict) -> bool:
    """Post a Lark `msg_type=post` rich-text body to the sale-reminder bot.

    The caller is expected to pass the full body shape, e.g.::

        {"msg_type": "post", "content": {"post": {"zh_cn": {"title": "",
            "content": [[{"tag": "text", "text": "..."}]]}}}}
    """
    return _post_lark_body(
        webhook_url=sysconfig.get("LARK_SALE_REMINDER_WEBHOOK_URL"),
        secret=sysconfig.get("LARK_SALE_REMINDER_SECRET"),
        body=body,
        timeout=sysconfig.get("LARK_SALE_REMINDER_TIMEOUT_SECONDS"),
    )
