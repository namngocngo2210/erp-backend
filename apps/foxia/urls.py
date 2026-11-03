from django.urls import path

from apps.foxia.transport.api_views import (
    list_channel_maps,
    marketing_report,
    sync_status,
    upsert_channel_map,
)
from apps.foxia.transport.link_config_views import (
    add_channels,
    delete_channel,
    link_config,
    save_cells,
)
from apps.foxia.transport.resale_views import resale_stats

urlpatterns = [
    path("reports/marketing", marketing_report, name="foxia-marketing-report"),
    path("channel-maps", list_channel_maps, name="foxia-channel-maps"),
    path("channel-maps/upsert", upsert_channel_map, name="foxia-channel-map-upsert"),
    path("sync-status", sync_status, name="foxia-sync-status"),
    # Cấu hình liên kết kênh × loại nguồn → idnguonkhachhang (spec 2026-09-09, đổi trục 2026-09-16)
    path("link-config", link_config, name="foxia-link-config"),
    path("link-config/channels", add_channels, name="foxia-link-config-channels"),
    path("link-config/channels/<int:pk>", delete_channel, name="foxia-link-config-channel"),
    path("link-config/cells", save_cells, name="foxia-link-config-cells"),
    # Thống kê cơ hội resale cho trang Chia đơn (docs/foxia-resale-delivered.md)
    path("resale-stats", resale_stats, name="foxia-resale-stats"),
]
