from django.urls import path

from apps.exports.transport import views

urlpatterns = [
    path("", views.exports_collection, name="export-collection"),
    path("<uuid:job_id>", views.export_detail, name="export-detail"),
    path("<uuid:job_id>/download", views.download_export, name="export-download"),
]
