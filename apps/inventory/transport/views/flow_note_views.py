"""Sửa ghi chú của một dòng bất kỳ trên trang Lịch sử nhập xuất.

Một cửa cho cả 5 loại dòng (`target_type`); việc chọn nguồn lưu nằm ở
`FlowNoteService`, view chỉ parse request và dịch lỗi sang mã HTTP.

Hai endpoint note cũ (`return-history/<id>/note`, `flow-pending/<id>/note`) vẫn
sống và dùng chung service — endpoint này KHÔNG nhân đôi logic ghi.
"""
from __future__ import annotations

from django.views.decorators.cache import never_cache
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.inventory.application.services.flow_note_service import (
    VALID_TARGETS,
    FlowNoteService,
)
from apps.users.permissions import require_permission


@extend_schema(
    request=inline_serializer(
        "FlowNoteUpdate",
        fields={
            "target_type": drf.ChoiceField(choices=sorted(VALID_TARGETS)),
            "target_id": drf.IntegerField(),
            "note": drf.CharField(allow_blank=True),
        },
    ),
    responses={
        200: inline_serializer(
            "FlowNoteResult",
            fields={
                "target_type": drf.CharField(),
                "target_id": drf.IntegerField(),
                "note": drf.CharField(allow_blank=True),
            },
        ),
        400: OpenApiResponse(description="target_type/target_id/note không hợp lệ"),
        404: OpenApiResponse(description="Không tìm thấy dòng cần ghi chú"),
    },
)
@api_view(["PATCH"])
@permission_classes([IsAuthenticated, require_permission("stock:edit")])
@never_cache
def flow_note_update(request):
    target_type = request.data.get("target_type") or ""
    note = request.data.get("note", "")
    if not isinstance(note, str):
        return Response(
            {"detail": "note phải là chuỗi."}, status=status.HTTP_400_BAD_REQUEST
        )
    try:
        target_id = int(request.data.get("target_id"))
    except (TypeError, ValueError):
        return Response(
            {"detail": "target_id phải là số nguyên."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        result = FlowNoteService.update(
            target_type=target_type,
            target_id=target_id,
            note=note,
            user_id=request.user.id,
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except LookupError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
    return Response(result, status=status.HTTP_200_OK)
