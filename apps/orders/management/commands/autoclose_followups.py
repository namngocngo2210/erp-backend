from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Max, Q
from django.utils import timezone

from apps.orders.models import Order, OrderAssessment, OrderJourneyEvent
from apps.orders.sheet_rules import (
    CASE_CLOSED_EVENT,
    CLOSED_SALES_STATUSES,
    FOLLOWUP_AUTOCLOSE_DAYS,
    SALE_CONTACT_EVENT,
    SHEET3_SOURCES,
    consulting_q,
)


class Command(BaseCommand):
    help = "Tự đóng (archive) các đơn follow-up Sheet 3 quá 14 ngày không liên hệ."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=FOLLOWUP_AUTOCLOSE_DAYS)
        contact_q = Q(journey_events__event_type=SALE_CONTACT_EVENT)
        qs = (
            Order.objects.filter(deleted_at__isnull=True, order_source__in=SHEET3_SOURCES)
            .filter(consulting_q())
            .exclude(assessment__sales_status_code__in=CLOSED_SALES_STATUSES)
            .annotate(_last=Max("journey_events__created_at", filter=contact_q))
        )
        stale = qs.filter(Q(_last__lte=cutoff) | (Q(_last__isnull=True) & Q(created_at__lte=cutoff)))
        count = 0
        for order in stale.iterator():
            assessment, _ = OrderAssessment.objects.get_or_create(order=order)
            prev = assessment.sales_status_code
            assessment.sales_status_code = "archived"
            assessment.save(update_fields=["sales_status_code", "updated_at"])
            OrderJourneyEvent.objects.create(
                order=order, event_type=CASE_CLOSED_EVENT, from_code=prev, to_code="archived",
                payload_json={"reason": None, "auto": True},
            )
            count += 1
        self.stdout.write(f"autoclosed={count}")
