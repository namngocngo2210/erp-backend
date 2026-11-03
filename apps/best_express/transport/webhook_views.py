"""Webhook nhận trạng thái từ Best Express.

Auth: Bearer token tĩnh (BestExpressAccount.webhook_token — mình sinh, gửi sale
Best cấu hình trên trang V9). Best yêu cầu đối tác luôn phản hồi Result 1/2.
"""

import logging

from django.utils.crypto import constant_time_compare
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.best_express.application.services.webhook_service import apply_event, store_event
from apps.best_express.models import BestExpressAccount

logger = logging.getLogger(__name__)


def _resolve_account(request) -> BestExpressAccount | None:
    header = str(request.headers.get("Authorization") or "")
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    if not token:
        return None
    # webhook_token mã hóa Fernet (không deterministic) nên không filter được
    # trong SQL — so sánh trong Python; số account rất nhỏ (1-2 dòng).
    for account in BestExpressAccount.objects.filter(is_active=True):
        if account.webhook_token and constant_time_compare(account.webhook_token, token):
            return account
    return None


# Webhook từ BEST Express (token-auth qua header, AllowAny) → loại khỏi schema CRM
# nội bộ, giống các webhook ingest khác. Payload do vendor định nghĩa, không phải
# hợp đồng FE↔BE.
@extend_schema(exclude=True)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def best_express_webhook(request):
    account = _resolve_account(request)
    if account is None:
        return Response(
            {"Result": 2, "Message": "Unauthorized"},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    payload = request.data
    if not isinstance(payload, dict):
        return Response(
            {"Result": 2, "Message": "Payload phải là JSON object"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        event = store_event(account, payload)
    except Exception:
        logger.exception("Best webhook: store_event lỗi hệ thống")
        return Response(
            {"Result": 2, "Message": "Internal error"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if event is not None:
        try:
            apply_event(event)
        except Exception:
            # Event đã lưu với processed=False — cron
            # `apps.best_express.tasks.retry_unprocessed_best_webhook_events` (15') sẽ áp
            # lại. Vẫn trả Result 1 để Best không retry vô hạn.
            logger.exception(
                "Best webhook: apply_event lỗi (event_id=%s)", event.pk
            )
    return Response({"Result": 1, "Message": ""})
