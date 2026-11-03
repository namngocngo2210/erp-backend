"""Endpoint nhận webhook CNV + vòng đời CnvWebhookEvent (spec 2026-08-19).

Điểm nhạy cảm: đây là URL công khai (`AllowAny`) mà ghi thẳng vào bảng `orders`.
Nhóm test đầu tiên vì thế là xác thực, không phải nghiệp vụ.
"""

import pytest
from rest_framework.test import APIClient

from apps.cnv_loyalty.application.services.webhook_service import apply_event, store_event
from apps.cnv_loyalty.models import CnvOrderLink, CnvWebhookEvent
from apps.cnv_loyalty.tests.test_inbound_service import cnv_order

WEBHOOK_URL = "/api/cnv/webhook"
TOKEN = "cnv-hook-secret"

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _inbound_on(settings):
    settings.CNV_INBOUND_ENABLED = True
    settings.CNV_WEBHOOK_VERIFY_TOKEN = TOKEN
    return settings


def auth_client(token=TOKEN, scheme="Token") -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"{scheme} {token}")
    return client


def payload(**overrides) -> dict:
    return {"orders": [cnv_order(**overrides)]}


# ── Xác thực ─────────────────────────────────────────────────────────────────


def test_thieu_hoac_sai_token_bi_401():
    assert APIClient().post(WEBHOOK_URL, payload(), format="json").status_code == 401
    assert auth_client("sai").post(WEBHOOK_URL, payload(), format="json").status_code == 401


def test_token_rong_trong_settings_tu_choi_tat_ca(settings):
    """Chưa cấu hình token = endpoint mở toang — phải đóng, không phải mở."""
    settings.CNV_WEBHOOK_VERIFY_TOKEN = ""
    assert auth_client("").post(WEBHOOK_URL, payload(), format="json").status_code == 401
    assert APIClient().post(WEBHOOK_URL, payload(), format="json").status_code == 401


@pytest.mark.parametrize("scheme", ["Token", "Bearer"])
def test_chap_nhan_ca_hai_scheme_header(scheme):
    """CNV không tài liệu hoá cách gửi token — nhận cả hai thay vì đoán một."""
    assert auth_client(scheme=scheme).post(WEBHOOK_URL, payload(), format="json").status_code == 200


def test_chap_nhan_token_o_header_rieng_va_trong_body():
    client = APIClient()
    assert (
        client.post(WEBHOOK_URL, payload(), format="json", HTTP_X_VERIFY_TOKEN=TOKEN).status_code
        == 200
    )
    body = payload(id=9002)
    body["verify_token"] = TOKEN
    assert client.post(WEBHOOK_URL, body, format="json").status_code == 200


def test_tat_co_inbound_tra_503_chu_khong_nuot_don(settings):
    """200 khi đang tắt = CNV coi như đã giao, đơn rơi vào hư không."""
    settings.CNV_INBOUND_ENABLED = False
    response = auth_client().post(WEBHOOK_URL, payload(), format="json")
    assert response.status_code == 503
    assert CnvWebhookEvent.objects.count() == 0


# ── Ingest qua webhook ───────────────────────────────────────────────────────


def test_webhook_hop_le_tao_don_va_luu_event():
    from apps.orders.models import Order, OrderSource

    response = auth_client().post(WEBHOOK_URL, payload(), format="json")

    assert response.status_code == 200
    event = CnvWebhookEvent.objects.get()
    assert event.processed is True and event.cnv_object_id == 9001
    order = Order.all_objects.get()
    assert order.order_source == OrderSource.CNV
    assert CnvOrderLink.objects.get().cnv_order_id == 9001


def test_cnv_gui_lai_y_het_khong_tao_event_thu_hai():
    """Tầng 4: hash payload chặn retry của CNV."""
    from apps.orders.models import Order

    client = auth_client()
    client.post(WEBHOOK_URL, payload(), format="json")
    client.post(WEBHOOK_URL, payload(), format="json")

    assert CnvWebhookEvent.objects.count() == 1
    assert Order.all_objects.count() == 1


def test_cap_nhat_that_su_van_vao_duoc():
    """Payload khác nội dung = event mới, không bị hash nuốt nhầm."""
    client = auth_client()
    client.post(WEBHOOK_URL, payload(), format="json")
    client.post(WEBHOOK_URL, payload(fulfillment_status="fulfilled"), format="json")

    assert CnvWebhookEvent.objects.count() == 2
    assert CnvOrderLink.objects.get().fulfillment_status == "fulfilled"


def test_don_minh_day_len_khong_quay_ve_thanh_don_mirror():
    """Yêu cầu gốc: webhook echo lại chính đơn CRM thì phải bị bỏ."""
    from apps.orders.models import Order

    response = auth_client().post(WEBHOOK_URL, payload(unique_id="desi-777"), format="json")

    assert response.status_code == 200
    assert Order.all_objects.count() == 0
    assert CnvWebhookEvent.objects.get().processed is True


def test_payload_khong_phai_json_object_bi_400():
    assert auth_client().post(WEBHOOK_URL, "abc", format="json").status_code == 400


# ── Event không nhận diện được ───────────────────────────────────────────────


def test_topic_la_khong_bi_nuot_im_lang():
    """Payload không chứa đơn: giữ processed=False + last_error để còn lần ra."""
    event = store_event({"customers": [{"id": 1}]}, topic="customers/update")

    assert apply_event(event) is False
    event.refresh_from_db()
    assert event.processed is False
    assert "chưa hỗ trợ" in event.last_error
    assert event.attempts == 1


def test_topic_lay_tu_header_khi_cnv_co_gui():
    auth_client().post(WEBHOOK_URL, payload(), format="json", HTTP_X_CNV_TOPIC="orders/create")

    assert CnvWebhookEvent.objects.get().topic == "orders/create"


def test_retry_task_ap_lai_event_chua_xu_ly():
    from apps.cnv_loyalty.tasks import retry_unprocessed_webhook_events
    from apps.orders.models import Order

    event = store_event(payload(), topic="orders/create")
    event.processed = False
    event.save(update_fields=["processed"])

    result = retry_unprocessed_webhook_events()

    assert result["applied"] == 1
    event.refresh_from_db()
    assert event.processed is True
    assert Order.all_objects.count() == 1


def test_purge_chi_xoa_event_da_xu_ly(settings):
    from datetime import timedelta

    from django.utils import timezone

    from apps.cnv_loyalty.tasks import purge_old_webhook_events

    settings.CNV_WEBHOOK_EVENT_RETENTION_DAYS = 1
    old = timezone.now() - timedelta(days=5)
    done = store_event(payload(id=1), topic="t1")
    pending = store_event(payload(id=2), topic="t2")
    done.processed = True
    done.save(update_fields=["processed"])
    CnvWebhookEvent.objects.update(received_at=old)

    result = purge_old_webhook_events()

    assert result["deleted"] == 1
    assert list(CnvWebhookEvent.objects.values_list("pk", flat=True)) == [pending.pk]
