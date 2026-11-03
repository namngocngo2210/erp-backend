"""Client Open API Pancake POS (`https://pos.pages.fm/api/v1`, spec 29/09/2026 —
docs/pancake-pos-product-sync.md §2).

- Xác thực bằng query `?api_key=`. Key KHÔNG BAO GIỜ được vào log/exception:
  `requests` nhét nguyên URL (kèm query) vào message exception, nên mọi message đều
  dựng lại từ method + path và đi qua `redact()`.
- Điều tốc token bucket (`PANCAKE_POS_RPS`) + retry có backoff mũ và jitter.
  Chính sách retry theo tính idempotent:
    • GET: retry 429, 5xx, timeout, lỗi kết nối.
    • POST/PUT: chỉ retry 429 (Pancake từ chối, chưa ghi) và ConnectTimeout (chưa gửi
      được). 5xx/ReadTimeout có thể đã ghi → retry là tạo trùng; để lượt sau đối
      chiếu theo SKU (§3.3) tự liên kết.
    • `update_quantities` (đặt tồn TUYỆT ĐỐI): lô 1 dòng retry như GET; lô nhiều dòng
      chỉ retry 429/ConnectTimeout — API thật trả 500 ổn định cho lô 100 (30/09/2026),
      retry vô ích, người gọi chia nhỏ gửi từng dòng.
- `success: false` (kể cả HTTP 200) → `PancakePosApiError` mang message của Pancake.
- Parse phòng thủ: thiếu trường/sai kiểu → giá trị rỗng, không nổ.
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable, Iterator
from typing import Any

import requests

from apps.common.http_client import HttpClient
from apps.pancake_pos.domain.entities import PancakeOrder, PancakeProduct, PancakeVariation, PancakeWarehouse
from apps.pancake_pos.infrastructure.rate_limiter import TokenBucket

logger = logging.getLogger(__name__)

BASE_URL = "https://pos.pages.fm/api/v1"
PAGE_SIZE = 100
# Chặn vòng lặp vô hạn nếu Pancake trả total_pages sai: 100 × 1000 = 100k mẫu mã.
MAX_PAGES = 1000

_API_KEY_QS_RE = re.compile(r"(api_key=)[^&\s#'\"]+", re.IGNORECASE)


class PancakePosError(Exception):
    """Lỗi chung khi gọi Pancake POS."""


class PancakePosApiError(PancakePosError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PancakePosNotFound(PancakePosApiError):
    pass


class PancakePosRateLimited(PancakePosApiError):
    pass


class PancakePosTransportError(PancakePosError):
    """Timeout/lỗi mạng sau khi hết lượt retry."""


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


# Trường mẫu mã gửi lại NGUYÊN TRẠNG khi PUT (mẫu mã không thuộc CRM, hoặc mẫu mã
# CRM cần ẩn). CỐ Ý không có `variations_warehouses` (tồn theo kho — gửi lại là
# ghi đè tồn) và các trường chỉ-đọc (display_id, inserted_at, ...).
VARIATION_PASSTHROUGH_KEYS = (
    "fields", "images", "retail_price", "last_imported_price", "price_at_counter",
    "weight", "size", "barcode", "custom_id", "is_hidden",
)
PRODUCT_PASSTHROUGH_KEYS = ("name", "custom_id", "weight", "product_attributes")


def _variation_raw(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in VARIATION_PASSTHROUGH_KEYS:
        if key not in raw or raw[key] is None:
            continue
        value = raw[key]
        if key == "fields":
            # Chỉ gửi lại {name, value}; `id` của field là của Pancake.
            value = [
                {"name": _str(f.get("name")), "value": _str(f.get("value"))}
                for f in value if isinstance(f, dict) and f.get("name")
            ] if isinstance(value, list) else []
        out[key] = value
    return out


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_warehouse_quantities(raw: dict[str, Any]) -> dict[str, int]:
    """`variations_warehouses` → {warehouse_id: actual_remain_quantity} (tồn thực — cùng
    đại lượng mà `update_quantity` với `is_actual_remain_quantity=true` ghi). Thiếu
    `actual_remain_quantity` thì lấy `remain_quantity`; dòng hỏng thì bỏ."""
    out: dict[str, int] = {}
    items = raw.get("variations_warehouses")
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict):
            continue
        warehouse_id = _str(item.get("warehouse_id"))
        quantity = _int(item.get("actual_remain_quantity"))
        if quantity is None:
            quantity = _int(item.get("remain_quantity"))
        if warehouse_id and quantity is not None:
            out[warehouse_id] = quantity
    return out


def _flatten_categories(nodes: Any, parent_id: str = "") -> list[dict[str, str]]:
    """Cây danh mục (`{id, text, nodes}`) → danh sách phẳng {id, name, parent_id}."""
    out: list[dict[str, str]] = []
    for node in nodes if isinstance(nodes, list) else ():
        if not isinstance(node, dict) or node.get("id") is None:
            continue
        category_id = _str(node.get("id"))
        out.append({"id": category_id, "name": _str(node.get("text") or node.get("name")), "parent_id": parent_id})
        out.extend(_flatten_categories(node.get("nodes"), category_id))
    return out


def _product_raw(raw: dict[str, Any]) -> dict[str, Any]:
    return {k: raw[k] for k in PRODUCT_PASSTHROUGH_KEYS if raw.get(k) is not None}


def parse_variation(raw: Any) -> PancakeVariation | None:
    if not isinstance(raw, dict):
        return None
    variation_id = _str(raw.get("id"))
    if not variation_id:
        return None
    product = raw.get("product") if isinstance(raw.get("product"), dict) else {}
    return PancakeVariation(
        id=variation_id,
        product_id=_str(raw.get("product_id") or product.get("id")),
        custom_id=_str(raw.get("custom_id")),
        barcode=_str(raw.get("barcode")),
        product_custom_id=_str(product.get("custom_id")),
        is_hidden=raw.get("is_hidden") is True,
        raw=_variation_raw(raw),
        warehouse_quantities=parse_warehouse_quantities(raw),
    )


def parse_product(raw: Any) -> PancakeProduct | None:
    """GET products/{sku}: spec không ghi `id` sản phẩm ở gốc → lấy `id`, rồi
    `product_id` của mẫu mã. Chấp nhận cả dạng bọc `{"data": {...}}`."""
    if isinstance(raw, dict) and isinstance(raw.get("data"), dict):
        raw = raw["data"]
    if not isinstance(raw, dict):
        return None
    custom_id = _str(raw.get("custom_id"))
    raw_variations = raw.get("variations") if isinstance(raw.get("variations"), list) else []
    product_id = _str(raw.get("id") or raw.get("product_id"))
    if not product_id:
        product_id = next(
            (_str(v.get("product_id")) for v in raw_variations if isinstance(v, dict) and v.get("product_id")),
            "",
        )
    variations: list[PancakeVariation] = []
    for item in raw_variations:
        if not isinstance(item, dict) or not _str(item.get("id")):
            continue
        variations.append(
            PancakeVariation(
                id=_str(item.get("id")),
                product_id=_str(item.get("product_id")) or product_id,
                custom_id=_str(item.get("custom_id")),
                barcode=_str(item.get("barcode")),
                product_custom_id=custom_id,
                is_hidden=item.get("is_hidden") is True,
                raw=_variation_raw(item),
            )
        )
    if not product_id:
        return None
    return PancakeProduct(id=product_id, custom_id=custom_id, variations=tuple(variations), raw=_product_raw(raw))


def parse_order(raw: Any) -> PancakeOrder | None:
    """Đơn Pancake (GET/POST/PUT orders). Chấp nhận cả dạng bọc `{"data": {...}}`."""
    if isinstance(raw, dict) and isinstance(raw.get("data"), dict):
        raw = raw["data"]
    if not isinstance(raw, dict):
        return None
    order_id = _str(raw.get("id"))
    if not order_id:
        return None
    return PancakeOrder(
        id=order_id,
        status=_int(raw.get("status")),
        custom_id=_str(raw.get("custom_id")),
        warehouse_id=_str(raw.get("warehouse_id")),
    )


class PancakePosClient:
    """Cài đặt `contracts.ports.PancakePosGateway`."""

    def __init__(
        self,
        api_key: str,
        shop_id: int,
        *,
        rps: float = 2.0,
        timeout: float = 15.0,
        max_retries: int = 4,
        backoff_base: float = 1.0,
        backoff_cap: float = 30.0,
        http: HttpClient | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        if not api_key:
            raise PancakePosError("Thiếu PANCAKE_POS_API_KEY")
        # shop_id = 0 hợp lệ cho `list_shops` (kiểm tra key + chọn shop trên UI);
        # mọi API theo shop thì chặn ở `_shop_path`.
        self._api_key = api_key
        self._shop_id = int(shop_id or 0)
        self._max_retries = max(int(max_retries), 0)
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._sleep = sleep
        self._jitter = jitter
        self._bucket = TokenBucket(rps, clock=clock, sleep=sleep)
        # retries=0: retry do client này tự quyết theo method (xem docstring module).
        self._http = http or HttpClient(base_url=BASE_URL, timeout=timeout, retries=0)

    # ── tiện ích ────────────────────────────────────────────────────────────
    def redact(self, text: str) -> str:
        """Che api_key trong mọi chuỗi có thể lọt ra log/exception."""
        out = _API_KEY_QS_RE.sub(r"\1***", text or "")
        if self._api_key:
            out = out.replace(self._api_key, "***")
        return out

    def close(self) -> None:
        self._http.close()

    def _shop_path(self, suffix: str) -> str:
        if not self._shop_id:
            raise PancakePosError("Thiếu PANCAKE_POS_SHOP_ID")
        return f"/shops/{self._shop_id}/{suffix.lstrip('/')}"

    def _backoff(self, attempt: int, retry_after: float | None = None) -> float:
        delay = min(self._backoff_cap, self._backoff_base * (2**attempt))
        delay = delay * (0.5 + self._jitter() / 2)  # jitter: 50–100% của mức mũ
        if retry_after is not None:
            delay = max(delay, min(retry_after, self._backoff_cap))
        return delay

    @staticmethod
    def _retry_after(response: requests.Response) -> float | None:
        headers = getattr(response, "headers", None) or {}
        try:
            value = float(headers.get("Retry-After"))
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    def _error_message(self, body: Any, response: requests.Response) -> str:
        if isinstance(body, dict):
            for key in ("message", "error", "errors", "msg"):
                value = body.get(key)
                if value:
                    return self.redact(str(value))[:500]
        text = getattr(response, "text", "") or ""
        return self.redact(str(text))[:300] or f"HTTP {response.status_code}"

    @staticmethod
    def _json(response: requests.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None

    # ── lõi gửi request ─────────────────────────────────────────────────────
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        idempotent: bool | None = None,
    ) -> Any:
        """`idempotent=None` → theo method (chỉ GET); ghi idempotent (đặt tồn tuyệt đối)
        truyền True để được retry 5xx/timeout."""
        idempotent = method == "GET" if idempotent is None else idempotent
        query = {**(params or {}), "api_key": self._api_key}
        attempt = 0
        while True:
            self._bucket.acquire()
            try:
                response = self._http.request(method, path, params=query, json=json)
            except requests.RequestException as exc:
                retryable = idempotent or isinstance(exc, requests.ConnectTimeout)
                if retryable and attempt < self._max_retries:
                    delay = self._backoff(attempt)
                    logger.warning(
                        "pancake_pos: %s %s lỗi mạng (%s), thử lại sau %.1fs (lần %d)",
                        method, path, type(exc).__name__, delay, attempt + 1,
                    )
                    self._sleep(delay)
                    attempt += 1
                    continue
                # KHÔNG mang str(exc): chứa URL kèm api_key.
                raise PancakePosTransportError(
                    f"Không gọi được Pancake POS {method} {path} ({type(exc).__name__})"
                ) from None

            status = response.status_code
            if status == 429 or (idempotent and status >= 500):
                if attempt < self._max_retries:
                    delay = self._backoff(attempt, self._retry_after(response) if status == 429 else None)
                    logger.warning(
                        "pancake_pos: %s %s HTTP %d, thử lại sau %.1fs (lần %d)",
                        method, path, status, delay, attempt + 1,
                    )
                    self._sleep(delay)
                    attempt += 1
                    continue
                body = self._json(response)
                error_cls = PancakePosRateLimited if status == 429 else PancakePosApiError
                raise error_cls(
                    f"{method} {path}: HTTP {status} sau {attempt + 1} lần — {self._error_message(body, response)}",
                    status_code=status,
                )

            body = self._json(response)
            if status == 404:
                raise PancakePosNotFound(
                    f"{method} {path}: không tìm thấy — {self._error_message(body, response)}", status_code=404
                )
            if status >= 400:
                raise PancakePosApiError(
                    f"{method} {path}: HTTP {status} — {self._error_message(body, response)}", status_code=status
                )
            if not isinstance(body, dict):
                raise PancakePosApiError(f"{method} {path}: response không phải JSON object", status_code=status)
            if body.get("success") is False:
                raise PancakePosApiError(
                    f"{method} {path}: success=false — {self._error_message(body, response)}", status_code=status
                )
            return body

    # ── API công khai ───────────────────────────────────────────────────────
    def list_shops(self) -> list[dict[str, Any]]:
        body = self._request("GET", "/shops")
        shops = body.get("shops") if isinstance(body.get("shops"), list) else []
        return [
            {"id": s.get("id"), "name": _str(s.get("name"))}
            for s in shops
            if isinstance(s, dict) and s.get("id") is not None
        ]

    def iter_variations(self, *, search: str | None = None, page_size: int = PAGE_SIZE) -> Iterator[PancakeVariation]:
        """Duyệt hết mọi trang `products/variations` (page_size=100).

        Chống lặp trang: một số API Pancake bỏ qua `page_size/page_number` và trả lại
        đúng trang 1 cho mọi số trang (đã thấy ở marketplace/products, 30/09/2026) —
        trang không có mẫu mã nào mới thì dừng, mẫu mã đã trả thì không trả lại."""
        page = 1
        seen: set[str] = set()
        while page <= MAX_PAGES:
            params: dict[str, Any] = {"page_size": page_size, "page_number": page}
            if search:
                params["search"] = search
            body = self._request("GET", self._shop_path("products/variations"), params=params)
            items = body.get("data") if isinstance(body.get("data"), list) else []
            fresh = 0
            for raw in items:
                variation = parse_variation(raw)
                if variation is None or variation.id in seen:
                    continue
                seen.add(variation.id)
                fresh += 1
                yield variation
            if items and not fresh and page > 1:
                logger.warning("pancake_pos: products/variations trang %d lặp lại trang trước — dừng", page)
                return
            try:
                total_pages = int(body.get("total_pages") or 0)
            except (TypeError, ValueError):
                total_pages = 0
            # Không có total_pages → dừng khi trang thiếu; có thì dừng khi đủ số trang.
            if not items or (total_pages and page >= total_pages) or (not total_pages and len(items) < page_size):
                return
            page += 1
        logger.error("pancake_pos: iter_variations chạm trần %d trang — dừng", MAX_PAGES)

    def get_product(self, sku_or_id: str) -> PancakeProduct | None:
        try:
            body = self._request("GET", self._shop_path(f"products/{requests.utils.quote(sku_or_id, safe='')}"))
        except PancakePosNotFound:
            return None
        return parse_product(body)

    def create_product(self, payload: dict[str, Any]) -> None:
        # Response chỉ có {success} — id phải đọc lại theo custom_id (§2).
        self._request("POST", self._shop_path("products"), json=payload)

    def update_product(self, product_id: str, payload: dict[str, Any]) -> None:
        self._request("PUT", self._shop_path(f"products/{requests.utils.quote(product_id, safe='')}"), json=payload)

    def list_categories(self) -> list[dict[str, str]]:
        """Danh mục sản phẩm, làm phẳng cây: [{id, name, parent_id}] (id dạng chuỗi)."""
        body = self._request("GET", self._shop_path("categories"))
        return _flatten_categories(body.get("data"))

    def create_category(self, name: str, *, parent_id: str | None = None) -> str:
        """Tạo danh mục, trả id (chuỗi). Response `{success, data:{id, name}}`."""
        payload: dict[str, Any] = {"name": name}
        if parent_id:
            payload["parent_id"] = parent_id
        body = self._request("POST", self._shop_path("categories"), json=payload)
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        category_id = _str(data.get("id"))
        if not category_id:
            raise PancakePosApiError("POST categories: response không có id danh mục")
        return category_id

    def list_warehouses(self) -> list[PancakeWarehouse]:
        body = self._request("GET", self._shop_path("warehouses"))
        items = body.get("data") if isinstance(body.get("data"), list) else []
        return [
            PancakeWarehouse(id=_str(w.get("id")), name=_str(w.get("name")))
            for w in items
            if isinstance(w, dict) and _str(w.get("id"))
        ]

    def update_quantities(self, entries: list[dict[str, Any]], *, actual: bool = True) -> list[dict[str, str]]:
        """`POST variations/update_quantity` — đặt tồn TUYỆT ĐỐI cho nhiều (mẫu mã, kho).
        `entries` = [{variation_id, warehouse_id, remain_quantity}], người gọi tự chia lô.

        Response `{success, results: [{success, variation_id, ...}]}`: `success` gốc
        true vẫn có thể có dòng lỗi → trả danh sách dòng lỗi
        `[{variation_id, warehouse_id, message}]` (rỗng = mọi dòng thành công)."""
        payload = {"is_actual_remain_quantity": actual, "variations_warehouses": entries}
        body = self._request(
            "POST", self._shop_path("variations/update_quantity"), json=payload, idempotent=len(entries) <= 1
        )
        results = body.get("results") if isinstance(body.get("results"), list) else []
        failures: list[dict[str, str]] = []
        for item in results:
            if not isinstance(item, dict) or item.get("success") is not False:
                continue
            message = next((str(item[k]) for k in ("message", "error", "errors", "msg") if item.get(k)), "success=false")
            failures.append({
                "variation_id": _str(item.get("variation_id")),
                "warehouse_id": _str(item.get("warehouse_id")),
                "message": self.redact(message)[:500],
            })
        return failures

    # ── webhook (two-way-sync §5.1, B1) ─────────────────────────────────────
    def update_webhook(self, shop: dict[str, Any]) -> dict[str, Any]:
        """`PUT /shops/{SHOP_ID}` body `{"shop": {webhook_*}}` — đặt cấu hình tuyệt đối nên
        retry như GET. Trả `shop` trong response (có thể chứa header bí mật — người gọi che)."""
        path = self._shop_path("").rstrip("/")
        body = self._request("PUT", path, json={"shop": shop}, idempotent=True)
        return body.get("shop") if isinstance(body.get("shop"), dict) else {}

    # ── đơn hàng (two-way-sync §4.1, A2) ──────────────────────────────────────
    def create_order(self, payload: dict[str, Any]) -> PancakeOrder:
        """`POST orders` — KHÔNG retry 5xx/ReadTimeout (có thể đã tạo); người gọi tra lại
        theo `custom_id` ở lượt sau. Response là đơn vừa tạo (có `id`)."""
        body = self._request("POST", self._shop_path("orders"), json=payload)
        order = parse_order(body)
        if order is None:
            raise PancakePosApiError("POST orders: response không có id đơn")
        return order

    def get_order(self, order_id: str) -> PancakeOrder | None:
        try:
            body = self._request("GET", self._shop_path(f"orders/{requests.utils.quote(order_id, safe='')}"))
        except PancakePosNotFound:
            return None
        return parse_order(body)

    def update_order(self, order_id: str, payload: dict[str, Any]) -> PancakeOrder | None:
        """`PUT orders/{id}` — sửa hoặc huỷ (`{"status": 6}`). Không retry 5xx/ReadTimeout
        (lượt sau GET lại trạng thái rồi quyết định)."""
        body = self._request("PUT", self._shop_path(f"orders/{requests.utils.quote(order_id, safe='')}"), json=payload)
        return parse_order(body)

    def iter_orders_raw(self, *, since: int, until: int, page_size: int = PAGE_SIZE) -> Iterator[dict[str, Any]]:
        """Đơn (payload thô) CẬP NHẬT trong [since, until] (unix) — `GET orders` với
        `updateStatus=updated_at`, cũ trước. Chỉ đọc (backfill trạng thái vận chuyển, MR D).
        Chống lặp trang như `iter_variations`: trang không có đơn mới → dừng."""
        seen: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            body = self._request("GET", self._shop_path("orders"), params={
                "page_size": page_size, "page_number": page, "updateStatus": "updated_at",
                "startDateTime": int(since), "endDateTime": int(until), "option_sort": "last_updated_order_asc",
            })
            items = [o for o in (body.get("data") or []) if isinstance(o, dict) and _str(o.get("id"))]
            fresh = [o for o in items if _str(o.get("id")) not in seen]
            if not fresh:
                return
            for order in fresh:
                seen.add(_str(order.get("id")))
                yield order
            if len(items) < page_size:
                return

    def find_orders(self, *, custom_id: str, search: str, since: int | None = None) -> list[PancakeOrder]:
        """Tìm đơn theo mã tham chiếu CRM: `GET orders?search=<SĐT>` (spec: search theo
        SĐT/tên/ghi chú; không có lọc `custom_id`) rồi lọc `custom_id` khớp chính xác.
        `since` = unix time, giới hạn cửa sổ tạo đơn. Một trang 100 đơn là đủ: cùng SĐT,
        cùng cửa sổ vài giờ."""
        params: dict[str, Any] = {"search": search, "page_size": PAGE_SIZE, "page_number": 1}
        if since is not None:
            params["startDateTime"] = int(since)
        body = self._request("GET", self._shop_path("orders"), params=params)
        items = body.get("data") if isinstance(body.get("data"), list) else []
        out = []
        for raw in items:
            order = parse_order(raw)
            if order is not None and order.custom_id == custom_id:
                out.append(order)
        return out
