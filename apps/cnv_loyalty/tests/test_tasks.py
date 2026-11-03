"""Cron retry/reconcile: chọn đúng record theo backoff + attempts, dispatch đúng task."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.cnv_loyalty import tasks
from apps.cnv_loyalty.models import CnvEntityType, CnvSyncRecord, CnvSyncStatus

pytestmark = pytest.mark.django_db


def _failed_record(entity_type, local_id, attempts, age_minutes):
    record = CnvSyncRecord.objects.create(
        entity_type=entity_type,
        local_id=local_id,
        status=CnvSyncStatus.FAILED,
        attempts=attempts,
    )
    CnvSyncRecord.objects.filter(pk=record.pk).update(
        updated_at=timezone.now() - timedelta(minutes=age_minutes)
    )
    return record


class TestRetryFailedRecords:
    def test_dispatches_due_records_only(self, settings):
        settings.CNV_SYNC_ENABLED = True
        # attempts=1 → backoff 2': record 5' tuổi đến hạn; record 30" chưa.
        _failed_record(CnvEntityType.ORDER, 1, attempts=1, age_minutes=5)
        _failed_record(CnvEntityType.ORDER, 2, attempts=3, age_minutes=1)
        with patch.object(tasks.sync_order, "delay") as sync_order_delay:
            result = tasks.retry_failed_records()
        assert result["dispatched"] == 1
        sync_order_delay.assert_called_once_with(1)

    def test_respects_max_attempts(self, settings):
        settings.CNV_SYNC_ENABLED = True
        _failed_record(CnvEntityType.CUSTOMER, 1, attempts=10, age_minutes=600)
        with patch.object(tasks.sync_customer, "delay") as sync_customer_delay:
            result = tasks.retry_failed_records()
        assert result["dispatched"] == 0
        sync_customer_delay.assert_not_called()

    def test_disabled_is_noop(self, settings):
        settings.CNV_SYNC_ENABLED = False
        _failed_record(CnvEntityType.ORDER, 1, attempts=1, age_minutes=60)
        with patch.object(tasks.sync_order, "delay") as sync_order_delay:
            result = tasks.retry_failed_records()
        assert result == {"dispatched": 0, "reason": "disabled"}
        sync_order_delay.assert_not_called()

    def test_dispatches_by_entity_type(self, settings):
        settings.CNV_SYNC_ENABLED = True
        _failed_record(CnvEntityType.PRODUCT, 7, attempts=0, age_minutes=5)
        with (
            patch.object(tasks.sync_product, "delay") as sync_product_delay,
            patch.object(tasks.sync_order, "delay") as sync_order_delay,
        ):
            tasks.retry_failed_records()
        sync_product_delay.assert_called_once_with(7)
        sync_order_delay.assert_not_called()


class TestReconcileFull:
    def test_dispatches_entities_without_record(self, settings, order_factory):
        settings.CNV_SYNC_ENABLED = True
        order = order_factory()
        with (
            patch.object(tasks.sync_order, "delay") as sync_order_delay,
            patch.object(tasks.sync_customer, "delay") as sync_customer_delay,
            patch.object(tasks.sync_product, "delay") as sync_product_delay,
            patch.object(tasks.sync_collection, "delay"),
        ):
            stats = tasks.reconcile_full()
        sync_order_delay.assert_any_call(order.pk)
        sync_customer_delay.assert_any_call(order.customer_id)
        assert sync_product_delay.called
        assert stats[CnvEntityType.ORDER] >= 1

    def test_tracked_entities_not_redispatched(self, settings, order_factory):
        settings.CNV_SYNC_ENABLED = True
        order = order_factory()
        CnvSyncRecord.objects.create(
            entity_type=CnvEntityType.ORDER, local_id=order.pk, status=CnvSyncStatus.SYNCED
        )
        with (
            patch.object(tasks.sync_order, "delay") as sync_order_delay,
            patch.object(tasks.sync_customer, "delay"),
            patch.object(tasks.sync_product, "delay"),
            patch.object(tasks.sync_collection, "delay"),
        ):
            tasks.reconcile_full()
        sync_order_delay.assert_not_called()
