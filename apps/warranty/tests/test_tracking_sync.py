import pytest

from apps.best_express.models import BestExpressAccount
from apps.warranty.journey_service import WarrantyJourneyService
from apps.warranty.models import WarrantyClaim, WarrantyClaimStatus, WarrantyJourney
from apps.warranty.tracking_sync_service import WarrantyTrackingSyncService


def _account():
    return BestExpressAccount.objects.create(
        name="acc", environment="production", username="u", password="p", is_active=True
    )


def _trace_result(code, status, modified_at=2):
    return {
        "code_best": code,
        "merchant_order_code": "",
        "traceLogs": [
            {"status": "211", "modified_at": 1},
            {"status": status, "modified_at": modified_at},  # mới nhất theo modified_at
        ],
    }


@pytest.mark.django_db
def test_sync_updates_matching_fields(order_factory, monkeypatch):
    _account()
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.new_tracking_code = "B-NEW"
    j.recall_code = "B-RC"
    j.save()

    def fake_trace(self, codes, lang="en-US"):
        data = []
        if "B-NEW" in codes:
            data.append(_trace_result("B-NEW", "601"))
        if "B-RC" in codes:
            data.append(_trace_result("B-RC", "708"))
        return {"data": data}

    monkeypatch.setattr(
        "apps.best_express.infrastructure.best_client.BestExpressClient.trace_query",
        fake_trace,
    )
    summary = WarrantyTrackingSyncService.sync()
    j.refresh_from_db()
    assert j.new_tracking_best_status == "601"
    assert j.recall_best_status == "708"
    assert j.tracking_synced_at is not None
    assert summary["updated"] == 1


@pytest.mark.django_db
def test_sync_skips_closed_claims(order_factory, monkeypatch):
    _account()
    claim = WarrantyClaim.objects.create(
        order=order_factory(), status=WarrantyClaimStatus.COMPLETED
    )
    j = WarrantyJourneyService.add_journey(claim)
    j.new_tracking_code = "B-DONE"
    j.save()
    called = {"n": 0}

    def fake_trace(self, codes, lang="en-US"):
        called["n"] += 1
        return {"data": []}

    monkeypatch.setattr(
        "apps.best_express.infrastructure.best_client.BestExpressClient.trace_query",
        fake_trace,
    )
    WarrantyTrackingSyncService.sync()
    assert called["n"] == 0  # không có mã nào cần trace


@pytest.mark.django_db
def test_sync_without_account_noop(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.new_tracking_code = "B-X"
    j.save()
    summary = WarrantyTrackingSyncService.sync()  # không có account — không nổ
    assert summary == {"codes": 0, "updated": 0, "skipped": "no_account"}


@pytest.mark.django_db
def test_sync_api_error_does_not_raise(order_factory, monkeypatch):
    from apps.best_express.exceptions import BestExpressError

    _account()
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.new_tracking_code = "B-ERR"
    j.save()

    def boom(self, codes, lang="en-US"):
        raise BestExpressError("Best chết")

    monkeypatch.setattr(
        "apps.best_express.infrastructure.best_client.BestExpressClient.trace_query", boom
    )
    summary = WarrantyTrackingSyncService.sync()  # log + bỏ qua, không raise
    assert summary["updated"] == 0


@pytest.mark.django_db
def test_sync_records_modified_at_ms(order_factory, monkeypatch):
    _account()
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.new_tracking_code = "B-NEW"
    j.save()

    def fake_trace(self, codes, lang="en-US"):
        return {"data": [_trace_result("B-NEW", "601")]}

    monkeypatch.setattr(
        "apps.best_express.infrastructure.best_client.BestExpressClient.trace_query",
        fake_trace,
    )
    WarrantyTrackingSyncService.sync()

    j.refresh_from_db()
    assert j.new_tracking_best_status == "601"
    # `_trace_result` đặt log mới nhất ở modified_at=2.
    assert j.new_tracking_best_status_ms == 2


@pytest.mark.django_db
def test_sync_does_not_overwrite_newer_webhook_status(order_factory, monkeypatch):
    _account()
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.new_tracking_code = "B-NEW"
    # Webhook đã ghi trạng thái mới hơn hẳn (mốc 999) — cron trace trả mốc 2.
    j.new_tracking_best_status = "666"
    j.new_tracking_best_status_ms = 999
    j.save()

    def fake_trace(self, codes, lang="en-US"):
        return {"data": [_trace_result("B-NEW", "302")]}

    monkeypatch.setattr(
        "apps.best_express.infrastructure.best_client.BestExpressClient.trace_query",
        fake_trace,
    )
    WarrantyTrackingSyncService.sync()

    j.refresh_from_db()
    assert j.new_tracking_best_status == "666"
    assert j.new_tracking_best_status_ms == 999


@pytest.mark.django_db
def test_sync_backfills_ms_even_when_status_unchanged(order_factory, monkeypatch):
    """Finding #5 (review 2026-07-22): guard cũ so status TRƯỚC ms, nên mọi
    row status ổn định nhưng `ms=0` (mọi row hiện có lúc deploy) sẽ KHÔNG BAO
    GIỜ được backfill `ms` — guard chống lạc hậu nằm chết vô thời hạn trên các
    row đó. Đẩy ms trước, so status sau.
    """
    _account()
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.new_tracking_code = "B-NEW"
    j.new_tracking_best_status = "601"  # status đã đúng từ trước
    j.new_tracking_best_status_ms = 0  # nhưng ms chưa từng được ghi (deploy cũ)
    j.save()

    def fake_trace(self, codes, lang="en-US"):
        # Trace trả CÙNG status "601" nhưng có mốc modified_at thật.
        return {"data": [_trace_result("B-NEW", "601", modified_at=1700000000000)]}

    monkeypatch.setattr(
        "apps.best_express.infrastructure.best_client.BestExpressClient.trace_query",
        fake_trace,
    )
    summary = WarrantyTrackingSyncService.sync()

    j.refresh_from_db()
    assert j.new_tracking_best_status == "601"
    assert j.new_tracking_best_status_ms == 1700000000000  # ms phải được backfill
    assert summary["updated"] == 1


@pytest.mark.django_db
def test_sync_unknown_status_code_kept_as_created_group(order_factory, monkeypatch):
    """Finding #4: mã lạ (kể cả "2" hủy) không có trong bảng nhóm hiển thị BH —
    cron phải bỏ qua (không ghi status/ms), giữ badge theo dữ liệu gần nhất đã
    biết, thay vì ghi mã thô khiến badge tụt về "Tạo mới".
    """
    _account()
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.new_tracking_code = "B-NEW"
    j.new_tracking_best_status = "666"  # GTC
    j.new_tracking_best_status_ms = 900
    j.save()

    def fake_trace(self, codes, lang="en-US"):
        return {"data": [_trace_result("B-NEW", "2", modified_at=2000)]}  # mã hủy lạ

    monkeypatch.setattr(
        "apps.best_express.infrastructure.best_client.BestExpressClient.trace_query",
        fake_trace,
    )
    summary = WarrantyTrackingSyncService.sync()

    j.refresh_from_db()
    assert j.new_tracking_best_status == "666"  # không bị kéo về "created"
    assert j.new_tracking_best_status_ms == 900
    assert summary["updated"] == 0


@pytest.mark.django_db
def test_sync_toctou_does_not_regress_against_concurrent_webhook(order_factory, monkeypatch):
    """Finding #1: cron `list()` toàn bộ journey vào bộ nhớ RỒI mới gọi mạng
    (chậm) sang Best. Mô phỏng ở đây: trong lúc `trace_query` "đang chạy",
    một webhook Best khác ghi thẳng vào DB một trạng thái MỚI HƠN. Guard so
    bằng ảnh chụp cũ (đọc trước network call) sẽ vẫn thấy ms=0 và cho ghi đè —
    kéo badge từ GTC lùi về "Đang trung chuyển" VÀ làm ms lùi lại theo. Bản vá
    dùng UPDATE có điều kiện ở tầng DB (so với giá trị ĐANG CÓ, không phải ảnh
    chụp) phải chặn được việc này.
    """
    _account()
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.new_tracking_code = "B-NEW"
    j.save()  # ms=0, status="" — đúng như mọi row trước khi có webhook

    def fake_trace(self, codes, lang="en-US"):
        # "Webhook" tới trong lúc cron đang chờ Best trả lời — ghi thẳng DB.
        WarrantyJourney.objects.filter(pk=j.pk).update(
            new_tracking_best_status="666", new_tracking_best_status_ms=1700000900000
        )
        # Best trả về trace CŨ hơn (mốc nhỏ hơn webhook vừa ghi).
        return {"data": [_trace_result("B-NEW", "302", modified_at=1700000100000)]}

    monkeypatch.setattr(
        "apps.best_express.infrastructure.best_client.BestExpressClient.trace_query",
        fake_trace,
    )
    WarrantyTrackingSyncService.sync()

    j.refresh_from_db()
    # Webhook thắng vì mốc mới hơn — cron KHÔNG được đè ngược badge lẫn ms.
    assert j.new_tracking_best_status == "666"
    assert j.new_tracking_best_status_ms == 1700000900000
