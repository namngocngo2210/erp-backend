"""Tracking bảo hành: map mã Best → nhóm VC, subscriber BestTrackingUpdated, webhook E2E.

E2E: webhook Best → badge hành trình bảo hành đổi ngay, không đợi cron.

Đặt ở `apps/warranty/tests/`, KHÔNG phải `apps/best_express/tests/` (finding #10,
review 2026-07-22): test này cố ý import `apps.warranty` — nếu để bên
`best_express` thì suite của `best_express` không còn tự chạy độc lập được
nữa, đi ngược đúng nguyên tắc cốt lõi "best_express độc lập hoàn toàn" mà
nhánh này dựng lên. Chiều phụ thuộc test ngược lại (warranty → best_express)
là chiều ĐÃ được cho phép — warranty vốn đã phụ thuộc `best_express.contracts`
ở code production.
"""

import uuid

import pytest
from rest_framework.test import APIClient

from apps.best_express.contracts.tracking_events import BestTrackingUpdated
from apps.best_express.models import BestExpressAccount
from apps.warranty.journey_service import WarrantyJourneyService
from apps.warranty.models import WarrantyClaim
from apps.warranty.subscribers import _on_best_tracking_updated
from apps.warranty.tracking_status import (
    BEST_VC_GROUP_LABELS,
    journey_vc_groups,
    map_best_vc_group,
)

# ---------------------------------------------------------------------------
# Map mã trạng thái Best → nhóm VC (từ test_tracking_status.py)
# ---------------------------------------------------------------------------


def test_map_best_vc_group_all_codes():
    expected = {
        "211": "picking_up",
        "203": "pickup_failed",
        "202": "in_transit", "301": "in_transit", "302": "in_transit",
        "303": "in_transit", "304": "in_transit", "309": "in_transit",
        "601": "delivering",
        "604": "redelivery",
        "666": "delivered",
        "605": "returning", "701": "returning", "702": "returning",
        "703": "returning", "704": "returning", "705": "returning",
        "707": "returning",
        "708": "returned",
        "777": "lost",
        "1000": "damaged",
    }
    for code, group in expected.items():
        assert map_best_vc_group(code) == group
    assert map_best_vc_group("999") is None  # mã lạ
    assert map_best_vc_group("") is None
    assert map_best_vc_group(None) is None
    assert map_best_vc_group(666) == "delivered"  # int cũng nhận


def test_group_labels_cover_11_groups():
    assert len(BEST_VC_GROUP_LABELS) == 11
    assert BEST_VC_GROUP_LABELS["created"] == "Tạo mới"
    assert BEST_VC_GROUP_LABELS["delivered"] == "GTC"


@pytest.mark.django_db
def test_journey_vc_groups_priority_and_fallback(order_factory):
    from apps.warranty.journey_service import WarrantyJourneyService

    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)

    # Không có mã nào → per-code None, main = created.
    groups = journey_vc_groups(j)
    assert groups == {"new": None, "recall": None, "return": None, "main": "created"}

    # Có mã gửi đi nhưng chưa có data Best → created.
    j.new_tracking_code = "B1"
    assert journey_vc_groups(j)["new"] == "created"
    assert journey_vc_groups(j)["main"] == "created"

    # Có data Best cho mã gửi đi.
    j.new_tracking_best_status = "601"
    assert journey_vc_groups(j)["new"] == "delivering"
    assert journey_vc_groups(j)["main"] == "delivering"

    # Ưu tiên: có mã thu hồi → main theo thu hồi.
    j.recall_code = "B2"
    j.recall_best_status = "301"
    assert journey_vc_groups(j)["main"] == "in_transit"

    # Ưu tiên cao nhất: mã gửi lại.
    j.return_tracking_code = "B3"
    j.return_tracking_best_status = "666"
    groups = journey_vc_groups(j)
    assert groups["return"] == "delivered"
    assert groups["main"] == "delivered"

    # Mã lạ đã lưu → coi như chưa có data (created), không nổ.
    j.return_tracking_best_status = "999"
    assert journey_vc_groups(j)["return"] == "created"


def test_should_apply_best_status_blocks_older_events():
    from apps.warranty.tracking_status import should_apply_best_status

    # Event mới hơn → ghi.
    assert should_apply_best_status(100, 200) is True
    # Cùng mốc → vẫn ghi (nội dung giống nhau, tránh kẹt).
    assert should_apply_best_status(200, 200) is True
    # Cũ hơn → chặn, nếu không "GTC" bị kéo ngược về "Đang trung chuyển".
    assert should_apply_best_status(200, 100) is False
    # Chưa có mốc nào (mặc định 0) → luôn ghi.
    assert should_apply_best_status(0, 0) is True
    assert should_apply_best_status(0, 50) is True


def test_journey_code_fields_cover_three_legs():
    from apps.warranty.tracking_status import JOURNEY_CODE_FIELDS

    assert JOURNEY_CODE_FIELDS == (
        ("new_tracking_code", "new_tracking_best_status", "new_tracking_best_status_ms"),
        ("recall_code", "recall_best_status", "recall_best_status_ms"),
        (
            "return_tracking_code",
            "return_tracking_best_status",
            "return_tracking_best_status_ms",
        ),
    )


# ---------------------------------------------------------------------------
# Subscriber BestTrackingUpdated (từ test_tracking_subscriber.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def journey(order_factory):
    claim = WarrantyClaim.objects.create(order=order_factory())
    j = WarrantyJourneyService.add_journey(claim)
    j.new_tracking_code = "B-NEW"
    j.recall_code = "B-RC"
    j.return_tracking_code = "B-RT"
    j.save()
    return j


@pytest.mark.django_db
def test_updates_leg_matched_by_code_best(journey):
    _on_best_tracking_updated(
        BestTrackingUpdated(code_best="B-RC", best_status_code="708", modified_at_ms=500)
    )

    journey.refresh_from_db()
    assert journey.recall_best_status == "708"
    assert journey.recall_best_status_ms == 500
    assert journey.tracking_synced_at is not None
    # Chặng khác không bị đụng.
    assert journey.new_tracking_best_status == ""
    assert journey.return_tracking_best_status == ""


@pytest.mark.django_db
def test_matches_by_merchant_order_code_when_code_best_empty(journey):
    _on_best_tracking_updated(
        BestTrackingUpdated(
            code_best="", merchant_order_code="B-NEW", best_status_code="601", modified_at_ms=10
        )
    )

    journey.refresh_from_db()
    assert journey.new_tracking_best_status == "601"


@pytest.mark.django_db
def test_older_event_does_not_overwrite(journey):
    _on_best_tracking_updated(
        BestTrackingUpdated(code_best="B-RT", best_status_code="666", modified_at_ms=900)
    )
    # Event tới muộn nhưng mốc CŨ hơn — không được kéo "GTC" về "đang trung chuyển".
    _on_best_tracking_updated(
        BestTrackingUpdated(code_best="B-RT", best_status_code="302", modified_at_ms=100)
    )

    journey.refresh_from_db()
    assert journey.return_tracking_best_status == "666"
    assert journey.return_tracking_best_status_ms == 900


@pytest.mark.django_db
def test_unknown_code_is_noop(journey):
    _on_best_tracking_updated(
        BestTrackingUpdated(code_best="KHONG-KHOP", best_status_code="601", modified_at_ms=1)
    )

    journey.refresh_from_db()
    assert journey.new_tracking_best_status == ""
    assert journey.tracking_synced_at is None


@pytest.mark.django_db
def test_event_without_code_or_status_is_noop(journey):
    _on_best_tracking_updated(BestTrackingUpdated(best_status_code="601", modified_at_ms=1))
    _on_best_tracking_updated(BestTrackingUpdated(code_best="B-NEW", best_status_code=""))

    journey.refresh_from_db()
    assert journey.new_tracking_best_status == ""


@pytest.mark.django_db
def test_register_is_idempotent_and_wired_to_bus(journey):
    from apps.common.events import event_bus
    from apps.warranty import subscribers

    subscribers.register()
    subscribers.register()

    # Finding #6 (review 2026-07-22): assertion cũ chỉ so ms=7 với ms=7 (dùng
    # `>=` nên ghi lại y giá trị cũ vẫn "đúng") — XANH cả khi `register()` bị
    # gỡ mất kiểm tra `is_subscribed` và đăng ký trùng handler. Đếm thẳng số
    # handler trên bus mới thật sự chứng minh idempotent.
    handlers = event_bus._handlers.get(BestTrackingUpdated.event_type, [])
    assert handlers.count(subscribers._on_best_tracking_updated) == 1

    event_bus.publish(
        BestTrackingUpdated(code_best="B-NEW", best_status_code="601", modified_at_ms=7)
    )

    journey.refresh_from_db()
    assert journey.new_tracking_best_status == "601"
    assert journey.new_tracking_best_status_ms == 7


@pytest.mark.django_db
def test_unmappable_status_code_is_ignored(journey):
    """Finding #4: mã lạ (kể cả "2" hủy của Best, không có trong bảng nhóm
    hiển thị BH) phải bị bỏ qua HOÀN TOÀN — không ghi status lẫn ms — để không
    kéo badge "GTC" lùi về "Tạo mới".
    """
    journey.return_tracking_best_status = "666"  # GTC
    journey.return_tracking_best_status_ms = 900
    journey.save()

    _on_best_tracking_updated(
        BestTrackingUpdated(code_best="B-RT", best_status_code="2", modified_at_ms=2000)
    )

    journey.refresh_from_db()
    assert journey.return_tracking_best_status == "666"
    assert journey.return_tracking_best_status_ms == 900
    assert journey.tracking_synced_at is None


@pytest.mark.django_db
def test_concurrent_events_do_not_lose_the_newer_write(journey, monkeypatch):
    """Finding #2: hai worker nhận 2 event của cùng mã gần như đồng thời — bản
    vá dùng UPDATE có điều kiện ở tầng DB (`ms_field__lte`), trọng tài THẬT là
    giá trị ĐANG CÓ trong DB tại thời điểm ghi, không phải bản đọc trong vòng
    lặp Python. Mô phỏng: ép guard sớm (chỉ là optimization, có thể dựa trên
    ảnh chụp cũ) luôn trả True — như worker B đọc phải bản snapshot ms=0 trước
    khi worker A kịp commit — rồi cho "worker A" ghi mốc mới hơn thẳng vào DB
    trước khi event cũ của "worker B" tới. Nếu code lỡ quay lại kiểu
    read-then-write, mốc mới sẽ bị đè ngược thành mốc cũ hơn.
    """
    from apps.warranty.models import WarrantyJourney

    monkeypatch.setattr(
        "apps.warranty.tracking_status.should_apply_best_status", lambda *_: True
    )
    # "Worker A" đã commit trạng thái mới hơn thẳng vào DB.
    WarrantyJourney.objects.filter(pk=journey.pk).update(
        return_tracking_best_status="666", return_tracking_best_status_ms=900
    )

    # "Worker B" xử lý event CŨ hơn (ms=100) — guard sớm bị ép nói "được ghi".
    _on_best_tracking_updated(
        BestTrackingUpdated(code_best="B-RT", best_status_code="302", modified_at_ms=100)
    )

    journey.refresh_from_db()
    # Trọng tài DB-level (`ms_field__lte`) phải giữ nguyên ghi của "worker A".
    assert journey.return_tracking_best_status == "666"
    assert journey.return_tracking_best_status_ms == 900


# ---------------------------------------------------------------------------
# E2E webhook Best → hành trình bảo hành (từ test_best_webhook_realtime.py)
# ---------------------------------------------------------------------------

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
def test_webhook_updates_warranty_journey(django_capture_on_commit_callbacks, order_factory):
    """Webhook Best về → badge hành trình bảo hành đổi ngay, không đợi cron.

    Subscriber production đã được đăng ký lại trước mỗi test warranty bởi
    fixture autouse `_ensure_warranty_subscribers` (xem conftest.py) — không
    cần gọi `subscribers.register()` tay ở đây.
    """
    make_account()
    code = f"848{uuid.uuid4().hex[:10]}"
    claim = WarrantyClaim.objects.create(order=order_factory())
    journey = WarrantyJourneyService.add_journey(claim)
    journey.new_tracking_code = code
    journey.save()

    # publish_on_commit chỉ chạy khi transaction commit; trong test bọc transaction
    # thì phải bắt callback và chạy tay.
    with django_capture_on_commit_callbacks(execute=True):
        response = auth_client().post(
            WEBHOOK_URL,
            {
                "code_best": code,
                "merchant_order_code": "",
                "status": "666",
                "modified_at": 1700000000000,
            },
            format="json",
        )

    assert response.status_code == 200
    assert response.json()["Result"] == 1

    journey.refresh_from_db()
    assert journey.new_tracking_best_status == "666"
    assert journey.new_tracking_best_status_ms == 1700000000000
    assert journey_vc_groups(journey)["new"] == "delivered"
