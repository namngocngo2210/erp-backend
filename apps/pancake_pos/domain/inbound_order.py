"""Luật thuần cho webhook Pancake → CRM (two-way-sync §5.1, §6b, §7 B1).

Không import Django: phân loại payload, đọc đơn Pancake thành snapshot, mã tra cứu
(mã đơn sàn, mã tham chiếu CRM), mốc gửi/giao/hoàn, gộp trạng thái đơn tách kiện.

Phát hiện từ API thật (GET orders, 30/09/2026, 300 đơn gần nhất):
- Đơn sàn: `id` Pancake CHÍNH LÀ mã đơn sàn (Shopee 14 ký tự `260930BY9FBX6H`, TikTok 18
  chữ số); `order_sources`/`marketplace_id` = "-3" Shopee, "-9" TikTok ("-1" Facebook).
  KiotViet lưu cùng mã với tiền tố: `invoice_code` HDSPE_/HDTTS_, `order_code` DHSPE_/DHTTS_.
- `account` = id shop/nguồn con, `account_name` = tên shop (trùng tên kênh CRM phần lớn).
- Mã vận đơn: `partner.extend_code` (rỗng tới khi ĐVVC nhận đơn), ĐVVC: `partner.partner_name`.
- Thời gian dạng `2026-09-29T20:06:41.410614` KHÔNG kèm múi giờ = UTC.
- Trạng thái vận chuyển (MR D): xem `domain.delivery_status` — Pancake không ghi
  `orders.status`, chỉ `partner_*` + mã "Trạng thái vận chuyển thực".
- Dòng hàng đơn sàn hiện `variation_id = null` (sàn chưa ghép SKU trên Pancake) → tạo đơn
  CRM phải tra SKU `variation_info.display_id`, không có thì bỏ dòng (ghi lý do).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from apps.pancake_pos.domain.delivery_status import DvvcStatus, dvvc_status

EVENT_ORDERS = "orders"
EVENT_INVENTORY = "variations_warehouses"
EVENT_PRODUCTS = "products"
EVENT_CUSTOMERS = "customers"
EVENT_UNKNOWN = "unknown"

# Trạng thái đơn Pancake (spec `WebhookOrderResponse.status`).
PANCAKE_SHIPPED = 2
PANCAKE_DELIVERED = 3
PANCAKE_RETURNING = 4
PANCAKE_RETURNED = 5
PANCAKE_CANCELLED = 6
PANCAKE_DELETED = 7
PANCAKE_PARTIAL_RETURN = 15
PANCAKE_COLLECTED = 16
DEAD_STATUSES = frozenset({PANCAKE_CANCELLED, PANCAKE_DELETED})
_SHIPPED_ON = frozenset({PANCAKE_SHIPPED})
_DELIVERED_ON = frozenset({PANCAKE_DELIVERED, PANCAKE_COLLECTED})
_RETURNED_ON = frozenset({PANCAKE_RETURNED, PANCAKE_PARTIAL_RETURN})
# Trạng thái chứng tỏ đơn ĐÃ rời kho (dùng suy mốc gửi khi thiếu lịch sử).
_AFTER_SHIPPED = frozenset({PANCAKE_SHIPPED, PANCAKE_DELIVERED, PANCAKE_COLLECTED, PANCAKE_RETURNING,
                            PANCAKE_RETURNED, PANCAKE_PARTIAL_RETURN})

# Mã sàn Pancake → tiền tố mã KiotViet của đơn sàn (orders.INVOICE_CODE_SOURCE_PREFIXES).
MARKETPLACE_PREFIXES: dict[str, tuple[str, str]] = {
    "-3": ("HDSPE_", "DHSPE_"),
    "-9": ("HDTTS_", "DHTTS_"),
}

# Tiền tố `CRM-` là định dạng cũ (trước 06/10/2026) — vẫn nhận để khớp đơn đẩy trước đó.
_CRM_REF_RE = re.compile(r"^(?:CRM-)?(\d{1,10})(?:-(\d+))?$")

# Tên ĐVVC Pancake → mã `orders.shipping_provider` CRM đang dùng (BEST/GHN/SPX/J&T…).
_PROVIDER_CODES = {
    "giao hàng nhanh": "GHN", "ghn": "GHN",
    "best inc": "BEST", "best express": "BEST", "best": "BEST",
    "shopee xpress": "SPX", "spx": "SPX",
    "j&t": "J&T", "j&t express": "J&T",
    "viettel post": "VTP", "vtp": "VTP",
    "giao hàng tiết kiệm": "GHTK", "ghtk": "GHTK",
}

def payload_hash(payload: Any) -> str:
    """Hash ổn định (sort_keys) — khoá chống trùng khi Pancake gửi lại y hệt."""
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_dt(value: Any) -> datetime | None:
    """ISO Pancake (không múi giờ = UTC). Sai định dạng → None."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ClassifiedEvent:
    event_type: str
    record_id: str
    version: datetime | None
    record: dict[str, Any]


def _looks_like_order(obj: dict[str, Any]) -> bool:
    return bool(_str(obj.get("id"))) and "status" in obj and any(
        k in obj for k in ("items", "partner", "bill_full_name", "order_sources", "shipping_address")
    )


def classify_payload(payload: Any) -> ClassifiedEvent:
    """Loại sự kiện theo hình dạng payload — Pancake không gửi header loại.

    - `orders`: đơn phẳng ở gốc (`id`, `status`, `items`…) hoặc bọc `{"data": {...}}`.
    - `variations_warehouses`: `data.record` có `variation_id` + `warehouse_id`.
    - `products`: `data.record` là sản phẩm (`variations`/`name`).
    - `customers`: có `phone_numbers`/`customer_id` mà không có `status`.
    """
    if not isinstance(payload, dict):
        return ClassifiedEvent(EVENT_UNKNOWN, "", None, {})
    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    record = data.get("record") if data and isinstance(data.get("record"), dict) else None
    if record is not None:
        if record.get("variation_id") and record.get("warehouse_id"):
            rid = f"{_str(record['variation_id'])}:{_str(record['warehouse_id'])}"
            return ClassifiedEvent(EVENT_INVENTORY, rid, parse_dt(record.get("inserted_at")), record)
        if _looks_like_order(record):
            return ClassifiedEvent(EVENT_ORDERS, _str(record["id"]), parse_dt(record.get("updated_at")), record)
        if "variations" in record or "name" in record:
            return ClassifiedEvent(EVENT_PRODUCTS, _str(record.get("id")), parse_dt(record.get("updated_at")), record)
    for candidate in (payload, data):
        if isinstance(candidate, dict) and _looks_like_order(candidate):
            return ClassifiedEvent(
                EVENT_ORDERS, _str(candidate["id"]), parse_dt(candidate.get("updated_at")), candidate
            )
    customer = record or data or payload
    if isinstance(customer, dict) and ("phone_numbers" in customer or "customer_id" in customer):
        return ClassifiedEvent(EVENT_CUSTOMERS, _str(customer.get("id")), parse_dt(customer.get("updated_at")), customer)
    return ClassifiedEvent(EVENT_UNKNOWN, "", None, payload)


@dataclass(frozen=True)
class InboundItem:
    variation_id: str
    sku: str
    name: str
    quantity: int
    unit_price: int
    discount_each: int


@dataclass(frozen=True)
class PancakeOrderSnapshot:
    id: str
    status: int | None
    updated_at: datetime | None
    inserted_at: datetime | None
    order_sources: str
    source_name: str
    account: str
    account_name: str
    page_id: str
    marketplace_id: str
    custom_id: str
    tracking_code: str
    partner_name: str
    customer_name: str
    phone: str
    address: str
    total_price: int | None
    total_discount: int
    total_after_discount: int | None
    items: tuple[InboundItem, ...]
    status_history: tuple[tuple[int, datetime], ...]
    time_send_partner: datetime | None
    status_name: str = ""
    dvvc: DvvcStatus = DvvcStatus("", None)
    warehouse_id: str = ""
    warehouse_name: str = ""

    @property
    def status_changed_at(self) -> datetime | None:
        """Lúc vào trạng thái hiện tại = mục lịch sử MỚI nhất có đúng trạng thái đó."""
        return next(
            (at for status, at in reversed(self.status_history) if status == self.status), None
        ) or self.updated_at

    @property
    def is_dead(self) -> bool:
        return self.status in DEAD_STATUSES


def _items(raw: Any) -> tuple[InboundItem, ...]:
    out = []
    for item in raw if isinstance(raw, list) else ():
        if not isinstance(item, dict):
            continue
        info = item.get("variation_info") if isinstance(item.get("variation_info"), dict) else {}
        out.append(InboundItem(
            variation_id=_str(item.get("variation_id")),
            sku=_str(info.get("display_id")),
            name=_str(info.get("name")),
            quantity=max(_int(item.get("quantity")) or 0, 0),
            unit_price=max(_int(info.get("retail_price")) or 0, 0),
            discount_each=max(_int(item.get("discount_each_product")) or 0, 0),
        ))
    return tuple(out)


def _history(raw: Any) -> tuple[tuple[int, datetime], ...]:
    out = []
    for row in raw if isinstance(raw, list) else ():
        if not isinstance(row, dict):
            continue
        status, at = _int(row.get("status")), parse_dt(row.get("updated_at"))
        if status is not None and at is not None:
            out.append((status, at))
    return tuple(sorted(out, key=lambda x: x[1]))


def parse_order_snapshot(record: dict[str, Any]) -> PancakeOrderSnapshot | None:
    order_id = _str(record.get("id"))
    if not order_id:
        return None
    partner = record.get("partner") if isinstance(record.get("partner"), dict) else {}
    address = record.get("shipping_address") if isinstance(record.get("shipping_address"), dict) else {}
    page = record.get("page") if isinstance(record.get("page"), dict) else {}
    warehouse = record.get("warehouse_info") if isinstance(record.get("warehouse_info"), dict) else {}
    return PancakeOrderSnapshot(
        id=order_id,
        status=_int(record.get("status")),
        updated_at=parse_dt(record.get("updated_at")),
        inserted_at=parse_dt(record.get("inserted_at")),
        order_sources=_str(record.get("order_sources")),
        source_name=_str(record.get("order_sources_name")),
        account=_str(record.get("account")),
        account_name=_str(record.get("account_name")) or _str(page.get("name")),
        page_id=_str(record.get("page_id")),
        marketplace_id=_str(record.get("marketplace_id")),
        custom_id=_str(record.get("custom_id")),
        tracking_code=_str(partner.get("extend_code")),
        partner_name=_str(partner.get("partner_name")),
        customer_name=_str(record.get("bill_full_name")) or _str(address.get("full_name")),
        phone=_str(record.get("bill_phone_number")) or _str(address.get("phone_number")),
        address=_str(address.get("full_address")) or _str(address.get("address")),
        total_price=_int(record.get("total_price")),
        total_discount=max(_int(record.get("total_discount")) or 0, 0),
        total_after_discount=_int(record.get("total_price_after_sub_discount")),
        items=_items(record.get("items")),
        status_history=_history(record.get("status_history")),
        time_send_partner=parse_dt(record.get("time_send_partner")),
        status_name=_str(record.get("status_name")),
        dvvc=dvvc_status(partner),
        warehouse_id=_str(record.get("warehouse_id")),
        warehouse_name=_str(warehouse.get("name")),
    )


def is_stale(snapshot_at: datetime | None, applied_at: datetime | None) -> bool:
    """Bản đang xét cũ hơn bản đã áp → bỏ qua. Bằng nhau vẫn áp (idempotent)."""
    return snapshot_at is not None and applied_at is not None and snapshot_at < applied_at


def marketplace_codes(snapshot: PancakeOrderSnapshot) -> tuple[str, ...]:
    """Mã KiotViet ứng với đơn sàn (`HDSPE_<id>`, `DHSPE_<id>`…). Không phải đơn sàn → ()."""
    market = snapshot.marketplace_id or snapshot.order_sources
    prefixes = MARKETPLACE_PREFIXES.get(market)
    if not prefixes:
        return ()
    return tuple(f"{prefix}{snapshot.id}" for prefix in prefixes)


def crm_ref(custom_id: str) -> tuple[int, int] | None:
    """`<order>[-<part>]` (A2 gửi làm `custom_id`, cũ có tiền tố `CRM-`) → (order_id, part_no).
    Chỉ là ứng viên — người gọi phải đối chiếu với liên kết đẩy (`has_part`)."""
    match = _CRM_REF_RE.match(custom_id or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2) or 1)


@dataclass(frozen=True)
class Milestones:
    shipped_at: datetime | None
    delivered_at: datetime | None
    returned_at: datetime | None


def milestones(snapshot: PancakeOrderSnapshot) -> Milestones:
    """Mốc gửi/giao/hoàn = lần ĐẦU đơn vào trạng thái tương ứng trong `status_history`.
    Thiếu lịch sử mà trạng thái hiện tại đã qua mốc → `time_send_partner` / `updated_at`."""

    def first(codes: frozenset[int]) -> datetime | None:
        return next((at for status, at in snapshot.status_history if status in codes), None)

    shipped = first(_SHIPPED_ON)
    if shipped is None and snapshot.status in _AFTER_SHIPPED:
        shipped = snapshot.time_send_partner or snapshot.updated_at
    delivered = first(_DELIVERED_ON)
    if delivered is None and snapshot.status in _DELIVERED_ON:
        delivered = snapshot.updated_at
    returned = first(_RETURNED_ON)
    if returned is None and snapshot.status in _RETURNED_ON:
        returned = snapshot.updated_at
    return Milestones(shipped, delivered, returned)


def provider_code(partner_name: str) -> str:
    name = (partner_name or "").strip()
    return _PROVIDER_CODES.get(name.lower(), name)[:50]


def parse_source_map(text: str) -> dict[str, str]:
    """`-3=shopee,-9=tiktok,-1=messenger` → {order_sources Pancake: order_source CRM}."""
    out: dict[str, str] = {}
    for part in (text or "").split(","):
        key, sep, value = part.partition("=")
        if sep and key.strip() and value.strip():
            out[key.strip()] = value.strip().lower()
    return out


def parse_channel_map(text: str) -> dict[str, int]:
    """`<account hoặc page_id>=<channel_id CRM>` cách nhau dấu phẩy; dòng sai bị bỏ."""
    out: dict[str, int] = {}
    for part in (text or "").split(","):
        key, sep, value = part.partition("=")
        if sep and key.strip() and value.strip().isdigit():
            out[key.strip()] = int(value.strip())
    return out


# ── tạo đơn CRM từ đơn gốc Pancake (cờ PANCAKE_POS_INBOUND_CREATE_ORDERS) ───────
@dataclass(frozen=True)
class ResolvedProduct:
    product_id: int
    sku: str
    name: str


@dataclass(frozen=True)
class NewCrmLine:
    product_id: int
    sku: str
    name: str
    quantity: int
    unit_price: int
    discount: int  # tiền giảm MỖI sản phẩm (nghĩa `order_items.discount`)


@dataclass(frozen=True)
class NewCrmOrder:
    pancake_order_id: str
    channel_id: int
    order_source: str
    status: int
    customer_name: str
    phone: str
    address: str
    lines: tuple[NewCrmLine, ...]
    missing_items: tuple[str, ...]
    product_text: str
    sku_text: str
    quantity: int
    total_amount: int
    discount_amount: int
    total_after_discount: int
    shipping_code: str
    shipping_provider: str


def item_key(item: InboundItem) -> str:
    return f"var:{item.variation_id}" if item.variation_id else f"sku:{item.sku.upper()}"


def build_new_order(
    snapshot: PancakeOrderSnapshot, *, channel_id: int, order_source: str, status: int,
    products: dict[str, ResolvedProduct],
) -> NewCrmOrder:
    """Đơn CRM dựng từ snapshot Pancake. `products` tra theo `item_key` (variation đã ghép
    trước, SKU sau). Dòng không tra được sản phẩm CRM → `missing_items` (đơn vẫn tạo, tiền
    theo Pancake). Dòng trùng sản phẩm gộp SL (UNIQUE (order, product) của order_items)."""
    merged: dict[int, NewCrmLine] = {}
    missing: list[str] = []
    for item in snapshot.items:
        if item.quantity <= 0:
            continue
        product = products.get(item_key(item))
        if product is None:
            missing.append(f"{item.sku or item.variation_id or '?'} {item.name}".strip())
            continue
        discount = min(item.discount_each, item.unit_price)
        prev = merged.get(product.product_id)
        if prev is None:
            merged[product.product_id] = NewCrmLine(
                product.product_id, product.sku, product.name or item.name, item.quantity, item.unit_price, discount
            )
        else:
            merged[product.product_id] = NewCrmLine(
                prev.product_id, prev.sku, prev.name, prev.quantity + item.quantity, prev.unit_price, prev.discount
            )
    gross = sum(i.unit_price * i.quantity for i in snapshot.items)
    total = snapshot.total_price if snapshot.total_price is not None else gross
    discount = sum(min(i.discount_each, i.unit_price) * i.quantity for i in snapshot.items) + snapshot.total_discount
    after = snapshot.total_after_discount if snapshot.total_after_discount is not None else total - discount
    names = [f"{i.name or i.sku} x{i.quantity}" for i in snapshot.items if i.quantity > 0]
    return NewCrmOrder(
        pancake_order_id=snapshot.id,
        channel_id=channel_id,
        order_source=order_source,
        status=status,
        customer_name=snapshot.customer_name,
        phone=snapshot.phone,
        address=snapshot.address,
        lines=tuple(merged.values()),
        missing_items=tuple(missing),
        product_text=", ".join(names),
        sku_text=", ".join(i.sku for i in snapshot.items if i.sku),
        quantity=sum(i.quantity for i in snapshot.items),
        total_amount=max(total, 0),
        discount_amount=max(min(discount, max(total, 0)), 0),
        total_after_discount=max(after, 0),
        shipping_code=snapshot.tracking_code,
        shipping_provider=provider_code(snapshot.partner_name),
    )
