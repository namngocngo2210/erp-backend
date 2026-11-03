"""Kéo đơn từ CNV về bảng `orders` (chiều ngược, spec 2026-08-19).

Một điểm ingest DUY NHẤT cho cả webhook lẫn poll — cùng lý do `webhook_service`
của Best Express dùng chung `apply_event` cho webhook và TraceQuery sync: hai
đường vào, một chỗ quyết định dữ liệu trông như thế nào.

Hai thứ phải đúng tuyệt đối ở file này:

1. **Không đẩy ngược lên CNV.** Mọi ghi đều bằng ``bulk_create``/``bulk_update``
   — Django KHÔNG phát ``post_save`` cho hai method này, nên subscriber sync
   không kích. Đó là lớp chặn thứ hai; lớp thứ nhất là ``"cnv"`` nằm trong
   ``MIRROR_ORDER_SOURCES``. Đổi sang ``.save()`` ở đây là mở lại vòng lặp echo.
2. **Không trùng đơn mình đã đẩy lên.** Xem ``_should_skip``.

Parser cố tình dễ tính: swagger CNV khai response của mọi endpoint chỉ là
``200 Success``, không có schema body. Thiếu field thì bỏ field đó, không văng
cả lô — một đơn dị dạng không được phép chặn 199 đơn còn lại.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, UTC
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from apps.cnv_loyalty.application.services import sentinel_service
from apps.cnv_loyalty.contracts.dtos import CnvInboundApi
from apps.cnv_loyalty.domain.mappers import UNIQUE_ID_PREFIX
from apps.cnv_loyalty.models import (
    CnvEntityType,
    CnvOrderLine,
    CnvOrderLink,
    CnvSyncRecord,
)

logger = logging.getLogger(__name__)

# `unique_id` của đơn do CHÍNH MÌNH đẩy lên: "desi-<order_id>".
_OWN_UNIQUE_ID = re.compile(rf"^{re.escape(UNIQUE_ID_PREFIX)}-\d+$", re.IGNORECASE)

# Order.total_amount là DECIMAL(12,2) — chặn ở đây thay vì để Postgres văng
# numeric field overflow giữa một lô 200 đơn.
_MONEY_MAX = Decimal("9999999999.99")
_PAGE_LIMIT = 100
_MAX_PAGES = 200  # trần an toàn: 20k đơn/lần chạy, tránh vòng lặp vô hạn nếu API lờ `page`


@dataclass
class IngestStats:
    created: int = 0
    updated: int = 0
    skipped_own: int = 0
    skipped_invalid: int = 0
    failed: int = 0
    pages: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "created": self.created,
            "updated": self.updated,
            "skipped_own": self.skipped_own,
            "skipped_invalid": self.skipped_invalid,
            "failed": self.failed,
            "pages": self.pages,
        }


class CnvOrderIngestService:
    def __init__(self, client: CnvInboundApi | None = None):
        # client chỉ cần cho đường POLL. Đường webhook đã có sẵn payload nên
        # khởi tạo được service mà không cần kết nối — test cũng nhờ đó gọn.
        self.client = client
        self._channel = None

    # -- public -------------------------------------------------------------

    def ingest_payload(self, payload) -> IngestStats:
        """Ingest payload thô bất kỳ (webhook hoặc một trang poll)."""
        return self.ingest_rows(extract_order_rows(payload))

    def ingest_rows(self, rows: list[dict]) -> IngestStats:
        stats = IngestStats()
        for raw in rows:
            try:
                self._ingest_one(raw, stats)
            except Exception as exc:  # noqa: BLE001 — một đơn hỏng không chặn cả lô
                stats.failed += 1
                message = f"cnv_order_id={raw.get('id')}: {exc}"
                if len(stats.errors) < 20:
                    stats.errors.append(message)
                logger.exception("CNV ingest: lỗi đơn %s", raw.get("id"))
        return stats

    def poll(self, window_from: datetime, window_to: datetime) -> IngestStats:
        """Quét cửa sổ thời gian, lật trang tới khi hết."""
        if self.client is None:
            raise RuntimeError("poll() cần client CNV")
        stats = IngestStats()
        for page in range(1, _MAX_PAGES + 1):
            data = self.client.list_orders(
                {
                    "updated_at_from": window_from.isoformat(),
                    "updated_at_to": window_to.isoformat(),
                    "limit": _PAGE_LIMIT,
                    "page": page,
                }
            )
            rows = extract_order_rows(data)
            stats.pages = page
            if not rows:
                break
            page_stats = self.ingest_rows(rows)
            _merge(stats, page_stats)
            if len(rows) < _PAGE_LIMIT:
                break
        else:
            logger.warning(
                "CNV poll: chạm trần %s trang — cửa sổ %s→%s còn tồn đọng, thu hẹp "
                "cửa sổ hoặc chạy lại",
                _MAX_PAGES, window_from, window_to,
            )
        return stats

    # -- dedup --------------------------------------------------------------

    def _should_skip(self, cnv_order_id: int, unique_id: str) -> bool:
        """Đơn này có phải đơn CHÍNH MÌNH đã đẩy lên CNV không?

        Tầng 1 — `unique_id` dạng ``desi-<id>``: nhận ra tức thì, không tốn query.
        Tầng 2 — `cnv_id` đã nằm trong outbox `CnvSyncRecord`: cứu trường hợp CNV
        không echo `unique_id` về (swagger không khai response nên không loại trừ
        được khả năng đó). Thiếu tầng 2 là mỗi đơn CRM đẩy lên sẽ quay về thành
        một đơn mirror trùng lặp.

        Tầng 2 KHÔNG lọc theo ``status``. `cnv_id` chỉ được ghi khi CNV đã thực sự
        tạo đơn, nên nó là bằng chứng "đơn này là của mình" bất kể lần đồng bộ SAU
        đó thành hay bại. Lọc ``status=synced`` (bản đầu, MR !1254) để lọt đúng ca
        hay xảy ra nhất: đơn tạo trên CNV xong, lần cập nhật sau lỗi →
        ``status=failed`` nhưng `cnv_id` vẫn còn → tầng 2 mù → đơn quay về thành
        một bản mirror trùng lặp.
        """
        if unique_id and _OWN_UNIQUE_ID.match(unique_id):
            return True
        return CnvSyncRecord.objects.filter(
            entity_type=CnvEntityType.ORDER,
            cnv_id=cnv_order_id,
        ).exists()

    # -- upsert -------------------------------------------------------------

    def _ingest_one(self, raw: dict, stats: IngestStats) -> None:
        from apps.orders.models import Order, OrderSource, OrderStatus

        cnv_order_id = _to_int(raw.get("id"))
        if not cnv_order_id:
            stats.skipped_invalid += 1
            logger.warning("CNV ingest: bỏ đơn không có id (keys=%s)", sorted(raw.keys()))
            return

        unique_id = str(raw.get("unique_id") or "").strip()
        if self._should_skip(cnv_order_id, unique_id):
            stats.skipped_own += 1
            return

        customer_raw = raw.get("customer") if isinstance(raw.get("customer"), dict) else {}
        full_name = _customer_name(customer_raw)
        phone_raw = str(customer_raw.get("phone") or raw.get("phone") or "").strip()
        phone = _normalize_phone(phone_raw)
        customer, _matched = sentinel_service.resolve_customer(phone)

        lines = _extract_lines(raw)
        address = _format_address(raw.get("shipping_address") or raw.get("billing_address"))

        order_values = {
            "channel": self._sentinel_channel(),
            "customer": customer,
            "order_source": OrderSource.CNV,
            # KHÔNG map sang OrderStatus: chưa có bản chốt mã trạng thái CNV.
            "status": OrderStatus.NEW,
            "partner_delivery_status": _partner_status(raw),
            "name": full_name[:255],
            "phone": (phone or phone_raw)[:30],
            "address": address,
            "raw_address": address,
            "has_address": bool(address),
            "order_code": str(raw.get("name") or f"CNV-{cnv_order_id}")[:100],
            "product": ", ".join(line["product_name"] for line in lines if line["product_name"])[:2000],
            "total_amount": _money(raw.get("subtotal_price")),
            "discount_amount": _money(raw.get("total_discounts")),
            "total_after_discount": _money(raw.get("total_price")),
        }
        link_values = {
            "cnv_name": str(raw.get("name") or "")[:100],
            "unique_id": unique_id[:128],
            "cnv_customer_id": _to_int(customer_raw.get("id")),
            "financial_status": str(raw.get("financial_status") or "")[:32],
            "fulfillment_status": str(raw.get("fulfillment_status") or "")[:32],
            "cancelled_status": str(raw.get("cancelled_status") or "")[:32],
            "location_id": _to_int(raw.get("location_id")),
            "subtotal_price": _money(raw.get("subtotal_price")),
            "total_discounts": _money(raw.get("total_discounts")),
            "total_price": _money(raw.get("total_price")),
            "shipment_fee": _money(raw.get("shipment_fee")),
            "cnv_created_at": _parse_dt(raw.get("created_at")),
            "cnv_updated_at": _parse_dt(raw.get("updated_at")) or timezone.now(),
        }

        link = CnvOrderLink.objects.filter(cnv_order_id=cnv_order_id).select_related("order").first()
        if link is None:
            # bulk_create KHÔNG phát post_save — đó là điểm mấu chốt, xem docstring.
            order = Order.all_objects.bulk_create([Order(**order_values)])[0]
            link = CnvOrderLink.objects.create(
                order=order, cnv_order_id=cnv_order_id, **link_values
            )
            stats.created += 1
        else:
            order = link.order
            for attr, value in order_values.items():
                setattr(order, attr, value)
            Order.all_objects.bulk_update([order], list(order_values.keys()))
            for attr, value in link_values.items():
                setattr(link, attr, value)
            link.save(update_fields=[*link_values.keys(), "synced_at"])
            stats.updated += 1

        self._sync_lines(link, lines)

    def _sync_lines(self, link: CnvOrderLink, lines: list[dict]) -> None:
        """Ghi lại dòng sản phẩm theo `line_index`.

        Xoá dòng thừa TRƯỚC khi upsert: đơn bị sửa bớt sản phẩm phía CNV mà mình
        chỉ update thì dòng cũ ở đuôi nằm lại vĩnh viễn, tổng tiền dòng không
        khớp tổng đơn và không ai truy ra vì sao.
        """
        CnvOrderLine.objects.filter(link=link, line_index__gte=len(lines)).delete()
        if not lines:
            return
        CnvOrderLine.objects.bulk_create(
            [CnvOrderLine(link=link, **line) for line in lines],
            update_conflicts=True,
            unique_fields=["link", "line_index"],
            update_fields=[
                "cnv_product_id",
                "cnv_variant_id",
                "sku",
                "product_name",
                "quantity",
                "unit_price",
                "total_discount",
            ],
        )

    def _sentinel_channel(self):
        if self._channel is None:
            self._channel = sentinel_service.get_sentinel_channel()
        return self._channel


# -- parsing helpers (module-level: webhook service dùng lại) ----------------


def extract_order_rows(payload) -> list[dict]:
    """Moi danh sách đơn ra khỏi payload có hình dạng chưa biết chắc.

    Chấp nhận: ``{"orders": [...]}``, ``{"data": [...]}``, ``{"order": {...}}``,
    mảng trần, và một object đơn lẻ. Swagger không khai response nên đây là chỗ
    duy nhất chịu rủi ro hình dạng — đổi hình dạng thì sửa một hàm này.
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("orders", "data", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            return extract_order_rows(value)
    order = payload.get("order")
    if isinstance(order, dict):
        return [order]
    if _looks_like_order(payload):
        return [payload]
    return []


def _looks_like_order(row: dict) -> bool:
    """Nhận diện theo HÌNH DẠNG, không theo tên topic.

    Tên topic của CNV chưa xác minh được (swagger khai `topics` là array<string>
    không kèm danh sách giá trị) — bắt theo tên là bắt theo thứ mình đang đoán.
    """
    if "id" not in row:
        return False
    return any(key in row for key in ("line_items", "financial_status", "total_price", "unique_id"))


def _extract_lines(raw: dict) -> list[dict]:
    items = raw.get("line_items")
    if not isinstance(items, list):
        return []
    lines = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        lines.append(
            {
                "line_index": index,
                "cnv_product_id": _to_int(item.get("product_id")),
                "cnv_variant_id": _to_int(item.get("variant_id")),
                "sku": str(item.get("sku") or "")[:100],
                "product_name": str(item.get("name") or item.get("title") or "")[:255],
                "quantity": _money(item.get("quantity"), places=3),
                "unit_price": _money(item.get("price")),
                "total_discount": _money(item.get("total_discount")),
            }
        )
    return lines


def _customer_name(customer_raw: dict) -> str:
    # CNV tách last_name/first_name theo lối phương Tây còn `map_order_customer`
    # của mình ghép ngược lại (họ trước, tên sau) — ghép đúng thứ tự đã gửi đi.
    parts = [
        str(customer_raw.get("last_name") or "").strip(),
        str(customer_raw.get("first_name") or "").strip(),
    ]
    joined = " ".join(part for part in parts if part)
    return joined or str(customer_raw.get("name") or "").strip()


def _format_address(address_raw) -> str:
    if not isinstance(address_raw, dict):
        return ""
    parts = [
        str(address_raw.get(key) or "").strip()
        for key in ("address1", "address", "ward", "district", "city", "province")
    ]
    return ", ".join(part for part in parts if part)[:2000]


def _partner_status(raw: dict) -> str:
    """Trạng thái nguyên văn, ưu tiên cái mang nhiều thông tin nhất.

    Đơn huỷ thì `cancelled_status` mới là thứ nói lên chuyện gì xảy ra;
    `fulfillment_status` lúc đó thường rỗng.
    """
    cancelled = str(raw.get("cancelled_status") or "").strip()
    if cancelled and cancelled.lower() != "uncancelled":
        return cancelled[:64]
    fulfillment = str(raw.get("fulfillment_status") or "").strip()
    if fulfillment:
        return fulfillment[:64]
    return str(raw.get("financial_status") or "").strip()[:64]


def _normalize_phone(phone: str) -> str:
    from apps.common.phone_utils import normalize_phone

    try:
        return normalize_phone(phone) or ""
    except Exception:  # noqa: BLE001 — số rác không được chặn ingest
        return ""


def _to_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number or None


def _money(value, places: int = 2) -> Decimal:
    try:
        number = Decimal(str(value if value is not None else 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")
    if number.is_nan() or number.is_infinite():  # NaN/Inf lọt từ JSON
        return Decimal("0")
    number = max(min(number, _MONEY_MAX), -_MONEY_MAX)
    return number.quantize(Decimal(1).scaleb(-places))


def _parse_dt(value):
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        # CNV không ghi offset ở một số field — coi là UTC thay vì giờ máy chủ:
        # đoán theo TIME_ZONE của app sẽ lệch 7 tiếng và lệch im lặng.
        return parsed.replace(tzinfo=UTC)
    return parsed


def _merge(target: IngestStats, other: IngestStats) -> None:
    target.created += other.created
    target.updated += other.updated
    target.skipped_own += other.skipped_own
    target.skipped_invalid += other.skipped_invalid
    target.failed += other.failed
    for message in other.errors:
        if len(target.errors) < 20:
            target.errors.append(message)
