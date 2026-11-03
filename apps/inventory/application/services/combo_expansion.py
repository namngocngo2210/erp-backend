"""Nở SKU combo ra sản phẩm con cho file Excel Lịch sử nhập xuất.

MỘT chỗ duy nhất dùng chung cho cả tab Xuất và tab Nhập hoàn: màn này từng có
hai nhánh code song song (màn hình và job export nền) khiến sửa một bên là lệch
số — helper chung là cách chặn việc đó tái diễn.

Việc nở theo khai báo HIỆN HÀNH trong ``product_components`` (bảng không có
phiên bản — sửa thành phần hôm nay thì file của kỳ trước cũng đổi số).

Từ 2026-08-20 module còn phục vụ ĐƯỜNG GHI SỔ qua ``expand_desired_quantities``:
bán combo trừ tồn vào mã lẻ giống KiotViet, thay vì trừ vào chính SKU combo như
trước (xem docs/combo-stock-expansion-2026-08-20.md). Lớp ``ComboExpander`` bên
dưới vẫn thuần tầng báo cáo.
"""
from __future__ import annotations

from apps.catalog.models import Product, ProductComponent


class ComboExpander:
    """Giữ sẵn tên sản phẩm + bảng thành phần cho MỘT trang dữ liệu."""

    __slots__ = ("_names", "_components")

    def __init__(self, names: dict[int, str], components: dict[int, list[dict]]) -> None:
        self._names = names
        self._components = components

    @staticmethod
    def for_items(items: list[dict]) -> ComboExpander:
        """Nạp dữ liệu cho mọi `product_id` xuất hiện trong trang — đúng 2 query."""
        product_ids = {
            line.get("product_id")
            for item in (items or [])
            for line in (item.get("lines") or [])
            if line.get("product_id")
        }
        return ComboExpander.for_product_ids(product_ids)

    @staticmethod
    def for_product_ids(product_ids) -> ComboExpander:
        product_ids = set(product_ids or ())
        components: dict[int, list[dict]] = {}
        if product_ids:
            for r in (
                ProductComponent.objects.filter(parent_id__in=product_ids)
                .values("parent_id", "component_id", "component__sku", "component__name", "quantity")
                .order_by("component__sku")
            ):
                components.setdefault(r["parent_id"], []).append(
                    {
                        "product_id": r["component_id"],
                        "sku": r["component__sku"] or "",
                        "name": r["component__name"] or "",
                        "quantity": r["quantity"],
                    }
                )
        # Tên sản phẩm cho CẢ sản phẩm gốc lẫn sản phẩm con (dòng nở lấy tên con).
        names_needed = set(product_ids)
        names: dict[int, str] = {}
        if names_needed:
            names = {
                r["id"]: r["name"] or ""
                for r in Product.objects.filter(id__in=names_needed).values("id", "name")
            }
        return ComboExpander(names, components)

    def is_combo(self, product_id) -> bool:
        return bool(self._components.get(product_id))

    def components_of(self, product_id) -> list[dict]:
        return self._components.get(product_id) or []

    def expand(self, lines: list[dict] | None) -> list[dict]:
        """Nở một danh sách `lines` thành các dòng phẳng đã nhân số lượng.

        SKU thường giữ nguyên một dòng, ``combo_sku`` rỗng. ``gross`` (SL đã
        TỪNG rời kho, chỉ tab Xuất có) mặc định bằng ``qty`` cho các luồng không
        khai field này.
        """
        out: list[dict] = []
        for line in lines or []:
            product_id = line.get("product_id")
            qty = line.get("qty") or 0
            gross = line.get("gross")
            gross = qty if gross is None else gross
            comps = self._components.get(product_id)
            if not comps:
                out.append(
                    {
                        "sku": line.get("sku") or "",
                        "name": self._names.get(product_id, ""),
                        "combo_sku": "",
                        "qty": qty,
                        "gross": gross,
                    }
                )
                continue
            combo_sku = line.get("sku") or ""
            for c in comps:
                out.append(
                    {
                        "sku": c["sku"],
                        "name": c["name"],
                        "combo_sku": combo_sku,
                        "qty": qty * c["quantity"],
                        "gross": gross * c["quantity"],
                    }
                )
        return out

    def merge_sku_rows(self, rows: list[dict], qty_keys: tuple[str, ...]) -> list[dict]:
        """Gộp dòng-theo-SKU sau khi nở combo (dùng cho chế độ Theo SKU).

        Mỗi dòng combo tan ra thành các dòng sản phẩm con với mọi cột SL trong
        ``qty_keys`` được nhân theo công thức, rồi cộng dồn vào dòng của chính SP
        con nếu SKU đó cũng bán lẻ trong kỳ. ``from_combo`` giữ phần đến từ combo.

        ``count`` (Số đơn / Số phiếu) cộng dồn THÔ: một đơn vừa mua lẻ vừa mua
        combo chứa SKU đó sẽ bị đếm hai lần. Trường hợp hiếm và cột này chỉ để
        tham khảo — SL mới là con số nghiệp vụ dùng.
        """
        merged: dict[str, dict] = {}

        def _slot(sku: str, name: str) -> dict:
            row = merged.get(sku)
            if row is None:
                row = {"sku": sku, "name": name, "count": 0, "from_combo": 0}
                row.update(dict.fromkeys(qty_keys, 0))
                merged[sku] = row
            return row

        for r in rows:
            product_id = r.get("product_id")
            comps = self._components.get(product_id)
            if not comps:
                row = _slot(r.get("sku") or "", r.get("name") or "")
                row["count"] += r.get("count") or 0
                for k in qty_keys:
                    row[k] += r.get(k) or 0
                continue
            for c in comps:
                row = _slot(c["sku"], c["name"])
                row["count"] += r.get("count") or 0
                for k in qty_keys:
                    value = (r.get(k) or 0) * c["quantity"]
                    row[k] += value
                row["from_combo"] += (r.get(qty_keys[-1]) or 0) * c["quantity"]
        return sorted(merged.values(), key=lambda x: x["sku"])


def expand_desired_quantities(desired: dict[int, int]) -> dict[int, int]:
    """Nở số lượng theo SKU combo thành số lượng theo MÃ LẺ, để GHI SỔ tồn kho.

    Khác phần còn lại của module (chỉ nở ở tầng xuất báo cáo), hàm này nằm trong
    đường ghi ``StockMovement``: bán 1 combo thì trừ tồn các sản phẩm con đúng
    theo công thức, giống hệt cách KiotViet trừ tồn.

    Nhận/trả ``{product_id: quantity}`` thuần thay vì ``SaleLine`` để không tạo
    import vòng với ``stock_sales_service`` (nơi định nghĩa ``SaleLine``).

    Hai luật quan trọng:

    - SKU combo CHƯA khai thành phần giữ nguyên, vẫn trừ vào chính nó. Trên prod
      118/121 SKU dạng combo chưa khai — im lặng bỏ qua chúng sẽ đánh mất bút toán
      xuất kho, tệ hơn hẳn việc tồn của SKU đó âm như trước.
    - Combo chứa mã A cộng dồn với dòng A bán lẻ trong cùng đơn thành MỘT bút toán,
      nhờ vậy ``outstanding_by_key`` (khoá theo ``(warehouse, product)``) không bị
      tách đôi và các luồng hoàn/huỷ vẫn đối chiếu được.
    """
    if not desired:
        return {}
    components: dict[int, list[tuple[int, int]]] = {}
    for row in ProductComponent.objects.filter(parent_id__in=desired.keys()).values(
        "parent_id", "component_id", "quantity"
    ):
        components.setdefault(row["parent_id"], []).append(
            (row["component_id"], row["quantity"])
        )
    if not components:
        return dict(desired)

    expanded: dict[int, int] = {}
    for product_id, quantity in desired.items():
        comps = components.get(product_id)
        if not comps:
            expanded[product_id] = expanded.get(product_id, 0) + quantity
            continue
        for component_id, per_combo in comps:
            expanded[component_id] = expanded.get(component_id, 0) + quantity * per_combo
    return expanded


def all_combo_components() -> dict[int, list[tuple[int, int]]]:
    """Toàn bộ bảng công thức combo: ``{parent_id: [(component_id, quantity)]}``.

    Dùng cho backfill (quét cả sổ, không theo một trang dữ liệu). Đặt ở đây thay
    vì trong `combo_stock_backfill` để giữ MỘT cửa duy nhất đọc `ProductComponent`
    từ phía inventory — module này đã có quyền chạm `apps.orders.models` trong
    `.importlinter`, không tạo thêm nợ phân tầng.
    """
    components: dict[int, list[tuple[int, int]]] = {}
    for row in ProductComponent.objects.values("parent_id", "component_id", "quantity"):
        components.setdefault(row["parent_id"], []).append(
            (row["component_id"], row["quantity"])
        )
    return components
