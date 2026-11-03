from django.urls import path

from .transport.views import ads, definitions, health, leads_stats, orders_stats, pipeline, revenue, summary

urlpatterns = [
    path("health", health, name="daily-report-health"),
    path("leads/stats", leads_stats, name="daily-report-leads-stats"),
    path("orders/stats", orders_stats, name="daily-report-orders-stats"),
    path("revenue", revenue, name="daily-report-revenue"),
    path("pipeline", pipeline, name="daily-report-pipeline"),
    path("summary", summary, name="daily-report-summary"),
    path("ads", ads, name="daily-report-ads"),
    path("definitions", definitions, name="daily-report-definitions"),
]
