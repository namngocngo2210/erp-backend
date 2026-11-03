"""Client Zenify — GET /v2/external/report/call-histories.

Tài liệu vendor: HuongDanTichHopWebSDKv2.3 (mục 2). Dùng chung credentials
ZENIFY_API_KEY/SECRET với sign-in webphone (services.py). Lỗi Zenify raise
ZenifyError NGUYÊN VĂN message (chuẩn error-passthrough của hệ thống).
"""

from __future__ import annotations

from datetime import datetime

import requests
from django.conf import settings

from apps.common.http_client import HttpClient
from apps.sysconfig import sysconfig

from .services import ZenifyError, _extract_error_message

CALL_HISTORIES_PATH = "/v2/external/report/call-histories"
# Format from/to theo doc: "YYYY-MM-DD HH:mm" (giờ VN).
ZENIFY_TIME_FORMAT = "%Y-%m-%d %H:%M"


def format_zenify_time(value: datetime) -> str:
    return value.strftime(ZENIFY_TIME_FORMAT)


def fetch_call_histories(from_str: str, to_str: str) -> list[dict]:
    """Trả list item cuộc gọi (dict raw của Zenify) trong khoảng [from, to].

    Task nền (không có user chờ) nên timeout 30s + retry mặc định của HttpClient.
    """
    client = HttpClient(base_url=settings.ZENIFY_API_BASE_URL, timeout=30)
    try:
        resp = client.get(
            CALL_HISTORIES_PATH,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "api-key": sysconfig.get("ZENIFY_API_KEY"),
                "api-secret": sysconfig.get("ZENIFY_API_SECRET"),
            },
            params={"from": from_str, "to": to_str},
        )
    except requests.RequestException as exc:
        raise ZenifyError(f"Không kết nối được Zenify: {exc}") from exc
    finally:
        client.close()

    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    data = payload.get("data")
    if resp.status_code != 200 or payload.get("error") is True or not isinstance(data, list):
        detail = _extract_error_message(payload) or resp.text[:500] or f"HTTP {resp.status_code}"
        raise ZenifyError(f"Zenify call-histories thất bại: {detail}")
    return [item for item in data if isinstance(item, dict)]
