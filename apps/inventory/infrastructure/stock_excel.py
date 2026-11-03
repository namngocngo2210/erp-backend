from __future__ import annotations

# `export_stock_pivot` (dựng .xlsx đồng bộ) đã xoá ở Task 18 cùng endpoint
# `GET /api/inventory/stock/export` (`stock_views.export_stock`) — luồng async
# `apps.inventory.exports_definitions.StockExport.write` tự ghi trực tiếp qua
# writer streaming của engine, không gọi lại hàm này. Chỉ giữ `_slug`: vẫn
# được `exports_definitions.py` dùng để build tên file.


def _slug(value: str) -> str:
    """Mã kho vào tên file: bỏ mọi ký tự có thể phá header Content-Disposition."""
    return "".join(c for c in (value or "") if c.isalnum() or c in "-_") or "kho"
