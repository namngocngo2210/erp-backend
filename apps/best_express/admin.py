from django.contrib import admin

from apps.best_express.models import BestExpressAccount, BestShipment, BestWebhookEvent


@admin.register(BestExpressAccount)
class BestExpressAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "environment", "username", "is_active", "token_expires_at")
    list_filter = ("environment", "is_active")


@admin.register(BestShipment)
class BestShipmentAdmin(admin.ModelAdmin):
    list_display = ("merchant_order_code", "code_best", "status", "best_status_code", "total_fee", "updated_at")
    list_filter = ("status",)
    search_fields = ("merchant_order_code", "code_best")
    raw_id_fields = ("order",)


@admin.register(BestWebhookEvent)
class BestWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("code_best", "merchant_order_code", "best_status_code", "processed", "received_at")
    list_filter = ("processed",)
    search_fields = ("code_best", "merchant_order_code")
