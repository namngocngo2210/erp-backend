"""Luật thuần của đẩy đơn CRM → Pancake POS (docs/pancake-pos-two-way-sync.md §4.1, §6b, §7 A2).

Không phụ thuộc Django/HTTP: service nạp dữ liệu (SQL) thành các dataclass dưới rồi
hỏi module này 4 câu:
1. `evaluate` — đơn có đủ điều kiện đẩy không (bỏ qua / chặn / trùng / đủ).
2. `choose_warehouse` — kho nào đủ hàng theo thứ tự ưu tiên (không kho nào → cần
   người chọn cách xuất).
3. `validate_parts` + `split_lines` — người điều phối chọn một kho hoặc tách kiện.
4. `build_payload` — body `POST/PUT /shops/{id}/orders`.

Số đo prod 30/09/2026 (§8) quyết định các luật:
- "Đã chốt" = `OrderAssessment.sales_status_code` ∈ {closed, closed_express,
  created_order} (KHÔNG gồm `closed_scheduled_date` — hẹn ngày, chưa được xuất) VÀ
  `Order.status` ∈ {0 mới, 1 chờ xử lý, 11 chờ giao}. `Order.status` do KiotViet/ĐVVC
  đẩy về nên đơn chốt chưa xuất luôn ở 0; bản thân `OrderStatus` không có mốc "chốt".
- KHÔNG dùng `invoice_code` làm tín hiệu "đã xuất": KiotViet vẫn xuất hoá đơn song song
  với Pancake (100% đơn `created_order` có `invoice_code` ngay khi chốt). Chống đẩy dồn
  đơn cũ lúc bật cờ bằng mốc `PANCAKE_POS_PUSH_ORDERS_SINCE` (`pushed_after`) — chỉ đơn
  chốt SAU mốc; trạng thái "đã xuất/đang xử lý" lấy từ Pancake (GET order), không từ CRM.
- Chống trùng landing ↔ Foxia: KHÔNG dựa vào việc có cơ hội Foxia (`FoxiaLeadLink`) —
  CRM đang đẩy MỌI đơn landing/mess/zalo/hotline sang Foxia làm cơ hội (2.352 cơ hội,
  98% trong 5 phút), và 1.084/1.682 đơn landing có cơ hội vẫn do sale CRM chốt, xuất
  KiotViet. Chỉ bỏ đơn landing khi cơ hội đã thành ĐƠN Foxia (mirror) còn sống.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

# ── hằng trạng thái CRM (khớp apps.orders.models.OrderStatus / crm_catalog) ──────
CRM_STATUS_NEW = 0
CRM_STATUS_PENDING_PROCESSING = 1
CRM_STATUS_CANCELLED = 6
CRM_STATUS_AWAITING_SHIPMENT = 11
PUSHABLE_CRM_STATUSES = frozenset({CRM_STATUS_NEW, CRM_STATUS_PENDING_PROCESSING, CRM_STATUS_AWAITING_SHIPMENT})

CLOSED_SALES_STATUSES = frozenset({"closed", "closed_express", "created_order"})
EXPRESS_SALES_STATUS = "closed_express"
CANCELLED_SALES_STATUS = "cancelled"

FOXIA_SOURCE = "foxia"
DEFAULT_PUSH_SOURCES = ("landing", "messenger", "zalo", "hotline", FOXIA_SOURCE)

# Trạng thái đơn Pancake (spec Open API) CRM dùng trực tiếp.
PANCAKE_STATUS_CANCELLED = 6
PANCAKE_STATUS_DELETED = 7

MAX_PARTS = 10


# ── kiểu dữ liệu ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class OrderLine:
    product_id: int
    sku: str
    name: str
    quantity: int
    unit_price: Decimal = Decimal(0)
    # Giảm trên MỖI sản phẩm (cùng nghĩa `OrderItem.discount` và `discount_each_product`).
    discount: Decimal = Decimal(0)
    variation_id: str = ""  # "" = SKU chưa ghép mẫu mã Pancake

    @property
    def net_total(self) -> Decimal:
        return (self.unit_price - self.discount) * self.quantity


@dataclass(frozen=True)
class CrmOrder:
    id: int
    order_source: str
    status: int
    sales_status_code: str = ""
    is_deleted: bool = False
    # Mốc hoạt động gần nhất: max(chốt, sửa đánh giá sale) cho đơn CRM; mốc tạo cho đơn Foxia mirror.
    closed_at: datetime | None = None
    # Mốc CHỐT (không tính lần sửa đánh giá sau đó): `sales_closed_at` cho đơn CRM, mốc tạo
    # cho đơn Foxia mirror. So với `PANCAKE_POS_PUSH_ORDERS_SINCE`.
    first_closed_at: datetime | None = None
    name: str = ""
    phone: str = ""
    address: str = ""
    channel_name: str = ""
    discount_amount: Decimal = Decimal(0)  # giảm cấp đơn + voucher
    total_after_discount: Decimal = Decimal(0)
    customer_prepaid: Decimal = Decimal(0)
    lines: tuple[OrderLine, ...] = ()
    # Landing→Foxia: đơn Foxia (mirror, còn sống) sinh từ cơ hội của đơn này.
    foxia_order_ids: tuple[int, ...] = ()
    # Đơn Foxia: đơn CRM gốc đã đẩy cơ hội sinh ra đơn này.
    origin_order_ids: tuple[int, ...] = ()

    @property
    def is_cancelled(self) -> bool:
        return (
            self.is_deleted
            or self.status == CRM_STATUS_CANCELLED
            or self.sales_status_code == CANCELLED_SALES_STATUS
        )


class Verdict:
    IGNORE = "ignore"  # không phải việc của luồng đẩy (không tạo liên kết)
    BLOCKED = "blocked"  # đủ điều kiện nghiệp vụ nhưng thiếu dữ liệu → liên kết `error`
    DUPLICATE = "duplicate"  # luật chống giao trùng → `skipped_duplicate`
    ELIGIBLE = "eligible"


@dataclass(frozen=True)
class Eligibility:
    verdict: str
    reason: str = ""
    detail: str = ""
    alert: bool = False  # cần cảnh báo Lark (trùng landing ↔ Foxia)


def evaluate(
    order: CrmOrder,
    *,
    sources: Iterable[str],
    closed_since: datetime | None = None,
    pushed_after: datetime | None = None,
    pushed_order_ids: Iterable[int] = (),
    allow_duplicate: bool = False,
) -> Eligibility:
    """Đơn CHƯA có liên kết có được đẩy không. `pushed_order_ids` = các đơn liên quan
    (Foxia ↔ gốc) đang có liên kết đẩy còn hiệu lực. `allow_duplicate` = người điều phối
    đã kiểm tra và bấm "Vẫn đẩy" cho đơn bị luật chống trùng chặn. `pushed_after` = mốc
    bắt đầu đẩy đơn (`PANCAKE_POS_PUSH_ORDERS_SINCE`): đơn chốt trước mốc do KiotViet/kho
    cũ xử lý nên bỏ qua."""
    source = (order.order_source or "").strip().lower()
    if order.is_deleted:
        return Eligibility(Verdict.IGNORE, "deleted")
    if source not in {s.strip().lower() for s in sources}:
        return Eligibility(Verdict.IGNORE, "source_not_enabled")
    if order.is_cancelled:
        return Eligibility(Verdict.IGNORE, "cancelled")
    if order.status not in PUSHABLE_CRM_STATUSES:
        return Eligibility(Verdict.IGNORE, "status_not_pending")
    # Đơn Foxia mirror không có đánh giá sale: đã kéo về = đã chốt bên Foxia.
    if source != FOXIA_SOURCE and order.sales_status_code not in CLOSED_SALES_STATUSES:
        return Eligibility(Verdict.IGNORE, "not_closed")
    if pushed_after is not None:
        first_closed = order.first_closed_at or order.closed_at
        if first_closed is None or first_closed < pushed_after:
            return Eligibility(Verdict.IGNORE, "before_push_since")
    if closed_since is not None and (order.closed_at is None or order.closed_at < closed_since):
        return Eligibility(Verdict.IGNORE, "too_old")

    pushed = set(pushed_order_ids)
    if allow_duplicate:
        pass
    elif source == FOXIA_SOURCE:
        origin_pushed = sorted(set(order.origin_order_ids) & pushed)
        if origin_pushed:
            return Eligibility(
                Verdict.DUPLICATE,
                "origin_pushed",
                f"Đơn CRM gốc #{', #'.join(map(str, origin_pushed))} đã đẩy sang Pancake",
                alert=True,
            )
    elif order.foxia_order_ids:
        return Eligibility(
            Verdict.DUPLICATE,
            "foxia_order_exists",
            f"Cơ hội Foxia đã thành đơn Foxia #{', #'.join(map(str, sorted(order.foxia_order_ids)))}",
            alert=True,
        )

    if not order.lines:
        return Eligibility(Verdict.BLOCKED, "no_items", "Đơn chưa có dòng hàng CRM")
    missing = sorted({line.sku or str(line.product_id) for line in order.lines if not line.variation_id})
    if missing:
        return Eligibility(
            Verdict.BLOCKED, "missing_variation", f"SKU chưa ghép mẫu mã Pancake: {', '.join(missing)}"
        )
    return Eligibility(Verdict.ELIGIBLE)


# ── chọn kho ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class WarehouseOption:
    warehouse_id: int
    pancake_warehouse_id: str
    priority: int = 0
    is_default: bool = False
    name: str = ""


def ordered_options(options: Iterable[WarehouseOption]) -> list[WarehouseOption]:
    """Thứ tự thử kho: `priority` nhỏ trước; cùng ưu tiên thì kho mặc định trước."""
    return sorted(options, key=lambda o: (o.priority, not o.is_default, o.warehouse_id))


def demand_of(
    lines: Iterable[OrderLine] | Iterable[tuple[int, int]],
    components: Mapping[int, Sequence[tuple[int, int]]] | None = None,
) -> dict[int, int]:
    """Nhu cầu tồn theo sản phẩm. Combo nở ra mã lẻ (`components[parent] =
    [(component_id, qty)]`) — CRM trừ tồn combo vào mã lẻ (combo-stock-expansion)."""
    components = components or {}
    out: dict[int, int] = {}
    for line in lines:
        product_id, quantity = (line.product_id, line.quantity) if isinstance(line, OrderLine) else line
        parts = components.get(product_id)
        if parts:
            for component_id, per_unit in parts:
                out[component_id] = out.get(component_id, 0) + per_unit * quantity
        else:
            out[product_id] = out.get(product_id, 0) + quantity
    return out


def warehouse_has(demand: Mapping[int, int], stock: Mapping[tuple[int, int], int], warehouse_id: int) -> bool:
    return all(stock.get((warehouse_id, pid), 0) >= qty for pid, qty in demand.items() if qty > 0)


def choose_warehouse(
    demand: Mapping[int, int],
    options: Iterable[WarehouseOption],
    stock: Mapping[tuple[int, int], int],
) -> WarehouseOption | None:
    """Kho ĐẦU TIÊN (theo `ordered_options`) có tồn Hàng hoá đủ cho mọi dòng."""
    if not demand:
        return None
    for option in ordered_options(options):
        if warehouse_has(demand, stock, option.warehouse_id):
            return option
    return None


# ── chọn cách xuất (một kho / tách kiện) ─────────────────────────────────────
class RoutingError(ValueError):
    """Phương án xuất người điều phối gửi không hợp lệ."""


@dataclass(frozen=True)
class PartPlan:
    warehouse_id: int
    items: tuple[tuple[int, int], ...] = field(default_factory=tuple)  # (product_id, quantity); rỗng = cả đơn


def order_quantities(lines: Iterable[OrderLine]) -> dict[int, int]:
    out: dict[int, int] = {}
    for line in lines:
        out[line.product_id] = out.get(line.product_id, 0) + line.quantity
    return out


def validate_parts(lines: Sequence[OrderLine], parts: Sequence[PartPlan], linked_warehouse_ids: Iterable[int]) -> None:
    """Một kho (1 part, items rỗng) hoặc tách: mỗi part một kho đã ghép, SL mỗi sản
    phẩm cộng các part phải ĐÚNG bằng SL trên đơn, không part rỗng."""
    linked = set(linked_warehouse_ids)
    if not parts:
        raise RoutingError("Chưa chọn kho xuất.")
    if len(parts) > MAX_PARTS:
        raise RoutingError(f"Tối đa {MAX_PARTS} kiện.")
    for part in parts:
        if part.warehouse_id not in linked:
            raise RoutingError(f"Kho #{part.warehouse_id} chưa ghép kho Pancake.")
    if len(parts) == 1 and not parts[0].items:
        return
    if any(not part.items for part in parts):
        raise RoutingError("Tách kiện: mỗi kiện phải có ít nhất một dòng hàng.")
    expected = order_quantities(lines)
    got: dict[int, int] = {}
    for part in parts:
        seen: set[int] = set()
        for product_id, quantity in part.items:
            if product_id not in expected:
                raise RoutingError(f"Sản phẩm #{product_id} không có trong đơn.")
            if quantity <= 0:
                raise RoutingError("Số lượng mỗi dòng phải > 0.")
            if product_id in seen:
                raise RoutingError(f"Sản phẩm #{product_id} lặp trong cùng một kiện.")
            seen.add(product_id)
            got[product_id] = got.get(product_id, 0) + quantity
    if got != expected:
        raise RoutingError("Tổng số lượng các kiện phải đúng bằng số lượng trên đơn.")


def split_lines(lines: Sequence[OrderLine], parts: Sequence[Sequence[tuple[int, int]]]) -> list[tuple[OrderLine, ...]]:
    """Chia dòng đơn cho từng kiện. Một sản phẩm có thể nằm ở nhiều dòng (vd dòng tặng
    giá 0): lấy lần lượt theo thứ tự dòng, xuyên suốt các kiện — giá từng dòng giữ nguyên."""
    remaining: list[list[Any]] = [[line, line.quantity] for line in lines]
    out: list[tuple[OrderLine, ...]] = []
    for items in parts:
        chunk: list[OrderLine] = []
        for product_id, quantity in items:
            need = quantity
            for slot in remaining:
                line, left = slot
                if need <= 0:
                    break
                if line.product_id != product_id or left <= 0:
                    continue
                take = min(left, need)
                slot[1] = left - take
                need -= take
                chunk.append(replace(line, quantity=take))
            if need > 0:
                raise RoutingError(f"Sản phẩm #{product_id} vượt số lượng trên đơn.")
        out.append(tuple(chunk))
    return out


def allocate(total: int, weights: Sequence[Decimal | int]) -> list[int]:
    """Chia `total` (đồng) theo tỷ trọng, phần dư dồn kiện cuối → tổng luôn khớp."""
    if not weights:
        return []
    weight_sum = sum(Decimal(w) for w in weights)
    if weight_sum <= 0:
        shares = [0] * len(weights)
        shares[-1] = total
        return shares
    shares = [
        int((Decimal(total) * Decimal(w) / weight_sum).to_integral_value(rounding=ROUND_HALF_UP))
        for w in weights[:-1]
    ]
    shares.append(total - sum(shares))
    return shares


# ── payload ──────────────────────────────────────────────────────────────────
def custom_id_for(order_id: int, part_no: int = 1, part_count: int = 1) -> str:
    """Mã đơn trên Pancake = `custom_id` = id đơn CRM (tách kiện: `<id>-<kiện>`), cũng là mã
    tra lại khi mất response. Pancake dùng `custom_id` làm mã đơn hiển thị."""
    return str(order_id) if part_count <= 1 and part_no == 1 else f"{order_id}-{part_no}"


def parse_source_map(raw: str) -> dict[str, tuple[str, str]]:
    """`landing=-7:123,messenger=-1` → {nguồn CRM: (order_sources, account)}. Dòng hỏng bỏ qua."""
    out: dict[str, tuple[str, str]] = {}
    for chunk in (raw or "").replace(";", ",").split(","):
        key, sep, value = chunk.partition("=")
        key, value = key.strip().lower(), value.strip()
        if not sep or not key or not value:
            continue
        source_id, _, account = value.partition(":")
        if source_id.strip():
            out[key] = (source_id.strip(), account.strip())
    return out


def _int(value: Decimal | int) -> int:
    return int(Decimal(value).to_integral_value(rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class PartMoney:
    total_discount: int
    cod: int
    prepaid: int


def part_money(order: CrmOrder, part_lines: Sequence[Sequence[OrderLine]]) -> list[PartMoney]:
    """Tiền từng kiện: giảm cấp đơn, trả trước và COD chia theo giá trị dòng hàng.
    COD = tổng sau giảm − trả trước (như đẩy KiotViet); phí ship KHÔNG thu khách
    (`Order.shipping_fee` là phí ĐVVC, không nằm trong `total_after_discount`)."""
    cod_total = max(0, _int(order.total_after_discount) - _int(order.customer_prepaid))
    weights = [sum((line.net_total for line in lines), Decimal(0)) for lines in part_lines]
    discounts = allocate(max(0, _int(order.discount_amount)), weights)
    prepaid = allocate(max(0, _int(order.customer_prepaid)), weights)
    cods = allocate(cod_total, weights)
    return [PartMoney(d, c, p) for d, c, p in zip(discounts, cods, prepaid, strict=True)]


def build_payload(
    order: CrmOrder,
    lines: Sequence[OrderLine],
    *,
    pancake_warehouse_id: str,
    money: PartMoney,
    part_no: int = 1,
    part_count: int = 1,
    source_ref: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """Body `POST/PUT /shops/{id}/orders`. SĐT gửi ĐẦY ĐỦ (dữ liệu vận hành, chỉ đi qua
    API). Địa chỉ gửi dạng chữ (`address`, không `province_id`) — Pancake tự tách
    tỉnh/huyện/xã (spec ShippingAddress.address); CRM không có id địa giới của Pancake."""
    address = (order.address or "").strip()
    note = [f"CRM #{order.id}", order.order_source or ""]
    if order.channel_name:
        note.append(order.channel_name)
    if part_count > 1:
        note.append(f"kiện {part_no}/{part_count}")
    if order.sales_status_code == EXPRESS_SALES_STATUS:
        note.append("HỎA TỐC")
    payload: dict[str, Any] = {
        "bill_full_name": order.name,
        "bill_phone_number": order.phone,
        "shipping_address": {
            "full_name": order.name,
            "phone_number": order.phone,
            "address": address,
            "full_address": address,
        },
        "warehouse_id": pancake_warehouse_id,
        "items": [
            {
                "variation_id": line.variation_id,
                "quantity": line.quantity,
                "discount_each_product": _int(line.discount),
                "is_discount_percent": False,
                "is_bonus_product": line.unit_price <= 0,
                "variation_info": {"retail_price": _int(line.unit_price)},
            }
            for line in lines
        ],
        "shipping_fee": 0,
        "total_discount": money.total_discount,
        "cod": money.cod,
        "received_at_shop": False,
        "custom_id": custom_id_for(order.id, part_no, part_count),
        "note": " · ".join(n for n in note if n),
    }
    if money.prepaid > 0:
        payload["transfer_money"] = money.prepaid
    if source_ref:
        source_id, account = source_ref
        payload["order_sources"] = source_id
        if account:
            payload["account"] = int(account) if account.lstrip("-").isdigit() else account
    return payload


# ── sửa / huỷ đơn đã đẩy ─────────────────────────────────────────────────────
class ChangeAction:
    NONE = "none"
    UPDATE = "update"
    CANCEL = "cancel"
    LOCKED = "locked"  # Pancake đã xử lý kho → chỉ cảnh báo
    GONE = "gone"  # đã huỷ/xoá trên Pancake


def plan_change(
    *,
    crm_cancelled: bool,
    payload_changed: bool,
    pancake_status: int | None,
    locked_statuses: Iterable[int],
) -> str:
    if not crm_cancelled and not payload_changed:
        return ChangeAction.NONE
    if pancake_status in (PANCAKE_STATUS_CANCELLED, PANCAKE_STATUS_DELETED):
        return ChangeAction.GONE
    if pancake_status is not None and pancake_status in set(locked_statuses):
        return ChangeAction.LOCKED
    return ChangeAction.CANCEL if crm_cancelled else ChangeAction.UPDATE
