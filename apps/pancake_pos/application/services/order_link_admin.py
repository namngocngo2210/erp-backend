"""Thao tác của người điều phối trên liên kết đơn (two-way-sync §6b, A2): xem ngữ cảnh
"Cần chọn cách xuất", chọn một kho / tách kiện, thử lại. View gọi module này rồi tự
xếp task `push_orders_for` cho đơn."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from apps.pancake_pos.domain import order_push as rules
from apps.pancake_pos.infrastructure.order_reader import SqlOrderReader
from apps.pancake_pos.models import OrderLinkStatus, PancakePosOrderLink
from apps.pancake_pos.repositories import OrderLinkConflict, OrderLinkRepository, WarehouseLinkRepository

__all__ = ["OrderLinkConflict", "OrderNotFound", "retry_link", "route_order", "routing_context"]


class OrderNotFound(Exception):
    pass


def routing_context(order_id: int) -> dict[str, Any]:
    """Dòng hàng của đơn + tồn Hàng hoá CRM ở từng kho đã ghép (combo nở mã lẻ)."""
    reader = SqlOrderReader()
    order = reader.load_orders([order_id]).get(order_id)
    if order is None:
        raise OrderNotFound(order_id)
    links = WarehouseLinkRepository.all_links()
    names = reader.warehouse_names()
    product_ids = sorted({line.product_id for line in order.lines})
    components = reader.components(product_ids)
    needed = set(product_ids) | {cid for parts in components.values() for cid, _ in parts}
    stock = reader.sellable_stock(warehouse_ids=[link.warehouse_id for link in links], product_ids=sorted(needed))
    demand = rules.demand_of(order.lines, components)
    options = rules.ordered_options(
        rules.WarehouseOption(link.warehouse_id, link.pancake_warehouse_id, link.priority, link.is_default_for_push)
        for link in links
    )
    quantities = rules.order_quantities(order.lines)
    first_line = {}
    for line in order.lines:
        first_line.setdefault(line.product_id, line)
    return {
        "order_id": order.id,
        "name": order.name,
        "order_source": order.order_source,
        "lines": [
            {"product_id": pid, "sku": first_line[pid].sku, "name": first_line[pid].name, "quantity": qty}
            for pid, qty in quantities.items()
        ],
        "warehouses": [
            {
                "warehouse_id": option.warehouse_id,
                "name": names.get(option.warehouse_id, ""),
                "pancake_warehouse_id": option.pancake_warehouse_id,
                "enough": rules.warehouse_has(demand, stock, option.warehouse_id),
                # Tồn theo sản phẩm của đơn; combo = số bộ lắp được từ mã lẻ.
                "stock": {
                    str(pid): _available(pid, components, stock, option.warehouse_id) for pid in quantities
                },
            }
            for option in options
        ],
        "links": [
            {"id": link.id, "part_no": link.part_no, "status": link.status}
            for link in OrderLinkRepository.for_orders([order_id]).get(order_id, [])
        ],
    }


def _available(product_id: int, components: dict[int, list[tuple[int, int]]],
               stock: dict[tuple[int, int], int], warehouse_id: int) -> int:
    parts = components.get(product_id)
    if not parts:
        return max(0, stock.get((warehouse_id, product_id), 0))
    return max(0, min(stock.get((warehouse_id, cid), 0) // max(per, 1) for cid, per in parts))


def route_order(order_id: int, parts: Sequence[rules.PartPlan]) -> list[int]:
    """Người điều phối chọn cách xuất: 1 kho (1 part, không items) hoặc N kiện."""
    reader = SqlOrderReader()
    order = reader.load_orders([order_id]).get(order_id)
    if order is None:
        raise OrderNotFound(order_id)
    links = {link.warehouse_id: link for link in WarehouseLinkRepository.all_links()}
    rules.validate_parts(order.lines, parts, links.keys())
    whole = len(parts) == 1 and not parts[0].items
    rows = [
        {
            "warehouse_id": part.warehouse_id,
            "pancake_warehouse_id": links[part.warehouse_id].pancake_warehouse_id,
            "items": None if whole else [{"product_id": p, "quantity": q} for p, q in part.items],
        }
        for part in parts
    ]
    return OrderLinkRepository.replace_routing(order_id, rows)


def retry_link(link_id: int, *, force: bool = False) -> int:
    """Thử lại một liên kết; trả order_id để view xếp task.

    `force` (chỉ cho `skipped_duplicate`): "Vẫn đẩy" — người điều phối đã kiểm tra không
    giao trùng; view xếp task với `force=True` để lượt đánh giá lại bỏ luật chống trùng.

    - Đã có đơn Pancake (sửa/huỷ lỗi): đối chiếu lại từ đầu (checked_at NULL).
    - Đã chọn kho, chưa có đơn Pancake (lỗi đẩy): về `pending`, đếm lại lượt tự thử.
    - Còn lại (cần chọn cách xuất / trùng / thiếu dữ liệu): xoá để đánh giá lại."""
    link = PancakePosOrderLink.objects.filter(id=link_id).values(
        "id", "order_id", "status", "pancake_order_id", "pancake_warehouse_id"
    ).first()
    if link is None:
        raise OrderNotFound(link_id)
    if force and link["status"] != OrderLinkStatus.SKIPPED_DUPLICATE:
        raise OrderLinkConflict("Chỉ đơn bị bỏ qua vì trùng mới dùng được \"Vẫn đẩy\".")
    if link["pancake_order_id"]:
        OrderLinkRepository.update(link_id, checked_at=None, payload_hash="")
    elif link["status"] == OrderLinkStatus.PUSHING:
        raise OrderLinkConflict("Đơn đang được đẩy — đợi lượt hiện tại xong.")
    elif link["status"] in (OrderLinkStatus.ERROR, OrderLinkStatus.PENDING) and link["pancake_warehouse_id"]:
        OrderLinkRepository.update(link_id, status=OrderLinkStatus.PENDING, attempts=0)
    elif link["status"] in (
        OrderLinkStatus.ERROR, OrderLinkStatus.NEEDS_ROUTING, OrderLinkStatus.SKIPPED_DUPLICATE,
        OrderLinkStatus.CANCELLED,
    ):
        OrderLinkRepository.reset_for_reevaluation(link["order_id"])
    else:
        raise OrderLinkConflict("Liên kết không ở trạng thái thử lại được.")
    return link["order_id"]
