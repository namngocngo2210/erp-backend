import logging

from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import (
    ORDER_CREATE_CODES,
    ORDER_SECTIONS,
    require_permission,
    require_section,
)

from apps.orders.import_excel import (
    build_orders_import_template,
    import_orders_from_koc_text,
    import_orders_from_text,
    import_orders_from_workbook,
)
from apps.orders.models import OrderSource

logger = logging.getLogger(__name__)

_VALID_ORDER_SOURCES = set(OrderSource.values)


def _import_result_serializer(name: str):
    """Kết quả import (Excel/text) — đếm dòng + danh sách lỗi từng dòng + cảnh báo (vd cột Nhân viên bị bỏ qua)."""
    return inline_serializer(
        name,
        fields={
            "total_rows": drf_serializers.IntegerField(),
            "success_count": drf_serializers.IntegerField(),
            "failed_count": drf_serializers.IntegerField(),
            "failures": drf_serializers.ListField(child=drf_serializers.DictField()),
            "warnings": drf_serializers.ListField(child=drf_serializers.CharField()),
        },
    )


def _resolve_landing_order_source(request):
    """Lấy order_source do client gửi cho import Landing; mặc định 'landing' nếu thiếu/không hợp lệ."""
    value = str(request.data.get("order_source") or "").strip().lower()
    if value in _VALID_ORDER_SOURCES:
        return value
    return OrderSource.LANDING.value


# Tải file .xlsx (binary), không phải JSON-contract → loại khỏi schema.
@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS)])
@never_cache
def download_orders_import_template(request):
    workbook_bytes = build_orders_import_template()
    response = HttpResponse(
        workbook_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="orders-import-template.xlsx"'
    return response


@extend_schema(
    request=inline_serializer(
        "ImportOrdersExcelRequest",
        fields={"file": drf_serializers.FileField()},
    ),
    responses={
        200: _import_result_serializer("ImportOrdersExcelResponse"),
        400: OpenApiResponse(description="Thiếu file hoặc file Excel không xử lý được"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission(*ORDER_CREATE_CODES)])
def import_orders_excel(request):
    upload = request.FILES.get("file")
    if upload is None:
        return Response({"detail": "Thiếu file Excel."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        result = import_orders_from_workbook(file_obj=upload, actor=request.user)
    except Exception:
        logger.exception("Orders Excel import failed unexpectedly.")
        return Response({"detail": "Không thể xử lý file Excel."}, status=status.HTTP_400_BAD_REQUEST)

    return Response(result, status=status.HTTP_200_OK)


# Tải file .xlsx (binary), không phải JSON-contract → loại khỏi schema.
@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("landing-orders")])
@never_cache
def download_landing_orders_import_template(request):
    """Landing import dùng cùng template 17-cột; backend tự inject order_source='landing'."""
    workbook_bytes = build_orders_import_template()
    response = HttpResponse(
        workbook_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="landing-orders-import-template.xlsx"'
    return response


@extend_schema(
    request=inline_serializer(
        "ImportLandingOrdersExcelRequest",
        fields={
            "file": drf_serializers.FileField(),
            "order_source": drf_serializers.CharField(required=False),
        },
    ),
    responses={
        200: _import_result_serializer("ImportLandingOrdersExcelResponse"),
        400: OpenApiResponse(description="Thiếu file hoặc file Excel không xử lý được"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("landing-orders:create")])
def import_landing_orders_excel(request):
    """Landing import: gắn order_source (client chọn, mặc định 'landing') + show_orders=False."""
    upload = request.FILES.get("file")
    if upload is None:
        return Response({"detail": "Thiếu file Excel."}, status=status.HTTP_400_BAD_REQUEST)

    order_source = _resolve_landing_order_source(request)
    try:
        result = import_orders_from_workbook(
            file_obj=upload,
            actor=request.user,
            extra_payload={"order_source": order_source, "show_orders": False},
        )
    except Exception:
        logger.exception("Landing orders Excel import failed unexpectedly.")
        return Response({"detail": "Không thể xử lý file Excel."}, status=status.HTTP_400_BAD_REQUEST)

    return Response(result, status=status.HTTP_200_OK)


@extend_schema(
    request=inline_serializer(
        "ImportLandingOrdersTextRequest",
        fields={
            "text": drf_serializers.CharField(),
            "order_source": drf_serializers.CharField(required=False),
        },
    ),
    responses={
        200: _import_result_serializer("ImportLandingOrdersTextResponse"),
        400: OpenApiResponse(description="Thiếu dữ liệu văn bản hoặc không xử lý được"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("landing-orders:create")])
def import_landing_orders_text(request):
    text_data = request.data.get("text")
    if not text_data or not isinstance(text_data, str):
        return Response({"detail": "Thiếu dữ liệu văn bản."}, status=status.HTTP_400_BAD_REQUEST)

    order_source = _resolve_landing_order_source(request)
    try:
        result = import_orders_from_text(
            text_data=text_data,
            actor=request.user,
            extra_payload={"order_source": order_source, "show_orders": False},
        )
    except Exception:
        logger.exception("Landing orders text import failed unexpectedly.")
        return Response({"detail": "Không thể xử lý dữ liệu văn bản."}, status=status.HTTP_400_BAD_REQUEST)

    return Response(result, status=status.HTTP_200_OK)


@extend_schema(
    request=inline_serializer(
        "ImportOrdersTextRequest",
        fields={"text": drf_serializers.CharField()},
    ),
    responses={
        200: _import_result_serializer("ImportOrdersTextResponse"),
        400: OpenApiResponse(description="Thiếu dữ liệu văn bản hoặc không xử lý được"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission(*ORDER_CREATE_CODES)])
def import_orders_text(request):
    text_data = request.data.get("text")
    if not text_data or not isinstance(text_data, str):
        return Response({"detail": "Thiếu dữ liệu văn bản."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        result = import_orders_from_text(text_data=text_data, actor=request.user)
    except Exception:
        logger.exception("Orders text import failed unexpectedly.")
        return Response({"detail": "Không thể xử lý dữ liệu văn bản."}, status=status.HTTP_400_BAD_REQUEST)

    return Response(result, status=status.HTTP_200_OK)


@extend_schema(
    request=inline_serializer(
        "ImportKocOrdersTextRequest",
        fields={"text": drf_serializers.CharField()},
    ),
    responses={
        200: _import_result_serializer("ImportKocOrdersTextResponse"),
        400: OpenApiResponse(description="Thiếu dữ liệu văn bản hoặc không xử lý được"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("koc-orders:create")])
def import_koc_orders_text(request):
    """Import đơn KOC dán text theo layout 19 cột riêng (xem KOC_IMPORT_PAYLOAD_FIELDS)."""
    text_data = request.data.get("text")
    if not text_data or not isinstance(text_data, str):
        return Response({"detail": "Thiếu dữ liệu văn bản."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        result = import_orders_from_koc_text(text_data=text_data, actor=request.user)
    except Exception:
        logger.exception("KOC orders text import failed unexpectedly.")
        return Response({"detail": "Không thể xử lý dữ liệu văn bản."}, status=status.HTTP_400_BAD_REQUEST)

    return Response(result, status=status.HTTP_200_OK)
