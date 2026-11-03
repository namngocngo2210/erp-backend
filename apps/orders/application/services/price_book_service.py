"""Điểm chân lý duy nhất cho việc chọn bảng giá và tra giá đã fallback.

Mọi nơi cần biết "đơn này tính theo giá nào" phải đi qua module này. Riêng KOC:
KHÔNG có nhánh if nào cho KOC ở đây — kênh KOC trỏ sang bảng giá `web` bằng dữ
liệu (xem management command ``assign_koc_price_book``).
"""
from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import PriceBook, ProductPackagePrice


def _active_books():
    return PriceBook.objects.filter(deleted_at__isnull=True, is_active=True)


def default_price_book() -> PriceBook | None:
    """Bảng giá mặc định (đích fallback). None khi DB chưa seed."""
    return _active_books().filter(is_default=True).first()


def resolve_price_book(channel) -> PriceBook | None:
    """Bảng giá áp dụng cho một kênh bán.

    Kênh chưa gán, hoặc gán vào bảng đã xoá/đã tắt → về bảng mặc định.
    """
    book_id = getattr(channel, "price_book_id", None)
    if book_id:
        book = _active_books().filter(id=book_id).first()
        if book is not None:
            return book
    return default_price_book()


def resolve_price_book_by_code(code: str | None) -> PriceBook | None:
    """Tra bảng giá theo code (lenient — code lạ/rỗng thì về mặc định, không raise)."""
    text = str(code or "").strip().lower()
    if text:
        book = _active_books().filter(code=text).first()
        if book is not None:
            return book
    return default_price_book()


def package_prices(
    product_ids: Iterable[int], book: PriceBook | None
) -> dict[tuple[int, int], Decimal]:
    """Giá từng gói của các sản phẩm, đã fallback về bảng mặc định.

    Trả về map ``(product_id, quantity) -> price``. Một query duy nhất cho cả hai
    bảng giá (đang chọn + mặc định), gộp bằng Python — giữ đúng đặc tính O(1) query
    của ``_serialize_products_fast``.
    """
    ids = [int(pid) for pid in product_ids]
    if not ids:
        return {}
    fallback = default_price_book()
    book_ids = {b.id for b in (book, fallback) if b is not None}
    if not book_ids:
        return {}

    rows = ProductPackagePrice.objects.filter(
        package__product_id__in=ids, price_book_id__in=book_ids
    ).values_list("package__product_id", "package__quantity", "price_book_id", "price")

    fallback_id = fallback.id if fallback is not None else None
    selected_id = book.id if book is not None else fallback_id
    result: dict[tuple[int, int], Decimal] = {}
    for product_id, quantity, price_book_id, price in rows:
        key = (product_id, quantity)
        # Giá của bảng đang chọn luôn thắng; giá bảng mặc định chỉ điền chỗ trống.
        if price_book_id == selected_id:
            result[key] = price
        elif key not in result and price_book_id == fallback_id:
            result[key] = price
    return result


def unit_price_for(product, book: PriceBook | None) -> Decimal:
    """Đơn giá 1 sản phẩm theo bảng giá — giá gói ``quantity=1`` đã fallback.

    Dùng cho các luồng ingest (landing/Lark/Excel) vốn chỉ nhân đơn giá với số
    lượng, không áp bậc gói.
    """
    prices = package_prices([product.id], book)
    price = prices.get((product.id, 1))
    if price is not None:
        return Decimal(price)
    return Decimal(product.price or 0)


class PriceBookNotFoundError(LookupError):
    """Bảng giá không tồn tại hoặc đã bị soft-delete."""


class DefaultPriceBookDeleteError(ValueError):
    """Cấm xoá bảng giá mặc định — nó là đích fallback của mọi bảng khác."""


def soft_delete_price_book(price_book_id: int) -> None:
    """Soft-delete một bảng giá (đặt ``deleted_at`` + tắt ``is_active``).

    ``select_for_update()`` khoá hàng ngay lúc fetch, và ``is_default`` được re-check
    NGAY TRƯỚC khi save (đọc từ object vừa khoá, không dùng giá trị fetch từ trước) —
    chặn race với một PATCH set-default chạy đồng thời và commit xen giữa lúc DELETE
    fetch xong và lúc DELETE save: nếu không khoá, DELETE có thể soft-delete một bảng
    mà PATCH vừa kịp đặt ``is_default=True``, làm hệ thống mất bảng mặc định fallback.

    Raise ``PriceBookNotFoundError`` khi không có bảng, ``DefaultPriceBookDeleteError``
    khi bảng đang là mặc định. Tách khỏi view theo audit 2026-09-23 T3/P2-1.
    """
    with transaction.atomic():
        book = (
            PriceBook.objects.select_for_update()
            .filter(id=price_book_id, deleted_at__isnull=True)
            .first()
        )
        if book is None:
            raise PriceBookNotFoundError(price_book_id)
        if book.is_default:
            # Bảng mặc định là đích fallback của mọi bảng khác — xoá đi thì sản phẩm
            # chưa khai giá ở nền tảng nào cũng mất giá.
            raise DefaultPriceBookDeleteError(price_book_id)
        book.deleted_at = timezone.now()
        book.is_active = False
        book.save(update_fields=["deleted_at", "is_active", "updated_at"])
