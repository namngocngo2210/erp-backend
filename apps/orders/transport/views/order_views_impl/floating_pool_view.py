"""API Kho số thả nổi: GET/PUT config (Task 7), GET entries list (Task 8), POST claim (Task 9)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.phone_utils import normalize_phone
from apps.orders.application.services.floating_pool_service import (
    ClaimError,
    FloatingPoolService,
    mask_phone,
)
from apps.orders.crm_catalog import SALES_STATUS_OPTIONS
from apps.orders.models import FloatingPoolEntry, OrderAssessment
from apps.users.auth_core import user_has_any_permission
from apps.users.permissions import (
    require_method_permission,
    require_permission,
    require_section,
)

def _local_midnight(d: date) -> datetime:
    """00:00 giờ local của ngày ``d`` (aware) — để lọc nửa-mở trên cột trần."""
    return timezone.make_aware(datetime.combine(d, time.min))


_CONFIG_FIELDS = (
    "enabled", "stale_days", "saved_status_codes", "max_float_count",
    "daily_claim_limit", "max_order_age_days", "hide_phone",
)


class _ConfigSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    stale_days = serializers.IntegerField(min_value=1, max_value=365)
    saved_status_codes = serializers.ListField(
        child=serializers.CharField(max_length=64), allow_empty=True
    )
    max_float_count = serializers.IntegerField(min_value=1, max_value=1000)
    daily_claim_limit = serializers.IntegerField(min_value=0, max_value=1000)
    max_order_age_days = serializers.IntegerField(min_value=0, max_value=3650)
    hide_phone = serializers.BooleanField()

    def validate_saved_status_codes(self, codes):
        valid = {o.code for o in SALES_STATUS_OPTIONS}
        bad = sorted(set(codes) - valid)
        if bad:
            raise serializers.ValidationError(f"Mã trạng thái không hợp lệ: {', '.join(bad)}")
        return codes


def _serialize_config(config) -> dict:
    data = {f: getattr(config, f) for f in _CONFIG_FIELDS}
    data["saved_status_options"] = [
        {"code": o.code, "label": o.label} for o in SALES_STATUS_OPTIONS
    ]
    return data


@extend_schema(
    request=_ConfigSerializer,
    responses={
        200: inline_serializer(
            "FloatingPoolConfigResponse",
            fields={
                "enabled": drf_serializers.BooleanField(),
                "stale_days": drf_serializers.IntegerField(),
                "saved_status_codes": drf_serializers.ListField(child=drf_serializers.CharField()),
                "max_float_count": drf_serializers.IntegerField(),
                "daily_claim_limit": drf_serializers.IntegerField(),
                "max_order_age_days": drf_serializers.IntegerField(),
                "hide_phone": drf_serializers.BooleanField(),
                "saved_status_options": inline_serializer(
                    "FloatingPoolStatusOption", many=True,
                    fields={"code": drf_serializers.CharField(), "label": drf_serializers.CharField()},
                ),
            },
        ),
    },
)
@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated, require_method_permission(GET="floating-pool:view", PUT="floating-pool:edit")])
def floating_pool_config(request):
    config = FloatingPoolService.get_config()
    if request.method == "GET":
        return Response(_serialize_config(config))
    if not user_has_any_permission(request.user, "floating-pool:edit"):
        return Response({"detail": "Cần quyền floating-pool:edit."}, status=status.HTTP_403_FORBIDDEN)
    serializer = _ConfigSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    for field, value in serializer.validated_data.items():
        setattr(config, field, value)
    config.updated_by = request.user
    config.save()
    return Response(_serialize_config(config))


@extend_schema(
    parameters=[
        OpenApiParameter("page", int), OpenApiParameter("page_size", int),
        OpenApiParameter("q", str), OpenApiParameter("entered_from", str),
        OpenApiParameter("entered_to", str), OpenApiParameter("channel", str),
    ],
    responses={
        200: inline_serializer(
            "FloatingPoolEntriesResponse",
            fields={
                "items": inline_serializer(
                    "FloatingPoolEntryItem", many=True,
                    fields={
                        "id": drf_serializers.IntegerField(),
                        "order_id": drf_serializers.IntegerField(),
                        "channel_name": drf_serializers.CharField(),
                        "order_source": drf_serializers.CharField(allow_null=True),
                        "customer_name": drf_serializers.CharField(),
                        "phone": drf_serializers.CharField(),
                        "customer_is_new": drf_serializers.BooleanField(allow_null=True),
                        "sales_status_code": drf_serializers.CharField(allow_null=True),
                        "sales_status_label": drf_serializers.CharField(allow_null=True),
                        "previous_staff_name": drf_serializers.CharField(allow_null=True),
                        "previous_staff_department": drf_serializers.CharField(allow_null=True),
                        "float_count": drf_serializers.IntegerField(),
                        "entered_at": drf_serializers.DateTimeField(),
                    },
                ),
                "total": drf_serializers.IntegerField(),
                "page": drf_serializers.IntegerField(),
                "page_size": drf_serializers.IntegerField(),
                "claimed_today": drf_serializers.IntegerField(),
                "daily_claim_limit": drf_serializers.IntegerField(),
            },
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("floating-pool")])
def floating_pool_entries(request):
    config = FloatingPoolService.get_config()
    qs = (
        FloatingPoolEntry.objects.filter(status=FloatingPoolEntry.Status.FLOATING)
        .select_related(
            "order__channel", "order__customer", "order__assessment",
            "previous_staff__department",
        )
        .order_by("-entered_at", "-id")
    )
    show_full = (not config.hide_phone) or user_has_any_permission(request.user, "floating-pool:edit")
    q = (request.query_params.get("q") or "").strip()
    if q:
        cond = Q(order__name__icontains=q)
        if show_full:
            # Chống dò số: viewer bị che số thì không được dò membership qua search exact.
            phone_exact = normalize_phone(q)
            if phone_exact:
                cond |= Q(order__phone_normalized=phone_exact)
        qs = qs.filter(cond)
    entered_from = request.query_params.get("entered_from")
    entered_to = request.query_params.get("entered_to")
    # Nửa-mở [00:00 from, 00:00 to+1) trên CỘT TRẦN thay cho `__date`: Django dịch
    # `__date` thành `(col AT TIME ZONE ...)::date` — biểu thức đó làm index
    # `floating_status_entered_idx` (status, entered_at) vô dụng. Cùng luật đã ghi
    # ở apps/inventory/.../movement_window.py (audit 2026-07-28 DB-15).
    if entered_from:
        qs = qs.filter(
            entered_at__gte=_local_midnight(
                serializers.DateField().to_internal_value(entered_from)
            )
        )
    if entered_to:
        qs = qs.filter(
            entered_at__lt=_local_midnight(
                serializers.DateField().to_internal_value(entered_to) + timedelta(days=1)
            )
        )
    channel = (request.query_params.get("channel") or "").strip()
    if channel:
        qs = qs.filter(order__channel__code=channel)

    try:
        page = max(1, int(request.query_params.get("page", 1)))
        page_size = min(100, max(1, int(request.query_params.get("page_size", 20))))
    except ValueError:
        return Response({"detail": "page/page_size không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)
    total = qs.count()
    rows = list(qs[(page - 1) * page_size : page * page_size])

    label_by_code = {o.code: o.label for o in SALES_STATUS_OPTIONS}
    items = []
    for e in rows:
        order = e.order
        try:
            code = order.assessment.sales_status_code
        except OrderAssessment.DoesNotExist:
            code = None
        staff = e.previous_staff
        items.append({
            "id": e.id,
            "order_id": e.order_id,
            "channel_name": order.channel.name,
            "order_source": order.order_source,
            "customer_name": order.name,
            "phone": order.phone if show_full else mask_phone(order.phone),
            "customer_is_new": order.customer.is_new_customer if order.customer_id else None,
            "sales_status_code": code,
            "sales_status_label": label_by_code.get(code) if code else None,
            "previous_staff_name": (staff.display_name or staff.username) if staff else None,
            "previous_staff_department": staff.department.name if staff and staff.department_id else None,
            "float_count": e.float_count,
            "entered_at": e.entered_at,
        })
    return Response({
        "items": items, "total": total, "page": page, "page_size": page_size,
        "claimed_today": FloatingPoolService.claimed_today(request.user),
        "daily_claim_limit": config.daily_claim_limit,
    })


@extend_schema(
    request=None,
    responses={
        200: inline_serializer(
            "FloatingPoolClaimResponse",
            fields={
                "order_id": drf_serializers.IntegerField(),
                "claimed_today": drf_serializers.IntegerField(),
                "daily_claim_limit": drf_serializers.IntegerField(),
            },
        ),
    },
)
# Nhận số từ pool GHI staff mới lên đơn → floating-pool:edit (sweep N2 giai đoạn
# 2). Nhận số là nghiệp vụ hằng ngày của MỌI sale có floating-pool:view, nên
# grandfather 0039 cấp :edit cho các role đó — ai đang nhận được vẫn nhận được
# ngày deploy; muốn tách "xem mà không nhận" thì thu hồi :edit per-role trên UI.
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("floating-pool:edit")])
def floating_pool_claim(request, entry_id: int):
    config = FloatingPoolService.get_config()
    try:
        order = FloatingPoolService.claim(entry_id, request.user)
    except FloatingPoolEntry.DoesNotExist:
        return Response({"detail": "Không tìm thấy số trong kho."}, status=status.HTTP_404_NOT_FOUND)
    except ClaimError as exc:
        return Response({"detail": str(exc)}, status=exc.status_code)
    return Response({
        "order_id": order.id,
        "claimed_today": FloatingPoolService.claimed_today(request.user),
        "daily_claim_limit": config.daily_claim_limit,
    })
