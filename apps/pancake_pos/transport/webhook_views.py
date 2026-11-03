"""Webhook Pancake POS → CRM + API quản trị webhook (two-way-sync §5.1, §6b, §7 B1).

Nhận (`POST /api/pancake-pos/webhook`): không JWT. Pancake không ký payload → xác thực
bằng header bí mật tự đặt `X-Desi-Webhook-Secret` (khai trong `webhook_headers` khi cấu
hình), so sánh hằng thời gian với `PANCAKE_POS_WEBHOOK_SECRET`. Secret trống/ngắn → 503
(fail-closed: endpoint AllowAny mà không có secret = ai cũng bơm được dữ liệu). Chỉ lưu
thô rồi trả 200; xử lý ở worker. Lỗi lưu → 500 để Pancake gửi lại.

Quản trị: xem = `pancake-pos:view`; cấu hình webhook trên Pancake / xử lý lại lỗi / khớp
tay = `pancake-pos:edit`.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.common.throttling import PancakePosWebhookThrottle
from apps.pancake_pos.application.services import webhook_config, webhook_jobs
from apps.pancake_pos.application.services.factory import PancakePosDisabled, build_inbound_order_service
from apps.pancake_pos.inbound_repositories import InboundOrderRepository, WebhookEventRepository
from apps.pancake_pos.infrastructure.pancake_pos_client import PancakePosError
from apps.pancake_pos.metrics import webhook_received_total
from apps.pancake_pos.models import InboundMatchStatus, PancakePosInboundOrder
from apps.pancake_pos.transport.webhook_serializers import (
    PancakePosInboundOrderPageSerializer,
    PancakePosInboundOrderSerializer,
    PancakePosInboundResolveRequestSerializer,
    PancakePosWebhookConfigureRequestSerializer,
    PancakePosWebhookConfigureResultSerializer,
    PancakePosWebhookRetryResultSerializer,
    PancakePosWebhookStatusSerializer,
)
from apps.sysconfig import sysconfig
from apps.users.permissions import require_method_permission, require_section

logger = logging.getLogger(__name__)

SECTION = "pancake-pos"
EDIT = f"{SECTION}:edit"
MAX_BODY_BYTES = 2 * 1024 * 1024  # một đơn đầy đủ ~50 KB; 2 MB là trần chống lạm dụng
MAX_LIMIT = 200


def _reject(result: str, code: int, message: str) -> Response:
    webhook_received_total.labels("-", result).inc()
    return Response({"ok": False, "message": message}, status=code)


# Webhook đối tác (header bí mật, AllowAny) → loại khỏi schema CRM nội bộ như các webhook
# ingest khác: payload do Pancake định nghĩa, không phải hợp đồng FE↔BE.
@extend_schema(exclude=True)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([PancakePosWebhookThrottle])
def pancake_pos_webhook(request):
    secret = str(sysconfig.get("PANCAKE_POS_WEBHOOK_SECRET") or "").strip()
    if len(secret) < webhook_config.MIN_SECRET_LEN:
        return _reject("disabled", status.HTTP_503_SERVICE_UNAVAILABLE, "Webhook chưa cấu hình")
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length > MAX_BODY_BYTES:
        return _reject("too_large", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Payload quá lớn")
    presented = str(request.headers.get(webhook_config.WEBHOOK_HEADER) or "")
    if not constant_time_compare(secret, presented):
        logger.warning("pancake_pos webhook: từ chối request thiếu/sai secret")
        return _reject("unauthorized", status.HTTP_401_UNAUTHORIZED, "Unauthorized")
    payload = request.data
    if not isinstance(payload, (dict, list)) or not payload:
        return _reject("invalid", status.HTTP_400_BAD_REQUEST, "Payload phải là JSON object hoặc array")
    try:
        results = webhook_jobs.ingest(payload)
    except Exception:  # noqa: BLE001 — 500 để Pancake gửi lại
        logger.exception("pancake_pos webhook: lưu sự kiện lỗi")
        return _reject("error", status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal error")
    stored = sum(1 for event_id, _ in results if event_id)
    return Response({"ok": True, "stored": stored, "duplicate": len(results) - stored})


# ── quản trị ────────────────────────────────────────────────────────────────
@extend_schema(operation_id="pancake_pos_webhook_status", responses={200: PancakePosWebhookStatusSerializer})
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def webhook_status(request):
    """Cấu hình phía CRM + số sự kiện 24h theo loại/trạng thái + lỗi gần nhất."""
    summary = WebhookEventRepository.summary()
    return Response({
        "config": webhook_config.local_state(),
        "counts_24h": WebhookEventRepository.stats_since(timezone.now() - timedelta(hours=24)),
        "last_received_at": summary["last_received_at"],
        "error_count": summary["error_count"],
        "last_error": summary["last_error"],
        "inbound_counts": InboundOrderRepository.counts(),
    })


@extend_schema(
    operation_id="pancake_pos_webhook_configure",
    request=PancakePosWebhookConfigureRequestSerializer,
    responses={
        200: PancakePosWebhookConfigureResultSerializer,
        400: OpenApiTypes.OBJECT,
        409: OpenApiResponse(description="PANCAKE_POS_ENABLED đang tắt"),
        502: OpenApiResponse(description="Pancake trả lỗi"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_method_permission(POST=EDIT)])
def webhook_configure(request):
    """Đặt webhook trên shop Pancake. Mặc định dry-run (chỉ trả body, secret đã che)."""
    serializer = PancakePosWebhookConfigureRequestSerializer(data=request.data or {})
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    try:
        result = webhook_config.configure(
            dry_run=data["dry_run"], url=data.get("url") or None, types=data.get("types") or None
        )
    except webhook_config.WebhookConfigError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except PancakePosDisabled as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    except PancakePosError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
    if not data["dry_run"]:
        logger.info("pancake_pos: user %s cấu hình webhook Pancake (types=%s)", request.user.pk,
                    result["body"]["shop"]["webhook_types"])
    return Response(result)


@extend_schema(
    operation_id="pancake_pos_webhook_retry_errors", request=None,
    responses={202: PancakePosWebhookRetryResultSerializer},
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_method_permission(POST=EDIT)])
def webhook_retry_errors(request):
    count = webhook_jobs.retry_errors()
    return Response({"count": count}, status=status.HTTP_202_ACCEPTED)


_INBOUND_FIELDS = (
    "id", "pancake_order_id", "order_id", "match_status", "match_method", "candidates", "reason",
    "pancake_status", "source_name", "account_name", "tracking_code", "partner_name", "total_price",
    "inserted_at", "shipped_at", "delivered_at", "returned_at", "first_seen_at", "updated_at",
)


@extend_schema(
    operation_id="pancake_pos_inbound_orders_list",
    parameters=[
        OpenApiParameter("match_status", OpenApiTypes.STR,
                         description="Lọc, nhiều giá trị cách dấu phẩy (mặc định waiting,unmatched)"),
        OpenApiParameter("q", OpenApiTypes.STR, description="Mã đơn Pancake / mã vận đơn / mã đơn CRM"),
        OpenApiParameter("limit", OpenApiTypes.INT), OpenApiParameter("offset", OpenApiTypes.INT),
    ],
    responses={200: PancakePosInboundOrderPageSerializer},
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(SECTION)])
def inbound_orders(request):
    """Màn "Chờ khớp đơn": đơn Pancake chưa khớp đơn CRM (mặc định chờ + chờ khớp tay)."""
    params = request.query_params
    try:
        limit = max(1, min(int(params.get("limit", 50)), MAX_LIMIT))
        offset = max(0, int(params.get("offset", 0)))
    except (TypeError, ValueError):
        return Response({"detail": "limit/offset không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)
    raw = str(params.get("match_status") or "waiting,unmatched")
    statuses = [s.strip() for s in raw.split(",") if s.strip()]
    if any(s not in InboundMatchStatus.values for s in statuses):
        return Response({"detail": "match_status không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)
    qs = PancakePosInboundOrder.objects.filter(match_status__in=statuses)
    q = str(params.get("q") or "").strip()
    if q:
        cond = Q(pancake_order_id=q) | Q(tracking_code=q)
        if q.isdigit():
            cond |= Q(order_id=int(q))
        qs = qs.filter(cond)
    qs = qs.order_by("-first_seen_at", "-id")
    total = qs.count()
    items = list(qs.values(*_INBOUND_FIELDS)[offset : offset + limit])
    return Response({
        "items": PancakePosInboundOrderSerializer(items, many=True).data,
        "total": total, "limit": limit, "offset": offset,
    })


@extend_schema(
    operation_id="pancake_pos_inbound_order_resolve",
    request=PancakePosInboundResolveRequestSerializer,
    responses={
        200: PancakePosInboundOrderSerializer,
        400: OpenApiTypes.OBJECT,
        404: OpenApiResponse(description="Không tìm thấy"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_method_permission(POST=EDIT)])
def resolve_inbound_order(request, pk: int):
    """Khớp tay đơn Pancake với đơn CRM (`match` + order_id) hoặc bỏ qua (`ignore`)."""
    serializer = PancakePosInboundResolveRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    service = build_inbound_order_service()
    try:
        service.resolve_manual(pk, order_id=data.get("order_id"), ignore=data["action"] == "ignore")
    except LookupError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    logger.info("pancake_pos: user %s xử lý tay đơn Pancake inbound #%s (%s)", request.user.pk, pk, data["action"])
    row = PancakePosInboundOrder.objects.filter(id=pk).values(*_INBOUND_FIELDS).first()
    return Response(PancakePosInboundOrderSerializer(row).data)
