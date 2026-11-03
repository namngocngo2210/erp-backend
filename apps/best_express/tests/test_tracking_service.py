import uuid

import pytest

from apps.best_express.application.services import tracking_service
from apps.best_express.domain.status_map import map_best_status
from apps.best_express.models import BestShipmentStatus
from apps.best_express.models import BestShipment

from ._factories import make_account


class FakeClient:
    def __init__(self):
        self.trace_calls = []
        self.print_calls = []

    def trace_query(self, codes, lang="en-US"):
        self.trace_calls.append(codes)
        return {
            "Result": 1,
            "data": [
                {
                    "merchant_order_code": f"DH-for-{code}",
                    "code_best": code,
                    "traceLogs": [
                        {"status": "202", "modified_at": 1700000001000},
                        {"status": "666", "modified_at": 1700000002000},
                    ],
                }
                for code in codes
            ],
        }

    def estimate_fee(self, payload):
        return {"Result": 1, "Message": "", "data": {"TotalFee": 19800.0}}

    def print_waybill(self, codes):
        self.print_calls.append(codes)
        return {"Result": 1, "Url": f"http://pdf/{len(codes)}.pdf", "Guid": "g"}


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(
        tracking_service, "BestExpressClient", lambda account: client
    )
    return client


@pytest.mark.django_db
def test_trace_chunks_by_10(fake_client):
    account = make_account()
    codes = [f"848{i:05d}" for i in range(23)]
    results = tracking_service.trace(account, codes)
    assert [len(c) for c in fake_client.trace_calls] == [10, 10, 3]
    assert len(results) == 23


@pytest.mark.django_db
def test_trace_syncs_shipment_status(fake_client):
    account = make_account()
    code = f"848{uuid.uuid4().hex[:10]}"
    shipment = BestShipment.objects.create(
        account=account, merchant_order_code=f"DH-{uuid.uuid4()}", code_best=code
    )
    tracking_service.trace(account, [code])
    shipment.refresh_from_db()
    assert shipment.status == BestShipmentStatus.DELIVERED
    assert shipment.best_status_code == "666"


@pytest.mark.django_db
def test_trace_log_without_modified_at_cannot_downgrade(monkeypatch):
    account = make_account()
    code = f"848{uuid.uuid4().hex[:10]}"
    shipment = BestShipment.objects.create(
        account=account, merchant_order_code=f"DH-{uuid.uuid4()}", code_best=code
    )

    class FlakyTraceClient:
        def __init__(self):
            self.calls = 0

        def trace_query(self, codes, lang="en-US"):
            self.calls += 1
            if self.calls == 1:
                trace_logs = [{"status": "666", "modified_at": 1700000002000}]
            else:
                trace_logs = [{"status": "601"}]
            return {
                "Result": 1,
                "data": [
                    {
                        "merchant_order_code": f"DH-for-{c}",
                        "code_best": c,
                        "traceLogs": trace_logs,
                    }
                    for c in codes
                ],
            }

    client = FlakyTraceClient()
    monkeypatch.setattr(tracking_service, "BestExpressClient", lambda account: client)

    tracking_service.trace(account, [code])
    tracking_service.trace(account, [code])

    shipment.refresh_from_db()
    assert shipment.status == BestShipmentStatus.DELIVERED


@pytest.mark.django_db
def test_trace_sync_idempotent(fake_client):
    account = make_account()
    code = f"848{uuid.uuid4().hex[:10]}"
    BestShipment.objects.create(
        account=account, merchant_order_code=f"DH-{uuid.uuid4()}", code_best=code
    )
    tracking_service.trace(account, [code])
    tracking_service.trace(account, [code])  # chạy lại — không lỗi, không đổi
    from apps.best_express.models import BestWebhookEvent

    assert BestWebhookEvent.objects.filter(code_best=code).count() == 2


@pytest.mark.django_db
def test_estimate_fee_returns_data(fake_client):
    account = make_account()
    data = tracking_service.estimate_fee(account, {"Weight": 100})
    assert data["TotalFee"] == 19800.0


@pytest.mark.django_db
def test_print_waybills_chunks_by_50(fake_client):
    account = make_account()
    codes = [f"848{i:05d}" for i in range(120)]
    urls = tracking_service.print_waybills(account, codes)
    assert [len(c) for c in fake_client.print_calls] == [50, 50, 20]
    assert len(urls) == 3


# --- map_best_status: bảng mã trạng thái Best mà tracking_service tiêu thụ ---


@pytest.mark.parametrize(
    "code,expected",
    [
        ("211", BestShipmentStatus.PICKING_UP),
        ("203", BestShipmentStatus.PICKUP_FAILED),
        ("202", BestShipmentStatus.PICKED_UP),
        ("301", BestShipmentStatus.IN_TRANSIT),
        ("302", BestShipmentStatus.IN_TRANSIT),
        ("303", BestShipmentStatus.IN_TRANSIT),
        ("304", BestShipmentStatus.IN_TRANSIT),
        ("309", BestShipmentStatus.IN_TRANSIT),
        ("601", BestShipmentStatus.OUT_FOR_DELIVERY),
        ("604", BestShipmentStatus.DELIVERY_FAILED),
        ("666", BestShipmentStatus.DELIVERED),
        ("605", BestShipmentStatus.RETURNING),
        ("701", BestShipmentStatus.RETURNING),
        ("702", BestShipmentStatus.RETURNING),
        ("703", BestShipmentStatus.RETURNING),
        ("704", BestShipmentStatus.RETURNING),
        ("705", BestShipmentStatus.RETURNING),
        ("707", BestShipmentStatus.RETURN_FAILED),
        ("708", BestShipmentStatus.RETURNED),
        ("777", BestShipmentStatus.LOST),
        ("1000", BestShipmentStatus.DAMAGED),
        ("2", BestShipmentStatus.CANCELLED),
        ("customer Cancel", BestShipmentStatus.CANCELLED),
        ("CUSTOMER CANCEL", BestShipmentStatus.CANCELLED),
    ],
)
def test_map_known_codes(code, expected):
    assert map_best_status(code) == expected


def test_unknown_code_returns_none():
    assert map_best_status("999") is None
    assert map_best_status("") is None
    assert map_best_status(None) is None


def test_int_code_accepted():
    assert map_best_status(666) == BestShipmentStatus.DELIVERED
