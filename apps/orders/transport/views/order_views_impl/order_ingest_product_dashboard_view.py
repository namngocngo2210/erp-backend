"""Re-export shim — file gốc đã tách 3 module (audit 2026-07 mục 4.5).

Giữ để các import/URL cũ không vỡ. Import mới hãy dùng thẳng:

- :mod:`.order_ingest_views` — landing + lark ingest
- :mod:`.product_catalog_views` — CRUD products/categories/groups
- :mod:`.dashboard_report_views` — dashboard_summary + ads_report_summary
"""

from .dashboard_report_views import ads_report_summary, dashboard_summary
from .order_ingest_views import ingest_landing_order, lark_ingest
from .product_catalog_views import (
    list_create_product_categories,
    list_create_product_groups,
    list_create_products,
    products_catalog_grouped,
    products_grouped,
    update_delete_product,
    update_delete_product_category,
    update_delete_product_group,
)

__all__ = [
    "ads_report_summary",
    "dashboard_summary",
    "ingest_landing_order",
    "lark_ingest",
    "list_create_product_categories",
    "list_create_product_groups",
    "list_create_products",
    "products_catalog_grouped",
    "products_grouped",
    "update_delete_product",
    "update_delete_product_category",
    "update_delete_product_group",
]
