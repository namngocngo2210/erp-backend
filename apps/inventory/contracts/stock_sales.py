"""Public stock-sales contract for other modules (e.g. orders). Import from here,
not from inventory internals."""

from apps.inventory.application.services.stock_sales_service import (  # noqa: F401
    SaleLine,
    StockSalesService,
    StockWarning,
)
