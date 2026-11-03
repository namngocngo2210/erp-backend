from unittest.mock import patch

import pytest
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.warranty.models import WarrantyClaim, WarrantyRegistration
from apps.warranty.views import WarrantyRegisterThrottle

# ---------------------------------------------------------------------------
# Ingest API token-based (từ test_warranty_ingest_api.py)
# ---------------------------------------------------------------------------

TOKEN = "ingest-secret"


@pytest.fixture
def ingest_client():
    return APIClient()


def _post(client, body, token=TOKEN):
    headers = {"HTTP_X_API_TOKEN": token} if token is not None else {}
    return client.post("/api/warranty/ingest", body, format="json", **headers)


@pytest.mark.django_db
@override_settings(ORDERS_INGEST_API_TOKEN=TOKEN, IS_PRODUCTION=False)
def test_success_creates_claim_and_first_journey(ingest_client, order_factory):
    order = order_factory(shipping_code="SHIP-1")
    res = _post(
        ingest_client,
        {
            "shipping_code": "SHIP-1",
            "send_reason": "Lỗi màn hình",
            "recall_code": "RC-1",
        },
    )
    assert res.status_code == 200, res.data
    assert res.data["order"]["id"] == order.id
    assert WarrantyClaim.objects.count() == 1
    assert len(res.data["journeys"]) == 1
    journey = res.data["journeys"][0]
    assert journey["seq"] == 1
    assert journey["send_reason"] == "Lỗi màn hình"
    assert journey["recall_code"] == "RC-1"


@pytest.mark.django_db
@override_settings(ORDERS_INGEST_API_TOKEN=TOKEN, IS_PRODUCTION=False)
def test_missing_shipping_code_400(ingest_client):
    res = _post(ingest_client, {"send_reason": "x"})
    assert res.status_code == 400


@pytest.mark.django_db
@override_settings(ORDERS_INGEST_API_TOKEN=TOKEN, IS_PRODUCTION=False)
def test_order_not_found_400(ingest_client):
    res = _post(ingest_client, {"shipping_code": "NONE"})
    assert res.status_code == 400


@pytest.mark.django_db
@override_settings(ORDERS_INGEST_API_TOKEN=TOKEN, IS_PRODUCTION=False)
def test_ambiguous_order_400(ingest_client, order_factory):
    order_factory(shipping_code="AMB")
    order_factory(shipping_code="AMB")
    res = _post(ingest_client, {"shipping_code": "AMB"})
    assert res.status_code == 400


@pytest.mark.django_db
@override_settings(ORDERS_INGEST_API_TOKEN=TOKEN, IS_PRODUCTION=False)
def test_duplicate_claim_409(ingest_client, order_factory):
    order = order_factory(shipping_code="DUP1")
    WarrantyClaim.objects.create(order=order)
    res = _post(ingest_client, {"shipping_code": "DUP1"})
    assert res.status_code == 409


@pytest.mark.django_db
@override_settings(ORDERS_INGEST_API_TOKEN=TOKEN, IS_PRODUCTION=False)
def test_invalid_token_401(ingest_client, order_factory):
    order_factory(shipping_code="SHIP-2")
    res = _post(ingest_client, {"shipping_code": "SHIP-2"}, token="wrong")
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# Form đăng ký bảo hành public (từ test_warranty_registration_validation.py)
# ---------------------------------------------------------------------------


class WarrantyRegistrationValidationTests(TestCase):
    """Form đăng ký bảo hành PUBLIC (AllowAny, throttle 20/h): validate phone + dedupe idempotent.

    Throttle 20/h đếm theo IP trong Redis THẬT dùng chung (cache.clear() bị cấm trong test)
    — không bypass thì chạy suite vài lần là cạn budget, cả class fail 429. Patch
    allow_request để test hermetic; KHÔNG nới rate production, KHÔNG đụng Redis.
    """

    def setUp(self):
        self.client = APIClient()
        throttle_bypass = patch.object(
            WarrantyRegisterThrottle, "allow_request", return_value=True
        )
        throttle_bypass.start()
        self.addCleanup(throttle_bypass.stop)

    def _payload(self, **overrides):
        data = dict(
            source="shopee",
            invoice_code="INV-001",
            full_name="Nguyen Van A",
            phone="0912345678",
            email="",
            note="",
        )
        data.update(overrides)
        return data

    def test_invalid_phone_rejected(self):
        res = self.client.post("/api/warranty/", self._payload(phone="abc"), format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(WarrantyRegistration.objects.count(), 0)

    def test_phone_missing_leading_zero_rejected(self):
        res = self.client.post("/api/warranty/", self._payload(phone="912345678"), format="json")
        self.assertEqual(res.status_code, 400)

    def test_valid_phone_accepted(self):
        res = self.client.post("/api/warranty/", self._payload(), format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(WarrantyRegistration.objects.count(), 1)

    def test_duplicate_submit_is_idempotent(self):
        payload = self._payload()
        res1 = self.client.post("/api/warranty/", payload, format="json")
        self.assertEqual(res1.status_code, 201, res1.data)

        # Second submit changes full_name/email — dedupe-hit must echo back what
        # THIS caller sent, never the stored row (AllowAny endpoint: anyone with
        # phone+invoice_code could otherwise read out the first submitter's PII).
        payload2 = self._payload(full_name="Someone Else", email="second@example.com")
        res2 = self.client.post("/api/warranty/", payload2, format="json")
        self.assertEqual(res2.status_code, 201, res2.data)

        self.assertEqual(WarrantyRegistration.objects.count(), 1)
        self.assertEqual(res1.data["id"], res2.data["id"])
        self.assertEqual(res2.data["full_name"], "Someone Else")
        self.assertEqual(res2.data["email"], "second@example.com")

    def test_duplicate_submit_never_echoes_stored_email(self):
        payload = self._payload(email="first@example.com")
        res1 = self.client.post("/api/warranty/", payload, format="json")
        self.assertEqual(res1.status_code, 201, res1.data)

        # Second submit omits email entirely — response must reflect THAT (empty),
        # not leak the stored record's email.
        payload2 = self._payload(email="")
        res2 = self.client.post("/api/warranty/", payload2, format="json")
        self.assertEqual(res2.status_code, 201, res2.data)

        self.assertEqual(WarrantyRegistration.objects.count(), 1)
        self.assertEqual(res2.data["id"], res1.data["id"])
        self.assertNotEqual(res2.data.get("email"), "first@example.com")
        self.assertEqual(res2.data.get("email"), "")

    def test_note_is_truncated(self):
        res = self.client.post(
            "/api/warranty/", self._payload(note="x" * 2000), format="json"
        )
        self.assertEqual(res.status_code, 201, res.data)
        reg = WarrantyRegistration.objects.get()
        self.assertEqual(len(reg.note), 1000)
