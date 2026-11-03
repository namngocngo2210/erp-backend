import json
from pathlib import Path

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import ORDER_SECTIONS, require_section

from apps.orders.address_level2 import (
    LEVEL2_FIELDS,
    empty_level2_fields,
    resolve_kiotviet_names,
    resolve_level2_edit,
    resolve_level2_fields,
)
from apps.orders.address_lookup import lookup_address
from apps.orders.address_parser import format_standard_address, parse_legacy_level3


# Trả về nguyên file kiotviet_address_final.json (cây địa giới tỉnh/quận/phường)
# — cấu trúc lồng tuỳ biến, không ràng buộc schema nên dùng OpenApiResponse mô tả.
@extend_schema(
    responses={200: OpenApiResponse(description="Dữ liệu địa giới KiotViet (JSON cây)")},
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS)])
def get_kiotviet_location_data(request):
    """Return kiotviet_address_final.json for frontend cascading dropdowns."""
    data_path = Path(__file__).resolve().parent.parent.parent.parent / "data" / "kiotviet_address_final.json"
    try:
        payload = json.loads(data_path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    return Response(payload)


@extend_schema(
    request=inline_serializer(
        "ParseRawAddressRequest",
        fields={"raw_address": drf_serializers.CharField(allow_blank=True)},
    ),
    responses={
        200: inline_serializer(
            "ParseRawAddressResponse",
            fields={
                "raw_address": drf_serializers.CharField(allow_blank=True),
                "address": drf_serializers.CharField(allow_blank=True),
                "kiotviet_location": drf_serializers.CharField(allow_blank=True),
                "kiotviet_ward": drf_serializers.CharField(allow_blank=True),
                "ward": drf_serializers.CharField(allow_blank=True),
                "district": drf_serializers.CharField(allow_blank=True),
                "province": drf_serializers.CharField(allow_blank=True),
                "ward_code": drf_serializers.CharField(allow_blank=True),
                "district_code": drf_serializers.CharField(allow_blank=True),
                "province_code": drf_serializers.CharField(allow_blank=True),
                **{key: drf_serializers.CharField(allow_blank=True) for key in LEVEL2_FIELDS},
            },
        ),
    },
)
# Trang bảo hành cũng dùng endpoint này để parse địa chỉ trước khi tạo đơn Best.
# Endpoint chỉ chuẩn hoá chuỗi người dùng tự nhập, không đọc dữ liệu đơn nào.
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS, "warranty")])
def parse_raw_address(request):
    """Parse raw VN address into structured components for FE inline edit preview."""
    raw_address = str(request.data.get("raw_address", "") or "").strip()
    if not raw_address:
        return Response(
            {
                "raw_address": "",
                "address": "",
                "kiotviet_location": "",
                "kiotviet_ward": "",
                "ward": "",
                "district": "",
                "province": "",
                "ward_code": "",
                "district_code": "",
                "province_code": "",
                **empty_level2_fields(),
            },
            status=status.HTTP_200_OK,
        )

    parsed = parse_legacy_level3(raw_address)
    formatted_address = format_standard_address(parsed)
    level2_fields = resolve_level2_fields(raw_address, parsed)
    kv_resolved = resolve_kiotviet_names(parsed, raw_address, level2_fields)
    return Response(
        {
            "raw_address": raw_address,
            "address": formatted_address,
            "kiotviet_location": kv_resolved.get("locationName", "") or "",
            "kiotviet_ward": kv_resolved.get("wardName", "") or "",
            "ward": parsed.get("ward", "") or "",
            "district": parsed.get("district", "") or "",
            "province": parsed.get("province", "") or "",
            "ward_code": str(parsed.get("ward_code", "") or ""),
            "district_code": str(parsed.get("district_code", "") or ""),
            "province_code": str(parsed.get("province_code", "") or ""),
            **level2_fields,
        },
        status=status.HTTP_200_OK,
    )


_LEVEL2_EDIT_FIELDS = {
    key: drf_serializers.CharField(allow_blank=True)
    for key in (*LEVEL2_FIELDS, "kiotviet_location", "kiotviet_ward")
}


@extend_schema(
    request=inline_serializer(
        "ParseAddressLevel2Request",
        fields={"address_level2": drf_serializers.CharField(allow_blank=True)},
    ),
    responses={200: inline_serializer("ParseAddressLevel2Response", fields=_LEVEL2_EDIT_FIELDS)},
)
# Preview khi sale sửa tay ô "Địa chỉ 2 cấp" ở chi tiết đơn: tách tỉnh mới/phường mới
# + Khu vực/Phường xã Kiot (rỗng nếu không khớp danh mục). PATCH đơn tính lại y hệt.
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS)])
def parse_address_level2(request):
    return Response(
        resolve_level2_edit(str(request.data.get("address_level2", "") or "")),
        status=status.HTTP_200_OK,
    )


_LOOKUP_LEVEL3_FIELDS = {
    key: drf_serializers.CharField(allow_blank=True)
    for key in ("address", "street", "ward", "district", "province", "ward_code", "district_code", "province_code")
}
_LOOKUP_LEVEL2_FIELDS = {
    key: drf_serializers.CharField(allow_blank=True)
    for key in ("address", "street", "ward", "province", "ward_code", "province_code", "source")
}


@extend_schema(
    request=inline_serializer(
        "LookupAddressRequest",
        fields={"raw_address": drf_serializers.CharField(allow_blank=True)},
    ),
    responses={
        200: inline_serializer(
            "LookupAddressResponse",
            fields={
                "raw_address": drf_serializers.CharField(allow_blank=True),
                "level3": inline_serializer("LookupAddressLevel3", fields=_LOOKUP_LEVEL3_FIELDS),
                "level2": inline_serializer("LookupAddressLevel2", fields=_LOOKUP_LEVEL2_FIELDS),
            },
        ),
    },
)
# Trang Tiện ích › Tra cứu địa chỉ: gate bằng section riêng `address-lookup`
# (chỉ "view"), không mượn quyền đơn hàng — tiện ích dùng cho mọi phòng ban.
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("address-lookup")])
def lookup_raw_address(request):
    """Địa chỉ thô → địa chỉ 3 cấp (63 tỉnh) và 2 cấp (34 tỉnh, sau 01/07/2025)."""
    raw_address = str(request.data.get("raw_address", "") or "").strip()
    return Response(lookup_address(raw_address), status=status.HTTP_200_OK)
