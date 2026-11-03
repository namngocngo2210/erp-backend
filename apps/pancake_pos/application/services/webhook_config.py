"""Cấu hình webhook trên shop Pancake (`PUT /shops/{SHOP_ID}`) — two-way-sync §5.1, B1.

Pancake không ký payload → xác thực bằng header bí mật tự đặt `X-Desi-Webhook-Secret`
(giá trị = sysconfig `PANCAKE_POS_WEBHOOK_SECRET`) khai trong `webhook_headers`.
URL = sysconfig `PANCAKE_POS_WEBHOOK_URL` (vd `https://<domain prod>/api/pancake-pos/webhook`).

Dry-run chỉ dựng body (che secret), không gọi Pancake. Ghi thật cần `PANCAKE_POS_ENABLED`
(staging luôn tắt) — KHÔNG có đường tự chạy: chỉ lệnh tay / nút có quyền `pancake-pos:edit`.
Chưa kiểm chứng bằng key thật: PUT có giữ nguyên `webhook_email` khi không gửi hay không.
"""

from __future__ import annotations

from typing import Any

from apps.pancake_pos.application.services.factory import build_client, ensure_write_enabled
from apps.sysconfig import sysconfig

WEBHOOK_HEADER = "X-Desi-Webhook-Secret"
DEFAULT_TYPES = ("orders", "products", "variations_warehouses")
ALLOWED_TYPES = frozenset({"orders", "customers", "products", "variations_warehouses"})
MIN_SECRET_LEN = 16


class WebhookConfigError(ValueError):
    """Cấu hình thiếu/sai — không gọi Pancake."""


def mask(secret: str) -> str:
    return f"***{secret[-4:]}" if len(secret) >= 8 else "***"


def build_body(*, url: str | None = None, types: list[str] | None = None) -> tuple[dict[str, Any], str]:
    """(body `shop` cần PUT, secret). Kiểm URL https + secret đủ dài + loại hợp lệ."""
    url = (url or str(sysconfig.get("PANCAKE_POS_WEBHOOK_URL") or "")).strip()
    secret = str(sysconfig.get("PANCAKE_POS_WEBHOOK_SECRET") or "").strip()
    chosen = [t.strip() for t in (types or DEFAULT_TYPES) if t and t.strip()]
    if not url:
        raise WebhookConfigError("Thiếu PANCAKE_POS_WEBHOOK_URL.")
    if not url.startswith("https://"):
        # Header bí mật đi trong request — http là gửi secret qua đường trần.
        raise WebhookConfigError("PANCAKE_POS_WEBHOOK_URL phải là HTTPS.")
    if len(secret) < MIN_SECRET_LEN:
        raise WebhookConfigError(
            f"PANCAKE_POS_WEBHOOK_SECRET trống hoặc ngắn hơn {MIN_SECRET_LEN} ký tự — endpoint sẽ từ chối mọi request."
        )
    unknown = sorted(set(chosen) - ALLOWED_TYPES)
    if unknown or not chosen:
        raise WebhookConfigError(f"Loại webhook không hợp lệ: {', '.join(unknown) or '(trống)'}.")
    body = {
        "webhook_enable": True,
        "webhook_url": url,
        "webhook_types": chosen,
        "webhook_headers": {WEBHOOK_HEADER: secret},
    }
    return body, secret


def masked_body(body: dict[str, Any], secret: str) -> dict[str, Any]:
    headers = {k: (mask(secret) if v == secret else v) for k, v in (body.get("webhook_headers") or {}).items()}
    return {**body, "webhook_headers": headers}


def configure(*, dry_run: bool, url: str | None = None, types: list[str] | None = None) -> dict[str, Any]:
    body, secret = build_body(url=url, types=types)
    out: dict[str, Any] = {"dry_run": dry_run, "body": {"shop": masked_body(body, secret)}, "response": None}
    if dry_run:
        return out
    ensure_write_enabled(dry_run=False)
    client = build_client()
    try:
        shop = client.update_webhook(body)
    finally:
        client.close()
    out["response"] = {
        k: v for k, v in masked_body(shop, secret).items() if k.startswith("webhook_")
    }
    return out


def local_state() -> dict[str, Any]:
    """Cấu hình phía CRM (không gọi Pancake — spec không có GET cấu hình webhook)."""
    secret = str(sysconfig.get("PANCAKE_POS_WEBHOOK_SECRET") or "").strip()
    return {
        "url": str(sysconfig.get("PANCAKE_POS_WEBHOOK_URL") or ""),
        "secret_set": len(secret) >= MIN_SECRET_LEN,
        "header": WEBHOOK_HEADER,
        "types": list(DEFAULT_TYPES),
        "create_orders": bool(sysconfig.get("PANCAKE_POS_INBOUND_CREATE_ORDERS")),
        "dispatch_enabled": bool(sysconfig.get("PANCAKE_POS_DISPATCH_ENABLED")),
        "unmatched_hours": int(sysconfig.get("PANCAKE_POS_INBOUND_UNMATCHED_HOURS") or 0),
    }
