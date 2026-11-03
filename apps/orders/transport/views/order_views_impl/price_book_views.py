"""CRUD bảng giá theo nền tảng."""

from django.db import IntegrityError
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.catalog.models import PriceBook
from apps.orders.application.services.price_book_service import (
    DefaultPriceBookDeleteError,
    PriceBookNotFoundError,
    soft_delete_price_book,
)
from apps.orders.serializers import PriceBookSerializer
from apps.users.permissions import ORDER_SECTIONS, require_method_permission

# Audit 2026-08-19 N2: sua/xoa bang gia phai can quyen ghi cung-action.
_PB_SECTIONS = ("products", *ORDER_SECTIONS)
_PB_VIEW = tuple(f"{s}:view" for s in _PB_SECTIONS)
_PB_CREATE = tuple(f"{s}:create" for s in _PB_SECTIONS)
_PB_EDIT = tuple(f"{s}:edit" for s in _PB_SECTIONS)
_PB_DELETE = tuple(f"{s}:delete" for s in _PB_SECTIONS)

# Hai request cùng đặt is_default=True cho hai bảng khác nhau (đụng pricebook_one_default),
# hoặc cùng dùng một code (đụng pricebook_code_active_uniq) nếu chạy đồng thời (xem
# _demote_other_default_price_books trong catalog_write_service và validate_code trong
# PriceBookSerializer, cả hai đều check-rồi-mới-save không lock) — bắt IntegrityError ở
# đây để trả lỗi nghiệp vụ, không để lộ 500. Chọn
# cách này thay vì chỉ khoá hàng bằng select_for_update() vì nó đúng trong MỌI kiểu xen kẽ
# giao dịch, kể cả những trường hợp select_for_update() không khoá được hàng mục tiêu (hàng
# đó chưa tồn tại/chưa match predicate tại thời điểm khoá).
#
# QUAN TRỌNG: phải phân biệt theo TÊN constraint trước khi gắn message, vì PriceBook có
# nhiều unique constraint độc lập — nuốt chung một IntegrityError sẽ gắn nhầm nguyên nhân
# (vd lỗi trùng code lại báo "xung đột default"). Constraint lạ (không nhận diện được)
# KHÔNG được nuốt — để nó nổi lên thành 500 thật, vì đó là lỗi không lường trước.
_DEFAULT_CONFLICT_DETAIL = "Một bảng giá khác vừa được đặt làm mặc định cùng lúc, vui lòng thử lại."
_CODE_CONFLICT_DETAIL = "Mã bảng giá này vừa được một request khác sử dụng, vui lòng thử lại."

_CONSTRAINT_DETAILS = {
    "pricebook_one_default": _DEFAULT_CONFLICT_DETAIL,
    "pricebook_code_active_uniq": _CODE_CONFLICT_DETAIL,
}


def _integrity_error_detail(err: IntegrityError) -> str | None:
    """Suy ra message nghiệp vụ tương ứng từ tên constraint bị vi phạm.

    Trả về ``None`` nếu không nhận diện được constraint — khi đó caller phải để lỗi
    tiếp tục nổi lên (không được tự bịa message/nuốt lỗi lạ).
    """
    # psycopg (driver Postgres đang dùng) đính kèm .diag.constraint_name trên exception
    # gốc; Django bọc lại thành IntegrityError nhưng vẫn giữ exception gốc ở __cause__.
    diag = getattr(getattr(err, "__cause__", None), "diag", None)
    constraint_name = getattr(diag, "constraint_name", None)
    if constraint_name:
        return _CONSTRAINT_DETAILS.get(constraint_name)
    # Fallback khi không lấy được .diag (driver/backend khác không hỗ trợ): dò tên
    # constraint xuất hiện trong nội dung thông điệp lỗi.
    text = str(err)
    for name, detail in _CONSTRAINT_DETAILS.items():
        if name in text:
            return detail
    return None


@extend_schema(
    request=PriceBookSerializer,
    responses={200: PriceBookSerializer(many=True), 201: PriceBookSerializer},
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_method_permission(GET=_PB_VIEW, POST=_PB_CREATE)])
@never_cache
def list_create_price_books(request):
    if request.method == "GET":
        books = PriceBook.objects.filter(deleted_at__isnull=True).order_by("sort_order", "id")
        return Response(PriceBookSerializer(books, many=True).data, status=status.HTTP_200_OK)

    serializer = PriceBookSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        serializer.save()
    except IntegrityError as err:
        detail = _integrity_error_detail(err)
        if detail is None:
            raise
        return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema(
    request=PriceBookSerializer,
    responses={
        200: PriceBookSerializer,
        204: OpenApiResponse(description="Đã xóa (soft-delete) bảng giá"),
        400: OpenApiResponse(description="Không thể xoá bảng giá mặc định, hoặc xung đột đặt mặc định đồng thời"),
        404: OpenApiResponse(description="Không tìm thấy bảng giá"),
    },
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_method_permission(PATCH=_PB_EDIT, DELETE=_PB_DELETE)])
@never_cache
def update_delete_price_book(request, price_book_id: int):
    if request.method == "DELETE":
        # Khoá hàng + re-check is_default nằm trong service (chặn race với PATCH
        # set-default đồng thời); view chỉ dịch exception nghiệp vụ sang HTTP.
        try:
            soft_delete_price_book(price_book_id)
        except PriceBookNotFoundError:
            return Response({"detail": "Không tìm thấy bảng giá."}, status=status.HTTP_404_NOT_FOUND)
        except DefaultPriceBookDeleteError:
            return Response(
                {"detail": "Không thể xoá bảng giá mặc định. Hãy đặt bảng giá mặc định khác trước."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    book = PriceBook.objects.filter(id=price_book_id, deleted_at__isnull=True).first()
    if not book:
        return Response({"detail": "Không tìm thấy bảng giá."}, status=status.HTTP_404_NOT_FOUND)

    serializer = PriceBookSerializer(instance=book, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    try:
        serializer.save()
    except IntegrityError as err:
        detail = _integrity_error_detail(err)
        if detail is None:
            raise
        return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)
    return Response(serializer.data, status=status.HTTP_200_OK)
