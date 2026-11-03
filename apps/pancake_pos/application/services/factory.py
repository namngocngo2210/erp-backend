"""Dựng `ProductSyncService` từ cấu hình hệ thống (sysconfig, nhóm integrations)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from apps.common.ops_alerts import send_ops_alert
from apps.pancake_pos.application.services.delivery_report_service import DeliveryReportService, StuckConfig
from apps.pancake_pos.application.services.delivery_sync_service import DeliverySyncService
from apps.pancake_pos.application.services.inbound_order_service import InboundConfig, InboundOrderService
from apps.pancake_pos.application.services.initial_stock_service import InitialStockPushService
from apps.pancake_pos.application.services.order_push_service import OrderPushService, PushConfig
from apps.pancake_pos.application.services.product_sync_service import ProductSyncService
from apps.pancake_pos.domain import inbound_order
from apps.pancake_pos.domain.order_push import parse_source_map
from apps.pancake_pos.inbound_repositories import status_map
from apps.pancake_pos.infrastructure.catalog_reader import SqlCatalogReader
from apps.pancake_pos.infrastructure.crm_delivery_gateway import SqlCrmDeliveryGateway
from apps.pancake_pos.infrastructure.crm_order_gateway import SqlCrmOrderGateway
from apps.pancake_pos.infrastructure.delivery_report_reader import SqlDeliveryReportReader
from apps.pancake_pos.infrastructure.order_reader import SqlOrderReader
from apps.pancake_pos.infrastructure.pancake_pos_client import PancakePosClient, PancakePosError
from apps.pancake_pos.infrastructure.stock_reader import SqlStockReader, SqlWarehouseReader
from apps.sysconfig import first_enabled_at, sysconfig

logger = logging.getLogger(__name__)


class PancakePosDisabled(PancakePosError):
    """Đồng bộ GHI bị tắt (PANCAKE_POS_ENABLED=false — staging luôn tắt)."""


def build_client() -> PancakePosClient:
    return PancakePosClient(
        api_key=sysconfig.get("PANCAKE_POS_API_KEY"),
        shop_id=sysconfig.get("PANCAKE_POS_SHOP_ID"),
        rps=sysconfig.get("PANCAKE_POS_RPS"),
        timeout=sysconfig.get("PANCAKE_POS_HTTP_TIMEOUT"),
    )


def ensure_write_enabled(dry_run: bool) -> None:
    """Dry-run chỉ đọc Pancake nên chạy được trước khi bật cờ; lượt ghi thật bắt buộc
    PANCAKE_POS_ENABLED (cờ staging_forbidden → staging không bao giờ ghi ra shop thật)."""
    if not dry_run and not sysconfig.get("PANCAKE_POS_ENABLED"):
        raise PancakePosDisabled("PANCAKE_POS_ENABLED đang tắt — chỉ chạy được --dry-run")


def build_product_sync_service(*, dry_run: bool) -> ProductSyncService:
    ensure_write_enabled(dry_run)
    return ProductSyncService(
        gateway=build_client(),
        catalog_reader=SqlCatalogReader(),
        default_weight_g=sysconfig.get("PANCAKE_POS_DEFAULT_WEIGHT_G"),
        include_components=sysconfig.get("PANCAKE_POS_INCLUDE_COMPONENTS"),
    )


def build_initial_stock_service(*, dry_run: bool) -> InitialStockPushService:
    ensure_write_enabled(dry_run)
    return InitialStockPushService(
        gateway=build_client(),
        stock_reader=SqlStockReader(),
        warehouse_reader=SqlWarehouseReader(),
        batch_size=sysconfig.get("PANCAKE_POS_STOCK_BATCH_SIZE"),
    )


def push_orders_enabled() -> bool:
    """Đẩy đơn chạy khi CẢ cờ ghi chung lẫn cờ đẩy đơn bật (cutover bật tay)."""
    return bool(sysconfig.get("PANCAKE_POS_ENABLED")) and bool(sysconfig.get("PANCAKE_POS_PUSH_ORDERS_ENABLED"))


def push_since() -> datetime | None:
    """Mốc bắt đầu đẩy đơn: `PANCAKE_POS_PUSH_ORDERS_SINCE`; trống → lần đầu bật cờ đẩy
    đơn (lịch sử cài đặt). Không có cả hai → chỉ còn cửa sổ lookback chặn đơn cũ."""
    return sysconfig.get_datetime("PANCAKE_POS_PUSH_ORDERS_SINCE") or first_enabled_at(
        "PANCAKE_POS_PUSH_ORDERS_ENABLED"
    )


def push_config() -> PushConfig:
    sources = tuple(
        s.strip().lower() for s in str(sysconfig.get("PANCAKE_POS_PUSH_ORDER_SOURCES") or "").split(",") if s.strip()
    )
    return PushConfig(
        sources=sources,
        lookback=timedelta(hours=max(1, int(sysconfig.get("PANCAKE_POS_PUSH_LOOKBACK_HOURS") or 72))),
        source_map=parse_source_map(str(sysconfig.get("PANCAKE_POS_ORDER_SOURCE_MAP") or "")),
        push_since=push_since(),
    )


def build_order_push_service() -> OrderPushService:
    return OrderPushService(
        gateway=build_client(), reader=SqlOrderReader(), alert=send_ops_alert, config=push_config()
    )


def inbound_config() -> InboundConfig:
    return InboundConfig(
        create_orders=bool(sysconfig.get("PANCAKE_POS_INBOUND_CREATE_ORDERS")),
        unmatched_after=timedelta(hours=max(1, int(sysconfig.get("PANCAKE_POS_INBOUND_UNMATCHED_HOURS") or 6))),
        source_map=inbound_order.parse_source_map(str(sysconfig.get("PANCAKE_POS_INBOUND_SOURCE_MAP") or "")),
        channel_map=inbound_order.parse_channel_map(str(sysconfig.get("PANCAKE_POS_INBOUND_CHANNEL_MAP") or "")),
    )


def _enqueue_delivery_sync(order_id: int) -> None:
    from apps.pancake_pos.tasks import sync_delivery_code

    try:
        sync_delivery_code.delay(order_id)
    except Exception:  # noqa: BLE001 — broker lỗi: sự kiện kế tiếp / backfill đồng bộ lại
        logger.exception("pancake_pos: không xếp được task đồng bộ trạng thái vận chuyển đơn %s", order_id)


def build_inbound_order_service(
    *, on_matched: Callable[[int], Any] | None = _enqueue_delivery_sync
) -> InboundOrderService:
    """Xử lý webhook KHÔNG gọi Pancake (chỉ DB) → không cần API key. `on_matched` chạy sau
    commit với id đơn CRM: mặc định xếp task đồng bộ "Trạng thái vận chuyển thực"."""
    return InboundOrderService(
        crm=SqlCrmOrderGateway(), alert=send_ops_alert, config=inbound_config(), on_matched=on_matched
    )


def dispatch_enabled() -> bool:
    return bool(sysconfig.get("PANCAKE_POS_DISPATCH_ENABLED"))


def build_delivery_sync_service() -> DeliverySyncService:
    return DeliverySyncService(crm=SqlCrmDeliveryGateway(), dispatch_enabled=dispatch_enabled())


def stuck_config() -> StuckConfig:
    return StuckConfig(
        not_sent_hours=max(1, int(sysconfig.get("PANCAKE_POS_STUCK_NOT_SENT_HOURS") or 24)),
        not_delivered_days=max(1, int(sysconfig.get("PANCAKE_POS_STUCK_NOT_DELIVERED_DAYS") or 5)),
        alert_hour=int(sysconfig.get("PANCAKE_POS_STUCK_ALERT_HOUR")),
    )


def build_delivery_report_service() -> DeliveryReportService:
    names = {code: entry.name for code, entry in status_map().items()}
    return DeliveryReportService(reader=SqlDeliveryReportReader(), config=stuck_config(), status_names=names)
