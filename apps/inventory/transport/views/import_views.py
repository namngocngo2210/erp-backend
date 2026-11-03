from __future__ import annotations

import logging

from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.upload_guards import validate_xlsx_upload
from apps.inventory.infrastructure.excel_lines import (
    build_lines_import_template,
    parse_lines_from_workbook,
)
from apps.users.permissions import require_section

logger = logging.getLogger(__name__)

_XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


# Upload .xlsx (multipart "file"), parse + resolve SKU -> {lines, errors}.
@extend_schema(
    request=inline_serializer(
        "ParseImportLinesRequest",
        fields={"file": drf_serializers.FileField()},
    ),
    responses={
        200: inline_serializer(
            "ParseImportLinesResponse",
            fields={
                "lines": drf_serializers.ListField(child=drf_serializers.DictField()),
                "errors": drf_serializers.ListField(child=drf_serializers.DictField()),
            },
        ),
        400: OpenApiResponse(description="Thiếu file hoặc file không đọc được"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("stock-documents", "returns")])
def parse_import_lines(request):
    """Parse an uploaded .xlsx file and resolve SKUs to product lines.

    Multipart body: ``file`` — the .xlsx workbook.

    Returns:
        200 ``{"lines": [...], "errors": [...]}``
        400 if file missing or unreadable.
    """
    upload = request.FILES.get("file")
    if upload is None:
        return Response({"detail": "Thiếu file Excel."}, status=400)
    # Trần 5MB + đuôi + magic ZIP trước khi đưa vào openpyxl (audit 2026-09-23 L4).
    upload_error = validate_xlsx_upload(upload)
    if upload_error:
        return Response({"detail": upload_error}, status=400)

    try:
        result = parse_lines_from_workbook(upload)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=400)
    except Exception:
        logger.exception("Failed to parse uploaded Excel for inventory import")
        return Response({"detail": "Không thể đọc file Excel."}, status=400)

    return Response(result, status=200)


# Trả về file .xlsx nhị phân (template) — không phải JSON nên loại khỏi schema.
@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock-documents", "returns")])
@never_cache
def download_lines_template(request):
    """Download a blank .xlsx template for inventory line import."""
    file_bytes = build_lines_import_template()
    response = HttpResponse(file_bytes, content_type=_XLSX_CONTENT_TYPE)
    response["Content-Disposition"] = (
        'attachment; filename="inventory-import-template.xlsx"'
    )
    return response
