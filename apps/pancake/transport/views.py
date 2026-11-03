"""API Pancake. Cấu hình (token, gán kênh) CHỈ superuser — gán sai kênh là cơ hội
đổ vào nguồn Foxia của kênh khác. Danh sách page (GET, để trang lead lọc theo
page) và danh sách lead theo quyền `pancake-leads:view`; tạo lại đơn từ lead
(`retry_order`) theo `pancake-leads:edit`.

Không có endpoint nào trả token (tài khoản hay page) ra ngoài.
"""

from __future__ import annotations

from datetime import datetime, time
from functools import wraps

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.channels_core.models import Channel
from apps.common.phone_utils import phone_search_candidates
from apps.orders.application.services.order_ingest_service import resolve_ingest_staff_id
from apps.pancake.application.services.page_service import PageNotEligible, PancakePageService, check_eligible
from apps.pancake.infrastructure.pancake_client import PancakeClient, PancakeError, PancakeTokenError
from apps.pancake.models import (
    PancakeAuthStatus,
    PancakeConnection,
    PancakeLead,
    PancakePage,
    PancakePushStatus,
    PancakeStaffMap,
)
from apps.pancake.tasks import retry_lead_order
from apps.users.models import User
from apps.users.permissions import require_method_permission, require_section
from apps.users.rbac import resolve_effective_permissions

MAX_LIMIT = 200


def superuser_only(view):
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not resolve_effective_permissions(request.user).is_superuser:
            return Response({"detail": "Chỉ quản trị viên."}, status=status.HTTP_403_FORBIDDEN)
        return view(request, *args, **kwargs)

    return _wrapped


def _bad(detail: str) -> Response:
    return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)


def _client() -> tuple[PancakeConnection | None, PancakeClient | None]:
    """Dựng client NGAY TẠI ĐÂY (không qua page_service.get_client) để test patch
    được `views.PancakeClient` — mọi lời gọi mạng từ API đều đi qua một cửa."""
    conn = PancakeConnection.get_active()
    if conn is None or not conn.access_token:
        return conn, None
    return conn, PancakeClient(conn.access_token)


def _int_param(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _connection_out(conn: PancakeConnection | None) -> dict:
    if conn is None:
        return {"has_token": False, "auth_status": "unknown", "auth_error": "", "last_pages_synced_at": None, "name": ""}
    return {
        "has_token": bool(conn.access_token),
        "auth_status": conn.auth_status,
        "auth_error": conn.auth_error,
        "last_pages_synced_at": conn.last_pages_synced_at,
        "name": conn.name,
    }


def _page_out(page: PancakePage) -> dict:
    return {
        "id": page.id,
        "pancake_page_id": page.pancake_page_id,
        "name": page.name,
        "platform": page.platform,
        "is_activated": page.is_activated,
        "has_active_subscription": page.has_active_subscription,
        "role_in_page": page.role_in_page,
        "channel_id": page.channel_id,
        "channel_name": page.channel.name if page.channel_id else "",
        "foxia_kind": page.foxia_kind,
        "is_enabled": page.is_enabled,
        "has_token": bool(page.page_access_token),
        "token_error": page.token_error,
        "polled_until": page.polled_until,
        "last_polled_at": page.last_polled_at,
        "last_error": page.last_error,
    }


def _pages_out() -> list[dict]:
    return [_page_out(p) for p in PancakePage.objects.select_related("channel")]


def _staff_map_out(m: PancakeStaffMap) -> dict:
    return {
        "id": m.id,
        "pancake_name": m.pancake_name,
        "user_id": m.user_id,
        "user_username": m.user.username if m.user_id else "",
        "user_display_name": m.user.display_name if m.user_id else "",
        "last_seen_at": m.last_seen_at,
    }


def _lead_out(lead: PancakeLead) -> dict:
    return {
        "id": lead.id,
        "page_id": lead.page_id,
        "pancake_page_id": lead.page.pancake_page_id,
        "page_name": lead.page.name,
        "channel_id": lead.channel_id,
        "channel_name": lead.channel.name,
        "conversation_id": lead.conversation_id,
        "conversation_inserted_at": lead.conversation_inserted_at,
        "customer_name": lead.customer_name,
        "phone": lead.phone,
        "tags": lead.tags or [],
        "phone_given_at": lead.phone_given_at,
        "assignee_name": lead.assignee_name,
        "created_at": lead.created_at,
        "foxia_kind": lead.foxia_kind,
        "foxia_source_id": lead.foxia_source_id,
        "push_status": lead.push_status,
        "http_status": lead.http_status,
        "error": lead.error,
        "pushed_at": lead.pushed_at,
        "cohoi_id": lead.cohoi_id,
        "order_id": lead.order_id,
    }


# ── connection ────────────────────────────────────────────────────────────────

@extend_schema(
    request=OpenApiTypes.OBJECT,
    responses={
        200: OpenApiResponse(description="Trạng thái kết nối Pancake (không trả token)"),
        400: OpenApiResponse(description="Token rỗng hoặc Pancake từ chối"),
    },
)
@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
@superuser_only
def connection(request):
    if request.method == "GET":
        return Response(_connection_out(PancakeConnection.get_active()))

    token = str(request.data.get("access_token") or "").strip()
    if not token:
        return _bad("Thiếu access token.")
    conn = PancakeConnection.get_active() or PancakeConnection(name="Pancake")
    conn.access_token = token
    conn.is_active = True
    conn.save()
    # Kiểm tra ngay bằng cách đồng bộ page: token sai phải lộ lúc nhập, không phải
    # 3 phút sau trong log worker.
    try:
        PancakePageService(PancakeClient(token)).sync_pages(conn)
    except PancakeTokenError as exc:
        return _bad(f"Pancake từ chối token: {exc}")
    except PancakeError as exc:
        # sync_pages chỉ tự ghi auth_status khi PancakeTokenError (token sai hẳn) —
        # lỗi mạng/HTTP khác đi thẳng ra đây mà không đụng conn, để nguyên thì
        # auth_status vẫn còn phản ánh lần kiểm tra THÀNH CÔNG trước đó dù token
        # vừa nhập chưa hề được xác nhận.
        conn.auth_status = PancakeAuthStatus.ERROR
        conn.auth_error = str(exc)
        conn.save(update_fields=["auth_status", "auth_error", "updated_at"])
        return _bad(f"Không gọi được Pancake: {exc}")
    return Response(_connection_out(conn))


# ── pages ─────────────────────────────────────────────────────────────────────

@extend_schema(
    request=None,
    responses={
        200: OpenApiResponse(description="Kết quả đồng bộ page + danh sách page"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@superuser_only
def sync_pages(request):
    conn, client = _client()
    if client is None:
        return _bad("Chưa nhập access token Pancake.")
    try:
        result = PancakePageService(client).sync_pages(conn)
    except PancakeTokenError as exc:
        return _bad(f"Pancake từ chối token: {exc}")
    except PancakeError as exc:
        return _bad(f"Không gọi được Pancake: {exc}")
    return Response(
        {
            "fetched": result.fetched,
            "created": result.created,
            "updated": result.updated,
            "deactivated": result.deactivated,
            "pages": _pages_out(),
        }
    )


@extend_schema(
    request=None,
    responses={
        200: OpenApiResponse(description="Danh sách page Pancake + kênh gán + trạng thái poll"),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("pancake-leads")])
def list_pages(request):
    # Mở cho `pancake-leads:view` (không chỉ superuser): trang Lead Pancake gọi
    # endpoint này để dựng bộ lọc theo page. Payload không có token (xem `_page_out`)
    # nên không có gì để lộ ra ngoài nhóm cấu hình.
    return Response({"pages": _pages_out()})


@extend_schema(
    request=OpenApiTypes.OBJECT,
    responses={
        200: OpenApiResponse(description="Page sau cập nhật"),
        400: OpenApiResponse(description="Kênh không hợp lệ / chưa đủ điều kiện bật / Pancake từ chối"),
        404: OpenApiResponse(description="Không tìm thấy page"),
    },
)
@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
@superuser_only
def update_page(request, pk: int):
    page = PancakePage.objects.select_related("channel").filter(pk=pk).first()
    if page is None:
        return Response({"detail": "Không tìm thấy page."}, status=status.HTTP_404_NOT_FOUND)

    if "channel_id" in request.data:
        raw_channel_id = request.data.get("channel_id")
        update_fields = ["channel"]
        if raw_channel_id in (None, ""):
            page.channel = None
            # Gỡ kênh khỏi page đang bật: page không còn nơi để lead trỏ tới, phải
            # tắt theo — không thì `poll_phone_leads` (channel__isnull=False) đã
            # âm thầm bỏ qua page này trong khi UI/DB vẫn hiện is_enabled=True.
            if page.is_enabled:
                page.is_enabled = False
                update_fields.append("is_enabled")
        else:
            channel_id = _int_param(raw_channel_id)
            if channel_id is None:
                return _bad("Kênh bán không hợp lệ.")
            channel = Channel.objects.filter(pk=channel_id, deleted_at__isnull=True).first()
            if channel is None:
                return _bad("Kênh bán không tồn tại.")
            page.channel = channel
        page.save(update_fields=update_fields)

    if "is_enabled" in request.data:
        enable = bool(request.data.get("is_enabled"))
        if enable:
            # Kiểm tra điều kiện LOCAL (kênh, nền tảng có cột Foxia) trước khi đòi
            # access token: đây là lỗi nhập liệu, không phải lỗi kết nối Pancake —
            # người dùng phải thấy "chưa gán kênh" thay vì "chưa có token" dù
            # chưa từng nhập token bao giờ. Dùng chung `check_eligible` với
            # `enable_page` — một nguồn message duy nhất.
            try:
                check_eligible(page)
            except PageNotEligible as exc:
                return _bad(str(exc))
            _, client = _client()
            if client is None:
                return _bad("Chưa nhập access token Pancake.")
            try:
                PancakePageService(client).enable_page(page)
            except PageNotEligible as exc:
                return _bad(str(exc))
            except PancakeTokenError as exc:
                return _bad(f"Không sinh được token page: {exc}")
            except PancakeError as exc:
                return _bad(f"Không gọi được Pancake: {exc}")
        else:
            # Tắt chỉ hạ cờ: giữ token + cursor, bật lại thì enable_page đặt lại cursor.
            page.is_enabled = False
            page.save(update_fields=["is_enabled"])

    page.refresh_from_db()
    return Response(_page_out(page))


# ── staff maps ────────────────────────────────────────────────────────────────

def _resolve_staff_user(user_id: int) -> User | None:
    """`user_id` gửi lên → User hợp lệ để gán sale, hoặc None. Dùng chung luật với
    ingest landing (`resolve_ingest_staff_id`): active, chưa xoá mềm, không role
    hệ thống — tránh gán nhầm một tài khoản admin/đã nghỉ làm sale mặc định của
    một nhân viên Pancake."""
    user = User.objects.filter(pk=user_id).first()
    if user is None or resolve_ingest_staff_id(user.username) != user_id:
        return None
    return user


@extend_schema(
    request=OpenApiTypes.OBJECT,
    responses={
        200: OpenApiResponse(description="Danh sách ánh xạ nhân viên Pancake → user"),
        400: OpenApiResponse(description="items không hợp lệ hoặc user_id không hợp lệ"),
    },
)
@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
@superuser_only
def staff_maps(request):
    if request.method == "GET":
        items = PancakeStaffMap.objects.select_related("user")
        return Response({"items": [_staff_map_out(m) for m in items]})

    raw_items = request.data.get("items")
    if not isinstance(raw_items, list):
        return _bad("items phải là danh sách.")

    updates: dict[int, int | None] = {}
    for raw in raw_items:
        if not isinstance(raw, dict) or "id" not in raw:
            return _bad("items không hợp lệ.")
        map_id = _int_param(raw.get("id"))
        if map_id is None:
            return _bad("id không hợp lệ.")
        raw_user_id = raw.get("user_id")
        if raw_user_id is None:
            updates[map_id] = None
            continue
        user_id = _int_param(raw_user_id)
        if user_id is None:
            return _bad("user_id không hợp lệ.")
        updates[map_id] = user_id

    maps = {m.id: m for m in PancakeStaffMap.objects.filter(id__in=updates.keys())}
    if len(maps) != len(updates):
        return _bad("Có ánh xạ không tồn tại.")

    # Kiểm hợp lệ TRƯỚC khi ghi bất kỳ dòng nào — sai một user_id thì không
    # ghi nửa chừng danh sách.
    resolved: dict[int, User] = {}
    for user_id in set(updates.values()):
        if user_id is None:
            continue
        user = _resolve_staff_user(user_id)
        if user is None:
            return _bad(f"user_id {user_id} không hợp lệ (không active, đã xoá hoặc role hệ thống).")
        resolved[user_id] = user

    for map_id, user_id in updates.items():
        m = maps[map_id]
        m.user = resolved[user_id] if user_id is not None else None
        m.save(update_fields=["user", "updated_at"])

    items = PancakeStaffMap.objects.select_related("user").filter(id__in=updates.keys())
    return Response({"items": [_staff_map_out(m) for m in items]})


# ── leads ─────────────────────────────────────────────────────────────────────

def _parse_day(value: str, end: bool) -> datetime | None:
    day = parse_date(value or "")
    if day is None:
        return None
    return timezone.make_aware(datetime.combine(day, time.max if end else time.min))


@extend_schema(
    request=None,
    parameters=[
        OpenApiParameter("page", OpenApiTypes.INT, description="Lọc theo id page"),
        OpenApiParameter("channel", OpenApiTypes.INT, description="Lọc theo id kênh bán"),
        OpenApiParameter(
            "push_status", OpenApiTypes.STR,
            description="pending | ok | failed | skipped_no_mapping | order_created | order_failed | order_exists",
        ),
        OpenApiParameter("q", OpenApiTypes.STR, description="Tìm theo sđt (chứa) hoặc tên (không phân biệt hoa thường)"),
        OpenApiParameter("date_from", OpenApiTypes.DATE, description="created_at >= ngày"),
        OpenApiParameter("date_to", OpenApiTypes.DATE, description="created_at <= ngày"),
        OpenApiParameter("limit", OpenApiTypes.INT, description="Mặc định 50, tối đa 200"),
        OpenApiParameter("offset", OpenApiTypes.INT),
    ],
    responses={
        200: OpenApiResponse(description="{items, total, limit, offset}"),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("pancake-leads")])
def list_leads(request):
    qs = PancakeLead.objects.select_related("page", "channel").order_by("-created_at", "-id")
    params = request.query_params
    if (page_id := _int_param(params.get("page"))) is not None:
        qs = qs.filter(page_id=page_id)
    if (channel_id := _int_param(params.get("channel"))) is not None:
        qs = qs.filter(channel_id=channel_id)
    if params.get("push_status") in PancakePushStatus.values:
        qs = qs.filter(push_status=params["push_status"])
    q = str(params.get("q") or "").strip()
    if q:
        # Ô tìm nhận cả sđt gõ dạng quốc tế (+84 912 345 678) lẫn tên — dùng chung
        # `phone_search_candidates` với apps.orders để "+84 912 345 678" khớp được
        # `phone` lưu dạng nội bộ "0912345678". Rỗng (từ khoá có chữ) → chỉ lọc tên.
        phone_q = Q()
        for candidate in phone_search_candidates(q):
            phone_q |= Q(phone__contains=candidate)
        qs = qs.filter(phone_q | Q(customer_name__icontains=q))
    if (start := _parse_day(params.get("date_from"), end=False)) is not None:
        qs = qs.filter(created_at__gte=start)
    if (stop := _parse_day(params.get("date_to"), end=True)) is not None:
        qs = qs.filter(created_at__lte=stop)

    try:
        limit = max(1, min(int(params.get("limit", 50)), MAX_LIMIT))
        offset = max(0, int(params.get("offset", 0)))
    except (TypeError, ValueError):
        return _bad("limit/offset không hợp lệ.")
    total = qs.count()
    items = [_lead_out(lead) for lead in qs[offset : offset + limit]]
    return Response({"items": items, "total": total, "limit": limit, "offset": offset})


@extend_schema(
    request=None,
    responses={
        202: OpenApiResponse(description="Đã xếp lịch tạo lại đơn"),
        404: OpenApiResponse(description="Không tìm thấy lead"),
        409: OpenApiResponse(description="Lead đã có đơn hoặc không ở trạng thái tạo lại được"),
    },
)
@api_view(["POST"])
# Xếp lịch TẠO ĐƠN THẬT từ lead = hành vi ghi → gate `pancake-leads:edit`, không đi
# chung :view (audit 2026-09-23 M1; migration users/0052 grandfather :edit cho role
# đang có :view nên hành vi prod không đổi ngày deploy).
@permission_classes([IsAuthenticated, require_method_permission(POST="pancake-leads:edit")])
def retry_order(request, pk: int):
    lead = PancakeLead.objects.filter(pk=pk).first()
    if lead is None:
        return Response({"detail": "Không tìm thấy lead."}, status=status.HTTP_404_NOT_FOUND)
    eligible_statuses = (
        PancakePushStatus.PENDING,
        PancakePushStatus.FAILED,
        PancakePushStatus.SKIPPED_NO_MAPPING,
        PancakePushStatus.ORDER_FAILED,
    )
    # Lead đã có order (kể cả order_exists) không tạo lại — order_id is not None
    # là điều kiện đứng trước push_status: một lead order_exists "nghe như" đã
    # xong chứ không phải lỗi, nhưng vẫn phải chặn ở đây vì nó đã có order_id.
    if lead.order_id is not None or lead.push_status not in eligible_statuses:
        return Response(
            {"detail": "Lead đã có đơn hoặc không ở trạng thái tạo lại đơn được."},
            status=status.HTTP_409_CONFLICT,
        )
    retry_lead_order.delay(lead.id)
    return Response({"status": "queued"}, status=status.HTTP_202_ACCEPTED)
