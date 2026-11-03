"""Fixture Media: connection sẵn, user 3 kiểu, client ads-crm giả (patch tại proxy_service)."""

from unittest.mock import MagicMock

import pytest
from rest_framework.test import APIClient

from apps.media.exceptions import AdsCrmApiError
from apps.media.models import MediaConnection
from apps.users.models import User


@pytest.fixture
def connection(db):
    return MediaConnection.objects.create(base_url="https://media.example", shared_api_key="dak_shared_key_123")


@pytest.fixture
def fake_client(monkeypatch):
    """MagicMock thay AdsCrmClient. `fake_client.json_request.return_value = (200, {...})`."""
    client = MagicMock()
    client.json_request.return_value = (200, {"items": [], "total": 0, "page": 1, "page_size": 20})
    client.me.return_value = {"id": 7, "username": "svc", "name": "Service", "role": "admin"}
    monkeypatch.setattr(
        "apps.media.application.services.proxy_service.AdsCrmClient",
        lambda base_url, api_key, **kw: client,
    )
    monkeypatch.setattr(
        "apps.media.application.services.connection_service.AdsCrmClient",
        lambda base_url, api_key, **kw: client,
    )
    return client


def _user(username, **fields):
    return User.objects.create_user(username=username, password="x", **fields)


@pytest.fixture
def personal_user(db):
    return _user("personal", media_api_key="dak_personal_key_1")


@pytest.fixture
def shared_viewer(db):
    return _user("viewer", media_role="viewer")


@pytest.fixture
def shared_uploader(db):
    return _user("uploader", media_role="uploader")


@pytest.fixture
def shared_marketing(db):
    return _user("marketing", media_role="marketing")


@pytest.fixture
def shared_admin(db):
    return _user("madmin", media_role="admin")


@pytest.fixture
def unprovisioned(db):
    return _user("nobody")


@pytest.fixture
def api():
    def _as(user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    return _as


@pytest.fixture
def upstream_error():
    return lambda code, status: AdsCrmApiError(code, status)
