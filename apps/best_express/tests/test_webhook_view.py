import uuid

import pytest
from rest_framework.test import APIClient

from apps.best_express.models import BestShipmentStatus
from apps.best_express.models import BestExpressAccount, BestShipment, BestWebhookEvent
from apps.best_express.transport import webhook_views

WEBHOOK_URL = "/api/best-express/webhook"


def make_account(webhook_token="hook-secret") -> BestExpressAccount:
    return BestExpressAccount.objects.create(
        name="T",
        username=f"u-{uuid.uuid4()}",
        password="pw",
        webhook_token=webhook_token,
    )


def auth_client(token="hook-secret") -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


@pytest.mark.django_db
def test_missing_or_wrong_token_401():
    make_account()
    assert APIClient().post(WEBHOOK_URL, {}, format="json").status_code == 401
    assert auth_client("wrong").post(WEBHOOK_URL, {}, format="json").status_code == 401


@pytest.mark.django_db
def test_valid_webhook_updates_shipment_and_returns_result_1():
    account = make_account()
    shipment = BestShipment.objects.create(
        account=account,
        merchant_order_code=f"DH-{uuid.uuid4()}",
        code_best=f"848{uuid.uuid4().hex[:10]}",
    )
    payload = {
        "code_best": shipment.code_best,
        "merchant_order_code": shipment.merchant_order_code,
        "status": "666",
        "status_content": "ok",
        "total_fee": 33880.0,
        "modified_at": 1700000000000,
    }
    response = auth_client().post(WEBHOOK_URL, payload, format="json")
    assert response.status_code == 200
    assert response.json() == {"Result": 1, "Message": ""}
    shipment.refresh_from_db()
    assert shipment.status == BestShipmentStatus.DELIVERED


@pytest.mark.django_db
def test_duplicate_webhook_still_returns_result_1():
    account = make_account()
    shipment = BestShipment.objects.create(
        account=account,
        merchant_order_code=f"DH-{uuid.uuid4()}",
        code_best=f"848{uuid.uuid4().hex[:10]}",
    )
    payload = {
        "code_best": shipment.code_best,
        "status": "202",
        "modified_at": 1700000000000,
    }
    assert auth_client().post(WEBHOOK_URL, payload, format="json").status_code == 200
    response = auth_client().post(WEBHOOK_URL, payload, format="json")
    assert response.status_code == 200
    assert response.json()["Result"] == 1
    assert BestWebhookEvent.objects.filter(code_best=shipment.code_best).count() == 1


@pytest.mark.django_db
def test_unmatched_code_stored_and_returns_result_1():
    make_account()
    payload = {"code_best": "848nope", "status": "666", "modified_at": 1700000000000}
    response = auth_client().post(WEBHOOK_URL, payload, format="json")
    assert response.status_code == 200
    assert response.json()["Result"] == 1
    event = BestWebhookEvent.objects.get(code_best="848nope")
    assert event.processed is False


@pytest.mark.django_db
def test_store_failure_returns_result_2(monkeypatch):
    make_account()

    def fail_store(account, payload):
        raise RuntimeError("db down")

    monkeypatch.setattr(webhook_views, "store_event", fail_store)
    payload = {"code_best": "848x", "status": "666", "modified_at": 1700000000000}
    response = auth_client().post(WEBHOOK_URL, payload, format="json")
    assert response.status_code == 500
    assert response.json()["Result"] == 2


@pytest.mark.django_db
def test_non_dict_body_400():
    make_account()
    response = auth_client().post(WEBHOOK_URL, ["not-a-dict"], format="json")
    assert response.status_code == 400
    assert response.json()["Result"] == 2

