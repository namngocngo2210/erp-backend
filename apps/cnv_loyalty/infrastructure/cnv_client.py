"""REST client cho CNV Loyalty API (apis.cnvloyalty.com).

Header ``Authorization: Token {access_token}`` (scheme "Token", KHÔNG phải
"Bearer" — khác với chính SSO của CNV). 401 → force refresh token đúng 1 lần
rồi thử lại. Đường path theo tài liệu CNV; tài liệu có chỗ gõ
``/custom_collections.json/{id}.json`` — coi là typo, dùng dạng chuẩn
``/{resource}/{id}.json`` (đối chiếu Swagger khi có token thật).
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

from apps.cnv_loyalty.exceptions import CnvApiError
from apps.cnv_loyalty.infrastructure.token_provider import ensure_access_token
from apps.cnv_loyalty.models import CnvConnection
from apps.common.http_client import HttpClient

logger = logging.getLogger(__name__)

_TIMEOUT = 15


class CnvLoyaltyClient:
    def __init__(self, connection: CnvConnection, http: HttpClient | None = None):
        self.connection = connection
        self._http = http or HttpClient(
            base_url=connection.resolve_api_base_url(), timeout=_TIMEOUT
        )

    # -- core ---------------------------------------------------------------

    def _headers(self) -> dict:
        _token_type, token = ensure_access_token(self.connection)
        return {"Accept": "application/json", "Authorization": f"Token {token}"}

    def _request(self, method: str, path: str, **kwargs) -> dict:
        # Staging chạy snapshot dữ liệu prod — không được ghi lên CNV thật
        # (cùng lý do kill switch của KiotViet web outbound).
        if getattr(settings, "IS_STAGING", False):
            raise RuntimeError("CNV outbound bị chặn trên staging (APP_ENV=staging).")
        response = self._send(method, path, self._headers(), **kwargs)
        if response.status_code == 401:
            logger.warning("CNV %s %s: 401 — refresh token và thử lại", method, path)
            ensure_access_token(self.connection, force_refresh=True)
            response = self._send(method, path, self._headers(), **kwargs)
        if response.status_code >= 400:
            raise CnvApiError(
                f"CNV {method} {path} HTTP {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
            )
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise CnvApiError(
                f"CNV {method} {path}: phản hồi không phải JSON: {response.text[:200]}"
            ) from exc
        # CNV trả lỗi nghiệp vụ dưới dạng HTTP 200 + {"errors": ...} (quan sát prod
        # 2026-08-06: "Contact email or phone is existed", "Data is invalid") —
        # phải coi là lỗi thật, không để caller đọc thiếu id rồi báo lỗi mù mờ.
        if isinstance(data, dict) and data.get("errors"):
            raise CnvApiError(
                f"CNV {method} {path}: {str(data['errors'])[:300]}",
                status_code=response.status_code,
                payload=data,
            )
        return data if isinstance(data, dict) else {"data": data}

    def _send(self, method: str, path: str, headers: dict, **kwargs):
        try:
            return self._http.request(method, path, headers=headers, **kwargs)
        except requests.exceptions.RequestException as exc:
            raise CnvApiError(f"CNV không phản hồi: {exc}") from exc

    # -- customers ----------------------------------------------------------

    def list_customers(self, params: dict | None = None) -> dict:
        return self._request("GET", "/customers.json", params=params or {})

    def get_customer(self, customer_id: int) -> dict:
        return self._request("GET", f"/customers/{customer_id}.json")

    def create_customer(self, payload: dict) -> dict:
        return self._request("POST", "/customers.json", json=payload)

    # -- products -----------------------------------------------------------

    def list_products(self, params: dict | None = None) -> dict:
        return self._request("GET", "/products.json", params=params or {})

    def get_product(self, product_id: int) -> dict:
        return self._request("GET", f"/products/{product_id}.json")

    def create_product(self, payload: dict) -> dict:
        return self._request("POST", "/products.json", json=payload)

    def update_product(self, product_id: int, payload: dict) -> dict:
        return self._request("PUT", f"/products/{product_id}.json", json=payload)

    def delete_product(self, product_id: int) -> dict:
        return self._request("DELETE", f"/products/{product_id}.json")

    # -- collections --------------------------------------------------------

    def list_custom_collections(self, params: dict | None = None) -> dict:
        return self._request("GET", "/custom_collections.json", params=params or {})

    def get_custom_collection(self, collection_id: int) -> dict:
        return self._request("GET", f"/custom_collections/{collection_id}.json")

    def create_custom_collection(self, payload: dict) -> dict:
        return self._request("POST", "/custom_collections.json", json=payload)

    def create_collect(self, collection_id: int, product_id: int) -> dict:
        """Gắn một product vào một custom collection."""
        return self._request(
            "POST",
            "/collects.json",
            json={"collect": {"collection_id": collection_id, "product_id": product_id}},
        )

    def list_smart_collections(self, params: dict | None = None) -> dict:
        return self._request("GET", "/smart_collections.json", params=params or {})

    def get_smart_collection(self, collection_id: int) -> dict:
        return self._request("GET", f"/smart_collections/{collection_id}.json")

    # -- orders -------------------------------------------------------------

    def list_orders(self, params: dict | None = None) -> dict:
        return self._request("GET", "/orders.json", params=params or {})

    def get_order(self, order_id: int) -> dict:
        return self._request("GET", f"/orders/{order_id}.json")

    def create_order(self, payload: dict) -> dict:
        return self._request("POST", "/orders.json", json=payload)

    def update_order(self, order_id: int, payload: dict) -> dict:
        # ĐÍNH CHÍNH 2026-08-19: swagger v1 CÓ mô tả `PUT /orders/{id}.json`
        # (ghi chú cũ "tài liệu không mô tả" dựa trên bản PDF, sai). Vẫn không
        # tự fallback tạo đơn mới khi lỗi — spec §3.4, tránh nhân đôi đơn phía CNV.
        return self._request("PUT", f"/orders/{order_id}.json", json=payload)

    def get_order_by_external_id(self, external_id: str) -> dict:
        """Tra đơn theo `unique_id` mình gửi lên (dạng ``desi-<order_id>``).

        Đây là đường đối chiếu khi nghi ngờ mapping lệch: CNV index theo external
        id nên tra được mà không cần biết `cnv_id`.
        """
        return self._request("GET", f"/orders/external/{external_id}.json")

    def count_orders(self, params: dict | None = None) -> dict:
        return self._request("GET", "/orders/count.json", params=params or {})

    def update_order_status(self, order_id: int, payload: dict) -> dict:
        """Đổi riêng trạng thái đơn, không phải đẩy lại toàn bộ payload."""
        return self._request("PUT", f"/orders/order-status/{order_id}.json", json=payload)

    # -- webhook ------------------------------------------------------------

    def list_webhooks(self) -> dict:
        """Xem CNV đang đăng ký gửi webhook về đâu, topic nào.

        Đây là cách DUY NHẤT biết chắc tên topic hợp lệ: swagger khai `topics` là
        `array<string>` không kèm danh sách giá trị, tài liệu công khai cũng
        không có. Chạy lệnh này trước khi register.
        """
        return self._request("GET", "/webhook/register")

    def register_webhook(
        self, callback_url: str, verify_token: str, topics: list[str]
    ) -> dict:
        return self._request(
            "POST",
            "/webhook/register",
            json={
                "callback_url": callback_url,
                "verify_token": verify_token,
                "topics": list(topics),
            },
        )

    def delete_webhook(self) -> dict:
        return self._request("DELETE", "/webhook/register")

    def subscribe_webhook(self, payload: dict | None = None) -> dict:
        return self._request("POST", "/webhook/subscribe", json=payload or {})

    def unsubscribe_webhook(self, payload: dict | None = None) -> dict:
        return self._request("DELETE", "/webhook/subscribe", json=payload or {})

    # -- tra cứu theo external id (dedup + đối soát) ------------------------

    def get_customer_by_external_id(self, external_id: str) -> dict:
        return self._request("GET", f"/customers/external/{external_id}.json")

    def get_customer_by_phone(self, phone: str) -> dict:
        """Tra khách theo SĐT.

        ĐÍNH CHÍNH 2026-08-19: probe 06/08 kết luận "CNV không cho lọc khách theo
        SĐT" vì thử tham số trên `/customers.json`. Thực tế CNV có endpoint
        riêng cho việc này — nhờ nó, khách đã tồn tại sẵn phía CNV lấy lại được
        `cnv_id` thay vì đánh dấu synced với id rỗng.
        """
        return self._request("GET", "/customers/get-by-phone.json", params={"phone": phone})

    def update_customer(self, customer_id: int, payload: dict) -> dict:
        return self._request("PUT", f"/customers/{customer_id}.json", json=payload)

    def add_customer_tags(self, customer_id: int, payload: dict) -> dict:
        return self._request("POST", f"/customer/{customer_id}/tags.json", json=payload)

    def get_product_by_external_id(self, external_id: str) -> dict:
        return self._request("GET", f"/products/external/{external_id}.json")

    def get_custom_collection_by_external_id(self, external_id: str) -> dict:
        return self._request("GET", f"/custom_collections/external/{external_id}.json")

    # -- đếm (biết tồn đọng trước khi quét) ---------------------------------

    def count_customers(self, params: dict | None = None) -> dict:
        return self._request("GET", "/customers/count.json", params=params or {})

    def count_products(self, params: dict | None = None) -> dict:
        return self._request("GET", "/products/count.json", params=params or {})

    def count_collects(self, params: dict | None = None) -> dict:
        return self._request("GET", "/collects/count.json", params=params or {})

    def count_custom_collections(self, params: dict | None = None) -> dict:
        return self._request("GET", "/custom_collections/count.json", params=params or {})

    def count_smart_collections(self, params: dict | None = None) -> dict:
        return self._request("GET", "/smart_collections/count.json", params=params or {})

    # -- collection CRUD còn thiếu ------------------------------------------

    def update_custom_collection(self, collection_id: int, payload: dict) -> dict:
        return self._request("PUT", f"/custom_collections/{collection_id}.json", json=payload)

    def delete_custom_collection(self, collection_id: int) -> dict:
        return self._request("DELETE", f"/custom_collections/{collection_id}.json")

    def list_collects(self, params: dict | None = None) -> dict:
        return self._request("GET", "/collects.json", params=params or {})

    def delete_collect(self, collect_id: int) -> dict:
        return self._request("DELETE", f"/collects/{collect_id}.json")

    # -- loyalty (điểm + hạng thành viên) -----------------------------------

    def get_customer_membership(self, customer_id: int) -> dict:
        return self._request("GET", f"/loyalty/customers/{customer_id}/membership.json")

    def list_membership_histories(self, customer_id: int, params: dict | None = None) -> dict:
        return self._request(
            "GET",
            f"/loyalty/customers/{customer_id}/membership/histories.json",
            params=params or {},
        )

    def list_membership_levels(self, params: dict | None = None) -> dict:
        return self._request("GET", "/loyalty/membership-levels.json", params=params or {})

    def adjust_customer_points(self, customer_id: int, payload: dict) -> dict:
        """Cộng/trừ điểm cho khách. GHI dữ liệu loyalty thật — gọi có chủ đích."""
        return self._request("POST", f"/loyalty/customers/{customer_id}/points.json", json=payload)

    def adjust_membership_external(self, payload: dict) -> dict:
        return self._request("POST", "/loyalty/adjust-customer-membership-external.json", json=payload)

    # -- vận hành / tham chiếu ----------------------------------------------

    def health_check(self) -> dict:
        return self._request("GET", "/health-check")

    def list_locations(self, params: dict | None = None) -> dict:
        return self._request("GET", "/locations.json", params=params or {})

    def list_sale_channels(self, params: dict | None = None) -> dict:
        return self._request("GET", "/sale-channels.json", params=params or {})
