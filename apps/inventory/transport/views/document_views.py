from __future__ import annotations

from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import (
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
from apps.inventory.application.services.stock_document_service import (
    StockDocumentService,
)
from apps.inventory.infrastructure.stock_document_excel import (
    build_stock_document_workbook,
)
from apps.inventory.infrastructure.stock_excel import _slug
from apps.inventory.models import StockDocument
from apps.inventory.transport.serializers.document_serializers import (
    CreateStockDocumentSerializer,
    StockDocumentSerializer,
)
from apps.users.permissions import require_method_permission, require_permission

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
_XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


# GET: danh sách phiếu kho phân trang {items,total,limit,offset}.
@extend_schema(
    methods=["GET"],
    responses={
        200: inline_serializer(
            "StockDocumentListResponse",
            fields={
                "items": StockDocumentSerializer(many=True),
                "total": drf_serializers.IntegerField(),
                "limit": drf_serializers.IntegerField(),
                "offset": drf_serializers.IntegerField(),
            },
        ),
    },
)
# POST: tạo phiếu kho nháp từ payload CreateStockDocumentSerializer.
@extend_schema(
    methods=["POST"],
    request=CreateStockDocumentSerializer,
    responses={
        201: StockDocumentSerializer,
        400: OpenApiResponse(description="Dữ liệu phiếu không hợp lệ"),
    },
)
@api_view(["GET", "POST"])
@permission_classes([
    IsAuthenticated,
    require_method_permission(GET="stock-documents:view", POST="stock-documents:create"),
])
@never_cache
def list_create_documents(request):
    if request.method == "GET":
        pagination, error_response = parse_required_pagination(
            request, max_limit=_MAX_LIMIT, default_limit=_DEFAULT_LIMIT
        )
        if error_response is not None:
            return error_response

        qs = (
            StockDocument.objects.prefetch_related(
                "lines__product", "attachments__uploaded_by"
            )
            # submitted_by/approved_by: serializer đọc `.username` của cả hai —
            # thiếu select_related là +2 query mỗi phiếu trong list (audit
            # 2026-07-28 DB-11). Các chỗ refetch 1 phiếu bên dưới cùng khuôn.
            .select_related(
                "source_warehouse", "dest_warehouse", "submitted_by", "approved_by"
            )
            .order_by("-created_at")
        )

        doc_type = request.query_params.get("doc_type")
        if doc_type:
            qs = qs.filter(doc_type=doc_type)

        doc_status = request.query_params.get("status")
        if doc_status:
            qs = qs.filter(status=doc_status)

        total = qs.count()
        items = qs[pagination["offset"] : pagination["offset"] + pagination["limit"]]
        return Response(
            {
                "items": StockDocumentSerializer(items, many=True).data,
                "total": total,
                "limit": pagination["limit"],
                "offset": pagination["offset"],
            },
            status=status.HTTP_200_OK,
        )

    # POST — create draft document
    serializer = CreateStockDocumentSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data
    try:
        doc = StockDocumentService.create_document(
            doc_type=data["doc_type"],
            lines=list(data["lines"]),
            source_warehouse_id=data.get("source_warehouse_id"),
            dest_warehouse_id=data.get("dest_warehouse_id"),
            note=data.get("note", ""),
            user_id=request.user.id,
            created_at=data.get("created_at"),
        )
    except ValueError as exc:
        # Ngày chứng từ tương lai / không sinh được mã phiếu — lỗi dữ liệu người
        # dùng, cùng luật với nhánh PATCH bên dưới.
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    doc = (
        StockDocument.objects.prefetch_related(
            "lines__product", "attachments__uploaded_by"
        )
        .select_related("source_warehouse", "dest_warehouse")
        .get(pk=doc.pk)
    )
    return Response(StockDocumentSerializer(doc).data, status=status.HTTP_201_CREATED)


# GET: chi tiết 1 phiếu kho.
@extend_schema(
    methods=["GET"],
    responses={
        200: StockDocumentSerializer,
        404: OpenApiResponse(description="Không tìm thấy phiếu"),
    },
)
# PATCH: cập nhật phiếu nháp.
@extend_schema(
    methods=["PATCH"],
    request=CreateStockDocumentSerializer,
    responses={
        200: StockDocumentSerializer,
        400: OpenApiResponse(description="Dữ liệu phiếu không hợp lệ"),
        404: OpenApiResponse(description="Không tìm thấy phiếu"),
    },
)
@api_view(["GET", "PATCH"])
@permission_classes([
    IsAuthenticated,
    require_method_permission(GET="stock-documents:view", PATCH="stock-documents:edit"),
])
@never_cache
def document_detail(request, document_id):
    if request.method == "PATCH":
        serializer = CreateStockDocumentSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data
        try:
            doc = StockDocumentService.update_document(
                document_id=document_id,
                doc_type=data["doc_type"],
                lines=list(data["lines"]),
                source_warehouse_id=data.get("source_warehouse_id"),
                dest_warehouse_id=data.get("dest_warehouse_id"),
                note=data.get("note", ""),
                created_at=data.get("created_at"),
            )
        except StockDocument.DoesNotExist:
            return Response(
                {"detail": "Không tìm thấy phiếu."}, status=status.HTTP_404_NOT_FOUND
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        doc = (
            StockDocument.objects.prefetch_related(
                "lines__product", "attachments__uploaded_by"
            )
            .select_related(
                "source_warehouse", "dest_warehouse", "submitted_by", "approved_by"
            )
            .get(pk=doc.pk)
        )
        return Response(StockDocumentSerializer(doc).data, status=status.HTTP_200_OK)

    try:
        doc = (
            StockDocument.objects.prefetch_related(
                "lines__product", "attachments__uploaded_by"
            )
            .select_related(
                "source_warehouse", "dest_warehouse", "submitted_by", "approved_by"
            )
            .get(pk=document_id)
        )
    except StockDocument.DoesNotExist:
        return Response(
            {"detail": "Không tìm thấy phiếu."}, status=status.HTTP_404_NOT_FOUND
        )

    return Response(StockDocumentSerializer(doc).data, status=status.HTTP_200_OK)


# Gửi phiếu đi duyệt — không có body, trả phiếu sau cập nhật trạng thái.
@extend_schema(
    request=None,
    responses={
        200: StockDocumentSerializer,
        400: OpenApiResponse(description="Không thể gửi duyệt ở trạng thái hiện tại"),
        404: OpenApiResponse(description="Không tìm thấy phiếu"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("stock-documents:edit")])
@never_cache
def submit_document_view(request, document_id):
    try:
        doc = StockDocumentService.submit_for_approval(
            document_id=document_id, user_id=request.user.id
        )
    except StockDocument.DoesNotExist:
        return Response(
            {"detail": "Không tìm thấy phiếu."}, status=status.HTTP_404_NOT_FOUND
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    doc = (
        StockDocument.objects.prefetch_related(
            "lines__product", "attachments__uploaded_by"
        )
        .select_related("source_warehouse", "dest_warehouse")
        .get(pk=doc.pk)
    )
    return Response(StockDocumentSerializer(doc).data, status=status.HTTP_200_OK)


# Duyệt phiếu — không có body, trả phiếu đã duyệt.
@extend_schema(
    request=None,
    responses={
        200: StockDocumentSerializer,
        400: OpenApiResponse(description="Không thể duyệt ở trạng thái hiện tại"),
        404: OpenApiResponse(description="Không tìm thấy phiếu"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("stock-documents:approve")])
@never_cache
def approve_document_view(request, document_id):
    try:
        doc = StockDocumentService.approve_document(
            document_id=document_id, user_id=request.user.id
        )
    except StockDocument.DoesNotExist:
        return Response(
            {"detail": "Không tìm thấy phiếu."}, status=status.HTTP_404_NOT_FOUND
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    doc = (
        StockDocument.objects.prefetch_related(
            "lines__product", "attachments__uploaded_by"
        )
        .select_related("source_warehouse", "dest_warehouse")
        .get(pk=doc.pk)
    )
    return Response(StockDocumentSerializer(doc).data, status=status.HTTP_200_OK)


# Từ chối phiếu — body tuỳ chọn {reason}, trả phiếu đã từ chối.
@extend_schema(
    request=inline_serializer(
        "RejectStockDocumentRequest",
        fields={"reason": drf_serializers.CharField(required=False, allow_blank=True)},
    ),
    responses={
        200: StockDocumentSerializer,
        400: OpenApiResponse(description="Không thể từ chối ở trạng thái hiện tại"),
        404: OpenApiResponse(description="Không tìm thấy phiếu"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("stock-documents:approve")])
@never_cache
def reject_document_view(request, document_id):
    reason = str(request.data.get("reason", "") or "")
    try:
        doc = StockDocumentService.reject_document(
            document_id=document_id, user_id=request.user.id, reason=reason
        )
    except StockDocument.DoesNotExist:
        return Response(
            {"detail": "Không tìm thấy phiếu."}, status=status.HTTP_404_NOT_FOUND
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    doc = (
        StockDocument.objects.prefetch_related(
            "lines__product", "attachments__uploaded_by"
        )
        .select_related("source_warehouse", "dest_warehouse")
        .get(pk=doc.pk)
    )
    return Response(StockDocumentSerializer(doc).data, status=status.HTTP_200_OK)


# Huỷ phiếu — không có body, trả phiếu đã huỷ.
@extend_schema(
    request=None,
    responses={
        200: StockDocumentSerializer,
        400: OpenApiResponse(description="Không thể huỷ ở trạng thái hiện tại"),
        404: OpenApiResponse(description="Không tìm thấy phiếu"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("stock-documents:approve")])
@never_cache
def cancel_document_view(request, document_id):
    try:
        doc = StockDocumentService.cancel_document(
            document_id=document_id, user_id=request.user.id
        )
    except StockDocument.DoesNotExist:
        return Response(
            {"detail": "Không tìm thấy phiếu."}, status=status.HTTP_404_NOT_FOUND
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    doc = (
        StockDocument.objects.prefetch_related(
            "lines__product", "attachments__uploaded_by"
        )
        .select_related("source_warehouse", "dest_warehouse")
        .get(pk=doc.pk)
    )
    return Response(StockDocumentSerializer(doc).data, status=status.HTTP_200_OK)


# Trả file .xlsx nhị phân của MỘT phiếu — không phải JSON nên loại khỏi schema.
@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_permission("stock-documents:view")])
@never_cache
def document_export(request, document_id):
    """Xuất Excel cho một phiếu kho — cùng quyền xem phiếu, không cần quyền export
    riêng: người đã đọc được phiếu trên màn hình thì tải file của chính phiếu đó
    không lộ thêm dữ liệu nào."""
    try:
        doc = (
            StockDocument.objects.prefetch_related("lines__product")
            .select_related(
                "source_warehouse", "dest_warehouse", "submitted_by", "approved_by"
            )
            .get(pk=document_id)
        )
    except StockDocument.DoesNotExist:
        return Response(
            {"detail": "Không tìm thấy phiếu."}, status=status.HTTP_404_NOT_FOUND
        )

    response = HttpResponse(
        build_stock_document_workbook(doc), content_type=_XLSX_CONTENT_TYPE
    )
    response["Content-Disposition"] = (
        f'attachment; filename="phieu-kho-{_slug(doc.code)}.xlsx"'
    )
    return response
