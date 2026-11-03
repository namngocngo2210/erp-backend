"""Package báo cáo đơn hàng — tách từ ``apps/orders/_repositories_reports.py``
(1936 dòng, 40+ classmethod trộn 4 miền) thành module theo miền nghiệp vụ:

- ``shared``: hằng số + helper dùng chung (trạng thái sale/giao hàng, scope kênh,
  hệ số chi phí ads, ``_ads_excluded_channel_ids``...).
- ``sale_reports``: báo cáo Sale (Nhóm 1/2/9/10, theo ngày, Sale FB).
- ``resale_reports``: doanh thu từ khách mua lại, gộp theo nhân viên sale.
- ``delivery_quality``: chất lượng data & giao hàng (Nhóm 3/5a/6/7, nhà mạng).
- ``ads_reports``: báo cáo Ads FB/Google + phân quyền kênh/TKQC.
- ``overview``: Báo cáo tổng (4 rổ theo trạng thái sale).

KHÔNG đổi hành vi. ``OrderReportsMixin`` bên dưới ghép 5 mixin lại — mọi method
là classmethod nên các helper nền (``_base_orders_queryset``,
``_sales_report_orders_queryset``, ``_apply_created_at_date_filters``,
``_exclude_hidden_branches``) resolve qua MRO khi mixin được kế thừa bởi
``OrderRepository`` (``repositories.py``), không cần import ngược (tránh vòng).

Đường import cũ ``apps.orders._repositories_reports`` vẫn sống (facade re-export)
— call site bên ngoài không phải đổi. Monkeypatch hằng số module-level
(``SALE_FB_CHANNEL_IDS``) phải nhắm vào ``apps.orders.reporting.shared``.
"""
from .ads_reports import AdsReportsMixin
from .delivery_quality import DeliveryQualityReportsMixin
from .overview import OverviewReportMixin
from .resale_reports import ResaleReportsMixin
from .sale_reports import SaleReportsMixin
from .shared import (
    ADS_DAILY_COST_MULTIPLIER,
    AWAITING_DELIVERY_ORDER_STATUSES,
    BOMB_ORDER_STATUSES,
    CANCELLED_ORDER_Q,
    CLOSED_REVENUE_SALES_STATUS_CODES,
    CLOSED_SALES_STATUS_CODES,
    DELIVERED_SUCCESS_ORDER_STATUS,
    DIRTY_SALES_STATUS_CODES,
    FACEBOOK_ADS_COST_MULTIPLIER_SCHEDULE,
    GOOGLE_ADS_DAILY_COST_MULTIPLIER,
    IN_DELIVERY_ORDER_STATUSES,
    KiotVietReportChannel,
    LEAD_ORDER_Q,
    MARKETPLACE_ORDER_Q,
    NOT_BUYING_SALES_STATUS_CODE,
    NURTURE_SALES_STATUS_CODES,
    NURTURE_SALES_STATUS_CODES_FOR_CAPACITY,
    OVERVIEW_EXCLUDED_BRANCH_IDS,
    PENDING_DELIVERY_ORDER_STATUSES,
    PENDING_DELIVERY_THRESHOLD_DAYS,
    RETURN_ORDER_STATUSES,
    RETURN_RISK_ORDER_STATUSES,
    RETURNED_ORDER_STATUS,
    SALE_CHANNEL_GROUPS,
    SALE_CHANNEL_MARKETPLACE,
    SALE_CHANNEL_OFF_MARKETPLACE,
    SALE_FB_CHANNEL_CODE,
    SALE_FB_CHANNEL_IDS,
    SALE_SOURCE_REPORT_ORDER_SOURCES,
    SENT_OUT_ORDER_STATUSES,
    SalesReportBranch,
    SalesReportChannel,
    UNDERLYING_CONDITION_SALES_STATUS_CODE,
    UNREACHABLE_SALES_STATUS_CODES,
    UNREACHED_SALES_STATUS_CODES,
    WANTS_STORE_VISIT_SALES_STATUS_CODE,
    WARRANTY_SALES_STATUS_CODE,
    _ads_excluded_channel_ids,
    _parse_report_date,
    _user_sees_all_ads_channels,
    facebook_ads_cost_multiplier,
    sale_report_channel_ids,
)

__all__ = [
    "ADS_DAILY_COST_MULTIPLIER",
    "AWAITING_DELIVERY_ORDER_STATUSES",
    "AdsReportsMixin",
    "BOMB_ORDER_STATUSES",
    "CANCELLED_ORDER_Q",
    "CLOSED_REVENUE_SALES_STATUS_CODES",
    "CLOSED_SALES_STATUS_CODES",
    "DELIVERED_SUCCESS_ORDER_STATUS",
    "DIRTY_SALES_STATUS_CODES",
    "DeliveryQualityReportsMixin",
    "FACEBOOK_ADS_COST_MULTIPLIER_SCHEDULE",
    "GOOGLE_ADS_DAILY_COST_MULTIPLIER",
    "IN_DELIVERY_ORDER_STATUSES",
    "KiotVietReportChannel",
    "LEAD_ORDER_Q",
    "MARKETPLACE_ORDER_Q",
    "NOT_BUYING_SALES_STATUS_CODE",
    "NURTURE_SALES_STATUS_CODES",
    "NURTURE_SALES_STATUS_CODES_FOR_CAPACITY",
    "OVERVIEW_EXCLUDED_BRANCH_IDS",
    "OrderReportsMixin",
    "OverviewReportMixin",
    "PENDING_DELIVERY_ORDER_STATUSES",
    "PENDING_DELIVERY_THRESHOLD_DAYS",
    "RETURN_ORDER_STATUSES",
    "RETURN_RISK_ORDER_STATUSES",
    "RETURNED_ORDER_STATUS",
    "ResaleReportsMixin",
    "SALE_CHANNEL_GROUPS",
    "SALE_CHANNEL_MARKETPLACE",
    "SALE_CHANNEL_OFF_MARKETPLACE",
    "SALE_FB_CHANNEL_CODE",
    "SALE_FB_CHANNEL_IDS",
    "SALE_SOURCE_REPORT_ORDER_SOURCES",
    "SENT_OUT_ORDER_STATUSES",
    "SaleReportsMixin",
    "SalesReportBranch",
    "SalesReportChannel",
    "UNDERLYING_CONDITION_SALES_STATUS_CODE",
    "UNREACHABLE_SALES_STATUS_CODES",
    "UNREACHED_SALES_STATUS_CODES",
    "WANTS_STORE_VISIT_SALES_STATUS_CODE",
    "WARRANTY_SALES_STATUS_CODE",
    "_ads_excluded_channel_ids",
    "_parse_report_date",
    "_user_sees_all_ads_channels",
    "facebook_ads_cost_multiplier",
    "sale_report_channel_ids",
]


class OrderReportsMixin(
    SaleReportsMixin,
    ResaleReportsMixin,
    DeliveryQualityReportsMixin,
    AdsReportsMixin,
    OverviewReportMixin,
):
    """Mixin providing summary_* report methods. Mixed into OrderRepository.

    Facade ghép 5 mixin theo miền — không thêm method nào của riêng nó. Thứ tự
    kế thừa không mang ý nghĩa: 5 mixin không có method trùng tên.
    """
