import uuid
from decimal import Decimal

import pytest

from apps.best_express.application.services import webhook_service
from apps.best_express.application.services.webhook_service import store_event
from apps.best_express.contracts.tracking_events import BestTrackingUpdated
from apps.best_express.models import BestShipmentStatus
from apps.best_express.models import BestExpressAccount, BestShipment
from apps.common.events import event_bus

from ._factories import make_account


def make_shipment(account, **kwargs) -> BestShipment:
    defaults = {
        "merchant_order_code": f"DH-{uuid.uuid4()}",
        "code_best": f"848{uuid.uuid4().hex[:10]}",
    }
    defaults.update(kwargs)
    return BestShipment.objects.create(account=account, **defaults)


def webhook_payload(shipment, status="666", modified_at=1700000000000, **extra):
    payload = {
        "code_best": shipment.code_best,
        "merchant_order_code": shipment.merchant_order_code,
        "status": status,
        "status_content": "ok",
        "cod": 180000.0,
        "product_price": 180000.0,
        "freight_fee": 33880.0,
        "cod_fee": 0.0,
        "insurance_fee": 0.0,
        "vat_fee": 0.0,
        "return_fee": 0.0,
        "discount": 0.0,
        "total_fee": 33880.0,
        "weight": 600,
        "length": 140,
        "width": 200,
        "height": 150,
        "modified_at": modified_at,
    }
    payload.update(extra)
    return payload


@pytest.mark.django_db
def test_store_event_then_duplicate_returns_none():
    account = make_account()
    shipment = make_shipment(account)
    payload = webhook_payload(shipment)
    event = webhook_service.store_event(account, payload)
    assert event is not None
    assert webhook_service.store_event(account, payload) is None


@pytest.mark.django_db
def test_apply_event_updates_shipment():
    account = make_account()
    shipment = make_shipment(account)
    event = webhook_service.store_event(account, webhook_payload(shipment))
    assert webhook_service.apply_event(event) is True
    shipment.refresh_from_db()
    assert shipment.status == BestShipmentStatus.DELIVERED
    assert shipment.best_status_code == "666"
    assert shipment.freight_fee == Decimal("33880")
    assert shipment.total_fee == Decimal("33880")
    assert shipment.weight == 600
    assert shipment.last_event_at is not None
    event.refresh_from_db()
    assert event.processed is True


@pytest.mark.django_db
def test_apply_out_of_order_event_does_not_downgrade():
    account = make_account()
    shipment = make_shipment(account)
    newer = webhook_service.store_event(
        account, webhook_payload(shipment, status="666", modified_at=1700000002000)
    )
    webhook_service.apply_event(newer)
    older = webhook_service.store_event(
        account, webhook_payload(shipment, status="601", modified_at=1700000001000)
    )
    assert webhook_service.apply_event(older) is False
    shipment.refresh_from_db()
    assert shipment.status == BestShipmentStatus.DELIVERED
    older.refresh_from_db()
    assert older.processed is True
    assert "stale" in older.processing_error


@pytest.mark.django_db
def test_event_without_modified_at_cannot_downgrade():
    account = make_account()
    shipment = make_shipment(account)
    newer = webhook_service.store_event(
        account, webhook_payload(shipment, status="666", modified_at=1700000002000)
    )
    webhook_service.apply_event(newer)
    payload = webhook_payload(shipment, status="601")
    del payload["modified_at"]
    no_ts = webhook_service.store_event(account, payload)
    assert webhook_service.apply_event(no_ts) is False
    shipment.refresh_from_db()
    assert shipment.status == BestShipmentStatus.DELIVERED
    no_ts.refresh_from_db()
    assert no_ts.processed is True
    assert "stale" in no_ts.processing_error


@pytest.mark.django_db
def test_apply_unmatched_event_kept_for_reconciliation():
    account = make_account()
    event = webhook_service.store_event(
        account,
        {"code_best": "848unknown", "status": "666", "modified_at": 1700000000000},
    )
    assert webhook_service.apply_event(event) is False
    event.refresh_from_db()
    assert event.processed is False
    assert "unmatched" in event.processing_error


@pytest.mark.django_db
def test_apply_matches_by_merchant_code_and_backfills_code_best():
    account = make_account()
    shipment = make_shipment(account, code_best=None)
    payload = webhook_payload(shipment)
    payload["code_best"] = "84855000000009"
    event = webhook_service.store_event(account, payload)
    assert webhook_service.apply_event(event) is True
    shipment.refresh_from_db()
    assert shipment.code_best == "84855000000009"


@pytest.mark.django_db
def test_identifierless_events_are_skipped():
    """Event không có code_best lẫn merchant_order_code không khớp partial unique
    nào → mỗi retry của Best sẽ nhân bản dòng. Short-circuit: không lưu."""
    from apps.best_express.models import BestWebhookEvent

    account = make_account()
    first = webhook_service.store_event(account, {"status": "666"})
    second = webhook_service.store_event(account, {"status": "666"})
    assert first is None
    assert second is None
    assert BestWebhookEvent.objects.count() == 0


@pytest.mark.django_db
def test_unknown_status_code_keeps_internal_status():
    account = make_account()
    shipment = make_shipment(account)
    event = webhook_service.store_event(
        account, webhook_payload(shipment, status="999")
    )
    assert webhook_service.apply_event(event) is True
    shipment.refresh_from_db()
    assert shipment.status == BestShipmentStatus.CREATED
    assert shipment.best_status_code == "999"


# --- store_event publish BestTrackingUpdated lên event bus ---


@pytest.fixture
def captured():
    """Bắt event trên bus.

    KHÔNG gọi `event_bus.clear()` khi teardown: bus là singleton toàn process,
    clear sẽ xoá luôn subscriber production của các app khác và làm test khác
    fail theo thứ tự chạy. Handler thừa chỉ append vào list không ai đọc.
    """
    received: list[BestTrackingUpdated] = []
    event_bus.subscribe(BestTrackingUpdated.event_type, received.append)
    return received


def _account():
    return BestExpressAccount.objects.create(
        name="acc", environment="production", username="u", password="p", is_active=True
    )


def _payload(status="601", modified_at=1700000000000):
    return {
        "code_best": "84859706027573",
        "merchant_order_code": "BHJ6N-5DTZ",
        "status": status,
        "modified_at": modified_at,
    }


@pytest.mark.django_db
def test_store_event_publishes_tracking_event(django_capture_on_commit_callbacks, captured):
    account = _account()

    # publish_on_commit chỉ chạy khi transaction thật sự commit. Test django_db mặc định
    # bọc mỗi test trong 1 transaction rollback ở cuối nên on_commit không tự kích hoạt —
    # dùng fixture của pytest-django để giả lập commit, giống cách
    # apps/common/tests/test_events.py::PublishOnCommitTests dùng captureOnCommitCallbacks.
    with django_capture_on_commit_callbacks(execute=True):
        assert store_event(account, _payload()) is not None
        # Finding #7 (review 2026-07-22): nếu implementation lỡ dùng
        # `event_bus.publish(...)` thẳng (không phải `publish_on_commit`), event
        # sẽ rơi vào `captured` NGAY TẠI ĐÂY — trong lúc transaction test còn mở,
        # chưa "commit" giả lập. Assert rỗng ở đây mới thật sự chứng minh việc
        # publish bị hoãn tới on_commit, chứ không phải publish ngay.
        assert captured == []

    assert len(captured) == 1
    event = captured[0]
    assert event.code_best == "84859706027573"
    assert event.merchant_order_code == "BHJ6N-5DTZ"
    assert event.best_status_code == "601"
    assert event.modified_at_ms == 1700000000000


@pytest.mark.django_db
def test_duplicate_event_does_not_publish(django_capture_on_commit_callbacks, captured):
    account = _account()

    with django_capture_on_commit_callbacks(execute=True):
        assert store_event(account, _payload()) is not None
        assert captured == []  # chưa commit — chưa publish (finding #7)
        # Cùng (code_best, status, modified_at) → unique constraint chặn, không publish lại.
        assert store_event(account, _payload()) is None

    assert len(captured) == 1
