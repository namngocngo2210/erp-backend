from django.contrib import admin

from .models import GoogleAdsAccount, GoogleAdsAccountDailySpend, GoogleAdsConfig


@admin.register(GoogleAdsConfig)
class GoogleAdsConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "login_customer_id", "created_at", "deleted_at")
    search_fields = ("name", "login_customer_id")


@admin.register(GoogleAdsAccount)
class GoogleAdsAccountAdmin(admin.ModelAdmin):
    list_display = ("id", "config", "name", "customer_id", "currency", "status", "last_synced_at")
    list_filter = ("status", "config", "currency")
    search_fields = ("name", "customer_id")


@admin.register(GoogleAdsAccountDailySpend)
class GoogleAdsAccountDailySpendAdmin(admin.ModelAdmin):
    list_display = ("id", "ad_account", "date", "spend", "impressions", "clicks", "last_synced_at")
    list_filter = ("date",)
    search_fields = ("ad_account__name", "ad_account__customer_id")
