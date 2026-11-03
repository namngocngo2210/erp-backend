from django.contrib import admin

from .models import FacebookAdAccount, FacebookApp


@admin.register(FacebookApp)
class FacebookAppAdmin(admin.ModelAdmin):
    list_display = (
        "id", "name", "app_id", "token_status", "token_expires_at",
        "token_checked_at", "created_at", "deleted_at",
    )
    list_filter = ("token_status",)
    search_fields = ("name", "app_id")
    # Do hệ thống ghi (debug_token / kết quả sync) — sửa tay chỉ che mất sự cố.
    readonly_fields = (
        "token_status", "token_expires_at", "data_access_expires_at",
        "token_checked_at", "token_invalid_since", "token_error",
    )


@admin.register(FacebookAdAccount)
class FacebookAdAccountAdmin(admin.ModelAdmin):
    list_display = ("id", "app", "name", "account_id", "currency", "spend_cap", "status", "last_synced_at")
    list_filter = ("status", "app", "currency")
    search_fields = ("name", "account_id", "fb_id")
