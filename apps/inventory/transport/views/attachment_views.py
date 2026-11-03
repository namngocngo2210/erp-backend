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
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.inventory.application.services.stock_document_attachment_service import (
    AttachmentValidationError,
    StockDocumentAttachmentService,
)
from apps.inventory.infrastructure import document_storage
from apps.inventory.models import StockDocument, StockDocumentAttachment
from apps.inventory.transport.serializers.document_serializers import (
    StockDocumentAttachmentSerializer,
)
from apps.users.permissions import require_permission, require_section

_ALLOWED_SERVE_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}


# Upload nhiều file đính kèm (multipart) cho 1 phiếu kho; trả về danh sách attachment đã tạo.
@extend_schema(
    request=inline_serializer(
        "StockDocumentAttachmentUploadRequest",
        fields={
            "files": drf_serializers.ListField(
                child=drf_serializers.FileField(), required=False
            ),
            "file": drf_serializers.FileField(required=False),
        },
    ),
    responses={
        201: StockDocumentAttachmentSerializer(many=True),
        400: OpenApiResponse(description="Thiếu file hoặc file không hợp lệ"),
        404: OpenApiResponse(description="Không tìm thấy phiếu"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("stock-documents:edit")])
@parser_classes([MultiPartParser, FormParser])
@never_cache
def upload_attachments_view(request, document_id):
    if not StockDocument.objects.filter(pk=document_id).exists():
        return Response(
            {"detail": "Không tìm thấy phiếu."}, status=status.HTTP_404_NOT_FOUND
        )

    files = request.FILES.getlist("files") or request.FILES.getlist("file")
    if not files:
        return Response(
            {"detail": "Thiếu file đính kèm."}, status=status.HTTP_400_BAD_REQUEST
        )

    created = []
    for upload in files:
        try:
            att = StockDocumentAttachmentService.add_attachment(
                document_id=document_id,
                data=upload.read(),
                content_type=upload.content_type or "",
                original_name=upload.name or "",
                user_id=request.user.id,
            )
        except AttachmentValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        created.append(att)

    return Response(
        StockDocumentAttachmentSerializer(created, many=True).data,
        status=status.HTTP_201_CREATED,
    )


# Trả về nội dung file nhị phân (ảnh/PDF) — không phải JSON nên loại khỏi schema.
@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock-documents")])
@never_cache
def attachment_file_view(request, attachment_id):
    att = StockDocumentAttachment.objects.filter(pk=attachment_id).first()
    if not att or not att.file_key:
        return Response(
            {"detail": "Không tìm thấy file."}, status=status.HTTP_404_NOT_FOUND
        )

    data, content_type = document_storage.get_object_bytes(att.file_key)
    safe_type = (
        content_type
        if content_type in _ALLOWED_SERVE_TYPES
        else "application/octet-stream"
    )

    resp = HttpResponse(data, content_type=safe_type)
    disposition = "attachment" if request.query_params.get("download") else "inline"
    filename = (att.original_name or "file").replace('"', "")
    resp["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Security-Policy"] = "default-src 'none'; img-src 'self'"
    return resp


@extend_schema(
    responses={
        204: OpenApiResponse(description="Đã xoá file đính kèm"),
        404: OpenApiResponse(description="Không tìm thấy file"),
    },
)
@api_view(["DELETE"])
@permission_classes([IsAuthenticated, require_permission("stock-documents:edit")])
@never_cache
def delete_attachment_view(request, attachment_id):
    try:
        StockDocumentAttachmentService.delete_attachment(attachment_id=attachment_id)
    except StockDocumentAttachment.DoesNotExist:
        return Response(
            {"detail": "Không tìm thấy file."}, status=status.HTTP_404_NOT_FOUND
        )
    return Response(status=status.HTTP_204_NO_CONTENT)
