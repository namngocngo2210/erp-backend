"""Tests retention landing_ingest_traces + order_journey_events (audit 2026-07 mục 3.4)."""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import LandingIngestTrace, Order, OrderJourneyEvent
from apps.orders.tasks import purge_old_ingest_traces, purge_old_journey_events


def _backdate(model, pk, days):
    model.objects.filter(pk=pk).update(created_at=timezone.now() - timedelta(days=days))


class PurgeIngestTracesTests(TestCase):
    @override_settings(LANDING_INGEST_TRACE_RETENTION_DAYS=120)
    def test_purges_old_traces_only(self):
        old = LandingIngestTrace.objects.create(source="landing", payload={})
        recent = LandingIngestTrace.objects.create(source="landing", payload={})
        _backdate(LandingIngestTrace, old.pk, 150)
        _backdate(LandingIngestTrace, recent.pk, 30)

        deleted = purge_old_ingest_traces()

        self.assertEqual(deleted, 1)
        self.assertFalse(LandingIngestTrace.objects.filter(pk=old.pk).exists())
        self.assertTrue(LandingIngestTrace.objects.filter(pk=recent.pk).exists())


class PurgeJourneyEventsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        channel = Channel.objects.create(code="retch", name="retention channel")
        customer = Customer.objects.create(full_name="Ret")
        cls.order = Order.objects.create(customer=customer, channel=channel)

    def test_default_zero_keeps_everything(self):
        ev = OrderJourneyEvent.objects.create(order=self.order, event_type="created")
        _backdate(OrderJourneyEvent, ev.pk, 4000)

        deleted = purge_old_journey_events()

        self.assertEqual(deleted, 0)
        self.assertTrue(OrderJourneyEvent.objects.filter(pk=ev.pk).exists())

    @override_settings(JOURNEY_EVENT_RETENTION_DAYS=365)
    def test_purges_when_enabled(self):
        old = OrderJourneyEvent.objects.create(order=self.order, event_type="created")
        recent = OrderJourneyEvent.objects.create(order=self.order, event_type="updated")
        _backdate(OrderJourneyEvent, old.pk, 400)

        deleted = purge_old_journey_events()

        self.assertEqual(deleted, 1)
        self.assertFalse(OrderJourneyEvent.objects.filter(pk=old.pk).exists())
        self.assertTrue(OrderJourneyEvent.objects.filter(pk=recent.pk).exists())
