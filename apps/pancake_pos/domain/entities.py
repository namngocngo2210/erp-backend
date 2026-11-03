"""Kiểu dữ liệu thuần của đồng bộ Pancake POS — không phụ thuộc Django/HTTP.

`CatalogProduct` là ảnh chụp một SKU CRM kèm các "sự kiện" cần cho luật chọn
(tồn, bán gần đây, combo); `PancakeVariation`/`PancakeProduct` là phần tối thiểu
đọc về từ Pancake để đối chiếu (docs/pancake-pos-product-sync.md §3.3).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

SYNC_MODE_AUTO = "auto"
SYNC_MODE_ALWAYS = "always"
SYNC_MODE_NEVER = "never"

# `products.product_type` (MR !1504): None = cột chưa có trên DB (chưa merge/deploy).
PRODUCT_TYPE_GOODS = "goods"
PRODUCT_TYPE_COMPONENT = "component"


@dataclass(frozen=True)
class CatalogProduct:
    id: int
    sku: str
    name: str
    price: Decimal = Decimal(0)
    weight: Decimal = Decimal(0)
    length: Decimal = Decimal(0)
    width: Decimal = Decimal(0)
    height: Decimal = Decimal(0)
    is_active: bool = True
    group_code: str | None = None
    is_combo: bool = False
    has_stock: bool = False
    last_sold_at: datetime | None = None
    sync_mode: str = SYNC_MODE_AUTO
    product_type: str | None = None

    @property
    def is_component(self) -> bool:
        return self.product_type == PRODUCT_TYPE_COMPONENT
    # Tên ngắn người dùng đặt (products.short_name) — ưu tiên hơn `name` khi đặt tên
    # trên Pancake vì `name` là tên hoá đơn dài, bị import KiotViet ghi đè.
    short_name: str = ""

    @property
    def display_name(self) -> str:
        """Tên dùng cho Pancake: `short_name` nếu có, không thì `name`."""
        return (self.short_name or "").strip() or (self.name or "").strip()


@dataclass(frozen=True)
class SyncGroup:
    """Một đơn vị đồng bộ = một sản phẩm Pancake (nhóm CRM hoặc SKU lẻ)."""

    key: str
    custom_id: str
    products: tuple[CatalogProduct, ...]
    is_grouped: bool


@dataclass(frozen=True)
class PancakeVariation:
    """`raw` = các trường Pancake trả về mà PUT cần gửi lại NGUYÊN TRẠNG cho mẫu mã
    không thuộc CRM (§3.3 — PUT luôn gửi đủ mẫu mã hiện có). Không so sánh/hash."""

    id: str
    product_id: str
    custom_id: str = ""
    barcode: str = ""
    product_custom_id: str = ""
    is_hidden: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)
    # Tồn THỰC theo kho Pancake (`variations_warehouses[].actual_remain_quantity`) —
    # chỉ dùng cho so sánh tồn đầu kỳ, không bao giờ gửi lại khi PUT sản phẩm.
    warehouse_quantities: Mapping[str, int] = field(default_factory=dict, compare=False, hash=False)


@dataclass(frozen=True)
class PancakeWarehouse:
    id: str
    name: str = ""


@dataclass(frozen=True)
class CrmWarehouse:
    id: int
    code: str
    name: str
    is_active: bool = True


@dataclass(frozen=True)
class CrmStockRow:
    """Tồn Hàng hoá (`stock_items.state='sellable'`) của một SKU ở một kho CRM."""

    warehouse_id: int
    product_id: int
    sku: str
    quantity: int


@dataclass(frozen=True)
class LinkedVariation:
    product_id: int
    sku: str
    pancake_variation_id: str


@dataclass(frozen=True)
class PancakeProduct:
    """`raw` = trường cấp sản phẩm (name, custom_id, weight, product_attributes) để
    PUT chỉ-ẩn khi nhóm không còn SKU nào đủ điều kiện."""

    id: str
    custom_id: str = ""
    variations: tuple[PancakeVariation, ...] = field(default_factory=tuple)
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)


@dataclass(frozen=True)
class PancakeOrder:
    """Phần tối thiểu của một đơn Pancake mà luồng đẩy đơn cần (id, trạng thái, mã
    tham chiếu CRM `custom_id`, kho)."""

    id: str
    status: int | None = None
    custom_id: str = ""
    warehouse_id: str = ""
