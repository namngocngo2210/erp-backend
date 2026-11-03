"""Backfill dữ liệu lịch sử sang CNV: products → collections → customers → orders.

Keyset pagination theo ``id`` (KHÔNG offset — sau PgBouncer transaction pooling,
xem d013caa2 "keyset pagination in backfill"), sleep ngắn giữa các batch để
không đập CNV. Idempotent: entity đã ``synced`` bị skip, chạy lại thoải mái.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from apps.cnv_loyalty.application.services.sync_service import CnvSyncService
from apps.cnv_loyalty.models import CnvEntityType, CnvSyncRecord, CnvSyncStatus

logger = logging.getLogger(__name__)

# Thứ tự bắt buộc: collection trước product (product sync tự gắn collect),
# customer/product trước order (order phụ thuộc cả hai).
DEFAULT_ENTITIES = ["collections", "products", "customers", "orders"]


class CnvBackfillService:
    def __init__(
        self,
        service: CnvSyncService,
        batch_size: int = 200,
        sleep_seconds: float = 0.2,
    ):
        self.service = service
        self.batch_size = batch_size
        self.sleep_seconds = sleep_seconds

    def run(
        self,
        entities: list[str] | None = None,
        since: datetime | None = None,
        dry_run: bool = False,
    ) -> dict[str, dict[str, int]]:
        stats: dict[str, dict[str, int]] = {}
        for entity in entities or DEFAULT_ENTITIES:
            runner = getattr(self, f"_backfill_{entity}", None)
            if runner is None:
                logger.warning("cnv backfill: entity lạ %r — bỏ qua", entity)
                continue
            stats[entity] = runner(since=since, dry_run=dry_run)
            logger.info("cnv backfill %s: %s", entity, stats[entity])
        return stats

    # -- per entity ---------------------------------------------------------

    def _backfill_collections(self, since, dry_run):
        from apps.catalog.models import ProductCategory

        queryset = ProductCategory.objects.filter(deleted_at__isnull=True)
        return self._walk(
            queryset, CnvEntityType.CUSTOM_COLLECTION, self.service.sync_collection, dry_run
        )

    def _backfill_products(self, since, dry_run):
        from apps.catalog.models import Product

        queryset = Product.objects.filter(deleted_at__isnull=True)
        return self._walk(queryset, CnvEntityType.PRODUCT, self.service.sync_product, dry_run)

    def _backfill_customers(self, since, dry_run):
        from apps.customers.models import Customer

        queryset = Customer.objects.filter(deleted_at__isnull=True)
        if since is not None:
            queryset = queryset.filter(created_at__gte=since)
        return self._walk(queryset, CnvEntityType.CUSTOMER, self.service.sync_customer, dry_run)

    def _backfill_orders(self, since, dry_run):
        from apps.orders.models import Order

        queryset = Order.objects.filter(deleted_at__isnull=True)
        if since is not None:
            queryset = queryset.filter(created_at__gte=since)
        return self._walk(queryset, CnvEntityType.ORDER, self.service.sync_order, dry_run)

    # -- core walk ----------------------------------------------------------

    def _walk(self, queryset, entity_type: str, sync_fn, dry_run: bool) -> dict[str, int]:
        stats = {"seen": 0, "skipped": 0, "synced": 0, "failed": 0}
        last_id = 0
        while True:
            ids = list(
                queryset.filter(id__gt=last_id)
                .order_by("id")
                .values_list("id", flat=True)[: self.batch_size]
            )
            if not ids:
                break
            last_id = ids[-1]
            stats["seen"] += len(ids)

            already_synced = set(
                CnvSyncRecord.objects.filter(
                    entity_type=entity_type,
                    local_id__in=ids,
                    status=CnvSyncStatus.SYNCED,
                ).values_list("local_id", flat=True)
            )
            stats["skipped"] += len(already_synced)
            pending_ids = [pk for pk in ids if pk not in already_synced]
            if dry_run:
                continue
            for pk in pending_ids:
                record = sync_fn(pk)
                key = "synced" if record.status == CnvSyncStatus.SYNCED else "failed"
                stats[key] += 1
            if self.sleep_seconds and pending_ids:
                time.sleep(self.sleep_seconds)
        return stats
