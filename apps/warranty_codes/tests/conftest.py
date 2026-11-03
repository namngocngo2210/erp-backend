import pytest
from rest_framework.test import APIClient

from apps.users.tests.helpers import make_user_with_sections


@pytest.fixture
def product_factory(db):
    from apps.catalog.models import Product

    def _make(name="Cà phê test", sku=None):
        import uuid

        return Product.objects.create(name=name, sku=sku or f"SKU-{uuid.uuid4().hex[:8]}")

    return _make


@pytest.fixture
def wc_user(db):
    return make_user_with_sections("wc-staff", ["warranty-codes"])


@pytest.fixture
def auth_client(wc_user):
    client = APIClient()
    client.force_authenticate(wc_user)
    return client


@pytest.fixture
def public_client():
    """Client không đăng nhập — dùng cho endpoint portal public."""
    return APIClient()


@pytest.fixture(autouse=True)
def _no_portal_throttle(monkeypatch):
    """Tắt trần request trong test (mẫu apps/warranty/tests/test_public_intake_api.py)."""
    from apps.warranty_codes.transport import portal_throttles

    monkeypatch.setattr(portal_throttles.PortalActivateThrottle, "allow_request", lambda self, r, v: True)
    monkeypatch.setattr(portal_throttles.PortalReadThrottle, "allow_request", lambda self, r, v: True)
    monkeypatch.setattr(portal_throttles.PortalMediaThrottle, "allow_request", lambda self, r, v: True)
