"""Kế hoạch đẩy tồn đầu kỳ CRM → Pancake (docs/pancake-pos-two-way-sync.md §7 A1).

Hàm thuần: nhận ghép kho, tồn Hàng hoá CRM, SKU đã liên kết và tồn thực đang có trên
Pancake; trả từng dòng (kho, SKU) với số CRM, số sẽ đặt, số Pancake và dòng nào cần ghi.

Luật:
- Chỉ SKU đã có liên kết mẫu mã (có id Pancake). SKU có tồn ≠ 0 mà chưa liên kết →
  `missing_links` (bỏ qua, liệt kê).
- Vũ trụ dòng = mọi SKU đã liên kết × mọi kho đã ghép: SKU không có dòng tồn CRM ở
  kho đó = 0 (đặt tuyệt đối → Pancake phải về 0 nếu đang lệch).
- Tồn CRM âm → đặt 0, liệt kê ở `negatives`.
- Cần ghi khi số đặt ≠ số Pancake, hoặc chưa biết số Pancake (mẫu mã không có trong
  danh sách đọc về). Chạy lại sau khi ghi xong → 0 dòng cần ghi (idempotent).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from apps.pancake_pos.domain.entities import CrmStockRow, LinkedVariation


@dataclass(frozen=True)
class WarehousePair:
    warehouse_id: int
    warehouse_name: str
    pancake_warehouse_id: str
    pancake_warehouse_name: str = ""


@dataclass(frozen=True)
class StockLine:
    warehouse_id: int
    warehouse_name: str
    pancake_warehouse_id: str
    product_id: int
    sku: str
    variation_id: str
    crm_qty: int
    pancake_qty: int | None  # None = không đọc được số trên Pancake

    @property
    def push_qty(self) -> int:
        return max(self.crm_qty, 0)

    @property
    def needs_write(self) -> bool:
        return self.pancake_qty is None or self.pancake_qty != self.push_qty

    @property
    def delta(self) -> int | None:
        """Số đặt − số Pancake (None khi chưa biết số Pancake)."""
        return None if self.pancake_qty is None else self.push_qty - self.pancake_qty

    def as_entry(self) -> dict[str, object]:
        """Một phần tử `variations_warehouses` của `POST variations/update_quantity`."""
        return {
            "variation_id": self.variation_id,
            "warehouse_id": self.pancake_warehouse_id,
            "remain_quantity": self.push_qty,
        }

    def as_json(self) -> dict[str, object]:
        return {
            "sku": self.sku,
            "product_id": self.product_id,
            "variation_id": self.variation_id,
            "warehouse_id": self.warehouse_id,
            "warehouse_name": self.warehouse_name,
            "crm_qty": self.crm_qty,
            "push_qty": self.push_qty,
            "pancake_qty": self.pancake_qty,
            "delta": self.delta,
        }


@dataclass(frozen=True)
class MissingLink:
    product_id: int
    sku: str
    quantities: dict[str, int]  # tên kho CRM → tồn

    def as_json(self) -> dict[str, object]:
        return {
            "product_id": self.product_id,
            "sku": self.sku,
            "quantity": sum(self.quantities.values()),
            "warehouses": dict(sorted(self.quantities.items())),
        }


@dataclass
class InitialStockPlan:
    lines: list[StockLine] = field(default_factory=list)
    missing_links: list[MissingLink] = field(default_factory=list)

    @property
    def to_write(self) -> list[StockLine]:
        return [line for line in self.lines if line.needs_write]

    @property
    def negatives(self) -> list[StockLine]:
        return [line for line in self.lines if line.crm_qty < 0]

    @property
    def unknown_on_pancake(self) -> int:
        return sum(1 for line in self.lines if line.pancake_qty is None)


def plan_initial_stock(
    *,
    warehouses: Sequence[WarehousePair],
    stock: Iterable[CrmStockRow],
    linked: Iterable[LinkedVariation],
    pancake_quantities: Mapping[str, Mapping[str, int]],
) -> InitialStockPlan:
    """`pancake_quantities` = {variation_id: {pancake_warehouse_id: tồn thực}} cho các
    mẫu mã ĐỌC ĐƯỢC từ Pancake; mẫu mã vắng mặt → số Pancake chưa biết (None).
    Mẫu mã có mặt nhưng không có dòng kho → 0 ở kho đó."""
    by_wh = {w.warehouse_id: w for w in warehouses}
    crm: dict[tuple[int, int], int] = {}
    stock_skus: dict[int, str] = {}
    for row in stock:
        if row.warehouse_id not in by_wh:
            continue
        crm[(row.warehouse_id, row.product_id)] = crm.get((row.warehouse_id, row.product_id), 0) + row.quantity
        stock_skus[row.product_id] = row.sku

    links = {v.product_id: v for v in linked if v.pancake_variation_id}
    plan = InitialStockPlan()
    for variation in sorted(links.values(), key=lambda v: (v.sku.upper(), v.product_id)):
        on_pancake = pancake_quantities.get(variation.pancake_variation_id)
        for pair in warehouses:
            pancake_qty = None if on_pancake is None else int(on_pancake.get(pair.pancake_warehouse_id, 0))
            plan.lines.append(StockLine(
                warehouse_id=pair.warehouse_id,
                warehouse_name=pair.warehouse_name,
                pancake_warehouse_id=pair.pancake_warehouse_id,
                product_id=variation.product_id,
                sku=variation.sku,
                variation_id=variation.pancake_variation_id,
                crm_qty=crm.get((pair.warehouse_id, variation.product_id), 0),
                pancake_qty=pancake_qty,
            ))

    missing: dict[int, dict[str, int]] = {}
    for (warehouse_id, product_id), qty in crm.items():
        if product_id in links or qty == 0:
            continue
        name = by_wh[warehouse_id].warehouse_name
        missing.setdefault(product_id, {})[name] = qty
    plan.missing_links = [
        MissingLink(product_id=pid, sku=stock_skus.get(pid, ""), quantities=qtys)
        for pid, qtys in sorted(missing.items(), key=lambda item: (stock_skus.get(item[0], "").upper(), item[0]))
    ]
    return plan


def batches(items: Sequence[StockLine], size: int) -> list[list[StockLine]]:
    size = max(int(size), 1)
    return [list(items[i : i + size]) for i in range(0, len(items), size)]
