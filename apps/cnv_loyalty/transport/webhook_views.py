"""Webhook nhận đơn mới/đơn đổi từ CNV Loyalty.

Auth: token tĩnh do MÌNH sinh (`CNV_WEBHOOK_VERIFY_TOKEN`), khai với CNV lúc
`POST /webhook/register` rồi CNV gửi lại. Chấp nhận cả 3 chỗ đặt token vì CNV
không tài liệu hoá chỗ nào: header `Authorization` (cả `Token`/`Bearer`), header
`X-Verify-Token`, và field `verify_token` trong body.

Token rỗng ⇒ từ chối tất cả. Endpoint này là `AllowAny` nên không có token nghĩa
là không xác thực được người gọi — mở toang cho bất kỳ ai bơm đơn vào DB.
"""

import logging

from django.conf import settings
from django.utils.crypto import constant_time_compare
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.cnv_loyalty.application.services.webhook_service import (
    apply_event,
    resolve_topic,
    store_event,
)

logger = logging.getLogger(__name__)


def _presented_tokens(request) -> list[str]:
    tokens = []
    header = str(request.headers.get("Authorization") or "").strip()
    if header:
        parts = header.split(None, 1)
        tokens.append(parts[1].strip() if len(parts) == 2 else header)
    for name in ("X-Verify-Token", "X-Cnv-Verify-Token"):
        value = str(request.headers.get(name) or "").strip()
        if value:
            tokens.append(value)
    if isinstance(request.data, dict):
        value = str(request.data.get("verify_token") or "").strip()
        if value:
            tokens.append(value)
    return tokens


def _authorized(request) -> bool:
    expected = getattr(settings, "CNV_WEBHOOK_VERIFY_TOKEN", "")
    if not expected:
        return False
    return any(constant_time_compare(expected, token) for token in _presented_tokens(request))


# Webhook từ CNV (token-auth qua header, AllowAny) → loại khỏi schema CRM nội bộ,
# giống các webhook ingest khác. Payload do vendor định nghĩa, không phải hợp
# đồng FE↔BE.
@extend_schema(exclude=True)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def cnv_webhook(request):
    if not getattr(settings, "CNV_INBOUND_ENABLED", False):
        # 503 chứ không 200: CNV thấy lỗi và (nếu có cơ chế retry) gửi lại sau
        # khi mình bật, thay vì coi như đã nhận rồi bỏ đơn vào hư không.
        return Response(
            {"ok": False, "message": "CNV inbound đang tắt"},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if not _authorized(request):
        logger.warning("CNV webhook: từ chối request thiếu/sai verify token")
        return Response({"ok": False, "message": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    payload = request.data
    if not isinstance(payload, (dict, list)):
        return Response(
            {"ok": False, "message": "Payload phải là JSON object hoặc array"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    topic = resolve_topic(request.headers, payload)
    try:
        event = store_event(payload, topic=topic)
    except Exception:
        logger.exception("CNV webhook: store_event lỗi hệ thống")
        return Response(
            {"ok": False, "message": "Internal error"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if event is not None:
        try:
            apply_event(event)
        except Exception:
            # Event đã lưu với processed=False — cron
            # `retry_unprocessed_webhook_events` sẽ áp lại. Vẫn trả 200 để CNV
            # không retry vô hạn cho một lỗi phía mình.
            logger.exception("CNV webhook: apply_event lỗi (event_id=%s)", event.pk)
    return Response({"ok": True})
