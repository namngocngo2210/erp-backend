"""Luật giảm giá tự động theo dòng hàng.

Dòng mua đúng 2 sản phẩm luôn được giảm 200k cho cả dòng. `OrderItem.discount` là
tiền giảm cho MỖI sản phẩm (line_total = (unit_price − discount) × quantity) nên
mức lưu là 100k. FE (`order-form-helpers.ts`, `applyTwoUnitLineDiscount`) dùng
cùng hằng số — đổi một bên phải đổi bên kia.
"""

from dataclasses import dataclass
from decimal import Decimal

TWO_UNIT_LINE_QUANTITY = 2
TWO_UNIT_LINE_UNIT_DISCOUNT = Decimal("100000")


def auto_line_unit_discount(quantity: int, unit_price: Decimal) -> Decimal:
    """Tiền giảm mỗi sản phẩm tự áp cho một dòng; kẹp ≤ đơn giá để thành tiền không âm."""
    if quantity != TWO_UNIT_LINE_QUANTITY:
        return Decimal("0")
    return min(TWO_UNIT_LINE_UNIT_DISCOUNT, max(unit_price, Decimal("0")))


@dataclass(frozen=True)
class PricedLine:
    quantity: int
    unit_price: Decimal
    discount: Decimal

    @property
    def line_total(self) -> Decimal:
        return (self.unit_price - self.discount) * self.quantity


def price_line(quantity: int, unit_price: Decimal, *, auto_discount: bool) -> PricedLine:
    """Định giá một dòng; `auto_discount=False` khi bên gửi đã chốt tiền."""
    discount = auto_line_unit_discount(quantity, unit_price) if auto_discount else Decimal("0")
    return PricedLine(quantity=quantity, unit_price=unit_price, discount=discount)
