import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.db import IntegrityError

from apps.best_express.application.services import shipment_service
from apps.best_express.models import BestShipmentStatus
from apps.best_express.exceptions import BestExpressError

from ._factories import make_account


def valid_payload(code=None) -> dict:
    return {
        "Code": code or f"DH-{uuid.uuid4()}",
        "ProductName": "Ốp lưng",
        "CollectAmount": 180000,
        "ProductPrice": 180000,
        "JourneyType": 1,
        "ServiceId": 12491,
        "PickupType": 2,
        "Weight": 600,
        "SourceCity": "Hà Nội",
        "SourceWard": "Phường Thượng Đình",
        "SourceAddress": "Nguyễn Trãi",
        "SourceName": "A",
        "SourcePhoneNumber": "0900000001",
        "DestCity": "Hồ Chí Minh",
        "DestWard": "Phường 13",
        "DestAddress": "Cộng Hòa",
        "DestName": "B",
        "DestPhoneNumber": "0900000002",
        "ReturnCity": "Hà Nội",
        "ReturnWard": "Phường Thượng Đình",
        "ReturnAddress": "Nguyễn Trãi",
        "ReturnName": "A",
        "ReturnPhoneNumber": "0900000001",
    }


class FakeClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def create_order(self, payload):
        self.calls.append(("create_order", payload))
        return self.responses.get(
            "create_order", {"Result": 1, "Code": "84855000000001", "RouteCode": "SG9"}
        )

    def cancel_order(self, code):
        self.calls.append(("cancel_order", code))
        return {"Result": 1, "Message": None}

    def update_order(self, payload):
        self.calls.append(("update_order", payload))
        return {"Result": 1, "Message": ""}

    def update_address(self, payload):
        self.calls.append(("update_address", payload))
        return {"Result": 1, "Message": "", "AddressTypeId": payload["AddressTypeId"]}


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(
        shipment_service, "BestExpressClient", lambda account: client
    )
    return client


@pytest.mark.django_db
def test_create_shipment_success(fake_client):
    account = make_account()
    payload = valid_payload()
    shipment = shipment_service.create_shipment(account, payload)
    assert shipment.code_best == "84855000000001"
    assert shipment.route_code == "SG9"
    assert shipment.status == BestShipmentStatus.CREATED
    assert shipment.cod == Decimal("180000")
    assert shipment.weight == 600
    # District trống được tự thêm (địa chỉ 2 cấp mới)
    sent = fake_client.calls[0][1]
    assert sent["SourceDistrict"] == ""
    assert sent["DestDistrict"] == ""
    assert sent["ReturnDistrict"] == ""


@pytest.mark.django_db
def test_create_shipment_missing_required_field(fake_client):
    account = make_account()
    payload = valid_payload()
    del payload["DestPhoneNumber"]
    with pytest.raises(BestExpressError) as exc:
        shipment_service.create_shipment(account, payload)
    assert "DestPhoneNumber" in exc.value.message
    assert fake_client.calls == []


@pytest.mark.django_db
def test_create_shipment_duplicate_merchant_code(fake_client):
    account = make_account()
    payload = valid_payload()
    shipment_service.create_shipment(account, payload)
    fake_client.responses["create_order"] = {"Result": 1, "Code": "848other"}
    with pytest.raises(BestExpressError):
        shipment_service.create_shipment(account, valid_payload(code=payload["Code"]))


@pytest.mark.django_db
def test_cancel_shipment(fake_client):
    account = make_account()
    shipment = shipment_service.create_shipment(account, valid_payload())
    shipment = shipment_service.cancel_shipment(shipment)
    assert shipment.status == BestShipmentStatus.CANCELLED
    assert fake_client.calls[-1] == ("cancel_order", "84855000000001")


@pytest.mark.django_db
def test_update_shipment_merges_changes(fake_client):
    account = make_account()
    shipment = shipment_service.create_shipment(account, valid_payload())
    shipment = shipment_service.update_shipment(
        shipment, {"CollectAmount": 200000, "Weight": 700}
    )
    assert shipment.cod == Decimal("200000")
    assert shipment.weight == 700
    action, sent = fake_client.calls[-1]
    assert action == "update_order"
    assert sent["Code"] == "84855000000001"
    assert sent["CollectAmount"] == 200000


@pytest.mark.django_db
def test_create_shipment_race_duplicate_raises_best_error(fake_client, monkeypatch):
    account = make_account()

    class RaceManager:
        def filter(self, **kwargs):
            return SimpleNamespace(exists=lambda: False)

        def create(self, **kwargs):
            raise IntegrityError("duplicate key value violates unique constraint")

    monkeypatch.setattr(
        shipment_service,
        "BestShipment",
        SimpleNamespace(objects=RaceManager()),
    )
    with pytest.raises(BestExpressError) as exc:
        shipment_service.create_shipment(account, valid_payload())
    assert "tạo trùng" in exc.value.message
    # Best đã nhận lệnh tạo trước khi race xảy ra
    assert fake_client.calls[0][0] == "create_order"


@pytest.mark.django_db
def test_update_shipment_address(fake_client):
    account = make_account()
    shipment = shipment_service.create_shipment(account, valid_payload())
    result = shipment_service.update_shipment_address(
        shipment,
        {
            "AddressTypeId": 2,
            "Address": "Thảo Điền",
            "CityName": "Hồ Chí Minh",
            "DistrictName": "",
            "WardName": "Thảo Điền",
            "ContactName": "B",
            "PhoneNumber1": "0900000002",
        },
    )
    assert result["Result"] == 1
    action, sent = fake_client.calls[-1]
    assert action == "update_address"
    assert sent["Code"] == "84855000000001"
