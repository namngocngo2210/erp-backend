"""REST API Best Express cho FE/tooling (hiện trong Swagger /api/docs).

Transport mỏng trên application services: payload gửi Best passthrough nguyên
bản theo tài liệu V3 (không re-validate nghiệp vụ), lỗi Best trả nguyên văn
HTTP 400. Đọc gate `shipments:view`, mutation gate `shipments:edit`.
"""

from django.apps import apps as django_apps
from django.db.models import Q
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.best_express.application.services import shipment_service, tracking_service
from apps.best_express.exceptions import BestExpressError
from apps.best_express.models import BestExpressAccount, BestShipment
from apps.best_express.transport.serializers import (
    BestAccountSerializer,
    BestShipmentSerializer,
    CodesRequestSerializer,
    CreateShipmentRequestSerializer,
    EstimateFeeRequestSerializer,
    UpdateAddressRequestSerializer,
    UpdateShipmentRequestSerializer,
)
from apps.users.permissions import require_permission

LIST_MAX_PAGE_SIZE = 100


def _error(exc: BestExpressError) -> Response:
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


def _resolve_account(account_id) -> BestExpressAccount:
    qs = BestExpressAccount.objects.filter(is_active=True)
    if account_id:
        account = qs.filter(pk=account_id).first()
        if account is None:
            raise BestExpressError(
                f"Không tìm thấy account Best id={account_id} (hoặc đã tắt)."
            )
        return account
    accounts = list(qs[:2])
    if not accounts:
        raise BestExpressError(
            "Chưa có account Best Express active — tạo/kích hoạt account trong "
            "admin/UI (bảng BestExpressAccount)."
        )
    if len(accounts) > 1:
        raise BestExpressError("Có nhiều account Best active — cần truyền account_id.")
    return accounts[0]


def _scoped_shipments(user):
    """Vận đơn Best trong PHẠM VI DỮ LIỆU của user (section `shipments`).

    Trang Vận đơn hiện có đã lọc theo scope kênh/NV (`_apply_user_channel_scope`); các
    endpoint Best phải theo cùng chuẩn, nếu không NV scope OWN xem/huỷ/in được phiếu gửi
    của MỌI vận đơn công ty — id là số nguyên tuần tự nên đoán được, và phiếu gửi chứa
    tên/SĐT/địa chỉ người nhận (audit 2026-07-14 vòng 2).

    Vận đơn chưa gắn đơn (`order` null) chỉ người có scope ALL mới thấy — không có gì để
    quy chiếu chủ sở hữu.
    """
    from apps.users.scoping import OWN_SCOPE_SENTINEL, resolved_channel_codes

    qs = BestShipment.objects.select_related("account")
    scope_codes = resolved_channel_codes(user, "shipments")
    if scope_codes is None:
        return qs
    # Vận đơn chưa gắn đơn: quy chiếu theo NGƯỜI TẠO. Không có nhánh này thì scope hẹp
    # tạo xong là mất dấu vận đơn của chính mình — không list/huỷ/in phiếu được (audit
    # 2026-07-14 vòng 3).
    mine = Q(order__isnull=True, created_by_id=user.id)
    if scope_codes is OWN_SCOPE_SENTINEL:
        # Cùng định nghĩa "của tôi" với danh sách đơn: Order.staff (repositories.py).
        return qs.filter(Q(order__staff_id=user.id) | mine)
    if not scope_codes:
        return qs.filter(mine)
    return qs.filter(Q(order__channel__code__in=scope_codes) | mine)


def _scoped_orders(user):
    """Đơn mà user được phép GẮN vận đơn Best vào — cùng chuẩn scope với _scoped_shipments."""
    from apps.users.scoping import OWN_SCOPE_SENTINEL, resolved_channel_codes

    order_model = django_apps.get_model("orders", "Order")
    qs = order_model.objects.filter(deleted_at__isnull=True)
    scope_codes = resolved_channel_codes(user, "shipments")
    if scope_codes is None:
        return qs
    if scope_codes is OWN_SCOPE_SENTINEL:
        return qs.filter(staff_id=user.id)
    if not scope_codes:
        return qs.none()
    return qs.filter(channel__code__in=scope_codes)


def _get_shipment(request, shipment_id: int) -> BestShipment | None:
    return _scoped_shipments(request.user).filter(pk=shipment_id).first()


def _assert_codes_in_scope(request, codes: list[str]) -> None:
    """Chặn tra cứu/in phiếu gửi bằng mã vận đơn ngoài phạm vi của user.

    Scope ALL (admin/ops toàn quyền) không bị chặn: họ xem được mọi vận đơn nên vẫn phải
    tra được mã CHƯA có bản ghi nội bộ (vd vận đơn tạo từ hệ thống khác). Scope hẹp thì
    mã phải thuộc phạm vi — phiếu gửi chứa tên/SĐT/địa chỉ người nhận.
    """
    from apps.users.scoping import resolved_channel_codes

    if resolved_channel_codes(request.user, "shipments") is None:
        return

    allowed = set(
        _scoped_shipments(request.user)
        .filter(Q(code_best__in=codes) | Q(merchant_order_code__in=codes))
        .values_list("code_best", flat=True)
    ) | set(
        _scoped_shipments(request.user)
        .filter(Q(code_best__in=codes) | Q(merchant_order_code__in=codes))
        .values_list("merchant_order_code", flat=True)
    )
    unknown = [code for code in codes if code not in allowed]
    if unknown:
        raise BestExpressError(
            "Không có quyền với vận đơn: " + ", ".join(sorted(unknown)[:10])
        )


@extend_schema(
    operation_id="best_express_accounts_list",
    responses={200: BestAccountSerializer(many=True)},
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_permission("shipments:view")])
def list_best_accounts(request):
    accounts = BestExpressAccount.objects.order_by("id")
    return Response(BestAccountSerializer(accounts, many=True).data)


@extend_schema(
    operation_id="best_express_estimate_fee",
    request=EstimateFeeRequestSerializer,
    responses={200: OpenApiResponse(description="Kết quả EstimateFee nguyên bản từ Best")},
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("shipments:view")])
def estimate_best_fee(request):
    serializer = EstimateFeeRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        account = _resolve_account(serializer.validated_data.get("account_id"))
        fee = tracking_service.estimate_fee(account, serializer.validated_data["params"])
    except BestExpressError as exc:
        return _error(exc)
    return Response(fee)


@extend_schema(
    methods=["GET"],
    operation_id="best_express_shipments_list",
    responses={
        200: inline_serializer(
            "BestShipmentListResponse",
            fields={
                "items": BestShipmentSerializer(many=True),
                "total": drf_serializers.IntegerField(),
            },
        ),
    },
)
@extend_schema(
    methods=["POST"],
    operation_id="best_express_shipments_create",
    request=CreateShipmentRequestSerializer,
    responses={201: BestShipmentSerializer},
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_permission("shipments:view")])
def list_create_best_shipments(request):
    if request.method == "GET":
        return _list_shipments(request)

    # Mutation gate chặt hơn GET: tạo vận đơn thật phía Best.
    if not require_permission("shipments:edit")().has_permission(request, None):
        return Response(
            {"detail": "Bạn không có quyền tạo vận đơn Best."},
            status=status.HTTP_403_FORBIDDEN,
        )
    serializer = CreateShipmentRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    order = None
    order_id = serializer.validated_data.get("order_id")
    if order_id:
        # Đơn phải NẰM TRONG scope của người tạo: không chặn thì NV scope OWN/CHANNEL gắn
        # được vận đơn Best vào đơn của kênh khác — order detail của nạn nhân mọc thêm vận
        # đơn lạ (write-IDOR). Các endpoint Best khác đều đã scope, riêng chỗ này sót
        # (audit 2026-07-14 vòng 3).
        order = _scoped_orders(request.user).filter(pk=order_id).first()
        if order is None:
            return Response(
                {"detail": f"Không tìm thấy đơn hàng id={order_id}."},
                status=status.HTTP_404_NOT_FOUND,
            )
    try:
        account = _resolve_account(serializer.validated_data.get("account_id"))
        shipment = shipment_service.create_shipment(
            account,
            serializer.validated_data["payload"],
            order=order,
            created_by_id=request.user.id,
        )
    except BestExpressError as exc:
        return _error(exc)
    return Response(BestShipmentSerializer(shipment).data, status=status.HTTP_201_CREATED)


def _list_shipments(request) -> Response:
    qs = _scoped_shipments(request.user).order_by("-id")
    query = str(request.query_params.get("q") or "").strip()
    if query:
        qs = qs.filter(
            Q(merchant_order_code__icontains=query) | Q(code_best__icontains=query)
        )
    status_filter = str(request.query_params.get("status") or "").strip()
    if status_filter:
        qs = qs.filter(status=status_filter)
    try:
        page = max(1, int(request.query_params.get("page", 1)))
        page_size = min(LIST_MAX_PAGE_SIZE, max(1, int(request.query_params.get("page_size", 20))))
    except (TypeError, ValueError):
        page, page_size = 1, 20
    total = qs.count()
    start = (page - 1) * page_size
    items = BestShipmentSerializer(qs[start : start + page_size], many=True).data
    return Response({"items": items, "total": total})


@extend_schema(
    operation_id="best_express_shipment_update",
    request=UpdateShipmentRequestSerializer,
    responses={200: BestShipmentSerializer, 404: OpenApiResponse(description="Không tìm thấy vận đơn")},
)
@api_view(["PATCH"])
@permission_classes([IsAuthenticated, require_permission("shipments:edit")])
def update_best_shipment(request, shipment_id: int):
    shipment = _get_shipment(request, shipment_id)
    if shipment is None:
        return Response({"detail": "Không tìm thấy vận đơn."}, status=status.HTTP_404_NOT_FOUND)
    serializer = UpdateShipmentRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        shipment = shipment_service.update_shipment(
            shipment, serializer.validated_data["changes"]
        )
    except BestExpressError as exc:
        return _error(exc)
    return Response(BestShipmentSerializer(shipment).data)


@extend_schema(
    operation_id="best_express_shipment_cancel",
    request=None,
    responses={200: BestShipmentSerializer, 404: OpenApiResponse(description="Không tìm thấy vận đơn")},
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("shipments:edit")])
def cancel_best_shipment(request, shipment_id: int):
    shipment = _get_shipment(request, shipment_id)
    if shipment is None:
        return Response({"detail": "Không tìm thấy vận đơn."}, status=status.HTTP_404_NOT_FOUND)
    try:
        shipment = shipment_service.cancel_shipment(shipment)
    except BestExpressError as exc:
        return _error(exc)
    return Response(BestShipmentSerializer(shipment).data)


@extend_schema(
    operation_id="best_express_shipment_update_address",
    request=UpdateAddressRequestSerializer,
    responses={200: OpenApiResponse(description="Kết quả UpdateAddress nguyên bản từ Best")},
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("shipments:edit")])
def update_best_shipment_address(request, shipment_id: int):
    shipment = _get_shipment(request, shipment_id)
    if shipment is None:
        return Response({"detail": "Không tìm thấy vận đơn."}, status=status.HTTP_404_NOT_FOUND)
    serializer = UpdateAddressRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        data = shipment_service.update_shipment_address(
            shipment, serializer.validated_data["address"]
        )
    except BestExpressError as exc:
        return _error(exc)
    return Response(data)


@extend_schema(
    operation_id="best_express_trace",
    request=CodesRequestSerializer,
    responses={200: OpenApiResponse(description="Hành trình vận đơn nguyên bản từ Best (đồng thời sync vào BestShipment)")},
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("shipments:view")])
def trace_best_shipments(request):
    serializer = CodesRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        _assert_codes_in_scope(request, serializer.validated_data["codes"])
        account = _resolve_account(serializer.validated_data.get("account_id"))
        results = tracking_service.trace(account, serializer.validated_data["codes"])
    except BestExpressError as exc:
        return _error(exc)
    return Response({"items": results})


@extend_schema(
    operation_id="best_express_print_waybill",
    request=CodesRequestSerializer,
    responses={
        200: inline_serializer(
            "BestPrintWaybillResponse",
            fields={"urls": drf_serializers.ListField(child=drf_serializers.CharField())},
        )
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("shipments:view")])
def print_best_waybills(request):
    serializer = CodesRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        _assert_codes_in_scope(request, serializer.validated_data["codes"])
        account = _resolve_account(serializer.validated_data.get("account_id"))
        urls = tracking_service.print_waybills(account, serializer.validated_data["codes"])
    except BestExpressError as exc:
        return _error(exc)
    return Response({"urls": urls})
