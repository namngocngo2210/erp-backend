"""Tra cứu hành trình / phí tạm tính / in vận đơn Best Express.

TraceQuery sync ngược vào BestShipment dùng CHUNG đường event với webhook
(store_event + apply_event) — idempotent nhờ unique constraint trên event.
"""

import logging

from apps.best_express.application.services.webhook_service import apply_event, store_event
from apps.best_express.infrastructure.best_client import (
    PRINT_MAX_CODES,
    TRACE_MAX_CODES,
    BestExpressClient,
)
from apps.best_express.models import BestExpressAccount

logger = logging.getLogger(__name__)

_TRACE_EVENT_FIELDS = (
    "status",
    "status_content",
    "modified_at",
    "cod",
    "product_price",
    "freight_fee",
    "cod_fee",
    "insurance_fee",
    "vat_fee",
    "return_fee",
    "discount",
    "total_fee",
    "weight",
    "length",
    "width",
    "height",
)


def _chunks(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def trace(
    account: BestExpressAccount, codes: list[str], lang: str = "en-US"
) -> list[dict]:
    client = BestExpressClient(account)
    cleaned = [str(code).strip() for code in codes if str(code or "").strip()]
    results: list[dict] = []
    for chunk in _chunks(cleaned, TRACE_MAX_CODES):
        data = client.trace_query(chunk, lang=lang)
        results.extend(data.get("data") or [])
    _sync_trace_results(account, results)
    return results


def _sync_trace_results(account: BestExpressAccount, results: list[dict]) -> None:
    for item in results:
        code_best = str(item.get("code_best") or "").strip()
        merchant_order_code = str(item.get("merchant_order_code") or "").strip()
        trace_logs = item.get("traceLogs") or []
        ordered = sorted(
            (log for log in trace_logs if isinstance(log, dict)),
            key=lambda log: log.get("modified_at") or 0,
        )
        for log in ordered:
            payload = {
                key: log[key] for key in _TRACE_EVENT_FIELDS if key in log
            }
            payload["code_best"] = code_best
            payload["merchant_order_code"] = merchant_order_code
            event = store_event(account, payload)
            if event is not None:
                apply_event(event)


def estimate_fee(account: BestExpressAccount, params: dict) -> dict:
    data = BestExpressClient(account).estimate_fee(params)
    return data.get("data") or {}


def print_waybills(account: BestExpressAccount, codes: list[str]) -> list[str]:
    client = BestExpressClient(account)
    cleaned = [str(code).strip() for code in codes if str(code or "").strip()]
    urls: list[str] = []
    for chunk in _chunks(cleaned, PRINT_MAX_CODES):
        data = client.print_waybill(chunk)
        url = str(data.get("Url") or "").strip()
        if url:
            urls.append(url)
    return urls
