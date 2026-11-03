from django.shortcuts import get_object_or_404
from django.core.cache import cache
from django.db.models import ProtectedError
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django.apps import apps as django_apps

from apps.common.pagination import parse_limit, parse_required_pagination
from apps.channels_core.models import Channel, ChannelType, UserChannel
from apps.channels_core.cache_utils import bump_channels_cache_version, get_channels_cache_version
from apps.channels_core.repositories import ChannelRepository
from apps.channels_core.serializers import (
    ChannelAssignmentSerializer,
    ChannelBulkDeleteSerializer,
    ChannelCreateSerializer,
    ChannelSerializer,
    ChannelTypeCreateSerializer,
    ChannelTypeSerializer,
    ChannelTypeUpdateSerializer,
    ChannelUpdateSerializer,
    KiotVietChannelCreateSerializer,
    KiotVietChannelSerializer,
    KiotVietChannelUpdateSerializer,
    StaffUserSerializer,
)
from apps.channels_core.services import ChannelService
from apps.users.constants import DepartmentID
from apps.users.department_membership import user_in_department
from apps.users.models import User
from apps.users.permissions import (
    ORDER_SECTIONS,
    require_method_permission,
    require_section,
    user_has_permission,
)
from apps.users.rbac import resolve_effective_permissions


def _parse_required_pagination(request):
    # Thin wrapper kept for backward-compatible imports; logic lives in apps.common.
    return parse_required_pagination(request)



# Trần dòng cho GET /channels/staff (audit 2026-09-23 L6).
_STAFF_MAX_LIMIT = 500

@extend_schema(
    request=ChannelCreateSerializer,
    responses={
        200: ChannelSerializer(many=True),  # GET: danh sách kênh
        201: ChannelSerializer,  # POST: kênh vừa tạo
        403: OpenApiResponse(description="Không có quyền quản lí kênh"),
    },
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def channels_collection(request):
    if request.method == "GET":
        pagination, error_response = _parse_required_pagination(request)
        if error_response is not None:
            return error_response

        version = get_channels_cache_version()
        cache_key = (
            f"channels:list:v{version}:u{request.user.id}:"
            f"limit={pagination['limit']}|offset={pagination['offset']}"
        )
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return Response(cached_data)

        queryset = ChannelService.list_channels(
            request.user,
            limit=pagination["limit"],
            offset=pagination["offset"],
        )
        data = ChannelSerializer(queryset, many=True).data
        cache.set(cache_key, data, timeout=30)
        return Response(data)

    if not ChannelService.can_manage(request.user):
        return Response({"detail": "Bạn không có quyền quản lí kênh."}, status=status.HTTP_403_FORBIDDEN)

    serializer = ChannelCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    channel = ChannelRepository.create_channel(**serializer.validated_data)
    bump_channels_cache_version()
    return Response(ChannelSerializer(channel).data, status=status.HTTP_201_CREATED)


@extend_schema(
    request=ChannelUpdateSerializer,
    responses={
        200: ChannelSerializer,  # PATCH: kênh sau cập nhật
        204: OpenApiResponse(description="DELETE thành công"),
        403: OpenApiResponse(description="Không có quyền quản lí kênh"),
    },
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def channel_detail(request, channel_id: int):
    if not ChannelService.can_manage(request.user):
        return Response({"detail": "Bạn không có quyền quản lí kênh."}, status=status.HTTP_403_FORBIDDEN)

    channel = get_object_or_404(Channel, id=channel_id, deleted_at__isnull=True)
    if request.method == "DELETE":
        ChannelRepository.soft_delete_channel(channel)
        bump_channels_cache_version()
        return Response(status=status.HTTP_204_NO_CONTENT)
    serializer = ChannelUpdateSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    updated = ChannelRepository.update_channel(channel, **serializer.validated_data)
    bump_channels_cache_version()
    return Response(ChannelSerializer(updated).data)


@extend_schema(
    request=ChannelBulkDeleteSerializer,
    responses={
        200: inline_serializer(
            "ChannelBulkDeleteResponse",
            fields={"deleted_count": drf_serializers.IntegerField()},
        ),
        403: OpenApiResponse(description="Không có quyền quản lí kênh"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def bulk_delete_channels(request):
    if not ChannelService.can_manage(request.user):
        return Response({"detail": "Bạn không có quyền quản lí kênh."}, status=status.HTTP_403_FORBIDDEN)

    serializer = ChannelBulkDeleteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    deleted_count = ChannelRepository.soft_delete_channels_by_ids(serializer.validated_data["ids"])
    bump_channels_cache_version()
    return Response({"deleted_count": deleted_count}, status=status.HTTP_200_OK)


# Quyền theo action thay vì gate chung `channels:view` cho mọi method: sửa/xoá
# danh mục là hành vi quản trị (audit 2026-08-19 N2 — endpoint ghi không được
# gác bằng `:view`).
# GET mở cho cả các section đơn hàng: ô lọc "Loại kênh" trên trang Đơn/Landing/KOC
# cần danh mục này, mà nhân viên sale không có `channels:view`. Danh mục chỉ là tên
# nền tảng (Shopee/TikTok/...), không phải dữ liệu nhạy cảm.
_CT_VIEW = ("channels:view", *(f"{section}:view" for section in ORDER_SECTIONS))
_CT_MANAGE_VIEW = ("channels:view",)
_CT_CREATE = ("channels:create",)
_CT_EDIT = ("channels:edit",)
_CT_DELETE = ("channels:delete",)

_TRUTHY = {"1", "true", "yes", "on"}


@extend_schema(
    parameters=[
        OpenApiParameter(
            name="include_inactive",
            type=bool,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Trả về cả loại kênh đã tắt. Chỉ dùng cho màn quản lý danh mục; "
                "dropdown chọn loại kênh phải để trống để chỉ nhận loại đang bật."
            ),
        ),
    ],
    request=ChannelTypeCreateSerializer,
    responses={
        200: ChannelTypeSerializer(many=True),
        201: ChannelTypeSerializer,
        403: OpenApiResponse(description="Không có quyền xem/tạo loại kênh"),
    },
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_method_permission(GET=_CT_VIEW, POST=_CT_CREATE)])
def channel_types_collection(request):
    """Danh mục loại kênh bán (Shopee/TikTok/Facebook/Web).

    Không phân trang: bảng tra cứu đóng, vài dòng — thêm limit/offset chỉ tổ làm
    client phải ghép trang cho một dropdown.
    """
    if request.method == "GET":
        include_inactive = str(request.query_params.get("include_inactive", "")).lower() in _TRUTHY
        # Loại đã tắt chỉ dành cho màn quản lý danh mục. Người chỉ có quyền trang đơn
        # (được vào đây để lấy option cho ô lọc) không được nới thêm cờ này.
        if include_inactive and not user_has_permission(request.user, *_CT_MANAGE_VIEW):
            include_inactive = False
        version = get_channels_cache_version()
        # Cờ include_inactive PHẢI nằm trong cache key: hai tập kết quả khác nhau
        # dùng chung khoá thì màn quản lý và dropdown ăn cache của nhau.
        cache_key = f"channels:types:v{version}:all={int(include_inactive)}"
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return Response(cached_data)

        queryset = ChannelRepository.list_channel_types(include_inactive=include_inactive)
        data = ChannelTypeSerializer(queryset, many=True).data
        cache.set(cache_key, data, timeout=30)
        return Response(data)

    serializer = ChannelTypeCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    channel_type = serializer.save()
    bump_channels_cache_version()
    return Response(ChannelTypeSerializer(channel_type).data, status=status.HTTP_201_CREATED)


@extend_schema(
    request=ChannelTypeUpdateSerializer,
    responses={
        200: ChannelTypeSerializer,
        204: OpenApiResponse(description="Đã xoá loại kênh"),
        400: OpenApiResponse(description="Loại kênh đang được kênh sử dụng, không xoá được"),
        403: OpenApiResponse(description="Không có quyền sửa/xoá loại kênh"),
        404: OpenApiResponse(description="Không tìm thấy loại kênh"),
    },
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_method_permission(PATCH=_CT_EDIT, DELETE=_CT_DELETE)])
def channel_type_detail(request, channel_type_id: int):
    channel_type = get_object_or_404(ChannelType, id=channel_type_id)

    if request.method == "DELETE":
        # FK để PROTECT (xem docs/channel-type.md §2.2): xoá loại đang dùng phải
        # báo lỗi nghiệp vụ chứ không được âm thầm bỏ phân loại hàng loạt kênh.
        # Đếm trước để nêu đúng con số, nhưng vẫn bắt ProtectedError vì kênh có
        # thể được gán xen vào giữa lúc đếm và lúc xoá.
        in_use = channel_type.channels.filter(deleted_at__isnull=True).count()
        if in_use:
            return Response(
                {
                    "detail": (
                        f"Còn {in_use} kênh đang dùng loại này. "
                        "Hãy đổi loại cho các kênh đó trước, hoặc tắt loại kênh thay vì xoá."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            channel_type.delete()
        except ProtectedError:
            return Response(
                {"detail": "Loại kênh vừa được gán cho một kênh khác, không xoá được."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        bump_channels_cache_version()
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = ChannelTypeUpdateSerializer(channel_type, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    updated = serializer.save()
    bump_channels_cache_version()
    return Response(ChannelTypeSerializer(updated).data)


@extend_schema(
    responses={
        200: inline_serializer(
            "ChannelForKiotvietResponse",
            many=True,
            fields={
                "id": drf_serializers.IntegerField(),
                "name": drf_serializers.CharField(),
                "code": drf_serializers.CharField(),
            },
        ),
    },
)
@api_view(["GET"])
# Danh sách kênh bán cho form đơn — gate theo các section có form dùng nó thay vì mở
# cho mọi user đăng nhập (audit 2026-07-14 vòng 5).
@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS, "lead-assignment")])
def channel_list_for_kiotviet(request):
    """Get list of all active channels for selection in KiotViet form."""
    pagination, error_response = _parse_required_pagination(request)
    if error_response is not None:
        return error_response

    version = get_channels_cache_version()
    cache_key = (
        f"channels:list-for-kiotviet:v{version}:"
        f"limit={pagination['limit']}|offset={pagination['offset']}"
    )
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return Response(cached_data)

    # Get all active channels that are not deleted
    queryset = Channel.objects.filter(
        is_active=True,
        deleted_at__isnull=True
    ).order_by("name")[pagination["offset"] : pagination["offset"] + pagination["limit"]]

    # Serialize only the id and name for selection
    data = [
        {"id": channel.id, "name": channel.name, "code": channel.code}
        for channel in queryset
    ]

    cache.set(cache_key, data, timeout=30)
    return Response(data)


@extend_schema(
    responses={
        200: inline_serializer(
            "MyChannelAssignmentResponse",
            many=True,
            fields={
                "id": drf_serializers.IntegerField(),
                "code": drf_serializers.CharField(),
                "name": drf_serializers.CharField(),
                "is_active": drf_serializers.BooleanField(),
            },
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_channel_assignments(request):
    """Return the current user's assigned channels.

    Only ads-department staff have a restricted channel scope; other users get
    an empty list so the frontend treats them as unrestricted.
    """
    from apps.users.constants import DepartmentID

    user = request.user
    if resolve_effective_permissions(user).is_superuser or getattr(user, "department_id", None) != DepartmentID.ADS:
        return Response([])

    version = get_channels_cache_version()
    cache_key = f"channels:my-assignments:v{version}:u{user.id}"
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return Response(cached_data)

    user_channels = UserChannel.objects.filter(
        user=user,
        is_active=True,
        channel__is_active=True,
        channel__deleted_at__isnull=True,
    ).select_related("channel")

    data = [
        {
            "id": uc.channel.id,
            "code": uc.channel.code,
            "name": uc.channel.name,
            "is_active": uc.channel.is_active,
        }
        for uc in user_channels
    ]

    cache.set(cache_key, data, timeout=30)
    return Response(data)



@extend_schema(
    parameters=[
        OpenApiParameter(
            name="department",
            type=int,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Lọc nhân viên theo id phòng ban, độc lập với phòng ban người xem. "
                "Bỏ trống dùng scope mặc định theo người xem."
            ),
        ),
    ],
    responses={200: StaffUserSerializer(many=True)},
)
@api_view(["GET"])
# Danh bạ nhân viên toàn công ty (id/username/tên/phòng ban) — không phải metadata công
# khai. Trước đây chỉ IsAuthenticated: MỌI nhân viên đăng nhập kéo được full danh bạ,
# kể cả lọc ?department= phòng khác (audit 2026-07-14 vòng 5). Gate theo đúng các trang
# đang dùng dropdown này: form đơn, chia đơn, KH thân thiết/CRM, bảo hành, quản trị user.
@permission_classes([
    IsAuthenticated,
    require_section(
        *ORDER_SECTIONS, "lead-assignment", "customers", "loyal-customers", "warranty", "users"
    ),
])
def staff_collection(request):
    """Danh sách nhân viên cho dropdown form đơn.

    - Có tham số `?department=<id>`: trả đúng nhân viên phòng ban đó cho MỌI người dùng,
      không phụ thuộc phòng ban người xem. Dropdown đơn dùng cách này để nhân viên phòng
      khác (vd Ads) vẫn thấy đúng nhân viên Sales/Booking. Việc chọn phòng ban theo trang
      do FE quyết định; FE chỉ hiển thị, không lọc lại.
    - Không có tham số: giữ hành vi cũ — admin (và staff chưa gán phòng ban) thấy tất cả,
      non-admin chỉ thấy nhân viên cùng phòng ban.
    """
    user = request.user
    # Trần số dòng (audit 2026-09-23 L6): danh bạ ~150 người, 500 là rộng gấp ba;
    # FE không gửi limit vẫn nhận nguyên danh sách như trước. Response giữ dạng list.
    limit, limit_error = parse_limit(request, default=_STAFF_MAX_LIMIT, max_limit=_STAFF_MAX_LIMIT)
    if limit_error is not None:
        return limit_error
    department_param = request.query_params.get("department")
    if department_param not in (None, ""):
        try:
            department_id = int(department_param)
        except (TypeError, ValueError):
            return Response(
                {"detail": "department không hợp lệ."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        scope_key = f"d{department_id}"
    else:
        # Dùng department_id (đã nạp sẵn cùng user row) để không phát sinh query thừa.
        department_id = (
            None if resolve_effective_permissions(user).is_superuser else user.department_id
        )
        scope_key = "all" if department_id is None else f"d{department_id}"
    version = get_channels_cache_version()
    cache_key = f"channels:staff:v{version}:{scope_key}:l{limit}"
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return Response(cached_data)

    queryset = ChannelRepository.list_staff_users(department=department_id)[:limit]
    data = StaffUserSerializer(queryset, many=True).data
    cache.set(cache_key, data, timeout=30)
    return Response(data)


@extend_schema(
    request=ChannelAssignmentSerializer,
    responses={
        200: StaffUserSerializer(many=True),  # GET: nhân viên đã gán
        201: StaffUserSerializer(many=True),  # POST: danh sách sau khi thêm
        400: OpenApiResponse(description="User không thuộc phòng Ads"),
        403: OpenApiResponse(description="Không có quyền quản lí kênh"),
    },
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def channel_assignments(request, channel_id: int):
    if not ChannelService.can_manage(request.user):
        return Response({"detail": "Bạn không có quyền quản lí kênh."}, status=status.HTTP_403_FORBIDDEN)

    channel = get_object_or_404(Channel, id=channel_id, deleted_at__isnull=True)

    if request.method == "GET":
        queryset = ChannelRepository.list_assigned_staff(channel)
        return Response(StaffUserSerializer(queryset, many=True).data)

    serializer = ChannelAssignmentSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    staff = get_object_or_404(
        User,
        id=serializer.validated_data["user_id"],
        is_active=True,
        deleted_at__isnull=True,
    )
    if not user_in_department(staff, DepartmentID.ADS):
        return Response(
            {"detail": "Chỉ user thuộc phòng Ads (phòng chính hoặc phòng ban nhiều) mới được gán vào kênh."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    ChannelRepository.add_staff_to_channel(channel=channel, user=staff)
    bump_channels_cache_version()
    queryset = ChannelRepository.list_assigned_staff(channel)
    return Response(StaffUserSerializer(queryset, many=True).data, status=status.HTTP_201_CREATED)


@extend_schema(
    responses={
        204: OpenApiResponse(description="Gỡ gán nhân viên thành công"),
        403: OpenApiResponse(description="Không có quyền quản lí kênh"),
    },
)
@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def channel_assignment_detail(request, channel_id: int, user_id: int):
    if not ChannelService.can_manage(request.user):
        return Response({"detail": "Bạn không có quyền quản lí kênh."}, status=status.HTTP_403_FORBIDDEN)

    channel = get_object_or_404(Channel, id=channel_id, deleted_at__isnull=True)
    staff = get_object_or_404(User, id=user_id, deleted_at__isnull=True)
    ChannelRepository.remove_staff_from_channel(channel=channel, user=staff)
    bump_channels_cache_version()
    return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    request=KiotVietChannelCreateSerializer,
    responses={
        200: KiotVietChannelSerializer(many=True),  # GET: danh sách kênh KiotViet
        201: KiotVietChannelSerializer,  # POST: kênh vừa tạo
        403: OpenApiResponse(description="Không có quyền quản lý kênh KiotViet"),
    },
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def kiotviet_channels_collection(request):
    if not ChannelService.can_manage(request.user):
        return Response({"detail": "Bạn không có quyền quản lý kênh KiotViet."}, status=status.HTTP_403_FORBIDDEN)

    if request.method == "GET":
        pagination, error_response = _parse_required_pagination(request)
        if error_response is not None:
            return error_response

        version = get_channels_cache_version()
        cache_key = (
            f"channels:kiotviet:list:v{version}:"
            f"limit={pagination['limit']}|offset={pagination['offset']}"
        )
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return Response(cached_data)

        queryset = ChannelRepository.list_kiotviet_channels(
            limit=pagination["limit"],
            offset=pagination["offset"],
        )
        data = KiotVietChannelSerializer(queryset, many=True).data
        cache.set(cache_key, data, timeout=30)
        return Response(data)

    serializer = KiotVietChannelCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    channel = ChannelRepository.create_kiotviet_channel(**serializer.validated_data)
    bump_channels_cache_version()
    return Response(KiotVietChannelSerializer(channel).data, status=status.HTTP_201_CREATED)


@extend_schema(
    request=KiotVietChannelUpdateSerializer,
    responses={
        200: KiotVietChannelSerializer,  # PATCH: kênh sau cập nhật
        204: OpenApiResponse(description="DELETE thành công"),
        403: OpenApiResponse(description="Không có quyền quản lý kênh KiotViet"),
    },
)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def kiotviet_channel_detail(request, channel_id: int):
    if not ChannelService.can_manage(request.user):
        return Response({"detail": "Bạn không có quyền quản lý kênh KiotViet."}, status=status.HTTP_403_FORBIDDEN)

    # Lazy get_model: cắt phụ thuộc import-time channels_core → kiotviet.
    # TODO(kiến trúc): thay bằng registry pattern ở sprint tách module sau.
    KiotVietChannel = django_apps.get_model("kiotviet", "KiotVietChannel")
    channel = get_object_or_404(KiotVietChannel, id=channel_id, deleted_at__isnull=True)

    if request.method == "DELETE":
        ChannelRepository.soft_delete_kiotviet_channel(channel)
        bump_channels_cache_version()
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = KiotVietChannelUpdateSerializer(data=request.data, partial=True, context={"instance": channel})
    serializer.is_valid(raise_exception=True)
    updated = ChannelRepository.update_kiotviet_channel(channel, **serializer.validated_data)
    bump_channels_cache_version()
    return Response(KiotVietChannelSerializer(updated).data)


@extend_schema(
    request=ChannelBulkDeleteSerializer,
    responses={
        200: inline_serializer(
            "KiotVietChannelBulkDeleteResponse",
            fields={"deleted_count": drf_serializers.IntegerField()},
        ),
        403: OpenApiResponse(description="Không có quyền quản lý kênh KiotViet"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def bulk_delete_kiotviet_channels(request):
    if not ChannelService.can_manage(request.user):
        return Response({"detail": "Bạn không có quyền quản lý kênh KiotViet."}, status=status.HTTP_403_FORBIDDEN)

    serializer = ChannelBulkDeleteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    deleted_count = ChannelRepository.soft_delete_kiotviet_channels_by_ids(serializer.validated_data["ids"])
    bump_channels_cache_version()
    return Response({"deleted_count": deleted_count}, status=status.HTTP_200_OK)
