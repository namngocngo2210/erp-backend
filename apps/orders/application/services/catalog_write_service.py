"""Ghi catalog bán hàng: bảng giá / sản phẩm + gói giá / danh mục sản phẩm.

Chuyển từ ``product_item_crm_serializers`` (audit backend-architecture 4.6 —
business logic + transaction nằm trong DRF serializer). Serializer chỉ còn
validate + shape payload; mọi transaction, query build-map và bulk_create nằm
ở đây. Lưới chặn tái phát: ``apps/users/tests/test_serializer_orm_write_guard.py``.

Persistence của create/update tái hiện đúng ``ModelSerializer.create/update``
cho model phẳng (không nested-writable, không m2m trong validated_data):
``Model.objects.create(**validated_data)`` và setattr-từng-field + ``save()``
— giữ nguyên hành vi/API contract, chỉ đổi chỗ đứng của code.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.orders.application.services.price_book_service import default_price_book
from apps.catalog.models import PriceBook, Product, ProductCategory, ProductPackage, ProductPackagePrice

# ---------------------------------------------------------------------------
# Bảng giá (PriceBook)
# ---------------------------------------------------------------------------


def default_price_book_code() -> str:
    """Code của bảng giá mặc định hiện hành, dùng làm điều kiện bắt buộc-có-giá.

    Từ khi ``PriceBookSerializer.validate`` chặn cả PATCH tắt ``is_active`` lẫn
    bỏ cờ ``is_default`` của bảng đang là mặc định, hệ thống LUÔN có đúng một
    bảng mặc định đang active sau khi migration seed đã chạy — ``default_price_book()``
    trả ``None`` chỉ còn xảy ra ở DB chưa migrate/seed (bootstrap) hoặc sau khi
    rollback migration xoá sạch bảng ``price_books`` (xem mục 2 cutover doc). Giữ
    fallback cứng "facebook" cho đúng hai cửa sổ đó thay vì để lỗi 500 lan ra —
    "facebook" khớp với code bảng mặc định mà migration seed luôn tạo.
    """
    book = default_price_book()
    return book.code if book else "facebook"


def _demote_other_default_price_books(exclude_id: int | None) -> None:
    qs = PriceBook.objects.filter(is_default=True, deleted_at__isnull=True)
    if exclude_id is not None:
        qs = qs.exclude(id=exclude_id)
    qs.update(is_default=False)


@transaction.atomic
def create_price_book(validated_data: dict) -> PriceBook:
    if validated_data.get("is_default"):
        _demote_other_default_price_books(exclude_id=None)
    return PriceBook.objects.create(**validated_data)


@transaction.atomic
def update_price_book(instance: PriceBook, validated_data: dict) -> PriceBook:
    if validated_data.get("is_default"):
        _demote_other_default_price_books(exclude_id=instance.id)
    for attr, value in validated_data.items():
        setattr(instance, attr, value)
    instance.save()
    return instance


# ---------------------------------------------------------------------------
# Sản phẩm + gói giá (Product / ProductPackage / ProductPackagePrice)
# ---------------------------------------------------------------------------


def _sync_base_from_qty1(product: Product, packages: list[dict]) -> None:
    """Mirror gói qty=1 của BẢNG MẶC ĐỊNH về Product (cột đọc nhiều nơi: report,
    price range ở products_grouped, KiotViet)."""
    qty1 = next((p for p in packages if int(p.get("quantity") or 0) == 1), None)
    if not qty1:
        return
    default_code = default_price_book_code()
    prices = qty1.get("prices") or {}
    product.price = Decimal(str(prices.get(default_code) or product.price))
    product.weight = qty1.get("weight", product.weight)
    product.length = qty1.get("length", product.length)
    product.width = qty1.get("width", product.width)
    product.height = qty1.get("height", product.height)
    product.save(update_fields=["price", "weight", "length", "width", "height", "updated_at"])


def _replace_packages(product: Product, packages: list[dict]) -> None:
    default_code = default_price_book_code()
    # Cùng tiêu chí với ProductPackageSerializer.to_representation (chỉ loại bảng
    # ĐÃ XOÁ, không loại theo is_active): "tắt" nghĩa là "tạm không dùng để bán",
    # không phải "xoá giá đã khai". Trước đây chỗ này lọc thêm is_active=True trong
    # khi phần đọc không lọc — client fetch thấy giá của bảng đã tắt rồi PATCH gửi
    # lại nguyên si sẽ bị lặng lẽ mất giá đó (finding review Task 4).
    books_by_code = {
        book.code: book for book in PriceBook.objects.filter(deleted_at__isnull=True)
    }
    product.packages.all().delete()  # CASCADE xoá luôn book_prices cũ
    created = ProductPackage.objects.bulk_create(
        [
            ProductPackage(
                product=product,
                quantity=int(pkg["quantity"]),
                # Cột legacy: mirror giá bảng mặc định. Bị drop ở migration pha 3.
                price=Decimal(str((pkg.get("prices") or {}).get(default_code) or 0)),
                weight=pkg.get("weight", 0),
                length=pkg.get("length", 0),
                width=pkg.get("width", 0),
                height=pkg.get("height", 0),
            )
            for pkg in packages
        ]
    )
    price_rows = []
    for package, pkg in zip(created, packages, strict=True):
        for code, raw in (pkg.get("prices") or {}).items():
            book = books_by_code.get(str(code).strip().lower())
            if book is None:
                continue  # code lạ → bỏ qua, không chặn lưu sản phẩm
            value = Decimal(str(raw or 0))
            if value <= 0 and book.code != default_code:
                continue  # để trống ở nền tảng phụ = kế thừa, không lưu row 0
            price_rows.append(
                ProductPackagePrice(package=package, price_book=book, price=value)
            )
    if price_rows:
        ProductPackagePrice.objects.bulk_create(price_rows)


def _maybe_auto_group(product: Product, explicit_group: bool) -> None:
    """Auto-assign the product's group unless the caller set group_id explicitly.

    An explicit ``group_id`` in the payload is a manual override and must be
    respected. The service is imported lazily to avoid an import cycle.
    """
    if explicit_group:
        return
    from apps.orders.application.services.product_group_service import ensure_group_for_product

    ensure_group_for_product(product)


# LƯU Ý: create_product/update_product CỐ Ý không bọc @transaction.atomic —
# giữ nguyên hành vi của serializer trước refactor (cặp Product chưa từng có
# atomic, khác hai cặp PriceBook/ProductCategory). Bọc atomic là một quyết định
# đổi hành vi lỗi (rollback partial-write) — để MR riêng nếu muốn siết.


def create_product(validated_data: dict, *, packages: list[dict], explicit_group: bool) -> Product:
    """Tạo sản phẩm kèm gói giá. ``packages`` đã qua ``_validate_packages`` ở serializer."""
    product = Product.objects.create(**validated_data)
    _replace_packages(product, packages)
    _sync_base_from_qty1(product, packages)
    _maybe_auto_group(product, explicit_group)
    return product


def update_product(
    instance: Product,
    validated_data: dict,
    *,
    packages: list[dict] | None,
    explicit_group: bool,
    sku_changed: bool,
) -> Product:
    for attr, value in validated_data.items():
        setattr(instance, attr, value)
    instance.save()
    if packages is not None:
        _replace_packages(instance, packages)
        _sync_base_from_qty1(instance, packages)
    # Auto-group chỉ phụ thuộc sku, nên chỉ chạy lại hook khi sku thực sự đổi
    # (hoặc caller set group_id tường minh) — tránh side effect không liên quan
    # trên mọi PATCH chỉ sửa giá v.v.
    if explicit_group or sku_changed:
        _maybe_auto_group(instance, explicit_group)
    return instance


# ---------------------------------------------------------------------------
# Danh mục sản phẩm (ProductCategory)
# ---------------------------------------------------------------------------


def _demote_other_loyal_default_categories(exclude_id: int | None) -> None:
    """Hạ cờ mặc định ở mọi danh mục (chưa xóa) khác, để tối đa một danh mục mặc định
    và tránh vi phạm partial unique constraint khi chuyển mặc định."""
    qs = ProductCategory.objects.filter(is_loyal_default=True, deleted_at__isnull=True)
    if exclude_id is not None:
        qs = qs.exclude(id=exclude_id)
    qs.update(is_loyal_default=False)


@transaction.atomic
def create_product_category(validated_data: dict, *, product_ids: list | None) -> ProductCategory:
    if validated_data.get("is_loyal_default"):
        _demote_other_loyal_default_categories(exclude_id=None)
    category = ProductCategory.objects.create(**validated_data)
    if product_ids is not None:
        category.products.set(product_ids)
    return category


@transaction.atomic
def update_product_category(
    instance: ProductCategory, validated_data: dict, *, product_ids: list | None
) -> ProductCategory:
    if validated_data.get("is_loyal_default"):
        _demote_other_loyal_default_categories(exclude_id=instance.id)
    for attr, value in validated_data.items():
        setattr(instance, attr, value)
    instance.save()
    if product_ids is not None:
        instance.products.set(product_ids)
    return instance
