"""API trang "Cấu hình liên kết Foxia" — bảng (kênh × loại nguồn) → idnguonkhachhang.

Hàng = kênh bán (thêm/bỏ được), cột = 5 loại nguồn cố định `FoxiaLinkKind`
(landing/messenger/hotline/zalo/resale) do chủ hệ thống tạo bên Foxia; đơn tra
cột theo `order_source` của nó.

Tách khỏi `api_views.py`: file đó phục vụ BÁO CÁO (chiều đọc), còn đây là cấu
hình của chiều GHI. Trộn hai việc vào một file là trộn hai vòng đời thay đổi.

Toàn bộ endpoint chỉ superuser: bảng này quyết định đơn của ai chảy vào nguồn
nào bên đối tác, sai một ô là gán nhầm doanh số marketing của cả một kênh.
"""

from __future__ import annotations

from functools import wraps

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.foxia.application.services.link_config_service import FoxiaLinkConfigService
from apps.foxia.models import FoxiaLinkCell, FoxiaLinkChannel, FoxiaLinkKind, FoxiaSource
from apps.users.rbac import resolve_effective_permissions


def superuser_only(view):
    """Chặn TRƯỚC khi chạm DB. Không dùng section RBAC — đây là quyền quản trị."""

    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not resolve_effective_permissions(request.user).is_superuser:
            return Response({"detail": "Chỉ quản trị viên."}, status=status.HTTP_403_FORBIDDEN)
        return view(request, *args, **kwargs)

    return _wrapped


def _serialize_grid() -> dict:
    rows = list(FoxiaLinkChannel.objects.select_related("channel"))
    cells = list(FoxiaLinkCell.objects.all())

    # Tên nguồn chỉ để người dùng tự kiểm tra mình gõ đúng id — tra một lượt,
    # không tham gia vào logic nào, thiếu thì để rỗng.
    wanted = [c.foxia_source_id for c in cells]
    names = dict(FoxiaSource.objects.filter(foxia_id__in=wanted).values_list("foxia_id", "name"))
    return {
        "channels": [
            {
                "id": row.id,
                "channel_id": row.channel_id,
                "code": row.channel.code,
                "name": row.channel.name,
                "sort_order": row.sort_order,
            }
            for row in rows
        ],
        # Thứ tự cột do BE quyết: FE chỉ render, không tự khai danh sách riêng.
        "kinds": [{"value": kind.value, "label": kind.label} for kind in FoxiaLinkKind],
        "cells": [
            {
                "link_channel_id": cell.link_channel_id,
                "kind": cell.kind,
                "foxia_source_id": cell.foxia_source_id,
                "source_name": names.get(cell.foxia_source_id, ""),
            }
            for cell in cells
        ],
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@superuser_only
def link_config(request):
    return Response(_serialize_grid())


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@superuser_only
def add_channels(request):
    """Thêm hàng kênh, bỏ qua id đã có mặt (idempotent) và id không phải số."""
    try:
        ids = [int(value) for value in (request.data.get("channel_ids") or [])]
    except (TypeError, ValueError):
        return Response({"detail": "Danh sách id không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)

    FoxiaLinkConfigService.add_channels(ids)
    return Response(_serialize_grid())


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
@superuser_only
def delete_channel(request, pk: int):
    obj = FoxiaLinkChannel.objects.filter(pk=pk).first()
    if obj is None:
        return Response({"detail": "Không tìm thấy."}, status=status.HTTP_404_NOT_FOUND)
    deleted = FoxiaLinkCell.objects.filter(link_channel_id=obj.id).count()
    obj.delete()  # ON DELETE CASCADE dọn các ô của hàng này
    return Response({"deleted_cells": deleted})


@api_view(["PUT"])
@permission_classes([IsAuthenticated])
@superuser_only
def save_cells(request):
    """Lưu HÀNG LOẠT các ô đã sửa. `foxia_source_id: null` = xoá ô.

    Validate TOÀN BỘ trước khi ghi bất cứ gì: bảng cấu hình lưu nửa vời nguy
    hiểm hơn là không lưu — người dùng sẽ tin những ô còn lại cũng đã lưu.
    """
    raw = request.data.get("cells")
    if not isinstance(raw, list):
        return Response({"detail": "Thiếu danh sách `cells`."}, status=status.HTTP_400_BAD_REQUEST)

    row_ids = set(FoxiaLinkChannel.objects.values_list("id", flat=True))

    upserts: list[tuple[int, str, int]] = []
    deletes: list[tuple[int, str]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            return Response({"detail": f"Ô thứ {index + 1} không hợp lệ."}, status=400)
        row = entry.get("link_channel_id")
        kind = entry.get("kind")
        if row not in row_ids:
            return Response(
                {"detail": f"Ô thứ {index + 1}: kênh không còn trong bảng."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if kind not in FoxiaLinkKind.values:
            return Response(
                {"detail": f"Ô thứ {index + 1}: loại nguồn không hợp lệ."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        value = entry.get("foxia_source_id")
        if value is None or value == "":
            deletes.append((row, kind))
            continue
        try:
            value = int(value)
        except (TypeError, ValueError):
            return Response({"detail": f"Ô thứ {index + 1}: id phải là số."}, status=400)
        if value <= 0:
            return Response({"detail": f"Ô thứ {index + 1}: id phải lớn hơn 0."}, status=400)
        upserts.append((row, kind, value))

    FoxiaLinkConfigService.save_cells(deletes=deletes, upserts=upserts, actor=request.user)
    return Response(_serialize_grid())
