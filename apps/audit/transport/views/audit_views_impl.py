from datetime import datetime, time as dt_time, timedelta

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_serializer,
)
from rest_framework import viewsets, filters, status
from rest_framework import serializers as drf_serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from apps.audit.models import AuditLog
from apps.audit.serializers import AuditLogListSerializer
from apps.common.db_guards import statement_timeout
from apps.common.pagination import parse_required_pagination
from apps.users.permissions import require_section
from apps.users.rbac import resolve_effective_permissions

_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500

# `search` BẮT BUỘC đi kèm khoảng thời gian (bounded scan): bảng audit_logs
# ~4.1GB/2.19M dòng, ILIKE không chặn cửa sổ từng làm timeout cả trang.
# Index composite `(-timestamp)` cắt tập quét trước, ILIKE chỉ chạy trong cửa sổ.
_SEARCH_MAX_WINDOW_DAYS = 31
# Van an toàn tầng DB cho nhánh search: nếu cửa sổ 31 ngày vẫn quá dày
# (giờ cao điểm log API), query tự bị hủy thay vì treo worker + giữ connection.
_SEARCH_STATEMENT_TIMEOUT_SECONDS = 15

_SEARCH_REQUIRES_RANGE_MSG = (
    "Tìm kiếm bắt buộc đi kèm khoảng thời gian: chọn 'Từ ngày' "
    f"(timestamp__gte) trước, tối đa {_SEARCH_MAX_WINDOW_DAYS} ngày."
)
_SEARCH_WINDOW_TOO_WIDE_MSG = (
    f"Khoảng thời gian tìm kiếm tối đa {_SEARCH_MAX_WINDOW_DAYS} ngày. "
    "Thu hẹp 'Từ ngày'/'Đến ngày' rồi thử lại."
)
_SEARCH_BAD_TIMESTAMP_MSG = (
    "timestamp__gte/timestamp__lte không hợp lệ: dùng định dạng ISO "
    "(YYYY-MM-DD hoặc YYYY-MM-DDTHH:MM:SS)."
)


def _parse_ts_param(raw: str) -> datetime | None:
    """Parse param timestamp theo đúng 2 dạng FE gửi: date thuần và ISO datetime.

    Trả None nếu không parse được. Giá trị naive được gắn timezone hiện hành để
    so sánh được với ``timezone.now()`` (aware).
    """
    parsed = parse_datetime(raw)
    if parsed is None:
        as_date = parse_date(raw)
        if as_date is None:
            return None
        parsed = datetime.combine(as_date, dt_time.min)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _validate_search_window(query_params) -> Response | None:
    """Luật bounded scan cho `search`: bắt buộc có `timestamp__gte` và cửa sổ
    [gte, lte-hoặc-now] không quá ``_SEARCH_MAX_WINDOW_DAYS`` ngày."""
    gte_raw = query_params.get("timestamp__gte", "").strip()
    if not gte_raw:
        return Response(
            {"detail": _SEARCH_REQUIRES_RANGE_MSG},
            status=status.HTTP_400_BAD_REQUEST,
        )
    gte = _parse_ts_param(gte_raw)
    if gte is None:
        return Response(
            {"detail": _SEARCH_BAD_TIMESTAMP_MSG},
            status=status.HTTP_400_BAD_REQUEST,
        )
    lte_raw = query_params.get("timestamp__lte", "").strip()
    if lte_raw:
        lte = _parse_ts_param(lte_raw)
        if lte is None:
            return Response(
                {"detail": _SEARCH_BAD_TIMESTAMP_MSG},
                status=status.HTTP_400_BAD_REQUEST,
            )
    else:
        # Không có trần thì cửa sổ kết thúc ở hiện tại — log không ghi tương lai
        # nên đây chính là biên phải thực tế của tập quét.
        lte = timezone.now()
    if lte - gte > timedelta(days=_SEARCH_MAX_WINDOW_DAYS):
        return Response(
            {"detail": _SEARCH_WINDOW_TOO_WIDE_MSG},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


# many=False: action `list` của ViewSet bị heuristic `_is_list_view` của
# drf-spectacular bọc response thành array — override để giữ đúng shape object
# {items,total,limit,offset}.
@extend_schema_serializer(many=False)
class AuditLogListResponseSerializer(drf_serializers.Serializer):
    """Schema-only: mô tả envelope phân trang của GET /api/audit-logs/."""

    items = AuditLogListSerializer(many=True)
    total = drf_serializers.IntegerField()
    limit = drf_serializers.IntegerField()
    offset = drf_serializers.IntegerField()


def _is_audit_admin(user) -> bool:
    """Admin của ứng dụng (RBAC superuser) hoặc Django superuser xem được toàn bộ log."""
    return resolve_effective_permissions(user).is_superuser or user.is_superuser


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint để xem audit logs
    Chỉ cho phép đọc, không cho phép chỉnh sửa
    """
    serializer_class = AuditLogListSerializer
    permission_classes = [IsAuthenticated, require_section("audit")]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    # `timestamp` có thêm gte/lte: FE gửi `timestamp__gte`/`timestamp__lte` từ
    # trước nhưng dạng list chỉ sinh lookup exact nên 2 param này bị django_filter
    # lặng lẽ bỏ qua — khoảng ngày trên UI không lọc gì cả. Dạng dict giữ nguyên
    # exact cũ và kích hoạt 2 lookup FE đang gửi (index `-timestamp` phủ được).
    filterset_fields = {
        'user': ['exact'],
        'action_type': ['exact'],
        'entity_type': ['exact'],
        'entity_id': ['exact'],
        'source': ['exact'],
        'timestamp': ['exact', 'gte', 'lte'],
    }
    ordering_fields = ['timestamp']
    ordering = ['-timestamp']
    # Phân trang limit/offset tự parse trong list() (chuẩn chung của repo:
    # apps.common.pagination) — không dùng pagination_class của DRF.
    pagination_class = None

    def get_queryset(self):
        """
        Người dùng bình thường chỉ xem được log của chính mình
        Admin có thể xem tất cả
        """
        # Schema generation chạy với AnonymousUser → trả queryset rỗng để
        # spectacular suy được model (hết warning "Failed to obtain model").
        if getattr(self, "swagger_fake_view", False):
            return AuditLog.objects.none()

        queryset = AuditLog.objects.select_related('user')

        if _is_audit_admin(self.request.user):
            return queryset
        return queryset.filter(user=self.request.user)

    @extend_schema(
        parameters=[
            OpenApiParameter("limit", int, description=f"Số dòng mỗi trang (mặc định {_DEFAULT_LIMIT}, tối đa {_MAX_LIMIT})."),
            OpenApiParameter("offset", int, description="Vị trí bắt đầu (mặc định 0)."),
            OpenApiParameter(
                "search",
                str,
                description=(
                    "Tìm chuỗi con (không phân biệt hoa thường) trên "
                    "entity_id, username người thao tác và ip_address. "
                    "BẮT BUỘC đi kèm timestamp__gte; cửa sổ "
                    f"[timestamp__gte, timestamp__lte] tối đa {_SEARCH_MAX_WINDOW_DAYS} ngày "
                    "(bounded scan trên bảng lớn)."
                ),
            ),
        ],
        responses={
            200: AuditLogListResponseSerializer,
            400: OpenApiResponse(
                description=(
                    "limit/offset không hợp lệ; hoặc search thiếu timestamp__gte / "
                    f"khoảng thời gian vượt {_SEARCH_MAX_WINDOW_DAYS} ngày"
                )
            ),
        },
    )
    def list(self, request, *args, **kwargs):
        """Danh sách audit log phân trang {items,total,limit,offset}."""
        pagination, error_response = parse_required_pagination(
            request, max_limit=_MAX_LIMIT, default_limit=_DEFAULT_LIMIT
        )
        if error_response is not None:
            return error_response

        search = request.query_params.get("search", "").strip()
        if search:
            error_response = _validate_search_window(request.query_params)
            if error_response is not None:
                return error_response

        # Tối ưu truy vấn: lấy các field cần thiết
        queryset = self.get_queryset().only(
            'id', 'user_id', 'action_type', 'entity_type', 'entity_id',
            'method', 'timestamp', 'ip_address', 'duration_ms', 'source',
            'curl_command'
        )

        # Áp dụng filter
        queryset = self.filter_queryset(queryset)

        if search:
            # Cột search chọn theo model AuditLog (đọc 2026-08-20):
            # - entity_id (CharField): mã đơn/khách/job bị tác động — manh mối
            #   điều tra số 1.
            # - user__username: truy theo người thao tác khi chỉ nhớ một phần tên.
            # - ip_address (inet, Django tự cast HOST()::text): truy đăng nhập/
            #   thao tác từ IP lạ.
            # Model KHÔNG có cột `path` — URL nằm trong curl_command, mà
            # curl_command/response_data/metadata là cột nặng cố tình không đụng
            # (TextField/JSONField chứa nguyên body request, ILIKE trên đó là
            # quét TOAST cả bảng).
            # Dùng `icontains` thay `__ilike_contains` (apps.common.db_lookups):
            # audit_logs chưa có index trgm nào (migrations chỉ khai báo B-tree)
            # nên UPPER(col) LIKE hay col ILIKE đều là scan tuần tự — nhưng đã bị
            # chặn trong cửa sổ ≤31 ngày theo index `-timestamp` nên chấp nhận
            # được. Nếu sau này thêm gin_trgm_ops trên entity_id thì đổi sang
            # `entity_id__ilike_contains` để planner dùng index.
            queryset = queryset.filter(
                Q(entity_id__icontains=search)
                | Q(user__username__icontains=search)
                | Q(ip_address__icontains=search)
            )

        # `total` = COUNT thật. Có filter (user/action_type/.../timestamp) thì
        # COUNT đi theo các index composite `(*, -timestamp)` — rẻ. Không filter
        # thì COUNT quét index `-timestamp` (~2.19M dòng, index-only scan) — đo
        # chấp nhận được cho trang ít truy cập này. Nếu sau này thành điểm nóng
        # thì thay nhánh không-filter bằng ước lượng reltuples.
        offset, limit = pagination["offset"], pagination["limit"]
        if search:
            # Nhánh search: ILIKE trong cửa sổ ngày vẫn có thể dày — ép COUNT và
            # trang kết quả vật hoá NGAY TRONG guard để timeout áp đúng 2 query này.
            with statement_timeout(_SEARCH_STATEMENT_TIMEOUT_SECONDS):
                total = queryset.count()
                page = list(queryset[offset:offset + limit])
        else:
            total = queryset.count()
            page = queryset[offset:offset + limit]

        serializer = self.get_serializer(page, many=True)
        return Response(
            {
                "items": serializer.data,
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    # `_EXPORT_COLUMNS`/`_EXPORT_MAX_ROWS`: action `export` (CSV đồng bộ,
    # `GET /api/audit-logs/export/`) đã xoá ở Task 18. Giữ lại 2 hằng này vì
    # `apps.audit.exports_definitions.AuditLogsExport` (luồng export chạy nền)
    # vẫn đọc `AuditLogViewSet._EXPORT_COLUMNS`/`._EXPORT_MAX_ROWS` để mirror
    # đúng bộ cột + trần của luồng đồng bộ cũ — xoá theo sẽ làm definition đó
    # crash ở import time.
    _EXPORT_COLUMNS = (
        "id", "user", "action_type", "entity_type", "entity_id",
        "method", "timestamp", "ip_address", "duration_ms", "source",
    )
    _EXPORT_MAX_ROWS = 10_000
