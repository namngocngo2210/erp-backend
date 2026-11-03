"""DTO + Protocol của module cnv_loyalty.

Service tầng application chỉ phụ thuộc các interface ở đây — infrastructure
(HTTP thật) và test (FakeClient) cùng thỏa Protocol, không import lẫn nhau.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TokenBundle:
    """Kết quả đổi code / refresh token từ SSO id.cnv.vn."""

    access_token: str
    refresh_token: str = ""
    token_type: str = "Bearer"
    expires_in: int = 0
    scope: str = ""


class CnvApi(Protocol):
    """Bề mặt REST API CNV mà sync service cần (subset của CnvLoyaltyClient)."""

    def create_customer(self, payload: dict) -> dict: ...

    def create_product(self, payload: dict) -> dict: ...

    def update_product(self, product_id: int, payload: dict) -> dict: ...

    def create_custom_collection(self, payload: dict) -> dict: ...

    def create_collect(self, collection_id: int, product_id: int) -> dict: ...

    def create_order(self, payload: dict) -> dict: ...

    def update_order(self, order_id: int, payload: dict) -> dict: ...

    def get_customer_by_phone(self, phone: str) -> dict: ...


class CnvInboundApi(Protocol):
    """Bề mặt CNV mà đường KÉO ĐƠN VỀ cần — tách khỏi `CnvApi` có chủ đích.

    Hai chiều bật/tắt độc lập và test bằng fake khác nhau; gộp một Protocol thì
    fake của chiều này phải cài method của chiều kia chỉ để thỏa kiểu.
    """

    def list_orders(self, params: dict | None = None) -> dict: ...

    def get_order(self, order_id: int) -> dict: ...

    def get_order_by_external_id(self, external_id: str) -> dict: ...


class CnvWebhookApi(Protocol):
    """Bề mặt quản lý đăng ký webhook (chỉ lệnh vận hành dùng)."""

    def list_webhooks(self) -> dict: ...

    def register_webhook(
        self, callback_url: str, verify_token: str, topics: list[str]
    ) -> dict: ...

    def delete_webhook(self) -> dict: ...
