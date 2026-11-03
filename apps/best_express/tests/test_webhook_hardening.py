"""Hardening webhook BEST (audit 2026-07-14):

- dedupe cho event chưa có `code_best` (chỉ có mã đơn của mình);
- `apply_event` chỉ ghi shipment THUỘC account đã xác thực bằng token;
- retry event chưa xử lý được + purge event cũ.
"""
from __future__ import annotations

import uuid

import pytest
from django.utils import timezone

from apps.best_express import tasks as best_tasks
from apps.best_express.application.services import webhook_service
from apps.best_express.models import BestExpressAccount, BestShipment, BestWebhookEvent

from .test_webhook_service import make_account, make_shipment, webhook_payload


@pytest.mark.django_db
def test_store_event_dedupes_when_code_best_missing():
    """Giai đoạn Best chưa cấp code_best: retry của Best / mỗi lần trace() re-store
    KHÔNG được nhân bản event."""
    account = make_account()
    shipment = make_shipment(account)
    payload = webhook_payload(shipment, code_best="")
    payload["code_best"] = ""

    first = webhook_service.store_event(account, payload)
    second = webhook_service.store_event(account, payload)

    assert first is not None
    assert second is None, "event trùng (theo merchant_order_code) phải bị chặn"
    assert BestWebhookEvent.objects.count() == 1


@pytest.mark.django_db
def test_store_event_dedupes_when_modified_at_missing():
    """Event thiếu `modified_at` (ts=0) vẫn phải dedupe: Best retry + mỗi lần trace()
    re-store traceLogs sẽ nhân bản dòng y hệt nếu constraint bỏ qua ts=0."""
    account = make_account()
    shipment = make_shipment(account)
    payload = webhook_payload(shipment)
    payload.pop("modified_at")

    first = webhook_service.store_event(account, payload)
    second = webhook_service.store_event(account, payload)

    assert first is not None
    assert first.modified_at_ms == 0
    assert second is None, "event ts=0 trùng phải bị chặn"
    assert BestWebhookEvent.objects.count() == 1


@pytest.mark.django_db
def test_retry_abandons_events_that_never_match(settings):
    """Event chết (mã lạ) không được chiếm slot retry vĩnh viễn — quá hạn thì bỏ cuộc,
    đánh dấu processed để purge dọn được, nhường chỗ cho event mới."""
    settings.BEST_WEBHOOK_EVENT_RETRY_MAX_AGE_DAYS = 3
    account = make_account()
    dead = webhook_service.store_event(
        account,
        {
            "code_best": "MA-LA-404",
            "merchant_order_code": "DH-KHONG-CO",
            "status": "666",
            "modified_at": 1700000000000,
        },
    )
    webhook_service.apply_event(dead)  # unmatched → processed=False
    BestWebhookEvent.objects.filter(pk=dead.pk).update(
        received_at=timezone.now() - timezone.timedelta(days=5)
    )

    result = best_tasks.retry_unprocessed_best_webhook_events()

    assert result["abandoned"] == 1
    assert result["pending"] == 0
    dead.refresh_from_db()
    assert dead.processed is True
    assert "abandoned" in dead.processing_error


@pytest.mark.django_db
def test_retry_does_not_abandon_events_that_were_never_attempted(settings):
    """Hệ thống chết vài ngày (worker treo / DB cạn connection) → event dồn lại CHƯA từng
    được thử. Vòng cron đầu tiên khi sống lại KHÔNG được đánh dấu chúng là bỏ cuộc: trong
    đó có Giao thành công / COD / Chuyển hoàn, mà Best không đẩy lại (audit 14/07 vòng 3).
    """
    settings.BEST_WEBHOOK_EVENT_RETRY_MAX_AGE_DAYS = 3
    account = make_account()
    shipment = make_shipment(account)
    event = webhook_service.store_event(account, webhook_payload(shipment))
    # Chưa từng apply → processing_error rỗng; nằm quá hạn vì hệ thống chết.
    BestWebhookEvent.objects.filter(pk=event.pk).update(
        processed=False,
        processing_error="",
        received_at=timezone.now() - timezone.timedelta(days=5),
    )

    result = best_tasks.retry_unprocessed_best_webhook_events()

    assert result["abandoned"] == 0, "event chưa thử lần nào không được bỏ cuộc"
    assert result["applied"] == 1, "phải được áp lại chứ không phải vứt đi"
    event.refresh_from_db()
    assert "abandoned" not in event.processing_error


@pytest.mark.django_db
def test_retry_applies_before_abandoning(settings):
    """Bỏ cuộc chạy SAU vòng áp: event unmatched quá hạn nhưng shipment vừa xuất hiện thì
    vẫn phải được áp trong chính vòng cron này, không bị vứt trước khi kịp thử."""
    settings.BEST_WEBHOOK_EVENT_RETRY_MAX_AGE_DAYS = 3
    account = make_account()
    order_code = f"DH-{uuid.uuid4()}"
    code_best = f"848{uuid.uuid4().hex[:10]}"
    event = webhook_service.store_event(
        account,
        {
            "code_best": code_best,
            "merchant_order_code": order_code,
            "status": "70",
            "modified_at": 1700000000000,
        },
    )
    webhook_service.apply_event(event)  # unmatched (shipment chưa có)
    BestWebhookEvent.objects.filter(pk=event.pk).update(
        received_at=timezone.now() - timezone.timedelta(days=5)
    )
    # Shipment được tạo muộn (Best trả code sau) — event cũ giờ khớp được.
    make_shipment(account, merchant_order_code=order_code, code_best=code_best)

    result = best_tasks.retry_unprocessed_best_webhook_events()

    assert result["applied"] == 1
    assert result["abandoned"] == 0
    event.refresh_from_db()
    assert event.processed is True
    assert event.processing_error == ""


@pytest.mark.django_db
def test_retry_records_error_and_abandons_persistent_exception(settings, monkeypatch):
    """Event ném exception BỀN VỮNG không được bất tử: lần lỗi ĐẦU ghi vết + được miễn
    abandon (còn một vòng thử nữa); lỗi TÁI DIỄN quá tuổi thì bỏ cuộc. Nếu miễn theo
    'lỗi trong vòng này' thì event deterministic được miễn MỖI vòng — bất tử trở lại
    (audit 2026-07-14 vòng 5)."""
    settings.BEST_WEBHOOK_EVENT_RETRY_MAX_AGE_DAYS = 3
    account = make_account()
    shipment = make_shipment(account)
    event = webhook_service.store_event(account, webhook_payload(shipment))
    BestWebhookEvent.objects.filter(pk=event.pk).update(
        received_at=timezone.now() - timezone.timedelta(days=5)
    )

    def boom(_event):
        raise RuntimeError("persistent failure")

    monkeypatch.setattr(best_tasks, "apply_event", boom, raising=False)
    import apps.best_express.application.services.webhook_service as ws

    monkeypatch.setattr(ws, "apply_event", boom)

    # Vòng 1: lỗi lần đầu → ghi "error:", CHƯA bỏ cuộc.
    result1 = best_tasks.retry_unprocessed_best_webhook_events()
    event.refresh_from_db()
    assert result1["abandoned"] == 0
    assert event.processed is False
    assert event.processing_error.startswith("error:")

    # Vòng 2: vẫn lỗi + quá tuổi → bỏ cuộc, nhả slot.
    result2 = best_tasks.retry_unprocessed_best_webhook_events()
    event.refresh_from_db()
    assert result2["abandoned"] == 1
    assert event.processed is True
    assert "abandoned" in event.processing_error


@pytest.mark.django_db
def test_apply_event_does_not_touch_other_accounts_shipment():
    """Token account A không được ghi đè trạng thái/phí shipment của account B."""
    account_a = make_account()
    account_b = BestExpressAccount.objects.create(
        name="B", username=f"u-{uuid.uuid4()}", password="pw"
    )
    shipment_b = make_shipment(account_b)

    # Event mang mã vận đơn của B nhưng được xác thực bằng token của A.
    event = webhook_service.store_event(account_a, webhook_payload(shipment_b))
    assert event is not None

    applied = webhook_service.apply_event(event)

    assert applied is False, "không được áp event sang shipment của account khác"
    event.refresh_from_db()
    assert event.processed is False
    shipment_b.refresh_from_db()
    assert shipment_b.best_status_code == "", "shipment của B phải giữ nguyên"


@pytest.mark.django_db
def test_apply_event_still_works_within_same_account():
    account = make_account()
    shipment = make_shipment(account)
    event = webhook_service.store_event(account, webhook_payload(shipment))

    assert webhook_service.apply_event(event) is True
    shipment.refresh_from_db()
    assert shipment.best_status_code == "666"


@pytest.mark.django_db
def test_retry_task_applies_events_that_arrived_before_shipment():
    """Event tới TRƯỚC khi shipment tồn tại (unmatched) phải được áp lại khi cron chạy —
    trước đây nằm chết vì Best đã nhận Result 1 và không retry."""
    account = make_account()
    order_code = f"DH-{uuid.uuid4()}"
    code_best = f"848{uuid.uuid4().hex[:10]}"
    event = webhook_service.store_event(
        account,
        {
            "code_best": code_best,
            "merchant_order_code": order_code,
            "status": "666",
            "modified_at": 1700000000000,
        },
    )
    assert webhook_service.apply_event(event) is False  # chưa có shipment
    event.refresh_from_db()
    assert event.processed is False

    shipment = BestShipment.objects.create(
        account=account, merchant_order_code=order_code, code_best=code_best
    )

    result = best_tasks.retry_unprocessed_best_webhook_events()

    assert result["applied"] == 1
    assert result["pending"] == 0
    shipment.refresh_from_db()
    assert shipment.best_status_code == "666"


@pytest.mark.django_db
def test_purge_removes_processed_events_but_keeps_unprocessed(settings):
    settings.BEST_WEBHOOK_EVENT_RETENTION_DAYS = 30
    account = make_account()
    shipment = make_shipment(account)
    old = timezone.now() - timezone.timedelta(days=60)

    processed = webhook_service.store_event(account, webhook_payload(shipment))
    webhook_service.apply_event(processed)
    # Event của một vận đơn CHƯA có trong DB (cả 2 mã đều lạ) → unmatched.
    unprocessed = webhook_service.store_event(
        account,
        {
            "code_best": "ZZZ-404",
            "merchant_order_code": "DH-KHONG-TON-TAI",
            "status": "666",
            "modified_at": 1700000001000,
        },
    )
    webhook_service.apply_event(unprocessed)  # unmatched → processed=False
    BestWebhookEvent.objects.update(received_at=old)

    deleted = best_tasks.purge_old_best_webhook_events()

    assert deleted == 1
    assert not BestWebhookEvent.objects.filter(pk=processed.pk).exists()
    assert BestWebhookEvent.objects.filter(pk=unprocessed.pk).exists(), (
        "event chưa áp được phải giữ lại để đối soát"
    )
