"""Cấu hình kết nối Media — admin (users:view/edit), không đi qua MediaProxy."""

from __future__ import annotations

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.media.application.services import connection_service as svc
from apps.media.exceptions import MediaDisabled
from apps.media.transport.errors import media_errors
from apps.media.transport.serializers import MediaSettingsUpdateSerializer
from apps.users.permissions import require_method_permission, require_permission


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated, require_method_permission(GET="users:view", PATCH="users:edit")])
@media_errors
def settings_view(request):
    if request.method == "GET":
        return Response(svc.to_payload(svc.current()))
    serializer = MediaSettingsUpdateSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    return Response(svc.to_payload(svc.update(serializer.validated_data)))


@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("users:edit")])
@media_errors
def settings_test(request):
    conn = svc.current()
    if conn is None or not conn.shared_api_key:
        raise MediaDisabled("Chưa nhập host hoặc API key chung.")
    return Response(svc.test_shared_key(conn))
