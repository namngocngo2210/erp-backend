"""Luật chọn SKU đẩy lên Pancake POS (docs/pancake-pos-product-sync.md §3.1).

Hàm thuần: mọi "sự kiện" (tồn, lần bán gần nhất, combo) đã được đọc sẵn vào
`CatalogProduct`; thời điểm hiện tại và cửa sổ bán hàng đi qua tham số.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from apps.pancake_pos.domain.entities import (
    SYNC_MODE_ALWAYS,
    SYNC_MODE_NEVER,
    CatalogProduct,
)

DEFAULT_SALES_WINDOW_DAYS = 180

# Mã có đuôi `-N` là lô nhập KiotViet cũ (đo prod 28/09/2026: không mã nào còn tồn/bán).
_LEGACY_SUFFIX_RE = re.compile(r"-\d+$")
_DELETED_MARKER = "{DEL}"

REASON_EMPTY_SKU = "empty_sku"
REASON_NEVER = "mode_never"
REASON_INACTIVE = "inactive"
REASON_DELETED_MARKER = "sku_del_marker"
REASON_LEGACY_SUFFIX = "sku_legacy_suffix"
REASON_COMBO = "combo"
REASON_COMPONENT = "component_excluded"
REASON_NO_ACTIVITY = "no_stock_no_recent_sale"


def ineligible_reason(
    product: CatalogProduct,
    *,
    now: datetime,
    sales_window_days: int = DEFAULT_SALES_WINDOW_DAYS,
    include_components: bool = True,
) -> str | None:
    """None = đủ điều kiện; ngược lại trả mã lý do (để báo cáo dry-run).

    Linh kiện (`product_type='component'`, two-way-sync §6b — thay luật loại linh kiện
    của !1508): mặc định VẪN đủ điều kiện (đẩy ẩn, danh mục "Linh kiện" — xem
    `mapping.build_group_payload`); `include_components=False`
    (PANCAKE_POS_INCLUDE_COMPONENTS) thì loại. Luật tồn/bán vẫn áp như hàng hoá.
    """
    sku = product.sku.strip()
    if not sku:
        return REASON_EMPTY_SKU  # không có khoá đối chiếu → không bao giờ đẩy, kể cả `always`
    if product.sync_mode == SYNC_MODE_NEVER:
        return REASON_NEVER
    if product.sync_mode == SYNC_MODE_ALWAYS:
        return None
    if not product.is_active:
        return REASON_INACTIVE
    if _DELETED_MARKER in sku.upper():
        return REASON_DELETED_MARKER
    if _LEGACY_SUFFIX_RE.search(sku):
        return REASON_LEGACY_SUFFIX
    if product.is_component and not include_components:
        return REASON_COMPONENT
    if product.is_combo or "+" in sku:
        return REASON_COMBO
    if product.has_stock:
        return None
    if product.last_sold_at is not None and product.last_sold_at >= now - timedelta(days=sales_window_days):
        return None
    return REASON_NO_ACTIVITY


def is_syncable(
    product: CatalogProduct,
    *,
    now: datetime,
    sales_window_days: int = DEFAULT_SALES_WINDOW_DAYS,
    include_components: bool = True,
) -> bool:
    return (
        ineligible_reason(
            product, now=now, sales_window_days=sales_window_days, include_components=include_components
        )
        is None
    )
