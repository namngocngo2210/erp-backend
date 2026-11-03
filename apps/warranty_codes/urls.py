from django.urls import path

from apps.warranty_codes.transport import batch_views, code_views

urlpatterns = [
    path("batches", batch_views.list_create_batches, name="warranty-code-batches"),
    path("batches/<int:batch_id>/assign", batch_views.assign_batch, name="warranty-code-batch-assign"),
    path("batches/<int:batch_id>/split", batch_views.split_batch, name="warranty-code-batch-split"),
    path("codes", code_views.list_codes, name="warranty-codes-list"),
    path("scan-logs", code_views.list_scan_logs, name="warranty-code-scan-logs"),
]
