"""Backfill Order.landing_confirmed_amount cho đơn ingest tạo trước khi có field.

Phạm vi: đơn ``order_source=landing`` HOẶC đơn có LandingIngestTrace CREATED
(đơn đổ qua API ingest nhưng payload gửi order_source khác — zalo, messenger...).
Đơn phải có product_raw hoặc sku thì mới tính được; thiếu cả hai -> giữ 0.

Tính lại theo đúng thứ tự fallback của ingest (bỏ bước payload vì không còn):
  1. Số tiền parse được từ product_raw (derive_total_amount).
  2. quantity đã lưu (fallback derive_quantity) × đơn giá bảng giá kênh theo sku
     đã lưu (unit_price_for — giá CATALOG HIỆN TẠI, chấp nhận sai số nếu giá đổi).
     Chạy được cả khi product_raw RỖNG — đơn ingest chỉ gửi product_id vẫn có
     sku/quantity trong DB (case 4 đơn 1.715.000 phát hiện 2026-08-04).
  3. Không ra gì -> giữ 0 (toàn bộ tiền đơn tính vào cột "chốt" theo spec).

Idempotent: chỉ đụng đơn landing_confirmed_amount = 0. Mặc định DRY-RUN,
--apply để ghi thật. Update bằng queryset.update() — không bắn signal/WS event.
Chạy tay giờ thấp điểm (tiền lệ migration 0032).

--repair-text-suffix: sửa dữ liệu ĐÃ GHI SAI bởi bug parse "khớp phần đuôi"
(2026-08-04: "1.599.000" bị nhận thành 599.000). Chỉ sửa đơn mà giá trị đang lưu
ĐÚNG BẰNG kết quả của parser lỗi cũ — đơn có snapshot từ payload tường minh
không bị đụng tới.
"""
import re
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.orders.application.services.price_book_service import (
    resolve_price_book,
    unit_price_for,
)
from apps.orders.models import LandingIngestTraceStatus, Order, OrderSource
from apps.catalog.models import Product
from apps.orders.product_parser import (
    SEPARATOR_PATTERN,
    SPECIAL_TOTAL_AMOUNT_RULES,
    TOTAL_AMOUNT_VALUES,
    derive_quantity,
    derive_total_amount,
    normalize_product_text,
)


def _legacy_amount_pattern(amount: int) -> re.Pattern[str]:
    """Bản sao parser TRƯỚC fix 2026-08-04 (biên \\b khớp cả đuôi số lớn hơn).

    Giữ lại CHỦ ĐÍCH để --repair-text-suffix nhận diện chính xác dòng bị parser
    cũ ghi sai; không dùng cho bất kỳ luồng ghi mới nào.
    """
    groups = f"{amount:,}".split(",")
    pattern = r"\b" + groups[0]
    for group in groups[1:]:
        pattern += rf"{SEPARATOR_PATTERN}{group}"
    pattern += r"\b"
    return re.compile(pattern, re.IGNORECASE)


_LEGACY_VALUES = [v for v in TOTAL_AMOUNT_VALUES if v != 1599000]  # 1599000 thêm sau fix
_LEGACY_AMOUNT_RULES: list[tuple[re.Pattern[str], int]] = [
    *SPECIAL_TOTAL_AMOUNT_RULES,
    *[(_legacy_amount_pattern(amount), amount) for amount in _LEGACY_VALUES],
]


def _legacy_derive_total_amount(product_text: str) -> int:
    text = normalize_product_text(product_text)
    for pattern, amount in _LEGACY_AMOUNT_RULES:
        if pattern.search(text):
            return amount
    return 0


class Command(BaseCommand):
    help = (
        "Backfill landing_confirmed_amount cho đơn landing cũ từ product_raw. "
        "Mặc định dry-run; --apply để ghi."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Ghi thật vào DB.")
        parser.add_argument(
            "--batch-size", type=int, default=500, help="Số đơn xử lý mỗi lượt iterator."
        )
        parser.add_argument(
            "--repair-text-suffix",
            action="store_true",
            help=(
                "Sửa đơn bị bug parse khớp-phần-đuôi ghi sai (vd 1.599.000 -> 599.000). "
                "Cũng tôn trọng dry-run/--apply."
            ),
        )

    @staticmethod
    def _ingest_orders_scope():
        """Đơn thuộc phạm vi snapshot: nguồn landing HOẶC có trace ingest CREATED.

        Trace bắt các đơn đổ qua API ingest nhưng payload gửi order_source khác
        (zalo, messenger...) — cùng tiêu chí với backfill_ingest_show_orders.
        """
        return (
            Order.objects.filter(deleted_at__isnull=True)
            .filter(
                Q(order_source=OrderSource.LANDING)
                | Q(landing_ingest_traces__status=LandingIngestTraceStatus.CREATED)
            )
            .distinct()
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        batch_size = options["batch_size"]
        if options["repair_text_suffix"]:
            self._repair_text_suffix(apply=apply, batch_size=batch_size)
            return

        qs = (
            self._ingest_orders_scope()
            .filter(landing_confirmed_amount=0)
            # Cần ít nhất một nguồn dữ liệu để tính: text hoặc sku.
            .exclude(Q(product_raw="") & Q(sku=""))
            .select_related("channel")
            .only(
                "id",
                "product_raw",
                "sku",
                "quantity",
                "landing_confirmed_amount",
                "channel",
            )
        )

        stats = {"from_text": 0, "from_price_book": 0, "unresolved": 0}
        products_by_sku = {p.sku: p for p in Product.objects.all()}
        book_by_channel: dict[int, object] = {}
        updates: list[tuple[int, Decimal]] = []

        # Keyset pagination theo id thay vì qs.iterator(): iterator() dùng
        # server-side cursor, prod đi qua PgBouncer transaction pooling nên cursor
        # chết giữa các lần fetch (InvalidCursorName — sự cố khi chạy 2026-08-04).
        last_id = 0
        while True:
            batch = list(qs.filter(id__gt=last_id).order_by("id")[:batch_size])
            if not batch:
                break
            last_id = batch[-1].id
            for order in batch:
                amount = Decimal(derive_total_amount(order.product_raw))
                if amount > 0:
                    stats["from_text"] += 1
                else:
                    # Đơn ingest cũ có thể chứa nhiều SKU nối chuỗi — chỉ backfill được
                    # ca 1 SKU khớp catalog; ca nhiều SKU không tách được số lượng
                    # từng dòng nên xếp vào unresolved (giữ 0).
                    product = products_by_sku.get(order.sku)
                    if product is not None:
                        if order.channel_id not in book_by_channel:
                            book_by_channel[order.channel_id] = resolve_price_book(order.channel)
                        quantity = order.quantity or derive_quantity(order.product_raw) or 1
                        amount = unit_price_for(product, book_by_channel[order.channel_id]) * quantity
                        stats["from_price_book"] += 1
                if amount > 0:
                    updates.append((order.id, amount))
                else:
                    stats["unresolved"] += 1

        self.stdout.write(f"Đơn landing cần backfill: {qs.count()}")
        self.stdout.write(f"  - bóc được tiền từ product_raw: {stats['from_text']}")
        self.stdout.write(f"  - tính từ quantity × bảng giá: {stats['from_price_book']}")
        self.stdout.write(f"  - không bóc được (giữ 0): {stats['unresolved']}")

        if not apply:
            self.stdout.write("DRY-RUN: chưa ghi gì. Thêm --apply để cập nhật.")
            return

        updated = 0
        for order_id, amount in updates:
            updated += Order.objects.filter(
                id=order_id, landing_confirmed_amount=0
            ).update(landing_confirmed_amount=amount)
        self.stdout.write(self.style.SUCCESS(f"Đã backfill {updated} đơn."))

    def _repair_text_suffix(self, *, apply: bool, batch_size: int) -> None:
        """Sửa snapshot bị parser cũ ghi sai kiểu khớp-phần-đuôi.

        Điều kiện sửa (cả ba phải đúng — để KHÔNG đụng snapshot đến từ payload
        tường minh hay từ bảng giá):
          1. parser cũ ra được số > 0 từ product_raw;
          2. giá trị đang lưu == đúng số parser cũ ra;
          3. parser mới ra số KHÁC (0 thì rơi tiếp xuống quantity × bảng giá).
        """
        qs = (
            self._ingest_orders_scope()
            .filter(landing_confirmed_amount__gt=0)
            .exclude(product_raw="")
            .select_related("channel")
            .only(
                "id",
                "product_raw",
                "sku",
                "quantity",
                "landing_confirmed_amount",
                "channel",
            )
        )

        products_by_sku = {p.sku: p for p in Product.objects.all()}
        book_by_channel: dict[int, object] = {}
        updates: list[tuple[int, Decimal, Decimal]] = []

        last_id = 0
        while True:
            batch = list(qs.filter(id__gt=last_id).order_by("id")[:batch_size])
            if not batch:
                break
            last_id = batch[-1].id
            for order in batch:
                legacy = _legacy_derive_total_amount(order.product_raw)
                if legacy <= 0 or order.landing_confirmed_amount != Decimal(legacy):
                    continue
                fixed = Decimal(derive_total_amount(order.product_raw))
                if fixed == Decimal(legacy):
                    continue  # parser mới đồng ý với số cũ -> không phải nạn nhân
                if fixed <= 0:
                    product = products_by_sku.get(order.sku)
                    if product is not None:
                        if order.channel_id not in book_by_channel:
                            book_by_channel[order.channel_id] = resolve_price_book(order.channel)
                        quantity = order.quantity or derive_quantity(order.product_raw) or 1
                        fixed = unit_price_for(product, book_by_channel[order.channel_id]) * quantity
                if fixed > 0 and fixed != order.landing_confirmed_amount:
                    updates.append((order.id, order.landing_confirmed_amount, fixed))

        self.stdout.write(f"Đơn bị parse khớp-phần-đuôi cần sửa: {len(updates)}")
        for order_id, old, new in updates[:20]:
            self.stdout.write(f"  - order {order_id}: {old} -> {new}")
        if len(updates) > 20:
            self.stdout.write(f"  ... và {len(updates) - 20} đơn nữa")

        if not apply:
            self.stdout.write("DRY-RUN: chưa ghi gì. Thêm --apply để cập nhật.")
            return

        updated = 0
        for order_id, old, new in updates:
            updated += Order.objects.filter(
                id=order_id, landing_confirmed_amount=old
            ).update(landing_confirmed_amount=new)
        self.stdout.write(self.style.SUCCESS(f"Đã sửa {updated} đơn."))
