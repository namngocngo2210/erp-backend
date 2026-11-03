"""API quản trị nội dung trang sản phẩm public (`/sp/<mã>`) — dùng trong CRM.

GET/PUT dùng chung 1 route vì cùng thao tác trên 1 resource (profile của sản
phẩm), nhưng yêu cầu quyền khác nhau: GET cần products:view, PUT cần
products:edit. Decorator chỉ mang được một mức quyền nên phần PUT tự kiểm tra
thêm trong thân hàm.
"""
from __future__ import annotations

from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orders.application.services.product_profile_service import (
    ProductProfileService,
    ProfileImageValidationError,
)
from apps.catalog.models import Product, ProductProfile, ProductProfileImage
from apps.orders.transport.serializers.product_profile_serializers import (
    ProductProfileImageSerializer,
    ProductProfileSerializer,
)
from apps.users.auth_core import user_has_any_permission
from apps.users.permissions import require_permission


def _serialize_profile(product_id: int) -> dict:
    """Trả cùng MỘT shape dù profile đã tồn tại hay chưa.

    `ProductProfile.objects.filter(...).first()` trả `None` khi sản phẩm chưa có
    profile; serialize thẳng `None` thì DRF trả shape "initial value" khác hẳn
    shape rỗng của GET, khiến frontend phải xử lý hai dạng "rỗng" khác nhau cho
    cùng một resource. Dùng chung hàm này ở mọi endpoint trả về profile.
    """
    profile = ProductProfile.objects.filter(product_id=product_id).first() or ProductProfile(
        product_id=product_id
    )
    return ProductProfileSerializer(profile).data


@extend_schema(methods=["GET"], responses={200: ProductProfileSerializer})
@extend_schema(
    methods=["PUT"],
    request=ProductProfileSerializer,
    responses={200: ProductProfileSerializer},
)
@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated, require_permission("products:view")])
@never_cache
def product_profile(request, product_id: int):
    if not Product.objects.filter(id=product_id, deleted_at__isnull=True).exists():
        return Response({"detail": "Không tìm thấy sản phẩm."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(_serialize_profile(product_id))

    # PUT cần products:edit — decorator ở trên chỉ khai products:view (mức yếu
    # hơn, dùng chung cho GET). Dùng user_has_any_permission (mẫu của
    # apps/warranty_codes/exports_definitions.py) chứ không tự dựng permission
    # class rồi gọi has_permission thủ công.
    if not user_has_any_permission(request.user, "products:edit"):
        return Response(
            {"detail": "Bạn không có quyền thực hiện hành động này."},
            status=status.HTTP_403_FORBIDDEN,
        )

    profile = ProductProfile.objects.filter(product_id=product_id).first()
    serializer = ProductProfileSerializer(profile, data=request.data, partial=profile is None)
    serializer.is_valid(raise_exception=True)
    serializer.save(product_id=product_id, updated_by=request.user)
    return Response(serializer.data)


@extend_schema(responses={201: ProductProfileImageSerializer})
@extend_schema(
    request=inline_serializer(
        "ProductProfileImageUpload", fields={"file": drf_serializers.FileField()}
    ),
    responses={200: ProductProfileSerializer},
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("products:edit")])
@parser_classes([MultiPartParser, FormParser])
@never_cache
def product_profile_images(request, product_id: int):
    if not Product.objects.filter(id=product_id, deleted_at__isnull=True).exists():
        return Response({"detail": "Không tìm thấy sản phẩm."}, status=status.HTTP_404_NOT_FOUND)

    upload = request.FILES.get("file")
    if upload is None:
        return Response({"detail": "Thiếu file ảnh."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        image = ProductProfileService.add_image(product_id=product_id, data=upload.read())
    except ProfileImageValidationError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(ProductProfileImageSerializer(image).data, status=status.HTTP_201_CREATED)


@extend_schema(responses={204: None})
@api_view(["DELETE"])
@permission_classes([IsAuthenticated, require_permission("products:edit")])
@never_cache
def product_profile_image_detail(request, product_id: int, image_id: int):
    if not ProductProfileImage.objects.filter(
        id=image_id, profile__product_id=product_id
    ).exists():
        return Response({"detail": "Không tìm thấy ảnh."}, status=status.HTTP_404_NOT_FOUND)
    ProductProfileService.delete_image(image_id=image_id)
    return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    request=inline_serializer(
        "ProductProfileImageOrder",
        fields={"image_ids": drf_serializers.ListField(child=drf_serializers.IntegerField())},
    ),
    responses={200: ProductProfileSerializer},
)
@api_view(["PUT"])
@permission_classes([IsAuthenticated, require_permission("products:edit")])
@never_cache
def product_profile_image_order(request, product_id: int):
    # Endpoint duy nhất trong 4 endpoint của module này từng thiếu check tồn tại
    # sản phẩm — 3 endpoint kia đều 404 cho id không tồn tại, endpoint này lại
    # trả 200 với profile rỗng, không nhất quán với chính các endpoint anh em.
    if not Product.objects.filter(id=product_id, deleted_at__isnull=True).exists():
        return Response({"detail": "Không tìm thấy sản phẩm."}, status=status.HTTP_404_NOT_FOUND)

    image_ids = request.data.get("image_ids") or []
    if not isinstance(image_ids, list):
        return Response({"detail": "image_ids phải là mảng."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        image_ids = [int(i) for i in image_ids]
    except (TypeError, ValueError):
        return Response(
            {"detail": "image_ids phải là mảng số nguyên."}, status=status.HTTP_400_BAD_REQUEST
        )
    ProductProfileService.reorder_images(product_id=product_id, image_ids=image_ids)
    return Response(_serialize_profile(product_id))
