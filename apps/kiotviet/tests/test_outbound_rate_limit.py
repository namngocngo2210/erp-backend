"""Trần tần suất gọi RA KiotViet Public API.

Vì sao cần: hạn mức của KiotViet tính theo GIAN HÀNG. Nhiều worker Celery + nhiều
tiến trình gunicorn cùng đồng bộ thì mỗi tiến trình tự giới hạn cũng vô nghĩa —
n tiến trình nhân lên n lần đúng thứ cần chặn. Trần phải dùng chung qua Redis và
key theo retailer.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.kiotviet.infrastructure.services import common_auth_customer as svc


@pytest.fixture
def acquire_spy():
    with patch.object(svc.rate_limiter, "acquire", return_value=True) as spy:
        yield spy


def test_slot_is_requested_per_retailer(settings, acquire_spy):
    settings.KIOTVIET_RATE_LIMIT_PER_SECOND = 7
    settings.KIOTVIET_RATE_LIMIT_MAX_WAIT = 3.0

    svc.wait_for_kiotviet_slot("desi-shop")

    _, kwargs = acquire_spy.call_args
    assert kwargs["key"] == "kiotviet:desi-shop"
    assert kwargs["limit"] == 7
    assert kwargs["window"] == 1
    assert kwargs["max_wait"] == 3.0
    assert kwargs["service"] == "kiotviet"


def test_missing_retailer_still_gets_a_bucket(settings, acquire_spy):
    """Không có retailer thì vẫn phải bị đếm — chứ không được thành đường vòng không giới hạn."""
    settings.KIOTVIET_RATE_LIMIT_PER_SECOND = 5

    svc.wait_for_kiotviet_slot("")

    assert acquire_spy.call_args.kwargs["key"] == "kiotviet:unknown"


def test_limiter_can_be_turned_off(settings, acquire_spy):
    settings.KIOTVIET_RATE_LIMIT_PER_SECOND = 0

    svc.wait_for_kiotviet_slot("desi-shop")

    acquire_spy.assert_not_called()


def test_request_waits_for_slot_before_hitting_kiotviet(settings, acquire_spy):
    """Xin slot phải xảy ra TRƯỚC lệnh HTTP, nếu không thì trần chỉ là trang trí."""
    settings.IS_STAGING = False
    settings.KIOTVIET_RATE_LIMIT_PER_SECOND = 5
    settings.KIOTVIET_API_BASE_URL = "https://public.kiotapi.com"
    channel = SimpleNamespace(retailer="desi-shop")

    calls = []
    acquire_spy.side_effect = lambda **kwargs: calls.append("slot") or True

    with (
        patch.object(svc, "get_access_token", return_value="token-x"),
        # GET đi qua http_read (client đọc dùng chung) — patch cả object để bắt .request.
        patch.object(svc, "http_read") as http_read_mock,
    ):
        http_read_mock.request.side_effect = lambda *args, **kwargs: calls.append("http")
        svc._kiotviet_request(None, channel, "GET", "/invoices")

    assert calls == ["slot", "http"]
