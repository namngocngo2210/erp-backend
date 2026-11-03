import csv

from django.contrib import admin
from django.http import HttpResponse

from .models import AssessmentFieldDef, LandingIngestTrace, Order

admin.site.register(Order)


@admin.register(AssessmentFieldDef)
class AssessmentFieldDefAdmin(admin.ModelAdmin):
    list_display = ("code", "label", "kind", "ai_enabled", "is_active", "sort_order", "updated_at")
    list_editable = ("ai_enabled", "is_active", "sort_order")
    list_filter = ("kind", "ai_enabled", "is_active")
    search_fields = ("code", "label")
    ordering = ("sort_order", "id")

    def has_delete_permission(self, request, obj=None):
        # Spec §2.1: đã có dữ liệu thì không hard-delete — chỉ tắt is_active.
        if obj is None:
            return super().has_delete_permission(request, obj)
        from apps.orders.models import OrderAssessment

        return not OrderAssessment.objects.filter(extra_fields__has_key=obj.code).exists()


@admin.register(LandingIngestTrace)
class LandingIngestTraceAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "created_at",
        "status",
        "is_dry_run",
        "phone",
        "name",
        "product_code",
        "url_page",
        "order_id",
        "task_id",
    )
    list_filter = ("status", "is_dry_run", "created_at")
    search_fields = ("phone", "name", "product_code", "url_page", "task_id")
    readonly_fields = (
        "created_at",
        "updated_at",
        "task_id",
        "payload",
        "validation_errors",
        "order",
    )
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    list_per_page = 50
    actions = ("export_as_csv",)

    @admin.action(description="Export selected traces as CSV")
    def export_as_csv(self, _request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="landing_ingest_traces.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "id",
            "created_at",
            "status",
            "is_dry_run",
            "phone",
            "name",
            "raw_address",
            "product_code",
            "url_page",
            "order_id",
            "task_id",
            "error_message",
        ])
        for trace in queryset.iterator():
            writer.writerow([
                trace.id,
                trace.created_at.isoformat(),
                trace.status,
                trace.is_dry_run,
                trace.phone,
                trace.name,
                trace.raw_address,
                trace.product_code,
                trace.url_page,
                trace.order_id or "",
                trace.task_id,
                trace.error_message,
            ])
        return response
