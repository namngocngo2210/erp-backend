from __future__ import annotations

from datetime import date, datetime

from django.utils import timezone
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import parse_required_pagination
from apps.inventory.application.services.stock_as_of_service import StockAsOfService
from apps.inventory.application.services.stock_query_service import StockQueryService
from apps.inventory.application.services.stock_reclass_service import StockReclassService
from apps.inventory.transport.serializers.stock_serializers import (
    ReclassInputSerializer,
    StockAvailabilityRowSerializer,
    StockItemSerializer,
    StockMovementSerializer,
)
from apps.users.permissions import require_permission, require_section
from apps.users.rbac import resolve_effective_permissions

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200

# Trần dòng cho chế độ xem theo mốc. Ở chế độ này không LIMIT/OFFSET ở DB được
# (số tồn tại mốc không nằm trong bảng), phải kéo cả tập đã lọc lên Python — nên
# cần một trần cứng. Cùng con số với `exports_definitions._STOCK_MAX_ROWS`; cố ý
# khai riêng thay vì import để `transport` không phụ thuộc ngược vào tầng export.
_AS_OF_MAX_ROWS = 20000

# Trần số mã mỗi lần hỏi tồn ở form đơn. Đơn thật chỉ vài dòng hàng; trần này chỉ
# để một client lỗi không biến endpoint thành đường quét cả bảng sản phẩm.
_AVAILABILITY_MAX_SKUS = 50


class _InvalidAsOf(ValueError):
    """Tham số `as_of` sai định dạng hoặc ở tương lai."""


def _parse_as_of(raw: str) -> date | None:
    """Chuẩn hoá `as_of`; None = không lọc theo mốc.

    `strptime` chứ không `date.fromisoformat`: giữ đúng độ chặt của `_valid_date`
    ở `flow_history_views`, vốn chấp nhận cả thành phần không đệm số 0.

    Mốc = HÔM NAY trả về None có chủ đích: tồn cuối ngày hôm nay chính là tồn
    hiện tại, khỏi tốn một vòng trừ ngược. Chuỗi ngày vẫn được echo lại nguyên
    vẹn cho client (xem `list_stock`).
    """
    if not raw:
        return None
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise _InvalidAsOf("as_of phải dạng YYYY-MM-DD.") from exc
    today = timezone.localdate()
    if parsed > today:
        raise _InvalidAsOf("as_of không được ở tương lai.")
    return None if parsed == today else parsed


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# Danh sách tồn kho phân trang {items,total,limit,offset}.
@extend_schema(
    responses={
        200: inline_serializer(
            "StockItemListResponse",
            fields={
                "items": StockItemSerializer(many=True),
                "total": drf_serializers.IntegerField(),
                "limit": drf_serializers.IntegerField(),
                "offset": drf_serializers.IntegerField(),
                "as_of": drf_serializers.CharField(allow_null=True),
            },
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def list_stock(request):
    pagination, error_response = parse_required_pagination(
        request, max_limit=_MAX_LIMIT, default_limit=_DEFAULT_LIMIT
    )
    if error_response is not None:
        return error_response
    raw_as_of = request.query_params.get("as_of", "")
    try:
        as_of = _parse_as_of(raw_as_of)
    except _InvalidAsOf as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    warehouse_id = _int_or_none(request.query_params.get("warehouse_id"))
    search = request.query_params.get("search", "").strip()
    state = request.query_params.get("state") or None
    qs = StockQueryService.list_stock(
        warehouse_id=warehouse_id,
        search=search,
        state=state,
    )
    offset, limit = pagination["offset"], pagination["limit"]
    if as_of is None:
        total = qs.count()
        items = list(qs[offset : offset + limit])
        run_rates = StockQueryService.sales_run_rates(
            {(item.warehouse_id, item.product_id) for item in items}
        )
    else:
        # Chế độ mốc: số tồn không nằm trong bảng nên phải tính xong cả tập đã
        # lọc rồi mới cắt trang. Thứ tự của `list_stock` được giữ nguyên qua
        # `apply`, nên trang vẫn ổn định.
        #
        # Không kho, không tìm kiếm = tập lọc bằng cả bảng StockItem. `list(qs)`
        # bên dưới sẽ vật chất hoá tới hàng chục nghìn instance (kèm
        # select_related) cho một request có thể chỉ xin `limit=1` — trần
        # `_AS_OF_MAX_ROWS` chặn số DÒNG chứ không chặn chi phí materialize này.
        # Giao diện luôn gửi `warehouse_id` nên guard gần như miễn phí với người
        # dùng thật.
        if not warehouse_id and not search:
            return Response(
                {"detail": "Xem theo ngày cần chọn kho hoặc lọc mã."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if qs.count() > _AS_OF_MAX_ROWS:
            return Response(
                {
                    "detail": (
                        f"Kho có hơn {_AS_OF_MAX_ROWS} dòng tồn — thu hẹp bằng ô "
                        "tìm kiếm rồi xem lại theo ngày."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        rows = StockAsOfService.apply(list(qs), as_of, warehouse_id=warehouse_id)
        total = len(rows)
        items = rows[offset : offset + limit]
        run_rates = StockQueryService.sales_run_rates(
            {(item.warehouse_id, item.product_id) for item in items},
            as_of_end=StockAsOfService.end_bound(as_of),
        )
    return Response(
        {
            "items": StockItemSerializer(
                items, many=True, context={"run_rates": run_rates}
            ).data,
            "total": total,
            "limit": limit,
            "offset": offset,
            # Echo NGUYÊN chuỗi client gửi (đã qua validate) chứ không phải `as_of`
            # đã chuẩn hoá: mốc = hôm nay được quy về None ở trên, client vẫn cần
            # biết yêu cầu của mình đã được chấp nhận.
            "as_of": raw_as_of or None,
        },
        status=status.HTTP_200_OK,
    )


# Tồn BÁN ĐƯỢC theo mã, tại MỘT kho — cho khối "tồn kho" của form đơn hàng.
#
# Cố ý KHÔNG gác bằng `stock:view` như các endpoint kho khác: nhóm tạo đơn nhưng
# không được vào màn hình Tồn kho (đo trên prod: role Phòng Ads) vẫn phải thấy
# được số tồn của đúng những mã họ đang bán. Endpoint chỉ trả tồn bán được của
# danh sách mã client hỏi, không liệt kê được cả kho, nên `orders:view` là mức
# quyền vừa đủ.
@extend_schema(
    parameters=[
        OpenApiParameter(name="warehouse_id", type=int, required=True, description="Id kho xuất"),
        OpenApiParameter(
            name="skus",
            type=str,
            required=True,
            description=f"Danh sách SKU, phân tách bằng dấu phẩy (tối đa {_AVAILABILITY_MAX_SKUS}).",
        ),
    ],
    responses={
        200: inline_serializer(
            "StockAvailabilityResponse",
            fields={
                "warehouse_id": drf_serializers.IntegerField(),
                "items": StockAvailabilityRowSerializer(many=True),
            },
        ),
        400: OpenApiResponse(description="Thiếu warehouse_id hoặc gửi quá nhiều mã"),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_permission("orders:view")])
@never_cache
def stock_availability(request):
    warehouse_id = _int_or_none(request.query_params.get("warehouse_id"))
    if not warehouse_id:
        return Response(
            {"detail": "warehouse_id là bắt buộc."}, status=status.HTTP_400_BAD_REQUEST
        )
    raw_skus = request.query_params.get("skus", "")
    # dict.fromkeys: khử trùng lặp mà GIỮ thứ tự dòng hàng trên form.
    skus = list(dict.fromkeys(part.strip() for part in raw_skus.split(",") if part.strip()))
    if len(skus) > _AVAILABILITY_MAX_SKUS:
        return Response(
            {"detail": f"Tối đa {_AVAILABILITY_MAX_SKUS} mã mỗi lần."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    rows = StockQueryService.availability(warehouse_id=warehouse_id, skus=skus)
    return Response(
        {"warehouse_id": warehouse_id, "items": rows},
        status=status.HTTP_200_OK,
    )


# Phân loại lại tồn (good/defect...) — trả {detail:"ok"} khi thành công.
@extend_schema(
    request=ReclassInputSerializer,
    responses={
        200: inline_serializer(
            "ReclassStockResponse",
            fields={"detail": drf_serializers.CharField()},
        ),
        400: OpenApiResponse(description="Dữ liệu không hợp lệ hoặc không đủ tồn"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("stock:edit")])
@never_cache
def reclassify_stock(request):
    serializer = ReclassInputSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    data = serializer.validated_data
    # send_back/scrap xuất hàng khỏi tồn vĩnh viễn — cùng bản chất với duyệt
    # phiếu xuất, không được bypass cổng kế toán (stock-documents:approve).
    if data["action"] != "to_sellable":
        eff = resolve_effective_permissions(request.user)
        if not eff.can("stock-documents:approve"):
            return Response(
                {"detail": "Xuất huỷ/gửi lại cần quyền duyệt phiếu kho."},
                status=status.HTTP_403_FORBIDDEN,
            )
    try:
        StockReclassService.reclassify(
            warehouse_id=data["warehouse_id"],
            product_id=data["product_id"],
            quantity=data["quantity"],
            from_state=data["from_state"],
            action=data["action"],
            note=data["note"],
            user_id=request.user.id,
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response({"detail": "ok"}, status=status.HTTP_200_OK)


# Danh sách biến động tồn (movements) phân trang {items,total,limit,offset}.
@extend_schema(
    responses={
        200: inline_serializer(
            "StockMovementListResponse",
            fields={
                "items": StockMovementSerializer(many=True),
                "total": drf_serializers.IntegerField(),
                "limit": drf_serializers.IntegerField(),
                "offset": drf_serializers.IntegerField(),
            },
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def list_movements(request):
    pagination, error_response = parse_required_pagination(
        request, max_limit=_MAX_LIMIT, default_limit=_DEFAULT_LIMIT
    )
    if error_response is not None:
        return error_response
    try:
        as_of = _parse_as_of(request.query_params.get("as_of", ""))
    except _InvalidAsOf as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    qs = StockQueryService.list_movements(
        warehouse_id=_int_or_none(request.query_params.get("warehouse_id")),
        product_id=_int_or_none(request.query_params.get("product_id")),
        as_of=as_of,
    )
    total = qs.count()
    items = list(qs[pagination["offset"] : pagination["offset"] + pagination["limit"]])
    reference_labels = StockQueryService.resolve_reference_labels(items)
    return Response(
        {
            "items": StockMovementSerializer(
                items, many=True, context={"reference_labels": reference_labels}
            ).data,
            "total": total,
            "limit": pagination["limit"],
            "offset": pagination["offset"],
        },
        status=status.HTTP_200_OK,
    )
