"""Nở dòng hàng của đơn theo SKU combo ra MÃ LẺ — cho MỌI UI gợi ý dòng nhập hoàn.

Từ 2026-08-20 bán 1 combo TRỪ TỒN theo mã lẻ (`expand_desired_quantities` chạy bên
trong `issue_for_order`), nên hạn mức phiếu hoàn (`_guard_double_credit`, đọc
`outstanding_by_key`) cũng tính theo mã lẻ. Màn nào gợi ý dòng hoàn bằng SKU combo
thì thủ kho chọn ĐÚNG thứ UI đưa ra vẫn bị chặn "đơn nguồn chỉ còn 0 đơn vị chưa
được cộng lại tồn".

Đặt ở `catalog` chứ không ở `inventory` là CÓ CHỦ Ý: hai màn cần luật này nằm ở hai
tầng khác nhau — `inventory.flow_pending_views` (panel khớp tay) và
`orders.shipping_lookup_view` (quét trả hàng loạt + phiếu kho thủ công). `catalog`
nằm DƯỚI cả hai trong contract phân tầng nên cả hai import được hợp lệ; để helper ở
`inventory` thì `orders` phải thêm một dòng nợ vào KHỐI B của `.importlinter` (khối
CHỈ ĐƯỢC GIẢM). Đây cũng chính là lý do lỗi 07/09 xảy ra: luật sống ở hai bản sao
song song, vá một bên thì bên kia lệch (xem docs/combo-stock-expansion-2026-08-20.md
§8).

Hai luật lấy nguyên từ đường ghi sổ, đổi ở đây phải đổi cả `expand_desired_quantities`:

- Combo CHƯA khai thành phần giữ nguyên, vẫn tính vào chính SKU combo. Trên prod
  phần lớn SKU dạng combo chưa khai — bỏ qua chúng sẽ đánh mất dòng hàng.
- Combo chứa mã A cộng dồn với dòng A bán lẻ trong cùng đơn thành MỘT dòng. Bút toán
  đã gộp nên `outstanding_by_key` chỉ có một key, mà `build_lines` còn chặn thẳng hai
  dòng trùng `(product_id, return_type)`.
"""
from __future__ import annotations

from apps.catalog.models import ProductComponent


def components_by_parent(product_ids) -> dict[int, list[dict]]:
    """``{parent_id: [{product_id, sku, name, quantity}]}`` cho các SKU đã khai.

    SKU không phải combo (hoặc combo chưa khai thành phần) KHÔNG có khoá ở đây —
    caller dựa vào chính điều đó để biết có phải nở hay không.
    """
    product_ids = {pid for pid in (product_ids or ()) if pid}
    if not product_ids:
        return {}
    out: dict[int, list[dict]] = {}
    for row in (
        ProductComponent.objects.filter(parent_id__in=product_ids)
        .values("parent_id", "component_id", "component__sku", "component__name", "quantity")
        .order_by("component__sku")
    ):
        out.setdefault(row["parent_id"], []).append(
            {
                "product_id": row["component_id"],
                "sku": row["component__sku"] or "",
                "name": row["component__name"] or "",
                "quantity": row["quantity"],
            }
        )
    return out


def expand_order_lines(rows: list[dict], *, with_name: bool = True) -> list[dict]:
    """Nở + gộp dòng hàng của MỘT đơn. Đúng 1 query cho cả danh sách.

    ``rows`` là ``{product_id, sku, quantity}`` (thêm ``name`` nếu ``with_name``).
    Dòng KHÔNG khớp sản phẩm (``product_id`` rỗng) đi thẳng qua, không gộp: không có
    gì để đối chiếu và gộp chúng lại với nhau là trộn hai mặt hàng khác nhau.
    """
    components = components_by_parent(r.get("product_id") for r in rows)
    return _expand_with(rows, components, with_name=with_name)


def expand_order_lines_bulk(
    rows_by_order: dict[int, list[dict]], *, with_name: bool = True
) -> dict[int, list[dict]]:
    """Như `expand_order_lines` nhưng cho NHIỀU đơn trong đúng 1 query — dùng cho
    màn tra cứu trả về cả trang kết quả, tránh N+1."""
    components = components_by_parent(
        r.get("product_id") for rows in rows_by_order.values() for r in rows
    )
    return {
        order_id: _expand_with(rows, components, with_name=with_name)
        for order_id, rows in rows_by_order.items()
    }


def _expand_with(
    rows: list[dict], components: dict[int, list[dict]], *, with_name: bool
) -> list[dict]:
    merged: dict[int, dict] = {}
    out: list[dict] = []
    for row in rows:
        comps = components.get(row.get("product_id"))
        if comps:
            expanded = [
                {
                    "product_id": c["product_id"],
                    "sku": c["sku"],
                    **({"name": c["name"]} if with_name else {}),
                    "quantity": row["quantity"] * c["quantity"],
                }
                for c in comps
            ]
        else:
            expanded = [dict(row)]
        for line in expanded:
            product_id = line.get("product_id")
            if not product_id:
                out.append(line)
                continue
            existing = merged.get(product_id)
            if existing is None:
                merged[product_id] = line
                out.append(line)
            else:
                existing["quantity"] += line["quantity"]
    return out


__all__ = ["components_by_parent", "expand_order_lines", "expand_order_lines_bulk"]
