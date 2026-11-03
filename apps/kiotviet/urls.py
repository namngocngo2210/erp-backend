from django.urls import path

from .views import invoice_webhook

# Trang Đồng bộ (SyncPage) đã gỡ 2026-07-14 — 2 endpoint sync/channels + sync/run đi cùng
# nó (caller duy nhất). Đồng bộ định kỳ vẫn chạy qua celery task, thao tác tay qua CLI
# `sync_invoices_between_channels`.
urlpatterns = [
    path("invoice/channel/<int:channel_id>", invoice_webhook, name="kiotviet-invoice-webhook"),
]
