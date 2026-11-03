"""Webhook ingest views: landing page + Lark.

Tách từ ``order_ingest_product_dashboard_view.py`` (audit 2026-07 mục 4.5).
Logic nghiệp vụ landing ingest nằm ở
:mod:`apps.orders.application.services.order_ingest_service`.
"""

import logging
import secrets

from django.conf import settings
from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.channels_core.models import Channel
from apps.orders.application.services.lead_filter_service import (
    FilterAction,
    LeadFilterService,
)
from apps.orders.application.services.order_ingest_service import (
    REJECT_STATUS_MAP as _REJECT_STATUS_MAP,
)
from apps.orders.application.services.order_ingest_service import OrderIngestService
from apps.orders.import_landing_excel import build_landing_payload_from_fields
from apps.orders.metrics import landing_ingest_received_total
from apps.orders.models import (
    IngestSource,
    LandingIngestTrace,
    LandingIngestTraceStatus,
)
from apps.orders.serializers import OrderCreateSerializer
from apps.orders.application.services.order_service_impl import OrderService
from apps.sysconfig import sysconfig
from apps.users.models import User

from ._view_helpers import _extract_ingest_api_token

logger = logging.getLogger(__name__)


def _raw_request_body(request) -> dict:
    data = request.data
    if hasattr(data, "lists"):
        return {key: values if len(values) > 1 else values[0] for key, values in data.lists()}
    if hasattr(data, "keys"):
        return dict(data)
    if isinstance(data, dict):
        return data
    return {"_raw": data}


# Webhook ingest từ landing page (token-auth, AllowAny) → loại khỏi schema CRM nội bộ.
@extend_schema(exclude=True)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def ingest_landing_order(request):
    expected_token = sysconfig.get("ORDERS_INGEST_API_TOKEN")
    if not expected_token:
        # Fail-closed in production: without a token this ingest endpoint would
        # be world-writable, so disable it rather than skipping auth.
        if getattr(settings, "IS_PRODUCTION", False):
            landing_ingest_received_total.labels(token="unconfigured").inc()
            logger.error("Landing ingest disabled: ORDERS_INGEST_API_TOKEN is not configured")
            return Response(
                {"detail": "Landing ingest is not configured"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        # Non-production: keep lenient so local dev works without a token.
    else:
        provided_token = _extract_ingest_api_token(request)
        if not provided_token or not secrets.compare_digest(provided_token, expected_token):
            landing_ingest_received_total.labels(token="invalid").inc()
            return Response({"detail": "Invalid ingest API token."}, status=status.HTTP_401_UNAUTHORIZED)
    landing_ingest_received_total.labels(token="ok").inc()

    result = OrderIngestService.ingest_landing_order(
        _raw_request_body(request),
        request.data,
        request_path=request.path,
        content_type=request.content_type,
    )
    return Response(result.body, status=result.status)


# Webhook ingest từ Lark (token-auth, AllowAny) → loại khỏi schema CRM nội bộ.
@extend_schema(exclude=True)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def lark_ingest(request):
    """Ingest single landing order pushed from Lark. Reuses ORDERS_INGEST_API_TOKEN."""
    expected_token = sysconfig.get("ORDERS_INGEST_API_TOKEN")
    if not expected_token:
        # Fail-closed in production: without a token this ingest endpoint would
        # be world-writable, so disable it rather than skipping auth.
        if getattr(settings, "IS_PRODUCTION", False):
            logger.error("Lark ingest disabled: ORDERS_INGEST_API_TOKEN is not configured")
            return Response(
                {"detail": "Lark ingest is not configured"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        # Non-production: keep lenient so local dev works without a token.
    else:
        provided_token = _extract_ingest_api_token(request)
        if not provided_token or not secrets.compare_digest(provided_token, expected_token):
            return Response({"detail": "Invalid ingest API token."}, status=status.HTTP_401_UNAUTHORIZED)

    raw_body = _raw_request_body(request)
    if not isinstance(raw_body, dict):
        return Response({"detail": "Payload phải là JSON object."}, status=status.HTTP_400_BAD_REQUEST)

    staff_username = str(raw_body.get("staff_username") or "").strip()
    actor = None
    if staff_username:
        actor = User.objects.filter(username__iexact=staff_username, deleted_at__isnull=True).first()

    try:
        row_payload = build_landing_payload_from_fields(raw_body)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception:
        logger.exception("Lark ingest payload build failed: body=%s", raw_body)
        return Response({"detail": "Dữ liệu không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)

    lark_trace = LandingIngestTrace.objects.create(
        status=LandingIngestTraceStatus.RECEIVED,
        source=IngestSource.LARK,
        payload=raw_body,
        phone=str(row_payload.get("phone", ""))[:30],
        name=str(row_payload.get("name", ""))[:255],
        raw_address=str(row_payload.get("raw_address", ""))[:4000],
        product_code="",
        url_page="",
    )

    channel_code = str(row_payload.get("channel_code") or "").strip()
    channel = (
        Channel.objects.filter(code=channel_code, deleted_at__isnull=True).first()
        if channel_code
        else None
    )
    if channel is None:
        lark_trace.status = LandingIngestTraceStatus.VALIDATION_FAILED
        lark_trace.error_message = f"Kênh bán không tồn tại: channel_code={channel_code}"[:4000]
        lark_trace.save(update_fields=["status", "error_message", "updated_at"])
        return Response(
            {"detail": f"Kênh bán không tồn tại: channel_code={channel_code}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    decision = LeadFilterService.evaluate(row_payload.get("phone", ""), channel)
    if not decision.accepted:
        lark_trace.status = _REJECT_STATUS_MAP.get(
            decision.action, LandingIngestTraceStatus.VALIDATION_FAILED
        )
        lark_trace.error_message = decision.reason[:4000]
        lark_trace.save(update_fields=["status", "error_message", "updated_at"])
        logger.info(
            "Lark ingest rejected: trace_id=%s action=%s reason=%s phone=%s channel_code=%s",
            lark_trace.id,
            decision.action.value,
            decision.reason,
            decision.phone_normalized,
            channel.code,
        )
        http_status = (
            status.HTTP_400_BAD_REQUEST
            if decision.action == FilterAction.REJECT_INVALID_PHONE
            else status.HTTP_409_CONFLICT
        )
        return Response(
            {"detail": decision.reason, **decision.to_response_dict(), "trace_id": lark_trace.id},
            status=http_status,
        )

    serializer = OrderCreateSerializer(data=row_payload, context={"skip_channel_check": True})
    if not serializer.is_valid():
        logger.warning("Lark ingest serializer invalid: errors=%s body=%s", serializer.errors, raw_body)
        lark_trace.status = LandingIngestTraceStatus.VALIDATION_FAILED
        lark_trace.validation_errors = serializer.errors
        lark_trace.save(update_fields=["status", "validation_errors", "updated_at"])
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    validated = serializer.validated_data
    if validated.get("staff") is None and actor is not None:
        validated["staff"] = actor

    from apps.orders.application.services.lead_assignment_service import LeadAssignmentService

    # Tạo đơn + chia sale trong CÙNG một transaction: create_order_manual xếp lịch
    # đẩy Foxia bằng on_commit (đơn ingest có show_orders=False), task đọc lại đơn
    # để lấy `staff.foxia_id` → nếu commit ngay sau create thì task có thể đọc
    # trước khi bộ chia gán sale và đẩy thiếu `staff_id`.
    with transaction.atomic():
        try:
            order = OrderService.create_order_manual(validated, actor, created_via="lark")
        except Exception as exc:
            logger.exception("Lark ingest create failed: body=%s", raw_body)
            lark_trace.status = LandingIngestTraceStatus.TASK_FAILED
            lark_trace.error_message = f"{exc.__class__.__name__}: {exc}"[:4000]
            lark_trace.save(update_fields=["status", "error_message", "updated_at"])
            return Response({"detail": str(exc) or "Không thể tạo đơn."}, status=status.HTTP_400_BAD_REQUEST)

        # Cờ bất biến "đơn ingest về không có địa chỉ" — create_order_manual đã set,
        # giữ lại đây để xét theo payload gốc của Lark (row_payload).
        # Phải trước bộ chia: classify_sheet đọc has_address.
        if not str(row_payload.get("raw_address") or "").strip() and not str(
            row_payload.get("address") or ""
        ).strip():
            order.has_address = False
            order.save(update_fields=["has_address", "updated_at"])

        # Savepoint riêng: bộ chia hỏng không được kéo đơn đã tạo rollback theo
        # (trước đây đơn đã commit rồi mới chia) — sweep 5' vớt lại đơn trống sale.
        try:
            with transaction.atomic():
                LeadAssignmentService.assign_if_unassigned(order)
        except Exception:  # noqa: BLE001 — chia đơn là phụ trợ, không phá ingest
            logger.exception("Lark ingest auto-assign failed (order_id=%s)", order.id)

    from apps.contact_flow.application.services.entry_service import OperationEntryService

    try:
        OperationEntryService.enter_on_new_order(order)
    except Exception:  # noqa: BLE001 — entry flow không được phá ingest
        logger.exception("contact-flow enter_on_new_order failed for order %s", order.id)

    LeadFilterService.register_claim(
        phone_raw=row_payload.get("phone", ""),
        channel=channel,
        order=order,
    )
    lark_trace.status = LandingIngestTraceStatus.CREATED
    lark_trace.order = order
    lark_trace.save(update_fields=["status", "order", "updated_at"])
    return Response(
        {"accepted": True, "order_id": order.id, "trace_id": lark_trace.id},
        status=status.HTTP_200_OK,
    )
