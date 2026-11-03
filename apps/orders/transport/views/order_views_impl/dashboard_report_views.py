"""Dashboard summary + ads report summary views.

Tách từ ``order_ingest_product_dashboard_view.py`` (audit 2026-07 mục 4.5) —
move refactor, không đổi logic.
"""

import hashlib
import json
import time

from django.conf import settings
from django.core.cache import cache
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.db_guards import single_flight
from apps.orders.repositories import OrderRepository
from apps.users.models import DataScope
from apps.users.permissions import require_section
from apps.users.rbac import resolve_effective_permissions

# Cache + single-flight cho các endpoint báo cáo (audit 2026-09-23 C4 / perf §2.1).
#
# Mỗi request dashboard là 11 aggregate GROUP BY trên `orders` theo khoảng ngày —
# nhiều người mở cùng khoảng ngày đầu ca/đầu tháng thì mỗi tab lặp lại y hệt, trong
# khi `landing_sheet_stats`/`sheet3_temp_counts` cùng cỡ đã có cache. Khuôn dùng
# chung với `sheet3_view.py` (temperature_counts):
#   - TTL = ORDERS_LIST_COUNT_CACHE_TTL (prod/staging/local = 120s; 0 = tắt cache,
#     mặc định khi không set env — test hiện có không đổi hành vi).
#   - Khoá = md5(tên báo cáo, SCOPE user, toàn bộ query params trừ `refresh`).
#     Scope BẮT BUỘC nằm trong khoá: báo cáo ads lọc kênh/TKQC theo quyền user —
#     dùng chung entry là lộ số của kênh ngoài phạm vi. Dashboard/tổng quan không
#     lọc theo user (chỉ gate section) nên scope là hằng "global" để mọi người
#     cùng khoảng ngày chia sẻ một entry.
#   - `?refresh=1` bỏ qua cache (tính lại + ghi đè) cho nút "Làm mới" trên FE.
#   - single_flight: N tab cùng miss → 1 request tính, các request khác chờ ngắn
#     (6 × 250ms) rồi đọc lại; vẫn miss (compute chậm/Redis chết) thì tự tính —
#     fail-open, không bao giờ trả thiếu dữ liệu.
_REPORT_CACHE_PREFIX = "orders:report"
_REPORT_LOCK_TTL_SECONDS = 30
_REPORT_WAIT_ROUNDS = 6
_REPORT_WAIT_STEP_SECONDS = 0.25


def _report_cache_ttl() -> int:
    return int(getattr(settings, "ORDERS_LIST_COUNT_CACHE_TTL", 0) or 0)


def _report_cache_key(name: str, scope: str, request) -> str:
    params = {
        key: sorted(str(v) for v in values)
        for key, values in request.query_params.lists()
        if key != "refresh"
    }
    raw = json.dumps({"scope": scope, "params": params}, sort_keys=True, ensure_ascii=False)
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()  # noqa: S324 - khoá cache, không phải bảo mật
    return f"{_REPORT_CACHE_PREFIX}:{name}:{digest}"


def _ads_report_scope(user) -> str:
    """Scope cache của báo cáo ads: user xem-toàn-bộ chia sẻ entry "all"; user bị
    giới hạn kênh/TKQC có entry riêng theo id (quyền đổi → tối đa TTL giây là
    thấy, cùng mức chấp nhận với các cache đếm của trang Đơn)."""
    if resolve_effective_permissions(user).scope_for("ads-report") == DataScope.ALL:
        return "all"
    return f"user:{user.id}"


def _wants_refresh(request) -> bool:
    return str(request.query_params.get("refresh") or "").strip().lower() in ("1", "true", "yes")


def _cached_report(request, *, name: str, scope: str, compute):
    """Trả payload báo cáo từ cache (theo khoá name+scope+params) hoặc tính mới."""
    ttl = _report_cache_ttl()
    if ttl <= 0:
        return compute()

    cache_key = _report_cache_key(name, scope, request)
    refresh = _wants_refresh(request)
    if not refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    data = None
    with single_flight(f"{cache_key}:lock", _REPORT_LOCK_TTL_SECONDS) as acquired:
        if acquired or refresh:
            data = compute()
            cache.set(cache_key, data, ttl)
        else:
            for _ in range(_REPORT_WAIT_ROUNDS):
                time.sleep(_REPORT_WAIT_STEP_SECONDS)
                data = cache.get(cache_key)
                if data is not None:
                    break
    if data is None:
        data = compute()
        cache.set(cache_key, data, ttl)
    return data


# Tổng hợp dashboard: mỗi key là một cấu trúc thống kê do OrderRepository tính
# (list/dict theo nhân viên/kênh) — khai báo đúng các key, value để dạng JSON tự do.
@extend_schema(
    responses={
        200: inline_serializer(
            "DashboardSummaryResponse",
            fields={
                "landing_delivered_by_staff": drf_serializers.JSONField(),
                "close_rate_by_staff": drf_serializers.JSONField(),
                "sale_fb_revenue_by_staff": drf_serializers.JSONField(),
                "sale_fb_team_revenue": drf_serializers.JSONField(),
                "sale_fb_team_closed_orders": drf_serializers.JSONField(),
                "sale_performance_by_staff": drf_serializers.JSONField(),
                "closed_revenue_by_staff_sheet": drf_serializers.JSONField(),
                "data_quality_by_channel": drf_serializers.JSONField(),
                "data_by_carrier": drf_serializers.JSONField(),
                "delivery_quality_by_staff": drf_serializers.JSONField(),
                "delivery_quality_by_channel": drf_serializers.JSONField(),
                "bomb_reasons": drf_serializers.JSONField(),
                "return_rate_by_product": drf_serializers.JSONField(),
            },
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("dashboard")])
def dashboard_summary(request):
    return Response(
        _cached_report(
            request,
            name="dashboard_summary",
            scope="global",
            compute=lambda: _compute_dashboard_summary(request),
        )
    )


def _compute_dashboard_summary(request) -> dict:
    date_from = request.query_params.get("date_from", "")
    date_to = request.query_params.get("date_to", "")

    # Tính scope kênh sale (sale_report_channel_ids) 1 lần cho cả request thay vì
    # để mỗi summary_* tự re-query (audit 2026-07 follow-up task 11) — hành vi
    # không đổi, chỉ giảm số query. sale_fb_revenue không dùng scope này.
    sale_channel_ids, _excluded_ids = OrderRepository.ads_channel_scope()

    landing_delivered_by_staff = OrderRepository.summary_landing_delivered_by_staff(
        date_from=date_from, date_to=date_to, sale_channel_ids=sale_channel_ids
    )
    close_rate_by_staff = OrderRepository.summary_close_rate_by_staff(
        date_from=date_from, date_to=date_to, sale_channel_ids=sale_channel_ids
    )
    sale_fb_revenue = OrderRepository.summary_sale_fb_revenue_by_staff(
        date_from=date_from, date_to=date_to
    )
    sale_performance_by_staff = OrderRepository.summary_sale_performance_by_staff(
        date_from=date_from, date_to=date_to, sale_channel_ids=sale_channel_ids
    )
    closed_revenue_by_staff_sheet = OrderRepository.summary_closed_revenue_by_staff_sheet(
        date_from=date_from, date_to=date_to, sale_channel_ids=sale_channel_ids
    )
    data_quality_by_channel = OrderRepository.summary_data_quality_by_channel(
        date_from=date_from, date_to=date_to, sale_channel_ids=sale_channel_ids
    )
    data_by_carrier = OrderRepository.summary_data_by_carrier(
        date_from=date_from, date_to=date_to, sale_channel_ids=sale_channel_ids
    )
    delivery_quality_by_staff = OrderRepository.summary_delivery_quality_by_staff(
        date_from=date_from, date_to=date_to, sale_channel_ids=sale_channel_ids
    )
    delivery_quality_by_channel = OrderRepository.summary_delivery_quality_by_channel(
        date_from=date_from, date_to=date_to, sale_channel_ids=sale_channel_ids
    )
    bomb_reasons = OrderRepository.summary_bomb_reasons(
        date_from=date_from, date_to=date_to, sale_channel_ids=sale_channel_ids
    )
    return_rate_by_product = OrderRepository.summary_return_rate_by_product(
        date_from=date_from, date_to=date_to, sale_channel_ids=sale_channel_ids
    )
    data = {
        "landing_delivered_by_staff": landing_delivered_by_staff,
        "close_rate_by_staff": close_rate_by_staff,
        "sale_fb_revenue_by_staff": sale_fb_revenue["by_staff"],
        "sale_fb_team_revenue": sale_fb_revenue["team_revenue"],
        "sale_fb_team_closed_orders": sale_fb_revenue["team_closed_orders"],
        "sale_performance_by_staff": sale_performance_by_staff,
        "closed_revenue_by_staff_sheet": closed_revenue_by_staff_sheet,
        "data_quality_by_channel": data_quality_by_channel,
        "data_by_carrier": data_by_carrier,
        "delivery_quality_by_staff": delivery_quality_by_staff,
        "delivery_quality_by_channel": delivery_quality_by_channel,
        "bomb_reasons": bomb_reasons,
        "return_rate_by_product": return_rate_by_product,
    }
    return data


@extend_schema(
    responses={
        200: inline_serializer(
            "SaleStaffReportResponse",
            fields={
                "by_staff_daily": inline_serializer(
                    "SaleStaffDailyRow",
                    fields={
                        "date": drf_serializers.CharField(),
                        "staff_id": drf_serializers.IntegerField(allow_null=True),
                        "staff__username": drf_serializers.CharField(allow_null=True),
                        "staff__display_name": drf_serializers.CharField(allow_null=True),
                        "data_count": drf_serializers.IntegerField(),
                        "orders": drf_serializers.IntegerField(),
                        "total_revenue": drf_serializers.CharField(),
                        "closed_revenue": drf_serializers.CharField(),
                        "confirmed_revenue": drf_serializers.CharField(),
                        "lead_time_seconds": drf_serializers.IntegerField(),
                        "lead_time_orders": drf_serializers.IntegerField(),
                        "queue_wait_seconds": drf_serializers.IntegerField(),
                    },
                    many=True,
                ),
            },
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("dashboard")])
def sale_staff_report_summary(request):
    """Báo cáo sale theo nhân viên — hàng phẳng (ngày, nhân viên).

    FE gộp theo nhân viên cho tab "Báo cáo theo sale" và lọc 1 nhân viên cho tab
    "Báo cáo chi tiết theo sale" từ cùng một payload.

    confirmed_revenue = tiền khách tự đăng ký trên landing (snapshot lúc ingest);
    closed_revenue = phần sale tư vấn thêm (giá trị cuối − snapshot).

    lead_time_seconds / lead_time_orders = TỔNG thời gian xử lý data và SỐ đơn có
    mốc tác nghiệp; FE chia sau khi gộp để cộng ngang các hàng không lệch.
    queue_wait_seconds = chặng trước đó (data nằm chờ chia), dùng chung mẫu số
    lead_time_orders nên hai cột cộng lại ra tổng thời gian từ lúc đơn đổ về.
    """
    date_from = request.query_params.get("date_from", "")
    date_to = request.query_params.get("date_to", "")

    def compute() -> dict:
        # Cùng scope kênh với dashboard_summary — tính 1 lần cho cả request.
        sale_channel_ids, _excluded_ids = OrderRepository.ads_channel_scope()
        return {
            "by_staff_daily": OrderRepository.summary_sale_daily_by_staff(
                date_from=date_from,
                date_to=date_to,
                sale_channel_ids=sale_channel_ids,
            ),
        }

    return Response(_cached_report(request, name="sale_staff", scope="global", compute=compute))


def _resale_bucket_fields():
    """Field instances cho các rổ resale — hàm trả dict MỚI mỗi lần gọi.

    DRF bind field instance vào serializer cha; tái dùng cùng instance ở hai
    ``inline_serializer`` khác nhau làm drf-spectacular lỗi lúc dựng schema
    ("field has already been bound"). Gọi hàm riêng ở mỗi chỗ dùng để mỗi bên
    có field instance của riêng mình.
    """
    return {
        "total_revenue": drf_serializers.CharField(),
        "total_orders": drf_serializers.IntegerField(),
        "delivered_revenue": drf_serializers.CharField(),
        "delivered_orders": drf_serializers.IntegerField(),
        "awaiting_revenue": drf_serializers.CharField(),
        "awaiting_orders": drf_serializers.IntegerField(),
        "returned_revenue": drf_serializers.CharField(),
        "returned_orders": drf_serializers.IntegerField(),
        "return_risk_revenue": drf_serializers.CharField(),
        "return_risk_orders": drf_serializers.IntegerField(),
    }


@extend_schema(
    responses={
        200: inline_serializer(
            "SaleResaleReportResponse",
            fields={
                "kpis": inline_serializer("SaleResaleKpis", fields=_resale_bucket_fields()),
                "by_staff": inline_serializer(
                    "SaleResaleStaffRow",
                    fields={
                        "staff_id": drf_serializers.IntegerField(allow_null=True),
                        "staff__username": drf_serializers.CharField(allow_null=True),
                        "staff__display_name": drf_serializers.CharField(allow_null=True),
                        **_resale_bucket_fields(),
                    },
                    many=True,
                ),
            },
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("dashboard")])
def resale_report_summary(request):
    """Tab "Resale" — đơn của KÊNH Resale, chia 4 rổ như Báo cáo tổng.

    Phạm vi là kênh (``resale_channel_ids()``), KHÔNG còn suy luận từ lịch sử
    mua như bản trước. Bốn rổ cộng lại đúng bằng tổng, cả tiền lẫn số đơn.
    """
    return Response(
        _cached_report(
            request,
            name="sale_resale",
            scope="global",
            compute=lambda: OrderRepository.summary_resale_by_staff(
                date_from=request.query_params.get("date_from", ""),
                date_to=request.query_params.get("date_to", ""),
            ),
        )
    )


def _int_list(request, param: str) -> list[int]:
    """Nhận cả dạng lặp param lẫn "1,2,3". Giá trị lạ bị bỏ qua thay vì trả 400."""
    values: list[int] = []
    for raw in request.query_params.getlist(param):
        for piece in str(raw).split(","):
            piece = piece.strip()
            if not piece:
                continue
            try:
                values.append(int(piece))
            except ValueError:
                continue
    return values


@extend_schema(
    responses={
        200: inline_serializer(
            "AdsReportSummaryResponse",
            fields={
                "revenue_by_channel": drf_serializers.JSONField(),
                "top_products": drf_serializers.JSONField(),
                "daily_breakdown": drf_serializers.JSONField(),
                "assigned_ads_cost": drf_serializers.CharField(),
                "revenue_by_province": drf_serializers.JSONField(),
                "revenue_by_gender": drf_serializers.JSONField(),
            },
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("ads-report")])
def ads_report_summary(request):
    return Response(
        _cached_report(
            request,
            name="ads_summary",
            scope=_ads_report_scope(request.user),
            compute=lambda: _compute_ads_report_summary(request),
        )
    )


def _compute_ads_report_summary(request) -> dict:
    date_from = request.query_params.get("date_from", "")
    date_to = request.query_params.get("date_to", "")

    channel_ids = _int_list(request, "channel_ids")
    # Lọc sản phẩm — chỉ 2 tab "Báo cáo theo digital" dùng, và CHỈ ảnh hưởng khối
    # `daily_breakdown` (khối duy nhất nuôi hai tab đó). Các khối còn lại giữ nguyên
    # phạm vi toàn bộ để tab Tổng quan không đổi số.
    product_ids = _int_list(request, "product_ids")

    # Tính scope kênh 1 lần cho cả request thay vì để mỗi summary_ads_* tự
    # re-query sale_report_channel_ids()/_ads_excluded_channel_ids() (audit
    # 2026-07 follow-up task 11) — hành vi không đổi, chỉ giảm số query.
    sale_channel_ids, excluded_ids = OrderRepository.ads_channel_scope()

    data = {
        "revenue_by_channel": OrderRepository.summary_ads_revenue_by_channel(
            date_from=date_from,
            date_to=date_to,
            user=request.user,
            channel_ids=channel_ids or None,
            sale_channel_ids=sale_channel_ids,
            excluded_ids=excluded_ids,
        ),
        "top_products": OrderRepository.summary_ads_top_products(
            date_from=date_from,
            date_to=date_to,
            user=request.user,
            channel_ids=channel_ids or None,
            sale_channel_ids=sale_channel_ids,
            excluded_ids=excluded_ids,
        ),
        "daily_breakdown": OrderRepository.summary_ads_daily_breakdown(
            date_from=date_from,
            date_to=date_to,
            user=request.user,
            channel_ids=channel_ids or None,
            sale_channel_ids=sale_channel_ids,
            excluded_ids=excluded_ids,
            product_ids=product_ids or None,
        ),
        # Chi phí đã quy được về sản phẩm. Frontend lấy tổng chi phí trừ đi số này ra
        # rổ "Chưa gán chiến dịch" — xem summary_ads_assigned_cost về lý do dùng phép
        # trừ thay vì cộng riêng phần chưa gán.
        "assigned_ads_cost": OrderRepository.summary_ads_assigned_cost(
            date_from=date_from,
            date_to=date_to,
            user=request.user,
            channel_ids=channel_ids or None,
            sale_channel_ids=sale_channel_ids,
            excluded_ids=excluded_ids,
        ),
        "revenue_by_province": OrderRepository.summary_ads_revenue_by_province(
            date_from=date_from,
            date_to=date_to,
            user=request.user,
            channel_ids=channel_ids or None,
            sale_channel_ids=sale_channel_ids,
            excluded_ids=excluded_ids,
        ),
        "revenue_by_gender": OrderRepository.summary_ads_revenue_by_gender(
            date_from=date_from,
            date_to=date_to,
            user=request.user,
            channel_ids=channel_ids or None,
            sale_channel_ids=sale_channel_ids,
            excluded_ids=excluded_ids,
        ),
    }
    return data


@extend_schema(
    parameters=[
        OpenApiParameter("date_from", str),
        OpenApiParameter("date_to", str),
        OpenApiParameter("channel_ids", str),
        OpenApiParameter("product_ids", str),
        OpenApiParameter(
            "group",
            str,
            description='"channel" (mặc định) gộp cả kỳ theo kênh; "day" thêm chiều ngày.',
        ),
    ],
    responses={
        200: inline_serializer(
            "AdsReportProductBreakdownResponse",
            fields={"rows": drf_serializers.JSONField()},
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("ads-report")])
def ads_report_product_breakdown(request):
    """Dòng con theo sản phẩm khi bung một hàng ở 2 tab "theo digital".

    Tách khỏi ``ads_report_summary`` để tải LƯỜI: chỉ bắn khi người dùng thật sự bung
    một hàng, và payload không bị nhân theo (ngày × kênh × sản phẩm) trong khối
    ``daily_breakdown`` vốn nuôi cả tab Tổng quan.
    """
    date_from = request.query_params.get("date_from", "")
    date_to = request.query_params.get("date_to", "")
    channel_ids = _int_list(request, "channel_ids")
    product_ids = _int_list(request, "product_ids")
    group_by_date = request.query_params.get("group", "channel") == "day"

    sale_channel_ids, excluded_ids = OrderRepository.ads_channel_scope()
    rows = OrderRepository.summary_ads_product_breakdown(
        date_from=date_from,
        date_to=date_to,
        user=request.user,
        channel_ids=channel_ids or None,
        sale_channel_ids=sale_channel_ids,
        excluded_ids=excluded_ids,
        product_ids=product_ids or None,
        group_by_date=group_by_date,
    )
    return Response({"rows": rows})



def _overview_timeseries_points():
    """Một mức của `revenue_timeseries` — cùng shape cho cả 3 mức."""
    return inline_serializer(
        "OverviewTimeseriesPoint",
        fields={
            "bucket": drf_serializers.CharField(),
            "sale_channel": drf_serializers.CharField(),
            "revenue": drf_serializers.CharField(),
            "orders": drf_serializers.IntegerField(),
        },
        many=True,
    )


@extend_schema(
    responses={
        200: inline_serializer(
            "OverviewReportSummaryResponse",
            fields={
                # Tổng = 4 rổ dưới cộng lại (giao thành công + chờ giao + hoàn +
                # rủi ro hoàn); ads_cost là tiền chi ra, ngoài phép cộng.
                "total_revenue": drf_serializers.CharField(),
                "total_orders": drf_serializers.IntegerField(),
                "delivered_revenue": drf_serializers.CharField(),
                "delivered_orders": drf_serializers.IntegerField(),
                "awaiting_revenue": drf_serializers.CharField(),
                "awaiting_orders": drf_serializers.IntegerField(),
                "returned_revenue": drf_serializers.CharField(),
                "returned_orders": drf_serializers.IntegerField(),
                "return_risk_revenue": drf_serializers.CharField(),
                "return_risk_orders": drf_serializers.IntegerField(),
                # `ads_cost` = tổng 2 nền tảng; hàng KPI dùng 2 field tách.
                # Cả ba chỉ khác 0 khi lọc sale_channel=off_marketplace.
                "ads_cost": drf_serializers.CharField(),
                "ads_cost_facebook": drf_serializers.CharField(),
                "ads_cost_google": drf_serializers.CharField(),
                # Khối doanh thu có thêm cặp cột "giao thành công"; khối hoàn/rủi
                # ro hoàn không có (đơn hoàn/rủi ro hoàn không thể là đơn giao
                # thành công).
                "revenue_by_sale_channel": inline_serializer(
                    "OverviewSaleChannelRevenue",
                    fields={
                        "sale_channel": drf_serializers.CharField(),
                        "revenue": drf_serializers.CharField(),
                        "orders": drf_serializers.IntegerField(),
                        "delivered_revenue": drf_serializers.CharField(),
                        "delivered_orders": drf_serializers.IntegerField(),
                    },
                    many=True,
                ),
                "returned_by_sale_channel": inline_serializer(
                    "OverviewSaleChannelAmount",
                    fields={
                        "sale_channel": drf_serializers.CharField(),
                        "revenue": drf_serializers.CharField(),
                        "orders": drf_serializers.IntegerField(),
                    },
                    many=True,
                ),
                "return_risk_by_sale_channel": inline_serializer(
                    "OverviewSaleChannelAmount",
                    fields={
                        "sale_channel": drf_serializers.CharField(),
                        "revenue": drf_serializers.CharField(),
                        "orders": drf_serializers.IntegerField(),
                    },
                    many=True,
                ),
                # Phân bổ địa lý của Tổng doanh thu (vùng: key trong REGION_LABELS).
                "revenue_by_region": inline_serializer(
                    "OverviewRegionRevenue",
                    fields={
                        "region": drf_serializers.CharField(),
                        "revenue": drf_serializers.CharField(),
                        "orders": drf_serializers.IntegerField(),
                        "delivered_revenue": drf_serializers.CharField(),
                        "delivered_orders": drf_serializers.IntegerField(),
                    },
                    many=True,
                ),
                "revenue_by_province": inline_serializer(
                    "OverviewProvinceRevenue",
                    fields={
                        "province": drf_serializers.CharField(),
                        "revenue": drf_serializers.CharField(),
                        "orders": drf_serializers.IntegerField(),
                        "delivered_revenue": drf_serializers.CharField(),
                        "delivered_orders": drf_serializers.IntegerField(),
                    },
                    many=True,
                ),
                # Diễn biến doanh thu THUẦN theo thời gian. `bucket` là chuỗi:
                # by_day "YYYY-MM-DD", by_hour "0".."23", by_weekday "1".."7"
                # (isoweekday, 1 = Thứ Hai). Chỉ có bucket CÓ dữ liệu — FE tự dựng
                # đủ trục và điền 0.
                "revenue_timeseries": inline_serializer(
                    "OverviewRevenueTimeseries",
                    fields={
                        "by_day": _overview_timeseries_points(),
                        "by_hour": _overview_timeseries_points(),
                        "by_weekday": _overview_timeseries_points(),
                    },
                ),
                # Top sản phẩm theo doanh thu THUẦN cộng theo DÒNG HÀNG
                # (line_total), giảm dần. Tổng các SP không buộc bằng ô KPI Doanh
                # thu thuần (chiết khấu cấp đơn / phí ship không nằm ở dòng hàng).
                "top_products": inline_serializer(
                    "OverviewTopProduct",
                    fields={
                        "product_id": drf_serializers.IntegerField(),
                        "sku": drf_serializers.CharField(),
                        "name": drf_serializers.CharField(),
                        "quantity": drf_serializers.IntegerField(),
                        "orders": drf_serializers.IntegerField(),
                        "revenue": drf_serializers.CharField(),
                        "delivered_orders": drf_serializers.IntegerField(),
                        "delivered_revenue": drf_serializers.CharField(),
                    },
                    many=True,
                ),
            },
        ),
    },
)
@extend_schema(
    parameters=[
        OpenApiParameter(
            name="product_ids",
            description=(
                "Lọc theo sản phẩm (id, phân tách bằng dấu phẩy). Khi có, mọi số "
                "tiền là tiền của riêng các sản phẩm này; chi phí Ads giữ nguyên tổng."
            ),
            required=False,
            type=str,
        ),
        OpenApiParameter(
            name="sale_channel",
            description=(
                "Ô 'Nguồn': 'marketplace' (nội sàn: Shopee/TikTok) hoặc "
                "'off_marketplace' (ngoại sàn: phần còn lại). Bỏ trống = tất cả."
            ),
            required=False,
            type=str,
        ),
        OpenApiParameter(
            name="sale_channel_group",
            description=(
                "Ô 'Kênh bán': lọc sâu thêm một tầng trong 'Nguồn' đang chọn. "
                "Nguồn bỏ trống → 'marketplace'/'off_marketplace'; nội sàn → "
                "'shopee'/'tiktok'; ngoại sàn → 'facebook'/'web'/'off_other'. "
                "Nhóm không thuộc tầng đang chọn bị bỏ qua."
            ),
            required=False,
            type=str,
        ),
    ],
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("overview-report")])
def overview_report_summary(request):
    """Báo cáo tổng toàn công ty — xem summary_overview về định nghĩa bucket."""
    date_from = request.query_params.get("date_from", "")
    date_to = request.query_params.get("date_to", "")
    # product_ids: id sản phẩm, nhận cả dạng lặp param lẫn "1,2,3" (như channel_ids
    # của báo cáo ads). Giá trị không phải số bị bỏ qua thay vì trả 400.
    product_ids: list[int] = []
    for raw in request.query_params.getlist("product_ids"):
        for piece in str(raw).split(","):
            piece = piece.strip()
            if not piece:
                continue
            try:
                product_ids.append(int(piece))
            except ValueError:
                continue
    sale_channel = (request.query_params.get("sale_channel") or "").strip()
    sale_channel_group = (request.query_params.get("sale_channel_group") or "").strip()
    return Response(
        _cached_report(
            request,
            name="overview",
            scope="global",
            compute=lambda: OrderRepository.summary_overview(
                date_from=date_from,
                date_to=date_to,
                product_ids=product_ids or None,
                sale_channel=sale_channel or None,
                sale_channel_group=sale_channel_group or None,
            ),
        )
    )
