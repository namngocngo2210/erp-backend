"""Kiểm thử trần request THẬT của portal (I6) — conftest.py có fixture autouse
``_no_portal_throttle`` tắt throttle cho MỌI test khác trong package này, nên
file này ghi đè fixture cùng tên bằng bản no-op để throttle thật được thi hành.
"""

import pytest
from django.core.cache.backends.locmem import LocMemCache

from apps.warranty_codes.application.services.batch_service import WarrantyCodeBatchService
from apps.warranty_codes.models import WarrantyCode
from apps.warranty_codes.transport.portal_throttles import PortalActivateThrottle

pytestmark = pytest.mark.django_db

URL = "/api/warranty-portal/activate"


@pytest.fixture(autouse=True)
def _no_portal_throttle():
    """Ghi đè (shadow) fixture autouse cùng tên ở conftest.py — KHÔNG tắt throttle ở đây."""
    yield


def _code() -> WarrantyCode:
    batch = WarrantyCodeBatchService.create_batch(quantity=1, product_id=None, created_by=None)
    return WarrantyCode.objects.filter(batch=batch).first()


def test_activate_endpoint_returns_429_past_rate_from_same_ip(public_client, monkeypatch):
    """Gọi activate quá rate/giờ từ cùng IP → 429 (rate giả lập thấp để test nhanh)."""
    # get_rate() là điểm mở rộng chuẩn của DRF SimpleRateThrottle (mirror
    # apps.common.throttling._FixedWindowThrottle.get_rate) — ổn định hơn đụng
    # vào THROTTLE_RATES nội bộ.
    monkeypatch.setattr(PortalActivateThrottle, "get_rate", lambda self: "3/hour")
    monkeypatch.setattr(PortalActivateThrottle, "cache", LocMemCache("portal-activate-429-test", {}))

    code = _code()
    ip = "203.0.113.50"
    statuses = []
    for _ in range(4):
        r = public_client.post(
            URL, {"keyword": code.code, "phone": "0901234567"}, format="json",
            HTTP_CF_CONNECTING_IP=ip,
        )
        statuses.append(r.status_code)

    # 3 request đầu còn trong hạn mức (201 kích hoạt lần đầu, 200 "viewed" 2 lần
    # sau — cùng mã); request thứ 4 vượt rate/giờ → 429.
    assert statuses == [201, 200, 200, 429]


def test_portal_activate_throttle_blocks_after_limit_same_ip():
    """Test đơn vị trực tiếp trên PortalActivateThrottle với rate giả lập."""
    from rest_framework.test import APIRequestFactory

    factory = APIRequestFactory()
    throttle = PortalActivateThrottle()
    throttle.cache = LocMemCache("portal-activate-unit-test", {})
    throttle.rate = "2/hour"
    throttle.num_requests, throttle.duration = throttle.parse_rate(throttle.rate)

    request = factory.post(URL, HTTP_CF_CONNECTING_IP="203.0.113.9")
    assert [throttle.allow_request(request, None) for _ in range(3)] == [True, True, False]


def test_portal_activate_throttle_separates_by_ip():
    from rest_framework.test import APIRequestFactory

    factory = APIRequestFactory()
    throttle = PortalActivateThrottle()
    throttle.cache = LocMemCache("portal-activate-unit-test-2", {})
    throttle.rate = "1/hour"
    throttle.num_requests, throttle.duration = throttle.parse_rate(throttle.rate)

    r1 = factory.post(URL, HTTP_CF_CONNECTING_IP="203.0.113.9")
    r2 = factory.post(URL, HTTP_CF_CONNECTING_IP="198.51.100.1")
    assert throttle.allow_request(r1, None) is True
    assert throttle.allow_request(r1, None) is False
    assert throttle.allow_request(r2, None) is True
