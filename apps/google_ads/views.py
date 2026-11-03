"""Facade module to preserve public imports while delegating to layered implementation."""

from .transport.views.google_ads_views_impl import (
    campaign_cost_report,
    campaign_products,
    daily_spend_summary,
    list_all_accounts,
    list_config_accounts,
    list_create_configs,
    sync_accounts,
    sync_daily_spend,
    update_delete_config,
)

__all__ = [
    "campaign_cost_report",
    "campaign_products",
    "daily_spend_summary",
    "list_all_accounts",
    "list_config_accounts",
    "list_create_configs",
    "sync_accounts",
    "sync_daily_spend",
    "update_delete_config",
]
