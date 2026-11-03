from django.urls import path

from .views import (
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

urlpatterns = [
    path("apps", list_create_apps, name="fb-apps-list-create"),
    path("apps/<int:app_id>", update_delete_app, name="fb-apps-update-delete"),
    path("apps/<int:app_id>/sync", sync_ad_accounts, name="fb-apps-sync"),
    path("apps/<int:app_id>/ad-accounts", list_app_ad_accounts, name="fb-apps-ad-accounts"),
    path("apps/<int:app_id>/insights-batch", insights_batch, name="fb-apps-insights-batch"),
    path("apps/<int:app_id>/daily-spend", daily_spend_summary, name="fb-apps-daily-spend"),
    path("apps/<int:app_id>/sync-daily-spend", sync_daily_spend, name="fb-apps-sync-daily-spend"),
    path("ad-accounts", list_all_ad_accounts, name="fb-ad-accounts-list"),
    path("ad-accounts/<int:ad_account_id>/insights", ad_account_insights, name="fb-adacc-insights"),
    path("reports/page", page_cost_report, name="fb-page-cost-report"),
    path("reports/campaign", campaign_cost_report, name="fb-campaign-cost-report"),
    path("campaign-products", campaign_products, name="fb-campaign-products"),
]
