"""Nhóm đơn hoàn (ReturnEntry) — chỉ còn 2 cửa: tạo qua luồng QUÉT trả hàng và
huỷ (đảo bút toán). Mọi thao tác tay khác của phiếu trả (xem/sửa nháp/ghi sổ tay/
chốt phiếu ngày) đã gỡ 2026-07-14 — xem docs/superpowers/specs/
2026-07-14-remove-return-note-ui-design.md.
"""
from __future__ import annotations

from django.views.decorators.cache import never_cache
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.models import ReturnEntry
from apps.inventory.transport.serializers.return_serializers import (
    AdjustReturnEntrySerializer,
    CreateReturnNoteSerializer,
    ReturnEntrySerializer,
)
from apps.users.permissions import require_permission

_NOT_FOUND = {"detail": "Không tìm thấy phiếu trả hàng."}


def _entry_qs():
    """Queryset chuẩn của nhóm đơn hoàn — nạp sẵn phiếu ngày + dòng hàng (tránh N+1)."""
    return (
        ReturnEntry.objects.select_related(
            "return_note", "return_note__warehouse", "source_order", "created_by"
        )
        .prefetch_related("lines__product")
    )


# Tạo nhóm đơn hoàn trong phiếu ngày đang mở (nháp, hoặc ghi sổ ngay khi
# auto_post=true — luồng quét khớp mã).
@extend_schema(
    request=CreateReturnNoteSerializer,
    responses={
        201: ReturnEntrySerializer,
        400: OpenApiResponse(description="Dữ liệu phiếu trả hàng không hợp lệ"),
    },
)
@api_view(["POST"])
# Audit 2026-08-19 N2: ghi can quyen ghi, khong gate bang require_section (=:view).
@permission_classes([IsAuthenticated, require_permission("returns:create")])
@never_cache
def create_return_entry(request):
    serializer = CreateReturnNoteSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data
    auto_post = data.pop("auto_post", False)
    create_kwargs = dict(
        warehouse_id=data["warehouse_id"],
        lines=list(data["lines"]),
        source_order_id=data.get("source_order_id"),
        shipping_code=data.get("shipping_code", ""),
        customer_name=data.get("customer_name", ""),
        customer_phone=data.get("customer_phone", ""),
        source_note=data.get("source_note", ""),
        note=data.get("note", ""),
        user_id=request.user.id,
        scan_batch_id=data.get("scan_batch_id"),
    )
    try:
        if auto_post:
            entry = ReturnEntryService.create_and_post_entry(**create_kwargs)
        else:
            entry = ReturnEntryService.create_entry(**create_kwargs)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    entry = _entry_qs().get(pk=entry.pk)
    return Response(ReturnEntrySerializer(entry).data, status=status.HTTP_201_CREATED)


# Huỷ nhóm đơn hoàn — không body, trả nhóm đơn đã huỷ.
# Huỷ 1 nhóm ĐÃ ghi sổ là bút toán đảo (xuất tồn khỏi kho) → cần quyền duyệt,
# đối xứng với huỷ phiếu kho posted (stock-documents:approve).
@extend_schema(
    request=None,
    responses={
        200: ReturnEntrySerializer,
        400: OpenApiResponse(description="Không thể huỷ ở trạng thái hiện tại"),
        404: OpenApiResponse(description="Không tìm thấy phiếu trả hàng"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("returns:approve")])
@never_cache
def cancel_return_view(request, entry_id):
    try:
        entry = ReturnEntryService.cancel_entry(
            entry_id=entry_id, user_id=request.user.id
        )
    except ReturnEntry.DoesNotExist:
        return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    entry = _entry_qs().get(pk=entry.pk)
    return Response(ReturnEntrySerializer(entry).data, status=status.HTTP_200_OK)


# Điều chỉnh số lượng dòng của nhóm đơn ĐÃ ghi sổ (thực nhận khác lúc quét).
# Thay đổi bút toán đã ghi sổ → cùng quyền duyệt với huỷ (returns:approve).
@extend_schema(
    request=AdjustReturnEntrySerializer,
    responses={
        200: ReturnEntrySerializer,
        400: OpenApiResponse(description="Dữ liệu điều chỉnh không hợp lệ"),
        404: OpenApiResponse(description="Không tìm thấy phiếu trả hàng"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("returns:approve")])
@never_cache
def adjust_return_view(request, entry_id):
    serializer = AdjustReturnEntrySerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    try:
        entry = ReturnEntryService.adjust_entry(
            entry_id=entry_id,
            lines=list(serializer.validated_data["lines"]),
            user_id=request.user.id,
        )
    except ReturnEntry.DoesNotExist:
        return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    entry = _entry_qs().get(pk=entry.pk)
    return Response(ReturnEntrySerializer(entry).data, status=status.HTTP_200_OK)
