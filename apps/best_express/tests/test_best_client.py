import uuid
from datetime import timedelta

import pytest
import requests
from django.utils import timezone

from apps.best_express.exceptions import BestExpressError
from apps.best_express.infrastructure import best_client
from apps.best_express.models import BestExpressAccount


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code
        self.text = str(json_data)

    def json(self):
        return self._json


class NonJSONResponse:
    def __init__(self, status_code=200, text="<html>not json</html>"):
        self.status_code = status_code
        self.text = text

    def json(self):
        raise ValueError("No JSON object could be decoded")


def make_account(**kwargs) -> BestExpressAccount:
    defaults = {
        "name": "T",
        "environment": "test",
        "base_url": "http://best.example",
        "username": f"user-{uuid.uuid4()}",
        "password": "pw",
    }
    defaults.update(kwargs)
    return BestExpressAccount.objects.create(**defaults)


@pytest.mark.django_db
def test_login_stores_token(monkeypatch):
    account = make_account()
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse({"token": "tok-123"})

    monkeypatch.setattr(best_client.requests, "post", fake_post)
    token = best_client.ensure_token(account)
    assert token == "tok-123"
    account.refresh_from_db()
    assert account.token == "tok-123"
    assert account.token_expires_at > timezone.now() + timedelta(days=28)
    assert calls[0][0] == "http://best.example/VietNamV3/v3/api/process/sears/User/Login"
    assert calls[0][1]["json"] == {"Username": account.username, "Password": "pw"}


@pytest.mark.django_db
def test_token_reused_when_valid(monkeypatch):
    account = make_account(
        token="cached", token_expires_at=timezone.now() + timedelta(days=10)
    )

    def fail_post(url, **kwargs):  # pragma: no cover - guard
        raise AssertionError("không được gọi login khi token còn hạn")

    monkeypatch.setattr(best_client.requests, "post", fail_post)
    assert best_client.ensure_token(account) == "cached"


@pytest.mark.django_db
def test_login_reuses_token_refreshed_by_other_worker(monkeypatch):
    account = make_account(
        token="stale", token_expires_at=timezone.now() + timedelta(days=10)
    )
    # Simulate another worker having already refreshed the token in the DB.
    BestExpressAccount.objects.filter(pk=account.pk).update(token="fresh-from-db")

    def fail_post(url, **kwargs):  # pragma: no cover - guard
        raise AssertionError("không được gọi login khi worker khác đã refresh token")

    monkeypatch.setattr(best_client.requests, "post", fail_post)
    assert best_client.ensure_token(account, force_refresh=True) == "fresh-from-db"


@pytest.mark.django_db
def test_relogin_once_on_401(monkeypatch):
    account = make_account(
        token="stale", token_expires_at=timezone.now() + timedelta(days=10)
    )
    responses = [
        FakeResponse({}, status_code=401),          # create_order với token cũ
        FakeResponse({"token": "fresh"}),            # login lại
        FakeResponse({"Result": 1, "Code": "848", "RouteCode": "R1"}),  # retry OK
    ]
    seen = []

    def fake_post(url, **kwargs):
        seen.append((url, kwargs.get("headers", {})))
        return responses.pop(0)

    monkeypatch.setattr(best_client.requests, "post", fake_post)
    data = best_client.BestExpressClient(account).create_order({"Code": "DH-1"})
    assert data["Code"] == "848"
    assert seen[2][1]["Authorization"] == "Bearer fresh"


@pytest.mark.django_db
def test_result_2_raises_with_original_message(monkeypatch):
    account = make_account(
        token="tok", token_expires_at=timezone.now() + timedelta(days=10)
    )
    monkeypatch.setattr(
        best_client.requests,
        "post",
        lambda url, **kw: FakeResponse(
            {"Result": 2, "Message": "[ex0034] The order have cancelled"}
        ),
    )
    with pytest.raises(BestExpressError) as exc:
        best_client.BestExpressClient(account).cancel_order("848")
    assert exc.value.message == "[ex0034] The order have cancelled"


@pytest.mark.django_db
def test_non_json_response_raises_best_express_error(monkeypatch):
    account = make_account(
        token="tok", token_expires_at=timezone.now() + timedelta(days=10)
    )
    monkeypatch.setattr(
        best_client.requests, "post", lambda url, **kw: NonJSONResponse()
    )
    with pytest.raises(BestExpressError):
        best_client.BestExpressClient(account).estimate_fee({})


@pytest.mark.django_db
def test_login_result_2_raises_original_message(monkeypatch):
    account = make_account()
    monkeypatch.setattr(
        best_client.requests,
        "post",
        lambda url, **kw: FakeResponse(
            {"Result": 2, "Message": "[ex0099] Sai tài khoản hoặc mật khẩu"}
        ),
    )
    with pytest.raises(BestExpressError) as exc:
        best_client.ensure_token(account, force_refresh=True)
    assert exc.value.message == "[ex0099] Sai tài khoản hoặc mật khẩu"


@pytest.mark.django_db
def test_login_rate_limited(monkeypatch):
    account = make_account()
    monkeypatch.setattr(
        best_client.requests, "post", lambda url, **kw: FakeResponse({"token": "t"})
    )
    for _ in range(best_client.LOGIN_RATE_LIMIT):
        best_client.ensure_token(account, force_refresh=True)
    with pytest.raises(BestExpressError):
        best_client.ensure_token(account, force_refresh=True)


@pytest.mark.django_db
def test_trace_and_print_limits(monkeypatch):
    account = make_account(
        token="tok", token_expires_at=timezone.now() + timedelta(days=10)
    )
    client = best_client.BestExpressClient(account)
    with pytest.raises(BestExpressError):
        client.trace_query([str(i) for i in range(11)])
    with pytest.raises(BestExpressError):
        client.print_waybill([str(i) for i in range(51)])


@pytest.mark.django_db
def test_ems_key_header_sent(monkeypatch):
    account = make_account(
        token="tok",
        token_expires_at=timezone.now() + timedelta(days=10),
        ems_key="EMS-1",
    )
    seen = {}

    def fake_post(url, **kwargs):
        seen["headers"] = kwargs["headers"]
        return FakeResponse({"Result": 1})

    monkeypatch.setattr(best_client.requests, "post", fake_post)
    best_client.BestExpressClient(account).estimate_fee({"Weight": 100})
    assert seen["headers"]["ems-key"] == "EMS-1"


@pytest.mark.django_db
def test_network_error_raises_best_express_error(monkeypatch):
    account = make_account(
        token="tok", token_expires_at=timezone.now() + timedelta(days=10)
    )

    def raise_connection_error(url, **kwargs):
        raise requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr(best_client.requests, "post", raise_connection_error)
    with pytest.raises(BestExpressError):
        best_client.BestExpressClient(account).estimate_fee({})
