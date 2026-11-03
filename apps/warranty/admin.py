from django.contrib import admin

from .models import WarrantyRegistration


@admin.register(WarrantyRegistration)
class WarrantyRegistrationAdmin(admin.ModelAdmin):
    list_display = ("id", "source", "invoice_code", "full_name", "phone", "email", "created_at")
    list_filter = ("source", "created_at")
    search_fields = ("invoice_code", "full_name", "phone", "email")
    ordering = ("-created_at",)
