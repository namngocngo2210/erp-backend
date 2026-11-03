import pytest

from apps.catalog.models import Product
from apps.warranty.claim_repository import WarrantyClaimRepository
from apps.warranty.journey_repository import WarrantyJourneyRepository
from apps.warranty.models import WarrantyClaim, WarrantyJourney
from apps.warranty.serializers import (
    WarrantyClaimOrderSerializer,
    WarrantyClaimSerializer,
    WarrantyClaimWriteSerializer,
    WarrantyJourneySerializer,
    WarrantyJourneyWriteSerializer,
)

# ---------------------------------------------------------------------------
# Serializers (từ test_serializers.py)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_claim_serializer_embeds_journeys(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    WarrantyJourney.objects.create(claim=claim, seq=1, receiver_name="Nam")
    data = WarrantyClaimSerializer(claim).data
    assert data["journeys"][0]["receiver_name"] == "Nam"
    assert "status_label" in data


def test_journey_write_validates_enum():
    s = WarrantyJourneyWriteSerializer(data={"classification": "bogus"})
    assert not s.is_valid()
    assert "classification" in s.errors


@pytest.mark.django_db
def test_claim_serializer_exposes_section1_fields(order_factory):
    import datetime

    order = order_factory()
    claim = WarrantyClaim.objects.create(
        order=order, customer_report_date=datetime.date(2026, 7, 1)
    )
    data = WarrantyClaimSerializer(claim).data
    assert data["customer_report_date"] == "2026-07-01"
    assert data["completed_at"] is None
    assert data["journeys"] == []


def test_journey_write_serializer_all_fields_optional():
    s = WarrantyJourneyWriteSerializer(data={})
    assert s.is_valid(), s.errors


@pytest.mark.django_db
def test_claim_write_serializer_validates_status(order_factory):
    s = WarrantyClaimWriteSerializer(data={"order_id": order_factory().id, "status": 999})
    assert not s.is_valid()
    assert "status" in s.errors


@pytest.mark.django_db
def test_journey_serializer_includes_product_and_status_label(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    journey = WarrantyJourney.objects.create(claim=claim, seq=1)
    data = WarrantyJourneySerializer(journey).data
    assert data["product_name"] == ""
    assert data["product_sku"] == ""
    assert data["status_label"] == "Mới"


@pytest.mark.django_db
def test_journey_serializer_exposes_vc_groups(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    journey = WarrantyJourney.objects.create(
        claim=claim, seq=1, new_tracking_code="B1", new_tracking_best_status="601"
    )
    data = WarrantyJourneySerializer(journey).data
    assert data["new_tracking_best_status"] == "601"
    assert data["vc_groups"] == {
        "new": "delivering", "recall": None, "return": None, "main": "delivering",
    }


@pytest.mark.django_db
def test_claim_order_serializer_exposes_invoice_created_at_and_channel(order_factory):
    order = order_factory(invoice_code="INV-001")
    data = WarrantyClaimOrderSerializer(order).data
    assert data["invoice_code"] == "INV-001"
    assert "created_at" in data
    assert data["kiotviet_channel_name"] == ""


# ---------------------------------------------------------------------------
# Repositories (từ test_repositories.py)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_next_seq(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    assert WarrantyJourneyRepository.next_seq(claim.id) == 1
    WarrantyJourney.objects.create(claim=claim, seq=1)
    WarrantyJourney.objects.create(claim=claim, seq=2)
    assert WarrantyJourneyRepository.next_seq(claim.id) == 3


@pytest.mark.django_db
def test_for_claim_orders_by_seq(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    j2 = WarrantyJourney.objects.create(claim=claim, seq=2)
    j1 = WarrantyJourney.objects.create(claim=claim, seq=1)

    result = WarrantyJourneyRepository.for_claim(claim.id)

    assert [j.id for j in result] == [j1.id, j2.id]


@pytest.mark.django_db
def test_for_claim_only_returns_own_claim_journeys(order_factory):
    claim1 = WarrantyClaim.objects.create(order=order_factory())
    claim2 = WarrantyClaim.objects.create(order=order_factory())
    WarrantyJourney.objects.create(claim=claim1, seq=1)
    j2 = WarrantyJourney.objects.create(claim=claim2, seq=1)

    result = WarrantyJourneyRepository.for_claim(claim2.id)

    assert [j.id for j in result] == [j2.id]


@pytest.mark.django_db
def test_get_returns_journey(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourney.objects.create(claim=claim, seq=1)

    result = WarrantyJourneyRepository.get(j.id)

    assert result is not None
    assert result.id == j.id


@pytest.mark.django_db
def test_get_returns_none_when_missing():
    assert WarrantyJourneyRepository.get(999999) is None


@pytest.mark.django_db
def test_claim_repository_base_qs_prefetches_journeys(order_factory, django_assert_num_queries):
    product = Product.objects.create(name="SP A", sku="SKU-WTY-1")
    claim = WarrantyClaim.objects.create(order=order_factory())
    WarrantyJourney.objects.create(claim=claim, seq=1, product=product)
    WarrantyJourney.objects.create(claim=claim, seq=2, product=product)

    with django_assert_num_queries(3):
        # 1 query for claim, 1 for journeys prefetch, 1 for journeys__product prefetch
        fetched = WarrantyClaimRepository.base_qs().get(id=claim.id)
        journeys = list(fetched.journeys.all())
        for j in journeys:
            _ = j.product

    assert len(journeys) == 2
