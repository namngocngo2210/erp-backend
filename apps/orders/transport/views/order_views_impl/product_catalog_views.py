"""CRUD sản phẩm / danh mục / nhóm sản phẩm + catalog grouped.

Tách từ ``order_ingest_product_dashboard_view.py`` (audit 2026-07 mục 4.5) —
move refactor, không đổi logic.
"""

import hashlib

from django.db.models import Count, Max, Min, Q
from django.http import HttpResponseNotModified
from django.utils import timezone
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import (
    OpenApiResponse,
    PolymorphicProxySerializer,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.catalog.models import PriceBook, Product, ProductCategory, ProductGroup, ProductPackage, ProductPackagePrice
from apps.orders.serializers import (
    ProductCategorySerializer,
    ProductGroupSerializer,
    ProductSerializer,
)
from apps.users.permissions import ORDER_SECTIONS, require_method_permission, require_section

# Audit 2026-08-19 N2: catalog doc chung voi 3 trang don (OR ORDER_SECTIONS) nhung
# GHI phai can quyen ghi cung-action cua mot trong cac section do.
_PRODUCT_SECTIONS = ("products", *ORDER_SECTIONS)
_CATEGORY_SECTIONS = ("product-categories", *ORDER_SECTIONS)


def _codes(sections, action):
    return tuple(f"{s}:{action}" for s in sections)


from ._view_helpers import _parse_required_pagination


def _product_category_queryset():
    """Return a base queryset for ProductCategory with product_count annotated."""
    return (
        ProductCategory.objects.filter(deleted_at__isnull=True)
        .prefetch_related("products")
        .annotate(product_count=Count("products", distinct=True))
    )


def _product_group_queryset():
    """Return a base queryset for ProductGroup with variant_count annotated.

    ``variant_count`` counts only active (not soft-deleted) products. The
    prefetch is filtered to the same set so the serializer's ``variants`` list
    and the count stay consistent and N+1-free.
    """
    from django.db.models import Prefetch

    active_products = Product.objects.filter(deleted_at__isnull=True).order_by("name", "id")
    return (
        ProductGroup.objects.filter(deleted_at__isnull=True)
        .prefetch_related(Prefetch("products", queryset=active_products))
        .annotate(
            variant_count=Count(
                "products", filter=Q(products__deleted_at__isnull=True), distinct=True
            )
        )
    )


def _serialize_products_options(product_queryset):
    """Payload SLIM cho các ô lọc/chọn theo sản phẩm — chỉ id/sku/name.

    Payload đầy đủ kéo theo packages + giá đã resolve của từng bảng giá: ~1,5MB
    cho 2.1k SP đang bán, trong khi dropdown chỉ hiển thị SKU và tìm theo tên.
    Chế độ này giữ nguyên bộ lọc/sắp xếp/phân trang, chỉ cắt phần thân payload
    và KHÔNG chạm bảng product_packages / product_package_prices.
    """
    return list(product_queryset.values("id", "sku", "name"))


def _serialize_products_fast(product_queryset, book=None, include_prices=False):
    """Serialize nhanh cho catalog product (limit tới 10k).

    DRF's nested ModelSerializer is the dominant cost when serializing thousands
    of products with their packages. We build the payload from flat `.values()`
    queries and stitch packages in Python (giữ O(1) query theo số sản phẩm).

    `price` của mỗi gói/sản phẩm là giá ĐÃ resolve theo `book` (fallback về bảng
    mặc định theo từng bậc gói qua ``price_book_service.package_prices``) — đây
    là contract cho form đơn (khác với contract quản trị `prices` dict thô, xem
    tham số ``include_prices``).
    """
    from apps.orders.application.services.price_book_service import package_prices

    products = list(product_queryset.values(
        "id", "name", "short_name", "sku", "price", "weight", "length", "width", "height", "is_active",
        "pancake_sync_mode", "group_id", "group__code", "group__name", "group__deleted_at",
        "created_at", "updated_at",
    ))
    product_ids = [p["id"] for p in products]
    resolved = package_prices(product_ids, book)

    raw_prices: dict[int, dict[str, str]] = {}
    if include_prices and product_ids:
        # Giá THÔ (không fallback) cho màn hình quản trị — một query duy nhất,
        # gộp theo package_id ở Python để giữ O(1) query.
        raw_rows = ProductPackagePrice.objects.filter(
            package__product_id__in=product_ids, price_book__deleted_at__isnull=True
        ).values_list("package_id", "price_book__code", "price")
        for package_id, code, price in raw_rows:
            raw_prices.setdefault(package_id, {})[code] = str(price)

    packages_by_product: dict[int, list[dict]] = {}
    if product_ids:
        package_rows = (
            ProductPackage.objects.filter(product_id__in=product_ids)
            .order_by("product_id", "quantity")
            .values("product_id", "id", "quantity", "price", "weight", "length", "width", "height")
        )
        for pkg in package_rows:
            # Rơi về cột legacy `price` (pha 2 vẫn dual-write) khi gói này chưa có
            # ProductPackagePrice ở cả bảng đang chọn lẫn bảng mặc định — KHÔNG được
            # trả "0" (đơn kênh chọn giá 0đ tường minh, không log gì). Đồng nhất với
            # fallback trong `products_catalog_grouped` bên dưới.
            price = resolved.get((pkg["product_id"], pkg["quantity"]), pkg["price"])
            packages_by_product.setdefault(pkg["product_id"], []).append(
                {
                    "id": pkg["id"],
                    "quantity": pkg["quantity"],
                    "price": str(price),
                    "weight": str(pkg["weight"]),
                    "length": str(pkg["length"]),
                    "width": str(pkg["width"]),
                    "height": str(pkg["height"]),
                    **({"prices": raw_prices.get(pkg["id"], {})} if include_prices else {}),
                }
            )
    base_prices = {pid: resolved.get((pid, 1)) for pid in product_ids}
    return [
        {
            "id": p["id"],
            "name": p["name"],
            "short_name": p["short_name"],
            "sku": p["sku"],
            # Giá base cũng theo bảng giá đang chọn — FE dùng nó khi sản phẩm không có
            # gói khớp số lượng (OrderFormDialog dòng 1013).
            "price": str(base_prices.get(p["id"]) if base_prices.get(p["id"]) is not None else p["price"]),
            "weight": str(p["weight"]),
            "length": str(p["length"]),
            "width": str(p["width"]),
            "height": str(p["height"]),
            "is_active": p["is_active"],
            "pancake_sync_mode": p["pancake_sync_mode"],
            "group": (
                {"id": p["group_id"], "code": p["group__code"], "name": p["group__name"]}
                # Never surface a soft-deleted group inline (FK is SET_NULL only on
                # hard delete); keep group_id in the DB but render group: null.
                if p["group_id"] and p["group__deleted_at"] is None
                else None
            ),
            "packages": packages_by_product.get(p["id"], []),
            "created_at": p["created_at"].isoformat() if p["created_at"] else None,
            "updated_at": p["updated_at"].isoformat() if p["updated_at"] else None,
        }
        for p in products
    ]


# Catalog là dữ liệu dùng chung, đổi ít → cho phép trình duyệt lưu private cache
# nhưng luôn revalidate bằng ETag (no-cache) để cập nhật hiện ngay khi có thay đổi.
_PRODUCTS_CACHE_CONTROL = "private, no-cache"


def _products_catalog_etag(
    search_query: str,
    offset: int,
    limit: int,
    group_id: int | None,
    price_book_code: str,
    include_prices: bool = False,
    slim: bool = False,
) -> str:
    """Version tag rẻ cho catalog product đang active.

    Ghép từ số lượng dòng + thời điểm cập nhật mới nhất của product/package nên
    đổi mỗi khi có product/package được tạo/sửa/(soft-)xoá hoặc bật-tắt active.
    Nhờ đó client revalidate bằng If-None-Match và nhận 304 thay vì serialize +
    truyền lại toàn bộ danh sách (tới 10k dòng).

    PHẢI phản ánh cả bảng giá đang chọn lẫn thời điểm sửa giá của mọi bảng giá:
    thiếu hai phần đó, sửa giá web xong client vẫn nhận 304 và tiếp tục bán giá cũ.

    Chế độ ``slim`` chỉ trả id/sku/name nên chỉ phụ thuộc bảng products — bỏ 4
    aggregate còn lại. Tag mang tiền tố riêng để KHÔNG bao giờ trùng tag của
    payload đầy đủ cùng tham số phân trang (trùng là client nhận 304 với payload
    sai shape).
    """
    product_agg = Product.objects.filter(is_active=True, deleted_at__isnull=True).aggregate(
        n=Count("id"), m=Max("updated_at")
    )
    if slim:
        slim_parts = [
            "slim",
            str(product_agg["n"] or 0),
            product_agg["m"].isoformat() if product_agg["m"] else "0",
            search_query,
            str(offset),
            str(limit),
            str(group_id) if group_id is not None else "",
        ]
        return f'"{hashlib.md5("|".join(slim_parts).encode("utf-8")).hexdigest()}"'

    package_m = ProductPackage.objects.filter(
        product__is_active=True, product__deleted_at__isnull=True
    ).aggregate(m=Max("updated_at"))["m"]
    price_agg = ProductPackagePrice.objects.filter(
        package__product__is_active=True, package__product__deleted_at__isnull=True
    ).aggregate(n=Count("id"), m=Max("updated_at"))
    book_agg = PriceBook.objects.aggregate(n=Count("id"), m=Max("updated_at"))
    # Groups render inline on each product, so a group rename/soft-delete must
    # bust the etag too — even though it never touches any product's updated_at.
    # Aggregate over ALL groups (incl. soft-deleted) so a soft-delete bumps the
    # count/max and clients revalidate instead of getting stale inline data.
    group_agg = ProductGroup.objects.aggregate(n=Count("id"), m=Max("updated_at"))
    parts = [
        str(product_agg["n"] or 0),
        product_agg["m"].isoformat() if product_agg["m"] else "0",
        package_m.isoformat() if package_m else "0",
        str(price_agg["n"] or 0),
        price_agg["m"].isoformat() if price_agg["m"] else "0",
        str(book_agg["n"] or 0),
        book_agg["m"].isoformat() if book_agg["m"] else "0",
        str(group_agg["n"] or 0),
        group_agg["m"].isoformat() if group_agg["m"] else "0",
        search_query,
        str(offset),
        str(limit),
        str(group_id) if group_id is not None else "",
        price_book_code,
        "1" if include_prices else "0",
    ]
    digest = hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()
    return f'"{digest}"'


# GET trả mảng product (đã serialize nhanh, khớp ProductSerializer; có thể 304 khi
# trùng ETag). POST tạo product mới → 201.
@extend_schema(
    request=ProductSerializer,
    responses={
        200: ProductSerializer(many=True),
        201: ProductSerializer,
    },
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_method_permission(GET=_codes(_PRODUCT_SECTIONS, "view"), POST=_codes(_PRODUCT_SECTIONS, "create"))])
def list_create_products(request):
    if request.method == "GET":
        pagination, error_response = _parse_required_pagination(request, max_limit=10000)
        if error_response is not None:
            return error_response

        search_query = request.query_params.get("q", "").strip()
        offset = pagination["offset"]
        limit = pagination["limit"]

        group_filter_id: int | None = None
        group_raw = request.query_params.get("group")
        if group_raw is not None and str(group_raw).strip():
            try:
                group_filter_id = int(group_raw)
            except (TypeError, ValueError):
                group_filter_id = None

        from apps.orders.application.services.price_book_service import resolve_price_book_by_code

        # Ô lọc/chọn theo SP chỉ cần id/sku/name → bỏ luôn bảng giá + packages.
        slim = request.query_params.get("slim") == "1"
        book = None if slim else resolve_price_book_by_code(request.query_params.get("price_book"))
        book_code = book.code if book else ""
        include_prices = not slim and request.query_params.get("include_prices") == "1"

        etag = _products_catalog_etag(
            search_query, offset, limit, group_filter_id, book_code, include_prices, slim
        )
        if_none_match = request.META.get("HTTP_IF_NONE_MATCH", "")
        if etag in {tag.strip() for tag in if_none_match.split(",") if tag.strip()}:
            not_modified = HttpResponseNotModified()
            not_modified["ETag"] = etag
            not_modified["Cache-Control"] = _PRODUCTS_CACHE_CONTROL
            return not_modified

        queryset = Product.objects.filter(is_active=True, deleted_at__isnull=True)
        if search_query:
            queryset = queryset.filter(Q(name__icontains=search_query) | Q(sku__icontains=search_query))
        if group_filter_id is not None:
            # Only match an active group, consistent with inline rendering and the
            # grouped catalog: filtering by a soft-deleted group returns nothing.
            queryset = queryset.filter(
                group_id=group_filter_id, group__deleted_at__isnull=True
            )

        queryset = queryset.order_by("name", "id")[offset : offset + limit]
        data = (
            _serialize_products_options(queryset)
            if slim
            else _serialize_products_fast(queryset, book, include_prices)
        )
        response = Response(data, status=status.HTTP_200_OK)
        response["ETag"] = etag
        response["Cache-Control"] = _PRODUCTS_CACHE_CONTROL
        return response

    serializer = ProductSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    response = Response(serializer.data, status=status.HTTP_201_CREATED)
    response["Cache-Control"] = "no-store"
    return response


@extend_schema(
    request=ProductSerializer,
    responses={
        200: ProductSerializer,
        204: OpenApiResponse(description="Đã xóa (soft-delete) sản phẩm"),
        404: OpenApiResponse(description="Không tìm thấy sản phẩm"),
    },
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_method_permission(PATCH=_codes(_PRODUCT_SECTIONS, "edit"), DELETE=_codes(_PRODUCT_SECTIONS, "delete"))])
@never_cache
def update_delete_product(request, product_id: int):
    product = (
        Product.objects.filter(id=product_id, deleted_at__isnull=True)
        .prefetch_related("packages__book_prices__price_book")
        .first()
    )
    if not product:
        return Response({"detail": "Không tìm thấy sản phẩm."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        product.deleted_at = timezone.now()
        product.is_active = False
        product.save(update_fields=["deleted_at", "is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = ProductSerializer(instance=product, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    request=ProductCategorySerializer,
    responses={
        200: inline_serializer(
            "ProductCategoryListEnvelope",
            fields={
                "items": ProductCategorySerializer(many=True),
                "total": drf_serializers.IntegerField(),
            },
        ),
        201: ProductCategorySerializer,
    },
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_method_permission(GET=_codes(_CATEGORY_SECTIONS, "view"), POST=_codes(_CATEGORY_SECTIONS, "create"))])
@never_cache
def list_create_product_categories(request):
    if request.method == "GET":
        # max_limit=500: FE chỉ request tối đa 100 (PAGE_MAX_SIZE trang danh mục /
        # default 100 của getProductCategories) — 10000 là cửa để một request kéo
        # cả bảng kèm prefetch products (audit 2026-07-28 DB-13). Vượt trần trả
        # 400 như mọi endpoint dùng parse_required_pagination.
        pagination, error_response = _parse_required_pagination(request, max_limit=500)
        if error_response is not None:
            return error_response

        search_query = request.query_params.get("q", "").strip()
        queryset = (
            ProductCategory.objects.filter(deleted_at__isnull=True)
            .prefetch_related("products")
            .annotate(product_count=Count("products", distinct=True))
        )
        if search_query:
            queryset = queryset.filter(Q(name__icontains=search_query) | Q(code__icontains=search_query))

        queryset = queryset.order_by("sort_order", "name", "id")
        total = queryset.count()
        page = queryset[pagination["offset"] : pagination["offset"] + pagination["limit"]]
        data = ProductCategorySerializer(page, many=True).data
        return Response({"items": data, "total": total}, status=status.HTTP_200_OK)

    serializer = ProductCategorySerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    instance = serializer.save()
    instance = _product_category_queryset().get(id=instance.id)
    return Response(ProductCategorySerializer(instance).data, status=status.HTTP_201_CREATED)


@extend_schema(
    request=ProductCategorySerializer,
    responses={
        200: ProductCategorySerializer,
        204: OpenApiResponse(description="Đã xóa (soft-delete) danh mục"),
        404: OpenApiResponse(description="Không tìm thấy danh mục"),
    },
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_method_permission(PATCH=_codes(_CATEGORY_SECTIONS, "edit"), DELETE=_codes(_CATEGORY_SECTIONS, "delete"))])
@never_cache
def update_delete_product_category(request, category_id: int):
    category = _product_category_queryset().filter(id=category_id).first()
    if not category:
        return Response({"detail": "Không tìm thấy danh mục."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        category.deleted_at = timezone.now()
        category.is_active = False
        category.save(update_fields=["deleted_at", "is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = ProductCategorySerializer(instance=category, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    instance = serializer.save()
    instance = _product_category_queryset().get(id=instance.id)
    return Response(ProductCategorySerializer(instance).data, status=status.HTTP_200_OK)


@extend_schema(
    request=ProductGroupSerializer,
    responses={
        200: ProductGroupSerializer(many=True),
        201: ProductGroupSerializer,
    },
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_method_permission(GET=_codes(_PRODUCT_SECTIONS, "view"), POST=_codes(_PRODUCT_SECTIONS, "create"))])
@never_cache
def list_create_product_groups(request):
    if request.method == "GET":
        pagination, error_response = _parse_required_pagination(request, max_limit=10000)
        if error_response is not None:
            return error_response

        search_query = request.query_params.get("q", "").strip()
        queryset = _product_group_queryset()
        if search_query:
            queryset = queryset.filter(Q(name__icontains=search_query) | Q(code__icontains=search_query))

        queryset = queryset.order_by("code", "id")[
            pagination["offset"] : pagination["offset"] + pagination["limit"]
        ]
        data = ProductGroupSerializer(queryset, many=True).data
        return Response(data, status=status.HTTP_200_OK)

    serializer = ProductGroupSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    instance = serializer.save()
    instance = _product_group_queryset().get(id=instance.id)
    return Response(ProductGroupSerializer(instance).data, status=status.HTTP_201_CREATED)


@extend_schema(
    request=ProductGroupSerializer,
    responses={
        200: ProductGroupSerializer,
        204: OpenApiResponse(description="Đã xóa (soft-delete) nhóm sản phẩm"),
        404: OpenApiResponse(description="Không tìm thấy nhóm sản phẩm"),
    },
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_method_permission(PATCH=_codes(_PRODUCT_SECTIONS, "edit"), DELETE=_codes(_PRODUCT_SECTIONS, "delete"))])
@never_cache
def update_delete_product_group(request, group_id: int):
    group = _product_group_queryset().filter(id=group_id).first()
    if not group:
        return Response({"detail": "Không tìm thấy nhóm sản phẩm."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        group.deleted_at = timezone.now()
        group.is_active = False
        group.save(update_fields=["deleted_at", "is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = ProductGroupSerializer(instance=group, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    instance = serializer.save()
    instance = _product_group_queryset().get(id=instance.id)
    return Response(ProductGroupSerializer(instance).data, status=status.HTTP_200_OK)


@extend_schema(
    responses={
        200: inline_serializer(
            "ProductsCatalogGroupedResponse",
            fields={
                "groups": inline_serializer(
                    "ProductCatalogGroup",
                    many=True,
                    fields={
                        "id": drf_serializers.IntegerField(),
                        "code": drf_serializers.CharField(),
                        "name": drf_serializers.CharField(),
                        "variants": inline_serializer(
                            "ProductCatalogVariant",
                            many=True,
                            fields={
                                "id": drf_serializers.IntegerField(),
                                "name": drf_serializers.CharField(),
                                "sku": drf_serializers.CharField(),
                                "price": drf_serializers.CharField(),
                            },
                        ),
                    },
                ),
                "ungrouped": inline_serializer(
                    "ProductCatalogUngrouped",
                    many=True,
                    fields={
                        "id": drf_serializers.IntegerField(),
                        "name": drf_serializers.CharField(),
                        "sku": drf_serializers.CharField(),
                        "price": drf_serializers.CharField(),
                    },
                ),
            },
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("products", *ORDER_SECTIONS)])
@never_cache
def products_catalog_grouped(request):
    """Grouped product catalog for the order-create UI.

    Response shape::

        {
          "groups": [
            {"id", "code", "name", "variants": [{"id", "name", "sku", "price"}]}
          ],
          "ungrouped": [
            {"id", "name", "sku", "price"}
          ]
        }

    Only active (``is_active=True``, ``deleted_at IS NULL``) products are
    included. Groups with no active variant are omitted. ``limit``/``offset``
    paginate the GROUP list (ungrouped products are always returned in full,
    capped at the same limit) to bound the payload.
    """
    # max_limit=500: FE hiện không gọi endpoint này với limit lớn (không có
    # call-site ngoài schema) — 10000 là cửa kéo cả catalog một phát
    # (audit 2026-07-28 DB-13). Vượt trần trả 400 như các endpoint khác.
    pagination, error_response = _parse_required_pagination(request, max_limit=500)
    if error_response is not None:
        return error_response
    offset = pagination["offset"]
    limit = pagination["limit"]

    from apps.orders.application.services.price_book_service import (
        package_prices,
        resolve_price_book_by_code,
    )

    book = resolve_price_book_by_code(request.query_params.get("price_book"))
    active_products = list(
        Product.objects.filter(is_active=True, deleted_at__isnull=True)
        .order_by("name", "id")
        .values("id", "name", "sku", "price", "group_id")
    )
    resolved = package_prices([p["id"] for p in active_products], book)

    groups_by_id: dict[int, dict] = {}
    ungrouped: list[dict] = []
    group_meta: dict[int, dict] = {}
    if active_products:
        group_ids = {p["group_id"] for p in active_products if p["group_id"]}
        if group_ids:
            for g in ProductGroup.objects.filter(
                id__in=group_ids, deleted_at__isnull=True
            ).values("id", "code", "name"):
                group_meta[g["id"]] = g

    for p in active_products:
        base_price = resolved.get((p["id"], 1))
        entry = {
            "id": p["id"],
            "name": p["name"],
            "sku": p["sku"],
            "price": str(base_price if base_price is not None else p["price"]),
        }
        gid = p["group_id"]
        if gid and gid in group_meta:
            bucket = groups_by_id.get(gid)
            if bucket is None:
                meta = group_meta[gid]
                bucket = {
                    "id": meta["id"],
                    "code": meta["code"],
                    "name": meta["name"],
                    "variants": [],
                }
                groups_by_id[gid] = bucket
            bucket["variants"].append(entry)
        else:
            ungrouped.append(entry)

    groups = sorted(groups_by_id.values(), key=lambda g: (g["code"], g["id"]))
    groups = groups[offset : offset + limit]
    ungrouped = ungrouped[:limit]
    return Response({"groups": groups, "ungrouped": ungrouped}, status=status.HTTP_200_OK)


# entries là danh sách hỗn hợp: phần tử kind="group" (header nhóm) hoặc
# kind="product" (bọc 1 product đầy đủ) → oneOf discriminate theo "kind" (WP6.2).
@extend_schema(
    responses={
        200: inline_serializer(
            "ProductsGroupedResponse",
            fields={
                "entries": PolymorphicProxySerializer(
                    component_name="ProductGroupedEntry",
                    resource_type_field_name="kind",
                    serializers={
                        "group": inline_serializer(
                            "ProductGroupedGroupEntry",
                            fields={
                                "kind": drf_serializers.ChoiceField(choices=["group"]),
                                "id": drf_serializers.IntegerField(),
                                "code": drf_serializers.CharField(allow_blank=True),
                                "name": drf_serializers.CharField(allow_blank=True),
                                "variant_count": drf_serializers.IntegerField(),
                                "price_min": drf_serializers.CharField(allow_null=True),
                                "price_max": drf_serializers.CharField(allow_null=True),
                            },
                        ),
                        "product": inline_serializer(
                            "ProductGroupedProductEntry",
                            fields={
                                "kind": drf_serializers.ChoiceField(choices=["product"]),
                                "product": ProductSerializer(),
                            },
                        ),
                    },
                    many=True,
                ),
                "has_next": drf_serializers.BooleanField(),
                "total": drf_serializers.IntegerField(),
            },
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("products", *ORDER_SECTIONS)])
@never_cache
def products_grouped(request):
    """Server-side paginated, grouped product catalog for the Products admin page.

    Pagination unit is an ENTRY: either a product GROUP (one expandable header
    with aggregated variant_count/price range over its ACTIVE variants) or a
    single UNGROUPED active product (rendered as a full product row).

    Response shape::

        {
          "entries": [
            {"kind": "group", "id", "code", "name",
             "variant_count", "price_min", "price_max"},
            {"kind": "product", "product": {...full product...}}
          ],
          "has_next": bool,
          "total": int  # tổng số entry sau filter, cho FE dựng phân trang số
        }

    Variants of a group are NOT included here; the client lazy-loads them via
    the existing ``?group=`` product list endpoint. Header lists are small
    (hundreds of rows) and the whole thing is a handful of queries (O(1)).
    """
    pagination, error_response = _parse_required_pagination(request, max_limit=200)
    if error_response is not None:
        return error_response
    offset = pagination["offset"]
    limit = pagination["limit"]
    search_query = request.query_params.get("q", "").strip()

    # Price range của header nhóm luôn theo BẢNG GIÁ MẶC ĐỊNH (Product.price là mirror
    # của nó) — đây là màn hình quản trị, không phải màn hình bán hàng.
    # 1. Group headers: aggregate active variants per group in a single query.
    group_rows = (
        Product.objects.filter(
            deleted_at__isnull=True,
            is_active=True,
            group__isnull=False,
            group__deleted_at__isnull=True,
        )
        .values("group_id", "group__code", "group__name")
        .annotate(cnt=Count("id"), pmin=Min("price"), pmax=Max("price"))
    )

    # 3a. Group IDs whose ACTIVE variant matches the search (one query).
    variant_match_group_ids: set[int] = set()
    if search_query:
        variant_match_group_ids = set(
            Product.objects.filter(
                deleted_at__isnull=True,
                is_active=True,
                group__isnull=False,
                group__deleted_at__isnull=True,
            )
            .filter(Q(name__icontains=search_query) | Q(sku__icontains=search_query))
            .values_list("group_id", flat=True)
        )

    group_headers: list[dict] = []
    for row in group_rows:
        gid = row["group_id"]
        code = row["group__code"] or ""
        name = row["group__name"] or ""
        if search_query and not (
            search_query.lower() in code.lower()
            or search_query.lower() in name.lower()
            or gid in variant_match_group_ids
        ):
            continue
        group_headers.append(
            {
                "kind": "group",
                "sort_key": code.lower(),
                "id": gid,
                "code": code,
                "name": name,
                "variant_count": row["cnt"],
                "price_min": str(row["pmin"]) if row["pmin"] is not None else None,
                "price_max": str(row["pmax"]) if row["pmax"] is not None else None,
            }
        )

    # 2. Ungrouped product headers (only id + sort_key here; hydrated later).
    ungrouped_qs = Product.objects.filter(
        deleted_at__isnull=True, is_active=True, group__isnull=True
    )
    if search_query:
        ungrouped_qs = ungrouped_qs.filter(
            Q(name__icontains=search_query) | Q(sku__icontains=search_query)
        )
    product_headers = [
        {"kind": "product", "sort_key": (name or "").lower(), "product_id": pid}
        for pid, name in ungrouped_qs.values_list("id", "name")
    ]

    # 4. Merge + stable sort by (sort_key, kind, id); slice with +1 lookahead.
    def _merge_key(entry: dict):
        ident = entry["id"] if entry["kind"] == "group" else entry["product_id"]
        return (entry["sort_key"], entry["kind"], ident)

    merged = sorted(group_headers + product_headers, key=_merge_key)
    window = merged[offset : offset + limit + 1]
    has_next = len(window) > limit
    page = window[:limit]

    # 5. Hydrate ungrouped product entries via the shared fast serializer.
    # Màn hình quản trị luôn cần dict giá thô (include_prices=True) để dialog sửa
    # sản phẩm mở lên có sẵn giá của mọi bảng — trang này chỉ vài trăm dòng/lần.
    page_product_ids = [e["product_id"] for e in page if e["kind"] == "product"]
    serialized_by_id: dict[int, dict] = {}
    if page_product_ids:
        serialized_by_id = {
            p["id"]: p
            for p in _serialize_products_fast(
                Product.objects.filter(id__in=page_product_ids), include_prices=True
            )
        }

    entries: list[dict] = []
    for entry in page:
        if entry["kind"] == "group":
            entries.append(
                {
                    "kind": "group",
                    "id": entry["id"],
                    "code": entry["code"],
                    "name": entry["name"],
                    "variant_count": entry["variant_count"],
                    "price_min": entry["price_min"],
                    "price_max": entry["price_max"],
                }
            )
        else:
            product = serialized_by_id.get(entry["product_id"])
            if product is not None:
                entries.append({"kind": "product", "product": product})

    return Response(
        {"entries": entries, "has_next": has_next, "total": len(merged)},
        status=status.HTTP_200_OK,
    )
