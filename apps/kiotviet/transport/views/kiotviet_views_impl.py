import logging

from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.kiotviet.webhook import process_invoice_webhook

logger = logging.getLogger(__name__)


# Webhook bên thứ 3 (KiotViet) ký X-Hub-Signature — không phải hợp đồng cho FE → ẩn khỏi schema.
@extend_schema(exclude=True)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def invoice_webhook(request, channel_id: int):
    signature = (request.headers.get("X-Hub-Signature") or "").strip()
    raw_body = request.body or b""
    result = process_invoice_webhook(
        channel_id=channel_id,
        raw_body=raw_body,
        signature=signature,
    )
    payload = {"detail": result.get("detail", "")}
    if result.get("stats") is not None:
        payload["stats"] = result["stats"]
    status_code = int(result.get("status_code") or 200)
    return Response(payload, status=status_code)
