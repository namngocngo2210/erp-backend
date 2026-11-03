"""Facade module to preserve public imports while delegating to layered implementation."""

from .transport.views.facebook_ads_views_impl import (
    ad_account_insights,
    campaign_cost_report,
    campaign_products,
    daily_spend_summary,
    insights_batch,
    list_all_ad_accounts,
    list_app_ad_accounts,
    list_create_apps,
    page_cost_report,
    sync_ad_accounts,
    sync_daily_spend,
    update_delete_app,
)

__all__ = [
    "ad_account_insights",
    "campaign_cost_report",
    "campaign_products",
    "daily_spend_summary",
    "insights_batch",
    "list_all_ad_accounts",
    "list_app_ad_accounts",
    "list_create_apps",
    "page_cost_report",
    "sync_ad_accounts",
    "sync_daily_spend",
    "update_delete_app",
]
