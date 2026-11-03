"""Cổng (Protocol) mà `ProductSyncService` phụ thuộc — thay được bằng fake trong test.

- `PancakePosGateway`: Open API Pancake POS (impl: `infrastructure.pancake_pos_client`).
- `CatalogReader`: đọc SKU CRM kèm sự kiện cho luật chọn (impl:
  `infrastructure.catalog_reader.SqlCatalogReader`).
- `WarehouseReader` / `StockReader`: kho CRM và tồn Hàng hoá theo (kho, SKU) cho
  ghép kho + đẩy tồn đầu kỳ (impl: `infrastructure.stock_reader`).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Any, Protocol

from apps.pancake_pos.domain.entities import (
    CatalogProduct,
    CrmStockRow,
    CrmWarehouse,
    LinkedVariation,
    PancakeOrder,
    PancakeProduct,
    PancakeVariation,
    PancakeWarehouse,
)


class PancakePosGateway(Protocol):
    def list_shops(self) -> list[dict[str, Any]]: ...

    def iter_variations(self, *, search: str | None = None) -> Iterator[PancakeVariation]: ...

    def get_product(self, sku_or_id: str) -> PancakeProduct | None: ...

    def create_product(self, payload: dict[str, Any]) -> None: ...

    def update_product(self, product_id: str, payload: dict[str, Any]) -> None: ...

    def list_categories(self) -> list[dict[str, str]]: ...

    def create_category(self, name: str, *, parent_id: str | None = None) -> str: ...

    def list_warehouses(self) -> list[PancakeWarehouse]: ...

    def update_quantities(self, entries: list[dict[str, Any]], *, actual: bool = True) -> list[dict[str, str]]:
        """Trả các dòng lỗi `[{variation_id, warehouse_id, message}]` (rỗng = thành công hết)."""
        ...


class PancakeOrderGateway(Protocol):
    """Phần đơn hàng của Open API (đẩy đơn CRM → Pancake, two-way-sync A2)."""

    def create_order(self, payload: dict[str, Any]) -> PancakeOrder: ...

    def get_order(self, order_id: str) -> PancakeOrder | None: ...

    def update_order(self, order_id: str, payload: dict[str, Any]) -> PancakeOrder | None: ...

    def find_orders(self, *, custom_id: str, search: str, since: int | None = None) -> list[PancakeOrder]: ...


class CatalogReader(Protocol):
    def list_candidates(
        self,
        *,
        sold_since: datetime,
        skus: Sequence[str] | None = None,
        product_ids: Sequence[int] | None = None,
    ) -> list[CatalogProduct]:
        """Sản phẩm chưa xoá mềm. `skus`/`product_ids` lọc — luôn kéo CẢ nhóm của
        phần tử khớp (kể cả khi phần tử đó đã xoá mềm)."""
        ...

    def has_product_type(self) -> bool:
        """DB đã có cột `products.product_type` (MR !1504) chưa."""
        ...


class WarehouseReader(Protocol):
    def list_warehouses(self) -> list[CrmWarehouse]:
        """Kho CRM chưa xoá mềm."""
        ...


class StockReader(Protocol):
    def sellable_stock(
        self, *, warehouse_ids: Sequence[int], skus: Sequence[str] | None = None
    ) -> list[CrmStockRow]:
        """Tồn `state='sellable'` ≠ 0 theo (kho, sản phẩm) trong các kho cho trước."""
        ...

    def linked_variations(self, *, skus: Sequence[str] | None = None) -> list[LinkedVariation]:
        """SKU CRM đã liên kết một mẫu mã Pancake (có id)."""
        ...
