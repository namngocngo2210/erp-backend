"""Đọc/ghi đơn CRM cho webhook Pancake → CRM (two-way-sync §5.1, §6b, §7 B1).

Đọc = SQL thẳng (mẫu `order_reader`), mỗi hàm một câu theo index sẵn có:
- mã đơn sàn: `order_invoice_code_idx` / `order_order_code_idx` (btree partial `> ''`),
- mã vận đơn: `ord_ship_code_btree_idx` (partial `shipping_code > ''`).
Điều kiện `> ''` viết tường minh để planner dùng được index partial với `= ANY(...)`.

Ghi: Pancake KHÔNG ghi `orders.status` (quyết định 01/10 — trạng thái đơn do KiotViet).
Chỉ ghi `partner_delivery_status` / `partner_dvvc_delivery_status` (+ điền mã vận đơn
khi trống) bằng UPDATE có điều kiện. Mã "Trạng thái vận chuyển thực" đi qua service
đơn hàng ở `crm_delivery_gateway` (journey + luồng xuất kho).

Tạo đơn: `bulk_create` (không `save()`) theo ràng buộc ở
`apps/foxia/application/services/order_sync_service.py`: không bắn `post_save`
(cnv_loyalty, refresh_customer_type) và không phát `order.created` (fanout WebSocket).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from django.db import connection
from django.utils.text import slugify

from apps.common.phone_utils import normalize_phone
from apps.pancake_pos.domain.inbound_order import NewCrmOrder, ResolvedProduct

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CrmOrderState:
    id: int
    status: int
    shipping_code: str
    shipping_provider: str
    invoice_code: str
    deleted: bool


_BY_CODES_SQL = """
SELECT id FROM orders
 WHERE deleted_at IS NULL
   AND ((invoice_code > '' AND invoice_code = ANY(%(codes)s))
        OR (order_code > '' AND order_code = ANY(%(codes)s)))
 ORDER BY id
 LIMIT 10
"""

_BY_TRACKING_SQL = """
SELECT id FROM orders
 WHERE deleted_at IS NULL AND shipping_code > '' AND shipping_code = %(code)s
 ORDER BY id
 LIMIT 10
"""

_STATE_SQL = """
SELECT id, status, coalesce(shipping_code, ''), coalesce(shipping_provider, ''), coalesce(invoice_code, ''),
       deleted_at IS NOT NULL
  FROM orders WHERE id = %(id)s
"""

# Đơn mirror: `partner_*` thuộc hệ đối tác của chúng (Foxia/CNV ghi) — Pancake không ghi đè.
MIRROR_ORDER_SOURCES = ("foxia", "cnv")

# PK lookup; điều kiện "khác giá trị" nằm trong WHERE → không đổi thì 0 dòng, không ghi WAL.
_WRITE_PARTNER_SQL = """
UPDATE orders
   SET partner_delivery_status = %(ps)s,
       partner_dvvc_delivery_status = %(ds)s,
       shipping_code = CASE WHEN coalesce(shipping_code, '') = '' AND %(code)s <> '' THEN %(code)s
                            ELSE shipping_code END,
       shipping_provider = CASE WHEN coalesce(shipping_provider, '') = '' AND %(prov)s <> '' THEN %(prov)s
                                ELSE shipping_provider END
 WHERE id = %(id)s
   AND deleted_at IS NULL
   AND (order_source IS NULL OR NOT (order_source = ANY(%(mirror)s)))
   AND (partner_delivery_status IS DISTINCT FROM %(ps)s
        OR partner_dvvc_delivery_status IS DISTINCT FROM %(ds)s
        OR (coalesce(shipping_code, '') = '' AND %(code)s <> '')
        OR (coalesce(shipping_provider, '') = '' AND %(prov)s <> ''))
"""

_CHANNELS_SQL = """
SELECT id, code, name FROM channels WHERE deleted_at IS NULL
"""

_PRODUCTS_BY_VARIATION_SQL = """
SELECT l.pancake_variation_id, p.id, p.sku, p.name
  FROM pancake_pos_variation_links l
  JOIN products p ON p.id = l.product_id
 WHERE l.pancake_variation_id = ANY(%(ids)s)
"""

_PRODUCTS_BY_SKU_SQL = """
SELECT upper(p.sku), p.id, p.sku, p.name
  FROM products p
 WHERE upper(p.sku) = ANY(%(skus)s) AND p.deleted_at IS NULL
 ORDER BY p.id
"""


def _ids(rows) -> list[int]:
    return [int(r[0]) for r in rows]


class SqlCrmOrderGateway:
    """Cài đặt `application.services.inbound_order_service.CrmOrderPort`."""

    # ── tra cứu ─────────────────────────────────────────────────────────────
    def find_by_codes(self, codes: Sequence[str]) -> list[int]:
        codes = [c for c in codes if c]
        if not codes:
            return []
        with connection.cursor() as cur:
            cur.execute(_BY_CODES_SQL, {"codes": codes})
            return _ids(cur.fetchall())

    def find_by_tracking(self, code: str) -> list[int]:
        if not code:
            return []
        with connection.cursor() as cur:
            cur.execute(_BY_TRACKING_SQL, {"code": code})
            return _ids(cur.fetchall())

    def load(self, order_id: int) -> CrmOrderState | None:
        with connection.cursor() as cur:
            cur.execute(_STATE_SQL, {"id": order_id})
            row = cur.fetchone()
        if row is None:
            return None
        return CrmOrderState(int(row[0]), int(row[1]), row[2], row[3], row[4], bool(row[5]))

    def resolve_channel(self, *, keys: Sequence[str], account_name: str, mapping: dict[str, int]) -> int | None:
        """Kênh CRM cho đơn gốc Pancake: cấu hình `account/page_id=channel` trước; không có
        thì kênh (chưa xoá) trùng TÊN hoặc MÃ với tên shop Pancake (so không phân biệt
        hoa/thường, mã so cả dạng slug: "DELAX CHÍNH HÃNG" ↔ `delax-chinh-hang`)."""
        with connection.cursor() as cur:
            cur.execute(_CHANNELS_SQL)
            channels = cur.fetchall()
        alive = {int(cid) for cid, _code, _name in channels}
        for key in keys:
            if key and mapping.get(key) in alive:
                return mapping[key]
        name = (account_name or "").strip().lower()
        if not name:
            return None
        slug = slugify(account_name)
        matches = sorted({
            int(cid) for cid, code, cname in channels
            if (cname or "").strip().lower() == name or (code or "").strip().lower() in (name, slug)
        })
        return matches[0] if len(matches) == 1 else None

    def resolve_products(self, *, variation_ids: Sequence[str], skus: Sequence[str]) -> dict[str, ResolvedProduct]:
        """Khoá `var:<id>` / `sku:<SKU HOA>` (khớp `domain.inbound_order.item_key`)."""
        out: dict[str, ResolvedProduct] = {}
        with connection.cursor() as cur:
            if variation_ids:
                cur.execute(_PRODUCTS_BY_VARIATION_SQL, {"ids": sorted(set(variation_ids))})
                for vid, pid, sku, name in cur.fetchall():
                    out[f"var:{vid}"] = ResolvedProduct(int(pid), sku or "", name or "")
            wanted = sorted({s.upper() for s in skus if s})
            if wanted:
                cur.execute(_PRODUCTS_BY_SKU_SQL, {"skus": wanted})
                for key, pid, sku, name in cur.fetchall():
                    out.setdefault(f"sku:{key}", ResolvedProduct(int(pid), sku or "", name or ""))
        return out

    # ── ghi ─────────────────────────────────────────────────────────────────
    def lock_order(self, order_id: int) -> None:
        """Khoá dòng đơn CRM tới hết transaction (tuần tự hoá các kiện của cùng đơn)."""
        with connection.cursor() as cur:
            cur.execute("SELECT 1 FROM orders WHERE id = %(id)s FOR UPDATE", {"id": order_id})

    def write_partner_status(
        self, order_id: int, *, partner_status: str, dvvc_status: str, shipping_code: str = "",
        shipping_provider: str = "",
    ) -> bool:
        """Ghi 2 field partner (+ điền mã vận đơn/ĐVVC khi CRM còn TRỐNG) bằng MỘT câu UPDATE
        có điều kiện — chỉ khi giá trị đổi; không `save()` → không post_save/fan-out, không
        đụng `status`, không đẩy `updated_at` (đó là mốc A2 dùng để phát hiện sửa đơn → PUT
        Pancake; ghi ngược trạng thái Pancake không phải "sửa đơn"). Đơn mirror Foxia/CNV
        bị loại: 2 field đó là của hệ đối tác tương ứng. Trả True nếu có ghi."""
        params = {
            "id": order_id, "ps": partner_status[:64], "ds": dvvc_status[:64],
            "code": (shipping_code or "")[:100], "prov": (shipping_provider or "")[:50],
            "mirror": list(MIRROR_ORDER_SOURCES),
        }
        with connection.cursor() as cur:
            cur.execute(_WRITE_PARTNER_SQL, params)
            return cur.rowcount > 0

    def create_order(self, new: NewCrmOrder) -> int:
        """Tạo đơn CRM (+ dòng hàng) bằng bulk_create — không bắn post_save/order.created."""
        from apps.orders.models import Order, OrderItem

        customer_id = self._customer_id(new.customer_name, new.phone)
        phone = new.phone[:30]
        order = Order(
            channel_id=new.channel_id, customer_id=customer_id, name=new.customer_name[:255], phone=phone,
            phone_normalized=normalize_phone(phone) if phone else "", raw_address=new.address,
            address=new.address, has_address=bool(new.address), product=new.product_text,
            product_raw=new.product_text, sku=new.sku_text, quantity=new.quantity, status=new.status,
            order_source=new.order_source, external_order_id=new.pancake_order_id[:100],
            total_amount=Decimal(new.total_amount), discount_amount=Decimal(new.discount_amount),
            total_after_discount=Decimal(new.total_after_discount), shipping_code=new.shipping_code[:100],
            shipping_provider=new.shipping_provider[:50],
            result="Tạo từ webhook Pancake POS" + (
                f" — thiếu sản phẩm CRM: {'; '.join(new.missing_items)}" if new.missing_items else ""
            ),
        )
        (created,) = Order.all_objects.bulk_create([order])
        OrderItem.objects.bulk_create([
            OrderItem(
                order_id=created.id, product_id=line.product_id, product_name=line.name[:255], sku=line.sku[:100],
                quantity=line.quantity, unit_price=Decimal(line.unit_price), discount=Decimal(line.discount),
                line_total=Decimal((line.unit_price - line.discount) * line.quantity),
            )
            for line in new.lines
        ])
        logger.info("pancake_pos: tạo đơn CRM %s từ đơn Pancake %s", created.id, new.pancake_order_id)
        return int(created.id)

    @staticmethod
    def _customer_id(full_name: str, phone: str) -> int:
        """SĐT hợp lệ → khách theo SĐT (dùng lại/tạo). SĐT bị sàn che (`******`) → tạo khách
        mới giữ chuỗi thô, như import KiotViet (`_upsert_customer`)."""
        from apps.customers.models import Customer
        from apps.customers.services import CustomerService

        if normalize_phone(phone):
            return int(CustomerService.get_or_create_by_phone(phone=phone, full_name=full_name).id)
        customer = Customer.objects.create(full_name=full_name or "Khách Pancake", phone_raw=phone[:64],
                                           phone_normalized="")
        return int(customer.id)
