from django.urls import path

from .claim_views import (
    bulk_delete_warranty_claims,
    complete_warranty_claim,
    detail_update_delete_warranty_claim,
    list_create_warranty_claims,
)
from .ingest_views import warranty_claim_ingest
from .journey_views import add_journey, update_delete_journey
from .views import create_warranty_registration

urlpatterns = [
    path("", create_warranty_registration, name="warranty-create"),
    path("ingest", warranty_claim_ingest, name="warranty-ingest"),
    path("claims", list_create_warranty_claims, name="warranty-claims"),
    path(
        "claims/bulk-delete",
        bulk_delete_warranty_claims,
        name="warranty-claims-bulk-delete",
    ),
    path(
        "claims/<int:claim_id>",
        detail_update_delete_warranty_claim,
        name="warranty-claim-detail",
    ),
    path(
        "claims/<int:claim_id>/complete",
        complete_warranty_claim,
        name="warranty-claim-complete",
    ),
    path(
        "claims/<int:claim_id>/journeys",
        add_journey,
        name="warranty-claim-journeys",
    ),
    path("journeys/<int:journey_id>", update_delete_journey, name="warranty-journey-detail"),
]
