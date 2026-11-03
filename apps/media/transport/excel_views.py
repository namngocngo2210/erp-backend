"""3 route Excel của Media: tải file mẫu, import hàng loạt, xuất danh sách.

Khác các view khác trong module: đây KHÔNG phải proxy 1:1 sang ads-crm (ads-crm
không có API import, còn export của họ là CSV) — desi-order tự đọc/ghi xlsx rồi
gọi `POST /contents` từng dòng qua `MediaProxy`, xem `application/services/excel_service.py`.
"""

from __future__ import annotations

from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from rest_framework import status as http
from rest_framework.response import Response

from apps.media.application.services.access_policy import authorize
from apps.media.application.services.excel_service import (
    fetch_contents_for_export,
    import_contents,
)
from apps.media.domain.roles import Action
from apps.media.exceptions import MediaError
from apps.media.infrastructure.content_excel import (
    XLSX_CONTENT_TYPE,
    ExcelFormatError,
    build_contents_export,
    build_import_template,
)
from apps.media.transport import params as P
from apps.media.transport.api_views import _auth, _new_proxy

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def _xlsx_response(content: bytes, filename: str) -> HttpResponse:
    response = HttpResponse(content, content_type=XLSX_CONTENT_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@_auth(["GET"])
@never_cache
def contents_import_template(request):
    """File mẫu import (header + dòng ví dụ + sheet hướng dẫn)."""
    proxy = _new_proxy(request)
    authorize(proxy.cred, Action.READ)
    return _xlsx_response(build_import_template(), "mau-import-noi-dung.xlsx")


@_auth(["POST"])
def contents_import(request):
    """Nhận file .xlsx (multipart `file`) → tạo nội dung từng dòng trên ads-crm."""
    proxy = _new_proxy(request)
    authorize(proxy.cred, Action.CONTENT_CREATE)
    upload = request.FILES.get("file")
    if upload is None:
        raise MediaError("Thiếu file Excel.", status=http.HTTP_400_BAD_REQUEST, code="missing_file")
    if upload.size and upload.size > MAX_UPLOAD_BYTES:
        raise MediaError(
            "File quá lớn (tối đa 5MB).", status=http.HTTP_400_BAD_REQUEST, code="file_too_large"
        )
    try:
        result = import_contents(proxy, upload)
    except ExcelFormatError as exc:
        raise MediaError(str(exc), status=http.HTTP_400_BAD_REQUEST, code="invalid_excel") from exc
    return Response(result, status=http.HTTP_200_OK)


@_auth(["GET"])
@never_cache
def contents_export(request):
    """Xuất danh sách nội dung theo đúng bộ lọc đang xem trên UI ra .xlsx."""
    proxy = _new_proxy(request)
    contents = fetch_contents_for_export(proxy, P.pick(request.query_params, P.CONTENTS_LIST))
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    return _xlsx_response(build_contents_export(contents), f"noi-dung-media-{stamp}.xlsx")
