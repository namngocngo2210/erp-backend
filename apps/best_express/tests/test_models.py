import uuid

import pytest
from django.db import IntegrityError
from django.test import override_settings

from apps.best_express.models import BestShipment, BestShipmentStatus, BestWebhookEvent

from ._factories import make_account


@pytest.mark.django_db
def test_password_encrypted_at_rest_and_decrypted_on_read():
    account = make_account(password="s3cret")
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("SELECT password FROM best_express_accounts WHERE id = %s", [account.pk])
        stored = cur.fetchone()[0]
    assert stored != "s3cret"
    account.refresh_from_db()
    assert account.password == "s3cret"


@pytest.mark.django_db
@override_settings(
    BEST_EXPRESS_BASE_URL_TEST="http://test.example",
    BEST_EXPRESS_BASE_URL_PROD="http://prod.example",
)
def test_resolve_base_url():
    assert make_account(environment="test").resolve_base_url() == "http://test.example"
    assert make_account(environment="production").resolve_base_url() == "http://prod.example"
    assert make_account(base_url="http://override.example").resolve_base_url() == "http://override.example"


@pytest.mark.django_db
def test_shipment_defaults():
    account = make_account()
    shipment = BestShipment.objects.create(
        account=account, merchant_order_code=f"DH-{uuid.uuid4()}"
    )
    assert shipment.status == BestShipmentStatus.CREATED
    assert shipment.cod == 0
    assert shipment.code_best is None


@pytest.mark.django_db
def test_webhook_event_unique_constraint():
    account = make_account()
    kwargs = {
        "account": account,
        "code_best": "84855000000001",
        "merchant_order_code": "DH-1",
        "best_status_code": "666",
        "payload": {"status": "666"},
        "modified_at_ms": 1700000000000,
    }
    BestWebhookEvent.objects.create(**kwargs)
    with pytest.raises(IntegrityError):
        BestWebhookEvent.objects.create(**kwargs)
