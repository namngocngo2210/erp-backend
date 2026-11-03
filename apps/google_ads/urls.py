from django.urls import path

from .views import (
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

urlpatterns = [
    path("configs", list_create_configs, name="gads-configs-list-create"),
    path("configs/<int:config_id>", update_delete_config, name="gads-configs-update-delete"),
    path("configs/<int:config_id>/sync", sync_accounts, name="gads-configs-sync"),
    path("configs/<int:config_id>/sync-daily-spend", sync_daily_spend, name="gads-configs-sync-daily-spend"),
    path("configs/<int:config_id>/daily-spend", daily_spend_summary, name="gads-configs-daily-spend"),
    path("configs/<int:config_id>/accounts", list_config_accounts, name="gads-configs-accounts"),
    path("accounts", list_all_accounts, name="gads-accounts-list"),
    path("reports/campaign", campaign_cost_report, name="gads-campaign-cost-report"),
    path("campaign-products", campaign_products, name="gads-campaign-products"),
]
