"""8 endpoint GET của API Daily Report — 5 trả danh sách dòng, /summary 410. Mọi view đi qua ``daily_report_view``."""

from __future__ import annotations

import logging

from django.db import DatabaseError
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response

from apps.common.health import check_db, check_redis
from apps.daily_report.application import ads_service, stats_service
from apps.daily_report.domain import paging, periods
from apps.daily_report.domain.periods import ParamError
from apps.daily_report.infrastructure.freshness import read_freshness
from apps.daily_report.transport import serializers as s
from apps.daily_report.transport.auth import daily_report_view

logger = logging.getLogger(__name__)

_ERRORS = {
    400: OpenApiResponse(s.ErrorSerializer, description="Tham số sai — detail nói rõ trường nào"),
    401: OpenApiResponse(s.ErrorSerializer, description="Thiếu/sai X-Api-Token"),
    503: OpenApiResponse(s.ErrorSerializer, description="Prod chưa cấu hình token, hoặc DB/mart lỗi"),
}
_FROM = OpenApiParameter("from", str, description="YYYY-MM-DD giờ VN, mặc định hôm qua, bao gồm")
_TO = OpenApiParameter("to", str, description="YYYY-MM-DD giờ VN, mặc định hôm qua, bao gồm; tối đa 92 ngày")
_CURSOR = OpenApiParameter(
    "cursor", str,
    description="Giá trị `next_cursor` của trang trước (keyset). Bỏ trống để lấy trang đầu.",
)
_LIMIT = OpenApiParameter(
    "limit", int,
    description="Số dòng mỗi trang, mặc định 200. Vượt trần bị kẹp về trần, không lỗi.",
)


def _paging(request, max_limit: int) -> tuple[int | None, int]:
    """Từ chối group_by (API không còn số gộp), đọc cursor số + limit."""
    paging.reject_group_by(request.query_params)
    return paging.parse_int_cursor(request.query_params), paging.parse_limit(request.query_params, max_limit=max_limit)


@extend_schema(
    auth=[{"ApiKeyAuth": []}],
    tags=["daily-report"],
    summary="Health: DB, Redis, độ tươi mart",
    responses={200: s.HealthSerializer, 503: s.HealthSerializer, 401: _ERRORS[401]},
)
@daily_report_view
def health(request):
    mart = read_freshness()
    checks = {"db": check_db(), "redis": check_redis(), "mart": mart.as_dict()}
    ok = checks["db"] and checks["redis"] and mart.ok
    return Response(
        {"status": "ok" if ok else "degraded", "checks": checks},
        status=status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def _bad_request(exc: ParamError) -> Response:
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


def _run_payload(build):
    """Gọi service; statement_timeout / DB chết → 503 rõ ràng thay vì 500 vô danh."""
    try:
        return Response(build())
    except DatabaseError:
        logger.exception("daily_report: truy vấn thất bại")
        return Response(
            {"detail": "Truy vấn quá lâu hoặc DB lỗi — thử lại sau."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


@extend_schema(
    auth=[{"ApiKeyAuth": []}],
    tags=["daily-report"],
    summary="Từng lead (đơn nội bộ tạo trong kỳ, mọi trạng thái)",
    description="Mỗi dòng = 1 đơn. Đếm theo `sales_status.bucket` để ra các rổ lead; `bucket` null = chưa đánh giá.",
    parameters=[_FROM, _TO, _CURSOR, _LIMIT],
    responses={200: s.page_envelope_of(s.OrderRowSerializer, name="LeadRowsPage", cursor_field=serializers.IntegerField), **_ERRORS},
)
@daily_report_view
def leads_stats(request):
    try:
        d_from, d_to = periods.parse_range(request.query_params)
        after, limit = _paging(request, paging.ORDER_MAX_LIMIT)
    except ParamError as exc:
        return _bad_request(exc)
    return _run_payload(lambda: stats_service.leads_payload(d_from, d_to, after=after, limit=limit))


@extend_schema(
    auth=[{"ApiKeyAuth": []}],
    tags=["daily-report"],
    summary="Từng đơn có hoá đơn (tập total_orders của Báo cáo tổng)",
    description="Mỗi dòng = 1 đơn đã lên hoá đơn, không huỷ, loại chi nhánh ẩn. Đếm theo `order_status.bucket` để ra delivered/awaiting/returned/return_risk; Σ `amounts.total` = total_revenue.",
    parameters=[_FROM, _TO, _CURSOR, _LIMIT],
    responses={200: s.page_envelope_of(s.OrderRowSerializer, name="OrderRowsPage", cursor_field=serializers.IntegerField), **_ERRORS},
)
@daily_report_view
def orders_stats(request):
    try:
        d_from, d_to = periods.parse_range(request.query_params)
        after, limit = _paging(request, paging.ORDER_MAX_LIMIT)
    except ParamError as exc:
        return _bad_request(exc)
    return _run_payload(lambda: stats_service.orders_payload(d_from, d_to, after=after, limit=limit))


@extend_schema(
    auth=[{"ApiKeyAuth": []}],
    tags=["daily-report"],
    summary="Từng dòng hàng (sản phẩm trong đơn) của tập đơn có hoá đơn",
    description="Cùng bộ lọc với /orders/stats; nối `order_id`. Σ `amount` theo đơn có thể khác `amounts.total` bên /orders/stats vì phí ship/giảm giá cấp đơn.",
    parameters=[_FROM, _TO, _CURSOR, _LIMIT],
    responses={200: s.page_envelope_of(s.OrderLineRowSerializer, name="OrderLineRowsPage", cursor_field=serializers.IntegerField), **_ERRORS},
)
@daily_report_view
def revenue(request):
    try:
        d_from, d_to = periods.parse_range(request.query_params)
        after, limit = _paging(request, paging.LINE_MAX_LIMIT)
    except ParamError as exc:
        return _bad_request(exc)
    return _run_payload(lambda: stats_service.revenue_payload(d_from, d_to, after=after, limit=limit))


@extend_schema(
    auth=[{"ApiKeyAuth": []}],
    tags=["daily-report"],
    summary="Từng dòng chi tiêu quảng cáo: ngày × nền tảng × TKQC × chiến dịch (+ lead/đơn/doanh thu quy về chiến dịch), kèm dòng dư mỗi TKQC",
    description=(
        "Σ `spend_adjusted` mọi dòng theo (platform, account_id) = chi tiêu TKQC (bản sao gộp Max). "
        "Dòng `is_remainder` = TKQC − Σ chiến dịch, có thể âm khi sync lệch nhịp — nêu ra, đừng cộng vào chiến dịch. "
        "Muốn theo Digital: nối `channels` với `channel` bên /orders/stats. Không có ad set / ad riêng lẻ. "
        "Dòng chiến dịch Facebook mang `leads`/`closed`/`orders`/`revenue` quy theo `fb_campaign_id` của lead (ngày TẠO); "
        "chiến dịch có đơn mà không tiêu trong ngày vẫn có dòng spend 0. Độ phủ quy về chiến dịch ở `meta.attribution`. "
        "Mốc sync mỗi nền tảng ở `meta.spend_as_of`."
    ),
    parameters=[_FROM, _TO, _CURSOR, _LIMIT],
    responses={200: s.page_envelope_of(s.AdSpendRowSerializer, name="AdSpendRowsPage", cursor_field=serializers.CharField), **_ERRORS},
)
@daily_report_view
def ads(request):
    try:
        d_from, d_to = periods.parse_range(request.query_params)
        paging.reject_group_by(request.query_params)
        after = ads_service.parse_cursor(request.query_params.get("cursor"))
        limit = paging.parse_limit(request.query_params, max_limit=paging.ADS_MAX_LIMIT)
    except ParamError as exc:
        return _bad_request(exc)
    return _run_payload(lambda: ads_service.ads_payload(d_from, d_to, after=after, limit=limit))


@extend_schema(
    auth=[{"ApiKeyAuth": []}],
    tags=["daily-report"],
    summary="Từng lead còn mở tính đến as_of",
    description="Lead tạo trong lookback_days ngày, chưa lên hoá đơn, chưa huỷ, trạng thái sale chưa kết thúc. `age_days` = as_of − ngày tạo; rổ tuổi cũ: 0-1d / 2-3d / 4-7d / 8d+.",
    parameters=[
        OpenApiParameter("as_of", str, description="YYYY-MM-DD, mặc định hôm nay giờ VN"),
        OpenApiParameter("lookback_days", int, description="1..92, mặc định 30"),
        _CURSOR, _LIMIT,
    ],
    responses={200: s.page_envelope_of(s.OpenLeadRowSerializer, name="OpenLeadRowsPage", cursor_field=serializers.IntegerField), **_ERRORS},
)
@daily_report_view
def pipeline(request):
    try:
        as_of = periods.parse_date_param(request.query_params, "as_of", default=periods.today_vn())
        lookback = periods.parse_int(
            request.query_params, "lookback_days",
            default=stats_service.DEFAULT_LOOKBACK_DAYS, minimum=1, maximum=periods.MAX_RANGE_DAYS,
        )
        after, limit = _paging(request, paging.ORDER_MAX_LIMIT)
    except ParamError as exc:
        return _bad_request(exc)
    return _run_payload(lambda: stats_service.pipeline_payload(as_of, lookback, after=after, limit=limit))


SUMMARY_GONE = "Endpoint đã bỏ (16/09/2026): API trả từng dòng, không còn gói số gộp. Ghép từ /leads/stats, /orders/stats, /pipeline."


@extend_schema(
    auth=[{"ApiKeyAuth": []}],
    tags=["daily-report"],
    summary="[Đã bỏ] Gói báo cáo ngày — luôn 410",
    description=SUMMARY_GONE,
    responses={410: OpenApiResponse(s.ErrorSerializer, description=SUMMARY_GONE), 401: _ERRORS[401]},
)
@daily_report_view
def summary(request):
    return Response({"detail": SUMMARY_GONE}, status=status.HTTP_410_GONE)


@extend_schema(
    auth=[{"ApiKeyAuth": []}],
    tags=["daily-report"],
    summary="Từ điển trạng thái + định nghĩa chỉ số",
    responses={200: s.envelope_of(s.DefinitionsSerializer, name="DefinitionsEnvelope"), **_ERRORS},
)
@daily_report_view
def definitions(request):
    return _run_payload(lambda: stats_service.definitions_payload())
