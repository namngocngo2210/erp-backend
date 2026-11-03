from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import parse_required_pagination
from apps.users.permissions import require_section
from apps.warranty_codes import repository
from apps.warranty_codes.transport._query_params import query_date, query_int
from apps.warranty_codes.transport.serializers import WarrantyCodeScanLogSerializer, WarrantyCodeSerializer


@extend_schema(
    responses={
        200: inline_serializer(
            "WarrantyCodeListResponse",
            fields={"results": WarrantyCodeSerializer(many=True), "total": drf_serializers.IntegerField()},
        )
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("warranty-codes")])
def list_codes(request):
    pagination, error = parse_required_pagination(request)
    if error is not None:
        return error
    rows, total = repository.list_codes(
        search=request.query_params.get("search"),
        product_id=query_int(request, "product_id"),
        batch_id=query_int(request, "batch_id"),
        date_from=query_date(request, "date_from"),
        date_to=query_date(request, "date_to"),
        limit=pagination["limit"],
        offset=pagination["offset"],
    )
    return Response({"results": WarrantyCodeSerializer(rows, many=True).data, "total": total})


@extend_schema(
    responses={
        200: inline_serializer(
            "WarrantyCodeScanLogListResponse",
            fields={"results": WarrantyCodeScanLogSerializer(many=True),
                    "total": drf_serializers.IntegerField()},
        )
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("warranty-codes")])
def list_scan_logs(request):
    pagination, error = parse_required_pagination(request)
    if error is not None:
        return error
    rows, total = repository.list_scan_logs(
        keyword=request.query_params.get("keyword"),
        ip_address=request.query_params.get("ip_address"),
        result=request.query_params.get("result"),
        channel=request.query_params.get("channel"),
        date_from=query_date(request, "date_from"),
        date_to=query_date(request, "date_to"),
        limit=pagination["limit"],
        offset=pagination["offset"],
    )
    return Response({"results": WarrantyCodeScanLogSerializer(rows, many=True).data, "total": total})
