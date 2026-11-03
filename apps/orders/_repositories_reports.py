"""Facade tương thích ngược — nội dung đã tách sang package ``apps.orders.reporting``.

Refactor 2026-08 (audit backend-architecture TOP15 #2): file này từng là 1936
dòng trộn 4 miền báo cáo. Toàn bộ logic giờ nằm ở ``reporting/`` (shared /
sale_reports / delivery_quality / ads_reports / overview); module này CHỈ
re-export để mọi đường import cũ tiếp tục chạy nguyên trạng:

- ``apps/orders/repositories.py`` (đã chuyển sang import ``.reporting`` trực tiếp)
- ``apps/customers/report_crosstabs.py`` (``CLOSED_SALES_STATUS_CODES``; entry
  .importlinter Khối B trỏ đúng tên module này)
- ``apps/facebook_ads/*``, ``apps/google_ads/*`` (``ADS_DAILY_COST_MULTIPLIER``)
- test: ``test_ads_reports``, ``test_dashboard_summary_api``, ``test_overview_report``

LƯU Ý: monkeypatch hằng số module-level (vd ``SALE_FB_CHANNEL_IDS``) phải patch
vào ``apps.orders.reporting.shared`` — nơi ``_ads_excluded_channel_ids`` đọc
global lúc chạy. Patch lên module này chỉ đổi binding re-export, không có tác
dụng (backend/conftest.py, fixture ``_isolate_hardcoded_channel_ids`` đã trỏ
đúng chỗ).
"""
from .reporting import (
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
    OrderReportsMixin,
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
    "BOMB_ORDER_STATUSES",
    "CANCELLED_ORDER_Q",
    "CLOSED_REVENUE_SALES_STATUS_CODES",
    "CLOSED_SALES_STATUS_CODES",
    "DELIVERED_SUCCESS_ORDER_STATUS",
    "DIRTY_SALES_STATUS_CODES",
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
    "PENDING_DELIVERY_ORDER_STATUSES",
    "PENDING_DELIVERY_THRESHOLD_DAYS",
    "RETURN_ORDER_STATUSES",
    "RETURN_RISK_ORDER_STATUSES",
    "RETURNED_ORDER_STATUS",
    "SALE_CHANNEL_GROUPS",
    "SALE_CHANNEL_MARKETPLACE",
    "SALE_CHANNEL_OFF_MARKETPLACE",
    "SALE_FB_CHANNEL_CODE",
    "SALE_FB_CHANNEL_IDS",
    "SALE_SOURCE_REPORT_ORDER_SOURCES",
    "SENT_OUT_ORDER_STATUSES",
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
