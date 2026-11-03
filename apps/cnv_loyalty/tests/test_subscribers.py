"""Trigger enqueue: event *_saved (phát từ Model.save, sau commit) + bus event
orders.status_changed, gate CNV_SYNC_ENABLED."""

from unittest.mock import patch

import pytest

from apps.common.events import event_bus
from apps.orders.contracts.order_events import OrderStatusChanged
from apps.orders.models import OrderStatus

pytestmark = pytest.mark.django_db


def test_order_create_enqueues_sync(settings, order_factory, django_capture_on_commit_callbacks):
    settings.CNV_SYNC_ENABLED = True
    with (
        patch("apps.cnv_loyalty.tasks.sync_order.delay") as sync_order_delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        order = order_factory()
    sync_order_delay.assert_any_call(order.pk)


def test_disabled_flag_skips_enqueue(settings, order_factory, django_capture_on_commit_callbacks):
    settings.CNV_SYNC_ENABLED = False
    with (
        patch("apps.cnv_loyalty.tasks.sync_order.delay") as sync_order_delay,
        patch("apps.cnv_loyalty.tasks.sync_customer.delay") as sync_customer_delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        order_factory()
    sync_order_delay.assert_not_called()
    sync_customer_delay.assert_not_called()


def test_customer_save_enqueues_sync(settings, order_factory, django_capture_on_commit_callbacks):
    settings.CNV_SYNC_ENABLED = True
    order = order_factory()
    with (
        patch("apps.cnv_loyalty.tasks.sync_customer.delay") as sync_customer_delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        order.customer.save()
    sync_customer_delay.assert_any_call(order.customer_id)


def test_status_changed_event_enqueues_sync(
    settings, order_factory, django_capture_on_commit_callbacks
):
    settings.CNV_SYNC_ENABLED = True
    order = order_factory()
    with (
        patch("apps.cnv_loyalty.tasks.sync_order.delay") as sync_order_delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        event_bus.publish(
            OrderStatusChanged(
                order_id=order.pk,
                old_status=OrderStatus.NEW,
                new_status=OrderStatus.DELIVERED_SUCCESS,
            )
        )
    sync_order_delay.assert_any_call(order.pk)


def test_irrelevant_update_fields_skip_enqueue(
    settings, order_factory, django_capture_on_commit_callbacks
):
    settings.CNV_SYNC_ENABLED = True
    order = order_factory()
    with (
        patch("apps.cnv_loyalty.tasks.sync_order.delay") as sync_order_delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        order.save(update_fields=["updated_at"])
    sync_order_delay.assert_not_called()


def test_relevant_update_fields_enqueue(
    settings, order_factory, django_capture_on_commit_callbacks
):
    settings.CNV_SYNC_ENABLED = True
    order = order_factory()
    order.status = OrderStatus.DELIVERED_SUCCESS
    with (
        patch("apps.cnv_loyalty.tasks.sync_order.delay") as sync_order_delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        order.save(update_fields=["status"])
    sync_order_delay.assert_any_call(order.pk)


def test_product_save_enqueues_sync(settings, db, django_capture_on_commit_callbacks):
    from apps.catalog.models import Product

    settings.CNV_SYNC_ENABLED = True
    with (
        patch("apps.cnv_loyalty.tasks.sync_product.delay") as sync_product_delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        product = Product.objects.create(sku="CNV-EVT-1", name="SP event")
    sync_product_delay.assert_any_call(product.pk)


def test_category_save_enqueues_sync(settings, db, django_capture_on_commit_callbacks):
    from apps.catalog.models import ProductCategory

    settings.CNV_SYNC_ENABLED = True
    with (
        patch("apps.cnv_loyalty.tasks.sync_collection.delay") as sync_collection_delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        category = ProductCategory.objects.create(code="cnv-evt", name="DM event")
    sync_collection_delay.assert_any_call(category.pk)


def test_soft_deleted_product_skips_enqueue(settings, db, django_capture_on_commit_callbacks):
    from django.utils import timezone

    from apps.catalog.models import Product

    settings.CNV_SYNC_ENABLED = True
    with (
        patch("apps.cnv_loyalty.tasks.sync_product.delay") as sync_product_delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        Product.objects.create(sku="CNV-EVT-2", name="SP xoá", deleted_at=timezone.now())
    sync_product_delay.assert_not_called()


def test_rollback_skips_enqueue(settings, order_factory):
    """Event phát sau commit: transaction rollback thì không enqueue gì."""
    from django.db import transaction

    settings.CNV_SYNC_ENABLED = True
    order = order_factory()
    order.status = OrderStatus.DELIVERED_SUCCESS
    with patch("apps.cnv_loyalty.tasks.sync_order.delay") as sync_order_delay:
        try:
            with transaction.atomic():
                order.save(update_fields=["status"])
                raise RuntimeError("rollback")
        except RuntimeError:
            pass
    sync_order_delay.assert_not_called()
